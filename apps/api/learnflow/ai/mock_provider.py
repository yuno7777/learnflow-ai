"""Deterministic offline AI provider and embedding generator for tests and local development.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file provides `MockLLM` and `MockEmbeddings`. Unlike a naive dummy stub that
returns "lorem ipsum", this provider is an intelligent deterministic test double:
1. JSON Schema Synthesis (`_Synth`): Inspects Pydantic JSON schemas, resolves `$ref`
   definitions, respects `minItems`, `minLength`, and regex patterns, and synthesizes
   valid structured data for courses, quizzes, and evaluations.
2. Quiz Coherence Pass (`_coherent`): Ensures multiple-choice questions have exactly
   4 unique options and that `correct_answer` is actually in `options`.
3. Hashed Bag-of-Words Embeddings (`MockEmbeddings`): Projects non-stopword tokens
   into a 384-dimensional unit vector using MD5 hashing. Semantically overlapping
   texts produce higher cosine similarity scores, enabling full RAG testing offline.

===============================================================================
WHY THIS FILE IS CRITICAL FOR THE PROJECT:
===============================================================================
- Allows the entire platform, backend test suite (59 test files), and local dev
  to run 100% offline without requiring paid third-party API keys.
- Guarantees deterministic test outcomes across CI/CD pipelines.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from typing import Any

from .base import ChatMessage, Completion, EmbeddingResult

_WORD = re.compile(r"[a-z0-9']+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_ALTERNATION = re.compile(r"^\^?\(([^)]+)\)\$?$")

# Function stopwords to exclude from mock vector hashing.
# Excluding common words ensures that random text doesn't match on common English words.
_STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how
    i if in into is it its may might must not of on or should so than that the their then
    there these they this those to was were what when where which who why will with would
    you your""".split()
)


def _seed(text: str) -> int:
    """Generates a deterministic random seed integer from input text using SHA-256."""
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


