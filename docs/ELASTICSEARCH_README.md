# Course Catalog — Elasticsearch Integration

Documentación completa de la integración de Elasticsearch en el Course Catalog Generator: búsqueda full-text multilingüe, thesaurus, indexado incremental, subtítulos con timestamps y consola de pruebas.

---

## Tabla de contenidos

1. [Visión general](#1-visión-general)
2. [Arquitectura](#2-arquitectura)
3. [Elasticsearch: Docker, datos y persistencia](#3-elasticsearch-docker-datos-y-persistencia)
4. [Inicio rápido](#4-inicio-rápido)
5. [Scripts de inicio](#5-scripts-de-inicio)
6. [Indexador — es_indexer.py](#6-indexador--es_indexerpy)
7. [Backend — catalog_server.py](#7-backend--catalog_serverpy)
8. [Thesaurus multilingüe](#8-thesaurus-multilingüe)
9. [Subtítulos SRT con timestamps](#9-subtítulos-srt-con-timestamps)
10. [Indexado incremental](#10-indexado-incremental)
11. [Consola de pruebas — es_test.html](#11-consola-de-pruebas--es_testhtml)
12. [Docker Compose](#12-docker-compose)
13. [Configuración — env.py y .env](#13-configuración--envpy-y-env)
14. [Referencia de API](#14-referencia-de-api)
15. [Referencia de parámetros CLI](#15-referencia-de-parámetros-cli)
16. [Solución de problemas](#16-solución-de-problemas)

---

## 1. Visión general

El sistema combina dos motores de búsqueda complementarios:

| Motor | Archivo | Cuándo se usa |
|---|---|---|
| **Python nativo** | `catalog_server.py` | Siempre disponible. Búsqueda fuzzy/fonética/multilingüe sobre texto extraído en tiempo real o caché. |
| **Elasticsearch** | `es_indexer.py` + Docker | Opcional. BM25 + kNN semántico, thesaurus EN/ES/DE, timestamps en subtítulos, búsquedas instantáneas en colecciones grandes. |

Cuando Elasticsearch está en ejecución, `/api/search` lo usa automáticamente y cae de vuelta al motor Python si ES no está disponible.

---

## 2. Arquitectura

```
┌─────────────────────────────────────────────────┐
│  course_catalog.html  (frontend estático)        │
│  es_test.html         (consola de pruebas ES)    │
└──────────────┬──────────────────────────────────┘
               │ REST / WebSocket
┌──────────────▼──────────────────────────────────┐
│  catalog_server.py  (Flask, puerto 5000)         │
│  ┌────────────────┐   ┌───────────────────────┐  │
│  │ Python search  │   │  ES client (auto-     │  │
│  │ fuzzy/fonético │   │  detect localhost:    │  │
│  └────────────────┘   │  9200 o ES_URL)       │  │
│                       └──────────┬────────────┘  │
└──────────────────────────────────┼───────────────┘
                                   │
┌──────────────────────────────────▼───────────────┐
│  Docker Compose                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │  elasticsearch:8.12.0  (puerto 9200)         │ │
│  │  • BM25 multilingüe (de/es/en)               │ │
│  │  • Thesaurus inline (synonym_graph)          │ │
│  │  • kNN dense_vector (384 dims, cosine)       │ │
│  └─────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────┐ │
│  │  indexer  (one-shot, incremental)            │ │
│  │  es_indexer.py — extrae y sube texto         │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

---

## 3. Elasticsearch: Docker, datos y persistencia

### ¿Con Docker o sin Docker?

**Elasticsearch siempre corre dentro de Docker** en esta implementación. El proyecto no instala ES directamente en el sistema operativo — usa la imagen oficial `docker.elastic.co/elasticsearch/elasticsearch:8.12.0`.

```text
Tu máquina
└── Docker Desktop
    └── Contenedor: course-catalog-es
        └── Elasticsearch 8.12.0  (puerto 9200 expuesto al host)
```

El backend Flask (`catalog_server.py`) puede correr fuera o dentro de Docker según el modo de inicio:

| Modo de inicio | Flask | Elasticsearch |
|---|---|---|
| `start.sh` / `start.bat` | Nativo (Python local) | Docker (si se acepta al preguntar) |
| `start-docker.sh` / `start-docker.bat` | Docker | Docker |

### ¿Dónde guarda los datos Elasticsearch?

ES guarda todo en un **volumen Docker nombrado**, gestionado internamente por Docker — no es una carpeta visible directamente en tu sistema de archivos:

```yaml
# docker-compose.yml
volumes:
  es_data:       # volumen nombrado, gestionado por Docker
    driver: local
```

Internamente, el volumen se monta en el contenedor como:

```text
/usr/share/elasticsearch/data/   (dentro del contenedor)
├── indices/
│   └── course_catalog/          ← tu índice
│       └── 0/
│           ├── _state/          ← metadatos del shard
│           └── index/           ← segmentos Lucene (datos reales)
└── _state/                      ← estado del cluster
```

Para ver la ubicación física real en tu máquina:

```bash
docker volume inspect _Course_catalog_Generator_es_data
# → "Mountpoint": "/var/lib/docker/volumes/..._es_data/_data"
#
# En Windows con Docker Desktop / WSL2:
# → \\wsl$\docker-desktop-data\data\docker\volumes\..._es_data\_data
```

### ¿Qué contiene el índice?

ES es una base de datos **documental** basada en Apache Lucene. Cada registro es un documento JSON con el texto extraído de tus archivos. Los archivos originales **no se copian** — sólo el texto extraído y sus metadatos.

Un archivo grande se divide en **chunks de ~1200 caracteres** para mejorar la precisión semántica:

```text
Índice: course_catalog
└── Un documento por cada chunk de texto
    ├── path, filename, ext          ← ubicación del archivo original
    ├── content                      ← texto del chunk (indexado con BM25 + thesaurus)
    ├── content_vector               ← embedding de 384 dimensiones (búsqueda semántica)
    ├── file_mtime                   ← fecha de modificación (para indexado incremental)
    ├── srt_start_time               ← segundos desde inicio del vídeo (sólo archivos SRT/VTT)
    └── srt_end_time
```

Ejemplo: un PDF de 50 páginas puede generar entre 10 y 40 documentos en ES.

### ¿Los datos persisten si paro Docker?

**Sí.** El volumen `es_data` sobrevive a reinicios y a `docker compose down`. Los datos sólo se pierden si se borra el volumen explícitamente:

```bash
# ✓ Para contenedores — los datos se CONSERVAN
docker compose down

# ✗ Para contenedores Y borra el volumen — datos PERDIDOS
docker compose down -v

# También eliminan datos del índice:
python es_indexer.py --reset   # elimina y recrea el índice
python es_indexer.py --clean   # elimina el índice (sin recrear)
```

> **Nota:** si desinstalar Docker Desktop del sistema, todos los volúmenes también desaparecen.

### Backup y restauración del volumen

```bash
# Exportar el volumen a un archivo tar
docker run --rm \
  -v _Course_catalog_Generator_es_data:/data \
  -v "$(pwd)":/backup \
  alpine tar czf /backup/es_backup.tar.gz /data

# Restaurar desde el backup
docker run --rm \
  -v _Course_catalog_Generator_es_data:/data \
  -v "$(pwd)":/backup \
  alpine tar xzf /backup/es_backup.tar.gz -C /
```

---

## 4. Inicio rápido

### Opción A — Nativo (Python, sin Docker)

```bash
# Windows
start.bat "C:\Users\Me\Videos"

# Linux / macOS
./start.sh "/home/me/videos"
```

El script instala dependencias, genera el catálogo HTML y arranca el backend. Si Docker está disponible, ofrece iniciarlo para Elasticsearch.

### Opción B — Docker completo (Elasticsearch incluido)

```bash
# Windows
start-docker.bat "C:\Users\Me\Videos"

# Linux / macOS
./start-docker.sh "/home/me/videos"
```

Levanta Elasticsearch + backend Flask, indexa los archivos y muestra resumen de URLs.

### Opción C — Sólo Elasticsearch (ES ya instalado o manualmente)

```bash
# 1. Copiar y editar configuración
cp .env.example .env
# editar COURSES_PATH en .env

# 2. Levantar ES
docker compose up -d elasticsearch

# 3. Indexar (incremental por defecto)
python es_indexer.py

# 4. Arrancar backend
python catalog_server.py
```

---

## 5. Scripts de inicio

### `start.bat` / `start.sh` — Setup nativo

Pasos que ejecuta:

| Paso | Descripción |
|---|---|
| 0 (nuevo) | Comprueba Docker. Si no está instalado: muestra instrucciones (Ubuntu: ofrece instalar vía `apt-get`). Si está instalado pero no arrancado: intenta iniciarlo. |
| 1 | Comprueba Python ≥ 3.9 |
| 2 | Determina la ruta de cursos (CLI o env.py) |
| 3 | Crea/activa virtualenv |
| 4 | Instala dependencias desde `requirements.txt` |
| 5 | Genera catálogo (`course_catalog_generator.py`) |
| 6 | Arranca backend (`catalog_server.py`) |

```bash
./start.sh                        # interactivo
./start.sh "/ruta/a/cursos"       # ruta directa
./start.sh --help
```

**Detección automática de Docker (nuevo en start.sh):**

- Ubuntu/Debian: si Docker no está instalado, ofrece instalarlo con `apt-get`.
- Linux con systemd: si Docker está parado, ejecuta `sudo systemctl start docker`.
- macOS: indica abrir Docker Desktop desde Aplicaciones.

### `start-docker.bat` / `start-docker.sh` — Setup Docker

```bash
./start-docker.sh                            # interactivo
./start-docker.sh "/ruta/cursos"             # ruta directa
./start-docker.sh "/ruta/cursos" --kibana    # incluye Kibana en :5601
./start-docker.sh --no-index                 # salta indexado
./start-docker.sh --no-generate              # salta generación HTML
```

Si el índice ya contiene documentos, pregunta:
1. ¿Reindexar todo desde cero? (`--reset`)
2. ¿Indexado incremental? (sólo archivos nuevos/modificados)
3. Usar el índice existente sin cambios.

### `course_catalog_generator.py` — Pregunta de Docker

Al generar el catálogo, pregunta si iniciar Elasticsearch:

```
──────────────────────────────────────────────
  Elasticsearch — Enhanced Search (optional)
──────────────────────────────────────────────
  Start Docker Elasticsearch? [y/N]:
```

Si se responde `y`:
1. Detecta `docker compose` v2 o `docker-compose` v1.
2. Arranca el contenedor de Elasticsearch.
3. Espera hasta 90 s a que esté listo (`/_cluster/health`).
4. Pregunta si indexar los archivos de cursos.
5. Ejecuta el indexador incremental.

---

## 6. Indexador — es_indexer.py

### Uso

```bash
# Primera vez (o reindexado completo)
python es_indexer.py --reset

# Incremental — sólo archivos nuevos o modificados (defecto)
python es_indexer.py

# Ruta personalizada
python es_indexer.py --path /otra/ruta

# Sin vectores semánticos (más rápido, sólo BM25)
python es_indexer.py --no-vectors

# Limpiar el índice (borrar todos los datos, sin reindexar)
python es_indexer.py --clean
python es_indexer.py --clean --yes    # sin confirmación

# Via Docker
docker compose run --rm indexer                           # incremental
docker compose run --rm indexer python es_indexer.py --reset   # completo
```

### Parámetros completos

| Parámetro | Default | Descripción |
|---|---|---|
| `--path PATH` | `SEARCH_BASE_PATH` de env.py | Directorio raíz a indexar |
| `--es-url URL` | `http://127.0.0.1:9200` | URL de Elasticsearch |
| `--index NAME` | `course_catalog` | Nombre del índice |
| `--reset` | false | Borra el índice y reindexa todo |
| `--clean` | false | Borra el índice y sale (sin reindexar) |
| `--yes` / `-y` | false | Omite confirmación para `--clean` |
| `--no-vectors` | false | Omite embeddings (BM25 únicamente) |
| `--batch N` | 50 | Tamaño de lote para bulk indexing |

### Formatos soportados

| Extensión | Extractor |
|---|---|
| `.pdf` | PyMuPDF (fitz) → pdfminer.six (fallback) |
| `.docx` | python-docx |
| `.xlsx`, `.xls` | openpyxl |
| `.pptx`, `.ppt` | python-pptx |
| `.rtf` | striprtf |
| `.html`, `.htm` | BeautifulSoup4 |
| `.srt`, `.vtt` | Parser propio con timestamps |
| `.md`, `.txt`, `.csv`, `.js`, `.py`, `.json`… | Texto plano con detección de encoding |

### Estructura del documento en ES

```json
{
  "path":           "/ruta/absoluta/al/archivo.md",
  "rel_path":       "Curso1/tema1.md",
  "filename":       "tema1.md",
  "ext":            "md",
  "chunk_index":    0,
  "file_mtime":     "2025-03-20T10:30:00+00:00",
  "content":        "Texto del chunk...",
  "content_vector": [0.12, -0.34, ...],
  "indexed_at":     "2025-03-25T14:00:00+00:00",
  "srt_start_time": 42.5,
  "srt_end_time":   47.1
}
```

`srt_start_time` / `srt_end_time` sólo están presentes en documentos de archivos `.srt` / `.vtt`.

---

## 7. Backend — catalog_server.py

### Detección automática de Elasticsearch

El backend detecta ES automáticamente aunque `ES_URL` esté vacío en `env.py`:

```python
# Prioridad:
# 1. ES_URL configurado en env.py (si no está vacío)
# 2. http://127.0.0.1:9200 (Docker local)
# 3. Sin ES — usa búsqueda Python
```

El reintento se throttlea a 30 segundos para no penalizar búsquedas cuando ES no está disponible.

### `/api/search` — Búsqueda unificada (nuevo comportamiento)

```
GET /api/search?q=<query>[&lang=de|es|en][&limit=N][&soundex=1][&cached=1]
```

**Flujo:**
1. Comprueba si ES está disponible y el índice tiene documentos.
2. Si sí → usa `_search_via_es()` (BM25 + thesaurus + highlighting).
3. Si no → cae en la búsqueda Python (fuzzy/fonética/multilingüe).
4. Misma estructura de respuesta en ambos casos.

### `/api/es-search` — ES explícito

```
GET /api/es-search?q=<query>[&lang=de|es|en][&semantic=1][&limit=N]
```

Llama siempre a Elasticsearch. Devuelve error 503 si ES no está disponible.

| Parámetro | Descripción |
|---|---|
| `q` | Término de búsqueda (mín. 2 caracteres) |
| `lang` | `de`, `es`, `en` o vacío (standard) |
| `semantic` | `1` → búsqueda kNN + BM25 híbrida |
| `limit` | Máximo resultados (default: MAX_RESULTS, máx. 200) |

---

## 8. Thesaurus multilingüe

Elasticsearch aplica sinónimos en tiempo de búsqueda (`updateable: true`). Los sinónimos están definidos **inline** en el mapping del índice (no requieren archivos externos).

### Cómo funciona

```
Índice:   texto con analizadores german/spanish/english (sin sinónimos)
Búsqueda: analizadores german_search/spanish_search/english_search
          con synonym_graph + stemming + stop words
```

Ejemplo — búsqueda `?q=film&lang=de`:
- `film` → también encuentra documentos con `video`, `aufnahme`, `clip`, `aufzeichnung`

### Grupos de sinónimos incluidos (selección)

**Inglés (`en`):**
```
video, movie, film, recording, clip
course, class, lesson, tutorial, training, workshop, module
document, file, paper, report, manual, guide
error, bug, issue, problem, fault, exception
install, setup, configure, deploy
database, db, datastore, storage, repository
```

**Español (`es`):**
```
video, película, grabación, clip
curso, clase, lección, tutorial, capacitación, formación, taller, módulo
documento, archivo, informe, manual, guía
error, fallo, problema, defecto
instalar, configurar, desplegar
base de datos, almacenamiento, repositorio
```

**Alemán (`de`):**
```
video, film, aufnahme, clip, aufzeichnung
kurs, klasse, lektion, tutorial, schulung, training, workshop, modul
dokument, datei, bericht, handbuch, anleitung
fehler, bug, problem, defekt
installieren, einrichten, konfigurieren, bereitstellen
datenbank, datenspeicher, repository
```

Los archivos de referencia están en `synonyms/synonyms_en.txt`, `synonyms_es.txt` y `synonyms_de.txt`.

### Aplicar thesaurus a un índice existente

Los sinónimos sólo se aplican a un índice **nuevo**. Para actualizar un índice existente:

```bash
python es_indexer.py --reset
```

---

## 9. Subtítulos SRT con timestamps

Los archivos `.srt` y `.vtt` reciben tratamiento especial: en lugar de extraer el texto plano, el indexador parsea cada bloque de subtítulo preservando su timestamp.

### Proceso

```
Archivo SRT:
  42
  00:00:42,500 --> 00:00:47,100
  Installation des ELO Servers auf Windows.

Se convierte en documento ES:
  content:        "Installation des ELO Servers auf Windows."
  srt_start_time: 42.5    ← segundos desde el inicio del vídeo
  srt_end_time:   47.1
  chunk_index:    0
```

Los subtítulos consecutivos se agrupan en chunks de ~1200 caracteres preservando el timestamp de inicio del primer subtítulo del grupo.

### Resultado en búsqueda

```json
{
  "file": "ELO_Basics_Tag1.srt",
  "type": "srt",
  "matches": [
    {
      "line": 3,
      "context": "**Installation** des ELO Servers auf Windows.",
      "srt_time": 42.5,
      "highlight": "Installation"
    }
  ]
}
```

El campo `srt_time` contiene los segundos desde el inicio del vídeo. Combinado con el endpoint `/api/play`, permite abrir VLC directamente en el momento exacto:

```
GET /api/play?srt=/ruta/subtitulo.srt&time=42
```

---

## 10. Indexado incremental

Por defecto (sin `--reset`), el indexador sólo procesa archivos nuevos o modificados.

### Cómo se detectan los cambios

1. Al iniciar, consulta ES para obtener `{path: file_mtime}` de todos los documentos indexados.
2. Compara la fecha de modificación del sistema de archivos con la almacenada.
3. **Sin cambios** → el archivo se omite completamente.
4. **Modificado** → se borran los documentos antiguos vía `delete_by_query` y se reindexa.
5. **Nuevo** → se indexa directamente.

```bash
# Primera indexación (vacía el índice y reindexa todo)
python es_indexer.py --reset

# Ejecuciones posteriores (sólo novedades)
python es_indexer.py
```

El campo `file_mtime` se almacena en formato ISO 8601 UTC en cada documento.

### Comportamiento del servicio Docker

El servicio `indexer` en `docker-compose.yml` ejecuta `python es_indexer.py` (incremental) por defecto. Para forzar reindexado completo:

```bash
docker compose run --rm indexer python es_indexer.py --reset
```

---

## 11. Consola de pruebas — es_test.html

Archivo standalone, abrir directamente en el navegador (no requiere servidor):

```
_Course_catalog_Generator/es_test.html
```

### Funcionalidades

| Sección | Descripción |
|---|---|
| **Indicadores de estado** | Puntos verde/amarillo/rojo para ES (`:9200`) y Backend API (`:5000`) |
| **Búsqueda** | Modos ES BM25, ES Hybrid (BM25 + semántico), Python fallback |
| **Selector de idioma** | auto, de, es, en — activa thesaurus correspondiente |
| **Estadísticas** | Documentos indexados, health del cluster, tamaño del índice |
| **Botón Clean** | Borra todos los documentos del índice vía `DELETE /_delete_by_query` |
| **Query directa** | Input de endpoint + cuerpo JSON para llamadas directas a ES |
| **Timestamps SRT** | En resultados de subtítulos, muestra `⏱ 0:42` clicable que copia el tiempo |
| **Raw JSON** | Toggle para ver la respuesta completa de la API |

### Para probar con la carpeta `test/`

```bash
# Desde _Course_catalog_Generator/:
python es_indexer.py --path "../test" --reset

# Luego abrir es_test.html en el navegador
# Buscar p.ej.: "Installation", "workflow", "Benutzer"
```

### Conexión

Los campos de URL en la sidebar son editables:

- **Backend URL**: `http://127.0.0.1:5000` (Flask)
- **Elasticsearch URL**: `http://127.0.0.1:9200` (Docker)

---

## 12. Docker Compose

### Servicios

```yaml
services:
  backend:        # Flask API — puerto 5000
  elasticsearch:  # ES 8.12.0 — puerto 9200
  indexer:        # one-shot, incremental
  kibana:         # opcional, --profile kibana — puerto 5601
```

### Comandos útiles

```bash
# Arrancar todo
docker compose up -d

# Arrancar con Kibana
docker compose --profile kibana up -d

# Ver estado
docker compose ps

# Logs en tiempo real
docker compose logs -f backend
docker compose logs -f elasticsearch

# Parar (mantiene datos)
docker compose down

# Parar y borrar datos de ES (volumen)
docker compose down -v

# Indexado manual
docker compose run --rm indexer                                   # incremental
docker compose run --rm indexer python es_indexer.py --reset      # completo
docker compose run --rm indexer python es_indexer.py --clean --yes # limpiar

# Rebuildar imagen tras cambios en código
docker compose up -d --build
```

### Variables de entorno (`.env`)

```bash
# Copiar plantilla
cp .env.example .env
```

| Variable | Default | Descripción |
|---|---|---|
| `COURSES_PATH` | *(requerido)* | Ruta al directorio de cursos |
| `PORT` | `5000` | Puerto del backend Flask |
| `ES_PORT` | `9200` | Puerto de Elasticsearch |
| `KIBANA_PORT` | `5601` | Puerto de Kibana |
| `DEBUG` | `false` | Modo debug de Flask |
| `MAX_RESULTS` | `50` | Máximo de resultados por búsqueda |
| `MAX_FILE_SIZE_MB` | `50` | Tamaño máximo de archivo a indexar |
| `ES_INDEX` | `course_catalog` | Nombre del índice ES |

---

## 13. Configuración — env.py y .env

### `env.py` — configuración local (sin Docker)

```python
SEARCH_BASE_PATH = r"C:\Users\Me\Videos"

HOST  = "0.0.0.0"
PORT  = 5000
DEBUG = True

MAX_RESULTS         = 50
MAX_MATCHES_PER_FILE = 5
CONTEXT_LINES       = 2
MAX_FILE_SIZE_MB    = 50

VLC_PATH = r"C:\Portable\VLCPortable\VLCPortable.exe"

# Dejar vacío para deshabilitar ES.
# Si se deja vacío, el backend intenta http://127.0.0.1:9200 automáticamente.
ES_URL   = ""
ES_INDEX = "course_catalog"

SEARCHABLE_EXTENSIONS = {
    "txt", "md", "csv", "srt", "vtt", "log",
    "js", "ts", "css", "html", "htm", "xml", "json",
    "py", "java", "cpp", "c", "h", "cs", "go", "rs",
    "pdf", "docx", "doc", "rtf", "xlsx", "xls", "pptx", "ppt", "odt",
}
```

El campo `ES_URL` se puede dejar vacío: el backend detectará automáticamente si Elasticsearch está corriendo en `127.0.0.1:9200`.

---

## 14. Referencia de API

### `GET /api/search`

Búsqueda unificada. Usa ES si está disponible, Python en caso contrario.

```
/api/search?q=Installation&lang=de&limit=20
```

| Parámetro | Tipo | Descripción |
|---|---|---|
| `q` | string | Término de búsqueda (requerido, mín. 2 chars) |
| `lang` | `de`/`es`/`en`/`` | Idioma para normalización y thesaurus |
| `soundex` | `0`/`1` | Activa matching fonético (sólo Python) |
| `cs` | `0`/`1` | Case-sensitive (sólo Python) |
| `cached` | `0`/`1` | Usa caché de texto (sólo Python) |
| `limit` | int | Máximo resultados (default 50, máx. 200) |
| `types` | string | Filtrar por extensión, p.ej. `pdf,srt` |

**Respuesta:**
```json
{
  "query": "Installation",
  "total": 14,
  "results": [
    {
      "path": "/cursos/ELO/setup.pdf",
      "file": "setup.pdf",
      "rel_path": "ELO/setup.pdf",
      "type": "pdf",
      "score": 3.142,
      "match_count": 2,
      "matches": [
        {
          "line": 0,
          "context": "**Installation** des ELO Servers…",
          "highlight": "Installation",
          "score": 3.142
        }
      ]
    }
  ],
  "mode": { "lang": "de", "engine": "elasticsearch" },
  "elapsed_s": 0.04
}
```

Para archivos SRT, cada match incluye `srt_time` (segundos):
```json
{ "line": 3, "context": "…", "srt_time": 42.5 }
```

### `GET /api/es-search`

Siempre usa Elasticsearch. Acepta los mismos parámetros que `/api/search` más `semantic=1`.

### `GET /api/health`

```json
{
  "status": "ok",
  "base_path": "/cursos",
  "elasticsearch": "ok",
  "supported_extensions": ["csv", "docx", "html", ...]
}
```

### `GET/POST /api/cache`

Construye/actualiza la caché de texto para todos los archivos. Útil antes de búsquedas Python en colecciones grandes.

### `GET /api/file?path=...`

Sirve un archivo (debe estar dentro de `SEARCH_BASE_PATH`).

### `GET /api/file-text?path=...`

Devuelve el texto extraído de un archivo para visualización.

### `GET /api/play?srt=...&time=...`

Lanza VLC con el vídeo asociado al SRT en el timestamp indicado (segundos).

### `GET /api/play-video?path=...&time=...`

Lanza VLC con un vídeo directamente.

---

## 15. Referencia de parámetros CLI

### `course_catalog_generator.py`

```bash
python course_catalog_generator.py <courses_dir> [--depth N] [--output DIR] [--force-youtube]
```

| Parámetro | Default | Descripción |
|---|---|---|
| `courses_dir` | *(requerido)* | Directorio raíz de cursos |
| `--depth N` | `2` | Niveles de subdirectorios a escanear |
| `--output DIR` | `.` | Directorio de salida para HTML/MD |
| `--force-youtube` | false | Embed YouTube en lugar de vídeo local |

Al iniciarse, pregunta si arrancar Docker con Elasticsearch.

### `catalog_server.py`

```bash
python catalog_server.py [--host HOST] [--port PORT] [--path PATH] [--debug]
```

### `es_indexer.py`

```bash
python es_indexer.py [--path PATH] [--es-url URL] [--index NAME]
                     [--reset] [--clean] [--yes] [--no-vectors] [--batch N]
```

---

## 16. Solución de problemas

### ES no aparece en la consola de pruebas

1. Verificar que el contenedor está corriendo: `docker compose ps`
2. Comprobar health: `curl http://127.0.0.1:9200/_cluster/health`
3. Si el estado es `red`, revisar logs: `docker compose logs elasticsearch`
4. En WSL2 (Windows), asegurarse de que Docker Desktop está en ejecución.

### El índice tiene documentos pero la búsqueda devuelve 0 resultados

El mapping con thesaurus y los nuevos campos sólo se aplica al crear el índice. Si el índice existía antes de la actualización:

```bash
python es_indexer.py --reset
```

### El thesaurus no funciona (p.ej. buscar "film" no encuentra "video")

Asegurarse de pasar `lang=de` (o el idioma correspondiente) en la petición. El thesaurus sólo se activa con el analizador de idioma específico:

```
/api/search?q=film&lang=de   ✓ activa german_search (con thesaurus)
/api/search?q=film           ✗ usa analizador standard (sin thesaurus)
```

### Los archivos SRT no muestran timestamps

Verificar que el archivo SRT tiene formato estándar con líneas de timestamp:
```
1
00:00:42,500 --> 00:00:47,100
Texto del subtítulo
```

El indexador también acepta `.vtt` y maneja etiquetas HTML dentro del texto (`<i>`, `<b>`, etc.).

### Error "index_not_found_exception" al buscar

El índice todavía no existe. Ejecutar:

```bash
python es_indexer.py --reset
```

### Docker no arranca en Windows

- Abrir Docker Desktop manualmente desde el menú inicio.
- Verificar que WSL2 está habilitado: `wsl --status`
- Docker Desktop requiere Windows 10 v2004+ o Windows 11.

### Memoria insuficiente para Elasticsearch

Ajustar en `docker-compose.yml`:
```yaml
environment:
  - ES_JAVA_OPTS=-Xms256m -Xmx512m   # reducir si hay poca RAM
```

Mínimo recomendado: 2 GB de RAM disponible para ES.

---

## Estructura de archivos

```
_Course_catalog_Generator/
├── course_catalog_generator.py   # Generador de catálogo HTML/MD
├── catalog_server.py             # Backend Flask (búsqueda, API)
├── es_indexer.py                 # Indexador Elasticsearch
├── es_test.html                  # Consola de pruebas ES (abrir en navegador)
├── docker-compose.yml            # Stack Docker (ES + backend + indexer)
├── Dockerfile                    # Imagen del backend
├── env.py                        # Configuración local (sin Docker)
├── env_docker.py                 # Configuración Docker
├── .env.example                  # Plantilla de variables de entorno Docker
├── requirements.txt              # Dependencias Python
├── synonyms/
│   ├── synonyms_en.txt           # Sinónimos inglés (referencia)
│   ├── synonyms_es.txt           # Sinónimos español (referencia)
│   └── synonyms_de.txt           # Sinónimos alemán (referencia)
├── start.bat                     # Inicio nativo Windows (con check Docker)
├── start.sh                      # Inicio nativo Linux/macOS (con install Docker)
├── start-docker.bat              # Inicio Docker Windows
├── start-docker.sh               # Inicio Docker Linux/macOS
├── cache_temporal/               # Caché de texto extraído
└── ELASTICSEARCH_README.md       # Este documento
```

---

## Retrieval quality (hybrid + reranker + German)

Pipeline for `/api/ask`: **LLM turns the question into search terms and translates them to German →
BM25 + vector search for each query → RRF fusion → cross-encoder reranker → top-k to the LLM.**

| Setting (`env.py` or environment variable) | Default | Meaning |
|---|---|---|
| `RERANK_ENABLED` | `True` | Cross-encoder reranking of the fused candidates |
| `RERANKER_MODEL` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Multilingual, ~470 MB, ~3 s for 25 passages on CPU. Heavier option: `BAAI/bge-reranker-v2-m3` (~2.2 GB, ~56 s on CPU — GPU only) |
| `RERANK_CANDIDATES` | `25` | Candidates sent to the reranker |
| `RAG_TRANSLATE_QUERY_TO` | `de` | Languages the search terms are translated into (`de,en`; empty = off). Needs an LLM (two-stage on) |
| `DE_DECOMPOUND_WORDS` | – | Extra German base words for compound splitting (index time, needs `--reset`) |
| `INDEX_EXCLUDE_PATTERNS` | – | Extra glob patterns to skip, `;`-separated (CLI: `--exclude`) |
| `NO_VECTOR_EXTENSIONS` | – | Extra extensions indexed BM25-only (default: json, xml, js, ts, css) |
| `MAX_VECTOR_CHUNKS_PER_FILE` | `400` | Bigger files are indexed BM25-only |

The reranker loads in a background thread; until it is ready, results keep the hybrid order.

**Index changes (need a full `--reset` re-index to take effect):** every chunk now stores `title`,
`section` (Markdown heading path) and `collection` (first folder); `content.de_c` splits German
compounds (*Schulungsunterlage* → *Schulung* + *Unterlage*); embeddings are computed on
`"<title > section>\n<chunk>"` (the stored `content` is unchanged). Markdown is chunked along its headings.
Older indexes keep working: the missing fields are simply ignored at query time.

**File viewer:** the modal shows the absolute path where the file is looked up, and explains
*why* a file cannot be shown (not found on this machine / outside the allowed base paths) via `GET /api/file-info`.

---

## Tools UI: saved folders, models & containers

**Saved folders** (Tools › Index Directory). Every scanned folder is stored in `data/scan_registry.json`
(server side, survives restarts; the list is also cached in the browser's localStorage). Per folder:
`↻` incremental re-scan with the options it was scanned with · `✎` change location (see below) · `🗑` remove
(optionally also delete its chunks). `＋ Save` adds a folder without scanning it. Folders already in
`SEARCH_BASE_PATH` are imported automatically; folders you remove are not re-imported.

*Change location* (folder moved on disk): rewrites the `path` of every indexed chunk under the old folder
(boundary-aware: `Kurse` never touches `Kurse2`; text and vectors are untouched), then updates `env.py`, the
viewer's allowed paths and the registry — in that order, so a failure leaves nothing half-applied.

**Incremental scans compare content, not just dates.** A file with a different date but the same SHA-256
(copied, synced, restored from a backup) keeps its chunks and only gets its stored date refreshed.
(Earlier versions deleted such files from the index.)

**Models & containers** (Tools › Models & containers): Docker containers serving Elasticsearch/Ollama (with
index size, vector dimensions and the Docker volume holding the data); installed Ollama models with size,
cloud/local, *loaded in memory*, position in the answer chain (`answers #n`), translation model and your
`★ chat/ask` selection — with Use / Unload / Delete; plus the embedding and reranker models (cached size, state).

## Moving the index to another machine

The index lives in the Docker **volume** `deploy_es_data`, not inside the container, so `docker commit` /
`docker save` do **not** include it. Options (same Elasticsearch version, 8.12.0, on both sides):

1. *Volume copy* (simplest). Source: `docker stop course-catalog-es`, then
   `docker run --rm -v deploy_es_data:/data -v "${PWD}:/backup" alpine tar czf /backup/es_data.tgz -C /data .`
   Target: create the volume, unpack the same way (`tar xzf` into `/data`), then `docker compose up -d`.
   The compose project must be named `deploy` on the target (folder name, or `COMPOSE_PROJECT_NAME=deploy`).
2. *Snapshot API*: add `path.repo` to `elasticsearch.yml` + a mounted folder, register an `fs` repository,
   take a snapshot, copy the folder, restore. More robust across minor versions.
3. *Logical export*: `tests/backup_es.py` / `restore_es.py` (NDJSON.gz incl. vectors; edit their `/tmp` paths on Windows).

After importing: use *Indexed Paths Manager › Replace path prefix* to point the other machine's folders to the
local ones, keep **`EMBEDDING_MODEL` identical** on both machines (queries must be embedded with the model that
built the index, and the mapping dimensions must match — unknown models silently default to 384 dims).
