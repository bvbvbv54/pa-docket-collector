"""Live UJS Case Search adapter.

Observed behaviour this module is built around (see docs/UJS_ACCESS_FINDINGS.md):

* The result grid renders every matching row at once and exposes no pager.
  When more rows match than the portal will render it prints
  ``Not all results are shown for <court>... Please narrow your search.``
  That notice is the completeness signal, not the row count.
* The notice names the truncated court. A county/day search can return a
  complete Common Pleas set while Magisterial District is truncated, so
  recovery is applied per court and never silently widens the claim.
* Magisterial District results can be subdivided with the ``MDJS Court Office``
  selector, which is populated per county. Common Pleas has no equivalent
  selector on a Date Filed search, so an unrecoverable Common Pleas truncation
  is recorded as a coverage gap rather than papered over.
* Docket PDFs are served from ``/Report/*`` and are fingerprinted by the
  User-Agent: a headless UA gets ``401 Unauthorized request`` while a normal
  Chrome UA gets ``200 application/pdf``. One browser identity is used for the
  whole session.
* Report links carry a signed ``dnh`` token produced by the search response.
  Tokens are resolved inside the session that produced them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError

from pa_docket.config import Settings
from pa_docket.documents.storage import InvalidPdfError, store_pdf, validate_pdf_bytes
from pa_docket.network import (
    NetworkConfigurationError,
    RequestLimiter,
    browser_proxy,
    classify_error,
    new_proxy_session_id,
    redact,
)
from pa_docket.sources.base import AccessStatus, DocketDetail, DocketRef, DocumentResult, SearchQuery, SearchResult
from pa_docket.sources.ujs.access import CASE_SEARCH_URL, probe_direct
from pa_docket.sources.ujs.selectors import (
    ADVANCED_SEARCH,
    COUNTY_SELECTOR,
    COURT_OFFICE_SELECTOR,
    DATE_FIELD_OPTION,
    DATE_FROM_INPUT,
    DATE_TO_INPUT,
    DOCKET_NUMBER_INPUT,
    DOCKET_NUMBER_OPTION,
    GRID_ROWS,
    RESULT_GRID,
    SEARCH_BUTTON,
    SEARCH_MODE_SELECTOR,
    TRUNCATION_NOTICE,
)

ORIGIN = "https://ujsportal.pacourts.us"

CRIMINAL_MARKER = "-CR-"
MAX_RANGE_DAYS = 180
DEFAULT_TIMEOUT_MS = 90_000
GRID_SETTLE_POLLS = 30
GRID_SETTLE_SECONDS = 1.0
GRID_STABLE_POLLS = 4
MAX_GRID_ROWS = 5000
# A dead proxy tunnel is transient; the signed report token is not. Retry the
# fetch on the same session before failing the single docket.
REPORT_FETCH_ATTEMPTS = 4
REPORT_FETCH_BACKOFF_SECONDS = 2.0

COVERAGE_COMPLETE = "COMPLETE"
COVERAGE_TRUNCATED = "TRUNCATED"
COVERAGE_ZERO = "ZERO_RESULTS"
COVERAGE_FAILED = "FAILED"


class PortalUnreachableError(RuntimeError):
    """The public Case Search page could not be reached from this network."""


class LiveAccessNotValidatedError(RuntimeError):
    """Reachability alone is insufficient to enable searches or document retrieval."""


class PortalStructureError(RuntimeError):
    """The portal answered but no longer matches the observed page contract."""


@dataclass
class GridRow:
    docket_number: str
    court_type: str
    case_caption: str
    case_status: str
    filing_date: str
    county: str
    court_office: str
    detail_url: str | None
    summary_url: str | None


@dataclass
class DocketLookup:
    """One docket-number search and the row the portal returned for it."""

    docket_number: str
    found: bool = False
    row: GridRow | None = None
    rows: list[GridRow] = field(default_factory=list)
    notice: str | None = None
    pages_visited: int = 0
    errors: list[str] = field(default_factory=list)
    coverage_status: str = COVERAGE_ZERO
    body_excerpt: str = ""


@dataclass
class PartitionResult:
    """One search window and everything the portal returned for it."""

    filing_date: date
    county: str | None
    court_office: str | None = None
    rows: list[GridRow] = field(default_factory=list)
    truncated_notice: str | None = None
    notice_courts: list[str] = field(default_factory=list)
    pages_visited: int = 0
    errors: list[str] = field(default_factory=list)
    coverage_status: str = COVERAGE_COMPLETE

    @property
    def criminal_rows(self) -> list[GridRow]:
        """Criminal rows only, first occurrence wins.

        The grid can render the same docket once per participant; the docket
        number is the collection unit, so repeats collapse here rather than at
        every caller.
        """
        seen: dict[str, GridRow] = {}
        for row in self.rows:
            if CRIMINAL_MARKER in row.docket_number:
                seen.setdefault(row.docket_number.strip(), row)
        return list(seen.values())

    @property
    def skipped_rows(self) -> int:
        return len(self.rows) - len(
            [row for row in self.rows if CRIMINAL_MARKER in row.docket_number]
        )

    @property
    def truncated(self) -> bool:
        return self.truncated_notice is not None


def _notice_courts(notice: str) -> list[str]:
    text = notice.casefold()
    courts = []
    if "common pleas" in text:
        courts.append("Common Pleas")
    if "magisterial district" in text:
        courts.append("Magisterial District")
    return courts or ["Unspecified"]


def _docket_sort_key(value: str) -> str:
    return value.strip()


def parse_us_date(value: str | None) -> date | None:
    """Parse the portal's MM/DD/YYYY grid date; never guess."""
    if not value:
        return None
    parts = value.strip().split("/")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[2]), int(parts[0]), int(parts[1]))
    except ValueError:
        return None


