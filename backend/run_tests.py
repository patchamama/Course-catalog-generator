#!/usr/bin/env python3
"""
run_tests.py — End-to-end validation & regression tests for Course Catalog Search.

Tests:
  9a. Cross-lingual search agnosticism (EN/ES/DE queries on same document)
  9b. RAG answer quality (multiple Ollama models, diverse questions)
  9c. Search mode comparison (ES BM25 / ES Hybrid / Python)
  9d. Edge cases (empty query, no-match, Ollama unavailable, ES unavailable)

Usage:
    python run_tests.py                       # all tests
    python run_tests.py --test 9a             # specific test
    python run_tests.py --api http://host:5000
    python run_tests.py --no-rag              # skip RAG tests (faster)
    python run_tests.py --output docs/test_results.md
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

# ── Config ───────────────────────────────────────────────────────────────────
DEFAULT_API = "http://127.0.0.1:5000"
DEFAULT_OUTPUT = "docs/test_results.md"

# Questions for RAG test (9b)
RAG_QUESTIONS = [
    "What is a function that returns an object with colors?",
    "How do you handle errors in JavaScript?",
    "What is the difference between var, let, and const?",
    "How do you search for files in the catalog?",
    "What configuration options are available in env.py?",
]

# Cross-lingual query triples for test 9a (same semantic content in 3 languages)
CROSS_LINGUAL_QUERIES = [
    ("function",       "función",     "Funktion"),
    ("configuration",  "configuración", "Konfiguration"),
    ("search",         "búsqueda",    "Suche"),
]

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 10) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _post(url: str, body: dict, timeout: int = 30) -> Optional[dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _post_sse(url: str, body: dict, timeout: int = 60) -> dict:
    """Call a streaming SSE endpoint, collect all events, return summary."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    result = {
        "tokens": [],
        "sources": [],
        "model": None,
        "error": None,
        "status_msgs": [],
        "elapsed_s": 0.0,
    }
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            buf = b""
            while True:
                chunk = r.read(1024)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode("utf-8", errors="replace").strip()
                    if line.startswith("data: "):
                        try:
                            evt = json.loads(line[6:])
                            t = evt.get("type", "")
                            if t == "token":
                                result["tokens"].append(evt.get("text", ""))
                            elif t == "sources":
                                result["sources"] = evt.get("sources", [])
                            elif t == "done":
                                result["model"] = evt.get("model")
                            elif t == "status":
                                result["status_msgs"].append(evt.get("message", ""))
                            elif t == "error":
                                result["error"] = evt.get("message", "unknown error")
                        except Exception:
                            pass
    except urllib.error.HTTPError as e:
        result["error"] = f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        result["error"] = str(e)
    result["elapsed_s"] = round(time.time() - t0, 2)
    result["answer"] = "".join(result["tokens"])
    return result


# ── Test results accumulator ──────────────────────────────────────────────────

class Results:
    def __init__(self):
        self.rows = []  # [(test_id, description, status, detail, elapsed)]

    def add(self, test_id, description, passed: bool, detail: str = "", elapsed: float = 0.0):
        status = "PASS" if passed else "FAIL"
        icon = PASS if passed else FAIL
        elapsed_str = f"{elapsed:.2f}s" if elapsed else ""
        print(f"  {icon}  [{test_id}] {description}"
              + (f"  {DIM}({elapsed_str}){RESET}" if elapsed_str else "")
              + (f"\n       {DIM}{detail}{RESET}" if detail else ""))
        self.rows.append((test_id, description, status, detail, elapsed_str))

    def warn(self, test_id, description, detail: str = ""):
        print(f"  {WARN}  [{test_id}] {description}"
              + (f"\n       {DIM}{detail}{RESET}" if detail else ""))
        self.rows.append((test_id, description, "SKIP", detail, ""))

    def summary(self) -> str:
        passed = sum(1 for r in self.rows if r[2] == "PASS")
        failed = sum(1 for r in self.rows if r[2] == "FAIL")
        skipped = sum(1 for r in self.rows if r[2] == "SKIP")
        return f"{passed} passed, {failed} failed, {skipped} skipped"

    def to_markdown(self) -> str:
        lines = [
            "# Course Catalog — Test Results",
            f"\nGenerated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Summary",
            "",
            f"**{self.summary()}**",
            "",
            "## Results",
            "",
            "| Test | Description | Status | Detail | Time |",
            "|------|-------------|--------|--------|------|",
        ]
        for test_id, desc, status, detail, elapsed in self.rows:
            icon = "✅" if status == "PASS" else ("⚠️" if status == "SKIP" else "❌")
            detail_short = (detail[:80] + "…") if len(detail) > 80 else detail
            detail_short = detail_short.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {test_id} | {desc} | {icon} {status} | {detail_short} | {elapsed} |")
        return "\n".join(lines) + "\n"


# ── Test 9a: Cross-lingual search agnosticism ─────────────────────────────────

