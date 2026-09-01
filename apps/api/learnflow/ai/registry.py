"""Provider factory and adapter instance registry.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file implements the **Factory Pattern** for language models and embedding clients:
1. `get_llm(provider)`: Resolves a provider string ("mock", "openai", "azure_openai",
   "ollama", "huggingface") to a cached concrete `LLMProvider` adapter instance.
2. `get_embedder(provider)`: Resolves an embedding provider string to a cached
   `EmbeddingProvider` instance.
3. `reset_providers()`: Clears the `@lru_cache` so test suites can dynamically
   switch providers between test cases.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `ai/gateway.py`: Calls `get_llm(provider_name)` during completion calls and
  `get_embedder()` during embedding generation.
"""

from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import EmbeddingProvider, LLMProvider, ProviderUnavailable
from .mock_provider import MockEmbeddings, MockLLM
from .ollama_provider import OllamaEmbeddings, OllamaLLM
from .openai_provider import OpenAICompatibleEmbeddings, OpenAICompatibleLLM


@lru_cache(maxsize=8)
def get_llm(provider: str | None = None) -> LLMProvider:
    """Factory creating or returning the cached LLMProvider instance.

    Supported Providers:
    - `mock`: In-memory offline synthesizer (`MockLLM`).
    - `openai`: Standard OpenAI client (`OpenAICompatibleLLM`).
    - `azure_openai`: Azure OpenAI deployment client.
    - `huggingface`: Hugging Face TGI / vLLM endpoint.
    - `ollama`: Local Ollama instance (`OllamaLLM`).
    """
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
    """Factory creating or returning the cached EmbeddingProvider instance.

    Supported Providers:
    - `mock`: Hashed bag-of-words embedder (`MockEmbeddings`).
    - `openai`: `OpenAICompatibleEmbeddings` with `text-embedding-3-small`.
    - `azure_openai`: Azure OpenAI embeddings deployment.
    - `huggingface`: TEI (Text Embeddings Inference) or vLLM embedding server.
    - `ollama`: `OllamaEmbeddings` with local embedding model.
    """
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
    """Clears the LRU cache. Used during unit tests when switching environment configs."""
    get_llm.cache_clear()
    get_embedder.cache_clear()
