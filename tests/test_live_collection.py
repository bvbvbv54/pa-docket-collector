"""Live collection resilience: a dead tunnel is a named gap, a crash is sealed.

These tests never touch the network. They drive the collector with a source that
returns prepared partitions so the failure paths are exercised directly.
"""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from playwright.async_api import Error as PlaywrightError
from sqlalchemy import select

from pa_docket.config import Settings
from pa_docket.db.models import CollectionJob, CollectionPartition, TaskQueue
from pa_docket.db.session import init_db, make_session_factory
from pa_docket.services.live import collect_live
from pa_docket.sources.base import DocumentResult
from pa_docket.sources.ujs.source import (
    COVERAGE_COMPLETE,
    GridRow,
    PartitionResult,
    PortalUnreachableError,
    UJSPortalSource,
)

DAY = date(2026, 11, 4)


def make_row(number: int) -> GridRow:
    return GridRow(
        docket_number=f"MJ-01-1-01-CR-{number:07d}-2026",
        court_type="Magisterial District",
        case_caption="Comm. v. Example",
        case_status="Active",
        filing_date="11/04/2026",
        county="Adams",
        court_office="MDJ-01-1-01",
        detail_url=f"https://ujsportal.pacourts.us/Report/MdjDocketSheet?docketNumber=CR-{number}",
        summary_url=None,
    )


def make_partition(rows: list[GridRow]) -> PartitionResult:
    return PartitionResult(
        filing_date=DAY,
        county="Adams",
        rows=rows,
        pages_visited=1,
        coverage_status=COVERAGE_COMPLETE,
    )


def non_criminal_row(number: int) -> GridRow:
    row = make_row(number)
    row.docket_number = f"MJ-01-1-01-TR-{number:07d}-2026"
    return row


def store(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'live.sqlite3').as_posix()}",
        storage_dir=tmp_path / "store",
    )
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    engine, factory = make_session_factory(settings)
    init_db(engine)
    return settings, engine, factory


class StubSource(UJSPortalSource):
    """A source with prepared partitions and a scripted document behavior."""

    def __init__(self, partition, behavior):
        super().__init__(settings=None)
        self._partitions = partition if isinstance(partition, list) else [partition]
        self._behavior = behavior
        self.searched = 0
        self.seen_skip: set[tuple[date, str]] | None = None

    async def iter_partitions(self, date_from, date_to, counties=None, *, max_partitions=None, skip=None):
        self.seen_skip = skip
        for part in self._partitions:
            if skip and (part.filing_date, part.county) in skip:
                continue
            self.searched += 1
            yield part

    async def get_document(self, ref):
        return self._behavior(ref)


def run_collect(settings, factory, source):
    return asyncio.run(
        collect_live(
            factory,
            settings,
            date_from=DAY,
            date_to=DAY,
            counties=["Adams"],
            max_dockets=10,
            source=source,
        )
    )


def test_one_dead_tunnel_fails_only_that_docket(tmp_path):
    settings, engine, factory = store(tmp_path)
    partition = make_partition([make_row(1), make_row(2), make_row(3)])

    def behavior(ref):
        if ref.docket_number.endswith("0000002-2026"):
            raise PortalUnreachableError("DOCUMENT_FETCH_FAILED after 4 attempts: connect ETIMEDOUT")
        suffix = ref.docket_number.split("-")[4]
        return DocumentResult(
            docket_number=ref.docket_number,
            source_url=ref.detail_url,
            storage_path=str(tmp_path / f"{suffix}.pdf"),
            document_hash=f"hash{suffix}",
            byte_count=10,
        )

    stats = run_collect(settings, factory, StubSource(partition, behavior))
    assert stats.documents_stored == 2
    assert stats.documents_failed == 1
    assert any("DOCUMENT_FETCH_FAILED" in failure for failure in stats.failures)

    with factory() as session:
        partition_row = session.scalars(
            select(CollectionPartition).where(CollectionPartition.job_id == stats.job_id)
        ).one()
        assert partition_row.coverage_status == "COMPLETE"
        assert len(session.execute(select(TaskQueue).where(TaskQueue.job_id == stats.job_id)).all()) == 2
        job = session.get(CollectionJob, stats.job_id)
        assert job.status == "PARTIAL"  # a failed docket is never reported complete
    engine.dispose()


def test_partition_count_reports_criminal_dockets_not_grid_rows(tmp_path):
    settings, engine, factory = store(tmp_path)
    partition = make_partition([make_row(1), non_criminal_row(9), make_row(2)])

    def behavior(ref):
        return DocumentResult(
            docket_number=ref.docket_number,
            source_url=ref.detail_url,
            storage_path=str(tmp_path / "x.pdf"),
            document_hash="hash",
            byte_count=10,
        )

    stats = run_collect(settings, factory, StubSource(partition, behavior))
    assert stats.documents_stored == 2

    with factory() as session:
        partition_row = session.scalars(
            select(CollectionPartition).where(CollectionPartition.job_id == stats.job_id)
        ).one()
        assert partition_row.result_count == 2
        assert partition_row.params["grid_rows"] == 3
        assert partition_row.params["criminal_dockets"] == 2
    engine.dispose()


