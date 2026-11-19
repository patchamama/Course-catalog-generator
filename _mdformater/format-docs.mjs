#!/usr/bin/env node
/**
 * format-docs.mjs — normalise a Markdown document.
 *
 * Usage:
 *   node format-docs.mjs <file.md> [--auto] [--images-only]
 *
 *   (default)       write the result to <file>.formatted.md and leave the original untouched (preview)
 *   --auto          non-interactive: rewrite <file> in place (the original is kept as <file>.bak)
 *   --images-only   do not touch the text; just download the remote images through
 *                   ../_mdImageDownloader/download-images.mjs
 *
 * What it fixes (never inside fenced code blocks):
 *   • CRLF → LF, trailing spaces removed, tabs in list indentation → 2 spaces
 *   • "#Heading" → "# Heading"; Setext headings (=== / ---) → "#" / "##"; a blank line around every heading
 *   • bullets "*" and "+" → "-"; a blank line before and after a list; a blank line around fenced code
 *   • runs of blank lines collapsed to one; exactly one newline at the end of the file
 * Needs Node.js 18+, no packages.
 */
import { readFileSync, writeFileSync, existsSync, copyFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, join, resolve, basename } from 'node:path'
import { fileURLToPath } from 'node:url'

const FENCE = /^\s{0,3}(`{3,}|~{3,})/
const LIT = '\u0001'                                // prefix of lines that must stay byte-for-byte (code blocks)
const isBlank = s => s.trim() === ''
const isList = s => /^\s*([-*+]|\d+[.)])\s+/.test(s)
const isHeading = s => /^#{1,6}\s/.test(s)

export function formatMarkdown(input) {
  const src = input.replace(/\r\n?/g, '\n').split('\n')
  const out = []
  let fence = null
  const push = line => out.push(line)
  const ensureBlank = () => { if (out.length && !isBlank(out[out.length - 1])) out.push('') }

  for (let i = 0; i < src.length; i++) {
    let line = src[i]
    const fm = line.match(FENCE)
    if (fence) {                                   // inside a code block: copy verbatim (LIT-marked: nothing below may touch it)
      push(LIT + line)
      if (fm && fm[1][0] === fence[0] && fm[1].length >= fence.length) { fence = null; out.push('\u0000AFTER_FENCE') }
      continue
    }
    if (fm) { ensureBlank(); fence = fm[1]; push(LIT + line.replace(/\s+$/, '')); continue }

    line = line.replace(/[ \t]+$/, '')
    // Setext heading: a text line followed by === or ---
    const next = src[i + 1]
    if (!isBlank(line) && !isList(line) && !isHeading(line) && next !== undefined && /^(=+|-+)\s*$/.test(next) && !isBlank(next)) {
      ensureBlank(); push(`${/^=/.test(next) ? '#' : '##'} ${line.trim()}`); push(''); i++; continue
    }
    line = line.replace(/^(#{1,6})([^\s#])/, '$1 $2')           // "#Title" → "# Title"
    if (isList(line)) {
      line = line.replace(/^(\s*)\t+/, (_, s) => s + '  ').replace(/^(\s*)[*+](\s+)/, '$1-$2')
      if (out.length && !isBlank(out[out.length - 1]) && !isList(out[out.length - 1]) && !/^\s+\S/.test(out[out.length - 1])) ensureBlank()
    } else if (out.length && isList(out[out.length - 1]) && !isBlank(line) && !/^\s+/.test(line)) {
      ensureBlank()                                              // text right after a list item
    }
    if (isHeading(line)) { ensureBlank(); push(line); push(''); continue }
    push(line)
  }
  // resolve the markers, collapse blank runs, single trailing newline
  const cleaned = []
  for (const l of out) {
    if (l === '\u0000AFTER_FENCE') { cleaned.push(''); continue }
    if (l.startsWith(LIT)) { cleaned.push(l); continue }            // code lines are never collapsed
    if (isBlank(l) && cleaned.length && isBlank(cleaned[cleaned.length - 1])) continue
    cleaned.push(l)
  }
  while (cleaned.length && isBlank(cleaned[0])) cleaned.shift()
  while (cleaned.length && isBlank(cleaned[cleaned.length - 1])) cleaned.pop()
  return cleaned.map(l => (l.startsWith(LIT) ? l.slice(1) : l)).join('\n') + '\n'
}

function main() {
  const args = process.argv.slice(2)
  const file = args.find(a => !a.startsWith('-'))
  const auto = args.includes('--auto'), imagesOnly = args.includes('--images-only')
  if (!file || args.includes('--help') || args.includes('-h')) {
    console.log('Usage: node format-docs.mjs <file.md> [--auto] [--images-only]')
    process.exit(file ? 0 : 2)
  }
  const path = resolve(file)
  if (!existsSync(path)) { console.error(`Error: file not found: ${path}`); process.exit(2) }

  if (imagesOnly) {
    const dl = join(dirname(fileURLToPath(import.meta.url)), '..', '_mdImageDownloader', 'download-images.mjs')
    if (!existsSync(dl)) { console.error('Error: _mdImageDownloader/download-images.mjs not found.'); process.exit(2) }
    const r = spawnSync(process.execPath, [dl, path], { stdio: 'inherit' })
    process.exit(r.status ?? 1)
  }

  const original = readFileSync(path, 'utf8')
  const formatted = formatMarkdown(original)
  if (formatted === original) { console.log(`${basename(path)} is already clean — nothing to change.`); return }
  const changed = formatted.split('\n').length - original.replace(/\r\n?/g, '\n').split('\n').length
  if (auto) {
    copyFileSync(path, path + '.bak')
    writeFileSync(path, formatted, 'utf8')
    console.log(`Formatted ${basename(path)} in place (${changed >= 0 ? '+' : ''}${changed} lines; backup: ${basename(path)}.bak)`)
  } else {
    const preview = path.replace(/\.md$/i, '') + '.formatted.md'
    writeFileSync(preview, formatted, 'utf8')
    console.log(`Preview written to ${preview} (${changed >= 0 ? '+' : ''}${changed} lines). Re-run with --auto to rewrite the original.`)
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) main()
