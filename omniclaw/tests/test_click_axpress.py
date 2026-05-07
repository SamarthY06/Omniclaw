"""Tests for the AXPress-first click path in macos_accessibility.click_by_index.

Background: synthetic CGEvent mouse clicks are routed by macOS to whichever
window is topmost at the target screen position. When the target app is
hidden behind another window (very common when the agent is launched from a
shell that lives inside another app), the click silently lands on the wrong
app -- and the tool reports success because the CGEvent posts cleanly.

The fix is to try AXPress (an Accessibility action targeting the AX element
directly) before falling back to coordinate clicks. AXPress is
focus-independent and always reaches the target element when the role+title
are stable in the registry.

These tests run only on macOS with pyobjc; the AX layer doesn't exist anywhere
else.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from omniclaw.tools import macos_accessibility as ax  # noqa: E402

if not ax.PYOBJC_AVAILABLE:
    pytest.skip("pyobjc not available; click tests require macOS", allow_module_level=True)


@pytest.fixture
def fake_registry(monkeypatch):
    registry = {
        "app": "Microsoft Teams",
        "pid": 12345,
        "elements": {
            "56": {
                "role": "AXRow",
                "title": "Group chat BLR - Team Last message Raghav: ...",
                "position": {"x": 84, "y": 606, "width": 276, "height": 44},
            },
            "999": {
                "role": "AXSlider",
                "title": "Volume",
                "position": {"x": 100, "y": 200, "width": 200, "height": 20},
            },
        },
    }
    monkeypatch.setattr(ax, "_load_element_registry", lambda: registry)
    return registry


def test_click_by_index_uses_axpress_first(monkeypatch, fake_registry):
    """When role+title are present and click is left-single, AXPress is used."""
    calls = []

    def _fake_create_app(pid):
        calls.append(("create_app", pid))
        return f"app:{pid}"

    def _fake_find(element, role, title, depth=0, max_depth=30):
        calls.append(("find", role, title))
        return f"el:{role}:{title[:20]}"

    def _fake_press(element, action):
        calls.append(("press", element, action))
        return ax.kAXErrorSuccess

    monkeypatch.setattr(ax, "AXUIElementCreateApplication", _fake_create_app)
    monkeypatch.setattr(ax, "_find_actionable_by_role_and_title", _fake_find)
    monkeypatch.setattr(ax, "AXUIElementPerformAction", _fake_press)

    def _refuse_mouse(*a, **kw):
        raise AssertionError("mouse click should NOT be invoked when AXPress succeeds")

    monkeypatch.setattr(ax, "_mouse_click", _refuse_mouse)
    monkeypatch.setattr(ax.time, "sleep", lambda *_: None)

    result = ax.click_by_index(56, click_type="left", click_count=1)
    assert result["success"] is True
    assert result["method"] == "AXPress"
    assert result["element"]["role"] == "AXRow"
    assert "BLR - Team" in result["element"]["title"]
    assert any(c[0] == "press" for c in calls)


def test_click_by_index_falls_back_to_coordinate_when_axpress_fails(monkeypatch, fake_registry):
    """If AXPress returns a non-success error, fall back to a CGEvent mouse click."""
    monkeypatch.setattr(ax, "AXUIElementCreateApplication", lambda pid: "app")
    monkeypatch.setattr(ax, "_find_actionable_by_role_and_title", lambda *a, **kw: "el")
    monkeypatch.setattr(ax, "AXUIElementPerformAction", lambda el, act: -25212)
    monkeypatch.setattr(ax.time, "sleep", lambda *_: None)

    mouse_calls = []

    def _fake_mouse(cx, cy, click_type, click_count):
        mouse_calls.append((cx, cy, click_type, click_count))
        return {"success": True, "clicked_at": [cx, cy]}

    monkeypatch.setattr(ax, "_mouse_click", _fake_mouse)

    result = ax.click_by_index(56, click_type="left", click_count=1)
    assert result["success"] is True
    assert result["method"] == "coordinate"
    assert mouse_calls == [(84 + 138.0, 606 + 22.0, "left", 1)]


def test_click_by_index_falls_back_when_no_matching_axpress_element(monkeypatch, fake_registry):
    """If the live AX walk can't find the title+role, fall back to coordinate."""
    monkeypatch.setattr(ax, "AXUIElementCreateApplication", lambda pid: "app")
    monkeypatch.setattr(ax, "_find_actionable_by_role_and_title", lambda *a, **kw: None)
    monkeypatch.setattr(ax.time, "sleep", lambda *_: None)

    mouse_calls = []

    def _fake_mouse(cx, cy, click_type, click_count):
        mouse_calls.append((cx, cy, click_type, click_count))
        return {"success": True, "clicked_at": [cx, cy]}

    monkeypatch.setattr(ax, "_mouse_click", _fake_mouse)

    result = ax.click_by_index(56, click_type="left", click_count=1)
    assert result["success"] is True
    assert result["method"] == "coordinate"
    assert len(mouse_calls) == 1


