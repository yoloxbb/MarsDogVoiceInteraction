from pathlib import Path

import pytest

from marsdog_voice_interaction.adapters.llm.rkllm_engine_chatml import (
    DEFAULT_SYSTEM_PROMPT,
    build_classification_prompt,
    parse_classification_output,
)
from marsdog_voice_interaction.providers.intent_rkllm import _parse_tag
from marsdog_voice_interaction.utils.config_loader import load_config


ROOT = Path(__file__).parents[1]


def test_model_intent_prompt_matches_fine_tuning_messages() -> None:
    assert DEFAULT_SYSTEM_PROMPT == (
        "Classify the owner's MasDog utterance. "
        "Return exactly one label in SOCIAL|INTENT|CONTROL format and nothing else."
    )
    assert build_classification_prompt("站端正了。") == (
        "<|im_start|>system\n"
        "Classify the owner's MasDog utterance. "
        "Return exactly one label in SOCIAL|INTENT|CONTROL format and nothing else."
        "<|im_end|>\n"
        "<|im_start|>user\n"
        "站端正了。<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def test_model_intent_output_parser_rejects_prose_and_illegal_combinations() -> None:
    assert parse_classification_output("NONE|STAND|DO\n") == (
        "NONE|STAND|DO"
    )
    assert parse_classification_output("NONE|OWNER_LEAVE|DO") == (
        "NONE|OWNER_LEAVE|DO"
    )
    assert _parse_tag("PRAISE|SIT|DO") == {
        "SOCIAL": "PRAISE",
        "INTENT": "SIT",
        "CONTROL": "DO",
    }
    with pytest.raises(ValueError):
        parse_classification_output("result: NONE|SIT|DO")
    with pytest.raises(ValueError):
        parse_classification_output("NONE|DOG_STATUS|DO")


def test_production_config_selects_the_rkllm_artifact() -> None:
    config = load_config(ROOT / "config" / "voice.yaml")
    intent_config = config["providers"]["intent_llm"]["config"]

    assert Path(intent_config["model"]).name == (
        "qwen2_5_5b_rk3588_260829_w8a8.rkllm"
    )
    assert intent_config["system_prompt"] == DEFAULT_SYSTEM_PROMPT
