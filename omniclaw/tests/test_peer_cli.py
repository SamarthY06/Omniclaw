"""peer_cli.py end-to-end: subprocess against a running PeerDaemon.

We launch the real CLI script via `python tools/peer_cli.py ...` and assert it
prints the expected JSON to stdout, exit code as expected. This catches
argparse regressions and JSON-shape regressions for the agent.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from omniclaw.peer.daemon import PeerDaemon
from omniclaw.peer.pair import IdentityRecord


REPO_ROOT = Path(__file__).resolve().parents[2]
PEER_CLI = REPO_ROOT / "omniclaw" / "tools" / "peer_cli.py"


@pytest_asyncio.fixture
async def running_daemon_with_cli(shared_secret, free_port):
    import secrets as _s
    sock_dir = Path(tempfile.gettempdir()) / f"jarvis-cli-{_s.token_hex(4)}"
    sock_dir.mkdir(parents=True, exist_ok=True)
    sock = sock_dir / "p.sock"
    daemon = PeerDaemon(
        identity=IdentityRecord(device_id="mac-cli-test", role="mac", priority=10),
        secret=shared_secret,
        peer_port=free_port,
        sock_path=sock,
        peer_record=None,
        host="127.0.0.1",
    )
    task = asyncio.create_task(daemon.run())
    for _ in range(100):
        if sock.exists():
            break
        if task.done():
            raise AssertionError(f"daemon crashed: {task.exception()!r}")
        await asyncio.sleep(0.02)
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


def _run_cli(*args: str, sock: Path = None, timeout: float = 10) -> tuple[int, dict]:
    cmd = [sys.executable, str(PEER_CLI)]
    if sock is not None:
        cmd += ["--sock", str(sock)]
    cmd += list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = proc.stdout.strip()
    parsed = json.loads(out) if out else {}
    return proc.returncode, parsed


def test_cli_json_tools_no_daemon():
    """`--json-tools` should work without a daemon running."""
    rc, out = _run_cli("--json-tools")
    assert rc == 0
    assert out["ok"] is True
    tool_names = {t["name"] for t in out["tools"]}
    assert "peer_ping" in tool_names
    assert "peer_caps" in tool_names
    assert "peer_tools_invoke" in tool_names
    assert "peer_task_run" in tool_names
    assert "peer_pair_show" in tool_names
    assert "peer_pair_accept" in tool_names
    # every tool has a parameters dict and a sensitivity tag
    for tool in out["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool
        assert "sensitivity" in tool


def test_cli_no_command_returns_help():
    rc, out = _run_cli()
    assert rc != 0
    assert out["ok"] is False


def test_cli_status_without_daemon():
    """No daemon running -> CLI prints actionable error JSON."""
    rc, out = _run_cli("--sock", "/tmp/definitely-not-a-real-sock-12345.sock", "status")
    assert rc != 0
    assert out["ok"] is False
    assert "daemon" in out["error"].lower()


@pytest.mark.asyncio
async def test_cli_status_with_running_daemon(running_daemon_with_cli):
    daemon, sock = running_daemon_with_cli
    # Run subprocess in an executor so it doesn't block our event loop.
    loop = asyncio.get_running_loop()
    rc, out = await loop.run_in_executor(None, lambda: _run_cli("status", sock=sock))
    assert rc == 0, out
    assert out["ok"] is True
    assert out["device_id"] == "mac-cli-test"
    assert out["role"] == "mac"


@pytest.mark.asyncio
async def test_cli_ping_self_with_running_daemon(running_daemon_with_cli):
    _daemon, sock = running_daemon_with_cli
    loop = asyncio.get_running_loop()
    rc, out = await loop.run_in_executor(None, lambda: _run_cli("ping", "--self", sock=sock))
    assert rc == 0, out
    assert out["ok"] is True
    assert "rtt_ms" in out


@pytest.mark.asyncio
async def test_cli_verify_with_running_daemon(running_daemon_with_cli):
    _daemon, sock = running_daemon_with_cli
    loop = asyncio.get_running_loop()
    rc, out = await loop.run_in_executor(None, lambda: _run_cli("verify", sock=sock))
    assert rc == 0, out
    checks = out["checks"]
    assert checks["daemon_alive"] is True
    assert checks["sock_perms_ok"] is True
    assert checks["ping_self"] is True
    assert checks["peer_paired"] is False


@pytest.mark.asyncio
async def test_cli_ping_peer_without_pairing_fails(running_daemon_with_cli):
    _daemon, sock = running_daemon_with_cli
    loop = asyncio.get_running_loop()
    rc, out = await loop.run_in_executor(None, lambda: _run_cli("ping", sock=sock))
    assert rc != 0
    assert out["ok"] is False
    assert "no_peer_paired" in out["error"]


@pytest.mark.asyncio
async def test_cli_tools_invoke_args_must_be_object(running_daemon_with_cli):
    _daemon, sock = running_daemon_with_cli
    loop = asyncio.get_running_loop()
    rc, out = await loop.run_in_executor(
        None, lambda: _run_cli("tools.invoke", "mac_screen_size", "--args", "123", sock=sock)
    )
    assert rc != 0
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_cli_tools_invoke_bad_json_args(running_daemon_with_cli):
    _daemon, sock = running_daemon_with_cli
    loop = asyncio.get_running_loop()
    rc, out = await loop.run_in_executor(
        None, lambda: _run_cli("tools.invoke", "mac_screen_size", "--args", "not-json", sock=sock)
    )
    assert rc != 0
    assert out["ok"] is False
    assert "JSON" in out["error"]


@pytest.mark.asyncio
async def test_cli_pair_show_no_daemon():
    """pair show should work even without a daemon (file-only fallback)."""
    loop = asyncio.get_running_loop()
    rc, out = await loop.run_in_executor(
        None,
        lambda: _run_cli(
            "--sock",
            "/tmp/no-such-sock-789.sock",
            "pair",
            "show",
            "--host",
            "test.local",
            "--port",
            "1234",
        ),
    )
    assert rc == 0, out
    assert out["ok"] is True
    assert out["uri"].startswith("jarvis://pair?")
    assert out["host"] == "test.local"
    assert out["port"] == 1234
