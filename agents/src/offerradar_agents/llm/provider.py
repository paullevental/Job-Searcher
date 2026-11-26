from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ProviderName = Literal["anthropic", "ollama"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]


# `cached_context` is the stable prefix (the resume); `user` varies per call.
@dataclass(frozen=True)
class LLMRequest:
    model: str
    system: str
    cached_context: str
    user: str
    tool: ToolSpec
    max_tokens: int


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    tool_input: dict[str, Any]
    usage: Usage


class LLMProvider(Protocol):
    @property
    def name(self) -> ProviderName: ...

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
