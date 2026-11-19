/* ocr.js — OCR Extract page logic */

'use strict';

const API = window.location.origin;

// ── State ──────────────────────────────────────────────────────────────────────
let _file       = null;      // selected File object
let _streaming  = false;     // extraction in progress
let _es         = null;      // active EventSource
let _fullText   = '';        // accumulated full extracted text
let _pageTexts  = {};        // {pageNum: text}
let _curView    = 'text';    // 'text' | 'markdown'
let _methods    = {};        // methods status from API

// ── DOM references ─────────────────────────────────────────────────────────────
const dropZone       = () => document.getElementById('drop-zone');
const fileInput      = () => document.getElementById('file-input');
const fileInfo       = () => document.getElementById('file-info');
const fileInfoName   = () => document.getElementById('file-info-name');
const fileInfoSize   = () => document.getElementById('file-info-size');
const extractBtn     = () => document.getElementById('extract-btn');
const progressRow    = () => document.getElementById('ocr-progress');
const progressFill   = () => document.getElementById('progress-fill');
const progressLabel  = () => document.getElementById('progress-label');
const statusBar      = () => document.getElementById('ocr-status-bar');
const resultEmpty    = () => document.getElementById('result-empty');
const textView       = () => document.getElementById('ocr-text-view');
const textArea       = () => document.getElementById('ocr-textarea');
const mdView         = () => document.getElementById('ocr-md-view');
const toolbarTitle   = () => document.getElementById('ocr-toolbar-title');
const copyBtn        = () => document.getElementById('copy-btn');
const dlBtn          = () => document.getElementById('dl-btn');
const clearBtn       = () => document.getElementById('clear-btn');
const cancelBtn      = () => document.getElementById('cancel-btn');
const ollamaOpts     = () => document.getElementById('ollama-opts');
const tessOpts       = () => document.getElementById('tess-opts');
const easyOpts       = () => document.getElementById('easy-opts');
const paddleOpts     = () => document.getElementById('paddle-opts');
const modelSelect    = () => document.getElementById('ollama-model-select');
const pagesInput     = () => document.getElementById('pages-input');
const dpiSelect      = () => document.getElementById('dpi-select');


// ── Initialisation ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadMethods();
  bindDropZone();
  bindMethodRadios();
  document.getElementById('format-select').addEventListener('change', () => {});
  cancelBtn().addEventListener('click', cancelExtraction);
  copyBtn().addEventListener('click', copyText);
  dlBtn().addEventListener('click', downloadText);
  clearBtn().addEventListener('click', clearResult);
  document.getElementById('view-text-btn').addEventListener('click', () => setView('text'));
  document.getElementById('view-md-btn').addEventListener('click', () => setView('markdown'));
  extractBtn().addEventListener('click', startExtraction);
  document.getElementById('install-model-btn').addEventListener('click', () => openInstallModal());
  document.getElementById('install-cancel-btn').addEventListener('click', closeInstallModal);
  document.getElementById('install-pull-btn').addEventListener('click', doInstallModel);
  document.getElementById('install-model-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') doInstallModel();
  });
  document.getElementById('install-engine-run').addEventListener('click', doInstallEngine);
  document.getElementById('install-engine-cancel').addEventListener('click', closeInstallEngineModal);
  // Page range preset
  document.getElementById('pages-all-btn').addEventListener('click', () => {
    pagesInput().value = 'all'; pagesInput().disabled = true;
  });
  document.getElementById('pages-range-btn').addEventListener('click', () => {
    pagesInput().disabled = false; pagesInput().focus();
  });
});


// ── Load methods from API ──────────────────────────────────────────────────────
async function loadMethods() {
  try {
    const r = await fetch(`${API}/api/ocr/methods`);
    if (!r.ok) throw new Error(r.statusText);
    _methods = await r.json();
    renderMethods();
    // Also load Ollama models
    loadOllamaModels();
  } catch (e) {
    setStatus(`Backend unreachable: ${e.message}`);
    // Still render methods with unknown status
    renderMethods();
  }
}

