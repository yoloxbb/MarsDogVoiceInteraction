"""Speaker provider using sherpa-onnx SpeakerEmbeddingExtractor.

Extracts speaker embeddings from raw audio and matches against
enrolled speakers. Used in both the wakeup→VAD→Speaker pipeline
and the /perception/perception_task service (enroll/verify).

Requires: sherpa-onnx
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class SpeakerSherpaProvider(BaseProvider):
    """Speaker identification using sherpa-onnx campplus model.

    Pipeline role:
      audio_data → extract embedding → search enrolled → {speaker_id, confidence}

    Service role:
      /perception/perception_task → enroll / verify

    Attributes:
        _extractor: SpeakerEmbeddingExtractor instance.
        _manager: SpeakerEmbeddingManager instance.
        _match_threshold: Similarity threshold for matching.
        _num_threads: Inference thread count.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        self._model_path = config.get("speaker_model", "")
        self._match_threshold = float(config.get("match_threshold", 0.5))
        self._num_threads = int(config.get("num_threads", 2))

        self._extractor: Any = None  # SpeakerEmbeddingExtractor
        self._manager: Any = None  # SpeakerEmbeddingManager

    def start(self) -> None:
        try:
            from sherpa_onnx import (
                SpeakerEmbeddingExtractor,
                SpeakerEmbeddingExtractorConfig,
                SpeakerEmbeddingManager,
            )

            if not self._model_path:
                raise FileNotFoundError("Speaker model path not configured")

            config = SpeakerEmbeddingExtractorConfig(
                model=self._model_path,
                num_threads=self._num_threads,
            )

            self._extractor = SpeakerEmbeddingExtractor(config=config)
            self._manager = SpeakerEmbeddingManager(dim=self._extractor.dim)

            self.available = True
            logger.info(
                "SpeakerSherpaProvider started — model=%s, dim=%d, threshold=%.2f",
                self._model_path,
                self._extractor.dim,
                self._match_threshold,
            )

        except FileNotFoundError as exc:
            self.available = False
            logger.warning("SpeakerSherpaProvider unavailable: %s", exc)
        except Exception as exc:
            self.available = False
            logger.warning(
                "SpeakerSherpaProvider unavailable: %s", exc, exc_info=True,
            )

    def stop(self) -> None:
        self._extractor = None
        self._manager = None
        self.available = False
        logger.info("SpeakerSherpaProvider stopped")

    # ── Pipeline interface ──────────────────────────────────────

    def verify(self, audio_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Identify speaker from raw audio.

        Called in the wakeup → VAD → ASR + Speaker pipeline.

        Args:
            audio_data: Dict from VAD with audio_samples (np.ndarray), sample_rate.

        Returns:
            Dict with speaker_id, confidence, matched.
        """
        if not self.available or self._extractor is None:
            return {"speaker_id": "unknown", "confidence": 0.0, "matched": False}

        if audio_data is None:
            return {"speaker_id": "unknown", "confidence": 0.0, "matched": False}

        samples = audio_data.get("audio_samples")
        if samples is None or (hasattr(samples, "__len__") and len(samples) == 0):
            return {"speaker_id": "unknown", "confidence": 0.0, "matched": False}

        if not audio_data.get("has_voice", True):
            return {"speaker_id": "unknown", "confidence": 0.0, "matched": False}

        try:
            # Extract speaker embedding
            embedding = self._extract_embedding(samples)

            # Search enrolled speakers
            speaker_id = self._manager.search(
                v=embedding,
                threshold=self._match_threshold,
            )

            # Get similarity score (approximate)
            confidence = 0.0
            if speaker_id:
                # Verify to get confidence indication
                is_match = self._manager.verify(
                    name=speaker_id,
                    v=embedding,
                    threshold=self._match_threshold,
                )
                confidence = self._match_threshold + 0.3 if is_match else self._match_threshold - 0.1

            matched = bool(speaker_id)
            if not speaker_id:
                speaker_id = "unknown"

            logger.debug(
                "Speaker verify: id=%s conf=%.2f matched=%s",
                speaker_id, confidence, matched,
            )

            return {
                "speaker_id": speaker_id,
                "confidence": round(confidence, 2),
                "matched": matched,
            }

        except Exception as exc:
            logger.error("Speaker verify error: %s", exc, exc_info=True)
            return {"speaker_id": "unknown", "confidence": 0.0, "matched": False}

    # ── Service interface ────────────────────────────────────────

    def enroll(self, audio_data: dict[str, Any] | None = None,
               speaker_id: str | None = None) -> dict[str, Any]:
        """Enroll a new speaker from raw audio.

        Called by /perception/perception_task (task_type=enroll_speaker).

        Args:
            audio_data: Dict from VAD with audio_samples.
            speaker_id: Speaker label.

        Returns:
            Dict with success and speaker_id.
        """
        if not self.available or self._extractor is None:
            return {"success": False, "speaker_id": speaker_id or ""}

        if audio_data is None or speaker_id is None:
            return {"success": False, "speaker_id": speaker_id or ""}

        samples = audio_data.get("audio_samples")
        if samples is None or (hasattr(samples, "__len__") and len(samples) == 0):
            return {"success": False, "speaker_id": speaker_id}

        try:
            embedding = self._extract_embedding(samples)

            success = self._manager.add(name=speaker_id, v=embedding)

            logger.info(
                "Speaker enrolled: id=%s, success=%s (total=%d)",
                speaker_id, success, self._manager.num_speakers,
            )

            return {"success": success, "speaker_id": speaker_id}

        except Exception as exc:
            logger.error("Speaker enroll error: %s", exc, exc_info=True)
            return {"success": False, "speaker_id": speaker_id}

    def verify_speaker(self, audio_data: dict[str, Any] | None = None,
                       speaker_id: str | None = None) -> dict[str, Any]:
        """Verify a specific speaker against enrolled embedding.

        Called by /perception/perception_task (task_type=verify_speaker).

        Args:
            audio_data: Dict from VAD with audio_samples.
            speaker_id: Speaker label to verify against.

        Returns:
            Dict with matched (bool) and confidence (float).
        """
        if not self.available or self._extractor is None:
            return {"matched": False, "confidence": 0.0}

        if audio_data is None or speaker_id is None:
            return {"matched": False, "confidence": 0.0}

        samples = audio_data.get("audio_samples")
        if samples is None or (hasattr(samples, "__len__") and len(samples) == 0):
            return {"matched": False, "confidence": 0.0}

        try:
            embedding = self._extract_embedding(samples)

            matched = self._manager.verify(
                name=speaker_id,
                v=embedding,
                threshold=self._match_threshold,
            )

            confidence = self._match_threshold + 0.3 if matched else self._match_threshold - 0.1

            logger.info(
                "Speaker verify_speaker: id=%s matched=%s conf=%.2f",
                speaker_id, matched, confidence,
            )

            return {"matched": matched, "confidence": round(confidence, 2)}

        except Exception as exc:
            logger.error("Speaker verify_speaker error: %s", exc, exc_info=True)
            return {"matched": False, "confidence": 0.0}

    # ── Internal helpers ─────────────────────────────────────────

    def _extract_embedding(self, samples: np.ndarray) -> list[float]:
        """Extract speaker embedding from audio samples.

        Args:
            samples: Float32 1-D numpy array of audio samples.

        Returns:
            Embedding as list of floats.
        """
        if not isinstance(samples, np.ndarray):
            samples = np.array(samples, dtype=np.float32)
        samples = samples.astype(np.float32)

        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=samples)
        stream.input_finished()

        assert self._extractor.is_ready(stream), "Extractor not ready"

        embedding = self._extractor.compute(stream)
        return list(embedding)
