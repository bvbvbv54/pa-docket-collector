from __future__ import annotations

from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from pa_docket.sources.base import AccessStatus

CASE_SEARCH_URL = "https://ujsportal.pacourts.us/CaseSearch"
USER_AGENT = "PA-Docket-Collector-access-check/0.1"


def probe_direct(url: str = CASE_SEARCH_URL, timeout: float = 15.0) -> AccessStatus:
    """Check the public page using a verified TLS connection and no proxy handler."""
    checked = datetime.now(UTC).isoformat()
    request = Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            response.read(1024 * 1024)
            return AccessStatus(
                status="ACCESSIBLE" if 200 <= status < 400 else "ACCESS_DENIED",
                checked_at=checked,
                url=response.geturl(),
                http_status=status,
                detail="Direct request completed; TLS certificate verification remained enabled.",
            )
    except HTTPError as exc:
        body = exc.read(32_768).decode("utf-8", errors="ignore").casefold()
        geo_marker = any(
            word in body for word in ("geographic", "geo-restricted", "your region", "country restriction")
        )
        blocked = exc.code in {403, 451} and geo_marker
        return AccessStatus(
            status="GEO_BLOCKED" if blocked else "ACCESS_DENIED",
            checked_at=checked,
            url=exc.url or url,
            http_status=exc.code,
            detail="The server explicitly indicated a location restriction."
            if blocked
            else f"The server returned HTTP {exc.code}; the response did not prove a geographic restriction.",
        )
    except (URLError, TimeoutError, OSError) as exc:
        return AccessStatus(
            status="UNREACHABLE",
            checked_at=checked,
            url=url,
            detail=f"Direct connection failed ({type(exc).__name__}); geographic blocking is unconfirmed.",
        )
