from __future__ import annotations

import time
from pathlib import Path
import threading
from typing import Any

import pytest
import yaml

from marsdog_voice_interaction.core.command_lexicon import CommandLexicon
from marsdog_voice_interaction.core.object_target_resolver import (
    ObjectTargetResolver,
)
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
from marsdog_voice_interaction.messages.intent_protocol import (
    classification_to_event,
)
from marsdog_voice_interaction.providers.kws_sherpa import KWSSherpaProvider


CATALOG_PATH = Path(__file__).parents[1] / "config" / "command_catalog.yaml"
OBJECT_TARGETS_PATH = (
    Path(__file__).parents[1] / "config" / "object_targets.yaml"
)


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
    assert match.emit_known_event
    assert match.nlu_social == "NONE"
    assert match.nlu_intent != ""
    assert match.nlu_control in {"DO", "STOP"}


def test_catalog_uses_exact_normalized_match_and_preserves_negation() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    assert lexicon.match("坐 下！").command_key == "SIT"  # type: ignore[union-attr]
    assert lexicon.match("你想不想吃") is None
    assert lexicon.match("不要坐下") is None
    assert lexicon.match("请你不要坐下") is None


def test_catalog_generates_ten_auditable_variants_per_phrase() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    expansion = raw["expansion"]

    assert lexicon.expansion_enabled
    assert lexicon.variants_per_phrase == 10
    assert lexicon.expansion_profile_count == 5
    assert lexicon.expanded_phrase_count == 1550
    assert lexicon.total_match_phrase_count == 1705

    command_profiles = expansion["command_profiles"]
    phrase_profiles = expansion["phrase_profiles"]
    checked = 0
    for command in raw["commands"]:
        for phrase in command["phrases"]:
            profile = phrase_profiles.get(
                phrase,
                command_profiles.get(
                    command["command_key"],
                    expansion["default_profile"],
                ),
            )
            for rule in expansion["profiles"][profile]:
                expanded = rule["template"].format(phrase=phrase)
                match = lexicon.match(expanded)
                assert match is not None
                assert match.command_key == command["command_key"]
                assert match.catalog_phrase == phrase
                assert match.matched_phrase == expanded
                assert match.match_strategy == "rule_expansion"
                assert match.expansion_profile == profile
                assert match.expansion_rule == rule["id"]
                checked += 1

    assert checked == 1550


@pytest.mark.parametrize(
    ("text", "command_key", "catalog_phrase", "profile"),
    [
        ("请你坐下", "SIT", "坐下", "command"),
        ("宝贝，太棒了", "PRAISE", "太棒了", "social"),
        ("我想问，你在哪里", "ASK_WHERE_ARE_YOU", "你在哪里", "query"),
        ("跟你说，我好孤独", "OWNER_LONELY", "我好孤独", "statement"),
        ("嘿，小狗", "CALL_NAME", "小狗", "vocative"),
    ],
)
def test_representative_expansions_route_without_intent_model(
    text: str,
    command_key: str,
    catalog_phrase: str,
    profile: str,
) -> None:
    match = CommandLexicon(CATALOG_PATH).match(text)

    assert match is not None
    assert match.command_key == command_key
    assert match.catalog_phrase == catalog_phrase
    assert match.match_strategy == "rule_expansion"
    assert match.expansion_profile == profile


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
    assert slots["match_strategy"] == "catalog_exact"


def test_expanded_event_carries_rule_audit_metadata() -> None:
    match = CommandLexicon(CATALOG_PATH).match("请你坐下")

    assert match is not None
    event = match.to_event(asr_text="请你坐下", language="zh")
    slots = {item["key"]: item["value"] for item in event["slots"]}
    assert slots["matched_phrase"] == "请你坐下"
    assert slots["catalog_phrase"] == "坐下"
    assert slots["match_strategy"] == "rule_expansion"
    assert slots["expansion_profile"] == "command"
    assert slots["expansion_rule"] == "polite_please_you"


