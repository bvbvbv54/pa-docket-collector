from __future__ import annotations

import csv
import hmac
import io
import os
import tempfile
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from pa_docket.config import Settings
from pa_docket.db.models import CollectionJob, ExportRecord, SourceDocument
from pa_docket.db.session import init_db, make_session_factory
from pa_docket.exports.csv_exporter import COLUMNS, iter_export_rows
from pa_docket.exports.xlsx_exporter import write_rows, write_xlsx
from pa_docket.services.jobs import create_fixture_job, list_jobs
from pa_docket.services.records import (
    RecordFilters,
    county_options,
    docket_detail,
    export_values,
    iter_record_rows,
)
from pa_docket.services.reporting import coverage_gaps, job_summary
from pa_docket.sources.fixture.source import FixtureSource

settings = Settings.from_env()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
engine, SessionFactory = make_session_factory(settings)
init_db(engine)

app = FastAPI(title="PA Docket Collector", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or "local-development-only-change-me",
    same_site="lax",
    https_only=os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
)


def require_auth(request: Request) -> str:
    if not settings.operator_username or not settings.operator_password or len(settings.session_secret or "") < 32:
        raise HTTPException(503, "Operator authentication is not configured")
    username = request.session.get("username")
    if not username:
        raise HTTPException(401, "Login required", headers={"WWW-Authenticate": "Session"})
    return username


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(select(func.count()).select_from(CollectionJob))
        return {"status": "ok", "live_collection": "enabled" if settings.live_ready else "disabled"}
    except Exception as exc:
        raise HTTPException(503, "Database unavailable") from exc


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse("""<!doctype html><title>PA Docket Collector Login</title>
      <form method="post"><label>Username <input name="username" required></label>
      <label>Password <input type="password" name="password" required></label>
      <button>Sign in</button></form>""")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if not settings.operator_username or not settings.operator_password or len(settings.session_secret or "") < 32:
        raise HTTPException(503, "Operator authentication is not configured")
    if not (
        hmac.compare_digest(username, settings.operator_username)
        and hmac.compare_digest(password, settings.operator_password)
    ):
        raise HTTPException(401, "Invalid username or password")
    request.session["username"] = username
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, _username: str = Depends(require_auth)):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(_username: str = Depends(require_auth)):
    with SessionFactory() as session:
        jobs = list_jobs(session, limit=20)
    rows = "".join(
        f"<tr><td><a href='/jobs/{job.id}'>{job.id}</a></td><td>{job.status}</td>"
        f"<td>{job.completed_tasks}/{job.total_tasks}</td></tr>"
        for job in jobs
    )
    return HTMLResponse(f"""<!doctype html><title>PA Docket Collector</title>
      <h1>PA Docket Collector</h1><p>Offline fixture and authorized PDF import mode.</p>
      <p><a href='/collect'>Create fixture collection</a> | <a href='/records'>Stored records</a></p>
      <table><thead><tr><th>Job</th><th>Status</th><th>Progress</th></tr></thead>
      <tbody>{rows}</tbody></table><form method='post' action='/logout'><button>Log out</button></form>""")


@app.get("/collect", response_class=HTMLResponse)
def collect_page(_username: str = Depends(require_auth)):
    return HTMLResponse("""<!doctype html><title>New fixture job</title><h1>New fixture job</h1>
      <form method='post' action='/collect'><label>From <input name='date_from' type='date' required></label>
      <label>To <input name='date_to' type='date' required></label>
      <label>County <input name='county'></label><button>Create</button></form>""")


def _create_range_job(date_from: str, date_to: str, county: str | None) -> str:
    from datetime import date

    start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if end < start:
        raise HTTPException(422, "End date must not precede start date")
    cases = FixtureSource().search(start=start, end=end, county=county)
    return create_fixture_job(
        SessionFactory,
        settings,
        cases,
        {"source": "fixture", "date_from": date_from, "date_to": date_to, "county": county},
    )


