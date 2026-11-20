#!/usr/bin/env python3
"""
Course Catalog Backend Server
Provides REST API for full-text search and file serving for the Course Catalog HTML frontend.

Usage:
    python backend/catalog_server.py [--host HOST] [--port PORT] [--path PATH]

See CATALOG_SERVER_README.md for full documentation.
"""

# Required: flask, flask-cors
# Optional (improves extraction): PyMuPDF (fitz), python-docx, openpyxl, python-pptx, striprtf, beautifulsoup4, chardet

import os
# The Hugging Face 'xet' downloader can stall at 0 B/s on some networks; the plain HTTP one is reliable.
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
if hasattr(os, 'uname') and os.uname().sysname == 'Darwin':
    # faiss-cpu, torch and scipy each ship their own libomp; loaded together they segfault in an OpenMP worker.
    os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
    # ...and torch/scipy/faiss together still crash when OpenMP spawns worker threads: use none (override with OMP_NUM_THREADS).
    os.environ.setdefault('OMP_NUM_THREADS', '1')
import sys
import re
import json
import time
import threading
from datetime import datetime, timezone
import logging
import argparse
import mimetypes
import shutil
import subprocess
from pathlib import Path
from functools import lru_cache
from typing import Optional

# ─── Multilingual / Phonetic Search ─────────────────────────────────────────
# Normalization rules per language (applied in order, always after lowercasing)
_LANG_NORMS: dict = {
    'de': [
        # Trigraphs / digraphs first
        ('tsch', 't'), ('sch', 's'), ('ph', 'f'), ('ch', 'x'), ('ck', 'k'),
        ('dt', 't'), ('th', 't'),
        # Umlauts from digraphs (ae→ä→a, oe→ö→o, ue→ü→u)
        ('ae', 'a'), ('oe', 'o'), ('ue', 'u'),
        # Umlauts direct
        ('ä', 'a'), ('ö', 'o'), ('ü', 'u'),
        # Other phonetic equivalents
        ('ß', 's'), ('ss', 's'),
        ('v', 'f'), ('w', 'v'), ('y', 'i'),
    ],
    'es': [
        ('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u'),
        ('ü', 'u'), ('ñ', 'n'), ('ll', 'y'), ('rr', 'r'),
    ],
    'en': [
        ('ph', 'f'), ('wh', 'w'),
    ],
}

# Soundex code table: a=0,b=1,c=2,d=3,e=0,f=1,g=2,h=0,i=0,j=2,k=2,l=4,
#                     m=5,n=5,o=0,p=1,q=2,r=6,s=2,t=3,u=0,v=1,w=0,x=2,y=0,z=2
_SDXMAP = str.maketrans('abcdefghijklmnopqrstuvwxyz', '01230120022455012623010202')

def _norm(text: str, lang: str) -> str:
    """Lowercase + apply language-specific character substitutions."""
    text = text.lower()
    for old, new in _LANG_NORMS.get(lang, []):
        text = text.replace(old, new)
    return text

def _soundex(word: str) -> str:
    """Return 4-char Soundex code for a single word."""
    if not word:
        return '0000'
    word = word.lower()
    first = word[0]
    coded = word.translate(_SDXMAP)
    prev = coded[0]
    result = first
    for c in coded[1:]:
        if c != '0' and c != prev:
            result += c
            if len(result) == 4:
                break
        if c != '0':
            prev = c
    return result.ljust(4, '0')[:4]

def _sdx_tokens(text: str) -> set:
    """Return Soundex codes for every word in text."""
    return {_soundex(w) for w in re.findall(r'[a-z]+', text.lower()) if w}

def _levenshtein(s1: str, s2: str) -> int:
    """Levenshtein edit distance between two strings (single-row DP)."""
    if not s1: return len(s2)
    if not s2: return len(s1)
    m, n = len(s1), len(s2)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            tmp = dp[j]
            dp[j] = prev if s1[i-1] == s2[j-1] else 1 + min(prev, dp[j], dp[j-1])
            prev = tmp
    return dp[n]

