"""Exercise _close_partition directly against a temp database."""
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import select

from pa_docket.config import Settings
from pa_docket.db.models import CollectionJob, CollectionPartition
from pa_docket.db.session import init_db, make_session_factory
from pa_docket.services.live import _close_partition, _record_partition
from pa_docket.sources.ujs.source import (
    COVERAGE_COMPLETE,
    GridRow,
    PartitionResult,
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    settings = Settings(
        database_url=f"sqlite:///{(tmp / 'probe.sqlite3').as_posix()}",
        storage_dir=tmp / "store",
    )
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    engine, factory = make_session_factory(settings)
    init_db(engine)
    with factory() as session, session.begin():
        job = CollectionJob(status="RUNNING", params={})
        session.add(job)
        session.flush()
        job_id = job.id

    result = PartitionResult(
        filing_date=date(2026, 11, 5),
        county="Lancaster",
        rows=[
            GridRow(
                docket_number=f"MJ-02-1-01-CR-{i:07d}-2026",
                court_type="Magisterial District",
                case_caption="Comm. v. Example",
                case_status="Active",
                filing_date="11/05/2026",
                county="Lancaster",
                court_office="MDJ-02-1-01",
                detail_url=None,
                summary_url=None,
            )
            for i in range(58)
        ],
        coverage_status=COVERAGE_COMPLETE,
        pages_visited=1,
    )
    partition_id = _record_partition(factory, job_id, result)
    _close_partition(
        factory, partition_id, result, downloaded=8, skipped_for_limit=True
    )
    with factory() as session:
        partition = session.scalars(
            select(CollectionPartition).where(CollectionPartition.job_id == job_id)
        ).one()
        print("status:", partition.coverage_status)
        print("errors:", partition.errors)
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
