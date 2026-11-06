import hashlib

import pymupdf
import pytest

from pa_docket.documents.storage import InvalidPdfError, store_pdf, validate_pdf_bytes
from pa_docket.sources.fixture.generator import render_pdf, sample_cases


@pytest.mark.parametrize("content", [b"", b"<html>Denied</html>", b"%PDF-1.7\n", b"%PDF-1.7\n" + b"x" * 80])
def test_reject_invalid_document(content):
    with pytest.raises(InvalidPdfError):
        validate_pdf_bytes(content)


def test_fixture_reopens_and_deduplicates(tmp_path):
    content = render_pdf(sample_cases()[0])
    digest, path = store_pdf(content, tmp_path)
    modified = path.stat().st_mtime_ns
    assert store_pdf(content, tmp_path) == (digest, path)
    assert path.stat().st_mtime_ns == modified
    assert digest == hashlib.sha256(content).hexdigest()
    assert path.stat().st_size == len(content)
    with pymupdf.open(path) as document:
        assert document.page_count > 0
        assert document[0].get_text().strip()
