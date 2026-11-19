#!/usr/bin/env bash
# Thin wrapper — delegates all logic to setup-dev.py (single source of truth).
# setup-dev.py handles venv creation, Docker, Elasticsearch, indexing, Flask, and Ollama.
#
# Usage:
#   ./setup-dev.sh                              # interactive
#   ./setup-dev.sh /path/to/courses             # incremental index
#   ./setup-dev.sh --reset /path/to/courses     # wipe + full reindex
#   ./setup-dev.sh --reset                      # interactive with forced reset
#   ./setup-dev.sh --ext pdf,md /path/to/courses
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

exec "$PYTHON" "$SCRIPT_DIR/setup-dev.py" "$@"
