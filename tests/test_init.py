"""Tests for setting up and unloading the integration."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import ANY, AsyncMock, MagicMock, PropertyMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homewizard_energy.errors import NotFoundError, RequestError, UnauthorizedError
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.homewizard_realtime_push.client import Transport
from custom_components.homewizard_realtime_push.const import CONF_UPDATE_SPEED, DOMAIN
from custom_components.homewizard_realtime_push.coordinator import RealtimeHub

from .conftest import HOST, TOKEN


@pytest.fixture
def remove_api() -> Generator[MagicMock]:
    """Patch the REST client used when an entry is removed."""
    with patch(
        "custom_components.homewizard_realtime_push.HomeWizardEnergyV2", autospec=True
    ) as api_class:
        api_class.return_value.delete_token = AsyncMock(return_value=None)
        yield api_class


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


async def test_remove_deletes_token(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: MagicMock
) -> None:
    mock_api.delete_token = AsyncMock(return_value=None)
    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    # Without a name, the meter deletes the user that owns the token.
    mock_api.delete_token.assert_awaited_once_with()
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_remove_entry_that_is_not_loaded_deletes_token(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, remove_api: MagicMock
) -> None:
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    remove_api.assert_called_once_with(HOST, token=TOKEN, clientsession=ANY)
    remove_api.return_value.delete_token.assert_awaited_once_with()
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    "error",
    [
        RequestError("timeout"),
        NotFoundError("Resource not found"),
        ValueError("unreadable answer"),
    ],
)
async def test_remove_works_when_token_delete_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    remove_api: MagicMock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    remove_api.return_value.delete_token.side_effect = error
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.config_entries.async_entries(DOMAIN)
    assert "Could not delete the token from the meter at 192.0.2.10" in caplog.text
    assert "Error calling entry remove callback" not in caplog.text


async def test_remove_with_rejected_token_is_quiet(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    remove_api: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A token the meter rejects is already gone from its user list."""
    remove_api.return_value.delete_token.side_effect = UnauthorizedError("rejected")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.config_entries.async_entries(DOMAIN)
    assert "Could not delete the token" not in caplog.text
    assert "Error calling entry remove callback" not in caplog.text


@pytest.mark.parametrize(
    ("period", "step", "placeholders"),
    [
        (None, "init", None),
        (9.8, "init_measured", {"seconds": "10"}),
        (1.02, "init_measured", {"seconds": "1"}),
        (0.6, "init_measured", {"seconds": "1"}),
    ],
)
async def test_options_flow_names_the_meter_interval_when_known(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    period: float | None,
    step: str,
    placeholders: dict[str, str] | None,
) -> None:
    with patch.object(
        RealtimeHub, "meter_interval", new_callable=PropertyMock, return_value=period
    ):
        result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step
    assert result["description_placeholders"] == placeholders

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_SPEED: "normal"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.runtime_data.throttle.floor == 5
