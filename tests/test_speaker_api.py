from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import pytest

import marsdog_voice_interaction.core.speaker_enrollment_manager as storage
from marsdog_voice_interaction.api.speaker_api import SpeakerApiServer
from marsdog_voice_interaction.core.speaker_enrollment_manager import (
    SpeakerEnrollmentManager,
    normalize_speaker_name,
    set_storage_root,
)
from marsdog_voice_interaction.messages.speaker_identity import (
    ALLOWED_SPEAKER_IDENTITIES,
)
from marsdog_voice_interaction.utils.uploaded_audio import (
    UploadedAudioVAD,
    decode_pcm16_wav,
    encode_pcm16_wav,
)


@dataclass
class _Segment:
    start: int
    samples: list[float]


class _Detector:
    def __init__(self, start: int = 8000, length: int = 8000) -> None:
        self._start = start
        self._length = length
        self._segments: list[_Segment] = []

    def reset(self) -> None:
        self._segments.clear()

    def accept_waveform(self, samples: list[float]) -> None:
        del samples

    def flush(self) -> None:
        self._segments.append(
            _Segment(self._start, [0.2] * self._length)
        )

    def empty(self) -> bool:
        return not self._segments

    @property
    def front(self) -> _Segment:
        return self._segments[0]

    def pop(self) -> None:
        self._segments.pop(0)


class _Stream:
    def accept_waveform(self, sample_rate: int, waveform: np.ndarray) -> None:
        assert sample_rate == 16000
        self.waveform = waveform

    def input_finished(self) -> None:
        pass


class _Extractor:
    def create_stream(self) -> _Stream:
        return _Stream()

    def is_ready(self, stream: _Stream) -> bool:
        return len(stream.waveform) >= 8000

    def compute(self, stream: _Stream) -> list[float]:
        return [float(np.mean(np.abs(stream.waveform))), 1.0]


class _EmbeddingManager:
    def __init__(self) -> None:
        self.values: dict[str, list[float]] = {"张_三": [0.0, 0.0]}

    @property
    def all_speakers(self) -> list[str]:
        return list(self.values)

    def remove(self, name: str) -> bool:
        self.values.pop(name, None)
        return True

    def add(self, name: str, v: list[float]) -> bool:
        self.values[name] = v
        return True


class _SpeakerProvider:
    def __init__(self) -> None:
        self._manager = _EmbeddingManager()


@pytest.fixture
def isolated_speaker_storage(tmp_path: Path):  # type: ignore[no-untyped-def]
    original = (
        storage._STORAGE_ROOT,
        storage._SPEAKERS_DIR,
        storage._REGISTRY_PATH,
    )
    set_storage_root(tmp_path)
    try:
        yield tmp_path
    finally:
        storage._STORAGE_ROOT = original[0]
        storage._SPEAKERS_DIR = original[1]
        storage._REGISTRY_PATH = original[2]


def _wav(duration_sec: float = 2.0, sample_rate: int = 16000) -> bytes:
    count = int(duration_sec * sample_rate)
    samples = np.sin(
        2 * np.pi * 220 * np.arange(count, dtype=np.float32) / sample_rate
    ) * 0.2
    return encode_pcm16_wav(samples, sample_rate)


def _vad(detector: _Detector | None = None) -> UploadedAudioVAD:
    return UploadedAudioVAD(
        {
            "sample_rate": 16000,
            "pre_roll_sec": 0,
            "upload_post_roll_sec": 0,
            "upload_join_silence_sec": 0,
            "upload_min_speech_sec": 0.5,
        },
        detector=detector or _Detector(),
    )


def _post(
    server: SpeakerApiServer,
    *,
    name: str,
    filename: str = "sample.wav",
    payload: bytes | None = None,
    extra_data: dict[str, str] | None = None,
) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=server.create_app())
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            data = {"name": name}
            data.update(extra_data or {})
            return await client.post(
                "/api/v1/speakers",
                data=data,
                files={
                    "audio": (
                        filename,
                        payload if payload is not None else _wav(),
                        "audio/wav",
                    )
                },
            )

    return asyncio.run(request())


