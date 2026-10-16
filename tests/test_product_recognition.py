"""Product-table semantics and complete utterances through the publishing path."""

from pathlib import Path
import threading

import pytest
import yaml

from marsdog_voice_interaction.core.command_lexicon import CommandLexicon
from marsdog_voice_interaction.core.object_target_resolver import ObjectTargetResolver
from marsdog_voice_interaction.core.parameterized_commands import ParameterizedCommandRouter
from marsdog_voice_interaction.messages.audio_event import normalize_audio_event
from marsdog_voice_interaction.nodes.voice_interaction_node import VoiceInteractionNode
from marsdog_voice_interaction.providers.kws_sherpa import KWSSherpaProvider
from marsdog_voice_interaction.utils.config_loader import load_config
from test_command_lexicon import _DirectRouteHarness, _KwsRouteHarness
from test_kws_negation_guard import _blocked_node


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def lexicon():
    return CommandLexicon(ROOT / "config/command_catalog.yaml")


@pytest.fixture(scope="module")
def router():
    return ParameterizedCommandRouter(
        ObjectTargetResolver(ROOT / "config/object_targets.yaml"),
        ROOT / "config/location_targets.yaml",
    )


@pytest.mark.parametrize("command,phrases", [
    ("FOLLOW", "走/跟上/跟我来/跟我走/跟着我"),
    ("APPROACH", "过来点/靠近点/再过来些"),
    ("SHAKE_HAND", "握手/握个手/抬手/握手手/手手/握爪子"),
    ("STAND_UP", "站起来/起来/站好/站着"),
    ("SPIN", "转圈/转一圈/转个圈/转一转"),
    ("CIRCLE_AROUND", "绕圈/绕着转一圈/绕一圈"),
    ("DROP", "吐掉/放下/松开/松口/张嘴/不可以吃/不可以咬"),
    ("HOLD_POSITION", "等等/别动/停/不准动/不许动/老实点/不可以/No"),
    ("PRAISE", "真棒/真乖/小跟屁虫/小宝贝/乖狗狗"),
    ("SCOLD", "你怎么不听话/不跟你玩了/不理你/不想理你/讨厌你/臭狗/臭宝/我要生气了/我要惩罚你/罚你站着/罚你不准吃饭"),
    ("CALL_NAME", "小狗/跟屁虫"),
])
def test_product_table_phrase_routes(lexicon, command, phrases):
    for text in phrases.split("/"):
        match = lexicon.match(text)
        assert match is not None, text
        assert match.command_key == command, text


@pytest.mark.parametrize("text", [
    "太厉害了", "太厉害", "好厉害", "你真厉害", "厉害了", "真厉害", "好厉害啊", "你太厉害了",
])
def test_natural_praise_publishes_social_reaction_despite_spurious_come_keyword(text):
    # The reported 18:36:10 utterance had an unrelated COME KWS candidate.
    node = _KwsRouteHarness("COME", text)
    node._poll_kws_events()
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    business = [event for event in node.published if event.get("should_trigger_behavior_tree")]
    assert len(business) == 1
    event = business[0]
    assert event["event_type"] == "EVT_VOICE_COMMAND_PRAISE"
    assert event["dispatch_role"] == "social_reaction"
    assert not event["is_executable"]
    assert event["asr_text"] == text
    assert not node.intent_called


@pytest.mark.parametrize("text", [
    "厉害你这个非常的事世这啊你个事件事", "不厉害", "没什么厉害的", "我说的是太厉害了吗",
])
def test_praise_is_not_a_substring_trigger(lexicon, text):
    assert lexicon.match_fuzzy(text) is None


@pytest.mark.parametrize("text", ["我生气了", "我生气了。"])
def test_reported_angry_phrase_publishes_scold_social_reaction(text):
    # The 19:04:55 report had correct ASR text and no KWS candidate.
    node = _DirectRouteHarness(text)
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-anger")
    business = [event for event in node.published if event.get("should_trigger_behavior_tree")]
    assert len(business) == 1
    event = business[0]
    assert event["event_type"] == "EVT_VOICE_COMMAND_SCOLD"
    assert event["command_id"] == "CMD_SCOLD"
    assert event["intent_source"] == "command_lexicon"
    assert event["dispatch_role"] == "social_reaction"
    assert event["is_executable"] is False
    assert event["utterance_id"] == "utterance-anger"
    assert not node.intent_called
    assert not any(e["event_type"] == "EVT_VOICE_COMMAND_UNKNOWN" for e in node.published)


