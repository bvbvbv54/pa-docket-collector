from __future__ import annotations

import csv
import hmac
import io
import os
import tempfile
from html import escape
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from pa_docket.config import Settings
from pa_docket.db.models import CollectionJob, ExportRecord
from pa_docket.db.session import init_db, make_session_factory
from pa_docket.exports.csv_exporter import COLUMNS, iter_export_rows
from pa_docket.exports.xlsx_exporter import write_xlsx
from pa_docket.services.jobs import create_fixture_job, list_jobs
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
      <p><a href='/collect'>Create fixture collection</a></p>
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
        f"<td>{escape(d['docket_number'] or '')}</td>"
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
