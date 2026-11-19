#!/usr/bin/env node
/**
 * download-images.mjs — download the remote images of a Markdown file and point it at local copies.
 *
 * Usage:
 *   node download-images.mjs <file.md> [--out <dir>] [--keep-html] [--dry-run]
 *
 *   --out <dir>    where images are saved (default: "images" next to the Markdown file)
 *   --keep-html    leave <img> tags as HTML (only their src is rewritten); default: convert to ![alt](path)
 *   --dry-run      list what would be downloaded; change nothing
 *
 * Handles ![alt](https://…) and <img src="https://…">. The same URL is downloaded once. The original
 * file is saved as <file>.bak before it is rewritten. Needs Node.js 18+ (built-in fetch), no packages.
 * Limits: 30 s and 25 MB per image; only http(s) URLs whose response is an image.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync, copyFileSync } from 'node:fs'
import { basename, dirname, extname, join, relative, resolve } from 'node:path'

const MAX_BYTES = 25 * 1024 * 1024
const TIMEOUT_MS = 30_000
const TYPE_EXT = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp', 'image/svg+xml': '.svg', 'image/avif': '.avif', 'image/bmp': '.bmp' }

export function parseArgs(argv) {
  const a = { file: null, out: null, keepHtml: false, dryRun: false, help: false }
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i]
    if (t === '--out') a.out = argv[++i]
    else if (t === '--keep-html') a.keepHtml = true
    else if (t === '--dry-run') a.dryRun = true
    else if (t === '--help' || t === '-h') a.help = true
    else if (!t.startsWith('-') && !a.file) a.file = t
    else throw new Error(`Unknown or misplaced argument: ${t}`)
  }
  return a
}

/** Every remote image reference in the text: [{kind:'md'|'html', match, url, alt}] (document order). */
export function findImages(text) {
  const found = []
  for (const m of text.matchAll(/!\[([^\]]*)\]\(\s*<?(https?:\/\/[^\s)>]+)>?(?:\s+"[^"]*")?\s*\)/g))
    found.push({ kind: 'md', match: m[0], url: m[2], alt: m[1], index: m.index })
  for (const m of text.matchAll(/<img\b[^>]*?\bsrc\s*=\s*["'](https?:\/\/[^"']+)["'][^>]*>/gi)) {
    const alt = (m[0].match(/\balt\s*=\s*["']([^"']*)["']/i) || [])[1] || ''
    found.push({ kind: 'html', match: m[0], url: m[1], alt, index: m.index })
  }
  return found.sort((x, y) => x.index - y.index)
}

export function safeName(url, n, contentType) {
  let base = ''
  try { base = decodeURIComponent(basename(new URL(url).pathname)) } catch { /* keep empty */ }
  base = base.replace(/[^\w.\-]+/g, '_').replace(/^\.+/, '').slice(0, 60)
  let ext = extname(base).toLowerCase()
  const fromType = TYPE_EXT[(contentType || '').split(';')[0].trim().toLowerCase()]
  if (!ext || ext.length > 5) { base = base.replace(/\.[^.]*$/, '') ; ext = fromType || '.img' }
  else base = base.slice(0, -ext.length)
  return `img-${String(n).padStart(3, '0')}-${base || 'image'}${ext}`
}

async function download(url) {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS)
  try {
    const res = await fetch(url, { signal: ctl.signal, redirect: 'follow', headers: { 'User-Agent': 'course-catalog-md-image-downloader/1.0' } })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const type = res.headers.get('content-type') || ''
    if (!/^image\//i.test(type)) throw new Error(`not an image (${type || 'no content-type'})`)
    const len = Number(res.headers.get('content-length') || 0)
    if (len > MAX_BYTES) throw new Error(`too large (${(len / 1048576).toFixed(1)} MB)`)
    const buf = Buffer.from(await res.arrayBuffer())
    if (buf.length > MAX_BYTES) throw new Error('too large')
    return { buf, type }
  } finally { clearTimeout(timer) }
}

async function main() {
  let a
  try { a = parseArgs(process.argv.slice(2)) } catch (e) { console.error(e.message); process.exit(2) }
  if (a.help || !a.file) {
    console.log('Usage: node download-images.mjs <file.md> [--out <dir>] [--keep-html] [--dry-run]')
    process.exit(a.help ? 0 : 2)
  }
  const file = resolve(a.file)
  if (!existsSync(file)) { console.error(`Error: file not found: ${file}`); process.exit(2) }
  const outDir = resolve(a.out || join(dirname(file), 'images'))
  const text = readFileSync(file, 'utf8')
  const images = findImages(text)
  const urls = [...new Set(images.map(i => i.url))]
  console.log(`${images.length} remote image reference(s), ${urls.length} unique URL(s) in ${basename(file)}`)
  if (!urls.length) { console.log('Nothing to download.'); return }
  if (a.dryRun) { urls.forEach((u, i) => console.log(`[${i + 1}/${urls.length}] would download ${u}`)); console.log('Dry run: no files were changed.'); return }

  mkdirSync(outDir, { recursive: true })
  const local = new Map()                       // url → relative path (posix) of the saved file
  let failed = 0
  for (let i = 0; i < urls.length; i++) {
    const url = urls[i]
    try {
      const { buf, type } = await download(url)
      const name = safeName(url, i + 1, type)
      writeFileSync(join(outDir, name), buf)
      local.set(url, relative(dirname(file), join(outDir, name)).split('\\').join('/'))
      console.log(`[${i + 1}/${urls.length}] ${name} (${(buf.length / 1024).toFixed(0)} KB)`)
    } catch (e) {
      failed++
      console.log(`[${i + 1}/${urls.length}] FAILED ${url} — ${e.name === 'AbortError' ? 'timeout' : e.message}`)
    }
  }

  let result = text
  for (const img of [...images].reverse()) {      // from the end, so earlier offsets stay valid
    const path = local.get(img.url)
    if (!path) continue
    let repl
    if (img.kind === 'md') repl = img.match.replace(img.url, path)
    else if (a.keepHtml) repl = img.match.replace(img.url, path)
    else repl = `![${img.alt}](${path})`
    result = result.slice(0, img.index) + repl + result.slice(img.index + img.match.length)
  }
  if (result !== text) {
    copyFileSync(file, file + '.bak')
    writeFileSync(file, result, 'utf8')
    console.log(`Updated ${basename(file)} (backup: ${basename(file)}.bak)`)
  }
  console.log(`Done: ${local.size} downloaded${failed ? `, ${failed} failed (links left unchanged)` : ''}. Images in ${outDir}`)
  if (failed && !local.size) process.exit(1)
}

import { fileURLToPath } from 'node:url'
if (process.argv[1] === fileURLToPath(import.meta.url)) main()