def test_resume_skips_finished_partitions_and_keeps_the_same_job(tmp_path):
    settings, engine, factory = store(tmp_path)

    def behavior(ref):
        return DocumentResult(
            docket_number=ref.docket_number,
            source_url=ref.detail_url,
            storage_path=str(tmp_path / "x.pdf"),
            document_hash="hash",
            byte_count=10,
        )

    day1 = make_partition([make_row(1)])
    first = asyncio.run(
        collect_live(
            factory,
            settings,
            date_from=DAY,
            date_to=DAY,
            counties=["Adams"],
            max_dockets=10,
            source=StubSource(day1, behavior),
        )
    )

    day2 = make_partition([make_row(2)])
    day2.filing_date = date(2026, 11, 5)
    resuming = StubSource([day1, day2], behavior)
    second = asyncio.run(
        collect_live(
            factory,
            settings,
            date_from=DAY,
            date_to=date(2026, 11, 5),
            counties=["Adams"],
            max_dockets=10,
            resume_job_id=first.job_id,
            source=resuming,
        )
    )

    assert second.job_id == first.job_id
    assert second.partitions == 1
    assert resuming.searched == 1
    assert resuming.seen_skip == {(DAY, "Adams")}

    with factory() as session:
        jobs = list(session.scalars(select(CollectionJob)))
        assert len(jobs) == 1
        partitions = list(
            session.scalars(
                select(CollectionPartition)
                .where(CollectionPartition.job_id == first.job_id)
                .order_by(CollectionPartition.filing_date)
            )
        )
        assert [p.filing_date for p in partitions] == [DAY, date(2026, 11, 5)]
        assert all(p.coverage_status == "COMPLETE" for p in partitions)
        assert not session.scalars(
            select(TaskQueue).where(
                TaskQueue.job_id == first.job_id, TaskQueue.status == "FAILED"
            )
        ).all()
    engine.dispose()


def test_resume_of_missing_job_is_refused(tmp_path):
    settings, engine, factory = store(tmp_path)
    partition = make_partition([make_row(1)])

    with pytest.raises(LookupError):
        asyncio.run(
            collect_live(
                factory,
                settings,
                date_from=DAY,
                date_to=DAY,
                counties=["Adams"],
                resume_job_id="00000000-0000-0000-0000-000000000000",
                source=StubSource(partition, lambda ref: None),
            )
        )
    engine.dispose()


def test_unexpected_crash_seals_the_partition_and_the_job(tmp_path):
    settings, engine, factory = store(tmp_path)
    partition = make_partition([make_row(1), make_row(2)])

    def behavior(ref):
        raise ValueError("parser exploded outside the classified set")

    with pytest.raises(ValueError):
        run_collect(settings, factory, StubSource(partition, behavior))

    with factory() as session:
        jobs = list(session.scalars(select(CollectionJob)))
        assert len(jobs) == 1
        partition_row = session.scalars(
            select(CollectionPartition).where(CollectionPartition.job_id == jobs[0].id)
        ).one()
        assert partition_row.coverage_status == "FAILED"
        assert partition_row.pagination_complete is False
        assert any("COLLECTION_ABORTED" in error for error in partition_row.errors)
        assert jobs[0].status == "PARTIAL"
    engine.dispose()


def test_report_fetch_retries_the_tunnel_then_succeeds(tmp_path):
    settings, engine, factory = store(tmp_path)
    engine.dispose()
    source = UJSPortalSource(settings)
    attempts = {"count": 0}

    class Response:
        status = 200
        headers = {"content-type": "application/pdf"}

        async def body(self):
            return b"%PDF-1.4 body"

    class Request:
        async def get(self, url, timeout=None):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise PlaywrightError("connect ETIMEDOUT 67.213.121.97:823")
            return Response()

    page = SimpleNamespace(context=SimpleNamespace(request=Request()))
    ref = SimpleNamespace(detail_url="https://ujsportal.pacourts.us/Report/CpDocketSheet?dnh=x", docket_number="CP-1")

    async def fetch():
        return await source._fetch_report(page, ref)

    status, _content_type, data = asyncio.run(fetch())
    assert status == 200
    assert data.startswith(b"%PDF")
    assert attempts["count"] == 3


def test_report_fetch_gives_up_after_the_attempt_budget(tmp_path):
    settings, engine, factory = store(tmp_path)
    engine.dispose()
    source = UJSPortalSource(settings)
    attempts = {"count": 0}

    class Request:
        async def get(self, url, timeout=None):
            attempts["count"] += 1
            raise PlaywrightError("connect ETIMEDOUT")

    page = SimpleNamespace(context=SimpleNamespace(request=Request()))
    ref = SimpleNamespace(detail_url="https://ujsportal.pacourts.us/Report/CpDocketSheet?dnh=x", docket_number="CP-1")

    async def fetch():
        return await source._fetch_report(page, ref)

    with pytest.raises(PortalUnreachableError) as raised:
        asyncio.run(fetch())
    assert "DOCUMENT_FETCH_FAILED" in str(raised.value)
    assert attempts["count"] == 4
