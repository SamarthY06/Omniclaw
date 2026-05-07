---
name: history
description: Read past sessions stored as JSONL on this device.
exec_pattern: "node /data/user/0/com.ben/files/openclaw/tools/history_cli.js *"
sensitivity: S1
---

# History skill

Subcommands:
- `list [--limit N] [--since ISO_TS]` - reverse-chrono session summaries.
- `show <session_id>` - full event stream for one session.
- `search <query> [--limit N]` - substring/keyword search across sessions.

Use this when the user asks "what did we talk about yesterday?" / "did we
ever set up the Spotify thing?" / "find the session where we sent that PDF
to Pragati".
