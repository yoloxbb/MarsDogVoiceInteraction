"""XFYun serial wakeup provider.

Wraps XFYunSerialReader to poll aiui_event messages from the USB serial
port and convert them to MarsDog wakeup interaction events.

Serial message format:
{
  "type": "aiui_event",
  "content": {
    "eventType": 4,
    "info": "{\"ivw\":{\"keyword\":\"ni2 hao3 wang4 cai2\",\"score\":907.0,\"angle\":100.0,...}}",
    "result": "ni2 hao3 wang4 cai2",
    "arg1": 1, "arg2": 0
  }
}
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from marsdog_voice_interaction.adapters.wakeup.xfyun_serial_reader import XFYunSerialReader
from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class WakeupXFYunSerialProvider(BaseProvider):
    """Wakeup provider using XFYun/AIUI serial module.

    Opens the configured USB serial port, polls for aiui_event messages,
    filters by voice_wake_event_types, parses the nested info JSON,
    and converts to MarsDog wakeup events.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        self.port = config.get("port", "/dev/ttyACM0")
        self.baudrate = int(config.get("baudrate", 115200))
        self.timeout = float(config.get("timeout", 0.2))
        self.voice_wake_event_types = set(
            config.get("voice_wake_event_types", [4])
        )
        self._reconnect_interval_sec = max(
            0.0,
            float(config.get("reconnect_interval_sec", 2.0)),
        )
        self._next_reconnect_at = 0.0
        self._stopping = False

        self.reader: XFYunSerialReader | None = None

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        self._stopping = False
        self._next_reconnect_at = 0.0
        self._connect(initial=True)

    def stop(self) -> None:
        self._stopping = True
        self._close_reader()
        self.available = False
        logger.info("XFYun wakeup stopped")

    # ── Event polling ──────────────────────────────────────────

    def poll_event(self) -> dict[str, Any] | None:
        """Non-blocking poll for a wakeup event.

        Returns:
            Wakeup event partial dict with event_type, wake_word,
            wake_angle, wake_confidence, latency_ms.
            None if no event available.
        """
        if not self._ensure_connected():
            return None

        try:
            assert self.reader is not None
            raw_msg = self.reader.get_message(block=False)
            if raw_msg is None:
                return None
            return self._parse_event(raw_msg)

        except Exception as exc:
            logger.error("Poll error: %s", exc)
            self._mark_disconnected(str(exc))
            return None

    def _ensure_connected(self) -> bool:
        reader = self.reader
        if reader is not None and reader.is_running:
            self.available = True
            return True

        if reader is not None:
            reason = reader.last_error or "serial reader stopped"
            self._mark_disconnected(reason)

        if self._stopping or time.monotonic() < self._next_reconnect_at:
            return False
        return self._connect(initial=False)

    def _connect(self, *, initial: bool) -> bool:
        self._close_reader()
        try:
            reader = XFYunSerialReader(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
            )
            reader.open()
            self.reader = reader
            self.available = True
            self._next_reconnect_at = 0.0
            logger.info(
                "XFYun wakeup %s — port=%s baud=%d events=%s",
                "started" if initial else "reconnected",
                self.port,
                self.baudrate,
                self.voice_wake_event_types,
            )
            return True
        except Exception as exc:
            self.reader = None
            self.available = False
            self._next_reconnect_at = (
                time.monotonic() + self._reconnect_interval_sec
            )
            logger.warning(
                "XFYun wakeup unavailable on %s: %s — retrying in %.1fs",
                self.port,
                exc,
                self._reconnect_interval_sec,
            )
            return False

    def _mark_disconnected(self, reason: str) -> None:
        if self.reader is not None or self.available:
            logger.warning(
                "XFYun wakeup disconnected on %s: %s",
                self.port,
                reason,
            )
        self._close_reader()
        self.available = False
        self._next_reconnect_at = (
            time.monotonic() + self._reconnect_interval_sec
        )

    def _close_reader(self) -> None:
        reader = self.reader
        self.reader = None
        if reader is None:
            return
        try:
            reader.close()
        except Exception as exc:
            logger.warning("Error closing serial: %s", exc)

    # ── Message parsing ───────────────────────────────────────

    def _parse_event(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Parse a raw XFYun serial message into a MarsDog wakeup event.

        Message structure (from actual serial output):
          {
            "type": "aiui_event",
            "content": {
              "eventType": 4,                   ← wake type
              "info": "{...ivw...}",            ← JSON string
              "result": "ni2 hao3 wang4 cai2",  ← pinyin
              "arg1": 1,
              "arg2": 0
            }
          }

        The info field contains an ivw (intelligent voice wake) object:
          {"ivw": {"keyword": "ni2 hao3 wang4 cai2", "score": 907.0,
                   "angle": 100.0, "beam": 1, "start_ms": ..., "end_ms": ...}}
        """
        t0 = time.perf_counter()

        # ── Validate event type ─────────────────────────────
        content = raw.get("content", {})
        if not isinstance(content, dict):
            return None

        raw_event_type = content.get("eventType")
        if raw_event_type is None:
            return None

        try:
            event_type = int(raw_event_type)
        except (TypeError, ValueError):
            return None

        if event_type not in self.voice_wake_event_types:
            return None

        # ── Parse info JSON ─────────────────────────────────
        wake_word = ""
        wake_angle = 0.0
        wake_confidence = 1.0

        info_str = content.get("info", "")
        if info_str and isinstance(info_str, str):
            try:
                info = json.loads(info_str)
                ivw = info.get("ivw", {})
                if isinstance(ivw, dict):
                    wake_word = str(ivw.get("keyword", ""))
                    wake_confidence = float(ivw.get("score", 1.0))
                    wake_angle = float(ivw.get("angle", 0.0))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.debug("Failed to parse info JSON: %s", exc)

        # Fallback: use result field as wake_word
        if not wake_word:
            wake_word = str(content.get("result", ""))

        # Normalize: strip tone numbers for readability
        # "ni2 hao3 wang4 cai2" stays as-is for downstream matching

        latency_ms = (time.perf_counter() - t0) * 1000.0

        logger.info(
            "XFYun wakeup: word=%r angle=%.0f conf=%.1f (%.1fms)",
            wake_word, wake_angle, wake_confidence, latency_ms,
        )

        return {
            "event_type": "wakeup",
            "wake_word": wake_word,
            "wake_angle": wake_angle,
            "wake_confidence": wake_confidence,
            "latency_ms": latency_ms,
        }
