#!/usr/bin/env python3
"""
Catalog DMS Lite — Startup Launcher
=====================================
Opens a startup progress screen in the browser (http://localhost:5099/),
streams live service status and log output via Server-Sent Events, then
starts the Flask backend and redirects the browser to the application.

Usage
-----
    python3 launcher.py                          # interactive (opens browser)
    python3 launcher.py --backend-port 8080      # custom backend port
    python3 launcher.py --no-browser             # skip auto-open
    python3 launcher.py --no-install             # skip pip install step
"""
from __future__ import annotations

import argparse
import json
import os
# The Hugging Face 'xet' downloader can stall at 0 B/s on some networks; the plain HTTP one is reliable.
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
import shutil
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

# ── Windows console UTF-8 fix ─────────────────────────────────────────────────
# The banner uses box-drawing characters that cp1252 (Windows default) cannot
# encode, crashing the launcher before it even starts.  Reconfigure stdout/stderr
# to UTF-8 (Python 3.7+) so Unicode output works regardless of the active code page.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT           = Path(__file__).parent.parent.resolve()   # tools/ → project root
BACKEND_MODULE = ROOT / "backend" / "catalog_server.py"
REQUIREMENTS   = ROOT / "backend" / "requirements.txt"
ENV_FILE       = ROOT / "backend" / "env.py"
STARTUP_HTML   = ROOT / "frontend" / "startup.html"

# env.py is per-machine configuration and is not committed: create it from the template on first start
_ENV_TEMPLATE = ROOT / "backend" / "env.example.py"
if not ENV_FILE.exists() and _ENV_TEMPLATE.exists():
    try:
        ENV_FILE.write_bytes(_ENV_TEMPLATE.read_bytes())
    except OSError:
        pass

# ── Defaults ──────────────────────────────────────────────────────────────────
LAUNCHER_PORT = 5099
BACKEND_PORT  = 5000
BACKEND_HOST  = "127.0.0.1"

# ── Shared broadcast state ────────────────────────────────────────────────────
_lock:        threading.Lock         = threading.Lock()
_log_buffer:  deque[dict]            = deque(maxlen=500)
_subscribers: list[queue.Queue]      = []
_svc_status:  dict[str, dict]        = {
    "python":        {"state": "pending", "msg": ""},
    "deps":          {"state": "pending", "msg": ""},
    "backend":       {"state": "pending", "msg": ""},
    "elasticsearch": {"state": "pending", "msg": ""},
    "ollama":        {"state": "pending", "msg": ""},
}
_redirect_url: str | None = None
_setup_done:   threading.Event = threading.Event()


def _broadcast(event: dict) -> None:
    """Push an event to the buffer and all active SSE subscribers."""
    with _lock:
        _log_buffer.append(event)
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def _subscribe() -> "queue.Queue[dict]":
    """Return a queue pre-loaded with current buffer for a new SSE client."""
    q: "queue.Queue[dict]" = queue.Queue(maxsize=1000)
    with _lock:
        for ev in _log_buffer:
            try:
                q.put_nowait(ev)
            except queue.Full:
                break
        _subscribers.append(q)
    return q


def _unsubscribe(q: "queue.Queue[dict]") -> None:
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def log(msg: str, level: str = "info") -> None:
    print(msg, flush=True)
    _broadcast({"type": "log", "msg": msg, "level": level, "ts": time.time()})


def set_status(service: str, state: str, msg: str = "") -> None:
    with _lock:
        _svc_status[service] = {"state": state, "msg": msg}
    _broadcast({"type": "status", "service": service, "state": state, "msg": msg})


# ── HTTP / SSE request handler ────────────────────────────────────────────────

class LauncherHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass  # silence access log

    # ── GET ──────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/startup.html"):
            self._serve_file(STARTUP_HTML, "text/html; charset=utf-8")
        elif path == "/events":
            self._stream_sse()
        elif path == "/api/status":
            self._send_json({"status": _svc_status, "redirect": _redirect_url})
        else:
            self.send_response(404)
            self.end_headers()

    # ── POST ─────────────────────────────────────────────────────────────────
    def do_POST(self) -> None:
        if self.path == "/api/configure":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                _apply_config(data)
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self._cors()
        self.end_headers()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _stream_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        q = _subscribe()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    data = json.dumps(event)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    if event.get("type") == "redirect":
                        break
                except queue.Empty:
                    # Keep-alive heartbeat
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            _unsubscribe(q)


# ── Config helper ─────────────────────────────────────────────────────────────

def _apply_config(data: dict) -> None:
    """Patch env.py with updated service URLs."""
    if not ENV_FILE.exists():
        log(f"env.py not found at {ENV_FILE} — cannot save config.", "warning")
        return

    content = ENV_FILE.read_text(encoding="utf-8")
    if "es_url" in data and data["es_url"]:
        content = re.sub(
            r'(ES_URL\s*=\s*)["\'].*?["\']',
            f'\\1"{data["es_url"]}"',
            content,
        )
    if "ollama_url" in data and data["ollama_url"]:
        content = re.sub(
            r'(OLLAMA_URL\s*=\s*)["\'].*?["\']',
            f'\\1"{data["ollama_url"]}"',
            content,
        )
    ENV_FILE.write_text(content, encoding="utf-8")
    log(f"Config saved to {ENV_FILE.name}", "success")


# ── URL probe ─────────────────────────────────────────────────────────────────

def _probe(url: str, timeout: float = 2.0) -> bool:
    """Return True if url responds with HTTP status < 500.

    Raw socket + HTTP/1.0 so no keep-alive can block us.  A single recv(512)
    is used — no loop — so the socket timeout is the only way it can block,
    and only for at most `timeout` seconds.

    'localhost' is resolved to 127.0.0.1 explicitly to skip the Windows
    IPv6 (::1) resolution attempt that can add several seconds of latency."""
    from urllib.parse import urlparse
    try:
        p    = urlparse(url)
        host = p.hostname or "127.0.0.1"
        port = p.port or 80
        # Force IPv4 — avoids Windows trying ::1 first when ES/Ollama only
        # listen on IPv4, which causes a slow connection-refused cycle.
        if host in ("localhost", "::1"):
            host = "127.0.0.1"
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(
                f"GET {path} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode()
            )
            buf = s.recv(512)   # one read; timeout fires if server is silent
        first = buf.split(b"\r\n")[0].decode("ascii", errors="ignore")
        parts = first.split()
        return len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) < 500
    except Exception:
        return False


def _free_port(port: int) -> None:
    """Kill any process currently listening on port (best-effort, silent).
    Called at launcher startup to clean up stale instances."""
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.strip().split()
                    if parts and parts[-1].isdigit():
                        pid = int(parts[-1])
                        if pid != os.getpid():
                            subprocess.run(
                                ["taskkill", "/F", "/PID", str(pid)],
                                capture_output=True,
                            )
        else:
            out = subprocess.check_output(
                ["lsof", "-ti", f"tcp:{port}"],
                text=True, stderr=subprocess.DEVNULL,
            )
            for pid_str in out.strip().splitlines():
                if pid_str.isdigit():
                    pid = int(pid_str)
                    if pid != os.getpid():
                        try:
                            os.kill(pid, 9)   # SIGKILL — immediate
                        except ProcessLookupError:
                            pass
    except Exception:
        pass  # lsof/netstat not available or no process on that port — fine


# ── Setup sequence ────────────────────────────────────────────────────────────

