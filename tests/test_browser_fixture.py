import pytest
from playwright.async_api import async_playwright

from scripts.inspect_ujs_page import search_interface_visible


@pytest.mark.asyncio
async def test_real_chromium_local_render_detection():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('<title>Case Search</title><select><option>Date Filed</option></select>'
                                   '<button id="btnSearch">Search</button><button id="btnReset">Clear</button>')
            assert await search_interface_visible(page)
            await page.set_content('<title>Case Search</title><body>Access denied</body>')
            assert not await search_interface_visible(page)
            await page.set_content('<title>Case Search</title><body></body>')
            assert not await search_interface_visible(page)
        finally:
            await browser.close()
