# _mdImageDownloader

Download the remote images of a Markdown file and point the document at the local copies. Node.js 18+, no packages.

```bash
node _mdImageDownloader/download-images.mjs FILE.md [--out DIR] [--keep-html] [--dry-run]
```

- Handles `![alt](https://…)` and `<img src="https://…">`; the same URL is downloaded once.
- Images go to `images/` next to the file (or `--out DIR`); links become relative paths. `<img>` tags become `![alt](path)` unless `--keep-html`.
- `--dry-run` lists what would be downloaded and changes nothing. The original is saved as `FILE.md.bak`.
- Limits: 30 s and 25 MB per image, only `http(s)` URLs that answer with an image; failures leave the original link untouched.

Used by **Search Console › Tools › mdImageDownloader**. Tests: `node --test tests/test_md_tools.mjs`.
