#!/usr/bin/env python3
"""
backup_es.py — Backup all documents from the course_catalog ES index.

Outputs:
  /tmp/es_backup_course_catalog.ndjson.gz  — compressed NDJSON, one doc per line
  /tmp/es_backup_settings.json             — index mappings + settings
"""

import gzip
import json
import os
import sys

from elasticsearch import Elasticsearch

ES_HOST = "http://127.0.0.1:9200"
INDEX = "course_catalog"
SCROLL_SIZE = 500
SCROLL_TTL = "5m"

NDJSON_PATH = "/tmp/es_backup_course_catalog.ndjson.gz"
SETTINGS_PATH = "/tmp/es_backup_settings.json"


def main():
    es = Elasticsearch(ES_HOST)

    # --- Verify cluster is up ---
    if not es.ping():
        print(f"ERROR: Cannot connect to Elasticsearch at {ES_HOST}", file=sys.stderr)
        sys.exit(1)

    print(f"Connected to Elasticsearch at {ES_HOST}")

    # --- Save index settings + mappings ---
    print(f"Fetching settings and mappings for index '{INDEX}' ...")
    settings = es.indices.get_settings(index=INDEX)
    mappings = es.indices.get_mapping(index=INDEX)

    combined = {
        "settings": settings[INDEX],
        "mappings": mappings[INDEX]["mappings"],
    }
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2)
    print(f"Saved settings + mappings to {SETTINGS_PATH}")

    # --- Count total docs ---
    count_resp = es.count(index=INDEX)
    total_expected = count_resp["count"]
    print(f"Index reports {total_expected:,} documents. Starting scroll backup ...")

    # --- Scroll and write NDJSON ---
    total_saved = 0

    with gzip.open(NDJSON_PATH, "wt", encoding="utf-8") as gz:

        # Initial scroll request
        resp = es.search(
            index=INDEX,
            scroll=SCROLL_TTL,
            size=SCROLL_SIZE,
            body={"query": {"match_all": {}}},
        )
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]

        while hits:
            for hit in hits:
                doc = {"_id": hit["_id"]}
                doc.update(hit["_source"])
                gz.write(json.dumps(doc, ensure_ascii=False, default=str).encode('utf-8', errors='replace').decode('utf-8') + "\n")
                total_saved += 1

            if total_saved % 1000 < SCROLL_SIZE:
                # Print progress roughly every 1000 docs
                milestone = (total_saved // 1000) * 1000
                if milestone > 0 and total_saved - len(hits) < milestone <= total_saved:
                    print(f"  ... {total_saved:,} / {total_expected:,} documents saved")

            # Fetch next batch
            resp = es.scroll(scroll_id=scroll_id, scroll=SCROLL_TTL)
            scroll_id = resp["_scroll_id"]
            hits = resp["hits"]["hits"]

        # Clear the scroll context
        try:
            es.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass  # Not critical

    # --- Summary ---
    file_size = os.path.getsize(NDJSON_PATH)
    file_size_mb = file_size / (1024 * 1024)

    print()
    print("=== Backup complete ===")
    print(f"  Total documents saved : {total_saved:,}")
    print(f"  Expected (index count): {total_expected:,}")
    print(f"  NDJSON file           : {NDJSON_PATH}")
    print(f"  File size             : {file_size_mb:.2f} MB ({file_size:,} bytes)")
    print(f"  Settings file         : {SETTINGS_PATH}")

    if total_saved != total_expected:
        print(
            f"WARNING: saved {total_saved:,} but index reported {total_expected:,}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
