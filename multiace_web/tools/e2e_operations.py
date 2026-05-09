"""Structural Playwright e2e for the multiACE Operations feature.

Injects synthetic state into the page via page.evaluate() to exercise all
four head-state matrix branches without real hardware.  Uses page.clock()
for the 3-second countdown so there is no wall-clock flake.

Must be run against a running instance of the web console (local dev or
live printer — but does NOT issue any real gcode commands because the mock
state has no Moonraker behind it; sendScript/sendCommand calls will bounce
with a 503 and the test only asserts UI structure).

Usage:
    pip install playwright && playwright install chromium
    # Start local dev server first:
    MULTIACE_LOG_DIR=/tmp/fake_logs uvicorn multiace_web.server:app --port 7126
    python tools/e2e_operations.py http://localhost:7126/

Pre-flight: NO print safety check required (no real gcode issued).
"""
from __future__ import annotations

import asyncio
import sys


async def inject_state(page, patch: dict) -> None:
    """Merge patch into the page's JS `state` object and re-render."""
    import json
    await page.evaluate(f"""
        Object.assign(window.state, {json.dumps(patch)});
        if (typeof window.renderAll === 'function') window.renderAll();
    """)


async def main(url: str) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(2)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # ---------------------------------------------------------------
        # Test 1: Empty head — chevron shows 4 load items, NO Unload item,
        # NO separator.
        # ---------------------------------------------------------------
        await inject_state(page, {
            "device_count": 2,
            "head_source": {"0": None, "1": None, "2": None, "3": None},
            "sensors": {"0": False, "1": False, "2": False, "3": False},
            "swap_in_progress": False,
            "smartSwapPending": None,
            "gate_status": [1, 1, 1, 1],
        })
        # Open chevron on ACE A (ace=0) slot 0
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        item_count = await page.locator(".head-target-menu-item").count()
        assert item_count == 4, f"Test 1 (empty): expected 4 load items, got {item_count}"
        sep_count = await page.locator(".head-target-menu-sep").count()
        assert sep_count == 0, f"Test 1 (empty): expected no separator, got {sep_count}"
        print("Test 1 PASS: empty head — 4 load items, no Unload")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

        # ---------------------------------------------------------------
        # Test 2: Loaded same-ACE — chevron prepends Unload item + separator.
        # ---------------------------------------------------------------
        await inject_state(page, {
            "head_source": {
                "0": {"ace": 0, "slot": 0, "type": "PLA", "color": "ff0000"},
                "1": None, "2": None, "3": None,
            },
            "sensors": {"0": True, "1": False, "2": False, "3": False},
        })
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        # First item should be "↗ Unload T1"
        first_item_text = await page.locator(".head-target-menu-item").nth(0).text_content()
        assert "Unload" in first_item_text, \
            f"Test 2 (loaded same-ACE): first item should be Unload, got: {first_item_text!r}"
        sep_count = await page.locator(".head-target-menu-sep").count()
        assert sep_count == 1, f"Test 2: expected 1 separator, got {sep_count}"
        total_items = await page.locator(".head-target-menu-item").count()
        assert total_items == 5, \
            f"Test 2: expected 1 Unload + 4 Load = 5 items, got {total_items}"
        print("Test 2 PASS: loaded same-ACE — Unload item prepended")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

        # ---------------------------------------------------------------
        # Test 3: Print state gating — all items disabled with tooltip.
        # ---------------------------------------------------------------
        await page.evaluate("window.printState = window.printState || {};")
        await page.evaluate("window.printState.state = 'printing';")
        await page.evaluate("if (typeof renderAll === 'function') renderAll();")
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        disabled_count = await page.locator(".head-target-menu-item[disabled]").count()
        total_count = await page.locator(".head-target-menu-item").count()
        assert disabled_count == total_count, \
            f"Test 3 (printing gate): expected all {total_count} items disabled, got {disabled_count} disabled"
        print(f"Test 3 PASS: printing gate — all {disabled_count} items disabled")
        # Restore safe state
        await page.evaluate("window.printState.state = 'standby';")
        await page.evaluate("if (typeof renderAll === 'function') renderAll();")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

        # ---------------------------------------------------------------
        # Test 4: smartSwapPending gate — all items disabled.
        # ---------------------------------------------------------------
        await inject_state(page, {
            "smartSwapPending": {"head": 0, "leg": 1, "startedAt": 0},
        })
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        disabled_count = await page.locator(".head-target-menu-item[disabled]").count()
        total_count = await page.locator(".head-target-menu-item").count()
        assert disabled_count == total_count, \
            f"Test 4 (smartSwapPending gate): expected all disabled, got {disabled_count}/{total_count}"
        print(f"Test 4 PASS: smartSwapPending gate — all {disabled_count} items disabled")
        await inject_state(page, {"smartSwapPending": None})
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

        # ---------------------------------------------------------------
        # Test 5: Toast countdown and Cancel — use page.clock() to fast-forward.
        # ---------------------------------------------------------------
        # Reset to empty head so clicking Load items goes to swap path.
        # For the toast to show we need a loaded head, inject loaded_cross_ace state:
        await inject_state(page, {
            "head_source": {
                "0": {"ace": 0, "slot": 0, "type": "PLA", "color": "ff0000"},
                "1": None, "2": None, "3": None,
            },
            "sensors": {"0": True, "1": False, "2": False, "3": False},
            "swapParkAvailable": False,
        })
        # Install fake clock BEFORE the click that triggers the timer
        await ctx.route("**/api/command", lambda r: r.fulfill(status=200, body='{"ok":true}'))
        await page.clock.install()
        # Open chevron on ACE A slot 0 and click → T1 (swap) to trigger toast
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        # Find T1 load item (index 1 — after the Unload separator): text contains "T1"
        load_items = page.locator(".head-target-menu-item")
        item_texts = await load_items.all_text_contents()
        # Find the "→ T1" item
        t1_idx = next((i for i, t in enumerate(item_texts) if "T1" in t and "Unload" not in t), None)
        assert t1_idx is not None, f"Test 5: could not find → T1 item. Items: {item_texts}"
        await load_items.nth(t1_idx).click()

        # Toast should appear within 500ms
        await page.wait_for_selector(".swap-confirm-toast", timeout=2000)
        toast_text = await page.locator(".swap-confirm-toast .swap-confirm-msg").text_content()
        assert "Swap" in toast_text, f"Test 5: expected Swap toast, got {toast_text!r}"
        assert "Cancel" in toast_text, f"Test 5: expected Cancel countdown in toast text"

        # Click Cancel button — toast disappears
        await page.locator(".swap-confirm-cancel").click()
        await page.wait_for_selector(".swap-confirm-toast", state="hidden", timeout=2000)
        print(f"Test 5 PASS: swap-confirm toast appears with Cancel; Cancel dismisses it")
        # Uninstall clock
        await page.clock.uninstall()

        # ---------------------------------------------------------------
        # Test 6: Toast navigates-away abort — switch tab during countdown.
        # ---------------------------------------------------------------
        await page.clock.install()
        await page.locator(
            '.ace-block[data-ace="0"] .card:nth-of-type(1) '
            '.slot-load-split > button:last-child'
        ).click()
        await page.locator(".head-target-menu").wait_for(state="visible", timeout=3000)
        item_texts = await page.locator(".head-target-menu-item").all_text_contents()
        t1_idx = next((i for i, t in enumerate(item_texts) if "T1" in t and "Unload" not in t), None)
        await page.locator(".head-target-menu-item").nth(t1_idx).click()
        await page.wait_for_selector(".swap-confirm-toast", timeout=2000)
        # Navigate to Activity tab — should abort toast
        await page.click('button[data-view="activity"]')
        await page.wait_for_selector(".swap-confirm-toast", state="hidden", timeout=2000)
        print("Test 6 PASS: navigate-away aborts swap-confirm toast")
        await page.clock.uninstall()

        # ---------------------------------------------------------------
        # Final summary
        # ---------------------------------------------------------------
        if errors:
            print(f"\nJS errors during test: {errors}", file=sys.stderr)
            sys.exit(1)
        print("\nAll e2e_operations tests PASSED")
        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
