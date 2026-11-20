'use strict';

// ── Config ─────────────────────────────────────────────────────────────────
// (BASE_URL removed — use apiUrl() from assets/catalog.js)
const DEFAULT_K   = 5;

// ── State ──────────────────────────────────────────────────────────────────
let conversationHistory   = [];  // [{role, content, sources}]
let currentAbortController = null;
let isStreaming            = false;

// ── DOM refs ───────────────────────────────────────────────────────────────
const chatContainer  = document.getElementById('chat-container');
const emptyState     = document.getElementById('empty-state');
const userInput      = document.getElementById('user-input');
const sendBtn        = document.getElementById('send-btn');
const cancelBtn      = document.getElementById('cancel-btn');
const statusBar      = document.getElementById('status-bar');
const statusText     = document.getElementById('status-text');
const backendNotice  = document.getElementById('backend-notice');
const ollamaDot      = document.getElementById('ollama-dot');
const ollamaLabel    = document.getElementById('ollama-label');
const modelSelect    = document.getElementById('model-select');
const modeSelect     = document.getElementById('mode-select');
const kSelect        = document.getElementById('k-select');
const langSelect     = document.getElementById('lang-select');
const ctxToggle      = document.getElementById('ctx-toggle');
const newChatBtn     = document.getElementById('new-chat-btn');

// ── Utility ────────────────────────────────────────────────────────────────
function setStatus(text, loading = false) {
  statusText.textContent = text;
  statusBar.classList.toggle('loading', loading);
}

function setOllamaStatus(online) {
  ollamaDot.className = 'dot ' + (online ? 'green' : 'red');
  ollamaLabel.textContent = online ? 'Ollama' : 'Ollama offline';
}

// External provider (OpenAI / Anthropic / custom): models come from localStorage settings
function setExternalStatus() {
  const p = LLM.payload();
  const ready = !!(p && p.model && (p.api_key || p.provider === 'custom'));
  ollamaDot.className = 'dot ' + (ready ? 'green' : 'red');
  ollamaLabel.textContent = ready ? LLM.label() : LLM.label() + ' — not configured';
}

function now() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ── File viewer modal ───────────────────────────────────────────────────────
// (file modal functions → shared in assets/catalog.js)

function autoResize() {
  userInput.style.height = 'auto';
  userInput.style.height = Math.min(userInput.scrollHeight, 180) + 'px';
}

function scrollToBottom(smooth = true) {
  chatContainer.scrollTo({ top: chatContainer.scrollHeight, behavior: smooth ? 'smooth' : 'instant' });
}

// ── Markdown renderer (minimal, safe) ──────────────────────────────────────
function renderMarkdown(raw) {
  if (!raw) return '';

  // Use marked.js for full markdown (tables, code blocks, lists, etc.)
  if (typeof marked !== 'undefined') {
    marked.setOptions({
      breaks: true,
      gfm: true,
      highlight: function(code, lang) {
        if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
          return hljs.highlight(code, { language: lang }).value;
        }
        return typeof hljs !== 'undefined' ? hljs.highlightAuto(code).value : code;
      },
    });
    let html = marked.parse(raw);
    // Post-process: citation references [1] → styled spans
    html = html.replace(/\[(\d+)\]/g, '<span class="cite-ref">[$1]</span>');
    return html;
  }

  // Fallback if marked.js not loaded (keep original implementation)
  const codeBlocks = [];
  let text = raw.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push(`<pre><code>${escapeHtml(code.trim())}</code></pre>`);
    return `\x00CB${idx}\x00`;
  });
  const parts = text.split(/\x00CB(\d+)\x00/); // eslint-disable-line no-control-regex
  text = parts.map((p, i) => i % 2 === 1 ? `\x00CB${p}\x00` : escapeHtml(p)).join('');
  text = text.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*([^*\n]+?)\*/g, '<em>$1</em>');
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  text = text.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
  text = text.replace(/^##\s+(.+)$/gm, '<h3>$1</h3>');
  text = text.replace(/\x00CB(\d+)\x00/g, (_, i) => codeBlocks[parseInt(i, 10)]); // eslint-disable-line no-control-regex
  const blocks = text.split(/\n{2,}/);
  text = blocks.map(b => {
    b = b.trim();
    if (!b || /^<(pre|ul|ol|h[1-6]|blockquote|hr)/.test(b)) return b;
    return `<p>${b.replace(/\n/g, ' ')}</p>`;
  }).filter(Boolean).join('\n');
  return text;
}

