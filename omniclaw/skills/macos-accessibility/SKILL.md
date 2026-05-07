---
name: macos-accessibility
description: Control native macOS apps via Accessibility APIs (pyobjc AXUIElement)
metadata:
  openclaw:
    os: ["darwin"]
    requires:
      bins: ["python3"]
---

# macOS Accessibility Skill

Control any native macOS application using the Accessibility (AX) API. This skill gives you the power to launch, read, click, type, scroll, and navigate apps like Notes, Finder, Terminal, Calendar, System Settings, Slack desktop, Microsoft Teams, and any other macOS application.

## Hard rule: prefer the desktop app

If the user names a desktop application that is installed on this Mac (Microsoft Teams, Slack, Discord, Notion, Linear, WhatsApp, Spotify, ...), you **MUST** drive that desktop app via this skill. Do NOT shortcut to the `browser` tool just because the AX tree is sparse or a click looks tricky -- that's exactly when you switch to the **vision skill's `text_locate` -> `mac_click_at`** (or `vision_locate` if there's no text label). See [omniclaw/skills/macos-vision/SKILL.md](../macos-vision/SKILL.md) for full guidance. `text_locate` runs Apple's on-device OCR (free, ~150ms, pixel-perfect) and handles 80%+ of click targets in chat apps; `vision_locate` uses OpenAI's GA `computer` tool with `gpt-5.5` for icon-only / non-text targets. Together they are the agent's universal click primitive on macOS and work on any app, regardless of how broken its accessibility tree is.

The `browser` tool is reserved for sites with **no installed desktop app** (Amazon, Gmail in some setups, generic URLs, etc.) or when the user explicitly asks for a browser.

## When to Use This Skill

Use `exec python3` with `macos_ax.py` for any app installed on the Mac:
- Notes, Finder, Terminal, Calendar, System Settings, Mail
- Slack (desktop app), WhatsApp (desktop), Discord (desktop), Microsoft Teams
- Any app that runs natively on macOS (Electron / native / Catalyst)

## When NOT to Use This Skill

Do **NOT** use this for sites that have no installed desktop counterpart. For those, use the `browser` tool:
- Amazon, LinkedIn, YouTube, generic URLs -- use `browser`
- "Open https://..." -- use `browser`

## Three-Layer Routing

| Task | Tool |
|---|---|
| "Search for iPhone on Amazon" | `browser` (no desktop app) |
| "Open Notes and create a new note" | `macos_ax.py` |
| "Check my Gmail" | `browser` (Gmail has no desktop app) |
| "Open System Settings" | `macos_ax.py` |
| "Get latest message on Teams" | `mac_screenshot` -> `text_locate 'BLR - Team'` -> `click-at --app 'Microsoft Teams'` -> `vision_read`. Browser is **NOT** an acceptable fallback. |
| "What did Slack say?" | `mac_screenshot` -> `text_locate '<channel>'` -> `click-at --app Slack` -> `vision_read` |
| "Find price on Amazon, save in Notes" | `browser` to read, then `macos_ax.py` to save |

## Command Reference

All commands are run via `exec python3 /Users/samarthyadannavar/Desktop/Personal/Personal\ Assistant/omniclaw/tools/macos_ax.py <command> [args]`.

Every command outputs JSON: `{"ok": true, ...}` on success, `{"ok": false, "error": "..."}` on failure.

### App Control

```bash
# Launch app, wait until ready, return indexed UI tree
python3 macos_ax.py launch "Notes"

# Bring app to front
python3 macos_ax.py focus "Notes"

# Quit app gracefully
python3 macos_ax.py quit "Notes"

# Get name of the currently focused app
python3 macos_ax.py focused-app
```

### Reading the UI (Indexed Elements)

The `tree --flat` command numbers every actionable element. Use these indices with `click --index N`.

```bash
# Read indexed UI tree of an app (RECOMMENDED)
python3 macos_ax.py tree --flat --app "Notes"

# Read focused app's indexed tree
python3 macos_ax.py tree --flat

# Read with positions visible
python3 macos_ax.py tree --flat --verbose

# Full raw tree (JSON, for debugging)
python3 macos_ax.py tree --app "Notes" --depth 12
```

Example output:
```
APP: Notes (pid: 12345)
Indexed elements below -- use `click --index N` to interact.

[1] [AXButton] "New Note" (clickable)
[2] [AXRow] (no label) (clickable)
[3] [AXCell] (Notes, 10 notes) (clickable)
[4] [AXTextField] "Search" (typeable, clickable)
[5] [AXButton] "Delete" (clickable)
```

### Clicking

There are TWO click paths. Pick based on the target app:

**(A) Native AX click -- use for native macOS apps with rich AX trees** (Mail, Notes, Calendar, System Settings, Finder, Messages):

```bash
python3 macos_ax.py tree --flat --app "Notes"
python3 macos_ax.py click --index 3
python3 macos_ax.py click --label "New Note" --app "Notes"
python3 macos_ax.py double-click --index 3
python3 macos_ax.py right-click --index 3
```

`click --index N` first tries the AX `AXPress` action (works regardless of which window is topmost), then falls back to a CGEvent mouse click. Pass `--no-press` to force coordinate clicks.

**(B) Vision skill (`text_locate` first, `vision_locate` fallback) -- use for Electron / webview apps and any time (A) is unreliable** (Microsoft Teams, Slack desktop, Discord, Linear desktop, Notion desktop, browsers, VS Code, Cursor):

```bash
# 1. snap the app
python3 macos_ax.py focus "Microsoft Teams"
python3 macos_ax.py screen-size                            # -> width, height
python3 macos_ax.py screenshot --app "Microsoft Teams"     # -> path

# 2a. TIER 1: free, on-device OCR + fuzzy match (works for any text label)
python3 omniclaw/tools/macos_vision.py text-locate \
    --image <path> \
    --target "BLR - Team" \
    --screen-width <W> --screen-height <H>
# -> {"ok": true, "found": true, "click_x": 158, "click_y": 461, ...}

# 2b. TIER 2 (only if text_locate found:false): OpenAI GA computer tool, gpt-5.5
python3 omniclaw/tools/macos_vision.py locate \
    --image <path> \
    --target "the smiley-face emoji button at the bottom-right of the compose toolbar" \
    --screen-width <W> --screen-height <H>
# -> {"ok": true, "found": true, "click_x": 196, "click_y": 463, ...}

# 3. click at those coordinates (--app refocuses Teams JUST before the click,
#    so the synthetic mouse event lands on Teams and not on Cursor / your IDE)
python3 macos_ax.py click-at 158 461 --app "Microsoft Teams"
```

Always pass `--app` to `click-at`. Without it, CGEvent clicks land on whatever window happens to be topmost at the screen point, which is almost never what you want.

`text_locate` is FREE and runs entirely on-device (Apple's `VNRecognizeTextRequest`). Always try it first when the click target has any visible text label -- it's strictly more reliable than asking an LLM to emit pixel coords.

**Direct coordinate click (no vision):**

```bash
python3 macos_ax.py click-at 500 300 --app "Microsoft Teams"
```

### Typing

```bash
# Type text into focused input field
python3 macos_ax.py type "Hello world"

# Click element by index to focus it, then type
python3 macos_ax.py type "Meeting notes" --index 5

# Focus an app first, then type
python3 macos_ax.py type "Meeting notes" --app "Notes"
```

### Keyboard Shortcuts

```bash
python3 macos_ax.py shortcut "cmd+n"        # New document/note
python3 macos_ax.py shortcut "cmd+s"        # Save
python3 macos_ax.py shortcut "cmd+c"        # Copy
python3 macos_ax.py shortcut "cmd+v"        # Paste
python3 macos_ax.py shortcut "cmd+shift+s"  # Save As
python3 macos_ax.py shortcut "cmd+w"        # Close window
python3 macos_ax.py shortcut "return"       # Press Enter
python3 macos_ax.py shortcut "escape"       # Press Escape
python3 macos_ax.py shortcut "tab"          # Press Tab
```

### Scrolling

Uses proper scroll wheel events (not keyboard Page Down).

```bash
python3 macos_ax.py scroll down 5
python3 macos_ax.py scroll up 3
```

### Screenshot (Visual Fallback)

When the AX tree doesn't expose text (common in Electron apps like Teams), take a screenshot and pair it with `omniclaw/tools/macos_vision.py` to read it.

```bash
# Full screen
python3 macos_ax.py screenshot

# Specific app window
python3 macos_ax.py screenshot --app "Microsoft Teams"

# Specific region
python3 macos_ax.py screenshot --region "0,0,800,600"
```

After the screenshot, ask vision to extract what you need:

```bash
python3 omniclaw/tools/macos_vision.py read \
    --image "<path returned by screenshot>" \
    --question 'Extract the last 5 messages as a JSON array with {sender, time, text}. Return only JSON.'
```

See `omniclaw/skills/macos-vision/SKILL.md` for the full vision skill (cost, sensitivity, prompt patterns, model options).

### Mouse Control

```bash
# Move mouse without clicking (for tooltips, menus)
python3 macos_ax.py hover 500 300

# Drag from one point to another
python3 macos_ax.py drag 100 200 500 400 --duration 0.5
```

### Info

```bash
python3 macos_ax.py screen-size
python3 macos_ax.py list-apps
python3 macos_ax.py list-apps --category browsers
python3 macos_ax.py list-apps --category running
```

## Workflow Pattern

**Always read the indexed tree before clicking. Never guess element labels or indices.**

1. `launch "AppName"` -- opens app, waits until ready, returns indexed tree
2. Identify the element index from the tree output
3. `click --index N` -- click the element by its number
4. `tree --flat` -- re-read to verify the result
5. Repeat until task is complete

### Example: Create a note in Apple Notes

```
1. exec python3 macos_ax.py launch "Notes"
   -> Returns indexed tree with [1] New Note button
2. exec python3 macos_ax.py click --index 1
   -> Clicks "New Note"
3. exec python3 macos_ax.py type "Shopping list\n- Milk\n- Bread"
   -> Types content
4. exec python3 macos_ax.py tree --flat --app "Notes"
   -> Verify note content appears
```

### Example: "What are the last 5 messages from BLR - Team on Microsoft Teams?"

This is the canonical Electron-app flow. Notice it never touches `browser`, it uses Apple's free on-device OCR for the click (not gpt-anything), and only the final read goes through the LLM.

```
1. exec python3 macos_ax.py launch "Microsoft Teams"
2. exec python3 macos_ax.py screen-size
   -> {"ok": true, "width": 1728, "height": 1117}
3. exec python3 macos_ax.py screenshot --app "Microsoft Teams"
   -> {"ok": true, "path": "/tmp/.../shot_a.png"}
4. exec python3 omniclaw/tools/macos_vision.py text-locate \
       --image /tmp/.../shot_a.png \
       --target "BLR - Team" \
       --screen-width 1728 --screen-height 1117
   -> {"ok": true, "found": true, "matched_text": "BLR - Team",
        "match_score": 1.0, "click_x": 158, "click_y": 461, ...}
5. exec python3 macos_ax.py click-at 158 461 --app "Microsoft Teams"
6. (wait ~1.5s for the conversation to render)
7. exec python3 macos_ax.py screenshot --app "Microsoft Teams"
   -> {"ok": true, "path": "/tmp/.../shot_b.png"}
8. (verify the right chat opened: text-locate the conversation header text on shot_b)
   exec python3 omniclaw/tools/macos_vision.py text-locate \
       --image /tmp/.../shot_b.png \
       --target "BLR - Team"
   -> if found at top-of-right-pane area, proceed; otherwise refine + retry.
9. exec python3 omniclaw/tools/macos_vision.py read \
       --image /tmp/.../shot_b.png \
       --question 'List the last 5 messages as a JSON array of {sender,time,text}. Return only JSON.'
   -> {"ok": true, "result": "[{\"sender\":\"...\", ...}]", ...}
10. Parse the JSON and reply to the user.
```

The agent must NEVER short-circuit to a browser for this query. The user has Teams desktop installed; the desktop app is the source of truth. If `text-locate` returns `found:false` (rare for chat rows), only THEN fall back to `vision_locate` -- not the browser.

## Sensitivity Levels

| Level | Actions | Behavior |
|---|---|---|
| 0 -- Read-only | Launch, focus, read tree, scroll, screenshot | Execute immediately |
| 1 -- Low risk | Add to cart, bookmark, create note, star email | Execute, verify after |
| 2 -- Confirm first | Send message, place order, delete file, submit form | Ask user "Should I proceed?" and wait for "yes" |
| 3 -- User must act | Enter password, complete payment, approve 2FA | Tell user to do it manually, wait for "done" |

## Cross-App Data Transfer

When the task involves both web and native:

1. Use `browser` to get data from the web (e.g., find a price on Amazon)
2. Store the data in your context (don't write to clipboard)
3. Use `exec python3 macos_ax.py launch` to open the native app
4. Use `exec python3 macos_ax.py type` to enter the data

## Error Recovery

If an action fails, follow this cascade. **Browser is not on the cascade.**

1. **Re-read**: `tree --flat` to refresh your view of the UI.
2. **Retry by index/label**: indices may have shifted; pick the right element.
3. **`text_locate` + `click-at`**: `mac_screenshot --app <name>` -> `macos_vision.py text-locate --image <path> --target "<exact label>" --screen-width W --screen-height H` -> `mac_click_at click_x click_y --app <name>`. Free, on-device, exact pixels. Try this for ANY click target with a visible text label.
4. **`vision_locate` + `click-at`**: only if step 3 returns `found: false`. `macos_vision.py locate --image <path> --target "<natural-language description>" --screen-width W --screen-height H` -> `mac_click_at click_x click_y --app <name>`. Uses OpenAI's GA `computer` tool with `gpt-5.5`.
5. **`vision_read`** (for reading): `macos_vision.py read --image <path> --question "..."` to recover the text the AX tree didn't expose.
6. **Ask user**: after 3 failed attempts, describe what you see and ask for help.

`browser` is only acceptable when (a) the user explicitly asks for the browser, or (b) there is no installed desktop counterpart to the requested app.

## Login Detection

If the `tree --flat` output shows elements like "Sign In", "Log In", "Enter password", "Continue with Google":

**STOP.** Message the user: "[App] requires sign-in. Please log in, then tell me 'done'."

Wait for the user to confirm before continuing.

## App Name Aliases

The following short names are recognized:

| Alias | App Name |
|---|---|
| chrome | Google Chrome |
| firefox | Firefox |
| safari | Safari |
| notes | Notes |
| settings | System Settings |
| vscode | Visual Studio Code |
| terminal | Terminal |
| finder | Finder |
| messages | Messages |
| mail | Mail |
| calendar | Calendar |
| music | Music |
| teams | Microsoft Teams |
| slack | Slack |
| discord | Discord |
| netflix | Netflix |
| spotify | Spotify |
