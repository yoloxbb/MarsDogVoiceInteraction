"""ReSpeaker Flex 0.5.x CDC JSON Lines transport, independent of ROS callbacks."""

from __future__ import annotations

from collections import deque
import json
import logging
import math
import threading
import time
from typing import Any

import serial
from serial.tools import list_ports

logger = logging.getLogger(__name__)


class JsonLineDecoder:
    """Bound memory and discard an entire oversized line, including its suffix."""

    def __init__(self, max_bytes: int = 4096) -> None:
        self.max_bytes = max_bytes
        self.pending = bytearray()
        self.discarding = False

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        messages = []
        for chunk in data.splitlines(keepends=True):
            # Only LF is a protocol delimiter; splitlines also recognises CR.
            ended = chunk.endswith(b"\n")
            if not self.discarding:
                self.pending.extend(chunk)
                if len(self.pending) > self.max_bytes:
                    self.pending.clear()
                    self.discarding = True
            if not ended:
                continue
            if not self.discarding:
                try:
                    value = json.loads(self.pending.decode("utf-8"))
                    if isinstance(value, dict):
                        messages.append(value)
                except (UnicodeDecodeError, ValueError, RecursionError):
                    pass
            self.pending.clear()
            self.discarding = False
        return messages


def _usb_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("USB identity must be an integer or a hexadecimal string")
    result = int(value, 0) if isinstance(value, str) else value
    if not 0 <= result <= 65535:
        raise ValueError("USB identity must be between 0 and 65535")
    return result