def _fuzzy_word_match(qw: str, lw: str) -> bool:
    """True if lw is within edit distance of qw.
    Max allowed distance = max(1, len(qw) // 4), so:
      len  4-7  → max_dist 1
      len  8-11 → max_dist 2
      len 12-15 → max_dist 3  …
    Length difference alone can rule out a match (early exit).
    Examples:
      'avreviatura'(11) vs 'abreviatura'(11) → edist 1 ≤ 2  → match ✓
      'avreviatura'(11) vs 'abrufen'(7)      → |11-7|=4 > 2 → reject ✓
      'avreviatura'(11) vs 'aufrufen'(8)     → |11-8|=3 > 2 → reject ✓
    """
    max_dist = max(1, len(qw) // 4)
    if abs(len(qw) - len(lw)) > max_dist:
        return False
    return _levenshtein(qw, lw) <= max_dist

# ─── VLC path resolution ─────────────────────────────────────────────────────

def _resolve_vlc_path(configured: str) -> str:
    """Return the best usable VLC executable path.

    Resolution order:
      1. configured path as-is (already correct)
      2. If running in WSL and configured path is a Windows path (C:\\...),
         convert it to /mnt/c/... using wslpath
      3. 'vlc' found anywhere in PATH
      4. Common install locations (Windows-on-WSL, Linux)

    Always returns *something* — if nothing works the original string is
    returned so the error at launch time is still informative.
    """
    import shutil

    def _exists(p: str) -> bool:
        try:
            return Path(p).is_file()
        except (ValueError, OSError):
            return False

    # 1. Configured path works already
    if _exists(configured):
        return configured

    # 2. WSL: convert Windows-style path (C:\...) to /mnt/c/...
    is_wsl = False
    try:
        is_wsl = 'microsoft' in Path('/proc/version').read_text().lower()
    except OSError:
        pass

    if is_wsl and len(configured) >= 3 and configured[1] == ':':
        try:
            result = subprocess.run(
                ['wslpath', '-u', configured],
                capture_output=True, text=True, timeout=3,
            )
            wsl_path = result.stdout.strip()
            if wsl_path and _exists(wsl_path):
                return wsl_path
        except Exception:
            pass

    # 3. 'vlc' / 'vlc.exe' anywhere in PATH
    for name in ('vlc', 'vlc.exe'):
        found = shutil.which(name)
        if found:
            return found

    # 4. Common install locations
    candidates = []
    if is_wsl or sys.platform == 'win32':
        candidates += [
            '/mnt/c/Program Files/VideoLAN/VLC/vlc.exe',
            '/mnt/c/Program Files (x86)/VideoLAN/VLC/vlc.exe',
            '/mnt/c/Portable/VLCPortable/VLCPortable.exe',
            '/mnt/t/Entwicklung/03_Schulungen/ELO/_tools/VLCPortable/VLCPortable.exe',
        ]
    candidates += [
        '/usr/bin/vlc',
        '/usr/local/bin/vlc',
        '/snap/bin/vlc',
        '/Applications/VLC.app/Contents/MacOS/VLC',  # macOS
    ]
    for c in candidates:
        if _exists(c):
            return c

    # Nothing found — return original so the 500 error stays informative
    return configured


def _is_wsl() -> bool:
    """Return True when running inside Windows Subsystem for Linux."""
    try:
        return 'microsoft' in Path('/proc/version').read_text().lower()
    except OSError:
        return False


def _to_windows_path(p: Path) -> str:
    """Convert a WSL/Linux path to a Windows path string for use as a VLC argument.

    VLC runs as a native Windows process, so it needs Windows-style paths
    (e.g. C:\\Users\\...) even when it is launched from WSL.
    Uses `wslpath -w` which is always available in WSL environments.
    Falls back to the original string if conversion fails.
    """
    try:
        result = subprocess.run(
            ['wslpath', '-w', str(p)],
            capture_output=True, text=True, timeout=3,
        )
        win = result.stdout.strip()
        if win:
            return win
    except Exception:
        pass
    return str(p)


# ─── SRT / VLC helpers ───────────────────────────────────────────────────────

_SRT_TS_RE = re.compile(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->')
_VIDEO_EXTS = ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'm4v', 'webm', 'flv', 'ts', 'mpg', 'mpeg']

def _parse_srt_timestamps(lines: list) -> dict:
    """Return {1-based-line-number: start_seconds} for text lines in an SRT file."""
    line_to_time: dict = {}
    current_time = None
    in_text = False
    for i, line in enumerate(lines):
        m = _SRT_TS_RE.search(line)
        if m:
            h, mn, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            current_time = h * 3600 + mn * 60 + s + ms / 1000.0
            in_text = True
        elif in_text and line.strip():
            line_to_time[i + 1] = current_time  # 1-based line number
        elif not line.strip():
            in_text = False
    return line_to_time

def _find_video_for_srt(srt_path: Path) -> Optional[Path]:
    """Find the video file that corresponds to an SRT subtitle file.

    Strategy (in order):
    1. Exact stem match (video.srt → video.mp4)
    2. Strip trailing language code (_es, _de, _en, …) and try exact match
    3. Progressively trim characters from the right of the stem and look for
       any video in the folder whose name *starts with* that prefix.
       This handles cases like 'My Video_[ID]_es.srt' → 'My Video_[ID].mp4'
       even when the suffix pattern doesn't match exactly.
    Searches same directory first, then parent directory.
    Minimum prefix length is 5 characters to avoid false positives.
    """
    stem = srt_path.stem

    # Strip known language suffix (_es, _de, _en, _fr, _pt, _it, …)
    lang_match = re.search(r'[._-][a-z]{2,3}$', stem, re.IGNORECASE)
    base_stem = stem[:lang_match.start()] if lang_match else stem

    for directory in [srt_path.parent, srt_path.parent.parent]:
        # Collect all video files in this directory once
        try:
            video_files = [
                f for f in directory.iterdir()
                if f.is_file() and f.suffix.lstrip('.').lower() in _VIDEO_EXTS
            ]
        except OSError:
            continue

        if not video_files:
            continue

        # 1 & 2 — exact match on full stem and base stem
        for s in ([stem, base_stem] if base_stem != stem else [stem]):
            for vf in video_files:
                if vf.stem == s:
                    return vf

        # 3 — progressively shorten base_stem from the right until a video
        #     whose stem *starts with* that prefix is found
        for length in range(len(base_stem), 4, -1):
            prefix = base_stem[:length]
            for vf in video_files:
                if vf.stem.startswith(prefix):
                    return vf

    return None


# YouTube ID: exactly 11 alphanumeric/dash/underscore chars inside square brackets
_YT_ID_RE = re.compile(r'\[([A-Za-z0-9_-]{11})\]')

def _extract_youtube_id(path: Path) -> Optional[str]:
    """Return YouTube video ID embedded in a filename like 'Title [CXpLkCD2lMU].srt'."""
    m = _YT_ID_RE.search(path.name)
    return m.group(1) if m else None


try:
    from flask import Flask, request, jsonify, send_file, send_from_directory, abort, Response, stream_with_context
    from flask_cors import CORS
except ImportError:
    print("ERROR: Flask not installed. Run: pip install flask flask-cors")
    sys.exit(1)

# Load config from env.py
def _safe_exists(p) -> bool:
    """Path.exists() that never raises. On Windows a network share that is offline or not permitted
    for this account raises PermissionError / OSError (WinError 53, 5…) instead of returning False,
    which used to crash the server at startup and the "search paths" list."""
    try:
        return Path(p).exists()
    except OSError:
        return False


def _parse_base_paths(raw: str) -> list:
    """Parse a semicolon-separated path string into a list of resolved Paths.
    Automatically converts Windows-style paths (C:\\...) to WSL paths when running in WSL.
    """
    paths = []
    for p in str(raw).split(';'):
        p = p.strip()
        if not p:
            continue
        # Convert Windows path to WSL path when running inside WSL
        if re.match(r'^[A-Za-z]:[/\\]', p):
            try:
                res = subprocess.run(['wslpath', '-u', p],
                                     capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and res.stdout.strip():
                    p = res.stdout.strip()
            except Exception:
                pass
        paths.append(Path(p).resolve())
    return paths

# First run: backend/env.py is per-machine configuration (not committed) — create it from the template.
_ENV_PY = Path(__file__).parent / 'env.py'
if not _ENV_PY.exists() and (_ENV_PY.parent / 'env.example.py').exists():
    try:
        import shutil as _shutil
        _shutil.copyfile(_ENV_PY.parent / 'env.example.py', _ENV_PY)
        print(f"  Created {_ENV_PY} from env.example.py — edit it to match your machine.", flush=True)
    except OSError as _e:
        print(f"  WARNING: could not create env.py ({_e}); using defaults.", flush=True)

try:
    import env
    _SEARCH_BASE_RAW = str(getattr(env, 'SEARCH_BASE_PATH', '.'))
    SEARCH_BASE_PATHS = _parse_base_paths(_SEARCH_BASE_RAW)
    SEARCH_BASE_PATH  = SEARCH_BASE_PATHS[0] if SEARCH_BASE_PATHS else Path('.').resolve()
    HOST = getattr(env, 'HOST', '0.0.0.0')
    PORT = getattr(env, 'PORT', 5000)
    DEBUG = getattr(env, 'DEBUG', False)
    MAX_RESULTS = getattr(env, 'MAX_RESULTS', 50)
    MAX_MATCHES_PER_FILE = getattr(env, 'MAX_MATCHES_PER_FILE', 5)
    CONTEXT_LINES = getattr(env, 'CONTEXT_LINES', 2)
    MAX_FILE_SIZE_MB = getattr(env, 'MAX_FILE_SIZE_MB', 50)
    SEARCHABLE_EXTENSIONS = getattr(env, 'SEARCHABLE_EXTENSIONS', set())
    VLC_PATH = getattr(env, 'VLC_PATH', 'vlc')
    ES_URL     = getattr(env, 'ES_URL',     os.environ.get('ES_URL', ''))
    ES_INDEX   = getattr(env, 'ES_INDEX',   os.environ.get('ES_INDEX', 'course_catalog'))
    OLLAMA_URL = getattr(env, 'OLLAMA_URL', os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434'))
    CANONICAL_LANGUAGE = getattr(env, 'CANONICAL_LANGUAGE', os.environ.get('CANONICAL_LANGUAGE', ''))
    TRANSLATION_MODEL  = getattr(env, 'TRANSLATION_MODEL',  os.environ.get('TRANSLATION_MODEL', 'translategemma:4b'))
    TRANSLATION_MODE   = getattr(env, 'TRANSLATION_MODE',   os.environ.get('TRANSLATION_MODE', 'background'))
except ImportError:
    SEARCH_BASE_PATHS = [Path('.').resolve()]
    SEARCH_BASE_PATH  = SEARCH_BASE_PATHS[0]
    HOST = '0.0.0.0'
    PORT = 5000
    DEBUG = False
    MAX_RESULTS = 50
    MAX_MATCHES_PER_FILE = 5
    CONTEXT_LINES = 2
    MAX_FILE_SIZE_MB = 50
    SEARCHABLE_EXTENSIONS = set()
    VLC_PATH = 'vlc'
    ES_URL     = os.environ.get('ES_URL', '')
    ES_INDEX   = os.environ.get('ES_INDEX', 'course_catalog')
    OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')
    CANONICAL_LANGUAGE = os.environ.get('CANONICAL_LANGUAGE', '')
    TRANSLATION_MODEL  = os.environ.get('TRANSLATION_MODEL', 'translategemma:4b')
    TRANSLATION_MODE   = os.environ.get('TRANSLATION_MODE', 'background')

# Resolve VLC path: convert Windows paths on WSL, auto-discover if not found
VLC_PATH = _resolve_vlc_path(VLC_PATH)

# Default searchable extensions if not configured
if not SEARCHABLE_EXTENSIONS:
    SEARCHABLE_EXTENSIONS = {
        "txt","md","html","htm","xml","json","csv","srt","vtt",
        "js","ts","css","py","java","cpp","c","h","cs","go",
        "rs","rb","php","sh","bash","sql","yaml","yml","toml","ini",
        "pdf","docx","doc","xlsx","xls","pptx","ppt","rtf","odt",
        "log","conf","config","properties",
    }

# Cache directory — sibling to this script
CACHE_DIR         = Path(__file__).parent / 'cache_temporal'
FAISS_INDEX_DIR   = CACHE_DIR / 'faiss_index'
FAISS_INDEX_PATH  = FAISS_INDEX_DIR / 'index.faiss'
FAISS_CHUNKS_PATH = FAISS_INDEX_DIR / 'chunks.json'
_FAISS_CHUNK_SIZE = 1000  # chars per chunk when building FAISS index

# Extension -> language name for syntax highlighting
LANG_MAP = {
    "js": "javascript", "ts": "typescript", "py": "python",
    "java": "java", "cpp": "cpp", "c": "c", "h": "c",
    "cs": "csharp", "go": "go", "rs": "rust", "rb": "ruby",
    "php": "php", "sh": "bash", "bash": "bash", "sql": "sql",
    "json": "json", "xml": "xml", "yaml": "yaml", "yml": "yaml",
    "toml": "toml", "css": "css", "html": "html", "htm": "html",
    "md": "markdown", "txt": "plaintext", "srt": "plaintext",
}

app = Flask(__name__)
CORS(app)

@app.errorhandler(400)
def _err400(e): return jsonify({"error": "Bad request", "detail": str(e)}), 400
@app.errorhandler(403)
def _err403(e): return jsonify({"error": "Forbidden"}), 403
@app.errorhandler(404)
def _err404(e): return jsonify({"error": "Not found"}), 404
@app.errorhandler(500)
def _err500(e): return jsonify({"error": "Internal server error", "detail": str(e)}), 500

# Project root (one level up from backend/)
_PROJECT_ROOT = Path(__file__).parent.parent

# ─── Optional: Flask-SocketIO (WebSocket streaming search) ──────────────────
_socketio_available = False
try:
    from flask_socketio import SocketIO
    socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')
    _socketio_available = True
except ImportError:
    socketio = None
    pass  # log not yet available — message printed in main()

_cancel_flags: dict = {}  # sid → threading.Event

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# --- Text Extraction ----------------------------------------------------------

def detect_encoding(path: str) -> str:
    """Detect file encoding, falling back to utf-8."""
    try:
        import chardet
        with open(path, 'rb') as f:
            raw = f.read(min(32768, os.path.getsize(path)))
        result = chardet.detect(raw)
        return result.get('encoding') or 'utf-8'
    except Exception:
        return 'utf-8'

def read_text_file(path: str) -> Optional[str]:
    """Read a plain text file with encoding detection."""
    try:
        enc = detect_encoding(path)
        with open(path, encoding=enc, errors='replace') as f:
            return f.read()
    except Exception as e:
        log.debug(f"Cannot read text file {path}: {e}")
        return None

def extract_pdf(path: str) -> Optional[str]:
    """Extract text from PDF."""
    # Try PyMuPDF
    try:
        import fitz
        doc = fitz.open(path)
        return '\n'.join(page.get_text() for page in doc)
    except ImportError:
        pass
    except Exception as e:
        log.debug(f"PyMuPDF failed for {path}: {e}")

    # Try pdfminer
    try:
        import pdfminer.high_level
        return pdfminer.high_level.extract_text(path)
    except ImportError:
        pass
    except Exception as e:
        log.debug(f"pdfminer failed for {path}: {e}")

    # Try pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return '\n'.join(p.extract_text() or '' for p in pdf.pages)
    except ImportError:
        pass
    except Exception as e:
        log.debug(f"pdfplumber failed for {path}: {e}")

    log.warning(f"No PDF extraction library available for {path}")
    return None

def extract_pdf_with_pages(path: str) -> Optional[list]:
    """Extract text per page from PDF using PyMuPDF. Returns [(page_num, text), ...] or None."""
    try:
        import fitz
        doc = fitz.open(path)
        return [(i + 1, page.get_text()) for i, page in enumerate(doc)]
    except (ImportError, Exception):
        return None


def search_pdf_with_pages(path: str, query: str, max_matches: int,
                           lang: str = '', use_soundex: bool = False,
                           case_sensitive: bool = False) -> list:
    """Search a PDF and include page numbers in match results."""
    pages = extract_pdf_with_pages(path)
    if pages is None:
        text = extract_pdf(path)
        if not text:
            return []
        return search_in_text(text.splitlines(), query, max_matches, lang, use_soundex, case_sensitive)
    results = []
    for page_num, page_text in pages:
        if len(results) >= max_matches:
            break
        page_matches = search_in_text(
            page_text.splitlines(), query, max_matches - len(results),
            lang, use_soundex, case_sensitive)
        for m in page_matches:
            m['page'] = page_num
        results.extend(page_matches)
    return results


def extract_docx(path: str) -> Optional[str]:
    """Extract text from DOCX."""
    try:
        from docx import Document
        doc = Document(path)
        parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        parts.append(cell.text)
        return '\n'.join(parts)
    except ImportError:
        log.debug("python-docx not installed")
        return None
    except Exception as e:
        log.debug(f"DOCX extraction failed for {path}: {e}")
        return None

def extract_xlsx(path: str) -> Optional[str]:
    """Extract text from XLSX."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts.append(f"[Sheet: {sheet_name}]")
            for row in ws.iter_rows():
                row_vals = [str(cell.value) for cell in row if cell.value is not None]
                if row_vals:
                    parts.append('\t'.join(row_vals))
        return '\n'.join(parts)
    except ImportError:
        log.debug("openpyxl not installed")
        return None
    except Exception as e:
        log.debug(f"XLSX extraction failed for {path}: {e}")
        return None

def extract_pptx(path: str) -> Optional[str]:
    """Extract text from PPTX."""
    try:
        from pptx import Presentation
        prs = Presentation(path)
        parts = []
        for i, slide in enumerate(prs.slides, 1):
            parts.append(f"[Slide {i}]")
            for shape in slide.shapes:
                if hasattr(shape, 'text') and shape.text.strip():
                    parts.append(shape.text)
        return '\n'.join(parts)
    except ImportError:
        log.debug("python-pptx not installed")
        return None
    except Exception as e:
        log.debug(f"PPTX extraction failed for {path}: {e}")
        return None

def extract_rtf(path: str) -> Optional[str]:
    """Extract text from RTF."""
    try:
        from striprtf.striprtf import rtf_to_text
        with open(path, encoding='utf-8', errors='replace') as f:
            return rtf_to_text(f.read())
    except ImportError:
        pass
    # Fallback: basic RTF stripping
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            content = f.read()
        content = re.sub(r'\\[a-z]+\-?\d*\s?', ' ', content)
        content = re.sub(r'\{|\}|\\', ' ', content)
        return re.sub(r'\s+', ' ', content).strip()
    except Exception as e:
        log.debug(f"RTF extraction failed for {path}: {e}")
        return None

def extract_html_text(path: str) -> Optional[str]:
    """Extract text from HTML file."""
    raw = read_text_file(path)
    if raw is None:
        return None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw, 'html.parser')
        return soup.get_text(separator='\n')
    except ImportError:
        # Fallback: strip tags
        return re.sub(r'<[^>]+>', ' ', raw)

def extract_text(path: str, ext: str) -> Optional[str]:
    """Extract text from file based on extension."""
    ext = ext.lower().lstrip('.')

    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        log.debug(f"Skipping {path}: too large ({size_mb:.1f}MB)")
        return None

    if ext == 'pdf':
        return extract_pdf(path)
    elif ext in ('docx',):
        return extract_docx(path)
    elif ext in ('doc',):
        # Try docx2txt fallback
        try:
            import docx2txt
            return docx2txt.process(path)
        except ImportError:
            return read_text_file(path)
    elif ext in ('xlsx', 'xls'):
        return extract_xlsx(path)
    elif ext in ('pptx', 'ppt'):
        return extract_pptx(path)
    elif ext == 'rtf':
        return extract_rtf(path)
    elif ext in ('html', 'htm'):
        return extract_html_text(path)
    elif ext in ('xml',):
        raw = read_text_file(path)
        if raw:
            return re.sub(r'<[^>]+>', ' ', raw)
        return None
    else:
        # Plain text / code
        return read_text_file(path)

# --- Cache helpers -----------------------------------------------------------

def _cache_path(fpath: Path, suffix: str) -> Path:
    """Return cache file path mirroring fpath structure under CACHE_DIR."""
    rel = None
    for base in SEARCH_BASE_PATHS:
        try:
            rel = fpath.relative_to(base)
            break
        except ValueError:
            continue
    if rel is None:
        rel = Path(fpath.name)
    cp = CACHE_DIR / rel
    return cp.with_suffix(cp.suffix + suffix)

def get_cache_path(fpath: Path) -> Path:
    """Return path for the cached plain-text file of fpath."""
    return _cache_path(fpath, '.txt')

def get_pdf_cache_path(fpath: Path) -> Path:
    """Return path for the cached PDF pages JSON file."""
    return _cache_path(fpath, '.json')

def _is_fresh(cache_path: Path, source: Path) -> bool:
    """True if cache_path exists and is newer than source."""
    try:
        return cache_path.exists() and cache_path.stat().st_mtime >= source.stat().st_mtime
    except Exception:
        return False

def _write_cache_file(cp: Path, content: str) -> bool:
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(content, encoding='utf-8')
        return True
    except Exception:
        return False

# ── Plain-text cache ──────────────────────────────────────────────────────────

def read_from_cache(fpath: Path) -> Optional[str]:
    """Return cached text if cache is fresh, else None."""
    cp = get_cache_path(fpath)
    if not _is_fresh(cp, fpath):
        return None
    try:
        return cp.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return None

def write_to_cache(fpath: Path, text: str) -> bool:
    return _write_cache_file(get_cache_path(fpath), text)

# ── PDF page cache (JSON) ─────────────────────────────────────────────────────

def read_pdf_cache(fpath: Path) -> Optional[list]:
    """Return cached PDF pages as [(page_num, text), ...] if fresh, else None."""
    cp = get_pdf_cache_path(fpath)
    if not _is_fresh(cp, fpath):
        return None
    try:
        data = json.loads(cp.read_text(encoding='utf-8'))
        return [(item[0], item[1]) for item in data]
    except Exception:
        return None

def write_pdf_cache(fpath: Path, pages: list) -> bool:
    """Write PDF pages [(page_num, text), ...] as JSON cache."""
    return _write_cache_file(get_pdf_cache_path(fpath), json.dumps(pages, ensure_ascii=False))

def _extract_pdf_pages_for_cache(path: str) -> Optional[list]:
    """Extract PDF as [(page_num, text), ...] using best available library."""
    pages = extract_pdf_with_pages(path)
    if pages:
        return pages
    # Fallback: no page-level library — store entire text as page 1
    text = extract_pdf(path)
    if text:
        return [(1, text)]
    return None

def search_pdf_from_cache(pages: list, query: str, max_matches: int,
                           lang: str = '', use_soundex: bool = False,
                           case_sensitive: bool = False) -> list:
    """Search pre-cached PDF pages, preserving page numbers."""
    results = []
    for page_num, page_text in pages:
        if len(results) >= max_matches:
            break
        page_matches = search_in_text(
            page_text.splitlines(), query, max_matches - len(results),
            lang, use_soundex, case_sensitive)
        for m in page_matches:
            m['page'] = page_num
        results.extend(page_matches)
    return results

# --- Search ------------------------------------------------------------------

def search_in_text(lines: list, query: str, max_matches: int,
                   lang: str = '', use_soundex: bool = False,
                   case_sensitive: bool = False) -> list:
    """Find query in lines. With no lang/soundex options behaves as before.
    lang     — 'de'/'es'/'en': apply language normalization before comparing.
    soundex  — True: word-level phonetic (Soundex) matching.
    case_sensitive — True: exact case match (only when lang='' and soundex=False).
    """
    results = []
    n = len(lines)

    # ── Simple mode (no lang, no soundex) ───────────────────────────────────
    if not lang and not use_soundex:
        cmp_q = query if case_sensitive else query.lower()
        for i, line in enumerate(lines):
            cmp_l = line if case_sensitive else line.lower()
            if cmp_q in cmp_l:
                ctx_start = max(0, i - CONTEXT_LINES)
                ctx_end = min(n, i + CONTEXT_LINES + 1)
                context = ' ... '.join(l.strip() for l in lines[ctx_start:ctx_end] if l.strip())
                results.append({"line": i + 1, "context": context[:400], "highlight": query})
                if len(results) >= max_matches:
                    break
        return results

    # ── Advanced mode (normalization and/or soundex) ─────────────────────────
    # Soundex requires a language — without language context phonetic rules
    # are meaningless and produce many cross-language false positives.
    if use_soundex and not lang:
        use_soundex = False

    norm_q = _norm(query, lang)

    # Pre-compute query tokens once (soundex code + word, for words >= 3 chars)
    # Pre-compute query words for fuzzy matching (words >= 4 chars)
    if use_soundex:
        q_words = [w for w in re.findall(r'[a-z]+', norm_q) if len(w) >= 4]

    for i, line in enumerate(lines):
        norm_l = _norm(line, lang)
        if use_soundex:
            # 1. Direct normalized substring match (exact)
            if norm_q in norm_l:
                matched = True
            # 2. Fuzzy (edit-distance) word match after language normalization.
            #    Tolerates typos/misspellings proportional to word length.
            #    "avreviatura" vs "abreviatura" → edist 1 ≤ 2 → match ✓
            #    "avreviatura" vs "abrufen"     → len diff 4 > 2 → reject ✓
            elif q_words:
                l_words = [w for w in re.findall(r'[a-z]+', norm_l) if len(w) >= 4]
                matched = any(
                    _fuzzy_word_match(qw, lw)
                    for qw in q_words
                    for lw in l_words
                )
            else:
                matched = False
        else:
            matched = norm_q in norm_l  # substring on normalised text
        if matched:
            ctx_start = max(0, i - CONTEXT_LINES)
            ctx_end = min(n, i + CONTEXT_LINES + 1)
            context = ' ... '.join(l.strip() for l in lines[ctx_start:ctx_end] if l.strip())
            results.append({"line": i + 1, "context": context[:400], "highlight": query})
            if len(results) >= max_matches:
                break

    return results

def get_rel_path(abs_path: Path) -> str:
    """Get path relative to the matching SEARCH_BASE_PATHS entry."""
    for base in SEARCH_BASE_PATHS:
        try:
            return str(abs_path.relative_to(base))
        except ValueError:
            continue
    return str(abs_path)

# --- Routes ------------------------------------------------------------------

def _run_search(query: str, lang: str, use_soundex: bool, case_sensitive: bool,
                use_cached: bool, filter_exts: set, limit: int,
                on_progress, on_result, cancel) -> dict:
    """Core search loop. Calls on_progress(idx, total, name) per folder, on_result(result) per match.
    cancel: threading.Event — checked before each file; stops early if set."""
    total_matches = 0
    result_count = 0
    t_start = time.perf_counter()

    # Collect top-level dirs across all base paths
    _top_dirs = []
    for _bp in SEARCH_BASE_PATHS:
        try:
            _top_dirs.extend(sorted(d for d in os.listdir(_bp)
                                    if not d.startswith('.') and (_bp / d).is_dir()))
        except Exception:
            pass
    _total_top = len(_top_dirs)
    _cur_top = [None]
    _top_idx = [0]

    for _bp in SEARCH_BASE_PATHS:
      for root, dirs, files in os.walk(_bp):
        if cancel.is_set():
            break
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        root_path = Path(root)
        if root_path.parent == _bp:
            name = root_path.name
            if name != _cur_top[0]:
                _cur_top[0] = name
                _top_idx[0] += 1
                on_progress(_top_idx[0], _total_top, name)

        for fname in files:
            if cancel.is_set() or result_count >= limit:
                break

            ext = Path(fname).suffix.lstrip('.').lower()
            if ext not in SEARCHABLE_EXTENSIONS:
                continue
            if filter_exts and ext not in filter_exts:
                continue

            fpath = Path(root) / fname
            try:
                lines = None
                if use_cached and ext == 'pdf':
                    pages = read_pdf_cache(fpath)
                    if pages is None:
                        pages = _extract_pdf_pages_for_cache(str(fpath))
                        if pages:
                            write_pdf_cache(fpath, pages)
                    if not pages:
                        continue
                    matches = search_pdf_from_cache(pages, query, MAX_MATCHES_PER_FILE,
                                                    lang=lang, use_soundex=use_soundex,
                                                    case_sensitive=case_sensitive)
                elif use_cached:
                    text = read_from_cache(fpath)
                    if text is None:
                        text = extract_text(str(fpath), ext) or ''
                        if text:
                            write_to_cache(fpath, text)
                    if not text:
                        continue
                    lines = text.splitlines()
                    matches = search_in_text(lines, query, MAX_MATCHES_PER_FILE,
                                             lang=lang, use_soundex=use_soundex,
                                             case_sensitive=case_sensitive)
                elif ext == 'pdf':
                    matches = search_pdf_with_pages(str(fpath), query, MAX_MATCHES_PER_FILE,
                                                    lang=lang, use_soundex=use_soundex,
                                                    case_sensitive=case_sensitive)
                else:
                    text = extract_text(str(fpath), ext)
                    if not text:
                        continue
                    lines = text.splitlines()
                    matches = search_in_text(lines, query, MAX_MATCHES_PER_FILE,
                                             lang=lang, use_soundex=use_soundex,
                                             case_sensitive=case_sensitive)

                if matches and ext == 'srt' and lines is not None:
                    line_to_time = _parse_srt_timestamps(lines)
                    for m in matches:
                        t = line_to_time.get(m['line'])
                        if t is not None:
                            m['srt_time'] = t

                if matches:
                    total_matches += len(matches)
                    result_count += 1
                    result = {
                        "path": str(fpath),
                        "file": fname,
                        "rel_path": get_rel_path(fpath),
                        "type": ext,
                        "match_count": len(matches),
                        "matches": matches,
                    }
                    on_result(result)
            except Exception as e:
                log.debug(f"Error processing {fpath}: {e}")
                continue

    elapsed = time.perf_counter() - t_start
    cancelled = cancel.is_set()
    if _total_top > 0 and not cancelled:
        print(f"\r  ✓ Done — {_top_idx[0]} folder(s) searched — {elapsed:.2f}s    ", flush=True)
    elif cancelled:
        print(f"\r  ✗ Cancelled — {elapsed:.2f}s    ", flush=True)
    return {"total_matches": total_matches, "result_count": result_count, "elapsed_s": round(elapsed, 3)}


@app.route('/api/search')
def search():
    query = request.args.get('q', '').strip()
    lang = request.args.get('lang', '').strip().lower()
    use_soundex = request.args.get('soundex', '0') == '1'
    case_sensitive = request.args.get('cs', '0') == '1'
    use_cached = request.args.get('cached', '0') == '1'
    limit = min(int(request.args.get('limit', MAX_RESULTS)), 200)
    _types_raw = request.args.get('types', '').strip()
    filter_exts = {t.strip().lower().lstrip('.') for t in _types_raw.split(',') if t.strip()} if _types_raw else set()

    if not query or len(query) < 2:
        return jsonify({"error": "Query too short", "total": 0, "results": []})
    if lang not in ('de', 'es', 'en', ''):
        lang = ''

    mode = {"lang": lang, "soundex": use_soundex, "cs": case_sensitive, "cached": use_cached}
    if filter_exts:
        mode["types"] = sorted(filter_exts)
    log.info(f"Search [API]: '{query}' lang={lang or '-'} soundex={use_soundex} cs={case_sensitive} cached={use_cached} in {SEARCH_BASE_PATH}")

    # ── Try Elasticsearch if available ──────────────────────────────────────
    es = _get_es()
    if es is not None:
        try:
            count_resp = es.count(index=ES_INDEX)
            if count_resp.get("count", 0) > 0:
                return _search_via_es(query, lang, limit, mode)
        except Exception as _e:
            log.warning(f"ES search failed, falling back: {_e}")

    # ── Try FAISS semantic fallback ───────────────────────────────────────
    faiss_result = _search_via_faiss(query, limit, mode)
    if faiss_result is not None:
        return faiss_result

    # ── Python substring fallback (last resort) ───────────────────────────
    all_results = []
    total_matches = [0]

    def _on_progress(idx, total, name):
        _pct = f"{idx}/{total}" if total else str(idx)
        print(f"\r  [{_pct}] Searching: {name[:55]:<55}", end='', flush=True)

    def _on_result(result):
        all_results.append(result)
        total_matches[0] += result["match_count"]

    cancel = threading.Event()
    summary = _run_search(query, lang, use_soundex, case_sensitive, use_cached,
                          filter_exts, limit, _on_progress, _on_result, cancel)

    return jsonify({
        "query": query,
        "total": total_matches[0],
        "results": all_results,
        "mode": mode,
        "elapsed_s": summary["elapsed_s"],
    })

# ─── WebSocket Search (Flask-SocketIO) ───────────────────────────────────────

if _socketio_available:
    @socketio.on('connect')
    def _ws_connect():
        sid = request.sid
        _cancel_flags[sid] = threading.Event()
        log.debug(f"WS connect: {sid[:8]}")

    @socketio.on('disconnect')
    def _ws_disconnect():
        sid = request.sid
        flag = _cancel_flags.pop(sid, None)
        if flag:
            flag.set()
        log.debug(f"WS disconnect: {sid[:8]}")

    @socketio.on('search_cancel')
    def _ws_cancel():
        sid = request.sid
        flag = _cancel_flags.get(sid)
        if flag:
            flag.set()

    @socketio.on('search')
    def _ws_search(data):
        sid = request.sid
        # Cancel previous search for this socket
        old_flag = _cancel_flags.get(sid)
        if old_flag:
            old_flag.set()
        new_flag = threading.Event()
        _cancel_flags[sid] = new_flag

        query = (data.get('q') or '').strip()
        lang = (data.get('lang') or '').strip().lower()
        use_soundex = bool(data.get('soundex'))
        case_sensitive = bool(data.get('cs'))
        use_cached = bool(data.get('cached'))
        limit = min(int(data.get('limit') or MAX_RESULTS), 200)
        _types_raw = (data.get('types') or '').strip()
        filter_exts = {t.strip().lower().lstrip('.') for t in _types_raw.split(',') if t.strip()} if _types_raw else set()

        if not query or len(query) < 2:
            socketio.emit('search_done', {"error": "Query too short", "total": 0,
                                          "result_count": 0, "elapsed_s": 0}, to=sid)
            return
        if lang not in ('de', 'es', 'en', ''):
            lang = ''

        log.info(f"WS search [{sid[:8]}]: '{query}'")

        def _run():
            def _on_progress(idx, total, name):
                if new_flag.is_set():
                    return
                socketio.emit('search_progress',
                              {"idx": idx, "total": total, "folder": name}, to=sid)

            def _on_result(result):
                if new_flag.is_set():
                    return
                socketio.emit('search_result', result, to=sid)

            summary = _run_search(query, lang, use_soundex, case_sensitive, use_cached,
                                   filter_exts, limit, _on_progress, _on_result, new_flag)
            cancelled = new_flag.is_set()
            socketio.emit('search_done', {
                "total": summary["total_matches"],
                "result_count": summary["result_count"],
                "elapsed_s": summary["elapsed_s"],
                "cancelled": cancelled,
                "query": query,
            }, to=sid)
            if sid in _cancel_flags and _cancel_flags.get(sid) is new_flag:
                _cancel_flags.pop(sid, None)

        t = threading.Thread(target=_run, daemon=True)
        t.start()


def validate_path(path_str: str) -> Optional[Path]:
    """Validate that path is within any of SEARCH_BASE_PATHS (security).
    NOTE: Flask already URL-decodes request.args, so path_str is the raw path string.
    """
    try:
        p = Path(path_str).resolve()
        for base in SEARCH_BASE_PATHS:
            try:
                p.relative_to(base.resolve())
                if p.is_file():
                    return p
            except ValueError:
                continue
        return None
    except OSError:
        return None

def validate_path_for_vlc(path_str: str) -> Optional[Path]:
    """Validate path for VLC launch — allows UNC and any accessible file path.
    Does NOT require the file to be within SEARCH_BASE_PATH, since VLC is a
    local media player and the user intentionally chose the file from the catalog.
    """
    try:
        # Handle UNC paths (\\server\share\...) on Windows by not resolving
        # through the OS, which may fail for network paths
        p = Path(path_str)
        if not p.is_absolute():
            return None
        if not p.is_file():
            return None
        ext = p.suffix.lstrip('.').lower()
        if ext not in set(_VIDEO_EXTS):
            return None
        return p
    except (ValueError, OSError):
        return None

@app.route('/api/file')
def serve_file():
    """Serve a file by absolute path (must be within SEARCH_BASE_PATH)."""
    path_str = request.args.get('path', '')
    if not path_str:
        abort(400)

    p = validate_path(path_str)
    if p is None:
        abort(404)

    try:
        mime, _ = mimetypes.guess_type(str(p))
        return send_file(str(p), mimetype=mime or 'application/octet-stream',
                         as_attachment=False)
    except Exception as e:
        log.error(f"Error serving file {p}: {e}")
        abort(500)

@app.route('/api/serve/<path:filepath>')
def serve_by_path(filepath):
    """Serve a file with the path embedded in the URL (not a query param).
    Used by the markdown/HTML viewer: an iframe with <base href='/api/serve/dir/'>
    makes all relative URLs (images, CSS, etc.) resolve through this endpoint,
    so relative paths in markdown and HTML files work correctly.
    Security: path is still validated against SEARCH_BASE_PATHS."""
    p = validate_path(_path_from_serve_url(filepath))
    if p is None:
        abort(404)
    try:
        mime, _ = mimetypes.guess_type(str(p))
        return send_file(str(p), mimetype=mime or 'application/octet-stream',
                         as_attachment=False)
    except Exception as e:
        log.error(f"serve_by_path error {p}: {e}")
        abort(500)


def _path_from_serve_url(filepath: str) -> str:
    r"""Rebuild the real filesystem path from an /api/serve/<...> URL tail.

    The frontend sends:  C:/dir/file.pdf         Windows drive path   → C:/dir/file.pdf
                         __unc__/server/share/f  UNC path             → \\server\share\f
                         home/user/f             POSIX path           → /home/user/f
    (Prefixing '/' blindly turned 'C:/…' into '/C:/…', which is not a valid Windows path.)
    """
    fp = filepath.replace('\\', '/')
    if fp.startswith('__unc__/'):
        return '\\\\' + fp[len('__unc__/'):].replace('/', '\\')
    if re.match(r'^[A-Za-z]:/', fp):
        return fp
    return '/' + fp.lstrip('/')


@app.route('/api/file-info')
def file_info():
    """Diagnose a file the viewer cannot show: absolute path, existence and why access is refused."""
    path_str = request.args.get('path', '').strip()
    if not path_str:
        abort(400)
    info = {'path': path_str, 'exists': False, 'is_file': False, 'size': None,
            'allowed': False, 'matched_base': None,
            'bases': [str(b) for b in SEARCH_BASE_PATHS], 'reason': ''}
    try:
        p = Path(path_str)
        info['absolute'] = p.is_absolute()
        info['exists'] = p.exists()
        info['is_file'] = p.is_file()
        if info['is_file']:
            info['size'] = p.stat().st_size
            rp = p.resolve()
            for base in SEARCH_BASE_PATHS:
                try:
                    rp.relative_to(base.resolve())
                    info['allowed'] = True
                    info['matched_base'] = str(base)
                    break
                except ValueError:
                    continue
    except (OSError, ValueError) as e:
        info['reason'] = f'Cannot access: {e.__class__.__name__}'
    if not info['reason']:
        if not info.get('absolute', True):
            info['reason'] = 'The indexed path is relative — it cannot be located on disk.'
        elif not info['exists']:
            info['reason'] = ('File not found on this machine. It was indexed from another location '
                              '(or moved/deleted, or a network drive is not mounted).')
        elif not info['is_file']:
            info['reason'] = 'Path exists but is not a file.'
        elif not info['allowed']:
            info['reason'] = ('File exists but is outside the allowed base paths — add its folder '
                              'in Tools > Search paths (or index it from the UI) to view it.')
    return jsonify(info)


@app.route('/api/file-text')
def serve_file_text():
    r"""Return extracted text content of a file for display in frontend.
    Supports UNC paths (\\server\share\...) for network files.
    """
    path_str = request.args.get('path', '')
    if not path_str:
        abort(400)

    # Try UNC/absolute path first, fall back to SEARCH_BASE_PATH restriction
    p = None
    try:
        candidate = Path(path_str)
        if candidate.is_absolute() and candidate.is_file():
            p = candidate
    except (ValueError, OSError):
        pass
    if p is None:
        p = validate_path(path_str)
    if p is None:
        abort(404)

    ext = p.suffix.lstrip('.').lower()
    language = LANG_MAP.get(ext, ext or 'plaintext')
    raw_mode = request.args.get('raw', '0') == '1'

    try:
        if raw_mode:
            content = read_text_file(str(p)) or f"[Cannot read {p.name}]"
        else:
            content = extract_text(str(p), ext)
        if content is None:
            content = f"[Cannot extract text from {ext.upper()} file]"
        # Truncate for display
        if len(content) > 500_000:
            content = content[:500_000] + '\n\n[... truncated ...]'
        return jsonify({
            "content": content,
            "language": language,
            "path": str(p),
            "size": p.stat().st_size,
        })
    except Exception as e:
        log.error(f"Error extracting text from {p}: {e}")
        abort(500)

@app.route('/api/play')
def play_srt():
    """Launch VLC with a video file at the given SRT timestamp.
    Params: srt=<path> (validated), time=<seconds as float>
    Looks for a video file with the same stem as the SRT in the same/parent dir.
    """
    srt_str = request.args.get('srt', '')
    time_str = request.args.get('time', '0')
    if not srt_str:
        abort(400)

    # Allow UNC paths for SRT files (network shares)
    srt_path = None
    try:
        p = Path(srt_str)
        if p.is_absolute() and p.is_file():
            srt_path = p
    except (ValueError, OSError):
        pass
    if srt_path is None:
        srt_path = validate_path(srt_str)
    if srt_path is None:
        return jsonify({"error": f"SRT file not found or not allowed: {srt_str}"}), 404

    try:
        start_time = float(time_str)
    except ValueError:
        start_time = 0.0

    video_path = _find_video_for_srt(srt_path)

    # YouTube fallback: video missing or zero-byte
    video_missing = video_path is None or video_path.stat().st_size == 0
    if video_missing:
        yt_id = _extract_youtube_id(srt_path)
        if yt_id is None and video_path is not None:
            yt_id = _extract_youtube_id(video_path)
        if yt_id:
            # Video unavailable locally — open in the web YouTube viewer
            viewer_url = f'http://127.0.0.1:{PORT}/youtube-viewer?v={yt_id}&t={int(start_time)}'
            log.info(f"YouTube viewer fallback: {viewer_url}")
            return jsonify({"status": "youtube_viewer", "url": viewer_url,
                            "youtube_id": yt_id, "time": start_time})
        return jsonify({"error": f"No video file found for {srt_path.name}"}), 404

    try:
        # When running in WSL, VLC is a Windows process and needs Windows-style
        # paths for media files and subtitle files (C:\...), not /mnt/c/... paths.
        if _is_wsl():
            arg_video = _to_windows_path(video_path)
            arg_srt   = _to_windows_path(srt_path)
        else:
            arg_video = str(video_path)
            arg_srt   = str(srt_path)

        cmd = [
            VLC_PATH,
            arg_video,
            f'--sub-file={arg_srt}',
            f'--start-time={int(start_time)}',
        ]
        kwargs = {}
        # On native Windows detach so VLC runs independently of the server
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        log.info(f"Launching VLC: {VLC_PATH!r}  video={arg_video!r}  @ {int(start_time)}s")
        subprocess.Popen(cmd, **kwargs)
        return jsonify({
            "status": "ok",
            "video": str(video_path),
            "srt": str(srt_path),
            "time": start_time,
        })
    except Exception as e:
        log.warning(f"VLC failed ({e}), falling back to web video viewer")
        video_str = _to_windows_path(video_path) if _is_wsl() else str(video_path)
        return jsonify({"status": "video_viewer", "video": video_str, "time": start_time})

@app.route('/api/find-video')
def find_video():
    """Return info about the video associated with an SRT file, without launching it.
    Used by the SRT viewer's View tab to embed video internally.
    Returns JSON: {type: 'local', video_path: str} | {type: 'youtube', youtube_id: str} | {type: 'not_found', message: str}
    """
    srt_str = request.args.get('srt', '').strip()
    if not srt_str:
        return jsonify({"type": "not_found", "message": "srt param required"}), 400

    srt_path = None
    try:
        p = Path(srt_str)
        if p.is_absolute() and p.is_file():
            srt_path = p
    except (ValueError, OSError):
        pass
    if srt_path is None:
        srt_path = validate_path(srt_str)
    if srt_path is None:
        return jsonify({"type": "not_found", "message": f"SRT file not found: {srt_str}"})

    video_path = _find_video_for_srt(srt_path)
    video_missing = video_path is None or video_path.stat().st_size == 0

    if video_missing:
        yt_id = _extract_youtube_id(srt_path)
        if yt_id:
            return jsonify({"type": "youtube", "youtube_id": yt_id})
        return jsonify({"type": "not_found", "message": f"No video found for {srt_path.name}"})

    video_str = _to_windows_path(video_path) if _is_wsl() else str(video_path)
    return jsonify({"type": "local", "video_path": video_str})


@app.route('/api/play-video')
def play_video():
    r"""Launch VLC with a video file directly (no SRT required).
    Params: path=<video path>, time=<start seconds, default 0>
    Supports UNC paths (\\server\share\...) in addition to local paths.
    """
    video_str = request.args.get('path', '')
    time_str = request.args.get('time', '0')
    if not video_str:
        abort(400)

    # Use validate_path_for_vlc which allows UNC/network paths
    video_path = validate_path_for_vlc(video_str)
    if video_path is None:
        # Fallback: also check within SEARCH_BASE_PATH (covers relative launches)
        video_path = validate_path(video_str)
        if video_path is None:
            return jsonify({"error": f"Video file not found or not allowed: {video_str}"}), 404
        ext = video_path.suffix.lstrip('.').lower()
        if ext not in set(_VIDEO_EXTS):
            return jsonify({"error": f"Not a supported video file: {video_path.name}"}), 400

    try:
        start_time = float(time_str)
    except ValueError:
        start_time = 0.0

    try:
        # WSL: convert file path to Windows format for the Windows VLC process
        arg_video = _to_windows_path(video_path) if _is_wsl() else str(video_path)

        cmd = [VLC_PATH, arg_video]
        if start_time > 0:
            cmd.append(f'--start-time={int(start_time)}')
        kwargs = {}
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        log.info(f"Launching VLC: {VLC_PATH!r}  video={arg_video!r}  @ {int(start_time)}s")
        subprocess.Popen(cmd, **kwargs)
        return jsonify({"status": "ok", "video": str(video_path), "time": start_time})
    except Exception as e:
        log.warning(f"VLC failed ({e}), falling back to web video viewer")
        video_str = _to_windows_path(video_path) if _is_wsl() else str(video_path)
        return jsonify({"status": "video_viewer", "video": video_str, "time": start_time})


@app.route('/api/stream-video')
def stream_video():
    """Stream a local video file for browser playback (supports HTTP Range for seeking).
    Params: path=<absolute video path>
    In WSL the client sends a Windows path (C:\\...); this is converted to /mnt/c/... internally.
    """
    path_str = request.args.get('path', '')
    if not path_str:
        abort(400)
    # WSL: client sends Windows path — convert to Linux path so Flask can read the file
    if _is_wsl() and re.match(r'^[A-Za-z]:[/\\]', path_str):
        try:
            res = subprocess.run(['wslpath', '-u', path_str],
                                 capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                path_str = res.stdout.strip()
        except Exception:
            pass
    # Use validate_path_for_vlc: accepts any absolute path without SEARCH_BASE_PATH restriction
    video_path = validate_path_for_vlc(path_str)
    if video_path is None:
        abort(404)
    mime = mimetypes.guess_type(str(video_path))[0] or 'video/mp4'
    return send_file(video_path, mimetype=mime, conditional=True)


@app.route('/youtube-viewer')
def youtube_viewer():
    """Serve a minimal HTML page that embeds a YouTube video in an iframe.
    Params: v=<YouTube video ID>, t=<start seconds (int)>
    """
    yt_id = request.args.get('v', '')
    t_str = request.args.get('t', '0')
    if not yt_id or not _YT_ID_RE.match(f'[{yt_id}]'):
        return "Invalid YouTube video ID", 400
    try:
        t_secs = int(float(t_str))
    except ValueError:
        t_secs = 0
    embed_url = f'https://www.youtube.com/embed/{yt_id}?autoplay=1&start={t_secs}'
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouTube Viewer</title>
<style>
  * {{ margin:0;padding:0;box-sizing:border-box; }}
  body {{ background:#000;display:flex;align-items:center;justify-content:center;
         width:100vw;height:100vh;overflow:hidden; }}
  iframe {{ width:100%;height:100%;border:none; }}
</style>
</head>
<body>
  <iframe src="{embed_url}"
    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
    allowfullscreen></iframe>
</body>
</html>"""
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


# ─── Elasticsearch (optional) ────────────────────────────────────────────────

_es_client = None
_es_embed_model = None
_es_last_failed = 0.0  # timestamp of last failed connection attempt
_ES_RETRY_INTERVAL = 30.0  # seconds between retry attempts when ES is unreachable

def _get_es():
    """Return cached Elasticsearch client. Auto-detects local Docker ES (127.0.0.1:9200)."""
    global _es_client, _es_last_failed
    if _es_client is not None:
        return _es_client

    # Throttle retry attempts when ES is unavailable
    if _es_last_failed > 0 and time.time() - _es_last_failed < _ES_RETRY_INTERVAL:
        return None

    # Try configured URL first, then auto-detect local Docker
    candidate_urls = [ES_URL] if ES_URL else ["http://127.0.0.1:9200"]

    try:
        from elasticsearch import Elasticsearch
    except ImportError:
        log.warning("elasticsearch package not installed — ES search disabled.")
        _es_last_failed = time.time()
        return None

    for url in candidate_urls:
        try:
            client = Elasticsearch(url, request_timeout=2)
            client.info()
            log.info(f"Elasticsearch connected: {url}")
            _es_client = client
            _es_last_failed = 0.0
            return _es_client
        except Exception as e:
            log.debug(f"ES not reachable at {url}: {e}")

    _es_last_failed = time.time()
    return None

# Embedding model config — mirrors search_indexer.py logic
_EMBEDDING_MODEL = getattr(env, 'EMBEDDING_MODEL',
    'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2') if 'env' in dir() else \
    os.environ.get('EMBEDDING_MODEL', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')

_MODEL_CONFIG = {
    'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2': {
        'dims': 384, 'normalize_case': True,  'multilingual': False,
        'description': 'Faster, smaller (~120 MB, 384 dims). Lowercase normalization applied.',
    },
    'sentence-transformers/paraphrase-multilingual-mpnet-base-v2': {
        'dims': 768, 'normalize_case': False, 'multilingual': False,
        'description': 'More accurate, larger (~400 MB, 768 dims). Robust to casing.',
    },
    'intfloat/multilingual-e5-small': {
        'dims': 384, 'normalize_case': False, 'multilingual': True,
        'description': 'Multilingual E5 small (~120 MB, 384 dims). Cross-lingual — no translation needed.',
    },
    'sentence-transformers/LaBSE': {
        'dims': 768, 'normalize_case': False, 'multilingual': True,
        'description': 'Language-agnostic BERT (~1.9 GB, 768 dims). Best cross-lingual quality — no translation needed.',
    },
    'intfloat/multilingual-e5-base': {
        'dims': 768, 'normalize_case': False, 'multilingual': True,
        'description': 'Multilingual E5 base (~1.1 GB, 768 dims). Noticeably better than small; fine on a GPU.',
    },
    'intfloat/multilingual-e5-large': {
        'dims': 1024, 'normalize_case': False, 'multilingual': True,
        'description': 'Multilingual E5 large (~2.2 GB, 1024 dims). High quality — index with a GPU (CUDA / Apple MPS).',
    },
    'BAAI/bge-m3': {
        'dims': 1024, 'normalize_case': False, 'multilingual': True,
        'description': 'BGE-M3 (~2.2 GB, 1024 dims, 8k context). Top multilingual retrieval — index with a GPU.',
    },
}
_mcfg = _MODEL_CONFIG.get(_EMBEDDING_MODEL, {'dims': 384, 'normalize_case': True, 'multilingual': False})
_NORMALIZE_EMBEDDING_CASE = _mcfg['normalize_case']
_MODEL_IS_MULTILINGUAL = _mcfg.get('multilingual', False)

# Models that require sentence-transformers (not supported by fastembed)
_ST_ONLY_MODELS   = {'intfloat/multilingual-e5-small', 'intfloat/multilingual-e5-base',
                     'intfloat/multilingual-e5-large', 'BAAI/bge-m3', 'sentence-transformers/LaBSE'}
_E5_MODELS        = {'intfloat/multilingual-e5-small', 'intfloat/multilingual-e5-base', 'intfloat/multilingual-e5-large'}
_es_embed_backend = None  # 'fastembed' | 'sentence-transformers'

def _get_embed_model():
    """Return cached embedding model (fastembed or sentence-transformers), or None."""
    global _es_embed_model, _es_embed_backend
    if _es_embed_model is not None:
        return _es_embed_model
    try:
        if _EMBEDDING_MODEL in _ST_ONLY_MODELS:
            from sentence_transformers import SentenceTransformer
            _es_embed_model   = SentenceTransformer(_EMBEDDING_MODEL)
            _es_embed_backend = 'sentence-transformers'
        else:
            import warnings
            from fastembed import TextEmbedding
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=".*now uses mean pooling.*",
                                        category=UserWarning)
                _es_embed_model = TextEmbedding(_EMBEDDING_MODEL)
            _es_embed_backend = 'fastembed'
        log.info(f"Embedding model loaded (backend: {_es_embed_backend})")
    except ImportError as e:
        log.warning(f"Embedding library not available: {e}")
    except Exception as e:
        log.warning(f"Failed to load embedding model: {e}")
    return _es_embed_model

def _query_vector(text: str) -> Optional[list]:
    """Return embedding vector for a query string, or None if model unavailable.

    Applies lowercase normalization (MiniLM), and 'query: ' prefix (E5 models).
    """
    model = _get_embed_model()
    if model is None:
        return None
    try:
        if _NORMALIZE_EMBEDDING_CASE:
            text = text.lower()
        if _es_embed_backend == 'sentence-transformers':
            if _EMBEDDING_MODEL in _E5_MODELS:
                text = "query: " + text
            vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False)
            return vec[0].tolist()
        vectors = list(model.embed([text]))
        return vectors[0].tolist()
    except Exception as e:
        log.warning(f"Embedding failed: {e}")
        return None


def _passage_vector(text: str) -> Optional[list]:
    """Like _query_vector but uses 'passage: ' prefix for E5 models (indexing side)."""
    model = _get_embed_model()
    if model is None:
        return None
    try:
        if _NORMALIZE_EMBEDDING_CASE:
            text = text.lower()
        if _es_embed_backend == 'sentence-transformers':
            if _EMBEDDING_MODEL in _E5_MODELS:
                text = "passage: " + text
            vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False)
            return vec[0].tolist()
        vectors = list(model.embed([text]))
        return vectors[0].tolist()
    except Exception as e:
        log.warning(f"Passage embedding failed: {e}")
        return None


def _chunk_text_simple(text: str) -> list:
    """Split text into overlapping chunks of ~_FAISS_CHUNK_SIZE chars on paragraph boundaries."""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    chunks, current, current_len = [], [], 0
    for para in paragraphs:
        if current_len + len(para) > _FAISS_CHUNK_SIZE and current:
            chunks.append('\n\n'.join(current))
            current, current_len = [], 0
        current.append(para)
        current_len += len(para)
    if current:
        chunks.append('\n\n'.join(current))
    return chunks or [text[:_FAISS_CHUNK_SIZE]]


# ─── FAISS fallback index ──────────────────────────────────────────────────────

_faiss_index    = None
_faiss_chunks: list = []
_faiss_lock     = threading.Lock()
_faiss_building = False


def _faiss_one_thread(faiss) -> None:
    """macOS: keep faiss' OpenMP pool to one thread so it cannot clash with torch's libomp."""
    if os.uname().sysname == 'Darwin' if hasattr(os, 'uname') else False:
        try:
            faiss.omp_set_num_threads(1)
        except Exception:  # noqa: BLE001 — optional hardening
            pass


def _load_faiss_index() -> bool:
    """Load FAISS index from disk. Returns True on success."""
    global _faiss_index, _faiss_chunks
    if not FAISS_INDEX_PATH.exists() or not FAISS_CHUNKS_PATH.exists():
        return False
    try:
        import faiss
        _faiss_one_thread(faiss)
    except ImportError:
        return False
    try:
        idx    = faiss.read_index(str(FAISS_INDEX_PATH))
        chunks = json.loads(FAISS_CHUNKS_PATH.read_text(encoding='utf-8'))
        with _faiss_lock:
            _faiss_index  = idx
            _faiss_chunks = chunks
        log.info(f"FAISS index loaded: {len(chunks)} chunks")
        return True
    except Exception as e:
        log.warning(f"Failed to load FAISS index: {e}")
        return False


def _build_faiss_index():
    """Walk SEARCH_BASE_PATHS, embed all chunks, and save FAISS index to disk."""
    global _faiss_index, _faiss_chunks, _faiss_building
    try:
        import faiss
        _faiss_one_thread(faiss)
        import numpy as np
    except ImportError:
        log.warning("faiss-cpu not installed — FAISS fallback disabled. Run: pip install faiss-cpu")
        return

    with _faiss_lock:
        if _faiss_building:
            return
        _faiss_building = True

    log.info("Building FAISS index (background)...")
    t0 = time.perf_counter()

    dims  = _mcfg['dims']
    index = faiss.IndexFlatIP(dims)
    chunks_meta: list = []

    for base_path in SEARCH_BASE_PATHS:
        for root, dirs, files in os.walk(str(base_path)):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for fname in files:
                ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
                if ext not in SEARCHABLE_EXTENSIONS:
                    continue
                fpath = Path(root) / fname
                try:
                    text = extract_text(str(fpath), ext)
                except Exception:
                    continue
                if not text or not text.strip():
                    continue
                try:
                    rel = str(fpath.relative_to(base_path))
                except ValueError:
                    rel = fname

                for i, chunk in enumerate(_chunk_text_simple(text)):
                    vec = _passage_vector(chunk)
                    if vec is None:
                        continue
                    import numpy as _np
                    index.add(_np.array([vec], dtype='float32'))
                    chunks_meta.append({
                        "path":        str(fpath),
                        "rel_path":    rel,
                        "file":        fname,
                        "ext":         ext,
                        "chunk_index": i,
                        "content":     chunk[:600],
                    })

    if not chunks_meta:
        log.warning("FAISS: no chunks indexed — check SEARCH_BASE_PATHS and SEARCHABLE_EXTENSIONS")
        with _faiss_lock:
            _faiss_building = False
        return

    FAISS_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(FAISS_INDEX_PATH))
    FAISS_CHUNKS_PATH.write_text(json.dumps(chunks_meta, ensure_ascii=False), encoding='utf-8')

    with _faiss_lock:
        _faiss_index  = index
        _faiss_chunks = chunks_meta
        _faiss_building = False

    elapsed = round(time.perf_counter() - t0, 2)
    log.info(f"FAISS index built: {len(chunks_meta)} chunks in {elapsed}s")


