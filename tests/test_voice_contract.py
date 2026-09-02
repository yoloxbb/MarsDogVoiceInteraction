from pathlib import Path

import pytest

from marsdog_voice_interaction.messages.audio_event import (
    WAKE_ANGLE_FRAME_ID,
    normalize_audio_event,
)
from marsdog_voice_interaction.messages.intent_protocol import (
    NLU_PROTOCOL,
    parse_intent_tag,
)
from marsdog_voice_interaction.messages.intent_event_router import (
    route_classification_events,
)
from marsdog_voice_interaction.messages.voice_event_types import (
    ACTION_TO_VOICE_EVENT,
    EVT_VOICE_CALL_NAME,
    EVT_VOICE_COMMAND_FETCH,
    EVT_VOICE_COMMAND_KNOWN,
    EVT_VOICE_COMMAND_SIT,
    EVT_VOICE_FOLK_ID,
    EVT_VOICE_MASTER_ID,
    EVT_VOICE_NEUTRAL,
    EVT_VOICE_STATUS_CARE,
    EVT_VOICE_UNMASTER_ID,
    classification_to_voice_event,
    speaker_to_voice_event,
)
from marsdog_voice_interaction.core.utterance_command_tracker import (
    UtteranceCommandTracker,
)
from marsdog_voice_interaction.providers.mock_event import MockEventProvider
from marsdog_voice_interaction.providers.mock_wakeup import MockWakeupProvider
from marsdog_voice_interaction.providers.asr_sherpa import (
    _normalize_sense_voice_language,
)
from marsdog_voice_interaction.providers.rule_intent import (
    RuleIntentProvider,
    _RULES,
)


def test_audio_contract_has_no_visual_binding() -> None:
    value = normalize_audio_event({
        "event_type": "speech",
        "utterance_id": "u1",
        "asr_text": "坐下",
    })
    assert value["schema_version"] == 2
    assert value["utterance_id"] == "u1"
    assert "target_track_id" not in value
    assert "target_identity" not in value


def test_audio_contract_preserves_interaction_id() -> None:
    value = normalize_audio_event({
        "event_type": "EVT_VOICE_CALL_NAME",
        "interaction_id": "session-1",
    })
    assert value["interaction_id"] == "session-1"


def test_audio_contract_bounds_wake_confidence_and_preserves_raw_score() -> None:
    value = normalize_audio_event({
        "event_type": "EVT_VOICE_CALL_NAME",
        "wake_confidence": 1.8,
        "wake_score_raw": 907.0,
    })

    assert value["wake_confidence"] == 1.0
    assert value["wake_score_raw"] == 907.0


def test_wake_angle_contract_uses_raw_microphone_array_frame() -> None:
    value = normalize_audio_event({
        "event_type": EVT_VOICE_CALL_NAME,
        "wake_angle": 42.0,
    })
    direct_mock = MockEventProvider({}).build_event(EVT_VOICE_CALL_NAME)
    pipeline_mock = MockWakeupProvider({
        "mock_min_interval_sec": 1.0,
        "mock_max_interval_sec": 1.0,
    })
    pipeline_mock.start()
    pipeline_mock._next_event_time = 0.0
    pipeline_event = pipeline_mock.poll_event()

    assert WAKE_ANGLE_FRAME_ID == "microphone_array"
    assert value["header"]["frame_id"] == WAKE_ANGLE_FRAME_ID
    assert direct_mock["header"]["frame_id"] == WAKE_ANGLE_FRAME_ID
    assert pipeline_event is not None
    assert pipeline_event["header"]["frame_id"] == WAKE_ANGLE_FRAME_ID


def test_intent_protocol_and_event_mapping() -> None:
    assert NLU_PROTOCOL == "rkllm_social_intent_control_v1"
    assert parse_intent_tag("NONE|SIT|DO") == ("NONE", "SIT", "DO")
    assert (
        classification_to_voice_event("NONE", "SIT", "DO")
        == EVT_VOICE_COMMAND_SIT
    )
    assert parse_intent_tag("NONE|SIT|CANCEL") is None
    assert parse_intent_tag("NONE|DOG_STATUS|DO") is None
    assert parse_intent_tag("NONE|NONE|DO") is None


