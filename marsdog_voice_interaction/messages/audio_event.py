"""Versioned data contract for ``/perception/audio_event``."""

from __future__ import annotations

import copy
import math
from typing import Any

from marsdog_voice_interaction.utils.time_utils import now_stamp


SCHEMA_VERSION = 2
WAKE_ANGLE_FRAME_ID = "microphone_array"

_TEMPLATE: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "header": {"stamp": 0.0, "frame_id": WAKE_ANGLE_FRAME_ID},
    "event_type": "",
    "interaction_id": "",
    "utterance_id": "",
    "wake_id": "",
    "wake_word": "",
    "wake_angle": 0.0,
    "wake_confidence": 0.0,
    "wake_score_raw": 0.0,
    "wake_provider": "",
    "wake_keyword_id": 0,
    "wake_threshold": 0.0,
    "wake_min_score": 0.0,
    "wake_angle_valid": False,
    "wake_angle_age_ms": -1.0,
    "wake_audio_age_ms": -1.0,
    "wake_angle_source": "",
    "asr_text": "",
    "speaker_id": "",
    "speaker_confidence": 0.0,
    "speaker_role": "",
    "speaker_status": "",
    "speaker_reason": "",
    "sound_id": "",
    "sound_trigger_stamp": 0.0,
    "doa": -1.0,
    "doa_valid": False,
    "doa_age_ms": -1.0,
    "doa_source": "",
    "social": "",
    "intent": "",
    "emotion": "",
    "action": "",
    "control": "",
    "language": "zh",
    "command_id": "",
    "intent_category": "",
    "intent_source": "",
    "intent_confidence": 0.0,
    "nlu_protocol": "",
    "raw_nlu_tag": "",
    "specific_event_type": "",
    "dispatch_role": "",
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
    if "wake_angle" in values and "wake_angle_valid" not in values:
        event.pop("wake_angle_valid")
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
        except (TypeError, ValueError, OverflowError):
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
        except (TypeError, ValueError, OverflowError):
            pass
    if not math.isfinite(event["wake_angle"]):
        event["wake_angle"] = 0.0
    # Legacy providers lack validity metadata; preserve their finite supplied
    # bearing. An explicit flag is strict: strings such as "false" are invalid.
    try:
        raw_angle = data["wake_angle"]
        finite_angle = (not isinstance(raw_angle, bool)
                        and math.isfinite(float(raw_angle))
                        and float(raw_angle) != -1.0)
    except (KeyError, TypeError, ValueError, OverflowError):
        finite_angle = False
    event["wake_angle_valid"] = finite_angle and (
        data.get("wake_angle_valid") is True
        if "wake_angle_valid" in data else True
    )
    for key in ("wake_angle_age_ms", "wake_audio_age_ms"):
        if not math.isfinite(event[key]) or event[key] < 0:
            event[key] = -1.0
    for key in ("wake_threshold", "wake_min_score"):
        if not math.isfinite(event[key]):
            event[key] = 0.0
    if not math.isfinite(event["wake_score_raw"]):
        event["wake_score_raw"] = 0.0
    if not math.isfinite(event["wake_confidence"]):
        event["wake_confidence"] = 0.0
    event["wake_confidence"] = min(
        1.0,
        max(0.0, event["wake_confidence"]),
    )
    # Sound bearings require an explicit JSON true and a finite 0..359 angle.
    raw_doa, raw_age = data.get("doa"), data.get("doa_age_ms")
    event["doa_valid"] = (
        data.get("doa_valid") is True
        and isinstance(raw_doa, (int, float)) and not isinstance(raw_doa, bool)
        and math.isfinite(event["doa"]) and 0 <= event["doa"] < 360
        and isinstance(raw_age, (int, float)) and not isinstance(raw_age, bool)
        and math.isfinite(event["doa_age_ms"]) and event["doa_age_ms"] >= 0
    )
    if not event["doa_valid"]:
        event["doa"] = -1.0
        event["doa_age_ms"] = -1.0
    if not math.isfinite(event["sound_trigger_stamp"]) or event["sound_trigger_stamp"] < 0:
        event["sound_trigger_stamp"] = 0.0
    return event
