from __future__ import annotations

from collections import deque
import sys
import threading
import types
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

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
from marsdog_voice_interaction.providers.mock_event import MockEventProvider


def test_vad_segment_includes_configured_pre_roll() -> None:
    provider = AudioSherpaProvider({"sample_rate": 10, "pre_roll_sec": 0.3})
    captured = np.arange(10, dtype=np.float32)
    segment = SimpleNamespace(start=6, samples=[60.0, 61.0])

    result = provider._segment_with_pre_roll(segment, captured)

    assert result.tolist() == [3.0, 4.0, 5.0, 60.0, 61.0]


def test_vad_segment_pre_roll_is_clamped_at_capture_start() -> None:
    provider = AudioSherpaProvider({"sample_rate": 10, "pre_roll_sec": 0.3})
    captured = np.arange(10, dtype=np.float32)
    segment = SimpleNamespace(start=1, samples=[60.0])

    result = provider._segment_with_pre_roll(segment, captured)

    assert result.tolist() == [0.0, 60.0]


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
    _trace = VoiceInteractionNode._trace
    _poll = VoiceInteractionNode._poll
    _poll_direct_mock = VoiceInteractionNode._poll_direct_mock
    _begin_interaction = VoiceInteractionNode._begin_interaction
    _refresh_interaction_activity = (
        VoiceInteractionNode._refresh_interaction_activity
    )
    _is_interaction_active = VoiceInteractionNode._is_interaction_active
    _prune_interaction_holds_locked = (
        VoiceInteractionNode._prune_interaction_holds_locked
    )
    _timeout_interaction_id = VoiceInteractionNode._timeout_interaction_id
    _start_interaction_capture = VoiceInteractionNode._start_interaction_capture
    _cancel_audio_capture = VoiceInteractionNode._cancel_audio_capture
    _end_interaction = VoiceInteractionNode._end_interaction
    _finish_kws_utterance = VoiceInteractionNode._finish_kws_utterance
    _poll_kws_events = VoiceInteractionNode._poll_kws_events
    _audio_speech_active = staticmethod(
        VoiceInteractionNode._audio_speech_active
    )
    _run_task = VoiceInteractionNode._run_task
    _hold_interaction = VoiceInteractionNode._hold_interaction
    _release_interaction_hold = VoiceInteractionNode._release_interaction_hold
    _interaction_state = VoiceInteractionNode._interaction_state

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
        self._interaction_lock = threading.RLock()
        self._interaction_active = True
        self._interaction_id = "interaction-test"
        self._last_interaction_time = 100.0
        self._interaction_holds: dict[str, dict[str, Any]] = {}
        self._idle_timeout = 10.0
        self._hold_max_lease_sec = 30.0
        self._latest_audio = None
        self.published: list[dict[str, Any]] = []

    def _publish(self, event: dict[str, Any]) -> None:
        value = dict(event)
        value.setdefault("interaction_id", self._interaction_id)
        self.published.append(value)

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

    assert result == {"ok": True, "listening": False, "ended": True}
    assert not node._interaction_active
    assert not audio.is_capturing()
    assert audio.cancel_count == 1
    assert node.published[-1]["state_reason"] == "stop_listening"


@pytest.mark.parametrize(
    "task_type",
    ["upload_speaker", "list_speakers", "delete_speaker"],
)
def test_removed_legacy_speaker_tasks_are_unsupported(task_type: str) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())

    result = node._run_task(task_type, {})

    assert result == {
        "ok": False,
        "error": f"unsupported task_type: {task_type}",
    }


def test_interaction_hold_pauses_idle_timeout(monkeypatch: Any) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    wall_clock = [111.0]
    monotonic_clock = [50.0]
    monkeypatch.setattr(node_module.time, "time", lambda: wall_clock[0])
    monkeypatch.setattr(
        node_module.time,
        "monotonic",
        lambda: monotonic_clock[0],
    )

    result = node._run_task("hold_interaction", {
        "interaction_id": "interaction-test",
        "hold_token": "wake-engagement:interaction-test",
        "lease_sec": 6.0,
        "reason": "wake_target_approach",
    })
    node._poll()

    assert result["ok"]
    assert not result["renewed"]
    assert node._interaction_active
    assert not node.published


