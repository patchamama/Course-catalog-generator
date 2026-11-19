"""
models_info.py — what models / containers the app uses (for the Tools > Models panel).

    ollama_overview()  installed Ollama models + which are loaded right now (/api/ps)
    hf_cache_info()    is a Hugging Face model (embedding, reranker) cached locally, and how big
    docker_overview()  containers on this machine and which one serves Elasticsearch / Ollama

Read-only helpers except `ollama_delete` / `ollama_unload`. No prompts, keys or file
contents are ever read here.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


def human(n: Optional[int]) -> str:
    if n is None:
        return ''
    n = float(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.0f} {unit}' if unit in ('B', 'KB') else f'{n:.1f} {unit}'
        n /= 1024
    return ''


def _http(url: str, method='GET', body: Optional[dict] = None, timeout: float = 5):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else {}


# ─── Ollama ──────────────────────────────────────────────────────────────────

def is_cloud(entry: dict) -> bool:
    name = entry.get('name', '')
    return bool(entry.get('remote_host') or entry.get('remote_model') or re.search(r'[:\-]cloud$', name))


def ollama_overview(base_url: str, entries: list) -> dict:
    """Installed models enriched with size / cloud flag / currently-loaded state."""
    running = {}
    reachable = True
    try:
        for m in _http(base_url.rstrip('/') + '/api/ps', timeout=3).get('models', []):
            running[m.get('name') or m.get('model')] = {
                'vram': m.get('size_vram'), 'size': m.get('size'), 'expires_at': m.get('expires_at')}
    except Exception:
        reachable = bool(entries)               # /api/ps missing on very old servers; tags answered
    rows = []
    for e in entries:
        name = e.get('name', '')
        d = e.get('details') or {}
        run = running.get(name)
        rows.append({
            'name': name, 'cloud': is_cloud(e), 'size': e.get('size'), 'size_h': human(e.get('size')),
            'modified_at': e.get('modified_at', ''), 'family': d.get('family', ''),
            'params': d.get('parameter_size', ''), 'quant': d.get('quantization_level', ''),
            'running': run is not None,
            'vram_h': human(run['vram']) if run and run.get('vram') else '',
            'expires_at': run['expires_at'] if run else '',
            'remote_host': e.get('remote_host', ''),
        })
    rows.sort(key=lambda r: (not r['running'], r['cloud'], r['name'].lower()))
    return {'reachable': reachable, 'models': rows, 'running_count': len(running)}


def ollama_delete(base_url: str, name: str) -> None:
    _http(base_url.rstrip('/') + '/api/delete', method='DELETE', body={'model': name}, timeout=30)


def ollama_unload(base_url: str, name: str) -> None:
    """keep_alive=0 on an empty generate request frees the model from RAM/VRAM immediately."""
    _http(base_url.rstrip('/') + '/api/generate', method='POST',
          body={'model': name, 'keep_alive': 0}, timeout=30)


# ─── Hugging Face cache (embedding / reranker models) ────────────────────────

def hf_cache_info(model_id: str) -> dict:
    """{cached, size_h, path} for a model id like 'intfloat/multilingual-e5-small'."""
    try:
        from huggingface_hub import constants
        root = constants.HF_HUB_CACHE
    except Exception:
        root = os.path.join(os.path.expanduser('~'), '.cache', 'huggingface', 'hub')
    folder = os.path.join(root, 'models--' + model_id.replace('/', '--'))
    if not os.path.isdir(folder):
        return {'cached': False, 'size_h': '', 'path': folder}
    total = 0
    blobs = os.path.join(folder, 'blobs')
    for base, _, files in os.walk(blobs if os.path.isdir(blobs) else folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return {'cached': total > 0, 'size_h': human(total), 'path': folder}


# ─── Docker ──────────────────────────────────────────────────────────────────

_PORT_RE = re.compile(r'(?:(?:\d{1,3}\.){3}\d{1,3}|\[::\]|::):(\d+)->(\d+)/tcp')


def _port_of(url: str, default: int) -> int:
    try:
        return urllib.parse.urlsplit(url).port or default
    except ValueError:
        return default


def docker_overview(es_url: str, ollama_url: str) -> dict:
    """Containers relevant to the app. `serves` says which app service each one provides,
    decided by matching its published host ports with ES_URL / OLLAMA_URL."""
    try:
        r = subprocess.run(['docker', 'ps', '-a', '--format', '{{json .}}'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
    except FileNotFoundError:
        return {'available': False, 'error': 'Docker CLI not found on the backend machine.', 'containers': []}
    except subprocess.TimeoutExpired:
        return {'available': False, 'error': 'Docker did not answer (is Docker Desktop running?).', 'containers': []}
    if r.returncode != 0:
        return {'available': False, 'error': (r.stderr or 'docker ps failed').strip()[:200], 'containers': []}

    es_port, ol_port = _port_of(es_url, 9200), _port_of(ollama_url, 11434)
    rows, others = [], 0
    for line in r.stdout.splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        name, image = c.get('Names', ''), c.get('Image', '')
        host_ports = {int(h) for h, _ in _PORT_RE.findall(c.get('Ports', ''))}
        serves = []
        if es_port in host_ports:
            serves.append('elasticsearch')
        if ol_port in host_ports:
            serves.append('ollama')
        if 'kibana' in image.lower() or 'kibana' in name.lower():
            serves.append('kibana')
        relevant = serves or name.startswith('course-catalog') or re.search(r'ollama|elasticsearch', image + name, re.I)
        if not relevant:
            others += 1
            continue
        running = (c.get('State') or '').lower() == 'running'
        rows.append({'name': name, 'image': image, 'state': c.get('State', ''), 'status': c.get('Status', ''),
                     'running': running, 'ports': c.get('Ports', ''), 'serves': serves,
                     'in_use': running and bool(serves)})
    rows.sort(key=lambda x: (not x['in_use'], x['name']))
    return {'available': True, 'containers': rows, 'other_containers': others,
            'es_port': es_port, 'ollama_port': ol_port}


def docker_volumes(container: str) -> list:
    """Named volumes / bind mounts of a container (where its data really lives)."""
    try:
        r = subprocess.run(['docker', 'inspect', container, '--format', '{{json .Mounts}}'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
        if r.returncode != 0:
            return []
        return [{'type': m.get('Type'), 'name': m.get('Name') or m.get('Source', ''),
                 'dest': m.get('Destination', '')} for m in json.loads(r.stdout or '[]')]
    except Exception:
        return []
