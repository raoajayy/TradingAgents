"""Embedding interface with a deterministic, dependency-free default.

The hashing embedder is not a semantic model — it is a signed
bag-of-words projection. It makes retrieval deterministic and offline
(tests, backtests, degraded ops); production deployments plug a real
embedding callable (OpenAI, local model) into ProMemory without touching
any other code.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


class EmbeddingFn(Protocol):
    def __call__(self, text: str) -> list[float]: ...


_TOKEN = re.compile(r"[a-z0-9_]+")


class HashingEmbedder:
    """Signed feature-hashing embedder: deterministic, normalized, no deps."""

    def __init__(self, dim: int = 256):
        if dim < 16:
            raise ValueError("dim must be >= 16")
        self.dim = dim

    def __call__(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = _TOKEN.findall(text.lower())
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha1(token.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(math.fsum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]


class SemanticEmbedder:
    """model2vec static embeddings (potion-base-8M): real semantics at
    hashing-like latency (~9ms/query, 256-dim, unit norm, ~30MB, no
    torch). Lazy-loads on first call so importing this module stays free
    of the dependency."""

    MODEL_ID = "minishlab/potion-base-8M"

    def __init__(self, model_id: str | None = None):
        self.model_id = model_id or self.MODEL_ID
        self._model = None

    def _load(self):
        if self._model is None:
            from model2vec import StaticModel

            self._model = StaticModel.from_pretrained(self.model_id)
        return self._model

    def __call__(self, text: str) -> list[float]:
        vector = self._load().encode([text])[0]
        return [float(v) for v in vector]


def make_default_embedder() -> EmbeddingFn:
    """Production default (P2-04): semantic, falling back to hashing.

    TRADINGAGENTS_EMBEDDER=hashing forces the old deterministic embedder
    (tests construct HashingEmbedder directly and are unaffected). Any
    failure to import/load the model — missing dep, no network for the
    first download — degrades to hashing with a warning instead of
    blocking boot; analog quality drops, nothing else changes.
    """
    import logging
    import os

    if os.environ.get("TRADINGAGENTS_EMBEDDER", "").lower() == "hashing":
        return HashingEmbedder()
    try:
        embedder = SemanticEmbedder()
        embedder("warmup")  # force the load so failure is visible NOW
        return embedder
    except Exception:  # noqa: BLE001 — degrade, don't block boot
        logging.getLogger(__name__).warning(
            "semantic embedder unavailable; falling back to hashing",
            exc_info=True)
        return HashingEmbedder()
