"""Independent ROS2 node for wakeup, ASR, speaker and intent interaction."""

from __future__ import annotations

import base64
import io
import json
import math
import re
import threading
import time
import uuid
import wave
from typing import Any

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

try:
    from marsdog_voice_interaction.srv import VoiceTask
except ImportError:
    VoiceTask = None  # type: ignore[assignment]

from marsdog_voice_interaction.core.command_lexicon import CommandLexicon
from marsdog_voice_interaction.core.interaction_state_machine import (
    Trigger,
    VoiceInteractionStateMachine,
)
from marsdog_voice_interaction.core.speaker_enrollment_manager import (
    SpeakerEnrollmentManager,
    set_storage_root,
)
from marsdog_voice_interaction.core.utterance_command_tracker import (
    UtteranceCommandTracker,
)
from marsdog_voice_interaction.messages.audio_event import normalize_audio_event
from marsdog_voice_interaction.messages.voice_event_types import (
    EVT_STATE_CHANGED,
    EVT_VOICE_CALL_NAME,
    classification_to_voice_event,
    speaker_to_voice_event,
)
from marsdog_voice_interaction.providers.base import BaseProvider
from marsdog_voice_interaction.utils.config_loader import load_config
from marsdog_voice_interaction.utils.logging_utils import (
    get_log_file_path,
    get_logger,
    log_trace,
    setup_logging,
)


logger = get_logger(__name__, module="voice")

_AUDIO_QOS = QoSProfile(
    reliability=ReliabilityPolicy.RELIABLE,
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
)

_UNKNOWN_INTENT = {
    "event_type": "EVT_VOICE_COMMAND_UNKNOWN",
    "emotion": "NONE",
    "action": "UNKNOWN",
    "control": "CLARIFY",
    "command_id": "CMD_UNKNOWN",
    "intent_category": "clarify",
    "intent_source": "fallback",
    "intent_confidence": 0.0,
    "slots": [
        {"key": "emotion", "value": "NONE"},
        {"key": "action", "value": "UNKNOWN"},
        {"key": "control", "value": "CLARIFY"},
        {"key": "raw_tag", "value": "NONE|UNKNOWN|CLARIFY"},
    ],
    "is_executable": False,
    "should_trigger_behavior_tree": False,
}


