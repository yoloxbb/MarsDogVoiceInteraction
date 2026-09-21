import numpy as np
import pytest

from marsdog_voice_interaction.providers.speaker_sherpa import SpeakerSherpaProvider


def provider_with_scores(monkeypatch, scores):
    provider = SpeakerSherpaProvider({"match_threshold": 0.5, "min_score_margin": 0.05})
    provider.available = True
    provider._extractor = object()
    monkeypatch.setattr(provider, "_prepare_embedding", lambda *_: (np.array([1., 0.]), "ok"))
    provider.set_templates({name: [np.array([score, np.sqrt(1-score**2)])]
                            for name, score in scores.items()})
    return provider


@pytest.mark.parametrize("scores", [
    {"owner": 0.6, "family_member_1": 0.6},
    {"family_member_1": 0.6, "owner": 0.6},
    {"owner": 0.65, "family_member_1": 0.62},
])
def test_ambiguous_identities_are_unknown(monkeypatch, scores):
    provider = provider_with_scores(monkeypatch, scores)
    result = provider.verify({"audio_samples": [0.1]})
    assert not result["matched"]
    assert result["speaker_id"] == "unknown"
    assert result["reason"] == "ambiguous_identity"


@pytest.mark.parametrize("scores,expected", [
    ({"owner": 0.8, "family_member_1": 0.6}, "owner"),
    ({"owner": 0.8}, "owner"),
    ({"owner": 0.4, "family_member_1": 0.1}, "unknown"),
])
def test_separated_and_below_threshold_identities(monkeypatch, scores, expected):
    result = provider_with_scores(monkeypatch, scores).verify({"audio_samples": [0.1]})
    assert result["speaker_id"] == expected


def test_margin_compares_identities_not_samples(monkeypatch):
    provider = provider_with_scores(monkeypatch, {"owner": .8, "family_member_1": .5})
    provider._templates["owner"].append(provider._templates["owner"][0].copy())
    assert provider.verify({"audio_samples": [0.1]})["matched"]


def test_short_audio_remains_unknown():
    provider = SpeakerSherpaProvider({})
    provider.available = True
    provider._extractor = object()
    result = provider.verify({"audio_samples": np.ones(4000), "sample_rate":16000})
    assert result["reason"] == "audio_too_short"


def test_extractor_failure_is_unknown():
    class BrokenExtractor:
        def create_stream(self):
            raise RuntimeError("model failed")
    provider = SpeakerSherpaProvider({})
    provider.available = True
    provider._extractor = BrokenExtractor()
    result = provider.verify({"audio_samples": np.ones(16000)})
    assert not result["matched"]
    assert result["reason"] == "compute_failed"
