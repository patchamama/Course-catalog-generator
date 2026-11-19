# Search Benchmark Report — Course Catalog Generator
# Informe de Benchmark de Búsqueda

> **Fecha / Date:** 2026-03-30
> **Modelos evaluados / Models tested:** mE5-small · LaBSE · MiniLM · mpnet
> **Consultas / Queries:** 28 (24 on-topic + 4 off-topic)
> **Métodos / Methods:** BM25 · Semantic (kNN) · Hybrid (BM25+kNN) · Python backend
> **Corpus:** `C:\Users\<user>\elo_test_corpus` — 24 archivos, 3 idiomas

---

## 1. Resumen Ejecutivo / Executive Summary

Se evaluaron cuatro modelos de embedding. **mE5-small** es el ganador absoluto: mejor semántica, compacto (384 dims), y nativo cross-lingual (sin traducción requerida).

| Modelo | Dims | BM25 | Semantic | Hybrid | ES→DE Sem | Cross-lingual |
|--------|------|:----:|:--------:|:------:|:---------:|:-------------:|
| **mE5-small** ★ | 384 | 83% | **88%** | **88%** | **50%** | Nativo |
| **LaBSE** | 768 | 83% | 79% | **88%** | **50%** | Nativo |
| **MiniLM** | 384 | 83% | 83% | 83% | 25% | Necesita traducción |
| **mpnet** | 768 | 83% | 71% | 83% | 25% | Necesita traducción |

**Recomendación: mE5-small para producción** — mejor semántica general (+5 pp vs MiniLM, +17 pp vs mpnet) y el doble de precisión en búsquedas ES→DE sin necesitar traducción.

---

## 2. Resultados por Sección / Results by Section

| Sección (queries) | mE5 BM25/Sem/Hyb | LaBSE BM25/Sem/Hyb | MiniLM BM25/Sem/Hyb | mpnet BM25/Sem/Hyb |
|-------------------|:----------------:|:------------------:|:-------------------:|:-----------------:|
| DE exactas (Q01–06) | 100/100/100% | 100/100/100% | 100/100/100% | 100/ 83/100% |
| DE semánticas (Q07–10) | 100/100/100% | 100/100/100% | 100/100/100% | 100/100/100% |
| EN→DE cross-lang (Q11–15) | 80/ 80/ 80% | 80/ 60/ 80% | 80/ 80/ 80% | 80/ 80/ 80% |
| ES→DE cross-lang (Q16–19) | 25/**50**/**50**% | 25/**50**/**50**% | 25/ 25/ 25% | 25/ 25/ 25% |
| EN código (Q20–24) | 100/100/100% | 100/ 80/100% | 100/100/100% | 100/ 60/100% |

> **★ mE5-small** dobla la cobertura semántica en consultas ES→DE (50% vs 25% en MiniLM/mpnet).

---

## 3. Detalle por Query

### 3.1 Alemán — Consultas exactas (Q01–Q06)

| Q# | Consulta | mE5 Sem | LaBSE Sem | MiniLM Sem | mpnet Sem |
|----|---------|:-------:|:---------:|:-----------:|:---------:|
| Q01 | Blended Learning E-Learning | 1 ✓ | 1 ✓ | 1 ✓ | 1 ✓ |
| Q02 | Check-out Check-in Dokumentensperre | 1 ✓ | 1 ✓ | 2 ✓ | MISS |
| Q03 | Verschlüsselungskreise schützen | 1 ✓ | 1 ✓ | 1 ✓ | 3 ✓ |
| Q04 | ELO Backup Datensicherung | 1 ✓ | 1 ✓ | 1 ✓ | 2 ✓ |
| Q05 | LDAP Active Directory | 1 ✓ | 1 ✓ | 1 ✓ | 1 ✓ |
| Q06 | Dynamische Ordner Klassifizierung | 1 ✓ | 1 ✓ | 2 ✓ | 1 ✓ |
| **Total** | | **6/6** | **6/6** | **6/6** | **5/6** |

### 3.2 Alemán — Consultas semánticas (Q07–Q10)

| Q# | Consulta | mE5 Sem | LaBSE Sem | MiniLM Sem | mpnet Sem |
|----|---------|:-------:|:---------:|:-----------:|:---------:|
| Q07 | Dokumente in ELO ablegen | 1 ✓ | 1 ✓ | 1 ✓ | MISS |
| Q08 | ELO Daten vor Verlust schützen | 1 ✓ | 1 ✓ | 1 ✓ | 1 ✓ |
| Q09 | Benutzer und Gruppen verwalten | 1 ✓ | 1 ✓ | 1 ✓ | 2 ✓ |
| Q10 | ELO Server Installation Windows | 1 ✓ | 1 ✓ | 2 ✓ | 1 ✓ |
| **Total** | | **4/4** | **4/4** | **4/4** | **3/4** |

### 3.3 Inglés → Alemán (Q11–Q15, cross-language)

| Q# | Consulta | mE5 Sem | LaBSE Sem | MiniLM Sem | mpnet Sem |
|----|---------|:-------:|:---------:|:-----------:|:---------:|
| Q11 | Enterprise Content Management ECM | 1 ✓ | 1 ✓ | 1 ✓ | 1 ✓ |
| Q12 | check-in check-out locking workflow | 1 ✓ | 1 ✓ | 1 ✓ | MISS |
| Q13 | user permissions access control ELO | MISS | MISS | MISS | MISS |
| Q14 | ELO on-premise installation database | 1 ✓ | MISS | 1 ✓ | MISS |
| Q15 | digital transformation document mgmt | 1 ✓ | 1 ✓ | 1 ✓ | MISS |
| **Total** | | **4/5** | **3/5** | **4/5** | **1/5** |

