from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Protocol

Vector = list[float]

# Both sides are documents (symmetric similarity); never mix with search_query:.
DEFAULT_DOCUMENT_PREFIX: Final = "search_document: "


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[Vector]: ...
