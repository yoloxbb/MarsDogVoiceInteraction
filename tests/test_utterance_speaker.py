from pathlib import Path
import threading
from types import MethodType

import pytest

from marsdog_voice_interaction.nodes.voice_interaction_node import VoiceInteractionNode
from marsdog_voice_interaction.utils.config_loader import load_config
from test_command_lexicon import _DirectRouteHarness, _KwsRouteHarness


class CountingSpeaker:
    def __init__(self, *identities):
        self.identities = iter(identities)
        self.calls = []

    def verify(self, audio_data):
        self.calls.append(audio_data)
        identity = next(self.identities)
        return {"speaker_id": identity, "confidence": 0.8, "matched": identity != "unknown"}


def publish_owner_wake(node):
    node._latest_wake_id = "wake-1"
    node._is_interaction_active = lambda: True
    VoiceInteractionNode._publish_wake_identity_result(
        node, node._interaction_id, "wake-1",
        {"speaker_id": "owner", "confidence": 0.95,
         "speaker_role": "owner", "speaker_status": "matched"},
    )
    assert node.published[-1]["speaker_id"] == "owner"
    assert not hasattr(node, "_wake_speaker_context")
    node.published.clear()


def test_owner_wake_does_not_override_each_utterances_speaker_result():
    node = _DirectRouteHarness("坐下")
    node._utterance_speaker_enabled = True
    speaker = CountingSpeaker("owner", "family_member_1", "unknown")
    node._providers["speaker"] = speaker
    publish_owner_wake(node)
    expected_events = ["EVT_VOICE_MASTER_ID", "EVT_VOICE_FOLK_ID", "EVT_VOICE_UNMASTER_ID"]
    for index, identity in enumerate(("owner", "family_member_1", "unknown")):
        utterance_id = f"utterance-{index + 1}"
        node._command_tracker.begin(utterance_id)
        audio = {"audio_samples": [index + 0.1], "sample_rate": 16000}
        assert node._process_speech(audio, utterance_id)
        events = [e for e in node.published if e.get("utterance_id") == utterance_id]
        assert len([e for e in events if e["event_type"] == expected_events[index]]) == 1
        assert any(e["event_type"] == "speech" for e in events)
        assert any(e["event_type"] == "EVT_VOICE_COMMAND_SIT" for e in events)
        assert all(e["speaker_id"] == identity for e in events)
        assert all(e.get("speaker_reason") != "inherited_from_wake" for e in events)
    assert len(speaker.calls) == 3
    assert [call["audio_samples"] for call in speaker.calls] == [[0.1], [1.1], [2.1]]


def test_immediate_kws_stays_pending_and_queued_speech_checks_its_own_voice_once():
    node = _KwsRouteHarness("HIGH_FIVE", "击掌")
    node._kws_arbitration["publish_mode"] = "immediate"
    node._utterance_speaker_enabled = True
    node._interaction_active = True
    node._interaction_lock = threading.RLock()
    node._speech_job_is_current = MethodType(VoiceInteractionNode._speech_job_is_current, node)
    speaker = CountingSpeaker("family_member_2")
    node._providers["speaker"] = speaker
    publish_owner_wake(node)
    node._poll_kws_events()
    command = node.published[-1]
    assert command["speaker_id"] == "unknown" and command["speaker_status"] == "pending"
    audio = {"audio_samples": [0.2], "sample_rate": 16000}
    job = {"interaction_id": node._interaction_id, "wake_id": "wake-1",
           "tracker": node._command_tracker, "audio_data": audio}
    recognition = VoiceInteractionNode._recognize_speech(node, job)
    node._process_speech(audio, "utterance-1", recognition=recognition)
    assert speaker.calls == [audio]
    assert len([e for e in node.published if e["event_type"] == "EVT_VOICE_COMMAND_HIGH_FIVE"]) == 1
    identity = next(e for e in node.published if e["event_type"] == "EVT_VOICE_FOLK_ID")
    speech = next(e for e in node.published if e["event_type"] == "speech")
    assert identity["speaker_id"] == speech["speaker_id"] == "family_member_2"
    assert identity["utterance_id"] == speech["utterance_id"] == command["utterance_id"]


@pytest.mark.parametrize("text", ["", "坐下"])
def test_voice_is_verified_even_when_asr_text_is_empty(text):
    node = _DirectRouteHarness(text)
    node._utterance_speaker_enabled = True
    speaker = CountingSpeaker("unknown")
    node._providers["speaker"] = speaker
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    assert len(speaker.calls) == 1
    assert any(e["event_type"] == "EVT_VOICE_UNMASTER_ID" for e in node.published)


@pytest.mark.parametrize("filename", ["voice.yaml", "voice.respeaker.yaml"])
def test_production_configs_enable_speaker_verification_for_every_utterance(filename):
    config = load_config(Path(__file__).parents[1] / "config" / filename)
    assert config["interaction"]["utterance_speaker_enabled"] is True
    assert config["providers"]["speaker"]["enabled"] is True