def test_model_intent_multi_axis_routes_specific_command_before_summary() -> None:
    events = route_classification_events(
        "PRAISE",
        "SIT",
        "DO",
        asr_text="真乖坐下",
        source="rkllm",
    )

    assert [event["event_type"] for event in events] == [
        "EVT_VOICE_PRAISE",
        EVT_VOICE_COMMAND_SIT,
        EVT_VOICE_COMMAND_KNOWN,
    ]
    assert all(event["nlu_protocol"] == NLU_PROTOCOL for event in events)
    assert all(event["raw_nlu_tag"] == "PRAISE|SIT|DO" for event in events)
    praise, specific, summary = events
    assert not praise["should_trigger_behavior_tree"]
    assert specific["dispatch_role"] == "specific_command"
    assert specific["specific_event_type"] == EVT_VOICE_COMMAND_SIT
    assert specific["action"] == "SIT"
    assert specific["command_id"] == "CMD_SIT"
    assert specific["is_executable"]
    assert specific["should_trigger_behavior_tree"]
    assert summary["dispatch_role"] == "semantic_classification"
    assert summary["specific_event_type"] == EVT_VOICE_COMMAND_SIT
    assert not summary["should_trigger_behavior_tree"]


@pytest.mark.parametrize(
    ("intent", "control", "command_key", "event_type"),
    [
        ("GO", "DO", "WALK", "EVT_VOICE_COMMAND_WALK"),
        ("COME", "DO", "COME", "EVT_VOICE_COMMAND_COME"),
        ("FOLLOW", "DO", "FOLLOW", "EVT_VOICE_COMMAND_FOLLOW"),
        ("GO_OUT", "DO", "GO_OUT", "EVT_VOICE_COMMAND_GO_OUT"),
        ("GO_HOME", "DO", "GO_HOME", "EVT_VOICE_COMMAND_GO_HOME"),
        ("APPROACH", "DO", "APPROACH", "EVT_VOICE_COMMAND_APPROACH"),
        ("BACK", "DO", "BACK_UP", "EVT_VOICE_COMMAND_BACK_UP"),
        ("SIT", "DO", "SIT", "EVT_VOICE_COMMAND_SIT"),
        ("LIE", "DO", "LIE_DOWN", "EVT_VOICE_COMMAND_LIE_DOWN"),
        (
            "PLAY_DEAD",
            "DO",
            "PLAY_DEAD",
            "EVT_VOICE_COMMAND_PLAY_DEAD",
        ),
        ("STAND", "DO", "STAND_UP", "EVT_VOICE_COMMAND_STAND_UP"),
        ("SHAKE", "DO", "SHAKE_HAND", "EVT_VOICE_COMMAND_SHAKE_HAND"),
        ("HIGH_FIVE", "DO", "HIGH_FIVE", "EVT_VOICE_COMMAND_HIGH_FIVE"),
        ("SPIN", "DO", "SPIN", "EVT_VOICE_COMMAND_SPIN"),
        ("ROLL", "DO", "ROLL_OVER", "EVT_VOICE_COMMAND_ROLL_OVER"),
        ("DROP", "DO", "DROP", "EVT_VOICE_COMMAND_DROP"),
        ("BARK", "STOP", "QUIET", "EVT_VOICE_COMMAND_QUIET"),
        ("TOILET", "DO", "TOILET", "EVT_VOICE_COMMAND_TOILET"),
        ("CLEAN", "DO", "CLEAN", "EVT_VOICE_COMMAND_CLEAN"),
        ("SLEEP", "DO", "SLEEP", "EVT_VOICE_COMMAND_SLEEP"),
    ],
)
def test_model_intent_explicit_command_allowlist(
    intent: str,
    control: str,
    command_key: str,
    event_type: str,
) -> None:
    events = route_classification_events(
        "NONE",
        intent,
        control,
        asr_text="model route",
        source="rkllm",
    )

    assert [event["event_type"] for event in events] == [
        event_type,
        EVT_VOICE_COMMAND_KNOWN,
    ]
    assert events[0]["action"] == command_key
    assert events[0]["should_trigger_behavior_tree"]
    assert not events[1]["should_trigger_behavior_tree"]


