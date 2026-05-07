---
name: peer
description: Cross-device tasks via the secure peer protocol (Mac<->Android).
exec_pattern: "node /data/user/0/com.ben/files/openclaw/tools/peer_cli.js *"
sensitivity: S1
---

# Peer skill

Two main subcommands:

- `tools.invoke <method> --args '{...}'` - call a SINGLE tool on the peer.
  Example: `peer_cli.js tools.invoke ax.tree`.
- `task.run <intent> --args '{...}'` - delegate a multi-step task to the
  peer's agent. Streams `task.event` events back; final result is a JSON
  object you can speak/log.

If no peer is paired, returns `{ok:false, error:'not_paired'}`. Onboarding
(or Settings -> Re-pair) handles pairing via QR scan.
