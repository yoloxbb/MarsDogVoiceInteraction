from __future__ import annotations

import time
from pathlib import Path
import threading
from typing import Any
from types import SimpleNamespace

import pytest
import yaml

from marsdog_voice_interaction.core.command_lexicon import CommandLexicon
from marsdog_voice_interaction.core.object_target_resolver import (
    ObjectTargetResolver,
)
from marsdog_voice_interaction.core.interaction_state_machine import (
    State,
    VoiceInteractionStateMachine,
)
from marsdog_voice_interaction.core.utterance_command_tracker import (
    UtteranceCommandTracker,
)
from marsdog_voice_interaction.nodes.voice_interaction_node import (
    VoiceInteractionNode,
)
from marsdog_voice_interaction.messages.intent_protocol import (
    classification_to_event,
)
from marsdog_voice_interaction.messages.voice_event_types import (
    MODEL_INTENT_CLASSIFICATION_EVENT_TYPES,
    MODEL_INTENT_EVENT_TYPES,
    MODEL_INTENT_SHARED_COMMAND_EVENT_TYPES,
)
from marsdog_voice_interaction.providers.kws_sherpa import KWSSherpaProvider


CATALOG_PATH = Path(__file__).parents[1] / "config" / "command_catalog.yaml"
OBJECT_TARGETS_PATH = (
    Path(__file__).parents[1] / "config" / "object_targets.yaml"
)


@pytest.mark.parametrize(
    ("text", "command_key", "event_type"),
    [
        ("走", "WALK", "EVT_VOICE_COMMAND_WALK"),
        ("回来", "COME", "EVT_VOICE_COMMAND_COME"),
        ("跟我走", "FOLLOW", "EVT_VOICE_COMMAND_FOLLOW"),
        ("出去溜溜", "GO_OUT", "EVT_VOICE_COMMAND_GO_OUT"),
        ("回家", "GO_HOME", "EVT_VOICE_COMMAND_GO_HOME"),
        ("靠近点", "APPROACH", "EVT_VOICE_COMMAND_APPROACH"),
        ("退后", "BACK_UP", "EVT_VOICE_COMMAND_BACK_UP"),
        ("蹲下", "SIT", "EVT_VOICE_COMMAND_SIT"),
        ("躺下", "LIE_DOWN", "EVT_VOICE_COMMAND_LIE_DOWN"),
        ("biu", "PLAY_DEAD", "EVT_VOICE_COMMAND_PLAY_DEAD"),
        ("起来", "STAND_UP", "EVT_VOICE_COMMAND_STAND_UP"),
        ("站着", "STAND_STILL", "EVT_VOICE_COMMAND_STAND_STILL"),
        ("抬手", "SHAKE_HAND", "EVT_VOICE_COMMAND_SHAKE_HAND"),
        ("拍手", "HIGH_FIVE", "EVT_VOICE_COMMAND_HIGH_FIVE"),
        ("转圈", "SPIN", "EVT_VOICE_COMMAND_SPIN"),
        ("翻滚", "ROLL_OVER", "EVT_VOICE_COMMAND_ROLL_OVER"),
        ("不许动", "HOLD_POSITION", "EVT_VOICE_COMMAND_HOLD_POSITION"),
        ("松口", "DROP", "EVT_VOICE_COMMAND_DROP"),
        ("别叫", "QUIET", "EVT_VOICE_COMMAND_QUIET"),
    ],
)
def test_catalog_covers_all_19_core_command_groups(
    text: str,
    command_key: str,
    event_type: str,
) -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    match = lexicon.match(text)

    assert lexicon.command_count == 82
    assert lexicon.core_command_count == 19
    assert lexicon.phrase_count == 156
    assert lexicon.source_row_count == 116
    assert lexicon.covered_source_row_count == 116
    assert match is not None
    assert match.command_key == command_key
    assert match.event_type == event_type
    assert match.core
    assert not match.emit_known_event
    assert match.nlu_social == "NONE"
    assert match.nlu_intent != ""
    assert match.nlu_control in {"DO", "STOP"}


def test_catalog_special_events_are_unique_and_share_only_reviewed_actions() -> None:
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    commands = [
        command for command in raw["commands"]
        if command.get("enabled", True)
    ]
    command_keys = [command["command_key"] for command in commands]
    command_ids = [command["command_id"] for command in commands]
    event_types = [command["event_type"] for command in commands]

    assert len(commands) == 82
    assert len(command_keys) == len(set(command_keys))
    assert len(command_ids) == len(set(command_ids))
    assert len(event_types) == len(set(event_types))
    assert all(event_type.startswith("EVT_VOICE_") for event_type in event_types)
    assert all(
        not event_type.startswith("EVT_VOICE_INTENT_")
        for event_type in event_types
    )
    assert set(event_types).isdisjoint(
        MODEL_INTENT_CLASSIFICATION_EVENT_TYPES
    )
    assert (
        set(event_types) & MODEL_INTENT_EVENT_TYPES
        <= MODEL_INTENT_SHARED_COMMAND_EVENT_TYPES
    )


def test_catalog_rejects_retired_intent_event_namespace(tmp_path: Path) -> None:
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    raw["commands"][0]["event_type"] = "EVT_VOICE_INTENT_COMMAND_WALK"
    catalog_path = tmp_path / "command_catalog.yaml"
    catalog_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="uses retired INTENT event_type"):
        CommandLexicon(catalog_path)


def test_catalog_rejects_model_classification_event(tmp_path: Path) -> None:
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    raw["commands"][0]["event_type"] = "EVT_VOICE_PRAISE"
    catalog_path = tmp_path / "command_catalog.yaml"
    catalog_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reserved non-catalog event_type"):
        CommandLexicon(catalog_path)