class VoiceInteractionNode(Node):
    """Own voice hardware, voice-print data and the voice ROS APIs."""

    def __init__(self) -> None:
        super().__init__("voice_interaction")
        self.declare_parameter("config_path", "config/voice.yaml")
        self.declare_parameter("log_level", "")
        self.declare_parameter("log_dir", "")
        config_path = str(self.get_parameter("config_path").value)
        try:
            self._config = load_config(config_path)
        except Exception as exc:
            setup_logging(node="voice_interaction")
            logger.error("Cannot load voice config %s: %s", config_path, exc)
            self._config = {}

        logging_config = self._config.get("logging", {})
        log_level_override = str(self.get_parameter("log_level").value).strip()
        log_dir_override = str(self.get_parameter("log_dir").value).strip()
        log_level = log_level_override or str(
            logging_config.get("level", "INFO")
        )
        log_dir = log_dir_override or str(logging_config.get("dir", "log"))
        setup_logging(
            log_dir=log_dir,
            level=log_level,
            node="voice_interaction",
            console=bool(logging_config.get("console", True)),
            file=bool(logging_config.get("file", True)),
        )
        self._event_trace_enabled = bool(
            logging_config.get("event_trace", True)
        )
        self._command_lexicon: CommandLexicon | None = None
        self._command_lexicon_status: dict[str, Any] = {
            "enabled": False,
            "ready": False,
        }
        self._init_command_lexicon()

        set_storage_root(
            self._config.get("storage", {}).get("root", "data")
        )
        self._enrollment = SpeakerEnrollmentManager()
        self._state_machine = VoiceInteractionStateMachine()
        self._providers: dict[str, BaseProvider | None] = {}
        self._speaker_operation_lock = threading.RLock()
        self._speaker_api: Any = None
        self._speaker_api_status: dict[str, Any] = {
            "enabled": False,
            "ready": False,
        }
        self._upload_vad: Any = None
        self._interaction_lock = threading.RLock()
        self._interaction_active = False
        self._interaction_id = ""
        self._last_interaction_time = 0.0
        self._interaction_holds: dict[str, dict[str, Any]] = {}
        self._latest_audio: dict[str, Any] | None = None
        self._command_tracker = UtteranceCommandTracker()
        self._utterance_started_monotonic = 0.0

        interaction = self._config.get("interaction", {})
        self._idle_timeout = float(interaction.get("idle_timeout_sec", 10))
        self._hold_max_lease_sec = max(
            0.1,
            float(interaction.get("hold_max_lease_sec", 30.0)),
        )
        self._init_providers()
        self._wire_speaker_enrollment()
        self._sync_speaker_registry()

        topics = self._config.get("topics", {})
        audio_topic = str(
            topics.get("audio_event", "/perception/audio_event")
        )
        enrollment_topic = str(
            topics.get(
                "enrollment_event",
                "/perception/voice/enrollment_event",
            )
        )
        self._audio_pub = self.create_publisher(
            String, audio_topic, _AUDIO_QOS
        )
        self._enrollment_pub = self.create_publisher(
            String, enrollment_topic, _AUDIO_QOS
        )
        self._timer = self.create_timer(0.05, self._poll)

        service_name = str(
            topics.get("voice_task", "/perception/voice/task")
        )
        self._service = (
            self.create_service(VoiceTask, service_name, self._handle_task)
            if VoiceTask is not None else None
        )
        logger.info(
            "Voice node ready: audio=%s service=%s",
            audio_topic,
            service_name if self._service is not None else "unavailable",
        )
        self._init_speaker_api()
        self._trace(
            "runtime_start",
            result="ready",
            runtime_mode=self._runtime_mode(),
            config_path=config_path,
            log_level=log_level.upper(),
            log_file=get_log_file_path(),
            audio_topic=audio_topic,
            enrollment_topic=enrollment_topic,
            service=service_name if self._service is not None else "unavailable",
            idle_timeout_sec=self._idle_timeout,
            speaker_api=self._speaker_api_status,
            command_lexicon=self._command_lexicon_status,
            providers={
                name: {
                    "class": type(provider).__name__,
                    "available": bool(provider and provider.is_available()),
                }
                for name, provider in sorted(self._providers.items())
            },
        )

    def _runtime_mode(self) -> str:
        mock = self._config.get("mock", {})
        if not mock.get("enabled", False):
            return "production"
        return f"mock_{mock.get('mode', 'pipeline')}"

    def _init_command_lexicon(self) -> None:
        config = self._config.get("command_lexicon", {})
        enabled = bool(config.get("enabled", False))
        self._command_lexicon_status = {
            "enabled": enabled,
            "ready": False,
        }
        if not enabled:
            return
        catalog_path = str(config.get("catalog", "")).strip()
        try:
            if not catalog_path:
                raise ValueError("command_lexicon.catalog is required")
            lexicon = CommandLexicon(catalog_path)
            self._command_lexicon = lexicon
            self._command_lexicon_status.update({
                "ready": True,
                "catalog": str(lexicon.catalog_path),
                "version": lexicon.version,
                "command_count": lexicon.command_count,
                "core_command_count": lexicon.core_command_count,
                "phrase_count": lexicon.phrase_count,
                "reference_phrase_count": lexicon.reference_phrase_count,
                "source_name": lexicon.source_name,
                "source_row_count": lexicon.source_row_count,
                "covered_source_row_count": lexicon.covered_source_row_count,
            })
            logger.info(
                "Command lexicon ready: version=%s commands=%d core=%d "
                "phrases=%d",
                lexicon.version,
                lexicon.command_count,
                lexicon.core_command_count,
                lexicon.phrase_count,
            )
        except Exception as exc:
            self._command_lexicon = None
            self._command_lexicon_status["error"] = str(exc)
            logger.error("Command lexicon unavailable: %s", exc)

    def _trace(self, record: str, **fields: Any) -> None:
        if getattr(self, "_event_trace_enabled", True):
            log_trace(logger, record, **fields)

    def _init_speaker_api(self) -> None:
        config = self._config.get("speaker_api", {})
        enabled = bool(config.get("enabled", False))
        self._speaker_api_status = {"enabled": enabled, "ready": False}
        if not enabled:
            return
        try:
            from marsdog_voice_interaction.api import SpeakerApiServer
            from marsdog_voice_interaction.utils.uploaded_audio import (
                UploadedAudioVAD,
            )

            audio_config = self._config.get("providers", {}).get(
                "audio",
                {},
            ).get("config", {})
            self._upload_vad = UploadedAudioVAD(audio_config)
            self._speaker_api = SpeakerApiServer(
                config,
                self._enroll_uploaded_speaker,
                list_handler=self._list_speakers_for_api,
                rename_handler=self._rename_speaker_for_api,
                delete_handler=self._delete_speaker_for_api,
            )
            ready = self._speaker_api.start()
            self._speaker_api_status = {
                "enabled": True,
                "ready": ready,
                "address": self._speaker_api.address,
                "docs": f"{self._speaker_api.address}/docs",
            }
        except Exception as exc:
            self._speaker_api = None
            self._upload_vad = None
            self._speaker_api_status = {
                "enabled": True,
                "ready": False,
                "error": str(exc),
            }
            logger.error("Speaker FastAPI unavailable: %s", exc, exc_info=True)

    def _enroll_uploaded_speaker(
        self,
        name: str,
        audio_bytes: bytes,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if self._upload_vad is None:
            result = {"ok": False, "error": "上传音频 VAD 不可用"}
        else:
            with self._speaker_operation_lock:
                result = self._enrollment.enroll_speaker_from_audio(
                    name,
                    audio_bytes,
                    vad=self._upload_vad,
                )
                if result.get("ok"):
                    self._sync_speaker_registry()
        self._trace(
            "speaker_api_upload",
            result="success" if result.get("ok") else "failure",
            speaker_name=str(result.get("name", name)),
            shots=int(result.get("shots", 0)),
            source_duration_ms=float(result.get("source_duration_ms", 0.0)),
            speech_duration_ms=float(result.get("speech_duration_ms", 0.0)),
            segment_count=int(result.get("segment_count", 0)),
            audio_valid=bool(result.get("audio_valid", False)),
            has_effective_speech=bool(
                result.get("has_effective_speech", False)
            ),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            error=str(result.get("error", "")),
        )
        return result

    def _list_speakers_for_api(self) -> dict[str, Any]:
        started = time.perf_counter()
        with self._speaker_operation_lock:
            result = self._enrollment.list_speaker_records()
        self._trace(
            "speaker_management",
            operation="list",
            result="success",
            speaker_count=int(result.get("count", 0)),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )
        return result

    def _rename_speaker_for_api(
        self,
        name: str,
        new_name: str,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        with self._speaker_operation_lock:
            result = self._enrollment.rename_speaker(name, new_name)
            if result.get("ok"):
                self._sync_speaker_registry()
        self._trace(
            "speaker_management",
            operation="rename",
            result="success" if result.get("ok") else "failure",
            speaker_name=str(result.get("name", new_name)),
            previous_name=str(result.get("previous_name", name)),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            error=str(result.get("error", "")),
        )
        return result

    def _delete_speaker_for_api(self, name: str) -> dict[str, Any]:
        started = time.perf_counter()
        with self._speaker_operation_lock:
            result = self._enrollment.delete_speaker(name)
            if result.get("ok"):
                self._sync_speaker_registry()
        self._trace(
            "speaker_management",
            operation="delete",
            result="success" if result.get("ok") else "failure",
            speaker_name=str(result.get("name", name)),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
            error=str(result.get("error", "")),
        )
        return result

    def _init_providers(self) -> None:
        providers = self._config.get("providers", {})
        mock = self._config.get("mock", {})
        if mock.get("enabled") and mock.get("mode") == "event":
            from marsdog_voice_interaction.providers.mock_event import (
                MockEventProvider,
            )
            provider = MockEventProvider(mock)
            provider.start()
            self._providers["mock_event"] = provider
            return

        self._providers["wakeup"] = self._build_wakeup(
            providers.get("wakeup", {})
        )
        self._providers["audio"] = self._build_audio(
            providers.get("audio", {})
        )
        self._providers["kws"] = self._build_kws(
            providers.get("kws", {})
        )
        audio = self._providers.get("audio")
        kws = self._providers.get("kws")
        if (
            audio is not None
            and kws is not None
            and kws.is_available()
            and hasattr(audio, "set_chunk_callback")
        ):
            audio.set_chunk_callback(  # type: ignore[attr-defined]
                kws.accept_waveform,  # type: ignore[attr-defined]
            )
        self._providers["asr"] = self._build_asr(providers.get("asr", {}))
        self._providers["speaker"] = self._build_speaker(
            providers.get("speaker", {})
        )

        rule_config = providers.get("intent_rule", {})
        if rule_config.get("enabled", True):
            from marsdog_voice_interaction.providers.rule_intent import (
                RuleIntentProvider,
            )
            rule = RuleIntentProvider(rule_config.get("config", {}))
            rule.start()
            self._providers["intent_rule"] = rule

        llm_config = providers.get("intent_llm", {})
        if llm_config.get("enabled", False) and llm_config.get("type") != "mock":
            from marsdog_voice_interaction.providers.intent_rkllm import (
                IntentRKLLMProvider,
            )
            llm = IntentRKLLMProvider(llm_config.get("config", {}))
            llm.start()
            self._providers["intent_llm"] = llm

    def _build_wakeup(self, section: dict[str, Any]) -> BaseProvider | None:
        if not section.get("enabled", True):
            return None
        config = section.get("config", {})
        if section.get("type", "xfyun_serial") == "xfyun_serial":
            from marsdog_voice_interaction.providers.wakeup_xfyun_serial import (
                WakeupXFYunSerialProvider,
            )
            provider: BaseProvider = WakeupXFYunSerialProvider(config)
            provider.start()
            if provider.is_available():
                return provider
            if not self._config.get("mock", {}).get("enabled", False):
                # Keep the real provider in production mode. It owns the
                # reconnect policy and may recover when the USB device returns.
                return provider
        from marsdog_voice_interaction.providers.mock_wakeup import (
            MockWakeupProvider,
        )
        fallback_config = dict(config)
        fallback_config.update({
            "enable_mock_interaction": True,
            "mock_enabled": True,
            "mock_interaction_interval_sec": float(
                self._config.get("mock", {}).get("event_interval_sec", 5)
            ),
        })
        provider = MockWakeupProvider(fallback_config)
        provider.start()
        return provider

    def _build_audio(self, section: dict[str, Any]) -> BaseProvider | None:
        if not section.get("enabled", True):
            return None
        config = dict(section.get("config", {}))
        if section.get("type", "sherpa") == "sherpa":
            from marsdog_voice_interaction.providers.audio_sherpa import (
                AudioSherpaProvider,
            )
            provider: BaseProvider = AudioSherpaProvider(config)
            provider.start()
            if provider.is_available():
                return provider
        from marsdog_voice_interaction.providers.mock_audio import MockAudioProvider
        config["mock_event_interval_sec"] = float(
            self._config.get("mock", {}).get("event_interval_sec", 5)
        )
        provider = MockAudioProvider(config)
        provider.start()
        return provider

    @staticmethod
    def _build_kws(section: dict[str, Any]) -> BaseProvider | None:
        if not section.get("enabled", False):
            return None
        if section.get("type", "sherpa") != "sherpa":
            logger.warning("Unsupported KWS provider type: %s", section.get("type"))
            return None
        from marsdog_voice_interaction.providers.kws_sherpa import (
            KWSSherpaProvider,
        )
        provider: BaseProvider = KWSSherpaProvider(section.get("config", {}))
        provider.start()
        return provider

    @staticmethod
    def _build_asr(section: dict[str, Any]) -> BaseProvider | None:
        if not section.get("enabled", True):
            return None
        config = section.get("config", {})
        if section.get("type", "sherpa") == "sherpa":
            from marsdog_voice_interaction.providers.asr_sherpa import (
                ASRSherpaProvider,
            )
            provider: BaseProvider = ASRSherpaProvider(config)
            provider.start()
            if provider.is_available():
                return provider
        from marsdog_voice_interaction.providers.mock_asr import MockASRProvider
        provider = MockASRProvider(config)
        provider.start()
        return provider

    @staticmethod
    def _build_speaker(section: dict[str, Any]) -> BaseProvider | None:
        if not section.get("enabled", True):
            return None
        config = section.get("config", {})
        if section.get("type", "sherpa") == "sherpa":
            from marsdog_voice_interaction.providers.speaker_sherpa import (
                SpeakerSherpaProvider,
            )
            provider: BaseProvider = SpeakerSherpaProvider(config)
            provider.start()
            if provider.is_available():
                return provider
        from marsdog_voice_interaction.providers.mock_speaker import (
            MockSpeakerProvider,
        )
        provider = MockSpeakerProvider(config)
        provider.start()
        return provider

    def _begin_interaction(
        self,
        interaction_id: str | None = None,
        *,
        source: str = "unknown",
    ) -> str:
        """Start one session and return its immutable interaction ID."""
        with self._interaction_lock:
            if self._interaction_active:
                return self._interaction_id
            self._interaction_id = interaction_id or uuid.uuid4().hex
            self._interaction_active = True
            self._interaction_holds.clear()
            self._last_interaction_time = time.time()
            self._state_machine.trigger(Trigger.WAKEUP)
            started_id = self._interaction_id
        self._trace(
            "interaction_start",
            result="started",
            source=source,
            interaction_id=started_id,
            state=self._state_machine.state.value,
        )
        return started_id

    def _refresh_interaction_activity(self, now: float | None = None) -> None:
        with self._interaction_lock:
            if self._interaction_active:
                self._last_interaction_time = time.time() if now is None else now

    def _is_interaction_active(self) -> bool:
        with self._interaction_lock:
            return self._interaction_active

    def _prune_interaction_holds_locked(self, now: float) -> None:
        expired = [
            token for token, hold in self._interaction_holds.items()
            if float(hold["deadline_monotonic"]) <= now
        ]
        for token in expired:
            hold = self._interaction_holds.pop(token, {})
            logger.info("Interaction hold lease expired: token=%s", token)
            self._trace(
                "interaction_hold",
                operation="expire",
                result="expired",
                interaction_id=self._interaction_id,
                hold_token=token,
                reason=str(hold.get("reason", "")),
            )

    def _timeout_interaction_id(self, now: float) -> str:
        """Return the session ID only when it is safe to idle-timeout."""
        with self._interaction_lock:
            if not self._interaction_active:
                return ""
            self._prune_interaction_holds_locked(time.monotonic())
            if self._interaction_holds:
                return ""
            if now - self._last_interaction_time <= self._idle_timeout:
                return ""
            return self._interaction_id

    def _poll_direct_mock(self, direct_mock: BaseProvider) -> None:
        """Run event mock through the same bounded session lifecycle."""
        event = direct_mock.poll_event()  # type: ignore[attr-defined]
        if event is not None:
            event_type = str(event.get("event_type", ""))
            if event_type == EVT_VOICE_CALL_NAME:
                self._begin_interaction(source="mock_event")
                self._publish(event)
            elif not self._is_interaction_active():
                logger.debug(
                    "Ignoring direct mock event outside an interaction: %s",
                    event_type,
                )
                complete = getattr(direct_mock, "complete_interaction", None)
                if callable(complete):
                    complete()
            else:
                self._state_machine.trigger(Trigger.SPEECH_START)
                should_execute = bool(
                    event.get("should_trigger_behavior_tree")
                )
                if should_execute:
                    self._state_machine.trigger(Trigger.INTENT_PARSED)
                else:
                    self._state_machine.trigger(Trigger.SPEECH_END)
                event["state"] = self._state_machine.state.value
                event["previous_state"] = (
                    self._state_machine.previous_state.value
                )
                event.setdefault("utterance_id", uuid.uuid4().hex)
                self._refresh_interaction_activity()
                self._publish(event)

        timed_out_id = self._timeout_interaction_id(time.time())
        if timed_out_id:
            self._end_interaction(
                "interaction_timeout",
                expected_interaction_id=timed_out_id,
            )

    def _poll(self) -> None:
        direct_mock = self._providers.get("mock_event")
        if direct_mock is not None:
            self._poll_direct_mock(direct_mock)
            return

        now = time.time()
        audio = self._providers.get("audio")
        if audio is not None and hasattr(audio, "poll_result"):
            if audio.is_capturing():  # type: ignore[attr-defined]
                session = self._enrollment.speaker_session
                enrollment_active = (
                    session is not None and not session.done
                )
                if not self._is_interaction_active() and not enrollment_active:
                    # A timeout or stop request must not leave a stale capture
                    # starving the wakeup provider at the end of this method.
                    self._cancel_audio_capture(audio)
                else:
                    self._poll_kws_events()
                    result = audio.poll_result()  # type: ignore[attr-defined]
                    if result is not None:
                        self._poll_kws_events()
                        self._finish_kws_utterance()
                        self._latest_audio = result
                        has_voice = bool(result.get("has_voice", True))
                        capture_started = getattr(
                            self,
                            "_utterance_started_monotonic",
                            0.0,
                        )
                        capture_latency_ms = (
                            (time.perf_counter() - capture_started) * 1000.0
                            if capture_started else 0.0
                        )
                        self._trace(
                            "stage_complete",
                            stage="vad_capture",
                            result="voice" if has_voice else "silence",
                            interaction_id=self._interaction_id,
                            utterance_id=self._command_tracker.utterance_id,
                            latency_ms=round(capture_latency_ms, 2),
                            audio_duration_ms=round(
                                float(result.get("duration_ms", 0.0)),
                                2,
                            ),
                        )
                        if enrollment_active:
                            self._process_enrollment_audio(result)
                        elif has_voice:
                            valid_speech = self._process_speech(
                                result,
                                self._command_tracker.utterance_id or None,
                            )
                            if valid_speech:
                                # Only recognized speech (or a KWS event in
                                # _poll_kws_events) extends the conversation.
                                self._refresh_interaction_activity()
                        else:
                            logger.debug(
                                "VAD silence result; idle timer remains at %.3f",
                                self._last_interaction_time,
                            )
                        self._command_tracker.finish()
                        timed_out_id = self._timeout_interaction_id(now)
                        if timed_out_id:
                            self._end_interaction(
                                "interaction_timeout",
                                expected_interaction_id=timed_out_id,
                            )
                        elif self._is_interaction_active():
                            self._start_interaction_capture(audio)
                        return
                    if enrollment_active:
                        return

            session = self._enrollment.speaker_session
            if session is not None and not session.done:
                audio.start_capture()  # type: ignore[attr-defined]
                return

        timed_out_id = self._timeout_interaction_id(now)
        if timed_out_id and not self._audio_speech_active(audio):
            self._end_interaction(
                "interaction_timeout",
                expected_interaction_id=timed_out_id,
            )
            return

        wakeup = self._providers.get("wakeup")
        if wakeup is None:
            return
        event = wakeup.poll_event()  # type: ignore[attr-defined]
        if event is None:
            return
        event["event_type"] = EVT_VOICE_CALL_NAME
        self._begin_interaction(source="wakeup")
        self._publish(event)
        if audio is not None and hasattr(audio, "start_capture"):
            self._start_interaction_capture(audio)

    @staticmethod
    def _audio_speech_active(audio: BaseProvider | None) -> bool:
        """Avoid ending a session in the middle of an unfinished utterance."""
        is_speech_active = getattr(audio, "is_speech_active", None)
        return bool(is_speech_active()) if callable(is_speech_active) else False

    def _process_speech(
        self,
        audio_data: dict[str, Any],
        utterance_id: str | None = None,
    ) -> bool:
        pipeline_started = time.perf_counter()
        self._state_machine.trigger(Trigger.SPEECH_START)
        utterance_id = utterance_id or uuid.uuid4().hex
        asr = self._providers.get("asr")
        speaker = self._providers.get("speaker")
        asr_started = time.perf_counter()
        asr_failed = False
        try:
            asr_result = (
                asr.transcribe(audio_data)  # type: ignore[attr-defined]
                if asr is not None else {}
            )
        except Exception as exc:
            logger.error("ASR failed: %s", exc)
            asr_result = {}
            asr_failed = True
        asr_latency_ms = (time.perf_counter() - asr_started) * 1000.0
        raw_text = str(asr_result.get("asr_text", ""))
        self._trace(
            "stage_complete",
            stage="asr",
            result=("error" if asr_failed else "ok" if raw_text else "empty"),
            interaction_id=self._interaction_id,
            utterance_id=utterance_id,
            latency_ms=round(asr_latency_ms, 2),
            language=str(asr_result.get("language", "")),
            text_length=len(raw_text),
        )

        speaker_started = time.perf_counter()
        speaker_failed = False
        try:
            with self._speaker_operation_lock:
                speaker_result = (
                    speaker.verify(audio_data)  # type: ignore[attr-defined]
                    if speaker is not None else {}
                )
        except Exception as exc:
            logger.error("Speaker verification failed: %s", exc)
            speaker_result = {}
            speaker_failed = True
        speaker_latency_ms = (time.perf_counter() - speaker_started) * 1000.0
        speaker_id = str(speaker_result.get("speaker_id", "unknown"))
        confidence = float(speaker_result.get("confidence", 0))
        self._trace(
            "stage_complete",
            stage="speaker",
            result=(
                "error"
                if speaker_failed
                else "matched" if speaker_id != "unknown" else "unknown"
            ),
            interaction_id=self._interaction_id,
            utterance_id=utterance_id,
            latency_ms=round(speaker_latency_ms, 2),
            speaker_id=speaker_id,
            speaker_confidence=confidence,
        )
        self._publish({
            "event_type": speaker_to_voice_event(speaker_id),
            "utterance_id": utterance_id,
            "speaker_id": speaker_id,
            "speaker_confidence": confidence,
        })

        text = self._clean_text(raw_text)
        if not text:
            self._state_machine.trigger(Trigger.SPEECH_END)
            self._trace(
                "utterance_complete",
                result="empty_asr",
                interaction_id=self._interaction_id,
                utterance_id=utterance_id,
                latency_ms=round(
                    (time.perf_counter() - pipeline_started) * 1000.0,
                    2,
                ),
            )
            return False
        self._publish({
            "event_type": "speech",
            "utterance_id": utterance_id,
            "asr_text": text,
            "speaker_id": speaker_id,
            "speaker_confidence": confidence,
            "language": str(asr_result.get("language", "zh")),
            "latency_ms": float(asr_result.get("latency_ms", 0)),
        })

        lexicon_started = time.perf_counter()
        direct_match = (
            self._command_lexicon.match(text)
            if self._command_lexicon is not None else None
        )
        lexicon_latency_ms = (
            time.perf_counter() - lexicon_started
        ) * 1000.0
        self._trace(
            "stage_complete",
            stage="command_lexicon",
            result=(
                "matched"
                if direct_match is not None
                else "no_match"
                if self._command_lexicon is not None
                else "unavailable"
            ),
            interaction_id=self._interaction_id,
            utterance_id=utterance_id,
            latency_ms=round(lexicon_latency_ms, 2),
            command_key=(
                direct_match.command_key if direct_match is not None else ""
            ),
            event_type=(
                direct_match.event_type if direct_match is not None else ""
            ),
            catalog_version=(
                direct_match.catalog_version
                if direct_match is not None else ""
            ),
            action_name=(
                direct_match.action_name
                if direct_match is not None else ""
            ),
            emotion=(
                direct_match.emotion if direct_match is not None else ""
            ),
            control=(
                direct_match.control if direct_match is not None else ""
            ),
            source_rows=(
                list(direct_match.source_rows)
                if direct_match is not None else []
            ),
            core=(direct_match.core if direct_match is not None else False),
        )
        if direct_match is not None:
            direct_event = direct_match.to_event(
                asr_text=text,
                language=str(asr_result.get("language", "zh")),
            )
            if direct_event.get("should_trigger_behavior_tree"):
                self._state_machine.trigger(Trigger.INTENT_PARSED)
            else:
                self._state_machine.trigger(Trigger.SPEECH_END)
            direct_event.update({
                "utterance_id": utterance_id,
                "speaker_id": speaker_id,
                "speaker_confidence": confidence,
            })
            final_event_type = direct_match.event_type
            duplicate_final = self._command_tracker.is_duplicate_final(
                final_event_type
            )
            conflicting_kws = bool(
                self._command_tracker.immediate_event_types
            ) and not duplicate_final
            if duplicate_final:
                logger.info(
                    "Suppressed duplicate command-lexicon result for "
                    "utterance=%s: %s",
                    utterance_id,
                    final_event_type,
                )
                completion_result = "suppressed_duplicate"
            elif conflicting_kws:
                logger.warning(
                    "Suppressed conflicting command-lexicon result for "
                    "utterance=%s: kws=%s catalog=%s",
                    utterance_id,
                    sorted(self._command_tracker.immediate_event_types),
                    final_event_type,
                )
                self._trace(
                    "command_conflict",
                    result="suppressed",
                    interaction_id=self._interaction_id,
                    utterance_id=utterance_id,
                    immediate_event_types=sorted(
                        self._command_tracker.immediate_event_types
                    ),
                    catalog_event_type=final_event_type,
                )
                completion_result = "suppressed_conflict"
            else:
                self._publish(direct_event)
                completion_result = (
                    "published_direct_command"
                    if direct_event.get("should_trigger_behavior_tree")
                    else "published_catalog_event"
                )
            self._trace(
                "utterance_complete",
                result=completion_result,
                interaction_id=self._interaction_id,
                utterance_id=utterance_id,
                event_type=final_event_type,
                intent_source="command_lexicon",
                latency_ms=round(
                    (time.perf_counter() - pipeline_started) * 1000.0,
                    2,
                ),
            )
            return True

        intent_started = time.perf_counter()
        parsed_intent = self._parse_intent(text)
        intent = parsed_intent or dict(_UNKNOWN_INTENT)
        if intent.get("should_trigger_behavior_tree"):
            self._state_machine.trigger(Trigger.INTENT_PARSED)
        else:
            self._state_machine.trigger(Trigger.SPEECH_END)
        final_event_type = classification_to_voice_event(
            str(intent.get("emotion", "NONE")),
            str(intent.get("action", "UNKNOWN")),
            str(intent.get("control", "CLARIFY")),
        )
        self._trace(
            "stage_complete",
            stage="intent",
            result="parsed" if parsed_intent is not None else "fallback_unknown",
            interaction_id=self._interaction_id,
            utterance_id=utterance_id,
            event_type=final_event_type,
            intent_source=str(intent.get("intent_source", "fallback")),
            latency_ms=round(
                (time.perf_counter() - intent_started) * 1000.0,
                2,
            ),
        )
        intent.update({
            "utterance_id": utterance_id,
            "asr_text": text,
            "speaker_id": speaker_id,
            "speaker_confidence": confidence,
            "event_type": final_event_type,
        })
        duplicate_final = self._command_tracker.is_duplicate_final(final_event_type)
        if duplicate_final:
            logger.info(
                "Suppressed duplicate final intent for utterance=%s: %s",
                utterance_id,
                final_event_type,
            )
        else:
            self._publish(intent)
        self._trace(
            "utterance_complete",
            result="suppressed_duplicate" if duplicate_final else "published",
            interaction_id=self._interaction_id,
            utterance_id=utterance_id,
            event_type=final_event_type,
            latency_ms=round(
                (time.perf_counter() - pipeline_started) * 1000.0,
                2,
            ),
        )
        return True

    def _start_interaction_capture(self, audio: BaseProvider) -> None:
        """Allocate an utterance ID, reset KWS, then start microphone capture."""
        utterance_id = uuid.uuid4().hex
        self._command_tracker.begin(utterance_id)
        self._utterance_started_monotonic = time.perf_counter()
        kws = self._providers.get("kws")
        if kws is not None and kws.is_available():
            kws.start_utterance()  # type: ignore[attr-defined]
        audio.start_capture()  # type: ignore[attr-defined]
        self._trace(
            "stage_start",
            stage="vad_capture",
            result="started",
            interaction_id=self._interaction_id,
            utterance_id=utterance_id,
        )

    def _cancel_audio_capture(self, audio: BaseProvider | None = None) -> None:
        """Stop an in-flight capture without shutting down the provider."""
        audio = audio or self._providers.get("audio")
        if audio is None:
            return
        cancel_capture = getattr(audio, "cancel_capture", None)
        if callable(cancel_capture):
            try:
                if cancel_capture() is False:
                    logger.error(
                        "Audio capture cancellation timed out; "
                        "wakeup recovery may be delayed"
                    )
            except Exception as exc:
                logger.error("Audio capture cancellation failed: %s", exc)
            return
        if (
            hasattr(audio, "is_capturing")
            and audio.is_capturing()  # type: ignore[attr-defined]
        ):
            logger.error(
                "Audio provider has no cancel_capture(); "
                "wakeup remains blocked until capture exits"
            )

    def _end_interaction(
        self,
        reason: str,
        *,
        expected_interaction_id: str = "",
    ) -> bool:
        """Atomically end listening and restore the wakeup polling path."""
        with self._interaction_lock:
            if (
                expected_interaction_id
                and expected_interaction_id != self._interaction_id
            ):
                return False
            if not self._interaction_active:
                self._interaction_holds.clear()
                return False
            interaction_id = self._interaction_id
            self._interaction_active = False
            self._interaction_holds.clear()
            self._cancel_audio_capture()
            self._finish_kws_utterance()
            self._command_tracker.finish()
            self._state_machine.trigger(Trigger.TIMEOUT)
            self._publish({
                "event_type": EVT_STATE_CHANGED,
                "interaction_id": interaction_id,
                "state": "idle",
                "state_reason": reason,
            })
            self._interaction_id = ""
        direct_mock = self._providers.get("mock_event")
        complete = getattr(direct_mock, "complete_interaction", None)
        if callable(complete):
            complete()
        logger.info(
            "Interaction ended: reason=%s; wakeup polling resumed",
            reason,
        )
        self._trace(
            "interaction_end",
            result="ended",
            interaction_id=interaction_id,
            reason=reason,
            state="idle",
        )
        return True

    def _finish_kws_utterance(self) -> None:
        kws = self._providers.get("kws")
        if kws is not None and kws.is_available():
            kws.finish_utterance()  # type: ignore[attr-defined]

    def _poll_kws_events(self) -> None:
        """Publish newly detected commands while the user is still speaking."""
        kws = self._providers.get("kws")
        if (
            kws is None
            or not kws.is_available()
            or not self._command_tracker.is_active
        ):
            return
        while True:
            event = kws.poll_event()  # type: ignore[attr-defined]
            if event is None:
                return
            event_type = str(event.get("event_type", ""))
            if not self._command_tracker.record_immediate(event_type):
                continue
            self._state_machine.trigger(Trigger.SPEECH_START)
            if event.get("should_trigger_behavior_tree"):
                self._state_machine.trigger(Trigger.INTENT_PARSED)
            event["utterance_id"] = self._command_tracker.utterance_id
            self._refresh_interaction_activity()
            self._publish(event)
            utterance_started = getattr(
                self,
                "_utterance_started_monotonic",
                0.0,
            )
            self._trace(
                "stage_complete",
                stage="kws",
                result="detected",
                interaction_id=self._interaction_id,
                utterance_id=self._command_tracker.utterance_id,
                event_type=event_type,
                latency_ms=round(
                    (time.perf_counter() - utterance_started) * 1000.0,
                    2,
                ) if utterance_started else 0.0,
            )

    def _parse_intent(self, text: str) -> dict[str, Any] | None:
        for name in ("intent_llm", "intent_rule"):
            provider = self._providers.get(name)
            if provider is None or not provider.is_available():
                continue
            try:
                result = provider.parse_intent(text)  # type: ignore[attr-defined]
                if result is not None:
                    return result
            except Exception as exc:
                logger.warning("%s intent failed: %s", name, exc)
        return None

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(
            r"""[，。！？、；：“”"'（）【】《》…—～,.!?;:()\[\]<>/\s]+""",
            "",
            text,
        ).strip()

    def _publish(self, partial: dict[str, Any]) -> None:
        value = dict(partial)
        with self._interaction_lock:
            value.setdefault("interaction_id", self._interaction_id)
        value.setdefault("state", self._state_machine.state.value)
        value.setdefault(
            "previous_state", self._state_machine.previous_state.value
        )
        event = normalize_audio_event(value)
        message = String()
        message.data = json.dumps(event, ensure_ascii=False)
        self._audio_pub.publish(message)
        self._trace(
            "event_publish",
            result="published",
            topic=str(
                self._config.get("topics", {}).get(
                    "audio_event",
                    "/perception/audio_event",
                )
            ),
            event_type=str(event.get("event_type", "")),
            interaction_id=str(event.get("interaction_id", "")),
            utterance_id=str(event.get("utterance_id", "")),
            state=str(event.get("state", "")),
            previous_state=str(event.get("previous_state", "")),
            state_reason=str(event.get("state_reason", "")),
            wake_word=str(event.get("wake_word", "")),
            wake_angle=round(float(event.get("wake_angle", 0.0)), 2),
            wake_confidence=round(
                float(event.get("wake_confidence", 0.0)),
                3,
            ),
            speaker_id=str(event.get("speaker_id", "")),
            speaker_confidence=round(
                float(event.get("speaker_confidence", 0.0)),
                3,
            ),
            action=str(event.get("action", "")),
            control=str(event.get("control", "")),
            intent_source=str(event.get("intent_source", "")),
            should_trigger_behavior_tree=bool(
                event.get("should_trigger_behavior_tree", False)
            ),
            latency_ms=round(float(event.get("latency_ms", 0.0)), 2),
            asr_text=str(event.get("asr_text", "")),
            emotion=str(event.get("emotion", "")),
            command_id=str(event.get("command_id", "")),
            intent_category=str(event.get("intent_category", "")),
            intent_confidence=round(
                float(event.get("intent_confidence", 0.0)),
                3,
            ),
            language=str(event.get("language", "")),
            slots=event.get("slots", []),
            payload=event,
        )

    def _process_enrollment_audio(self, audio_data: dict[str, Any]) -> None:
        started = time.perf_counter()
        with self._speaker_operation_lock:
            result = self._enrollment.process_speaker_audio(
                np.asarray(
                    audio_data.get("audio_samples", []),
                    dtype=np.float32,
                ),
                int(audio_data.get("sample_rate", 16000)),
            )
            if result.get("done"):
                self._sync_speaker_registry()
        message = String()
        message.data = json.dumps(result, ensure_ascii=False)
        self._enrollment_pub.publish(message)
        self._trace(
            "enrollment_publish",
            result="complete" if result.get("done") else "progress",
            topic=str(
                self._config.get("topics", {}).get(
                    "enrollment_event",
                    "/perception/voice/enrollment_event",
                )
            ),
            interaction_id=self._interaction_id,
            speaker_id=str(result.get("speaker_id", result.get("name", ""))),
            latency_ms=round(
                (time.perf_counter() - started) * 1000.0,
                2,
            ),
            payload=result,
        )

    def _handle_task(self, request: Any, response: Any) -> Any:
        started = time.perf_counter()
        response.task_id = request.task_id
        response.task_type = request.task_type
        response.success = False
        response.result_json = ""
        response.error_message = ""
        try:
            params = json.loads(request.params_json or "{}")
            if isinstance(params, list):
                params = {
                    str(item.get("key", "")): item.get("value")
                    for item in params if isinstance(item, dict)
                }
            if not isinstance(params, dict):
                params = {}
            result = self._run_task(str(request.task_type), params)
            response.success = bool(result.get("ok", True))
            response.result_json = json.dumps(result, ensure_ascii=False)
            if not response.success:
                response.error_message = str(result.get("error", "task failed"))
        except Exception as exc:
            response.error_message = str(exc)
        response.latency_ms = (time.perf_counter() - started) * 1000
        response_payload = (
            json.loads(response.result_json) if response.result_json else {}
        )
        self._trace(
            "service_complete",
            result="success" if response.success else "failure",
            service=str(
                self._config.get("topics", {}).get(
                    "voice_task",
                    "/perception/voice/task",
                )
            ),
            task_id=str(request.task_id),
            task_type=str(request.task_type),
            interaction_id=str(
                response_payload.get("interaction_id", self._interaction_id)
            ),
            latency_ms=round(response.latency_ms, 2),
            error=response.error_message,
            task_result=response_payload,
        )
        return response

    def _hold_interaction(self, params: dict[str, Any]) -> dict[str, Any]:
        interaction_id = str(params.get("interaction_id", "")).strip()
        hold_token = str(params.get("hold_token", "")).strip()
        reason = str(params.get("reason", "")).strip()
        try:
            lease_sec = float(params.get("lease_sec", 0.0))
        except (TypeError, ValueError):
            lease_sec = float("nan")
        if not interaction_id:
            return {"ok": False, "error": "interaction_id is required"}
        if not hold_token:
            return {"ok": False, "error": "hold_token is required"}
        if not math.isfinite(lease_sec) or lease_sec <= 0.0:
            return {"ok": False, "error": "lease_sec must be finite and > 0"}
        if lease_sec > self._hold_max_lease_sec:
            return {
                "ok": False,
                "error": (
                    "lease_sec exceeds hold_max_lease_sec="
                    f"{self._hold_max_lease_sec:g}"
                ),
            }
        now = time.monotonic()
        with self._interaction_lock:
            self._prune_interaction_holds_locked(now)
            if not self._interaction_active:
                return {"ok": False, "error": "interaction is not active"}
            if interaction_id != self._interaction_id:
                return {"ok": False, "error": "interaction_id mismatch"}
            renewed = hold_token in self._interaction_holds
            self._interaction_holds[hold_token] = {
                "reason": reason,
                "deadline_monotonic": now + lease_sec,
            }
            self._trace(
                "interaction_hold",
                operation="renew" if renewed else "acquire",
                result="held",
                interaction_id=interaction_id,
                hold_token=hold_token,
                reason=reason,
                lease_sec=lease_sec,
            )
            return {
                "ok": True,
                "interaction_id": interaction_id,
                "hold_token": hold_token,
                "held": True,
                "renewed": renewed,
                "lease_sec": lease_sec,
                "expires_in_sec": lease_sec,
            }

    def _release_interaction_hold(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        interaction_id = str(params.get("interaction_id", "")).strip()
        hold_token = str(params.get("hold_token", "")).strip()
        reset_idle_timer = bool(params.get("reset_idle_timer", False))
        if not interaction_id:
            return {"ok": False, "error": "interaction_id is required"}
        if not hold_token:
            return {"ok": False, "error": "hold_token is required"}
        now = time.monotonic()
        with self._interaction_lock:
            self._prune_interaction_holds_locked(now)
            if not self._interaction_active:
                return {"ok": False, "error": "interaction is not active"}
            if interaction_id != self._interaction_id:
                return {"ok": False, "error": "interaction_id mismatch"}
            released = self._interaction_holds.pop(hold_token, None) is not None
            if released and reset_idle_timer:
                self._last_interaction_time = time.time()
            self._trace(
                "interaction_hold",
                operation="release",
                result="released" if released else "not_found",
                interaction_id=interaction_id,
                hold_token=hold_token,
                idle_timer_reset=bool(released and reset_idle_timer),
            )
            return {
                "ok": True,
                "interaction_id": interaction_id,
                "hold_token": hold_token,
                "held": False,
                "released": released,
                "idle_timer_reset": bool(released and reset_idle_timer),
            }

    def _interaction_state(self) -> dict[str, Any]:
        now_monotonic = time.monotonic()
        now_wall = time.time()
        with self._interaction_lock:
            self._prune_interaction_holds_locked(now_monotonic)
            holds = [
                {
                    "hold_token": token,
                    "reason": str(hold.get("reason", "")),
                    "expires_in_sec": max(
                        0.0,
                        float(hold["deadline_monotonic"]) - now_monotonic,
                    ),
                }
                for token, hold in sorted(self._interaction_holds.items())
            ]
            idle_elapsed = (
                max(0.0, now_wall - self._last_interaction_time)
                if self._interaction_active else 0.0
            )
            return {
                "ok": True,
                "listening": self._interaction_active,
                "interaction_active": self._interaction_active,
                "interaction_id": self._interaction_id,
                "state": self._state_machine.state.value,
                "idle_timeout_sec": self._idle_timeout,
                "idle_elapsed_sec": idle_elapsed,
                "hold_active": bool(holds),
                "holds": holds,
            }

    def _run_task(self, task_type: str, params: dict[str, Any]) -> dict[str, Any]:
        if task_type == "start_speaker_enrollment":
            with self._speaker_operation_lock:
                result = self._enrollment.start_speaker(
                    str(params.get("name", "")),
                    int(params.get("required_shots", 3)),
                )
            if result.get("ok"):
                audio = self._providers.get("audio")
                if audio is not None and hasattr(audio, "start_capture"):
                    audio.start_capture()  # type: ignore[attr-defined]
            return result
        if task_type == "cancel_speaker_enrollment":
            with self._speaker_operation_lock:
                return self._enrollment.cancel_speaker()
        if task_type == "upload_speaker":
            payload = base64.b64decode(
                str(params.get("audio_base64", "")), validate=True
            )
            return self._enroll_uploaded_speaker(
                str(params.get("name", "")),
                payload,
            )
        if task_type == "list_speakers":
            with self._speaker_operation_lock:
                return {
                    "ok": True,
                    "speakers": self._enrollment.list_enrolled_speakers(),
                }
        if task_type == "delete_speaker":
            return self._delete_speaker_for_api(
                str(params.get("name", ""))
            )
        if task_type == "verify_speaker":
            speaker = self._providers.get("speaker")
            audio_data = self._decode_audio_params(params) or self._latest_audio
            if speaker is None or audio_data is None:
                return {"ok": False, "error": "speaker or audio unavailable"}
            with self._speaker_operation_lock:
                result = speaker.verify(audio_data)  # type: ignore[attr-defined]
            return {"ok": True, **result}
        if task_type == "hold_interaction":
            return self._hold_interaction(params)
        if task_type == "release_interaction_hold":
            return self._release_interaction_hold(params)
        if task_type == "get_interaction_state":
            return self._interaction_state()
        if task_type == "start_listening":
            expected_id = str(
                params.get("expected_interaction_id", "")
            ).strip()
            with self._interaction_lock:
                if self._interaction_active:
                    if expected_id and expected_id != self._interaction_id:
                        return {
                            "ok": False,
                            "error": "expected_interaction_id mismatch",
                        }
                    interaction_id = self._interaction_id
                    self._last_interaction_time = time.time()
                else:
                    if expected_id:
                        return {
                            "ok": False,
                            "error": "expected interaction is no longer active",
                        }
                    interaction_id = self._begin_interaction(source="service")
            audio = self._providers.get("audio")
            if audio is not None and hasattr(audio, "start_capture"):
                is_capturing = getattr(audio, "is_capturing", None)
                if not callable(is_capturing) or not is_capturing():
                    self._start_interaction_capture(audio)
            return {
                "ok": True,
                "listening": True,
                "interaction_id": interaction_id,
            }
        if task_type == "stop_listening":
            ended = self._end_interaction("stop_listening")
            return {"ok": True, "listening": False, "ended": ended}
        return {"ok": False, "error": f"unsupported task_type: {task_type}"}

    @staticmethod
    def _decode_audio_params(
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        encoded = str(params.get("audio_base64", ""))
        if not encoded:
            return None
        payload = base64.b64decode(encoded, validate=True)
        with wave.open(io.BytesIO(payload), "rb") as source:
            sample_rate = source.getframerate()
            samples = np.frombuffer(
                source.readframes(source.getnframes()), dtype=np.int16
            ).astype(np.float32) / 32768.0
        return {
            "audio_samples": samples,
            "sample_rate": sample_rate,
            "has_voice": True,
        }

    def _wire_speaker_enrollment(self) -> None:
        speaker = self._providers.get("speaker")
        extractor = getattr(speaker, "_extractor", None)
        if extractor is not None:
            self._enrollment.set_speaker_extractor(extractor)

    def _sync_speaker_registry(self) -> None:
        speaker = self._providers.get("speaker")
        if speaker is not None:
            self._enrollment.sync_to_provider(speaker)

    def destroy_node(self) -> None:
        if self._speaker_api is not None:
            self._speaker_api.stop()
            self._speaker_api = None
        with self._speaker_operation_lock:
            for provider in self._providers.values():
                if provider is not None:
                    provider.stop()
        super().destroy_node()


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = VoiceInteractionNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass
