from __future__ import annotations

import logging
from pathlib import Path
import sys
import time
import types
from types import SimpleNamespace
from typing import Any

import numpy as np
from scipy.io import wavfile

# PortAudio device probing can block on headless CI. These tests exercise the
# provider's pure buffer/debug methods, so a minimal import stub is sufficient.
_sounddevice = types.ModuleType("sounddevice")
_sounddevice.PortAudioError = RuntimeError  # type: ignore[attr-defined]
_sounddevice.InputStream = object  # type: ignore[attr-defined]
sys.modules.setdefault("sounddevice", _sounddevice)

from marsdog_voice_interaction.providers.asr_sherpa import ASRSherpaProvider
from marsdog_voice_interaction.providers.audio_sherpa import AudioSherpaProvider
from marsdog_voice_interaction.utils.audio_debug import AudioDebugRecorder


class _FakeStream:
    def __init__(self) -> None:
        self.waveform = np.array([], dtype=np.float32)
        self.result = SimpleNamespace(text="", lang="")

    def accept_waveform(
        self,
        sample_rate: int,
        waveform: np.ndarray,
    ) -> None:
        assert sample_rate == 16000
        self.waveform = np.asarray(waveform, dtype=np.float32).copy()


class _FakeRecognizer:
    def __init__(self) -> None:
        self.streams: list[_FakeStream] = []

    def create_stream(self) -> _FakeStream:
        stream = _FakeStream()
        self.streams.append(stream)
        return stream

    @staticmethod
    def decode_stream(stream: _FakeStream) -> None:
        stream.result = SimpleNamespace(
            text=f"samples={stream.waveform.size}",
            lang="",
        )


def _debug_config(tmp_path: Path) -> dict[str, Any]:
    return {
        "enabled": True,
        "output_dir": str(tmp_path),
        "save_raw_capture": True,
        "save_vad_segment": True,
        "save_asr_input": True,
        "compare_asr": False,
    }


def test_audio_debug_wav_preserves_float32_samples_and_metadata(
    tmp_path: Path,
    caplog: Any,
) -> None:
    caplog.set_level(logging.INFO)
    recorder = AudioDebugRecorder(_debug_config(tmp_path))
    samples = np.array([-0.75, 0.0, 0.5], dtype=np.float32)

    path = recorder.save("utterance-1", "raw", samples, 16000)

    assert path == tmp_path / "utterance-1" / "01_raw_capture.wav"
    sample_rate, restored = wavfile.read(path)
    assert sample_rate == 16000
    assert restored.dtype == np.float32
    np.testing.assert_array_equal(restored, samples)
    assert "type=raw" in caplog.text
    assert "dtype=float32" in caplog.text
    assert "channels=1" in caplog.text
    assert "out_of_range_count=0" in caplog.text


