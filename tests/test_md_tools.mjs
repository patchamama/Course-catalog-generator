// Tests for the Node tools: _mdformater, _mdtranslator, _mdImageDownloader.
// Run:  node --test tests/test_md_tools.mjs     (Node 18+, no packages, no network: images come from a local server)
import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdtempSync, writeFileSync, readFileSync, existsSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { spawnSync } from 'node:child_process'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const imp = rel => import(pathToFileURL(join(ROOT, rel)).href)
const run = (rel, ...args) => spawnSync(process.execPath, [join(ROOT, rel), ...args], { encoding: 'utf8' })
const tmp = () => mkdtempSync(join(tmpdir(), 'mdtools-'))

// ── formatter ───────────────────────────────────────────────────────────────
test('formatter: headings, bullets, blank lines, trailing spaces, CRLF', async () => {
  const { formatMarkdown } = await imp('_mdformater/format-docs.mjs')
  const out = formatMarkdown('#Title\r\ntext   \r\n* a\r\n+ b\r\nafter list\r\n\r\n\r\n\r\nSetext\r\n======\r\nbody')
  assert.equal(out, ['# Title', '', 'text', '', '- a', '- b', '', 'after list', '', '# Setext', '', 'body', ''].join('\n'))
})

test('formatter: never touches fenced code', async () => {
  const { formatMarkdown } = await imp('_mdformater/format-docs.mjs')
  const code = '```js\n#notaheading   \n* not a bullet\n\n\n\nx = 1\n```'
  const out = formatMarkdown(`para\n${code}\nnext`)
  assert.ok(out.includes(code), 'the code block must be byte-for-byte unchanged')
  assert.match(out, /para\n\n```js/)
  assert.match(out, /```\n\nnext\n$/)
})

test('formatter CLI: preview by default, --auto rewrites in place with a .bak', () => {
  const d = tmp(), f = join(d, 'doc.md')
  writeFileSync(f, '#Hi\n* x\n')
  assert.equal(run('_mdformater/format-docs.mjs', f).status, 0)
  assert.equal(readFileSync(f, 'utf8'), '#Hi\n* x\n', 'original untouched without --auto')
  assert.equal(readFileSync(join(d, 'doc.formatted.md'), 'utf8'), '# Hi\n\n- x\n')
  assert.equal(run('_mdformater/format-docs.mjs', f, '--auto').status, 0)
  assert.equal(readFileSync(f, 'utf8'), '# Hi\n\n- x\n')
  assert.equal(readFileSync(f + '.bak', 'utf8'), '#Hi\n* x\n')
  assert.match(run('_mdformater/format-docs.mjs', f, '--auto').stdout, /already clean/)
})

test('formatter CLI: missing file → exit code 2', () => {
  assert.equal(run('_mdformater/format-docs.mjs', join(tmp(), 'nope.md')).status, 2)
})

