#!/usr/bin/env node
/**
 * validate_js.js — Extract <script> blocks from a generated HTML file and
 * check them for syntax errors using the V8 engine built into Node.js.
 *
 * Usage:
 *   node validate_js.js [html_file]          (default: course_catalog.html)
 *   node validate_js.js course_catalog.html
 */
'use strict';

const fs   = require('fs');
const vm   = require('vm');
const path = require('path');

const htmlFile = process.argv[2] || path.join(__dirname, 'course_catalog.html');

if (!fs.existsSync(htmlFile)) {
  console.error(`File not found: ${htmlFile}`);
  process.exit(1);
}

const html = fs.readFileSync(htmlFile, 'utf8');

// Extract all inline <script>...</script> blocks (ignore src= references)
const scriptRe = /<script(?![^>]*\bsrc\b)[^>]*>([\s\S]*?)<\/script>/gi;
const blocks = [];
let m;
while ((m = scriptRe.exec(html)) !== null) {
  blocks.push({ code: m[1], offset: m.index });
}

if (blocks.length === 0) {
  console.log('No inline <script> blocks found.');
  process.exit(0);
}

let errors = 0;

blocks.forEach((block, idx) => {
  const label = `<script> block #${idx + 1}`;
  try {
    new vm.Script(block.code, { filename: htmlFile });
    console.log(`  ✓  ${label}`);
  } catch (e) {
    errors++;
    // Compute approximate line number in the HTML file
    const htmlLine = html.slice(0, block.offset).split('\n').length + (e.lineNumber || 1) - 1;
    console.error(`  ✗  ${label}`);
    console.error(`     ${e.message}`);
    if (e.lineNumber) {
      const jsLines = block.code.split('\n');
      const errLine = jsLines[e.lineNumber - 1] || '';
      console.error(`     JS line ${e.lineNumber} (≈ HTML line ${htmlLine}): ${errLine.trim()}`);
    }
  }
});

console.log(`\n${errors === 0 ? '✅ All JS blocks valid.' : `❌ ${errors} error(s) found.`}`);
process.exit(errors > 0 ? 1 : 0);
