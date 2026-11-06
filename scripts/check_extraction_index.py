"""Check the extraction-result uniqueness index and duplicate groups in the DB."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pa_docket.config import Settings

settings = Settings.from_env()
url = settings.database_url
path = url.split("sqlite:///")[-1] if "sqlite:///" in url else None
if not path:
    raise SystemExit(f"not a sqlite database: {url}")
connection = sqlite3.connect(Path(path))
row = connection.execute(
    "select sql from sqlite_master where name = 'uq_extraction_job_docket'"
).fetchone()
print("index:", row[0] if row else "MISSING")
groups = connection.execute(
    "select job_id, docket_id, count(*) n from extraction_results "
    "where docket_id is not null group by 1, 2 having n > 1"
).fetchall()
print("duplicate (job, docket) groups:", groups)
