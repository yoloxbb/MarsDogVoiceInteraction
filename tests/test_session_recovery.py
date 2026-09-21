from __future__ import annotations

from collections import deque
from pathlib import Path
import sys
import threading
import time
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
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        *,
        start_result: bool = True,
    ) -> None:
        self.result = result
        self.capturing = result is not None
        self.start_count = 0
        self.cancel_count = 0
        self.speech_active = False
        self.start_result = start_result

    def is_capturing(self) -> bool:
        return self.capturing

    def poll_result(self) -> dict[str, Any] | None:
        result = self.result
        self.result = None
        if result is not None:
            self.capturing = False
        return result

    def start_capture(self) -> bool:
        self.start_count += 1
        if not self.start_result:
            return False
        self.capturing = True
        return True

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
    _resolve_max_interaction_duration = staticmethod(
        VoiceInteractionNode._resolve_max_interaction_duration
    )
    _refresh_for_asr_result = (
        VoiceInteractionNode._refresh_for_asr_result
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
        self._interaction_started_time = 100.0
        self._last_interaction_time = 100.0
        self._last_interaction_activity_reason = "interaction_start"
        self._interaction_holds: dict[str, dict[str, Any]] = {}
        self._idle_timeout = 10.0
        self._max_interaction_duration = 120.0
        self._refresh_on_any_speech = False
        self._hold_max_lease_sec = 30.0
        self._latest_audio = None
        self._utterance_started_monotonic = 0.0
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
    monkeypatch.setattr(node_module.time, "monotonic", lambda: clock[0])

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
    assert node.published[-1]["event_type"] == "EVT_VOICE_WAKEUP"
    assert not node.published[-1].get("utterance_id")


def test_accepted_voice_result_refreshes_timeout_before_expiry_check(
    monkeypatch: Any,
) -> None:
    audio = _FakeAudio({"has_voice": True, "audio_samples": [0.1]})
    node = _NodeHarness(audio, _FakeWakeup())
    processed: list[dict[str, Any]] = []

    def process_valid(
        result: dict[str, Any], _utterance_id: str | None = None,
    ) -> bool:
        processed.append(result)
        node._refresh_interaction_activity(reason="accepted_test_utterance")
        return True

    node._process_speech = process_valid  # type: ignore[method-assign]
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 111.0)

    node._poll()

    assert processed == [{"has_voice": True, "audio_samples": [0.1]}]
    assert node._last_interaction_time == 111.0
    assert node._last_interaction_activity_reason == "accepted_test_utterance"
    assert node._interaction_active
    assert not node.published
    assert audio.start_count == 1


def test_provider_utterance_id_is_not_relabelled_by_the_tracker(
    monkeypatch: Any,
) -> None:
    """A provider that reports its own ID wins over the node's tracker.

    On 2026-09-20 14:33 a wakeup arrived mid-capture; the new start was
    refused while the previous worker kept recording. Relabelling that
    audio with the refused utterance's ID misattributed it in traces,
    debug audio and downstream events.
    """
    audio = _FakeAudio(
        {
            "has_voice": True,
            "audio_samples": [0.1],
            "utterance_id": "worker-utterance",
        }
    )
    node = _NodeHarness(audio, _FakeWakeup())
    node._command_tracker.begin("refused-utterance")
    seen: list[str | None] = []

    def process_valid(
        _result: dict[str, Any], utterance_id: str | None = None,
    ) -> bool:
        seen.append(utterance_id)
        return True

    node._process_speech = process_valid  # type: ignore[method-assign]
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 101.0)

    node._poll()

    assert seen == ["worker-utterance"]
    assert node._latest_audio is not None
    assert node._latest_audio["utterance_id"] == "worker-utterance"


