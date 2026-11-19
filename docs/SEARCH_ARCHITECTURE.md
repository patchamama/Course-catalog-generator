# Multilingual Document Search Architecture: From Static Keyword Matching to AI-Augmented Retrieval and Discourse Analysis

## Arquitectura de Búsqueda Documental Multilingüe: Del Emparejamiento Léxico Estático a la Recuperación Aumentada por IA y el Análisis del Discurso

---

**Draft version · Borrador** — _Course Catalog Generator Project_
**Date / Fecha:** 2026-03
**Status:** Working paper / Documento de trabajo

---

## Abstract

This paper presents a layered architecture for document retrieval and analysis systems applied to heterogeneous multilingual corpora (Markdown, PDF, HTML, SRT subtitles, plain text). We describe seven progressive capability levels — from clientside static search to cloud-augmented Retrieval-Augmented Generation (RAG) — and analyze the trade-offs between computational cost, infrastructure complexity, and retrieval quality at each level. We further propose a language-normalization strategy based on a single canonical storage language with a background translation queue powered by a lightweight local model (TranslateGemma), enabling homogeneous indexing across multilingual content. We then examine how large language models can be grounded on retrieved document context to produce elaborated, citation-backed answers rather than hallucinated responses. Finally, we outline a corpus analytics framework for automated document profiling: summarization, gendered language statistics, narrative person distribution, verb tense analysis, lexical richness, and named entity extraction. The architecture is designed to be modular and hardware-aware, supporting CPU-only laptops, Apple Silicon (Metal GPU), and NVIDIA CUDA environments with adaptive model selection.

---

## Resumen

Este artículo presenta una arquitectura por capas para sistemas de recuperación y análisis documental aplicados a corpus multilingües heterogéneos (Markdown, PDF, HTML, subtítulos SRT, texto plano). Se describen siete niveles progresivos de capacidad — desde la búsqueda estática en el cliente hasta la Generación Aumentada por Recuperación (RAG) con soporte en la nube — y se analizan los compromisos entre coste computacional, complejidad de infraestructura y calidad de recuperación en cada nivel. Se propone además una estrategia de normalización lingüística basada en un único idioma canónico de almacenamiento con una cola de traducción en segundo plano impulsada por un modelo local ligero (TranslateGemma), que permite una indexación homogénea de contenidos multilingües. Se examina cómo los grandes modelos de lenguaje pueden anclarse en el contexto documental recuperado para producir respuestas elaboradas con citas en lugar de alucinaciones. Por último, se esboza un marco de análisis de corpus para la creación automatizada de perfiles documentales: resúmenes, estadísticas de lenguaje con perspectiva de género, distribución de la persona narrativa, análisis de tiempos verbales, riqueza léxica y extracción de entidades nombradas. La arquitectura está diseñada para ser modular y consciente del hardware, admitiendo portátiles solo-CPU, Apple Silicon (GPU Metal) y entornos NVIDIA CUDA con selección adaptativa de modelos.

---

## Table of Contents / Índice

