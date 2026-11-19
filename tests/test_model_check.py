"""Imported-index embedding model check: embedding_model_report()."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import catalog_server as cs  # noqa: E402


class ModelReportTests(unittest.TestCase):
    def test_same_model_matches(self):
        r = cs.embedding_model_report(cs._EMBEDDING_MODEL)
        self.assertTrue(r['matches'])
        self.assertTrue(r['supported'])

    def test_other_supported_model_differs(self):
        other = next(m for m in cs._MODEL_CONFIG if m != cs._EMBEDDING_MODEL)
        r = cs.embedding_model_report(other, cs._MODEL_CONFIG[other]['dims'])
        self.assertFalse(r['matches'])
        self.assertTrue(r['supported'])
        self.assertFalse(r['dims_mismatch'])

    def test_unknown_model_not_supported(self):
        r = cs.embedding_model_report('acme/not-a-model')
        self.assertFalse(r['supported'])

    def test_dims_mismatch_flagged(self):
        m = next(iter(cs._MODEL_CONFIG))
        self.assertTrue(cs.embedding_model_report(m, 99999)['dims_mismatch'])


if __name__ == '__main__':
    unittest.main()