class UJSPortalSource:
    """Playwright-backed reader for public Pennsylvania criminal docket sheets."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 90.0,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.timeout = timeout
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._limiter = RequestLimiter(self.settings.requests_per_minute)
        self._session_id: str | None = None
        self._env = {
            key: value
            for key, value in __import__("os").environ.items()
            if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
        }

    # ------------------------------------------------------------------- gate

    async def check_access(self) -> AccessStatus:
        if self.settings.network_mode.strip().lower() == "direct":
            return await asyncio.to_thread(probe_direct, CASE_SEARCH_URL, 20.0)
        try:
            browser_proxy(self.settings, "access-check")
        except NetworkConfigurationError as exc:
            return AccessStatus(
                status="BLOCKED",
                checked_at="",
                url=CASE_SEARCH_URL,
                detail=f"Proxy configuration rejected: {exc}",
            )
        return AccessStatus(
            status="CONFIGURED",
            checked_at="",
            url=CASE_SEARCH_URL,
            detail="Proxy route is configured; page reachability is proven when the browser session opens.",
        )

    def _findings(self) -> dict[str, Any]:
        path = self.settings.storage_dir / "ujs_access_findings.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    async def _require_ready(self) -> None:
        if not self.settings.live_ready:
            raise LiveAccessNotValidatedError(
                "Live collection is disabled. Set LIVE_COLLECTION_ENABLED and "
                "LIVE_ACCESS_APPROVED after the recorded validation checks pass."
            )
        findings = self._findings()
        missing = [
            key
            for key in ("selectors_verified", "search_flow_validated", "pdf_delivery_validated")
            if findings.get(key) is not True
        ]
        if missing:
            raise LiveAccessNotValidatedError(
                "Live validation evidence is incomplete: " + ", ".join(missing)
                + ". See docs/UJS_ACCESS_FINDINGS.md."
            )

    # ---------------------------------------------------------------- session

    async def open(self) -> None:
        from playwright.async_api import async_playwright

        if self._browser is not None:
            return
        self._playwright = await async_playwright().start()
        await self._launch()

    async def _launch(self) -> None:
        from playwright.async_api import Error as PlaywrightError

        session_id = new_proxy_session_id("ujs")
        proxy = browser_proxy(self.settings, session_id)
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            proxy=proxy,
            # The provider's gateway occasionally resets a cold HTTP/2 tunnel;
            # HTTP/1.1 with QUIC disabled keeps one sticky exit alive.
            args=["--disable-quic", "--disable-http2"],
            env=self._env,
        )
        self._context = await self._browser.new_context(
            ignore_https_errors=False,
            accept_downloads=True,
            user_agent=self.settings.browser_user_agent,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 1000},
        )
        host = urlsplit(CASE_SEARCH_URL).hostname
        limiter = self._limiter

        async def gate(route):
            request = route.request
            if urlsplit(request.url).hostname != host:
                await route.abort()
                return
            if request.resource_type in {"image", "font", "media"}:
                await route.abort()
                return
            if request.resource_type in {"document", "xhr", "fetch"}:
                await limiter.wait()
            try:
                await route.continue_()
            except PlaywrightError:
                pass

        await self._context.route("**/*", gate)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        self._page.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
        self._session_id = session_id

    async def _drop_session(self) -> None:
        browser, self._browser = self._browser, None
        self._context = None
        self._page = None
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    async def _ensure_page(self):
        """Open a session, retrying with a fresh sticky exit when the tunnel dies."""
        failures: list[str] = []
        for _attempt in range(4):
            if self._page is None:
                await self.open()
            try:
                await self._page.goto(CASE_SEARCH_URL, wait_until="domcontentloaded")
                await self._page.locator("body").wait_for(timeout=20_000)
                if await self._page.locator(SEARCH_MODE_SELECTOR).count() == 0:
                    raise PortalStructureError("Search form is not present at the landing URL")
                return self._page
            except Exception as exc:
                failures.append(classify_error(exc))
                await self._drop_session()
        raise PortalUnreachableError(
            "UJS Case Search could not be opened after 4 attempts: " + ", ".join(failures)
        )

    async def close(self) -> None:
        await self._drop_session()
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def __aenter__(self) -> UJSPortalSource:
        await self.open()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    # ----------------------------------------------------------------- search

    async def _prepare_date_form(self, page, county: str | None) -> None:
        await page.locator(SEARCH_MODE_SELECTOR).select_option(DATE_FIELD_OPTION)
        # The advanced controls mount after the search mode change settles.
        await page.wait_for_timeout(1_000)
        await page.locator(ADVANCED_SEARCH).evaluate("element => element.click()")
        await page.locator(COUNTY_SELECTOR).wait_for(state="visible", timeout=20_000)
        if county:
            await page.locator(COUNTY_SELECTOR).select_option(county)
            await page.wait_for_timeout(1_500)

    async def court_offices(self, county: str) -> list[str]:
        page = await self._ensure_page()
        await self._prepare_date_form(page, county)
        return await page.locator(COURT_OFFICE_SELECTOR).evaluate(
            "select => [...select.options].map(option => option.value).filter(Boolean)"
        )

    async def _read_grid(self, page) -> tuple[list[GridRow], str | None, str]:
        body = await page.locator("body").inner_text()
        notice = next(
            (line.strip() for line in body.splitlines() if TRUNCATION_NOTICE in line),
            None,
        )
        raw_rows = await page.locator(RESULT_GRID).evaluate_all(
            """
            (tables, limit) => {
                const root = tables && tables[0];
                if (!root || typeof root.querySelectorAll !== 'function') return [];
                const rows = [...root.querySelectorAll('tbody tr')].slice(0, limit);
                return rows.map(tr => {
                    const cells = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
                    const links = [...tr.querySelectorAll('a')].map(a => ({
                        label: (a.innerText || a.getAttribute('aria-label') || '').trim(),
                        href: a.getAttribute('href'),
                    }));
                    const pick = label => (links.find(l => l.label === label) || {}).href || null;
                    return {
                        cells,
                        docketSheet: pick('Docket Sheet'),
                        courtSummary: pick('Court Summary'),
                    };
                });
            }
            """,
            MAX_GRID_ROWS,
        )
        rows: list[GridRow] = []
        for entry in raw_rows:
            cells = [cell for cell in entry["cells"]]
            docket = cells[2] if len(cells) > 2 else ""
            if not docket:
                continue
            rows.append(
                GridRow(
                    docket_number=docket,
                    court_type=cells[3] if len(cells) > 3 else "",
                    case_caption=cells[4] if len(cells) > 4 else "",
                    case_status=cells[5] if len(cells) > 5 else "",
                    filing_date=cells[6] if len(cells) > 6 else "",
                    county=cells[9] if len(cells) > 9 else "",
                    court_office=cells[10] if len(cells) > 10 else "",
                    detail_url=urljoin(ORIGIN, entry["docketSheet"]) if entry["docketSheet"] else None,
                    summary_url=urljoin(ORIGIN, entry["courtSummary"]) if entry["courtSummary"] else None,
                )
            )
        return rows, notice, body

    async def _wait_for_grid(self, page) -> int:
        """Wait until the row count stops changing; return the settled count."""
        previous = -1
        stable = 0
        count = 0
        for _ in range(GRID_SETTLE_POLLS):
            await page.wait_for_timeout(int(GRID_SETTLE_SECONDS * 1000))
            count = await page.locator(GRID_ROWS).count()
            stable = stable + 1 if count == previous and count > 0 else 0
            previous = count
            if stable >= GRID_STABLE_POLLS:
                break
        return count

    async def search_partition(
        self,
        filing_date: date,
        county: str | None = None,
        court_office: str | None = None,
    ) -> PartitionResult:
        page = await self._ensure_page()
        result = PartitionResult(filing_date=filing_date, county=county, court_office=court_office)
        try:
            await self._prepare_date_form(page, county)
            if court_office:
                await page.locator(COURT_OFFICE_SELECTOR).select_option(court_office)
            await page.locator(DATE_FROM_INPUT).fill(filing_date.isoformat())
            await page.locator(DATE_TO_INPUT).fill(filing_date.isoformat())
            await page.locator(SEARCH_BUTTON).click()
            result.pages_visited = 1
            # An office with no filings that day renders an empty grid, not a
            # failure. Wait for either a populated grid or the portal's own
            # no-results message, then let the row count settle.
            await page.wait_for_function(
                """
                selector => {
                    const body = document.body ? document.body.innerText : '';
                    if (/no results/i.test(body)) return true;
                    const table = document.querySelector(selector);
                    return !!(table && table.querySelector('tbody tr'));
                }
                """,
                arg=RESULT_GRID,
                timeout=DEFAULT_TIMEOUT_MS,
            )
            await self._wait_for_grid(page)
            rows, notice, body = await self._read_grid(page)
        except PortalStructureError:
            raise
        except Exception as exc:
            detail = redact(str(exc), self.settings)
            result.errors.append(f"{classify_error(exc)}: {detail[:300]}")
            result.coverage_status = COVERAGE_FAILED
            await self._drop_session()
            return result

        if "no results" in body.casefold() and not rows:
            result.coverage_status = COVERAGE_ZERO
            return result
        result.rows = rows
        result.truncated_notice = notice
        if notice:
            result.notice_courts = _notice_courts(notice)
            result.coverage_status = COVERAGE_TRUNCATED
        if not rows:
            result.coverage_status = COVERAGE_ZERO
            # An empty grid without a zero-result message is a page failure, not
            # an empty day; keep the page text as evidence instead of guessing.
            if "no results" not in body.casefold():
                result.coverage_status = COVERAGE_FAILED
                result.errors.append(
                    "Grid rendered no rows and the page carried no zero-result message: "
                    + " ".join(body.split())[:300]
                )
        return result

    @staticmethod
    def _normalize_docket_number(value: str) -> str:
        return " ".join(value.upper().split())

    async def lookup_docket(self, docket_number: str) -> DocketLookup:
        """Search by docket number and return the portal's own row for it.

        This is the re-entry point for a known docket: it produces a fresh signed
        report URL, which a stored URL cannot be relied on to provide later.
        """
        await self._require_ready()
        target = self._normalize_docket_number(docket_number)
        lookup = DocketLookup(docket_number=target)
        page = await self._ensure_page()
        try:
            await page.locator(SEARCH_MODE_SELECTOR).select_option(DOCKET_NUMBER_OPTION)
            await page.wait_for_timeout(1_000)
            field = page.locator(DOCKET_NUMBER_INPUT)
            await field.wait_for(state="visible", timeout=20_000)
            await field.fill(target)
            await page.locator(SEARCH_BUTTON).click()
            lookup.pages_visited = 1
            await page.wait_for_function(
                """
                selector => {
                    const body = document.body ? document.body.innerText : '';
                    if (/no results/i.test(body)) return true;
                    const table = document.querySelector(selector);
                    return !!(table && table.querySelector('tbody tr'));
                }
                """,
                arg=RESULT_GRID,
                timeout=DEFAULT_TIMEOUT_MS,
            )
            await self._wait_for_grid(page)
            rows, notice, body = await self._read_grid(page)
        except Exception as exc:
            detail = redact(str(exc), self.settings)
            lookup.errors.append(f"{classify_error(exc)}: {detail[:300]}")
            lookup.coverage_status = COVERAGE_FAILED
            await self._drop_session()
            return lookup

        lookup.rows = rows
        lookup.notice = notice
        lookup.body_excerpt = " ".join(body.split())[:500]
        match = next(
            (
                row
                for row in rows
                if self._normalize_docket_number(row.docket_number) == target
            ),
            None,
        )
        if match is None:
            if not rows or "no results" in body.casefold():
                lookup.coverage_status = COVERAGE_ZERO
            else:
                lookup.coverage_status = COVERAGE_FAILED
                lookup.errors.append(
                    "Search returned rows but none matched the requested docket number"
                )
            return lookup
        lookup.row = match
        lookup.found = True
        lookup.coverage_status = COVERAGE_COMPLETE
        return lookup

    @staticmethod
    def _dedupe(results: Iterable[PartitionResult]) -> list[GridRow]:
        seen: dict[str, GridRow] = {}
        for result in results:
            for row in result.criminal_rows:
                seen.setdefault(_docket_sort_key(row.docket_number), row)
        return sorted(seen.values(), key=lambda row: _docket_sort_key(row.docket_number))

    async def iter_partitions(
        self,
        date_from: date,
        date_to: date,
        counties: Iterable[str] | None = None,
        *,
        max_partitions: int | None = None,
        skip: set[tuple[date, str]] | None = None,
    ) -> AsyncIterator[PartitionResult]:
        """Yield one result set per (day, county), recovering truncated MDJ days.

        ``skip`` names (day, county) pairs already collected in a prior run. A
        skipped pair is never searched, so extending a date range costs only the
        new dates.
        """
        counties = list(counties or [])
        if not counties:
            page = await self._ensure_page()
            await page.locator(SEARCH_MODE_SELECTOR).select_option(DATE_FIELD_OPTION)
            await page.wait_for_timeout(1_000)
            await page.locator(ADVANCED_SEARCH).evaluate("element => element.click()")
            await page.locator(COUNTY_SELECTOR).wait_for(state="visible", timeout=20_000)
            counties = [
                value
                for value in await page.locator(COUNTY_SELECTOR).evaluate(
                    "select => [...select.options].map(option => option.value).filter(Boolean)"
                )
            ]
        if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"Date range exceeds the portal limit of {MAX_RANGE_DAYS} days")

        emitted = 0
        day = date_from
        while day <= date_to:
            for county in counties:
                if max_partitions is not None and emitted >= max_partitions:
                    return
                if skip and (day, county) in skip:
                    continue
                base = await self.search_partition(day, county)
                if not base.truncated:
                    emitted += 1
                    yield base
                    continue

                if "Magisterial District" not in base.notice_courts:
                    emitted += 1
                    yield base
                    continue

                offices = await self.court_offices(county)
                merged: list[PartitionResult] = [base]
                for office in offices:
                    if max_partitions is not None and emitted + 1 >= max_partitions:
                        break
                    office_result = await self.search_partition(day, county, office)
                    merged.append(office_result)
                    if office_result.truncated:
                        base.errors.append(
                            f"Court office {office} still exceeded the result cap for {day.isoformat()}"
                        )
                base.rows = self._dedupe(merged)
                base.errors.extend(
                    sorted(
                        {
                            error
                            for result in merged[1:]
                            for error in result.errors
                        }
                    )
                )
                # Status is decided after the per-office evidence is merged in:
                # a timeout or a still-truncated office is a coverage failure and
                # must not be reported as COMPLETE.
                if "Common Pleas" in base.notice_courts:
                    base.coverage_status = COVERAGE_TRUNCATED
                    base.errors.append(
                        f"Common Pleas results for {county} on {day.isoformat()} exceeded the "
                        "portal result cap and cannot be subdivided by court office"
                    )
                elif any(result.coverage_status == COVERAGE_FAILED for result in merged[1:]):
                    base.coverage_status = COVERAGE_FAILED
                elif base.errors:
                    base.coverage_status = COVERAGE_TRUNCATED
                else:
                    base.coverage_status = COVERAGE_COMPLETE
                emitted += 1
                yield base
            day += timedelta(days=1)

    async def search(self, query: SearchQuery) -> SearchResult:
        await self._require_ready()
        if query.docket_number:
            lookup = await self.lookup_docket(query.docket_number)
            row = lookup.row
            dockets = (
                [
                    DocketRef(
                        docket_number=row.docket_number,
                        court_type=row.court_type or None,
                        county=row.county or None,
                        filing_date=parse_us_date(row.filing_date),
                        detail_url=row.detail_url,
                    )
                ]
                if row is not None
                else []
            )
            return SearchResult(
                dockets=dockets,
                pages_visited=lookup.pages_visited,
                pagination_complete=lookup.found,
                coverage_status=lookup.coverage_status,
                errors=lookup.errors,
            )
        if not query.date_from:
            raise LiveAccessNotValidatedError("A filing date is required.")
        date_to = query.date_to or query.date_from
        counties = [query.county] if query.county else []
        dockets: list[DocketRef] = []
        errors: list[str] = []
        pages = 0
        complete = True
        status = COVERAGE_COMPLETE
        async for partition in self.iter_partitions(query.date_from, date_to, counties):
            pages += partition.pages_visited
            errors.extend(partition.errors)
            if partition.coverage_status != COVERAGE_COMPLETE:
                complete = False
                if partition.coverage_status == COVERAGE_FAILED:
                    status = COVERAGE_FAILED
            for row in partition.criminal_rows:
                dockets.append(
                    DocketRef(
                        docket_number=row.docket_number,
                        court_type=row.court_type or None,
                        county=row.county or partition.county,
                        filing_date=partition.filing_date,
                        detail_url=row.detail_url,
                    )
                )
        return SearchResult(
            dockets=dockets,
            pages_visited=pages,
            pagination_complete=complete,
            coverage_status=status,
            errors=errors,
        )

    # ---------------------------------------------------------------- document

    async def get_docket(self, ref: DocketRef) -> DocketDetail:
        await self._require_ready()
        return DocketDetail(
            docket=ref,
            document_url=ref.detail_url,
            metadata={"court_type": ref.court_type, "county": ref.county},
        )

    async def get_document(self, ref: DocketRef) -> DocumentResult:
        await self._require_ready()
        if not ref.detail_url:
            raise PortalStructureError(f"No docket sheet URL recorded for {ref.docket_number}")
        page = await self._ensure_page()
        await self._limiter.wait()
        status, content_type, data = await self._fetch_report(page, ref)

        if status == 401:
            raise PortalStructureError(
                "Docket sheet denied with 401; the portal is fingerprinting this session"
            )
        if status != 200 or not data.startswith(b"%PDF"):
            raise PortalStructureError(
                f"Unexpected docket delivery: HTTP {status}, content-type {content_type!r}"
            )
        try:
            validate_pdf_bytes(data)
        except InvalidPdfError as exc:
            raise PortalStructureError(f"Rejected PDF for {ref.docket_number}: {exc}") from exc
        digest, path = store_pdf(data, self.settings.storage_dir)
        from datetime import UTC, datetime

        return DocumentResult(
            docket_number=ref.docket_number,
            source_url=ref.detail_url,
            storage_path=str(path),
            document_hash=digest,
            byte_count=len(data),
            retrieved_at=datetime.now(UTC).isoformat(),
        )

    async def _fetch_report(self, page, ref: DocketRef) -> tuple[int, str, bytes]:
        """Fetch one report inside the issuing session, riding out dead tunnels.

        A proxy tunnel can drop between the grid search and the document fetch.
        The signed ``dnh`` token is bound to the exit that issued it, so the fix
        is to retry on the same context, not to open a new session. After the
        last attempt the docket is failed and named; it never aborts the rest of
        the partition.
        """
        last_error: Exception | None = None
        for attempt in range(1, REPORT_FETCH_ATTEMPTS + 1):
            try:
                response = await page.context.request.get(
                    ref.detail_url, timeout=DEFAULT_TIMEOUT_MS
                )
                return (
                    response.status,
                    response.headers.get("content-type", ""),
                    await response.body(),
                )
            except PlaywrightError as exc:
                last_error = exc
                if attempt == REPORT_FETCH_ATTEMPTS:
                    break
                await asyncio.sleep(REPORT_FETCH_BACKOFF_SECONDS * attempt)
        raise PortalUnreachableError(
            f"DOCUMENT_FETCH_FAILED after {REPORT_FETCH_ATTEMPTS} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error


__all__ = [
    "COVERAGE_COMPLETE",
    "COVERAGE_FAILED",
    "COVERAGE_TRUNCATED",
    "COVERAGE_ZERO",
    "GridRow",
    "LiveAccessNotValidatedError",
    "PartitionResult",
    "PortalStructureError",
    "PortalUnreachableError",
    "UJSPortalSource",
]
