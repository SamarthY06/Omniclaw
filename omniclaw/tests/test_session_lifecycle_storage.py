"""Tests for omniclaw/voice/session.py.

Two layers:

1. Pure-Python: SessionTimer + SessionStore behaviour with a fast cutoff.
2. Cross-impl parity: assert that the JSONL schema written here matches the
   one that android/.../node/src/session/store.js writes (we run the Node
   side in a child process to produce a session, then compare key sets).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from omniclaw.voice.session import SessionStore, SessionTimer


REPO_ROOT = Path(__file__).resolve().parents[2]
JS_STORE = REPO_ROOT / "android" / "app" / "src" / "main" / "assets" / "node" / "src" / "session" / "store.js"


# ---------------------------------------------------------------------------
# SessionTimer
# ---------------------------------------------------------------------------

def test_session_timer_fires_after_cutoff() -> None:
    fired: list[dict] = []
    t = SessionTimer(cutoff_ms=120, on_timeout=lambda ev: fired.append(ev))
    t.start()
    time.sleep(0.25)
    assert len(fired) == 1
    assert fired[0]["reason"] == "silence_cutoff"


def test_session_timer_reset_extends_window() -> None:
    fired: list[dict] = []
    t = SessionTimer(cutoff_ms=120, on_timeout=lambda ev: fired.append(ev))
    t.start()
    # Reset twice; should not have fired yet at the original cutoff.
    time.sleep(0.08)
    t.mark_activity("vad")
    time.sleep(0.08)
    t.mark_activity("audio_delta")
    # Now wait full cutoff again.
    time.sleep(0.25)
    assert len(fired) == 1


def test_session_timer_stop_prevents_fire() -> None:
    fired: list[dict] = []
    t = SessionTimer(cutoff_ms=80, on_timeout=lambda ev: fired.append(ev))
    t.start()
    t.stop()
    time.sleep(0.20)
    assert len(fired) == 0


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------

def test_session_store_lifecycle(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    sid = "abc123"
    store.start(sid, device="phone")
    store.append_user_text(sid, "Hey Ben, ping the Mac")
    store.append_assistant_text(sid, "Sure, pinging now.")
    store.append_tool_call(sid, "peer_cli", "verify", {"ts_ms": 0})
    store.append_tool_result(sid, "peer_cli", True, "rtt_ms=14")
    store.end(sid, reason="user_ended")

    # Find the per-session JSONL file.
    sessions_dir = tmp_path / "sessions"
    session_files = list(sessions_dir.rglob("sess_abc123.jsonl"))
    assert len(session_files) == 1
    events = [json.loads(line) for line in session_files[0].read_text().splitlines()]
    types = [e["type"] for e in events]
    assert types == [
        "session.started", "user.text", "assistant.text",
        "tool.call", "tool.result", "session.ended",
    ]
    assert events[0]["device"] == "phone"
    assert events[0]["wake_word"] == "Ben"
    assert events[0]["session_id"] == sid

    # Index file.
    index = (sessions_dir / "index.jsonl").read_text().splitlines()
    assert len(index) == 1
    summary = json.loads(index[0])
    assert summary["id"] == sid
    assert summary["device"] == "phone"
    assert summary["first_user_line"] == "Hey Ben, ping the Mac"
    assert "peer_cli" in summary["tools_used"]
    assert summary["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Schema parity with JS store
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("node"), reason="node required")
def test_jsonl_schema_matches_js_store(tmp_path: Path) -> None:
    """Run the JS SessionStore.start/.../.end on a fresh workspace and assert
    its output JSONL has the same top-level keys as the Python store.
    """
    js_workspace = tmp_path / "js"
    js_workspace.mkdir()
    driver = (
        "const { sessionStore } = require(" + json.dumps(str(JS_STORE)) + ");\n"
        "const ws = " + json.dumps(str(js_workspace)) + ";\n"
        "const s = sessionStore(ws);\n"
        "s.start('abc', 'phone');\n"
        "s.appendUserText('abc', 'Hello');\n"
        "s.appendAssistantText('abc', 'Hi');\n"
        "s.appendToolCall('abc', 'peer_cli', 'verify', { ts_ms: 0 });\n"
        "s.appendToolResult('abc', 'peer_cli', true, 'rtt=14');\n"
        "s.end('abc', 'user_ended');\n"
        "console.log('done');\n"
    )
    proc = subprocess.run(["node", "-e", driver], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr

    # And run the Python store on a parallel workspace.
    py_workspace = tmp_path / "py"
    store = SessionStore(py_workspace)
    store.start("abc", "phone")
    store.append_user_text("abc", "Hello")
    store.append_assistant_text("abc", "Hi")
    store.append_tool_call("abc", "peer_cli", "verify", {"ts_ms": 0})
    store.append_tool_result("abc", "peer_cli", True, "rtt=14")
    store.end("abc", reason="user_ended")

    # Compare top-level event keys (order is fixed: session.started ... session.ended).
    js_events = _load_session(js_workspace, "abc")
    py_events = _load_session(py_workspace, "abc")
    assert [e["type"] for e in js_events] == [e["type"] for e in py_events]
    for je, pe in zip(js_events, py_events):
        # Same set of top-level keys, regardless of values (timestamps differ).
        assert set(je.keys()) == set(pe.keys()), \
            f"key mismatch on {je['type']}: js={set(je.keys())}, py={set(pe.keys())}"

    # Index file: same keys.
    js_idx = json.loads((js_workspace / "sessions" / "index.jsonl").read_text().splitlines()[0])
    py_idx = json.loads((py_workspace / "sessions" / "index.jsonl").read_text().splitlines()[0])
    assert set(js_idx.keys()) == set(py_idx.keys())


def _load_session(workspace: Path, sid: str) -> list[dict]:
    files = list((workspace / "sessions").rglob(f"sess_{sid}.jsonl"))
    assert len(files) == 1, f"expected one session file, got {files}"
    return [json.loads(line) for line in files[0].read_text().splitlines()]
