#!/usr/bin/env python3
"""
compare_models.py — Standalone MiniLM vs mpnet comparison benchmark.

This script:
  1. Creates two temporary ES indices (course_catalog_test_minilm / mpnet)
     with the correct vector dims for each model (384 / 768).
  2. Indexes the test corpus from ~/elo_test_corpus
     into both indices using fastembed directly (no backend restart needed).
  3. Runs all 28 queries from test_queries.json against both indices using
     four methods: BM25 / Semantic (kNN) / Hybrid (BM25+kNN) / Python-backend.
  4. Saves results to tests/results/MiniLM_results.json and mpnet_results.json.
  5. Prints a comparison summary table.

Usage:
    .venv\\Scripts\\python.exe tests/compare_models.py
    .venv\\Scripts\\python.exe tests/compare_models.py --skip-index  # reuse existing temp indices
    .venv\\Scripts\\python.exe tests/compare_models.py --cleanup     # delete temp indices after run
"""

import json
import fnmatch
import os
import re
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import requests
warnings.filterwarnings("ignore")

# ── Configuration ──────────────────────────────────────────────────────────────

# Support both Windows and WSL paths
# Test corpus: override with CORPUS_DIR=…; default is ~/elo_test_corpus (macOS / Linux / Windows)
CORPUS_DIR   = os.environ.get("CORPUS_DIR") or os.path.join(os.path.expanduser("~"), "elo_test_corpus")
QUERIES_FILE = os.path.join(CORPUS_DIR, "test_queries.json")

RESULTS_DIR  = str(Path(__file__).parent / "results")

ES_URL       = "http://127.0.0.1:9200"
BACKEND_URL  = "http://127.0.0.1:5000"

MODELS = {
    "MiniLM": {
        "model_id":    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "dims":        384,
        "index":       "course_catalog_test_minilm",
        "lowercase":   True,
        "multilingual": False,
    },
    "mpnet": {
        "model_id":    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        "dims":        768,
        "index":       "course_catalog_test_mpnet",
        "lowercase":   False,
        "multilingual": False,
    },
    "mE5-small": {
        "model_id":    "intfloat/multilingual-e5-small",
        "dims":        384,
        "index":       "course_catalog_test_me5small",
        "lowercase":   False,
        "multilingual": True,
    },
    "LaBSE": {
        "model_id":    "sentence-transformers/LaBSE",
        "dims":        768,
        "index":       "course_catalog_test_labse",
        "lowercase":   False,
        "multilingual": True,
    },
    "deepset-mxbai-de-large": {
        "model_id":    "mixedbread-ai/deepset-mxbai-embed-de-large-v1",
        "dims":        1024,
        "index":       "course_catalog_test_deepset_mxbai_de_large",
        "lowercase":   False,
        "multilingual": True,
    },
}

CHUNK_SIZE    = 1500
CHUNK_OVERLAP = 200
HTTP_TIMEOUT  = 30

# ── ES index helpers ───────────────────────────────────────────────────────────

def create_test_index(model_name: str) -> str:
    cfg   = MODELS[model_name]
    index = cfg["index"]
    dims  = cfg["dims"]

    # Delete if exists
    requests.delete(f"{ES_URL}/{index}", timeout=10)

    mapping = {
        "settings": {
            "analysis": {
                "analyzer": {
                    "german_custom":  {"type": "german"},
                    "english_custom": {"type": "english"},
                    "spanish_custom": {"type": "spanish"},
                }
            }
        },
        "mappings": {
            "properties": {
                "path":     {"type": "keyword"},
                "filename": {"type": "keyword",
                             "fields": {"text": {"type": "text"}}},
                "ext":      {"type": "keyword"},
                "content":  {
                    "type": "text",
                    "fields": {
                        "de": {"type": "text", "analyzer": "german_custom"},
                        "en": {"type": "text", "analyzer": "english_custom"},
                        "es": {"type": "text", "analyzer": "spanish_custom"},
                    }
                },
                "content_vector": {
                    "type":       "dense_vector",
                    "dims":       dims,
                    "index":      True,
                    "similarity": "cosine",
                },
                "chunk_index": {"type": "integer"},
            }
        }
    }
    r = requests.put(f"{ES_URL}/{index}", json=mapping, timeout=10)
    r.raise_for_status()
    print(f"  Created index '{index}' ({dims} dims)")
    return index