def _sentence(rng: random.Random, words: list[str], n: int) -> str:
    """Generates a pseudo-sentence of `n` words chosen from the vocabulary pool."""
    pool = words or ["training", "policy", "process", "compliance", "review", "module"]
    return " ".join(rng.choice(pool) for _ in range(n)).capitalize() + "."


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Recursively resolves JSON schema `$ref` pointers (e.g. `#/definitions/Question`)."""
    seen = 0
    while "$ref" in schema and seen < 10:
        pointer = schema["$ref"].lstrip("#/").split("/")
        target: Any = root
        for part in pointer:
            target = target[part]
        schema = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
        seen += 1
    if "allOf" in schema and len(schema["allOf"]) == 1:
        schema = {**_resolve(schema["allOf"][0], root), **schema}
    return schema


class _Synth:
    """Recursively synthesizes valid JSON payloads satisfying a Pydantic JSON schema."""

    def __init__(self, root: dict[str, Any], rng: random.Random, source: str) -> None:
        self.root = root
        self.rng = rng
        self.words = _WORD.findall(source.lower())[-500:]
        self.sentences = [s.strip() for s in _SENTENCE.split(source) if len(s.strip()) > 25][-40:]

    def _string(self, schema: dict[str, Any]) -> str:
        """Synthesizes a string matching enum values, regex patterns, or length constraints."""
        if "enum" in schema:
            return self.rng.choice(schema["enum"])
        pattern = schema.get("pattern")
        if pattern:
            match = _ALTERNATION.match(pattern)
            if match:
                return self.rng.choice(match.group(1).split("|"))
        minimum = int(schema.get("minLength", 0))
        maximum = int(schema.get("maxLength", 400))
        text = _sentence(self.rng, self.words, self.rng.randint(6, 14))
        while len(text) < minimum:
            text += " " + _sentence(self.rng, self.words, 8)
        return text[:maximum] if maximum >= minimum else text

    def build(self, schema: dict[str, Any]) -> Any:
        """Constructs a python dictionary/list/primitive matching the schema specification."""
        schema = _resolve(schema, self.root)
        for key in ("anyOf", "oneOf"):
            if key in schema:
                options = [s for s in schema[key] if _resolve(s, self.root).get("type") != "null"]
                schema = _resolve(options[0] if options else schema[key][0], self.root)

        kind = schema.get("type", "string")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), "string")

        if "enum" in schema:
            return self.rng.choice(schema["enum"])
        if kind == "object":
            properties: dict[str, Any] = schema.get("properties", {})
            return self._coherent({k: self.build(v) for k, v in properties.items()})
        if kind == "array":
            low = max(int(schema.get("minItems", 2)), 1)
            high = max(low, min(int(schema.get("maxItems", low + 1)), low + 2))
            item = schema.get("items", {"type": "string"})
            return [self.build(item) for _ in range(self.rng.randint(low, high))]
        if kind == "integer":
            low_i = int(schema.get("minimum", schema.get("exclusiveMinimum", 0) + 1 or 1))
            high_i = int(schema.get("maximum", low_i + 4))
            return self.rng.randint(low_i, max(low_i, high_i))
        if kind == "number":
            low_f = float(schema.get("minimum", schema.get("exclusiveMinimum", 0.0)))
            high_f = float(schema.get("maximum", max(low_f + 1.0, 1.0)))
            return round(self.rng.uniform(low_f, high_f), 2)
        if kind == "boolean":
            return self.rng.random() > 0.5
        return self._string(schema)

    def _coherent(self, obj: dict[str, Any]) -> dict[str, Any]:
        """Post-processes generated objects to guarantee internal semantic validity."""
        if "options" in obj and "correct_answer" in obj:
            options = obj.get("options") or []
            if obj.get("type") == "multiple_choice":
                while len(options) < 4:
                    options.append(_sentence(self.rng, self.words, 4))
                options = list(dict.fromkeys(options))[:4]
                obj["options"] = options
                obj["correct_answer"] = self.rng.choice(options)
            elif obj.get("type") == "true_false":
                obj["options"] = ["true", "false"]
                obj["correct_answer"] = self.rng.choice(["true", "false"])
        if "source_reference" in obj and self.sentences:
            obj["source_reference"] = self.rng.choice(self.sentences)[:400]
        if "confidence" in obj and isinstance(obj["confidence"], (int, float)):
            obj["confidence"] = round(self.rng.uniform(0.7, 0.98), 2)
        if obj.get("type") in ("short_answer", "scenario") and "rubric" in obj:
            obj["rubric"] = (
                "Full marks for naming the rule and the approval step; half marks for one of them."
            )
        if "prompt" in obj and isinstance(obj["prompt"], str):
            obj["prompt"] = f"{obj['prompt']} ({self.rng.randint(1000, 9999)})"
        return obj


class MockLLM:
    """Deterministic mock language model implementing the `LLMProvider` protocol."""

    name = "mock"

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str = "mock",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        joined = "\n".join(m.content for m in messages)
        rng = random.Random(_seed(joined + model))

        if json_schema is not None:
            synth = _Synth(json_schema, rng, joined)
            body = json.dumps(synth.build(json_schema), ensure_ascii=False)
        elif "no relevant context" in joined.lower():
            body = "I don't have enough information in the knowledge base to answer that."
        else:
            words = _WORD.findall(joined.lower())[-400:]
            body = " ".join(_sentence(rng, words, rng.randint(8, 16)) for _ in range(3))

        return Completion(
            text=body,
            model=model,
            provider=self.name,
            input_tokens=max(1, len(joined) // 4),
            output_tokens=max(1, len(body) // 4),
        )


class MockEmbeddings:
    """Deterministic mock embedding provider implementing `EmbeddingProvider` protocol."""

    name = "mock"

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        """Projects non-stopword tokens into a normalized unit vector using MD5 hash modulo."""
        vec = [0.0] * self.dimensions
        tokens = [t for t in _WORD.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2]
        for token in tokens:
            h = int(hashlib.md5(token.encode(), usedforsecurity=False).hexdigest(), 16)
            vec[h % self.dimensions] += 1.0
            vec[(h >> 16) % self.dimensions] += 0.5  # Secondary hash to smooth collisions
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: list[str], *, model: str = "mock-embed") -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[self._vector(t) for t in texts],
            model=model,
            provider=self.name,
            input_tokens=sum(max(1, len(t) // 4) for t in texts),
        )
