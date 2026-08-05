"""Streaming command keyword spotting with sherpa-onnx."""

from __future__ import annotations

from collections import deque
import logging
from pathlib import Path
import threading
from typing import Any

import numpy as np

from marsdog_voice_interaction.messages.intent_protocol import (
    ACTION_LABELS,
    classification_to_event,
)
from marsdog_voice_interaction.messages.voice_event_types import (
    classification_to_voice_event,
)
from marsdog_voice_interaction.providers.base import BaseProvider


logger = logging.getLogger(__name__)


class KWSSherpaProvider(BaseProvider):
    """Consume live microphone chunks and emit canonical command events."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        model_dir = Path(str(config.get("model_dir", ""))).expanduser()
        self._tokens = str(config.get("tokens") or model_dir / "tokens.txt")
        self._encoder = str(
            config.get("encoder")
            or model_dir
            / "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx"
        )
        self._decoder = str(
            config.get("decoder")
            or model_dir / "decoder-epoch-13-avg-2-chunk-8-left-64.onnx"
        )
        self._joiner = str(
            config.get("joiner")
            or model_dir
            / "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx"
        )
        self._keywords_file = str(config.get("keywords_file", ""))
        self._sample_rate = int(config.get("sample_rate", 16000))
        self._num_threads = int(config.get("num_threads", 2))
        self._max_active_paths = int(config.get("max_active_paths", 4))
        self._keywords_score = float(config.get("keywords_score", 1.0))
        self._keywords_threshold = float(
            config.get("keywords_threshold", 0.25)
        )
        self._num_trailing_blanks = int(
            config.get("num_trailing_blanks", 1)
        )
        self._provider = str(config.get("provider", "cpu"))
        self._device = int(config.get("device", 0))
        self._event_confidence = float(
            config.get("event_confidence", 0.90)
        )

        self._spotter: Any = None
        self._stream: Any = None
        self._stream_lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque()
        self._event_lock = threading.Lock()
        self._seen_actions: set[str] = set()

    def start(self) -> None:
        try:
            from sherpa_onnx import KeywordSpotter

            required = (
                self._tokens,
                self._encoder,
                self._decoder,
                self._joiner,
                self._keywords_file,
            )
            missing = [path for path in required if not Path(path).is_file()]
            if missing:
                raise FileNotFoundError(
                    "KWS files not found: " + ", ".join(missing)
                )

            self._spotter = KeywordSpotter(
                tokens=self._tokens,
                encoder=self._encoder,
                decoder=self._decoder,
                joiner=self._joiner,
                keywords_file=self._keywords_file,
                num_threads=self._num_threads,
                sample_rate=self._sample_rate,
                max_active_paths=self._max_active_paths,
                keywords_score=self._keywords_score,
                keywords_threshold=self._keywords_threshold,
                num_trailing_blanks=self._num_trailing_blanks,
                provider=self._provider,
                device=self._device,
            )
            self.available = True
            logger.info(
                "KWSSherpaProvider started — model=%s keywords=%s "
                "threshold=%.2f",
                self._encoder,
                self._keywords_file,
                self._keywords_threshold,
            )
        except FileNotFoundError as exc:
            self.available = False
            logger.warning("KWSSherpaProvider unavailable: %s", exc)
        except Exception as exc:
            self.available = False
            logger.warning(
                "KWSSherpaProvider unavailable: %s",
                exc,
                exc_info=True,
            )

    def stop(self) -> None:
        with self._stream_lock:
            self._stream = None
            self._spotter = None
        with self._event_lock:
            self._events.clear()
        self._seen_actions.clear()
        self.available = False
        logger.info("KWSSherpaProvider stopped")

    def start_utterance(self) -> None:
        """Start a fresh KWS stream for an upcoming VAD capture."""
        if not self.available or self._spotter is None:
            return
        with self._stream_lock:
            self._stream = self._spotter.create_stream()
            self._seen_actions.clear()
        with self._event_lock:
            self._events.clear()

    def finish_utterance(self) -> None:
        """Discard the current stream after all captured chunks were consumed."""
        with self._stream_lock:
            self._stream = None

    def accept_waveform(
        self,
        samples: np.ndarray | list[float],
        sample_rate: int,
    ) -> None:
        """Accept one live microphone chunk and run all ready decode steps."""
        if not self.available or self._spotter is None:
            return
        waveform = np.asarray(samples, dtype=np.float32).reshape(-1)
        if waveform.size == 0:
            return

        try:
            with self._stream_lock:
                stream = self._stream
                if stream is None:
                    return
                stream.accept_waveform(int(sample_rate), waveform)
                while self._spotter.is_ready(stream):
                    self._spotter.decode_stream(stream)
                    keyword = str(self._spotter.get_result(stream)).strip()
                    if not keyword:
                        continue
                    self._spotter.reset_stream(stream)
                    self._queue_keyword(keyword)
        except Exception as exc:
            logger.warning("KWS streaming decode failed: %s", exc)

    def poll_event(self) -> dict[str, Any] | None:
        """Return the next detected command without blocking."""
        with self._event_lock:
            return self._events.popleft() if self._events else None

    def _queue_keyword(self, keyword: str) -> None:
        action = keyword.upper()
        if action not in ACTION_LABELS or action in {
            "NONE",
            "UNKNOWN",
            "MULTI",
        }:
            logger.warning("KWS ignored unmapped keyword label: %r", keyword)
            return
        if action in self._seen_actions:
            return
        self._seen_actions.add(action)

        control = "CANCEL" if action == "STOP" else "DO"
        event = classification_to_event(
            emotion="NONE",
            action=action,
            control=control,
            asr_text=keyword,
            source="kws",
            confidence=self._event_confidence,
            extra_slots=[{"key": "kws_keyword", "value": keyword}],
        )
        event.update({
            "event_type": classification_to_voice_event(
                "NONE",
                action,
                control,
            ),
            "language": "zh-en",
        })
        with self._event_lock:
            self._events.append(event)
        logger.info("KWS detected command: %s", action)
