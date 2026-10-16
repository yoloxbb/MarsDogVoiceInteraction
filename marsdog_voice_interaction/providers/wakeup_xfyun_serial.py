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
import math
import time
from typing import Any

from marsdog_voice_interaction.adapters.wakeup.xfyun_serial_reader import XFYunSerialReader
from marsdog_voice_interaction.messages.audio_event import WAKE_ANGLE_FRAME_ID
from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class WakeupXFYunSerialProvider(BaseProvider):
    """Wakeup provider using XFYun/AIUI serial module.

    Resolves the configured USB serial port, polls for aiui_event messages,
    filters by voice_wake_event_types, parses the nested info JSON,
    and converts to MarsDog wakeup events.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        self.port = str(config.get("port") or "auto").strip() or "auto"
        self.usb_vid = config.get("usb_vid", 0x1A86)
        self.usb_pid = config.get("usb_pid", 0x55D4)
        self.serial_number = str(config.get("serial_number") or "").strip()
        self._resolved_port = self.port
        self.baudrate = int(config.get("baudrate", 115200))
        self.timeout = float(config.get("timeout", 0.2))
        self.protocol = str(config.get("protocol", "auto"))
        self.wake_score_scale = float(config.get("wake_score_scale", 1000.0))
        if not math.isfinite(self.wake_score_scale) or self.wake_score_scale <= 0:
            self.wake_score_scale = 1000.0
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
            for _ in range(32):
                raw_msg = self.reader.get_message(block=False)
                if raw_msg is None:
                    return None
                event = self._parse_event(raw_msg)
                if event is not None:
                    return event
            return None

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
        self._resolved_port = self.port
        try:
            self._resolved_port = self._resolve_port()
            reader = XFYunSerialReader(
                port=self._resolved_port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                protocol=self.protocol,
            )
            self.reader = reader
            reader.open()
            self.available = True
            self._next_reconnect_at = 0.0
            logger.info(
                "XFYun wakeup %s — port=%s baud=%d events=%s",
                "started" if initial else "reconnected",
                self._resolved_port,
                self.baudrate,
                self.voice_wake_event_types,
            )
            return True
        except Exception as exc:
            self._close_reader()
            self.available = False
            self._next_reconnect_at = (
                time.monotonic() + self._reconnect_interval_sec
            )
            logger.warning(
                "XFYun wakeup unavailable on %s: %s — retrying in %.1fs",
                self._resolved_port,
                exc,
                self._reconnect_interval_sec,
            )
            return False

    @staticmethod
    def _usb_id(value: Any, name: str) -> int:
        """Accept YAML integers or strings such as '0x1a86'."""
        try:
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError
            parsed = int(value, 0) if isinstance(value, str) else value
            if not 0 <= parsed <= 0xFFFF:
                raise ValueError
            return parsed
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer from 0 to 65535") from exc

    def _resolve_port(self) -> str:
        """Resolve each connection attempt; never guess between USB devices.

        Explicit paths remain authoritative. Auto mode enumerates device
        identities without opening ports, including after a disconnect.
        """
        if self.port.lower() != "auto":
            return self.port

        from serial.tools import list_ports

        vid = self._usb_id(self.usb_vid, "usb_vid")
        pid = self._usb_id(self.usb_pid, "usb_pid")
        ports = sorted(list_ports.comports(), key=lambda port: port.device)
        matches = [
            port for port in ports
            if port.vid == vid and port.pid == pid
            and (not self.serial_number or port.serial_number == self.serial_number)
        ]
        selector = f"VID:PID={vid:04x}:{pid:04x} serial_number={self.serial_number!r}"
        if len(matches) != 1:
            details = "; ".join(
                f"{port.device} VID={port.vid!r} PID={port.pid!r} "
                f"serial_number={port.serial_number!r}"
                for port in (matches or ports)
            ) or "none"
            if matches:
                raise RuntimeError(
                    f"Multiple wakeup serial ports match {selector}: {details}. "
                    "Set serial_number or an explicit port to select one."
                )
            raise RuntimeError(
                f"No wakeup serial port matches {selector}; detected ports: {details}"
            )
        selected = matches[0]
        logger.info(
            "XFYun wakeup auto-selected port=%s VID:PID=%04x:%04x serial_number=%r",
            selected.device, vid, pid, selected.serial_number,
        )
        return str(selected.device)

    def _mark_disconnected(self, reason: str) -> None:
        if self.reader is not None or self.available:
            logger.warning(
                "XFYun wakeup disconnected on %s: %s",
                self._resolved_port,
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
        wake_score_raw = 1.0
        wake_duration_sec = 0.0

        info_str = content.get("info", "")
        if info_str and isinstance(info_str, str):
            try:
                info = json.loads(info_str)
                ivw = info.get("ivw", {})
                if isinstance(ivw, dict):
                    wake_word = str(ivw.get("keyword", ""))
                    wake_score_raw = float(ivw.get("score", 1.0))
                    wake_confidence = self._normalize_wake_score(
                        wake_score_raw
                    )
                    wake_angle = float(ivw.get("angle", 0.0))
                    start_ms = float(ivw.get("start_ms", 0.0))
                    end_ms = float(ivw.get("end_ms", 0.0))
                    if math.isfinite(start_ms) and math.isfinite(end_ms):
                        wake_duration_sec = max(0.0, (end_ms - start_ms) / 1000.0)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.debug("Failed to parse info JSON: %s", exc)

        # Fallback: use result field as wake_word
        if not wake_word:
            wake_word = str(content.get("result", ""))
        if not math.isfinite(wake_angle):
            wake_angle = 0.0

        # Normalize: strip tone numbers for readability
        # "ni2 hao3 wang4 cai2" stays as-is for downstream matching

        latency_ms = (time.perf_counter() - t0) * 1000.0

        logger.info(
            "XFYun wakeup: word=%r angle=%.0f conf=%.3f raw=%.1f (%.1fms)",
            wake_word,
            wake_angle,
            wake_confidence,
            wake_score_raw,
            latency_ms,
        )

        return {
            "header": {"frame_id": WAKE_ANGLE_FRAME_ID},
            "event_type": "wakeup",
            "wake_word": wake_word,
            "wake_angle": wake_angle,
            "wake_confidence": wake_confidence,
            "wake_score_raw": wake_score_raw,
            "wake_duration_sec": wake_duration_sec,
            "received_monotonic": float(
                raw.get("_received_monotonic", time.monotonic())
            ),
            "latency_ms": latency_ms,
        }

    def _normalize_wake_score(self, score: float) -> float:
        """Convert the AIUI raw score to the public [0, 1] confidence."""
        if not math.isfinite(score):
            return 0.0
        normalized = score if score <= 1.0 else score / self.wake_score_scale
        return min(1.0, max(0.0, normalized))