def test_disabled_audio_debug_does_not_create_output_directory(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "must_not_exist"
    recorder = AudioDebugRecorder({
        "enabled": False,
        "output_dir": str(output_dir),
    })

    assert recorder.save(
        "utterance-disabled", "raw", np.ones(3, np.float32), 16000,
    ) is None
    assert not output_dir.exists()


def test_capture_debug_preserves_original_gap_and_final_asr_input(
    tmp_path: Path,
    caplog: Any,
) -> None:
    caplog.set_level(logging.INFO)
    provider = AudioSherpaProvider({
        "sample_rate": 10,
        "pre_roll_sec": 0.3,
        "audio_debug": _debug_config(tmp_path),
    })
    raw = np.arange(10, dtype=np.float32) / 10.0
    segments = [
        {"start": 2, "end": 4, "samples": raw[2:4]},
        {"start": 6, "end": 8, "samples": raw[6:8]},
    ]
    final = np.array([0.0, 0.2, 0.3, 0.3, 0.4, 0.5, 0.6, 0.7])

    provider._save_capture_debug("utterance-2", raw, segments, final)

    recorder = AudioDebugRecorder(_debug_config(tmp_path))
    raw_wav, _ = recorder.load("utterance-2", "raw")
    vad_wav, _ = recorder.load("utterance-2", "vad")
    asr_wav, _ = recorder.load("utterance-2", "asr_input")
    np.testing.assert_array_equal(raw_wav, raw)
    np.testing.assert_array_equal(vad_wav, raw[2:8])
    np.testing.assert_array_equal(asr_wav, final.astype(np.float32))
    assert "segment_count=2" in caplog.text
    assert "original_gap_ms=200.00" in caplog.text
    assert "joined_gap_ms=300.00" in caplog.text
    assert "configured_pre_roll_ms=300.00" in caplog.text


def test_live_vad_result_creates_all_three_utterance_wavs(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    source = np.linspace(-0.5, 0.5, 320, dtype=np.float32)

    class InputStream:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        @property
        def read_available(self) -> int:
            return 320

        def start(self) -> None:
            return None

        def read(self, frames: int) -> tuple[np.ndarray, None]:
            assert frames == 320
            return source.reshape(-1, 1), None

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

    class Vad:
        is_speech_detected = False

        def __init__(self) -> None:
            self.ready = False

        def reset(self) -> None:
            self.ready = False

        def accept_waveform(self, _samples: list[float]) -> None:
            self.ready = True

        def empty(self) -> bool:
            return not self.ready

        @property
        def front(self) -> Any:
            return SimpleNamespace(start=100, samples=source[100:200])

        def pop(self) -> None:
            self.ready = False

    monkeypatch.setitem(
        AudioSherpaProvider._stream_vad.__globals__,
        "_HAS_AUDIO_CAPTURE",
        True,
    )
    monkeypatch.setitem(
        AudioSherpaProvider._stream_vad.__globals__,
        "sd",
        SimpleNamespace(InputStream=InputStream, PortAudioError=RuntimeError),
    )
    provider = AudioSherpaProvider({
        "sample_rate": 16000,
        "pre_roll_sec": 0.3,
        "audio_debug": _debug_config(tmp_path),
    })
    provider._vad = Vad()
    provider.available = True
    provider.set_utterance_id("utterance-live")
    provider.start_capture()
    deadline = time.monotonic() + 1.0
    result = None
    while result is None and time.monotonic() < deadline:
        result = provider.poll_result()
        time.sleep(0.01)

    assert result is not None
    assert result["has_voice"]
    recorder = AudioDebugRecorder(_debug_config(tmp_path))
    raw_wav, _ = recorder.load("utterance-live", "raw")
    vad_wav, _ = recorder.load("utterance-live", "vad")
    asr_wav, _ = recorder.load("utterance-live", "asr_input")
    np.testing.assert_array_equal(raw_wav, source)
    np.testing.assert_array_equal(vad_wav, source[100:200])
    np.testing.assert_array_equal(asr_wav, source[:200])


def test_debug_switch_can_disable_only_project_extra_pre_roll(
    tmp_path: Path,
) -> None:
    provider = AudioSherpaProvider({
        "sample_rate": 10,
        "pre_roll_sec": 0.3,
        "audio_debug": {
            **_debug_config(tmp_path),
            "debug_disable_extra_pre_roll": True,
        },
    })
    captured = np.arange(10, dtype=np.float32)
    segment = SimpleNamespace(start=6, samples=[60.0, 61.0])

    result = provider._segment_with_pre_roll(segment, captured)

    assert result.tolist() == [60.0, 61.0]


def test_asr_input_wav_is_the_exact_normalized_waveform_seen_by_model(
    tmp_path: Path,
) -> None:
    provider = ASRSherpaProvider({
        "model_type": "paraformer",
        "sample_rate": 16000,
        "audio_debug": _debug_config(tmp_path),
    })
    recognizer = _FakeRecognizer()
    provider._recognizer = recognizer
    provider.available = True
    samples = np.array([-0.25, 0.1, 0.75], dtype=np.float64)

    result = provider.transcribe({
        "utterance_id": "utterance-3",
        "audio_samples": samples,
        "sample_rate": 16000,
        "has_voice": True,
    })

    restored, sample_rate = provider._audio_debug.load(
        "utterance-3", "asr_input",
    )
    assert sample_rate == 16000
    np.testing.assert_array_equal(restored, samples.astype(np.float32))
    np.testing.assert_array_equal(recognizer.streams[0].waveform, restored)
    assert result["asr_text"] == "samples=3"


def test_asr_compare_reuses_loaded_recognizer_for_all_three_wavs(
    tmp_path: Path,
    caplog: Any,
) -> None:
    caplog.set_level(logging.INFO)
    debug_config = _debug_config(tmp_path)
    recorder = AudioDebugRecorder(debug_config)
    recorder.save("utterance-4", "raw", np.zeros(4, np.float32), 16000)
    recorder.save("utterance-4", "vad", np.zeros(2, np.float32), 16000)
    recorder.save("utterance-4", "asr_input", np.zeros(3, np.float32), 16000)
    provider = ASRSherpaProvider({
        "model_type": "paraformer",
        "sample_rate": 16000,
        "audio_debug": debug_config,
    })
    provider._recognizer = _FakeRecognizer()
    provider.available = True

    result = provider.compare_debug_utterance("utterance-4")

    assert result == {
        "RAW": "samples=4",
        "VAD": "samples=2",
        "ASR_INPUT": "samples=3",
    }
    assert "ASR_COMPARE utterance_id=utterance-4" in caplog.text
