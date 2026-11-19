# Course Catalog Backend Server

## Overview

`catalog_server.py` is a lightweight Python Flask server that powers the full-text search and file-viewing capabilities of the Course Catalog HTML frontend.

It provides three core capabilities:

- **Full-text search** — walks your course directory tree, extracts text from all supported file formats (PDF, DOCX, XLSX, PPTX, plain text, code, etc.), and returns ranked matches with surrounding context.
- **File serving** — delivers raw file bytes to the browser so users can download or open course materials directly.
- **Syntax-highlighted text viewing** — extracts and returns the text content of any file as JSON, tagged with the appropriate language identifier for syntax highlighting in the frontend viewer.

The server runs entirely locally (or on a shared machine on your LAN) and does not require any database or cloud service. All search is done live by walking the file system.

---

## Requirements

### Python version

Python **3.9 or newer** is required.

### Required packages

These must be installed for the server to start:

```bash
pip install flask flask-cors
```

### Optional packages (recommended)

These packages enable richer text extraction from binary formats. The server starts and runs without them, but search will return no results for the corresponding file types.

| Package | Enables | Install |
|---|---|---|
| `PyMuPDF` | PDF text extraction (fast, accurate) | `pip install PyMuPDF` |
| `pdfminer.six` | PDF text extraction (fallback) | `pip install pdfminer.six` |
| `pdfplumber` | PDF text extraction (second fallback) | `pip install pdfplumber` |
| `python-docx` | DOCX (Word) extraction | `pip install python-docx` |
| `openpyxl` | XLSX (Excel) extraction | `pip install openpyxl` |
| `python-pptx` | PPTX (PowerPoint) extraction | `pip install python-pptx` |
| `striprtf` | RTF extraction | `pip install striprtf` |
| `beautifulsoup4` | Cleaner HTML text extraction | `pip install beautifulsoup4` |
| `chardet` | Encoding detection for text files | `pip install chardet` |

Install all optional packages at once:

```bash
pip install PyMuPDF python-docx openpyxl python-pptx striprtf beautifulsoup4 chardet
```

---

## Quick Start

**Step 1 — Configure `env.py`**

Open `env.py` and set `SEARCH_BASE_PATH` to the root directory of your course files:

```python
SEARCH_BASE_PATH = "C:/Users/Me/Courses"          # Windows local
SEARCH_BASE_PATH = "//fileserver/Schulungen"       # Windows network share
SEARCH_BASE_PATH = "/home/me/courses"              # macOS / Linux
```

**Step 2 — Install dependencies**

```bash
pip install flask flask-cors
# Optional but recommended:
pip install PyMuPDF python-docx openpyxl python-pptx striprtf beautifulsoup4 chardet
```

**Step 3 — Run the server**

```bash
python catalog_server.py
```

The server starts on `http://0.0.0.0:5000`. Open your Course Catalog HTML file in a browser — search will now be powered by the backend.

---

## Configuration (`env.py`)

All settings live in `env.py` next to `catalog_server.py`. Edit this file before starting the server.

| Setting | Default | Description |
|---|---|---|
| `SEARCH_BASE_PATH` | `"//fileserver/Schulungen"` | Root directory scanned for course files. Only files within this path can be served (security boundary). |
| `HOST` | `"0.0.0.0"` | Network interface to bind. Use `"127.0.0.1"` to restrict access to the local machine only. |
| `PORT` | `5000` | TCP port the server listens on. Change if 5000 is already in use. |
| `DEBUG` | `False` | Enables Flask debug mode (auto-reload, verbose errors). Use only during development. |
| `MAX_RESULTS` | `50` | Maximum number of matching files returned per search query. Cap at 200 (hard limit). |
| `MAX_MATCHES_PER_FILE` | `5` | Maximum number of matching lines shown per file in results. |
| `CONTEXT_LINES` | `2` | Number of lines shown before and after each matching line for context. |
| `MAX_FILE_SIZE_MB` | `50` | Files larger than this size (in MB) are skipped during search. |
| `SEARCHABLE_EXTENSIONS` | (see below) | Set of file extensions (without dot) that will be searched. |

