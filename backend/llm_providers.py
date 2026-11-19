"""
llm_providers.py — External LLM providers for the RAG chat (stdlib only).

Supported providers
-------------------
  openai     OpenAI API                        (default base https://api.openai.com/v1)
  anthropic  Anthropic Messages API            (default base https://api.anthropic.com)
  custom     Any OpenAI-compatible endpoint    (OpenRouter, Groq, Together, Mistral,
                                                LM Studio, vLLM, llama.cpp server, …)
  ollama     Local Ollama (only used for list/test here; chat stays in catalog_server)

A *config* is a plain dict sent by the frontend on every request:
    {"provider": "openai", "base_url": "", "api_key": "sk-…", "model": "gpt-4o-mini"}
The API key is never stored or logged by the backend.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Iterator, Optional

PROVIDERS = ('ollama', 'openai', 'anthropic', 'custom')
DEFAULT_BASE = {
    'openai':    'https://api.openai.com/v1',
    'anthropic': 'https://api.anthropic.com',
    'ollama':    'http://127.0.0.1:11434',
    'custom':    '',
}
ANTHROPIC_VERSION = '2023-06-01'
_NON_CHAT = re.compile(r'embed|whisper|tts|dall-e|moderation|davinci|babbage|realtime|audio|transcribe|image|search-preview',
                       re.I)
_REASONING = re.compile(r'^(o\d|gpt-(?:[5-9]|[1-9]\d)(?:\D|$))', re.I)  # Reasoning budgets; no temperature


class LLMError(Exception):
    """Raised for any provider failure; message is safe to show to the user."""


def is_external(cfg: Optional[dict]) -> bool:
    return bool(cfg) and str(cfg.get('provider', 'ollama')).lower() in ('openai', 'anthropic', 'custom')


def normalize(cfg: Optional[dict]) -> dict:
    cfg = dict(cfg or {})
    provider = str(cfg.get('provider') or 'ollama').lower().strip()
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown provider '{provider}'")
    base = str(cfg.get('base_url') or '').strip().rstrip('/') or DEFAULT_BASE[provider]
    if not base:
        raise LLMError('Base URL is required for an OpenAI-compatible endpoint.')
    if not re.match(r'^https?://', base, re.I):
        raise LLMError('Base URL must start with http:// or https://')
    return {
        'provider': provider,
        'base_url': base,
        'api_key':  str(cfg.get('api_key') or '').strip(),
        'model':    str(cfg.get('model') or '').strip(),
    }


# ─── HTTP plumbing ────────────────────────────────────────────────────────────

def _headers(c: dict) -> dict:
    h = {'Content-Type': 'application/json', 'Accept': 'application/json',
         'User-Agent': 'course-catalog/1.0'}
    if c['provider'] == 'anthropic':
        h['anthropic-version'] = ANTHROPIC_VERSION
        if c['api_key']:
            h['x-api-key'] = c['api_key']
    elif c['api_key']:
        h['Authorization'] = 'Bearer ' + c['api_key']
    return h


def _err_text(e: urllib.error.HTTPError, key: str) -> str:
    try:
        raw = e.read().decode('utf-8', 'replace')
        try:
            j = json.loads(raw)
            msg = (j.get('error') or {})
            msg = msg.get('message') if isinstance(msg, dict) else (msg or j.get('message'))
            raw = msg or raw
        except Exception:
            pass
    except Exception:
        raw = ''
    raw = (raw or e.reason or '').strip()[:400]
    if len(key) >= 8:                      # never mask trivial strings — it would mangle the message
        raw = raw.replace(key, '***')
    hint = {401: ' (invalid or missing API key)', 403: ' (access denied)',
            404: ' (endpoint or model not found — check the Base URL)',
            429: ' (rate limit / quota exceeded)'}.get(e.code, '')
    return f'HTTP {e.code}{hint}: {raw}'


def _open(c: dict, url: str, payload: Optional[dict], timeout: float):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(c),
                                 method='POST' if payload is not None else 'GET')
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise LLMError(_err_text(e, c['api_key'])) from None
    except urllib.error.URLError as e:
        raise LLMError(f'Cannot reach {c["base_url"]}: {e.reason}') from None
    except Exception as e:  # timeout, ssl, …
        raise LLMError(f'Cannot reach {c["base_url"]}: {e}') from None


def _anthropic_base(c: dict) -> str:
    return re.sub(r'/v1$', '', c['base_url'])


# ─── Models ───────────────────────────────────────────────────────────────────

def list_models(cfg: dict, timeout: float = 15) -> list:
    c = normalize(cfg)
    p = c['provider']
    if p == 'ollama':
        with _open(c, c['base_url'] + '/api/tags', None, timeout) as r:
            return sorted(m['name'] for m in json.loads(r.read()).get('models', []))
    if p == 'anthropic':
        with _open(c, _anthropic_base(c) + '/v1/models?limit=1000', None, timeout) as r:
            data = json.loads(r.read())
        return [m['id'] for m in data.get('data', []) if m.get('id')]
    # openai / custom
    with _open(c, c['base_url'] + '/models', None, timeout) as r:
        data = json.loads(r.read())
    items = data.get('data') if isinstance(data, dict) else data
    if items is None and isinstance(data, dict):
        items = data.get('models')          # some servers
    ids = []
    for m in items or []:
        mid = m if isinstance(m, str) else (m.get('id') or m.get('name') or '')
        if mid.startswith('models/'):
            mid = mid[len('models/'):]
        if mid:
            ids.append(mid)
    if p == 'openai':
        ids = [i for i in ids if not _NON_CHAT.search(i)]
    return sorted(set(ids))


def test_connection(cfg: dict) -> dict:
    """Validate credentials by listing models; if a model is set, also run a tiny completion."""
    c = normalize(cfg)
    t0 = time.time()
    out = {'ok': False, 'provider': c['provider'], 'base_url': c['base_url']}
    try:
        models = list_models(c)
        out['models'] = models
        out['model_count'] = len(models)
        out['ok'] = True
    except LLMError as e:
        out['error'] = str(e)
        out['ms'] = int((time.time() - t0) * 1000)
        return out
    except Exception as e:
        out['error'] = f'Unexpected response from server: {e}'
        out['ms'] = int((time.time() - t0) * 1000)
        return out
    if c['model'] and c['provider'] != 'ollama':
        try:
            reply = complete(c, 'Reply with the single word: OK', max_tokens=64)   # reasoning models need headroom
            out['reply'] = reply.strip()[:80]
        except LLMError as e:
            out['ok'] = False
            out['error'] = f"Model '{c['model']}' failed: {e}"
    out['ms'] = int((time.time() - t0) * 1000)
    return out


# ─── Chat ─────────────────────────────────────────────────────────────────────

def _merge_anthropic(messages: list) -> tuple:
    """Anthropic wants a separate system string and strictly alternating user/assistant turns."""
    system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
    turns: list = []
    for m in messages:
        if m['role'] not in ('user', 'assistant') or not m.get('content'):
            continue
        if turns and turns[-1]['role'] == m['role']:
            turns[-1]['content'] += '\n\n' + m['content']
        else:
            turns.append({'role': m['role'], 'content': m['content']})
    while turns and turns[0]['role'] != 'user':
        turns.pop(0)
    return system, turns


def stream_chat(cfg: dict, messages: list, temperature: float = 0.1,
                max_tokens: int = 2048, timeout: float = 120) -> Iterator[str]:
    """Yield answer text fragments."""
    c = normalize(cfg)
    if not c['model']:
        raise LLMError('No model selected.')
    p = c['provider']

    if p == 'anthropic':
        system, turns = _merge_anthropic(messages)
        payload = {'model': c['model'], 'max_tokens': max_tokens, 'stream': True,
                   'messages': turns}   # no temperature: newer Claude models reject/deprecate it
        if system:
            payload['system'] = system
        with _open(c, _anthropic_base(c) + '/v1/messages', payload, timeout) as r:
            for raw in r:
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                t = ev.get('type')
                if t == 'content_block_delta':
                    d = ev.get('delta', {})
                    if d.get('type') == 'text_delta' and d.get('text'):
                        yield d['text']
                elif t == 'error':
                    raise LLMError(ev.get('error', {}).get('message', 'Anthropic stream error'))
                elif t == 'message_stop':
                    return
        return

    # openai / custom (OpenAI chat-completions protocol)
    payload = {'model': c['model'], 'messages': messages, 'stream': True}
    reasoning = p == 'openai' and bool(_REASONING.match(c['model']))
    # OpenAI deprecated max_tokens; compatible third-party servers may still need it.
    if p == 'openai':
        payload['max_completion_tokens'] = max_tokens * 4 if reasoning else max_tokens
    else:
        payload['max_tokens'] = max_tokens
    if not reasoning:
        payload['temperature'] = temperature
    r = _open_adaptive(c, c['base_url'] + '/chat/completions', payload, timeout)
    with r:
        for raw in r:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            body = line[5:].strip()
            if body == '[DONE]':
                return
            try:
                ev = json.loads(body)
            except ValueError:
                continue
            if ev.get('error'):
                e = ev['error']
                raise LLMError(e.get('message', str(e)) if isinstance(e, dict) else str(e))
            for ch in ev.get('choices') or []:
                tok = (ch.get('delta') or {}).get('content')
                if tok:
                    yield tok


def _open_adaptive(c: dict, url: str, payload: dict, timeout: float):
    """POST to an OpenAI-style endpoint, fixing parameters the model rejects.

    Model families keep changing which sampling/limit parameters they accept
    (o-series, gpt-5+, proxies in front of them…). Instead of hard-coding model
    names, react to the 400 error text and retry with the parameter swapped or
    removed. At most one adjustment per parameter, so it can't loop.
    """
    for _ in range(4):
        try:
            return _open(c, url, payload, timeout)
        except LLMError as e:
            msg = str(e).lower()
            if 'http 400' not in msg:
                raise
            if "'max_tokens'" in msg and 'max_tokens' in payload:
                payload['max_completion_tokens'] = payload.pop('max_tokens') * 4  # reasoning models burn tokens thinking
            elif "'max_completion_tokens'" in msg and 'max_completion_tokens' in payload:
                payload['max_tokens'] = payload.pop('max_completion_tokens')
            elif 'temperature' in msg and 'temperature' in payload:
                payload.pop('temperature')
            elif "'top_p'" in msg and 'top_p' in payload:
                payload.pop('top_p')
            else:
                raise
    return _open(c, url, payload, timeout)


def complete(cfg: dict, prompt: str, max_tokens: int = 64, temperature: float = 0.0,
             timeout: float = 40) -> str:
    """One-shot completion (used for the short search-query rewrite step)."""
    return ''.join(stream_chat(cfg, [{'role': 'user', 'content': prompt}],
                               temperature=temperature, max_tokens=max_tokens, timeout=timeout))