@pytest.mark.parametrize(
    ("intent", "control"),
    [
        ("STAY", "DO"),
        ("EAT", "DO"),
        ("FETCH", "DO"),
        ("FIND_PERSON", "DO"),
    ],
)
def test_model_intent_ambiguous_commands_remain_summary_only(
    intent: str,
    control: str,
) -> None:
    events = route_classification_events(
        "NONE",
        intent,
        control,
        asr_text="ambiguous model route",
        source="rkllm",
    )

    assert [event["event_type"] for event in events] == [
        EVT_VOICE_COMMAND_KNOWN
    ]
    assert not events[0]["should_trigger_behavior_tree"]


@pytest.mark.parametrize(
    ("asr_text", "command_key", "event_type"),
    [
        (
            "保持站立姿势",
            "STAND_STILL",
            "EVT_VOICE_COMMAND_STAND_STILL",
        ),
        (
            "保持原地不要走",
            "HOLD_POSITION",
            "EVT_VOICE_COMMAND_HOLD_POSITION",
        ),
        (
            "Please remain standing",
            "STAND_STILL",
            "EVT_VOICE_COMMAND_STAND_STILL",
        ),
        (
            "Hold still and don't move",
            "HOLD_POSITION",
            "EVT_VOICE_COMMAND_HOLD_POSITION",
        ),
    ],
)
def test_model_intent_stay_uses_asr_text_to_resolve_specific_command(
    asr_text: str,
    command_key: str,
    event_type: str,
) -> None:
    events = route_classification_events(
        "NONE",
        "STAY",
        "DO",
        asr_text=asr_text,
        source="rkllm",
    )

    assert [event["event_type"] for event in events] == [
        event_type,
        EVT_VOICE_COMMAND_KNOWN,
    ]
    assert events[0]["action"] == command_key
    assert events[0]["should_trigger_behavior_tree"]
    assert not events[1]["should_trigger_behavior_tree"]


def test_model_intent_find_query_requires_supported_object_for_specific_event() -> None:
    matched_slots = [
        {"key": "object_name", "value": "dog toy ball"},
        {"key": "object_mention", "value": "球"},
        {"key": "object_match_source", "value": "asr_rule"},
    ]
    matched = route_classification_events(
        "NONE",
        "FIND_TOY",
        "QUERY",
        asr_text="看看那个球在哪里",
        source="rkllm",
        extra_slots=matched_slots,
    )
    unsupported = route_classification_events(
        "NONE",
        "FIND_TOY",
        "QUERY",
        asr_text="看看那个布偶娃娃在哪里",
        source="rkllm",
        extra_slots=[
            {"key": "object_name", "value": "NONE"},
            {"key": "object_mention", "value": "布偶娃娃"},
            {"key": "object_match_source", "value": "unsupported"},
        ],
    )

    assert [event["event_type"] for event in matched] == [
        EVT_VOICE_COMMAND_FETCH,
        EVT_VOICE_STATUS_CARE,
    ]
    assert matched[0]["should_trigger_behavior_tree"]
    assert matched[0]["action"] == "FETCH"
    assert matched[0]["command_id"] == "CMD_FETCH_OBJECT"
    assert not matched[1]["should_trigger_behavior_tree"]
    assert [event["event_type"] for event in unsupported] == [
        EVT_VOICE_STATUS_CARE
    ]
    assert not unsupported[0]["should_trigger_behavior_tree"]
    assert {slot["key"]: slot["value"] for slot in unsupported[0]["slots"]}[
        "object_name"
    ] == "NONE"


def test_model_intent_fetch_do_with_supported_object_routes_specific_then_known() -> None:
    events = route_classification_events(
        "NONE",
        "FETCH",
        "DO",
        asr_text="把球捡回来",
        source="rkllm",
        extra_slots=[
            {"key": "object_name", "value": "dog toy ball"},
            {"key": "object_mention", "value": "球"},
            {"key": "object_match_source", "value": "asr_rule"},
        ],
    )

    assert [event["event_type"] for event in events] == [
        EVT_VOICE_COMMAND_FETCH,
        EVT_VOICE_COMMAND_KNOWN,
    ]
    assert events[0]["should_trigger_behavior_tree"]
    assert not events[1]["should_trigger_behavior_tree"]


