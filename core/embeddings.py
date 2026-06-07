"""Embedding client + vector helpers for semantic memory retrieval (Tier 2).

Vectors are fetched from an OpenAI-compatible ``/embeddings`` endpoint and
stored as packed float32 blobs alongside each long-term memory. Similarity is
brute-force cosine in pure Python — no native SQLite extension, so it behaves
identically on a local machine and inside the container.
"""

from __future__ import annotations

import array
import importlib
import logging
import math
from typing import Any, cast

log = logging.getLogger(__name__)

# OpenAI-compatible base URLs for providers that expose an /embeddings endpoint.
_DEFAULT_BASE_URLS = {
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "deepseek": "https://api.deepseek.com",
}


def pack_vector(vector: list[float]) -> bytes:
    """Pack a float vector into a compact float32 blob for storage."""
    return array.array("f", vector).tobytes()


def unpack_vector(blob: bytes | None) -> list[float] | None:
    """Unpack a float32 blob back into a list of floats (None if empty)."""
    if not blob:
        return None
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors (0.0 on degenerate input)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class EmbeddingClient:
    """Thin wrapper over an OpenAI-compatible embeddings endpoint."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        dimensions: int = 0,
    ):
        self.provider = (provider or "openai").strip().lower()
        self.model = model
        self.dimensions = dimensions or 0
        resolved_base = base_url or _DEFAULT_BASE_URLS.get(self.provider)
        try:
            module = importlib.import_module("openai")
            client_class = cast(Any, getattr(module, "AsyncOpenAI"))
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError("openai package is required for embeddings") from exc
        self._client = cast(Any, client_class)(api_key=api_key, base_url=resolved_base or None)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""
        if not texts:
            return []
        kwargs: dict[str, Any] = {"model": self.model, "input": texts}
        if self.dimensions:
            kwargs["dimensions"] = self.dimensions
        response = await self._client.embeddings.create(**kwargs)
        # Preserve request order (OpenAI returns data sorted by index, but be safe).
        items = sorted(response.data, key=lambda d: getattr(d, "index", 0))
        return [list(item.embedding) for item in items]

    async def embed_one(self, text: str) -> list[float]:
        """Return a single embedding vector (empty list on failure)."""
        vectors = await self.embed([text])
        return vectors[0] if vectors else []
