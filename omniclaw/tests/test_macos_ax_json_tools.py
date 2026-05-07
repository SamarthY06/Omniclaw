"""macos_ax.py --json-tools shape: must yield OpenAI function-tool schemas."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MACOS_AX = REPO_ROOT / "omniclaw" / "tools" / "macos_ax.py"


def test_macos_ax_json_tools_shape():
    # Generous timeout: pyobjc imports can take a couple of seconds, and
    # several subprocess.run() tests run back-to-back in CI.
    proc = subprocess.run(
        [sys.executable, str(MACOS_AX), "json-tools"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    tools = data["tools"]
    names = {t["name"] for t in tools}
    # spot-check a handful of well-known tools
    for required in {
        "mac_launch",
        "mac_screen_size",
        "mac_focused_app",
        "mac_screenshot",
        "mac_click",
        "mac_type",
        "mac_shortcut",
    }:
        assert required in names, f"missing tool: {required}"
    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool
        assert tool["parameters"]["type"] == "object"
        assert "sensitivity" in tool
        assert tool["sensitivity"] in {"S0", "S1", "S2", "S3"}
