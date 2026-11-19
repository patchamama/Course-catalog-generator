#!/usr/bin/env bash
# mdfromhtml — HTML to Markdown pipeline
#
# Pipeline per source folder:
#   Step 1  html-to-md.mjs       <source>/  →  _output/1_raw/
#   Step 2  build-collection.mjs <source>/  →  _output/2_collection/
#   Step 3  merge-book.mjs       _output/2_collection/  →  _output/book_full.md + _output/<source>.md
#
# Final: all book_full.md files merged into _all_books.md at project root
#
# Skip logic:
#   - _output/book_full.md exists → folder fully done, skip entirely (and sync _output/<source>.md)
#   - _output/2_collection/ has .md files → steps 1+2 done, resume at step 3
#   - _output/1_raw/ has .md files        → step 1 done, resume at step 2
#   - Folders with _ or . prefix, node_modules, or no root-level HTML are excluded
#
# Requirements: Node.js 18+, pandoc

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "mdfromhtml — HTML to Markdown pipeline"
echo "======================================="
echo ""

# ── Prerequisites ──────────────────────────────────────────────────────────────

echo "Checking prerequisites..."

if ! command -v node &>/dev/null; then
  echo "  ERROR: Node.js not found. Install 18+ from https://nodejs.org"
  exit 1
fi
node_major=$(node -e "process.stdout.write(process.versions.node.split('.')[0])")
if (( node_major < 18 )); then
  echo "  ERROR: Node.js 18+ required. Found: $(node --version)"
  exit 1
fi
echo "  Node.js $(node --version) ✓"

if ! command -v pandoc &>/dev/null; then
  echo "  ERROR: pandoc not found."
  echo "         macOS:   brew install pandoc"
  echo "         Ubuntu:  sudo apt install pandoc"
  echo "         Windows: https://github.com/jgm/pandoc/releases"
  exit 1
fi
echo "  $(pandoc --version | head -1) ✓"
echo ""

# ── Per-folder pipeline ────────────────────────────────────────────────────────

declare -a PROCESSED=()
declare -a RESUMED=()
declare -a SKIPPED=()
declare -a FAILED=()
declare -a ALL_BOOKS=()   # entries: "name|path_to_book_full.md"

for dir in */; do
  name="${dir%/}"

  # Exclude internal, hidden, and non-source dirs
  [[ "$name" == .* ]]           && continue
  [[ "$name" == _* ]]           && continue
  [[ "$name" == "node_modules" ]] && continue

  # Only process folders that have HTML files directly inside them
  shopt -s nullglob
  html_files=("${dir}"*.html)
  shopt -u nullglob
  [[ ${#html_files[@]} -eq 0 ]] && continue

  html_count=${#html_files[@]}
  src="${dir%/}"          # source folder (no trailing slash)
  OUT="$src/_output"
  STEP1="$OUT/1_raw"
  STEP2="$OUT/2_collection"
  BOOK="$OUT/book_full.md"
  NAMED_BOOK="$OUT/$name.md"

  # ── Fully done: skip ───────────────────────────────────────────────────────
  if [[ -f "$BOOK" ]]; then
    cp -f "$BOOK" "$NAMED_BOOK"
    printf "  %-60s [done]\n" "$name"
    SKIPPED+=("$name")
    ALL_BOOKS+=("$name|$BOOK")
    continue
  fi

  # ── Detect which steps are already done (resume support) ──────────────────
  step1_done=false
  step2_done=false
  shopt -s nullglob
  s1_mds=("$STEP1"/*.md)
  s2_mds=("$STEP2"/*.md)
  shopt -u nullglob
  [[ ${#s1_mds[@]} -gt 0 ]] && step1_done=true
  [[ ${#s2_mds[@]} -gt 0 ]] && step2_done=true
  is_resume=false
  ($step1_done || $step2_done) && is_resume=true

  echo "┌──────────────────────────────────────────────────────────────"
  echo "│  $name  ($html_count HTML files)"
  $is_resume && echo "│  Resuming from partial completion..."
  echo "└──────────────────────────────────────────────────────────────"

  mkdir -p "$STEP1" "$STEP2"

  # ── Step 1: HTML → raw Markdown ──────────────────────────────────────────
  # Input:  <source>/*.html
  # Output: _output/1_raw/*.md  (one per HTML, images in subfolders)
  if $step1_done; then
    echo "  [1/3] html-to-md ......... already done"
  else
    echo "  [1/3] html-to-md ..."
    if ! node html-to-md.mjs "$src" "$STEP1"; then
      echo "        FAILED — skipping folder"
      FAILED+=("$name (step 1: html-to-md)")
      continue
    fi
    echo "        → $STEP1"
  fi

  # ── Step 2: HTML → quality Markdown collection ───────────────────────────
  # Input:  <source>/*.html  (build-collection.mjs requires HTML, not MDs)
  # Output: _output/2_collection/*.md + content.md + book.md + 99.videos.md
  # Note:   This output feeds Step 3.
  if $step2_done; then
    echo "  [2/3] build-collection ... already done"
  else
    echo "  [2/3] build-collection ..."
    if ! node build-collection.mjs "$src" "$STEP2"; then
      echo "        FAILED — skipping folder"
      FAILED+=("$name (step 2: build-collection)")
      continue
    fi
    echo "        → $STEP2"
  fi

  # ── Step 3: quality Markdown → merged single book ────────────────────────
  # Input:  _output/2_collection/  (step 2 output)
  # Output: _output/book_full.md + _output/<source>.md + _output/images/ (consolidated)
  echo "  [3/3] merge-book ..."
  if ! node merge-book.mjs "$STEP2" \
      --output "$BOOK" \
      --images-dest "$OUT/images"; then
    echo "        FAILED — skipping folder"
    FAILED+=("$name (step 3: merge-book)")
    continue
  fi
  cp -f "$BOOK" "$NAMED_BOOK"
  echo "        → $BOOK"
  echo "        → $NAMED_BOOK"

  [[ -f "$STEP2/99.videos.md" ]] && echo "        → $STEP2/99.videos.md (video index)"
  echo ""

  $is_resume && RESUMED+=("$name") || PROCESSED+=("$name")
  ALL_BOOKS+=("$name|$BOOK")
done

# ── Final unified markdown ─────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"

total_books=${#ALL_BOOKS[@]}

if [[ $total_books -gt 0 ]]; then
  FINAL="_all_books.md"
  echo "  Building $FINAL  ($total_books book(s))..."
  {
    echo "# All Books"
    echo ""
    echo "> Generated: $(date '+%Y-%m-%d %H:%M:%S')  |  $total_books book(s)"
    echo ""
    echo "---"
  } > "$FINAL"

  for entry in "${ALL_BOOKS[@]}"; do
    bname="${entry%%|*}"
    bpath="${entry##*|}"
    {
      echo ""
      echo "# $bname"
      echo ""
      cat "$bpath"
      echo ""
      echo "---"
    } >> "$FINAL"
  done
  echo "  → $FINAL"
fi

echo ""
echo "  Processed:    ${#PROCESSED[@]}  (new)"
[[ ${#RESUMED[@]} -gt 0 ]] && echo "  Resumed:      ${#RESUMED[@]}  (partial → completed)"
[[ ${#SKIPPED[@]} -gt 0 ]] && echo "  Already done: ${#SKIPPED[@]}  (skipped)"
[[ ${#FAILED[@]}  -gt 0 ]] && {
  echo ""
  echo "  FAILED (${#FAILED[@]}):"
  for f in "${FAILED[@]}"; do echo "    ✗ $f"; done
}

echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Done."
