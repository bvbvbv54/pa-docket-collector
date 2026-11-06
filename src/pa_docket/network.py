"""Explicit browser networking; never fall back from proxy to direct."""
from __future__ import annotations

import asyncio
import re
import time
from secrets import token_hex
from urllib.parse import quote, urlsplit

from pa_docket.config import Settings


class RequestLimiter:
    """Serialize request starts; one instance must be shared by a browser job."""

    def __init__(self, rpm: int = 10, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.interval = 60 / min(10, max(1, rpm))
        self.clock, self.sleep = clock, sleep
        self.next_start = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            delay = self.next_start - self.clock()
            if delay > 0:
                await self.sleep(delay)
            self.next_start = self.clock() + self.interval


class NetworkConfigurationError(ValueError):
    """Messages contain configuration keys only, never values."""


def new_proxy_session_id(prefix: str = "pdc") -> str:
    """Short sticky-session tag; provider keeps one exit IP per tag."""
    return prefix + "-" + token_hex(6)


def browser_proxy(settings: Settings, session_id: str | None = None) -> dict[str, str] | None:
    mode = settings.network_mode.strip().lower()
    if mode not in {"direct", "proxy"}:
        raise NetworkConfigurationError("INVALID_NETWORK_MODE")
    if mode == "direct":
        return None
    missing = [key for key, value in (
        ("PROXY_SERVER", settings.proxy_server),
        ("PROXY_USERNAME", settings.proxy_username),
        ("PROXY_PASSWORD", settings.proxy_password),
    ) if not value or not value.strip()]
    if missing:
        raise NetworkConfigurationError("MISSING_CONFIGURATION:" + ",".join(missing))
    server = settings.proxy_server.strip()
    if "://" not in server:
        server = "http://" + server
    try:
        parsed = urlsplit(server)
        valid = (
            parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port
            and parsed.username is None and parsed.password is None
            and not parsed.path and not parsed.query and not parsed.fragment
            and not any(c.isspace() for c in server)
        )
    except ValueError:
        valid = False
    if not valid:
        raise NetworkConfigurationError("INVALID_PROXY_SERVER")
    if not re.fullmatch(r"[A-Z]{2}", settings.proxy_country):
        raise NetworkConfigurationError("INVALID_PROXY_COUNTRY")
    username = settings.proxy_username
    # Official provider syntax: https://docs.dataimpulse.com/proxies/parameters/state
    if parsed.hostname == "gw.dataimpulse.com":
        if "__cr." not in username:
            username += "__cr." + settings.proxy_country.lower()
        if session_id and ";sessid." not in username:
            username += ";sessid." + session_id
    return {"server": server, "username": username, "password": settings.proxy_password}


def redact(message: str, settings: Settings) -> str:
    for secret in sorted(filter(None, (settings.proxy_username, settings.proxy_password)), key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]").replace(quote(secret, safe=""), "[REDACTED]")
    return re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", message)


def classify_error(exc: Exception) -> str:
    text = str(exc).upper()
    for markers, status in (
        (("ERR_PROXY_AUTH", "ERR_INVALID_AUTH_CREDENTIALS", "407"), "PROXY_AUTH_ERROR"),
        (("ERR_NAME_NOT_RESOLVED", "ENOTFOUND"), "DNS_ERROR"),
        (("ERR_CERT", "ERR_SSL", "CERTIFICATE"), "TLS_ERROR"),
        (("TIMEOUT", "TIMED OUT"), "TIMEOUT"),
        (("EXECUTABLE DOESN'T EXIST", "BROWSER CLOSED"), "BROWSER_CONFIGURATION_ERROR"),
        (("ERR_PROXY_CONNECTION_FAILED",), "PROXY_CONNECTION_FAILED"),
    ):
        if any(marker in text for marker in markers):
            return status
    return "NETWORK_CONNECTIVITY_FAILURE"
