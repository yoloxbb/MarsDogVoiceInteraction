from pathlib import Path

import numpy as np
import pytest

from marsdog_voice_interaction.core.utterance_command_tracker import UtteranceCommandTracker
from marsdog_voice_interaction.providers.kws_sherpa import KWSSherpaProvider
from test_command_lexicon import _KwsRouteHarness


class _Stream:
    def __init__(self):
        self.ready = False
        self.index = -1

    def accept_waveform(self, sample_rate, samples):
        self.ready = True


class _Spotter:
    def __init__(self, results):
        self.results = results

    def create_stream(self):
        return _Stream()

    def is_ready(self, stream):
        return stream.ready

    def decode_stream(self, stream):
        stream.ready = False
        stream.index += 1
        result = self.get_result(stream)
        if isinstance(result, Exception):
            raise result

    def get_result(self, stream):
        return self.results[stream.index] if stream.index < len(self.results) else ""

    def reset_stream(self, stream):
        pass


def _provider(commands=("COME",), negations=()):
    provider = KWSSherpaProvider({
        "publish_mode": "immediate",
        "negation_guard_enabled": True,
        "negation_guard_window_sec": 0.25,
    })
    provider._spotter = _Spotter(commands)
    provider._negation_spotter = _Spotter(negations)
    provider.available = True
    provider.start_utterance()
    return provider


def _feed(provider, chunks=1):
    events = []
    for _ in range(chunks):
        provider.accept_waveform(np.zeros(1600, dtype=np.float32), 16000)
        while (event := provider.poll_event()) is not None:
            events.append(event)
    return events


@pytest.mark.parametrize("negations", [("NEGATION",), ("", "NEGATION"), ("", "", "NEGATION")])
def test_negation_before_or_after_command_hit_prevents_publication(negations):
    provider = _provider(commands=("COME", "SIT"), negations=negations)
    events = _feed(provider, 8)
    assert events == [{"kind": "kws_guard", "reason": "negation_keyword", "keyword": "NEGATION"}]


def test_command_waits_for_following_audio_and_is_published_once():
    provider = _provider(commands=("COME",) * 6)
    assert _feed(provider, 3) == []
    events = _feed(provider)
    assert [e["event_type"] for e in events] == ["EVT_VOICE_COMMAND_COME"]
    assert events[0]["kws_guard_window_ms"] == 250.0
    assert _feed(provider, 4) == []


def test_polling_without_audio_does_not_bypass_guard_and_vad_end_drops_pending():
    provider = _provider()
    assert _feed(provider) == []
    for _ in range(10):
        assert provider.poll_event() is None
    provider.finish_utterance()
    assert provider.poll_event() is None
    assert _feed(provider, 4) == []


def test_unpolled_command_is_removed_when_a_veto_arrives():
    provider = _provider(negations=("", "", "", "", "NEGATION"))
    for _ in range(5):
        provider.accept_waveform(np.zeros(1600, dtype=np.float32), 16000)
    assert provider.poll_event() == {"kind": "kws_guard", "reason": "negation_keyword", "keyword": "NEGATION"}
    assert provider.poll_event() is None


def test_decoder_error_latches_veto_instead_of_releasing_pending_command():
    provider = _provider(negations=("", RuntimeError("decoder failed")))
    assert _feed(provider, 6) == [{"kind": "kws_guard", "reason": "kws_decode_error", "keyword": ""}]


def test_new_utterance_clears_negation_and_discards_old_events():
    provider = _provider(negations=("NEGATION",))
    provider.accept_waveform(np.zeros(1600, dtype=np.float32), 16000)
    provider.finish_utterance()
    provider._negation_spotter = _Spotter(())
    provider.start_utterance()
    events = _feed(provider, 4)
    assert [e.get("event_type") for e in events] == ["EVT_VOICE_COMMAND_COME"]


@pytest.mark.parametrize("window", [0, -1, float("nan"), float("inf")])
def test_guard_rejects_invalid_confirmation_window(window):
    with pytest.raises(ValueError):
        KWSSherpaProvider({"publish_mode": "immediate", "negation_guard_enabled": True,
                           "negation_guard_window_sec": window})


def test_missing_guard_keyword_file_makes_provider_unavailable(tmp_path):
    provider = KWSSherpaProvider({"publish_mode": "immediate", "negation_guard_enabled": True,
                                  "negation_keywords_file": str(tmp_path / "missing.txt")})
    provider.start()
    assert not provider.is_available()


