from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

import pytest
import yaml

from marsdog_voice_interaction.core.command_lexicon import CommandLexicon
from marsdog_voice_interaction.core.interaction_state_machine import (
    State,
    VoiceInteractionStateMachine,
)
from marsdog_voice_interaction.core.utterance_command_tracker import (
    UtteranceCommandTracker,
)
from marsdog_voice_interaction.nodes.voice_interaction_node import (
    VoiceInteractionNode,
)


CATALOG_PATH = Path(__file__).parents[1] / "config" / "command_catalog.yaml"


@pytest.mark.parametrize(
    ("text", "command_key", "event_type"),
    [
        ("走", "WALK", "EVT_VOICE_COMMAND_WALK"),
        ("回来", "COME", "EVT_VOICE_COMMAND_COME"),
        ("跟我走", "FOLLOW", "EVT_VOICE_COMMAND_FOLLOW"),
        ("出去溜溜", "GO_OUT", "EVT_VOICE_COMMAND_GO_OUT"),
        ("回家", "GO_HOME", "EVT_VOICE_COMMAND_GO_HOME"),
        ("靠近点", "APPROACH", "EVT_VOICE_COMMAND_APPROACH"),
        ("退后", "BACK_UP", "EVT_VOICE_COMMAND_BACK_UP"),
        ("蹲下", "SIT", "EVT_VOICE_COMMAND_SIT"),
        ("躺下", "LIE_DOWN", "EVT_VOICE_COMMAND_LIE_DOWN"),
        ("biu", "PLAY_DEAD", "EVT_VOICE_COMMAND_PLAY_DEAD"),
        ("起来", "STAND_UP", "EVT_VOICE_COMMAND_STAND_UP"),
        ("站着", "STAND_STILL", "EVT_VOICE_COMMAND_STAND_STILL"),
        ("抬手", "SHAKE_HAND", "EVT_VOICE_COMMAND_SHAKE_HAND"),
        ("拍手", "HIGH_FIVE", "EVT_VOICE_COMMAND_HIGH_FIVE"),
        ("转圈", "SPIN", "EVT_VOICE_COMMAND_SPIN"),
        ("翻滚", "ROLL_OVER", "EVT_VOICE_COMMAND_ROLL_OVER"),
        ("不许动", "HOLD_POSITION", "EVT_VOICE_COMMAND_HOLD_POSITION"),
        ("松口", "DROP", "EVT_VOICE_COMMAND_DROP"),
        ("别叫", "QUIET", "EVT_VOICE_COMMAND_QUIET"),
    ],
)
def test_catalog_covers_all_19_core_command_groups(
    text: str,
    command_key: str,
    event_type: str,
) -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    match = lexicon.match(text)

    assert lexicon.command_count == 81
    assert lexicon.core_command_count == 19
    assert lexicon.phrase_count == 155
    assert lexicon.source_row_count == 116
    assert lexicon.covered_source_row_count == 116
    assert match is not None
    assert match.command_key == command_key
    assert match.event_type == event_type
    assert match.core


def test_catalog_uses_exact_normalized_match_and_does_not_take_queries() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    assert lexicon.match("坐 下！").command_key == "SIT"  # type: ignore[union-attr]
    assert lexicon.match("你想不想吃") is None
    assert lexicon.match("请你坐下") is None


@pytest.mark.parametrize(
    ("text", "command_key", "event_type", "action_name"),
    [
        ("小宝贝", "CALL_NAME", "EVT_VOICE_CALL_NAME", ""),
        ("真聪明", "PRAISE", "EVT_VOICE_PRAISE", ""),
        ("坏狗狗", "SCOLD", "EVT_VOICE_SCOLD", ""),
        (
            "吃饭",
            "EAT_MEAL",
            "EVT_VOICE_COMMAND_EAT_MEAL",
            "ACT_EAT_MEAL",
        ),
        (
            "肚子饿不饿",
            "RESPOND_HUNGRY_QUERY",
            "EVT_VOICE_COMMAND_RESPOND_HUNGRY_QUERY",
            "ACT_RESPOND_HUNGRY_QUERY",
        ),
        ("去便便", "TOILET", "EVT_VOICE_COMMAND_TOILET", ""),
        ("擦一擦脚", "CLEAN", "EVT_VOICE_COMMAND_CLEAN", ""),
        ("一起玩", "PLAY", "EVT_VOICE_COMMAND_PLAY", ""),
        (
            "去找妈妈",
            "FIND_MOM",
            "EVT_VOICE_COMMAND_FIND_MOM",
            "ACT_FIND_MOM",
        ),
        (
            "我好孤独",
            "OWNER_LONELY",
            "EVT_VOICE_COMMAND_OWNER_LONELY",
            "ACT_OWNER_LONELY",
        ),
    ],
)
def test_full_catalog_routes_representative_product_rows(
    text: str,
    command_key: str,
    event_type: str,
    action_name: str,
) -> None:
    match = CommandLexicon(CATALOG_PATH).match(text)

    assert match is not None
    assert match.command_key == command_key
    assert match.event_type == event_type
    assert match.action_name == action_name


