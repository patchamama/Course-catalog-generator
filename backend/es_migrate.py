"""
es_migrate.py — export / import the Elasticsearch data volume (Docker) as ONE archive.

Use case: index quickly on a powerful machine (e.g. an Apple-Silicon Mac with the big embedding
model), export, copy the .tgz to another machine and import it there — no re-indexing.

Why the VOLUME and not the container: the index lives in a Docker named volume
(`deploy_es_data`); `docker commit` / `docker save` would not include it.

    export_index()  stop ES → pack the volume with `tar czf` in a throw-away alpine container →
                    verify (gzip -t + sha256) → write <name>.manifest.json → restart ES
    import_index()  validate the archive → stop ES → (optional safety backup of the current
                    volume) → wipe the volume and unpack → `docker compose up -d` → wait for
                    health → verify the document count
    folder_roots()  the folders the indexed files came from (old paths), to remap to this machine

Each run is a Job with ordered steps (pending / running / ok / failed / skipped) and a log; the web
UI polls it. All docker calls use argument lists (no shell), host paths go through `--mount`
(no `:` ambiguity with Windows drive letters).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ALPINE = 'alpine:3.20'
ES_DATA_DIR = '/usr/share/elasticsearch/data'
ARCHIVE_SUFFIXES = ('.tgz', '.tar.gz')


EXPORT_STEPS = ('Check Docker & volume', 'Read index information', 'Stop Elasticsearch', 'Pack volume (tar.gz)',
                'Verify archive', 'Write manifest', 'Restart Elasticsearch')
IMPORT_STEPS = ('Validate archive', 'Check Docker & volume', 'Stop Elasticsearch', 'Safety backup of current index',
                'Replace volume contents', 'Start Elasticsearch', 'Verify imported index')


class MigrateError(Exception):
    """A step failed; the message is safe to show to the user."""


# ─── Jobs (polled by the UI) ─────────────────────────────────────────────────

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


class Job:
    def __init__(self, kind: str, steps: list):
        self.id = f"{kind}-{uuid.uuid4().hex[:8]}"
        self.kind = kind
        self.status = 'running'            # running | done | failed
        self.steps = [{'name': s, 'status': 'pending', 'detail': ''} for s in steps]
        self.log: list = []
        self.result: dict = {}
        self.error = ''
        self.started = time.time()
        self.finished = 0.0
        with _JOBS_LOCK:
            _JOBS[self.id] = self

    # -- progress API used by the workers
    def say(self, msg: str) -> None:
        self.log.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")
        self.log = self.log[-300:]

    def _step(self, name: str) -> dict:
        return next(s for s in self.steps if s['name'] == name)

    def start(self, name: str, detail: str = '') -> None:
        s = self._step(name); s['status'] = 'running'; s['detail'] = detail; s['t0'] = time.time()
        self.say(f"▶ {name}" + (f" — {detail}" if detail else ''))

    def detail(self, name: str, detail: str) -> None:
        self._step(name)['detail'] = detail

    def ok(self, name: str, detail: str = '') -> None:
        s = self._step(name); s['status'] = 'ok'
        if detail:
            s['detail'] = detail
        s['secs'] = round(time.time() - s.get('t0', time.time()), 1)
        self.say(f"✓ {name}" + (f" — {detail}" if detail else ''))

    def skip(self, name: str, why: str) -> None:
        s = self._step(name); s['status'] = 'skipped'; s['detail'] = why
        self.say(f"– {name} (skipped: {why})")

    def fail(self, name: str, why: str) -> None:
        s = self._step(name); s['status'] = 'failed'; s['detail'] = why
        s['secs'] = round(time.time() - s.get('t0', time.time()), 1)
        self.say(f"✗ {name} — {why}")

    def finish(self, ok: bool, error: str = '') -> None:
        self.status = 'done' if ok else 'failed'
        self.error = error
        self.finished = time.time()

    def to_dict(self) -> dict:
        now = self.finished or time.time()
        return {'id': self.id, 'kind': self.kind, 'status': self.status, 'steps': [
                    {k: v for k, v in s.items() if k != 't0'} for s in self.steps],
                'log': self.log[-60:], 'result': self.result, 'error': self.error,
                'elapsed': int(now - self.started)}


def get_job(job_id: str) -> Optional[Job]:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def running_job() -> Optional[Job]:
    with _JOBS_LOCK:
        return next((j for j in _JOBS.values() if j.status == 'running'), None)


# ─── Docker plumbing ─────────────────────────────────────────────────────────

def _hsize(n: float) -> str:
    """12 B / 35 KB / 1.4 MB / 2.3 GB — small test archives must not read '0 MB'."""
    for unit, div in (('GB', 1 << 30), ('MB', 1 << 20), ('KB', 1 << 10)):
        if n >= div:
            return f"{n / div:.1f} {unit}" if unit != 'KB' else f"{n / div:.0f} KB"
    return f"{int(n)} B"


def _run(args: list, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except FileNotFoundError:
        raise MigrateError(f"'{args[0]}' not found — is Docker installed and on PATH?") from None
    except subprocess.TimeoutExpired:
        raise MigrateError(f"Command timed out after {int(timeout)}s: {' '.join(args[:4])} …") from None
    if check and r.returncode != 0:
        raise MigrateError((r.stderr or r.stdout or f'exit {r.returncode}').strip().splitlines()[-1][:300])
    return r


def docker_ok() -> tuple:
    """(available, message)."""
    try:
        r = subprocess.run(['docker', 'version', '--format', '{{.Server.Version}}'], capture_output=True,
                           text=True, timeout=15)
    except FileNotFoundError:
        return False, 'Docker CLI not found on this machine.'
    except subprocess.TimeoutExpired:
        return False, 'Docker did not answer (is Docker Desktop running?).'
    if r.returncode != 0:
        return False, (r.stderr or 'Docker daemon not reachable').strip().splitlines()[-1][:200]
    return True, r.stdout.strip()


def compose_command() -> Optional[list]:
    """`docker compose` (v2 plugin) or the old `docker-compose` binary."""
    for cmd in (['docker', 'compose'], ['docker-compose']):
        try:
            if subprocess.run(cmd + ['version'], capture_output=True, timeout=15).returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def inspect_container(name: str) -> Optional[dict]:
    r = _run(['docker', 'inspect', name], check=False)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)[0]
    except (ValueError, IndexError):
        return None


def resolve_target(cfg: dict) -> dict:
    """Which container / volume holds the ES data: from the container's mounts if it exists,
    otherwise `<compose project>_es_data` (what `docker compose up` would create)."""
    info = inspect_container(cfg['container'])
    volume = cfg.get('volume') or ''
    if info and not volume:
        for m in info.get('Mounts', []):
            if m.get('Destination') == ES_DATA_DIR and m.get('Type') == 'volume':
                volume = m['Name']
    if not volume:
        volume = f"{cfg.get('project', 'deploy')}_es_data"
    return {'exists': info is not None, 'running': bool(info and info['State']['Running']), 'volume': volume}


def volume_exists(name: str) -> bool:
    return _run(['docker', 'volume', 'inspect', name], check=False).returncode == 0


def _mount(kind: str, source: str, target: str, readonly: bool = False) -> list:
    spec = f"type={kind},source={source},target={target}" + (',readonly' if readonly else '')
    return ['--mount', spec]


def _alpine(mounts: list, shell_cmd: str, timeout: float = 600) -> subprocess.CompletedProcess:
    return _run(['docker', 'run', '--rm', *mounts, ALPINE, 'sh', '-c', shell_cmd], timeout=timeout)


def _wait_es(es_url: str, seconds: int = 240, job: Optional[Job] = None, step: str = '') -> dict:
    """Poll _cluster/health until green/yellow."""
    t0, last = time.time(), ''
    while time.time() - t0 < seconds:
        try:
            with urllib.request.urlopen(es_url.rstrip('/') + '/_cluster/health', timeout=5) as r:
                h = json.loads(r.read())
            if h.get('status') in ('green', 'yellow'):
                return h
            last = f"cluster {h.get('status')}"
        except Exception as e:
            last = type(e).__name__
        if job and step:
            job.detail(step, f"waiting for Elasticsearch… {int(time.time() - t0)}s ({last})")
        time.sleep(3)
    raise MigrateError(f"Elasticsearch did not become healthy within {seconds}s ({last}). "
                       f"Check: docker logs <container>")


def _es_json(es_url: str, path: str, timeout: float = 20):
    with urllib.request.urlopen(es_url.rstrip('/') + path, timeout=timeout) as r:
        return json.loads(r.read())


def _start_es(cfg: dict, job: Job, step: Optional[str] = None) -> None:
    """`docker compose up -d elasticsearch` when a compose file is configured (it also applies
    elasticsearch.yml and recreates a missing container); otherwise `docker start`."""
    cf = cfg.get('compose_file')
    comp = compose_command() if cf and Path(cf).is_file() else None
    if comp:
        if step: job.detail(step, 'docker compose up -d elasticsearch')
        _run(comp + ['-p', cfg.get('project', 'deploy'), '-f', str(cf), 'up', '-d', cfg.get('service', 'elasticsearch')],
             timeout=300)
    else:
        if step: job.detail(step, f"docker start {cfg['container']}")
        _run(['docker', 'start', cfg['container']], timeout=60)


# ─── Index information / folders ─────────────────────────────────────────────

def _all_paths(es_url: str, index: str) -> dict:
    """{path: chunks} for every indexed file (composite aggregation, paged)."""
    out, after, field = {}, None, 'path'
    while True:
        comp = {'size': 2000, 'sources': [{'p': {'terms': {'field': field}}}]}
        if after:
            comp['after'] = after
        body = json.dumps({'size': 0, 'aggs': {'f': {'composite': comp}}}).encode()
        req = urllib.request.Request(f"{es_url.rstrip('/')}/{index}/_search", data=body,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                agg = json.loads(r.read())['aggregations']['f']
        except urllib.error.HTTPError as e:
            if e.code == 400 and field == 'path' and not out:
                field = 'path.keyword'          # index created with dynamic mapping (path is `text`)
                continue
            raise
        for b in agg['buckets']:
            out[b['key']['p']] = b['doc_count']
        after = agg.get('after_key')
        if len(agg['buckets']) < 2000 or not after:
            return out


def _segments(p: str) -> list:
    return [s for s in re.split(r'[\\/]+', p) if s]


def _volume_key(p: str) -> str:
    """Drive letter / UNC share / POSIX root that a path lives on."""
    if p.startswith('\\\\') or p.startswith('//'):
        segs = _segments(p)
        return '\\\\' + '\\'.join(segs[:2]).lower()
    m = re.match(r'^([A-Za-z]:)', p)
    if m:
        return m.group(1).upper()
    return '/'


def _split_path(p: str) -> tuple:
    """(prefix, parent-folder segments): prefix is two backslashes for UNC, '/' for POSIX, '' for drive paths."""
    BS = chr(92)
    pre = BS * 2 if p.startswith((BS * 2, '//')) else ('/' if p.startswith('/') else '')
    return pre, _segments(p)[:-1]


def _common_dir(paths: list) -> str:
    """Longest common FOLDER of a set of file paths, keeping the original separator style."""
    BS = chr(92)
    sep = BS if any(BS in p for p in paths) else '/'
    pre = _split_path(paths[0])[0]
    common = []
    for parts in zip(*[_split_path(p)[1] for p in paths]):
        if len({x.lower() for x in parts}) == 1:
            common.append(parts[0])
        else:
            break
    root = pre + sep.join(common)
    if re.fullmatch(r'[A-Za-z]:', root):
        root += sep
    return root or pre or paths[0]


def folder_roots(es_url: str, index: str, hint_folders: Optional[list] = None) -> list:
    """Folders the indexed files came from — [{'root', 'files', 'chunks', 'exists_here'}].

    `hint_folders` (the scanned folders recorded in the export manifest) give the precise roots;
    without them the roots are the deepest folder shared by all files of each drive / share.
    """
    paths = _all_paths(es_url, index)
    if not paths:
        return []
    roots = []
    # hints come as plain strings or as manifest entries ({'root': ..., 'files': ..., 'chunks': ...})
    hints = [(h.get('root') if isinstance(h, dict) else h) for h in (hint_folders or [])]
    hints = [h for h in hints if h]
    if hints:
        for h in hints:
            sep = '\\' if '\\' in h else '/'
            hn = h.rstrip('\\/')
            files = [p for p in paths if p == hn or p.startswith(hn + sep) or p.lower().startswith((hn + sep).lower())]
            if files:
                roots.append((hn, files))
        covered = {f for _, fs in roots for f in fs}
        rest = [p for p in paths if p not in covered]
    else:
        rest = list(paths)
    groups: dict = {}
    for p in rest:
        groups.setdefault(_volume_key(p), []).append(p)
    for files in groups.values():
        roots.append((_common_dir(files), files))
    out = []
    for root, files in roots:
        out.append({'root': root, 'files': len(files), 'chunks': sum(paths[f] for f in files),
                    'exists_here': os.path.isdir(root)})
    out.sort(key=lambda r: -r['chunks'])
    return out


def collect_info(es_url: str, index: str, extra: dict, hint_folders: Optional[list] = None) -> dict:
    """What goes into the manifest: counts, versions, vector dims, embedding model, source folders."""
    info = {'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'index': index, 'host': platform.node(), 'os': f"{platform.system()} {platform.machine()}"}
    try:
        info['es_version'] = _es_json(es_url, '/')['version']['number']
        info['docs'] = _es_json(es_url, f'/{index}/_count')['count']
        st = _es_json(es_url, f'/{index}/_stats/store')['_all']['total']['store']['size_in_bytes']
        info['index_bytes'] = st
        m = _es_json(es_url, f'/{index}/_mapping')[index]['mappings']['properties']
        info['vector_dims'] = m.get('content_vector', {}).get('dims')
        info['has_title_field'] = 'title' in m
        info['folders'] = folder_roots(es_url, index, hint_folders)
    except Exception as e:
        info['warning'] = f"Could not read the index: {type(e).__name__}: {e}"
    info.update(extra)
    return info


# ─── Export ──────────────────────────────────────────────────────────────────

def _pack(job: Job, step: str, volume: str, out_dir: Path, name: str, timeout: float = 7200) -> int:
    """tar czf the volume into out_dir/name in an alpine container; show bytes written live."""
    target = out_dir / name
    cmd = ['docker', 'run', '--rm', *_mount('volume', volume, '/data', True), *_mount('bind', str(out_dir), '/backup'),
           ALPINE, 'sh', '-c', f"tar czf '/backup/{name}' -C /data ."]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    t0 = time.time()
    while p.poll() is None:
        if time.time() - t0 > timeout:
            p.kill()
            raise MigrateError('Packing timed out.')
        try:
            sz = target.stat().st_size
            job.detail(step, f"{_hsize(sz)} written · {int(time.time() - t0)}s")
        except OSError:
            job.detail(step, f"starting container… {int(time.time() - t0)}s")
        time.sleep(1)
    err = (p.stderr.read() or '').strip()
    if p.returncode != 0:
        raise MigrateError(f"tar failed: {err.splitlines()[-1][:250] if err else p.returncode}")
    return target.stat().st_size


def export_index(job: Job, cfg: dict, out_dir: str, es_url: str, index: str, extra: dict,
                 hint_folders: Optional[list] = None) -> None:
    """Worker (run in a thread). Always tries to restart Elasticsearch, even after a failure."""
    S1, S2, S3, S4, S5, S6, S7 = EXPORT_STEPS
    es_stopped, current = False, S1
    try:
        job.start(S1)
        okd, msg = docker_ok()
        if not okd:
            raise MigrateError(msg)
        tgt = resolve_target(cfg)
        if not volume_exists(tgt['volume']):
            raise MigrateError(f"Docker volume '{tgt['volume']}' not found — nothing to export. "
                               f"(Is the Elasticsearch container created on this machine?)")
        out = Path(out_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        name = f"es_export_{stamp}.tgz"
        job.ok(S1, f"volume {tgt['volume']} · docker {msg}")

        current = S2; job.start(S2)
        manifest = {}
        if tgt['running']:
            manifest = collect_info(es_url, index, extra, hint_folders)
            job.ok(S2, f"{manifest.get('docs', '?')} chunks · {len(manifest.get('folders', []))} source folder(s)")
        else:
            manifest = {'index': index, 'warning': 'Elasticsearch was not running; details unavailable'}
            job.skip(S2, 'Elasticsearch is not running')

        current = S3; job.start(S3, 'consistent copy needs ES stopped')
        if tgt['running']:
            _run(['docker', 'stop', '-t', '120', cfg['container']], timeout=180)
            es_stopped = True
            job.ok(S3)
        else:
            job.skip(S3, 'already stopped')

        current = S4; job.start(S4)
        size = _pack(job, S4, tgt['volume'], out, name)
        job.ok(S4, f"{_hsize(size)} → {out / name}")

        current = S5; job.start(S5, 'gzip -t + sha256')
        r = _alpine(_mount('bind', str(out), '/backup', True), f"gzip -t '/backup/{name}' && sha256sum '/backup/{name}'", timeout=1800)
        sha = r.stdout.split()[0] if r.stdout.strip() else ''
        job.ok(S5, f"intact · sha256 {sha[:16]}…")

        current = S6; job.start(S6)
        manifest.update({'archive': name, 'archive_bytes': size, 'sha256': sha, 'volume': tgt['volume'],
                         'container': cfg['container']})
        (out / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
        job.ok(S6, f"{name}.manifest.json")
        job.result = {'archive': str(out / name), 'manifest': str(out / f"{name}.manifest.json"),
                      'size_bytes': size, 'sha256': sha, 'docs': manifest.get('docs'),
                      'folders': manifest.get('folders', [])}
        ok, err = True, ''
    except Exception as e:
        job.fail(current, str(e))
        ok, err = False, str(e)
    finally:
        # never leave ES stopped because of a failed export
        if es_stopped:
            job.start(S7)
            try:
                _start_es(cfg, job, S7)
                h = _wait_es(es_url, 240, job, S7)
                job.ok(S7, f"cluster {h.get('status')}")
            except Exception as e:
                job.fail(S7, str(e))
                ok, err = False, (err + ' | ' if err else '') + f"Elasticsearch could not be restarted: {e}"
        else:
            job.skip(S7, 'was not stopped')
    job.finish(ok, err)


# ─── Import ──────────────────────────────────────────────────────────────────

def read_manifest(archive: Path) -> Optional[dict]:
    for cand in (archive.with_name(archive.name + '.manifest.json'),
                 archive.with_suffix('').with_suffix('.manifest.json')):
        try:
            return json.loads(cand.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
    return None


def _sha256(path: Path, job: Job, step: str) -> str:
    h, done, total, t0 = hashlib.sha256(), 0, path.stat().st_size, time.time()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
            done += len(chunk)
            job.detail(step, f"checking sha256 {done * 100 // max(total, 1)}% · {int(time.time() - t0)}s")
    return h.hexdigest()


def import_index(job: Job, cfg: dict, archive: str, es_url: str, index: str, exports_dir: str,
                 backup_current: bool = True) -> None:
    S1, S2, S3, S4, S5, S6, S7 = IMPORT_STEPS
    current, replaced = S1, False
    ok, err = False, ''
    try:
        # 1 ── validate (nothing is touched until this passes)
        job.start(S1)
        arc = Path(archive).expanduser()
        if not arc.is_absolute():
            raise MigrateError('Give the full (absolute) path of the .tgz file.')
        if not arc.is_file():
            raise MigrateError(f"File not found: {arc}")
        if not arc.name.lower().endswith(ARCHIVE_SUFFIXES):
            raise MigrateError('Expected a .tgz / .tar.gz archive created by "Export".')
        arc = arc.resolve()
        if not re.fullmatch(r"[\w.\- ()\[\]]+", arc.name):
            raise MigrateError("The file name has unsupported characters. Rename it using only letters, digits, "
                               "spaces and . _ - ( ) [ ] — the name is used inside a container command.")
        manifest = read_manifest(arc)
        if manifest and manifest.get('sha256'):
            got = _sha256(arc, job, S1)
            if got != manifest['sha256']:
                raise MigrateError('Checksum mismatch — the file was corrupted or incompletely copied. Copy it again.')
            sum_note = 'sha256 matches the manifest'
        else:
            sum_note = 'no manifest found — checksum not verified'
        okd, msg = docker_ok()
        if not okd:
            raise MigrateError(msg)
        head = _alpine(_mount('bind', str(arc.parent), '/in', True),
                       f"gzip -t '/in/{arc.name}' && tar tzf '/in/{arc.name}' | head -n 400", timeout=1800).stdout
        # ES 8.x data dir: indices/, _state/, node.lock (+ a `nodes` file); older layouts: nodes/0/…
        if not (re.search(r'(^|\n)(\./)?(indices|nodes)/', head) and re.search(r'(^|\n)(\./)?(_state/|node\.lock|nodes/)', head)):
            raise MigrateError("This archive does not look like an Elasticsearch data volume "
                               "(expected 'indices/' and '_state/' at its root).")
        job.ok(S1, f"{_hsize(arc.stat().st_size)} · {sum_note}"
                   + (f" · {manifest.get('docs', '?')} chunks, ES {manifest.get('es_version', '?')}" if manifest else ''))

        current = S2; job.start(S2)
        tgt = resolve_target(cfg)
        if manifest and manifest.get('es_version'):
            job.say(f"Archive was created with Elasticsearch {manifest['es_version']} "
                    f"({manifest.get('os', '?')}); this machine runs the version pinned in docker-compose.yml.")
        job.ok(S2, f"volume {tgt['volume']} · container {'exists' if tgt['exists'] else 'will be created'}")

        # 2 ── stop
        current = S3; job.start(S3)
        if tgt['running']:
            _run(['docker', 'stop', '-t', '120', cfg['container']], timeout=180)
            job.ok(S3)
        else:
            job.skip(S3, 'not running')

        # 3 ── safety backup (only if there is something to lose)
        current = S4
        has_data = volume_exists(tgt['volume']) and bool(
            _alpine(_mount('volume', tgt['volume'], '/data', True), "ls -A /data | head -n 1").stdout.strip())
        if backup_current and has_data:
            job.start(S4)
            out = Path(exports_dir).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
            bname = f"pre-import_{datetime.now().strftime('%Y%m%d-%H%M%S')}.tgz"
            size = _pack(job, S4, tgt['volume'], out, bname)
            job.ok(S4, f"{_hsize(size)} → {out / bname}")
            job.result['safety_backup'] = str(out / bname)
        else:
            job.skip(S4, 'disabled' if not backup_current else 'current volume is empty')

        # 4 ── replace
        current = S5; job.start(S5, 'wiping the volume and unpacking…')
        if not volume_exists(tgt['volume']):
            _run(['docker', 'volume', 'create', tgt['volume']])
        replaced = True                                  # from here on the old data is gone
        mounts = _mount('volume', tgt['volume'], '/data') + _mount('bind', str(arc.parent), '/in', True)
        cmd = ['docker', 'run', '--rm', *mounts, ALPINE, 'sh', '-c',
               f"find /data -mindepth 1 -delete && tar xzf '/in/{arc.name}' -C /data && chown -R 1000:0 /data"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        t0 = time.time()
        while p.poll() is None:
            job.detail(S5, f"unpacking {_hsize(arc.stat().st_size)}… {int(time.time() - t0)}s")
            time.sleep(1)
        if p.returncode != 0:
            raise MigrateError(f"unpack failed: {(p.stderr.read() or '').strip().splitlines()[-1][:250]}")
        job.ok(S5, f"done in {int(time.time() - t0)}s")

        # 5 ── start
        current = S6; job.start(S6)
        _start_es(cfg, job, S6)
        h = _wait_es(es_url, 300, job, S6)
        job.ok(S6, f"cluster {h.get('status')}")

        # 6 ── verify
        current = S7; job.start(S7)
        count = _es_json(es_url, f'/{index}/_count')['count']
        want = (manifest or {}).get('docs')
        if want is not None and count != want:
            job.say(f"⚠ expected {want} chunks (manifest) but the index has {count}.")
            job.ok(S7, f"{count} chunks (manifest said {want}) — check it")
        else:
            job.ok(S7, f"{count} chunks" + (' — matches the manifest' if want is not None else ''))
        job.result.update({'docs': count, 'manifest': manifest or {}, 'folders': (manifest or {}).get('folders', [])})
        ok = True
    except Exception as e:
        job.fail(current, str(e))
        ok, err = False, str(e)
        if replaced:
            err += ' — the volume was already replaced; restore the safety backup if needed.'
        # best effort: bring ES back up so the app is not left without a search engine
        try:
            if not _es_up(es_url):
                _start_es(cfg, job)
        except Exception:
            pass
    job.finish(ok, err)


def _es_up(es_url: str) -> bool:
    try:
        _es_json(es_url, '/_cluster/health', 5)
        return True
    except Exception:
        return False


# ─── Exports on disk ─────────────────────────────────────────────────────────

def list_exports(directory: str) -> list:
    d = Path(directory).expanduser()
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.name.lower().endswith(ARCHIVE_SUFFIXES):
            m = read_manifest(f) or {}
            out.append({'name': f.name, 'path': str(f), 'size_bytes': f.stat().st_size,
                        'modified': datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec='seconds'),
                        'docs': m.get('docs'), 'es_version': m.get('es_version'), 'os': m.get('os'),
                        'vector_dims': m.get('vector_dims'), 'embedding_model': m.get('embedding_model'),
                        'has_manifest': bool(m)})
    return out