def test_catalog_rejects_executable_social_event(tmp_path: Path) -> None:
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    call_name = next(
        command for command in raw["commands"]
        if command["command_key"] == "CALL_NAME"
    )
    call_name["control"] = "DO"
    catalog_path = tmp_path / "command_catalog.yaml"
    catalog_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="social event 'CALL' must use control NONE",
    ):
        CommandLexicon(catalog_path)


def test_catalog_rejects_duplicate_special_event_names(tmp_path: Path) -> None:
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    raw["commands"][1]["event_type"] = raw["commands"][0]["event_type"]
    catalog_path = tmp_path / "command_catalog.yaml"
    catalog_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate catalog event_type"):
        CommandLexicon(catalog_path)


def test_catalog_uses_exact_normalized_match_and_preserves_negation() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    assert lexicon.match("坐 下！").command_key == "SIT"  # type: ignore[union-attr]
    assert lexicon.match("你想不想吃") is None
    assert lexicon.match("不要坐下") is None
    assert lexicon.match("请你不要坐下") is None


def test_match_fuzzy_rescues_homophone_but_rejects_prefix_edits() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    # Exact matches are preserved and tagged as exact/expansion.
    exact = lexicon.match_fuzzy("坐下")
    assert exact is not None and exact.command_key == "SIT"
    assert exact.match_strategy in {"catalog_exact", "rule_expansion"}

    # A same-length, same-pinyin typo/homophone is rescued.
    homophone = lexicon.match_fuzzy("坐虾")
    assert homophone is not None and homophone.command_key == "SIT"
    assert homophone.match_strategy == "fuzzy_homophone"

    # Prefix/suffix edits and non-Chinese input must not fuzzy-match.
    assert lexicon.match_fuzzy("不要坐下") is None
    assert lexicon.match_fuzzy("你想不想吃") is None
    assert lexicon.match_fuzzy("请你不要坐下") is None
    assert lexicon.match_fuzzy("Good dog") is None


