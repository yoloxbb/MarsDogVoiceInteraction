"""Route Model Intent classifications to downstream business events.

Every valid classification produces one or more coarse semantic events.  A
small explicit allowlist may additionally authorize an unambiguous concrete
command; model labels outside that allowlist remain non-executable.
"""

from __future__ import annotations

import re
from typing import Any

from marsdog_voice_interaction.messages.intent_protocol import (
    classification_to_event,
)
from marsdog_voice_interaction.messages.voice_event_types import (
    EVT_VOICE_CALL_NAME,
    EVT_VOICE_COMMAND_APPROACH,
    EVT_VOICE_COMMAND_BACK_UP,
    EVT_VOICE_COMMAND_CLEAN,
    EVT_VOICE_COMMAND_COME,
    EVT_VOICE_COMMAND_DROP,
    EVT_VOICE_COMMAND_FETCH,
    EVT_VOICE_COMMAND_FOLLOW,
    EVT_VOICE_COMMAND_GO_HOME,
    EVT_VOICE_COMMAND_GO_OUT,
    EVT_VOICE_COMMAND_HIGH_FIVE,
    EVT_VOICE_COMMAND_HOLD_POSITION,
    EVT_VOICE_COMMAND_KNOWN,
    EVT_VOICE_COMMAND_LIE_DOWN,
    EVT_VOICE_COMMAND_PLAY_DEAD,
    EVT_VOICE_COMMAND_QUIET,
    EVT_VOICE_COMMAND_ROLL_OVER,
    EVT_VOICE_COMMAND_SHAKE_HAND,
    EVT_VOICE_COMMAND_SIT,
    EVT_VOICE_COMMAND_SLEEP,
    EVT_VOICE_COMMAND_SPIN,
    EVT_VOICE_COMMAND_STAND_STILL,
    EVT_VOICE_COMMAND_STAND_UP,
    EVT_VOICE_COMMAND_TOILET,
    EVT_VOICE_COMMAND_WALK,
    EVT_VOICE_COMFORT,
    EVT_VOICE_NEGATIVE_EMOTION,
    EVT_VOICE_NEUTRAL,
    EVT_VOICE_PLAY_INTERACTION,
    EVT_VOICE_POSITIVE_EMOTION,
    EVT_VOICE_PRAISE,
    EVT_VOICE_SCOLD,
    EVT_VOICE_STATUS_CARE,
)


_SOCIAL_EVENTS = {
    "CALL": EVT_VOICE_CALL_NAME,
    "PRAISE": EVT_VOICE_PRAISE,
    "SCOLD": EVT_VOICE_SCOLD,
    "COMFORT": EVT_VOICE_COMFORT,
    "PLAYFUL": EVT_VOICE_PLAY_INTERACTION,
    "OWNER_POSITIVE": EVT_VOICE_POSITIVE_EMOTION,
    "OWNER_NEGATIVE": EVT_VOICE_NEGATIVE_EMOTION,
}
_PLAY_INTENTS = frozenset({"PLAY", "TUG", "DANCE"})

