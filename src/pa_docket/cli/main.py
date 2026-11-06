from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

from pa_docket.config import Settings
from pa_docket.db.models import CollectionJob, ExportRecord
from pa_docket.db.session import init_db, make_session_factory
from pa_docket.exports.csv_exporter import COLUMNS, iter_export_rows
from pa_docket.exports.xlsx_exporter import write_xlsx
from pa_docket.services.imports import create_pdf_import_job
from pa_docket.services.jobs import create_fixture_job, list_jobs, resume_job
from pa_docket.sources.fixture.source import FixtureSource
from pa_docket.workers.main import run_worker


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Date must use YYYY-MM-DD") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pa-docket")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check database, storage, and live-collection gates")
    collect = sub.add_parser("collect", help="Create a fixture-backed collection job")
    collect.add_argument("--from", dest="date_from", type=_date)
    collect.add_argument("--to", dest="date_to", type=_date)
    collect.add_argument("--county")
    collect.add_argument("--docket")
    collect.add_argument("--enqueue-only", action="store_true")
    collect.add_argument("--workers", type=int)
    live = sub.add_parser("collect-live", help="Collect docket sheets from the UJS portal")
    live.add_argument("--from", dest="date_from", type=_date, required=True)
    live.add_argument("--to", dest="date_to", type=_date, required=True)
    live.add_argument("--county", action="append", default=[])
    live.add_argument("--limit", type=int)
    live.add_argument(
        "--resume-job",
        help="Continue an existing job; partitions already complete are skipped",
    )
    live.add_argument("--enqueue-only", action="store_true")
    live.add_argument("--workers", type=int)
    live.add_argument("--progress", action="store_true")
    dockets = sub.add_parser(
        "collect-dockets", help="Retrieve named criminal dockets by docket number"
    )
    dockets.add_argument("--docket", action="append", required=True)
    dockets.add_argument("--enqueue-only", action="store_true")
    dockets.add_argument("--workers", type=int)
    dockets.add_argument("--progress", action="store_true")
    refresh = sub.add_parser(
        "refresh", help="Re-retrieve stored dockets and record revisions"
    )
    refresh.add_argument("--job", help="Refresh every docket stored for this job")
    refresh.add_argument("--docket", action="append", default=[])
    refresh.add_argument("--limit", type=int)
    refresh.add_argument("--enqueue-only", action="store_true")
    refresh.add_argument("--workers", type=int)
    refresh.add_argument("--progress", action="store_true")
    imp = sub.add_parser("import-pdfs", help="Import locally supplied PDF docket sheets")
    imp.add_argument("path", type=Path)
    imp.add_argument("--enqueue-only", action="store_true")
    imp.add_argument("--workers", type=int)
    known = sub.add_parser("import-dockets", help="Import a CSV with docket_number,pdf_path columns")
    known.add_argument("path", type=Path)
    known.add_argument("--enqueue-only", action="store_true")
    known.add_argument("--workers", type=int)
    worker = sub.add_parser("worker", help="Process durable database queue tasks")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--watch", action="store_true")
    worker.add_argument("--concurrency", type=int)
    jobs = sub.add_parser("jobs", help="Inspect or resume jobs")
    jobs_sub = jobs.add_subparsers(dest="jobs_command", required=True)
    jobs_sub.add_parser("list")
    status = jobs_sub.add_parser("status")
    status.add_argument("job_id")
    resume = jobs_sub.add_parser("resume")
    resume.add_argument("job_id")
    serve = sub.add_parser("serve", help="Run the authenticated dashboard and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    export = sub.add_parser("export", help="Export a job to CSV or XLSX")
    export.add_argument("--job", required=True)
    export.add_argument("--format", choices=["csv", "xlsx"], required=True)
    export.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    engine, session_factory = make_session_factory(settings)
    init_db(engine)

    if args.command == "doctor":
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print(f"database: ok ({engine.dialect.name})")
        print(f"storage: ok ({settings.storage_dir.resolve()})")
        print(f"live_collection: {'enabled and approved' if settings.live_ready else 'disabled'}")
        findings_path = settings.storage_dir / "ujs_access_findings.json"
        if findings_path.exists():
            import json as _json

            findings = _json.loads(findings_path.read_text(encoding="utf-8"))
            required = ("selectors_verified", "search_flow_validated", "pdf_delivery_validated")
            state = ", ".join(f"{key}={findings.get(key)}" for key in required)
            print(f"live_evidence: {state}")
        else:
            print(f"live_evidence: missing ({findings_path})")
        print(f"network_mode: {settings.network_mode}")
        print(f"rate_limit_rpm: {settings.requests_per_minute}")
        return 0
    if args.command == "collect":
        if args.docket:
            cases = FixtureSource().search(docket_number=args.docket)
            params = {"source": "fixture", "docket_number": args.docket}
        elif args.date_from and args.date_to and args.date_from <= args.date_to:
            cases = FixtureSource().search(start=args.date_from, end=args.date_to, county=args.county)
            params = {
                "source": "fixture",
                "date_from": args.date_from.isoformat(),
                "date_to": args.date_to.isoformat(),
                "county": args.county,
            }
        else:
            print("Use --docket or a valid --from and --to range", file=sys.stderr)
            return 2
        job_id = create_fixture_job(session_factory, settings, cases, params)
        print(f"job_id={job_id} discovered={len(cases)} source=fixture")
        if not args.enqueue_only:
            run_worker(session_factory, settings, concurrency=args.workers or settings.max_parse_workers)
            _show_status(session_factory, job_id)
        return 0
    if args.command == "collect-live":
        import asyncio
        import json

        from pa_docket.services.live import collect_live

        if args.date_from > args.date_to:
            print("--from must not be after --to", file=sys.stderr)
            return 2
        try:
            stats = asyncio.run(
                collect_live(
                    session_factory,
                    settings,
                    date_from=args.date_from,
                    date_to=args.date_to,
                    counties=args.county,
                    max_dockets=args.limit,
                    resume_job_id=args.resume_job,
                    progress=args.progress,
                )
            )
        except Exception as exc:
            print(f"collection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(stats.as_dict(), indent=2))
        if not args.enqueue_only:
            run_worker(session_factory, settings, concurrency=args.workers or settings.max_parse_workers)
            _show_status(session_factory, stats.job_id)
        return 0 if not stats.failures else 1
    if args.command == "collect-dockets":
        import asyncio
        import json

        from pa_docket.services.live import collect_dockets

        try:
            stats = asyncio.run(
                collect_dockets(
                    session_factory,
                    settings,
                    args.docket,
                    progress=args.progress,
                )
            )
        except Exception as exc:
            print(f"docket retrieval failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(stats.as_dict(), indent=2))
        if not args.enqueue_only:
            run_worker(session_factory, settings, concurrency=args.workers or settings.max_parse_workers)
            _show_status(session_factory, stats.job_id)
        return 0 if not stats.failures else 1
    if args.command == "refresh":
        import asyncio
        import json

        from pa_docket.services.live import refresh_dockets

        try:
            stats = asyncio.run(
                refresh_dockets(
                    session_factory,
                    settings,
                    job_id=args.job,
                    docket_numbers=args.docket,
                    limit=args.limit,
                    progress=args.progress,
                )
            )
        except Exception as exc:
            print(f"refresh failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(stats.as_dict(), indent=2))
        if not args.enqueue_only:
            run_worker(session_factory, settings, concurrency=args.workers or settings.max_parse_workers)
            _show_status(session_factory, stats.job_id)
        return 0 if not stats.failures else 1
    if args.command in {"import-pdfs", "import-dockets"}:
        if args.command == "import-pdfs":
            paths = list(args.path.rglob("*.pdf")) if args.path.is_dir() else [args.path]
        else:
            with args.path.open(newline="", encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            if not rows or not {"docket_number", "pdf_path"} <= set(rows[0]):
                print("CSV must have docket_number,pdf_path columns", file=sys.stderr)
                return 2
            paths = [(row["docket_number"].strip(), Path(row["pdf_path"])) for row in rows]
        job_id = create_pdf_import_job(session_factory, settings, paths, label=str(args.path))
        print(f"job_id={job_id} imported={len(paths)}")
        if not args.enqueue_only:
            run_worker(session_factory, settings, concurrency=args.workers or settings.max_parse_workers)
            _show_status(session_factory, job_id)
        return 0
    if args.command == "worker":
        count = run_worker(
            session_factory,
            settings,
            concurrency=args.concurrency or settings.max_parse_workers,
            watch=args.watch,
            once=args.once,
        )
        print(f"processed_tasks={count}")
        return 0
    if args.command == "jobs":
        with session_factory() as session:
            if args.jobs_command == "list":
                for job in list_jobs(session):
                    print(f"{job.id}\t{job.status}\t{job.completed_tasks}/{job.total_tasks}")
                return 0
            if args.jobs_command == "status":
                return _show_status(session_factory, args.job_id)
        if args.jobs_command == "resume":
            try:
                count = resume_job(session_factory, args.job_id)
            except LookupError:
                print("job not found", file=sys.stderr)
                return 1
            print(f"job_id={args.job_id} reset_tasks={count}")
            return 0
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "pa_docket.api.app:app",
            host=args.host,
            port=args.port,
            log_level="info",
        )
        return 0
    if args.command == "export":
        output = args.output or Path(f"{args.job}.{args.format}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with session_factory() as session:
            job = session.get(CollectionJob, args.job)
            if not job:
                print("job not found", file=sys.stderr)
                return 1
            if args.format == "csv":
                count = 0
                with output.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream, lineterminator="\r\n")
                    writer.writerow(COLUMNS)
                    for row in iter_export_rows(session, args.job):
                        writer.writerow(row)
                        count += 1
            else:
                count = write_xlsx(session, args.job, output)
            session.add(ExportRecord(job_id=args.job, format=args.format, row_count=count))
            session.commit()
        print(f"output={output.resolve()} rows={count}")
        return 0
    return 2


def _show_status(session_factory, job_id: str) -> int:
    with session_factory() as session:
        job = session.get(CollectionJob, job_id)
        if not job:
            print("job not found", file=sys.stderr)
            return 1
        print(
            f"job_id={job.id} status={job.status} completed={job.completed_tasks}/{job.total_tasks} "
            f"failed={job.failed_tasks}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
