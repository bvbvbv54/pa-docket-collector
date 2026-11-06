"""Download a small set of real docket PDFs for parser development.

Usage: python scripts/fetch_ujs_samples.py <county> <date_from> <date_to> <max_per_kind>
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

import pymupdf as fitz
from playwright.async_api import async_playwright

from pa_docket.config import Settings
from pa_docket.network import browser_proxy
from pa_docket.sources.ujs.access import CASE_SEARCH_URL

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")


async def main(county: str, date_from: str, date_to: str, max_per_kind: int) -> None:
    s = Settings.from_env()
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    out = Path("data/samples")
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, proxy=browser_proxy(s, "samples"), env=env)
        try:
            c = await b.new_context(
                ignore_https_errors=False, user_agent=UA, locale="en-US",
                timezone_id="America/New_York", viewport={"width": 1440, "height": 1000},
            )
            p = await c.new_page()
            await p.goto(CASE_SEARCH_URL, wait_until="domcontentloaded", timeout=90000)
            await p.locator('select[title="Search By"]').select_option("DateFiled")
            await p.wait_for_timeout(1200)
            await p.locator('input[name="AdvanceSearch"]').evaluate("e => e.click()")
            await p.locator('select[title="County"]').select_option(county)
            await p.locator('input[name="FiledStartDate"]').fill(date_from)
            await p.locator('input[name="FiledEndDate"]').fill(date_to)
            await p.locator("#btnSearch").click()
            await p.wait_for_selector("#caseSearchResultGrid a", timeout=90000)

            hrefs = await p.locator("#caseSearchResultGrid a", has_text="Docket Sheet").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))"
            )
            by_kind: dict[str, list[str]] = {"CP": [], "MJ": [], "MC": []}
            for href in hrefs:
                docket = href.split("docketNumber=")[-1].split("&")[0]
                prefix = docket.split("-")[0]
                if prefix in by_kind and len(by_kind[prefix]) < max_per_kind:
                    by_kind[prefix].append(href)
            print({k: len(v) for k, v in by_kind.items()}, "total rows:", len(hrefs))

            for kind, items in by_kind.items():
                for href in items:
                    url = "https://ujsportal.pacourts.us" + href
                    resp = await c.request.get(url, timeout=60000)
                    data = await resp.body()
                    if resp.status != 200 or not data.startswith(b"%PDF"):
                        print(kind, "FAILED", resp.status, resp.headers.get("content-type"))
                        continue
                    digest = hashlib.sha256(data).hexdigest()[:16]
                    pdf_path = out / f"{kind.lower()}_{county.lower()}_{digest}.pdf"
                    pdf_path.write_bytes(data)
                    with fitz.open(stream=data, filetype="pdf") as pdf:
                        text = "\n".join(page.get_text("text", sort=True) for page in pdf)
                        pages = pdf.page_count
                    (out / f"{kind.lower()}_{county.lower()}_{digest}.txt").write_text(text, encoding="utf-8")
                    print(f"{kind} {pages}p {len(data)}B {pdf_path.name} | race={'Race:' in text} "
                          f"officer={'Arresting Officer' in text} disposition={'DISPOSITION' in text}")
        finally:
            await b.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 3))
