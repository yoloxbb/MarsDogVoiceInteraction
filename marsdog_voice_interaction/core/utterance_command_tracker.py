"""Track deferred KWS candidates during one utterance."""

from __future__ import annotations


class UtteranceCommandTracker:
    """Cache unique KWS candidates until ASR arbitration is complete."""

    def __init__(self) -> None:
        self._utterance_id = ""
        self._kws_candidates: dict[str, dict[str, object]] = {}

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

    def begin(self, utterance_id: str) -> None:
        self._utterance_id = str(utterance_id)
        self._kws_candidates.clear()

    def record_kws_candidate(self, event: dict[str, object]) -> bool:
        """Record one unique candidate and return whether it is new."""

        event_type = str(event.get("event_type", "")).strip()
        if not self.is_active or not event_type:
            return False
        if event_type in self._kws_candidates:
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
