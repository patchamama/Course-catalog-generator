#!/usr/bin/env python3
"""
setup-dev.py — Course Catalog: full dev / Elasticsearch environment setup

Replicates everything setup-dev.sh / setup-dev.bat used to do:
  1. Create / reuse Python virtual environment + install deps
  2. Start Docker + Elasticsearch
  3. Optionally index a directory (incremental or full --reset)
  4. Start the Flask backend in the background
  5. Check Ollama (optional)
  6. Show ES index status and open search-console.html in the browser

This file is the single source of truth for all OS platforms.
setup-dev.sh / setup-dev.bat are thin wrappers that just find Python and exec this.

Usage:
  python setup-dev.py                              # interactive
  python setup-dev.py /path/to/courses             # non-interactive, incremental
  python setup-dev.py --reset /path/to/courses     # wipe + full reindex
  python setup-dev.py --reset                      # interactive with forced reset
  python setup-dev.py --ext pdf,md /path/to/courses
"""

import os, sys, subprocess, shutil, time, json, argparse, signal, atexit
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

SCRIPT_DIR       = Path(__file__).parent.resolve()   # deploy/
ROOT_DIR         = SCRIPT_DIR.parent                  # _Course_catalog_Generator/
BACKEND_DIR      = ROOT_DIR / 'backend'
VENV_DIR         = ROOT_DIR / '.venv'
REQ_FILE         = BACKEND_DIR / 'requirements.txt'
ES_TEST_HTML     = ROOT_DIR / 'frontend' / 'search-console.html'
ES_URL           = 'http://127.0.0.1:9200'
API_URL          = 'http://localhost:5000'
INDEX_NAME       = 'course_catalog'
COMPOSE_FILE     = SCRIPT_DIR / 'docker-compose.yml'   # stays in deploy/
MIN_PY           = (3, 9)

# ── Bootstrap ─────────────────────────────────────────────────────────────────

def _venv_python() -> Path:
    sub = 'Scripts' if sys.platform == 'win32' else 'bin'
    exe = 'python.exe' if sys.platform == 'win32' else 'python3'
    return VENV_DIR / sub / exe

def _bootstrap():
    if os.environ.get('_CATALOG_VENV') == '1':
        return
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
                'flask', 'flask-cors', 'elasticsearch', 'beautifulsoup4',
                'PyMuPDF', 'python-docx', 'openpyxl', '-q'], check=True)

    env = {**os.environ, '_CATALOG_VENV': '1'}
    result = subprocess.run([str(venv_py)] + sys.argv, env=env)
    sys.exit(result.returncode)

_bootstrap()

# ─────────────────────────────────────────────────────────────────────────────
# Everything below runs inside the venv.
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
        'RED':   '\033[0;31m', 'GREEN':  '\033[0;32m', 'YELLOW': '\033[1;33m',
        'CYAN':  '\033[0;36m', 'BOLD':   '\033[1m',    'DIM':    '\033[2m',
        'RESET': '\033[0m',
    }

C = _init_colors()

def header(m): print(f"\n{C['BOLD']}{C['CYAN']}▸ {m}{C['RESET']}")
def ok(m):     print(f"  {C['GREEN']}✓{C['RESET']}  {m}")
def warn(m):   print(f"  {C['YELLOW']}⚠{C['RESET']}  {m}")
def info(m):   print(f"  {C['DIM']}{m}{C['RESET']}")
def err(m):    print(f"\n  {C['RED']}✗  ERROR:{C['RESET']} {m}", file=sys.stderr); sys.exit(1)
def sep():     print(f"{C['DIM']}{'─' * 50}{C['RESET']}")
def ask(m):    return input(f"  {C['BOLD']}{m}{C['RESET']} ").strip()

# ── Platform helpers ──────────────────────────────────────────────────────────

def _is_wsl() -> bool:
    try:
        return 'microsoft' in Path('/proc/version').read_text().lower()
    except Exception:
        return False

def _http_get_json(url: str, timeout: int = 5):
    try:
        with urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _http_ok(url: str, timeout: int = 3) -> bool:
    return _http_get_json(url, timeout) is not None

def _open_browser(path_or_url: str):
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

