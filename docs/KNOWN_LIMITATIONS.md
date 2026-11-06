# Known limitations

- UJS browser automation is not implemented or tested. Live search, pagination, docket-sheet retrieval, and portal rate handling remain acceptance work.
- Generic extraction recognizes explicit labeled text fields and synthetic layout. It is not verified against authorized real docket PDFs or court-specific layouts.
- OCR is not implemented. Scanned image-only PDFs are marked `OCR_REQUIRED`.
- Import and fixture processing are implemented; date discovery is fixture-only. The current fixture corpus is intentionally small.
- There is no human review/correction queue, retention/deletion workflow, or field-level issue table yet.
- The dashboard is a minimal authenticated operator surface. It does not provide full record browsing, cancellation, or partition pagination controls.
- Local multi-process workers are safe on PostgreSQL, but workers on different hosts require shared document storage; current deployment uses one named Docker volume.
- Browser context pooling and browser-based PDF retrieval are not implemented because live access validation is outstanding.
- Performance acceptance at 100+ documents depends on the actual machine and database; see the executed test results in the delivery report.