### Default searchable extensions

```python
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
```

---

## Running the Server

### Basic start (uses `env.py` settings)

```bash
python catalog_server.py
```

### Override settings via command-line arguments

Command-line arguments take precedence over `env.py`.

```bash
# Custom port
python catalog_server.py --port 8080

# Custom base path
python catalog_server.py --path "C:/Training/Materials"

# Custom host (localhost only)
python catalog_server.py --host 127.0.0.1

# All options together
python catalog_server.py --host 0.0.0.0 --port 5000 --path "//server/share" --debug
```

### Windows

```bat
REM From the _Course_catalog_Generator directory:
python catalog_server.py

REM Or with a network path:
python catalog_server.py --path "//fileserver/Schulungen"

REM Run in background (keep terminal open or use pythonw):
start /B python catalog_server.py
```

### macOS / Linux

```bash
# Foreground
python3 catalog_server.py

# Background
python3 catalog_server.py &

# With nohup (survives terminal close)
nohup python3 catalog_server.py > catalog_server.log 2>&1 &
```

### Available CLI arguments

| Argument | Description |
|---|---|
| `--host HOST` | Network interface to bind (default: from `env.py`) |
| `--port PORT` | Port number (default: from `env.py`) |
| `--path PATH` | Base directory for search (default: from `env.py`) |
| `--debug` | Enable Flask debug mode |

---

## API Reference

All endpoints return JSON (except `/api/file` which returns raw file bytes). The server also injects CORS headers on every response, so the HTML frontend can call it from any origin.

### `GET /api/search`

Full-text search across all indexed files.

**Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `q` | Yes | Search query string (minimum 2 characters) |
| `limit` | No | Maximum results to return (default: `MAX_RESULTS`, hard cap: 200) |

**Example request:**

```
GET http://localhost:5000/api/search?q=ELO+Installation&limit=20
```

**Response schema:**

```json
{
  "query": "ELO Installation",
  "total": 12,
  "results": [
    {
      "path": "//fileserver/Schulungen/ELO/01_Install/guide.pdf",
      "file": "guide.pdf",
      "rel_path": "ELO/01_Install/guide.pdf",
      "type": "pdf",
      "match_count": 3,
      "matches": [
        {
          "line": 42,
          "context": "... prerequisites ... ELO Installation requires Windows Server 2019 ... disk space ...",
          "highlight": "ELO Installation"
        }
      ]
    }
  ]
}
```

**Error response (query too short):**

```json
{
  "error": "Query too short",
  "total": 0,
  "results": []
}
```

---

### `GET /api/file`

Serves raw file bytes. The browser will open or download the file based on its MIME type.

**Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `path` | Yes | URL-encoded absolute path to the file |

**Example request:**

```
GET http://localhost:5000/api/file?path=%2F%2Ffileserver%2FSchulungen%2FELO%2Fguide.pdf
```

**Response:** Raw file bytes with appropriate `Content-Type` header (e.g., `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`).

**Error codes:**
- `400` — No path provided
- `404` — File not found or outside `SEARCH_BASE_PATH`
- `500` — Server error reading the file

---

### `GET /api/file-text`

Returns the extracted text content of a file as JSON, along with a language identifier for syntax highlighting.

**Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `path` | Yes | URL-encoded absolute path to the file |

**Example request:**

```
GET http://localhost:5000/api/file-text?path=%2F%2Ffileserver%2FSchulungen%2Fscript.py
```

**Response schema:**

```json
{
  "content": "#!/usr/bin/env python3\n...",
  "language": "python",
  "path": "//fileserver/Schulungen/script.py",
  "size": 4096
}
```

The `language` field maps to standard identifiers used by syntax highlighting libraries (e.g., Highlight.js, Prism): `javascript`, `typescript`, `python`, `java`, `cpp`, `csharp`, `go`, `rust`, `ruby`, `php`, `bash`, `sql`, `json`, `xml`, `yaml`, `toml`, `css`, `html`, `markdown`, `plaintext`.

Content is truncated at 500,000 characters with a `[... truncated ...]` marker appended.

**Error codes:**
- `400` — No path provided
- `404` — File not found
- `500` — Server error during extraction

