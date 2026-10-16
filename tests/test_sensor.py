"""Tests for the sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_realtime_push.client import Transport
from custom_components.homewizard_realtime_push.const import DOMAIN

from .conftest import SERIAL, make_measurement

POWER = "sensor.p1_meter_realtime_power"
INTERVAL = "sensor.p1_meter_realtime_effective_update_interval"
PHASE_ENTITIES = [
    f"sensor.p1_meter_realtime_{kind}_phase_{phase}"
    for kind in ("power", "voltage", "current")
    for phase in (1, 2, 3)
]


async def test_default_entities(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    state = hass.states.get(POWER)
    assert state is not None
    assert state.state == "-543.0"
    assert state.attributes["unit_of_measurement"] == "W"
    assert state.attributes["device_class"] == "power"
    assert state.attributes["state_class"] == "measurement"

    interval = hass.states.get(INTERVAL)
    assert interval is not None
    assert interval.state == "2.0"
    assert interval.attributes["configured_interval"] == 2.0
    assert interval.attributes["reason"] == "configured"
    assert interval.attributes["transport"] == "connecting"
    # Fast-moving throttle values live in diagnostics, not on this entity.
    assert "loop_lag_ms" not in interval.attributes
    assert "frame_interval_s" not in interval.attributes

    entity_registry = er.async_get(hass)
    for entity_id in PHASE_ENTITIES:
        entry = entity_registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(entity_id) is None

    power = entity_registry.async_get(POWER)
    assert power is not None
    assert power.unique_id == f"{DOMAIN}_{SERIAL}_power_w"

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SERIAL), init_integration.entry_id
    )
    assert device is not None
    assert device.name == "P1 meter Realtime"
    assert device.manufacturer == "HomeWizard"
    assert device.sw_version == "6.05"


@pytest.mark.usefixtures("mock_client")
async def test_enabled_phase_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    mock_config_entry.add_to_hass(hass)
    for entity_id in PHASE_ENTITIES:
        kind, phase = entity_id.split("_realtime_")[1].split("_phase_")
        unit = {"power": "w", "voltage": "v", "current": "a"}[kind]
        entity_registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{DOMAIN}_{SERIAL}_{kind}_l{phase}_{unit}",
            suggested_object_id=entity_id.split(".")[1],
            config_entry=mock_config_entry,
        )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.p1_meter_realtime_power_phase_2").state == "-300.0"
    assert hass.states.get("sensor.p1_meter_realtime_voltage_phase_1").state == "230.1"
    assert hass.states.get("sensor.p1_meter_realtime_current_phase_3").state == "0.19"


@pytest.mark.usefixtures("mock_client")
async def test_single_phase_meter(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api: MagicMock
) -> None:
    mock_api.measurement.return_value = make_measurement(
        power_l2_w=None,
        power_l3_w=None,
        voltage_l2_v=None,
        voltage_l3_v=None,
        current_l2_a=None,
        current_l3_a=None,
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entities = {
        entry.entity_id
        for entry in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
    }
    assert "sensor.p1_meter_realtime_power_phase_1" in entities
    assert "sensor.p1_meter_realtime_power_phase_2" not in entities
    assert "sensor.p1_meter_realtime_current_phase_3" not in entities


async def test_updates_and_availability(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    hub = init_integration.runtime_data
    hub._publish(make_measurement(power_w=1234))
    assert hass.states.get(POWER).state == "1234.0"

    hub._set_unavailable(hub._clock())
    assert hass.states.get(POWER).state == STATE_UNAVAILABLE
    # The diagnostic entity keeps reporting.
    assert hass.states.get(INTERVAL).state == "2.0"

    hub.on_measurement(make_measurement(power_w=None))
    assert hass.states.get(POWER).state == STATE_UNKNOWN


async def test_interval_entity_follows_status(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    hub = init_integration.runtime_data
    hub.on_transport(Transport.WEBSOCKET)
    assert hass.states.get(INTERVAL).attributes["transport"] == "websocket"