class ReSpeakerSerialReader:
    """One worker owns discovery, handshake, heartbeat and reconnection.

    Wake, keyword and VAD events use separate bounded queues. VAD/keyword traffic cannot
    starve wake delivery. The firmware has no replay or ACK protocol. We never modify
    its NVS word lists or recognition parameters.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.port = str(config.get("port") or "auto").strip()
        self.vid = _usb_id(config.get("usb_vid", 0x303A))
        self.pid = _usb_id(config.get("usb_pid", 0x4013))
        self.serial_number = str(config.get("serial_number") or "").strip()
        self.baudrate = int(config.get("baudrate", 115200))
        self.timeout = self._duration(config, "timeout", 0.2)
        self.settle_sec = self._duration(config, "settle_sec", 0.5)
        self.handshake_timeout_sec = self._duration(config, "handshake_timeout_sec", 2.0)
        self.heartbeat_sec = self._duration(config, "heartbeat_sec", 10.0)
        self.reconnect_interval_sec = self._duration(config, "reconnect_interval_sec", 2.0)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._messages: deque[dict[str, Any]] = deque(maxlen=128)
        self._keyword_messages: deque[dict[str, Any]] = deque(maxlen=128)
        self._vad_messages: deque[dict[str, Any]] = deque(maxlen=128)
        self.connection_id = 0
        # (measured_monotonic, doa, doa_source) from any message, for host-side keyword spotting.
        self._doas: deque[tuple[float, float, str]] = deque(maxlen=64)
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.resolved_port: str | None = None

    @staticmethod
    def _duration(config: dict[str, Any], name: str, default: float) -> float:
        value = float(config.get(name, default))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
        return value

    def resolve_port(self) -> str:
        if self.port.lower() != "auto":
            return self.port
        matches = [p for p in list_ports.comports()
                   if p.vid == self.vid and p.pid == self.pid
                   and (not self.serial_number or p.serial_number == self.serial_number)]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one Flex CDC {self.vid:04x}:{self.pid:04x} "
                f"serial={self.serial_number!r}; found {[p.device for p in matches]}"
            )
        return str(matches[0].device)

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def open(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="respeaker-cdc", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.timeout + 1.0)
        self._reset_session()

    def _reset_session(self) -> None:
        with self._lock:
            self._ready.clear()
            self._messages.clear()
            self._keyword_messages.clear()
            self._vad_messages.clear()
            self._doas.clear()
            self.connection_id += 1

    def _note_doa(self, message: dict[str, Any], received: float) -> None:
        angle, age = message.get("doa"), message.get("doa_age_ms")
        if (message.get("doa_valid") is not True
                or isinstance(angle, bool) or not isinstance(angle, (int, float))
                or isinstance(age, bool) or not isinstance(age, (int, float))):
            return
        try:
            angle, age = float(angle), float(age)
            if not (math.isfinite(angle) and 0 <= angle < 360 and math.isfinite(age) and age >= 0):
                return
            if message.get("type") in ("wake", "keyword"):
                # These bearings belong to an earlier recognition audio frame.
                audio_age = message.get("audio_age_ms")
                if isinstance(audio_age, bool) or not isinstance(audio_age, (int, float)):
                    return
                audio_age = float(audio_age)
                if not math.isfinite(audio_age) or audio_age < 0:
                    return
                age += audio_age
        except OverflowError:
            return
        measured = received - age / 1000.0
        if not math.isfinite(measured):
            return
        with self._lock:
            self._doas.append((measured, angle, str(message.get("doa_source", ""))))

    def recent_doa(self, since: float, until: float) -> tuple[float, float, str] | None:
        """Latest valid bearing measured in [since, until]; firmware "auto" estimates beat fixed beams."""
        with self._lock:
            hits = [doa for doa in self._doas if since <= doa[0] <= until]
        auto = [doa for doa in hits if doa[2] == "auto"]
        return max(auto or hits, key=lambda doa: doa[0], default=None)

    def get_message(self) -> dict[str, Any] | None:
        with self._lock:
            if not self.ready or not self._messages:
                return None
            return self._messages.popleft()

    def get_vad_message(self) -> dict[str, Any] | None:
        """Read activity boundaries/updates without consuming wake events."""
        with self._lock:
            if not self.ready or not self._vad_messages:
                return None
            return self._vad_messages.popleft()

    def get_keyword_message(self) -> dict[str, Any] | None:
        """Read firmware KWS notifications without consuming wake events."""
        with self._lock:
            if not self.ready or not self._keyword_messages:
                return None
            return self._keyword_messages.popleft()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.resolved_port = self.resolve_port()
                # Configure control lines before open; do not use AIUI frames.
                with serial.Serial(port=None, baudrate=self.baudrate,
                                   timeout=self.timeout, write_timeout=self.timeout,
                                   exclusive=True) as port:
                    port.dtr = True
                    port.rts = False
                    port.port = self.resolved_port
                    port.open()
                    if self._stop.wait(self.settle_sec):
                        return
                    port.reset_input_buffer()
                    self._read_connection(port)
            except (OSError, serial.SerialException, RuntimeError, ValueError) as exc:
                self.last_error = str(exc)
                if not self._stop.is_set():
                    logger.warning("Flex CDC unavailable: %s; retry in %.1fs",
                                   exc, self.reconnect_interval_sec)
            finally:
                self._reset_session()
            self._stop.wait(self.reconnect_interval_sec)

    def _read_connection(self, port: Any) -> None:
        decoder = JsonLineDecoder()
        port.write(b'{"cmd":"ping"}\n')
        pending_ping = time.monotonic()
        last_pong = pending_ping
        while not self._stop.is_set():
            raw = port.read(min(port.in_waiting or 1, 4096))
            received = time.monotonic()
            for message in decoder.feed(raw):
                kind = message.get("type")
                if self.ready:
                    self._note_doa(message, received)
                if kind == "pong" and pending_ping is not None:
                    pending_ping = None
                    last_pong = received
                    if not self.ready:
                        logger.info("Flex CDC ready port=%s (pong)", self.resolved_port)
                    self.last_error = None
                    self._ready.set()
                elif kind == "wake" and self.ready:
                    message["_received_monotonic"] = received
                    with self._lock:
                        self._messages.append(message)
                elif kind == "keyword" and self.ready:
                    message["_received_monotonic"] = received
                    with self._lock:
                        self._keyword_messages.append(message)
                elif kind == "vad" and self.ready:
                    message["_received_monotonic"] = received
                    message["_connection_id"] = self.connection_id
                    with self._lock:
                        self._vad_messages.append(message)
                elif kind == "error":
                    logger.warning("Flex CDC error: %s", message)
                else:
                    logger.debug("Flex CDC %s: %s", kind, message)
            if pending_ping is not None:
                if received - pending_ping >= self.handshake_timeout_sec:
                    raise RuntimeError("Flex ping timed out")
            elif received - last_pong >= self.heartbeat_sec:
                port.write(b'{"cmd":"ping"}\n')
                pending_ping = time.monotonic()