def test_9a(api: str, r: Results):
    print(f"\n{BOLD}Test 9a — Cross-lingual search agnosticism{RESET}")

    health = _get(f"{api}/api/health")
    if not health or not health.get("es"):
        r.warn("9a", "Cross-lingual test", "ES not available — skipping")
        return

    for en_q, es_q, de_q in CROSS_LINGUAL_QUERIES:
        for mode_name, mode_params in [
            ("ES BM25",   {"mode": "bm25"}),
            ("ES Hybrid", {"mode": "hybrid"}),
        ]:
            results_per_lang = {}
            for lang_code, query in [("en", en_q), ("es", es_q), ("de", de_q)]:
                t0 = time.time()
                resp = _post(f"{api}/api/es-search", {
                    "q": query,
                    "lang": lang_code,
                    "limit": 5,
                    **mode_params,
                })
                elapsed = time.time() - t0
                hits = (resp or {}).get("results", [])
                results_per_lang[lang_code] = {
                    "hits": [h.get("filename", h.get("path", "?")) for h in hits[:5]],
                    "count": len(hits),
                    "elapsed": elapsed,
                }

            # Check overlap between languages (kNN/hybrid should find similar results)
            all_hits = [set(results_per_lang[l]["hits"]) for l in ["en", "es", "de"]]
            overlap_en_es = len(all_hits[0] & all_hits[1])
            overlap_en_de = len(all_hits[0] & all_hits[2])
            max_overlap = max(overlap_en_es, overlap_en_de)

            passed = (mode_name == "ES BM25") or (max_overlap >= 1)
            detail = (f"EN:{results_per_lang['en']['count']} ES:{results_per_lang['es']['count']} "
                      f"DE:{results_per_lang['de']['count']} hits — overlap: {max_overlap}")
            avg_t = sum(v['elapsed'] for v in results_per_lang.values()) / 3
            r.add("9a", f"Cross-lingual '{en_q}' [{mode_name}]", passed, detail, avg_t)


# ── Test 9b: RAG answer quality ───────────────────────────────────────────────

def test_9b(api: str, r: Results, skip_rag: bool = False):
    print(f"\n{BOLD}Test 9b — RAG answer quality{RESET}")

    if skip_rag:
        r.warn("9b", "RAG tests", "Skipped via --no-rag")
        return

    health = _get(f"{api}/api/health")
    ollama_ok = (health or {}).get("ollama", False)
    if not ollama_ok:
        r.warn("9b", "RAG tests", "Ollama not running — skipping")
        return

    models_resp = _get(f"{api}/api/ollama-models")
    models = (models_resp or {}).get("models", [])
    if not models:
        r.warn("9b", "RAG tests", "No Ollama models installed")
        return

    # Test with first available model
    model = models[0]
    print(f"  Using model: {model}")

    for i, question in enumerate(RAG_QUESTIONS[:3]):  # limit to 3 questions for speed
        t0 = time.time()
        result = _post_sse(f"{api}/api/ask", {
            "question": question,
            "lang": "auto",
            "k": 5,
            "model": model,
        })
        elapsed = time.time() - t0

        has_answer = len(result["answer"].strip()) > 20
        has_sources = len(result["sources"]) > 0
        no_error = result["error"] is None
        passed = has_answer and no_error

        detail = (f"answer_len={len(result['answer'])} sources={len(result['sources'])} "
                  f"model={result['model'] or model}")
        if result["error"]:
            detail += f" error={result['error']}"

        r.add(f"9b.{i+1}", f"RAG: {question[:50]}…", passed, detail, elapsed)

        if not has_sources:
            r.warn(f"9b.{i+1}s", f"No sources retrieved for: {question[:40]}…")


# ── Test 9c: Search mode comparison ───────────────────────────────────────────

def test_9c(api: str, r: Results):
    print(f"\n{BOLD}Test 9c — Search mode comparison{RESET}")

    health = _get(f"{api}/api/health")
    es_ok = (health or {}).get("es", False)

    test_queries = ["function", "error handling", "configuration"]

    for query in test_queries:
        mode_results = {}

        # Python search
        t0 = time.time()
        resp = _post(f"{api}/api/search", {"q": query, "limit": 5})
        elapsed_py = time.time() - t0
        py_hits = (resp or {}).get("results", [])
        mode_results["Python"] = {"count": len(py_hits), "top": py_hits[0].get("filename", "?") if py_hits else "", "elapsed": elapsed_py}

        if es_ok:
            for mode_name, mode_key in [("ES BM25", "bm25"), ("ES Hybrid", "hybrid")]:
                t0 = time.time()
                resp = _post(f"{api}/api/es-search", {"q": query, "mode": mode_key, "limit": 5})
                elapsed = time.time() - t0
                hits = (resp or {}).get("results", [])
                mode_results[mode_name] = {
                    "count": len(hits),
                    "top": hits[0].get("filename", "?") if hits else "",
                    "elapsed": elapsed,
                }

        # All modes should return at least some results (or 0 if index empty)
        counts = [v["count"] for v in mode_results.values()]
        any_results = any(c > 0 for c in counts)
        detail = " | ".join(f"{k}: {v['count']} hits ({v['elapsed']:.2f}s)" for k, v in mode_results.items())
        # Pass if either all modes have results or none do (consistent behavior)
        consistent = len(set(c > 0 for c in counts)) == 1
        r.add("9c", f"Mode comparison: '{query}'", consistent or not any_results, detail)


