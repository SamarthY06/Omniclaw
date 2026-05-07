"""Peer WebSocket server.

Validates HMAC + replay window on every incoming envelope, dispatches RPC
methods to handler callables, supports streaming events for `task.run` runs.

Handlers are async callables: `async def handler(params: dict, context: HandlerContext) -> dict`

For task.run-shaped handlers, the handler may instead be an async generator yielding
TaskEvent objects with a final TaskResult. The server emits each yielded event over
the WS connection that initiated the run.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import websockets
from websockets.asyncio.server import ServerConnection, serve

from omniclaw.proto.crypto import sign_envelope, verify_envelope
from omniclaw.proto.types import (
    AuthBlock,
    Envelope,
    LifecycleEvent,
    AssistantEvent,
    ToolEvent,
    TaskResult,
    SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Handler protocol


@dataclass
class HandlerContext:
    """Per-RPC context passed to handlers."""
    method: str
    request_id: str
    peer_device_id: str
    server: "PeerServer"
    connection: ServerConnection

    async def emit_event(self, payload_model: LifecycleEvent | AssistantEvent | ToolEvent) -> None:
        """Send a `task.event` event tied to this run's request_id."""
        await self.server._send_event(self.connection, payload_model)


Handler = Callable[[dict[str, Any], HandlerContext], Awaitable[dict[str, Any] | None]]


class PeerServerError(Exception):
    pass


# ---------------------------------------------------------------------------
# Server


class PeerServer:
    def __init__(
        self,
        device_id: str,
        secret: bytes,
        handlers: dict[str, Handler],
        host: str = "0.0.0.0",
        port: int = 18790,
        max_skew_ms: int = 60_000,
    ) -> None:
        self.device_id = device_id
        self.secret = secret
        self.handlers = handlers
        self.host = host
        self.port = port
        self.max_skew_ms = max_skew_ms
        self._server: Optional[Any] = None
        self._stop_event: Optional[asyncio.Event] = None

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        """Bind, accept connections. Returns once the server is ready."""
        self._stop_event = asyncio.Event()
        self._server = await serve(self._handle, self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._stop_event:
            self._stop_event.set()

    @property
    def actual_port(self) -> int:
        """The port we're bound to. May differ from `self.port` if 0 was requested."""
        if not self._server:
            return self.port
        sockets = self._server.sockets
        if not sockets:
            return self.port
        return sockets[0].getsockname()[1]

    @asynccontextmanager
    async def running(self) -> AsyncIterator["PeerServer"]:
        await self.start()
        try:
            yield self
        finally:
            await self.stop()

    # ---- per-connection loop --------------------------------------------

    async def _handle(self, connection: ServerConnection) -> None:
        async for raw in connection:
            try:
                env = self._parse(raw if isinstance(raw, str) else raw.decode("utf-8"))
            except Exception as exc:
                await self._send_error(connection, request_id=None, code="parse_error", detail=str(exc))
                continue

            ok, reason = verify_envelope(env, self.secret, max_skew_ms=self.max_skew_ms)
            if not ok:
                await self._send_error(connection, request_id=env.id, code="auth_failed", detail=reason or "")
                continue

            if env.kind != "req":
                # We never receive res or event from the client side except for streamed
                # task events that the *client* would send back; not in v1 scope.
                continue

            handler = self.handlers.get(env.method)
            if not handler:
                await self._send_error(connection, request_id=env.id, code="unknown_method", detail=env.method)
                continue

            ctx = HandlerContext(
                method=env.method,
                request_id=env.id,
                peer_device_id=env.auth.device_id,
                server=self,
                connection=connection,
            )

            try:
                result = await handler(env.params, ctx)
            except Exception as exc:
                await self._send_error(connection, request_id=env.id, code="handler_error", detail=str(exc))
                continue

            await self._send_result(connection, request_id=env.id, method=env.method, result=result or {})

    # ---- send helpers ----------------------------------------------------

    async def _send_result(self, connection: ServerConnection, request_id: str, method: str, result: dict[str, Any]) -> None:
        env = self._make_envelope(kind="res", method=method, params=result, request_id=request_id)
        await connection.send(json.dumps(env.model_dump()))

    async def _send_event(self, connection: ServerConnection, event_model: LifecycleEvent | AssistantEvent | ToolEvent) -> None:
        env = self._make_envelope(
            kind="event",
            method="task.event",
            params=event_model.model_dump(exclude_none=True),
        )
        await connection.send(json.dumps(env.model_dump()))

    async def _send_error(self, connection: ServerConnection, request_id: str | None, code: str, detail: str = "") -> None:
        env = self._make_envelope(
            kind="res",
            method="error",
            params={"code": code, "detail": detail, "request_id": request_id},
            request_id=request_id,
        )
        try:
            await connection.send(json.dumps(env.model_dump()))
        except Exception:
            pass

    # ---- envelope build --------------------------------------------------

    def _make_envelope(
        self,
        *,
        kind: str,
        method: str,
        params: dict[str, Any],
        request_id: str | None = None,
    ) -> Envelope:
        env = Envelope(
            v=SCHEMA_VERSION,
            id=request_id or str(uuid.uuid4()),
            kind=kind,
            method=method,
            ts_ms=int(time.time() * 1000),
            params=params,
            auth=AuthBlock(device_id=self.device_id, hmac_sha256="0" * 64),
        )
        return sign_envelope(env, self.secret)

    def _parse(self, raw: str) -> Envelope:
        data = json.loads(raw)
        return Envelope.model_validate(data)
