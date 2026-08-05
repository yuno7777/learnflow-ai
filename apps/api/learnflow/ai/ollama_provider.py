"""Local Ollama. Uses the native /api endpoints so `format: json` schema mode works."""

from __future__ import annotations

from typing import Any

import httpx

from ..config import settings
from .base import ChatMessage, Completion, EmbeddingResult, ProviderUnavailable


class OllamaLLM:
    name = "ollama"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
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
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.to_api() for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_schema is not None:
            body["format"] = json_schema  # Ollama >=0.5 accepts a JSON schema here
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
    name = "ollama"

    def __init__(self, base_url: str | None = None, dimensions: int | None = None) -> None:
        super().__init__(base_url)
        self.dimensions = dimensions or settings.embedding_dimensions

    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResult:
        data = await self._post("/api/embed", {"model": model, "input": texts})
        return EmbeddingResult(
            vectors=data["embeddings"],
            model=model,
            provider=self.name,
            input_tokens=data.get("prompt_eval_count", 0),
        )
