from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import uuid4

from sqlalchemy import or_, select, update

from pa_docket.db.models import TaskQueue

LEASE_MINUTES = 30
_SQLITE_CLAIM_LOCKS: dict[int, Lock] = {}


def claim_lock(session):
    if session.bind.dialect.name != "sqlite":
        from contextlib import nullcontext

        return nullcontext()
    key = id(session.bind)
    return _SQLITE_CLAIM_LOCKS.setdefault(key, Lock())


def recover_expired(session) -> int:
    now = datetime.now(UTC)
    expired = list(
        session.scalars(
            select(TaskQueue).where(TaskQueue.status.in_(["CLAIMED", "PROCESSING"]), TaskQueue.lease_until < now)
        )
    )
    for task in expired:
        task.status = "RETRY" if task.attempts < task.max_attempts else "FAILED"
        task.available_at = now
        task.worker_id = None
        task.lease_until = None
        task.last_error = "Worker lease expired"
    return len(expired)


def claim_one(session, worker_id: str | None = None) -> TaskQueue | None:
    now = datetime.now(UTC)
    worker_id = worker_id or str(uuid4())
    eligible = and_eligible(now)
    query = select(TaskQueue).where(eligible).order_by(TaskQueue.created_at, TaskQueue.id).limit(1)
    if session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    task = session.scalars(query).first()
    if task is None:
        return None
    result = session.execute(
        update(TaskQueue)
        .where(TaskQueue.id == task.id, eligible)
        .values(
            status="CLAIMED",
            worker_id=worker_id,
            attempts=TaskQueue.attempts + 1,
            lease_until=now + timedelta(minutes=LEASE_MINUTES),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None
    session.refresh(task)
    return task


def and_eligible(now):
    return or_(
        (TaskQueue.status.in_(["PENDING", "RETRY"]) & (TaskQueue.available_at <= now)),
        (TaskQueue.status.in_(["CLAIMED", "PROCESSING"]) & (TaskQueue.lease_until < now)),
    )
