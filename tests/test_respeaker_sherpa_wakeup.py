import time
from pathlib import Path

import numpy as np
import pytest

from marsdog_voice_interaction.adapters.wakeup.respeaker_serial_reader import ReSpeakerSerialReader
from marsdog_voice_interaction.messages.audio_event import normalize_audio_event
from marsdog_voice_interaction.providers.audio_capture import BufferedAudioCapture
from marsdog_voice_interaction.providers.wakeup_respeaker_sherpa import WakeupReSpeakerSherpaProvider


def test_default_host_wake_words_include_workbook_custom_names():
    root = Path(__file__).parents[1]
    lines = (root / "config/wake_keywords.txt").read_text(encoding="utf-8").splitlines()
    keywords = {line.rsplit("@", 1)[1] for line in lines if line.strip()}
    assert keywords == {"旺财旺财", "小火小火", "小狗", "跟屁虫"}
    assert keywords.isdisjoint({"这儿", "这里这里", "到这来"})


def vad(doa, age_ms=20, valid=True, source="auto"):
    return {"type": "vad", "state": "update", "doa": doa, "doa_valid": valid,
            "doa_age_ms": age_ms, "doa_source": source}


def firmware_wake(received):
    return {"type": "wake", "keyword": "旺财旺财", "id": 1, "score": 0.08, "confidence": 8,
            "threshold": 0.02, "min_score": 0.02, "doa": 95, "doa_valid": True, "doa_age_ms": 80,
            "audio_age_ms": 180, "doa_source": "auto", "_received_monotonic": received}


def test_reader_keeps_the_latest_valid_bearing_and_prefers_auto_estimates():
    reader = ReSpeakerSerialReader({})
    reader._note_doa(vad(70, age_ms=100), 10.0)
    reader._note_doa(vad(90, source="beam"), 10.5)
    reader._note_doa(vad(-1, valid=False), 10.8)
    reader._note_doa(vad(True), 10.8)
    reader._note_doa(vad(400), 10.8)
    assert reader.recent_doa(9.0, 11.0)[1:] == (70.0, "auto")
    assert reader.recent_doa(10.2, 11.0)[1:] == (90.0, "beam")
    assert reader.recent_doa(10.6, 11.0) is None


