from __future__ import annotations

from collections.abc import Sequence

import httpx
from pydantic import BaseModel, ConfigDict

from offerradar_agents.config import Settings, get_settings
from offerradar_agents.embeddings.errors import EmbeddingResponseError
from offerradar_agents.embeddings.provider import DEFAULT_DOCUMENT_PREFIX, Vector


class _EmbedResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    embeddings: list[Vector]


class OllamaEmbeddingProvider:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        document_prefix: str = DEFAULT_DOCUMENT_PREFIX,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._prefix = document_prefix
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(timeout=self._settings.embedding_timeout_seconds)
        )

    async def __aenter__(self) -> OllamaEmbeddingProvider:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        prefixed = [f"{self._prefix}{text}" for text in texts]
        batch_size = self._settings.embedding_batch_size
        vectors: list[Vector] = []
        for start in range(0, len(prefixed), batch_size):
            vectors.extend(await self._embed_batch(prefixed[start : start + batch_size]))
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[Vector]:
        url = f"{self._settings.ollama_url}/api/embed"
        response = await self._client.post(
            url, json={"model": self._settings.embedding_model, "input": batch}
        )
        if response.status_code != httpx.codes.OK:
            raise EmbeddingResponseError(f"ollama answered {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingResponseError("ollama answered with a non-JSON body") from exc
        parsed = _EmbedResponse.model_validate(body)
        if len(parsed.embeddings) != len(batch):
            raise EmbeddingResponseError(
                f"ollama returned {len(parsed.embeddings)} embeddings for {len(batch)} inputs"
            )
        return parsed.embeddings
