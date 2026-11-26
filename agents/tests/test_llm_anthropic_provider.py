from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from offerradar_agents.config import Settings
from offerradar_agents.llm.anthropic_provider import AnthropicProvider
from offerradar_agents.llm.errors import LLMError, LLMResponseError, RetryableLLMError
from offerradar_agents.llm.provider import LLMRequest, ToolSpec

KEY = "sk-ant-test-not-real"
TOOL = ToolSpec(name="score", description="score a job", input_schema={"type": "object"})
REQUEST = LLMRequest(
    model="claude-haiku-5-5",
    system="sys",
    cached_context="resume",
    user="posting",
    tool=TOOL,
    max_tokens=321,
)


def message_body(
    content: list[dict[str, Any]] | None = None,
    stop_reason: str = "tool_use",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": content
        if content is not None
        else [{"type": "tool_use", "id": "tu_1", "name": "score", "input": {"fit": 4}}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }


def error_body(message: str = "boom", details: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"type": "api_error", "message": message}
    if details is not None:
        error["details"] = details
    return {"type": "error", "error": error}


def make_provider(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> AnthropicProvider:
    client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    settings = Settings(ANTHROPIC_API_KEY=KEY)  # type: ignore[arg-type]
    return AnthropicProvider(settings=settings, http_client=client)


def respond(status: int, body: dict[str, Any]) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda request: httpx2.Response(status, json=body)


async def test_request_shape_and_usage() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        usage = {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 200,
        }
        return httpx2.Response(200, json=message_body(usage=usage))

    async with make_provider(handler) as provider:
        assert provider.name == "anthropic"
        response = await provider.complete(REQUEST)

    sent = seen[0]
    assert sent["max_tokens"] == 321
    assert sent["tool_choice"] == {"type": "tool", "name": "score"}
    assert sent["tools"][0]["strict"] is True
    assert "cache_control" not in sent["system"][0]
    assert sent["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in sent
    assert response.tool_input == {"fit": 4}
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert response.usage.cache_read_tokens == 100
    assert response.usage.cache_write_tokens == 200


async def test_missing_cache_fields_default_to_zero() -> None:
    async with make_provider(respond(200, message_body())) as provider:
        response = await provider.complete(REQUEST)
    assert response.usage.cache_read_tokens == 0
    assert response.usage.cache_write_tokens == 0


async def test_refusal_raises_response_error() -> None:
    body = message_body(content=[], stop_reason="refusal")
    async with make_provider(respond(200, body)) as provider:
        with pytest.raises(LLMResponseError):
            await provider.complete(REQUEST)


async def test_no_tool_block_raises_response_error() -> None:
    body = message_body(content=[{"type": "text", "text": "hi"}], stop_reason="end_turn")
    async with make_provider(respond(200, body)) as provider:
        with pytest.raises(LLMResponseError):
            await provider.complete(REQUEST)


@pytest.mark.parametrize("status", [429, 500, 529])
async def test_retryable_statuses(status: int) -> None:
    async with make_provider(respond(status, error_body())) as provider:
        with pytest.raises(RetryableLLMError) as info:
            await provider.complete(REQUEST)
    assert info.value.status == status


async def test_spend_cap_429_is_not_retryable() -> None:
    body = error_body(details={"error_code": "enforced_spend_limit_reached"})
    async with make_provider(respond(429, body)) as provider:
        with pytest.raises(LLMError) as info:
            await provider.complete(REQUEST)
    assert not isinstance(info.value, RetryableLLMError)
    assert info.value.status == 429


async def test_unauthorized_is_not_retryable() -> None:
    async with make_provider(respond(401, error_body("bad key"))) as provider:
        with pytest.raises(LLMError) as info:
            await provider.complete(REQUEST)
    assert not isinstance(info.value, RetryableLLMError)
    assert info.value.status == 401


async def test_connection_error_is_retryable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    async with make_provider(handler) as provider:
        with pytest.raises(RetryableLLMError) as info:
            await provider.complete(REQUEST)
    assert info.value.status is None


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_key_never_leaks_into_errors(status: int) -> None:
    async with make_provider(respond(status, error_body())) as provider:
        with pytest.raises(LLMError) as info:
            await provider.complete(REQUEST)
    error = info.value
    assert error.__cause__ is None
    assert error.__suppress_context__
    for text in (str(error), repr(error)):
        assert KEY not in text
        assert "x-api-key" not in text.lower()


def test_missing_key_raises() -> None:
    with pytest.raises(LLMError):
        AnthropicProvider(settings=Settings())
