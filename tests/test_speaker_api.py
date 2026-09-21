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
    def __init__(
        self,
        start: int = 8000,
        length: int = 8000,
        level: float = 0.2,
    ) -> None:
        self._start = start
        self._length = length
        self._level = level
        self._segments: list[_Segment] = []

    def reset(self) -> None:
        self._segments.clear()

    def accept_waveform(self, samples: list[float]) -> None:
        del samples

    def flush(self) -> None:
        self._segments.append(
            _Segment(self._start, [self._level] * self._length)
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


class _SequenceExtractor(_Extractor):
    def __init__(self, embeddings: list[list[float]]) -> None:
        self._embeddings = list(embeddings)

    def compute(self, stream: _Stream) -> list[float]:
        del stream
        return self._embeddings.pop(0)


class _SpeakerProvider:
    def __init__(self) -> None:
        self.templates: dict[str, list[np.ndarray]] = {}

    def set_templates(self, templates: dict[str, list[np.ndarray]]) -> None:
        self.templates = {
            name: [np.asarray(value) for value in values]
            for name, values in templates.items()
        }


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


def _wav(
    duration_sec: float = 2.0,
    sample_rate: int = 16000,
    amplitude: float = 0.2,
) -> bytes:
    count = int(duration_sec * sample_rate)
    samples = np.sin(
        2 * np.pi * 220 * np.arange(count, dtype=np.float32) / sample_rate
    ) * amplitude
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
            data = dict(extra_data or {})
            return await client.post(
                f"/api/v1/speakers/{name}/samples",
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


def test_padding_does_not_count_as_effective_speech() -> None:
    vad = UploadedAudioVAD(
        {"sample_rate": 16000, "pre_roll_sec": 0.3,
         "upload_post_roll_sec": 0.2, "upload_min_speech_sec": 0.5},
        detector=_Detector(length=4000),
    )
    with pytest.raises(ValueError, match="有效语音过短"):
        vad.trim_wav(_wav())


def test_upload_preserves_unpadded_speech_duration(isolated_speaker_storage: Path) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())
    vad = UploadedAudioVAD(
        {"sample_rate": 16000, "pre_roll_sec": 0.3, "upload_post_roll_sec": 0.2},
        detector=_Detector(length=8000),
    )
    result = manager.enroll_speaker_from_audio("owner", _wav(), vad=vad)
    assert result["ok"]
    assert result["speech_duration_ms"] == 500
    samples, rate = decode_pcm16_wav(Path(result["audio_path"]).read_bytes())
    assert len(samples) / rate == 1.0


def test_live_enrollment_cannot_bypass_cross_identity_check(isolated_speaker_storage: Path) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())
    assert manager.enroll_speaker_from_audio("owner", _wav())["ok"]
    assert manager.start_speaker("family_member_1", 1)["ok"]
    samples, rate = decode_pcm16_wav(_wav())
    result = manager.process_speaker_audio(samples, rate)
    assert result["code"] == "speaker_identity_conflict"
    assert manager.speaker_session.shots_collected == 0
    assert not (isolated_speaker_storage / "speakers/family_member_1").exists()


def test_live_enrollment_appends_matching_audio_and_embedding(isolated_speaker_storage: Path) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())
    first = manager.enroll_speaker_from_audio("owner", _wav())
    original_wav = Path(first["audio_path"]).read_bytes()
    original_embedding = Path(first["embedding_path"]).read_bytes()
    assert manager.start_speaker("owner", 1)["ok"]
    samples, rate = decode_pcm16_wav(_wav(amplitude=0.3))
    result = manager.process_speaker_audio(samples, rate, vad=_vad())
    assert result["ok"] and result["done"]
    assert result["sample_ids"] == [2]
    assert Path(first["audio_path"]).read_bytes() == original_wav
    assert Path(first["embedding_path"]).read_bytes() == original_embedding
    new_sample = result["samples"][0]
    stored, rate = decode_pcm16_wav(Path(new_sample["audio_path"]).read_bytes())
    expected = manager._extract_embedding(stored, rate)
    np.testing.assert_allclose(np.load(new_sample["embedding_path"]), expected, atol=1e-4)


