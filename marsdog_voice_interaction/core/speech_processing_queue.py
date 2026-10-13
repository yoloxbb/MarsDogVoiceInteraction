from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import math
import time
from typing import Any, Callable


@dataclass(frozen=True)
class SpeechProcessingResult:
    payload: Any
    result: Any = None
    discard_reason: str = ""


class SpeechProcessingQueue:
    """串行执行整句识别，限制等待音频的时长和保留时间。"""

    def __init__(self, processor: Callable[[Any], Any], *, max_buffer_sec: float):
        if not math.isfinite(max_buffer_sec) or max_buffer_sec <= 0:
            raise ValueError("max_buffer_sec 必须是有限正数")
        self._processor = processor
        self._max_buffer_sec = max_buffer_sec
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech-processing")
        self._running: tuple[Any, Future[Any]] | None = None
        self._running_cancelled = False
        self._pending: deque[tuple[Any, float, float]] = deque()
        self._pending_duration_sec = 0.0
        self._discarded: deque[SpeechProcessingResult] = deque()

    @property
    def pending_payloads(self) -> tuple[Any, ...]:
        running = (
            (self._running[0],)
            if self._running is not None and not self._running_cancelled else ()
        )
        return running + tuple(payload for payload, _, _ in self._pending)

    def submit(self, payload: Any, *, duration_sec: float) -> bool:
        if not math.isfinite(duration_sec) or duration_sec <= 0:
            raise ValueError("duration_sec 必须是有限正数")
        if self._running is None:
            self._running = (payload, self._executor.submit(self._processor, payload))
            return True
        self._discard_expired()
        if self._pending_duration_sec + duration_sec > self._max_buffer_sec:
            return False
        self._pending.append((payload, time.monotonic(), duration_sec))
        self._pending_duration_sec += duration_sec
        return True

    def poll(self) -> SpeechProcessingResult | None:
        self._discard_expired()
        if self._discarded:
            return self._discarded.popleft()
        if self._running is None or not self._running[1].done():
            return None
        payload, future = self._running
        result = None if future.cancelled() else future.result()
        reason = "cancelled" if self._running_cancelled or future.cancelled() else ""
        self._running = None
        self._running_cancelled = False
        self._start_next()
        return SpeechProcessingResult(payload, result, reason)

    def _discard_expired(self) -> None:
        now = time.monotonic()
        while self._pending and now - self._pending[0][1] > self._max_buffer_sec:
            payload, _, duration_sec = self._pending.popleft()
            self._pending_duration_sec = max(0.0, self._pending_duration_sec - duration_sec)
            self._discarded.append(SpeechProcessingResult(payload, discard_reason="expired"))

    def _start_next(self) -> None:
        self._discard_expired()
        while self._pending:
            payload, _, duration_sec = self._pending.popleft()
            self._pending_duration_sec = max(0.0, self._pending_duration_sec - duration_sec)
            self._running = (payload, self._executor.submit(self._processor, payload))
            return

    def clear(self) -> None:
        self._pending.clear()
        self._pending_duration_sec = 0.0
        self._discarded.clear()
        if self._running is not None:
            self._running_cancelled = True
            self._running[1].cancel()

    def close(self) -> None:
        self.clear()
        self._executor.shutdown(wait=True, cancel_futures=True)
