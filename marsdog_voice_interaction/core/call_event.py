"""Session call normalization, acoustic direction snapshots and deduplication."""
from __future__ import annotations

from collections import deque
import json
import logging
import math
from typing import Any

from marsdog_voice_interaction.messages.voice_event_types import (
    CALL_EVENT_TYPES, CALL_HERE_KEYWORDS, EVT_VOICE_CALL,
)

logger = logging.getLogger(__name__)


def _missing_direction(value: dict[str, Any], since: Any, until: Any, reason: str) -> dict[str, Any]:
    logger.info("CALL_DIRECTION %s", json.dumps({
        "record": "call_direction", "result": reason,
        "window_start": since, "window_end": until,
    }, ensure_ascii=False, separators=(",", ":"), default=str))
    return value


def is_call(event: dict[str, Any]) -> bool:
    return event.get("event_type") in CALL_EVENT_TYPES


def normalize_call(event: dict[str, Any]) -> dict[str, Any]:
    value = dict(event)
    kind = value.get("call_kind")
    if kind not in ("here", "name"):
        kind = ("here" if value.get("event_type") == "EVT_VOICE_CALL_HERE"
                or str(value.get("wake_word") or value.get("asr_text", "")).strip()
                in CALL_HERE_KEYWORDS else "name")
    value.update(
        event_type=EVT_VOICE_CALL, specific_event_type=EVT_VOICE_CALL,
        call_kind=kind, social="CALL", intent="NONE", action="NONE", control="NONE",
        intent_category="call", dispatch_role="call", is_executable=False,
        should_trigger_behavior_tree=False, wake_id="",
        wake_angle=-1.0, wake_angle_valid=False,
        intent_source=(str(value.get("intent_source") or value.get("wake_provider")
                           or {"EVT_VOICE_CALL_HERE": "respeaker_keyword",
                               "EVT_VOICE_COMMAND_CALL_NAME": "command_lexicon"}.get(
                                   value.get("event_type"), "model_intent"))),
    )
    # Old recognition snapshot timing is not the live VAD bearing attached to a call.
    for key in ("_wake_angle_measured_monotonic", "_wake_angle_max_age_ms"):
        value.pop(key, None)
    return value


def direction_snapshot(wakeup: Any, since: Any, until: Any) -> dict[str, Any]:
    """Freeze even a missing bearing, so later speakers can never fill it in."""
    value: dict[str, Any] = {
        "doa": -1.0, "doa_valid": False, "doa_age_ms": -1.0, "doa_source": "none",
        "_call_direction_bound": True,
    }
    if (type(since) not in (int, float) or type(until) not in (int, float)
            or not math.isfinite(since) or not math.isfinite(until) or since > until):
        return _missing_direction(value, since, until, "invalid_window")
    value.update(_call_window_start=since, _call_window_end=until)
    reader = getattr(wakeup, "reader", None)
    query = getattr(reader, "recent_call_doa", None)
    if not callable(query):
        return _missing_direction(value, since, until, "reader_unavailable")
    if not reader.ready:
        return _missing_direction(value, since, until, "not_ready")
    generation = reader.connection_id
    doa = query(since, until)
    if doa is None:
        return value
    if not reader.ready or reader.connection_id != generation:
        return _missing_direction(value, since, until, "connection_changed")
    value.update(doa=doa[1], doa_valid=True, doa_source=doa[2],
                 doa_age_ms=max(0.0, until - doa[0]) * 1000.0,
                 _doa_measured_monotonic=doa[0],
                 _doa_max_age_ms=min(600.0, float(getattr(wakeup, "max_angle_age_ms", 600.0))),
                 _call_connection_id=generation)
    return value


def bind_utterance_direction(audio: dict[str, Any], wakeup: Any) -> dict[str, Any]:
    if "_call_direction" in audio:
        return audio
    return {**audio, "_call_direction": direction_snapshot(
        wakeup, audio.get("speech_started_monotonic"), audio.get("speech_ended_monotonic"),
    )}


def invalidate_disconnected_direction(event: dict[str, Any], wakeup: Any) -> dict[str, Any]:
    reader = getattr(wakeup, "reader", None)
    if "_call_connection_id" in event and (
            reader is None or not reader.ready
            or event["_call_connection_id"] != reader.connection_id):
        return {**event, "doa": -1.0, "doa_valid": False}
    return event


class CallDeduplicator:
    """Bound memory; compare acoustic times rather than delayed ASR arrival times."""
    def __init__(self, debounce_sec: float = 0.8) -> None:
        self.debounce_sec = debounce_sec
        self._events: deque[dict[str, Any]] = deque(maxlen=128)

    def accept(self, event: dict[str, Any]) -> bool:
        for previous in self._events:
            if event.get("interaction_id") != previous.get("interaction_id"):
                continue
            if (event.get("utterance_id") and event.get("utterance_id") in (
                    previous.get("utterance_id"), previous.get("_call_matched_utterance_id"))):
                return False
            if event.get("call_kind") != previous.get("call_kind"):
                continue
            # Different ASR utterances remain distinct. Cross-source firmware
            # hits may share a speech interval even when ASR returns much later.
            current_anchor, previous_anchor = event.get("_call_anchor"), previous.get("_call_anchor")
            if current_anchor is not None and previous_anchor is not None:
                if abs(current_anchor - previous_anchor) < self.debounce_sec:
                    return False
            elif event.get("intent_source") != previous.get("intent_source"):
                if previous.get("_call_matched_utterance_id"):
                    # One uncertain firmware timestamp may overlap two adjacent
                    # intervals. It can be paired with at most one ASR utterance.
                    continue
                anchor, speech = ((current_anchor, previous) if current_anchor is not None
                                  else (previous_anchor, event))
                if (anchor is not None and "_call_window_start" in speech
                        and speech["_call_window_start"] - 0.1 <= anchor <= speech["_call_window_end"] + 0.1):
                    previous["_call_matched_utterance_id"] = speech.get("utterance_id", "")
                    if current_anchor is not None:
                        previous["_call_anchor"] = current_anchor
                    return False
        self._events.append(dict(event))
        return True
