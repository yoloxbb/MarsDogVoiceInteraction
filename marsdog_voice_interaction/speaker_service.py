"""Standalone speaker enrollment web service; no ROS or microphone capture."""

from __future__ import annotations

import argparse
from functools import partial
import logging
from pathlib import Path
from typing import Any

from marsdog_voice_interaction.api.speaker_api import SpeakerApiServer
from marsdog_voice_interaction.core.speaker_enrollment_manager import (
    SpeakerEnrollmentManager,
    set_storage_root,
)
from marsdog_voice_interaction.providers.speaker_sherpa import SpeakerSherpaProvider
from marsdog_voice_interaction.utils.config_loader import load_config
from marsdog_voice_interaction.utils.logging_utils import setup_logging
from marsdog_voice_interaction.utils.uploaded_audio import UploadedAudioVAD


logger = logging.getLogger(__name__)


class StandaloneSpeakerService:
    """Reuse the existing HTTP API, validation and on-disk speaker registry.

    Run this service while the voice node is stopped. The registry has no
    cross-process writer lock, and a running voice node caches its templates.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        api_config = dict(config.get("speaker_api", {}))
        # This flag controls the embedded API. An explicit standalone launch
        # must still work with speaker_api.enabled=false in the voice config.
        api_config["enabled"] = True
        if not str(api_config.get("host", "127.0.0.1")).strip():
            raise ValueError("speaker_api.host 不能为空")
        if not 1 <= int(api_config.get("port", 8091)) <= 65535:
            raise ValueError("speaker_api.port 必须在 1 到 65535 之间")
        storage_root = str(config.get("storage", {}).get("root", "")).strip()
        if not storage_root:
            raise ValueError("必须配置 storage.root，与语音节点使用同一声纹目录")

        providers = config.get("providers", {})
        speaker = providers.get("speaker", {})
        if speaker.get("type", "sherpa") != "sherpa":
            raise ValueError("独立声纹服务需要 providers.speaker.type=sherpa")
        speaker_config = speaker.get("config", {})
        audio_config = providers.get("audio", {}).get("config", {})
        for key, values in (("speaker_model", speaker_config), ("vad_model", audio_config)):
            model = str(values.get(key, "")).strip()
            if not model or not Path(model).is_file():
                raise ValueError(f"{key} 模型文件不存在: {model or '未配置'}")

        self.provider = SpeakerSherpaProvider(speaker_config)
        self.provider.start()
        try:
            if not self.provider.is_available():
                raise RuntimeError("声纹模型加载失败，请检查 providers.speaker.config")
            self.vad = UploadedAudioVAD(audio_config)
            set_storage_root(storage_root)
            self.enrollment = SpeakerEnrollmentManager(
                cross_identity_similarity_threshold=api_config.get(
                    "cross_identity_similarity_threshold", 0.75,
                ),
                same_identity_similarity_threshold=api_config.get(
                    "same_identity_similarity_threshold", 0.5,
                ),
            )
            self.enrollment.set_speaker_extractor(self.provider._extractor)
            self.server = SpeakerApiServer(
                api_config,
                partial(self.enrollment.enroll_speaker_from_audio, vad=self.vad),
                list_handler=self.enrollment.list_speaker_records,
                sample_list_handler=self.enrollment.list_speaker_samples,
                sample_get_handler=self.enrollment.get_speaker_sample,
                sample_replace_handler=partial(
                    self.enrollment.replace_speaker_sample, vad=self.vad,
                ),
                sample_delete_handler=self.enrollment.delete_speaker_sample,
                batch_upload_handler=partial(
                    self.enrollment.enroll_speaker_batch_from_audio, vad=self.vad,
                ),
                speaker_delete_handler=self.enrollment.delete_speaker,
                delete_all_handler=self.enrollment.delete_all_speakers,
                vad_handler=self.analyze_recording,
            )
        except BaseException:
            self.provider.stop()
            raise
        logger.info("Standalone speaker storage: %s", storage_root)
        logger.info("Speaker enrollment page: %s/speaker-enrollment", self.server.address)

    def analyze_recording(self, audio_bytes: bytes) -> dict[str, Any]:
        try:
            return self.vad.analyze_wav(audio_bytes)
        except ValueError as exc:
            return {"ok": False, "status": 422, "error": str(exc)}
        except Exception:
            logger.exception("Speaker recording VAD failed")
            return {"ok": False, "status": 503, "error": "录音 VAD 不可用"}

    def close(self) -> None:
        self.server.stop()
        self.provider.stop()


def main(args: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="语音 YAML 配置路径（复用模型和声纹库）")
    parser.add_argument("--host", help="覆盖 speaker_api.host")
    parser.add_argument("--port", type=int, help="覆盖 speaker_api.port")
    options = parser.parse_args(args)
    try:
        config = load_config(options.config)
        api_config = config.setdefault("speaker_api", {})
        if options.host is not None:
            api_config["host"] = options.host
        if options.port is not None:
            api_config["port"] = options.port
        log_config = config.get("logging", {})
        setup_logging(
            log_dir=str(log_config.get("dir", "log")),
            level=str(log_config.get("level", "INFO")),
            node="speaker_service",
            console=bool(log_config.get("console", True)),
            file=bool(log_config.get("file", True)),
        )
        service = StandaloneSpeakerService(config)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    try:
        service.server.run()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()


if __name__ == "__main__":
    main()
