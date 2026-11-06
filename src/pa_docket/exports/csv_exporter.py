from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator

from sqlalchemy import select

from pa_docket.db.models import ExtractionResult

COLUMNS = [
    "docket_number",
    "court_type",
    "county",
    "filing_date",
    "defendant_name",
    "arresting_officer",
    "defendant_race",
    "charge_number",
    "charge_description",
    "charge_statute",
    "offense_date",
    "charge_disposition",
    "disposition_date",
    "case_status",
    "case_disposition",
    "source_url",
    "retrieved_at",
    "validation_status",
]


def _value(field: dict | None, *, raw: bool = False) -> str:
    if not field:
        return ""
    value = field.get("raw_value") if raw else field.get("normalized_value")
    return "" if value is None else str(value)


def _safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def rows_for_record(record: dict) -> Iterator[list[str]]:
    defendants = {int(d.get("index", 1)): d for d in record.get("defendants", [])}
    metadata = record.get("metadata") or {}
    common = [
        _value(record.get("docket_number")),
        _value(record.get("court")),
        _value(record.get("county")),
        _value(record.get("filing_date")),
    ]
    for charge in record.get("charges", []):
        defendant = defendants.get(int(charge.get("defendant_index", 1)), {})
        row = common + [
            _value(defendant.get("name")),
            _value(charge.get("arresting_officer")),
            _value(defendant.get("race")),
            _value(charge.get("charge_number")),
            _value(charge.get("description")),
            _value(charge.get("statute")),
            _value(charge.get("offense_date")),
            _value(charge.get("disposition"), raw=True),
            _value(charge.get("disposition_date")),
            _value(record.get("case_status"), raw=True),
            _value(metadata.get("case_disposition"), raw=True),
            record.get("source_pdf_url") or "",
            record.get("retrieved_at") or "",
            record.get("validation_status") or "",
        ]
        yield [_safe_cell(value) for value in row]


def iter_export_rows(session, job_id: str) -> Iterator[list[str]]:
    results = session.scalars(
        select(ExtractionResult)
        .where(ExtractionResult.job_id == job_id)
        .order_by(ExtractionResult.id)
        .execution_options(yield_per=500)
    )
    for result in results:
        yield from rows_for_record(result.result_json)


def csv_bytes(rows: Iterable[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")
