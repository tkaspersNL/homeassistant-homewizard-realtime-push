"""Base entity for HomeWizard Realtime Push."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import RealtimeHub


class RealtimeEntity(Entity):
    """Entity that is updated by the hub instead of polling."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: RealtimeHub, key: str) -> None:
        """Attach the entity to the meter's realtime device."""
        self.hub = hub
        device = hub.device
        self._attr_unique_id = f"{DOMAIN}_{device.serial}_{key}"
        # A separate device keeps entity IDs apart from the official integration.
        self._attr_device_info = DeviceInfo(
            # The serial is the meter's MAC address; with it, DHCP discovery
            # can follow the meter to a new IP address.
            connections={(CONNECTION_NETWORK_MAC, device.serial)},
            identifiers={(DOMAIN, device.serial)},
            manufacturer="HomeWizard",
            model=device.model_name or device.product_type,
            model_id=device.product_type,
            name=f"{device.product_name} Realtime",
            serial_number=device.serial,
            sw_version=device.firmware_version,
        )
