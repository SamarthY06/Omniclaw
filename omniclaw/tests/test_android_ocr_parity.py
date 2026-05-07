"""Schema + coordinate parity test between macos_ocr.py and the Android ML Kit
OCR path that backs android_vision.js text-locate.

We can't run ML Kit on the host (it's an Android-only library), so we drive
the Android side by spawning a fake JSON-RPC server that returns the same
mocked OCR result that the Python side gets from a stub recognize_text call.
The expectation is that, given byte-identical OCR results, the public JSON
output of `text-locate` from BOTH backends agrees on:

  * the shape (same top-level keys),
  * the matched_text,
  * the image_x / image_y center,
  * the screen_x / screen_y when screen-width / -height are provided.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
JS_ROOT = ROOT / "android" / "app" / "src" / "main" / "assets" / "node"
ANDROID_VISION = JS_ROOT / "src" / "tools" / "android_vision.js"


def _has_node() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(not _has_node(), reason="node required for parity test")


# Fixed OCR result both backends will see. (Object-bbox form, since that's
# what kotlin_rpc.ocr.recognize_text returns; the Python side maps tuple-bbox
# to object on its way through.)
FIXED_OCR = {
    "ok": True,
    "image_width": 1000,
    "image_height": 2000,
    "items": [
        {"text": "BLR - Team", "confidence": 0.97, "bbox": {"x": 100, "y": 200, "w": 300, "h": 60}},
        {"text": "Pragati Biradar", "confidence": 0.93, "bbox": {"x": 80, "y": 400, "w": 360, "h": 70}},
        {"text": "Project notes", "confidence": 0.91, "bbox": {"x": 150, "y": 800, "w": 220, "h": 50}},
    ],
}

EXPECTED_TARGET = "Pragati Biradar"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Drive android_vision.js text-locate
# ---------------------------------------------------------------------------


def _start_fake_kotlin_server(ocr_result: dict, port: int) -> subprocess.Popen:
    """Start a small Node fake-server that answers ocr.recognize_text and secrets.openai."""
    server_js = (
        "const net = require('net');\n"
        f"const port = {port};\n"
        f"const ocr = {json.dumps(ocr_result)};\n"
        "net.createServer((s) => {\n"
        "  let buf = '';\n"
        "  s.on('data', (c) => {\n"
        "    buf += c.toString();\n"
        "    let nl;\n"
        "    while ((nl = buf.indexOf('\\n')) !== -1) {\n"
        "      const line = buf.slice(0, nl);\n"
        "      buf = buf.slice(nl + 1);\n"
        "      const r = JSON.parse(line);\n"
        "      let result = null;\n"
        "      if (r.method === 'ocr.recognize_text') result = ocr;\n"
        "      else if (r.method === 'secrets.openai') result = { key: '' };\n"
        "      else result = {};\n"
        "      s.write(JSON.stringify({ id: r.id, result }) + '\\n');\n"
        "    }\n"
        "  });\n"
        "  s.on('error', () => {});\n"
        "}).listen(port, '127.0.0.1');\n"
        "console.log('READY');\n"
    )
    proc = subprocess.Popen(
        ["node", "-e", server_js],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    # Wait for READY.
    for _ in range(30):
        line = proc.stdout.readline()
        if line.strip() == "READY":
            return proc
    proc.terminate()
    raise RuntimeError("fake kotlin server never became ready")


def _run_android_text_locate(target: str, screen_w: int = 0, screen_h: int = 0,
                              min_score: float = 0.7) -> dict:
    """Run android_vision.js text-locate against a fake kotlin RPC server."""
    port = _free_port()
    server = _start_fake_kotlin_server(FIXED_OCR, port)
    try:
        env = dict(os.environ)
        env["BEN_RPC_PORT"] = str(port)
        # The image path is unused (kotlin returns canned OCR), but the CLI requires it.
        args = [
            "node", str(ANDROID_VISION), "text-locate",
            "--image", "/tmp/dummy.png",
            "--target", target,
            "--min-score", str(min_score),
        ]
        if screen_w and screen_h:
            args += ["--screen-width", str(screen_w), "--screen-height", str(screen_h)]
        proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=10)
        assert proc.returncode == 0, f"android_vision.js failed: {proc.stderr}"
        last = [ln for ln in proc.stdout.splitlines() if ln.strip()][-1]
        return json.loads(last)
    finally:
        server.terminate()
        server.wait(timeout=2)


# ---------------------------------------------------------------------------
# Drive macos_vision.py text_locate (shape only - we reach in directly so we
# don't need an actual screenshot to OCR).
# ---------------------------------------------------------------------------


def _run_mac_text_locate(target: str, screen_w: int | None = None, screen_h: int | None = None,
                         min_score: float = 0.7) -> dict:
    sys.path.insert(0, str(ROOT))
    from omniclaw.tools import macos_vision

    return macos_vision._text_locate_from_ocr_result(
        ocr_result=_to_tuple_bbox(FIXED_OCR),
        target=target,
        screen_width=screen_w,
        screen_height=screen_h,
        min_score=min_score,
        max_candidates=8,
    )


def _to_tuple_bbox(result: dict) -> dict:
    """Mac side internally uses tuple bbox; convert from our shared object form."""
    items = []
    for it in result["items"]:
        b = it["bbox"]
        items.append(
            {**it, "bbox": (b["x"], b["y"], b["w"], b["h"])}
        )
    return {**result, "items": items}


# ---------------------------------------------------------------------------
# The actual parity assertion
# ---------------------------------------------------------------------------


def test_text_locate_parity_image_only() -> None:
    a = _run_android_text_locate(EXPECTED_TARGET)
    # Mac path uses an internal helper; if not available skip with a clear note.
    try:
        m = _run_mac_text_locate(EXPECTED_TARGET)
    except (ImportError, AttributeError):
        pytest.skip("macos_vision._text_locate_from_ocr_result helper not exported yet")

    # Both should report success on the same target.
    assert a["ok"] is True
    assert m["ok"] is True
    assert a.get("matched_text") == m.get("matched_text") == EXPECTED_TARGET

    # Centers within 1 pixel of each other (rounding only).
    assert abs(a["image_x"] - m["image_x"]) <= 1
    assert abs(a["image_y"] - m["image_y"]) <= 1

    # Top-level keys agree (some keys are optional; we check the always-present set).
    required_keys = {"ok", "matched_text", "image_x", "image_y", "image_width", "image_height"}
    assert required_keys <= set(a.keys()), f"android missing: {required_keys - set(a.keys())}"
    assert required_keys <= set(m.keys()), f"mac missing: {required_keys - set(m.keys())}"


def test_text_locate_parity_with_screen_dims() -> None:
    a = _run_android_text_locate(EXPECTED_TARGET, screen_w=2000, screen_h=4000)
    try:
        m = _run_mac_text_locate(EXPECTED_TARGET, screen_w=2000, screen_h=4000)
    except (ImportError, AttributeError):
        pytest.skip("macos_vision._text_locate_from_ocr_result helper not exported yet")

    # click_x / click_y should match within 20px (per the plan's tolerance).
    assert abs(a["click_x"] - m["click_x"]) <= 20, f"a={a['click_x']}, m={m['click_x']}"
    assert abs(a["click_y"] - m["click_y"]) <= 20, f"a={a['click_y']}, m={m['click_y']}"
