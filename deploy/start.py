#!/usr/bin/env python3
"""
start.py — Course Catalog: native setup & launch (cross-platform)

Handles everything start.sh / start.bat used to do:
  1. Create / reuse Python virtual environment
  2. Install dependencies
  3. Detect Docker / Elasticsearch (optional)
  4. Prompt for (or accept) courses path
  5. Generate course catalog (catalog_generator.py)
  6. Check Ollama (optional RAG feature)
  7. Start backend server (catalog_server.py)

This file is the single source of truth for all OS platforms.
start.sh / start.bat are thin wrappers that just find Python and exec this.

Usage (called directly or via wrapper):
  python start.py                        # interactive
  python start.py "/path/to/courses"     # use given path
  python start.py --help
"""

import os, sys, subprocess, shutil, time, json, argparse, signal
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

SCRIPT_DIR  = Path(__file__).parent.resolve()   # deploy/
ROOT_DIR    = SCRIPT_DIR.parent                  # _Course_catalog_Generator/
BACKEND_DIR = ROOT_DIR / 'backend'
VENV_DIR    = ROOT_DIR / '.venv'
REQ_FILE    = BACKEND_DIR / 'requirements.txt'
MIN_PY     = (3, 9)

# ── Bootstrap ─────────────────────────────────────────────────────────────────
# If we're not yet inside the project venv, create it + install deps + re-exec.
# The env-var _CATALOG_VENV=1 prevents infinite loops.

def _venv_python() -> Path:
    sub = 'Scripts' if sys.platform == 'win32' else 'bin'
    exe = 'python.exe' if sys.platform == 'win32' else 'python3'
    return VENV_DIR / sub / exe

def _bootstrap():
    if os.environ.get('_CATALOG_VENV') == '1':
        return  # already inside venv
    if sys.version_info < MIN_PY:
        print(f"Python {MIN_PY[0]}.{MIN_PY[1]}+ required (found {sys.version_info[:2]})",
              file=sys.stderr)
        sys.exit(1)

    venv_py = _venv_python()
    if not venv_py.exists():
        print("  Creating virtual environment in .venv …")
        subprocess.run([sys.executable, '-m', 'venv', str(VENV_DIR)], check=True)
        print("  Installing dependencies …")
        pip = VENV_DIR / ('Scripts/pip.exe' if sys.platform == 'win32' else 'bin/pip')
        if REQ_FILE.exists():
            subprocess.run([str(pip), 'install', '-r', str(REQ_FILE), '-q'], check=True)
        else:
            subprocess.run([str(pip), 'install',
                'flask', 'flask-cors', 'PyMuPDF', 'python-docx', 'openpyxl',
                'python-pptx', 'striprtf', 'beautifulsoup4', 'chardet', 'pdfminer.six',
                '-q'], check=True)

    env = {**os.environ, '_CATALOG_VENV': '1'}
    result = subprocess.run([str(venv_py)] + sys.argv, env=env)
    sys.exit(result.returncode)

_bootstrap()

# ─────────────────────────────────────────────────────────────────────────────
# Everything below runs inside the venv (all third-party deps available).
# ─────────────────────────────────────────────────────────────────────────────

# ── Colors ────────────────────────────────────────────────────────────────────

def _init_colors() -> dict:
    if not sys.stdout.isatty():
        return {k: '' for k in ('RED', 'GREEN', 'YELLOW', 'CYAN', 'BOLD', 'DIM', 'RESET')}
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7)
        except Exception:
            return {k: '' for k in ('RED', 'GREEN', 'YELLOW', 'CYAN', 'BOLD', 'DIM', 'RESET')}
    return {
        'RED':    '\033[0;31m', 'GREEN':  '\033[0;32m', 'YELLOW': '\033[1;33m',
        'CYAN':   '\033[0;36m', 'BOLD':   '\033[1m',    'DIM':    '\033[2m',
        'RESET':  '\033[0m',
    }

C = _init_colors()

def header(m): print(f"\n{C['BOLD']}{C['CYAN']}▸ {m}{C['RESET']}")
def ok(m):     print(f"  {C['GREEN']}✓{C['RESET']}  {m}")
def warn(m):   print(f"  {C['YELLOW']}⚠{C['RESET']}  {m}")
def info(m):   print(f"  {C['DIM']}{m}{C['RESET']}")
def err(m):    print(f"\n  {C['RED']}✗  ERROR:{C['RESET']} {m}", file=sys.stderr); sys.exit(1)
def sep():     print(f"{C['DIM']}{'─' * 54}{C['RESET']}")
def ask(m):    return input(f"  {C['BOLD']}{m}{C['RESET']} ").strip()