def _search_via_faiss(query: str, limit: int, mode: dict):
    """Semantic search using in-memory FAISS index. Returns jsonify response or None."""
    with _faiss_lock:
        idx    = _faiss_index
        chunks = _faiss_chunks

    if idx is None or not chunks:
        return None

    vec = _query_vector(query)
    if vec is None:
        return None

    try:
        import numpy as np
        q = np.array([vec], dtype='float32')
        k = min(limit * 4, idx.ntotal)
        t0 = time.perf_counter()
        scores, ids = idx.search(q, k)
        elapsed = round(time.perf_counter() - t0, 3)
    except Exception as e:
        log.warning(f"FAISS search error: {e}")
        return None

    seen_paths: dict = {}
    for score, cid in zip(scores[0], ids[0]):
        if cid < 0 or cid >= len(chunks):
            continue
        chunk = chunks[cid]
        pk    = chunk["path"]
        match_entry = {
            "line":          chunk["chunk_index"],
            "context":       chunk["content"][:400],
            "highlight":     query,
            "score":         round(float(score), 4),
            "chunk_content": chunk["content"],
        }
        if pk in seen_paths:
            seen_paths[pk]["matches"].append(match_entry)
            seen_paths[pk]["match_count"] += 1
            if float(score) > seen_paths[pk]["score"]:
                seen_paths[pk]["score"] = round(float(score), 4)
        else:
            seen_paths[pk] = {
                "path":        pk,
                "file":        chunk["file"],
                "rel_path":    chunk["rel_path"],
                "type":        chunk["ext"],
                "score":       round(float(score), 4),
                "match_count": 1,
                "matches":     [match_entry],
            }
        if len(seen_paths) >= limit:
            break

    results = sorted(seen_paths.values(), key=lambda r: r["score"], reverse=True)
    faiss_mode          = dict(mode)
    faiss_mode["engine"] = "faiss"

    return jsonify({
        "query":     query,
        "total":     sum(r["match_count"] for r in results),
        "results":   results,
        "mode":      faiss_mode,
        "elapsed_s": elapsed,
    })


# ─── Ollama / RAG ─────────────────────────────────────────────────────────────

_ollama_last_failed = 0.0
_OLLAMA_RETRY_INTERVAL = 15.0

def _get_ollama() -> Optional[str]:
    """Return Ollama base URL if reachable, else None."""
    global _ollama_last_failed
    if _ollama_last_failed > 0 and time.time() - _ollama_last_failed < _OLLAMA_RETRY_INTERVAL:
        return None
    import urllib.request as _ureq
    try:
        with _ureq.urlopen(OLLAMA_URL + '/api/tags', timeout=5) as r:
            if r.status == 200:
                _ollama_last_failed = 0.0
                return OLLAMA_URL
    except Exception:
        pass
    _ollama_last_failed = time.time()
    return None

def _ollama_model_entries() -> list:
    """Keep cloud metadata while preserving the existing names-only API."""
    import urllib.request as _ureq
    try:
        with _ureq.urlopen(OLLAMA_URL + '/api/tags', timeout=3) as r:
            return json.loads(r.read()).get('models', [])
    except Exception:
        return []


def _ollama_list_models() -> list:
    """Return list of installed Ollama model names."""
    return [m['name'] for m in _ollama_model_entries() if m.get('name')]


@app.route('/api/tools/ollama-catalog')
def tools_ollama_catalog():
    """Search the official newest-model catalog without accepting arbitrary URLs."""
    from ollama_support import catalog
    try:
        cloud_only = request.args.get('cloud', '').lower() in ('1', 'true', 'yes')
        return jsonify({'models': catalog(request.args.get('q', ''), cloud_only)})
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception:
        return jsonify({'error': 'Catalog unavailable. Enter a model manually or try again.'}), 502


def _rerank_mod():
    """Lazy import of the optional cross-encoder reranker (backend/rerank.py)."""
    import importlib, sys as _sys
    _d = str(Path(__file__).parent)
    if _d not in _sys.path:
        _sys.path.insert(0, _d)
    try:
        return importlib.import_module('rerank')
    except Exception as e:                                    # sentence-transformers missing, etc.
        log.debug(f"reranker module unavailable: {e}")
        return None


# ─── Models & containers overview (Tools > Models) ────────────────────────────

def _models_info():
    import importlib
    _d = str(Path(__file__).parent)
    if _d not in sys.path:
        sys.path.insert(0, _d)
    return importlib.import_module('models_info')


@app.route('/api/models/overview')
def models_overview():
    """Everything the app runs on: installed Ollama models (loaded / used by the RAG chain),
    the embedding + reranker models, and the Docker containers serving Elasticsearch / Ollama."""
    mi = _models_info()
    entries = _ollama_model_entries()
    ollama = mi.ollama_overview(OLLAMA_URL, entries)

    from ollama_support import candidates
    chain = candidates(entries, '')           # order in which /api/ask tries models: cloud first, then local
    for m in ollama['models']:
        used = []
        if m['name'] in chain:
            used.append({'kind': 'rag_chain', 'rank': chain.index(m['name']) + 1})
        if m['name'] == TRANSLATION_MODEL:
            used.append({'kind': 'translation'})
        m['used_by'] = used

    rr = _rerank_mod()
    rr_st = rr.status() if rr else {'enabled': False, 'state': 'unavailable', 'model': '', 'error': 'sentence-transformers missing'}
    app_models = [
        dict(role='embedding', name=_EMBEDDING_MODEL, detail=f"{_mcfg['dims']} dimensions · runs inside the backend process (CPU)",
             state='in use', **mi.hf_cache_info(_EMBEDDING_MODEL)),
        dict(role='reranker', name=rr_st['model'], detail='cross-encoder · loads in the background on the first question',
             state=rr_st['state'], error=rr_st.get('error', ''), **(mi.hf_cache_info(rr_st['model']) if rr_st['model'] else {})),
    ]

    docker = mi.docker_overview(ES_URL or 'http://127.0.0.1:9200', OLLAMA_URL)
    index = {}
    es = _get_es()
    if es is not None:
        try:
            st = es.indices.stats(index=ES_INDEX, metric='docs,store')['_all']['total']
            index = {'name': ES_INDEX, 'chunks': st['docs']['count'], 'size_h': mi.human(st['store']['size_in_bytes']),
                     'version': es.info()['version']['number'], 'vector_dims': _mcfg['dims'],
                     'embedding_model': _EMBEDDING_MODEL}
        except Exception as ex:
            index = {'name': ES_INDEX, 'error': str(ex)[:120]}
    loaded = [m['name'] for m in ollama['models'] if m['running']]
    for c in docker.get('containers', []):
        if 'elasticsearch' in c['serves']:
            c['volumes'] = mi.docker_volumes(c['name'])
            c['index'] = index
        if 'ollama' in c['serves']:
            c['loaded_models'] = loaded
    ollama_in_docker = any('ollama' in c['serves'] and c['running'] for c in docker.get('containers', []))
    return jsonify({
        'ollama': dict(ollama, url=OLLAMA_URL, host='docker container' if ollama_in_docker
                       else ('host process' if ollama['reachable'] else 'not running')),
        'app_models': app_models, 'docker': docker, 'index': index,
        'selected_hint': {'translation_model': TRANSLATION_MODEL},
    })


@app.route('/api/models/ollama/<action>', methods=['POST'])
def models_ollama_action(action):
    """delete | unload an installed Ollama model (cloud registrations too)."""
    if action not in ('delete', 'unload'):
        abort(404)
    name = str((request.get_json(force=True) or {}).get('name', '')).strip()
    if name not in {e.get('name') for e in _ollama_model_entries()}:
        return jsonify({"error": f"Model not installed: {name}"}), 400
    mi = _models_info()
    try:
        (mi.ollama_delete if action == 'delete' else mi.ollama_unload)(OLLAMA_URL, name)
    except Exception as ex:
        return jsonify({"error": f"Ollama refused to {action} {name}: {ex}"}), 502
    return jsonify({"ok": True, "action": action, "name": name})