---

### `GET /api/health`

Health check endpoint. Returns `200 OK` when the server is running.

**Response:**

```json
{
  "status": "ok",
  "base_path": "//fileserver/Schulungen"
}
```

---

### `GET /`

Lists available endpoints.

**Response:**

```json
{
  "service": "Course Catalog Backend",
  "endpoints": [
    "/api/search?q=query",
    "/api/file?path=...",
    "/api/file-text?path=...",
    "/api/health"
  ]
}
```

---

## Integration with Course Catalog Generator

The Course Catalog HTML frontend (generated by `course_catalog_generator.py`) communicates with this backend via a `BACKEND_URL` constant embedded in the generated HTML file.

### How the frontend uses the backend

1. **Search box** — When the user types a query and presses Enter or clicks the search button, the frontend sends a `GET /api/search?q=<query>` request. Results are displayed in a panel below the catalog, grouped by file with match context snippets.

2. **Open file** — Each search result includes an "Open" button that triggers `GET /api/file?path=<encoded_path>`, causing the browser to open or download the file.

3. **View text** — Each search result also includes a "View" button that calls `GET /api/file-text?path=<encoded_path>` and displays the extracted text in a modal viewer with syntax highlighting.

4. **Health check** — On page load, the frontend calls `GET /api/health`. If the backend is not reachable, a notice is shown: "Search backend not available — start catalog_server.py to enable full-text search."

### Setting `BACKEND_URL`

The `BACKEND_URL` constant in the generated HTML defaults to `http://localhost:5000`. If you run the server on a different port or host, regenerate the catalog HTML or manually update the `BACKEND_URL` value near the top of the `<script>` block in the generated file:

```javascript
const BACKEND_URL = "http://localhost:5000";  // Change if needed
```

### Graceful degradation

The frontend works fully without the backend for browsing the catalog (folder tree, file list, metadata). The backend is only required for full-text search and inline file viewing. If the backend is not running, the search input is disabled with an explanatory message.

---

## Supported File Formats

| Format | Extension(s) | Library used | Notes |
|---|---|---|---|
| PDF | `.pdf` | PyMuPDF (primary) → pdfminer.six → pdfplumber | Requires at least one PDF library installed |
| Word Document | `.docx` | python-docx | Extracts paragraphs and table cells |
| Word Document (legacy) | `.doc` | docx2txt (fallback: plain text read) | Binary `.doc` support is limited |
| Excel Spreadsheet | `.xlsx`, `.xls` | openpyxl | Extracts cell values, labeled by sheet |
| PowerPoint | `.pptx`, `.ppt` | python-pptx | Extracts text from all shapes per slide |
| Rich Text Format | `.rtf` | striprtf (fallback: regex strip) | Regex fallback works for most RTFs |
| HTML | `.html`, `.htm` | beautifulsoup4 (fallback: regex) | Tag stripping; BeautifulSoup gives cleaner output |
| XML | `.xml` | built-in (regex tag strip) | No library needed |
| Plain text / code | `.txt`, `.md`, `.csv`, `.json`, `.py`, `.js`, `.ts`, `.css`, `.sql`, `.sh`, `.yaml`, `.toml`, `.ini`, `.log`, etc. | built-in | Encoding auto-detected via chardet if installed |
| Subtitles | `.srt`, `.vtt` | built-in (plain text) | Searched as plain text including timestamps |

---

## Security Notes

### Path traversal protection

The `/api/file` endpoint validates that all requested paths resolve to a location within `SEARCH_BASE_PATH` before serving the file. Requests for files outside this boundary return `404`. This prevents path traversal attacks.

Note: Python's `Path.resolve()` may not follow UNC network paths (`//server/share`) on all systems. For network paths, the server falls back to checking that the file exists — ensure `SEARCH_BASE_PATH` is set conservatively on publicly accessible machines.

### Running on localhost

By default, `HOST = "0.0.0.0"` makes the server reachable from any machine on the network. If you intend to use the server only on your own machine, set `HOST = "127.0.0.1"` in `env.py`. This binds the server to the loopback interface only.

