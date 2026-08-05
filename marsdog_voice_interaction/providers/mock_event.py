"""Direct voice-event mock used without audio hardware or models."""

from __future__ import annotations

import random
import time
from typing import Any

from marsdog_voice_interaction.messages.intent_protocol import (
    classification_to_event,
)
from marsdog_voice_interaction.messages.voice_event_types import (
    ACTION_TO_VOICE_EVENT,
    EVT_VOICE_CALL_NAME,
    EVT_VOICE_HAPPY,
    EVT_VOICE_MASTER_ID,
    EVT_VOICE_NEUTRAL,
    EVT_VOICE_PRAISE,
    EVT_VOICE_SAD,
    EVT_VOICE_SCOLD,
    EVT_VOICE_STRANGER_ID,
)
from marsdog_voice_interaction.providers.base import BaseProvider


_COMMANDS = (
    ("过来", "COME", "DO"),
    ("握手", "SHAKE_HAND", "DO"),
    ("击掌", "HIGH_FIVE", "DO"),
    ("坐下", "SIT", "DO"),
    ("趴下", "LIE_DOWN", "DO"),
    ("站起来", "STAND_UP", "DO"),
    ("等一下", "WAIT", "DO"),
    ("跟着我", "FOLLOW", "DO"),
    ("翻滚", "ROLL_OVER", "DO"),
    ("转圈", "SPIN", "DO"),
    ("回来", "RETURN", "DO"),
    ("吐掉", "DROP", "DO"),
    ("装死", "PLAY_DEAD", "DO"),
    ("把玩具拿来", "BRING", "DO"),
    ("去找球", "FETCH", "DO"),
    ("停止", "STOP", "CANCEL"),
)

MOCK_AUDIO_EVENT_TYPES = (
    EVT_VOICE_CALL_NAME,
    EVT_VOICE_MASTER_ID,
    EVT_VOICE_STRANGER_ID,
    EVT_VOICE_PRAISE,
    EVT_VOICE_SCOLD,
    *ACTION_TO_VOICE_EVENT.values(),
    EVT_VOICE_HAPPY,
    EVT_VOICE_SAD,
    EVT_VOICE_NEUTRAL,
)


class MockEventProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._interval = float(config.get("event_interval_sec", 5.0))
        self._random = random.Random(config.get("seed"))
        self._last = ""
        self._next = 0.0

    def start(self) -> None:
        self.available = bool(self.config.get("enabled", True))
        self._next = time.monotonic() + self._interval

    def stop(self) -> None:
        self.available = False

    def poll_event(self) -> dict[str, Any] | None:
        now = time.monotonic()
        if not self.available or now < self._next:
            return None
        choices = tuple(
            event for event in MOCK_AUDIO_EVENT_TYPES if event != self._last
        ) or MOCK_AUDIO_EVENT_TYPES
        event_type = self._random.choice(choices)
        self._last = event_type
        self._next = now + self._interval
        return self.build_event(event_type)

    def build_event(self, event_type: str) -> dict[str, Any]:
        if event_type == EVT_VOICE_CALL_NAME:
            return {
                "event_type": event_type,
                "wake_word": "你好小狗",
                "wake_angle": self._random.uniform(-90, 90),
                "wake_confidence": 1.0,
                "state": "attention",
                "previous_state": "idle",
            }
        if event_type == EVT_VOICE_MASTER_ID:
            return {
                "event_type": event_type,
                "speaker_id": "mock_master",
                "speaker_confidence": 0.95,
                "state": "interaction",
            }
        if event_type == EVT_VOICE_STRANGER_ID:
            return {
                "event_type": event_type,
                "speaker_id": "unknown",
                "speaker_confidence": 0.1,
                "state": "interaction",
            }

        if event_type in ACTION_TO_VOICE_EVENT.values():
            text, action, control = next(
                item for item in _COMMANDS
                if ACTION_TO_VOICE_EVENT[item[1]] == event_type
            )
            event = classification_to_event(
                emotion="NONE",
                action=action,
                control=control,
                asr_text=text,
                source="mock_event",
                confidence=1.0,
            )
        elif event_type == EVT_VOICE_PRAISE:
            event = classification_to_event(
                emotion="PRAISE", action="NONE", control="NONE",
                asr_text="你真棒", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_SCOLD:
            event = classification_to_event(
                emotion="REPRIMAND", action="NONE", control="NONE",
                asr_text="你这样做不对", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_HAPPY:
            event = classification_to_event(
                emotion="JOY", action="NONE", control="NONE",
                asr_text="我今天很开心", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_SAD:
            event = classification_to_event(
                emotion="SADNESS", action="NONE", control="NONE",
                asr_text="我有点难过", source="mock_event", confidence=1.0,
            )
        else:
            event = classification_to_event(
                emotion="CALM", action="NONE", control="NONE",
                asr_text="你好", source="mock_event", confidence=1.0,
            )
        event.update({
            "event_type": event_type,
            "speaker_id": "mock_master",
            "speaker_confidence": 0.95,
            "state": (
                "execution"
                if event["should_trigger_behavior_tree"] else "attention"
            ),
            "previous_state": "interaction",
        })
        return event
