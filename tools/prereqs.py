#!/usr/bin/env python3
"""
prereqs.py — detect and install the SYSTEM requirements of the app (used by the launcher / start.sh / start.bat).

What the app needs besides the Python packages (those are installed by the launcher into .venv):

  python3-venv   Ubuntu / Debian only — the venv module is a separate package
  docker         runs Elasticsearch (search index); + the compose plugin
  ollama         local LLMs for the chat (optional but part of the default experience)
  node           Node.js 18+ for the Markdown tools            (mdformater, mdImageDownloader, mdtranslator, mdfromhtml)
  pandoc         HTML → Markdown                              (mdfromhtml)
  ffmpeg         merges video + audio / extracts mp3          (YouTube downloader)
  --with-extras: vlc (video player buttons) and tesseract (OCR)

Per platform it uses the system package manager: Homebrew (macOS), apt / dnf / pacman (Linux), winget (Windows).
It never installs a package manager itself (Homebrew / winget must already exist) and it only runs `sudo`
when needed. Every step is logged; a failed optional step is a warning, never a crash.

    python tools/prereqs.py --plan [--platform mac|linux|windows] [--with-extras]     # show, don't install
    python tools/prereqs.py --install [--with-extras]                                  # install what is missing
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
Log = Callable[[str, str], None]


def _default_log(msg: str, level: str = 'info') -> None:
    print(msg, flush=True)


# ─── Platform ────────────────────────────────────────────────────────────────

def current_os() -> str:
    return 'windows' if sys.platform == 'win32' else 'mac' if sys.platform == 'darwin' else 'linux'


def add_known_paths() -> None:
    """Freshly installed tools are not on this process' PATH yet — add their usual folders."""
    home = Path.home()
    if current_os() == 'mac':
        extra = ['/opt/homebrew/bin', '/opt/homebrew/sbin', '/usr/local/bin', '/Applications/Docker.app/Contents/Resources/bin']
    elif current_os() == 'windows':
        pf, la = os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('LOCALAPPDATA', str(home / 'AppData' / 'Local'))
        extra = [rf'{pf}\Docker\Docker\resources\bin', rf'{pf}\nodejs', rf'{la}\Programs\Ollama', rf'{la}\Pandoc',
                 rf'{pf}\Pandoc', rf'{la}\Microsoft\WinGet\Links', rf'{la}\Microsoft\WindowsApps', rf'{pf}\VideoLAN\VLC', rf'{pf}\Tesseract-OCR']
    else:
        extra = ['/usr/local/bin', '/snap/bin']
    parts = os.environ.get('PATH', '').split(os.pathsep)
    os.environ['PATH'] = os.pathsep.join(parts + [p for p in extra if p not in parts and Path(p).is_dir()])


def package_manager(os_name: Optional[str] = None, have: Callable[[str], bool] = lambda c: shutil.which(c) is not None) -> Optional[str]:
    os_name = os_name or current_os()
    if os_name == 'mac':
        return 'brew' if have('brew') else None
    if os_name == 'windows':
        return 'winget' if have('winget') else None
    for pm, binary in (('apt', 'apt-get'), ('dnf', 'dnf'), ('pacman', 'pacman')):
        if have(binary):
            return pm
    return None


def sudo_prefix(os_name: Optional[str] = None) -> list:
    """[] when no elevation is needed (Windows/macOS-brew/root), ['sudo'] on Linux."""
    if (os_name or current_os()) != 'linux':
        return []
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        return []
    return ['sudo']


# ─── What to install, per platform (pure — unit-tested) ──────────────────────

WINGET = ['--silent', '--accept-package-agreements', '--accept-source-agreements']

def _user() -> str:
    """Account that should be allowed to use Docker without sudo (effective after the next login)."""
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get('USER', 'root')