@pytest.mark.parametrize("text", [
    "我你我生气了", "我没有生气", "我不生气了", "他生气了", "你说我生气了吗",
])
def test_angry_phrase_does_not_accept_noise_negation_or_quoted_fragments(lexicon, text):
    assert lexicon.match_fuzzy(text) is None


@pytest.mark.parametrize("text,target", [
    ("去拿球", "dog toy ball"), ("把球拿来", "dog toy ball"),
    ("帮我把飞盘拿过来", "dog frisbee toy"),
    ("小狗，请你去拿那个玩具球回来吧", "dog toy ball"),
    ("拿球", "dog toy ball"), ("去叼飞盘回来", "dog frisbee toy"),
    ("找找那个纸箱", "cardboard shipping box"),
    ("把拖鞋给我拿来", "slipper"),
])
def test_fetch_publishes_canonical_target_without_intent_model(router, text, target):
    node = _DirectRouteHarness(text)
    node._parameterized_commands = router
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    events = [e for e in node.published if e.get("intent_source") == "parameterized_command"]
    assert len(events) == 1
    event = normalize_audio_event(events[0])
    assert event["event_type"] == "EVT_VOICE_COMMAND_FETCH"
    assert event["command_id"] == "CMD_FETCH_OBJECT"
    assert event["should_trigger_behavior_tree"]
    assert event["utterance_id"] == "utterance-1"
    assert event["speaker_id"] == "tester"
    assert _slots(event)["object_name"] == target
    assert not node.intent_called


@pytest.mark.parametrize("text", ["去拿球", "到客厅去", "去拿篮球鞋"])
def test_background_recognition_skips_model_and_preserves_parameter_result(router, text):
    node = _DirectRouteHarness(text)
    node._parameterized_commands = router
    node._command_fuzzy_matching = True
    node._interaction_lock = threading.RLock()
    node._speech_job_is_current = lambda _job: True
    audio = {"audio_samples": [0.1], "sample_rate": 16000}
    recognition = VoiceInteractionNode._recognize_speech(node, {
        "audio_data": audio, "tracker": node._command_tracker, "wake_id": "",
    })
    assert recognition["parsed_intent"] is None
    node._process_speech(audio, "utterance-1", recognition=recognition)
    assert node.published[-1]["intent_source"] == "parameterized_command"
    assert not node.intent_called


def _slots(event):
    return {slot["key"]: slot["value"] for slot in event["slots"]}


@pytest.mark.parametrize("text,name,identifier", [
    ("去狗窝", "狗窝", "dog_bed"), ("到客厅去", "客厅", "living_room"),
    ("回卧室去", "卧室", "bedroom"), ("去房间", "房间", "room"),
    ("请你去厨房吧", "厨房", "kitchen"), ("回狗床去", "狗窝", "dog_bed"),
    ("去门口", "门口", "entrance"), ("到门口去", "门口", "entrance"),
])
def test_location_event_contains_only_semantic_place_not_map_coordinates(router, text, name, identifier):
    node = _DirectRouteHarness(text)
    node._parameterized_commands = router
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    event = node.published[-1]
    assert event["event_type"] == "EVT_VOICE_COMMAND_GO_TO_LOCATION"
    assert event["command_id"] == "CMD_GO_TO_LOCATION"
    assert event["should_trigger_behavior_tree"]
    slots = _slots(event)
    assert (slots["location_name"], slots["location_id"]) == (name, identifier)
    assert not {"x", "y", "yaw", "map", "waypoint_id"}.intersection(slots)
    assert not node.intent_called


@pytest.mark.parametrize("text", [
    "不要去拿球", "请你别把球拿来", "不准去厨房", "我说的是去拿球",
    "他要到客厅去", "去拿球吗", "到客厅去吗", "球在哪里",
])
def test_templates_reject_negation_statements_and_questions(router, text):
    assert router.route(text) is None


@pytest.mark.parametrize("text,mention", [
    ("把咬绳拿来", "咬绳"), ("去拿玩具箱", "玩具箱"),
    ("去拿玩具和球", "玩具和球"),
    ("去拿篮球鞋", "篮球鞋"), ("去拿球和飞盘", "球和飞盘"),
    ("去拿球然后坐下", "球然后坐下"),
])
def test_unsupported_or_multiple_targets_never_degrade_to_partial_noun(router, text, mention):
    event = router.route(text)
    assert event and not event["is_executable"] and not event["should_trigger_behavior_tree"]
    assert _slots(event)["object_name"] == "NONE"
    assert _slots(event)["object_mention"] == mention


