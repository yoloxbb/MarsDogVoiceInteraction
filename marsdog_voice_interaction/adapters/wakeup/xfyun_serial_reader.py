"""XFYun / AIUI serial reader for framed AIUI and legacy JSON streams.

Validates AIUI binary frames, acknowledges requests and decodes compressed or
uncompressed JSON payloads. Legacy plain JSON transport is also supported.

Usage:
    reader = XFYunSerialReader(port="/dev/ttyACM0")
    msg = reader.get_message(block=False)  # returns dict or None
    reader.close()
"""

import codecs
import gzip
import io
import json
import logging
import struct
import threading
import time
import zlib
from collections import deque
from hashlib import sha256
from typing import Optional

try:
    import serial
except ImportError:
    serial = None

logger = logging.getLogger(__name__)


def _encode_frame(message_type: int, message_id: int, payload: bytes) -> bytes:
    """Encode the documented little-endian AIUI frame and checksum."""
    packet = b"\xa5\x01" + struct.pack("<BHH", message_type, len(payload), message_id) + payload
    return packet + bytes([(-sum(packet)) & 0xFF])


class _FrameDecoder:
    """Retain partial frames and resynchronize after noise or bad checksums."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, int, bytes]]:
        self.buffer.extend(data)
        frames = []
        while self.buffer:
            start = self.buffer.find(b"\xa5\x01")
            if start < 0:
                # A trailing sync byte may be the start of the next chunk.
                self.buffer[:] = b"\xa5" if self.buffer[-1] == 0xA5 else b""
                break
            del self.buffer[:start]
            if len(self.buffer) < 7:
                break
            message_type, length, message_id = struct.unpack_from("<BHH", self.buffer, 2)
            frame_size = 8 + length
            if len(self.buffer) < frame_size:
                break
            packet = bytes(self.buffer[:frame_size])
            if sum(packet) & 0xFF:
                logger.debug("Discarding AIUI frame with invalid checksum")
                del self.buffer[0]
                continue
            del self.buffer[:frame_size]
            frames.append((message_type, message_id, packet[7:-1]))
        return frames


class XFYunSerialReader:
    """Serial reader for XFYun/AIUI voice module over USB-serial.

    Runs a daemon thread that:
    - Continuously reads raw bytes from the serial port
    - Acknowledges valid AIUI requests and decodes GZIP or plain JSON payloads
    - Accepts legacy JSON without sending binary responses to that transport
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
        protocol: str = "auto",
    ):
        if serial is None:
            raise RuntimeError("pyserial is required. Install with: pip install pyserial")

        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._max_buffer_size = max_buffer_size
        self._keep_buffer_size = keep_buffer_size
        if protocol not in {"auto", "aiui", "json"}:
            raise ValueError("serial protocol must be auto, aiui or json")
        self._protocol = protocol
        self._reset_transport()

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
            write_timeout=max(0.05, self._timeout),
        )
        logger.info(f"Serial {self._port} opened @ {self._baudrate} baud")

        self._last_error = None
        self._reset_transport()
        self._buffer = ""
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

    def _reset_transport(self) -> None:
        self._active_protocol = None if self._protocol == "auto" else self._protocol
        self._protocol_buffer = bytearray()
        self._frame_decoder = _FrameDecoder()
        self._text_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._recent_messages: deque[tuple[int, bytes]] = deque(maxlen=200)
        self._handshake_seen = False

    def _process_data(self, data: bytes) -> None:
        if self._active_protocol is None:
            self._protocol_buffer.extend(data)
            binary_start = self._protocol_buffer.find(b"\xa5\x01")
            json_start = self._protocol_buffer.find(b"{")
            if binary_start >= 0 and (json_start < 0 or binary_start < json_start):
                self._active_protocol = "aiui"
                start = binary_start
            elif json_start >= 0:
                self._active_protocol = "json"
                start = json_start
            else:
                self._protocol_buffer[:] = self._protocol_buffer[-1:]
                return
            data = bytes(self._protocol_buffer[start:])
            self._protocol_buffer.clear()
            logger.info("Serial transport detected protocol=%s port=%s",
                        self._active_protocol, self._port)

        if self._active_protocol == "aiui":
            for message_type, message_id, payload in self._frame_decoder.feed(data):
                self._handle_frame(message_type, message_id, payload)
            return

        self._buffer += self._text_decoder.decode(data)
        if len(self._buffer) > self._max_buffer_size:
            self._buffer = self._buffer[-self._keep_buffer_size:]
        while True:
            msg, end_idx = self._extract_json(self._buffer)
            if end_idx is None:
                break
            self._buffer = self._buffer[end_idx:]
            if msg is not None:
                self._handle_message(msg)

    def _handle_frame(self, message_type: int, message_id: int, payload: bytes) -> None:
        if message_type == 0xFF:
            return  # A confirmation must never be acknowledged again.
        assert self._ser is not None
        ack = _encode_frame(0xFF, message_id, b"\xa5\x00\x00\x00")
        if self._ser.write(ack) != len(ack):
            raise serial.SerialException("Incomplete AIUI acknowledgement write")
        if message_type == 0x01:
            if not self._handshake_seen:
                logger.info("AIUI handshake acknowledged port=%s message_id=%d",
                            self._port, message_id)
                self._handshake_seen = True
            return
        if message_type != 0x04:
            return
        key = (message_id, sha256(payload).digest())
        if key in self._recent_messages:
            return  # Still ACK retransmissions, but do not publish them twice.
        try:
            if payload.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                    decoded = compressed.read(1024 * 1024 + 1)
            else:
                # This board firmware sends uncompressed UTF-8 JSON inside
                # otherwise standard, checksummed AIUI type-0x04 frames.
                decoded = payload
            if len(decoded) > 1024 * 1024:
                raise ValueError("AIUI decompressed message exceeds 1 MiB")
            msg = json.loads(decoded.decode("utf-8"))
        except (OSError, EOFError, UnicodeError, ValueError, zlib.error) as exc:
            logger.warning("Discarding invalid AIUI payload message_id=%d: %s", message_id, exc)
            return
        self._recent_messages.append(key)
        if isinstance(msg, dict):
            self._handle_message(msg)

    @staticmethod
    def _extract_json(text: str) -> tuple[Optional[dict], Optional[int]]:
        """Find the first complete JSON object in text.

        Returns (dict, end_index) or (None, None).
        Tracks quotes and escapes so braces inside strings do not split JSON.
        """
        start = text.find("{")
        if start == -1:
            return None, None

        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
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
                    return None, i + 1

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

                self._process_data(data)

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
            stamped = dict(msg)
            stamped["_received_monotonic"] = time.monotonic()
            if len(self._message_queue) >= 128:
                self._message_queue.pop(0)
            self._message_queue.append(stamped)
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
            self._reset_transport()
            self._message_queue.clear()
            self._message_event.clear()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_error(self) -> str | None:
        return self._last_error
