import base64
import io
import threading
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from marsdog_voice_interaction.nodes.voice_interaction_node import VoiceInteractionNode
from marsdog_voice_interaction.providers.speaker_sherpa import SpeakerSherpaProvider
from marsdog_voice_interaction.providers.mock_speaker import MockSpeakerProvider
from marsdog_voice_interaction.utils.uploaded_audio import encode_pcm16_wav


def test_failed_real_model_never_becomes_mock(monkeypatch):
    monkeypatch.setattr(SpeakerSherpaProvider, "start", lambda self: None)
    provider = VoiceInteractionNode._build_speaker({"type": "sherpa", "config": {}})
    assert isinstance(provider, SpeakerSherpaProvider)
    assert provider.verify(None)["reason"] == "unavailable"
    assert isinstance(VoiceInteractionNode._build_speaker({"type": "mock"}), MockSpeakerProvider)


def test_ros_decoder_downmixes_stereo_without_doubling_duration():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(np.tile(np.array([1000, -1000], dtype="<i2"), 16000).tobytes())
    result = VoiceInteractionNode._decode_audio_params({
        "audio_base64": base64.b64encode(buf.getvalue()).decode(),
    })
    assert result["sample_rate"] == 16000
    assert len(result["audio_samples"]) == 16000
    assert not result["audio_samples"].any()


def test_ros_decoder_rejects_wrong_bit_depth():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(1)
        stream.setframerate(16000)
        stream.writeframes(bytes(16000))
    with pytest.raises(ValueError, match="16-bit"):
        VoiceInteractionNode._decode_audio_params({"audio_base64": base64.b64encode(buf.getvalue()).decode()})


@pytest.mark.parametrize("encoded", ["", "not base64!", base64.b64encode(b"not WAV").decode()])
def test_explicit_invalid_audio_never_falls_back_to_previous_speaker(encoded):
    provider = Mock()
    node = SimpleNamespace(
        _providers={"speaker": provider}, _latest_audio={"audio_samples": [0.1]},
        _speaker_operation_lock=threading.RLock(),
        _decode_audio_params=VoiceInteractionNode._decode_audio_params,
    )
    result = VoiceInteractionNode._run_task(node, "verify_speaker", {"audio_base64": encoded})
    assert not result["ok"]
    provider.verify.assert_not_called()


def test_ros_verification_requires_vad_speech():
    provider = Mock()
    vad = Mock()
    vad.trim_wav.side_effect = ValueError("VAD 未检测到有效语音")
    node = SimpleNamespace(
        _providers={"speaker": provider}, _latest_audio=None,
        _speaker_operation_lock=threading.RLock(),
        _decode_audio_params=VoiceInteractionNode._decode_audio_params,
        _get_speaker_audio_vad=lambda: vad,
    )
    payload = encode_pcm16_wav(np.zeros(16000), 16000)
    result = VoiceInteractionNode._run_task(node, "verify_speaker", {"audio_base64":base64.b64encode(payload).decode()})
    assert not result["ok"]
    assert result["code"] == "invalid_audio"
    provider.verify.assert_not_called()


def test_ros_verification_uses_trimmed_normalized_audio():
    provider = Mock()
    provider.verify.return_value = {"speaker_id": "owner", "matched":True}
    trimmed = np.ones(8000, dtype=np.float32)
    node = SimpleNamespace(
        _providers={"speaker": provider}, _latest_audio=None,
        _speaker_operation_lock=threading.RLock(),
        _decode_audio_params=VoiceInteractionNode._decode_audio_params,
        _get_speaker_audio_vad=lambda: SimpleNamespace(
            trim_wav=lambda _: SimpleNamespace(samples=trimmed, sample_rate=16000)),
    )
    payload = encode_pcm16_wav(np.ones(48000), 48000)
    result = VoiceInteractionNode._run_task(node, "verify_speaker", {"audio_base64":base64.b64encode(payload).decode()})
    assert result["ok"]
    passed = provider.verify.call_args.args[0]
    assert passed["sample_rate"] == 16000
    assert passed["audio_samples"] is trimmed
