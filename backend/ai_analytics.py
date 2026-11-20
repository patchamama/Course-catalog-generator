#!/usr/bin/env python3
"""
Analytics Worker for Course Catalog.

Performs per-document auto-summarization via Ollama, corpus-level statistics
from Elasticsearch, and optional named entity extraction using spaCy.

Usage:
    python backend/ai_analytics.py                  # run once: summarize + entities
    python backend/ai_analytics.py --stats-only     # corpus stats only, no LLM calls
    python backend/ai_analytics.py --no-summarize   # skip LLM summarization
    python backend/ai_analytics.py --no-entities    # skip entity extraction

Dependencies:
    - Standard library only (urllib.request for all HTTP calls)
    - spaCy is optional: if installed, enables entity extraction
      (xx_ent_wiki_sm or en_core_web_sm model required)
    - No Flask dependency — runs as a standalone script
"""

import os
import sys
import json
import time
import logging
import argparse
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
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
    _ES_URL_DEFAULT     = getattr(_env, 'ES_URL',         os.environ.get('ES_URL',         'http://127.0.0.1:9200'))
    _ES_INDEX_DEFAULT   = getattr(_env, 'ES_INDEX',       os.environ.get('ES_INDEX',       'course_catalog'))
    _OLLAMA_URL_DEFAULT = getattr(_env, 'OLLAMA_URL',     os.environ.get('OLLAMA_URL',     'http://127.0.0.1:11434'))
except ImportError:
    _ES_URL_DEFAULT     = os.environ.get('ES_URL',         'http://127.0.0.1:9200')
    _ES_INDEX_DEFAULT   = os.environ.get('ES_INDEX',       'course_catalog')
    _OLLAMA_URL_DEFAULT = os.environ.get('OLLAMA_URL',     'http://127.0.0.1:11434')

# Default summarization model (fallback if Ollama does not have the preferred one)
_DEFAULT_SUMMARIZE_MODEL = 'qwen2.5:3b'

_STATUS_FILE_DEFAULT = Path(__file__).parent / 'analytics_status.json'

# Batch size for ES scroll queries
_SCROLL_BATCH = 100

# Summarization prompt (max ~100 words output)
_SUMMARIZE_PROMPT = (
    "Summarize the following document in at most 100 words. "
    "Output ONLY the summary, no preamble or explanations:\n\n{content}"
)


# ─── Helpers: status file ─────────────────────────────────────────────────────

def write_status(status_file: Path, total: int, summarized: int,
                 entities_extracted: int, worker_running: bool):
    """Write analytics_status.json atomically."""
    data = {
        'total':              total,
        'summarized':         summarized,
        'entities_extracted': entities_extracted,
        'last_updated':       datetime.now(timezone.utc).isoformat(),
        'worker_running':     worker_running,
    }
    try:
        dir_path = status_file.parent
        dir_path.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp', prefix='astatus_')
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
        log.warning(f"Failed to write analytics status file: {e}")


