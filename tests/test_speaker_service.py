"""Standalone bootstrap, HTTP wiring and storage handoff checks."""

import asyncio
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import numpy as np
import pytest

import marsdog_voice_interaction.core.speaker_enrollment_manager as storage
from marsdog_voice_interaction import speaker_service
from marsdog_voice_interaction.utils.config_loader import load_config
from marsdog_voice_interaction.utils.uploaded_audio import (
    decode_pcm16_wav,
    encode_pcm16_wav,
)


class _Extractor:
    def create_stream(self):
        return SimpleNamespace(accept_waveform=lambda **_: None, input_finished=lambda: None)

    def is_ready(self, stream):
        return True

    def compute(self, stream):
        return [1.0, 0.0]


class _Vad:
    def __init__(self, config):
        pass

    def analyze_wav(self, payload):
        samples, rate = decode_pcm16_wav(payload)
        duration = len(samples) * 1000 / rate
        return {"ok": True, "speech_duration_ms": duration, "valid_for_enrollment": True}

    def trim_wav(self, payload):
        samples, rate = decode_pcm16_wav(payload)
        return SimpleNamespace(
            wav_bytes=payload, sample_rate=rate, source_sample_rate=rate,
            source_duration_ms=len(samples) * 1000 / rate,
            speech_duration_ms=len(samples) * 1000 / rate, segment_count=1,
        )


@pytest.fixture
def configured_service(tmp_path, monkeypatch):
    # Preserve the module's process-wide storage root for other tests.
    for name in ("_STORAGE_ROOT", "_SPEAKERS_DIR", "_REGISTRY_PATH"):
        monkeypatch.setattr(storage, name, getattr(storage, name))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (tmp_path / "model.onnx").touch()
    path = config_dir / "voice.yaml"
    path.write_text("""
storage:
  root: ../data
speaker_api:
  enabled: false
  host: 127.0.0.1
  port: 8092
providers:
  speaker:
    type: sherpa
    config:
      speaker_model: ../model.onnx
  audio:
    config:
      vad_model: ../model.onnx
""")
    provider = Mock()
    provider._extractor = _Extractor()
    provider.is_available.return_value = True
    monkeypatch.setattr(speaker_service, "SpeakerSherpaProvider", lambda _: provider)
    monkeypatch.setattr(speaker_service, "UploadedAudioVAD", _Vad)
    service = speaker_service.StandaloneSpeakerService(load_config(path))
    yield service, path, provider
    service.close()


def test_standalone_http_enrollment_and_handoff(configured_service):
    service, path, provider = configured_service
    payload = encode_pcm16_wav(np.full(16000, 0.2), 16000)
    files = {"audio": ("recording.wav", payload, "audio/wav")}

    async def check():
        transport = httpx.ASGITransport(app=service.server.create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/speaker-enrollment")).status_code == 200
            assert (await client.get("/speaker-enrollment/assets/app.js")).status_code == 200
            assert (await client.get("/api/v1/speakers")).json()["count"] == 0
            vad = await client.post("/api/v1/recordings/vad", files=files)
            assert vad.status_code == 200
            assert vad.json()["valid_for_enrollment"]
            assert not (path.parent.parent / "data/speaker_registry.json").exists()
            added = await client.post("/api/v1/speakers/owner/samples", files=files)
            assert added.status_code == 201, added.text
            assert (await client.get("/api/v1/speakers/owner/samples")).json()["shots"] == 1
            assert (await client.get("/api/v1/speakers/owner/samples/1")).status_code == 200
            downloaded = await client.get("/api/v1/speakers/owner/samples/1/audio")
            assert downloaded.status_code == 200
            assert decode_pcm16_wav(downloaded.content)[1] == 16000
            replaced = await client.put("/api/v1/speakers/owner/samples/1", files=files)
            assert replaced.status_code == 200, replaced.text
            batch = await client.post("/api/v1/speakers/owner/samples/batch", files=[
                ("audios", ("second.wav", payload, "audio/wav")),
                ("audios", ("third.wav", payload, "audio/wav")),
            ])
            assert batch.status_code == 201, batch.text
            # A fresh manager reads exactly what a restarted voice node will load.
            fresh = storage.SpeakerEnrollmentManager()
            sink = Mock()
            assert fresh.sync_to_provider(sink) == 1
            assert len(sink.set_templates.call_args.args[0]["owner"]) == 3
            conflict = await client.post("/api/v1/speakers/family_member_1/samples", files=files)
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == "speaker_identity_conflict"
            assert (await client.delete("/api/v1/speakers/owner/samples/1")).status_code == 200
            assert (await client.delete("/api/v1/speakers")).status_code == 400
            assert (await client.delete("/api/v1/speakers/owner/samples")).status_code == 200
            assert (await client.delete("/api/v1/speakers?confirm=true")).status_code == 200
            assert (await client.get("/api/v1/speakers")).json()["count"] == 0

    asyncio.run(check())
    assert service.server.enabled  # Explicit launch overrides embedded enabled=false.
    assert service.server.address == "http://127.0.0.1:8092"
    provider.start.assert_called_once()


def test_standalone_vad_errors_preserve_http_status(configured_service):
    service, _, _ = configured_service
    service.vad = Mock()
    service.vad.analyze_wav.side_effect = ValueError("bad WAV")
    assert service.analyze_recording(b"invalid")["status"] == 422
    service.vad.analyze_wav.side_effect = RuntimeError("model unavailable")
    assert service.analyze_recording(b"invalid")["status"] == 503


def test_standalone_model_failure_never_serves_healthy_api(configured_service):
    _, path, provider = configured_service
    provider.is_available.return_value = False
    with pytest.raises(RuntimeError, match="声纹模型加载失败"):
        speaker_service.StandaloneSpeakerService(load_config(path))
    provider.stop.assert_called_once()


def test_standalone_cli_overrides_and_cleans_up(configured_service, monkeypatch):
    _, path, _ = configured_service
    factory = Mock()
    service = factory.return_value
    service.server.run.side_effect = KeyboardInterrupt
    monkeypatch.setattr(speaker_service, "StandaloneSpeakerService", factory)
    monkeypatch.setattr(speaker_service, "setup_logging", lambda **_: None)
    speaker_service.main(["--config", str(path), "--host", "127.0.0.1", "--port", "8093"])
    config = factory.call_args.args[0]
    assert config["speaker_api"]["port"] == 8093
    assert config["storage"]["root"] == str(path.parent.parent / "data")
    service.server.run.assert_called_once()
    service.close.assert_called_once()


def test_standalone_entrypoint_needs_no_ros_or_capture_imports():
    code = """
import importlib.abc
import runpy
import sys
class NoHardware(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in {'rclpy', 'sounddevice', 'serial'} or fullname.endswith(
            ('audio_sherpa', 'audio_capture', 'asr_sherpa', 'kws_sherpa', 'voice_interaction_node')
        ):
            raise AssertionError('Unexpected dependency: ' + fullname)
sys.meta_path.insert(0, NoHardware())
sys.argv = ['speaker_service', '--help']
runpy.run_module('marsdog_voice_interaction.speaker_service', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "--config" in result.stdout
