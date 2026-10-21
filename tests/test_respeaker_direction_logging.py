"""Direction diagnostics stay visible at the normal INFO log level."""
import json
import logging
import time

from marsdog_voice_interaction.core.call_event import direction_snapshot
from marsdog_voice_interaction.adapters.wakeup.respeaker_serial_reader import ReSpeakerSerialReader


def records(caplog, prefix):
    return [json.loads(record.getMessage().split(" ", 1)[1])
            for record in caplog.records if record.getMessage().startswith(prefix + " ")]


def test_serial_logs_raw_direction_and_cache_decision_at_info(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    reader = ReSpeakerSerialReader({})
    start = {"type": "vad", "state": "start", "active": True,
             "doa": 73, "doa_valid": True, "doa_age_ms": 300, "doa_source": "auto"}
    keyword = {"type": "keyword", "keyword": "这儿", "id": 101,
               "doa": 0, "doa_valid": True, "doa_age_ms": 20,
               "audio_age_ms": 180, "doa_source": "auto"}
    messages = [start, {"type": "pong"}, start, keyword,
                {**start, "state": "end", "active": False},
                {**start, "state": "update", "doa_valid": False},
                {**start, "state": "update", "doa_age_ms": 500},
                {**keyword, "audio_age_ms": None}]
    raw = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
    class Port:
        in_waiting = len(raw)
        def write(self, _data):
            pass
        def read(self, _size):
            reader._stop.set()
            return raw
    reader._read_connection(Port())
    logged = records(caplog, "RESPEAKER_DOA")
    assert [entry["cache_result"] for entry in logged] == [
        "not_ready", "stored", "stored", "invalid_state", "invalid_doa",
        "out_of_order", "invalid_audio_age",
    ]
    assert logged[2]["raw"] == keyword
    assert logged[2]["received_monotonic"] == 100.0
    assert logged[2]["measured_monotonic"] == 99.8
    assert logged[2]["total_age_ms"] == 200
    assert logged[2]["connection_id"] == reader.connection_id
    assert reader.recent_call_doa(99.0, 100.0) == (99.8, 0.0, "auto")
    assert reader.get_keyword_message()["doa"] == 0
    assert reader.get_vad_message()["state"] == "start"
    assert reader.get_vad_message()["state"] == "end"


def test_call_query_logs_match_and_latest_sample_outside_window(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(time, "monotonic", lambda: 100.1)
    reader = ReSpeakerSerialReader({})
    reader._ready.set()
    reader._note_doa({"type": "vad", "state": "update", "doa": 283,
                      "doa_valid": True, "doa_age_ms": 20, "doa_source": "auto"}, 100.0)
    assert reader.recent_call_doa(99.0, 100.0) == (99.98, 283.0, "auto")
    assert reader.recent_call_doa(100.0, 100.1) is None
    reader._reset_session()
    assert reader.recent_call_doa(99.0, 100.0) is None
    logged = records(caplog, "CALL_DIRECTION")
    assert [entry["result"] for entry in logged] == ["matched", "no_sample", "not_ready"]
    assert logged[0]["selected"][1] == 283
    assert logged[1]["window_start"] == 100.0
    assert logged[1]["window_end"] == 100.1
    assert logged[1]["latest"][1] == 283
    assert logged[1]["sample_count"] == 1
    assert logged[2]["sample_count"] == 0


def test_missing_speech_times_are_logged_without_looking_up_another_bearing(caplog):
    caplog.set_level(logging.INFO)
    snapshot = direction_snapshot(None, None, None)
    assert snapshot["doa_valid"] is False
    assert records(caplog, "CALL_DIRECTION")[0]["result"] == "invalid_window"
