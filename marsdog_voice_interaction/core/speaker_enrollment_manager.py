"""Speaker enrollment and voice-print storage."""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from marsdog_voice_interaction.messages.speaker_identity import (
    ALLOWED_SPEAKER_IDENTITIES,
    speaker_identity_role,
    validate_speaker_identity,
)
from marsdog_voice_interaction.utils.uploaded_audio import (
    decode_pcm16_wav,
    encode_pcm16_wav,
    normalize_pcm16_wav,
)


_STORAGE_ROOT = Path("data")
_SPEAKERS_DIR = _STORAGE_ROOT / "speakers"
_REGISTRY_PATH = _STORAGE_ROOT / "speaker_registry.json"
MAX_SPEAKERS = len(ALLOWED_SPEAKER_IDENTITIES)
MAX_SAMPLES_PER_SPEAKER = 5
SPEAKER_SAMPLE_RATE = 16000

ENROLL_SENTENCES = (
    "你好小狗，很高兴认识你",
    "今天天气不错，我们一起玩吧",
    "记住我的声音，以后听我的指令",
)

def set_storage_root(path: str | Path) -> None:
    global _STORAGE_ROOT, _SPEAKERS_DIR, _REGISTRY_PATH
    _STORAGE_ROOT = Path(path)
    _SPEAKERS_DIR = _STORAGE_ROOT / "speakers"
    _REGISTRY_PATH = _STORAGE_ROOT / "speaker_registry.json"
    _SPEAKERS_DIR.mkdir(parents=True, exist_ok=True)


def _load_registry() -> dict[str, Any]:
    if not _REGISTRY_PATH.exists():
        return {"schema_version": 1, "speakers": {}}
    try:
        value = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.setdefault("schema_version", 1)
            value.setdefault("speakers", {})
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return {"schema_version": 1, "speakers": {}}


def _save_registry(value: dict[str, Any]) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _REGISTRY_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(_REGISTRY_PATH)


def _known_speaker_names(registry: dict[str, Any] | None = None) -> set[str]:
    """Return fixed identity slots backed by registry entries or directories."""
    value = registry if registry is not None else _load_registry()
    names = {
        str(name)
        for name in value.get("speakers", {})
        if str(name) in ALLOWED_SPEAKER_IDENTITIES
    }
    if _SPEAKERS_DIR.exists():
        names.update(
            item.name
            for item in _SPEAKERS_DIR.iterdir()
            if item.is_dir() and item.name in ALLOWED_SPEAKER_IDENTITIES
        )
    return names


