#!/usr/bin/env python3
"""
restore_es.py — Restore course_catalog index from backup files.

Reads:
  /tmp/es_backup_settings.json             — index mappings (settings ignored, defaults used)
  /tmp/es_backup_course_catalog.ndjson.gz  — compressed NDJSON produced by backup_es.py

Steps:
  1. Delete existing index (if present)
  2. Re-create index with saved mappings only (default settings)
  3. Bulk-import all documents in batches of 200, preserving _id
  4. Verify final count matches backup count
"""

import gzip
import json
import sys

import requests

ES_HOST = "http://127.0.0.1:9200"
INDEX = "course_catalog"
BATCH_SIZE = 200

NDJSON_PATH = "/tmp/es_backup_course_catalog.ndjson.gz"
SETTINGS_PATH = "/tmp/es_backup_settings.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def es_url(path: str) -> str:
    return f"{ES_HOST}{path}"


def check_response(resp: requests.Response, context: str = "") -> dict:
    """Raise a clear error if the response is not 2xx."""
    if not resp.ok:
        msg = f"ES error{' (' + context + ')' if context else ''}: " \
              f"HTTP {resp.status_code} — {resp.text[:400]}"
        print(msg, file=sys.stderr)
        sys.exit(1)
    return resp.json()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # --- Ping ---
    try:
        ping = requests.get(es_url("/"), timeout=10)
        ping.raise_for_status()
    except Exception as exc:
        print(f"ERROR: Cannot connect to Elasticsearch at {ES_HOST}: {exc}", file=sys.stderr)
        sys.exit(1)

    info = ping.json()
    print(f"Connected to Elasticsearch {info.get('version', {}).get('number', '?')} at {ES_HOST}")

    # --- Load saved mappings ---
    print(f"Loading mappings from {SETTINGS_PATH} ...")
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        saved = json.load(f)

    mappings = saved.get("mappings", {})
    if not mappings:
        print("ERROR: No 'mappings' key found in settings file.", file=sys.stderr)
        sys.exit(1)

    # --- Delete existing index ---
    exists_resp = requests.head(es_url(f"/{INDEX}"), timeout=10)
    if exists_resp.status_code == 200:
        print(f"Index '{INDEX}' exists — deleting ...")
        del_resp = requests.delete(es_url(f"/{INDEX}"), timeout=30)
        check_response(del_resp, "delete index")
        print(f"Index '{INDEX}' deleted.")
    else:
        print(f"Index '{INDEX}' does not exist — will create fresh.")

    # --- Create index with saved mappings ---
    print(f"Creating index '{INDEX}' with saved mappings ...")
    create_body = {"mappings": mappings}
    create_resp = requests.put(
        es_url(f"/{INDEX}"),
        headers={"Content-Type": "application/json"},
        data=json.dumps(create_body),
        timeout=30,
    )
    check_response(create_resp, "create index")
    print(f"Index '{INDEX}' created.")

    # --- Bulk import ---
    print(f"Reading documents from {NDJSON_PATH} ...")
    total_imported = 0
    batch = []

    def flush_batch(batch: list) -> int:
        """Send one bulk request; return number of successfully indexed docs."""
        if not batch:
            return 0

        # Build NDJSON bulk body
        lines = []
        for doc in batch:
            doc_id = doc.pop("_id")
            action = {"index": {"_index": INDEX, "_id": doc_id}}
            lines.append(json.dumps(action, ensure_ascii=False))
            lines.append(json.dumps(doc, ensure_ascii=False))

        body = "\n".join(lines) + "\n"
        resp = requests.post(
            es_url("/_bulk"),
            headers={"Content-Type": "application/x-ndjson"},
            data=body.encode("utf-8"),
            timeout=60,
        )
        result = check_response(resp, "bulk index")

        if result.get("errors"):
            # Log first error item for diagnosis
            for item in result.get("items", []):
                action_result = item.get("index", {})
                if action_result.get("error"):
                    print(
                        f"  Bulk error on _id={action_result.get('_id')}: "
                        f"{action_result['error']}",
                        file=sys.stderr,
                    )
                    break  # Only show first error; don't abort

        return len(batch)

    with gzip.open(NDJSON_PATH, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue

            try:
                doc = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"WARNING: Skipping malformed line: {exc}", file=sys.stderr)
                continue

            batch.append(doc)

            if len(batch) >= BATCH_SIZE:
                total_imported += flush_batch(batch)
                batch = []

                if total_imported % 2000 == 0:
                    print(f"  ... {total_imported:,} documents imported")

    # Flush remaining
    if batch:
        total_imported += flush_batch(batch)

    print()
    print(f"Import finished. {total_imported:,} documents sent to bulk API.")

    # --- Refresh and verify count ---
    print("Refreshing index ...")
    requests.post(es_url(f"/{INDEX}/_refresh"), timeout=30)

    count_resp = requests.get(es_url(f"/{INDEX}/_count"), timeout=30)
    count_data = check_response(count_resp, "count")
    actual_count = count_data["count"]

    print()
    print("=== Restore complete ===")
    print(f"  Documents imported (bulk API): {total_imported:,}")
    print(f"  Documents in index (verified): {actual_count:,}")

    if actual_count != total_imported:
        print(
            f"WARNING: count mismatch — bulk sent {total_imported:,} "
            f"but index has {actual_count:,}",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        print("  Count verified OK.")


if __name__ == "__main__":
    main()
