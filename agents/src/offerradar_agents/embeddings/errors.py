from __future__ import annotations

import httpx
from pydantic import ValidationError


class EmbeddingResponseError(RuntimeError):
    pass


# A stopped Ollama surfaces as httpx, a malformed body as ValidationError: catch all three.
EMBEDDING_ERRORS = (httpx.HTTPError, ValidationError, EmbeddingResponseError)