def _request(
    server: SpeakerApiServer,
    method: str,
    path: str,
    **kwargs: object,
) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=server.create_app())
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(request())


def test_speaker_name_is_unicode_safe_and_cannot_escape_storage() -> None:
    assert normalize_speaker_name(" ../../张 三 ") == "张_三"
    assert normalize_speaker_name(" Alice / Bob ") == "Alice_Bob"
    with pytest.raises(ValueError):
        normalize_speaker_name("../../")


def test_uploaded_wav_is_trimmed_to_vad_speech() -> None:
    result = _vad().trim_wav(_wav())
    decoded, sample_rate = decode_pcm16_wav(result.wav_bytes)

    assert sample_rate == 16000
    assert len(decoded) == 8000
    assert result.source_duration_ms == 2000.0
    assert result.speech_duration_ms == 500.0
    assert result.segment_count == 1


def test_uploaded_wav_without_vad_speech_is_rejected() -> None:
    with pytest.raises(ValueError, match="未检测到有效语音"):
        _vad(_Detector(length=0)).trim_wav(_wav())


def test_truncated_or_non_wav_audio_is_rejected_before_storage(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())

    corrupt = manager.enroll_speaker_from_audio(
        "owner",
        _wav()[:-100],
        vad=_vad(),
    )
    no_speech = manager.enroll_speaker_from_audio(
        "owner",
        _wav(),
        vad=_vad(_Detector(length=0)),
    )

    assert corrupt["ok"] is False
    assert "不完整" in corrupt["error"]
    assert no_speech["ok"] is False
    assert "未检测到有效语音" in no_speech["error"]
    assert list((isolated_speaker_storage / "speakers").iterdir()) == []


def test_upload_appends_audio_and_embedding_under_fixed_identity(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())

    first = manager.enroll_speaker_from_audio("owner", _wav(), vad=_vad())
    second = manager.enroll_speaker_from_audio("owner", _wav(), vad=_vad())

    directory = isolated_speaker_storage / "speakers" / "owner"
    assert first["ok"] is True
    assert second["shots"] == 2
    assert (directory / "001.wav").exists()
    assert (directory / "001.npy").exists()
    assert (directory / "002.wav").exists()
    assert (directory / "002.npy").exists()
    assert (directory / "centroid.npy").exists()
    assert SpeakerEnrollmentManager.list_enrolled_speakers() == ["owner"]

    provider = _SpeakerProvider()
    assert manager.sync_to_provider(provider) == 1
    assert provider._manager.values["owner"] != [0.0, 0.0]


def test_storage_exposes_exactly_five_identity_slots_and_crud_releases_one(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())

    for identity in ALLOWED_SPEAKER_IDENTITIES:
        result = manager.enroll_speaker_from_audio(
            identity,
            _wav(),
            vad=_vad(),
        )
        assert result["ok"] is True

    rejected = manager.enroll_speaker_from_audio(
        "family_member_5",
        _wav(),
        vad=_vad(),
    )
    existing = manager.enroll_speaker_from_audio(
        "owner",
        _wav(),
        vad=_vad(),
    )

    assert rejected["status"] == 422
    assert rejected["code"] == "invalid_speaker_identity"
    assert not (
        isolated_speaker_storage / "speakers" / "family_member_5"
    ).exists()
    assert existing["ok"] is True
    assert existing["shots"] == 2

    conflict = manager.rename_speaker("owner", "family_member_2")
    assert conflict["status"] == 409

    assert manager.delete_speaker("family_member_4")["ok"] is True
    admitted = manager.enroll_speaker_from_audio(
        "family_member_4",
        _wav(),
        vad=_vad(),
    )
    listing = manager.list_speaker_records()
    assert admitted["ok"] is True
    assert listing["count"] == 5
    assert listing["max_speakers"] == 5
    assert listing["max_samples_per_speaker"] == 5
    assert listing["allowed_names"] == list(ALLOWED_SPEAKER_IDENTITIES)
    assert listing["available_names"] == []
    assert {item["role"] for item in listing["speakers"]} == {
        "owner",
        "family",
    }


