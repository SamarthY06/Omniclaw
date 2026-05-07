---
name: macos-vision
description: Read text from a screenshot AND locate UI elements (by text label OR by natural-language description) so the agent can click inside any macOS app, even Electron / webview apps where the AX tree is blind. Three subcommands - text_locate (free, on-device, exact pixels), vision_locate (OpenAI GA computer tool with gpt-5.5), vision_read (gpt-5.5 multimodal). text_locate -> click_at is the agent's PRIMARY click path on non-native apps; vision_locate is the fallback for icon-only / non-text targets. Browser is NOT a fallback.
metadata:
  openclaw:
    os: ["darwin"]
    requires:
      bins: ["python3"]
      env: ["OPENAI_API_KEY"]
      packages: ["pyobjc-framework-Vision"]
  exec_pattern: "python3 omniclaw/tools/macos_vision.py *"
---

# macOS Vision Skill (read + text-locate + vision-locate)

The Mac Accessibility (AX) API is rich on native apps (Mail, Notes, Calendar,
Messages, Finder) but blind on webview / Electron apps (Microsoft Teams, Slack
desktop, Discord, Linear desktop, Notion desktop). Their conversation panes
render text in a `<canvas>` or React tree that AX cannot see, so the agent
only gets `[AXRow] (no label)` rows AND `mac_click --index N` often misses
because the row is not a real `AXButton`.

This skill closes that gap with **three** subcommands:

1. **`text_locate`** -- find a TEXT element using Apple's on-device Vision OCR
   (`VNRecognizeTextRequest`). Free, ~150ms, pixel-perfect, NO image leaves
   the device. Always try this FIRST when the click target has any visible
   text label (chat names, channel names, button labels, message senders,
   menu items). Sensitivity S0.
2. **`vision_locate`** -- find a UI element by natural-language description
   using OpenAI's GA `computer` tool with `gpt-5.5`. Purpose-trained for
   click coordinates (ScreenSpot-Pro 85.4%, OSWorld-Verified 75.0% -- above
   the human baseline). Use this when `text_locate` fails: icon-only buttons,
   non-text targets, complex visual reasoning. Sensitivity S2.
3. **`vision_read`** -- send a PNG to `gpt-5.5` and get extracted text /
   structured JSON back. Use after a successful click when you need to read
   content the AX tree doesn't expose. Sensitivity S2.

Together they let the agent operate ANY desktop app the user has installed,
without ever falling back to a browser.

## Tier order (always)

```
mac_screenshot
  -> text_locate     (free, on-device, exact pixels for text targets)
        if found: -> mac_click_at --app "<App>"
        else:     -> vision_locate  (OpenAI GA computer tool, gpt-5.5)
                          -> mac_click_at --app "<App>"
  -> verify (new screenshot + small text_locate or vision_read)
```

If verification fails, refine the `--target` description and retry once.
Browser is NEVER on this cascade -- if the user named a desktop app that is
installed, you must drive the desktop app.

## Why text_locate first

For chat apps (Teams, Slack, Discord, ...), 80%+ of click targets are
labelled text rows or named buttons. Apple's `VNRecognizeTextRequest` returns
exact pixel-perfect bounding boxes for every visible text run, on-device,
in milliseconds, with no API cost and no privacy leak. Asking an LLM to emit
pixel coordinates is strictly worse than this for any text target.

`vision_locate` is reserved for the long tail: icon-only buttons (e.g. the
emoji picker, attach-file paperclip), avatar-only contact rows, custom
canvas-rendered widgets.

## How to invoke

Always call the CLI; the agent never makes the OpenAI request directly.

```bash
# discovery (call once at session start; cache the result):
python3 omniclaw/tools/macos_vision.py --json-tools

# text_locate -- on-device OCR + fuzzy match (TIER 1)
python3 omniclaw/tools/macos_vision.py text-locate \
    --image /var/folders/.../omniclaw_screenshot_xxx.png \
    --target 'BLR - Team' \
    --screen-width 1728 --screen-height 1117

# vision_locate -- OpenAI GA computer tool, gpt-5.5 (TIER 2 fallback)
python3 omniclaw/tools/macos_vision.py locate \
    --image /var/folders/.../omniclaw_screenshot_xxx.png \
    --target 'the smiley-face emoji button at the bottom-right of the compose toolbar' \
    --screen-width 1728 --screen-height 1117

# vision_read -- extract text from an image (gpt-5.5)
python3 omniclaw/tools/macos_vision.py read \
    --image /var/folders/.../omniclaw_screenshot_xxx.png \
    --question 'Extract the last 5 messages from this Microsoft Teams conversation as a JSON array with fields {sender, time, text}. Return only the JSON, no prose.'
```