def _synthetic_catalog(tmp_path: Path, commands: list[dict[str, Any]]) -> Path:
    path = tmp_path / "command_catalog.yaml"
    path.write_text(
        yaml.safe_dump(
            {"version": "test-v1", "commands": commands},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_phrase_variants_match_without_changing_catalog_counts() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    assert lexicon.variant_phrase_count > 0
    # The reviewed product catalog contract is untouched.
    assert lexicon.phrase_count == 156
    assert lexicon.expanded_phrase_count == 1560

    for text, command_key in [
        ("往后退一点点", "BACK_UP"),
        ("后退", "BACK_UP"),
        ("去睡觉", "SLEEP"),
        ("转一圈", "SPIN"),
        ("往我这儿来", "COME"),
        ("吐掉", "DROP"),
    ]:
        match = lexicon.match(text)
        assert match is not None, text
        assert match.command_key == command_key, text
        assert match.match_strategy == "catalog_variant"


def test_phrase_variants_do_not_generate_expansions() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    # Only the declared surface form matches; the polite templates are applied
    # to product catalog phrases alone.
    assert lexicon.match("去睡觉一下") is None
    assert lexicon.match("请去睡觉") is None


def test_phrase_variants_reject_conflicts_and_unknown_commands(
    tmp_path: Path,
) -> None:
    base = {
        "command_key": "SIT",
        "command_id": "CMD_SIT",
        "event_type": "EVT_VOICE_COMMAND_SIT",
        "phrases": ["坐下"],
    }

    conflicting = _synthetic_catalog(
        tmp_path, [dict(base, phrases=["坐下", "坐好"])]
    )
    raw = yaml.safe_load(conflicting.read_text(encoding="utf-8"))
    raw["phrase_variants"] = {"SIT": ["坐好"]}
    conflicting.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="conflicts with"):
        CommandLexicon(conflicting)

    unknown = _synthetic_catalog(tmp_path, [base])
    raw = yaml.safe_load(unknown.read_text(encoding="utf-8"))
    raw["phrase_variants"] = {"NOPE": ["随便"]}
    unknown.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown command keys"):
        CommandLexicon(unknown)


def test_match_fuzzy_prefers_the_only_core_command_in_a_homophone_group(
    tmp_path: Path,
) -> None:
    path = _synthetic_catalog(
        tmp_path,
        [
            {
                "command_key": "SIT",
                "command_id": "CMD_SIT",
                "event_type": "EVT_VOICE_COMMAND_SIT",
                "core": True,
                "phrases": ["坐下"],
            },
            {
                "command_key": "LIE_DOWN",
                "command_id": "CMD_LIE_DOWN",
                "event_type": "EVT_VOICE_COMMAND_LIE_DOWN",
                "core": False,
                "phrases": ["坐虾"],
            },
        ],
    )
    lexicon = CommandLexicon(path)

    assert lexicon.match("作下") is None
    match = lexicon.match_fuzzy("作下")
    assert match is not None and match.command_key == "SIT"
    assert match.match_strategy == "fuzzy_homophone"


def test_match_fuzzy_refuses_a_genuinely_ambiguous_group(
    tmp_path: Path,
) -> None:
    path = _synthetic_catalog(
        tmp_path,
        [
            {
                "command_key": "SIT",
                "command_id": "CMD_SIT",
                "event_type": "EVT_VOICE_COMMAND_SIT",
                "core": False,
                "phrases": ["坐下"],
            },
            {
                "command_key": "LIE_DOWN",
                "command_id": "CMD_LIE_DOWN",
                "event_type": "EVT_VOICE_COMMAND_LIE_DOWN",
                "core": False,
                "phrases": ["坐虾"],
            },
        ],
    )
    lexicon = CommandLexicon(path)

    assert lexicon.match_fuzzy("作下") is None


def test_match_fuzzy_accepts_a_group_that_dispatches_identically(
    tmp_path: Path,
) -> None:
    # Two commands cannot share command_key/command_id/event_type, so the
    # equivalent case is one command declaring both homophones itself.
    path = _synthetic_catalog(
        tmp_path,
        [
            {
                "command_key": "SIT",
                "command_id": "CMD_SIT",
                "event_type": "EVT_VOICE_COMMAND_SIT",
                "phrases": ["坐下", "坐虾"],
            }
        ],
    )
    lexicon = CommandLexicon(path)

    match = lexicon.match_fuzzy("作下")
    assert match is not None and match.command_key == "SIT"


def test_catalog_generates_ten_auditable_variants_per_phrase() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    expansion = raw["expansion"]

    assert lexicon.expansion_enabled
    assert lexicon.variants_per_phrase == 10
    assert lexicon.expansion_profile_count == 5
    assert lexicon.expanded_phrase_count == 1560
    # phrase_count/expanded_phrase_count describe the reviewed product catalog
    # only; natural-speech variants are counted separately.
    variant_count = sum(
        len(phrases) for phrases in raw.get("phrase_variants", {}).values()
    )
    assert lexicon.variant_phrase_count == variant_count
    assert lexicon.total_match_phrase_count == 1716 + variant_count

    command_profiles = expansion["command_profiles"]
    phrase_profiles = expansion["phrase_profiles"]
    checked = 0
    for command in raw["commands"]:
        for phrase in command["phrases"]:
            profile = phrase_profiles.get(
                phrase,
                command_profiles.get(
                    command["command_key"],
                    expansion["default_profile"],
                ),
            )
            for rule in expansion["profiles"][profile]:
                expanded = rule["template"].format(phrase=phrase)
                match = lexicon.match(expanded)
                assert match is not None
                assert match.command_key == command["command_key"]
                assert match.catalog_phrase == phrase
                assert match.matched_phrase == expanded
                assert match.match_strategy == "rule_expansion"
                assert match.expansion_profile == profile
                assert match.expansion_rule == rule["id"]
                checked += 1

    assert checked == 1560


@pytest.mark.parametrize(
    ("text", "command_key", "catalog_phrase", "profile"),
    [
        ("请你坐下", "SIT", "坐下", "command"),
        ("宝贝，太棒了", "PRAISE", "太棒了", "social"),
        ("我想问，你在哪里", "ASK_WHERE_ARE_YOU", "你在哪里", "query"),
        ("跟你说，我好孤独", "OWNER_LONELY", "我好孤独", "statement"),
        ("嘿，小狗", "CALL_NAME", "小狗", "vocative"),
    ],
)
def test_representative_expansions_route_without_intent_model(
    text: str,
    command_key: str,
    catalog_phrase: str,
    profile: str,
) -> None:
    match = CommandLexicon(CATALOG_PATH).match(text)

    assert match is not None
    assert match.command_key == command_key
    assert match.catalog_phrase == catalog_phrase
    assert match.match_strategy == "rule_expansion"
    assert match.expansion_profile == profile


@pytest.mark.parametrize(
    ("text", "command_key", "event_type", "action_name"),
    [
        ("跟着我", "FOLLOW", "EVT_VOICE_COMMAND_FOLLOW", "ACT_FOLLOW"),
        (
            "出去溜溜",
            "GO_OUT",
            "EVT_VOICE_COMMAND_GO_OUT",
            "ACT_GO_OUT_TO_PLAY",
        ),
        ("回家", "GO_HOME", "EVT_VOICE_COMMAND_GO_HOME", "ACT_GO_HOME"),
        (
            "停",
            "HOLD_POSITION",
            "EVT_VOICE_COMMAND_HOLD_POSITION",
            "ACT_HOLD_POSITION",
        ),
        (
            "小宝贝",
            "CALL_NAME",
            "EVT_VOICE_COMMAND_CALL_NAME",
            "",
        ),
        ("真聪明", "PRAISE", "EVT_VOICE_COMMAND_PRAISE", ""),
        ("坏狗狗", "SCOLD", "EVT_VOICE_COMMAND_SCOLD", ""),
        (
            "吃饭",
            "EAT_MEAL",
            "EVT_VOICE_COMMAND_EAT_MEAL",
            "ACT_EAT_MEAL",
        ),
        (
            "肚子饿不饿",
            "RESPOND_HUNGRY_QUERY",
            "EVT_VOICE_COMMAND_RESPOND_HUNGRY_QUERY",
            "ACT_RESPOND_HUNGRY_QUERY",
        ),
        ("去便便", "TOILET", "EVT_VOICE_COMMAND_TOILET", ""),
        ("擦一擦脚", "CLEAN", "EVT_VOICE_COMMAND_CLEAN", ""),
        ("一起玩", "PLAY", "EVT_VOICE_COMMAND_PLAY", ""),
        (
            "去找妈妈",
            "FIND_MOM",
            "EVT_VOICE_COMMAND_FIND_MOM",
            "ACT_FIND_MOM",
        ),
        (
            "我好孤独",
            "OWNER_LONELY",
            "EVT_VOICE_COMMAND_OWNER_LONELY",
            "ACT_OWNER_LONELY",
        ),
    ],
)
def test_full_catalog_routes_representative_product_rows(
    text: str,
    command_key: str,
    event_type: str,
    action_name: str,
) -> None:
    match = CommandLexicon(CATALOG_PATH).match(text)

    assert match is not None
    assert match.command_key == command_key
    assert match.event_type == event_type
    assert match.action_name == action_name


def test_every_configured_phrase_matches_its_declared_command() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))

    checked = 0
    for command in raw["commands"]:
        for phrase in command["phrases"]:
            match = lexicon.match(str(phrase))
            assert match is not None
            assert match.command_key == command["command_key"]
            assert match.command_id == command["command_id"]
            assert match.event_type == command["event_type"]
            checked += 1

    assert checked == 156


