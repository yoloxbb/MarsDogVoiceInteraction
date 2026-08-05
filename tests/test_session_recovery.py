from __future__ import annotations

from collections import deque
import sys
import threading
import types
from types import SimpleNamespace
from typing import Any

from marsdog_voice_interaction.core.interaction_state_machine import (
    Trigger,
    VoiceInteractionStateMachine,
)
from marsdog_voice_interaction.core.utterance_command_tracker import (
    UtteranceCommandTracker,
)
from marsdog_voice_interaction.nodes import voice_interaction_node as node_module
from marsdog_voice_interaction.nodes.voice_interaction_node import (
    VoiceInteractionNode,
)

# Importing PortAudio may probe hardware indefinitely on headless CI. The
# cancellation test replaces _stream_vad, so a minimal module stub is enough.
_sounddevice = types.ModuleType("sounddevice")
_sounddevice.PortAudioError = RuntimeError  # type: ignore[attr-defined]
_sounddevice.InputStream = object  # type: ignore[attr-defined]
sys.modules.setdefault("sounddevice", _sounddevice)

from marsdog_voice_interaction.providers.audio_sherpa import (
    AudioSherpaProvider,
)
from marsdog_voice_interaction.providers import wakeup_xfyun_serial as wakeup_module
from marsdog_voice_interaction.providers.wakeup_xfyun_serial import (
    WakeupXFYunSerialProvider,
)


class _FakeEnrollment:
    speaker_session = None


class _FakeAudio:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result
        self.capturing = result is not None
        self.start_count = 0
        self.cancel_count = 0
        self.speech_active = False

    def is_capturing(self) -> bool:
        return self.capturing

    def poll_result(self) -> dict[str, Any] | None:
        result = self.result
        self.result = None
        if result is not None:
            self.capturing = False
        return result

    def start_capture(self) -> None:
        self.start_count += 1
        self.capturing = True

    def cancel_capture(self) -> bool:
        self.cancel_count += 1
        self.capturing = False
        self.result = None
        return True

    def is_speech_active(self) -> bool:
        return self.speech_active


class _FakeWakeup:
    def __init__(self) -> None:
        self.events: deque[dict[str, Any]] = deque()
        self.poll_count = 0

    def poll_event(self) -> dict[str, Any] | None:
        self.poll_count += 1
        return self.events.popleft() if self.events else None


class _NodeHarness:
    _poll = VoiceInteractionNode._poll
    _start_interaction_capture = VoiceInteractionNode._start_interaction_capture
    _cancel_audio_capture = VoiceInteractionNode._cancel_audio_capture
    _end_interaction = VoiceInteractionNode._end_interaction
    _finish_kws_utterance = VoiceInteractionNode._finish_kws_utterance
    _poll_kws_events = VoiceInteractionNode._poll_kws_events
    _audio_speech_active = staticmethod(
        VoiceInteractionNode._audio_speech_active
    )
    _run_task = VoiceInteractionNode._run_task

    def __init__(self, audio: _FakeAudio, wakeup: _FakeWakeup) -> None:
        self._providers = {
            "audio": audio,
            "wakeup": wakeup,
            "kws": None,
        }
        self._enrollment = _FakeEnrollment()
        self._state_machine = VoiceInteractionStateMachine()
        self._state_machine.trigger(Trigger.WAKEUP)
        self._command_tracker = UtteranceCommandTracker()
        self._interaction_active = True
        self._interaction_id = "interaction-test"
        self._last_interaction_time = 100.0
        self._idle_timeout = 10.0
        self._latest_audio = None
        self.published: list[dict[str, Any]] = []

    def _publish(self, event: dict[str, Any]) -> None:
        self.published.append(event)

    def _process_speech(
        self,
        _audio_data: dict[str, Any],
        _utterance_id: str | None = None,
    ) -> bool:
        raise AssertionError("silence must not enter speech processing")


def test_silence_does_not_refresh_idle_timer_and_wakeup_recovers(
    monkeypatch: Any,
) -> None:
    audio = _FakeAudio({"has_voice": False, "audio_samples": []})
    wakeup = _FakeWakeup()
    node = _NodeHarness(audio, wakeup)
    clock = [108.0]
    monkeypatch.setattr(node_module.time, "time", lambda: clock[0])

    node._poll()

    assert node._last_interaction_time == 100.0
    assert node._interaction_active
    assert audio.start_count == 1
    assert wakeup.poll_count == 0

    clock[0] = 111.0
    node._poll()

    assert not node._interaction_active
    assert audio.cancel_count == 1
    assert not audio.is_capturing()
    assert node.published[-1]["event_type"] == "EVT_STATE_CHANGED"
    assert node.published[-1]["state"] == "idle"
    assert node.published[-1]["state_reason"] == "interaction_timeout"
    assert node.published[-1]["interaction_id"] == "interaction-test"

    wakeup.events.append({"wake_word": "ni2 hao3 wang4 cai2"})
    clock[0] = 112.0
    node._poll()

    assert wakeup.poll_count == 1
    assert node._interaction_active
    assert audio.start_count == 2


