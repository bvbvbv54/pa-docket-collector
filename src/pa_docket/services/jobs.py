from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pa_docket.config import Settings
from pa_docket.db.models import AuditEvent, CollectionJob, CollectionPartition, TaskQueue
from pa_docket.documents.storage import store_pdf
from pa_docket.sources.fixture.generator import SyntheticDocket


def create_fixture_job(
    session_factory,
    settings: Settings,
    cases: Iterable[tuple[SyntheticDocket, bytes]],
    params: dict,
    *,
    max_pdfs: int | None = None,
) -> str:
    unique = {}
    limit = min(max_pdfs or settings.max_pdfs_per_job, settings.max_pdfs_per_job)
    for case, data in cases:
        if case.docket_number in unique:
            continue
        if len(unique) >= limit:
            break
        digest, path = store_pdf(data, settings.storage_dir)
        unique[case.docket_number] = (case, digest, path, len(data))
    cases = sorted(
        unique.values(),
        key=lambda item: (datetime.strptime(item[0].filing_date, "%m/%d/%Y").date(), item[0].docket_number),
    )
    with session_factory() as session, session.begin():
        job = CollectionJob(status="QUEUED", params=params, total_tasks=len(cases))
        session.add(job)
        session.flush()
        partitions = {}
        for case, digest, path, byte_count in cases:
            filed = datetime.strptime(case.filing_date, "%m/%d/%Y").date()
            key = (filed, case.county, case.court)
            partition = partitions.get(key)
            if partition is None:
                partition = CollectionPartition(
                    job_id=job.id,
                    filing_date=filed,
                    county=case.county,
                    court_type=case.court,
                    params={
                        "filing_date": filed.isoformat(),
                        "county": case.county,
                        "court_type": case.court,
                    },
                    started_at=datetime.now(UTC),
                    coverage_status="IN_PROGRESS",
                )
                session.add(partition)
                session.flush()
                partitions[key] = partition
            partition.result_count += 1
            session.add(
                TaskQueue(
                    job_id=job.id,
                    partition_id=partition.id,
                    task_type="EXTRACT_PDF",
                    payload={
                        "docket_number": case.docket_number,
                        "document_hash": digest,
                        "storage_path": str(path),
                        "source_url": None,
                        "byte_count": byte_count,
                    },
                )
            )
        if not cases:
            partition = CollectionPartition(
                job_id=job.id,
                params=params,
                result_count=0,
                pages_visited=0,
                pagination_complete=True,
                coverage_status="ZERO_RESULTS",
                completed_at=datetime.now(UTC),
            )
            session.add(partition)
            job.status = "COMPLETED"
            job.completed_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                event_type="JOB_CREATED",
                job_id=job.id,
                details={
                    "source": "fixture",
                    "task_count": len(cases),
                    "partition_count": len(partitions),
                },
            )
        )
        return job.id


def get_job(session: Session, job_id: str) -> CollectionJob | None:
    return session.get(CollectionJob, job_id)


def list_jobs(session: Session, limit: int = 100) -> list[CollectionJob]:
    return list(session.scalars(select(CollectionJob).order_by(CollectionJob.created_at.desc()).limit(limit)))


def resume_job(session_factory, job_id: str) -> int:
    """Make retryable and lease-expired tasks available again; completed tasks stay immutable."""
    now = datetime.now(UTC)
    with session_factory() as session, session.begin():
        job = session.get(CollectionJob, job_id)
        if not job:
            raise LookupError("Job not found")
        tasks = session.scalars(
            select(TaskQueue).where(TaskQueue.job_id == job_id, TaskQueue.status.in_(["FAILED", "RETRY"]))
        )
        count = 0
        for task in tasks:
            task.status = "PENDING"
            task.available_at = now
            task.last_error = None
            count += 1
        job.status = "QUEUED"
        job.completed_at = None
        session.add(AuditEvent(event_type="JOB_RESUMED", job_id=job_id, details={"tasks_reset": count}))
        return count
