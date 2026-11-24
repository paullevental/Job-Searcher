from __future__ import annotations

from collections.abc import Sequence

from offerradar_agents.embeddings.errors import EMBEDDING_ERRORS, EmbeddingResponseError
from offerradar_agents.embeddings.provider import EmbeddingProvider, Vector


# Fail fast after one failure: a hung Ollama would otherwise cost a timeout per target.
class LatchingEmbeddingProvider:
    def __init__(self, inner: EmbeddingProvider) -> None:
        self._inner = inner
        self._failure: Exception | None = None

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        if self._failure is not None:
            raise EmbeddingResponseError("embedding disabled for this run") from self._failure
        try:
            return await self._inner.embed(texts)
        except EMBEDDING_ERRORS as exc:
            self._failure = exc
            raise
