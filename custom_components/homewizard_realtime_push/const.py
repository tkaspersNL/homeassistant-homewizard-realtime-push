"""Constants for HomeWizard Realtime Push."""

from __future__ import annotations

from enum import StrEnum
import logging
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "homewizard_realtime_push"
LOGGER = logging.getLogger(__package__)

PLATFORMS: Final = [Platform.BINARY_SENSOR, Platform.SENSOR]

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


class Problem(StrEnum):
    """Why there is no fresh data."""

    NO_DATA = "no_data"
    TOKEN_REJECTED = "token_rejected"  # noqa: S105


class Issue(StrEnum):
    """Repair issues raised when a problem lasts."""

    NO_DATA = "no_data"
    POLLING_FALLBACK = "polling_fallback"
    THROTTLED = "throttled"


# Short hiccups are only logged; these durations (s) raise a repair issue.
ISSUE_AFTER: Final[dict[Issue, float]] = {
    Issue.NO_DATA: 5 * 60,
    Issue.POLLING_FALLBACK: 15 * 60,
    Issue.THROTTLED: 30 * 60,
}


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
