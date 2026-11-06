from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from pa_docket.db.models import (
    AuditEvent,
    ChargeRow,
    CollectionJob,
    CollectionPartition,
    Docket,
    DocketRevision,
    ExtractionResult,
    SourceDocument,
    TaskQueue,
)
from pa_docket.documents.storage import InvalidPdfError, validate_pdf_bytes
from pa_docket.domain.enums import ValidationStatus
from pa_docket.parsers import NonCriminalDocketError, parse_docket_pdf
from pa_docket.parsers.generic import MalformedPdfError
from pa_docket.validation.service import validate_record
from pa_docket.workers.queue import claim_lock, claim_one, recover_expired


def _field_signature(field: dict | None) -> tuple:
    if not isinstance(field, dict):
        return (None, None)
    return (field.get("normalized_value"), field.get("presence_status"))


def _charge_map(record: dict) -> dict[tuple, dict]:
    mapped: dict[tuple, dict] = {}
    for charge in record.get("charges") or []:
        key = (
            charge.get("defendant_index"),
            (charge.get("charge_number") or {}).get("normalized_value"),
        )
        mapped[key] = charge
    return mapped


def _changed_fields(previous: dict, current: dict) -> list[str]:
    """Name the fields a refresh moved, field by field, charge by charge."""
    changes: list[str] = []
    for key in ("docket_number", "court", "county", "filing_date", "case_status"):
        if _field_signature(previous.get(key)) != _field_signature(current.get(key)):
            changes.append(key)
    if _field_signature((previous.get("metadata") or {}).get("case_disposition")) != _field_signature(
        (current.get("metadata") or {}).get("case_disposition")
    ):
        changes.append("case_disposition")

    old_defendants = {d.get("index", 1): d for d in previous.get("defendants") or []}
    new_defendants = {d.get("index", 1): d for d in current.get("defendants") or []}
    for index in sorted(set(old_defendants) | set(new_defendants)):
        for field in ("name", "race"):
            if _field_signature((old_defendants.get(index) or {}).get(field)) != _field_signature(
                (new_defendants.get(index) or {}).get(field)
            ):
                changes.append(f"defendant {index} {field}")

    old_charges = _charge_map(previous)
    new_charges = _charge_map(current)
    for key in sorted(set(old_charges) | set(new_charges), key=str):
        label = key[1] or f"#{key[0]}"
        if key not in new_charges:
            changes.append(f"charge {label} removed")
            continue
        if key not in old_charges:
            changes.append(f"charge {label} added")
            continue
        for field in (
            "description",
            "statute",
            "offense_date",
            "arresting_officer",
            "disposition",
            "disposition_date",
        ):
            if _field_signature(old_charges[key].get(field)) != _field_signature(
                new_charges[key].get(field)
            ):
                changes.append(f"charge {label} {field}")
    return changes


