from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/pa_docket.sqlite3"
    storage_dir: Path = Path("./data")
    max_pdfs_per_job: int = 500
    max_parse_workers: int = 2
    max_concurrent_downloads: int = 3
    max_browser_contexts: int = 3
    requests_per_minute: int = 10
    network_mode: str = "direct"
    proxy_server: str | None = None
    proxy_username: str | None = field(default=None, repr=False)
    proxy_password: str | None = field(default=None, repr=False)
    proxy_country: str = "US"
    live_collection_enabled: bool = False
    live_access_approved: bool = False
    operator_username: str | None = None
    operator_password: str | None = None
    session_secret: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            storage_dir=Path(os.getenv("STORAGE_DIR", "./data")),
            max_pdfs_per_job=int(os.getenv("MAX_PDFS_PER_JOB", "500")),
            max_parse_workers=int(os.getenv("MAX_PARSE_WORKERS", "2")),
            max_concurrent_downloads=int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3")),
            max_browser_contexts=int(os.getenv("MAX_BROWSER_CONTEXTS", "3")),
            requests_per_minute=min(
                10, max(1, int(os.getenv("RATE_LIMIT_RPM", os.getenv("REQUESTS_PER_MINUTE", "10"))))) ,
            network_mode=os.getenv("NETWORK_MODE", "direct").strip().lower(),
            proxy_country=os.getenv("PROXY_COUNTRY", "US").strip().upper(),
            proxy_server=os.getenv("PROXY_SERVER") or None,
            proxy_username=os.getenv("PROXY_USERNAME") or None,
            proxy_password=os.getenv("PROXY_PASSWORD") or None,
            live_collection_enabled=os.getenv("LIVE_COLLECTION_ENABLED", "false").lower() == "true",
            live_access_approved=os.getenv("LIVE_ACCESS_APPROVED", "false").lower() == "true",
            operator_username=os.getenv("OPERATOR_USERNAME") or None,
            operator_password=os.getenv("OPERATOR_PASSWORD") or None,
            session_secret=os.getenv("SESSION_SECRET") or None,
        )

    @property
    def live_ready(self) -> bool:
        return self.live_collection_enabled and self.live_access_approved
