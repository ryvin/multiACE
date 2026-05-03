"""Headless Playwright visual regression for the multiACE Web Console.

Captures Dashboard / Activity / Dryer / Config / Diag / Hardware at desktop (1280x900)
and mobile (390x844, iPhone 12 Pro), reports console errors, and exits
non-zero if any page has a JS pageerror. Read-only — never clicks an action
button, never submits the Config form.

Usage:
    pip install playwright
    playwright install chromium
    python tools/visual_regression.py http://192.168.1.171/multiace/
    # or against a local dev server:
    python tools/visual_regression.py http://localhost:7126/

Screenshots land in ./screenshots/ next to wherever you run it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path


READ_ONLY_TABS = ["dashboard", "activity", "dryer", "config", "diag", "hardware"]


def main(url: str, out_dir: Path) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright not installed. run:\n"
            "    pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    errors_by_view: dict[str, list[str]] = {}
    fail = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for label, viewport in [
            ("desktop", {"width": 1280, "height": 900}),
            ("mobile", {"width": 390, "height": 844}),
        ]:
            ctx_kwargs = {"viewport": viewport}
            if label == "mobile":
                ctx_kwargs["device_scale_factor"] = 2
            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()

            errors: list[str] = []
            page.on(
                "console",
                lambda m: errors.append(f"[{m.type}] {m.text}")
                if m.type in ("error", "warning")
                else None,
            )
            page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2500)

            for tab in READ_ONLY_TABS:
                # First tab is already active; otherwise click its tab button.
                if tab != "dashboard":
                    selector = f"button[data-view='{tab}']"
                    if page.query_selector(selector):
                        page.click(selector)
                        page.wait_for_timeout(800)

                shot = out_dir / f"{timestamp}-{label}-{tab}.png"
                page.screenshot(path=str(shot), full_page=True)
                print(f"  {shot}")

            errors_by_view[label] = list(errors)
            ctx.close()

        browser.close()

    print()
    print("Console errors / pageerrors:")
    for label, errs in errors_by_view.items():
        if not errs:
            print(f"  {label}: clean")
            continue
        fail = True
        print(f"  {label}: {len(errs)} issue(s)")
        for e in errs:
            print(f"    {e}")

    return 1 if fail else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)

    target_url = sys.argv[1]
    output_dir = Path(os.environ.get("MULTIACE_VR_OUT", "screenshots"))

    rc = main(target_url, output_dir)
    sys.exit(rc)
