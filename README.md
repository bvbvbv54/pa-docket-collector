# PA Docket Collector

Collect Pennsylvania public criminal docket sheets from
`https://ujsportal.pacourts.us/CaseSearch` by filing date, extract every
requested field with evidence, store records idempotently, and export one row per
charge to CSV or Excel.

Attribution: Built by ☠ SYSTEM BREACHED ☠ —◥꧁ SHΔDØW HΛCKΞR ꧂◤

## What actually runs

The collector executes the full workflow against the live portal:

1. Select **Search By → Date Filed**, apply a county, set `FiledStartDate` and
   `FiledEndDate`.
2. Enumerate the results grid (`#caseSearchResultGrid`). There is **no
   pagination**; completeness is proven by the absence of the portal's own
   truncation sentence.
3. Recover Magisterial District truncation by enumerating every
   `MDJS Court Office` in the county and unioning the rows.
4. Download each criminal docket sheet PDF through the issuing session.
5. Parse the two observed document families (`MDJS 1200`, `CPCMS 9082`).
6. Upsert one `ExtractionResult` per `(job_id, docket_id)` and one export row per
   charge.
7. Record partition coverage: `COMPLETE`, `TRUNCATED`, `FAILED`, `ZERO_RESULTS`,
   or `PARTIAL` when a docket limit stopped downloads early.

Every job keeps the docket number, the source PDF hash, and the retrieval time
that produced each row.

## Requirements

