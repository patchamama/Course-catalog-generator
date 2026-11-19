# Plan de Pruebas de Búsqueda — Course Catalog Generator
# Search Testing Plan — Course Catalog Generator

> **Fecha / Date:** 2026-03-30
> **Estado / Status:** Completado / Completed
> **Redactado por / Drafted by:** Claude (asistente AI)

---

## Objetivo / Objective

Evaluar y comparar la calidad de búsqueda del sistema **Course Catalog Generator** usando:

1. **Dos modelos de embeddings**: MiniLM (384 dims) vs mpnet (768 dims)
2. **Cuatro métodos de búsqueda**: BM25, Semántico, Híbrido, Python-backend
3. **Tres idiomas**: Alemán (de), Inglés (en), Español (es)
4. **Cuatro tipos de consulta**: exacta, semántica, off-topic (sin resultados esperados)

---

## Corpus de Prueba / Test Corpus

### Ubicación / Location

```
C:\Users\<user>\elo_test_corpus\
```

### Archivos indexados / Indexed files

| Archivo | Formato | Idioma | Tema |
|---------|---------|--------|------|
| elo_01_einfuehrung.md | Markdown | DE | ELO Introducción / Grundlagen |
| elo_01_einfuehrung.html | HTML | DE | ELO Introducción / Grundlagen |
| elo_01_einfuehrung.pdf | PDF | DE | ELO Introducción / Grundlagen |
| elo_02_client.md | Markdown | DE | ELO Client — documentos, check-in/out |
| elo_02_client.pdf | PDF | DE | ELO Client — documentos, check-in/out |
| elo_03_technologien.html | HTML | DE | ELO Technologien |
| elo_03_technologien.pdf | PDF | DE | ELO Technologien |
| elo_05_vertiefungstraining.html | HTML | DE | ELO Vertiefungstraining |
| elo_06_onprem.md | Markdown | DE | ELO On-Premise instalación |
| elo_backup_procedures.doc | DOC | DE | Procedimientos de backup ELO |
| elo_betriebshandbuch.docx | DOCX | DE | Manual de operación ELO |
| elo_infrastrukturkonzept.docx | DOCX | DE | Concepto de infraestructura |
| elo_installation_guide.doc | DOC | DE | Guía de instalación ELO |
| elo_training_tag1.srt | SRT | ES | Formación ELO Día 1 (subtítulos) |
| elo_training_tag2.srt | SRT | ES | Formación ELO Día 2 (subtítulos) |
| elo_training_tag3.srt | SRT | ES | Formación ELO Día 3 (subtítulos) |
| elo_user_management.rtf | RTF | DE | Gestión de usuarios ELO |
| elo_workflow_documentation.rtf | RTF | DE | Documentación de workflow ELO |
| nodejs_express_guide.doc | DOC | EN | Node.js Express — guía programación |
| nodejs_express_guide.md | Markdown | EN | Node.js Express — guía programación |
| python_flask_api.md | Markdown | EN | Python Flask REST API |
| python_flask_api.rtf | RTF | EN | Python Flask REST API |
| react_hooks_guide.docx | DOCX | EN | React Hooks — guía programación |
| react_hooks_guide.md | Markdown | EN | React Hooks — guía programación |

**Total**: 24 archivos de contenido + 1 JSON de consultas = 25 archivos en total

### Distribución por formato / Format distribution

| Formato | Cantidad |
|---------|----------|
| Markdown (.md) | 6 |
| HTML (.html) | 4 |
| PDF (.pdf) | 4 |
| DOC (.doc) | 3 |
| DOCX (.docx) | 3 |
| RTF (.rtf) | 3 |
| SRT (.srt) | 3 |

> ✅ Cumple con el requisito de al menos 3 archivos por cada tipo de formato.

---

## Batería de Consultas / Query Battery

### Archivo de consultas / Query file

```
test_queries.json  (28 consultas / 28 queries)
```

### Distribución de consultas / Query distribution

| Categoría | Cantidad |
|-----------|----------|
| Exactas en alemán (de/exact) | 6 |
| Semánticas en alemán (de/semantic) | 4 |
| Semánticas en inglés (en/semantic) | 5 |
| Exactas en inglés (en/exact) — código | 5 |
| Exactas/semánticas en español (es/exact + es/semantic) | 4 |
| Off-topic sin resultados (off-topic) | 4 |
| **Total** | **28** |

### Tipos de consulta / Query types

1. **Exactas**: Términos que aparecen textualmente en los documentos
2. **Semánticas**: Preguntas con significado equivalente pero vocabulario diferente
3. **Off-topic**: Consultas sobre temas no indexados (astronomía, cocina) — no deben dar resultados
4. **Código**: Búsquedas de APIs, funciones y frameworks de programación (React, Python, Node.js)
5. **Multiidioma**: Consultas en inglés/español sobre documentos en alemán

---

## Modelos Evaluados / Models Evaluated