// ── Source chips ───────────────────────────────────────────────────────────
// (FILE_TYPE_ICONS, getFileIcon → shared in assets/catalog.js)

function createSourcesSection(sources) {
  const section = document.createElement('div');
  section.className = 'sources-section';

  if (!sources || sources.length === 0) {
    section.style.display = 'none';
    return section;
  }

  const toggle = document.createElement('button');
  toggle.className = 'sources-toggle';
  toggle.innerHTML = `<span class="chevron">&#9658;</span> ${sources.length} source${sources.length !== 1 ? 's' : ''}`;

  const list = document.createElement('div');
  list.className = 'sources-list';

  sources.forEach((src, i) => {
    const file  = src.filename || src.file || src.source || '—';
    const chunk = src.chunk_index ?? (src._source && src._source.chunk_index);
    const icon  = getFileIcon(file);

    const chip = document.createElement('div');
    chip.className = 'source-chip';
    chip.title = file + ' — click to view';
    chip.style.cursor = 'pointer';
    chip.innerHTML =
      `<span style="font-size:14px;flex-shrink:0;">${icon}</span>` +
      `<span class="src-num">[${i + 1}]</span>` +
      `<span class="src-file">${escapeHtml(file)}</span>` +
      (chunk != null ? `<span class="src-chunk">#${chunk}</span>` : '');
    chip.addEventListener('click', () => openFileModal(src.path || src.rel_path || file, src.srt_time ?? null, src.content || ''));

    list.appendChild(chip);
  });

  toggle.addEventListener('click', () => {
    const open = list.classList.toggle('open');
    toggle.classList.toggle('open', open);
    toggle.querySelector('.chevron').innerHTML = open ? '&#9660;' : '&#9658;';
  });

  section.appendChild(toggle);
  section.appendChild(list);
  return section;
}

// ── Message creation ───────────────────────────────────────────────────────
function createMessageEl(role, content = '', sources = []) {
  const wrapper = document.createElement('div');
  wrapper.className = `message ${role}`;

  // Meta row
  const meta = document.createElement('div');
  meta.className = 'msg-meta';

  const roleLabel = document.createElement('span');
  roleLabel.className = 'role-label';
  roleLabel.textContent = role === 'user' ? 'You' : 'Assistant';

  const timeEl = document.createElement('span');
  timeEl.className = 'msg-time';
  timeEl.textContent = now();

  meta.appendChild(roleLabel);
  meta.appendChild(timeEl);

  // Bubble
  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  if (role === 'user') {
    bubble.textContent = content;
    bubble.title = 'Click to copy to input';
    bubble.addEventListener('click', () => {
      userInput.value = content;
      autoResize();
      userInput.focus();
    });
  } else {
    if (content) bubble.innerHTML = renderMarkdown(content);
  }

  wrapper.appendChild(meta);
  wrapper.appendChild(bubble);

  // Sources (assistant only)
  if (role === 'assistant') {
    wrapper.appendChild(createSourcesSection(sources));
  }

  return { wrapper, bubble, meta };
}

function addModelBadge(metaEl, model) {
  const prev = metaEl.querySelector('.model-badge');
  if (prev) prev.remove();
  if (!model) return;
  const badge = document.createElement('span');
  badge.className = 'model-badge';
  // Trim ":latest" or long tags for display brevity
  badge.textContent = model.replace(/:latest$/, '').split('/').pop();
  badge.title = model;
  // Insert before time element
  const time = metaEl.querySelector('.msg-time');
  metaEl.insertBefore(badge, time);
}

// catalog.js history handler calls doAsk() for non-search inputs
function doAsk() { sendMessage(); }