# ── Text reading helpers ───────────────────────────────────────────────────────

def _read_text_file(path: str) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    return ""


def read_corpus_file(path: str) -> str:
    """Return plain text for a corpus file, handling PDF/DOC/DOCX/RTF/SRT."""
    ext = Path(path).suffix.lower()

    if ext in (".txt", ".md", ".html", ".htm", ".srt", ".vtt", ".json"):
        text = _read_text_file(path)
        if ext in (".html", ".htm"):
            # Very basic HTML tag stripping
            text = re.sub(r'<[^>]+>', ' ', text)
        return text

    if ext == ".pdf":
        try:
            import pdfminer.high_level as pdf
            return pdf.extract_text(path) or ""
        except Exception:
            pass
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            return "\n".join(p.extract_text() or "" for p in reader.pages)
        except Exception:
            return ""

    if ext in (".docx", ".doc"):
        try:
            import docx2txt
            return docx2txt.process(path) or ""
        except Exception:
            pass
        try:
            from docx import Document
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception:
            pass
        # Fallback: try plain text (some .doc files are actually plain text)
        text = _read_text_file(path)
        if text and len(text) > 20:
            return text
        return ""

    if ext == ".rtf":
        try:
            from striprtf.striprtf import rtf_to_text
            raw = _read_text_file(path)
            return rtf_to_text(raw) or ""
        except Exception:
            pass
        # Fallback: strip RTF control words
        raw = _read_text_file(path)
        text = re.sub(r'\\[a-z]+\d*\s?', ' ', raw)
        text = re.sub(r'[{}\\]', ' ', text)
        return text

    return _read_text_file(path)


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list:
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    chunks, current, current_len = [], [], 0

    for para in paragraphs:
        para_len = len(para)
        if current_len + para_len > CHUNK_SIZE and current:
            chunks.append('\n\n'.join(current))
            current = current[-1:]
            current_len = len(current[0]) if current else 0
        if para_len > CHUNK_SIZE:
            for i in range(0, para_len, CHUNK_SIZE - CHUNK_OVERLAP):
                sub = para[i:i + CHUNK_SIZE]
                if sub.strip():
                    chunks.append(sub)
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append('\n\n'.join(current))
    return chunks or [text[:CHUNK_SIZE]]


# ── Embedding helper ──────────────────────────────────────────────────────────

# Models that require sentence-transformers (not in fastembed)
_ST_ONLY_MODELS = {
    'intfloat/multilingual-e5-small',
    'sentence-transformers/LaBSE',
    'mixedbread-ai/deepset-mxbai-embed-de-large-v1',
}
# E5-family models require "query: " prefix for queries and "passage: " for docs
_PREFIXED_MODELS = {
    'intfloat/multilingual-e5-small',
    'intfloat/multilingual-e5-large',
    'mixedbread-ai/deepset-mxbai-embed-de-large-v1',
}


def load_embed_fn(model_name: str):
    """Return a function embed(texts, is_query=False) -> list[list[float]].

    Uses fastembed for supported models, sentence-transformers as fallback.
    E5 models automatically prepend 'query:' / 'passage:' prefixes.
    """
    cfg      = MODELS[model_name]
    model_id = cfg["model_id"]
    lowercase = cfg["lowercase"]

    if model_id in _ST_ONLY_MODELS:
        from sentence_transformers import SentenceTransformer
        print(f"    Using sentence-transformers for {model_id}")
        # Force CPU by default: on some WSL/CUDA setups, auto device detection can
        # hang during large model initialization. Override with ST_DEVICE=cuda if
        # a working GPU stack is available.
        st_device = os.environ.get("ST_DEVICE", "cpu")
        st_model  = SentenceTransformer(model_id, device=st_device)
        needs_prefix = model_id in _PREFIXED_MODELS

        def embed(texts: list, is_query: bool = False) -> list:
            if needs_prefix:
                prefix   = "query: " if is_query else "passage: "
                texts    = [prefix + t for t in texts]
            vecs = st_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            return [v.tolist() for v in vecs]
    else:
        from fastembed import TextEmbedding
        fe_model = TextEmbedding(model_id)

        def embed(texts: list, is_query: bool = False) -> list:
            prepared = [t.lower() if lowercase else t for t in texts]
            return [v.tolist() for v in fe_model.embed(prepared)]

    return embed


