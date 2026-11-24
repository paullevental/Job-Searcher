from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Final

from offerradar_agents.embeddings.provider import DEFAULT_DOCUMENT_PREFIX, Vector

DEFAULT_DIMENSION: Final = 16


class FakeEmbeddingProvider:
    def __init__(
        self,
        *,
        dimension: int = DEFAULT_DIMENSION,
        document_prefix: str = DEFAULT_DOCUMENT_PREFIX,
    ) -> None:
        self._dimension = dimension
        self._prefix = document_prefix

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self._embed_one(f"{self._prefix}{text}") for text in texts]

    def _embed_one(self, text: str) -> Vector:
        seed = text.encode()
        raw = [self._coordinate(seed, i) for i in range(self._dimension)]
        norm = math.sqrt(sum(v * v for v in raw))
        if norm == 0:
            return raw
        return [v / norm for v in raw]

    @staticmethod
    def _coordinate(seed: bytes, index: int) -> float:
        digest = hashlib.sha256(seed + index.to_bytes(4, "big")).digest()
        unsigned = int.from_bytes(digest[:8], "big") / 2**64
        return unsigned * 2 - 1
