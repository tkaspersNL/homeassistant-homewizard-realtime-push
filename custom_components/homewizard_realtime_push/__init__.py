"""HomeWizard Realtime Push: fast P1 measurements over the API v2 WebSocket."""

from __future__ import annotations

from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homewizard_energy import HomeWizardEnergyV2
from homewizard_energy.errors import RequestError, UnauthorizedError

from .const import DOMAIN, PLATFORMS
from .coordinator import HomeWizardRealtimeConfigEntry, RealtimeHub


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeWizardRealtimeConfigEntry
) -> bool:
    """Set up HomeWizard Realtime Push from a config entry."""
    host = entry.data[CONF_IP_ADDRESS]
    api = HomeWizardEnergyV2(
        host, token=entry.data[CONF_TOKEN], clientsession=async_get_clientsession(hass)
    )
    try:
        device = await api.device()
        measurement = await api.measurement()
    except UnauthorizedError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="token_rejected"
        ) from err
    except RequestError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": host},
        ) from err

    hub = RealtimeHub(hass, entry, api, device, measurement)
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hub.async_start()
    entry.async_on_unload(hub.async_stop)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HomeWizardRealtimeConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
