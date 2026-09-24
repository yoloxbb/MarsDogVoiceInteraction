import os
import sys
import threading
import time
import types
from types import SimpleNamespace

import numpy as np
import pytest

sd = types.ModuleType("sounddevice")
sd.InputStream = object
sd.PortAudioError = RuntimeError
sys.modules.setdefault("sounddevice", sd)

from marsdog_voice_interaction.providers.audio_capture import (
    AudioCaptureGap, BufferedAudioCapture, read_pipe_chunk,
)
from marsdog_voice_interaction.providers.audio_sherpa import AudioSherpaProvider


def test_speech_has_its_own_deadline():
    provider = AudioSherpaProvider({"max_duration_sec": 8, "max_speech_duration_sec": 8})
    assert provider._capture_deadline_reached(8.1, None)
    assert not provider._capture_deadline_reached(8.1, 7.5)
    assert not provider._capture_deadline_reached(15.4, 7.5)
    assert provider._capture_deadline_reached(15.5, 7.5)


def test_wake_history_replays_only_audio_after_wake_boundary(monkeypatch):
    clock = [1.0]
    monkeypatch.setattr("marsdog_voice_interaction.providers.audio_capture.time.monotonic",
                        lambda: clock[0])
    capture = BufferedAudioCapture(10, None, None, 6.0, 2.0, history_sec=3.0)
    for when in (1.0, 2.0, 3.0):
        clock[0] = when
        capture._push(np.full(2, when, dtype=np.float32))
    assert capture.snapshot(3.0, 2.5).tolist() == [1.0] * 2 + [2.0] * 2 + [3.0] * 2

    capture.start_delivery(2.5)
    assert capture.read().tolist() == [3.0, 3.0]
    clock[0] = 4.0
    capture._push(np.full(2, 4.0, dtype=np.float32))
    assert capture.read().tolist() == [4.0, 4.0]
    capture.stop_delivery()
    assert capture.read() is None


def test_speech_started_near_wait_deadline_finishes_normally():
    class LateVad:
        def reset(self):
            self.count = 0
            self.done = False
        def accept_waveform(self, samples):
            self.count += 1
            self.is_speech_detected = self.count >= 9
        def empty(self):
            return self.count < 15 or self.done
        @property
        def front(self):
            return SimpleNamespace(start=8 * 320, samples=np.ones(7 * 320))
        def pop(self):
            self.done = True
        def flush(self):
            raise AssertionError("late speech must not hit the waiting deadline")
    provider = AudioSherpaProvider({"continuous_capture": True,
                                  "max_duration_sec": .2, "max_speech_duration_sec": .2})
    provider._vad = LateVad()
    provider._buffered_capture = SimpleNamespace(read=lambda: np.ones(320, dtype=np.float32))
    result = provider._stream_vad_buffered(threading.Event())
    assert result["has_voice"]
    assert result["capture_end_reason"] == "vad_complete"
    assert provider._vad.count == 15


def test_vad_factories_receive_sample_rate_and_threads(monkeypatch):
    import sherpa_onnx
    from marsdog_voice_interaction.utils.uploaded_audio import UploadedAudioVAD
    configs = []
    def detector(*, config, buffer_size_in_seconds):
        configs.append(config)
        return object()
    monkeypatch.setattr(sherpa_onnx, "VoiceActivityDetector", detector)
    settings = {"vad_model": "fake.onnx", "sample_rate": 16000, "num_threads": 3}
    provider = AudioSherpaProvider(settings)
    provider.start()
    assert provider.is_available()
    UploadedAudioVAD(settings)
    assert len(configs) == 2
    assert all(c.sample_rate == 16000 and c.num_threads == 3 for c in configs)


def test_overlapping_preroll_uses_each_sample_once():
    provider = AudioSherpaProvider({"sample_rate": 10, "pre_roll_sec": .3})
    raw = np.arange(20, dtype=np.float32)
    result = provider._merge_speech_segments([
        {"start": 3, "end": 7}, {"start": 8, "end": 11},
    ], raw)
    np.testing.assert_array_equal(result, raw[:11])


def test_pipe_without_data_returns_within_deadline():
    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(read_fd, "rb", buffering=0) as pipe:
            started = time.monotonic()
            assert read_pipe_chunk(pipe, 640, .02) is None
            assert time.monotonic() - started < .5
            os.write(write_fd, b"abc")
            assert read_pipe_chunk(pipe, 640) == b"abc"
    finally:
        os.close(write_fd)


def test_buffer_overflow_invalidates_utterance_and_stays_bounded():
    capture = BufferedAudioCapture(16000, None, None, .04, 1)
    for _ in range(10):
        capture._push(np.ones(320))
    assert capture._queue.qsize() <= 2
    with pytest.raises(AudioCaptureGap):
        capture.read()
    assert capture.read() is None
    capture._push(np.full(320, .2))
    np.testing.assert_allclose(capture.read(), .2)


def test_expired_audio_never_reaches_recognition(monkeypatch):
    capture = BufferedAudioCapture(16000, None, None, 1, 1)
    capture._queue.put((1., np.ones(320)))
    monkeypatch.setattr(time, "monotonic", lambda: 3.)
    with pytest.raises(AudioCaptureGap, match="expired"):
        capture.read()


