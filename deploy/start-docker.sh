#!/usr/bin/env bash
# =============================================================================
#  Course Catalog — Docker Setup & Launch  (Linux / macOS)
# =============================================================================
#  Starts the full Docker stack:
#    • Flask backend (catalog_server.py) on port 5000
#    • Elasticsearch 8.x              on port 9200
#    • (Optional) Kibana              on port 5601
#
#  Optionally indexes your course files into Elasticsearch for fast,
#  language-aware, and semantic search via /api/es-search.
#
#  Usage:
#    ./start-docker.sh                          (prompts for settings)
#    ./start-docker.sh "/path/to/courses"       (set courses path directly)
#    ./start-docker.sh --kibana                 (also start Kibana)
#    ./start-docker.sh --no-index               (skip ES indexing)
#    ./start-docker.sh --help
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colours ──────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; DIM=''; RESET=''
fi

header() { echo -e "\n${BOLD}${CYAN}▸ $1${RESET}"; }
ok()     { echo -e "  ${GREEN}✓${RESET}  $1"; }
warn()   { echo -e "  ${YELLOW}⚠${RESET}  $1"; }
info()   { echo -e "  ${DIM}$1${RESET}"; }
err()    { echo -e "\n  ${RED}✗  ERROR:${RESET} $1" >&2; exit 1; }
ask()    { echo -en "  ${BOLD}$1${RESET} "; }   # inline prompt label
sep()    { echo -e "${DIM}────────────────────────────────────────────────────${RESET}"; }

# ── Parse arguments ───────────────────────────────────────────────────────────
COURSES_PATH=""
WITH_KIBANA=false
DO_INDEX=true
DO_GENERATE=true

for arg in "$@"; do
  case "$arg" in
    --help|-h)
      echo "Usage: ./start-docker.sh [courses_path] [--kibana] [--no-index] [--no-generate]"
      echo ""
      echo "  courses_path    Path to your courses directory."
      echo "                  If omitted, read from .env or prompted."
      echo "  --kibana        Also start Kibana (ES management UI at :5601)."
      echo "  --no-index      Skip Elasticsearch indexing step."
      echo "  --no-generate   Skip catalog HTML generation (requires Python locally)."
      echo ""
      echo "Examples:"
      echo "  ./start-docker.sh"
      echo "  ./start-docker.sh \"/home/me/courses\""
      echo "  ./start-docker.sh \"/home/me/courses\" --kibana"
      exit 0
      ;;
    --kibana)      WITH_KIBANA=true ;;
    --no-index)    DO_INDEX=false ;;
    --no-generate) DO_GENERATE=false ;;
    -*)            warn "Unknown option: $arg" ;;
    *)             [ -z "$COURSES_PATH" ] && COURSES_PATH="$arg" ;;
  esac
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║          Course Catalog — Docker Setup               ║"
echo "  ║    Flask + Elasticsearch + Semantic Search           ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo -e "${RESET}"

# =============================================================================
# STEP 1 — Check Docker
# =============================================================================
header "Step 1 — Checking Docker"

command -v docker &>/dev/null || err "Docker is not installed or not in PATH.\nInstall Docker Desktop from: https://www.docker.com/products/docker-desktop/"

docker info &>/dev/null || err "Docker daemon is not running. Start Docker Desktop first."
ok "Docker: $(docker --version)"

# Prefer 'docker compose' (v2 plugin), fall back to 'docker-compose' (v1 standalone)
COMPOSE=""
if docker compose version &>/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
  COMPOSE="docker-compose"
else
  err "docker-compose / 'docker compose' not found.\nInstall Docker Desktop or: https://docs.docker.com/compose/install/"
fi
ok "Compose: $($COMPOSE version --short 2>/dev/null || $COMPOSE version)"

# =============================================================================
# STEP 2 — Configure courses path and .env
# =============================================================================
header "Step 2 — Configuration (.env)"

ENV_FILE="$SCRIPT_DIR/.env"

# Determine COURSES_PATH: CLI arg → .env file → prompt
if [ -z "$COURSES_PATH" ]; then
  # Try reading from existing .env
  if [ -f "$ENV_FILE" ]; then
    EXISTING=$(grep -E '^COURSES_PATH=' "$ENV_FILE" | tail -1 | sed 's/^COURSES_PATH=//' | tr -d '"' | tr -d "'") || true
    if [ -n "$EXISTING" ]; then
      info "Found in .env: COURSES_PATH=$EXISTING"
      ask "Keep this path? [Y/n]:"
      read -r KEEP
      if [[ "${KEEP:-y}" =~ ^[Nn]$ ]]; then
        ask "Enter new courses path:"
        read -r COURSES_PATH
      else
        COURSES_PATH="$EXISTING"
      fi
    fi
  fi