def test_reference_english_phrases_are_metadata_not_runtime_triggers() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)

    assert lexicon.reference_phrase_count == 138
    assert lexicon.match("Good dog") is None
    assert lexicon.match("Come here") is None


def test_direct_event_carries_product_action_and_source_metadata() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)
    match = lexicon.match("吃饭")

    assert match is not None
    event = match.to_event(asr_text="吃饭", language="zh")
    slots = {item["key"]: item["value"] for item in event["slots"]}
    assert slots["action_name"] == "ACT_EAT_MEAL"
    assert slots["catalog_source_rows"] == "53"
    assert slots["behavior"] == "去指定地点进食"
    assert slots["match_strategy"] == "catalog_exact"


def test_expanded_event_carries_rule_audit_metadata() -> None:
    match = CommandLexicon(CATALOG_PATH).match("请你坐下")

    assert match is not None
    event = match.to_event(asr_text="请你坐下", language="zh")
    slots = {item["key"]: item["value"] for item in event["slots"]}
    assert slots["matched_phrase"] == "请你坐下"
    assert slots["catalog_phrase"] == "坐下"
    assert slots["match_strategy"] == "rule_expansion"
    assert slots["expansion_profile"] == "command"
    assert slots["expansion_rule"] == "polite_please_you"


def test_catalog_exposes_core_metadata_by_command_key() -> None:
    lexicon = CommandLexicon(CATALOG_PATH)
    command = lexicon.get_command("high_five")

    assert command is not None
    assert command.command_key == "HIGH_FIVE"
    assert command.command_id == "CMD_FIVE"
    assert command.event_type == "EVT_VOICE_COMMAND_HIGH_FIVE"
    assert not command.emit_known_event

    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    assert all(
        not lexicon.get_command(item["command_key"]).emit_known_event
        for item in raw["commands"]
    )


class _FakeASR:
    def __init__(self, text: str = "坐下") -> None:
        self._text = text

    def transcribe(self, _audio_data: dict[str, Any]) -> dict[str, Any]:
        return {"asr_text": self._text, "language": "zh", "latency_ms": 3.0}


class _FakeSpeaker:
    def verify(self, _audio_data: dict[str, Any]) -> dict[str, Any]:
        return {"speaker_id": "tester", "confidence": 0.8}


class _DirectRouteHarness:
    _process_speech = VoiceInteractionNode._process_speech
    _wakeup_supersedes_utterance = VoiceInteractionNode._wakeup_supersedes_utterance
    _clean_text = staticmethod(VoiceInteractionNode._clean_text)
    _effective_kws_arbitration = (
        VoiceInteractionNode._effective_kws_arbitration
    )
    _is_short_asr_text = VoiceInteractionNode._is_short_asr_text
    _select_kws_candidate = VoiceInteractionNode._select_kws_candidate
    _trace_recognition_arbitration = (
        VoiceInteractionNode._trace_recognition_arbitration
    )
    _publish_selected_kws_candidate = (
        VoiceInteractionNode._publish_selected_kws_candidate
    )
    _refresh_for_asr_result = (
        VoiceInteractionNode._refresh_for_asr_result
    )

    def __init__(self, text: str = "坐下") -> None:
        self._providers = {
            "asr": _FakeASR(text),
            "speaker": _FakeSpeaker(),
        }
        self._speaker_operation_lock = threading.RLock()
        self._state_machine = VoiceInteractionStateMachine()
        self._state_machine.force_state(State.ATTENTION)
        self._interaction_id = "interaction-1"
        self._command_tracker = UtteranceCommandTracker()
        self._command_tracker.begin("utterance-1")
        self._command_lexicon = CommandLexicon(CATALOG_PATH)
        self._kws_arbitration = {
            "publish_mode": "deferred",
            "arbitration_mode": "exclusive",
            "asr_long_text_wins": True,
            "kws_fallback_on_asr_empty": False,
            "short_requires_asr_agreement": True,
            "short_max_chars_zh": 2,
            "short_max_words_en": 2,
            "priority_command_keys": (),
            "priority_asr_aliases": {},
        }
        self.published: list[dict[str, Any]] = []
        self.traces: list[tuple[str, dict[str, Any]]] = []
        self.intent_called = False
        self.activity_reasons: list[str] = []

    def _publish(self, event: dict[str, Any]) -> None:
        self.published.append(dict(event))

    def _trace(self, record: str, **fields: Any) -> None:
        self.traces.append((record, fields))

    def _refresh_interaction_activity(self, *, reason: str = "activity") -> None:
        self.activity_reasons.append(reason)

    def _parse_intent(self, _text: str) -> dict[str, Any] | None:
        self.intent_called = True
        raise AssertionError("direct command must skip intent providers")