#            component: {pm: [argv, ...]}   (argv lists run in order; ('sh', '-c', …) for vendor scripts)
def install_commands(component: str, os_name: str, pm: Optional[str], sudo: Optional[list] = None) -> list:
    sudo = sudo_prefix(os_name) if sudo is None else sudo
    s = lambda *a: sudo + list(a)                                      # noqa: E731
    table = {
        'python-venv': {'apt': [s('apt-get', 'update'), s('apt-get', 'install', '-y', 'python3-venv', 'python3-pip')]},
        'docker': {
            'brew': [['brew', 'install', '--cask', 'docker']],
            'apt': [s('apt-get', 'update'), s('apt-get', 'install', '-y', 'docker.io'),
                    # compose v2: package name differs between releases — first one that exists wins
                    ['sh', '-c', ' '.join(sudo) + ' apt-get install -y docker-compose-v2 || ' + ' '.join(sudo) + ' apt-get install -y docker-compose-plugin || '
                     + ' '.join(sudo) + ' apt-get install -y docker-compose'],
                    s('systemctl', 'enable', '--now', 'docker'), s('usermod', '-aG', 'docker', _user())],
            'dnf': [s('dnf', 'install', '-y', 'docker', 'docker-compose-plugin'), s('systemctl', 'enable', '--now', 'docker'),
                    s('usermod', '-aG', 'docker', _user())],
            'pacman': [s('pacman', '-S', '--noconfirm', 'docker', 'docker-compose'), s('systemctl', 'enable', '--now', 'docker'),
                       s('usermod', '-aG', 'docker', _user())],
            'winget': [['winget', 'install', '-e', '--id', 'Docker.DockerDesktop'] + WINGET],
        },
        'ollama': {
            'brew': [['brew', 'install', 'ollama']],
            'winget': [['winget', 'install', '-e', '--id', 'Ollama.Ollama'] + WINGET],
            'apt': [['sh', '-c', 'curl -fsSL https://ollama.com/install.sh | sh']],       # the official installer
            'dnf': [['sh', '-c', 'curl -fsSL https://ollama.com/install.sh | sh']],
            'pacman': [s('pacman', '-S', '--noconfirm', 'ollama')],
        },
        'node': {
            'brew': [['brew', 'install', 'node']],
            'apt': [s('apt-get', 'update'), s('apt-get', 'install', '-y', 'nodejs', 'npm')],
            'dnf': [s('dnf', 'install', '-y', 'nodejs', 'npm')],
            'pacman': [s('pacman', '-S', '--noconfirm', 'nodejs', 'npm')],
            'winget': [['winget', 'install', '-e', '--id', 'OpenJS.NodeJS.LTS'] + WINGET],
        },
        'pandoc': {
            'brew': [['brew', 'install', 'pandoc']],
            'apt': [s('apt-get', 'install', '-y', 'pandoc')], 'dnf': [s('dnf', 'install', '-y', 'pandoc')],
            'pacman': [s('pacman', '-S', '--noconfirm', 'pandoc-cli')],
            'winget': [['winget', 'install', '-e', '--id', 'JohnMacFarlane.Pandoc'] + WINGET],
        },
        'ffmpeg': {
            'brew': [['brew', 'install', 'ffmpeg']],
            'apt': [s('apt-get', 'install', '-y', 'ffmpeg')], 'dnf': [s('dnf', 'install', '-y', 'ffmpeg-free')],
            'pacman': [s('pacman', '-S', '--noconfirm', 'ffmpeg')],
            'winget': [['winget', 'install', '-e', '--id', 'Gyan.FFmpeg'] + WINGET],
        },
        'vlc': {
            'brew': [['brew', 'install', '--cask', 'vlc']],
            'apt': [s('apt-get', 'install', '-y', 'vlc')], 'dnf': [s('dnf', 'install', '-y', 'vlc')],
            'pacman': [s('pacman', '-S', '--noconfirm', 'vlc')],
            'winget': [['winget', 'install', '-e', '--id', 'VideoLAN.VLC'] + WINGET],
        },
        'tesseract': {
            'brew': [['brew', 'install', 'tesseract', 'tesseract-lang']],
            'apt': [s('apt-get', 'install', '-y', 'tesseract-ocr', 'tesseract-ocr-deu', 'tesseract-ocr-spa')],
            'dnf': [s('dnf', 'install', '-y', 'tesseract', 'tesseract-langpack-deu', 'tesseract-langpack-spa')],
            'pacman': [s('pacman', '-S', '--noconfirm', 'tesseract', 'tesseract-data-deu', 'tesseract-data-spa')],
            'winget': [['winget', 'install', '-e', '--id', 'UB-Mannheim.TesseractOCR'] + WINGET],
        },
    }
    return table.get(component, {}).get(pm or '', [])