# Model classifications only gain execution authority through this explicit
# one-to-one allowlist.  Ambiguous labels such as STAY, EAT and FIND_PERSON
# deliberately stay coarse until the ASR text or slots resolve the concrete
# product action.  FETCH/FIND_TOY use a separate supported-object gate below.
# Tuple values are (command_key, command_id, event_type).
_MODEL_COMMAND_ALLOWLIST: dict[
    tuple[str, str], tuple[str, str, str]
] = {
    ("GO", "DO"): (
        "WALK", "CMD_WALK", EVT_VOICE_COMMAND_WALK,
    ),
    ("COME", "DO"): (
        "COME", "CMD_COME_HERE", EVT_VOICE_COMMAND_COME,
    ),
    ("FOLLOW", "DO"): (
        "FOLLOW", "CMD_FOLLOW", EVT_VOICE_COMMAND_FOLLOW,
    ),
    ("GO_OUT", "DO"): (
        "GO_OUT", "CMD_GO_OUT", EVT_VOICE_COMMAND_GO_OUT,
    ),
    ("GO_HOME", "DO"): (
        "GO_HOME", "CMD_GO_HOME", EVT_VOICE_COMMAND_GO_HOME,
    ),
    ("APPROACH", "DO"): (
        "APPROACH", "CMD_APPROACH", EVT_VOICE_COMMAND_APPROACH,
    ),
    ("BACK", "DO"): (
        "BACK_UP", "CMD_BACK_UP", EVT_VOICE_COMMAND_BACK_UP,
    ),
    ("SIT", "DO"): (
        "SIT", "CMD_SIT", EVT_VOICE_COMMAND_SIT,
    ),
    ("LIE", "DO"): (
        "LIE_DOWN", "CMD_LIE_DOWN", EVT_VOICE_COMMAND_LIE_DOWN,
    ),
    ("PLAY_DEAD", "DO"): (
        "PLAY_DEAD", "CMD_DEAD", EVT_VOICE_COMMAND_PLAY_DEAD,
    ),
    ("STAND", "DO"): (
        "STAND_UP", "CMD_STAND_UP", EVT_VOICE_COMMAND_STAND_UP,
    ),
    ("SHAKE", "DO"): (
        "SHAKE_HAND", "CMD_HAND", EVT_VOICE_COMMAND_SHAKE_HAND,
    ),
    ("HIGH_FIVE", "DO"): (
        "HIGH_FIVE", "CMD_FIVE", EVT_VOICE_COMMAND_HIGH_FIVE,
    ),
    ("SPIN", "DO"): (
        "SPIN", "CMD_SPIN", EVT_VOICE_COMMAND_SPIN,
    ),
    ("ROLL", "DO"): (
        "ROLL_OVER", "CMD_ROLL", EVT_VOICE_COMMAND_ROLL_OVER,
    ),
    ("DROP", "DO"): (
        "DROP", "CMD_SPIT", EVT_VOICE_COMMAND_DROP,
    ),
    ("BARK", "STOP"): (
        "QUIET", "CMD_QUIET", EVT_VOICE_COMMAND_QUIET,
    ),
    ("TOILET", "DO"): (
        "TOILET", "CMD_TOILET", EVT_VOICE_COMMAND_TOILET,
    ),
    ("CLEAN", "DO"): (
        "CLEAN", "CMD_CLEAN", EVT_VOICE_COMMAND_CLEAN,
    ),
    ("SLEEP", "DO"): (
        "SLEEP", "CMD_SLEEP", EVT_VOICE_COMMAND_SLEEP,
    ),
}

_ROUTE_TEXT_SEPARATORS = re.compile(
    r"[，。！？、；：“”\"'（）【】《》…—～,.!?;:()\[\]<>/\s]+"
)
_STAY_STAND_STILL_MARKERS = (
    "站好",
    "站着",
    "站稳",
    "站直",
    "站立",
    "站姿",
    "standstill",
    "staystanding",
    "remainstanding",
)
_STAY_HOLD_POSITION_MARKERS = (
    "别动",
    "不要动",
    "不准动",
    "不许动",
    "不能动",
    "保持不动",
    "等着",
    "等等",
    "原地",
    "不要走",
    "别走",
    "停下",
    "dontmove",
    "donotmove",
    "staythere",
    "holdstill",
    "waitthere",
    "stopmoving",
)

