from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


JsonType = JSON().with_variant(JSONB, "postgresql")


class CollectionJob(Base):
    __tablename__ = "collection_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED", index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, default=0)


class CollectionPartition(Base):
    __tablename__ = "collection_partitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("collection_jobs.id", ondelete="CASCADE"), index=True)
    filing_date: Mapped[date | None] = mapped_column(Date, index=True)
    county: Mapped[str | None] = mapped_column(String(100), index=True)
    court_type: Mapped[str | None] = mapped_column(String(200), index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    pages_visited: Mapped[int] = mapped_column(Integer, default=0)
    pagination_complete: Mapped[bool | None] = mapped_column(default=None)
    coverage_status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    errors: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)


class Docket(Base):
    __tablename__ = "dockets"
    __table_args__ = (UniqueConstraint("docket_number", "court", name="uq_docket_number_court"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    docket_number: Mapped[str | None] = mapped_column(String(120), index=True)
    court: Mapped[str | None] = mapped_column(String(200), index=True)
    county: Mapped[str | None] = mapped_column(String(100), index=True)
    filing_date: Mapped[date | None] = mapped_column(Date, index=True)
    case_status: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    record_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChargeRow(Base):
    __tablename__ = "charges"
    __table_args__ = (UniqueConstraint("docket_id", "charge_number", "defendant_index", name="uq_charge_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    docket_id: Mapped[int] = mapped_column(ForeignKey("dockets.id", ondelete="CASCADE"), index=True)
    charge_number: Mapped[str] = mapped_column(String(40), nullable=False)
    defendant_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    record_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (UniqueConstraint("document_hash", "docket_id", name="uq_document_hash_docket"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    docket_id: Mapped[int | None] = mapped_column(ForeignKey("dockets.id", ondelete="CASCADE"), index=True)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtractionResult(Base):
    __tablename__ = "extraction_results"
    __table_args__ = (
        # One extraction per docket per job: a restart re-downloads the sheet and
        # must replace the row, never append a second export line.
        UniqueConstraint("job_id", "docket_id", name="uq_extraction_job_docket"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    docket_id: Mapped[int | None] = mapped_column(ForeignKey("dockets.id", ondelete="CASCADE"), index=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"))
    job_id: Mapped[str] = mapped_column(ForeignKey("collection_jobs.id", ondelete="CASCADE"), index=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskQueue(Base):
    __tablename__ = "task_queue"
    __table_args__ = (Index("ix_task_claim", "status", "available_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("collection_jobs.id", ondelete="CASCADE"), index=True)
    partition_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_partitions.id", ondelete="CASCADE"), index=True
    )
    task_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    worker_id: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExportRecord(Base):
    __tablename__ = "export_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("collection_jobs.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