### CORS

CORS (Cross-Origin Resource Sharing) is enabled for all origins via `flask-cors`. This is intentional — the Course Catalog HTML file may be opened from the local filesystem (`file://`) or from a different port, and must be able to call the backend API. Do not run this server on a public network without adding authentication or restricting CORS origins.

### No authentication

The server does not implement authentication or authorization. It is designed for use on a trusted local network or machine. Do not expose it to the public internet.

---

## Troubleshooting

### "Flask not installed" on startup

```
ERROR: Flask not installed. Run: pip install flask flask-cors
```

Run the install command shown in the error:

```bash
pip install flask flask-cors
```

If you have multiple Python installations, make sure you are using the correct `pip`:

```bash
python -m pip install flask flask-cors
```

---

### Search returns no results for PDF/DOCX/XLSX

The relevant extraction library is not installed. Check the server log — you will see a message like:

```
WARNING - No PDF extraction library available for /path/to/file.pdf
```

Install the missing library:

```bash
pip install PyMuPDF          # for PDF
pip install python-docx      # for DOCX
pip install openpyxl         # for XLSX
pip install python-pptx      # for PPTX
```

---

### "Path does not exist" warning on startup

```
WARNING: Path '//fileserver/Schulungen' does not exist.
```

The path in `env.py` is not accessible from the machine running the server. Check:

- The network share is mounted / accessible (`net use` on Windows, `mount` on Linux/macOS).
- The path uses forward slashes (`//server/share`) or the correct OS convention.
- You have read permissions on the directory.

The server will still start and can be queried, but all searches will return zero results until the path is accessible.

---

### Port already in use

```
OSError: [Errno 98] Address already in use
```

Another process is using port 5000. Either stop the other process or change the port:

```bash
python catalog_server.py --port 5001
```

On macOS, port 5000 is used by AirPlay Receiver in macOS Monterey and later. Disable it in **System Settings > General > AirDrop & Handoff**, or use a different port.

---

### CORS errors in the browser console

```
Access to fetch at 'http://localhost:5000/api/search' from origin 'null' has been blocked by CORS policy
```

This should not happen with `flask-cors` installed and configured. If it does:

1. Confirm `flask-cors` is installed: `pip install flask-cors`
2. Confirm the server restarted after installing `flask-cors`
3. Check that the server is actually running: open `http://localhost:5000/api/health` in the browser

If the browser opens the HTML file from the filesystem (`file://` origin), some browsers block all `fetch()` calls regardless of CORS headers. In that case, serve the HTML file via a local HTTP server:

```bash
# Python built-in server
python -m http.server 8000
# Then open http://localhost:8000/course_catalog.html
```

---

### Search is slow on large directories

The server performs a full directory walk and file extraction for every search query — there is no persistent index. On large directories with thousands of files, searches may take several seconds.

To improve performance:

- Reduce `SEARCHABLE_EXTENSIONS` to only the formats you actually need.
- Lower `MAX_FILE_SIZE_MB` to skip very large files.
- Lower `MAX_RESULTS` to stop walking the tree earlier once enough results are found.
- For very large collections, consider running the server on the machine that hosts the files (avoids network I/O for extraction).

---

### Encoding errors in text files

If search results show garbled text, install `chardet` for automatic encoding detection:

```bash
pip install chardet
```

Without `chardet`, the server assumes UTF-8, which may produce incorrect output for files encoded in Windows-1252 (common for older German documents), Latin-1, or other legacy encodings.

---

## Docker Deployment

Running via Docker is the easiest way to get the full stack running on any operating system. The Docker image bundles all Python dependencies and the multilingual embedding model so no local Python installation is needed.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS) or Docker Engine (Linux)
- At least **2 GB free RAM** (Elasticsearch needs ~1 GB)
- At least **2 GB free disk** (image layers + ES data)

### Step 1 — Configure `.env`

Copy the example environment file and edit the path to your courses:

```bash
cp .env.example .env
```

Open `.env` and set `COURSES_PATH` to the directory containing your course materials:

