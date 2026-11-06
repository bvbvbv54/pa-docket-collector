"""Job reporting: the stored record as the operator sees it.

Both the JSON API and the HTML job page read from this one function so the two
surfaces can never disagree about what the database holds.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pa_docket.db.models import (
    CollectionJob,
    CollectionPartition,
    ExtractionResult,
    SourceDocument,
    TaskQueue,
)


def _field(field: dict | None) -> str | None:
    if not field:
        return None
    value = field.get("normalized_value")
    if value is None:
        value = field.get("raw_value")
    return None if value is None else str(value)


def _presence(field: dict | None, default: str = "ABSENT") -> str:
    if not field:
        return default
    return str(field.get("presence_status") or default)


def job_summary(session: Session, job_id: str) -> dict | None:
    job = session.get(CollectionJob, job_id)
    if job is None:
        return None

    partitions = list(
        session.scalars(
            select(CollectionPartition)
            .where(CollectionPartition.job_id == job_id)
            .order_by(CollectionPartition.filing_date, CollectionPartition.county)
        )
    )
    tasks = dict(
        session.execute(
            select(TaskQueue.status, func.count())
            .where(TaskQueue.job_id == job_id)
            .group_by(TaskQueue.status)
        ).all()
    )
    results = list(
        session.scalars(
            select(ExtractionResult)
            .where(ExtractionResult.job_id == job_id)
            .order_by(ExtractionResult.id)
        )
    )
    dockets: list[dict] = []
    charge_total = 0
    for result in results:
        record = result.result_json or {}
        defendants = record.get("defendants") or [{}]
        defendant = defendants[0]
        charges = record.get("charges") or []
        charge_total += len(charges)
        dockets.append(
            {
                "docket_number": _field(record.get("docket_number")),
                "court": _field(record.get("court")),
                "county": _field(record.get("county")),
                "filing_date": _field(record.get("filing_date")),
                "defendant_name": _field(defendant.get("name")),
                "defendant_race": _field(defendant.get("race")),
                "race_presence": _presence(defendant.get("race")),
                "case_status": _field(record.get("case_status")),
                "case_disposition": _field((record.get("metadata") or {}).get("case_disposition")),
                "validation_status": result.validation_status,
                "document_family": (record.get("metadata") or {}).get("document_family"),
                "source_pdf_url": record.get("source_pdf_url"),
                "charge_count": len(charges),
                "charges": [
                    {
                        "charge_number": _field(charge.get("charge_number")),
                        "description": _field(charge.get("description")),
                        "statute": _field(charge.get("statute")),
                        "offense_date": _field(charge.get("offense_date")),
                        "arresting_officer": _field(charge.get("arresting_officer")),
                        "disposition": _field(charge.get("disposition")),
                        "disposition_presence": _presence(charge.get("disposition")),
                        "disposition_date": _field(charge.get("disposition_date")),
                    }
                    for charge in charges
                ],
            }
        )

    document_count = (
        session.scalar(
            select(func.count())
            .select_from(SourceDocument)
            .where(
                SourceDocument.docket_id.in_(
                    [result.docket_id for result in results if result.docket_id is not None]
                )
            )
        )
        if results
        else 0
    )
    return {
        "id": job.id,
        "status": job.status,
        "params": job.params,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "total_tasks": job.total_tasks,
        "completed_tasks": job.completed_tasks,
        "failed_tasks": job.failed_tasks,
        "tasks": tasks,
        "docket_count": len(dockets),
        "charge_row_count": charge_total,
        "document_count": document_count,
        "coverage": [
            {
                "filing_date": p.filing_date.isoformat() if p.filing_date else None,
                "county": p.county,
                "court_type": p.court_type,
                "result_count": p.result_count,
                "pages_visited": p.pages_visited,
                "pagination_complete": p.pagination_complete,
                "coverage_status": p.coverage_status,
                "errors": list(p.errors or []),
            }
            for p in partitions
        ],
        "dockets": dockets,
    }


def coverage_gaps(summary: dict) -> list[str]:
    """Every partition that did not prove complete coverage, stated plainly."""
    gaps: list[str] = []
    for partition in summary.get("coverage", []):
        if partition["coverage_status"] == "COMPLETE":
            continue
        label = " ".join(
            part
            for part in (partition["filing_date"], partition["county"], partition["court_type"])
            if part
        )
        gaps.append(f"{label or 'partition'}: {partition['coverage_status']}")
        gaps.extend(partition["errors"])
    return gaps


__all__ = ["coverage_gaps", "job_summary"]