def read_status(status_file: Path) -> dict:
    """Read analytics_status.json, returning empty dict on failure."""
    if not status_file.exists():
        return {}
    try:
        with open(status_file, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ─── Helpers: Elasticsearch HTTP (urllib only) ───────────────────────────────

def _es_request(method: str, url: str, body: Optional[dict] = None,
                timeout: int = 30) -> dict:
    """Execute an ES HTTP request. Returns parsed JSON response.

    Raises RuntimeError on HTTP errors or connection failures.
    """
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Content-Type': 'application/json'} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"ES HTTP {e.code} {method} {url}: {raw[:300]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"ES unreachable {url}: {e}") from e


def es_ping(es_url: str) -> bool:
    """Return True if Elasticsearch is reachable."""
    try:
        _es_request('GET', es_url.rstrip('/') + '/', timeout=5)
        return True
    except Exception:
        return False


def es_count(es_url: str, index: str) -> int:
    """Return total document count in the index."""
    url = f"{es_url.rstrip('/')}/{index}/_count"
    resp = _es_request('GET', url)
    return resp.get('count', 0)


def es_search(es_url: str, index: str, body: dict, timeout: int = 30) -> dict:
    """Run an ES search/aggregation query."""
    url = f"{es_url.rstrip('/')}/{index}/_search"
    return _es_request('POST', url, body, timeout=timeout)


def es_update_doc(es_url: str, index: str, doc_id: str, fields: dict):
    """Update an ES document with the given fields."""
    url = f"{es_url.rstrip('/')}/{index}/_update/{doc_id}"
    _es_request('POST', url, {'doc': fields})


def es_scroll_docs(es_url: str, index: str, query: dict,
                   source_fields: list, batch_size: int = _SCROLL_BATCH):
    """Generator: yield ES hits (dicts with _id + _source) matching query.

    Uses search_after for pagination (stateless, no scroll API needed).
    """
    body = {
        'query': query,
        'size':  batch_size,
        '_source': source_fields,
        'sort': [{'_id': 'asc'}],
    }

    while True:
        resp = es_search(es_url, index, body)
        hits = resp.get('hits', {}).get('hits', [])
        if not hits:
            break
        for hit in hits:
            yield hit
        # Use search_after for next page
        last_sort = hits[-1].get('sort')
        if not last_sort or len(hits) < batch_size:
            break
        body['search_after'] = last_sort


# ─── Corpus statistics ────────────────────────────────────────────────────────

def compute_corpus_stats(es_url: str, index: str) -> dict:
    """Compute corpus-level statistics from ES aggregations.

    Returns a dict with:
        doc_count, by_language, avg_doc_length, top_keywords,
        top_file_types, translation_stats
    """
    body = {
        'size': 0,
        'aggs': {
            'by_language': {
                'terms': {'field': 'detected_lang', 'size': 30},
            },
            'avg_content_length': {
                'avg': {'field': 'content_length'},
            },
            'avg_word_count': {
                'avg': {'field': 'word_count'},
            },
            'top_file_types': {
                'terms': {'field': 'file_type', 'size': 20},
            },
            'translation_stats': {
                'terms': {'field': 'translation_status', 'size': 10},
            },
            'top_keywords': {
                'significant_terms': {
                    'field': 'content',
                    'size':  20,
                    'min_doc_count': 2,
                },
            },
        },
    }

    resp = es_search(es_url, index, body, timeout=60)
    aggs = resp.get('aggregations', {})
    total = resp.get('hits', {}).get('total', {})
    if isinstance(total, dict):
        doc_count = total.get('value', 0)
    else:
        doc_count = total or 0

    def _buckets(agg_name: str) -> list:
        buckets = aggs.get(agg_name, {}).get('buckets', [])
        return [{'key': b['key'], 'count': b['doc_count']} for b in buckets]

    avg_len = aggs.get('avg_content_length', {}).get('value')
    avg_wc  = aggs.get('avg_word_count', {}).get('value')

    # Top keywords: significant_terms uses score-based keys
    kw_buckets = aggs.get('top_keywords', {}).get('buckets', [])
    top_keywords = [
        {'term': b['key'], 'score': round(b.get('score', 0), 4),
         'doc_count': b['doc_count']}
        for b in kw_buckets
    ]

    return {
        'doc_count':        doc_count,
        'by_language':      _buckets('by_language'),
        'avg_doc_length':   round(avg_len, 1) if avg_len is not None else None,
        'avg_word_count':   round(avg_wc, 1)  if avg_wc  is not None else None,
        'top_keywords':     top_keywords,
        'top_file_types':   _buckets('top_file_types'),
        'translation_stats': _buckets('translation_stats'),
    }


# ─── Ollama helpers ───────────────────────────────────────────────────────────

def _ollama_list_models(ollama_url: str) -> list:
    """Return list of installed Ollama model names."""
    try:
        url = ollama_url.rstrip('/') + '/api/tags'
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
            return [m['name'] for m in data.get('models', [])]
    except Exception:
        return []


def _pick_model(ollama_url: str, preferred: str) -> Optional[str]:
    """Return preferred model if available, else first available, else None."""
    models = _ollama_list_models(ollama_url)
    if not models:
        return None
    # Exact match first
    for m in models:
        if m == preferred:
            return m
    # Prefix match (e.g. "qwen2.5:3b" matches "qwen2.5:3b-instruct-q4")
    prefix = preferred.split(':')[0]
    for m in models:
        if m.startswith(prefix):
            return m
    # Fall back to first available
    return models[0]


def summarize_text(content: str, ollama_url: str, model: str) -> str:
    """Call Ollama /api/generate to produce a short summary.

    Returns the summary string. Raises RuntimeError on failure.
    """
    # Truncate content to ~3000 chars to keep prompt manageable
    truncated = content[:3000]
    prompt = _SUMMARIZE_PROMPT.format(content=truncated)

    payload = {'model': model, 'prompt': prompt, 'stream': False}
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

    summary = result.get('response', '').strip()
    if not summary:
        raise RuntimeError(f"Empty summary from Ollama. Full response: {result}")
    return summary


# ─── Summarization pass ───────────────────────────────────────────────────────

def run_summarization(es_url: str, index: str, ollama_url: str,
                      model: str, status_file: Path,
                      current_totals: dict) -> int:
    """Summarize all docs in ES that lack a doc_summary field.

    Returns number of documents summarized in this run.
    """
    # Query docs without doc_summary
    query = {
        'bool': {
            'must_not': [{'exists': {'field': 'doc_summary'}}],
        }
    }
    source_fields = ['content', 'path', 'title']

    docs = list(es_scroll_docs(es_url, index, query, source_fields))
    if not docs:
        log.info('Summarization: no documents without doc_summary found.')
        return 0

    log.info(f"Summarization: {len(docs)} document(s) to summarize (model={model})")
    summarized = 0

    for hit in docs:
        doc_id = hit['_id']
        source = hit.get('_source', {})
        content = source.get('content', '')
        path    = source.get('path', doc_id)

        if not content.strip():
            log.debug(f"Skipping empty content doc: {path}")
            continue

        try:
            summary = summarize_text(content, ollama_url, model)
            es_update_doc(es_url, index, doc_id, {'doc_summary': summary})
            summarized += 1
            log.info(f"Summarized [{summarized}/{len(docs)}]: {path}")
        except RuntimeError as e:
            log.warning(f"Failed to summarize {path}: {e}")
            continue

        # Update status periodically
        if summarized % 10 == 0:
            write_status(
                status_file,
                total              = current_totals.get('total', 0),
                summarized         = current_totals.get('summarized', 0) + summarized,
                entities_extracted = current_totals.get('entities_extracted', 0),
                worker_running     = True,
            )

    log.info(f"Summarization complete: {summarized}/{len(docs)} documents summarized.")
    return summarized


# ─── Entity extraction (optional spaCy) ──────────────────────────────────────

def _load_spacy_model():
    """Attempt to load a spaCy NLP model. Returns model or None."""
    try:
        import spacy  # type: ignore
    except ImportError:
        log.info("spaCy not installed — entity extraction skipped.")
        return None

    for model_name in ('xx_ent_wiki_sm', 'en_core_web_sm'):
        try:
            nlp = spacy.load(model_name)
            log.info(f"Loaded spaCy model: {model_name}")
            return nlp
        except OSError:
            continue

    log.warning(
        "spaCy installed but no suitable model found. "
        "Install with: python -m spacy download xx_ent_wiki_sm"
    )
    return None


def run_entity_extraction(es_url: str, index: str, status_file: Path,
                          current_totals: dict) -> int:
    """Extract named entities from docs without the 'entities' field.

    Requires spaCy with a compatible model. Skips gracefully if unavailable.
    Returns number of documents processed.
    """
    nlp = _load_spacy_model()
    if nlp is None:
        return 0

    query = {
        'bool': {
            'must_not': [{'exists': {'field': 'entities'}}],
        }
    }
    source_fields = ['content', 'path']
    docs = list(es_scroll_docs(es_url, index, query, source_fields))

    if not docs:
        log.info('Entity extraction: no documents without entities field found.')
        return 0

    log.info(f"Entity extraction: {len(docs)} document(s) to process.")
    extracted = 0

    for hit in docs:
        doc_id = hit['_id']
        source = hit.get('_source', {})
        content = source.get('content', '')
        path    = source.get('path', doc_id)

        if not content.strip():
            continue

        try:
            # Truncate to avoid very long spaCy processing times
            doc = nlp(content[:10000])
            entities = [
                {'text': ent.text, 'label': ent.label_}
                for ent in doc.ents
                if ent.label_ in ('PERSON', 'ORG', 'LOC', 'GPE')
            ]
            # Deduplicate by (text, label)
            seen = set()
            unique_entities = []
            for e in entities:
                key = (e['text'].lower(), e['label'])
                if key not in seen:
                    seen.add(key)
                    unique_entities.append(e)

            es_update_doc(es_url, index, doc_id, {'entities': unique_entities})
            extracted += 1
            log.info(
                f"Entities [{extracted}/{len(docs)}]: {path} "
                f"({len(unique_entities)} entities)"
            )
        except Exception as e:
            log.warning(f"Entity extraction failed for {path}: {e}")
            continue

        if extracted % 10 == 0:
            write_status(
                status_file,
                total              = current_totals.get('total', 0),
                summarized         = current_totals.get('summarized', 0),
                entities_extracted = current_totals.get('entities_extracted', 0) + extracted,
                worker_running     = True,
            )

    log.info(f"Entity extraction complete: {extracted}/{len(docs)} documents processed.")
    return extracted


# ─── Main worker function ─────────────────────────────────────────────────────

def run_analytics(
    es_url:       str  = _ES_URL_DEFAULT,
    index:        str  = _ES_INDEX_DEFAULT,
    ollama_url:   str  = _OLLAMA_URL_DEFAULT,
    model:        Optional[str] = None,
    stats_only:   bool = False,
    no_summarize: bool = False,
    no_entities:  bool = False,
    status_file:  Path = _STATUS_FILE_DEFAULT,
) -> dict:
    """Run the analytics pipeline.

    Returns a dict with corpus stats and summarization/entity progress.
    """
    log.info(f"Analytics worker started (es={es_url}, index={index})")

    # ── Check ES availability ─────────────────────────────────────────────────
    if not es_ping(es_url):
        log.error(f"Elasticsearch not reachable at {es_url}. Aborting.")
        write_status(status_file, 0, 0, 0, False)
        return {'error': 'ES not available'}

    # ── Read existing status counters ─────────────────────────────────────────
    existing = read_status(status_file)
    current_totals = {
        'total':              existing.get('total', 0),
        'summarized':         existing.get('summarized', 0),
        'entities_extracted': existing.get('entities_extracted', 0),
    }

    try:
        total_docs = es_count(es_url, index)
    except Exception as e:
        log.error(f"Failed to count ES documents: {e}")
        total_docs = 0
    current_totals['total'] = total_docs

    write_status(status_file, total_docs,
                 current_totals['summarized'],
                 current_totals['entities_extracted'],
                 worker_running=True)

    # ── Corpus statistics ─────────────────────────────────────────────────────
    stats = {}
    try:
        log.info("Computing corpus statistics…")
        stats = compute_corpus_stats(es_url, index)
        log.info(
            f"Corpus stats: {stats['doc_count']} docs, "
            f"{len(stats['by_language'])} languages, "
            f"{len(stats['top_keywords'])} keywords"
        )
    except Exception as e:
        log.error(f"Corpus stats computation failed: {e}")
        stats = {'error': str(e)}

    if stats_only:
        write_status(status_file, total_docs,
                     current_totals['summarized'],
                     current_totals['entities_extracted'],
                     worker_running=False)
        log.info("Stats-only mode: done.")
        return stats

    # ── Summarization ─────────────────────────────────────────────────────────
    new_summarized = 0
    if not no_summarize:
        # Resolve model
        resolved_model = model
        if resolved_model is None:
            resolved_model = _pick_model(ollama_url, _DEFAULT_SUMMARIZE_MODEL)
        if resolved_model is None:
            log.warning(
                f"Ollama not available or no models installed at {ollama_url}. "
                "Skipping summarization."
            )
        else:
            try:
                new_summarized = run_summarization(
                    es_url, index, ollama_url, resolved_model,
                    status_file, current_totals,
                )
                current_totals['summarized'] += new_summarized
            except Exception as e:
                log.error(f"Summarization run failed: {e}")

    # ── Entity extraction ─────────────────────────────────────────────────────
    new_entities = 0
    if not no_entities:
        try:
            new_entities = run_entity_extraction(
                es_url, index, status_file, current_totals,
            )
            current_totals['entities_extracted'] += new_entities
        except Exception as e:
            log.error(f"Entity extraction run failed: {e}")

    # ── Final status update ───────────────────────────────────────────────────
    write_status(
        status_file,
        total              = total_docs,
        summarized         = current_totals['summarized'],
        entities_extracted = current_totals['entities_extracted'],
        worker_running     = False,
    )

    result = dict(stats)
    result['summarization_progress'] = {
        'total':      total_docs,
        'summarized': current_totals['summarized'],
        'new_this_run': new_summarized,
    }
    result['entity_progress'] = {
        'total':              total_docs,
        'entities_extracted': current_totals['entities_extracted'],
        'new_this_run':       new_entities,
    }
    result['last_updated'] = datetime.now(timezone.utc).isoformat()

    log.info(
        f"Analytics complete. summarized={current_totals['summarized']} "
        f"entities={current_totals['entities_extracted']}"
    )
    return result


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Analytics worker: summarize docs, extract entities, compute corpus stats.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--es-url',
        default=_ES_URL_DEFAULT,
        help='Elasticsearch base URL',
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
        '--model',
        default=None,
        help=f'Ollama model for summarization (default: auto-detect, prefer {_DEFAULT_SUMMARIZE_MODEL})',
    )
    parser.add_argument(
        '--stats-only',
        action='store_true',
        help='Compute corpus statistics only; skip LLM summarization and entity extraction',
    )
    parser.add_argument(
        '--no-summarize',
        action='store_true',
        help='Skip LLM summarization',
    )
    parser.add_argument(
        '--no-entities',
        action='store_true',
        help='Skip named entity extraction',
    )
    parser.add_argument(
        '--status-file',
        default=str(_STATUS_FILE_DEFAULT),
        help='Path to analytics_status.json',
    )
    args = parser.parse_args()

    result = run_analytics(
        es_url       = args.es_url,
        index        = args.index,
        ollama_url   = args.ollama_url,
        model        = args.model,
        stats_only   = args.stats_only,
        no_summarize = args.no_summarize,
        no_entities  = args.no_entities,
        status_file  = Path(args.status_file),
    )

    if 'error' in result:
        log.error(f"Analytics failed: {result['error']}")
        sys.exit(1)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
