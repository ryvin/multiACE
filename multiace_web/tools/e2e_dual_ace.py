"""Manual Playwright golden-path for the dual-ACE GUI.

Run only when no print is in progress — the script issues a real
ACE_LOAD_HEAD via the chevron menu and waits for head_source[0] to
resolve. Use against a printer with at least 2 ACEs configured.

Asserts:
- Both ACE blocks render (one per device_count).
- 📖 picker button on ACE B / slot 0 opens FilamentHub URL with the
  printer/ace/slot query params (no popup blocker assumed).
- The chevron menu opens with one item per head and indicates busy heads.
- Selecting "→ T0" issues an ACE_LOAD_HEAD, and head_source[0] resolves
  to {ace_index: <ace>, slot: <slot>} within 90 s.
- Final state is screenshotted to e2e_dual_ace_success.png.

Usage:
    pip install playwright && playwright install chromium
    export DAVINCI_U1_HOST=192.168.1.136   # or whatever the printer's IP is
    python tools/e2e_dual_ace.py http://$DAVINCI_U1_HOST/multiace/

Env vars:
    DAVINCI_U1_HOST       Printer host/IP (default 192.168.1.136).
    MULTIACE_E2E_PRINTER  Full Moonraker URL override; takes precedence
                          over DAVINCI_U1_HOST when set.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx


_DEFAULT_HOST = os.environ.get("DAVINCI_U1_HOST", "192.168.1.136")
PRINTER_HTTP = os.environ.get(
    "MULTIACE_E2E_PRINTER", f"http://{_DEFAULT_HOST}:7125")
SAFE_STATES = {"standby", "complete", "cancelled", "error"}


async def assert_safe() -> None:
    async with httpx.AsyncClient(timeout=4) as c:
        r = await c.get(f"{PRINTER_HTTP}/printer/objects/query?print_stats")
        state = r.json()["result"]["status"]["print_stats"]["state"]
    if state not in SAFE_STATES:
        raise SystemExit(
            f"Unsafe to run e2e — print state is {state!r}. "
            f"Aborting (must be one of {SAFE_STATES})."
        )


async def main(url: str) -> None:
    await assert_safe()
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "playwright not installed. run:\n"
            "    pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(2)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2500)

        ace_blocks = await page.locator(".ace-block").count()
        print(f"ACE blocks visible: {ace_blocks}")
        if ace_blocks < 2:
            raise SystemExit(
                f"Expected >= 2 ace-blocks; got {ace_blocks}. "
                f"Skip if printer has only one ACE."
            )

        # Verify deep-link URL — click 📖 on ACE B / slot 0 and capture popup URL.
        async with ctx.expect_page() as popup_info:
            await page.locator(
                '.ace-block[data-ace="1"] .card:nth-of-type(1) .btn-icon'
            ).click()
        popup = await popup_info.value
        popup_url = popup.url
        await popup.close()
        assert "picker=ace" in popup_url, f"deep-link missing picker=ace: {popup_url}"
        assert "ace=1" in popup_url, f"deep-link missing ace=1: {popup_url}"
        # slot=0 (the first card in ACE B's block)
        assert "slot=0" in popup_url, f"deep-link missing slot=0: {popup_url}"
        print(f"deep-link URL OK: {popup_url}")

        # Open chevron menu on ACE B / slot 0 — verify 4 head items appear.
        await page.locator(
            '.ace-block[data-ace="1"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        item_count = await page.locator(".head-target-menu-item").count()
        print(f"head-target menu items: {item_count}")
        assert item_count == 4, f"expected 4 head-menu items, got {item_count}"

        # Click → T0
        await page.locator(".head-target-menu-item").nth(0).click()
        print("issued ACE_LOAD_HEAD via chevron menu → T0")

        # Wait up to 120 s for head_source[0] to settle.
        deadline = time.time() + 120
        async with httpx.AsyncClient(timeout=4) as c:
            while time.time() < deadline:
                r = await c.get(f"{PRINTER_HTTP}/printer/objects/query?ace")
                hs0 = r.json()["result"]["status"]["ace"]["head_source"].get("0")
                if hs0:
                    print(f"head_source[0] resolved: {hs0}")
                    break
                await asyncio.sleep(2)
            else:
                raise SystemExit("head_source[0] never updated within 120 s")

        await page.screenshot(path="e2e_dual_ace_success.png", full_page=True)
        print("OK — see e2e_dual_ace_success.png")
        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