def test_catalog_exposes_core_metadata_by_command_key() -> None:
    command = CommandLexicon(CATALOG_PATH).get_command("high_five")

    assert command is not None
    assert command.command_key == "HIGH_FIVE"
    assert command.command_id == "CMD_FIVE"
    assert command.event_type == "EVT_VOICE_COMMAND_HIGH_FIVE"
    assert command.emit_known_event


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
    _effective_kws_arbitration = (
        VoiceInteractionNode._effective_kws_arbitration
    )
    _is_short_asr_text = VoiceInteractionNode._is_short_asr_text
    _select_kws_candidate = VoiceInteractionNode._select_kws_candidate
    _trace_recognition_arbitration = (
        VoiceInteractionNode._trace_recognition_arbitration
    )
    _publish_selected_kws_candidate = (
        VoiceInteractionNode._publish_selected_kws_candidate
    )

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
        self._kws_arbitration = {
            "publish_mode": "deferred",
            "arbitration_mode": "exclusive",
            "asr_long_text_wins": True,
            "kws_fallback_on_asr_empty": True,
            "short_max_chars_zh": 2,
            "short_max_words_en": 2,
        }
        self.published: list[dict[str, Any]] = []
        self.traces: list[tuple[str, dict[str, Any]]] = []
        self.intent_called = False

    def _publish(self, event: dict[str, Any]) -> None:
        self.published.append(dict(event))

    def _trace(self, record: str, **fields: Any) -> None:
        self.traces.append((record, fields))

    def _refresh_interaction_activity(self) -> None:
        pass

    def _parse_intent(self, _text: str) -> dict[str, Any] | None:
        self.intent_called = True
        raise AssertionError("direct command must skip intent providers")


class _KwsRouteHarness:
    _poll_kws_events = VoiceInteractionNode._poll_kws_events
    _process_speech = VoiceInteractionNode._process_speech
    _clean_text = staticmethod(VoiceInteractionNode._clean_text)
    _effective_kws_arbitration = (
        VoiceInteractionNode._effective_kws_arbitration
    )
    _is_short_asr_text = VoiceInteractionNode._is_short_asr_text
    _select_kws_candidate = VoiceInteractionNode._select_kws_candidate
    _trace_recognition_arbitration = (
        VoiceInteractionNode._trace_recognition_arbitration
    )
    _publish_selected_kws_candidate = (
        VoiceInteractionNode._publish_selected_kws_candidate
    )

    def __init__(
        self,
        command_key: str = "HIGH_FIVE",
        asr_text: str = "机长",
    ) -> None:
        kws = KWSSherpaProvider({})
        kws.available = True
        kws._queue_keyword(command_key)
        self._providers = {
            "kws": kws,
            "asr": _FakeASR(asr_text),
            "speaker": _FakeSpeaker(),
        }
        self._speaker_operation_lock = threading.RLock()
        self._state_machine = VoiceInteractionStateMachine()
        self._state_machine.force_state(State.ATTENTION)
        self._interaction_id = "interaction-1"
        self._command_tracker = UtteranceCommandTracker()
        self._command_tracker.begin("utterance-1")
        self._command_lexicon = CommandLexicon(CATALOG_PATH)
        self._kws_arbitration = {
            "publish_mode": "deferred",
            "arbitration_mode": "exclusive",
            "asr_long_text_wins": True,
            "kws_fallback_on_asr_empty": True,
            "short_max_chars_zh": 2,
            "short_max_words_en": 2,
        }
        self._utterance_started_monotonic = time.perf_counter()
        self.published: list[dict[str, Any]] = []
        self.traces: list[tuple[str, dict[str, Any]]] = []
        self.activity_refreshed = False
        self.intent_called = False

    def _publish(self, event: dict[str, Any]) -> None:
        self.published.append(dict(event))

    def _trace(self, record: str, **fields: Any) -> None:
        self.traces.append((record, fields))

    def _refresh_interaction_activity(self) -> None:
        self.activity_refreshed = True

    def _parse_intent(self, text: str) -> dict[str, Any]:
        self.intent_called = True
        return classification_to_event(
            "NONE",
            "NONE",
            "NONE",
            asr_text=text,
            source="rkllm",
        )


def test_core_kws_is_cached_without_publishing_before_arbitration() -> None:
    node = _KwsRouteHarness()

    node._poll_kws_events()

    assert node.published == []
    assert node._command_tracker.kws_candidate_count == 1
    candidate = node._command_tracker.single_kws_candidate()
    assert candidate is not None
    assert candidate["event_type"] == "EVT_VOICE_COMMAND_HIGH_FIVE"
    assert not node.activity_refreshed
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "kws"
        and fields.get("result") == "candidate"
        and fields.get("candidate_count") == 1
        and fields.get("published_event_types") == []
        for record, fields in node.traces
    )


def test_asr_homophone_does_not_remove_or_repeat_core_kws_events() -> None:
    node = _KwsRouteHarness(asr_text="机长")

    node._poll_kws_events()
    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    event_types = [event["event_type"] for event in node.published]
    assert event_types.count("EVT_VOICE_COMMAND_KNOWN") == 1
    assert event_types.count("EVT_VOICE_COMMAND_HIGH_FIVE") == 1
    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "command_lexicon"
        and fields.get("result") == "no_match"
        for record, fields in node.traces
    )
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("result") == "kws_selected"
        and fields.get("reason") == "short_asr_kws_preferred"
        for record, fields in node.traces
    )
    assert any(
        record == "utterance_complete"
        and fields.get("result") == "published_kws_selected"
        for record, fields in node.traces
    )


