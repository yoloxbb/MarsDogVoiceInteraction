"""Deterministic ASR-text routing for catalog voice events."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from marsdog_voice_interaction.messages.intent_protocol import EMOTION_LABELS
from marsdog_voice_interaction.messages import voice_event_types


_TRAILING_AND_INLINE_PUNCTUATION = re.compile(
    r"""[，。！？、；：“”\"'（）【】《》…—～,.!?;:()\[\]<>/\s]+"""
)
_KNOWN_VOICE_EVENTS = {
    value
    for name, value in vars(voice_event_types).items()
    if name.startswith("EVT_VOICE_") and isinstance(value, str)
}


def normalize_command_phrase(value: str) -> str:
    """Normalize one controlled phrase for exact matching."""
    if not isinstance(value, str):
        return ""
    return _TRAILING_AND_INLINE_PUNCTUATION.sub("", value).strip().lower()


@dataclass(frozen=True)
class DirectCommandMatch:
    """One exact command-catalog match."""

    command_key: str
    command_id: str
    event_type: str
    control: str
    matched_phrase: str
    catalog_phrase: str
    catalog_version: str
    core: bool
    emotion: str = "NONE"
    action_name: str = ""
    behavior: str = ""
    source_rows: tuple[int, ...] = ()
    slots: tuple[tuple[str, str], ...] = ()

    def to_event(self, *, asr_text: str, language: str) -> dict[str, Any]:
        """Build a v1-compatible direct-command event payload.

        Executable authority comes from the exact catalog event, not from an
        intent-model classification. Social events may use ``control=NONE``
        and therefore remain non-executable.
        """
        slots = [
            {"key": key, "value": value}
            for key, value in self.slots
        ]
        slots.extend([
            {"key": "command_key", "value": self.command_key},
            {"key": "matched_phrase", "value": self.matched_phrase},
            {"key": "catalog_phrase", "value": self.catalog_phrase},
            {"key": "command_catalog_version", "value": self.catalog_version},
        ])
        if self.action_name:
            slots.append({"key": "action_name", "value": self.action_name})
        if self.behavior:
            slots.append({"key": "behavior", "value": self.behavior})
        if self.source_rows:
            slots.append({
                "key": "catalog_source_rows",
                "value": ",".join(str(row) for row in self.source_rows),
            })
        triggers_behavior_tree = self.control in {"DO", "CANCEL"}
        if self.control == "CANCEL":
            intent_category = "cancel"
        elif self.emotion == "PRAISE":
            intent_category = "praise"
        elif self.emotion == "REPRIMAND":
            intent_category = "blame"
        elif triggers_behavior_tree:
            intent_category = "command"
        else:
            intent_category = "none"
        return {
            "event_type": self.event_type,
            "asr_text": asr_text,
            "emotion": self.emotion,
            "action": (
                "NONE" if self.emotion != "NONE" else self.command_key
            ),
            "control": self.control,
            "command_id": self.command_id,
            "intent_category": intent_category,
            "intent_source": "command_lexicon",
            "intent_confidence": 1.0,
            "slots": slots,
            "response_text": "",
            "is_executable": triggers_behavior_tree,
            "should_trigger_behavior_tree": triggers_behavior_tree,
            "language": language or "zh",
        }


