#!/bin/bash
# OmniClaw launcher
# Usage: ./start.sh
# Run from the omniclaw/ directory

set -e
cd "$(dirname "$0")"

export KMP_DUPLICATE_LIB_OK=TRUE
export PYTHONPATH="$(pwd):$PYTHONPATH"

echo ""
echo "Starting OmniClaw..."
python3 main.py
