#!/usr/bin/env python3
"""
Translation Worker for Course Catalog.

Reads translation tasks from a JSONL queue file and translates text to the
canonical language using Ollama. Updates Elasticsearch documents on success.

Queue item format (one JSON per line):
    {
        "task_id":    str,        # UUID
        "es_doc_id":  str,        # ES _id of the document to update
        "path":       str,        # file path (for reference)
        "chunk_index": int,       # chunk index within file
        "source_lang": str,       # detected source language (e.g. "de")
        "target_lang": str,       # target language (e.g. "en")
        "text":       str,        # text to translate
        "status":     "pending" | "done" | "failed",
        "attempts":   int,        # number of attempts made
        "created_at": str,        # ISO 8601 timestamp
    }

Usage:
    # Process queue once and exit
    python backend/ai_translation.py --once

    # Run continuously, polling every 5 seconds
    python backend/ai_translation.py

    # Custom options
    python backend/ai_translation.py --es-url http://127.0.0.1:9200 --model translategemma:4b --once
"""

import os
import sys
import json
import time
import logging
import argparse
import threading
import tempfile
import uuid
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

try:
    import env as _env
    _ES_URL_DEFAULT            = getattr(_env, 'ES_URL',             os.environ.get('ES_URL',             'http://127.0.0.1:9200'))
    _ES_INDEX_DEFAULT          = getattr(_env, 'ES_INDEX',           os.environ.get('ES_INDEX',           'course_catalog'))
    _OLLAMA_URL_DEFAULT        = getattr(_env, 'OLLAMA_URL',         os.environ.get('OLLAMA_URL',         'http://127.0.0.1:11434'))
    _TRANSLATION_MODEL_DEFAULT = getattr(_env, 'TRANSLATION_MODEL',  os.environ.get('TRANSLATION_MODEL',  'translategemma:4b'))
    _CANONICAL_LANGUAGE_DEFAULT = getattr(_env, 'CANONICAL_LANGUAGE', os.environ.get('CANONICAL_LANGUAGE', ''))
except ImportError:
    _ES_URL_DEFAULT            = os.environ.get('ES_URL',             'http://127.0.0.1:9200')
    _ES_INDEX_DEFAULT          = os.environ.get('ES_INDEX',           'course_catalog')
    _OLLAMA_URL_DEFAULT        = os.environ.get('OLLAMA_URL',         'http://127.0.0.1:11434')
    _TRANSLATION_MODEL_DEFAULT = os.environ.get('TRANSLATION_MODEL',  'translategemma:4b')
    _CANONICAL_LANGUAGE_DEFAULT = os.environ.get('CANONICAL_LANGUAGE', '')

# Language code → human-readable name (for prompt)
_LANG_NAMES = {
    'en': 'English',
    'es': 'Spanish',
    'de': 'German',
    'fr': 'French',
    'it': 'Italian',
    'pt': 'Portuguese',
}

_QUEUE_FILE_DEFAULT  = Path(__file__).parent / 'translation_queue.jsonl'
_STATUS_FILE_DEFAULT = Path(__file__).parent / 'translation_status.json'

# Maximum translation attempts before marking as failed
MAX_ATTEMPTS = 3

# Polling interval when running continuously (seconds)
POLL_INTERVAL = 5

# Number of parallel workers
try:
    import os as _os
    _cpu = _os.cpu_count() or 2
except Exception:
    _cpu = 2
