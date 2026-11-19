#!/usr/bin/env python3
"""
run_tests.py — Search quality benchmark for Course Catalog Generator.

Runs each query in test_queries.json against four search methods:
  1. BM25 direct to Elasticsearch
  2. Python backend /api/search
  3. Semantic via /api/es-search?semantic=1
  4. Hybrid  via /api/es-search?hybrid=1

Computes precision@5 per method, grouped by language and query type,
then writes full results JSON to tests/results/ and prints a summary table.

Usage:
    python3 run_tests.py
    python3 run_tests.py --model MiniLM
    python3 run_tests.py --skip-bm25
"""

import gzip  # available if ever needed for compressed results
import json
import os
import sys
import time
from datetime import datetime
from urllib.parse import quote

import requests

# ── Configuration ─────────────────────────────────────────────────────────────

BACKEND_URL = "http://127.0.0.1:5000"
ES_URL      = "http://127.0.0.1:9200"
ES_INDEX    = "course_catalog"

QUERIES_FILE = "/tmp/elo_test_corpus/test_queries.json"
RESULTS_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
CORPUS_DIR = "/tmp/elo_test_corpus"

# Timeout (seconds) for individual HTTP requests
HTTP_TIMEOUT = 15

# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_result(path: str, score: float, content_snippet: str = "") -> dict:
    """Return a uniform result dict regardless of which search method produced it."""
    fname    = os.path.basename(path)
    ext      = os.path.splitext(fname)[1].lstrip(".").lower()
    snippet  = (content_snippet or "")[:150]
    return {
        "file":    fname,
        "path":    path,
        "score":   round(float(score), 4),
        "ext":     ext,
        "snippet": snippet,
    }


# ── Search functions ───────────────────────────────────────────────────────────

