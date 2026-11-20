#!/usr/bin/env python3
"""
Elasticsearch indexer for Course Catalog.

Walks SEARCH_BASE_PATH, extracts text from each supported file,
generates multilingual embeddings (paraphrase-multilingual-MiniLM-L12-v2),
and bulk-indexes everything into Elasticsearch.

After indexing, /api/es-search in catalog_server.py can serve:
  - Full-text BM25 search with language-specific analyzers (de/es/en)
  - Semantic kNN search using dense vector similarity
  - Hybrid BM25 + kNN search

Usage:
    # Index everything (first run)
    python backend/search_indexer.py

    # Re-index from scratch (delete existing index first)
    python backend/search_indexer.py --reset

    # Delete the index (clear all data, no reindex)
    python backend/search_indexer.py --clean
    python backend/search_indexer.py --clean --yes  # skip confirmation

    # Custom path / ES URL
    python backend/search_indexer.py --path /courses --es-url http://127.0.0.1:9200

    # Skip embedding generation (BM25 only, much faster)
    python backend/search_indexer.py --no-vectors

    # From Docker:
    docker-compose run --rm indexer
"""

import os
# The Hugging Face 'xet' downloader can stall at 0 B/s on some networks; the plain HTTP one is reliable.
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
import sys
import re
import time
import json
import uuid
import hashlib
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Iterator

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)
# Suppress elastic_transport's verbose per-retry stack traces; our wait_for_es
# already logs connection failures at INFO level with a clean message.
logging.getLogger('elastic_transport').setLevel(logging.ERROR)
logging.getLogger('elastic_transport.node_pool').setLevel(logging.ERROR)

# ─── Configuration ────────────────────────────────────────────────────────────

try:
    import env
    SEARCH_BASE_PATH = Path(getattr(env, 'SEARCH_BASE_PATH', '.'))
    MAX_FILE_SIZE_MB = getattr(env, 'MAX_FILE_SIZE_MB', 50)
    SEARCHABLE_EXTENSIONS = getattr(env, 'SEARCHABLE_EXTENSIONS', set())
    ES_URL   = getattr(env, 'ES_URL', None) or os.environ.get('ES_URL', 'http://127.0.0.1:9200')
    ES_INDEX = getattr(env, 'ES_INDEX', os.environ.get('ES_INDEX', 'course_catalog'))
    CANONICAL_LANGUAGE = getattr(env, 'CANONICAL_LANGUAGE', os.environ.get('CANONICAL_LANGUAGE', ''))
    TRANSLATION_MODEL  = getattr(env, 'TRANSLATION_MODEL',  os.environ.get('TRANSLATION_MODEL', 'translategemma:4b'))
    TRANSLATION_MODE   = getattr(env, 'TRANSLATION_MODE',   os.environ.get('TRANSLATION_MODE', 'background'))
    OLLAMA_URL         = getattr(env, 'OLLAMA_URL',          os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434'))
except ImportError:
    SEARCH_BASE_PATH = Path(os.environ.get('SEARCH_BASE_PATH', '.'))
    MAX_FILE_SIZE_MB = int(os.environ.get('MAX_FILE_SIZE_MB', '50'))
    SEARCHABLE_EXTENSIONS = set()
    ES_URL   = os.environ.get('ES_URL', 'http://127.0.0.1:9200')
    ES_INDEX = os.environ.get('ES_INDEX', 'course_catalog')
    CANONICAL_LANGUAGE = os.environ.get('CANONICAL_LANGUAGE', '')
    TRANSLATION_MODEL  = os.environ.get('TRANSLATION_MODEL', 'translategemma:4b')
    TRANSLATION_MODE   = os.environ.get('TRANSLATION_MODE', 'background')
    OLLAMA_URL         = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')

if not SEARCHABLE_EXTENSIONS:
    SEARCHABLE_EXTENSIONS = {
        "txt", "md", "html", "htm", "csv", "srt", "vtt",
        "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "rtf", "odt",
    }

# ─── Minification / generated-file filters ───────────────────────────────────

# Directories that are always skipped (dependency/build/cache dirs)
_SKIP_DIRS = frozenset({
    'node_modules', '__pycache__', '.venv', 'venv',
    '.mypy_cache', '.pytest_cache', 'bower_components',
})

# Exact filenames that are auto-generated and not useful for search
_SKIP_FILENAMES = frozenset({
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
    'npm-shrinkwrap.json', 'Pipfile.lock', 'composer.lock',
    'poetry.lock', 'Gemfile.lock', 'Cargo.lock',
})


# ─── Indexing policy (all overridable via env.py or CLI) ─────────────────────
#
#   env.py / environment variable          CLI                    meaning
#   INDEX_EXCLUDE_PATTERNS  (list, ';'-sep) --exclude 'a*,b*'      extra glob patterns to skip
#   NO_VECTOR_EXTENSIONS    (set,  ','-sep) --no-vector-ext json   BM25 only (no embeddings)
#   MAX_VECTOR_CHUNKS_PER_FILE (int)        --max-vector-chunks N  bigger files → BM25 only
#
# Defaults target machine-generated / vendored noise that costs minutes of
# embedding time and adds nothing to semantic search.

def _opt(name: str, default):
    """Read optional setting from env.py (if importable) or the environment."""
    _env = globals().get('env')
    if _env is not None and hasattr(_env, name):
        return getattr(_env, name)
    return os.environ.get(name, default)

def _as_list(v, sep) -> list:
    if isinstance(v, (list, tuple, set, frozenset)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v or '').split(sep) if x.strip()]

# Directories skipped entirely (case-insensitive name match), besides _SKIP_DIRS above
_SKIP_DIRS_EXTRA = {'sboms', 'sbom', 'site-packages', 'vendor', 'third_party', 'thirdparty', '3rdparty'}

# Glob patterns (fnmatch, case-insensitive) tested against the file name and the
# path relative to the indexed root (with '/' separators)
EXCLUDE_PATTERNS = [
    '3rdpartylicenses.txt', 'third-party-licenses*', 'sbom*.json', '*.sbom.json', 'bom.json',
    'jquery*.js', 'jquery-ui*.css', 'bootstrap*.js', 'angular*.js', 'lodash*.js',
    'moment*.js', 'popper*.js', 'underscore*.js',
]
EXCLUDE_PATTERNS += _as_list(_opt('INDEX_EXCLUDE_PATTERNS', ''), ';')

# Extensions indexed for BM25 only — embeddings add little for structured data / code
NO_VECTOR_EXTS = {'json', 'xml', 'js', 'ts', 'css'}
NO_VECTOR_EXTS |= {e.lower().lstrip('.') for e in _as_list(_opt('NO_VECTOR_EXTENSIONS', ''), ',')}

# Safety net: a single file with more chunks than this is indexed BM25-only
# (otherwise one big file can block the run for 10+ minutes)
_VECTOR_POLICY = {'max_chunks': int(_opt('MAX_VECTOR_CHUNKS_PER_FILE', 400) or 0)}

def _prune_dirs(dirs: list) -> None:
    """In-place os.walk pruning: hidden dirs and dependency / vendor / SBOM dirs."""
    dirs[:] = [d for d in dirs
               if not d.startswith('.') and d not in _SKIP_DIRS
               and d.lower() not in _SKIP_DIRS_EXTRA]

def is_excluded(fpath: Path, base_path: Optional[Path] = None) -> bool:
    """True if the file matches one of EXCLUDE_PATTERNS."""
    import fnmatch
    name = fpath.name.lower()
    try:
        rel = fpath.relative_to(base_path).as_posix().lower() if base_path else name
    except ValueError:
        rel = fpath.as_posix().lower()
    return any(fnmatch.fnmatch(name, p.lower()) or fnmatch.fnmatch(rel, p.lower())
               for p in EXCLUDE_PATTERNS)

