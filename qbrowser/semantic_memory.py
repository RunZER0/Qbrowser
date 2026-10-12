"""Optional local embeddings for cross-task memory retrieval.

No embedding service is contacted. FastEmbed downloads its selected model on the
first explicit use and runs inference locally; enabling this feature is opt-in.
"""

from __future__ import annotations

import math
import os
import struct
from functools import lru_cache
from typing import Any


DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


def model_name() -> str:
    value = os.getenv("QBROWSER_MEMORY_EMBEDDING_MODEL", DEFAULT_MODEL).strip()
    if not value or len(value) > 200:
        raise ValueError("QBROWSER_MEMORY_EMBEDDING_MODEL must contain 1-200 characters")
    return value


def enabled() -> bool:
    return os.getenv("QBROWSER_MEMORY_EMBEDDINGS", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }


@lru_cache(maxsize=2)
def _load_embedder(name: str):
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError(
            "Local semantic memory needs the optional dependency: "
            "pip install -e '.[memory]'") from exc
    return TextEmbedding(model_name=name)


def embed_texts(texts: list[str], *, embedder: Any | None = None,
                name: str | None = None) -> list[list[float]]:
    """Return validated, unit-normalized vectors for a small batch of strings."""
    if not isinstance(texts, list) or any(not isinstance(text, str) or not text for text in texts):
        raise ValueError("Embedding input must be a list of non-empty strings")
    if not texts:
        return []
    selected = name or model_name()
    engine = embedder if embedder is not None else _load_embedder(selected)
    raw_vectors = list(engine.embed(texts))
    if len(raw_vectors) != len(texts):
        raise RuntimeError("Embedding model returned a different number of vectors")
    vectors: list[list[float]] = []
    dimensions: int | None = None
    for raw in raw_vectors:
        vector = [float(value) for value in raw]
        if (not vector or (dimensions is not None and len(vector) != dimensions) or
                any(not math.isfinite(value) for value in vector)):
            raise RuntimeError("Embedding model returned invalid vector data")
        dimensions = len(vector)
        norm = math.sqrt(math.fsum(value * value for value in vector))
        if norm <= 0:
            raise RuntimeError("Embedding model returned a zero-length vector")
        vectors.append([value / norm for value in vector])
    return vectors


def pack_vector(vector: list[float]) -> bytes:
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("Cannot store an invalid embedding vector")
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(data: bytes, dimensions: int) -> list[float]:
    if type(dimensions) is not int or not 1 <= dimensions <= 100_000:
        raise ValueError("Invalid stored embedding dimensions")
    if not isinstance(data, bytes) or len(data) != dimensions * 4:
        raise ValueError("Stored embedding size does not match its dimensions")
    vector = list(struct.unpack(f"<{dimensions}f", data))
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("Stored embedding contains invalid values")
    return vector


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    return math.fsum(a * b for a, b in zip(left, right))
