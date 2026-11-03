"""Config flow for HomeWizard Realtime Push."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers import instance_id
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homewizard_energy import HomeWizardEnergyV1, HomeWizardEnergyV2, has_v2_api
from homewizard_energy.const import Model
from homewizard_energy.errors import (
    DisabledError,
    HomeWizardEnergyException,
    RequestError,
    UnauthorizedError,
)
from homewizard_energy.models import Device
import probatio as vol

from .const import (
    CONF_PRODUCT_NAME,
    CONF_PRODUCT_TYPE,
    CONF_SERIAL,
    CONF_UPDATE_SPEED,
    DEFAULT_UPDATE_SPEED,
    DOMAIN,
    TOKEN_NAME,
    TOKEN_RETRY_INTERVAL,
    TOKEN_WAIT,
    UpdateSpeed,
)

HOST_SCHEMA = vol.Schema({vol.Required(CONF_IP_ADDRESS): TextSelector()})


class HomeWizardRealtimeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a P1 meter with a token from the device."""

    VERSION = 1

    host: str
    product_name: str = "P1 meter"
    _token_task: asyncio.Task[str | None] | None = None
    _token: str
    _authorize_error: str

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return RealtimeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the IP address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_IP_ADDRESS].strip()
            session = async_get_clientsession(self.hass)
            if await has_v2_api(host, session):
                self.host = host
                return await self.async_step_authorize()
            if await self._async_has_v1_api(host):
                return self.async_abort(reason="firmware_too_old")
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(HOST_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a device found on the network."""
        # The meter can announce itself with only an IPv6 address while it
        # rejoins the network; the API client can't use one, and storing it
        # would replace a working IPv4 address.
        if discovery_info.ip_address.version == 6:
            return self.async_abort(reason="ipv6_not_supported")
        properties = discovery_info.properties
        if any(
            key not in properties
            for key in (CONF_PRODUCT_NAME, CONF_PRODUCT_TYPE, CONF_SERIAL)
        ):
            return self.async_abort(reason="invalid_discovery_parameters")
        if properties[CONF_PRODUCT_TYPE] != Model.P1_METER:
            return self.async_abort(reason="not_p1_meter")

        await self.async_set_unique_id(properties[CONF_SERIAL])
        self._abort_if_unique_id_configured(
            updates={CONF_IP_ADDRESS: discovery_info.host}
        )
        self.host = discovery_info.host
        self.product_name = properties[CONF_PRODUCT_NAME]
        self.context["title_placeholders"] = {"name": self.product_name}
        return await self.async_step_discovery_confirm()

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Follow a configured meter to a new IP address.

        Only MAC addresses of this integration's devices start this step.
        """
        host = discovery_info.ip
        await self.async_set_unique_id(discovery_info.macaddress)
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, discovery_info.macaddress
        )
        if entry is not None and entry.data[CONF_IP_ADDRESS] != host:
            # A DHCP request can ask for an address the meter doesn't get,
            # so only an address where it answers replaces the stored one.
            api = HomeWizardEnergyV2(
                host,
                token=entry.data[CONF_TOKEN],
                clientsession=async_get_clientsession(self.hass),
            )
            try:
                device = await api.device()
            except HomeWizardEnergyException:
                return self.async_abort(reason="cannot_connect")
            if device.serial != entry.unique_id:
                return self.async_abort(reason="wrong_device")
        self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESS: host})
        # Setting up a new meter needs the button press, so DHCP leaves that
        # to zeroconf discovery and the user.
        return self.async_abort(reason="unknown")

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device."""
        if user_input is not None:
            return await self.async_step_authorize()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": self.product_name, "host": self.host},
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start over with a new token when the old one is rejected."""
        self.host = entry_data[CONF_IP_ADDRESS]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain why a new token is needed."""
        if user_input is not None:
            return await self.async_step_authorize()
        return self.async_show_form(
            step_id="reauth_confirm", description_placeholders=self._wait_placeholders()
        )

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for the button press while asking the device for a token."""
        if self._token_task is None:
            # Not eager, so the waiting screen shows before the first request.
            self._token_task = self.hass.async_create_task(
                self._async_request_token(TOKEN_WAIT), eager_start=False
            )
        if not self._token_task.done():
            return self.async_show_progress(
                step_id="authorize",
                progress_action="wait_for_button",
                progress_task=self._token_task,
                description_placeholders=self._wait_placeholders(),
            )

        task, self._token_task = self._token_task, None
        try:
            token = task.result()
        except RequestError:
            self._authorize_error = "cannot_connect"
            return self.async_show_progress_done(next_step_id="authorize_retry")
        if token is None:
            self._authorize_error = "button_not_pressed"
            return self.async_show_progress_done(next_step_id="authorize_retry")
        self._token = token
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_authorize_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Say why no token came, and wait again when asked."""
        if user_input is not None:
            return await self.async_step_authorize()
        return self.async_show_form(
            step_id="authorize_retry",
            errors={"base": self._authorize_error},
            description_placeholders=self._wait_placeholders(),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the IP address."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_IP_ADDRESS].strip()
            api = HomeWizardEnergyV2(
                host,
                token=entry.data[CONF_TOKEN],
                clientsession=async_get_clientsession(self.hass),
            )
            try:
                device = await api.device()
            except UnauthorizedError:
                errors["base"] = "token_rejected"
            except RequestError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device.serial)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_IP_ADDRESS: host}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                HOST_SCHEMA,
                user_input or {CONF_IP_ADDRESS: entry.data[CONF_IP_ADDRESS]},
            ),
            errors=errors,
        )

    async def _async_has_v1_api(self, host: str) -> bool:
        """Return True when the device answers on the old v1 API only."""
        api = HomeWizardEnergyV1(host, clientsession=async_get_clientsession(self.hass))
        try:
            await api.device()
        except DisabledError:
            # The device exists; its v1 API is switched off.
            return True
        except HomeWizardEnergyException:
            return False
        return True

    @staticmethod
    def _wait_placeholders() -> dict[str, str]:
        return {"seconds": f"{TOKEN_WAIT:.0f}"}

    async def _async_request_token(self, wait: float) -> str | None:
        """Request a token, retrying until the button is pressed or time runs out."""
        api = HomeWizardEnergyV2(
            self.host, clientsession=async_get_clientsession(self.hass)
        )
        # A per-instance suffix keeps tokens of several HA instances apart.
        uuid = await instance_id.async_get(self.hass)
        name = f"{TOKEN_NAME}#{uuid[:6]}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        answered = False
        while True:
            try:
                token: str = await api.get_token(name)
            except DisabledError:
                answered = True
            except RequestError:
                # Once the meter has answered, a Wi-Fi hiccup during the wait
                # is not fatal; a meter that never answers fails at once.
                if not answered:
                    raise
            else:
                return token
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(TOKEN_RETRY_INTERVAL)

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check the device with the new token and store it."""
        token = self._token
        api = HomeWizardEnergyV2(
            self.host, token=token, clientsession=async_get_clientsession(self.hass)
        )
        try:
            device: Device = await api.device()
        except RequestError, UnauthorizedError:
            return self.async_abort(reason="cannot_connect")
        if device.product_type != Model.P1_METER:
            return self.async_abort(reason="not_p1_meter")

        await self.async_set_unique_id(device.serial, raise_on_progress=False)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_device")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates={CONF_TOKEN: token}
            )

        data = {CONF_IP_ADDRESS: self.host, CONF_TOKEN: token}
        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(title=device.product_name, data=data)


class RealtimeOptionsFlow(OptionsFlowWithReload):
    """Choose the update speed."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the update speed dropdown."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_SPEED,
                    default=self.config_entry.options.get(
                        CONF_UPDATE_SPEED, DEFAULT_UPDATE_SPEED
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[speed.value for speed in UpdateSpeed],
                        translation_key=CONF_UPDATE_SPEED,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