// ── translator preparation ──────────────────────────────────────────────────
test('translate-prepare: protects code, URLs and link targets; round-trips', async () => {
  const { prepare, restore } = await imp('_mdtranslator/translate-prepare.mjs')
  const md = ['---', 'title: x', '---', '# Hello world', '', 'See [the docs](https://example.com/a) and `code()` now.', '',
    '```py', 'print("do not translate")', '```', '', '- item one', '| a | b |', '|---|---|', '![alt text](img/a.png)'].join('\n')
  const { segments, skeleton } = prepare(md)
  const texts = segments.map(s => s.text)
  assert.ok(texts.includes('Hello world'))
  assert.ok(texts.includes('the docs'), 'link label is translatable')
  assert.ok(texts.includes('item one'))
  assert.ok(!texts.some(t => t.includes('print(') || t.includes('example.com') || t.includes('code()') || t.includes('img/a.png')), 'code/URLs/paths never extracted')
  assert.ok(skeleton.includes('print("do not translate")') && skeleton.includes('https://example.com/a') && skeleton.includes('title: x'))
  assert.equal(restore(skeleton, segments), md, 'with no translations the document is restored exactly')
  const es = segments.map(s => ({ ...s, translation: s.text === 'Hello world' ? 'Hola mundo' : '' }))
  assert.match(restore(skeleton, es), /^# Hola mundo$/m)
})

test('translate-prepare CLI: prepare then --restore', () => {
  const d = tmp(), f = join(d, 'book.md')
  writeFileSync(f, '# Title\n\nSome text here.\n')
  assert.equal(run('_mdtranslator/translate-prepare.mjs', f).status, 0)
  const segFile = join(d, 'book.segments.json')
  const segs = JSON.parse(readFileSync(segFile, 'utf8'))
  segs.find(s => s.text === 'Title').translation = 'Título'
  writeFileSync(segFile, JSON.stringify(segs))
  const r = run('_mdtranslator/translate-prepare.mjs', f, '--restore', segFile)
  assert.equal(r.status, 0, r.stderr)
  assert.equal(readFileSync(join(d, 'book.translated.md'), 'utf8'), '# Título\n\nSome text here.\n')
})

// ── image downloader ────────────────────────────────────────────────────────
test('image downloader: parse + safe names', async () => {
  const { findImages, safeName, parseArgs } = await imp('_mdImageDownloader/download-images.mjs')
  const found = findImages('![a](https://x.test/p/one.png "t") text <img src="https://x.test/two.jpg" alt="B"> ![local](img/l.png)')
  assert.deepEqual(found.map(f => [f.kind, f.url, f.alt]), [['md', 'https://x.test/p/one.png', 'a'], ['html', 'https://x.test/two.jpg', 'B']])
  assert.equal(safeName('https://x.test/a b/Ünï.png?x=1', 7, 'image/png'), 'img-007-_n_.png')
  assert.match(safeName('https://x.test/download', 1, 'image/webp'), /\.webp$/)
  assert.throws(() => parseArgs(['f.md', '--wat']))
})

test('image downloader CLI: dry-run, download, rewrite, keep-html, failures keep the link', async () => {
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64')
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/ok')) { res.writeHead(200, { 'Content-Type': 'image/png' }); res.end(png) }
    else if (req.url.startsWith('/html')) { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end('<html>') }
    else { res.writeHead(404); res.end() }
  })
  await new Promise(r => server.listen(0, '127.0.0.1', r))
  const base = `http://127.0.0.1:${server.address().port}`
  try {
    const d = tmp(), f = join(d, 'doc.md')
    const original = `![one](${base}/ok/a.png)\n\n<img src="${base}/ok/a.png" alt="again">\n\n![bad](${base}/missing.png)\n![page](${base}/html/x.png)\n`
    writeFileSync(f, original)
    const spawnAsync = (...a) => new Promise(res => { const p = spawnSync(process.execPath, [join(ROOT, '_mdImageDownloader/download-images.mjs'), ...a], { encoding: 'utf8' }); res(p) })

    const dry = await spawnAsync(f, '--dry-run')
    assert.match(dry.stdout, /would download/)
    assert.equal(readFileSync(f, 'utf8'), original, 'dry run changes nothing')
    assert.ok(!existsSync(join(d, 'images')))

    // spawnSync blocks this process' event loop → run the real download in a child that can reach our server
    const { spawn } = await import('node:child_process')
    const child = spawn(process.execPath, [join(ROOT, '_mdImageDownloader/download-images.mjs'), f], { encoding: 'utf8' })
    let out = ''; child.stdout.on('data', c => out += c)
    const code = await new Promise(r => child.on('close', r))
    assert.equal(code, 0, out)
    const text = readFileSync(f, 'utf8')
    assert.match(text, /!\[one\]\(images\/img-001-a\.png\)/, 'markdown image rewritten to the local copy')
    assert.match(text, /!\[again\]\(images\/img-001-a\.png\)/, '<img> converted, same URL → same file (downloaded once)')
    assert.ok(text.includes(`${base}/missing.png`) && text.includes(`${base}/html/x.png`), 'failed / non-image URLs stay untouched')
    assert.equal(readdirSync(join(d, 'images')).length, 1)
    assert.equal(readFileSync(f + '.bak', 'utf8'), original)
    assert.match(out, /FAILED .*missing\.png.*HTTP 404/)
    assert.match(out, /FAILED .*html\/x\.png.*not an image/)
  } finally { server.close() }
})