1. [Introduction / Introducción](#1-introduction--introducción)
2. [Architecture Levels / Niveles de Arquitectura](#2-architecture-levels--niveles-de-arquitectura)
   - L0 — Static Client-Side Search
   - L1 — Python Backend with Cached Full-Text Search
   - L2 — Elasticsearch BM25 with Language Analyzers
   - L3 — Semantic kNN Vector Search
   - L4 — Hybrid Search (BM25 + kNN)
   - L5 — Cross-Lingual Normalization with Translation Pipeline
   - L6 — Local RAG with Ollama
   - L7 — Cloud-Augmented RAG (Claude / Gemini API)
3. [Language-Agnostic Storage Strategy](#3-language-agnostic-storage-strategy--estrategia-de-almacenamiento-agnóstico-de-idioma)
4. [Background Translation Pipeline](#4-background-translation-pipeline--cola-de-traducción-en-segundo-plano)
5. [RAG: Grounding LLMs on Retrieved Context](#5-rag-grounding-llms-on-retrieved-context--anclando-los-llm-en-el-contexto-recuperado)
6. [Context Enrichment for Better LLM Answers](#6-context-enrichment-for-better-llm-answers--enriquecimiento-de-contexto-para-mejores-respuestas)
7. [Hardware-Aware Model Selection](#7-hardware-aware-model-selection--selección-de-modelo-consciente-del-hardware)
8. [Corpus Analytics and Document Profiling](#8-corpus-analytics-and-document-profiling--análisis-de-corpus-y-perfilado-documental)
9. [System Integration Map](#9-system-integration-map--mapa-de-integración-del-sistema)
10. [Evaluation Metrics](#10-evaluation-metrics--métricas-de-evaluación)
11. [Future Work / Trabajo Futuro](#11-future-work--trabajo-futuro)
12. [References / Referencias](#12-references--referencias)

---

## 1. Introduction / Introducción

### EN

Modern knowledge management systems frequently operate over heterogeneous, multilingual document collections. Corporate training repositories, educational platforms, and digital libraries accumulate content in multiple formats (PDF, Markdown, HTML, video subtitles) and multiple languages simultaneously. Traditional keyword-based search systems fail in three fundamental ways: (1) they cannot bridge lexical gaps across languages; (2) they retrieve only documents containing exact query terms, missing semantically related material; and (3) they return document fragments rather than synthesized, contextualized answers.

This paper describes the architecture of a document retrieval and analysis system built around a course catalog generator. Starting from a zero-infrastructure baseline (a static HTML page with client-side JavaScript search), the system evolves through seven capability levels to a full RAG pipeline grounded on a hybrid Elasticsearch index, with an optional language-normalization layer and corpus analytics capabilities.

The design philosophy is _progressive enhancement_: each layer is independently deployable, and the system gracefully degrades when infrastructure components (Docker, Elasticsearch, Ollama) are unavailable.

### ES

Los sistemas modernos de gestión del conocimiento operan frecuentemente sobre colecciones documentales heterogéneas y multilingües. Los repositorios de formación corporativa, las plataformas educativas y las bibliotecas digitales acumulan contenido en múltiples formatos (PDF, Markdown, HTML, subtítulos de vídeo) y en varios idiomas simultáneamente. Los sistemas de búsqueda tradicionales basados en palabras clave fallan de tres formas fundamentales: (1) no pueden salvar las diferencias léxicas entre idiomas; (2) solo recuperan documentos que contienen los términos exactos de la consulta, ignorando el material semánticamente relacionado; y (3) devuelven fragmentos de documentos en lugar de respuestas sintetizadas y contextualizadas.

Este artículo describe la arquitectura de un sistema de recuperación y análisis documental construido en torno a un generador de catálogos de cursos. Partiendo de una línea base sin infraestructura (una página HTML estática con búsqueda JavaScript en el cliente), el sistema evoluciona a través de siete niveles de capacidad hasta un pipeline RAG completo anclado en un índice híbrido de Elasticsearch, con una capa opcional de normalización lingüística y capacidades de análisis de corpus.

La filosofía de diseño es la _mejora progresiva_: cada capa es desplegable de forma independiente, y el sistema degrada elegantemente cuando los componentes de infraestructura (Docker, Elasticsearch, Ollama) no están disponibles.

---

## 2. Architecture Levels / Niveles de Arquitectura

### L0 — Static Client-Side Search

**Complexity:** Minimal · **Infrastructure:** None · **Languages:** Single index language

The baseline implementation generates a single `course_catalog.html` file containing all document metadata pre-rendered at build time. Search is performed entirely in the browser using JavaScript substring matching over the DOM. No server, no database, no network requests after initial page load.

**Strengths:** Zero deployment friction, works offline, instant response.
**Limitations:** No relevance ranking, no cross-lingual capability, no full-text content search (metadata only), performance degrades above ~10,000 entries.

```
[Build time]  course_catalog_generator.py  →  course_catalog.html
[Runtime]     Browser JS  →  DOM substring filter
```

---

### L1 — Python Backend with Cached Full-Text Search

**Complexity:** Low · **Infrastructure:** Python process · **Languages:** Detected per-file

`catalog_server.py` provides a REST API over a pre-built in-memory index of document chunks. On startup, it walks the content directory, extracts text (PDF via PyMuPDF, DOCX via python-docx, etc.), and stores a list of `{path, content, chunk_index}` records. Queries are resolved by iterating this list with fuzzy substring matching and optional soundex normalization for phonetic search.

A 30-second TTL cache on query results reduces CPU load for repeated queries. Language detection (`langdetect`) enables language-aware scoring.

**Strengths:** Simple, no external dependencies, supports 20+ file formats.
**Limitations:** In-memory only (no persistence), no semantic understanding, restarts lose index, poor performance on corpora > 50,000 chunks.

```
[Startup]   Walk filesystem → extract text → RAM index
[Query]     HTTP GET /api/search?q=... → iterate RAM index → return matches
[Cache]     TTL=30s LRU cache on (query, lang) key
```

---

### L2 — Elasticsearch BM25 with Language Analyzers

**Complexity:** Medium · **Infrastructure:** Docker + Elasticsearch 8.x

Elasticsearch's BM25 (Best Match 25) probabilistic ranking model replaces linear iteration. Documents are indexed with language-specific analyzers:

- **`english_search`**: lowercase → stop words (EN) → Porter stemmer → synonym_graph (updateable)
- **`spanish_search`**: lowercase → stop words (ES) → Snowball stemmer → synonym_graph (updateable)
- **`german_search`**: lowercase → stop words (DE) → German normalization → Snowball → synonym_graph

The `synonym_graph` filter with `updateable: true` operates at search time only, enabling synonym expansion without re-indexing. Synonym files are managed in `synonyms/synonyms_{en,es,de}.txt`.

**Incremental indexing:** Each document stores `file_mtime` (ISO 8601 UTC). On subsequent runs, the indexer compares filesystem mtimes to indexed mtimes; only new or modified files are re-indexed.

**BM25 relevance formula:**

$$\text{score}(D, Q) = \sum_{i=1}^{n} \text{IDF}(q_i) \cdot \frac{f(q_i, D) \cdot (k_1 + 1)}{f(q_i, D) + k_1 \cdot (1 - b + b \cdot \frac{|D|}{\text{avgdl}})}$$

where $k_1 = 1.2$, $b = 0.75$ (Elasticsearch defaults).

**Strengths:** Persistent storage, fast ranked retrieval, multi-language, synonym expansion, SRT subtitle indexing with timestamps.
**Limitations:** Still term-based; query "machine learning" does not match "maschinelles Lernen" across languages.

---

### L3 — Semantic kNN Vector Search

**Complexity:** Medium-High · **Infrastructure:** Docker + ES + fastembed

Each document chunk is encoded into a dense vector using `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions, ONNX Runtime via `fastembed`). This model was trained on 50+ languages with contrastive learning, mapping semantically equivalent sentences to nearby points in the vector space regardless of language.

```
"authentication"       →  [0.231, -0.412, 0.089, ...]
"Authentifizierung"    →  [0.228, -0.408, 0.091, ...]  ← cosine sim ≈ 0.97
"autenticación"        →  [0.235, -0.415, 0.087, ...]  ← cosine sim ≈ 0.96
```

Elasticsearch stores these as `dense_vector` fields with HNSW (Hierarchical Navigable Small World) approximate nearest-neighbor index.

**Query flow:**
1. Encode query string with same model → query vector
2. ES `knn` query: find top-k vectors by cosine similarity
3. Return document chunks, ranked by semantic similarity

**Strengths:** True cross-lingual retrieval, catches paraphrases and synonyms not in thesaurus files, works with technical vocabulary.
**Limitations:** Requires ~400 MB model download, slower indexing (embedding generation is CPU/GPU-bound), approximate (not exact) similarity.

---

### L4 — Hybrid Search (BM25 + kNN)

**Complexity:** Medium-High · **Infrastructure:** Same as L3

Hybrid search combines BM25 lexical scores with kNN semantic scores using Elasticsearch's Reciprocal Rank Fusion (RRF):

$$\text{RRF}(d) = \sum_{r \in R} \frac{1}{k + r(d)}$$

where $k = 60$ (constant), $r(d)$ is the rank of document $d$ in result set $r$.

This fusion captures both: (1) exact term matches valued by BM25, and (2) semantic relatedness valued by kNN. Empirically, hybrid search outperforms either method alone on mixed queries.

**Query modes available:**
- `mode=bm25` — keyword only (fast, L2)
- `mode=semantic` — kNN only (cross-lingual, L3)
- `mode=hybrid` — RRF fusion (best quality, L4)

---

### L5 — Cross-Lingual Normalization with Translation Pipeline

**Complexity:** High · **Infrastructure:** Docker + ES + Ollama (TranslateGemma)

See Section 3 for full details. At this level, all documents are translated to a single canonical language (configurable: EN/ES/DE) at index time using a local translation model. Queries are also translated before search. This creates a fully language-agnostic retrieval system.

---

### L6 — Local RAG with Ollama

**Complexity:** High · **Infrastructure:** Docker + ES + Ollama

See Section 5 for full details. Retrieved document chunks are passed as context to a locally-running LLM (via Ollama) to generate synthesized, citation-backed answers.

---

### L7 — Cloud-Augmented RAG (Claude / Gemini API)

**Complexity:** High · **Infrastructure:** Docker + ES + Internet + API key

Identical to L6 but uses an external LLM API (Anthropic Claude, Google Gemini) for generation. Higher answer quality, supports larger context windows (200K tokens for Claude 3), but requires internet connectivity and incurs per-token cost.

A provider abstraction layer allows switching between Ollama, Claude, and Gemini via a single configuration parameter.

---

## 3. Language-Agnostic Storage Strategy / Estrategia de Almacenamiento Agnóstico de Idioma

### EN

The multi-analyzer approach (L2–L4) optimizes each language separately but creates retrieval asymmetry: a Spanish query will not match German-indexed content via BM25. The kNN approach (L3) partially solves this, but performance degrades on short, technical queries where the multilingual model's cross-lingual alignment is weaker.

A complementary strategy is **canonical language normalization**: translate all content to a single chosen language before indexing. This creates a homogeneous index where all retrieval operates in one linguistic space.

**Configuration (env.py / catalog_config.json):**
```python
CANONICAL_LANGUAGE = "en"   # Options: "en", "es", "de"
TRANSLATION_MODEL  = "translategemma:4b"  # or "translategemma:12b"
TRANSLATION_MODE   = "background"  # "inline" | "background" | "disabled"
```

**Indexing flow with normalization:**
```
Raw file (any language)
  → text extraction
  → language detection (langdetect / fasttext)
  → IF lang != CANONICAL_LANGUAGE:
       → add to translation queue
       → index original content immediately (for incremental availability)
  → ELSE: index directly

[Background worker]
  → dequeue translation task
  → translate chunk via Ollama (TranslateGemma)
  → update ES document with canonical_content field
  → mark translation_status: "done"
```

**Advantages:**
- Single-language BM25 with one highly-tuned analyzer
- Queries never need translation (always canonical language)
- Cross-lingual retrieval via BM25 (not just kNN)
- Simpler relevance tuning

**Disadvantages:**
- Translation adds latency and storage overhead (~2x documents size)
- Translation errors propagate to retrieval quality
- Original language nuance may be lost (especially for technical German compound nouns)

### ES

El enfoque multi-analizador (L2–L4) optimiza cada idioma por separado pero crea asimetría en la recuperación: una consulta en español no coincidirá con contenido indexado en alemán mediante BM25. El enfoque kNN (L3) resuelve parcialmente esto, pero el rendimiento se degrada en consultas cortas y técnicas donde el alineamiento cruzado del modelo multilingüe es más débil.

Una estrategia complementaria es la **normalización al idioma canónico**: traducir todo el contenido a un único idioma elegido antes de indexarlo. Esto crea un índice homogéneo donde toda la recuperación opera en un único espacio lingüístico.

La ventaja principal es que se elimina la asimetría de recuperación entre idiomas. La desventaja es que la traducción introduce latencia y duplica el almacenamiento, y los errores de traducción se propagan a la calidad de la búsqueda. Para corpus técnicos multilingües con terminología específica, el enfoque híbrido (L4 + normalización opcional) suele ofrecer el mejor equilibrio.

---

## 4. Background Translation Pipeline / Cola de Traducción en Segundo Plano

### Architecture

The translation pipeline is designed as an asynchronous task queue to avoid blocking the main indexing process. Documents are immediately available for search in their original language; canonical translations are added incrementally as the background worker processes the queue.

```
┌─────────────────────────────────────────────────────────────────┐
│                     INDEXING PROCESS                            │
│                                                                 │
│  File → Extract → Detect Lang → Index (original) → Queue Task  │
│                                          │                      │
│                                          ▼                      │
│                              translation_queue.jsonl            │
│                              (append-only log file)             │
└─────────────────────────────────────────────────────────────────┘
                                           │
                                           │ (background worker)
                                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  TRANSLATION WORKER (daemon)                    │
│                                                                 │
│  Dequeue task → Ollama TranslateGemma → Update ES doc           │
│                                                                 │
│  Rate control: N parallel requests (N = CPU cores / 2)          │
│  Retry: exponential backoff, max 3 attempts                     │
│  Progress: written to translation_status.json                   │
└─────────────────────────────────────────────────────────────────┘
```

### TranslateGemma Model Selection

| Model | Size | VRAM req. | Speed (CPU) | Speed (M1 Max GPU) | Recommended for |
|---|---|---|---|---|---|
| `translategemma:4b` | 3.3 GB | ~5 GB | ~8 tokens/s | ~60 tokens/s | CPU systems, quick setup |
| `translategemma:12b` | 8.1 GB | ~12 GB | ~2 tokens/s | ~25 tokens/s | GPU systems, higher quality |

### Queue File Format (translation_queue.jsonl)

```json
{"task_id": "abc123", "es_doc_id": "...", "path": "...", "chunk_index": 0,
 "source_lang": "de", "target_lang": "en", "text": "...", "status": "pending",
 "created_at": "2026-03-25T10:00:00Z", "attempts": 0}
```

### Availability During Translation

The system supports a **dual-field search strategy** during the translation transition period:

```json
"content": "Original German text...",       // always present
"canonical_content": "Translated English...", // null until translated
"translation_status": "pending|done|failed"
```

BM25 queries target `canonical_content` when `CANONICAL_LANGUAGE` is set. If `canonical_content` is null (not yet translated), the document falls back to kNN scoring only (via the always-present multilingual embedding of the original text). Once translation completes, the document becomes fully searchable in the canonical language.

---

## 5. RAG: Grounding LLMs on Retrieved Context / Anclando los LLM en el Contexto Recuperado

### EN

Retrieval-Augmented Generation (RAG) addresses the fundamental limitation of search systems: they return document fragments but do not synthesize answers. RAG combines the precision of retrieval (knowing which documents are relevant) with the generative capability of LLMs (synthesizing coherent, elaborated responses).

**Critically, the LLM does not access Elasticsearch directly.** The orchestration layer retrieves relevant chunks, constructs a structured prompt, and passes it to the LLM. The LLM operates only on what it receives in the prompt context window. This is architecturally important: it means the system's knowledge is bounded by what is indexed, hallucinations are minimized by instructing the model to cite sources, and the retrieval quality directly determines the answer quality.

### RAG Query Flow

```
User question: "How is authentication configured?"
        │
        ▼
[1. Query Processing]
   - Language detection
   - Query expansion (synonyms from ES thesaurus)
   - Optional: query translation to canonical language
        │
        ▼
[2. Hybrid Retrieval from Elasticsearch]
   - BM25 search on content / canonical_content
   - kNN search on content_vector
   - RRF fusion → top-K chunks (K = 5 to 10)
   - Include metadata: filename, page, section heading, SRT timestamps
        │
        ▼
[3. Context Assembly]
   - Rank chunks by relevance + recency + section proximity
   - Truncate to fit LLM context window (4K–200K tokens)
   - Prepend document titles and section headings
        │
        ▼
[4. Prompt Construction]
   System: "You are a document assistant. Answer ONLY from the provided
            context. If the answer is not in the context, say so.
            Cite sources as [filename:section]."

   Context: "=== auth_guide.pdf | Section: Initial Setup ===
             To configure authentication, navigate to Settings > Security...
             === intro.md | Section: JWT Module ===
             The login module requires a JWT token issued by..."

   Question: "How is authentication configured?"
        │
        ▼
[5. LLM Generation]
   - Local: Ollama (llama3.2, qwen2.5, phi3.5)
   - Cloud: Claude API, Gemini API
   - Stream response for perceived responsiveness
        │
        ▼
[6. Response with Citations]
   "Authentication is configured in two steps:
    1. In Settings > Security, enable the authentication module [auth_guide.pdf].
    2. Configure the JWT token in the login module's settings [intro.md].
    ..."
```

### ES

La Generación Aumentada por Recuperación (RAG) aborda la limitación fundamental de los sistemas de búsqueda: devuelven fragmentos de documentos pero no sintetizan respuestas. RAG combina la precisión de la recuperación (saber qué documentos son relevantes) con la capacidad generativa de los LLM (sintetizar respuestas coherentes y elaboradas).

El LLM no accede a Elasticsearch directamente. La capa de orquestación recupera los fragmentos relevantes, construye un prompt estructurado y lo pasa al LLM. El LLM opera únicamente sobre lo que recibe en la ventana de contexto del prompt. Esto es arquitectónicamente importante: significa que el conocimiento del sistema está acotado por lo que está indexado, las alucinaciones se minimizan instruyendo al modelo para que cite fuentes, y la calidad de la recuperación determina directamente la calidad de la respuesta.

---

## 6. Context Enrichment for Better LLM Answers / Enriquecimiento de Contexto para Mejores Respuestas

### EN

The quality of RAG responses is directly proportional to the quality of context provided. Raw chunk retrieval delivers fragments; context enrichment delivers understanding.

### 6.1 Hierarchical Context (Chunk → Section → Chapter)

When a chunk is retrieved, its parent section and chapter are appended to the context:

```
Retrieved chunk: "JWT tokens expire after 24 hours by default."
  + Parent section heading: "## Token Lifecycle Management"
  + Chapter title: "# Authentication and Authorization Guide"
  + Document summary (pre-generated): "This guide covers the auth system..."
```

This gives the LLM the "frame" of the chunk — where it appears in the document's logical structure — enabling more accurate, contextual answers.

### 6.2 Document-Level Metadata Injection

Each retrieved source contributes:
- **Title and chapter** — what the document is about
- **Author, date** — for temporal reasoning ("the 2023 version says...")
- **Document type** — training material, API reference, tutorial
- **Pre-generated summary** (see Section 8) — compact description of the full document

### 6.3 Surrounding Chunks (Window Expansion)

Beyond the retrieved chunk, include $N$ preceding and $N$ following chunks:

```python
context_window = [chunk_{i-2}, chunk_{i-1}, chunk_i, chunk_{i+1}, chunk_{i+2}]
```

This provides continuity — the LLM sees the argument being built, not just an isolated sentence.

### 6.4 Dynamic Context Budget

Available context varies by LLM:

| Model | Context window | Usable for RAG context |
|---|---|---|
| `phi3.5` (3.8B) | 128K tokens | ~100K tokens |
| `llama3.2` (3B) | 128K tokens | ~100K tokens |
| `qwen2.5:7b` | 128K tokens | ~100K tokens |
| `llama3.1:70b` | 128K tokens | ~100K tokens |
| Claude 3.5 Sonnet | 200K tokens | ~180K tokens |
| Gemini 1.5 Pro | 1M tokens | ~900K tokens |

The context assembler ranks retrieved chunks by relevance and fills the budget greedily, including surrounding-chunk windows for the highest-ranked results.

### 6.5 SRT Subtitle Specific Context

For video subtitle content, the retrieved chunk includes:
- **Timestamp**: `[00:04:32]` — the moment in the video
- **Surrounding 60 seconds** of transcript for conversational continuity
- **Video title and chapter marker** (from SRT block headers)

This enables time-positioned answers: "This is explained at 4:32 in the 'Getting Started' video."

### ES

La calidad de las respuestas RAG es directamente proporcional a la calidad del contexto proporcionado. La recuperación cruda de fragmentos entrega trozos aislados; el enriquecimiento de contexto entrega comprensión.

**Contexto jerárquico:** cuando se recupera un fragmento, se añaden su sección padre y el capítulo al contexto, dando al LLM el "marco" del fragmento — dónde aparece en la estructura lógica del documento.

**Ventana de fragmentos adyacentes:** incluir los $N$ fragmentos anteriores y posteriores al recuperado proporciona continuidad narrativa — el LLM ve el argumento siendo construido, no solo una frase aislada.

**Presupuesto de contexto dinámico:** el ensamblador de contexto clasifica los fragmentos recuperados por relevancia y llena el presupuesto de tokens disponible de forma voraz, priorizando las ventanas de fragmentos adyacentes para los resultados más relevantes.

---

## 7. Hardware-Aware Model Selection / Selección de Modelo Consciente del Hardware

### Detection Strategy

```python
def detect_hardware_profile():
    profile = {
        "platform": sys.platform,           # darwin / linux / win32
        "arch": platform.machine(),          # arm64 / x86_64
        "cpu_cores": os.cpu_count(),
        "ram_gb": psutil.virtual_memory().total / 1e9,
        "gpu_type": None,    # "apple_silicon" | "nvidia" | "amd" | None
        "gpu_vram_gb": 0,
        "ollama_installed": shutil.which("ollama") is not None,
    }

    # Apple Silicon detection
    if profile["arch"] == "arm64" and profile["platform"] == "darwin":
        profile["gpu_type"] = "apple_silicon"
        # Unified memory — GPU shares system RAM
        profile["gpu_vram_gb"] = profile["ram_gb"]

    # NVIDIA detection
    elif shutil.which("nvidia-smi"):
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True)
        if result.returncode == 0:
            profile["gpu_type"] = "nvidia"
            profile["gpu_vram_gb"] = float(result.stdout.strip()) / 1024

    # AMD ROCm detection
    elif shutil.which("rocm-smi"):
        profile["gpu_type"] = "amd"
        # Parse rocm-smi output for VRAM
        ...

    return profile
```

### Model Recommendation Matrix

| Hardware Profile | RAM | GPU VRAM | LLM Recommended | Translation Model |
|---|---|---|---|---|
| CPU only (laptop) | < 8 GB | — | `phi3.5` (3.8B) | `translategemma:4b` |
| CPU only (desktop) | 8–16 GB | — | `llama3.2:3b` | `translategemma:4b` |
| CPU only (workstation) | ≥ 16 GB | — | `qwen2.5:7b` | `translategemma:4b` |
| Apple Silicon M1/M2 | 16 GB | 16 GB (unified) | `llama3.2:3b` | `translategemma:4b` |
| Apple Silicon M1 Max/Ultra | 32–64 GB | 32–64 GB | `qwen2.5:14b` or `llama3.1:70b` | `translategemma:12b` |
| NVIDIA 4 GB VRAM | any | 4 GB | `phi3.5` (Q4) | `translategemma:4b` |
| NVIDIA 8–12 GB VRAM | any | 8–12 GB | `llama3.2:7b` | `translategemma:4b` |
| NVIDIA ≥ 16 GB VRAM | any | ≥ 16 GB | `qwen2.5:14b` | `translategemma:12b` |
| NVIDIA ≥ 24 GB VRAM | any | ≥ 24 GB | `llama3.1:70b` (Q4) | `translategemma:12b` |

### Ollama Installation (Cross-Platform)

```bash
# Linux / WSL
curl -fsSL https://ollama.com/install.sh | sh

# macOS
brew install ollama
# or: download from https://ollama.com

# Windows (PowerShell)
winget install Ollama.Ollama
```

---

## 8. Corpus Analytics and Document Profiling / Análisis de Corpus y Perfilado Documental

### EN

Beyond search and question-answering, the indexed corpus provides a foundation for systematic document analysis. The following analytical modules are proposed, each operating on the text content stored in Elasticsearch:

### 8.1 Automatic Summarization

**Method:** LLM-based abstractive summarization, applied per document or per chapter.

```
Input:  Full document text (or chapter)
Prompt: "Summarize this document in 3-5 sentences, focusing on the main
         topic, key concepts, and target audience."
Output: Summary stored in ES field "doc_summary"
```

**Use cases:** Display in catalog cards, RAG context injection, quick document overview.

### 8.2 Gendered Language Analysis / Análisis del Lenguaje de Género

Quantify gender-marked language usage in documents:

- **Masculine generics:** count uses of "he/him", "der/die/das" masculine defaults, "él" as generic
- **Feminine forms:** "she/her", German "-in" suffix forms (Mitarbeiterin), "ella"
- **Neutral/inclusive language:** "they/them", German gendering (Mitarbeiter*in, Mitarbeiter:in), "persona"
- **Gender ratio score:** `masculine_count / (masculine_count + feminine_count + neutral_count)`

**Implementation:**
```python
# spaCy (de/en/es models) for POS tagging
# Pattern matching for gendered pronouns and noun suffixes
# Per-document and corpus-level statistics
```

**Output metrics:**
```json
{
  "doc_id": "...",
  "gender_score": 0.73,         // 0=fully neutral, 1=fully masculine-generic
  "masculine_terms": 145,
  "feminine_terms": 23,
  "neutral_terms": 31,
  "inclusive_markers": 5,
  "flagged_phrases": ["mankind", "chairman", "he should..."]
}
```

### 8.3 Narrative Person Distribution / Distribución de Persona Narrativa

Analyze the predominant narrative voice:

- **1st person singular** (I/me/my, ich/mein, yo/mi): personal testimony, autobiographical
- **1st person plural** (we/our, wir/unser, nosotros): collaborative, institutional
- **2nd person** (you/your, du/Sie/Ihr, tú/usted): instructional, addressed to reader
- **3rd person** (he/she/they/it, er/sie/es, él/ella/ellos): descriptive, reportorial

```python
person_counts = {
    "1sg": 0, "1pl": 0, "2sg": 0, "2pl": 0,
    "3sg_m": 0, "3sg_f": 0, "3sg_n": 0, "3pl": 0
}
# Normalized by total pronoun count → distribution vector
```

**Use case:** Training materials heavy in 2nd person are instructional. Academic papers heavy in 3rd person are descriptive. First-person plural indicates collaborative authorship or institutional voice.

### 8.4 Verb Tense Analysis / Análisis de Tiempos Verbales

Map the temporal distribution of discourse:

- **Present tense** — current state descriptions, definitions, general truths
- **Past tense** — case studies, historical references, completed actions
- **Future tense** — plans, recommendations, predictions
- **Conditional/subjunctive** — hypothetical scenarios, requirements
- **Imperative** — direct instructions, commands

```
Tense distribution radar chart per document:
  Present:   ████████ 62%
  Past:      ███ 18%
  Future:    ██ 11%
  Imperative: █ 6%
  Other:      █ 3%
```

**Use case:** A document with 80% imperative mood is a procedural guide. A document with 40% past tense may be a post-mortem or case study.

### 8.5 Lexical Richness and Descriptive Density / Riqueza Léxica y Densidad Descriptiva

Standard readability and vocabulary metrics:

| Metric | Formula | Interpretation |
|---|---|---|
| **TTR** (Type-Token Ratio) | `unique_words / total_words` | Vocabulary diversity |
| **MTLD** | Moving-average TTR | More stable than TTR for long texts |
| **Adjective density** | `adjective_count / total_words` | Descriptive richness |
| **Adverb density** | `adverb_count / total_words` | Qualifier usage |
| **Flesch Reading Ease** | `206.835 - 1.015*(words/sent) - 84.6*(syllables/word)` | Readability (EN) |
| **Wiener Sachtextformel** | German readability equivalent | |
| **Fernández Huerta** | Spanish readability equivalent | |

### 8.6 Named Entity Extraction / Extracción de Entidades Nombradas

Using spaCy multilingual NER models:

- **PERSON** — people mentioned
- **ORG** — organizations, companies, institutions
- **PRODUCT** — software, tools, products
- **GPE** — geographical locations
- **DATE/TIME** — temporal references
- **TECH** — (custom) programming languages, frameworks, protocols

**Output:** Entity frequency heatmap per corpus → shows which topics/people/products appear most across the training catalog.

### 8.7 Corpus-Level Statistics Dashboard

Aggregated across all indexed documents:

```json
{
  "total_documents": 847,
  "total_chunks": 23456,
  "languages": {"de": 61%, "en": 28%, "es": 11%},
  "avg_doc_length_words": 4823,
  "avg_lexical_richness": 0.42,
  "top_entities": ["SAP", "Python", "ISO 27001", ...],
  "gender_score_corpus": 0.71,
  "dominant_tense_by_doctype": {
    "tutorial": "imperative",
    "reference": "present",
    "case_study": "past"
  }
}
```

### ES

Más allá de la búsqueda y el sistema de preguntas, el corpus indexado proporciona una base para el análisis documental sistemático.

**Resúmenes automáticos** mediante LLM abstractivo aplicado por documento o capítulo — resultados almacenados en ES para uso en RAG y visualización en el catálogo.

**Análisis de lenguaje de género** cuantificando el uso del genérico masculino, formas femeninas e indicadores de lenguaje inclusivo en cada documento y a nivel de corpus.

**Distribución de persona narrativa** mapeando la voz predominante (1ª/2ª/3ª persona) para caracterizar el tipo discursivo (instructivo, descriptivo, testimonial, institucional).

**Análisis de tiempos verbales** para caracterizar la orientación temporal del discurso: documentos predominantemente en imperativo son guías procedimentales; los dominados por pasado son estudios de caso; los dominados por presente son referencias o definiciones.

**Riqueza léxica y densidad descriptiva** mediante métricas estándar de legibilidad (Flesch, Fernández Huerta, Wiener Sachtextformel) y razón tipo-token (TTR/MTLD) como indicadores de complejidad y variedad léxica.

**Extracción de entidades nombradas** usando modelos spaCy multilingües para identificar personas, organizaciones, productos y tecnologías mencionadas en el corpus.

---

## 9. System Integration Map / Mapa de Integración del Sistema

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          USER INTERFACES                                │
│                                                                         │
│  course_catalog.html    es_test.html    future: chat_interface.html     │
│  (static catalog)       (ES test)       (RAG question answering)        │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ HTTP REST
┌────────────────────────────▼────────────────────────────────────────────┐
│                       catalog_server.py (Flask)                         │
│                                                                         │
│  /api/search          BM25 or Python fallback search                   │
│  /api/es-search       Direct ES search (BM25/semantic/hybrid)          │
│  /api/ask             RAG endpoint (new)                                │
│  /api/health          Status: ES connected, Ollama available           │
│  /api/analytics       Corpus statistics (new)                          │
│  /api/translate-status Translation queue progress (new)                │
└────────┬───────────────────────────────────────────┬───────────────────┘
         │                                           │
         ▼                                           ▼
┌─────────────────────┐                  ┌───────────────────────┐
│   Elasticsearch 8   │                  │   Ollama (local)      │
│                     │                  │                       │
│  Index: course_     │                  │  LLM: llama3.2,       │
│  catalog            │                  │       qwen2.5, etc.   │
│                     │                  │                       │
│  Fields:            │                  │  Translation:         │
│  - content          │                  │  translategemma:4b    │
│  - canonical_content│                  │       or :12b         │
│  - content_vector   │                  │                       │
│  - doc_summary      │                  │  Embeddings:          │
│  - srt_start/end    │                  │  (fastembed, local)   │
│  - analytics.*      │                  └───────────────────────┘
└─────────────────────┘                            │
         │                                         │ (optional)
         ▼                                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    BACKGROUND WORKERS (daemon threads)                  │
│                                                                         │
│  translation_worker.py   — processes translation_queue.jsonl            │
│  analytics_worker.py     — computes doc profiles, stores in ES          │
│  embedding_worker.py     — generates vectors for new/updated docs       │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              EXTERNAL LLM APIs (optional, L7)                           │
│                                                                         │
│  Anthropic Claude API  (200K context, best quality)                     │
│  Google Gemini API     (1M context, multimodal)                         │
│  OpenAI API            (GPT-4o, gpt-4o-mini)                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Evaluation Metrics / Métricas de Evaluación

### Retrieval Quality

| Metric | Description |
|---|---|
| **Precision@K** | Fraction of top-K results that are relevant |
| **Recall@K** | Fraction of all relevant documents in top-K |
| **MRR** (Mean Reciprocal Rank) | Average inverse rank of first correct result |
| **NDCG@K** | Normalized Discounted Cumulative Gain |

### RAG Answer Quality

| Metric | Description | Tool |
|---|---|---|
| **Faithfulness** | Does answer stay within provided context? | RAGAS |
| **Answer Relevancy** | Does answer address the question? | RAGAS |
| **Context Precision** | Are retrieved chunks actually useful? | RAGAS |
| **Hallucination rate** | % of claims not supported by sources | Manual or LLM-judge |

### Translation Quality

| Metric | Description |
|---|---|
| **BLEU** | N-gram overlap with reference translation |
| **chrF** | Character-level F-score (better for morphologically rich languages) |
| **COMET** | Neural metric correlating with human judgment |

---

## 11. Future Work / Trabajo Futuro

### EN

1. **Multimodal indexing**: Extract and index text from images within PDFs (OCR via Tesseract or PaddleOCR), enabling search over scanned documents and presentation slides.

2. **Graph-based knowledge representation**: Build a knowledge graph from named entity co-occurrences, enabling relationship queries ("which documents mention both SAP and ISO 27001?").

3. **Personalized retrieval**: Track user search history and document interactions to personalize result ranking using collaborative filtering.

4. **Real-time document ingestion**: Filesystem watcher (watchdog) triggering incremental indexing as documents are added or modified, without manual re-runs.

5. **Query understanding**: Classify query intent (factual lookup, procedural guidance, comparative analysis) and route to specialized retrieval strategies.

6. **Structured data integration**: Index spreadsheet content (xlsx, csv) with row-level granularity, enabling questions like "find all courses with duration > 8 hours."

7. **Citation graph analysis**: For academic or technical document collections, extract and index citation relationships to enable influence and lineage queries.

8. **Multilingual analytics**: Extend corpus analytics to compare linguistic patterns across languages within the same document corpus (e.g., "do German documents use more imperative mood than Spanish ones?").

### ES

1. **Indexación multimodal**: OCR sobre imágenes en PDFs para permitir búsqueda en documentos escaneados y diapositivas.

2. **Representación por grafo de conocimiento**: Grafo de co-ocurrencias de entidades nombradas para consultas relacionales.

3. **Recuperación personalizada**: Historial de búsqueda y recomendación colaborativa.

4. **Ingestión de documentos en tiempo real**: Vigilante del sistema de archivos (watchdog) que desencadena indexado incremental automático.

5. **Comprensión de la consulta**: Clasificación de la intención de la consulta para enrutamiento a estrategias de recuperación especializadas.

6. **Integración de datos estructurados**: Indexado de hojas de cálculo a nivel de fila para consultas sobre atributos numéricos o categóricos.

7. **Análisis de grafos de citaciones**: Relaciones de influencia y linaje en corpus de documentación técnica o académica.

8. **Análisis lingüístico multilingüe comparado**: Comparación de patrones discursivos entre idiomas dentro del mismo corpus.

---

## 12. References / Referencias

- Robertson, S., & Zaragoza, H. (2009). *The Probabilistic Relevance Framework: BM25 and Beyond*. Foundations and Trends in Information Retrieval, 3(4), 333–389.

- Lewis, P., et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*. NeurIPS 2020.

- Reimers, N., & Gurevych, I. (2019). *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks*. EMNLP 2019.

- Reimers, N., & Gurevych, I. (2020). *Making Monolingual Sentence Embeddings Multilingual using Knowledge Distillation*. EMNLP 2020. _(paraphrase-multilingual-MiniLM model)_

- Es, S., et al. (2023). *RAGAS: Automated Evaluation of Retrieval Augmented Generation*. arXiv:2309.15217.

- Cormack, G.V., Clarke, C.L.A., & Buettcher, S. (2009). *Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods*. SIGIR 2009.

- Google DeepMind. (2024). *TranslateGemma: Efficient Neural Machine Translation with Gemma*. Technical Report.

- Elasticsearch B.V. (2024). *Elasticsearch Reference 8.x*. https://www.elastic.co/guide/en/elasticsearch/reference/

- Ollama. (2024). *Ollama: Get up and running with large language models locally*. https://ollama.com

- spaCy. (2024). *Industrial-strength Natural Language Processing*. https://spacy.io

---

_This document is a working draft / Este documento es un borrador de trabajo._
_Last updated / Última actualización: 2026-03_
