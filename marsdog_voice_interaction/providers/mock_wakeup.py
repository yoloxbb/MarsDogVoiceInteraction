"""Mock wakeup provider with configurable fixed or random intervals."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class MockWakeupProvider(BaseProvider):
    """Mock wakeup provider with a fixed or random interval.

    Unlike the real XFYun module which can fire at any time, the mock
    simulates the natural cadence of human-robot interaction:
      - A single interaction interval produces a fixed cadence
      - Legacy min/max settings retain randomized cadence support

    Attributes:
        _min_interval: Minimum seconds between wakeups.
        _max_interval: Maximum seconds between wakeups.
        _next_event_time: Time of next scheduled wakeup.
        _enabled: Whether to produce events at all.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._min_interval = float(config.get("mock_min_interval_sec", 45.0))
        self._max_interval = float(config.get("mock_max_interval_sec", 120.0))
        # A single interval is used by the unified mock event cadence.
        legacy = config.get("mock_interaction_interval_sec")
        if legacy is not None:
            self._min_interval = float(legacy)
            self._max_interval = float(legacy)
        self._enabled = bool(config.get("enable_mock_interaction", True))
        self._next_event_time = 0.0

    def start(self) -> None:
        try:
            self._schedule_next()
            self.available = True
            logger.info(
                "MockWakeupProvider started — interval=[%.0f-%.0fs], enabled=%s",
                self._min_interval, self._max_interval, self._enabled,
            )
        except Exception as exc:
            self.available = False
            logger.warning(
                "MockWakeupProvider start failed (unexpected): %s",
                exc, exc_info=True,
            )

    def stop(self) -> None:
        self.available = False
        logger.info("MockWakeupProvider stopped")

    def poll_event(self) -> dict[str, Any] | None:
        """Poll for a mock wakeup event.

        Returns an event when the random interval timer fires.
        After each event, schedules the next one at a new random delay.

        Setting config/mock/enabled: false globally stops all mock events.

        Returns:
            Wakeup event dict or None.
        """
        if not self._enabled:
            return None
        if not self.config.get("mock_enabled", True):
            return None

        now = time.perf_counter()
        if now < self._next_event_time:
            return None

        # Fire event
        delay = now - (self._next_event_time - self._last_interval)
        logger.info(
            "MockWakeupProvider: wakeup event (interval=%.0fs)", self._last_interval,
        )
        self._schedule_next()

        return {
            "event_type": "wakeup",
            "wake_word": "你好小狗",
            "wake_angle": 0.0,
            "wake_confidence": 1.0,
            "latency_ms": 0.0,
        }

    def _schedule_next(self) -> None:
        self._last_interval = random.uniform(self._min_interval, self._max_interval)
        self._next_event_time = time.perf_counter() + self._last_interval
