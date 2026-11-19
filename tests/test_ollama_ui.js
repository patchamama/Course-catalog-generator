const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/assets/es_test.js', 'utf8');
const start = source.indexOf('async function ollamaDirectAnswer(');
const direct = source.slice(start, source.indexOf('// ── Direct-mode:', start));
const attempts = [];
const context = { AbortSignal, Date, fetch: async (url, opts) => {
  if (url.endsWith('/api/tags')) return {ok: true, json: async () => ({models: [
    {name: 'local'}, {name: 'deepseek-v4.1-flash:cloud'},
    {name: 'glm-5.3-flash:cloud'}, {name: 'kimi-k3.1-pro:cloud'}]})};
  const model = JSON.parse(opts.body).model;
  attempts.push(model);
  return {ok: true, json: async () => model === 'local'
    ? {done: true, message: {content: 'answer'}} : {error: 'quota'}};
}};
vm.createContext(context);
vm.runInContext(direct, context);
(async () => {
  const result = await context.ollamaDirectAnswer('http://localhost:11434', 'local', []);
  assert.equal(result.model, 'local');
  assert.equal(result.text, 'answer');
  assert.deepEqual(attempts, ['deepseek-v4.1-flash:cloud', 'glm-5.3-flash:cloud', 'kimi-k3.1-pro:cloud', 'local']);
  console.log('Direct-mode cloud rotation and local fallback: PASS');
  const box = {hidden: true, children: [], style: {}, replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); }};
  const input = {value: 'llama', focus() { this.focused = true; }};
  const makeEl = () => ({style: {}, children: [], append(c) { this.children.push(c); }});
  context.document = {
    // the "☁ cloud only" checkbox is not ticked here
    getElementById: id => id === 'tool-ollama-catalog' ? box : id === 'tool-ollama-cloud' ? {checked: false} : input,
    createElement: makeEl, createTextNode: text => ({textContent: text})
  };
  context.apiUrl = () => 'http://backend.test';
  context.fetch = async url => {
    assert.ok(url.endsWith('?q=llama'));
    return {ok: true, json: async () => ({models: [{name: 'llama:cloud', cloud: true, sizes: ['8b', '70b']}]})};
  };
  const catalogStart = source.indexOf('let ollamaCatalogRequest = 0;');
  vm.runInContext(source.slice(catalogStart, source.indexOf('async function runOllamaPull()', catalogStart)), context);
  await context.browseOllamaModels();
  assert.equal(box.hidden, false);
  // compact chip: "☁ name" plus a muted "8b · 70b" span; the tooltip carries the approximate download size
  assert.equal(box.children[1].textContent, '☁ llama:cloud');
  assert.equal(box.children[1].children[0].textContent, '8b · 70b');
  assert.match(box.children[1].title, /approx\. 4\.8 GB \/ 42 GB/);
  box.children[1].onclick();
  assert.equal(input.value, 'llama:cloud');
  assert.equal(box.hidden, true);
  assert.equal(input.focused, true);
  console.log('Catalog search and compact selection without installation: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