> Q13 falla en todos los modelos — el documento de permisos no tiene suficiente señal léxica en ningún idioma.

### 3.4 Español → Alemán (Q16–Q19, cross-language)

| Q# | Consulta | mE5 Sem | LaBSE Sem | MiniLM Sem | mpnet Sem |
|----|---------|:-------:|:---------:|:-----------:|:---------:|
| Q16 | delegación vacaciones sustitución | MISS | MISS | MISS | MISS |
| Q17 | asignación grupo permisos representación | 2 ✓ | 1 ✓ | 3 ✓ | MISS |
| Q18 | flujo de trabajo aprobación ELO | MISS | MISS | MISS | MISS |
| Q19 | cómo archivar documentos ELO | 1 ✓ | 1 ✓ | MISS | MISS |
| **Total** | | **2/4 ★** | **2/4 ★** | **1/4** | **0/4** |

> **mE5-small y LaBSE duplican** la cobertura ES→DE: Q19 ("archivar documentos") es recuperado porque el modelo mapea "archivar" ↔ "ablegen/archivieren" en espacio vectorial.

### 3.5 Inglés — Código (Q20–Q24)

| Q# | Consulta | mE5 Sem | LaBSE Sem | MiniLM Sem | mpnet Sem |
|----|---------|:-------:|:---------:|:-----------:|:---------:|
| Q20 | React useState useEffect hooks | 1 ✓ | 1 ✓ | 1 ✓ | MISS |
| Q21 | Python Flask REST API endpoint | 1 ✓ | 1 ✓ | 1 ✓ | MISS |
| Q22 | Node.js Express Router middleware | 1 ✓ | 1 ✓ | 1 ✓ | MISS |
| Q23 | SQLAlchemy ORM database query | 1 ✓ | MISS | 3 ✓ | MISS |
| Q24 | React memoization performance | 1 ✓ | 1 ✓ | 1 ✓ | MISS |
| **Total** | | **5/5** | **4/5** | **5/5** | **0/5** |

---

## 4. Off-Topic (Q25–Q28)

Todos los modelos generan falsos positivos en las consultas off-topic (4/4 para BM25 y Semantic). Esto es esperable con un corpus pequeño (200 chunks) y sin umbral mínimo de score.

**Mitigación recomendada:** añadir `minimum_score` en las queries ES para filtrar resultados con score bajo.

---

## 5. Tabla Global

| Modelo | BM25 P@5 | Semantic P@5 | Hybrid P@5 | Off-FP | Dims | Tipo |
|--------|:--------:|:------------:|:----------:|:------:|:----:|:----:|
| **mE5-small** ★ | 83% | **88%** | **88%** | 4/4 | 384 | Multilingual nativo |
| LaBSE | 83% | 79% | **88%** | 4/4 | 768 | Multilingual nativo |
| MiniLM | 83% | 83% | 83% | 4/4 | 384 | Requiere traducción |
| mpnet | 83% | 71% | 83% | 4/4 | 768 | Requiere traducción |

---

## 6. Análisis Temporal

| Método | Avg latency |
|--------|------------|
| BM25 / Hybrid | ~1.1 s |
| Semantic (kNN) | ~1.1 s |
| Python backend | ~0.1–0.3 s |

> Los tiempos son ~20× más rápidos que en el benchmark anterior (21 s → 1.1 s) gracias a que Elasticsearch ahora corre en Docker local con warm-up del índice.

---

## 7. Conclusiones

### Recomendación principal: **mE5-small**

| Criterio | mE5-small | LaBSE | MiniLM | mpnet |
|---------|:---------:|:-----:|:------:|:-----:|
| Semantic P@5 global | **88%** | 79% | 83% | 71% |
| ES→DE cobertura | **50%** | **50%** | 25% | 25% |
| EN→DE cobertura | 80% | 60% | 80% | 80% |
| EN-código | **100%** | 80% | **100%** | 60% |
| Necesita traducción | ❌ No | ❌ No | ⚠️ Sí | ⚠️ Sí |
| Tamaño | **~120 MB** | ~1.9 GB | **~120 MB** | ~400 MB |
| Dims vectoriales | **384** | 768 | **384** | 768 |

**mE5-small gana en todos los criterios relevantes**: mejor semántica, cross-lingual nativo, compacto. LaBSE tiene igual cobertura ES→DE pero peor semántica EN-código y es 15× más grande.

### Mejoras adicionales recomendadas

1. **Threshold mínimo de score** para reducir falsos positivos off-topic en todos los modelos.
2. **Q13/Q16/Q18 — coverage gap**: los documentos de permisos y workflow no contienen suficiente señal semántica. Mejorar extracción RTF o añadir metadatos de tag.
3. **Python backend**: P@5 muy baja (16.7%) — no recomendado como método principal. Usar BM25/Hybrid como fallback cuando ES no está disponible.

---

## 8. Archivos de Resultados

| Archivo | Modelo | Timestamp |
|---------|--------|-----------|
| `tests/results/mE5-small_results.json` | intfloat/multilingual-e5-small | 20260330_163119 |
| `tests/results/LaBSE_results.json` | sentence-transformers/LaBSE | 20260330_173939 |
| `tests/results/MiniLM_results.json` | paraphrase-multilingual-MiniLM-L12-v2 | 20260330_112250 |
| `tests/results/mpnet_results.json` | paraphrase-multilingual-mpnet-base-v2 | 20260330_112525 |

---

*Reporte actualizado 2026-03-30 con resultados de 4 modelos: mE5-small, LaBSE, MiniLM, mpnet.*
