from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf


class InvalidPdfError(ValueError):
    pass


def validate_pdf_bytes(data: bytes) -> None:
    if not data:
        raise InvalidPdfError("EMPTY")
    if not data.startswith(b"%PDF-"):
        raise InvalidPdfError("Document does not start with PDF magic bytes")
    if len(data) < 32:
        raise InvalidPdfError("PDF is truncated")
    if not data.rstrip().endswith(b"%%EOF"):
        raise InvalidPdfError("INVALID_PDF: missing end marker")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass or document.is_repaired or document.page_count == 0:
                raise InvalidPdfError("INVALID_PDF: encrypted, repaired or empty document")
            for page in document:
                page.get_text()
    except InvalidPdfError:
        raise
    except Exception:
        raise InvalidPdfError("INVALID_PDF: structure cannot be parsed") from None


def store_pdf(data: bytes, root: Path) -> tuple[str, Path]:
    validate_pdf_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path = root / "documents" / digest[:2] / f"{digest}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
    return digest, path
