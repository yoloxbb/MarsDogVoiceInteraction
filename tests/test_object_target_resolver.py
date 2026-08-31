from __future__ import annotations

from pathlib import Path

from marsdog_voice_interaction.core.object_target_resolver import (
    ObjectTargetResolver,
    object_resolution_slots,
)


CATALOG_PATH = (
    Path(__file__).parents[1] / "config" / "object_targets.yaml"
)


def _slot_map(slots: list[dict[str, str]]) -> dict[str, str]:
    return {slot["key"]: slot["value"] for slot in slots}


def test_catalog_has_exact_product_detector_inventory() -> None:
    resolver = ObjectTargetResolver(CATALOG_PATH)

    assert resolver.target_count == 18
    assert set(resolver.target_names) == {
        "dog toy ball",
        "dog frisbee toy",
        "dog tug ring toy",
        "dog collar",
        "dog bowl",
        "dog leash",
        "dog treat bag",
        "dog food can",
        "dog bed",
        "trash can",
        "cardboard shipping box",
        "sock",
        "slipper",
        "tissue paper",
        "door",
        "stairs",
        "cat",
        "dog",
    }


def test_longest_alias_prevents_dog_from_stealing_dog_food_can() -> None:
    resolver = ObjectTargetResolver(CATALOG_PATH)

    match = resolver.resolve("帮我找一下狗粮罐头")

    assert match is not None
    assert match.object_name == "dog food can"
    assert match.matched_alias == "狗粮罐头"


def test_chinese_and_english_aliases_resolve_to_canonical_class() -> None:
    resolver = ObjectTargetResolver(CATALOG_PATH)

    chinese = resolver.resolve("看看那个飞盘在哪里")
    english = resolver.resolve("please locate the cardboard shipping box")

    assert chinese is not None
    assert chinese.object_name == "dog frisbee toy"
    assert english is not None
    assert english.object_name == "cardboard shipping box"


def test_short_english_alias_does_not_match_inside_another_word() -> None:
    resolver = ObjectTargetResolver(CATALOG_PATH)

    assert resolver.resolve("please locate the target") is None


def test_unsupported_object_is_explicit_none_with_original_mention() -> None:
    resolver = ObjectTargetResolver(CATALOG_PATH)

    slots, supported = object_resolution_slots(
        resolver,
        "看看那个布偶娃娃在哪里",
    )
    values = _slot_map(slots)

    assert not supported
    assert values["object_name"] == "NONE"
    assert values["object_mention"] == "布偶娃娃"
    assert values["object_match_source"] == "unsupported"


def test_supported_object_slots_use_detector_class_name() -> None:
    resolver = ObjectTargetResolver(CATALOG_PATH)

    slots, supported = object_resolution_slots(
        resolver,
        "看看那个球在哪里",
    )
    values = _slot_map(slots)

    assert supported
    assert values["object_name"] == "dog toy ball"
    assert values["object_mention"] == "球"
    assert values["object_match_source"] == "asr_rule"

