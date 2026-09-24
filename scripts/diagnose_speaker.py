#!/usr/bin/env python3
"""Read-only speaker enrollment and labelled-probe comparison.

Example:
    python scripts/diagnose_speaker.py --speaker owner \
        --sample-ids 1,2,3 \
        --probe-owner /tmp/voice_debug/UTTERANCE/03_asr_input.wav \
        --probe-other /tmp/other_speaker.wav

The script does not save waveforms or change the speaker registry.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

from marsdog_voice_interaction.messages.speaker_identity import (
    validate_speaker_identity,
)
from marsdog_voice_interaction.providers.speaker_sherpa import (
    SpeakerSherpaProvider,
)
from marsdog_voice_interaction.utils.config_loader import load_config


def read_waveform(path: Path) -> tuple[np.ndarray, int, dict[str, Any]]:
    rate, raw = wavfile.read(path, mmap=False)
    source = np.asarray(raw)
    if source.ndim not in (1, 2) or source.size == 0:
        raise ValueError(f"WAV 数据为空或声道格式无效: {path}")
    if source.dtype.kind not in "fi":
        raise ValueError(f"WAV 采样格式不支持: {source.dtype}")
    samples = source.astype(np.float32)
    if source.dtype.kind == "i":
        samples /= float(max(abs(np.iinfo(source.dtype).min), 1))
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    if not np.isfinite(samples).all():
        raise ValueError(f"WAV 含非有限采样: {path}")
    if not 8000 <= int(rate) <= 96000:
        raise ValueError(f"WAV 采样率不在 8-96 kHz: {path}")
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    metrics = {
        "sample_rate": int(rate),
        "channels": 1 if source.ndim == 1 else int(source.shape[1]),
        "encoding": str(source.dtype),
        "duration_sec": round(len(samples) / rate, 3),
        "rms": round(rms, 5),
        "peak": round(peak, 5),
        "near_clipping_fraction": round(float(np.mean(np.abs(samples) >= 0.99)), 5),
    }
    return samples, int(rate), metrics


def embedding_for(
    provider: SpeakerSherpaProvider, path: Path,
) -> tuple[np.ndarray, dict[str, Any]]:
    waveform, rate, metrics = read_waveform(path)
    if rate != 16000:
        divisor = math.gcd(rate, 16000)
        waveform = np.ascontiguousarray(
            resample_poly(waveform, 16000 // divisor, rate // divisor),
            dtype=np.float32,
        )
    metrics["embedding_sample_rate"] = 16000
    metrics["resampling"] = "polyphase" if rate != 16000 else "none"
    embedding, reason = provider._prepare_embedding(waveform, 16000)
    if embedding is None:
        raise ValueError(f"声纹提取失败 {path}: {reason}")
    return embedding, metrics


def diagnose(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    name = validate_speaker_identity(args.speaker)
    speaker_config = config["providers"]["speaker"]["config"]
    speaker_dir = Path(config["storage"]["root"]) / "speakers" / name
    if args.sample_ids:
        sample_ids = [int(value) for value in args.sample_ids.split(",")]
        if len(set(sample_ids)) != len(sample_ids) or any(
            value < 1 or value > 5 for value in sample_ids
        ):
            raise ValueError("--sample-ids 必须是互不重复的 1-5 编号")
        sample_files = [speaker_dir / f"{value:03d}.npy" for value in sample_ids]
    else:
        sample_files = sorted(speaker_dir.glob("[0-9][0-9][0-9].npy"))
    if not sample_files or any(not path.is_file() for path in sample_files):
        raise ValueError(f"未找到所选声纹样本: {speaker_dir}")

    provider = SpeakerSherpaProvider(speaker_config)
    provider.start()
    if not provider.is_available():
        raise RuntimeError("声纹模型未成功加载，请检查 speaker_model 配置")
    templates: dict[str, np.ndarray] = {}
    enrolled: list[dict[str, Any]] = []
    try:
        for path in sample_files:
            stored = np.asarray(np.load(path, allow_pickle=False), dtype=np.float32)
            audio_path = path.with_suffix(".wav")
            recomputed, metrics = embedding_for(provider, audio_path)
            parity = provider._cosine(stored, recomputed)
            templates[path.stem] = stored
            enrolled.append({
                "sample_id": int(path.stem),
                "audio": metrics,
                "saved_vs_recomputed_cosine": round(parity, 4),
            })
        pairwise = [
            {
                "sample_ids": [int(left), int(right)],
                "cosine": round(provider._cosine(templates[left], templates[right]), 4),
            }
            for index, left in enumerate(templates)
            for right in list(templates)[index + 1:]
        ]
        probes: list[dict[str, Any]] = []
        for label, paths in (
            ("owner", args.probe_owner),
            ("other", args.probe_other),
            ("unknown", args.probe_unknown),
        ):
            for raw_path in paths:
                path = Path(raw_path).expanduser().resolve()
                embedding, metrics = embedding_for(provider, path)
                scores = {
                    sample_id: round(provider._cosine(embedding, reference), 4)
                    for sample_id, reference in templates.items()
                }
                probes.append({
                    "path": str(path),
                    "label": label,
                    "audio": metrics,
                    "scores_by_sample_id": scores,
                    "best_score": max(scores.values()),
                })
    finally:
        provider.stop()

    threshold = float(speaker_config.get("match_threshold", 0.5))
    owner_scores = [p["best_score"] for p in probes if p["label"] == "owner"]
    other_scores = [p["best_score"] for p in probes if p["label"] == "other"]
    observed: dict[str, Any] = {
        "owner_count": len(owner_scores),
        "owner_below_configured_threshold": sum(s < threshold for s in owner_scores),
        "other_count": len(other_scores),
        "other_above_configured_threshold": sum(s >= threshold for s in other_scores),
    }
    if owner_scores:
        observed["lowest_owner_score"] = min(owner_scores)
    if other_scores:
        observed["highest_other_score"] = max(other_scores)
    if owner_scores and other_scores:
        observed["observed_gap"] = round(
            min(owner_scores) - max(other_scores), 4
        )
    return {
        "speaker": name,
        "model": str(speaker_config["speaker_model"]),
        "configured_match_threshold": threshold,
        "enrolled": enrolled,
        "pairwise_enrollment_scores": pairwise,
        "probes": probes,
        "observed_labels": observed,
        "note": "样本量与标签未经独立验收；这些分数不构成自动调阈值建议。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path,
        default=Path(__file__).resolve().parents[1] / "config/voice.yaml",
    )
    parser.add_argument("--speaker", default="owner")
    parser.add_argument("--sample-ids", default="")
    parser.add_argument("--probe-owner", action="append", default=[], metavar="WAV")
    parser.add_argument("--probe-other", action="append", default=[], metavar="WAV")
    parser.add_argument("--probe-unknown", action="append", default=[], metavar="WAV")
    args = parser.parse_args()
    try:
        print(json.dumps(diagnose(args), ensure_ascii=False, indent=2))
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(f"诊断失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
