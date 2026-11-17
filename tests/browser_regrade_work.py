"""SEE THEIR WORK in a real browser (REGRADES "FOLLOW-UP", requirement, 2026-10-21: "when I get a regrade request in followup, can we
have it link to the crop of the problem in question (ie, click on the link and a popup of the crop along with the rubric lines
appears"): on Follow-up, a regrade request's message has See their work beside View conversation; it opens a popup with the grade,
the rubric lines applied (the others folded) and the answer's crop, which enlarges; Close returns to the follow-up with nothing
changed. The app is the synthetic regrade fixture (fake Gradescope, a scripted model): nothing reaches Gradescope, Canvas or a model."""
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
    RegradeFollowup.setUpClass()
    fixture = RegradeFollowup("test_a_keep_card_posts_the_reply_alone")
    fixture.setUp()
    server = None
    try:
        fixture.prepare(fixture.decide(decision="keep", reply="Hi Alice,\n\nThe grade stays.", reason="The final answer is not exact."))
        status = patch("assistant.conversations.engine_for", return_value=PictureEngine())
        status.start()
        fixture.addCleanup(status.stop)
        before = len(fixture.fake.requests)
        server = create_server(fixture.app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(address + "/#follow-ups?filter=regrades")  # REGRADES "APPROVE ALL": their own tab
            expect(page.locator(".support-review .support-proposal").first).to_contain_text("Keep the grade", timeout=15000)
            message = page.locator(".support-review .support-message").first
            message.get_by_role("button", name="See their work").click()

            dialog = page.locator("#dialog")
            expect(dialog).to_be_visible()
            work = dialog.locator(".regrade-work")
            expect(work.locator("[data-cert-score]")).to_contain_text("4 / 10", timeout=10000)
            expect(work.locator("[data-cert-applied-lines] li").first).to_be_visible()
            expect(work.locator(".certification-other-lines")).to_be_attached()
            crop = work.locator(".certification-crops img").first
            expect(crop).to_be_visible()
            expect(work.locator("[data-cert-change]")).to_have_count(0)  # read-only: Edit grade or reply changes it
            Path("test-results").mkdir(exist_ok=True)
            dialog.screenshot(path="test-results/regrade-work.png")
            work.locator("[data-crop-zoom]").first.click()
            zoom = page.locator(".grading-crop-dialog")
            expect(zoom).to_be_visible()
            page.keyboard.press("Escape")
            expect(zoom).to_have_count(0)
            dialog.get_by_role("button", name="Close", exact=True).click()
            expect(dialog).to_be_hidden()
            expect(page.locator(".support-review .support-proposal").first).to_contain_text("Keep the grade")

            # A conversation Faculty cannot place says why.
            page.route("**/api/grading/regrade-work*", lambda route: route.fulfill(status=200, json={"reason": "Faculty's records do not say which answer this request is about."}))
            message.get_by_role("button", name="See their work").click()
            expect(dialog).to_contain_text("do not say which answer")
            dialog.get_by_role("button", name="Close", exact=True).click()

            assert all(r["method"] == "GET" or r["path"] == "/login" for r in fixture.fake.requests[before:]), "nothing is written to Gradescope"
            assert not errors, errors
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
        RegradeFollowup.tearDownClass()
        test_gradescope_send.tearDownModule()
    print("Regrade work browser checks passed: See their work opens the grade, the applied rubric lines, the other lines folded and "
          "the crop (which enlarges) read-only in a popup; Close returns to the follow-up; an unplaced request says why; nothing written.")


if __name__ == "__main__":
    main()