# ── Platform helpers ──────────────────────────────────────────────────────────

def _is_wsl() -> bool:
    try:
        return 'microsoft' in Path('/proc/version').read_text().lower()
    except Exception:
        return False

def _http_ok(url: str, timeout: int = 3) -> bool:
    try:
        with urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False

def _open_browser(path_or_url: str):
    """Open a local file or URL — WSL and Windows aware."""
    if _is_wsl():
        try:
            win = subprocess.run(['wslpath', '-w', path_or_url],
                                  capture_output=True, text=True).stdout.strip()
            subprocess.Popen(['cmd.exe', '/c', 'start', '', win],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    import webbrowser
    url = f'file://{path_or_url}' if Path(path_or_url).exists() else path_or_url
    webbrowser.open(url)

def _docker_running() -> bool:
    return subprocess.run(['docker', 'info'], capture_output=True).returncode == 0

def _compose_cmd():
    if subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode == 0:
        return ['docker', 'compose']
    if shutil.which('docker-compose'):
        return ['docker-compose']
    return None

# ── Read env.py defaults ──────────────────────────────────────────────────────

def _env_default() -> str:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('env', BACKEND_DIR / 'env.py')
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, 'SEARCH_BASE_PATH', '').split(';')[0].strip()
    except Exception:
        return ''

# ── Ollama helper ─────────────────────────────────────────────────────────────

def _check_ollama():
    setup_script = BACKEND_DIR / 'ai_ollama_setup.py'
    if not setup_script.exists():
        info("ai_ollama_setup.py not found — skipping Ollama setup.")
        return
    try:
        r = subprocess.run([sys.executable, str(setup_script), '--json'],
                           capture_output=True, text=True, timeout=10)
        d = json.loads(r.stdout or '{}')
    except Exception:
        d = {}

    ollama    = d.get('ollama', {})
    running   = ollama.get('running', False)
    installed = ollama.get('installed', False)
    rec_llm   = (d.get('recommendations', {}).get('llm') or [''])[0]

    if running:
        models = ', '.join(ollama.get('models', []))
        ok(f"Ollama running{f' — models: {models}' if models else ''}")
        return

    if installed:
        warn("Ollama installed but not running. Start with: ollama serve")
    else:
        warn("Ollama not installed.")
        info("Ollama enables the 'Ask your documents' (RAG) feature.")
        if rec_llm:
            info(f"Recommended model for this machine: {rec_llm}")
    if ask("Run Ollama setup assistant? [y/N]:").lower() in ('y', 'yes'):
        subprocess.run([sys.executable, str(setup_script)])

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='start.py',
        description='Course Catalog — native setup & launch',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python start.py
  python start.py "/path/to/courses"
  python start.py "/mnt/c/Users/Me/Videos/Courses"
