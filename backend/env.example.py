# env.example.py — template for backend/env.py (per-machine configuration)
#
# backend/env.py is created from this file automatically on the first start. It is NOT committed
# (it holds paths of your machine); edit it freely. Every setting is optional — anything missing
# falls back to the default shown here, and every setting can also be given as an environment variable.

import os

# --- Base path(s) -------------------------------------------------------------
# Folders that contain your courses / documents. Only files inside these folders can be served by
# the file viewer. Separate several with a semicolon (;). The Tools › "Index Directory" and
# "Saved folders" screens add and change entries here for you.
#
#   macOS / Linux:  SEARCH_BASE_PATH = "/Users/me/Courses;/data/Training"
#   Windows:        SEARCH_BASE_PATH = r"C:\Courses;D:\Training;\\server\share"
SEARCH_BASE_PATH = os.path.join(os.path.expanduser("~"), "Courses")

# --- Server -------------------------------------------------------------------
HOST = "127.0.0.1"   # "0.0.0.0" exposes the server to your network (export/import stays localhost-only)
PORT = 5000
DEBUG = False

# --- Search settings ----------------------------------------------------------
MAX_RESULTS = 50              # max files returned per search
MAX_MATCHES_PER_FILE = 5      # max matching lines shown per file
CONTEXT_LINES = 2             # lines of context around each match
MAX_FILE_SIZE_MB = 50         # skip files larger than this

# VLC (optional) — "Play" buttons on videos / subtitles. Leave unset to auto-detect.
# VLC_PATH = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
# VLC_PATH = "/Applications/VLC.app/Contents/MacOS/VLC"

# --- Elasticsearch ------------------------------------------------------------
# Use 127.0.0.1, not "localhost": on Windows "localhost" can resolve to ::1 first.
ES_URL   = "http://127.0.0.1:9200"
ES_INDEX = "course_catalog"

# Docker names used by Tools › "Export / Import index" (defaults match deploy/docker-compose.yml)
# ES_CONTAINER = "course-catalog-es"
# ES_COMPOSE_PROJECT = "deploy"
# EXPORT_DIR = "/path/to/toexport"        # where exports are written (default: <project>/toexport)

# --- Embedding model (semantic search) -----------------------------------------
# The query side and the index MUST use the same model; switching needs a re-index.
#   "intfloat/multilingual-e5-small"                  384 d  fast, multilingual (default)
#   "intfloat/multilingual-e5-base"                   768 d
#   "intfloat/multilingual-e5-large"                 1024 d  best quality — index it on a GPU
#   "BAAI/bge-m3"                                    1024 d  best quality, long context — GPU
#   "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"   384 d (CPU/ONNX)
#   "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"   768 d (CPU/ONNX)
#   "sentence-transformers/LaBSE"                     768 d
EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
# EMBEDDING_DEVICE = "auto"    # auto | cuda (NVIDIA) | mps (Apple-Silicon GPU) | cpu

# --- Indexing policy (all optional) ---------------------------------------------
# NO_VECTOR_EXTENSIONS = "json,xml,js,ts,css"   # BM25 only for these (no embeddings — much faster)
# MAX_VECTOR_CHUNKS_PER_FILE = 400              # bigger files are indexed BM25-only
# INDEX_EXCLUDE_PATTERNS = "sbom*.json;*/legacy/*"
# DE_DECOMPOUND_WORDS = "lernplattform,bildungsträger"   # extra German base words (needs a --reset re-index)

# --- RAG / answers ---------------------------------------------------------------
# Cross-encoder reranker (loads in the background on the first question)
# RERANK_ENABLED = True
# RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"   # light; "BAAI/bge-reranker-v2-m3" needs a GPU
# RERANK_CANDIDATES = 25
# The question's search terms are also translated into the language of your documents:
# RAG_TRANSLATE_QUERY_TO = "de"      # "de,en", or "" to disable

# --- Translation / canonical language (optional) ---------------------------------
CANONICAL_LANGUAGE = ""            # "en" | "es" | "de" | "" (disabled)
TRANSLATION_MODEL = "translategemma:4b"
TRANSLATION_MODE = "disabled"      # "background" | "inline" | "disabled"

# --- Ollama -------------------------------------------------------------------------
OLLAMA_URL = "http://127.0.0.1:11434"
