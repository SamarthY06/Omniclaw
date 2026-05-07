"""
macOS Accessibility Layer — wraps AXUIElement via pyobjc.

Bulletproof version: indexed elements, coordinate-based clicking,
deep tree reading with smart filtering, screenshot capture,
wait-for-ready logic, and proper scroll/mouse interactions.
"""

import subprocess
import time
import json
import logging
import tempfile
import os
from typing import Optional

logger = logging.getLogger(__name__)

ELEMENT_REGISTRY_PATH = "/tmp/omniclaw_element_registry.json"

# ─── pyobjc imports ─────────────────────────────────────────────────────────
try:
    from AppKit import NSWorkspace, NSRunningApplication
    from ApplicationServices import (
        AXUIElementCreateApplication,
        AXUIElementCreateSystemWide,
        AXUIElementCopyAttributeValue,
        AXUIElementCopyAttributeNames,
        AXUIElementPerformAction,
        AXUIElementSetAttributeValue,
        AXValueCreate,
        kAXErrorSuccess,
        kAXFocusedApplicationAttribute,
        kAXWindowsAttribute,
        kAXTitleAttribute,
        kAXRoleAttribute,
        kAXChildrenAttribute,
        kAXValueAttribute,
        kAXDescriptionAttribute,
        kAXEnabledAttribute,
        kAXFocusedAttribute,
        kAXSelectedTextAttribute,
        kAXPressAction,
        kAXFocusedUIElementAttribute,
        kAXPositionAttribute,
        kAXSizeAttribute,
    )
    PYOBJC_AVAILABLE = True
except ImportError:
    PYOBJC_AVAILABLE = False
    logger.warning("pyobjc not available — accessibility layer disabled")


# ─── App Name Resolution ─────────────────────────────────────────────────────

_APP_ALIASES = {
    "chrome": "Google Chrome",
    "firefox": "Firefox",
    "safari": "Safari",
    "notes": "Notes",
    "settings": "System Settings",
    "system preferences": "System Settings",
    "system settings": "System Settings",
    "whatsapp": "WhatsApp",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "terminal": "Terminal",
    "iterm": "iTerm",
    "finder": "Finder",
    "messages": "Messages",
    "mail": "Mail",
    "calendar": "Calendar",
    "music": "Music",
    "photos": "Photos",
    "preview": "Preview",
    "textedit": "TextEdit",
    "teams": "Microsoft Teams",
    "slack": "Slack",
    "discord": "Discord",
    "netflix": "Netflix",
    "spotify": "Spotify",
}


def _resolve_app_name(name: str) -> str:
    """Map common short names / aliases to the real macOS app name."""
    return _APP_ALIASES.get(name.lower().strip(), name)


# ─── AX Helpers ──────────────────────────────────────────────────────────────

def _get_ax_attr(element, attr: str):
    """Safely read a single AX attribute value."""
    try:
        err, value = AXUIElementCopyAttributeValue(element, attr, None)
        if err == kAXErrorSuccess:
            return value
    except Exception:
        pass
    return None


def _get_ax_actions(element) -> list:
    """Get the list of performable actions on an element."""
    try:
        from ApplicationServices import AXUIElementCopyActionNames
        err, actions = AXUIElementCopyActionNames(element, None)
        if err == kAXErrorSuccess and actions:
            return list(actions)
    except Exception:
        pass
    return []


def _get_element_position_size(element):
    """Extract (x, y, width, height) from an AX element, or None."""
    pos_val = _get_ax_attr(element, kAXPositionAttribute)
    size_val = _get_ax_attr(element, kAXSizeAttribute)
    if pos_val is None or size_val is None:
        return None
    try:
        from ApplicationServices import AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType
        ok1, point = AXValueGetValue(pos_val, kAXValueCGPointType, None)
        ok2, sz = AXValueGetValue(size_val, kAXValueCGSizeType, None)
        if ok1 and ok2:
            return (float(point.x), float(point.y),
                    float(sz.width), float(sz.height))
    except Exception:
        pass
    # Fallback: parse string representation
    import re
    try:
        pos_str = str(pos_val)
        size_str = str(size_val)
        pm = re.search(r'x:([\d.-]+)\s+y:([\d.-]+)', pos_str)
        sm = re.search(r'w:([\d.-]+)\s+h:([\d.-]+)', size_str)
        if pm and sm:
            return (float(pm.group(1)), float(pm.group(2)),
                    float(sm.group(1)), float(sm.group(2)))
    except Exception:
        pass
    return None


# ─── Enhanced Accessibility ──────────────────────────────────────────────────

