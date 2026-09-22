"""Text embeddings for the knowledge base.

- local: sentence-transformers (default BAAI/bge-m3: multilingual, strong retrieval), GPU if available.
- hash:  deterministic hashed bag-of-words; not semantic, only for tests/offline runs.
- none:  no vectors; retrieval falls back to full-text (BM25) only.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading

import numpy as np

log = logging.getLogger(__name__)


class Embedder:
    dim: int = 0
    semantic: bool = False  # True when similarity reflects meaning (safe for semantic dedupe)

    def encode(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError


class NoEmbedder(Embedder):
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 0), np.float32)


class HashEmbedder(Embedder):
    dim = 256

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), np.float32)
        for i, t in enumerate(texts):
            for tok in re.findall(r"\w+", t.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest()[:8], 16)
                out[i, h % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)


class LocalEmbedder(Embedder):
    semantic = True

    def __init__(self, model: str, device: str | None = None):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model, device=device)
        self.dim = int(self.model.get_sentence_embedding_dimension() or 0)
        self._lock = threading.Lock()

    def encode(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            v = self.model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(v, dtype=np.float32)


def make_embedder(kind: str, model: str, device: str | None = None) -> Embedder:
    if kind == "hash":
        return HashEmbedder()
    if kind == "none":
        return NoEmbedder()
    try:
        return LocalEmbedder(model, device)
    except Exception as e:  # sentence-transformers missing or model download failed
        if kind == "local":
            raise
        log.warning("embeddings unavailable (%s); knowledge base uses full-text search only", e)
        return NoEmbedder()
