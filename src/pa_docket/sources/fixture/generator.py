from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf as fitz


@dataclass
class SyntheticCharge:
    number: str
    description: str
    offense_date: str | None
    disposition: str
    disposition_date: str | None = None
    statute: str = "18 Pa.C.S. § 0000"
    grade: str = "M1"
    defendant_index: int = 1
    arresting_officer: str | None = None
    affiant: str | None = None


@dataclass
class SyntheticDefendant:
    name: str
    race: str | None = None


@dataclass
class SyntheticDocket:
    docket_number: str
    court: str = "Magisterial District Court"
    county: str = "Dauphin"
    filing_date: str = "01/10/2025"
    case_status: str = "Pending"
    defendants: list[SyntheticDefendant] = field(default_factory=list)
    charges: list[SyntheticCharge] = field(default_factory=list)
    related_dockets: list[str] = field(default_factory=list)


def render_pdf(case: SyntheticDocket) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 48

    def put(line: str) -> None:
        nonlocal y, page
        if y > 760:
            page = doc.new_page()
            y = 48
        page.insert_text((48, y), line, fontsize=10)
        y += 18

    put("SYNTHETIC PUBLIC DOCKET FIXTURE — NOT A REAL CASE")
    put(f"Docket Number: {case.docket_number}")
    put(f"Court: {case.court}")
    put(f"County: {case.county}")
    put(f"Filing Date: {case.filing_date}")
    put(f"Case Status: {case.case_status}")
    for number in case.related_dockets:
        put(f"Related Docket: {number}")
    for index, defendant in enumerate(case.defendants, start=1):
        put(f"DEFENDANT {index}")
        put(f"Defendant Name: {defendant.name}")
        if defendant.race is not None:
            put(f"Race: {defendant.race}")
    for charge in case.charges:
        put(f"CHARGE {charge.number}")
        put(f"Charge Number: {charge.number}")
        put(f"Defendant Index: {charge.defendant_index}")
        put(f"Description: {charge.description}")
        put(f"Statute: {charge.statute}")
        put(f"Grade: {charge.grade}")
        if charge.offense_date:
            put(f"Offense Date: {charge.offense_date}")
        put(f"Disposition: {charge.disposition}")
        if charge.disposition_date:
            put(f"Disposition Date: {charge.disposition_date}")
        if charge.arresting_officer:
            put(f"Arresting Officer: {charge.arresting_officer}")
        if charge.affiant:
            put(f"Affiant: {charge.affiant}")
    # Disable the random trailer document ID so identical fixture inputs hash identically.
    out = doc.tobytes(deflate=True, no_new_id=True)
    doc.close()
    return out


def sample_cases() -> list[SyntheticDocket]:
    return [
        SyntheticDocket(
            "MD-01-CR-0000123-2025",
            filing_date="01/02/2025",
            county="Dauphin",
            defendants=[SyntheticDefendant("Jordan Example", "White")],
            charges=[
                SyntheticCharge(
                    "1",
                    "Theft by unlawful taking",
                    "12/31/2024",
                    "Pending",
                    statute="18 Pa.C.S. § 3921",
                    arresting_officer="Officer Alex Sample",
                )
            ],
        ),
        SyntheticDocket(
            "MD-02-CR-0000124-2025",
            filing_date="01/03/2025",
            county="Allegheny",
            defendants=[SyntheticDefendant("Casey Sample")],
            charges=[
                SyntheticCharge(
                    "1",
                    "Simple assault",
                    "01/01/2025",
                    "Guilty",
                    "02/01/2025",
                    statute="18 Pa.C.S. § 2701",
                    arresting_officer="Officer Pat Demo",
                ),
                SyntheticCharge(
                    "2",
                    "Harassment",
                    "01/02/2025",
                    "Dismissed",
                    "02/02/2025",
                    statute="18 Pa.C.S. § 2709",
                    affiant="Taylor Witness",
                ),
                SyntheticCharge(
                    "3",
                    "Disorderly conduct",
                    "01/02/2025",
                    "Withdrawn",
                    "02/03/2025",
                    statute="18 Pa.C.S. § 5503",
                ),
            ],
            related_dockets=["CP-02-CR-0000456-2025"],
        ),
        SyntheticDocket(
            "CP-03-CR-0000789-2025",
            court="Court of Common Pleas",
            county="Philadelphia",
            filing_date="01/04/2025",
            case_status="Guilty",
            defendants=[
                SyntheticDefendant("Morgan Demo", "Black"),
                SyntheticDefendant("Riley Fixture", "Asian"),
            ],
            charges=[
                SyntheticCharge("1", "Burglary", "12/20/2024", "Held for Court", defendant_index=1),
                SyntheticCharge("2", "Criminal trespass", "12/21/2024", "Pending", defendant_index=2),
            ],
        ),
    ]
