"""Paths indexed on a Mac are stored decomposed (NFD); on Windows/Linux the files have composed (NFC) names."""
import os
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
os.environ.setdefault('SEARCH_BASE_PATH', tempfile.gettempdir())
import catalog_server as C  # noqa: E402

BS = chr(92)


def win(*parts):
    return BS.join(parts)


class UnicodePathTests(unittest.TestCase):
    def test_nfd_path_resolves_to_nfc_file(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / unicodedata.normalize('NFC', 'Präsentationen und Anleitungen')
            folder.mkdir()
            f = folder / 'Handbuch.pdf'
            f.write_bytes(b'%PDF-1.4')
            stored = unicodedata.normalize('NFD', str(f))
            self.assertNotEqual(stored, str(f))
            got = C.resolve_fs_path(stored)
            self.assertIsNotNone(got)
            self.assertTrue(got.is_file())

    def test_missing_file_is_none(self):
        self.assertIsNone(C.resolve_fs_path(os.path.join(tempfile.gettempdir(), 'nope-ä', 'x.pdf')))

    def test_prefix_query_matches_both_spellings(self):
        q = C._prefix_query(win('C:', 'Kurse', 'Präsentationen'))
        terms = {c['term']['path'] for c in q['bool']['should'] if 'term' in c}
        self.assertEqual(len(terms), 2)


class DocKeyTests(unittest.TestCase):
    def test_same_name_other_extension_is_one_document(self):
        a = C._doc_key(win('C:', 'Kurse', 'ELO', 'Handbuch.pdf'))
        self.assertEqual(a, C._doc_key(win('C:', 'Kurse', 'ELO', 'Handbuch.md')))
        self.assertEqual(a, C._doc_key('c:/kurse/elo/HANDBUCH.html'))
        self.assertNotEqual(a, C._doc_key(win('C:', 'Kurse', 'ELO', 'Handbuch2.pdf')))
        self.assertNotEqual(a, C._doc_key(win('C:', 'Kurse', 'Other', 'Handbuch.pdf')))

    def test_nfd_and_nfc_names_match(self):
        self.assertEqual(C._doc_key(win('C:', 'a', 'Prä.pdf')), C._doc_key(win('C:', 'a', 'Pra\u0308.md')))


if __name__ == '__main__':
    unittest.main()
