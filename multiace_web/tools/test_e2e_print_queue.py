"""Playwright structural smoke test for the Print queue tab.

Requires a running multiACE web instance (local dev or live printer).
The test is READ-ONLY — it never clicks Print, Fix loadout submit, or Re-validate
on a print that is active.

Usage:
    pip install playwright && playwright install chromium
    DAVINCI_U1_HOST=192.168.1.136 python tools/test_e2e_print_queue.py http://$DAVINCI_U1_HOST/multiace/
    # or against local dev:
    python tools/test_e2e_print_queue.py http://localhost:7127/
"""
from __future__ import annotations
import os
import sys
from pathlib import Path


def main(base_url: str) -> int:
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError:
        print("playwright not installed. Run: pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 2

    errors: list[str] = []
    fail = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
        page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}")
                if m.type in ("error",) else None)

        print(f"Navigating to {base_url}...")
        page.goto(base_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # 1. Print queue tab must exist
        pq_btn = page.query_selector("button[data-view='print-queue']")
        if not pq_btn:
            print("FAIL: Print queue tab button not found")
            return 1
        print("OK: Print queue tab button found")

        # 2. Navigate to Print queue tab
        pq_btn.click()
        page.wait_for_timeout(1000)

        # 3. Section must be visible
        pq_section = page.query_selector("section[data-view='print-queue']")
        if not pq_section:
            print("FAIL: print-queue section not found")
            return 1
        if "active" not in (pq_section.get_attribute("class") or ""):
            print("FAIL: print-queue section not active after clicking tab")
            return 1
        print("OK: Print queue section is active")

        # 4. Either a list of items or the empty state must be visible
        pq_list = page.query_selector("#print-queue-list")
        empty_state = page.query_selector("#print-queue-empty")
        has_items = pq_list and pq_list.inner_text().strip() != ""
        empty_visible = empty_state and "hidden" not in (empty_state.get_attribute("class") or "")

        if has_items:
            print("OK: Print queue has items")
            # 5. If items exist, verify status chip is present
            chip = page.query_selector(".status-chip")
            if chip:
                print(f"OK: Status chip found: {chip.inner_text()}")
            else:
                print("WARN: no status chip found in print queue items")

            # 6. Verify resolution table renders on expand
            details = page.query_selector(".pq-details")
            if details:
                details.click()
                page.wait_for_timeout(400)
                table = page.query_selector(".resolution-table")
                if table:
                    print("OK: Resolution table renders on expand")
                else:
                    print("WARN: no resolution-table after expanding details")

            # 7. Verify Re-validate button present (but don't click during print)
            rev_btn = page.query_selector(".pq-revalidate-btn")
            if rev_btn:
                print("OK: Re-validate button present")
            else:
                print("WARN: no Re-validate button found")

        elif empty_visible:
            print("OK: Empty state shown (no sidecars present — slice a print to test further)")
        else:
            print("WARN: neither list items nor empty state visible")

        # 8. Screenshot
        out_dir = Path(os.environ.get("MULTIACE_VR_OUT", "screenshots"))
        out_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        shot = out_dir / f"{ts}-print-queue.png"
        page.screenshot(path=str(shot), full_page=True)
        print(f"Screenshot: {shot}")

        ctx.close()
        browser.close()

    if errors:
        fail = True
        print(f"\n{len(errors)} console error(s):")
        for e in errors:
            print(f"  {e}")
    else:
        print("\nNo console errors.")

    return 1 if fail else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