# Max line length (chars) before a JS/CSS/TS file is considered minified
_MINIFY_LINE_THRESHOLD = 500
_MINIFY_SAMPLE_BYTES   = 8192

# ─── Chunk size ───────────────────────────────────────────────────────────────

# Chunk size: characters per ES document. Smaller chunks = better semantic precision.
CHUNK_SIZE   = 1200
CHUNK_OVERLAP = 100

# Embedding model — read from env.py, fallback to MiniLM
try:
    EMBEDDING_MODEL = getattr(env, 'EMBEDDING_MODEL',
        'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
except NameError:
    EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL',
        'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')

# Model dimensions and normalization rules.
# multilingual=True means the model encodes all languages into a shared space
# and does NOT need translation before embedding (LaBSE, mE5-small).
_MODEL_CONFIG = {
    'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2': {
        'dims': 384, 'normalize_case': True,  'multilingual': False,
    },
    'sentence-transformers/paraphrase-multilingual-mpnet-base-v2': {
        'dims': 768, 'normalize_case': False, 'multilingual': False,
    },
    'intfloat/multilingual-e5-small': {
        'dims': 384, 'normalize_case': False, 'multilingual': True,
    },
    'sentence-transformers/LaBSE': {
        'dims': 768, 'normalize_case': False, 'multilingual': True,
    },
    # Bigger multilingual models — best indexed on a GPU (CUDA, or the Apple-Silicon GPU via MPS)
    'intfloat/multilingual-e5-base': {
        'dims': 768, 'normalize_case': False, 'multilingual': True,
    },
    'intfloat/multilingual-e5-large': {
        'dims': 1024, 'normalize_case': False, 'multilingual': True,
    },
    'BAAI/bge-m3': {
        'dims': 1024, 'normalize_case': False, 'multilingual': True,
    },
}
_mcfg = _MODEL_CONFIG.get(EMBEDDING_MODEL, {'dims': 384, 'normalize_case': True, 'multilingual': False})
if EMBEDDING_MODEL not in _MODEL_CONFIG:
    # A wrong vector size makes indexing fail (or silently corrupts search), so say it loudly.
    logging.getLogger('search_indexer').warning(
        f"Unknown embedding model '{EMBEDDING_MODEL}': assuming 384 dimensions. "
        f"Add it to _MODEL_CONFIG (search_indexer.py AND catalog_server.py) with its real size.")
VECTOR_DIMS = _mcfg['dims']
NORMALIZE_EMBEDDING_CASE = _mcfg['normalize_case']
MODEL_IS_MULTILINGUAL = _mcfg.get('multilingual', False)

# ─── German compound splitting (BM25 recall) ─────────────────────────────────
#
# German glues nouns together ("Schulungsunterlage", "Dokumentenablage"), so a query for
# "Schulung" misses documents that only contain the compound. A proper hyphenation
# decompounder needs a pattern file mounted into the ES container; instead we use
# ES's `dictionary_decompounder` with an inline word list of common IT / document-
# management / training vocabulary. Compounds are kept AND split, so recall rises
# without losing exact matches. Extend the list with DE_DECOMPOUND_WORDS in env.py
# (list, or comma-separated string) — it only matters at index time (needs --reset).

def _de_norm(w: str) -> str:
    """Same folding as ES `german_normalization`, which runs before the decompounder."""
    return (w.lower().replace('ä', 'a').replace('ö', 'o').replace('ü', 'u').replace('ß', 'ss'))

_DE_BASE_WORDS = """
schulung unterlage handbuch anleitung dokument dokumentation datei verwaltung konfiguration einstellung
benutzer berechtigung recht archiv ablage workflow ablauf schnittstelle funktion installation dienst
server client datenbank passwort vertrieb produkt kunde projekt protokoll fehler meldung sicherung
wiederherstellung aktualisierung update lizenz mandant ordner register suche volltext index erweiterung
vorlage formular feld maske bericht auswertung statistik aufgabe termin kalender nachricht postfach
empfang versand rechnung eingang ausgang freigabe genehmigung prüfung gruppe rolle regel vertrag
personal urlaub abwesenheit zeit erfassung buchung beleg konto zahlung kosten stelle mitarbeiter
abteilung standort system plattform anwendung oberfläche bedienung anmeldung abmeldung sitzung
verbindung netzwerk laufwerk speicher platz zugriff schutz sicherheit verschlüsselung zertifikat
signatur stempel notiz kommentar anhang version historie verlauf status kennzeichen schlagwort
metadaten attribut wert liste tabelle ansicht filter sortierung export import migration konvertierung
druck scan erkennung schrift bild video kurs lektion modul übung grundlage einführung überblick
entwicklung entwickler plugin skript script programm befehl parameter option eigenschaft objekt
element knoten struktur baum pfad verzeichnis quelle ziel zeile spalte zelle seite blatt mappe
fach kasten tafel betrieb wartung störung ausfall überwachung protokollierung zustellung
bearbeitung bearbeiter vertreter vertretung eskalation fristen frist wiedervorlage ereignis
aktion trigger auslöser bedingung zuweisung zuständigkeit organisation firma kunden lieferant
partner angebot auftrag bestellung bestand lager artikel preis rabatt vorgang akte sachakte
personalakte ablageplan aufbewahrung löschung revision nachweis dsgvo datenschutz einwilligung
""".split()

DE_DECOMP_WORDS = sorted({
    _de_norm(w) for w in
    _DE_BASE_WORDS + _as_list(_opt('DE_DECOMPOUND_WORDS', ''), ',')
    if len(w) >= 4
})

# ─── ES Index Mapping ─────────────────────────────────────────────────────────

INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "filter": {
                # Stop word filters
                "en_stop":  {"type": "stop", "stopwords": "_english_"},
                "es_stop":  {"type": "stop", "stopwords": "_spanish_"},
                "de_stop":  {"type": "stop", "stopwords": "_german_"},
                # Stemmer filters
                "en_stemmer": {"type": "stemmer", "language": "english"},
                "es_stemmer": {"type": "stemmer", "language": "light_spanish"},
                "de_stemmer": {"type": "stemmer", "language": "light_german"},
                # German compound splitting (see DE_DECOMP_WORDS): keeps the compound AND adds parts
                "de_decomp": {
                    "type": "dictionary_decompounder",
                    "word_list": DE_DECOMP_WORDS,
                    "min_word_size": 7, "min_subword_size": 4, "max_subword_size": 18,
                    "only_longest_match": False,
                },
                # Synonym filters (updateable=True → search-time only)
                "en_synonyms": {
                    "type": "synonym_graph",
                    "synonyms": [
                        "video, movie, film, recording, clip",
                        "audio, sound, music",
                        "course, class, lesson, tutorial, training, workshop, module",
                        "presentation, slides, deck, slideshow, powerpoint",
                        "document, file, paper, report, manual, guide, handbook",
                        "image, picture, photo, figure, screenshot, thumbnail",
                        "introduction, intro, overview, basics, fundamentals",
                        "advanced, expert, professional, senior",
                        "beginner, starter, novice, elementary",
                        "exercise, practice, homework, task, assignment, lab",
                        "summary, overview, conclusion, review, recap",
                        "error, bug, issue, problem, fault, exception",
                        "install, setup, configure, deploy, initialize",
                        "database, db, datastore, storage, repository",
                        "server, backend, api, service, endpoint",
                        "frontend, client, ui, interface, web",
                        "password, credential, secret, auth, authentication",
                        "test, testing, spec",
                        "function, method, procedure, routine",
                        "variable, parameter, argument, attribute, property",
                        "array, list, collection, sequence, vector",
                        "string, text, character",
                    ],
                    "updateable": True,
                },
                "es_synonyms": {
                    "type": "synonym_graph",
                    "synonyms": [
                        "video, pelicula, grabacion, clip",
                        "audio, sonido, musica, pista",
                        "curso, clase, leccion, tutorial, capacitacion, formacion, taller, modulo",
                        "presentacion, diapositivas, exposicion",
                        "documento, archivo, informe, manual, guia",
                        "imagen, foto, fotografia, figura",
                        "introduccion, fundamentos",
                        "avanzado, experto, profesional",
                        "principiante, novato, basico",
                        "ejercicio, practica, tarea",
                        "resumen, conclusion, sintesis",
                        "error, fallo, problema, defecto",
                        "instalar, configurar, desplegar",
                        "almacenamiento, repositorio",
                        "servidor, servicio, api",
                        "contrasena, credencial, autenticacion",
                        "prueba, test, especificacion",
                        "funcion, metodo, procedimiento, rutina",
                        "variable, parametro, argumento, atributo",
                        "arreglo, lista, coleccion, vector",
                        "cadena, texto, caracter",
                    ],
                    "updateable": True,
                },
                "de_synonyms": {
                    "type": "synonym_graph",
                    "synonyms": [
                        "video, film, aufnahme, clip, aufzeichnung",
                        "audio, ton, musik, klang",
                        "kurs, klasse, lektion, tutorial, schulung, training, workshop, modul",
                        "prasentation, folien, vortrag",
                        "dokument, datei, bericht, handbuch, anleitung",
                        "bild, foto, abbildung, screenshot",
                        "einfuhrung, einleitung, uberblick, grundlagen",
                        "fortgeschritten, experte, profi",
                        "anfanger, einsteiger, grundstufe",
                        "ubung, aufgabe, hausaufgabe",
                        "zusammenfassung, fazit, schluss",
                        "fehler, bug, problem, defekt",
                        "installieren, einrichten, konfigurieren, bereitstellen",
                        "datenbank, datenspeicher, repository",
                        "server, dienst, api, schnittstelle",
                        "passwort, kennwort, zugangsdaten, authentifizierung",
                        "test, prufung, spezifikation",
                        "funktion, methode, prozedur, routine",
                        "variable, parameter, argument, attribut",
                        "array, liste, sammlung, vektor",
                        "zeichenkette, text, zeichen",
                    ],
                    "updateable": True,
                },
            },
            "analyzer": {
                # Search-time analyzers with synonyms
                # (index-time uses built-in german/spanish/english)
                "english_search": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "en_stop", "en_stemmer", "en_synonyms"],
                },
                "spanish_search": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "es_stop", "es_stemmer", "es_synonyms"],
                },
                "german_search": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "german_normalization", "de_stop", "de_stemmer", "de_synonyms"],
                },
                # Same analyzer at index and query time: stems AND splits compounds
                "german_decomp": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "german_normalization", "de_decomp", "de_stop", "de_stemmer"],
                },
            },
        },
    },
    "mappings": {
        "properties": {
            "path":           {"type": "keyword"},
            "rel_path":       {"type": "keyword"},
            "filename":       {"type": "keyword"},
            "ext":            {"type": "keyword"},
            "file_hash":      {"type": "keyword"},
            "chunk_index":    {"type": "integer"},
            "file_mtime":     {"type": "date"},
            "srt_start_time": {"type": "float"},
            "srt_end_time":   {"type": "float"},
            "content": {
                "type": "text",
                "analyzer": "standard",
                "fields": {
                    "de": {
                        "type": "text",
                        "analyzer": "german",
                        "search_analyzer": "german_search",
                    },
                    "es": {
                        "type": "text",
                        "analyzer": "spanish",
                        "search_analyzer": "spanish_search",
                    },
                    "en": {
                        "type": "text",
                        "analyzer": "english",
                        "search_analyzer": "english_search",
                    },
                    # German with compound splitting (recall for "Schulung" → "Schulungsunterlage")
                    "de_c": {"type": "text", "analyzer": "german_decomp"},
                },
            },
            # Document title and section breadcrumb ("Manual > Install > Linux"): short,
            # high-signal fields that BM25 weights above the body text.
            "title": {"type": "text", "analyzer": "standard",
                      "fields": {"de": {"type": "text", "analyzer": "german"}}},
            "section":    {"type": "text", "analyzer": "standard"},
            "collection": {"type": "keyword"},   # first folder under the indexed root → filter by source
            "content_vector": {
                "type": "dense_vector",
                "dims": VECTOR_DIMS,
                "index": True,
                "similarity": "cosine",
            },
            "indexed_at": {"type": "date"},
            "canonical_content":   {"type": "text", "analyzer": "standard"},
            "translation_status":  {"type": "keyword"},
            "detected_lang":       {"type": "keyword"},
            "analytics": {
                "type": "object",
                "properties": {
                    "person_1st":     {"type": "integer"},
                    "person_2nd":     {"type": "integer"},
                    "person_3rd":     {"type": "integer"},
                    "lexical_richness": {"type": "float"},
                    "avg_word_length":  {"type": "float"},
                },
            },
        }
    },
}

# ─── Text Extraction (same logic as catalog_server.py, no Flask dependency) ───

def detect_encoding(path: str) -> str:
    try:
        import chardet
        with open(path, 'rb') as f:
            raw = f.read(32768)
        return chardet.detect(raw).get('encoding') or 'utf-8'
    except Exception:
        return 'utf-8'

def read_text(path: str) -> Optional[str]:
    enc = detect_encoding(path)
    try:
        with open(path, encoding=enc, errors='replace') as f:
            return f.read()
    except Exception:
        return None

def is_likely_minified(fpath: Path, ext: str) -> bool:
    """Return True if the file appears to be minified, bundled, or machine-generated.

    Checks (in order):
      1. Known lock / manifest filenames (package-lock.json, yarn.lock, …)
      2. Source-map files (*.map)
      3. Explicit .min.* suffix or bundler patterns (.bundle.js, .chunk.js, …)
      4. Content heuristic for JS/CSS/TS: any line longer than _MINIFY_LINE_THRESHOLD
         chars in the first _MINIFY_SAMPLE_BYTES suggests minification.
    """
    name = fpath.name

    # 1. Known auto-generated lock / manifest files
    if name in _SKIP_FILENAMES:
        return True

    name_lower = name.lower()

    # 2. Source maps — never useful for text search
    if name_lower.endswith('.map'):
        return True

    # 3. Explicit .min.* or bundler output patterns
    if re.search(r'\.min\.(js|css|ts)$', name_lower):
        return True
    if re.search(r'\.(bundle|chunk|vendor|polyfill|runtime)\.(js|ts)$', name_lower):
        return True

    # 4. Content-based detection for JS / CSS / TS
    if ext in ('js', 'css', 'ts'):
        try:
            if fpath.stat().st_size < 200:
                return False  # tiny file — no need to check
            sample = fpath.read_bytes()[:_MINIFY_SAMPLE_BYTES].decode('utf-8', errors='replace')
            max_line = max((len(line) for line in sample.split('\n')), default=0)
            if max_line > _MINIFY_LINE_THRESHOLD:
                log.debug(f"  Skipped (minified, max_line={max_line}): {name}")
                return True
        except OSError:
            pass

    return False


