"""Tests for the config, reauth, reconfigure and options flows."""

from __future__ import annotations

from collections.abc import Generator
from ipaddress import ip_address
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homewizard_energy.errors import DisabledError, RequestError, UnauthorizedError
from homewizard_energy.models import Device
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_realtime_push.const import (
    CONF_UPDATE_SPEED,
    DOMAIN,
)

from .conftest import DEVICE, HOST, SERIAL, TOKEN

FLOW = "custom_components.homewizard_realtime_push.config_flow"
NEW_TOKEN = "FEDCBA9876543210FEDCBA9876543210"


@pytest.fixture(autouse=True)
def no_wait() -> Generator[None]:
    with (
        patch(f"{FLOW}.TOKEN_WAIT", 0),
        patch(f"{FLOW}.TOKEN_RETRY_INTERVAL", 0),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    with patch(
        "custom_components.homewizard_realtime_push.async_setup_entry",
        return_value=True,
    ) as setup:
        yield setup


@pytest.fixture
def has_v2() -> Generator[AsyncMock]:
    with patch(f"{FLOW}.has_v2_api", return_value=True) as mock:
        yield mock


@pytest.fixture
def v2_api() -> Generator[MagicMock]:
    """The device: first token request opens the window, the second succeeds."""
    with patch(f"{FLOW}.HomeWizardEnergyV2", autospec=True) as api_class:
        api = api_class.return_value
        api.get_token = AsyncMock(side_effect=[DisabledError, NEW_TOKEN])
        api.device = AsyncMock(return_value=Device.from_dict(DEVICE))
        yield api


@pytest.fixture
def v1_api() -> Generator[MagicMock]:
    with patch(f"{FLOW}.HomeWizardEnergyV1", autospec=True) as api_class:
        api = api_class.return_value
        api.device = AsyncMock(side_effect=RequestError)
        yield api


def zeroconf_info(**properties: str) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address(HOST),
        ip_addresses=[ip_address(HOST)],
        hostname="p1meter.local.",
        name="p1meter._homewizard._tcp.local.",
        port=443,
        type="_homewizard._tcp.local.",
        properties={
            "product_name": "P1 meter",
            "product_type": "HWE-P1",
            "serial": SERIAL,
            **properties,
        },
    )


@pytest.mark.usefixtures("has_v2")
async def test_user_flow(hass: HomeAssistant, v2_api: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IP_ADDRESS: f" {HOST} "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "authorize"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "P1 meter"
    assert result["data"] == {CONF_IP_ADDRESS: HOST, CONF_TOKEN: NEW_TOKEN}
    assert result["result"].unique_id == SERIAL

    name = v2_api.get_token.await_args.args[0]
    assert name.startswith("ha-realtime-push#")


@pytest.mark.usefixtures("has_v2")
async def test_button_pressed_before_submit(
    hass: HomeAssistant, v2_api: MagicMock
) -> None:
    v2_api.get_token.side_effect = None
    v2_api.get_token.return_value = NEW_TOKEN
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_IP_ADDRESS: HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("has_v2")
async def test_button_not_pressed_then_pressed(
    hass: HomeAssistant, v2_api: MagicMock
) -> None:
    v2_api.get_token.side_effect = [DisabledError, DisabledError, NEW_TOKEN]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_IP_ADDRESS: HOST}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "button_not_pressed"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("has_v2")
async def test_retries_until_button_pressed(
    hass: HomeAssistant, v2_api: MagicMock
) -> None:
    v2_api.get_token.side_effect = [DisabledError, DisabledError, NEW_TOKEN]
    with patch(f"{FLOW}.TOKEN_WAIT", 5):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data={CONF_IP_ADDRESS: HOST}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert v2_api.get_token.await_count == 3


@pytest.mark.usefixtures("has_v2")
async def test_authorize_cannot_connect(hass: HomeAssistant, v2_api: MagicMock) -> None:
    v2_api.get_token.side_effect = [RequestError, NEW_TOKEN]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_IP_ADDRESS: HOST}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("has_v2")
