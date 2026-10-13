from pathlib import Path
import threading
import time

from marsdog_voice_interaction.core.command_lexicon import CommandLexicon
from marsdog_voice_interaction.core.utterance_command_tracker import UtteranceCommandTracker


CATALOG_PATH = Path(__file__).parents[1] / "config" / "command_catalog.yaml"


def test_published_keyword_owns_the_utterance_business_result():
    tracker = UtteranceCommandTracker()
    tracker.begin("utterance-1")
    candidate = {"event_type": "EVT_VOICE_COMMAND_SIT", "action": "SIT"}
    assert tracker.record_kws_candidate(candidate)
    assert tracker.mark_kws_published("EVT_VOICE_COMMAND_SIT")
    assert not tracker.mark_kws_published("EVT_VOICE_COMMAND_SIT")
    assert tracker.published_event_types == ("EVT_VOICE_COMMAND_SIT",)
    assert not tracker.asr_business_allowed


def test_new_utterance_accepts_the_same_keyword_again():
    tracker = UtteranceCommandTracker()
    tracker.begin("utterance-1")
    tracker.record_kws_candidate({"event_type": "EVT_VOICE_COMMAND_SIT"})
    tracker.mark_kws_published("EVT_VOICE_COMMAND_SIT")
    tracker.begin("utterance-2")
    assert tracker.asr_business_allowed
    assert tracker.published_event_types == ()
    assert tracker.record_kws_candidate({"event_type": "EVT_VOICE_COMMAND_SIT"})
    assert tracker.mark_kws_published("EVT_VOICE_COMMAND_SIT")


def test_unrecorded_or_finished_keyword_cannot_be_marked_as_published():
    tracker = UtteranceCommandTracker()
    tracker.begin("utterance-1")
    assert not tracker.mark_kws_published("EVT_VOICE_COMMAND_SIT")
    tracker.record_kws_candidate({"event_type": "EVT_VOICE_COMMAND_SIT"})
    tracker.finish()
    assert not tracker.mark_kws_published("EVT_VOICE_COMMAND_SIT")
    assert tracker.asr_business_allowed


def test_asr_completion_releases_the_utterance_timeout_guard():
    tracker = UtteranceCommandTracker()
    tracker.begin("utterance-1")
    assert tracker.awaiting_asr
    tracker.mark_asr_complete()
    assert not tracker.awaiting_asr
    tracker.begin("utterance-2")
    assert tracker.awaiting_asr
    tracker.finish()
    assert not tracker.awaiting_asr


def test_immediate_event_uses_catalog_identity_and_pending_speaker():
    command = CommandLexicon(CATALOG_PATH).get_command("SIT")
    assert command is not None
    event = command.to_kws_event(
        {"action": "SIT", "intent_confidence": 0.9},
        utterance_id="utterance-1",
        latency_ms=170.0,
    )
    assert event["event_type"] == "EVT_VOICE_COMMAND_SIT"
    assert event["command_id"] == "CMD_SIT"
    assert event["utterance_id"] == "utterance-1"
    assert event["asr_text"] == ""
    assert event["intent_source"] == "kws"
    assert event["speaker_id"] == "unknown"
    assert event["speaker_confidence"] == 0.0
    assert event["speaker_status"] == "pending"
    assert event["is_executable"] and event["should_trigger_behavior_tree"]
    assert event["latency_ms"] == 170.0
    assert event["intent_confidence"] == 0.9
    slots = {item["key"]: item["value"] for item in event["slots"]}
    assert slots["recognition_strategy"] == "kws_immediate"
    assert slots["kws_keyword"] == "SIT"


def test_recognition_runs_without_blocking_keyword_delivery():
    from marsdog_voice_interaction.core.speech_processing_queue import SpeechProcessingQueue

    ready = threading.Event()
    processing = SpeechProcessingQueue(ready.wait, max_buffer_sec=1.0)
    try:
        assert processing.submit(None, duration_sec=0.5)
        tracker = UtteranceCommandTracker()
        tracker.begin("utterance-1")
        assert tracker.record_kws_candidate({"event_type": "EVT_VOICE_COMMAND_SIT"})
        assert tracker.mark_kws_published("EVT_VOICE_COMMAND_SIT")
        assert processing.poll() is None
        ready.set()
        result = wait_for_processing(processing)
        assert result.result is True
        assert result.discard_reason == ""
    finally:
        ready.set()
        processing.close()


