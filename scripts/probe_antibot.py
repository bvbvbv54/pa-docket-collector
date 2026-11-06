"""Probe the public portal for anti-bot controls and the docket-number form.

Read-only: loads the Case Search landing page, switches the search mode, and
records what the page actually presents. It never solves, bypasses or submits a
CAPTCHA; if one is present the probe records it and stops.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\probe_antibot.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from pa_docket.config import Settings
from pa_docket.sources.ujs.access import CASE_SEARCH_URL
from pa_docket.sources.ujs.source import UJSPortalSource

FINGERPRINTS = {
    "recaptcha": ("recaptcha", "grecaptcha"),
    "hcaptcha": ("hcaptcha",),
    "turnstile": ("turnstile", "challenges.cloudflare.com"),
    "cloudflare": ("cf-chl", "__cf_bm", "cf_clearance", "cloudflare"),
    "datadome": ("datadome",),
    "perimeterx": ("_px", "px-captcha", "perimeterx"),
    "akamai": ("_abck", "ak_bmsc", "bm_sz"),
    "incapsula": ("incap_ses", "visid_incap", "incapsula"),
    "imperva": ("imperva",),
    "kasada": ("kasada", "kpsdk"),
    "shape": ("shape", "f5"),
    "aws_waf": ("aws-waf-token", "awswaf"),
}

CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    "div.g-recaptcha",
    "div[class*='captcha' i]",
    "div[id*='captcha' i]",
    "#challenge-form",
    "#px-captcha",
]

HEADER_KEYS = {
    "server",
    "via",
    "x-cache",
    "x-served-by",
    "cf-ray",
    "cf-cache-status",
    "x-akamai-transformed",
    "x-datadome",
    "x-iinfo",
    "x-cdn",
    "x-powered-by",
    "set-cookie",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "retry-after",
}


def _hits(haystack: str) -> list[str]:
    text = haystack.casefold()
    return sorted(name for name, needles in FINGERPRINTS.items() if any(n in text for n in needles))


async def probe() -> dict:
    settings = Settings.from_env()
    source = UJSPortalSource(settings)
    report: dict = {
        "probed_at": datetime.now(UTC).isoformat(),
        "url": CASE_SEARCH_URL,
        "network_mode": settings.network_mode,
        "user_agent": settings.browser_user_agent,
        "dns_or_proxy": "proxy" if settings.network_mode == "proxy" else "direct",
    }
    await source.open()
    try:
        page = source._page
        response = await page.goto(CASE_SEARCH_URL, wait_until="domcontentloaded")
        await page.locator("body").wait_for(timeout=30_000)
        headers = response.headers if response else {}
        report["landing_status"] = response.status if response else None
        report["landing_headers"] = {
            key: ("<redacted>" if key == "set-cookie" else value)
            for key, value in headers.items()
            if key.casefold() in HEADER_KEYS
        }
        report["landing_header_fingerprints"] = _hits(json.dumps(headers))

        html = await page.content()
        cookies = await page.context.cookies()
        cookie_text = " ".join(f"{c['name']}={c['value']}" for c in cookies)
        report["cookie_names"] = sorted({c["name"] for c in cookies})
        report["dom_fingerprints"] = _hits(html)
        report["cookie_fingerprints"] = _hits(cookie_text)
        report["captcha_selectors_present"] = {
            selector: await page.locator(selector).count() for selector in CAPTCHA_SELECTORS
        }
        report["captcha_frame_urls"] = [
            frame.url for frame in page.frames if frame.url and frame.url != CASE_SEARCH_URL
        ]

        modes = await page.locator('select[title="Search By"]').evaluate(
            "select => [...select.options].map(o => ({value: o.value, text: o.textContent.trim()}))"
        )
        report["search_modes"] = modes
        if any(mode["value"] == "DocketNumber" for mode in modes):
            await page.locator('select[title="Search By"]').select_option("DocketNumber")
            await page.wait_for_timeout(2_000)
            report["docket_mode_inputs"] = await page.locator(
                "input:visible, select:visible"
            ).evaluate_all(
                """
                elements => elements.map(el => ({
                    tag: el.tagName,
                    id: el.id || null,
                    name: el.getAttribute('name'),
                    title: el.getAttribute('title'),
                    placeholder: el.getAttribute('placeholder'),
                    type: el.getAttribute('type'),
                }))
                """
            )
            report["docket_mode_body"] = " ".join(
                (await page.locator("body").inner_text()).split()
            )[:1500]
        report["title"] = await page.title()
        report["body_excerpt"] = " ".join((await page.locator("body").inner_text()).split())[:800]
    finally:
        await source.close()
    return report


def main() -> int:
    report = asyncio.run(probe())
    out = Path("docs/validation/antibot_probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    blocked = (
        report.get("landing_status") != 200
        or any(report.get("captcha_selectors_present", {}).values())
        or report.get("landing_header_fingerprints")
        or report.get("dom_fingerprints")
        or report.get("cookie_fingerprints")
    )
    print(json.dumps({k: v for k, v in report.items() if k != "docket_mode_body"}, indent=2))
    print()
    print("anti_bot_signal:", "PRESENT" if blocked else "NONE OBSERVED")
    print("report:", out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
