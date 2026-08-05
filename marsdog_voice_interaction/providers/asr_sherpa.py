"""ASR provider using sherpa-onnx OfflineRecognizer (SenseVoice / Paraformer).

Takes raw audio segments from the VAD provider and transcribes them
to Chinese text using the configured model.

Supports:
- SenseVoice (multi-language, ITN, SenseVoice language tags)
- Paraformer (Chinese-only, no ITN, no language tags)

Requires: sherpa-onnx
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from marsdog_voice_interaction.providers.base import BaseProvider

logger = logging.getLogger(__name__)

_VALID_MODEL_TYPES = frozenset({"sense_voice", "paraformer"})


def _normalize_sense_voice_language(value: Any, fallback: str) -> str:
    """Convert SenseVoice tags such as ``<|en|>`` to protocol values."""
    language = str(value or "").strip()
    if language.startswith("<|") and language.endswith("|>"):
        language = language[2:-2]
    return language if language in {"zh", "en", "ja", "ko", "yue"} else fallback


class ASRSherpaProvider(BaseProvider):
    """ASR provider using sherpa-onnx.

    Supports SenseVoice and Paraformer via ``model_type`` config key.

    Attributes:
        _model_type: ``"sense_voice"`` or ``"paraformer"``.
        _model_path: Path to the ONNX model file.
        _tokens: Path to tokens.txt.
        _sample_rate: Expected audio sample rate.
        _language: Language hint (SenseVoice only: auto/zh/en/ja/ko/yue).
        _use_itn: Enable inverse text normalization (SenseVoice only).
        _num_threads: Inference thread count.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        model_type = str(config.get("model_type", "sense_voice")).lower()
        if model_type not in _VALID_MODEL_TYPES:
            raise ValueError(
                f"Unknown ASR model_type: {model_type!r}. "
                f"Valid values: {', '.join(sorted(_VALID_MODEL_TYPES))}"
            )
        self._model_type = model_type
        self._model_path = config.get("asr_model", "")
        self._tokens = config.get("tokens", "")
        self._sample_rate = int(config.get("sample_rate", 16000))
        self._language = config.get("language", "zh")
        self._use_itn = bool(config.get("use_itn", True))
        self._num_threads = int(config.get("num_threads", 4))

        self._recognizer: Any = None  # OfflineRecognizer

    def start(self) -> None:
        try:
            from sherpa_onnx import OfflineRecognizer

            if not self._model_path:
                raise FileNotFoundError("ASR model path not configured")
            if not self._tokens:
                raise FileNotFoundError("ASR tokens path not configured")

            common = dict(
                num_threads=self._num_threads,
                sample_rate=self._sample_rate,
                decoding_method="greedy_search",
                debug=False,
            )

            if self._model_type == "paraformer":
                self._recognizer = OfflineRecognizer.from_paraformer(
                    paraformer=self._model_path,
                    tokens=self._tokens,
                    **common,
                )
            else:
                self._recognizer = OfflineRecognizer.from_sense_voice(
                    model=self._model_path,
                    tokens=self._tokens,
                    language=self._language,
                    use_itn=self._use_itn,
                    **common,
                )

            self.available = True
            logger.info(
                "ASRSherpaProvider started — type=%s model=%s threads=%d",
                self._model_type,
                self._model_path,
                self._num_threads,
            )

        except FileNotFoundError as exc:
            self.available = False
            logger.warning("ASRSherpaProvider unavailable: %s", exc)
        except Exception as exc:
            self.available = False
            logger.warning(
                "ASRSherpaProvider unavailable: %s", exc, exc_info=True,
            )

    def stop(self) -> None:
        self._recognizer = None
        self.available = False
        logger.info("ASRSherpaProvider stopped")

    def transcribe(self, audio_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Transcribe audio to text.

        Args:
            audio_data: Dict from VAD provider with keys:
                audio_samples (np.ndarray float32), sample_rate (int).
                If None or has_voice=False, returns empty result.

        Returns:
            Dict with asr_text, language, confidence, and latency_ms.
        """
        if not self.available or self._recognizer is None:
            return {"asr_text": "", "language": self._language, "confidence": 0.0}

        if audio_data is None:
            return {"asr_text": "", "language": self._language, "confidence": 0.0}

        samples = audio_data.get("audio_samples")
        if samples is None or (hasattr(samples, "__len__") and len(samples) == 0):
            return {"asr_text": "", "language": self._language, "confidence": 0.0}

        if not audio_data.get("has_voice", True):
            return {"asr_text": "", "language": self._language, "confidence": 0.0}

        try:
            import time

            t0 = time.perf_counter()

            # Ensure float32 and correct sample rate
            if not isinstance(samples, np.ndarray):
                samples = np.array(samples, dtype=np.float32)
            samples = samples.astype(np.float32)

            sr = audio_data.get("sample_rate", self._sample_rate)

            # Create stream and feed audio
            stream = self._recognizer.create_stream()
            stream.accept_waveform(sample_rate=sr, waveform=samples)

            # Run recognition
            self._recognizer.decode_stream(stream)

            result = stream.result
            asr_text = result.text
            if self._model_type == "paraformer":
                # Paraformer is Chinese-only; no language tag in output.
                language = "zh"
            else:
                language = _normalize_sense_voice_language(
                    getattr(result, "lang", ""),
                    self._language,
                )
            latency_ms = (time.perf_counter() - t0) * 1000.0

            logger.info(
                "ASR: %r lang=%s (%.0fms)",
                asr_text,
                language,
                latency_ms,
            )

            return {
                "asr_text": asr_text,
                "language": language,
                "confidence": 0.90,
                "latency_ms": round(latency_ms, 2),
            }

        except Exception as exc:
            logger.error("ASR transcription error: %s", exc, exc_info=True)
            return {"asr_text": "", "language": self._language, "confidence": 0.0}
