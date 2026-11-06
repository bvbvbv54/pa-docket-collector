"""Refresh history, records filtering and named-docket retrieval."""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from pa_docket.config import Settings
from pa_docket.db.models import (
    CollectionJob,
    Docket,
    DocketRevision,
    ExtractionResult,
    TaskQueue,
)
from pa_docket.db.session import init_db, make_session_factory
from pa_docket.exports.csv_exporter import COLUMNS
from pa_docket.exports.xlsx_exporter import write_rows
from pa_docket.services.jobs import create_fixture_job
from pa_docket.services.live import collect_dockets, refresh_dockets, stored_docket_numbers
from pa_docket.services.records import (
    RecordFilters,
    docket_detail,
    export_values,
    iter_record_rows,
)
from pa_docket.sources.fixture.generator import (
    SyntheticCharge,
    SyntheticDefendant,
    SyntheticDocket,
    render_pdf,
)
from pa_docket.sources.ujs.source import DocketLookup, UJSPortalSource
from pa_docket.workers.main import run_worker
from pa_docket.workers.tasks import _changed_fields


def setup_db(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'records.sqlite3').as_posix()}",
        storage_dir=tmp_path / "store",
    )
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    engine, factory = make_session_factory(settings)
    init_db(engine)
    return settings, engine, factory


def make_case(disposition: str = "Pending", race: str | None = "White") -> SyntheticDocket:
    return SyntheticDocket(
        "CP-01-CR-0001427-2026",
        court="Court of Common Pleas",
        county="Adams",
        filing_date="10/05/2026",
        case_status="Active",
        defendants=[SyntheticDefendant("Alex Example", race=race)],
        charges=[
            SyntheticCharge(
                "1",
                "Simple Assault",
                "10/03/2026",
                disposition,
                arresting_officer="J. Parker",
                affiant="J. Parker",
            )
        ],
    )


def run_case(factory, settings, case, label: str) -> str:
    job_id = create_fixture_job(factory, settings, [(case, render_pdf(case))], {"run": label})
    run_worker(factory, settings, concurrency=1)
    return job_id


def test_refresh_is_quiet_when_the_document_is_unchanged(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    case = make_case()
    run_case(factory, settings, case, "first")
    run_case(factory, settings, case, "second")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DocketRevision)) == 0
    engine.dispose()