def _blocked_node(text, command="COME", reason="negation_keyword"):
    node = _KwsRouteHarness(command_key=command, asr_text=text)
    node._kws_arbitration["publish_mode"] = "immediate"
    node._providers["kws"]._events.appendleft({"kind": "kws_guard", "reason": reason})
    node._poll_kws_events()
    assert node.published == []  # No internal veto marker on ROS.
    return node


@pytest.mark.parametrize("text,command", [
    ("不要过来", "COME"), ("那这里好像有不要过来", "COME"), ("不要坐下", "SIT"),
    ("过来", "COME"), ("坐下", "SIT"), ("", "COME"),  # ASR loses the prefix/is empty.
    ("不要安静", "QUIET"), ("不要别动", "HOLD_POSITION"),
])
def test_veto_also_blocks_asr_and_model_business_fallback(text, command):
    node = _blocked_node(text, command)
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    assert not any(e.get("should_trigger_behavior_tree") for e in node.published)
    assert not node.intent_called
    assert any(r == "utterance_complete" and f.get("result") == "blocked_kws_guard"
               for r, f in node.traces)
    if text:
        assert any(e.get("asr_text") == text and e["event_type"] == "speech" for e in node.published)


@pytest.mark.parametrize("text,command", [
    ("别动", "HOLD_POSITION"), ("不准动", "HOLD_POSITION"), ("不许动", "HOLD_POSITION"),
    ("别叫", "QUIET"), ("不许叫", "QUIET"),
])
def test_exact_stop_and_quiet_commands_survive_negation_prefix(text, command):
    node = _blocked_node(text, command)
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    business = [e for e in node.published if e.get("should_trigger_behavior_tree")]
    assert len(business) == 1
    assert business[0]["action"] == command
    assert business[0]["intent_source"] == "command_lexicon"


def test_decoder_error_has_no_exact_control_exception():
    node = _blocked_node("别动", "HOLD_POSITION", reason="kws_decode_error")
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    assert not any(e.get("should_trigger_behavior_tree") for e in node.published)


def test_late_negation_does_not_claim_that_an_already_published_command_was_blocked():
    node = _KwsRouteHarness(command_key="COME", asr_text="不要过来")
    node._kws_arbitration["publish_mode"] = "immediate"
    node._poll_kws_events()
    node._providers["kws"]._events.append({"kind": "kws_guard", "reason": "negation_keyword"})
    node._poll_kws_events()
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    assert len([e for e in node.published if e.get("should_trigger_behavior_tree")]) == 1
    assert any(f.get("result") == "late_veto" for _, f in node.traces)
    assert any(f.get("result") == "negation_after_kws_publish" for _, f in node.traces)


def test_deferred_mode_keeps_its_original_candidate_path():
    provider = KWSSherpaProvider({"publish_mode": "deferred", "negation_guard_enabled": True})
    provider._spotter = _Spotter(("COME",))
    provider.available = True
    provider.start_utterance()
    events = _feed(provider)
    assert [e["event_type"] for e in events] == ["EVT_VOICE_COMMAND_COME"]
    assert "kws_guard_window_ms" not in events[0]


def test_queued_utterance_keeps_its_veto_when_capture_moves_to_next_sentence():
    node = _blocked_node("过来")
    old_tracker = node._command_tracker
    node._command_tracker = UtteranceCommandTracker()
    node._command_tracker.begin("utterance-2")
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000},
                         "utterance-1", command_tracker=old_tracker)
    assert not any(e.get("should_trigger_behavior_tree") for e in node.published)
    assert node._command_tracker.asr_business_allowed
    old_tracker.finish()
    assert not old_tracker.kws_block_reason


def test_guarded_event_exposes_confirmation_window():
    from marsdog_voice_interaction.core.command_lexicon import CommandLexicon
    command = CommandLexicon(Path(__file__).parents[1] / "config/command_catalog.yaml").get_command("COME")
    event = command.to_kws_event({"kws_guard_window_ms": 250.0}, utterance_id="u1", latency_ms=400)
    slots = {s["key"]: s["value"] for s in event["slots"]}
    assert slots["kws_negation_guard"] == "passed"
    assert slots["kws_guard_window_ms"] == "250.0"