def test_latest_voice_result_refreshes_timeout_before_expiry_check(
    monkeypatch: Any,
) -> None:
    audio = _FakeAudio({"has_voice": True, "audio_samples": [0.1]})
    node = _NodeHarness(audio, _FakeWakeup())
    processed: list[dict[str, Any]] = []

    def process_valid(
        result: dict[str, Any], _utterance_id: str | None = None,
    ) -> bool:
        processed.append(result)
        return True

    node._process_speech = process_valid  # type: ignore[method-assign]
    monkeypatch.setattr(node_module.time, "time", lambda: 111.0)

    node._poll()

    assert processed == [{"has_voice": True, "audio_samples": [0.1]}]
    assert node._last_interaction_time == 111.0
    assert node._interaction_active
    assert not node.published
    assert audio.start_count == 1


def test_empty_asr_does_not_refresh_idle_timeout(monkeypatch: Any) -> None:
    audio = _FakeAudio({"has_voice": True, "audio_samples": [0.1]})
    node = _NodeHarness(audio, _FakeWakeup())
    node._process_speech = lambda *_args: False  # type: ignore[method-assign]
    monkeypatch.setattr(node_module.time, "time", lambda: 111.0)

    node._poll()

    assert node._last_interaction_time == 100.0
    assert not node._interaction_active
    assert node.published[-1]["state_reason"] == "interaction_timeout"


def test_active_speech_is_not_cut_off_by_idle_timeout(monkeypatch: Any) -> None:
    audio = _FakeAudio()
    audio.capturing = True
    audio.speech_active = True
    node = _NodeHarness(audio, _FakeWakeup())
    monkeypatch.setattr(node_module.time, "time", lambda: 111.0)

    node._poll()

    assert node._interaction_active
    assert audio.cancel_count == 0
    assert not node.published


def test_stop_listening_cancels_capture_immediately() -> None:
    audio = _FakeAudio()
    audio.capturing = True
    node = _NodeHarness(audio, _FakeWakeup())

    result = node._run_task("stop_listening", {})

    assert result == {"ok": True, "listening": False}
    assert not node._interaction_active
    assert not audio.is_capturing()
    assert audio.cancel_count == 1
    assert node.published[-1]["state_reason"] == "stop_listening"


def test_real_audio_capture_can_be_cancelled_without_stale_result() -> None:
    provider = AudioSherpaProvider({})
    provider.available = True

    def wait_for_cancel(
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        cancel_event.wait(1.0)
        return {"has_voice": False, "audio_samples": []}

    provider._stream_vad = wait_for_cancel  # type: ignore[method-assign]
    provider.start_capture()

    assert provider.is_capturing()
    assert provider.cancel_capture(timeout=0.5)
    assert not provider.is_capturing()
    assert provider.poll_result() is None


def test_wakeup_provider_reconnects_after_reader_thread_stops(
    monkeypatch: Any,
) -> None:
    class FakeReader:
        instances: list["FakeReader"] = []

        def __init__(self, **_kwargs: Any) -> None:
            self.running = False
            self.error: str | None = None
            self.closed = False
            self.__class__.instances.append(self)

        def open(self) -> None:
            self.running = True

        def close(self) -> None:
            self.running = False
            self.closed = True

        def get_message(self, **_kwargs: Any) -> None:
            return None

        @property
        def is_running(self) -> bool:
            return self.running

        @property
        def last_error(self) -> str | None:
            return self.error

    monkeypatch.setattr(wakeup_module, "XFYunSerialReader", FakeReader)
    provider = WakeupXFYunSerialProvider({
        "reconnect_interval_sec": 0.0,
    })
    provider.start()
    first = provider.reader
    assert first is not None

    first.running = False
    first.error = "USB serial disconnected"
    provider.poll_event()

    assert first.closed
    assert provider.reader is not None
    assert provider.reader is not first
    assert provider.is_available()
    assert len(FakeReader.instances) == 2


def test_real_wakeup_provider_is_retained_when_usb_is_missing_at_start(
    monkeypatch: Any,
) -> None:
    class ReconnectingProvider:
        def __init__(self, _config: dict[str, Any]) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

        def is_available(self) -> bool:
            return False

    monkeypatch.setattr(
        wakeup_module,
        "WakeupXFYunSerialProvider",
        ReconnectingProvider,
    )
    node = SimpleNamespace(_config={"mock": {"enabled": False}})

    provider = VoiceInteractionNode._build_wakeup(
        node,
        {"type": "xfyun_serial", "enabled": True, "config": {}},
    )

    assert isinstance(provider, ReconnectingProvider)
    assert provider.started