def test_refused_capture_start_drops_the_new_utterance_id(
    monkeypatch: Any,
) -> None:
    """A refused start must not leave its ID claiming the device."""
    audio = _FakeAudio(start_result=False)
    wakeup = _FakeWakeup()
    wakeup.events.append({"wake_word": "xiao3 wei1 xiao3 wei1"})
    node = _NodeHarness(audio, wakeup)
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 101.0)

    node._poll()

    assert audio.start_count == 1
    assert node._command_tracker.utterance_id == ""
    assert node._utterance_started_monotonic == 0.0


def test_wakeup_during_capture_cancels_the_stale_worker_first(
    monkeypatch: Any,
) -> None:
    """A wakeup mid-capture supersedes the utterance already recording.

    start_capture() refuses while the old worker still holds the device,
    so the node must cancel it first — otherwise the stale audio (the
    wake word included) is later processed as the new utterance.
    """
    audio = _FakeAudio()
    audio.capturing = True
    wakeup = _FakeWakeup()
    wakeup.events.append({"wake_word": "xiao3 wei1 xiao3 wei1"})
    node = _NodeHarness(audio, wakeup)
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 101.0)

    node._poll()

    assert audio.cancel_count == 1
    assert audio.start_count == 1
    assert node.published[-1]["event_type"] == "EVT_VOICE_WAKEUP"


def test_wakeup_without_an_active_capture_does_not_cancel(
    monkeypatch: Any,
) -> None:
    audio = _FakeAudio()
    wakeup = _FakeWakeup()
    wakeup.events.append({"wake_word": "xiao3 wei1 xiao3 wei1"})
    node = _NodeHarness(audio, wakeup)
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 101.0)

    node._poll()

    assert audio.cancel_count == 0
    assert audio.start_count == 1


def test_vad_voice_with_empty_asr_does_not_refresh_idle_timeout(
    monkeypatch: Any,
) -> None:
    audio = _FakeAudio({"has_voice": True, "audio_samples": [0.1]})
    node = _NodeHarness(audio, _FakeWakeup())
    node._process_speech = lambda *_args: False  # type: ignore[method-assign]
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 111.0)

    node._poll()

    assert node._last_interaction_time == 100.0
    assert node._last_interaction_activity_reason == "interaction_start"
    assert not node._interaction_active
    assert node.published[-1]["state_reason"] == "interaction_timeout"
    assert audio.start_count == 0


def test_refresh_on_any_speech_keeps_session_alive_without_semantic_result(
    monkeypatch: Any,
) -> None:
    """Test mode: VAD speech refreshes the session even with no ASR result."""

    audio = _FakeAudio({"has_voice": True, "audio_samples": [0.1]})
    node = _NodeHarness(audio, _FakeWakeup())
    node._refresh_on_any_speech = True
    node._process_speech = lambda *_args: False  # type: ignore[method-assign]
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 111.0)

    node._poll()

    assert node._last_interaction_time == 111.0
    assert node._last_interaction_activity_reason == "vad_speech"
    assert node._interaction_active
    assert not node.published
    assert audio.start_count == 1


def test_active_speech_is_not_cut_off_by_idle_timeout(monkeypatch: Any) -> None:
    audio = _FakeAudio()
    audio.capturing = True
    audio.speech_active = True
    node = _NodeHarness(audio, _FakeWakeup())
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 111.0)

    node._poll()

    assert node._interaction_active
    assert audio.cancel_count == 0
    assert not node.published


def test_absolute_session_deadline_cannot_be_extended_by_activity_or_hold(
    monkeypatch: Any,
) -> None:
    audio = _FakeAudio()
    node = _NodeHarness(audio, _FakeWakeup())
    node._max_interaction_duration = 20.0
    node._last_interaction_time = 120.5
    node._interaction_holds["lease"] = {
        "reason": "downstream_work",
        "deadline_monotonic": 130.0,
    }
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 121.0)

    node._poll()

    assert not node._interaction_active
    assert node.published[-1]["state_reason"] == "interaction_timeout"


