"""XFYun / AIUI serial reader — daemon thread extracts JSON from serial stream.

Reads raw bytes from a USB serial port, extracts complete JSON objects,
and filters for aiui_event messages. Thread-safe, non-blocking consumer API.

Usage:
    reader = XFYunSerialReader(port="/dev/ttyACM0")
    msg = reader.get_message(block=False)  # returns dict or None
    reader.close()
"""

import json
import logging
import threading
import time
from typing import Optional

try:
    import serial
except ImportError:
    serial = None

logger = logging.getLogger(__name__)


class XFYunSerialReader:
    """Serial reader for XFYun/AIUI voice module over USB-serial.

    Runs a daemon thread that:
    - Continuously reads raw bytes from the serial port
    - Accumulates into an internal buffer
    - Extracts complete JSON objects using brace-depth tracking
    - Enqueues messages with type == "aiui_event"

    Thread-safety:
    - _reader_loop: writes to buffer and queue (lock-protected)
    - get_message(): reads from queue (lock-protected)
    """

    def __init__(
        self,
        port: str = "/dev/ttyACM0",
        baudrate: int = 115200,
        timeout: float = 0.2,
        max_buffer_size: int = 4096,
        keep_buffer_size: int = 2048,
    ):
        if serial is None:
            raise RuntimeError("pyserial is required. Install with: pip install pyserial")

        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._max_buffer_size = max_buffer_size
        self._keep_buffer_size = keep_buffer_size

        self._ser: Optional["serial.Serial"] = None
        self._running = False
        self._buffer = ""
        self._last_error: str | None = None

        # Thread-safe message queue
        self._message_event = threading.Event()
        self._message_queue: list[dict] = []
        self._lock = threading.Lock()

        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ────────────────────────────────────────────

    def open(self) -> None:
        """Open the serial port and start the reader thread."""
        if self._running:
            return

        self._ser = serial.Serial(
            port=self._port,
            baudrate=self._baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self._timeout,
        )
        logger.info(f"Serial {self._port} opened @ {self._baudrate} baud")

        self._last_error = None
        self._running = True
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="xfyun-serial-reader",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        """Stop the reader thread and close the serial port."""
        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._ser is not None and self._ser.is_open:
            self._ser.close()
            self._ser = None

        logger.info("Serial reader closed")

    # ── Message extraction ───────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> tuple[Optional[dict], Optional[int]]:
        """Find the first complete JSON object in text.

        Returns (dict, end_index) or (None, None).
        Uses brace-depth tracking — handles nested objects correctly.
        """
        start = text.find("{")
        if start == -1:
            return None, None

        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1

            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        return obj, i + 1
                    return None, i + 1
                except json.JSONDecodeError:
                    return None, None

        return None, None

    # ── Reader thread ────────────────────────────────────────

    def _reader_loop(self) -> None:
        """Main serial read loop (runs in daemon thread)."""
        while self._running:
            try:
                if self._ser is None or not self._ser.is_open:
                    time.sleep(0.05)
                    continue

                # Read available bytes (at least 1, to avoid busy-wait)
                data = self._ser.read(self._ser.in_waiting or 1)
                if not data:
                    time.sleep(0.01)
                    continue

                self._buffer += data.decode("utf-8", errors="ignore")

                # Prevent unbounded growth
                if len(self._buffer) > self._max_buffer_size:
                    self._buffer = self._buffer[-self._keep_buffer_size:]

                # Extract all complete JSON objects
                while True:
                    msg, end_idx = self._extract_json(self._buffer)
                    if msg is None:
                        break

                    self._buffer = self._buffer[end_idx:]
                    self._handle_message(msg)

                time.sleep(0.005)

            except serial.SerialException as e:
                logger.error(f"Serial error: {e}")
                self._running = False
                self._last_error = str(e)
                if self._ser is not None and self._ser.is_open:
                    try:
                        self._ser.close()
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Reader loop error: {e}", exc_info=True)
                time.sleep(0.05)

    def _handle_message(self, msg: dict) -> None:
        """Filter and enqueue an aiui_event message."""
        if not isinstance(msg, dict):
            return

        if msg.get("type") != "aiui_event":
            return

        with self._lock:
            self._message_queue.append(msg)
            self._message_event.set()

    # ── Consumer API (thread-safe) ───────────────────────────

    def has_message(self) -> bool:
        """Check if a message is available (non-blocking)."""
        with self._lock:
            return bool(self._message_queue)

    def get_message(
        self,
        block: bool = False,
        timeout: Optional[float] = None,
    ) -> Optional[dict]:
        """Get the next message from the queue.

        Args:
            block: If True, wait up to `timeout` seconds for a message.
            timeout: Max seconds to wait when blocking.

        Returns:
            The parsed dict, or None if no message available.
        """
        if block:
            ok = self._message_event.wait(timeout)
            if not ok:
                return None

        with self._lock:
            if self._message_queue:
                msg = self._message_queue.pop(0)
                if not self._message_queue:
                    self._message_event.clear()
                return msg

            self._message_event.clear()
            return None

    def reset(self) -> None:
        """Clear buffer and message queue."""
        with self._lock:
            self._buffer = ""
            self._message_queue.clear()
            self._message_event.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_error(self) -> str | None:
        return self._last_error
