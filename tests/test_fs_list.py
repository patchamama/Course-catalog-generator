"""Folder-picker backend: list_subdirs() lists only visible sub-folders."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import catalog_server as cs  # noqa: E402


class ListSubdirsTests(unittest.TestCase):
    def test_lists_only_visible_dirs_sorted(self):
        with tempfile.TemporaryDirectory() as t:
            for n in ('b', 'A', '.hidden'):
                (Path(t) / n).mkdir()
            (Path(t) / 'file.txt').write_text('x')
            r = cs.list_subdirs(t)
            self.assertEqual([d['name'] for d in r['dirs']], ['A', 'b'])
            self.assertEqual(Path(r['path']), Path(t).resolve())
            self.assertEqual(Path(r['parent']), Path(t).resolve().parent)

    def test_empty_path_is_home(self):
        self.assertEqual(Path(cs.list_subdirs('')['path']), Path.home().resolve())

    def test_quoted_path_is_cleaned(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(cs._clean_path(f"  '{t}'  "), t)
            self.assertEqual(cs._clean_path(f'"{t}"'), t)
            self.assertEqual(Path(cs.list_subdirs(f"'{t}'")['path']), Path(t).resolve())

    def test_not_a_folder_raises(self):
        with self.assertRaises(NotADirectoryError):
            cs.list_subdirs('/definitely/not/here')


if __name__ == '__main__':
    unittest.main()
