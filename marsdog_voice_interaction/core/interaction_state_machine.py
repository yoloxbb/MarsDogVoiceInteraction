"""Voice-session state machine with no visual target dependency."""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any


class State(Enum):
    IDLE = "idle"
    ATTENTION = "attention"
    INTERACTION = "interaction"
    EXECUTION = "execution"


class Trigger:
    WAKEUP = "wakeup"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    INTENT_PARSED = "intent_parsed"
    COMMAND_DONE = "command_done"
    TIMEOUT = "timeout"


_TRANSITIONS = {
    State.IDLE: {
        Trigger.WAKEUP: State.ATTENTION,
    },
    State.ATTENTION: {
        Trigger.SPEECH_START: State.INTERACTION,
        Trigger.TIMEOUT: State.IDLE,
    },
    State.INTERACTION: {
        Trigger.INTENT_PARSED: State.EXECUTION,
        Trigger.SPEECH_END: State.ATTENTION,
        Trigger.TIMEOUT: State.IDLE,
    },
    State.EXECUTION: {
        Trigger.SPEECH_START: State.INTERACTION,
        Trigger.COMMAND_DONE: State.ATTENTION,
        Trigger.TIMEOUT: State.IDLE,
    },
}


class VoiceInteractionStateMachine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = State.IDLE
        self._previous_state = State.IDLE
        self._state_entered_at = time.time()

    @property
    def state(self) -> State:
        return self._state

    @property
    def previous_state(self) -> State:
        return self._previous_state

    def state_dict(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "previous_state": self._previous_state.value,
            "elapsed_sec": round(time.time() - self._state_entered_at, 2),
        }

    def trigger(self, event: str) -> State:
        with self._lock:
            next_state = _TRANSITIONS.get(self._state, {}).get(event)
            if next_state is not None:
                self._previous_state = self._state
                self._state = next_state
                self._state_entered_at = time.time()
            return self._state

    def force_state(self, state: State) -> None:
        with self._lock:
            self._previous_state = self._state
            self._state = state
            self._state_entered_at = time.time()


# Compatibility alias for code migrated from the combined project.
PerceptionStateMachine = VoiceInteractionStateMachine
