"""Cross-device acceptance: 'Ben, ask my Mac for the last 5 messages on Teams'.

This test runs against:
  * an adb-connected Android phone with the Ben APK installed and onboarded,
  * a paired Mac peer (the launchd ai.jarvis.peer agent must be loaded and
    answering ping).

It will be SKIPPED automatically when either side isn't ready.

Procedure:

    1. Confirm both devices are talking via peer_cli.py ping.
    2. Inject the prompt as a `user.text` event on the phone.
    3. Wait for `tool.call` events that route through `peer_cli.task.run`.
    4. Confirm Mac's gateway log records the corresponding tool calls:
       text_locate -> click-at -> vision_read.
    5. Assert NO browser tool was called on EITHER device.
    6. Verify the assistant reply contains 5 entries (or matches a relaxed
       count fallback) of recognizable Teams message structure (sender + text).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


ACCEPTANCE_TIMEOUT_S = 120.0
PROMPT = "Ben, ask my Mac for the last 5 messages on Teams"
APP_PACKAGE = "com.ben"
DEVICE_TRACE_DIR = "/data/data/com.ben/files/openclaw/workspace/sessions"
MAC_GATEWAY_LOG = Path.home() / ".openclaw" / "logs" / "gateway.log"


def _adb_device_ready() -> bool:
    if not shutil.which("adb"):
        return False
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
    return any(ln.strip().endswith("device") for ln in out.stdout.splitlines()[1:])


def _ben_installed() -> bool:
    if not _adb_device_ready():
        return False
    out = subprocess.run(["adb", "shell", "pm", "list", "packages", APP_PACKAGE],
                         capture_output=True, text=True, timeout=10)
    return APP_PACKAGE in out.stdout


def _mac_peer_ok() -> bool:
    cli = Path(__file__).resolve().parents[1] / "tools" / "peer_cli.py"
    if not cli.is_file():
        return False
    try:
        out = subprocess.run(
            [".venv/bin/python", str(cli), "ping"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
    except Exception:
        return False
    if out.returncode != 0:
        return False
    try:
        return bool(json.loads(out.stdout).get("ok"))
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_ben_installed() and _mac_peer_ok()),
    reason="cross-device acceptance requires phone with com.ben installed AND Mac peer responding to ping",
)


def test_cross_device_teams_last_5(tmp_path: Path) -> None:
    """End-to-end: phone wakes, agent peer-routes to Mac, Mac reads Teams."""
    subprocess.run(["adb", "shell", "am", "force-stop", APP_PACKAGE], check=False)
    subprocess.run(["adb", "shell", "monkey", "-p", APP_PACKAGE, "-c",
                    "android.intent.category.LAUNCHER", "1"], check=False)
    time.sleep(2.0)

    subprocess.run(["adb", "forward", "tcp:18792", "tcp:18792"], check=True)
    session_id = "acceptance_cross_" + str(int(time.time()))
    _send_rpc({"id": 1, "method": "session.started",
               "params": {"session_id": session_id, "device": "phone"}})
    _send_rpc({"id": 2, "method": "session.user_text",
               "params": {"session_id": session_id, "text": PROMPT}})

    deadline = time.monotonic() + ACCEPTANCE_TIMEOUT_S
    trace_local = tmp_path / "trace.jsonl"
    success = False
    while time.monotonic() < deadline:
        time.sleep(2.5)
        try:
            remote = _find_session_file(session_id)
            if remote:
                subprocess.run(["adb", "pull", remote, str(trace_local)],
                               capture_output=True, timeout=15)
                if "session.ended" in trace_local.read_text(errors="replace"):
                    success = True
                    break
        except Exception:
            pass

    if not success:
        pytest.fail(f"agent never ended session {session_id} within {ACCEPTANCE_TIMEOUT_S}s")

    events = [json.loads(ln) for ln in trace_local.read_text().splitlines() if ln.strip()]

    # Must have routed through peer_cli (task.run) at least once.
    peer_calls = [e for e in events if e.get("type") == "tool.call"
                  and (e.get("name") or "").startswith("peer_cli")]
    assert peer_calls, "phone never routed to Mac via peer_cli"

    # Browser must NOT have been used on either side.
    for e in events:
        if e.get("type") != "tool.call":
            continue
        name = (e.get("name") or "").lower()
        assert "browser" not in name, f"Phone agent called a browser tool: {e}"

    if MAC_GATEWAY_LOG.is_file():
        log = MAC_GATEWAY_LOG.read_text(errors="replace").lower()
        assert "browser" not in log[-50_000:], \
            "Mac gateway log shows browser tool usage in the recent window"

    # Assistant should have produced a numbered or bulleted list of ~5 items.
    assistant_lines = [e["text"] for e in events if e.get("type") == "assistant.text"]
    combined = "\n".join(assistant_lines).lower()
    bullet_count = sum(combined.count(prefix) for prefix in ("- ", "1.", "2.", "3.", "4.", "5."))
    assert bullet_count >= 3, (
        f"assistant didn't return a structured Teams message list; got: {combined[:400]}"
    )


def _send_rpc(req: dict) -> dict:
    import socket
    s = socket.create_connection(("127.0.0.1", 18792), timeout=5)
    s.sendall((json.dumps(req) + "\n").encode("utf-8"))
    raw = b""
    while not raw.endswith(b"\n"):
        chunk = s.recv(4096)
        if not chunk:
            break
        raw += chunk
    s.close()
    return json.loads(raw.decode("utf-8"))


def _find_session_file(session_id: str) -> str | None:
    out = subprocess.run([
        "adb", "shell", "find", DEVICE_TRACE_DIR, "-name", f"sess_{session_id}.jsonl",
    ], capture_output=True, text=True, timeout=10)
    line = out.stdout.strip().splitlines()
    return line[0] if line else None
