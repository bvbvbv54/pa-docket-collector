from __future__ import annotations

from datetime import date, datetime

from pa_docket.sources.fixture.generator import render_pdf, sample_cases


class FixtureSource:
    """Deterministic, offline source. Synthetic PDFs are marked clearly as fixtures."""

    async def check_access(self) -> dict[str, str]:
        return {"status": "AVAILABLE", "source": "fixture"}

    def search(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        county: str | None = None,
        docket_number: str | None = None,
    ):
        for case in sample_cases():
            filed = datetime.strptime(case.filing_date, "%m/%d/%Y").date()
            if docket_number and case.docket_number.casefold() != docket_number.casefold():
                continue
            if start and filed < start or end and filed > end:
                continue
            if county and case.county.casefold() != county.casefold():
                continue
            yield case, render_pdf(case)
