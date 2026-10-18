"""Tests for the WebSocket client against a local aiohttp server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
import ssl
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homewizard_energy.errors import RequestError, UnauthorizedError
from homewizard_energy.models import Measurement
import pytest

from custom_components.homewizard_realtime_push import client as client_module
from custom_components.homewizard_realtime_push.client import (
    BACKOFF_MAX,
    RealtimeClient,
    Transport,
    backoff_delay,
    build_ssl_context,
)

# The fake device listens on localhost.
pytestmark = pytest.mark.usefixtures("socket_enabled")

TOKEN = "test-token"
Handler = Callable[[web.WebSocketResponse], Awaitable[None]]


class Recorder:
    """Collects listener calls."""

    def __init__(self) -> None:
        self.measurements: list[Measurement] = []
        self.transports: list[Transport] = []
        self.auth_failed = 0
        self.changed = asyncio.Event()

    def on_measurement(self, measurement: Measurement) -> None:
        self.measurements.append(measurement)
        self.changed.set()

    def on_transport(self, transport: Transport) -> None:
        self.transports.append(transport)
        self.changed.set()

    def on_auth_failed(self) -> None:
        self.auth_failed += 1
        self.changed.set()

    async def wait_for(self, check: Callable[[], bool]) -> None:
        async with asyncio.timeout(5):
            while not check():
                self.changed.clear()
                await self.changed.wait()


class FakeDevice:
    """A local WebSocket server that runs one scripted handler per connection."""

    def __init__(self, handlers: list[Handler]) -> None:
        self.handlers = handlers
        self.connections = 0
        self.received: list[Any] = []

    async def ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        handler = self.handlers[min(self.connections, len(self.handlers) - 1)]
        self.connections += 1
        await handler(ws)
        return ws

    async def handshake(self, ws: web.WebSocketResponse) -> None:
        await ws.send_json(
            {"type": "authorization_requested", "data": {"api_version": "2.0.0"}}
        )
        self.received.append(await ws.receive_json())
        await ws.send_json({"type": "authorized"})
        self.received.append(await ws.receive_json())


@pytest.fixture
def no_backoff() -> AsyncIterator[None]:
    with patch.object(client_module, "backoff_delay", return_value=0.01):
        yield


@pytest.fixture
def fast_poll() -> float:
    return 0.01


async def start(
    device: FakeDevice, api: MagicMock, recorder: Recorder
) -> tuple[asyncio.Task[None], TestServer, ClientSession]:
    app = web.Application()
    app.router.add_get("/api/ws", device.ws)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    api.host = f"127.0.0.1:{server.port}"
    session = ClientSession()
    client = RealtimeClient(session, api, TOKEN, recorder, lambda: 0.01)
    with patch.object(client_module, "WS_URL", "ws://{host}/api/ws"):
        task = asyncio.create_task(client.run())
        await asyncio.sleep(0)
    return task, server, session


async def stop(
    task: asyncio.Task[None], server: TestServer, session: ClientSession
) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await session.close()
    await server.close()


def make_api(*results: Any) -> MagicMock:
    api = MagicMock()
    api.measurement = AsyncMock(side_effect=list(results) or None)
    if not results:
        api.measurement.return_value = Measurement.from_dict({"power_w": 1})
    return api


@pytest.fixture(autouse=True)
def ws_url() -> AsyncIterator[None]:
    with patch.object(client_module, "WS_URL", "ws://{host}/api/ws"):
        yield


async def test_streams_measurements() -> None:
    async def handler(ws: web.WebSocketResponse) -> None:
        await device.handshake(ws)
        await ws.send_json({"type": "measurement", "data": {"power_w": -120}})
        await ws.send_json({"type": "measurement", "data": {"power_w": 340.5}})
        await ws.receive()

    device = FakeDevice([handler])
    recorder = Recorder()
    api = make_api()
    task, server, session = await start(device, api, recorder)
    try:
        await recorder.wait_for(lambda: len(recorder.measurements) == 2)
    finally:
        await stop(task, server, session)

    assert [m.power_w for m in recorder.measurements] == [-120, 340.5]
    assert recorder.transports == [Transport.WEBSOCKET]
    assert device.received == [
        {"type": "authorization", "data": TOKEN},
        {"type": "subscribe", "data": "measurement"},
    ]
    api.measurement.assert_not_called()


@pytest.mark.usefixtures("no_backoff")
async def test_reconnects_after_close_and_polls_meanwhile() -> None:
    async def first(ws: web.WebSocketResponse) -> None:
        await device.handshake(ws)
        await ws.send_json({"type": "measurement", "data": {"power_w": 1}})
        await ws.close()

    async def second(ws: web.WebSocketResponse) -> None:
        await device.handshake(ws)
        await ws.send_json({"type": "measurement", "data": {"power_w": 3}})
        await ws.receive()

    device = FakeDevice([first, second])
    recorder = Recorder()
    task, server, session = await start(device, make_api(), recorder)
    try:
        await recorder.wait_for(
            lambda: (
                recorder.transports[-1:] == [Transport.WEBSOCKET]
                and len(recorder.transports) == 3
            )
        )
    finally:
        await stop(task, server, session)

    assert recorder.transports == [
        Transport.WEBSOCKET,
        Transport.POLLING,
        Transport.WEBSOCKET,
    ]
    assert device.connections == 2


@pytest.mark.usefixtures("no_backoff")
@pytest.mark.parametrize(
    "script",
    [
        pytest.param([{"type": "hello"}], id="unexpected-first-message"),
        pytest.param(
            [{"type": "authorization_requested"}, {"type": "error", "data": {}}],
            id="device-error",
        ),
        pytest.param(["not json"], id="invalid-json"),
        pytest.param([[1, 2]], id="not-an-object"),
    ],
)
async def test_bad_handshake_falls_back_to_polling(script: list[Any]) -> None:
    async def handler(ws: web.WebSocketResponse) -> None:
        for item in script:
            if isinstance(item, str):
                await ws.send_str(item)
            else:
                await ws.send_json(item)
            if item == {"type": "authorization_requested"}:
                await ws.receive()
        await ws.receive()

    device = FakeDevice([handler])
    recorder = Recorder()
    task, server, session = await start(device, make_api(), recorder)
    try:
        await recorder.wait_for(lambda: len(recorder.measurements) >= 2)
    finally:
        await stop(task, server, session)

    assert recorder.transports == [Transport.POLLING]
    assert device.connections >= 1


@pytest.mark.usefixtures("no_backoff")
async def test_bad_frames() -> None:
    async def first(ws: web.WebSocketResponse) -> None:
        await device.handshake(ws)
        # Unreadable values are skipped; the stream continues.
        await ws.send_json({"type": "measurement", "data": {"power_w": "lots"}})
        await ws.send_json({"type": "something_else"})
        await ws.send_json({"type": "measurement", "data": {"power_w": 5}})
        # A frame without data breaks the connection.
        await ws.send_json({"type": "measurement"})
        await ws.receive()

    async def idle(ws: web.WebSocketResponse) -> None:
        await ws.receive()

    device = FakeDevice([first, idle])
    recorder = Recorder()
    api = make_api()
    api.measurement.return_value = Measurement.from_dict({"power_w": 9})
    task, server, session = await start(device, api, recorder)
    try:
        await recorder.wait_for(lambda: Transport.POLLING in recorder.transports)
    finally:
        await stop(task, server, session)

    assert recorder.measurements[0].power_w == 5


@pytest.mark.usefixtures("no_backoff")
async def test_receive_timeout_reconnects() -> None:
    async def silent(ws: web.WebSocketResponse) -> None:
        await device.handshake(ws)
        await ws.receive()

    device = FakeDevice([silent])
    recorder = Recorder()
    with patch.object(client_module, "RECEIVE_TIMEOUT", 0.05):
        task, server, session = await start(device, make_api(), recorder)
        try:
            await recorder.wait_for(lambda: device.connections >= 2)
        finally:
            await stop(task, server, session)


@pytest.mark.usefixtures("no_backoff")
async def test_rejected_token_stops_client() -> None:
    async def refuse(ws: web.WebSocketResponse) -> None:
        await ws.close()

    device = FakeDevice([refuse])
    recorder = Recorder()
    api = make_api(UnauthorizedError("Token rejected"))
    task, server, session = await start(device, api, recorder)
    try:
        async with asyncio.timeout(5):
            await task
    finally:
        await stop(task, server, session)

    assert recorder.auth_failed == 1


@pytest.mark.usefixtures("no_backoff")
async def test_polling_survives_request_errors() -> None:
    async def refuse(ws: web.WebSocketResponse) -> None:
        await ws.close()

    device = FakeDevice([refuse])
    recorder = Recorder()
    api = make_api(
        RequestError("timeout"),
        Measurement.from_dict({"power_w": 7}),
        Measurement.from_dict({"power_w": 8}),
    )
    task, server, session = await start(device, api, recorder)
    try:
        await recorder.wait_for(lambda: len(recorder.measurements) >= 1)
    finally:
        await stop(task, server, session)

    assert recorder.measurements[0].power_w == 7


@pytest.mark.usefixtures("no_backoff")
@pytest.mark.parametrize(
    "poll_error",
    [
        # The library parses whatever the meter answers, also an error page
        # while it boots.
        pytest.param(ValueError("Expecting value"), id="unreadable-response"),
        pytest.param(RuntimeError("unexpected"), id="unexpected-error"),
    ],
)
async def test_failed_poll_does_not_stop_the_stream(
    poll_error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    async def refuse(ws: web.WebSocketResponse) -> None:
        await ws.close()

    async def stream(ws: web.WebSocketResponse) -> None:
        await device.handshake(ws)
        await ws.send_json({"type": "measurement", "data": {"power_w": -5}})
        await ws.receive()

    device = FakeDevice([refuse, stream])
    recorder = Recorder()
    api = make_api(poll_error, *[Measurement.from_dict({"power_w": 9})] * 50)
    task, server, session = await start(device, api, recorder)
    try:
        await recorder.wait_for(
            lambda: any(m.power_w == -5 for m in recorder.measurements)
        )
        await asyncio.sleep(0.05)
        assert not task.done()
    finally:
        await stop(task, server, session)

    assert recorder.transports == [Transport.POLLING, Transport.WEBSOCKET]


@pytest.mark.usefixtures("no_backoff")
async def test_unreachable_device_polls() -> None:
    recorder = Recorder()
    api = make_api()
    session = ClientSession()
    api.host = "127.0.0.1:1"
    client = RealtimeClient(session, api, TOKEN, recorder, lambda: 0.01)
    task = asyncio.create_task(client.run())
    try:
        await recorder.wait_for(lambda: len(recorder.measurements) >= 2)
        assert client.transport is Transport.POLLING
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await session.close()


def test_backoff_grows_and_caps() -> None:
    delays = [backoff_delay(attempt, rand=lambda: 0.0) for attempt in range(8)]
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]
    assert backoff_delay(10, rand=lambda: 1.0) == pytest.approx(BACKOFF_MAX * 0.8)


async def test_ssl_context_pins_homewizard_ca() -> None:
    context = await build_ssl_context()
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert not context.check_hostname
    assert context.get_ca_certs()