def test_click_by_index_skips_axpress_for_double_click(monkeypatch, fake_registry):
    """Double-click can't be done via AXPress; must use coords."""
    def _fail_press(*a, **kw):
        raise AssertionError("AXPress should not be tried for double-click")

    monkeypatch.setattr(ax, "_find_actionable_by_role_and_title", _fail_press)

    mouse_calls = []
    monkeypatch.setattr(
        ax,
        "_mouse_click",
        lambda cx, cy, t, n: mouse_calls.append((cx, cy, t, n)) or {"success": True, "clicked_at": [cx, cy]},
    )

    result = ax.click_by_index(56, click_type="left", click_count=2)
    assert result["success"] is True
    assert result["method"] == "coordinate"
    assert mouse_calls[0][3] == 2


def test_click_by_index_skips_axpress_for_right_click(monkeypatch, fake_registry):
    """Right-click semantics aren't AXPress."""
    def _fail_press(*a, **kw):
        raise AssertionError("AXPress should not be tried for right-click")

    monkeypatch.setattr(ax, "_find_actionable_by_role_and_title", _fail_press)
    mouse_calls = []
    monkeypatch.setattr(
        ax,
        "_mouse_click",
        lambda cx, cy, t, n: mouse_calls.append((cx, cy, t, n)) or {"success": True, "clicked_at": [cx, cy]},
    )

    result = ax.click_by_index(56, click_type="right", click_count=1)
    assert result["success"] is True
    assert result["method"] == "coordinate"
    assert mouse_calls[0][2] == "right"


def test_click_by_index_skips_axpress_when_prefer_press_false(monkeypatch, fake_registry):
    """Caller can opt out of AXPress via prefer_press=False (the --no-press flag)."""
    def _fail_press(*a, **kw):
        raise AssertionError("AXPress must not be invoked when prefer_press=False")

    monkeypatch.setattr(ax, "_find_actionable_by_role_and_title", _fail_press)

    mouse_calls = []
    monkeypatch.setattr(
        ax,
        "_mouse_click",
        lambda cx, cy, t, n: mouse_calls.append((cx, cy, t, n)) or {"success": True, "clicked_at": [cx, cy]},
    )

    result = ax.click_by_index(56, click_type="left", click_count=1, prefer_press=False)
    assert result["success"] is True
    assert result["method"] == "coordinate"
    assert len(mouse_calls) == 1


def test_click_by_index_unknown_index_returns_error(fake_registry):
    result = ax.click_by_index(9999)
    assert result["success"] is False
    assert "not found" in result["error"]


def test_click_by_index_no_registry_returns_error(monkeypatch):
    monkeypatch.setattr(ax, "_load_element_registry", lambda: {})
    result = ax.click_by_index(1)
    assert result["success"] is False
    assert "registry" in result["error"]
