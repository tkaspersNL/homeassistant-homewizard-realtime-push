"""Diagnostics for HomeWizard Realtime Push."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant

from .const import CONF_SERIAL
from .coordinator import HomeWizardRealtimeConfigEntry

TO_REDACT = {CONF_IP_ADDRESS, CONF_TOKEN, CONF_SERIAL, "unique_id", "id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HomeWizardRealtimeConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    hub = entry.runtime_data
    throttle = hub.throttle
    measurement = hub.measurement
    return async_redact_data(
        {
            "entry": {
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "device": {
                "product_type": hub.device.product_type,
                "product_name": hub.device.product_name,
                "serial": hub.device.serial,
                "api_version": str(hub.device.api_version),
                "firmware_version": hub.device.firmware_version,
            },
            "hub": {
                "available": hub.available,
                "transport": hub.transport.value,
                "stale_after_s": hub.stale_after,
                "problem": hub.problem,
                "issues": hub.issues,
            },
            "throttle": {
                "configured_interval_s": throttle.floor,
                "effective_interval_s": throttle.interval,
                "reason": throttle.reason.value,
                "loop_lag_s": throttle.loop_lag,
                "frame_interval_s": throttle.frame_period,
            },
            "measurement": {
                key: value
                for key, value in asdict(measurement).items()
                if key.startswith(("power_", "voltage_", "current_"))
            },
        },
        TO_REDACT,
    )
