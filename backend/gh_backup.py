"""Store Elasticsearch export archives as GitHub *releases* (one release per backup, the .tgz is the asset).

Why releases: assets may be up to 2 GB (git files: 100 MB), they list newest-first, and deleting a release
leaves the git history untouched.

Safety:
  * the token is only used for the call, never stored or logged (it comes from the request, GITHUB_TOKEN or `gh auth token`);
  * an index contains the TEXT of your documents, so uploading to a PUBLIC repository is refused unless the caller
    passes allow_public=True explicitly.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

API = 'https://api.github.com'
TAG_PREFIX = 'es-backup-'
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


class GhError(Exception):
    """Message is safe to show to the user (never contains the token)."""


def find_token(explicit: str = '') -> tuple:
    """(token, source). Order: explicit > GITHUB_TOKEN / GH_TOKEN > `gh auth token`."""
    if explicit and explicit.strip():
        return explicit.strip(), 'typed'
    for var in ('GITHUB_TOKEN', 'GH_TOKEN'):
        if os.environ.get(var):
            return os.environ[var].strip(), var
    gh = shutil.which('gh')
    if gh:
        try:
            r = subprocess.run([gh, 'auth', 'token'], capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip(), 'gh CLI'
        except (OSError, subprocess.SubprocessError):
            pass
    return '', ''


def clean_repo(repo: str) -> str:
    repo = (repo or '').strip()
    repo = re.sub(r'^(?:https?://)?github\.com/', '', repo).strip('/')
    repo = re.sub(r'\.git$', '', repo)
    if not REPO_RE.match(repo):
        raise GhError('Repository must look like "owner/name" (e.g. my-user/es-backups).')
    return repo


def _headers(token: str, accept: str = 'application/vnd.github+json') -> dict:
    return {'Authorization': f'Bearer {token}', 'Accept': accept, 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'course-catalog-backup'}


def _api(token: str, method: str, path: str, body: Optional[dict] = None, ok404: bool = False):
    url = path if path.startswith('http') else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={**_headers(token), **(
        {'Content-Type': 'application/json'} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 404 and ok404:
            return None
        msg = ''
        try:
            msg = json.loads(e.read().decode('utf-8', 'replace')).get('message', '')
        except Exception:  # noqa: BLE001
            pass
        hint = {401: 'The token is invalid or expired.',
                403: 'The token lacks permission (needs "repo" / contents:write) or the rate limit was hit.',
                404: 'Repository not found, or the token cannot see it.'}.get(e.code, '')
        raise GhError(f'GitHub HTTP {e.code}: {msg or hint}'.strip() + (f' — {hint}' if hint and msg else ''))
    except (urllib.error.URLError, OSError) as e:
        raise GhError(f'Cannot reach GitHub: {e}')


def check(token: str, repo: str) -> dict:
    """Verify the token and the repository. Returns {user, repo, private, can_push}."""
    repo = clean_repo(repo)
    me = _api(token, 'GET', '/user')
    r = _api(token, 'GET', f'/repos/{repo}')
    perms = r.get('permissions') or {}
    return {'user': me.get('login'), 'repo': r.get('full_name', repo), 'private': bool(r.get('private')),
            'can_push': bool(perms.get('push') or perms.get('admin')), 'default_branch': r.get('default_branch')}


def _hsize(n: float) -> str:
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return f'{n:.0f} {u}' if u == 'B' else f'{n:.1f} {u}'
        n /= 1024
    return str(n)


def list_backups(token: str, repo: str) -> list:
    """Backups (releases whose tag starts with es-backup-), newest first."""
    repo = clean_repo(repo)
    out, page = [], 1
    while page <= 5:
        rels = _api(token, 'GET', f'/repos/{repo}/releases?per_page=100&page={page}')
        if not rels:
            break
        for rl in rels:
            if not str(rl.get('tag_name', '')).startswith(TAG_PREFIX):
                continue
            arc = next((a for a in rl.get('assets', []) if a['name'].lower().endswith(('.tgz', '.tar.gz'))), None)
            if not arc:
                continue
            meta = {}
            try:
                meta = json.loads(rl.get('body') or '{}')
            except ValueError:
                pass
            out.append({'release_id': rl['id'], 'tag': rl['tag_name'], 'name': arc['name'], 'asset_id': arc['id'],
                        'size_bytes': arc['size'], 'size': _hsize(arc['size']),
                        'created': rl.get('created_at'), 'docs': meta.get('docs'),
                        'embedding_model': meta.get('embedding_model'), 'os': meta.get('os'),
                        'note': meta.get('note', '')})
        if len(rels) < 100:
            break
        page += 1
    out.sort(key=lambda x: x['created'] or '', reverse=True)
    return out


def upload(token: str, repo: str, archive: str, manifest: Optional[dict] = None, note: str = '',
           allow_public: bool = False, progress: Optional[Callable[[str], None]] = None) -> dict:
    repo = clean_repo(repo)
    info = check(token, repo)
    if not info['can_push']:
        raise GhError(f'This token cannot write to {repo}.')
    if not info['private'] and not allow_public:
        raise GhError(f'{repo} is PUBLIC. An index contains the text of your documents, so uploading it would '
                      'publish them. Use a private repository (recommended) or tick "I understand, upload to a public repo".')
    arc = Path(archive)
    if not arc.is_file():
        raise GhError(f'Archive not found: {arc}')
    size = arc.stat().st_size
    if size > 2 * 1024 ** 3 - 1:
        raise GhError('GitHub release assets are limited to 2 GB; this archive is larger.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    tag = f'{TAG_PREFIX}{stamp}'
    meta = {k: (manifest or {}).get(k) for k in ('docs', 'embedding_model', 'os', 'es_version', 'vector_dims')}
    meta['note'] = note
    rel = _api(token, 'POST', f'/repos/{repo}/releases', {
        'tag_name': tag, 'name': f'ES backup {stamp}', 'body': json.dumps(meta), 'draft': False, 'prerelease': True,
        'target_commitish': info.get('default_branch') or 'main'})
    upload_url = rel['upload_url'].split('{')[0] + '?' + urllib.parse.urlencode({'name': arc.name})
    u = urllib.parse.urlparse(upload_url)
    if progress:
        progress(f'Uploading {arc.name} ({_hsize(size)}) to {repo}…')
    conn = http.client.HTTPSConnection(u.netloc, timeout=3600)
    try:
        with arc.open('rb') as fh:
            conn.request('POST', u.path + '?' + u.query, body=fh,
                         headers={**_headers(token), 'Content-Type': 'application/gzip', 'Content-Length': str(size)})
            resp = conn.getresponse()
            payload = resp.read()
    except OSError as e:
        _api(token, 'DELETE', f'/repos/{repo}/releases/{rel["id"]}', ok404=True)
        raise GhError(f'Upload failed: {e}')
    finally:
        conn.close()
    if resp.status >= 300:
        _api(token, 'DELETE', f'/repos/{repo}/releases/{rel["id"]}', ok404=True)
        _api(token, 'DELETE', f'/repos/{repo}/git/refs/tags/{tag}', ok404=True)
        try:
            msg = json.loads(payload).get('message', '')
        except ValueError:
            msg = ''
        raise GhError(f'Upload failed (HTTP {resp.status}) {msg}')
    return {'release_id': rel['id'], 'tag': tag, 'url': rel.get('html_url'), 'size': _hsize(size)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def download(token: str, repo: str, asset_id: int, dest: Path,
             progress: Optional[Callable[[str], None]] = None) -> Path:
    """Download a release asset. The API redirects to a signed storage URL that must NOT get our token."""
    repo = clean_repo(repo)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(f'{API}/repos/{repo}/releases/assets/{int(asset_id)}',
                                 headers=_headers(token, 'application/octet-stream'))
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=60)
        loc = resp.headers.get('Location') or ''
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get('Location') or ''
        else:
            raise GhError(f'GitHub HTTP {e.code} while downloading the backup.')
    except (urllib.error.URLError, OSError) as e:
        raise GhError(f'Cannot reach GitHub: {e}')
    if not loc:
        raise GhError('GitHub did not return a download location.')
    tmp = dest.with_suffix(dest.suffix + '.part')
    done, last = 0, 0
    try:
        with urllib.request.urlopen(urllib.request.Request(loc, headers={'User-Agent': 'course-catalog-backup'}),
                                    timeout=120) as r, tmp.open('wb') as out:
            total = int(r.headers.get('Content-Length') or 0)
            while True:
                buf = r.read(1024 * 1024)
                if not buf:
                    break
                out.write(buf)
                done += len(buf)
                if progress and done - last >= 20 * 1024 * 1024:
                    last = done
                    progress(f'Downloaded {_hsize(done)}' + (f' of {_hsize(total)}' if total else ''))
        tmp.replace(dest)
    except (urllib.error.URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise GhError(f'Download failed: {e}')
    return dest


def delete(token: str, repo: str, release_id: int) -> None:
    repo = clean_repo(repo)
    rel = _api(token, 'GET', f'/repos/{repo}/releases/{int(release_id)}', ok404=True)
    if rel is None:
        raise GhError('That backup no longer exists.')
    tag = str(rel.get('tag_name', ''))
    if not tag.startswith(TAG_PREFIX):                       # never delete releases that are not ours
        raise GhError('Refusing to delete a release that is not an Elasticsearch backup.')
    _api(token, 'DELETE', f'/repos/{repo}/releases/{int(release_id)}')
    _api(token, 'DELETE', f'/repos/{repo}/git/refs/tags/{urllib.parse.quote(tag)}', ok404=True)