// ── Send message ───────────────────────────────────────────────────────────
async function sendMessage() {
  const question = userInput.value.trim();
  if (!question || isStreaming) return;
  localStorage.setItem('cc_last_query', question);
  _pushChatHistory(question);

  // Hide empty state
  emptyState.style.display = 'none';

  // Render user bubble
  const { wrapper: userWrapper } = createMessageEl('user', question);
  chatContainer.appendChild(userWrapper);
  scrollToBottom();

  // Add to history
  conversationHistory.push({ role: 'user', content: question });

  // Clear input
  userInput.value = '';
  autoResize();

  // Create assistant bubble (empty, will fill via streaming)
  const { wrapper: botWrapper, bubble: botBubble, meta: botMeta } = createMessageEl('assistant');
  chatContainer.appendChild(botWrapper);
  scrollToBottom(false);

  // Blinking cursor
  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  botBubble.appendChild(cursor);

  // UI: streaming mode
  isStreaming = true;
  sendBtn.disabled = true;
  cancelBtn.classList.add('visible');
  setStatus('Connecting…', true);

  currentAbortController = new AbortController();

  let fullText  = '';
  let sources   = [];
  let srcSection = botWrapper.querySelector('.sources-section');

  // History for context continuity (all turns except the current question).
  // Truncate content to 2000 chars to keep POST body size manageable.
  const history = ctxToggle.checked
    ? conversationHistory.slice(0, -1).map(h => ({
        role: h.role,
        content: (h.content || '').slice(0, 2000),
      }))
    : [];

  const checkedExts = [...document.querySelectorAll('#type-filter-grid input:checked')].map(c => c.value);

  try {
    const res = await fetch(`${apiUrl()}/api/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        lang:    langSelect.value,
        k:       parseInt(kSelect.value) || 5,
        model:   modelSelect.value || undefined,
        llm:     LLM.payload() || undefined,   // external provider (key stays in localStorage → backend)
        mode:    modeSelect.value || 'es-semantic',
        history: history.length ? history : undefined,
        types:   checkedExts.length ? checkedExts.join(',') : undefined,
      }),
      signal: currentAbortController.signal,
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status} — ${res.statusText}`);
    }

    // ── SSE streaming via fetch + ReadableStream ──────────────────────────
    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer    = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep incomplete last line

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw || raw === '[DONE]') continue;

        let data;
        try { data = JSON.parse(raw); } catch { continue; }

        switch (data.type) {

          case 'status':
            setStatus(data.text || data.message || 'Processing…', true);
            break;

          case 'sources': {
            sources = data.sources || [];
            if (srcSection) srcSection.remove();
            srcSection = createSourcesSection(sources);
            botWrapper.appendChild(srcSection);
            break;
          }

          case 'token':
            fullText += data.text || data.token || '';
            // Re-render with cursor at end
            cursor.remove();
            botBubble.innerHTML = renderMarkdown(fullText);
            botBubble.appendChild(cursor);
            scrollToBottom(false);
            break;

          case 'done':
            cursor.remove();
            botBubble.innerHTML = renderMarkdown(fullText);
            addModelBadge(botMeta, data.model || modelSelect.value || '');
            setStatus('Done', false);
            break;

          case 'error': {
            cursor.remove();
            const errEl = document.createElement('div');
            errEl.className = 'error-bubble';
            errEl.textContent = data.text || data.message || 'An error occurred.';
            botBubble.innerHTML = '';
            botBubble.appendChild(errEl);
            setStatus('Error from server', false);
            break;
          }
        }
      }
    }

    // Flush any remaining buffer fragment
    if (buffer.startsWith('data: ')) {
      try {
        const data = JSON.parse(buffer.slice(6).trim());
        if (data.type === 'token') fullText += data.text || data.token || '';
      } catch { /* ignore */ }
    }

    // Ensure cursor removed and final render
    cursor.remove();
    if (!botBubble.querySelector('.error-bubble') && botBubble.innerHTML === '') {
      botBubble.innerHTML = renderMarkdown(fullText) ||
        '<em style="color:var(--muted);font-size:12px">No response received.</em>';
    }

    // Save assistant message
    conversationHistory.push({ role: 'assistant', content: fullText, sources });

  } catch (err) {
    cursor.remove();

    if (err.name === 'AbortError') {
      // User cancelled
      if (fullText.trim()) {
        botBubble.innerHTML = renderMarkdown(fullText) +
          '<span style="color:var(--muted);font-size:11px;margin-left:6px">[cancelled]</span>';
        conversationHistory.push({ role: 'assistant', content: fullText, sources });
      } else {
        botWrapper.remove();
        // Also remove the user message from history since nothing was answered
        conversationHistory.pop();
      }
      setStatus('Cancelled', false);
    } else {
      const errEl = document.createElement('div');
      errEl.className = 'error-bubble';
      errEl.textContent = `Connection error: ${err.message}`;
      botBubble.innerHTML = '';
      botBubble.appendChild(errEl);
      setStatus('Error — check server connection', false);
    }
  } finally {
    isStreaming = false;
    sendBtn.disabled = false;
    cancelBtn.classList.remove('visible');
    currentAbortController = null;
    scrollToBottom();
  }
}

