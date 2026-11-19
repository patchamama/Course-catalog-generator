# _mdsplit

Split a Markdown file into several files by heading level.

```bash
python _mdsplit/mdsplit.py FILE.md [--max-level N] [--output DIR] [--toc] [--force]
```

- Headings of level `1..N` (default `2`) start a new part: `NN-slug.md` in document order; text before the first heading is `00-intro.md`.
- Headings inside fenced code blocks are ignored.
- `--toc` writes `00-contents.md` with links to every part. `--force` overwrites an existing output folder; without it a non-empty folder is refused.
- Default output: `<FILE stem>_split/` next to the file. The input is never modified.

Used by **Search Console › Tools › mdsplit**. Tests: `python -m unittest tests.test_md_pdf_tools`.
