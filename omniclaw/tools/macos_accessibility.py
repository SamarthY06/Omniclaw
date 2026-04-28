"""
macOS Accessibility Layer — wraps AXUIElement via pyobjc.
This is the lightweight MVP version of OculOS for macOS.
All reads are synchronous (AX API is sync); we run them in
asyncio.to_thread() at the call site to keep the event loop free.
"""

import subprocess
import time
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

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
}


def _resolve_app_name(name: str) -> str:
    """Map common short names / aliases to the real macOS app name."""
    return _APP_ALIASES.get(name.lower().strip(), name)


# ─── App Launch ──────────────────────────────────────────────────────────────

def launch_app(app_name: str) -> dict:
    """Launch an app by name (supports common aliases). Returns {success, pid, error}."""
    resolved = _resolve_app_name(app_name)
    try:
        result = subprocess.run(
            ["open", "-a", resolved],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            time.sleep(1.5)
            pid = _get_app_pid(resolved)
            return {"success": True, "pid": pid, "error": None}
        else:
            return {
                "success": False, "pid": None,
                "error": f"Unable to find application named '{resolved}'"
                         if "Unable to find" in result.stderr
                         else result.stderr.strip(),
            }
    except Exception as e:
        return {"success": False, "pid": None, "error": str(e)}


def focus_app(app_name: str) -> dict:
    """Bring an app to the foreground (supports common aliases)."""
    resolved = _resolve_app_name(app_name)
    script = f'tell application "{resolved}" to activate'
    result = _run_applescript(script)
    time.sleep(0.5)
    return result


def quit_app(app_name: str) -> dict:
    """Quit an app gracefully (supports common aliases)."""
    resolved = _resolve_app_name(app_name)
    script = f'tell application "{resolved}" to quit'
    return _run_applescript(script)


def _get_app_pid(app_name: str) -> Optional[int]:
    """Find the PID of a running app by name."""
    if not PYOBJC_AVAILABLE:
        return None
    resolved = _resolve_app_name(app_name)
    workspace = NSWorkspace.sharedWorkspace()
    for app in workspace.runningApplications():
        name = app.localizedName()
        if name and resolved.lower() in name.lower():
            return app.processIdentifier()
    return None


# ─── UI Tree Reading ──────────────────────────────────────────────────────────

def get_ui_tree(app_name: str, max_depth: int = 6) -> dict:
    """
    Read the accessibility UI tree of a running app.
    Returns a nested dict representing the element tree.
    Capped at max_depth to avoid huge trees.
    """
    if not PYOBJC_AVAILABLE:
        return {"error": "pyobjc not available", "elements": []}

    pid = _get_app_pid(app_name)
    if pid is None:
        return {"error": f"App '{app_name}' not running", "elements": []}

    try:
        app_element = AXUIElementCreateApplication(pid)
        tree = _read_element(app_element, depth=0, max_depth=max_depth)
        return {"app": app_name, "pid": pid, "tree": tree}
    except Exception as e:
        return {"error": str(e), "elements": []}


def get_focused_app_ui_tree(max_depth: int = 5) -> dict:
    """Read the UI tree of whichever app is currently focused."""
    if not PYOBJC_AVAILABLE:
        return {"error": "pyobjc not available"}

    workspace = NSWorkspace.sharedWorkspace()
    active = workspace.frontmostApplication()
    if active is None:
        return {"error": "No active application"}

    name = active.localizedName()
    pid = active.processIdentifier()

    try:
        app_element = AXUIElementCreateApplication(pid)
        tree = _read_element(app_element, depth=0, max_depth=max_depth)
        return {"app": name, "pid": pid, "tree": tree}
    except Exception as e:
        return {"error": str(e)}


def _get_ax_attr(element, attr: str):
    """Safely read a single AX attribute value."""
    try:
        err, value = AXUIElementCopyAttributeValue(element, attr, None)
        if err == kAXErrorSuccess:
            return value
    except Exception:
        pass
    return None


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
        if len(v) < 500:  # cap long values
            node["value"] = v

    enabled = _get_ax_attr(element, kAXEnabledAttribute)
    if enabled is not None:
        node["enabled"] = bool(enabled)

    # Read children
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


def flatten_ui_tree(tree: dict, max_elements: int = 80) -> str:
    """
    Convert the nested UI tree into a flat, human-readable string
    for injection into the LLM prompt. Caps at max_elements lines.
    """
    lines = []

    def _walk(node, indent=0):
        if len(lines) >= max_elements:
            return
        if not isinstance(node, dict):
            return

        role = node.get("role", "")
        title = node.get("title", "")
        value = node.get("value", "")
        desc = node.get("description", "")

        # Skip empty container nodes
        if not any([title, value, desc]) and role in ("AXGroup", "AXLayoutArea", ""):
            for child in node.get("children", []):
                _walk(child, indent)
            return

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
            lines.append("  " * indent + " ".join(parts))

        for child in node.get("children", []):
            _walk(child, indent + 1)

    app = tree.get("app", "Unknown App")
    lines.append(f"APP: {app}")

    root = tree.get("tree", {})
    if isinstance(root, dict):
        _walk(root)

    if tree.get("error"):
        lines.append(f"ERROR: {tree['error']}")

    return "\n".join(lines[:max_elements])


# ─── Actions ─────────────────────────────────────────────────────────────────

def keyboard_shortcut(shortcut: str) -> dict:
    """Execute a keyboard shortcut via AppleScript System Events.

    AppleScript is used as the primary method because it reliably delivers
    keystrokes to the frontmost application (Chrome, Notes, etc.).
    CGEvent is the fallback if AppleScript fails.
    """
    result = _keyboard_shortcut_applescript(shortcut)
    if result.get("success"):
        return result
    logger.warning(f"AppleScript shortcut failed, trying CGEvent for: {shortcut}")
    return _keyboard_shortcut_cgevent(shortcut)


def _keyboard_shortcut_applescript(shortcut: str) -> dict:
    """Primary: AppleScript keystroke via System Events."""
    key_map = {"cmd": "command", "ctrl": "control", "opt": "option", "alt": "option", "shift": "shift"}
    parts = shortcut.lower().replace(" ", "").split("+")
    key = parts[-1]
    modifiers = parts[:-1]
    special_key_codes = {
        "return": 36, "enter": 36, "escape": 53, "esc": 53, "tab": 48,
        "space": 49, "delete": 51, "up": 126, "down": 125, "left": 123, "right": 124,
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
            CGEventCreateKeyboardEvent,
            CGEventPost,
            CGEventSetFlags,
            kCGHIDEventTap,
            kCGEventFlagMaskCommand,
            kCGEventFlagMaskControl,
            kCGEventFlagMaskAlternate,
            kCGEventFlagMaskShift,
        )
    except ImportError:
        return {"success": False, "error": "Quartz not available"}

    KEY_CODES = {
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
        "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16,
        "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
        "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30,
        "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
        "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45,
        "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50, "delete": 51,
        "return": 36, "enter": 36, "escape": 53, "esc": 53,
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
    """Type text via AppleScript System Events keystroke.

    This is the most reliable method for delivering text to input fields
    in Chrome, Notes, and other macOS apps. The text appears visibly as
    if the user typed it.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "System Events" to keystroke "{escaped}"'
    result = _run_applescript(script)
    if result.get("success"):
        time.sleep(0.3)
        return result
    logger.warning(f"AppleScript type_text failed: {result.get('error')}, trying CGEvent")
    return _type_text_cgevent(text)


def _type_text_cgevent(text: str) -> dict:
    """Fallback: CGEvent Unicode character-by-character typing."""
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPost,
            CGEventKeyboardSetUnicodeString,
            kCGHIDEventTap,
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


def click_element_by_title(app_name: str, title: str) -> dict:
    """Click a UI element by its title/label in the given app."""
    if not PYOBJC_AVAILABLE:
        return {"success": False, "error": "pyobjc not available"}

    pid = _get_app_pid(app_name)
    if pid is None:
        return {"success": False, "error": f"App '{app_name}' not running"}

    try:
        app_element = AXUIElementCreateApplication(pid)
        element = _find_element_by_title(app_element, title, depth=0, max_depth=6)
        if element is None:
            return {"success": False, "error": f"Element '{title}' not found"}

        err = AXUIElementPerformAction(element, kAXPressAction)
        if err == kAXErrorSuccess:
            time.sleep(0.3)
            return {"success": True, "error": None}
        else:
            return {"success": False, "error": f"AX press failed with code {err}"}
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


def scroll(direction: str = "down", amount: int = 3) -> dict:
    """Scroll the currently focused scroll area."""
    if direction == "down":
        script = f'tell application "System Events" to scroll down by {amount}'
    else:
        script = f'tell application "System Events" to scroll up by {amount}'

    # Fallback: use keyboard
    key = "down arrow" if direction == "down" else "up arrow"
    script = f'tell application "System Events" to key code {125 if direction == "down" else 126}'
    # Better: use Page Down/Up
    page_code = 121 if direction == "down" else 116
    script = f'tell application "System Events" to key code {page_code}'
    result = _run_applescript(script)
    time.sleep(0.2)
    return result


def navigate_to_url(url: str) -> dict:
    """Focus the active browser's address bar, type a URL, and press Enter.

    Timings are generous so the user can see each phase happening.
    """
    keyboard_shortcut("cmd+l")
    time.sleep(0.8)
    type_text(url)
    time.sleep(0.5)
    keyboard_shortcut("return")
    time.sleep(3.0)
    return {"success": True, "url": url}


def read_text_from_app(app_name: str) -> str:
    """Read visible text content from the frontmost window of an app."""
    tree = get_ui_tree(app_name, max_depth=5)
    return flatten_ui_tree(tree, max_elements=120)


# ─── AppleScript Helper ───────────────────────────────────────────────────────

def _run_applescript(script: str) -> dict:
    """Run an AppleScript string via osascript subprocess."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return {"success": True, "output": result.stdout.strip(), "error": None}
        else:
            return {"success": False, "output": "", "error": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "AppleScript timeout"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


# ─── High-Level Composite Actions ────────────────────────────────────────────

def open_new_tab_chrome() -> dict:
    focus_app("Google Chrome")
    time.sleep(0.3)
    return keyboard_shortcut("cmd+t")


def search_google(query: str) -> dict:
    """Open Chrome, focus address bar, search Google."""
    focus_app("Google Chrome")
    time.sleep(0.3)
    keyboard_shortcut("cmd+l")
    time.sleep(0.3)
    type_text(query)
    time.sleep(0.2)
    keyboard_shortcut("return")
    time.sleep(2)
    return {"success": True, "query": query}
