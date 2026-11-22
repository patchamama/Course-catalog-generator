import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import gh_backup as gh  # noqa: E402


class GhBackupTests(unittest.TestCase):
    def test_clean_repo(self):
        self.assertEqual(gh.clean_repo('https://github.com/me/es-backups.git'), 'me/es-backups')
        self.assertEqual(gh.clean_repo(' me/x '), 'me/x')
        for bad in ('', 'noslash', 'a/b/c', 'me/x; rm -rf'):
            with self.assertRaises(gh.GhError):
                gh.clean_repo(bad)

    def test_typed_token_wins(self):
        self.assertEqual(gh.find_token(' abc ')[0], 'abc')

    def test_public_repo_is_refused_without_consent(self):
        with tempfile.TemporaryDirectory() as d:
            arc = Path(d) / 'a.tgz'
            arc.write_bytes(b'x')
            info = {'user': 'u', 'repo': 'me/x', 'private': False, 'can_push': True, 'default_branch': 'main'}
            with mock.patch.object(gh, 'check', return_value=info):
                with self.assertRaises(gh.GhError) as cm:
                    gh.upload('t', 'me/x', str(arc))
                self.assertIn('PUBLIC', str(cm.exception))

    def test_read_only_token_is_refused(self):
        info = {'user': 'u', 'repo': 'me/x', 'private': True, 'can_push': False, 'default_branch': 'main'}
        with mock.patch.object(gh, 'check', return_value=info):
            with self.assertRaises(gh.GhError):
                gh.upload('t', 'me/x', __file__)

    def test_delete_refuses_foreign_release(self):
        with mock.patch.object(gh, '_api', return_value={'tag_name': 'v1.0.0'}) as api:
            with self.assertRaises(gh.GhError):
                gh.delete('t', 'me/x', 5)
            self.assertEqual(api.call_count, 1)          # only the GET, nothing deleted

    def test_delete_removes_release_and_tag(self):
        calls = []

        def fake(token, method, path, body=None, ok404=False):
            calls.append((method, path))
            return {'tag_name': gh.TAG_PREFIX + '20261120-1'} if method == 'GET' else {}
        with mock.patch.object(gh, '_api', side_effect=fake):
            gh.delete('t', 'me/x', 5)
        self.assertEqual([c[0] for c in calls], ['GET', 'DELETE', 'DELETE'])

    def test_list_sorts_newest_first_and_ignores_other_releases(self):
        rels = [
            {'id': 1, 'tag_name': gh.TAG_PREFIX + 'a', 'created_at': '2026-01-01T00:00:00Z', 'body': '{"docs": 5}',
             'assets': [{'id': 11, 'name': 'a.tgz', 'size': 2048}]},
            {'id': 2, 'tag_name': 'v1.0.0', 'created_at': '2026-03-01T00:00:00Z', 'assets': []},
            {'id': 3, 'tag_name': gh.TAG_PREFIX + 'b', 'created_at': '2026-02-01T00:00:00Z', 'body': 'not json',
             'assets': [{'id': 33, 'name': 'b.tgz', 'size': 10}]},
        ]
        with mock.patch.object(gh, '_api', return_value=rels):
            out = gh.list_backups('t', 'me/x')
        self.assertEqual([o['release_id'] for o in out], [3, 1])
        self.assertEqual(out[1]['docs'], 5)


if __name__ == '__main__':
    unittest.main()
