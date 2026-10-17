"""Hub that turns pushed frames into throttled entity updates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from homewizard_energy import HomeWizardEnergyV2
from homewizard_energy.models import Device, Measurement

from .client import RealtimeClient, Transport
from .const import (
    CONF_UPDATE_SPEED,
    DEFAULT_UPDATE_SPEED,
    DOMAIN,
    ISSUE_AFTER,
    LOGGER,
    STALE_FACTOR,
    STALE_MIN,
    UPDATE_SPEED_INTERVAL,
    Issue,
    Problem,
    UpdateSpeed,
)
from .freshness import Freshness
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
        self.freshness = Freshness()
        self._last_frame = now
        self._timer: asyncio.TimerHandle | None = None
        self._hold: asyncio.TimerHandle | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._status_listeners: list[CALLBACK_TYPE] = []

        self._token_rejected = False
        self._stopping = False
        self._unsub_stop: CALLBACK_TYPE | None = None
        # When each issue condition started; None while it does not hold.
        self._since: dict[Issue, float | None] = dict.fromkeys(Issue)
        self._raised: set[Issue] = set()

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

    @property
    def problem(self) -> Problem | None:
        """Return why there is no fresh data, or None when there is."""
        if self._token_rejected:
            return Problem.TOKEN_REJECTED
        if not self.available:
            return Problem.NO_DATA
        return None

    @property
    def issues(self) -> list[str]:
        """Return the repair issues currently raised."""
        return sorted(self._raised)

    @callback
    def async_start(self) -> None:
        """Start streaming and the timer that checks lag, staleness and issues."""
        self.entry.async_create_background_task(
            self.hass, self.client.run(), name=f"{DOMAIN} {self.api.host}"
        )
        self._unsub_stop = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_hass_stopping
        )
        self._schedule(self._clock() + self.throttle.interval)

    @callback
    def async_stop(self) -> None:
        """Stop the timers and clear issues; the client task ends with the entry."""
        self._cancel_timer()
        self._cancel_hold()
        if self._unsub_stop is not None:
            self._unsub_stop()
            self._unsub_stop = None
        for issue in Issue:
            self._clear_issue(issue)

    @callback
    def _async_hass_stopping(self, _event: Event) -> None:
        # The connection closes during shutdown; that is not a meter problem,
        # so freeze the entities instead of reporting stale data.
        LOGGER.debug(
            "Home Assistant is stopping; pausing updates for %s", self.api.host
        )
        self._unsub_stop = None
        self._stopping = True
        self._cancel_timer()
        self._cancel_hold()

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call update whenever a measurement is published or availability changes."""
        return self._add(self._listeners, update)

    @callback
    def async_add_status_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call update when the interval, transport or problem changes."""
        return self._add(self._status_listeners, update)

    @callback
    def on_measurement(self, measurement: Measurement) -> None:
        """Handle a frame from the client."""
        if self._stopping:
            return
        now = self._clock()
        gap = now - self._last_frame
        self._last_frame = now
        publish_now = self.throttle.offer(measurement, now)
        if (sent := measurement.timestamp) is not None:
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=dt_util.get_default_time_zone())
            self.freshness.telegram_delay((dt_util.utcnow() - sent).total_seconds())
        if not self.available:
            LOGGER.info("Measurements from %s are back after %.0fs", self.api.host, gap)
            self.available = True
            self._since[Issue.NO_DATA] = None
            self._clear_issue(Issue.NO_DATA)
            publish_now = True
            self._notify(self._status_listeners)
        if publish_now:
            self._publish_pending()
        elif self._hold is None and (deadline := self.throttle.deadline) is not None:
            self._hold = self.hass.loop.call_later(
                max(0.0, deadline - now), self._publish_held
            )

    @callback
    def on_transport(self, transport: Transport) -> None:
        """Handle a transport change from the client."""
        if self._stopping:
            return
        now = self._clock()
        if self.transport is Transport.WEBSOCKET:
            self.throttle.record_reconnect(now)
            LOGGER.info(
                "WebSocket to %s dropped; polling until it reconnects", self.api.host
            )
        elif transport is Transport.WEBSOCKET and self.transport is Transport.POLLING:
            LOGGER.info("WebSocket to %s is streaming again", self.api.host)
        LOGGER.debug("Transport for %s is now %s", self.api.host, transport)

        if transport is Transport.POLLING:
            if self._since[Issue.POLLING_FALLBACK] is None:
                self._since[Issue.POLLING_FALLBACK] = now
        else:
            self._since[Issue.POLLING_FALLBACK] = None
            self._clear_issue(Issue.POLLING_FALLBACK)

        self.transport = transport
        self._notify(self._status_listeners)

    @callback
    def on_auth_failed(self) -> None:
        """Handle a rejected token."""
        if self._stopping:
            return
        LOGGER.warning("%s rejected the token; reauthentication needed", self.api.host)
        self._token_rejected = True
        self._set_unavailable(self._clock())
        self.entry.async_start_reauth(self.hass)

    @callback
    def _tick(self, planned: float) -> None:
        now = self._clock()
        self.throttle.record_lag(now - planned, now)

        if self.available and now - self._last_frame > self.stale_after:
            LOGGER.warning(
                "No measurements from %s for %.0fs; entities are unavailable",
                self.api.host,
                now - self._last_frame,
            )
            self._set_unavailable(now)

        if self.throttle.evaluate(now):
            LOGGER.info(
                "Update interval for %s is now %ss (%s)",
                self.api.host,
                self.throttle.interval,
                self.throttle.reason,
            )
            self._notify(self._status_listeners)
        if self.throttle.interval > self.throttle.floor:
            if self._since[Issue.THROTTLED] is None:
                self._since[Issue.THROTTLED] = now
        else:
            self._since[Issue.THROTTLED] = None
            self._clear_issue(Issue.THROTTLED)

        self._check_issues(now)

        planned += self.throttle.interval
        if planned <= now:
            # Skip missed ticks instead of firing them in a burst.
            planned = now + self.throttle.interval
        self._schedule(planned)

    def _check_issues(self, now: float) -> None:
        for issue, after in ISSUE_AFTER.items():
            since = self._since[issue]
            if since is None or issue in self._raised or now - since < after:
                continue
            # Reauthentication already tells the user what is wrong.
            if issue is Issue.NO_DATA and self._token_rejected:
                continue
            LOGGER.warning(
                "%s: %s for %.0f minutes; raising a repair issue",
                self.api.host,
                issue,
                (now - since) / 60,
            )
            self._raised.add(issue)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id(issue),
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=issue.value,
                translation_placeholders={
                    "name": self.entry.title,
                    "minutes": f"{after / 60:.0f}",
                },
            )

    def _clear_issue(self, issue: Issue) -> None:
        if issue in self._raised:
            self._raised.discard(issue)
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(issue))

    def _issue_id(self, issue: Issue) -> str:
        return f"{issue}_{self.entry.entry_id}"

    def _schedule(self, when: float) -> None:
        self._cancel_timer()
        self._timer = self.hass.loop.call_later(
            max(0.0, when - self._clock()), self._tick, when
        )

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    @callback
    def _publish_held(self) -> None:
        # The next frame is late; don't keep the held one back any longer.
        self._hold = None
        self._publish_pending(held=True)

    def _publish_pending(self, held: bool = False) -> None:
        self._cancel_hold()
        now = self._clock()
        if (latest := self.throttle.take(now)) is not None:
            # The held frame is always the latest one to arrive.
            self.freshness.published(now, self._last_frame, held)
            self._publish(latest)

    def _cancel_hold(self) -> None:
        if self._hold is not None:
            self._hold.cancel()
            self._hold = None

    def _set_unavailable(self, now: float) -> None:
        if self.available:
            self._since[Issue.NO_DATA] = now
        self.available = False
        # Never publish a frame from before the gap.
        self.throttle.discard()
        self._cancel_hold()
        self.freshness.interrupted()
        self._notify(self._listeners)
        self._notify(self._status_listeners)

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
