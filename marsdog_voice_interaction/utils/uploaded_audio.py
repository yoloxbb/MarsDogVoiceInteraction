"""Decode uploaded WAV files and retain only Silero-VAD speech segments."""

from __future__ import annotations

import io
import math
import threading
import wave
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.signal import resample_poly


@dataclass(frozen=True)
class VadTrimResult:
    """Normalized speech extracted from one uploaded WAV file."""

    samples: np.ndarray
    sample_rate: int
    source_sample_rate: int
    wav_bytes: bytes
    source_duration_ms: float
    speech_duration_ms: float
    segment_count: int


def decode_pcm16_wav(payload: bytes) -> tuple[np.ndarray, int]:
    """Decode PCM16 WAV bytes to one mono float32 waveform."""
    if not payload:
        raise ValueError("音频文件为空")
    try:
        with wave.open(io.BytesIO(payload), "rb") as source:
            if source.getcomptype() != "NONE":
                raise ValueError("只支持未压缩 PCM WAV")
            channels = int(source.getnchannels())
            sample_width = int(source.getsampwidth())
            sample_rate = int(source.getframerate())
            frame_count = int(source.getnframes())
            raw = source.readframes(frame_count)
    except (EOFError, wave.Error) as exc:
        raise ValueError(f"无法解析 WAV: {exc}") from exc

    if channels < 1 or channels > 8:
        raise ValueError("WAV 声道数无效")
    if sample_width != 2:
        raise ValueError("只支持 16-bit PCM WAV")
    if sample_rate < 8000 or sample_rate > 96000:
        raise ValueError("WAV 采样率必须在 8000-96000 Hz")
    expected_bytes = frame_count * channels * sample_width
    if len(raw) != expected_bytes:
        raise ValueError("WAV 数据不完整或已损坏")
    values = np.frombuffer(raw, dtype="<i2")
    if values.size == 0:
        raise ValueError("WAV 中没有音频采样")
    usable = values.size - values.size % channels
    values = values[:usable].reshape(-1, channels).astype(np.float32)
    mono = values.mean(axis=1) / 32768.0
    return np.ascontiguousarray(mono, dtype=np.float32), sample_rate


