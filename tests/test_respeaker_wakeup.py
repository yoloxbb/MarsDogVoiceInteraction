from collections import deque
import json
import threading
import time
from types import SimpleNamespace

import pytest

from marsdog_voice_interaction.adapters.wakeup import respeaker_serial_reader as transport
from marsdog_voice_interaction.messages.audio_event import normalize_audio_event
from marsdog_voice_interaction.providers.wakeup_respeaker_serial import WakeupReSpeakerSerialProvider


def wake(**updates):
    return {
        "type": "wake", "keyword": "旺财旺财", "id": 1,
        "score": 0.08, "confidence": 8, "threshold": 0.02, "min_score": 0.02,
        "doa": 95, "doa_valid": True, "doa_age_ms": 80,
        "audio_age_ms": 180, "doa_source": "auto", "activity": 0,
        "_received_monotonic": time.monotonic(), **updates,
    }


def line(message):
    return json.dumps(message, ensure_ascii=False).encode() + b"\n"


def eventually(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.005)
    pytest.fail("condition did not become true")


def test_json_lines_handle_every_utf8_split_and_interleaved_responses():
    expected = [{"type": "vad"}, wake(), {"type": "pong"}]
    payload = b"".join(line(m) for m in expected)
    for split in range(len(payload) + 1):
        decoder = transport.JsonLineDecoder()
        assert decoder.feed(payload[:split]) + decoder.feed(payload[split:]) == expected
    decoder = transport.JsonLineDecoder()
    result = []
    for byte in payload:
        result.extend(decoder.feed(bytes([byte])))
    assert result == expected


def test_corrupt_and_oversized_line_cannot_execute_its_json_suffix():
    decoder = transport.JsonLineDecoder(max_bytes=32)
    assert decoder.feed(b"x" * 33) == []
    assert decoder.feed(b'{"type":"wake"}\n') == []
    assert decoder.feed(b'[]\nnull\n{bad}\n\xff\n{"type":"pong"}\r\n') == [{"type": "pong"}]
    assert not decoder.pending


@pytest.mark.parametrize("kind", ["vad", "keyword", "pong", "status", "commands"])
def test_only_wake_can_open_a_session(kind):
    assert WakeupReSpeakerSerialProvider({})._parse_event(wake(type=kind)) is None


@pytest.mark.parametrize("word", ["这儿", "这里这里", "到这来"])
@pytest.mark.parametrize("valid", [False, True])
def test_selected_firmware_keywords_are_notifications_with_optional_direction(word, valid):
    provider = WakeupReSpeakerSerialProvider({})
    event = provider._parse_event(wake(type="keyword", keyword=word, id=101, doa_valid=valid))
    assert event["event_type"] == "EVT_VOICE_CALL"
    assert event["wake_provider"] == "respeaker_keyword"
    assert event["wake_word"] == word and event["wake_keyword_id"] == 101
    assert event["wake_angle_valid"] is valid
    assert event["wake_angle"] == (95 if valid else -1)


@pytest.mark.parametrize("word", ["打开灯光", "旺财旺财", "", None, [], {}])
def test_unrelated_keywords_never_become_wakes_even_with_the_same_id(word):
    assert WakeupReSpeakerSerialProvider({})._parse_event(wake(type="keyword", keyword=word, id=1)) is None


def test_keyword_queue_is_bounded_independent_and_cleared_on_reconnect():
    reader = transport.ReSpeakerSerialReader({})
    reader._ready.set()
    for index in range(200):
        reader._keyword_messages.append({"id": index})
    reader._messages.append({"type": "wake"})
    assert len(reader._keyword_messages) == 128
    assert reader.get_message() == {"type": "wake"}
    assert reader.get_keyword_message() == {"id": 72}
    reader._reset_session()
    assert reader.get_keyword_message() is None and not reader._keyword_messages


def test_mapping_preserves_raw_score_and_does_not_require_vad():
    result = WakeupReSpeakerSerialProvider({})._parse_event(wake())
    result["event_type"] = "EVT_VOICE_WAKEUP"
    public = normalize_audio_event(result)
    assert public["wake_confidence"] == public["wake_score_raw"] == 0.08
    assert public["wake_word"] == "旺财旺财"
    assert public["wake_keyword_id"] == 1
    assert public["wake_angle"] == 95
    assert public["wake_angle_valid"] is True
    assert public["wake_threshold"] == public["wake_min_score"] == 0.02
    assert public["wake_audio_age_ms"] == 180
    assert public["wake_provider"] == "respeaker_serial"
    assert "wake_duration_sec" not in result


@pytest.mark.parametrize("updates", [
    {"doa_valid": False}, {"doa_valid": "false"}, {"doa": -1},
    {"doa": 360}, {"doa": float("nan")}, {"doa_age_ms": -1},
    {"doa_age_ms": 500, "audio_age_ms": 180}, {"audio_age_ms": None},
    {"_received_monotonic": 0.0},
])
def test_invalid_or_stale_direction_is_marked_without_rejecting_wake(updates):
    event = WakeupReSpeakerSerialProvider({})._parse_event(wake(**updates))
    assert event is not None
    assert event["wake_angle_valid"] is False
    assert event["wake_angle"] == -1
    assert normalize_audio_event(event)["wake_angle_valid"] is False


