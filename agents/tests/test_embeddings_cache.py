from __future__ import annotations

from collections.abc import Sequence

from offerradar_agents.embeddings.cache import CachingEmbeddingProvider
from offerradar_agents.embeddings.fake import FakeEmbeddingProvider
from offerradar_agents.embeddings.provider import Vector


class CountingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._fake = FakeEmbeddingProvider()

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        self.calls.append(list(texts))
        return await self._fake.embed(texts)


async def test_only_unseen_distinct_texts_reach_the_inner_provider() -> None:
    inner = CountingProvider()
    cached = CachingEmbeddingProvider(inner)

    first = await cached.embed(["a", "b", "a"])
    second = await cached.embed(["b", "c"])

    assert inner.calls == [["a", "b"], ["c"]]
    assert first[0] == first[2]
    assert second[0] == first[1]


async def test_fully_cached_call_makes_no_request() -> None:
    inner = CountingProvider()
    cached = CachingEmbeddingProvider(inner)
    await cached.embed(["a"])

    assert await cached.embed(["a"]) == await FakeEmbeddingProvider().embed(["a"])
    assert len(inner.calls) == 1
