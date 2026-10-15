"""Tests for the hub: publishing, staleness and throttle wiring."""

from __future__ import annotations

from collections.abc import Callable, Generator
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homewizard_energy.models import Device
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_realtime_push.client import Transport
from custom_components.homewizard_realtime_push.const import (
    CONF_UPDATE_SPEED,
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


async def test_publishes_latest_frame_once_per_interval(make_hub, clock) -> None:
    hub = make_hub(UpdateSpeed.VERY_FAST)
    calls = counter(hub)

    for power in (10, 20, 30):
        clock.now += 0.5
        hub.on_measurement(make_measurement(power_w=power))
    assert calls[0] == 0
    assert hub.measurement.power_w == 0

    tick(hub, clock, 0.5)
    assert calls[0] == 1
    # The latest value, not an average.
    assert hub.measurement.power_w == 30

    # Nothing new: no publish.
    tick(hub, clock, 2)
    assert calls[0] == 1


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

    hub.on_measurement(make_measurement(power_w=1))
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