function renderMethods() {
  const CATS = {
    pdf: { label: 'PDF text extraction', color: 'blue' },
    ocr: { label: 'Image OCR engines',  color: 'green' },
    ai:  { label: 'AI vision models',   color: 'purple' },
  };
  const ORDER = ['pymupdf', 'pdfminer', 'tesseract', 'easyocr', 'paddleocr', 'ollama'];
  const container = document.getElementById('method-list');
  container.innerHTML = '';

  const CATEGORY_COLORS = { pdf: '#4f8ef7', ocr: '#34d399', ai: '#a78bfa' };

  ORDER.forEach(key => {
    const m = _methods[key] || {};
    const avail = m.available !== false;
    const label = m.label || key;
    const sub   = m.subtitle || m.category || '';
    const note  = m.note || '';
    const cat   = m.category || 'ocr';

    const item = document.createElement('label');
    item.className = `method-item${avail ? '' : ' disabled'}`;
    item.dataset.method = key;
    item.title = avail ? note : (m.install || note);

    let badgeHtml;
    if (avail) {
      badgeHtml = `<span class="method-badge badge-ok">ready</span>`;
    } else if (key === 'ollama') {
      badgeHtml = `<button type="button" class="method-badge badge-miss badge-install-btn"
                           onclick="event.stopPropagation();openInstallModal()"
                           title="Pull an Ollama vision model">install</button>`;
    } else {
      badgeHtml = `<button type="button" class="method-badge badge-miss badge-install-btn"
                           onclick="event.stopPropagation();openInstallEngineModal('${key}')"
                           title="${(m.install || '').replace(/"/g, '&quot;')}">install</button>`;
    }

    item.innerHTML = `
      <input type="radio" name="method" value="${key}" class="method-radio" ${key === 'pymupdf' ? 'checked' : ''} ${avail ? '' : 'disabled'}>
      <div class="method-body">
        <div class="method-label">${label}</div>
        <div class="method-sub">${sub}</div>
      </div>
      ${badgeHtml}
    `;
    container.appendChild(item);
  });

  bindMethodRadios();
  // Default select first available
  const firstAvail = ORDER.find(k => _methods[k] && _methods[k].available !== false);
  if (firstAvail) {
    const radio = container.querySelector(`input[value="${firstAvail}"]`);
    if (radio) { radio.checked = true; showMethodOptions(firstAvail); }
  }
}

function bindMethodRadios() {
  document.querySelectorAll('input[name=method]').forEach(r => {
    r.addEventListener('change', () => showMethodOptions(r.value));
  });
}

function showMethodOptions(method) {
  // Highlight selected item
  document.querySelectorAll('.method-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.method === method);
  });
  // Show/hide sub-options
  const allOpts = [ollamaOpts(), tessOpts(), easyOpts(), paddleOpts()];
  allOpts.forEach(el => { if (el) el.classList.remove('show'); });
  if (method === 'ollama' && ollamaOpts())  ollamaOpts().classList.add('show');
  if (method === 'tesseract' && tessOpts()) tessOpts().classList.add('show');
  if (method === 'easyocr' && easyOpts())   easyOpts().classList.add('show');
  if (method === 'paddleocr' && paddleOpts()) paddleOpts().classList.add('show');
}

async function loadOllamaModels() {
  try {
    const r = await fetch(`${API}/api/ocr/models`);
    if (!r.ok) return;
    const data = await r.json();
    const sel = modelSelect();
    if (!sel) return;
    const vision = data.vision || [];
    const all    = data.models || [];
    sel.innerHTML = '';
    if (!vision.length) {
      sel.innerHTML = '<option value="">No vision models installed</option>';
      return;
    }
    vision.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = m;
      sel.appendChild(opt);
    });
    // Also offer all models (some may have vision capability)
    if (all.length > vision.length) {
      const sep = document.createElement('option');
      sep.disabled = true; sep.textContent = '── all models ──';
      sel.appendChild(sep);
      all.filter(m => !vision.includes(m)).forEach(m => {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        sel.appendChild(opt);
      });
    }
  } catch (_) {}
}


