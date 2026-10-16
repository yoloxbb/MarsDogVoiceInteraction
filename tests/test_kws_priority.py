"""First guarded KWS hit owns a turn; ASR uses catalog/templates then RKLLM."""

from pathlib import Path
import threading
from types import MethodType

import numpy as np
import pytest

from marsdog_voice_interaction.core.object_target_resolver import ObjectTargetResolver
from marsdog_voice_interaction.core.parameterized_commands import ParameterizedCommandRouter
from marsdog_voice_interaction.messages.intent_protocol import classification_to_event
from marsdog_voice_interaction.nodes.voice_interaction_node import VoiceInteractionNode
from marsdog_voice_interaction.utils.config_loader import load_config
from test_command_lexicon import _KwsRouteHarness
from test_kws_negation_guard import _provider


ROOT = Path(__file__).parents[1]


class _Intent:
    def __init__(self, tag=("NONE", "NONE", "NONE")):
        self.calls = []
        self.tag = tag

    def is_available(self):
        return True

    def parse_intent(self, text):
        self.calls.append(text)
        return classification_to_event(*self.tag, asr_text=text, source="rkllm")


def _node(text, *, commands=(), negations=(), tag=("NONE", "NONE", "NONE")):
    node = _KwsRouteHarness("COME", text)
    node._providers["kws"] = _provider(commands, negations)
    node._providers["intent_llm"] = _Intent(tag)
    node._parse_intent = MethodType(VoiceInteractionNode._parse_intent, node)
    node._kws_arbitration["publish_mode"] = "immediate"
    node._parameterized_commands = ParameterizedCommandRouter(
        ObjectTargetResolver(ROOT / "config/object_targets.yaml"), ROOT / "config/location_targets.yaml"
    )
    node._interaction_lock = threading.RLock()
    node._command_fuzzy_matching = True
    node._latest_wake_id = "wake-1"
    node._speech_job_is_current = lambda _job: True
    return node


def _capture(node, chunks=10):
    for _ in range(chunks):
        node._providers["kws"].accept_waveform(np.zeros(1600, dtype=np.float32), 16000)
        node._poll_kws_events()


def _finish(node):
    node._providers["kws"].finish_utterance()
    job = {"audio_data": {"audio_samples": [0.1], "sample_rate": 16000},
           "tracker": node._command_tracker, "wake_id": "wake-1"}
    recognition = VoiceInteractionNode._recognize_speech(node, job)
    node._process_speech(job["audio_data"], "utterance-1", recognition=recognition)


def _business(node):
    return [e for e in node.published if e.get("should_trigger_behavior_tree")]


@pytest.mark.parametrize("text", ["过来", "过来过来过来", "去拿球", "", "后来改成坐下"])
def test_one_guarded_hit_publishes_before_asr_and_skips_intent(text):
    node = _node(text, commands=("COME",))
    _capture(node, 3)
    assert not _business(node)
    _capture(node, 1)
    assert [e["event_type"] for e in _business(node)] == ["EVT_VOICE_COMMAND_COME"]
    assert _business(node)[0]["asr_text"] == ""
    assert not node._providers["intent_llm"].calls
    _finish(node)
    assert len(_business(node)) == 1
    assert not node._providers["intent_llm"].calls


def test_later_different_keywords_do_not_publish_another_command():
    node = _node("过来坐下站起来", commands=("COME", "SIT", "STAND_UP", "COME"))
    _capture(node)
    assert [e["action"] for e in _business(node)] == ["COME"]
    assert any(f.get("result") == "suppressed_after_first_kws" for _, f in node.traces)
    _finish(node)
    assert [e["action"] for e in _business(node)] == ["COME"]
    assert not node._providers["intent_llm"].calls


@pytest.mark.parametrize("text,event", [
    ("过来", "EVT_VOICE_COMMAND_COME"),
    ("去拿球", "EVT_VOICE_COMMAND_FETCH"),
    ("到客厅去", "EVT_VOICE_COMMAND_GO_TO_LOCATION"),
])
def test_without_kws_catalog_and_templates_precede_rkllm(text, event):
    node = _node(text)
    _capture(node)
    _finish(node)
    assert [e["event_type"] for e in _business(node)] == [event]
    assert not node._providers["intent_llm"].calls


def test_without_kws_or_deterministic_match_asr_reaches_rkllm_once():
    text = "今天有点累需要陪伴"
    node = _node(text)
    assert node._command_lexicon.match_fuzzy(text) is None
    assert node._parameterized_commands.route(text) is None
    _capture(node)
    _finish(node)
    assert node._providers["intent_llm"].calls == [text]
    assert any(e.get("intent_source") == "rkllm" for e in node.published)


@pytest.mark.parametrize("keyword,text", [
    ("不要", "不要过去"), ("不要", "过去"),  # ASR loses the prefix.
    ("别", "别过去"), ("不准", "不准过去"), ("不许", "不许过去"),
])
def test_acoustic_negation_vetoes_keyword_and_all_asr_business_fallback(keyword, text):
    node = _node(text, commands=("WALK",), negations=(keyword,))
    _capture(node)
    _finish(node)
    assert not _business(node)
    assert not node._providers["intent_llm"].calls
    assert node._command_tracker.kws_block_reason == "negation_keyword"


def test_negation_detected_during_confirmation_window_vetoes_keyword():
    node = _node("不要过去", commands=("WALK",), negations=("", "", "不要"))
    _capture(node)
    _finish(node)
    assert not _business(node)
    assert not node._providers["intent_llm"].calls


@pytest.mark.parametrize("text,keyword,command", [
    ("不要动", "不要", "HOLD_POSITION"), ("不可以", "不准", "HOLD_POSITION"),
    ("别动啊", "别", "HOLD_POSITION"), ("不许动啊", "不许", "HOLD_POSITION"),
    ("别叫了", "别", "QUIET"), ("不要叫", "不要", "QUIET"),
    ("不可以吃", "不准", "DROP"), ("不可以咬", "不准", "DROP"),
])
def test_exact_product_control_phrases_survive_negation_veto(text, keyword, command):
    node = _node(text, commands=(command,), negations=(keyword,))
    _capture(node)
    _finish(node)
    assert [e["action"] for e in _business(node)] == [command]
    assert not node._providers["intent_llm"].calls


@pytest.mark.parametrize("text,intent", [
    ("不要过去", "GO"), ("不可以过来", "COME"), ("禁止坐下", "SIT"),
])
def test_model_action_gate_vetoes_text_negation_when_acoustic_veto_is_missing(text, intent):
    node = _node(text, tag=("NONE", intent, "DO"))
    _capture(node)
    _finish(node)
    assert node._providers["intent_llm"].calls == [text]
    assert not any(e.get("is_executable") for e in node.published)
    assert any(
        slot.get("key") == "model_action_gate_reason" and slot.get("value") == "negated_action_text"
        for event in node.published for slot in event.get("slots", [])
    )


def test_production_config_selects_first_hit_and_disables_intent_fallback():
    config = load_config(ROOT / "config/voice.yaml")
    kws = config["providers"]["kws"]["config"]
    assert kws["publish_mode"] == "immediate"
    assert kws["negation_guard_enabled"]
    assert kws["negation_guard_window_sec"] == 0.25
    assert not config["providers"]["intent_llm"]["enabled"]
    assert not config["providers"]["intent_rule"]["enabled"]
