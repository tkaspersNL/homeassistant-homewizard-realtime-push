"""Tests for diagnostics."""

from __future__ import annotations

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_realtime_push.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import HOST, SERIAL, TOKEN


async def test_diagnostics_are_redacted(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    result = await async_get_config_entry_diagnostics(hass, init_integration)

    assert result["entry"]["data"] == {"ip_address": REDACTED, "token": REDACTED}
    assert result["device"]["serial"] == REDACTED
    assert result["device"]["product_type"] == "HWE-P1"
    assert result["throttle"]["effective_interval_s"] == 2.0
    assert result["hub"]["available"] is True
    assert result["measurement"]["power_w"] == -543

    text = repr(result)
    for secret in (HOST, SERIAL, TOKEN):
        assert secret not in text
