"""Tests for the adaptive throttle."""

from __future__ import annotations

import pytest

from custom_components.homewizard_realtime_push.throttle import (
    CLEAN_PERIOD,
    LEVELS,
    SLACK,
    WINDOW,
    Reason,
    Throttle,
)


def feed_frames(throttle: Throttle[int], start: float, count: int, gap: float) -> float:
    """Offer frames at a fixed gap and return the time of the last one."""
    now = start
    for i in range(count):
        now = start + i * gap
        throttle.offer(i, now)
    return now


def test_rejects_unknown_floor() -> None:
    with pytest.raises(ValueError, match="Unsupported interval"):
        Throttle[int](4.0, now=0)


def test_initial_state() -> None:
    throttle = Throttle[int](2.0, now=0)
    assert throttle.interval == 2.0
    assert throttle.floor == 2.0
    assert throttle.reason is Reason.CONFIGURED
    assert not throttle.publish_on_arrival
    assert throttle.frame_period is None
    assert throttle.loop_lag is None


def test_realtime_publishes_on_arrival() -> None:
    throttle = Throttle[int](1.0, now=0)
    assert throttle.offer(1, 0.0) is True
    assert throttle.take(0.0) == 1


def test_take_returns_latest_frame_once() -> None:
    throttle = Throttle[int](2.0, now=0)
    assert throttle.offer(1, 0.5) is False
    assert throttle.offer(2, 1.0) is False
    assert throttle.take(1.0) == 2
    assert throttle.take(1.0) is None


def test_publishes_the_frame_that_arrives_when_the_interval_is_up() -> None:
    throttle = Throttle[int](2.0, now=0)
    assert throttle.offer(1, 1.0) is False
    assert throttle.offer(2, 2.0) is True
    throttle.take(2.0)
    # The interval counts from the last publish, so it follows the telegrams.
    assert throttle.offer(3, 3.0) is False
    assert throttle.offer(4, 4.0) is True


def test_slightly_early_frame_does_not_wait_a_telegram() -> None:
    throttle = Throttle[int](2.0, now=0)
    assert throttle.offer(1, 2.0 - SLACK - 0.01) is False
    assert throttle.offer(2, 2.0 - SLACK) is True


def test_deadline_for_held_frame() -> None:
    throttle = Throttle[int](3.0, now=0)
    assert throttle.deadline is None
    throttle.offer(1, 1.0)
    assert throttle.deadline == 3.0 + SLACK
    throttle.take(3.5)
    assert throttle.deadline is None
    throttle.offer(2, 4.0)
    assert throttle.deadline == 3.5 + 3.0 + SLACK


def test_discard_drops_held_frame() -> None:
    throttle = Throttle[int](2.0, now=0)
    throttle.offer(1, 1.0)
    throttle.discard()
    assert throttle.deadline is None
    assert throttle.take(1.0) is None
    # Discarding is not publishing: the next frame is still due on time.
    assert throttle.offer(2, 2.0) is True


def test_steps_up_on_loop_lag_after_full_window() -> None:
    throttle = Throttle[int](1.0, now=0)
    for t in range(1, 10):
        throttle.record_lag(0.5, t)
        assert throttle.evaluate(t) is False
    throttle.record_lag(0.5, WINDOW)
    assert throttle.evaluate(WINDOW) is True
    assert throttle.interval == 2.0
    assert throttle.reason is Reason.LOOP_LAG
    assert not throttle.publish_on_arrival


def test_small_lag_does_not_step_up() -> None:
    throttle = Throttle[int](1.0, now=0)
    for t in range(1, 30):
        throttle.record_lag(0.1, t)
        assert throttle.evaluate(t) is False
    assert throttle.interval == 1.0


def test_negative_lag_is_clamped() -> None:
    throttle = Throttle[int](1.0, now=0)
    throttle.record_lag(-0.5, 1)
    assert throttle.loop_lag == 0.0


def test_single_lag_sample_is_not_enough() -> None:
    throttle = Throttle[int](3.0, now=0)
    throttle.record_lag(1.0, 10)
    assert throttle.evaluate(10) is False


