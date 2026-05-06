"""Headless Playwright demo of the workflow panel.

Drives the dashboard with synthesized state via the page's JS context so the
workflow panel can be screenshotted at each phase without triggering a real
multi-step ACE action.

Usage:
    python tools/workflow_demo.py http://192.168.1.171/multiace/
"""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.1.171/multiace/"
OUT = Path(os.environ.get("MULTIACE_VR_OUT", "screenshots/workflow"))
OUT.mkdir(parents=True, exist_ok=True)

LOAD_4HEADS = """
state.head_source = {
  0: {ace: 0, slot: 0, type: "PLA", color: "FFB400"},
  1: {ace: 0, slot: 1, type: "PLA", color: "FF3030"},
  2: {ace: 0, slot: 2, type: "PETG", color: "30A0FF"},
  3: {ace: 0, slot: 3, type: "TPU", color: "30C060"}
};
state.gate_status = [1, 1, 1, 1];
state.sensors = {0: true, 1: true, 2: true, 3: true};
state.print_task_config = {
  0: {type: "PLA",  color: 4294947840, vendor: "Snapmaker"},
  1: {type: "PLA",  color: 4294918192, vendor: "Generic"},
  2: {type: "PETG", color: 4281392367, vendor: "Snapmaker"},
  3: {type: "TPU",  color: 4281352800, vendor: "Generic"}
};
state.active_device = 0;
state.device_count = 1;
state.swap_in_progress = false;
"""


def shot(page, label):
    p = OUT / f"workflow-{label}.png"
    page.screenshot(path=str(p), full_page=True)
    print(f"  {p}")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 1100})
    page = ctx.new_page()
    page.on("pageerror", lambda e: print(f"  [pageerror] {e}"))

    page.goto(URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)

    # 1. Seed loaded state and re-render
    page.evaluate(LOAD_4HEADS + "renderAll();")
    shot(page, "01-loaded-baseline")

    # 2. Click Unload All — seeds workflow, T0 starts running
    page.evaluate("seedUnloadAllWorkflow();")
    shot(page, "02-unload-all-started")

    # 3. T0 completes — workflow advances to T1
    page.evaluate("""
      applyEventToWorkflow({
        action: "UNLOAD_HEAD",
        params: {head: 0, ace: 0, slot: 0}
      });
      // Reflect head_source change too so the slot strip updates
      state.head_source[0] = null;
      renderAll();
    """)
    page.wait_for_timeout(400)
    shot(page, "03-T0-done-T1-running")

    # 4. T1 fails
    page.evaluate("""
      applyEventToWorkflow({
        action: "UNLOAD_HEAD_FAILED",
        params: {head: 1, error: "feed_auto_error timeout!"}
      });
      renderAll();
    """)
    page.wait_for_timeout(400)
    shot(page, "04-T1-failed-T2-running")

    # 5. T2 completes
    page.evaluate("""
      applyEventToWorkflow({
        action: "UNLOAD_HEAD",
        params: {head: 2, ace: 0, slot: 2}
      });
      state.head_source[2] = null;
      renderAll();
    """)
    page.wait_for_timeout(400)
    shot(page, "05-T2-done-T3-running")

    # 6. T3 completes — workflow finishes
    page.evaluate("""
      applyEventToWorkflow({
        action: "UNLOAD_HEAD",
        params: {head: 3, ace: 0, slot: 3}
      });
      state.head_source[3] = null;
      renderAll();
    """)
    page.wait_for_timeout(400)
    shot(page, "06-all-terminal")

    # Mobile too — the panel should stack cleanly
    ctx.close()
    mctx = browser.new_context(viewport={"width": 390, "height": 844},
                               device_scale_factor=2)
    page = mctx.new_page()
    page.goto(URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    page.evaluate(LOAD_4HEADS + "renderAll(); seedUnloadAllWorkflow();")
    shot(page, "07-mobile-started")
    page.evaluate("""
      applyEventToWorkflow({action: "UNLOAD_HEAD", params: {head: 0, ace: 0, slot: 0}});
      state.head_source[0] = null;
      applyEventToWorkflow({action: "UNLOAD_HEAD_FAILED", params: {head: 1, error: "feed_auto_error timeout!"}});
      renderAll();
    """)
    page.wait_for_timeout(400)
    shot(page, "08-mobile-mid")
    mctx.close()

    browser.close()

print("\nWorkflow demo screenshots written to:", OUT)
