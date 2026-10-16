"""Deferred-mode repeat exception, from audio chunks to business events."""

import numpy as np
import pytest
from pathlib import Path
from types import SimpleNamespace

from marsdog_voice_interaction.core.kws_repeat_policy import KWSRepeatPolicy
from marsdog_voice_interaction.core.object_target_resolver import ObjectTargetResolver
from marsdog_voice_interaction.core.parameterized_commands import ParameterizedCommandRouter
from marsdog_voice_interaction.nodes.voice_interaction_node import VoiceInteractionNode
from marsdog_voice_interaction.providers.kws_sherpa import KWSSherpaProvider
from marsdog_voice_interaction.utils.config_loader import load_config
from test_command_lexicon import _KwsRouteHarness
from test_kws_negation_guard import _Spotter


CONFIG = {
    "publish_mode": "deferred",
    "negation_guard_enabled": True,
    "repeat_early_command_keys": ["COME"],
    "repeat_early_min_hits": 2,
    "repeat_early_min_gap_sec": 0.3,
    "repeat_early_max_gap_sec": 1.5,
}


def _node(commands, *, negations=(), text="过来过来过来过来", **overrides):
    config = {**CONFIG, **overrides}
    node = _KwsRouteHarness(command_key="COME", asr_text=text)
    provider = KWSSherpaProvider(config)
    provider._spotter = _Spotter(commands)
    provider._negation_spotter = _Spotter(negations)
    provider.available = True
    provider.start_utterance()
    node._providers["kws"] = provider
    node._kws_arbitration.update(config)
    return node


def _feed(node, count=1):
    for _ in range(count):
        node._providers["kws"].accept_waveform(np.zeros(1600, dtype=np.float32), 16000)
        node._poll_kws_events()


def _business(node):
    return [event for event in node.published if event.get("should_trigger_behavior_tree")]


def _finish(node):
    node._providers["kws"].finish_utterance()
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")


def test_repeat_publishes_before_vad_end_and_asr_without_duplicate():
    node = _node(("COME", "", "", "", "COME", "", "", "", "COME", "", "", "", "COME"))
    _feed(node, 5)
    assert node._command_tracker.kws_candidate_count == 1
    assert not _business(node)  # second detection still awaits its guard window
    _feed(node, 3)
    assert len(_business(node)) == 1
    event = _business(node)[0]
    assert event["event_type"] == "EVT_VOICE_COMMAND_COME"
    assert event["command_id"] == "CMD_COME_HERE"
    assert event["utterance_id"] == "utterance-1"
    assert event["asr_text"] == ""
    assert event["speaker_status"] == "pending"
    slots = {slot["key"]: slot["value"] for slot in event["slots"]}
    assert slots["recognition_strategy"] == "kws_repeat_early"
    assert slots["kws_repeat_hits"] == "2"
    assert slots["kws_negation_guard"] == "passed"
    assert node.activity_reasons == ["kws_repeat_early"]
    assert any(fields.get("result") == "published_repeat_early" for _, fields in node.traces)
    _feed(node, 10)
    _finish(node)
    assert len(_business(node)) == 1
    assert any(event.get("event_type") == "speech" and event["asr_text"] == "过来过来过来过来"
               for event in node.published)
    assert any(fields.get("result") == "published_kws_repeat_early" for _, fields in node.traces)


@pytest.mark.parametrize("text", ["", "不要过来", "去拿球"])
def test_asr_cannot_publish_a_second_business_result_after_early_release(text):
    node = _node(("COME", "", "", "", "COME"), text=text)
    _feed(node, 9)
    _finish(node)
    assert [event["event_type"] for event in _business(node)] == ["EVT_VOICE_COMMAND_COME"]


@pytest.mark.parametrize("negations", [
    ("不要",), ("", "", "", "", "不要"), ("", "", "", "", "", "", "不要"),
    (RuntimeError("decode failed"),),
])
def test_negation_or_decoder_failure_prevents_repeat_and_asr_fallback(negations):
    node = _node(("COME", "", "", "", "COME"), negations=negations, text="过来")
    _feed(node, 10)
    _finish(node)
    assert not _business(node)
    assert node._command_tracker.kws_block_reason


def test_late_negation_records_that_the_command_was_already_published():
    node = _node(("COME", "", "", "", "COME"),
                 negations=("",) * 8 + ("不要",))
    _feed(node, 10)
    _finish(node)
    assert len(_business(node)) == 1
    assert any(fields.get("result") == "late_veto" for _, fields in node.traces)
    assert any(fields.get("result") == "negation_after_kws_publish" for _, fields in node.traces)


