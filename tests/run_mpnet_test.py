#!/usr/bin/env python3
"""
run_mpnet_test.py - Run mpnet benchmark against course_catalog_test_mpnet index.

Usage:
    .venv\\Scripts\\python.exe tests\\run_mpnet_test.py
"""
import sys, os, warnings, json, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

# Add tests dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from compare_models import (
    MODELS, QUERIES_FILE, RESULTS_DIR,
    load_embed_fn, bm25_search, semantic_search, hybrid_search,
    python_backend_search, find_expected, ES_URL
)
from datetime import datetime
import requests

def main():
    print("Loading mpnet model...")
    embed_fn = load_embed_fn('mpnet')
    print("mpnet loaded. Checking index...")

    index = MODELS['mpnet']['index']
    count = requests.get(f"{ES_URL}/{index}/_count", timeout=5).json()['count']
    print(f"mpnet index '{index}' has {count} chunks")

    if count == 0:
        print("ERROR: mpnet index is empty. Run compare_models.py first.")
        sys.exit(1)

    with open(QUERIES_FILE, encoding='utf-8') as f:
        queries = json.load(f)['queries']

    print(f"\nRunning {len(queries)} queries x 4 methods...\n")
    all_records = []

    for idx, q in enumerate(queries, 1):
        qid      = q.get('id')
        text     = q.get('text', '')
        lang     = q.get('lang', '')
        qtype    = q.get('type', '')
        expected = q.get('expected_files', [])

        print(f"[{idx:2d}/{len(queries)}] {qid} ({lang}/{qtype}): {text[:55]}")

        methods = [
            ('BM25',     lambda q, ix=index: bm25_search(q, ix)),
            ('Semantic', lambda q, ix=index, ef=embed_fn: semantic_search(q, ix, ef)),
            ('Hybrid',   lambda q, ix=index, ef=embed_fn: hybrid_search(q, ix, ef)),
            ('Python',   lambda q: python_backend_search(q)),
        ]

        for method_name, fn in methods:
            t0 = time.time()
            try:
                results = fn(text)
            except Exception as exc:
                print(f"  ERROR {method_name}: {exc}")
                results = []
            elapsed = round(time.time() - t0, 3)
            hit, rank = find_expected(results, expected)
            hit_str = (f"rank={rank}" if (hit and rank > 0)
                       else ("OK(empty)" if hit else "MISS"))
            print(f"  mpnet/{method_name:<10} {hit_str}  ({elapsed:.2f}s)")
            all_records.append({
                'query_id': qid, 'query_text': text, 'lang': lang,
                'type': qtype, 'method': method_name, 'model': 'mpnet',
                'elapsed_s': elapsed, 'results': results,
                'hit_in_top5': hit, 'rank_of_expected': rank,
                'expected_files': expected,
            })

    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    payload = {'model': 'mpnet', 'timestamp': ts,
               'query_file': QUERIES_FILE, 'records': all_records}

    for path in [os.path.join(RESULTS_DIR, f'mpnet_results_{ts}.json'),
                 os.path.join(RESULTS_DIR, 'mpnet_results.json')]:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {RESULTS_DIR}")

    # Quick summary
    methods = sorted(set(r['method'] for r in all_records))
    print("\n=== SUMMARY ===")
    for m in methods:
        s = [r for r in all_records if r['method'] == m and r['expected_files']]
        h = sum(1 for r in s if r['hit_in_top5'])
        t = len(s)
        at = sum(r['elapsed_s'] for r in s) / len(s) if s else 0
        print(f"  {m:<12} P@5={h}/{t}({100*h/t if t else 0:.1f}%)  avg={at:.3f}s")

if __name__ == "__main__":
    main()
