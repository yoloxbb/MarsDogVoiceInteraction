"""Rule fallback for the RKLLM SOCIAL|INTENT|CONTROL protocol."""

from __future__ import annotations

import logging
import re
from typing import Any

from marsdog_voice_interaction.messages.intent_protocol import (
    classification_to_event,
    parse_intent_tag,
)
from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)


# ── Rule definitions ────────────────────────────────────────────

# Each rule is a (pattern, legacy command/category, protocol tag, response, slots_fn).
# slots_fn receives the regex match object and returns a list of slot dicts.

_RULES: list[tuple[re.Pattern, str, str, str, str, Any]] = [
    # ── C (command) ──────────────────────────────────────────
    (re.compile(r"^(?:坐下|sit|sitdown)$", re.IGNORECASE),
     "CMD_SIT", "command", "NONE|SIT|DO", "好的，我坐下了。",
     lambda _m: []),
    (re.compile(r"^(?:趴下|liedown)$", re.IGNORECASE),
     "CMD_LIE_DOWN", "command", "NONE|LIE|DO", "好的，我趴下了。",
     lambda _m: []),
    (re.compile(r"^(?:过来(?:一下)?|come|comehere)$", re.IGNORECASE),
     "CMD_COME_HERE", "command", "NONE|COME|DO", "好的，我过来了。",
     lambda _m: []),
    (re.compile(r"^(?:握手|shakehands?)$", re.IGNORECASE),
     "CMD_HAND", "command", "NONE|SHAKE|DO", "好的，握个手！",
     lambda _m: []),
    (re.compile(r"^(?:击掌|highfive)$", re.IGNORECASE),
     "CMD_FIVE", "command", "NONE|HIGH_FIVE|DO", "击掌！",
     lambda _m: []),
    (re.compile(r"^(?:站起来|standup)$", re.IGNORECASE),
     "CMD_STAND_UP", "command", "NONE|STAND|DO", "好的，我站起来了。",
     lambda _m: []),
    (re.compile(r"^(?:等一下|等等|wait)$", re.IGNORECASE),
     "CMD_WAIT", "command", "NONE|STAY|DO", "好的，我等一下。",
     lambda _m: []),
    (re.compile(r"^(?:随行|跟着我|跟我走|followme)$", re.IGNORECASE),
     "CMD_FOLLOW", "command", "NONE|FOLLOW|DO", "好的，我跟着你。",
     lambda _m: []),
    (re.compile(r"^(?:翻滚|打滚|rollover)$", re.IGNORECASE),
     "CMD_ROLL", "command", "NONE|ROLL|DO", "看我的，翻滚！",
     lambda _m: []),
    (re.compile(r"^(?:转圈|转个圈|转一圈|spin)$", re.IGNORECASE),
     "CMD_SPIN", "command", "NONE|SPIN|DO", "好的，转个圈！",
     lambda _m: []),
    (re.compile(r"^(?:回来|绕回来|绕回|comeback)$", re.IGNORECASE),
     "CMD_BACK", "command", "NONE|COME|DO", "好的，我回来了。",
     lambda _m: []),
    (re.compile(r"^(?:吐掉|吐出来|放下|dropit)$", re.IGNORECASE),
     "CMD_SPIT", "command", "NONE|DROP|DO", "好的，吐掉了。",
     lambda _m: []),
    (re.compile(r"^(?:装死|装死吧|playdead)$", re.IGNORECASE),
     "CMD_DEAD", "command", "NONE|PLAY_DEAD|DO", "装死中…",
     lambda _m: []),
    (re.compile(r"^(?:停止|停下|停|别动了|不许动)$"),
     "CMD_STOP", "command", "NONE|STAY|STOP", "好的，停下了。",
     lambda _m: []),

    # ── B (blame) ────────────────────────────────────────────
    (re.compile(r"^(?:吐出来|不许吃|快吐出来)$"),
     "CMD_SPIT", "blame", "SCOLD|DROP|STOP", "吐掉！好的。",
     lambda _m: []),
    (re.compile(r"^(?:不许|不准|禁止|不可以|不能|别乱|别跑).*"),
     "CMD_WARN", "blame", "SCOLD|NONE|NONE", "警告！收到。",
     lambda _m: []),

    # ── P (praise) ───────────────────────────────────────────
    # Single-word / short praise
    (re.compile(r"^(?:真棒|好狗|乖狗|厉害|干得好|真乖|好孩子|好厉害|好棒|太棒了?|太厉害了?|太好了?|不错|真不错|很好|非常好|真聪明|太聪明了?|做得真好)$"),
     "CMD_PRAISE", "praise", "PRAISE|NONE|NONE", "谢谢你的夸奖！",
     lambda _m: []),
    # "你 + adj" / general praise patterns
    (re.compile(r"^(?:你.*(?:真|好|太|很).*(?:棒|厉害|聪明|乖|好|能干|帅|漂亮|可爱|听话)|.*很棒|.*好厉害|.*太棒|.*真不错|.*太好了|.*真聪明)$"),
     "CMD_PRAISE", "praise", "PRAISE|NONE|NONE", "谢谢你的夸奖！",
     lambda _m: []),
    # Fuzzy match — ASR often adds noise around core praise words
    (re.compile(r"^.*(?:乖狗|好狗|真乖|好乖|太棒了?|真棒|好棒|好厉害|太厉害了?).*$"),
     "CMD_PRAISE", "praise", "PRAISE|NONE|NONE", "谢谢你的夸奖！",
     lambda _m: []),
    # Encouragement
    (re.compile(r"^(?:加油|你可以|你能行|.*加油.*)$"),
     "CMD_ENCOUR", "praise", "PRAISE|NONE|NONE", "谢谢你的鼓励！",
     lambda _m: []),

    # ── E (emotion) ──────────────────────────────────────────
    (re.compile(r"^(?:别难过|别伤心|没事的|别怕)$"),
     "CMD_COMFORT", "emotion", "COMFORT|NONE|NONE", "别难过，我陪着你。",
     lambda _m: []),
    (re.compile(r"^(?:聊聊天.*|陪陪我|说说话)$"),
     "CMD_CHAT", "emotion", "OWNER_NEGATIVE|NONE|NONE", "我在呢。",
     lambda _m: []),
    (re.compile(r"^(?:你好|嗨|哈[喽啰]|hello|hi|我去?喜欢你|谢谢.*|多谢|感谢)$"),
     "CMD_CHAT", "emotion", "CALL|NONE|NONE", "我在呢。",
     lambda _m: []),

    # ── Object tasks (extended from Phase 1, mapped to C) ────
    (re.compile(r"^把(.+?)(?:拿给我|拿过来|给我|过来)$"),
     "CMD_BRING_OBJECT", "command", "NONE|FETCH|DO",
     "好的，我去拿。",
     lambda m: [{"key": "object_name", "value": m.group(1).strip()}]),
    (re.compile(r"^找(.+)$"),
     "CMD_FIND_OBJECT", "command", "NONE|FETCH|DO",
     "好的，我去找。",
     lambda m: [{"key": "object_name", "value": m.group(1).strip()}]),

    # ── Add missing command patterns ────────────────────────
    (re.compile(r"^(?:别跑了?|站好)$"),
     "CMD_STOP", "command", "NONE|STAY|STOP", "好的，停下了。",
     lambda _m: []),

    # ── Fuzzy praise catch-all (runs after specific rules) ──
    (re.compile(r"^.*(?:最棒|最厉害|真可爱|太可爱|好可爱|你最棒).*$"),
     "CMD_PRAISE", "praise", "PRAISE|NONE|NONE", "谢谢你的夸奖！",
     lambda _m: []),

    # ── Happy emotion catch-all ─────────────────────────────
    (re.compile(r"^.*(?:好开心|好高兴|好快乐|好爱你|好喜欢你|真开心|好幸福|真好|真是我的|见到你真好|天气真好).*$"),
     "CMD_CHAT", "emotion", "OWNER_POSITIVE|NONE|NONE", "我也好开心！",
     lambda _m: []),

    # ── Sad emotion catch-all ───────────────────────────────
    (re.compile(r"^.*(?:好难过|不太开心|不开心|好累|不舒服|好想你|心里不舒服|有点累|好伤心|好失落|别难过).*$"),
     "CMD_COMFORT", "emotion", "OWNER_NEGATIVE|NONE|NONE", "别难过，我陪着你。",
     lambda _m: []),

    # ── Neutral chat catch-all ──────────────────────────────
    (re.compile(r"^(?:.*(?:过得怎么样|在干嘛|玩.*游戏|你好小狗|散步|见面|喜欢你|我爱你).*|谢谢.*|聊聊天.*)$"),
     "CMD_CHAT", "emotion", "NONE|NONE|NONE", "我在呢。",
     lambda _m: []),
]

