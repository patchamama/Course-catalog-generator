#!/usr/bin/env bash
# Thin wrapper — delegates all logic to start.py (single source of truth).
# start.py handles venv creation, catalog generation, Ollama check, and Flask server.
#
# Usage:
#   ./start.sh                        # interactive (shows env.py default)
#   ./start.sh "/path/to/courses"     # use given path directly
#   ./start.sh --help
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON=""
for cmd in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
  if command -v "$cmd" &>/dev/null; then
    VER=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    MAJOR=${VER%%.*}; MINOR=${VER##*.}
    if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 9 ]; then
      PYTHON="$cmd"; break
    fi
  fi
done
[ -z "$PYTHON" ] && { echo "Python 3.9+ required. Install from https://www.python.org/" >&2; exit 1; }

exec "$PYTHON" "$SCRIPT_DIR/start.py" "$@"
