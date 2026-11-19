#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Catalog — DMS Lite  |  Start Script (Linux / macOS)
# ─────────────────────────────────────────────────────────────────────────────
#  Opens the startup progress screen in the browser, checks all services,
#  installs Python dependencies if needed, and launches the Flask backend.
#
#  Usage:
#    ./start.sh                            # interactive, opens browser
#    ./start.sh --no-browser               # headless / server mode
#    ./start.sh --backend-port 8080        # custom backend port
#    ./start.sh --no-install               # skip pip install
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Find Python 3.9+ ─────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
  if command -v "$cmd" &>/dev/null; then
    VER=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    MAJOR="${VER%%.*}"
    MINOR="${VER##*.}"
    if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 9 ]; then
      PYTHON="$cmd"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo ""
  echo "  ✗  Python 3.9 or higher is required but was not found."
  echo "     Install from: https://www.python.org/downloads/"
  echo ""
  exit 1
fi

echo ""
echo "  Using Python: $($PYTHON --version)"
echo "  Launcher    : $SCRIPT_DIR/tools/launcher.py"
echo ""

exec "$PYTHON" "$SCRIPT_DIR/tools/launcher.py" "$@"
