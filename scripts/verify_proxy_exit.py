"""Check one neutral HTTPS endpoint without printing addresses or secrets."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pa_docket.config import Settings
from pa_docket.network import NetworkConfigurationError, browser_proxy, classify_error

ENDPOINT = "https://api.ipinfo.io/json"


async def verify(settings: Settings) -> dict:
    report = {"country": None, "region": None, "status": "UNKNOWN_FAILURE"}
    try:
        proxy = browser_proxy(settings)
    except NetworkConfigurationError as exc:
        report["status"] = str(exc)
        return report
    if proxy is None:
        report["status"] = "PROXY_NOT_SELECTED"
        return report
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        report["status"] = "PLAYWRIGHT_NOT_INSTALLED"
        return report
    try:
        async with async_playwright() as playwright:
            env = {k: v for k, v in os.environ.items()
                   if k.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
            browser = await playwright.chromium.launch(headless=True, proxy=proxy, env=env)
            try:
                context = await browser.new_context(ignore_https_errors=False)
                page = await context.new_page()
                response = await page.goto(ENDPOINT, wait_until="domcontentloaded", timeout=30_000)
                if response is not None and response.status == 404:
                    response = await page.goto(
                        "https://ipinfo.io/json", wait_until="domcontentloaded", timeout=30_000
                    )
                if response is None or response.status != 200:
                    report["status"] = (
                        "PROXY_AUTH_ERROR" if response and response.status == 407
                        else f"HTTP_{response.status}" if response else "NO_HTTP_RESPONSE"
                    )
                    return report
                try:
                    data = json.loads(await page.locator("body").inner_text(timeout=5000))
                    country, region = data.get("country"), data.get("region")
                    if not isinstance(country, str) or len(country) != 2 or not country.isalpha():
                        report["status"] = "INVALID_GEOLOCATION_RESPONSE"
                        return report
                    report.update(country=country.upper(), region=region if isinstance(region, str) else None)
                    report["status"] = "PASS" if country.upper() == settings.proxy_country else "COUNTRY_MISMATCH"
                except (ValueError, AttributeError):
                    report["status"] = "INVALID_GEOLOCATION_RESPONSE"
            finally:
                await browser.close()
    except Exception as exc:
        report["status"] = classify_error(exc)
    return report


def main() -> int:
    report = asyncio.run(verify(Settings.from_env()))
    now = datetime.now(UTC)
    evidence = Path(__file__).resolve().parents[1] / "docs" / "validation"
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence / f"proxy_exit_{now:%Y%m%d_%H%M%S_%f}.json"
    path.write_text(json.dumps({**report, "timestamp": now.isoformat()}, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