def test_zero_max_duration_never_triggers_absolute_timeout() -> None:
    """Test mode: 0 means no absolute cap, so only the idle rule can end."""

    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    node._max_interaction_duration = 0.0
    node._interaction_started_time = 100.0
    node._last_interaction_time = 490.0

    assert node._timeout_interaction_id(500.0) == ""


def test_permanent_session_does_not_timeout_during_long_silence(monkeypatch: Any) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    node._idle_timeout = 0.0
    node._max_interaction_duration = 0.0
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 1000000.0)
    node._poll()
    assert node._interaction_active
    assert not node.published
    assert node._end_interaction("stop_listening")
    assert not node._interaction_active
    assert node.published[-1]["state_reason"] == "stop_listening"


def test_disabling_idle_timeout_preserves_configured_absolute_cap() -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    node._idle_timeout = 0.0
    node._max_interaction_duration = 120.0
    node._interaction_started_time = 100.0
    assert node._timeout_interaction_id(150.0) == ""
    assert node._timeout_interaction_id(221.0) == node._interaction_id


def test_max_duration_non_positive_disables_absolute_cap() -> None:
    resolve = VoiceInteractionNode._resolve_max_interaction_duration

    assert resolve(20.0, 0.0) == 0.0
    assert resolve(20.0, 0) == 0.0
    assert resolve(20.0, -5.0) == 0.0


def test_max_duration_positive_or_invalid_values_keep_a_cap() -> None:
    resolve = VoiceInteractionNode._resolve_max_interaction_duration

    assert resolve(20.0, 120.0) == 120.0
    # A cap below the idle timeout is clamped, so the hard deadline can never
    # fire before the idle deadline would.
    assert resolve(20.0, 5.0) == 20.0
    # A bad value must not silently disable the cap.
    assert resolve(20.0, "abc") == 120.0
    assert resolve(20.0, float("nan")) == 120.0
    assert resolve(20.0, float("inf")) == 120.0


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
    monotonic_clock = [111.0]
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

    monotonic_clock[0] = 113.0
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
    monotonic_clock = [111.0]
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

    monotonic_clock[0] = 115.0
    released = node._run_task("release_interaction_hold", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "reset_idle_timer": True,
    })
    monotonic_clock[0] = 124.0
    node._poll()

    assert released["released"]
    assert released["idle_timer_reset"]
    assert node._interaction_active

    monotonic_clock[0] = 126.0
    node._poll()
    assert not node._interaction_active


def test_duplicate_release_does_not_reset_idle_timer(monkeypatch: Any) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    monotonic_clock = [200.0]
    monkeypatch.setattr(
        node_module.time,
        "monotonic",
        lambda: monotonic_clock[0],
    )
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
    monotonic_clock[0] = 205.0
    duplicate = node._run_task("release_interaction_hold", {
        "interaction_id": "interaction-test",
        "hold_token": "token",
        "reset_idle_timer": True,
    })

    assert first["released"] and first["idle_timer_reset"]
    assert not duplicate["released"]
    assert not duplicate["idle_timer_reset"]
    assert node._last_interaction_time == 200.0


