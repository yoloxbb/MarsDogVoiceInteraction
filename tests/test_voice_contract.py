from pathlib import Path

import pytest

from marsdog_voice_interaction.messages.audio_event import (
    WAKE_ANGLE_FRAME_ID,
    normalize_audio_event,
)
from marsdog_voice_interaction.messages.intent_protocol import (
    parse_intent_tag,
)
from marsdog_voice_interaction.messages.voice_event_types import (
    EVT_VOICE_CALL_NAME,
    EVT_VOICE_COMMAND_SIT,
    classification_to_voice_event,
)
from marsdog_voice_interaction.core.utterance_command_tracker import (
    UtteranceCommandTracker,
)
from marsdog_voice_interaction.providers.mock_event import MockEventProvider
from marsdog_voice_interaction.providers.mock_wakeup import MockWakeupProvider
from marsdog_voice_interaction.providers.asr_sherpa import (
    _normalize_sense_voice_language,
)
from marsdog_voice_interaction.providers.rule_intent import RuleIntentProvider


def test_audio_contract_has_no_visual_binding() -> None:
    value = normalize_audio_event({
        "event_type": "speech",
        "utterance_id": "u1",
        "asr_text": "坐下",
    })
    assert value["schema_version"] == 1
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
    assert parse_intent_tag("NONE|SIT|DO") == ("NONE", "SIT", "DO")
    assert (
        classification_to_voice_event("NONE", "SIT", "DO")
        == EVT_VOICE_COMMAND_SIT
    )
    with pytest.raises(ValueError):
        parse_intent_tag(" none|SIT|DO")


def test_direct_mock_only_emits_voice_events() -> None:
    provider = MockEventProvider({"enabled": True, "event_interval_sec": 1})
    event = provider.build_event(EVT_VOICE_COMMAND_SIT)
    assert event["event_type"] == EVT_VOICE_COMMAND_SIT
    assert event["action"] == "SIT"


def test_voice_source_does_not_import_vision_project() -> None:
    root = Path(__file__).parents[1] / "marsdog_voice_interaction"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
    )
    assert "marsdog_vision_interaction" not in source
    assert "marsdog_perception." not in source


def test_kws_final_intent_is_deduplicated_by_event_type() -> None:
    tracker = UtteranceCommandTracker()
    tracker.begin("utterance-1")

    assert tracker.record_immediate(EVT_VOICE_COMMAND_SIT)
    assert not tracker.record_immediate(EVT_VOICE_COMMAND_SIT)
    assert tracker.is_duplicate_final(EVT_VOICE_COMMAND_SIT)
    assert not tracker.is_duplicate_final("EVT_VOICE_COMMAND_STAND_UP")

    tracker.finish()
    assert not tracker.is_duplicate_final(EVT_VOICE_COMMAND_SIT)


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("站起来", "STAND_UP"),
        ("等一下", "WAIT"),
        ("COMEHERE", "COME"),
        ("shakehands", "SHAKE_HAND"),
        ("HIGHFIVE", "HIGH_FIVE"),
        ("followme", "FOLLOW"),
        ("playdead", "PLAY_DEAD"),
    ],
)
def test_rule_intent_covers_kws_commands_in_both_languages(
    text: str,
    action: str,
) -> None:
    provider = RuleIntentProvider({})
    provider.start()
    event = provider.parse_intent(text)
    assert event is not None
    assert event["action"] == action
    assert event["control"] == "DO"


def test_sense_voice_language_tag_is_normalized() -> None:
    assert _normalize_sense_voice_language("<|zh|>", "auto") == "zh"
    assert _normalize_sense_voice_language("<|en|>", "auto") == "en"
    assert _normalize_sense_voice_language("", "auto") == "auto"
