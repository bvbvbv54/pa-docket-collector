# End-to-end demonstration

One command runs the whole client workflow against the live portal and fails on
any integrity breach:

```powershell
.\.venv\Scripts\python.exe scripts\run_demo.py `
  --from 2026-11-04 --to 2026-11-05 `
  --county Adams --max-dockets 10
```

Measured on 2026-11-06 with that command: 103 grid rows examined, 8 criminal
dockets across 2 filing dates, 25 charges, 8 source PDFs verified on disk, 25 CSV
rows, 25 XLSX rows, both partitions `COMPLETE`, verdict `PASS`.

What it does, in order:

1. `collect_live` searches each filing date and county from the rendered form.
2. Enumerates the results grid; recovers Magisterial District truncation per
   court office.
3. Downloads every criminal docket sheet PDF through the issuing session.
4. Runs the worker until the queue drains: parse, validate, upsert.
5. Reconciles the database: duplicate `(job_id, docket_id)` rows, orphan charges,
   dockets without a source PDF, missing files on disk, export row counts.
6. Writes `exports/<job_id>.csv` and `exports/<job_id>.xlsx`.
7. Prints the partition table, the field presence matrix, coverage gaps, and a
   PASS/FAIL verdict.

Exit code is 0 only when every check passes.

## What the output proves

| Line | Meaning |
| --- | --- |
| `collection: {...}` | dockets discovered, documents stored, failures, duplicates skipped |
| `partitions:` | one row per filing date and county, with coverage status |
| `requested fields:` | presence counts per field, including explicit `MISSING` / `NOT_RECORDED` |
| `job=... status=PARTIAL` | a coverage gap or a docket limit stopped the run short |
| `verdict: PASS` | every stored charge traces to a docket and a retained PDF |

`MISSING` and `NOT_RECORDED` are correct outcomes when the document states
nothing. They are never fabricated and never silently filled.

## Coverage states

| Status | Meaning |
| --- | --- |
| `COMPLETE` | no truncation notice, or MDJ recovery closed the gap |
| `TRUNCATED` | the portal withheld rows and recovery could not close it |
| `FAILED` | the portal could not be read for that partition |
| `ZERO_RESULTS` | the portal returned no cases for that day and county |
| `PARTIAL` | a docket limit stopped downloads; the unretrieved count is recorded |

## Restart demonstration

Run the same command twice. The second job discovers the same dockets, skips
nothing new, and stores no duplicate export rows:

```powershell
.\.venv\Scripts\python.exe scripts\job_shape.py JOB_ID_1 JOB_ID_2
```

Extend a finished job by adding a filing date. Completed partitions are skipped
without a search:

```powershell
pa-docket collect-live `
  --from 2026-11-04 --to 2026-11-06 `
  --county Adams --limit 10 `
  --resume-job JOB_ID
```

The audit log records `LIVE_COLLECTION_RESUMED` with the skipped partitions.

Interruption recovery:

```powershell
.\.venv\Scripts\python.exe scripts\simulate_interruption.py
```

## Dashboard demonstration

```powershell
.\.venv\Scripts\python.exe -m uvicorn pa_docket.api.app:app --host 127.0.0.1 --port 8077
.\.venv\Scripts\python.exe scripts\verify_dashboard.py JOB_ID
```

The verifier logs in, reads `/api/jobs/{id}`, asserts every stored docket number
appears in `/jobs/{id}`, and downloads both exports.

## Field report

```powershell
.\.venv\Scripts\python.exe scripts\verify_requested_fields.py JOB_ID
```

Prints one block per docket with presence status and evidence section for each
requested field.

## Docket-number retrieval and refresh

A known docket is searched in Docket Number mode, so the report URL is signed
fresh on every retrieval:

```powershell
pa-docket collect-dockets --docket MJ-51303-CR-0000199-2026 --progress
```

Refresh re-searches each stored docket number, downloads the latest sheet,
compares it with the stored record, and writes a revision only when a field
actually moved:

```powershell
pa-docket refresh --docket MJ-51303-CR-0000199-2026 --progress
pa-docket refresh --job JOB_ID --limit 50 --progress
```

A refresh that finds the same document and the same values writes no revision.
The docket page prints the revision history when one exists.

## Records page and filtered downloads

```powershell
.\.venv\Scripts\python.exe -m uvicorn pa_docket.api.app:app --host 127.0.0.1 --port 8077
```

Then open `http://127.0.0.1:8077/records`. Filters: filing date range, county,
charge text, disposition. `Download Excel` and `Download CSV` carry the same
filters to `/api/exports/records.xlsx` and `/api/exports/records.csv`; both stream
one row per charge and read the same query as the table.

`/dockets/<docket-number>` shows the extracted fields, the charge table, the
stored PDF link, and the revision history.