def test_wall_clock_jump_does_not_trigger_idle_timeout(
    monkeypatch: Any,
) -> None:
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    monotonic_clock = [105.0]
    wall_clock = [10_000.0]
    monkeypatch.setattr(
        node_module.time,
        "monotonic",
        lambda: monotonic_clock[0],
    )
    monkeypatch.setattr(node_module.time, "time", lambda: wall_clock[0])

    node._poll()

    assert node._interaction_active
    assert not node.published

    wall_clock[0] = 100_000.0
    monotonic_clock[0] = 111.0
    node._poll()

    assert not node._interaction_active
    assert node.published[-1]["state_reason"] == "interaction_timeout"


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
    monotonic_clock = [100.0]
    monkeypatch.setattr(
        node_module.time,
        "monotonic",
        lambda: monotonic_clock[0],
    )

    provider._next = 0.0
    node._poll()
    interaction_id = node._interaction_id
    provider._next = 0.0
    node._poll()

    assert interaction_id
    assert node.published[0]["event_type"] == "EVT_VOICE_WAKEUP"
    assert node.published[0]["interaction_id"] == interaction_id
    assert node.published[1]["interaction_id"] == interaction_id

    monotonic_clock[0] = 111.0
    node._poll()

    assert node.published[-1]["event_type"] == "EVT_STATE_CHANGED"
    assert node.published[-1]["interaction_id"] == interaction_id
    assert not node._interaction_active

    monotonic_clock[0] = 112.0
    provider._next = 0.0
    node._poll()
    second_interaction_id = node._interaction_id

    assert second_interaction_id
    assert second_interaction_id != interaction_id
    assert node.published[-1]["event_type"] == "EVT_VOICE_WAKEUP"
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
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 100.0)

    node._poll_direct_mock(provider)
    state = node._run_task("get_interaction_state", {})

    assert not event["should_trigger_behavior_tree"]
    assert node.published[-1]["state"] == "attention"
    assert node.published[-1]["previous_state"] == "interaction"
    assert state["state"] == node.published[-1]["state"]


def test_mock_neutral_event_refreshes_when_any_speech_enabled(
    monkeypatch: Any,
) -> None:
    """Test mode: mock interaction events refresh even when NEUTRAL."""

    provider = MockEventProvider({"enabled": True})
    event = provider.build_event("EVT_VOICE_NEUTRAL")
    provider.poll_event = lambda: event  # type: ignore[method-assign]
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    node._providers = {"mock_event": provider, "audio": _FakeAudio()}
    node._refresh_on_any_speech = True
    monkeypatch.setattr(node_module.time, "time", lambda: 111.0)
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 111.0)

    node._poll_direct_mock(provider)

    assert node.published[-1]["event_type"] == "EVT_VOICE_NEUTRAL"
    assert node._last_interaction_time == 111.0
    assert node._last_interaction_activity_reason == "mock_event"
    assert node._interaction_active


def test_mock_event_without_asr_does_not_refresh_when_switch_is_off(
    monkeypatch: Any,
) -> None:
    """An event without ASR text does not extend a session."""

    provider = MockEventProvider({"enabled": True})
    event = provider.build_event("EVT_VOICE_NEUTRAL")
    event["asr_text"] = ""
    provider.poll_event = lambda: event  # type: ignore[method-assign]
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    node._providers = {"mock_event": provider, "audio": _FakeAudio()}
    node._refresh_on_any_speech = False
    monkeypatch.setattr(node_module.time, "time", lambda: 111.0)
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 111.0)

    node._poll_direct_mock(provider)

    assert node._last_interaction_time == 100.0
    assert not node._interaction_active
    assert node.published[-1]["state_reason"] == "interaction_timeout"


def test_mock_neutral_asr_refreshes_without_semantic_acceptance(monkeypatch: Any) -> None:
    provider = MockEventProvider({"enabled": True})
    event = provider.build_event("EVT_VOICE_NEUTRAL")
    event["asr_text"] = "今天随便聊聊"
    provider.poll_event = lambda: event
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    monkeypatch.setattr(node_module.time, "monotonic", lambda: 111.0)
    node._poll_direct_mock(provider)
    assert node._last_interaction_time == 111.0
    assert node._last_interaction_activity_reason == "asr_result"
    assert node._interaction_active


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


def test_capture_timeout_log_includes_backend_phase_and_worker_age(
    caplog: Any,
) -> None:
    release_worker = threading.Event()
    provider = AudioSherpaProvider({})
    provider.available = True

    def ignore_cancel(
        _cancel_event: threading.Event,
    ) -> dict[str, Any]:
        release_worker.wait(1.0)
        return {"has_voice": False, "audio_samples": []}

    provider._stream_vad = ignore_cancel  # type: ignore[method-assign]
    provider.start_capture()

    assert not provider.cancel_capture(timeout=0.01)
    assert "backend=sounddevice" in caplog.text
    assert "phase=worker_starting" in caplog.text
    assert "worker_age_sec=" in caplog.text
    assert "VAD capture worker Python stack" in caplog.text
    assert "ignore_cancel" in caplog.text

    release_worker.set()
    assert provider.cancel_capture(timeout=0.5)


