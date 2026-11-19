'use strict';

// ── API URL ───────────────────────────────────────────────────────────────────
// Returns the Flask backend base URL (no trailing slash).
// Reads from #api-url input if present (search-console.html), otherwise falls back to
// 127.0.0.1:5000 (chat.html which has no such input).
function apiUrl() {
  const el = document.getElementById('api-url');
  return (el ? el.value : 'http://127.0.0.1:5000').replace(/\/$/, '') || 'http://127.0.0.1:5000';
}

// ── HTML escaping ─────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
// escapeHtml: also escapes " — alias used by chat.html
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Content utilities ─────────────────────────────────────────────────────────
function unescapeContent(s) {
  // Decode JSON-style escape sequences that may appear in extracted file content.
  // Order matters: \\ must be last to avoid double-processing.
  return s
    .replace(/\\n/g,  '\n')
    .replace(/\\t/g,  '\t')
    .replace(/\\r/g,  '')
    .replace(/\\"/g,  '"')
    .replace(/\\'/g,  "'")
    .replace(/\\\\/g, '\\')
    .replace(/\n{3,}/g, '\n\n');
}

function formatTime(sec) {
  if (sec == null) return '';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

// ── File type mappings ────────────────────────────────────────────────────────
const EXT_TO_HLJS = {
  js:'javascript', ts:'typescript', py:'python', rb:'ruby', java:'java', cs:'csharp',
  cpp:'cpp', c:'c', h:'c', go:'go', rs:'rust', php:'php', sh:'bash', bash:'bash',
  sql:'sql', html:'xml', htm:'xml', xml:'xml', css:'css', json:'json',
  yaml:'yaml', yml:'yaml', toml:'ini', ini:'ini', md:'markdown', markdown:'markdown',
  txt:null, pdf:null, docx:null, doc:null, xlsx:null, pptx:null, ppt:null,
  srt:null, vtt:null, csv:null, log:null, rtf:null,
};

const FILE_TYPE_ICONS = {
  pdf:'📄', md:'📋', txt:'📋', html:'🌐', htm:'🌐',
  srt:'💬', vtt:'💬', docx:'📝', doc:'📝', xlsx:'📈', xls:'📈',
  pptx:'📊', ppt:'📊', py:'🐍', js:'⚡', ts:'⚡', json:'🔧',
  mp4:'🎬', mkv:'🎬', avi:'🎬', webm:'🎬', mp3:'🎵', wav:'🎵',
  csv:'📈', xml:'🔧', yaml:'🔧', yml:'🔧', java:'☕', cpp:'🔧',
  c:'🔧', h:'🔧', go:'🔧', rs:'🦀', rb:'💎', sh:'📜', log:'📋',
};

function getFileIcon(filename) {
  const ext = (filename || '').split('.').pop().toLowerCase();
  return FILE_TYPE_ICONS[ext] || '📎';
}

// ── Chunk highlighting ────────────────────────────────────────────────────────
// Apply syntax highlighting first, then mark the chunk range using a TreeWalker
// so both work simultaneously even with hljs <span> elements inside the chunk.
function _highlightChunk(pre, fullText, chunkText, ext) {
  const norm     = s => s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const normFull = norm(fullText);
  const needle   = norm(chunkText).trim();
  const idx      = needle ? normFull.indexOf(needle) : -1;

  const lang = ext && EXT_TO_HLJS[ext];
  if (typeof hljs !== 'undefined' && lang) {
    const code = document.createElement('code');
    code.className = `language-${lang}`;
    code.textContent = normFull;
    pre.innerHTML = '';
    pre.appendChild(code);
    hljs.highlightElement(code);
  } else {
    pre.textContent = normFull;
  }

  if (idx >= 0) _markRangeInEl(pre, idx, idx + needle.length);
}

function _markRangeInEl(container, start, end) {
  let pos = 0, nodes = [];
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const len = n.textContent.length;
    if (pos + len > start && pos < end)
      nodes.push({ n, s: Math.max(0, start - pos), e: Math.min(len, end - pos) });
    pos += len;
    if (pos >= end) break;
  }
  for (const { n, s, e } of nodes.reverse()) {
    const before = n.textContent.slice(0, s);
    const match  = n.textContent.slice(s, e);
    const after  = n.textContent.slice(e);
    const mark   = document.createElement('mark');
    mark.className = 'chunk-highlight';
    mark.textContent = match;
    const p = n.parentNode, nx = n.nextSibling;
    p.removeChild(n);
    if (before) p.insertBefore(document.createTextNode(before), nx);
    p.insertBefore(mark, nx);
    if (after)  p.insertBefore(document.createTextNode(after), nx);
  }
  const first = container.querySelector('mark.chunk-highlight');
  if (first) setTimeout(() => first.scrollIntoView({ behavior: 'smooth', block: 'center' }), 120);
}

// ── Markdown in iframe ────────────────────────────────────────────────────────
// Render markdown in a sandboxed iframe using <base href> so all relative
// resources (images, links) resolve through /api/serve/<path> automatically.
// URL of /api/serve for an absolute path. Handles Windows drive paths (C:\…), UNC shares
// (\\server\share\…) and POSIX paths, and percent-encodes every segment (spaces, umlauts, # ? %).
function serveUrl(path) {
  let p = String(path).replace(/\\/g, '/');
  if (/^[A-Za-z]:\//.test(p)) { /* drive path: keep "C:/…" */ }
  else if (p.startsWith('//')) p = '__unc__/' + p.replace(/^\/+/, '');
  else p = p.replace(/^\/+/, '');
  return `${apiUrl()}/api/serve/` + p.split('/').map(encodeURIComponent).join('/');
}

function _renderMdInIframe(preview, mdContent, filePath, apiBase) {
  // <base> = the file's folder, so relative images/links in the markdown resolve through /api/serve
  const baseHref = serveUrl(filePath.replace(/[\\/][^\\/]*$/, '')) + '/';
  const isDark   = document.documentElement.getAttribute('data-theme') !== 'light';
  const fg    = isDark ? '#e2e8f0' : '#1e293b';
  const muted = isDark ? '#8892a4' : '#64748b';
  const bdr   = isDark ? '#2a2d3a' : '#d1d5db';
  const cbg   = isDark ? '#252836' : '#f1f5f9';
  const acc   = isDark ? '#4f8ef7' : '#2563eb';
  const srcdoc = `<!DOCTYPE html><html><head>
<base href="${baseHref}">
<meta charset="UTF-8">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.7;padding:10px 14px;color:${fg}}
img{max-width:100%;border-radius:4px;display:block;margin:.5em 0}
h1,h2{border-bottom:1px solid ${bdr};padding-bottom:.3em;margin:1em 0 .5em}
h1,h2,h3,h4{font-weight:600;margin-top:.8em}
p{margin:.5em 0}
pre{background:${cbg};border-radius:5px;padding:10px;overflow-x:auto;font-size:12px;margin:.5em 0}
code{background:${cbg};border-radius:3px;padding:1px 4px;font-size:12px;font-family:'Cascadia Code','Fira Code',monospace}
pre code{background:transparent;padding:0}
blockquote{border-left:3px solid ${acc};margin:.5em 0;padding-left:12px;color:${muted}}
table{border-collapse:collapse;width:100%;font-size:12px;margin:.5em 0}
th,td{border:1px solid ${bdr};padding:5px 8px;text-align:left}
th{background:${cbg}}
a{color:${acc};text-decoration:none}a:hover{text-decoration:underline}
ul,ol{padding-left:1.5em;margin:.5em 0}li{margin:.2em 0}
hr{border:none;border-top:1px solid ${bdr};margin:1em 0}
</style>
</head><body>${marked.parse(mdContent)}</body></html>`;
  preview.innerHTML = '';
  const iframe = document.createElement('iframe');
  iframe.setAttribute('srcdoc', srcdoc);
  iframe.style.cssText = 'width:100%;border:none;display:block;';
  preview.appendChild(iframe);
}

// ── File viewer modal ─────────────────────────────────────────────────────────
let _fmodalSrtPath = null, _fmodalSrtTime = null, _fmodalChunkText = '', _fmodalViewMode = 'iframe';
let _fmodalSrtVideoEl = null;

// Path bar under the title: absolute path on disk + whether the backend can find / serve it.
async function _fmodalPathInfo(path) {
  let bar = document.getElementById('fmodal-pathbar');
  if (!bar) {
    const hdr = document.querySelector('#fmodal-overlay .fmodal-hdr');
    if (!hdr) return null;
    bar = document.createElement('div');
    bar.id = 'fmodal-pathbar';
    bar.style.cssText = 'display:flex;align-items:center;gap:8px;padding:4px 14px;font-size:11px;' +
      'border-bottom:1px solid var(--border,#2a2d3a);color:var(--muted,#8888aa);min-width:0;';
    hdr.insertAdjacentElement('afterend', bar);
  }
  const esc = escHtml(path);
  bar.innerHTML = `<span title="Absolute path where the file is looked up on disk">📁</span>` +
    `<code id="fmodal-pathtxt" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;user-select:all" title="${esc}">${esc}</code>` +
    `<span id="fmodal-pathstate">checking…</span>` +
    `<button type="button" class="btn secondary" style="padding:1px 7px;font-size:11px" title="Copy path" ` +
    `onclick="navigator.clipboard&&navigator.clipboard.writeText(document.getElementById('fmodal-pathtxt').textContent)">⧉ Copy</button>`;
  const state = bar.querySelector('#fmodal-pathstate');
  try {
    const r = await fetch(`${apiUrl()}/api/file-info?path=${encodeURIComponent(path)}`, { signal: AbortSignal.timeout(8000) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const info = await r.json();
    if (info.exists && info.is_file && info.allowed) {
      const kb = info.size != null ? ` · ${info.size >= 1048576 ? (info.size / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(info.size / 1024)) + ' KB'}` : '';
      state.innerHTML = `<span style="color:var(--green,#3ecf8e)">✓ found${kb}</span>`;
      state.title = 'Served from base path: ' + (info.matched_base || '');
    } else {
      state.innerHTML = `<span style="color:var(--red,#e74c3c)">✗ ${info.exists ? 'not allowed' : 'not found'}</span>`;
      state.title = info.reason || '';
    }
    return info;
  } catch (e) {
    state.textContent = '';
    return null;                       // older backend without /api/file-info — fall back to old behaviour
  }
}

function _fmodalProblemHtml(info, path) {
  const bases = (info.bases || []).map(b => `<li><code>${escHtml(b)}</code></li>`).join('');
  return `<div style="padding:18px 22px;line-height:1.6;font-size:13px;max-width:760px;margin:auto;overflow:auto">` +
    `<div style="font-size:15px;font-weight:600;color:var(--red,#e74c3c);margin-bottom:8px">` +
    `${info.exists ? 'File cannot be shown' : 'File not found on disk'}</div>` +
    `<div>${escHtml(info.reason || '')}</div>` +
    `<div style="margin-top:10px;color:var(--muted,#8888aa)">Looked up at:</div>` +
    `<div><code style="word-break:break-all;user-select:all">${escHtml(path)}</code></div>` +
    (bases ? `<details style="margin-top:10px"><summary style="cursor:pointer;color:var(--muted,#8888aa)">Allowed base paths (${info.bases.length})</summary><ul style="margin:6px 0 0 18px">${bases}</ul></details>` : '') +
    `</div>`;
}

async function openFileModal(path, srtTime, chunkText = '') {
  if (!path) return;
  const ext    = (path.split('.').pop() || '').toLowerCase();
  const isSrt  = ['srt', 'vtt'].includes(ext);
  const isPdf  = ext === 'pdf';
  const isHtml = ['html', 'htm'].includes(ext);
  const isMd   = ['md', 'markdown'].includes(ext);
  const isImg  = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'].includes(ext);
  const isCsv  = ext === 'csv';
  const hasViewTab = isPdf || isHtml || isMd || isImg || isSrt || isCsv;

  _fmodalSrtPath    = isSrt ? path : null;
  _fmodalSrtTime    = srtTime ?? null;
  _fmodalChunkText  = chunkText || '';
  _fmodalSrtVideoEl = null;
  _fmodalViewMode   = isMd ? 'markdown' : isImg ? 'image' : isSrt ? 'srt-video' : isCsv ? 'csv-table' : 'iframe';

  const pre      = document.getElementById('fmodal-pre');
  const pdfFrame = document.getElementById('fmodal-pdf');
  const tabs     = document.getElementById('fmodal-tabs');
  const viewTab  = document.getElementById('fmodal-view-tab');
  const preview  = document.getElementById('fmodal-preview');

  document.getElementById('fmodal-title').textContent = path.split(/[\\/]/).pop();
  document.getElementById('fmodal-ext').textContent   = ext.toUpperCase();
  pre.textContent = 'Loading…';
  pre.className   = '';
  pre.style.display = '';
  pdfFrame.style.display = 'none';
  pdfFrame.src = '';
  preview.style.display = 'none';
  preview.innerHTML = '';
  const vlcBtn = document.getElementById('fmodal-vlc-btn');
  if (vlcBtn) vlcBtn.style.display = 'none';
  document.getElementById('fmodal-overlay').style.display = 'flex';
  document.body.style.overflow = 'hidden';

  tabs.style.display = 'flex';
  if (viewTab) viewTab.style.display = hasViewTab ? '' : 'none';
  switchFModalTab('text');
  const infoP = _fmodalPathInfo(path);           // shows absolute path + found / not found
  const viewable = info => !info || (info.exists && info.is_file && info.allowed);
  if (isPdf || isHtml) {
    infoP.then(info => {
      if (!viewable(info)) {                     // explain instead of an iframe saying "Not found"
        _fmodalViewMode = 'message';
        preview.innerHTML = _fmodalProblemHtml(info, path);
        if (document.querySelector('.fmodal-tab.active')?.dataset.tab === 'view') switchFModalTab('view');
      } else {
        pdfFrame.src = serveUrl(path);
      }
    });
  }

  try {
    const r = await fetch(`${apiUrl()}/api/file-text?path=${encodeURIComponent(path)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const content = unescapeContent(d.content || '(empty)');

    if (isSrt) {
      _renderSrtContent(pre, content, srtTime ?? null);
    } else if (_fmodalChunkText) {
      _highlightChunk(pre, content, _fmodalChunkText, ext);
    } else {
      const hljsLang = EXT_TO_HLJS[ext];
      if (typeof hljs !== 'undefined' && hljsLang) {
        pre.innerHTML = `<code class="language-${hljsLang}">${escHtml(content)}</code>`;
        hljs.highlightElement(pre.querySelector('code'));
      } else if (typeof hljs !== 'undefined' && ext && !(ext in EXT_TO_HLJS)) {
        pre.innerHTML = `<code>${escHtml(content)}</code>`;
        hljs.highlightElement(pre.querySelector('code'));
      } else {
        pre.textContent = content;
      }
    }

    if (isMd) {
      _renderMdInIframe(preview, content, path, apiUrl());
    } else if (isImg) {
      preview.innerHTML = `<img src="${serveUrl(path)}" alt="${escHtml(path.split(/[\\/]/).pop())}" style="max-width:100%;border-radius:4px;">`;
    } else if (isSrt) {
      _renderSrtVideo(preview, path, srtTime ?? 0, content);
    } else if (isCsv) {
      _renderCsvTable(preview, content);
    }
  } catch(e) {
    // Say WHY and WHERE instead of a bare "HTTP 404"
    const info = await infoP;
    pre.textContent = 'Error loading file: ' + e.message +
      '\n\nLooked up at:\n  ' + path +
      (info && info.reason ? '\n\n' + info.reason : '');
  }
}

function switchFModalTab(tab) {
  const body    = document.getElementById('fmodal-body');
  const pre     = document.getElementById('fmodal-pre');
  const pdf     = document.getElementById('fmodal-pdf');
  const preview = document.getElementById('fmodal-preview');
  document.querySelectorAll('.fmodal-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  if (tab === 'text') {
    body.classList.remove('view-mode');
    pre.style.display = '';
    pdf.style.display = 'none';
    preview.style.display = 'none';
  } else {
    body.classList.add('view-mode');
    pre.style.display = 'none';
    if (_fmodalViewMode === 'iframe') {
      pdf.style.display = 'block';
      preview.style.display = 'none';
    } else {
      pdf.style.display = 'none';
      preview.style.display = 'flex';
    }
  }
}

function toggleFModalMaximize() {
  const modal = document.querySelector('#fmodal-overlay .fmodal');
  const btn   = document.getElementById('fmodal-max-btn');
  modal.classList.toggle('maximized');
  btn.textContent = modal.classList.contains('maximized') ? '⧉' : '⛶';
  btn.title       = modal.classList.contains('maximized') ? 'Restore' : 'Maximize';
}

function closeFModal() {
  const modal = document.querySelector('#fmodal-overlay .fmodal');
  if (modal) modal.classList.remove('maximized');
  document.getElementById('fmodal-max-btn').textContent = '⛶';
  document.getElementById('fmodal-overlay').style.display = 'none';
  document.getElementById('fmodal-pdf').src = '';
  const prev = document.getElementById('fmodal-preview');
  if (prev) { prev.innerHTML = ''; prev.style.display = 'none'; }
  document.body.style.overflow = '';
  document.getElementById('fmodal-body')?.classList.remove('view-mode');
  if (_fmodalSrtVideoEl) { try { _fmodalSrtVideoEl.pause(); _fmodalSrtVideoEl.src = ''; } catch(e) {} }
  _fmodalSrtPath = null; _fmodalSrtTime = null; _fmodalChunkText = ''; _fmodalSrtVideoEl = null;
}

// ── SRT style settings (localStorage) ─────────────────────────────────────────
const _SRT_SK = 'cc_srt_style';
const _SRT_DEF = { show:true, color:'#ffffff', bgColor:'#000000', bgOpacity:0.72, size:18, font:'sans-serif', posBottom:50, posLeft:50 };
function _getSrtStyle() { try { return {..._SRT_DEF, ...JSON.parse(localStorage.getItem(_SRT_SK)||'{}')}; } catch { return {..._SRT_DEF}; } }
function _saveSrtStyle(u) { const s=_getSrtStyle(); localStorage.setItem(_SRT_SK, JSON.stringify({...s,...u})); }
function _hexToRgb(h) { const m=/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(h); return m?`${parseInt(m[1],16)},${parseInt(m[2],16)},${parseInt(m[3],16)}`:null; }

function _applySrtStyle() {
  const o = document.getElementById('srt-sub-overlay');
  if (!o) return;
  const s = _getSrtStyle();
  const bg = _hexToRgb(s.bgColor);
  o.style.color       = s.color;
  o.style.fontSize    = s.size + 'px';
  o.style.fontFamily  = s.font;
  o.style.background  = bg ? `rgba(${bg},${s.bgOpacity})` : `rgba(0,0,0,${s.bgOpacity})`;
  o.style.bottom      = (s.posBottom ?? 50) + 'px';
  o.style.left        = (s.posLeft ?? 50) + '%';
  o.style.transform   = 'translateX(-50%)';
  o.style.display     = s.show !== false ? '' : 'none';
}

function _moveSrtPos(dx, dy) {
  const s = _getSrtStyle();
  _saveSrtStyle({ posLeft: Math.max(10, Math.min(90, (s.posLeft??50)+dx)), posBottom: Math.max(5, Math.min(300, (s.posBottom??50)+dy)) });
  _applySrtStyle();
}
function _adjustSrtSize(delta) {
  const s = _getSrtStyle();
  const ns = Math.max(10, Math.min(52, (s.size??18)+delta));
  _saveSrtStyle({size: ns});
  _applySrtStyle();
  const lbl = document.getElementById('srt-size-lbl'); if (lbl) lbl.textContent = ns + 'px';
}

// ── SRT viewer helpers ─────────────────────────────────────────────────────────
function _srtTimeToSec(t) {
  const m = t.match(/(\d+):(\d+):(\d+)[,.](\d+)/);
  if (!m) return null;
  return parseInt(m[1])*3600 + parseInt(m[2])*60 + parseInt(m[3]) + parseInt(m[4])/1000;
}

function _parseSrtEntries(content) {
  const entries = [];
  for (const block of content.split(/\n\n+/).filter(b => b.trim())) {
    const lines = block.split('\n').filter(l => l.trim());
    let ti = -1, s0 = null, s1 = null;
    for (let i = 0; i < Math.min(lines.length, 3); i++) {
      if (lines[i].includes('-->')) { ti = i; const p = lines[i].split('-->'); s0 = _srtTimeToSec(p[0]); s1 = _srtTimeToSec(p[1]||''); break; }
    }
    if (s0 == null) continue;
    const text = lines.slice(ti + 1).join('\n').trim();
    if (text) entries.push({ startSec: s0, endSec: s1 ?? s0 + 3, text });
  }
  return entries;
}

function _renderSrtContent(pre, content, targetTime) {
  pre.style.whiteSpace = 'normal';
  const blocks = content.split(/\n\n+/).filter(b => b.trim());
  let html = '', scrollId = null;

  for (let bi = 0; bi < blocks.length; bi++) {
    const lines = blocks[bi].split('\n').filter(l => l.trim());
    let timeLine = -1, startSec = null, endSec = null;
    for (let i = 0; i < Math.min(lines.length, 3); i++) {
      if (lines[i].includes('-->')) {
        timeLine = i;
        const parts = lines[i].split('-->');
        startSec = _srtTimeToSec(parts[0]);
        endSec   = _srtTimeToSec(parts[1] || '');
        break;
      }
    }
    const isActive = targetTime != null && startSec != null &&
                     targetTime >= startSec - 0.5 && (endSec == null || targetTime <= endSec + 2);
    const isNear   = !isActive && targetTime != null && startSec != null && Math.abs(startSec - targetTime) < 30;
    const blkId = `sb${bi}`;
    if (isActive && !scrollId) scrollId = blkId;
    html += `<div class="srt-block${isActive?' srt-active':isNear?' srt-near':''}" id="${blkId}" data-start="${startSec??''}">`;
    for (let li = 0; li < lines.length; li++) {
      if (li === timeLine) {
        const p = lines[li].split('-->');
        const st = p[0].trim(), et = (p[1]||'').trim().split(/\s/)[0];
        html += `<div class="srt-timebar">`;
        html += `<span class="srt-ts srt-ts-s" onclick="fmodalSrtSeek(${startSec??0})" title="${formatTime(startSec)}">${escHtml(st)}</span>`;
        html += `<span class="srt-arrow"> → </span>`;
        html += `<span class="srt-ts srt-ts-e" onclick="fmodalSrtSeek(${endSec??0})" title="${formatTime(endSec)}">${escHtml(et)}</span>`;
        html += `</div>`;
      } else if (timeLine < 0 ? li === 0 : li < timeLine) {
        html += `<span class="srt-idx">${escHtml(lines[li])}</span>`;
      } else {
        html += `<em class="srt-txt">${escHtml(lines[li])}</em>`;
      }
    }
    html += '</div>';
  }
  pre.innerHTML = html;
  if (scrollId) requestAnimationFrame(() => {
    const el = document.getElementById(scrollId);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

function _renderSrtVideo(preview, srtPath, startTime, srtContent) {
  const s = _getSrtStyle();
  const entries = srtContent ? _parseSrtEntries(srtContent) : [];

  const fontOpts = [['sans-serif','Sans'],['serif','Serif'],['monospace','Mono'],["'Arial',sans-serif",'Arial'],["'Times New Roman',serif",'Times']];
  const fontSel = fontOpts.map(([v,l]) => `<option value="${v}" ${s.font===v?'selected':''}>${l}</option>`).join('');

  preview.style.cssText = 'display:flex;flex-direction:column;height:100%;';
  preview.innerHTML = `
    <div class="srt-ctrl-bar">
      <label class="srt-ctrl-item" title="Show/hide subtitles">
        <input type="checkbox" id="srt-sub-on" ${s.show?'checked':''} onchange="_saveSrtStyle({show:this.checked});_applySrtStyle()">
        <span>Sub</span>
      </label>
      <span class="srt-ctrl-sep"></span>
      <label class="srt-ctrl-item" title="Text color"><span>A</span>
        <input type="color" value="${s.color}" style="width:20px;height:18px;padding:0;border:none;cursor:pointer;border-radius:3px;background:none;"
               onchange="_saveSrtStyle({color:this.value});_applySrtStyle()">
      </label>
      <label class="srt-ctrl-item" title="Background color"><span>BG</span>
        <input type="color" value="${s.bgColor}" style="width:20px;height:18px;padding:0;border:none;cursor:pointer;border-radius:3px;background:none;"
               onchange="_saveSrtStyle({bgColor:this.value});_applySrtStyle()">
        <input type="range" min="0" max="1" step="0.05" value="${s.bgOpacity}" title="Background opacity" style="width:46px;"
               oninput="_saveSrtStyle({bgOpacity:parseFloat(this.value)});_applySrtStyle()">
      </label>
      <span class="srt-ctrl-sep"></span>
      <label class="srt-ctrl-item" title="Font size">
        <button class="srt-ctrl-btn" onclick="_adjustSrtSize(-2)">A−</button>
        <span id="srt-size-lbl" style="min-width:26px;text-align:center;">${s.size}px</span>
        <button class="srt-ctrl-btn" onclick="_adjustSrtSize(2)">A+</button>
      </label>
      <select class="srt-ctrl-sel" title="Font family" onchange="_saveSrtStyle({font:this.value});_applySrtStyle()">${fontSel}</select>
      <span class="srt-ctrl-sep"></span>
      <label class="srt-ctrl-item" title="Position">
        <button class="srt-ctrl-btn" onclick="_moveSrtPos(0,8)">↑</button>
        <button class="srt-ctrl-btn" onclick="_moveSrtPos(0,-8)">↓</button>
        <button class="srt-ctrl-btn" onclick="_moveSrtPos(-5,0)">←</button>
        <button class="srt-ctrl-btn" onclick="_moveSrtPos(5,0)">→</button>
      </label>
      <span class="srt-ctrl-sep"></span>
      <label class="srt-ctrl-item" title="When ON, clicking a time in the Text tab seeks the video">
        <input type="checkbox" id="srt-sync-chk"> <span>Sync</span>
      </label>
    </div>
    <div id="srt-video-area" style="flex:1;min-height:0;background:#111;border-radius:0 0 6px 6px;overflow:hidden;position:relative;display:flex;align-items:center;justify-content:center;">
      <span id="srt-video-msg" style="color:#888;font-size:12px;padding:16px;">Loading video…</span>
    </div>`;

  fetch(`${apiUrl()}/api/find-video?srt=${encodeURIComponent(srtPath)}`)
    .then(r => r.json())
    .then(d => {
      const area = document.getElementById('srt-video-area');
      if (!area) return;
      if (d.type === 'youtube') {
        const t = startTime ? `&start=${Math.floor(startTime)}` : '';
        area.innerHTML = `<iframe src="https://www.youtube.com/embed/${escHtml(d.youtube_id)}?autoplay=1&rel=0${t}"
          style="width:100%;height:100%;border:none;" allowfullscreen></iframe>`;
      } else if (d.type === 'local') {
        area.innerHTML = `<video id="srt-vid" controls style="width:100%;height:100%;display:block;object-fit:contain;"></video>`;
        const vid = document.getElementById('srt-vid');
        _fmodalSrtVideoEl = vid;
        vid.src = `${apiUrl()}/api/stream-video?path=${encodeURIComponent(d.video_path)}`;
        vid.addEventListener('loadedmetadata', () => { vid.currentTime = startTime ?? 0; }, { once: true });
        // Subtitle overlay
        if (entries.length) {
          const ov = document.createElement('div');
          ov.id = 'srt-sub-overlay';
          ov.style.cssText = 'position:absolute;z-index:10;text-align:center;pointer-events:none;white-space:pre-wrap;word-break:break-word;max-width:80%;border-radius:4px;padding:4px 12px;';
          area.appendChild(ov);
          _applySrtStyle();
          vid.addEventListener('timeupdate', () => {
            const sub = document.getElementById('srt-sub-overlay'); if (!sub) return;
            const t = vid.currentTime;
            const e = entries.find(e => t >= e.startSec && t <= e.endSec);
            sub.textContent = e ? e.text : '';
          });
        }
      } else {
        area.innerHTML = `<span style="color:#888;font-size:12px;padding:16px;">${escHtml(d.message||'No video found for this file.')}</span>`;
      }
    })
    .catch(e => { const m = document.getElementById('srt-video-msg'); if (m) m.textContent = 'Error: ' + e.message; });
}

function fmodalSrtSeek(time) {
  document.querySelectorAll('.srt-block').forEach(b => {
    const bs = parseFloat(b.dataset.start);
    b.classList.toggle('srt-active', !isNaN(bs) && Math.abs(bs - time) < 0.5);
    b.classList.toggle('srt-near',   !isNaN(bs) && Math.abs(bs - time) < 30 && Math.abs(bs - time) >= 0.5);
  });
  _fmodalSrtTime = time;
  // Switch to View tab and seek video
  const viewTab = document.getElementById('fmodal-view-tab');
  if (viewTab && viewTab.style.display !== 'none') {
    switchFModalTab('view');
    setTimeout(() => {
      if (_fmodalSrtVideoEl) { _fmodalSrtVideoEl.currentTime = time; _fmodalSrtVideoEl.play().catch(()=>{}); }
    }, 50);
  } else {
    const syncChk = document.getElementById('srt-sync-chk');
    if (syncChk?.checked && _fmodalSrtVideoEl) {
      _fmodalSrtVideoEl.currentTime = time;
      if (_fmodalSrtVideoEl.paused) _fmodalSrtVideoEl.play().catch(() => {});
    }
  }
}

// ── Direct video playback (for time badge clicks in search results) ─────────────
async function playVideoAtTime(srtPath, time) {
  try {
    const r = await fetch(`${apiUrl()}/api/play?srt=${encodeURIComponent(srtPath)}&time=${time}`);
    const d = await r.json();
    if (d.status === 'youtube_viewer' && d.url) { openYTModal(d.url); }
    else if (d.status === 'video_viewer' && d.video) { openVideoModal(d.video, d.time); }
  } catch(e) { /* silent — VLC launched or error */ }
}

// ── CSV table viewer ───────────────────────────────────────────────────────────
function _renderCsvTable(preview, content) {
  const lines = content.split('\n').filter(l => l.trim());
  if (!lines.length) { preview.innerHTML = '<em style="color:var(--muted);font-size:12px;padding:8px;">Empty file</em>'; return; }
  const parseRow = line => {
    const cells = []; let cur = '', inQ = false;
    for (const ch of line) {
      if (ch === '"') inQ = !inQ;
      else if (ch === ',' && !inQ) { cells.push(cur.trim()); cur = ''; }
      else cur += ch;
    }
    cells.push(cur.trim());
    return cells;
  };
  const rows = lines.map(parseRow);
  const cols = Math.max(...rows.map(r => r.length));
  let html = '<div style="overflow:auto;height:100%;padding:6px;"><table class="csv-table"><thead><tr>';
  for (let c = 0; c < cols; c++) html += `<th>${escHtml(rows[0]?.[c] ?? '')}</th>`;
  html += '</tr></thead><tbody>';
  for (let r = 1; r < rows.length; r++) {
    html += '<tr>';
    for (let c = 0; c < cols; c++) html += `<td>${escHtml(rows[r]?.[c] ?? '')}</td>`;
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  preview.innerHTML = html;
}

async function fmodalPlayVlc() {
  if (!_fmodalSrtPath) return;
  const time = _fmodalSrtTime ?? 0;
  const r = await fetch(`${apiUrl()}/api/play?srt=${encodeURIComponent(_fmodalSrtPath)}&time=${time}`);
  const d = await r.json();
  if (d.status === 'youtube_viewer' && d.url) {
    closeFModal(); openYTModal(d.url);
  } else if (d.status === 'video_viewer' && d.video) {
    closeFModal(); openVideoModal(d.video, d.time);
  } else {
    document.getElementById('fmodal-pre').textContent =
      d.error ? `VLC error: ${d.error}` : `VLC opened: ${d.video || d.youtube_id} @ ${formatTime(d.time)}`;
  }
}

// ── Video modals ──────────────────────────────────────────────────────────────
function openVideoModal(path, time) {
  const url = `${apiUrl()}/api/stream-video?path=${encodeURIComponent(path)}`;
  const vid = document.getElementById('vmodal-video');
  vid.src = url;
  vid.addEventListener('loadedmetadata', () => { vid.currentTime = time || 0; }, { once: true });
  document.getElementById('vmodal-overlay').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}
function closeVideoModal() {
  const vid = document.getElementById('vmodal-video');
  vid.pause(); vid.src = '';
  document.getElementById('vmodal-overlay').style.display = 'none';
  document.body.style.overflow = '';
}
function openYTModal(url) {
  document.getElementById('ytmodal-iframe').src = url;
  document.getElementById('ytmodal-overlay').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}
function closeYTModal() {
  document.getElementById('ytmodal-overlay').style.display = 'none';
  document.getElementById('ytmodal-iframe').src = '';
  document.body.style.overflow = '';
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeFModal(); closeYTModal(); closeVideoModal(); }
});

// ── Theme ─────────────────────────────────────────────────────────────────────
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = theme === 'light' ? '🌙' : '☀';
  const dark  = document.getElementById('hljs-theme-dark');
  const light = document.getElementById('hljs-theme-light');
  if (dark)  dark.disabled  = (theme === 'light');
  if (light) light.disabled = (theme !== 'light');
}
function toggleTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  localStorage.setItem('cc_theme', next);
  applyTheme(next);
}

// ── Search history (generic) ──────────────────────────────────────────────────
const _HISTORY_MAX = 10;

function _getHistory(key) {
  try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch { return []; }
}
function _saveHistory(key, items) {
  localStorage.setItem(key, JSON.stringify(items.slice(0, _HISTORY_MAX)));
}
function pushHistory(storageKey, query) {
  if (!query.trim()) return;
  let h = _getHistory(storageKey);
  h = [query, ...h.filter(x => x !== query)];
  _saveHistory(storageKey, h);
}
function showHistory(inputId, listId) {
  const key  = inputId === 'q' ? 'cc_hist_search' : 'cc_hist_ask';
  const h    = _getHistory(key);
  const list = document.getElementById(listId);
  if (!list || !h.length) return;
  list.innerHTML = '';
  h.forEach((item, i) => {
    const div = document.createElement('div');
    div.className = 'history-item';
    div.innerHTML = `<span class="hi-icon">↺</span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(item)}</span>
      <span class="hi-del" title="Remove">✕</span>`;
    div.onmousedown = e => {
      e.preventDefault();
      document.getElementById(inputId).value = item;
      hideHistory(listId);
      if (inputId === 'q') doSearch(); else doAsk();
    };
    div.querySelector('.hi-del').onmousedown = e => {
      e.stopPropagation(); e.preventDefault();
      const arr = _getHistory(key); arr.splice(i, 1);
      _saveHistory(key, arr);
      showHistory(inputId, listId);
    };
    list.appendChild(div);
  });
  list.classList.add('open');
}
function hideHistory(listId) {
  const el = document.getElementById(listId);
  if (el) el.classList.remove('open');
}