# A valid RKLLM label is only a semantic candidate.  It may authorize a
# concrete physical command only when the ASR text independently contains
# intent-specific evidence.  This blocks protocol-valid but semantically
# wrong outputs such as ``去睡觉 -> GO|DO``.
_MODEL_ACTION_EVIDENCE: dict[tuple[str, str], tuple[str, ...]] = {
    ("GO", "DO"): (
        "走吧", "走开", "走一走", "去走", "散步", "溜达", "前进", "往前",
        "出发", "walk", "forward",
    ),
    ("COME", "DO"): ("过来", "回来", "来这", "到我这", "comehere", "comeback"),
    ("FOLLOW", "DO"): ("跟着", "跟我", "跟随", "follow"),
    ("GO_OUT", "DO"): ("出去", "出门", "外面", "goout"),
    ("GO_HOME", "DO"): ("回家", "回去", "gohome"),
    ("APPROACH", "DO"): ("靠近", "近一点", "贴近", "closer", "approach"),
    ("BACK", "DO"): ("后退", "退后", "往后", "倒退", "backup", "backward"),
    ("SIT", "DO"): ("坐下", "坐好", "请坐", "坐吧", "蹲下", "sit"),
    ("LIE", "DO"): ("趴下", "趴好", "躺下", "躺好", "liedown"),
    ("PLAY_DEAD", "DO"): ("装死", "biu", "playdead"),
    ("STAND", "DO"): ("站起来", "起立", "起来", "standup"),
    ("SHAKE", "DO"): ("握手", "抬手", "shakehands"),
    ("HIGH_FIVE", "DO"): ("击掌", "拍手", "highfive"),
    ("SPIN", "DO"): ("转圈", "旋转", "转一圈", "spin", "turnaround"),
    ("ROLL", "DO"): ("翻滚", "打滚", "rollover"),
    ("DROP", "DO"): ("放下", "松开", "松口", "吐出来", "张嘴", "drop", "letgo"),
    ("BARK", "STOP"): ("安静", "闭嘴", "别叫", "不要叫", "不许叫", "quiet"),
    ("TOILET", "DO"): (
        "去尿", "尿尿", "便便", "去厕所", "如厕", "potty", "toilet",
    ),
    ("CLEAN", "DO"): ("擦一下", "擦擦", "清洁", "洗干净", "洗一下", "clean", "wipe"),
    ("SLEEP", "DO"): ("睡觉", "睡吧", "去睡", "休息", "sleep", "rest"),
    ("FETCH", "DO"): ("拿", "取", "叼", "捡", "给我", "fetch", "bring"),
    ("FIND_TOY", "DO"): ("找", "寻找", "find"),
    ("FIND_TOY", "QUERY"): ("找", "哪里", "哪儿", "在哪", "find", "where"),
}
_NEGATED_DO_MARKERS = (
    "不要",
    "别",
    "不用",
    "不许",
    "不准",
    "不想",
    "不能",
    "donot",
    "dont",
    "donotwant",
)
_NON_COMMAND_SUBJECT_PREFIXES = ("我", "他", "她", "它", "爸爸", "妈妈")
_QUESTION_SUFFIXES = ("吗", "呢", "么", "嘛")


def _slot_value(slots: list[dict[str, str]], key: str) -> str:
    for slot in slots:
        if str(slot.get("key", "")) == key:
            return str(slot.get("value", "")).strip()
    return ""


def _model_command_route(
    intent: str,
    control: str,
    object_name: str = "",
    asr_text: str = "",
) -> tuple[str, str, str] | None:
    route = _MODEL_COMMAND_ALLOWLIST.get((intent, control))
    if route is not None:
        return route
    if intent == "STAY" and control == "DO":
        normalized_text = _ROUTE_TEXT_SEPARATORS.sub(
            "", str(asr_text)
        ).lower()
        if any(
            marker in normalized_text
            for marker in _STAY_STAND_STILL_MARKERS
        ):
            return (
                "STAND_STILL",
                "CMD_STAND_STILL",
                EVT_VOICE_COMMAND_STAND_STILL,
            )
        if any(
            marker in normalized_text
            for marker in _STAY_HOLD_POSITION_MARKERS
        ):
            return (
                "HOLD_POSITION",
                "CMD_HOLD_POSITION",
                EVT_VOICE_COMMAND_HOLD_POSITION,
            )
    if object_name and object_name != "NONE":
        if intent == "FIND_TOY" and control in {"DO", "QUERY"}:
            return (
                "FETCH",
                "CMD_FETCH_OBJECT",
                EVT_VOICE_COMMAND_FETCH,
            )
        if intent == "FETCH" and control == "DO":
            return (
                "FETCH",
                "CMD_FETCH_OBJECT",
                EVT_VOICE_COMMAND_FETCH,
            )
    return None


def _model_action_evidence(
    intent: str,
    control: str,
    *,
    asr_text: str,
    object_name: str = "",
) -> tuple[bool, str]:
    """Require text evidence before an RKLLM label can execute an action."""

    route = _model_command_route(intent, control, object_name, asr_text)
    if route is None:
        return False, "not_specific_allowlist"
    normalized_text = _ROUTE_TEXT_SEPARATORS.sub(
        "", str(asr_text)
    ).lower()
    if not normalized_text:
        return False, "empty_asr_text"
    if control == "DO" and normalized_text.startswith(
        _NON_COMMAND_SUBJECT_PREFIXES
    ):
        return False, "statement_subject_prefix"
    if control == "DO" and normalized_text.endswith(_QUESTION_SUFFIXES):
        return False, "question_text"
    if control == "DO" and intent != "STAY" and any(
        marker in normalized_text for marker in _NEGATED_DO_MARKERS
    ):
        return False, "negated_action_text"
    if intent == "STAY":
        return True, "stay_text_resolved"
    markers = _MODEL_ACTION_EVIDENCE.get((intent, control), ())
    if any(marker in normalized_text for marker in markers):
        return True, "intent_text_evidence"
    return False, "missing_intent_text_evidence"


