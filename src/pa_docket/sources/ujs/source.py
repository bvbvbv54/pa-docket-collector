from __future__ import annotations

import asyncio
import json

from pa_docket.config import Settings
from pa_docket.sources.base import AccessStatus, DocketDetail, DocketRef, DocumentResult, SearchQuery, SearchResult
from pa_docket.sources.ujs.access import CASE_SEARCH_URL, probe_direct


class PortalUnreachableError(RuntimeError):
    """The public Case Search page could not be reached from this network."""


class LiveAccessNotValidatedError(RuntimeError):
    """Reachability alone is insufficient to enable searches or document retrieval."""


class UJSPortalSource:
    """Direct-only UJS source scaffold; operations are gated pending public-flow validation."""

    def __init__(self, settings: Settings | None = None, *, timeout: float = 15.0):
        self.settings = settings or Settings.from_env()
        self.timeout = timeout

    async def check_access(self) -> AccessStatus:
        if self.settings.network_mode.casefold() != "direct":
            return AccessStatus(
                status="BLOCKED",
                checked_at="",
                url=CASE_SEARCH_URL,
                detail="UJSPortalSource permits direct connections only; proxy mode is disabled.",
            )
        return await asyncio.to_thread(probe_direct, CASE_SEARCH_URL, self.timeout)

    async def _require_ready(self) -> None:
        access = await self.check_access()
        if access.status != "ACCESSIBLE":
            raise PortalUnreachableError(
                f"UJS public Case Search is {access.status}: {access.detail or 'no additional details'}. "
                "Run scripts/validate_ujs_access.py and deploy only from a network permitted to access the page."
            )
        if not self.settings.live_ready:
            raise LiveAccessNotValidatedError(
                "Live collection remains disabled. Reachability does not approve automated searching."
            )
        findings_path = self.settings.storage_dir / "ujs_access_findings.json"
        try:
            findings = json.loads(findings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            findings = {}
        ready = all(
            findings.get(key) is True
            for key in ("selectors_verified", "search_flow_validated", "pdf_delivery_validated")
        )
        if not ready:
            raise LiveAccessNotValidatedError(
                "Search controls, pagination, and PDF delivery are not recorded as validated. "
                "Keep live collection disabled until those checks are completed through the public UI."
            )

    async def search(self, query: SearchQuery) -> SearchResult:
        await self._require_ready()
        raise LiveAccessNotValidatedError(
            "Search execution is intentionally unavailable until observed selectors are reviewed and implemented."
        )

    async def get_docket(self, ref: DocketRef) -> DocketDetail:
        await self._require_ready()
        raise LiveAccessNotValidatedError(
            "Docket navigation is intentionally unavailable until result and docket-sheet controls are validated."
        )

    async def get_document(self, ref: DocketRef) -> DocumentResult:
        await self._require_ready()
        raise LiveAccessNotValidatedError(
            "Document retrieval is intentionally unavailable until the public PDF delivery mechanism is validated."
        )
