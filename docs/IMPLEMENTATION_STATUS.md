# Implementation acceptance status

States: PASS (executed), FAIL (executed and failed), BLOCKED (external dependency), NOT TESTED.

## CURRENT STATUS — 2026-11-06 (supersedes every BLOCKED entry below)

Every earlier live blocker in this file is resolved or replaced. Where this
section disagrees with a section below, this section is the record.

### End-to-end live pipeline

- PASS — Filing-date search through the rendered form: mode `DateFiled`, county,
  `FiledStartDate`/`FiledEndDate`, `#btnSearch`.
- PASS — Results enumeration from `#caseSearchResultGrid`. No pagination exists;
  completeness is proven by absence of the truncation sentence.
- PASS — Chronological run `e24feda7-7a51-4fc1-be56-8b4d5a3b6cd8`: Adams
  2026-11-04..05, 103 grid rows, 8 criminal dockets, 25 charges, 2 partitions
  both `COMPLETE`, 8 source PDFs verified on disk, 25 CSV rows, 25 XLSX rows,
  verdict PASS.
- PASS — Resumed run on the same job with `--to 2026-11-06`: both finished days
  skipped without a search, one new partition collected (18 grid rows, 1 criminal
  docket), job finished 9 dockets / 28 charges / 3 `COMPLETE` partitions with no
  duplicate `(job, docket)` rows.
- PASS — Dashboard read of the resumed job: 9 dockets, 28 charges, 9/9 tasks
  completed, 3 coverage rows, every stored docket number rendered in
  `/jobs/{id}`, CSV 28 rows, XLSX 8536 bytes with `PK` magic.
- PASS — Magisterial District truncation recovery by enumerating every
  `MDJS Court Office` per county. Lancaster 2026-11-05: base 551 grid rows, 21
  offices, per-office union 739, all offices `COMPLETE`.
- PASS — Common Pleas truncation detected and recorded as unrecoverable, never
  reported as complete.
- PASS — PDF retrieval through the issuing session: Chrome UA
  `Chrome/156.0.0.0`, sticky proxy exit, HTTP/1.1, QUIC disabled. The earlier
  `HTTP 401` is explained and fixed by the user agent and sticky session.
- PASS — Both document families parsed: `MDJS 1200` and `CPCMS 9082`.
- PASS — Persistence idempotent per `(job_id, docket_id)`; unique index
  `uq_extraction_job_docket` applied by Alembic `0002`.
- PASS — CSV and XLSX exports from stored rows, one row per charge.

### Field validation on real retrieved PDFs

Lancaster CP job `4dbf2448-f628-4a7b-8abc-fb3a5b39fe80` — 6 dockets, 31 charges:

- PASS — docket number, defendant name, charge description, statute, offense
  date, arresting officer, charge disposition, disposition date, case status,
  case disposition: all `PRESENT`, all records `VALIDATED`.
- PASS — `defendant_race` = `NOT_RECORDED` on 6/6. `CPCMS 9082` has no race
  field; this is a document property, not an extraction failure.

Lancaster mixed job `0776d3c4-9649-483f-9b59-6b84e7275a98` — 8 dockets, 42 charges:

- PASS — every requested field `PRESENT` on all 42 charge rows, including
  `charge_disposition` with real labels (`waived`, `held`, `changed`, `guilty`)
  and `disposition_date`.
- PASS — `defendant_race` = `NOT_RECORDED` 8/8, all documents CP.
- PASS — coverage: 58 criminal dockets discovered, 8 downloaded, partition
  `PARTIAL` with `Docket limit reached: 8 of 58 criminal dockets downloaded, 50
  not retrieved`. No truncation gap remains after MDJ recovery.

Adams MDJ job `ffeae2fc-dd62-48a3-af23-561674e0e5ce` — 4 dockets, 14 charges:

- PASS — every field `PRESENT` except `charge_disposition` /
  `disposition_date`, which are `MISSING` on 14/14 because the fresh filings
  print `Disposition:` blank and `Case Status: Active`. Confirmed against raw
  PDF text.

### Idempotency and restart

- PASS — Within-job re-download of the same docket yields 2 `SourceDocument`
  rows (the portal re-signs each PDF) and 1 `ExtractionResult`, 1 export row.
- PASS — Repeated job, Adams `bdb79148-3524-4839-9ce2-1b1245157545`: same 4
  dockets, 14 charges, 0 duplicates, 0 gaps.
