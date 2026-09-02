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
from marsdog_voice_interaction.utils.audio_debug import AudioDebugRecorder

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
        self._audio_debug = AudioDebugRecorder(config.get("audio_debug"))

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
            received = np.asarray(samples)
            received_dtype = str(received.dtype)
            received_shape = tuple(received.shape)
            samples = received.astype(np.float32)

            sr = int(audio_data.get("sample_rate", self._sample_rate))
            utterance_id = str(audio_data.get("utterance_id", "")).strip()
            if self._audio_debug.enabled and utterance_id:
                logger.info(
                    "audio_debug asr_format utterance_id=%s "
                    "sample_rate=%d received_dtype=%s received_shape=%s "
                    "normalized_dtype=%s normalized_shape=%s",
                    utterance_id,
                    sr,
                    received_dtype,
                    received_shape,
                    str(samples.dtype),
                    tuple(samples.shape),
                )
                # This is deliberately immediately before accept_waveform():
                # 03_asr_input.wav is the exact normalized ndarray seen below.
                self._audio_debug.save(
                    utterance_id, "asr_input", samples, sr,
                )
                if sr != self._sample_rate:
                    logger.error(
                        "audio_debug ASR sample-rate mismatch "
                        "utterance_id=%s actual=%d expected=%d",
                        utterance_id,
                        sr,
                        self._sample_rate,
                    )
                if samples.ndim != 1:
                    logger.error(
                        "audio_debug ASR channel/shape mismatch "
                        "utterance_id=%s shape=%s expected=mono_1d",
                        utterance_id,
                        tuple(samples.shape),
                    )

            # Create stream and feed audio
            asr_text, language = self._decode_waveform(samples, sr)
            latency_ms = (time.perf_counter() - t0) * 1000.0

            logger.info(
                "ASR: %r lang=%s (%.0fms)",
                asr_text,
                language,
                latency_ms,
            )

            response = {
                "asr_text": asr_text,
                "language": language,
                "confidence": 0.90,
                "latency_ms": round(latency_ms, 2),
            }
            if (
                self._audio_debug.enabled
                and self._audio_debug.compare_asr
                and utterance_id
            ):
                response["debug_asr_compare"] = self.compare_debug_utterance(
                    utterance_id,
                )
            return response

        except Exception as exc:
            logger.error("ASR transcription error: %s", exc, exc_info=True)
            return {"asr_text": "", "language": self._language, "confidence": 0.0}

    def _decode_waveform(
        self,
        samples: np.ndarray,
        sample_rate: int,
    ) -> tuple[str, str]:
        """Decode one waveform with the provider's already-loaded model."""
        stream = self._recognizer.create_stream()
        stream.accept_waveform(
            sample_rate=int(sample_rate),
            waveform=np.asarray(samples, dtype=np.float32),
        )
        self._recognizer.decode_stream(stream)
        result = stream.result
        if self._model_type == "paraformer":
            language = "zh"
        else:
            language = _normalize_sense_voice_language(
                getattr(result, "lang", ""), self._language,
            )
        return str(result.text), language

    def compare_debug_utterance(self, utterance_id: str) -> dict[str, str]:
        """Run RAW, VAD and final ASR-input WAVs through this same model."""
        labels = (("raw", "RAW"), ("vad", "VAD"), ("asr_input", "ASR_INPUT"))
        results: dict[str, str] = {}
        try:
            for audio_type, label in labels:
                samples, sample_rate = self._audio_debug.load(
                    utterance_id, audio_type,
                )
                if samples.ndim != 1:
                    raise ValueError(
                        f"{audio_type} is not mono: shape={samples.shape}"
                    )
                text, _ = self._decode_waveform(samples, sample_rate)
                results[label] = text
            logger.info(
                "ASR_COMPARE utterance_id=%s\nRAW:\n%s\nVAD:\n%s\n"
                "ASR_INPUT:\n%s",
                utterance_id,
                results["RAW"],
                results["VAD"],
                results["ASR_INPUT"],
            )
        except Exception as exc:
            logger.error(
                "ASR_COMPARE failed utterance_id=%s: %s",
                utterance_id,
                exc,
                exc_info=True,
            )
        return results
