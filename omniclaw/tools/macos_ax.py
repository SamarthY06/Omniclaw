#!/usr/bin/env python3
"""CLI wrapper for macos_accessibility.py -- called by OpenClaw exec tool.

Every command prints a JSON object to stdout:
  {"ok": true, ...}   on success
  {"ok": false, "error": "..."}  on failure
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from macos_accessibility import (
    launch_app,
    focus_app,
    quit_app,
    get_focused_app_ui_tree,
    get_ui_tree,
    flatten_ui_tree,
    click_by_index,
    click_element_by_label,
    click_at_pixel,
    double_click_at_pixel,
    right_click_at_pixel,
    focus_element_by_index,
    type_text,
    keyboard_shortcut,
    scroll,
    hover,
    drag,
    take_screenshot,
    get_screen_size,
    get_focused_app_name,
)


def _json(data):
    print(json.dumps(data, ensure_ascii=False))


# ─── App Commands ─────────────────────────────────────────────────────────────

def cmd_launch(args):
    result = launch_app(args.app, wait=True)
    out = {"ok": result["success"], "pid": result.get("pid"),
           "error": result.get("error")}
    if result["success"]:
        tree = get_ui_tree(args.app, max_depth=args.depth if hasattr(args, 'depth') else 12)
        flat = flatten_ui_tree(tree, max_elements=200)
        out["tree"] = flat
    _json(out)


def cmd_focus(args):
    result = focus_app(args.app)
    _json({"ok": result.get("success", False), "error": result.get("error")})


def cmd_quit(args):
    result = quit_app(args.app)
    _json({"ok": result.get("success", False), "error": result.get("error")})


# ─── Tree Command ─────────────────────────────────────────────────────────────

def cmd_tree(args):
    depth = args.depth
    if args.app:
        tree = get_ui_tree(args.app, max_depth=depth)
    else:
        tree = get_focused_app_ui_tree(max_depth=depth)

    if args.flat:
        flat = flatten_ui_tree(tree, max_elements=args.max_elements,
                               verbose=args.verbose)
        _json({"ok": True, "app": tree.get("app", "Unknown"), "tree": flat})
    else:
        _json({"ok": True, **tree})


# ─── Click Commands ───────────────────────────────────────────────────────────

def cmd_click(args):
    if args.index is not None:
        result = click_by_index(
            args.index,
            click_type="left",
            click_count=1,
            prefer_press=not getattr(args, "no_press", False),
        )
        _json({"ok": result.get("success", False),
               "clicked_at": result.get("clicked_at"),
               "element": result.get("element"),
               "method": result.get("method", "coordinate"),
               "error": result.get("error")})
    elif args.label:
        app = args.app or get_focused_app_name()
        result = click_element_by_label(app, args.label,
                                        click_type="left", click_count=1)
        _json({"ok": result.get("success", False),
               "clicked_at": result.get("clicked_at"),
               "method": result.get("method", "coordinate"),
               "error": result.get("error")})
    else:
        _json({"ok": False, "error": "Provide --index N or --label 'text'"})


def cmd_double_click(args):
    if args.index is not None:
        result = click_by_index(args.index, click_type="left", click_count=2)
    elif args.label:
        app = args.app or get_focused_app_name()
        result = click_element_by_label(app, args.label,
                                        click_type="left", click_count=2)
    else:
        _json({"ok": False, "error": "Provide --index N or --label 'text'"})
        return
    _json({"ok": result.get("success", False),
           "clicked_at": result.get("clicked_at"),
           "element": result.get("element"),
           "error": result.get("error")})


def cmd_right_click(args):
    if args.index is not None:
        result = click_by_index(args.index, click_type="right", click_count=1)
    elif args.label:
        app = args.app or get_focused_app_name()
        result = click_element_by_label(app, args.label,
                                        click_type="right", click_count=1)
    else:
        _json({"ok": False, "error": "Provide --index N or --label 'text'"})
        return
    _json({"ok": result.get("success", False),
           "clicked_at": result.get("clicked_at"),
           "element": result.get("element"),
           "error": result.get("error")})


def cmd_click_at(args):
    if getattr(args, "app", None):
        focus_app(args.app)
        import time
        time.sleep(0.25)
    result = click_at_pixel(args.x, args.y)
    _json({"ok": result.get("success", False),
           "clicked_at": result.get("clicked_at"),
           "focused_app": getattr(args, "app", None),
           "error": result.get("error")})


# ─── Type Command ─────────────────────────────────────────────────────────────

def cmd_type(args):
    if args.index is not None:
        focus_result = focus_element_by_index(args.index)
        if not focus_result.get("success"):
            _json({"ok": False,
                   "error": f"Could not focus element {args.index}: "
                            f"{focus_result.get('error')}"})
            return
        import time
        time.sleep(0.2)
    elif args.app:
        focus_app(args.app)

    result = type_text(args.text)
    _json({"ok": result.get("success", False), "error": result.get("error")})


# ─── Shortcut Command ────────────────────────────────────────────────────────

def cmd_shortcut(args):
    result = keyboard_shortcut(args.keys)
    _json({"ok": result.get("success", False), "error": result.get("error")})


# ─── Scroll Command ──────────────────────────────────────────────────────────

def cmd_scroll(args):
    result = scroll(direction=args.direction, amount=args.amount)
    _json({"ok": result.get("success", False), "error": result.get("error")})


# ─── Mouse Commands ──────────────────────────────────────────────────────────

def cmd_hover(args):
    result = hover(args.x, args.y)
    _json({"ok": result.get("success", False),
           "position": result.get("position"),
           "error": result.get("error")})


def cmd_drag(args):
    result = drag(args.start_x, args.start_y, args.end_x, args.end_y,
                  duration=args.duration)
    _json({"ok": result.get("success", False),
           "from": result.get("from"),
           "to": result.get("to"),
           "error": result.get("error")})


# ─── Screenshot Command ──────────────────────────────────────────────────────

def cmd_screenshot(args):
    result = take_screenshot(app_name=args.app, region=args.region)
    _json({"ok": result.get("success", False),
           "path": result.get("path"),
           "width": result.get("width"),
           "height": result.get("height"),
           "error": result.get("error")})


# ─── Info Commands ────────────────────────────────────────────────────────────

def cmd_screen_size(args):
    w, h = get_screen_size()
    _json({"ok": True, "width": w, "height": h})


def cmd_focused_app(args):
    name = get_focused_app_name()
    _json({"ok": True, "app": name})


_KNOWN_BROWSERS = {
    "Google Chrome", "Brave Browser", "Safari", "Firefox",
    "Microsoft Edge", "Arc", "Opera", "Vivaldi", "Chromium",
    "Orion", "Zen Browser",
}


_TOOL_SCHEMAS = [
    {
        "name": "mac_launch",
        "description": "Launch / activate a macOS app, wait until ready, return its indexed UI tree.",
        "parameters": {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]},
        "sensitivity": "S0",
    },
    {
        "name": "mac_focus",
        "description": "Bring an already-running app's window to the front.",
        "parameters": {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]},
        "sensitivity": "S0",
    },
    {
        "name": "mac_quit",
        "description": "Quit a macOS app gracefully.",
        "parameters": {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"]},
        "sensitivity": "S1",
    },
    {
        "name": "mac_tree",
        "description": "Read the accessibility UI tree of an app, indexed.",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "depth": {"type": "integer", "default": 12},
                "max_elements": {"type": "integer", "default": 200},
            },
        },
        "sensitivity": "S0",
    },
    {
        "name": "mac_click",
        "description": "Click a UI element by index (from a recent tree) or by label.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
                "label": {"type": "string"},
                "app": {"type": "string"},
            },
        },
        "sensitivity": "S1",
    },
    {
        "name": "mac_click_at",
        "description": (
            "Click at absolute screen pixel coordinates (X, Y in points). PRIMARY "
            "click path for Electron / webview apps where mac_click by index/label "
            "is unreliable. Typical pairing: `vision_locate` -> {click_x, click_y} "
            "-> `mac_click_at`. ALWAYS pass `app` so the target is refocused right "
            "before the click; otherwise the synthetic mouse event lands on "
            "whatever window is topmost at that screen point (frequently the IDE)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "Screen X in points."},
                "y": {"type": "number", "description": "Screen Y in points."},
                "app": {
                    "type": "string",
                    "description": (
                        "App name to focus immediately before clicking. Strongly "
                        "recommended for any Electron/desktop click flow."
                    ),
                },
            },
            "required": ["x", "y"],
        },
        "sensitivity": "S1",
    },
    {
        "name": "mac_type",
        "description": "Type text into the focused input. Optionally focus an app first.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "app": {"type": "string"},
                "index": {"type": "integer"},
            },
            "required": ["text"],
        },
        "sensitivity": "S1",
    },
    {
        "name": "mac_shortcut",
        "description": "Press a keyboard shortcut, e.g. cmd+s.",
        "parameters": {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]},
        "sensitivity": "S1",
    },
    {
        "name": "mac_scroll",
        "description": "Scroll up or down N lines.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "integer", "default": 3},
            },
            "required": ["direction"],
        },
        "sensitivity": "S0",
    },
    {
        "name": "mac_screenshot",
        "description": "Capture full screen, an app window, or a region. Returns file path.",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "region": {"type": "string"},
            },
        },
        "sensitivity": "S0",
    },
    {
        "name": "mac_screen_size",
        "description": "Get the main screen resolution.",
        "parameters": {"type": "object", "properties": {}},
        "sensitivity": "S0",
    },
    {
        "name": "mac_focused_app",
        "description": "Get the name of the currently focused app.",
        "parameters": {"type": "object", "properties": {}},
        "sensitivity": "S0",
    },
    {
        "name": "mac_list_apps",
        "description": "List installed and running apps.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["all", "browsers", "running"], "default": "all"},
            },
        },
        "sensitivity": "S0",
    },
]


def cmd_json_tools(args):
    _json({"ok": True, "tools": _TOOL_SCHEMAS})


def cmd_list_apps(args):
    installed = set()
    for app_dir in ("/Applications", "/Applications/Utilities",
                    os.path.expanduser("~/Applications")):
        if not os.path.isdir(app_dir):
            continue
        for entry in os.listdir(app_dir):
            if entry.endswith(".app"):
                installed.add(entry[:-4])

    running = set()
    try:
        from AppKit import NSWorkspace
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            name = app.localizedName()
            if name:
                running.add(name)
    except Exception:
        pass

    browsers = sorted(installed & _KNOWN_BROWSERS)

    if args.category == "browsers":
        _json({"ok": True, "browsers": browsers})
    elif args.category == "running":
        _json({"ok": True, "running": sorted(running)})
    else:
        _json({
            "ok": True,
            "installed": sorted(installed),
            "running": sorted(running),
            "browsers": browsers,
        })


# ─── Argument Parser ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="macOS Accessibility CLI — control native apps via AXUIElement",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
commands:
  launch          Launch/activate an app, wait until ready, return tree
  focus           Bring app window to front
  quit            Quit an app gracefully
  tree            Read the accessibility UI tree (indexed elements)
  click           Click an element by index or label
  double-click    Double-click an element
  right-click     Right-click an element
  click-at        Click at screen pixel coordinates
  type            Type text into focused input field
  shortcut        Execute a keyboard shortcut
  scroll          Scroll up/down using scroll wheel
  hover           Move mouse to position without clicking
  drag            Click-drag from one point to another
  screenshot      Capture screen or app window
  screen-size     Get main screen resolution
  focused-app     Get name of the focused app
  list-apps       List installed and running apps
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # launch
    p = sub.add_parser("launch", help="Launch/activate an app, wait until ready")
    p.add_argument("app", help="App name (e.g. Notes, Finder, 'Microsoft Teams')")
    p.set_defaults(func=cmd_launch)

    # focus
    p = sub.add_parser("focus", help="Bring app window to front")
    p.add_argument("app", help="App name")
    p.set_defaults(func=cmd_focus)

    # quit
    p = sub.add_parser("quit", help="Quit an app gracefully")
    p.add_argument("app", help="App name")
    p.set_defaults(func=cmd_quit)

    # tree
    p = sub.add_parser("tree", help="Read the accessibility UI tree")
    p.add_argument("--app", default=None, help="App name (default: focused app)")
    p.add_argument("--depth", type=int, default=12,
                   help="Max tree depth (default: 12)")
    p.add_argument("--flat", action="store_true",
                   help="Flatten to indexed readable text (recommended)")
    p.add_argument("--verbose", action="store_true",
                   help="Include position/size and all elements in flat mode")
    p.add_argument("--max-elements", type=int, default=200,
                   help="Max elements in flat mode (default: 200)")
    p.set_defaults(func=cmd_tree)

    # click (by index or label)
    p = sub.add_parser("click", help="Click an element by index or label")
    p.add_argument("--index", type=int, default=None,
                   help="Element index from last tree output")
    p.add_argument("--label", default=None,
                   help="Element title/description to search for")
    p.add_argument("--app", default=None,
                   help="App name (for --label mode, default: focused app)")
    p.add_argument("--no-press", action="store_true",
                   help="Skip the AXPress fast-path; force a coordinate-based "
                        "mouse click. Use only if AXPress is misbehaving for "
                        "the target element. Coordinate clicks fail silently "
                        "when the target window isn't the topmost visible one.")
    p.set_defaults(func=cmd_click)

    # double-click
    p = sub.add_parser("double-click", help="Double-click an element")
    p.add_argument("--index", type=int, default=None,
                   help="Element index from last tree output")
    p.add_argument("--label", default=None,
                   help="Element title/description")
    p.add_argument("--app", default=None,
                   help="App name (for --label mode)")
    p.set_defaults(func=cmd_double_click)

    # right-click
    p = sub.add_parser("right-click", help="Right-click an element")
    p.add_argument("--index", type=int, default=None,
                   help="Element index from last tree output")
    p.add_argument("--label", default=None,
                   help="Element title/description")
    p.add_argument("--app", default=None,
                   help="App name (for --label mode)")
    p.set_defaults(func=cmd_right_click)

    # click-at
    p = sub.add_parser(
        "click-at",
        help="Click at screen pixel coordinates (use --app to refocus first)",
    )
    p.add_argument("x", type=float, help="X coordinate")
    p.add_argument("y", type=float, help="Y coordinate")
    p.add_argument(
        "--app",
        default=None,
        help=(
            "Focus this app immediately before clicking. STRONGLY recommended "
            "after vision_locate, otherwise CGEvent clicks land on whatever "
            "window is topmost at the screen point (often the IDE)."
        ),
    )
    p.set_defaults(func=cmd_click_at)

    # type
    p = sub.add_parser("type", help="Type text into the focused input field")
    p.add_argument("text", help="Text to type")
    p.add_argument("--app", default=None, help="Focus this app first")
    p.add_argument("--index", type=int, default=None,
                   help="Click element by index first to focus it, then type")
    p.set_defaults(func=cmd_type)

    # shortcut
    p = sub.add_parser("shortcut", help="Execute a keyboard shortcut")
    p.add_argument("keys", help="Shortcut string (e.g. cmd+n, cmd+shift+s)")
    p.set_defaults(func=cmd_shortcut)

    # scroll
    p = sub.add_parser("scroll", help="Scroll using scroll wheel events")
    p.add_argument("direction", choices=["up", "down"],
                   help="Scroll direction")
    p.add_argument("amount", type=int, nargs="?", default=3,
                   help="Scroll amount in lines (default: 3)")
    p.set_defaults(func=cmd_scroll)

    # hover
    p = sub.add_parser("hover", help="Move mouse cursor without clicking")
    p.add_argument("x", type=float, help="X coordinate")
    p.add_argument("y", type=float, help="Y coordinate")
    p.set_defaults(func=cmd_hover)

    # drag
    p = sub.add_parser("drag", help="Click-drag from one point to another")
    p.add_argument("start_x", type=float, help="Start X")
    p.add_argument("start_y", type=float, help="Start Y")
    p.add_argument("end_x", type=float, help="End X")
    p.add_argument("end_y", type=float, help="End Y")
    p.add_argument("--duration", type=float, default=0.5,
                   help="Drag duration in seconds (default: 0.5)")
    p.set_defaults(func=cmd_drag)

    # screenshot
    p = sub.add_parser("screenshot", help="Capture screen or app window")
    p.add_argument("--app", default=None,
                   help="Capture only this app's window")
    p.add_argument("--region", default=None,
                   help="Capture region: x,y,width,height")
    p.set_defaults(func=cmd_screenshot)

    # screen-size
    p = sub.add_parser("screen-size", help="Get main screen resolution")
    p.set_defaults(func=cmd_screen_size)

    # focused-app
    p = sub.add_parser("focused-app", help="Get name of the focused app")
    p.set_defaults(func=cmd_focused_app)

    # list-apps
    p = sub.add_parser("list-apps", help="List installed and running apps")
    p.add_argument(
        "--category",
        choices=["all", "browsers", "running"],
        default="all",
        help="Filter: all (default), browsers, or running",
    )
    p.set_defaults(func=cmd_list_apps)

    # json-tools (used by voice/realtime.py to register tools with OpenAI)
    p = sub.add_parser("json-tools",
                       help="Dump tool schemas in OpenAI function-tool format")
    p.set_defaults(func=cmd_json_tools)

    args = parser.parse_args()

    try:
        args.func(args)
    except Exception as e:
        _json({"ok": False, "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