class CommandLexicon:
    """Load and exactly match a versioned direct-command catalog."""

    def __init__(self, catalog_path: str | Path) -> None:
        self.catalog_path = Path(catalog_path).expanduser().resolve()
        with self.catalog_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
        if not isinstance(raw, dict):
            raise ValueError("Command catalog must be a YAML mapping")

        self.version = str(raw.get("version", "")).strip()
        if not self.version:
            raise ValueError("Command catalog version is required")
        commands = raw.get("commands")
        if not isinstance(commands, list):
            raise ValueError("Command catalog commands must be a list")

        self.source_name = str(raw.get("source_name", "")).strip()
        self.source_row_count = int(raw.get("source_row_count", 0) or 0)
        if self.source_row_count < 0:
            raise ValueError("Command catalog source_row_count cannot be negative")

        self._phrases: dict[str, DirectCommandMatch] = {}
        self._command_keys: set[str] = set()
        self._source_rows: set[int] = set()
        self.command_count = 0
        self.core_command_count = 0
        self.reference_phrase_count = 0
        for index, item in enumerate(commands, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Command entry {index} must be a mapping")
            if not bool(item.get("enabled", True)):
                continue
            self._load_command(index, item)

        if not self._phrases:
            raise ValueError("Command catalog contains no enabled phrases")
        if self.source_row_count:
            expected_rows = set(range(1, self.source_row_count + 1))
            if self._source_rows != expected_rows:
                missing = sorted(expected_rows - self._source_rows)
                unexpected = sorted(self._source_rows - expected_rows)
                raise ValueError(
                    "Command catalog source row coverage mismatch: "
                    f"missing={missing} unexpected={unexpected}"
                )

    @property
    def phrase_count(self) -> int:
        return len(self._phrases)

    @property
    def covered_source_row_count(self) -> int:
        return len(self._source_rows)

    def match(self, text: str) -> DirectCommandMatch | None:
        normalized = normalize_command_phrase(text)
        template = self._phrases.get(normalized)
        if template is None:
            return None
        return DirectCommandMatch(
            command_key=template.command_key,
            command_id=template.command_id,
            event_type=template.event_type,
            control=template.control,
            matched_phrase=text,
            catalog_phrase=template.catalog_phrase,
            catalog_version=template.catalog_version,
            core=template.core,
            emotion=template.emotion,
            action_name=template.action_name,
            behavior=template.behavior,
            source_rows=template.source_rows,
            slots=template.slots,
        )

    def _load_command(self, index: int, item: dict[str, Any]) -> None:
        command_key = str(item.get("command_key", "")).strip().upper()
        command_id = str(item.get("command_id", "")).strip().upper()
        event_type = str(item.get("event_type", "")).strip().upper()
        control = str(item.get("control", "DO")).strip().upper()
        core = bool(item.get("core", False))
        emotion = str(item.get("emotion", "NONE")).strip().upper()
        action_name = str(item.get("action_name", "")).strip().upper()
        behavior = str(item.get("behavior", "")).strip()
        if not command_key or not command_id or not event_type:
            raise ValueError(
                f"Command entry {index} requires command_key/command_id/event_type"
            )
        if not event_type.startswith("EVT_VOICE_"):
            raise ValueError(
                f"Command entry {index} has invalid event_type {event_type!r}"
            )
        derived_event = (
            f"EVT_VOICE_COMMAND_{action_name[4:]}"
            if action_name.startswith("ACT_") else ""
        )
        if event_type not in _KNOWN_VOICE_EVENTS and event_type != derived_event:
            raise ValueError(
                f"Command entry {index} uses undeclared event_type "
                f"{event_type!r}"
            )
        if command_key in self._command_keys:
            raise ValueError(f"Duplicate command_key {command_key!r}")
        self._command_keys.add(command_key)
        if control not in {"NONE", "DO", "CANCEL"}:
            raise ValueError(
                f"Command entry {index} has invalid control {control!r}"
            )
        if emotion not in EMOTION_LABELS:
            raise ValueError(
                f"Command entry {index} has invalid emotion {emotion!r}"
            )

        raw_source_rows = item.get("source_rows", [])
        if not isinstance(raw_source_rows, list):
            raise ValueError(f"Command entry {index} source_rows must be a list")
        source_rows = tuple(int(row) for row in raw_source_rows)
        if any(row <= 0 for row in source_rows):
            raise ValueError(f"Command entry {index} has invalid source_rows")
        duplicate_rows = self._source_rows.intersection(source_rows)
        if duplicate_rows:
            raise ValueError(
                f"Command entry {index} repeats source_rows "
                f"{sorted(duplicate_rows)}"
            )
        self._source_rows.update(source_rows)

        reference_phrases = item.get("reference_phrases_en", [])
        if not isinstance(reference_phrases, list):
            raise ValueError(
                f"Command entry {index} reference_phrases_en must be a list"
            )
        self.reference_phrase_count += len(reference_phrases)

        raw_slots = item.get("slots", {})
        if raw_slots is None:
            raw_slots = {}
        if not isinstance(raw_slots, dict):
            raise ValueError(f"Command entry {index} slots must be a mapping")
        slots = tuple(
            (str(key), str(value)) for key, value in raw_slots.items()
        )

        phrases = item.get("phrases")
        if not isinstance(phrases, list) or not phrases:
            raise ValueError(f"Command entry {index} phrases must be a list")
        loaded = 0
        for raw_phrase in phrases:
            phrase = str(raw_phrase).strip()
            normalized = normalize_command_phrase(phrase)
            if not normalized:
                raise ValueError(f"Command entry {index} contains an empty phrase")
            existing = self._phrases.get(normalized)
            if existing is not None and existing.command_key != command_key:
                raise ValueError(
                    f"Phrase {phrase!r} maps to both "
                    f"{existing.command_key} and {command_key}"
                )
            self._phrases[normalized] = DirectCommandMatch(
                command_key=command_key,
                command_id=command_id,
                event_type=event_type,
                control=control,
                matched_phrase=phrase,
                catalog_phrase=phrase,
                catalog_version=self.version,
                core=core,
                emotion=emotion,
                action_name=action_name,
                behavior=behavior,
                source_rows=source_rows,
                slots=slots,
            )
            loaded += 1

        if loaded:
            self.command_count += 1
            if core:
                self.core_command_count += 1
