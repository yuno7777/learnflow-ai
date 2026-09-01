"""OpenAI, Azure OpenAI, and OpenAI-compatible API adapter.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file implements `OpenAICompatibleLLM` and `OpenAICompatibleEmbeddings` using raw `httpx`.
It supports:
1. Standard OpenAI API (`https://api.openai.com/v1`).
2. Azure OpenAI deployments (`https://{resource}.openai.azure.com/openai/deployments/...`).
3. Local/self-hosted inference engines that expose the OpenAI-compatible REST API
   (e.g. vLLM, LM Studio, Hugging Face TGI, LocalAI).

===============================================================================
WHY RAW HTTPX INSTEAD OF THE OFFICIAL OPENAI PYTHON SDK?
===============================================================================
1. A single unified code path supports OpenAI, Azure, and third-party local endpoints
   without instantiating 3 different client objects or managing SDK version breaks.
2. Direct control over async connection pooling, timeouts, and `Retry-After` header extraction.
3. Clean error categorization: transient network/rate-limit issues raise `ProviderUnavailable`,
   while invalid client requests (4xx) raise `ValueError` to avoid wasting retries.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import settings
from .base import (
    ChatMessage,
    Completion,
    EmbeddingResult,
    ProviderRateLimited,
    ProviderUnavailable,
    ToolCall,
)

# HTTP status codes that indicate transient issues worth retrying with exponential backoff
_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class OpenAICompatibleLLM:
    """LLM client adapter for OpenAI, Azure OpenAI, and OpenAI-compatible endpoints."""

    def __init__(
        self,
        name: str = "openai",
        base_url: str | None = None,
        api_key: str | None = None,
        azure: bool = False,
    ) -> None:
        self.name = name
        self.azure = azure
        self.base_url = (base_url or settings.openai_base_url).rstrip("/")
        self.api_key = api_key or settings.openai_api_key
        if not self.api_key:
            raise ProviderUnavailable(f"{name}: no API key configured")

    def _url(self, path: str, model: str) -> str:
        """Constructs API endpoint URL depending on whether Azure OpenAI or standard OpenAI is used."""
        if self.azure:
            return (
                f"{self.base_url}/openai/deployments/{model}/{path}"
                f"?api-version={settings.azure_openai_api_version}"
            )
        return f"{self.base_url}/{path}"

    def _headers(self) -> dict[str, str]:
        """Constructs authentication headers (Azure uses `api-key`, standard uses `Authorization: Bearer`)."""
        return (
            {"api-key": self.api_key or ""}
            if self.azure
            else {"Authorization": f"Bearer {self.api_key}"}
        )

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """Executes async HTTP POST request with timeout and error classification."""
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                resp = await client.post(url, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"{self.name}: {exc}") from exc

        # Handle 429 Rate Limiting
        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            raise ProviderRateLimited(
                f"{self.name}: rate limited", float(retry_after) if retry_after else None
            )
        # Handle transient server/gateway errors
        if resp.status_code in _RETRYABLE:
            raise ProviderUnavailable(f"{self.name}: HTTP {resp.status_code}")
        # Handle permanent client errors (400, 401, 403, 404)
        if resp.status_code >= 400:
            raise ValueError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:400]}")
        return resp.json()

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        """Sends chat completion request to the upstream API and formats standard `Completion`."""
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.to_api() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
            # Enforce structured output via OpenAI response_format json_schema
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": False, "schema": json_schema},
            }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        data = await self._post(self._url("chat/completions", model), body)
        choice = data["choices"][0]
        message = choice["message"]
        
        # Parse function calling tool calls if returned by model
        calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=json.loads(tc["function"]["arguments"] or "{}"),
            )
            for tc in message.get("tool_calls") or []
        ]
        usage = data.get("usage") or {}
        return Completion(
            text=message.get("content") or "",
            model=data.get("model", model),
            provider=self.name,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=calls,
        )


class OpenAICompatibleEmbeddings(OpenAICompatibleLLM):
    """Dense vector embedding client implementing the `EmbeddingProvider` protocol."""

    def __init__(self, *args: Any, dimensions: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.dimensions = dimensions or settings.embedding_dimensions

    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResult:
        """Calls the `/embeddings` endpoint and sorts returned vector objects by index."""
        data = await self._post(self._url("embeddings", model), {"model": model, "input": texts})
        vectors = [item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"])]
        return EmbeddingResult(
            vectors=vectors,
            model=data.get("model", model),
            provider=self.name,
            input_tokens=(data.get("usage") or {}).get("prompt_tokens", 0),
        )
