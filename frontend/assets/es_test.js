// ── Config ──────────────────────────────────────────────────────────────────
let SEARCH_MODE = 'es-semantic';

function esUrl()     { return (document.getElementById('es-url').value     || 'http://127.0.0.1:9200').replace(/\/$/, ''); }
function ollamaUrl() { return (document.getElementById('ollama-url')?.value || 'http://localhost:11434').replace(/\/$/, ''); }
function indexName() { return 'course_catalog'; }

// ── Logging ─────────────────────────────────────────────────────────────────
function log(msg, type = 'info') {
  const el = document.getElementById('log');
  const e  = document.createElement('div');
  e.className = 'log-entry ' + type;
  const ts = new Date().toTimeString().slice(0, 8);
  e.textContent = `[${ts}] ${msg}`;
  el.appendChild(e);
  el.scrollTop = el.scrollHeight;
}

// ── Status checks ────────────────────────────────────────────────────────────
async function checkES() {
  const dot = document.getElementById('dot-es');
  const lbl = document.getElementById('lbl-es');
  try {
    const r = await fetch(esUrl() + '/_cluster/health', { signal: AbortSignal.timeout(3000) });
    const d = await r.json();
    const s = d.status;
    dot.className = 'dot ' + (s === 'green' ? 'green' : s === 'yellow' ? 'yellow' : 'red');
    lbl.textContent = 'ES: ' + s;
    log('ES health: ' + s, s === 'red' ? 'error' : 'ok');
    return true;
  } catch(e) {
    dot.className = 'dot red';
    lbl.textContent = 'ES: unreachable';
    log('ES unreachable: ' + e.message, 'error');
    return false;
  }
}

async function checkAPI() {
  const dot = document.getElementById('dot-api');
  const lbl = document.getElementById('lbl-api');
  try {
    const r = await fetch(apiUrl() + '/api/health', { signal: AbortSignal.timeout(3000) });
    const d = await r.json();
    dot.className = 'dot green';
    lbl.textContent = 'API: ok (ES: ' + (d.elasticsearch || '?') + ')';
    _backendOk = true; updateDirectBanner(); setSvcDot('bk', true);
    document.getElementById('svc-start-cmd').style.display = 'none';
    log('Backend API ok', 'ok');
    return true;
  } catch(e) {
    dot.className = 'dot red';
    lbl.textContent = 'API: unreachable';
    _backendOk = false; updateDirectBanner(); setSvcDot('bk', false);
    document.getElementById('svc-start-cmd').style.display = 'flex';
    log('Backend API unreachable: ' + e.message, 'warn');
    return false;
  }
}

async function refreshStats() {
  try {
    const idx = indexName();
    const [cntR, statsR] = await Promise.all([
      fetch(esUrl() + '/' + idx + '/_count', { signal: AbortSignal.timeout(4000) }),
      fetch(esUrl() + '/' + idx + '/_stats/store,docs', { signal: AbortSignal.timeout(4000) })
    ]);
    // Empty / not-yet-created index (ES answers 404) must show 0, not a stale value or "—"
    if (cntR.ok) {
      const c = await cntR.json();
      document.getElementById('stat-docs').textContent = (c.count || 0).toLocaleString();
    } else if (cntR.status === 404) {
      document.getElementById('stat-docs').textContent = '0';
    }
    if (statsR.ok) {
      const s = await statsR.json();
      const bytes = s._all?.total?.store?.size_in_bytes || 0;
      document.getElementById('stat-size').textContent = formatBytes(bytes);
    } else if (statsR.status === 404) {
      document.getElementById('stat-size').textContent = formatBytes(0);
    }
    document.getElementById('stat-index').textContent = idx;
    const hr = await fetch(esUrl() + '/_cluster/health', { signal: AbortSignal.timeout(3000) });
    if (hr.ok) {
      const hd = await hr.json();
      const sh = document.getElementById('stat-health');
      sh.textContent = hd.status;
      sh.style.color = hd.status === 'green' ? 'var(--green)' : hd.status === 'yellow' ? 'var(--yellow)' : 'var(--red)';
    }
    log('Stats refreshed', 'ok');
  } catch(e) {
    log('Stats error: ' + e.message, 'warn');
  }
}

function formatBytes(bytes) {
  if (bytes < 1024)       return bytes + ' B';
  if (bytes < 1048576)    return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1073741824).toFixed(2) + ' GB';
}

// Fetch Ollama models: try backend first (avoids CORS), fall back to direct call.
// Returns { models: [string], source: 'backend'|'direct' } or throws.
async function _fetchOllamaModels() {
  // 1. Try backend proxy (no CORS issues)
  if (_backendOk) {
    try {
      const r = await fetch(apiUrl() + '/api/ollama-models', { signal: AbortSignal.timeout(4000) });
      if (r.ok) {
        const d = await r.json();
        if (d.available) return { models: d.models || [], source: 'backend' };
      }
    } catch(_) { /* fall through */ }
  }
  // 2. Direct Ollama call (works when OLLAMA_ORIGINS=* is set)
  const r = await fetch(ollamaUrl() + '/api/tags', { signal: AbortSignal.timeout(4000) });
  if (!r.ok) throw new Error(`Ollama ${r.status}`);
  const d = await r.json();
  const models = (d.models || []).map(m => (typeof m === 'string' ? m : m.name) || '').filter(Boolean);
  return { models, source: 'direct' };
}

async function checkOllama() {
  const dot = document.getElementById('dot-ollama');
  const lbl = document.getElementById('lbl-ollama');
  try {
    const { models } = await _fetchOllamaModels();
    dot.className = 'dot green';
    const count = models.length;
    lbl.textContent = `Ollama: ${count} model${count !== 1 ? 's' : ''}`;
    log(`Ollama ok — models: ${models.join(', ') || '(none)'}`, 'ok');
    // Populate model selector
    const sel = document.getElementById('ask-model');
    sel.innerHTML = '<option value="">auto (recommended)</option>';
    models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = m;
      sel.appendChild(opt);
    });
    const st = document.getElementById('ollama-model-status');
    st.textContent = count ? `${count} installed` : 'no models';
    st.style.color = count ? 'var(--green)' : 'var(--yellow)';
    document.getElementById('ollama-notice').style.display = 'none';
    // Restore saved model selection (must happen after options are populated)
    const savedModel = localStorage.getItem('cc_ask_model');
    if (savedModel) sel.value = savedModel;
  } catch(e) {
    dot.className = 'dot red';
    lbl.textContent = 'Ollama: —';
    log(`Ollama not reachable (backend + direct failed) — ${e.message}`, 'warn');
    document.getElementById('ollama-model-status').textContent = 'not available';
    document.getElementById('ollama-notice').style.display = 'block';
  } finally {
    _applyExternalLlmToAsk();
  }
}

// When an external LLM (OpenAI / Anthropic / custom) is active, the Ask model list comes from its settings
function _applyExternalLlmToAsk() {
  const st = document.getElementById('ollama-model-status');
  if (!window.LLM || !LLM.isExternal()) return;
  const sel = document.getElementById('ask-model');
  LLM.fillSelect(sel);
  const n = LLM.models().length;
  st.textContent = `${LLM.label()} · ${n ? n + ' models' : 'no models — open ⚙ LLM'}`;
  st.style.color = LLM.model() ? 'var(--green)' : 'var(--yellow)';
  document.getElementById('ollama-notice').style.display = 'none';
}
let _askWasExternal = !!(window.LLM && LLM.isExternal());
window.addEventListener('llm-config-changed', () => {
  const ext = !!(window.LLM && LLM.isExternal());
  if (ext) _applyExternalLlmToAsk();                 // typing in the dialog → no network
  else if (_askWasExternal) checkOllama();           // switched back to Ollama → reload its models
  _askWasExternal = ext;
});
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('ask-model')?.addEventListener('change', e => {
    if (window.LLM && LLM.isExternal()) LLM.setModel(e.target.value);
  });
});

async function refreshStatus() {
  await Promise.all([checkES(), checkAPI()]);
  await Promise.all([refreshStats(), checkOllama(), updateSvcStatus()]);
}

// ── Search mode help ─────────────────────────────────────────────────────────
const HELP_CONTENT = {
  'query-syntax': {
    title: 'Query Syntax — Search Tips & Operators',
    body: `
      <p>The query box accepts plain text or Elasticsearch query strings depending on the selected
      search mode. Below are the main patterns you can use.</p>

      <h5 style="margin:10px 0 4px">Basic searches</h5>
      <div class="ex">installation guide        <span style="opacity:.6">— all docs containing both words</span></div>
      <div class="ex">Benutzer                  <span style="opacity:.6">— single keyword (stemmed with lang=de)</span></div>
      <div class="ex">error handling network    <span style="opacity:.6">— any of these words, ranked by relevance</span></div>

      <h5 style="margin:10px 0 4px">Phrase search</h5>
      <div class="ex">"user authentication"     <span style="opacity:.6">— exact phrase match (BM25 / Python)</span></div>
      <div class="ex">"getting started"         <span style="opacity:.6">— words must appear in this order</span></div>

      <h5 style="margin:10px 0 4px">Boolean operators <small style="opacity:.6">(BM25 / Python)</small></h5>
      <div class="ex">login AND password        <span style="opacity:.6">— both terms required</span></div>
      <div class="ex">install OR setup          <span style="opacity:.6">— either term</span></div>
      <div class="ex">authentication NOT oauth  <span style="opacity:.6">— exclude term</span></div>
      <div class="ex">+required -excluded       <span style="opacity:.6">— + must appear, − must not</span></div>

      <h5 style="margin:10px 0 4px">Wildcards <small style="opacity:.6">(BM25 — avoid leading *)</small></h5>
      <div class="ex">config*                   <span style="opacity:.6">— config, configuration, configure…</span></div>
      <div class="ex">?ser                      <span style="opacity:.6">— user, äser, üser (single char)</span></div>

      <h5 style="margin:10px 0 4px">Fuzzy matching <small style="opacity:.6">(auto-applied in BM25)</small></h5>
      <div class="ex">autentikation~            <span style="opacity:.6">— tolerates 1–2 typos (~ suffix)</span></div>
      <div class="ex">installtion               <span style="opacity:.6">— fuzziness:AUTO catches this</span></div>

      <h5 style="margin:10px 0 4px">Semantic / meaning-based <small style="opacity:.6">(ES Semantic / Hybrid)</small></h5>
      <div class="ex">how to handle errors gracefully   <span style="opacity:.6">— finds try/catch docs</span></div>
      <div class="ex">función que devuelve colores      <span style="opacity:.6">— cross-lang: finds English docs</span></div>
      <div class="ex">Vorlage erstellen                 <span style="opacity:.6">— "template" docs in any language</span></div>

      <h5 style="margin:10px 0 4px">Filename search mode</h5>
      <div class="ex">*.srt                     <span style="opacity:.6">— all subtitle files</span></div>
      <div class="ex">chapter*                  <span style="opacity:.6">— files starting with "chapter"</span></div>
      <div class="ex">*2024*                    <span style="opacity:.6">— name/path contains "2024"</span></div>

      <h5 style="margin:10px 0 4px">Type-only search <small style="opacity:.6">(leave query empty)</small></h5>
      <p style="margin:4px 0">Select file types in <strong>Filter by file type</strong> and leave the query
      blank — returns all indexed files of those types (BM25 / Filename modes).</p>

      <h5 style="margin:10px 0 4px">Language tip</h5>
      <p style="margin:4px 0">Set <strong>Language</strong> to <code>de</code>, <code>es</code>, or <code>en</code>
      to activate the corresponding stemmer and stop-word list (BM25 only). Leave on <em>auto</em>
      for multilingual corpora or when using Semantic / Hybrid modes.</p>
    `
  },
  'es-bm25': {
    title: 'ES BM25 — Elasticsearch Full-Text Search',
    body: `
      <p><strong>BM25</strong> (Best Match 25) is the probabilistic ranking algorithm used by
      Elasticsearch. It scores documents by term frequency and inverse document frequency,
      rewarding matches where the query terms appear frequently in a document but rarely
      across the whole corpus.</p>
      <p><strong>Language-aware analyzers</strong> apply stemming, stop-word removal, and
      synonym expansion per language (English, Spanish, German), so searching
      <code>running</code> also matches <code>runs</code> and <code>ran</code>.</p>
      <p><strong>Best for:</strong> exact keyword lookups, known technical terms, fast queries.</p>
      <ul>
        <li>Fast — sub-10ms on large indexes</li>
        <li>Precise on exact terms</li>
        <li>Misses synonyms not in the thesaurus</li>
        <li>No cross-language matching (German query ≠ English doc)</li>
      </ul>
      <p><strong>Examples:</strong></p>
      <div class="ex">readColors</div>
      <div class="ex">Benutzeranmeldung authentication</div>
      <div class="ex">checkoutColors LockC.NO</div>
    `
  },
  'es-hybrid': {
    title: 'ES Hybrid — BM25 + Semantic kNN Fusion',
    body: `
      <p><strong>Hybrid search</strong> combines two independent retrieval signals and merges
      them using <strong>Reciprocal Rank Fusion (RRF)</strong> computed in Python:</p>
      <p style="font-family:monospace;background:var(--code-bg);padding:6px 10px;border-radius:4px;font-size:11px;">
        score(d) = 1/(60 + bm25_rank) + 1/(60 + knn_rank)
      </p>
      <p><strong>Semantic kNN</strong> uses the
      <code>paraphrase-multilingual-MiniLM-L12-v2</code> model (384-dim dense vectors)
      to find passages that are <em>semantically similar</em> to the query — even if they
      share no keywords. This model covers 50+ languages, enabling true cross-lingual search.</p>
      <p><strong>Best for:</strong> conceptual questions, cross-language queries, paraphrases.</p>
      <ul>
        <li>Finds meaning, not just keywords</li>
        <li>Cross-lingual: Spanish query can match German/English documents</li>
        <li>Slower than BM25 alone (vector computation)</li>
        <li>Requires documents to be indexed with <code>--vectors</code></li>
      </ul>
      <p><strong>Examples:</strong></p>
      <div class="ex">function that returns all colors</div>
      <div class="ex">cómo configurar autenticación   ← finds English/German docs too</div>
      <div class="ex">error handling network timeout</div>
    `
  },
  'es-semantic': {
    title: 'ES Semantic — Pure kNN Cross-Lingual Search',
    body: `
      <p><strong>Pure semantic search</strong> — sends <em>only</em> the kNN vector query to
      Elasticsearch, with <strong>no BM25 component</strong>. This makes it fully
      language-agnostic: the query language does not need to match the document language.</p>
      <p style="font-family:monospace;background:var(--code-bg);padding:6px 10px;border-radius:4px;font-size:11px;">
        GET /api/es-search?q=...&amp;semantic_only=1
      </p>
      <p>Uses the <code>paraphrase-multilingual-MiniLM-L12-v2</code> model (50+ languages).
      The query is converted to a 384-dim vector and compared against all document vectors
      using cosine similarity — no keyword matching involved.</p>
      <p><strong>When to use:</strong> cross-language queries where the query language differs
      from the document language (e.g. German query → English docs).</p>
      <ul>
        <li>Language-agnostic: "Vorlage" finds docs with "template"</li>
        <li>No synonym tuning needed — meaning is captured by the model</li>
        <li>May miss exact technical terms (prefer Hybrid for mixed needs)</li>
        <li>Requires documents indexed with vectors (<code>--vectors</code>)</li>
      </ul>
      <p><strong>Examples:</strong></p>
      <div class="ex">Gibt ein Objekt zurück das alle Farben enthält   ← finds English docs</div>
      <div class="ex">Vorlage   ← finds "template" documents</div>
      <div class="ex">cómo manejar errores   ← finds try/catch docs in any language</div>
    `
  },
  'es-filename': {
    title: 'Filename — Search by File Name or Path',
    body: `
      <p><strong>Filename search</strong> matches against the file name and relative path stored
      in Elasticsearch — not the file content. Useful for locating a specific file when you know
      part of its name or extension.</p>
      <p><strong>Wildcard patterns</strong> using <code>*</code> (any characters) and
      <code>?</code> (single character) are supported:</p>
      <div class="ex">*.pdf   ← all PDF files</div>
      <div class="ex">chapter*   ← files starting with "chapter"</div>
      <div class="ex">*2024*   ← files containing "2024" in name or path</div>
      <div class="ex">report_??.docx   ← e.g. report_01.docx, report_ab.docx</div>
      <p>Without wildcards, a case-insensitive prefix match is applied to the filename field.</p>
      <p><strong>Results show the first chunk of each matching file</strong> — click to open the file.</p>
    `
  },
  'python': {
    title: 'Python — In-Memory Full-Text Search (No Elasticsearch)',
    body: `
      <p>The <strong>Python fallback search</strong> runs entirely inside the Flask backend
      without Elasticsearch. On startup, it walks the file system, extracts text from all
      supported formats (PDF, DOCX, HTML, SRT, code files…), and stores everything in RAM.</p>
      <p>Matching uses <strong>substring search</strong> with optional enhancements:</p>
      <ul>
        <li><strong>Fuzzy matching</strong> — Levenshtein edit distance tolerates typos
            (<code>avreviatura</code> matches <code>abreviatura</code>)</li>
        <li><strong>Soundex / phonetic</strong> — matches words that sound similar
            (add <code>&soundex=1</code> to URL)</li>
        <li><strong>Language normalization</strong> — umlaut folding for German
            (<code>ae→ä</code>, <code>ue→ü</code>), accent stripping for Spanish</li>
        <li><strong>30s TTL cache</strong> — repeated queries served from memory</li>
      </ul>
      <p><strong>Best for:</strong> quick setup, no Docker, small corpora (&lt;50k chunks).</p>
      <p><strong>Limitations:</strong> no ranking, no cross-language, slow on large repos.</p>
      <p><strong>Examples:</strong></p>
      <div class="ex">readColors</div>
      <div class="ex">Benutzer   ← with lang=de applies German phonetic normalization</div>
      <div class="ex">autenticacion   ← with lang=es strips accents → matches autenticación</div>
    `
  }
};

function showHelp(mode) {
  const panel = document.getElementById('help-panel');
  const h = HELP_CONTENT[mode];
  if (!h) return;
  // Toggle: clicking the same mode's ? again closes the panel
  if (panel.classList.contains('visible') && panel.dataset.mode === mode) {
    panel.classList.remove('visible');
    panel.dataset.mode = '';
    return;
  }
  panel.innerHTML = `<h4>${h.title}</h4>${h.body}
    <div style="text-align:right;margin-top:8px;">
      <span onclick="document.getElementById('help-panel').classList.remove('visible')"
            style="font-size:11px;color:var(--muted);cursor:pointer;">✕ close</span>
    </div>`;
  panel.dataset.mode = mode;
  panel.classList.add('visible');
}

// ── File modal ────────────────────────────────────────────────────────────────
// (openFileModal, switchFModalTab, toggleFModalMaximize, closeFModal, fmodalPlayVlc,
//  openVideoModal, closeVideoModal, openYTModal, closeYTModal → shared in assets/catalog.js)


