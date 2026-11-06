"""Simulate an interrupted worker: leave tasks leased by a worker that died."""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from pa_docket.config import Settings
from pa_docket.db.models import TaskQueue
from pa_docket.db.session import make_session_factory


def main(job_id: str) -> int:
    settings = Settings.from_env()
    _engine, session_factory = make_session_factory(settings)
    stale = datetime.now(UTC) - timedelta(hours=2)
    with session_factory() as session, session.begin():
        tasks = list(
            session.scalars(
                select(TaskQueue)
                .where(TaskQueue.job_id == job_id, TaskQueue.status == "PENDING")
                .order_by(TaskQueue.id)
            )
        )
        for index, task in enumerate(tasks):
            task.status = "PROCESSING" if index == 0 else "CLAIMED"
            task.worker_id = f"dead-worker-{index}"
            task.lease_until = stale
            task.attempts += 1
        print(f"orphaned_tasks={len(tasks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
