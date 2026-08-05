"""OpenAI / Azure OpenAI / any OpenAI-compatible endpoint (incl. HF TGI, vLLM, LM Studio).

Raw httpx rather than the SDK: one request shape, and it keeps Azure + HF-compatible hosts
on the same code path instead of three client objects.
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

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class OpenAICompatibleLLM:
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
        if self.azure:
            return (
                f"{self.base_url}/openai/deployments/{model}/{path}"
                f"?api-version={settings.azure_openai_api_version}"
            )
        return f"{self.base_url}/{path}"

    def _headers(self) -> dict[str, str]:
        return (
            {"api-key": self.api_key or ""}
            if self.azure
            else {"Authorization": f"Bearer {self.api_key}"}
        )

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                resp = await client.post(url, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"{self.name}: {exc}") from exc

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            raise ProviderRateLimited(
                f"{self.name}: rate limited", float(retry_after) if retry_after else None
            )
        if resp.status_code in _RETRYABLE:
            raise ProviderUnavailable(f"{self.name}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            # 4xx that isn't rate limiting is our bug — don't burn retries on it.
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
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.to_api() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
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
    def __init__(self, *args: Any, dimensions: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.dimensions = dimensions or settings.embedding_dimensions

    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResult:
        data = await self._post(self._url("embeddings", model), {"model": model, "input": texts})
        vectors = [item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"])]
        return EmbeddingResult(
            vectors=vectors,
            model=data.get("model", model),
            provider=self.name,
            input_tokens=(data.get("usage") or {}).get("prompt_tokens", 0),
        )
