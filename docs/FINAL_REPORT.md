# PA UJS Docket Collector — Measured Results

Prepared 2026-11-06. Every number below was read from the local database or the
exported files produced by the commands shown. Nothing here is projected.

## What was measured

| Run | Command | Result |
| --- | --- | --- |
| Headline demo | `scripts/run_demo.py --from 2026-11-04 --to 2026-11-05 --county Adams --max-dockets 10` | 8 dockets, 25 charges, 2 `COMPLETE` partitions, verdict `PASS` |
| Resumed run | `pa-docket collect-live --from 2026-11-04 --to 2026-11-06 --county Adams --limit 10 --resume-job e24feda7-… --enqueue-only` | 1 new partition (2026-11-06, 18 grid rows, 1 docket); no repeated work |
| Final job state | job `e24feda7-7a51-4fc1-be56-8b4d5a3b6cd8` | 9 dockets, 28 charges, 121 grid rows, 3 `COMPLETE` partitions, 9/9 tasks |
| Dashboard | `scripts/verify_dashboard.py e24feda7-…` | 200 OK, 9 docket numbers in HTML, 3 coverage rows, CSV 28 rows, XLSX 8,536 bytes (`PK`) |
| Tests | `pytest tests -q` | 90 passed; `ruff check src tests scripts` clean |

Job `e24feda7-…` was collected through the public portal with the production
configuration (Chrome user agent, sticky proxy exit, HTTP/1.1).

### Partition coverage

| Filing date | County | Grid rows | Criminal dockets | Coverage |
| --- | --- | ---: | ---: | --- |
| 2026-11-04 | Adams | 58 | 4 | `COMPLETE` |
| 2026-11-05 | Adams | 45 | 4 | `COMPLETE` |
| 2026-11-06 | Adams | 18 | 1 | `COMPLETE` |
| **Total** | | **121** | **9** | |

112 grid rows were non-criminal dockets (traffic and civil) and were counted as
skipped, never as charges. No truncation notice appeared on any Adams partition,
so `COMPLETE` is justified by the portal's own completeness signal, not by an
assumption.

## Field results

Counted across the 9 dockets and 28 charges of `e24feda7-…`. Status values are
the extractor's explicit presence markers, not silence.

| Field | PRESENT | NOT_RECORDED | MISSING |
| --- | ---: | ---: | ---: |
| `docket_number` | 9 | 0 | 0 |
| `defendant_name` | 9 | 0 | 0 |
| `charge_description` | 28 | 0 | 0 |
| `charge_statute` | 28 | 0 | 0 |
| `offense_date` | 28 | 0 | 0 |
| `arresting_officer` | 28 | 0 | 0 |
| `defendant_race` | 8 | 1 | 0 |
| `charge_disposition` | 1 | 0 | 27 |
| `disposition_date` | 1 | 0 | 27 |
| `case_status` | 9 | 0 | 0 |
| `case_disposition` | 1 | 8 | 0 |

All 9 records carry `validation_status = VALIDATED`.

Supporting runs, same extractor, different document families:

| Job | Scope | Result |
| --- | --- | --- |
| `4dbf2448-…` | Lancaster Common Pleas, 2026-11-05 | 6 dockets / 31 charges, every charge field `PRESENT`, race `NOT_RECORDED` (field absent from CPCMS 9082) |
| `0776d3c4-…` | Lancaster mixed CP + MDJ, 2026-11-05 | 8 dockets / 42 charges, dispositions `waived` / `held` / `changed` / `guilty` extracted, partition `PARTIAL` with the 8-of-58 limit recorded |
| `ffeae2fc-…` | Adams MDJ, 2026-11-05 | 4 dockets / 14 charges, disposition fields `MISSING` because the source prints them blank |

## Incorrect or absent fields

- **`charge_disposition` / `disposition_date` are `MISSING` on 27 of 28 charges.**
  The Adams dockets are fresh filings; the source PDF prints `Disposition:` with
  an empty value and `Case Status: Active`. The extractor reports the blank as
  `MISSING` rather than guessing. The one populated charge is a Lancaster Common
  Pleas case with a recorded disposition.
