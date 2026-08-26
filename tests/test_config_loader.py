from __future__ import annotations

from pathlib import Path

from marsdog_voice_interaction.utils.config_loader import load_config


def test_load_config_resolves_declared_paths_from_yaml_directory(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "voice.yaml"
    config_path.write_text(
        """
logging:
  dir: ../log
storage:
  root: ../data
command_lexicon:
  catalog: command_catalog.yaml
providers:
  audio:
    config:
      vad_model: ../../models/vad.onnx
  kws:
    config:
      keywords_file: keywords.txt
  wakeup:
    config:
      port: /dev/ttyACM0
topics:
  audio_event: /perception/audio_event
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["logging"]["dir"] == str(tmp_path / "log")
    assert config["storage"]["root"] == str(tmp_path / "data")
    assert config["command_lexicon"]["catalog"] == str(
        config_dir / "command_catalog.yaml"
    )
    assert config["providers"]["audio"]["config"]["vad_model"] == str(
        tmp_path.parent / "models" / "vad.onnx"
    )
    assert config["providers"]["kws"]["config"]["keywords_file"] == str(
        config_dir / "keywords.txt"
    )
    assert config["providers"]["wakeup"]["config"]["port"] == "/dev/ttyACM0"
    assert config["topics"]["audio_event"] == "/perception/audio_event"


def test_project_configs_only_use_relative_filesystem_paths() -> None:
    root = Path(__file__).parents[1]
    production_text = (root / "config" / "voice.yaml").read_text(
        encoding="utf-8"
    )
    production = load_config(root / "config" / "voice.yaml")
    event_mock = load_config(root / "config" / "voice.mock.yaml")
    pipeline_mock = load_config(root / "config" / "voice.pipeline.mock.yaml")

    assert "/home/cat/" not in production_text
    assert production["command_lexicon"] == {
        "enabled": True,
        "catalog": str(root / "config" / "command_catalog.yaml"),
    }
    assert event_mock["storage"]["root"] == str(root / "data" / "mock")
    assert pipeline_mock["storage"]["root"] == str(
        root / "data" / "pipeline_mock"
    )
