"""PeerDaemon control plane: start daemon, talk to its Unix socket via
peer_cli helpers, exercise self-loopback ping and pairing flow."""
from __future__ import annotations

import asyncio
import json
import socket
import stat
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from omniclaw.peer.daemon import LOCAL_CAPS, PeerDaemon
from omniclaw.peer.pair import (
    IdentityRecord,
    create_pairing_payload,
    payload_to_uri,
)


# ---- helpers --------------------------------------------------------------


async def _send_unix(sock_path: Path, payload: dict, timeout_s: float = 5.0) -> dict:
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(sock_path)),
        timeout=timeout_s,
    )
    try:
        writer.write(json.dumps(payload).encode("utf-8") + b"\n")
        writer.write_eof()
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=timeout_s)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    text = raw.decode("utf-8").strip()
    return json.loads(text.splitlines()[0])


@pytest_asyncio.fixture
async def running_daemon(shared_secret, free_port):
    """Spin up a PeerDaemon with a tmp Unix socket, no peer paired.

    AF_UNIX paths are capped at ~104 bytes on macOS, so we don't use
    pytest's tmp_path (which is deep under /private/var/folders/...).
    """
    import secrets as _s
    import tempfile

    sock_dir = Path(tempfile.gettempdir()) / f"jarvis-test-{_s.token_hex(4)}"
    sock_dir.mkdir(parents=True, exist_ok=True)
    sock = sock_dir / "p.sock"
    identity = IdentityRecord(device_id="mac-test-001", role="mac", priority=10)
    daemon = PeerDaemon(
        identity=identity,
        secret=shared_secret,
        peer_port=free_port,
        sock_path=sock,
        peer_record=None,
        host="127.0.0.1",
    )
    task = asyncio.create_task(daemon.run())
    # wait for socket to appear
    for i in range(100):
        if sock.exists():
            break
        if task.done():
            try:
                exc = task.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                exc = None
            raise AssertionError(f"daemon task crashed during startup: {exc!r}")
        await asyncio.sleep(0.02)
    assert sock.exists(), f"daemon never created its socket after 2s; task_done={task.done()}"

    yield daemon, sock

    daemon.stop()
    try:
        await asyncio.wait_for(task, timeout=2)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    finally:
        try:
            sock_dir.rmdir()
        except OSError:
            pass


# ---- tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_socket_has_owner_only_perms(running_daemon):
    _daemon, sock = running_daemon
    mode = sock.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


@pytest.mark.asyncio
async def test_status_op(running_daemon):
    _daemon, sock = running_daemon
    out = await _send_unix(sock, {"op": "status"})
    assert out["ok"] is True
    assert out["device_id"] == "mac-test-001"
    assert out["role"] == "mac"
    assert out["peer_paired"] is False
    assert out["sock_path"] == str(sock)
    assert isinstance(out["ws_port"], int)


@pytest.mark.asyncio
async def test_unknown_op_returns_error(running_daemon):
    _daemon, sock = running_daemon
    out = await _send_unix(sock, {"op": "nonexistent"})
    assert out["ok"] is False
    assert "unknown_op" in out["error"]


@pytest.mark.asyncio
async def test_ping_self_loopback(running_daemon):
    """Daemon connects a fresh client to its own server; round-trips peer.ping."""
    _daemon, sock = running_daemon
    out = await _send_unix(sock, {"op": "ping_self"})
    assert out["ok"] is True
    assert "rtt_ms" in out
    assert out["result"]["sent_ts_ms"] >= 0


@pytest.mark.asyncio
async def test_ping_peer_without_pairing_fails(running_daemon):
    _daemon, sock = running_daemon
    out = await _send_unix(sock, {"op": "ping_peer"})
    assert out["ok"] is False
    assert out["error"] == "no_peer_paired"


@pytest.mark.asyncio
async def test_caps_without_pairing_fails(running_daemon):
    _daemon, sock = running_daemon
    out = await _send_unix(sock, {"op": "caps"})
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_verify_op_returns_check_dict(running_daemon):
    _daemon, sock = running_daemon
    out = await _send_unix(sock, {"op": "verify"})
    assert out["ok"] is True
    checks = out["checks"]
    assert checks["daemon_alive"] is True
    assert checks["sock_perms_ok"] is True
    assert checks["ping_self"] is True
    assert checks["peer_paired"] is False


@pytest.mark.asyncio
async def test_pair_show_emits_uri(running_daemon):
    _daemon, sock = running_daemon
    out = await _send_unix(sock, {"op": "pair_show", "host": "host.example", "port": 18790})
    assert out["ok"] is True
    assert out["uri"].startswith("jarvis://pair?")
    assert out["host"] == "host.example"
    assert out["port"] == 18790


@pytest.mark.asyncio
async def test_pair_accept_then_reload_marks_peer_paired(running_daemon, tmp_path):
    daemon, sock = running_daemon
    # Generate a pairing URI from a fake peer
    payload = create_pairing_payload(
        host="127.0.0.1",
        port=daemon._server.actual_port,
        role="android",
        device_id="phone-001",
    )
    uri = payload_to_uri(payload)
    out = await _send_unix(sock, {"op": "pair_accept", "uri": uri})
    assert out["ok"] is True, out
    assert out["peer_device_id"] == "phone-001"
    # daemon now has peer_record set
    status = await _send_unix(sock, {"op": "status"})
    assert status["peer_paired"] is True
    assert status["peer_device_id"] == "phone-001"


@pytest.mark.asyncio
async def test_local_caps_advertised(running_daemon):
    """The daemon's hello result should include LOCAL_CAPS."""
    daemon, sock = running_daemon
    # Ping ourselves with peer.hello.
    from omniclaw.peer.client import PeerClient

    client = PeerClient(
        device_id="self-tester",
        secret=daemon.secret,
        endpoint=f"ws://127.0.0.1:{daemon._server.actual_port}",
    )
    async with client.connected():
        res = await client.call(
            "peer.hello",
            {"schema_version": 1, "device_id": "self-tester", "role": "android", "caps": []},
        )
    assert res["role"] == "mac"
    assert "tool:mac_screen_size" in res["caps"]
    for cap in LOCAL_CAPS:
        assert cap in res["caps"]
