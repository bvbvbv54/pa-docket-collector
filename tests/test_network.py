from dataclasses import replace
from urllib.parse import quote

import pytest

from pa_docket.config import Settings
from pa_docket.network import NetworkConfigurationError, browser_proxy, classify_error, redact
from scripts.verify_proxy_exit import verify


def configured():
    return Settings(network_mode="proxy", proxy_server="gw.dataimpulse.com:823",
                    proxy_username="mock-user", proxy_password="mock/p@ss")


def test_proxy_configuration():
    proxy = browser_proxy(configured())
    assert proxy == {"server": "http://gw.dataimpulse.com:823",
                     "username": "mock-user__cr.us", "password": "mock/p@ss"}


def test_direct_ignores_proxy():
    assert browser_proxy(replace(configured(), network_mode="direct")) is None


@pytest.mark.parametrize("field,key", [("proxy_server", "PROXY_SERVER"),
                                      ("proxy_username", "PROXY_USERNAME"),
                                      ("proxy_password", "PROXY_PASSWORD")])
def test_missing_configuration(field, key):
    with pytest.raises(NetworkConfigurationError, match=key):
        browser_proxy(replace(configured(), **{field: None}))


@pytest.mark.parametrize("server", ["http://user:secret@host:823", "host:bad", "host:99999",
                                    "socks5://host:823", "http://host:823/path"])
def test_invalid_server(server):
    with pytest.raises(NetworkConfigurationError, match="INVALID_PROXY_SERVER"):
        browser_proxy(replace(configured(), proxy_server=server))


def test_invalid_mode():
    with pytest.raises(NetworkConfigurationError, match="INVALID_NETWORK_MODE"):
        browser_proxy(Settings(network_mode="automatic"))


def test_secret_redaction():
    settings = configured()
    message = f"{settings.proxy_username} {settings.proxy_password} {quote(settings.proxy_password, safe='')}"
    assert redact(message, settings) == "[REDACTED] [REDACTED] [REDACTED]"
    assert "mock-user" not in repr(settings)
    assert "mock/p@ss" not in repr(settings)
    assert "other:secret" not in redact("http://other:secret@host:823", settings)


def test_environment(monkeypatch):
    monkeypatch.setenv("NETWORK_MODE", "PROXY")
    monkeypatch.setenv("PROXY_SERVER", "gw.dataimpulse.com:823")
    monkeypatch.setenv("PROXY_USERNAME", "mock-user")
    monkeypatch.setenv("PROXY_PASSWORD", "mock-pass")
    assert browser_proxy(Settings.from_env())["username"] == "mock-user__cr.us"


@pytest.mark.asyncio
async def test_verifier_fails_closed():
    result = await verify(Settings(network_mode="proxy"))
    assert result["status"].startswith("MISSING_CONFIGURATION:")
    assert result["country"] is None
    assert (await verify(Settings()))["status"] == "PROXY_NOT_SELECTED"


@pytest.mark.parametrize("message,status", [("ERR_INVALID_AUTH_CREDENTIALS", "PROXY_AUTH_ERROR"),
                                           ("ERR_NAME_NOT_RESOLVED", "DNS_ERROR"),
                                           ("ERR_CERT_AUTHORITY_INVALID", "TLS_ERROR"),
                                           ("Timeout 30000ms", "TIMEOUT"),
                                           ("secret", "NETWORK_CONNECTIVITY_FAILURE")])
def test_sanitized_error(message, status):
    assert classify_error(RuntimeError(message)) == status


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_mock_browser_cleanup(monkeypatch, failure):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import playwright.async_api

    page = MagicMock()
    page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
    page.locator.return_value.inner_text = AsyncMock(return_value='{"country":"US","region":"Virginia"}')
    if failure:
        page.goto.side_effect = RuntimeError("ERR_CERT_AUTHORITY_INVALID mock/p@ss")
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    launch = AsyncMock(return_value=browser)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=SimpleNamespace(chromium=SimpleNamespace(launch=launch)))
    manager.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: manager)
    result = await verify(configured())
    assert result["status"] == ("TLS_ERROR" if failure else "PASS")
    assert "mock/p@ss" not in str(result)
    browser.close.assert_awaited_once()
    assert launch.call_args.kwargs["proxy"] == browser_proxy(configured())
    browser.new_context.assert_awaited_once_with(ignore_https_errors=False)


def test_rate_alias_clamped(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_RPM", "50")
    assert Settings.from_env().requests_per_minute == 10


@pytest.mark.asyncio
async def test_request_rate_limit():
    from pa_docket.network import RequestLimiter

    now = [0.0]
    delays = []

    async def sleep(seconds):
        delays.append(seconds)
        now[0] += seconds

    limiter = RequestLimiter(100, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        await limiter.wait()
    assert delays == [6.0, 6.0]