// ── Mode ─────────────────────────────────────────────────────────────────────
function setMode(el) {
  document.querySelectorAll('#mode-tabs .tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  SEARCH_MODE = el.dataset.mode;
  localStorage.setItem('cc_search_mode', SEARCH_MODE);
}

// ── Search ───────────────────────────────────────────────────────────────────
async function doSearch() {
  const q     = document.getElementById('q').value.trim();
  saveSearchOptions();
  const lang  = document.getElementById('lang').value;
  const limit = parseInt(document.getElementById('limit').value) || 20;

  // Collect selected file type filters (needed for guard check below)
  const checkedExts = [...document.querySelectorAll('#ext-filter-grid input:checked')].map(c => c.value);
  const typesParam  = checkedExts.length ? `&types=${checkedExts.join(',')}` : '';

  // Allow search with no query only when file type filters are selected
  if (!q && !checkedExts.length) { log('Enter a query or select a file type filter', 'warn'); return; }
  const isSemanticMode = SEARCH_MODE === 'es-semantic' || SEARCH_MODE === 'es-hybrid';
  if (!q && isSemanticMode) { log('Semantic search requires a query — use BM25 or Filename mode for type-only search', 'warn'); return; }

  // Use '*' as match-all sentinel when query is empty
  const effectiveQ = q || '*';
  if (q) { localStorage.setItem('cc_last_query', q); pushHistory('cc_hist_search', q); }

  // Always switch to the Search results panel when a search is triggered
  switchPanel('search');

  document.getElementById('empty-state').style.display = 'none';
  document.getElementById('direct-result').style.display = 'none';
  const rc = document.getElementById('results-container');
  rc.style.display = 'block';
  document.getElementById('results-list').innerHTML = '<div style="padding:20px;color:var(--muted)"><span class="spinner"></span> Searching…</div>';

  const btn = document.getElementById('search-btn-text');
  btn.innerHTML = '<span class="spinner"></span>';

  const t0 = performance.now();
  let data, url;

  try {
    if (SEARCH_MODE === 'python') {
      url = `${apiUrl()}/api/search?q=${encodeURIComponent(effectiveQ)}&lang=${lang}&limit=${limit}${typesParam}`;
      log(`GET ${url}`, 'info');
      const r = await fetch(url);
      data = await r.json();
    } else if (SEARCH_MODE === 'es-filename') {
      url = `${apiUrl()}/api/es-search?q=${encodeURIComponent(effectiveQ)}&filename_search=1&limit=${limit}${typesParam}`;
      log(`GET ${url}`, 'info');
      const r = await fetch(url);
      data = await r.json();
    } else {
      const semantic = SEARCH_MODE === 'es-hybrid'   ? '&semantic=1' :
                       SEARCH_MODE === 'es-semantic' ? '&semantic_only=1' : '';
      url = `${apiUrl()}/api/es-search?q=${encodeURIComponent(effectiveQ)}&lang=${lang}&limit=${limit}${semantic}${typesParam}`;
      log(`GET ${url}`, 'info');
      const r = await fetch(url);
      data = await r.json();
    }
  } catch(e) {
    _backendOk = false; updateDirectBanner();
    if (SEARCH_MODE === 'es-filename') {
      log('Filename search requires the backend (not available via direct ES).', 'warn');
      document.getElementById('results-list').innerHTML =
        `<div class="no-results"><div class="icon">⚠️</div><div>Backend offline — Filename search requires the catalog server.</div></div>`;
      btn.textContent = 'Search';
      return;
    }
    log('Backend unreachable, trying direct ES search…', 'warn');
    try {
      data = await directEsSearch(effectiveQ, limit, lang, SEARCH_MODE);
      url  = `direct:${esUrl()}/${indexName()}/_search`;
    } catch(e2) {
      log('Direct ES also failed: ' + e2.message, 'error');
      document.getElementById('results-list').innerHTML =
        `<div class="no-results"><div class="icon">⚠️</div><div>Backend offline &amp; ES unreachable: ${e2.message}</div></div>`;
      btn.textContent = 'Search';
      return;
    }
  }

  btn.textContent = 'Search';
  const elapsed = ((performance.now() - t0) / 1000).toFixed(2);

  // Metadata
  const engine = data.mode?.engine || SEARCH_MODE;
  document.getElementById('engine-badge').textContent = engine.toUpperCase();
  document.getElementById('results-meta').textContent =
    `${(data.results || []).length} files · ${data.total || 0} matches · ${elapsed}s`;
  log(`Found ${(data.results||[]).length} files, ${data.total||0} matches in ${elapsed}s`, 'ok');

  // Raw JSON
  document.getElementById('raw-json').textContent = JSON.stringify(data, null, 2);

  // Render results
  if (SEARCH_MODE === 'es-filename') {
    renderFilenameResults(data.results || []);
  } else {
    renderResults(data.results || [], q);
  }
}

function renderResults(results, query) {
  const container = document.getElementById('results-list');
  if (!results.length) {
    container.innerHTML = '<div class="no-results"><div class="icon">😶</div><div>No results found.</div></div>';
    return;
  }

  const qLower = (query || '').toLowerCase().trim();

  container.innerHTML = results.map(r => {
    const ext  = (r.type || r.filename || '').split('.').pop().toLowerCase();
    const icon = FILE_TYPE_ICONS[ext] || '📎';
    const score = r.score != null ? `<span class="result-score">${r.score.toFixed(3)}</span>` : '';
    const isSrtType = ext === 'srt' || ext === 'vtt';

    const filePath = r.path || r.rel_path || '';
    let cardHasExact = false;
    const firstSrtTime = isSrtType ? ((r.matches || [])[0]?.srt_time ?? null) : null;

    const matchHtml = (r.matches || []).slice(0, 5).map(m => {
      const rawCtx  = (m.context || '').toLowerCase();
      const isExact = qLower && rawCtx.includes(qLower);
      if (isExact) cardHasExact = true;
      const exactBadge = isExact ? `<span class="match-exact-badge">Exact</span>` : '';
      const ctx = highlightQuery(m.context || '', m.highlight || query);
      const srtTime = m.srt_time != null ? m.srt_time : null;
      const chunkAttr = `data-path="${escHtml(filePath)}" data-chunk="${escHtml(isSrtType ? '' : (m.chunk_content || ''))}"`;
      const timePart = srtTime != null
        ? `<span class="match-time" title="Play video at this time"
               data-path="${escHtml(filePath)}" data-time="${srtTime}"
               onclick="event.stopPropagation();playVideoAtTime(this.dataset.path, parseFloat(this.dataset.time))">⏱ ${formatTime(srtTime)}</span>`
        : '';
      const matchOnclick = isSrtType && srtTime != null
        ? `openFileModal(this.dataset.path, parseFloat(this.dataset.time), '')`
        : `openFileModal(this.dataset.path, null, this.dataset.chunk)`;
      const linePart = `<span class="match-line" style="cursor:pointer;" ${chunkAttr}
          ${isSrtType && srtTime != null ? `data-time="${srtTime}"` : ''}
          onclick="event.stopPropagation();${matchOnclick}"
          title="Click to view this chunk">chunk ${m.line}</span>`;
      return `<div class="match-item" style="cursor:pointer;" ${chunkAttr}
          ${isSrtType && srtTime != null ? `data-time="${srtTime}"` : ''}
          onclick="${matchOnclick}">
        <div class="match-meta">${linePart}${timePart}${exactBadge}</div>
        <div class="match-context">${ctx}</div>
      </div>`;
    }).join('');

    const firstChunk = isSrtType ? '' : escHtml((r.matches || [])[0]?.chunk_content || '');
    const cardTimeAttr = isSrtType && firstSrtTime != null ? `data-time="${firstSrtTime}"` : '';
    const cardOnclick = isSrtType && firstSrtTime != null
      ? `openFileModal(this.dataset.path, parseFloat(this.dataset.time), '')`
      : `openFileModal(this.dataset.path, null, this.dataset.chunk)`;
    const exactClass = cardHasExact ? ' has-exact' : '';
    return `<div class="result-card${exactClass}">
      <div class="result-header" style="cursor:pointer;" data-path="${escHtml(filePath)}" data-chunk="${firstChunk}" ${cardTimeAttr}
           onclick="${cardOnclick}" title="Click to view file">
        <span class="file-icon">${icon}</span>
        <div class="file-info">
          <div class="file-name">${escHtml(r.file || '')}</div>
          <div class="file-path">${escHtml(r.rel_path || r.path || '')}</div>
        </div>
        ${score}
      </div>
      <div class="matches-list">${matchHtml}</div>
    </div>`;
  }).join('');
}

function renderFilenameResults(results) {
  const container = document.getElementById('results-list');
  if (!results.length) {
    container.innerHTML = '<div class="no-results"><div class="icon">😶</div><div>No files found.</div></div>';
    return;
  }
  let html = '<div style="display:flex;flex-direction:column;gap:3px;">';
  for (const r of results) {
    const filePath = r.path || r.rel_path || '';
    const ext = (r.type || filePath).split('.').pop().toLowerCase();
    const icon = FILE_TYPE_ICONS[ext] || '📎';
    const escapedPath = escHtml(filePath);
    const escapedJs   = filePath.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:var(--code-bg);border-radius:6px;cursor:pointer;transition:background .1s;"
        onmouseover="this.style.background='var(--surface2,rgba(255,255,255,.08))'"
        onmouseout="this.style.background='var(--code-bg)'"
        onclick="openFileModal('${escapedJs}')" title="${escapedPath}">
      <span style="font-size:16px;flex-shrink:0;">${icon}</span>
      <div style="flex:1;min-width:0;">
        <div style="font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(r.file || filePath.split(/[\\/]/).pop())}</div>
        <div style="font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapedPath}</div>
      </div>
    </div>`;
  }
  html += '</div>';
  container.innerHTML = html;
}

function highlightQuery(context, query) {
  const safe = escHtml(context);
  // Highlight **word** markdown-style markers from ES
  const withBold = safe.replace(/\*\*(.*?)\*\*/g, '<b>$1</b>');
  return withBold;
}

function copyTime(sec) {
  navigator.clipboard.writeText(String(Math.floor(sec))).then(() => {
    log(`Copied time ${formatTime(sec)} (${Math.floor(sec)}s) to clipboard`, 'ok');
  });
}

function clearResults() {
  document.getElementById('empty-state').style.display = 'block';
  document.getElementById('results-container').style.display = 'none';
  document.getElementById('direct-result').style.display = 'none';
}

// ── Direct ES Query ──────────────────────────────────────────────────────────
async function runDirect() {
  const endpoint = document.getElementById('es-endpoint').value.trim() || '/_cluster/health';
  const bodyRaw  = document.getElementById('es-body').value.trim();
  const method   = bodyRaw ? 'POST' : 'GET';
  const url      = esUrl() + endpoint;

  document.getElementById('empty-state').style.display = 'none';
  document.getElementById('results-container').style.display = 'none';
  const dr = document.getElementById('direct-result');
  dr.style.display = 'block';
  document.getElementById('direct-json').textContent = 'Loading…';

  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (bodyRaw) {
    try { opts.body = JSON.stringify(JSON.parse(bodyRaw)); }
    catch { opts.body = bodyRaw; }
  }

  log(`${method} ${url}`, 'info');
  try {
    const r   = await fetch(url, opts);
    const txt = await r.text();
    let parsed;
    try { parsed = JSON.stringify(JSON.parse(txt), null, 2); } catch { parsed = txt; }
    document.getElementById('direct-json').textContent = parsed;
    document.getElementById('direct-meta').textContent = `${method} ${endpoint} → HTTP ${r.status}`;
    log(`${method} ${endpoint} → ${r.status}`, r.ok ? 'ok' : 'warn');
  } catch(e) {
    document.getElementById('direct-json').textContent = 'Error: ' + e.message;
    log('Direct query error: ' + e.message, 'error');
  }
}

// ── Clean Index ───────────────────────────────────────────────────────────────
async function cleanIndex() {
  const idx = indexName();
  if (!confirm(`Delete ALL documents from index "${idx}"?\n\nThis cannot be undone. The index schema will be preserved.`))
    return;

  log(`Deleting all documents from "${idx}"…`, 'warn');
  try {
    const url = `${esUrl()}/${idx}/_delete_by_query`;
    const r   = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: { match_all: {} } }),
    });
    const d = await r.json();
    if (r.ok) {
      log(`Deleted ${d.deleted || 0} documents from "${idx}"`, 'ok');
      await refreshStats();
    } else {
      log('Delete error: ' + JSON.stringify(d.error?.reason || d), 'error');
    }
  } catch(e) {
    log('Delete error: ' + e.message, 'error');
  }
}

// ── Enter key on query input ─────────────────────────────────────────────────
document.getElementById('q').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});

// ── Init ─────────────────────────────────────────────────────────────────────
// ── Panel switching ───────────────────────────────────────────────────────────
function switchPanel(panel) {
  const isSearch = panel === 'search';
  const isAsk    = panel === 'ask';
  const isStatus = panel === 'status';
  const isTools  = panel === 'tools';

  document.getElementById('tab-search').classList.toggle('active', isSearch);
  document.getElementById('tab-ask').classList.toggle('active', isAsk);
  document.getElementById('tab-status').classList.toggle('active', isStatus);
  document.getElementById('tab-tools').classList.toggle('active', isTools);

  document.getElementById('results-panel').style.display = isSearch ? '' : 'none';

  const ap = document.getElementById('ask-panel');
  ap.classList.toggle('visible', isAsk);
  ap.style.display = isAsk ? '' : 'none';

  const sp = document.getElementById('status-panel');
  sp.classList.toggle('visible', isStatus);
  sp.style.display = isStatus ? '' : 'none';

  const tp = document.getElementById('tools-panel');
  tp.classList.toggle('visible', isTools);
  tp.style.display = isTools ? '' : 'none';

  // Lazy-load status data the first time the tab is opened
  if (isStatus) loadStatus(false);
  // Lazy-load embedding config the first time Tools tab is opened
  if (isTools) loadToolsConfig();
}

// ── Status / Statistics panel ──────────────────────────────────────────────────
let _statusLoaded = false;

function fmtBytes(n) {
  const u = ['B','KB','MB','GB','TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(1) + ' ' + u[i];
}

function fmtNum(n) { return n == null ? '—' : n.toLocaleString(); }

function stHtml(report) {
  const es  = report.elasticsearch  || {};
  const idx = report.index          || {};
  const ext = report.by_extension   || {};
  const sch = report.search         || {};
  const lng = report.content?.language_distribution || {};
  const trn = report.content?.translation_status    || {};
  const rag = report.rag            || {};
  const ES_URL = esUrl();
  const IDX    = indexName();

  const statusColor = { green: 'ok', yellow: 'warn', red: 'err' };
  const sc = statusColor[es.cluster_status] || 'warn';

  // — cluster ——————————————————————————————————————————————————————————————
  let html = `
  <div class="st-section">
    <div class="st-section-title">Cluster</div>
    <div class="st-grid">
      <span class="st-label">URL</span>        <span class="st-value">${es.url || ES_URL}</span>
      <span class="st-label">Version</span>    <span class="st-value">${es.version || '—'} (${es.build_flavor || '—'})</span>
      <span class="st-label">Status</span>     <span class="st-value ${sc}">${(es.cluster_status||'—').toUpperCase()}</span>
      <span class="st-label">Nodes</span>      <span class="st-value">${es.nodes ?? '—'}</span>
      <span class="st-label">Plugins</span>    <span class="st-value">${(es.plugins||[]).join(', ') || '—'}</span>
      <span class="st-label">Data path</span>  <span class="st-value">${(es.data_paths||[]).join('<br>') || '—'} <span style="color:var(--muted);font-size:11px;">(Docker volume: es_data)</span></span>
    </div>
  </div>`;

  // — index ————————————————————————————————————————————————————————————————
  html += `
  <div class="st-section">
    <div class="st-section-title">Index: ${idx.name || IDX}</div>
    <div class="st-grid">
      <span class="st-label">Store size</span>        <span class="st-value" style="color:var(--accent);font-weight:700;">${idx.store_human || fmtBytes(idx.store_bytes||0)}</span>
      <span class="st-label">Total chunks</span>      <span class="st-value">${fmtNum(idx.total_chunks)}</span>
      <span class="st-label">Unique files</span>      <span class="st-value">${fmtNum(idx.unique_files)}</span>
      <span class="st-label">Avg chunks/file</span>   <span class="st-value">${idx.avg_chunks_per_file ?? '—'}</span>
      <span class="st-label">First indexed</span>     <span class="st-value" style="color:var(--muted)">${idx.first_indexed || '—'}</span>
      <span class="st-label">Last indexed</span>      <span class="st-value" style="color:var(--muted)">${idx.last_indexed  || '—'}</span>
    </div>
  </div>`;

  // — files by type ————————————————————————————————————————————————————————
  const extEntries = Object.entries(ext).sort((a,b) => b[1].files - a[1].files);
  if (extEntries.length) {
    html += `
  <div class="st-section">
    <div class="st-section-title">Files by type</div>
    <table class="st-table">
      <tr><th>Extension</th><th style="text-align:right">Files</th><th style="text-align:right">Chunks</th><th style="text-align:right">Avg chunks</th></tr>`;
    for (const [ext_, s] of extEntries) {
      const avg = s.files ? (s.chunks / s.files).toFixed(1) : '—';
      html += `<tr><td><code>${ext_}</code></td><td style="text-align:right">${fmtNum(s.files)}</td><td style="text-align:right">${fmtNum(s.chunks)}</td><td style="text-align:right">${avg}</td></tr>`;
    }
    html += `</table></div>`;
  }

  // — search capabilities ——————————————————————————————————————————————————
  const caps = [
    [true,              'BM25 full-text search', ''],
    [sch.multilingual_bm25, 'Multilingual BM25', (sch.analyzers||[]).join(', ')],
    [sch.knn_vectors,   'kNN semantic vectors',  sch.knn_vectors ? 'paraphrase-multilingual-MiniLM-L12-v2' : 're-index with vectors enabled'],
    [sch.hybrid_bm25_knn, 'Hybrid BM25 + kNN (RRF)', ''],
    [sch.srt_timestamps,  'SRT/VTT timestamp-aware search', ''],
  ];
  html += `<div class="st-section"><div class="st-section-title">Search capabilities</div>`;
  for (const [ok, label, detail] of caps) {
    html += `<div class="st-cap"><span class="mark">${ok ? '✅' : '⬜'}</span><span>${label}</span>${detail ? `<span class="detail">(${detail})</span>` : ''}</div>`;
  }
  html += `</div>`;

  // — languages ————————————————————————————————————————————————————————————
  const lngEntries = Object.entries(lng).sort((a,b) => b[1]-a[1]);
  if (lngEntries.length) {
    const totalLng = lngEntries.reduce((s,[,v]) => s+v, 0);
    html += `<div class="st-section"><div class="st-section-title">Detected languages</div>`;
    for (const [lang, count] of lngEntries) {
      const pct = totalLng ? (count/totalLng*100).toFixed(1) : 0;
      const w   = Math.max(3, Math.round(count/totalLng*200));
      html += `<div class="st-bar-wrap"><span class="st-bar-label">${lang}</span><div class="st-bar" style="width:${w}px"></div><span class="st-bar-pct">${fmtNum(count)} (${pct}%)</span></div>`;
    }
    html += `</div>`;
  }

  // — translation ——————————————————————————————————————————————————————————
  if (Object.keys(trn).length) {
    html += `<div class="st-section"><div class="st-section-title">Translation status</div><div class="st-grid">`;
    for (const [st, count] of Object.entries(trn)) {
      const c = st === 'done' ? 'ok' : 'warn';
      html += `<span class="st-label">${st}</span><span class="st-value ${c}">${fmtNum(count)}</span>`;
    }
    html += `</div></div>`;
  }

  // — RAG / Ollama —————————————————————————————————————————————————————————
  html += `<div class="st-section"><div class="st-section-title">RAG / Ollama</div>`;
  if (rag.ollama_running) {
    html += `<div class="st-cap"><span class="mark">✅</span><span>Ollama running</span></div>`;
    for (const m of (rag.ollama_models||[])) {
      html += `<div style="font-size:11px;color:var(--muted);margin-left:28px;">${m}</div>`;
    }
  } else {
    html += `<div class="st-cap"><span class="mark">⬜</span><span>Ollama not running</span><span class="detail">start with: ollama serve</span></div>`;
  }
  html += `</div>`;

  // — curl examples ————————————————————————————————————————————————————————
  const backendUrl = (document.getElementById('api-url')?.value || 'http://127.0.0.1:5000').replace(/\/$/, '');
  const curlExamples = [
    ['BM25 full-text search (multilingual)',
     `curl -s "${ES_URL}/${IDX}/_search" -H "Content-Type: application/json" -d '{"query":{"multi_match":{"query":"<query>","fields":["content"],"type":"best_fields"}},"size":5,"_source":["filename","rel_path","content"]}'`],
    ['Semantic kNN search (via Flask backend)',
     `curl -s "${backendUrl}/api/es-search?q=<query>&mode=semantic&k=10&size=5"`],
    ['BM25 only (via Flask backend)',
     `curl -s "${backendUrl}/api/es-search?q=<query>&mode=bm25&size=10"`],
    ['Hybrid BM25 + kNN (via Flask backend)',
     `curl -s "${backendUrl}/api/es-search?q=<query>&mode=hybrid&k=10&size=5"`],
    ['SRT/VTT subtitle timestamp search',
     `curl -s "${ES_URL}/${IDX}/_search" -H "Content-Type: application/json" -d '{"query":{"bool":{"must":{"match":{"content":"<query>"}},"filter":{"exists":{"field":"srt_start_time"}}}},"size":5,"_source":["filename","content","srt_start_time","srt_end_time"]}'`],
    ['File-type aggregation',
     `curl -s "${ES_URL}/${IDX}/_search" -H "Content-Type: application/json" -d '{"size":0,"aggs":{"by_ext":{"terms":{"field":"ext","size":20},"aggs":{"files":{"cardinality":{"field":"path"}}}}}}'`],
    ['Cluster health',
     `curl -s "${ES_URL}/_cluster/health?pretty"`],
    ['Index store size & doc count',
     `curl -s "${ES_URL}/${IDX}/_stats?pretty"`],
  ];
  html += `<div class="st-section"><div class="st-section-title">curl examples</div>`;
  for (const [title, cmd] of curlExamples) {
    html += `<div class="st-curl-title"># ${title}</div><pre class="st-curl-block">${cmd}</pre>`;
  }
  html += `</div>`;

  return html;
}

async function loadStatus(force = false) {
  if (_statusLoaded && !force) return;
  const container = document.getElementById('status-content');
  container.innerHTML = '<div class="st-loading"><span class="spinner"></span> Loading status…</div>';

  const ES  = esUrl();
  const IDX = indexName();

  try {
    // Run all queries in parallel
    const [healthResp, pluginsResp, statsResp, settingsResp,
           extResp, vecResp, srtResp, langResp, timeResp, dataPathResp, ollamaResp] =
      await Promise.allSettled([
        fetch(`${ES}/_cluster/health`,         { signal: AbortSignal.timeout(5000) }).then(r => r.json()),
        fetch(`${ES}/_nodes/plugins`,           { signal: AbortSignal.timeout(5000) }).then(r => r.json()),
        fetch(`${ES}/${IDX}/_stats`,            { signal: AbortSignal.timeout(5000) }).then(r => r.json()),
        fetch(`${ES}/${IDX}/_settings`,         { signal: AbortSignal.timeout(5000) }).then(r => r.json()),
        fetch(`${ES}/${IDX}/_search`, { method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({size:0,aggs:{by_ext:{terms:{field:'ext',size:50},aggs:{unique_files:{cardinality:{field:'path',precision_threshold:10000}}}}}}),
          signal: AbortSignal.timeout(8000) }).then(r => r.json()),
        fetch(`${ES}/${IDX}/_search`, { method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({size:1,query:{exists:{field:'content_vector'}}}),
          signal: AbortSignal.timeout(5000) }).then(r => r.json()),
        fetch(`${ES}/${IDX}/_search`, { method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({size:1,query:{exists:{field:'srt_start_time'}}}),
          signal: AbortSignal.timeout(5000) }).then(r => r.json()),
        fetch(`${ES}/${IDX}/_search`, { method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({size:0,query:{exists:{field:'detected_lang'}},aggs:{languages:{terms:{field:'detected_lang',size:20}},trans_status:{terms:{field:'translation_status',size:5}}}}),
          signal: AbortSignal.timeout(5000) }).then(r => r.json()),
        fetch(`${ES}/${IDX}/_search`, { method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({size:0,aggs:{newest:{max:{field:'indexed_at'}},oldest:{min:{field:'indexed_at'}}}}),
          signal: AbortSignal.timeout(5000) }).then(r => r.json()),
        fetch(`${ES}/_nodes/settings`,          { signal: AbortSignal.timeout(5000) }).then(r => r.json()),
        _fetchOllamaModels(),
      ]);

    const ok = r => r.status === 'fulfilled' ? r.value : null;

    // Parse cluster
    const health   = ok(healthResp)  || {};
    const plugins  = ok(pluginsResp) || {};
    const pluginNames = [];
    for (const node of Object.values(plugins.nodes || {}))
      for (const p of (node.plugins || [])) if (!pluginNames.includes(p.name)) pluginNames.push(p.name);

    // Data path
    const nodeSettings = ok(dataPathResp) || {};
    const dataPaths = [];
    for (const node of Object.values(nodeSettings.nodes || {})) {
      const raw = node?.settings?.path?.data;
      if (Array.isArray(raw)) dataPaths.push(...raw);
      else if (raw) dataPaths.push(raw);
    }

    // Index stats
    const statsData  = ok(statsResp) || {};
    const idxTotal   = statsData?.indices?.[IDX]?.total || {};
    const storeBytes = idxTotal?.store?.size_in_bytes || 0;
    const docCount   = idxTotal?.docs?.count || 0;

    // Settings → analyzers
    const settingsData    = ok(settingsResp) || {};
    const analyzerKeys    = Object.keys(settingsData?.[IDX]?.settings?.index?.analysis?.analyzer || {});

    // Extension stats
    const extData   = ok(extResp) || {};
    const extBuckets = extData?.aggregations?.by_ext?.buckets || [];
    const byExt = {};
    for (const b of extBuckets) byExt[b.key] = { chunks: b.doc_count, files: b.unique_files?.value || 0 };

    // Vectors / SRT
    const hasVectors = (ok(vecResp)?.hits?.total?.value || 0) > 0;
    const hasSrt     = (ok(srtResp)?.hits?.total?.value || 0) > 0;

    // Language distribution
    const langData   = ok(langResp) || {};
    const langDist   = {};
    for (const b of (langData?.aggregations?.languages?.buckets || [])) langDist[b.key] = b.doc_count;
    const transDist  = {};
    for (const b of (langData?.aggregations?.trans_status?.buckets || [])) transDist[b.key] = b.doc_count;

    // Timestamps
    const timeData  = ok(timeResp) || {};
    const toDateStr = ms => ms ? new Date(ms).toISOString().replace('T',' ').slice(0,19) + ' UTC' : '—';
    const firstIndexed = toDateStr(timeData?.aggregations?.oldest?.value);
    const lastIndexed  = toDateStr(timeData?.aggregations?.newest?.value);

    // Ollama — via _fetchOllamaModels() which tries backend then direct
    // Shape: { models: [string], source: 'backend'|'direct' }
    const ollamaData   = ok(ollamaResp) || {};
    const ollamaModels = Array.isArray(ollamaData.models) ? ollamaData.models : [];
    const ollamaOk     = ollamaResp.status === 'fulfilled' && ollamaModels.length >= 0 && ollamaData.source != null;

    const report = {
      elasticsearch: {
        url:            ES,
        version:        health.cluster_name ? (ok(healthResp)?.cluster_name || '') : '—',
        build_flavor:   '',
        cluster_status: health.status || 'red',
        nodes:          health.number_of_nodes ?? '—',
        plugins:        pluginNames.sort(),
        data_paths:     dataPaths,
      },
      index: {
        name:               IDX,
        store_bytes:        storeBytes,
        store_human:        fmtBytes(storeBytes),
        total_chunks:       docCount,
        unique_files:       null,   // not computed client-side (composite agg too heavy)
        avg_chunks_per_file: null,
        first_indexed:      firstIndexed,
        last_indexed:       lastIndexed,
      },
      by_extension: byExt,
      search: {
        bm25:              true,
        multilingual_bm25: analyzerKeys.length > 0,
        analyzers:         analyzerKeys,
        knn_vectors:       hasVectors,
        hybrid_bm25_knn:   hasVectors,
        srt_timestamps:    hasSrt,
      },
      content: {
        language_distribution: langDist,
        translation_status:    transDist,
      },
      rag: { ollama_running: ollamaOk, ollama_models: ollamaModels },
    };

    // Fetch ES version via /  (separate because _cluster/health doesn't return version)
    try {
      const infoResp = await fetch(ES, { signal: AbortSignal.timeout(3000) });
      const info = await infoResp.json();
      report.elasticsearch.version     = info?.version?.number || '—';
      report.elasticsearch.build_flavor = info?.version?.build_flavor || '';
    } catch(_) {}

    // Unique files: use extBuckets sum of files (approximate)
    const totalFiles = Object.values(byExt).reduce((s, v) => s + v.files, 0);
    report.index.unique_files        = totalFiles || null;
    report.index.avg_chunks_per_file = (totalFiles && docCount) ? (docCount / totalFiles).toFixed(1) : null;

    container.innerHTML = stHtml(report);
    _statusLoaded = true;
  } catch (err) {
    container.innerHTML = `<div style="color:var(--red);padding:20px;">Error loading status: ${err.message}</div>`;
  }

  // Refresh active-task banner: reconnect SSE if tasks are still running
  _reconnectActiveTasks();
}

async function _reconnectActiveTasks() {
  try {
    const ar = await fetch(apiUrl() + '/api/tools/active');
    const active = await ar.json();
    let anyActive = false;
    for (const [tid, info] of Object.entries(active)) {
      if (info.type === 'index' && !_idxEvt) {
        anyActive = true;
        _setIndexBusy(true);
        _showActiveBanner('Index directory in progress…');
        toolOut('tool-idx-out', 'Indexing in progress (reconnected)…\n');
        _followIndexTask(tid);
      }
      if (info.type === 'reindex' && !_reindexEvt) {
        anyActive = true;
        _setReindexBusy(true);
        _showActiveBanner('Reindex in progress…');
        toolOut('tool-embed-out', 'Reindex in progress (reconnected)…\n');
        _reindexEvt = followTask(tid, _taskHandlers('reindex', 'tool-embed-out', {
          title: 'Reindex', okMsg: '\nReindex complete.',
          setBusy: _setReindexBusy, clear: () => { _reindexEvt = null; },
        }));
      }
    }
    if (!anyActive && !_idxEvt && !_reindexEvt) _hideActiveBanner();
  } catch(e) { /* backend not reachable */ }
}

// ── Ask / RAG ─────────────────────────────────────────────────────────────────
let _askController = null;

function setAskStatus(msg, type = 'info') {
  const el = document.getElementById('ask-status-row');
  el.innerHTML = msg ? `<span class="spinner"></span><span>${msg}</span>` : '';
  if (!msg) el.innerHTML = '';
}

function clearAsk() {
  if (_askController) { _askController.abort(); _askController = null; }
  document.getElementById('ask-q').value = '';
  document.getElementById('ask-answer-wrap').style.display = 'none';
  document.getElementById('ask-answer').innerHTML = '';
  document.getElementById('ask-sources').style.display = 'none';
  document.getElementById('ask-sources-bar').innerHTML = '';
  document.getElementById('ask-model-badge').style.display = 'none';
  document.getElementById('ask-status-row').innerHTML = '';
  document.getElementById('ask-btn').disabled = false;
  document.getElementById('ask-btn').textContent = 'Ask';
}

function renderAnswer(text) {
  // Use marked.js for full markdown (tables, code blocks, lists, etc.)
  if (typeof marked !== 'undefined') {
    marked.setOptions({
      breaks: false,
      gfm: true,
      highlight: function(code, lang) {
        if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
          return hljs.highlight(code, { language: lang }).value;
        }
        return typeof hljs !== 'undefined' ? hljs.highlightAuto(code).value : code;
      },
    });
    let html = marked.parse(text);
    // Post-process: citation references [1] → styled spans
    html = html.replace(/\[(\d+)\]/g, '<span class="cite-ref">[$1]</span>');
    return html;
  }
  // Fallback if marked.js not loaded
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[(\d+)\]/g, '<span class="cite-ref">[$1]</span>');
}