""")
    parser.add_argument('courses_path', nargs='?', default='',
                        help='Path to the courses directory')
    args = parser.parse_args()

    # ── Banner ────────────────────────────────────────────────────────────────
    print(f"\n{C['BOLD']}")
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║          Course Catalog — Setup & Launch             ║")
    print("  ║          Native mode  (no Docker required)           ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print(C['RESET'])

    # ── Step 0: Docker / Elasticsearch (optional) ─────────────────────────────
    header("Step 0 — Docker / Elasticsearch (optional)")

    docker_available = False
    if shutil.which('docker'):
        if _docker_running():
            ver = subprocess.run(['docker', '--version'],
                                  capture_output=True, text=True).stdout.strip()
            ok(f"Docker running: {ver}")
            docker_available = True
        else:
            warn("Docker installed but daemon not running.")
            if sys.platform.startswith('linux') and not _is_wsl():
                info("Attempting to start Docker daemon …")
                subprocess.run(['sudo', 'systemctl', 'start', 'docker'], capture_output=True)
                time.sleep(2)
                docker_available = _docker_running()
                ok("Docker started.") if docker_available else warn("Could not start Docker.")
            elif sys.platform == 'darwin':
                info("Start Docker Desktop from your Applications folder.")
    else:
        warn("Docker not installed — Elasticsearch search will not be available.")
        info("Install from: https://docs.docker.com/get-docker/")

    if docker_available:
        compose = _compose_cmd()
        if compose:
            es_compose = SCRIPT_DIR / 'docker-compose.yml'   # stays in deploy/
            if es_compose.exists():
                if ask("Start Elasticsearch container? [y/N]:").lower() in ('y', 'yes'):
                    subprocess.run(compose + ['-f', str(es_compose), 'up', '-d', 'elasticsearch'],
                                   capture_output=True)
                    ok("Elasticsearch container started (may take ~30 s to be ready)")

    # ── Step 1: Python info ───────────────────────────────────────────────────
    header("Step 1 — Python")
    ok(f"Python {sys.version.split()[0]} — venv active ({VENV_DIR})")

    # ── Step 2: Courses path ──────────────────────────────────────────────────
    header("Step 2 — Courses Path")

    courses_path = args.courses_path
    env_default  = _env_default()

    if not courses_path:
        if env_default:
            info(f"Current env.py default: {env_default}")
            inp = ask("Enter courses path [Enter = keep default]:")
            courses_path = inp or env_default
        else:
            courses_path = ask("Enter the path to your courses directory:")

    if not courses_path:
        err("No courses path provided.")

    if not Path(courses_path).exists():
        warn(f"Path '{courses_path}' does not exist or is not accessible.")
        if ask("Continue anyway? [y/N]:").lower() not in ('y', 'yes'):
            err("Aborted.")
    ok(f"Courses path: {courses_path}")

    # ── Step 3: Virtual environment ───────────────────────────────────────────
    header("Step 3 — Virtual Environment")
    ok(f"Active: {VENV_DIR} (Python {sys.version.split()[0]})")

    # ── Step 4: Dependencies ──────────────────────────────────────────────────
    header("Step 4 — Dependencies")
    info("Checking / updating dependencies …")
    if REQ_FILE.exists():
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(REQ_FILE), '-q'],
                       check=True)
        ok("All dependencies installed / up-to-date")
    else:
        warn("requirements.txt not found — skipping dependency update")

    # ── Step 5: Generate catalog ──────────────────────────────────────────────
    header("Step 5 — Generating Course Catalog")

    gen = BACKEND_DIR / 'catalog_generator.py'
    if not gen.exists():
        warn("catalog_generator.py not found — skipping catalog generation")
    else:
        output_dir = ROOT_DIR / 'output'
        output_dir.mkdir(exist_ok=True)
        info(f"Running: {gen.name} \"{courses_path}\"")
        r = subprocess.run([sys.executable, str(gen), courses_path, '--output', str(output_dir)])
        if r.returncode == 0:
            ok("Catalog generated:")
            info(f"  {output_dir / 'course_catalog.md'}")
            info(f"  {output_dir / 'course_catalog.html'}")
        else:
            warn(f"Catalog generation exited with code {r.returncode}")

    # ── Step 5.5: Ollama ──────────────────────────────────────────────────────
    header("Step 5.5 — Ollama (optional)")
    _check_ollama()

    # ── Step 6: Start backend server ──────────────────────────────────────────
    header("Step 6 — Starting Backend Server")

    sep()
    catalog_html = ROOT_DIR / 'output' / 'course_catalog.html'
    print(f"\n  {C['BOLD']}Catalog HTML :{C['RESET']} {catalog_html}")
    print(f"  {C['BOLD']}Backend API  :{C['RESET']} http://localhost:5000")
    print(f"  {C['BOLD']}Courses path :{C['RESET']} {courses_path}")
    print()
    sep()
    print()
    print(f"  {C['BOLD']}Quick tests (open a second terminal):{C['RESET']}")
    print(f"\n  {C['CYAN']}# Health check{C['RESET']}")
    print("  curl http://localhost:5000/api/health")
    print(f"\n  {C['CYAN']}# Full-text search{C['RESET']}")
    print("  curl 'http://localhost:5000/api/search?q=Introduction'")
    print()
    sep()
    print(f"\n  {C['YELLOW']}Press Ctrl+C to stop the server.{C['RESET']}\n")

    server = BACKEND_DIR / 'catalog_server.py'
    subprocess.run([sys.executable, str(server), '--path', courses_path])


if __name__ == '__main__':
    main()
