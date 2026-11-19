"""The Tools UI sends named options; the backend must turn them into each script's CLI arguments.

Regression: the endpoint used to read only an `args` list, so every UI button ran its script with no arguments.
Run:  python -m unittest tests.test_tool_args
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))


class ToolArgsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('SEARCH_BASE_PATH', tempfile.gettempdir())
        import catalog_server
        cls.srv = catalog_server
        cls.tmp = tempfile.TemporaryDirectory()
        cls.md = Path(cls.tmp.name) / 'doc.md'
        cls.md.write_text('# x\n')
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def args(self, tool, **opts):
        return self.srv._tool_cli_args(tool, opts)

    def test_mdsplit(self):
        self.assertEqual(self.args('mdsplit', file=str(self.md), max_level='3', output='out', table_of_contents=True, force=True),
                         [str(self.md), '--max-level', '3', '--output', 'out', '--toc', '--force'])
        self.assertEqual(self.args('mdsplit', file=str(self.md)), [str(self.md)])

    def test_image_downloader_and_formatter_and_translator(self):
        self.assertEqual(self.args('mdImageDownloader', file=str(self.md), out='img', keep_html=True, dry_run=True),
                         [str(self.md), '--out', 'img', '--keep-html', '--dry-run'])
        self.assertEqual(self.args('mdformater', file=str(self.md), auto=True, images_only=True),
                         [str(self.md), '--auto', '--images-only'])
        self.assertEqual(self.args('mdtranslator', file=str(self.md)), [str(self.md)])

    def test_html_to_markdown_modes(self):
        self.assertEqual(self.args('mdfromhtml', input=str(self.md), output='o.md'), [str(self.md), 'o.md'])
        self.assertEqual(self.args('mdfromhtml-collection', input_dir=str(self.dir), output_dir='o'), [str(self.dir), 'o'])
        merged = self.args('mdfromhtml-merge', input_dir=str(self.dir), toc_depth='3', no_convert_emphasis=True, images_dest='img')
        self.assertEqual(merged, [str(self.dir), '--toc-depth', '3', '--include-original-refs', '--no-convert-emphasis', '--images-dest', 'img'])
        # "Skip original reference links" ticked → the script's default (excluded) → no --include-original-refs
        self.assertNotIn('--include-original-refs', self.args('mdfromhtml-merge', input_dir=str(self.dir), no_refs=True))

    def test_bad_input_is_rejected_with_a_message(self):
        for tool, opts, text in [
            ('mdsplit', {}, 'required'),
            ('mdsplit', {'file': str(self.dir / 'missing.md')}, 'not found'),
            ('mdsplit', {'file': str(self.md), 'max_level': '9'}, '1 to 6'),
            ('mdformater', {'file': '--auto'}, 'cannot start'),                         # option injection
            ('mdImageDownloader', {'file': str(self.md), 'out': '--evil'}, 'cannot start'),
            ('mdfromhtml-merge', {'input_dir': str(self.dir), 'toc_depth': 'x'}, '0 to 6'),
        ]:
            with self.subTest(tool=tool, opts=opts):
                with self.assertRaises(ValueError) as cm:
                    self.args(tool, **opts)
                self.assertIn(text, str(cm.exception))

    def test_endpoints_are_localhost_only_and_validate(self):
        client = self.srv.app.test_client()
        env = {'REMOTE_ADDR': '10.1.2.3'}
        for url, body in [('/api/tools/run-script', {'tool': 'mdsplit', 'file': str(self.md)}),
                          ('/api/tools/pdf-collector', {'source': str(self.dir)}),
                          ('/api/tools/youtube-download', {'urls': ['https://youtu.be/x']})]:
            self.assertEqual(client.post(url, json=body, environ_overrides=env).status_code, 403, url)
        local = {'REMOTE_ADDR': '127.0.0.1'}
        self.assertEqual(client.post('/api/tools/youtube-download', json={'urls': ['--cookies', 'x']}, environ_overrides=local).status_code, 400)
        self.assertEqual(client.post('/api/tools/pdf-collector', json={'source': str(self.dir / 'nope')}, environ_overrides=local).status_code, 400)
        r = client.post('/api/tools/run-script', json={'tool': 'mdsplit', 'file': str(self.dir / 'nope.md')}, environ_overrides=local)
        self.assertEqual(r.status_code, 400)
        self.assertIn('not found', r.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
