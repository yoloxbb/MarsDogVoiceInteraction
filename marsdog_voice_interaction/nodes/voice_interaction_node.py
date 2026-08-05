"""Independent ROS2 node for wakeup, ASR, speaker and intent interaction."""

from __future__ import annotations

import base64
import io
import json
import re
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
from marsdog_voice_interaction.utils.logging_utils import get_logger, setup_logging


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
        self.declare_parameter("log_level", "INFO")
        self.declare_parameter("log_dir", "log")
        config_path = str(self.get_parameter("config_path").value)
        setup_logging(
            log_dir=str(self.get_parameter("log_dir").value),
            level=str(self.get_parameter("log_level").value),
            node="voice_interaction",
        )
        try:
            self._config = load_config(config_path)
        except Exception as exc:
            logger.error("Cannot load voice config %s: %s", config_path, exc)
            self._config = {}

        set_storage_root(
            self._config.get("storage", {}).get("root", "data")
        )
        self._enrollment = SpeakerEnrollmentManager()
        self._state_machine = VoiceInteractionStateMachine()
        self._providers: dict[str, BaseProvider | None] = {}
        self._interaction_active = False
        self._interaction_id = ""
        self._last_interaction_time = 0.0
        self._latest_audio: dict[str, Any] | None = None
        self._command_tracker = UtteranceCommandTracker()

        interaction = self._config.get("interaction", {})
        self._idle_timeout = float(interaction.get("idle_timeout_sec", 10))
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

    def _poll(self) -> None:
        direct_mock = self._providers.get("mock_event")
        if direct_mock is not None:
            event = direct_mock.poll_event()  # type: ignore[attr-defined]
            if event is not None:
                if event.get("event_type") == EVT_VOICE_CALL_NAME:
                    self._interaction_id = uuid.uuid4().hex
                    self._interaction_active = True
                self._publish(event)
            return

        now = time.time()
        audio = self._providers.get("audio")
        if audio is not None and hasattr(audio, "poll_result"):
            if audio.is_capturing():  # type: ignore[attr-defined]
                session = self._enrollment.speaker_session
                enrollment_active = (
                    session is not None and not session.done
                )
                if not self._interaction_active and not enrollment_active:
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
                                self._last_interaction_time = time.time()
                        else:
                            logger.debug(
                                "VAD silence result; idle timer remains at %.3f",
                                self._last_interaction_time,
                            )
                        self._command_tracker.finish()
                        if (
                            self._interaction_active
                            and now - self._last_interaction_time
                            > self._idle_timeout
                        ):
                            self._end_interaction("interaction_timeout")
                        elif self._interaction_active:
                            self._start_interaction_capture(audio)
                        return
                    if enrollment_active:
                        return

            session = self._enrollment.speaker_session
            if session is not None and not session.done:
                audio.start_capture()  # type: ignore[attr-defined]
                return

        if (
            self._interaction_active
            and now - self._last_interaction_time > self._idle_timeout
            and not self._audio_speech_active(audio)
        ):
            self._end_interaction("interaction_timeout")
            return

        wakeup = self._providers.get("wakeup")
        if wakeup is None:
            return
        event = wakeup.poll_event()  # type: ignore[attr-defined]
        if event is None:
            return
        event["event_type"] = EVT_VOICE_CALL_NAME
        self._state_machine.trigger(Trigger.WAKEUP)
        self._interaction_id = uuid.uuid4().hex
        self._interaction_active = True
        self._publish(event)
        self._last_interaction_time = now
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
        self._state_machine.trigger(Trigger.SPEECH_START)
        utterance_id = utterance_id or uuid.uuid4().hex
        asr = self._providers.get("asr")
        speaker = self._providers.get("speaker")
        try:
            asr_result = (
                asr.transcribe(audio_data)  # type: ignore[attr-defined]
                if asr is not None else {}
            )
        except Exception as exc:
            logger.error("ASR failed: %s", exc)
            asr_result = {}
        try:
            speaker_result = (
                speaker.verify(audio_data)  # type: ignore[attr-defined]
                if speaker is not None else {}
            )
        except Exception as exc:
            logger.error("Speaker verification failed: %s", exc)
            speaker_result = {}
        speaker_id = str(speaker_result.get("speaker_id", "unknown"))
        confidence = float(speaker_result.get("confidence", 0))
        self._publish({
            "event_type": speaker_to_voice_event(speaker_id),
            "utterance_id": utterance_id,
            "speaker_id": speaker_id,
            "speaker_confidence": confidence,
        })

        text = self._clean_text(str(asr_result.get("asr_text", "")))
        if not text:
            self._state_machine.trigger(Trigger.SPEECH_END)
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

        intent = self._parse_intent(text) or dict(_UNKNOWN_INTENT)
        if intent.get("should_trigger_behavior_tree"):
            self._state_machine.trigger(Trigger.INTENT_PARSED)
        else:
            self._state_machine.trigger(Trigger.SPEECH_END)
        final_event_type = classification_to_voice_event(
            str(intent.get("emotion", "NONE")),
            str(intent.get("action", "UNKNOWN")),
            str(intent.get("control", "CLARIFY")),
        )
        intent.update({
            "utterance_id": utterance_id,
            "asr_text": text,
            "speaker_id": speaker_id,
            "speaker_confidence": confidence,
            "event_type": final_event_type,
        })
        if self._command_tracker.is_duplicate_final(final_event_type):
            logger.info(
                "Suppressed duplicate final intent for utterance=%s: %s",
                utterance_id,
                final_event_type,
            )
        else:
            self._publish(intent)
        return True

    def _start_interaction_capture(self, audio: BaseProvider) -> None:
        """Allocate an utterance ID, reset KWS, then start microphone capture."""
        utterance_id = uuid.uuid4().hex
        self._command_tracker.begin(utterance_id)
        kws = self._providers.get("kws")
        if kws is not None and kws.is_available():
            kws.start_utterance()  # type: ignore[attr-defined]
        audio.start_capture()  # type: ignore[attr-defined]

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

    def _end_interaction(self, reason: str) -> None:
        """Atomically end listening and restore the wakeup polling path."""
        interaction_id = self._interaction_id
        self._interaction_active = False
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
        logger.info(
            "Interaction ended: reason=%s; wakeup polling resumed",
            reason,
        )

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
            self._last_interaction_time = time.time()
            self._publish(event)

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
        value.setdefault("interaction_id", self._interaction_id)
        value.setdefault("state", self._state_machine.state.value)
        value.setdefault(
            "previous_state", self._state_machine.previous_state.value
        )
        event = normalize_audio_event(value)
        message = String()
        message.data = json.dumps(event, ensure_ascii=False)
        self._audio_pub.publish(message)

    def _process_enrollment_audio(self, audio_data: dict[str, Any]) -> None:
        result = self._enrollment.process_speaker_audio(
            np.asarray(audio_data.get("audio_samples", []), dtype=np.float32),
            int(audio_data.get("sample_rate", 16000)),
        )
        if result.get("done"):
            self._sync_speaker_registry()
        message = String()
        message.data = json.dumps(result, ensure_ascii=False)
        self._enrollment_pub.publish(message)

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
        return response

    def _run_task(self, task_type: str, params: dict[str, Any]) -> dict[str, Any]:
        if task_type == "start_speaker_enrollment":
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
            return self._enrollment.cancel_speaker()
        if task_type == "upload_speaker":
            payload = base64.b64decode(
                str(params.get("audio_base64", "")), validate=True
            )
            result = self._enrollment.enroll_speaker_from_audio(
                str(params.get("name", "")), payload
            )
            if result.get("ok"):
                self._sync_speaker_registry()
            return result
        if task_type == "list_speakers":
            return {
                "ok": True,
                "speakers": self._enrollment.list_enrolled_speakers(),
            }
        if task_type == "delete_speaker":
            return self._enrollment.delete_speaker(
                str(params.get("name", ""))
            )
        if task_type == "verify_speaker":
            speaker = self._providers.get("speaker")
            audio_data = self._decode_audio_params(params) or self._latest_audio
            if speaker is None or audio_data is None:
                return {"ok": False, "error": "speaker or audio unavailable"}
            result = speaker.verify(audio_data)  # type: ignore[attr-defined]
            return {"ok": True, **result}
        if task_type == "start_listening":
            if not self._interaction_id:
                self._interaction_id = uuid.uuid4().hex
            self._interaction_active = True
            self._last_interaction_time = time.time()
            audio = self._providers.get("audio")
            if audio is not None and hasattr(audio, "start_capture"):
                self._start_interaction_capture(audio)
            return {"ok": True, "listening": True}
        if task_type == "stop_listening":
            self._end_interaction("stop_listening")
            return {"ok": True, "listening": False}
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