def encode_pcm16_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode a mono float waveform as PCM16 WAV bytes."""
    waveform = np.asarray(samples, dtype=np.float32).reshape(-1)
    waveform = np.nan_to_num(waveform, nan=0.0, posinf=1.0, neginf=-1.0)
    pcm = np.clip(waveform, -1.0, 1.0)
    pcm = np.round(pcm * 32767.0).astype("<i2")
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(int(sample_rate))
        output.writeframes(pcm.tobytes())
    return target.getvalue()


def _resample(
    samples: np.ndarray,
    source_rate: int,
    target_rate: int,
) -> np.ndarray:
    if source_rate == target_rate:
        return np.ascontiguousarray(samples, dtype=np.float32)
    divisor = math.gcd(int(source_rate), int(target_rate))
    result = resample_poly(
        samples,
        int(target_rate) // divisor,
        int(source_rate) // divisor,
    )
    return np.ascontiguousarray(result, dtype=np.float32)


def normalize_pcm16_wav(
    payload: bytes,
    target_rate: int = 16000,
) -> tuple[np.ndarray, int, bytes]:
    """Decode a PCM16 WAV and return normalized mono PCM16 WAV data."""
    samples, source_rate = decode_pcm16_wav(payload)
    normalized = _resample(samples, source_rate, int(target_rate))
    return normalized, int(target_rate), encode_pcm16_wav(normalized, target_rate)


class UploadedAudioVAD:
    """Own a dedicated sherpa-onnx VAD for uploaded, already-recorded audio."""

    def __init__(self, config: dict[str, Any], detector: Any | None = None) -> None:
        self._sample_rate = int(config.get("sample_rate", 16000))
        if self._sample_rate != 16000:
            raise ValueError("Uploaded VAD sample_rate must be 16000")
        self._num_threads = int(config.get("num_threads", 2))
        self._model_path = str(config.get("vad_model", ""))
        self._threshold = float(config.get("vad_threshold", 0.5))
        self._min_silence_sec = float(config.get("min_silence_dur", 0.5))
        self._min_speech_sec = float(config.get("min_speech_dur", 0.25))
        self._pre_roll_sec = max(0.0, float(config.get("pre_roll_sec", 0.3)))
        self._post_roll_sec = max(
            0.0,
            float(config.get("upload_post_roll_sec", 0.2)),
        )
        self._join_silence_sec = max(
            0.0,
            float(config.get("upload_join_silence_sec", 0.1)),
        )
        self._max_duration_sec = max(
            1.0,
            float(config.get("upload_max_duration_sec", 60.0)),
        )
        self._min_effective_sec = max(
            0.5,
            float(config.get("upload_min_speech_sec", 0.5)),
        )
        self._chunk_samples = max(1, int(self._sample_rate * 0.02))
        self._lock = threading.Lock()
        self._detector = detector or self._create_detector()

    @property
    def available(self) -> bool:
        return self._detector is not None

    def _create_detector(self) -> Any:
        if not self._model_path:
            raise RuntimeError("未配置上传音频 VAD 模型")
        from sherpa_onnx import (
            SileroVadModelConfig,
            VadModelConfig,
            VoiceActivityDetector,
        )

        silero = SileroVadModelConfig(
            model=self._model_path,
            threshold=self._threshold,
            min_silence_duration=self._min_silence_sec,
            min_speech_duration=self._min_speech_sec,
        )
        return VoiceActivityDetector(
            config=VadModelConfig(
                silero_vad=silero, sample_rate=self._sample_rate,
                num_threads=self._num_threads,
            ),
            buffer_size_in_seconds=max(60, int(self._max_duration_sec) + 5),
        )

    def trim_wav(self, payload: bytes) -> VadTrimResult:
        normalized, source_rate, source_duration_sec, ranges = (
            self._prepare_wav(payload)
        )
        if not ranges:
            raise ValueError("VAD 未检测到有效语音")
        speech_duration_sec = sum(end - start for start, end in ranges) / self._sample_rate
        if speech_duration_sec < self._min_effective_sec:
            raise ValueError(
                "VAD 有效语音过短，至少需要 "
                f"{self._min_effective_sec:g} 秒"
            )
        segment_count = len(ranges)
        pre = int(self._pre_roll_sec * self._sample_rate)
        post = int(self._post_roll_sec * self._sample_rate)
        ranges = self._merge_ranges([
            (max(0, start - pre), min(len(normalized), end + post))
            for start, end in ranges
        ])

        separator = np.zeros(
            int(self._join_silence_sec * self._sample_rate),
            dtype=np.float32,
        )
        pieces: list[np.ndarray] = []
        for index, (start, end) in enumerate(ranges):
            if index and separator.size:
                pieces.append(separator)
            pieces.append(normalized[start:end])
        speech = np.ascontiguousarray(np.concatenate(pieces), dtype=np.float32)
        return VadTrimResult(
            samples=speech,
            sample_rate=self._sample_rate,
            source_sample_rate=source_rate,
            wav_bytes=encode_pcm16_wav(speech, self._sample_rate),
            source_duration_ms=round(source_duration_sec * 1000.0, 2),
            speech_duration_ms=round(speech_duration_sec * 1000.0, 2),
            segment_count=segment_count,
        )

    def analyze_wav(self, payload: bytes) -> dict[str, Any]:
        """Return VAD ranges for a browser editor without storing the audio."""
        _, source_rate, source_duration_sec, ranges = (
            self._prepare_wav(payload)
        )
        speech_duration_ms = round(
            sum(end - start for start, end in ranges) * 1000.0 / self._sample_rate,
            2,
        )
        duration_ms = round(source_duration_sec * 1000.0, 2)
        start_ms = end_ms = None
        if ranges:
            start_ms = max(
                0.0,
                round(ranges[0][0] * 1000.0 / self._sample_rate
                      - self._pre_roll_sec * 1000.0, 2),
            )
            end_ms = min(
                duration_ms,
                round(ranges[-1][1] * 1000.0 / self._sample_rate
                      + self._post_roll_sec * 1000.0, 2),
            )
        return {
            "ok": True,
            "source_sample_rate": source_rate,
            "source_duration_ms": duration_ms,
            "speech_duration_ms": speech_duration_ms,
            "min_speech_duration_ms": round(self._min_effective_sec * 1000.0, 2),
            "segment_count": len(ranges),
            "speech_ranges_ms": [
                {
                    "start_ms": round(start * 1000.0 / self._sample_rate, 2),
                    "end_ms": round(end * 1000.0 / self._sample_rate, 2),
                }
                for start, end in ranges
            ],
            "suggested_crop_ms": (
                {"start_ms": start_ms, "end_ms": end_ms}
                if ranges else None
            ),
            "valid_for_enrollment": bool(
                ranges and speech_duration_ms >= self._min_effective_sec * 1000.0
            ),
        }

    def _prepare_wav(
        self, payload: bytes
    ) -> tuple[np.ndarray, int, float, list[tuple[int, int]]]:
        samples, source_rate = decode_pcm16_wav(payload)
        source_duration_sec = len(samples) / float(source_rate)
        if source_duration_sec > self._max_duration_sec:
            raise ValueError(
                f"音频时长超过上限 {self._max_duration_sec:g} 秒"
            )
        normalized = _resample(samples, source_rate, self._sample_rate)
        ranges = self._detect_ranges(normalized)
        return normalized, source_rate, source_duration_sec, ranges

    def _detect_ranges(self, samples: np.ndarray) -> list[tuple[int, int]]:
        with self._lock:
            detector = self._detector
            detector.reset()
            detected: list[tuple[int, int]] = []
            for start in range(0, len(samples), self._chunk_samples):
                detector.accept_waveform(
                    samples[start:start + self._chunk_samples].tolist()
                )
                self._drain(detector, detected)
            detector.flush()
            self._drain(detector, detected)

        bounded = [
            (max(0, start), min(len(samples), end))
            for start, end in detected
            if min(len(samples), end) > max(0, start)
        ]
        return self._merge_ranges(bounded)

    @staticmethod
    def _drain(detector: Any, target: list[tuple[int, int]]) -> None:
        while not detector.empty():
            segment = detector.front
            start = max(0, int(getattr(segment, "start", 0)))
            length = len(getattr(segment, "samples", ()))
            if length:
                target.append((start, start + length))
            detector.pop()

    @staticmethod
    def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        merged: list[tuple[int, int]] = []
        for start, end in sorted(ranges):
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        return merged
