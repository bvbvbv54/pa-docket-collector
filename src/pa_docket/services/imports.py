from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from pa_docket.db.models import AuditEvent, CollectionJob, CollectionPartition, TaskQueue
from pa_docket.documents.storage import store_pdf


def create_pdf_import_job(
    session_factory,
    settings,
    files: Iterable[Path | tuple[str, Path]],
    *,
    label: str = "pdf-import",
) -> str:
    entries = []
    for item in files:
        expected, path = item if isinstance(item, tuple) else (None, item)
        path = Path(path)
        if path.suffix.lower() == ".pdf":
            entries.append((expected, path))
    entries = sorted(entries, key=lambda entry: str(entry[1]))[: settings.max_pdfs_per_job]
    with session_factory() as session, session.begin():
        job = CollectionJob(status="QUEUED", params={"source": "import", "label": label}, total_tasks=len(entries))
        session.add(job)
        session.flush()
        partition = CollectionPartition(
            job_id=job.id,
            params={"source": "import"},
            result_count=len(entries),
            pagination_complete=True,
            coverage_status="IN_PROGRESS",
        )
        session.add(partition)
        session.flush()
        for expected, path in entries:
            data = path.read_bytes()
            digest, stored = store_pdf(data, settings.storage_dir)
            session.add(
                TaskQueue(
                    job_id=job.id,
                    partition_id=partition.id,
                    task_type="EXTRACT_PDF",
                    payload={
                        "docket_number": None,
                        "expected_docket_number": expected,
                        "document_hash": digest,
                        "storage_path": str(stored),
                        "source_url": None,
                        "byte_count": len(data),
                    },
                )
            )
        session.add(AuditEvent(event_type="PDF_IMPORT_CREATED", job_id=job.id, details={"input_count": len(entries)}))
        if not entries:
            job.status = "COMPLETED"
            partition.coverage_status = "ZERO_RESULTS"
        return job.id
