"""Probe one Date Filed partition and report the form state the grid was built from."""
from __future__ import annotations

import asyncio
import sys
from datetime import date

from pa_docket.config import Settings
from pa_docket.sources.ujs.selectors import (
    COUNTY_SELECTOR,
    DATE_FROM_INPUT,
    DATE_TO_INPUT,
)
from pa_docket.sources.ujs.source import UJSPortalSource


async def main(county: str, day: date) -> int:
    settings = Settings.from_env()
    source = UJSPortalSource(settings)
    await source.open()
    try:
        result = await source.search_partition(day, county)
        page = await source._ensure_page()
        state = await page.evaluate(
            """selectors => ({
                county: document.querySelector(selectors.county)?.value ?? null,
                dateFrom: document.querySelector(selectors.from)?.value ?? null,
                dateTo: document.querySelector(selectors.to)?.value ?? null,
                rows: document.querySelectorAll('#caseSearchResultGrid tbody tr').length,
            })""",
            {"county": COUNTY_SELECTOR, "from": DATE_FROM_INPUT, "to": DATE_TO_INPUT},
        )
        print("form state  :", state)
        print("coverage    :", result.coverage_status)
        print("rows        :", len(result.rows))
        print("notice      :", result.truncated_notice)
        print("errors      :", result.errors)
        for row in result.rows[:3]:
            print("  first row :", row.docket_number, "|", row.court_type, "|", row.court_office)
        offices = await source.court_offices(county)
        print("offices     :", len(offices), offices[:3])
    finally:
        await source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1], date.fromisoformat(sys.argv[2]))))
