#!/usr/bin/env python3
"""
collect.py — collect the PDF files of a folder tree into one organised folder.

Usage:
    python collect.py SOURCE [OUTPUT] [--join-pdfs-in-same-folder]

  SOURCE   folder searched recursively for *.pdf
  OUTPUT   where the collection goes (default: <SOURCE>/_pdf_collection)

Without --join-pdfs-in-same-folder every PDF is copied, keeping its relative sub-folder.
With it, the PDFs of each folder are merged (natural file-name order: 2.pdf before 10.pdf) into ONE file
named after that folder, e.g.  OUTPUT/Module 1/Module 1.pdf.  Merging needs PyMuPDF (pip install pymupdf,
already part of backend/requirements.txt). A PDF that cannot be read is reported and skipped.

Progress lines look like "[3/20] Module 1/slides.pdf" — the Tools UI turns them into a progress bar.
The source folder is never modified.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


def natural_key(name: str) -> list:
    """'file2' < 'file10' (plain string sort would put 10 first)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', name)]


def find_pdfs(source: Path, skip: Path) -> list:
    """All PDFs under `source`, except anything inside the output folder."""
    out = []
    for p in source.rglob('*'):
        if p.is_file() and p.suffix.lower() == '.pdf':
            try:
                p.relative_to(skip)
                continue                      # already part of the collection
            except ValueError:
                out.append(p)
    return sorted(out, key=lambda p: [natural_key(part) for part in p.relative_to(source).parts])


def merge_pdfs(files: list, target: Path) -> tuple:
    """Merge `files` into `target`. Returns (merged_count, [(file, error), ...])."""
    try:                                  # PyMuPDF — imported lazily so plain copying works without it
        import pymupdf as fitz
    except ImportError:
        import fitz

    out, bad, merged = fitz.open(), [], 0
    for f in files:
        try:
            with fitz.open(f) as src:
                out.insert_pdf(src)
            merged += 1
        except Exception as e:                # encrypted / damaged file
            bad.append((f, str(e).splitlines()[0] if str(e) else type(e).__name__))
    if merged:
        target.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(target), garbage=3, deflate=True)
    out.close()
    return merged, bad


def collect(source: Path, output: Path, join: bool, log=print) -> dict:
    pdfs = find_pdfs(source, output)
    stats = {'found': len(pdfs), 'copied': 0, 'merged_files': 0, 'outputs': 0, 'skipped': 0}
    if not pdfs:
        log('No PDF files found.')
        return stats
    output.mkdir(parents=True, exist_ok=True)

    if not join:
        for i, p in enumerate(pdfs, 1):
            rel = p.relative_to(source)
            log(f'[{i}/{len(pdfs)}] {rel.as_posix()}')
            dest = output / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            stats['copied'] += 1
        stats['outputs'] = stats['copied']
        return stats

    groups: dict = {}
    for p in pdfs:
        groups.setdefault(p.parent, []).append(p)
    done = 0
    for folder, files in groups.items():
        rel_dir = folder.relative_to(source)
        label = (rel_dir.as_posix() if rel_dir.parts else source.name) or 'root'
        target = output / rel_dir / f'{folder.name or source.name}.pdf'
        done += len(files)
        log(f'[{done}/{len(pdfs)}] {label} ({len(files)} file(s))')
        if len(files) == 1:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(files[0], target)
            stats['copied'] += 1
            stats['outputs'] += 1
            continue
        merged, bad = merge_pdfs(files, target)
        for f, err in bad:
            log(f'  ! skipped {f.name}: {err}')
            stats['skipped'] += 1
        stats['merged_files'] += merged
        stats['outputs'] += 1 if merged else 0
    return stats


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):      # never crash on a legacy console code page (cp1252)
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description='Collect (and optionally merge per folder) the PDFs of a folder tree.')
    ap.add_argument('source', help='folder to search recursively')
    ap.add_argument('output', nargs='?', help='output folder (default: <source>/_pdf_collection)')
    ap.add_argument('--join-pdfs-in-same-folder', action='store_true', help='merge the PDFs of each folder into one file')
    a = ap.parse_args(argv)

    source = Path(a.source).expanduser().resolve()
    if not source.is_dir():
        print(f'Error: source folder not found: {source}', file=sys.stderr)
        return 2
    output = Path(a.output).expanduser().resolve() if a.output else source / '_pdf_collection'
    if output == source:
        print('Error: the output folder must differ from the source folder.', file=sys.stderr)
        return 2

    print(f'Source : {source}\nOutput : {output}\nMode   : {"merge PDFs per folder" if a.join_pdfs_in_same_folder else "copy"}', flush=True)
    try:
        st = collect(source, output, a.join_pdfs_in_same_folder, log=lambda m: print(m, flush=True))
    except ImportError:
        print('Error: merging needs PyMuPDF — run: pip install pymupdf', file=sys.stderr)
        return 3
    print(f"Done: {st['found']} PDF(s) found -> {st['outputs']} file(s) written"
          + (f", {st['skipped']} unreadable skipped" if st['skipped'] else '') + f'. Output: {output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
