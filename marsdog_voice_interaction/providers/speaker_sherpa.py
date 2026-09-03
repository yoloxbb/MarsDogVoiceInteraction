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
      audio_data → extract embedding → score against enrolled templates
      → {speaker_id, confidence, matched, reason}

    Service role:
      /perception/perception_task → enroll / verify

    Attributes:
        _extractor: SpeakerEmbeddingExtractor instance.
        _templates: Mapping of speaker name to one or more enrollment
            embeddings. Identification scores the query against every
            template and keeps the maximum cosine similarity per speaker.
        _match_threshold: Cosine-similarity threshold for matching.
        _min_samples: Minimum audio length (in samples at 16 kHz) required
            to produce a usable embedding.
        _num_threads: Inference thread count.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        self._model_path = config.get("speaker_model", "")
        self._match_threshold = float(config.get("match_threshold", 0.5))
        self._min_samples = int(config.get("min_samples", 8000))
        self._num_threads = int(config.get("num_threads", 2))

        self._extractor: Any = None  # SpeakerEmbeddingExtractor
        self._templates: dict[str, list[np.ndarray]] = {}

    def start(self) -> None:
        try:
            from sherpa_onnx import (
                SpeakerEmbeddingExtractor,
                SpeakerEmbeddingExtractorConfig,
            )

            if not self._model_path:
                raise FileNotFoundError("Speaker model path not configured")

            config = SpeakerEmbeddingExtractorConfig(
                model=self._model_path,
                num_threads=self._num_threads,
            )

            self._extractor = SpeakerEmbeddingExtractor(config=config)

            self.available = True
            logger.info(
                "SpeakerSherpaProvider started — model=%s, dim=%d, "
                "threshold=%.2f, min_samples=%d",
                self._model_path,
                self._extractor.dim,
                self._match_threshold,
                self._min_samples,
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
        self._templates = {}
        self.available = False
        logger.info("SpeakerSherpaProvider stopped")

    def set_templates(self, templates: dict[str, list[np.ndarray]]) -> None:
        """Replace the enrolled templates used for identification."""
        self._templates = {
            name: [
                np.asarray(value, dtype=np.float32).reshape(-1)
                for value in values
            ]
            for name, values in templates.items()
            if values
        }

    # ── Pipeline interface ──────────────────────────────────────

    def verify(self, audio_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Identify speaker from raw audio.

        Called in the wakeup → VAD → ASR + Speaker pipeline.

        Args:
            audio_data: Dict from VAD with audio_samples (np.ndarray), sample_rate.

        Returns:
            Dict with speaker_id, confidence (real cosine similarity), matched,
            and a reason string.
        """
        if not self.available or self._extractor is None:
            return {"speaker_id": "unknown", "confidence": 0.0,
                    "matched": False, "reason": "unavailable"}

        if audio_data is None:
            return {"speaker_id": "unknown", "confidence": 0.0,
                    "matched": False, "reason": "no_audio"}

        samples = audio_data.get("audio_samples")
        if samples is None or (hasattr(samples, "__len__") and len(samples) == 0):
            return {"speaker_id": "unknown", "confidence": 0.0,
                    "matched": False, "reason": "empty_audio"}

        if not audio_data.get("has_voice", True):
            return {"speaker_id": "unknown", "confidence": 0.0,
                    "matched": False, "reason": "no_voice"}

        sample_rate = int(audio_data.get("sample_rate") or 16000)
        embedding, reason = self._prepare_embedding(samples, sample_rate)
        if embedding is None:
            return {"speaker_id": "unknown", "confidence": 0.0,
                    "matched": False, "reason": reason}

        if not self._templates:
            return {"speaker_id": "unknown", "confidence": 0.0,
                    "matched": False, "reason": "no_templates"}

        best_name, best_score = self._best_match(embedding)
        matched = bool(best_name) and best_score >= self._match_threshold
        speaker_id = best_name if matched else "unknown"

        logger.debug(
            "Speaker verify: id=%s score=%.4f matched=%s",
            speaker_id, best_score, matched,
        )

        return {
            "speaker_id": speaker_id,
            "confidence": round(best_score, 4),
            "matched": matched,
            "reason": "matched" if matched else "below_threshold",
        }

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

        embedding, reason = self._prepare_embedding(
            samples, int(audio_data.get("sample_rate") or 16000),
        )
        if embedding is None:
            return {"success": False, "speaker_id": speaker_id, "reason": reason}

        self._templates.setdefault(speaker_id, []).append(embedding)

        logger.info(
            "Speaker enrolled: id=%s (total templates=%d)",
            speaker_id, len(self._templates.get(speaker_id, [])),
        )

        return {"success": True, "speaker_id": speaker_id}

    def verify_speaker(self, audio_data: dict[str, Any] | None = None,
                       speaker_id: str | None = None) -> dict[str, Any]:
        """Verify a specific speaker against enrolled embedding.

        Called by /perception/perception_task (task_type=verify_speaker).

        Args:
            audio_data: Dict from VAD with audio_samples.
            speaker_id: Speaker label to verify against.

        Returns:
            Dict with matched (bool) and confidence (real cosine similarity).
        """
        if not self.available or self._extractor is None:
            return {"matched": False, "confidence": 0.0}

        if audio_data is None or speaker_id is None:
            return {"matched": False, "confidence": 0.0}

        samples = audio_data.get("audio_samples")
        if samples is None or (hasattr(samples, "__len__") and len(samples) == 0):
            return {"matched": False, "confidence": 0.0}

        embedding, reason = self._prepare_embedding(
            samples, int(audio_data.get("sample_rate") or 16000),
        )
        if embedding is None:
            return {"matched": False, "confidence": 0.0, "reason": reason}

        templates = self._templates.get(speaker_id, [])
        if not templates:
            return {"matched": False, "confidence": 0.0, "reason": "no_templates"}

        best_score = max(self._cosine(embedding, t) for t in templates)
        matched = best_score >= self._match_threshold

        logger.info(
            "Speaker verify_speaker: id=%s score=%.4f matched=%s",
            speaker_id, best_score, matched,
        )

        return {
            "matched": matched,
            "confidence": round(best_score, 4),
            "reason": "matched" if matched else "below_threshold",
        }

    # ── Internal helpers ─────────────────────────────────────────

    def _best_match(self, embedding: np.ndarray) -> tuple[str, float]:
        """Return the (speaker, score) pair with the highest cosine similarity."""
        best_name = ""
        best_score = -1.0
        for name, templates in self._templates.items():
            for template in templates:
                score = self._cosine(embedding, template)
                if score > best_score:
                    best_score = score
                    best_name = name
        return best_name, best_score

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two embeddings."""
        left = np.asarray(a, dtype=np.float32).reshape(-1)
        right = np.asarray(b, dtype=np.float32).reshape(-1)
        left_norm = float(np.linalg.norm(left))
        right_norm = float(np.linalg.norm(right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return float(np.dot(left, right) / (left_norm * right_norm))

    def _prepare_embedding(
        self,
        samples: Any,
        sample_rate: int,
    ) -> tuple[np.ndarray | None, str]:
        """Normalize, resample, and gate raw audio; return (embedding, reason).

        Args:
            samples: Float32 audio samples.
            sample_rate: Source sample rate in Hz.

        Returns:
            Embedding as a float32 1-D array and ``"ok"`` on success, or
            ``(None, reason)`` describing why no embedding could be produced.
        """
        try:
            waveform = np.asarray(samples, dtype=np.float32).reshape(-1)
        except (TypeError, ValueError):
            return None, "invalid_audio"

        if waveform.size == 0:
            return None, "empty_audio"

        rate = int(sample_rate) if sample_rate else 16000
        if rate != 16000:
            import scipy.signal

            waveform = scipy.signal.resample(
                waveform, int(waveform.size * 16000 / rate),
            ).astype(np.float32)

        if waveform.size < self._min_samples:
            return None, "audio_too_short"

        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=waveform)
        stream.input_finished()

        if not self._extractor.is_ready(stream):
            return None, "extractor_not_ready"

        try:
            return (
                np.asarray(self._extractor.compute(stream), dtype=np.float32),
                "ok",
            )
        except Exception:
            return None, "compute_failed"