def extract_text(path: str, ext: str) -> Optional[str]:
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        return None

    ext = ext.lower().lstrip('.')

    if ext == 'pdf':
        try:
            import fitz
            doc = fitz.open(path)
            return '\n'.join(p.get_text() for p in doc)
        except ImportError:
            pass
        try:
            import pdfminer.high_level
            return pdfminer.high_level.extract_text(path)
        except Exception:
            return None

    if ext == 'docx':
        try:
            from docx import Document
            doc = Document(path)
            parts = [p.text for p in doc.paragraphs if p.text.strip()]
            for tbl in doc.tables:
                for row in tbl.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            parts.append(cell.text)
            return '\n'.join(parts)
        except Exception:
            return None

    if ext in ('xlsx', 'xls'):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets:
                parts.append(f'[Sheet: {ws.title}]')
                for row in ws.iter_rows():
                    vals = [str(c.value) for c in row if c.value is not None]
                    if vals:
                        parts.append('\t'.join(vals))
            return '\n'.join(parts)
        except Exception:
            return None

    if ext in ('pptx', 'ppt'):
        try:
            from pptx import Presentation
            prs = Presentation(path)
            parts = []
            for i, slide in enumerate(prs.slides, 1):
                parts.append(f'[Slide {i}]')
                for shape in slide.shapes:
                    if hasattr(shape, 'text') and shape.text.strip():
                        parts.append(shape.text)
            return '\n'.join(parts)
        except Exception:
            return None

    if ext == 'rtf':
        try:
            from striprtf.striprtf import rtf_to_text
            with open(path, encoding='utf-8', errors='replace') as f:
                return rtf_to_text(f.read())
        except ImportError:
            pass
        raw = read_text(path)
        if raw:
            raw = re.sub(r'\\[a-z]+-?\d*\s?', ' ', raw)
            raw = re.sub(r'[{}\\]', ' ', raw)
            return re.sub(r'\s+', ' ', raw).strip()
        return None

    if ext in ('html', 'htm'):
        raw = read_text(path)
        if raw is None:
            return None
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(raw, 'html.parser').get_text(separator='\n')
        except ImportError:
            return re.sub(r'<[^>]+>', ' ', raw)

    if ext == 'xml':
        raw = read_text(path)
        return re.sub(r'<[^>]+>', ' ', raw) if raw else None

    # Plain text / code / srt / vtt / etc.
    return read_text(path)


def detect_language(text: str) -> str:
    """Detect language of text. Returns ISO 639-1 code or 'unknown'."""
    try:
        from langdetect import detect
        sample = text[:500].strip()
        if not sample:
            return 'unknown'
        lang = detect(sample)
        # Normalize to supported: de/es/en → keep; others → 'other'
        return lang if lang in ('de', 'es', 'en') else 'other'
    except Exception:
        return 'unknown'


def enqueue_translation(queue_file: Path, doc_id: str, path: str, chunk_index: int,
                        source_lang: str, target_lang: str, text: str):
    """Append a translation task to the queue file."""
    task = {
        "task_id":     str(uuid.uuid4()),
        "es_doc_id":   doc_id,
        "path":        path,
        "chunk_index": chunk_index,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "text":        text,
        "status":      "pending",
        "attempts":    0,
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }
    with open(queue_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(task, ensure_ascii=False) + '\n')


# ─── Discourse analytics ──────────────────────────────────────────────────────

_RE_WORDS = re.compile(r"\b[a-zA-Z']+\b")

# Whole-word patterns for pronoun groups
_RE_P1 = re.compile(
    r'\b(i|we|my|our|me|us|myself|ourselves)\b', re.IGNORECASE
)
_RE_P2 = re.compile(
    r'\b(you|your|yourself|yourselves)\b', re.IGNORECASE
)
_RE_P3 = re.compile(
    r'\b(he|she|they|it|his|her|their|him|them)\b', re.IGNORECASE
)


def compute_discourse_metrics(text: str) -> dict:
    """Return discourse analytics metrics for a text chunk.

    All computation is regex/string-based — no ML or spaCy required.

    Keys returned:
        person_1st      — count of 1st-person pronouns (I, we, my, …)
        person_2nd      — count of 2nd-person pronouns (you, your, …)
        person_3rd      — count of 3rd-person pronouns (he, she, they, …)
        lexical_richness — unique_words / total_words (TTR); 0.0 if empty
        avg_word_length  — mean character length of word tokens; 0.0 if empty
    """
    person_1st = len(_RE_P1.findall(text))
    person_2nd = len(_RE_P2.findall(text))
    person_3rd = len(_RE_P3.findall(text))

    words = _RE_WORDS.findall(text)
    total = len(words)
    if total:
        unique = len(set(w.lower() for w in words))
        lexical_richness = round(unique / total, 4)
        avg_word_length  = round(sum(len(w) for w in words) / total, 4)
    else:
        lexical_richness = 0.0
        avg_word_length  = 0.0

    return {
        "person_1st":      person_1st,
        "person_2nd":      person_2nd,
        "person_3rd":      person_3rd,
        "lexical_richness": lexical_richness,
        "avg_word_length":  avg_word_length,
    }


