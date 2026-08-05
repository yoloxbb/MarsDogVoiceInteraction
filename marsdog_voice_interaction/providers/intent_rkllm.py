"""RKLLM ChatML intent provider.

The fine-tuned model receives a single ChatML user turn and must return exactly:

    EMOTION|ACTION|CONTROL
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from marsdog_voice_interaction.adapters.llm.rkllm_engine_chatml import (
    DEFAULT_SYSTEM_PROMPT,
    RKLLMEngine,
)
from marsdog_voice_interaction.messages.intent_protocol import (
    classification_to_event,
    parse_intent_tag,
)
from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)

# RKLLM Runtime calls are not reentrant.
_RKLLM_LOCK = threading.Lock()


def _parse_tag(raw: str) -> dict[str, str] | None:
    """Parse an exact EMOTION|ACTION|CONTROL tag."""
    try:
        emotion, action, control = parse_intent_tag(raw)
    except ValueError:
        return None
    return {
        "EMOTION": emotion,
        "ACTION": action,
        "CONTROL": control,
    }


def _tag_to_intent_event(
    tag_parts: dict[str, str],
    asr_text: str,
) -> dict[str, Any]:
    """Convert protocol fields to a ROS interaction-event partial payload."""
    return classification_to_event(
        emotion=tag_parts["EMOTION"],
        action=tag_parts["ACTION"],
        control=tag_parts["CONTROL"],
        asr_text=asr_text,
        source="rkllm",
        confidence=0.90,
    )


class IntentRKLLMProvider(BaseProvider):
    """Intent classification provider backed by the ChatML RKLLM engine."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._model_path = config.get("model", "")
        self._lib_path = config.get("lib_path")
        self._platform = config.get("platform", "rk3588")
        self._max_context_len = int(config.get("max_context_len", 512))
        self._max_new_tokens = int(config.get("max_new_tokens", 16))
        self._top_k = int(config.get("top_k", 1))
        self._top_p = float(config.get("top_p", 1.0))
        self._temperature = float(config.get("temperature", 0.0))
        self._repeat_penalty = float(config.get("repeat_penalty", 1.0))
        self._system_prompt = str(
            config.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        )
        self._lora_model_path = config.get("lora_model_path")
        self._prompt_cache_path = config.get("prompt_cache_path")
        self._verbose = bool(config.get("verbose", False))

        self._engine: RKLLMEngine | None = None

    def start(self) -> None:
        try:
            logger.info(
                "IntentRKLLMProvider starting ChatML engine — model=%s, "
                "platform=%s",
                self._model_path,
                self._platform,
            )
            self._engine = RKLLMEngine(
                model_path=self._model_path,
                platform=self._platform,
                max_context_len=self._max_context_len,
                max_new_tokens=self._max_new_tokens,
                top_k=self._top_k,
                top_p=self._top_p,
                temperature=self._temperature,
                repeat_penalty=self._repeat_penalty,
                lora_model_path=self._lora_model_path,
                prompt_cache_path=self._prompt_cache_path,
                lib_path=self._lib_path,
                verbose=self._verbose,
            )
            self.available = True
            logger.info("IntentRKLLMProvider ChatML engine started")
        except FileNotFoundError as exc:
            self._engine = None
            self.available = False
            logger.warning(
                "IntentRKLLMProvider unavailable — model or lib not found: %s",
                exc,
            )
        except Exception as exc:
            self._engine = None
            self.available = False
            logger.warning(
                "IntentRKLLMProvider unavailable — init failed: %s",
                exc,
                exc_info=True,
            )

    def stop(self) -> None:
        if self._engine is not None:
            try:
                self._engine.release()
            except Exception as exc:
                logger.warning("Error releasing RKLLM engine: %s", exc)
            self._engine = None
        self.available = False
        logger.info("IntentRKLLMProvider stopped")

    def parse_intent(self, asr_text: str) -> dict[str, Any] | None:
        """Classify one ASR utterance using the fine-tuned ChatML format."""
        if self._engine is None or not self.available:
            return None
        if not isinstance(asr_text, str) or not asr_text.strip():
            return None

        utterance = asr_text.strip()
        try:
            with _RKLLM_LOCK:
                output = self._engine.classify(
                    utterance,
                    system_prompt=self._system_prompt,
                    stream_print=False,
                    max_new_tokens=self._max_new_tokens,
                )

            tag_parts = _parse_tag(output)
            if tag_parts is None:
                logger.warning(
                    "IntentRKLLM rejected non-protocol output for %r: %r",
                    utterance,
                    output,
                )
                return None

            event = _tag_to_intent_event(tag_parts, utterance)
            logger.info(
                "IntentRKLLM: %r -> %s (EMOTION=%s ACTION=%s CONTROL=%s)",
                utterance,
                event["command_id"],
                tag_parts["EMOTION"],
                tag_parts["ACTION"],
                tag_parts["CONTROL"],
            )
            return event
        except ValueError as exc:
            logger.warning(
                "IntentRKLLM invalid classification for %r: %s",
                utterance,
                exc,
            )
            return None
        except Exception as exc:
            logger.error(
                "IntentRKLLM parse_intent failed for %r: %s",
                utterance,
                exc,
                exc_info=True,
            )
            return None
