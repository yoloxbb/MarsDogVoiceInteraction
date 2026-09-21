"""Deterministic ASR-text routing for catalog voice events."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Any

import yaml

from marsdog_voice_interaction.messages.intent_protocol import (
    COMMAND_KEY_TO_NLU,
    NLU_PROTOCOL,
    make_intent_tag,
)
from marsdog_voice_interaction.messages import voice_event_types


_TRAILING_AND_INLINE_PUNCTUATION = re.compile(
    r"""[，。！？、；：“”\"'（）【】《》…—～,.!?;:()\[\]<>/\s]+"""
)
_KNOWN_VOICE_EVENTS = {
    value
    for name, value in vars(voice_event_types).items()
    if name.startswith("EVT_VOICE_") and isinstance(value, str)
}
_CATALOG_SOCIAL_LABELS = frozenset({"NONE", "CALL", "PRAISE", "SCOLD"})


def normalize_command_phrase(value: str) -> str:
    """Normalize one controlled phrase for exact matching."""
    if not isinstance(value, str):
        return ""
    return _TRAILING_AND_INLINE_PUNCTUATION.sub("", value).strip().lower()


def _pinyin_syllables(text: str) -> tuple[str, ...]:
    """Return the tone-less pinyin syllables of ``text``.

    Used for homophone-tolerant matching. Degrades to an empty tuple when
    ``pypinyin`` is unavailable so exact matching still works.
    """
    if not text:
        return ()
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        return ()
    syllables = lazy_pinyin(text, style=Style.NORMAL)
    return tuple(
        str(syllable).strip().lower()
        for syllable in syllables
        if syllable and str(syllable).strip()
    )


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
    match_strategy: str = "catalog_exact"
    expansion_profile: str = ""
    expansion_rule: str = ""
    emit_known_event: bool = False
    nlu_social: str = ""
    nlu_intent: str = ""
    nlu_control: str = ""
    emotion: str = "NONE"
    action_name: str = ""
    behavior: str = ""
    source_rows: tuple[int, ...] = ()
    slots: tuple[tuple[str, str], ...] = ()

    def to_event(self, *, asr_text: str, language: str) -> dict[str, Any]:
        """Build a schema-v2 direct-command event payload.

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
            {"key": "match_strategy", "value": self.match_strategy},
        ])
        if self.expansion_profile:
            slots.append({
                "key": "expansion_profile",
                "value": self.expansion_profile,
            })
        if self.expansion_rule:
            slots.append({
                "key": "expansion_rule",
                "value": self.expansion_rule,
            })
        if self.action_name:
            slots.append({"key": "action_name", "value": self.action_name})
        if self.behavior:
            slots.append({"key": "behavior", "value": self.behavior})
        if self.source_rows:
            slots.append({
                "key": "catalog_source_rows",
                "value": ",".join(str(row) for row in self.source_rows),
            })
        is_social_event = self.emotion in {"CALL", "PRAISE", "SCOLD"}
        is_social_reaction = self.emotion in {"PRAISE", "SCOLD"}
        is_executable = self.control == "DO" and not is_social_event
        triggers_behavior_tree = is_executable or is_social_reaction
        if self.emotion == "PRAISE":
            intent_category = "praise"
        elif self.emotion == "SCOLD":
            intent_category = "blame"
        elif self.emotion == "CALL":
            intent_category = "social"
        elif triggers_behavior_tree:
            intent_category = "command"
        else:
            intent_category = "none"
        if self.nlu_social:
            social = self.nlu_social
            intent = self.nlu_intent
            semantic_control = self.nlu_control
        elif is_social_event:
            social = self.emotion
            intent = "NONE"
            semantic_control = "NONE"
        else:
            social = ""
            intent = ""
            semantic_control = self.control
        raw_nlu_tag = ""
        if social and intent and semantic_control:
            raw_nlu_tag = make_intent_tag(social, intent, semantic_control)
        return {
            "event_type": self.event_type,
            "asr_text": asr_text,
            "social": social,
            "intent": intent,
            "emotion": social or self.emotion,
            "action": "NONE" if is_social_event else self.command_key,
            "control": semantic_control,
            "command_id": self.command_id,
            "intent_category": intent_category,
            "intent_source": "command_lexicon",
            "intent_confidence": 1.0,
            "nlu_protocol": NLU_PROTOCOL if raw_nlu_tag else "",
            "raw_nlu_tag": raw_nlu_tag,
            "specific_event_type": self.event_type,
            "dispatch_role": (
                "social_reaction" if is_social_reaction
                else "specific_command"
            ),
            "slots": slots,
            "response_text": "",
            "is_executable": is_executable,
            "should_trigger_behavior_tree": triggers_behavior_tree,
            "language": language or "zh",
        }

    def to_known_event(
        self,
        *,
        asr_text: str,
        language: str,
        specific_dispatch: str,
        source: str = "command_lexicon",
        confidence: float = 1.0,
        matched_phrase: str | None = None,
        extra_slots: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Build the non-executable recognition summary for a core command."""

        if not self.emit_known_event:
            raise ValueError(
                f"command {self.command_key} does not emit a KNOWN summary"
            )
        raw_tag = make_intent_tag(
            self.nlu_social,
            self.nlu_intent,
            self.nlu_control,
        )
        slots = [
            {"key": "command_key", "value": self.command_key},
            {
                "key": "matched_phrase",
                "value": (
                    self.matched_phrase
                    if matched_phrase is None else str(matched_phrase)
                ),
            },
            {
                "key": "command_catalog_version",
                "value": self.catalog_version,
            },
            {"key": "catalog_phrase", "value": self.catalog_phrase},
            {"key": "match_strategy", "value": self.match_strategy},
            {"key": "specific_dispatch", "value": specific_dispatch},
        ]
        if self.expansion_profile:
            slots.append({
                "key": "expansion_profile",
                "value": self.expansion_profile,
            })
        if self.expansion_rule:
            slots.append({
                "key": "expansion_rule",
                "value": self.expansion_rule,
            })
        if extra_slots:
            slots.extend(dict(slot) for slot in extra_slots)
        return {
            "event_type": voice_event_types.EVT_VOICE_COMMAND_KNOWN,
            "asr_text": asr_text,
            "social": self.nlu_social,
            "intent": self.nlu_intent,
            "control": self.nlu_control,
            # Deprecated aliases retained for compatibility.
            "emotion": self.nlu_social,
            "action": self.command_key,
            "command_id": self.command_id,
            "intent_category": "command",
            "intent_source": source,
            "intent_confidence": float(confidence),
            "nlu_protocol": NLU_PROTOCOL,
            "raw_nlu_tag": raw_tag,
            "specific_event_type": self.event_type,
            "dispatch_role": "recognition_summary",
            "slots": slots,
            "response_text": "",
            "is_executable": False,
            "should_trigger_behavior_tree": False,
            "language": language or "zh",
        }


