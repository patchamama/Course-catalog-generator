#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Catalog — DMS Lite  |  Start Script (macOS / Ubuntu / other Linux)
# ─────────────────────────────────────────────────────────────────────────────
#  One command that gets everything running:
#    1. makes sure Python 3.9+ (and, on Ubuntu, python3-venv) is installed
#    2. the launcher then installs the Python packages (.venv), the system requirements
#       — Docker, Ollama, Node.js, pandoc, ffmpeg — starts Elasticsearch (Docker) and Ollama,
#       launches the backend and opens the app in your browser.
#
#  Missing software is installed with Homebrew (macOS) or apt / dnf / pacman (Linux); `sudo` is only used when
#  needed (you may be asked for your password). Homebrew itself must already exist on macOS: https://brew.sh
#
#  Usage:
#    ./start.sh                            # install what is missing, start everything, open the browser
#    ./start.sh --no-browser               # headless / server mode
#    ./start.sh --no-system-install        # do NOT install Docker/Ollama/Node/…; start what is installed
#    ./start.sh --with-extras              # also VLC (video buttons) and Tesseract (OCR)
#    ./start.sh --no-install               # skip `pip install` of the Python packages
#    ./start.sh --backend-port 8080        # custom backend port
#    python3 tools/prereqs.py --plan       # show what is missing and the commands that would run (installs nothing)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SYSTEM_INSTALL=1
for arg in "$@"; do
  [ "$arg" = "--no-system-install" ] && SYSTEM_INSTALL=0
done

OS="$(uname -s)"

# Homebrew is not on PATH in a fresh shell on Apple Silicon / Intel macs
if [ "$OS" = "Darwin" ] && ! command -v brew &>/dev/null; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
  done
fi

# ── Find Python 3.9+ ─────────────────────────────────────────────────────────
find_python() {
  local cmd ver major minor
  for cmd in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
    if command -v "$cmd" &>/dev/null; then
      ver=$("$cmd" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || continue
      major="${ver%%.*}"
      minor="${ver##*.}"
      if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
        PYTHON="$cmd"
        return 0
      fi
    fi
  done
  return 1
}

# sudo only when we are not root and the command needs it
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo &>/dev/null; then SUDO="sudo"; fi

install_python() {
  echo "  Python 3.9+ was not found — installing it …"
  case "$OS" in
    Darwin)
      if command -v brew &>/dev/null; then
        brew install python@3.12
      else
        echo "  ✗ Homebrew is required to install Python automatically: https://brew.sh"
        echo "    (or install Python from https://www.python.org/downloads/macos/)"
        return 1
      fi ;;
    Linux)
      if   command -v apt-get &>/dev/null; then $SUDO apt-get update && $SUDO apt-get install -y python3 python3-venv python3-pip
      elif command -v dnf     &>/dev/null; then $SUDO dnf install -y python3 python3-pip
      elif command -v pacman  &>/dev/null; then $SUDO pacman -S --noconfirm python python-pip
      else echo "  ✗ No supported package manager (apt / dnf / pacman) — install Python 3.9+ manually."; return 1
      fi ;;
    *) echo "  ✗ Unsupported system: $OS"; return 1 ;;
  esac
}

PYTHON=""
if ! find_python; then
  if [ "$SYSTEM_INSTALL" -eq 1 ]; then
    install_python || true
    hash -r
    # Homebrew may have just been used: refresh PATH for this shell
    if [ "$OS" = "Darwin" ]; then
      for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
    fi
    find_python || true
  fi
fi

if [ -z "$PYTHON" ]; then
  echo ""
  echo "  ✗  Python 3.9 or higher is required but was not found."
  echo "     macOS : brew install python@3.12      Ubuntu : sudo apt install python3 python3-venv"
  echo "     or download it from https://www.python.org/downloads/"
  echo ""
  exit 1
fi

# Debian / Ubuntu ship the venv module as a separate package
if ! "$PYTHON" -c 'import venv, ensurepip' &>/dev/null; then
  if [ "$SYSTEM_INSTALL" -eq 1 ] && command -v apt-get &>/dev/null; then
    echo "  The Python venv module is missing — installing python3-venv …"
    $SUDO apt-get update && $SUDO apt-get install -y python3-venv python3-pip || true
  fi
fi

echo ""
echo "  Using Python: $($PYTHON --version)  ($OS $(uname -m))"
echo "  Launcher    : $SCRIPT_DIR/tools/launcher.py"
echo ""

exec "$PYTHON" "$SCRIPT_DIR/tools/launcher.py" "$@"