def _enable_enhanced_accessibility(pid: int) -> None:
    """Tell an app we are assistive technology so it exposes its full AX tree.

    Sets AXEnhancedUserInterface on the app element — the same flag VoiceOver
    sets. Critical for Chromium/Electron apps that gate their web-content AX
    tree behind this flag.
    """
    if not PYOBJC_AVAILABLE or pid is None:
        return
    try:
        app_el = AXUIElementCreateApplication(pid)
        AXUIElementSetAttributeValue(app_el, "AXEnhancedUserInterface", True)
    except Exception:
        pass


# ─── App Management ──────────────────────────────────────────────────────────

def _get_app_pid(app_name: str) -> Optional[int]:
    """Find the PID of a running app by name."""
    if not PYOBJC_AVAILABLE:
        return None
    resolved = _resolve_app_name(app_name)
    workspace = NSWorkspace.sharedWorkspace()
    for app in workspace.runningApplications():
        name = app.localizedName()
        if name and resolved.lower() == name.lower():
            return app.processIdentifier()
    # Fallback: substring match
    for app in workspace.runningApplications():
        name = app.localizedName()
        if name and resolved.lower() in name.lower():
            return app.processIdentifier()
    return None


def launch_app(app_name: str, wait: bool = True) -> dict:
    """Launch an app by name and optionally wait until its UI tree is ready."""
    resolved = _resolve_app_name(app_name)
    try:
        result = subprocess.run(
            ["open", "-a", resolved],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            err_msg = result.stderr.strip()
            if "Unable to find" in err_msg:
                err_msg = f"Unable to find application named '{resolved}'"
            return {"success": False, "pid": None, "error": err_msg}

        if not wait:
            time.sleep(1.5)
            pid = _get_app_pid(resolved)
            _enable_enhanced_accessibility(pid)
            return {"success": True, "pid": pid, "error": None}

        pid = _wait_for_app_ready(resolved, timeout=6.0)
        if pid is None:
            return {"success": True, "pid": None,
                    "error": "App launched but window not detected in time"}
        return {"success": True, "pid": pid, "error": None}

    except Exception as e:
        return {"success": False, "pid": None, "error": str(e)}


def _wait_for_app_ready(app_name: str, timeout: float = 6.0) -> Optional[int]:
    """Poll until the app has a window with a non-empty AX tree."""
    resolved = _resolve_app_name(app_name)
    deadline = time.monotonic() + timeout
    pid = None

    while time.monotonic() < deadline:
        pid = _get_app_pid(resolved)
        if pid is not None:
            break
        time.sleep(0.2)

    if pid is None:
        return None

    _enable_enhanced_accessibility(pid)
    time.sleep(0.3)

    while time.monotonic() < deadline:
        try:
            app_el = AXUIElementCreateApplication(pid)
            windows = _get_ax_attr(app_el, kAXWindowsAttribute)
            if windows and len(windows) > 0:
                children = _get_ax_attr(windows[0], kAXChildrenAttribute)
                if children and len(children) > 0:
                    return pid
        except Exception:
            pass
        time.sleep(0.25)

    return pid


def focus_app(app_name: str) -> dict:
    """Bring an app to the foreground."""
    resolved = _resolve_app_name(app_name)
    script = f'tell application "{resolved}" to activate'
    result = _run_applescript(script)
    time.sleep(0.5)
    pid = _get_app_pid(resolved)
    _enable_enhanced_accessibility(pid)
    return result


def quit_app(app_name: str) -> dict:
    """Quit an app gracefully."""
    resolved = _resolve_app_name(app_name)
    script = f'tell application "{resolved}" to quit'
    return _run_applescript(script)


# ─── UI Tree Reading ─────────────────────────────────────────────────────────

_ACTIONABLE_ROLES = frozenset([
    "AXButton", "AXLink", "AXMenuItem", "AXMenuBarItem", "AXMenu",
    "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXComboBox",
    "AXSlider", "AXIncrementor", "AXDisclosureTriangle",
    "AXCell", "AXRow", "AXOutlineRow", "AXTabGroup", "AXTab",
    "AXToolbar", "AXToolbarButton", "AXImage",
])

_TYPEABLE_ROLES = frozenset([
    "AXTextField", "AXTextArea", "AXComboBox", "AXSearchField",
    "AXSecureTextField",
])

_SCROLLABLE_ROLES = frozenset([
    "AXScrollArea", "AXScrollBar", "AXTable", "AXOutline", "AXList",
    "AXBrowser",
])

_SKIP_ROLES = frozenset([
    "AXSplitter", "AXGrowArea", "AXRuler", "AXRulerMarker",
    "AXMatte", "AXValueIndicator", "AXUnknown",
])

_TRANSIENT_ROLES = frozenset(["AXSystemDialog", "AXNotificationCenter"])
_TRANSIENT_KEYWORDS = frozenset([
    "crash", "problem report", "not responding", "quit unexpectedly",
    "notification center", "update available",
])


def _is_transient(role: str, title: str, desc: str) -> bool:
    """Return True for system dialogs, crash reporters, notification banners."""
    if role in _TRANSIENT_ROLES:
        return True
    text = f"{title} {desc}".lower()
    return any(kw in text for kw in _TRANSIENT_KEYWORDS)


def _is_visible(element) -> bool:
    """Check if an element has a non-zero bounding box and is not hidden."""
    hidden = _get_ax_attr(element, "AXHidden")
    if hidden:
        return False
    rect = _get_element_position_size(element)
    if rect is not None:
        _, _, w, h = rect
        if w < 2 or h < 2:
            return False
    return True


def _is_actionable(role: str, actions: list) -> bool:
    """An element is actionable if it has a press/confirm action or is a known role."""
    if role in _ACTIONABLE_ROLES:
        return True
    if "AXPress" in actions or "AXConfirm" in actions or "AXPick" in actions:
        return True
    return False


def _element_capabilities(role: str, actions: list) -> list:
    """Return human-readable capability tags for an element."""
    caps = []
    if role in _TYPEABLE_ROLES:
        caps.append("typeable")
    if _is_actionable(role, actions):
        caps.append("clickable")
    if role in _SCROLLABLE_ROLES:
        caps.append("scrollable")
    if "AXShowMenu" in actions:
        caps.append("has-menu")
    return caps


def get_ui_tree(app_name: str, max_depth: int = 12) -> dict:
    """Read the accessibility UI tree of a running app."""
    if not PYOBJC_AVAILABLE:
        return {"error": "pyobjc not available", "elements": []}

    pid = _get_app_pid(app_name)
    if pid is None:
        return {"error": f"App '{app_name}' not running", "elements": []}

    _enable_enhanced_accessibility(pid)

    try:
        app_element = AXUIElementCreateApplication(pid)
        tree = _read_element(app_element, depth=0, max_depth=max_depth)
        return {"app": app_name, "pid": pid, "tree": tree}
    except Exception as e:
        return {"error": str(e), "elements": []}


def get_focused_app_ui_tree(max_depth: int = 12) -> dict:
    """Read the UI tree of whichever app is currently focused."""
    if not PYOBJC_AVAILABLE:
        return {"error": "pyobjc not available"}

    workspace = NSWorkspace.sharedWorkspace()
    active = workspace.frontmostApplication()
    if active is None:
        return {"error": "No active application"}

    name = active.localizedName()
    pid = active.processIdentifier()
    _enable_enhanced_accessibility(pid)

    try:
        app_element = AXUIElementCreateApplication(pid)
        tree = _read_element(app_element, depth=0, max_depth=max_depth)
        return {"app": name, "pid": pid, "tree": tree}
    except Exception as e:
        return {"error": str(e)}


def _read_element(element, depth: int, max_depth: int) -> dict:
    """Recursively read an AX element into a plain dict."""
    if depth > max_depth:
        return {"truncated": True}

    node = {}

    role = _get_ax_attr(element, kAXRoleAttribute)
    if role:
        node["role"] = str(role)

    title = _get_ax_attr(element, kAXTitleAttribute)
    if title:
        node["title"] = str(title)

    desc = _get_ax_attr(element, kAXDescriptionAttribute)
    if desc:
        node["description"] = str(desc)

    value = _get_ax_attr(element, kAXValueAttribute)
    if value is not None:
        v = str(value)
        if len(v) < 500:
            node["value"] = v

    enabled = _get_ax_attr(element, kAXEnabledAttribute)
    if enabled is not None:
        node["enabled"] = bool(enabled)

    rect = _get_element_position_size(element)
    if rect is not None:
        node["position"] = {"x": rect[0], "y": rect[1],
                            "width": rect[2], "height": rect[3]}

    actions = _get_ax_actions(element)
    if actions:
        node["actions"] = actions

    children_raw = _get_ax_attr(element, kAXChildrenAttribute)
    if children_raw:
        children = []
        try:
            for child in children_raw:
                child_node = _read_element(child, depth + 1, max_depth)
                if child_node and child_node != {"truncated": True}:
                    children.append(child_node)
        except Exception:
            pass
        if children:
            node["children"] = children

    return node


# ─── Indexed Flat Tree ────────────────────────────────────────────────────────

def flatten_ui_tree(tree: dict, max_elements: int = 200, verbose: bool = False) -> str:
    """Convert the nested UI tree into a flat, indexed, human-readable string.

    Every actionable/typeable element gets a sequential index [1] [2] [3].
    The element registry is saved to disk so click-by-index can use it.
    """
    lines = []
    registry = {}
    counter = [0]

    def _walk(node, depth=0):
        if len(lines) >= max_elements:
            return
        if not isinstance(node, dict):
            return

        role = node.get("role", "")
        title = node.get("title", "")
        value = node.get("value", "")
        desc = node.get("description", "")
        actions = node.get("actions", [])
        pos = node.get("position")
        enabled = node.get("enabled", True)

        if _is_transient(role, title, desc):
            return

        if role in _SKIP_ROLES:
            for child in node.get("children", []):
                _walk(child, depth)
            return

        # Skip empty containers unless verbose
        if not verbose:
            has_content = any([title, value, desc])
            if not has_content and role in ("AXGroup", "AXLayoutArea", "AXScrollArea", ""):
                for child in node.get("children", []):
                    _walk(child, depth)
                return

        # Skip invisible elements (zero-size)
        if pos:
            w = pos.get("width", 0)
            h = pos.get("height", 0)
            if w < 2 or h < 2:
                for child in node.get("children", []):
                    _walk(child, depth)
                return

        is_actionable = _is_actionable(role, actions)
        is_typeable = role in _TYPEABLE_ROLES
        is_indexed = is_actionable or is_typeable

        if is_indexed and enabled is not False:
            counter[0] += 1
            idx = counter[0]
            caps = _element_capabilities(role, actions)
            caps_str = f" ({', '.join(caps)})" if caps else ""

            label_parts = []
            if title:
                label_parts.append(f'"{title}"')
            if desc and desc != title:
                label_parts.append(f'({desc[:60]})')
            if value and value != title and role in _TYPEABLE_ROLES:
                val_display = value[:80]
                label_parts.append(f'= "{val_display}"')

            label = " ".join(label_parts) if label_parts else "(no label)"

            pos_str = ""
            if verbose and pos:
                pos_str = f" @({int(pos['x'])},{int(pos['y'])} {int(pos['width'])}x{int(pos['height'])})"

            lines.append(f"[{idx}] [{role}] {label}{caps_str}{pos_str}")

            reg_entry = {"index": idx, "role": role, "title": title,
                         "description": desc, "value": value}
            if pos:
                reg_entry["position"] = pos
            registry[str(idx)] = reg_entry

        elif title or value or desc or verbose:
            indent = "  " * min(depth, 4)
            parts = []
            if role:
                parts.append(f"[{role}]")
            if title:
                parts.append(f'"{title}"')
            if value and value != title:
                parts.append(f"= {value[:80]}")
            if desc and desc not in (title, value):
                parts.append(f"({desc[:60]})")
            if parts:
                lines.append(f"{indent}{' '.join(parts)}")

        for child in node.get("children", []):
            _walk(child, depth + 1)

    app = tree.get("app", "Unknown App")
    pid = tree.get("pid", "?")
    lines.append(f"APP: {app} (pid: {pid})")
    lines.append(f"Indexed elements below — use `click --index N` to interact.")
    lines.append("")

    root = tree.get("tree", {})
    if isinstance(root, dict):
        _walk(root)

    if tree.get("error"):
        lines.append(f"ERROR: {tree['error']}")

    lines.append(f"\n--- {counter[0]} actionable elements indexed ---")

    _save_element_registry(registry, app, pid)

    return "\n".join(lines[:max_elements + 5])


def _save_element_registry(registry: dict, app: str, pid) -> None:
    """Persist the element registry to disk for click-by-index."""
    data = {
        "app": app,
        "pid": pid,
        "timestamp": time.time(),
        "elements": registry,
    }
    try:
        with open(ELEMENT_REGISTRY_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Failed to save element registry: {e}")


def _load_element_registry() -> dict:
    """Load the element registry from disk."""
    try:
        with open(ELEMENT_REGISTRY_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


# ─── Actions ──────────────────────────────────────────────────────────────────

def keyboard_shortcut(shortcut: str) -> dict:
    """Execute a keyboard shortcut via AppleScript, with CGEvent fallback."""
    result = _keyboard_shortcut_applescript(shortcut)
    if result.get("success"):
        return result
    logger.warning(f"AppleScript shortcut failed, trying CGEvent for: {shortcut}")
    return _keyboard_shortcut_cgevent(shortcut)


def _keyboard_shortcut_applescript(shortcut: str) -> dict:
    """Primary: AppleScript keystroke via System Events."""
    key_map = {"cmd": "command", "ctrl": "control", "opt": "option",
               "alt": "option", "shift": "shift"}
    parts = shortcut.lower().replace(" ", "").split("+")
    key = parts[-1]
    modifiers = parts[:-1]
    special_key_codes = {
        "return": 36, "enter": 36, "escape": 53, "esc": 53, "tab": 48,
        "space": 49, "delete": 51, "up": 126, "down": 125, "left": 123,
        "right": 124, "f1": 122, "f2": 120, "f3": 99, "f4": 118,
        "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    }
    if modifiers:
        mod_str = " & ".join(f"{key_map.get(m, m)} down" for m in modifiers)
        if key in special_key_codes:
            script = f'tell application "System Events" to key code {special_key_codes[key]} using {{{mod_str}}}'
        else:
            script = f'tell application "System Events" to keystroke "{key}" using {{{mod_str}}}'
    else:
        if key in special_key_codes:
            script = f'tell application "System Events" to key code {special_key_codes[key]}'
        else:
            script = f'tell application "System Events" to keystroke "{key}"'
    result = _run_applescript(script)
    time.sleep(0.5 if modifiers else 0.2)
    return result


def _keyboard_shortcut_cgevent(shortcut: str) -> dict:
    """Fallback: CGEvent keyboard shortcut."""
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
            kCGHIDEventTap, kCGEventFlagMaskCommand, kCGEventFlagMaskControl,
            kCGEventFlagMaskAlternate, kCGEventFlagMaskShift,
        )
    except ImportError:
        return {"success": False, "error": "Quartz not available"}

    KEY_CODES = {
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
        "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
        "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
        "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
        "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37,
        "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
        "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50,
        "delete": 51, "return": 36, "enter": 36, "escape": 53, "esc": 53,
        "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
        "up": 126, "down": 125, "left": 123, "right": 124,
        "pageup": 116, "pagedown": 121, "home": 115, "end": 119,
    }
    FLAG_MAP = {
        "cmd": kCGEventFlagMaskCommand, "command": kCGEventFlagMaskCommand,
        "ctrl": kCGEventFlagMaskControl, "control": kCGEventFlagMaskControl,
        "opt": kCGEventFlagMaskAlternate, "alt": kCGEventFlagMaskAlternate,
        "option": kCGEventFlagMaskAlternate, "shift": kCGEventFlagMaskShift,
    }

    parts = shortcut.lower().replace(" ", "").split("+")
    key = parts[-1]
    modifiers = parts[:-1]
    key_code = KEY_CODES.get(key)
    if key_code is None:
        return {"success": False, "error": f"Unknown key: {key}"}

    flags = 0
    for mod in modifiers:
        flags |= FLAG_MAP.get(mod, 0)

    try:
        event_down = CGEventCreateKeyboardEvent(None, key_code, True)
        if flags:
            CGEventSetFlags(event_down, flags)
        CGEventPost(kCGHIDEventTap, event_down)
        time.sleep(0.05)
        event_up = CGEventCreateKeyboardEvent(None, key_code, False)
        if flags:
            CGEventSetFlags(event_up, flags)
        CGEventPost(kCGHIDEventTap, event_up)
        time.sleep(0.4 if flags else 0.15)
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def type_text(text: str, app_name: str = None) -> dict:
    """Type text via AppleScript System Events keystroke."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "System Events" to keystroke "{escaped}"'
    result = _run_applescript(script)
    if result.get("success"):
        time.sleep(0.3)
        return result
    logger.warning(f"AppleScript type_text failed, trying CGEvent")
    return _type_text_cgevent(text)


def _type_text_cgevent(text: str) -> dict:
    """Fallback: CGEvent Unicode character-by-character typing."""
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent, CGEventPost,
            CGEventKeyboardSetUnicodeString, kCGHIDEventTap,
        )
        time.sleep(0.15)
        for char in text:
            event_down = CGEventCreateKeyboardEvent(None, 0, True)
            CGEventKeyboardSetUnicodeString(event_down, 1, char)
            CGEventPost(kCGHIDEventTap, event_down)
            event_up = CGEventCreateKeyboardEvent(None, 0, False)
            CGEventKeyboardSetUnicodeString(event_up, 1, char)
            CGEventPost(kCGHIDEventTap, event_up)
            time.sleep(0.04)
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─── Clicking ─────────────────────────────────────────────────────────────────

def _mouse_click(x: float, y: float, click_type: str = "left",
                 click_count: int = 1) -> dict:
    """Low-level mouse click at screen coordinates using CGEvent.

    click_type: "left", "right"
    click_count: 1 for single, 2 for double
    """
    try:
        from Quartz import (
            CGEventCreateMouseEvent, CGEventPost, CGEventSetIntegerValueField,
            kCGHIDEventTap, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
            kCGEventRightMouseDown, kCGEventRightMouseUp,
            kCGMouseEventClickState, CGPointMake,
        )

        point = CGPointMake(float(x), float(y))

        if click_type == "right":
            down_type = kCGEventRightMouseDown
            up_type = kCGEventRightMouseUp
        else:
            down_type = kCGEventLeftMouseDown
            up_type = kCGEventLeftMouseUp

        for i in range(click_count):
            down = CGEventCreateMouseEvent(None, down_type, point, 0)
            up = CGEventCreateMouseEvent(None, up_type, point, 0)
            CGEventSetIntegerValueField(down, kCGMouseEventClickState, i + 1)
            CGEventSetIntegerValueField(up, kCGMouseEventClickState, i + 1)
            CGEventPost(kCGHIDEventTap, down)
            time.sleep(0.05)
            CGEventPost(kCGHIDEventTap, up)
            if i < click_count - 1:
                time.sleep(0.05)

        time.sleep(0.3)
        return {"success": True, "error": None, "clicked_at": [x, y]}
    except Exception as e:
        return {"success": False, "error": f"Mouse click failed: {e}"}


def click_at_pixel(x: float, y: float) -> dict:
    """Click at raw screen coordinates (x, y)."""
    return _mouse_click(x, y, "left", 1)


def double_click_at_pixel(x: float, y: float) -> dict:
    """Double-click at raw screen coordinates."""
    return _mouse_click(x, y, "left", 2)


def right_click_at_pixel(x: float, y: float) -> dict:
    """Right-click at raw screen coordinates."""
    return _mouse_click(x, y, "right", 1)


def click_by_index(index: int, click_type: str = "left",
                   click_count: int = 1, prefer_press: bool = True) -> dict:
    """Click an element by its index from the last tree output.

    Strategy:
    1. If the element has a stable title and role in the registry AND the
       caller wants a single left-click, try AXPress first. This is
       focus-independent (works when the target window isn't the topmost
       visible one), bypasses CGEvent routing entirely, and is the most
       reliable way to invoke a button-like AX element.
    2. Otherwise (or if AXPress fails), fall back to a coordinate-based
       mouse click at the element's center using the registry's stored
       position. This is required for right-clicks, double-clicks, and
       elements that don't support AXPress (drag handles, sliders, etc.).
    """
    registry = _load_element_registry()
    if not registry or "elements" not in registry:
        return {"success": False,
                "error": "No element registry found. Run `tree --flat` first."}

    elements = registry["elements"]
    entry = elements.get(str(index))
    if entry is None:
        return {"success": False,
                "error": f"Index {index} not found in registry "
                         f"(max: {max(int(k) for k in elements.keys()) if elements else 0})"}

    role = entry.get("role")
    title = entry.get("title")
    pid_val = registry.get("pid")
    element_meta = {"index": index, "role": role, "title": title}

    if (
        prefer_press
        and click_type == "left"
        and click_count == 1
        and role
        and title
        and pid_val
    ):
        try:
            app_element = AXUIElementCreateApplication(int(pid_val))
            ax_el = _find_actionable_by_role_and_title(
                app_element, role, title, depth=0, max_depth=30,
            )
            if ax_el is not None:
                err = AXUIElementPerformAction(ax_el, kAXPressAction)
                if err == kAXErrorSuccess:
                    time.sleep(0.3)
                    return {
                        "success": True,
                        "error": None,
                        "method": "AXPress",
                        "element": element_meta,
                    }
        except Exception:
            pass

    pos = entry.get("position")
    if pos and pos.get("width", 0) > 0 and pos.get("height", 0) > 0:
        cx = pos["x"] + pos["width"] / 2
        cy = pos["y"] + pos["height"] / 2
        result = _mouse_click(cx, cy, click_type, click_count)
        if result.get("success"):
            result["element"] = element_meta
            result.setdefault("method", "coordinate")
            return result

    if pid_val:
        try:
            app_element = AXUIElementCreateApplication(int(pid_val))
            ax_el = _find_element_by_index_walk(app_element, index, [0], 0, 12)
            if ax_el is not None:
                err = AXUIElementPerformAction(ax_el, kAXPressAction)
                if err == kAXErrorSuccess:
                    time.sleep(0.3)
                    return {
                        "success": True,
                        "error": None,
                        "method": "AXPress-by-walk",
                        "element": element_meta,
                    }
        except Exception:
            pass

    return {
        "success": False,
        "error": f"Could not click element at index {index}: AXPress failed and no position data",
    }


def _find_element_by_index_walk(element, target_index: int, counter: list,
                                depth: int, max_depth: int):
    """Walk the AX tree and return the element at the given sequential index."""
    if depth > max_depth:
        return None

    role = str(_get_ax_attr(element, kAXRoleAttribute) or "")
    actions = _get_ax_actions(element)
    is_actionable = _is_actionable(role, actions)
    is_typeable = role in _TYPEABLE_ROLES

    if is_actionable or is_typeable:
        counter[0] += 1
        if counter[0] == target_index:
            return element

    children = _get_ax_attr(element, kAXChildrenAttribute)
    if children:
        for child in children:
            found = _find_element_by_index_walk(
                child, target_index, counter, depth + 1, max_depth)
            if found is not None:
                return found
    return None


def _find_actionable_by_role_and_title(element, role: str, title: str,
                                       depth: int = 0, max_depth: int = 30):
    """Walk the live AX tree, return the first element with matching role + exact title.

    Used for AXPress-based clicking that doesn't depend on screen coordinates
    (so it works even when the target window isn't the topmost visible one).
    Title comparison is exact and case-sensitive; the title is normally captured
    from the same registry that the user clicks against, so it's stable.
    """
    if depth > max_depth or element is None:
        return None
    el_role = str(_get_ax_attr(element, kAXRoleAttribute) or "")
    el_title = str(_get_ax_attr(element, kAXTitleAttribute) or "")
    el_desc = str(_get_ax_attr(element, kAXDescriptionAttribute) or "")
    if el_role == role and (el_title == title or el_desc == title):
        return element
    children = _get_ax_attr(element, kAXChildrenAttribute)
    if children:
        for child in children:
            found = _find_actionable_by_role_and_title(
                child, role, title, depth + 1, max_depth)
            if found is not None:
                return found
    return None


def click_element_by_label(app_name: str, label: str,
                           click_type: str = "left",
                           click_count: int = 1) -> dict:
    """Click a UI element by its title/label. Coordinate-based primary, AXPress fallback."""
    if not PYOBJC_AVAILABLE:
        return {"success": False, "error": "pyobjc not available"}

    pid = _get_app_pid(app_name)
    if pid is None:
        return {"success": False, "error": f"App '{app_name}' not running"}

    _enable_enhanced_accessibility(pid)

    try:
        app_element = AXUIElementCreateApplication(pid)
        element = _find_element_by_title(app_element, label, depth=0, max_depth=12)
        if element is None:
            return {"success": False, "error": f"Element '{label}' not found"}

        rect = _get_element_position_size(element)
        if rect is not None:
            x, y, w, h = rect
            cx, cy = x + w / 2, y + h / 2
            result = _mouse_click(cx, cy, click_type, click_count)
            if result.get("success"):
                return result

        if click_type == "left" and click_count == 1:
            err = AXUIElementPerformAction(element, kAXPressAction)
            if err == kAXErrorSuccess:
                time.sleep(0.3)
                return {"success": True, "error": None, "method": "AXPress"}

        return {"success": False, "error": f"Could not click element '{label}'"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _find_element_by_title(element, target: str, depth: int, max_depth: int):
    """Recursively find an AX element matching the target title/description."""
    if depth > max_depth:
        return None

    for attr in [kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute]:
        val = _get_ax_attr(element, attr)
        if val and target.lower() in str(val).lower():
            return element

    children = _get_ax_attr(element, kAXChildrenAttribute)
    if children:
        for child in children:
            found = _find_element_by_title(child, target, depth + 1, max_depth)
            if found:
                return found
    return None


def focus_element_by_index(index: int) -> dict:
    """Focus (set keyboard focus to) an element by its index."""
    registry = _load_element_registry()
    if not registry or "elements" not in registry:
        return {"success": False, "error": "No element registry. Run `tree --flat` first."}

    entry = registry["elements"].get(str(index))
    if entry is None:
        return {"success": False, "error": f"Index {index} not found in registry"}

    pos = entry.get("position")
    if pos and pos.get("width", 0) > 0:
        cx = pos["x"] + pos["width"] / 2
        cy = pos["y"] + pos["height"] / 2
        return _mouse_click(cx, cy, "left", 1)

    return {"success": False, "error": f"Cannot focus element {index}: no position"}


# ─── Scroll ───────────────────────────────────────────────────────────────────

def scroll(direction: str = "down", amount: int = 3) -> dict:
    """Scroll using CGEvent scroll wheel events."""
    try:
        from Quartz import (
            CGEventCreateScrollWheelEvent, CGEventPost,
            kCGHIDEventTap, kCGScrollEventUnitLine,
        )

        delta = -amount if direction == "down" else amount
        event = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitLine, 1, delta)
        CGEventPost(kCGHIDEventTap, event)
        time.sleep(0.3)
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": f"Scroll failed: {e}"}


# ─── Mouse Movement / Drag / Hover ───────────────────────────────────────────

def hover(x: float, y: float) -> dict:
    """Move mouse cursor to (x, y) without clicking."""
    try:
        from Quartz import (
            CGEventCreateMouseEvent, CGEventPost,
            kCGHIDEventTap, kCGEventMouseMoved, CGPointMake,
        )
        point = CGPointMake(float(x), float(y))
        event = CGEventCreateMouseEvent(None, kCGEventMouseMoved, point, 0)
        CGEventPost(kCGHIDEventTap, event)
        time.sleep(0.2)
        return {"success": True, "error": None, "position": [x, y]}
    except Exception as e:
        return {"success": False, "error": f"Hover failed: {e}"}


def drag(start_x: float, start_y: float, end_x: float, end_y: float,
         duration: float = 0.5) -> dict:
    """Click-drag from one point to another."""
    try:
        from Quartz import (
            CGEventCreateMouseEvent, CGEventPost,
            kCGHIDEventTap, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
            kCGEventLeftMouseDragged, CGPointMake,
        )

        start = CGPointMake(float(start_x), float(start_y))
        end = CGPointMake(float(end_x), float(end_y))

        down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, start, 0)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(0.1)

        steps = max(int(duration / 0.02), 5)
        for i in range(1, steps + 1):
            t = i / steps
            ix = start_x + (end_x - start_x) * t
            iy = start_y + (end_y - start_y) * t
            mid = CGPointMake(ix, iy)
            drag_ev = CGEventCreateMouseEvent(
                None, kCGEventLeftMouseDragged, mid, 0)
            CGEventPost(kCGHIDEventTap, drag_ev)
            time.sleep(0.02)

        up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, end, 0)
        CGEventPost(kCGHIDEventTap, up)
        time.sleep(0.3)
        return {"success": True, "error": None,
                "from": [start_x, start_y], "to": [end_x, end_y]}
    except Exception as e:
        return {"success": False, "error": f"Drag failed: {e}"}


# ─── Screenshot ───────────────────────────────────────────────────────────────

def take_screenshot(app_name: str = None, region: str = None) -> dict:
    """Capture a screenshot. Uses screencapture CLI for reliability.

    app_name: capture only that app's main window
    region: "x,y,w,h" to capture a specific rectangle
    Returns path to the saved PNG.
    """
    fd, path = tempfile.mkstemp(prefix="omniclaw_screenshot_", suffix=".png")
    os.close(fd)

    try:
        if app_name:
            pid = _get_app_pid(app_name)
            if pid is None:
                return {"success": False, "error": f"App '{app_name}' not running"}

            # Find the main window (largest named window) for this app
            from Quartz import (
                CGWindowListCopyWindowInfo, kCGWindowListOptionAll,
                kCGNullWindowID,
            )
            window_list = CGWindowListCopyWindowInfo(
                kCGWindowListOptionAll, kCGNullWindowID)

            best_wid = None
            best_area = 0
            for win in window_list:
                if win.get("kCGWindowOwnerPID") != pid:
                    continue
                bounds = win.get("kCGWindowBounds", {})
                w = bounds.get("Width", 0)
                h = bounds.get("Height", 0)
                area = w * h
                if area > best_area:
                    best_area = area
                    best_wid = win.get("kCGWindowNumber")

            if best_wid is not None:
                result = subprocess.run(
                    ["screencapture", "-l", str(best_wid), "-o", "-x", path],
                    capture_output=True, text=True, timeout=10)
                if result.returncode == 0 and os.path.exists(path):
                    sz = os.path.getsize(path)
                    if sz > 0:
                        return {"success": True, "path": path,
                                "width": None, "height": None, "error": None}

        elif region:
            parts = region.split(",")
            if len(parts) == 4:
                x, y, w, h = parts
                x2 = str(float(x) + float(w))
                y2 = str(float(y) + float(h))
                result = subprocess.run(
                    ["screencapture", "-R",
                     f"{x},{y},{w},{h}", "-x", path],
                    capture_output=True, text=True, timeout=10)
                if result.returncode == 0 and os.path.exists(path):
                    return {"success": True, "path": path,
                            "width": int(float(w)), "height": int(float(h)),
                            "error": None}

        # Full screen capture
        result = subprocess.run(
            ["screencapture", "-x", path],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and os.path.exists(path):
            w_s, h_s = get_screen_size()
            return {"success": True, "path": path,
                    "width": w_s, "height": h_s, "error": None}

        return {"success": False, "error": "screencapture command failed"}

    except Exception as e:
        return {"success": False, "error": f"Screenshot failed: {e}"}


# ─── Screen Info ──────────────────────────────────────────────────────────────

def get_screen_size() -> tuple[int, int]:
    """Return the main screen resolution as (width, height)."""
    try:
        from Quartz import CGDisplayBounds, CGMainDisplayID
        bounds = CGDisplayBounds(CGMainDisplayID())
        return int(bounds.size.width), int(bounds.size.height)
    except Exception:
        return 1920, 1080


def get_focused_app_name() -> str:
    """Return the name of the currently focused (frontmost) application."""
    result = _run_applescript(
        'tell application "System Events" to get name of first '
        'application process whose frontmost is true'
    )
    if result.get("success"):
        return result.get("output", "Unknown")
    return "Unknown"


# ─── AppleScript Helper ──────────────────────────────────────────────────────

def _run_applescript(script: str) -> dict:
    """Run an AppleScript string via osascript subprocess."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return {"success": True, "output": result.stdout.strip(),
                    "error": None}
        else:
            return {"success": False, "output": "",
                    "error": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "AppleScript timeout"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}