def setup_sequence(args: argparse.Namespace) -> None:
    global _redirect_url
    time.sleep(0.8)  # give browser time to open

    # ── 1. Python version ─────────────────────────────────────────────────────
    set_status("python", "checking")
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    log(f"Python {ver} — {sys.executable}")
    if sys.version_info < (3, 9):
        set_status("python", "error", f"Needs 3.9+ (got {ver})")
        log(f"✗ Python {ver} is too old. Install Python 3.9+ from https://www.python.org/", "error")
        return
    set_status("python", "ok", f"Python {ver}")

    # ── 2. Virtual environment + dependencies ─────────────────────────────────
    venv_dir = ROOT / ".venv"

    # Platform-specific venv paths
    if sys.platform == "win32":
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python3"

    if not args.no_install and REQUIREMENTS.exists():
        # Create venv if missing or incomplete
        if not venv_python.exists():
            set_status("deps", "checking", "Creating virtual environment…")
            log("Creating virtual environment at .venv/ …")
            r = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True, text=True,
            )
            # Debian / Ubuntu ship the venv module as a separate package: install it and retry once
            if (r.returncode != 0 and sys.platform.startswith("linux") and not args.no_system_install
                    and any(k in (r.stderr or "") + (r.stdout or "") for k in ("ensurepip", "python3-venv", "No module named"))):
                try:
                    sys.path.insert(0, str(Path(__file__).parent))
                    import prereqs
                    if prereqs.ensure_python_venv(log):
                        shutil.rmtree(venv_dir, ignore_errors=True)       # drop the half-built environment
                        r = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                                           capture_output=True, text=True)
                except Exception as e:
                    log(f"⚠ Could not install python3-venv automatically: {e}", "warning")
            if r.returncode != 0:
                set_status("deps", "error", "venv creation failed")
                err_out = (r.stderr or r.stdout or "")
                for line in err_out.splitlines():
                    if line.strip():
                        log(line, "error")
                # Debian/Ubuntu hint for missing python3-venv
                if "ensurepip" in err_out or "python3-venv" in err_out or "No module named" in err_out:
                    log("Hint: install python3-venv with:", "warning")
                    log("  sudo apt install python3-venv python3-full", "warning")
                log("Falling back to system Python (may fail on PEP-668 systems).", "warning")
                venv_python = Path(sys.executable)
            else:
                log("✓ Virtual environment created at .venv/")
        else:
            log("Using existing virtual environment at .venv/")

        # Install / upgrade dependencies inside venv
        set_status("deps", "checking", "Installing packages…")
        log(f"Installing dependencies from {REQUIREMENTS.name}…")

        # List the packages that will be processed so the user sees progress
        try:
            req_lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            pkgs = [
                ln.strip() for ln in req_lines
                if ln.strip() and not ln.strip().startswith("#")
            ]
            log(f"Packages to install/upgrade ({len(pkgs)}):")
            for pkg in pkgs:
                log(f"  • {pkg}")
        except OSError as e:
            log(f"Could not read {REQUIREMENTS}: {e}", "warning")

        # Stream pip output line by line so the UI shows real progress
        pip_cmd = [
            str(venv_python), "-m", "pip", "install",
            "-r", str(REQUIREMENTS),
            "--upgrade",
            "--progress-bar", "off",
        ]
        proc_pip = subprocess.Popen(
            pip_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc_pip.stdout is not None
        for line in proc_pip.stdout:
            line = line.rstrip()
            if not line:
                continue
            lower = line.lower()
            if lower.startswith("error") or "error:" in lower:
                level = "error"
            elif lower.startswith("warning") or "deprecat" in lower:
                level = "warning"
            else:
                level = "info"
            # Surface key milestones in the status chip
            if lower.startswith("collecting "):
                set_status("deps", "checking", line)
            elif lower.startswith("installing collected packages"):
                set_status("deps", "checking", "Installing collected packages…")
            log(line, level)
        rc = proc_pip.wait()
        if rc == 0:
            set_status("deps", "ok", "All packages ready")
            log("✓ Dependencies installed")
        else:
            set_status("deps", "error", f"pip install failed (exit {rc})")
            log(f"⚠ pip exited with code {rc} — backend may not start correctly.", "warning")

    elif not REQUIREMENTS.exists():
        set_status("deps", "warning", "requirements.txt not found")
        log(f"requirements.txt not found at {REQUIREMENTS}", "warning")
    else:
        set_status("deps", "ok", "Skipped (--no-install)")
        log("Dependency install skipped.")

    # Choose Python for the backend: prefer venv, fall back to system
    backend_python = str(venv_python) if venv_python.exists() else sys.executable
    log(f"Backend Python: {backend_python}")

    # ── 3. Flask backend ──────────────────────────────────────────────────────
    if not BACKEND_MODULE.exists():
        set_status("backend", "error", "catalog_server.py not found")
        log(f"✗ Backend not found: {BACKEND_MODULE}", "error")
        return

    set_status("backend", "checking", f"Starting on :{args.backend_port}…")
    log(f"Starting backend on port {args.backend_port}…")

    proc = subprocess.Popen(
        [backend_python, str(BACKEND_MODULE), "--port", str(args.backend_port), "--no-reload"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=os.environ.copy(),
    )

    def _relay():
        """Forward Flask stdout to the SSE log, filtering out HTTP access lines.

        Werkzeug access log lines always contain ' HTTP/1.' (the protocol token
        inside the quoted request string).  That substring is extremely unlikely
        to appear in real application log messages, making it a reliable filter."""
        assert proc.stdout
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line or " HTTP/1." in line:
                continue   # skip blank lines and HTTP access log noise
            log(line)

    threading.Thread(target=_relay, daemon=True).start()

    # Wait for backend — probe /api/ping (instant, no ES/Ollama checks)
    backend_url = f"http://{BACKEND_HOST}:{args.backend_port}"
    log(f"  Waiting for backend on {backend_url}/api/ping …")
    for attempt in range(60):
        if proc.poll() is not None:
            set_status("backend", "error", "Exited before ready")
            log("✗ Backend process terminated unexpectedly.", "error")
            return
        t0 = time.monotonic()
        ok = _probe(f"{backend_url}/api/ping", timeout=3.0)
        elapsed = time.monotonic() - t0
        log(f"  attempt {attempt + 1}: {'ok' if ok else 'waiting'} ({elapsed:.1f}s)")
        if ok:
            break
        time.sleep(1)
    else:
        set_status("backend", "error", "Timed out (60 s)")
        log("✗ Backend did not respond within 60 s.", "error")
        return

    set_status("backend", "ok", f"Running on :{args.backend_port}")
    log(f"✓ Backend ready at {backend_url}")

    # ── 4. System requirements: Docker, Ollama, Node.js, pandoc, ffmpeg (+ VLC / Tesseract with --with-extras) ──
    # Missing ones are installed with the system package manager (Homebrew / apt / winget) unless
    # --no-system-install is given; a failure is only a warning — the app still starts without them.
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import prereqs
        set_status("elasticsearch", "checking", "Checking system requirements…")
        set_status("ollama", "checking", "Checking system requirements…")
        log("Checking system requirements (Docker, Ollama, Node.js, pandoc, ffmpeg) …")
        prereqs.ensure_system(log, with_extras=args.with_extras, install_missing=not args.no_system_install,
                              status=set_status)
    except Exception as e:                                # never block the app on the installer
        prereqs = None
        log(f"⚠ System-requirement check failed: {e}", "warning")

    # ── 5. Elasticsearch: start it (Docker) if it is not answering ────────────
    set_status("elasticsearch", "checking")
    t0 = time.monotonic()
    es_ok = _probe("http://127.0.0.1:9200/", timeout=3.0)
    if not es_ok and prereqs is not None and prereqs.is_present("docker"):
        set_status("elasticsearch", "checking", "Starting Elasticsearch (Docker)…")
        es_ok = prereqs.ensure_elasticsearch(log)
    log(f"  Elasticsearch: {'ok' if es_ok else 'not available'} ({time.monotonic()-t0:.1f}s)")
    if es_ok:
        set_status("elasticsearch", "ok", "Connected :9200")
        log("✓ Elasticsearch ready on :9200")
    else:
        set_status("elasticsearch", "warning", "Not running (optional)")
        log("⚠ Elasticsearch is not running — advanced search is disabled. Install Docker and re-run, or start it with "
            "`docker compose -f deploy/docker-compose.yml up -d elasticsearch`.", "warning")

    # ── 6. Ollama: start it if installed ──────────────────────────────────────
    set_status("ollama", "checking")
    t0 = time.monotonic()
    ollama_ok = _probe("http://127.0.0.1:11434/", timeout=3.0)
    if not ollama_ok and prereqs is not None:
        ollama_ok = prereqs.ensure_ollama_running(log)
    log(f"  Ollama: {'ok' if ollama_ok else 'not available'} ({time.monotonic()-t0:.1f}s)")
    if ollama_ok:
        set_status("ollama", "ok", "Running :11434")
        log("✓ Ollama ready on :11434 (install models from Search Console › Tools › Ollama Model Installer)")
    else:
        set_status("ollama", "warning", "Not running (optional)")
        log("⚠ Ollama not found — local chat is disabled (external LLMs still work). Install from https://ollama.com/", "warning")

    # ── Done ──────────────────────────────────────────────────────────────────
    log("✓ Application ready!", "success")
    _redirect_url = f"{backend_url}/"
    _broadcast({"type": "redirect", "url": _redirect_url})
    _setup_done.set()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Catalog DMS Lite — Startup Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--launcher-port", type=int, default=LAUNCHER_PORT,
                        metavar="PORT",
                        help=f"Port for the startup UI (default: {LAUNCHER_PORT})")
    parser.add_argument("--backend-port",  type=int, default=BACKEND_PORT,
                        metavar="PORT",
                        help=f"Port for the Flask backend (default: {BACKEND_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open the browser automatically")
    parser.add_argument("--no-install", action="store_true",
                        help="Skip automatic pip install of dependencies")
    parser.add_argument("--no-system-install", action="store_true",
                        help="Do not install system requirements (Docker, Ollama, Node.js, pandoc, ffmpeg); "
                             "still starts the ones that are installed")
    parser.add_argument("--with-extras", action="store_true",
                        help="Also install VLC (video player buttons) and Tesseract (OCR)")
    args = parser.parse_args()

    banner = (
        "╔══════════════════════════════════════╗\n"
        "║  Catalog — DMS Lite                  ║\n"
        "║  Startup Launcher                    ║\n"
        "╚══════════════════════════════════════╝\n"
        f"  Startup UI : http://localhost:{args.launcher_port}/\n"
        f"  Backend    : http://127.0.0.1:{args.backend_port}/\n"
        "\n"
        "  If the browser does not open automatically,\n"
        f"  navigate to: http://localhost:{args.launcher_port}/\n"
    )
    print(banner, flush=True)

    # Kill any stale launcher or backend from a previous run before binding
    print("  Cleaning up stale processes…", flush=True)
    _free_port(args.launcher_port)
    _free_port(args.backend_port)
    time.sleep(0.4)   # let the OS release the ports

    server = ThreadingHTTPServer(("127.0.0.1", args.launcher_port), LauncherHandler)

    # Open browser (WSL-aware)
    if not args.no_browser:
        def _open_browser():
            time.sleep(0.5)
            url = f"http://localhost:{args.launcher_port}/"
            opened = False

            # ── WSL detection ──────────────────────────────────────────────
            try:
                osrelease = Path("/proc/sys/kernel/osrelease").read_text().lower()
                is_wsl = "microsoft" in osrelease or "wsl" in osrelease
            except Exception:
                is_wsl = False

            if is_wsl:
                # Try wslview first (from wslu package), then cmd.exe
                for cmd in (["wslview", url], ["cmd.exe", "/c", "start", url]):
                    try:
                        subprocess.run(cmd, capture_output=True, timeout=5)
                        opened = True
                        break
                    except Exception:
                        pass

            if not opened:
                try:
                    webbrowser.open(url)
                    opened = True
                except Exception:
                    pass

            if not opened:
                print(f"\n  ► Open in your browser: {url}\n", flush=True)

        threading.Thread(target=_open_browser, daemon=True).start()

    # Run setup in background
    threading.Thread(target=setup_sequence, args=(args,), daemon=True).start()

    print(f"Launcher ready — press Ctrl+C to stop.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down launcher…", flush=True)


if __name__ == "__main__":
    main()