class _KwsRouteHarness:
    _poll_kws_events = VoiceInteractionNode._poll_kws_events
    _process_speech = VoiceInteractionNode._process_speech
    _wakeup_supersedes_utterance = VoiceInteractionNode._wakeup_supersedes_utterance
    _clean_text = staticmethod(VoiceInteractionNode._clean_text)
    _effective_kws_arbitration = (
        VoiceInteractionNode._effective_kws_arbitration
    )
    _is_short_asr_text = VoiceInteractionNode._is_short_asr_text
    _select_kws_candidate = VoiceInteractionNode._select_kws_candidate
    _trace_recognition_arbitration = (
        VoiceInteractionNode._trace_recognition_arbitration
    )
    _publish_selected_kws_candidate = (
        VoiceInteractionNode._publish_selected_kws_candidate
    )
    _refresh_for_asr_result = (
        VoiceInteractionNode._refresh_for_asr_result
    )

    def __init__(
        self,
        command_key: str = "HIGH_FIVE",
        asr_text: str = "机长",
    ) -> None:
        kws = KWSSherpaProvider({})
        kws.available = True
        kws._queue_keyword(command_key)
        self._providers = {
            "kws": kws,
            "asr": _FakeASR(asr_text),
            "speaker": _FakeSpeaker(),
        }
        self._speaker_operation_lock = threading.RLock()
        self._state_machine = VoiceInteractionStateMachine()
        self._state_machine.force_state(State.ATTENTION)
        self._interaction_id = "interaction-1"
        self._command_tracker = UtteranceCommandTracker()
        self._command_tracker.begin("utterance-1")
        self._command_lexicon = CommandLexicon(CATALOG_PATH)
        self._kws_arbitration = {
            "publish_mode": "deferred",
            "arbitration_mode": "exclusive",
            "asr_long_text_wins": True,
            "kws_fallback_on_asr_empty": False,
            "short_requires_asr_agreement": True,
            "short_max_chars_zh": 2,
            "short_max_words_en": 2,
            "priority_command_keys": (),
            "priority_asr_aliases": {},
        }
        self._utterance_started_monotonic = time.perf_counter()
        self.published: list[dict[str, Any]] = []
        self.traces: list[tuple[str, dict[str, Any]]] = []
        self.activity_refreshed = False
        self.activity_reasons: list[str] = []
        self.intent_called = False

    def _publish(self, event: dict[str, Any]) -> None:
        self.published.append(dict(event))

    def _trace(self, record: str, **fields: Any) -> None:
        self.traces.append((record, fields))

    def _refresh_interaction_activity(self, *, reason: str = "activity") -> None:
        self.activity_refreshed = True
        self.activity_reasons.append(reason)

    def _parse_intent(self, text: str) -> dict[str, Any]:
        self.intent_called = True
        return classification_to_event(
            "NONE",
            "NONE",
            "NONE",
            asr_text=text,
            source="rkllm",
        )


def test_hardware_wake_arriving_during_asr_discards_old_command() -> None:
    node = _DirectRouteHarness("坐下")
    node._latest_wake_id = "wake-1"
    wake_events = iter([{"wake_word": "ni2 hao3 wang4 cai2"}])
    node._providers["wakeup"] = SimpleNamespace(
        poll_event=lambda: next(wake_events, None)
    )

    def accept_wake(_event: dict[str, Any], _audio: Any) -> bool:
        node._latest_wake_id = "wake-2"
        return True

    node._handle_wakeup = accept_wake
    assert not node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1"
    )
    assert node.published == []
    assert node.activity_reasons == []


def test_core_kws_is_cached_without_publishing_before_arbitration() -> None:
    node = _KwsRouteHarness()

    node._poll_kws_events()

    assert node.published == []
    assert node._command_tracker.kws_candidate_count == 1
    candidate = node._command_tracker.single_kws_candidate()
    assert candidate is not None
    assert candidate["event_type"] == "EVT_VOICE_COMMAND_HIGH_FIVE"
    assert not node.activity_refreshed
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "kws"
        and fields.get("result") == "candidate"
        and fields.get("candidate_count") == 1
        and fields.get("published_event_types") == []
        for record, fields in node.traces
    )


def test_unconfirmed_short_kws_candidate_does_not_publish_action() -> None:
    # The ASR sample must NOT hit the lexicon on its own: when it does, the
    # direct-match path publishes from ASR evidence alone and the KWS gate is
    # never exercised.  The original sample "机长" became a fuzzy homophone hit
    # for HIGH_FIVE once command_lexicon.fuzzy_matching was enabled.
    node = _KwsRouteHarness(asr_text="苹果")

    node._poll_kws_events()
    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    event_types = [event["event_type"] for event in node.published]
    assert event_types.count("EVT_VOICE_COMMAND_KNOWN") == 0
    assert event_types.count("EVT_VOICE_COMMAND_HIGH_FIVE") == 0
    assert node.intent_called
    assert node.activity_refreshed
    assert node.activity_reasons == ["asr_result"]
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "command_lexicon"
        and fields.get("result") == "no_match"
        for record, fields in node.traces
    )
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("result") == "asr_selected"
        and fields.get("reason") == "short_asr_unconfirmed_kws"
        for record, fields in node.traces
    )


def test_direct_catalog_match_publishes_event_and_skips_intent_model() -> None:
    node = _DirectRouteHarness()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert not node.intent_called
    catalog_events = [
        event for event in node.published
        if event.get("intent_source") == "command_lexicon"
    ]
    assert [event["event_type"] for event in catalog_events] == [
        "EVT_VOICE_COMMAND_SIT",
    ]
    direct = catalog_events[0]
    assert direct["event_type"] == "EVT_VOICE_COMMAND_SIT"
    assert direct["dispatch_role"] == "specific_command"
    assert direct["intent_source"] == "command_lexicon"
    assert direct["should_trigger_behavior_tree"]
    assert direct["utterance_id"] == "utterance-1"
    assert node.activity_reasons == ["asr_result"]
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "command_lexicon"
        and fields.get("result") == "matched"
        and fields.get("action_name") == "ACT_SIT"
        and fields.get("source_rows") == [8]
        for record, fields in node.traces
    )
    assert any(
        record == "utterance_complete"
        and fields.get("result") == "published_direct_command"
        and fields.get("published_event_types") == [
            "EVT_VOICE_COMMAND_SIT",
        ]
        for record, fields in node.traces
    )


