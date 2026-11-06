"""Charge-level records: one row per charge across every stored docket.

The records page and the filtered exports read this one query so a filter shown
on screen and a filter applied to the downloaded file can never drift apart.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pa_docket.db.models import Docket, DocketRevision, ExtractionResult, SourceDocument
from pa_docket.exports.csv_exporter import COLUMNS, _safe_cell


@dataclass(frozen=True)
class RecordFilters:
    date_from: date | None = None
    date_to: date | None = None
    county: str | None = None
    charge: str | None = None
    disposition: str | None = None
    docket_number: str | None = None
    job_id: str | None = None


def _value(field: dict | None, *, raw: bool = False) -> str:
    if not field:
        return ""
    value = field.get("raw_value") if raw else field.get("normalized_value")
    return "" if value is None else str(value)


def _presence(field: dict | None) -> str:
    return str((field or {}).get("presence_status") or "ABSENT")


def _filing_date(record: dict, docket: Docket) -> date | None:
    if docket.filing_date:
        return docket.filing_date
    value = _value(record.get("filing_date"))
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _flatten(docket: Docket, record: dict, charge: dict, defendant: dict) -> dict:
    metadata = record.get("metadata") or {}
    disposition = charge.get("disposition")
    return {
        "docket_number": _value(record.get("docket_number")),
        "court_type": _value(record.get("court")),
        "county": _value(record.get("county")),
        "filing_date": _value(record.get("filing_date")),
        "defendant_name": _value(defendant.get("name")),
        "arresting_officer": _value(charge.get("arresting_officer")),
        "defendant_race": _value(defendant.get("race")),
        "race_presence": _presence(defendant.get("race")),
        "charge_number": _value(charge.get("charge_number")),
        "charge_description": _value(charge.get("description")),
        "charge_statute": _value(charge.get("statute")),
        "offense_date": _value(charge.get("offense_date")),
        "charge_disposition": _value(disposition, raw=True),
        "disposition_presence": _presence(disposition),
        "disposition_date": _value(charge.get("disposition_date")),
        "case_status": _value(record.get("case_status"), raw=True),
        "case_disposition": _value(metadata.get("case_disposition"), raw=True),
        "source_url": record.get("source_pdf_url") or "",
        "retrieved_at": record.get("retrieved_at") or "",
        "validation_status": record.get("validation_status") or docket.validation_status or "",
        "_docket_id": docket.id,
        "_filing_date": _filing_date(record, docket),
    }


def _matches(row: dict, filters: RecordFilters) -> bool:
    if filters.county and filters.county.casefold() not in row["county"].casefold():
        return False
    if filters.charge:
        haystack = f"{row['charge_description']} {row['charge_statute']}".casefold()
        if filters.charge.casefold() not in haystack:
            return False
    if filters.disposition:
        wanted = filters.disposition.casefold()
        recorded = row["charge_disposition"].casefold()
        if wanted in {"missing", "not recorded", "not_recorded"}:
            if row["disposition_presence"] == "PRESENT":
                return False
        elif wanted not in recorded:
            return False
    if filters.docket_number and filters.docket_number.casefold() not in row["docket_number"].casefold():
        return False
    filed = row["_filing_date"]
    if filters.date_from and (filed is None or filed < filters.date_from):
        return False
    if filters.date_to and (filed is None or filed > filters.date_to):
        return False
    return True


def iter_record_rows(session: Session, filters: RecordFilters) -> Iterator[dict]:
    statement = select(Docket).order_by(Docket.filing_date.desc(), Docket.docket_number)
    if filters.job_id:
        statement = statement.where(
            Docket.id.in_(
                select(ExtractionResult.docket_id).where(ExtractionResult.job_id == filters.job_id)
            )
        )
    if filters.date_from:
        statement = statement.where(Docket.filing_date >= filters.date_from)
    if filters.date_to:
        statement = statement.where(Docket.filing_date <= filters.date_to)
    if filters.county:
        statement = statement.where(Docket.county.ilike(f"%{filters.county}%"))

    latest_document = dict(
        session.execute(
            select(SourceDocument.docket_id, func.max(SourceDocument.id)).group_by(
                SourceDocument.docket_id
            )
        ).all()
    )
    for docket in session.scalars(statement.execution_options(yield_per=200)):
        record = docket.record_json or {}
        defendants = {int(d.get("index", 1)): d for d in record.get("defendants") or []}
        for charge in record.get("charges") or []:
            defendant = defendants.get(int(charge.get("defendant_index", 1)), {})
            row = _flatten(docket, record, charge, defendant)
            row["source_document_id"] = latest_document.get(docket.id)
            if _matches(row, filters):
                yield row


def record_rows(session: Session, filters: RecordFilters) -> list[dict]:
    return list(iter_record_rows(session, filters))


def export_values(row: dict) -> list[str]:
    """Project a record row onto the published export column order."""
    return [_safe_cell(row.get(column, "")) for column in COLUMNS]


def county_options(session: Session) -> list[str]:
    return [
        county
        for county in session.scalars(
            select(Docket.county).distinct().order_by(Docket.county)
        )
        if county
    ]


def docket_detail(session: Session, docket_number: str) -> dict | None:
    """One docket with its documents and every revision a refresh recorded."""
    normalized = " ".join(docket_number.upper().split())
    docket = session.scalars(
        select(Docket).where(Docket.docket_number == normalized)
    ).first()
    if docket is None:
        return None
    documents = list(
        session.scalars(
            select(SourceDocument)
            .where(SourceDocument.docket_id == docket.id)
            .order_by(SourceDocument.id.desc())
        )
    )
    revisions = list(
        session.scalars(
            select(DocketRevision)
            .where(DocketRevision.docket_id == docket.id)
            .order_by(DocketRevision.created_at.desc(), DocketRevision.id.desc())
        )
    )
    return {
        "docket_number": docket.docket_number,
        "court": docket.court,
        "county": docket.county,
        "filing_date": docket.filing_date.isoformat() if docket.filing_date else None,
        "validation_status": docket.validation_status,
        "updated_at": docket.updated_at.isoformat() if docket.updated_at else None,
        "record": docket.record_json or {},
        "documents": [
            {
                "id": document.id,
                "document_hash": document.document_hash,
                "byte_count": document.byte_count,
                "retrieved_at": document.retrieved_at.isoformat()
                if document.retrieved_at
                else None,
                "source_url": document.source_url,
            }
            for document in documents
        ],
        "revisions": [
            {
                "id": revision.id,
                "job_id": revision.job_id,
                "created_at": revision.created_at.isoformat() if revision.created_at else None,
                "changed_fields": list(revision.changed_fields or []),
                "previous": revision.previous_record_json,
                "current": revision.new_record_json,
            }
            for revision in revisions
        ],
    }


__all__ = [
    "COLUMNS",
    "RecordFilters",
    "county_options",
    "docket_detail",
    "export_values",
    "iter_record_rows",
    "record_rows",
]
