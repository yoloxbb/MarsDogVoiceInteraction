"""YAML configuration loader for MarsDog perception.

Loads perception.yaml and merges with defaults, providing typed access
to provider configs, topic settings, and debug options.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_CONFIG_PATH_FIELDS = (
    ("logging", "dir"),
    ("storage", "root"),
    ("providers", "audio", "config", "vad_model"),
    ("providers", "kws", "config", "model_dir"),
    ("providers", "kws", "config", "keywords_file"),
    ("providers", "asr", "config", "asr_model"),
    ("providers", "asr", "config", "tokens"),
    ("providers", "speaker", "config", "speaker_model"),
    ("providers", "intent_llm", "config", "model"),
    ("providers", "intent_llm", "config", "lib_path"),
)


def _resolve_config_paths(
    data: dict[str, Any],
    config_dir: Path,
) -> None:
    """Resolve declared filesystem paths relative to the YAML directory."""
    for fields in _CONFIG_PATH_FIELDS:
        parent: Any = data
        for field in fields[:-1]:
            if not isinstance(parent, dict):
                break
            parent = parent.get(field)
        else:
            field = fields[-1]
            if not isinstance(parent, dict) or field not in parent:
                continue
            raw_value = parent[field]
            if raw_value is None or not str(raw_value).strip():
                continue
            path = Path(str(raw_value)).expanduser()
            if not path.is_absolute():
                path = config_dir / path
            parent[field] = str(path.resolve())


def load_config(path: str | Path) -> dict[str, Any]:
    """Load and validate a perception YAML config file.

    Args:
        path: Path to a YAML config file (e.g. config/perception.yaml).

    Returns:
        Parsed config dict. Empty dict if file not found or invalid.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    config_path = Path(path).expanduser().resolve()

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Config {config_path} must be a YAML mapping, got {type(data).__name__}")

    _resolve_config_paths(data, config_path.parent)
    return data


def load_config_safe(path: str | Path, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load config with fallback to defaults on any error.

    Args:
        path: Path to a YAML config file.
        defaults: Fallback dict if load fails.

    Returns:
        Parsed config dict or defaults.
    """
    try:
        return load_config(path)
    except Exception:
        return defaults if defaults is not None else {}