def test_model_intent_play_route_is_deduplicated_and_none_routes_neutral() -> None:
    playful = route_classification_events(
        "PLAYFUL",
        "PLAY",
        "DO",
        asr_text="来玩呀",
        source="rkllm",
    )
    oos = route_classification_events(
        "NONE",
        "NONE",
        "NONE",
        asr_text="读一下消息",
        source="rkllm",
    )

    assert [event["event_type"] for event in playful] == [
        "EVT_VOICE_PLAY_INTERACTION"
    ]
    assert [event["event_type"] for event in oos] == [EVT_VOICE_NEUTRAL]
    assert oos[0]["intent_category"] == "neutral"
    assert not oos[0]["should_trigger_behavior_tree"]


def test_direct_mock_only_emits_voice_events() -> None:
    provider = MockEventProvider({"enabled": True, "event_interval_sec": 1})
    event = provider.build_event(EVT_VOICE_COMMAND_SIT)
    assert event["event_type"] == EVT_VOICE_COMMAND_SIT
    assert event["action"] == "SIT"


@pytest.mark.parametrize(
    ("speaker_id", "event_type"),
    [
        ("owner", EVT_VOICE_MASTER_ID),
        ("family_member_1", EVT_VOICE_FOLK_ID),
        ("family_member_4", EVT_VOICE_FOLK_ID),
        ("unknown", EVT_VOICE_UNMASTER_ID),
        ("legacy_name", EVT_VOICE_UNMASTER_ID),
        ("", EVT_VOICE_UNMASTER_ID),
    ],
)
def test_speaker_identity_routes_to_distinct_events(
    speaker_id: str,
    event_type: str,
) -> None:
    assert speaker_to_voice_event(speaker_id) == event_type


def test_voice_source_does_not_import_vision_project() -> None:
    root = Path(__file__).parents[1] / "marsdog_voice_interaction"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
    )
    assert "marsdog_vision_interaction" not in source
    assert "marsdog_perception." not in source


def test_kws_candidates_are_unique_and_scoped_to_one_utterance() -> None:
    tracker = UtteranceCommandTracker()
    tracker.begin("utterance-1")

    sit = {"event_type": EVT_VOICE_COMMAND_SIT, "action": "SIT"}
    stand = {
        "event_type": "EVT_VOICE_COMMAND_STAND_UP",
        "action": "STAND_UP",
    }
    assert tracker.record_kws_candidate(sit)
    assert not tracker.record_kws_candidate(sit)
    assert tracker.kws_candidate_count == 1
    assert tracker.single_kws_candidate() == sit
    assert tracker.record_kws_candidate(stand)
    assert tracker.kws_candidate_count == 2
    assert tracker.single_kws_candidate() is None

    tracker.finish()
    assert tracker.kws_candidate_count == 0
    assert tracker.kws_candidates == ()
    assert tracker.single_kws_candidate() is None


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("站起来", "STAND"),
        ("等一下", "STAY"),
        ("COMEHERE", "COME"),
        ("shakehands", "SHAKE"),
        ("HIGHFIVE", "HIGH_FIVE"),
        ("followme", "FOLLOW"),
        ("playdead", "PLAY_DEAD"),
    ],
)
def test_rule_intent_covers_kws_commands_in_both_languages(
    text: str,
    intent: str,
) -> None:
    provider = RuleIntentProvider({})
    provider.start()
    event = provider.parse_intent(text)
    assert event is not None
    assert event["intent"] == intent
    assert event["action"] == intent
    assert event["control"] == "DO"


def test_sense_voice_language_tag_is_normalized() -> None:
    assert _normalize_sense_voice_language("<|zh|>", "auto") == "zh"
    assert _normalize_sense_voice_language("<|en|>", "auto") == "en"
    assert _normalize_sense_voice_language("", "auto") == "auto"


def test_documented_qa_command_inventory_matches_current_code() -> None:
    keyword_file = Path(__file__).parents[1] / "config" / "kws_keywords_raw.txt"
    keyword_lines = [
        line.strip()
        for line in keyword_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    keyword_actions = {
        line.rsplit("@", 1)[-1].strip()
        for line in keyword_lines
    }

    assert len(ACTION_TO_VOICE_EVENT) == 16
    assert len(_RULES) == 30
    assert len(keyword_lines) == 26
    assert len(keyword_actions) == 12
    assert {"BRING", "FETCH", "STOP"}.isdisjoint(keyword_actions)