```bash
# Windows (Docker Desktop with WSL2) — use forward slashes
COURSES_PATH=C:/Users/Me/Videos/My Courses

# Linux / macOS
COURSES_PATH=/home/me/courses

# Windows network share
COURSES_PATH=//fileserver/Schulungen/ELO
```

All other settings in `.env` have sensible defaults (see `.env.example`).

### Step 2 — Build and start

```bash
# Build the image and start backend + Elasticsearch in background
docker-compose up -d --build

# Follow logs while starting up
docker-compose logs -f backend
```

The first build downloads and caches the multilingual embedding model (~400 MB) — this is a one-time operation. Subsequent builds are fast.

### Step 3 — Index your files into Elasticsearch

After the services are running, run the indexer to populate the ES index:

```bash
docker-compose run --rm indexer
```

This walks `/courses` inside the container (your `COURSES_PATH` mounted read-only), extracts text from every supported file, generates multilingual embeddings, and bulk-indexes everything into Elasticsearch.

> **Tip:** Re-run with `--reset` to rebuild the index from scratch:
> ```bash
> docker-compose run --rm indexer python es_indexer.py --reset
> ```

### Step 4 — Open the frontend

The backend is now available at **http://localhost:5000**. Open your `course_catalog.html` file in a browser and search as usual. The ES-powered endpoint is also available at `http://localhost:5000/api/es-search`.

### Useful Docker commands

```bash
# View running containers
docker-compose ps

# View backend logs
docker-compose logs -f backend

# Stop all services
docker-compose down

# Stop and delete Elasticsearch data volume (full reset)
docker-compose down -v

# Start with Kibana (ES management UI on http://localhost:5601)
docker-compose --profile kibana up -d

# Rebuild after code changes
docker-compose up -d --build backend
```

### Architecture

```
┌──────────────────────────────────────────────────────┐
│  Docker network                                       │
│                                                       │
│  ┌──────────────────┐      ┌──────────────────────┐  │
│  │  backend:5000     │─────▶│  elasticsearch:9200  │  │
│  │  (Flask + Python) │      │  (ES 8.12, single)   │  │
│  └──────────────────┘      └──────────────────────┘  │
│          │                          │                 │
│          │                          │                 │
└──────────┼──────────────────────────┼─────────────────┘
           │                          │
    localhost:5000               127.0.0.1:9200
    (browser / frontend)         (indexer / Kibana)

  /courses ──► mounted read-only in backend and indexer
```

---

## Elasticsearch Search (`/api/es-search`)

Once files are indexed with `es_indexer.py`, the `/api/es-search` endpoint provides fast, language-aware, and optionally semantic search.

### Endpoint

```
GET /api/es-search
```

### Parameters

| Parameter | Required | Description |
|---|---|---|
| `q` | Yes | Search query (minimum 2 characters) |
| `lang` | No | Language for analyzer boosting: `de`, `es`, `en`, or empty |
| `semantic` | No | `1` to enable kNN semantic search (requires indexed vectors) |
| `limit` | No | Max results (default: `MAX_RESULTS`, hard cap: 200) |

### Examples

```bash
# Basic full-text search
curl "http://localhost:5000/api/es-search?q=ELO+Installation"

# German language boosted search
curl "http://localhost:5000/api/es-search?q=Benutzer+Verwaltung&lang=de"

# Spanish semantic search (kNN)
curl "http://localhost:5000/api/es-search?q=abreviatura&lang=es&semantic=1"

# Hybrid BM25 + semantic
curl "http://localhost:5000/api/es-search?q=workflow&lang=en&semantic=1&limit=20"
```

### Response

Same structure as `/api/search`:

```json
{
  "query": "ELO Installation",
  "total": 5,
  "results": [
    {
      "path": "/courses/ELO/01_Install/guide.pdf",
      "file": "guide.pdf",
      "rel_path": "ELO/01_Install/guide.pdf",
      "type": "pdf",
      "score": 12.34,
      "match_count": 2,
      "matches": [
        {
          "line": 3,
          "context": "...ELO Installation requires Windows Server 2019...",
          "highlight": "ELO Installation",
          "score": 12.34
        }
      ]
    }
  ],
  "mode": {"lang": "de", "semantic": false, "engine": "elasticsearch"}
}
```