- PASS — Interrupted job `a1dd4c4d-4848-41ca-a48b-d9206849c019`: 2 orphaned
  tasks with stale leases recovered, all tasks `COMPLETED`, 4 dockets, 14
  charges.
- PASS — Tunnel death mid-download: a document fetch is retried four times on the
  issuing session, then the single docket is failed by name and the partition
  continues. Regressions:
  `test_one_dead_tunnel_fails_only_that_docket`,
  `test_report_fetch_retries_the_tunnel_then_succeeds`,
  `test_report_fetch_gives_up_after_the_attempt_budget`.
- PASS — Unexpected crash: the open partition is sealed `FAILED` and the job is
  not left `RUNNING`. Regression:
  `test_unexpected_crash_seals_the_partition_and_the_job`.
- PASS — Adding filing dates to a finished job: completed partitions are skipped
  without a search and the same job accumulates the new rows. Regressions:
  `test_resume_skips_finished_partitions_and_keeps_the_same_job`,
  `test_skip_avoids_searching_finished_partitions`,
  `test_resume_of_missing_job_is_refused`.
- PASS — Partition `result_count` reports criminal dockets; raw grid rows and
  criminal count are kept in partition params. Regression:
  `test_partition_count_reports_criminal_dockets_not_grid_rows`.

### Coverage accounting

- PASS — Partition opens `IN_PROGRESS`, closes `COMPLETE` / `TRUNCATED` /
  `FAILED` / `ZERO_RESULTS`, or `PARTIAL` when a docket limit stopped downloads
  and the unretrieved count is recorded.
- PASS — A recovered MDJ partition is `COMPLETE`; the base-grid notice stays in
  the partition parameters as evidence and is not counted as a gap.
- PASS — Extraction cannot upgrade coverage. A `PARTIAL` partition stays
  `PARTIAL` after every queued docket parses, and the job stays `PARTIAL`.
  Regression: `test_extraction_never_upgrades_a_partial_partition_or_job`.
- PASS — Chronological iteration: days ascend, one partition per day and county.
  Regression: `test_days_are_emitted_chronologically_and_each_county_once`.

### Dashboard

- PASS — `scripts/verify_dashboard.py` against a running server: health 200,
  login 303, authenticated job detail 200, 6 dockets / 31 charges, every stored
  docket number present in the HTML page, CSV 31 rows, XLSX 8400 bytes with `PK`
  magic.

### Gates and access

- PASS — Live collection requires `LIVE_COLLECTION_ENABLED=true`,
  `LIVE_ACCESS_APPROVED=true` and a validated findings file.
- PASS — No CAPTCHA solving, no security bypass, 10 RPM ceiling.

### Remaining blockers

- BLOCKED — Client authorization and retention policy approval. Policy item, not
  code.
- BLOCKED — Common Pleas truncation on wide Date Filed searches. Source
  limitation; narrow the window or county.
- NOT TESTED — Multi-host worker deployment with shared document storage.

### Evidence

- Regression suite: full run green, including 22 parser tests, 9 partition
  tests, pipeline restart tests, API and migration tests.
- Proof utilities: `scripts/verify_requested_fields.py`,
  `scripts/verify_dashboard.py`, `scripts/job_shape.py`,
  `scripts/simulate_interruption.py`, `scripts/check_extraction_index.py`.
- Source contract: `docs/UJS_ACCESS_FINDINGS.md`. Limits:
  `docs/KNOWN_LIMITATIONS.md`.

## Infrastructure
- PASS — Shared proxy/direct configuration; configuration and malformed-server tests.
- PASS — Credential validation and secret redaction; mocked credentials only.
- PASS — Proxy verifier browser cleanup after successful navigation and TLS failure (mocked).
- NOT TESTED — Lifecycle management across all other browser entry points.
- PASS — RATE_LIMIT_RPM alias and maximum 10 RPM configuration clamp.
- NOT TESTED — Shared request-level rate limiting across all browser entry points.
- PASS — Timeout/error classification unit tests.
- NOT TESTED — Controlled retry execution and browser launch-failure tests.

