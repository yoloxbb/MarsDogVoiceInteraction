"""Whole-utterance fetch/location recognition; navigation belongs downstream."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml

from marsdog_voice_interaction.core.command_lexicon import normalize_command_phrase
from marsdog_voice_interaction.core.object_target_resolver import ObjectTargetResolver
from marsdog_voice_interaction.messages.voice_event_types import (
    EVT_VOICE_COMMAND_FETCH,
    EVT_VOICE_COMMAND_GO_TO_LOCATION,
    EVT_VOICE_COMMAND_FIND_PERSON,
    EVT_VOICE_COMMAND_FETCH_FROM_LOCATION,
)


_PREFIX = r"(?:(?:小狗|狗狗|宝贝|小宝贝))?(?:(?:请你|请|麻烦你|麻烦))?(?:帮我|给我)?"
_FETCH_PATTERNS = tuple(re.compile(_PREFIX + pattern + r"(?:吧|呀|啊)?$") for pattern in (
    r"(?:去)?(?:拿|取|叼|捡)(?:一下)?(?P<target>.+?)(?:拿给我|给我|过来|回来|来)?",
    r"把(?P<target>.+?)(?:给我)?(?:拿|取|叼|捡)(?:给我|过来|回来|来)",
    r"(?:去)?(?:找找|寻找|找)(?:一下)?(?P<target>.+?)(?:拿给我|给我|过来|回来|来)?",
))
_LOCATION = re.compile(_PREFIX + r"(?:去|到|回)(?P<target>.+?)(?:去)?(?:吧|呀|啊)?$")
_FETCH_FROM_LOCATION = re.compile(
    _PREFIX + r"(?:去|到|回)(?!拿|取|叼|捡|找)(?P<location>.+?)(?:去)?"
    r"(?P<fetch>(?:去)?(?:拿|取|叼|捡|找).+?)(?:吧|呀|啊)?$")
_FIND_PERSON = re.compile(_PREFIX + r"去找(?P<target>.+?)(?:吧|呀|啊)?$")
_DEMONSTRATIVE = re.compile(r"^(?:那个|这个|那只|这只|那件|这件|一个|一只)")
_NEGATION = re.compile(r"不要|不准|不许|不能|不可以|别|不用|不想")
# Semantic groups are downstream search requests, never detector class names.
_OBJECT_GROUPS = {"玩具": "toy"}


class ParameterizedCommandRouter:
    """Recognize reviewed sentence templates independently of Model Intent.

    Known places are semantic IDs, never map coordinates. Unsupported nouns
    are reported with their original mention and no execution authority.
    """

    def __init__(self, objects: ObjectTargetResolver | None, locations_path: str | Path,
                 persons_path: str | Path | None = None):
        self.objects = objects
        self.locations_path = Path(locations_path).expanduser().resolve()
        raw = yaml.safe_load(self.locations_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not str(raw.get("version", "")).strip():
            raise ValueError("Location catalog requires a version")
        self.version = str(raw["version"])
        locations = raw.get("locations")
        if not isinstance(locations, list) or not locations:
            raise ValueError("Location catalog requires non-empty locations")
        self._locations: dict[str, tuple[str, str]] = {}
        seen_ids: set[str] = set()
        for item in locations:
            if not isinstance(item, dict):
                raise ValueError("Location entries must be mappings")
            location_id = str(item.get("location_id", "")).strip()
            name = str(item.get("name", "")).strip()
            aliases = item.get("aliases", [])
            if (not re.fullmatch(r"[a-z][a-z0-9_]*", location_id)
                    or location_id in seen_ids or not name or not isinstance(aliases, list)):
                raise ValueError("Location requires a unique ID, name and alias list")
            seen_ids.add(location_id)
            for alias in [name, *aliases]:
                normalized = normalize_command_phrase(alias)
                if not normalized:
                    raise ValueError("Location aliases must be non-empty strings")
                previous = self._locations.get(normalized)
                if previous is not None and previous[0] != location_id:
                    raise ValueError(f"Ambiguous location alias: {alias}")
                self._locations[normalized] = (location_id, name)

        # IDs must be supplied by the identity owner, never inferred from a
        # family role or fabricated from the spoken name.
        self._persons: dict[str, tuple[str, str]] = {}
        if persons_path:
            people = yaml.safe_load(Path(persons_path).read_text(encoding="utf-8"))
            if (not isinstance(people, dict) or not people.get("version")
                    or not isinstance(people.get("persons"), list)):
                raise ValueError("Person catalog requires version and persons list")
            seen_person_ids: set[str] = set()
            for person in people["persons"]:
                if not isinstance(person, dict):
                    raise ValueError("Person entries must be mappings")
                identifier = person.get("person_id", "")
                if (not isinstance(identifier, str) or not identifier.strip()
                        or identifier.strip().lower() in {"none", "unknown"}):
                    raise ValueError("Person requires a registered, non-placeholder ID")
                identifier = identifier.strip()
                name = str(person.get("name", "")).strip()
                aliases = person.get("aliases", [])
                if (not identifier or identifier in seen_person_ids or not name
                        or not isinstance(aliases, list)):
                    raise ValueError("Person requires unique ID, name and alias list")
                seen_person_ids.add(identifier)
                for alias in [name, *aliases]:
                    normalized = normalize_command_phrase(alias)
                    if not normalized:
                        raise ValueError("Person aliases must be non-empty strings")
                    previous = self._persons.get(normalized)
                    if previous and previous[0] != identifier:
                        raise ValueError(f"Ambiguous person alias: {alias}")
                    if (normalized in _OBJECT_GROUPS
                            or self.objects and self.objects.resolve_exact(normalized)):
                        raise ValueError(f"Person alias conflicts with object: {alias}")
                    self._persons[normalized] = (identifier, name)

    def route(self, text: str, language: str = "zh") -> dict[str, Any] | None:
        normalized = normalize_command_phrase(text)
        if (not normalized or _NEGATION.search(normalized)
                or normalized.endswith(("吗", "呢", "么", "嘛", "哪里", "哪儿"))):
            return None
        combined = _FETCH_FROM_LOCATION.fullmatch(normalized)
        if combined:
            fetch = next((m for p in _FETCH_PATTERNS
                          if (m := p.fullmatch(combined.group("fetch")))), None)
            if fetch:
                location_mention = combined.group("location")
                location = self._locations.get(location_mention)
                mention = _DEMONSTRATIVE.sub("", fetch.group("target"))
                object_slots, object_supported = self._resolve_object(mention)
                slots = object_slots + self._location_slots(location_mention, location)
                if not object_supported and location is None:
                    slots = [s for s in slots if s["key"] != "reason"]
                    slots.append({"key": "reason", "value": "unsupported_object_and_location"})
                return self._event(text, language, "FETCH_FROM_LOCATION", "CMD_FETCH_FROM_LOCATION",
                                   EVT_VOICE_COMMAND_FETCH_FROM_LOCATION, slots,
                                   object_supported and location is not None, 45)
        person_match = _FIND_PERSON.fullmatch(normalized)
        if person_match:
            mention = person_match.group("target")
            person = self._persons.get(mention)
            # Reviewed object aliases remain FETCH, including 找狗绳来.
            fetch_match = next((m for p in _FETCH_PATTERNS
                                if (m := p.fullmatch(normalized))), None)
            object_mention = (_DEMONSTRATIVE.sub("", fetch_match.group("target"))
                              if fetch_match else mention)
            is_object = (object_mention in _OBJECT_GROUPS or object_mention == "咬绳"
                         or self.objects and self.objects.resolve_exact(object_mention))
            if person or (not is_object and not mention.endswith(("来", "回来", "过来"))):
                slots = [
                    {"key": "person_id", "value": person[0] if person else "NONE"},
                    {"key": "person_name", "value": person[1] if person else mention},
                    {"key": "person_mention", "value": mention},
                ]
                if not person:
                    slots.append({"key": "reason", "value": "unregistered_person"})
                return self._event(text, language, "FIND_PERSON", "CMD_FIND_PERSON",
                                   EVT_VOICE_COMMAND_FIND_PERSON, slots, person is not None, 43)
        for pattern in _FETCH_PATTERNS:
            match = pattern.fullmatch(normalized)
            if match is None:
                continue
            mention = _DEMONSTRATIVE.sub("", match.group("target"))
            slots, supported = self._resolve_object(mention)
            return self._event(text, language, "FETCH", "CMD_FETCH_OBJECT",
                               EVT_VOICE_COMMAND_FETCH, slots, supported, 42)
        match = _LOCATION.fullmatch(normalized)
        if match is None:
            return None
        mention = match.group("target")
        location = self._locations.get(mention)
        slots = self._location_slots(mention, location)
        return self._event(text, language, "GO_TO_LOCATION", "CMD_GO_TO_LOCATION",
                           EVT_VOICE_COMMAND_GO_TO_LOCATION, slots, location is not None, 44)

    def _resolve_object(self, mention: str) -> tuple[list[dict[str, str]], bool]:
        target = self.objects.resolve_exact(mention) if self.objects else None
        if target:
            return target.to_slots(), True
        group = _OBJECT_GROUPS.get(mention)
        if group:
            return [
                {"key": "object_name", "value": "NONE"},
                {"key": "object_mention", "value": mention},
                {"key": "object_group", "value": group},
                {"key": "object_match_source", "value": "semantic_group"},
            ], True
        return [
            {"key": "object_name", "value": "NONE"},
            {"key": "object_mention", "value": mention},
            {"key": "object_match_source", "value": "unsupported"},
            {"key": "reason", "value": "unsupported_object"},
        ], False

    def _location_slots(self, mention, location):
        slots = [
            {"key": "location_id", "value": location[0] if location else "NONE"},
            {"key": "location_name", "value": location[1] if location else mention},
            {"key": "location_mention", "value": mention},
            {"key": "location_catalog_version", "value": self.version},
        ]
        if location is None:
            slots.append({"key": "reason", "value": "unsupported_location"})
        return slots

    def _event(self, text: str, language: str, command: str, command_id: str,
               event_type: str, slots: list[dict[str, str]], supported: bool,
               source_row: int) -> dict[str, Any]:
        return {
            "event_type": event_type, "specific_event_type": event_type,
            "asr_text": text, "language": language,
            "social": "NONE", "emotion": "NONE", "intent": command,
            "action": command, "control": "DO", "command_id": command_id,
            "intent_category": "command", "intent_source": "parameterized_command",
            "intent_confidence": 1.0,
            "dispatch_role": "specific_command" if supported else "recognition_summary",
            "is_executable": supported, "should_trigger_behavior_tree": supported,
            "slots": slots + [
                {"key": "command_key", "value": command},
                {"key": "match_strategy", "value": "sentence_template"},
                {"key": "catalog_source_rows", "value": str(source_row)},
            ],
        }
