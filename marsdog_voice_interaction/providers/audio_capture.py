"""Bounded microphone buffering independent of utterance processing."""

from __future__ import annotations

import logging
import os
import queue
import select
import subprocess
import threading
import time
from collections import deque
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def read_pipe_chunk(pipe: Any, size: int, timeout: float = 0.02) -> bytes | None:
    """Read available pipe bytes without waiting for a full buffered read."""
    if not select.select([pipe.fileno()], [], [], timeout)[0]:
        return None
    return os.read(pipe.fileno(), size)


class AudioCaptureGap(RuntimeError):
    """Samples were dropped; the current utterance must not be recognized."""


class BufferedAudioCapture:
    """Keep the device open across ASR calls; never deliver unbounded backlog."""

    def __init__(self, sample_rate: int, device: Any, sounddevice: Any,
                 buffer_sec: float, read_timeout_sec: float,
                 history_sec: float = 0.0) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self.sounddevice = sounddevice
        self.chunk_samples = int(sample_rate * 0.02)
        self.buffer_sec = buffer_sec
        self.read_timeout_sec = read_timeout_sec
        self.history_sec = max(0.0, float(history_sec))
        self._queue: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(
            maxsize=max(1, int(buffer_sec / 0.02)),
        )
        self._stop = threading.Event()
        self._gap = threading.Event()
        self._error: Exception | None = None
        self._history: deque[tuple[float, np.ndarray]] = deque()
        self._history_lock = threading.Lock()
        self._delivery_active = self.history_sec <= 0.0
        self._deliver_after = 0.0
        self._thread = threading.Thread(target=self._run, name="voice-microphone", daemon=True)

    def start(self) -> None:
        self._thread.start()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def delivery_active(self) -> bool:
        with self._history_lock:
            return self._delivery_active

    def close(self, timeout: float = 2.0) -> bool:
        self._stop.set()
        self._thread.join(timeout=max(0.0, timeout))
        self._discard()
        return not self._thread.is_alive()

    def _discard(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _push(self, samples: np.ndarray) -> None:
        if self._stop.is_set():
            return
        captured_at = time.monotonic()
        with self._history_lock:
            if self.history_sec > 0.0:
                self._history.append((captured_at, samples.copy()))
                cutoff = captured_at - self.history_sec
                while self._history and self._history[0][0] < cutoff:
                    self._history.popleft()
            if self._delivery_active and captured_at >= self._deliver_after:
                try:
                    self._queue.put_nowait((captured_at, samples.copy()))
                except queue.Full:
                    self._gap.set()
                    self._discard()

    def snapshot(self, end_monotonic: float, duration_sec: float) -> np.ndarray:
        """Copy bounded microphone history ending at a hardware wake event."""
        start = end_monotonic - max(0.0, duration_sec)
        with self._history_lock:
            chunks = [samples.copy() for captured_at, samples in self._history
                      if start <= captured_at <= end_monotonic]
        return (np.concatenate(chunks) if chunks
                else np.array([], dtype=np.float32))

    def start_delivery(self, after_monotonic: float) -> None:
        """Start a fresh utterance after a wake without replaying its audio."""
        with self._history_lock:
            self._delivery_active = False
            self._discard()
            self._gap.clear()
            for captured_at, samples in self._history:
                if captured_at >= after_monotonic:
                    try:
                        self._queue.put_nowait((captured_at, samples.copy()))
                    except queue.Full:
                        self._gap.set()
                        self._discard()
                        break
            self._delivery_active = True
            self._deliver_after = after_monotonic

    def stop_delivery(self) -> None:
        with self._history_lock:
            self._delivery_active = False
            self._discard()
            self._gap.clear()

    def read(self) -> np.ndarray | None:
        if self._gap.is_set():
            self._discard()
            self._gap.clear()
            raise AudioCaptureGap("microphone buffer overflow or input overrun")
        if self._error is not None:
            raise RuntimeError(f"microphone capture failed: {self._error}")
        try:
            captured_at, samples = self._queue.get(timeout=0.02)
        except queue.Empty:
            return None
        if time.monotonic() - captured_at > self.buffer_sec:
            self._discard()
            raise AudioCaptureGap("microphone buffered audio expired")
        return samples

    def _run(self) -> None:
        try:
            if self.sounddevice is not None:
                try:
                    self._run_sounddevice()
                    return
                except Exception as exc:
                    if self._stop.is_set():
                        return
                    logger.warning("Microphone backend failed, trying arecord: %s", exc)
                    self._gap.set()
                    self._discard()
            self._run_arecord()
        except Exception as exc:
            if not self._stop.is_set():
                self._error = exc
                logger.error("Buffered microphone stopped: %s", exc)

    def _run_sounddevice(self) -> None:
        stream = self.sounddevice.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32",
            device=self.device, blocksize=self.chunk_samples,
        )
        try:
            stream.start()
            last_read = time.monotonic()
            while not self._stop.is_set():
                if stream.read_available >= self.chunk_samples:
                    samples, overflowed = stream.read(self.chunk_samples)
                    if overflowed:
                        self._gap.set()
                    self._push(np.asarray(samples, dtype=np.float32).reshape(-1))
                    last_read = time.monotonic()
                elif time.monotonic() - last_read >= self.read_timeout_sec:
                    raise TimeoutError("sounddevice produced no audio")
                else:
                    self._stop.wait(0.01)
        finally:
            stream.close()

    def _run_arecord(self) -> None:
        command = ["arecord", "-f", "S16_LE", "-r", str(self.sample_rate),
                   "-c", "1", "-t", "raw"]
        if self.device is not None:
            command.extend(["-D", str(self.device)])
        # Inherit stderr so an unread error pipe cannot block the recorder.
        process = subprocess.Popen(command, stdout=subprocess.PIPE)
        pending = bytearray()
        try:
            last_read = time.monotonic()
            while not self._stop.is_set():
                raw = read_pipe_chunk(process.stdout, self.chunk_samples * 2)
                if raw == b"":
                    raise RuntimeError(f"arecord exited: {process.poll()}")
                if raw is None:
                    if time.monotonic() - last_read >= self.read_timeout_sec:
                        raise TimeoutError("arecord produced no audio")
                    continue
                last_read = time.monotonic()
                pending.extend(raw)
                while len(pending) >= self.chunk_samples * 2:
                    payload = bytes(pending[:self.chunk_samples * 2])
                    del pending[:self.chunk_samples * 2]
                    self._push(np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=0.5)
