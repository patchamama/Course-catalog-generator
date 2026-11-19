"""Payload regressions through the public provider interface; no live credentials."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import llm_providers as llm


class ProviderPayloadTests(unittest.TestCase):
    def payload(self, provider, model):
        captured = []

        def open_request(request, timeout):
            body = json.loads(request.data)
            captured.append(body)
            if provider == 'anthropic':
                event = {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'OK'}}
            else:
                event = {'choices': [{'delta': {'content': 'OK'}}]}
            return io.BytesIO(('data: ' + json.dumps(event) + '\n').encode())

        with patch.object(llm.urllib.request, 'urlopen', side_effect=open_request):
            result = llm.complete({'provider': provider, 'model': model,
                                   'base_url': 'https://example.test/v1'}, 'test', max_tokens=16)
        self.assertEqual(result, 'OK')
        return captured[0]

    def test_reported_gpt6_model_uses_completion_tokens(self):
        payload = self.payload('openai', 'gpt-6.1-sol')
        self.assertNotIn('max_tokens', payload)
        self.assertEqual(payload['max_completion_tokens'], 64)
        self.assertNotIn('temperature', payload)

    def test_existing_openai_reasoning_budget(self):
        payload = self.payload('openai', 'gpt-5')
        self.assertEqual(payload['max_completion_tokens'], 64)
        self.assertNotIn('temperature', payload)

    def test_existing_openai_nonreasoning_budget_and_temperature(self):
        payload = self.payload('openai', 'gpt-4o-mini')
        self.assertEqual(payload['max_completion_tokens'], 16)
        self.assertEqual(payload['temperature'], 0.0)

    def test_custom_compatible_payload_unchanged(self):
        payload = self.payload('custom', 'gpt-6.1-sol')
        self.assertEqual(payload['max_tokens'], 16)
        self.assertEqual(payload['temperature'], 0.0)
        self.assertNotIn('max_completion_tokens', payload)

    def test_anthropic_payload_unchanged(self):
        payload = self.payload('anthropic', 'claude-test')
        self.assertEqual(payload['max_tokens'], 16)
        self.assertNotIn('max_completion_tokens', payload)
        self.assertNotIn('temperature', payload)


if __name__ == '__main__':
    unittest.main()
