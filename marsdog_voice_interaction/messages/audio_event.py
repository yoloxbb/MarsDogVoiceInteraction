"""Versioned data contract for ``/perception/audio_event``."""

from __future__ import annotations

import copy
from typing import Any

from marsdog_voice_interaction.utils.time_utils import now_stamp


SCHEMA_VERSION = 1

_TEMPLATE: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "header": {"stamp": 0.0, "frame_id": "base_link"},
    "event_type": "",
    "interaction_id": "",
    "utterance_id": "",
    "wake_word": "",
    "wake_angle": 0.0,
    "wake_confidence": 0.0,
    "asr_text": "",
    "speaker_id": "",
    "speaker_confidence": 0.0,
    "emotion": "",
    "action": "",
    "control": "",
    "language": "zh",
    "command_id": "",
    "intent_category": "",
    "intent_source": "",
    "intent_confidence": 0.0,
    "slots": [],
    "response_text": "",
    "is_executable": False,
    "should_trigger_behavior_tree": False,
    "danger_type": "",
    "danger_angle": 0.0,
    "state": "",
    "previous_state": "",
    "state_reason": "",
    "latency_ms": 0.0,
}


def make_audio_event(event_type: str, **values: Any) -> dict[str, Any]:
    event = copy.deepcopy(_TEMPLATE)
    event["header"]["stamp"] = now_stamp()
    event["event_type"] = str(event_type)
    event.update({key: value for key, value in values.items() if key in event})
    return normalize_audio_event(event)


def normalize_audio_event(data: Any) -> dict[str, Any]:
    event = copy.deepcopy(_TEMPLATE)
    event["header"]["stamp"] = now_stamp()
    if not isinstance(data, dict):
        return event
    header = data.get("header")
    if isinstance(header, dict):
        try:
            event["header"]["stamp"] = float(
                header.get("stamp", event["header"]["stamp"])
            )
        except (TypeError, ValueError):
            pass
        event["header"]["frame_id"] = str(
            header.get("frame_id", event["header"]["frame_id"])
        )
    for key, default in _TEMPLATE.items():
        if key in ("schema_version", "header") or key not in data:
            continue
        value = data[key]
        if key == "slots":
            if isinstance(value, list):
                event[key] = [
                    {
                        "key": str(item.get("key", "")),
                        "value": str(item.get("value", "")),
                    }
                    for item in value if isinstance(item, dict)
                ]
            continue
        try:
            event[key] = type(default)(value)
        except (TypeError, ValueError):
            pass
    return event