@pytest.mark.parametrize(
    ("text", "expected_asr_text"),
    [
        ("自己去玩吧", "自己去玩吧"),
        ("自己去玩吧！", "自己去玩吧"),
        ("小狗，自己去玩吧", "小狗自己去玩吧"),
    ],
)
def test_play_alone_publishes_unique_special_event(
    text: str, expected_asr_text: str,
) -> None:
    node = _DirectRouteHarness(text=text)

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-play-alone",
    )

    assert not node.intent_called
    events = [event for event in node.published if event.get("intent_source")]
    assert [event["event_type"] for event in events] == [
        "EVT_VOICE_COMMAND_PLAY_ALONE",
    ]
    event = events[0]
    assert event["command_id"] == "CMD_PLAY_ALONE"
    assert event["specific_event_type"] == event["event_type"]
    assert event["dispatch_role"] == "specific_command"
    assert event["intent_source"] == "command_lexicon"
    assert event["control"] == "DO"
    assert event["is_executable"] is True
    assert event["should_trigger_behavior_tree"] is True
    assert event["asr_text"] == expected_asr_text
    slots = {slot["key"]: slot["value"] for slot in event["slots"]}
    assert slots["command_key"] == "PLAY_ALONE"
    assert slots["catalog_phrase"] == "自己去玩吧"
    assert slots["action_name"] == "ACT_PLAY_ALONE"
    assert slots["behavior"] == "去随机位置自己玩"
    assert "catalog_source_rows" not in slots


@pytest.mark.parametrize("text", ["不要自己去玩吧", "别自己去玩吧", "我说的是自己去玩吧吗"])
def test_play_alone_does_not_match_negation_or_embedded_phrase(text: str) -> None:
    lexicon = CommandLexicon(CATALOG_PATH)
    assert lexicon.match(text) is None
    assert lexicon.match_fuzzy(text) is None


def test_expanded_catalog_match_publishes_event_and_skips_intent_model() -> None:
    node = _DirectRouteHarness(text="请你坐下")

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "command_lexicon"
        and fields.get("result") == "matched"
        and fields.get("match_strategy") == "rule_expansion"
        and fields.get("catalog_phrase") == "坐下"
        and fields.get("matched_phrase") == "请你坐下"
        and fields.get("expansion_profile") == "command"
        and fields.get("expansion_rule") == "polite_please_you"
        for record, fields in node.traces
    )
    assert node.activity_reasons == ["asr_result"]
    catalog_events = [
        event for event in node.published
        if event.get("intent_source") == "command_lexicon"
    ]
    assert [event["event_type"] for event in catalog_events] == [
        "EVT_VOICE_COMMAND_SIT",
    ]
    for event in catalog_events:
        slots = {slot["key"]: slot["value"] for slot in event["slots"]}
        assert slots["catalog_phrase"] == "坐下"
        assert slots["matched_phrase"] == "请你坐下"
        assert slots["match_strategy"] == "rule_expansion"
        assert slots["expansion_profile"] == "command"
        assert slots["expansion_rule"] == "polite_please_you"


@pytest.mark.parametrize(
    ("text", "event_type", "emotion", "category"),
    [
        (
            "小宝贝",
            "EVT_VOICE_COMMAND_CALL_NAME",
            "CALL",
            "social",
        ),
        (
            "真聪明",
            "EVT_VOICE_COMMAND_PRAISE",
            "PRAISE",
            "praise",
        ),
        (
            "坏狗狗",
            "EVT_VOICE_COMMAND_SCOLD",
            "SCOLD",
            "blame",
        ),
    ],
)
def test_social_catalog_event_uses_event_specific_tree_authority(
    text: str,
    event_type: str,
    emotion: str,
    category: str,
) -> None:
    node = _DirectRouteHarness(text)

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    direct = node.published[-1]
    assert not node.intent_called
    assert direct["event_type"] == event_type
    assert direct["emotion"] == emotion
    assert direct["social"] == emotion
    assert direct["intent"] == "NONE"
    assert direct["raw_nlu_tag"] == f"{emotion}|NONE|NONE"
    assert direct["action"] == "NONE"
    assert direct["control"] == "NONE"
    assert direct["intent_category"] == category
    assert not direct["is_executable"]
    is_reaction = emotion in {"PRAISE", "SCOLD"}
    assert direct["should_trigger_behavior_tree"] is is_reaction
    assert direct["dispatch_role"] == (
        "social_reaction" if is_reaction else "specific_command"
    )
    assert node._state_machine.state == (
        State.EXECUTION if is_reaction else State.ATTENTION
    )
    assert any(
        record == "utterance_complete"
        and fields.get("result") == (
            "published_social_reaction"
            if is_reaction else "published_catalog_event"
        )
        for record, fields in node.traces
    )


def test_short_asr_catalog_agreement_selects_kws_result_group() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="坐下")
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    business_events = [
        event for event in node.published
        if event.get("intent_source") in {"kws", "command_lexicon"}
    ]
    assert [event["event_type"] for event in business_events] == [
        "EVT_VOICE_COMMAND_SIT",
    ]
    assert all(event["intent_source"] == "kws" for event in business_events)
    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("reason") == "short_asr_catalog_agrees"
        for record, fields in node.traces
    )
    assert node.activity_reasons == ["asr_result"]


