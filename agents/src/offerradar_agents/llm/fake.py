from __future__ import annotations

from offerradar_agents.llm.provider import LLMRequest, LLMResponse, ProviderName


class FakeLLMProvider:
    def __init__(self, name: ProviderName, responses: list[LLMResponse | Exception]) -> None:
        self.name: ProviderName = name
        self.requests: list[LLMRequest] = []
        self._responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
