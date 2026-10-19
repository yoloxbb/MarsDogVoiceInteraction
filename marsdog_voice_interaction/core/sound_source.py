"""Publish sound-source events directly from ReSpeaker CDC JSON updates."""

from __future__ import annotations

import math
import time
from typing import Any, Callable
import uuid

from marsdog_voice_interaction.messages.audio_event import WAKE_ANGLE_FRAME_ID
from marsdog_voice_interaction.messages.voice_event_types import EVT_VOICE_SOUND_SOURCE_DETECTED


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


class SoundSourceEventRouter:
    """Each valid vad/update maps to an event, independently of wake or VAD boundaries."""

    def __init__(
        self, reader: Any, config: dict[str, Any], *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.reader = reader
        self._clock, self._wall_clock = clock, wall_clock
        self._max_doa_age_ms = float(config.get("max_doa_age_ms", 600.0))
        if not math.isfinite(self._max_doa_age_ms) or self._max_doa_age_ms <= 0:
            raise ValueError("sound_source.max_doa_age_ms must be finite and positive")
        self._stopped = False

    def _event(self, raw: dict[str, Any], now: float) -> dict[str, Any] | None:
        if (raw.get("type") != "vad" or raw.get("state") != "update"
                or raw.get("doa_valid") is not True):
            return None
        angle, age = _number(raw.get("doa")), _number(raw.get("doa_age_ms"))
        received = _number(raw.get("_received_monotonic"))
        if (angle is None or not 0 <= angle < 360 or age is None or age < 0
                or received is None or received > now + 0.2):
            return None
        queue_ms = max(0.0, now - received) * 1000.0
        if age + queue_ms > self._max_doa_age_ms:
            return None
        stamp = self._wall_clock()
        slots = [{"key": "trigger", "value": "vad/update"}]
        slots.extend({"key": key, "value": str(raw[key])} for key in (
            "source", "raw_rms", "rms", "noise_floor", "speech", "xmos_speech",
        ) if key in raw)
        return {
            "header": {"stamp": stamp, "frame_id": WAKE_ANGLE_FRAME_ID},
            "event_type": EVT_VOICE_SOUND_SOURCE_DETECTED,
            "interaction_id": "", "wake_id": "", "utterance_id": "",
            "sound_id": uuid.uuid4().hex,
            "sound_trigger_stamp": stamp - queue_ms / 1000.0,
            "doa": angle, "doa_valid": True, "doa_age_ms": age + queue_ms,
            "doa_source": str(raw.get("doa_source", "none")),
            "intent_source": "respeaker_vad", "intent_category": "sound_source",
            "dispatch_role": "sound_source", "action": "NONE",
            "is_executable": False, "should_trigger_behavior_tree": True,
            "latency_ms": queue_ms, "slots": slots,
            "_doa_measured_monotonic": received - age / 1000.0,
            "_doa_max_age_ms": self._max_doa_age_ms,
        }

    def poll(self) -> list[dict[str, Any]]:
        if self._stopped or not self.reader.ready:
            return []
        connection_id = self.reader.connection_id
        events = []
        for _ in range(128):
            raw = self.reader.get_vad_message()
            if raw is None:
                break
            if raw.get("_connection_id") != connection_id:
                continue
            event = self._event(raw, self._clock())
            if event is not None:
                events.append(event)
        return events if self.reader.ready and self.reader.connection_id == connection_id else []

    def stop(self) -> None:
        self._stopped = True