function fmtTime(secs) {
  if (secs == null) return null;
  const t = Math.round(secs);
  return `${String(Math.floor(t/3600)).padStart(2,'0')}:${String(Math.floor((t%3600)/60)).padStart(2,'0')}:${String(t%60).padStart(2,'0')}`;
}

async function doAsk() {
  const q     = document.getElementById('ask-q').value.trim();
  const lang  = document.getElementById('ask-lang').value;
  const k     = parseInt(document.getElementById('ask-k').value) || 5;
  const model = document.getElementById('ask-model').value;

  if (!q) { document.getElementById('ask-q').focus(); return; }
  if (q) { localStorage.setItem('cc_last_ask', q); pushHistory('cc_hist_ask', q); }
  saveAskOptions();
  if (_askController) { _askController.abort(); }

  _askController = new AbortController();
  const btn = document.getElementById('ask-btn');
  btn.disabled = true; btn.textContent = '…';

  // Reset UI
  document.getElementById('ask-answer-wrap').style.display = 'none';
  document.getElementById('ask-answer').innerHTML = '';
  document.getElementById('ask-sources').style.display = 'none';
  document.getElementById('ask-sources-bar').innerHTML = '';
  document.getElementById('ask-model-badge').style.display = 'none';
  setAskStatus('Connecting…');

  log(`Ask: "${q}" (k=${k}, lang=${lang||'auto'}, model=${model||'auto'})`, 'info');

  // Pass the active search mode (python falls back to es-hybrid for RAG)
  const askMode = SEARCH_MODE === 'python' ? 'es-hybrid' : SEARCH_MODE;
  let params = `q=${encodeURIComponent(q)}&k=${k}&mode=${askMode}`;
  if (lang)  params += `&lang=${lang}`;
  if (model) params += `&model=${encodeURIComponent(model)}`;

  // Apply file type filter (same panel as search)
  const checkedExts = [...document.querySelectorAll('#ext-filter-grid input:checked')].map(c => c.value);
  if (checkedExts.length) params += `&types=${checkedExts.join(',')}`;

  // Reset debug panel
  document.getElementById('ask-debug').style.display = 'none';
  document.getElementById('ask-debug-content').textContent = '';

  let answerText = '';

  try {
    // External LLM → POST so the API key travels in the body, never in the URL
    const llmCfg = window.LLM ? LLM.payload() : null;
    const res = llmCfg
      ? await fetch(apiUrl() + '/api/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            q, k, mode: askMode, lang: lang || '', model: model || llmCfg.model,
            types: checkedExts.join(','), llm: llmCfg,
          }),
          signal: _askController.signal,
        })
      : await fetch(apiUrl() + '/api/ask?' + params, {
          signal: _askController.signal,
        });

    if (!res.ok) {
      const err = await res.text();
      setAskStatus('');
      document.getElementById('ask-answer-wrap').style.display = 'block';
      document.getElementById('ask-answer').innerHTML = `<span style="color:var(--red)">${err}</span>`;
      btn.disabled = false; btn.textContent = 'Ask';
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let evt;
        try { evt = JSON.parse(line.slice(6)); } catch { continue; }

        if (evt.type === 'status') {
          setAskStatus(evt.text);

        } else if (evt.type === 'sources') {
          const bar = document.getElementById('ask-sources-bar');
          bar.innerHTML = '';
          evt.sources.forEach((s, i) => {
            const chip = document.createElement('div');
            chip.className = 'source-chip';
            chip.title = (s.rel_path || s.filename) + ' — click to view';
            chip.style.cursor = 'pointer';
            chip.innerHTML = `${getFileIcon(s.filename)} [${i+1}] ${escHtml(s.filename)}` +
              `<span class="chip-score">${s.score.toFixed(3)}</span>` +
              (s.srt_time != null ? `<span class="chip-time"> @${fmtTime(s.srt_time)}</span>` : '');
            chip.onclick = () => openFileModal(s.path || s.rel_path || s.filename, s.srt_time ?? null, s.content || '');
            bar.appendChild(chip);
          });
          document.getElementById('ask-sources').style.display = 'block';

        } else if (evt.type === 'token') {
          if (!answerText) {
            setAskStatus('');
            document.getElementById('ask-answer-wrap').style.display = 'block';
          }
          answerText += evt.text;
          const el = document.getElementById('ask-answer');
          el.innerHTML = renderAnswer(answerText) + '<span class="ask-cursor"></span>';
          el.parentElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        } else if (evt.type === 'done') {
          // Remove cursor, set final text
          document.getElementById('ask-answer').innerHTML = renderAnswer(answerText);
          setAskStatus('');
          if (evt.model) {
            const badge = document.getElementById('ask-model-badge');
            badge.textContent = evt.model;
            badge.style.display = 'inline-block';
          }
          log(`Answer complete (model: ${evt.model || 'none'})`, 'ok');

        } else if (evt.type === 'search_query') {
          // Two-stage RAG: show the LLM-optimised search query
          const tr = evt.translations || [];
          setAskStatus(`Search query: "${evt.optimised}"` + (tr.length ? `  →  ${tr.map(t => `"${t}"`).join(', ')}` : ''));
          const dbg = document.getElementById('ask-debug');
          const pre = document.getElementById('ask-debug-content');
          const prev = pre.textContent ? pre.textContent + '\n\n' : '';
          pre.textContent = prev +
            `=== Stage 1 — Search query extraction ===\n` +
            `Original  : ${evt.original}\n` +
            `Optimised : ${evt.optimised}\n` +
            (tr.length ? `Translated: ${tr.join('  |  ')}   (each query is searched, rankings fused by RRF)` : '');
          dbg.style.display = 'block';

        } else if (evt.type === 'retrieval') {
          // Retrieval summary: queries used + whether the cross-encoder reranked the candidates
          const dbg = document.getElementById('ask-debug');
          const pre = document.getElementById('ask-debug-content');
          const prev = pre.textContent ? pre.textContent + '\n\n' : '';
          pre.textContent = prev + `=== Retrieval ===\nQueries: ${(evt.queries || []).join('  |  ')}\n` +
            `Rerank : ${evt.reranked ? `cross-encoder reordered ${evt.pool} candidates` : `not applied (${evt.rerank_state || 'off'})`}`;
          dbg.style.display = 'block';

        } else if (evt.type === 'context') {
          // Debug: show exact prompt sent to LLM
          const dbg = document.getElementById('ask-debug');
          const pre = document.getElementById('ask-debug-content');
          const prev = pre.textContent ? pre.textContent + '\n\n' : '';
          pre.textContent = prev +
            `=== Stage 2 — Answer generation ===\n` +
            `Search mode: ${evt.search_mode}\n\n` +
            `--- SYSTEM PROMPT ---\n${evt.system_prompt}\n\n` +
            `--- USER PROMPT (context + question) ---\n${evt.user_prompt}`;
          dbg.style.display = 'block';

        } else if (evt.type === 'error') {
          setAskStatus('');
          document.getElementById('ask-answer-wrap').style.display = 'block';
          document.getElementById('ask-answer').innerHTML =
            `<span style="color:var(--red)">Error: ${evt.text}</span>`;
          log('Ask error: ' + evt.text, 'error');
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      btn.disabled = false; btn.textContent = 'Ask'; _askController = null; return;
    }
    // Backend offline – try direct ES + Ollama
    _backendOk = false; updateDirectBanner();
    log('Backend offline, switching to direct ES + Ollama…', 'warn');
    try {
      await directAsk(q, k, model, lang);
    } catch(e2) {
      setAskStatus('');
      document.getElementById('ask-answer-wrap').style.display = 'block';
      document.getElementById('ask-answer').innerHTML =
        `<span style="color:var(--red)">Direct mode failed: ${e2.message}</span>`;
      log('Direct ask failed: ' + e2.message, 'error');
    }
  }

  btn.disabled = false; btn.textContent = 'Ask';
  _askController = null;
}


