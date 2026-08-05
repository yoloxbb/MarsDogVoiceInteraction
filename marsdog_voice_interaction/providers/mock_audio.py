"""Mock audio (VAD) provider — streaming-compatible with real pipeline.

Provides voice activity detection with the same streaming interface as
AudioSherpaProvider (start_capture → poll_result → is_capturing).

Real implementation: sherpa-onnx VAD (silero_vad.onnx).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class MockAudioProvider(BaseProvider):
    """Mock VAD provider — streaming-compatible with AudioSherpaProvider.

    Interface matches the real provider:
      - start_capture(): begin background listening
      - poll_result():   non-blocking check for completed speech segment
      - is_capturing():  True while capture is running or result waiting

    Mock behavior:
      After start_capture(), completes one synthetic utterance on a fixed
      event cadence when mock_event_interval_sec is configured.
      Returns audio_samples (np.ndarray float32) matching the real provider.

    Attributes:
        _capture_duration_sec: Simulated capture duration.
        _sample_rate: Audio sample rate.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._capture_duration_sec = float(config.get("capture_duration_sec", 2.0))
        self._sample_rate = int(config.get("sample_rate", 16000))
        self._num_samples = int(self._capture_duration_sec * self._sample_rate)
        self._event_interval_sec = float(
            config.get("mock_event_interval_sec", 0.0),
        )
        # Random delay before each utterance starts (simulates thinking/silence)
        self._min_delay_sec = float(config.get("min_inter_utterance_delay_sec", 3.0))
        self._max_delay_sec = float(config.get("max_inter_utterance_delay_sec", 8.0))

        # Streaming state
        self._capture_thread: threading.Thread | None = None
        self._capture_result: dict[str, Any] | None = None
        self._capture_lock = threading.Lock()
        self._capturing = False
        self._capture_cancel_event: threading.Event | None = None

    def start(self) -> None:
        try:
            if self._event_interval_sec > 0:
                logger.info(
                    "MockAudioProvider starting — capture=%.1fs, "
                    "fixed_event_interval=%.1fs, sr=%d",
                    self._capture_duration_sec,
                    self._event_interval_sec,
                    self._sample_rate,
                )
            else:
                logger.info(
                    "MockAudioProvider starting — capture=%.1fs, "
                    "random_delay=[%.0f-%.0fs], sr=%d",
                    self._capture_duration_sec,
                    self._min_delay_sec,
                    self._max_delay_sec,
                    self._sample_rate,
                )
            self.available = True
            logger.info("MockAudioProvider started (streaming mock)")
        except Exception as exc:
            self.available = False
            logger.warning(
                "MockAudioProvider start failed: %s", exc, exc_info=True,
            )

    def stop(self) -> None:
        self.cancel_capture()
        self.available = False
        logger.info("MockAudioProvider stopped")

    # ── Streaming interface (matches AudioSherpaProvider) ──────────

    def start_capture(self) -> None:
        """Start background capture (non-blocking).

        Simulates microphone capture — after ~2s, produces a synthetic
        audio buffer with has_voice=True.
        """
        if not self.available:
            return
        if self._capturing:
            return
        if (
            self._capture_thread is not None
            and self._capture_thread.is_alive()
        ):
            return

        cancel_event = threading.Event()
        with self._capture_lock:
            self._capturing = True
            self._capture_result = None
            self._capture_cancel_event = cancel_event

        self._capture_thread = threading.Thread(
            target=self._capture_thread_fn,
            args=(cancel_event,),
            name="mock-vad-capture",
            daemon=True,
        )
        self._capture_thread.start()
        logger.debug("MockAudio: capture started (streaming)")

    def cancel_capture(self, timeout: float = 2.0) -> bool:
        """Cancel the synthetic capture and discard its pending result."""
        with self._capture_lock:
            cancel_event = self._capture_cancel_event
            thread = self._capture_thread
            self._capturing = False
            if cancel_event is not None:
                cancel_event.set()

        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=max(0.0, timeout))

        worker_stopped = thread is None or not thread.is_alive()
        with self._capture_lock:
            self._capture_result = None
            if self._capture_thread is thread and worker_stopped:
                self._capture_thread = None
                self._capture_cancel_event = None
        return worker_stopped

    def poll_result(self) -> dict[str, Any] | None:
        """Non-blocking poll for capture result.

        Returns:
            Dict matching AudioSherpaProvider format:
              {"audio_samples": np.ndarray(float32), "sample_rate": int,
               "duration_ms": float, "has_voice": bool}
            None if capture still in progress.
        """
        with self._capture_lock:
            result = self._capture_result
            if result is not None:
                self._capture_result = None
                self._capturing = False
            return result

    def is_capturing(self) -> bool:
        """Check if a capture is currently in progress."""
        if self._capturing:
            return True
        with self._capture_lock:
            return self._capture_result is not None

    # ── Backward-compatible blocking API ──────────────────────────

    def capture(self) -> dict[str, Any] | None:
        """Blocking capture (legacy API).

        Returns same dict format as poll_result().
        """
        if not self.available:
            return None

        import time
        time.sleep(self._capture_duration_sec)
        duration_ms = self._capture_duration_sec * 1000
        samples = np.zeros(self._num_samples, dtype=np.float32)

        logger.debug("MockAudio: capture complete (%.1fs, mock)", self._capture_duration_sec)
        return {
            "audio_samples": samples,
            "sample_rate": self._sample_rate,
            "duration_ms": duration_ms,
            "has_voice": True,
        }

    def has_voice(self, audio_data: dict[str, Any] | None = None) -> bool:
        """Check if audio segment contains voice activity."""
        if audio_data is not None:
            return audio_data.get("has_voice", False)
        return self.available

    # ── Internal ──────────────────────────────────────────────────

    def _delay_before_capture(self) -> float:
        """Return the silence delay that preserves the configured cadence."""
        if self._event_interval_sec > 0:
            return max(
                0.0,
                self._event_interval_sec - self._capture_duration_sec,
            )
        import random
        return random.uniform(self._min_delay_sec, self._max_delay_sec)

    def _capture_thread_fn(
        self,
        cancel_event: threading.Event,
    ) -> None:
        """Background thread: cadence delay → simulated speech."""
        try:
            # Delay plus synthetic capture equals the configured event cadence.
            delay = self._delay_before_capture()
            logger.debug("MockAudio: pausing %.1fs before next utterance", delay)
            if cancel_event.wait(delay):
                return
            # Simulate actual speech duration
            if cancel_event.wait(self._capture_duration_sec):
                return

            # Generate synthetic audio (short sine burst as mock speech)
            t = np.linspace(0, self._capture_duration_sec, self._num_samples, dtype=np.float32)
            samples = (np.sin(2 * np.pi * 440 * t) * 0.01).astype(np.float32)

            result = {
                "audio_samples": samples,
                "sample_rate": self._sample_rate,
                "duration_ms": self._capture_duration_sec * 1000,
                "has_voice": True,
            }
            with self._capture_lock:
                if self._capture_cancel_event is cancel_event:
                    if not cancel_event.is_set():
                        self._capture_result = result
                    self._capturing = False
                    self._capture_thread = None
                    self._capture_cancel_event = None
        except Exception as exc:
            logger.error("MockAudio capture thread error: %s", exc)
            with self._capture_lock:
                if self._capture_cancel_event is cancel_event:
                    if not cancel_event.is_set():
                        self._capture_result = {
                            "audio_samples": np.array([], dtype=np.float32),
                            "sample_rate": self._sample_rate,
                            "duration_ms": 0.0,
                            "has_voice": False,
                        }
                    self._capturing = False
                    self._capture_thread = None
                    self._capture_cancel_event = None
        finally:
            with self._capture_lock:
                if self._capture_cancel_event is cancel_event:
                    self._capturing = False
                    self._capture_thread = None
                    self._capture_cancel_event = None