### How it works

| Mode | Behavior |
|---|---|
| **BM25 only** (`semantic=0`) | Full-text multi-match with language-specific stemmer (German / Spanish / English). Tolerates typos via `fuzziness: AUTO`. |
| **Semantic** (`semantic=1`) | Adds kNN approximate nearest-neighbor search over 384-dimensional multilingual embeddings (paraphrase-multilingual-MiniLM-L12-v2). Scores are combined with BM25 automatically by Elasticsearch. |

### Language analyzers

When `lang` is provided, the matching content field is boosted (weight ×3):

| `lang` | Analyzer | Effect |
|---|---|---|
| `de` | German | Stemming (lernen→lern), umlaut normalization (ü→u), stopwords |
| `es` | Spanish | Stemming (aprender→aprend), accent folding (á→a), stopwords |
| `en` | English | Stemming (learning→learn), stopwords |
| *(empty)* | Standard | Unicode tokenization, no stemming |

### ES error (`503 Service Unavailable`)

If ES is not reachable, `/api/es-search` returns:

```json
{"error": "Elasticsearch is not configured or not reachable. Set ES_URL in env.py and ensure ES is running."}
```

To use ES without Docker (e.g. local ES installation), set `ES_URL` in `env.py`:

```python
ES_URL = "http://127.0.0.1:9200"
```

---

## Elasticsearch Indexer (`es_indexer.py`)

`es_indexer.py` is a standalone script that populates the ES index. Run it once before using `/api/es-search`, and re-run whenever course files change.

### Usage (local, without Docker)

```bash
# Install ES client + embedding library
pip install "elasticsearch>=8.0,<9.0" fastembed

# Index with defaults (reads ES_URL and path from env.py)
python es_indexer.py

# Re-index from scratch
python es_indexer.py --reset

# Custom settings
python es_indexer.py --es-url http://127.0.0.1:9200 --path "C:/Courses" --index my_courses

# Skip embeddings (BM25 only — much faster, smaller index)
python es_indexer.py --no-vectors
```

### Usage (inside Docker)

```bash
# One-shot container (defined in docker-compose.yml)
docker-compose run --rm indexer

# Re-index from scratch
docker-compose run --rm indexer python es_indexer.py --reset

# Skip vectors
docker-compose run --rm indexer python es_indexer.py --no-vectors
```

### CLI arguments

| Argument | Default | Description |
|---|---|---|
| `--es-url URL` | from `env.py` | Elasticsearch URL |
| `--index NAME` | `course_catalog` | ES index name |
| `--path PATH` | from `env.py` | Directory to index |
| `--reset` | off | Delete and recreate the index before indexing |
| `--no-vectors` | off | Skip embedding generation (BM25 only) |
| `--batch N` | `50` | Bulk batch size |

### Index structure

Each file is split into ~1200-character chunks. Each chunk becomes one ES document:

| Field | Type | Description |
|---|---|---|
| `path` | keyword | Absolute file path |
| `rel_path` | keyword | Path relative to `SEARCH_BASE_PATH` |
| `filename` | keyword | File name |
| `ext` | keyword | File extension |
| `chunk_index` | integer | Chunk number within the file (0-based) |
| `content` | text (+ .de/.es/.en sub-fields) | Chunk text content |
| `content_vector` | dense_vector (384 dims) | Multilingual embedding |
| `indexed_at` | date | Indexing timestamp |

---

## Running Without Docker

If you prefer not to use Docker, you can run Elasticsearch and the backend natively. You need:

1. **Java 17+** (required by Elasticsearch)
2. **Elasticsearch 8.x** — [download](https://www.elastic.co/downloads/elasticsearch)
3. Python 3.9+ with dependencies from `requirements.txt`

```bash
# Start ES (disable security for local dev)
# In elasticsearch.yml: xpack.security.enabled: false
./bin/elasticsearch

# Install Python deps
pip install -r requirements.txt

# Set ES_URL in env.py
# ES_URL = "http://127.0.0.1:9200"

# Index files
python es_indexer.py

# Start backend
python catalog_server.py
```
