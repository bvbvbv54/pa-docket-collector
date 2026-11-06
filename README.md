# PA Docket Collector

Offline-first collection, PDF validation, structured extraction, PostgreSQL storage, and CSV/XLSX export for Pennsylvania public docket sheets.

## Current delivery scope

The working pipeline uses synthetic docket PDFs and operator-supplied local PDFs. It validates PDF signatures, hashes and content-addresses files, extracts explicitly labeled fields with page/line evidence, writes idempotent docket/charge/document records transactionally, and exports one row per charge. The database-backed worker queue uses leases and `FOR UPDATE SKIP LOCKED` on PostgreSQL. SQLite is provided for local development.

The UJS browser adapter is deliberately a blocked stub. Live portal search and PDF retrieval have not been implemented or tested. Portal interaction is gated by both `LIVE_COLLECTION_ENABLED=true` and `LIVE_ACCESS_APPROVED=true`, and the current source still reports unvalidated until the documented UI acceptance work is completed. Do not treat fixture results as portal results.

## Local setup (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
$env:DATABASE_URL = "sqlite:///./data/local.sqlite3"
pa-docket doctor
```

Run an offline fixture collection and inspect its CSV:

```powershell
pa-docket collect --from 2025-01-01 --to 2025-01-31 --county Dauphin
pa-docket jobs list
pa-docket export --job JOB_ID --format csv --output .\dockets.csv
pa-docket export --job JOB_ID --format xlsx --output .\dockets.xlsx
```

Use `pa-docket collect --docket MD-01-CR-0000123-2025` for a known fixture docket. To process operator-supplied PDFs, use `pa-docket import-pdfs .\authorized-pdfs`. `pa-docket import-dockets input.csv` accepts `docket_number,pdf_path` columns and extracts the docket number from the PDF itself; the CSV docket number is not used to fill or override the source document.

Queue a job without running it using `--enqueue-only`, then run `pa-docket worker --once` or `pa-docket worker`. For a long-running worker use `pa-docket worker --watch`. Multiple worker containers may share PostgreSQL and the document volume.

## Tests and fixtures

```powershell
pytest
python scripts/generate_fixtures.py
```

Synthetic fixture PDFs are clearly labeled and contain no real people or docket data. OCR is intentionally out of scope: image-only PDFs receive `OCR_REQUIRED`, rather than a successful empty extraction.

## Docker Compose

Copy `.env.example` to `.env`, replace every placeholder with a unique secret, then:

```powershell
docker compose up --build -d
docker compose exec app pa-docket doctor
```

The app binds to loopback at port 8000 and should sit behind a client-managed HTTPS reverse proxy. Set a strong `SESSION_SECRET`, operator username/password, and database password before exposure. Scale parse workers according to available memory: `docker compose up --build -d --scale worker=2`. The default 2 GB target is sized for one worker container; additional replicas need the documented 4 GB upgrade path. Back up both PostgreSQL and the `docket_data` volume.

## Data handling

The dashboard requires an operator login. Secrets are read only from environment variables. Unknown fields remain NULL with their presence status; offense dates are read only from explicit offense-date labels; affiants are not promoted to arresting officers; race is never inferred. Public docket data is not a certified background check. Restricted, sealed, expunged, juvenile, and limited-access records must not be collected.

## Live source status

The official help page describes Case Search as a way to view public docket sheets and the reference guide documents Date Filed start/end criteria and a Docket Sheet action. Those documents establish the visible public workflow but do not document selectors, session internals, pagination completeness, or a public machine interface. Those details remain unvalidated here; no selector or endpoint has been fabricated. See [`docs/ACCESS_POLICY.md`](docs/ACCESS_POLICY.md) before implementing the live adapter.

To run the read-only access check from an authorized network, install the optional browser and Chromium, then run `python scripts/validate_ujs_access.py`. The checker forces a direct connection, ignores proxy environment variables, never submits a search, and writes observed controls to `docs/UJS_ACCESS_FINDINGS.md`. When Chromium is available, it also writes a local landing-page screenshot under `artifacts/`.

```powershell
python -m pip install -e ".[browser]"
playwright install chromium
python scripts/validate_ujs_access.py
```

Search, pagination, and PDF retrieval remain disabled until a reviewer completes the checks in [`docs/LIVE_READINESS.md`](docs/LIVE_READINESS.md).