WORKER_THREADS = max(1, _cpu // 2)


# ─── Queue I/O ────────────────────────────────────────────────────────────────

def read_queue(queue_file: Path) -> list:
    """Read all tasks from queue file. Returns list of task dicts."""
    if not queue_file.exists():
        return []
    tasks = []
    try:
        with open(queue_file, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tasks.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning(f"Skipping malformed queue line: {line[:80]}")
    except Exception as e:
        log.error(f"Failed to read queue file: {e}")
    return tasks


def write_queue_atomic(queue_file: Path, tasks: list):
    """Rewrite the entire queue file atomically (write to tmp, then rename)."""
    try:
        dir_path = queue_file.parent
        dir_path.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp', prefix='tqueue_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                for task in tasks:
                    f.write(json.dumps(task, ensure_ascii=False) + '\n')
            os.replace(tmp_path, queue_file)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        log.error(f"Failed to write queue file: {e}")
        raise


def write_status(status_file: Path, tasks: list, worker_running: bool):
    """Write summary status to translation_status.json."""
    total   = len(tasks)
    pending = sum(1 for t in tasks if t.get('status') == 'pending')
    done    = sum(1 for t in tasks if t.get('status') == 'done')
    failed  = sum(1 for t in tasks if t.get('status') == 'failed')
    data = {
        'total':          total,
        'pending':        pending,
        'done':           done,
        'failed':         failed,
        'worker_running': worker_running,
        'last_updated':   datetime.now(timezone.utc).isoformat(),
    }
    try:
        dir_path = status_file.parent
        dir_path.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp', prefix='tstatus_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write('\n')
            os.replace(tmp_path, status_file)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        log.warning(f"Failed to write status file: {e}")


# ─── Translation ──────────────────────────────────────────────────────────────

def translate_text(text: str, target_lang: str, ollama_url: str, model: str) -> str:
    """Translate text to target_lang using Ollama /api/generate.

    Returns the translated string.
    Raises on failure (caller handles retries).
    """
    import urllib.request
    import urllib.error

    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    prompt = (
        f"Translate the following text to {lang_name}. "
        f"Output ONLY the translation, no explanations:\n\n{text}"
    )

    payload = {
        "model":  model,
        "prompt": prompt,
        "stream": False,
    }
    data = json.dumps(payload).encode('utf-8')

    url = ollama_url.rstrip('/') + '/api/generate'
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode('utf-8')
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama request failed: {e}") from e

    try:
        result = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from Ollama: {body[:200]}") from e

    translation = result.get('response', '').strip()
    if not translation:
        raise RuntimeError(f"Empty translation response from Ollama. Full response: {result}")

    return translation


# ─── ES update ───────────────────────────────────────────────────────────────

def update_es_doc(es_url: str, index: str, doc_id: str, fields: dict):
    """Update an ES document with the given fields."""
    import urllib.request
    import urllib.error

    url = f"{es_url.rstrip('/')}/{index}/_update/{doc_id}"
    payload = {"doc": fields}
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()  # consume response
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"ES update HTTP {e.code}: {body[:200]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"ES update failed: {e}") from e


# ─── Worker ───────────────────────────────────────────────────────────────────

class TranslationWorker:
    """Manages the translation queue and worker threads.

    Usage:
        worker = TranslationWorker(es_url, queue_file, index, model, ollama_url, canonical_lang)
        worker.start()          # starts as daemon thread
        status = worker.get_status()
    """

    def __init__(
        self,
        es_url:         str  = _ES_URL_DEFAULT,
        queue_file:     Path = _QUEUE_FILE_DEFAULT,
        index:          str  = _ES_INDEX_DEFAULT,
        model:          str  = _TRANSLATION_MODEL_DEFAULT,
        ollama_url:     str  = _OLLAMA_URL_DEFAULT,
        canonical_lang: str  = _CANONICAL_LANGUAGE_DEFAULT,
        status_file:    Path = _STATUS_FILE_DEFAULT,
        once:           bool = False,
    ):
        self.es_url         = es_url
        self.queue_file     = Path(queue_file)
        self.index          = index
        self.model          = model
        self.ollama_url     = ollama_url
        self.canonical_lang = canonical_lang
        self.status_file    = Path(status_file)
        self.once           = once

        self._lock          = threading.Lock()
        self._running       = False
        self._thread: Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> threading.Thread:
        """Start the worker as a daemon background thread."""
        t = threading.Thread(target=self._run, daemon=True, name='TranslationWorker')
        t.start()
        self._thread = t
        return t

    def get_status(self) -> dict:
        """Return current status dict (reads from status file or queue)."""
        if self.status_file.exists():
            try:
                with open(self.status_file, encoding='utf-8') as f:
                    data = json.load(f)
                data['worker_running'] = self._running
                return data
            except Exception:
                pass
        # Fallback: count from queue
        tasks = read_queue(self.queue_file)
        return {
            'total':          len(tasks),
            'pending':        sum(1 for t in tasks if t.get('status') == 'pending'),
            'done':           sum(1 for t in tasks if t.get('status') == 'done'),
            'failed':         sum(1 for t in tasks if t.get('status') == 'failed'),
            'worker_running': self._running,
            'last_updated':   None,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self):
        """Main loop: process queue once or continuously."""
        self._running = True
        log.info(f"TranslationWorker started (model={self.model}, "
                 f"target={self.canonical_lang}, threads={WORKER_THREADS})")
        try:
            if self.once:
                self._process_once()
            else:
                while True:
                    self._process_once()
                    time.sleep(POLL_INTERVAL)
        finally:
            self._running = False
            # Update status file to reflect worker stopped
            try:
                tasks = read_queue(self.queue_file)
                write_status(self.status_file, tasks, False)
            except Exception:
                pass
            log.info("TranslationWorker stopped.")

    def _process_once(self):
        """Read queue, process all pending tasks, rewrite queue atomically."""
        with self._lock:
            tasks = read_queue(self.queue_file)

        # Filter tasks eligible for processing:
        # - status == "pending"
        # - target_lang matches canonical_lang (if set)
        # - attempts < MAX_ATTEMPTS
        pending = [
            t for t in tasks
            if t.get('status') == 'pending'
            and (not self.canonical_lang or t.get('target_lang') == self.canonical_lang)
            and t.get('attempts', 0) < MAX_ATTEMPTS
        ]

        if not pending:
            # Still write status even if nothing to do
            write_status(self.status_file, tasks, self._running)
            return

        log.info(f"Processing {len(pending)} pending translation task(s)…")

        # Map task_id → index in tasks list for fast update
        id_to_idx = {t['task_id']: i for i, t in enumerate(tasks) if 'task_id' in t}

        # Process in parallel
        results: dict = {}  # task_id → {'translated': str} or {'error': str}

        with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
            future_to_task = {
                executor.submit(self._translate_task, task): task
                for task in pending
            }
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    translated = future.result()
                    results[task['task_id']] = {'translated': translated}
                except Exception as e:
                    results[task['task_id']] = {'error': str(e)}

        # Update task statuses in-memory
        with self._lock:
            # Re-read queue in case it changed while we were processing
            tasks = read_queue(self.queue_file)
            id_to_idx = {t['task_id']: i for i, t in enumerate(tasks) if 'task_id' in t}

            for task in pending:
                tid = task['task_id']
                idx = id_to_idx.get(tid)
                if idx is None:
                    continue  # task was removed from queue externally

                result = results.get(tid, {})
                if 'translated' in result:
                    tasks[idx]['status']   = 'done'
                    tasks[idx]['attempts'] = tasks[idx].get('attempts', 0) + 1
                    # Update ES doc
                    self._update_es(task, result['translated'])
                else:
                    attempts = tasks[idx].get('attempts', 0) + 1
                    tasks[idx]['attempts'] = attempts
                    if attempts >= MAX_ATTEMPTS:
                        tasks[idx]['status'] = 'failed'
                        log.warning(f"Task {tid} failed after {attempts} attempt(s): "
                                    f"{result.get('error', 'unknown error')}")
                        # Mark ES doc as failed
                        self._mark_es_failed(task)
                    else:
                        log.info(f"Task {tid} attempt {attempts} failed, will retry: "
                                 f"{result.get('error', 'unknown error')}")

            # Write updated queue atomically
            write_queue_atomic(self.queue_file, tasks)
            write_status(self.status_file, tasks, self._running)

    def _translate_task(self, task: dict) -> str:
        """Translate a single task with exponential backoff on failure.

        Returns translated text on success, raises on all retries failed.
        """
        attempts = task.get('attempts', 0)
        text        = task['text']
        target_lang = task['target_lang']

        # Exponential backoff based on prior attempts
        if attempts > 0:
            wait = min(2 ** attempts, 60)
            log.debug(f"Waiting {wait}s before retry (attempt {attempts + 1})…")
            time.sleep(wait)

        try:
            return translate_text(text, target_lang, self.ollama_url, self.model)
        except Exception as e:
            raise RuntimeError(f"Translation error: {e}") from e

    def _update_es(self, task: dict, translated: str):
        """Update ES document with translated content."""
        doc_id = task.get('es_doc_id')
        if not doc_id or not self.es_url:
            return
        try:
            update_es_doc(
                self.es_url,
                self.index,
                doc_id,
                {
                    'canonical_content':  translated,
                    'translation_status': 'done',
                },
            )
            log.debug(f"ES updated for doc {doc_id} (chunk {task.get('chunk_index')})")
        except Exception as e:
            log.warning(f"ES update failed for doc {doc_id}: {e}")

    def _mark_es_failed(self, task: dict):
        """Mark ES document translation as failed."""
        doc_id = task.get('es_doc_id')
        if not doc_id or not self.es_url:
            return
        try:
            update_es_doc(
                self.es_url,
                self.index,
                doc_id,
                {'translation_status': 'failed'},
            )
        except Exception as e:
            log.warning(f"ES failed-mark update failed for doc {doc_id}: {e}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Process translation queue and update Elasticsearch.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--es-url',
        default=_ES_URL_DEFAULT,
        help='Elasticsearch URL',
    )
    parser.add_argument(
        '--queue-file',
        default=str(_QUEUE_FILE_DEFAULT),
        help='Path to translation_queue.jsonl',
    )
    parser.add_argument(
        '--model',
        default=_TRANSLATION_MODEL_DEFAULT,
        help='Ollama model name for translation',
    )
    parser.add_argument(
        '--index',
        default=_ES_INDEX_DEFAULT,
        help='Elasticsearch index name',
    )
    parser.add_argument(
        '--ollama-url',
        default=_OLLAMA_URL_DEFAULT,
        help='Ollama base URL',
    )
    parser.add_argument(
        '--canonical-lang',
        default=_CANONICAL_LANGUAGE_DEFAULT,
        help='Target language (canonical). Empty = process all pending tasks.',
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Process queue once and exit (default: run continuously)',
    )
    args = parser.parse_args()

    worker = TranslationWorker(
        es_url         = args.es_url,
        queue_file     = Path(args.queue_file),
        index          = args.index,
        model          = args.model,
        ollama_url     = args.ollama_url,
        canonical_lang = args.canonical_lang,
        once           = args.once,
    )

    if args.once:
        # Run synchronously in the main thread
        log.info("Running translation worker (once)…")
        worker._running = True
        try:
            worker._process_once()
        finally:
            worker._running = False
        status = worker.get_status()
        log.info(
            f"Done. total={status['total']} done={status['done']} "
            f"pending={status['pending']} failed={status['failed']}"
        )
    else:
        # Run in background thread; block main until KeyboardInterrupt
        t = worker.start()
        log.info("Translation worker running. Press Ctrl+C to stop.")
        try:
            while t.is_alive():
                t.join(timeout=1)
        except KeyboardInterrupt:
            log.info("Interrupted. Waiting for current batch to finish…")
            # The daemon thread will be killed when main exits


if __name__ == '__main__':
    main()