def test_interaction_hold_renewal_is_idempotent(monkeypatch: Any) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    clock = [50.0]
    monkeypatch.setattr(node_module.time, "monotonic", lambda: clock[0])
    params = {
        "interaction_id": "interaction-test",
        "hold_token": "wake-engagement:interaction-test",
        "lease_sec": 6.0,
    }

    first = node._run_task("hold_interaction", params)
    clock[0] = 54.0
    second = node._run_task("hold_interaction", params)

    assert first["ok"] and not first["renewed"]
    assert second["ok"] and second["renewed"]
    assert node._interaction_holds[params["hold_token"]][
        "deadline_monotonic"
    ] == 60.0


def test_expired_hold_restores_normal_timeout(monkeypatch: Any) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    wall_clock = [111.0]
    monotonic_clock = [50.0]
    monkeypatch.setattr(node_module.time, "time", lambda: wall_clock[0])
    monkeypatch.setattr(
        node_module.time,
        "monotonic",
        lambda: monotonic_clock[0],
    )
    node._run_task("hold_interaction", {
        "interaction_id": "interaction-test",
        "hold_token": "short",
        "lease_sec": 1.0,
    })

    monotonic_clock[0] = 52.0
    node._poll()

    assert not node._interaction_active
    assert node.published[-1]["state_reason"] == "interaction_timeout"
    assert not node._interaction_holds


def test_hold_rejects_wrong_session_and_oversized_lease() -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())

    mismatch = node._run_task("hold_interaction", {
        "interaction_id": "other",
        "hold_token": "token",
        "lease_sec": 6.0,
    })
    oversized = node._run_task("hold_interaction", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "lease_sec": 31.0,
    })

    assert not mismatch["ok"]
    assert mismatch["error"] == "interaction_id mismatch"
    assert not oversized["ok"]
    assert "hold_max_lease_sec=30" in oversized["error"]


def test_release_hold_can_restart_idle_timer(monkeypatch: Any) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    wall_clock = [111.0]
    monotonic_clock = [50.0]
    monkeypatch.setattr(node_module.time, "time", lambda: wall_clock[0])
    monkeypatch.setattr(
        node_module.time,
        "monotonic",
        lambda: monotonic_clock[0],
    )
    params = {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "lease_sec": 6.0,
    }
    node._run_task("hold_interaction", params)

    wall_clock[0] = 200.0
    released = node._run_task("release_interaction_hold", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "reset_idle_timer": True,
    })
    wall_clock[0] = 209.0
    node._poll()

    assert released["released"]
    assert released["idle_timer_reset"]
    assert node._interaction_active

    wall_clock[0] = 211.0
    node._poll()
    assert not node._interaction_active


def test_duplicate_release_does_not_reset_idle_timer(monkeypatch: Any) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    wall_clock = [200.0]
    monkeypatch.setattr(node_module.time, "time", lambda: wall_clock[0])
    node._run_task("hold_interaction", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "lease_sec": 6.0,
    })

    first = node._run_task("release_interaction_hold", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "reset_idle_timer": True,
    })
    wall_clock[0] = 205.0
    duplicate = node._run_task("release_interaction_hold", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "reset_idle_timer": True,
    })

    assert first["released"] and first["idle_timer_reset"]
    assert not duplicate["released"]
    assert not duplicate["idle_timer_reset"]
    assert node._last_interaction_time == 200.0


def test_release_rejects_wrong_session_without_removing_hold() -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    node._run_task("hold_interaction", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "lease_sec": 6.0,
    })

    result = node._run_task("release_interaction_hold", {
        "interaction_id": "other-interaction",
        "hold_token": "token",
        "reset_idle_timer": True,
    })

    assert not result["ok"]
    assert result["error"] == "interaction_id mismatch"
    assert "token" in node._interaction_holds
    assert node._last_interaction_time == 100.0


