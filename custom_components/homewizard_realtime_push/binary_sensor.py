"""Status sensor for HomeWizard Realtime Push."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HomeWizardRealtimeConfigEntry, RealtimeHub
from .entity import RealtimeEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomeWizardRealtimeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the status sensor."""
    async_add_entities([StatusBinarySensor(entry.runtime_data)])


class StatusBinarySensor(RealtimeEntity, BinarySensorEntity):
    """OK, or Problem (on) when there is no fresh data, so automations can fail safe."""

    _attr_translation_key = "status"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: RealtimeHub) -> None:
        """Set up the sensor."""
        # The unique ID keeps its original key so existing entities stay put.
        super().__init__(hub, "problem")

    async def async_added_to_hass(self) -> None:
        """Follow status changes only, not every frame."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hub.async_add_status_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool:
        """Return True when there is no fresh data."""
        return self.hub.problem is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return why there is no fresh data."""
        problem = self.hub.problem
        return {"reason": None if problem is None else problem.value}
