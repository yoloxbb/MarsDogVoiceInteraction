"""Mock ASR provider — globally randomized sentence pool.

Every transcription independently samples from all mock sentences.  Theme
groups are retained only to keep the fixture readable; they do not control
selection order.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)

# ── Sentence pool — organized by theme category ─────────────────────

_CATEGORY_POOLS: dict[str, list[str]] = {
    "command": [
        "坐下", "过来", "握手", "击掌", "趴下", "转圈",
        "翻滚", "吐掉", "装死", "停止", "随行", "跟着我",
        "把红球拿给我", "把飞盘拿给我", "找玩具", "把绳子拿过来",
        "别动了", "过来一下", "站好",
        # 纯指令，不含情绪
    ],
    "praise": [
        "太棒了", "真棒", "好厉害", "你真聪明", "干得好",
        "好孩子", "真乖", "你太厉害了", "很棒", "做得真好",
        "太好了", "非常好", "真不错", "你最棒",
    ],
    "happy": [
        "今天好开心", "我好高兴", "你真是我的好伙伴", "好爱你",
        "今天天气真好", "见到你真好", "我好喜欢你",
        "今天真开心", "好幸福呀", "有你真好",
    ],
    "sad": [
        "我好难过", "今天不太开心", "我有点累",
        "心里不舒服", "别难过", "我好想你",
        "好伤心", "好失落",
    ],
    "neutral": [
        "今天过得怎么样", "你好", "谢谢你", "我喜欢你",
        "你在干嘛", "我们玩个游戏吧", "你好小狗", "嗨",
        "聊聊天吧", "出去散步吗", "出去散步",
    ],
    "scold": [
        "不许吃", "不准叫", "快吐出来",
        "不许咬", "不许碰", "不准乱跑",
        "不可以", "不许乱叫",
    ],
}

_ALL_UTTERANCES: tuple[tuple[str, str], ...] = tuple(
    (category, text)
    for category, sentences in _CATEGORY_POOLS.items()
    for text in sentences
)


class MockASRProvider(BaseProvider):
    """Mock ASR with independent global sentence selection.

    Attributes:
        _mock_asr_text: Legacy fallback text.
        _language: Language code.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._mock_asr_text = config.get("mock_asr_text", "把红球拿给我")
        self._language = config.get("language", "zh")

    def start(self) -> None:
        try:
            logger.info(
                "MockASRProvider starting — global random pool: "
                "%d sentences in %d categories",
                len(_ALL_UTTERANCES),
                len(_CATEGORY_POOLS),
            )
            self.available = True
            logger.info("MockASRProvider started (global random)")
        except Exception as exc:
            self.available = False
            logger.warning("MockASRProvider start failed: %s", exc, exc_info=True)

    def stop(self) -> None:
        self.available = False
        logger.info("MockASRProvider stopped")

    def transcribe(self, audio_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Independently pick one sentence from the complete mock pool.

        Args:
            audio_data: Audio dict (unused).

        Returns:
            Dict with asr_text, language, confidence.
        """
        _ = audio_data

        if not self.available:
            return {"asr_text": "", "language": self._language, "confidence": 0.0}

        category, text = random.choice(_ALL_UTTERANCES)
        logger.info("MockASR: global_random category=%s text=%r", category, text)
        return {
            "asr_text": text,
            "language": self._language,
            "confidence": 0.95,
        }

    def set_mock_text(self, text: str) -> None:
        """Change legacy fallback text (for debugging)."""
        self._mock_asr_text = text
