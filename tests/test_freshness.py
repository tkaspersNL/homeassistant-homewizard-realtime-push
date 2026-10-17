"""Tests for the freshness tracker."""

from __future__ import annotations

from custom_components.homewizard_realtime_push.freshness import Freshness


def test_empty() -> None:
    assert Freshness().as_dict() == {
        "average_age_s": None,
        "max_age_s": None,
        "publishes": 0,
        "held_publishes": 0,
        "telegram_delay_s": None,
    }


def test_publish_on_arrival_every_two_seconds() -> None:
    freshness = Freshness()
    for t in (0.0, 2.0, 4.0):
        freshness.published(t, arrived=t, held=False)
    summary = freshness.as_dict()
    # The age grows from 0 to 2s between publishes: 1s on average.
    assert summary["average_age_s"] == 1.0
    assert summary["max_age_s"] == 2.0
    assert summary["publishes"] == 3


def test_age_at_publish_counts() -> None:
    freshness = Freshness()
    freshness.published(0.0, arrived=-0.5, held=True)
    freshness.published(2.0, arrived=2.0, held=False)
    summary = freshness.as_dict()
    assert summary["average_age_s"] == 1.5
    assert summary["max_age_s"] == 2.5
    assert summary["held_publishes"] == 1


def test_outage_is_not_counted() -> None:
    freshness = Freshness()
    freshness.published(0.0, arrived=0.0, held=False)
    freshness.interrupted()
    freshness.published(100.0, arrived=100.0, held=False)
    freshness.published(102.0, arrived=102.0, held=False)
    summary = freshness.as_dict()
    assert summary["average_age_s"] == 1.0
    assert summary["max_age_s"] == 2.0


def test_telegram_delay_is_the_median() -> None:
    freshness = Freshness()
    for delay in (0.1, 0.2, 5.0):
        freshness.telegram_delay(delay)
    assert freshness.as_dict()["telegram_delay_s"] == 0.2
