# TOOLS.md — Tool guidance

You have four tool families. Pick the right one for the job.

## 1. Built-in OpenClaw tools

The default OpenClaw tools are always available. Use them first when they
fit:

- `read`, `write`, `edit`, `apply_patch` — files on this machine.
- `exec` — run any command. Subject to `tools.exec.approval` rules.
- `web_fetch`, `web_search` — anything HTTP / search engines.
- `browser` — full automated browser. For sites that don't have an API.
- `cron` — schedule things; the agent registers + manages cron jobs itself.
- `subagents` (spawn family) — run a sub-task in parallel; great for
  long-running research or multi-step plans.
- `memory_search`, `memory_get`, `memory_*` — durable memory across sessions.
- `nodes`, `sessions_*`, `tts` — built-ins for the voice / session layer.

## 2. Mac UI control: `omniclaw/tools/macos_ax.py`

Discover capabilities once at session start:

```bash
python3 omniclaw/tools/macos_ax.py json-tools
```

Then call any tool by its subcommand:

```bash
python3 omniclaw/tools/macos_ax.py launch "Cursor"
python3 omniclaw/tools/macos_ax.py tree --app "Cursor" --flat
python3 omniclaw/tools/macos_ax.py click --label "New Conversation"
python3 omniclaw/tools/macos_ax.py type "hi from Jarvis"
python3 omniclaw/tools/macos_ax.py shortcut cmd+s
python3 omniclaw/tools/macos_ax.py screenshot --app Cursor
python3 omniclaw/tools/macos_ax.py screen-size
python3 omniclaw/tools/macos_ax.py focused-app
python3 omniclaw/tools/macos_ax.py list-apps --category running
```

This drives any macOS app via the AccessibilityService API. Workflow:
launch → tree → identify the element → click/type. Use `--flat` mode for
tree dumps; element indices from a flat dump can be reused for clicks.

Output is always JSON. `{"ok": false, "error": "..."}` on failure.

## 3. Vision read + locate: `omniclaw/tools/macos_vision.py`

Three subcommands, in tier order:

- **`text_locate`** (TIER 1) -- after `mac_screenshot`, find a TEXT element
  using Apple's on-device Vision OCR (`VNRecognizeTextRequest`). Free,
  ~150ms, pixel-perfect, no image leaves the device. Always try this first
  when the click target has a visible text label.
- **`vision_locate`** (TIER 2 fallback) -- after `mac_screenshot`, find a UI
  element by natural-language description using OpenAI's GA `computer` tool
  (`gpt-5.5`, purpose-trained for click coordinates: ScreenSpot-Pro 85.4%).
  Use this only when `text_locate` fails (icon-only buttons, non-text targets).
- **`vision_read`** -- after a successful click, extract text/structure from
  the new screenshot via `gpt-5.5` multimodal.

```bash
python3 omniclaw/tools/macos_vision.py --json-tools

# TIER 1: free, on-device OCR + fuzzy match (try this FIRST)
python3 omniclaw/tools/macos_vision.py text-locate \
    --image /var/folders/.../omniclaw_screenshot_xxx.png \
    --target "BLR - Team" \
    --screen-width 1728 --screen-height 1117
# -> {"ok": true, "found": true, "matched_text": "BLR - Team",
#     "match_score": 1.0, "click_x": 158, "click_y": 461, ...}

# TIER 2 (only if text_locate found:false): OpenAI GA computer tool, gpt-5.5
python3 omniclaw/tools/macos_vision.py locate \
    --image /var/folders/.../omniclaw_screenshot_xxx.png \
    --target "the smiley-face emoji button at the bottom-right of the compose toolbar" \
    --screen-width 1728 --screen-height 1117
# -> {"ok": true, "found": true, "click_x": 196, "click_y": 463, ...}

# read text out of a screenshot (after clicking)
python3 omniclaw/tools/macos_vision.py read \
    --image /var/folders/.../omniclaw_screenshot_xxx.png \
    --question 'Extract the last 5 messages as JSON {sender,time,text}. Return only JSON.'

# then click those coords (--app refocuses Teams immediately before clicking;
# without it, the synthetic mouse event hits whatever window is topmost)
python3 omniclaw/tools/macos_ax.py click-at 158 461 --app "Microsoft Teams"
```