def test_direct_catalog_match_publishes_event_and_skips_intent_model() -> None:
    node = _DirectRouteHarness()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert not node.intent_called
    catalog_events = [
        event for event in node.published
        if event.get("intent_source") == "command_lexicon"
    ]
    assert [event["event_type"] for event in catalog_events] == [
        "EVT_VOICE_COMMAND_KNOWN",
        "EVT_VOICE_COMMAND_SIT",
    ]
    summary, direct = catalog_events
    assert summary["dispatch_role"] == "recognition_summary"
    assert summary["specific_event_type"] == "EVT_VOICE_COMMAND_SIT"
    assert not summary["should_trigger_behavior_tree"]
    assert direct["event_type"] == "EVT_VOICE_COMMAND_SIT"
    assert direct["dispatch_role"] == "specific_command"
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
        and fields.get("result") == "published_known_and_specific"
        and fields.get("published_event_types") == [
            "EVT_VOICE_COMMAND_KNOWN",
            "EVT_VOICE_COMMAND_SIT",
        ]
        for record, fields in node.traces
    )


def test_expanded_catalog_match_publishes_event_and_skips_intent_model() -> None:
    node = _DirectRouteHarness(text="请你坐下")

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "command_lexicon"
        and fields.get("result") == "matched"
        and fields.get("match_strategy") == "rule_expansion"
        and fields.get("catalog_phrase") == "坐下"
        and fields.get("matched_phrase") == "请你坐下"
        and fields.get("expansion_profile") == "command"
        and fields.get("expansion_rule") == "polite_please_you"
        for record, fields in node.traces
    )
    catalog_events = [
        event for event in node.published
        if event.get("intent_source") == "command_lexicon"
    ]
    assert [event["event_type"] for event in catalog_events] == [
        "EVT_VOICE_COMMAND_KNOWN",
        "EVT_VOICE_COMMAND_SIT",
    ]
    for event in catalog_events:
        slots = {slot["key"]: slot["value"] for slot in event["slots"]}
        assert slots["catalog_phrase"] == "坐下"
        assert slots["matched_phrase"] == "请你坐下"
        assert slots["match_strategy"] == "rule_expansion"
        assert slots["expansion_profile"] == "command"
        assert slots["expansion_rule"] == "polite_please_you"


@pytest.mark.parametrize(
    ("text", "event_type", "emotion", "category"),
    [
        ("真聪明", "EVT_VOICE_PRAISE", "PRAISE", "praise"),
        ("坏狗狗", "EVT_VOICE_SCOLD", "SCOLD", "blame"),
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
    assert direct["social"] == emotion
    assert direct["intent"] == "NONE"
    assert direct["raw_nlu_tag"] == f"{emotion}|NONE|NONE"
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


def test_short_asr_catalog_agreement_selects_kws_result_group() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="坐下")
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    business_events = [
        event for event in node.published
        if event.get("intent_source") in {"kws", "command_lexicon"}
    ]
    assert [event["event_type"] for event in business_events] == [
        "EVT_VOICE_COMMAND_KNOWN",
        "EVT_VOICE_COMMAND_SIT",
    ]
    assert all(event["intent_source"] == "kws" for event in business_events)
    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("reason") == "short_asr_catalog_agrees"
        for record, fields in node.traces
    )


def test_long_asr_text_containing_keyword_selects_asr_catalog() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="请你坐下")
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    business_events = [
        event for event in node.published
        if event.get("intent_source") in {"kws", "command_lexicon"}
    ]
    assert [event["event_type"] for event in business_events] == [
        "EVT_VOICE_COMMAND_KNOWN",
        "EVT_VOICE_COMMAND_SIT",
    ]
    assert all(
        event["intent_source"] == "command_lexicon"
        for event in business_events
    )
    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("result") == "asr_selected"
        and fields.get("reason") == "long_asr_text"
        for record, fields in node.traces
    )


def test_short_conflicting_asr_catalog_result_wins_over_kws() -> None:
    node = _KwsRouteHarness(command_key="STAND_UP", asr_text="坐下")
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    business_events = [
        event for event in node.published
        if event.get("intent_source") in {"kws", "command_lexicon"}
    ]
    assert [event["event_type"] for event in business_events] == [
        "EVT_VOICE_COMMAND_KNOWN",
        "EVT_VOICE_COMMAND_SIT",
    ]
    assert all(
        event["intent_source"] == "command_lexicon"
        for event in business_events
    )
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("result") == "asr_selected"
        and fields.get("reason") == "asr_catalog_conflicts_with_kws"
        for record, fields in node.traces
    )


