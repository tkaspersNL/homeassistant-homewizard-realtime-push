"""Client for the HomeWizard API v2 measurement push.

Subscribes to measurements over the WebSocket. While the WebSocket is down it
polls the REST API instead and keeps retrying the WebSocket with backoff.
No Home Assistant imports, so it can be tested with a plain aiohttp server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from enum import StrEnum
import json
import logging
import random
import ssl
from typing import Any, Protocol

import aiohttp
from homewizard_energy import HomeWizardEnergyV2
from homewizard_energy.errors import RequestError, UnauthorizedError
from homewizard_energy.models import Measurement
from homewizard_energy.v2.cacert import CACERT
from mashumaro.exceptions import InvalidFieldValue, MissingField

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10.0
HANDSHAKE_TIMEOUT = 10.0
# Older meters send a telegram every 10s; allow a few to go missing.
RECEIVE_TIMEOUT = 35.0
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0
BACKOFF_JITTER = 0.2
WS_URL = "wss://{host}/api/ws"


class Transport(StrEnum):
    """How measurements currently reach us."""

    CONNECTING = "connecting"
    WEBSOCKET = "websocket"
    POLLING = "polling"


class ClientListener(Protocol):
    """Receives client events. Called from the event loop."""

    def on_measurement(self, measurement: Measurement) -> None:
        """Handle a new measurement."""

    def on_transport(self, transport: Transport) -> None:
        """Handle a transport change."""

    def on_auth_failed(self) -> None:
        """Handle a rejected token."""


class ProtocolError(Exception):
    """The device sent something we did not expect."""


def backoff_delay(attempt: int, rand: Callable[[], float] = random.random) -> float:
    """Return the reconnect delay for a zero-based attempt number."""
    base = min(BACKOFF_MAX, BACKOFF_MIN * 2.0**attempt)
    # Jitter downwards only, so the cap stays a cap.
    return base * (1 - BACKOFF_JITTER * rand())


async def build_ssl_context() -> ssl.SSLContext:
    """Build a TLS context that trusts only the HomeWizard CA."""

    def _build() -> ssl.SSLContext:
        context = ssl.create_default_context(cadata=CACERT)
        context.verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN
        # Device certificates name the serial, not the IP address.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    # Loading certificates is blocking I/O.
    return await asyncio.get_running_loop().run_in_executor(None, _build)


class RealtimeClient:
    """Stream measurements from one device."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api: HomeWizardEnergyV2,
        token: str,
        listener: ClientListener,
        poll_interval: Callable[[], float],
    ) -> None:
        """Set up the client; call run() to start it."""
        self._session = session
        self._api = api
        self._token = token
        self._listener = listener
        self._poll_interval = poll_interval
        self._transport = Transport.CONNECTING
        self._poll_task: asyncio.Task[None] | None = None
        self._auth_failed = asyncio.Event()
        self._streaming = False

    @property
    def transport(self) -> Transport:
        """Return the current transport."""
        return self._transport

    async def run(self) -> None:
        """Run until cancelled or the token is rejected."""
        ssl_context = await build_ssl_context()
        attempt = 0
        try:
            while not self._auth_failed.is_set():
                try:
                    await self._run_websocket(ssl_context)
                except (aiohttp.ClientError, TimeoutError, ProtocolError) as err:
                    _LOGGER.debug("WebSocket to %s ended: %r", self._api.host, err)

                if self._streaming:
                    attempt = 0
                    self._streaming = False
                self._start_polling()
                await self._wait(backoff_delay(attempt))
                attempt += 1
        finally:
            await self._stop_polling()

    async def _run_websocket(self, ssl_context: ssl.SSLContext) -> None:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            ws = await self._session.ws_connect(
                WS_URL.format(host=self._api.host),
                ssl=ssl_context,
                timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=5.0),
            )
        async with ws:
            await self._expect(ws, "authorization_requested")
            await ws.send_json({"type": "authorization", "data": self._token})
            await self._expect(ws, "authorized")
            await ws.send_json({"type": "subscribe", "data": "measurement"})
            while True:
                message = await self._receive(ws, RECEIVE_TIMEOUT)
                if message.get("type") == "measurement":
                    await self._handle_measurement(message.get("data"))

    async def _handle_measurement(self, data: Any) -> None:
        if not isinstance(data, dict):
            raise ProtocolError(f"Measurement without data: {data!r}")
        try:
            measurement = Measurement.from_dict(data)
        except (InvalidFieldValue, MissingField, TypeError, ValueError) as err:
            _LOGGER.debug("Skipping unreadable measurement: %r", err)
            return
        if not self._streaming:
            self._streaming = True
            await self._stop_polling()
            self._set_transport(Transport.WEBSOCKET)
            _LOGGER.debug("Streaming measurements from %s", self._api.host)
        self._listener.on_measurement(measurement)

    async def _expect(self, ws: aiohttp.ClientWebSocketResponse, kind: str) -> None:
        message = await self._receive(ws, HANDSHAKE_TIMEOUT)
        if message.get("type") != kind:
            raise ProtocolError(f"Expected {kind}, got {message!r}")

    async def _receive(
        self, ws: aiohttp.ClientWebSocketResponse, timeout: float
    ) -> dict[str, Any]:
        msg = await ws.receive(timeout)
        if msg.type is not aiohttp.WSMsgType.TEXT:
            raise ProtocolError(f"Connection closed ({msg.type.name})")
        try:
            message = json.loads(msg.data)
        except ValueError as err:
            raise ProtocolError("Invalid JSON") from err
        if not isinstance(message, dict):
            raise ProtocolError(f"Unexpected message: {message!r}")
        if message.get("type") == "error":
            raise ProtocolError(f"Device error: {message.get('data')!r}")
        return message

    def _start_polling(self) -> None:
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll())
        self._set_transport(Transport.POLLING)

    async def _stop_polling(self) -> None:
        if (task := self._poll_task) is None:
            return
        self._poll_task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _poll(self) -> None:
        while True:
            try:
                measurement = await self._api.measurement()
            except UnauthorizedError:
                _LOGGER.debug("Token rejected by %s", self._api.host)
                self._auth_failed.set()
                self._listener.on_auth_failed()
                return
            except RequestError as err:
                _LOGGER.debug("Polling %s failed: %r", self._api.host, err)
            else:
                self._listener.on_measurement(measurement)
            await asyncio.sleep(self._poll_interval())

    async def _wait(self, delay: float) -> None:
        # Sleep, but wake up early when the token gets rejected.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(delay):
                await self._auth_failed.wait()

    def _set_transport(self, transport: Transport) -> None:
        if transport is not self._transport:
            self._transport = transport
            self._listener.on_transport(transport)
