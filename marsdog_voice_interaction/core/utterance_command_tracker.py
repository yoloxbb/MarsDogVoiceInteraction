"""Track command events emitted during one utterance."""

from __future__ import annotations


class UtteranceCommandTracker:
    """Remember immediate KWS events so the final intent is not duplicated."""

    def __init__(self) -> None:
        self._utterance_id = ""
        self._immediate_event_types: set[str] = set()

    @property
    def utterance_id(self) -> str:
        return self._utterance_id

    @property
    def is_active(self) -> bool:
        return bool(self._utterance_id)

    @property
    def immediate_event_types(self) -> frozenset[str]:
        return frozenset(self._immediate_event_types)

    def begin(self, utterance_id: str) -> None:
        self._utterance_id = str(utterance_id)
        self._immediate_event_types.clear()

    def record_immediate(self, event_type: str) -> bool:
        """Record an immediate event and return whether it is new."""
        value = str(event_type)
        if not self.is_active or not value:
            return False
        if value in self._immediate_event_types:
            return False
        self._immediate_event_types.add(value)
        return True

    def is_duplicate_final(self, event_type: str) -> bool:
        """Return whether the final intent matches an immediate KWS event."""
        return (
            self.is_active
            and bool(event_type)
            and str(event_type) in self._immediate_event_types
        )

    def finish(self) -> None:
        self._utterance_id = ""
        self._immediate_event_types.clear()
