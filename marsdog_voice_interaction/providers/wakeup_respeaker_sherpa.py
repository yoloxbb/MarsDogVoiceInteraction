"""ReSpeaker Flex wakes plus a host sherpa keyword spotter on the same microphone.

The Flex firmware recognizer misses about half of the wake words that the host
sherpa spotter catches on the same audio, so either one may wake. A wake within
``dedupe_sec`` of the previous one is the same utterance and is dropped. Call-here
keywords are gated and debounced by the node only after session/direction checks. Sherpa
wakes, and Flex wakes whose own bearing is too old, take their bearing from the
latest valid Flex DOA heard while the word was being spoken.
"""

from __future__ import annotations

from collections import deque
import logging
import math
from pathlib import Path
import threading
import time
from typing import Any

import numpy as np

from marsdog_voice_interaction.messages.audio_event import WAKE_ANGLE_FRAME_ID
from marsdog_voice_interaction.messages.voice_event_types import (
    CALL_HERE_KEYWORDS,
    EVT_VOICE_CALL_HERE,
)
from marsdog_voice_interaction.providers.base import BaseProvider
from marsdog_voice_interaction.providers.wakeup_respeaker_serial import WakeupReSpeakerSerialProvider

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHUNK_SEC = 0.02


def _positive(config: dict[str, Any], name: str, default: float) -> float:
    value = float(config.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


class WakeupReSpeakerSherpaProvider(WakeupReSpeakerSerialProvider):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        model_dir = Path(str(config.get("sherpa_model_dir", ""))).expanduser()
        self._model_files = {
            "tokens": model_dir / "tokens.txt",
            "encoder": model_dir / "encoder-epoch-13-avg-2-chunk-16-left-64.onnx",
            "decoder": model_dir / "decoder-epoch-13-avg-2-chunk-16-left-64.onnx",
            "joiner": model_dir / "joiner-epoch-13-avg-2-chunk-16-left-64.onnx",
        }
        self._keywords_file = Path(str(config.get("sherpa_keywords_file", ""))).expanduser()
        self._threshold = _positive(config, "sherpa_keywords_threshold", 0.25)
        self._num_threads = int(config.get("sherpa_num_threads", 1))
        self._doa_window_sec = _positive(config, "doa_window_sec", 2.0)
        self._dedupe_sec = _positive(config, "dedupe_sec", 1.5)
        # Bounded backlog: if decoding falls behind, old standby audio is dropped.
        self._chunks: deque[tuple[float, np.ndarray]] = deque(
            maxlen=int(_positive(config, "sherpa_backlog_sec", 2.0) / _CHUNK_SEC))
        self._keyword_ids: dict[str, int] = {}
        self._spotter: Any = None
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=8)
        self._last_events: dict[str, float] = {}

    def start(self) -> None:
        super().start()
        try:
            from sherpa_onnx import KeywordSpotter

            missing = [str(path) for path in (*self._model_files.values(), self._keywords_file)
                       if not path.is_file()]
            if missing:
                raise FileNotFoundError("sherpa wake files not found: " + ", ".join(missing))
            self._keyword_ids = {}
            for line in self._keywords_file.read_text(encoding="utf-8").splitlines():
                if "@" in line and not line.lstrip().startswith("#"):
                    self._keyword_ids.setdefault(line.rsplit("@", 1)[1].strip(), len(self._keyword_ids) + 1)
            self._spotter = KeywordSpotter(
                **{name: str(path) for name, path in self._model_files.items()},
                keywords_file=str(self._keywords_file), num_threads=self._num_threads,
                sample_rate=_SAMPLE_RATE, keywords_threshold=self._threshold,
            )
        except Exception as exc:
            self._spotter = None
            logger.warning("Sherpa wake spotter unavailable, Flex wakes only: %s", exc)
            return
        self._halt.clear()
        self._thread = threading.Thread(target=self._run, name="sherpa-wake", daemon=True)
        self._thread.start()
        logger.info("Sherpa wake spotter started keywords=%s threshold=%.2f",
                    list(self._keyword_ids), self._threshold)

    def stop(self) -> None:
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        super().stop()

    def attach_audio(self, audio: BaseProvider | None) -> None:
        if self._spotter is None:
            return
        attach = getattr(audio, "set_standby_listener", None)
        if not callable(attach) or not attach(self._on_audio):
            logger.warning("Sherpa wake spotter has no standby microphone "
                           "(needs audio.wake_history_sec > 0); Flex wakes only")

    def _on_audio(self, captured_at: float, samples: np.ndarray) -> None:
        self._chunks.append((captured_at, samples))

    def _run(self) -> None:
        stream = self._spotter.create_stream()
        while not self._halt.is_set():
            try:
                captured_at, samples = self._chunks.popleft()
            except IndexError:
                self._halt.wait(_CHUNK_SEC)
                continue
            stream.accept_waveform(_SAMPLE_RATE, samples)
            while self._spotter.is_ready(stream):
                self._spotter.decode_stream(stream)
                word = str(self._spotter.get_result(stream)).strip()
                if word:
                    self._spotter.reset_stream(stream)
                    self._events.append(self._keyword_event(word, captured_at))

    def _keyword_event(self, word: str, heard_at: float) -> dict[str, Any]:
        doa = self.reader.recent_doa(heard_at - self._doa_window_sec, heard_at)
        age_ms = -1.0 if doa is None else (heard_at - doa[0]) * 1000.0
        event = {
            "header": {"frame_id": WAKE_ANGLE_FRAME_ID},
            "event_type": EVT_VOICE_CALL_HERE if word.strip() in CALL_HERE_KEYWORDS else "wakeup",
            "wake_provider": "respeaker_sherpa",
            "wake_keyword_id": self._keyword_ids.get(word, 1),
            "wake_word": word,
            "wake_confidence": 1.0,
            "wake_score_raw": 1.0,
            "wake_threshold": self._threshold,
            "wake_min_score": self._threshold,
            "wake_angle": -1.0 if doa is None else doa[1],
            "wake_angle_valid": doa is not None,
            "wake_angle_age_ms": age_ms,
            "wake_audio_age_ms": -1.0,
            "wake_angle_source": "none" if doa is None else doa[2],
            "received_monotonic": heard_at,
            "latency_ms": max(0.0, (time.monotonic() - heard_at) * 1000.0),
        }
        kind = "call-here" if event["event_type"] == EVT_VOICE_CALL_HERE else "wakeup"
        logger.info("Sherpa %s: word=%r angle=%.1f valid=%s age_ms=%.1f",
                    kind, word, event["wake_angle"], event["wake_angle_valid"], age_ms)
        return event

    def _recover_bearing(self, event: dict[str, Any]) -> None:
        # The Flex bearing is often taken at the start of a long wake word and
        # is past max_angle_age_ms by the time the word is recognized.
        heard_at = event["received_monotonic"]
        doa = self.reader.recent_doa(heard_at - self._doa_window_sec, heard_at)
        if doa is None:
            return
        event.update(wake_angle=doa[1], wake_angle_valid=True,
                     wake_angle_age_ms=(heard_at - doa[0]) * 1000.0, wake_angle_source=doa[2])
        kind = "call-here" if event["event_type"] == EVT_VOICE_CALL_HERE else "wakeup"
        logger.info("Flex %s bearing taken from the word: angle=%.1f age_ms=%.1f source=%s",
                    kind, doa[1], event["wake_angle_age_ms"], doa[2])

    def poll_event(self) -> dict[str, Any] | None:
        while True:
            event = super().poll_event()
            from_device = event is not None
            if event is None:
                try:
                    event = self._events.popleft()
                except IndexError:
                    return None
            # Do not replace an invalid device keyword bearing with historical DOA.
            # The node owns acceptance and debounce, including the awake-state gate.
            if event["event_type"] == EVT_VOICE_CALL_HERE:
                return event
            if from_device and not event["wake_angle_valid"]:
                self._recover_bearing(event)
            heard_at = event["received_monotonic"]
            # A call-here notification must not consume the real wake's window.
            event_type = event["event_type"]
            last_heard = self._last_events.get(event_type, -math.inf)
            if abs(heard_at - last_heard) < self._dedupe_sec:
                logger.info("Drop duplicate %s %s %.2fs after the previous one",
                            event["wake_provider"], event_type, heard_at - last_heard)
                continue
            self._last_events[event_type] = heard_at
            return event
