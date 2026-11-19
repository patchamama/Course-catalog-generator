#!/usr/bin/env python3
"""
Catalog — DMS Lite  |  Desktop App Mode
==========================================
Wraps the application in a native OS window using pywebview, so it looks and
behaves like a proper desktop application (taskbar icon, native menus, etc.).

Install pywebview:
    pip install pywebview

Usage:
    python3 desktop.py              # native window, auto-starts backend
    python3 desktop.py --browser    # fall back to system browser instead

Build a standalone executable (requires PyInstaller):
    pip install pyinstaller
    pyinstaller --onefile --windowed --name "Catalog DMS Lite" desktop.py
    # macOS: also bundles as .app in dist/
    # Windows: creates dist/Catalog DMS Lite.exe

See tools/package/ for full platform-specific packaging scripts.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT          = Path(__file__).parent.parent.resolve()   # tools/ → project root
LAUNCHER_PORT = 5099
BACKEND_PORT  = 5000
APP_TITLE     = "Catalog — DMS Lite"
APP_WIDTH     = 1300
APP_HEIGHT    = 820
APP_MIN_W     = 900
APP_MIN_H     = 600


def _probe(url: str, timeout: float = 1.5) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def run_native(launcher_proc: subprocess.Popen) -> None:
    """Open native window once the launcher UI is ready."""
    try:
        import webview  # type: ignore[import]
    except ImportError:
        print("pywebview not installed. Install with: pip install pywebview", file=sys.stderr)
        sys.exit(1)

    # Wait for launcher HTTP server
    for _ in range(20):
        if _probe(f"http://127.0.0.1:{LAUNCHER_PORT}/"):
            break
        time.sleep(0.3)

    start_url = f"http://127.0.0.1:{LAUNCHER_PORT}/"

    window = webview.create_window(
        title=APP_TITLE,
        url=start_url,
        width=APP_WIDTH,
        height=APP_HEIGHT,
        min_size=(APP_MIN_W, APP_MIN_H),
        text_select=True,
    )

    def _on_loaded():
        """Once the backend is ready, the startup.html auto-redirects — nothing to do."""
        pass

    window.events.loaded += _on_loaded
    webview.start(debug=False)

    # Window closed — stop launcher
    launcher_proc.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} — Desktop Mode")
    parser.add_argument("--browser", action="store_true",
                        help="Open in system browser instead of native window")
    parser.add_argument("--backend-port", type=int, default=BACKEND_PORT)
    parser.add_argument("--launcher-port", type=int, default=LAUNCHER_PORT)
    args = parser.parse_args()

    # Start the launcher (which in turn starts the Flask backend)
    launcher_args = [
        sys.executable, str(ROOT / "tools" / "launcher.py"),
        "--no-browser",
        "--backend-port",  str(args.backend_port),
        "--launcher-port", str(args.launcher_port),
    ]
    proc = subprocess.Popen(launcher_args, cwd=str(ROOT))

    if args.browser:
        import webbrowser
        time.sleep(1.0)
        webbrowser.open(f"http://127.0.0.1:{args.launcher_port}/")
        proc.wait()
    else:
        try:
            run_native(proc)
        except SystemExit:
            pass
        except Exception as exc:
            print(f"Native window failed ({exc}). Falling back to browser.", file=sys.stderr)
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{args.launcher_port}/")
            proc.wait()


if __name__ == "__main__":
    main()