// ── File handling ──────────────────────────────────────────────────────────────
function bindDropZone() {
  const dz = dropZone();
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
  dz.addEventListener('drop', e => {
    e.preventDefault(); dz.classList.remove('drag-over');
    const f = e.dataTransfer.files[0];
    if (f) setFile(f);
  });
  fileInput().addEventListener('change', e => {
    if (e.target.files[0]) setFile(e.target.files[0]);
  });
}

function setFile(f) {
  _file = f;
  const ext = f.name.split('.').pop().toLowerCase();
  const icons = { pdf: '📄', png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️',
                  bmp: '🖼️', tiff: '🖼️', tif: '🖼️', webp: '🖼️' };
  document.getElementById('file-info-icon').textContent = icons[ext] || '📁';
  fileInfoName().textContent = f.name;
  fileInfoSize().textContent = formatBytes(f.size);
  fileInfo().classList.add('show');
  dropZone().style.display = 'none';

  // If PDF, enable page range; otherwise disable
  const isPdf = ext === 'pdf';
  pagesInput().disabled  = !isPdf;
  document.getElementById('pages-all-btn').disabled   = !isPdf;
  document.getElementById('pages-range-btn').disabled = !isPdf;
  if (!isPdf) pagesInput().value = '1';

  // Disable PDF-only methods if file is image
  ['pymupdf', 'pdfminer'].forEach(m => {
    const item = document.querySelector(`.method-item[data-method="${m}"]`);
    if (item) {
      const radio = item.querySelector('input[type=radio]');
      if (!isPdf) {
        item.classList.add('disabled');
        if (radio) radio.disabled = true;
        // Deselect if currently selected
        if (radio && radio.checked) {
          const tess = document.querySelector('input[value=tesseract]');
          if (tess && !tess.disabled) { tess.checked = true; showMethodOptions('tesseract'); }
        }
      } else {
        if (_methods[m] && _methods[m].available !== false) {
          item.classList.remove('disabled');
          if (radio) radio.disabled = false;
        }
      }
    }
  });

  setStatus(`File selected: ${f.name}`);
}

function clearFile() {
  _file = null;
  fileInfo().classList.remove('show');
  dropZone().style.display = '';
  fileInput().value = '';
  pagesInput().disabled = false;
  setStatus('Ready');
}


// ── Extraction ─────────────────────────────────────────────────────────────────
function selectedMethod() {
  const r = document.querySelector('input[name=method]:checked');
  return r ? r.value : 'pymupdf';
}

function startExtraction() {
  if (_streaming) return;
  if (!_file) { setStatus('Please select a file first.'); return; }

  const method = selectedMethod();
  const model  = (modelSelect() && modelSelect().value) || '';
  if (method === 'ollama' && !model) {
    setStatus('Select an Ollama vision model first.'); return;
  }

  const langTess   = (document.getElementById('lang-tess')   || {}).value || 'eng';
  const langEasy   = (document.getElementById('lang-easy')   || {}).value || 'en';
  const langPaddle = (document.getElementById('lang-paddle') || {}).value || 'en';
  const langMap    = { tesseract: langTess, easyocr: langEasy, paddleocr: langPaddle };
  const lang       = langMap[method] || 'eng';

  const pages  = pagesInput().disabled ? 'all' : (pagesInput().value.trim() || 'all');
  const prompt = (document.getElementById('ollama-prompt') || {}).value || '';
  const fmt    = (document.getElementById('format-select') || {}).value || 'markdown';
  const dpi    = (dpiSelect() || {}).value || '200';

  const fd = new FormData();
  fd.append('file', _file);
  fd.append('method', method);
  fd.append('model', model);
  fd.append('lang', lang);
  fd.append('pages', pages);
  fd.append('prompt', prompt);
  fd.append('format', fmt);
  fd.append('dpi', dpi);

  _streaming = true;
  _fullText  = '';
  _pageTexts = {};
  textArea().value = '';
  mdView().innerHTML = '';
  resultEmpty().style.display = 'none';
  textView().classList.add('show');
  mdView().classList.remove('show');
  if (_curView === 'markdown') { setView('markdown'); }

  progressRow().classList.add('show');
  progressFill().style.width = '0%';
  progressLabel().textContent = 'Starting…';
  cancelBtn().style.display = '';
  extractBtn().disabled = true;
  toolbarTitle().textContent = `Extracting with ${method}…`;

  // Use fetch + ReadableStream to POST form data and receive SSE
  fetch(`${API}/api/ocr/extract`, { method: 'POST', body: fd })
    .then(resp => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      function pump() {
        return reader.read().then(({ done, value }) => {
          if (done) { onExtractionDone(); return; }
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop();
          lines.forEach(line => {
            if (line.startsWith('data: ')) {
              try { handleEvent(JSON.parse(line.slice(6))); } catch (_) {}
            }
          });
          return pump();
        });
      }
      return pump();
    })
    .catch(e => {
      setStatus(`Error: ${e.message}`);
      onExtractionDone();
    });
}