def test_long_asr_text_containing_keyword_selects_asr_catalog() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="请你坐下")
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    business_events = [
        event for event in node.published
        if event.get("intent_source") in {"kws", "command_lexicon"}
    ]
    assert [event["event_type"] for event in business_events] == [
        "EVT_VOICE_COMMAND_SIT",
    ]
    assert all(
        event["intent_source"] == "command_lexicon"
        for event in business_events
    )
    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("result") == "asr_selected"
        and fields.get("reason") == "long_asr_text"
        for record, fields in node.traces
    )


@pytest.mark.parametrize(
    ("command_key", "asr_text", "event_type"),
    [
        (
            "EAT_CANNED_FOOD",
            "去滚罐",
            "EVT_VOICE_COMMAND_EAT_CANNED_FOOD",
        ),
        ("GO_GET_IT", "去哪", "EVT_VOICE_COMMAND_GO_GET_IT"),
    ],
)
def test_configured_kws_priority_recovers_confirmed_asr_errors(
    command_key: str,
    asr_text: str,
    event_type: str,
) -> None:
    node = _KwsRouteHarness(command_key=command_key, asr_text=asr_text)
    node._kws_arbitration["priority_command_keys"] = frozenset({
        "EAT_CANNED_FOOD",
        "GO_GET_IT",
    })
    node._kws_arbitration["priority_asr_aliases"] = {
        "EAT_CANNED_FOOD": ("去滚罐",),
        "GO_GET_IT": ("去哪",),
    }
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    business_events = [
        event for event in node.published
        if event.get("intent_source") in {"kws", "command_lexicon"}
    ]
    assert [event["event_type"] for event in business_events] == [event_type]
    assert business_events[0]["asr_text"] == asr_text
    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("result") == "kws_selected"
        and fields.get("reason") == "configured_kws_priority_alias"
        for record, fields in node.traces
    )


def test_priority_kws_does_not_override_unlisted_asr_text() -> None:
    node = _KwsRouteHarness(
        command_key="GO_GET_IT",
        asr_text="我们今天去哪里玩",
    )
    node._kws_arbitration["priority_command_keys"] = ("GO_GET_IT",)
    node._kws_arbitration["priority_asr_aliases"] = {
        "GO_GET_IT": ("去哪",),
    }
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert not any(
        event.get("intent_source") == "kws" for event in node.published
    )
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("reason") == "long_asr_text"
        for record, fields in node.traces
    )


def test_short_conflicting_kws_candidate_defers_to_asr_catalog() -> None:
    node = _KwsRouteHarness(command_key="STAND_UP", asr_text="坐下")
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    business_events = [
        event for event in node.published
        if event.get("intent_source") in {"kws", "command_lexicon"}
    ]
    assert [event["event_type"] for event in business_events] == [
        "EVT_VOICE_COMMAND_SIT",
    ]
    assert all(
        event["intent_source"] == "command_lexicon"
        for event in business_events
    )
    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("result") == "asr_selected"
        and fields.get("reason") == "short_asr_catalog_conflict"
        for record, fields in node.traces
    )


def test_long_asr_text_without_catalog_match_does_not_trigger_kws() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="不要坐下")
    node._poll_kws_events()

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert node.intent_called
    assert not any(
        event.get("intent_source") == "kws" for event in node.published
    )
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("result") == "asr_selected"
        and fields.get("reason") == "long_asr_text"
        for record, fields in node.traces
    )


def test_empty_asr_does_not_execute_single_kws_candidate() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="")
    node._poll_kws_events()

    assert not node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    business_events = [
        event for event in node.published if event.get("intent_source") == "kws"
    ]
    assert business_events == []
    assert not any(event["event_type"] == "speech" for event in node.published)
    assert not node.intent_called
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("reason") == "empty_asr_fallback_disabled"
        for record, fields in node.traces
    )
    assert not node.activity_refreshed


def test_multiple_kws_candidates_defer_to_asr_pipeline() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="不要坐下")
    node._providers["kws"]._queue_keyword("STAND_UP")
    node._poll_kws_events()

    assert node._command_tracker.kws_candidate_count == 2
    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    assert node.intent_called
    assert not any(
        event.get("intent_source") == "kws" for event in node.published
    )
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "recognition_arbitration"
        and fields.get("reason") == "multiple_kws_candidates"
        for record, fields in node.traces
    )


class _ModelRouteHarness(_DirectRouteHarness):
    def __init__(
        self,
        text: str,
        labels: tuple[str, str, str] | None,
    ) -> None:
        super().__init__(text)
        self._labels = labels
        self._object_target_resolver = ObjectTargetResolver(
            OBJECT_TARGETS_PATH
        )

    def _parse_intent(self, text: str) -> dict[str, Any] | None:
        self.intent_called = True
        if self._labels is None:
            return None
        social, intent, control = self._labels
        return classification_to_event(
            social,
            intent,
            control,
            asr_text=text,
            source="rkllm",
        )


@pytest.mark.parametrize("labels", [None, ("NONE", "NONE", "NONE")])
def test_non_command_asr_refreshes_before_speaker_and_semantic_routing(labels) -> None:
    node = _ModelRouteHarness("我今天随便说句话", labels)

    class Speaker:
        def verify(self, _audio):
            assert node.activity_reasons == ["asr_result"]
            return {"speaker_id": "unknown", "confidence": 0.0}

    node._providers["speaker"] = Speaker()
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000})
    assert node.activity_reasons == ["asr_result"]


@pytest.mark.parametrize("text", ["", "  \t\n", None])
def test_empty_or_blank_asr_does_not_refresh(text) -> None:
    node = _DirectRouteHarness(text)
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000})
    assert node.activity_reasons == []


def test_asr_exception_does_not_refresh() -> None:
    node = _DirectRouteHarness()

    class BrokenASR:
        def transcribe(self, _audio):
            raise RuntimeError("ASR failed")

    node._providers["asr"] = BrokenASR()
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000})
    assert node.activity_reasons == []


