"""Tests for setting up and unloading the integration."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_OFF
from homeassistant.core import HomeAssistant
from homewizard_energy.errors import RequestError, UnauthorizedError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_realtime_push.client import Transport
from custom_components.homewizard_realtime_push.const import DOMAIN


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    assert init_integration.state is ConfigEntryState.LOADED
    mock_client.return_value.run.assert_awaited_once()

    hub = init_integration.runtime_data
    assert hub._timer is not None

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert hub._timer is None


@pytest.mark.usefixtures("mock_client")
async def test_rejected_token_starts_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: MagicMock
) -> None:
    mock_api.device.side_effect = UnauthorizedError("Token rejected")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


@pytest.mark.usefixtures("mock_client")
async def test_unreachable_device_retries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: MagicMock
) -> None:
    mock_api.measurement.side_effect = RequestError("timeout")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_shutdown_is_not_a_problem(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A restart closes the connection; entities must not report stale data."""
    hub = init_integration.runtime_data
    hub.on_transport(Transport.WEBSOCKET)
    power = hass.states.get("sensor.p1_meter_realtime_power").state

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert hub._timer is None

    # What the client reports while the connection closes is ignored.
    hub.on_transport(Transport.POLLING)
    hub.on_auth_failed()
    assert hub.transport is Transport.WEBSOCKET
    assert hass.states.get("binary_sensor.p1_meter_realtime_status").state == STATE_OFF
    assert hass.states.get("sensor.p1_meter_realtime_power").state == power

    # Unloading after the stop event must not trip over the used listener.
    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert "Unable to remove unknown job listener" not in caplog.text
