"""OmniClaw + Jarvis: Mac-side runtime for the cross-device peer-to-peer assistant.

Most of what a "Jarvis" needs on Mac is already provided by OpenClaw (agent
loop, exec/read/write/edit/subagent tools, voice with gpt-realtime, wake word,
cron, skills, memory, exec approval gating).

This package contains only the Mac-side glue OpenClaw doesn't:

  proto     - peer protocol envelope, RPC types, HMAC + canonical JSON
  peer      - WS server, WS client, mDNS discovery, QR pairing, daemon process
  wake      - UDP-multicast wake-word arbitration (only-one-device-answers)
  tools     - exec-style CLIs invoked by OpenClaw: macos_ax.py, peer_cli.py
  skills    - OpenClaw skill manifests (peer/, macos-accessibility/)
"""

__version__ = "0.1.0"
