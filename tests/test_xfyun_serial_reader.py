from __future__ import annotations

import gzip
import json
import random
import string
from types import SimpleNamespace

import pytest
import serial

from marsdog_voice_interaction.adapters.wakeup.xfyun_serial_reader import (
    XFYunSerialReader,
    _encode_frame,
)
from marsdog_voice_interaction.providers.wakeup_xfyun_serial import WakeupXFYunSerialProvider


def _reader(protocol: str = "auto") -> tuple[XFYunSerialReader, list[bytes]]:
    reader = XFYunSerialReader(protocol=protocol)
    writes: list[bytes] = []

    def write(packet: bytes) -> int:
        writes.append(packet)
        return len(packet)

    reader._ser = SimpleNamespace(write=write)
    return reader, writes


def _wake(word: str = "小薇小薇") -> dict:
    return {
        "type": "aiui_event",
        "content": {
            "eventType": 4,
            "info": json.dumps({"ivw": {"keyword": word, "score": 927.0, "angle": 123.0}}),
            "result": word,
        },
    }


def _event_frame(message: dict, message_id: int = 23) -> bytes:
    return _encode_frame(0x04, message_id, gzip.compress(json.dumps(message).encode()))


@pytest.mark.parametrize("split", range(1, 12))
def test_captured_handshake_is_acknowledged_with_matching_id(split: int) -> None:
    # Actual bytes captured from WCH serial on 2026-10-09; expected ACK is
    # independently calculated from the documented frame format.
    packet = bytes.fromhex("a5 01 01 04 00 05 12 a5 00 00 00 99")
    reader, writes = _reader()
    reader._process_data(packet[:split])
    assert writes == []
    reader._process_data(packet[split:])
    assert writes == [bytes.fromhex("a5 01 ff 04 00 05 12 a5 00 00 00 9b")]
    assert reader.get_message() is None


def test_gzip_wake_event_reaches_provider_with_word_angle_and_score() -> None:
    reader, writes = _reader()
    reader._process_data(_event_frame(_wake()))
    raw = reader.get_message()
    assert raw is not None
    event = WakeupXFYunSerialProvider({})._parse_event(raw)
    assert event is not None
    assert event["wake_word"] == "小薇小薇"
    assert event["wake_angle"] == 123.0
    assert event["wake_confidence"] == pytest.approx(0.927)
    assert len(writes) == 1


def test_bad_checksum_is_not_acknowledged_and_next_frame_recovers() -> None:
    reader, writes = _reader("aiui")
    bad = bytearray(_event_frame(_wake(), 1))
    bad[-1] ^= 1
    reader._process_data(b"noise" + bad + _event_frame(_wake(), 2))
    assert len(writes) == 1
    assert reader.get_message() is not None
    assert reader.get_message() is None


def test_uncompressed_firmware_event_reaches_provider_through_valid_frame() -> None:
    reader, writes = _reader("aiui")
    payload = json.dumps(_wake(), ensure_ascii=False).encode()
    reader._process_data(_encode_frame(4, 5196, payload))
    raw = reader.get_message()
    assert raw is not None
    event = WakeupXFYunSerialProvider({})._parse_event(raw)
    assert event is not None
    assert event["wake_word"] == "小薇小薇"
    assert event["wake_angle"] == 123.0
    assert len(writes) == 1


def test_compressed_and_uncompressed_frames_can_share_one_serial_stream() -> None:
    reader, writes = _reader("aiui")
    reader._process_data(_encode_frame(4, 1, json.dumps(_wake("小薇小薇")).encode())
                         + _event_frame(_wake("旺财旺财"), 2))
    assert reader.get_message()["content"]["result"] == "小薇小薇"
    assert reader.get_message()["content"]["result"] == "旺财旺财"
    assert len(writes) == 2


def test_duplicate_events_are_acknowledged_without_duplicate_delivery() -> None:
    reader, writes = _reader("aiui")
    packet = _event_frame(_wake())
    reader._process_data(packet + packet)
    assert len(writes) == 2
    assert writes[0] == writes[1]
    assert reader.get_message() is not None
    assert reader.get_message() is None


def test_reused_id_with_different_payload_does_not_drop_a_new_wake() -> None:
    reader, _ = _reader("aiui")
    reader._process_data(_event_frame(_wake("小薇小薇")) + _event_frame(_wake("旺财旺财")))
    assert reader.get_message()["content"]["result"] == "小薇小薇"
    assert reader.get_message()["content"]["result"] == "旺财旺财"


@pytest.mark.parametrize("payload", [b"not gzip", gzip.compress(b"invalid json"),
                                      gzip.compress(b"x" * (1024 * 1024 + 1))])
def test_malformed_or_oversized_payload_is_dropped_and_next_event_recovers(payload: bytes) -> None:
    reader, writes = _reader("aiui")
    reader._process_data(_encode_frame(4, 1, payload) + _event_frame(_wake(), 2))
    assert len(writes) == 2
    assert reader.get_message() is not None
    assert reader.get_message() is None


def test_ack_frame_is_never_acknowledged() -> None:
    reader, writes = _reader("aiui")
    reader._process_data(_encode_frame(0xFF, 7, b"\xa5\x00\x00\x00"))
    assert writes == []


def test_non_event_json_is_acknowledged_but_not_delivered() -> None:
    reader, writes = _reader("aiui")
    reader._process_data(_event_frame({"type": "wifi_status", "content": {"connected": False}}))
    assert len(writes) == 1
    assert reader.get_message() is None


def test_binary_packet_larger_than_legacy_buffer_survives_fragmentation() -> None:
    message = _wake()
    message["padding"] = "".join(random.Random(17).choices(string.ascii_letters, k=16000))
    packet = _event_frame(message)
    assert len(packet) > 4096
    reader, writes = _reader()
    for offset in range(0, len(packet), 113):
        reader._process_data(packet[offset:offset + 113])
    assert reader.get_message()["padding"] == message["padding"]
    assert len(writes) == 1


def test_legacy_json_handles_utf8_fragments_and_braces_inside_strings() -> None:
    message = _wake('小薇 } " { 小薇')
    reader, writes = _reader()
    for byte in b"legacy log prefix " + json.dumps(message, ensure_ascii=False).encode():
        reader._process_data(bytes([byte]))
    assert reader.get_message()["content"] == message["content"]
    assert writes == []


def test_complete_malformed_legacy_json_does_not_block_next_event() -> None:
    reader, _ = _reader("json")
    reader._process_data(b"{invalid}" + json.dumps(_wake()).encode())
    assert reader.get_message() is not None


def test_binary_json_lookalike_with_bad_checksum_cannot_bypass_validation() -> None:
    reader, writes = _reader()
    bad = bytearray(_encode_frame(4, 1, json.dumps(_wake()).encode()))
    bad[-1] ^= 1
    reader._process_data(bytes(bad))
    assert writes == []
    assert reader.get_message() is None


def test_short_ack_write_fails_instead_of_delivering_an_unconfirmed_event() -> None:
    reader, _ = _reader("aiui")
    reader._ser.write = lambda _packet: 0
    with pytest.raises(serial.SerialException, match="Incomplete"):
        reader._process_data(_event_frame(_wake()))
    assert reader.get_message() is None


def test_reset_clears_partial_frames_and_duplicate_cache() -> None:
    reader, writes = _reader("aiui")
    packet = _event_frame(_wake())
    reader._process_data(packet)
    assert reader.get_message() is not None
    reader._process_data(packet[:4])
    reader.reset()
    reader._process_data(packet)
    assert reader.get_message() is not None
    assert len(writes) == 2