// ── Backend / direct-mode detection ─────────────────────────────────────────
let _backendOk = null;   // null=unknown, true=available, false=offline

function updateDirectBanner() {
  const b = document.getElementById('direct-banner');
  if (b) b.style.display = _backendOk === false ? 'flex' : 'none';
}

// ── Service controls ─────────────────────────────────────────────────────────
async function backendAction(action) {
  const msgEl = document.getElementById('svc-bk-msg');
  if (msgEl) msgEl.textContent = action === 'restart' ? 'Restarting…' : 'Stopping…';
  try {
    await fetch(`${apiUrl()}/api/services/backend/${action}`, { method: 'POST' });
  } catch {}
  // Backend is shutting down — update state immediately
  setSvcDot('bk', false); _backendOk = false; updateDirectBanner();
  document.getElementById('svc-start-cmd').style.display = 'flex';
  if (action === 'stop') {
    if (msgEl) msgEl.textContent = '■ stopped';
    return;
  }
  // restart: poll until backend responds again
  if (msgEl) msgEl.textContent = 'Waiting for restart…';
  let tries = 0;
  const poll = setInterval(async () => {
    tries++;
    try {
      await fetch(apiUrl() + '/api/health', { signal: AbortSignal.timeout(1500) });
      clearInterval(poll);
      setSvcDot('bk', true); _backendOk = true; updateDirectBanner();
      document.getElementById('svc-start-cmd').style.display = 'none';
      if (msgEl) msgEl.textContent = '✓ restarted';
      refreshStatus();
      if (_statusLoaded) loadStatus(true);
    } catch {
      if (tries > 24) { clearInterval(poll); if (msgEl) msgEl.textContent = '✗ did not come back'; }
    }
  }, 1500);
}

async function updateSvcStatus() {
  try {
    const r = await fetch(apiUrl() + '/api/services/status', { signal: AbortSignal.timeout(3000) });
    const d = await r.json();
    setSvcDot('es', d.elasticsearch);
    setSvcDot('ol', d.ollama);
    setSvcDot('bk', true);
    _backendOk = true; updateDirectBanner();
    document.getElementById('svc-start-cmd').style.display = 'none';
  } catch {
    setSvcDot('es', null); setSvcDot('ol', null); setSvcDot('bk', false);
    _backendOk = false; updateDirectBanner();
    document.getElementById('svc-start-cmd').style.display = 'flex';
  }
}
function setSvcDot(id, status) {
  const el = document.getElementById('svc-' + id + '-dot');
  if (!el) return;
  el.className = 'svc-dot' + (status === true ? ' green' : status === false ? ' red' : '');
}
async function svcAction(service, action) {
  const short  = service === 'elasticsearch' ? 'es' : 'ol';
  const msgEl  = document.getElementById('svc-' + short + '-msg');
  if (msgEl) msgEl.textContent = action === 'start' ? 'Starting…' : 'Stopping…';
  try {
    const r = await fetch(`${apiUrl()}/api/services/${service}/${action}`, { method: 'POST' });
    let d;
    try { d = await r.json(); }
    catch (_) {
      // Empty / non-JSON body: usually another program answers on this port
      // (e.g. macOS AirPlay Receiver on :5000 when the URL is "localhost").
      throw new Error(`HTTP ${r.status} with no JSON — is ${apiUrl()} the catalog backend? Use http://127.0.0.1:5000`);
    }
    if (msgEl) msgEl.textContent = d.output || (d.ok ? '✓ done' : '✗ failed');
    // Poll twice: once after 1 s, again after 6 s (ES takes a moment to start)
    setTimeout(updateSvcStatus, 1200);
    const settleMs = (action === 'start' && service === 'elasticsearch') ? 9000 : 3000;
    setTimeout(() => { refreshStatus(); if (_statusLoaded) loadStatus(true); }, settleMs);
    if (action === 'start') setTimeout(updateSvcStatus, 6000);
  } catch(e) {
    if (msgEl) msgEl.textContent = 'Error: ' + e.message;
  }
}

// ── Direct-mode: search ES without backend ───────────────────────────────────
async function directEsSearch(query, limit, lang, mode) {
  // Mirror catalog_server.py /api/es-search exactly so results match the backend proxy.
  // mode: 'es-bm25' | 'es-hybrid' | 'es-semantic' | undefined → BM25 only
  //
  // Semantic / hybrid modes require kNN. In direct mode the backend fastembed model is
  // unavailable, so we try ES query_vector_builder (works if ES has an inference model
  // deployed). On failure we fall back to BM25 and log a warning.
  const langBoost = { de: 'content.de^3', es: 'content.es^3', en: 'content.en^3' };
  const contentFields = (lang && langBoost[lang])
    ? [langBoost[lang], 'content']
    : ['content'];

  const bm25Query = {
    multi_match: {
      query,
      fields:    contentFields,
      type:      'best_fields',
      fuzziness: 'AUTO',
    }
  };

  const src = ['path', 'rel_path', 'filename', 'ext', 'content', 'canonical_content',
               'chunk_index', 'srt_start_time', 'srt_end_time'];
  const hl  = { fields: { content: { fragment_size: 300, number_of_fragments: 2 } },
                pre_tags: ['**'], post_tags: ['**'] };

  const semantic     = mode === 'es-hybrid' || mode === 'es-semantic';
  const semanticOnly = mode === 'es-semantic';

  let body;
  if (semantic) {
    // Try ES inference-based kNN. ES model ID is configurable; fall back to BM25 if absent.
    const esModelId = document.getElementById('es-embed-model')?.value?.trim() || '';
    if (esModelId) {
      const knnBlock = {
        field: 'content_vector',
        query_vector_builder: { text_embedding: { model_id: esModelId, model_text: query } },
        k: limit,
        num_candidates: limit * 10,
        boost: 0.5,
      };
      body = { size: limit, _source: src, highlight: hl };
      if (semanticOnly) {
        body.knn = knnBlock;
      } else {
        body.query = bm25Query;
        body.knn   = knnBlock;
      }
    } else {
      // No ES inference model configured → fall back to BM25
      log(`⚠ Semantic search (${mode}) requires the backend or an ES inference model. Falling back to BM25.`, 'warn');
      body = { size: limit, _source: src, highlight: hl, query: bm25Query };
    }
  } else {
    body = { size: limit, _source: src, highlight: hl, query: bm25Query };
  }

  let r = await fetch(`${esUrl()}/${indexName()}/_search`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  // If kNN request failed (no ML model deployed in ES), retry with BM25 only
  if (!r.ok && semantic) {
    log(`⚠ ES kNN failed (${r.status}) — no inference model deployed. Falling back to BM25.`, 'warn');
    const fallbackBody = { size: limit, _source: src, highlight: hl, query: bm25Query };
    r = await fetch(`${esUrl()}/${indexName()}/_search`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fallbackBody),
    });
  }
  if (!r.ok) throw new Error('ES ' + r.status);
  const data = await r.json();

  // Group by path — ES returns one doc per chunk, renderResults() expects one per file.
  // Backend keeps the score of the FIRST hit (ES already returns hits sorted desc by score).
  const byPath = new Map();
  for (const hit of (data.hits?.hits || [])) {
    const src   = hit._source || {};
    const score = hit._score  || 0;
    const pk    = src.path || '';

    const hl      = hit.highlight?.content || [];
    const context = hl.length ? hl.join(' … ') : (src.content || '').slice(0, 400);

    const matchEntry = {
      line:          src.chunk_index || 0,
      context:       context.slice(0, 400),
      highlight:     query,
      score:         Math.round(score * 1e4) / 1e4,
      chunk_content: src.content || '',
    };
    if ((src.ext === 'srt' || src.ext === 'vtt') && src.srt_start_time != null)
      matchEntry.srt_time = src.srt_start_time;

    if (byPath.has(pk)) {
      const e = byPath.get(pk);
      e.matches.push(matchEntry);
      e.match_count++;
      // Score not updated — first hit already has the highest score
    } else {
      byPath.set(pk, {
        path:        pk,
        rel_path:    src.rel_path || pk,
        file:        src.filename || pk.split(/[/\\]/).pop(),
        type:        src.ext || '',
        score:       Math.round(score * 1e4) / 1e4,
        match_count: 1,
        matches:     [matchEntry],
      });
    }
  }

  // Sort desc by score — same as backend sorted(…, key=lambda r: r["score"], reverse=True)
  const results = Array.from(byPath.values()).sort((a, b) => b.score - a.score);
  return {
    results,
    total: results.reduce((s, r) => s + r.match_count, 0),
    mode:  { engine: 'es-direct' },
  };
}

async function ollamaDirectAnswer(url, preferred, messages) {
  const tags = await fetch(url + '/api/tags', {signal: AbortSignal.timeout(5000)});
  if (!tags.ok) throw new Error('Cannot list installed Ollama models.');
  const entries = (await tags.json()).models || [];
  const cloud = [], local = [];
  for (const entry of entries) {
    const name = entry.name || '';
    if (!name || /embed|bge-|nomic-/i.test(name)) continue;
    const group = entry.remote_host || entry.remote_model || name.endsWith(':cloud') ? cloud : local;
    if (!group.includes(name)) group.push(name);
  }
  for (const group of [cloud, local]) {
    const index = group.indexOf(preferred);
    if (index >= 0) group.unshift(...group.splice(index, 1));
  }
  const deadline = Date.now() + 120000;
  for (const model of [...cloud, ...local]) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    try {
      const response = await fetch(url + '/api/chat', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        signal: AbortSignal.timeout(Math.min(30000, remaining)),
        body: JSON.stringify({model, messages, stream: false})
      });
      if (!response.ok) continue;
      const data = await response.json();
      if (!data.error && data.done && data.message?.content?.trim()) {
        return {model, text: data.message.content};
      }
    } catch { /* A failed attempt must not leak partial answers. */ }
  }
  throw new Error('All installed Ollama chat models failed or the request timed out.');
}

// ── Direct-mode: ask ES + Ollama without backend ─────────────────────────────
async function directAsk(q, k, model, lang) {
  // 1. Retrieve chunks from ES
  setAskStatus('Searching ES (direct)…');
  const esBody = {
    size: k,
    query: { multi_match: { query: q, fields: ['content','filename^2'], fuzziness: 'AUTO' } },
    _source: ['content','filename','path','rel_path','srt_start_time']
  };
  const esR = await fetch(`${esUrl()}/${indexName()}/_search`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(esBody)
  });
  if (!esR.ok) throw new Error('ES ' + esR.status);
  const esData = await esR.json();
  const hits   = esData.hits?.hits || [];

  // Show sources
  if (hits.length) {
    const bar = document.getElementById('ask-sources-bar');
    bar.innerHTML = '';
    hits.forEach((h, i) => {
      const s    = h._source || {};
      const chip = document.createElement('div');
      chip.className = 'source-chip'; chip.style.cursor = 'pointer';
      chip.innerHTML = `${getFileIcon(s.filename || '')} [${i+1}] ${escHtml(s.filename || '')}` +
        `<span class="chip-score">${h._score.toFixed(3)}</span>` +
        (s.srt_start_time != null ? `<span class="chip-time"> @${fmtTime(s.srt_start_time)}</span>` : '');
      chip.onclick = () => openFileModal(s.path || s.rel_path || s.filename, s.srt_start_time ?? null, s.content || '');
      bar.appendChild(chip);
    });
    document.getElementById('ask-sources').style.display = 'block';
  }

  // 2. Build context
  const context = hits.map((h,i) =>
    `[${i+1}] ${h._source.filename||''}\n${(h._source.content||'').slice(0,600)}`
  ).join('\n\n');

  setAskStatus('Trying installed cloud models, then local fallback…');
  const result = await ollamaDirectAnswer(ollamaUrl(), model, [
    {role: 'system', content: `Answer using these excerpts:\n\n${context}`},
    {role: 'user', content: q}
  ]);
  document.getElementById('ask-answer-wrap').style.display = 'block';
  document.getElementById('ask-answer').innerHTML = renderAnswer(result.text);
  setAskStatus('');
  log(`Direct ask complete (model: ${result.model})`, 'ok');
}

// ── Tools panel ─────────────────────────────────────────────────────────────
let _toolsConfigLoaded = false;
let _origEmbedModel = '';

function getId(id) { return document.getElementById(id).value.trim(); }

function toggleToolCard(id) {
  document.getElementById(id).classList.toggle('open');
}

function toolOut(id, msg, append) {
  const el = document.getElementById(id);
  el.classList.add('visible');
  if (append) {
    el.textContent += msg + '\n';
    // thousands of file lines make the <pre> sluggish → keep only the tail
    if (el.textContent.length > 60000) el.textContent = '… (older lines trimmed)\n' + el.textContent.slice(-40000);
  } else      { el.textContent = msg; }
  el.scrollTop = el.scrollHeight;
}

async function loadToolsConfig() {
  if (_toolsConfigLoaded) return;
  try {
    const r = await fetch(apiUrl() + '/api/tools/config');
    if (r.ok) {
      const d = await r.json();
      const sel = document.getElementById('tool-embed-model');
      if (d.embedding_model) { sel.value = d.embedding_model; _origEmbedModel = d.embedding_model; }
      // Canonical language settings
      const chk = document.getElementById('canonical-lang-enabled');
      if (chk) {
        const active = d.canonical_language && d.translation_mode !== 'disabled';
        chk.checked = !!active;
        const langSel = document.getElementById('canonical-lang-select');
        if (langSel && d.canonical_language) langSel.value = d.canonical_language;
        const transModel = document.getElementById('canonical-trans-model');
        if (transModel && d.translation_model) transModel.value = d.translation_model;
      }
      onEmbedModelChange();
    }
    _toolsConfigLoaded = true;
  } catch(e) {
    log('Tools config load failed: ' + e.message, 'warn');
  }
}

// Models that are natively multilingual — no translation needed
const _MULTILINGUAL_MODELS = new Set([
  'intfloat/multilingual-e5-small',
  'sentence-transformers/LaBSE',
]);

function onEmbedModelChange() {
  const btn = document.getElementById('tool-reindex-btn');
  const cur = document.getElementById('tool-embed-model').value;
  btn.disabled = (cur === _origEmbedModel);
  btn.title = btn.disabled ? 'Change model to enable' : 'Reindex with new model';
  _updateCanonicalLangVisibility(cur);
}

function _updateCanonicalLangVisibility(model) {
  const section   = document.getElementById('canonical-lang-section');
  const optsDiv   = document.getElementById('canonical-lang-opts');
  const noteDiv   = document.getElementById('canonical-lang-multilingual-note');
  const chk       = document.getElementById('canonical-lang-enabled');
  if (!section) return;
  const isMultilingual = _MULTILINGUAL_MODELS.has(model);
  // Hide canonical opts and show note for multilingual models
  if (noteDiv) noteDiv.style.display = isMultilingual ? '' : 'none';
  if (chk) chk.disabled = isMultilingual;
  if (optsDiv) optsDiv.style.display = (!isMultilingual && chk && chk.checked) ? '' : 'none';
}

function onCanonicalLangChange() {
  const cur = document.getElementById('tool-embed-model').value;
  _updateCanonicalLangVisibility(cur);
}

async function saveEmbeddingModel() {
  const model        = document.getElementById('tool-embed-model').value;
  const chk          = document.getElementById('canonical-lang-enabled');
  const isMultilingual = _MULTILINGUAL_MODELS.has(model);
  const canonEnabled = chk && chk.checked && !isMultilingual;
  const canonical_language = canonEnabled
    ? (document.getElementById('canonical-lang-select')?.value || '')
    : '';
  const translation_model  = document.getElementById('canonical-trans-model')?.value || 'translategemma:4b';
  const translation_mode   = canonEnabled ? 'background' : 'disabled';

  toolOut('tool-embed-out', 'Saving config…');
  try {
    const r = await fetch(apiUrl() + '/api/tools/config', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ embedding_model: model, canonical_language, translation_model, translation_mode })
    });
    const d = await r.json();
    if (d.ok) {
      _origEmbedModel = model;
      onEmbedModelChange();
      let msg = 'Config saved.\nModel: ' + model;
      if (d.canonical_language) msg += '\nCanonical language: ' + d.canonical_language + ' (' + translation_mode + ')';
      msg += '\n' + d.note;
      toolOut('tool-embed-out', msg);
      log('Embedding config saved: ' + model, 'ok');
    } else {
      toolOut('tool-embed-out', 'Error: ' + JSON.stringify(d));
    }
  } catch(e) {
    toolOut('tool-embed-out', 'Error: ' + e.message);
  }
}

// ── Shared active-task banner (shown in Status panel) ────────────────────────
function _showActiveBanner(title) {
  const banner = document.getElementById('active-task-banner');
  const titleEl = document.getElementById('active-task-banner-title-text');
  if (banner)  banner.classList.add('visible');
  if (titleEl) titleEl.textContent = title;
}
function _updateActiveBanner(pct, n, total) {
  const fill  = document.getElementById('active-task-banner-fill');
  const label = document.getElementById('active-task-banner-label');
  if (fill)  fill.style.width = pct + '%';
  if (label) label.textContent = `${pct}%  —  ${n} / ${total} files`;
}
function _hideActiveBanner(delay = 0) {
  setTimeout(() => {
    // Only hide if no other task is using it
    if (!_idxEvt && !_reindexEvt) {
      const banner = document.getElementById('active-task-banner');
      if (banner) banner.classList.remove('visible');
    }
  }, delay);
}