def derive_intent_routes(
    social: str,
    intent: str,
    control: str,
    *,
    object_name: str = "",
    asr_text: str = "",
) -> list[tuple[str, str]]:
    """Return ordered ``(event_type, derived_axis)`` routes without duplicates."""

    routes: list[tuple[str, str]] = []
    social_event = _SOCIAL_EVENTS.get(social)
    if social_event:
        routes.append((social_event, "social"))

    command_route = _model_command_route(
        intent,
        control,
        object_name,
        asr_text,
    )
    if command_route:
        routes.append((command_route[2], "specific_command"))

    intent_event = ""
    if intent != "NONE":
        if control == "QUERY":
            intent_event = EVT_VOICE_STATUS_CARE
        elif intent in _PLAY_INTENTS:
            intent_event = EVT_VOICE_PLAY_INTERACTION
        elif control in {"DO", "STOP"}:
            intent_event = EVT_VOICE_COMMAND_KNOWN
    if intent_event and all(event_type != intent_event for event_type, _ in routes):
        routes.append((intent_event, "intent"))
    if not routes and (social, intent, control) == ("NONE", "NONE", "NONE"):
        routes.append((EVT_VOICE_NEUTRAL, "neutral"))
    return routes


def route_classification_events(
    social: str,
    intent: str,
    control: str,
    *,
    asr_text: str,
    source: str,
    confidence: float = 0.0,
    language: str = "zh",
    extra_slots: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Build ordered business events for one validated Model Intent result."""

    base = classification_to_event(
        social,
        intent,
        control,
        asr_text=asr_text,
        source=source,
        confidence=confidence,
        language=language,
        dispatch_role="semantic_classification",
        executable=False,
        extra_slots=extra_slots,
    )
    events: list[dict[str, Any]] = []
    object_name = _slot_value(base["slots"], "object_name")
    potential_command_route = _model_command_route(
        intent,
        control,
        object_name,
        asr_text,
    )
    command_route = potential_command_route
    if source == "rkllm" and potential_command_route is not None:
        gate_allowed, gate_reason = _model_action_evidence(
            intent,
            control,
            asr_text=asr_text,
            object_name=object_name,
        )
        base["slots"] = [
            *base["slots"],
            {
                "key": "model_action_gate",
                "value": "accepted" if gate_allowed else "rejected",
            },
            {"key": "model_action_gate_reason", "value": gate_reason},
        ]
        if not gate_allowed:
            command_route = None
    for event_type, derived_axis in derive_intent_routes(
        social,
        intent,
        control,
        object_name=object_name,
        asr_text=asr_text,
    ):
        if derived_axis == "specific_command" and command_route is None:
            continue
        event = dict(base)
        event["event_type"] = event_type
        event["slots"] = [
            *base["slots"],
            {"key": "derived_axis", "value": derived_axis},
        ]
        if derived_axis == "specific_command" and command_route is not None:
            command_key, command_id, specific_event_type = command_route
            event.update({
                "action": command_key,
                "command_id": command_id,
                "specific_event_type": specific_event_type,
                "dispatch_role": "specific_command",
                "is_executable": True,
                "should_trigger_behavior_tree": True,
                "slots": [
                    *event["slots"],
                    {"key": "command_key", "value": command_key},
                    {
                        "key": "model_dispatch_policy",
                        "value": "allowlist_and_text_evidence",
                    },
                ],
            })
        elif derived_axis == "intent" and command_route is not None:
            command_key, command_id, specific_event_type = command_route
            event.update({
                "action": command_key,
                "command_id": command_id,
                "specific_event_type": specific_event_type,
                "slots": [
                    *event["slots"],
                    {"key": "command_key", "value": command_key},
                    {"key": "specific_dispatch", "value": "published"},
                ],
            })
        elif derived_axis == "neutral":
            event["intent_category"] = "neutral"
        events.append(event)
    return events
