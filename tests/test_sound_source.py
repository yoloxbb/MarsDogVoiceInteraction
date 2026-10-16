from collections import deque
import json
from types import SimpleNamespace

import pytest

from marsdog_voice_interaction.adapters.wakeup.respeaker_serial_reader import ReSpeakerSerialReader
from marsdog_voice_interaction.core.sound_source import SoundSourceEventRouter
from marsdog_voice_interaction.messages.audio_event import normalize_audio_event
from marsdog_voice_interaction.messages.voice_event_types import EVT_VOICE_SOUND_SOURCE_DETECTED


def raw_update(**changes):
    return {
        "type": "vad", "state": "update", "active": True,
        "doa": 95, "doa_valid": True, "doa_age_ms": 30, "doa_source": "auto",
        "source": "audio_activity", "speech": 0, "raw_rms": 0.018, "noise_floor": 0.0006,
        "_received_monotonic": 10.0, "_connection_id": 1, **changes,
    }


def make_router(*messages):
    pending = deque(messages)
    reader = SimpleNamespace(
        ready=True, connection_id=1,
        get_vad_message=lambda: pending.popleft() if pending else None,
    )
    router = SoundSourceEventRouter(
        reader, {}, clock=lambda: 10.0, wall_clock=lambda: 1700000010.0,
    )
    return router, reader


def test_valid_update_immediately_emits_without_start_end_audio_or_model():
    router, _ = make_router(raw_update())
    events = router.poll()
    assert len(events) == 1
    event = normalize_audio_event(events[0])
    assert event["event_type"] == EVT_VOICE_SOUND_SOURCE_DETECTED
    assert event["doa"] == 95 and event["doa_valid"] is True
    assert event["doa_age_ms"] == 30 and event["sound_trigger_stamp"] == 1700000010.0
    assert event["intent_source"] == "respeaker_vad"
    assert event["interaction_id"] == event["wake_id"] == event["utterance_id"] == ""
    assert event["should_trigger_behavior_tree"] is True and event["is_executable"] is False
    assert {s["key"]: s["value"] for s in event["slots"]}["speech"] == "0"
    assert router.poll() == []


@pytest.mark.parametrize("changes", [
    {"doa_valid": False}, {"doa_valid": "false"}, {"doa_valid": "true"},
    {"doa_valid": 1}, {"doa_valid": None},
    {"state": "start"}, {"state": "end"}, {"type": "wake"},
    {"doa": -1}, {"doa": 360}, {"doa": True}, {"doa": float("nan")},
    {"doa": 10 ** 1000}, {"doa_age_ms": -1}, {"doa_age_ms": 601},
    {"doa_age_ms": True}, {"doa_age_ms": float("inf")}, {"doa_age_ms": 10 ** 1000},
    {"_received_monotonic": 9.3}, {"_received_monotonic": float("nan")},
])
def test_json_that_does_not_meet_the_trigger_is_ignored(changes):
    router, _ = make_router(raw_update(**changes))
    assert router.poll() == []


def test_each_valid_update_produces_one_event_and_invalid_updates_produce_none():
    router, _ = make_router(
        raw_update(doa_valid=False), raw_update(doa=95),
        raw_update(doa_valid=False), raw_update(doa=120),
    )
    events = router.poll()
    assert [event["doa"] for event in events] == [95.0, 120.0]
    assert events[0]["sound_id"] != events[1]["sound_id"]
    assert router.poll() == []


def test_host_queue_delay_counts_toward_direction_age():
    router, _ = make_router(raw_update(doa_age_ms=200, _received_monotonic=9.5))
    assert router.poll() == []
    router, _ = make_router(raw_update(doa_age_ms=200, _received_monotonic=9.9))
    assert router.poll()[0]["doa_age_ms"] == pytest.approx(300.0)


def test_disconnected_or_old_connection_updates_are_not_published():
    router, reader = make_router(raw_update(_connection_id=0))
    assert router.poll() == []
    router, reader = make_router(raw_update())
    reader.ready = False
    assert router.poll() == []
    router.stop()
    reader.ready = True
    assert router.poll() == []


def test_node_can_enable_json_events_with_only_the_cdc_reader():
    from marsdog_voice_interaction.nodes.voice_interaction_node import VoiceInteractionNode
    _, reader = make_router()
    node = SimpleNamespace(
        _config={"sound_source": {"enabled": True}},
        _providers={"wakeup": SimpleNamespace(reader=reader)},
        _sound_source=None, _sound_source_status={"enabled": False, "ready": False},
    )
    VoiceInteractionNode._init_sound_source(node)
    assert node._sound_source_status == {"enabled": True, "ready": True}
    assert isinstance(node._sound_source, SoundSourceEventRouter)
    node._sound_source.stop()


def test_vad_queue_is_separate_from_wakes_and_cleared_on_disconnect():
    reader = ReSpeakerSerialReader({})
    messages = [{"type": "pong"}] + [raw_update()] * 200
    messages.append({"type": "wake", "keyword": "旺财旺财"})
    raw = b"".join(json.dumps(m).encode() + b"\n" for m in messages)
    class Port:
        in_waiting = len(raw)
        def write(self, data):
            assert data == b'{"cmd":"ping"}\n'
        def read(self, _size):
            reader._stop.set()
            return raw
    reader._read_connection(Port())
    assert reader.get_message()["type"] == "wake"
    assert reader.get_message() is None and len(reader._vad_messages) == 128
    assert reader.get_vad_message()["type"] == "vad"
    reader._reset_session()
    assert reader.get_vad_message() is None and reader.get_message() is None


@pytest.mark.parametrize("flag", [False, "false", 1, None])
def test_audio_contract_requires_explicit_valid_direction(flag):
    event = normalize_audio_event({"doa": 95, "doa_valid": flag, "doa_age_ms": 30})
    assert event["doa_valid"] is False and event["doa"] == -1


def test_audio_contract_discards_overflowing_bearing_numbers():
    event = normalize_audio_event({"doa": 10 ** 1000, "doa_valid": True, "doa_age_ms": 0})
    assert event["doa_valid"] is False
