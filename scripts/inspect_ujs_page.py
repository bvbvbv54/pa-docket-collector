"""Read-only landing-page evidence; no searches or document retrieval."""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pa_docket.config import Settings
from pa_docket.network import NetworkConfigurationError, RequestLimiter, browser_proxy, classify_error
from pa_docket.sources.ujs.access import CASE_SEARCH_URL


async def search_interface_visible(page) -> bool:
    """Require the observed landing-page title, buttons and a visible search dropdown."""
    return (
        await page.title() == "Case Search"
        and await page.locator("#btnSearch").is_visible()
        and await page.locator("#btnReset").is_visible()
        and await page.locator("select:visible").count() > 0
    )


async def inspect(settings: Settings) -> dict:
    from playwright.async_api import async_playwright

    now = datetime.now(UTC)
    root = Path(__file__).resolve().parents[1] / "docs" / "validation"
    root.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    report = dict(timestamp=now.isoformat(), status="UNKNOWN_FAILURE", http_status=None,
                  final_url=None, title=None, rendered=False, components=[],
                  javascript_errors=[], network_errors=[], screenshot=None)
    start = time.monotonic()
    try:
        proxy = browser_proxy(settings)
        env = {k: v for k, v in os.environ.items()
               if k.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, proxy=proxy, env=env)
            try:
                context = await browser.new_context(ignore_https_errors=False, viewport={"width": 1280, "height": 900})
                limiter = RequestLimiter(settings.requests_per_minute)

                async def limit_request(route):
                    if urlsplit(route.request.url).hostname != urlsplit(CASE_SEARCH_URL).hostname:
                        await route.abort()
                        return
                    await limiter.wait()
                    await route.continue_()

                await context.route("**/*", limit_request)
                page = await context.new_page()
                page.on("pageerror", lambda _: report["javascript_errors"].append("JAVASCRIPT_ERROR"))
                page.on("requestfailed", lambda _: report["network_errors"].append("REQUEST_FAILED"))
                response = await page.goto(CASE_SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
                await page.locator("body").wait_for(timeout=10000)
                report["http_status"] = response.status if response else None
                parsed = urlsplit(page.url)
                report["final_url"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                report["title"] = await page.title()
                body = (await page.locator("body").inner_text()).lower()
                if report["http_status"] in {401, 403, 451} or "access denied" in body:
                    report["status"] = "ACCESS_DENIED"
                elif "captcha" in body or await page.locator('iframe[src*="captcha"]').count():
                    report["status"] = "CAPTCHA_PRESENT"
                elif any(x in body for x in ("verify you are human", "checking your browser", "just a moment")):
                    report["status"] = "BOT_PROTECTION"
                else:
                    report["components"] = await page.locator("select, input, button").evaluate_all(
                        "els => els.map(e => ({tag:e.tagName, id:e.id, name:e.name||null, "
                        "type:e.type||null, label:e.getAttribute('aria-label'), "
                        "visible:!!e.getClientRects().length, enabled:!e.disabled, "
                        "options:e.tagName==='SELECT'?[...e.options].map(o=>o.text):[], "
                        "confirmation:'DISCOVERY_REQUIRED'}))"
                    )
                    report["status"] = "INTERFACE_REVIEW_REQUIRED" if report["components"] else "APPLICATION_ERROR"
                    report["rendered"] = bool(body.strip())
                    if report["http_status"] == 200 and await search_interface_visible(page):
                        report["status"] = "ACCESSIBLE"
                shot = root / f"ujs_casesearch_{stamp}.png"
                await page.screenshot(path=str(shot), full_page=True)
                report["screenshot"] = str(shot)
            finally:
                await browser.close()
    except NetworkConfigurationError as exc:
        report["status"] = str(exc)
    except Exception as exc:
        report["status"] = classify_error(exc)
    report["navigation_seconds"] = round(time.monotonic() - start, 3)
    path = root / f"ujs_access_{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "components"}, indent=2))
    print(f"Evidence: {path}")
    return report


def main() -> int:
    report = asyncio.run(inspect(Settings.from_env()))
    return 0 if report["status"] == "ACCESSIBLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