Every command prints exactly one line of JSON to stdout. Exit code 0 = ok,
non-0 = failure with `{"ok": false, "error": "..."}`.

### `text_locate` success shape

```json
{
  "ok": true,
  "found": true,
  "matched_text": "BLR - Team",
  "match_score": 1.0,
  "ocr_confidence": 1.0,
  "image_x": 312,
  "image_y": 828,
  "image_width": 3418,
  "image_height": 2008,
  "bbox": [243, 815, 139, 25],
  "screen_width": 1728,
  "screen_height": 1117,
  "click_x": 158,
  "click_y": 461,
  "candidates": [
    {"text": "BLR - Team", "similarity": 1.0, "ocr_confidence": 1.0, "score": 1.0, "bbox": [243, 815, 139, 25]},
    ...
  ]
}
```

If `found` is `false`, the top-N candidates are still listed for debugging
and the caller should fall back to `vision_locate`.

### `vision_locate` success shape

```json
{
  "ok": true,
  "found": true,
  "image_x": 387,
  "image_y": 833,
  "image_width": 3418,
  "image_height": 2008,
  "screen_width": 1728,
  "screen_height": 1117,
  "click_x": 196,
  "click_y": 463,
  "raw_action": {"type": "click", "button": "left", "keys": null, "x": 387, "y": 833},
  "model": "gpt-5.5-2026-04-23"
}
```

`vision_locate` runs a 2-turn dance with the GA `computer` tool: the model
asks for a screenshot, we feed it back, the model returns one `click` action
in the screenshot's pixel space. We rescale to screen points using the ratio
`screen_width / image_width` (the screenshot is typically Retina 2x).

Pass `click_x` and `click_y` straight to `mac_click_at`, and ALWAYS pass
`--app "<App>"` so the target window is refocused right before the click.

## Canonical chain: "what are the last 5 messages from BLR - Team on Teams?"

```bash
# 1. focus + first screenshot of Teams' current state
python3 omniclaw/tools/macos_ax.py focus "Microsoft Teams"
python3 omniclaw/tools/macos_ax.py screen-size
# -> {"ok": true, "width": 1728, "height": 1117}
python3 omniclaw/tools/macos_ax.py screenshot --app "Microsoft Teams"
# -> {"ok": true, "path": "/var/folders/.../omniclaw_screenshot_aaa.png"}

# 2. TIER 1: free, on-device OCR
python3 omniclaw/tools/macos_vision.py text-locate \
    --image "/var/folders/.../omniclaw_screenshot_aaa.png" \
    --target 'BLR - Team' \
    --screen-width 1728 --screen-height 1117
# -> {"ok": true, "found": true, "click_x": 158, "click_y": 461, ...}

# 3. click at those coordinates (--app refocuses Teams right before the click)
python3 omniclaw/tools/macos_ax.py click-at 158 461 --app "Microsoft Teams"

# 4. wait, screenshot, read the messages
sleep 1.5
python3 omniclaw/tools/macos_ax.py screenshot --app "Microsoft Teams"
# -> {"ok": true, "path": "/var/folders/.../omniclaw_screenshot_bbb.png"}
python3 omniclaw/tools/macos_vision.py read \
    --image "/var/folders/.../omniclaw_screenshot_bbb.png" \
    --question 'List the last 5 messages in this Teams conversation as a JSON array with {sender, time, text}. Return only the JSON.'
```

## Verify-after-click loop (mandatory for non-native apps)

`text_locate` and `vision_locate` are NOT infallible. The robust pattern is:

1. `mac_screenshot` -> `text_locate` (or `vision_locate` if no text) -> `mac_click_at`.
2. Wait ~1s, take ANOTHER `mac_screenshot`.
3. Independently verify with **`text_locate`** that the screen now reflects
   what you intended (e.g. for a chat row, look for an expected message
   header on the right pane). DO NOT use `vision_read` to ask "did this
   work?" -- that biases the model toward saying yes.
