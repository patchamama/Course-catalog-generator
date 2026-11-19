'use strict';
/*
 * llm_config.js — shared "LLM provider" settings (Ollama / OpenAI / Anthropic / OpenAI-compatible).
 *
 * - Settings dialog: provider, base URL, API key, model, "Test connection", "List models".
 * - Everything is persisted in localStorage (key `cc_llm_cfg`), so it survives reloads.
 * - Calls go through the Flask backend (/api/llm/test, /api/llm/models) to avoid CORS problems.
 * - The API key is sent only to YOUR backend, in the request body, and forwarded to the provider.
 *   It is stored in this browser's localStorage in clear text — don't use on shared machines.
 *
 * Public API (window.LLM):
 *   LLM.open()                 open the settings dialog
 *   LLM.isExternal()           true when a non-Ollama provider is active
 *   LLM.payload()              {provider, base_url, api_key, model} for /api/ask, or null for Ollama
 *   LLM.label()                short provider name for status badges
 *   LLM.models()               cached model list of the active external provider
 *   LLM.model()  / LLM.setModel(m)
 *   LLM.fillSelect(selectEl)   fill a <select> with the cached models; returns true if external
 *   window event 'llm-config-changed' fires whenever the active provider/model/credentials change.
 */
(function () {
  const KEY = 'cc_llm_cfg';
  const PROVIDERS = {
    ollama:    { label: 'Ollama (local)',                  short: 'Ollama',    base: '',                            keyReq: false },
    openai:    { label: 'OpenAI',                          short: 'OpenAI',    base: 'https://api.openai.com/v1',   keyReq: true  },
    anthropic: { label: 'Anthropic (Claude)',              short: 'Anthropic', base: 'https://api.anthropic.com',   keyReq: true  },
    custom:    { label: 'OpenAI-compatible (custom URL)',  short: 'Custom',    base: '',                            keyReq: false },
  };
  const EXAMPLES = 'OpenRouter https://openrouter.ai/api/v1 · Groq https://api.groq.com/openai/v1 · ' +
                   'Together https://api.together.xyz/v1 · Mistral https://api.mistral.ai/v1 · ' +
                   'LM Studio http://127.0.0.1:1234/v1 · vLLM http://127.0.0.1:8000/v1';

  // ── Persistence ────────────────────────────────────────────────────────────
  function blank() {
    const profiles = {};
    ['openai', 'anthropic', 'custom'].forEach(p => { profiles[p] = { base_url: PROVIDERS[p].base, api_key: '', model: '', models: [] }; });
    return { active: 'ollama', profiles };
  }
  function load() {
    const d = blank();
    try {
      const s = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (s && PROVIDERS[s.active]) d.active = s.active;
      if (s && s.profiles) for (const p in d.profiles) Object.assign(d.profiles[p], s.profiles[p] || {});
    } catch (_) { /* corrupted or blocked storage → defaults */ }
    return d;
  }
  let state = load();
  function persist(notify) {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (_) { /* storage full/blocked */ }
    if (notify) window.dispatchEvent(new CustomEvent('llm-config-changed'));
  }
  const prof = () => state.profiles[state.active] || null;

  // ── Public getters ─────────────────────────────────────────────────────────
  const isExternal = () => state.active !== 'ollama';
  function payload() {
    if (!isExternal()) return null;
    const p = prof();
    return { provider: state.active, base_url: p.base_url, api_key: p.api_key, model: p.model };
  }
  const label  = () => PROVIDERS[state.active].short;
  const models = () => (isExternal() ? prof().models.slice() : []);
  const model  = () => (isExternal() ? prof().model : '');
  function setModel(m) { if (isExternal()) { prof().model = m || ''; persist(false); } }
  function fillSelect(sel) {
    if (!isExternal() || !sel) return false;
    const p = prof();
    const list = p.models.slice();
    if (p.model && !list.includes(p.model)) list.unshift(p.model);
    sel.innerHTML = '';
    if (!list.length) sel.innerHTML = '<option value="">— click ⚙ LLM → List models —</option>';
    list.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; sel.appendChild(o); });
    if (p.model) sel.value = p.model;
    else if (list.length) { sel.value = list[0]; }
    return true;
  }

  // ── Backend calls ──────────────────────────────────────────────────────────
  function api() {
    try { if (typeof apiUrl === 'function') return apiUrl(); } catch (_) { /* fall through */ }
    return 'http://127.0.0.1:5000';
  }
  async function post(path, body) {
    const r = await fetch(api() + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(60000),
    });
    if (!r.ok) throw new Error(`Backend HTTP ${r.status}`);
    return r.json();
  }

  // ── Dialog ─────────────────────────────────────────────────────────────────
  let dlg = null;
  const $ = id => dlg.querySelector('#' + id);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function injectStyle() {
    if (document.getElementById('llm-style')) return;
    const st = document.createElement('style');
    st.id = 'llm-style';
    st.textContent = `
      .llm-ov{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px}
      .llm-box{background:var(--surface,#16213e);color:var(--text,#e0e0e0);border:1px solid var(--border,#2a2d3a);border-radius:10px;
               width:min(560px,100%);max-height:92vh;overflow:auto;padding:18px 20px;font:13px/1.45 system-ui,sans-serif;box-shadow:0 12px 40px rgba(0,0,0,.5)}
      .llm-box h3{margin:0 0 4px;font-size:16px}
      .llm-box .llm-sub{color:var(--muted,#8888aa);font-size:11.5px;margin-bottom:12px}
      .llm-box label{display:block;margin:10px 0 3px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--muted,#8888aa)}
      .llm-box input,.llm-box select{width:100%;box-sizing:border-box;padding:7px 9px;border-radius:6px;border:1px solid var(--border,#2a2d3a);
               background:var(--bg,#0d0d1a);color:var(--text,#e0e0e0);font:inherit}
      .llm-row{display:flex;gap:6px;align-items:center}
      .llm-row>input{flex:1}
      .llm-btn{padding:7px 12px;border-radius:6px;border:1px solid var(--border,#2a2d3a);background:var(--bg,#0d0d1a);color:var(--text,#e0e0e0);cursor:pointer;font:inherit;white-space:nowrap}
      .llm-btn:hover{border-color:var(--accent,#2ecc71)}
      .llm-btn.primary{background:var(--accent,#2ecc71);color:#07130c;border-color:transparent;font-weight:600}
      .llm-btn:disabled{opacity:.55;cursor:wait}
      .llm-msg{margin-top:12px;padding:8px 10px;border-radius:6px;font-size:12px;display:none;white-space:pre-wrap;word-break:break-word}
      .llm-msg.ok{display:block;background:rgba(62,207,142,.12);border:1px solid var(--green,#3ecf8e)}
      .llm-msg.err{display:block;background:rgba(231,76,60,.12);border:1px solid var(--red,#e74c3c)}
      .llm-msg.info{display:block;background:rgba(136,136,170,.12);border:1px solid var(--border,#2a2d3a)}
      .llm-models{margin-top:8px;max-height:160px;overflow:auto;border:1px solid var(--border,#2a2d3a);border-radius:6px;display:none}
      .llm-models div{padding:5px 9px;cursor:pointer;font-family:ui-monospace,Consolas,monospace;font-size:12px}
      .llm-models div:hover{background:rgba(136,136,170,.18)}
      .llm-models div.sel{background:rgba(46,204,113,.18);font-weight:600}
      .llm-hint{font-size:11px;color:var(--muted,#8888aa);margin-top:4px}
      .llm-foot{display:flex;justify-content:space-between;gap:8px;margin-top:16px;align-items:center}`;
    document.head.appendChild(st);
  }

  function build() {
    injectStyle();
    dlg = document.createElement('div');
    dlg.className = 'llm-ov';
    dlg.innerHTML = `
      <div class="llm-box" role="dialog" aria-modal="true" aria-label="LLM provider settings">
        <h3>⚙ LLM provider</h3>
        <div class="llm-sub">Choose where answers are generated. Settings are saved in this browser (localStorage).</div>

        <label for="llm-provider">Provider</label>
        <select id="llm-provider">
          ${Object.keys(PROVIDERS).map(k => `<option value="${k}">${esc(PROVIDERS[k].label)}</option>`).join('')}
        </select>

        <div id="llm-ext">
          <label for="llm-base">Base URL</label>
          <input id="llm-base" type="url" autocomplete="off" spellcheck="false" placeholder="https://…/v1">
          <div class="llm-hint" id="llm-base-hint"></div>

          <label for="llm-key">API key</label>
          <div class="llm-row">
            <input id="llm-key" type="password" autocomplete="off" spellcheck="false" placeholder="sk-…">
            <button class="llm-btn" id="llm-eye" type="button" title="Show / hide key">👁</button>
          </div>
          <div class="llm-hint">⚠ Stored unencrypted in this browser's localStorage and sent to your local backend with each request.</div>

          <label for="llm-model">Model</label>
          <div class="llm-row">
            <input id="llm-model" type="text" autocomplete="off" spellcheck="false" placeholder="e.g. gpt-4o-mini / claude-sonnet-… (type or pick from list)">
          </div>
          <div class="llm-row" style="margin-top:8px">
            <button class="llm-btn" id="llm-list" type="button">📋 List models</button>
            <button class="llm-btn" id="llm-test" type="button">🔌 Test connection</button>
          </div>
          <div class="llm-models" id="llm-models"></div>
        </div>

        <div class="llm-msg" id="llm-msg"></div>

        <div class="llm-foot">
          <button class="llm-btn" id="llm-clear" type="button" title="Remove the saved API key for this provider">🗑 Clear key</button>
          <button class="llm-btn primary" id="llm-done" type="button">Done</button>
        </div>
      </div>`;
    document.body.appendChild(dlg);

    dlg.addEventListener('mousedown', e => { if (e.target === dlg) close(); });
    document.addEventListener('keydown', escClose);
    $('llm-provider').addEventListener('change', () => { state.active = $('llm-provider').value; persist(true); render(); });
    ['llm-base', 'llm-key', 'llm-model'].forEach(id => $(id).addEventListener('input', onEdit));
    $('llm-eye').addEventListener('click', () => { const k = $('llm-key'); k.type = k.type === 'password' ? 'text' : 'password'; });
    $('llm-list').addEventListener('click', doList);
    $('llm-test').addEventListener('click', doTest);
    $('llm-clear').addEventListener('click', () => { if (prof()) { prof().api_key = ''; persist(true); render(); msg('API key removed.', 'info'); } });
    $('llm-done').addEventListener('click', close);
  }
  const escClose = e => { if (e.key === 'Escape' && dlg) close(); };

  function onEdit() {
    const p = prof(); if (!p) return;
    p.base_url = $('llm-base').value.trim();
    p.api_key  = $('llm-key').value.trim();
    p.model    = $('llm-model').value.trim();
    persist(true);
    highlightModel();
  }

  function msg(text, kind) {
    const m = $('llm-msg');
    m.className = 'llm-msg ' + (kind || 'info');
    m.textContent = text;
  }

  function render() {
    $('llm-provider').value = state.active;
    const ext = isExternal();
    $('llm-ext').style.display = ext ? '' : 'none';
    $('llm-msg').className = 'llm-msg';
    if (!ext) {
      msg('Using local Ollama (configured on the backend). Pick a model in the page header.', 'info');
      $('llm-models').style.display = 'none';
      return;
    }
    const p = prof();
    $('llm-base').value  = p.base_url;
    $('llm-key').value   = p.api_key;
    $('llm-model').value = p.model;
    $('llm-base-hint').textContent = state.active === 'custom' ? EXAMPLES
      : 'Default: ' + PROVIDERS[state.active].base + ' — change only for proxies / Azure / gateways.';
    $('llm-key').placeholder = PROVIDERS[state.active].keyReq ? 'required' : 'optional (local servers usually need none)';
    renderModels();
  }

  function renderModels() {
    const box = $('llm-models'), p = prof();
    if (!p || !p.models.length) { box.style.display = 'none'; return; }
    box.style.display = 'block';
    box.innerHTML = p.models.map(m => `<div data-m="${esc(m)}">${esc(m)}</div>`).join('');
    box.onclick = e => {
      const d = e.target.closest('[data-m]'); if (!d) return;
      $('llm-model').value = d.dataset.m; onEdit();
    };
    highlightModel();
  }
  function highlightModel() {
    const cur = prof() && prof().model;
    $('llm-models').querySelectorAll('[data-m]').forEach(d => d.classList.toggle('sel', d.dataset.m === cur));
  }

  function creds() {
    const p = prof();
    if (PROVIDERS[state.active].keyReq && !p.api_key) { msg('Enter an API key first.', 'err'); return null; }
    if (!p.base_url) { msg('Enter the Base URL first.', 'err'); return null; }
    return { provider: state.active, base_url: p.base_url, api_key: p.api_key, model: p.model };
  }
  async function busy(btn, fn) {
    const old = btn.textContent; btn.disabled = true; btn.textContent = '…';
    try { await fn(); } catch (e) { msg('Backend not reachable: ' + e.message + '\nIs the Flask server running?', 'err'); }
    finally { btn.disabled = false; btn.textContent = old; }
  }

  function doList() {
    const c = creds(); if (!c) return;
    busy($('llm-list'), async () => {
      msg('Fetching models…', 'info');
      const d = await post('/api/llm/models', c);
      if (!d.ok) { msg(d.error || 'Failed to list models.', 'err'); return; }
      prof().models = d.models; persist(true); renderModels();
      msg(`${d.models.length} model(s) available — click one to select it.`, 'ok');
    });
  }

  function doTest() {
    const c = creds(); if (!c) return;
    busy($('llm-test'), async () => {
      msg('Testing…', 'info');
      const d = await post('/api/llm/test', c);
      if (Array.isArray(d.models) && d.models.length) { prof().models = d.models; persist(true); renderModels(); }
      if (!d.ok) { msg('✗ ' + (d.error || 'Connection failed.'), 'err'); return; }
      let t = `✓ Connected to ${PROVIDERS[state.active].short} in ${d.ms} ms — ${d.model_count} model(s).`;
      if (d.reply !== undefined) t += `\n✓ Model "${c.model}" replied: "${d.reply}"`;
      else t += '\nSelect a model and test again to verify a real completion.';
      msg(t, 'ok');
    });
  }

  function open() {
    state = load();
    if (dlg) dlg.remove();
    build();
    render();
  }
  function close() {
    document.removeEventListener('keydown', escClose);
    if (dlg) { dlg.remove(); dlg = null; }
  }

  window.LLM = { open, close, isExternal, payload, label, models, model, setModel, fillSelect, PROVIDERS };
  // Keep other tabs/pages in sync
  window.addEventListener('storage', e => { if (e.key === KEY) { state = load(); window.dispatchEvent(new CustomEvent('llm-config-changed')); } });
})();
