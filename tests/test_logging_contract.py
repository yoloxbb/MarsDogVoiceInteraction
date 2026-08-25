from __future__ import annotations

import json
import logging
import threading

from marsdog_voice_interaction.core.interaction_state_machine import (
    Trigger,
    VoiceInteractionStateMachine,
)
from marsdog_voice_interaction.nodes.voice_interaction_node import (
    VoiceInteractionNode,
)
from marsdog_voice_interaction.utils.logging_utils import (
    get_logger,
    log_trace,
)


def _trace_payload(message: str) -> dict[str, object]:
    prefix = "VOICE_TRACE "
    assert message.startswith(prefix)
    return json.loads(message[len(prefix):])


def test_trace_record_is_one_line_json_with_stable_record_name(
    caplog: object,
) -> None:
    logger = get_logger("test_trace_record", module="voice")
    with caplog.at_level(logging.INFO):  # type: ignore[attr-defined]
        log_trace(
            logger,
            "stage_complete",
            stage="asr",
            result="ok",
            latency_ms=12.34,
            interaction_id="session-1",
            utterance_id="utterance-1",
        )

    records = caplog.records  # type: ignore[attr-defined]
    payload = _trace_payload(records[-1].getMessage())
    assert payload == {
        "record": "stage_complete",
        "stage": "asr",
        "result": "ok",
        "latency_ms": 12.34,
        "interaction_id": "session-1",
        "utterance_id": "utterance-1",
    }


def test_structured_logger_preserves_exception_logging_kwargs(
    caplog: object,
) -> None:
    logger = get_logger("test_exception_trace", module="voice")
    with caplog.at_level(logging.ERROR):  # type: ignore[attr-defined]
        try:
            raise RuntimeError("provider failed")
        except RuntimeError:
            logger.error("stage failed", stage="asr", exc_info=True)

    record = caplog.records[-1]  # type: ignore[attr-defined]
    assert record.exc_info is not None
    assert "stage='asr'" in record.getMessage()


class _Publisher:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def publish(self, message: object) -> None:
        self.messages.append(message)


class _PublishHarness:
    _publish = VoiceInteractionNode._publish
    _trace = VoiceInteractionNode._trace

    def __init__(self) -> None:
        self._audio_pub = _Publisher()
        self._interaction_lock = threading.RLock()
        self._interaction_id = "session-1"
        self._state_machine = VoiceInteractionStateMachine()
        self._state_machine.trigger(Trigger.WAKEUP)
        self._event_trace_enabled = True
        self._config = {
            "topics": {"audio_event": "/perception/audio_event"},
        }


def test_every_topic_publish_has_correlated_event_trace(caplog: object) -> None:
    node = _PublishHarness()
    with caplog.at_level(logging.INFO):  # type: ignore[attr-defined]
        node._publish({
            "event_type": "EVT_VOICE_COMMAND_SIT",
            "utterance_id": "utterance-1",
            "asr_text": "坐下",
            "emotion": "NONE",
            "action": "SIT",
            "control": "DO",
            "command_id": "CMD_SIT",
            "intent_source": "rule",
            "should_trigger_behavior_tree": True,
        })

    records = caplog.records  # type: ignore[attr-defined]
    payload = _trace_payload(records[-1].getMessage())
    assert payload["record"] == "event_publish"
    assert payload["event_type"] == "EVT_VOICE_COMMAND_SIT"
    assert payload["interaction_id"] == "session-1"
    assert payload["utterance_id"] == "utterance-1"
    assert payload["asr_text"] == "坐下"
    assert payload["emotion"] == "NONE"
    assert payload["action"] == "SIT"
    assert payload["control"] == "DO"
    assert payload["should_trigger_behavior_tree"] is True
    event_payload = payload["payload"]
    assert isinstance(event_payload, dict)
    assert event_payload["event_type"] == "EVT_VOICE_COMMAND_SIT"
    assert event_payload["slots"] == []
