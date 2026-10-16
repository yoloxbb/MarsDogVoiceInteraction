"""Opt-in first-hit regression with the 2026-10-14 private raw recordings."""

from io import BytesIO
import os
from pathlib import Path

import pytest
from scipy.io import wavfile

from marsdog_voice_interaction.providers.kws_sherpa import KWSSherpaProvider
from marsdog_voice_interaction.utils.config_loader import load_config
from test_kws_priority import _node, _finish, _business


AUDIO_DIR = os.environ.get("MARSDOG_PRIORITY_TEST_AUDIO_DIR")
pytestmark = pytest.mark.skipif(AUDIO_DIR is None, reason="需要 2026-10-14 原始录音快照")


@pytest.fixture(scope="module")
def detector():
    config = load_config(Path(__file__).parents[1] / "config/voice.yaml")
    provider = KWSSherpaProvider(config["providers"]["kws"]["config"])
    try:
        provider.start()
        assert provider.is_available()
        yield provider
    finally:
        provider.stop()


@pytest.mark.parametrize("utterance_id,text", [
    ("e7f676755a6b4191b0380ffcd7f9587a", "过来过来过来过来过来是样"),
    ("3350f44dd8bf469a9d872e912461158c", "过来过来嗯我"),
    ("2072234662664f448bb8ed8c15261e3c", "过来过来过来"),
    ("80f912e6f0b24ae49c4d921c17d842c5", "过来"),
    ("1493de4bac7b4f91b68ec2896d7c49ea", "过来过来过来"),
    ("3790223839d541f8adaa0034b6327bf3", "就过来过来过来过来"),
])
def test_first_hit_releases_one_command_without_waiting_for_asr(detector, utterance_id, text):
    path = Path(AUDIO_DIR) / utterance_id / "01_raw_capture.wav"
    try:
        recording = path.read_bytes()
    except FileNotFoundError:
        pytest.skip(f"缺少原始录音快照：{path}")
    sample_rate, samples = wavfile.read(BytesIO(recording))
    node = _node(text)
    node._providers["kws"] = detector
    detector.start_utterance()
    published_at = None
    try:
        for offset in range(0, len(samples), 320):
            chunk = samples[offset:offset + 320]
            detector.accept_waveform(chunk, sample_rate)
            node._poll_kws_events()
            if published_at is None and _business(node):
                published_at = (offset + len(chunk)) / sample_rate
    finally:
        detector.finish_utterance()
    assert [e["event_type"] for e in _business(node)] == ["EVT_VOICE_COMMAND_COME"]
    assert published_at <= len(samples) / sample_rate
    # These two utterances previously had only one KWS detection and UNKNOWN.
    if utterance_id in {"3350f44dd8bf469a9d872e912461158c", "2072234662664f448bb8ed8c15261e3c"}:
        assert published_at < len(samples) / sample_rate
    _finish(node)
    assert len(_business(node)) == 1
    assert not node._providers["intent_llm"].calls
