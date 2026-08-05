"""Shared EMOTION|ACTION|CONTROL intent protocol helpers."""

from __future__ import annotations

from typing import Any


EMOTION_LABELS = frozenset({
    "NONE",
    "CALM",
    "JOY",
    "EXCITEMENT",
    "ANXIETY",
    "FEAR",
    "SADNESS",
    "LONELINESS",
    "CURIOSITY",
    "PRAISE",
    "REPRIMAND",
})

ACTION_LABELS = frozenset({
    "NONE",
    "COME",
    "SHAKE_HAND",
    "HIGH_FIVE",
    "SIT",
    "LIE_DOWN",
    "STAND_UP",
    "WAIT",
    "FOLLOW",
    "ROLL_OVER",
    "SPIN",
    "RETURN",
    "DROP",
    "PLAY_DEAD",
    "BRING",
    "FETCH",
    "STOP",
    "UNKNOWN",
    "MULTI",
})

CONTROL_LABELS = frozenset({
    "NONE",
    "DO",
    "CANCEL",
    "CLARIFY",
})

_ACTION_TO_COMMAND = {
    "NONE": "CMD_NONE",
    "COME": "CMD_COME_HERE",
    "SHAKE_HAND": "CMD_HAND",
    "HIGH_FIVE": "CMD_FIVE",
    "SIT": "CMD_SIT",
    "LIE_DOWN": "CMD_LIE_DOWN",
    "STAND_UP": "CMD_STAND_UP",
    "WAIT": "CMD_WAIT",
    "FOLLOW": "CMD_FOLLOW",
    "ROLL_OVER": "CMD_ROLL",
    "SPIN": "CMD_SPIN",
    "RETURN": "CMD_BACK",
    "DROP": "CMD_SPIT",
    "PLAY_DEAD": "CMD_DEAD",
    "BRING": "CMD_BRING_OBJECT",
    "FETCH": "CMD_FETCH_OBJECT",
    "STOP": "CMD_STOP",
    "UNKNOWN": "CMD_UNKNOWN",
    "MULTI": "CMD_MULTI",
}

_CONTROL_TO_CATEGORY = {
    "NONE": "none",
    "DO": "command",
    "CANCEL": "cancel",
    "CLARIFY": "clarify",
}

_EMOTION_TO_CATEGORY = {
    "PRAISE": "praise",
    "REPRIMAND": "blame",
}


def parse_intent_tag(raw: str) -> tuple[str, str, str]:
    """Validate an exact EMOTION|ACTION|CONTROL model output.

    The protocol intentionally does not normalize case, trim whitespace, remove
    special tokens, or extract a valid-looking substring. Any such content
    violates the model contract and is rejected.
    """
    if not isinstance(raw, str) or not raw:
        raise ValueError("Intent output must be a non-empty string")

    parts = raw.split("|")
    if len(parts) != 3:
        raise ValueError(
            "Intent output must contain exactly three pipe-delimited fields"
        )

    emotion, action, control = parts
    if emotion not in EMOTION_LABELS:
        raise ValueError(f"Invalid EMOTION label: {emotion!r}")
    if action not in ACTION_LABELS:
        raise ValueError(f"Invalid ACTION label: {action!r}")
    if control not in CONTROL_LABELS:
        raise ValueError(f"Invalid CONTROL label: {control!r}")

    return emotion, action, control


def make_intent_tag(emotion: str, action: str, control: str) -> str:
    """Build and validate a protocol tag."""
    tag = f"{emotion}|{action}|{control}"
    parse_intent_tag(tag)
    return tag


def control_triggers_behavior_tree(control: str) -> bool:
    """Return whether CONTROL requires behavior-tree handling."""
    return control in {"DO", "CANCEL"}


def classification_to_event(
    *,
    emotion: str,
    action: str,
    control: str,
    asr_text: str,
    source: str,
    confidence: float,
    extra_slots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convert a validated classification into an interaction-event payload."""
    tag = make_intent_tag(emotion, action, control)
    triggers_behavior_tree = control_triggers_behavior_tree(control)
    slots = list(extra_slots or [])
    slots.extend([
        {"key": "emotion", "value": emotion},
        {"key": "action", "value": action},
        {"key": "control", "value": control},
        {"key": "raw_tag", "value": tag},
    ])

    if control != "NONE":
        intent_category = _CONTROL_TO_CATEGORY[control]
    elif emotion != "NONE":
        intent_category = _EMOTION_TO_CATEGORY.get(emotion, "emotion")
    else:
        intent_category = "none"

    return {
        "event_type": "intent",
        "asr_text": asr_text,
        "emotion": emotion,
        "action": action,
        "control": control,
        "command_id": _ACTION_TO_COMMAND[action],
        "intent_category": intent_category,
        "intent_source": source,
        "intent_confidence": float(confidence),
        "slots": slots,
        "response_text": "",
        # Kept for existing consumers. New consumers should use CONTROL.
        "is_executable": triggers_behavior_tree,
        "should_trigger_behavior_tree": triggers_behavior_tree,
        "language": "zh",
    }
