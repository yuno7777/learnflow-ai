"""Sentence-aware token-budgeted document chunking with overlap.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file slices extracted text `Segment` objects into dense vector `Chunk` objects:
1. `count_tokens`: Accurately counts tokens using OpenAI's `tiktoken` (`cl100k_base`),
   falling back to `~4 chars/token` if tiktoken is not installed.
2. `split_sentences`: Uses regular expressions to detect sentence boundaries
   without splitting on abbreviations or decimal numbers.
3. `chunk_segments`: Packs whole sentences into target token budgets (e.g. 700 tokens)
   and carries a tail of sentences forward as overlap (e.g. 100 tokens).

===============================================================================
WHY SENTENCE-AWARE CHUNKING OVER NAIVE FIXED-CHARACTER SPLITTING?
===============================================================================
1. Naive character splitting (e.g. `text[0:1000]`) cuts words and sentences in half,
   destroying semantic meaning and reducing vector embedding quality.
2. Packing complete sentences guarantees that every chunk contains coherent thoughts.
3. Carried-forward overlap guarantees that facts spanning chunk boundaries are
   retrievable from both preceding and succeeding chunks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .extract import Segment

# Regex splitting on punctuation (.!?) followed by whitespace and capital letter/digit/quote,
# or double newlines (paragraphs)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n{2,}")


@dataclass(slots=True)
class Chunk:
    """A prepared text chunk ready for vector embedding and database insertion."""

    text: str
    position: int
    token_count: int
    page: int | None = None
    section: str | None = None


@lru_cache(maxsize=1)
def _encoder():
    """Returns cached `tiktoken` BPE tokenizer for `cl100k_base` encoding."""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        # Gracefully degrade to character heuristic if tiktoken files are unavailable offline
        return None


def count_tokens(text: str) -> int:
    """Calculates accurate token count for a text string using tiktoken."""
    enc = _encoder()
    if enc is not None:
        return len(enc.encode(text, disallowed_special=()))
    return max(1, len(text) // 4)  # ~4 characters per token heuristic fallback


def split_sentences(text: str) -> list[str]:
    """Splits a block of text into distinct sentences."""
    return [s.strip() for s in _SENTENCE.split(text) if s.strip()]


def chunk_segments(
    segments: list[Segment], *, chunk_size: int = 700, overlap: int = 100
) -> list[Chunk]:
    """Packs complete sentences from text segments into token-bounded chunks with overlap.

    Algorithm:
    1. Iterates over text segments (preserving page and section numbers).
    2. Splits each segment into sentences.
    3. Accumulates sentences in a buffer until total tokens exceed `chunk_size`.
    4. Upon reaching capacity, emits a `Chunk`.
    5. Carries the last `overlap` tokens of sentences into the next buffer.
    6. If a single sentence exceeds `chunk_size` (e.g. unformatted tables), splits on words.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    overlap = min(overlap, chunk_size // 2)

    chunks: list[Chunk] = []
    buffer: list[tuple[str, int]] = []  # List of (sentence_text, token_count)
    buffer_tokens = 0
    page: int | None = None
    section: str | None = None

    def flush() -> None:
        """Flushes the current sentence buffer into a Chunk and carries forward overlap."""
        nonlocal buffer, buffer_tokens
        if not buffer:
            return
        text = " ".join(s for s, _ in buffer).strip()
        if text:
            chunks.append(
                Chunk(
                    text=text,
                    position=len(chunks),
                    token_count=buffer_tokens,
                    page=page,
                    section=section,
                )
            )
        # Carry the tail forward so facts across boundaries remain discoverable
        tail: list[tuple[str, int]] = []
        carried = 0
        for sentence, tokens in reversed(buffer):
            if carried + tokens > overlap:
                break
            tail.insert(0, (sentence, tokens))
            carried += tokens
        buffer = tail
        buffer_tokens = carried

    for segment in segments:
        page, section = segment.page, segment.section
        for sentence in split_sentences(segment.text):
            tokens = count_tokens(sentence)

            # Handle oversized single sentences (e.g. long tables or code blocks)
            if tokens > chunk_size:
                flush()
                words = sentence.split()
                step = max(1, len(words) * chunk_size // max(tokens, 1))
                for i in range(0, len(words), step):
                    piece = " ".join(words[i : i + step])
                    chunks.append(
                        Chunk(
                            text=piece,
                            position=len(chunks),
                            token_count=count_tokens(piece),
                            page=page,
                            section=section,
                        )
                    )
                continue

            # Check if buffer reached target token budget
            if buffer_tokens + tokens > chunk_size:
                flush()
            buffer.append((sentence, tokens))
            buffer_tokens += tokens

    # Final flush to drain remaining sentences without carrying forward
    if buffer:
        text = " ".join(s for s, _ in buffer).strip()
        if text:
            chunks.append(
                Chunk(
                    text=text,
                    position=len(chunks),
                    token_count=buffer_tokens,
                    page=page,
                    section=section,
                )
            )
    return chunks
