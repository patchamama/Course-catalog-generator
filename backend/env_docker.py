# env_docker.py — Docker configuration for catalog_server.py
# This file is automatically used inside Docker containers
# (mounted as env.py by docker-compose.yml).
# All values are read from environment variables set in docker-compose.yml / .env

import os

# ── Base path ─────────────────────────────────────────────────────────────────
SEARCH_BASE_PATH = os.environ.get('SEARCH_BASE_PATH', '/courses')

# ── Server settings ───────────────────────────────────────────────────────────
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '5000'))
DEBUG = os.environ.get('DEBUG', 'false').lower() in ('1', 'true', 'yes')

# ── Search settings ───────────────────────────────────────────────────────────
MAX_RESULTS = int(os.environ.get('MAX_RESULTS', '50'))
MAX_MATCHES_PER_FILE = int(os.environ.get('MAX_MATCHES_PER_FILE', '5'))
CONTEXT_LINES = int(os.environ.get('CONTEXT_LINES', '2'))
MAX_FILE_SIZE_MB = int(os.environ.get('MAX_FILE_SIZE_MB', '50'))

# ── VLC (not applicable in Docker, kept for API compatibility) ────────────────
VLC_PATH = os.environ.get('VLC_PATH', 'vlc')

# ── Elasticsearch ─────────────────────────────────────────────────────────────
ES_URL   = os.environ.get('ES_URL', 'http://elasticsearch:9200')
ES_INDEX = os.environ.get('ES_INDEX', 'course_catalog')

# ── Searchable extensions ─────────────────────────────────────────────────────
SEARCHABLE_EXTENSIONS = {
    "txt", "md", "csv", "srt", "vtt", "log",
    "js", "ts", "css", "html", "htm", "xml", "json",
    "py", "java", "cpp", "c", "h", "cs", "go", "rs", "rb",
    "php", "sh", "bash", "sql", "yaml", "yml", "toml", "ini",
    "pdf", "docx", "doc", "rtf",
    "xlsx", "xls",
    "pptx", "ppt",
    "odt",
}
