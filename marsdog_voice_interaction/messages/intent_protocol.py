"""Model Intent ``SOCIAL|INTENT|CONTROL`` protocol helpers.

The Model Intent output is a semantic classification.  It is deliberately kept
separate from the deterministic command catalog: a model label only authorizes
a concrete robot action when the downstream router contains an explicit,
unambiguous allowlist entry.
"""

from __future__ import annotations

from typing import Any


NLU_PROTOCOL = "rkllm_social_intent_control_v1"

SOCIAL_LABELS = frozenset(
    {
        "NONE",
        "CALL",
        "PRAISE",
        "SCOLD",
        "COMFORT",
        "PLAYFUL",
        "OWNER_POSITIVE",
        "OWNER_NEGATIVE",
    }
)

INTENT_LABELS = frozenset(
    {
        "NONE",
        "GO",
        "COME",
        "FOLLOW",
        "GO_OUT",
        "GO_HOME",
        "APPROACH",
        "BACK",
        "SIT",
        "LIE",
        "PLAY_DEAD",
        "STAND",
        "STAY",
        "SHAKE",
        "HIGH_FIVE",
        "SPIN",
        "ROLL",
        "DROP",
        "BARK",
        "EAT",
        "TOILET",
        "CLEAN",
        "SLEEP",
        "PLAY",
        "TUG",
        "FIND_PERSON",
        "DANCE",
        "FETCH",
        "FIND_TOY",
        "OWNER_LEAVE",
        "OWNER_RETURN",
        "DOG_STATUS",
        "DOG_PREFERENCE",
        "DOG_CAPABILITY",
    }
)

CONTROL_LABELS = frozenset({"NONE", "DO", "STOP", "QUERY"})

QUERY_ONLY_INTENTS = frozenset(
    {"DOG_STATUS", "DOG_PREFERENCE", "DOG_CAPABILITY"}
)
OWNER_EVENT_INTENTS = frozenset({"OWNER_LEAVE", "OWNER_RETURN"})

# Canonical semantic labels attached to deterministic command events.  These
# values are metadata only; dispatch remains governed by command_catalog.yaml.
COMMAND_KEY_TO_NLU: dict[str, tuple[str, str, str]] = {
    "WALK": ("NONE", "GO", "DO"),
    "COME": ("NONE", "COME", "DO"),
    "FOLLOW": ("NONE", "FOLLOW", "DO"),
    "GO_OUT": ("NONE", "GO_OUT", "DO"),
    "GO_HOME": ("NONE", "GO_HOME", "DO"),
    "APPROACH": ("NONE", "APPROACH", "DO"),
    "BACK_UP": ("NONE", "BACK", "DO"),
    "SIT": ("NONE", "SIT", "DO"),
    "LIE_DOWN": ("NONE", "LIE", "DO"),
    "PLAY_DEAD": ("NONE", "PLAY_DEAD", "DO"),
    "STAND_UP": ("NONE", "STAND", "DO"),
    "STAND_STILL": ("NONE", "STAY", "DO"),
    "SHAKE_HAND": ("NONE", "SHAKE", "DO"),
    "HIGH_FIVE": ("NONE", "HIGH_FIVE", "DO"),
    "SPIN": ("NONE", "SPIN", "DO"),
    "ROLL_OVER": ("NONE", "ROLL", "DO"),
    "HOLD_POSITION": ("NONE", "STAY", "DO"),
    "WAIT": ("NONE", "STAY", "DO"),
    "DROP": ("NONE", "DROP", "DO"),
    "QUIET": ("NONE", "BARK", "STOP"),
    "BRING": ("NONE", "FETCH", "DO"),
    "FETCH": ("NONE", "FETCH", "DO"),
    "RETURN": ("NONE", "COME", "DO"),
    "STOP": ("NONE", "STAY", "STOP"),
}

COMMAND_KEY_TO_COMMAND_ID: dict[str, str] = {
    key: f"CMD_{key}" for key in COMMAND_KEY_TO_NLU
}


def validate_intent_combination(
    social: str,
    intent: str,
    control: str,
) -> bool:
    """Return whether a three-axis result satisfies the frozen contract."""

    if (
        social not in SOCIAL_LABELS
        or intent not in INTENT_LABELS
        or control not in CONTROL_LABELS
    ):
        return False
    if intent == "NONE":
        return control == "NONE"
    if intent in QUERY_ONLY_INTENTS:
        return control == "QUERY"
    if intent in OWNER_EVENT_INTENTS:
        return control == "DO"
    return control in {"DO", "STOP", "QUERY"}


def parse_intent_tag(value: Any) -> tuple[str, str, str] | None:
    """Strictly parse a Model Intent response; surrounding prose is rejected."""

    if not isinstance(value, str):
        return None
    parts = [part.strip().upper() for part in value.strip().split("|")]
    if len(parts) != 3:
        return None
    social, intent, control = parts
    if not validate_intent_combination(social, intent, control):
        return None
    return social, intent, control


def make_intent_tag(social: str, intent: str, control: str) -> str:
    social = str(social).strip().upper()
    intent = str(intent).strip().upper()
    control = str(control).strip().upper()
    if not validate_intent_combination(social, intent, control):
        raise ValueError(
            f"invalid {NLU_PROTOCOL} value: {social}|{intent}|{control}"
        )
    return f"{social}|{intent}|{control}"


def classification_to_event(
    social: str,
    intent: str,
    control: str,
    *,
    asr_text: str,
    source: str,
    confidence: float = 0.0,
    language: str = "zh",
    command_id: str = "",
    specific_event_type: str = "",
    dispatch_role: str = "classification",
    executable: bool = False,
    extra_slots: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build common payload fields for a validated three-axis result."""

    raw_tag = make_intent_tag(social, intent, control)
    if not command_id and intent != "NONE":
        command_id = f"INTENT_{intent}_{control}"
    if control == "QUERY":
        category = "query"
    elif intent in {"PLAY", "TUG", "DANCE"}:
        category = "play"
    elif intent != "NONE":
        category = "command"
    elif social != "NONE":
        category = "social"
    else:
        category = "none"
    slots = list(extra_slots or [])
    return {
        "asr_text": str(asr_text),
        "social": social,
        "intent": intent,
        "control": control,
        # Deprecated aliases retained for one compatibility window.
        "emotion": social,
        "action": intent,
        "language": str(language),
        "command_id": command_id,
        "intent_category": category,
        "intent_source": str(source),
        "intent_confidence": float(confidence),
        "nlu_protocol": NLU_PROTOCOL,
        "raw_nlu_tag": raw_tag,
        "specific_event_type": str(specific_event_type),
        "dispatch_role": str(dispatch_role),
        "slots": slots,
        "is_executable": bool(executable),
        "should_trigger_behavior_tree": bool(executable),
    }
