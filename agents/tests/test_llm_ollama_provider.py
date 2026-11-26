from __future__ import annotations

import json

import httpx
import pytest
import respx

from offerradar_agents.config import Settings
from offerradar_agents.llm.errors import LLMError, LLMResponseError, RetryableLLMError
from offerradar_agents.llm.ollama_provider import OllamaLLMProvider
from offerradar_agents.llm.provider import LLMRequest, ToolSpec

CHAT_URL = "http://ollama.test:11434/api/chat"
TOOL = ToolSpec(name="score", description="score a job", input_schema={"type": "object"})
REQUEST = LLMRequest(
    model="llama3",
    system="sys",
    cached_context="resume",
    user="posting",
    tool=TOOL,
    max_tokens=123,
)


def make_settings() -> Settings:
    return Settings(OLLAMA_URL="http://ollama.test:11434", LLM_TIMEOUT_SECONDS=5.0)  # type: ignore[arg-type]


def chat_body(arguments: object, name: str = "score") -> dict[str, object]:
    return {
        "message": {
            "role": "assistant",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        },
        "prompt_eval_count": 11,
        "eval_count": 7,
    }


@respx.mock
async def test_tool_call_dict_arguments_and_request_body() -> None:
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_body({"fit": 4})))
    async with OllamaLLMProvider(settings=make_settings()) as provider:
        response = await provider.complete(REQUEST)
        assert provider.name == "ollama"
    assert response.tool_input == {"fit": 4}
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7
    sent = json.loads(route.calls.last.request.content)
    assert sent["options"] == {"num_predict": 123}
    assert sent["stream"] is False
    assert sent["tools"][0]["function"]["name"] == "score"
    assert sent["messages"][0]["content"] == "sys\n\nresume"


@respx.mock
async def test_tool_call_json_string_arguments() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_body('{"fit": 2}')))
    async with OllamaLLMProvider(settings=make_settings()) as provider:
        response = await provider.complete(REQUEST)
    assert response.tool_input == {"fit": 2}


@respx.mock
async def test_no_tool_calls_raises_response_error() -> None:
    body = {"message": {"role": "assistant", "content": '{"fit": 4}'}}
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=body))
    async with OllamaLLMProvider(settings=make_settings()) as provider:
        with pytest.raises(LLMResponseError):
            await provider.complete(REQUEST)


@respx.mock
async def test_wrong_tool_name_raises_response_error() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_body({}, name="other")))
    async with OllamaLLMProvider(settings=make_settings()) as provider:
        with pytest.raises(LLMResponseError):
            await provider.complete(REQUEST)


@respx.mock
async def test_non_json_body_raises_response_error() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, content=b"nope"))
    async with OllamaLLMProvider(settings=make_settings()) as provider:
        with pytest.raises(LLMResponseError):
            await provider.complete(REQUEST)


@pytest.mark.parametrize("status", [429, 500, 503])
@respx.mock
async def test_retryable_statuses(status: int) -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(status))
    async with OllamaLLMProvider(settings=make_settings()) as provider:
        with pytest.raises(RetryableLLMError) as info:
            await provider.complete(REQUEST)
    assert info.value.status == status


@respx.mock
async def test_client_error_is_not_retryable() -> None:
    respx.post(CHAT_URL).mock(return_value=httpx.Response(400))
    async with OllamaLLMProvider(settings=make_settings()) as provider:
        with pytest.raises(LLMError) as info:
            await provider.complete(REQUEST)
    assert not isinstance(info.value, RetryableLLMError)
    assert info.value.status == 400


@respx.mock
async def test_timeout_is_retryable() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    async with OllamaLLMProvider(settings=make_settings()) as provider:
        with pytest.raises(RetryableLLMError):
            await provider.complete(REQUEST)