def test_sherpa_wake_takes_the_bearing_heard_during_the_word(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    provider = WakeupReSpeakerSherpaProvider({})
    provider.reader._note_doa(vad(73, age_ms=0), 99.7)
    event = provider._keyword_event("旺财旺财", 100.0)
    assert event["wake_angle"] == 73 and event["wake_angle_valid"] is True
    assert event["wake_provider"] == "respeaker_sherpa" and event["received_monotonic"] == 100.0
    event["event_type"] = "EVT_VOICE_WAKEUP"
    public = normalize_audio_event(event)
    assert public["wake_word"] == "旺财旺财" and public["wake_angle_valid"] is True

    clock[0] = 103.5
    stale = provider._keyword_event("旺财旺财", 103.5)
    assert stale["wake_angle"] == -1 and stale["wake_angle_valid"] is False


def test_the_same_word_heard_by_firmware_and_sherpa_wakes_once(monkeypatch):
    provider = WakeupReSpeakerSherpaProvider({})
    now = time.monotonic()
    serial = [firmware_wake(now)]
    monkeypatch.setattr(provider.reader, "get_message", lambda: serial.pop(0) if serial else None)
    provider._events.append(provider._keyword_event("旺财旺财", now + 0.3))
    first = provider.poll_event()
    assert first["wake_provider"] == "respeaker_serial"
    assert provider.poll_event() is None

    provider._events.append(provider._keyword_event("旺财旺财", now + 3.0))
    assert provider.poll_event()["wake_provider"] == "respeaker_sherpa"


@pytest.mark.parametrize("word", ["这儿", "这里这里", "到这来"])
@pytest.mark.parametrize("kind", ["wake", "keyword"])
def test_call_here_keywords_use_a_separate_event(monkeypatch, word, kind):
    provider = WakeupReSpeakerSherpaProvider({})
    now = time.monotonic()
    provider.reader._note_doa(vad(73, age_ms=0), now)
    host_event = provider._keyword_event(word, now)
    assert host_event["event_type"] == "EVT_VOICE_CALL_HERE"
    assert host_event["wake_word"] == word and host_event["wake_angle"] == 73
    serial = [dict(firmware_wake(now), type=kind, keyword=word)]
    monkeypatch.setattr(provider.reader, "get_message", lambda: serial.pop(0) if serial and kind == "wake" else None)
    monkeypatch.setattr(provider.reader, "get_keyword_message", lambda: serial.pop(0) if serial else None)
    provider._events.append(host_event)
    first = provider.poll_event()
    assert first["event_type"] == "EVT_VOICE_CALL_HERE"
    assert first["wake_provider"] == ("respeaker_keyword" if kind == "keyword" else "respeaker_serial")
    # Only the node knows if the session is awake; it owns keyword debounce.
    assert provider.poll_event()["wake_provider"] == "respeaker_sherpa"
    assert provider.poll_event() is None


@pytest.mark.parametrize("words", [("这儿", "旺财旺财"), ("旺财旺财", "这儿")])
def test_call_here_and_real_wakes_have_independent_deduplication(monkeypatch, words):
    provider = WakeupReSpeakerSherpaProvider({})
    monkeypatch.setattr(provider.reader, "get_message", lambda: None)
    now = time.monotonic()
    for index, word in enumerate(words):
        provider._events.append(provider._keyword_event(word, now + index * 0.1))
    assert [provider.poll_event()["wake_word"] for _ in words] == list(words)
    provider._events.append(provider._keyword_event("这里这里", now + 0.2))
    assert provider.poll_event()["event_type"] == "EVT_VOICE_CALL_HERE"
    provider._events.append(provider._keyword_event("到这来", now + 2.0))
    assert provider.poll_event()["event_type"] == "EVT_VOICE_CALL_HERE"


@pytest.mark.parametrize("updates", [
    {"doa_valid": False}, {"doa": -1}, {"doa": False},
    {"doa_valid": "true"}, {"doa_age_ms": -1}, {"audio_age_ms": None},
])
def test_call_here_cannot_recover_invalid_direction_from_recent_doa(monkeypatch, updates):
    provider = WakeupReSpeakerSherpaProvider({})
    now = time.monotonic()
    provider.reader._note_doa(vad(73, age_ms=0), now)
    serial = [dict(firmware_wake(now), type="keyword", keyword="这儿", **updates)]
    monkeypatch.setattr(provider.reader, "get_keyword_message", lambda: serial.pop(0) if serial else None)
    event = provider.poll_event()
    assert event["event_type"] == "EVT_VOICE_CALL_HERE"
    assert event["wake_angle_valid"] is False and event["wake_angle"] == -1
    # Rejected notifications do not consume a provider debounce window.
    serial.append(dict(firmware_wake(now + 0.1), type="keyword", keyword="这儿"))
    assert provider.poll_event()["wake_angle_valid"] is True


def stale_firmware_wake(monkeypatch, provider, received):
    serial = [dict(firmware_wake(received), doa_age_ms=1100)]
    monkeypatch.setattr(provider.reader, "get_message", lambda: serial.pop(0) if serial else None)


def test_a_firmware_wake_with_a_stale_bearing_takes_the_one_heard_during_the_word(monkeypatch):
    provider = WakeupReSpeakerSherpaProvider({})
    now = time.monotonic()
    provider.reader._note_doa(vad(70, age_ms=0), now - 0.3)
    stale_firmware_wake(monkeypatch, provider, now)
    event = provider.poll_event()
    assert event["wake_provider"] == "respeaker_serial"
    assert event["wake_angle"] == 70 and event["wake_angle_valid"] is True and event["wake_angle_source"] == "auto"


def test_a_firmware_wake_keeps_no_bearing_when_none_was_heard_during_the_word(monkeypatch):
    provider = WakeupReSpeakerSherpaProvider({})
    now = time.monotonic()
    provider.reader._note_doa(vad(70, age_ms=0), now - 3.0)
    stale_firmware_wake(monkeypatch, provider, now)
    event = provider.poll_event()
    assert event["wake_angle"] == -1 and event["wake_angle_valid"] is False


def test_microphone_listener_sees_standby_audio_without_delivery():
    heard = []
    capture = BufferedAudioCapture(16000, None, None, 1.0, 1.0, history_sec=1.0,
                                   listener=lambda at, samples: heard.append(samples.size))
    capture._push(np.zeros(320, dtype=np.float32))
    assert heard == [320] and not capture.delivery_active


@pytest.mark.parametrize("kind", ["wake", "keyword"])
def test_recognition_bearing_cache_includes_the_audio_snapshot_age(kind):
    reader = ReSpeakerSerialReader({})
    reader._note_doa(dict(firmware_wake(100.0), type=kind,
                          doa_age_ms=500, audio_age_ms=700), 100.0)
    assert reader.recent_doa(98.0, 100.0)[0] == pytest.approx(98.8)
    assert reader.recent_doa(99.4, 100.0) is None


@pytest.mark.parametrize("audio_age", [None, -1, True, float("inf"), 10 ** 1000])
def test_recognition_bearing_cache_rejects_unknown_audio_snapshot_age(audio_age):
    reader = ReSpeakerSerialReader({})
    reader._note_doa(dict(firmware_wake(100.0), audio_age_ms=audio_age), 100.0)
    assert reader.recent_doa(98.0, 100.0) is None


def test_latest_cached_bearing_uses_measurement_time_instead_of_arrival_order():
    reader = ReSpeakerSerialReader({})
    reader._note_doa(vad(73, age_ms=0), 99.8)
    reader._note_doa(dict(firmware_wake(100.0), doa_age_ms=500, audio_age_ms=700), 100.0)
    assert reader.recent_doa(98.0, 100.0)[1] == 73


@pytest.mark.parametrize("word", ["这儿", "这里这里", "到这来"])
@pytest.mark.parametrize("kind", ["wake", "keyword"])
def test_call_here_recovers_stale_snapshot_using_fresh_bearing(monkeypatch, word, kind):
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    provider = WakeupReSpeakerSherpaProvider({})
    provider.reader._ready.set()
    provider.reader._note_doa(vad(73, age_ms=0), 99.8)
    raw = dict(firmware_wake(100.0), type=kind, keyword=word, doa_age_ms=1100)
    provider.reader._note_doa(raw, 100.0)
    queue = provider.reader._messages if kind == "wake" else provider.reader._keyword_messages
    queue.append(raw)
    event = provider.poll_event()
    assert event["event_type"] == "EVT_VOICE_CALL_HERE"
    assert event["wake_angle_valid"] is True and event["wake_angle"] == 73
    assert event["wake_angle_age_ms"] == pytest.approx(200.0)


@pytest.mark.parametrize("word", ["旺财旺财", "这里这里"])
def test_recovery_does_not_rejuvenate_the_same_expired_snapshot(monkeypatch, word):
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    provider = WakeupReSpeakerSherpaProvider({})
    provider.reader._ready.set()
    raw = dict(firmware_wake(100.0), keyword=word, doa_age_ms=500, audio_age_ms=700)
    provider.reader._note_doa(raw, 100.0)
    provider.reader._messages.append(raw)
    event = provider.poll_event()
    assert event["wake_angle_valid"] is False and event["wake_angle"] == -1
    assert event["wake_angle_age_ms"] == pytest.approx(1200.0)


@pytest.mark.parametrize("word", ["旺财旺财", "这里这里"])
def test_recovery_rejects_bearings_inside_history_window_but_over_age_limit(monkeypatch, word):
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    provider = WakeupReSpeakerSherpaProvider({})
    provider.reader._ready.set()
    provider.reader._note_doa(vad(73, age_ms=0), 99.1)
    provider.reader._messages.append(dict(firmware_wake(100.0), keyword=word, doa_age_ms=1100))
    assert provider.poll_event()["wake_angle_valid"] is False


def test_fresh_beam_is_used_when_auto_bearing_has_expired(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    provider = WakeupReSpeakerSherpaProvider({})
    provider.reader._note_doa(vad(73, age_ms=0), 99.1)
    provider.reader._note_doa(vad(95, age_ms=0, source="beam"), 99.8)
    event = provider._keyword_event("旺财旺财", 100.0)
    assert event["wake_angle"] == 95 and event["wake_angle_source"] == "beam"


@pytest.mark.parametrize("now, valid", [(100.3, True), (100.5, False)])
def test_sherpa_decode_backlog_counts_toward_bearing_age(monkeypatch, now, valid):
    monkeypatch.setattr(time, "monotonic", lambda: now)
    provider = WakeupReSpeakerSherpaProvider({})
    provider.reader._note_doa(vad(73, age_ms=0), 99.8)
    event = provider._keyword_event("旺财旺财", 100.0)
    assert event["wake_angle_valid"] is valid
    if valid:
        assert event["wake_angle_age_ms"] == pytest.approx(500.0)