@app.post("/collect")
def collect_form(
    request: Request,
    date_from: str = Form(...),
    date_to: str = Form(...),
    county: str | None = Form(None),
    _username: str = Depends(require_auth),
):
    job_id = _create_range_job(date_from, date_to, county)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


class CollectRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    county: str | None = None
    docket_number: str | None = None


@app.post("/api/collect")
def api_collect(body: CollectRequest, _username: str = Depends(require_auth)):
    if body.docket_number:
        cases = FixtureSource().search(docket_number=body.docket_number)
        params = {"source": "fixture", "docket_number": body.docket_number}
        job_id = create_fixture_job(SessionFactory, settings, cases, params)
    elif body.date_from and body.date_to:
        job_id = _create_range_job(body.date_from, body.date_to, body.county)
    else:
        raise HTTPException(422, "Provide docket_number or date_from and date_to")
    return {"job_id": job_id}


@app.get("/api/jobs")
def api_jobs(_username: str = Depends(require_auth)):
    with SessionFactory() as session:
        return [
            {
                "id": j.id,
                "status": j.status,
                "total_tasks": j.total_tasks,
                "completed_tasks": j.completed_tasks,
                "failed_tasks": j.failed_tasks,
            }
            for j in list_jobs(session)
        ]


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, _username: str = Depends(require_auth)):
    with SessionFactory() as session:
        summary = job_summary(session, job_id)
    if summary is None:
        raise HTTPException(404, "Job not found")
    return summary


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(job_id: str, _username: str = Depends(require_auth)):
    with SessionFactory() as session:
        summary = job_summary(session, job_id)
    if summary is None:
        raise HTTPException(404, "Job not found")
    gaps = coverage_gaps(summary)
    rows = "".join(
        "<tr>"
        f"<td><a href='/dockets/{escape(d['docket_number'] or '')}'>{escape(d['docket_number'] or '')}</a></td>"
        f"<td>{escape(d['county'] or '')}</td>"
        f"<td>{escape(d['filing_date'] or '')}</td>"
        f"<td>{escape(d['defendant_name'] or '')}</td>"
        f"<td>{escape(d['defendant_race'] or d['race_presence'])}</td>"
        f"<td>{escape(d['case_status'] or '')}</td>"
        f"<td>{escape(d['case_disposition'] or '')}</td>"
        f"<td>{d['charge_count']}</td>"
        f"<td>{escape(d['validation_status'] or '')}</td>"
        "</tr>"
        for d in summary["dockets"]
    )
    coverage = "".join(
        "<li>"
        f"{escape(c['filing_date'] or '')} {escape(c['county'] or '')} "
        f"{escape(c['court_type'] or '')}: {escape(c['coverage_status'])} "
        f"({c['result_count']} rows, {c['pages_visited']} page(s))"
        "</li>"
        for c in summary["coverage"]
    )
    gap_block = (
        "<h2>Coverage gaps</h2><ul>"
        + "".join(f"<li>{escape(gap)}</li>" for gap in gaps)
        + "</ul>"
        if gaps
        else "<p>Coverage: every partition reported COMPLETE.</p>"
    )
    return HTMLResponse(
        f"""<!doctype html><title>Job {escape(summary['id'])}</title>
      <h1>Job {escape(summary['id'])}</h1>
      <p>Status: {escape(summary['status'] or '')}</p>
      <p>Progress: {summary['completed_tasks']}/{summary['total_tasks']} tasks,
         {summary['failed_tasks']} failed</p>
      <p>Records: {summary['docket_count']} dockets,
         {summary['charge_row_count']} charge rows,
         {summary['document_count']} stored PDFs</p>
      <p><a href='/api/exports/{escape(summary['id'])}/csv'>CSV</a>
         <a href='/api/exports/{escape(summary['id'])}/xlsx'>XLSX</a>
         <a href='/api/jobs/{escape(summary['id'])}'>JSON</a></p>
      {gap_block}
      <h2>Partitions</h2><ul>{coverage}</ul>
      <h2>Dockets</h2>
      <table><thead><tr><th>Docket</th><th>County</th><th>Filed</th><th>Defendant</th>
      <th>Race</th><th>Case status</th><th>Case disposition</th><th>Charges</th>
      <th>Validation</th></tr></thead><tbody>{rows}</tbody></table>
      <form method='post' action='/logout'><button>Log out</button></form>"""
    )


