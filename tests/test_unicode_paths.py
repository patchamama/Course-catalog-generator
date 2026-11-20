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
        q = C._prefix_query('C:\\Kurse\\Präsentationen')
        terms = {c['term']['path'] for c in q['bool']['should'] if 'term' in c}
        self.assertEqual(len(terms), 2)
        self.assertTrue(any(unicodedata.is_normalized('NFD', t) and t != unicodedata.normalize('NFC', t) for t in terms))


if __name__ == '__main__':
    unittest.main()
