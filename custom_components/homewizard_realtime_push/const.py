"""Constants for HomeWizard Realtime Push."""

from __future__ import annotations

from enum import StrEnum
import logging
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "homewizard_realtime_push"
LOGGER = logging.getLogger(__package__)

PLATFORMS: Final = [Platform.SENSOR]

CONF_PRODUCT_NAME: Final = "product_name"
CONF_PRODUCT_TYPE: Final = "product_type"
CONF_SERIAL: Final = "serial"
CONF_UPDATE_SPEED: Final = "update_speed"

# Token name prefix as shown in the device's user list (the library adds "local/").
TOKEN_NAME: Final = "ha-realtime-push"  # noqa: S105
# Pressing the button opens a 30s window on the device.
TOKEN_WAIT: Final = 30.0
TOKEN_RETRY_INTERVAL: Final = 1.0

# Entities go unavailable after this long without a frame, or 3 intervals.
STALE_MIN: Final = 10.0
STALE_FACTOR: Final = 3


class UpdateSpeed(StrEnum):
    """User-facing update speed options."""

    REALTIME = "realtime"
    VERY_FAST = "very_fast"
    FAST = "fast"
    NORMAL = "normal"


UPDATE_SPEED_INTERVAL: Final[dict[UpdateSpeed, float]] = {
    UpdateSpeed.REALTIME: 1.0,
    UpdateSpeed.VERY_FAST: 2.0,
    UpdateSpeed.FAST: 3.0,
    UpdateSpeed.NORMAL: 5.0,
}
DEFAULT_UPDATE_SPEED: Final = UpdateSpeed.VERY_FAST
