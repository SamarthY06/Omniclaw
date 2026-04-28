#!/bin/bash
# Run OmniClaw test suite
# Usage: ./run_tests.sh [--test TC-001] [--verbose]

set -e
cd "$(dirname "$0")"

export KMP_DUPLICATE_LIB_OK=TRUE
export PYTHONPATH="$(pwd):$PYTHONPATH"

python3 tests/run_tests.py "$@"
