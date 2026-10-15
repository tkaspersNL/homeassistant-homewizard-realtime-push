"""Sensors for HomeWizard Realtime Push."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from operator import attrgetter
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homewizard_energy.models import Measurement

from .coordinator import HomeWizardRealtimeConfigEntry, RealtimeHub
from .entity import RealtimeEntity

# The hub pushes updates; there is nothing to throttle per entity.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class RealtimeSensorEntityDescription(SensorEntityDescription):
    """Sensor description with a value getter."""

    value_fn: Callable[[Measurement], float | None]


def _phase_sensors() -> list[RealtimeSensorEntityDescription]:
    kinds: list[tuple[str, str, SensorDeviceClass, str, int]] = [
        ("power_l{}_w", "power_phase", SensorDeviceClass.POWER, UnitOfPower.WATT, 0),
        (
            "voltage_l{}_v",
            "voltage_phase",
            SensorDeviceClass.VOLTAGE,
            UnitOfElectricPotential.VOLT,
            1,
        ),
        (
            "current_l{}_a",
            "current_phase",
            SensorDeviceClass.CURRENT,
            UnitOfElectricCurrent.AMPERE,
            2,
        ),
    ]
    return [
        RealtimeSensorEntityDescription(
            key=key.format(phase),
            translation_key=translation_key,
            translation_placeholders={"phase": str(phase)},
            device_class=device_class,
            native_unit_of_measurement=unit,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=precision,
            entity_registry_enabled_default=False,
            value_fn=attrgetter(key.format(phase)),
        )
        for key, translation_key, device_class, unit, precision in kinds
        for phase in (1, 2, 3)
    ]


SENSORS: tuple[RealtimeSensorEntityDescription, ...] = (
    RealtimeSensorEntityDescription(
        key="power_w",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda measurement: measurement.power_w,
    ),
    *_phase_sensors(),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomeWizardRealtimeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for the values this meter reports."""
    hub = entry.runtime_data
    entities: list[SensorEntity] = [
        RealtimeSensor(hub, description)
        for description in SENSORS
        # Single-phase meters have no L2 and L3 values.
        if description.value_fn(hub.measurement) is not None
    ]
    entities.append(EffectiveIntervalSensor(hub))
    async_add_entities(entities)


class RealtimeSensor(RealtimeEntity, SensorEntity):
    """A fast-moving measurement."""

    entity_description: RealtimeSensorEntityDescription

    def __init__(
        self, hub: RealtimeHub, description: RealtimeSensorEntityDescription
    ) -> None:
        """Set up the sensor."""
        super().__init__(hub, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Follow published measurements."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        """Return False when measurements stopped arriving."""
        return self.hub.available

    @property
    def native_value(self) -> float | None:
        """Return the latest published value."""
        return self.entity_description.value_fn(self.hub.measurement)


class EffectiveIntervalSensor(RealtimeEntity, SensorEntity):
    """The update interval the throttle currently uses."""

    _attr_translation_key = "effective_interval"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: RealtimeHub) -> None:
        """Set up the sensor."""
        super().__init__(hub, "effective_interval")

    async def async_added_to_hass(self) -> None:
        """Follow throttle and transport changes only, not every frame."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hub.async_add_status_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> float:
        """Return the effective interval in seconds."""
        return self.hub.throttle.interval

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return why the interval is what it is."""
        throttle = self.hub.throttle
        lag = throttle.loop_lag
        return {
            "configured_interval": throttle.floor,
            "reason": throttle.reason.value,
            "transport": self.hub.transport.value,
            "loop_lag_ms": None if lag is None else round(lag * 1000),
            "frame_interval_s": throttle.frame_period,
        }
