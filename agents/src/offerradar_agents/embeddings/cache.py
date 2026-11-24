from __future__ import annotations

from collections.abc import Sequence

from offerradar_agents.embeddings.provider import EmbeddingProvider, Vector


# Per-run memo: dedup re-embeds a company's sibling titles once per seen posting.
class CachingEmbeddingProvider:
    def __init__(self, inner: EmbeddingProvider) -> None:
        self._inner = inner
        self._cache: dict[str, Vector] = {}

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        missing = list(dict.fromkeys(t for t in texts if t not in self._cache))
        if missing:
            vectors = await self._inner.embed(missing)
            self._cache.update(zip(missing, vectors, strict=True))
        return [self._cache[t] for t in texts]
