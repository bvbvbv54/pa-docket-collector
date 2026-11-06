"""Bounded form discovery with access-terms evidence and no search submission."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from pa_docket.config import Settings
from pa_docket.network import RequestLimiter, browser_proxy, classify_error
from pa_docket.sources.ujs.access import CASE_SEARCH_URL


async def main():
    settings = Settings.from_env()
    root = Path(__file__).resolve().parents[1] / "docs/validation"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report = {"timestamp": stamp, "search_submitted": False, "pdf_count": 0, "status": "STARTED"}
    limiter = RequestLimiter(10)
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, proxy=browser_proxy(settings), env=env)
        try:
            context = await browser.new_context(service_workers="block", ignore_https_errors=False)

            async def route_request(route):
                if (urlsplit(route.request.url).hostname != urlsplit(CASE_SEARCH_URL).hostname
                        or route.request.resource_type in {"image", "font", "media"}):
                    await route.abort()
                    return
                await limiter.wait()
                await route.continue_()

            await context.route("**/*", route_request)
            page = await context.new_page()
            await page.goto(CASE_SEARCH_URL, wait_until="domcontentloaded", timeout=240000)
            body = (await page.locator("body").inner_text()).lower()
            if any(x in body for x in ("access denied", "captcha", "verify you are human")):
                report["status"] = "BLOCKED"
                return
            await page.locator('select[title="Search By"]').select_option("DateFiled")
            await page.locator("#btnSearch").wait_for()
            await page.locator('input[name="AdvanceSearch"]').check()
            await page.locator('select[title="County"]').select_option("Adams")
            await page.locator('input[name="FiledStartDate"]').fill("2026-11-05")
            await page.locator('input[name="FiledEndDate"]').fill("2026-11-05")
            report["search_parameters"] = {"date_from": "2026-11-05", "date_to": "2026-11-05", "county": "Adams"}
            report["search_submitted"] = True
            await page.locator("#btnSearch").click()
            await page.wait_for_timeout(15000)
            report["status"] = "SEARCH_RESPONSE_REVIEW_REQUIRED"
            body = (await page.locator("body").inner_text()).lower()
            if any(x in body for x in ("captcha", "verify you are human", "access denied")):
                report["status"] = "BLOCKED"
            report["links"] = await page.locator("a:visible").evaluate_all(
                "els=>els.map(e=>({text:e.innerText,href:e.getAttribute('href')}))")
            report["tables"] = await page.locator("table").evaluate_all(
                "els=>els.map(e=>({id:e.id,rows:e.rows.length,headers:[...e.querySelectorAll('th')].map(x=>x.innerText)}))")
            report["form_text"] = await page.locator("body").inner_text()
            await page.screenshot(path=str(root / f"ujs_date_form_{stamp}.png"), full_page=True)
            controls = page.locator("select:visible, input:visible, button:visible")
            report["visible_controls"] = await controls.evaluate_all(
                "els=>els.map(e=>({tag:e.tagName,id:e.id,name:e.name,outer:e.outerHTML.slice(0,1500),"
                "options:e.tagName==='SELECT'?[...e.options].map(o=>({text:o.text,value:o.value})):[]}))"
            )
        except Exception as exc:
            report["status"] = classify_error(exc)
        finally:
            await browser.close()
            path = root / f"ujs_workflow_{stamp}.json"
            path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({"status": report["status"], "evidence": str(path)}))


if __name__ == "__main__":
    asyncio.run(main())
