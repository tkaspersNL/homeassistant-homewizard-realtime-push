"""Adaptive publish rate for incoming measurement frames.

Pure Python on purpose: no Home Assistant imports, so it can be unit-tested
directly. All times are monotonic seconds passed in by the caller.
"""

from __future__ import annotations

from collections import deque
from enum import StrEnum
from statistics import median

# Publish interval levels in seconds. The lowest level publishes every frame
# on arrival; telegrams on a DSMR 5 meter arrive about once a second.
LEVELS: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0)

WINDOW = 10.0
LAG_THRESHOLD = 0.25
CLEAN_PERIOD = 60.0
# A frame is late when its gap is this many times the usual telegram period.
LATE_FACTOR = 2.0
# One late frame is normal Wi-Fi jitter; more than that in a window is not.
LATE_FRAMES_TRIGGER = 2
MIN_LAG_SAMPLES = 2
PERIOD_SAMPLES = 20
MIN_PERIOD_SAMPLES = 5


class Reason(StrEnum):
    """Why the effective interval is what it is."""

    CONFIGURED = "configured"
    LOOP_LAG = "loop_lag"
    LATE_FRAMES = "late_frames"
    RECONNECT = "reconnect"


class Throttle[T]:
    """Keep the latest frame and decide how often it gets published.

    Steps up one level when Home Assistant lags, frames arrive late or the
    connection drops, and steps back down after a clean period. It never goes
    below the configured floor and never averages: the latest frame wins.
    """

    def __init__(self, floor: float, now: float) -> None:
        """Start at the configured floor."""
        if floor not in LEVELS:
            raise ValueError(f"Unsupported interval: {floor}")
        self._floor = LEVELS.index(floor)
        self._level = self._floor
        self._reason = Reason.CONFIGURED
        self._last_change = now
        self._last_trouble = float("-inf")

        self._pending: T | None = None
        self._last_frame: float | None = None
        self._gaps: deque[float] = deque(maxlen=PERIOD_SAMPLES)

        self._lags: deque[tuple[float, float]] = deque()
        self._late: deque[float] = deque()
        self._reconnects: deque[float] = deque()

    @property
    def interval(self) -> float:
        """Return the effective publish interval in seconds."""
        return LEVELS[self._level]

    @property
    def floor(self) -> float:
        """Return the configured interval in seconds."""
        return LEVELS[self._floor]

    @property
    def reason(self) -> Reason:
        """Return why the interval is at its current level."""
        return self._reason

    @property
    def publish_on_arrival(self) -> bool:
        """Return True when every frame is published as it arrives."""
        return self._level == 0

    @property
    def frame_period(self) -> float | None:
        """Return the usual time between frames, once there are enough samples."""
        if len(self._gaps) < MIN_PERIOD_SAMPLES:
            return None
        return median(self._gaps)

    @property
    def loop_lag(self) -> float | None:
        """Return the median timer lag in the current window."""
        if not self._lags:
            return None
        return median(lag for _, lag in self._lags)

    def offer(self, frame: T, now: float) -> bool:
        """Store a new frame. Return True when it should be published now."""
        self._track_frame(now)
        self._pending = frame
        return self.publish_on_arrival

    def take(self) -> T | None:
        """Return the latest unpublished frame, if any, and clear it."""
        frame, self._pending = self._pending, None
        return frame

    def record_lag(self, lag: float, now: float) -> None:
        """Record how late a scheduled tick ran."""
        self._lags.append((now, max(0.0, lag)))

    def record_reconnect(self, now: float) -> None:
        """Record a dropped connection."""
        self._reconnects.append(now)
        # The outage itself is not a telegram gap.
        self._last_frame = None

    def evaluate(self, now: float) -> bool:
        """Step the level up or down. Return True when the interval changed."""
        self._prune(now)
        if (trouble := self._trouble()) is not None:
            self._last_trouble = now
            if now - self._last_change >= WINDOW and self._level < len(LEVELS) - 1:
                self._level += 1
                self._reason = trouble
                self._changed(now)
                return True
            return False

        quiet_since = max(self._last_trouble, self._last_change)
        if self._level > self._floor and now - quiet_since >= CLEAN_PERIOD:
            self._level -= 1
            if self._level == self._floor:
                self._reason = Reason.CONFIGURED
            self._changed(now)
            return True
        return False

    def _track_frame(self, now: float) -> None:
        if self._last_frame is not None:
            gap = now - self._last_frame
            period = self.frame_period
            if period is not None and gap > LATE_FACTOR * period:
                self._late.append(now)
            self._gaps.append(gap)
        self._last_frame = now

    def _trouble(self) -> Reason | None:
        if self._reconnects:
            return Reason.RECONNECT
        if len(self._late) >= LATE_FRAMES_TRIGGER:
            return Reason.LATE_FRAMES
        if len(self._lags) >= MIN_LAG_SAMPLES and (
            (lag := self.loop_lag) is not None and lag > LAG_THRESHOLD
        ):
            return Reason.LOOP_LAG
        return None

    def _prune(self, now: float) -> None:
        start = now - WINDOW
        while self._lags and self._lags[0][0] < start:
            self._lags.popleft()
        for times in (self._late, self._reconnects):
            while times and times[0] < start:
                times.popleft()

    def _changed(self, now: float) -> None:
        # Measure the new level on its own merits.
        self._last_change = now
        self._lags.clear()
        self._late.clear()
        self._reconnects.clear()