// ── Reindex (Embedding Model card) ──────────────────────────────────────────
let _reindexEvt = null;

function _setReindexBusy(busy) {
  const btn  = document.getElementById('tool-reindex-btn');
  const wrap = document.getElementById('reindex-progress-wrap');
  if (wrap) wrap.classList.toggle('visible', busy);
  if (btn) {
    if (busy) {
      btn.disabled = true;
      btn._wasBusyText = btn.textContent;
      btn.textContent = '⏳ Reindexing…';
    } else {
      btn.textContent = '↺ Reindex';
      onEmbedModelChange(); // restore correct disabled/enabled state
    }
  }
  if (!busy) {
    const fill  = document.getElementById('reindex-progress-fill');
    const label = document.getElementById('reindex-progress-label');
    if (fill)  fill.style.width = '0%';
    if (label) label.textContent = '';
  }
}

function _updateReindexProgress(pct, n, total) {
  const fill  = document.getElementById('reindex-progress-fill');
  const label = document.getElementById('reindex-progress-label');
  if (fill)  fill.style.width = pct + '%';
  if (label) label.textContent = `${pct}%  —  ${n} / ${total} files`;
  _updateActiveBanner(pct, n, total);
  log(`Reindex: ${pct}% (${n}/${total} files)`, 'info');
}

async function runReindex() {
  if (_reindexEvt) { toolOut('tool-embed-out', 'Reindex already in progress. Please wait.'); return; }
  const reindexExts = [...document.querySelectorAll('#tool-reindex-ext-grid input:checked')].map(c => c.value);
  const ext_filter = reindexExts.join(',');
  const extNote = ext_filter ? ` [types: ${ext_filter}]` : ' [all types]';
  toolOut('tool-embed-out', `Starting reindex${extNote}… this may take a few minutes.\n`);
  log('Reindex started', 'info');
  _setReindexBusy(true);
  _showActiveBanner('Reindex in progress…');

  let taskId;
  try {
    const r = await fetch(apiUrl() + '/api/tools/reindex', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ ext_filter: ext_filter || undefined })
    });
    const d = await r.json();
    if (!d.task_id) { toolOut('tool-embed-out', 'Error: ' + JSON.stringify(d)); _setReindexBusy(false); _hideActiveBanner(); return; }
    taskId = d.task_id;
  } catch(e) {
    toolOut('tool-embed-out', 'Error: ' + e.message); _setReindexBusy(false); _hideActiveBanner(); return;
  }

  _reindexEvt = followTask(taskId, _taskHandlers('reindex', 'tool-embed-out', {
    title: 'Reindex', okMsg: '\nReindex complete.',
    setBusy: _setReindexBusy, clear: () => { _reindexEvt = null; },
  }));
}

async function pollToolStatus(taskId, outId) {
  const poll = async () => {
    try {
      const r = await fetch(apiUrl() + '/api/tools/status/' + taskId);
      const d = await r.json();
      const lines = Array.isArray(d.log) ? d.log.join('\n') : (d.log || '');
      toolOut(outId, `[${d.status}]\n` + lines.trim());
      if (d.status === 'running' || d.status === 'starting') {
        setTimeout(poll, 2000);
      } else {
        log(`Task ${taskId} ${d.status}`, d.status === 'done' ? 'ok' : 'error');
      }
    } catch(e) {
      toolOut(outId, 'Poll error: ' + e.message, true);
    }
  };
  poll();
}

// ── Resilient background-task follower (index / reindex) ─────────────────────
// - resumes the SSE stream after a dropped connection (?from=<last line>) instead of
//   declaring the task finished (the task keeps running on the server)
// - shows current file, embedding sub-progress, elapsed / ETA and "still working" hints
function _fmtDur(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}h${String(m).padStart(2, '0')}m` : m ? `${m}m${String(x).padStart(2, '0')}s` : `${x}s`;
}

function followTask(taskId, h) {
  let es = null, seq = 0, finished = false, retry = 0, timer = null;
  const handle = { close() { finished = true; clearTimeout(timer); if (es) es.close(); } };
  function finish(status) {
    if (finished) return;
    finished = true; clearTimeout(timer);
    if (es) es.close();
    h.onDone(status);
  }
  function connect() {
    if (finished) return;
    es = new EventSource(`${apiUrl()}/api/tools/stream/${taskId}?from=${seq}`);
    es.onopen = () => { retry = 0; h.onState('live'); };
    es.addEventListener('log',       e => { seq = parseInt(e.lastEventId) || seq + 1; h.onLog(JSON.parse(e.data)); });
    es.addEventListener('progress',  e => h.onSnap(JSON.parse(e.data), false));
    es.addEventListener('heartbeat', e => h.onSnap(JSON.parse(e.data), true));
    es.addEventListener('done',      e => finish(JSON.parse(e.data).status));
    es.addEventListener('error', async e => {
      if (e.data) { finish('lost'); return; }          // server says "Unknown task" (backend restarted)
      if (finished) return;
      es.close(); es = null;
      h.onState('reconnecting');
      try {                                              // did it finish while we were disconnected?
        const r = await fetch(`${apiUrl()}/api/tools/status/${taskId}`, { signal: AbortSignal.timeout(4000) });
        if (r.status === 404) { finish('lost'); return; }
        const d = await r.json();
        if (d.status === 'done' || d.status === 'error') { finish(d.status); return; }
      } catch (_) { /* backend unreachable — keep retrying */ }
      retry++;
      timer = setTimeout(connect, Math.min(5000, 1000 * retry));
    });
  }
  connect();
  return handle;
}

function _renderTaskProgress(kind, p, state) {
  const fill  = document.getElementById(`${kind}-progress-fill`);
  const label = document.getElementById(`${kind}-progress-label`);
  const pct = p.total ? Math.round(p.n * 100 / p.total) : 0;
  if (fill) fill.style.width = pct + '%';
  const eta = (p.n >= 10 && p.total > p.n) ? p.elapsed / p.n * (p.total - p.n) : null;
  const e = escapeHtml;
  let l1 = p.total ? `<b>${pct}%</b> — ${p.n.toLocaleString()} / ${p.total.toLocaleString()} files`
                   : `⏳ ${e(p.phase || 'starting')}…`;
  l1 += ` · ⏱ ${_fmtDur(p.elapsed)}` + (eta != null ? ` · ETA ~${_fmtDur(eta)}` : '');
  let l2 = '';
  if (p.phase === 'embedding' && p.embed_total) {
    l2 = `🧠 embedding ${p.embed_done.toLocaleString()}/${p.embed_total.toLocaleString()} chunks `
       + `(${Math.round(p.embed_done * 100 / p.embed_total)}%) · ~${_fmtDur(p.embed_eta)} left — ${e(p.file)}`;
  } else if (p.file) {
    l2 = `📄 ${e(p.file)}${p.file_chunks ? ` (${p.file_chunks} chunks)` : ''}`;
  }
  let l3 = '';
  if (state === 'reconnecting')
    l3 = '<span style="color:var(--red)">⚠ connection to backend lost — reconnecting… (the task keeps running on the server)</span>';
  else if (p.idle >= 45)
    l3 = `<span style="color:var(--yellow)">⏳ no output for ${_fmtDur(p.idle)} — still running`
       + `${p.phase === 'embedding' ? '' : ' (large file or slow disk / network share)'}</span>`;
  if (label) label.innerHTML = [l1, l2, l3].filter(Boolean).join('<br>');
  _updateActiveBanner(pct, p.n, p.total);
}

function _taskHandlers(kind, outId, o) {
  let state = 'live', last = null, loggedAt = -1;
  return {
    onLog: line => toolOut(outId, line, true),
    onSnap: (p, beat) => {
      last = p;
      _renderTaskProgress(kind, p, state);
      if (!beat && p.n && p.n !== loggedAt && p.n % 25 === 0) {   // keep the console log readable
        loggedAt = p.n;
        log(`${o.title}: ${p.percent}% (${p.n}/${p.total} files)`, 'info');
      }
    },
    onState: s => {
      state = s;
      if (last) _renderTaskProgress(kind, last, state);
      else {
        const l = document.getElementById(`${kind}-progress-label`);
        if (l) l.textContent = s === 'reconnecting' ? '⚠ reconnecting to backend…' : 'Starting…';
      }
    },
    onDone: status => {
      const ok = status === 'done';
      if (status === 'lost')
        toolOut(outId, '\n⚠ Lost track of the task (backend restarted?). Check the server console.', true);
      else
        toolOut(outId, ok ? o.okMsg : '\nFinished with errors.', true);
      log(`${o.title} ${ok ? 'completed' : status === 'lost' ? 'lost' : 'failed'}`, ok ? 'ok' : 'error');
      if (ok) {
        const fill  = document.getElementById(`${kind}-progress-fill`);
        const label = document.getElementById(`${kind}-progress-label`);
        if (fill)  fill.style.width = '100%';
        if (label) label.textContent = '100% — Done' + (last ? ` in ${_fmtDur(last.elapsed)}` : '');
      }
      o.clear();
      setTimeout(() => { o.setBusy(false); _hideActiveBanner(500); }, 1500);
    },
  };
}

// ── Index Directory ──────────────────────────────────────────────────────────
let _idxEvt = null;  // active task follower (handle with .close()) for index-directory

function _setIndexBusy(busy) {
  const btn  = document.getElementById('btn-idx-run');
  const wrap = document.getElementById('idx-progress-wrap');
  if (btn)  { btn.disabled = busy; btn.textContent = busy ? '⏳ Indexing…' : '▶ Index directory'; }
  if (wrap) { wrap.classList.toggle('visible', busy); }
  if (!busy) {
    const fill  = document.getElementById('idx-progress-fill');
    const label = document.getElementById('idx-progress-label');
    if (fill)  fill.style.width = '0%';
    if (label) label.textContent = '';
  }
}

function _updateIndexProgress(pct, n, total) {
  const fill  = document.getElementById('idx-progress-fill');
  const label = document.getElementById('idx-progress-label');
  if (fill)  fill.style.width = pct + '%';
  if (label) label.textContent = `${pct}%  —  ${n} / ${total} files`;
  _updateActiveBanner(pct, n, total);
  log(`Indexing: ${pct}% (${n}/${total} files)`, 'info');
}

async function runIndexDir() {
  if (_idxEvt) { toolOut('tool-idx-out', 'Indexing already in progress. Please wait.'); return; }
  const path = getId('tool-idx-path');
  if (!path) { toolOut('tool-idx-out', 'Please enter a directory path.'); return; }
  const reset = document.getElementById('tool-idx-reset').checked;
  const no_vectors = document.getElementById('tool-idx-novectors').checked;
  const batch = parseInt(document.getElementById('tool-idx-batch').value) || 50;
  const canonical_lang = document.getElementById('tool-idx-lang').value;
  const idxExts = [...document.querySelectorAll('#tool-idx-ext-grid input:checked')].map(c => c.value);
  const ext_filter = idxExts.join(',');

  const mode = reset ? 'Full reindex (reset)' : 'Incremental';
  const extNote = ext_filter ? ` [types: ${ext_filter}]` : ' [all types]';
  toolOut('tool-idx-out', `${mode}${extNote} — indexing ${path}…\n`);
  _setIndexBusy(true);
  _showActiveBanner('Index directory in progress…');
  log('Index directory started: ' + path, 'info');

  let taskId;
  try {
    const r = await fetch(apiUrl() + '/api/tools/index-directory', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ path, reset, no_vectors, batch, canonical_lang, ext_filter })
    });
    const d = await r.json();
    if (d.error) { toolOut('tool-idx-out', 'Error: ' + d.error); _setIndexBusy(false); return; }
    if (!d.task_id) { toolOut('tool-idx-out', 'Error: ' + JSON.stringify(d)); _setIndexBusy(false); return; }
    taskId = d.task_id;
  } catch(e) {
    toolOut('tool-idx-out', 'Error: ' + e.message); _setIndexBusy(false); return;
  }

  _followIndexTask(taskId);
}

// Follow an index task (auto-reconnecting SSE) with the shared progress bar; refresh the
// saved-folder list when it ends so "last scan" / chunk counts update.
function _followIndexTask(taskId) {
  _idxEvt = followTask(taskId, _taskHandlers('idx', 'tool-idx-out', {
    title: 'Index directory', okMsg: '\nDone.',
    setBusy: _setIndexBusy, clear: () => { _idxEvt = null; loadScanFolders(); },
  }));
}

// ── Path Manager ────────────────────────────────────────────────────────────
async function loadIndexedPaths() {
  toolOut('tool-paths-out', 'Loading paths from index…');
  try {
    const r = await fetch(apiUrl() + '/api/tools/paths');
    const d = await r.json();
    if (d.error) { toolOut('tool-paths-out', 'Error: ' + d.error); return; }

    // Show base directories
    const dirsEl = document.getElementById('tool-paths-dirs');
    const replEl = document.getElementById('tool-paths-replace');
    dirsEl.style.display = 'block';
    replEl.style.display = 'block';

    let html = `<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:6px;">
      Base directories (${d.total_files} files total)</div>`;
    if (d.base_directories && d.base_directories.length) {
      html += '<div style="display:flex;flex-direction:column;gap:4px;">';
      for (const bd of d.base_directories) {
        html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:var(--code-bg);border-radius:5px;font-size:12px;font-family:monospace;">
          <span style="flex:1;word-break:break-all;cursor:pointer;" onclick="document.getElementById('tool-paths-old').value=this.textContent.trim()" title="Click to copy to Old prefix">${bd.path}</span>
          <span style="color:var(--muted);font-size:11px;white-space:nowrap;">${bd.files} file(s)</span>
          <button class="secondary" style="font-size:10px;padding:2px 8px;" onclick="verifySingleDir('${bd.path.replace(/'/g,"\\'")}')">verify</button>
        </div>`;
      }
      html += '</div>';
    } else {
      html += '<div style="color:var(--muted);font-size:12px;">No files indexed.</div>';
    }
    dirsEl.innerHTML = html;
    toolOut('tool-paths-out', `Loaded ${d.total_files} indexed file paths across ${d.base_directories.length} base directories.`);
  } catch(e) {
    toolOut('tool-paths-out', 'Error: ' + e.message);
  }
}

async function verifyAllPaths() {
  toolOut('tool-paths-out', 'Verifying all indexed file paths…');
  const verEl = document.getElementById('tool-paths-verify');
  verEl.style.display = 'block';
  verEl.innerHTML = '<div style="display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px;"><span class="spinner"></span> Checking files…</div>';
  try {
    const r = await fetch(apiUrl() + '/api/tools/verify-paths', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({})
    });
    const d = await r.json();
    if (d.error) { verEl.innerHTML = ''; toolOut('tool-paths-out', 'Error: ' + d.error); return; }
    _renderVerifyResult(verEl, d);
  } catch(e) {
    verEl.innerHTML = '';
    toolOut('tool-paths-out', 'Error: ' + e.message);
  }
}

async function verifySingleDir(dir) {
  const verEl = document.getElementById('tool-paths-verify');
  verEl.style.display = 'block';
  verEl.innerHTML = '<div style="display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px;"><span class="spinner"></span> Checking ' + dir + '…</div>';
  try {
    const r = await fetch(apiUrl() + '/api/tools/verify-paths', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ directory: dir })
    });
    const d = await r.json();
    if (d.error) { verEl.innerHTML = ''; toolOut('tool-paths-out', 'Error: ' + d.error); return; }
    _renderVerifyResult(verEl, d, dir);
  } catch(e) {
    verEl.innerHTML = '';
    toolOut('tool-paths-out', 'Error: ' + e.message);
  }
}

function _renderVerifyResult(el, d, dir) {
  const pct = d.total ? Math.round(d.existing / d.total * 100) : 0;
  const color = d.healthy ? 'var(--green)' : 'var(--red)';
  const icon = d.healthy ? '✓' : '✗';
  let html = `<div style="padding:10px 12px;background:var(--code-bg);border-radius:6px;font-size:12px;">
    <div style="font-weight:600;margin-bottom:6px;color:${color};">${icon} ${dir ? dir : 'All paths'}: ${d.existing}/${d.total} files exist (${pct}%)</div>`;
  if (d.missing_count > 0) {
    html += `<details><summary style="cursor:pointer;font-size:11px;color:var(--muted);">${d.missing_count} missing file(s)</summary>
      <div style="max-height:200px;overflow-y:auto;margin-top:6px;">`;
    for (const p of d.missing) {
      html += `<div style="font-size:11px;font-family:monospace;color:var(--red);padding:1px 0;word-break:break-all;">${p}</div>`;
    }
    html += '</div></details>';
  }
  html += '</div>';
  el.innerHTML = html;
}

async function replacePathPrefix() {
  const oldPfx = getId('tool-paths-old');
  const newPfx = getId('tool-paths-new');
  if (!oldPfx || !newPfx) { toolOut('tool-paths-out', 'Both old and new prefix are required.'); return; }
  toolOut('tool-paths-out', `Replacing "${oldPfx}" → "${newPfx}"…`);
  try {
    const r = await fetch(apiUrl() + '/api/tools/update-paths', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ old_prefix: oldPfx, new_prefix: newPfx })
    });
    const d = await r.json();
    if (d.error) { toolOut('tool-paths-out', 'Error: ' + d.error); return; }
    toolOut('tool-paths-out', `Done — ${d.updated} document(s) updated.`);
    log(`Path prefix updated: ${d.updated} docs`, 'ok');
  } catch(e) {
    toolOut('tool-paths-out', 'Error: ' + e.message);
  }
}

// ── Saved folders (persistent list of scanned paths) ─────────────────────────
// Source of truth: the backend registry (data/scan_registry.json). The last list is also
// cached in localStorage so it renders instantly and still shows when the backend is down.
const _SF_KEY = 'cc_scan_folders';
let _scanFolders = [];
let _sfEditing = null;                       // id of the folder whose path is being edited

function _sfAgo(iso) {
  if (!iso) return 'never scanned';
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!isFinite(s)) return iso;
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + ' min ago';
  if (s < 129600) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' d ago';
}
function _sfCache(list) {
  try { localStorage.setItem(_SF_KEY, JSON.stringify(list.map(f => ({ id: f.id, path: f.path, last_scan_at: f.last_scan_at, docs: f.docs, ext_filter: f.ext_filter })))); } catch (_) {}
}
function _sfFromCache() {
  try { return JSON.parse(localStorage.getItem(_SF_KEY) || '[]'); } catch (_) { return []; }
}