### MiniLM
- **Nombre completo**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- **Proveedor**: HuggingFace / fastembed
- **Dimensiones vectoriales**: 384
- **Tamaño**: ~120 MB
- **Normalización**: lowercase en texto antes de embedding
- **Índice ES temporal**: `course_catalog_test_minilm`

### mpnet
- **Nombre completo**: `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`
- **Proveedor**: HuggingFace / fastembed
- **Dimensiones vectoriales**: 768
- **Tamaño**: ~400 MB
- **Normalización**: sin normalización de mayúsculas
- **Índice ES usado**: `course_catalog` (producción) / `course_catalog_test_mpnet` (prueba)

---

## Métodos de Búsqueda / Search Methods

### 1. BM25 (Elasticsearch directo)
- Query `multi_match` sobre campos `content`, `content.de`, `content.en`, `content.es`
- Fuzzy matching (`fuzziness: AUTO`)
- Sin vectores/embeddings — búsqueda léxica pura
- **No depende del modelo de embedding**

### 2. Semántico (kNN Elasticsearch)
- Endpoint: `/api/es-search?semantic=1`
- Búsqueda por similitud vectorial (cosine)
- Depende del modelo de embedding configurado
- **Requiere índice con vectores del modelo correcto**

### 3. Híbrido (BM25 + kNN)
- Endpoint: `/api/es-search?hybrid=1`
- Combina multi_match + kNN con boost para el vector
- **Mejor de ambos mundos: léxico + semántico**

### 4. Python (backend /api/search)
- Endpoint: `/api/search`
- Búsqueda textual del backend usando ES BM25 internamente
- Recorre los paths base configurados en `env.py`
- **Resultado independiente del modelo de embedding**

---

## Métricas de Evaluación / Evaluation Metrics

| Métrica | Descripción |
|---------|-------------|
| **P@5 (Precisión en top 5)** | % de consultas donde el documento esperado aparece en los primeros 5 resultados |
| **Rank promedio** | Posición media del documento esperado cuando se encuentra |
| **Tiempo de respuesta** | Tiempo promedio de cada consulta en segundos |
| **Tasa de falsos positivos** | % de consultas off-topic que devuelven resultados (idealmente 0%) |

---

## Procedimiento de Prueba / Test Procedure

### Fase 1: Preparación del corpus

1. Se seleccionaron documentos de prueba de `C:\Users\<user>\Videos\_Catalog`
2. Se crearon/convirtieron documentos en todos los formatos requeridos
3. Se generaron preguntas basadas en el contenido real de los documentos
4. Se incluyeron preguntas de código (React, Python Flask, Node.js Express)
5. Se añadieron preguntas multiidioma (búsqueda en inglés/español de contenido alemán)

### Fase 2: Backup del índice de producción

```bash
python tests/backup_es.py
# Salida: /tmp/es_backup_course_catalog.ndjson.gz
#         /tmp/es_backup_settings.json
```

### Fase 3: Indexación de corpus de prueba

```bash
# Para mpnet (producción, ya configurado)
python backend/search_indexer.py --reset --path C:\Users\<user>\elo_test_corpus

# Para MiniLM (índice temporal)
# Manejado automáticamente por tests/compare_models.py
```

### Fase 4: Ejecución de benchmarks

```bash
# Comparación directa MiniLM vs mpnet (script autónomo)
.venv/Scripts/python.exe tests/compare_models.py

# Benchmark individual con el backend de producción (mpnet)
python tests/run_tests.py --model mpnet
```

### Fase 5: Análisis de resultados

```bash
python tests/compare_models.py --skip-index  # solo análisis
```

---

## Enfoque en Búsquedas Multiidioma / Focus: Cross-language Search

Un objetivo clave es evaluar qué método funciona mejor cuando:
- La **consulta** está en inglés o español
- El **contenido indexado** está principalmente en alemán

Para este escenario se crearon específicamente las consultas Q11-Q15 (en/semantic) y Q16-Q19 (es).

**Hipótesis**: Los modelos multilingüe (ambos son `paraphrase-multilingual-*`) deberían
manejar bien este cruce de idiomas. El ranking comparativo entre MiniLM y mpnet
en estas consultas es clave para la recomendación final.

---

## Archivos de Resultados / Results Files

```
tests/results/
├── MiniLM_results.json         # Últimas pruebas con MiniLM (índice temporal)
├── MiniLM_results_*.json       # Histórico de pruebas MiniLM
├── mpnet_results.json          # Pruebas con mpnet (índice producción)
├── mpnet_results_*.json        # Histórico de pruebas mpnet
└── ...
```

---

## Archivo de Reporte / Report File

Ver: [`SEARCH_BENCHMARK_REPORT.md`](SEARCH_BENCHMARK_REPORT.md)

---

*Plan creado como guía para la evaluación comparativa de métodos de búsqueda en el Course Catalog Generator.*
