from __future__ import annotations

from offerradar_agents.config import Settings
from offerradar_agents.llm.anthropic_provider import AnthropicProvider
from offerradar_agents.llm.ollama_provider import OllamaLLMProvider
from offerradar_agents.llm.provider import ProviderName


def build_provider(settings: Settings, name: ProviderName) -> AnthropicProvider | OllamaLLMProvider:
    if name == "anthropic":
        return AnthropicProvider(settings=settings)
    return OllamaLLMProvider(settings=settings)


def model_for(settings: Settings, name: ProviderName) -> str:
    return settings.llm_score_model if name == "anthropic" else settings.ollama_llm_model
