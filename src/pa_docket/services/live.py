"""Live collection: search the portal, store docket PDFs, enqueue extraction.

The service owns ordering, deduplication and coverage bookkeeping. It never
holds a database transaction open across network I/O, so a crash mid-collection
leaves the database at the last committed partition rather than at the last
successful navigation.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select

from pa_docket.config import Settings
from pa_docket.db.models import (
    AuditEvent,
    CollectionJob,
    CollectionPartition,
    Docket,
    ExtractionResult,
    TaskQueue,
)
from pa_docket.documents.storage import InvalidPdfError
from pa_docket.sources.base import DocketRef
from pa_docket.sources.ujs.source import (
    COVERAGE_COMPLETE,
    COVERAGE_FAILED,
    COVERAGE_TRUNCATED,
    COVERAGE_ZERO,
    PartitionResult,
    PortalStructureError,
    PortalUnreachableError,
    UJSPortalSource,
    parse_us_date,
)


@dataclass
class LiveStats:
    job_id: str = ""
    partitions: int = 0
    rows_seen: int = 0
    criminal_dockets: int = 0
    skipped_non_criminal: int = 0
    documents_stored: int = 0
    documents_failed: int = 0
    duplicates: int = 0
    dockets_requested: int = 0
    dockets_not_found: int = 0
    coverage_gaps: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "partitions": self.partitions,
            "rows_seen": self.rows_seen,
            "criminal_dockets": self.criminal_dockets,
            "skipped_non_criminal": self.skipped_non_criminal,
            "documents_stored": self.documents_stored,
            "documents_failed": self.documents_failed,
            "duplicates": self.duplicates,
            "dockets_requested": self.dockets_requested,
            "dockets_not_found": self.dockets_not_found,
            "coverage_gaps": self.coverage_gaps,
            "failures": self.failures,
        }


def _progress(enabled: bool, message: str) -> None:
    if enabled:
        print(message, file=sys.stderr, flush=True)


def _partition_status(result: PartitionResult) -> str:
    if result.coverage_status == COVERAGE_FAILED:
        return "BLOCKED"
    if result.coverage_status == COVERAGE_TRUNCATED:
        return "PARTIAL"
    if result.coverage_status == COVERAGE_ZERO:
        return "ZERO_RESULTS"
    return "COMPLETE"


def _start_job(session_factory, settings: Settings, params: dict) -> str:
    with session_factory() as session, session.begin():
        job = CollectionJob(status="RUNNING", params=params, started_at=datetime.now(UTC))
        session.add(job)
        session.flush()
        session.add(
            AuditEvent(
                event_type="LIVE_COLLECTION_STARTED",
                job_id=job.id,
                details={
                    "date_from": params.get("date_from"),
                    "date_to": params.get("date_to"),
                    "counties": params.get("counties"),
                    "network_mode": settings.network_mode,
                },
            )
        )
        return job.id


def _finished_partitions(session_factory, job_id: str) -> set[tuple[date, str]] | None:
    """(day, county) pairs a resumed job can skip without a search."""
    with session_factory() as session:
        job = session.get(CollectionJob, job_id)
        if job is None:
            return None
        return {
            (partition.filing_date, partition.county)
            for partition in session.scalars(
                select(CollectionPartition).where(
                    CollectionPartition.job_id == job_id,
                    CollectionPartition.coverage_status.in_(["COMPLETE", "ZERO_RESULTS"]),
                )
            )
            if partition.filing_date is not None and partition.county
        }


def _record_partition(session_factory, job_id: str, result: PartitionResult) -> int:
    """Open the partition row before any download so tasks can point at it.

    Coverage is written when the partition closes (``_close_partition``); a
    partition that opens here is IN_PROGRESS, never COMPLETE, so a crash mid-
    partition can never be read as full coverage.
    """
    with session_factory() as session, session.begin():
        partition = CollectionPartition(
            job_id=job_id,
            filing_date=result.filing_date,
            county=result.county,
            court_type=", ".join(result.notice_courts) or None,
            params={
                "filing_date": result.filing_date.isoformat(),
                "county": result.county,
                "court_office": result.court_office,
                "truncated_notice": result.truncated_notice,
                "notice_courts": result.notice_courts,
                # Grid rows and criminal dockets are different facts: the grid
                # also renders traffic and civil cases this job does not collect.
                "grid_rows": len(result.rows),
                "criminal_dockets": len(result.criminal_rows),
            },
            started_at=datetime.now(UTC),
            result_count=len(result.criminal_rows),
            pages_visited=result.pages_visited,
            coverage_status="IN_PROGRESS",
            errors=[],
        )
        session.add(partition)
        session.flush()
        return partition.id


def _abort_partition(session_factory, partition_id: int, exc: Exception) -> None:
    """Seal a partition that died mid-download so it never reads as open or complete."""
    with session_factory() as session, session.begin():
        partition = session.get(CollectionPartition, partition_id)
        if partition is None:
            return
        partition.completed_at = datetime.now(UTC)
        partition.coverage_status = "FAILED"
        partition.pagination_complete = False
        partition.errors = [
            *(partition.errors or []),
            f"COLLECTION_ABORTED: {type(exc).__name__}: {exc}"[:2000],
        ]


def _close_partition(
    session_factory,
    partition_id: int,
    result: PartitionResult,
    *,
    downloaded: int,
    skipped_for_limit: bool,
) -> None:
    """Seal a partition with the coverage the portal actually delivered.

    Enumerating a partition and downloading every docket in it are different
    facts. When a docket limit stops the download short, the partition is
    PARTIAL and says so: the grid saw rows this job never retrieved.
    """
    errors = list(result.errors)
    status = _partition_status(result)
    if skipped_for_limit:
        missing = len(result.criminal_rows) - downloaded
        errors.append(
            f"Docket limit reached: {downloaded} of {len(result.criminal_rows)} criminal "
            f"dockets downloaded, {missing} not retrieved"
        )
        if status == "COMPLETE" or status == "ZERO_RESULTS":
            status = "PARTIAL"
    with session_factory() as session, session.begin():
        partition = session.get(CollectionPartition, partition_id)
        if partition is None:
            return
        partition.completed_at = datetime.now(UTC)
        partition.coverage_status = status
        partition.pagination_complete = status == "COMPLETE" or status == "ZERO_RESULTS"
        partition.errors = errors


def _queued_dockets(session_factory, job_id: str) -> set[str]:
    with session_factory() as session:
        rows = session.execute(
            select(TaskQueue.payload).where(
                TaskQueue.job_id == job_id, TaskQueue.task_type == "EXTRACT_PDF"
            )
        ).scalars()
        return {
            payload.get("docket_number")
            for payload in rows
            if payload and payload.get("docket_number")
        }


def _enqueue_document(
    session_factory,
    job_id: str,
    partition_id: int | None,
    ref: DocketRef,
    document,
) -> None:
    with session_factory() as session, session.begin():
        session.add(
            TaskQueue(
                job_id=job_id,
                partition_id=partition_id,
                task_type="EXTRACT_PDF",
                payload={
                    "docket_number": ref.docket_number,
                    "expected_docket_number": ref.docket_number,
                    "document_hash": document.document_hash,
                    "storage_path": document.storage_path,
                    "source_url": document.source_url,
                    "byte_count": document.byte_count,
                    "court_type": ref.court_type,
                    "county": ref.county,
                    "filing_date": ref.filing_date.isoformat() if ref.filing_date else None,
                },
            )
        )


def _finish_job(session_factory, job_id: str, stats: LiveStats) -> None:
    with session_factory() as session, session.begin():
        job = session.get(CollectionJob, job_id)
        if job is None:
            return
        tasks = session.execute(select(TaskQueue.status).where(TaskQueue.job_id == job_id)).scalars()
        statuses = list(tasks)
        job.total_tasks = len(statuses)
        job.completed_tasks = statuses.count("COMPLETED")
        job.failed_tasks = statuses.count("FAILED")
        if stats.failures:
            job.status = "PARTIAL"
        elif stats.coverage_gaps:
            job.status = "PARTIAL"
        elif job.total_tasks == 0:
            job.status = "COMPLETED"
        else:
            job.status = "QUEUED"
        job.completed_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                event_type="LIVE_COLLECTION_FINISHED",
                job_id=job_id,
                details=stats.as_dict(),
            )
        )


async def collect_live(
    session_factory,
    settings: Settings,
    *,
    date_from: date,
    date_to: date,
    counties: Iterable[str] | None = None,
    max_dockets: int | None = None,
    resume_job_id: str | None = None,
    source: UJSPortalSource | None = None,
    progress: bool = False,
) -> LiveStats:
    """Run one live collection job and return its accounting.

    ``resume_job_id`` continues an existing job: partitions already sealed
    COMPLETE or ZERO_RESULTS are skipped without a search, so adding filing dates
    to a range never repeats finished work.
    """
    if date_from > date_to:
        raise ValueError("date_from must not be after date_to")
    limit = min(max_dockets or settings.max_pdfs_per_job, settings.max_pdfs_per_job)
    county_list = sorted({county.strip() for county in (counties or []) if county and county.strip()})
    params = {
        "source": "ujs-live",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "counties": county_list or "ALL",
        "max_dockets": limit,
    }
    skip: set[tuple[date, str]] = set()
    if resume_job_id:
        done = _finished_partitions(session_factory, resume_job_id)
        if done is None:
            raise LookupError(f"job not found: {resume_job_id}")
        skip = done
        stats = LiveStats(job_id=resume_job_id)
        with session_factory() as session, session.begin():
            job = session.get(CollectionJob, resume_job_id)
            if job is not None:
                previous = dict(job.params or {})
                job.params = {
                    **previous,
                    "date_from": min(previous.get("date_from", params["date_from"]), params["date_from"]),
                    "date_to": max(previous.get("date_to", params["date_to"]), params["date_to"]),
                    "counties": params["counties"],
                }
                # A resumed job is open again until _finish_job seals it. A crash
                # mid-resume must never leave a COMPLETED label on open work.
                job.status = "RUNNING"
                job.completed_at = None
            session.add(
                AuditEvent(
                    event_type="LIVE_COLLECTION_RESUMED",
                    job_id=resume_job_id,
                    details={
                        "date_from": params["date_from"],
                        "date_to": params["date_to"],
                        "skipped_partitions": sorted(
                            f"{day.isoformat()} {county}" for day, county in skip
                        ),
                    },
                )
            )
    else:
        stats = LiveStats(job_id=_start_job(session_factory, settings, params))
    owned = source is None
    portal = source or UJSPortalSource(settings)
    seen: set[str] = set()
    _progress(
        progress,
        f"job={stats.job_id} range={date_from.isoformat()}..{date_to.isoformat()} "
        f"counties={','.join(county_list) or 'ALL'} skipped_partitions={len(skip)}",
    )
    try:
        if owned:
            await portal.open()
        async for result in portal.iter_partitions(date_from, date_to, county_list, skip=skip):
            stats.partitions += 1
            stats.rows_seen += len(result.rows)
            stats.skipped_non_criminal += result.skipped_rows
            if result.coverage_status != COVERAGE_COMPLETE:
                # The notice is only a gap if recovery did not close it. A
                # truncated grid whose per-office union proved complete is
                # complete coverage, and saying otherwise would be false.
                stats.coverage_gaps.append(
                    f"{result.filing_date.isoformat()} {result.county}: "
                    f"{result.coverage_status}: "
                    f"{result.truncated_notice or '; '.join(result.errors) or 'no detail'}"
                )
            if result.coverage_status == COVERAGE_FAILED:
                stats.failures.extend(
                    f"{result.filing_date.isoformat()} {result.county}: {error}"
                    for error in result.errors
                )
            partition_id = _record_partition(session_factory, stats.job_id, result)
            try:
                already = _queued_dockets(session_factory, stats.job_id)
                downloaded_here = 0
                skipped_for_limit = False

                for row in result.criminal_rows:
                    if stats.documents_stored >= limit:
                        skipped_for_limit = True
                        stats.coverage_gaps.append(
                            f"Docket limit {limit} reached; remaining partitions not downloaded"
                        )
                        break
                    if row.docket_number in seen or row.docket_number in already:
                        stats.duplicates += 1
                        continue
                    seen.add(row.docket_number)
                    stats.criminal_dockets += 1
                    ref = DocketRef(
                        docket_number=row.docket_number,
                        court_type=row.court_type or None,
                        county=row.county or result.county,
                        filing_date=result.filing_date,
                        detail_url=row.detail_url,
                    )
                    try:
                        document = await portal.get_document(ref)
                    except (
                        PortalUnreachableError,
                        PortalStructureError,
                        InvalidPdfError,
                        OSError,
                    ) as exc:
                        # One docket that cannot be fetched is a named gap, not the
                        # end of the partition. It is never counted as retrieved.
                        stats.documents_failed += 1
                        stats.failures.append(f"{row.docket_number}: {type(exc).__name__}: {exc}"[:300])
                        continue
                    _enqueue_document(session_factory, stats.job_id, partition_id, ref, document)
                    stats.documents_stored += 1
                    downloaded_here += 1
                if stats.documents_stored >= limit and not skipped_for_limit:
                    skipped_for_limit = downloaded_here < len(result.criminal_rows)
                _close_partition(
                    session_factory,
                    partition_id,
                    result,
                    downloaded=downloaded_here,
                    skipped_for_limit=skipped_for_limit,
                )
                _progress(
                    progress,
                    f"[{result.filing_date.isoformat()} {result.county}] "
                    f"{_partition_status(result) if not skipped_for_limit else 'PARTIAL'} "
                    f"grid={len(result.rows)} criminal={len(result.criminal_rows)} "
                    f"downloaded={downloaded_here} total={stats.documents_stored}"
                    + (
                        f" notice={result.truncated_notice[:80]}"
                        if result.truncated_notice
                        else ""
                    ),
                )
            except Exception as exc:
                # Unexpected failure inside a partition: seal it FAILED so a
                # crash is never read as open coverage, then let the job finalize.
                _abort_partition(session_factory, partition_id, exc)
                stats.failures.append(
                    f"{result.filing_date.isoformat()} {result.county}: "
                    f"COLLECTION_ABORTED: {type(exc).__name__}: {exc}"[:300]
                )
                raise
            if stats.documents_stored >= limit:
                break
    finally:
        if owned:
            await portal.close()
        _finish_job(session_factory, stats.job_id, stats)
    return stats


def stored_docket_numbers(
    session_factory,
    *,
    job_id: str | None = None,
    docket_numbers: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[str]:
    """Resolve the dockets a refresh should revisit.

    Explicit numbers win. Otherwise every docket stored for the job (or every
    docket in the database when no job is named) is refreshed once.
    """
    explicit = [
        " ".join(str(number).upper().split())
        for number in (docket_numbers or [])
        if number and str(number).strip()
    ]
    if explicit:
        numbers = list(dict.fromkeys(explicit))
    else:
        with session_factory() as session:
            statement = select(Docket.docket_number).distinct()
            if job_id:
                statement = statement.join(
                    ExtractionResult, ExtractionResult.docket_id == Docket.id
                ).where(ExtractionResult.job_id == job_id)
            numbers = [
                number
                for number in session.scalars(statement.order_by(Docket.docket_number))
                if number
            ]
    return numbers[:limit] if limit else numbers


async def collect_dockets(
    session_factory,
    settings: Settings,
    docket_numbers: Iterable[str],
    *,
    job_kind: str = "ujs-docket",
    extra_params: dict | None = None,
    source: UJSPortalSource | None = None,
    progress: bool = False,
) -> LiveStats:
    """Retrieve and enqueue extraction for a named set of docket numbers.

    Every number is re-searched in Docket Number mode so the report URL is
    freshly signed; a stored URL is never trusted to still resolve.
    """
    numbers = list(
        dict.fromkeys(
            " ".join(str(number).upper().split())
            for number in docket_numbers
            if number and str(number).strip()
        )
    )
    if not numbers:
        raise ValueError("At least one docket number is required")
    params = {
        "source": job_kind,
        "docket_numbers": numbers,
        **(extra_params or {}),
    }
    stats = LiveStats(job_id=_start_job(session_factory, settings, params))
    stats.dockets_requested = len(numbers)
    owned = source is None
    portal = source or UJSPortalSource(settings)
    _progress(progress, f"job={stats.job_id} dockets={len(numbers)}")
    try:
        if owned:
            await portal.open()
        for number in numbers:
            try:
                lookup = await portal.lookup_docket(number)
            except Exception as exc:
                stats.documents_failed += 1
                stats.failures.append(f"{number}: LOOKUP_FAILED: {type(exc).__name__}: {exc}"[:300])
                _progress(progress, f"[{number}] LOOKUP_FAILED")
                continue
            if not lookup.found:
                stats.dockets_not_found += 1
                stats.documents_failed += 1
                stats.failures.append(
                    f"{number}: NOT_FOUND: {(lookup.body_excerpt or '; '.join(lookup.errors))[:200]}"
                )
                _progress(progress, f"[{number}] NOT_FOUND")
                continue
            row = lookup.row
            if row is None:
                stats.documents_failed += 1
                stats.failures.append(f"{number}: NO_GRID_ROW")
                continue
            ref = DocketRef(
                docket_number=row.docket_number,
                court_type=row.court_type or None,
                county=row.county or None,
                filing_date=parse_us_date(row.filing_date),
                detail_url=row.detail_url,
            )
            try:
                document = await portal.get_document(ref)
            except (
                PortalUnreachableError,
                PortalStructureError,
                InvalidPdfError,
                OSError,
            ) as exc:
                stats.documents_failed += 1
                stats.failures.append(f"{number}: {type(exc).__name__}: {exc}"[:300])
                _progress(progress, f"[{number}] FETCH_FAILED {type(exc).__name__}")
                continue
            _enqueue_document(session_factory, stats.job_id, None, ref, document)
            stats.documents_stored += 1
            stats.criminal_dockets += 1
            _progress(
                progress,
                f"[{number}] RETRIEVED county={row.county} filed={row.filing_date} "
                f"stored={stats.documents_stored}",
            )
    finally:
        if owned:
            await portal.close()
        _finish_job(session_factory, stats.job_id, stats)
    return stats


async def refresh_dockets(
    session_factory,
    settings: Settings,
    *,
    job_id: str | None = None,
    docket_numbers: Iterable[str] | None = None,
    limit: int | None = None,
    source: UJSPortalSource | None = None,
    progress: bool = False,
) -> LiveStats:
    """Re-retrieve stored dockets and let the worker record any revisions."""
    numbers = stored_docket_numbers(
        session_factory, job_id=job_id, docket_numbers=docket_numbers, limit=limit
    )
    if not numbers:
        raise LookupError("No stored dockets match the refresh request")
    with session_factory() as session:
        if job_id and session.get(CollectionJob, job_id) is None:
            raise LookupError(f"job not found: {job_id}")
    return await collect_dockets(
        session_factory,
        settings,
        numbers,
        job_kind="ujs-refresh",
        extra_params={"refresh_of_job": job_id, "limit": limit},
        source=source,
        progress=progress,
    )