def test_live_enrollment_respects_remaining_capacity(isolated_speaker_storage: Path) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())
    assert manager.enroll_speaker_batch_from_audio("owner", [_wav()] * 4)["ok"]
    assert manager.start_speaker("owner", 2)["code"] == "speaker_sample_limit_reached"


def test_inconsistent_same_identity_batch_leaves_no_samples(isolated_speaker_storage: Path) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_SequenceExtractor([[1., 0.], [0., 1.]]))
    result = manager.enroll_speaker_batch_from_audio("owner", [_wav(), _wav()])
    assert result["code"] == "speaker_sample_inconsistent"
    assert result["failed_file_index"] == 2
    assert not (isolated_speaker_storage / "speakers/owner").exists()


def test_inconsistent_replacement_preserves_sample(isolated_speaker_storage: Path) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_SequenceExtractor([[1., 0.], [1., 0.], [0., 1.]]))
    added = manager.enroll_speaker_batch_from_audio("owner", [_wav(), _wav()])
    target = Path(added["samples"][0]["embedding_path"])
    before = target.read_bytes()
    result = manager.replace_speaker_sample("owner", 1, _wav())
    assert result["code"] == "speaker_sample_inconsistent"
    assert target.read_bytes() == before


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
    assert "owner" in provider.templates
    assert len(provider.templates["owner"]) == 2


def test_upload_normalizes_48khz_audio_to_mono_pcm16_16khz(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())

    result = manager.enroll_speaker_from_audio(
        "owner",
        _wav(sample_rate=48000),
        vad=_vad(),
    )
    stored, stored_rate = decode_pcm16_wav(
        (
            isolated_speaker_storage / "speakers" / "owner" / "001.wav"
        ).read_bytes()
    )

    assert result["ok"] is True
    assert result["source_sample_rate"] == 48000
    assert result["stored_sample_rate"] == 16000
    assert result["stored_channels"] == 1
    assert result["stored_sample_width_bits"] == 16
    assert stored_rate == 16000
    assert stored.dtype == np.float32