# ─── Text chunking ────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks of ~CHUNK_SIZE characters.

    Splits on paragraph boundaries (double newline) where possible so chunks
    contain semantically coherent units rather than half-sentences.
    """
    # Normalise whitespace inside paragraphs
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

    chunks = []
    current = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current_len + para_len > CHUNK_SIZE and current:
            chunks.append('\n\n'.join(current))
            # Keep last paragraph for overlap
            current = current[-1:]
            current_len = len(current[0]) if current else 0
        # If a single paragraph is larger than CHUNK_SIZE, hard-split it
        if para_len > CHUNK_SIZE:
            for i in range(0, para_len, CHUNK_SIZE - CHUNK_OVERLAP):
                sub = para[i:i + CHUNK_SIZE]
                if sub.strip():
                    chunks.append(sub)
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append('\n\n'.join(current))

    return chunks or [text[:CHUNK_SIZE]]

# ─── Structure-aware chunking + document context ─────────────────────────────

_MD_HEADING = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$')
MIN_SECTION_CHARS = 300      # smaller sections are merged into the next one

def chunk_structured(text: str, ext: str) -> list:
    """Split into (chunk, section) pairs.

    Markdown is cut along its headings: a chunk never straddles two sections, and each
    one carries its heading path ("Install > Linux") as `section`. Other formats keep
    the generic paragraph chunking with an empty section.
    """
    if ext not in ('md', 'markdown'):
        return [(c, '') for c in chunk_text(text)]

    sections, stack, cur, cur_path, in_code = [], [], [], '', False
    for line in text.splitlines():
        if line.lstrip().startswith('```'):
            in_code = not in_code
        m = None if in_code else _MD_HEADING.match(line)
        if m:
            sections.append((cur_path, '\n'.join(cur).strip()))
            level, title = len(m.group(1)), m.group(2).strip()
            stack = [(l, t) for l, t in stack if l < level] + [(level, title)]
            cur_path = ' > '.join(t for _, t in stack)
            cur = [line]
        else:
            cur.append(line)
    sections.append((cur_path, '\n'.join(cur).strip()))

    out, pending = [], ''
    for path, body in sections:
        if not body:
            continue
        body = (pending + '\n\n' + body) if pending else body
        if len(body) < MIN_SECTION_CHARS:
            pending = body                       # too small to stand alone → prepend to the next section
            pending_path = path
            continue
        pending = ''
        out.extend((c, path) for c in chunk_text(body))
    if pending:
        out.extend((c, pending_path) for c in chunk_text(pending))
    return out or [(c, '') for c in chunk_text(text)]


def doc_title(text: str, ext: str, fname: str) -> str:
    """First H1 for markdown, otherwise the file name without extension."""
    if ext in ('md', 'markdown'):
        for line in text.splitlines()[:60]:
            m = re.match(r'^#\s+(.+?)\s*#*\s*$', line)
            if m:
                return m.group(1).strip()[:200]
    stem = re.sub(r'[_\-]+', ' ', Path(fname).stem).strip()
    return stem[:200] or fname


def context_prefix(title: str, section: str) -> str:
    """Breadcrumb prepended to the text that gets EMBEDDED (contextual embeddings).

    A chunk like "Set the value to 30" means little alone; with "ELO Server > Timeouts"
    in front, the vector knows what it is about. The stored `content` stays unchanged.
    """
    parts = [p for p in (title, section) if p]
    if title and section and section.startswith(title):
        parts = [section]
    return ' > '.join(parts)[:240]

# ─── SRT parsing ──────────────────────────────────────────────────────────────

_SRT_TS_BLOCK_RE = re.compile(
    r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})'
)

def parse_srt_blocks(path: str) -> list:
    """Parse SRT file into list of {text, start_seconds, end_seconds} dicts."""
    raw = read_text(path)
    if not raw:
        return []

    result = []
    for block in re.split(r'\n\s*\n', raw.strip()):
        lines = block.strip().splitlines()
        if not lines:
            continue
        for idx, line in enumerate(lines):
            m = _SRT_TS_BLOCK_RE.search(line)
            if m:
                start_s = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3)) + int(m.group(4))/1000
                end_s   = int(m.group(5))*3600 + int(m.group(6))*60 + int(m.group(7)) + int(m.group(8))/1000
                # Text lines after timestamp line; strip HTML tags common in SRTs
                text_lines = [re.sub(r'<[^>]+>', '', l).strip() for l in lines[idx+1:] if l.strip()]
                text = ' '.join(text_lines)
                if text:
                    result.append({'text': text, 'start_seconds': start_s, 'end_seconds': end_s})
                break
    return result


def chunk_srt_blocks(blocks: list) -> list:
    """Group SRT blocks into time-stamped chunks of ~CHUNK_SIZE characters."""
    chunks = []
    current_texts = []
    current_len = 0
    chunk_start = None
    chunk_end = None

    for block in blocks:
        blen = len(block['text'])
        if current_len + blen > CHUNK_SIZE and current_texts:
            chunks.append({
                'text': ' '.join(current_texts),
                'start_seconds': chunk_start,
                'end_seconds': chunk_end,
            })
            current_texts = []
            current_len = 0
            chunk_start = None

        if chunk_start is None:
            chunk_start = block['start_seconds']
        chunk_end = block['end_seconds']
        current_texts.append(block['text'])
        current_len += blen

    if current_texts:
        chunks.append({
            'text': ' '.join(current_texts),
            'start_seconds': chunk_start,
            'end_seconds': chunk_end,
        })
    return chunks


# ─── File hashing ─────────────────────────────────────────────────────────────

def compute_file_hash(path: str, buf_size: int = 65536) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            data = f.read(buf_size)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


# ─── Incremental indexing helpers ─────────────────────────────────────────────

def get_indexed_hashes(es, index: str) -> set:
    """Return set of file_hash values already present in the index.

    Used to skip duplicate files (same content, different path).
    """
    if not es.indices.exists(index=index):
        return set()
    try:
        result = set()
        after_key = None
        while True:
            composite = {
                "size": 1000,
                "sources": [{"file_hash": {"terms": {"field": "file_hash"}}}],
            }
            if after_key:
                composite["after"] = after_key
            resp = es.search(
                index=index,
                body={
                    "size": 0,
                    "aggs": {"by_hash": {"composite": composite}},
                },
            )
            buckets = resp["aggregations"]["by_hash"]["buckets"]
            for bucket in buckets:
                h = bucket["key"]["file_hash"]
                if h:
                    result.add(h)
            if len(buckets) < 1000:
                break
            after_key = resp["aggregations"]["by_hash"]["after_key"]
        return result
    except Exception as e:
        log.warning(f"Could not retrieve indexed hashes: {e}")
        return set()


def get_indexed_mtimes(es, index: str) -> dict:
    """Return {path: mtime_iso_prefix} for all paths in the index.

    Uses composite aggregation with pagination to avoid the ES
    max_result_window (10 000) limit that breaks large indexes.
    """
    if not es.indices.exists(index=index):
        return {}
    try:
        result = {}
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
                body={
                    "size": 0,
                    "aggs": {
                        "by_path": {
                            "composite": composite,
                            "aggs": {
                                "latest_mtime": {"max": {"field": "file_mtime"}},
                            },
                        }
                    },
                },
            )
            buckets = resp["aggregations"]["by_path"]["buckets"]
            for bucket in buckets:
                path = bucket["key"]["path"]
                epoch_ms = bucket["latest_mtime"]["value"]
                if epoch_ms is not None:
                    iso = datetime.fromtimestamp(
                        epoch_ms / 1000, timezone.utc
                    ).isoformat()
                    result[path] = iso[:19]
            if len(buckets) < 1000:
                break
            after_key = resp["aggregations"]["by_path"]["after_key"]
        return result
    except Exception as e:
        log.warning(f"Could not retrieve indexed mtimes: {e}")
        return {}


def get_indexed_path_info(es, index: str) -> dict:
    """Return {path: (mtime_iso_prefix, file_hash)} for every file in the index.

    The hash is what lets an incremental scan tell a file that was only *touched*
    (copied, synced, restored → newer mtime, same content) from one that really changed.
    """
    if not es.indices.exists(index=index):
        return {}
    try:
        result, after_key = {}, None
        while True:
            composite = {"size": 1000, "sources": [{"path": {"terms": {"field": "path"}}}]}
            if after_key:
                composite["after"] = after_key
            resp = es.search(index=index, body={"size": 0, "aggs": {"by_path": {
                "composite": composite,
                "aggs": {"latest_mtime": {"max": {"field": "file_mtime"}},
                         "hash": {"terms": {"field": "file_hash", "size": 1}}},
            }}})
            buckets = resp["aggregations"]["by_path"]["buckets"]
            for b in buckets:
                ms = b["latest_mtime"]["value"]
                if ms is None:
                    continue
                hb = b["hash"]["buckets"]
                result[b["key"]["path"]] = (
                    datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()[:19],
                    hb[0]["key"] if hb else '')
            if len(buckets) < 1000:
                break
            after_key = resp["aggregations"]["by_path"]["after_key"]
        return result
    except Exception as e:
        log.warning(f"Could not retrieve indexed path info: {e}")
        return {}


def refresh_mtimes(es, index: str, mtimes: dict):
    """Store the files' current mtime on their (unchanged) chunks, so the next scan skips them."""
    items = list(mtimes.items())
    for i in range(0, len(items), 400):
        batch = dict(items[i:i + 400])
        try:
            es.update_by_query(
                index=index, conflicts='proceed', refresh=True,
                body={"query": {"terms": {"path": list(batch)}},
                      "script": {"lang": "painless",
                                 "source": "ctx._source.file_mtime = params.m.get(ctx._source.path);",
                                 "params": {"m": batch}}})
        except Exception as e:
            log.warning(f"Could not refresh stored dates for {len(batch)} file(s): {e}")


def delete_paths_from_index(es, index: str, paths: list):
    """Delete all documents for the given list of file paths."""
    if not paths:
        return
    try:
        es.delete_by_query(
            index=index,
            body={"query": {"terms": {"path": paths}}},
            refresh=True,
        )
        log.info(f"Deleted documents for {len(paths)} changed/removed file(s).")
    except Exception as e:
        log.warning(f"delete_by_query failed: {e}")


# ─── Embedding ────────────────────────────────────────────────────────────────

# Models not supported by fastembed — use sentence-transformers instead
_ST_ONLY_MODELS = {'intfloat/multilingual-e5-small', 'intfloat/multilingual-e5-base',
                   'intfloat/multilingual-e5-large', 'BAAI/bge-m3', 'sentence-transformers/LaBSE'}
# E5-family models require "query:" / "passage:" prefixes
_E5_MODELS = {'intfloat/multilingual-e5-small', 'intfloat/multilingual-e5-base', 'intfloat/multilingual-e5-large'}

_embed_model = None
_embed_backend = None  # 'fastembed' | 'sentence-transformers'
_embed_device = 'cpu'