def test_legacy_name_is_unmaster_and_can_move_to_fixed_identity(
    isolated_speaker_storage: Path,
) -> None:
    legacy_directory = isolated_speaker_storage / "speakers" / "Alice"
    legacy_directory.mkdir(parents=True)
    np.save(legacy_directory / "001.npy", np.asarray([0.1, 1.0]))
    np.save(legacy_directory / "centroid.npy", np.asarray([0.1, 1.0]))
    storage._save_registry({
        "schema_version": 1,
        "speakers": {"Alice": {"shots": 1, "enrolled_at": 1.0}},
    })
    manager = SpeakerEnrollmentManager()

    before = manager.list_speaker_records()
    renamed = manager.rename_speaker("Alice", "owner")
    after = manager.list_speaker_records()

    assert before["legacy_count"] == 1
    assert before["speakers"][0]["role"] == "unmaster"
    assert before["speakers"][0]["legacy"] is True
    assert renamed["ok"] is True
    assert after["legacy_count"] == 0
    assert after["speakers"][0]["name"] == "owner"
    assert after["speakers"][0]["role"] == "owner"


def test_each_speaker_accepts_at_most_five_uploaded_samples(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())

    for expected_shots in range(1, 6):
        result = manager.enroll_speaker_from_audio(
            "owner",
            _wav(),
            vad=_vad(),
        )
        assert result["ok"] is True
        assert result["shots"] == expected_shots
        assert result["max_samples_per_speaker"] == 5

    rejected = manager.enroll_speaker_from_audio(
        "owner",
        _wav(),
        vad=_vad(),
    )
    directory = isolated_speaker_storage / "speakers" / "owner"

    assert rejected["status"] == 409
    assert rejected["code"] == "speaker_sample_limit_reached"
    assert rejected["shots"] == 5
    assert not (directory / "006.wav").exists()
    assert not (directory / "006.npy").exists()
    assert manager.list_speaker_records()["speakers"][0]["shots"] == 5


def test_live_enrollment_required_shots_cannot_exceed_five(
    isolated_speaker_storage: Path,
) -> None:
    del isolated_speaker_storage
    manager = SpeakerEnrollmentManager()

    rejected = manager.start_speaker("owner", required_shots=6)
    accepted = manager.start_speaker("owner", required_shots=5)

    assert rejected["status"] == 422
    assert rejected["code"] == "invalid_required_shots"
    assert accepted["ok"] is True
    assert accepted["total_steps"] == 5


def test_fastapi_accepts_selected_identity_and_wav() -> None:
    calls: list[tuple[str, bytes]] = []

    def handler(name: str, payload: bytes) -> dict[str, object]:
        calls.append((name, payload))
        return {"ok": True, "name": name, "shots": 1}

    server = SpeakerApiServer({}, handler)
    response = _post(
        server,
        name="owner",
    )

    assert response.status_code == 201
    assert response.json()["name"] == "owner"
    assert len(response.json()["request_id"]) == 32
    assert calls == [("owner", _wav())]


def test_fastapi_rejects_free_form_name_and_documents_enum() -> None:
    calls: list[str] = []
    server = SpeakerApiServer(
        {},
        lambda name, payload: calls.append(name) or {"ok": True},
    )

    rejected = _post(server, name="张三")
    schema = server.create_app().openapi()
    identity_schema = schema["components"]["schemas"]["SpeakerIdentity"]

    assert rejected.status_code == 422
    assert calls == []
    assert identity_schema["enum"] == list(ALLOWED_SPEAKER_IDENTITIES)


def test_fastapi_never_accepts_a_storage_path_from_upload(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())
    server = SpeakerApiServer(
        {},
        lambda name, payload: manager.enroll_speaker_from_audio(
            name,
            payload,
            vad=_vad(),
        ),
    )

    response = _post(
        server,
        name="owner",
        extra_data={"storage_root": "/tmp/forbidden-speaker-path"},
    )

    assert response.status_code == 201
    assert Path(response.json()["audio_path"]).is_relative_to(
        isolated_speaker_storage
    )


