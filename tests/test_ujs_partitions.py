"""Partition recovery: the portal's truncation notice drives coverage, not row counts."""

from __future__ import annotations

import asyncio
from datetime import date

from pa_docket.sources.ujs.source import (
    COVERAGE_COMPLETE,
    COVERAGE_TRUNCATED,
    GridRow,
    PartitionResult,
    UJSPortalSource,
    _notice_courts,
)

DAY = date(2026, 11, 5)
NOTICE_MDJ = "Not all results are shown for Magisterial District. Please narrow your search."
NOTICE_BOTH = "Not all results are shown for Common Pleas and Magisterial District. Please narrow your search."


def row(docket: str, county: str = "Lancaster", office: str = "MDJ-02-1-01") -> GridRow:
    return GridRow(
        docket_number=docket,
        court_type="Magisterial District" if docket.startswith("MJ-") else "Common Pleas",
        case_caption="Comm. v. Example",
        case_status="Active",
        filing_date="11/05/2026",
        county=county,
        court_office=office,
        detail_url=f"https://ujsportal.pacourts.us/Report/Docket?docketNumber={docket}",
        summary_url=None,
    )


def result(rows: list[GridRow], notice: str | None = None, office: str | None = None) -> PartitionResult:
    return PartitionResult(
        filing_date=DAY,
        county="Lancaster",
        court_office=office,
        rows=rows,
        truncated_notice=notice,
        notice_courts=_notice_courts(notice) if notice else [],
        pages_visited=1,
        coverage_status=COVERAGE_TRUNCATED if notice else COVERAGE_COMPLETE,
    )


class FakeSource(UJSPortalSource):
    """A portal whose responses are supplied, so no browser or network is used."""

    def __init__(self, base, offices, per_office):
        super().__init__(settings=None)
        self._base = base
        self._offices = offices
        self._per_office = per_office
        self.searches = 0

    async def search_partition(self, filing_date, county=None, court_office=None):
        self.searches += 1
        if court_office is None:
            return self._base
        return self._per_office.get(court_office, result([], office=court_office))

    async def court_offices(self, county):
        return list(self._offices)


def run(source, **kwargs):
    async def collect():
        return [part async for part in source.iter_partitions(DAY, DAY, ["Lancaster"], **kwargs)]

    return asyncio.run(collect())


def test_untruncated_partition_is_passed_through_untouched():
    base = result([row("MJ-02-1-01-CR-0000001-2026")])
    source = FakeSource(base, ["MDJ-02-1-01"], {})
    parts = run(source)
    assert len(parts) == 1
    assert parts[0].coverage_status == COVERAGE_COMPLETE
    assert source.searches == 1


def test_mdj_truncation_is_recovered_by_office_enumeration():
    base = result(
        [row("MJ-02-1-01-CR-0000001-2026"), row("MJ-02-2-01-CR-0000002-2026", office="MDJ-02-2-01")],
        notice=NOTICE_MDJ,
    )
    per_office = {
        "MDJ-02-1-01": result([row("MJ-02-1-01-CR-0000001-2026")], office="MDJ-02-1-01"),
        "MDJ-02-2-01": result([row("MJ-02-2-01-CR-0000002-2026", office="MDJ-02-2-01")], office="MDJ-02-2-01"),
    }
    source = FakeSource(base, ["MDJ-02-1-01", "MDJ-02-2-01"], per_office)
    parts = run(source)
    assert len(parts) == 1
    merged = parts[0]
    assert merged.coverage_status == COVERAGE_COMPLETE
    assert merged.errors == []
    assert [r.docket_number for r in merged.rows] == [
        "MJ-02-1-01-CR-0000001-2026",
        "MJ-02-2-01-CR-0000002-2026",
    ]
    assert source.searches == 3


def test_office_subset_still_covering_every_county_docket_is_complete():
    base = result([row("MJ-02-1-01-CR-0000001-2026")], notice=NOTICE_MDJ)
    source = FakeSource(
        base,
        ["MDJ-02-1-01"],
        {"MDJ-02-1-01": result([row("MJ-02-1-01-CR-0000001-2026")], office="MDJ-02-1-01")},
    )
    parts = run(source)
    assert parts[0].coverage_status == COVERAGE_COMPLETE


def test_common_pleas_truncation_is_recorded_as_a_gap_not_hidden():
    base = result([row("CP-36-CR-0000003-2026")], notice=NOTICE_BOTH)
    source = FakeSource(
        base,
        ["MDJ-02-1-01"],
        {"MDJ-02-1-01": result([row("MJ-02-1-01-CR-0000001-2026")], office="MDJ-02-1-01")},
    )
    parts = run(source)
    merged = parts[0]
    assert merged.coverage_status == COVERAGE_TRUNCATED
    assert merged.truncated_notice == NOTICE_BOTH
    assert any("Common Pleas" in error and "cannot be subdivided" in error for error in merged.errors)


