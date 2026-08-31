"""Resolve an ASR object mention to a fixed downstream detector class."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml


_NORMALIZE_PUNCTUATION = re.compile(
    r"""[，。！？、；：“”\"'（）【】《》…—～,.!?;:()\[\]<>/\s]+"""
)
_OBJECT_MENTION_PATTERNS = (
    re.compile(
        r"^(?:请)?(?:帮我)?(?:看看|看一下|看下|找找|寻找|找|捡起|捡|拿起|拿|叼)"
        r"(?:一下)?(?:那个|这个|那只|这只|那件|这件|一个|一只)?"
        r"(?P<object>.+?)(?:在哪里|在哪儿|在哪|的位置|在什么地方|"
        r"找出来|找一下|拿给我|拿过来|捡起来)?$"
    ),
    re.compile(
        r"^(?:那个|这个|那只|这只|那件|这件)?(?P<object>.+?)"
        r"(?:在哪里|在哪儿|在哪|的位置|在什么地方)$"
    ),
)


def normalize_object_text(value: str) -> str:
    """Normalize ASR text and aliases for deterministic substring matching."""

    return _NORMALIZE_PUNCTUATION.sub("", str(value)).strip().lower()


@dataclass(frozen=True)
class ObjectTargetMatch:
    """One supported detector target found in ASR text."""

    object_name: str
    object_mention: str
    matched_alias: str
    catalog_version: str

    def to_slots(self) -> list[dict[str, str]]:
        return [
            {"key": "object_name", "value": self.object_name},
            {"key": "object_mention", "value": self.object_mention},
            {"key": "object_matched_alias", "value": self.matched_alias},
            {"key": "object_match_source", "value": "asr_rule"},
            {
                "key": "object_catalog_version",
                "value": self.catalog_version,
            },
        ]


class ObjectTargetResolver:
    """Load and match the fixed target vocabulary used by Vision/Tree."""

    def __init__(self, catalog_path: str | Path) -> None:
        self.catalog_path = Path(catalog_path).expanduser().resolve()
        with self.catalog_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
        if not isinstance(raw, dict):
            raise ValueError("Object target catalog must be a YAML mapping")

        self.version = str(raw.get("version", "")).strip()
        if not self.version:
            raise ValueError("Object target catalog version is required")
        targets = raw.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError("Object target catalog targets must be non-empty")

        canonical_names: set[str] = set()
        alias_routes: dict[str, tuple[str, str]] = {}
        for index, item in enumerate(targets, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Object target #{index} must be a mapping")
            object_name = str(item.get("name", "")).strip()
            if not object_name or object_name in canonical_names:
                raise ValueError(
                    f"Object target #{index} has an empty or duplicate name"
                )
            canonical_names.add(object_name)
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list):
                raise ValueError(
                    f"Object target {object_name} aliases must be a list"
                )
            for raw_alias in [object_name, *aliases]:
                alias = str(raw_alias).strip()
                normalized = normalize_object_text(alias)
                if not normalized:
                    continue
                previous = alias_routes.get(normalized)
                if previous is not None and previous[0] != object_name:
                    raise ValueError(
                        f"Object alias {alias!r} routes to both "
                        f"{previous[0]!r} and {object_name!r}"
                    )
                alias_routes[normalized] = (object_name, alias)

        self.target_names = tuple(sorted(canonical_names))
        self._alias_routes = tuple(
            sorted(
                (
                    (normalized, object_name, alias)
                    for normalized, (object_name, alias)
                    in alias_routes.items()
                ),
                key=lambda item: (-len(item[0]), item[0]),
            )
        )

    @property
    def target_count(self) -> int:
        return len(self.target_names)

    @property
    def alias_count(self) -> int:
        return len(self._alias_routes)

    def resolve(self, asr_text: str) -> ObjectTargetMatch | None:
        """Return the longest supported alias contained in the ASR text."""

        normalized_text = normalize_object_text(asr_text)
        if not normalized_text:
            return None
        ascii_text = re.sub(
            r"[^a-z0-9]+", " ", str(asr_text).lower()
        ).strip()
        for normalized_alias, object_name, alias in self._alias_routes:
            if normalized_alias.isascii() and normalized_alias.isalnum():
                ascii_alias = re.sub(
                    r"[^a-z0-9]+", " ", alias.lower()
                ).strip()
                matched = bool(
                    ascii_alias
                    and re.search(
                        rf"(?<![a-z0-9]){re.escape(ascii_alias)}"
                        rf"(?![a-z0-9])",
                        ascii_text,
                    )
                )
            else:
                matched = normalized_alias in normalized_text
            if matched:
                return ObjectTargetMatch(
                    object_name=object_name,
                    object_mention=alias,
                    matched_alias=alias,
                    catalog_version=self.version,
                )
        return None

    def unsupported_slots(self, asr_text: str) -> list[dict[str, str]]:
        """Build explicit NONE slots for an unsupported object mention."""

        mention = self.extract_mention(asr_text)
        return [
            {"key": "object_name", "value": "NONE"},
            {"key": "object_mention", "value": mention},
            {"key": "object_match_source", "value": "unsupported"},
            {"key": "object_catalog_version", "value": self.version},
        ]

    @staticmethod
    def extract_mention(asr_text: str) -> str:
        """Best-effort extraction used only for unsupported-target evidence."""

        text = str(asr_text).strip()
        text = re.sub(r"[，。！？、；：…—～,.!?;:\s]+$", "", text)
        for pattern in _OBJECT_MENTION_PATTERNS:
            match = pattern.match(text)
            if match:
                return match.group("object").strip()
        return text


def object_resolution_slots(
    resolver: ObjectTargetResolver | None,
    asr_text: str,
) -> tuple[list[dict[str, str]], bool]:
    """Return target slots and whether a supported canonical class matched."""

    if resolver is None:
        return [
            {"key": "object_name", "value": "NONE"},
            {"key": "object_mention", "value": str(asr_text).strip()},
            {"key": "object_match_source", "value": "unavailable"},
        ], False
    match = resolver.resolve(asr_text)
    if match is not None:
        return match.to_slots(), True
    return resolver.unsupported_slots(asr_text), False