function handleEvent(ev) {
  switch (ev.type) {
    case 'info':
      progressLabel().textContent = `0 / ${ev.selected} pages`;
      setStatus(`Extracting ${ev.filename} — ${ev.selected} page(s) selected, method: ${ev.method}`);
      break;

    case 'status':
      setStatus(ev.text);
      progressLabel().textContent = ev.text;
      break;

    case 'page': {
      const total = ev.total || 1;
      _pageTexts[ev.page] = ev.text;
      rebuildFullText();
      const done = Object.keys(_pageTexts).length;
      const pct  = Math.round(done / total * 100);
      progressFill().style.width = pct + '%';
      progressLabel().textContent = `${done} / ${total} pages`;
      updateTextDisplay();
      break;
    }

    case 'token':
      // Ollama streaming token — append to current page buffer
      if (!_pageTexts[ev.page]) _pageTexts[ev.page] = '';
      _pageTexts[ev.page] += ev.token;
      rebuildFullText();
      updateTextDisplay();
      break;

    case 'page_done':
      _pageTexts[ev.page] = ev.text;
      rebuildFullText();
      updateTextDisplay();
      break;

    case 'done':
      _fullText = ev.text;
      textArea().value = _fullText;
      if (_curView === 'markdown') renderMarkdown();
      enableResultButtons(true);
      break;

    case 'error':
      setStatus(`Error: ${ev.text}`);
      break;
  }
}

function rebuildFullText() {
  const parts = Object.keys(_pageTexts)
    .sort((a, b) => Number(a) - Number(b))
    .map(k => _pageTexts[k]);
  _fullText = parts.join('\n\n---\n\n');
}

function updateTextDisplay() {
  if (_curView === 'text') {
    textArea().value = _fullText;
    textArea().scrollTop = textArea().scrollHeight;
  } else {
    renderMarkdown();
    mdView().scrollTop = mdView().scrollHeight;
  }
}

function onExtractionDone() {
  _streaming = false;
  cancelBtn().style.display = 'none';
  extractBtn().disabled = false;
  progressRow().classList.remove('show');
  if (_fullText) {
    toolbarTitle().textContent = `Extracted — ${_fullText.length.toLocaleString()} chars`;
    enableResultButtons(true);
  } else {
    toolbarTitle().textContent = 'No content extracted';
    resultEmpty().style.display = '';
    textView().classList.remove('show');
  }
}

function cancelExtraction() {
  if (_es) { _es.close(); _es = null; }
  _streaming = false;
  cancelBtn().style.display = 'none';
  extractBtn().disabled = false;
  progressRow().classList.remove('show');
  setStatus('Cancelled.');
  toolbarTitle().textContent = 'Cancelled';
}


