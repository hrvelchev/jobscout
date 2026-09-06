"""Embedder seam. Production: fastembed (local ONNX, no API key, no network
after the one-time model download). Tests: tests/fakes.FakeEmbedder."""

from __future__ import annotations

import asyncio
from typing import Protocol

EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    """Lazy-loads the model on first use (the ~130MB download happens once,
    outside the request path of anything time-critical)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        self.model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = await asyncio.to_thread(self._ensure_model)
        vectors = await asyncio.to_thread(lambda: list(model.embed(texts)))
        return [list(map(float, v)) for v in vectors]
