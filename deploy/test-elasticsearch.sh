#!/usr/bin/env bash
# Backwards-compatibility wrapper — delegates to setup-dev.sh (which calls setup-dev.py).
# Original script name kept for existing users and documentation references.
#
# Supports all setup-dev.sh / setup-dev.py arguments:
#   ./test-elasticsearch.sh                              # interactive
#   ./test-elasticsearch.sh /path/to/courses             # incremental index
#   ./test-elasticsearch.sh --reset /path/to/courses     # wipe + full reindex
#   ./test-elasticsearch.sh --ext pdf,md /path           # filter by extension
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/setup-dev.sh" "$@"