// ── View toggle ────────────────────────────────────────────────────────────────
function setView(v) {
  _curView = v;
  document.getElementById('view-text-btn').classList.toggle('active', v === 'text');
  document.getElementById('view-md-btn').classList.toggle('active', v === 'markdown');
  textView().classList.toggle('show', v === 'text');
  mdView().classList.toggle('show', v === 'markdown');
  if (v === 'markdown') renderMarkdown();
}

function renderMarkdown() {
  if (typeof marked === 'undefined') return;
  mdView().innerHTML = marked.parse(_fullText || '');
  if (window.hljs) {
    mdView().querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
  }
}


// ── Result actions ─────────────────────────────────────────────────────────────
function enableResultButtons(on) {
  copyBtn().disabled  = !on;
  dlBtn().disabled    = !on;
  clearBtn().disabled = !on;
}

function copyText() {
  if (!_fullText) return;
  navigator.clipboard.writeText(_fullText).then(() => setStatus('Copied to clipboard.'));
}

function downloadText() {
  if (!_fullText) return;
  const ext  = _curView === 'markdown' ? '.md' : '.txt';
  const stem = (_file && _file.name.replace(/\.[^.]+$/, '')) || 'ocr-result';
  const blob = new Blob([_fullText], { type: 'text/plain' });
  const a    = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = stem + ext;
  a.click();
  URL.revokeObjectURL(a.href);
}

function clearResult() {
  _fullText  = '';
  _pageTexts = {};
  textArea().value = '';
  mdView().innerHTML = '';
  enableResultButtons(false);
  toolbarTitle().textContent = 'Result';
  resultEmpty().style.display = '';
  textView().classList.remove('show');
  mdView().classList.remove('show');
}


// ── Install model modal ────────────────────────────────────────────────────────
function openInstallModal() {
  document.getElementById('install-modal-overlay').classList.add('show');
  document.getElementById('install-model-input').focus();
}

function closeInstallModal() {
  document.getElementById('install-modal-overlay').classList.remove('show');
  document.getElementById('install-log').classList.remove('show');
  document.getElementById('install-log').textContent = '';
}

async function doInstallModel() {
  const name = document.getElementById('install-model-input').value.trim();
  if (!name) return;
  const log = document.getElementById('install-log');
  log.classList.add('show');
  log.textContent = `Pulling ${name}…\n`;
  document.getElementById('install-pull-btn').disabled = true;
  try {
    const r = await fetch(`${API}/api/tools/ollama-pull`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: name }),
    });
    const data = await r.json();
    if (data.task_id) {
      pollInstallTask(data.task_id, log);
    } else {
      log.textContent += data.error || 'Unknown error';
    }
  } catch (e) {
    log.textContent += `Error: ${e.message}`;
    document.getElementById('install-pull-btn').disabled = false;
  }
}

function pollInstallTask(taskId, log) {
  let last = 0;
  const iv = setInterval(async () => {
    try {
      const r = await fetch(`${API}/api/tools/status/${taskId}`);
      const info = await r.json();
      const lines = info.log || [];
      while (last < lines.length) { log.textContent += lines[last++] + '\n'; }
      log.scrollTop = log.scrollHeight;
      if (info.status === 'done' || info.status === 'error') {
        clearInterval(iv);
        document.getElementById('install-pull-btn').disabled = false;
        if (info.status === 'done') {
          setStatus(`Model ${document.getElementById('install-model-input').value} installed.`);
          closeInstallModal();
          loadOllamaModels();
        }
      }
    } catch (_) { clearInterval(iv); }
  }, 800);
}


// ── Utilities ──────────────────────────────────────────────────────────────────
function setStatus(msg) {
  const sb = statusBar();
  if (sb) sb.textContent = msg;
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(2) + ' MB';
}

