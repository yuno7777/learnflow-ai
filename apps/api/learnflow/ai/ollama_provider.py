"""Local Ollama AI provider adapter.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file implements `OllamaLLM` and `OllamaEmbeddings` for self-hosted local model
inference (e.g. Llama 3, Mistral, Qwen, Nomic Embed Text).

===============================================================================
WHY NATIVE OLLAMA /api ENDPOINTS INSTEAD OF OPENAI COMPATIBILITY ROUTE?
===============================================================================
While Ollama offers an `/v1/chat/completions` endpoint, its native `/api/chat` endpoint
directly accepts JSON Schema definitions inside the `"format"` parameter (Ollama >= 0.5),
enabling strict constrained sampling on local hardware with zero external dependencies.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..config import settings
from .base import ChatMessage, Completion, EmbeddingResult, ProviderUnavailable


class OllamaLLM:
    """Adapter for self-hosted Ollama local LLM instances."""
    name = "ollama"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Sends HTTP POST to Ollama native API with timeout."""
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                resp = await client.post(f"{self.base_url}{path}", json=body)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"ollama: {exc}") from exc

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
        """Invokes Ollama's `/api/chat` endpoint with temperature and schema formatting."""
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.to_api() for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_schema is not None:
            # Native Ollama JSON schema constraint
            body["format"] = json_schema
        data = await self._post("/api/chat", body)
        return Completion(
            text=(data.get("message") or {}).get("content", ""),
            model=model,
            provider=self.name,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            finish_reason=data.get("done_reason", "stop"),
        )


class OllamaEmbeddings(OllamaLLM):
    """Adapter for Ollama local vector embedding models (e.g. `nomic-embed-text`)."""
    name = "ollama"

    def __init__(self, base_url: str | None = None, dimensions: int | None = None) -> None:
        super().__init__(base_url)
        self.dimensions = dimensions or settings.embedding_dimensions

    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResult:
        """Invokes Ollama's `/api/embed` endpoint."""
        data = await self._post("/api/embed", {"model": model, "input": texts})
        return EmbeddingResult(
            vectors=data["embeddings"],
            model=model,
            provider=self.name,
            input_tokens=data.get("prompt_eval_count", 0),
        )
