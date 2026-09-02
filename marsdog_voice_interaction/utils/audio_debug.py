"""Opt-in waveform evidence for the microphone -> VAD -> ASR pipeline."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import wavfile


logger = logging.getLogger(__name__)

_FILENAMES = {
    "raw": "01_raw_capture.wav",
    "vad": "02_vad_segment.wav",
    "asr_input": "03_asr_input.wav",
}


class AudioDebugRecorder:
    """Save float32 WAV evidence and emit machine-readable metadata logs."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config if isinstance(config, dict) else {}
        self.enabled = bool(config.get("enabled", False))
        self.output_dir = Path(
            str(config.get("output_dir", "/tmp/voice_debug"))
        ).expanduser()
        self.save_raw_capture = bool(config.get("save_raw_capture", True))
        self.save_vad_segment = bool(config.get("save_vad_segment", True))
        self.save_asr_input = bool(config.get("save_asr_input", True))
        self.compare_asr = bool(config.get("compare_asr", False))
        self.disable_extra_pre_roll = bool(
            config.get("debug_disable_extra_pre_roll", False)
        )

    @staticmethod
    def _safe_utterance_id(utterance_id: str) -> str:
        value = re.sub(r"[^A-Za-z0-9_.-]", "_", str(utterance_id).strip())
        return value or "unknown_utterance"

    def utterance_dir(self, utterance_id: str) -> Path:
        return self.output_dir / self._safe_utterance_id(utterance_id)

    def path_for(self, utterance_id: str, audio_type: str) -> Path:
        return self.utterance_dir(utterance_id) / _FILENAMES[audio_type]

    def should_save(self, audio_type: str) -> bool:
        return self.enabled and {
            "raw": self.save_raw_capture,
            "vad": self.save_vad_segment,
            "asr_input": self.save_asr_input,
        }[audio_type]

    def save(
        self,
        utterance_id: str,
        audio_type: str,
        samples: Any,
        sample_rate: int,
    ) -> Path | None:
        """Save one IEEE-float WAV without changing sample values or shape."""
        if not self.should_save(audio_type):
            return None
        waveform = np.asarray(samples)
        original_dtype = str(waveform.dtype)
        if waveform.ndim == 0:
            waveform = waveform.reshape(1)
        channels = int(waveform.shape[1]) if waveform.ndim == 2 else 1
        num_samples = int(waveform.shape[0]) if waveform.ndim else 0
        stored = np.ascontiguousarray(waveform, dtype=np.float32)
        finite = stored[np.isfinite(stored)]
        min_value = float(np.min(finite)) if finite.size else 0.0
        max_value = float(np.max(finite)) if finite.size else 0.0
        rms = (
            float(np.sqrt(np.mean(np.square(finite, dtype=np.float64))))
            if finite.size else 0.0
        )
        path = self.path_for(utterance_id, audio_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(path, int(sample_rate), stored)
        logger.info(
            "audio_debug utterance_id=%s type=%s sample_rate=%d "
            "num_samples=%d duration_ms=%.2f dtype=%s source_dtype=%s "
            "channels=%d min=%.7f max=%.7f rms=%.7f "
            "nonfinite_count=%d out_of_range_count=%d wav_encoding=FLOAT32 "
            "path=%s",
            utterance_id,
            audio_type,
            int(sample_rate),
            num_samples,
            num_samples / max(1, int(sample_rate)) * 1000.0,
            str(stored.dtype),
            original_dtype,
            channels,
            min_value,
            max_value,
            rms,
            int(stored.size - finite.size),
            int(np.count_nonzero((finite < -1.0) | (finite > 1.0))),
            path,
        )
        return path

    def load(
        self,
        utterance_id: str,
        audio_type: str,
    ) -> tuple[np.ndarray, int]:
        sample_rate, samples = wavfile.read(
            self.path_for(utterance_id, audio_type), mmap=False,
        )
        return np.asarray(samples, dtype=np.float32), int(sample_rate)
