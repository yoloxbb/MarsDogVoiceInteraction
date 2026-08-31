"""Speaker enrollment and voice-print storage."""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from marsdog_voice_interaction.messages.speaker_identity import (
    ALLOWED_SPEAKER_IDENTITIES,
    speaker_identity_role,
    validate_speaker_identity,
)
from marsdog_voice_interaction.utils.uploaded_audio import decode_pcm16_wav


_STORAGE_ROOT = Path("data")
_SPEAKERS_DIR = _STORAGE_ROOT / "speakers"
_REGISTRY_PATH = _STORAGE_ROOT / "speaker_registry.json"
MAX_SPEAKERS = len(ALLOWED_SPEAKER_IDENTITIES)
MAX_SAMPLES_PER_SPEAKER = 5

ENROLL_SENTENCES = (
    "你好小狗，很高兴认识你",
    "今天天气不错，我们一起玩吧",
    "记住我的声音，以后听我的指令",
)

_UNSAFE_NAME_RUN = re.compile(r"[^\w-]+", re.UNICODE)
_UNDERSCORE_RUN = re.compile(r"_+")


def normalize_speaker_name(value: str, max_length: int = 64) -> str:
    """Return a stable directory-safe speaker name while preserving Unicode."""
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    normalized = _UNSAFE_NAME_RUN.sub("_", normalized)
    normalized = _UNDERSCORE_RUN.sub("_", normalized).strip("_-")
    normalized = normalized[:max(1, int(max_length))].rstrip("_-")
    if not normalized or normalized in {".", ".."}:
        raise ValueError("名称不能为空或仅包含非法字符")
    return normalized


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
    """Return the union of registered names and actual speaker directories."""
    value = registry if registry is not None else _load_registry()
    names = {
        str(name)
        for name in value.get("speakers", {})
        if str(name).strip()
    }
    if _SPEAKERS_DIR.exists():
        names.update(
            item.name
            for item in _SPEAKERS_DIR.iterdir()
            if item.is_dir() and not item.name.startswith(".")
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
    stored_count = (
        len(list(directory.glob("[0-9][0-9][0-9].npy")))
        if directory.exists()
        else 0
    )
    metadata = value.get("speakers", {}).get(name, {})
    registered_count = int(metadata.get("shots", 0))
    return max(stored_count, registered_count)


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
    done: bool = False


class SpeakerEnrollmentManager:
    """Own voice-print sessions; no face or camera state exists here."""

    def __init__(self) -> None:
        self._extractor: Any = None
        self._session: SpeakerEnrollment | None = None
        self._storage_lock = threading.RLock()
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
    ) -> dict[str, Any]:
        session = self._session
        if session is None or session.done:
            return {"ok": False, "error": "没有进行中的声纹注册会话"}
        embedding = self._extract_embedding(audio_samples, sample_rate)
        if embedding is None:
            return {
                "ok": True,
                "status": "retry",
                "step": session.current_step,
                "total_steps": session.required_shots,
                "done": False,
            }

        session.embeddings.append(embedding)
        session.shots_collected += 1
        if session.shots_collected >= session.required_shots:
            with self._storage_lock:
                registry = _load_registry()
                capacity_error = _capacity_error(session.name, registry)
                if capacity_error is not None:
                    session.done = True
                    return {**capacity_error, "done": True}
                session.done = True
                directory = _SPEAKERS_DIR / session.name
                directory.mkdir(parents=True, exist_ok=True)
                for index, value in enumerate(session.embeddings, start=1):
                    np.save(directory / f"{index:03d}.npy", value)
                np.save(
                    directory / "centroid.npy",
                    np.mean(session.embeddings, axis=0),
                )
                registry["speakers"][session.name] = {
                    "shots": session.shots_collected,
                    "enrolled_at": time.time(),
                }
                _save_registry(registry)
            return {
                "ok": True,
                "name": session.name,
                "status": "done",
                "shots": session.shots_collected,
                "done": True,
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
        try:
            normalized_name = validate_speaker_identity(name)
            with self._storage_lock:
                registry = _load_registry()
                capacity_error = _capacity_error(
                    normalized_name,
                    registry,
                )
                if capacity_error is not None:
                    return capacity_error
                sample_error = _sample_capacity_error(
                    normalized_name,
                    registry,
                )
                if sample_error is not None:
                    return sample_error
            if vad is None:
                samples, sample_rate = decode_pcm16_wav(audio_bytes)
                source_duration_ms = len(samples) / sample_rate * 1000.0
                speech_duration_ms = source_duration_ms
                segment_count = 1
                stored_wav = audio_bytes
            else:
                trimmed = vad.trim_wav(audio_bytes)
                samples = trimmed.samples
                sample_rate = trimmed.sample_rate
                source_duration_ms = trimmed.source_duration_ms
                speech_duration_ms = trimmed.speech_duration_ms
                segment_count = trimmed.segment_count
                stored_wav = trimmed.wav_bytes
        except (RuntimeError, ValueError) as exc:
            result: dict[str, Any] = {"ok": False, "error": str(exc)}
            if isinstance(exc, ValueError) and str(exc).startswith("声纹身份只能是"):
                result.update({
                    "status": 422,
                    "code": "invalid_speaker_identity",
                    "allowed_names": list(ALLOWED_SPEAKER_IDENTITIES),
                })
            return result

        embedding = self._extract_embedding(samples, sample_rate)
        if embedding is None:
            return {"ok": False, "error": "无法提取声纹"}

        with self._storage_lock:
            registry = _load_registry()
            capacity_error = _capacity_error(normalized_name, registry)
            if capacity_error is not None:
                return capacity_error
            sample_error = _sample_capacity_error(normalized_name, registry)
            if sample_error is not None:
                return sample_error
            directory = _SPEAKERS_DIR / normalized_name
            directory.mkdir(parents=True, exist_ok=True)
            shots = _speaker_sample_count(normalized_name, registry) + 1
            stem = f"{shots:03d}"
            embedding_path = directory / f"{stem}.npy"
            audio_path = directory / f"{stem}.wav"
            audio_path.write_bytes(stored_wav)
            np.save(embedding_path, embedding)
            all_embeddings = [
                np.load(item)
                for item in sorted(directory.glob("[0-9][0-9][0-9].npy"))
            ]
            np.save(
                directory / "centroid.npy",
                np.mean(all_embeddings, axis=0),
            )
            registry["speakers"][normalized_name] = {
                "shots": shots,
                "enrolled_at": time.time(),
            }
            _save_registry(registry)
        return {
            "ok": True,
            "name": normalized_name,
            "shots": shots,
            "audio_path": str(audio_path),
            "embedding_path": str(embedding_path),
            "source_duration_ms": round(source_duration_ms, 2),
            "speech_duration_ms": round(speech_duration_ms, 2),
            "segment_count": segment_count,
            "audio_valid": True,
            "has_effective_speech": True,
            "max_speakers": MAX_SPEAKERS,
            "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
            "speaker_role": speaker_identity_role(normalized_name),
        }

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
            return np.asarray(self._extractor.compute(stream), dtype=np.float32)
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
                records.append({
                    "name": name,
                    "shots": shots,
                    "enrolled_at": float(metadata.get("enrolled_at", 0.0)),
                    "ready": (directory / "centroid.npy").exists(),
                    "role": speaker_identity_role(name),
                    "legacy": name not in ALLOWED_SPEAKER_IDENTITIES,
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
                "legacy_count": sum(
                    1 for record in records if record["legacy"]
                ),
                "max_speakers": MAX_SPEAKERS,
                "max_samples_per_speaker": MAX_SAMPLES_PER_SPEAKER,
                "allowed_names": list(ALLOWED_SPEAKER_IDENTITIES),
                "available_names": [
                    name
                    for name in ALLOWED_SPEAKER_IDENTITIES
                    if has_capacity and name not in occupied_names
                ],
                "speakers": records,
            }

    @staticmethod
    def get_speaker_centroid(name: str) -> np.ndarray | None:
        try:
            normalized = normalize_speaker_name(name)
        except ValueError:
            return None
        path = _SPEAKERS_DIR / normalized / "centroid.npy"
        return np.load(path) if path.exists() else None

    def rename_speaker(self, name: str, new_name: str) -> dict[str, Any]:
        try:
            source_name = normalize_speaker_name(name)
            target_name = validate_speaker_identity(new_name)
        except ValueError as exc:
            result: dict[str, Any] = {
                "ok": False,
                "status": 422,
                "error": str(exc),
            }
            if str(exc).startswith("声纹身份只能是"):
                result.update({
                    "code": "invalid_speaker_identity",
                    "allowed_names": list(ALLOWED_SPEAKER_IDENTITIES),
                })
            return result
        with self._storage_lock:
            registry = _load_registry()
            names = _known_speaker_names(registry)
            if source_name not in names:
                return {
                    "ok": False,
                    "status": 404,
                    "error": "speaker not found",
                }
            if source_name == target_name:
                return {
                    "ok": True,
                    "name": source_name,
                    "previous_name": source_name,
                    "changed": False,
                }
            if target_name in names:
                return {
                    "ok": False,
                    "status": 409,
                    "error": "目标名称已存在",
                }

            source = _SPEAKERS_DIR / source_name
            target = _SPEAKERS_DIR / target_name
            if source.exists():
                source.rename(target)
            metadata = registry["speakers"].pop(source_name, {})
            registry["speakers"][target_name] = metadata
            _save_registry(registry)
        return {
            "ok": True,
            "name": target_name,
            "previous_name": source_name,
            "changed": True,
        }

    def delete_speaker(self, name: str) -> dict[str, Any]:
        try:
            normalized = normalize_speaker_name(name)
        except ValueError:
            return {"ok": False, "status": 404, "error": "speaker not found"}
        with self._storage_lock:
            registry = _load_registry()
            target = _SPEAKERS_DIR / normalized
            if (
                not target.exists()
                and normalized not in registry.get("speakers", {})
            ):
                return {
                    "ok": False,
                    "status": 404,
                    "error": "speaker not found",
                }
            if target.exists():
                shutil.rmtree(target)
            registry["speakers"].pop(normalized, None)
            _save_registry(registry)
        return {"ok": True, "name": normalized}

    def sync_to_provider(self, provider: Any) -> int:
        manager = getattr(provider, "_manager", None)
        if manager is None:
            mock_store = getattr(provider, "_enrolled", None)
            if isinstance(mock_store, dict):
                stored_names = set(self.list_enrolled_speakers())
                for name in set(mock_store) - stored_names:
                    mock_store.pop(name, None)
                for name in stored_names:
                    mock_store[name] = {"migrated": True}
                return len(mock_store)
            return 0
        count = 0
        stored_names = set(self.list_enrolled_speakers())
        existing_names = set(getattr(manager, "all_speakers", []))
        for name in existing_names - stored_names:
            manager.remove(name=name)
        for name in stored_names:
            centroid = self.get_speaker_centroid(name)
            if centroid is None:
                continue
            if name in set(getattr(manager, "all_speakers", [])):
                manager.remove(name=name)
            if manager.add(name=name, v=centroid.tolist()):
                count += 1
        return count