def _pick_device() -> str:
    """Where the embedding model runs: 'cuda' (NVIDIA), 'mps' (Apple-Silicon GPU) or 'cpu'.

    Automatic by default; force one with EMBEDDING_DEVICE in env.py / the environment
    (e.g. EMBEDDING_DEVICE=cpu if the GPU runs out of memory)."""
    want = str(_opt('EMBEDDING_DEVICE', 'auto')).strip().lower()
    if want not in ('', 'auto'):
        return want
    try:
        import torch
        if torch.cuda.is_available():
            return 'cuda'
        mps = getattr(torch.backends, 'mps', None)
        if mps is not None and mps.is_available():
            return 'mps'
    except Exception:
        pass
    return 'cpu'

def _prefer_offline_model() -> None:
    """Skip the Hugging Face network round-trips when the model is already cached.

    Every start otherwise sends several HEAD/GET requests to huggingface.co, which
    took ~2 min on this network. Never forces offline when the model is NOT cached
    (first run still downloads), and never overrides an explicit HF_HUB_OFFLINE.
    """
    if 'HF_HUB_OFFLINE' in os.environ:
        return
    try:
        from huggingface_hub import try_to_load_from_cache
        import huggingface_hub.constants as _hfc
        hit = try_to_load_from_cache(EMBEDDING_MODEL, 'config.json')
        if isinstance(hit, str):                       # a real cached file path
            os.environ['HF_HUB_OFFLINE'] = '1'
            os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
            _hfc.HF_HUB_OFFLINE = True                 # already-imported module reads this at call time
            log.info("Embedding model found in local cache → loading offline (HF_HUB_OFFLINE=1).")
    except Exception as e:                             # missing hub lib, odd cache layout, …
        log.debug(f"Offline-cache check skipped: {e}")

def get_embed_model():
    global _embed_model, _embed_backend
    if _embed_model is None:
        log.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _prefer_offline_model()
        if EMBEDDING_MODEL in _ST_ONLY_MODELS:
            global _embed_device
            # PyTorch ops that Apple's GPU backend lacks fall back to the CPU instead of crashing
            os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
            from sentence_transformers import SentenceTransformer
            _embed_device  = _pick_device()
            _embed_model   = SentenceTransformer(EMBEDDING_MODEL, device=_embed_device)
            _embed_backend = 'sentence-transformers'
        else:
            import warnings
            from fastembed import TextEmbedding
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=".*now uses mean pooling.*",
                                        category=UserWarning)
                _embed_model = TextEmbedding(EMBEDDING_MODEL)
            _embed_backend = 'fastembed'
        if _embed_backend == 'sentence-transformers':
            log.info(f"Embedding model ready (backend: {_embed_backend}, device: {_embed_device}"
                     f"{' — GPU acceleration' if _embed_device in ('cuda', 'mps') else ''}).")
        else:
            log.info(f"Embedding model ready (backend: {_embed_backend}, CPU only). "
                     f"For GPU use a sentence-transformers model, e.g. intfloat/multilingual-e5-small or BAAI/bge-m3.")
    return _embed_model

def embed_texts(texts: list[str], is_query: bool = False) -> list[list[float]]:
    """Return list of embedding vectors (as plain Python lists for JSON).

    is_query=True adds 'query: ' prefix for E5 models (vs 'passage: ' for docs).
    When NORMALIZE_EMBEDDING_CASE is True (MiniLM), texts are lowercased first.
    """
    model = get_embed_model()
    if NORMALIZE_EMBEDDING_CASE:
        texts = [t.lower() for t in texts]
    if _embed_backend == 'sentence-transformers':
        if EMBEDDING_MODEL in _E5_MODELS:
            prefix = "query: " if is_query else "passage: "
            texts  = [prefix + t for t in texts]
        import numpy as _np
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False,
                            batch_size=64 if _embed_device in ('cuda', 'mps') else 32)   # GPUs like bigger batches
        return [v.tolist() for v in vecs]
    return [v.tolist() for v in model.embed(texts)]

EMBED_BATCH = 64   # chunks per embedding call → progress line + responsiveness

def embed_texts_batched(texts: list[str], label: str = '') -> list[list[float]]:
    """embed_texts() in batches, logging progress for big files.

    A single call on a 1,400-chunk file blocked the run ~13 min with no output.
    NOTE: progress lines deliberately avoid the "[n/total]" pattern — the backend
    parses that to drive the file-level progress bar.
    """
    n = len(texts)
    if n <= EMBED_BATCH:
        return embed_texts(texts)
    out: list = []
    t0 = time.time()
    for i in range(0, n, EMBED_BATCH):
        out.extend(embed_texts(texts[i:i + EMBED_BATCH]))
        done = len(out)
        el = time.time() - t0
        eta = el / done * (n - done)
        log.info(f"    embedding {done}/{n} chunks ({done * 100 // n}%) — {label}"
                 f"  elapsed {el:.0f}s, ~{eta:.0f}s left")
    return out

def vectors_wanted(ext: str, n_chunks: int, fname: str) -> bool:
    """Per-file policy: structured/code extensions and very large files are BM25-only."""
    if ext in NO_VECTOR_EXTS:
        return False
    mx = _VECTOR_POLICY['max_chunks']
    if mx and n_chunks > mx:
        log.info(f"    {fname}: {n_chunks} chunks > {mx} → BM25 only (no vectors)")
        return False
    return True

# ─── ES helpers ───────────────────────────────────────────────────────────────

def wait_for_es(es, retries: int = 30, interval: float = 5.0):
    import time
    for i in range(retries):
        try:
            info = es.info()
            log.info(f"Connected to Elasticsearch {info['version']['number']}")
            return True
        except Exception as e:
            log.info(f"Waiting for Elasticsearch ({i+1}/{retries})… {e}")
            if i < retries - 1:
                time.sleep(interval)
    log.error("Could not connect to Elasticsearch after multiple attempts.")
    return False

def create_index(es, index: str, reset: bool):
    exists = es.indices.exists(index=index)
    if exists and reset:
        log.info(f"Deleting existing index '{index}'…")
        es.indices.delete(index=index)
        exists = False
    if not exists:
        log.info(f"Creating index '{index}'…")
        es.indices.create(index=index, body=INDEX_MAPPING)
        log.info(f"Index '{index}' created.")
    else:
        log.info(f"Index '{index}' already exists (use --reset to re-index).")

