"""Bounded official catalog discovery and installed Ollama cloud failover."""
import json
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class _CatalogParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            match = re.fullmatch(r'/library/([a-zA-Z0-9_.-]+)', href)
            self.current = {'name': match[1], 'cloud': False, 'sizes': []} if match else None

    def handle_data(self, text):
        # Parameter-count chips are lowercase ("8b", "70b", "270m", "e2b", "8x7b"); pull counts
        # on the same card are uppercase ("121.2M", "23.6K") and must not be mistaken for sizes.
        t = text.strip()
        if self.current and re.fullmatch(r'(?:e|\d+x)?\d+(?:\.\d+)?[bm]', t) and t not in self.current['sizes']:
            self.current['sizes'].append(t)
        # Cloud models carry a standalone "cloud" tag chip; a description that merely
        # mentions the word ("runs in the cloud") must not count.
        if self.current and text.strip().lower() == 'cloud':
            self.current['cloud'] = True

    def handle_endtag(self, tag):
        if tag == 'a' and self.current:
            if self.current['cloud']:
                self.current['name'] += ':cloud'
            if self.current not in self.items:
                self.items.append(self.current)
            self.current = None


def parse_catalog(html):
    parser = _CatalogParser()
    parser.feed(html)
    if not parser.items:
        raise RuntimeError('No catalog entries found. Try another search or enter a model manually.')
    return parser.items[:20]


def catalog(query='', cloud_only=False):
    """Official Ollama models: newest first when no query, relevance order when searching.

    Sources (ollama.com changed its search page, so `o=newest` on /search now returns
    community `user/model` entries only, which this parser — correctly — ignores):
      no query          /library?sort=newest      newest official models, cloud ones tagged
      query             /search?q=<query>          official matches
      cloud_only        /search?c=cloud&o=newest   cloud-capable models, newest first
    """
    query = query.strip()
    if len(query) > 80:
        raise ValueError('Search must be at most 80 characters.')
    if cloud_only:
        url = 'https://ollama.com/search?' + urllib.parse.urlencode({'c': 'cloud', 'o': 'newest', 'q': query})
    elif query:
        url = 'https://ollama.com/search?' + urllib.parse.urlencode({'q': query})
    else:
        url = 'https://ollama.com/library?' + urllib.parse.urlencode({'sort': 'newest'})
    req = urllib.request.Request(url, headers={'User-Agent': 'course-catalog/1.0'})
    # Fixed upstream, bounded fetch; do not follow redirects to arbitrary hosts.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        """Follow redirects only within https://ollama.com (e.g. ?q= normalisation)."""
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
            if target.scheme != 'https' or target.netloc != 'ollama.com':
                raise RuntimeError('Catalog redirect rejected.')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    with urllib.request.build_opener(NoRedirect).open(req, timeout=12) as response:
        final_url = response.geturl() if hasattr(response, 'geturl') else url
        if urllib.parse.urlsplit(final_url).netloc != 'ollama.com':
            raise RuntimeError('Unexpected catalog host.')
        # Results are ordered and only the first 20 are used, so a prefix is enough
        # (/library is ~1 MB and growing); a cut-off tail is harmless to the parser.
        raw = response.read(400 * 1024)
    return parse_catalog(raw.decode('utf-8', 'replace'))


def candidates(models, preferred=''):
    cloud, local = [], []
    for entry in models:
        entry = {'name': entry} if isinstance(entry, str) else entry
        name = entry.get('name', '')
        if not name or re.search(r'embed|bge-|nomic-', name, re.I):
            continue
        group = cloud if entry.get('remote_host') or entry.get('remote_model') or name.endswith(':cloud') else local
        if name not in group:
            group.append(name)
    for group in (cloud, local):
        if preferred in group:
            group.remove(preferred)
            group.insert(0, preferred)
    return cloud + local


def answer(url, models, messages, budget=120):
    """Buffer each attempt so a failed cloud stream cannot leak partial answers."""
    deadline = time.monotonic() + budget
    failed = []          # model names only — never prompts, bodies or credentials
    for index, model in enumerate(models):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        payload = {'model': model, 'messages': messages, 'stream': True,
                   'think': False,   # reasoning models (Qwen3, DeepSeek…) would otherwise answer with thoughts only
                   'options': {'temperature': 0.1, 'num_predict': 1024}}
        req = urllib.request.Request(url.rstrip('/') + '/api/chat',
                                     data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            parts, size, done = [], 0, False
            attempt_limit = min(30, remaining / (len(models) - index))
            attempt_deadline = time.monotonic() + attempt_limit
            with urllib.request.urlopen(req, timeout=attempt_limit) as response:
                for raw in response:
                    if time.monotonic() >= attempt_deadline:
                        raise RuntimeError('Answer deadline exceeded.')
                    data = json.loads(raw)
                    if data.get('error'):
                        raise RuntimeError('Model returned an error.')
                    token = data.get('message', {}).get('content', '')
                    size += len(token)
                    if size > 256000:
                        raise RuntimeError('Answer too large.')
                    parts.append(token)
                    if data.get('done'):
                        done = True
                        break
            text = ''.join(parts)
            if done and text.strip():
                return model, text
            failed.append(f'{model} (empty answer)')
        except Exception as e:
            # Do not log prompts, returned error bodies, or credentials: class / HTTP status only.
            failed.append(f'{model} ({getattr(e, "code", None) or type(e).__name__})')
            continue
    raise RuntimeError('All installed Ollama chat models failed or the request timed out. Tried: '
                       + ', '.join(failed[:8]) + ('…' if len(failed) > 8 else ''))