fi

if [ -z "$COURSES_PATH" ]; then
  ask "Enter the path to your courses directory:"
  read -r COURSES_PATH
fi

[ -z "$COURSES_PATH" ] && err "No courses path provided."

if [ ! -d "$COURSES_PATH" ]; then
  warn "Path '$COURSES_PATH' does not exist or is not accessible from this machine."
  ask "Continue anyway? [y/N]:"
  read -r CONFIRM
  [[ "${CONFIRM:-n}" =~ ^[Yy]$ ]] || err "Aborted."
fi

ok "Courses path: $COURSES_PATH"

# Set KEY=VALUE in a file without `sed -i` (GNU sed on Ubuntu takes `-i`, BSD sed on macOS
# needs `-i ''` — one script that must run on both) and without regex-escaping the value.
set_env_var() {
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp)"
  grep -v "^${key}=" "$file" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

# Write / update .env
if [ -f "$ENV_FILE" ]; then
  set_env_var "$ENV_FILE" COURSES_PATH "$COURSES_PATH"
  ok ".env updated"
else
  cp "$SCRIPT_DIR/.env.example" "$ENV_FILE" 2>/dev/null || cat > "$ENV_FILE" <<EOF
COURSES_PATH=$COURSES_PATH
PORT=5000
ES_PORT=9200
KIBANA_PORT=5601
DEBUG=false
MAX_RESULTS=50
MAX_MATCHES_PER_FILE=5
MAX_FILE_SIZE_MB=50
ES_INDEX=course_catalog
EOF
  set_env_var "$ENV_FILE" COURSES_PATH "$COURSES_PATH"
  ok ".env created"
fi

# =============================================================================
# STEP 3 — (Optional) Generate catalog HTML locally
# =============================================================================
if $DO_GENERATE; then
  header "Step 3 — Generating Course Catalog (optional)"
  PYTHON=""
  for cmd in python3 python python3.11 python3.10 python3.9; do
    command -v "$cmd" &>/dev/null && PYTHON="$cmd" && break
  done

  if [ -n "$PYTHON" ]; then
    VENV_DIR="$SCRIPT_DIR/.venv"
    if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/activate" ]; then
      # shellcheck source=/dev/null
      source "$VENV_DIR/bin/activate" 2>/dev/null || true
      info "Using virtual environment"
    fi
    if command -v python &>/dev/null && python -c "import flask" &>/dev/null 2>&1; then
      cd "$SCRIPT_DIR"
      python "$SCRIPT_DIR/../backend/catalog_generator.py" "$COURSES_PATH" --output "$SCRIPT_DIR/../output" \
        && ok "Catalog generated: output/course_catalog.html" \
        || warn "Catalog generation failed — open an existing course_catalog.html or run start.sh first"
    else
      warn "Dependencies not installed in local Python — skipping catalog generation."
      info "Run './start.sh' first (native) to generate the catalog, then use this script for Docker/ES."
    fi
  else
    warn "Python not found locally — skipping catalog generation."
    info "Run './start.sh' first (native) to generate the catalog."
  fi
else
  info "(Skipping catalog generation — --no-generate)"
fi

# =============================================================================
# STEP 4 — Build and start Docker services
# =============================================================================
header "Step 4 — Starting Docker Services"

cd "$SCRIPT_DIR"

COMPOSE_CMD="$COMPOSE up -d --build"
$WITH_KIBANA && COMPOSE_CMD="$COMPOSE --profile kibana up -d --build"

info "Running: $COMPOSE_CMD"
eval "$COMPOSE_CMD"

# Wait for Elasticsearch to be healthy
info "Waiting for Elasticsearch to be ready …"
MAX_WAIT=120
WAITED=0
while true; do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' course-catalog-es 2>/dev/null || echo "missing")
  if [ "$STATUS" = "healthy" ]; then
    ok "Elasticsearch is ready"
    break
  elif [ "$WAITED" -ge "$MAX_WAIT" ]; then
    warn "Elasticsearch health check timed out after ${MAX_WAIT}s. It may still be starting."
    break
  fi
  printf "  ."; sleep 5; WAITED=$((WAITED + 5))
done
echo ""

ok "Backend API available at: http://localhost:5000"
$WITH_KIBANA && ok "Kibana available at: http://localhost:5601" || true

