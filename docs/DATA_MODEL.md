# Data model

The initial schema contains jobs, collection partitions, docket records, one-to-many charge rows, source document versions, extraction results, a durable task queue, audit events, and export records. Dockets have a unique `(docket_number, court)` constraint; source document versions are keyed by `(document_hash, docket_id)`. Unknown docket identifiers are stored as NULL in extraction output rather than inferred.

Each extracted field is represented as `{raw_value, normalized_value, presence_status, evidence_reference}`. Evidence references identify page and line text. Charge data is stored per charge, with a defendant index preserving the relationship. Related docket numbers are preserved as references and never merged.

The schema is an initial operational subset: separate defendant, related-docket, status-history, and field-issue tables are not yet normalized out of the JSON extraction snapshot. They should be introduced before broad production use if operators need independent correction/review workflows or advanced relational queries.

