"""Product workbook coverage and compound commands through publication."""
from pathlib import Path
import csv
import io

import pytest
import yaml

from scripts.generate_command_catalog import build, read_sheet, phrases_from_cell, render
from marsdog_voice_interaction.core.command_lexicon import CommandLexicon
from marsdog_voice_interaction.core.object_target_resolver import ObjectTargetResolver
from marsdog_voice_interaction.core.parameterized_commands import ParameterizedCommandRouter
from test_command_lexicon import _DirectRouteHarness
from test_kws_negation_guard import _blocked_node

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def lexicon():
    return CommandLexicon(ROOT / "config/command_catalog.yaml")


@pytest.fixture(scope="module")
def router():
    return ParameterizedCommandRouter(ObjectTargetResolver(ROOT / "config/object_targets.yaml"),
                                      ROOT / "config/location_targets.yaml", ROOT / "config/person_targets.yaml")


def test_checked_artifacts_match_workbook():
    catalog, rules = build()
    for path, content in render(catalog, rules).items():
        assert path.read_text(encoding="utf-8") == content.replace("\r\n", "\n"), path
    assert len(catalog["commands"]) + len(catalog["non_catalog_entries"]) == 49
    assert catalog["source_rows"] == list(range(3, 42)) + list(range(47, 51))
    assert {e["source_row"]: e["route"] for e in catalog["non_catalog_entries"]} == {
        42: "FETCH", 43: "FIND_PERSON", 44: "GO_TO_LOCATION",
        45: "FETCH_FROM_LOCATION", 46: "CHAT_FALLBACK", 51: "SOUND_SOURCE",
    }


def test_sound_source_export_keeps_its_distinct_non_motion_contract():
    catalog, rules = build()
    rendered = render(catalog, rules)[ROOT / "docs/DOWNSTREAM_COMMAND_MAP.csv"]
    rows = list(csv.DictReader(io.StringIO(rendered.lstrip("\ufeff"))))
    sound = next(row for row in rows if row["command_key"] == "SOUND_SOURCE")
    assert sound["source_rows"] == "51"
    assert sound["event_type"] == "EVT_VOICE_SOUND_SOURCE_DETECTED"
    assert sound["command_id"] == ""
    assert sound["is_executable"] == "false"
    assert sound["should_trigger_behavior_tree"] == "true"
    assert sound["dispatch_role"] == "sound_source"


def test_every_fixed_workbook_phrase_is_covered_and_duplicate_bark_wins(lexicon):
    rows = read_sheet(ROOT / "docs/语音识别内容.xlsx", "Sheet1")
    for row in list(range(4, 42)) + list(range(47, 51)):
        for phrase in phrases_from_cell(rows[row]["D"]):
            match = lexicon.match(phrase)
            assert match is not None, (row, phrase)
            assert match.source_rows == ((20,) if phrase in {"叫两声", "说话"} else (row,))
    for phrase in ("小火小火", "小狗", "跟屁虫", "嘬嘬嘬"):
        event = lexicon.match(phrase).to_event(asr_text=phrase, language="zh")
        assert event["event_type"] == "EVT_VOICE_CALL"
        assert not event["is_executable"] and not event["should_trigger_behavior_tree"]


@pytest.mark.parametrize("text,key", [
    ("叫两声", "BARK"), ("说话", "BARK"), ("你会说话吗", "SOCIAL_GREETING"),
    ("hello", "SOCIAL_GREETING"), ("Hello", "SOCIAL_GREETING"),
    ("HELLO!", "SOCIAL_GREETING"), ("Hello。", "SOCIAL_GREETING"),
    ("哈喽", "SOCIAL_GREETING"),
    ("往前走走", "MOVE_FORWARD"), ("走", "FOLLOW"), ("转圈", "SPIN"),
    ("左转", "TURN_LEFT"), ("右转", "TURN_RIGHT"),
    ("左转。", "TURN_LEFT"), ("右转。", "TURN_RIGHT"),
    ("绕圈", "CIRCLE_AROUND"), ("来玩", "PLAY"), ("抓小狗游戏", "CHASE"),
    ("去追球", "CHASE_BALL"),
    ("玩累了", "PLAY_END"), ("不跟你玩了", "SCOLD"),
    ("不开心", "OWNER_UNHAPPY"), ("开心", "OWNER_HAPPY"),
    ("回来", "COME"), ("回来咯", "OWNER_RETURNED"), ("我回去啦", "OWNER_GOING_OUT"),
])
def test_semantically_distinct_phrases_publish_once(text, key):
    node = _DirectRouteHarness(text)
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "workbook-1")
    business = [e for e in node.published if e.get("should_trigger_behavior_tree")]
    assert [e["event_type"] for e in business] == ["EVT_VOICE_COMMAND_" + key]
    assert not node.intent_called


