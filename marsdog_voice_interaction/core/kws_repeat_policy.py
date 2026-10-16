"""Opt-in early publication of repeated keywords in deferred mode."""

from dataclasses import dataclass
import math
from typing import Any

from marsdog_voice_interaction.messages.intent_protocol import COMMAND_KEY_TO_NLU


@dataclass(frozen=True)
class KWSRepeatPolicy:
    command_keys: tuple[str, ...] = ()
    min_hits: int = 2
    min_gap_sec: float = 0.3
    max_gap_sec: float = 1.5

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "KWSRepeatPolicy":
        keys = config.get("repeat_early_command_keys", [])
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise ValueError("repeat_early_command_keys must be a list of command keys")
        keys = tuple(dict.fromkeys(key.strip().upper() for key in keys))
        if any(key not in COMMAND_KEY_TO_NLU for key in keys):
            raise ValueError("repeat_early_command_keys contains an unknown command key")
        min_hits = config.get("repeat_early_min_hits", 2)
        if isinstance(min_hits, bool) or not isinstance(min_hits, int) or min_hits < 2:
            raise ValueError("repeat_early_min_hits must be an integer >= 2")
        min_gap = float(config.get("repeat_early_min_gap_sec", 0.3))
        max_gap = float(config.get("repeat_early_max_gap_sec", 1.5))
        if not (math.isfinite(min_gap) and math.isfinite(max_gap) and 0 < min_gap <= max_gap):
            raise ValueError("repeat_early gaps must be finite and 0 < min_gap_sec <= max_gap_sec")
        if str(config.get("publish_mode", "deferred")).strip().lower() != "deferred":
            keys = ()
        if keys and not config.get("negation_guard_enabled", False):
            raise ValueError("repeat_early_command_keys requires negation_guard_enabled=true")
        return cls(keys, min_hits, min_gap, max_gap)
