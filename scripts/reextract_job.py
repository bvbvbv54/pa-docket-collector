"""Re-extract every stored docket PDF through the current parser.

Resets the job's EXTRACT_PDF tasks to PENDING and clears the charge rows those
dockets own, so the worker writes a fresh extraction rather than skipping a
docket it has already seen.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from pa_docket.config import Settings
from pa_docket.db.models import ChargeRow, Docket, TaskQueue
from pa_docket.db.session import make_session_factory

job_id = sys.argv[1] if len(sys.argv) > 1 else None
settings = Settings.from_env()
engine, session_factory = make_session_factory(settings)

with session_factory() as session, session.begin():
    query = select(TaskQueue).where(TaskQueue.task_type == "EXTRACT_PDF")
    if job_id:
        query = query.where(TaskQueue.job_id == job_id)
    tasks = list(session.scalars(query))
    dockets = {task.payload["docket_number"] for task in tasks}
    ids = list(
        session.scalars(select(Docket.id).where(Docket.docket_number.in_(dockets)))
    ) if dockets else []
    if ids:
        deleted = session.execute(
            ChargeRow.__table__.delete().where(ChargeRow.docket_id.in_(ids))
        ).rowcount
    else:
        deleted = 0
    for task in tasks:
        task.status = "PENDING"
        task.attempts = 0
        task.error = None
    print(f"tasks_reset={len(tasks)} dockets={len(dockets)} charge_rows_deleted={deleted}")