def test_stop_and_end_clear_all_interaction_holds() -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    node._run_task("hold_interaction", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "lease_sec": 6.0,
    })

    node._run_task("stop_listening", {})

    assert not node._interaction_holds


def test_start_listening_returns_id_and_will_not_resurrect_old_session() -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    current = node._run_task("start_listening", {
        "expected_interaction_id": "interaction-test",
    })
    mismatch = node._run_task("start_listening", {
        "expected_interaction_id": "other",
    })
    node._run_task("stop_listening", {})
    stale = node._run_task("start_listening", {
        "expected_interaction_id": "interaction-test",
    })
    fresh = node._run_task("start_listening", {})

    assert current["interaction_id"] == "interaction-test"
    assert not mismatch["ok"]
    assert not stale["ok"]
    assert fresh["ok"]
    assert fresh["interaction_id"] != "interaction-test"


def test_get_interaction_state_reports_hold_lease(monkeypatch: Any) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    monotonic_clock = [50.0]
    monkeypatch.setattr(
        node_module.time,
        "monotonic",
        lambda: monotonic_clock[0],
    )
    node._run_task("hold_interaction", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "lease_sec": 6.0,
        "reason": "wake_target_approach",
    })

    result = node._run_task("get_interaction_state", {})

    assert result["interaction_id"] == "interaction-test"
    assert result["hold_active"]
    assert result["holds"][0]["hold_token"] == "token"
    assert result["holds"][0]["expires_in_sec"] == 6.0


def test_direct_mock_uses_one_id_until_idle_timeout(monkeypatch: Any) -> None:
    provider = MockEventProvider({
        "enabled": True,
        "event_interval_sec": 0.1,
        "seed": 1,
    })
    provider.start()
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    node._providers = {"mock_event": provider, "audio": _FakeAudio()}
    node._interaction_active = False
    node._interaction_id = ""
    node._state_machine = VoiceInteractionStateMachine()
    wall_clock = [100.0]
    monkeypatch.setattr(node_module.time, "time", lambda: wall_clock[0])

    provider._next = 0.0
    node._poll()
    interaction_id = node._interaction_id
    provider._next = 0.0
    node._poll()

    assert interaction_id
    assert node.published[0]["event_type"] == "EVT_VOICE_CALL_NAME"
    assert node.published[0]["interaction_id"] == interaction_id
    assert node.published[1]["interaction_id"] == interaction_id

    wall_clock[0] = 111.0
    node._poll()

    assert node.published[-1]["event_type"] == "EVT_STATE_CHANGED"
    assert node.published[-1]["interaction_id"] == interaction_id
    assert not node._interaction_active

    wall_clock[0] = 112.0
    provider._next = 0.0
    node._poll()
    second_interaction_id = node._interaction_id

    assert second_interaction_id
    assert second_interaction_id != interaction_id
    assert node.published[-1]["event_type"] == "EVT_VOICE_CALL_NAME"
    assert node.published[-1]["interaction_id"] == second_interaction_id


def test_direct_mock_non_executable_event_returns_to_attention(
    monkeypatch: Any,
) -> None:
    provider = MockEventProvider({"enabled": True})
    event = provider.build_event("EVT_VOICE_NEUTRAL")
    provider.poll_event = lambda: event  # type: ignore[method-assign]
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    node._providers = {"mock_event": provider, "audio": _FakeAudio()}
    monkeypatch.setattr(node_module.time, "time", lambda: 100.0)

    node._poll_direct_mock(provider)
    state = node._run_task("get_interaction_state", {})

    assert not event["should_trigger_behavior_tree"]
    assert node.published[-1]["state"] == "attention"
    assert node.published[-1]["previous_state"] == "interaction"
    assert state["state"] == node.published[-1]["state"]


