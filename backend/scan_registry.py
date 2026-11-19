"""
scan_registry.py — persistent list of the folders that have been scanned / indexed.

Stored as JSON (data/scan_registry.json, git-ignored) so the Tools UI can show the
folders again after a reload or a server restart, re-scan one with a single click, move
it to a new location, or add new ones.

Entry:
    id            stable 8-char id (survives a path change)
    path          absolute folder (as stored in the index: str(Path(..).resolve()))
    added_at / last_scan_at   ISO-8601 UTC
    last_status   'done' | 'error' | 'running' | ''
    last_task     id of the last background task (for progress / log)
    last_files    files in the last scan (from the "[n/total]" progress)
    ext_filter / no_vectors / batch / canonical_lang    options reused by "refresh"
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_OPTION_KEYS = ('ext_filter', 'no_vectors', 'batch', 'canonical_lang')


def norm(path: str) -> str:
    """Canonical string used to compare paths (case-insensitive on Windows)."""
    return os.path.normcase(str(path).strip().rstrip('\\/'))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Registry:
    def __init__(self, file: Path):
        self.file = Path(file)
        self._lock = threading.RLock()
        self._items: list = []
        self._dismissed: list = []      # normalised paths the user removed — never re-imported from `seed`
        self._loaded = False

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.file.read_text(encoding='utf-8'))
            self._items = data.get('folders', [])
            self._dismissed = data.get('dismissed', [])
        except (OSError, ValueError):
            self._items, self._dismissed = [], []

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix('.tmp')
        tmp.write_text(json.dumps({'folders': self._items, 'dismissed': self._dismissed},
                                  indent=2, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.file)                       # atomic: never leaves a half-written file

    # ── queries ──────────────────────────────────────────────────────────────
    def list(self, seed: Optional[list] = None) -> list:
        """All entries. `seed` (the configured base paths = folders scanned before the registry
        existed) is imported whenever an entry is missing — unless the user removed it."""
        with self._lock:
            self._load()
            changed = False
            for p in seed or []:
                n = norm(p)
                if n not in self._dismissed and not any(norm(x['path']) == n for x in self._items):
                    self._add_locked(p)
                    changed = True
            if changed:
                self._save()
            return [dict(e) for e in self._items]

    def get(self, entry_id: str) -> Optional[dict]:
        with self._lock:
            self._load()
            e = next((x for x in self._items if x['id'] == entry_id), None)
            return dict(e) if e else None

    def find(self, path: str) -> Optional[dict]:
        with self._lock:
            self._load()
            n = norm(path)
            e = next((x for x in self._items if norm(x['path']) == n), None)
            return dict(e) if e else None

    # ── mutations ────────────────────────────────────────────────────────────
    def _add_locked(self, path: str, **opts) -> dict:
        n = norm(path)
        existing = next((x for x in self._items if norm(x['path']) == n), None)
        if existing:
            existing.update({k: v for k, v in opts.items() if k in _OPTION_KEYS and v is not None})
            return existing
        entry = {'id': uuid.uuid4().hex[:8], 'path': str(path).strip().rstrip('\\/') or str(path),
                 'added_at': _now(), 'last_scan_at': '', 'last_status': '', 'last_task': '',
                 'last_files': 0, 'ext_filter': '', 'no_vectors': False, 'batch': 50, 'canonical_lang': ''}
        entry.update({k: v for k, v in opts.items() if k in _OPTION_KEYS and v is not None})
        self._items.append(entry)
        self._dismissed = [d for d in self._dismissed if d != n]    # explicitly (re-)added → no longer dismissed
        return entry

    def upsert(self, path: str, **opts) -> dict:
        with self._lock:
            self._load()
            e = self._add_locked(path, **opts)
            self._save()
            return dict(e)

    def update(self, entry_id: str, **fields) -> Optional[dict]:
        with self._lock:
            self._load()
            e = next((x for x in self._items if x['id'] == entry_id), None)
            if not e:
                return None
            e.update(fields)
            self._save()
            return dict(e)

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            self._load()
            gone = next((x for x in self._items if x['id'] == entry_id), None)
            if not gone:
                return False
            self._items = [x for x in self._items if x['id'] != entry_id]
            n = norm(gone['path'])
            if n not in self._dismissed:
                self._dismissed.append(n)        # it may still be in SEARCH_BASE_PATH: don't re-import it
            self._save()
            return True
