"""The single choke point for every model call in the platform.

Nothing else in the codebase talks to a provider directly. That buys us, in one place:
retries with backoff, provider fallback, structured-output validation with one repair
attempt, token accounting, cost estimation and an observability row per attempt.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..errors import ProviderError
from ..logging import get_logger
from ..models import LLMCall, UsageRecord
from .base import ChatMessage, Completion, ProviderRateLimited, ProviderUnavailable
from .pricing import estimate_cost
from .prompts import Prompt
from .registry import get_embedder, get_llm

log = get_logger(__name__)
M = TypeVar("M", bound=BaseModel)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(slots=True)
class AIResult:
    text: str
    parsed: Any | None
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_usd: float
    used_fallback: bool
    retry_count: int
    call_id: uuid.UUID | None = None
    tool_calls: list[Any] = field(default_factory=list)


def _extract_json(text: str) -> str:
    """Models wrap JSON in prose or fences more often than they admit."""
    fenced = _JSON_BLOCK.search(text)
    if fenced:
        return fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


class AIGateway:
    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.user_id = user_id
        self.request_id = request_id
        # Set by the workflow engine so every call made during a run is attributable to it.
        self.workflow_execution_id: uuid.UUID | None = None

    # ------------------------------------------------------------------ public
    async def complete(
        self,
        operation: str,
        messages: list[ChatMessage],
        *,
        prompt: Prompt | None = None,
        schema: type[M] | None = None,
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict[str, Any]] | None = None,
        retrieved_chunk_ids: list[str] | None = None,
    ) -> AIResult:
        model = model or settings.llm_model
        json_schema = schema.model_json_schema() if schema else None
        started = time.perf_counter()
        attempts = 0
        used_fallback = False
        schema_failed = False
        last_error: Exception | None = None

        for provider_name in self._provider_chain(provider):
            used_fallback = provider_name != (provider or settings.llm_provider)
            for attempt in range(settings.llm_max_retries):
                attempts += 1
                try:
                    client = get_llm(provider_name)
                    completion = await client.complete(
                        messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_schema=json_schema,
                        tools=tools,
                    )
                    parsed = None
                    if schema is not None:
                        parsed, repaired = await self._parse_or_repair(
                            completion, schema, client, messages, model, temperature, max_tokens
                        )
                        schema_failed = repaired
                        if repaired:
                            attempts += 1
                    return await self._record(
                        operation=operation,
                        completion=completion,
                        parsed=parsed,
                        prompt=prompt,
                        started=started,
                        retry_count=attempts - 1,
                        used_fallback=used_fallback,
                        schema_failed=schema_failed,
                        retrieved_chunk_ids=retrieved_chunk_ids or [],
                        success=True,
                        error=None,
                    )
                except ProviderRateLimited as exc:
                    last_error = exc
                    await asyncio.sleep(exc.retry_after or (2**attempt))
                except (ProviderUnavailable, ValidationError, ValueError) as exc:
                    last_error = exc
                    if isinstance(exc, ValueError) and not isinstance(exc, ProviderUnavailable):
                        break  # 4xx / unparseable-after-repair: another attempt won't help
                    await asyncio.sleep(0.25 * (2**attempt))
            log.warning("llm_provider_exhausted", provider=provider_name, operation=operation)

        await self._record(
            operation=operation,
            completion=Completion(text="", model=model, provider=provider or settings.llm_provider),
            parsed=None,
            prompt=prompt,
            started=started,
            retry_count=max(0, attempts - 1),
            used_fallback=used_fallback,
            schema_failed=schema_failed,
            retrieved_chunk_ids=retrieved_chunk_ids or [],
            success=False,
            error=str(last_error)[:1000],
        )
        raise ProviderError(
            f"AI operation '{operation}' failed after {attempts} attempts",
            {"reason": str(last_error)[:300]},
        )

    async def embed(
        self, texts: list[str], *, operation: str = "embed"
    ) -> tuple[list[list[float]], int]:
        embedder = get_embedder()
        model = settings.embedding_model if embedder.name != "mock" else "mock-embed"
        started = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(settings.llm_max_retries):
            try:
                result = await embedder.embed(texts, model=model)
                cost = estimate_cost(result.model, result.input_tokens, 0)
                self.session.add(
                    UsageRecord(
                        organization_id=self.organization_id,
                        kind="embedding",
                        provider=result.provider,
                        model=result.model,
                        operation=operation,
                        input_tokens=result.input_tokens,
                        output_tokens=0,
                        estimated_cost_usd=cost,
                        occurred_at=datetime.now(UTC),
                    )
                )
                log.info(
                    "embedding",
                    operation=operation,
                    count=len(texts),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                return result.vectors, result.input_tokens
            except ProviderUnavailable as exc:
                last_error = exc
                await asyncio.sleep(0.25 * (2**attempt))
        raise ProviderError("Embedding failed", {"reason": str(last_error)[:300]})

    # ------------------------------------------------------------------ internals
    def _provider_chain(self, provider: str | None) -> list[str]:
        chain = [provider or settings.llm_provider]
        if settings.llm_fallback_provider and settings.llm_fallback_provider not in chain:
            chain.append(settings.llm_fallback_provider)
        return chain

    async def _parse_or_repair(
        self,
        completion: Completion,
        schema: type[M],
        client: Any,
        messages: list[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[M, bool]:
        try:
            return schema.model_validate_json(_extract_json(completion.text)), False
        except (ValidationError, json.JSONDecodeError) as exc:
            log.warning("structured_output_invalid", schema=schema.__name__, error=str(exc)[:300])

        repair = [
            *messages,
            ChatMessage(role="assistant", content=completion.text[:4000]),
            ChatMessage(
                role="user",
                content=(
                    "That response did not match the required JSON schema. Return ONLY valid "
                    "JSON conforming to the schema — no prose, no code fences."
                ),
            ),
        ]
        retry = await client.complete(
            repair,
            model=model,
            temperature=0.0,
            max_tokens=max_tokens,
            json_schema=schema.model_json_schema(),
        )
        completion.input_tokens += retry.input_tokens
        completion.output_tokens += retry.output_tokens
        completion.text = retry.text
        # A second failure raises ValidationError, which the caller treats as terminal.
        return schema.model_validate_json(_extract_json(retry.text)), True

    async def _record(
        self,
        *,
        operation: str,
        completion: Completion,
        parsed: Any,
        prompt: Prompt | None,
        started: float,
        retry_count: int,
        used_fallback: bool,
        schema_failed: bool,
        retrieved_chunk_ids: list[str],
        success: bool,
        error: str | None,
    ) -> AIResult:
        latency_ms = int((time.perf_counter() - started) * 1000)
        cost = estimate_cost(completion.model, completion.input_tokens, completion.output_tokens)
        call = LLMCall(
            organization_id=self.organization_id,
            operation=operation,
            provider=completion.provider,
            model=completion.model,
            prompt_name=prompt.name if prompt else None,
            prompt_version=prompt.version if prompt else None,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            success=success,
            retry_count=retry_count,
            used_fallback=used_fallback,
            schema_validation_failed=schema_failed,
            error=error,
            retrieved_chunk_ids=retrieved_chunk_ids,
            tool_calls=[{"name": t.name, "arguments": t.arguments} for t in completion.tool_calls],
            user_id=self.user_id,
            request_id=self.request_id,
            workflow_execution_id=self.workflow_execution_id,
        )
        self.session.add(call)
        if success:
            self.session.add(
                UsageRecord(
                    organization_id=self.organization_id,
                    kind="llm",
                    provider=completion.provider,
                    model=completion.model,
                    operation=operation,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    estimated_cost_usd=cost,
                    occurred_at=datetime.now(UTC),
                )
            )
        await self.session.flush()
        log.info(
            "llm_call",
            operation=operation,
            provider=completion.provider,
            model=completion.model,
            success=success,
            latency_ms=latency_ms,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            retry_count=retry_count,
        )
        return AIResult(
            text=completion.text,
            parsed=parsed,
            model=completion.model,
            provider=completion.provider,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=float(cost),
            used_fallback=used_fallback,
            retry_count=retry_count,
            call_id=call.id,
            tool_calls=completion.tool_calls,
        )