class CommandLexicon:
    """Load and match a versioned direct-command catalog.

    ``match`` performs exact hash lookup; ``match_fuzzy`` adds a
    homophone-tolerant fallback for common ASR homophone/typo errors.
    """

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
        self._catalog_phrases: list[DirectCommandMatch] = []
        self._commands: dict[str, DirectCommandMatch] = {}
        self._command_keys: set[str] = set()
        self._command_ids: set[str] = set()
        self._event_types: set[str] = set()
        self._source_rows: set[int] = set()
        self.command_count = 0
        self.core_command_count = 0
        self.reference_phrase_count = 0
        self.expansion_enabled = False
        self.variants_per_phrase = 0
        self.expanded_phrase_count = 0
        self.expansion_profile_count = 0
        # Natural-speech variants declared under the optional top-level
        # ``phrase_variants`` mapping.  They join the exact-match table but are
        # deliberately excluded from ``phrase_count``/``expanded_phrase_count``
        # so the reviewed product catalog contract stays intact.
        self.variant_phrase_count = 0
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
        self._load_expansions(raw.get("expansion"))
        self._load_phrase_variants(raw.get("phrase_variants"))

        self._homophones: dict[tuple[str, ...], list[str]] = {}
        for normalized in self._phrases:
            syllables = _pinyin_syllables(normalized)
            if syllables:
                self._homophones.setdefault(syllables, []).append(normalized)

    @property
    def phrase_count(self) -> int:
        """Number of authoritative phrases declared under ``commands``."""

        return len(self._catalog_phrases)

    @property
    def total_match_phrase_count(self) -> int:
        """Total exact lookup entries: standard phrases, expansions, variants."""

        return len(self._phrases)

    @property
    def covered_source_row_count(self) -> int:
        return len(self._source_rows)

    def match(self, text: str) -> DirectCommandMatch | None:
        normalized = normalize_command_phrase(text)
        template = self._phrases.get(normalized)
        if template is None:
            return None
        return replace(template, matched_phrase=text)

    def match_fuzzy(self, text: str) -> DirectCommandMatch | None:
        """Match exactly, then fall back to homophone matching.

        The fallback returns a catalog phrase whose tone-less pinyin equals
        the input's pinyin but whose characters differ — this rescues common
        ASR homophone/typo errors (e.g. ``坐虾`` → ``坐下``) while rejecting
        prefix/suffix edits such as ``不要坐下`` or ``你想不想吃``.

        Several catalog entries routinely share one tone-less pinyin (the
        ``zuo|xia`` and ``pa|xia`` families are the common ones for two-syllable
        commands), so an ambiguous group is resolved by :meth:`_resolve_ambiguous`
        instead of being rejected outright.  When that cannot decide safely the
        match is refused and ``None`` is returned.
        """
        exact = self.match(text)
        if exact is not None:
            return exact
        normalized = normalize_command_phrase(text)
        if not normalized:
            return None
        syllables = _pinyin_syllables(normalized)
        if not syllables:
            return None
        candidates = self._homophones.get(syllables)
        if not candidates:
            return None
        chosen = self._resolve_ambiguous(candidates)
        if chosen is None:
            return None
        template = self._phrases[chosen]
        return replace(
            template,
            matched_phrase=text,
            match_strategy="fuzzy_homophone",
        )

    def _resolve_ambiguous(self, candidates: list[str]) -> str | None:
        """Pick one phrase out of a same-pinyin group, or refuse.

        Only refusals are safe by default, so each rule below has to leave
        exactly one candidate standing:

        1. every candidate routes to the same command — the ambiguity is
           cosmetic because any of them dispatches identically;
        2. exactly one candidate is a core command;
        3. exactly one candidate is an authoritative catalog phrase (the rest
           being controlled expansions or declared variants).

        Otherwise the group is genuinely ambiguous and stays unmatched.
        """

        if len(candidates) == 1:
            return candidates[0]

        templates = [self._phrases[name] for name in candidates]

        command_keys = {item.command_key for item in templates}
        if len(command_keys) == 1:
            return self._prefer_authoritative(candidates, templates)

        core = [
            name for name, item in zip(candidates, templates) if item.core
        ]
        if len(core) == 1:
            return core[0]

        authoritative = [
            name
            for name, item in zip(candidates, templates)
            if item.match_strategy == "catalog_exact"
        ]
        if len(authoritative) == 1:
            return authoritative[0]

        return None

    @staticmethod
    def _prefer_authoritative(
        candidates: list[str],
        templates: list[DirectCommandMatch],
    ) -> str:
        """Deterministically pick one name out of an equivalent group."""

        order = {"catalog_exact": 0, "catalog_variant": 1, "rule_expansion": 2}
        return min(
            zip(candidates, templates),
            key=lambda pair: (
                order.get(pair[1].match_strategy, 3),
                len(pair[0]),
                pair[0],
            ),
        )[0]

    def get_command(self, command_key: str) -> DirectCommandMatch | None:
        """Return immutable catalog metadata for one canonical command key."""

        return self._commands.get(str(command_key).strip().upper())

    def _load_command(self, index: int, item: dict[str, Any]) -> None:
        command_key = str(item.get("command_key", "")).strip().upper()
        command_id = str(item.get("command_id", "")).strip().upper()
        event_type = str(item.get("event_type", "")).strip().upper()
        control = str(item.get("control", "DO")).strip().upper()
        core = bool(item.get("core", False))
        # Catalog/KWS hits are already concrete special-command events, so the
        # lexicon never adds the generic COMMAND_KNOWN summary.
        emit_known_event = False
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
        if event_type.startswith("EVT_VOICE_INTENT_"):
            raise ValueError(
                f"Command entry {index} uses retired INTENT event_type "
                f"{event_type!r}"
            )
        if (
            event_type
            in voice_event_types.MODEL_INTENT_CLASSIFICATION_EVENT_TYPES
            or event_type == voice_event_types.EVT_VOICE_WAKEUP
        ):
            raise ValueError(
                f"Command entry {index} reuses reserved non-catalog "
                f"event_type {event_type!r}"
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
        if command_id in self._command_ids:
            raise ValueError(f"Duplicate command_id {command_id!r}")
        if event_type in self._event_types:
            raise ValueError(f"Duplicate catalog event_type {event_type!r}")
        self._command_keys.add(command_key)
        self._command_ids.add(command_id)
        self._event_types.add(event_type)
        if control not in {"NONE", "DO"}:
            raise ValueError(
                f"Command entry {index} has invalid control {control!r}"
            )
        if emotion not in _CATALOG_SOCIAL_LABELS:
            raise ValueError(
                f"Command entry {index} has invalid emotion {emotion!r}"
            )
        if emotion != "NONE" and control != "NONE":
            raise ValueError(
                f"Command entry {index} social event {emotion!r} must use "
                "control NONE"
            )

        configured_nlu = item.get("nlu")
        if configured_nlu is None:
            configured_nlu = COMMAND_KEY_TO_NLU.get(command_key)
        if configured_nlu is None:
            nlu_social = nlu_intent = nlu_control = ""
        elif isinstance(configured_nlu, str):
            nlu_social, nlu_intent, nlu_control = make_intent_tag(
                *configured_nlu.split("|")
            ).split("|")
        elif isinstance(configured_nlu, (list, tuple)) and len(configured_nlu) == 3:
            nlu_social, nlu_intent, nlu_control = make_intent_tag(
                *(str(value) for value in configured_nlu)
            ).split("|")
        else:
            raise ValueError(
                f"Command entry {index} has invalid nlu triple {configured_nlu!r}"
            )
        if emit_known_event and not nlu_social:
            raise ValueError(
                f"Command entry {index} requires a valid nlu triple when "
                "emit_known_event is true"
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
            template = DirectCommandMatch(
                command_key=command_key,
                command_id=command_id,
                event_type=event_type,
                control=control,
                matched_phrase=phrase,
                catalog_phrase=phrase,
                catalog_version=self.version,
                core=core,
                emit_known_event=emit_known_event,
                nlu_social=nlu_social,
                nlu_intent=nlu_intent,
                nlu_control=nlu_control,
                emotion=emotion,
                action_name=action_name,
                behavior=behavior,
                source_rows=source_rows,
                slots=slots,
            )
            self._phrases[normalized] = template
            self._catalog_phrases.append(template)
            self._commands.setdefault(command_key, self._phrases[normalized])
            loaded += 1

        if loaded:
            self.command_count += 1
            if core:
                self.core_command_count += 1

    def _load_expansions(self, raw_expansion: Any) -> None:
        """Generate a finite, auditable set of exact-match surface variants."""

        if raw_expansion is None:
            return
        if not isinstance(raw_expansion, dict):
            raise ValueError("Command catalog expansion must be a mapping")
        self.expansion_enabled = bool(raw_expansion.get("enabled", False))
        if not self.expansion_enabled:
            return

        self.variants_per_phrase = int(
            raw_expansion.get("variants_per_phrase", 0) or 0
        )
        if self.variants_per_phrase <= 0:
            raise ValueError("expansion.variants_per_phrase must be positive")
        default_profile = str(
            raw_expansion.get("default_profile", "")
        ).strip()
        raw_profiles = raw_expansion.get("profiles")
        if not default_profile or not isinstance(raw_profiles, dict):
            raise ValueError(
                "expansion.default_profile and expansion.profiles are required"
            )

        profiles: dict[str, tuple[tuple[str, str], ...]] = {}
        for profile_name, raw_rules in raw_profiles.items():
            name = str(profile_name).strip()
            if not name or not isinstance(raw_rules, list):
                raise ValueError("Each expansion profile must be a rule list")
            rules: list[tuple[str, str]] = []
            rule_ids: set[str] = set()
            for rule in raw_rules:
                if not isinstance(rule, dict):
                    raise ValueError(
                        f"Expansion profile {name!r} contains a non-mapping rule"
                    )
                rule_id = str(rule.get("id", "")).strip()
                template = str(rule.get("template", "")).strip()
                if not rule_id or rule_id in rule_ids:
                    raise ValueError(
                        f"Expansion profile {name!r} has a missing/duplicate rule id"
                    )
                if template.count("{phrase}") != 1:
                    raise ValueError(
                        f"Expansion rule {name}.{rule_id} must contain one "
                        "{phrase} placeholder"
                    )
                rule_ids.add(rule_id)
                rules.append((rule_id, template))
            if len(rules) != self.variants_per_phrase:
                raise ValueError(
                    f"Expansion profile {name!r} must contain exactly "
                    f"{self.variants_per_phrase} rules"
                )
            profiles[name] = tuple(rules)
        if default_profile not in profiles:
            raise ValueError(
                f"Unknown expansion default_profile {default_profile!r}"
            )

        raw_command_profiles = raw_expansion.get("command_profiles", {})
        raw_phrase_profiles = raw_expansion.get("phrase_profiles", {})
        if not isinstance(raw_command_profiles, dict):
            raise ValueError("expansion.command_profiles must be a mapping")
        if not isinstance(raw_phrase_profiles, dict):
            raise ValueError("expansion.phrase_profiles must be a mapping")
        command_profiles = {
            str(key).strip().upper(): str(value).strip()
            for key, value in raw_command_profiles.items()
        }
        phrase_profiles = {
            normalize_command_phrase(str(key)): str(value).strip()
            for key, value in raw_phrase_profiles.items()
        }
        unknown_commands = sorted(set(command_profiles) - self._command_keys)
        if unknown_commands:
            raise ValueError(
                "Expansion profiles reference unknown command keys: "
                f"{unknown_commands}"
            )
        unknown_profiles = sorted(
            (set(command_profiles.values()) | set(phrase_profiles.values()))
            - set(profiles)
        )
        if unknown_profiles:
            raise ValueError(
                f"Expansion mappings reference unknown profiles: {unknown_profiles}"
            )
        catalog_phrase_keys = {
            normalize_command_phrase(item.catalog_phrase)
            for item in self._catalog_phrases
        }
        unknown_phrases = sorted(set(phrase_profiles) - catalog_phrase_keys)
        if unknown_phrases:
            raise ValueError(
                "Expansion profiles reference unknown catalog phrases: "
                f"{unknown_phrases}"
            )

        for base in self._catalog_phrases:
            normalized_base = normalize_command_phrase(base.catalog_phrase)
            profile = phrase_profiles.get(
                normalized_base,
                command_profiles.get(base.command_key, default_profile),
            )
            generated_for_phrase: set[str] = set()
            for rule_id, template in profiles[profile]:
                expanded_phrase = template.format(phrase=base.catalog_phrase)
                normalized = normalize_command_phrase(expanded_phrase)
                if not normalized or normalized == normalized_base:
                    raise ValueError(
                        f"Expansion rule {profile}.{rule_id} does not create a "
                        f"new phrase for {base.catalog_phrase!r}"
                    )
                if normalized in generated_for_phrase:
                    raise ValueError(
                        f"Expansion rules create duplicate variants for "
                        f"{base.catalog_phrase!r}: {expanded_phrase!r}"
                    )
                generated_for_phrase.add(normalized)
                existing = self._phrases.get(normalized)
                if existing is not None:
                    raise ValueError(
                        f"Expanded phrase {expanded_phrase!r} from "
                        f"{base.command_key}/{base.catalog_phrase!r} conflicts "
                        f"with {existing.command_key}/{existing.catalog_phrase!r}"
                    )
                self._phrases[normalized] = replace(
                    base,
                    matched_phrase=expanded_phrase,
                    match_strategy="rule_expansion",
                    expansion_profile=profile,
                    expansion_rule=rule_id,
                )
                self.expanded_phrase_count += 1

        expected = self.phrase_count * self.variants_per_phrase
        if self.expanded_phrase_count != expected:
            raise ValueError(
                "Expansion count mismatch: "
                f"expected={expected} actual={self.expanded_phrase_count}"
            )
        self.expansion_profile_count = len(profiles)

    def _load_phrase_variants(self, raw_variants: Any) -> None:
        """Load natural-speech variants declared under ``phrase_variants``.

        These are the surface forms people actually say but which the reviewed
        product catalog intentionally does not carry as standard phrases, for
        example ``往后退一点点`` for ``退后`` or ``去睡觉`` for ``睡觉``.

        Variants join the exact-match table (and therefore the homophone
        index) with ``match_strategy="catalog_variant"``.  They are kept out of
        ``_catalog_phrases`` so that ``phrase_count``/``expanded_phrase_count``
        keep describing the product catalog alone, and they never generate
        controlled expansions.
        """

        if raw_variants is None:
            return
        if not isinstance(raw_variants, dict):
            raise ValueError("Command catalog phrase_variants must be a mapping")

        unknown_commands = sorted(
            {str(key).strip().upper() for key in raw_variants}
            - self._command_keys
        )
        if unknown_commands:
            raise ValueError(
                "phrase_variants reference unknown command keys: "
                f"{unknown_commands}"
            )

        for raw_key, raw_phrases in raw_variants.items():
            command_key = str(raw_key).strip().upper()
            if not isinstance(raw_phrases, list) or not raw_phrases:
                raise ValueError(
                    f"phrase_variants.{command_key} must be a non-empty list"
                )
            template = self._commands[command_key]
            for raw_phrase in raw_phrases:
                phrase = str(raw_phrase).strip()
                normalized = normalize_command_phrase(phrase)
                if not normalized:
                    raise ValueError(
                        f"phrase_variants.{command_key} contains an empty phrase"
                    )
                existing = self._phrases.get(normalized)
                if existing is not None:
                    raise ValueError(
                        f"Variant {phrase!r} for {command_key} conflicts with "
                        f"{existing.command_key}/{existing.catalog_phrase!r} "
                        f"({existing.match_strategy})"
                    )
                self._phrases[normalized] = replace(
                    template,
                    matched_phrase=phrase,
                    match_strategy="catalog_variant",
                )
                self.variant_phrase_count += 1
