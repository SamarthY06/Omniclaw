#!/usr/bin/env bash
# Invoked by wakeword_mac.py on a wake-phrase match. Opens the existing
# OpenClaw realtime entry on the Mac (the same path the Talk-mode CLI uses).
#
# We deliberately exec a thin script (instead of importing OpenClaw inside
# wakeword_mac.py) so:
#   * the wake listener stays a tight, single-purpose process,
#   * the user can swap this for any other realtime entry without touching
#     wakeword_mac.py (e.g. point it at a Whisper.cpp + LiveKit local stack),
#   * launchd doesn't need to know how to find OpenClaw.
#
# The script blocks until the realtime session ends, which is the contract
# wakeword_mac.py expects (it re-arms when this returns).
set -eu

LOG="${HOME}/.jarvis/voice.log"
mkdir -p "$(dirname "$LOG")"

ts() { date +"%Y-%m-%dT%H:%M:%S%z"; }
echo "$(ts) start_voice.sh fired" >> "$LOG"

# Resolution order:
#   1. $BEN_REALTIME_CMD (user-provided absolute command; wins).
#   2. `openclaw talk` if openclaw is on PATH (default for users following INSTALL.md).
#   3. Fallback: print to log so diagnostics are obvious.
if [ -n "${BEN_REALTIME_CMD:-}" ]; then
  echo "$(ts) execing \$BEN_REALTIME_CMD" >> "$LOG"
  exec env "$BEN_REALTIME_CMD" 2>>"$LOG"
fi

if command -v openclaw >/dev/null 2>&1; then
  echo "$(ts) running: openclaw talk --once" >> "$LOG"
  openclaw talk --once 2>>"$LOG"
  echo "$(ts) openclaw talk returned $?" >> "$LOG"
  exit 0
fi

echo "$(ts) ERROR: no realtime entrypoint available. Set BEN_REALTIME_CMD or install openclaw." >> "$LOG"
exit 1
