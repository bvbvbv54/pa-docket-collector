from __future__ import annotations

import asyncio
from datetime import date

import pytest

from pa_docket.config import Settings
from pa_docket.sources.base import SearchQuery
from pa_docket.sources.ujs.access import CASE_SEARCH_URL, probe_direct
from pa_docket.sources.ujs.source import (
    LiveAccessNotValidatedError,
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


def test_ujs_source_reports_proxy_route_as_configured(tmp_path):
    settings = Settings(
        network_mode="proxy",
        storage_dir=tmp_path,
        proxy_server="http://gw.dataimpulse.com:823",
        proxy_username="user",
        proxy_password="secret",
    )
    result = asyncio.run(UJSPortalSource(settings).check_access())
    assert result.status == "CONFIGURED"
    assert "page reachability is proven" in result.detail


def test_ujs_source_blocks_on_incomplete_proxy_configuration(tmp_path):
    settings = Settings(network_mode="proxy", storage_dir=tmp_path)
    result = asyncio.run(UJSPortalSource(settings).check_access())
    assert result.status == "BLOCKED"
    assert "Proxy configuration rejected" in result.detail


def test_live_search_is_refused_until_the_recorded_evidence_is_complete(tmp_path):
    settings = Settings(
        network_mode="proxy",
        storage_dir=tmp_path,
        live_collection_enabled=True,
        live_access_approved=True,
        proxy_server="http://gw.dataimpulse.com:823",
        proxy_username="user",
        proxy_password="secret",
    )
    with pytest.raises(LiveAccessNotValidatedError, match="validation evidence is incomplete"):
        asyncio.run(
            UJSPortalSource(settings).search(SearchQuery(date_from=date(2026, 11, 5)))
        )


def test_live_operations_stay_disabled_without_operator_approval(tmp_path):
    settings = Settings(network_mode="direct", storage_dir=tmp_path)
    with pytest.raises(LiveAccessNotValidatedError, match="Live collection is disabled"):
        asyncio.run(
            UJSPortalSource(settings).search(SearchQuery(date_from=date(2026, 11, 5)))
        )
