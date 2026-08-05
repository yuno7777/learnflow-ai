"""Sentence-aware, token-budgeted chunking with overlap.

Splitting on token count alone cuts mid-sentence and wrecks retrieval quality; splitting on
sentences alone produces wildly uneven chunks. This packs whole sentences up to a token
budget and carries a tail of the previous chunk forward as overlap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .extract import Segment

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n{2,}")


@dataclass(slots=True)
class Chunk:
    text: str
    position: int
    token_count: int
    page: int | None = None
    section: str | None = None


@lru_cache(maxsize=1)
def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # tiktoken needs a download on first use; degrade instead of failing
        return None


def count_tokens(text: str) -> int:
    enc = _encoder()
    if enc is not None:
        return len(enc.encode(text, disallowed_special=()))
    return max(1, len(text) // 4)  # ~4 chars/token is close enough for budgeting


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s.strip()]


def chunk_segments(
    segments: list[Segment], *, chunk_size: int = 700, overlap: int = 100
) -> list[Chunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    overlap = min(overlap, chunk_size // 2)

    chunks: list[Chunk] = []
    buffer: list[tuple[str, int]] = []  # (sentence, tokens)
    buffer_tokens = 0
    page: int | None = None
    section: str | None = None

    def flush() -> None:
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
        # Carry the tail forward so a fact spanning a boundary is retrievable from both sides.
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
            if tokens > chunk_size:
                # A single monster sentence (tables, minified text): hard-split on words.
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
            if buffer_tokens + tokens > chunk_size:
                flush()
            buffer.append((sentence, tokens))
            buffer_tokens += tokens

    # Final flush must not re-seed overlap, so drain directly.
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