# ── Indexing ──────────────────────────────────────────────────────────────────

def _bulk_index(index: str, docs: list) -> int:
    if not docs:
        return 0
    bulk_docs = []
    for d in docs:
        bulk_docs.append({"index": {"_index": index}})
        bulk_docs.append(d)
    bulk_body = "\n".join(json.dumps(d, ensure_ascii=False) for d in bulk_docs) + "\n"
    r = requests.post(
        f"{ES_URL}/_bulk",
        data=bulk_body.encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
        timeout=120,
    )
    r.raise_for_status()
    resp = r.json()
    errors = [i for i in resp.get("items", []) if i.get("index", {}).get("error")]
    if errors:
        print(f"    [WARN] {len(errors)} bulk errors")
    return len(docs)


def index_corpus(
    model_name: str,
    embed_fn,
    batch_size: int = 16,
    bulk_size: int = 300,
    file_patterns: list | None = None,
    max_files: int | None = None,
) -> int:
    cfg   = MODELS[model_name]
    index = cfg["index"]

    corpus = Path(CORPUS_DIR)
    files  = [f for f in corpus.iterdir()
              if f.suffix.lower() in
              (".md", ".html", ".htm", ".pdf", ".doc", ".docx", ".rtf", ".srt", ".txt")
              and f.name != "test_queries.json"]
    files = sorted(files)
    if file_patterns:
        files = [f for f in files if any(fnmatch.fnmatch(f.name, pat) for pat in file_patterns)]
    if max_files and max_files > 0:
        files = files[:max_files]

    total_chunks = 0
    pending_docs = []

    for fpath in files:
        t_file0 = time.time()
        text = read_corpus_file(str(fpath))
        if not text.strip():
            print(f"    [SKIP] {fpath.name} (empty)")
            continue

        chunks = chunk_text(text)
        prepared = [c.lower() if cfg["lowercase"] else c for c in chunks]
        vectors = []
        for i in range(0, len(prepared), batch_size):
            vectors.extend(embed_fn(prepared[i:i + batch_size], is_query=False))

        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            pending_docs.append({
                "path":           str(fpath),
                "filename":       fpath.name,
                "ext":            fpath.suffix.lstrip(".").lower(),
                "content":        chunk,
                "content_vector": vec,
                "chunk_index":    i,
            })
            total_chunks += 1

            if len(pending_docs) >= bulk_size:
                _bulk_index(index, pending_docs)
                pending_docs = []

        if pending_docs:
            _bulk_index(index, pending_docs)
            pending_docs = []

        t_file = time.time() - t_file0
        print(f"    {fpath.name}: {len(chunks)} chunks  [{t_file:.1f}s]")

    if pending_docs:
        _bulk_index(index, pending_docs)

    # Refresh
    requests.post(f"{ES_URL}/{index}/_refresh", timeout=10)
    print(f"  Indexed {total_chunks} chunks into '{index}'")
    return total_chunks


# ── Search functions ───────────────────────────────────────────────────────────

def _normalize(path, score, snippet=""):
    return {
        "file":    Path(path).name,
        "path":    path,
        "score":   round(float(score), 4),
        "ext":     Path(path).suffix.lstrip(".").lower(),
        "snippet": snippet[:150],
    }