// ── New conversation ───────────────────────────────────────────────────────
function newConversation() {
  if (isStreaming) currentAbortController?.abort();

  conversationHistory = [];
  chatContainer.querySelectorAll('.message').forEach(el => el.remove());
  emptyState.style.display = 'flex';
  setStatus('Ready', false);
  userInput.focus();
}

// ── Health check ───────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const res = await fetch(`${apiUrl()}/api/health`, {
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) throw new Error('not ok');
    backendNotice.style.display = 'none';
    return true;
  } catch {
    backendNotice.style.display = 'block';
    return false;
  }
}

// ── Load models ────────────────────────────────────────────────────────────
async function loadModels() {
  if (LLM.isExternal()) {
    LLM.fillSelect(modelSelect);
    setExternalStatus();
    return;
  }
  try {
    const res = await fetch(`${apiUrl()}/api/ollama-models`, {
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) throw new Error('not ok');
    const data = await res.json();
    const models = Array.isArray(data) ? data : (data.models || []);

    modelSelect.innerHTML = '';
    if (models.length > 0) {
      models.forEach(m => {
        const name = typeof m === 'string' ? m : (m.name || m.model || String(m));
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        modelSelect.appendChild(opt);
      });
      setOllamaStatus(true);
    } else {
      modelSelect.innerHTML = '<option value="">No models found</option>';
      setOllamaStatus(false);
    }
  } catch {
    modelSelect.innerHTML = '<option value="">Ollama unavailable</option>';
    setOllamaStatus(false);
  }
}

// ── Event listeners ────────────────────────────────────────────────────────
sendBtn.addEventListener('click', sendMessage);

cancelBtn.addEventListener('click', () => {
  currentAbortController?.abort();
});

userInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
  if (e.key === 'Escape' && isStreaming) {
    currentAbortController?.abort();
  }
});

userInput.addEventListener('input', autoResize);

newChatBtn.addEventListener('click', newConversation);

document.getElementById('suggestion-chips').addEventListener('click', e => {
  const chip = e.target.closest('[data-q]');
  if (!chip) return;
  userInput.value = chip.dataset.q;
  autoResize();
  userInput.focus();
  sendMessage();
});

// ── ES chunk count ─────────────────────────────────────────────────────────
async function loadEsCount() {
  try {
    const r = await fetch(`${apiUrl()}/api/es-search?q=*&limit=1`, { signal: AbortSignal.timeout(4000) });
    if (!r.ok) return;
    const d = await r.json();
    const count = Number(d.total) || 0;   // empty index → 0 (badge stays visible)
    document.getElementById('files-count').textContent = count.toLocaleString();
    document.getElementById('files-badge').style.display = '';
  } catch { /* ES not available */ }
}

// (applyTheme, toggleTheme → shared in assets/catalog.js)
applyTheme(localStorage.getItem('cc_theme') || 'dark');

// ── Search history ──────────────────────────────────────────────────────────
const _CHAT_HIST_MAX = 10;
function _getChatHistory() {
  try { return JSON.parse(localStorage.getItem('cc_chat_hist') || '[]'); } catch { return []; }
}
function _pushChatHistory(q) {
  if (!q.trim()) return;
  let h = _getChatHistory();
  h = [q, ...h.filter(x => x !== q)].slice(0, _CHAT_HIST_MAX);
  localStorage.setItem('cc_chat_hist', JSON.stringify(h));
}
function _renderChatHistList() {
  const list = document.getElementById('chat-hist-list');
  if (!list) return;
  const h = _getChatHistory();
  if (!h.length) { list.innerHTML = ''; return; }
  list.innerHTML = h.map((item, i) =>
    `<div class="chat-hist-item" onmousedown="event.preventDefault();_pickChatHist(${JSON.stringify(item)})">
       <span style="color:var(--muted);font-size:11px;">↺</span>
       <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(item)}</span>
       <span class="chi-del" onmousedown="event.stopPropagation();event.preventDefault();_deleteChatHist(${i})">✕</span>
     </div>`
  ).join('');
}
function _pickChatHist(val) {
  userInput.value = val;
  document.getElementById('chat-hist-list').classList.remove('open');
  userInput.focus();
}
function _deleteChatHist(idx) {
  let h = _getChatHistory(); h.splice(idx, 1);
  localStorage.setItem('cc_chat_hist', JSON.stringify(h));
  _renderChatHistList();
}

