"""The retired Grading index (2026-10-18, pipeline review P3 / I5 / I7): `#grading?view=list` and every `#grading` address without an
assignment open Assignments, without reading or polling the grading overview; `#grading?settings=1` opens Compute settings over
Assignments; compute availability and costs are Compute settings' "Availability and costs" section, read when it is opened and on
Check again, never on a timer."""
import re
import sys
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests

fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
job, _ = fixture.queue()
fixture.finish(job)
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(15000)
        calls, errors = Counter(), []
        page.on("request", lambda req: calls.update([urlsplit(req.url).path + (f"?{urlsplit(req.url).query}" if urlsplit(req.url).query else "")]))
        page.on("pageerror", lambda error: errors.append(str(error)))

        # The old index address lands on Assignments, replacing the address (no history entry), and reads no grading overview.
        page.goto(address + "/#grading?view=list")
        page.wait_for_function("location.hash.startsWith('#assignments')")
        expect(page.locator("#grading-section")).not_to_be_visible()
        page.wait_for_timeout(500)
        assert calls["/api/grading?health=0"] == 0, calls
        assert not page.locator(".grading-assessment-grid").count()

        # Compute settings over Assignments, from the link Workspace settings and other pages use.
        page.evaluate("location.hash = '#grading?settings=1'")
        page.wait_for_function("location.hash.startsWith('#assignments')")
        dialog = page.locator("#grading-settings-dialog")
        expect(dialog).to_be_visible()
        assert calls["/api/grading/health"] == 0, calls
        # Availability and costs: read only when its section is opened.
        tab = dialog.get_by_role("tab", name="Availability and costs")
        with page.expect_response(re.compile(r".*/api/grading/health$")):
            tab.click()
        expect(dialog.locator("[data-compute-health] .grading-health")).to_be_visible()
        expect(dialog.locator("[data-compute-health] [data-compute-cost]")).to_contain_text("recorded from usage")
        reads = calls["/api/grading/health"]
        # Switching sections and back keeps what was read; Check again reads once more. Nothing reads on a timer.
        dialog.get_by_role("tab", name="Defaults").click()
        tab.click()
        expect(dialog.locator("[data-compute-health] .grading-health")).to_be_visible()
        assert calls["/api/grading/health"] == reads, calls
        page.wait_for_timeout(1500)
        assert calls["/api/grading/health"] == reads, calls
        with page.expect_response(re.compile(r".*/api/grading/health$")):
            dialog.locator("[data-compute-health]").get_by_role("button", name="Check again").click()
        expect(dialog.locator("[data-compute-health] .grading-health")).to_be_visible()
        assert calls["/api/grading?health=0"] == 0, calls
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert not errors, errors
        browser.close()
        print("Retired Grading index passed: #grading?view=list opens Assignments without reading the grading overview, #grading?settings=1 opens Compute settings over it, and Availability and costs reads health only when opened or checked again.")
finally:
    server.close()
    fixture.doCleanups()
