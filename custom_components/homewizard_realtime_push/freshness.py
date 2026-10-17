"""Measure how old the published values are.

Pure Python on purpose: no Home Assistant imports, so it can be unit-tested
directly. Times are monotonic seconds passed in by the caller.
"""

from __future__ import annotations

from collections import deque
from statistics import median
from typing import Any

DELAY_SAMPLES = 60


class Freshness:
    """Track the age of the published value, counted from its arrival.

    The age at a moment is how long ago the frame on display arrived in
    Home Assistant. It grows from the age at publish until the next publish,
    so the time-weighted average is what an automation sees on average.
    """

    def __init__(self) -> None:
        """Start without samples."""
        self._last_publish: float | None = None
        self._age_at_publish = 0.0
        self._age_area = 0.0
        self._time = 0.0
        self._max_age = 0.0
        self._publishes = 0
        self._held = 0
        self._delays: deque[float] = deque(maxlen=DELAY_SAMPLES)

    def published(self, now: float, arrived: float, held: bool) -> None:
        """Record a publish of a frame that arrived at `arrived`."""
        if self._last_publish is not None:
            span = now - self._last_publish
            self._age_area += self._age_at_publish * span + span * span / 2
            self._time += span
            self._max_age = max(self._max_age, self._age_at_publish + span)
        self._last_publish = now
        self._age_at_publish = now - arrived
        self._publishes += 1
        if held:
            self._held += 1

    def interrupted(self) -> None:
        """Stop counting until the next publish; an outage has no age."""
        self._last_publish = None

    def telegram_delay(self, seconds: float) -> None:
        """Record arrival time minus the telegram's own timestamp."""
        self._delays.append(seconds)

    def as_dict(self) -> dict[str, Any]:
        """Return a summary for diagnostics."""
        return {
            "average_age_s": (
                round(self._age_area / self._time, 3) if self._time else None
            ),
            "max_age_s": round(self._max_age, 3) if self._time else None,
            "publishes": self._publishes,
            "held_publishes": self._held,
            # Includes any offset of the meter's clock; telegram times have
            # whole-second resolution.
            "telegram_delay_s": (
                round(median(self._delays), 3) if self._delays else None
            ),
        }
