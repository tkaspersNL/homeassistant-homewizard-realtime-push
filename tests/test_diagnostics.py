"""Tests for diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_realtime_push.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import HOST, SERIAL, TOKEN, make_measurement


async def test_diagnostics_are_redacted(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    result = await async_get_config_entry_diagnostics(hass, init_integration)

    assert result["entry"]["data"] == {"ip_address": REDACTED, "token": REDACTED}
    assert result["device"]["serial"] == REDACTED
    assert result["device"]["product_type"] == "HWE-P1"
    assert result["throttle"]["effective_interval_s"] == 2.0
    assert "loop_lag_s" in result["throttle"]
    assert "frame_interval_s" in result["throttle"]
    assert result["hub"]["available"] is True
    assert result["freshness"]["publishes"] == 0
    assert result["measurement"]["power_w"] == -543

    text = repr(result)
    for secret in (HOST, SERIAL, TOKEN):
        assert secret not in text


async def test_diagnostics_show_the_meter_timestamp(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: MagicMock,
    mock_client: MagicMock,
) -> None:
    mock_api.measurement.return_value = make_measurement(
        timestamp="2026-01-01T12:00:00"
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["measurement"]["timestamp"] == "2026-01-01T12:00:00"