def _kill_port(port: int):
    """Kill any process listening on the given port (best-effort)."""
    if sys.platform == 'win32':
        try:
            r = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if f':{port}' in line and 'LISTENING' in line:
                    pid = line.strip().split()[-1]
                    subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
        except Exception:
            pass
    else:
        for cmd in [['fuser', '-k', f'{port}/tcp'], ['lsof', '-ti', f':{port}']]:
            try:
                if 'lsof' in cmd[0]:
                    pids = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
                    for pid in pids.split():
                        subprocess.run(['kill', '-9', pid], capture_output=True)
                else:
                    subprocess.run(cmd, capture_output=True)
                break
            except Exception:
                continue

def _compose_cmd():
    if subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode == 0:
        return ['docker', 'compose']
    if shutil.which('docker-compose'):
        return ['docker-compose']
    return None

# ── ES / index helpers ────────────────────────────────────────────────────────

def _es_doc_count() -> int:
    d = _http_get_json(f'{ES_URL}/{INDEX_NAME}/_count')
    return (d or {}).get('count', 0)

def _es_status() -> str:
    d = _http_get_json(f'{ES_URL}/_cluster/health')
    return (d or {}).get('status', '')

def _wait_for_es(timeout: int = 120) -> bool:
    print("  Waiting for Elasticsearch", end='', flush=True)
    for _ in range(timeout // 3):
        st = _es_status()
        if st in ('green', 'yellow'):
            print(f"\n  {C['GREEN']}✓{C['RESET']}  Elasticsearch ready (status: {st})")
            return True
        print('.', end='', flush=True)
        time.sleep(3)
    print()
    return False

def _wait_for_backend(timeout: int = 20) -> bool:
    print("  Waiting for backend", end='', flush=True)
    for _ in range(timeout):
        if _http_ok(f'{API_URL}/api/health'):
            print(f"\n  {C['GREEN']}✓{C['RESET']}  Backend ready at {API_URL}")
            return True
        print('.', end='', flush=True)
        time.sleep(1)
    print()
    return False

def _update_env_path(index_path: str):
    """Append index_path to SEARCH_BASE_PATH in env.py if not already there."""
    env_file = BACKEND_DIR / 'env.py'
    if not env_file.exists():
        return
    import re
    content = env_file.read_text(encoding='utf-8')
    m = re.search(r'^(SEARCH_BASE_PATH\s*=\s*r?["\'])(.+?)(["\'])\s*$',
                  content, re.MULTILINE)
    if not m:
        return
    prefix, val, quote = m.group(1), m.group(2), m.group(3)
    paths = [p.strip() for p in val.split(';') if p.strip()]
    norm  = lambda p: str(Path(p).resolve()).rstrip('/\\').lower()
    if norm(index_path) in {norm(p) for p in paths}:
        return
    paths.append(index_path)
    new_line = prefix + ';'.join(paths) + quote
    env_file.write_text(content[:m.start()] + new_line + content[m.end():],
                        encoding='utf-8')
    ok(f"env.py: SEARCH_BASE_PATH updated with {index_path}")

# ── Ollama helper ─────────────────────────────────────────────────────────────

def _check_ollama():
    setup_script = BACKEND_DIR / 'ai_ollama_setup.py'
    if not setup_script.exists():
        info("ai_ollama_setup.py not found — skipping.")
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
        prog='setup-dev.py',
        description='Course Catalog — full dev/Elasticsearch environment setup',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python setup-dev.py                               # interactive
  python setup-dev.py /path/to/courses              # incremental index
  python setup-dev.py --reset /path/to/courses      # wipe + full reindex
  python setup-dev.py --reset                       # interactive with forced reset
  python setup-dev.py --ext pdf,md /path/to/courses # index only pdf and md
""")
    parser.add_argument('index_dir', nargs='?', default='',
                        help='Directory to index (optional)')
    parser.add_argument('--reset', action='store_true',
                        help='Delete the existing index before indexing (full reindex)')
    parser.add_argument('--ext', default='',
                        help='Comma-separated file extensions to index (e.g. pdf,md,srt)')
    args = parser.parse_args()

    # ── Banner ────────────────────────────────────────────────────────────────
    print(f"\n{C['BOLD']}")
    print("  ╔═══════════════════════════════════════════════════╗")
    print("  ║       Course Catalog — Dev Environment Setup      ║")
    print("  ║    Docker + Elasticsearch + Flask + search-console.html  ║")
    print("  ╚═══════════════════════════════════════════════════╝")
    print(C['RESET'])

    # ── Step 1: Python + venv ─────────────────────────────────────────────────
    header("Step 1/6 — Python")
    ok(f"Python {sys.version.split()[0]} — venv active ({VENV_DIR})")

    # ── Step 2: Dependencies ──────────────────────────────────────────────────
    header("Step 2/6 — Dependencies")
    info("Checking / updating dependencies …")
    if REQ_FILE.exists():
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(REQ_FILE), '-q'],
                       check=True)
        ok("All dependencies installed / up-to-date")
    else:
        warn("requirements.txt not found — skipping dependency update")

    # ── Step 3: Docker + Elasticsearch ───────────────────────────────────────
    header("Step 3/6 — Docker + Elasticsearch")

    if not shutil.which('docker'):
        err("Docker not found.\nInstall from: https://docs.docker.com/get-docker/")

    docker_ok = subprocess.run(['docker', 'info'], capture_output=True).returncode == 0
    if not docker_ok:
        warn("Docker daemon is not running. Attempting to start …")
        if sys.platform.startswith('linux') and not _is_wsl():
            subprocess.run(['sudo', 'systemctl', 'start', 'docker'], capture_output=True)
            time.sleep(3)
            docker_ok = subprocess.run(['docker', 'info'], capture_output=True).returncode == 0
        if not docker_ok:
            err("Docker is not running.\nStart Docker Desktop or run: sudo systemctl start docker")

    ver = subprocess.run(['docker', '--version'],
                          capture_output=True, text=True).stdout.strip()
    ok(f"Docker: {ver}")

    compose = _compose_cmd()
    if not compose:
        err("docker compose not found.\nInstall Docker Desktop or docker-compose.")

    if not COMPOSE_FILE.exists():
        err(f"docker-compose.yml not found at {COMPOSE_FILE}")

    info("Starting Elasticsearch container …")
    subprocess.run(compose + ['-f', str(COMPOSE_FILE), 'up', '-d', 'elasticsearch'],
                   capture_output=True)

    if not _wait_for_es():
        err("Elasticsearch did not respond within 120 s.\n"
            "Check: docker logs course-catalog-es")

    # ── Step 4: Index data (optional) ────────────────────────────────────────
    header("Step 4/6 — Index data (optional)")

    index_path = ''
    default_test = ROOT_DIR.parent / 'test'

    if args.index_dir:
        # Non-interactive path
        if not Path(args.index_dir).is_dir():
            err(f"Directory does not exist: {args.index_dir}")
        index_path = str(Path(args.index_dir).resolve())
        ok(f"Directory from argument: {index_path}")
        reset_flag   = ['--reset'] if args.reset else []
        vector_flag  = []
        ext_flag     = ['--ext', args.ext] if args.ext else []
        info(f"{'Full reindex' if args.reset else 'Incremental indexing'} with semantic vectors.")
    else:
        # Interactive
        if default_test.is_dir():
            ok(f"Test directory found: {default_test}")
            if ask("Index this directory in Elasticsearch? [Y/n]:").lower() not in ('n', 'no'):
                index_path = str(default_test)
            else:
                custom = ask("Index another directory? Enter path or press Enter to skip:")
                if custom:
                    index_path = custom
        else:
            info(f"Test directory not found: {default_test}")
            custom = ask("Index a directory? Enter path or press Enter to skip:")
            if custom:
                if Path(custom).is_dir():
                    index_path = custom
                else:
                    warn(f"Directory '{custom}' does not exist — skipping.")

        if index_path:
            # Ask about vectors
            info("Semantic search (kNN) requires downloading a model (~400 MB).")
            vector_flag = [] if ask("Enable semantic vectors? [y/N]:").lower() in ('y', 'yes') \
                          else ['--no-vectors']

            # Ask about reset
            if args.reset:
                reset_flag = ['--reset']
                warn("--reset flag passed — index will be wiped.")
            else:
                doc_count = _es_doc_count()
                reset_flag = ['--reset']
                if doc_count > 0:
                    warn(f"Index already contains {doc_count} document(s).")
                    if ask("Delete and re-index from scratch? [Y/n]:").lower() in ('n', 'no'):
                        reset_flag = []

            # Ask about file extensions
            if not args.ext:
                info("Supported: pdf md txt html srt vtt docx xlsx csv json xml py js ts css sh")
                ext_input = ask("Filter by file type? (comma-separated, or Enter for all):")
                ext_flag  = ['--ext', ext_input] if ext_input else []
            else:
                ext_flag = ['--ext', args.ext]
        else:
            info("Skipping indexing. Run later with:")
            info("  python backend/search_indexer.py --path /your/path --reset")

    if index_path:
        indexer = BACKEND_DIR / 'search_indexer.py'
        if not indexer.exists():
            warn("search_indexer.py not found — skipping indexing")
        else:
            info(f"Indexing: {index_path} …")
            cmd = [sys.executable, str(indexer), '--path', index_path]
            cmd += reset_flag + vector_flag + ext_flag
            subprocess.run(cmd, check=True)
            ok("Indexing complete")
            _update_env_path(index_path)
    else:
        info("No indexing — existing index will be used as-is.")

    # ── Step 5: Flask backend ─────────────────────────────────────────────────
    header("Step 5/6 — Flask backend")

    _kill_port(5000)
    time.sleep(1)

    # Determine search path for the backend
    backend_path = index_path
    if not backend_path:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location('env', BACKEND_DIR / 'env.py')
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            backend_path = getattr(mod, 'SEARCH_BASE_PATH', str(ROOT_DIR))
        except Exception:
            backend_path = str(ROOT_DIR)

    server = BACKEND_DIR / 'catalog_server.py'
    log_file = Path(
        os.environ.get('TEMP', '/tmp') if sys.platform == 'win32' else '/tmp'
    ) / 'catalog_server_dev.log'

    info("Starting backend/catalog_server.py on port 5000 …")
    info(f"  search paths: {backend_path}")
    info(f"  log: {log_file}")

    with open(log_file, 'w') as log_f:
        proc = subprocess.Popen(
            [sys.executable, str(server), '--path', backend_path],
            stdout=log_f, stderr=log_f)

    def _cleanup():
        if proc.poll() is None:
            proc.terminate()
            info(f"Backend stopped (PID {proc.pid})")

    atexit.register(_cleanup)

    if not _wait_for_backend():
        warn(f"Backend may still be starting. Check: {log_file}")

    # ── Step 5.5: Ollama ──────────────────────────────────────────────────────
    header("Step 5.5/6 — Ollama (optional)")
    _check_ollama()

    # ── Step 6: ES status + open browser ─────────────────────────────────────
    header("Step 6/6 — Index status and test console")

    es_status_script = BACKEND_DIR / 'search_status.py'
    if es_status_script.exists():
        subprocess.run([sys.executable, str(es_status_script),
                        '--es-url', ES_URL, '--index', INDEX_NAME])

    doc_total = _es_doc_count()

    sep()
    print(f"\n  {C['BOLD']}All set:{C['RESET']}\n")
    print(f"  Elasticsearch  →  {C['CYAN']}{ES_URL}{C['RESET']}  (documents: {doc_total})")
    print(f"  Backend API    →  {C['CYAN']}{API_URL}{C['RESET']}")
    print(f"  Test console   →  {C['CYAN']}file://{ES_TEST_HTML}{C['RESET']}")
    print()
    print(f"  {C['DIM']}Backend log  : {log_file}{C['RESET']}")
    print(f"  {C['DIM']}Stop ES      : docker compose -f \"{COMPOSE_FILE}\" down{C['RESET']}")
    print(f"  {C['DIM']}Stop backend : kill {proc.pid}{C['RESET']}")
    print()
    sep()
    print()

    _open_browser(str(ES_TEST_HTML))
    ok("Test console opened!")
    print()
    info("Press Ctrl+C to stop the backend and exit.")
    print()

    try:
        proc.wait()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