# component → (binary to look for, human label, group)
COMPONENTS = {
    'docker':    ('docker',    'Docker (runs Elasticsearch)', 'core'),
    'ollama':    ('ollama',    'Ollama (local LLMs for the chat)', 'core'),
    'node':      ('node',      'Node.js 18+ (Markdown tools)', 'tools'),
    'pandoc':    ('pandoc',    'pandoc (HTML → Markdown tool)', 'tools'),
    'ffmpeg':    ('ffmpeg',    'ffmpeg (YouTube downloader)', 'tools'),
    'vlc':       ('vlc',       'VLC (video player buttons)', 'extras'),
    'tesseract': ('tesseract', 'Tesseract (OCR)', 'extras'),
}


def is_present(component: str) -> bool:
    binary = COMPONENTS[component][0]
    if shutil.which(binary):
        return True
    if component == 'ollama' and current_os() == 'mac' and Path('/Applications/Ollama.app').exists():
        return True
    if component == 'docker' and current_os() == 'mac' and Path('/Applications/Docker.app').exists():
        return True
    if component == 'vlc' and current_os() == 'mac' and Path('/Applications/VLC.app').exists():
        return True
    return False


def node_ok() -> bool:
    try:
        v = subprocess.run(['node', '--version'], capture_output=True, text=True, timeout=10).stdout.strip().lstrip('v')
        return int(v.split('.')[0]) >= 18
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def wanted(with_extras: bool, os_name: Optional[str] = None) -> list:
    groups = {'core', 'tools'} | ({'extras'} if with_extras else set())
    return [c for c, (_, _, g) in COMPONENTS.items() if g in groups]


# ─── Running commands ────────────────────────────────────────────────────────

def run_stream(argv: list, log: Log, timeout: int = 1800) -> int:
    """Run a command, stream its output into the log, kill it after `timeout` seconds."""
    log('  $ ' + ' '.join(argv if argv[:2] != ['sh', '-c'] else [argv[2]]), 'info')
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding='utf-8', errors='replace', bufsize=1)
    except FileNotFoundError:
        log(f'  ✗ command not found: {argv[0]}', 'error')
        return 127
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    try:
        for line in p.stdout:                       # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                log('    ' + line[:200], 'info')
        return p.wait()
    finally:
        timer.cancel()


def install(component: str, log: Log, os_name: Optional[str] = None) -> bool:
    os_name = os_name or current_os()
    pm = package_manager(os_name)
    label = COMPONENTS[component][1]
    if pm is None:
        hint = {'mac': 'Install Homebrew first: https://brew.sh', 'windows': 'winget (App Installer) is not available — install it from the Microsoft Store',
                'linux': 'no supported package manager (apt / dnf / pacman) found'}[os_name]
        log(f'  ⚠ Cannot install {label}: {hint}', 'warning')
        return False
    cmds = install_commands(component, os_name, pm)
    if not cmds:
        log(f'  ⚠ No automatic install of {label} for {pm}. Install it manually.', 'warning')
        return False
    if os_name == 'linux' and cmds and cmds[0][:1] == ['sudo'] and not _sudo_usable():
        log(f'  ⚠ {label} needs administrator rights (sudo). Run start.sh from a terminal, or install it manually.', 'warning')
        return False
    log(f'⬇ Installing {label} with {pm} …')
    for argv in cmds:
        rc = run_stream(argv, log)
        if rc not in (0, -1978335189):              # winget: -1978335189 = "already installed / no upgrade"
            log(f'  ✗ step failed (exit {rc}): {" ".join(argv[:4])}', 'warning')
            if component != 'docker' or argv[:2] == ['winget', 'install'] or 'install' in argv:
                return False
    add_known_paths()
    return is_present(component)


