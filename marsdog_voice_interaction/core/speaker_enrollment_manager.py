"""Speaker enrollment and voice-print storage."""

from __future__ import annotations

import io
import json
import shutil
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


_STORAGE_ROOT = Path("data")
_SPEAKERS_DIR = _STORAGE_ROOT / "speakers"
_REGISTRY_PATH = _STORAGE_ROOT / "speaker_registry.json"

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
        name = name.strip()
        if not name:
            return {"ok": False, "error": "名称不能为空"}
        required = max(1, int(required_shots))
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
            session.done = True
            directory = _SPEAKERS_DIR / session.name
            directory.mkdir(parents=True, exist_ok=True)
            for index, value in enumerate(session.embeddings, start=1):
                np.save(directory / f"{index:03d}.npy", value)
            np.save(directory / "centroid.npy", np.mean(session.embeddings, axis=0))
            registry = _load_registry()
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
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            return {"ok": False, "error": "名称不能为空"}
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as source:
                sample_rate = source.getframerate()
                samples = np.frombuffer(
                    source.readframes(source.getnframes()), dtype=np.int16
                ).astype(np.float32) / 32768.0
        except Exception as exc:
            return {"ok": False, "error": f"无法解析 WAV: {exc}"}
        if len(samples) < sample_rate // 2:
            return {"ok": False, "error": "音频太短"}
        embedding = self._extract_embedding(samples, sample_rate)
        if embedding is None:
            return {"ok": False, "error": "无法提取声纹"}

        directory = _SPEAKERS_DIR / name
        directory.mkdir(parents=True, exist_ok=True)
        shots = len(list(directory.glob("[0-9][0-9][0-9].npy"))) + 1
        path = directory / f"{shots:03d}.npy"
        np.save(path, embedding)
        all_embeddings = [
            np.load(item)
            for item in sorted(directory.glob("[0-9][0-9][0-9].npy"))
        ]
        np.save(directory / "centroid.npy", np.mean(all_embeddings, axis=0))
        registry = _load_registry()
        registry["speakers"][name] = {
            "shots": shots,
            "enrolled_at": time.time(),
        }
        _save_registry(registry)
        return {"ok": True, "name": name, "shots": shots, "path": str(path)}

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
        return sorted(_load_registry()["speakers"])

    @staticmethod
    def get_speaker_centroid(name: str) -> np.ndarray | None:
        path = _SPEAKERS_DIR / name / "centroid.npy"
        return np.load(path) if path.exists() else None

    @staticmethod
    def delete_speaker(name: str) -> dict[str, Any]:
        name = name.strip()
        target = _SPEAKERS_DIR / name
        if not name or not target.exists():
            return {"ok": False, "status": 404, "error": "speaker not found"}
        shutil.rmtree(target)
        registry = _load_registry()
        registry["speakers"].pop(name, None)
        _save_registry(registry)
        return {"ok": True, "name": name}

    def sync_to_provider(self, provider: Any) -> int:
        manager = getattr(provider, "_manager", None)
        if manager is None:
            mock_store = getattr(provider, "_enrolled", None)
            if isinstance(mock_store, dict):
                for name in self.list_enrolled_speakers():
                    mock_store[name] = {"migrated": True}
                return len(mock_store)
            return 0
        count = 0
        for name in self.list_enrolled_speakers():
            centroid = self.get_speaker_centroid(name)
            if centroid is not None and manager.add(name=name, v=centroid.tolist()):
                count += 1
        return count
