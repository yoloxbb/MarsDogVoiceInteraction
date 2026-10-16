"""Opt-in replay of saved 2026-10-14 COME recordings, without ROS or a mic.

MARSDOG_REPEAT_TEST_AUDIO_DIR points to the private voice_debug directory.
"""

import os
from io import BytesIO
from pathlib import Path

import pytest
from scipy.io import wavfile

from marsdog_voice_interaction.providers.kws_sherpa import KWSSherpaProvider
from marsdog_voice_interaction.utils.config_loader import load_config
from test_command_lexicon import _KwsRouteHarness


AUDIO_DIR = os.environ.get("MARSDOG_REPEAT_TEST_AUDIO_DIR")
pytestmark = pytest.mark.skipif(AUDIO_DIR is None, reason="需要 2026-10-14 现场原始录音")


@pytest.fixture(scope="module")
def detector():
    config = load_config(Path(__file__).parents[1] / "config/voice.yaml")
    # This fixture exercises the optional legacy deferred repeat policy.
    config["providers"]["kws"]["config"].update({
        "publish_mode": "deferred", "repeat_early_command_keys": ["COME"],
    })
    provider = KWSSherpaProvider(config["providers"]["kws"]["config"])
    try:
        provider.start()
        assert provider.is_available()
        yield provider
    finally:
        provider.stop()


@pytest.mark.parametrize("utterance_id,early", [
    ("33a49160e23945478df1cfa66f76cc2f", True),
    ("507f15e2779b401cbb17c06ec68c172f", True),
    ("52a27acc788648d887e5ffe0d63dbdef", True),
    ("4edfc22e241e4b489d08bb28d5257054", False),
    ("03f643c188264a929f23ab789fcf15c5", False),
    ("176c3df825cf4f7ca344eed08746d352", False),
])
def test_recorded_repeats_publish_once_without_waiting_for_asr(detector, utterance_id, early):
    path = Path(AUDIO_DIR) / utterance_id / "01_raw_capture.wav"
    try:
        # Read a snapshot so log retention cannot remove it during decoding.
        recording = path.read_bytes()
    except FileNotFoundError:
        pytest.skip(f"缺少原始录音，请使用保留样本目录：{path}")
    sample_rate, samples = wavfile.read(BytesIO(recording))
    node = _KwsRouteHarness(command_key="COME", asr_text="过来过来过来过来")
    node._kws_arbitration.update({"repeat_early_command_keys": ["COME"], "repeat_early_min_hits": 2})
    node._providers["kws"] = detector
    detector.start_utterance()
    published_at = None
    try:
        for offset in range(0, len(samples), 320):
            detector.accept_waveform(samples[offset:offset + 320], sample_rate)
            node._poll_kws_events()
            if published_at is None and any(e.get("should_trigger_behavior_tree") for e in node.published):
                published_at = (offset + len(samples[offset:offset + 320])) / sample_rate
    finally:
        detector.finish_utterance()
    commands = [e for e in node.published if e.get("should_trigger_behavior_tree")]
    if early:
        assert [e["event_type"] for e in commands] == ["EVT_VOICE_COMMAND_COME"]
        assert published_at <= len(samples) / sample_rate
        if utterance_id == "33a49160e23945478df1cfa66f76cc2f":
            assert published_at < len(samples) / sample_rate - 0.25
        assert commands[0]["asr_text"] == ""
        node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
        assert len([e for e in node.published if e.get("should_trigger_behavior_tree")]) == 1
    else:
        assert not commands
