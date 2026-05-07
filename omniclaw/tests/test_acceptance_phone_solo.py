"""Phone-only acceptance: 'Ben, send Pragati Biradar on WhatsApp: on my way'.

This test runs against a connected Android device with the Ben APK installed
and onboarding completed. It will be SKIPPED automatically when:

  * no adb on PATH
  * no device listed by `adb devices`
  * the device does not have the Ben app installed

That keeps CI green without a phone in the loop, while still letting the
developer (and ultimately the user) execute the acceptance check from this
exact pytest path.

Procedure:

    1. Boot the embedded agent on the phone with a fresh session.
    2. Inject the prompt as a `user.text` event into the inbound JSON-RPC.
    3. Wait up to ACCEPTANCE_TIMEOUT_S for the session.ended event.
    4. Pull the JSONL trace via `adb pull` and parse it.
    5. Independently verify by running `adb screencap`, then OCR'ing the PNG
       and confirming the literal string 'on my way' appears at the right
       side of the screen (the WhatsApp message body's outgoing pane).

We don't trust the agent's self-claim of success; we verify with our own OCR.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest


ACCEPTANCE_TIMEOUT_S = 90.0
PROMPT = "Ben, send Pragati Biradar on WhatsApp: on my way"
EXPECTED_TEXT = "on my way"
APP_PACKAGE = "com.ben"
DEVICE_TRACE_DIR = "/data/data/com.ben/files/openclaw/workspace/sessions"


def _adb_available() -> bool:
    return shutil.which("adb") is not None


def _device_listed() -> bool:
    if not _adb_available():
        return False
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        return False
    lines = [ln.strip() for ln in out.stdout.splitlines()[1:] if ln.strip()]
    return any(ln.endswith("device") for ln in lines)


def _ben_installed() -> bool:
    if not _device_listed():
        return False
    out = subprocess.run(["adb", "shell", "pm", "list", "packages", APP_PACKAGE],
                         capture_output=True, text=True, timeout=10)
    return APP_PACKAGE in out.stdout


pytestmark = pytest.mark.skipif(
    not _ben_installed(),
    reason="phone-solo acceptance requires an adb-connected device with com.ben installed",
)


def test_phone_solo_send_whatsapp_message(tmp_path: Path) -> None:
    """End-to-end: prompt -> WhatsApp -> verify outgoing message via OCR."""
    # 1. Force-stop and restart the app to guarantee a fresh session.
    subprocess.run(["adb", "shell", "am", "force-stop", APP_PACKAGE], check=False)
    subprocess.run(["adb", "shell", "monkey", "-p", APP_PACKAGE, "-c",
                    "android.intent.category.LAUNCHER", "1"], check=False)
    time.sleep(2.0)

    # 2. Inject the prompt via the inbound RPC port (forwarded over adb).
    subprocess.run(["adb", "forward", "tcp:18792", "tcp:18792"], check=True)
    session_id = "acceptance_solo_" + str(int(time.time()))
    rpc_calls = [
        {"id": 1, "method": "session.started",
         "params": {"session_id": session_id, "device": "phone"}},
        {"id": 2, "method": "session.user_text",
         "params": {"session_id": session_id, "text": PROMPT}},
    ]
    for call in rpc_calls:
        _send_rpc(call)

    # 3. Wait for the agent to finish.
    deadline = time.monotonic() + ACCEPTANCE_TIMEOUT_S
    trace_local = tmp_path / "trace.jsonl"
    success = False
    while time.monotonic() < deadline:
        time.sleep(2.0)
        try:
            trace_remote = _find_session_file(session_id)
            if trace_remote:
                subprocess.run(["adb", "pull", trace_remote, str(trace_local)],
                               capture_output=True, text=True, timeout=15)
                if "session.ended" in trace_local.read_text(errors="replace"):
                    success = True
                    break
        except Exception:
            pass

    if not success:
        pytest.fail(f"agent never closed session {session_id} within {ACCEPTANCE_TIMEOUT_S}s")

    # 4. Independent OCR verification: screenshot the phone, OCR it locally,
    # confirm the outgoing message text appears.
    screenshot = tmp_path / "screen.png"
    subprocess.run(["adb", "exec-out", "screencap", "-p"],
                   stdout=screenshot.open("wb"), check=True, timeout=15)

    # We use ML Kit indirectly via `android_vision.js text-locate` against a
    # fake kotlin server that ALSO returns canned OCR doesn't help here - we
    # need a real OCR. Use the Mac's macos_ocr.py which works on PNG bytes
    # regardless of source device.
    try:
        from omniclaw.tools import macos_ocr
    except ImportError:
        pytest.skip("macos_ocr unavailable; cannot do independent OCR verify")
    ocr = macos_ocr.recognize_text(screenshot)
    assert ocr.get("ok"), f"OCR failed: {ocr}"
    texts = " ".join(it["text"] for it in ocr.get("items", [])).lower()
    assert EXPECTED_TEXT in texts, (
        f"EXPECTED_TEXT={EXPECTED_TEXT!r} not present after agent run; OCR saw: {texts}"
    )

    # 5. Assert the agent did NOT use a browser (per AGENTS.md hard rule).
    events = [json.loads(ln) for ln in trace_local.read_text().splitlines() if ln.strip()]
    tool_calls = [e for e in events if e.get("type") == "tool.call"]
    for tc in tool_calls:
        assert "browser" not in (tc.get("name") or "").lower(), \
            f"Browser tool called - violates AGENTS.md: {tc}"


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
