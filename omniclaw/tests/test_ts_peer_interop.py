"""Wire-format compatibility test between the Python and JavaScript peer impls.

Both directions are exercised:

  * Python PeerClient -> JS PeerServer driver
    Validates that the JS server accepts our HMAC envelopes and that its
    responses pass our `verify_envelope` check.

  * JS peer client driver -> Python PeerServer
    Validates the inverse: JS-generated envelopes verify on the Python side.

The shared secret is randomized per test, so a successful HMAC verify proves
both sides serialize the same `signed_dict` bytes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_ROOT = ROOT / "android" / "app" / "src" / "main" / "assets" / "node"
SERVER_DRIVER = JS_ROOT / "test" / "peer_server_driver.js"
CLIENT_DRIVER = JS_ROOT / "test" / "peer_client_driver.js"


def _has_node_and_ws() -> bool:
    if shutil.which("node") is None:
        return False
    if not (JS_ROOT / "node_modules" / "ws").is_dir():
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _has_node_and_ws(),
    reason="node + ws npm package required for cross-impl interop",
)


def _free_port() -> int:
    """Return an OS-assigned free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _new_secret() -> bytes:
    return secrets.token_bytes(32)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Direction 1: Python client -> JS server driver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_python_client_against_js_server() -> None:
    secret = _new_secret()
    port = _free_port()
    proc = subprocess.Popen(
        ["node", str(SERVER_DRIVER), _b64url(secret), "js-test-server", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(JS_ROOT),
    )
    try:
        # Wait for "READY <port>" within a few seconds.
        ready_re = re.compile(r"^READY (\d+)$")
        actual_port: int | None = None
        for _ in range(50):
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    err = proc.stderr.read() if proc.stderr else ""
                    pytest.fail(f"JS server exited early: {err}")
                await asyncio.sleep(0.1)
                continue
            m = ready_re.match(line.strip())
            if m:
                actual_port = int(m.group(1))
                break
        assert actual_port is not None, "JS server never printed READY"

        from omniclaw.peer.client import PeerClient

        client = PeerClient(
            device_id="py-test-client",
            secret=secret,
            endpoint=f"ws://127.0.0.1:{actual_port}",
        )
        async with client.connected():
            # peer.ping
            pong = await client.call("peer.ping", {"ts_ms": 1234})
            assert pong["sent_ts_ms"] == 1234
            assert "recv_ts_ms" in pong and pong["recv_ts_ms"] > 0

            # peer.hello
            hello = await client.call(
                "peer.hello",
                {"schema_version": 1, "device_id": "py-test-client", "role": "mac", "caps": ["t"]},
            )
            assert hello["device_id"] == "js-test-server"
            assert hello["role"] == "android"
            assert "tool:test" in hello["caps"]

            # tools.invoke
            invoked = await client.call(
                "tools.invoke",
                {"tool_name": "echo", "args": {"hi": 1}, "deadline_ms": 5_000},
            )
            assert invoked["ok"] is True
            assert invoked["output"]["echo"]["tool_name"] == "echo"
            assert invoked["output"]["source"] == "js"

            # task.run streamed events
            events: list[dict] = []
            async with client.stream("task.run", {
                "run_id": "r1", "intent": "noop", "args": {},
                "allow_remote_tools": False, "deadline_ms": 5_000,
            }) as (queue, result_future):
                async def drain():
                    while True:
                        try:
                            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
                        except asyncio.TimeoutError:
                            return
                        events.append(ev)
                        if result_future.done():
                            return
                drain_task = asyncio.create_task(drain())
                result = await asyncio.wait_for(result_future, timeout=5.0)
                drain_task.cancel()
            assert result["run_id"] == "r1"
            assert result["status"] == "completed"
            # We should have seen at least one lifecycle event.
            assert any(e.get("type") == "lifecycle" for e in events)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# Direction 2: JS client driver -> Python server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_js_client_against_python_server() -> None:
    from omniclaw.peer.server import PeerServer, HandlerContext
    from omniclaw.proto.types import LifecycleEvent, AssistantEvent

    secret = _new_secret()

    async def hello_handler(params, ctx):
        return {
            "schema_version": 1, "device_id": "py-test-server", "role": "mac",
            "caps": ["tool:test", "tool:echo"], "schema_min": 1, "schema_max": 1,
        }

    async def ping_handler(params, ctx):
        import time
        return {
            "sent_ts_ms": params["ts_ms"],
            "recv_ts_ms": int(time.time() * 1000),
            "peer_ts_ms": int(time.time() * 1000),
        }

    async def invoke_handler(params, ctx):
        return {"ok": True, "output": {"source": "py", "echo": params}}

    async def task_run_handler(params, ctx):
        run_id = params["run_id"]
        await ctx.emit_event(LifecycleEvent(run_id=run_id, status="started"))
        await ctx.emit_event(AssistantEvent(run_id=run_id, text_delta="hello from py", final=True))
        await ctx.emit_event(LifecycleEvent(run_id=run_id, status="completed"))
        return {"run_id": run_id, "status": "completed", "output": {"ok": True}}

    server = PeerServer(
        device_id="py-test-server",
        secret=secret,
        handlers={
            "peer.hello": hello_handler,
            "peer.ping": ping_handler,
            "tools.invoke": invoke_handler,
            "task.run": task_run_handler,
        },
        host="127.0.0.1",
        port=0,
    )
    async with server.running():
        port = server.actual_port
        proc = await asyncio.create_subprocess_exec(
            "node", str(CLIENT_DRIVER),
            _b64url(secret), f"ws://127.0.0.1:{port}", "js-test-client",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(JS_ROOT),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except asyncio.TimeoutError:
            proc.kill()
            pytest.fail("JS client driver timed out")
        if proc.returncode != 0:
            pytest.fail(f"JS client driver exited {proc.returncode}: {stderr.decode()}")
        # Last non-empty stdout line should be JSON.
        out_lines = [ln for ln in stdout.decode().splitlines() if ln.strip()]
        assert out_lines, "JS client driver produced no stdout"
        result = json.loads(out_lines[-1])
        assert result["ping"]["sent_ts_ms"] > 0
        assert result["hello"]["device_id"] == "py-test-server"
        assert result["inv"]["ok"] is True
        # Streamed events.
        assert result["result"]["status"] == "completed"
        assert any(e.get("type") == "lifecycle" for e in result["events"])
