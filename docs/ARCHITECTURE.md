# Architecture

The current runtime separates source, storage, extraction, validation, and export:

1. Fixture or local-PDF import creates a collection job and a task per document.
2. PDFs are magic-byte checked, SHA-256 hashed, and atomically placed in content-addressed storage.
3. Workers claim tasks transactionally. PostgreSQL claims use `FOR UPDATE SKIP LOCKED`; leases allow expired work to be reclaimed.
4. PyMuPDF extracts selectable text. Labeled fields retain page/line snippets as evidence references.
5. Extraction output and the docket, charge rows, document metadata, and task completion are written in one database transaction.
6. CSV uses the standard library; XLSX uses openpyxl write-only mode. Spreadsheet formula prefixes are escaped.

Worker processes share only PostgreSQL and the document volume. For production, use a shared durable volume or object-storage adapter before distributing workers across hosts; a local container filesystem is not sufficient for multi-host storage.

The current first migration creates the initial schema. Schema evolution should be added as numbered Alembic migrations. No Redis, Celery, or LLM service is used.

