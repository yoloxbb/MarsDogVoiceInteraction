"""Call direction attribution and canonical wire behavior, without hardware."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from marsdog_voice_interaction.adapters.wakeup.respeaker_serial_reader import ReSpeakerSerialReader
from marsdog_voice_interaction.providers.audio_capture import BufferedAudioCapture
from marsdog_voice_interaction.providers.audio_sherpa import AudioSherpaProvider


def vad(angle=73, **updates):
    return {"type": "vad", "state": "update", "doa": angle, "doa_valid": True,
            "doa_age_ms": 20, "doa_source": "auto", **updates}


def ready_reader():
    reader = ReSpeakerSerialReader({})
    reader._ready.set()
    return reader


def test_call_history_ignores_status_and_end_and_accepts_zero():
    reader = ready_reader()
    reader._note_doa(vad(0), 100.0)
    reader._note_doa(vad(180, type="status"), 100.1)
    reader._note_doa(vad(220, state="end"), 100.2)
    query = reader.recent_call_doa
    assert query(99.0, 101.0) == (99.98, 0.0, "auto")


@pytest.mark.parametrize("state", ["start", "update"])
def test_active_vad_boundary_records_its_valid_direction(state):
    reader = ready_reader()
    reader._note_doa(vad(0, state=state, active=True), 100.0)
    assert reader.recent_call_doa(99.0, 101.0) == (99.98, 0.0, "auto")


@pytest.mark.parametrize("updates", [
    {"state": "end"}, {"state": "unknown"}, {"state": None},
    {"active": False}, {"active": "true"}, {"type": "status"},
])
def test_non_direction_activity_does_not_replace_either_bearing_cache(updates):
    reader = ready_reader()
    reader._note_doa(vad(0), 100.0)
    reader._note_doa(vad(180, **updates), 100.1)
    assert reader.recent_doa(99.0, 101.0) == (99.98, 0.0, "auto")
    assert reader.recent_call_doa(99.0, 101.0) == (99.98, 0.0, "auto")


def test_keyword_direction_is_measured_at_its_audio_frame_without_rejuvenation():
    reader = ready_reader()
    reader._note_doa(vad(0, type="keyword", audio_age_ms=180), 100.0)
    assert reader.recent_call_doa(99.0, 101.0) == (99.8, 0.0, "auto")
    reader._note_doa(vad(180, doa_age_ms=400), 100.1)
    assert reader.recent_call_doa(99.0, 101.0) == (99.8, 0.0, "auto")


@pytest.mark.parametrize("updates", [
    {"doa_valid": False}, {"doa_valid": "true"}, {"doa": True}, {"doa": -1},
    {"doa": 360}, {"doa": float("nan")}, {"doa": 10 ** 1000},
    {"doa_age_ms": -1}, {"doa_age_ms": True}, {"doa_age_ms": float("inf")},
])
def test_invalid_update_does_not_replace_or_rejuvenate_call_bearing(updates):
    reader = ready_reader()
    reader._note_doa(vad(), 100.0)
    reader._note_doa(vad(**updates), 100.5)
    query = reader.recent_call_doa
    assert query(99.0, 101.0) == (99.98, 73.0, "auto")
    assert query(100.0, 101.0) is None


def test_call_history_ignores_out_of_order_measurements_and_reconnect_clears_it():
    reader = ready_reader()
    reader._note_doa(vad(73), 100.0)
    reader._note_doa(vad(150, doa_age_ms=500), 100.1)
    query = reader.recent_call_doa
    assert query(99.0, 101.0)[1] == 73
    reader._reset_session()
    reader._ready.set()
    assert query(99.0, 101.0) is None


def test_buffer_read_retains_capture_time_instead_of_consumer_time(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    capture = BufferedAudioCapture(16000, None, None, 6, 1)
    capture._push(np.ones(320))
    clock[0] = 102.0
    assert callable(getattr(capture, "read_chunk", None))
    captured_at, samples = capture.read_chunk()
    assert captured_at == 100.0 and samples.size == 320


@pytest.mark.parametrize("start, size, stamps, expected_start, expected_end", [
    (160, 480, (100.02, 100.04, 100.06), 100.01, 100.04),
    (320, 320, (100.02, 100.52, 100.54), 100.50, 100.52),
])
def test_buffered_speech_timestamps_follow_vad_boundaries_not_preroll(
        monkeypatch, start, size, stamps, expected_start, expected_end):
    monkeypatch.setattr(time, "monotonic", lambda: 101.0)
    class Detector:
        def reset(self):
            self.count, self.done = 0, False
        def accept_waveform(self, samples):
            self.count += 1
            self.is_speech_detected = True
        def empty(self):
            return self.count < 3 or self.done
        @property
        def front(self):
            return SimpleNamespace(start=start, samples=np.ones(size))
        def pop(self):
            self.done = True
    provider = AudioSherpaProvider({"sample_rate": 16000, "pre_roll_sec": .3})
    provider._vad = Detector()
    capture = BufferedAudioCapture(16000, None, None, 6, 1)
    for stamp in stamps:
        capture._queue.put((stamp, np.ones(320)))
    provider._buffered_capture = capture
    result = provider._stream_vad_buffered(threading.Event())
    assert result.get("speech_started_monotonic") == pytest.approx(expected_start)
    assert result.get("speech_ended_monotonic") == pytest.approx(expected_end)


@pytest.fixture
def wire_node(monkeypatch):
    from test_session_recovery import _NodeHarness, _FakeAudio, _FakeWakeup
    from marsdog_voice_interaction.nodes.voice_interaction_node import VoiceInteractionNode
    monkeypatch.setattr(time, "monotonic", lambda: 101.0)
    node = _NodeHarness(_FakeAudio(), _FakeWakeup())
    reader = ready_reader()
    node._providers["wakeup"] = SimpleNamespace(reader=reader, max_angle_age_ms=600)
    node._config = {}
    wire = []
    node._audio_pub = SimpleNamespace(publish=lambda msg: wire.append(json.loads(msg.data)))
    node._publish = lambda event: VoiceInteractionNode._publish(node, event)
    return node, reader, wire


@pytest.mark.parametrize("legacy", ["EVT_VOICE_CALL_HERE", "EVT_VOICE_CALL_NAME", "EVT_VOICE_COMMAND_CALL_NAME"])
def test_legacy_calls_have_one_wire_event_even_without_a_direction(wire_node, legacy):
    node, reader, wire = wire_node
    assert node._publish({"event_type": legacy, "utterance_id": "one", "wake_word": "这儿" if legacy.endswith("HERE") else "小狗"})
    assert wire[0]["event_type"] == "EVT_VOICE_CALL"
    assert wire[0]["call_kind"] == ("here" if legacy.endswith("HERE") else "name")
    assert wire[0]["intent_source"]
    assert wire[0]["doa"] == -1 and wire[0]["doa_valid"] is False
    assert wire[0]["should_trigger_behavior_tree"] is wire[0]["is_executable"] is False


def test_firmware_call_uses_latest_measured_bearing_from_reader(wire_node):
    node, reader, wire = wire_node
    reader._note_doa(vad(0, doa_age_ms=0), 100.9)
    assert not node._handle_wakeup({
        "event_type": "EVT_VOICE_CALL_HERE", "wake_word": "这里这里",
        "received_monotonic": 101.0, "wake_angle": 180, "wake_angle_valid": False,
    }, node._providers["audio"])
    assert len(wire) == 1
    assert wire[0]["event_type"] == "EVT_VOICE_CALL" and wire[0]["call_kind"] == "here"
    assert wire[0]["doa"] == 0 and wire[0]["doa_valid"] is True
    assert wire[0]["doa_age_ms"] == pytest.approx(100)
    assert node._providers["audio"].cancel_count == 0


def test_call_publish_trace_exposes_direction_without_a_sound_id(wire_node):
    node, reader, wire = wire_node
    traces = []
    node._trace = lambda record, **values: traces.append((record, values))
    reader._note_doa(vad(0, state="start", active=True), 100.9)
    node._handle_wakeup({"event_type": "EVT_VOICE_CALL", "wake_word": "这儿",
                         "received_monotonic": 101.0}, node._providers["audio"])
    published = next(values for record, values in traces if record == "event_publish")
    assert published["doa"] == wire[0]["doa"] == 0
    assert published["doa_valid"] is wire[0]["doa_valid"] is True
    assert published["doa_age_ms"] == wire[0]["doa_age_ms"]


@pytest.mark.parametrize("audio_age, expected_valid", [(180, True), (700, False)])
def test_serial_keyword_binds_direction_before_queueing_without_vad_update(
        wire_node, audio_age, expected_valid):
    from marsdog_voice_interaction.providers.wakeup_respeaker_serial import WakeupReSpeakerSerialProvider
    node, reader, wire = wire_node
    messages = [
        {"type": "pong"},
        {"type": "keyword", "keyword": "这里这里", "id": 101,
         "score": .08, "threshold": .02, "min_score": .02,
         "doa": 0, "doa_valid": True, "doa_age_ms": 20,
         "audio_age_ms": audio_age, "doa_source": "auto"},
        vad(-1, doa_valid=False),
        vad(180, state="end", active=False),
    ]
    raw = b"".join(json.dumps(message).encode() + b"\n" for message in messages)
    class Port:
        in_waiting = len(raw)
        def write(self, _data):
            pass
        def read(self, _size):
            reader._stop.set()
            return raw
    reader._read_connection(Port())
    provider = WakeupReSpeakerSerialProvider({})
    provider.reader = reader
    assert not node._handle_wakeup(provider.poll_event(), node._providers["audio"])
    assert len(wire) == 1
    assert wire[0]["doa_valid"] is expected_valid
    assert wire[0]["doa"] == (0 if expected_valid else -1)
    assert wire[0]["doa_age_ms"] == pytest.approx(audio_age + 20)
    # Raw activity boundaries remain available without overwriting the bearing.
    assert reader.get_vad_message()["doa_valid"] is False
    assert reader.get_vad_message()["state"] == "end"


def test_calls_remain_session_only_and_idle_does_not_consume_debounce(wire_node):
    node, reader, wire = wire_node
    node._interaction_active = False
    event = {"wake_word": "这里这里", "received_monotonic": 101.0}
    node._handle_wakeup(event, None)
    assert wire == []
    node._interaction_active = True
    node._handle_wakeup(event, None)
    assert len(wire) == 1 and wire[0]["doa_valid"] is False


def test_catalog_and_model_calls_share_the_canonical_event():
    from marsdog_voice_interaction.core.command_lexicon import CommandLexicon
    from marsdog_voice_interaction.messages.intent_event_router import route_classification_events
    root = Path(__file__).parents[1]
    catalog = CommandLexicon(root / "config/command_catalog.yaml")
    nickname = catalog.match("小狗").to_event(asr_text="小狗", language="zh")
    model = route_classification_events("CALL", "COME", "DO", asr_text="小狗过来", source="rkllm")
    assert nickname["event_type"] == "EVT_VOICE_CALL"
    assert nickname["call_kind"] == "name"
    assert [e["event_type"] for e in model][:2] == ["EVT_VOICE_CALL", "EVT_VOICE_COMMAND_COME"]
    assert model[0]["call_kind"] == "name"
    assert model[1]["should_trigger_behavior_tree"] is True


@pytest.mark.parametrize("delay, valid", [(0.1, True), (1.0, False)])
def test_asr_call_keeps_original_speaker_direction_when_later_speech_arrives(monkeypatch, delay, valid):
    from test_command_lexicon import _DirectRouteHarness
    from marsdog_voice_interaction.nodes.voice_interaction_node import VoiceInteractionNode
    clock = [100.1]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    node = _DirectRouteHarness("小狗")
    reader = ready_reader()
    reader._note_doa(vad(73, doa_age_ms=0), 100.05)
    from marsdog_voice_interaction.providers.wakeup_respeaker_sherpa import WakeupReSpeakerSherpaProvider
    wakeup = WakeupReSpeakerSherpaProvider({})
    wakeup.reader = reader
    node._providers["wakeup"] = wakeup
    node._interaction_active = True
    node._interaction_lock = threading.RLock()
    node._config = {}
    wire = []
    node._audio_pub = SimpleNamespace(publish=lambda msg: wire.append(json.loads(msg.data)))
    node._publish = lambda event: VoiceInteractionNode._publish(node, event)
    def transcribe(_audio):
        clock[0] += delay
        reader._note_doa(vad(150, doa_age_ms=0), clock[0])
        return {"asr_text": "小狗", "language": "zh"}
    node._providers["asr"].transcribe = transcribe
    node._process_speech({"audio_samples": [0.1] * 1600, "sample_rate": 16000,
                          "speech_started_monotonic": 100.0, "speech_ended_monotonic": 100.1}, "old-speech")
    call = next(e for e in wire if e["event_type"] == "EVT_VOICE_CALL")
    assert call["doa_valid"] is valid
    assert call["doa"] == (73 if valid else -1)
    assert call["doa_age_ms"] == pytest.approx((0.05 + delay) * 1000)


def test_unknown_audio_timing_does_not_attach_a_global_direction(wire_node):
    node, reader, wire = wire_node
    reader._note_doa(vad(150, doa_age_ms=0), 101.0)
    node._publish({"event_type": "EVT_VOICE_CALL_NAME", "utterance_id": "unknown-time"})
    assert wire[0]["event_type"] == "EVT_VOICE_CALL"
    assert wire[0]["doa_valid"] is False


def test_model_here_phrase_keeps_here_subtype():
    from marsdog_voice_interaction.messages.intent_event_router import route_classification_events
    event = route_classification_events("CALL", "NONE", "NONE", asr_text="这里这里", source="rkllm")[0]
    assert event["call_kind"] == "here"


def test_queued_audio_binds_direction_before_asr_and_missing_stays_missing(monkeypatch, wire_node):
    from marsdog_voice_interaction.nodes.voice_interaction_node import VoiceInteractionNode
    from marsdog_voice_interaction.core.call_event import bind_utterance_direction
    node, reader, wire = wire_node
    submitted = []
    node._speech_processing = SimpleNamespace(submit=lambda job, **_: submitted.append(job) or True)
    audio = {"audio_samples": np.ones(1600), "sample_rate": 16000,
             "speech_started_monotonic": 100.8, "speech_ended_monotonic": 101.0}
    VoiceInteractionNode._queue_speech(node, audio, "queued-one")
    reader._note_doa(vad(150, doa_age_ms=0), 100.9)
    bound = bind_utterance_direction(submitted[0]["audio_data"], node._providers["wakeup"])
    assert bound["_call_direction"]["doa_valid"] is False


def test_call_snapshot_is_invalidated_by_reader_reconnect(wire_node):
    from marsdog_voice_interaction.core.call_event import direction_snapshot
    node, reader, wire = wire_node
    reader._note_doa(vad(73, doa_age_ms=0), 100.9)
    frozen = direction_snapshot(node._providers["wakeup"], 100.8, 101.0)
    reader._reset_session()
    reader._ready.set()
    reader._note_doa(vad(150, doa_age_ms=0), 100.95)
    node._publish({"event_type": "EVT_VOICE_CALL", "call_kind": "name", **frozen})
    assert wire[0]["doa_valid"] is False and wire[0]["doa"] == -1


def test_firmware_and_asr_same_call_are_deduplicated_but_next_utterance_survives(wire_node):
    from marsdog_voice_interaction.core.call_event import direction_snapshot
    node, reader, wire = wire_node
    node._handle_wakeup({"wake_word": "这里这里", "received_monotonic": 101.0}, None)
    speech = direction_snapshot(node._providers["wakeup"], 100.8, 101.1)
    assert not node._publish({"event_type": "EVT_VOICE_CALL", "call_kind": "here",
                              "intent_source": "command_lexicon", "utterance_id": "same-audio", **speech})
    assert node._publish({"event_type": "EVT_VOICE_CALL", "call_kind": "here",
                         "intent_source": "command_lexicon", "utterance_id": "next-audio",
                         **direction_snapshot(node._providers["wakeup"], 102.0, 102.2)})
    assert len(wire) == 2


def test_publication_of_mock_name_call_preserves_kind(wire_node):
    node, reader, wire = wire_node
    node._handle_wakeup({"event_type": "EVT_VOICE_CALL_NAME", "asr_text": "小狗",
                         "intent_source": "mock_event",
                         "received_monotonic": 101.0}, None)
    assert wire[0]["call_kind"] == "name"
    assert wire[0]["intent_source"] == "mock_event"


def test_firmware_and_host_providers_emit_canonical_call_candidates(monkeypatch):
    from marsdog_voice_interaction.providers.wakeup_respeaker_sherpa import WakeupReSpeakerSherpaProvider
    monkeypatch.setattr(time, "monotonic", lambda: 101.0)
    provider = WakeupReSpeakerSherpaProvider({})
    serial = provider._parse_event({
        "type": "keyword", "keyword": "这里这里", "id": 102, "score": .08,
        "threshold": .02, "min_score": .02, "doa": 95, "doa_valid": True,
        "doa_age_ms": 80, "audio_age_ms": 180, "_received_monotonic": 101.0,
    })
    host = provider._keyword_event("这里这里", 101.0)
    assert serial["event_type"] == host["event_type"] == "EVT_VOICE_CALL"
    assert serial["call_kind"] == host["call_kind"] == "here"


def test_mock_builder_supports_canonical_calls():
    from marsdog_voice_interaction.providers.mock_event import MockEventProvider
    event = MockEventProvider({}).build_event("EVT_VOICE_CALL")
    assert event["event_type"] == "EVT_VOICE_CALL" and event["call_kind"] == "name"


def test_call_session_cannot_end_between_validation_and_publication(wire_node):
    node, reader, wire = wire_node
    lock_acquired = []
    def probe_lock():
        acquired = node._interaction_lock.acquire(blocking=False)
        lock_acquired.append(acquired)
        if acquired:
            node._interaction_lock.release()
    def publish(_msg):
        worker = threading.Thread(target=probe_lock)
        worker.start()
        worker.join(timeout=1)
        assert not worker.is_alive()
    node._audio_pub = SimpleNamespace(publish=publish)
    node._publish({"event_type": "EVT_VOICE_CALL", "call_kind": "name"})
    assert lock_acquired == [False]


def test_call_age_never_exceeds_600ms_even_when_wake_age_is_relaxed(wire_node):
    from marsdog_voice_interaction.core.call_event import direction_snapshot
    node, reader, wire = wire_node
    node._providers["wakeup"].max_angle_age_ms = 2000
    reader._note_doa(vad(73, doa_age_ms=0), 100.0)
    snapshot = direction_snapshot(node._providers["wakeup"], 99.9, 100.1)
    node._publish({"event_type": "EVT_VOICE_CALL", "call_kind": "name", **snapshot})
    assert wire[0]["doa_age_ms"] == 1000
    assert wire[0]["doa_valid"] is False and wire[0]["doa"] == -1


def test_one_firmware_anchor_cannot_consume_two_adjacent_asr_utterances():
    from marsdog_voice_interaction.core.call_event import CallDeduplicator
    dedupe = CallDeduplicator()
    firmware = {"call_kind": "here", "interaction_id": "session", "utterance_id": "firmware",
                "intent_source": "respeaker_keyword", "_call_anchor": 100.25}
    speech = {"call_kind": "here", "interaction_id": "session", "utterance_id": "A",
              "intent_source": "command_lexicon", "_call_window_start": 100.0, "_call_window_end": 100.2}
    assert dedupe.accept(firmware)
    assert not dedupe.accept(speech)
    assert not dedupe.accept(speech)  # A duplicate retry also remains suppressed.
    assert dedupe.accept({**speech, "utterance_id": "B", "_call_window_start": 100.3, "_call_window_end": 100.5})


def test_firmware_audio_age_aligns_deduplication_with_original_speech(wire_node):
    from marsdog_voice_interaction.core.call_event import direction_snapshot
    node, reader, wire = wire_node
    node._handle_wakeup({"wake_word": "这里这里", "received_monotonic": 101.0,
                         "wake_audio_age_ms": 500, "wake_provider": "respeaker_keyword"}, None)
    snapshot = direction_snapshot(node._providers["wakeup"], 100.45, 100.55)
    assert not node._publish({"event_type": "EVT_VOICE_CALL", "call_kind": "here",
                              "intent_source": "rkllm", "utterance_id": "original-speech", **snapshot})
    assert len(wire) == 1


def test_firmware_call_from_audio_before_current_session_is_ignored(wire_node):
    node, reader, wire = wire_node
    node._handle_wakeup({"wake_word": "这里这里", "received_monotonic": 101.0,
                         "wake_audio_age_ms": 1200, "wake_provider": "respeaker_keyword"}, None)
    assert wire == []