@pytest.mark.parametrize(
    ("device", "reason"),
    [
        ({**DEVICE, "product_type": "HWE-SKT"}, "not_p1_meter"),
        (RequestError(), "cannot_connect"),
    ],
)
async def test_finish_aborts(
    hass: HomeAssistant, v2_api: MagicMock, device: object, reason: str
) -> None:
    if isinstance(device, dict):
        v2_api.device.return_value = Device.from_dict(device)
    else:
        v2_api.device.side_effect = device
    v2_api.get_token.side_effect = None
    v2_api.get_token.return_value = NEW_TOKEN
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_IP_ADDRESS: HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


@pytest.mark.usefixtures("has_v2", "v2_api")
async def test_user_flow_already_configured_updates_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={CONF_IP_ADDRESS: "192.0.2.20"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data == {
        CONF_IP_ADDRESS: "192.0.2.20",
        CONF_TOKEN: NEW_TOKEN,
    }


@pytest.mark.parametrize(
    ("v1_result", "outcome"),
    [
        (Device.from_dict({**DEVICE, "api_version": "v1"}), "firmware_too_old"),
        (DisabledError(), "firmware_too_old"),
        (RequestError(), "cannot_connect"),
    ],
)
async def test_user_flow_without_v2(
    hass: HomeAssistant, v1_api: MagicMock, v1_result: object, outcome: str
) -> None:
    if isinstance(v1_result, Exception):
        v1_api.device.side_effect = v1_result
    else:
        v1_api.device.side_effect = None
        v1_api.device.return_value = v1_result
    with patch(f"{FLOW}.has_v2_api", return_value=False):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data={CONF_IP_ADDRESS: HOST}
        )
    if outcome == "cannot_connect":
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}
    else:
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == outcome


@pytest.mark.usefixtures("v2_api")
async def test_zeroconf_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=zeroconf_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"] == {"name": "P1 meter", "host": HOST}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "authorize"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_IP_ADDRESS: HOST, CONF_TOKEN: NEW_TOKEN}


async def test_zeroconf_other_device(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_ZEROCONF},
        data=zeroconf_info(product_type="HWE-SKT"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_p1_meter"


async def test_zeroconf_missing_properties(hass: HomeAssistant) -> None:
    info = zeroconf_info()
    del info.properties["serial"]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_parameters"


async def test_zeroconf_updates_ip_of_configured_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    info = zeroconf_info()
    info.ip_address = ip_address("192.0.2.30")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_IP_ADDRESS] == "192.0.2.30"


async def test_zeroconf_ignores_ipv6(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    info = zeroconf_info()
    info.ip_address = ip_address("fd00::1")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "ipv6_not_supported"
    # A working IPv4 address must survive an IPv6-only announcement.
    assert mock_config_entry.data[CONF_IP_ADDRESS] == HOST


@pytest.mark.usefixtures("v2_api")
async def test_reauth(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "authorize"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == {CONF_IP_ADDRESS: HOST, CONF_TOKEN: NEW_TOKEN}


async def test_reauth_wrong_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, v2_api: MagicMock
) -> None:
    v2_api.device.return_value = Device.from_dict({**DEVICE, "serial": "aabbccddeeff"})
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert mock_config_entry.data[CONF_TOKEN] == TOKEN


async def test_reconfigure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, v2_api: MagicMock
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    v2_api.device.side_effect = [
        UnauthorizedError,
        RequestError,
        Device.from_dict(DEVICE),
    ]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IP_ADDRESS: "192.0.2.40"}
    )
    assert result["errors"] == {"base": "token_rejected"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IP_ADDRESS: "192.0.2.40"}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IP_ADDRESS: "192.0.2.40"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {CONF_IP_ADDRESS: "192.0.2.40", CONF_TOKEN: TOKEN}


async def test_reconfigure_wrong_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, v2_api: MagicMock
) -> None:
    v2_api.device.return_value = Device.from_dict({**DEVICE, "serial": "aabbccddeeff"})
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IP_ADDRESS: "192.0.2.40"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert mock_config_entry.data[CONF_IP_ADDRESS] == HOST


async def test_options_flow(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_SPEED: "realtime"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {CONF_UPDATE_SPEED: "realtime"}
