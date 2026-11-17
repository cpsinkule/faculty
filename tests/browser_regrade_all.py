"""REGRADES "APPROVE ALL" in a real browser (requirement, 2026-10-29): Follow-up's Regrades tab lists the regrade conversations, which leave Active;
Approve all 2 asks once, then approves the keep and the change in that order, each through its own Approve, and drives the
change's Apply to the end. The app is the synthetic regrade fixture (fake Gradescope, a scripted model): nothing here reaches
Gradescope, Canvas or a model."""
import logging
import sys
import threading
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import expect, sync_playwright  # noqa: E402
from waitress.server import create_server  # noqa: E402
import test_gradescope_send  # noqa: E402
from test_regrade_followup import PictureEngine, RegradeFollowup  # noqa: E402


def main():
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    RegradeFollowup.setUpClass()  # the graded workspace, built once (test_gradescope_send)
    fixture = RegradeFollowup("test_approve_all_takes_keeps_first_since_an_approved_change_outdates_the_exams_other_cards")
    fixture.setUp()
    server = None
    try:
        cards = fixture.two_cards()
        status = patch("assistant.conversations.engine_for", return_value=PictureEngine())
        status.start()
        fixture.addCleanup(status.stop)
        server = create_server(fixture.app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors, approvals = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: approvals.append(request.url.split("/actions/")[1].split("/")[0])
                    if request.method == "POST" and "/api/assistant/actions/" in request.url and request.url.endswith("/approve") else None)
            # A LOST ANSWER (2026-10-29: the browser lost the second approval's answer while the server carried it out): the
            # first approval reaches the server, its answer never reaches the page; Approve all reads the card back and goes on.
            lost = []
            def lose_first(route):
                if lost:
                    route.continue_()
                    return
                lost.append(route.request.url)
                route.fetch()
                route.abort()
            page.route("**/api/assistant/actions/*/approve", lose_first)
            page.goto(address + "/#follow-ups")
            tabs = page.locator(".support-filter-tabs")
            expect(tabs.get_by_role("button", name="Regrades 2")).to_be_visible(timeout=15000)
            expect(tabs.get_by_role("button", name="Active 0")).to_be_visible()  # regrade requests leave Active
            expect(page.locator("[data-regrade-approve-all]")).to_have_count(0)  # only on its own tab
            tabs.get_by_role("button", name="Regrades 2").click()
            expect(page.locator(".support-queue")).to_contain_text("answer the regrade request")
            Path("test-results").mkdir(exist_ok=True)
            page.locator(".support-page").screenshot(path="test-results/regrade-approve-all-tab.png")
            page.get_by_role("button", name="Approve all 2").click()

            dialog = page.locator("#support-regrades-all")
            expect(dialog).to_contain_text("1 keep the grade, 1 change it.")
            expect(dialog).to_contain_text("a changed grade goes to Gradescope, the gradebook and Canvas")
            Path("test-results").mkdir(exist_ok=True)
            dialog.screenshot(path="test-results/regrade-approve-all-dialog.png")
            dialog.get_by_role("button", name="Approve all 2").click()
            expect(page.locator("#toast")).to_contain_text("Approved 2 of 2.", timeout=30000)
            assert approvals == [cards["keep"]["id"], cards["change"]["id"]], approvals  # keeps first, the lost one never sent twice
            assert len(lost) == 1 and cards["keep"]["id"] in lost[0], lost

            assert fixture.request.get("staff_comment") in (None, ""), "the change's reply waits for Gradescope to show its grade"
            fixture.sender.drain()  # the fake Gradescope takes the grade; the page's Apply goes on by itself
            expect(page.locator("#toast")).to_contain_text("Applied 1 changed grade", timeout=30000)
            assert fixture.request.get("staff_comment") == "Hi, you were right about the setup.", fixture.request
            expect(page.locator("[data-regrade-approve-all]")).to_have_count(0)
            page.screenshot(path="test-results/regrade-approve-all.png", full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
        RegradeFollowup.tearDownClass()
        test_gradescope_send.tearDownModule()
    print("Regrade approve-all browser checks passed: Regrades is its own tab (out of Active), Approve all asks once, approves "
          "the keep before the change through each card's own Approve, and drives the change's Apply to the end; a lost answer is read back and the run goes on.")


if __name__ == "__main__":
    main()