// ── Persist & restore all search options ───────────────────────────────────
function saveChatOptions() {
  localStorage.setItem('cc_chat_mode',  modeSelect.value);
  localStorage.setItem('cc_chat_k',     kSelect.value);
  localStorage.setItem('cc_chat_lang',  langSelect.value);
  localStorage.setItem('cc_chat_ctx',   ctxToggle.checked ? '1' : '0');
  const exts = [...document.querySelectorAll('#type-filter-grid input:checked')].map(c => c.value);
  localStorage.setItem('cc_chat_exts',  JSON.stringify(exts));
}
function restoreChatOptions() {
  const mode = localStorage.getItem('cc_chat_mode');
  const k    = localStorage.getItem('cc_chat_k');
  const lang = localStorage.getItem('cc_chat_lang');
  const ctx  = localStorage.getItem('cc_chat_ctx');
  const exts = JSON.parse(localStorage.getItem('cc_chat_exts') || 'null');
  if (mode) modeSelect.value = mode;
  if (k)    kSelect.value    = k;
  if (lang) langSelect.value = lang;
  if (ctx  !== null) ctxToggle.checked = ctx === '1';
  if (exts && exts.length) {
    document.querySelectorAll('#type-filter-grid input').forEach(cb => {
      cb.checked = exts.includes(cb.value);
    });
  }
}

// ── Bootstrap ──────────────────────────────────────────────────────────────
(async () => {
  const savedQ = localStorage.getItem('cc_last_query');
  if (savedQ) userInput.value = savedQ;

  restoreChatOptions();

  // Attach change listeners to auto-save options
  modeSelect.addEventListener('change', saveChatOptions);
  kSelect.addEventListener('change', saveChatOptions);
  langSelect.addEventListener('change', saveChatOptions);
  ctxToggle.addEventListener('change', saveChatOptions);
  document.getElementById('type-filter-grid').addEventListener('change', saveChatOptions);
  modelSelect.addEventListener('change', () => {
    if (LLM.isExternal()) { LLM.setModel(modelSelect.value); setExternalStatus(); }
    else localStorage.setItem('cc_chat_model', modelSelect.value);
  });
  // Provider / key / model changed in the ⚙ LLM dialog → refresh the header
  let _llmWasExternal = LLM.isExternal();
  window.addEventListener('llm-config-changed', () => {
    const ext = LLM.isExternal();
    if (ext || _llmWasExternal) {
      const keep = modelSelect.value;
      loadModels().then(() => {
        if (!ext && keep && [...modelSelect.options].some(o => o.value === keep)) modelSelect.value = keep;
      });
    }
    _llmWasExternal = ext;
  });

  // History dropdown on textarea focus/blur
  userInput.addEventListener('focus', () => {
    _renderChatHistList();
    const list = document.getElementById('chat-hist-list');
    const h = _getChatHistory();
    if (h.length) list.classList.add('open');
  });
  userInput.addEventListener('blur', () => {
    setTimeout(() => document.getElementById('chat-hist-list').classList.remove('open'), 150);
  });

  setStatus('Connecting to backend…', true);
  const healthy = await checkHealth();
  setStatus(healthy ? 'Loading models…' : 'Backend unavailable', healthy);
  await Promise.all([loadModels(), loadEsCount()]);
  setStatus('Ready', false);
  // Restore model after it's loaded
  const savedModel = localStorage.getItem('cc_chat_model');
  if (savedModel && !LLM.isExternal()) modelSelect.value = savedModel;
  userInput.focus();
})();