def _store_record(session, record, task: TaskQueue) -> None:
    docket_no = record.docket_number.normalized_value
    court = record.court.normalized_value
    docket = None
    if docket_no:
        docket = session.scalars(
            select(Docket).where(
                Docket.docket_number == docket_no,
                Docket.court == court if court is not None else Docket.court.is_(None),
            )
        ).first()
    previous_record: dict | None = None
    previous_document_id: int | None = None
    if docket is not None:
        previous_record = dict(docket.record_json or {})
        prior_doc = session.scalars(
            select(SourceDocument)
            .where(SourceDocument.docket_id == docket.id)
            .order_by(SourceDocument.id.desc())
        ).first()
        previous_document_id = prior_doc.id if prior_doc else None
    filed = date.fromisoformat(record.filing_date.normalized_value) if record.filing_date.normalized_value else None
    if docket_no and docket is None:
        docket = Docket(
            docket_number=docket_no,
            court=court,
            county=record.county.normalized_value,
            filing_date=filed,
            case_status=record.case_status.model_dump(mode="json"),
            record_json=record.model_dump(mode="json"),
            validation_status=str(record.validation_status),
            parser_version=record.parser_version,
        )
        session.add(docket)
        session.flush()
    if docket is not None:
        docket.county = record.county.normalized_value
        docket.filing_date = filed
        docket.case_status = record.case_status.model_dump(mode="json")
        docket.record_json = record.model_dump(mode="json")
        docket.validation_status = str(record.validation_status)
        docket.parser_version = record.parser_version
        session.execute(delete(ChargeRow).where(ChargeRow.docket_id == docket.id))
        for charge in record.charges:
            number = charge.charge_number.normalized_value or ""
            session.add(
                ChargeRow(
                    docket_id=docket.id,
                    charge_number=number,
                    defendant_index=charge.defendant_index,
                    record_json=charge.model_dump(mode="json"),
                )
            )
    docket_id = docket.id if docket else None
    source_doc = session.scalars(
        select(SourceDocument).where(
            SourceDocument.docket_id == docket_id,
            SourceDocument.document_hash == task.payload["document_hash"],
        )
    ).first()
    if source_doc is None:
        source_doc = SourceDocument(
            docket_id=docket_id,
            document_hash=task.payload["document_hash"],
            storage_path=task.payload["storage_path"],
            source_url=task.payload.get("source_url"),
            byte_count=task.payload["byte_count"],
        )
        session.add(source_doc)
        session.flush()
    existing = session.scalars(
        select(ExtractionResult).where(
            ExtractionResult.job_id == task.job_id,
            ExtractionResult.docket_id == docket_id,
        )
    ).first()
    if existing is None:
        session.add(
            ExtractionResult(
                docket_id=docket_id,
                source_document_id=source_doc.id,
                job_id=task.job_id,
                result_json=record.model_dump(mode="json"),
                validation_status=str(record.validation_status),
            )
        )
    else:
        # One extraction per docket per job. A restart that re-downloads the same
        # docket produces a fresh PDF (the portal re-signs it, so the bytes and
        # their hash move) and must not append a second export row; the newest
        # document becomes the evidence pointer for the existing result.
        existing.source_document_id = source_doc.id
        existing.result_json = record.model_dump(mode="json")
        existing.validation_status = str(record.validation_status)
    if docket is not None and previous_record:
        current_record = record.model_dump(mode="json")
        changed = _changed_fields(previous_record, current_record)
        if changed:
            session.add(
                DocketRevision(
                    docket_id=docket.id,
                    job_id=task.job_id,
                    previous_document_id=previous_document_id,
                    new_document_id=source_doc.id,
                    previous_record_json=previous_record,
                    new_record_json=current_record,
                    changed_fields=changed,
                )
            )
            session.add(
                AuditEvent(
                    event_type="DOCKET_REVISED",
                    job_id=task.job_id,
                    details={
                        "docket_number": docket_no,
                        "changed_fields": changed[:50],
                        "previous_document_id": previous_document_id,
                        "new_document_id": source_doc.id,
                    },
                )
            )
    session.add(
        AuditEvent(
            event_type="DOCKET_EXTRACTED",
            job_id=task.job_id,
            details={
                "docket_number": docket_no,
                "validation_status": str(record.validation_status),
            },
        )
    )