@app.get("/api/exports/{job_id}/csv")
def export_csv(job_id: str, _username: str = Depends(require_auth)):
    with SessionFactory() as session:
        if not session.get(CollectionJob, job_id):
            raise HTTPException(404, "Job not found")

    def stream():
        count = 0
        with SessionFactory() as session:
            buf = io.StringIO(newline="")
            writer = csv.writer(buf, lineterminator="\r\n")
            writer.writerow(COLUMNS)
            yield buf.getvalue()
            for row in iter_export_rows(session, job_id):
                buf.seek(0)
                buf.truncate(0)
                writer.writerow(row)
                yield buf.getvalue()
                count += 1
            session.add(ExportRecord(job_id=job_id, format="csv", row_count=count))
            session.commit()

    return StreamingResponse(
        stream(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'},
    )


@app.get("/api/exports/{job_id}/xlsx")
def export_xlsx(job_id: str, _username: str = Depends(require_auth)):
    with SessionFactory() as session:
        if not session.get(CollectionJob, job_id):
            raise HTTPException(404, "Job not found")
    export_dir = settings.storage_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f"{job_id}-", suffix=".xlsx", dir=export_dir, delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        with SessionFactory() as session:
            count = write_xlsx(session, job_id, path)
            session.add(ExportRecord(job_id=job_id, format="xlsx", row_count=count))
            session.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path,
        filename=f"{job_id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


# ----------------------------------------------------------------- records


def _parse_filter_date(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, f"{label} must use YYYY-MM-DD") from exc


def _filters_from_query(
    date_from: str | None,
    date_to: str | None,
    county: str | None,
    charge: str | None,
    disposition: str | None,
    docket: str | None,
    job: str | None,
) -> RecordFilters:
    start = _parse_filter_date(date_from, "date_from")
    end = _parse_filter_date(date_to, "date_to")
    if start and end and end < start:
        raise HTTPException(422, "date_to must not precede date_from")
    return RecordFilters(
        date_from=start,
        date_to=end,
        county=county or None,
        charge=charge or None,
        disposition=disposition or None,
        docket_number=docket or None,
        job_id=job or None,
    )


def _records_query_string(filters: RecordFilters) -> str:
    pairs = {
        "date_from": filters.date_from.isoformat() if filters.date_from else "",
        "date_to": filters.date_to.isoformat() if filters.date_to else "",
        "county": filters.county or "",
        "charge": filters.charge or "",
        "disposition": filters.disposition or "",
        "docket": filters.docket_number or "",
        "job": filters.job_id or "",
    }
    return urlencode({key: value for key, value in pairs.items() if value})


def _filter_form(filters: RecordFilters, counties: list[str]) -> str:
    county_options_html = "".join(
        f"<option value='{escape(name)}'"
        f"{' selected' if filters.county and filters.county.casefold() == name.casefold() else ''}>"
        f"{escape(name)}</option>"
        for name in counties
    )
    date_from = escape(filters.date_from.isoformat() if filters.date_from else "")
    date_to = escape(filters.date_to.isoformat() if filters.date_to else "")
    charge = escape(filters.charge or "")
    disposition = escape(filters.disposition or "")
    docket = escape(filters.docket_number or "")
    job = escape(filters.job_id or "")
    return f"""<form method='get' action='/records' class='filters'>
      <label>Filed from <input type='date' name='date_from' value='{date_from}'></label>
      <label>to <input type='date' name='date_to' value='{date_to}'></label>
      <label>County <select name='county'><option value=''>All counties</option>{county_options_html}</select></label>
      <label>Charge <input name='charge' value='{charge}' placeholder='description or statute'></label>
      <label>Disposition <input name='disposition' value='{disposition}' placeholder='guilty, missing...'></label>
      <label>Docket <input name='docket' value='{docket}'></label>
      <input type='hidden' name='job' value='{job}'>
      <button>Filter</button>
      <a href='/records'>Reset</a>
    </form>"""


def _records_table(rows: list[dict], limit: int = 500) -> str:
    body_parts: list[str] = []
    for row in rows[:limit]:
        disposition = (
            escape(row["charge_disposition"])
            if row["disposition_presence"] == "PRESENT"
            else f"<span class='absent'>{escape(row['disposition_presence'])}</span>"
        )
        race = (
            escape(row["defendant_race"])
            if row["race_presence"] == "PRESENT"
            else f"<span class='absent'>{escape(row['race_presence'])}</span>"
        )
        body_parts.append(
            "<tr>"
            f"<td><a href='/dockets/{escape(row['docket_number'])}'>{escape(row['docket_number'])}</a></td>"
            f"<td>{escape(row['county'])}</td>"
            f"<td>{escape(row['filing_date'])}</td>"
            f"<td>{escape(row['defendant_name'])}</td>"
            f"<td>{escape(row['charge_description'])}</td>"
            f"<td>{escape(row['offense_date'])}</td>"
            f"<td>{disposition}</td>"
            f"<td>{escape(row['arresting_officer'])}</td>"
            f"<td>{race}</td>"
            "</tr>"
        )
    more = (
        f"<p class='note'>Showing the first {limit} of {len(rows)} matching charge rows. "
        "Download the export for every row.</p>"
        if len(rows) > limit
        else ""
    )
    return (
        "<table><thead><tr><th>Docket</th><th>County</th><th>Filed</th><th>Defendant</th>"
        "<th>Charge</th><th>Offense date</th><th>Disposition</th><th>Officer</th>"
        f"<th>Race</th></tr></thead><tbody>{''.join(body_parts)}</tbody></table>{more}"
    )


@app.get("/records", response_class=HTMLResponse)
def records_page(
    date_from: str | None = None,
    date_to: str | None = None,
    county: str | None = None,
    charge: str | None = None,
    disposition: str | None = None,
    docket: str | None = None,
    job: str | None = None,
    _username: str = Depends(require_auth),
):
    filters = _filters_from_query(
        date_from, date_to, county, charge, disposition, docket, job
    )
    with SessionFactory() as session:
        rows = list(iter_record_rows(session, filters))
        counties = county_options(session)
    query = _records_query_string(filters)
    dockets = {row["docket_number"] for row in rows}
    summary = (
        f"{len(rows)} charge rows across {len(dockets)} dockets"
        if rows
        else "No charge rows match these filters"
    )
    return HTMLResponse(
        f"""<!doctype html><title>Records</title>
      <style>
        body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
        table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
        th, td {{ border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }}
        th {{ background: #f4f4f4; position: sticky; top: 0; }}
        .filters label {{ display: inline-block; margin: 0 12px 10px 0; font-size: 13px; }}
        .filters input, .filters select {{ padding: 4px; }}
        .absent {{ color: #8a6d3b; font-size: 12px; }}
        .note {{ color: #555; font-size: 13px; }}
      </style>
      <h1>Stored criminal records</h1>
      <p>{escape(summary)}. One row per charge; every row links to its docket and its source PDF.</p>
      {_filter_form(filters, counties)}
      <p>
        <a href='/api/exports/records.csv?{escape(query)}'>Download CSV</a>
        &nbsp;|&nbsp;
        <a href='/api/exports/records.xlsx?{escape(query)}'>Download Excel</a>
        &nbsp;|&nbsp;
        <a href='/'>Jobs</a>
      </p>
      {_records_table(rows)}
      <form method='post' action='/logout'><button>Log out</button></form>"""
    )


@app.get("/api/records")
def api_records(
    date_from: str | None = None,
    date_to: str | None = None,
    county: str | None = None,
    charge: str | None = None,
    disposition: str | None = None,
    docket: str | None = None,
    job: str | None = None,
    limit: int = 1000,
    _username: str = Depends(require_auth),
):
    filters = _filters_from_query(
        date_from, date_to, county, charge, disposition, docket, job
    )
    with SessionFactory() as session:
        rows = []
        for row in iter_record_rows(session, filters):
            rows.append({key: value for key, value in row.items() if not key.startswith("_")})
            if len(rows) >= max(1, min(limit, 10000)):
                break
    return {
        "count": len(rows),
        "filters": {
            "date_from": filters.date_from.isoformat() if filters.date_from else None,
            "date_to": filters.date_to.isoformat() if filters.date_to else None,
            "county": filters.county,
            "charge": filters.charge,
            "disposition": filters.disposition,
            "docket": filters.docket_number,
            "job": filters.job_id,
        },
        "rows": rows,
    }


@app.get("/api/exports/records.csv")
def export_records_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    county: str | None = None,
    charge: str | None = None,
    disposition: str | None = None,
    docket: str | None = None,
    job: str | None = None,
    _username: str = Depends(require_auth),
):
    filters = _filters_from_query(
        date_from, date_to, county, charge, disposition, docket, job
    )

    def stream():
        with SessionFactory() as session:
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer, lineterminator="\r\n")
            writer.writerow(COLUMNS)
            yield buffer.getvalue()
            for row in iter_record_rows(session, filters):
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow(export_values(row))
                yield buffer.getvalue()

    return StreamingResponse(
        stream(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="pa-docket-records.csv"'},
    )


@app.get("/api/exports/records.xlsx")
def export_records_xlsx(
    date_from: str | None = None,
    date_to: str | None = None,
    county: str | None = None,
    charge: str | None = None,
    disposition: str | None = None,
    docket: str | None = None,
    job: str | None = None,
    _username: str = Depends(require_auth),
):
    filters = _filters_from_query(
        date_from, date_to, county, charge, disposition, docket, job
    )
    export_dir = settings.storage_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix="records-", suffix=".xlsx", dir=export_dir, delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        with SessionFactory() as session:
            write_rows(
                (export_values(row) for row in iter_record_rows(session, filters)),
                COLUMNS,
                path,
                sheet_name="Charges",
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path,
        filename="pa-docket-records.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@app.get("/api/documents/{document_id}")
def document_download(document_id: int, _username: str = Depends(require_auth)):
    with SessionFactory() as session:
        document = session.get(SourceDocument, document_id)
        if document is None:
            raise HTTPException(404, "Document not found")
        path = Path(document.storage_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        raise HTTPException(404, "Stored document is missing on disk")
    return FileResponse(path, filename=f"{document_id}.pdf", media_type="application/pdf")


@app.get("/dockets/{docket_number}", response_class=HTMLResponse)
def docket_page(docket_number: str, _username: str = Depends(require_auth)):
    with SessionFactory() as session:
        detail = docket_detail(session, docket_number)
    if detail is None:
        raise HTTPException(404, "Docket not found in the local store")
    record = detail["record"]
    defendants = record.get("defendants") or [{}]
    defendant = defendants[0]
    charges = record.get("charges") or []

    def field(item: dict | None):
        item = item or {}
        return item.get("normalized_value") or item.get("raw_value") or ""

    def presence(item: dict | None) -> str:
        return str((item or {}).get("presence_status") or "ABSENT")

    race = field(defendant.get("race"))
    case_disposition = field((record.get("metadata") or {}).get("case_disposition"))
    case_disposition_cell = (
        escape(str(case_disposition))
        if case_disposition
        else "<span class='absent'>NOT_RECORDED</span>"
    )
    race_cell = (
        escape(str(race))
        if race
        else f"<span class='absent'>{escape(presence(defendant.get('race')))}</span>"
    )
    document = detail["documents"][0] if detail["documents"] else None
    pdf_link = (
        f"<a href='/api/documents/{document['id']}'>stored PDF ({document['byte_count']} bytes, "
        f"hash {escape(document['document_hash'][:12])})</a>"
        if document
        else "<span class='absent'>no stored PDF</span>"
    )
    charge_parts: list[str] = []
    for charge in charges:
        disposition_cell = (
            escape(str(field(charge.get("disposition"))))
            if presence(charge.get("disposition")) == "PRESENT"
            else f"<span class='absent'>{escape(presence(charge.get('disposition')))}</span>"
        )
        charge_parts.append(
            "<tr>"
            f"<td>{escape(str(field(charge.get('charge_number'))))}</td>"
            f"<td>{escape(str(field(charge.get('description'))))}</td>"
            f"<td>{escape(str(field(charge.get('statute'))))}</td>"
            f"<td>{escape(str(field(charge.get('offense_date'))))}</td>"
            f"<td>{escape(str(field(charge.get('arresting_officer'))))}</td>"
            f"<td>{disposition_cell}</td>"
            f"<td>{escape(str(field(charge.get('disposition_date'))))}</td>"
            "</tr>"
        )
    charge_rows = "".join(charge_parts)
    revisions = "".join(
        "<tr>"
        f"<td>{escape(revision['created_at'] or '')}</td>"
        f"<td>{escape(', '.join(revision['changed_fields']))}</td>"
        f"<td>{escape(str((revision['job_id'] or '')[:8]))}</td>"
        "</tr>"
        for revision in detail["revisions"]
    )
    revision_block = (
        "<h2>Revision history</h2><table><thead><tr><th>Observed</th>"
        "<th>Changed fields</th><th>Job</th></tr></thead>"
        f"<tbody>{revisions}</tbody></table>"
        if revisions
        else "<p>No revisions recorded. The stored record matches the first retrieval.</p>"
    )
    return HTMLResponse(
        f"""<!doctype html><title>{escape(detail['docket_number'])}</title>
      <style>
        body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
        table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
        th, td {{ border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
        th {{ background: #f4f4f4; }}
        .absent {{ color: #8a6d3b; }}
        dt {{ font-weight: 600; }}
      </style>
      <h1>{escape(detail['docket_number'])}</h1>
      <dl>
        <dt>Defendant</dt><dd>{escape(str(field(defendant.get('name'))))}</dd>
        <dt>County / court</dt><dd>{escape(str(detail['county'] or ''))} / {escape(str(detail['court'] or ''))}</dd>
        <dt>Filing date</dt><dd>{escape(str(detail['filing_date'] or field(record.get('filing_date'))))}</dd>
        <dt>Race</dt><dd>{race_cell}</dd>
        <dt>Case status</dt><dd>{escape(str(field(record.get('case_status'))))}</dd>
        <dt>Case disposition</dt><dd>{case_disposition_cell}</dd>
        <dt>Validation</dt><dd>{escape(str(detail['validation_status']))}</dd>
        <dt>Source document</dt><dd>{pdf_link}</dd>
      </dl>
      <h2>Charges</h2>
      <table><thead><tr><th>#</th><th>Charge</th><th>Statute</th><th>Offense date</th>
      <th>Arresting officer</th><th>Disposition</th><th>Disposition date</th></tr></thead>
      <tbody>{charge_rows}</tbody></table>
      {revision_block}
      <p><a href='/records'>All records</a> | <a href='/'>Jobs</a></p>
      <form method='post' action='/logout'><button>Log out</button></form>"""
    )


@app.get("/api/dockets/{docket_number}")
def api_docket(docket_number: str, _username: str = Depends(require_auth)):
    with SessionFactory() as session:
        detail = docket_detail(session, docket_number)
    if detail is None:
        raise HTTPException(404, "Docket not found in the local store")
    return detail
