# _mdformater

Normalise a Markdown document. Node.js 18+, no packages.

```bash
node _mdformater/format-docs.mjs FILE.md [--auto] [--images-only]
```

- **Default**: writes `FILE.formatted.md` (a preview) and leaves the original alone.
- **`--auto`**: non-interactive — rewrites `FILE.md` in place and keeps `FILE.md.bak`.
- **`--images-only`**: does not touch the text; downloads the remote images via `_mdImageDownloader`.

Fixes (never inside fenced code): CRLF → LF, trailing spaces, `#Title` → `# Title`, Setext (`===`/`---`) → `#`/`##`,
a blank line around headings / lists / code blocks, `*` and `+` bullets → `-`, runs of blank lines → one, single final newline.

Used by **Search Console › Tools › mdformater**. Tests: `node --test tests/test_md_tools.mjs`.