def test_capture_timeout_detaches_worker_so_next_capture_starts() -> None:
    """A worker that outlived its join budget must not refuse the next capture."""
    release_worker = threading.Event()
    provider = AudioSherpaProvider({})
    provider.available = True

    def ignore_cancel(
        _cancel_event: threading.Event,
    ) -> dict[str, Any]:
        release_worker.wait(2.0)
        return {"has_voice": False, "audio_samples": np.array([], np.float32)}

    provider._stream_vad = ignore_cancel  # type: ignore[method-assign]
    assert provider.start_capture() is True
    assert not provider.cancel_capture(timeout=0.01)

    # The stalled worker is detached, not left registered as the active one.
    assert provider._capture_thread is None
    assert len(provider._orphan_workers) == 1
    assert provider.start_capture() is True

    release_worker.set()
    provider.stop()
    assert not provider._orphan_workers


def test_start_capture_reports_refusal_instead_of_faking_success(
    caplog: Any,
) -> None:
    release_worker = threading.Event()
    provider = AudioSherpaProvider({})
    provider.available = True

    def ignore_cancel(
        _cancel_event: threading.Event,
    ) -> dict[str, Any]:
        release_worker.wait(2.0)
        return {"has_voice": False, "audio_samples": np.array([], np.float32)}

    provider._stream_vad = ignore_cancel  # type: ignore[method-assign]
    assert provider.start_capture() is True
    assert provider.start_capture() is False  # already capturing

    # A still-registered worker is a refusal, not a silent no-op: the node
    # must not trace result="started" while the microphone stays closed.
    provider._capturing = False
    assert provider.start_capture() is False
    assert "VAD capture start ignored" in caplog.text

    release_worker.set()
    assert provider.cancel_capture(timeout=0.5)
    provider.stop()


