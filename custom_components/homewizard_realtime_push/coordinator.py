"""Hub that turns pushed frames into throttled entity updates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homewizard_energy import HomeWizardEnergyV2
from homewizard_energy.models import Device, Measurement

from .client import RealtimeClient, Transport
from .const import (
    CONF_UPDATE_SPEED,
    DEFAULT_UPDATE_SPEED,
    DOMAIN,
    LOGGER,
    STALE_FACTOR,
    STALE_MIN,
    UPDATE_SPEED_INTERVAL,
    UpdateSpeed,
)
from .throttle import Throttle

type HomeWizardRealtimeConfigEntry = ConfigEntry[RealtimeHub]


class RealtimeHub:
    """Own the client and throttle, and tell entities when to update."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HomeWizardRealtimeConfigEntry,
        api: HomeWizardEnergyV2,
        device: Device,
        measurement: Measurement,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Start from the measurement fetched during setup."""
        self.hass = hass
        self.entry = entry
        self.api = api
        self.device = device
        self.measurement = measurement
        self.available = True
        self.transport = Transport.CONNECTING

        self._clock = clock or hass.loop.time
        now = self._clock()
        speed = UpdateSpeed(entry.options.get(CONF_UPDATE_SPEED, DEFAULT_UPDATE_SPEED))
        self.throttle = Throttle[Measurement](UPDATE_SPEED_INTERVAL[speed], now)
        self._last_frame = now
        self._timer: asyncio.TimerHandle | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._status_listeners: list[CALLBACK_TYPE] = []

        self.client = RealtimeClient(
            async_get_clientsession(hass),
            api,
            entry.data[CONF_TOKEN],
            self,
            lambda: self.throttle.interval,
        )

    @property
    def stale_after(self) -> float:
        """Return how long without frames before entities go unavailable."""
        period = self.throttle.frame_period or 0.0
        return max(
            STALE_MIN,
            STALE_FACTOR * self.throttle.interval,
            # Meters older than DSMR 5 only send a telegram every 10s.
            STALE_FACTOR * period,
        )

    @callback
    def async_start(self) -> None:
        """Start streaming and the publish timer."""
        self.entry.async_create_background_task(
            self.hass, self.client.run(), name=f"{DOMAIN} {self.api.host}"
        )
        self._schedule(self._clock() + self.throttle.interval)

    @callback
    def async_stop(self) -> None:
        """Stop the publish timer; the client task ends with the entry."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call update whenever a measurement is published or availability changes."""
        return self._add(self._listeners, update)

    @callback
    def async_add_status_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call update whenever the throttle level or transport changes."""
        return self._add(self._status_listeners, update)

    @callback
    def on_measurement(self, measurement: Measurement) -> None:
        """Handle a frame from the client."""
        now = self._clock()
        self._last_frame = now
        publish_now = self.throttle.offer(measurement, now)
        if not self.available:
            LOGGER.info("Measurements from %s are back", self.api.host)
            self.available = True
            publish_now = True
        if publish_now and (latest := self.throttle.take()) is not None:
            self._publish(latest)

    @callback
    def on_transport(self, transport: Transport) -> None:
        """Handle a transport change from the client."""
        if self.transport is Transport.WEBSOCKET:
            self.throttle.record_reconnect(self._clock())
        LOGGER.debug("Transport for %s is now %s", self.api.host, transport)
        self.transport = transport
        self._notify(self._status_listeners)

    @callback
    def on_auth_failed(self) -> None:
        """Handle a rejected token."""
        LOGGER.warning("%s rejected the token; reauthentication needed", self.api.host)
        self._set_unavailable()
        self.entry.async_start_reauth(self.hass)

    @callback
    def _tick(self, planned: float) -> None:
        now = self._clock()
        self.throttle.record_lag(now - planned, now)

        if self.available and now - self._last_frame > self.stale_after:
            LOGGER.info(
                "No measurements from %s for %.0fs; entities are unavailable",
                self.api.host,
                now - self._last_frame,
            )
            self._set_unavailable()
        elif (
            self.available
            and not self.throttle.publish_on_arrival
            and (latest := self.throttle.take()) is not None
        ):
            self._publish(latest)

        if self.throttle.evaluate(now):
            LOGGER.debug(
                "Update interval for %s is now %ss (%s)",
                self.api.host,
                self.throttle.interval,
                self.throttle.reason,
            )
            self._notify(self._status_listeners)

        planned += self.throttle.interval
        if planned <= now:
            # Skip missed ticks instead of firing them in a burst.
            planned = now + self.throttle.interval
        self._schedule(planned)

    def _schedule(self, when: float) -> None:
        self.async_stop()
        self._timer = self.hass.loop.call_later(
            max(0.0, when - self._clock()), self._tick, when
        )

    def _set_unavailable(self) -> None:
        self.available = False
        # Never publish a frame from before the gap.
        self.throttle.take()
        self._notify(self._listeners)

    def _publish(self, measurement: Measurement) -> None:
        self.measurement = measurement
        self._notify(self._listeners)

    @staticmethod
    def _notify(listeners: list[CALLBACK_TYPE]) -> None:
        for update in list(listeners):
            update()

    @staticmethod
    def _add(listeners: list[CALLBACK_TYPE], update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        listeners.append(update)

        @callback
        def remove() -> None:
            listeners.remove(update)

        return remove