def bm25_search(query: str, index: str, top_k: int = 5) -> list:
    body = {
        "size": top_k * 3,
        "query": {
            "multi_match": {
                "query":     query,
                "fields":    ["content", "content.de", "content.en", "content.es"],
                "type":      "best_fields",
                "fuzziness": "AUTO",
            }
        },
        "_source": ["path", "filename", "content"],
    }
    try:
        r = requests.post(f"{ES_URL}/{index}/_search", json=body, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        hits = r.json()["hits"]["hits"]
    except Exception as exc:
        print(f"  [WARN] BM25 failed: {exc}")
        return []

    seen = {}
    for hit in hits:
        src  = hit["_source"]
        p    = src.get("path", "")
        if p and p not in seen:
            seen[p] = _normalize(p, hit["_score"], src.get("content", ""))
    return list(seen.values())[:top_k]


def semantic_search(query: str, index: str, embed_fn, top_k: int = 5) -> list:
    vec = embed_fn([query], is_query=True)[0]
    body = {
        "size": top_k * 3,
        "knn": {
            "field":          "content_vector",
            "query_vector":   vec,
            "k":              top_k * 3,
            "num_candidates": 50,
        },
        "_source": ["path", "filename", "content"],
    }
    try:
        r = requests.post(f"{ES_URL}/{index}/_search", json=body, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        hits = r.json()["hits"]["hits"]
    except Exception as exc:
        print(f"  [WARN] Semantic failed: {exc}")
        return []

    seen = {}
    for hit in hits:
        src = hit["_source"]
        p   = src.get("path", "")
        if p and p not in seen:
            seen[p] = _normalize(p, hit["_score"], src.get("content", ""))
    return list(seen.values())[:top_k]


def hybrid_search(query: str, index: str, embed_fn, top_k: int = 5) -> list:
    vec = embed_fn([query], is_query=True)[0]
    body = {
        "size": top_k * 3,
        "query": {
            "multi_match": {
                "query":     query,
                "fields":    ["content", "content.de", "content.en", "content.es"],
                "fuzziness": "AUTO",
            }
        },
        "knn": {
            "field":          "content_vector",
            "query_vector":   vec,
            "k":              top_k * 3,
            "num_candidates": 50,
            "boost":          0.7,
        },
        "_source": ["path", "filename", "content"],
    }
    try:
        r = requests.post(f"{ES_URL}/{index}/_search", json=body, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        hits = r.json()["hits"]["hits"]
    except Exception as exc:
        print(f"  [WARN] Hybrid failed: {exc}")
        return []

    seen = {}
    for hit in hits:
        src = hit["_source"]
        p   = src.get("path", "")
        if p and p not in seen:
            seen[p] = _normalize(p, hit["_score"], src.get("content", ""))
    return list(seen.values())[:top_k]


def python_backend_search(query: str, top_k: int = 5) -> list:
    """Call the running backend /api/search (model-agnostic BM25)."""
    try:
        r = requests.get(
            f"{BACKEND_URL}/api/search",
            params={"q": query, "limit": top_k},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f"  [WARN] /api/search failed: {exc}")
        return []

    results = []
    for item in data.get("results", [])[:top_k]:
        path    = item.get("path", "")
        score   = item.get("score", 0.0)
        matches = item.get("matches", [])
        snippet = matches[0].get("chunk_content", "") if matches else ""
        results.append(_normalize(path, score, snippet))
    return results


# ── Hit detection ──────────────────────────────────────────────────────────────

def find_expected(results: list, expected_files: list) -> tuple:
    if not expected_files:
        return (len(results) == 0, -1)

    tokens = []
    for ef in expected_files:
        bare = ef.split("(")[0].strip().split()[0].strip().lower()
        if bare:
            tokens.append(bare)

    for rank, res in enumerate(results, start=1):
        text = (res.get("file", "") + " " + res.get("path", "")).lower()
        for token in tokens:
            if token and token in text:
                return (True, rank)
    return (False, -1)


# ── Benchmark runner ───────────────────────────────────────────────────────────

def run_benchmark(
    model_name: str,
    embed_fn,
    skip_index: bool = False,
    query_langs: list | None = None,
    batch_size: int = 16,
    bulk_size: int = 300,
    file_patterns: list | None = None,
    max_files: int | None = None,
) -> str:
    cfg   = MODELS[model_name]
    index = cfg["index"]

    if not skip_index:
        print(f"\n  Setting up index for {model_name}...")
        create_test_index(model_name)
        print(f"  Indexing corpus with {model_name}...")
        index_corpus(
            model_name,
            embed_fn,
            batch_size=batch_size,
            bulk_size=bulk_size,
            file_patterns=file_patterns,
            max_files=max_files,
        )

    with open(QUERIES_FILE, encoding="utf-8") as f:
        queries = json.load(f)["queries"]
    if query_langs:
        qset = {q.lower().strip() for q in query_langs}
        queries = [q for q in queries if str(q.get("lang", "")).lower() in qset]

    print(f"\n  Running {len(queries)} queries × 4 methods  [{model_name}]")

    all_records = []
    for idx, q in enumerate(queries, 1):
        qid      = q.get("id", f"Q{idx:02d}")
        text     = q.get("text", "")
        lang     = q.get("lang", "")
        qtype    = q.get("type", "")
        expected = q.get("expected_files", [])

        print(f"  [{idx:2d}/{len(queries)}] {qid} ({lang}/{qtype}): {text[:55]}")

        methods = {
            "BM25":     lambda q: bm25_search(q, index),
            "Semantic": lambda q: semantic_search(q, index, embed_fn),
            "Hybrid":   lambda q: hybrid_search(q, index, embed_fn),
            "Python":   lambda q: python_backend_search(q),
        }

        for method_name, fn in methods.items():
            t0 = time.time()
            try:
                results = fn(text)
            except Exception as exc:
                print(f"       {method_name} ERROR: {exc}")
                results = []
            elapsed = round(time.time() - t0, 3)

            hit, rank = find_expected(results, expected)
            hit_str   = f"rank={rank}" if hit and rank > 0 else ("MATCH(empty)" if hit else "MISS")
            print(f"       {method_name:<10} {hit_str}  ({elapsed:.2f}s)")

            all_records.append({
                "query_id":         qid,
                "query_text":       text,
                "lang":             lang,
                "type":             qtype,
                "method":           method_name,
                "model":            model_name,
                "elapsed_s":        elapsed,
                "results":          results,
                "hit_in_top5":      hit,
                "rank_of_expected": rank,
                "expected_files":   expected,
            })

    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts         = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path   = os.path.join(RESULTS_DIR, f"{model_name}_results_{ts}.json")
    latest     = os.path.join(RESULTS_DIR, f"{model_name}_results.json")
    payload    = {"model": model_name, "timestamp": ts,
                  "query_file": QUERIES_FILE, "records": all_records}

    for p in (out_path, latest):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n  Results: {latest}")
    return latest


# ── Summary ────────────────────────────────────────────────────────────────────

def print_summary(results_file: str) -> dict:
    with open(results_file, encoding="utf-8") as f:
        data = json.load(f)
    records = data["records"]
    model   = data["model"]
    ts      = data["timestamp"]

    methods = sorted(set(r["method"] for r in records))
    langs   = sorted(set(r["lang"]   for r in records))
    types   = sorted(set(r["type"]   for r in records))

    def p5(subset, off=False):
        if off:
            hits  = sum(1 for r in subset if not r["results"])
        else:
            hits  = sum(1 for r in subset if r["hit_in_top5"])
        total = len(subset)
        pct   = 100 * hits / total if total else 0
        return hits, total, pct

    print(f"\n{'='*60}")
    print(f"  {model}  ({ts})")
    print(f"{'='*60}")

    summary = {}
    for m in methods:
        s = [r for r in records if r["method"] == m and r["expected_files"]]
        h, t, pct = p5(s)
        avg_t = sum(r["elapsed_s"] for r in s) / len(s) if s else 0
        avr   = [r["rank_of_expected"] for r in records
                 if r["method"] == m and r["hit_in_top5"] and r["rank_of_expected"] > 0]
        avg_r = sum(avr) / len(avr) if avr else float("nan")

        by_lang = {}
        for lg in langs:
            sl = [r for r in records if r["method"] == m and r["lang"] == lg and r["expected_files"]]
            hl, tl, pl = p5(sl)
            by_lang[lg] = (hl, tl, pl)

        by_type = {}
        for ty in types:
            if ty == "off-topic":
                st = [r for r in records if r["method"] == m and r["type"] == ty]
                ht, tt, pt = p5(st, off=True)
            else:
                st = [r for r in records if r["method"] == m and r["type"] == ty and r["expected_files"]]
                ht, tt, pt = p5(st)
            by_type[ty] = (ht, tt, pt)

        summary[m] = {
            "overall":  (h, t, pct),
            "avg_time": avg_t,
            "avg_rank": avg_r,
            "by_lang":  by_lang,
            "by_type":  by_type,
        }

        print(f"\n  {m}:  P@5={h}/{t}({pct:.1f}%)  avg_time={avg_t:.3f}s  avg_rank={avg_r:.2f}")
        lang_str = "  ".join(f"{lg}:{hl}/{tl}({pl:.0f}%)" for lg,(hl,tl,pl) in by_lang.items())
        print(f"    langs: {lang_str}")
        type_str = "  ".join(f"{ty}:{ht}/{tt}({pt:.0f}%)" for ty,(ht,tt,pt) in by_type.items())
        print(f"    types: {type_str}")

    print(f"\n{'='*60}")
    return summary


def print_comparison(summaries: dict):
    """Print a comparative table for all models in summaries dict."""
    all_methods = sorted(set(m for s in summaries.values() for m in s.keys()))
    model_names = list(summaries.keys())

    print(f"\n{'='*80}")
    print(f"  COMPARATIVE SUMMARY: {' vs '.join(model_names)}")
    print(f"{'='*80}")
    print(f"  {'Method':<12}  {'Model':<12}  {'P@5':>10}  {'avg_time':>10}  {'avg_rank':>10}")
    print(f"  {'-'*12}  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*10}")

    for method in all_methods:
        for name, summary in summaries.items():
            if method not in summary:
                continue
            s = summary[method]
            h, t, pct = s["overall"]
            print(f"  {method:<12}  {name:<12}  {h:>3}/{t:<3}({pct:>4.1f}%)  {s['avg_time']:>10.3f}s  {s['avg_rank']:>10.2f}")
    print(f"{'='*80}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Embedding model comparison benchmark (MiniLM / mpnet / mE5-small / LaBSE)")
    parser.add_argument("--skip-index",  action="store_true",
                        help="Reuse existing temp indices (faster, for re-runs)")
    parser.add_argument("--cleanup",     action="store_true",
                        help="Delete temp indices after benchmark")
    parser.add_argument("--only-model",  choices=list(MODELS.keys()), default=None,
                        help="Run only one model instead of all")
    parser.add_argument("--models",      nargs="+", choices=list(MODELS.keys()), default=None,
                        help="Run a specific subset of models, e.g. --models mE5-small LaBSE")
    parser.add_argument("--query-langs", nargs="+", default=None,
                        help="Filter queries by language, e.g. --query-langs de en")
    parser.add_argument("--batch-size",  type=int, default=16,
                        help="Embedding encode batch size (default: 16)")
    parser.add_argument("--bulk-size",   type=int, default=300,
                        help="Elasticsearch bulk flush size in docs (default: 300)")
    parser.add_argument("--corpus-include", nargs="+", default=None,
                        help="Optional filename globs to include, e.g. 'elo_0[1-3]*.md'")
    parser.add_argument("--max-files", type=int, default=None,
                        help="Optional max number of corpus files to index")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("  Course Catalog — Embedding Model Benchmark")
    print("="*60)
    print(f"  Corpus  : {CORPUS_DIR}")
    print(f"  Queries : {QUERIES_FILE}")
    print(f"  Results : {RESULTS_DIR}")
    print(f"  Time    : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    # ── Sanity checks ─────────────────────────────────────────────────────────
    if not os.path.isfile(QUERIES_FILE):
        print(f"\n  ERROR: {QUERIES_FILE} not found"); sys.exit(1)

    try:
        r = requests.get(f"{ES_URL}/_cluster/health", timeout=5)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        print(f"\n  [OK] Elasticsearch: {r.json().get('status')}")
    except Exception as e:
        print(f"\n  ERROR: Elasticsearch not reachable at {ES_URL} — {e}"); sys.exit(1)

    if args.only_model:
        models_to_run = [args.only_model]
    elif args.models:
        models_to_run = args.models
    else:
        models_to_run = list(MODELS.keys())

    summaries = {}

    for model_name in models_to_run:
        print(f"\n{'-'*60}")
        print(f"  Loading {model_name} embedding model...")
        try:
            embed_fn = load_embed_fn(model_name)
        except Exception as e:
            print(f"  [ERROR] Could not load {model_name}: {e}")
            print(f"  Skipping {model_name}.")
            continue
        print(f"  [OK] {model_name} loaded")

        results_file = run_benchmark(
            model_name,
            embed_fn,
            skip_index=args.skip_index,
            query_langs=args.query_langs,
            batch_size=args.batch_size,
            bulk_size=args.bulk_size,
            file_patterns=args.corpus_include,
            max_files=args.max_files,
        )
        summaries[model_name] = print_summary(results_file)

    if len(summaries) >= 2:
        print_comparison(summaries)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    if args.cleanup:
        for model_name in models_to_run:
            if model_name in MODELS:
                idx = MODELS[model_name]["index"]
                requests.delete(f"{ES_URL}/{idx}", timeout=10)
                print(f"  Deleted temp index '{idx}'")

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
