"""In-memory text extraction from uploaded resumes (PDF, DOCX, TXT/MD). Nothing touches disk."""
from __future__ import annotations

import io
import re

MAX_PDF_PAGES = 15
MAX_CHARS = 60_000


class DocumentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _clean(text: str) -> str:
    text = text.replace("\x00", " ").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_CHARS]


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise DocumentError("encrypted", "This PDF is password-protected.")
        pages = reader.pages[:MAX_PDF_PAGES]
        return "\n".join((p.extract_text() or "") for p in pages)
    except PdfReadError:
        raise DocumentError("unreadable", "We couldn't read this PDF.")


def _docx(data: bytes) -> str:
    import docx

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception:
        raise DocumentError("unreadable", "We couldn't read this Word document.")
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        text = _pdf(data)
    elif name.endswith(".docx") or data[:2] == b"PK":
        text = _docx(data)
    elif name.endswith((".txt", ".md", ".text")):
        text = data.decode("utf-8", errors="replace")
    else:
        raise DocumentError("unsupported", "Please upload a PDF, DOCX or TXT file.")

    text = _clean(text)
    if len(text) < 80:
        raise DocumentError(
            "empty",
            "We couldn't find readable text. If this is a scanned PDF, paste the text or upload a screenshot instead.",
        )
    return text
