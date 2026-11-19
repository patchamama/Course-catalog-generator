"""Tests for the Python tools: _mdsplit/mdsplit.py and _pdf_collector/collect.py.

Run:  python -m unittest tests.test_md_pdf_tools
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '_mdsplit'))
sys.path.insert(0, str(ROOT / '_pdf_collector'))
import mdsplit    # noqa: E402
import collect    # noqa: E402

DOC = """Intro text before any heading.

# Part One

Alpha.

## Sub A

```python
# not a heading, it is a comment
## nor this
```

## Sub B

Beta.

# Part Two ✓

Gamma.
"""


class MdSplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / 'book.md'
        self.src.write_text(DOC, encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_split_levels_ignores_fenced_code(self):
        parts = mdsplit.split_markdown(DOC, 2)
        self.assertEqual([(t, l) for t, l, _ in parts],
                         [('Introduction', 0), ('Part One', 1), ('Sub A', 2), ('Sub B', 2), ('Part Two ✓', 1)])
        self.assertIn('# not a heading', parts[2][2])                     # stayed inside Sub A
        self.assertEqual([t for t, _, _ in mdsplit.split_markdown(DOC, 1)], ['Introduction', 'Part One', 'Part Two ✓'])

    def test_cli_writes_numbered_files_and_toc(self):
        out = self.dir / 'parts'
        self.assertEqual(mdsplit.main([str(self.src), '--max-level', '2', '--output', str(out), '--toc']), 0)
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, ['00-contents.md', '00-intro.md', '01-part-one.md', '02-sub-a.md', '03-sub-b.md', '04-part-two.md'])
        toc = (out / '00-contents.md').read_text(encoding='utf-8')
        self.assertIn('[Sub A](02-sub-a.md)', toc)
        self.assertEqual(self.src.read_text(encoding='utf-8'), DOC)        # input never modified

    def test_refuses_non_empty_output_without_force(self):
        out = self.dir / 'parts'
        out.mkdir()
        (out / 'x.md').write_text('keep me')
        self.assertEqual(mdsplit.main([str(self.src), '--output', str(out)]), 2)
        self.assertEqual((out / 'x.md').read_text(), 'keep me')
        self.assertEqual(mdsplit.main([str(self.src), '--output', str(out), '--force']), 0)

    def test_bad_input(self):
        self.assertEqual(mdsplit.main([str(self.dir / 'missing.md')]), 2)
        self.assertEqual(mdsplit.main([str(self.src), '--max-level', '9']), 2)


def make_pdf(path: Path, pages: int, text: str):
    import fitz
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f'{text} page {i + 1}')
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()


class PdfCollectTests(unittest.TestCase):
    def setUp(self):
        try:
            import fitz  # noqa: F401
        except ImportError:
            self.skipTest('PyMuPDF not installed')
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / 'courses'
        make_pdf(self.src / 'Module 1' / '2-second.pdf', 2, 'second')
        make_pdf(self.src / 'Module 1' / '10-tenth.pdf', 1, 'tenth')
        make_pdf(self.src / 'Module 1' / '1-first.pdf', 3, 'first')
        make_pdf(self.src / 'Module 2' / 'only.pdf', 1, 'only')
        (self.src / 'notes.txt').write_text('not a pdf')

    def tearDown(self):
        self.tmp.cleanup()

    def test_natural_order(self):
        self.assertEqual(sorted(['file10', 'file2', 'file1'], key=collect.natural_key), ['file1', 'file2', 'file10'])

    def test_copy_keeps_structure_and_default_output_is_not_rescanned(self):
        self.assertEqual(collect.main([str(self.src)]), 0)
        out = self.src / '_pdf_collection'
        self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob('*.pdf')),
                         ['Module 1/1-first.pdf', 'Module 1/10-tenth.pdf', 'Module 1/2-second.pdf', 'Module 2/only.pdf'])
        self.assertEqual(collect.main([str(self.src)]), 0)                 # second run: output not collected into itself
        self.assertEqual(len(list(out.rglob('*.pdf'))), 4)

    def test_join_merges_per_folder_in_natural_order(self):
        import fitz
        out = Path(self.tmp.name) / 'merged'
        self.assertEqual(collect.main([str(self.src), str(out), '--join-pdfs-in-same-folder']), 0)
        merged = out / 'Module 1' / 'Module 1.pdf'
        self.assertTrue(merged.is_file())
        with fitz.open(merged) as doc:
            self.assertEqual(doc.page_count, 3 + 2 + 1)                    # first(3) + second(2) + tenth(1)
            self.assertIn('first', doc[0].get_text())
            self.assertIn('second', doc[3].get_text())
            self.assertIn('tenth', doc[5].get_text())
        self.assertTrue((out / 'Module 2' / 'Module 2.pdf').is_file())     # single file: just copied

    def test_unreadable_pdf_is_skipped_not_fatal(self):
        (self.src / 'Module 1' / '0-broken.pdf').write_bytes(b'this is not a pdf')
        out = Path(self.tmp.name) / 'merged2'
        self.assertEqual(collect.main([str(self.src), str(out), '--join-pdfs-in-same-folder']), 0)
        self.assertTrue((out / 'Module 1' / 'Module 1.pdf').is_file())

    def test_errors(self):
        self.assertEqual(collect.main([str(self.src / 'nope')]), 2)
        self.assertEqual(collect.main([str(self.src), str(self.src)]), 2)


if __name__ == '__main__':
    unittest.main()