# Number of rules for startup logging
_NUM_RULES = len(_RULES)


class RuleIntentProvider(BaseProvider):
    """Regex fallback that emits the same three-field protocol as RKLLM."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

    def start(self) -> None:
        try:
            logger.info(
                "RuleIntentProvider starting — %d rules loaded",
                _NUM_RULES,
            )
            self.available = True
            logger.info("RuleIntentProvider started")
        except Exception as exc:
            self.available = False
            logger.warning(
                "RuleIntentProvider start failed (unexpected): %s",
                exc,
                exc_info=True,
            )

    def stop(self) -> None:
        self.available = False
        logger.info("RuleIntentProvider stopped")

    def parse_intent(self, asr_text: str) -> dict[str, Any] | None:
        """Parse ASR text into a structured intent.

        Args:
            asr_text: The recognized speech text (Chinese).

        Returns:
            Intent event partial dict if matched, None otherwise.
            Includes SOCIAL|INTENT|CONTROL fields and raw tag.
        """
        if not asr_text or not isinstance(asr_text, str):
            return None

        text = asr_text.strip()
        # Strip trailing Chinese/Western punctuation for robust matching
        import re as _re
        text = _re.sub(r'[，。！？、；：…—～,.!?;:\s]+$', '', text)
        logger.debug("RuleIntentProvider parsing: %r", text)

        for pattern, _command_id, _category, tag, _response, slots_fn in _RULES:
            match = pattern.match(text)
            if match:
                parsed = parse_intent_tag(tag)
                if parsed is None:
                    logger.error("Invalid built-in rule tag: %s", tag)
                    continue
                social, intent, control = parsed
                event = classification_to_event(
                    social=social,
                    intent=intent,
                    control=control,
                    asr_text=text,
                    source="rule_rkllm_compatible",
                    confidence=0.95,
                    extra_slots=slots_fn(match),
                )

                logger.info(
                    "RuleIntentProvider matched: %r -> %s, tag=%s",
                    text,
                    event["command_id"],
                    tag,
                )
                return event

        logger.debug("RuleIntentProvider no match for: %r", text)
        return None