async function loadScanFolders() {
  const box = document.getElementById('scan-folders-list');
  if (!box) return;
  try {
    const r = await fetch(apiUrl() + '/api/tools/scan-paths', { signal: AbortSignal.timeout(20000) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    _scanFolders = (await r.json()).folders || [];
    _sfCache(_scanFolders);
    _renderScanFolders(false);
  } catch (e) {
    _scanFolders = _sfFromCache();
    _renderScanFolders(true);
  }
}

function _renderScanFolders(offline) {
  const box = document.getElementById('scan-folders-list');
  if (!box) return;
  if (!_scanFolders.length) {
    box.innerHTML = `<div class="sf-empty">${offline ? 'Backend not reachable.' : 'No folders yet — type a path below and press “＋ Save” or “Index directory”.'}</div>`;
    return;
  }
  const e = escapeHtml;
  box.innerHTML = (offline ? '<div class="sf-empty">⚠ Backend not reachable — showing the last saved list (read-only).</div>' : '') +
    _scanFolders.map(f => {
      const dot = offline ? ['var(--muted)', 'status unknown']
                : f.running ? ['var(--yellow)', 'scan running']
                : f.exists === false ? ['var(--red)', 'folder not found on disk — use ✎ to point to its new location']
                : ['var(--green)', 'folder found'];
      const meta = (f.docs != null ? `${Number(f.docs).toLocaleString()} chunks · ` : '') + _sfAgo(f.last_scan_at);
      const dis = (offline || f.running) ? 'disabled' : '';
      if (_sfEditing === f.id) {
        return `<div class="sf-row" data-id="${f.id}"><span class="sf-dot" style="color:${dot[0]}">●</span>` +
          `<input class="sf-edit" type="text" value="${e(f.path)}" spellcheck="false" title="New location of this folder">` +
          `<button class="sf-btn" data-act="save" title="Save the new location and update the index">✔</button>` +
          `<button class="sf-btn secondary" data-act="cancel" title="Cancel">✕</button></div>`;
      }
      return `<div class="sf-row" data-id="${f.id}"><span class="sf-dot" style="color:${dot[0]}" title="${e(dot[1])}">●</span>` +
        `<span class="sf-path" data-act="use" title="${e(f.path)}\n(click to load it into the form below)">${e(f.path)}</span>` +
        `<span class="sf-meta">${e(meta)}</span>` +
        `<button class="sf-btn secondary" data-act="refresh" ${dis} title="Re-scan now (incremental: only new / changed files)">↻</button>` +
        `<button class="sf-btn secondary" data-act="edit" ${offline ? 'disabled' : ''} title="Change location (folder moved on disk) — updates the index">✎</button>` +
        `<button class="sf-btn secondary" data-act="remove" ${offline ? 'disabled' : ''} title="Remove from the list">🗑</button></div>`;
    }).join('');
  const input = box.querySelector('.sf-edit');
  if (input) { input.focus(); input.select(); input.onkeydown = ev => { if (ev.key === 'Enter') saveScanFolder(_sfEditing); if (ev.key === 'Escape') { _sfEditing = null; _renderScanFolders(false); } }; }
}

document.getElementById('scan-folders-list')?.addEventListener('click', ev => {
  const b = ev.target.closest('[data-act]');
  if (!b || b.disabled) return;
  const id = b.closest('.sf-row')?.dataset.id;
  const act = b.dataset.act;
  if (act === 'refresh') refreshScanFolder(id);
  else if (act === 'edit')   { _sfEditing = id; _renderScanFolders(false); }
  else if (act === 'cancel') { _sfEditing = null; _renderScanFolders(false); }
  else if (act === 'save')   saveScanFolder(id);
  else if (act === 'remove') removeScanFolder(id);
  else if (act === 'use')    useScanFolder(id);
});

function useScanFolder(id) {
  const f = _scanFolders.find(x => x.id === id);
  if (!f) return;
  document.getElementById('tool-idx-path').value = f.path;
  const exts = new Set(String(f.ext_filter || '').split(',').filter(Boolean));
  document.querySelectorAll('#tool-idx-ext-grid input').forEach(c => { c.checked = exts.has(c.value); });
  if (f.no_vectors != null) document.getElementById('tool-idx-novectors').checked = !!f.no_vectors;
  if (f.canonical_lang != null) document.getElementById('tool-idx-lang').value = f.canonical_lang || '';
  if (f.batch) document.getElementById('tool-idx-batch').value = f.batch;
}

async function addScanFolder() {
  const path = getId('tool-idx-path');
  if (!path) { toolOut('tool-idx-out', 'Please enter a directory path.'); return; }
  const ext_filter = [...document.querySelectorAll('#tool-idx-ext-grid input:checked')].map(c => c.value).join(',');
  try {
    const r = await fetch(apiUrl() + '/api/tools/scan-paths', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, ext_filter,
        no_vectors: document.getElementById('tool-idx-novectors').checked,
        batch: parseInt(document.getElementById('tool-idx-batch').value) || 50,
        canonical_lang: document.getElementById('tool-idx-lang').value }),
    });
    const d = await r.json();
    if (!r.ok) { toolOut('tool-idx-out', 'Error: ' + (d.error || r.status)); return; }
    toolOut('tool-idx-out', `Saved ${d.entry.path} — press ↻ in the list to scan it.`);
    await loadScanFolders();
  } catch (e) { toolOut('tool-idx-out', 'Error: ' + e.message); }
}

async function refreshScanFolder(id) {
  const f = _scanFolders.find(x => x.id === id);
  if (!f) return;
  if (_idxEvt) { toolOut('tool-idx-out', 'Indexing already in progress. Please wait.'); return; }
  toolOut('tool-idx-out', `Re-scanning ${f.path} (incremental)…\n`);
  _setIndexBusy(true);
  _showActiveBanner('Index directory in progress…');
  try {
    const r = await fetch(apiUrl() + `/api/tools/scan-paths/${id}/refresh`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) {
      toolOut('tool-idx-out', 'Error: ' + (d.error || r.status), true);
      if (d.task_id) { _followIndexTask(d.task_id); return; }     // already running → just follow it
      _setIndexBusy(false); _hideActiveBanner(); return;
    }
    _followIndexTask(d.task_id);
    loadScanFolders();
  } catch (e) {
    toolOut('tool-idx-out', 'Error: ' + e.message, true);
    _setIndexBusy(false); _hideActiveBanner();
  }
}

