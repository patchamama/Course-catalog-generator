#!/usr/bin/env node
/**
 * translate-prepare.mjs — prepare a Markdown document for (machine) translation, and put it back together.
 *
 * Usage:
 *   node translate-prepare.mjs <file.md>                      prepare
 *   node translate-prepare.mjs <file.md> --restore <translated.json>   rebuild the document
 *
 * PREPARE writes next to the document:
 *   <name>.segments.json   [{ "id": 3, "text": "…translatable…", "translation": "" }, …]
 *   <name>.skeleton.md     the document with every segment replaced by {{SEG:3}} (code, URLs, HTML… untouched)
 * Translate the "text" of each segment (an LLM, DeepL, a translator…), put the result in "translation",
 * then run RESTORE: <name>.translated.md = skeleton with every {{SEG:n}} replaced by its translation
 * (a segment without translation keeps the original text).
 *
 * Never sent for translation: fenced / indented code, inline code, URLs, image paths, HTML tags, front matter,
 * link targets. Only the readable text of paragraphs, headings, list items, table cells and block quotes is.
 * Needs Node.js 18+, no packages.
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const FENCE = /^\s{0,3}(`{3,}|~{3,})/

/** Split a line into [{translate:boolean, text}] — protects code spans, links targets, URLs, HTML tags. */
function splitInline(line) {
  const tokens = []
  const re = /(`[^`]*`|!?\[[^\]]*\]\([^)]*\)|<[^>]+>|https?:\/\/[^\s)>\]]+|\{\{SEG:\d+\}\})/g
  let last = 0
  for (const m of line.matchAll(re)) {
    if (m.index > last) tokens.push({ translate: true, text: line.slice(last, m.index) })
    const t = m[0]
    const link = t.match(/^(!?)\[([^\]]*)\]\(([^)]*)\)$/)
    if (link && !link[1] && link[2].trim()) {           // [label](target): label is text, target is not
      tokens.push({ translate: false, text: '[' }, { translate: true, text: link[2] }, { translate: false, text: `](${link[3]})` })
    } else tokens.push({ translate: false, text: t })
    last = m.index + t.length
  }
  if (last < line.length) tokens.push({ translate: true, text: line.slice(last) })
  return tokens
}

export function prepare(markdown) {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n')
  const segments = []
  const skeleton = []
  let fence = null, i = 0
  if (lines[0] === '---') {                              // YAML front matter stays untouched
    const end = lines.indexOf('---', 1)
    if (end > 0) { skeleton.push(...lines.slice(0, end + 1)); i = end + 1 }
  }
  for (; i < lines.length; i++) {
    const line = lines[i]
    const fm = line.match(FENCE)
    if (fence) { skeleton.push(line); if (fm && fm[1][0] === fence[0] && fm[1].length >= fence.length) fence = null; continue }
    if (fm) { fence = fm[1]; skeleton.push(line); continue }
    if (/^( {4}|\t)/.test(line) || /^\s*$/.test(line) || /^\s*([-*_]\s*){3,}$/.test(line) || /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes('-')) {
      skeleton.push(line); continue                        // indented code, blank line, rule, table separator
    }
    // keep the structural prefix (#, >, list marker) out of the translatable text
    const prefix = (line.match(/^(\s*(?:#{1,6}\s+|>\s*|(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)*)/) || [''])[0]
    const body = line.slice(prefix.length)
    let rebuilt = prefix
    for (const tok of splitInline(body)) {
      if (!tok.translate || !/[\p{L}]/u.test(tok.text)) { rebuilt += tok.text; continue }
      const lead = tok.text.match(/^\s*/)[0], trail = tok.text.match(/\s*$/)[0]
      const core = tok.text.trim()
      if (!core) { rebuilt += tok.text; continue }
      const id = segments.length + 1
      segments.push({ id, text: core, translation: '' })
      rebuilt += `${lead}{{SEG:${id}}}${trail}`
    }
    skeleton.push(rebuilt)
  }
  return { segments, skeleton: skeleton.join('\n') }
}

export function restore(skeleton, segments) {
  const byId = new Map(segments.map(s => [String(s.id), s]))
  return skeleton.replace(/\{\{SEG:(\d+)\}\}/g, (m, id) => {
    const s = byId.get(id)
    return s ? (s.translation && s.translation.trim() ? s.translation.trim() : s.text) : m
  })
}

function main() {
  const args = process.argv.slice(2)
  const restoreAt = args.indexOf('--restore')
  const restoreArg = restoreAt >= 0 ? args[restoreAt + 1] : null          // value of --restore, not the document
  const file = args.find(a => !a.startsWith('-') && a !== restoreArg)
  if (!file || args.includes('--help')) { console.log('Usage: node translate-prepare.mjs <file.md> [--restore <segments.json>]'); process.exit(file ? 0 : 2) }
  const path = resolve(file)
  if (!existsSync(path)) { console.error(`Error: file not found: ${path}`); process.exit(2) }
  const stem = join(dirname(path), basename(path).replace(/\.md$/i, ''))

  const ri = args.indexOf('--restore')
  if (ri >= 0) {
    const jsonPath = resolve(args[ri + 1] || `${stem}.segments.json`)
    const skelPath = `${stem}.skeleton.md`
    if (!existsSync(jsonPath) || !existsSync(skelPath)) { console.error(`Error: need ${skelPath} and ${jsonPath}`); process.exit(2) }
    const segments = JSON.parse(readFileSync(jsonPath, 'utf8'))
    const translated = segments.filter(s => s.translation && s.translation.trim()).length
    writeFileSync(`${stem}.translated.md`, restore(readFileSync(skelPath, 'utf8'), segments), 'utf8')
    console.log(`Restored ${basename(stem)}.translated.md — ${translated}/${segments.length} segments translated${translated < segments.length ? ' (the rest keep the original text)' : ''}.`)
    return
  }
  const { segments, skeleton } = prepare(readFileSync(path, 'utf8'))
  writeFileSync(`${stem}.segments.json`, JSON.stringify(segments, null, 2), 'utf8')
  writeFileSync(`${stem}.skeleton.md`, skeleton, 'utf8')            // exact: restore() must return the original
  const words = segments.reduce((n, s) => n + s.text.split(/\s+/).length, 0)
  console.log(`${segments.length} translatable segment(s), ~${words} words.`)
  console.log(`Wrote ${basename(stem)}.segments.json and ${basename(stem)}.skeleton.md`)
  console.log('Next: fill the "translation" fields, then run again with --restore.')
}

if (process.argv[1] === fileURLToPath(import.meta.url)) main()
