#!/usr/bin/env python3
"""
mdsplit.py — split a Markdown file into several files by heading level.

Usage:
    python mdsplit.py FILE.md [--max-level N] [--output DIR] [--toc] [--force]

  --max-level N   headings of level 1..N start a new file (default 2)
  --output DIR    where to write the parts (default: <FILE stem>_split/ next to FILE)
  --toc           also write 00-contents.md linking to every part
  --force         overwrite files that already exist (otherwise a non-empty output folder is refused)

Parts are named NN-slug.md in document order. Text before the first heading becomes 00-intro.md.
Headings inside fenced code blocks (``` or ~~~) are ignored. The input file is never modified.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

HEADING = re.compile(r'^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$')
FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})')


def slugify(text: str, fallback: str = 'section') -> str:
    t = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    t = re.sub(r'[^A-Za-z0-9]+', '-', t).strip('-').lower()
    return (t or fallback)[:60]


def split_markdown(text: str, max_level: int) -> list:
    """-> [(title, level, body_text)] ; the first item has level 0 when there is a preamble."""
    parts, cur_title, cur_level, buf = [], None, 0, []
    fence = None                                   # the opening fence marker while inside a code block
    for line in text.splitlines():
        m = FENCE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker[0] * 3
            elif marker.startswith(fence):
                fence = None
        h = None if fence else HEADING.match(line)
        if h and len(h.group(1)) <= max_level:
            if cur_title is not None or any(b.strip() for b in buf):
                parts.append((cur_title or 'Introduction', cur_level, '\n'.join(buf).rstrip() + '\n'))
            cur_title, cur_level, buf = h.group(2).strip(), len(h.group(1)), [line]
        else:
            buf.append(line)
    if cur_title is not None or any(b.strip() for b in buf):
        parts.append((cur_title or 'Introduction', cur_level, '\n'.join(buf).rstrip() + '\n'))
    return parts


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):      # never crash on a legacy console code page (cp1252)
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description='Split a Markdown file by heading level.')
    ap.add_argument('file')
    ap.add_argument('--max-level', type=int, default=2, help='split on headings of level 1..N (default 2)')
    ap.add_argument('--output', help='output directory')
    ap.add_argument('--toc', action='store_true', help='write 00-contents.md')
    ap.add_argument('--force', action='store_true', help='overwrite existing files')
    a = ap.parse_args(argv)

    src = Path(a.file).expanduser().resolve()
    if not src.is_file():
        print(f'Error: file not found: {src}', file=sys.stderr)
        return 2
    if not 1 <= a.max_level <= 6:
        print('Error: --max-level must be between 1 and 6.', file=sys.stderr)
        return 2
    out = Path(a.output).expanduser().resolve() if a.output else src.with_name(src.stem + '_split')
    if out.exists() and any(out.iterdir()) and not a.force:
        print(f'Error: {out} is not empty. Use --force to overwrite or choose another output folder.', file=sys.stderr)
        return 2

    parts = split_markdown(src.read_text(encoding='utf-8-sig', errors='replace'), a.max_level)
    if not parts:
        print('Nothing to split (the file is empty).')
        return 0
    out.mkdir(parents=True, exist_ok=True)

    width = max(2, len(str(len(parts))))
    has_preamble = parts[0][1] == 0              # text before the first heading → 00-intro.md
    written = []
    for i, (title, level, body) in enumerate(parts):
        num = i if has_preamble else i + 1       # headed parts are numbered from 01
        name = f'{num:0{width}d}-{"intro" if level == 0 else slugify(title)}.md'
        (out / name).write_text(body, encoding='utf-8')
        written.append((title, level, name))
        print(f'[{i + 1}/{len(parts)}] {name}', flush=True)

    if a.toc:
        lines = [f'# Contents of {src.name}', '']
        for title, level, name in written:
            indent = '  ' * max(0, level - 1)
            lines.append(f'{indent}- [{title}]({name})')
        (out / f'{0:0{width}d}-contents.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(f'Table of contents: {0:0{width}d}-contents.md')

    print(f'Done: {len(written)} part(s) in {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
