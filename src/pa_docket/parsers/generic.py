from __future__ import annotations

import re
from datetime import UTC, datetime

import pymupdf as fitz

from pa_docket.domain.enums import PresenceStatus, ValidationStatus
from pa_docket.domain.models import Charge, Defendant, DocketRecord, FieldValue

PARSER_VERSION = "generic-0.1.0"
DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%B %d, %Y")
DISPOSITIONS = {
    "pending": "pending",
    "guilty": "guilty",
    "guilty plea": "guilty",
    "not guilty": "not-guilty",
    "acquitted": "not-guilty",
    "dismissed": "dismissed",
    "withdrawn": "withdrawn",
    "held for court": "held",
    "held": "held",
    "waived": "waived",
}
TAG = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /().-]*):\s*(.*?)\s*$")
SECTION = re.compile(r"^\s*(DEFENDANT|CHARGE)\s+(\d+)\s*$", re.I)


class MalformedPdfError(ValueError):
    """The file has a PDF signature but PyMuPDF cannot parse its structure."""


def _field(
    raw: str | None,
    ref: str | None,
    *,
    absent: PresenceStatus = PresenceStatus.MISSING,
    normalize: str = "text",
) -> FieldValue:
    if raw is None or not raw.strip():
        return FieldValue(presence_status=absent)
    value = raw.strip()
    normalized = " ".join(value.split())
    if normalize == "identifier":
        normalized = value
    elif normalize == "date":
        normalized = ""
        for fmt in DATE_FORMATS:
            try:
                normalized = datetime.strptime(value, fmt).date().isoformat()
                break
            except ValueError:
                continue
        if not normalized:
            return FieldValue(raw_value=value, presence_status=PresenceStatus.PRESENT, evidence_reference=ref)
    elif normalize == "disposition":
        normalized = DISPOSITIONS.get(value.casefold(), "unknown")
    return FieldValue(
        raw_value=value,
        normalized_value=normalized,
        presence_status=PresenceStatus.PRESENT,
        evidence_reference=ref,
    )


