"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homewizard_energy.models import Device, Measurement
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_realtime_push.const import DOMAIN

HOST = "192.0.2.10"
SERIAL = "5c2fafabcdef"
TOKEN = "0123456789ABCDEF0123456789ABCDEF"

DEVICE: dict[str, Any] = {
    "product_name": "P1 meter",
    "product_type": "HWE-P1",
    "serial": SERIAL,
    "api_version": "2.0.0",
    "firmware_version": "6.05",
}
THREE_PHASE: dict[str, Any] = {
    "power_w": -543,
    "power_l1_w": -200,
    "power_l2_w": -300,
    "power_l3_w": -43,
    "voltage_l1_v": 230.1,
    "voltage_l2_v": 231.2,
    "voltage_l3_v": 229.8,
    "current_l1_a": 0.87,
    "current_l2_a": 1.3,
    "current_l3_a": 0.19,
}


def make_measurement(**values: Any) -> Measurement:
    """Return a measurement with three-phase defaults."""
    return Measurement.from_dict({**THREE_PHASE, **values})


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the custom integration in every test."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a configured entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="P1 meter",
        unique_id=SERIAL,
        data={CONF_IP_ADDRESS: HOST, CONF_TOKEN: TOKEN},
    )


@pytest.fixture
def mock_api() -> Generator[MagicMock]:
    """Patch the REST client used during setup."""
    with patch(
        "custom_components.homewizard_realtime_push.HomeWizardEnergyV2", autospec=True
    ) as api_class:
        api = api_class.return_value
        api.host = HOST
        api.device = AsyncMock(return_value=Device.from_dict(DEVICE))
        api.measurement = AsyncMock(return_value=make_measurement())
        yield api


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Patch the streaming client so no connection is made."""
    with patch(
        "custom_components.homewizard_realtime_push.coordinator.RealtimeClient",
        autospec=True,
    ) as client_class:
        client_class.return_value.run = AsyncMock(return_value=None)
        yield client_class


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: MagicMock,
    mock_client: MagicMock,
) -> MockConfigEntry:
    """Set up the integration."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
