"""File validation, magic-byte inspection, text extraction, and text cleaning.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file handles the parsing and cleaning stages of the document ingestion pipeline:
1. `validate_upload`: Verifies file extension, size limit, and **magic bytes**
   (e.g. `%PDF-` for PDFs, `PK\x03\x04` for DOCX) to reject spoofed or hostile uploads.
2. `extract`: Multi-format text extractor producing page-aware `Segment` objects:
   - PDF: Uses `pypdf.PdfReader` preserving page numbers.
   - DOCX: Uses `docx.Document` preserving heading section titles and table rows.
   - CSV: Converts tabular rows into self-describing key-value segments.
   - Markdown/TXT: Preserves heading hierarchy.
3. `clean`: Cleans raw text (Unicode NFKC normalization, hyphenated line-break repair,
   control character removal, whitespace collapsing).
4. `detect_language`: Lightweight stopword voting heuristic for language inference.

===============================================================================
WHY MAGIC-BYTE VALIDATION MATTERS (Security):
===============================================================================
Relying solely on file extensions (e.g. `document.pdf`) or browser-supplied
`Content-Type` headers is dangerous. Attackers can upload malicious binaries or scripts
disguised with a `.pdf` extension. Checking magic bytes (`b"%PDF-"`) guarantees that
the underlying binary structure actually matches the claimed format.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..errors import ValidationFailed

# Allowed file extensions and corresponding MIME types
SUPPORTED: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain", "text/x-markdown"},
    ".csv": {"text/csv", "application/csv", "text/plain"},
}

# Binary magic byte signatures required for specific file extensions
_MAGIC = {".pdf": b"%PDF-", ".docx": b"PK\x03\x04"}


@dataclass(slots=True)
class Segment:
    """A extracted section of text with optional page number and section heading."""
    text: str
    page: int | None = None
    section: str | None = None


def validate_upload(filename: str, content_type: str, data: bytes, max_bytes: int) -> str:
    """Validates file suffix, byte size, magic bytes, and MIME types.

    Returns:
        str: Validated file extension (e.g. ".pdf").
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValidationFailed(
            f"Unsupported file type '{suffix}'", {"supported": sorted(SUPPORTED)}
        )
    if not data:
        raise ValidationFailed("File is empty")
    if len(data) > max_bytes:
        raise ValidationFailed("File too large", {"size_bytes": len(data), "max_bytes": max_bytes})
    
    # Check binary header bytes
    magic = _MAGIC.get(suffix)
    if magic and not data.startswith(magic):
        raise ValidationFailed(f"File content does not match its '{suffix}' extension")
    
    # Check MIME content type if provided
    if content_type and content_type.split(";")[0].strip() not in SUPPORTED[suffix]:
        raise ValidationFailed(
            f"Content-Type '{content_type}' does not match '{suffix}'",
            {"expected": sorted(SUPPORTED[suffix])},
        )
    return suffix


def checksum(data: bytes) -> str:
    """Computes SHA-256 hexadecimal checksum of file bytes for deduplication."""
    return hashlib.sha256(data).hexdigest()


def extract(data: bytes, suffix: str) -> list[Segment]:
    """Routes file bytes to format-specific text extraction parsers."""
    if suffix == ".pdf":
        return _pdf(data)
    if suffix == ".docx":
        return _docx(data)
    if suffix == ".csv":
        return _csv(data)
    return _markdown(data.decode("utf-8", errors="replace"))


def _pdf(data: bytes) -> list[Segment]:
    """Extracts text page-by-page from PDF files using `pypdf`."""
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
    """Extracts text, headings, and tables from Word DOCX files using `docx`."""
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
        # Heading styles split text into logical sections
        if (para.style is not None) and para.style.name.startswith("Heading"):
            flush()
            section = text
        buffer.append(text)
    flush()
    # Extract table rows formatted as markdown pipe tables
    for table in document.tables:
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        if rows:
            segments.append(Segment(text="\n".join(rows), section=section))
    if not segments:
        raise ValidationFailed("No extractable text in DOCX")
    return segments


def _csv(data: bytes) -> list[Segment]:
    """Extracts CSV rows into self-contained key-value string segments."""
    text = data.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise ValidationFailed("CSV is empty")
    header, *body = rows
    # Inlines column headers on every row so chunks retain semantic context after splitting
    return [
        Segment(text="; ".join(f"{h}: {v}" for h, v in zip(header, row, strict=False)))
        for row in body
        if any(cell.strip() for cell in row)
    ] or [Segment(text=" | ".join(header))]


def _markdown(text: str) -> list[Segment]:
    """Extracts Markdown/TXT content, preserving markdown `#` headers as section titles."""
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
    """Cleans and normalizes text for embedding and LLM comprehension.

    Steps:
    1. Normalizes Unicode characters to standard NFKC representation.
    2. Recombines words broken across lines by hyphens (e.g. "con-\nnect" -> "connect").
    3. Drops non-printable ASCII control characters.
    4. Collapses redundant spaces, tabs, and excess blank lines.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")
    text = _WS.sub(" ", text)
    return _NL.sub("\n\n", text).strip()


def detect_language(text: str) -> str:
    """Heuristic language detection using stopword intersection counts."""
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