def generate_actions(
    base_path: Path,
    index: str,
    use_vectors: bool,
    files_list: Optional[list] = None,
    canonical_lang: str = '',
    queue_file: Optional[Path] = None,
    translation_tasks: Optional[list] = None,
) -> Iterator[dict]:
    """Yield ES bulk action dicts.

    If files_list is provided (list of (fpath, ext, fmtime[, file_hash]) tuples),
    use that. Otherwise walk base_path for a full index.

    If canonical_lang is set, detects language per file and adds detected_lang +
    translation_status fields to each doc. Chunks needing translation are
    appended to translation_tasks (if provided) or written to queue_file directly.
    """
    now = datetime.now(timezone.utc).isoformat()
    file_count = 0
    chunk_count = 0

    if files_list is None:
        # Build full list from directory walk, deduplicating by content hash
        files_list = []
        seen_hashes = set()
        skipped_dupes = 0
        for root, dirs, files in os.walk(base_path):
            _prune_dirs(dirs)
            for fname in files:
                ext = Path(fname).suffix.lstrip('.').lower()
                if ext not in SEARCHABLE_EXTENSIONS:
                    continue
                fpath = Path(root) / fname
                if is_excluded(fpath, base_path) or is_likely_minified(fpath, ext):
                    continue
                try:
                    fmtime = datetime.fromtimestamp(fpath.stat().st_mtime, timezone.utc).isoformat()
                except OSError:
                    fmtime = now
                try:
                    fhash = compute_file_hash(str(fpath))
                except OSError:
                    fhash = ''
                if fhash and fhash in seen_hashes:
                    skipped_dupes += 1
                    log.debug(f"  Skipped duplicate: {fname} (hash {fhash[:12]}…)")
                    continue
                if fhash:
                    seen_hashes.add(fhash)
                files_list.append((fpath, ext, fmtime, fhash))
        if skipped_dupes:
            log.info(f"  {skipped_dupes} duplicate file(s) skipped (same content, different path).")

    total = len(files_list)

    for entry in files_list:
        fpath, ext, fmtime = entry[0], entry[1], entry[2]
        fhash = entry[3] if len(entry) > 3 else ''
        fname = fpath.name
        try:
            rel = str(fpath.relative_to(base_path))
        except ValueError:
            rel = str(fpath)

        # ── SRT: preserve timestamps ──────────────────────────────────────────
        if ext in ('srt', 'vtt'):
            blocks = parse_srt_blocks(str(fpath))
            if not blocks:
                continue
            srt_chunks = chunk_srt_blocks(blocks)

            if use_vectors and vectors_wanted(ext, len(srt_chunks), fname):
                try:
                    vectors = embed_texts_batched([c['text'] for c in srt_chunks], fname)
                except Exception as e:
                    log.warning(f"Embedding failed for {fname}: {e}")
                    vectors = [None] * len(srt_chunks)
            else:
                vectors = [None] * len(srt_chunks)

            # Detect language once per SRT file (use first chunk's text)
            if canonical_lang and srt_chunks:
                detected_lang = detect_language(srt_chunks[0]['text'])
            else:
                detected_lang = 'unknown'

            file_count += 1
            log.info(f"  [{file_count}/{total}] {fname}  ({len(srt_chunks)} chunks)")
            for i, (chunk, vector) in enumerate(zip(srt_chunks, vectors)):
                needs_translation = (
                    canonical_lang
                    and detected_lang not in ('unknown', 'other')
                    and detected_lang != canonical_lang
                )
                doc = {
                    "path":           str(fpath),
                    "rel_path":       rel,
                    "filename":       fname,
                    "ext":            ext,
                    "file_hash":      fhash,
                    "chunk_index":    i,
                    "file_mtime":     fmtime,
                    "content":        chunk['text'],
                    "indexed_at":     now,
                    "srt_start_time": chunk['start_seconds'],
                    "srt_end_time":   chunk['end_seconds'],
                    "analytics":      compute_discourse_metrics(chunk['text']),
                }
                if canonical_lang:
                    doc["detected_lang"] = detected_lang
                    doc["translation_status"] = "pending" if needs_translation else "done"
                if vector is not None:
                    doc["content_vector"] = vector
                action = {"_index": index, "_source": doc}
                yield action
                # After yielding we don't know the ES _id yet, so queue without doc_id
                # (worker will update by query or the doc_id is set post-bulk)
                if needs_translation and queue_file:
                    try:
                        enqueue_translation(
                            queue_file, doc_id='', path=str(fpath),
                            chunk_index=i, source_lang=detected_lang,
                            target_lang=canonical_lang, text=chunk['text'],
                        )
                    except Exception as eq:
                        log.warning(f"Failed to enqueue translation task: {eq}")
                elif needs_translation and translation_tasks is not None:
                    translation_tasks.append({
                        'path': str(fpath), 'chunk_index': i,
                        'source_lang': detected_lang, 'target_lang': canonical_lang,
                        'text': chunk['text'],
                    })
                chunk_count += 1
            continue

        # ── Standard file extraction ──────────────────────────────────────────
        try:
            text = extract_text(str(fpath), ext)
        except Exception as e:
            log.debug(f"Extraction failed {fpath}: {e}")
            continue

        if not text or not text.strip():
            continue

        pairs    = chunk_structured(text, ext)
        chunks   = [c for c, _ in pairs]
        sections = [s for _, s in pairs]
        title    = doc_title(text, ext, fname)
        collection = rel.replace('\\', '/').split('/')[0] if ('/' in rel or '\\' in rel) else ''
        file_count += 1
        log.info(f"  [{file_count}/{total}] {fname}  ({len(chunks)} chunks)")

        # Detect language once per file (use beginning of full text)
        if canonical_lang:
            detected_lang = detect_language(text)
        else:
            detected_lang = 'unknown'

        if use_vectors and vectors_wanted(ext, len(chunks), fname):
            try:
                # contextual embeddings: "<title > section>\n<chunk>" (stored content is NOT changed)
                to_embed = []
                for c, s in zip(chunks, sections):
                    pre = context_prefix(title, s)
                    to_embed.append(f"{pre}\n{c}" if pre else c)
                vectors = embed_texts_batched(to_embed, fname)
            except Exception as e:
                log.warning(f"Embedding failed for {fname}: {e} — skipping vectors")
                vectors = [None] * len(chunks)
        else:
            vectors = [None] * len(chunks)

        for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
            needs_translation = (
                canonical_lang
                and detected_lang not in ('unknown', 'other')
                and detected_lang != canonical_lang
            )
            doc = {
                "path":        str(fpath),
                "rel_path":    rel,
                "filename":    fname,
                "ext":         ext,
                "file_hash":   fhash,
                "chunk_index": i,
                "file_mtime":  fmtime,
                "content":     chunk,
                "title":       title,
                "indexed_at":  now,
                "analytics":   compute_discourse_metrics(chunk),
            }
            if sections[i]:
                doc["section"] = sections[i]
            if collection:
                doc["collection"] = collection
            if canonical_lang:
                doc["detected_lang"] = detected_lang
                doc["translation_status"] = "pending" if needs_translation else "done"
            if vector is not None:
                doc["content_vector"] = vector
            yield {"_index": index, "_source": doc}
            if needs_translation and queue_file:
                try:
                    enqueue_translation(
                        queue_file, doc_id='', path=str(fpath),
                        chunk_index=i, source_lang=detected_lang,
                        target_lang=canonical_lang, text=chunk,
                    )
                except Exception as eq:
                    log.warning(f"Failed to enqueue translation task: {eq}")
            elif needs_translation and translation_tasks is not None:
                translation_tasks.append({
                    'path': str(fpath), 'chunk_index': i,
                    'source_lang': detected_lang, 'target_lang': canonical_lang,
                    'text': chunk,
                })
            chunk_count += 1

    log.info(f"Done: {file_count} files → {chunk_count} chunks indexed.")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Index course files into Elasticsearch")
    parser.add_argument('--es-url',      default=ES_URL,                 help=f'ES URL (default: {ES_URL})')
    parser.add_argument('--index',       default=ES_INDEX,               help=f'Index name (default: {ES_INDEX})')
    parser.add_argument('--path',        default=str(SEARCH_BASE_PATH),  help='Base path to index')
    parser.add_argument('--reset',       action='store_true',            help='Delete and recreate the index (full reindex)')
    parser.add_argument('--clean',       action='store_true',            help='Delete the index and all data (no reindex). Asks for confirmation unless --yes is also given.')
    parser.add_argument('--yes', '-y',   action='store_true',            help='Skip confirmation prompt for --clean')
    parser.add_argument('--no-vectors',    action='store_true',              help='Skip embedding generation (BM25 only)')
    parser.add_argument('--batch',         type=int, default=50,             help='Bulk batch size (default: 50)')
    parser.add_argument('--canonical-lang', default=CANONICAL_LANGUAGE,      help=f'Normalize all content to this language via translation (default: "{CANONICAL_LANGUAGE}")')
    parser.add_argument('--ext',           default='',                       help='Comma-separated file extensions to index (e.g. pdf,md,srt). Overrides config/defaults.')
    parser.add_argument('--exclude',       default='',                       help="Extra comma-separated glob patterns to skip, e.g. 'sbom*.json,*/legacy/*'")
    parser.add_argument('--no-vector-ext', default='',                       help=f"Extra comma-separated extensions indexed BM25-only (default already: {','.join(sorted(NO_VECTOR_EXTS))})")
    parser.add_argument('--max-vector-chunks', type=int, default=None,       help=f"Files with more chunks than this get no vectors (default: {_VECTOR_POLICY['max_chunks']}; 0 = no limit)")
    args = parser.parse_args()

    # Indexing-policy overrides (mutate in place → no `global` needed)
    EXCLUDE_PATTERNS.extend(_as_list(args.exclude, ','))
    NO_VECTOR_EXTS.update(e.lower().lstrip('.') for e in _as_list(args.no_vector_ext, ','))
    if args.max_vector_chunks is not None:
        _VECTOR_POLICY['max_chunks'] = max(0, args.max_vector_chunks)
    log.info(f"Policy: BM25-only ext={sorted(NO_VECTOR_EXTS)}, max vector chunks/file="
             f"{_VECTOR_POLICY['max_chunks'] or 'unlimited'}, {len(EXCLUDE_PATTERNS)} exclude patterns")

    # Override SEARCHABLE_EXTENSIONS when --ext is given
    if args.ext:
        global SEARCHABLE_EXTENSIONS
        SEARCHABLE_EXTENSIONS = {e.strip().lower().lstrip('.') for e in args.ext.split(',') if e.strip()}
        log.info(f"Extension filter: {sorted(SEARCHABLE_EXTENSIONS)}")

    base_path = Path(args.path).resolve()
    if not base_path.exists():
        log.error(f"Path does not exist: {base_path}")
        sys.exit(1)

    try:
        from elasticsearch import Elasticsearch
        from elasticsearch.helpers import bulk
    except ImportError:
        log.error("elasticsearch package not installed. Run: pip install 'elasticsearch>=8.0,<9.0'")
        sys.exit(1)

    es = Elasticsearch(args.es_url)
    if not wait_for_es(es):
        sys.exit(1)

    # ── --clean: delete index and exit (no reindex) ───────────────────────────
    if args.clean:
        if not args.reset:  # --reset already deletes; --clean alone asks
            if not args.yes:
                log.info(f"About to DELETE index '{args.index}' at {args.es_url}.")
                try:
                    answer = input(f"  Delete ALL data in '{args.index}'? [y/N]: ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    log.info("Aborted.")
                    sys.exit(0)
                if answer not in ('y', 'yes'):
                    log.info("Aborted.")
                    sys.exit(0)
            if es.indices.exists(index=args.index):
                es.indices.delete(index=args.index)
                log.info(f"Index '{args.index}' deleted.")
            else:
                log.info(f"Index '{args.index}' does not exist — nothing to delete.")
        sys.exit(0)

    create_index(es, args.index, args.reset)

    use_vectors = not args.no_vectors
    if use_vectors:
        try:
            import fastembed  # noqa: F401
        except ImportError:
            log.warning("fastembed not installed — running without vectors (BM25 only). "
                        "Install with: pip install fastembed")
            use_vectors = False

    now = datetime.now(timezone.utc).isoformat()
    canonical_lang = args.canonical_lang
    queue_file = Path(__file__).parent / 'translation_queue.jsonl' if canonical_lang else None

    if args.reset:
        # Full reindex — walk all files
        log.info(f"Full reindex: '{base_path}' → {args.es_url}/{args.index}  "
                 f"[vectors={'yes' if use_vectors else 'no'}]")
        actions = generate_actions(
            base_path, args.index, use_vectors,
            canonical_lang=canonical_lang, queue_file=queue_file,
        )
    else:
        # Incremental: only new/changed files, skip content duplicates
        log.info("Incremental mode: loading already-indexed paths & hashes…")
        path_info = get_indexed_path_info(es, args.index)
        indexed_mtimes = {p: v[0] for p, v in path_info.items()}
        indexed_hashes = get_indexed_hashes(es, args.index)
        log.info(f"  {len(indexed_mtimes)} paths, {len(indexed_hashes)} unique hashes already indexed.")

        to_delete = []
        to_index = []
        touched: dict = {}          # path → current mtime, for files whose content did not change
        skipped_dupes = 0

        for root, dirs, files in os.walk(base_path):
            _prune_dirs(dirs)
            for fname in files:
                ext = Path(fname).suffix.lstrip('.').lower()
                if ext not in SEARCHABLE_EXTENSIONS:
                    continue
                fpath = Path(root) / fname
                if is_excluded(fpath, base_path) or is_likely_minified(fpath, ext):
                    continue
                try:
                    fmtime = datetime.fromtimestamp(fpath.stat().st_mtime, timezone.utc).isoformat()
                except OSError:
                    continue
                path_str = str(fpath)

                try:
                    fhash = None        # computed lazily: hashing every unchanged file would be slow
                    # 1) Already indexed at same path — check mtime
                    if path_str in indexed_mtimes:
                        # Same date → unchanged. ANY other date (newer OR older, e.g. a file restored
                        # from a backup) → decide by content hash, not by which date is bigger.
                        if indexed_mtimes[path_str] == fmtime[:19]:
                            continue  # unchanged — skip
                        fhash = compute_file_hash(str(fpath))
                        if fhash and path_info[path_str][1] == fhash:
                            # Newer date but IDENTICAL content (copied / synced / restored files):
                            # keep its chunks. Deleting them here and then skipping the file as a
                            # "duplicate of itself" used to wipe the file from the index.
                            touched[path_str] = fmtime
                            continue
                        to_delete.append(path_str)  # really changed — re-index
                    if fhash is None:
                        fhash = compute_file_hash(str(fpath))
                except OSError:
                    fhash = ''

                # 2) Content duplicates (same content already indexed under another path)
                if fhash and fhash in indexed_hashes:
                    # Same content already indexed (different path) — skip
                    skipped_dupes += 1
                    log.debug(f"  Skipped duplicate: {fname} (hash {fhash[:12]}…)")
                    continue

                to_index.append((fpath, ext, fmtime, fhash))
                if fhash:
                    indexed_hashes.add(fhash)  # prevent later dupes in same run

        log.info(f"  {len(to_index)} new/changed files to index, "
                 f"{len(to_delete)} changed to remove, "
                 f"{skipped_dupes} duplicates skipped"
                 f"{f', {len(touched)} with a newer date but identical content kept' if touched else ''}.")

        if touched:
            refresh_mtimes(es, args.index, touched)
        if to_delete:
            delete_paths_from_index(es, args.index, to_delete)

        if not to_index:
            log.info("Nothing new to index — all files up to date.")
            return

        log.info(f"Indexing {len(to_index)} file(s)…  [vectors={'yes' if use_vectors else 'no'}]")
        actions = generate_actions(
            base_path, args.index, use_vectors, files_list=to_index,
            canonical_lang=canonical_lang, queue_file=queue_file,
        )

    success, failed = bulk(
        es,
        actions,
        chunk_size=args.batch,
        raise_on_error=False,
        stats_only=False,
    )
    if failed:
        log.warning(f"{len(failed)} documents failed to index.")
    log.info(f"Indexed {success} documents successfully.")

    # ── Post-indexing translation ─────────────────────────────────────────────
    if canonical_lang and TRANSLATION_MODE != 'disabled':
        if TRANSLATION_MODE == 'background':
            log.info("Translation queued. Run: python ai_translation.py --once  to process.")
        elif TRANSLATION_MODE == 'inline':
            log.info("Running inline translation (TRANSLATION_MODE=inline)…")
            try:
                from ai_translation import TranslationWorker
                worker = TranslationWorker(
                    es_url=args.es_url,
                    queue_file=queue_file,
                    index=args.index,
                    canonical_lang=canonical_lang,
                    once=True,
                )
                worker._running = True
                try:
                    worker._process_once()
                finally:
                    worker._running = False
                status = worker.get_status()
                log.info(
                    f"Translation complete: done={status['done']} "
                    f"pending={status['pending']} failed={status['failed']}"
                )
            except Exception as e:
                log.warning(f"Inline translation failed: {e}")

if __name__ == '__main__':
    main()
