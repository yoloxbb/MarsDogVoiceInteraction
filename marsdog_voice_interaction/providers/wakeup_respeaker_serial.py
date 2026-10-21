"""Translate Flex CDC wakes and selected firmware KWS notifications."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from marsdog_voice_interaction.adapters.wakeup.respeaker_serial_reader import ReSpeakerSerialReader
from marsdog_voice_interaction.messages.audio_event import WAKE_ANGLE_FRAME_ID
from marsdog_voice_interaction.messages.voice_event_types import (
    CALL_HERE_KEYWORDS,
    EVT_VOICE_CALL,
)
from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Expected finite number")
    return result


class WakeupReSpeakerSerialProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.reader = ReSpeakerSerialReader(config)
        self.max_angle_age_ms = _number(config.get("max_angle_age_ms", 600.0))
        if self.max_angle_age_ms <= 0:
            raise ValueError("max_angle_age_ms must be positive")

    def start(self) -> None:
        self.reader.open()

    def stop(self) -> None:
        self.reader.close()
        self.available = False

    def is_available(self) -> bool:
        return self.reader.ready

    def poll_event(self) -> dict[str, Any] | None:
        self.available = self.reader.ready
        # Separate bounded queues keep unrelated keyword traffic behind wakes.
        for poll in (self.reader.get_message, self.reader.get_keyword_message):
            for _ in range(32):
                raw = poll()
                if raw is None:
                    break
                event = self._parse_event(raw)
                if event is not None:
                    return event
        return None

    def _parse_event(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        kind = raw.get("type")
        if kind not in ("wake", "keyword"):
            return None
        try:
            word = raw["keyword"]
            if not isinstance(word, str) or not word.strip():
                return None
            # Generic device keywords must never fall through to wake lifecycle.
            if kind == "keyword" and word.strip() not in CALL_HERE_KEYWORDS:
                return None
            keyword_id = raw["id"]
            if type(keyword_id) is not int or not 1 <= keyword_id <= 32767:
                return None
            score = _number(raw["score"])
            threshold = _number(raw["threshold"])
            min_score = _number(raw["min_score"])
            if not all(0 <= value <= 1 for value in (score, threshold, min_score)):
                return None
            received = _number(raw["_received_monotonic"])
        except (KeyError, ValueError, TypeError, OverflowError):
            logger.warning("Discard malformed Flex recognition: %r", raw)
            return None
        # Direction freshness gates motion, not the wake/session itself.
        # Keep invalid direction metadata so downstream can skip orientation.
        angle, doa_age, audio_age = -1.0, -1.0, -1.0
        valid = False
        snapshot_valid = False
        queue_ms = max(0.0, (time.monotonic() - received) * 1000.0)
        try:
            angle = _number(raw["doa"])
            doa_age = _number(raw["doa_age_ms"])
            audio_age = _number(raw["audio_age_ms"])
            snapshot_valid = (raw.get("doa_valid") is True and 0 <= angle < 360
                              and doa_age >= 0 and audio_age >= 0)
            valid = (snapshot_valid
                     and doa_age + audio_age + queue_ms <= self.max_angle_age_ms)
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
        event = {
            "header": {"frame_id": WAKE_ANGLE_FRAME_ID},
            "event_type": EVT_VOICE_CALL if word.strip() in CALL_HERE_KEYWORDS else "wakeup",
            "call_kind": "here" if word.strip() in CALL_HERE_KEYWORDS else "",
            "wake_provider": "respeaker_keyword" if kind == "keyword" else "respeaker_serial",
            "wake_keyword_id": keyword_id,
            "wake_word": word,
            "wake_confidence": score,
            "wake_score_raw": score,
            "wake_threshold": threshold,
            "wake_min_score": min_score,
            "wake_angle": angle if valid else -1.0,
            "wake_angle_valid": valid,
            "wake_angle_age_ms": (doa_age + audio_age + queue_ms
                                  if doa_age >= 0 and audio_age >= 0 else -1.0),
            "wake_audio_age_ms": audio_age,
            "wake_angle_source": str(raw.get("doa_source", "none")),
            "received_monotonic": received,
            "latency_ms": queue_ms,
            "_wake_angle_stale": snapshot_valid and not valid,
        }
        if doa_age >= 0 and audio_age >= 0:
            event["_wake_angle_measured_monotonic"] = received - (doa_age + audio_age) / 1000.0
            event["_wake_angle_max_age_ms"] = self.max_angle_age_ms
        kind = "call-here" if event["event_type"] == EVT_VOICE_CALL else "wakeup"
        logger.info("Flex %s: word=%r score=%.4f angle=%.1f valid=%s age_ms=%.1f",
                    kind, word, score, event["wake_angle"], valid, event["wake_angle_age_ms"])
        return event
