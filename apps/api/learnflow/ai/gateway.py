"""Central AI Gateway: Single choke point for all LLM and embedding operations.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file is the single gateway through which ALL language model completions and
vector embedding requests must pass. It encapsulates:
1. Automated Retries with Exponential Backoff: Catches rate limits and transient 5xx errors.
2. Provider Fallback Chain: Falls back (e.g. OpenAI -> Local Ollama) if the primary fails.
3. Structured Output Validation & Self-Repair: Automatically parses JSON into Pydantic models.
   If validation fails, fires an automated zero-temperature repair prompt back to the model.
4. Granular Telemetry & Cost Accounting: Writes an `LLMCall` row for every single attempt,
   recording latency in ms, prompt/completion tokens, prompt version, and estimated USD cost.
5. Workflow Attribution: Links LLM calls to active `WorkflowExecution` IDs for billing audits.

===============================================================================
DATA FLOW OF AN AI CALL:
===============================================================================
Caller (e.g. Course Generator)
  ──► `gateway.complete(operation="course_generate", messages, schema=GeneratedCourse)`
  ──► Checks provider chain (`[primary, fallback]`)
  ──► Executes attempt via provider adapter (`openai_provider.py` or `mock_provider.py`)
  ──► Extracts JSON and validates against Pydantic schema
  ──► (If invalid) Fires self-repair prompt with `temperature=0.0`
  ──► Inserts `LLMCall` and `UsageRecord` rows into PostgreSQL
  ──► Returns `AIResult(text, parsed, cost_usd, latency_ms)`
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

# Regex to extract JSON content even if wrapped in markdown code fences (```json ... ```)
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(slots=True)
class AIResult:
    """Unified return type from `AIGateway.complete()`.

    Contains raw text, parsed Pydantic object, token metrics, latency, and telemetry ID.
    """

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
    """Extracts raw JSON substring from model output.

    Handles models wrapping JSON inside markdown fences (```json ... ```)
    or preambles like 'Here is the JSON: {...}'.
    """
    fenced = _JSON_BLOCK.search(text)
    if fenced:
        return fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


class AIGateway:
    """Central gateway orchestrating LLM calls, retries, fallbacks, and observability."""

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
        # Explicit workflow link: set by automation engine to attribute spend to specific executions
        self.workflow_execution_id: uuid.UUID | None = None

    # -------------------------------------------------------------------------
    # Public Completion API
    # -------------------------------------------------------------------------
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
        """Executes a chat completion with retries, fallback provider failover, and Pydantic
        validation.

        Args:
            operation: Audit label (e.g. "rag_answer", "course_generate", "assessment_evaluate").
            messages: List of `ChatMessage` objects representing system/user/assistant context.
            prompt: Optional versioned `Prompt` dataclass for audit tracking.
            schema: Optional Pydantic `BaseModel` class to enforce structured JSON output.
            model: Optional model override (defaults to `settings.llm_model`).
            provider: Optional provider override (defaults to `settings.llm_provider`).
            temperature: Sampling temperature (0.0 to 1.0).
            max_tokens: Max tokens to generate.
            tools: Function calling definitions for assistant tools.
            retrieved_chunk_ids: Vector chunk IDs included in prompt context (for RAG citations).

        Returns:
            AIResult: Normalized result containing text, parsed object, tokens, cost, and latency.
        """
        model = model or settings.llm_model
        json_schema = schema.model_json_schema() if schema else None
        started = time.perf_counter()
        attempts = 0
        used_fallback = False
        schema_failed = False
        last_error: Exception | None = None

        # 1. Iterate through provider chain (Primary -> Fallback)
        for provider_name in self._provider_chain(provider):
            used_fallback = provider_name != (provider or settings.llm_provider)

            # 2. Retry loop with exponential backoff
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

                    # 3. Pydantic validation & self-repair pass
                    parsed = None
                    if schema is not None:
                        parsed, repaired = await self._parse_or_repair(
                            completion, schema, client, messages, model, temperature, max_tokens
                        )
                        schema_failed = repaired
                        if repaired:
                            attempts += 1

                    # 4. Record successful telemetry and return
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
                        # Client input error or unrepairable schema: retrying won't help
                        break
                    await asyncio.sleep(0.25 * (2**attempt))
            log.warning("llm_provider_exhausted", provider=provider_name, operation=operation)

        # 5. All providers and retries failed: log error and raise ProviderError (502)
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

    # -------------------------------------------------------------------------
    # Public Embedding API
    # -------------------------------------------------------------------------
    async def embed(
        self, texts: list[str], *, operation: str = "embed"
    ) -> tuple[list[list[float]], int]:
        """Generates dense vector embeddings for a list of text passages.

        Returns:
            tuple[list[list[float]], int]: (list_of_vectors, total_input_tokens)
        """
        embedder = get_embedder()
        model = settings.embedding_model if embedder.name != "mock" else "mock-embed"
        started = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(settings.llm_max_retries):
            try:
                result = await embedder.embed(texts, model=model)
                cost = estimate_cost(result.model, result.input_tokens, 0)

                # Write billing usage record
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

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------
    def _provider_chain(self, provider: str | None) -> list[str]:
        """Builds ordered provider failover sequence (e.g. `['openai', 'ollama']`)."""
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
        """Attempts to validate model output against Pydantic schema; repairs if invalid.

        If the initial output fails validation, appends the invalid output and an error
        instruction to the conversation and requests a repair with `temperature=0.0`.
        """
        try:
            return schema.model_validate_json(_extract_json(completion.text)), False
        except (ValidationError, json.JSONDecodeError) as exc:
            log.warning("structured_output_invalid", schema=schema.__name__, error=str(exc)[:300])

        # Self-Repair Prompt: Force model to output valid JSON without pleasantries
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
        # If second attempt fails, raises ValidationError terminating the attempt
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
        """Persists granular telemetry to `llm_calls` and rolled-up spend to `usage_records`."""
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