def test_refresh_records_the_previous_and_current_disposition(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    first_job = run_case(factory, settings, make_case("Pending"), "first")
    second_job = run_case(factory, settings, make_case("Guilty"), "refresh")

    with factory() as session:
        revision = session.scalars(select(DocketRevision)).one()
        assert revision.job_id == second_job
        assert "charge 1 disposition" in revision.changed_fields
        assert revision.previous_record_json["charges"][0]["disposition"]["raw_value"] == "Pending"
        assert revision.new_record_json["charges"][0]["disposition"]["raw_value"] == "Guilty"
        # The current record moved; history kept the old value.
        docket = session.scalars(select(Docket)).one()
        assert docket.record_json["charges"][0]["disposition"]["raw_value"] == "Guilty"
        detail = docket_detail(session, docket.docket_number)
        assert detail is not None
        assert len(detail["revisions"]) == 1
        assert detail["revisions"][0]["previous"]["charges"][0]["disposition"]["raw_value"] == "Pending"
        # The first job's extraction is not rewritten by the refresh.
        first = session.scalars(
            select(ExtractionResult).where(ExtractionResult.job_id == first_job)
        ).one()
        assert first.result_json["charges"][0]["disposition"]["raw_value"] == "Pending"
    engine.dispose()


def test_changed_fields_names_each_charge_field():
    previous = {
        "charges": [
            {
                "defendant_index": 1,
                "charge_number": {"normalized_value": "1"},
                "description": {"normalized_value": "Simple Assault"},
                "disposition": {"normalized_value": None, "presence_status": "MISSING"},
            }
        ]
    }
    current = {
        "charges": [
            {
                "defendant_index": 1,
                "charge_number": {"normalized_value": "1"},
                "description": {"normalized_value": "Simple Assault"},
                "disposition": {"normalized_value": "Guilty", "presence_status": "PRESENT"},
            }
        ]
    }
    assert _changed_fields(previous, current) == ["charge 1 disposition"]
    assert _changed_fields(current, current) == []


def test_records_filter_by_county_charge_and_disposition(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    run_case(factory, settings, make_case("Guilty"), "one")

    other = SyntheticDocket(
        "MJ-01-1-01-CR-0000002-2026",
        county="Lancaster",
        filing_date="10/07/2026",
        defendants=[SyntheticDefendant("Jordan Sample")],
        charges=[SyntheticCharge("1", "Retail Theft", "10/06/2026", "Pending")],
    )
    run_case(factory, settings, other, "two")

    blank = SyntheticDocket(
        "MJ-01-1-01-CR-0000003-2026",
        county="Lancaster",
        filing_date="10/08/2026",
        defendants=[SyntheticDefendant("Casey Blank")],
        charges=[SyntheticCharge("1", "Forgery", "10/04/2026", "")],
    )
    run_case(factory, settings, blank, "three")

    with factory() as session:
        all_rows = list(iter_record_rows(session, RecordFilters()))
        assert len(all_rows) == 3

        adams = list(iter_record_rows(session, RecordFilters(county="Adams")))
        assert [row["docket_number"] for row in adams] == ["CP-01-CR-0001427-2026"]

        theft = list(iter_record_rows(session, RecordFilters(charge="theft")))
        assert [row["defendant_name"] for row in theft] == ["Jordan Sample"]

        guilty = list(iter_record_rows(session, RecordFilters(disposition="guilty")))
        assert [row["defendant_name"] for row in guilty] == ["Alex Example"]

        missing = list(iter_record_rows(session, RecordFilters(disposition="missing")))
        assert [row["defendant_name"] for row in missing] == ["Casey Blank"]
        assert missing[0]["disposition_presence"] != "PRESENT"

        window = list(
            iter_record_rows(
                session,
                RecordFilters(date_from=date(2026, 10, 6), date_to=date(2026, 10, 31)),
            )
        )
        assert [row["docket_number"] for row in window] == [
            "MJ-01-1-01-CR-0000003-2026",
            "MJ-01-1-01-CR-0000002-2026",
        ]

        row = next(r for r in all_rows if r["county"] == "Adams")
        assert len(export_values(row)) == len(COLUMNS)
        assert row["race_presence"] == "PRESENT"
        assert row["defendant_race"] == "White"
    engine.dispose()


def test_filtered_xlsx_export_has_the_filtered_row_count(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    run_case(factory, settings, make_case("Guilty"), "one")
    with factory() as session:
        rows = [export_values(row) for row in iter_record_rows(session, RecordFilters(county="Adams"))]
    output = tmp_path / "filtered.xlsx"
    count = write_rows(rows, COLUMNS, output)
    assert count == 1
    assert output.read_bytes()[:2] == b"PK"
    engine.dispose()


class LookupSource(UJSPortalSource):
    """A source that answers docket lookups from a prepared map."""

    def __init__(self, found: dict[str, SimpleNamespace], document):
        super().__init__(settings=None)
        self.found = found
        self.document = document
        self.looked_up: list[str] = []

    async def lookup_docket(self, docket_number: str) -> DocketLookup:
        self.looked_up.append(docket_number)
        row = self.found.get(docket_number)
        return DocketLookup(
            docket_number=docket_number,
            found=row is not None,
            row=row,
            rows=[row] if row else [],
            coverage_status="COMPLETE" if row else "ZERO_RESULTS",
            body_excerpt="no results" if row is None else "",
        )

    async def get_document(self, ref):
        return self.document(ref)


def test_collect_dockets_reports_a_missing_number_without_stopping_the_batch(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    found = {
        "CP-01-CR-0001427-2026": SimpleNamespace(
            docket_number="CP-01-CR-0001427-2026",
            court_type="Court of Common Pleas",
            county="Adams",
            filing_date="10/05/2026",
            detail_url="https://ujsportal.pacourts.us/Report/CpDocketSheet?dnh=x",
        )
    }

    def document(ref):
        return SimpleNamespace(
            docket_number=ref.docket_number,
            source_url=ref.detail_url,
            document_hash="hash-1",
            storage_path=str(tmp_path / "one.pdf"),
            byte_count=10,
        )

    source = LookupSource(found, document)
    stats = asyncio.run(
        collect_dockets(
            factory,
            settings,
            ["CP-01-CR-0001427-2026", "CP-01-CR-9999999-2026"],
            source=source,
        )
    )
    assert stats.documents_stored == 1
    assert stats.dockets_not_found == 1
    assert stats.failures and "NOT_FOUND" in stats.failures[0]
    assert source.looked_up[0] == "CP-01-CR-0001427-2026"

    with factory() as session:
        job = session.get(CollectionJob, stats.job_id)
        assert job.status == "PARTIAL"
        task = session.scalars(select(TaskQueue).where(TaskQueue.job_id == stats.job_id)).one()
        assert task.payload["docket_number"] == "CP-01-CR-0001427-2026"
        assert task.payload["filing_date"] == "2026-10-05"
    engine.dispose()


def test_stored_docket_numbers_scopes_to_the_job(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    job_id = run_case(factory, settings, make_case(), "one")
    same_job = stored_docket_numbers(factory, job_id=job_id)
    missing = stored_docket_numbers(factory, job_id="00000000-0000-0000-0000-000000000000")
    assert same_job == ["CP-01-CR-0001427-2026"]
    assert missing == []
    engine.dispose()


def test_refresh_refuses_when_no_dockets_match(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(refresh_dockets(factory, settings, job_id=None))
    engine.dispose()