Decision rules:

**Reading text**

| Source | Right tool |
|---|---|
| Native macOS app body (Mail, Notes, Calendar, Messages) | `macos_ax.py tree --flat` |
| Web app (Gmail, Linear web, Notion web) in a browser | `browser` (built-in) |
| Webview / Electron app body (Teams, Slack desktop, Discord) | `macos_ax.py screenshot` -> `macos_vision.py read` |
| Visual reasoning ("is the warning red?") | `macos_ax.py screenshot` -> `macos_vision.py read` |

**Clicking elements**

| Target | Right click |
|---|---|
| Native macOS app element with a clear AX role+title | `macos_ax.py click --index N` (or `--label`) |
| Webview / Electron / canvas element with a visible text label (chat row, channel, named button) | `mac_screenshot` -> `text_locate` -> `mac_click_at --app '<App>'` |
| Icon-only / unlabelled element (emoji picker, paperclip, avatar-only row) | `mac_screenshot` -> `vision_locate` -> `mac_click_at --app '<App>'` |
| Anywhere `mac_click` silently fails or the row is `(no label)` | `mac_screenshot` -> `text_locate` -> if not found, `vision_locate` -> `mac_click_at --app '<App>'` |

If the user named a desktop app that is installed (Teams, Slack, Discord, ...),
NEVER fall back to the `browser` tool just because clicking looks tricky. Use
the vision skill's `text_locate` -> `vision_locate` cascade instead. `browser`
is only correct when the target has no installed desktop counterpart (Amazon,
plain URLs, etc.) or the user explicitly asked for the browser.

Sensitivity:
  - `text_locate`: S0 (on-device, no network).
  - `vision_locate` and `vision_read`: S2 (image is sent to OpenAI). The
    `tools.exec.approval` policy auto-approves them inside Talk mode and logs
    every call.

## 4. Cross-device: `omniclaw/tools/peer_cli.py`

See `skills/peer/SKILL.md` for the full guide. Quick reference:

```bash
python3 omniclaw/tools/peer_cli.py --json-tools
python3 omniclaw/tools/peer_cli.py status
python3 omniclaw/tools/peer_cli.py verify
python3 omniclaw/tools/peer_cli.py caps
python3 omniclaw/tools/peer_cli.py ping
python3 omniclaw/tools/peer_cli.py tools.invoke <tool> --args '{...}'
python3 omniclaw/tools/peer_cli.py task.run <intent> --args '{...}'
python3 omniclaw/tools/peer_cli.py pair show --qr
python3 omniclaw/tools/peer_cli.py pair accept '<jarvis://pair?...>'
```

The agent never holds the peer's shared secret — the daemon does.

## Sensitivity at a glance

The exec approval policy is configured in `~/.openclaw/openclaw.json`. You
do not need to remember the exact rules; the policy will gate dangerous
calls automatically. But for your own planning:

- S0: read-only screen state, info queries, web reads. Auto.
- S1: app launches, tree reads, hovering, scrolling, screenshots. Auto.
- S2: typing, clicking buttons that send/save, payments, alarms,
  contacts changes, **vision-read (image leaves device)**. Confirm.
- S3: OTPs, payment finalize, password entry, biometric. Hand off to
  the user via `peer.handoff_screen` or local TTS.

## Discovery cadence

- At every voice session start (Talk mode opens):
  1. `peer_cli.py --json-tools`
  2. `macos_ax.py json-tools`
  3. `macos_vision.py --json-tools`  (returns vision_read, text_locate, vision_locate)
  4. Cache all three. They rarely change within a session.
- After pairing or app updates: re-run discovery.
