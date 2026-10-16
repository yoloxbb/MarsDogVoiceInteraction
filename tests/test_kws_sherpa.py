from collections import Counter
from pathlib import Path

import numpy as np

from marsdog_voice_interaction.messages.voice_event_types import (
    EVT_VOICE_COMMAND_GO_OUT,
    EVT_VOICE_COMMAND_SIT,
)
from marsdog_voice_interaction.providers.kws_sherpa import KWSSherpaProvider


class _FakeStream:
    def __init__(self) -> None:
        self.ready = False

    def accept_waveform(self, _sample_rate: int, _samples: np.ndarray) -> None:
        self.ready = True


class _FakeSpotter:
    def create_stream(self) -> _FakeStream:
        return _FakeStream()

    @staticmethod
    def is_ready(stream: _FakeStream) -> bool:
        return stream.ready

    @staticmethod
    def decode_stream(stream: _FakeStream) -> None:
        stream.ready = False

    @staticmethod
    def get_result(_stream: _FakeStream) -> str:
        return "SIT"

    @staticmethod
    def reset_stream(_stream: _FakeStream) -> None:
        return None


def test_streaming_kws_builds_canonical_command_event() -> None:
    provider = KWSSherpaProvider({})
    provider._spotter = _FakeSpotter()
    provider.available = True
    provider.start_utterance()

    provider.accept_waveform(np.zeros(320, dtype=np.float32), 16000)
    event = provider.poll_event()

    assert event is not None
    assert event["event_type"] == EVT_VOICE_COMMAND_SIT
    assert event["action"] == "SIT"
    assert event["control"] == "DO"
    assert event["intent_source"] == "kws"
    assert event["should_trigger_behavior_tree"]

    provider.accept_waveform(np.zeros(320, dtype=np.float32), 16000)
    assert provider.poll_event() is None


def test_go_out_keyword_builds_executable_event() -> None:
    provider = KWSSherpaProvider({})

    provider._queue_keyword("GO_OUT")
    event = provider.poll_event()

    assert event is not None
    assert event["event_type"] == EVT_VOICE_COMMAND_GO_OUT
    assert event["action"] == "GO_OUT"
    assert event["control"] == "DO"
    assert event["is_executable"]
    assert event["should_trigger_behavior_tree"]


def test_keyword_file_label_inventory_matches_expected() -> None:
    keywords = (
        Path(__file__).parents[1] / "config" / "kws_keywords.txt"
    ).read_text(encoding="utf-8").splitlines()
    labels = Counter(
        line.rsplit("@", 1)[1]
        for line in keywords
        if line.strip()
    )
    assert labels == Counter({
        "QUIET": 4, "HOLD_POSITION": 6, "FOLLOW": 4, "COME": 5,
        "APPROACH": 3, "BACK_UP": 5, "SIT": 2, "LIE_DOWN": 2,
        "STAND_UP": 4, "SHAKE_HAND": 5, "HIGH_FIVE": 3, "SPIN": 4,
        "DROP": 7, "PLAY_DEAD": 2,
    })


def test_keyword_file_excludes_ambiguous_single_character_destination_word() -> None:
    keywords = (
        Path(__file__).parents[1] / "config" / "kws_keywords_raw.txt"
    ).read_text(encoding="utf-8").splitlines()
    phrases = {
        line.rsplit("@", 1)[0].strip()
        for line in keywords
        if line.strip() and not line.startswith("#")
    }

    assert {"走", "来", "坐", "停"}.isdisjoint(phrases)
    assert "去" not in phrases
