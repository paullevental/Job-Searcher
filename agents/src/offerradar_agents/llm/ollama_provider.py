from __future__ import annotations

import json
from typing import Any

import httpx

from offerradar_agents.config import Settings, get_settings
from offerradar_agents.llm.errors import LLMError, LLMResponseError, RetryableLLMError
from offerradar_agents.llm.provider import LLMRequest, LLMResponse, ProviderName, Usage


class OllamaLLMProvider:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(timeout=self._settings.llm_timeout_seconds)
        )

    @property
    def name(self) -> ProviderName:
        return "ollama"

    async def __aenter__(self) -> OllamaLLMProvider:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": request.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": f"{request.system}\n\n{request.cached_context}"},
                {"role": "user", "content": request.user},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": request.tool.name,
                        "description": request.tool.description,
                        "parameters": dict(request.tool.input_schema),
                    },
                }
            ],
            "options": {"num_predict": request.max_tokens},
        }
        try:
            response = await self._client.post(f"{self._settings.ollama_url}/api/chat", json=body)
        except (httpx.TimeoutException, httpx.TransportError):
            raise RetryableLLMError("ollama connection failed or timed out") from None

        status = response.status_code
        if status == 429 or status >= 500:
            raise RetryableLLMError(f"ollama {status}", status=status)
        if status >= 400:
            raise LLMError(f"ollama {status}", status=status)
        try:
            payload = response.json()
        except ValueError:
            raise LLMResponseError("ollama answered with a non-JSON body") from None

        arguments = _tool_arguments(payload, request.tool.name)
        return LLMResponse(
            tool_input=arguments,
            usage=Usage(
                input_tokens=int(payload.get("prompt_eval_count") or 0),
                output_tokens=int(payload.get("eval_count") or 0),
            ),
        )


def _tool_arguments(payload: Any, tool_name: str) -> dict[str, Any]:
    message = payload.get("message") if isinstance(payload, dict) else None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list):
        raise LLMResponseError("ollama returned no tool call")
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or function.get("name") != tool_name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                raise LLMResponseError("ollama tool arguments are not JSON") from None
        if isinstance(arguments, dict):
            return arguments
    raise LLMResponseError(f"ollama returned no {tool_name} tool call")
