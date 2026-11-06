"""Parser for Pennsylvania UJS criminal docket sheets.

Two document families are produced by the portal:

``MDJS 1200``  Magisterial District Judge criminal docket.
               Carries ``Race:`` in DEFENDANT INFORMATION, the arresting officer
               in CASE PARTICIPANTS, and a per-charge ``Disposition`` column.

``CPCMS 9082`` Court of Common Pleas criminal docket.
               No race field exists on this report. The arresting officer sits
               inline in CASE INFORMATION. Outcomes live in the
               DISPOSITION SENTENCING/PENALTIES section, keyed by charge
               sequence number, not in the charge table.

Both are laid out as space-aligned columns; a split on runs of two or more
spaces separates the cells cleanly in ``sort=True`` text. Wrapped description
lines carry no leading sequence number and are folded back into the previous
charge. Nothing is inferred across sections: an absent race stays
NOT_RECORDED, an absent officer stays NOT_RECORDED, and an outcome that the
document never states stays MISSING rather than inheriting the case outcome.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import pymupdf as fitz

from pa_docket.domain.enums import PresenceStatus, ValidationStatus
from pa_docket.domain.models import Charge, Defendant, DocketRecord, FieldValue
from pa_docket.parsers.generic import MalformedPdfError

PARSER_VERSION = "ujs-1.1.0"

COURT_FAMILY_MDJ = "MDJ"
COURT_FAMILY_CP = "CP"


class NonCriminalDocketError(ValueError):
    """The document is a real UJS docket, but not a criminal one.

    Landlord/tenant, civil and summary-appeal dockets share the MDJS report.
    They are out of scope rather than malformed, so they are skipped by name
    instead of being failed as unparseable.
    """

DATE_FORMATS = ("%m/%d/%Y",)
DATE_RE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
DATE_ONLY_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
CELL_SPLIT = re.compile(r"\s{2,}")

SECTION_STOPS = {
    "CASE INFORMATION",
    "STATUS INFORMATION",
    "CALENDAR EVENTS",
    "DEFENDANT INFORMATION",
    "CASE PARTICIPANTS",
    "BAIL INFORMATION",
    "CHARGES",
    "DISPOSITION SENTENCING/PENALTIES",
    "DISPOSITION / SENTENCING DETAILS",
    "COMMONWEALTH INFORMATION",
    "ATTORNEY INFORMATION",
    "ENTRIES",
    "DOCKET ENTRY INFORMATION",
    "CASE FINANCIAL INFORMATION",
}

DISPOSITION_MAP = {
    "guilty plea": "guilty",
    "guilty": "guilty",
    "not guilty": "not-guilty",
    "nolle prosequi": "dismissed",
    "nol pros": "dismissed",
    "dismissed": "dismissed",
    "withdrawn": "withdrawn",
    "charge changed": "changed",
    "held for court (lower court)": "held",
    "held for court": "held",
    "held": "held",
    "waived": "waived",
    "pending": "pending",
    "active": "pending",
    "not final": "pending",
    "moved to juvenile": "transferred",
    "transferred": "transferred",
    "deceased": "closed",
    "inactive": "closed",
    "closed": "closed",
    "unavailable": "unavailable",
}
# Ordered by specificity: the portal writes both codes ("HP - Held for Court")
# and plain words, so an exact-dictionary lookup is not sufficient.
DISPOSITION_PHRASES = (
    ("nolle prosequi", "dismissed"),
    ("nol pros", "dismissed"),
    ("not guilty", "not-guilty"),
    ("guilty plea", "guilty"),
    ("held for court", "held"),
    ("charge changed", "changed"),
    ("withdrawn", "withdrawn"),
    ("dismissed", "dismissed"),
    ("waived", "waived"),
    ("moved to juvenile", "transferred"),
    ("transferred", "transferred"),
    ("deceased", "closed"),
    ("not final", "pending"),
    ("pending", "pending"),
    ("active", "pending"),
)


@dataclass(frozen=True)
class _Line:
    page: int
    number: int
    text: str

    @property
    def stripped(self) -> str:
        return self.text.strip()


def normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_statute(raw: str | None) -> str | None:
    """Whitespace only: a statute citation keeps the section marks it was printed with.

    ``35 § 780-113 §§ A30`` is a subsection citation; collapsing the doubled mark
    would silently rewrite the citation, so the source glyphs survive.
    """
    if not raw:
        return None
    return " ".join(raw.split())


def normalize_disposition(raw: str | None) -> str | None:
    if not raw:
        return None
    key = " ".join(raw.strip().casefold().split())
    if key in DISPOSITION_MAP:
        return DISPOSITION_MAP[key]
    for phrase, value in DISPOSITION_PHRASES:
        if phrase in key:
            return value
    return None


def _field(
    raw: str | None,
    ref: str | None,
    *,
    normalized: str | None = None,
    status: PresenceStatus | None = None,
) -> FieldValue:
    """Absent values never acquire a normalized value of their own."""
    clean = " ".join(raw.split()) if raw and raw.strip() else None
    if status is not None:
        presence = status
    elif clean:
        presence = PresenceStatus.PRESENT
    else:
        presence = PresenceStatus.MISSING
    if clean is None:
        return FieldValue(presence_status=presence, evidence_reference=ref)
    return FieldValue(
        raw_value=clean,
        normalized_value=normalized if normalized is not None else clean,
        presence_status=presence,
        evidence_reference=ref,
    )


def absent(ref: str, status: PresenceStatus = PresenceStatus.NOT_RECORDED) -> FieldValue:
    return FieldValue(presence_status=status, evidence_reference=ref)


def detect_family(text: str) -> str | None:
    if "MDJS 1200" in text or re.search(
        r"\bMAGISTERIAL DISTRICT JUDGE\b", text, re.IGNORECASE
    ):
        return COURT_FAMILY_MDJ
    if "CPCMS 9082" in text or re.search(r"\bCRIMINAL DOCKET\b", text, re.IGNORECASE):
        return COURT_FAMILY_CP
    return None


NON_CRIMINAL_KIND_RE = re.compile(
    r"\b(Landlord/Tenant|Civil|Summary Appeal|Non[- ]Traffic|Miscellaneous)\s+Docket\b",
    re.IGNORECASE,
)


def docket_kind(text: str) -> str | None:
    """The docket type the document declares about itself.

    The portal serves civil, landlord/tenant and summary-appeal dockets through
    the same MDJS report. The declared type is authoritative; the docket number
    is not (``CP-22-MD-...`` is a criminal docket whose number carries ``MD``).
    """
    if re.search(r"\bCriminal Docket\b", text, re.IGNORECASE):
        return "Criminal"
    found = NON_CRIMINAL_KIND_RE.search(text)
    return found.group(1) if found else None


def _section(lines: list[_Line], name: str) -> list[_Line]:
    """Collect a section across page breaks.

    Report sections are repeated as bare headings on every page they touch, so
    the section's own heading is not a terminator; the next *different*
    heading is. A heading that shares a line with another heading still ends
    the section, which is why the stop test is a prefix test.
    """
    starts = [
        index
        for index, line in enumerate(lines)
        if line.stripped == name or line.stripped.startswith(name)
    ]
    if not starts:
        return []
    start = starts[0] + 1
    stops = SECTION_STOPS - {name}
    end = len(lines)
    for index in range(start, len(lines)):
        stripped = lines[index].stripped
        if any(stripped == stop or stripped.startswith(stop) for stop in stops):
            end = index
            break
    return lines[start:end]


def _first(pattern: str, lines: list[_Line]) -> tuple[str, _Line] | None:
    rx = re.compile(pattern)
    for line in lines:
        match = rx.search(line.text)
        if match:
            return match.group(1), line
    return None


def _ref(line: _Line, where: str) -> str:
    return f"p{line.page}:{where}"


def _cells(text: str) -> list[str]:
    return [cell.strip() for cell in CELL_SPLIT.split(text.strip()) if cell.strip()]


CASE_STATUS_RE = re.compile(
    r"(?:^\s*|\s{2,})Case Status:\s{2,}(\S.*?)(?:\s{2,}|$)", re.MULTILINE
)

# Some counties name the case event kind instead of its outcome. Those labels
# describe a stage of the case, not a result, so they are not used as the case
# disposition when the report also states a final-disposition cell.
GENERIC_CASE_EVENT_RE = re.compile(r"\(\s*generic\s*\)|\bproceeding\b", re.IGNORECASE)


def _case_status(lines: list[_Line]) -> FieldValue:
    """The report's own ``Case Status:`` cell, never the disposition.

    Both report families print the status on a labelled cell in the case header
    and the outcome further down. Reading the outcome here would silently
    overwrite the status with the disposition, so the status is read from its
    own label and the disposition travels in ``metadata["case_disposition"]``.

    The status cell sits under a ``STATUS INFORMATION`` banner that both report
    families place after ``CASE INFORMATION``, so the whole document is scanned
    rather than the first section.
    """
    for line in lines:
        match = CASE_STATUS_RE.search(line.text)
        if match:
            return _field(match.group(1), _ref(line, "STATUS INFORMATION"))
    return absent("STATUS INFORMATION")



def _append_description(charge: Charge, extra: str) -> None:
    charge.description = charge.description.model_copy(
        update={
            "raw_value": f"{charge.description.raw_value or ''} {extra}".strip(),
            "normalized_value": f"{charge.description.normalized_value or ''} {extra}".strip(),
        }
    )


# Report furniture that repeats inside a section across page breaks. A wrapped
# description never carries one of these, and folding one into a charge turns a
# clean offense label into a paragraph of boilerplate.
PAGE_NOISE_RE = re.compile(
    r"(?:MDJS\s*1200"
    r"|CPCMS\s*9082"
    r"|Printed:\s*\d"
    r"|Page\s+\d+\s+of\s+\d+"
    r"|Recent entries made"
    r"|Docket Number:"
    r"|Court of Common Pleas of"
    r"|Magisterial District Judge\s+\d"
    r"|Unified Judicial System"
    r"|Administrative Office of Pennsylvania Courts"
    r"|Criminal History Record Information Act)",
    re.IGNORECASE,
)
PAGE_BANNER_RE = re.compile(
    r"^\s*(?:DOCKET|CRIMINAL DOCKET|Criminal Docket|Commonwealth of Pennsylvania|"
    r"Court Case|v\.)\s*$",
    re.IGNORECASE,
)


def _is_report_furniture(line: _Line) -> bool:
    """Page furniture: footer form ids, the disclaimer block, the report banner."""
    stripped = line.stripped
    return bool(PAGE_NOISE_RE.search(stripped) or PAGE_BANNER_RE.match(stripped))


def _is_wrapped_description(line: _Line, row_indent: int | None) -> bool:
    """A continuation is part of the previous charge only if it is indented under it.

    Charge rows begin at the left margin (indent 3 for CP, 8 for MDJ); a wrapped
    offense description is printed deep inside the description column. Report
    furniture — page footers, the repeated banner, the disclaimer block — falls
    back toward the left margin, so the indent test separates text from trim
    without having to know the statute cell's width.
    """
    stripped = line.stripped
    if not stripped:
        return False
    if row_indent is None:
        return False
    indent = len(line.text) - len(line.text.lstrip())
    if indent <= row_indent:
        return False
    if _is_report_furniture(line):
        return False
    if stripped in SECTION_STOPS:
        return False
    if DATE_RE.search(stripped):
        return False
    return True


class UJSDocketParser:
    """Deterministic extractor for CP and MDJ criminal docket sheets."""

    version = PARSER_VERSION

    def supports(self, text: str) -> bool:
        return detect_family(text) is not None

    def parse_bytes(
        self,
        data: bytes,
        *,
        source_url: str | None = None,
    ) -> DocketRecord:
        try:
            with fitz.open(stream=data, filetype="pdf") as pdf:
                if pdf.needs_pass:
                    raise MalformedPdfError("Encrypted PDF")
                pages = [page.get_text("text", sort=True) for page in pdf]
        except MalformedPdfError:
            raise
        except Exception as exc:  # PyMuPDF raises several undocumented types
            raise MalformedPdfError(str(exc)) from exc
        if not pages:
            raise MalformedPdfError("PDF has no pages")
        record = self.parse_pages(pages)
        record.source_pdf_url = source_url
        record.retrieved_at = datetime.now(UTC)
        return record

    def parse_pages(self, pages: list[str]) -> DocketRecord:
        lines: list[_Line] = []
        for page_number, page in enumerate(pages, start=1):
            for number, raw in enumerate(page.splitlines()):
                if raw.strip():
                    lines.append(_Line(page_number, number, raw.rstrip()))
        text = "\n".join(pages)
        family = detect_family(text)
        if family is None:
            raise MalformedPdfError("Unrecognised UJS layout")
        kind = docket_kind(text)
        if kind is not None and kind.casefold() != "criminal" and not _section(lines, "CHARGES"):
            docket = _first(r"Docket Number:\s*(\S+)", lines)
            raise NonCriminalDocketError(
                f"{kind} docket {docket[0] if docket else '(unknown)'} carries no criminal charges"
            )
        if family == COURT_FAMILY_MDJ:
            return self._parse_mdj(lines)
        return self._parse_cp(lines)

    # ------------------------------------------------------------------ shared

    def _common(self, lines: list[_Line]) -> dict[str, tuple[str, _Line] | None]:
        return {
            "docket": _first(r"Docket Number:\s*(\S+)", lines),
            "county": _first(r"^\s*County:\s+(\S+)", lines),
            "filed": _first(r"(?:Date Filed|File Date):\s+(\d{2}/\d{2}/\d{4})", lines),
            "issued": _first(r"(?:Issue Date|Initiation Date):\s+(\d{2}/\d{2}/\d{4})", lines),
        }

    def _defendant_name(self, lines: list[_Line]) -> tuple[str, _Line] | None:
        for line in _section(lines, "CASE PARTICIPANTS"):
            if line.stripped.casefold().startswith("defendant"):
                cells = _cells(line.text)
                if len(cells) >= 2:
                    return cells[-1], line
        for index, line in enumerate(lines):
            if line.stripped == "v." and index + 1 < len(lines):
                return lines[index + 1].stripped, lines[index + 1]
        return None

    def _court_name(self, lines: list[_Line], family: str) -> tuple[str, _Line] | None:
        if family == COURT_FAMILY_MDJ:
            return _first(r"(Magisterial District Judge\s+\S+)", lines)
        found = _first(r"(COURT OF COMMON PLEAS OF .+?COUNTY)", lines)
        if found:
            spelled = found[0].title().replace("Of", "of").replace("And", "and")
            return spelled, found[1]
        return None

    def _build(
        self,
        *,
        family: str,
        lines: list[_Line],
        name: tuple[str, _Line] | None,
        race: FieldValue,
        officer: FieldValue,
        charges: list[Charge],
        case_status: FieldValue,
        errors: list[str],
    ) -> DocketRecord:
        common = self._common(lines)
        docket = common["docket"]
        county = common["county"]
        filed = common["filed"]
        court = self._court_name(lines, family)

        present = sum(
            1
            for value in (
                docket[0] if docket else None,
                name[0] if name else None,
                filed[0] if filed else None,
                county[0] if county else None,
                race.raw_value,
                officer.raw_value,
                case_status.raw_value,
            )
            if value
        )
        if charges:
            present += 1
        # Race is printed on the MDJS report only; CPCMS 9082 has no race field.
        # A field the document cannot state never demotes a complete read.
        expected = 8 if family == COURT_FAMILY_MDJ else 7
        if not charges:
            # A criminal docket with no charge rows is a structural failure of
            # the read, not a partially complete record.
            status = ValidationStatus.NEEDS_REVIEW
        elif present == expected and not errors:
            status = ValidationStatus.VALIDATED
        elif present >= expected - 3:
            status = ValidationStatus.PARTIAL
        else:
            status = ValidationStatus.NEEDS_REVIEW

        name_ref = _ref(name[1], "CASE PARTICIPANTS") if name else None
        return DocketRecord(
            docket_number=_field(
                docket[0] if docket else None,
                _ref(docket[1], "header") if docket else None,
            ),
            court=_field(court[0] if court else None, _ref(court[1], "header") if court else None),
            county=_field(
                county[0] if county else None,
                _ref(county[1], "CASE INFORMATION") if county else None,
            ),
            filing_date=_field(
                filed[0] if filed else None,
                _ref(filed[1], "CASE INFORMATION") if filed else None,
                normalized=normalize_date(filed[0]) if filed else None,
            ),
            case_status=case_status,
            defendants=[
                Defendant(
                    index=1,
                    name=_field(name[0] if name else None, name_ref),
                    race=race,
                )
            ],
            charges=charges,
            parser_version=self.version,
            validation_status=status,
            validation_errors=errors,
            metadata={
                "document_family": family,
                "initiation_date": normalize_date(common["issued"][0]) if common["issued"] else None,
            },
        )

    # --------------------------------------------------------------------- MDJ

    def _parse_mdj(self, lines: list[_Line]) -> DocketRecord:
        errors: list[str] = []
        name = self._defendant_name(lines)

        race_found = _first(
            r"\bRace:\s{2,}(.+?)(?:\s{2,}Sex:|\s*$)", _section(lines, "DEFENDANT INFORMATION")
        )
        race = (
            _field(race_found[0], _ref(race_found[1], "DEFENDANT INFORMATION"))
            if race_found
            else absent("p1:DEFENDANT INFORMATION")
        )

        officer_found = None
        for line in _section(lines, "CASE PARTICIPANTS"):
            if line.stripped.casefold().startswith("arresting officer"):
                cells = _cells(line.text)
                if len(cells) >= 2:
                    officer_found = (cells[-1], line)
                    break
        officer = (
            _field(officer_found[0], _ref(officer_found[1], "CASE PARTICIPANTS"))
            if officer_found
            else absent("CASE PARTICIPANTS")
        )

        disposition_raw = None
        disposition_date_raw = None
        disposition_line = None
        for line in _section(lines, "CASE INFORMATION"):
            if line.stripped.casefold().startswith("disposition"):
                cells = _cells(line.text)
                values = [cell for cell in cells if ":" not in cell]
                if values:
                    disposition_raw = values[0]
                    disposition_date_raw = values[1] if len(values) > 1 else None
                    disposition_line = line
                break
        if disposition_raw is None:
            for line in _section(lines, "DISPOSITION / SENTENCING DETAILS"):
                cells = _cells(line.text)
                if len(cells) >= 2 and DATE_ONLY_RE.fullmatch(cells[1]):
                    disposition_raw = cells[0]
                    disposition_date_raw = cells[1]
                    disposition_line = line
                    break

        # Case status and case disposition are different facts; the report keeps
        # them on different lines, so they stay in different fields.
        case_status = _case_status(lines)
        case_disposition = (
            _field(
                disposition_raw,
                _ref(disposition_line, "CASE INFORMATION") if disposition_line else None,
                normalized=normalize_disposition(disposition_raw),
            )
            if disposition_raw
            else absent("CASE INFORMATION", PresenceStatus.NOT_RECORDED)
        )
        if case_disposition.raw_value and case_disposition.normalized_value is None:
            case_disposition = case_disposition.model_copy(
                update={"presence_status": PresenceStatus.UNVERIFIED}
            )
            errors.append(f"Case disposition: unrecognised outcome {disposition_raw!r}")

        charges = self._mdj_charges(lines, officer, errors, disposition_date_raw)
        record = self._build(
            family=COURT_FAMILY_MDJ,
            lines=lines,
            name=name,
            race=race,
            officer=officer,
            charges=charges,
            case_status=case_status,
            errors=errors,
        )
        record.metadata["case_disposition"] = case_disposition.model_dump(mode="json")
        record.metadata["case_disposition_date"] = normalize_date(disposition_date_raw)
        return record

    def _mdj_charges(
        self,
        lines: list[_Line],
        officer: FieldValue,
        errors: list[str],
        case_disposition_date: str | None,
    ) -> list[Charge]:
        charges: list[Charge] = []
        row_indent: int | None = None
        row = re.compile(
            r"^\s*(\d+)\s+(.+?)\s{2,}([A-Z0-9]{1,3})\s{2,}(.+?)\s{2,}"
            r"(\d{2}/\d{2}/\d{4})(?:\s{2,}(.+?))?\s*$"
        )
        for line in _section(lines, "CHARGES"):
            match = row.match(line.text)
            if not match:
                if _is_report_furniture(line):
                    # A page break or banner ends the current charge row; the
                    # caption that follows a repeated ``v.`` is not offense text.
                    row_indent = None
                elif charges and _is_wrapped_description(line, row_indent):
                    _append_description(charges[-1], line.stripped)
                continue
            seq, statute, grade, description, offense_date, disposition = match.groups()
            row_indent = len(line.text) - len(line.text.lstrip())
            ref = _ref(line, "CHARGES")
            if disposition:
                normalized = normalize_disposition(disposition)
                disposition_value = _field(disposition, ref, normalized=normalized)
                if normalized is None:
                    # The document states an outcome the mapping does not know.
                    # Keep the raw label, refuse to normalise it, and flag it.
                    disposition_value = disposition_value.model_copy(
                        update={
                            "normalized_value": None,
                            "presence_status": PresenceStatus.UNVERIFIED,
                        }
                    )
                    errors.append(f"Charge {seq}: unrecognised disposition {disposition!r}")
            else:
                disposition_value = FieldValue(
                    presence_status=PresenceStatus.MISSING, evidence_reference=ref
                )
            charges.append(
                Charge(
                    charge_number=_field(seq, ref),
                    defendant_index=1,
                    description=_field(description, ref),
                    statute=_field(statute, ref, normalized=normalize_statute(statute)),
                    grade=_field(grade, ref),
                    offense_date=_field(offense_date, ref, normalized=normalize_date(offense_date)),
                    arresting_officer=officer.model_copy(),
                    disposition=disposition_value,
                    disposition_date=_field(
                        case_disposition_date,
                        ref,
                        normalized=normalize_date(case_disposition_date),
                    ),
                )
            )
        if not charges:
            errors.append("No charge rows found in CHARGES section")
        return charges

    # ---------------------------------------------------------------------- CP

    def _parse_cp(self, lines: list[_Line]) -> DocketRecord:
        errors: list[str] = []
        name = self._defendant_name(lines)

        # CPCMS 9082 has no race field at all; this is a document property, not
        # a failed extraction, so it stays NOT_RECORDED with section evidence.
        race = absent("p1:DEFENDANT INFORMATION")

        officer_found = _first(r"Arresting Officer:\s+(.+?)\s*$", lines)
        officer = (
            _field(officer_found[0], _ref(officer_found[1], "CASE INFORMATION"))
            if officer_found
            else absent("CASE INFORMATION")
        )

        dispositions, disposition_dates = self._cp_dispositions(lines)
        charges = self._cp_charges(lines, officer, dispositions, disposition_dates, errors)

        section = _section(lines, "DISPOSITION SENTENCING/PENALTIES")
        disposition_label: str | None = None
        disposition_line: _Line | None = None
        headers = ("disposition", "case event", "sequence/description", "sentencing judge",
                   "sentence/diversion", "sentence conditions", "cpcms", "mdjs", "printed:",
                   "recent entries", "court of common pleas of", "docket")
        for line in section:
            stripped = line.stripped
            if not stripped or len(stripped) > 110 or stripped.casefold().startswith(headers):
                continue
            # Case-level event labels sit at the left margin; per-charge rows and
            # their wrapped descriptions are indented beneath them.
            if line.text[:1].strip():
                disposition_label = re.split(r"\s{2,}", stripped)[0]
                disposition_line = line
                break

        # The CPCMS report carries both a case status and a case disposition.
        # They are separate facts on separate lines and never overwrite one another.
        case_status = _case_status(lines)
        final = _first(
            r"^\s*(?:Lower Court Disposition|Preliminary Hearing|Arraignment|Trial)\s{2,}"
            r"\d{2}/\d{2}/\d{4}\s{2,}(.+?)\s*$",
            section,
        )
        # Some counties print a generic event kind where others print the outcome
        # itself. The generic label is not a disposition, so the final-disposition
        # cell speaks for the case and the label stays in metadata untouched.
        outcome = disposition_label
        if outcome and GENERIC_CASE_EVENT_RE.search(outcome) and final and final[0]:
            outcome = final[0]
        case_disposition = (
            _field(
                outcome,
                _ref(disposition_line, "DISPOSITION SENTENCING/PENALTIES")
                if disposition_line
                else None,
                normalized=normalize_disposition(outcome),
            )
            if outcome
            else absent("DISPOSITION SENTENCING/PENALTIES", PresenceStatus.NOT_RECORDED)
        )
        if case_disposition.raw_value and case_disposition.normalized_value is None:
            case_disposition = case_disposition.model_copy(
                update={"presence_status": PresenceStatus.UNVERIFIED}
            )
            errors.append(f"Case disposition: unrecognised outcome {outcome!r}")

        metadata = {
            "case_event_label": disposition_label,
            "case_disposition": case_disposition.model_dump(mode="json"),
        }
        if final:
            metadata["case_final_disposition"] = final[0]

        record = self._build(
            family=COURT_FAMILY_CP,
            lines=lines,
            name=name,
            race=race,
            officer=officer,
            charges=charges,
            case_status=case_status,
            errors=errors,
        )
        record.metadata.update(metadata)
        return record

    def _cp_dispositions(
        self, lines: list[_Line]
    ) -> tuple[dict[str, tuple[str, _Line]], dict[str, str]]:
        by_seq: dict[str, tuple[str, _Line]] = {}
        dates: dict[str, str] = {}
        current_seq: str | None = None
        pending_date: str | None = None
        seq_head = re.compile(r"^\s*(\d+)\s*/\s+(\S.*)$")
        event_row = re.compile(r"^\s*(.+?)\s{2,}(\d{2}/\d{2}/\d{4})\s{2,}(.+?)\s*$")
        for line in _section(lines, "DISPOSITION SENTENCING/PENALTIES"):
            stripped = line.stripped
            if not stripped:
                continue
            match = seq_head.match(line.text)
            if match and not DATE_ONLY_RE.match(stripped):
                seq = match.group(1)
                cells = _cells(line.text)
                disposition = next(
                    (
                        cell
                        for cell in cells[1:]
                        if not DATE_ONLY_RE.fullmatch(cell)
                        and not re.fullmatch(r"[A-Z0-9]{1,3}", cell)
                        and not re.match(r"^\d+( \S+)? §", cell)
                    ),
                    None,
                )
                if disposition:
                    by_seq[seq] = (disposition, line)
                    if pending_date:
                        dates[seq] = pending_date
                current_seq = seq
                continue
            event = event_row.match(line.text)
            if event:
                label, when, _outcome = event.groups()
                key = label.casefold()
                if "disposition" in key or "hearing" in key or "sentence" in key:
                    normalized = normalize_date(when)
                    if normalized:
                        pending_date = normalized
                        if current_seq and current_seq not in dates:
                            dates[current_seq] = normalized
        return by_seq, dates

    def _cp_charges(
        self,
        lines: list[_Line],
        officer: FieldValue,
        dispositions: dict[str, tuple[str, _Line]],
        disposition_dates: dict[str, str],
        errors: list[str],
    ) -> list[Charge]:
        charges: list[Charge] = []
        row_indent: int | None = None
        row = re.compile(
            r"^\s*(\d+)\s{2,}(\d*)\s{2,}([A-Z0-9]{1,3})\s{2,}(.+?)\s{2,}(.+?)\s{2,}"
            r"(\d{2}/\d{2}/\d{4})(?:\s{2,}(\S.*?))?\s*$"
        )
        for line in _section(lines, "CHARGES"):
            if line.stripped.startswith("Seq."):
                continue
            match = row.match(line.text)
            if not match:
                if _is_report_furniture(line):
                    row_indent = None
                elif charges and _is_wrapped_description(line, row_indent):
                    _append_description(charges[-1], line.stripped)
                continue
            seq, _orig, grade, statute, description, offense_date, _otn = match.groups()
            row_indent = len(line.text) - len(line.text.lstrip())
            ref = _ref(line, "CHARGES")
            entry = dispositions.get(seq)
            if entry:
                raw_disposition, disposition_line = entry
                normalized = normalize_disposition(raw_disposition)
                disposition = _field(
                    raw_disposition,
                    _ref(disposition_line, "DISPOSITION SENTENCING/PENALTIES"),
                    normalized=normalized,
                )
                if normalized is None:
                    disposition = disposition.model_copy(
                        update={
                            "normalized_value": None,
                            "presence_status": PresenceStatus.UNVERIFIED,
                        }
                    )
                    errors.append(f"Charge {seq}: unrecognised disposition {raw_disposition!r}")
            else:
                disposition = absent("DISPOSITION SENTENCING/PENALTIES", PresenceStatus.MISSING)
            when = disposition_dates.get(seq)
            charges.append(
                Charge(
                    charge_number=_field(seq, ref),
                    defendant_index=1,
                    description=_field(description, ref),
                    statute=_field(statute, ref, normalized=normalize_statute(statute)),
                    grade=_field(grade, ref),
                    offense_date=_field(offense_date, ref, normalized=normalize_date(offense_date)),
                    arresting_officer=officer.model_copy(),
                    disposition=disposition,
                    disposition_date=_field(
                        when,
                        "DISPOSITION SENTENCING/PENALTIES",
                        normalized=when,
                    )
                    if when
                    else absent("DISPOSITION SENTENCING/PENALTIES"),
                )
            )
        if not charges:
            errors.append("No charge rows found in CHARGES section")
        return charges
