from __future__ import annotations

from offerradar_agents.llm.provider import Usage


# Carries a status and a message only: never the SDK request, which holds the API key.
class LLMError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RetryableLLMError(LLMError):
    pass


# A billed reply the caller cannot use; `usage` lets the client still log its cost.
class LLMResponseError(LLMError):
    def __init__(
        self, message: str, *, status: int | None = None, usage: Usage | None = None
    ) -> None:
        super().__init__(message, status=status)
        self.usage = usage


class BudgetExhaustedError(LLMError):
    pass


class ToolNotAllowedError(LLMError):
    pass
