#!/usr/bin/env python3
"""Full mpnet benchmark - index + query. Run with the venv Python."""
import sys, os, warnings, json, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

# Determine project root and add tests to path
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from compare_models import (
    MODELS, QUERIES_FILE, RESULTS_DIR, ES_URL,
    create_test_index, index_corpus, load_embed_fn,
    bm25_search, semantic_search, hybrid_search,
    python_backend_search, find_expected,
)
from datetime import datetime
import requests

MODEL_NAME = 'mpnet'

def main():
    log = open(os.path.join(SCRIPT_DIR, 'mpnet_benchmark_log.txt'), 'w', encoding='utf-8')

    def prn(msg):
        print(msg, flush=True)
        log.write(msg + '\n')
        log.flush()

    prn("=== mpnet Benchmark ===")
    prn(f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    # Check ES
    try:
        r = requests.get(f"{ES_URL}/_cluster/health", timeout=5)
        prn(f"ES status: {r.json().get('status')}")
    except Exception as e:
        prn(f"ES ERROR: {e}"); sys.exit(1)

    # Setup index
    prn(f"\nCreating index '{MODELS[MODEL_NAME]['index']}'...")
    create_test_index(MODEL_NAME)

    prn(f"Loading {MODEL_NAME} embedding model...")
    embed_fn = load_embed_fn(MODEL_NAME)
    prn(f"{MODEL_NAME} loaded!")

    prn(f"Indexing corpus...")
    index_corpus(MODEL_NAME, embed_fn)

    count = requests.get(f"{ES_URL}/{MODELS[MODEL_NAME]['index']}/_count",
                         timeout=5).json()['count']
    prn(f"Index has {count} chunks")

    # Load queries
    with open(QUERIES_FILE, encoding='utf-8') as f:
        queries = json.load(f)['queries']
    prn(f"\nRunning {len(queries)} queries x 4 methods...")

    all_records = []
    index = MODELS[MODEL_NAME]['index']

    for idx, q in enumerate(queries, 1):
        qid      = q.get('id')
        text     = q.get('text', '')
        lang     = q.get('lang', '')
        qtype    = q.get('type', '')
        expected = q.get('expected_files', [])

        prn(f"[{idx:2d}/{len(queries)}] {qid} ({lang}/{qtype}): {text[:55]}")

        for method_name, fn in [
            ('BM25',     lambda q, ix=index: bm25_search(q, ix)),
            ('Semantic', lambda q, ix=index, ef=embed_fn: semantic_search(q, ix, ef)),
            ('Hybrid',   lambda q, ix=index, ef=embed_fn: hybrid_search(q, ix, ef)),
            ('Python',   lambda q: python_backend_search(q)),
        ]:
            t0 = time.time()
            try:
                results = fn(text)
            except Exception as exc:
                prn(f"  ERROR {method_name}: {exc}"); results = []
            elapsed = round(time.time() - t0, 3)
            hit, rank = find_expected(results, expected)
            hit_str = (f"rank={rank}" if (hit and rank > 0)
                       else ("OK(empty)" if hit else "MISS"))
            prn(f"  {MODEL_NAME}/{method_name:<10} {hit_str}  ({elapsed:.2f}s)")
            all_records.append({
                'query_id': qid, 'query_text': text, 'lang': lang,
                'type': qtype, 'method': method_name, 'model': MODEL_NAME,
                'elapsed_s': elapsed, 'results': results,
                'hit_in_top5': hit, 'rank_of_expected': rank,
                'expected_files': expected,
            })

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    payload = {'model': MODEL_NAME, 'timestamp': ts,
               'query_file': QUERIES_FILE, 'records': all_records}
    for path in [os.path.join(RESULTS_DIR, f'{MODEL_NAME}_results_{ts}.json'),
                 os.path.join(RESULTS_DIR, f'{MODEL_NAME}_results.json')]:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        prn(f"Saved: {path}")

    # Summary
    methods = sorted(set(r['method'] for r in all_records))
    prn("\n=== SUMMARY ===")
    for m in methods:
        s = [r for r in all_records if r['method'] == m and r['expected_files']]
        h = sum(1 for r in s if r['hit_in_top5'])
        t = len(s)
        at = sum(r['elapsed_s'] for r in s) / len(s) if s else 0
        prn(f"  {m:<12} P@5={h}/{t}({100*h/t if t else 0:.1f}%)  avg={at:.3f}s")

    prn("\nDONE!")
    log.close()

if __name__ == "__main__":
    main()
