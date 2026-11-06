"""Parser dispatch: UJS report families first, labelled-field parser last."""

from pa_docket.parsers.generic import GenericParser, MalformedPdfError
from pa_docket.parsers.ujs import (
    NonCriminalDocketError,
    UJSDocketParser,
    detect_family,
    docket_kind,
)

__all__ = [
    "GenericParser",
    "MalformedPdfError",
    "NonCriminalDocketError",
    "UJSDocketParser",
    "detect_family",
    "docket_kind",
    "parse_docket_pdf",
]


def parse_docket_pdf(data: bytes, *, source_url: str | None = None):
    """Pick the parser by document family; fall back to the labelled-field parser.

    A UJS report is recognised by its own form identifier, so a docket sheet is
    never handed to the generic parser and a fixture is never handed to the UJS
    parser by guesswork. A real but non-criminal UJS docket raises
    ``NonCriminalDocketError`` so the caller can skip it by name.
    """
    try:
        with __import__("pymupdf").open(stream=data, filetype="pdf") as pdf:
            text = "\n".join(page.get_text("text", sort=True) for page in pdf)
    except Exception as exc:
        raise MalformedPdfError(str(exc)) from exc
    if detect_family(text) is not None:
        return UJSDocketParser().parse_bytes(data, source_url=source_url)
    record = GenericParser().parse(data, source_url=source_url)
    record.metadata["document_family"] = "GENERIC"
    return record
