"""Pure-logic tests (no Docker / Elasticsearch needed) for export-import helpers and the folder registry.

Run:  python -m unittest tests.test_es_migrate
The Docker round-trip itself is exercised by hand against throw-away containers (see docs/ELASTICSEARCH_README.md).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import es_migrate as M            # noqa: E402
import scan_registry as R         # noqa: E402

BS = chr(92)


def win(*parts):
    return BS.join(parts)


class CommonFolderTests(unittest.TestCase):
    def test_windows_drive_paths(self):
        self.assertEqual(M._common_dir([win('C:', 'Users', 'A', 'Docs', 'Kurse', 'x', 'a.pdf'),
                                        win('C:', 'Users', 'A', 'Docs', 'Kurse', 'y', 'b.pdf')]),
                         win('C:', 'Users', 'A', 'Docs', 'Kurse'))

    def test_drive_root_keeps_trailing_separator(self):
        self.assertEqual(M._common_dir([win('C:', 'a.pdf'), win('C:', 'b.pdf')]), 'C:' + BS)

    def test_unc_share(self):
        p1 = BS * 2 + win('srv', 'share', 'f1', 'a.pdf')
        p2 = BS * 2 + win('srv', 'share', 'f2', 'b.pdf')
        self.assertEqual(M._common_dir([p1, p2]), BS * 2 + win('srv', 'share'))

    def test_posix(self):
        self.assertEqual(M._common_dir(['/Users/me/docs/a.md', '/Users/me/docs/sub/b.md']), '/Users/me/docs')
        self.assertEqual(M._common_dir(['/a.md', '/b.md']), '/')

    def test_case_insensitive_on_windows_style_paths(self):
        self.assertEqual(M._common_dir([win('C:', 'Users', 'A', 'a.pdf'), win('c:', 'users', 'a', 'b.pdf')]),
                         win('C:', 'Users', 'A'))

    def test_volume_keys_separate_drives_and_shares(self):
        self.assertEqual(M._volume_key(win('C:', 'x', 'a')), 'C:')
        self.assertEqual(M._volume_key(BS * 2 + win('Srv', 'Share', 'x')), BS * 2 + win('srv', 'share'))
        self.assertEqual(M._volume_key('/mnt/c/x'), '/')


class FormatTests(unittest.TestCase):
    def test_hsize_never_reads_zero_mb_for_small_files(self):
        self.assertEqual(M._hsize(36416), '36 KB')
        self.assertEqual(M._hsize(5 * 1024 * 1024), '5.0 MB')
        self.assertEqual(M._hsize(3 * 1024 ** 3), '3.0 GB')
        self.assertEqual(M._hsize(12), '12 B')

    def test_archive_suffixes(self):
        self.assertTrue('x.tgz'.endswith(M.ARCHIVE_SUFFIXES))
        self.assertTrue('x.tar.gz'.endswith(M.ARCHIVE_SUFFIXES))
        self.assertFalse('x.zip'.endswith(M.ARCHIVE_SUFFIXES))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / 'data' / 'reg.json'
        self.reg = R.Registry(self.file)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_imports_known_folders_once(self):
        got = self.reg.list(seed=['/a/Kurse', '/b/Videos'])
        self.assertEqual(sorted(e['path'] for e in got), ['/a/Kurse', '/b/Videos'])
        self.assertEqual(len(self.reg.list(seed=['/a/Kurse'])), 2)          # no duplicates

    def test_folder_added_before_first_listing_does_not_block_seeding(self):
        self.reg.upsert('/x/New')                                           # e.g. via the API
        paths = sorted(e['path'] for e in self.reg.list(seed=['/a/Kurse']))
        self.assertEqual(paths, ['/a/Kurse', '/x/New'])

    def test_removed_folder_is_not_reimported_from_seed(self):
        entry = self.reg.list(seed=['/a/Kurse'])[0]
        self.assertTrue(self.reg.remove(entry['id']))
        self.assertEqual(self.reg.list(seed=['/a/Kurse']), [])              # stays removed
        self.reg.upsert('/a/Kurse')                                         # explicit re-add wins
        self.assertEqual(len(self.reg.list(seed=['/a/Kurse'])), 1)

    def test_upsert_is_idempotent_and_keeps_id_across_path_change(self):
        a = self.reg.upsert('/a/Kurse', ext_filter='pdf')
        b = self.reg.upsert('/a/Kurse')
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(self.reg.get(a['id'])['ext_filter'], 'pdf')
        self.reg.update(a['id'], path='/moved/Kurse')
        self.assertEqual(self.reg.get(a['id'])['path'], '/moved/Kurse')
        self.assertIsNone(self.reg.find('/a/Kurse'))

    def test_persists_to_disk(self):
        self.reg.upsert('/a/Kurse')
        again = R.Registry(self.file)
        self.assertEqual([e['path'] for e in again.list()], ['/a/Kurse'])


if __name__ == '__main__':
    unittest.main()
