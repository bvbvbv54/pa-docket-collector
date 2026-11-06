from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from pa_docket.config import Settings
from pa_docket.sources.base import AccessStatus, SearchQuery
from pa_docket.sources.ujs import source as ujs_source
from pa_docket.sources.ujs.access import CASE_SEARCH_URL, probe_direct
from pa_docket.sources.ujs.source import (
    LiveAccessNotValidatedError,
    PortalUnreachableError,
    UJSPortalSource,
)


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return b"<html></html>"

    def geturl(self):
        return CASE_SEARCH_URL


class FakeOpener:
    def __init__(self, handler):
        self.handler = handler

    def open(self, request, timeout):
        assert request.get_full_url() == CASE_SEARCH_URL
        assert timeout == 1.0
        return FakeResponse()


def test_access_probe_forces_empty_proxy_map(monkeypatch):
    seen = {}

    def build_opener(handler):
        seen["proxy_map"] = handler.proxies
        return FakeOpener(handler)

    monkeypatch.setattr("pa_docket.sources.ujs.access.build_opener", build_opener)
    result = probe_direct(timeout=1.0)
    assert seen["proxy_map"] == {}
    assert result.status == "ACCESSIBLE"
    assert result.http_status == 200


def test_ujs_source_rejects_proxy_mode_without_network_access(tmp_path, monkeypatch):
    settings = Settings(network_mode="proxy", storage_dir=tmp_path)
    source = UJSPortalSource(settings)
    result = asyncio.run(source.check_access())
    assert result.status == "BLOCKED"
    assert "direct connections only" in result.detail


def test_ujs_operations_fail_with_clear_guidance_when_unreachable(tmp_path, monkeypatch):
    settings = Settings(network_mode="direct", storage_dir=tmp_path)
    source = UJSPortalSource(settings)
    monkeypatch.setattr(
        ujs_source,
        "probe_direct",
        lambda *_args: AccessStatus(
            status="UNREACHABLE",
            checked_at=datetime.now(UTC).isoformat(),
            url=CASE_SEARCH_URL,
            detail="direct route unavailable",
        ),
    )
    with pytest.raises(PortalUnreachableError, match="deploy only from a network permitted"):
        asyncio.run(source.search(SearchQuery(docket_number="not-a-real-docket")))


def test_reachability_alone_does_not_enable_search(tmp_path, monkeypatch):
    settings = Settings(network_mode="direct", storage_dir=tmp_path)
    source = UJSPortalSource(settings)
    monkeypatch.setattr(
        ujs_source,
        "probe_direct",
        lambda *_args: AccessStatus(
            status="ACCESSIBLE",
            checked_at=datetime.now(UTC).isoformat(),
            url=CASE_SEARCH_URL,
            http_status=200,
        ),
    )
    with pytest.raises(LiveAccessNotValidatedError, match="Live collection remains disabled"):
        asyncio.run(source.search(SearchQuery(docket_number="not-a-real-docket")))
