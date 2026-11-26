from __future__ import annotations

from typing import Any

import anthropic

from offerradar_agents.config import Settings, get_settings
from offerradar_agents.llm.errors import LLMError, LLMResponseError, RetryableLLMError
from offerradar_agents.llm.provider import LLMRequest, LLMResponse, ProviderName, Usage

SPEND_LIMIT_CODE = "enforced_spend_limit_reached"


def _is_spend_limit(body: object) -> bool:
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    return isinstance(details, dict) and details.get("error_code") == SPEND_LIMIT_CODE


class AnthropicProvider:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: Any = None,
    ) -> None:
        settings = settings if settings is not None else get_settings()
        if settings.anthropic_api_key is None or not settings.anthropic_api_key.get_secret_value():
            raise LLMError("ANTHROPIC_API_KEY is not set")
        kwargs: dict[str, Any] = {}
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_retries=0,
            timeout=settings.llm_timeout_seconds,
            **kwargs,
        )

    @property
    def name(self) -> ProviderName:
        return "anthropic"

    async def __aenter__(self) -> AnthropicProvider:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.close()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            message = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=[
                    {"type": "text", "text": request.system},
                    {
                        "type": "text",
                        "text": request.cached_context,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[{"role": "user", "content": request.user}],
                tools=[
                    {
                        "name": request.tool.name,
                        "description": request.tool.description,
                        "input_schema": dict(request.tool.input_schema),
                        "strict": True,
                    }
                ],
                tool_choice={"type": "tool", "name": request.tool.name},
            )
        except anthropic.RateLimitError as exc:
            if _is_spend_limit(exc.body):
                raise LLMError("anthropic 429: spend limit reached", status=429) from None
            raise RetryableLLMError("anthropic 429: rate limited", status=429) from None
        except anthropic.APIStatusError as exc:
            status = exc.status_code
            if status >= 500:
                raise RetryableLLMError(f"anthropic {status}", status=status) from None
            raise LLMError(f"anthropic {status}", status=status) from None
        except (anthropic.APITimeoutError, anthropic.APIConnectionError):
            raise RetryableLLMError("anthropic connection failed or timed out") from None

        usage = Usage(
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            cache_read_tokens=message.usage.cache_read_input_tokens or 0,
            cache_write_tokens=message.usage.cache_creation_input_tokens or 0,
        )
        if message.stop_reason == "refusal":
            raise LLMResponseError("anthropic refused the request", usage=usage)
        for block in message.content:
            if block.type == "tool_use" and block.name == request.tool.name:
                return LLMResponse(tool_input=dict(block.input), usage=usage)
        raise LLMResponseError(f"anthropic returned no {request.tool.name} tool call", usage=usage)
