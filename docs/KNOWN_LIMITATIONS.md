# Known limitations

These are measured properties of the source and the collector, not open work
items. Each states what the output does about it.

## Source coverage

- **Common Pleas truncation is unrecoverable on a Date Filed search.** The portal
  caps a result grid at ≈1046 rows and offers no CP subdivision for that search
  mode. A capped CP set is stored with `coverage_status=TRUNCATED` and an explicit
  error naming the cause. The job is marked `PARTIAL`. Narrow the filing-date
  window or county to obtain a complete CP set.
- **Magisterial District truncation is recoverable** by enumerating every
  `MDJS Court Office` in the county. Recovery is all-or-nothing per partition:
  if any office is still capped or fails, the partition stays `TRUNCATED` or
  `FAILED` and the offending office is named in the partition errors.
- **The grid ceiling is empirical.** It was observed at 1046 rows; the portal
  does not publish it. The collector trusts the truncation sentence, never a row
  count.
- **Filing-date windows are capped at 180 days** by the portal. Longer ranges are
  refused before any request is made.
- **Traffic, non-traffic, landlord/tenant and other non-criminal dockets are
  counted as skipped**, not collected. Only `-CR-` dockets are collected.

## Field availability

- **Race is only printed on MDJS 1200.** CPCMS 9082 has no race field. CP rows
  therefore carry `defendant_race=NOT_RECORDED` with section evidence. This is a
  document property, not a failed extraction, and it does not demote a CP record's
  validation status.
- **Fresh filings often have no disposition.** A docket filed on the collected
  date typically shows `Disposition:` blank and `Case Status: Active`. Those rows
  carry `charge_disposition=MISSING` and `disposition_date=MISSING` because the
  document states nothing. Re-collect the docket later to pick up a disposition.
- **Case status and case disposition are separate facts.** `case_status` is the
  report's `Case Status:` cell. The case-level outcome is
  `metadata["case_disposition"]`. A case can be `Active` with a charge-level
  `held` and no final case disposition.
- **An unrecognised disposition label is preserved raw** with
  `normalized_value=None`, `presence_status=UNVERIFIED`, and a validation error.
  It is never silently mapped to a nearby outcome.

## Extraction

- **OCR is not implemented.** Image-only PDFs raise `INVALID_PDF`/`MALFORMED_PDF`
  and the task fails with that classification instead of inventing fields.
- **Multi-defendant dockets are indexed per charge** by `defendant_index`; the
  export repeats the docket on one row per charge, not one row per defendant.

## Storage and operations

- **A document fetch that loses its proxy tunnel is retried four times on the
  same session.** The signed `dnh` token is bound to the issuing exit, so opening
  a new session cannot recover it. After the fourth attempt the single docket is
  failed by name, counted in `documents_failed`, and the rest of the partition
  continues. The job is `PARTIAL`; it is never reported complete.
- **An unexpected crash inside a partition seals it `FAILED`.** Coverage is never
  left `IN_PROGRESS` and a job is never left `RUNNING` by an exception.
- **Document deduplication is content-addressed by PDF bytes.** The portal
  re-signs a report on each retrieval, so a re-download can differ by a byte and
  is stored as a second `SourceDocument`. Export rows are still unique per
  `(job_id, docket_id)`.
- **No retention/deletion workflow.** Documents accumulate under
  `data/documents/<2-hex>/<sha>.pdf`.
- **The dashboard is an operator surface, not a review queue.** It shows stored
  dockets, coverage status and exports; it does not offer correction, cancellation
  or per-partition re-run.
- **Multi-host workers require shared document storage.** The local deployment
  uses one volume.
- **Throughput is bound by the 10 RPM limiter and portal latency**, not by CPU.
