"""Maps a provider name from config/DB to a concrete adapter instance."""

from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import EmbeddingProvider, LLMProvider, ProviderUnavailable
from .mock_provider import MockEmbeddings, MockLLM
from .ollama_provider import OllamaEmbeddings, OllamaLLM
from .openai_provider import OpenAICompatibleEmbeddings, OpenAICompatibleLLM


@lru_cache(maxsize=8)
def get_llm(provider: str | None = None) -> LLMProvider:
    name = (provider or settings.llm_provider).lower()
    if name == "mock":
        return MockLLM()
    if name == "openai":
        return OpenAICompatibleLLM("openai")
    if name == "azure_openai":
        if not settings.azure_openai_endpoint:
            raise ProviderUnavailable("azure_openai: AZURE_OPENAI_ENDPOINT not set")
        return OpenAICompatibleLLM("azure_openai", settings.azure_openai_endpoint, azure=True)
    if name == "huggingface":
        if not settings.huggingface_base_url:
            raise ProviderUnavailable("huggingface: HUGGINGFACE_BASE_URL not set")
        return OpenAICompatibleLLM(
            "huggingface", settings.huggingface_base_url, settings.huggingface_api_key
        )
    if name == "ollama":
        return OllamaLLM()
    raise ProviderUnavailable(f"Unknown LLM provider '{name}'")


@lru_cache(maxsize=8)
def get_embedder(provider: str | None = None) -> EmbeddingProvider:
    name = (provider or settings.embedding_provider).lower()
    if name == "mock":
        return MockEmbeddings()
    if name == "openai":
        return OpenAICompatibleEmbeddings("openai")
    if name == "azure_openai":
        return OpenAICompatibleEmbeddings(
            "azure_openai", settings.azure_openai_endpoint, azure=True
        )
    if name == "huggingface":
        return OpenAICompatibleEmbeddings(
            "huggingface", settings.huggingface_base_url, settings.huggingface_api_key
        )
    if name == "ollama":
        return OllamaEmbeddings()
    raise ProviderUnavailable(f"Unknown embedding provider '{name}'")


def reset_providers() -> None:
    """Tests flip settings between cases; the lru_cache has to follow."""
    get_llm.cache_clear()
    get_embedder.cache_clear()
