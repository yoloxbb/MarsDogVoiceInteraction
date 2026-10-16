from __future__ import annotations


class UtteranceCommandTracker:
    """记录同一句的 KWS 候选及已经发布的事件。"""

    def __init__(self) -> None:
        self._utterance_id = ""
        self._kws_candidates: dict[str, dict[str, object]] = {}
        self._published_event_types: dict[str, None] = {}
        self._asr_complete = False
        self._kws_block_reason = ""

    @property
    def utterance_id(self) -> str:
        return self._utterance_id

    @property
    def is_active(self) -> bool:
        return bool(self._utterance_id)

    @property
    def kws_candidates(self) -> tuple[dict[str, object], ...]:
        """Return copies of candidates in first-detection order."""

        return tuple(dict(event) for event in self._kws_candidates.values())

    @property
    def kws_candidate_count(self) -> int:
        return len(self._kws_candidates)

    @property
    def published_event_types(self) -> tuple[str, ...]:
        return tuple(self._published_event_types)

    @property
    def asr_business_allowed(self) -> bool:
        return not self._published_event_types and not self._kws_block_reason

    @property
    def kws_block_reason(self) -> str:
        return self._kws_block_reason

    def block_kws(self, reason: str) -> None:
        if self.is_active:
            self._kws_block_reason = str(reason)

    @property
    def awaiting_asr(self) -> bool:
        return self.is_active and not self._asr_complete

    def mark_asr_complete(self) -> None:
        self._asr_complete = True

    def begin(self, utterance_id: str) -> None:
        self._utterance_id = str(utterance_id)
        self._kws_candidates.clear()
        self._published_event_types.clear()
        self._asr_complete = False
        self._kws_block_reason = ""

    def mark_kws_published(self, event_type: str) -> bool:
        if self._kws_block_reason or not self.is_active or event_type not in self._kws_candidates:
            return False
        if event_type in self._published_event_types:
            return False
        self._published_event_types[event_type] = None
        return True

    def record_kws_candidate(self, event: dict[str, object], *, replace: bool = False) -> bool:
        """Record a candidate, optionally update evidence; return whether new."""

        event_type = str(event.get("event_type", "")).strip()
        if not self.is_active or not event_type:
            return False
        if event_type in self._kws_candidates:
            if replace:
                self._kws_candidates[event_type] = dict(event)
            return False
        self._kws_candidates[event_type] = dict(event)
        return True

    def single_kws_candidate(self) -> dict[str, object] | None:
        """Return the sole candidate, or ``None`` when zero/multiple exist."""

        if len(self._kws_candidates) != 1:
            return None
        return dict(next(iter(self._kws_candidates.values())))

    def finish(self) -> None:
        self._utterance_id = ""
        self._kws_candidates.clear()
        self._published_event_types.clear()
        self._kws_block_reason = ""