- **`defendant_race` is `NOT_RECORDED` on 1 of 9 dockets.** That docket is a
  Common Pleas sheet; the CPCMS layout has no race field. On the 8 MDJ sheets race
  is explicitly printed and was extracted.
- **`case_disposition` is `NOT_RECORDED` on 8 of 9 dockets**, for the same reason:
  no final case disposition has been entered yet on active cases.
- No field was fabricated, inferred from a name, or carried across dockets. Every
  value retains its `raw_value`, `normalized_value`, `presence_status` and page
  reference for audit.

## Resilience and restart

Proven, with regression tests:

- A proxy tunnel that dies mid-download is retried four times on the issuing
  session. If it still fails, only that docket is failed and named
  (`documents_failed`), the partition continues, and the job is `PARTIAL`.
  Tests: `test_one_dead_tunnel_fails_only_that_docket`,
  `test_report_fetch_retries_the_tunnel_then_succeeds`,
  `test_report_fetch_gives_up_after_the_attempt_budget`.
- An unexpected crash inside a partition seals it `FAILED`; a job is never left
  `RUNNING` by an exception. Test: `test_unexpected_crash_seals_the_partition_and_the_job`.
- Adding filing dates to a finished job skips `COMPLETE` and `ZERO_RESULTS`
  partitions without a search. Tests: `test_resume_skips_finished_partitions_and_keeps_the_same_job`,
  `test_skip_avoids_searching_finished_partitions`, `test_resume_of_missing_job_is_refused`.
- Re-downloading the same docket inside a job produces one extraction row and one
  export row; `(job_id, docket_id)` is unique in the database (Alembic `0002`).
- Interrupted job `a1dd4c4d-…`: two orphaned tasks with stale leases were
  recovered and finished.
- Partition `result_count` reports criminal dockets; the raw grid row count is
  kept separately in partition params.

## Coverage limits and remaining blockers

1. **Common Pleas truncation is not recoverable.** When a wide Date Filed search
   exceeds the portal's display limit, the grid prints
   `Not all results are shown for <court>`. MDJ days are recovered by enumerating
   every `MDJS Court Office` in the county; Common Pleas has no equivalent
   subdivision on Date Filed. Such partitions are recorded `PARTIAL` with the
   notice kept as evidence. The remedy is a narrower date window or a single
   county, not a code change.
2. **The result grid has no pagination.** Completeness therefore rests on the
   absence of the truncation notice. If the portal changes that wording, the
   collector will not silently claim completeness — the notice check is the only
   completeness gate.
3. **Dispositions lag filings.** Fresh filings print blank dispositions. Any
   report generated on filing date will show `MISSING` for those fields until the
   case advances; that is the source, not the extractor.
4. **Client authorization and retention-policy approval are still pending.** This
   is a policy item, not a code item. The collector reads only public case search
   and public docket sheets, and never bypasses CAPTCHA or any portal access
   control; if a CAPTCHA appears the partition is recorded `BLOCKED`.
5. **Multi-host worker deployment with shared document storage has not been
   tested.** Single-host operation is proven end to end.

## Reproduce

```powershell
# full pipeline with integrity checks
.\.venv\Scripts\python.exe scripts\run_demo.py --from 2026-11-04 --to 2026-11-05 --county Adams --max-dockets 10

# field matrix for a stored job
.\.venv\Scripts\python.exe scripts\verify_requested_fields.py JOB_ID

# exports
pa-docket export --job JOB_ID --format csv  --output .\dockets.csv
pa-docket export --job JOB_ID --format xlsx --output .\dockets.xlsx
```

Build notes and operating detail: `README.md`, `docs/DEMO.md`,
`docs/KNOWN_LIMITATIONS.md`, `docs/IMPLEMENTATION_STATUS.md`.
