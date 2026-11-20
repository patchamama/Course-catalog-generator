#!/usr/bin/env python3
"""
search_status.py — Elasticsearch index status and statistics for Course Catalog.

Displays:
  - Cluster info: version, health status, nodes, installed plugins
  - Index metrics: store size, total chunks, unique files, avg chunks per file
  - Per-type breakdown: file count and chunk count for each extension
  - Search capabilities: BM25, multilingual analyzers, kNN semantic vectors,
    hybrid BM25+kNN (RRF), SRT timestamp-aware search
  - Content info: detected languages, translation status
  - RAG: Ollama availability and loaded models

Usage:
    python backend/search_status.py
    python backend/search_status.py --es-url http://127.0.0.1:9200 --index course_catalog
    python backend/search_status.py --json
"""

import os
import sys
import json
import argparse
import subprocess
import urllib.request
from datetime import datetime, timezone

# ─── Load config from env.py (same pattern as search_indexer.py) ─────────────────
try:
    import env
    ES_URL   = getattr(env, 'ES_URL', None) or os.environ.get('ES_URL', 'http://127.0.0.1:9200')
    ES_INDEX = getattr(env, 'ES_INDEX', os.environ.get('ES_INDEX', 'course_catalog'))
except ImportError:
    ES_URL   = os.environ.get('ES_URL', 'http://127.0.0.1:9200')
    ES_INDEX = os.environ.get('ES_INDEX', 'course_catalog')

# ─── ANSI colour codes (only when stdout is a real terminal) ──────────────────
if sys.stdout.isatty():
    CR  = '\033[0;31m'   # red
    CG  = '\033[0;32m'   # green
    CY  = '\033[1;33m'   # yellow
    CC  = '\033[0;36m'   # cyan
    CB  = '\033[1m'      # bold
    DIM = '\033[2m'      # dim
    RST = '\033[0m'      # reset
else:
    CR = CG = CY = CC = CB = DIM = RST = ''


# ─── Helpers ──────────────────────────────────────────────────────────────────

def fmt_bytes(n: int) -> str:
    """Return a human-readable byte size string (e.g. '1.4 GB')."""
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def sep(width: int = 58) -> None:
    """Print a horizontal separator line."""
    print(f"{DIM}{'─' * width}{RST}")


def epoch_ms_to_str(ms) -> str:
    """Convert epoch milliseconds (from ES date aggregation) to a readable string."""
    if ms is None:
        return "—"
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def count_unique_files(es, index: str) -> int:
    """
    Count unique file paths using composite aggregation with pagination.
    Avoids the max_result_window (10 000) limit.
    """
    total = 0
    after_key = None
    while True:
        composite = {
            "size": 1000,
            "sources": [{"path": {"terms": {"field": "path"}}}],
        }
        if after_key:
            composite["after"] = after_key
        resp = es.search(
            index=index,
            body={"size": 0, "aggs": {"paths": {"composite": composite}}},
        )
        buckets = resp["aggregations"]["paths"]["buckets"]
        total += len(buckets)
        if len(buckets) < 1000:
            break
        after_key = resp["aggregations"]["paths"]["after_key"]
    return total


# ─── Docker volume helper ─────────────────────────────────────────────────────

