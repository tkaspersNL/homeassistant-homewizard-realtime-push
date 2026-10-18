"""Tests for the hub: publishing, staleness and throttle wiring."""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import timedelta
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from homewizard_energy.models import Device
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_realtime_push.client import Transport
from custom_components.homewizard_realtime_push.const import (
    CONF_UPDATE_SPEED,
    DOMAIN,
    ISSUE_AFTER,
    Issue,
    Problem,
    UpdateSpeed,
)
from custom_components.homewizard_realtime_push.coordinator import RealtimeHub
from custom_components.homewizard_realtime_push.throttle import CLEAN_PERIOD, WINDOW

from .conftest import DEVICE, make_measurement


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_hub(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    clock: Clock,
) -> Generator[Callable[..., RealtimeHub]]:
    hubs: list[RealtimeHub] = []

    def _make(speed: UpdateSpeed = UpdateSpeed.VERY_FAST) -> RealtimeHub:
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(
            mock_config_entry, options={CONF_UPDATE_SPEED: speed}
        )
        api = MagicMock()
        api.host = "p1.local"
        hub = RealtimeHub(
            hass,
            mock_config_entry,
            api,
            Device.from_dict(DEVICE),
            make_measurement(power_w=0),
            clock=clock,
        )
        hubs.append(hub)
        return hub

    yield _make
    for hub in hubs:
        hub.async_stop()


def counter(hub: RealtimeHub, status: bool = False) -> list[int]:
    calls = [0]

    def update() -> None:
        calls[0] += 1

    if status:
        hub.async_add_status_listener(update)
    else:
        hub.async_add_listener(update)
    return calls


def tick(hub: RealtimeHub, clock: Clock, seconds: float) -> None:
    """Advance the clock and run the timer as if it fired on time."""
    clock.now += seconds
    hub._tick(clock.now)


def frame(hub: RealtimeHub, clock: Clock, seconds: float, power: int) -> None:
    """Advance the clock and deliver a frame."""
    clock.now += seconds
    hub.on_measurement(make_measurement(power_w=power))


