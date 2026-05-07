"""Peer server + client end-to-end over localhost WebSocket.

Covers the round-trip path that the daemon will use under the hood: a server
binds, a client connects with the SAME shared secret, RPC is signed, verified,
dispatched, and the result is returned.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from omniclaw.peer.client import PeerClient, RemoteError
from omniclaw.peer.server import HandlerContext, PeerServer
from omniclaw.proto.types import LifecycleEvent


# ---- helpers --------------------------------------------------------------


async def _ping_handler(params: dict, ctx: HandlerContext) -> dict:
    return {
        "sent_ts_ms": params["ts_ms"],
        "recv_ts_ms": int(time.time() * 1000),
        "peer_ts_ms": int(time.time() * 1000),
    }


async def _hello_handler(params: dict, ctx: HandlerContext) -> dict:
    return {
        "schema_version": 1,
        "device_id": "mac-test-server",
        "role": "mac",
        "caps": ["tool:mac_screen_size", "tool:mac_screenshot"],
        "schema_min": 1,
        "schema_max": 1,
    }


async def _tools_invoke_handler(params: dict, ctx: HandlerContext) -> dict:
    if params.get("tool_name") == "mac_screen_size":
        return {"ok": True, "output": {"width": 1920, "height": 1080}}
    return {"ok": False, "error": "unknown_tool"}


async def _task_run_handler(params: dict, ctx: HandlerContext) -> dict:
    """Streams two lifecycle events then returns a final TaskResult."""
    run_id = params["run_id"]
    await ctx.emit_event(LifecycleEvent(run_id=run_id, status="started"))
    await ctx.emit_event(LifecycleEvent(run_id=run_id, status="thinking"))
    return {"run_id": run_id, "status": "completed", "output": {"echo": params.get("intent")}}


@pytest.fixture
def handlers() -> dict:
    return {
        "peer.hello": _hello_handler,
        "peer.ping": _ping_handler,
        "tools.invoke": _tools_invoke_handler,
        "task.run": _task_run_handler,
    }


# ---- tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_round_trip(shared_secret, free_port, handlers):
    server = PeerServer(
        device_id="mac-server",
        secret=shared_secret,
        handlers=handlers,
        host="127.0.0.1",
        port=free_port,
    )
    async with server.running():
        client = PeerClient(
            device_id="phone-client",
            secret=shared_secret,
            endpoint=f"ws://127.0.0.1:{free_port}",
        )
        async with client.connected():
            sent = int(time.time() * 1000)
            res = await client.call("peer.ping", {"ts_ms": sent}, timeout_s=2)
            assert res["sent_ts_ms"] == sent
            assert res["recv_ts_ms"] >= sent


@pytest.mark.asyncio
async def test_hello_returns_caps(shared_secret, free_port, handlers):
    server = PeerServer(
        device_id="mac-server",
        secret=shared_secret,
        handlers=handlers,
        host="127.0.0.1",
        port=free_port,
    )
    async with server.running():
        client = PeerClient(
            device_id="phone-client",
            secret=shared_secret,
            endpoint=f"ws://127.0.0.1:{free_port}",
        )
        async with client.connected():
            res = await client.call(
                "peer.hello",
                {"schema_version": 1, "device_id": "phone-client", "role": "android", "caps": []},
            )
            assert "tool:mac_screen_size" in res["caps"]
            assert res["role"] == "mac"


@pytest.mark.asyncio
async def test_tools_invoke_dispatch(shared_secret, free_port, handlers):
    server = PeerServer(
        device_id="mac-server",
        secret=shared_secret,
        handlers=handlers,
        host="127.0.0.1",
        port=free_port,
    )
    async with server.running():
        client = PeerClient(
            device_id="phone-client",
            secret=shared_secret,
            endpoint=f"ws://127.0.0.1:{free_port}",
        )
        async with client.connected():
            res = await client.call(
                "tools.invoke",
                {"tool_name": "mac_screen_size", "args": {}, "deadline_ms": 5000},
            )
            assert res["ok"] is True
            assert res["output"] == {"width": 1920, "height": 1080}


@pytest.mark.asyncio
async def test_unknown_method_returns_error(shared_secret, free_port, handlers):
    server = PeerServer(
        device_id="mac-server",
        secret=shared_secret,
        handlers=handlers,
        host="127.0.0.1",
        port=free_port,
    )
    async with server.running():
        client = PeerClient(
            device_id="phone-client",
            secret=shared_secret,
            endpoint=f"ws://127.0.0.1:{free_port}",
        )
        async with client.connected():
            with pytest.raises(RemoteError) as excinfo:
                await client.call("does.not.exist", {})
            assert excinfo.value.code == "unknown_method"


@pytest.mark.asyncio
async def test_wrong_secret_is_rejected(shared_secret, free_port, handlers):
    """Both sides reject each other's signatures, so the impostor's call
    just times out (the server's auth_failed response is signed with the
    server's secret which the client also rejects)."""
    server = PeerServer(
        device_id="mac-server",
        secret=shared_secret,
        handlers=handlers,
        host="127.0.0.1",
        port=free_port,
    )
    async with server.running():
        wrong_client = PeerClient(
            device_id="impostor",
            secret=b"\xFF" * 32,  # different from shared_secret
            endpoint=f"ws://127.0.0.1:{free_port}",
        )
        async with wrong_client.connected():
            with pytest.raises(asyncio.TimeoutError):
                await wrong_client.call("peer.ping", {"ts_ms": 1}, timeout_s=0.5)


@pytest.mark.asyncio
async def test_task_run_streams_events_and_result(shared_secret, free_port, handlers):
    server = PeerServer(
        device_id="mac-server",
        secret=shared_secret,
        handlers=handlers,
        host="127.0.0.1",
        port=free_port,
    )
    async with server.running():
        client = PeerClient(
            device_id="phone-client",
            secret=shared_secret,
            endpoint=f"ws://127.0.0.1:{free_port}",
        )
        async with client.connected():
            run_id = "test-run-001"
            params = {
                "run_id": run_id,
                "intent": "say_hello",
                "args": {},
                "allow_remote_tools": True,
                "deadline_ms": 5000,
            }
            seen_events: list[dict] = []
            async with client.stream("task.run", params) as (queue, fut):
                # collect events until the future completes
                while not fut.done():
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=2)
                    except asyncio.TimeoutError:
                        break
                    seen_events.append(ev)
                # drain remaining events
                while not queue.empty():
                    seen_events.append(queue.get_nowait())
                result = await asyncio.wait_for(fut, timeout=2)
            assert result["status"] == "completed"
            assert result["output"] == {"echo": "say_hello"}
            statuses = [ev.get("status") for ev in seen_events if ev.get("type") == "lifecycle"]
            assert "started" in statuses
            assert "thinking" in statuses