@pytest.mark.parametrize("text,key", [
    ("低头", "LOOK_DOWN"), ("看地板", "LOOK_DOWN"), ("头低一点", "LOOK_DOWN"),
    ("抬头", "LOOK_UP"), ("往上看", "LOOK_UP"), ("看上边", "LOOK_UP"),
    ("这是啥", "WHAT_IS_THIS"), ("那是什么", "WHAT_IS_THIS"), ("看前边", "WHAT_IS_THIS"),
    ("看", "WHO_IS_IT"), ("我是谁", "WHO_IS_IT"), ("看看谁回来了", "WHO_IS_IT"),
    ("倒下来", "PLAY_DEAD"),
])
def test_added_workbook_phrases_are_executable(text, key, lexicon):
    match = lexicon.match(text)
    assert match is not None and match.command_key == key
    event = match.to_event(asr_text=text, language="zh")
    assert event["event_type"] == event["specific_event_type"] == "EVT_VOICE_COMMAND_" + key
    assert event["dispatch_role"] == "specific_command" and event["is_executable"]


@pytest.mark.parametrize("text", ["不玩了", "不想玩了", "不开心", "别叫", "不可以咬", "罚你不准吃饭",
                                 "请你别叫", "小狗，不许动", "请不可以咬"])
def test_exact_negative_semantics_survive_guard_without_embedded_action(text, lexicon):
    node = _blocked_node(text, "COME")
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    business = [e for e in node.published if e.get("should_trigger_behavior_tree")]
    assert [e["event_type"] for e in business] == [lexicon.match(text).event_type]


@pytest.mark.parametrize("text", [
    "不要前进", "别向左转", "不要来追我呀", "我说的是向右转", "他想玩游戏",
    "不要左转", "不要右转", "别左转", "别右转", "不准左转", "不准右转",
    "我说的是左转", "我说的是右转", "他要左转", "他要右转", "左转吗", "右转吗",
    "不要hello", "我说的是hello", "他在说hello", "hello world", "say hello",
    "不要去追球", "别去追球", "我说的是去追球", "他要去追球", "去追球吗",
])
def test_new_commands_do_not_accept_embedded_or_negated_phrases(text, lexicon):
    assert lexicon.match_fuzzy(text) is None


@pytest.mark.parametrize("text", ["雷", "爬", "做"])
def test_single_character_homophones_do_not_authorize_actions(text, lexicon):
    assert lexicon.match_fuzzy(text) is None


@pytest.mark.parametrize("text", [
    "去厨房拿狗绳子", "到厨房去拿狗绳过来", "回厨房去找狗绳子来", "请你去厨房去拿狗绳吧",
])
def test_compound_fetch_publishes_one_event_with_both_targets(text, router):
    node = _DirectRouteHarness(text)
    node._parameterized_commands = router
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "workbook-fetch")
    events = [e for e in node.published if e.get("intent_source") == "parameterized_command"]
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "EVT_VOICE_COMMAND_FETCH_FROM_LOCATION"
    assert event["is_executable"] and event["should_trigger_behavior_tree"]
    slots = {s["key"]: s["value"] for s in event["slots"]}
    assert slots["location_id"] == "kitchen"
    assert slots["object_name"] == "dog leash"
    assert slots["catalog_source_rows"] == "45"
    assert not node.intent_called


@pytest.mark.parametrize("text", ["去拿狗绳子", "拿狗碗过来", "找狗绳过来", "找狗绳子来", "去找狗碗",
                                 "去找找狗绳", "去拿狗绳拿给我"])
def test_workbook_object_templates(router, text):
    event = router.route(text)
    assert event["event_type"] == "EVT_VOICE_COMMAND_FETCH"
    assert event["is_executable"]


@pytest.mark.parametrize("text,row,key", [
    ("去拿狗绳", 42, "FETCH"), ("去找主人", 43, "FIND_PERSON"),
    ("到客厅去", 44, "GO_TO_LOCATION"), ("去厨房拿狗绳", 45, "FETCH_FROM_LOCATION"),
])
def test_parameter_events_reference_the_current_workbook_row(router, text, row, key):
    event = router.route(text)
    assert event["is_executable"]
    slots = {s["key"]: s["value"] for s in event["slots"]}
    assert slots["catalog_source_rows"] == str(row)
    catalog = yaml.safe_load((ROOT / "config/command_catalog.yaml").read_text())
    assert next(e["route"] for e in catalog["non_catalog_entries"] if e["source_row"] == row) == key