def test_xfyun_wakeup_score_is_normalized_and_raw_score_is_preserved() -> None:
    provider = WakeupXFYunSerialProvider({"wake_score_scale": 1000.0})
    result = provider._parse_event({
        "content": {
            "eventType": 4,
            "info": (
                '{"ivw":{"keyword":"ni2 hao3 wang4 cai2",'
                '"score":907.0,"angle":100.0}}'
            ),
        },
    })

    assert result is not None
    assert result["wake_confidence"] == 0.907
    assert result["wake_score_raw"] == 907.0
    assert result["wake_angle"] == 100.0
    assert result["header"]["frame_id"] == "microphone_array"


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


def test_blocked_sounddevice_read_is_aborted_during_cancel(
    monkeypatch: Any,
) -> None:
    read_started = threading.Event()
    read_released = threading.Event()

    class BlockingInputStream:
        instances: list["BlockingInputStream"] = []

        def __init__(self, **_kwargs: Any) -> None:
            self.abort_count = 0
            self.close_count = 0
            self.close_thread_name = ""
            self.__class__.instances.append(self)

        def start(self) -> None:
            return None

        def read(self, frames: int) -> tuple[np.ndarray, None]:
            read_started.set()
            read_released.wait(5.0)
            return np.zeros((frames, 1), dtype=np.float32), None

        def abort(self) -> None:
            self.abort_count += 1
            read_released.set()

        def stop(self) -> None:
            read_released.set()

        def close(self) -> None:
            self.close_count += 1
            self.close_thread_name = threading.current_thread().name
            read_released.set()

    class FakeVad:
        is_speech_detected = False

        def reset(self) -> None:
            return None

        def empty(self) -> bool:
            return True

    from marsdog_voice_interaction.providers import audio_sherpa as audio_module

    monkeypatch.setattr(audio_module, "_HAS_AUDIO_CAPTURE", True)
    monkeypatch.setattr(
        audio_module.sd,
        "InputStream",
        BlockingInputStream,
    )
    provider = AudioSherpaProvider({})
    provider._vad = FakeVad()
    provider.available = True
    provider.start_capture()

    assert read_started.wait(0.5)
    assert provider.cancel_capture(timeout=0.5)
    assert not provider.is_capturing()
    assert provider.poll_result() is None
    stream = BlockingInputStream.instances[0]
    assert stream.abort_count == 1
    assert stream.close_count == 1
    assert stream.close_thread_name == "vad-capture"


def test_blocked_arecord_read_is_terminated_during_cancel(
    monkeypatch: Any,
) -> None:
    import subprocess
    from marsdog_voice_interaction.providers import audio_sherpa as audio_module

    read_started = threading.Event()
    read_released = threading.Event()

    class BlockingStdout:
        def read(self, size: int) -> bytes:
            read_started.set()
            read_released.wait(5.0)
            return b"\x00" * size

    class BlockingProcess:
        instances: list["BlockingProcess"] = []

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.stdout = BlockingStdout()
            self.running = True
            self.terminated = False
            self.__class__.instances.append(self)

        def poll(self) -> int | None:
            return None if self.running else 0

        def terminate(self) -> None:
            self.terminated = True
            self.running = False
            read_released.set()

        def communicate(
            self,
            timeout: float | None = None,
        ) -> tuple[bytes, bytes]:
            del timeout
            return b"", b""

        def kill(self) -> None:
            self.running = False
            read_released.set()

    class FakeVad:
        def reset(self) -> None:
            return None

        def empty(self) -> bool:
            return True

    monkeypatch.setattr(audio_module, "_HAS_AUDIO_CAPTURE", False)
    monkeypatch.setattr(subprocess, "Popen", BlockingProcess)
    provider = AudioSherpaProvider({})
    provider._vad = FakeVad()
    provider.available = True
    provider.start_capture()

    assert read_started.wait(0.5)
    assert provider.cancel_capture(timeout=0.5)
    assert not provider.is_capturing()
    assert provider.poll_result() is None
    assert BlockingProcess.instances[0].terminated


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