def test_kws_fallback_without_asr_does_not_refresh() -> None:
    node = _KwsRouteHarness(command_key="SIT", asr_text="")
    node._kws_arbitration["kws_fallback_on_asr_empty"] = True
    node._poll_kws_events()
    assert node._process_speech({"audio_samples": [0.1], "sample_rate": 16000})
    assert any(event.get("intent_source") == "kws" for event in node.published)
    assert node.activity_reasons == []


def test_catalog_miss_routes_model_intent_to_social_specific_and_summary() -> None:
    node = _ModelRouteHarness("真乖请坐好", ("PRAISE", "SIT", "DO"))

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    model_events = [
        event for event in node.published
        if event.get("intent_source") == "rkllm"
    ]
    assert node.intent_called
    assert [event["event_type"] for event in model_events] == [
        "EVT_VOICE_PRAISE",
        "EVT_VOICE_COMMAND_SIT",
        "EVT_VOICE_COMMAND_KNOWN",
    ]
    assert [
        event["should_trigger_behavior_tree"] for event in model_events
    ] == [False, True, False]
    assert model_events[1]["dispatch_role"] == "specific_command"
    assert model_events[2]["specific_event_type"] == (
        "EVT_VOICE_COMMAND_SIT"
    )
    assert node.activity_reasons == ["asr_result"]


def test_rkllm_go_label_without_go_evidence_is_summary_only() -> None:
    # The utterance must miss the command lexicon, otherwise the catalog
    # answers the request and the rkllm action gate is never reached.
    # "去睡觉" is now a SLEEP catalog variant.
    node = _ModelRouteHarness("读一下消息", ("NONE", "GO", "DO"))

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    model_events = [
        event for event in node.published
        if event.get("intent_source") == "rkllm"
    ]
    assert [event["event_type"] for event in model_events] == [
        "EVT_VOICE_COMMAND_KNOWN"
    ]
    assert not model_events[0]["should_trigger_behavior_tree"]
    assert node.activity_reasons == ["asr_result"]
    slots = {
        slot["key"]: slot["value"] for slot in model_events[0]["slots"]
    }
    assert slots["model_action_gate"] == "rejected"
    assert slots["model_action_gate_reason"] == (
        "missing_intent_text_evidence"
    )


def test_catalog_miss_with_all_none_publishes_neutral_event() -> None:
    node = _ModelRouteHarness("读一下消息", ("NONE", "NONE", "NONE"))

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    model_events = [
        event for event in node.published
        if event.get("intent_source") == "rkllm"
    ]
    assert [event["event_type"] for event in model_events] == [
        "EVT_VOICE_NEUTRAL"
    ]
    assert not model_events[0]["should_trigger_behavior_tree"]
    assert any(
        record == "utterance_complete"
        and fields.get("result") == "published"
        for record, fields in node.traces
    )


def test_catalog_miss_with_invalid_model_output_uses_intent_unknown() -> None:
    node = _ModelRouteHarness("无法解析的输入", None)

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    diagnostic_events = [
        event for event in node.published
        if event.get("intent_source") == "invalid_protocol_fallback"
    ]
    assert [event["event_type"] for event in diagnostic_events] == [
        "EVT_VOICE_COMMAND_UNKNOWN"
    ]
    assert not diagnostic_events[0]["should_trigger_behavior_tree"]


def test_asr_number_text_is_normalized_before_publish_and_intent() -> None:
    node = _ModelRouteHarness(
        "编号三百二十一",
        ("NONE", "NONE", "NONE"),
    )

    assert node._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    speech_event = next(
        event for event in node.published
        if event.get("event_type") == "speech"
    )
    model_event = next(
        event for event in node.published
        if event.get("intent_source") == "rkllm"
    )
    assert speech_event["asr_text"] == "编号321"
    assert model_event["asr_text"] == "编号321"


def test_model_find_query_routes_only_supported_detector_target() -> None:
    supported = _ModelRouteHarness(
        "看看那个球在哪里",
        ("NONE", "FIND_TOY", "QUERY"),
    )
    unsupported = _ModelRouteHarness(
        "看看那个布偶娃娃在哪里",
        ("NONE", "FIND_TOY", "QUERY"),
    )

    assert supported._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )
    assert unsupported._process_speech(
        {"audio_samples": [0.1], "sample_rate": 16000},
        "utterance-1",
    )

    supported_events = [
        event for event in supported.published
        if event.get("intent_source") == "rkllm"
    ]
    unsupported_events = [
        event for event in unsupported.published
        if event.get("intent_source") == "rkllm"
    ]
    assert [event["event_type"] for event in supported_events] == [
        "EVT_VOICE_COMMAND_FETCH",
        "EVT_VOICE_STATUS_CARE",
    ]
    assert supported_events[0]["should_trigger_behavior_tree"]
    supported_slots = {
        slot["key"]: slot["value"] for slot in supported_events[0]["slots"]
    }
    assert supported_slots["object_name"] == "dog toy ball"

    assert [event["event_type"] for event in unsupported_events] == [
        "EVT_VOICE_STATUS_CARE"
    ]
    assert not unsupported_events[0]["should_trigger_behavior_tree"]
    unsupported_slots = {
        slot["key"]: slot["value"]
        for slot in unsupported_events[0]["slots"]
    }
    assert unsupported_slots["object_name"] == "NONE"
    assert unsupported_slots["object_mention"] == "布偶娃娃"
    assert any(
        record == "stage_complete"
        and fields.get("stage") == "object_target"
        and fields.get("result") == "unsupported"
        for record, fields in unsupported.traces
    )