def test_long_asr_text_without_catalog_match_does_not_trigger_kws() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="不要坐下")
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert node.intent_called
    assert not any(
        event.get("intent_source") == "kws" for event in node.published
    )
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("result") == "asr_selected"
        and fields.get("reason") == "long_asr_text"
        for record, fields in node.traces
    )


def test_empty_asr_uses_single_kws_candidate_as_fallback() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="")
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    business_events = [
        event for event in node.published if event.get("intent_source") == "kws"
    ]
    assert [event["event_type"] for event in business_events] == [
        "EVT_VOICE_COMMAND_KNOWN",
        "EVT_VOICE_COMMAND_SIT",
    ]
    assert not any(event["event_type"] == "speech" for event in node.published)
    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("reason") == "empty_asr_single_candidate"
        for record, fields in node.traces
    )


def test_multiple_kws_candidates_defer_to_asr_pipeline() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="不要坐下")
    node._providers["kws"]._queue_keyword("STAND_UP")
    node._poll_kws_events()

    assert node._command_tracker.kws_candidate_count == 2
    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert node.intent_called
    assert not any(
        event.get("intent_source") == "kws" for event in node.published
    )
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("reason") == "multiple_kws_candidates"
        for record, fields in node.traces
    )


class _ModelRouteHarness(_DirectRouteHarness):
    def __init__(
        self,
        text: str,
        labels: tuple[str, str, str] | None,
    ) -> None:
        super().__init__(text)
        self._labels = labels
        self._object_target_resolver = ObjectTargetResolver(
            OBJECT_TARGETS_PATH
        )

    def _parse_intent(self, text: str) -> dict[str, Any] | None:
        self.intent_called = True
        if self._labels is None:
            return None
        social, intent, control = self._labels
        return classification_to_event(
            social,
            intent,
            control,
            asr_text=text,
            source="rkllm",
        )


def test_catalog_miss_routes_model_intent_to_social_specific_and_summary() -> None:
    node = _ModelRouteHarness("真乖请坐好", ("PRAISE", "SIT", "DO"))

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    model_events = [
        event for event in node.published
        if event.get("intent_source") == "rkllm"
    ]
    assert node.intent_called
    assert [event["event_type"] for event in model_events] == [
        "EVT_VOICE_PRAISE",
        "EVT_VOICE_COMMAND_SIT",
        "EVT_VOICE_COMMAND_KNOWN",
    ]
    assert [
        event["should_trigger_behavior_tree"] for event in model_events
    ] == [False, True, False]
    assert model_events[1]["dispatch_role"] == "specific_command"
    assert model_events[2]["specific_event_type"] == (
        "EVT_VOICE_COMMAND_SIT"
    )


def test_catalog_miss_with_all_none_publishes_neutral_event() -> None:
    node = _ModelRouteHarness("读一下消息", ("NONE", "NONE", "NONE"))

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    model_events = [
        event for event in node.published
        if event.get("intent_source") == "rkllm"
    ]
    assert [event["event_type"] for event in model_events] == [
        "EVT_VOICE_NEUTRAL"
    ]
    assert not model_events[0]["should_trigger_behavior_tree"]
    assert any(
        record == "utterance_complete"
        and fields.get("result") == "published"
        for record, fields in node.traces
    )


def test_model_find_query_routes_only_supported_detector_target() -> None:
    supported = _ModelRouteHarness(
        "看看那个球在哪里",
        ("NONE", "FIND_TOY", "QUERY"),
    )
    unsupported = _ModelRouteHarness(
        "看看那个布偶娃娃在哪里",
        ("NONE", "FIND_TOY", "QUERY"),
    )

    assert supported._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )
    assert unsupported._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    supported_events = [
        event for event in supported.published
        if event.get("intent_source") == "rkllm"
    ]
    unsupported_events = [
        event for event in unsupported.published
        if event.get("intent_source") == "rkllm"
    ]
    assert [event["event_type"] for event in supported_events] == [
        "EVT_VOICE_COMMAND_FETCH",
        "EVT_VOICE_STATUS_CARE",
    ]
    assert supported_events[0]["should_trigger_behavior_tree"]
    supported_slots = {
        slot["key"]: slot["value"] for slot in supported_events[0]["slots"]
    }
    assert supported_slots["object_name"] == "dog toy ball"

    assert [event["event_type"] for event in unsupported_events] == [
        "EVT_VOICE_STATUS_CARE"
    ]
    assert not unsupported_events[0]["should_trigger_behavior_tree"]
    unsupported_slots = {
        slot["key"]: slot["value"]
        for slot in unsupported_events[0]["slots"]
    }
    assert unsupported_slots["object_name"] == "NONE"
    assert unsupported_slots["object_mention"] == "布偶娃娃"
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "object_target"
        and fields.get("result") == "unsupported"
        for record, fields in unsupported.traces
    )