async def test_publishes_frame_on_arrival_once_per_interval(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    calls = counter(hub)

    frame(hub, clock, 0.5, 10)
    frame(hub, clock, 0.5, 20)
    assert calls[0] == 0
    assert hub.measurement.power_w == 0

    # Published the moment it arrives, so it is as fresh as the telegram.
    frame(hub, clock, 1, 30)
    assert calls[0] == 1
    assert hub.measurement.power_w == 30
    assert hub._hold is None

    # The timer does not publish.
    tick(hub, clock, 0.5)
    assert calls[0] == 1

    frame(hub, clock, 0.5, 40)
    frame(hub, clock, 1, 50)
    assert calls[0] == 2
    # The latest value, not an average.
    assert hub.measurement.power_w == 50


async def test_held_frame_is_published_when_the_next_one_is_late(
    hass: HomeAssistant, make_hub, clock
) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    calls = counter(hub)

    frame(hub, clock, 1, 10)
    assert calls[0] == 0
    assert hub._hold is not None
    delay = hub._hold.when() - hass.loop.time()
    assert 1.4 < delay <= 1.5

    # Fire the timer by hand.
    clock.now += 1.5
    hub._hold.cancel()
    hub._publish_held()
    assert calls[0] == 1
    assert hub.measurement.power_w == 10
    assert hub._hold is None

    # Nothing held: firing again publishes nothing.
    hub._publish_held()
    assert calls[0] == 1


async def test_due_frame_cancels_the_hold(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    frame(hub, clock, 1, 10)
    hold = hub._hold
    assert hold is not None

    frame(hub, clock, 1, 20)
    assert hold.cancelled()
    assert hub._hold is None
    assert hub.measurement.power_w == 20


async def test_unavailable_drops_the_held_frame(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    frame(hub, clock, 1, 10)
    hold = hub._hold
    assert hold is not None

    hub._set_unavailable(clock.now)
    assert hold.cancelled()
    assert hub.throttle.deadline is None

    frame(hub, clock, 0.1, 20)
    assert hub.available is True
    assert hub.measurement.power_w == 20


async def test_stop_cancels_the_hold(hass: HomeAssistant, make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    frame(hub, clock, 1, 10)
    hold = hub._hold
    assert hold is not None
    hub._async_hass_stopping(MagicMock())
    assert hold.cancelled()

    hub = make_hub(UpdateSpeed.VERY_FAST)
    frame(hub, clock, 1, 10)
    hold = hub._hold
    assert hold is not None
    hub.async_stop()
    assert hold.cancelled()


async def test_realtime_publishes_on_arrival(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.REALTIME)
    calls = counter(hub)

    hub.on_measurement(make_measurement(power_w=5))
    assert calls[0] == 1
    assert hub.measurement.power_w == 5

    tick(hub, clock, 1)
    assert calls[0] == 1


async def test_goes_unavailable_when_frames_stop_and_recovers(
    make_hub, clock, caplog: pytest.LogCaptureFixture
) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    calls = counter(hub)
    assert hub.stale_after == 10

    frame(hub, clock, 2, 1)
    for _ in range(5):
        tick(hub, clock, 2)
    assert hub.available is True
    assert hub.measurement.power_w == 1

    tick(hub, clock, 2)
    assert hub.available is False
    assert "entities are unavailable" in caplog.text
    unavailable_calls = calls[0]

    # Stays unavailable without logging again.
    tick(hub, clock, 2)
    assert caplog.text.count("entities are unavailable") == 1

    hub.on_measurement(make_measurement(power_w=2))
    assert hub.available is True
    assert hub.measurement.power_w == 2
    assert calls[0] == unavailable_calls + 1
    assert "are back" in caplog.text


async def test_stale_window_follows_slow_meters(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    for _ in range(6):
        clock.now += 10
        hub.on_measurement(make_measurement())
    assert hub.stale_after == 30


async def test_steps_up_on_reconnect_and_reports_status(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    status = counter(hub, status=True)

    hub.on_transport(Transport.WEBSOCKET)
    assert status[0] == 1
    hub.on_transport(Transport.POLLING)
    assert status[0] == 2

    tick(hub, clock, WINDOW)
    assert hub.throttle.interval == 3
    assert status[0] == 3

    hub.on_measurement(make_measurement())
    for _ in range(int(CLEAN_PERIOD / 3)):
        tick(hub, clock, 3)
        hub.on_measurement(make_measurement())
    assert hub.throttle.interval == 2


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, make_hub, mock_config_entry: MockConfigEntry
) -> None:
    hub = make_hub()
    calls = counter(hub)
    with patch.object(mock_config_entry, "async_start_reauth") as start_reauth:
        hub.on_auth_failed()
    start_reauth.assert_called_once_with(hass)
    assert hub.available is False
    assert calls[0] == 1


async def test_missed_ticks_are_skipped(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    planned = clock.now
    clock.now += 7
    hub._tick(planned)
    assert hub.throttle.loop_lag == 7
    when = hub._timer.when() - hub.hass.loop.time()
    assert 1.5 < when <= 2


async def test_listener_removal(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.REALTIME)
    calls = [0]

    def update() -> None:
        calls[0] += 1

    remove = hub.async_add_listener(update)
    remove()
    hub.on_measurement(make_measurement())
    assert calls[0] == 0


def issue(hass: HomeAssistant, hub: RealtimeHub, kind: Issue) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_{hub.entry.entry_id}")


async def test_no_data_issue_after_five_minutes(
    hass: HomeAssistant, make_hub, clock, caplog: pytest.LogCaptureFixture
) -> None:
    hub = make_hub()
    tick(hub, clock, 12)
    assert hub.problem is Problem.NO_DATA
    assert "WARNING" in [
        r.levelname for r in caplog.records if "unavailable" in r.message
    ]
    assert issue(hass, hub, Issue.NO_DATA) is None

    tick(hub, clock, ISSUE_AFTER[Issue.NO_DATA] - 1)
    assert issue(hass, hub, Issue.NO_DATA) is None
    tick(hub, clock, 1)
    raised = issue(hass, hub, Issue.NO_DATA)
    assert raised is not None
    assert raised.severity is ir.IssueSeverity.WARNING
    assert raised.translation_placeholders == {"name": "P1 meter", "minutes": "5"}
    assert hub.issues == ["no_data"]

    hub.on_measurement(make_measurement())
    assert hub.problem is None
    assert issue(hass, hub, Issue.NO_DATA) is None
    assert "are back after 312s" in caplog.text
    assert "no_data is over; repair issue cleared" in caplog.text


async def test_no_data_issue_skipped_when_token_rejected(
    hass: HomeAssistant, make_hub, clock, mock_config_entry: MockConfigEntry
) -> None:
    hub = make_hub()
    with patch.object(mock_config_entry, "async_start_reauth"):
        hub.on_auth_failed()
    assert hub.problem is Problem.TOKEN_REJECTED
    tick(hub, clock, ISSUE_AFTER[Issue.NO_DATA] + 60)
    assert issue(hass, hub, Issue.NO_DATA) is None


async def test_polling_issue_after_fifteen_minutes(
    hass: HomeAssistant, make_hub, clock, caplog: pytest.LogCaptureFixture
) -> None:
    hub = make_hub()
    hub.on_transport(Transport.WEBSOCKET)
    hub.on_transport(Transport.POLLING)
    assert "dropped; polling" in caplog.text
    # Polling still delivers data, so this is not a problem for automations.
    for _ in range(int(ISSUE_AFTER[Issue.POLLING_FALLBACK] / 5)):
        clock.now += 5
        hub.on_measurement(make_measurement())
        hub._tick(clock.now)
    assert hub.problem is None
    assert issue(hass, hub, Issue.POLLING_FALLBACK) is not None

    hub.on_transport(Transport.WEBSOCKET)
    assert "streaming again" in caplog.text
    assert issue(hass, hub, Issue.POLLING_FALLBACK) is None
    assert "polling_fallback is over; repair issue cleared" in caplog.text


async def test_throttled_issue_after_thirty_minutes(
    hass: HomeAssistant, make_hub, clock
) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    hub.throttle._level += 1
    with patch.object(hub.throttle, "evaluate", return_value=False):
        for _ in range(int(ISSUE_AFTER[Issue.THROTTLED] / 3) + 1):
            clock.now += 3
            hub.on_measurement(make_measurement())
            hub._tick(clock.now)
    assert issue(hass, hub, Issue.THROTTLED) is not None

    hub.throttle._level -= 1
    tick(hub, clock, 0)
    assert issue(hass, hub, Issue.THROTTLED) is None


async def test_stop_clears_issues(
    hass: HomeAssistant, make_hub, clock, caplog: pytest.LogCaptureFixture
) -> None:
    hub = make_hub()
    tick(hub, clock, 12)
    tick(hub, clock, ISSUE_AFTER[Issue.NO_DATA])
    assert issue(hass, hub, Issue.NO_DATA) is not None
    hub.async_stop()
    assert issue(hass, hub, Issue.NO_DATA) is None
    assert hub.issues == []
    # Unloading is not the problem ending, so it is not logged as such.
    assert "repair issue cleared" not in caplog.text


async def test_freshness_follows_publishes(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    frame(hub, clock, 2, 10)
    frame(hub, clock, 1, 20)
    # Late: the held frame is published 1.5s after it arrived.
    clock.now += 1.5
    hub._hold.cancel()
    hub._publish_held()
    frame(hub, clock, 0.5, 30)
    clock.now += 1
    hub._hold.cancel()
    hub._publish_held()

    summary = hub.freshness.as_dict()
    assert summary["publishes"] == 3
    assert summary["held_publishes"] == 2
    # Spans of 2.5s from age 0 and 1.5s from age 1.5s.
    assert summary["max_age_s"] == 3.0

    hub._set_unavailable(clock.now)
    clock.now += 60
    frame(hub, clock, 0, 40)
    assert hub.freshness.as_dict()["max_age_s"] == 3.0


async def test_telegram_delay_from_naive_and_aware_timestamps(make_hub) -> None:
    hub = make_hub(UpdateSpeed.REALTIME)
    sent = dt_util.now() - timedelta(seconds=0.4)
    hub.on_measurement(
        make_measurement(timestamp=sent.replace(tzinfo=None).isoformat())
    )
    hub.on_measurement(make_measurement(timestamp=sent.isoformat()))
    delay = hub.freshness.as_dict()["telegram_delay_s"]
    assert 0.3 < delay < 1.5
