from __future__ import annotations

from collections.abc import Sequence

import httpx
import pytest

from offerradar_agents.embeddings.errors import EmbeddingResponseError
from offerradar_agents.embeddings.latch import LatchingEmbeddingProvider
from offerradar_agents.embeddings.provider import Vector


class FailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        self.calls += 1
        raise httpx.ConnectTimeout("ollama hung")


async def test_first_failure_propagates_unchanged() -> None:
    latched = LatchingEmbeddingProvider(FailingProvider())

    with pytest.raises(httpx.ConnectTimeout):
        await latched.embed(["a"])


async def test_later_calls_fail_fast_without_reaching_the_inner_provider() -> None:
    inner = FailingProvider()
    latched = LatchingEmbeddingProvider(inner)
    with pytest.raises(httpx.ConnectTimeout):
        await latched.embed(["a"])

    with pytest.raises(EmbeddingResponseError, match="disabled"):
        await latched.embed(["b"])
    assert inner.calls == 1


async def test_a_healthy_provider_passes_through() -> None:
    class Echo:
        async def embed(self, texts: Sequence[str]) -> list[Vector]:
            return [[1.0] for _ in texts]

    assert await LatchingEmbeddingProvider(Echo()).embed(["a", "b"]) == [[1.0], [1.0]]