async function _sfPut(id, body) {
  const r = await fetch(apiUrl() + '/api/tools/scan-paths/' + id, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
}

async function saveScanFolder(id) {
  const f = _scanFolders.find(x => x.id === id);
  const np = document.querySelector('#scan-folders-list .sf-edit')?.value.trim();
  if (!f) return;
  if (!np || np === f.path) { _sfEditing = null; _renderScanFolders(false); return; }
  try {
    const q = await _sfPut(id, { path: np, dry_run: true });
    if (!confirm(`Move this folder in the index?\n\nFrom: ${f.path}\nTo:      ${np}\n\n` +
                 `${q.chunks.toLocaleString()} indexed chunks will point to the new location (their text and vectors are kept).\n` +
                 `The files must already be at the new location. Large indexes can take a few minutes.`)) return;
    const r = await _sfPut(id, { path: np });
    toolOut('tool-idx-out', `Moving ${r.chunks.toLocaleString()} chunks to ${np} …`);
    for (let i = 0; i < 4800; i++) {                      // up to ~2 h, polled every 1.5 s
      await new Promise(res => setTimeout(res, 1500));
      const st = await (await fetch(apiUrl() + '/api/tools/scan-paths/relocation/' + r.task_id)).json();
      if (st.status === 'running') { toolOut('tool-idx-out', `Updating the index… ${st.elapsed}s`); continue; }
      if (st.status === 'done') {
        toolOut('tool-idx-out', `Done — ${st.updated.toLocaleString()} chunks now point to ${np}.`);
        log(`Folder moved: ${f.path} → ${np} (${st.updated} chunks)`, 'ok');
      } else toolOut('tool-idx-out', 'Error: ' + st.error);
      break;
    }
    _sfEditing = null;
    await loadScanFolders();
  } catch (e) { toolOut('tool-idx-out', 'Error: ' + e.message); }
}

async function removeScanFolder(id) {
  const f = _scanFolders.find(x => x.id === id);
  if (!f) return;
  if (!confirm(`Remove this folder from the list?\n\n${f.path}\n\nIts indexed data stays in Elasticsearch unless you choose to delete it next.`)) return;
  let purge = false;
  if (f.docs) purge = confirm(`Also DELETE its ${Number(f.docs).toLocaleString()} indexed chunks from Elasticsearch?\n\nOK = delete them too · Cancel = keep them.`);
  try {
    const r = await fetch(apiUrl() + `/api/tools/scan-paths/${id}${purge ? '?purge=1' : ''}`, { method: 'DELETE' });
    const d = await r.json();
    if (!r.ok) { toolOut('tool-idx-out', 'Error: ' + (d.error || r.status)); return; }
    toolOut('tool-idx-out', `Removed ${d.removed}` + (purge ? ` — ${d.deleted_chunks.toLocaleString()} chunks deleted.` : ' (index data kept).'));
    await loadScanFolders();
  } catch (e) { toolOut('tool-idx-out', 'Error: ' + e.message); }
}

// show the cached list immediately, then refresh from the backend
_scanFolders = _sfFromCache();
if (_scanFolders.length) _renderScanFolders(false);
loadScanFolders();

// ── Models & containers panel ────────────────────────────────────────────────
function _moPrefs() {
  return { chat: localStorage.getItem('cc_chat_model') || '', ask: localStorage.getItem('cc_ask_model') || '' };
}

async function loadModelsOverview() {
  const box = document.getElementById('models-ov');
  if (!box) return;
  box.textContent = 'Loading…';
  try {
    const r = await fetch(apiUrl() + '/api/models/overview', { signal: AbortSignal.timeout(30000) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    _renderModelsOverview(box, await r.json());
  } catch (e) {
    box.innerHTML = `<div class="mo-row dim">Could not load: ${escapeHtml(e.message)} — is the backend running?</div>`;
  }
}

function _renderModelsOverview(box, d) {
  const e = escapeHtml, prefs = _moPrefs();
  const badge = (txt, cls, title) => `<span class="mo-badge ${cls || ''}" title="${e(title || '')}">${txt}</span>`;
  let h = '';

  // ── Docker ──
  h += '<div><div class="mo-sec">🐳 Docker — what the app is using</div>';
  const dk = d.docker || {};
  if (!dk.available) {
    h += `<div class="mo-row dim">Docker not available: ${e(dk.error || '')}</div>`;
  } else {
    const used = dk.containers.filter(c => c.in_use), idle = dk.containers.filter(c => !c.in_use);
    if (!used.length) h += '<div class="mo-row dim">No container is serving Elasticsearch or Ollama right now.</div>';
    for (const c of used) {
      let extra = '';
      if (c.serves.includes('elasticsearch') && c.index && c.index.chunks != null) {
        const ix = c.index;
        extra = `Index <b>${e(ix.name)}</b>: ${ix.chunks.toLocaleString()} chunks · ${e(ix.size_h)} · Elasticsearch ${e(ix.version)} · ` +
                `${ix.vector_dims}-dim vectors from <code>${e(ix.embedding_model)}</code>` +
                (c.volumes?.length ? `<br>Data lives in: ${c.volumes.filter(v => v.type === 'volume').map(v => '<code>' + e(v.name) + '</code>').join(', ') || '—'} <span class="mo-sub">(a Docker volume — see “Move the index to another machine”)</span>` : '');
      }
      if (c.serves.includes('ollama')) extra = c.loaded_models?.length ? 'Loaded now: ' + c.loaded_models.map(n => `<code>${e(n)}</code>`).join(', ') : 'No model loaded in memory right now.';
      h += `<div class="mo-row"><div class="mo-grow"><span class="mo-name">${e(c.name)}</span> ${badge('● in use', 'on')} ` +
           c.serves.map(s => badge(e(s), 'use', 'Serves ' + s + ' on this machine')).join(' ') +
           `<div class="mo-sub">${e(c.image)} · ${e(c.status)}${c.ports ? ' · ' + e(c.ports) : ''}</div>` +
           (extra ? `<div class="mo-sub" style="margin-top:2px">${extra}</div>` : '') + '</div></div>';
    }
    if (!dk.containers.some(c => c.serves.includes('ollama')))
      h += `<div class="mo-row"><div class="mo-grow"><span class="mo-name">Ollama</span> ${badge(d.ollama.reachable ? '● running' : 'not running', d.ollama.reachable ? 'on' : 'warn')}` +
           `<div class="mo-sub">${e(d.ollama.host)} · ${e(d.ollama.url)} — not in Docker</div></div></div>`;
    if (idle.length || dk.other_containers)
      h += `<details><summary class="mo-sub" style="cursor:pointer">${idle.length + (dk.other_containers || 0)} other container(s) not used by the app</summary>` +
           idle.map(c => `<div class="mo-row dim"><span class="mo-name">${e(c.name)}</span><span class="mo-sub">${e(c.image)} · ${e(c.status)}</span></div>`).join('') + '</details>';
  }
  h += '</div>';

  // ── Ollama models ──
  const o = d.ollama;
  h += `<div><div class="mo-sec">Ollama models — ${e(o.host)}${o.running_count ? ` · ${o.running_count} loaded in memory` : ''}</div>`;
  if (!o.reachable) h += '<div class="mo-row dim">Ollama is not running (start it from the status bar, or <code>ollama serve</code>).</div>';
  else if (!o.models.length) h += '<div class="mo-row dim">No models installed. Use “＋ Add model…” or “Pull &amp; use”.</div>';
  for (const m of o.models) {
    const bs = [];
    if (m.running) bs.push(badge('● loaded' + (m.vram_h ? ' · ' + e(m.vram_h) : ''), 'on', 'Loaded in memory right now'));
    for (const u of m.used_by || []) {
      if (u.kind === 'rag_chain') bs.push(badge('answers #' + u.rank, 'use', 'Order in which questions are answered: installed cloud models first, then local ones. #1 is tried first; if it fails the next one is used.'));
      if (u.kind === 'translation') bs.push(badge('translation', 'use', 'TRANSLATION_MODEL (env.py): translates documents'));
    }
    if (prefs.chat === m.name) bs.push(badge('★ chat', 'use', 'Selected in the Chat page'));
    if (prefs.ask === m.name) bs.push(badge('★ ask', 'use', 'Selected in the Ask panel'));
    const sel = prefs.chat === m.name && prefs.ask === m.name;
    h += `<div class="mo-row" data-model="${e(m.name)}"><div class="mo-grow"><span class="mo-name">${m.cloud ? '☁ ' : ''}${e(m.name)}</span> ` +
         `<span class="mo-sub">${m.cloud ? 'cloud (runs remotely)' : e(m.size_h) + (m.params ? ' · ' + e(m.params) : '') + (m.quant ? ' · ' + e(m.quant) : '')}</span> ${bs.join(' ')}</div>` +
         `<button class="secondary mo-btn" data-mo="use" ${sel ? 'disabled' : ''} title="Use this model in Chat and Ask">${sel ? 'in use' : 'Use'}</button>` +
         (m.running ? `<button class="secondary mo-btn" data-mo="unload" title="Free its memory (it reloads on next use)">⏏ Unload</button>` : '') +
         `<button class="secondary mo-btn" data-mo="delete" title="Delete from Ollama (frees disk space)">🗑</button></div>`;
  }
  h += '</div>';

  // ── Embedding / reranker ──
  h += '<div><div class="mo-sec">Search models (run inside the backend)</div>';
  for (const a of d.app_models) {
    const st = a.state === 'ready' || a.state === 'in use' ? badge('● ' + e(a.state), 'on') : badge(e(a.state), a.state === 'failed' ? 'warn' : '');
    h += `<div class="mo-row"><div class="mo-grow"><b>${e(a.role)}</b> <span class="mo-name">${e(a.name)}</span> ${st} ` +
         `${a.cached ? badge('cached · ' + e(a.size_h), '') : badge('not downloaded', 'warn')}` +
         `<div class="mo-sub">${e(a.detail)}${a.error ? ' · ' + e(a.error) : ''}</div></div></div>`;
  }
  h += '</div>';
  box.innerHTML = h;
}

document.getElementById('models-ov')?.addEventListener('click', async ev => {
  const b = ev.target.closest('[data-mo]');
  if (!b || b.disabled) return;
  const name = b.closest('[data-model]')?.dataset.model;
  const act = b.dataset.mo;
  if (act === 'use') { useOllamaModel(name); return; }
  if (act === 'delete' && !confirm(`Delete the model “${name}” from Ollama?\n\nLocal models free their disk space; cloud models are only unregistered. You can pull it again later.`)) return;
  try {
    const r = await fetch(apiUrl() + '/api/models/ollama/' + act, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
    const d = await r.json();
    if (!r.ok) { toolOut('models-out', 'Error: ' + (d.error || r.status)); return; }
    toolOut('models-out', act === 'delete' ? `Deleted ${name}.` : `Unloaded ${name} from memory.`);
    if (act === 'delete') {                                        // don't leave a dangling selection
      for (const k of ['cc_chat_model', 'cc_ask_model']) if (localStorage.getItem(k) === name) localStorage.removeItem(k);
    }
    loadModelsOverview();
    if (typeof checkOllama === 'function') checkOllama();
  } catch (e) { toolOut('models-out', 'Error: ' + e.message); }
});

function useOllamaModel(name) {
  localStorage.setItem('cc_chat_model', name);
  localStorage.setItem('cc_ask_model', name);
  const sel = document.getElementById('ask-model');
  if (sel && [...sel.options].some(o => o.value === name)) sel.value = name;
  const ext = window.LLM && LLM.isExternal();
  toolOut('models-out', `Now using ${name} in Chat and Ask.` +
          (ext ? ` (An external provider — ${LLM.label()} — is active; this applies when you switch back to Ollama.)` : ''));
  loadModelsOverview();
}

function openOllamaInstaller() {
  const c = document.getElementById('tc-ollama');
  c.classList.add('open');
  c.scrollIntoView({ behavior: 'smooth', block: 'center' });
  document.getElementById('tool-ollama-model')?.focus();
}

async function pullAndUse() {
  const name = getId('mo-pull-name');
  if (!name) { toolOut('models-out', 'Please enter a model name.'); return; }
  toolOut('models-out', `Pulling ${name} …`);
  try {
    const r = await fetch(apiUrl() + '/api/tools/ollama-pull', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: name }) });
    const d = await r.json();
    if (!d.task_id) { toolOut('models-out', 'Error: ' + JSON.stringify(d)); return; }
    for (let i = 0; i < 1800; i++) {                               // up to ~1 h
      await new Promise(res => setTimeout(res, 2000));
      const st = await (await fetch(apiUrl() + '/api/tools/status/' + d.task_id)).json();
      const last = (st.log || []).filter(Boolean).slice(-1)[0] || '';
      if (st.status === 'running' || st.status === 'starting') { toolOut('models-out', `Pulling ${name} … ${last}`); continue; }
      if (st.status === 'done') { toolOut('models-out', `Pulled ${name}.`); useOllamaModel(name); }
      else toolOut('models-out', `Pull failed: ${last}`);
      break;
    }
  } catch (e) { toolOut('models-out', 'Error: ' + e.message); }
}

// ── Export / Import index ────────────────────────────────────────────────────
// The backend runs the Docker steps (stop ES → tar in an alpine container → verify → restart);
// this UI only starts a job and polls its steps. The job id is kept in sessionStorage so a page
// reload re-attaches to a running job.
let _mgPoll = null;

function _mgBytes(n) { return n == null ? '' : n >= 1073741824 ? (n / 1073741824).toFixed(2) + ' GB' : n >= 1048576 ? (n / 1048576).toFixed(n >= 10485760 ? 0 : 1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'; }

async function mgInit() {
  const info = document.getElementById('mg-info');
  try {
    const r = await fetch(apiUrl() + '/api/migrate/info', { signal: AbortSignal.timeout(20000) });
    if (r.status === 403) throw new Error('Open the console from http://127.0.0.1:5000 (export / import is localhost-only).');
    const d = await r.json();
    const dir = document.getElementById('mg-export-dir');
    if (!dir.value) dir.value = localStorage.getItem('cc_mg_export_dir') || d.exports_dir;
    const dk = d.docker.ok
      ? `<span style="color:var(--green)">● Docker ${escapeHtml(d.docker.message)}</span>` +
        ` · ${d.compose ? 'docker compose found' : '<span style="color:var(--yellow)">docker compose not found (will use docker start)</span>'}` +
        ` · container <code>${escapeHtml(d.cfg.container)}</code> ${d.target.exists ? (d.target.running ? '(running)' : '(stopped)') : '(not created yet)'}` +
        ` · volume <code>${escapeHtml(d.target.volume)}</code> ${d.target.volume_exists ? '' : '(will be created)'}`
      : `<span style="color:var(--red)">✗ ${escapeHtml(d.docker.message)}</span>`;
    info.innerHTML = `${dk} · ${escapeHtml(d.platform)}` + (d.busy ? `<br><span style="color:var(--yellow)">⚠ ${escapeHtml(d.busy)}</span>` : '');
    mgLoadExports();
    const last = sessionStorage.getItem('cc_mg_job');
    if (last && !_mgPoll) mgPollJob(last, true);
  } catch (e) { info.innerHTML = `<span style="color:var(--red)">${escapeHtml(e.message)}</span>`; }
}

async function mgLoadExports() {
  const box = document.getElementById('mg-exports');
  const dir = document.getElementById('mg-export-dir').value.trim();
  try {
    const d = await (await fetch(apiUrl() + '/api/migrate/exports?dir=' + encodeURIComponent(dir))).json();
    const e = escapeHtml;
    box.innerHTML = !d.exports.length ? '<div class="mo-sub">No exports in this folder yet.</div>' :
      d.exports.map(x => `<div class="mo-row"><div class="mo-grow"><span class="mo-name">${e(x.name)}</span> ` +
        `<span class="mo-sub">${_mgBytes(x.size_bytes)} · ${e(x.modified.replace('T', ' '))}` +
        (x.docs != null ? ` · ${Number(x.docs).toLocaleString()} chunks · ES ${e(x.es_version || '?')} · ${x.vector_dims || '?'}-dim` : ' · no manifest') +
        (x.embedding_model ? ` · <code>${e(x.embedding_model)}</code>` : '') + `</span></div>` +
        `<button class="secondary mo-btn" data-path="${e(x.path)}" data-mg="use" title="Put this path in the import box">Use for import</button>` +
        `<button class="secondary mo-btn" data-path="${e(x.path)}" data-mg="copy" title="Copy the full path">⧉ Copy path</button></div>`).join('');
  } catch (_) { box.innerHTML = ''; }
}

document.getElementById('mg-exports')?.addEventListener('click', ev => {
  const b = ev.target.closest('[data-mg]');
  if (!b) return;
  if (b.dataset.mg === 'use') document.getElementById('mg-import-file').value = b.dataset.path;
  else navigator.clipboard?.writeText(b.dataset.path);
});

async function _mgStart(url, body) {
  const r = await fetch(apiUrl() + url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  sessionStorage.setItem('cc_mg_job', d.job_id);
  document.getElementById('mg-remap').innerHTML = '';
  mgPollJob(d.job_id);
}

async function mgStartExport() {
  const dir = document.getElementById('mg-export-dir').value.trim();
  if (!dir) { alert('Choose an export folder.'); return; }
  if (!confirm(`Create an export in\n${dir}\n\nElasticsearch will be stopped while the data is copied (a minute or more for large indexes) and restarted afterwards.`)) return;
  localStorage.setItem('cc_mg_export_dir', dir);
  try { await _mgStart('/api/migrate/export', { dir }); } catch (e) { document.getElementById('mg-job').innerHTML = `<div class="mo-row" style="color:var(--red)">${escapeHtml(e.message)}</div>`; }
}

async function mgStartImport() {
  const file = document.getElementById('mg-import-file').value.trim().replace(/^"|"$/g, '');
  if (!file) { alert('Enter the full path of the .tgz archive.'); return; }
  const backup = document.getElementById('mg-import-backup').checked;
  if (!confirm(`REPLACE all Elasticsearch data on this machine with:\n\n${file}\n\n` +
               (backup ? 'A safety backup of the current index is made first.' : '⚠ NO safety backup — the current index will be lost.') +
               `\n\nElasticsearch is stopped and restarted. Continue?`)) return;
  try { await _mgStart('/api/migrate/import', { archive: file, backup_current: backup }); }
  catch (e) { document.getElementById('mg-job').innerHTML = `<div class="mo-row" style="color:var(--red)">${escapeHtml(e.message)}</div>`; }
}

function mgPollJob(id, resumed) {
  clearInterval(_mgPoll);
  const busy = on => ['mg-export-btn', 'mg-import-btn'].forEach(i => { const b = document.getElementById(i); if (b) b.disabled = on; });
  busy(true);
  const tick = async () => {
    try {
      const r = await fetch(apiUrl() + '/api/migrate/jobs/' + id);
      if (r.status === 404) { clearInterval(_mgPoll); _mgPoll = null; busy(false); sessionStorage.removeItem('cc_mg_job'); if (!resumed) document.getElementById('mg-job').innerHTML = '<div class="mo-row dim">Job not found (backend restarted).</div>'; return; }
      const j = await r.json();
      mgRenderJob(j);
      if (j.status !== 'running') {
        clearInterval(_mgPoll); _mgPoll = null; busy(false);
        mgLoadExports();
        if (j.kind === 'import' && j.status === 'done') mgShowRemap(j.result.folders || []);
        if (typeof refreshStatus === 'function') refreshStatus();
        loadScanFolders();
      }
    } catch (_) { /* backend busy or restarting — keep polling */ }
  };
  tick();
  _mgPoll = setInterval(tick, 1000);
}

function mgRenderJob(j) {
  const e = escapeHtml, ic = { pending: '○', running: '⏳', ok: '✓', failed: '✗', skipped: '–' };
  const head = j.status === 'running' ? `⏳ ${j.kind === 'export' ? 'Exporting' : 'Importing'}… ${j.elapsed}s`
             : j.status === 'done' ? `<span style="color:var(--green)">✓ ${j.kind === 'export' ? 'Export' : 'Import'} finished in ${j.elapsed}s</span>`
             : `<span style="color:var(--red)">✗ ${j.kind === 'export' ? 'Export' : 'Import'} failed — ${e(j.error)}</span>`;
  let h = `<div class="mo-sec">${head}</div><div class="mg-steps">` +
    j.steps.map(s => `<div class="mg-step ${s.status}"><span class="ic">${ic[s.status]}</span><span class="nm">${e(s.name)}</span>` +
      `<span class="dt">${e(s.detail || '')}${s.secs != null ? ` <i>(${s.secs}s)</i>` : ''}</span></div>`).join('') + '</div>';
  if (j.kind === 'export' && j.status === 'done' && j.result.archive) {
    h += `<div class="mo-row" style="margin-top:8px;flex-direction:column;align-items:flex-start;">` +
      `<div><b>Archive ready:</b> <code style="user-select:all">${e(j.result.archive)}</code> ` +
      `<button class="secondary mo-btn" onclick="navigator.clipboard?.writeText(${e(JSON.stringify(j.result.archive))})">⧉ Copy path</button></div>` +
      `<div class="mo-sub">${_mgBytes(j.result.size_bytes)} · ${Number(j.result.docs || 0).toLocaleString()} chunks · sha256 ${e((j.result.sha256 || '').slice(0, 16))}…<br>` +
      `Copy this file <b>and the <code>.manifest.json</code> next to it</b> to the other machine, then use “Import” there.</div></div>`;
  }
  if (j.result && j.result.safety_backup) h += `<div class="mo-sub" style="margin-top:6px">Safety backup of the previous index: <code>${e(j.result.safety_backup)}</code></div>`;
  h += `<details style="margin-top:6px"><summary class="mo-sub" style="cursor:pointer">Log</summary><div class="mg-log">${e((j.log || []).join('\n'))}</div></details>`;
  document.getElementById('mg-job').innerHTML = h;
}

async function mgShowRemap(manifestFolders) {
  const box = document.getElementById('mg-remap');
  box.innerHTML = '<div class="mo-sub">Reading the folders stored in the imported index…</div>';
  try {
    const hints = (manifestFolders || []).map(f => f.root || f);
    const d = await (await fetch(apiUrl() + '/api/migrate/folders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ hints }) })).json();
    if (d.error) throw new Error(d.error);
    window._mgFolders = d.folders;
    const e = escapeHtml;
    box.innerHTML = `<div class="mo-sec">📁 Where were the files on the other machine?</div>` +
      `<div class="mo-sub" style="margin-bottom:6px">These are the folders the imported chunks point to. Type where each one lives on <b>this</b> machine ` +
      `(search works either way; the file viewer and re-scans need the real location). Only the start of the path is replaced.</div>` +
      d.folders.map((f, i) => `<div class="mg-remap-row" data-i="${i}">` +
        `<div><code style="word-break:break-all">${e(f.root)}</code><div class="mo-sub">${f.files.toLocaleString()} files · ${f.chunks.toLocaleString()} chunks</div></div>` +
        `<span title="${f.exists_here ? 'exists on this machine' : 'does not exist here'}">${f.exists_here ? '✓ here' : '→'}</span>` +
        `<input type="text" spellcheck="false" placeholder="New folder on this machine" value="${f.exists_here ? e(f.root) : ''}">` +
        `<button class="secondary mo-btn" data-mgmap="${i}">Apply</button></div>`).join('') +
      (d.folders.length ? '' : '<div class="mo-sub">No folders found (the index is empty).</div>') +
      `<div id="mg-remap-out" class="mo-sub" style="margin-top:6px"></div>`;
  } catch (e) { box.innerHTML = `<div class="mo-row" style="color:var(--red)">${escapeHtml(e.message)}</div>`; }
}

document.getElementById('mg-remap')?.addEventListener('click', async ev => {
  const b = ev.target.closest('[data-mgmap]');
  if (!b) return;
  const row = b.closest('.mg-remap-row'), f = (window._mgFolders || [])[+b.dataset.mgmap];
  const np = row.querySelector('input').value.trim(), out = document.getElementById('mg-remap-out');
  if (!f || !np) { out.textContent = 'Type the new folder first.'; return; }
  const post = body => fetch(apiUrl() + '/api/migrate/remap', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(async r => ({ ok: r.ok, d: await r.json() }));
  try {
    const q = await post({ old: f.root, new: np, dry_run: true });
    if (!q.ok) { out.innerHTML = `<span style="color:var(--red)">${escapeHtml(q.d.error)}</span>`; return; }
    if (q.d.unchanged) { out.textContent = 'Same folder — nothing to change.'; return; }
    if (!confirm(`Point ${q.d.chunks.toLocaleString()} chunks from\n${f.root}\nto\n${q.d.new}\n?`)) return;
    b.disabled = true;
    const r = await post({ old: f.root, new: np });
    if (!r.ok) { out.innerHTML = `<span style="color:var(--red)">${escapeHtml(r.d.error)}</span>`; b.disabled = false; return; }
    for (let i = 0; i < 4800; i++) {
      await new Promise(res => setTimeout(res, 1500));
      const st = await (await fetch(apiUrl() + '/api/tools/scan-paths/relocation/' + r.d.task_id)).json();
      if (st.status === 'running') { out.textContent = `Updating the index… ${st.elapsed}s`; continue; }
      out.innerHTML = st.status === 'done' ? `<span style="color:var(--green)">✓ ${st.updated.toLocaleString()} chunks now point to ${escapeHtml(np)}</span>`
                                           : `<span style="color:var(--red)">${escapeHtml(st.error)}</span>`;
      break;
    }
    loadScanFolders();
    row.querySelector('[data-mgmap]').disabled = false;
  } catch (e) { out.innerHTML = `<span style="color:var(--red)">${escapeHtml(e.message)}</span>`; b.disabled = false; }
});

// ── File Manager ─────────────────────────────────────────────────────────────
let _filesCache = [];  // cache last search results for re-render

async function searchIndexedFiles(overrideQuery) {
  const query = overrideQuery !== undefined ? overrideQuery : getId('tool-files-query');
  toolOut('tool-files-out', 'Searching files…');
  try {
    const r = await fetch(apiUrl() + '/api/tools/search-files', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ query, limit: 500 })
    });
    const d = await r.json();
    if (d.error) { toolOut('tool-files-out', 'Error: ' + d.error); return; }

    _filesCache = d.files || [];
    const styleEl = document.getElementById('tool-files-style');
    const winIcon = d.dominant_style === 'windows' ? '🪟' : '🐧';
    styleEl.textContent = `${winIcon} Paths: ${d.style_counts.windows} Windows, ${d.style_counts.posix} Linux/macOS — ${d.total} total`;

    _renderFileList(d.files, d.total, d.returned);
    toolOut('tool-files-out', `Found ${d.total} file(s)` + (query ? ` matching "${query}"` : '') + `. Showing ${d.returned}.`);
  } catch(e) {
    toolOut('tool-files-out', 'Error: ' + e.message);
  }
}

function _renderFileList(files, total, returned) {
  const el = document.getElementById('tool-files-list');
  el.style.display = 'block';
  if (!files.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:8px;">No files found.</div>';
    return;
  }
  let html = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap;">
    <label style="font-size:11px;display:flex;align-items:center;gap:4px;cursor:pointer;">
      <input type="checkbox" id="fm-select-all" onchange="fmSelectAll(this.checked)"> Select all
    </label>
    <button class="secondary" id="fm-del-btn" style="font-size:10px;padding:2px 8px;" onclick="fmDeleteSelected()" disabled>🗑 Delete selected</button>
    <span id="fm-sel-count" style="font-size:10px;color:var(--muted);"></span>
  </div>`;
  if (total > returned) {
    html += `<div style="font-size:11px;color:var(--muted);margin-bottom:6px;">Showing ${returned} of ${total} — refine your search to see more.</div>`;
  }
  for (const f of files) {
    const sIcon = f.style === 'windows' ? '🪟' : '🐧';
    const existsIcon = f.exists ? '✓' : '✗';
    const existsColor = f.exists ? 'var(--green,#4caf50)' : 'var(--red,#f44336)';
    const escapedAttr = escHtml(f.path);
    const escapedJs   = f.path.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const fExt = f.path.split('.').pop().toLowerCase();
    const fIcon = FILE_TYPE_ICONS[fExt] || '📎';
    html += `<div class="fm-row" style="display:flex;align-items:center;gap:6px;padding:5px 8px;background:var(--code-bg);border-radius:5px;font-size:11px;font-family:monospace;margin-bottom:3px;">
      <input type="checkbox" class="fm-chk" data-path="${escapedAttr}" onchange="fmChkChange()" style="cursor:pointer;flex-shrink:0;">
      <span style="flex-shrink:0;font-size:14px;" title="${fExt}">${fIcon}</span>
      <span title="${f.style}" style="flex-shrink:0;font-size:11px;">${sIcon}</span>
      <span style="color:${existsColor};flex-shrink:0;font-weight:bold;" title="${f.exists ? 'File exists on disk' : 'File NOT found on disk'}">${existsIcon}</span>
      <span style="flex:1;word-break:break-all;cursor:pointer;color:var(--accent);" onclick="openFileModal('${escapedJs}')" title="Click to open in viewer">${escapedAttr}</span>
      <button class="secondary" style="font-size:10px;padding:2px 7px;flex-shrink:0;" onclick="startFileEdit('${escapedJs}')" title="Edit path">✏️</button>
      <button class="secondary" style="font-size:10px;padding:2px 7px;flex-shrink:0;color:var(--red,#f44336);" onclick="deleteFile('${escapedJs}')" title="Delete from index">🗑️</button>
    </div>`;
  }
  el.innerHTML = html;
}

function fmSelectAll(checked) {
  document.querySelectorAll('.fm-chk').forEach(c => c.checked = checked);
  fmChkChange();
}

function fmChkChange() {
  const all = [...document.querySelectorAll('.fm-chk')];
  const sel = all.filter(c => c.checked);
  const btn = document.getElementById('fm-del-btn');
  const cnt = document.getElementById('fm-sel-count');
  const sa  = document.getElementById('fm-select-all');
  if (btn) {
    btn.disabled = sel.length === 0;
    btn.textContent = sel.length > 0 ? `🗑 Delete selected (${sel.length})` : '🗑 Delete selected';
  }
  if (cnt) cnt.textContent = sel.length > 0 ? `${sel.length} selected` : '';
  if (sa) { sa.checked = sel.length === all.length && all.length > 0; sa.indeterminate = sel.length > 0 && sel.length < all.length; }
}

async function fmDeleteSelected() {
  const paths = [...document.querySelectorAll('.fm-chk:checked')].map(c => c.dataset.path);
  if (!paths.length) return;
  if (!confirm(`Delete ${paths.length} file(s) from the index?\n\nThis removes all indexed chunks and cannot be undone.`)) return;
  toolOut('tool-files-out', `Deleting ${paths.length} file(s)…`);
  let totalDel = 0, errors = 0;
  for (const path of paths) {
    try {
      const r = await fetch(apiUrl() + '/api/tools/delete-file', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ path })
      });
      const d = await r.json();
      if (d.error) { errors++; continue; }
      totalDel += d.deleted || 0;
      _filesCache = _filesCache.filter(f => f.path !== path);
    } catch(e) { errors++; }
  }
  _renderFileList(_filesCache, _filesCache.length, _filesCache.length);
  const msg = `Deleted ${totalDel} chunk(s)` + (errors ? `, ${errors} error(s)` : '');
  toolOut('tool-files-out', msg);
  log(msg, errors ? 'warn' : 'ok');
}

async function deleteFile(path) {
  if (!confirm(`Delete all index entries for:\n${path}\n\nThis cannot be undone.`)) return;
  toolOut('tool-files-out', `Deleting "${path}"…`);
  try {
    const r = await fetch(apiUrl() + '/api/tools/delete-file', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ path })
    });
    const d = await r.json();
    if (d.error) { toolOut('tool-files-out', 'Error: ' + d.error); return; }
    toolOut('tool-files-out', `Deleted ${d.deleted} document(s) for: ${path}`);
    log(`File deleted from index: ${d.deleted} docs — ${path}`, 'ok');
    // Remove from cache and re-render
    _filesCache = _filesCache.filter(f => f.path !== path);
    _renderFileList(_filesCache, _filesCache.length, _filesCache.length);
  } catch(e) {
    toolOut('tool-files-out', 'Error: ' + e.message);
  }
}

function startFileEdit(path) {
  const editEl = document.getElementById('tool-files-edit');
  editEl.style.display = 'block';
  document.getElementById('tool-files-old-path').value = path;
  document.getElementById('tool-files-new-path').value = path;
  document.getElementById('tool-files-new-path').focus();

  // Auto-detect path style
  const sel = document.getElementById('tool-files-path-style');
  if (path.includes('\\') && path.length > 1 && path[1] === ':') {
    sel.value = 'windows';
  } else if (path.startsWith('/')) {
    sel.value = 'posix';
  } else {
    sel.value = 'auto';
  }
  editEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function cancelFileEdit() {
  document.getElementById('tool-files-edit').style.display = 'none';
}

function convertPathStyle() {
  const style = document.getElementById('tool-files-path-style').value;
  const inp = document.getElementById('tool-files-new-path');
  let p = inp.value;
  if (style === 'windows') {
    // Convert forward slashes to backslashes; add drive letter hint if missing
    p = p.replace(/\//g, '\\');
  } else if (style === 'posix') {
    // Convert backslashes to forward slashes; strip drive letter
    p = p.replace(/\\/g, '/');
    if (/^[A-Za-z]:/.test(p)) {
      p = p.substring(2);  // remove "C:" prefix
    }
  }
  inp.value = p;
}

async function saveFilePath() {
  const oldPath = document.getElementById('tool-files-old-path').value.trim();
  const newPath = document.getElementById('tool-files-new-path').value.trim();
  if (!oldPath || !newPath) { toolOut('tool-files-out', 'Both paths are required.'); return; }
  if (oldPath === newPath) { toolOut('tool-files-out', 'Paths are identical — nothing to change.'); return; }

  toolOut('tool-files-out', `Updating "${oldPath}" → "${newPath}"…`);
  try {
    const r = await fetch(apiUrl() + '/api/tools/update-file-path', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ old_path: oldPath, new_path: newPath })
    });
    const d = await r.json();
    if (d.error) { toolOut('tool-files-out', 'Error: ' + d.error); return; }
    toolOut('tool-files-out', `Done — ${d.updated} document(s) updated: ${oldPath} → ${newPath}`);
    log(`File path updated: ${d.updated} docs`, 'ok');
    cancelFileEdit();
    // Update in cache
    for (const f of _filesCache) {
      if (f.path === oldPath) { f.path = newPath; f.exists = true; }
    }
    _renderFileList(_filesCache, _filesCache.length, _filesCache.length);
  } catch(e) {
    toolOut('tool-files-out', 'Error: ' + e.message);
  }
}

async function runCatalogGen() {
  const path = getId('tool-catalog-path');
  if (!path) { toolOut('tool-catalog-out', 'Please enter a content directory path.'); return; }
  toolOut('tool-catalog-out', 'Generating catalog…');
  try {
    const r = await fetch(apiUrl() + '/api/tools/generate-catalog', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ path })
    });
    const d = await r.json();
    if (d.task_id) pollToolStatus(d.task_id, 'tool-catalog-out');
    else toolOut('tool-catalog-out', 'Error: ' + JSON.stringify(d));
  } catch(e) {
    toolOut('tool-catalog-out', 'Error: ' + e.message);
  }
}

async function runYTDownload() {
  const urls = getId('tool-yt-urls').split('\n').map(u => u.trim()).filter(Boolean);
  if (!urls.length) { toolOut('tool-yt-out', 'Please enter at least one URL.'); return; }
  const output = getId('tool-yt-output') || undefined;
  const video = document.getElementById('tool-yt-video').checked;
  const noVideo = document.getElementById('tool-yt-novideo').checked;
  toolOut('tool-yt-out', 'Starting download…');
  try {
    const r = await fetch(apiUrl() + '/api/tools/youtube-download', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ urls, output, video, no_video: noVideo })
    });
    const d = await r.json();
    if (d.task_id) pollToolStatus(d.task_id, 'tool-yt-out');
    else toolOut('tool-yt-out', 'Error: ' + JSON.stringify(d));
  } catch(e) {
    toolOut('tool-yt-out', 'Error: ' + e.message);
  }
}

let ollamaCatalogRequest = 0;
async function browseOllamaModels() {
  const requestId = ++ollamaCatalogRequest;
  const box = document.getElementById('tool-ollama-catalog');
  const input = document.getElementById('tool-ollama-model');
  box.hidden = false;
  box.textContent = 'Loading newest models…';
  try {
    const cloudOnly = document.getElementById('tool-ollama-cloud')?.checked ? '&cloud=1' : '';
    const response = await fetch(apiUrl() + '/api/tools/ollama-catalog?q=' + encodeURIComponent(input.value.trim()) + cloudOnly,
      { signal: AbortSignal.timeout(15000) });
    const data = await response.json();
    if (requestId !== ollamaCatalogRequest) return;
    if (!response.ok) throw new Error(data.error || 'Catalog unavailable.');
    box.replaceChildren();
    // compact: two-column grid of chips (name + ☁ for cloud), no per-row description
    box.style.cssText = 'max-height:150px;overflow:auto;margin-top:6px;display:grid;'
                      + 'grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:3px;';
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = '✕ Close';
    close.style.cssText = 'grid-column:1/-1;justify-self:end;padding:1px 8px;font-size:11px;';
    close.onclick = () => { box.hidden = true; input.focus(); };
    box.append(close);
    if (!data.models?.length) box.append(document.createTextNode(' No matching models.'));
    for (const model of (data.models || [])) {
      const button = document.createElement('button');
      button.type = 'button';
      button.style.cssText = 'text-align:left;padding:2px 7px;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
      button.textContent = (model.cloud ? '☁ ' : '') + model.name;
      const sizes = model.sizes || [];
      if (sizes.length) {                       // parameter counts, e.g. "8b · 70b · 405b"
        const s = document.createElement('span');
        s.style.cssText = 'opacity:.6;margin-left:6px;font-size:11px;';
        s.textContent = sizes.join(' · ');
        button.append(s);
      }
      // Rough download size at the default ~Q4 quantisation: ≈0.6 GB per billion parameters
      const gb = sizes.map(x => { const n = parseFloat(x.replace(/^e|^\d+x/, '')); const mult = /^\d+x/.test(x) ? parseInt(x) : 1;
                                  return x.endsWith('m') ? n / 1000 * 0.6 * mult : n * 0.6 * mult; });
      const approx = gb.length ? ' — approx. ' + gb.map(v => (v < 1 ? v.toFixed(2) : v.toFixed(v < 10 ? 1 : 0)) + ' GB').join(' / ') + ' (Q4, estimate)' : '';
      button.title = (model.cloud ? 'Cloud model (needs `ollama signin`, runs remotely)' : 'Local model') + approx + ' — click to select';
      button.onclick = () => { input.value = model.name; box.hidden = true; input.focus(); };
      box.append(button);
    }
  } catch (error) {
    if (requestId === ollamaCatalogRequest) box.textContent = error.message + ' Enter a model manually or retry Browse.';
  }
}

async function runOllamaPull() {
  const model = getId('tool-ollama-model');
  if (!model) { toolOut('tool-ollama-out', 'Please enter a model name.'); return; }
  toolOut('tool-ollama-out', 'Pulling model ' + model + '…');
  try {
    const r = await fetch(apiUrl() + '/api/tools/ollama-pull', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ model })
    });
    const d = await r.json();
    if (d.task_id) pollToolStatus(d.task_id, 'tool-ollama-out');
    else toolOut('tool-ollama-out', 'Error: ' + JSON.stringify(d));
  } catch(e) {
    toolOut('tool-ollama-out', 'Error: ' + e.message);
  }
}

async function runPdfCollect() {
  const source = getId('tool-pdf-source');
  const output = getId('tool-pdf-output');
  if (!source) { toolOut('tool-pdf-out', 'Please enter a source folder.'); return; }
  toolOut('tool-pdf-out', 'Collecting PDFs…');
  try {
    const r = await fetch(apiUrl() + '/api/tools/pdf-collector', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ source, output: output || undefined, join: document.getElementById('tool-pdf-join').checked })
    });
    const d = await r.json();
    if (d.task_id) pollToolStatus(d.task_id, 'tool-pdf-out');
    else toolOut('tool-pdf-out', 'Error: ' + JSON.stringify(d));
  } catch(e) {
    toolOut('tool-pdf-out', 'Error: ' + e.message);
  }
}

function runMdScript(tool) {
  const opts = {};
  if (tool === 'mdsplit') {
    opts.file = getId('tool-mdsplit-input');
    if (!opts.file) { toolOut('tool-mdsplit-out', 'Please enter an input file.'); return; }
    opts.max_level = getId('tool-mdsplit-level');
    const out = getId('tool-mdsplit-output');
    if (out) opts.output = out;
    if (document.getElementById('tool-mdsplit-toc').checked) opts.table_of_contents = true;
    if (document.getElementById('tool-mdsplit-force').checked) opts.force = true;
    _runGenericTool(tool, opts, 'tool-mdsplit-out');
  } else if (tool === 'mdImageDownloader') {
    opts.file = getId('tool-mdimg-path');
    if (!opts.file) { toolOut('tool-mdimages-out', 'Please enter a document path.'); return; }
    const outDir = getId('tool-mdimg-out-dir');
    if (outDir) opts.out = outDir;
    if (document.getElementById('tool-mdimg-keephtml').checked) opts.keep_html = true;
    if (document.getElementById('tool-mdimg-dryrun').checked) opts.dry_run = true;
    _runGenericTool(tool, opts, 'tool-mdimages-out');
  } else if (tool === 'mdformater') {
    opts.file = getId('tool-mdformat-path');
    if (!opts.file) { toolOut('tool-mdformat-out', 'Please enter a document path.'); return; }
    if (document.getElementById('tool-mdformat-auto').checked) opts.auto = true;
    if (document.getElementById('tool-mdformat-images').checked) opts.images_only = true;
    _runGenericTool(tool, opts, 'tool-mdformat-out');
  }
}

async function _runGenericTool(tool, opts, outId) {
  toolOut(outId, 'Running ' + tool + '…');
  try {
    const r = await fetch(apiUrl() + '/api/tools/run-script', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ tool, ...opts })
    });
    const d = await r.json();
    if (d.task_id) pollToolStatus(d.task_id, outId);
    else toolOut(outId, 'Error: ' + JSON.stringify(d));
  } catch(e) {
    toolOut(outId, 'Error: ' + e.message);
  }
}

function showFromHtmlTab(tab) {
  ['single','collection','merge'].forEach(t => {
    document.getElementById('fhtml-' + t).style.display = t === tab ? '' : 'none';
    const btn = document.getElementById('fhtml-tab-' + t);
    if (t === tab) btn.classList.remove('secondary');
    else btn.classList.add('secondary');
  });
}

async function runFromHtml(tool) {
  const opts = {};
  if (tool === 'mdfromhtml') {
    opts.input = getId('tool-fhtml-s-input');
    opts.output = getId('tool-fhtml-s-output');
    if (!opts.input) { toolOut('tool-mdfromhtml-out', 'Please enter an input HTML file.'); return; }
  } else if (tool === 'mdfromhtml-collection') {
    opts.input_dir = getId('tool-fhtml-c-input');
    opts.output_dir = getId('tool-fhtml-c-output');
    if (!opts.input_dir) { toolOut('tool-mdfromhtml-out', 'Please enter an input directory.'); return; }
  } else if (tool === 'mdfromhtml-merge') {
    opts.input_dir = getId('tool-fhtml-m-input');
    const output = getId('tool-fhtml-m-output');
    if (output) opts.output = output;
    opts.toc_depth = getId('tool-fhtml-m-toc');
    if (document.getElementById('tool-fhtml-m-norefs').checked) opts.no_refs = true;
    if (document.getElementById('tool-fhtml-m-noemph').checked) opts.no_convert_emphasis = true;
    if (document.getElementById('tool-fhtml-m-notables').checked) opts.no_format_tables = true;
    const imgDest = getId('tool-fhtml-m-imgdest');
    if (imgDest) opts.images_dest = imgDest;
    if (!opts.input_dir) { toolOut('tool-mdfromhtml-out', 'Please enter an input directory.'); return; }
  }
  toolOut('tool-mdfromhtml-out', 'Running ' + tool + '…');
  try {
    const r = await fetch(apiUrl() + '/api/tools/run-script', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ tool, ...opts })
    });
    const d = await r.json();
    if (d.task_id) pollToolStatus(d.task_id, 'tool-mdfromhtml-out');
    else toolOut('tool-mdfromhtml-out', 'Error: ' + JSON.stringify(d));
  } catch(e) {
    toolOut('tool-mdfromhtml-out', 'Error: ' + e.message);
  }
}

// mdtranslator uses the generic runner directly (called inline from onclick)
async function runScript(tool, outId, opts) {
  if (!opts.file) { toolOut(outId, 'Please enter a document path.'); return; }
  _runGenericTool(tool, opts, outId);
}

// (applyTheme, toggleTheme → shared in assets/catalog.js)
applyTheme(localStorage.getItem('cc_theme') || 'dark');

// ── Sidebar toggle ───────────────────────────────────────────────────────────
function toggleSidebar() {
  const main = document.querySelector('main');
  const collapsed = main.classList.toggle('sidebar-collapsed');
  localStorage.setItem('cc_sidebar_collapsed', collapsed ? '1' : '0');
}

// (pushHistory, showHistory, hideHistory, _getHistory, _saveHistory, _HISTORY_MAX → shared in assets/catalog.js)

// ── Copy query between search and ask ────────────────────────────────────────
function copyQuery(fromId, toId) {
  const val = document.getElementById(fromId).value;
  document.getElementById(toId).value = val;
  document.getElementById(toId).focus();
}

// ── Persist & restore all search options ────────────────────────────────────
function _updateFilterBadges() {
  const badges = document.getElementById('ext-filter-badges');
  if (!badges) return;
  const checked = [...document.querySelectorAll('#ext-filter-grid input:checked')];
  const total   = document.querySelectorAll('#ext-filter-grid input').length;
  if (!checked.length || checked.length === total) { badges.innerHTML = ''; return; }
  const shown = checked.slice(0, 8);
  const more  = checked.length > 8 ? ` <span style="opacity:.55;font-size:10px;">+${checked.length - 8}</span>` : '';
  badges.innerHTML = ' — ' + shown.map(c => `${FILE_TYPE_ICONS[c.value]||'📎'} ${c.value}`).join('  ') + more;
}

function saveSearchOptions() {
  localStorage.setItem('cc_search_lang',   document.getElementById('lang').value);
  localStorage.setItem('cc_search_limit',  document.getElementById('limit').value);
  localStorage.setItem('cc_search_mode',   SEARCH_MODE);
  const exts = [...document.querySelectorAll('#ext-filter-grid input:checked')].map(c => c.value);
  localStorage.setItem('cc_search_exts',   JSON.stringify(exts));
  _updateFilterBadges();
}
function saveAskOptions() {
  localStorage.setItem('cc_ask_lang',  document.getElementById('ask-lang').value);
  localStorage.setItem('cc_ask_k',     document.getElementById('ask-k').value);
  if (!(window.LLM && LLM.isExternal()))   // external model is persisted by LLM.setModel()
    localStorage.setItem('cc_ask_model', document.getElementById('ask-model').value);
}
function restoreSearchOptions() {
  const lang  = localStorage.getItem('cc_search_lang');
  const limit = localStorage.getItem('cc_search_limit');
  const mode  = localStorage.getItem('cc_search_mode');
  const exts  = JSON.parse(localStorage.getItem('cc_search_exts') || 'null');
  if (lang  !== null) document.getElementById('lang').value  = lang;
  if (limit !== null) document.getElementById('limit').value = limit;
  if (mode) {
    const tab = document.querySelector(`#mode-tabs .tab[data-mode="${mode}"]`);
    if (tab) setMode(tab);
  }
  if (exts && exts.length) {
    // Only restore if user had a selection (don't change "none checked" default)
    document.querySelectorAll('#ext-filter-grid input').forEach(cb => {
      cb.checked = exts.includes(cb.value);
    });
  }
}
function restoreAskOptions() {
  const lang  = localStorage.getItem('cc_ask_lang');
  const k     = localStorage.getItem('cc_ask_k');
  // Model restored after Ollama models load
  if (lang !== null) document.getElementById('ask-lang').value = lang;
  if (k    !== null) document.getElementById('ask-k').value    = k;
}

(async () => {
  log('Console ready. Checking connections…', 'info');

  // Restore sidebar state
  if (localStorage.getItem('cc_sidebar_collapsed') === '1')
    document.querySelector('main').classList.add('sidebar-collapsed');

  // Restore queries
  const saved = localStorage.getItem('cc_last_query');
  if (saved) document.getElementById('q').value = saved;
  const savedAsk = localStorage.getItem('cc_last_ask');
  if (savedAsk) document.getElementById('ask-q').value = savedAsk;

  // Restore all other search options
  restoreSearchOptions();
  restoreAskOptions();
  _updateFilterBadges();

  // Attach change listeners to save options on change
  document.getElementById('lang').addEventListener('change', saveSearchOptions);
  document.getElementById('limit').addEventListener('change', saveSearchOptions);
  document.getElementById('ext-filter-grid').addEventListener('change', saveSearchOptions);
  document.getElementById('ask-lang').addEventListener('change', saveAskOptions);
  document.getElementById('ask-k').addEventListener('change', saveAskOptions);
  document.getElementById('ask-model').addEventListener('change', saveAskOptions);

  // Reconnect to any active indexing tasks running before page reload
  _reconnectActiveTasks();

  await refreshStatus();
})();


// ── Folder picker (Directory path → Browse…) ─────────────────────────────────
// Browsers never expose absolute paths, so the folders are listed by the backend (/api/fs/list).
let _fpickTarget = null, _fpickCur = '', _fpickParent = '';

function openFolderPicker(inputId) {
  _fpickTarget = inputId;
  document.getElementById('fpick-overlay').style.display = 'flex';
  fpickLoad((document.getElementById(inputId)?.value || '').trim());
}
function closeFolderPicker() { document.getElementById('fpick-overlay').style.display = 'none'; }
function fpickUp() { if (_fpickParent) fpickLoad(_fpickParent); }
function fpickChoose() {
  const el = document.getElementById(_fpickTarget);
  if (el && _fpickCur) { el.value = _fpickCur; el.dispatchEvent(new Event('input', { bubbles: true })); }
  closeFolderPicker();
}
async function fpickLoad(path) {
  const err = document.getElementById('fpick-err'), list = document.getElementById('fpick-list');
  err.textContent = '';
  try {
    const r = await fetch(`${apiUrl()}/api/fs/list?path=${encodeURIComponent(path || '')}`);
    let d; try { d = await r.json(); } catch (_) { throw new Error(`HTTP ${r.status}: backend not reachable at ${apiUrl()}`); }
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    _fpickCur = d.path; _fpickParent = d.parent;
    document.getElementById('fpick-path').value = d.path;
    document.getElementById('fpick-up').disabled = !d.parent;
    const btn = (label, p) => `<button type="button" class="secondary" style="padding:2px 8px;font-size:12px;" data-p="${escHtml(p).replace(/"/g,'&quot;')}">${escHtml(label)}</button>`;
    document.getElementById('fpick-roots').innerHTML = d.roots.map(x => btn(x.name, x.path)).join('');
    list.innerHTML = d.dirs.length
      ? d.dirs.map(x => `<div class="fpick-row" style="padding:5px 4px;cursor:pointer;border-bottom:1px solid var(--border);" data-p="${escHtml(x.path).replace(/"/g,'&quot;')}">📁 ${escHtml(x.name)}</div>`).join('')
      : '<div style="color:var(--muted);padding:8px 4px;">No sub-folders</div>';
    for (const el of document.querySelectorAll('#fpick-roots [data-p], #fpick-list [data-p]'))
      el.onclick = () => fpickLoad(el.dataset.p);
  } catch (e) { err.textContent = e.message; }
}
