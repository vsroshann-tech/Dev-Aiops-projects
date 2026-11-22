"""Document parser and section-aware overlapping chunker."""

import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from app.security.redactor import redact_secrets


@dataclass
class TextChunk:
    """A single indexed chunk of an internal document."""

    chunk_index: int
    heading: str
    content: str


_MD_HEADING_RE = re.compile(r"^#{1,4}\s+(.+)$", re.MULTILINE)
_NUM_HEADING_RE = re.compile(r"^(\d+\.\s+[A-Z][^\n]{3,80})$", re.MULTILINE)


def _normalize_pdf_text(text: str) -> str:
    """Normalize ligatures and rejoin hyphenated identifiers wrapped across PDF lines."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n")
    # Rejoin hyphenated identifiers split across PDF table columns/lines (e.g. "acme-\ncorp" -> "acme-corp")
    normalized = re.sub(r"-\s*\n\s*([a-z0-9])", r"-\1", normalized)
    return normalized


def extract_text_from_bytes(filename: str, raw_bytes: bytes) -> str:
    """Extract UTF-8 text from supported document formats (.md, .txt, .pdf, .yaml, .json, .sql)."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(io.BytesIO(raw_bytes))
            pages = [_normalize_pdf_text(page.extract_text() or "") for page in reader.pages]
            return "\n\n".join(pages)
        except ImportError as exc:
            raise ValueError(
                "PDF support requires 'pypdf'. Install requirements or upload .md/.txt/.yaml."
            ) from exc

    return unicodedata.normalize("NFKC", raw_bytes.decode("utf-8", errors="replace"))


def chunk_document(
    text: str,
    chunk_size: int = 1400,
    chunk_overlap: int = 220,
    redact: bool = True,
) -> Tuple[List[TextChunk], dict]:
    """Redact secrets if enabled and split document into overlapping chunks."""
    redaction_counts = {}
    cleaned = text
    if redact:
        cleaned, redaction_counts = redact_secrets(text)

    cleaned = cleaned.replace("\r\n", "\n").strip()
    if not cleaned:
        return [], redaction_counts

    sections = _split_by_headings(cleaned)
    chunks: List[TextChunk] = []
    idx = 0

    for heading, body in sections:
        if len(body) <= chunk_size:
            chunks.append(TextChunk(chunk_index=idx, heading=heading, content=body))
            idx += 1
            continue

        start = 0
        while start < len(body):
            end = min(len(body), start + chunk_size)
            slice_text = body[start:end].strip()
            if slice_text:
                chunks.append(TextChunk(chunk_index=idx, heading=heading, content=slice_text))
                idx += 1
            if end >= len(body):
                break
            start = max(start + 1, end - chunk_overlap)

    return chunks, redaction_counts


def _split_by_headings(text: str) -> List[Tuple[str, str]]:
    matches = list(_MD_HEADING_RE.finditer(text))
    if not matches:
        matches = list(_NUM_HEADING_RE.finditer(text))
    if not matches:
        return [("General", text)]

    sections: List[Tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("Overview", preamble))

    for i, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((heading, body))

    return sections