# =============================================================================
# STEP 5 — Elasticsearch indexing
# =============================================================================
if $DO_INDEX; then
  header "Step 5 — Elasticsearch Indexing"

  # Check if index already has data
  DOC_COUNT=$(curl -sf "http://127.0.0.1:9200/course_catalog/_count" 2>/dev/null \
              | grep -o '"count":[0-9]*' | grep -o '[0-9]*') || DOC_COUNT="0"

  if [ "$DOC_COUNT" -gt 0 ]; then
    echo ""
    echo -e "  ${YELLOW}The Elasticsearch index already contains ${DOC_COUNT} document(s).${RESET}"
    echo ""
    ask "Reset the index and re-index all files from scratch? [y/N]:"
    read -r RESET_CHOICE

    if [[ "${RESET_CHOICE:-n}" =~ ^[Yy]$ ]]; then
      info "Re-indexing with --reset (deletes existing data) …"
      $COMPOSE run --rm indexer python backend/search_indexer.py --reset
    else
      ask "Run incremental index (add new files only)? [Y/n]:"
      read -r INC_CHOICE
      if [[ "${INC_CHOICE:-y}" =~ ^[Yy]$ ]]; then
        info "Running incremental indexer …"
        $COMPOSE run --rm indexer
      else
        info "Skipping indexing. Existing index will be used."
      fi
    fi
  else
    info "Index is empty — running full indexer now …"
    info "(This may take a few minutes depending on the number and size of files)"
    $COMPOSE run --rm indexer
  fi
  ok "Elasticsearch indexing complete"
else
  info "(Skipping indexing — --no-index)"
fi

# =============================================================================
# STEP 6 — Usage summary and test examples
# =============================================================================
header "Step 6 — Ready!"

sep
echo ""
echo -e "  ${BOLD}Services running:${RESET}"
echo ""
echo -e "  Backend API   →  ${CYAN}http://localhost:5000${RESET}"
echo -e "  Elasticsearch →  ${CYAN}http://127.0.0.1:9200${RESET}"
$WITH_KIBANA && echo -e "  Kibana        →  ${CYAN}http://localhost:5601${RESET}" || true
echo ""
echo -e "  ${BOLD}Catalog HTML  →${RESET}  $SCRIPT_DIR/course_catalog.html"
echo -e "  ${DIM}(open this file in your browser)${RESET}"
echo ""
sep
echo ""
echo -e "  ${BOLD}Test the backend API:${RESET}"
echo ""
echo -e "  ${CYAN}# Health check (shows ES status)${RESET}"
echo "  curl http://localhost:5000/api/health"
echo ""
echo -e "  ${CYAN}# Standard full-text search${RESET}"
echo "  curl 'http://localhost:5000/api/search?q=Installation'"
echo ""
echo -e "  ${CYAN}# ES full-text search (BM25, language-boosted)${RESET}"
echo "  curl 'http://localhost:5000/api/es-search?q=Installation&lang=de'"
echo ""
echo -e "  ${CYAN}# ES semantic search (kNN vector similarity)${RESET}"
echo "  curl 'http://localhost:5000/api/es-search?q=user+administration&semantic=1'"
echo ""
echo -e "  ${CYAN}# ES hybrid search (BM25 + semantic, Spanish)${RESET}"
echo "  curl 'http://localhost:5000/api/es-search?q=abreviatura&lang=es&semantic=1'"
echo ""
echo -e "  ${CYAN}# List all files for a search term${RESET}"
echo "  curl 'http://localhost:5000/api/es-search?q=workflow&limit=20'"
echo ""
echo -e "  ${CYAN}# Elasticsearch cluster info${RESET}"
echo "  curl 'http://127.0.0.1:9200/_cluster/health?pretty'"
echo ""
echo -e "  ${CYAN}# Count indexed documents${RESET}"
echo "  curl 'http://127.0.0.1:9200/course_catalog/_count?pretty'"
echo ""
sep
echo ""
echo -e "  ${BOLD}Docker management:${RESET}"
echo ""
echo "  $COMPOSE ps                    # show running services"
echo "  $COMPOSE logs -f backend       # follow backend logs"
echo "  $COMPOSE down                  # stop all services"
echo "  $COMPOSE down -v               # stop + delete ES data volume"
echo ""
if $WITH_KIBANA; then
  echo -e "  ${CYAN}Kibana:${RESET} open http://localhost:5601 in your browser"
  echo "        → Stack Management → Index Management → course_catalog"
  echo ""
fi
sep
echo ""
