"""Retrieve one observed criminal docket link; stop on non-PDF or denial."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.async_api import async_playwright

from pa_docket.config import Settings
from pa_docket.documents.storage import store_pdf
from pa_docket.network import RequestLimiter, browser_proxy, classify_error
from pa_docket.parsers import GenericParser
from pa_docket.sources.ujs.access import CASE_SEARCH_URL


async def main():
    settings = Settings.from_env()
    root = Path(__file__).resolve().parents[1]
    discovery = json.loads((root / "docs/validation/ujs_workflow_20261106_145249.json").read_text())
    links = [x for x in discovery["links"] if x["text"] == "Docket Sheet" and "-CR-" in (x["href"] or "")]
    report = {"status": "NOT_RETRIEVED", "retrieved": 0, "failed": 0, "documents": []}
    limiter = RequestLimiter(10)
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, proxy=browser_proxy(settings), env=env)
        try:
            context = await browser.new_context(ignore_https_errors=False)
            for link in links[:1]:
                url = urljoin(CASE_SEARCH_URL, link["href"])
                await limiter.wait()
                response = await context.request.get(url, timeout=60000, max_redirects=0)
                data = await response.body()
                report["http_status"] = response.status
                if response.status in {401, 403, 429, 451}:
                    report["status"] = "BLOCKED"
                    report["failed"] += 1
                    break
                if response.status != 200 or not data.startswith(b"%PDF-"):
                    report["status"] = "NON_PDF"
                    report["failed"] += 1
                    # Only retain text for access/error diagnosis, never cookies or headers.
                    (root / "data/ujs_document_error.html").write_bytes(data)
                    break
                digest, path = store_pdf(data, settings.storage_dir)
                record = GenericParser().parse(data, source_url=url)
                parsed = settings.storage_dir / f"{digest}.parsed.json"
                parsed.write_text(record.model_dump_json(indent=2), encoding="utf-8")
                report["documents"].append({"sha256": digest, "bytes": len(data), "path": str(path.resolve()),
                                            "retrieved_at": datetime.now(UTC).isoformat(),
                                            "source_path": urlsplit(url).path,
                                            "parser_status": str(record.validation_status)})
                report["retrieved"] += 1
                report["status"] = "PDF_STORED"
        except Exception as exc:
            report["status"] = classify_error(exc)
        finally:
            await browser.close()
            path = root / "docs/validation/ujs_retrieval_sample.json"
            path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