def _capacity_error(name: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    names = _known_speaker_names(registry)
    if name not in names and len(names) >= MAX_SPEAKERS:
        return {
            "ok": False,
            "status": 409,
            "code": "speaker_limit_reached",
            "error": f"声纹人数已达到上限 {MAX_SPEAKERS} 人",
            "count": len(names),
            "max_speakers": MAX_SPEAKERS,
        }
    return None


def _speaker_sample_count(
    name: str,
    registry: dict[str, Any] | None = None,
) -> int:
    value = registry if registry is not None else _load_registry()
    directory = _SPEAKERS_DIR / name
    stored_ids = _speaker_sample_ids(directory)
    if stored_ids:
        return len(stored_ids)
    metadata = value.get("speakers", {}).get(name, {})
    return int(metadata.get("shots", 0))


def _speaker_sample_ids(directory: Path) -> list[int]:
    """Return stable numeric IDs occupied by WAV or embedding files."""
    if not directory.exists():
        return []
    sample_ids: set[int] = set()
    for suffix in ("wav", "npy"):
        for item in directory.glob(f"[0-9][0-9][0-9].{suffix}"):
            try:
                sample_id = int(item.stem)
            except ValueError:
                continue
            if 1 <= sample_id <= MAX_SAMPLES_PER_SPEAKER:
                sample_ids.add(sample_id)
    return sorted(sample_ids)


def _validate_sample_id(value: int) -> int:
    sample_id = int(value)
    if not 1 <= sample_id <= MAX_SAMPLES_PER_SPEAKER:
        raise ValueError(
            "sample_id 必须在 1 到 "
            f"{MAX_SAMPLES_PER_SPEAKER} 之间"
        )
    return sample_id


def _next_available_sample_id(directory: Path) -> int | None:
    occupied = set(_speaker_sample_ids(directory))
    for sample_id in range(1, MAX_SAMPLES_PER_SPEAKER + 1):
        if sample_id not in occupied:
            return sample_id
    return None


def _mean_sample_embeddings(
    directory: Path,
    sample_ids: list[int],
    *,
    replacement: tuple[int, np.ndarray] | None = None,
) -> np.ndarray:
    replacement_id = replacement[0] if replacement is not None else -1
    values: list[np.ndarray] = []
    expected_shape: tuple[int, ...] | None = None
    for sample_id in sample_ids:
        if sample_id == replacement_id and replacement is not None:
            value = np.asarray(replacement[1], dtype=np.float32)
        else:
            path = directory / f"{sample_id:03d}.npy"
            if not path.exists():
                raise RuntimeError(
                    f"声纹样本 {sample_id:03d} 缺少 embedding 文件"
                )
            value = np.asarray(np.load(path), dtype=np.float32)
        if expected_shape is None:
            expected_shape = value.shape
        elif value.shape != expected_shape:
            raise RuntimeError("声纹样本 embedding 维度不一致")
        values.append(value)
    if not values:
        raise RuntimeError("没有可用于计算 centroid 的声纹样本")
    return np.mean(values, axis=0)


def _save_array_atomic(path: Path, value: np.ndarray) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as stream:
            np.save(stream, value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _sample_record(name: str, sample_id: int) -> dict[str, Any]:
    directory = _SPEAKERS_DIR / name
    stem = f"{sample_id:03d}"
    audio_path = directory / f"{stem}.wav"
    embedding_path = directory / f"{stem}.npy"
    existing_paths = [
        path for path in (audio_path, embedding_path) if path.exists()
    ]
    updated_at = max(
        (path.stat().st_mtime for path in existing_paths),
        default=0.0,
    )
    return {
        "sample_id": sample_id,
        "sample_key": stem,
        "audio_filename": audio_path.name,
        "embedding_filename": embedding_path.name,
        "audio_path": str(audio_path),
        "embedding_path": str(embedding_path),
        "audio_url": f"/api/v1/speakers/{name}/samples/{sample_id}/audio",
        "audio_available": audio_path.exists(),
        "embedding_available": embedding_path.exists(),
        "ready": audio_path.exists() and embedding_path.exists(),
        "audio_size_bytes": (
            audio_path.stat().st_size if audio_path.exists() else 0
        ),
        "updated_at": updated_at,
    }


def _sample_capacity_error(
    name: str,
    registry: dict[str, Any],
) -> dict[str, Any] | None:
    sample_count = _speaker_sample_count(name, registry)
    if sample_count >= MAX_SAMPLES_PER_SPEAKER:
        return {
            "ok": False,
            "status": 409,
            "code": "speaker_sample_limit_reached",
            "error": (
                f"单人声纹样本已达到上限 "
                f"{MAX_SAMPLES_PER_SPEAKER} 个"
            ),
            "name": name,
            "shots": sample_count,
            "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
        }
    return None


@dataclass
class SpeakerEnrollment:
    name: str
    required_shots: int
    started_at: float
    current_step: int = 1
    shots_collected: int = 0
    embeddings: list[np.ndarray] = field(default_factory=list)
    samples: list[_PreparedSpeakerSample] = field(default_factory=list)
    done: bool = False


@dataclass(frozen=True)
class _PreparedSpeakerSample:
    embedding: np.ndarray
    wav_bytes: bytes
    source_sample_rate: int
    source_duration_ms: float
    speech_duration_ms: float
    segment_count: int


class SpeakerEnrollmentManager:
    """Own voice-print sessions; no face or camera state exists here."""

    def __init__(
        self,
        cross_identity_similarity_threshold: float | None = 0.75,
        same_identity_similarity_threshold: float | None = 0.5,
    ) -> None:
        self._extractor: Any = None
        self._session: SpeakerEnrollment | None = None
        self._storage_lock = threading.RLock()
        self._same_identity_similarity_threshold = same_identity_similarity_threshold
        if (
            same_identity_similarity_threshold is not None
            and not 0.0 <= same_identity_similarity_threshold <= 1.0
        ):
            raise ValueError("same_identity_similarity_threshold 必须在 0.0 到 1.0 之间")
        self._cross_identity_similarity_threshold = (
            None
            if cross_identity_similarity_threshold is None
            else float(cross_identity_similarity_threshold)
        )
        if (
            self._cross_identity_similarity_threshold is not None
            and not 0.0 <= self._cross_identity_similarity_threshold <= 1.0
        ):
            raise ValueError(
                "cross_identity_similarity_threshold 必须在 0.0 到 1.0 之间"
            )
        _SPEAKERS_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def speaker_session(self) -> SpeakerEnrollment | None:
        return self._session

    def set_speaker_extractor(self, extractor: Any) -> None:
        self._extractor = extractor

    def start_speaker(
        self,
        name: str,
        required_shots: int = 3,
    ) -> dict[str, Any]:
        try:
            name = validate_speaker_identity(name)
        except ValueError as exc:
            return {
                "ok": False,
                "status": 422,
                "code": "invalid_speaker_identity",
                "error": str(exc),
                "allowed_names": list(ALLOWED_SPEAKER_IDENTITIES),
            }
        with self._storage_lock:
            capacity_error = _capacity_error(name, _load_registry())
            if capacity_error is not None:
                return capacity_error
        required = int(required_shots)
        if required < 1 or required > MAX_SAMPLES_PER_SPEAKER:
            return {
                "ok": False,
                "status": 422,
                "code": "invalid_required_shots",
                "error": (
                    "required_shots 必须在 1 到 "
                    f"{MAX_SAMPLES_PER_SPEAKER} 之间"
                ),
                "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
            }
        with self._storage_lock:
            current_count = _speaker_sample_count(name)
            if current_count + required > MAX_SAMPLES_PER_SPEAKER:
                return {
                    "ok": False, "status": 409,
                    "code": "speaker_sample_limit_reached",
                    "error": "新增注册样本将超过单人 5 个样本上限",
                    "shots": current_count,
                }
        self._session = SpeakerEnrollment(name, required, time.time())
        return {
            "ok": True,
            "name": name,
            "step": 1,
            "total_steps": required,
            "text": ENROLL_SENTENCES[0],
        }

    def process_speaker_audio(
        self,
        audio_samples: np.ndarray,
        sample_rate: int,
        vad: Any | None = None,
    ) -> dict[str, Any]:
        session = self._session
        if session is None or session.done:
            return {"ok": False, "error": "没有进行中的声纹注册会话"}
        try:
            prepared = self._prepare_uploaded_sample(
                encode_pcm16_wav(audio_samples, sample_rate), vad,
            )
        except (ValueError, RuntimeError) as exc:
            return {
                "ok": False, "status": "retry", "done": False,
                "code": "invalid_audio", "error": str(exc),
                "step": session.current_step,
            }
        if prepared is None:
            return {
                "ok": True,
                "status": "retry",
                "step": session.current_step,
                "total_steps": session.required_shots,
                "done": False,
            }

        with self._storage_lock:
            conflict = self._cross_identity_conflict(session.name, prepared.embedding)
            if conflict is None:
                references = (
                    self.get_speaker_templates(session.name)
                    or session.embeddings[:1]
                )
                conflict = self._same_identity_conflict(prepared.embedding, references)
        if conflict is not None:
            return {**conflict, "done": False, "step": session.current_step}
        session.embeddings.append(prepared.embedding)
        session.samples.append(prepared)
        session.shots_collected += 1
        if session.shots_collected >= session.required_shots:
            result = self._commit_speaker_samples(session.name, session.samples)
            session.done = True
            return {
                **result, "done": True,
                "status": "done" if result.get("ok") else "failed",
            }

        session.current_step = session.shots_collected + 1
        return {
            "ok": True,
            "name": session.name,
            "status": "captured",
            "step": session.current_step,
            "total_steps": session.required_shots,
            "shots": session.shots_collected,
            "text": ENROLL_SENTENCES[
                min(session.current_step - 1, len(ENROLL_SENTENCES) - 1)
            ],
            "done": False,
        }

    def enroll_speaker_from_audio(
        self,
        name: str,
        audio_bytes: bytes,
        vad: Any | None = None,
    ) -> dict[str, Any]:
        """Validate and append one uploaded sample."""
        result = self.enroll_speaker_batch_from_audio(
            name,
            [audio_bytes],
            vad=vad,
        )
        if not result.get("ok"):
            return result
        sample = result["samples"][0]
        return {
            key: value
            for key, value in result.items()
            if key not in {"added_count", "sample_ids", "samples"}
        } | sample

    def enroll_speaker_batch_from_audio(
        self,
        name: str,
        audio_items: list[bytes],
        vad: Any | None = None,
    ) -> dict[str, Any]:
        """Validate a group of uploads, then append all of them together."""
        try:
            normalized_name = validate_speaker_identity(name)
        except ValueError as exc:
            return {
                "ok": False,
                "status": 422,
                "code": "invalid_speaker_identity",
                "error": str(exc),
                "allowed_names": list(ALLOWED_SPEAKER_IDENTITIES),
            }
        if not audio_items:
            return {
                "ok": False,
                "status": 400,
                "code": "empty_audio_batch",
                "error": "至少需要上传 1 个音频文件",
            }
        if len(audio_items) > MAX_SAMPLES_PER_SPEAKER:
            return {
                "ok": False,
                "status": 409,
                "code": "speaker_sample_limit_reached",
                "error": (
                    "单次上传不能超过 "
                    f"{MAX_SAMPLES_PER_SPEAKER} 个声纹样本"
                ),
                "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
            }

        with self._storage_lock:
            registry = _load_registry()
            capacity_error = _capacity_error(normalized_name, registry)
            if capacity_error is not None:
                return capacity_error
            current_count = _speaker_sample_count(normalized_name, registry)
            if current_count + len(audio_items) > MAX_SAMPLES_PER_SPEAKER:
                return {
                    "ok": False,
                    "status": 409,
                    "code": "speaker_sample_limit_reached",
                    "error": (
                        f"当前已有 {current_count} 个样本，本次上传 "
                        f"{len(audio_items)} 个将超过单人上限 "
                        f"{MAX_SAMPLES_PER_SPEAKER} 个"
                    ),
                    "name": normalized_name,
                    "shots": current_count,
                    "requested_count": len(audio_items),
                    "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
                }

        prepared_items: list[_PreparedSpeakerSample] = []
        for index, audio_bytes in enumerate(audio_items, start=1):
            try:
                prepared = self._prepare_uploaded_sample(audio_bytes, vad)
            except (RuntimeError, ValueError) as exc:
                return {
                    "ok": False,
                    "status": 422,
                    "code": "invalid_audio",
                    "error": str(exc),
                    "failed_file_index": index,
                }
            if prepared is None:
                return {
                    "ok": False,
                    "status": 422,
                    "code": "speaker_embedding_failed",
                    "error": "无法提取声纹",
                    "failed_file_index": index,
                }
            prepared_items.append(prepared)

        return self._commit_speaker_samples(normalized_name, prepared_items)

    def _commit_speaker_samples(
        self, normalized_name: str, prepared_items: list[_PreparedSpeakerSample],
    ) -> dict[str, Any]:
        """Shared append path for uploaded and live enrollment samples."""
        with self._storage_lock:
            registry = _load_registry()
            capacity_error = _capacity_error(normalized_name, registry)
            if capacity_error is not None:
                return capacity_error
            directory = _SPEAKERS_DIR / normalized_name
            existing_ids = _speaker_sample_ids(directory)
            if len(existing_ids) + len(prepared_items) > MAX_SAMPLES_PER_SPEAKER:
                return {
                    "ok": False,
                    "status": 409,
                    "code": "speaker_sample_limit_reached",
                    "error": (
                        f"当前已有 {len(existing_ids)} 个样本，本次上传 "
                        f"{len(prepared_items)} 个将超过单人上限 "
                        f"{MAX_SAMPLES_PER_SPEAKER} 个"
                    ),
                    "name": normalized_name,
                    "shots": len(existing_ids),
                    "requested_count": len(prepared_items),
                    "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
                }

            for index, prepared in enumerate(prepared_items, start=1):
                conflict = self._cross_identity_conflict(
                    normalized_name,
                    prepared.embedding,
                )
                if conflict is not None:
                    return {**conflict, "failed_file_index": index}

            occupied = set(existing_ids)
            allocated_ids: list[int] = []
            for _ in prepared_items:
                sample_id = next(
                    (
                        value
                        for value in range(1, MAX_SAMPLES_PER_SPEAKER + 1)
                        if value not in occupied
                    ),
                    None,
                )
                if sample_id is None:
                    raise RuntimeError("声纹样本编号分配失败")
                occupied.add(sample_id)
                allocated_ids.append(sample_id)

            try:
                existing_embeddings = [
                    np.asarray(
                        np.load(directory / f"{sample_id:03d}.npy"),
                        dtype=np.float32,
                    )
                    for sample_id in existing_ids
                ]
                all_embeddings = existing_embeddings + [
                    item.embedding for item in prepared_items
                ]
                references = existing_embeddings or [prepared_items[0].embedding]
                for index, item in enumerate(prepared_items, start=1):
                    conflict = self._same_identity_conflict(item.embedding, references)
                    if conflict is not None:
                        return {**conflict, "failed_file_index": index}
                shapes = {value.shape for value in all_embeddings}
                if len(shapes) != 1:
                    raise RuntimeError("声纹样本 embedding 维度不一致")
                centroid = np.mean(all_embeddings, axis=0)
            except (OSError, RuntimeError, ValueError) as exc:
                return {
                    "ok": False,
                    "status": 409,
                    "code": "speaker_sample_storage_inconsistent",
                    "error": str(exc),
                }

            directory.mkdir(parents=True, exist_ok=True)
            committed_paths: list[Path] = []
            try:
                for sample_id, prepared in zip(
                    allocated_ids,
                    prepared_items,
                    strict=True,
                ):
                    stem = f"{sample_id:03d}"
                    audio_path = directory / f"{stem}.wav"
                    embedding_path = directory / f"{stem}.npy"
                    committed_paths.extend([audio_path, embedding_path])
                    audio_path.write_bytes(prepared.wav_bytes)
                    np.save(embedding_path, prepared.embedding)
                _save_array_atomic(directory / "centroid.npy", centroid)
            except OSError as exc:
                for path in committed_paths:
                    path.unlink(missing_ok=True)
                if not existing_ids and directory.exists():
                    shutil.rmtree(directory)
                return {
                    "ok": False,
                    "status": 500,
                    "code": "speaker_sample_write_failed",
                    "error": str(exc),
                }

            shots = len(existing_ids) + len(prepared_items)
            previous_metadata = registry["speakers"].get(
                normalized_name,
                {},
            )
            now = time.time()
            registry["speakers"][normalized_name] = {
                "shots": shots,
                "enrolled_at": float(
                    previous_metadata.get("enrolled_at", now)
                ),
                "updated_at": now,
            }
            _save_registry(registry)

        samples_result = []
        for sample_id, prepared in zip(
            allocated_ids,
            prepared_items,
            strict=True,
        ):
            stem = f"{sample_id:03d}"
            samples_result.append({
                "sample_id": sample_id,
                "sample_key": stem,
                "audio_path": str(directory / f"{stem}.wav"),
                "embedding_path": str(directory / f"{stem}.npy"),
                "source_sample_rate": prepared.source_sample_rate,
                "stored_sample_rate": SPEAKER_SAMPLE_RATE,
                "stored_channels": 1,
                "stored_sample_width_bits": 16,
                "source_duration_ms": round(prepared.source_duration_ms, 2),
                "speech_duration_ms": round(prepared.speech_duration_ms, 2),
                "segment_count": prepared.segment_count,
                "audio_valid": True,
                "has_effective_speech": True,
            })
        return {
            "ok": True,
            "name": normalized_name,
            "shots": shots,
            "added_count": len(samples_result),
            "sample_ids": allocated_ids,
            "samples": samples_result,
            "max_speakers": MAX_SPEAKERS,
            "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
            "speaker_role": speaker_identity_role(normalized_name),
            "cross_identity_similarity_threshold": (
                self._cross_identity_similarity_threshold
            ),
        }

    def _prepare_uploaded_sample(
        self,
        audio_bytes: bytes,
        vad: Any | None,
    ) -> _PreparedSpeakerSample | None:
        if vad is None:
            source_samples, source_sample_rate = decode_pcm16_wav(audio_bytes)
            samples, sample_rate, stored_wav = normalize_pcm16_wav(
                audio_bytes,
                SPEAKER_SAMPLE_RATE,
            )
            source_duration_ms = (
                len(source_samples) / source_sample_rate * 1000.0
            )
            speech_duration_ms = source_duration_ms
            segment_count = 1
        else:
            trimmed = vad.trim_wav(audio_bytes)
            source_sample_rate = int(
                getattr(trimmed, "source_sample_rate", trimmed.sample_rate)
            )
            samples, sample_rate, stored_wav = normalize_pcm16_wav(
                trimmed.wav_bytes,
                SPEAKER_SAMPLE_RATE,
            )
            source_duration_ms = trimmed.source_duration_ms
            speech_duration_ms = trimmed.speech_duration_ms
            segment_count = trimmed.segment_count
        embedding = self._extract_embedding(samples, sample_rate)
        if embedding is None:
            return None
        return _PreparedSpeakerSample(
            embedding=embedding,
            wav_bytes=stored_wav,
            source_sample_rate=source_sample_rate,
            source_duration_ms=source_duration_ms,
            speech_duration_ms=speech_duration_ms,
            segment_count=segment_count,
        )

    def _cross_identity_conflict(
        self,
        name: str,
        embedding: np.ndarray,
    ) -> dict[str, Any] | None:
        threshold = self._cross_identity_similarity_threshold
        if threshold is None:
            return None
        best_name = ""
        best_sample_id = 0
        best_score = -1.0
        for candidate_name in sorted(_known_speaker_names()):
            if candidate_name == name:
                continue
            directory = _SPEAKERS_DIR / candidate_name
            for sample_id in _speaker_sample_ids(directory):
                path = directory / f"{sample_id:03d}.npy"
                if not path.exists():
                    continue
                try:
                    candidate = np.asarray(np.load(path), dtype=np.float32)
                    score = self._cosine_similarity(embedding, candidate)
                except (OSError, ValueError):
                    continue
                if score > best_score:
                    best_name = candidate_name
                    best_sample_id = sample_id
                    best_score = score
        if best_name and best_score >= threshold:
            return {
                "ok": False,
                "status": 409,
                "code": "speaker_identity_conflict",
                "error": (
                    f"上传声纹与已注册身份 {best_name} 高度相似，"
                    "不能注册到不同身份"
                ),
                "name": name,
                "conflicting_speaker": best_name,
                "conflicting_sample_id": best_sample_id,
                "similarity": round(best_score, 4),
                "similarity_threshold": threshold,
            }
        return None

    @staticmethod
    def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
        left = np.asarray(left, dtype=np.float32).reshape(-1)
        right = np.asarray(right, dtype=np.float32).reshape(-1)
        if left.shape != right.shape:
            return -1.0
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator == 0.0:
            return 0.0
        return float(np.dot(left, right) / denominator)

    def _same_identity_conflict(
        self, embedding: np.ndarray, references: list[np.ndarray],
    ) -> dict[str, Any] | None:
        threshold = self._same_identity_similarity_threshold
        if threshold is None or not references:
            return None
        score = max(
            self._cosine_similarity(embedding, value) for value in references
        )
        if not np.isfinite(score) or score < threshold:
            return {
                "ok": False, "status": 409,
                "code": "speaker_sample_inconsistent",
                "error": "新样本与该身份已有样本不一致，请确认说话人并重新录制",
                "similarity": round(score, 4) if np.isfinite(score) else None,
                "similarity_threshold": threshold,
            }
        return None

    def cancel_speaker(self) -> dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": "没有进行中的声纹注册会话"}
        name = self._session.name
        self._session = None
        return {"ok": True, "name": name, "cancelled": True}

    def _extract_embedding(
        self,
        samples: np.ndarray,
        sample_rate: int,
    ) -> np.ndarray | None:
        if self._extractor is None:
            return None
        samples = np.asarray(samples, dtype=np.float32).ravel()
        if sample_rate != 16000:
            import scipy.signal
            samples = scipy.signal.resample(
                samples, int(len(samples) * 16000 / sample_rate)
            ).astype(np.float32)
        if len(samples) < 8000:
            return None
        try:
            stream = self._extractor.create_stream()
            stream.accept_waveform(sample_rate=16000, waveform=samples)
            stream.input_finished()
            if not self._extractor.is_ready(stream):
                return None
            embedding = np.asarray(
                self._extractor.compute(stream), dtype=np.float32,
            ).reshape(-1)
            if (
                not embedding.size or not np.isfinite(embedding).all()
                or np.linalg.norm(embedding) == 0
            ):
                return None
            return embedding
        except Exception:
            return None

    @staticmethod
    def list_enrolled_speakers() -> list[str]:
        return sorted(_known_speaker_names())

    def list_speaker_records(self) -> dict[str, Any]:
        with self._storage_lock:
            registry = _load_registry()
            records: list[dict[str, Any]] = []
            for name in sorted(_known_speaker_names(registry)):
                directory = _SPEAKERS_DIR / name
                metadata = registry.get("speakers", {}).get(name, {})
                shots = _speaker_sample_count(name, registry)
                sample_ids = _speaker_sample_ids(directory)
                records.append({
                    "name": name,
                    "shots": shots,
                    "sample_ids": sample_ids,
                    "samples_url": f"/api/v1/speakers/{name}/samples",
                    "enrolled_at": float(metadata.get("enrolled_at", 0.0)),
                    "updated_at": float(metadata.get("updated_at", 0.0)),
                    "ready": (directory / "centroid.npy").exists(),
                    "role": speaker_identity_role(name),
                })
            occupied_names = {
                record["name"]
                for record in records
                if record["name"] in ALLOWED_SPEAKER_IDENTITIES
            }
            has_capacity = len(records) < MAX_SPEAKERS
            return {
                "ok": True,
                "count": len(records),
                "max_speakers": MAX_SPEAKERS,
                "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
                "max_batch_files": MAX_SAMPLES_PER_SPEAKER,
                "stored_sample_rate": SPEAKER_SAMPLE_RATE,
                "stored_channels": 1,
                "stored_sample_width_bits": 16,
                "cross_identity_similarity_threshold": (
                    self._cross_identity_similarity_threshold
                ),
                "allowed_names": list(ALLOWED_SPEAKER_IDENTITIES),
                "available_names": [
                    name
                    for name in ALLOWED_SPEAKER_IDENTITIES
                    if has_capacity and name not in occupied_names
                ],
                "speakers": records,
            }

    def list_speaker_samples(self, name: str) -> dict[str, Any]:
        try:
            normalized = validate_speaker_identity(name)
        except ValueError as exc:
            return {
                "ok": False,
                "status": 422,
                "code": "invalid_speaker_identity",
                "error": str(exc),
                "allowed_names": list(ALLOWED_SPEAKER_IDENTITIES),
            }
        with self._storage_lock:
            registry = _load_registry()
            if normalized not in _known_speaker_names(registry):
                return {
                    "ok": False,
                    "status": 404,
                    "error": "speaker not found",
                }
            directory = _SPEAKERS_DIR / normalized
            sample_ids = _speaker_sample_ids(directory)
            return {
                "ok": True,
                "name": normalized,
                "role": speaker_identity_role(normalized),
                "shots": len(sample_ids),
                "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
                "sample_ids": sample_ids,
                "samples": [
                    _sample_record(normalized, sample_id)
                    for sample_id in sample_ids
                ],
            }

    def get_speaker_sample(
        self,
        name: str,
        sample_id: int,
    ) -> dict[str, Any]:
        try:
            normalized = validate_speaker_identity(name)
            normalized_sample_id = _validate_sample_id(sample_id)
        except ValueError as exc:
            return {
                "ok": False,
                "status": 422,
                "error": str(exc),
            }
        with self._storage_lock:
            directory = _SPEAKERS_DIR / normalized
            if normalized_sample_id not in _speaker_sample_ids(directory):
                return {
                    "ok": False,
                    "status": 404,
                    "error": "speaker sample not found",
                }
            return {
                "ok": True,
                "name": normalized,
                "role": speaker_identity_role(normalized),
                **_sample_record(normalized, normalized_sample_id),
            }

    def replace_speaker_sample(
        self,
        name: str,
        sample_id: int,
        audio_bytes: bytes,
        vad: Any | None = None,
    ) -> dict[str, Any]:
        try:
            normalized = validate_speaker_identity(name)
            normalized_sample_id = _validate_sample_id(sample_id)
            with self._storage_lock:
                directory = _SPEAKERS_DIR / normalized
                if normalized_sample_id not in _speaker_sample_ids(directory):
                    return {
                        "ok": False,
                        "status": 404,
                        "error": "speaker sample not found",
                    }
            prepared = self._prepare_uploaded_sample(audio_bytes, vad)
        except (RuntimeError, ValueError) as exc:
            return {
                "ok": False,
                "status": 422,
                "code": "invalid_audio",
                "error": str(exc),
            }

        if prepared is None:
            return {
                "ok": False,
                "status": 422,
                "code": "speaker_embedding_failed",
                "error": "无法提取声纹",
            }

        with self._storage_lock:
            registry = _load_registry()
            directory = _SPEAKERS_DIR / normalized
            sample_ids = _speaker_sample_ids(directory)
            if normalized_sample_id not in sample_ids:
                return {
                    "ok": False,
                    "status": 404,
                    "error": "speaker sample not found",
                }
            conflict = self._cross_identity_conflict(
                normalized,
                prepared.embedding,
            )
            if conflict is not None:
                return conflict
            try:
                references = [
                    np.asarray(np.load(directory / f"{value:03d}.npy"), dtype=np.float32)
                    for value in sample_ids if value != normalized_sample_id
                ]
                conflict = self._same_identity_conflict(prepared.embedding, references)
                if conflict is not None:
                    return conflict
                centroid = _mean_sample_embeddings(
                    directory,
                    sample_ids,
                    replacement=(normalized_sample_id, prepared.embedding),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                return {
                    "ok": False,
                    "status": 409,
                    "code": "speaker_sample_storage_inconsistent",
                    "error": str(exc),
                }

            stem = f"{normalized_sample_id:03d}"
            audio_path = directory / f"{stem}.wav"
            embedding_path = directory / f"{stem}.npy"
            audio_temporary = directory / f".{stem}.wav.tmp"
            embedding_temporary = directory / f".{stem}.npy.tmp"
            try:
                audio_temporary.write_bytes(prepared.wav_bytes)
                with embedding_temporary.open("wb") as stream:
                    np.save(stream, prepared.embedding)
                audio_temporary.replace(audio_path)
                embedding_temporary.replace(embedding_path)
                _save_array_atomic(directory / "centroid.npy", centroid)
            finally:
                audio_temporary.unlink(missing_ok=True)
                embedding_temporary.unlink(missing_ok=True)

            metadata = registry["speakers"].get(normalized, {})
            now = time.time()
            registry["speakers"][normalized] = {
                **metadata,
                "shots": len(sample_ids),
                "enrolled_at": float(metadata.get("enrolled_at", now)),
                "updated_at": now,
            }
            _save_registry(registry)

        return {
            "ok": True,
            "name": normalized,
            "speaker_role": speaker_identity_role(normalized),
            "shots": len(sample_ids),
            "sample_id": normalized_sample_id,
            "sample_key": stem,
            "replaced": True,
            "audio_path": str(audio_path),
            "embedding_path": str(embedding_path),
            "source_sample_rate": prepared.source_sample_rate,
            "stored_sample_rate": SPEAKER_SAMPLE_RATE,
            "stored_channels": 1,
            "stored_sample_width_bits": 16,
            "source_duration_ms": round(prepared.source_duration_ms, 2),
            "speech_duration_ms": round(prepared.speech_duration_ms, 2),
            "segment_count": prepared.segment_count,
            "audio_valid": True,
            "has_effective_speech": True,
        }

    def delete_speaker(self, name: str) -> dict[str, Any]:
        """Delete every sample belonging to one fixed identity."""
        try:
            normalized = validate_speaker_identity(name)
        except ValueError as exc:
            return {
                "ok": False,
                "status": 422,
                "code": "invalid_speaker_identity",
                "error": str(exc),
                "allowed_names": list(ALLOWED_SPEAKER_IDENTITIES),
            }
        with self._storage_lock:
            registry = _load_registry()
            directory = _SPEAKERS_DIR / normalized
            sample_ids = _speaker_sample_ids(directory)
            if normalized not in _known_speaker_names(registry):
                return {
                    "ok": False,
                    "status": 404,
                    "code": "speaker_not_found",
                    "error": "speaker not found",
                }
            if directory.exists():
                shutil.rmtree(directory)
            registry["speakers"].pop(normalized, None)
            _save_registry(registry)
            if self._session is not None and self._session.name == normalized:
                self._session = None
        return {
            "ok": True,
            "name": normalized,
            "speaker_role": speaker_identity_role(normalized),
            "deleted_sample_ids": sample_ids,
            "deleted_count": len(sample_ids),
            "speaker_removed": True,
        }

    def delete_all_speakers(self) -> dict[str, Any]:
        """Delete all fixed-identity speaker data, preserving unrelated data."""
        with self._storage_lock:
            registry = _load_registry()
            names = sorted(_known_speaker_names(registry))
            deleted_sample_count = 0
            for name in names:
                directory = _SPEAKERS_DIR / name
                deleted_sample_count += len(_speaker_sample_ids(directory))
                if directory.exists():
                    shutil.rmtree(directory)
                registry["speakers"].pop(name, None)
            _save_registry(registry)
            if self._session is not None and self._session.name in names:
                self._session = None
        return {
            "ok": True,
            "deleted_speakers": names,
            "deleted_speaker_count": len(names),
            "deleted_sample_count": deleted_sample_count,
        }

    def delete_speaker_sample(
        self,
        name: str,
        sample_id: int,
    ) -> dict[str, Any]:
        try:
            normalized = validate_speaker_identity(name)
            normalized_sample_id = _validate_sample_id(sample_id)
        except ValueError as exc:
            return {
                "ok": False,
                "status": 422,
                "error": str(exc),
            }
        with self._storage_lock:
            registry = _load_registry()
            directory = _SPEAKERS_DIR / normalized
            sample_ids = _speaker_sample_ids(directory)
            if normalized_sample_id not in sample_ids:
                return {
                    "ok": False,
                    "status": 404,
                    "error": "speaker sample not found",
                }
            remaining_ids = [
                value
                for value in sample_ids
                if value != normalized_sample_id
            ]
            centroid: np.ndarray | None = None
            if remaining_ids:
                try:
                    centroid = _mean_sample_embeddings(
                        directory,
                        remaining_ids,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    return {
                        "ok": False,
                        "status": 409,
                        "code": "speaker_sample_storage_inconsistent",
                        "error": str(exc),
                    }

            stem = f"{normalized_sample_id:03d}"
            (directory / f"{stem}.wav").unlink(missing_ok=True)
            (directory / f"{stem}.npy").unlink(missing_ok=True)
            if not remaining_ids:
                if directory.exists():
                    shutil.rmtree(directory)
                registry["speakers"].pop(normalized, None)
                speaker_removed = True
            else:
                assert centroid is not None
                _save_array_atomic(directory / "centroid.npy", centroid)
                metadata = registry["speakers"].get(normalized, {})
                now = time.time()
                registry["speakers"][normalized] = {
                    **metadata,
                    "shots": len(remaining_ids),
                    "enrolled_at": float(metadata.get("enrolled_at", now)),
                    "updated_at": now,
                }
                speaker_removed = False
            _save_registry(registry)
        return {
            "ok": True,
            "name": normalized,
            "speaker_role": speaker_identity_role(normalized),
            "deleted_sample_id": normalized_sample_id,
            "deleted_sample_key": stem,
            "shots": len(remaining_ids),
            "remaining_sample_ids": remaining_ids,
            "speaker_removed": speaker_removed,
            "centroid_recomputed": bool(remaining_ids),
        }

    @staticmethod
    def get_speaker_centroid(name: str) -> np.ndarray | None:
        try:
            normalized = validate_speaker_identity(name)
        except ValueError:
            return None
        path = _SPEAKERS_DIR / normalized / "centroid.npy"
        return np.load(path) if path.exists() else None

    def get_speaker_templates(self, name: str) -> list[np.ndarray]:
        """Return every per-sample embedding for a speaker (multi-template)."""
        try:
            normalized = validate_speaker_identity(name)
        except ValueError:
            return []
        directory = _SPEAKERS_DIR / normalized
        if not directory.exists():
            return []
        values: list[np.ndarray] = []
        for sample_id in _speaker_sample_ids(directory):
            path = directory / f"{sample_id:03d}.npy"
            if not path.exists():
                continue
            try:
                values.append(np.asarray(np.load(path), dtype=np.float32))
            except (OSError, ValueError):
                continue
        if not values:
            centroid = self.get_speaker_centroid(normalized)
            if centroid is not None:
                return [np.asarray(centroid, dtype=np.float32)]
        return values

    def sync_to_provider(self, provider: Any) -> int:
        """Push every enrolled template into the runtime speaker provider.

        Prefers the multi-template ``set_templates`` interface (real sherpa
        provider); falls back to the in-memory mock store for test/demo runs.
        Returns the number of speakers registered.
        """
        set_templates = getattr(provider, "set_templates", None)
        if callable(set_templates):
            templates: dict[str, list[np.ndarray]] = {}
            for name in self.list_enrolled_speakers():
                values = self.get_speaker_templates(name)
                if values:
                    templates[name] = values
            set_templates(templates)
            return len(templates)

        mock_store = getattr(provider, "_enrolled", None)
        if isinstance(mock_store, dict):
            stored_names = set(self.list_enrolled_speakers())
            for name in set(mock_store) - stored_names:
                mock_store.pop(name, None)
            for name in stored_names:
                mock_store[name] = {"migrated": True}
            return len(mock_store)
        return 0