def test_cross_identity_high_similarity_is_rejected_but_same_identity_is_allowed(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager(
        cross_identity_similarity_threshold=0.8,
    )
    manager.set_speaker_extractor(
        _SequenceExtractor([[1.0, 0.0], [0.99, 0.01], [1.0, 0.0]])
    )

    owner = manager.enroll_speaker_from_audio("owner", _wav(), vad=_vad())
    conflict = manager.enroll_speaker_from_audio(
        "family_member_1",
        _wav(),
        vad=_vad(),
    )
    same_identity = manager.enroll_speaker_from_audio(
        "owner",
        _wav(),
        vad=_vad(),
    )

    assert owner["ok"] is True
    assert conflict["status"] == 409
    assert conflict["code"] == "speaker_identity_conflict"
    assert conflict["conflicting_speaker"] == "owner"
    assert conflict["conflicting_sample_id"] == 1
    assert conflict["similarity"] >= conflict["similarity_threshold"]
    assert same_identity["ok"] is True
    assert same_identity["shots"] == 2
    assert not (
        isolated_speaker_storage / "speakers" / "family_member_1"
    ).exists()


def test_batch_upload_is_validation_atomic_and_assigns_stable_ids(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(
        _SequenceExtractor([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
    )

    rejected = manager.enroll_speaker_batch_from_audio(
        "owner",
        [_wav(), b"RIFF-invalid-wav"],
        vad=_vad(),
    )

    assert rejected["ok"] is False
    assert rejected["failed_file_index"] == 2
    assert not (isolated_speaker_storage / "speakers" / "owner").exists()

    accepted = manager.enroll_speaker_batch_from_audio(
        "owner",
        [_wav(), _wav()],
        vad=_vad(),
    )

    assert accepted["ok"] is True
    assert accepted["added_count"] == 2
    assert accepted["sample_ids"] == [1, 2]
    assert accepted["shots"] == 2
    assert [item["stored_sample_rate"] for item in accepted["samples"]] == [
        16000,
        16000,
    ]


def test_storage_exposes_exactly_five_identity_slots_and_sample_delete_releases_one(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager(
        cross_identity_similarity_threshold=None,
    )
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

    deleted = manager.delete_speaker_sample("family_member_4", 1)
    assert deleted["ok"] is True
    assert deleted["speaker_removed"] is True
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


def test_legacy_identity_data_is_not_loaded_or_exposed(
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
    provider = _SpeakerProvider()

    listing = manager.list_speaker_records()

    assert listing["count"] == 0
    assert listing["speakers"] == []
    assert manager.sync_to_provider(provider) == 0
    assert provider.templates == {}


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


def test_individual_sample_crud_preserves_ids_and_recomputes_centroid(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())

    first = manager.enroll_speaker_from_audio(
        "owner",
        _wav(amplitude=0.1),
        vad=_vad(),
    )
    second = manager.enroll_speaker_from_audio(
        "owner",
        _wav(amplitude=0.3),
        vad=_vad(),
    )
    directory = isolated_speaker_storage / "speakers" / "owner"

    assert first["sample_id"] == 1
    assert second["sample_id"] == 2
    assert manager.list_speaker_samples("owner")["sample_ids"] == [1, 2]
    assert manager.get_speaker_sample("owner", 2)["ready"] is True
    first_embedding = np.load(directory / "001.npy")
    original_second_embedding = np.load(directory / "002.npy")

    replaced = manager.replace_speaker_sample(
        "owner",
        2,
        _wav(amplitude=0.5),
        vad=_vad(),
    )
    replacement_embedding = np.load(directory / "002.npy")
    centroid_after_replace = np.load(directory / "centroid.npy")

    assert replaced["ok"] is True
    assert replaced["sample_id"] == 2
    assert replaced["shots"] == 2
    assert not np.allclose(replacement_embedding, original_second_embedding)
    assert np.allclose(
        centroid_after_replace,
        np.mean([first_embedding, replacement_embedding], axis=0),
    )

    deleted = manager.delete_speaker_sample("owner", 1)
    centroid_after_delete = np.load(directory / "centroid.npy")

    assert deleted["ok"] is True
    assert deleted["remaining_sample_ids"] == [2]
    assert deleted["speaker_removed"] is False
    assert not (directory / "001.wav").exists()
    assert not (directory / "001.npy").exists()
    assert (directory / "002.wav").exists()
    assert np.allclose(centroid_after_delete, replacement_embedding)

    added = manager.enroll_speaker_from_audio(
        "owner",
        _wav(amplitude=0.2),
        vad=_vad(),
    )

    assert added["sample_id"] == 1
    assert added["shots"] == 2
    assert manager.list_speaker_samples("owner")["sample_ids"] == [1, 2]


def test_invalid_sample_replacement_keeps_original_files(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())
    manager.enroll_speaker_from_audio("family_member_1", _wav(), vad=_vad())
    directory = isolated_speaker_storage / "speakers" / "family_member_1"
    original_audio = (directory / "001.wav").read_bytes()
    original_embedding = (directory / "001.npy").read_bytes()
    original_centroid = (directory / "centroid.npy").read_bytes()

    result = manager.replace_speaker_sample(
        "family_member_1",
        1,
        b"RIFF-invalid-wav",
        vad=_vad(),
    )

    assert result["ok"] is False
    assert (directory / "001.wav").read_bytes() == original_audio
    assert (directory / "001.npy").read_bytes() == original_embedding
    assert (directory / "centroid.npy").read_bytes() == original_centroid


def test_deleting_last_sample_releases_identity_and_runtime_index(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())
    manager.enroll_speaker_from_audio("owner", _wav(), vad=_vad())
    provider = _SpeakerProvider()
    manager.sync_to_provider(provider)
    assert "owner" in provider.templates

    deleted = manager.delete_speaker_sample("owner", 1)
    synced = manager.sync_to_provider(provider)

    assert deleted["speaker_removed"] is True
    assert deleted["shots"] == 0
    assert synced == 0
    assert "owner" not in provider.templates
    assert not (isolated_speaker_storage / "speakers" / "owner").exists()
    assert manager.list_speaker_records()["count"] == 0


def test_delete_one_identity_and_delete_all_fixed_identities(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager(
        cross_identity_similarity_threshold=None,
    )
    manager.set_speaker_extractor(_Extractor())
    manager.enroll_speaker_from_audio("owner", _wav(), vad=_vad())
    manager.enroll_speaker_from_audio(
        "family_member_1",
        _wav(),
        vad=_vad(),
    )

    owner_deleted = manager.delete_speaker("owner")
    all_deleted = manager.delete_all_speakers()

    assert owner_deleted["ok"] is True
    assert owner_deleted["deleted_sample_ids"] == [1]
    assert owner_deleted["deleted_count"] == 1
    assert all_deleted["deleted_speakers"] == ["family_member_1"]
    assert all_deleted["deleted_speaker_count"] == 1
    assert all_deleted["deleted_sample_count"] == 1
    assert manager.list_speaker_records()["speakers"] == []
    assert list((isolated_speaker_storage / "speakers").iterdir()) == []


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
    assert "上限 5 人" in response.json()["detail"]["error"]


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
    assert "上限 5 个" in response.json()["detail"]["error"]


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


def test_fastapi_lists_speakers_and_omits_removed_legacy_routes() -> None:
    speakers = {"owner": {"name": "owner", "shots": 1}}

    def listing() -> dict[str, object]:
        return {
            "ok": True,
            "count": len(speakers),
            "max_speakers": 5,
            "max_samples_per_speaker": 5,
            "speakers": list(speakers.values()),
        }

    server = SpeakerApiServer(
        {},
        lambda name, payload: {"ok": True, "name": name},
        list_handler=listing,
    )

    listed = _request(server, "GET", "/api/v1/speakers")
    schema_paths = server.create_app().openapi()["paths"]

    assert listed.status_code == 200
    assert listed.json()["max_speakers"] == 5
    assert listed.json()["max_samples_per_speaker"] == 5
    assert "post" not in schema_paths["/api/v1/speakers"]
    assert "/api/v1/speakers/{name}" not in schema_paths


def test_fastapi_batch_upload_and_delete_all_routes() -> None:
    batch_calls: list[tuple[str, list[bytes]]] = []
    deleted_names: list[str] = []
    delete_all_calls = 0

    def batch_upload(
        name: str,
        payloads: list[bytes],
    ) -> dict[str, object]:
        batch_calls.append((name, payloads))
        return {
            "ok": True,
            "name": name,
            "added_count": len(payloads),
            "sample_ids": list(range(1, len(payloads) + 1)),
            "samples": [],
        }

    def delete_all() -> dict[str, object]:
        nonlocal delete_all_calls
        delete_all_calls += 1
        return {
            "ok": True,
            "deleted_speaker_count": 1,
            "deleted_sample_count": 2,
        }

    server = SpeakerApiServer(
        {},
        lambda name, payload: {"ok": True, "name": name},
        batch_upload_handler=batch_upload,
        speaker_delete_handler=lambda name: (
            deleted_names.append(name)
            or {"ok": True, "name": name, "deleted_count": 2}
        ),
        delete_all_handler=delete_all,
    )
    batch = _request(
        server,
        "POST",
        "/api/v1/speakers/owner/samples/batch",
        files=[
            ("audios", ("one.wav", _wav(), "audio/wav")),
            ("audios", ("two.wav", _wav(amplitude=0.3), "audio/wav")),
        ],
    )
    one_deleted = _request(
        server,
        "DELETE",
        "/api/v1/speakers/owner/samples",
    )
    not_confirmed = _request(server, "DELETE", "/api/v1/speakers")
    all_deleted = _request(
        server,
        "DELETE",
        "/api/v1/speakers?confirm=true",
    )

    assert batch.status_code == 201
    assert batch.json()["added_count"] == 2
    assert len(batch_calls) == 1
    assert batch_calls[0][0] == "owner"
    assert len(batch_calls[0][1]) == 2
    assert one_deleted.status_code == 200
    assert deleted_names == ["owner"]
    assert not_confirmed.status_code == 400
    assert not_confirmed.json()["detail"]["code"] == (
        "delete_all_confirmation_required"
    )
    assert all_deleted.status_code == 200
    assert delete_all_calls == 1


def test_fastapi_exposes_structured_similarity_conflict() -> None:
    server = SpeakerApiServer(
        {},
        lambda name, payload: {
            "ok": False,
            "status": 409,
            "code": "speaker_identity_conflict",
            "error": "不能注册到不同身份",
            "conflicting_speaker": "owner",
            "similarity": 0.93,
            "similarity_threshold": 0.75,
        },
    )

    response = _post(server, name="family_member_1")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "speaker_identity_conflict",
        "error": "不能注册到不同身份",
        "conflicting_speaker": "owner",
        "similarity": 0.93,
        "similarity_threshold": 0.75,
    }


def test_fastapi_manages_and_downloads_individual_samples(
    isolated_speaker_storage: Path,
) -> None:
    manager = SpeakerEnrollmentManager()
    manager.set_speaker_extractor(_Extractor())

    def upload(name: str, payload: bytes) -> dict[str, object]:
        return manager.enroll_speaker_from_audio(name, payload, vad=_vad())

    def replace(
        name: str,
        sample_id: int,
        payload: bytes,
    ) -> dict[str, object]:
        return manager.replace_speaker_sample(
            name,
            sample_id,
            payload,
            vad=_vad(_Detector(level=0.4)),
        )

    server = SpeakerApiServer(
        {},
        upload,
        list_handler=manager.list_speaker_records,
        sample_list_handler=manager.list_speaker_samples,
        sample_get_handler=manager.get_speaker_sample,
        sample_replace_handler=replace,
        sample_delete_handler=manager.delete_speaker_sample,
    )

    added = _request(
        server,
        "POST",
        "/api/v1/speakers/owner/samples",
        files={"audio": ("owner.wav", _wav(), "audio/wav")},
    )
    listed = _request(server, "GET", "/api/v1/speakers/owner/samples")
    detail = _request(server, "GET", "/api/v1/speakers/owner/samples/1")
    audio = _request(
        server,
        "GET",
        "/api/v1/speakers/owner/samples/1/audio",
    )
    stored_audio = (
        isolated_speaker_storage / "speakers" / "owner" / "001.wav"
    ).read_bytes()
    replaced = _request(
        server,
        "PUT",
        "/api/v1/speakers/owner/samples/1",
        files={"audio": ("replacement.wav", _wav(), "audio/wav")},
    )
    deleted = _request(
        server,
        "DELETE",
        "/api/v1/speakers/owner/samples/1",
    )
    missing = _request(
        server,
        "GET",
        "/api/v1/speakers/owner/samples/1",
    )

    assert added.status_code == 201
    assert added.json()["sample_id"] == 1
    assert listed.status_code == 200
    assert listed.json()["sample_ids"] == [1]
    assert detail.status_code == 200
    assert detail.json()["sample_key"] == "001"
    assert detail.json()["audio_url"].endswith("/samples/1/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content == stored_audio
    assert replaced.status_code == 200
    assert replaced.json()["replaced"] is True
    assert deleted.status_code == 200
    assert deleted.json()["speaker_removed"] is True
    assert missing.status_code == 404


def test_sample_api_rejects_unknown_identity_and_out_of_range_id() -> None:
    server = SpeakerApiServer(
        {},
        lambda name, payload: {"ok": True, "name": name},
        sample_get_handler=lambda name, sample_id: {
            "ok": True,
            "name": name,
            "sample_id": sample_id,
        },
    )

    unknown_identity = _request(
        server,
        "GET",
        "/api/v1/speakers/alice/samples/1",
    )
    invalid_id = _request(
        server,
        "GET",
        "/api/v1/speakers/owner/samples/6",
    )

    assert unknown_identity.status_code == 422
    assert invalid_id.status_code == 422
