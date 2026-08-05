"""File validation + text extraction. Returns page-aware segments so citations can be exact."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..errors import ValidationFailed

SUPPORTED: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain", "text/x-markdown"},
    ".csv": {"text/csv", "application/csv", "text/plain"},
}

# First bytes that must be present for the extension to be believable.
_MAGIC = {".pdf": b"%PDF-", ".docx": b"PK\x03\x04"}


@dataclass(slots=True)
class Segment:
    text: str
    page: int | None = None
    section: str | None = None


def validate_upload(filename: str, content_type: str, data: bytes, max_bytes: int) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValidationFailed(
            f"Unsupported file type '{suffix}'", {"supported": sorted(SUPPORTED)}
        )
    if not data:
        raise ValidationFailed("File is empty")
    if len(data) > max_bytes:
        raise ValidationFailed("File too large", {"size_bytes": len(data), "max_bytes": max_bytes})
    magic = _MAGIC.get(suffix)
    if magic and not data.startswith(magic):
        # Extension says one thing, bytes say another — treat as hostile, not as a typo.
        raise ValidationFailed(f"File content does not match its '{suffix}' extension")
    if content_type and content_type.split(";")[0].strip() not in SUPPORTED[suffix]:
        raise ValidationFailed(
            f"Content-Type '{content_type}' does not match '{suffix}'",
            {"expected": sorted(SUPPORTED[suffix])},
        )
    return suffix


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract(data: bytes, suffix: str) -> list[Segment]:
    if suffix == ".pdf":
        return _pdf(data)
    if suffix == ".docx":
        return _docx(data)
    if suffix == ".csv":
        return _csv(data)
    return _markdown(data.decode("utf-8", errors="replace"))


def _pdf(data: bytes) -> list[Segment]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValidationFailed("PDF is password protected") from exc
    segments = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            segments.append(Segment(text=text, page=i))
    if not segments:
        raise ValidationFailed("No extractable text in PDF (is it a scan? OCR is not supported)")
    return segments


def _docx(data: bytes) -> list[Segment]:
    import docx

    document = docx.Document(io.BytesIO(data))
    segments: list[Segment] = []
    section: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            segments.append(Segment(text="\n".join(buffer), section=section))
            buffer.clear()

    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if (para.style is not None) and para.style.name.startswith("Heading"):
            flush()
            section = text
        buffer.append(text)
    flush()
    for table in document.tables:
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        if rows:
            segments.append(Segment(text="\n".join(rows), section=section))
    if not segments:
        raise ValidationFailed("No extractable text in DOCX")
    return segments


def _csv(data: bytes) -> list[Segment]:
    text = data.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise ValidationFailed("CSV is empty")
    header, *body = rows
    # Row-per-segment with the header inlined: keeps each row self-describing after chunking.
    return [
        Segment(text="; ".join(f"{h}: {v}" for h, v in zip(header, row, strict=False)))
        for row in body
        if any(cell.strip() for cell in row)
    ] or [Segment(text=" | ".join(header))]


def _markdown(text: str) -> list[Segment]:
    segments: list[Segment] = []
    section: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if buffer:
                segments.append(Segment(text="\n".join(buffer).strip(), section=section))
                buffer = []
            section = line.lstrip("#").strip()
        buffer.append(line)
    if buffer:
        segments.append(Segment(text="\n".join(buffer).strip(), section=section))
    cleaned = [s for s in segments if s.text]
    if not cleaned:
        raise ValidationFailed("File contains no text")
    return cleaned


_WS = re.compile(r"[ \t]+")
_NL = re.compile(r"\n{3,}")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def clean(text: str) -> str:
    """Normalise unicode, join hyphen-broken words, collapse whitespace, drop control chars."""
    text = unicodedata.normalize("NFKC", text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")
    text = _WS.sub(" ", text)
    return _NL.sub("\n\n", text).strip()


def detect_language(text: str) -> str:
    """ponytail: stopword vote, not a language model. Swap for `langdetect` if non-EN matters."""
    sample = set(re.findall(r"[a-zà-ÿ]+", text.lower())[:400])
    hints = {
        "en": {"the", "and", "of", "to", "in", "is"},
        "es": {"el", "la", "de", "que", "y", "en"},
        "fr": {"le", "de", "et", "les", "des", "une"},
        "de": {"der", "die", "und", "das", "ist", "mit"},
    }
    scored = {lang: len(sample & words) for lang, words in hints.items()}
    best = max(scored, key=lambda k: scored[k])
    return best if scored[best] >= 2 else "unknown"