def _sudo_usable() -> bool:
    if sudo_prefix() == []:
        return True
    if subprocess.run(['sudo', '-n', 'true'], capture_output=True).returncode == 0:
        return True                                  # passwordless / cached credentials
    return sys.stdin is not None and sys.stdin.isatty()   # a terminal can answer the password prompt


# ─── Docker + Elasticsearch + Ollama (start what is installed) ───────────────

def docker_cmd() -> Optional[list]:
    """['docker'] if the daemon answers; ['sudo','docker'] if only root can talk to it (user not in the docker group yet)."""
    for prefix in ([], ['sudo', '-n']):
        if prefix and current_os() != 'linux':
            break
        try:
            if subprocess.run(prefix + ['docker', 'info'], capture_output=True, timeout=25).returncode == 0:
                return prefix + ['docker']
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def start_docker_daemon(log: Log, wait: int = 240) -> Optional[list]:
    """Make the Docker daemon answer: launch Docker Desktop (mac / Windows) or the service (Linux)."""
    cmd = docker_cmd()
    if cmd:
        return cmd
    os_name = current_os()
    log('Docker is installed but not running — starting it …')
    try:
        if os_name == 'mac':
            subprocess.Popen(['open', '-a', 'Docker'])
        elif os_name == 'windows':
            for exe in (Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Docker' / 'Docker' / 'Docker Desktop.exe',):
                if exe.exists():
                    subprocess.Popen([str(exe)], creationflags=0x00000008)      # DETACHED_PROCESS
        else:
            subprocess.run(sudo_prefix() + ['systemctl', 'start', 'docker'], capture_output=True, timeout=60)
    except OSError as e:
        log(f'  ✗ could not start Docker: {e}', 'warning')
    t0 = time.time()
    while time.time() - t0 < wait:
        cmd = docker_cmd()
        if cmd:
            log(f'✓ Docker is running ({int(time.time() - t0)} s)')
            return cmd
        time.sleep(4)
    log(f'⚠ Docker did not become ready within {wait} s. On first launch Docker Desktop asks you to accept its terms — '
        f'open it once, then start this script again.', 'warning')
    return None


def compose_cmd(docker: list) -> Optional[list]:
    for cmd in (docker + ['compose'], ['docker-compose']):
        try:
            if subprocess.run(cmd + ['version'], capture_output=True, timeout=25).returncode == 0:
                return cmd
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def probe(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def ensure_elasticsearch(log: Log, wait: int = 300) -> bool:
    """Elasticsearch answering on :9200 — start the compose service (pulls the image on first run)."""
    if probe('http://127.0.0.1:9200/'):
        return True
    docker = start_docker_daemon(log)
    if not docker:
        return False
    comp = compose_cmd(docker)
    cf = ROOT / 'deploy' / 'docker-compose.yml'
    if not comp or not cf.exists():
        log('⚠ docker compose not found — cannot start Elasticsearch automatically.', 'warning')
        return False
    log('Starting Elasticsearch (the first run downloads ~600 MB) …')
    if run_stream(comp + ['-p', 'deploy', '-f', str(cf), 'up', '-d', 'elasticsearch'], log, timeout=1800) != 0:
        return False
    t0 = time.time()
    while time.time() - t0 < wait:
        if probe('http://127.0.0.1:9200/_cluster/health'):
            log(f'✓ Elasticsearch ready ({int(time.time() - t0)} s)')
            return True
        time.sleep(3)
    log(f'⚠ Elasticsearch did not answer within {wait} s — check: docker logs course-catalog-es', 'warning')
    return False


def ensure_ollama_running(log: Log, wait: int = 30) -> bool:
    if probe('http://127.0.0.1:11434/'):
        return True
    if not is_present('ollama'):
        return False
    log('Starting Ollama …')
    try:
        kw = {'creationflags': 0x00000008} if current_os() == 'windows' else {'start_new_session': True}
        subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    except OSError as e:
        log(f'  ✗ could not start ollama: {e}', 'warning')
        return False
    t0 = time.time()
    while time.time() - t0 < wait:
        if probe('http://127.0.0.1:11434/'):
            return True
        time.sleep(1.5)
    return False


# ─── Orchestration ───────────────────────────────────────────────────────────

def ensure_python_venv(log: Log) -> bool:
    """Debian / Ubuntu ship the venv module separately (python3-venv)."""
    try:
        import ensurepip, venv  # noqa: F401
        return True
    except ImportError:
        pass
    if current_os() != 'linux' or package_manager() != 'apt':
        return False
    log('The Python "venv" module is missing — installing python3-venv …', 'warning')
    for argv in install_commands('python-venv', 'linux', 'apt'):
        if run_stream(argv, log) != 0:
            return False
    return True


def ensure_system(log: Log, with_extras: bool = False, install_missing: bool = True,
                  status: Callable[[str, str, str], None] = lambda *a: None) -> dict:
    """Install what is missing (unless install_missing=False), start Docker / Elasticsearch / Ollama.
    Returns {component: bool}. Never raises — every failure is logged as a warning."""
    add_known_paths()
    result = {}
    for comp in wanted(with_extras):
        ok = is_present(comp) and (comp != 'node' or node_ok())
        if not ok and comp == 'node' and is_present('node'):
            log('⚠ Node.js is older than 18 — the Markdown tools need Node 18+ (https://nodejs.org).', 'warning')
        if not ok and install_missing:
            if comp in ('docker', 'ollama'):
                status('elasticsearch' if comp == 'docker' else 'ollama', 'checking', f'Installing {comp}…')
            ok = install(comp, log)
            if ok and comp == 'node' and not node_ok():
                log('⚠ The installed Node.js is older than 18. Get a current one from https://nodejs.org', 'warning')
        elif not ok:
            log(f'  – {COMPONENTS[comp][1]} not found (automatic install disabled)')
        result[comp] = ok
        if ok:
            log(f'✓ {COMPONENTS[comp][1]}')
    return result


# ─── CLI: --plan / --install ─────────────────────────────────────────────────

def plan(os_name: str, with_extras: bool, have: Callable[[str], bool], assume_pm: bool = False) -> list:
    # when simulating another OS, assume that platform's usual package manager is installed
    pm = {'mac': 'brew', 'linux': 'apt', 'windows': 'winget'}[os_name] if assume_pm else package_manager(os_name, have)
    rows = []
    for comp in wanted(with_extras):
        present = have(COMPONENTS[comp][0])
        cmds = [] if present else install_commands(comp, os_name, pm, sudo=['sudo'] if os_name == 'linux' else [])
        rows.append({'component': comp, 'label': COMPONENTS[comp][1], 'present': present, 'package_manager': pm,
                     'commands': [' '.join(c if c[:2] != ['sh', '-c'] else [c[2]]) for c in cmds]})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Detect / install the system requirements of the app.')
    ap.add_argument('--plan', action='store_true', help='show what is missing and the commands that would run (installs nothing)')
    ap.add_argument('--install', action='store_true', help='install what is missing and start Docker / Elasticsearch / Ollama')
    ap.add_argument('--platform', choices=('mac', 'linux', 'windows'), help='with --plan: pretend to be another OS (every tool "missing")')
    ap.add_argument('--with-extras', action='store_true', help='also VLC and Tesseract')
    a = ap.parse_args(argv)
    if a.plan or not a.install:
        simulated = a.platform and a.platform != current_os()
        add_known_paths()
        have = (lambda c: False) if simulated else (lambda c: shutil.which(c) is not None)
        rows = plan(a.platform or current_os(), a.with_extras, have, assume_pm=bool(simulated))
        pm = rows[0]['package_manager'] if rows else None
        print(f"Platform: {a.platform or current_os()}  |  package manager: {pm or 'NONE FOUND'}" + ('  |  (simulated: all tools shown as missing)' if simulated else ''))
        for r in rows:
            print(f"  [{'x' if r['present'] else ' '}] {r['label']}")
            for c in r['commands']:
                print(f'        $ {c}')
        return 0
    ensure_system(_default_log, a.with_extras)
    ensure_elasticsearch(_default_log)
    ensure_ollama_running(_default_log)
    return 0


if __name__ == '__main__':
    sys.exit(main())