@pytest.mark.parametrize("commands", [
    ("COME",),  # single command remains deferred
    ("SIT", "", "", "", "SIT"),  # outside the allowlist
    ("COME", "COME"),  # immediate decoder retrigger is not a second word
    ("COME",) + ("",) * 16 + ("COME",),  # outside the repeat window
    ("COME", "SIT", "", "", "COME"),  # different command breaks the sequence
    ("SIT", "COME", "", "", "", "COME"),  # mixed candidates remain deferred
])
def test_unconfirmed_or_ambiguous_commands_remain_deferred(commands):
    node = _node(commands)
    _feed(node, len(commands) + 4)
    assert not _business(node)


def test_repeat_exception_can_be_disabled():
    node = _node(("COME", "", "", "", "COME"), repeat_early_command_keys=[])
    _feed(node, 10)
    assert not _business(node)


def test_single_keyword_in_a_parameterized_sentence_still_uses_whole_asr():
    root = Path(__file__).parents[1]
    node = _node(("COME",), text="把球拿过来")
    node._parameterized_commands = ParameterizedCommandRouter(
        ObjectTargetResolver(root / "config/object_targets.yaml"), root / "config/location_targets.yaml"
    )
    _feed(node, 10)
    assert not _business(node)
    _finish(node)
    assert [event["event_type"] for event in _business(node)] == ["EVT_VOICE_COMMAND_FETCH"]


def test_configurable_hit_count_waits_for_three_detections():
    node = _node(("COME", "", "", "", "COME", "", "", "", "COME"),
                 repeat_early_min_hits=3)
    _feed(node, 8)
    assert not _business(node)
    _feed(node, 4)
    assert len(_business(node)) == 1


def test_next_utterance_can_publish_the_same_repeated_command():
    node = _node(("COME", "", "", "", "COME"))
    _feed(node, 10)
    assert len(_business(node)) == 1
    node._command_tracker.begin("utterance-2")
    node._providers["kws"].start_utterance()
    _feed(node, 10)
    assert [event["utterance_id"] for event in _business(node)] == ["utterance-1", "utterance-2"]


def test_vad_end_does_not_shorten_the_repeat_guard_window():
    node = _node(("COME", "", "", "", "COME"))
    _feed(node, 5)
    node._providers["kws"].finish_utterance()
    node._poll_kws_events()
    assert not _business(node)


def test_immediate_mode_keeps_its_existing_single_hit_semantics():
    node = _node(("COME", "", "", "", "COME"), publish_mode="immediate")
    _feed(node, 4)
    assert len(_business(node)) == 1
    _feed(node, 6)
    assert len(_business(node)) == 1
    assert any(fields.get("result") == "published_immediate" for _, fields in node.traces)


@pytest.mark.parametrize("overrides", [
    {"repeat_early_command_keys": "COME"}, {"repeat_early_command_keys": ["NO_SUCH_COMMAND"]},
    {"repeat_early_command_keys": [""]}, {"repeat_early_command_keys": [None]},
    {"repeat_early_min_hits": 1}, {"repeat_early_min_hits": 2.5}, {"repeat_early_min_hits": True},
    {"repeat_early_min_gap_sec": 0}, {"repeat_early_max_gap_sec": 0.1},
    {"repeat_early_max_gap_sec": float("inf")}, {"repeat_early_min_gap_sec": float("nan")},
    {"negation_guard_enabled": False},
])
def test_invalid_repeat_config_is_rejected(overrides):
    with pytest.raises(ValueError):
        KWSRepeatPolicy.from_config({**CONFIG, **overrides})


def test_optional_deferred_repeat_policy_and_node_policy_agree():
    config = load_config(Path(__file__).parents[1] / "config/voice.yaml")
    config["providers"]["kws"]["config"].update(CONFIG)
    policy = VoiceInteractionNode._load_kws_arbitration_config(
        SimpleNamespace(_config=config, _clean_text=VoiceInteractionNode._clean_text)
    )
    provider = KWSSherpaProvider(config["providers"]["kws"]["config"])
    assert policy["publish_mode"] == "deferred"
    assert policy["repeat_early_command_keys"] == provider._repeat_policy.command_keys == ("COME",)
    assert policy["repeat_early_min_hits"] == provider._repeat_policy.min_hits == 2