def get_es_count() -> int:
    """Return the total number of documents in the ES index, or 0 on error."""
    try:
        resp = requests.get(
            f"{ES_URL}/{ES_INDEX}/_count",
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return int(resp.json().get("count", 0))
    except Exception as exc:
        print(f"  [WARN] ES count failed: {exc}")
        return 0


def bm25_search(query_text: str, top_k: int = 5) -> list:
    """
    Direct BM25 multi_match against ES.

    Searches content, content.de, content.en, content.es fields.
    Returns list of normalised result dicts (up to top_k, deduplicated by path).
    """
    body = {
        "size": top_k * 3,  # fetch extra so deduplication still yields top_k
        "query": {
            "multi_match": {
                "query":     query_text,
                "fields":    ["content", "content.de", "content.en", "content.es"],
                "type":      "best_fields",
                "fuzziness": "AUTO",
            }
        },
        "_source": ["path", "filename", "content", "ext"],
    }
    try:
        resp = requests.post(
            f"{ES_URL}/{ES_INDEX}/_search",
            json=body,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
    except Exception as exc:
        print(f"  [WARN] BM25 ES search failed: {exc}")
        return []

    # Deduplicate by path (keep highest-score chunk per file)
    seen: dict = {}
    for hit in hits:
        src   = hit.get("_source", {})
        score = hit.get("_score") or 0.0
        path  = src.get("path", "")
        if path and path not in seen:
            seen[path] = _normalize_result(
                path,
                score,
                src.get("content", ""),
            )

    return list(seen.values())[:top_k]


def python_backend_search(query_text: str, top_k: int = 5) -> list:
    """
    Call the backend /api/search endpoint (Python filesystem search,
    falls back to ES via _search_via_es if ES is available).

    Response shape: {"results": [{"path", "file", "type", "score", "matches": [...]}]}
    Each match has "context" with the snippet text.
    """
    try:
        resp = requests.get(
            f"{BACKEND_URL}/api/search",
            params={"q": query_text, "limit": top_k},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [WARN] /api/search failed: {exc}")
        return []

    results = []
    for item in data.get("results", [])[:top_k]:
        path    = item.get("path", "")
        score   = item.get("score", 0.0)
        # Grab the snippet from the first match's context field
        matches = item.get("matches", [])
        snippet = matches[0].get("chunk_content", "") if matches else ""
        results.append(_normalize_result(path, score, snippet))

    return results


def semantic_search_minilm(query_text: str, top_k: int = 5) -> list:
    """
    Semantic (kNN) search via /api/es-search?semantic=1.

    Response shape: {"results": [{"path", "file", "type", "score", "matches": [...]}]}
    """
    try:
        resp = requests.get(
            f"{BACKEND_URL}/api/es-search",
            params={"q": query_text, "semantic": "1", "limit": top_k},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [WARN] /api/es-search?semantic=1 failed: {exc}")
        return []

    results = []
    for item in data.get("results", [])[:top_k]:
        path    = item.get("path", "")
        score   = item.get("score", 0.0)
        matches = item.get("matches", [])
        snippet = matches[0].get("chunk_content", "") if matches else ""
        results.append(_normalize_result(path, score, snippet))

    return results


def hybrid_search_minilm(query_text: str, top_k: int = 5) -> list:
    """
    Hybrid (BM25 + kNN) search via /api/es-search?hybrid=1.

    Response shape: same as semantic endpoint above.
    """
    try:
        resp = requests.get(
            f"{BACKEND_URL}/api/es-search",
            params={"q": query_text, "hybrid": "1", "limit": top_k},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [WARN] /api/es-search?hybrid=1 failed: {exc}")
        return []

    results = []
    for item in data.get("results", [])[:top_k]:
        path    = item.get("path", "")
        score   = item.get("score", 0.0)
        matches = item.get("matches", [])
        snippet = matches[0].get("chunk_content", "") if matches else ""
        results.append(_normalize_result(path, score, snippet))

    return results


# ── Hit-detection helper ───────────────────────────────────────────────────────

def _find_expected(results: list, expected_files: list) -> tuple:
    """
    Return (hit_in_top5, rank) where:
      hit_in_top5 — True if any expected file token appears in any result path/file
      rank        — 1-based position of first hit, or -1 if not found

    Matching is case-insensitive partial match on result["file"] or result["path"].
    expected_files entries may be tokens like "react_hooks_guide.md" or
    partial labels like "README.md (Elo_Client)" — we extract the bare filename
    portion before the first space.
    """
    if not expected_files:
        # Off-topic queries: a hit means the search returned something (false positive)
        return (len(results) == 0, -1)

    # Build a flat list of lowercase tokens to search for
    tokens = []
    for ef in expected_files:
        # Take the part before the first space/paren as the filename token
        bare = ef.split("(")[0].strip().split()[0].strip().lower()
        if bare:
            tokens.append(bare)

    for rank, res in enumerate(results, start=1):
        result_text = (res.get("file", "") + " " + res.get("path", "")).lower()
        for token in tokens:
            if token and token in result_text:
                return (True, rank)

    return (False, -1)


# ── Main benchmark runner ──────────────────────────────────────────────────────

def run_all_tests(model_name: str = "MiniLM") -> str:
    """
    Load test_queries.json, run all 4 search methods for each query,
    compute hit_in_top5 and rank_of_expected, save to RESULTS_DIR.

    Returns the path to the saved results JSON file.
    """
    # Load queries
    with open(QUERIES_FILE, encoding="utf-8") as fh:
        corpus = json.load(fh)
    queries = corpus.get("queries", [])
    print(f"\n  Loaded {len(queries)} queries from {QUERIES_FILE}")

    os.makedirs(RESULTS_DIR, exist_ok=True)

    methods = {
        "BM25":     bm25_search,
        "Python":   python_backend_search,
        "Semantic": semantic_search_minilm,
        "Hybrid":   hybrid_search_minilm,
    }

    all_records = []
    total = len(queries)

    for idx, q in enumerate(queries, start=1):
        qid   = q.get("id", f"Q{idx:02d}")
        text  = q.get("text", "")
        lang  = q.get("lang", "")
        qtype = q.get("type", "")
        expected = q.get("expected_files", [])

        print(f"  [{idx:2d}/{total}] {qid} ({lang}/{qtype}): {text[:60]}")

        for method_name, search_fn in methods.items():
            t0 = time.time()
            try:
                results = search_fn(text, top_k=5)
            except Exception as exc:
                print(f"         {method_name} ERROR: {exc}")
                results = []
            elapsed = round(time.time() - t0, 3)

            hit, rank = _find_expected(results, expected)

            record = {
                "query_id":          qid,
                "query_text":        text,
                "lang":              lang,
                "type":              qtype,
                "method":            method_name,
                "model":             model_name,
                "elapsed_s":         elapsed,
                "results":           results,
                "hit_in_top5":       hit,
                "rank_of_expected":  rank,
                "expected_files":    expected,
            }
            all_records.append(record)

            hit_str = f"rank={rank}" if hit else "MISS"
            print(f"         {method_name:<10} {hit_str}  ({elapsed:.2f}s)")

    # Persist results
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(RESULTS_DIR, f"{model_name}_results_{ts}.json")
    # Also write a stable "latest" file for easy reference
    latest_path = os.path.join(RESULTS_DIR, f"{model_name}_results.json")

    payload = {
        "model":      model_name,
        "timestamp":  ts,
        "query_file": QUERIES_FILE,
        "records":    all_records,
    }
    for path in (out_path, latest_path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"\n  Results saved to:\n    {out_path}\n    {latest_path}")
    return latest_path


# ── Summary printer ────────────────────────────────────────────────────────────

def print_summary(results_file: str) -> None:
    """
    Load a results JSON file produced by run_all_tests() and print:
      - Overall precision@5 per method
      - Breakdown by language (de / en / es)
      - Breakdown by query type (exact / semantic / off-topic)
    """
    with open(results_file, encoding="utf-8") as fh:
        data = json.load(fh)

    records = data.get("records", [])
    model   = data.get("model", "?")
    ts      = data.get("timestamp", "?")

    print(f"\n{'='*65}")
    print(f"  SEARCH QUALITY SUMMARY — model={model}  ts={ts}")
    print(f"{'='*65}")

    # Collect the methods found in the results
    methods = sorted({r["method"] for r in records})

    def _precision(subset: list) -> str:
        """Return 'hits/total (pct%)' for subset, handling off-topic separately."""
        # Off-topic queries have expected_files==[] and hit means FALSE POSITIVE
        # For them, hit_in_top5=True means "correctly returned no results"
        hits  = sum(1 for r in subset if r["hit_in_top5"])
        total = len(subset)
        pct   = 100 * hits / total if total else 0.0
        return f"{hits:2d}/{total:2d} ({pct:5.1f}%)"

    # ── Overall ──────────────────────────────────────────────────────────────
    print(f"\n  {'Method':<12} {'Overall P@5':>16}")
    print(f"  {'-'*12}   {'-'*16}")
    for m in methods:
        subset = [r for r in records if r["method"] == m]
        print(f"  {m:<12} {_precision(subset):>16}")

    # ── By language ──────────────────────────────────────────────────────────
    langs = sorted({r["lang"] for r in records})
    print(f"\n  {'Method':<12}" + "".join(f"  {lg:>10}" for lg in langs))
    print(f"  {'-'*12}" + "".join(f"  {'-'*10}" for _ in langs))
    for m in methods:
        row = f"  {m:<12}"
        for lg in langs:
            subset = [r for r in records if r["method"] == m and r["lang"] == lg]
            row += f"  {_precision(subset):>10}"
        print(row)

    # ── By type ──────────────────────────────────────────────────────────────
    types = sorted({r["type"] for r in records})
    print(f"\n  {'Method':<12}" + "".join(f"  {t:>12}" for t in types))
    print(f"  {'-'*12}" + "".join(f"  {'-'*12}" for _ in types))
    for m in methods:
        row = f"  {m:<12}"
        for t in types:
            subset = [r for r in records if r["method"] == m and r["type"] == t]
            row += f"  {_precision(subset):>12}"
        print(row)

    # ── Average rank when found ───────────────────────────────────────────────
    print(f"\n  {'Method':<12}  {'Avg rank (when found)':>22}")
    print(f"  {'-'*12}  {'-'*22}")
    for m in methods:
        found = [r["rank_of_expected"] for r in records
                 if r["method"] == m and r["hit_in_top5"] and r["rank_of_expected"] > 0]
        avg = sum(found) / len(found) if found else float("nan")
        print(f"  {m:<12}  {avg:>22.2f}  (n={len(found)})")

    print(f"\n{'='*65}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Search quality benchmark for Course Catalog Generator"
    )
    parser.add_argument(
        "--model", default="MiniLM",
        help="Model label used for results filename (default: MiniLM)"
    )
    parser.add_argument(
        "--skip-bm25", action="store_true",
        help="Skip direct ES BM25 queries (useful if ES not reachable)"
    )
    args = parser.parse_args()

    print(f"\n{'='*65}")
    print("  Course Catalog — Search Quality Benchmark")
    print(f"{'='*65}")
    print(f"  Backend : {BACKEND_URL}")
    print(f"  ES      : {ES_URL}")
    print(f"  Index   : {ES_INDEX}")
    print(f"  Queries : {QUERIES_FILE}")
    print(f"  Results : {RESULTS_DIR}")
    print(f"  Model   : {args.model}")
    print(f"  Time    : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    # ── Check backend ─────────────────────────────────────────────────────────
    print("\n  Checking services...")
    backend_ok = False
    try:
        r = requests.get(f"{BACKEND_URL}/api/ping", timeout=5)
        if r.status_code == 200:
            backend_ok = True
            print(f"  [OK] Backend reachable at {BACKEND_URL}")
        else:
            print(f"  [WARN] Backend returned HTTP {r.status_code}")
    except Exception as exc:
        print(f"  [FAIL] Backend not reachable: {exc}")
        # Try /api/health as fallback
        try:
            r = requests.get(f"{BACKEND_URL}/api/health", timeout=5)
            if r.status_code == 200:
                backend_ok = True
                health = r.json()
                es_status = health.get("elasticsearch", "unknown")
                print(f"  [OK] Backend reachable (/api/health)  ES={es_status}")
        except Exception:
            pass

    if not backend_ok:
        print(f"\n  ERROR: Backend not responding at {BACKEND_URL}")
        print("  Start with: python catalog_server.py\n")
        sys.exit(1)

    # ── Check ES ──────────────────────────────────────────────────────────────
    es_ok = False
    try:
        r = requests.get(f"{ES_URL}/_cluster/health", timeout=5)
        if r.status_code == 200:
            es_ok = True
            status = r.json().get("status", "?")
            print(f"  [OK] Elasticsearch reachable  cluster_status={status}")
    except Exception as exc:
        print(f"  [WARN] Elasticsearch not reachable: {exc}")

    # ── ES doc count ──────────────────────────────────────────────────────────
    if es_ok:
        count = get_es_count()
        print(f"  [INFO] ES index '{ES_INDEX}' contains {count} documents")
        if count == 0:
            print("  [WARN] Index is empty — ES searches will return no results")

    # ── Check queries file ────────────────────────────────────────────────────
    if not os.path.isfile(QUERIES_FILE):
        print(f"\n  ERROR: Queries file not found: {QUERIES_FILE}")
        print(f"  Generate it with: python tools/generate_test_queries.py\n")
        sys.exit(1)

    # ── Monkey-patch BM25 search if requested ────────────────────────────────
    if args.skip_bm25:
        print("  [INFO] --skip-bm25 set: BM25 method will return empty results")
        global bm25_search
        bm25_search = lambda q, top_k=5: []  # noqa: E731

    # ── Run tests ─────────────────────────────────────────────────────────────
    print()
    results_file = run_all_tests(model_name=args.model)

    # ── Print summary ──────────────────────────────────────────────────────────
    print_summary(results_file)


if __name__ == "__main__":
    main()
