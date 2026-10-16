"""Replay the 2026-10-13 recordings without opening a microphone or ROS node.

Set MARSDOG_NEGATION_TEST_AUDIO_DIR to the saved voice_debug directory.
The recordings themselves remain private, outside the test source tree.
"""

import os
from pathlib import Path

import pytest
from scipy.io import wavfile

from marsdog_voice_interaction.providers.kws_sherpa import KWSSherpaProvider
from marsdog_voice_interaction.utils.config_loader import load_config


AUDIO_DIR = os.environ.get("MARSDOG_NEGATION_TEST_AUDIO_DIR")
pytestmark = pytest.mark.skipif(AUDIO_DIR is None, reason="需要 2026-10-13 现场原始录音")


@pytest.fixture(scope="module")
def detectors():
    config = load_config(Path(__file__).parents[1] / "config/voice.yaml")
    config = config["providers"]["kws"]["config"]
    guarded = KWSSherpaProvider(config)
    baseline = KWSSherpaProvider({**config, "negation_guard_enabled": False,
                                "repeat_early_command_keys": []})
    try:
        guarded.start()
        baseline.start()
        assert guarded.is_available() and baseline.is_available()
        yield baseline, guarded
    finally:
        baseline.stop()
        guarded.stop()


def _replay(provider, samples, sample_rate):
    provider.start_utterance()
    events = []
    try:
        for offset in range(0, len(samples), 320):
            chunk = samples[offset:offset + 320]
            provider.accept_waveform(chunk, sample_rate)
            while (event := provider.poll_event()) is not None:
                events.append(((offset + len(chunk)) / sample_rate, event))
    finally:
        provider.finish_utterance()
    return events


@pytest.mark.parametrize("utterance_id,event_type,negated", [
    ("67798b87a47e45bfbef2f31636f68ac0", "EVT_VOICE_COMMAND_COME", True),
    ("b0fb847407ba409497c7311b88fffb9f", "EVT_VOICE_COMMAND_COME", True),
    ("a66e50204d954a1bb156d3602fbf9d7e", "EVT_VOICE_COMMAND_SIT", True),
    ("90d8f1f10f424a65a27f500f82e24e0a", "EVT_VOICE_COMMAND_COME", False),
    ("225457445ed647cf9751e4ee3d1ad2e3", "EVT_VOICE_COMMAND_SIT", False),
])
def test_captured_negations_are_blocked_and_positive_commands_survive(
    detectors, utterance_id, event_type, negated,
):
    sample_rate, samples = wavfile.read(Path(AUDIO_DIR) / utterance_id / "01_raw_capture.wav")
    baseline, guarded = [_replay(p, samples, sample_rate) for p in detectors]
    baseline_commands = [(t, e) for t, e in baseline if e.get("should_trigger_behavior_tree")]
    guarded_commands = [(t, e) for t, e in guarded if e.get("should_trigger_behavior_tree")]
    assert [e["event_type"] for _, e in baseline_commands] == [event_type]
    if negated:
        assert guarded_commands == []
        assert any(e.get("reason") == "negation_keyword" and e.get("keyword") == "不要"
                   for _, e in guarded)
    else:
        assert [e["event_type"] for _, e in guarded_commands] == [event_type]
        added_audio_sec = guarded_commands[0][0] - baseline_commands[0][0]
        assert 0.25 <= added_audio_sec <= 0.28