4. If verification fails, refine the `--target` description (more context,
   nearby landmarks) and retry once.

This is the same pattern Anthropic Computer Use and OpenAI Operator run
internally; on stable UIs two iterations are usually enough.

## Prompting the vision model well

For `vision_read`, be explicit about output format:

- "List the last 5 messages as a JSON array with {sender, time, text}. Return only the JSON."
- "Read the page title and the first paragraph. Return as JSON: {title, body}."
- "Find the value next to 'Total Due' on this invoice. Return only the number."

Avoid:
- "What is on the screen?" -- too open, wastes tokens.
- "Read everything" -- the model will hallucinate plausible-but-wrong text on dense screens.

For `vision_locate` (`--target`), be specific about location and surrounding
context: "the smiley-face emoji button at the bottom-right of the compose
toolbar" beats "emoji". Mention the app name and pane.

## Sensitivity

| Tool | Sensitivity | Why |
|---|---|---|
| `text_locate` | **S0** | On-device, no network, no image leaves the Mac. |
| `vision_locate` | **S2** | Image is sent to OpenAI's GA `computer` tool. |
| `vision_read` | **S2** | Image is sent to OpenAI's chat-completions endpoint. |

The OpenClaw `tools.exec.approval` policy auto-approves all three inside
Talk mode (the user is actively asking) and logs every call.

## Cost & latency

- `text_locate`: 0$, ~100-200ms (Apple Vision OCR), local.
- `vision_locate`: ~$0.02-0.05 per call (gpt-5.5 with `computer` tool, 2-turn handshake), 5-12s end-to-end.
- `vision_read`: ~$0.005-0.02 per call (gpt-5.5 multimodal), 1-3s end-to-end.

Always try `text_locate` first to keep cost and latency near zero.

## API key resolution

The OpenAI key (used by `vision_locate` and `vision_read`; `text_locate` does
not need it) is resolved in this order:

1. `OPENAI_API_KEY` env var (set by launchd plist or the parent shell).
2. `~/.openclaw/openclaw.json` -> `talk.realtime.openai.apiKey`.
3. `<repo>/omniclaw/.env` (a single `OPENAI_API_KEY=...` line).

If none are set, `vision_*` tools return `{"ok": false, "error": "no OPENAI_API_KEY ..."}`.

## Failure modes

- `image not found: ...` -> the path doesn't exist. Re-run `mac_screenshot`,
  it returned a path; pass that exact path through.
- `macos_ocr unavailable: ...` -> `pyobjc-framework-Vision` is not installed.
  Run `pip install pyobjc-framework-Vision`.
- `Vision request failed: ...` -> Apple's OCR rejected the image (corrupt,
  zero-byte, unsupported codec). Re-run `mac_screenshot`.
- `no OPENAI_API_KEY set` -> add the key per the resolution rules above.
- `openai http 4xx` -> the model rejected the request (image too large, key
  invalid, rate limited). The error body is included in the message.
- `network error: ...` -> no internet, or OpenAI is down. `text_locate` keeps
  working in this case, so prefer it.
- `computer tool returned no click action` -> rare; the model gave up. Refine
  `--target` with more context (nearby landmarks) and retry.

## Routing summary

### Reading text

| Source of text | Tool to use |
|---|---|
| Native macOS app (Mail, Notes, Calendar, Messages) | `macos_ax.py tree` |
| Web app (Gmail, Linear web, Notion web) in a browser | OpenClaw `browser` |
| Webview / Electron app body (Teams, Slack desktop, Discord) | `macos_ax.py screenshot` -> `macos_vision.py read` |
| Visual reasoning ("what color", "is button greyed out") | `macos_ax.py screenshot` -> `macos_vision.py read` |

### Clicking elements

| Target | Click path |
|---|---|
| Native macOS app element with a clear AX role+title | `macos_ax.py click --index N` (or `--label`) |
| Webview / Electron / canvas element with a visible text label (chat row, channel, button) | `mac_screenshot` -> `text_locate` -> `mac_click_at --app '<App>'` |
| Icon-only / unlabelled element (emoji picker, paperclip, avatar-only row) | `mac_screenshot` -> `vision_locate` -> `mac_click_at --app '<App>'` |
| Anything you would otherwise solve by switching to the browser | Try `text_locate`, then `vision_locate`. The user explicitly prefers desktop apps when both exist. |