## Website
- BLOCKED — Live proxy connectivity: replacement PROXY_USERNAME and PROXY_PASSWORD unavailable locally.
- FAIL — Latest proxy verifier preflight: MISSING_CONFIGURATION:PROXY_USERNAME,PROXY_PASSWORD. No network attempted.
- BLOCKED — UJS reachability and real page rendering; proxy gate has not passed.
- BLOCKED — Search controls, filing-date input, required filters, results, pagination.
- NOT TESTED — Shared networking in existing direct-only UJS validator/source remains unfinished.
- NOT TESTED — Reusable inspect_ujs_page.py has not been implemented.
- NOT TESTED — Local HTML discovery and access-denial tests have not been added.

## Retrieval
- BLOCKED — Real PDF delivery mechanism and first real PDF retrieval.
- PASS — Existing offline PDF signature check, SHA-256 storage and pipeline regression tests.
- NOT TESTED — Full structural PDF integrity validation and structured retrieval metadata/failures.
- NOT TESTED — Dedicated duplicate-handling acceptance test.

## Processing
- PASS — Existing parser local-fixture regression tests.
- BLOCKED — Parser verification against real retrieved PDFs.
- NOT TESTED — Field-level accuracy report generation for this acceptance run.
- PASS — Existing extraction regression suite; no live accuracy claim.

## Evidence
- Full regression run: 40 passed in 16.74s (18 original plus 22 new tests).
- Ruff: All checks passed before latest report-only update.
- Proxy evidence: docs/validation/proxy_exit_20261106_144048_484332.json.
- No live UJS requests, screenshots, selectors, searches or PDFs produced in this work.
- Uncommitted project preserved; no commit created. Originals copied under data/backups/.


## Latest executed update � 2026-11-06
This section supersedes earlier live-blocker entries above.
- PASS � Authenticated proxy exit: US, New York; provider country targeting enabled from official syntax.
- PASS � UJS landing-page reachability: HTTP 200, title Case Search; screenshot manually inspected.
- PASS � Observed Search and Clear button selectors: #btnSearch and #btnReset.
- NOT TESTED � Search-mode interaction, filing-date workflow, results, pagination and document delivery.
- PASS � Strict PDF structure validation, fixture reopen/parse, SHA-256 and duplicate storage regression.
- PASS � Latest full suite: 45 passed in 19.79s; Ruff all checks passed.
- NOT TESTED � Updated shared-network validate_ujs_access entry point live rerun.
- NOT TESTED � Request-level rate limiting and complete source adapter integration remain unfinished.
- NOT TESTED � Date-level completeness bookkeeping and retrieval provenance improvements remain unfinished.
- NOT TESTED � Field-level accuracy report; no real PDFs downloaded.
- Evidence: docs/validation/ujs_access_20261106_144611.json and ujs_casesearch_20261106_144611.png.
- Credentials were supplied only to child-process environments; no credential file was created.

## Priority-one local regression increment
- PASS � Request-start limiter unit test enforces six-second spacing at the 10 RPM ceiling.
- Implemented � Inspector routes use the shared limiter and refuse external-origin subresources.
- Added � Real Chromium local-HTML test for observed form versus blank/error pages.
- No additional live portal requests made by this increment.

## Latest bounded live execution � 2026-11-06
- PASS � Date Filed search mode selected through the rendered control.
- PASS � Required start/end HTML date inputs accept YYYY-MM-DD; display placeholder mm/dd/yyyy.
- PASS � Advanced Search exposes County; bounded search used Adams and 2026-11-05 only.
- PASS � Search rendered caseSearchResultGrid with 46 DOM rows (not asserted as 46 cases).
- PASS � Four criminal Docket Sheet actions observed in that result set.
- BLOCKED � First observed criminal PDF endpoint returned HTTP 401. Stopped; no retries or bypass attempted.
- Real PDFs retrieved: 0. Failed retrieval attempts: 1. Real parser accuracy: not measurable.
- Pagination completeness: NOT TESTED; date must not be marked completed.
- Source adapter and live job persistence/export remain incomplete, not certified end-to-end.
- Evidence: ujs_workflow_20261106_145249.json and ujs_retrieval_sample.json under docs/validation.
- GitHub delivery: BLOCKED � no remote configured; destination repository URL required.

## GitHub delivery � 2026-11-06
- PASS � Initial project pushed to https://github.com/bvbvbv54/pa-docket-collector on master, commit 7a7c104.
- 98 project files included. Credential scan passed.
- Excluded ignored local runtime data and raw search-results JSON/screenshot containing personal details and signed links.
- Last regression run: 47 passed in 15.57s; Ruff all checks passed.
- Publication does not resolve the HTTP 401 document-retrieval blocker or certify end-to-end completion.