def test_recognition_queue_bounds_pending_audio():
    from marsdog_voice_interaction.core.speech_processing_queue import SpeechProcessingQueue

    ready = threading.Event()
    processing = SpeechProcessingQueue(ready.wait, max_buffer_sec=0.5)
    try:
        assert processing.submit(None, duration_sec=2.0)
        assert processing.submit(10.0, duration_sec=0.3)
        assert not processing.submit(20.0, duration_sec=0.3)
        ready.set()
        assert wait_for_processing(processing).payload is None
        assert wait_for_processing(processing).payload == 10.0
        assert processing.poll() is None
    finally:
        ready.set()
        processing.close()


def test_expired_queued_audio_is_discarded_before_processing():
    from marsdog_voice_interaction.core.speech_processing_queue import SpeechProcessingQueue

    ready = threading.Event()
    processing = SpeechProcessingQueue(ready.wait, max_buffer_sec=0.05)
    try:
        assert processing.submit(None, duration_sec=0.01)
        assert processing.submit(0.0, duration_sec=0.01)
        time.sleep(0.08)
        ready.set()
        results = [wait_for_processing(processing), wait_for_processing(processing)]
        completed = next(item for item in results if item.payload is None)
        expired = next(item for item in results if item.payload == 0.0)
        assert completed.result is True
        assert expired.discard_reason == "expired"
    finally:
        ready.set()
        processing.close()


def test_cancelling_recognition_discards_inflight_and_queued_results():
    from marsdog_voice_interaction.core.speech_processing_queue import SpeechProcessingQueue

    ready = threading.Event()
    processing = SpeechProcessingQueue(ready.wait, max_buffer_sec=1.0)
    try:
        assert processing.submit(None, duration_sec=0.5)
        assert processing.submit(10.0, duration_sec=0.5)
        assert processing.pending_payloads == (None, 10.0)
        processing.clear()
        assert processing.pending_payloads == ()
        ready.set()
        result = wait_for_processing(processing)
        assert result.discard_reason == "cancelled"
        assert processing.poll() is None
    finally:
        ready.set()
        processing.close()


def test_poll_discards_expired_audio_while_the_processor_is_busy():
    from marsdog_voice_interaction.core.speech_processing_queue import SpeechProcessingQueue

    ready = threading.Event()
    processing = SpeechProcessingQueue(ready.wait, max_buffer_sec=0.1)
    try:
        assert processing.submit(None, duration_sec=1.0)
        assert processing.submit(10.0, duration_sec=0.08)
        time.sleep(0.12)
        expired = processing.poll()
        assert expired is not None
        assert expired.discard_reason == "expired"
        assert processing.pending_payloads == (None,)
    finally:
        ready.set()
        processing.close()


def test_expired_audio_releases_capacity_while_recognition_is_running():
    from marsdog_voice_interaction.core.speech_processing_queue import SpeechProcessingQueue

    ready = threading.Event()
    processing = SpeechProcessingQueue(ready.wait, max_buffer_sec=0.1)
    try:
        assert processing.submit(None, duration_sec=1.0)
        assert processing.submit(10.0, duration_sec=0.08)
        time.sleep(0.12)
        assert processing.submit(20.0, duration_sec=0.08)
        discarded = wait_for_processing(processing)
        assert discarded.payload == 10.0
        assert discarded.discard_reason == "expired"
        ready.set()
        assert wait_for_processing(processing).payload is None
        assert wait_for_processing(processing).payload == 20.0
    finally:
        ready.set()
        processing.close()


def wait_for_processing(processing):
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        result = processing.poll()
        if result is not None:
            return result
        time.sleep(0.005)
    raise AssertionError("识别任务没有在限定时间内返回")