class GenericParser:
    """Parser for explicit, labeled text fields; it does not infer unlabeled values."""

    def parse(self, data: bytes, *, source_url: str | None = None) -> DocketRecord:
        if not data.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF magic bytes")
        pages: list[str] = []
        try:
            with fitz.open(stream=data, filetype="pdf") as pdf:
                pages = [page.get_text("text", sort=True) for page in pdf]
        except Exception as exc:
            raise MalformedPdfError("PDF structure is malformed or truncated") from exc
        lines: list[tuple[int, int, str]] = []
        for page_no, text in enumerate(pages, start=1):
            for line_no, line in enumerate(text.splitlines(), start=1):
                if line.strip():
                    lines.append((page_no, line_no, line.strip()))
        if not lines:
            return DocketRecord(
                docket_number=_field(None, None),
                court=_field(None, None),
                county=_field(None, None),
                filing_date=_field(None, None),
                case_status=_field(None, None),
                parser_version=PARSER_VERSION,
                validation_status=ValidationStatus.OCR_REQUIRED,
                validation_errors=["No selectable text was found in the PDF"],
                source_pdf_url=source_url,
                retrieved_at=datetime.now(UTC),
            )

        docket: dict[str, tuple[str, str]] = {}
        defendants: dict[int, dict[str, tuple[str, str]]] = {}
        charges: dict[int, dict[str, tuple[str, str]]] = {}
        related: list[FieldValue] = []
        section: str | None = None
        section_index = 0
        found_any_tag = False
        for page_no, line_no, line in lines:
            ref = f"page={page_no};line={line_no};text={line[:180]}"
            match_section = SECTION.match(line)
            if match_section:
                section, n = match_section.group(1).upper(), int(match_section.group(2))
                section_index = n
                if section == "DEFENDANT":
                    defendants.setdefault(n, {})
                else:
                    charges.setdefault(n, {})
                continue
            tag = TAG.match(line)
            if not tag:
                continue
            found_any_tag = True
            label, value = tag.group(1).strip().casefold(), tag.group(2).strip()
            if label == "related docket":
                related.append(_field(value or None, ref))
                continue
            if section == "DEFENDANT":
                defendants[section_index][label] = (value, ref)
            elif section == "CHARGE":
                charges[section_index][label] = (value, ref)
            else:
                docket[label] = (value, ref)

        if not found_any_tag:
            return DocketRecord(
                docket_number=_field(None, None),
                court=_field(None, None),
                county=_field(None, None),
                filing_date=_field(None, None),
                case_status=_field(None, None),
                parser_version=PARSER_VERSION,
                validation_status=ValidationStatus.UNSUPPORTED_LAYOUT,
                validation_errors=["No labeled fields matched the generic parser"],
                source_pdf_url=source_url,
                retrieved_at=datetime.now(UTC),
            )

        def get(values: dict[str, tuple[str, str]], *keys: str) -> tuple[str | None, str | None]:
            for key in keys:
                found = values.get(key)
                if found:
                    return found
            return None, None

        docket_number, dref = get(docket, "docket number")
        court, cref = get(docket, "court", "court type")
        county, coref = get(docket, "county")
        filing_date, fref = get(docket, "filing date")
        case_status, sref = get(docket, "case status", "case disposition")
        parsed_defendants = []
        for index, values in sorted(defendants.items()):
            name, nref = get(values, "defendant name", "name")
            race, rref = get(values, "race", "defendant race")
            parsed_defendants.append(
                Defendant(
                    index=index,
                    name=_field(name, nref),
                    race=_field(race, rref, absent=PresenceStatus.NOT_RECORDED),
                )
            )
        parsed_charges = []
        for _index, values in sorted(charges.items()):
            charge_no, nref = get(values, "charge number", "number")
            desc, dref_charge = get(values, "description", "charge description")
            statute, tref = get(values, "statute", "charge statute")
            grade, gref = get(values, "grade")
            offense, oref = get(values, "offense date", "date of offense")
            officer, aref = get(values, "arresting officer")
            affiant, _ = get(values, "affiant")
            disposition, dispref = get(values, "disposition", "charge disposition")
            disposition_date, ddref = get(values, "disposition date")
            defendant_raw, _ = get(values, "defendant index")
            try:
                defendant_index = int(defendant_raw) if defendant_raw else 1
            except ValueError:
                defendant_index = 1
            officer_absence = PresenceStatus.UNVERIFIED if affiant else PresenceStatus.NOT_RECORDED
            parsed_charges.append(
                Charge(
                    charge_number=_field(charge_no, nref, normalize="identifier"),
                    defendant_index=defendant_index,
                    description=_field(desc, dref_charge),
                    statute=_field(statute, tref),
                    grade=_field(grade, gref),
                    offense_date=_field(offense, oref, normalize="date"),
                    arresting_officer=_field(officer, aref, absent=officer_absence),
                    disposition=(
                        _field(disposition, dispref, normalize="disposition")
                        if disposition
                        else FieldValue(
                            raw_value=None,
                            normalized_value="unknown",
                            presence_status=PresenceStatus.MISSING,
                        )
                    ),
                    disposition_date=_field(disposition_date, ddref, normalize="date"),
                )
            )

        errors = []
        if not docket_number:
            errors.append("Docket number is not explicitly labeled")
        if not parsed_defendants or any(x.name.presence_status != PresenceStatus.PRESENT for x in parsed_defendants):
            errors.append("A defendant name is missing")
        if not parsed_charges:
            errors.append("No charge sections were extracted")
        if any(c.description.presence_status != PresenceStatus.PRESENT for c in parsed_charges):
            errors.append("One or more charge descriptions are missing")
        if any(c.charge_number.presence_status != PresenceStatus.PRESENT for c in parsed_charges):
            errors.append("One or more charge numbers are missing")
        if any(c.offense_date.raw_value and not c.offense_date.normalized_value for c in parsed_charges):
            errors.append("One or more offense dates could not be normalized")
        if any(c.disposition.normalized_value == "unknown" for c in parsed_charges):
            errors.append("One or more charge dispositions need review")
        status = ValidationStatus.NEEDS_REVIEW if errors else ValidationStatus.VALIDATED
        if not parsed_charges:
            status = ValidationStatus.UNSUPPORTED_LAYOUT
        return DocketRecord(
            docket_number=_field(docket_number, dref, normalize="identifier"),
            court=_field(court, cref),
            county=_field(county, coref),
            filing_date=_field(filing_date, fref, normalize="date"),
            case_status=_field(case_status, sref, normalize="disposition"),
            related_docket_numbers=related,
            defendants=parsed_defendants,
            charges=parsed_charges,
            parser_version=PARSER_VERSION,
            validation_status=status,
            validation_errors=errors,
            source_pdf_url=source_url,
            retrieved_at=datetime.now(UTC),
        )