def test_unknown_location_is_published_without_inventing_a_destination(router):
    event = router.route("去办公室")
    assert event and not event["should_trigger_behavior_tree"]
    assert _slots(event)["location_id"] == "NONE"
    assert _slots(event)["location_mention"] == "办公室"


@pytest.mark.parametrize("text", ["去拿玩具", "找玩具", "去找玩具", "把玩具拿来", "请你去拿那个玩具回来吧"])
def test_generic_toy_publishes_executable_search_group_without_inventing_a_class(router, text):
    node = _DirectRouteHarness(text)
    node._parameterized_commands = router
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "toy-1")
    business = [e for e in node.published if e.get("should_trigger_behavior_tree")]
    assert len(business) == 1
    event = normalize_audio_event(business[0])
    assert event["event_type"] == "EVT_VOICE_COMMAND_FETCH"
    assert event["command_id"] == "CMD_FETCH_OBJECT"
    assert event["is_executable"] is True
    assert event["dispatch_role"] == "specific_command"
    assert event["utterance_id"] == "toy-1"
    slots = _slots(event)
    assert slots["object_name"] == "NONE"
    assert slots["object_mention"] == "玩具"
    assert slots["object_group"] == "toy"
    assert slots["object_match_source"] == "semantic_group"
    assert "reason" not in slots
    assert not node.intent_called


@pytest.mark.parametrize("text", ["不要去拿玩具", "去拿玩具吗", "他去拿玩具", "别去门口拿狗绳"])
def test_new_targets_keep_whole_utterance_safety(router, text):
    assert router.route(text) is None


@pytest.mark.parametrize("text,keyword,event_type", [
    ("去拿球", "GO_GET_IT", "EVT_VOICE_COMMAND_FETCH"),
    ("去厨房", "WALK", "EVT_VOICE_COMMAND_GO_TO_LOCATION"),
    ("把球拿来", "COME", "EVT_VOICE_COMMAND_FETCH"),
    ("罚你站着", "STAND_UP", "EVT_VOICE_COMMAND_SCOLD"),
    ("过来点", "COME", "EVT_VOICE_COMMAND_APPROACH"),
    ("拿球", "GO_GET_IT", "EVT_VOICE_COMMAND_FETCH"),
])
def test_keyword_fragment_cannot_steal_a_complete_command(router, text, keyword, event_type):
    node = _KwsRouteHarness(keyword, text)
    node._parameterized_commands = router
    node._poll_kws_events()
    assert node.published == []
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    events = [e for e in node.published if e.get("should_trigger_behavior_tree")]
    assert len(events) == 1
    assert events[0]["event_type"] == event_type


def test_production_kws_priority_retains_acoustic_negation_veto(router):
    config = load_config(ROOT / "config/voice.yaml")
    kws_config = config["providers"]["kws"]["config"]
    assert kws_config["publish_mode"] == "immediate"
    assert not config["providers"]["intent_llm"]["enabled"]
    assert not kws_config["kws_fallback_on_asr_empty"]
    assert KWSSherpaProvider(kws_config)._guard_enabled
    node = _blocked_node("去拿球", "GO_GET_IT")  # ASR lost '不要'.
    node._kws_arbitration["publish_mode"] = "immediate"
    node._parameterized_commands = router
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    assert not any(e.get("should_trigger_behavior_tree") for e in node.published)


@pytest.mark.parametrize("text", ["罚你不准吃饭", "不准吃饭"])
def test_exact_scolding_survives_veto_without_executing_embedded_actions(text):
    node = _blocked_node(text, "STAND_UP")
    node._kws_arbitration["publish_mode"] = "deferred"
    node._process_speech({"audio_samples": [0.1], "sample_rate": 16000}, "utterance-1")
    events = [e for e in node.published if e.get("should_trigger_behavior_tree")]
    assert [e["event_type"] for e in events] == ["EVT_VOICE_COMMAND_SCOLD"]
    assert not events[0]["is_executable"]


def test_ambiguous_location_alias_is_rejected(tmp_path, router):
    path = tmp_path / "locations.yaml"
    path.write_text(yaml.safe_dump({"version": "test", "locations": [
        {"location_id": "a", "name": "客厅"}, {"location_id": "b", "name": "客厅"},
    ]}))
    with pytest.raises(ValueError, match="Ambiguous"):
        ParameterizedCommandRouter(router.objects, path)
