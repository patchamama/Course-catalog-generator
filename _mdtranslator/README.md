# _mdtranslator

Prepare a Markdown document for translation (and put it back together). Node.js 18+, no packages.

```bash
node _mdtranslator/translate-prepare.mjs FILE.md                        # prepare
node _mdtranslator/translate-prepare.mjs FILE.md --restore FILE.segments.json   # rebuild
```

**Prepare** writes `FILE.segments.json` (`[{id, text, translation}]`) and `FILE.skeleton.md` (the document with every
translatable segment replaced by `{{SEG:n}}`). Code blocks, inline code, URLs, link targets, image paths, HTML tags and
front matter are never offered for translation. Fill in `translation` (an LLM, DeepL, a human…), then **restore** to get
`FILE.translated.md`; a segment without a translation keeps its original text.

This tool does not call any translation service itself. Used by **Search Console › Tools › mdtranslator**.
Tests: `node --test tests/test_md_tools.mjs`.
