from __future__ import annotations

import hashlib
from io import BytesIO

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw

from pa_docket.documents.storage import InvalidPdfError, store_pdf, validate_pdf_bytes
from pa_docket.domain.enums import PresenceStatus, ValidationStatus
from pa_docket.parsers import GenericParser
from pa_docket.sources.fixture.generator import (
    SyntheticCharge,
    SyntheticDefendant,
    SyntheticDocket,
    render_pdf,
)


def test_extracts_multiple_charges_and_keeps_charge_dates_and_dispositions():
    case = SyntheticDocket(
        "MD-01-CR-0001111-2025",
        defendants=[SyntheticDefendant("Jordan Example", "White")],
        charges=[
            SyntheticCharge("1", "Theft", "12/31/2024", "Guilty", "02/01/2025"),
            SyntheticCharge("2", "Assault", "01/02/2025", "Dismissed", "02/02/2025"),
            SyntheticCharge("3", "Harassment", "01/03/2025", "Withdrawn", "02/03/2025"),
        ],
    )
    record = GenericParser().parse(render_pdf(case))
    assert record.docket_number.raw_value == "MD-01-CR-0001111-2025"
    assert record.defendants[0].name.normalized_value == "Jordan Example"
    assert record.defendants[0].race.normalized_value == "White"
    assert [charge.description.raw_value for charge in record.charges] == [
        "Theft",
        "Assault",
        "Harassment",
    ]
    assert [charge.offense_date.normalized_value for charge in record.charges] == [
        "2024-12-31",
        "2025-01-02",
        "2025-01-03",
    ]
    assert [charge.disposition.normalized_value for charge in record.charges] == [
        "guilty",
        "dismissed",
        "withdrawn",
    ]
    assert all("page=1;line=" in charge.description.evidence_reference for charge in record.charges)
    assert record.validation_status == ValidationStatus.VALIDATED


def test_missing_race_and_affiant_do_not_become_inferred_values():
    case = SyntheticDocket(
        "MD-01-CR-0001112-2025",
        defendants=[SyntheticDefendant("Casey Sample")],
        charges=[SyntheticCharge("1", "Assault", "01/01/2025", "Pending", affiant="Taylor Witness")],
    )
    record = GenericParser().parse(render_pdf(case))
    assert record.defendants[0].race.raw_value is None
    assert record.defendants[0].race.normalized_value is None
    assert record.defendants[0].race.presence_status == PresenceStatus.NOT_RECORDED
    assert record.charges[0].arresting_officer.raw_value is None
    assert record.charges[0].arresting_officer.presence_status == PresenceStatus.UNVERIFIED
    assert record.charges[0].disposition.normalized_value == "pending"


def test_multiple_defendants_keep_charge_associations_and_related_dockets():
    case = SyntheticDocket(
        "CP-01-CR-0001113-2025",
        defendants=[SyntheticDefendant("Alex One"), SyntheticDefendant("Blair Two", "Black")],
        charges=[
            SyntheticCharge("1", "Burglary", "01/01/2025", "Held for Court", defendant_index=1),
            SyntheticCharge("2", "Trespass", "01/02/2025", "Not Guilty", defendant_index=2),
        ],
        related_dockets=["MD-01-CR-0001000-2025"],
    )
    record = GenericParser().parse(render_pdf(case))
    assert [charge.defendant_index for charge in record.charges] == [1, 2]
    assert [charge.disposition.normalized_value for charge in record.charges] == [
        "held",
        "not-guilty",
    ]
    assert record.related_docket_numbers[0].raw_value == "MD-01-CR-0001000-2025"


def test_missing_offense_date_is_not_replaced_with_filing_date():
    case = SyntheticDocket(
        "MD-01-CR-0001114-2025",
        filing_date="01/10/2025",
        defendants=[SyntheticDefendant("Riley Fixture")],
        charges=[SyntheticCharge("1", "Simple assault", None, "Pending")],
    )
    record = GenericParser().parse(render_pdf(case))
    assert record.filing_date.normalized_value == "2025-01-10"
    assert record.charges[0].offense_date.normalized_value is None
    assert record.charges[0].offense_date.presence_status == PresenceStatus.MISSING


def test_image_only_and_unsupported_layout_are_not_reported_as_success():
    buffer = BytesIO()
    image = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(image).text((30, 30), "Scanned docket sheet fixture", fill="black")
    image.save(buffer, format="PNG")
    blank = fitz.open()
    page = blank.new_page()
    page.insert_image(page.rect, stream=buffer.getvalue())
    image_only = blank.tobytes()
    blank.close()
    assert GenericParser().parse(image_only).validation_status == ValidationStatus.OCR_REQUIRED

    unsupported_doc = fitz.open()
    page = unsupported_doc.new_page()
    page.insert_text((48, 48), "Docket narrative without the expected labeled fields")
    unsupported = unsupported_doc.tobytes()
    unsupported_doc.close()
    assert GenericParser().parse(unsupported).validation_status == ValidationStatus.UNSUPPORTED_LAYOUT


def test_pdf_magic_hash_and_content_addressed_versions(tmp_path):
    with pytest.raises(InvalidPdfError):
        validate_pdf_bytes(b"<html>not a pdf</html>")
    with pytest.raises(InvalidPdfError):
        validate_pdf_bytes(b"%PDF-1.7\nshort")

    case = SyntheticDocket(
        "MD-01-CR-0001115-2025",
        defendants=[SyntheticDefendant("Demo")],
        charges=[SyntheticCharge("1", "Theft", "01/01/2025", "Pending")],
    )
    first = render_pdf(case)
    duplicate = render_pdf(case)
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(duplicate).hexdigest()
    hash_a, path_a = store_pdf(first, tmp_path)
    hash_dup, path_dup = store_pdf(duplicate, tmp_path)
    assert hash_a == hash_dup and path_a == path_dup

    doc = fitz.open(stream=first, filetype="pdf")
    doc[0].insert_text((48, 780), "Changed document version", fontsize=8)
    modified = doc.tobytes()
    doc.close()
    hash_b, path_b = store_pdf(modified, tmp_path)
    assert hash_b != hash_a and path_b != path_a