@pytest.mark.parametrize("text,key,row", [
    ("去追球", "CHASE_BALL", 37), ("来追我呀", "CHASE", 34), ("去拿球", "FETCH", 42),
])
def test_chasing_ball_person_and_fetching_publish_distinct_events(router, text, key, row):
    node = _DirectRouteHarness(text)
    node._parameterized_commands = router
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "distinct-game")
    business = [e for e in node.published if e.get("should_trigger_behavior_tree")]
    assert len(business) == 1
    assert business[0]["event_type"] == "EVT_VOICE_COMMAND_" + key
    assert business[0]["is_executable"]
    assert {s["key"]: s["value"] for s in business[0]["slots"]}["catalog_source_rows"] == str(row)
    assert not node.intent_called


@pytest.mark.parametrize("text", ["去办公室拿狗绳", "去办公室拿玩具", "去厨房拿玩具箱", "去厨房拿狗绳和狗碗", "去厨房拿狗绳然后坐下"])
def test_unknown_or_multiple_compound_targets_are_non_executable(router, text):
    event = router.route(text)
    assert event["event_type"] == "EVT_VOICE_COMMAND_FETCH_FROM_LOCATION"
    assert not event["is_executable"] and not event["should_trigger_behavior_tree"]


@pytest.mark.parametrize("text,location,object_name,group", [
    ("去门口拿狗绳", "entrance", "dog leash", None),
    ("去门口拿玩具", "entrance", "NONE", "toy"),
    ("去厨房拿玩具", "kitchen", "NONE", "toy"),
])
def test_new_compound_targets_publish_both_targets_once(router, text, location, object_name, group):
    node = _DirectRouteHarness(text)
    node._parameterized_commands = router
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "compound-1")
    business = [e for e in node.published if e.get("should_trigger_behavior_tree")]
    assert len(business) == 1
    event = business[0]
    assert event["event_type"] == "EVT_VOICE_COMMAND_FETCH_FROM_LOCATION"
    assert event["is_executable"]
    slots = {s["key"]: s["value"] for s in event["slots"]}
    assert slots["location_id"] == location
    assert slots["object_name"] == object_name
    assert slots.get("object_group") == group
    assert "reason" not in slots
    assert not node.intent_called


@pytest.mark.parametrize("text", ["不要去厨房拿狗绳", "去厨房拿狗绳吗", "他去找爸爸", "别去找妈妈"])
def test_parameter_templates_reject_negation_questions_and_reports(router, text):
    assert router.route(text) is None


def test_person_requires_actual_configured_identity(router, tmp_path):
    unknown = router.route("去找爸爸")
    assert unknown["event_type"] == "EVT_VOICE_COMMAND_FIND_PERSON"
    assert not unknown["is_executable"]
    assert {s["key"]: s["value"] for s in unknown["slots"]}["person_id"] == "NONE"
    people = tmp_path / "people.yaml"
    people.write_text(yaml.safe_dump({"version": "test", "persons": [
        {"person_id": "registered-test-id", "name": "测试人物", "aliases": ["爸爸"]},
    ]}, allow_unicode=True))
    configured = ParameterizedCommandRouter(router.objects, ROOT / "config/location_targets.yaml", people)
    event = configured.route("去找爸爸")
    assert event["is_executable"]
    assert {s["key"]: s["value"] for s in event["slots"]}["person_id"] == "registered-test-id"


def test_sparse_source_rows_are_validated(lexicon, tmp_path):
    raw = yaml.safe_load(lexicon.catalog_path.read_text())
    raw["source_rows"][-1] = 46
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True))
    with pytest.raises(ValueError, match="source row coverage mismatch"):
        CommandLexicon(path)


@pytest.mark.parametrize("identifier", [None, "", "NONE", "unknown", 123])
def test_person_placeholder_ids_cannot_authorize_search(router, tmp_path, identifier):
    path = tmp_path / "people.yaml"
    path.write_text(yaml.safe_dump({"version": "test", "persons": [
        {"person_id": identifier, "name": "爸爸"},
    ]}))
    with pytest.raises(ValueError, match="non-placeholder ID"):
        ParameterizedCommandRouter(router.objects, ROOT / "config/location_targets.yaml", path)