@pytest.mark.parametrize("updates", [
    {"score": float("nan")}, {"score": 8}, {"score": True}, {"score": "0.08"},
    {"threshold": None}, {"id": True}, {"id": 0}, {"keyword": ""},
])
def test_malformed_recognition_does_not_wake(updates):
    assert WakeupReSpeakerSerialProvider({})._parse_event(wake(**updates)) is None


def test_port_selection_excludes_bootloader_and_refuses_ambiguity(monkeypatch):
    def port(path, pid=0x4013, serial_number="one"):
        return SimpleNamespace(device=path, vid=0x303A, pid=pid, serial_number=serial_number)
    devices = [port("/dev/boot", 0x1001), port("/dev/ttyACM2")]
    monkeypatch.setattr(transport.list_ports, "comports", lambda: devices)
    reader = transport.ReSpeakerSerialReader({})
    assert reader.resolve_port() == "/dev/ttyACM2"
    devices.append(port("/dev/ttyACM3", serial_number="two"))
    with pytest.raises(RuntimeError, match="Expected one"):
        reader.resolve_port()
    assert transport.ReSpeakerSerialReader({"serial_number": "two"}).resolve_port() == "/dev/ttyACM3"
    assert transport.ReSpeakerSerialReader({"port": "/dev/chosen"}).resolve_port() == "/dev/chosen"


class FakeSerial:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.incoming = deque()
        self.writes = []
        self.disconnected = False
        self.closed = False
        self.reply = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def open(self):
        assert self.dtr is True and self.rts is False
        assert self.options["exclusive"] is True

    def reset_input_buffer(self):
        self.incoming.clear()

    @property
    def in_waiting(self):
        if self.disconnected:
            raise OSError("unplugged")
        return 1

    def read(self, _size):
        if self.incoming:
            return self.incoming.popleft()
        time.sleep(0.002)
        return b""

    def write(self, data):
        self.writes.append(data)
        assert data == b'{"cmd":"ping"}\n'
        if self.reply:
            self.incoming.append(line({"type": "vad"}) + line({"type": "pong"}))
        return len(data)


def test_handshake_event_delivery_heartbeat_and_reenumeration(monkeypatch):
    instances = []
    device = [SimpleNamespace(device="/dev/ttyACM0", vid=0x303A, pid=0x4013, serial_number="x")]
    monkeypatch.setattr(transport.list_ports, "comports", lambda: device)
    def serial_factory(**kwargs):
        port = FakeSerial(**kwargs)
        instances.append(port)
        return port
    monkeypatch.setattr(transport.serial, "Serial", serial_factory)
    provider = WakeupReSpeakerSerialProvider({
        "settle_sec": 0.01, "timeout": 0.01, "handshake_timeout_sec": 0.05,
        "heartbeat_sec": 0.04, "reconnect_interval_sec": 0.02,
    })
    provider.start()
    try:
        eventually(provider.is_available)
        first = instances[0]
        first.incoming.append(line({"type": "keyword"}) + line(wake()))
        event = eventually(provider.poll_event)
        assert event["wake_word"] == "旺财旺财"
        assert event["wake_angle_valid"]
        first.incoming.append(line(wake(type="keyword", keyword="这儿", id=101)))
        keyword = eventually(provider.poll_event)
        assert keyword["event_type"] == "EVT_VOICE_CALL"
        assert keyword["wake_provider"] == "respeaker_keyword"
        assert keyword["wake_word"] == "这儿" and keyword["wake_angle_valid"]
        eventually(lambda: len(first.writes) >= 2)
        first.incoming.append(line(wake(keyword="old-session-wake")))
        eventually(lambda: bool(provider.reader._messages))
        device[0].device = "/dev/ttyACM7"
        first.disconnected = True
        eventually(lambda: len(instances) == 2 and provider.is_available())
        assert first.closed
        assert instances[1].port == "/dev/ttyACM7"
        assert provider.poll_event() is None  # no replay across reconnection
        instances[1].reply = False
        eventually(lambda: len(instances) >= 3 and provider.is_available())
        assert instances[1].closed  # a missing pong triggers reconnect
    finally:
        provider.stop()
    assert not provider.reader._thread.is_alive()
    assert not provider.is_available()


def test_poll_does_not_wait_for_discovery_or_handshake(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    def enumerate_ports():
        entered.set()
        release.wait(1)
        return []
    monkeypatch.setattr(transport.list_ports, "comports", enumerate_ports)
    provider = WakeupReSpeakerSerialProvider({})
    provider.start()
    try:
        assert entered.wait(1)
        assert provider.poll_event() is None
    finally:
        release.set()
        provider.stop()
