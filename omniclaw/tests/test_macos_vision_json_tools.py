"""macos_vision.py --json-tools shape: must yield OpenAI function-tool schemas."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MACOS_VISION = REPO_ROOT / "omniclaw" / "tools" / "macos_vision.py"


def test_macos_vision_json_tools_shape():
    proc = subprocess.run(
        [sys.executable, str(MACOS_VISION), "--json-tools"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    tools = data["tools"]
    names = {t["name"] for t in tools}
    assert "vision_read" in names

    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool
        assert tool["parameters"]["type"] == "object"
        assert "sensitivity" in tool
        assert tool["sensitivity"] in {"S0", "S1", "S2", "S3"}

    vr = next(t for t in tools if t["name"] == "vision_read")
    props = vr["parameters"]["properties"]
    assert "image" in props
    assert "question" in props
    assert "max_tokens" in props
    assert "detail" in props
    assert "model" in props
    required = vr["parameters"]["required"]
    assert "image" in required
    assert "question" in required
    assert vr["sensitivity"] == "S2"


def test_macos_vision_no_command_fails_cleanly():
    proc = subprocess.run(
        [sys.executable, str(MACOS_VISION)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode != 0
    data = json.loads(proc.stdout)
    assert data["ok"] is False
    assert "no command" in data["error"]


def test_macos_vision_read_missing_image_fails(tmp_path, monkeypatch):
    proc = subprocess.run(
        [
            sys.executable,
            str(MACOS_VISION),
            "read",
            "--image",
            str(tmp_path / "does-not-exist.png"),
            "--question",
            "describe",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={**__import__("os").environ, "OPENAI_API_KEY": "sk-test-fake"},
    )
    assert proc.returncode != 0
    data = json.loads(proc.stdout)
    assert data["ok"] is False
    assert "image not found" in data["error"]
