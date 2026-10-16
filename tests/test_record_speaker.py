import io
import subprocess
import wave
from unittest.mock import Mock

import pytest

from scripts import record_speaker


def _process(monkeypatch, raw=b"", error=b"", returncode=0):
    process = Mock()
    process.communicate.return_value = (raw, error)
    process.returncode = returncode
    process.poll.return_value = returncode
    monkeypatch.setattr(record_speaker.shutil, "which", lambda _: "/usr/bin/arecord")
    popen = Mock(return_value=process)
    monkeypatch.setattr(record_speaker.subprocess, "Popen", popen)
    return process, popen


def test_capture_produces_pcm16_mono_wav_on_selected_device(monkeypatch):
    raw = b"\x34\x12" * 16000
    _, popen = _process(monkeypatch, raw=raw)
    payload = record_speaker.record_wav("plughw:CARD=CDC,DEV=0", 1)
    with wave.open(io.BytesIO(payload), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        assert audio.readframes(16000) == raw
    command = popen.call_args.args[0]
    assert command[command.index("-D") + 1] == "plughw:CARD=CDC,DEV=0"
    assert command[command.index("-s") + 1] == "16000"


@pytest.mark.parametrize(("raw", "error", "code", "message"), [
    (b"", b"audio open error: Device or resource busy", 1, "被占用"),
    (b"", b"No such file or directory", 1, "录音失败"),
    (b"\x01\x00" * 40, b"", 0, "数据不完整"),
    (bytes(32000), b"", 0, "全零"),
])
def test_bad_capture_is_rejected_without_switching_device(monkeypatch, raw, error, code, message):
    _, popen = _process(monkeypatch, raw, error, code)
    with pytest.raises(RuntimeError, match=message):
        record_speaker.record_wav("plughw:CARD=CDC,DEV=0", 1)
    popen.assert_called_once()


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("arecord", 6), KeyboardInterrupt()])
def test_capture_timeout_or_cancel_releases_child(monkeypatch, failure):
    process, _ = _process(monkeypatch, returncode=None)
    process.communicate.side_effect = [failure, (b"", b"")]
    with pytest.raises(RuntimeError if isinstance(failure, subprocess.TimeoutExpired) else KeyboardInterrupt):
        record_speaker.record_wav("plughw:CARD=CDC,DEV=0", 1)
    process.kill.assert_called_once()
    assert process.communicate.call_count == 2


@pytest.fixture
def runtime(monkeypatch):
    service = Mock()
    service.enrollment.list_speaker_records.return_value = {"speakers": []}
    service.enrollment.enroll_speaker_batch_from_audio.return_value = {
        "ok": True, "shots": 3, "added_count": 3,
        "samples": [{"audio_path": "/temporary/owner/001.wav"}],
    }
    service.vad.trim_wav.return_value.speech_duration_ms = 2000
    monkeypatch.setattr(record_speaker, "StandaloneSpeakerService", lambda _: service)
    monkeypatch.setattr(record_speaker, "load_config", lambda _: {
        "providers": {"audio": {"config": {"arecord_device": "plughw:CARD=CDC,DEV=0"}}},
    })
    capture = Mock(side_effect=[b"first", b"second", b"third"])
    monkeypatch.setattr(record_speaker, "record_wav", capture)
    return service, capture


def test_defaults_register_three_owner_samples_in_one_batch(runtime, monkeypatch):
    service, capture = runtime
    prompt = Mock(return_value="")
    monkeypatch.setattr("builtins.input", prompt)
    assert record_speaker.main([]) == 0
    assert prompt.call_count == 3
    assert capture.call_count == 3
    assert capture.call_args.args == ("plughw:CARD=CDC,DEV=0", 6.0)
    service.enrollment.enroll_speaker_batch_from_audio.assert_called_once_with(
        "owner", [b"first", b"second", b"third"], vad=service.vad,
    )
    service.server.start.assert_not_called()
    service.server.run.assert_not_called()
    service.close.assert_called_once()


def test_full_slot_is_reported_before_capture(runtime):
    service, capture = runtime
    service.enrollment.list_speaker_records.return_value = {
        "speakers": [{"name": "owner", "shots": 4}],
    }
    assert record_speaker.main(["--no-prompt"]) == 1
    capture.assert_not_called()
    service.enrollment.enroll_speaker_batch_from_audio.assert_not_called()
    service.close.assert_called_once()


def test_speechless_recording_never_commits_partial_batch(runtime):
    service, capture = runtime
    service.vad.trim_wav.side_effect = [
        Mock(speech_duration_ms=2000), ValueError("VAD 未检测到有效语音"),
    ]
    assert record_speaker.main(["--no-prompt"]) == 1
    assert capture.call_count == 2
    service.enrollment.enroll_speaker_batch_from_audio.assert_not_called()
    service.close.assert_called_once()


def test_cancel_before_recording_leaves_samples_unchanged(runtime, monkeypatch):
    service, capture = runtime
    monkeypatch.setattr("builtins.input", Mock(side_effect=EOFError))
    assert record_speaker.main([]) == 130
    capture.assert_not_called()
    service.enrollment.enroll_speaker_batch_from_audio.assert_not_called()
    service.close.assert_called_once()


def test_identity_conflict_reports_failure(runtime, capsys):
    service, _ = runtime
    service.enrollment.enroll_speaker_batch_from_audio.return_value = {
        "ok": False, "code": "speaker_identity_conflict", "error": "其他身份已有相似声纹",
    }
    assert record_speaker.main(["--no-prompt", "--speaker", "family_member_1", "--shots", "1"]) == 1
    assert service.enrollment.enroll_speaker_batch_from_audio.call_args.args[0] == "family_member_1"
    assert "speaker_identity_conflict" in capsys.readouterr().err
    service.close.assert_called_once()