def _rag_retrieve(question: str, lang: str, k: int = 5, mode: str = 'es-hybrid',
                  filter_exts: set = None, extra_queries: list = None,
                  rerank: bool = True, rerank_query: str = None, info: dict = None) -> list:
    """Retrieve top-k relevant chunks using BM25 + optional kNN, fused in Python.

    RRF (Reciprocal Rank Fusion) requires an ES Enterprise license, so fusion
    is done client-side: run BM25 and kNN separately, merge by Python RRF.

    mode: 'es-bm25'    → BM25 only (keyword, language-specific)
          'es-hybrid'  → BM25 + kNN fused via RRF (default)
          'es-semantic'→ kNN only (pure vector, cross-lingual)
    filter_exts: optional set of file extensions to restrict retrieval (e.g. {'pdf', 'md'})
    extra_queries: translations of the question (e.g. into German, the corpus language).
                   Every query runs BM25 + kNN and all result lists are fused by RRF.
    rerank: re-order the fused candidates with the cross-encoder (backend/rerank.py) if ready.
    info: optional dict that receives {queries, reranked, rerank_state, pool} for the UI.
    """
    run_bm25 = mode != 'es-semantic'
    run_knn  = mode != 'es-bm25'
    es = _get_es()
    if es is None:
        return None  # ES unavailable — caller distinguishes from empty results

    lang_boost = {'de': 'content.de^3', 'es': 'content.es^3', 'en': 'content.en^3'}
    if CANONICAL_LANGUAGE and not lang:
        # Use canonical content field for language-agnostic BM25
        lang_boost_canonical = {
            'en': 'canonical_content.en^3',
            'es': 'canonical_content.es^3',
            'de': 'canonical_content.de^3',
        }
        content_fields = [lang_boost_canonical.get(CANONICAL_LANGUAGE, 'canonical_content'), 'canonical_content', 'content']
    elif lang in lang_boost:
        content_fields = [lang_boost[lang], 'content']
    else:
        content_fields = ['content']
    # German is the main corpus language: stemmed + compound-split German fields help even
    # when the user asks in another language (the translated query is German).
    if lang == 'de':
        content_fields += ['content.de_c^2']
    elif lang not in lang_boost:
        content_fields += ['content.de^2', 'content.de_c^1.5']
    # Short, high-signal fields (document title, heading breadcrumb). Fields missing from an
    # older index are simply ignored by Elasticsearch.
    content_fields += ['title^2.5', 'title.de^2', 'section^1.5']

    _src = ["path", "filename", "rel_path", "chunk_index", "title", "section",
            "content", "srt_start_time", "srt_end_time", "ext"]

    docs: dict = {}        # _id → doc dict
    rrf_score: dict = {}   # _id → fused score over ALL (query × method) rankings
    RRF_K = 60             # standard Reciprocal-Rank-Fusion constant

    def _hit_to_doc(hit):
        src = hit.get('_source', {})
        return {
            'id': hit['_id'], 'bm25_rank': None, 'knn_rank': None,
            'path': src.get('path', ''), 'filename': src.get('filename', ''),
            'rel_path': src.get('rel_path', ''), 'chunk_index': src.get('chunk_index', 0),
            'content': src.get('content', ''), 'ext': src.get('ext', ''),
            'title': src.get('title', ''), 'section': src.get('section', ''),
            'srt_start_time': src.get('srt_start_time'),
        }

    def _register(hit, rank, kind):
        did = hit['_id']
        d = docs.get(did) or docs.setdefault(did, _hit_to_doc(hit))
        key = kind + '_rank'
        if d[key] is None or rank < d[key]:
            d[key] = rank
        rrf_score[did] = rrf_score.get(did, 0.0) + 1.0 / (RRF_K + rank + 1)

    # Fetch more chunks than k so deduplication by file still yields k results
    fetch = k * 5

    # Extension filter (shared between BM25 and kNN)
    ext_filter = {"terms": {"ext": sorted(filter_exts)}} if filter_exts else None

    # Original question first, then its translations (skip duplicates)
    queries = [question]
    for q in (extra_queries or []):
        if q and q.strip() and q.strip().lower() not in {x.lower() for x in queries}:
            queries.append(q.strip())

    for qtext in queries:
        # ── BM25 search ──────────────────────────────────────────────────────────
        if run_bm25:
            try:
                mm_query = {"multi_match": {"query": qtext, "fields": content_fields,
                                            "type": "best_fields", "fuzziness": "AUTO"}}
                bm25_body = {
                    "_source": _src, "size": fetch,
                    "query": {"bool": {"must": mm_query, "filter": ext_filter}} if ext_filter
                             else {"bool": {"must": mm_query}},
                }
                resp = es.search(index=ES_INDEX, body=bm25_body)
                for rank, hit in enumerate(resp.get('hits', {}).get('hits', [])):
                    _register(hit, rank, 'bm25')
            except Exception as e:
                log.warning(f"RAG BM25 search failed: {e}")

        # ── kNN search (separate query — no RRF flag needed) ────────────────────
        if run_knn:
            vec = _query_vector(qtext)
            if vec is not None:
                try:
                    knn_body = {
                        "_source": _src, "size": fetch,
                        "knn": {"field": "content_vector", "query_vector": vec,
                                "k": fetch, "num_candidates": fetch * 10},
                    }
                    if ext_filter:
                        knn_body["post_filter"] = ext_filter
                    resp = es.search(index=ES_INDEX, body=knn_body)
                    for rank, hit in enumerate(resp.get('hits', {}).get('hits', [])):
                        _register(hit, rank, 'knn')
                except Exception as e:
                    log.debug(f"RAG kNN search failed (non-fatal): {e}")

    if not docs:
        return []

    # Deduplicate by file path: keep only the best chunk per file. When reranking, keep a
    # wider candidate pool so the cross-encoder can promote passages RRF ranked lower.
    rr = _rerank_mod() if rerank else None
    do_rerank = bool(rr and rr.ENABLED)
    pool_n = max(k, rr.CANDIDATES) if do_rerank else k
    seen_file_paths: set = set()
    pool = []
    for doc in sorted(docs.values(), key=lambda d: rrf_score[d['id']], reverse=True):
        fp = doc.get('path') or doc['id']
        if fp not in seen_file_paths:
            seen_file_paths.add(fp)
            doc['rrf'] = round(rrf_score[doc['id']], 6)
            pool.append(doc)
        if len(pool) >= pool_n:
            break

    reranked = False
    if do_rerank and len(pool) > 1:
        def _passage(d):
            head = ' > '.join(x for x in (d.get('title'), d.get('section')) if x)
            return (head + '\n' if head else '') + d.get('content', '')
        # The cross-encoder judges the user's real question, not the keyword list used for retrieval
        scores = rr.score(rerank_query or question, [_passage(d) for d in pool])
        if scores:
            for d, sc in zip(pool, scores):
                d['rerank_score'] = round(sc, 4)
            pool.sort(key=lambda d: d['rerank_score'], reverse=True)
            reranked = True

    chunks = pool[:k]
    for doc in chunks:
        doc['score'] = doc['rerank_score'] if reranked else doc['rrf']
    if info is not None:
        info.update(queries=queries, reranked=reranked, pool=len(pool),
                    rerank_state=(rr.status()['state'] if rr else 'unavailable'))

    # ── Enrich all chunks with surrounding context (prev + next chunk) ───────
    for chunk in chunks:
        ci   = chunk.get('chunk_index', 0)
        path = chunk.get('path', '')
        if not path:
            continue
        neighbours = []
        if ci > 0:
            neighbours.append(ci - 1)
        neighbours.append(ci + 1)
        try:
            nb_resp = es.search(index=ES_INDEX, body={
                "_source": ["chunk_index", "content"], "size": 20,
                "query": {"bool": {"filter": [
                    {"term": {"path": path}},
                    {"terms": {"chunk_index": neighbours}},
                ]}},
                "sort": [{"chunk_index": "asc"}],
            })
            seen_nb: set = set()
            for nb_hit in nb_resp.get('hits', {}).get('hits', []):
                nb_src = nb_hit['_source']
                nb_ci  = nb_src.get('chunk_index', -1)
                if nb_ci in seen_nb:
                    continue  # skip duplicate indexed docs
                seen_nb.add(nb_ci)
                nb_txt = nb_src.get('content', '')
                if nb_ci == ci - 1:
                    chunk['context_before'] = nb_txt
                elif nb_ci == ci + 1:
                    chunk['context_after'] = nb_txt
        except Exception:
            pass

    return chunks

def _format_rag_context(chunks: list) -> str:
    """Format retrieved chunks into a numbered context block for the LLM prompt."""
    parts = []
    for i, c in enumerate(chunks, 1):
        header = f"[{i}] {c.get('filename', 'unknown')}"
        rp = c.get('rel_path', '')
        if rp and rp != c.get('filename', ''):
            header += f" ({rp})"
        if c.get('section'):
            header += f" — {c['section']}"
        ts = c.get('srt_start_time')
        if ts is not None:
            t = int(ts)
            header += f" @ {t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"

        text = ''
        if c.get('context_before'):
            text += c['context_before'].strip() + '\n'
        text += c['content'].strip()
        if c.get('context_after'):
            text += '\n' + c['context_after'].strip()
        parts.append(f"=== Source {header} ===\n{text}")
    return '\n\n'.join(parts)

