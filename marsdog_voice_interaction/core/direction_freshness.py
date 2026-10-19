"""Refresh internal bearing timestamps without resetting their measured age."""

from __future__ import annotations

import math
from typing import Any


def refresh_direction_age(event: dict[str, Any], now: float) -> dict[str, Any]:
    """Age tracked bearings to ``now``; expired bearings can never become valid."""
    value = dict(event)
    for field in ("wake_angle", "doa"):
        measured_key = f"_{field}_measured_monotonic"
        if measured_key not in value:
            continue
        measured = value[measured_key]
        limit = value.get(f"_{field}_max_age_ms")
        valid_time = (
            type(measured) in (int, float) and type(limit) in (int, float)
            and math.isfinite(measured) and math.isfinite(limit) and limit > 0
            and measured <= now + 0.2
        )
        age_ms = round(max(0.0, now - measured) * 1000.0, 6) if valid_time else -1.0
        if not math.isfinite(age_ms):
            age_ms = -1.0
        value[f"{field}_age_ms"] = age_ms
        if age_ms < 0 or age_ms > limit:
            value[field] = -1.0
            value[f"{field}_valid"] = False
    return value
