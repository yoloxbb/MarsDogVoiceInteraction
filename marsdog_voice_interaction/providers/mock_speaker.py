"""Mock speaker provider for Phase 1.

Speaker identification from raw audio segments. Participates in the
wakeup → VAD → ASR+Speaker pipeline, and also serves enroll/verify
via the /perception/perception_task service.

Real implementation (Phase 3): sherpa-onnx speaker (3dspeaker campplus model).
"""

from __future__ import annotations

import logging
from typing import Any

from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class MockSpeakerProvider(BaseProvider):
    """Mock speaker provider for identification, verification, and enrollment.

    Pipeline role:
      audio_data → verify(audio_data) → {speaker_id, confidence}
      Result merged into the speech interaction_event.

    Service role:
      /perception/perception_task → enroll_speaker / verify_speaker
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._match_threshold = float(config.get("match_threshold", 0.5))
        # In-memory mock embedding store
        self._enrolled: dict[str, Any] = {}

    def start(self) -> None:
        try:
            logger.info(
                "MockSpeakerProvider starting — threshold=%.2f",
                self._match_threshold,
            )
            self.available = True
            logger.info("MockSpeakerProvider started (mock)")
        except Exception as exc:
            self.available = False
            logger.warning(
                "MockSpeakerProvider start failed: %s", exc, exc_info=True,
            )

    def stop(self) -> None:
        self.available = False
        self._enrolled.clear()
        logger.info("MockSpeakerProvider stopped")

    # ── Pipeline interface ──────────────────────────────────────

    def verify(self, audio_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Identify speaker from raw audio.

        Called by the bridge node after VAD captures an audio segment.
        Runs in parallel with ASR transcription.

        Args:
            audio_data: Audio dict from VAD provider.

        Returns:
            Dict with speaker_id, confidence, and matched flag.
        """
        _ = audio_data

        if not self.available:
            return {"speaker_id": "unknown", "confidence": 0.0, "matched": False}

        # Mock: always returns "unknown" unless enroll was called
        speaker_id = "unknown"
        confidence = 0.0
        matched = False

        # If any speaker is enrolled, return the first one as a mock match
        if self._enrolled:
            speaker_id = next(iter(self._enrolled))
            confidence = 0.85
            matched = True

        logger.debug(
            "MockSpeakerProvider verify → id=%s conf=%.2f matched=%s",
            speaker_id, confidence, matched,
        )
        return {
            "speaker_id": speaker_id,
            "confidence": confidence,
            "matched": matched,
        }

    # ── Service interface ────────────────────────────────────────

    def enroll(self, audio_data: dict[str, Any] | None = None,
               speaker_id: str | None = None) -> dict[str, Any]:
        """Enroll a new speaker from raw audio.

        Called by /perception/perception_task (task_type=enroll_speaker).

        Args:
            audio_data: Audio dict from VAD provider.
            speaker_id: Optional speaker label.

        Returns:
            Dict with success, speaker_id.
        """
        _ = audio_data
        sid = speaker_id or f"speaker_{len(self._enrolled) + 1:03d}"

        self._enrolled[sid] = {
            "speaker_id": sid,
            "enrolled_at": __import__("time").time(),
        }

        logger.info("MockSpeakerProvider enrolled: %s (total: %d)", sid, len(self._enrolled))
        return {"success": True, "speaker_id": sid}

    def verify_speaker(self, audio_data: dict[str, Any] | None = None,
                       speaker_id: str | None = None) -> dict[str, Any]:
        """Verify a specific speaker against enrolled embedding.

        Args:
            audio_data: Audio dict from VAD provider.
            speaker_id: Speaker label to verify against.

        Returns:
            Dict with matched (bool) and confidence (float).
        """
        _ = audio_data

        if not self.available:
            return {"matched": False, "confidence": 0.0}

        if speaker_id is None or speaker_id not in self._enrolled:
            return {"matched": False, "confidence": 0.0}

        logger.info("MockSpeakerProvider verify_speaker: id=%s matched=True", speaker_id)
        return {"matched": True, "confidence": 0.90}