class Vad:
    def reset(self):
        self.count = 0
        self.segments = []
    def accept_waveform(self, samples):
        self.count += 1
        self.is_speech_detected = True
        self.segments.append(SimpleNamespace(start=0, samples=samples))
    def empty(self):
        return not self.segments
    @property
    def front(self):
        return self.segments[0]
    def pop(self):
        self.segments.pop(0)


def wait_result(provider):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        result = provider.poll_result()
        if result is not None:
            return result
        time.sleep(.005)
    raise AssertionError("capture did not finish")


def test_buffer_survives_asr_gap_and_kws_only_sees_current_utterance():
    provider = AudioSherpaProvider({"continuous_capture": True})
    provider.available = True
    provider._vad = Vad()
    capture = BufferedAudioCapture(16000, None, None, 6, 1)
    # No hardware: drive the microphone producer deterministically.
    capture._thread = threading.Thread(target=lambda: None)
    capture.start()
    capture._thread.join()
    provider._buffered_capture = capture
    kws_chunks = []
    provider.set_chunk_callback(lambda samples, _: kws_chunks.append(samples.copy()))
    capture._push(np.full(320, .1, dtype=np.float32))
    provider.set_utterance_id("first")
    assert provider.start_capture()
    first = wait_result(provider)
    # Microphone produces the next command while ASR works on the first.
    capture._push(np.full(320, .2, dtype=np.float32))
    assert len(kws_chunks) == 1
    assert provider._buffered_capture is capture
    provider.set_utterance_id("second")
    assert provider.start_capture()
    second = wait_result(provider)
    assert first["utterance_id"] == "first"
    assert second["utterance_id"] == "second"
    np.testing.assert_allclose(first["audio_samples"], .1)
    np.testing.assert_allclose(second["audio_samples"], .2)
    assert len(kws_chunks) == 2
    capture._push(np.ones(320))
    assert provider.cancel_capture()
    assert provider._buffered_capture is None
    assert capture._queue.empty()


def test_gap_drops_entire_utterance():
    provider = AudioSherpaProvider({"continuous_capture": True})
    provider._vad = Vad()
    capture = BufferedAudioCapture(16000, None, None, .02, 1)
    provider._buffered_capture = capture
    capture._push(np.ones(320))
    capture._push(np.ones(320))
    result = provider._stream_vad_buffered(threading.Event())
    assert not result["has_voice"]
    assert result["capture_end_reason"] == "audio_gap"
    assert provider._resync_required


def test_after_gap_discards_speech_suffix_until_quiet_boundary():
    class ResyncVad(Vad):
        def accept_waveform(self, samples):
            super().accept_waveform(samples)
            self.is_speech_detected = bool(np.any(samples))
    provider = AudioSherpaProvider({"continuous_capture": True,
                                  "min_silence_dur": .02, "min_speech_dur": .02})
    provider._vad = ResyncVad()
    provider._resync_required = True
    capture = BufferedAudioCapture(16000, None, None, 6, 1)
    provider._buffered_capture = capture
    for level in (.5, 0., 0., .2):
        capture._push(np.full(320, level, dtype=np.float32))
    seen = []
    provider.set_chunk_callback(lambda samples, _: seen.append(samples))
    result = provider._stream_vad_buffered(threading.Event())
    assert result["has_voice"]
    assert not provider._resync_required
    assert len(seen) == 1
    np.testing.assert_allclose(result["audio_samples"], .2)


def test_arecord_no_data_times_out_and_terminates_process(monkeypatch):
    import subprocess
    class Process:
        def __init__(self, *args, **kwargs):
            read_fd, self.write_fd = os.pipe()
            self.stdout = os.fdopen(read_fd, "rb", buffering=0)
            self.running = True
            processes.append(self)
        def poll(self):
            return None if self.running else 0
        def terminate(self):
            self.running = False
        def communicate(self, timeout=None):
            self.stdout.close()
            os.close(self.write_fd)
            return b"", b""
        kill = terminate
    processes = []
    monkeypatch.setattr(subprocess, "Popen", Process)
    capture = BufferedAudioCapture(16000, None, None, 1, .05)
    capture.start()
    deadline = time.monotonic() + 1
    error = None
    try:
        while time.monotonic() < deadline:
            try:
                capture.read()
            except RuntimeError as exc:
                error = exc
                break
        assert error is not None and "no audio" in str(error)
    finally:
        assert capture.close()
    assert processes and not processes[0].running


@pytest.mark.parametrize("sample_rate", [8000, 48000])
def test_live_vad_rejects_unsupported_capture_sample_rate(sample_rate):
    provider = AudioSherpaProvider({"sample_rate": sample_rate})
    provider.start()
    assert not provider.is_available()


def test_microphone_keeps_reading_until_explicit_close():
    class Stream:
        reads = 0
        closed = False
        read_available = 320
        def start(self):
            pass
        def read(self, count):
            time.sleep(.005)
            self.reads += 1
            return np.ones((count, 1)), False
        def close(self):
            self.closed = True
    stream = Stream()
    capture = BufferedAudioCapture(16000, None, SimpleNamespace(InputStream=lambda **_: stream), 1, 1)
    capture.start()
    assert capture.read() is not None
    previous_reads = stream.reads
    deadline = time.monotonic() + 1
    while stream.reads <= previous_reads + 2 and time.monotonic() < deadline:
        time.sleep(.005)
    assert stream.reads > previous_reads + 2
    assert not stream.closed
    assert capture.close()
    assert stream.closed
    assert capture._queue.empty()
