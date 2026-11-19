import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import ollama_support as support


class OllamaTests(unittest.TestCase):
    def test_cloud_first_deduplicated(self):
        models = [{'name': 'local'}, {'name': 'deepseek-v4.1-flash:cloud'},
                  {'name': 'remote', 'remote_host': 'https://ollama.com'},
                  {'name': 'local'}, {'name': 'nomic-embed-text'}]
        self.assertEqual(support.candidates(models, 'local'),
                         ['deepseek-v4.1-flash:cloud', 'remote', 'local'])

    def test_fallback_discards_failed_partial_answer(self):
        def open_request(req, timeout):
            model = json.loads(req.data)['model']
            if model == 'cloud:cloud':
                return io.BytesIO(b'{"message":{"content":"wrong"}}\n{"error":"quota"}\n')
            return io.BytesIO(b'{"message":{"content":"local answer"},"done":true}\n')
        with patch.object(support.urllib.request, 'urlopen', side_effect=open_request):
            self.assertEqual(support.answer('http://localhost:11434',
                             ['cloud:cloud', 'local'], []), ('local', 'local answer'))

    def test_empty_and_truncated_responses_fail(self):
        for stream in (b'{"done":true}\n', b'{"message":{"content":"partial"}}\n'):
            with self.subTest(stream=stream), patch.object(support.urllib.request, 'urlopen',
                                                          return_value=io.BytesIO(stream)):
                with self.assertRaises(RuntimeError):
                    support.answer('http://localhost:11434', ['local'], [])

    def test_catalog_parser_and_cloud_tag(self):
        html = '<a href="/library/llama3.2"><h2>llama3.2</h2></a>' \
               '<a href="/library/kimi-k3.1-pro"><h2>kimi-k3.1-pro</h2><span>cloud</span></a>'
        self.assertEqual(support.parse_catalog(html),
                         [{'name': 'llama3.2', 'cloud': False, 'sizes': []},
                          {'name': 'kimi-k3.1-pro:cloud', 'cloud': True, 'sizes': []}])

    def test_catalog_parses_sizes_and_ignores_lookalikes(self):
        # parameter-count chips are lowercase (8b, 70b, 270m, e2b); pull counts are uppercase (121.2M, 23.6K);
        # the word "cloud" inside a description is not the cloud tag
        html = ('<a href="/library/llama3.1"><h2>llama3.1</h2><p>runs in the cloud or locally</p>'
                '<span>8b</span><span>70b</span><span>121.2M</span><span>23.6K</span><span>8b</span></a>'
                '<a href="/library/gemma3n"><span>e2b</span><span>e4b</span></a>')
        self.assertEqual(support.parse_catalog(html), [
            {'name': 'llama3.1', 'cloud': False, 'sizes': ['8b', '70b']},
            {'name': 'gemma3n', 'cloud': False, 'sizes': ['e2b', 'e4b']}])

    def test_catalog_urls_per_mode(self):
        seen = []
        def fake_open(req, *a, **k):
            seen.append(req.full_url)
            return io.BytesIO(b'<a href="/library/llama3.2">llama</a>')
        with patch.object(support.urllib.request, 'build_opener') as builder:
            builder.return_value.open.side_effect = fake_open
            support.catalog('')                         # newest official models
            support.catalog('llama')                    # search (no o=newest: that returns community models only)
            support.catalog('', cloud_only=True)        # cloud filter
        self.assertIn('/library?sort=newest', seen[0])
        self.assertIn('/search?q=llama', seen[1])
        self.assertNotIn('o=newest', seen[1])
        self.assertIn('c=cloud', seen[2])

    def test_catalog_fetch_encodes_query_and_is_bounded(self):
        with patch.object(support.urllib.request, 'build_opener') as builder:
            builder.return_value.open.return_value = io.BytesIO(b'<a href="/library/llama3.2">llama</a>')
            self.assertEqual(support.catalog('llama & test')[0]['name'], 'llama3.2')
            self.assertIn('q=llama+%26+test', builder.return_value.open.call_args.args[0].full_url)
        with self.assertRaises(ValueError):
            support.catalog('x' * 81)

    def test_catalog_markup_failure_is_not_empty_success(self):
        with self.assertRaises(RuntimeError):
            support.parse_catalog('<html>upstream changed</html>')


if __name__ == '__main__':
    unittest.main()