def _get_docker_volume_info() -> dict:
    """
    Locate the Docker named volume that stores Elasticsearch data.

    Returns a dict with:
      volume_name  — full volume name as Docker sees it
      mountpoint   — host filesystem path where the volume is stored
      container_path — path inside the ES container (always /usr/share/elasticsearch/data)

    Returns an empty dict if Docker is unavailable or the volume cannot be found.
    """
    container_path = '/usr/share/elasticsearch/data'
    try:
        # List all volume names
        ls = subprocess.run(
            ['docker', 'volume', 'ls', '--format', '{{.Name}}'],
            capture_output=True, text=True, timeout=6,
        )
        volume_name = None
        for line in ls.stdout.splitlines():
            name = line.strip()
            if name.endswith('es_data'):   # matches *_es_data or es_data
                volume_name = name
                break

        if not volume_name:
            return {'container_path': container_path,
                    'error': 'Volume *es_data not found — is the container running?'}

        # Get the host mount path
        inspect = subprocess.run(
            ['docker', 'volume', 'inspect', volume_name, '--format', '{{.Mountpoint}}'],
            capture_output=True, text=True, timeout=6,
        )
        mountpoint = inspect.stdout.strip()
        return {
            'volume_name':    volume_name,
            'mountpoint':     mountpoint,
            'container_path': container_path,
        }
    except FileNotFoundError:
        return {'container_path': container_path,
                'error': 'docker command not found'}
    except Exception as exc:
        return {'container_path': container_path, 'error': str(exc)}


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show Elasticsearch index statistics for Course Catalog"
    )
    parser.add_argument('--es-url', default=ES_URL,   help=f'ES URL  (default: {ES_URL})')
    parser.add_argument('--index',  default=ES_INDEX, help=f'Index name  (default: {ES_INDEX})')
    parser.add_argument('--json',   action='store_true',
                        help='Output raw JSON instead of a formatted report')
    args = parser.parse_args()

    # ── Import ES client ──────────────────────────────────────────────────────
    try:
        from elasticsearch import Elasticsearch
    except ImportError:
        print("ERROR: elasticsearch package not installed.  "
              "Run: pip install 'elasticsearch>=8.0,<9.0'")
        sys.exit(1)

    es = Elasticsearch(args.es_url)

    # ── Cluster connection ────────────────────────────────────────────────────
    try:
        info = es.info()
    except Exception as exc:
        print(f"ERROR: Cannot connect to Elasticsearch at {args.es_url}\n  {exc}")
        sys.exit(1)

    es_version   = info['version']['number']
    build_flavor = info['version'].get('build_flavor', '')

    # ── Cluster health ────────────────────────────────────────────────────────
    health         = es.cluster.health()
    cluster_status = health['status']           # green / yellow / red
    num_nodes      = health['number_of_nodes']

    # ── Installed plugins (per-node, deduplicated) ────────────────────────────
    plugins: set[str] = set()
    try:
        nodes_info = es.nodes.info(metric='plugins')
        for node in nodes_info['nodes'].values():
            for p in node.get('plugins', []):
                plugins.add(p['name'])
    except Exception:
        pass  # not critical

    # ── Data storage: find Docker volume that holds ES data ───────────────────
    # Elasticsearch runs inside Docker; data lives in a named volume (es_data).
    # Docker Compose prefixes it with the project name (directory name, lowercased).
    # We use `docker volume ls` to find the right volume, then inspect its host path.
    docker_volume_info: dict = _get_docker_volume_info()

    # ── Index existence ───────────────────────────────────────────────────────
    if not es.indices.exists(index=args.index):
        msg = f"Index '{args.index}' does not exist yet — run the indexer first."
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(f"\n  {CY}{msg}{RST}\n")
        sys.exit(0)

    # ── Store size and document count ─────────────────────────────────────────
    idx_raw    = es.indices.stats(index=args.index)
    idx_total  = idx_raw['indices'][args.index]['total']
    store_bytes = idx_total['store']['size_in_bytes']
    doc_count   = idx_total['docs']['count']      # total chunks

    # ── Configured analyzers (from index settings) ────────────────────────────
    idx_settings = es.indices.get_settings(index=args.index)
    analysis_cfg = (
        idx_settings.get(args.index, {})
                    .get('settings', {})
                    .get('index', {})
                    .get('analysis', {})
    )
    configured_analyzers = list(analysis_cfg.get('analyzer', {}).keys())

    # ── Unique file count (composite pagination, no size limit) ───────────────
    unique_files = count_unique_files(es, args.index)

    # ── Stats grouped by file extension ───────────────────────────────────────
    ext_resp = es.search(
        index=args.index,
        body={
            "size": 0,
            "aggs": {
                "by_ext": {
                    "terms": {"field": "ext", "size": 50},
                    "aggs": {
                        # cardinality gives approximate unique file count per type
                        "unique_files": {
                            "cardinality": {
                                "field": "path",
                                "precision_threshold": 10000,
                            }
                        },
                    },
                }
            },
        },
    )
    ext_stats: dict[str, dict] = {}
    for bucket in ext_resp["aggregations"]["by_ext"]["buckets"]:
        ext_stats[bucket["key"]] = {
            "chunks": bucket["doc_count"],
            "files":  bucket["unique_files"]["value"],
        }

    # ── kNN / semantic vector availability ────────────────────────────────────
    vec_resp    = es.search(
        index=args.index,
        body={"size": 1, "query": {"exists": {"field": "content_vector"}}},
    )
    has_vectors = vec_resp["hits"]["total"]["value"] > 0

    # ── SRT / VTT timestamp-aware search ─────────────────────────────────────
    srt_resp          = es.search(
        index=args.index,
        body={"size": 1, "query": {"exists": {"field": "srt_start_time"}}},
    )
    has_srt_timestamps = srt_resp["hits"]["total"]["value"] > 0

    # ── Language distribution and translation status ───────────────────────────
    lang_resp = es.search(
        index=args.index,
        body={
            "size": 0,
            "query": {"exists": {"field": "detected_lang"}},
            "aggs": {
                "languages":   {"terms": {"field": "detected_lang",     "size": 20}},
                "trans_status":{"terms": {"field": "translation_status","size": 5}},
            },
        },
    )
    lang_dist  = {b["key"]: b["doc_count"]
                  for b in lang_resp["aggregations"]["languages"]["buckets"]}
    trans_dist = {b["key"]: b["doc_count"]
                  for b in lang_resp["aggregations"]["trans_status"]["buckets"]}

    # ── Oldest / newest indexed_at timestamp ──────────────────────────────────
    time_resp = es.search(
        index=args.index,
        body={
            "size": 0,
            "aggs": {
                "newest": {"max": {"field": "indexed_at"}},
                "oldest": {"min": {"field": "indexed_at"}},
            },
        },
    )
    oldest_str = epoch_ms_to_str(time_resp["aggregations"]["oldest"]["value"])
    newest_str = epoch_ms_to_str(time_resp["aggregations"]["newest"]["value"])

    # ── Ollama availability and loaded models ─────────────────────────────────
    ollama_running = False
    ollama_models: list[str] = []
    try:
        req = urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=2)
        data = json.loads(req.read())
        ollama_models = [m['name'] for m in data.get('models', [])]
        ollama_running = True
    except Exception:
        pass

    # ── Build unified report dict ─────────────────────────────────────────────
    avg_chunks = round(doc_count / unique_files, 1) if unique_files else 0
    report = {
        "elasticsearch": {
            "url":            args.es_url,
            "version":        es_version,
            "build_flavor":   build_flavor,
            "cluster_status": cluster_status,
            "nodes":          num_nodes,
            "plugins":        sorted(plugins),
            "docker_storage": docker_volume_info,
        },
        "index": {
            "name":              args.index,
            "store_bytes":       store_bytes,
            "store_human":       fmt_bytes(store_bytes),
            "total_chunks":      doc_count,
            "unique_files":      unique_files,
            "avg_chunks_per_file": avg_chunks,
            "first_indexed":     oldest_str,
            "last_indexed":      newest_str,
        },
        "by_extension": ext_stats,
        "search": {
            "bm25":               True,
            "multilingual_bm25":  len(configured_analyzers) > 0,
            "analyzers":          configured_analyzers,
            "knn_vectors":        has_vectors,
            "hybrid_bm25_knn":    has_vectors,
            "srt_timestamps":     has_srt_timestamps,
        },
        "content": {
            "language_distribution": lang_dist,
            "translation_status":    trans_dist,
        },
        "rag": {
            "ollama_running": ollama_running,
            "ollama_models":  ollama_models,
        },
    }

    # ── JSON output mode ──────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(report, indent=2))
        return

    # ── Pretty-printed report ─────────────────────────────────────────────────
    STATUS_COLOR = {"green": CG, "yellow": CY, "red": CR}
    sc = STATUS_COLOR.get(cluster_status, CY)

    print()
    sep()
    print(f"  {CB}Elasticsearch Status Report{RST}")
    sep()

    # — Cluster section ——————————————————————————————————————————————————————
    print(f"\n  {CB}Cluster{RST}")
    print(f"    URL         {CC}{args.es_url}{RST}")
    print(f"    Version     {es_version}  ({build_flavor})")
    print(f"    Status      {sc}{cluster_status.upper()}{RST}")
    print(f"    Nodes       {num_nodes}")
    if plugins:
        print(f"    Plugins     {', '.join(sorted(plugins))}")
    else:
        print(f"    Plugins     {DIM}none{RST}")

    # — Index section ————————————————————————————————————————————————————————
    print(f"\n  {CB}Index:{RST}  {CC}{args.index}{RST}")
    print(f"    Size              {CB}{fmt_bytes(store_bytes)}{RST}")
    print(f"    Total chunks      {doc_count:,}")
    print(f"    Unique files      {unique_files:,}")
    print(f"    Avg chunks/file   {avg_chunks}")
    print(f"    First indexed     {DIM}{oldest_str}{RST}")
    print(f"    Last indexed      {DIM}{newest_str}{RST}")

    # — Files by type ————————————————————————————————————————————————————————
    if ext_stats:
        print(f"\n  {CB}Files by type{RST}")
        sorted_exts = sorted(ext_stats.items(), key=lambda x: x[1]['files'], reverse=True)
        col_w = max(len(e) for e in ext_stats) + 2
        print(f"    {'EXT':<{col_w}}  {'FILES':>6}  {'CHUNKS':>8}  {'AVG CHUNKS':>10}")
        print(f"    {'─' * col_w}  {'─' * 6}  {'─' * 8}  {'─' * 10}")
        for ext, s in sorted_exts:
            avg = round(s['chunks'] / s['files'], 1) if s['files'] else 0
            print(f"    {ext:<{col_w}}  {s['files']:>6,}  {s['chunks']:>8,}  {avg:>10.1f}")

    # — Search capabilities ———————————————————————————————————————————————————
    print(f"\n  {CB}Search capabilities{RST}")

    def cap(enabled: bool, label: str, detail: str = '') -> None:
        mark       = f"{CG}✓{RST}" if enabled else f"{DIM}✗{RST}"
        detail_str = f"  {DIM}({detail}){RST}" if detail else ''
        print(f"    {mark}  {label}{detail_str}")

    cap(True, "BM25 full-text search")
    cap(
        bool(configured_analyzers),
        "Multilingual BM25",
        ', '.join(configured_analyzers) if configured_analyzers else '',
    )
    cap(
        has_vectors,
        "kNN semantic vectors",
        "paraphrase-multilingual-MiniLM-L12-v2" if has_vectors
        else "re-index with vectors enabled to activate",
    )
    cap(has_vectors, "Hybrid BM25 + kNN (RRF)")
    cap(has_srt_timestamps, "SRT/VTT timestamp-aware search")

    # — Language distribution ─────────────────────────────────────────────────
    if lang_dist:
        print(f"\n  {CB}Detected languages{RST}")
        total_lang = sum(lang_dist.values())
        for lang, count in sorted(lang_dist.items(), key=lambda x: -x[1]):
            bar_w = min(28, max(1, int(count / total_lang * 28)))
            pct   = count / total_lang * 100
            print(f"    {lang:<10}  {'█' * bar_w:<28}  {count:>8,}  ({pct:.1f}%)")

    # — Translation status ────────────────────────────────────────────────────
    if trans_dist:
        print(f"\n  {CB}Translation status{RST}")
        for status, count in trans_dist.items():
            colour = CG if status == 'done' else CY
            print(f"    {colour}{status:<12}{RST}  {count:,}")

    # — Data storage path ────────────────────────────────────────────────────
    dv = docker_volume_info
    print(f"\n  {CB}Data storage{RST}")
    if dv.get('error'):
        print(f"    {DIM}{dv['error']}{RST}")
        print(f"    Container path  {CC}{dv.get('container_path', '/usr/share/elasticsearch/data')}{RST}")
        print(f"    {DIM}Inspect manually: docker volume inspect <volume_name>{RST}")
    else:
        print(f"    Volume name     {CC}{dv.get('volume_name', '—')}{RST}")
        print(f"    Host path       {CC}{dv.get('mountpoint', '—')}{RST}")
        print(f"    Container path  {DIM}{dv.get('container_path', '—')}{RST}")
        print(f"    {DIM}Tip: docker volume inspect {dv.get('volume_name', 'es_data')}{RST}")

    # — RAG / Ollama ─────────────────────────────────────────────────────────
    print(f"\n  {CB}RAG / Ollama{RST}")
    if ollama_running:
        print(f"    {CG}✓{RST}  Ollama running")
        for m in ollama_models:
            print(f"         {DIM}{m}{RST}")
    else:
        print(f"    {DIM}✗{RST}  Ollama not running  "
              f"{DIM}(start with: ollama serve){RST}")

    sep()
    print()


if __name__ == '__main__':
    main()
