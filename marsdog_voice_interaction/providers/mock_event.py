"""Direct voice-event mock used without audio hardware or models."""

from __future__ import annotations

import random
import time
from typing import Any

from marsdog_voice_interaction.messages.intent_protocol import (
    COMMAND_KEY_TO_NLU,
    classification_to_event,
)
from marsdog_voice_interaction.messages.audio_event import WAKE_ANGLE_FRAME_ID
from marsdog_voice_interaction.messages.voice_event_types import (
    ACTION_TO_VOICE_EVENT,
    EVT_VOICE_CALL_NAME,
    EVT_VOICE_COMFORT,
    EVT_VOICE_FOLK_ID,
    EVT_VOICE_HAPPY,
    EVT_VOICE_MASTER_ID,
    EVT_VOICE_NEGATIVE_EMOTION,
    EVT_VOICE_NEUTRAL,
    EVT_VOICE_PLAY_INTERACTION,
    EVT_VOICE_POSITIVE_EMOTION,
    EVT_VOICE_PRAISE,
    EVT_VOICE_SAD,
    EVT_VOICE_SCOLD,
    EVT_VOICE_STATUS_CARE,
    EVT_VOICE_UNMASTER_ID,
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
    ("停止", "STOP", "STOP"),
)

MOCK_AUDIO_EVENT_TYPES = (
    EVT_VOICE_CALL_NAME,
    EVT_VOICE_MASTER_ID,
    EVT_VOICE_FOLK_ID,
    EVT_VOICE_UNMASTER_ID,
    EVT_VOICE_PRAISE,
    EVT_VOICE_SCOLD,
    EVT_VOICE_COMFORT,
    EVT_VOICE_PLAY_INTERACTION,
    EVT_VOICE_STATUS_CARE,
    EVT_VOICE_POSITIVE_EMOTION,
    EVT_VOICE_NEGATIVE_EMOTION,
    *ACTION_TO_VOICE_EVENT.values(),
)

_MOCK_INTERACTION_EVENT_TYPES = tuple(
    event_type for event_type in MOCK_AUDIO_EVENT_TYPES
    if event_type != EVT_VOICE_CALL_NAME
)


class MockEventProvider(BaseProvider):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._interval = float(config.get("event_interval_sec", 5.0))
        self._random = random.Random(config.get("seed"))
        self._last = ""
        self._next = 0.0
        self._phase = "call"

    def start(self) -> None:
        self.available = bool(self.config.get("enabled", True))
        self._phase = "call"
        self._next = time.monotonic() + self._interval

    def stop(self) -> None:
        self.available = False

    def poll_event(self) -> dict[str, Any] | None:
        now = time.monotonic()
        if not self.available or now < self._next:
            return None
        if self._phase == "waiting":
            return None
        if self._phase == "call":
            event_type = EVT_VOICE_CALL_NAME
            self._phase = "event"
        else:
            choices = tuple(
                event for event in _MOCK_INTERACTION_EVENT_TYPES
                if event != self._last
            ) or _MOCK_INTERACTION_EVENT_TYPES
            event_type = self._random.choice(choices)
            self._last = event_type
            self._phase = "waiting"
        self._next = now + self._interval
        return self.build_event(event_type)

    def complete_interaction(self) -> None:
        """Allow the next direct-mock session after the node publishes idle."""
        self._phase = "call"
        self._next = time.monotonic() + self._interval

    def build_event(self, event_type: str) -> dict[str, Any]:
        if event_type == EVT_VOICE_CALL_NAME:
            return {
                "header": {"frame_id": WAKE_ANGLE_FRAME_ID},
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
                "speaker_id": "owner",
                "speaker_confidence": 0.95,
                "state": "interaction",
            }
        if event_type == EVT_VOICE_FOLK_ID:
            return {
                "event_type": event_type,
                "speaker_id": "family_member_1",
                "speaker_confidence": 0.95,
                "state": "interaction",
            }
        if event_type == EVT_VOICE_UNMASTER_ID:
            return {
                "event_type": event_type,
                "speaker_id": "unknown",
                "speaker_confidence": 0.1,
                "state": "interaction",
            }

        if event_type in ACTION_TO_VOICE_EVENT.values():
            text, command_key, _legacy_control = next(
                item for item in _COMMANDS
                if ACTION_TO_VOICE_EVENT[item[1]] == event_type
            )
            social, intent, control = COMMAND_KEY_TO_NLU[command_key]
            event = classification_to_event(
                social=social,
                intent=intent,
                control=control,
                asr_text=text,
                source="mock_event",
                confidence=1.0,
                specific_event_type=event_type,
                dispatch_role="specific_command",
                executable=True,
            )
            event["action"] = command_key
        elif event_type == EVT_VOICE_PRAISE:
            event = classification_to_event(
                social="PRAISE", intent="NONE", control="NONE",
                asr_text="你真棒", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_SCOLD:
            event = classification_to_event(
                social="SCOLD", intent="NONE", control="NONE",
                asr_text="你这样做不对", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_COMFORT:
            event = classification_to_event(
                social="COMFORT", intent="NONE", control="NONE",
                asr_text="不怕不怕", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_PLAY_INTERACTION:
            event = classification_to_event(
                social="PLAYFUL", intent="PLAY", control="DO",
                asr_text="来玩呀", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_STATUS_CARE:
            event = classification_to_event(
                social="NONE", intent="DOG_STATUS", control="QUERY",
                asr_text="你在哪里", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_POSITIVE_EMOTION:
            event = classification_to_event(
                social="OWNER_POSITIVE", intent="NONE", control="NONE",
                asr_text="我今天很开心", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_NEGATIVE_EMOTION:
            event = classification_to_event(
                social="OWNER_NEGATIVE", intent="NONE", control="NONE",
                asr_text="我有点难过", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_HAPPY:
            event = classification_to_event(
                social="OWNER_POSITIVE", intent="NONE", control="NONE",
                asr_text="我今天很开心", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_SAD:
            event = classification_to_event(
                social="OWNER_NEGATIVE", intent="NONE", control="NONE",
                asr_text="我有点难过", source="mock_event", confidence=1.0,
            )
        elif event_type == EVT_VOICE_NEUTRAL:
            event = classification_to_event(
                social="NONE", intent="NONE", control="NONE",
                asr_text="你好", source="mock_event", confidence=1.0,
            )
        else:
            raise ValueError(f"Unsupported mock voice event: {event_type}")
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