def _wait_for_result(
    provider: AudioSherpaProvider,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = provider.poll_result()
        if result is not None:
            return result
        time.sleep(0.01)
    return None


def test_refused_start_does_not_relabel_the_running_capture() -> None:
    """The 2026-09-20 14:33 case: a wakeup mid-capture, refused start.

    The worker still holding the device belongs to the previous utterance.
    Its result must carry its own ID, so traces, debug audio and downstream
    events are not misattributed to the utterance that never started.
    """
    release_worker = threading.Event()
    provider = AudioSherpaProvider({})
    provider.available = True

    def hold(_cancel_event: threading.Event) -> dict[str, Any]:
        release_worker.wait(2.0)
        return {"has_voice": True, "audio_samples": np.array([], np.float32)}

    provider._stream_vad = hold  # type: ignore[method-assign]
    provider.set_utterance_id("utterance-a")
    assert provider.start_capture() is True

    # The node advances to a new utterance and is refused, exactly as the
    # wakeup path did before it learned to cancel the stale worker.
    provider.set_utterance_id("utterance-b")
    assert provider.start_capture() is False

    release_worker.set()
    result = _wait_for_result(provider)

    assert result is not None
    assert result["utterance_id"] == "utterance-a"
    provider.stop()


def test_dead_orphans_are_pruned_so_the_set_does_not_grow() -> None:
    """Only stop() drains orphans; exited ones must not pile up meanwhile."""
    provider = AudioSherpaProvider({})
    provider.available = True

    finished = threading.Thread(target=lambda: None)
    finished.start()
    finished.join()

    release_worker = threading.Event()
    stalled = threading.Thread(target=release_worker.wait)
    stalled.start()
    try:
        provider._orphan_workers.update({finished, stalled})
        provider._prune_dead_orphans()
        assert provider._orphan_workers == {stalled}
    finally:
        release_worker.set()
        stalled.join(timeout=2.0)


def test_cancelled_capture_skips_debug_audio(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """A cancelled capture must not spend its exit path on debug WAV writes."""
    wait_started = threading.Event()

    class SilentInputStream:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        def start(self) -> None:
            return None

        @property
        def read_available(self) -> int:
            wait_started.set()
            return 0

        def read(self, frames: int) -> tuple[np.ndarray, None]:
            del frames
            raise AssertionError("read() must not block when no frames are ready")

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

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
        SilentInputStream,
    )
    provider = AudioSherpaProvider({
        "sample_rate": 16000,
        "audio_debug": {
            "enabled": True,
            "output_dir": str(tmp_path),
            "save_raw_capture": True,
            "save_vad_segment": True,
            "save_asr_input": True,
        },
    })
    provider._vad = FakeVad()
    provider.available = True

    saved: list[str] = []
    monkeypatch.setattr(
        provider._audio_debug,
        "save",
        lambda _utterance_id, audio_type, *_a, **_k: saved.append(audio_type),
    )

    assert provider.start_capture() is True
    assert wait_started.wait(0.5)
    assert provider.cancel_capture(timeout=0.5)

    assert saved == []
    assert not list(tmp_path.iterdir())


def test_sounddevice_wait_is_cancelled_without_cross_thread_abort(
    monkeypatch: Any,
) -> None:
    wait_started = threading.Event()

    class BlockingInputStream:
        instances: list["BlockingInputStream"] = []

        def __init__(self, **_kwargs: Any) -> None:
            self.abort_count = 0
            self.close_count = 0
            self.close_thread_name = ""
            self.__class__.instances.append(self)

        def start(self) -> None:
            return None

        @property
        def read_available(self) -> int:
            wait_started.set()
            return 0

        def read(self, frames: int) -> tuple[np.ndarray, None]:
            del frames
            raise AssertionError("read() must not block when no frames are ready")

        def abort(self) -> None:
            self.abort_count += 1

        def stop(self) -> None:
            return None

        def close(self) -> None:
            self.close_count += 1
            self.close_thread_name = threading.current_thread().name

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

    assert wait_started.wait(0.5)
    assert provider.cancel_capture(timeout=0.5)
    assert not provider.is_capturing()
    assert provider.poll_result() is None
    stream = BlockingInputStream.instances[0]
    assert stream.abort_count == 0
    assert stream.close_count == 1
    assert stream.close_thread_name == "vad-capture"


def test_sounddevice_reads_only_after_complete_chunk_is_available() -> None:
    class ReadyInputStream:
        read_count = 0

        @property
        def read_available(self) -> int:
            return 320

        def read(self, frames: int) -> tuple[np.ndarray, None]:
            assert frames == 320
            self.read_count += 1
            return np.ones((frames, 1), dtype=np.float32), None

    provider = AudioSherpaProvider({})
    provider._capturing = True
    stream = ReadyInputStream()

    chunk = provider._read_sounddevice_chunk(stream, threading.Event())

    assert chunk is not None
    assert chunk.shape == (320, 1)
    assert stream.read_count == 1


def test_blocked_arecord_read_is_terminated_during_cancel(
    monkeypatch: Any,
) -> None:
    import subprocess
    import os
    from marsdog_voice_interaction.providers import audio_sherpa as audio_module

    read_started = threading.Event()
    read_released = threading.Event()

    class BlockingProcess:
        instances: list["BlockingProcess"] = []

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            read_fd, self.write_fd = os.pipe()
            self.stdout = os.fdopen(read_fd, "rb", buffering=0)
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
            if not self.stdout.closed:
                self.stdout.close()
                os.close(self.write_fd)
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
    original_read = audio_module.read_pipe_chunk

    def observed_read(*args, **kwargs):
        read_started.set()
        return original_read(*args, **kwargs)

    monkeypatch.setattr(audio_module, "read_pipe_chunk", observed_read)
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
