"""End-to-end demonstration: filing dates in, verified CSV/XLSX out.

Runs the whole client workflow against the live portal for a bounded sample and
fails loudly on any integrity breach:

    search filing dates -> discover dockets -> retrieve PDFs -> extract ->
    persist idempotently -> export CSV and XLSX

Usage:
    python scripts/run_demo.py --from 2026-11-04 --to 2026-11-05 \
        --county Adams --max-dockets 6

Every check below reads the database, not the collector's own counters.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from pa_docket.config import Settings
from pa_docket.db.models import (
    ChargeRow,
    CollectionPartition,
    ExtractionResult,
    SourceDocument,
)
from pa_docket.db.session import init_db, make_session_factory
from pa_docket.exports.csv_exporter import csv_bytes, iter_export_rows
from pa_docket.exports.xlsx_exporter import write_xlsx
from pa_docket.services.live import collect_live
from pa_docket.services.reporting import coverage_gaps, job_summary
from pa_docket.workers.main import run_worker

REQUESTED_FIELDS = (
    "docket_number",
    "defendant_name",
    "charge_description",
    "charge_statute",
    "offense_date",
    "arresting_officer",
    "defendant_race",
    "charge_disposition",
    "disposition_date",
    "case_status",
    "case_disposition",
)

EXPLICIT_STATUSES = {"PRESENT", "MISSING", "NOT_RECORDED", "UNVERIFIED"}


def _block(value: dict | None) -> dict:
    return value or {}


def _presence(value: dict | None) -> str:
    return str(_block(value).get("presence_status") or "ABSENT")


def check_records(session, job_id: str, failures: list[str]) -> dict:
    """Reconcile stored rows. Returns the measured field matrix."""
    duplicates = session.execute(
        select(ExtractionResult.job_id, ExtractionResult.docket_id, func.count())
        .where(ExtractionResult.job_id == job_id)
        .group_by(ExtractionResult.job_id, ExtractionResult.docket_id)
        .having(func.count() > 1)
    ).all()
    if duplicates:
        failures.append(f"duplicate extraction rows for {len(duplicates)} (job, docket) pairs")

    results = list(
        session.scalars(select(ExtractionResult).where(ExtractionResult.job_id == job_id)).all()
    )
    matrix: dict[str, dict[str, int]] = {field: {} for field in REQUESTED_FIELDS}

    for result in results:
        record = result.result_json or {}
        defendant = (record.get("defendants") or [{}])[0]
        metadata = record.get("metadata") or {}
        holders = {
            "docket_number": record.get("docket_number"),
            "defendant_name": defendant.get("name"),
            "defendant_race": defendant.get("race"),
            "case_status": record.get("case_status"),
            "case_disposition": metadata.get("case_disposition"),
        }
        for field, block in holders.items():
            status = _presence(block)
            if status not in EXPLICIT_STATUSES:
                failures.append(f"{result.id}: {field} has no explicit presence status ({status})")
            matrix[field][status] = matrix[field].get(status, 0) + 1

        charges = record.get("charges") or []
        if not charges:
            failures.append(f"{result.id}: docket {record.get('docket_number')} stored zero charges")
        for charge in charges:
            for field in (
                "description",
                "statute",
                "offense_date",
                "arresting_officer",
                "disposition",
                "disposition_date",
            ):
                status = _presence(charge.get(field))
                if status not in EXPLICIT_STATUSES:
                    failures.append(
                        f"{result.id}: charge {charge.get('charge_number', {}).get('raw_value')} "
                        f"{field} has no explicit presence status ({status})"
                    )
                key = f"charge_{field}" if field in {"description", "statute", "disposition"} else field
                if key in matrix:
                    matrix[key][status] = matrix[key].get(status, 0) + 1

        if not record.get("source_pdf_url"):
            failures.append(f"{result.id}: no source URL recorded")
        if not record.get("source_document_hash"):
            failures.append(f"{result.id}: no source document hash recorded")

    orphan_charges = session.scalar(
        select(func.count()).select_from(ChargeRow).where(ChargeRow.docket_id.is_(None))
    )
    if orphan_charges:
        failures.append(f"{orphan_charges} charge rows without a docket")

    job_dockets = {result.docket_id for result in results if result.docket_id is not None}
    missing_docs = [
        docket_id
        for docket_id in job_dockets
        if not session.scalar(
            select(func.count())
            .select_from(SourceDocument)
            .where(SourceDocument.docket_id == docket_id)
        )
    ]
    if missing_docs:
        failures.append(f"{len(missing_docs)} stored dockets have no source PDF")

    return matrix


def check_pdfs(session, storage_root: Path, job_id: str, failures: list[str]) -> int:
    """Verify the evidence pointer of every stored result still resolves on disk."""
    settings_cwd = Path.cwd()
    document_ids = session.execute(
        select(ExtractionResult.source_document_id).where(ExtractionResult.job_id == job_id)
    ).scalars()
    checked = 0
    for document_id in document_ids:
        if document_id is None:
            failures.append("extraction result without a source document pointer")
            continue
        document = session.get(SourceDocument, document_id)
        if document is None:
            failures.append(f"source document {document_id} not found")
            continue
        checked += 1
        stored = Path(document.storage_path)
        candidates = [stored] if stored.is_absolute() else [
            settings_cwd / stored,
            storage_root / stored,
            storage_root / "documents" / stored.name,
        ]
        if not any(candidate.exists() for candidate in candidates):
            failures.append(f"source PDF missing on disk: {stored}")
    return checked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="date_from", required=True, type=date.fromisoformat)
    parser.add_argument("--to", dest="date_to", required=True, type=date.fromisoformat)
    parser.add_argument("--county", action="append", required=True)
    parser.add_argument("--max-dockets", type=int, default=6)
    parser.add_argument("--output-dir", type=Path, default=Path("exports"))
    args = parser.parse_args()

    settings = Settings.from_env()
    engine, session_factory = make_session_factory(settings)
    init_db(engine)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    stats = asyncio.run(
        collect_live(
            session_factory,
            settings,
            date_from=args.date_from,
            date_to=args.date_to,
            counties=args.county,
            max_dockets=args.max_dockets,
        )
    )
    job_id = stats.job_id
    print("collection:", json.dumps(stats.as_dict(), indent=2))

    processed = run_worker(
        session_factory, settings, concurrency=settings.max_parse_workers
    )
    print(f"worker processed_tasks={processed}")

    failures: list[str] = []
    with session_factory() as session:
        summary = job_summary(session, job_id)
        matrix = check_records(session, job_id, failures)
        pdfs = check_pdfs(session, settings.storage_dir, job_id, failures)
        rows = list(iter_export_rows(session, job_id))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = args.output_dir / f"{job_id}.csv"
        xlsx_path = args.output_dir / f"{job_id}.xlsx"
        csv_path.write_bytes(csv_bytes(rows))
        xlsx_rows = write_xlsx(session, job_id, xlsx_path)

        partitions = list(
            session.scalars(
                select(CollectionPartition)
                .where(CollectionPartition.job_id == job_id)
                .order_by(CollectionPartition.filing_date)
            )
        )
        gaps = coverage_gaps(summary)

    if not rows:
        failures.append("export produced zero rows")
    if len(rows) != summary["charge_row_count"]:
        failures.append(
            f"export rows {len(rows)} != stored charge rows {summary['charge_row_count']}"
        )
    if xlsx_rows != len(rows):
        failures.append(f"xlsx rows {xlsx_rows} != csv rows {len(rows)}")

    print()
    print("partitions:")
    for partition in partitions:
        print(
            f"  {partition.filing_date} {partition.county:<12} "
            f"{partition.coverage_status:12} dockets={partition.result_count:<4} "
            f"pages={partition.pages_visited}"
        )
        for error in partition.errors:
            print(f"      ! {error[:150]}")

    print()
    print("requested fields (docket-level x1, charge-level xN):")
    for field in REQUESTED_FIELDS:
        counts = matrix[field]
        if counts:
            rendered = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            print(f"  {field:20} {rendered}")

    print()
    print(
        f"job={job_id} status={summary['status']} dockets={summary['docket_count']} "
        f"charges={summary['charge_row_count']} documents={pdfs} "
        f"csv_rows={len(rows)} xlsx_rows={xlsx_rows}"
    )
    if gaps:
        print("coverage gaps:")
        for gap in gaps:
            print(f"  - {gap}")

    if failures:
        print()
        print("FAILED CHECKS:")
        for failure in failures:
            print(f"  x {failure}")
        engine.dispose()
        return 1

    print()
    print(f"csv : {csv_path.resolve()}")
    print(f"xlsx: {xlsx_path.resolve()}")
    print("verdict: PASS - every stored charge traces to a docket and a source PDF")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
