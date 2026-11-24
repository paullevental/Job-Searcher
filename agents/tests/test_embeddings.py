from __future__ import annotations

import json
import math

import httpx
import pytest
import respx

from offerradar_agents.config import Settings
from offerradar_agents.embeddings.errors import EmbeddingResponseError
from offerradar_agents.embeddings.fake import FakeEmbeddingProvider
from offerradar_agents.embeddings.ollama import OllamaEmbeddingProvider

EMBED_URL = "http://ollama.test:11434/api/embed"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "OLLAMA_URL": "http://ollama.test:11434",
        "EMBEDDING_MODEL": "nomic-embed-text",
        "EMBEDDING_BATCH_SIZE": 2,
        "EMBEDDING_TIMEOUT_SECONDS": 5.0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def vectors_for(count: int, dim: int = 3) -> list[list[float]]:
    return [[float(i), float(i), float(i)][:dim] for i in range(count)]


async def test_prefix_is_applied_to_every_input() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.post(EMBED_URL).respond(200, json={"embeddings": vectors_for(2)})
        async with OllamaEmbeddingProvider(settings=make_settings()) as provider:
            await provider.embed(["alpha", "beta"])

    sent = route.calls.last.request.read()
    body = json.loads(sent)
    assert body["input"] == ["search_document: alpha", "search_document: beta"]


async def test_empty_prefix_is_accepted_for_models_that_want_none() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.post(EMBED_URL).respond(200, json={"embeddings": vectors_for(1)})
        async with OllamaEmbeddingProvider(
            settings=make_settings(), document_prefix=""
        ) as provider:
            await provider.embed(["alpha"])

    sent = route.calls.last.request.read()
    assert json.loads(sent)["input"] == ["alpha"]


async def test_batching_splits_at_the_configured_size_and_preserves_order() -> None:
    texts = ["a", "b", "c", "d", "e"]
    with respx.mock(assert_all_called=True) as router:
        route = router.post(EMBED_URL)
        route.side_effect = [
            httpx.Response(200, json={"embeddings": [[1.0], [2.0]]}),
            httpx.Response(200, json={"embeddings": [[3.0], [4.0]]}),
            httpx.Response(200, json={"embeddings": [[5.0]]}),
        ]
        async with OllamaEmbeddingProvider(settings=make_settings(EMBEDDING_BATCH_SIZE=2)) as p:
            result = await p.embed(texts)

    assert result == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert route.call_count == 3


async def test_non_2xx_raises_embedding_response_error() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(EMBED_URL).respond(500, text="boom")
        async with OllamaEmbeddingProvider(settings=make_settings()) as provider:
            with pytest.raises(EmbeddingResponseError):
                await provider.embed(["alpha"])


async def test_embedding_count_mismatch_raises_rather_than_misaligning() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(EMBED_URL).respond(200, json={"embeddings": vectors_for(1)})
        async with OllamaEmbeddingProvider(settings=make_settings()) as provider:
            with pytest.raises(EmbeddingResponseError):
                await provider.embed(["alpha", "beta"])


async def test_fake_provider_is_deterministic() -> None:
    provider = FakeEmbeddingProvider()

    first = await provider.embed(["same text"])
    second = await provider.embed(["same text"])

    assert first == second


async def test_fake_provider_returns_unit_vectors() -> None:
    provider = FakeEmbeddingProvider()

    vectors = await provider.embed(["one", "two", "three"])

    for vector in vectors:
        norm = math.sqrt(sum(v * v for v in vector))
        assert math.isclose(norm, 1.0, rel_tol=1e-9)


async def test_fake_provider_distinguishes_different_texts() -> None:
    provider = FakeEmbeddingProvider()

    vectors = await provider.embed(["alpha", "beta"])

    assert vectors[0] != vectors[1]