// ── Install OCR engine modal ───────────────────────────────────────────────────
const ENGINE_INFO = {
  tesseract: {
    title: 'Install Tesseract OCR',
    desc: 'Classic OCR engine — 100+ language support, fast on CPU.',
    packages: ['pytesseract', 'Pillow'],
    note: '⚠ Also requires the Tesseract binary: <code>apt install tesseract-ocr</code> (Linux), <code>brew install tesseract</code> (Mac), or download the Windows installer from GitHub.',
  },
  easyocr: {
    title: 'Install EasyOCR',
    desc: 'Deep learning OCR — accurate for 80+ languages. GPU optional.',
    packages: ['easyocr'],
    note: 'First run will download model weights (~1–2 GB).',
  },
  paddleocr: {
    title: 'Install PaddleOCR',
    desc: 'Layout-aware OCR — excellent for tables and complex document layouts.',
    packages: ['paddlepaddle', 'paddleocr'],
    note: 'First run will download model weights (~500 MB).',
  },
};

let _installEngineKey = '';

function openInstallEngineModal(engine) {
  const info = ENGINE_INFO[engine];
  if (!info) return;
  _installEngineKey = engine;

  document.getElementById('install-engine-title').textContent = info.title;
  const pkgTags = info.packages
    .map(p => `<code class="pkg-tag">${p}</code>`).join(' ');
  document.getElementById('install-engine-meta').innerHTML =
    `<div>${info.desc}</div>` +
    `<div style="margin-top:6px"><strong>Packages:</strong> ${pkgTags}</div>` +
    (info.note ? `<div class="install-engine-note">${info.note}</div>` : '');

  const log = document.getElementById('install-engine-log');
  log.classList.remove('show');
  log.textContent = '';
  const runBtn = document.getElementById('install-engine-run');
  runBtn.disabled = false;
  runBtn.textContent = 'Install';

  document.getElementById('install-engine-overlay').classList.add('show');
}

function closeInstallEngineModal() {
  document.getElementById('install-engine-overlay').classList.remove('show');
}

async function doInstallEngine() {
  if (!_installEngineKey) return;
  const runBtn = document.getElementById('install-engine-run');
  const log    = document.getElementById('install-engine-log');
  log.classList.add('show');
  log.textContent = `Installing ${_installEngineKey}…\n`;
  runBtn.disabled = true;
  runBtn.textContent = 'Installing…';
  try {
    const r = await fetch(`${API}/api/ocr/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engine: _installEngineKey }),
    });
    const data = await r.json();
    if (data.task_id) {
      pollEngineInstallTask(data.task_id, log, runBtn);
    } else {
      log.textContent += data.error || 'Unknown error';
      runBtn.disabled = false;
      runBtn.textContent = 'Retry';
    }
  } catch (e) {
    log.textContent += `Error: ${e.message}`;
    runBtn.disabled = false;
    runBtn.textContent = 'Retry';
  }
}

function pollEngineInstallTask(taskId, log, runBtn) {
  let last = 0;
  const iv = setInterval(async () => {
    try {
      const r    = await fetch(`${API}/api/tools/status/${taskId}`);
      const info = await r.json();
      const lines = info.log || [];
      while (last < lines.length) { log.textContent += lines[last++] + '\n'; }
      log.scrollTop = log.scrollHeight;
      if (info.status === 'done' || info.status === 'error') {
        clearInterval(iv);
        if (info.status === 'done') {
          runBtn.textContent = 'Done ✓';
          setStatus(`${_installEngineKey} installed successfully.`);
          setTimeout(() => { closeInstallEngineModal(); loadMethods(); }, 800);
        } else {
          runBtn.disabled = false;
          runBtn.textContent = 'Retry';
          log.textContent += '\nInstallation failed.';
        }
      }
    } catch (_) { clearInterval(iv); }
  }, 800);
}


// Expose globals for inline HTML handlers
window.clearFile                = clearFile;
window.openInstallModal         = openInstallModal;
window.closeInstallModal        = closeInstallModal;
window.openInstallEngineModal   = openInstallEngineModal;
window.closeInstallEngineModal  = closeInstallEngineModal;
