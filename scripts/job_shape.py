"""Print the stored shape of one job: partitions, dockets, charges, results, tasks."""
from __future__ import annotations

import sys

from sqlalchemy import func, select

from pa_docket.config import Settings
from pa_docket.db.models import (
    ChargeRow,
    CollectionJob,
    CollectionPartition,
    Docket,
    ExtractionResult,
    SourceDocument,
    TaskQueue,
)
from pa_docket.db.session import make_session_factory


def main(job_id: str) -> int:
    settings = Settings.from_env()
    _engine, session_factory = make_session_factory(settings)
    with session_factory() as session:
        job = session.get(CollectionJob, job_id)
        if job is None:
            print("no such job", job_id)
            return 1
        docket_ids = list(
            session.scalars(
                select(ExtractionResult.docket_id)
                .where(ExtractionResult.job_id == job_id)
                .distinct()
            )
        )
        docket_ids = [d for d in docket_ids if d is not None]
        dockets = (
            list(session.scalars(select(Docket).where(Docket.id.in_(docket_ids))))
            if docket_ids
            else []
        )
        charges = (
            session.scalar(
                select(func.count())
                .select_from(ChargeRow)
                .where(ChargeRow.docket_id.in_(docket_ids))
            )
            if docket_ids
            else 0
        )
        results = session.scalar(
            select(func.count())
            .select_from(ExtractionResult)
            .where(ExtractionResult.job_id == job_id)
        )
        documents = (
            session.scalar(
                select(func.count())
                .select_from(SourceDocument)
                .where(SourceDocument.docket_id.in_(docket_ids))
            )
            if docket_ids
            else 0
        )
        partitions = session.scalar(
            select(func.count())
            .select_from(CollectionPartition)
            .where(CollectionPartition.job_id == job_id)
        )
        tasks = dict(
            session.execute(
                select(TaskQueue.status, func.count())
                .where(TaskQueue.job_id == job_id)
                .group_by(TaskQueue.status)
            ).all()
        )
        duplicates = len(dockets) - len({d.docket_number for d in dockets})

    print(f"job            = {job_id}")
    print(f"status         = {job.status}")
    print(f"partitions     = {partitions}")
    print(f"dockets        = {len(dockets)} (duplicate docket numbers: {duplicates})")
    print(f"documents      = {documents}")
    print(f"charge_rows    = {charges}")
    print(f"extractions    = {results}")
    print(f"tasks          = {tasks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
