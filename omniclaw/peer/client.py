"""Peer WebSocket client with reconnect, RPC + event streaming."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import websockets
from websockets.asyncio.client import ClientConnection, connect

from omniclaw.proto.crypto import sign_envelope, verify_envelope
from omniclaw.proto.types import (
    AuthBlock,
    Envelope,
    SCHEMA_VERSION,
)


class PeerClientError(Exception):
    pass


class RemoteError(PeerClientError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class _PendingCall:
    future: asyncio.Future[dict[str, Any]]
    method: str


class PeerClient:
    """One client = one peer. Manages a persistent WS, fans out RPCs."""

    def __init__(
        self,
        device_id: str,
        secret: bytes,
        endpoint: str,
        max_skew_ms: int = 60_000,
        connect_timeout_s: float = 5.0,
        backoff_ms_initial: int = 250,
        backoff_ms_max: int = 4000,
    ) -> None:
        self.device_id = device_id
        self.secret = secret
        self.endpoint = endpoint
        self.max_skew_ms = max_skew_ms
        self.connect_timeout_s = connect_timeout_s
        self._backoff_initial = backoff_ms_initial
        self._backoff_max = backoff_ms_max

        self._ws: Optional[ClientConnection] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._pending: dict[str, _PendingCall] = {}
        self._event_subscribers: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    # ---- connect / disconnect -------------------------------------------

    async def connect(self) -> None:
        if self._ws is not None:
            return
        self._ws = await asyncio.wait_for(connect(self.endpoint), timeout=self.connect_timeout_s)
        self._reader_task = asyncio.create_task(self._reader())

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(PeerClientError("client closed"))
        self._pending.clear()

    @asynccontextmanager
    async def connected(self) -> AsyncIterator["PeerClient"]:
        await self.connect()
        try:
            yield self
        finally:
            await self.close()

    # ---- RPC -------------------------------------------------------------

    async def call(self, method: str, params: dict[str, Any], timeout_s: float = 10) -> dict[str, Any]:
        if self._ws is None:
            await self.connect()
        env = self._make_envelope(kind="req", method=method, params=params)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[env.id] = _PendingCall(future=fut, method=method)
        await self._ws.send(json.dumps(env.model_dump()))
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        finally:
            self._pending.pop(env.id, None)

    @asynccontextmanager
    async def stream(self, method: str, params: dict[str, Any], timeout_s: float = 60) -> AsyncIterator[tuple[asyncio.Queue[dict[str, Any]], asyncio.Future[dict[str, Any]]]]:
        """Start an RPC, get an event queue + a future for the final result.

        Use for `task.run` where the server streams TaskEvent envelopes during execution.
        Identifies events by run_id taken from `params['run_id']`.
        """
        run_id = params.get("run_id")
        if not run_id:
            raise PeerClientError("stream() requires params.run_id")
        if self._ws is None:
            await self.connect()
        env = self._make_envelope(kind="req", method=method, params=params)
        loop = asyncio.get_running_loop()
        result_future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[env.id] = _PendingCall(future=result_future, method=method)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._event_subscribers[run_id] = queue
        await self._ws.send(json.dumps(env.model_dump()))
        try:
            yield queue, result_future
        finally:
            self._pending.pop(env.id, None)
            self._event_subscribers.pop(run_id, None)

    # ---- reader loop -----------------------------------------------------

    async def _reader(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                    env = Envelope.model_validate(data)
                except Exception:
                    continue
                ok, _ = verify_envelope(env, self.secret, max_skew_ms=self.max_skew_ms)
                if not ok:
                    continue
                if env.kind == "res":
                    await self._dispatch_result(env)
                elif env.kind == "event":
                    await self._dispatch_event(env)
        except websockets.ConnectionClosed:
            pass
        finally:
            for pending in self._pending.values():
                if not pending.future.done():
                    pending.future.set_exception(PeerClientError("connection closed"))
            self._pending.clear()

    async def _dispatch_result(self, env: Envelope) -> None:
        pending = self._pending.get(env.id)
        if not pending or pending.future.done():
            return
        if env.method == "error":
            pending.future.set_exception(
                RemoteError(env.params.get("code", "unknown"), env.params.get("detail", ""))
            )
            return
        pending.future.set_result(env.params)

    async def _dispatch_event(self, env: Envelope) -> None:
        run_id = env.params.get("run_id")
        if not run_id:
            return
        queue = self._event_subscribers.get(run_id)
        if queue:
            await queue.put(env.params)

    # ---- envelope build --------------------------------------------------

    def _make_envelope(self, *, kind: str, method: str, params: dict[str, Any]) -> Envelope:
        env = Envelope(
            v=SCHEMA_VERSION,
            id=str(uuid.uuid4()),
            kind=kind,
            method=method,
            ts_ms=int(time.time() * 1000),
            params=params,
            auth=AuthBlock(device_id=self.device_id, hmac_sha256="0" * 64),
        )
        return sign_envelope(env, self.secret)