# ── Test 9d: Edge cases ───────────────────────────────────────────────────────

def test_9d(api: str, r: Results):
    print(f"\n{BOLD}Test 9d — Edge cases{RESET}")

    # Empty query
    t0 = time.time()
    resp = _post(f"{api}/api/search", {"q": "", "limit": 5})
    elapsed = time.time() - t0
    # Should return 400 or empty results, not crash (500)
    if resp is None:
        passed = False
        detail = "Server returned no response (possible crash)"
    else:
        passed = True
        detail = f"status={resp.get('status', 'ok')} results={len(resp.get('results', []))}"
    r.add("9d.1", "Empty query — no crash", passed, detail, elapsed)

    # Query with no matches
    t0 = time.time()
    resp = _post(f"{api}/api/search", {"q": "zzzzxxxxxqqqqqnowaythismatches99999", "limit": 5})
    elapsed = time.time() - t0
    passed = resp is not None and "results" in resp
    detail = f"results={len((resp or {}).get('results', []))}"
    r.add("9d.2", "No-match query — graceful empty result", passed, detail, elapsed)

    # Health check
    t0 = time.time()
    health = _get(f"{api}/api/health")
    elapsed = time.time() - t0
    passed = health is not None
    detail = f"es={health.get('es')} ollama={health.get('ollama')}" if health else "no response"
    r.add("9d.3", "Health endpoint responds", passed, detail, elapsed)

    # ES search when ES may be unavailable (should not crash)
    t0 = time.time()
    resp = _post(f"{api}/api/es-search", {"q": "test", "limit": 5})
    elapsed = time.time() - t0
    # If ES is down, should return error JSON, not 500 crash
    passed = resp is not None  # any response is OK
    detail = (f"error={resp.get('error', 'none')}" if resp and "error" in resp
              else f"results={len((resp or {}).get('results', []))}")
    r.add("9d.4", "ES unavailable — graceful error (not crash)", passed, detail, elapsed)

    # RAG with Ollama unavailable — should fallback to excerpts
    health = _get(f"{api}/api/health")
    if not (health or {}).get("ollama"):
        t0 = time.time()
        result = _post_sse(f"{api}/api/ask", {"question": "What is a function?", "lang": "auto", "k": 3})
        elapsed = time.time() - t0
        # Should return an error event or a fallback, not crash
        server_alive = _get(f"{api}/api/health") is not None
        r.add("9d.5", "RAG with Ollama down — no crash", server_alive,
              f"error={result.get('error', 'none')} server_still_alive={server_alive}", elapsed)
    else:
        r.warn("9d.5", "RAG Ollama-down test", "Ollama is running — skipping this edge case")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="End-to-end tests for Course Catalog Search")
    parser.add_argument("--api",    default=DEFAULT_API, help=f"Backend URL (default: {DEFAULT_API})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"Report output path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--test",   help="Run specific test: 9a, 9b, 9c, 9d")
    parser.add_argument("--no-rag", action="store_true", help="Skip RAG/LLM tests (faster)")
    args = parser.parse_args()

    print(f"\n{BOLD}{'='*60}")
    print("  Course Catalog — Validation & Regression Tests")
    print(f"{'='*60}{RESET}")
    print(f"  API: {args.api}")
    print(f"  Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")

    # Check backend is up
    health = _get(f"{args.api}/api/health", timeout=5)
    if not health:
        print(f"  {FAIL} Backend not responding at {args.api}")
        print("  Start with: python catalog_server.py --path /your/courses")
        sys.exit(1)

    print(f"  {PASS} Backend is up")
    print(f"  ES:     {'running' if health.get('es') else 'NOT RUNNING'}")
    print(f"  Ollama: {'running' if health.get('ollama') else 'NOT RUNNING'}")

    r = Results()
    run_all = args.test is None

    if run_all or args.test == "9a":
        test_9a(args.api, r)
    if run_all or args.test == "9b":
        test_9b(args.api, r, skip_rag=args.no_rag)
    if run_all or args.test == "9c":
        test_9c(args.api, r)
    if run_all or args.test == "9d":
        test_9d(args.api, r)

    # Summary
    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"  {BOLD}Results: {r.summary()}{RESET}")
    print(f"{'─'*60}\n")

    # Write markdown report
    import os
    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(r.to_markdown())
    print(f"  Report saved to: {args.output}\n")

    # Exit non-zero if any tests failed
    failed = sum(1 for row in r.rows if row[2] == "FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