- Python 3.12
- Chromium via Playwright
- A US egress for production; DataImpulse proxy for development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,browser]"
playwright install chromium
```

## Route configuration

The portal has two hard requirements, both real and both measured:

| Setting | Value | Why |
| --- | --- | --- |
| User agent | Chrome desktop `Chrome/156.0.0.0` | headless UA gets `401` on `/Report/*` |
| Session | sticky exit | rotating exits invalidate the signed `dnh` token |
| Transport | `--disable-quic --disable-http2` | HTTP/2 through a cold tunnel fails |

Production runs direct from the US. Development runs through DataImpulse with
sticky session suffix:

```powershell
$env:NETWORK_MODE = "proxy"
$env:PROXY_SERVER = "http://gw.dataimpulse.com:823"
$env:PROXY_USERNAME = "USERNAME__cr.us;sessid.padocket"
$env:PROXY_PASSWORD = "PASSWORD"
```

Switch to `NETWORK_MODE=direct` for production. No scraper code changes between
the two.

## Gates

Live collection requires both flags and a preflight findings file:

```powershell
$env:LIVE_COLLECTION_ENABLED = "true"
$env:LIVE_ACCESS_APPROVED = "true"
pa-docket doctor
```

`UJSPortalSource.search`, `search_partition`, `iter_partitions`, and
`get_document` raise `LiveAccessNotValidatedError` until the gates are satisfied.
The collector never solves or bypasses a CAPTCHA and makes no attempt to defeat
portal security.

## Collect

```powershell
$env:DATABASE_URL = "sqlite:///./data/pa_docket.sqlite3"
pa-docket collect-live --from 2026-11-05 --to 2026-11-05 --county Lancaster --limit 8
```

- `--limit` caps downloaded dockets; hitting it marks the partition `PARTIAL`.
- `--enqueue-only` stores PDFs and leaves extraction to `pa-docket worker`.
- `--workers N` sets parse concurrency for the inline run.
- `--resume-job JOB_ID` extends an existing job: partitions already sealed
  `COMPLETE` or `ZERO_RESULTS` are skipped without a search.
- Windows are capped at 180 days by the portal.

A dropped proxy tunnel is retried four times on the issuing session. If it still
fails, only that docket is failed and named; the partition continues and the job
is `PARTIAL`.

Re-running the same dates is safe: dockets already queued for the job are
skipped, and an extraction for the same `(job_id, docket_id)` replaces the prior
result rather than appending. A restart resumes from the last committed
partition.

## Process and inspect

```powershell
pa-docket worker --once
pa-docket jobs list
pa-docket jobs status JOB_ID
pa-docket export --job JOB_ID --format csv  --output .\dockets.csv
pa-docket export --job JOB_ID --format xlsx --output .\dockets.xlsx
```

Dashboard:

```powershell
pa-docket serve
# http://127.0.0.1:8000  (operator login required)
```

`/api/jobs/{id}` and `/jobs/{id}` serve stored dockets, charges, per-partition
coverage and the export links.

## Export columns

`docket_number, court_type, county, filing_date, defendant_name,
arresting_officer, defendant_race, charge_number, charge_description,
charge_statute, offense_date, charge_disposition, disposition_date, case_status,
case_disposition, source_url, retrieved_at, validation_status`

One row per charge. `filing_date` is when the case was filed, never the offense
date. `case_status` is the report's `Case Status:` cell; `case_disposition` is the
case-level outcome. Unavailable data is empty with an explicit presence status
(`MISSING`, `NOT_RECORDED`), never inferred.

## Field availability

- **Race** prints only on `MDJS 1200`. `CPCMS 9082` has no race field, so CP rows
  carry `NOT_RECORDED` with section evidence; this is not a failed extraction.
- **Fresh filings often have no disposition.** A docket filed today commonly shows
  `Disposition:` blank and `Case Status: Active`. Those rows carry `MISSING`.
- **Unrecognised disposition labels** keep `raw_value`, set
  `normalized_value=None`, `presence_status=UNVERIFIED`, and add a validation
  error. No silent nearest-match mapping.
- **OCR is not implemented.** Image-only PDFs fail with `OCR_REQUIRED` /
  `INVALID_PDF` instead of inventing fields.

## Coverage honesty

The collector reports what the portal delivered:

- A capped **Common Pleas** set cannot be subdivided on a Date Filed search. It is
  stored `TRUNCATED` with an explicit error and the job is `PARTIAL`.
- A capped **Magisterial District** set is recovered per court office. If recovery
  closes the gap the partition is `COMPLETE`; the notice stays in the partition
  parameters as evidence.
- A docket limit marks the partition `PARTIAL` and names how many criminal
  dockets were not retrieved.
- Non-criminal dockets served by the same report are counted as skipped, not
  failed. Only `-CR-` dockets are collected.

## Tests and fixtures

```powershell
pytest
python scripts/generate_fixtures.py
```

Fixtures are synthetic and clearly labeled. Parser tests cover both document
families, wrapped descriptions, page-break section detection, footer bleed, and
non-criminal detection. Pipeline tests cover restart recovery, interrupted
leases, and same-docket replacement.

Live proof utilities:

```powershell
python scripts/run_demo.py --from 2026-11-04 --to 2026-11-05 --county Adams --max-dockets 6
python scripts/verify_requested_fields.py JOB_ID
python scripts/verify_dashboard.py JOB_ID
python scripts/job_shape.py JOB_ID
python scripts/simulate_interruption.py
```

## Docker Compose

Copy `.env.example` to `.env`, replace every placeholder, then:

```powershell
docker compose up --build -d
docker compose exec app pa-docket doctor
```

The app binds loopback on 8000 and belongs behind a client-managed HTTPS reverse
proxy. Set a strong `SESSION_SECRET`, operator credentials, and a database
password before exposure. Back up both PostgreSQL and the `docket_data` volume.

## Data handling

The dashboard requires operator login. Secrets come only from environment
variables. Unlabeled fields stay NULL with a presence status; affiants are never
promoted to arresting officers; race is never inferred. Public docket data is not
a certified background check. Restricted, sealed, expunged, juvenile, and
limited-access records must not be collected.

## Documentation

- [`docs/UJS_ACCESS_FINDINGS.md`](docs/UJS_ACCESS_FINDINGS.md) — portal contract
- [`docs/LIVE_READINESS.md`](docs/LIVE_READINESS.md) — gates and executed checks
- [`docs/DEMO.md`](docs/DEMO.md) — end-to-end demonstration runbook
- [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) — measured limits
- [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) — acceptance state
- [`docs/ACCESS_POLICY.md`](docs/ACCESS_POLICY.md) — access policy