def process_one(session_factory, settings, worker_id: str, *, max_attempts: int = 3) -> bool:
    with session_factory() as session, session.begin():
        with claim_lock(session):
            recover_expired(session)
            task = claim_one(session, worker_id)
        if task is None:
            return False
        task_id, job_id = task.id, task.job_id
        payload = dict(task.payload)
        task.status = "PROCESSING"
        job = session.get(CollectionJob, job_id)
        if job:
            job.status = "RUNNING"
            job.started_at = job.started_at or datetime.now(UTC)
    try:
        data = Path(payload["storage_path"]).read_bytes()
        validate_pdf_bytes(data)
        record = validate_record(parse_docket_pdf(data, source_url=payload.get("source_url")))
        expected_docket = payload.get("expected_docket_number")
        if expected_docket and record.docket_number.normalized_value != expected_docket:
            record.validation_errors.append(
                "Imported docket identifier does not match the identifier supplied with the PDF"
            )
            record.validation_status = ValidationStatus.NEEDS_REVIEW
        record.source_document_hash = payload["document_hash"]
        with session_factory() as session, session.begin():
            task = session.get(TaskQueue, task_id)
            if task is None or task.worker_id != worker_id:
                return True
            _store_record(session, record, task)
            task.status = "COMPLETED"
            task.lease_until = None
            task.last_error = None
            _finalize_job(session, job_id)
        return True
    except NonCriminalDocketError as exc:
        # A civil or landlord/tenant docket is out of scope, not a failed read.
        with session_factory() as session, session.begin():
            task = session.get(TaskQueue, task_id)
            if task:
                task.status = "CANCELLED"
                task.last_error = f"NON_CRIMINAL_DOCKET: {exc}"[:2000]
                task.lease_until = None
            _finalize_job(session, job_id)
        return True
    except Exception as exc:
        with session_factory() as session, session.begin():
            task = session.get(TaskQueue, task_id)
            if task:
                permanent = isinstance(exc, (InvalidPdfError, MalformedPdfError, FileNotFoundError))
                classification = (
                    "INVALID_PDF"
                    if isinstance(exc, InvalidPdfError)
                    else (
                        "MALFORMED_PDF"
                        if isinstance(exc, MalformedPdfError)
                        else "SOURCE_FILE_MISSING"
                        if isinstance(exc, FileNotFoundError)
                        else "TRANSIENT"
                    )
                )
                task.last_error = f"{classification}: {type(exc).__name__}: {exc}"[:2000]
                task.lease_until = None
                task.status = "FAILED" if permanent or task.attempts >= task.max_attempts else "RETRY"
                task.available_at = datetime.now(UTC)
                if task.status == "RETRY":
                    task.available_at += timedelta(seconds=min(300, 2**task.attempts))
            _finalize_job(session, job_id)
        return True


def _finalize_job(session, job_id: str) -> None:
    job = session.get(CollectionJob, job_id)
    if not job:
        return
    tasks = list(session.scalars(select(TaskQueue).where(TaskQueue.job_id == job_id)))
    partitions = list(session.scalars(select(CollectionPartition).where(CollectionPartition.job_id == job_id)))
    for partition in partitions:
        partition_tasks = [t for t in tasks if t.partition_id == partition.id]
        # Coverage belongs to collection, not to extraction. A partition that
        # was closed as PARTIAL because a docket limit stopped the download
        # stays PARTIAL no matter how many of the queued dockets parsed. Only a
        # partition collection never closed is sealed here from task outcomes.
        if partition.coverage_status != "IN_PROGRESS":
            continue
        if partition_tasks and all(t.status in {"COMPLETED", "FAILED", "CANCELLED"} for t in partition_tasks):
            partition.completed_at = datetime.now(UTC)
            partition.pagination_complete = False
            partition.coverage_status = (
                "COMPLETE" if all(t.status == "COMPLETED" for t in partition_tasks) else "PARTIAL"
            )
    job.completed_tasks = sum(t.status == "COMPLETED" for t in tasks)
    job.failed_tasks = sum(t.status == "FAILED" for t in tasks)
    pending = any(t.status in {"PENDING", "CLAIMED", "PROCESSING", "RETRY"} for t in tasks)
    if pending:
        job.status = "RUNNING" if job.started_at else "QUEUED"
        job.completed_at = None
        return
    job.completed_at = datetime.now(UTC)
    coverage_incomplete = any(
        partition.coverage_status not in {"COMPLETE", "ZERO_RESULTS"} for partition in partitions
    )
    if job.failed_tasks or coverage_incomplete:
        job.status = "PARTIAL" if job.completed_tasks else "FAILED"
    else:
        validation_states = [
            result.validation_status
            for result in session.scalars(select(ExtractionResult).where(ExtractionResult.job_id == job_id))
        ]
        job.status = "NEEDS_REVIEW" if "NEEDS_REVIEW" in validation_states else "COMPLETED"