def test_still_truncated_office_keeps_the_partition_partial():
    base = result([row("MJ-02-1-01-CR-0000001-2026")], notice=NOTICE_MDJ)
    source = FakeSource(
        base,
        ["MDJ-02-1-01"],
        {"MDJ-02-1-01": result([row("MJ-02-1-01-CR-0000001-2026")], notice=NOTICE_MDJ, office="MDJ-02-1-01")},
    )
    parts = run(source)
    assert parts[0].coverage_status == COVERAGE_TRUNCATED
    assert any("still exceeded the result cap" in error for error in parts[0].errors)


def test_truncation_that_does_not_name_mdj_is_not_subdivided():
    notice = "Not all results are shown for Common Pleas. Please narrow your search."
    base = result([row("CP-36-CR-0000003-2026")], notice=notice)
    source = FakeSource(base, ["MDJ-02-1-01"], {})
    parts = run(source)
    assert parts[0].coverage_status == COVERAGE_TRUNCATED
    assert source.searches == 1


def test_only_criminal_dockets_are_collected_and_duplicates_collapse():
    base = result(
        [
            row("MJ-02-1-01-CR-0000001-2026"),
            row("MJ-02-1-01-CR-0000001-2026"),
            row("MJ-02-1-01-TR-0000009-2026"),
            row("CP-36-CR-0000003-2026"),
        ]
    )
    source = FakeSource(base, [], {})
    parts = run(source)
    dockets = [r.docket_number for r in parts[0].criminal_rows]
    assert dockets == ["MJ-02-1-01-CR-0000001-2026", "CP-36-CR-0000003-2026"]
    assert parts[0].skipped_rows == 1
    assert len(UJSPortalSource._dedupe([parts[0]])) == 2


def test_notice_courts_reads_both_courts_when_named():
    assert _notice_courts(NOTICE_MDJ) == ["Magisterial District"]
    assert _notice_courts(NOTICE_BOTH) == ["Common Pleas", "Magisterial District"]
    assert _notice_courts("something unexpected") == ["Unspecified"]


class CalendarSource(UJSPortalSource):
    """A portal whose only behavior is one docket per (day, county)."""

    def __init__(self):
        super().__init__(settings=None)
        self.searches = 0

    async def search_partition(self, filing_date, county=None, court_office=None):
        self.searches += 1
        docket = f"CP-36-CR-{filing_date.strftime('%m%d')}-{county[:3].upper()}"
        return PartitionResult(
            filing_date=filing_date,
            county=county,
            rows=[row(docket, county=county, office=None)],
            pages_visited=1,
            coverage_status=COVERAGE_COMPLETE,
        )


def test_days_are_emitted_chronologically_and_each_county_once():
    source = CalendarSource()

    async def collect():
        return [
            part
            async for part in source.iter_partitions(date(2026, 11, 3), date(2026, 11, 5), ["Adams", "York"])
        ]

    parts = asyncio.run(collect())
    order = [(part.filing_date.isoformat(), part.county) for part in parts]
    assert order == [
        ("2026-11-03", "Adams"),
        ("2026-11-03", "York"),
        ("2026-11-04", "Adams"),
        ("2026-11-04", "York"),
        ("2026-11-05", "Adams"),
        ("2026-11-05", "York"),
    ]
    assert len({part.rows[0].docket_number for part in parts}) == 6


def test_skip_avoids_searching_finished_partitions():
    source = CalendarSource()

    async def collect():
        return [
            part
            async for part in source.iter_partitions(
                date(2026, 11, 3),
                date(2026, 11, 5),
                ["Adams"],
                skip={(date(2026, 11, 4), "Adams")},
            )
        ]

    parts = asyncio.run(collect())
    assert [part.filing_date for part in parts] == [date(2026, 11, 3), date(2026, 11, 5)]
    assert source.searches == 2


def test_range_beyond_the_portal_limit_is_refused():
    source = FakeSource(result([]), [], {})

    async def collect():
        return [
            part
            async for part in source.iter_partitions(date(2026, 1, 1), date(2026, 12, 31), ["Lancaster"])
        ]

    try:
        asyncio.run(collect())
    except ValueError as exc:
        assert "180 days" in str(exc)
    else:  # pragma: no cover - failure path
        raise AssertionError("expected the 180-day portal limit to be enforced")
