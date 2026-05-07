---
name: peer
description: Cross-device tools that delegate work to the paired Android phone via the secure peer-to-peer daemon. Use when the requested action needs phone capabilities (SMS, calls, camera, native maps, food delivery apps, alarms, hardware sensors) or when the user explicitly says "on my phone".
metadata:
  openclaw:
    os: ["darwin"]
  routes_to: peer
  exec_pattern: "python3 omniclaw/tools/peer_cli.py *"
---

# Peer (Mac -> Android) Skill

You can drive the paired Android phone by shelling out to a small CLI that
talks to the local peer daemon over a Unix socket. The agent does NOT hold
the peer's shared secret directly; the daemon does. All you do is call the
CLI via your `exec` tool, just like you call `omniclaw/tools/macos_ax.py` for
local UI automation.

## Decision rule

1. If the task can be done on the Mac, do it on the Mac (use `macos_ax.py` or
   built-in OpenClaw tools).
2. If the task is a phone task (it involves a phone-only app, the phone
   network, the phone hardware, or the user said "on my phone"), use this
   skill.
3. Don't reach out for ambiguous tasks — ask the user which device.

## How to invoke

Always call the CLI; never speak the WebSocket protocol directly.

```bash
# discovery (call once at session start; cache the result):
python3 omniclaw/tools/peer_cli.py --json-tools

# health:
python3 omniclaw/tools/peer_cli.py status
python3 omniclaw/tools/peer_cli.py verify

# what tools does the phone advertise right now:
python3 omniclaw/tools/peer_cli.py caps

# direct tool call (you know exactly which phone tool to fire):
python3 omniclaw/tools/peer_cli.py tools.invoke take_photo \
    --args '{"camera":"back"}'

# delegate an open-ended intent (the phone's own agent plans + executes):
python3 omniclaw/tools/peer_cli.py task.run send_whatsapp \
    --args '{"contact":"Dad","file_url":"file:///Users/me/Desktop/x.pdf"}'

# pairing (one-time setup):
python3 omniclaw/tools/peer_cli.py pair show --qr
python3 omniclaw/tools/peer_cli.py pair accept "<jarvis://pair?...>"
```

Every command prints exactly one line of JSON to stdout. Exit code 0 = ok,
non-0 = failure with `{"ok": false, "error": "..."}`.

## When to use which subcommand

| Request | Subcommand | Why |
|---|---|---|
| "Send an SMS to dad" | `tools.invoke send_sms ...` | Direct: known tool, narrow contract |
| "Take a photo with my phone front camera" | `tools.invoke take_photo --args '{"camera":"front"}'` | Direct: hardware tool |
| "Order biryani from Swiggy" | `task.run order_food --args '{"item":"biryani"}'` | Open-ended: the phone's agent navigates the app |
| "Set an alarm on my phone for 5am" | `tools.invoke set_alarm ...` | Direct |
| "What's my step count today" | `tools.invoke health_query --args '{"metric":"steps"}'` | Direct |
| "Make a call to mom" | `tools.invoke dial --args '{"contact":"Mom"}'` | Direct |
| "Read me my unread WhatsApps" | `task.run read_unread --args '{"app":"whatsapp"}'` | Open-ended: needs app navigation |

## Sensitivity gating

The OpenClaw `tools.exec.approval` policy is configured to:

- Auto-allow: `peer_cli.py status|verify|caps|ping`, `tools.invoke read_*`,
  `task.run morning_*|evening_*` (read-only standing orders).
- Require user confirmation: `tools.invoke send_*|pay_*|transfer_*|delete_*`,
  any `task.run` involving payments / messages / app installs.

If a confirmation is requested, do not bypass it. Tell the user what you're
about to do and why; let them approve.

## Failure modes

- `daemon socket not found at ~/.jarvis/peer.sock` -> the launchd job isn't
  loaded. Tell the user: `launchctl load ~/Library/LaunchAgents/ai.jarvis.peer.plist`.
- `no_peer_paired` -> phone isn't paired yet. Run `pair show --qr`, ask the
  user to scan it from the phone.
- `connection closed` / timeout -> phone is unreachable (off-LAN and not on
  Tailscale, or phone app killed by battery saver). Suggest the user open
  the Jarvis app on the phone or check Tailscale.

## Examples

**Direct tool call**

```bash
python3 omniclaw/tools/peer_cli.py tools.invoke take_photo --args '{"camera":"back","save_to_album":"jarvis"}'
# -> {"ok":true,"result":{"ok":true,"output":{"file":"/storage/.../IMG_0001.jpg"}}}
```

**Delegated intent**

```bash
python3 omniclaw/tools/peer_cli.py task.run "send the design doc to dad on whatsapp" \
    --args '{"file_url":"file:///Users/me/Desktop/design.pdf","contact":"Dad"}'
# -> {"ok":true,"events":[...],"result":{"status":"completed","output":{"sent":true}}}
```
