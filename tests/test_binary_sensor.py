"""Tests for the problem sensor."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import make_measurement

PROBLEM = "binary_sensor.p1_meter_realtime_problem"


async def test_problem_sensor_follows_data(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    state = hass.states.get(PROBLEM)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["device_class"] == "problem"
    assert state.attributes["reason"] is None

    entry = er.async_get(hass).async_get(PROBLEM)
    assert entry is not None
    assert entry.entity_category is er.EntityCategory.DIAGNOSTIC
    assert entry.disabled_by is None

    hub = init_integration.runtime_data
    hub._set_unavailable(hub._clock())
    state = hass.states.get(PROBLEM)
    assert state.state == STATE_ON
    assert state.attributes["reason"] == "no_data"

    hub.on_measurement(make_measurement())
    assert hass.states.get(PROBLEM).state == STATE_OFF


async def test_problem_sensor_on_rejected_token(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    hub = init_integration.runtime_data
    with patch.object(init_integration, "async_start_reauth"):
        hub.on_auth_failed()
    state = hass.states.get(PROBLEM)
    assert state.state == STATE_ON
    assert state.attributes["reason"] == "token_rejected"
