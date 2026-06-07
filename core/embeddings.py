"""Embedding client + vector helpers for semantic memory retrieval (Tier 2).

Vectors are fetched from an OpenAI-compatible ``/embeddings`` endpoint and
stored as packed float32 blobs alongside each long-term memory. Similarity is
brute-force cosine in pure Python — no native SQLite extension, so it behaves
identically on a local machine and inside the container.
"""

from __future__ import annotations

import array
import asyncio
import importlib
import logging
import math
from typing import Any, cast

log = logging.getLogger(__name__)

# Default local model: small, CPU-friendly, 384-dim (~130MB ONNX). Good balance
# of quality and speed on modest self-hosted hardware.
DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_LOCAL_CACHE = "models"

# Provider names that mean "run the model locally" rather than call an API.
LOCAL_PROVIDERS = frozenset({"local", "fastembed"})

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


class LocalEmbeddingClient:
    """Runs a sentence-embedding model locally via ``fastembed`` (ONNX/CPU).

    No API key, no network at inference time, and the data never leaves the
    machine. The model is loaded lazily on first use (in a worker thread, so it
    never blocks construction or the event loop) and cached for the process
    lifetime. In Docker the model is prefetched at build time (see the
    ``prefetch`` entry point below) so the first call has no download latency.
    """

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL, cache_dir: str | None = None):
        self.model = model or DEFAULT_LOCAL_MODEL
        self.cache_dir = cache_dir or DEFAULT_LOCAL_CACHE
        self._model: Any = None
        self._lock = asyncio.Lock()

    def _load_model(self) -> Any:
        try:
            module = importlib.import_module("fastembed")
            text_embedding = cast(Any, getattr(module, "TextEmbedding"))
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "fastembed is required for local embeddings (pip install fastembed)"
            ) from exc
        return text_embedding(model_name=self.model, cache_dir=self.cache_dir)

    async def _ensure_model(self) -> Any:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    log.info(
                        "Loading local embedding model %s (cache=%s)", self.model, self.cache_dir
                    )
                    self._model = await asyncio.to_thread(self._load_model)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._ensure_model()

        def _run() -> list[list[float]]:
            return [list(map(float, vec)) for vec in model.embed(list(texts))]

        return await asyncio.to_thread(_run)

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0] if vectors else []


def prefetch_local_model(
    model: str = DEFAULT_LOCAL_MODEL, cache_dir: str = DEFAULT_LOCAL_CACHE
) -> int:
    """Download a local embedding model into *cache_dir* and verify it runs.

    Returns the embedding dimension. Used by the Docker build (and the admin
    "Download model" button) so the model is bundled ahead of time.
    """
    module = importlib.import_module("fastembed")
    text_embedding = cast(Any, getattr(module, "TextEmbedding"))
    embedder = text_embedding(model_name=model, cache_dir=cache_dir)
    vec = next(iter(embedder.embed(["warmup"])))
    dim = len(list(vec))
    log.info("Prefetched local embedding model %s (dim=%d) into %s", model, dim, cache_dir)
    return dim


if __name__ == "__main__":  # pragma: no cover - build-time / CLI use
    import sys

    _args = sys.argv[1:]
    if _args and _args[0] == "prefetch":
        _model = _args[1] if len(_args) > 1 else DEFAULT_LOCAL_MODEL
        _cache = _args[2] if len(_args) > 2 else DEFAULT_LOCAL_CACHE
        _dim = prefetch_local_model(_model, _cache)
        print(f"prefetched {_model} (dim={_dim}) -> {_cache}")
    else:
        print("usage: python -m core.embeddings prefetch [MODEL] [CACHE_DIR]")