def test_every_configured_phrase_matches_its_declared_command() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))

    checked = 0
    for command in raw["commands"]:
        for phrase in command["phrases"]:
            match = lexicon.match(str(phrase))
            assert match is not None
            assert match.command_key == command["command_key"]
            assert match.command_id == command["command_id"]
            assert match.event_type == command["event_type"]
            checked += 1

    assert checked == 155


def test_reference_english_phrases_are_metadata_not_runtime_triggers() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    assert lexicon.reference_phrase_count == 138
    assert lexicon.match("Good dog") is None
    assert lexicon.match("Come here") is None


def test_direct_event_carries_product_action_and_source_metadata() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)
    match = lexicon.match("吃饭")

    assert match is not None
    event = match.to_event(asr_text="吃饭", language="zh")
    slots = {item["key"]: item["value"] for item in event["slots"]}
    assert slots["action_name"] == "ACT_EAT_MEAL"
    assert slots["catalog_source_rows"] == "53"
    assert slots["behavior"] == "去指定地点进食"


class _FakeASR:
    def __init__(self, text: str = "坐下") -> None:
        self._text = text

    def transcribe(self, _audio_data: dict[str, Any]) -> dict[str, Any]:
        return {"asr_text": self._text, "language": "zh", "latency_ms": 3.0}


class _FakeSpeaker:
    def verify(self, _audio_data: dict[str, Any]) -> dict[str, Any]:
        return {"speaker_id": "tester", "confidence": 0.8}


class _DirectRouteHarness:
    _process_speech = VoiceInteractionNode._process_speech
    _clean_text = staticmethod(VoiceInteractionNode._clean_text)

    def __init__(self, text: str = "坐下") -> None:
        self._providers = {
            "asr": _FakeASR(text),
            "speaker": _FakeSpeaker(),
        }
        self._speaker_operation_lock = threading.RLock()
        self._state_machine = VoiceInteractionStateMachine()
        self._state_machine.force_state(State.ATTENTION)
        self._interaction_id = "interaction-1"
        self._command_tracker = UtteranceCommandTracker()
        self._command_tracker.begin("utterance-1")
        self._command_lexicon = CommandLexicon(CATALOG_PATH)
        self.published: list[dict[str, Any]] = []
        self.traces: list[tuple[str, dict[str, Any]]] = []
        self.intent_called = False

    def _publish(self, event: dict[str, Any]) -> None:
        self.published.append(dict(event))

    def _trace(self, record: str, **fields: Any) -> None:
        self.traces.append((record, fields))

    def _parse_intent(self, _text: str) -> dict[str, Any] | None:
        self.intent_called = True
        raise AssertionError("direct command must skip intent providers")


def test_direct_catalog_match_publishes_event_and_skips_intent_model() -> None:
    node = _DirectRouteHarness()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert not node.intent_called
    direct = node.published[-1]
    assert direct["event_type"] == "EVT_VOICE_COMMAND_SIT"
    assert direct["intent_source"] == "command_lexicon"
    assert direct["should_trigger_behavior_tree"]
    assert direct["utterance_id"] == "utterance-1"
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "command_lexicon"
        and fields.get("result") == "matched"
        and fields.get("action_name") == "ACT_SIT"
        and fields.get("source_rows") == [8]
        for record, fields in node.traces
    )
    assert any(
        record == "utterance_complete"
        and fields.get("result") == "published_direct_command"
        for record, fields in node.traces
    )


@pytest.mark.parametrize(
    ("text", "event_type", "emotion", "category"),
    [
        ("真聪明", "EVT_VOICE_PRAISE", "PRAISE", "praise"),
        ("坏狗狗", "EVT_VOICE_SCOLD", "REPRIMAND", "blame"),
    ],
)
def test_social_catalog_event_skips_intent_without_becoming_executable(
    text: str,
    event_type: str,
    emotion: str,
    category: str,
) -> None:
    node = _DirectRouteHarness(text)

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    direct = node.published[-1]
    assert not node.intent_called
    assert direct["event_type"] == event_type
    assert direct["emotion"] == emotion
    assert direct["action"] == "NONE"
    assert direct["control"] == "NONE"
    assert direct["intent_category"] == category
    assert not direct["is_executable"]
    assert not direct["should_trigger_behavior_tree"]
    assert node._state_machine.state == State.ATTENTION
    assert any(
        record == "utterance_complete"
        and fields.get("result") == "published_catalog_event"
        for record, fields in node.traces
    )


def test_direct_catalog_match_is_suppressed_after_same_kws_event() -> None:
    node = _DirectRouteHarness()
    node._command_tracker.record_immediate("EVT_VOICE_COMMAND_SIT")

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert not any(
        event.get("intent_source") == "command_lexicon"
        for event in node.published
    )
    assert any(
        record == "utterance_complete"
        and fields.get("result") == "suppressed_duplicate"
        for record, fields in node.traces
    )


def test_conflicting_catalog_event_is_suppressed_after_kws_event() -> None:
    node = _DirectRouteHarness()
    node._command_tracker.record_immediate("EVT_VOICE_COMMAND_STAND_UP")

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert not any(
        event.get("intent_source") == "command_lexicon"
        for event in node.published
    )
    assert any(
        record == "command_conflict"
        and fields.get("result") == "suppressed"
        and fields.get("catalog_event_type") == "EVT_VOICE_COMMAND_SIT"
        for record, fields in node.traces
    )