def test_old_lag_samples_leave_the_window() -> None:
    throttle = Throttle[int](1.0, now=0)
    throttle.record_lag(1.0, 1)
    throttle.record_lag(1.0, 2)
    assert throttle.evaluate(15) is False
    assert throttle.loop_lag is None


def test_steps_up_on_reconnect() -> None:
    throttle = Throttle[int](2.0, now=0)
    throttle.record_reconnect(12)
    assert throttle.evaluate(12) is True
    assert throttle.interval == 3.0
    assert throttle.reason is Reason.RECONNECT


def test_reconnect_inside_first_window_waits() -> None:
    throttle = Throttle[int](2.0, now=0)
    throttle.record_reconnect(3)
    assert throttle.evaluate(3) is False
    assert throttle.evaluate(WINDOW) is True
    assert throttle.interval == 3.0


def test_steps_up_on_late_frames() -> None:
    throttle = Throttle[int](1.0, now=0)
    now = feed_frames(throttle, 0.0, 10, 1.0)
    assert throttle.frame_period == 1.0
    throttle.offer(10, now + 3)
    assert throttle.evaluate(now + 3) is False
    throttle.offer(11, now + 6)
    assert throttle.evaluate(now + 6) is True
    assert throttle.reason is Reason.LATE_FRAMES


def test_reconnect_gap_is_not_a_late_frame() -> None:
    throttle = Throttle[int](1.0, now=0)
    now = feed_frames(throttle, 0.0, 10, 1.0)
    throttle.record_reconnect(now)
    throttle.offer(10, now + 20)
    throttle.offer(11, now + 21)
    # Only the reconnect counts, not the outage as a late frame.
    assert not throttle._late


def test_slow_meter_frames_are_not_late() -> None:
    throttle = Throttle[int](1.0, now=0)
    now = feed_frames(throttle, 0.0, 10, 10.0)
    for t in (now + 10, now + 20, now + 30):
        throttle.offer(0, t)
        assert throttle.evaluate(t) is False
    assert throttle.frame_period == 10.0


def test_steps_down_after_clean_period() -> None:
    throttle = Throttle[int](1.0, now=0)
    throttle.record_reconnect(10)
    assert throttle.evaluate(10) is True
    assert throttle.interval == 2.0

    assert throttle.evaluate(10 + CLEAN_PERIOD - 1) is False
    assert throttle.evaluate(10 + CLEAN_PERIOD) is True
    assert throttle.interval == 1.0
    assert throttle.reason is Reason.CONFIGURED


def test_hysteresis_restarts_after_trouble() -> None:
    throttle = Throttle[int](1.0, now=0)
    throttle.record_reconnect(10)
    assert throttle.evaluate(10) is True

    # Trouble at the new level that is too young to step up again.
    throttle.record_reconnect(15)
    assert throttle.evaluate(15) is False
    # A clean period counts from the last trouble, not from the step up.
    assert throttle.evaluate(10 + CLEAN_PERIOD) is False
    assert throttle.evaluate(30 + CLEAN_PERIOD) is True


def test_step_down_keeps_reason_until_floor() -> None:
    throttle = Throttle[int](1.0, now=0)
    now = 0.0
    for _ in range(2):
        now += WINDOW
        throttle.record_reconnect(now)
        assert throttle.evaluate(now) is True
    assert throttle.interval == 3.0

    now += CLEAN_PERIOD
    assert throttle.evaluate(now) is True
    assert throttle.interval == 2.0
    assert throttle.reason is Reason.RECONNECT


def test_never_exceeds_top_level() -> None:
    throttle = Throttle[int](1.0, now=0)
    now = 0.0
    for _ in range(10):
        now += WINDOW
        throttle.record_reconnect(now)
        throttle.evaluate(now)
    assert throttle.interval == LEVELS[-1]


def test_never_goes_below_floor() -> None:
    throttle = Throttle[int](3.0, now=0)
    assert throttle.evaluate(CLEAN_PERIOD * 5) is False
    assert throttle.interval == 3.0


@pytest.mark.parametrize("floor", LEVELS)
def test_every_level_is_a_valid_floor(floor: float) -> None:
    assert Throttle[int](floor, now=0).interval == floor
