"""
rerank.py — optional cross-encoder reranker for the RAG retrieval step.

Why: BM25 and vector search are fast but coarse. A cross-encoder reads the question
TOGETHER with each candidate passage and scores the pair, which is far more accurate
at picking the passages that actually answer the question. Typical flow:

    hybrid retrieval (BM25 + kNN, RRF)  →  top ~25 candidates  →  rerank  →  top k  →  LLM

The model is multilingual (German manuals, Spanish/English questions). It loads in a
BACKGROUND thread the first time it's needed, so the first questions are not blocked by
a model download; until it is ready, retrieval silently keeps the RRF order.

Settings (env.py or environment variables):
    RERANK_ENABLED     True       master switch
    RERANKER_MODEL     cross-encoder/mmarco-mMiniLMv2-L12-H384-v1   (default; multilingual, ~470 MB, ~3 s/25 passages on CPU)
                       BAAI/bge-reranker-v2-m3   (larger, ~2.2 GB; ~56 s/25 passages on CPU → use with a GPU)
    RERANK_CANDIDATES  25         how many fused candidates are reranked
    RERANK_MAX_CHARS   1500       passage text sent to the model (truncated)
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

try:
    import env as _env
except ImportError:                      # standalone use
    _env = None


def _opt(name: str, default):
    if _env is not None and hasattr(_env, name):
        return getattr(_env, name)
    return os.environ.get(name, default)


def _truthy(v) -> bool:
    return str(v).strip().lower() not in ('0', 'false', 'no', 'off', '')


ENABLED     = _truthy(_opt('RERANK_ENABLED', True))
# Default = light multilingual model: ~3 s for 25 passages on CPU. BAAI/bge-reranker-v2-m3 is
# larger (~2.2 GB) and took ~56 s for the same batch on this machine — only worth it with a GPU.
MODEL_NAME  = str(_opt('RERANKER_MODEL', 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'))
CANDIDATES  = int(_opt('RERANK_CANDIDATES', 25))
MAX_CHARS   = int(_opt('RERANK_MAX_CHARS', 1500))

_model = None
_state = 'idle'            # idle | loading | ready | failed
_error = ''
_lock = threading.Lock()


def _load() -> None:
    global _model, _state, _error
    t0 = time.time()
    try:
        from sentence_transformers import CrossEncoder
        log.info(f"Reranker: loading {MODEL_NAME} …")
        try:
            # Cached model: no network round-trips (scoped — does not touch global HF settings)
            _model = CrossEncoder(MODEL_NAME, max_length=512, local_files_only=True)
        except Exception:
            log.info("Reranker: not in local cache — downloading (first use only) …")
            _model = CrossEncoder(MODEL_NAME, max_length=512)
        _state = 'ready'
        log.info(f"Reranker ready ({time.time() - t0:.0f}s).")
    except Exception as e:                # missing package, no network for first download, OOM …
        _state, _error = 'failed', f"{type(e).__name__}: {e}"[:300]
        log.warning(f"Reranker unavailable — keeping RRF order. {_error}")


def warmup() -> str:
    """Start loading in the background (idempotent). Returns the current state."""
    global _state
    if not ENABLED:
        return 'disabled'
    with _lock:
        if _state == 'idle':
            _state = 'loading'
            threading.Thread(target=_load, name='reranker-load', daemon=True).start()
    return _state


def status() -> dict:
    return {'enabled': ENABLED, 'state': 'disabled' if not ENABLED else _state,
            'model': MODEL_NAME, 'error': _error, 'candidates': CANDIDATES}


def ready() -> bool:
    return ENABLED and _state == 'ready'


def score(query: str, passages: list) -> Optional[list]:
    """Relevance in 0..1 per passage (same order), or None if the reranker isn't usable."""
    if not passages:
        return []
    if not ready():
        warmup()
        return None
    try:
        pairs = [(query, (p or '')[:MAX_CHARS]) for p in passages]
        raw = _model.predict(pairs, batch_size=8, show_progress_bar=False)
        return [1.0 / (1.0 + math.exp(-float(x))) for x in raw]    # logits → probability
    except Exception as e:
        log.warning(f"Reranker failed ({type(e).__name__}: {e}) — keeping RRF order")
        return None
