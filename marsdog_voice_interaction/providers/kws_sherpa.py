"""Streaming command keyword spotting with sherpa-onnx."""

from __future__ import annotations

from collections import deque
import logging
import math
from pathlib import Path
import threading
from typing import Any

import numpy as np

from marsdog_voice_interaction.core.kws_repeat_policy import KWSRepeatPolicy
from marsdog_voice_interaction.messages.intent_protocol import (
    COMMAND_KEY_TO_COMMAND_ID,
    COMMAND_KEY_TO_NLU,
    classification_to_event,
)
from marsdog_voice_interaction.messages.voice_event_types import (
    ACTION_TO_VOICE_EVENT,
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
        # Deferred publication also needs acoustic veto evidence when ASR
        # loses a negation prefix. The node keeps whole-phrase control exceptions.
        self._guard_enabled = bool(config.get("negation_guard_enabled", False))
        self._negation_keywords_file = str(config.get("negation_keywords_file", ""))
        self._guard_window_sec = float(config.get("negation_guard_window_sec", 0.25))
        if self._guard_enabled and (
            not math.isfinite(self._guard_window_sec) or self._guard_window_sec <= 0
        ):
            raise ValueError("negation_guard_window_sec must be a finite positive number")
        self._repeat_policy = KWSRepeatPolicy.from_config(config)
        self._repeat_key = ""
        self._repeat_hits = 0
        self._repeat_last_sec = 0.0
        self._repeat_early_queued: set[str] = set()

        self._spotter: Any = None
        self._stream: Any = None
        self._negation_spotter: Any = None
        self._negation_stream: Any = None
        self._guard_reason = ""
        self._audio_received_sec = 0.0
        self._pending_commands: deque[tuple[float, dict[str, Any]]] = deque()
        self._stream_lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque()
        self._event_lock = threading.Lock()
        self._seen_actions: set[str] = set()
        # Per-utterance counters.  They exist so the keywords_threshold can be
        # tuned from field logs: a threshold that is too high shows up as many
        # decode steps with zero detections.
        self._utterance_decodes = 0
        self._utterance_hits = 0

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
            if self._guard_enabled:
                required += (self._negation_keywords_file,)
            missing = [path for path in required if not Path(path).is_file()]
            if missing:
                raise FileNotFoundError(
                    "KWS files not found: " + ", ".join(missing)
                )

            common = dict(
                tokens=self._tokens,
                encoder=self._encoder,
                decoder=self._decoder,
                joiner=self._joiner,
                num_threads=self._num_threads,
                sample_rate=self._sample_rate,
                max_active_paths=self._max_active_paths,
                keywords_score=self._keywords_score,
                keywords_threshold=self._keywords_threshold,
                num_trailing_blanks=self._num_trailing_blanks,
                provider=self._provider,
                device=self._device,
            )
            self._spotter = KeywordSpotter(keywords_file=self._keywords_file, **common)
            if self._guard_enabled:
                # A separate vocabulary/stream prevents command hits and resets
                # from consuming the negation prefix. Do not add veto labels to
                # the business command inventory.
                self._negation_spotter = KeywordSpotter(
                    keywords_file=self._negation_keywords_file, **common,
                )
            self.available = True
            logger.info(
                "KWSSherpaProvider started — model=%s keywords=%s "
                "threshold=%.2f keyword_count=%d negation_guard=%s guard_window_sec=%.3f",
                self._encoder,
                self._keywords_file,
                self._keywords_threshold,
                self._count_keywords(),
                self._guard_enabled,
                self._guard_window_sec if self._guard_enabled else 0.0,
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
            self._negation_stream = None
            self._negation_spotter = None
            self._pending_commands.clear()
        with self._event_lock:
            self._events.clear()
        self._seen_actions.clear()
        self.available = False
        logger.info("KWSSherpaProvider stopped")

    def _count_keywords(self) -> int:
        """Number of non-comment keyword lines actually handed to sherpa-onnx."""

        try:
            lines = Path(self._keywords_file).read_text(
                encoding="utf-8"
            ).splitlines()
        except OSError:
            return 0
        return sum(
            1 for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )

    def start_utterance(self) -> None:
        """Start a fresh KWS stream for an upcoming VAD capture."""
        if not self.available or self._spotter is None:
            return
        with self._stream_lock:
            self._stream = self._spotter.create_stream()
            self._negation_stream = (
                self._negation_spotter.create_stream() if self._guard_enabled else None
            )
            self._guard_reason = ""
            self._audio_received_sec = 0.0
            self._pending_commands.clear()
            self._seen_actions.clear()
            self._repeat_key = ""
            self._repeat_hits = 0
            self._repeat_last_sec = 0.0
            self._repeat_early_queued.clear()
            self._utterance_decodes = 0
            self._utterance_hits = 0
            with self._event_lock:
                self._events.clear()

    def finish_utterance(self) -> None:
        """Discard the current stream after all captured chunks were consumed."""
        with self._stream_lock:
            self._stream = None
            self._negation_stream = None
            # Never shorten the confirmation window at VAD end/cancellation.
            # The ordinary ASR route can still handle an unconfirmed command.
            self._pending_commands.clear()
            decodes = self._utterance_decodes
            hits = self._utterance_hits
            self._utterance_decodes = 0
            self._utterance_hits = 0
        if decodes:
            # Zero hits after many decode steps is the fingerprint of a
            # keywords_threshold that is too high for this room/microphone.
            logger.info(
                "KWS utterance summary: decode_steps=%d detections=%d "
                "threshold=%.2f",
                decodes,
                hits,
                self._keywords_threshold,
            )

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

        stream = None
        try:
            with self._stream_lock:
                stream = self._stream
                if stream is None:
                    return
                self._audio_received_sec += waveform.size / int(sample_rate)
                if self._guard_enabled:
                    guard = self._negation_spotter
                    guard_stream = self._negation_stream
                    if guard is None or guard_stream is None:
                        raise RuntimeError("negation detector is unavailable")
                    guard_stream.accept_waveform(int(sample_rate), waveform)
                    # Decode the veto stream first for every audio chunk.
                    while guard.is_ready(guard_stream):
                        guard.decode_stream(guard_stream)
                        negation_keyword = str(guard.get_result(guard_stream)).strip()
                        if negation_keyword:
                            guard.reset_stream(guard_stream)
                            self._block_guard("negation_keyword", keyword=negation_keyword)
                stream.accept_waveform(int(sample_rate), waveform)
                while self._spotter.is_ready(stream):
                    self._utterance_decodes += 1
                    self._spotter.decode_stream(stream)
                    keyword = str(self._spotter.get_result(stream)).strip()
                    if not keyword:
                        continue
                    self._utterance_hits += 1
                    self._spotter.reset_stream(stream)
                    self._queue_keyword(keyword)
                self._release_guarded_commands()
        except Exception as exc:
            if self._guard_enabled:
                with self._stream_lock:
                    if stream is not None and stream is self._stream:
                        self._block_guard("kws_decode_error")
            logger.warning("KWS streaming decode failed: %s", exc)

    def _block_guard(self, reason: str, *, keyword: str = "") -> None:
        """Latch a veto under the stream lock; never expose it as a ROS action."""
        if self._guard_reason == reason or self._guard_reason == "kws_decode_error":
            return
        self._guard_reason = reason
        self._pending_commands.clear()
        with self._event_lock:
            # Also suppress commands ready for polling but not yet consumed.
            self._events.clear()
            self._events.append({"kind": "kws_guard", "reason": reason, "keyword": keyword})
        logger.info("KWS negation guard blocked utterance: reason=%s keyword=%s", reason, keyword)

    def _release_guarded_commands(self) -> None:
        if self._guard_reason:
            return
        while self._pending_commands and self._pending_commands[0][0] <= self._audio_received_sec:
            _, event = self._pending_commands.popleft()
            with self._event_lock:
                self._events.append(event)

    def poll_event(self) -> dict[str, Any] | None:
        """Return the next detected command without blocking."""
        with self._event_lock:
            return self._events.popleft() if self._events else None

    def _queue_keyword(self, keyword: str) -> None:
        command_key = keyword.upper()
        labels = COMMAND_KEY_TO_NLU.get(command_key)
        event_type = ACTION_TO_VOICE_EVENT.get(command_key)
        if labels is None or event_type is None:
            logger.warning("KWS ignored unmapped keyword label: %r", keyword)
            return
        if self._guard_reason:
            logger.info("KWS command suppressed: command=%s reason=%s", command_key, self._guard_reason)
            return
        # Count separate detections before the ordinary per-utterance dedupe.
        # Different command labels or a long gap break the repeat sequence.
        repeat_early = False
        policy = self._repeat_policy
        if command_key in policy.command_keys:
            gap = self._audio_received_sec - self._repeat_last_sec
            if self._repeat_key != command_key or gap > policy.max_gap_sec:
                self._repeat_key = command_key
                self._repeat_hits = 1
                self._repeat_last_sec = self._audio_received_sec
            elif gap >= policy.min_gap_sec:
                self._repeat_hits += 1
                self._repeat_last_sec = self._audio_received_sec
            repeat_early = (
                self._repeat_hits >= policy.min_hits
                and command_key not in self._repeat_early_queued
            )
        else:
            self._repeat_key = ""
            self._repeat_hits = 0
        if command_key in self._seen_actions and not repeat_early:
            return
        self._seen_actions.add(command_key)
        if repeat_early:
            self._repeat_early_queued.add(command_key)

        social, intent, control = labels
        event = classification_to_event(
            social=social,
            intent=intent,
            control=control,
            asr_text=keyword,
            source="kws",
            confidence=self._event_confidence,
            command_id=COMMAND_KEY_TO_COMMAND_ID.get(
                command_key,
                f"CMD_{command_key}",
            ),
            specific_event_type=event_type,
            dispatch_role="specific_command",
            executable=True,
            extra_slots=[{"key": "kws_keyword", "value": keyword}],
        )
        event.update({
            "event_type": event_type,
            # Compatibility action preserves the concrete command key.
            "action": command_key,
            "language": "zh-en",
        })
        if repeat_early:
            event["kws_repeat_early"] = True
            event["kws_repeat_hits"] = self._repeat_hits
        if self._guard_enabled:
            event["kws_guard_window_ms"] = round(self._guard_window_sec * 1000.0, 2)
            self._pending_commands.append((self._audio_received_sec + self._guard_window_sec, event))
        else:
            with self._event_lock:
                self._events.append(event)
        logger.info("KWS detected command: %s", command_key)