def test_fastapi_returns_422_for_corrupt_or_speechless_wav(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())
    corrupt_server = SpeakerApiServer(
        {},
        lambda name, payload: manager.enroll_speaker_from_audio(
            name,
            payload,
            vad=_vad(),
        ),
    )
    speechless_server = SpeakerApiServer(
        {},
        lambda name, payload: manager.enroll_speaker_from_audio(
            name,
            payload,
            vad=_vad(_Detector(length=0)),
        ),
    )

    corrupt = _post(
        corrupt_server,
        name="owner",
        payload=b"RIFF-invalid-wav",
    )
    speechless = _post(speechless_server, name="family_member_1")

    assert corrupt.status_code == 422
    assert speechless.status_code == 422
    assert list((isolated_speaker_storage / "speakers").iterdir()) == []


def test_fastapi_maps_five_person_limit_to_http_409() -> None:
    server = SpeakerApiServer(
        {},
        lambda name, payload: {
            "ok": False,
            "status": 409,
            "code": "speaker_limit_reached",
            "error": "声纹人数已达到上限 5 人",
        },
    )

    response = _post(server, name="family_member_4")

    assert response.status_code == 409
    assert "上限 5 人" in response.json()["detail"]


def test_fastapi_maps_five_sample_limit_to_http_409() -> None:
    server = SpeakerApiServer(
        {},
        lambda name, payload: {
            "ok": False,
            "status": 409,
            "code": "speaker_sample_limit_reached",
            "error": "单人声纹样本已达到上限 5 个",
        },
    )

    response = _post(server, name="owner")

    assert response.status_code == 409
    assert "上限 5 个" in response.json()["detail"]


def test_fastapi_rejects_non_wav_and_accepts_lan_host() -> None:
    server = SpeakerApiServer(
        {"host": "0.0.0.0"},
        lambda name, payload: {"ok": True, "name": name},
    )
    response = _post(
        server,
        name="owner",
        filename="sample.mp3",
        payload=b"not-a-wav",
    )

    assert response.status_code == 415
    server._validate_host()


def test_fastapi_lists_changes_identity_and_deletes_speakers() -> None:
    speakers = {"owner": {"name": "owner", "shots": 1}}

    def listing() -> dict[str, object]:
        return {
            "ok": True,
            "count": len(speakers),
            "max_speakers": 5,
            "max_samples_per_speaker": 5,
            "speakers": list(speakers.values()),
        }

    def rename(name: str, new_name: str) -> dict[str, object]:
        if name not in speakers:
            return {"ok": False, "status": 404, "error": "not found"}
        speakers[new_name] = {**speakers.pop(name), "name": new_name}
        return {
            "ok": True,
            "name": new_name,
            "previous_name": name,
            "changed": True,
        }

    def delete(name: str) -> dict[str, object]:
        if speakers.pop(name, None) is None:
            return {"ok": False, "status": 404, "error": "not found"}
        return {"ok": True, "name": name}

    server = SpeakerApiServer(
        {},
        lambda name, payload: {"ok": True, "name": name},
        list_handler=listing,
        rename_handler=rename,
        delete_handler=delete,
    )

    listed = _request(server, "GET", "/api/v1/speakers")
    renamed = _request(
        server,
        "PATCH",
        "/api/v1/speakers/owner",
        json={"name": "family_member_1"},
    )
    rejected_path = _request(
        server,
        "PATCH",
        "/api/v1/speakers/family_member_1",
        json={"name": "family_member_1", "storage_root": "/tmp/forbidden"},
    )
    deleted = _request(
        server,
        "DELETE",
        "/api/v1/speakers/family_member_1",
    )
    missing = _request(
        server,
        "DELETE",
        "/api/v1/speakers/family_member_1",
    )

    assert listed.status_code == 200
    assert listed.json()["max_speakers"] == 5
    assert listed.json()["max_samples_per_speaker"] == 5
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "family_member_1"
    assert rejected_path.status_code == 422
    assert deleted.status_code == 200
    assert missing.status_code == 404
