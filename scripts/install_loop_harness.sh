#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${SAGE_LOOP_PYTHON_BIN:-$(command -v python)}"

exec "$PYTHON_BIN" "$ROOT/scripts/loopctl.py" install "$@"