@app.route('/api/ask', methods=['GET', 'POST'])
def rag_ask():
    """RAG endpoint: retrieve ES chunks → generate answer via Ollama (streaming SSE)."""
    body     = request.get_json(silent=True) or {}
    def _p(key, default=''):
        return request.args.get(key, body.get(key, default))
    # POST body may use 'question' instead of 'q'
    question = (_p('q') or body.get('question', '')).strip()
    lang     = _p('lang').lower().strip()
    k        = min(int(_p('k', 5)), 15)
    model    = _p('model').strip()
    mode     = _p('mode', 'es-hybrid').strip()
    if mode not in ('es-bm25', 'es-hybrid', 'es-semantic'):
        mode = 'es-hybrid'
    _types_raw = _p('types', '').strip()
    filter_exts = {t.strip().lower().lstrip('.') for t in _types_raw.split(',') if t.strip()} if _types_raw else set()
    history    = body.get('history', [])   # [{role, content}, ...] for multi-turn context
    two_stage  = str(_p('two_stage', 'true')).lower() not in ('false', '0', 'no')

    if not question:
        return jsonify({'error': 'Missing query parameter q'}), 400

    # Optional external LLM (OpenAI / Anthropic / OpenAI-compatible); API key comes
    # in the POST body only and is never stored or logged.
    ext = None
    _llm = _llm_mod()
    if _llm.is_external(body.get('llm')):
        try:
            ext = _llm.normalize(body['llm'])
        except _llm.LLMError as e:
            return jsonify({'error': str(e)}), 400
        ext['model'] = ext['model'] or model
        model = ext['model']

    ollama_url = None if ext else _get_ollama()

    def generate():
        import urllib.request as _ureq
        import json as _j

        def evt(obj):
            return f"data: {_j.dumps(obj)}\n\n"

        yield evt({'type': 'status', 'text': 'Searching documents…'})

        # When history is provided (Continue context):
        # - Use the PREVIOUS user question for ES retrieval → stable sources across follow-ups
        # - Build the prompt as "Teniendo en cuenta: <prev>, responde: <current>"
        prev_question = ''
        if history:
            prev_question = next((h['content'] for h in reversed(history)
                                  if h.get('role') == 'user' and h.get('content')), '')

        base_question = prev_question if prev_question else question

        # ── Stage 1 (two-stage RAG): extract optimised search query via LLM ──
        # The user's natural-language question is reformulated into concise
        # search terms for better ES retrieval, then the *original* question
        # is used for the LLM answer prompt.
        # The same LLM call also TRANSLATES the terms into the corpus language (German by
        # default, RAG_TRANSLATE_QUERY_TO) — the documents are mostly German, so a Spanish
        # or English question is searched in both languages and the rankings are fused.
        search_query = base_question
        translations: list = []
        _raw_terms = ''
        if two_stage and ext and ext['model']:
            yield evt({'type': 'status', 'text': 'Optimising & translating search query…'})
            try:
                _raw_terms = _llm.complete(ext, _multilang_query_prompt(base_question), max_tokens=120)
            except Exception:
                pass   # fall back to the original question; the answer step reports real errors
        elif two_stage and ollama_url:
            from ollama_support import answer, candidates
            stage1_models = candidates(_ollama_model_entries(), model)
            if stage1_models:
                yield evt({'type': 'status', 'text': 'Optimising & translating search query (cloud first)…'})
                try:
                    _, _raw_terms = answer(ollama_url, stage1_models,
                                           [{'role': 'user', 'content': _multilang_query_prompt(base_question)}],
                                           budget=40)
                except RuntimeError:
                    pass
        if _raw_terms:
            search_query, translations = _split_query_variants(_raw_terms, base_question)
        if search_query != base_question or translations:
            yield evt({'type': 'search_query', 'original': base_question,
                       'optimised': search_query, 'translations': translations})

        _rr = _rerank_mod()
        if _rr is not None and _rr.ENABLED:
            _rr.warmup()                       # background load; never blocks the answer
        _ret_info: dict = {}
        yield evt({'type': 'status', 'text': 'Retrieving passages (BM25 + vectors)…'})
        chunks = _rag_retrieve(search_query, lang, k, mode=mode,
                               filter_exts=filter_exts or None,
                               extra_queries=translations, rerank_query=question, info=_ret_info)
        if _ret_info.get('reranked'):
            yield evt({'type': 'status', 'text': f"Reranked {_ret_info['pool']} candidates (cross-encoder)…"})
        elif _ret_info.get('rerank_state') == 'loading':
            yield evt({'type': 'status', 'text': 'Reranker is loading in the background — using hybrid ranking this time…'})
        yield evt({'type': 'retrieval', **{k_: _ret_info.get(k_) for k_ in ('queries', 'reranked', 'rerank_state', 'pool')}})

        if chunks is None:
            yield evt({'type': 'error',
                       'text': 'Could not connect to Elasticsearch. Make sure it is running and indexed.'})
            return
        if not chunks:
            yield evt({'type': 'error',
                       'text': 'No relevant documents found.'})
            return

        sources = [{'filename': c['filename'], 'rel_path': c.get('rel_path', ''),
                    'path': c.get('path', ''), 'score': c['score'],
                    'chunk_index': c.get('chunk_index', 0),
                    'content': c.get('content', ''),
                    'title': c.get('title', ''), 'section': c.get('section', ''),
                    'rerank_score': c.get('rerank_score'),
                    'srt_time': c.get('srt_start_time')}
                   for c in chunks]
        yield evt({'type': 'sources', 'count': len(chunks), 'sources': sources})

        context_text = _format_rag_context(chunks)
        lang_instr = {
            'de': 'Answer in German (Deutsch).',
            'es': 'Answer in Spanish (Español).',
            'en': 'Answer in English.',
        }.get(lang, 'Answer in the same language as the question.')

        system_prompt = (
            "You are a precise document assistant. "
            "Answer the user's question ONLY based on the provided document excerpts. "
            "If the answer is not found in the excerpts, clearly say so — do not guess. "
            "Cite sources using their number like [1], [2], etc. "
            f"{lang_instr}"
        )

        # Build the user question: if follow-up, prefix with previous question for clarity
        if prev_question and prev_question != question:
            display_question = (
                f"Teniendo en cuenta: {prev_question}\n"
                f"Responde esta pregunta: {question}"
            )
        else:
            display_question = question

        user_prompt = f"Document excerpts:\n\n{context_text}\n\n---\nQuestion: {display_question}"

        # Debug event — exact prompt sent to the LLM
        yield evt({'type': 'context', 'search_mode': mode,
                   'system_prompt': system_prompt, 'user_prompt': user_prompt})

        if ext:
            if not ext['model']:
                yield evt({'type': 'error', 'text': 'No model selected for the external LLM.'})
                return
            yield evt({'type': 'status', 'text': f"Generating answer with {ext['model']} ({ext['provider']})…"})
            _msgs = [{"role": "system", "content": system_prompt}]
            for h in history:
                if isinstance(h, dict) and h.get('role') in ('user', 'assistant') and h.get('content'):
                    _msgs.append({"role": h['role'], "content": str(h['content'])})
            _msgs.append({"role": "user", "content": user_prompt})
            try:
                _got = False
                for tok in _llm.stream_chat(ext, _msgs, temperature=0.1, max_tokens=2048):
                    _got = True
                    yield evt({'type': 'token', 'text': tok})
                if not _got:
                    yield evt({'type': 'error', 'text': 'The model returned an empty answer.'})
                    return
                yield evt({'type': 'done', 'model': ext['model']})
            except Exception as e:
                yield evt({'type': 'error', 'text': f"{ext['provider']} error: {e}"})
            return

        if ollama_url is None:
            # Fallback: return context without LLM synthesis
            summary = "**Ollama is not available.** Relevant excerpts found:\n\n"
            for i, c in enumerate(chunks[:3], 1):
                summary += f"**[{i}] {c['filename']}**\n{c['content'][:500].strip()}…\n\n"
            yield evt({'type': 'token', 'text': summary})
            yield evt({'type': 'done', 'model': None})
            return

        # Installed cloud registrations first, then installed local chat models.
        from ollama_support import answer, candidates
        chosen_models = candidates(_ollama_model_entries(), model)
        if not chosen_models:
            yield evt({'type': 'error', 'text': 'No Ollama chat models installed. Run: ollama pull llama3.2'})
            return
        yield evt({'type': 'status', 'text': 'Trying installed cloud models, then local fallback…'})
        messages = [{"role": "system", "content": system_prompt}]
        for h in history:
            if isinstance(h, dict) and h.get('role') in ('user', 'assistant') and h.get('content'):
                messages.append({"role": h['role'], "content": str(h['content'])})
        messages.append({"role": "user", "content": user_prompt})
        try:
            chosen, text = answer(ollama_url, chosen_models, messages)
            yield evt({'type': 'token', 'text': text})
            yield evt({'type': 'done', 'model': chosen})
        except RuntimeError as error:
            yield evt({'type': 'error', 'text': str(error)})

    return app.response_class(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ─── Service management (start / stop ES + Ollama) ───────────────────────────

_ES_CONTAINER = 'course-catalog-es'

def _check_service_health(base_url: str, path: str, timeout: int = 3) -> bool:
    """Direct HTTP health check — does NOT use the cached ES client or retry throttle."""
    import urllib.request
    try:
        with urllib.request.urlopen(base_url.rstrip('/') + path, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False

@app.route('/api/services/status')
def services_status():
    """Return live running state of Elasticsearch and Ollama (bypasses retry throttle)."""
    from concurrent.futures import ThreadPoolExecutor      # in parallel: see health() — ~2 s per closed port on Windows
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_es = pool.submit(_check_service_health, ES_URL or 'http://127.0.0.1:9200', '/_cluster/health')
        f_ol = pool.submit(_check_service_health, OLLAMA_URL, '/api/tags')
        es_ok, ollama_ok = f_es.result(), f_ol.result()
    return jsonify({'elasticsearch': es_ok, 'ollama': ollama_ok})

@app.route('/api/services/<service>/<action>', methods=['POST'])
def service_control(service, action):
    """Start or stop Elasticsearch (Docker) or Ollama.
    service: elasticsearch | ollama
    action:  start | stop
    """
    global _es_client, _es_last_failed
    if service not in ('elasticsearch', 'ollama') or action not in ('start', 'stop'):
        abort(400)

    if service == 'elasticsearch':
        try:
            cmd = ['docker', action, _ES_CONTAINER]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if action == 'start' and r.returncode == 0:
                # Reset cached client so the next request reconnects
                _es_client = None
                _es_last_failed = 0.0
            return jsonify({'ok': r.returncode == 0,
                            'output': (r.stdout + r.stderr).strip()})
        except Exception as e:
            return jsonify({'ok': False, 'output': str(e)}), 500

    # ollama
    if action == 'start':
        try:
            proc = subprocess.Popen(
                ['ollama', 'serve'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return jsonify({'ok': True, 'output': f'ollama serve started (pid {proc.pid})'})
        except Exception as e:
            return jsonify({'ok': False, 'output': str(e)}), 500
    else:  # stop
        try:
            if sys.platform == 'win32':
                r = subprocess.run(['taskkill', '/f', '/im', 'ollama.exe'],
                                   capture_output=True, text=True, timeout=10)
            else:
                r = subprocess.run(['pkill', '-f', 'ollama serve'],
                                   capture_output=True, text=True, timeout=10)
            # returncode 1 means "no process found" — still OK
            return jsonify({'ok': r.returncode in (0, 1),
                            'output': (r.stdout + r.stderr).strip() or 'done'})
        except Exception as e:
            return jsonify({'ok': False, 'output': str(e)}), 500

@app.route('/api/services/backend/<action>', methods=['POST'])
def backend_control(action):
    """Stop or restart the Flask backend process itself.
    action: stop | restart
    Sends the JSON response first, then performs the action after a short delay.
    """
    if action not in ('stop', 'restart'):
        abort(400)

    def _deferred():
        import time
        time.sleep(0.5)          # let Flask flush the response
        if action == 'restart':
            log.info("Restarting backend via os.execv …")
            if sys.platform == 'win32':
                subprocess.Popen([sys.executable] + sys.argv)
                os._exit(0)
            else:
                os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            log.info("Stopping backend via os._exit …")
            os._exit(0)

    threading.Thread(target=_deferred, daemon=True).start()
    return jsonify({'ok': True, 'action': action,
                    'start_cmd': f'python {sys.argv[0]}'})


@app.route('/api/ollama-models')
def ollama_models():
    """Return list of available Ollama models and running status."""
    url = _get_ollama()
    if url is None:
        return jsonify({'available': False, 'models': [], 'url': OLLAMA_URL})
    return jsonify({'available': True, 'url': url, 'models': _ollama_list_models()})


# ─── External LLM providers (OpenAI / Anthropic / OpenAI-compatible) ──────────

def _llm_mod():
    """Lazy-import llm_providers from the backend/ directory."""
    import importlib, sys
    _backend_dir = str(Path(__file__).parent)
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
    return importlib.import_module('llm_providers')


@app.route('/api/llm/models', methods=['POST'])
def llm_models():
    """List models of a provider. Body: {provider, base_url, api_key}. Key is never stored."""
    llm = _llm_mod()
    try:
        models = llm.list_models(request.get_json(silent=True) or {})
        return jsonify({'ok': True, 'models': models})
    except llm.LLMError as e:
        return jsonify({'ok': False, 'error': str(e), 'models': []})
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Unexpected response from server: {e}', 'models': []})


@app.route('/api/llm/test', methods=['POST'])
def llm_test():
    """Test credentials (lists models) and, when a model is set, run a 1-word completion."""
    llm = _llm_mod()
    try:
        return jsonify(llm.test_connection(request.get_json(silent=True) or {}))
    except llm.LLMError as e:
        return jsonify({'ok': False, 'error': str(e)})

# ─── ES search helpers ────────────────────────────────────────────────────────

def _detect_query_language(text: str) -> Optional[str]:
    """Detect 2-letter ISO 639-1 language code for *text*.

    Uses langdetect if available; returns None on any failure so callers can
    safely skip translation without crashing.
    """
    try:
        from langdetect import detect
        sample = text[:500].strip()
        if not sample:
            return None
        return detect(sample)
    except Exception:
        return None


def _translate_query(text: str, target_lang: str) -> Optional[str]:
    """Translate *text* to *target_lang* via Ollama.

    Uses TRANSLATION_MODEL (from env.py).  Returns the translated string or
    None on any failure so callers can fall back silently.
    """
    _LANG_NAMES = {
        'en': 'English', 'es': 'Spanish', 'de': 'German',
        'fr': 'French',  'pt': 'Portuguese', 'it': 'Italian',
        'nl': 'Dutch',   'pl': 'Polish',      'ru': 'Russian',
        'zh': 'Chinese', 'ja': 'Japanese',    'ko': 'Korean',
        'ar': 'Arabic',  'tr': 'Turkish',     'sv': 'Swedish',
    }
    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    prompt = f"Translate to {lang_name}: {text}\nTranslation:"
    payload = json.dumps({
        "model":  TRANSLATION_MODEL,
        "prompt": prompt,
        "stream": False,
    }).encode('utf-8')
    try:
        import urllib.request as _ureq
        req = _ureq.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _ureq.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        result = data.get('response', '').strip()
        return result if result else None
    except Exception:
        return None


# Languages the search terms are translated into (the corpus language). env.py / env var
# RAG_TRANSLATE_QUERY_TO, comma-separated, '' disables translation. Default: German.
_QUERY_LANG_NAMES = {'de': 'German', 'en': 'English', 'es': 'Spanish', 'fr': 'French'}
try:
    _QUERY_TRANSLATE_TO = [x.strip().lower() for x in str(
        getattr(env, 'RAG_TRANSLATE_QUERY_TO', os.environ.get('RAG_TRANSLATE_QUERY_TO', 'de'))).split(',')
        if x.strip().lower() in _QUERY_LANG_NAMES]
except NameError:
    _QUERY_TRANSLATE_TO = ['de']


def _multilang_query_prompt(question: str) -> str:
    """One LLM call: key search terms in the question's language + the same terms translated."""
    targets = [_QUERY_LANG_NAMES[x] for x in _QUERY_TRANSLATE_TO]
    lines = ["line 1: the terms in the SAME language as the question"]
    lines += [f"line {i + 2}: the same terms in {name}" for i, name in enumerate(targets)]
    return (
        "You prepare queries for a document search engine over technical documentation "
        "(manuals, courses, tickets) that is mostly written in German, partly in English. "
        "Extract 3-7 key search terms from the user question. "
        f"Output exactly {len(lines)} lines, nothing else: no labels, no explanation, no punctuation.\n"
        + "\n".join(lines) + "\n"
        "Keep product names, error codes, file names and identifiers exactly as written.\n"
        f"Question: {question}\n"
        "Search terms:"
    )


_LABEL_RE = re.compile(r'^\s*(?:line\s*\d+|german|deutsch|english|spanish|espa[nñ]ol|french|de|en|es|fr)\s*[:\-–]\s*',
                       re.I)


def _split_query_variants(raw: str, fallback: str) -> tuple:
    """Parse the LLM output into (primary_terms, [translations]); never raises."""
    seen, out = set(), []
    for ln in (raw or '').splitlines():
        ln = _LABEL_RE.sub('', ln).strip().strip('`"\'').rstrip('.').strip()
        if len(ln) >= 3 and ln.lower() not in seen:
            seen.add(ln.lower())
            out.append(ln)
    if not out:
        return fallback, []
    return out[0], out[1:4]


_SEARCH_QUERY_EXTRACTION_PROMPT = (
    "You are a search engine query optimizer. "
    "Extract 3-7 key search terms from the user question to retrieve technical documents. "
    "Rules: return ONLY the terms separated by spaces, no explanation, no punctuation. "
    "Include technical variants and German equivalents if the topic is DMS/ELO/enterprise software. "
    "Question: {question}\n"
    "Search terms:"
)

def _extract_search_query(question: str, ollama_url: str, model: str) -> str:
    """Stage-1 RAG: ask the LLM to reformulate the user question as ES search terms.

    Uses a short, fast prompt (temperature=0, max 60 tokens) to extract keywords.
    Falls back to the original question on any error so callers are never blocked.
    """
    if not ollama_url or not question:
        return question
    try:
        import urllib.request as _ureq
        prompt = _SEARCH_QUERY_EXTRACTION_PROMPT.format(question=question)
        payload = json.dumps({
            "model":  model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 60},
        }).encode('utf-8')
        req = _ureq.Request(
            f"{ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ureq.urlopen(req, timeout=35) as resp:
            result = json.loads(resp.read()).get("response", "").strip()
        # Take only the first line (model may add explanation after a newline)
        result = result.split("\n")[0].strip().rstrip(".")
        return result if len(result) >= 3 else question
    except Exception:
        return question


def _search_via_es(query: str, lang: str, limit: int, mode: dict):
    """Execute ES search and return jsonify response compatible with /api/search format."""
    es = _get_es()
    if es is None:
        return None  # caller handles fallback

    # If canonical language is set and query language differs, translate query first
    translated_query = query
    if CANONICAL_LANGUAGE and TRANSLATION_MODE != 'disabled':
        detected_q_lang = _detect_query_language(query)
        if detected_q_lang and detected_q_lang != CANONICAL_LANGUAGE:
            translated = _translate_query(query, CANONICAL_LANGUAGE)
            if translated:
                translated_query = translated

    lang_boost = {'de': 'content.de^3', 'es': 'content.es^3', 'en': 'content.en^3'}
    if CANONICAL_LANGUAGE and not lang:
        # Use canonical content field for language-agnostic BM25
        lang_boost_canonical = {
            'en': 'canonical_content.en^3',
            'es': 'canonical_content.es^3',
            'de': 'canonical_content.de^3',
        }
        content_fields = [lang_boost_canonical.get(CANONICAL_LANGUAGE, 'canonical_content'), 'canonical_content', 'content']
    elif lang in lang_boost:
        content_fields = [lang_boost[lang], 'content']
    else:
        content_fields = ['content']

    bm25_query = {
        "multi_match": {
            "query":     translated_query,
            "fields":    content_fields,
            "type":      "best_fields",
            "fuzziness": "AUTO",
        }
    }

    body = {
        "_source": ["path", "rel_path", "filename", "ext", "content", "canonical_content",
                    "chunk_index", "srt_start_time", "srt_end_time"],
        "size": limit,
        "highlight": {
            "fields": {"content": {"fragment_size": 300, "number_of_fragments": 2}},
            "pre_tags":  ["**"],
            "post_tags": ["**"],
        },
        "query": bm25_query,
    }

    t0 = time.perf_counter()
    log.debug(f"ES _search_via_es body:\n{json.dumps(body, indent=2, ensure_ascii=False)}")
    try:
        resp = es.search(index=ES_INDEX, body=body)
    except Exception as e:
        log.error(f"ES search error: {e}")
        return None  # caller handles fallback

    seen_paths: dict = {}
    for hit in resp["hits"]["hits"]:
        src   = hit["_source"]
        score = hit.get("_score") or 0.0
        highlights = hit.get("highlight", {}).get("content", [])
        context = ' … '.join(highlights) if highlights else src.get("content", "")[:400]

        match_entry = {
            "line":          src.get("chunk_index", 0),
            "context":       context[:400],
            "highlight":     query,
            "score":         round(score, 4),
            "chunk_content": src.get("content", ""),
        }
        # Include SRT timestamp when available
        if src.get("ext") in ("srt", "vtt") and "srt_start_time" in src:
            match_entry["srt_time"] = src["srt_start_time"]

        pk = src.get("path", "")
        if pk in seen_paths:
            seen_paths[pk]["matches"].append(match_entry)
            seen_paths[pk]["match_count"] += 1
            if score > seen_paths[pk]["score"]:
                seen_paths[pk]["score"] = round(score, 4)
        else:
            seen_paths[pk] = {
                "path":        pk,
                "file":        src.get("filename", ""),
                "rel_path":    src.get("rel_path", ""),
                "type":        src.get("ext", ""),
                "score":       round(score, 4),
                "match_count": 1,
                "matches":     [match_entry],
            }

    results = sorted(seen_paths.values(), key=lambda r: r["score"], reverse=True)
    elapsed = round(time.perf_counter() - t0, 3)

    es_mode = dict(mode)
    es_mode["engine"] = "elasticsearch"

    return jsonify({
        "query":     query,
        "total":     sum(r["match_count"] for r in results),
        "results":   results,
        "mode":      es_mode,
        "elapsed_s": elapsed,
    })


@app.route('/api/es-search')
def es_search():
    """Full-text + optional semantic search via Elasticsearch.

    Query params:
        q               — search term (required, min 2 chars)
        lang            — language analyzer: 'de', 'es', 'en', or '' (default)
        semantic        — '1' to enable kNN semantic search (requires indexed vectors)
        semantic_only   — '1' for pure kNN search (no BM25) — language-agnostic cross-lingual
        filename_search — '1' to search by filename/path instead of content; supports * and ? wildcards
        limit           — max results (default MAX_RESULTS, max 200)
        types           — comma-separated file extensions to filter (e.g. 'pdf,md,srt')
    """
    query         = request.args.get('q', '').strip()
    lang          = request.args.get('lang', '').strip().lower()
    semantic_only = request.args.get('semantic_only', '0') == '1'
    semantic      = semantic_only or (request.args.get('semantic', '0') == '1')
    limit = min(int(request.args.get('limit', MAX_RESULTS)), 200)
    _types_raw    = request.args.get('types', '').strip()
    filter_exts   = {t.strip().lower().lstrip('.') for t in _types_raw.split(',') if t.strip()} if _types_raw else set()

    filename_search = request.args.get('filename_search', '0') == '1'

    if not query or len(query) < 2:
        # Allow match-all when a type filter is set or query is the '*' sentinel
        if query == '*' or (not query and filter_exts):
            query = '*'
        else:
            return jsonify({"error": "Query too short", "total": 0, "results": []})

    if lang not in ('de', 'es', 'en', ''):
        lang = ''

    es = _get_es()
    if es is None:
        return jsonify({"error": "Elasticsearch is not configured or not reachable. "
                                 "Set ES_URL in env.py and ensure ES is running."}), 503

    # ── Translate query when canonical language is set (non-multilingual models only) ──
    translated_query = query
    if CANONICAL_LANGUAGE and TRANSLATION_MODE != 'disabled' and not _MODEL_IS_MULTILINGUAL and query != '*':
        detected_q_lang = _detect_query_language(query)
        if detected_q_lang and detected_q_lang != CANONICAL_LANGUAGE:
            translated = _translate_query(query, CANONICAL_LANGUAGE)
            if translated:
                translated_query = translated

    # ── Build content field list with language boosting ──────────────────────
    # e.g. for German: content.de^3 (German stemmer) + content (standard) fallback
    lang_boost = {'de': 'content.de^3', 'es': 'content.es^3', 'en': 'content.en^3'}
    if CANONICAL_LANGUAGE and not lang:
        # Use canonical content field for language-agnostic BM25
        lang_boost_canonical = {
            'en': 'canonical_content.en^3',
            'es': 'canonical_content.es^3',
            'de': 'canonical_content.de^3',
        }
        content_fields = [lang_boost_canonical.get(CANONICAL_LANGUAGE, 'canonical_content'), 'canonical_content', 'content']
    elif lang in lang_boost:
        content_fields = [lang_boost[lang], 'content']
    else:
        content_fields = ['content']

    # '*' is the match-all sentinel (no query text supplied)
    if query == '*':
        bm25_match = {"match_all": {}}
    else:
        bm25_match = {
            "multi_match": {
                "query":     translated_query,
                "fields":    content_fields,
                "type":      "best_fields",
                "fuzziness": "AUTO",
            }
        }

    # Wrap with extension filter when types are specified
    ext_filter = {"terms": {"ext": sorted(filter_exts)}} if filter_exts else None
    if ext_filter:
        bm25_query = {"bool": {"must": bm25_match, "filter": ext_filter}} if query != '*' \
                     else {"bool": {"filter": ext_filter}}
    else:
        bm25_query = bm25_match

    body: dict = {
        "_source": ["path", "rel_path", "filename", "ext", "content", "canonical_content",
                    "chunk_index", "srt_start_time", "srt_end_time"],
        "size": limit,
        "highlight": {
            "fields": {"content": {"fragment_size": 300, "number_of_fragments": 2}},
            "pre_tags":  ["**"],
            "post_tags": ["**"],
        },
    }

    if filename_search:
        # ── Filename / path search — matches against filename and rel_path fields ──
        fn_q = query.lower()
        if '*' in fn_q or '?' in fn_q:
            fn_cond = {"wildcard": {"filename": {"value": fn_q, "case_insensitive": True}}}
        else:
            fn_cond = {"multi_match": {
                "query":  query,
                "fields": ["filename^3", "rel_path"],
                "type":   "phrase_prefix",
            }}
        body["query"] = {"bool": {"must": fn_cond, "filter": ext_filter}} if ext_filter else fn_cond
        body.pop("highlight", None)   # no text highlights needed for filename search
        # Collapse to one result per unique file path so `limit` = number of unique files
        body["collapse"] = {"field": "path"}
        semantic = False
    elif semantic and query != '*':
        # Use translated query for vector if canonical_language is active and model is not multilingual
        _vec_query = translated_query if (CANONICAL_LANGUAGE and TRANSLATION_MODE != 'disabled'
                                          and not _MODEL_IS_MULTILINGUAL) else query
        vector = _query_vector(_vec_query)
        if vector is not None:
            knn_block = {
                "field":          "content_vector",
                "query_vector":   vector,
                "k":              limit,
                "num_candidates": limit * 10,
                "boost":          0.5,
            }
            if ext_filter:
                knn_block["filter"] = ext_filter
            if semantic_only:
                # Pure kNN — no BM25, fully language-agnostic cross-lingual search
                body["knn"] = knn_block
            else:
                # Hybrid BM25 + kNN — ES 8.x combines scores automatically
                body["query"] = bm25_query
                body["knn"]   = knn_block
        else:
            body["query"] = bm25_query
            semantic = False  # no model available — fall back to BM25
    else:
        body["query"] = bm25_query

    log.debug(f"ES /api/es-search body:\n{json.dumps(body, indent=2, ensure_ascii=False)}")
    try:
        resp = es.search(index=ES_INDEX, body=body)
    except Exception as e:
        log.error(f"ES search failed: {e}")
        return jsonify({"error": f"Elasticsearch search error: {e}"}), 500

    # ── Format results (same shape as /api/search) ───────────────────────────
    seen_paths: dict = {}   # path → result entry (merge chunks from same file)

    for hit in resp["hits"]["hits"]:
        src   = hit["_source"]
        score = hit.get("_score") or 0.0
        # Prefer highlighted snippet, fallback to raw content
        highlights = hit.get("highlight", {}).get("content", [])
        context = ' … '.join(highlights) if highlights else src.get("content", "")[:400]

        match_entry = {
            "line":          src.get("chunk_index", 0),
            "context":       context[:400],
            "highlight":     query,
            "score":         round(score, 4),
            "chunk_content": src.get("content", ""),
        }
        # Include SRT timestamp when available
        if src.get("ext") in ("srt", "vtt") and "srt_start_time" in src:
            match_entry["srt_time"] = src["srt_start_time"]

        path_key = src.get("path", "")
        if path_key in seen_paths:
            seen_paths[path_key]["matches"].append(match_entry)
            seen_paths[path_key]["match_count"] += 1
        else:
            seen_paths[path_key] = {
                "path":        path_key,
                "file":        src.get("filename", ""),
                "rel_path":    src.get("rel_path", ""),
                "type":        src.get("ext", ""),
                "score":       round(score, 4),
                "match_count": 1,
                "matches":     [match_entry],
            }

    results = sorted(seen_paths.values(), key=lambda r: r["score"], reverse=True)

    return jsonify({
        "query":   query,
        "total":   sum(r["match_count"] for r in results),
        "results": results,
        "mode":    {"lang": lang, "semantic": semantic, "engine": "elasticsearch",
                    **({"types": sorted(filter_exts)} if filter_exts else {})},
    })

@app.route('/api/translate-status')
def translate_status():
    """Return translation queue progress."""
    queue_file  = Path(__file__).parent / 'translation_queue.jsonl'
    status_file = Path(__file__).parent / 'translation_status.json'

    # Try reading pre-computed status file first (written by worker)
    if status_file.exists():
        try:
            with open(status_file, encoding='utf-8') as f:
                data = json.load(f)
            data['canonical_language'] = CANONICAL_LANGUAGE
            data['queue_file'] = str(queue_file)
            return jsonify(data)
        except Exception:
            pass

    # Fallback: count from queue file directly
    total = pending = done = failed = 0
    if queue_file.exists():
        try:
            with open(queue_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        task = json.loads(line)
                        total += 1
                        s = task.get('status', 'pending')
                        if s == 'pending':   pending += 1
                        elif s == 'done':    done += 1
                        elif s == 'failed':  failed += 1
                    except Exception:
                        pass
        except Exception:
            pass

    return jsonify({
        'total': total,
        'pending': pending,
        'done': done,
        'failed': failed,
        'worker_running': False,
        'canonical_language': CANONICAL_LANGUAGE,
        'queue_file': str(queue_file),
        'last_updated': None,
    })


@app.route('/api/analytics')
def analytics():
    """Return corpus-level analytics: doc counts, language breakdown,
    avg doc length, top keywords, file types, translation stats, and
    summarization/entity-extraction progress from analytics_status.json.
    """
    import urllib.request as _ureq
    import urllib.error as _uerr

    status_file = Path(__file__).parent / 'analytics_status.json'

    if not ES_URL:
        return jsonify({'error': 'ES not configured'}), 503

    # ── ES aggregations ───────────────────────────────────────────────────────
    def _es_post(path: str, body: dict) -> dict:
        url  = ES_URL.rstrip('/') + path
        data = json.dumps(body).encode('utf-8')
        req  = _ureq.Request(url, data=data,
                             headers={'Content-Type': 'application/json'},
                             method='POST')
        with _ureq.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))

    try:
        agg_body = {
            'size': 0,
            'aggs': {
                'by_language': {
                    'terms': {'field': 'detected_lang', 'size': 30},
                },
                'avg_content_length': {
                    'avg': {'field': 'content_length'},
                },
                'avg_word_count': {
                    'avg': {'field': 'word_count'},
                },
                'top_file_types': {
                    'terms': {'field': 'file_type', 'size': 20},
                },
                'translation_stats': {
                    'terms': {'field': 'translation_status', 'size': 10},
                },
                'top_keywords': {
                    'significant_terms': {
                        'field': 'content',
                        'size':  20,
                        'min_doc_count': 2,
                    },
                },
            },
        }
        resp = _es_post(f'/{ES_INDEX}/_search', agg_body)
        aggs  = resp.get('aggregations', {})
        total = resp.get('hits', {}).get('total', {})
        doc_count = total.get('value', 0) if isinstance(total, dict) else (total or 0)

        def _buckets(name: str) -> list:
            return [
                {'key': b['key'], 'count': b['doc_count']}
                for b in aggs.get(name, {}).get('buckets', [])
            ]

        avg_len = aggs.get('avg_content_length', {}).get('value')
        avg_wc  = aggs.get('avg_word_count',     {}).get('value')

        kw_buckets = aggs.get('top_keywords', {}).get('buckets', [])
        top_keywords = [
            {'term': b['key'], 'score': round(b.get('score', 0), 4),
             'doc_count': b['doc_count']}
            for b in kw_buckets
        ]

    except (_uerr.URLError, OSError) as e:
        return jsonify({'error': f'ES not available: {e}'}), 503
    except Exception as e:
        log.error(f'/api/analytics ES query failed: {e}')
        return jsonify({'error': str(e)}), 500

    # ── Summarization progress from status file ───────────────────────────────
    summarization_progress = {
        'total': doc_count, 'summarized': 0, 'entities_extracted': 0,
        'worker_running': False, 'last_updated': None,
    }
    if status_file.exists():
        try:
            with open(status_file, encoding='utf-8') as f:
                st = json.load(f)
            summarization_progress.update(st)
        except Exception:
            pass

    return jsonify({
        'doc_count':              doc_count,
        'by_language':            _buckets('by_language'),
        'avg_doc_length':         round(avg_len, 1) if avg_len is not None else None,
        'avg_word_count':         round(avg_wc,  1) if avg_wc  is not None else None,
        'top_keywords':           top_keywords,
        'top_file_types':         _buckets('top_file_types'),
        'translation_stats':      _buckets('translation_stats'),
        'summarization_progress': summarization_progress,
        'last_updated':           summarization_progress.get('last_updated'),
    })


@app.route('/api/ping')
def ping():
    """Instant liveness probe — used by the launcher to detect startup.
    Returns immediately without touching ES or Ollama."""
    return jsonify({"status": "ok"})


@app.route('/api/health')
def health():
    # Probe both services AT ONCE. A refused connection to a closed port costs ~2 s on Windows, so probing
    # Elasticsearch and Ollama one after the other (4 s) exceeded the 3 s the UI gives this endpoint and a
    # perfectly healthy backend was shown as "unreachable" on the first load.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as _pool:
        _f_es = _pool.submit(_get_es) if ES_URL else None
        _f_ol = _pool.submit(_get_ollama)
        es = _f_es.result() if _f_es else None
        ollama_url = _f_ol.result()
    es_status = "not configured" if not ES_URL else ("ok" if es is not None else "unreachable")
    ollama_models = _ollama_list_models() if ollama_url else []
    return jsonify({
        "status": "ok",
        "base_path": str(SEARCH_BASE_PATH),
        "base_paths": [str(p) for p in SEARCH_BASE_PATHS],
        "elasticsearch": es_status,
        "ollama": "ok" if ollama_url else "unavailable",
        "ollama_models": ollama_models,
        "supported_extensions": sorted(SEARCHABLE_EXTENSIONS),
    })

@app.route('/api/cache', methods=['GET', 'POST'])
def build_cache():
    """Build/update text cache for all searchable files under SEARCH_BASE_PATH.
    Skips files whose cache is already up to date.
    Returns JSON stats: processed, skipped, errors, total, elapsed_s.
    """
    t_start = time.perf_counter()
    stats = {"processed": 0, "skipped": 0, "errors": 0, "total": 0}
    log.info(f"Cache build started for {SEARCH_BASE_PATHS}")
    print(f"\n{'='*60}", flush=True)
    print(f"  Building cache: {'; '.join(str(p) for p in SEARCH_BASE_PATHS)}", flush=True)
    print(f"{'='*60}", flush=True)

    # Progress tracking: list top-level dirs across all base paths
    _top_dirs = []
    for _bp in SEARCH_BASE_PATHS:
        try:
            _top_dirs.extend(sorted(d for d in os.listdir(_bp)
                                    if not d.startswith('.') and (_bp / d).is_dir()))
        except Exception:
            pass
    _total_top = len(_top_dirs)
    _cur_top = [None]
    _top_idx = [0]

    for _bp in SEARCH_BASE_PATHS:
      for root, dirs, files in os.walk(_bp):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        root_path = Path(root)
        if root_path.parent == _bp:
            name = root_path.name
            if name != _cur_top[0]:
                _cur_top[0] = name
                _top_idx[0] += 1
                _pct = f"{_top_idx[0]}/{_total_top}" if _total_top else str(_top_idx[0])
                print(f"\n  [{_pct}] {name}", flush=True)

        for fname in files:
            ext = Path(fname).suffix.lstrip('.').lower()
            if ext not in SEARCHABLE_EXTENSIONS:
                continue
            fpath = Path(root) / fname
            stats["total"] += 1
            try:
                if ext == 'pdf':
                    cp = get_pdf_cache_path(fpath)
                    if _is_fresh(cp, fpath):
                        stats["skipped"] += 1
                        continue
                    pages = _extract_pdf_pages_for_cache(str(fpath))
                    if pages and write_pdf_cache(fpath, pages):
                        stats["processed"] += 1
                    else:
                        stats["errors"] += 1
                else:
                    if _is_fresh(get_cache_path(fpath), fpath):
                        stats["skipped"] += 1
                        continue
                    text = extract_text(str(fpath), ext) or ''
                    if text and write_to_cache(fpath, text):
                        stats["processed"] += 1
                    else:
                        stats["errors"] += 1
            except Exception as e:
                log.debug(f"Cache error {fpath}: {e}")
                stats["errors"] += 1

    elapsed = time.perf_counter() - t_start
    stats["elapsed_s"] = round(elapsed, 3)
    log.info(f"Cache build done: {stats}")
    print(f"\n{'='*60}", flush=True)
    print(f"  ✓ Cache built — {stats['processed']} new, {stats['skipped']} up-to-date, {stats['errors']} errors — {elapsed:.2f}s", flush=True)
    print(f"{'='*60}\n", flush=True)
    return jsonify(stats)

# ─── Tools API ────────────────────────────────────────────────────────────────
# Background process tracking for long-running tool tasks
_tool_processes: dict = {}  # task_id → {process, log, status, ...}
_tool_lock = threading.Lock()

def _run_tool_bg(task_id: str, cmd: list, cwd: str = None, task_type: str = '', on_done=None):
    """Run a command in background, capture output in _tool_processes.

    on_done(status, info) — optional callback when the process ends ('done' | 'error');
    `info` is a copy of the task entry (progress_total, returncode, …).
    """
    _PROGRESS_RE = re.compile(r'\[(\d+)/(\d+)\]')
    # "[12/8237] file name.pdf  (34 chunks)"
    _FILE_RE  = re.compile(r'\[(\d+)/(\d+)\]\s+(.+?)\s+\((\d+) chunks\)\s*$')
    # "embedding 128/1458 chunks (8%) — file.json  elapsed 52s, ~560s left"
    # (the separator after "(92%)" is matched as any non-space token, so a dash in any encoding works)
    _EMBED_RE = re.compile(r'embedding (\d+)/(\d+) chunks \(\d+%\)\s+\S+\s+(.+?)\s{2}elapsed (\d+)s, ~(\d+)s left')
    _PHASES = (('Loading embedding model', 'loading model'), ('Embedding model ready', 'model ready'),
               ('Incremental mode', 'scanning index'), ('new/changed files', 'scanning files'),
               ('Indexing ', 'indexing'), ('Waiting for Elasticsearch', 'waiting for Elasticsearch'),
               ('Nothing new', 'nothing to do'), ('Indexed ', 'finishing'))

    def _runner():
        try:
            # Force UTF-8 on both ends: on Windows the child (Python) writes UTF-8 to the pipe
            # while text=True would decode with the ANSI code page (cp1252) → "—" shows as "â€”".
            child_env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                cwd=cwd, bufsize=1, env=child_env,
            )
            with _tool_lock:
                _tool_processes[task_id]['pid'] = proc.pid
                _tool_processes[task_id]['status'] = 'running'
            for line in proc.stdout:
                line = line.rstrip('\n')
                with _tool_lock:
                    entry = _tool_processes[task_id]
                    entry['log'].append(line)
                    entry['last_activity'] = time.time()
                    if len(entry['log']) > 500:
                        # keep an absolute line counter so SSE clients never lose their place
                        drop = len(entry['log']) - 500
                        entry['log'] = entry['log'][drop:]
                        entry['log_base'] += drop
                    for key, phase in _PHASES:
                        if key in line:
                            entry['phase'] = phase
                            break
                    mf = _FILE_RE.search(line)
                    if mf:
                        entry.update(phase='processing', file=mf.group(3), file_chunks=int(mf.group(4)),
                                     embed_done=0, embed_total=0, embed_eta=0)
                    me = _EMBED_RE.search(line)
                    if me:
                        entry.update(phase='embedding', file=me.group(3).strip(),
                                     embed_done=int(me.group(1)), embed_total=int(me.group(2)),
                                     embed_eta=int(me.group(5)))
                    m = _PROGRESS_RE.search(line)
                    if m:
                        n, total = int(m.group(1)), int(m.group(2))
                        entry['progress'] = round(n / total * 100) if total > 0 else 0
                        entry['progress_n'] = n
                        entry['progress_total'] = total
            proc.wait()
            with _tool_lock:
                _tool_processes[task_id]['status'] = 'done' if proc.returncode == 0 else 'error'
                _tool_processes[task_id]['returncode'] = proc.returncode
        except Exception as e:
            with _tool_lock:
                _tool_processes[task_id]['status'] = 'error'
                _tool_processes[task_id]['log'].append(str(e))
        if on_done:
            try:
                with _tool_lock:
                    snap = {k: v for k, v in _tool_processes[task_id].items() if k != 'log'}
                on_done(snap['status'], snap)
            except Exception as e:                       # a callback must never break the task
                log.warning(f"on_done callback failed for {task_id}: {e}")

    with _tool_lock:
        _tool_processes[task_id] = {
            'status': 'starting', 'log': [], 'log_base': 0, 'cmd': ' '.join(cmd),
            'type': task_type, 'progress': 0, 'phase': 'starting',
            'started': time.time(), 'last_activity': time.time(),
        }
    t = threading.Thread(target=_runner, daemon=True)
    t.start()


@app.route('/api/tools/status/<task_id>')
def tool_status(task_id):
    """Return status and log for a background tool task."""
    with _tool_lock:
        info = _tool_processes.get(task_id)
    if not info:
        return jsonify({"error": "Unknown task"}), 404
    return jsonify(info)


@app.route('/api/tools/stream/<task_id>')
def tool_stream(task_id):
    """Stream log lines from a background task as SSE events."""
    # `?from=N` resumes after absolute log line N (sent as the SSE `id:` of every log event)
    try:
        start_at = max(0, int(request.args.get('from', 0)))
    except ValueError:
        start_at = 0

    def _snapshot(info: dict) -> dict:
        """Structured progress the UI renders (file, embedding sub-progress, timings)."""
        now = time.time()
        return {
            'percent': info.get('progress', 0), 'n': info.get('progress_n', 0),
            'total': info.get('progress_total', 0), 'phase': info.get('phase', ''),
            'file': info.get('file', ''), 'file_chunks': info.get('file_chunks', 0),
            'embed_done': info.get('embed_done', 0), 'embed_total': info.get('embed_total', 0),
            'embed_eta': info.get('embed_eta', 0),
            'elapsed': int(now - info.get('started', now)),
            'idle': int(now - info.get('last_activity', now)),
        }

    def generate():
        # NOTE: the stored log is trimmed to the last 500 lines, so `sent` is an
        # ABSOLUTE line number (log_base + index). Indexing the trimmed list with a
        # running counter froze the stream after ~500 lines (UI stuck at ~447 files).
        sent = start_at
        last_key = None
        last_beat = 0.0
        while True:
            with _tool_lock:
                info = _tool_processes.get(task_id)
                if info:
                    base = info.get('log_base', 0)
                    status = info.get('status', 'unknown')
                    if sent < base:
                        sent = base                      # skipped lines were trimmed away
                    new_lines = info['log'][sent - base:]
                    snap = _snapshot(info)
            if not info:
                yield f'event: error\ndata: {json.dumps({"error": "Unknown task"})}\n\n'
                return
            for line in new_lines:
                sent += 1
                yield f'id: {sent}\nevent: log\ndata: {json.dumps(line)}\n\n'
            key = (snap['n'], snap['total'], snap['file'], snap['embed_done'], snap['phase'])
            if key != last_key:
                last_key = key
                yield f'event: progress\ndata: {json.dumps(snap)}\n\n'
            if status in ('done', 'error'):
                yield f'event: done\ndata: {json.dumps({"status": status})}\n\n'
                return
            if time.time() - last_beat >= 2.0:           # keep-alive + "still working" signal
                last_beat = time.time()
                yield f'event: heartbeat\ndata: {json.dumps(dict(snap, status=status))}\n\n'
            time.sleep(0.25)

    resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@app.route('/api/tools/active')
def tools_active():
    """Return currently running (or starting) tool tasks, keyed by type."""
    with _tool_lock:
        active = {
            tid: {'status': info['status'], 'type': info.get('type', ''),
                  'progress': info.get('progress', 0),
                  'n': info.get('progress_n', 0), 'total': info.get('progress_total', 0),
                  'phase': info.get('phase', ''), 'file': info.get('file', '')}
            for tid, info in _tool_processes.items()
            if info.get('status') in ('starting', 'running')
        }
    return jsonify(active)


@app.route('/api/tools/config', methods=['GET', 'POST'])
def tools_config():
    """GET: return current embedding model config. POST: update env.py."""
    global CANONICAL_LANGUAGE, TRANSLATION_MODEL, TRANSLATION_MODE
    global _EMBEDDING_MODEL, _NORMALIZE_EMBEDDING_CASE, _MODEL_IS_MULTILINGUAL
    script_dir = Path(__file__).parent
    env_file = script_dir / 'env.py'

    if request.method == 'GET':
        return jsonify({
            "embedding_model": _EMBEDDING_MODEL,
            "normalize_case":  _NORMALIZE_EMBEDDING_CASE,
            "model_is_multilingual": _MODEL_IS_MULTILINGUAL,
            "canonical_language": CANONICAL_LANGUAGE,
            "translation_model":  TRANSLATION_MODEL,
            "translation_mode":   TRANSLATION_MODE,
            "models": {k: {
                "dims":         v["dims"],
                "normalize_case": v["normalize_case"],
                "multilingual": v.get("multilingual", False),
                "description":  v.get("description", ""),
            } for k, v in _MODEL_CONFIG.items()},
        })

    # POST — update EMBEDDING_MODEL + canonical language settings in env.py
    import re as _re
    data = request.get_json(force=True) or {}
    new_model = data.get('embedding_model', '').strip()
    valid_models = list(_MODEL_CONFIG.keys())
    if new_model not in valid_models:
        return jsonify({"error": f"Invalid model. Choose from: {valid_models}"}), 400

    new_canonical  = data.get('canonical_language', CANONICAL_LANGUAGE).strip()
    new_trans_model = data.get('translation_model', TRANSLATION_MODEL).strip()
    new_trans_mode  = data.get('translation_mode', TRANSLATION_MODE).strip()

    try:
        content = env_file.read_text(encoding='utf-8')

        def _replace_or_append(text, var, value):
            pattern = rf'^{var}\s*=\s*["\'].*?["\']'
            replacement = f'{var} = "{value}"'
            if _re.search(pattern, text, flags=_re.MULTILINE):
                return _re.sub(pattern, replacement, text, count=1, flags=_re.MULTILINE)
            return text + f'\n{replacement}\n'

        content = _replace_or_append(content, 'EMBEDDING_MODEL',  new_model)
        content = _replace_or_append(content, 'CANONICAL_LANGUAGE', new_canonical)
        content = _replace_or_append(content, 'TRANSLATION_MODEL',  new_trans_model)
        content = _replace_or_append(content, 'TRANSLATION_MODE',   new_trans_mode)
        env_file.write_text(content, encoding='utf-8')

        # Update in-memory globals so queries immediately reflect new settings
        CANONICAL_LANGUAGE  = new_canonical
        TRANSLATION_MODEL   = new_trans_model
        TRANSLATION_MODE    = new_trans_mode
        _EMBEDDING_MODEL    = new_model
        _mcfg2 = _MODEL_CONFIG.get(new_model, {'dims': 384, 'normalize_case': True, 'multilingual': False})
        _NORMALIZE_EMBEDDING_CASE = _mcfg2['normalize_case']
        _MODEL_IS_MULTILINGUAL    = _mcfg2.get('multilingual', False)

        return jsonify({"ok": True, "model": new_model,
                        "canonical_language": new_canonical,
                        "translation_mode": new_trans_mode,
                        "note": "Config saved. Reindex required if model changed."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _append_search_path(new_path: str) -> bool:
    """Append new_path to SEARCH_BASE_PATH in env.py (semicolon-separated).
    Returns True if the path was added, False if already present."""
    global SEARCH_BASE_PATHS, SEARCH_BASE_PATH
    import re as _re

    resolved = Path(new_path).resolve()
    # Check if already in the list
    for bp in SEARCH_BASE_PATHS:
        if bp == resolved:
            return False

    env_file = Path(__file__).parent / 'env.py'
    content = env_file.read_text(encoding='utf-8')
    pattern = _re.compile(r'^(SEARCH_BASE_PATH\s*=\s*r?["\'])(.+?)(["\'])\s*$', _re.MULTILINE)
    match = pattern.search(content)
    if match:
        prefix, old_val, quote = match.group(1), match.group(2), match.group(3)
        new_val = old_val.rstrip(';') + ';' + str(resolved)
        new_line = f'{prefix}{new_val}{quote}'
        content = content[:match.start()] + new_line + content[match.end():]
    else:
        content += f'\nSEARCH_BASE_PATH = r"{resolved}"\n'
    env_file.write_text(content, encoding='utf-8')

    # Update in-memory globals
    SEARCH_BASE_PATHS.append(resolved)
    if len(SEARCH_BASE_PATHS) == 1:
        SEARCH_BASE_PATH = resolved
    return True


@app.route('/api/tools/search-paths', methods=['GET', 'POST', 'DELETE'])
def tools_search_paths():
    """Manage SEARCH_BASE_PATH entries in env.py.
    GET: list current paths. POST: add a path. DELETE: remove a path.
    """
    global SEARCH_BASE_PATHS, SEARCH_BASE_PATH
    if request.method == 'GET':
        return jsonify({
            "paths": [{"path": str(p), "exists": _safe_exists(p)} for p in SEARCH_BASE_PATHS]
        })

    if request.method == 'POST':
        data = request.get_json(force=True) or {}
        new_path = _clean_path(data.get('path'))
        if not new_path:
            return jsonify({"error": "path is required"}), 400
        if not Path(new_path).is_dir():
            return jsonify({"error": f"Directory does not exist: {new_path}"}), 400
        try:
            added = _append_search_path(new_path)
            if added:
                return jsonify({"ok": True, "added": str(Path(new_path).resolve()),
                                "paths": [str(p) for p in SEARCH_BASE_PATHS]})
            else:
                return jsonify({"ok": True, "already_exists": True,
                                "paths": [str(p) for p in SEARCH_BASE_PATHS]})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    if request.method == 'DELETE':
        import re as _re
        data = request.get_json(force=True) or {}
        rm_path = _clean_path(data.get('path'))
        if not rm_path:
            return jsonify({"error": "path is required"}), 400
        resolved = Path(rm_path).resolve()
        if resolved not in SEARCH_BASE_PATHS:
            return jsonify({"error": "Path not in SEARCH_BASE_PATHS"}), 404
        if len(SEARCH_BASE_PATHS) <= 1:
            return jsonify({"error": "Cannot remove the last search path"}), 400

        SEARCH_BASE_PATHS = [p for p in SEARCH_BASE_PATHS if p != resolved]
        SEARCH_BASE_PATH = SEARCH_BASE_PATHS[0]

        # Update env.py
        env_file = Path(__file__).parent / 'env.py'
        content = env_file.read_text(encoding='utf-8')
        new_val = ';'.join(str(p) for p in SEARCH_BASE_PATHS)
        pattern = _re.compile(r'^(SEARCH_BASE_PATH\s*=\s*r?["\'])(.+?)(["\'])\s*$', _re.MULTILINE)
        match = pattern.search(content)
        if match:
            prefix, _, quote = match.group(1), match.group(2), match.group(3)
            new_line = f'{prefix}{new_val}{quote}'
            content = content[:match.start()] + new_line + content[match.end():]
            env_file.write_text(content, encoding='utf-8')
        return jsonify({"ok": True, "removed": str(resolved),
                        "paths": [str(p) for p in SEARCH_BASE_PATHS]})


@app.route('/api/tools/reindex', methods=['POST'])
def tools_reindex():
    """Trigger a full ES reindex of all SEARCH_BASE_PATHS in background."""
    data = request.get_json(force=True) or {}
    reset = data.get('reset', True)
    no_vectors = data.get('no_vectors', False)
    ext_filter = data.get('ext_filter', '').strip()

    # Index each base path sequentially via a shell script
    script_dir = Path(__file__).parent
    indexer = str(script_dir / 'search_indexer.py')
    # First path gets --reset (if requested), subsequent paths are incremental
    cmds = []
    for i, bp in enumerate(SEARCH_BASE_PATHS):
        c = [sys.executable, indexer, '--path', str(bp)]
        if reset and i == 0:
            c.append('--reset')
        if no_vectors:
            c.append('--no-vectors')
        if ext_filter:
            c.extend(['--ext', ext_filter])
        cmds.append(' '.join(f'"{x}"' if ' ' in x else x for x in c))
    shell_cmd = ' && '.join(cmds)
    cmd = ['bash', '-c', shell_cmd] if os.name != 'nt' else ['cmd', '/c', ' & '.join(cmds)]

    task_id = f"reindex-{int(time.time())}"
    _run_tool_bg(task_id, cmd, cwd=str(script_dir), task_type='reindex')
    return jsonify({"task_id": task_id, "status": "started"})


@app.route('/api/tools/generate-catalog', methods=['POST'])
def tools_generate_catalog():
    """Generate course catalog HTML from a given path."""
    data = request.get_json(force=True) or {}
    courses_path = _clean_path(data.get('path'))
    if not courses_path:
        return jsonify({"error": "path is required"}), 400

    script_dir = Path(__file__).parent
    cmd = [sys.executable, str(script_dir / 'catalog_generator.py'),
           courses_path, '--output', str(script_dir)]

    task_id = f"catalog-{int(time.time())}"
    _run_tool_bg(task_id, cmd, cwd=str(script_dir))
    return jsonify({"task_id": task_id, "status": "started"})


@app.route('/api/tools/youtube-download', methods=['POST'])
def tools_youtube_download():
    """Download YouTube subtitles/video."""
    _loopback_only()                      # starts a downloader that writes files → this machine only
    data = request.get_json(force=True) or {}
    urls = data.get('urls', [])
    download_video = data.get('video', False)
    output_dir = data.get('output', '').strip()

    if not urls:
        return jsonify({"error": "urls list is required"}), 400
    urls = [str(u).strip() for u in urls if str(u).strip()]
    if not urls or any(not re.match(r'^https?://', u, re.I) for u in urls):
        return jsonify({"error": "Every entry must be a full http(s):// URL."}), 400
    if output_dir.startswith('-'):
        return jsonify({"error": "The output directory must be a path."}), 400

    script_dir = Path(__file__).parent.parent / '_youtube_downloader'
    cmd = [sys.executable, str(script_dir / 'downloader.py')]
    cmd.extend(urls)
    if download_video:
        cmd.append('--video')
    else:
        cmd.append('--no-video')
    if output_dir:
        cmd.extend(['--output', output_dir])

    task_id = f"youtube-{int(time.time())}"
    _run_tool_bg(task_id, cmd, cwd=str(script_dir))
    return jsonify({"task_id": task_id, "status": "started"})


@app.route('/api/tools/ollama-pull', methods=['POST'])
def tools_ollama_pull():
    """Pull/install an Ollama model."""
    data = request.get_json(force=True) or {}
    model_name = data.get('model', '').strip()
    if not model_name:
        return jsonify({"error": "model name is required"}), 400

    cmd = ['ollama', 'pull', model_name]
    task_id = f"ollama-pull-{int(time.time())}"
    _run_tool_bg(task_id, cmd)
    return jsonify({"task_id": task_id, "status": "started"})


@app.route('/api/tools/pdf-collector', methods=['POST'])
def tools_pdf_collector():
    """Run pdf_collector on a source folder."""
    _loopback_only()                      # copies / merges files → this machine only
    data = request.get_json(force=True) or {}
    source = data.get('source', '').strip()
    output = data.get('output', '').strip()
    join_pdfs = data.get('join_pdfs', data.get('join', False))     # the UI sends "join"

    if not source:
        return jsonify({"error": "source folder is required"}), 400
    if source.startswith('-') or output.startswith('-'):
        return jsonify({"error": "Folders must be paths (they cannot start with \"-\")."}), 400
    if not Path(source).expanduser().is_dir():
        return jsonify({"error": f"Source folder not found: {source}"}), 400

    script_dir = Path(__file__).parent.parent / '_pdf_collector'
    cmd = [sys.executable, str(script_dir / 'collect.py'), source]
    if output:
        cmd.append(output)
    if join_pdfs:
        cmd.append('--join-pdfs-in-same-folder')

    task_id = f"pdf-collector-{int(time.time())}"
    _run_tool_bg(task_id, cmd, cwd=str(script_dir))
    return jsonify({"task_id": task_id, "status": "started"})


@app.route('/api/tools/run-script', methods=['POST'])
def tools_run_script():
    """Run a markdown tool script (mdsplit, mdtranslator, mdImageDownloader, mdformater, mdfromhtml)."""
    _loopback_only()                      # runs scripts that read / write files → this machine only
    data = request.get_json(force=True) or {}
    tool = data.get('tool', '').strip()
    args_list = data.get('args')

    # Map tool names to script paths
    project_root = Path(__file__).parent.parent
    tool_map = {
        'mdsplit':           {'dir': '_mdsplit',           'cmd': [sys.executable, 'mdsplit.py']},
        'mdtranslator':      {'dir': '_mdtranslator',     'cmd': ['node', 'translate-prepare.mjs']},
        'mdImageDownloader': {'dir': '_mdImageDownloader', 'cmd': ['node', 'download-images.mjs']},
        'mdformater':        {'dir': '_mdformater',       'cmd': ['node', 'format-docs.mjs']},
        'mdfromhtml':        {'dir': '_mdfromhtml',        'cmd': ['node', 'html-to-md.mjs']},
        'mdfromhtml-merge':  {'dir': '_mdfromhtml',        'cmd': ['node', 'merge-book.mjs']},
        'mdfromhtml-collection': {'dir': '_mdfromhtml',    'cmd': ['node', 'build-collection.mjs']},
    }

    if tool not in tool_map:
        return jsonify({"error": f"Unknown tool: {tool}. Available: {list(tool_map.keys())}"}), 400

    info = tool_map[tool]
    script_dir = project_root / info['dir']
    if not (script_dir / info['cmd'][1]).is_file():
        return jsonify({"error": f"{info['cmd'][1]} not found in {info['dir']}/ — is the tool folder present?"}), 404
    if info['cmd'][0] == 'node' and not shutil.which('node'):
        return jsonify({"error": "Node.js 18+ is required for this tool (https://nodejs.org). Re-run start.sh / start.bat to install it."}), 400
    if args_list is None:                  # the UI sends named options; translate them into CLI arguments
        try:
            args_list = _tool_cli_args(tool, data)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    cmd = info['cmd'] + [str(a) for a in args_list]

    task_id = f"{tool}-{int(time.time())}"
    _run_tool_bg(task_id, cmd, cwd=str(script_dir))
    return jsonify({"task_id": task_id, "status": "started"})


def _tool_cli_args(tool: str, o: dict) -> list:
    """Turn the options the Tools UI sends (file, max_level, keep_html, …) into the command-line
    arguments of each script. Raises ValueError with a user-facing message for bad input.
    Before this existed the endpoint only read an `args` list, so every UI button ran its script with
    no arguments at all."""
    def s(key):
        return str(o.get(key) or '').strip()

    def opt(key, flag):
        return [flag, s(key)] if s(key) else []

    def on(key, flag):
        return [flag] if o.get(key) else []

    def need(key, label, must_exist=True):
        v = s(key)
        if not v:
            raise ValueError(f'{label} is required.')
        if v.startswith('-'):
            raise ValueError(f'{label} must be a path (it cannot start with "-").')
        if must_exist and not Path(v).expanduser().exists():
            raise ValueError(f'{label} not found: {v}')
        return v

    for key in ('output', 'out', 'output_dir', 'images_dest'):      # values that become arguments must not look like flags
        if s(key).startswith('-'):
            raise ValueError(f'"{key}" must be a path (it cannot start with "-").')
    if tool == 'mdsplit':
        lvl = s('max_level')
        if lvl and not (lvl.isdigit() and 1 <= int(lvl) <= 6):
            raise ValueError('Max heading level must be a number from 1 to 6.')
        return [need('file', 'Input file')] + opt('max_level', '--max-level') + opt('output', '--output') \
               + on('table_of_contents', '--toc') + on('force', '--force')
    if tool == 'mdImageDownloader':
        return [need('file', 'Document path')] + opt('out', '--out') + on('keep_html', '--keep-html') + on('dry_run', '--dry-run')
    if tool == 'mdformater':
        return [need('file', 'Document path')] + on('auto', '--auto') + on('images_only', '--images-only')
    if tool == 'mdtranslator':
        return [need('file', 'Document path')]
    if tool == 'mdfromhtml':
        return [need('input', 'Input HTML file')] + ([s('output')] if s('output') else [])
    if tool == 'mdfromhtml-collection':
        return [need('input_dir', 'Input directory')] + ([s('output_dir')] if s('output_dir') else [])
    if tool == 'mdfromhtml-merge':
        depth = s('toc_depth')
        if depth and not (depth.isdigit() and 0 <= int(depth) <= 6):
            raise ValueError('TOC depth must be a number from 0 to 6.')
        # merge-book excludes the "view original" reference lines by default; the box "Skip original
        # reference links" unticked means "keep them"
        return [need('input_dir', 'Input directory')] + opt('output', '--output') + opt('toc_depth', '--toc-depth') \
               + ([] if o.get('no_refs') else ['--include-original-refs']) \
               + on('no_convert_emphasis', '--no-convert-emphasis') + on('no_format_tables', '--no-format-tables') \
               + opt('images_dest', '--images-dest')
    return []


@app.route('/api/tools/index-directory', methods=['POST'])
def tools_index_directory():
    """Index a new directory into Elasticsearch (incremental or full reset)."""
    data = request.get_json(force=True) or {}
    path = _clean_path(data.get('path'))
    if not path:
        return jsonify({"error": "path is required"}), 400
    if not Path(path).is_dir():
        return jsonify({"error": f"Directory does not exist: {path}"}), 400

    task_id, added = _start_index_task(
        path,
        reset=bool(data.get('reset', False)),
        no_vectors=bool(data.get('no_vectors', False)),
        batch=data.get('batch', 50),
        canonical_lang=str(data.get('canonical_lang', '')).strip(),
        ext_filter=str(data.get('ext_filter', '')).strip(),
    )
    return jsonify({"task_id": task_id, "status": "started",
                    "path_added_to_env": added})


def _start_index_task(path: str, reset=False, no_vectors=False, batch=50,
                      canonical_lang='', ext_filter=''):
    """Launch search_indexer.py for `path`, register the folder and keep its registry entry
    up to date (last scan time / status / file count). Returns (task_id, added_to_env)."""
    script_dir = Path(__file__).parent
    path = str(Path(path).resolve())          # same form the indexer stores in the index
    cmd = [sys.executable, str(script_dir / 'search_indexer.py'), '--path', path]
    if reset:
        cmd.append('--reset')
    if no_vectors:
        cmd.append('--no-vectors')
    if batch and int(batch) != 50:
        cmd.extend(['--batch', str(int(batch))])
    if canonical_lang:
        cmd.extend(['--canonical-lang', canonical_lang])
    if ext_filter:
        cmd.extend(['--ext', ext_filter])

    # Auto-append path to SEARCH_BASE_PATH in env.py
    try:
        added = _append_search_path(path)
    except Exception as e:
        log.warning(f"Could not update env.py with new path: {e}")
        added = False

    task_id = f"index-{int(time.time())}"
    reg = _scan_registry()
    entry = reg.upsert(path, ext_filter=ext_filter, no_vectors=no_vectors,
                       batch=int(batch or 50), canonical_lang=canonical_lang)
    reg.update(entry['id'], last_task=task_id, last_status='running')

    def _finished(status, snap):
        reg.update(entry['id'], last_status=status, last_scan_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                   last_files=int(snap.get('progress_total') or 0))

    _run_tool_bg(task_id, cmd, cwd=str(script_dir), task_type='index', on_done=_finished)
    return task_id, added


# ─── Scanned-folder registry (persistent list in the Tools UI) ────────────────

_SCAN_REG = None

def _scan_registry():
    """Singleton registry stored in <project>/data/scan_registry.json."""
    global _SCAN_REG
    if _SCAN_REG is None:
        import importlib
        _d = str(Path(__file__).parent)
        if _d not in sys.path:
            sys.path.insert(0, _d)
        _SCAN_REG = importlib.import_module('scan_registry').Registry(_PROJECT_ROOT / 'data' / 'scan_registry.json')
    return _SCAN_REG


def _under(path: str, folder: str) -> bool:
    """True if `path` is `folder` or inside it (boundary-aware: 'C:\\Kurse2' is NOT under 'C:\\Kurse')."""
    a, b = os.path.normcase(str(path)).rstrip('\\/'), os.path.normcase(str(folder)).rstrip('\\/')
    return a == b or a.startswith(b + '\\') or a.startswith(b + '/')


def _prefix_query(folder: str) -> dict:
    """ES filter matching every doc whose `path` is inside `folder` (exact or folder + separator)."""
    f = str(folder).rstrip('\\/')
    sep = '\\' if '\\' in f else '/'
    return {"bool": {"should": [{"term": {"path": f}}, {"prefix": {"path": f + sep}}],
                     "minimum_should_match": 1}}


@app.route('/api/tools/scan-paths', methods=['GET', 'POST'])
def scan_paths():
    """GET: saved folders with live status (exists on disk, indexed chunks, running scan).
    POST: save a new folder (does not scan it)."""
    reg = _scan_registry()
    if request.method == 'POST':
        data = request.get_json(force=True) or {}
        path = _clean_path(data.get('path'))
        if not path:
            return jsonify({"error": "path is required"}), 400
        if not Path(path).is_dir():
            return jsonify({"error": f"Directory does not exist: {path}"}), 400
        resolved = str(Path(path).resolve())
        try:
            _append_search_path(resolved)       # allow the viewer to serve its files
        except Exception as e:
            log.warning(f"Could not add {resolved} to SEARCH_BASE_PATH: {e}")
        entry = reg.upsert(resolved, ext_filter=str(data.get('ext_filter', '')).strip(),
                           no_vectors=bool(data.get('no_vectors', False)),
                           batch=int(data.get('batch', 50) or 50),
                           canonical_lang=str(data.get('canonical_lang', '')).strip())
        return jsonify({"ok": True, "entry": entry})

    entries = reg.list(seed=[str(p) for p in SEARCH_BASE_PATHS])
    es = _get_es()
    out = []
    for e in entries:
        p = Path(e['path'])
        try:
            exists = p.is_dir()
        except OSError:
            exists = False
        docs = None
        if es is not None:
            try:
                docs = es.count(index=ES_INDEX, body={"query": _prefix_query(e['path'])})['count']
            except Exception:
                docs = None
        with _tool_lock:
            t = _tool_processes.get(e.get('last_task') or '')
            running = e['last_task'] if t and t.get('status') in ('starting', 'running') else None
        out.append(dict(e, exists=exists, docs=docs, running=running,
                        allowed=any(_under(e['path'], str(b)) for b in SEARCH_BASE_PATHS)))
    return jsonify({"folders": out, "es": es is not None})


@app.route('/api/tools/scan-paths/<entry_id>/refresh', methods=['POST'])
def scan_paths_refresh(entry_id):
    """Incremental re-scan of a saved folder with the options it was scanned with."""
    e = _scan_registry().get(entry_id)
    if not e:
        return jsonify({"error": "Unknown folder"}), 404
    if not Path(e['path']).is_dir():
        return jsonify({"error": f"Directory not found on disk: {e['path']} — edit the path if it was moved."}), 400
    with _tool_lock:
        t = _tool_processes.get(e.get('last_task') or '')
        if t and t.get('status') in ('starting', 'running'):
            return jsonify({"error": "A scan of this folder is already running.", "task_id": e['last_task']}), 409
    task_id, _ = _start_index_task(e['path'], reset=False, no_vectors=e['no_vectors'], batch=e['batch'],
                                   canonical_lang=e['canonical_lang'], ext_filter=e['ext_filter'])
    return jsonify({"task_id": task_id, "status": "started"})


_reloc_tasks: dict = {}


def _replace_search_path(old: str, new: str) -> None:
    """Swap `old` for `new` in SEARCH_BASE_PATH (env.py + memory); append `new` if `old` is absent."""
    global SEARCH_BASE_PATHS, SEARCH_BASE_PATH
    new_resolved = Path(new).resolve()
    swapped = False
    paths = []
    for p in SEARCH_BASE_PATHS:
        if os.path.normcase(str(p)) == os.path.normcase(str(Path(old).resolve())):
            if not swapped:
                paths.append(new_resolved)
            swapped = True
        elif p != new_resolved:
            paths.append(p)
    if not swapped and new_resolved not in paths:
        paths.append(new_resolved)
    SEARCH_BASE_PATHS = paths
    SEARCH_BASE_PATH = paths[0]
    # Edit env.py ENTRY BY ENTRY and leave every other entry exactly as written (the in-memory
    # list may hold converted/resolved forms, e.g. WSL paths, that must not leak into the file).
    env_file = Path(__file__).parent / 'env.py'
    content = env_file.read_text(encoding='utf-8')
    pattern = re.compile(r'^(SEARCH_BASE_PATH\s*=\s*r?["\'])(.+?)(["\'])\s*$', re.MULTILINE)
    m = pattern.search(content)
    if m:
        old_key = os.path.normcase(str(old).strip().rstrip('\\/'))
        entries, replaced = [], False
        for ent in m.group(2).split(';'):
            if os.path.normcase(ent.strip().rstrip('\\/')) == old_key and not replaced:
                entries.append(str(new_resolved)); replaced = True
            elif ent.strip():
                entries.append(ent.strip())
        if not replaced and str(new_resolved) not in entries:
            entries.append(str(new_resolved))
        content = content[:m.start()] + f"{m.group(1)}{';'.join(entries)}{m.group(3)}" + content[m.end():]
    else:
        content += f'\nSEARCH_BASE_PATH = r"{new_resolved}"\n'
    env_file.write_text(content, encoding='utf-8')


def _relocate_paths(old_path: str, new_raw: str, dry_run: bool = False, entry_id: str = None):
    """Point every indexed chunk under `old_path` at `new_path` (a folder that was moved, or the
    same files on another machine). Shared by the saved-folder ✎ button and the post-import remap.
    Returns (payload, http_status). The real update runs in a background thread
    (poll /api/tools/scan-paths/relocation/<task_id>)."""
    reg, es = _scan_registry(), _get_es()
    new_raw = str(new_raw or '').strip()
    if not new_raw:
        return {"error": "path is required"}, 400
    if not Path(new_raw).is_dir():
        return {"error": f"New directory does not exist: {new_raw}"}, 400
    new_path = str(Path(new_raw).resolve())
    if os.path.normcase(new_path) == os.path.normcase(str(old_path)):
        return {"ok": True, "unchanged": True}, 200
    other = reg.find(new_path)
    # Moving a LIST ROW onto a folder that already has its own row would create a duplicate; remapping
    # an imported (foreign) folder onto an already-registered local folder is fine and common.
    if entry_id and other and other['id'] != entry_id:
        return {"error": "That folder is already in the list."}, 409
    if es is None:
        return {"error": "Elasticsearch is not reachable."}, 503
    try:
        count = es.count(index=ES_INDEX, body={"query": _prefix_query(old_path)})['count']
    except Exception as ex:
        return {"error": f"Elasticsearch error: {ex}"}, 500
    if dry_run:
        return {"ok": True, "dry_run": True, "chunks": count, "old": str(old_path), "new": new_path}, 200

    task_id = f"reloc-{int(time.time() * 1000)}"
    _reloc_tasks[task_id] = {'status': 'running', 'chunks': count, 'updated': 0, 'error': '',
                             'old': str(old_path), 'new': new_path, 'started': time.time()}

    def _worker():
        t = _reloc_tasks[task_id]
        try:
            old_n = str(old_path).rstrip('\\/')
            new_n = new_path.rstrip('\\/')
            sep_old = '\\' if '\\' in old_n else '/'
            sep_new = '\\' if '\\' in new_n else '/'
            body = {
                "query": _prefix_query(old_n),
                "script": {"lang": "painless", "source": """
                    String p = ctx._source.path;
                    if (p == null) { ctx.op = 'noop'; }
                    else if (p.equals(params.old)) { ctx._source.path = params.nw; }
                    else if (p.startsWith(params.old + params.so)) {
                        ctx._source.path = params.nw + p.substring(params.old.length()).replace(params.so, params.sn);
                    } else { ctx.op = 'noop'; }""",
                    "params": {"old": old_n, "nw": new_n, "so": sep_old, "sn": sep_new}},
            }
            resp = es.options(request_timeout=7200).update_by_query(
                index=ES_INDEX, body=body, conflicts='proceed', refresh=True)
            t['updated'] = resp.get('updated', 0)
            if resp.get('failures'):
                raise RuntimeError(f"{len(resp['failures'])} documents failed to update")
            # Only after the index is consistent: switch env.py / viewer allow-list / registry
            _replace_search_path(str(old_path), new_path)
            ent = reg.get(entry_id) if entry_id else reg.find(str(old_path))
            if ent:
                reg.update(ent['id'], path=new_path)
            else:
                reg.upsert(new_path)
            t['status'] = 'done'
        except Exception as ex:
            t['status'], t['error'] = 'error', str(ex)[:400]
            log.error(f"Folder relocation failed: {ex}")

    threading.Thread(target=_worker, daemon=True, name=task_id).start()
    return {"ok": True, "task_id": task_id, "chunks": count}, 200


@app.route('/api/tools/scan-paths/<entry_id>', methods=['PUT', 'DELETE'])
def scan_paths_item(entry_id):
    """PUT {path, dry_run}: move a folder — rewrites `path` of every indexed chunk under it and
    updates env.py + the registry. DELETE [?purge=1]: forget the folder (optionally delete its chunks)."""
    reg = _scan_registry()
    e = reg.get(entry_id)
    if not e:
        return jsonify({"error": "Unknown folder"}), 404
    es = _get_es()

    if request.method == 'DELETE':
        deleted = 0
        if request.args.get('purge') in ('1', 'true') and es is not None:
            try:
                resp = es.options(request_timeout=3600).delete_by_query(
                    index=ES_INDEX, body={"query": _prefix_query(e['path'])},
                    conflicts='proceed', refresh=True)
                deleted = resp.get('deleted', 0)
            except Exception as ex:
                return jsonify({"error": f"Could not delete indexed data: {ex}"}), 500
        reg.remove(entry_id)
        return jsonify({"ok": True, "removed": e['path'], "deleted_chunks": deleted})

    data = request.get_json(force=True) or {}
    payload, status = _relocate_paths(e['path'], _clean_path(data.get('path')), bool(data.get('dry_run')), entry_id)
    if payload.get('unchanged'):
        payload['entry'] = e
    return jsonify(payload), status


@app.route('/api/tools/scan-paths/relocation/<task_id>')
def scan_paths_relocation(task_id):
    t = _reloc_tasks.get(task_id)
    if not t:
        return jsonify({"error": "Unknown task"}), 404
    return jsonify(dict(t, elapsed=int(time.time() - t['started'])))


# ─── Export / import the Elasticsearch volume (Tools > Export / Import index) ──
# Packs the Docker volume holding the index into ONE .tgz (and unpacks it on another machine),
# with step-by-step progress for the UI. See backend/es_migrate.py. Only reachable from localhost:
# the server listens on 0.0.0.0, and replacing the index must not be possible from the network.

_MIGRATE_CFG_OVERRIDE = None      # tests point this at throw-away containers


def _migrate_mod():
    import importlib
    _d = str(Path(__file__).parent)
    if _d not in sys.path:
        sys.path.insert(0, _d)
    return importlib.import_module('es_migrate')


def _opt_env(name: str, default: str = '') -> str:
    return str(getattr(globals().get('env'), name, os.environ.get(name, default)))


def _migrate_cfg() -> dict:
    if _MIGRATE_CFG_OVERRIDE:
        return dict(_MIGRATE_CFG_OVERRIDE)
    return {'container': _opt_env('ES_CONTAINER', _ES_CONTAINER),
            'volume': _opt_env('ES_VOLUME', ''),                 # '' → detected from the container
            'project': _opt_env('ES_COMPOSE_PROJECT', 'deploy'),
            'service': 'elasticsearch',
            'compose_file': _opt_env('ES_COMPOSE_FILE', str(_PROJECT_ROOT / 'deploy' / 'docker-compose.yml'))}


def _default_exports_dir() -> str:
    return _opt_env('EXPORT_DIR', str(_PROJECT_ROOT / 'toexport'))


def _loopback_only():
    if (request.remote_addr or '') not in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
        abort(403, 'Export / import is only allowed from this machine (localhost).')


def _clean_path(value) -> str:
    """User-typed folder path: trim spaces and surrounding quotes (e.g. pasted from a shell or Finder)."""
    v = str(value or '').strip()
    while len(v) >= 2 and v[0] == v[-1] and v[0] in '\'"':
        v = v[1:-1].strip()
    return v


def list_subdirs(path: str = '') -> dict:
    """Folder-picker data: the sub-folders of `path` (home when empty). Dirs only, hidden ones skipped."""
    path = _clean_path(path)
    p = Path(os.path.expanduser(path)) if path else Path.home()
    p = p.resolve()
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    dirs = []
    try:
        for e in sorted(os.scandir(p), key=lambda x: x.name.lower()):
            if e.name.startswith('.'):
                continue
            try:
                if e.is_dir():
                    dirs.append({'name': e.name, 'path': str(Path(e.path))})
            except OSError:
                continue
    except PermissionError:
        pass
    roots = [{'name': 'Home', 'path': str(Path.home())}]
    if sys.platform == 'win32':
        import string
        roots += [{'name': f'{d}:\\', 'path': f'{d}:\\'} for d in string.ascii_uppercase
                  if os.path.exists(f'{d}:\\')]
    else:
        roots.append({'name': '/', 'path': '/'})
        if os.path.isdir('/Volumes'):
            roots.append({'name': 'Volumes', 'path': '/Volumes'})
    parent = str(p.parent) if p.parent != p else ''
    return {'path': str(p), 'parent': parent, 'dirs': dirs, 'roots': roots}


@app.route('/api/fs/list')
def api_fs_list():
    """Browse server-side folders for the "Directory path" picker (this machine only)."""
    _loopback_only()
    try:
        return jsonify(list_subdirs(request.args.get('path', '')))
    except (NotADirectoryError, FileNotFoundError):
        return jsonify({'error': 'Not a folder: ' + request.args.get('path', '')}), 404
    except OSError as e:
        return jsonify({'error': str(e)}), 400


def _migrate_busy() -> str:
    """Why a new export/import can't start right now ('' = free)."""
    if _migrate_mod().running_job():
        return 'Another export / import is already running.'
    with _tool_lock:
        if any(t.get('status') in ('starting', 'running') and t.get('type') in ('index', 'reindex')
               for t in _tool_processes.values()):
            return 'An indexing task is running — wait for it to finish (Elasticsearch gets stopped during export/import).'
    return ''


def _scanned_folder_hints() -> list:
    """Folders known to have been scanned (registry + SEARCH_BASE_PATH) — the precise 'roots'."""
    seen, out = set(), []
    for p in [e['path'] for e in _scan_registry().list()] + [str(b) for b in SEARCH_BASE_PATHS]:
        if os.path.normcase(p) not in seen:
            seen.add(os.path.normcase(p))
            out.append(p)
    return out


@app.route('/api/migrate/info')
def migrate_info():
    _loopback_only()
    import platform as _pf
    mg, cfg = _migrate_mod(), _migrate_cfg()
    ok, msg = mg.docker_ok()
    info = {'docker': {'ok': ok, 'message': msg}, 'exports_dir': _default_exports_dir(),
            'cfg': {k: cfg.get(k) for k in ('container', 'volume', 'project', 'compose_file')},
            'platform': f"{_pf.system()} {_pf.machine()}", 'busy': _migrate_busy()}
    if ok:
        info['compose'] = bool(mg.compose_command())
        tgt = mg.resolve_target(cfg)
        info['target'] = dict(tgt, volume_exists=mg.volume_exists(tgt['volume']))
    return jsonify(info)


@app.route('/api/migrate/export', methods=['POST'])
def migrate_export():
    _loopback_only()
    busy = _migrate_busy()
    if busy:
        return jsonify({'error': busy}), 409
    data = request.get_json(silent=True) or {}
    out_dir = str(data.get('dir') or _default_exports_dir()).strip()
    if not Path(out_dir).expanduser().is_absolute():
        return jsonify({'error': 'The export folder must be an absolute path.'}), 400
    mg = _migrate_mod()
    job = mg.Job('export', mg.EXPORT_STEPS)
    extra = {'embedding_model': _EMBEDDING_MODEL, 'app': 'course-catalog'}
    threading.Thread(target=mg.export_index, name=job.id, daemon=True,
                     args=(job, _migrate_cfg(), out_dir, ES_URL or 'http://127.0.0.1:9200', ES_INDEX, extra,
                           _scanned_folder_hints())).start()
    return jsonify({'job_id': job.id})


@app.route('/api/migrate/import', methods=['POST'])
def migrate_import():
    _loopback_only()
    busy = _migrate_busy()
    if busy:
        return jsonify({'error': busy}), 409
    data = request.get_json(silent=True) or {}
    archive = str(data.get('archive', '')).strip().strip('"')
    if not archive:
        return jsonify({'error': 'Give the path of the .tgz file to import.'}), 400
    mg = _migrate_mod()
    job = mg.Job('import', mg.IMPORT_STEPS)

    def _run():
        global _es_client, _es_last_failed
        try:
            mg.import_index(job, _migrate_cfg(), archive, ES_URL or 'http://127.0.0.1:9200', ES_INDEX,
                            _default_exports_dir(), bool(data.get('backup_current', True)))
        finally:
            _es_client, _es_last_failed = None, 0.0        # the app must reconnect to the new data

    threading.Thread(target=_run, name=job.id, daemon=True).start()
    return jsonify({'job_id': job.id})


@app.route('/api/migrate/jobs/<job_id>')
def migrate_job(job_id):
    _loopback_only()
    job = _migrate_mod().get_job(job_id)
    if not job:
        return jsonify({'error': 'Unknown job (the backend was restarted?)'}), 404
    return jsonify(job.to_dict())


@app.route('/api/migrate/exports')
def migrate_exports():
    _loopback_only()
    d = request.args.get('dir') or _default_exports_dir()
    return jsonify({'dir': d, 'exports': _migrate_mod().list_exports(d)})


@app.route('/api/migrate/folders', methods=['POST'])
def migrate_folders():
    """The folders the indexed files came from (their OLD paths) and whether each exists here.
    `hints` = scanned folders recorded in the export manifest (precise); else derived per drive/share."""
    _loopback_only()
    hints = (request.get_json(silent=True) or {}).get('hints') or None
    try:
        roots = _migrate_mod().folder_roots(ES_URL or 'http://127.0.0.1:9200', ES_INDEX, hints)
    except Exception as ex:
        return jsonify({'error': f'Could not read the index: {ex}'}), 500
    return jsonify({'folders': roots})


def embedding_model_report(model: str, vector_dims=None) -> dict:
    """Compare the embedding model recorded in an import manifest with the one this backend uses."""
    model = (model or '').strip()
    cfg = _MODEL_CONFIG.get(model)
    rep = {'model': model, 'active': _EMBEDDING_MODEL, 'matches': model == _EMBEDDING_MODEL,
           'supported': cfg is not None, 'dims': (cfg or {}).get('dims'), 'vector_dims': vector_dims,
           'dims_mismatch': bool(cfg and vector_dims and int(vector_dims) != cfg['dims'])}
    rep.update({k: v for k, v in _models_info().hf_cache_info(model).items() if k in ('cached', 'size_h')} if model else {})
    return rep


_model_apply = {'state': 'idle', 'model': '', 'error': ''}
_model_apply_lock = threading.Lock()


def _download_embedder(model: str) -> None:
    """Load the model once so the library downloads it into the Hugging Face cache."""
    if model in _ST_ONLY_MODELS:
        from sentence_transformers import SentenceTransformer
        SentenceTransformer(model)
    else:
        from fastembed import TextEmbedding
        TextEmbedding(model)


def _set_embedding_model(new_model: str) -> None:
    """Make `new_model` the active embedding model (env.py + memory). A reindex is NOT needed for imported data."""
    import re as _re
    global _EMBEDDING_MODEL, _NORMALIZE_EMBEDDING_CASE, _MODEL_IS_MULTILINGUAL, _es_embed_model
    env_file = Path(__file__).parent / 'env.py'
    content = env_file.read_text(encoding='utf-8')
    line = f'EMBEDDING_MODEL = "{new_model}"'
    pat = r'^EMBEDDING_MODEL\s*=\s*["\'].*?["\']'
    content = _re.sub(pat, line, content, count=1, flags=_re.MULTILINE) if _re.search(pat, content, flags=_re.MULTILINE) \
        else content + f'\n{line}\n'
    env_file.write_text(content, encoding='utf-8')
    cfg = _MODEL_CONFIG[new_model]
    _EMBEDDING_MODEL, _NORMALIZE_EMBEDDING_CASE = new_model, cfg['normalize_case']
    _MODEL_IS_MULTILINGUAL = cfg.get('multilingual', False)
    _es_embed_model = None  # reload lazily with the new model


@app.route('/api/migrate/model-check')
def migrate_model_check():
    _loopback_only()
    return jsonify(embedding_model_report(request.args.get('model', ''), request.args.get('dims')))


@app.route('/api/migrate/model-apply', methods=['POST'])
def migrate_model_apply():
    """Download (if needed) and activate the embedding model an imported index was built with."""
    _loopback_only()
    model = str((request.get_json(silent=True) or {}).get('model', '')).strip()
    if model not in _MODEL_CONFIG:
        return jsonify({'error': f'Unsupported model: {model or "(none)"}'}), 400
    with _model_apply_lock:
        if _model_apply['state'] == 'running':
            return jsonify({'error': 'A model is already being prepared.'}), 409
        _model_apply.update(state='running', model=model, error='')

    def _run():
        try:
            _download_embedder(model)  # no-op when fully cached; completes partial caches
            _set_embedding_model(model)
            _model_apply['state'] = 'done'
        except Exception as e:  # noqa: BLE001 — surfaced to the UI
            _model_apply.update(state='failed', error=f'{type(e).__name__}: {e}')
    threading.Thread(target=_run, name='model-apply', daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/migrate/model-status')
def migrate_model_status():
    _loopback_only()
    return jsonify(dict(_model_apply))


@app.route('/api/migrate/remap', methods=['POST'])
def migrate_remap():
    """Point the chunks of an old folder (from the other machine) at its location on THIS machine."""
    _loopback_only()
    data = request.get_json(silent=True) or {}
    old = str(data.get('old', '')).strip()
    if not old:
        return jsonify({'error': 'old path is required'}), 400
    payload, status = _relocate_paths(old, data.get('new', ''), bool(data.get('dry_run')), None)
    return jsonify(payload), status


@app.route('/api/tools/paths', methods=['GET'])
def tools_get_paths():
    """Return unique base directories (parent paths) from ES index."""
    try:
        from elasticsearch import Elasticsearch
        es = Elasticsearch(ES_URL)
        result = set()
        after_key = None
        while True:
            composite = {"size": 1000, "sources": [{"path": {"terms": {"field": "path"}}}]}
            if after_key:
                composite["after"] = after_key
            resp = es.search(index=ES_INDEX, body={"size": 0, "aggs": {"by_path": {"composite": composite}}})
            buckets = resp["aggregations"]["by_path"]["buckets"]
            for b in buckets:
                p = b["key"]["path"]
                if p:
                    result.add(p)
            if len(buckets) < 1000:
                break
            after_key = resp["aggregations"]["by_path"]["after_key"]

        # Group by directory — find unique root directories
        dirs = set()
        for p in result:
            dirs.add(str(Path(p).parent))
        # Find the shortest common prefixes (base directories)
        base_dirs = {}
        for d in sorted(dirs):
            # Check if this dir is already covered by a parent
            covered = False
            for bd in list(base_dirs.keys()):
                if d.startswith(bd + os.sep) or d == bd:
                    base_dirs[bd] += 1
                    covered = True
                    break
            if not covered:
                base_dirs[d] = 1

        paths_list = sorted(result)
        return jsonify({
            "total_files": len(paths_list),
            "base_directories": [{"path": k, "files": v} for k, v in sorted(base_dirs.items())],
            "paths": paths_list,
        })
    except Exception as e:
        msg = str(e)
        if 'ConnectionRefused' in msg or 'ConnectionError' in msg or '9200' in msg:
            msg = 'Elasticsearch is not reachable (port 9200). Start ES and index first.'
        return jsonify({"error": msg}), 500


@app.route('/api/tools/verify-paths', methods=['POST'])
def tools_verify_paths():
    """Check which indexed file paths actually exist on disk."""
    data = request.get_json(force=True) or {}
    paths = data.get('paths', [])
    check_dir = data.get('directory', '').strip()

    # If no specific paths given, load all from ES
    if not paths:
        try:
            from elasticsearch import Elasticsearch
            es = Elasticsearch(ES_URL)
            paths = []
            after_key = None
            while True:
                composite = {"size": 1000, "sources": [{"path": {"terms": {"field": "path"}}}]}
                if after_key:
                    composite["after"] = after_key
                resp = es.search(index=ES_INDEX, body={"size": 0, "aggs": {"by_path": {"composite": composite}}})
                buckets = resp["aggregations"]["by_path"]["buckets"]
                for b in buckets:
                    p = b["key"]["path"]
                    if p:
                        paths.append(p)
                if len(buckets) < 1000:
                    break
                after_key = resp["aggregations"]["by_path"]["after_key"]
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # If checking a specific directory, only check paths under it
    if check_dir:
        paths = [p for p in paths if p.startswith(check_dir)]

    existing = []
    missing = []
    for p in paths:
        if Path(p).exists():
            existing.append(p)
        else:
            missing.append(p)

    return jsonify({
        "total": len(paths),
        "existing": len(existing),
        "missing_count": len(missing),
        "missing": missing[:500],  # limit to avoid huge response
        "healthy": len(missing) == 0,
    })


@app.route('/api/tools/search-files', methods=['POST'])
def tools_search_files():
    """Search for files in the ES index by path substring or filename pattern.
    Returns unique file paths with path-style detection (Windows vs POSIX).
    """
    data = request.get_json(force=True) or {}
    query = data.get('query', '').strip()
    limit = min(int(data.get('limit', 200)), 2000)

    try:
        from elasticsearch import Elasticsearch
        es = Elasticsearch(ES_URL)

        # Gather all unique paths via composite aggregation
        all_paths = []
        after_key = None
        while True:
            composite = {"size": 1000,
                         "sources": [{"path": {"terms": {"field": "path"}}}]}
            if after_key:
                composite["after"] = after_key
            resp = es.search(index=ES_INDEX, body={
                "size": 0,
                "aggs": {"by_path": {"composite": composite}}
            })
            buckets = resp["aggregations"]["by_path"]["buckets"]
            for b in buckets:
                p = b["key"]["path"]
                if p:
                    all_paths.append(p)
            if len(buckets) < 1000:
                break
            after_key = resp["aggregations"]["by_path"]["after_key"]

        # Filter by query — supports * and ? wildcards (fnmatch) or plain substring
        if query:
            import fnmatch as _fnmatch
            q_lower = query.lower()
            if '*' in q_lower or '?' in q_lower:
                # Match against filename (last segment) first, then full path
                all_paths = [p for p in all_paths
                             if _fnmatch.fnmatch(Path(p).name.lower(), q_lower)
                             or _fnmatch.fnmatch(p.lower(), q_lower)]
            else:
                all_paths = [p for p in all_paths if q_lower in p.lower()]

        # Detect path style per file
        def path_style(p):
            if '\\' in p and ':' in p[:3]:
                return 'windows'
            if p.startswith('/'):
                return 'posix'
            if '\\' in p:
                return 'windows'
            return 'posix'

        # Build response — detect per-file and overall dominant style
        files = []
        style_counts = {'windows': 0, 'posix': 0}
        for p in sorted(all_paths)[:limit]:
            s = path_style(p)
            style_counts[s] += 1
            files.append({"path": p, "style": s, "exists": Path(p).exists()})

        dominant = 'windows' if style_counts['windows'] >= style_counts['posix'] else 'posix'

        return jsonify({
            "total": len(all_paths),
            "returned": len(files),
            "dominant_style": dominant,
            "style_counts": style_counts,
            "files": files,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/tools/delete-file', methods=['POST'])
def tools_delete_file():
    """Delete all ES documents for a given file path."""
    data = request.get_json(force=True) or {}
    path = data.get('path', '').strip()
    if not path:
        return jsonify({"error": "path is required"}), 400

    try:
        from elasticsearch import Elasticsearch
        es = Elasticsearch(ES_URL)
        resp = es.delete_by_query(
            index=ES_INDEX,
            body={"query": {"term": {"path": path}}},
            refresh=True,
        )
        deleted = resp.get('deleted', 0)
        return jsonify({"ok": True, "deleted": deleted, "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/tools/update-file-path', methods=['POST'])
def tools_update_file_path():
    """Update the path of a single file in ES (when a file was moved).
    Also updates rel_path to keep it consistent.
    """
    data = request.get_json(force=True) or {}
    old_path = data.get('old_path', '').strip()
    new_path = data.get('new_path', '').strip()
    if not old_path or not new_path:
        return jsonify({"error": "old_path and new_path are required"}), 400
    if not Path(new_path).exists():
        return jsonify({"error": f"New path does not exist: {new_path}"}), 400

    try:
        from elasticsearch import Elasticsearch
        es = Elasticsearch(ES_URL)

        # Compute new rel_path relative to any of the configured base paths
        new_rel = new_path
        for bp in SEARCH_BASE_PATHS:
            try:
                new_rel = str(Path(new_path).resolve().relative_to(bp.resolve()))
                break
            except ValueError:
                continue

        body = {
            "script": {
                "source": """
                    ctx._source.path = params.new_path;
                    ctx._source.rel_path = params.new_rel;
                """,
                "lang": "painless",
                "params": {
                    "new_path": new_path,
                    "new_rel": new_rel,
                },
            },
            "query": {"term": {"path": old_path}},
        }
        resp = es.update_by_query(index=ES_INDEX, body=body, refresh=True)
        updated = resp.get('updated', 0)
        return jsonify({"ok": True, "updated": updated,
                        "old_path": old_path, "new_path": new_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/tools/update-paths', methods=['POST'])
def tools_update_paths():
    """Replace a path prefix in all ES documents (e.g. when directory was moved)."""
    data = request.get_json(force=True) or {}
    old_prefix = data.get('old_prefix', '').strip()
    new_prefix = data.get('new_prefix', '').strip()
    if not old_prefix or not new_prefix:
        return jsonify({"error": "old_prefix and new_prefix are required"}), 400
    if not Path(new_prefix).is_dir():
        return jsonify({"error": f"New prefix directory does not exist: {new_prefix}"}), 400

    try:
        from elasticsearch import Elasticsearch
        es = Elasticsearch(ES_URL)
        # Use painless script to update path and rel_path
        body = {
            "script": {
                "source": """
                    if (ctx._source.path != null && ctx._source.path.startsWith(params.old_pfx)) {
                        ctx._source.path = params.new_pfx + ctx._source.path.substring(params.old_pfx.length());
                    }
                    if (ctx._source.rel_path != null && ctx._source.rel_path.startsWith(params.old_rel)) {
                        ctx._source.rel_path = params.new_rel + ctx._source.rel_path.substring(params.old_rel.length());
                    }
                """,
                "lang": "painless",
                "params": {
                    "old_pfx": old_prefix,
                    "new_pfx": new_prefix,
                    "old_rel": old_prefix,
                    "new_rel": new_prefix,
                },
            },
            "query": {"prefix": {"path": old_prefix}},
        }
        resp = es.update_by_query(index=ES_INDEX, body=body, refresh=True)
        updated = resp.get('updated', 0)
        return jsonify({"ok": True, "updated": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── OCR Extract ───────────────────────────────────────────────────────────────

def _ocr_engine():
    """Lazy-import ocr_engine from the backend/ directory."""
    import importlib, sys
    _backend_dir = str(Path(__file__).parent)
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
    return importlib.import_module('ocr_engine')


@app.route('/api/ocr/methods')
def ocr_methods_status():
    """Return available OCR methods and their availability status."""
    ocr = _ocr_engine()
    methods_avail = ocr.check_methods(ollama_url=OLLAMA_URL)
    result = {}
    for key, meta in ocr.METHODS.items():
        result[key] = {**meta, **methods_avail.get(key, {})}
    return jsonify(result)


@app.route('/api/ocr/extract', methods=['POST'])
def ocr_extract():
    """Extract text from an uploaded file using the chosen OCR method.

    Form fields:
      file     — uploaded PDF or image (multipart/form-data)
      method   — 'pymupdf' | 'pdfminer' | 'tesseract' | 'easyocr' | 'paddleocr' | 'ollama'
      model    — Ollama model name (required when method=ollama)
      lang     — language hint: 'eng' for Tesseract, 'en' for EasyOCR/PaddleOCR (default 'eng')
      pages    — 'all' | '1' | '1-3' | '1,3,5'  (default 'all')
      prompt   — custom prompt for Ollama vision (optional)
      format   — 'text' | 'markdown' (hint for Ollama, default 'markdown')
      dpi      — rasterization DPI for OCR methods (default 200)

    Returns: text/event-stream with JSON events:
      {"type":"info",      "pages": N, "method": "…"}
      {"type":"page",      "page": N, "total": T, "text": "…"}   (non-Ollama)
      {"type":"token",     "page": N, "token": "…"}               (Ollama streaming)
      {"type":"page_done", "page": N, "text": "…"}                (Ollama per-page done)
      {"type":"done",      "text": "…"}                           (full extracted text)
      {"type":"error",     "text": "…"}
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f = request.files['file']
    file_bytes = f.read()
    filename = f.filename or 'upload'

    method  = request.form.get('method', 'pymupdf').strip()
    model   = request.form.get('model', '').strip()
    lang    = request.form.get('lang', 'eng').strip()
    pages   = request.form.get('pages', 'all').strip()
    prompt  = request.form.get('prompt', '').strip()
    fmt     = request.form.get('format', 'markdown').strip()
    dpi     = int(request.form.get('dpi', '200') or '200')
    dpi     = max(72, min(dpi, 600))

    is_pdf  = file_bytes[:4] == b'%PDF' or filename.lower().endswith('.pdf')

    def generate():
        ocr = _ocr_engine()

        def evt(obj):
            return f"data: {json.dumps(obj)}\n\n"

        try:
            # ── Determine pages ───────────────────────────────────────────────
            if is_pdf:
                total_pages = ocr.pdf_page_count(file_bytes)
                page_list   = ocr.parse_page_range(pages, total_pages)
            else:
                total_pages = 1
                page_list   = [0]

            yield evt({'type': 'info', 'pages': total_pages, 'selected': len(page_list),
                       'method': method, 'filename': filename})

            all_text_parts = []

            # ── PyMuPDF ───────────────────────────────────────────────────────
            if method == 'pymupdf':
                if not is_pdf:
                    yield evt({'type': 'error', 'text': 'PyMuPDF only supports PDF files.'})
                    return
                results = ocr.extract_pymupdf(file_bytes, page_list)
                for pg_num, text in results:
                    all_text_parts.append(text)
                    yield evt({'type': 'page', 'page': pg_num, 'total': total_pages, 'text': text})

            # ── PDFMiner ──────────────────────────────────────────────────────
            elif method == 'pdfminer':
                if not is_pdf:
                    yield evt({'type': 'error', 'text': 'PDFMiner only supports PDF files.'})
                    return
                results = ocr.extract_pdfminer(file_bytes, page_list)
                for pg_num, text in results:
                    all_text_parts.append(text)
                    yield evt({'type': 'page', 'page': pg_num, 'total': total_pages, 'text': text})

            # ── Tesseract ─────────────────────────────────────────────────────
            elif method == 'tesseract':
                if is_pdf:
                    yield evt({'type': 'status', 'text': 'Rendering PDF pages…'})
                    img_pages = ocr.pdf_to_images(file_bytes, page_list, dpi=dpi)
                else:
                    png = ocr.image_bytes_to_png(file_bytes)
                    img_pages = [(1, png)]
                results = ocr.extract_tesseract(img_pages, lang=lang)
                for pg_num, text in results:
                    all_text_parts.append(text)
                    yield evt({'type': 'page', 'page': pg_num, 'total': total_pages, 'text': text})

            # ── EasyOCR ───────────────────────────────────────────────────────
            elif method == 'easyocr':
                # Map Tesseract lang codes to EasyOCR codes (best-effort)
                _lang_map = {'eng': 'en', 'spa': 'es', 'deu': 'de', 'fra': 'fr',
                             'ita': 'it', 'por': 'pt', 'rus': 'ru', 'chi_sim': 'ch_sim',
                             'chi_tra': 'ch_tra', 'jpn': 'ja', 'kor': 'ko', 'ara': 'ar'}
                easy_lang = [_lang_map.get(lang, lang)]
                if is_pdf:
                    yield evt({'type': 'status', 'text': 'Rendering PDF pages…'})
                    img_pages = ocr.pdf_to_images(file_bytes, page_list, dpi=dpi)
                else:
                    png = ocr.image_bytes_to_png(file_bytes)
                    img_pages = [(1, png)]
                yield evt({'type': 'status', 'text': 'Loading EasyOCR model (first run may take a moment)…'})
                results = ocr.extract_easyocr(img_pages, langs=easy_lang)
                for pg_num, text in results:
                    all_text_parts.append(text)
                    yield evt({'type': 'page', 'page': pg_num, 'total': total_pages, 'text': text})

            # ── PaddleOCR ─────────────────────────────────────────────────────
            elif method == 'paddleocr':
                _lang_map = {'eng': 'en', 'spa': 'es', 'deu': 'german', 'fra': 'french',
                             'chi_sim': 'ch', 'jpn': 'japan', 'kor': 'korean', 'ara': 'ar'}
                paddle_lang = _lang_map.get(lang, 'en')
                if is_pdf:
                    yield evt({'type': 'status', 'text': 'Rendering PDF pages…'})
                    img_pages = ocr.pdf_to_images(file_bytes, page_list, dpi=dpi)
                else:
                    png = ocr.image_bytes_to_png(file_bytes)
                    img_pages = [(1, png)]
                yield evt({'type': 'status', 'text': 'Running PaddleOCR…'})
                results = ocr.extract_paddleocr(img_pages, lang=paddle_lang)
                for pg_num, text in results:
                    all_text_parts.append(text)
                    yield evt({'type': 'page', 'page': pg_num, 'total': total_pages, 'text': text})

            # ── Ollama Vision ─────────────────────────────────────────────────
            elif method == 'ollama':
                if not model:
                    yield evt({'type': 'error', 'text': 'No Ollama model selected.'})
                    return
                ollama_url = _get_ollama()
                if not ollama_url:
                    yield evt({'type': 'error', 'text': 'Ollama is not running.'})
                    return
                if is_pdf:
                    yield evt({'type': 'status', 'text': 'Rendering PDF pages…'})
                    img_pages = ocr.pdf_to_images(file_bytes, page_list, dpi=dpi)
                else:
                    png = ocr.image_bytes_to_png(file_bytes)
                    img_pages = [(1, png)]
                page_texts: dict[int, str] = {}
                for chunk in ocr.stream_ollama(img_pages, ollama_url, model, prompt, fmt):
                    if chunk['type'] == 'page_done':
                        page_texts[chunk['page']] = chunk['text']
                        all_text_parts.append(chunk['text'])
                    yield evt(chunk)

            else:
                yield evt({'type': 'error', 'text': f'Unknown method: {method}'})
                return

            full_text = '\n\n---\n\n'.join(t for t in all_text_parts if t)
            yield evt({'type': 'done', 'text': full_text})

        except Exception as e:
            log.exception(f"OCR extract error: {e}")
            yield evt({'type': 'error', 'text': str(e)})

    resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@app.route('/api/ocr/models')
def ocr_ollama_models():
    """List Ollama models that support vision (image input)."""
    ocr = _ocr_engine()
    all_models = _ollama_list_models()
    vision = [m for m in all_models if ocr._is_vision_model(m)]
    return jsonify({'models': all_models, 'vision': vision})


@app.route('/api/ocr/install', methods=['POST'])
def ocr_install_engine():
    """Install an OCR Python package via pip."""
    data = request.get_json(force=True) or {}
    engine = data.get('engine', '').strip().lower()

    ENGINE_PACKAGES = {
        'tesseract': ['pytesseract', 'Pillow'],
        'easyocr':   ['easyocr'],
        'paddleocr': ['paddlepaddle', 'paddleocr'],
    }

    if engine not in ENGINE_PACKAGES:
        return jsonify({"error": f"Unknown engine: {engine}"}), 400

    packages = ENGINE_PACKAGES[engine]
    cmd = [sys.executable, '-m', 'pip', 'install'] + packages
    task_id = f"ocr-install-{engine}-{int(time.time())}"
    _run_tool_bg(task_id, cmd, task_type=f'ocr-install-{engine}')
    return jsonify({"task_id": task_id, "status": "started", "engine": engine})


@app.route('/')
def serve_index():
    """Serve the Catalog DMS Lite hub page (index.html)."""
    if (_PROJECT_ROOT / 'index.html').exists():
        return send_from_directory(str(_PROJECT_ROOT), 'index.html')
    return jsonify({
        "service": "Catalog DMS Lite Backend",
        "endpoints": [
            "/api/search?q=query",
            "/api/es-search?q=query[&lang=de|es|en][&semantic=1]",
            "/api/ask?q=question[&lang=de|es|en][&k=5][&model=llama3.2]",
            "/api/ollama-models",
            "/api/file?path=...",
            "/api/file-text?path=...",
            "/api/play?srt=...&time=...",
            "/api/play-video?path=...&time=...",
            "/api/health",
            "/api/cache",
            "/api/translate-status",
            "/api/tools/config",
            "/api/tools/search-paths",
            "/api/tools/reindex",
            "/api/tools/index-directory",
            "/api/tools/paths",
            "/api/tools/verify-paths",
            "/api/tools/update-paths",
            "/api/tools/generate-catalog",
            "/api/tools/youtube-download",
            "/api/tools/ollama-pull",
            "/api/tools/pdf-collector",
            "/api/tools/run-script",
            "/api/tools/status/<task_id>",
        ]
    })


@app.route('/startup.html')
def serve_startup():
    """Serve the startup progress page."""
    return send_from_directory(str(_PROJECT_ROOT / 'frontend'), 'startup.html')


@app.route('/frontend/<path:filename>')
def serve_frontend(filename):
    """Serve frontend static files (HTML, CSS, JS, assets)."""
    frontend_dir = str(_PROJECT_ROOT / 'frontend')
    return send_from_directory(frontend_dir, filename)


# --- CLI ---------------------------------------------------------------------

def main():
    global SEARCH_BASE_PATH, SEARCH_BASE_PATHS, HOST, PORT, DEBUG

    parser = argparse.ArgumentParser(description="Catalog DMS Lite Backend Server")
    parser.add_argument('--host', default=HOST, help=f'Host to bind (default: {HOST})')
    parser.add_argument('--port', type=int, default=PORT, help=f'Port (default: {PORT})')
    parser.add_argument('--path', default=';'.join(str(p) for p in SEARCH_BASE_PATHS),
                        help='Base path(s) for search (semicolon-separated)')
    parser.add_argument('--debug', action='store_true', default=DEBUG)
    parser.add_argument('--no-reload', action='store_true', default=False,
                        help='Disable Werkzeug auto-reloader (faster startup, used by launcher)')
    args = parser.parse_args()

    SEARCH_BASE_PATHS = _parse_base_paths(args.path)
    SEARCH_BASE_PATH  = SEARCH_BASE_PATHS[0] if SEARCH_BASE_PATHS else Path('.').resolve()
    HOST = args.host
    PORT = args.port
    DEBUG = args.debug

    for bp in SEARCH_BASE_PATHS:
        if not _safe_exists(bp):
            print(f"WARNING: Path '{bp}' does not exist or cannot be accessed "
                  f"(offline network share / no permission?). It is skipped until it is reachable.", file=sys.stderr)

    print(f"\n{'='*60}")
    print("  Catalog — DMS Lite Backend")
    print(f"{'='*60}")
    print(f"  Project root: {_PROJECT_ROOT}")
    print(f"  Frontend dir: {_PROJECT_ROOT / 'frontend'}")
    print(f"  index.html  : {'found' if (_PROJECT_ROOT / 'index.html').exists() else 'NOT FOUND'}")
    print(f"  frontend/   : {'found' if (_PROJECT_ROOT / 'frontend').exists() else 'NOT FOUND'}")
    print(f"  Base paths  : {'; '.join(str(p) for p in SEARCH_BASE_PATHS)}")
    print(f"  Listening   : http://{HOST}:{PORT}")
    print("  API docs    : See CATALOG_SERVER_README.md")

    # Load FAISS index from disk, or build it in background if missing
    if not _load_faiss_index():
        log.info("FAISS index not found — building in background thread...")
        threading.Thread(target=_build_faiss_index, daemon=True).start()

    if _socketio_available:
        print("  WebSocket : enabled (flask-socketio)")
        print(f"{'='*60}\n")
        socketio.run(app, host=HOST, port=PORT, debug=DEBUG,
                     use_reloader=not args.no_reload, allow_unsafe_werkzeug=True)
    else:
        print("  WebSocket : disabled (pip install flask-socketio)")
        print(f"{'='*60}\n")
        app.run(host=HOST, port=PORT, debug=DEBUG, use_reloader=not args.no_reload)

if __name__ == '__main__':
    main()
