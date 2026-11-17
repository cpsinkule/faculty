"""EDIT THE GRADE in a real browser (REGRADES, requirement, 2026-10-08): on Follow-up, a regrade request's
Keep card opens with Edit grade or reply, reads the question's rubric, ticks the grade's lines now, and Save turns it into a change
card naming the line it adds, the grade it makes and the instructor's reply, in the card's place; nothing is applied. The app is the
synthetic regrade fixture (fake Gradescope, a scripted model): nothing here reaches Gradescope, Canvas or a model."""
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

REPLY = "Hi Alice,\n\nI looked again: the final answer counts, so I gave you those points back."


def main():
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    RegradeFollowup.setUpClass()  # the graded workspace, built once (test_gradescope_send)
    fixture = RegradeFollowup("test_a_keep_card_posts_the_reply_alone")
    fixture.setUp()
    server = None
    try:
        fixture.prepare(fixture.decide(decision="keep", reply="Hi Alice,\n\nThe grade stays.", reason="The final answer is not exact."))
        # The page's Assistant status reads the scripted model too (the fixture's hosted connection names no host).
        status = patch("assistant.conversations.engine_for", return_value=PictureEngine())
        status.start()
        fixture.addCleanup(status.stop)
        before = len(fixture.fake.saves())
        server = create_server(fixture.app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(address + "/#follow-ups?filter=regrades")  # REGRADES "APPROVE ALL": their own tab
            card = page.locator(".support-review .support-proposal").first
            expect(card).to_contain_text("Keep the grade and answer the regrade request", timeout=15000)
            card.get_by_role("button", name="Edit grade or reply").click()

            form = page.locator(".support-review [data-followup-edit-form]")
            expect(form).to_contain_text("Edit the regrade decision")
            lines = form.locator("input[name='line']")
            expect(lines.first).to_be_visible(timeout=10000)  # read once Edit opens
            expect(form.locator("legend")).to_contain_text("Now 4 / 10")
            expect(form.locator("input[name='line'][value='R3_2']")).to_be_checked()
            expect(form.locator("input[name='line'][value='R3_3']")).not_to_be_checked()
            form.locator("input[name='line'][value='R3_3']").check()
            form.locator("textarea[name='body']").fill(REPLY)
            page.wait_for_timeout(4500)  # a poll redraws the page: what was ticked and typed stays
            expect(form.locator("input[name='line'][value='R3_3']")).to_be_checked()
            expect(form.locator("textarea[name='body']")).to_have_value(REPLY)
            Path("test-results").mkdir(exist_ok=True)
            form.screenshot(path="test-results/regrade-edit-form.png")
            form.get_by_role("button", name="Save changes").click()
            expect(page.locator("#toast")).to_contain_text("Your decision is on the card")

            edited = page.locator(".support-review .support-proposal").first
            expect(edited).to_contain_text("Change the grade to")
            expect(edited).to_contain_text("/ 10 and answer the regrade request")
            expect(edited.locator(".support-changes").first).to_contain_text("Rubric lines")
            expect(edited.locator(".support-changes").first).to_contain_text("+ 3.")
            expect(edited.locator(".support-changes").first).to_contain_text("I looked again: the final answer counts")
            expect(edited.get_by_role("button", name="Apply")).to_be_visible()
            expect(page.locator(".support-review [data-followup-edit-form]")).to_have_count(0)
            edited.screenshot(path="test-results/regrade-edit-card.png")

            # Cancel leaves a card as it was; the reply alone when the rubric cannot be read.
            edited.get_by_role("button", name="Edit grade or reply").click()
            form = page.locator(".support-review [data-followup-edit-form]")
            expect(form.locator("input[name='line'][value='R3_3']")).to_be_checked(timeout=10000)  # ticked as the card proposes
            form.get_by_role("button", name="Cancel").click()
            expect(page.locator(".support-review [data-followup-edit-form]")).to_have_count(0)
            page.route("**/api/grading/responses/*", lambda route: route.fulfill(status=503, json={"error": "Synthetic outage."}))
            page.locator(".support-review .support-proposal").first.get_by_role("button", name="Edit grade or reply").click()
            form = page.locator(".support-review [data-followup-edit-form]")
            expect(form).to_contain_text("The rubric could not be read: Synthetic outage. You can still edit the reply.")
            expect(form.locator("input[name='line']")).to_have_count(0)
            form.get_by_role("button", name="Cancel").click()

            assert len(fixture.fake.saves()) == before, "nothing is written to Gradescope before Apply"
            assert fixture.request.get("staff_comment") in (None, ""), "no reply is posted before Apply"
            Path("test-results").mkdir(exist_ok=True)
            page.screenshot(path="test-results/regrade-edit.png", full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
        RegradeFollowup.tearDownClass()
        test_gradescope_send.tearDownModule()
    print("Regrade edit browser checks passed: Edit grade or reply reads the rubric, ticks the grade now, keeps ticks and text through a "
          "poll, saves a change card naming the added line in place, Cancel, the reply alone when the rubric cannot be read, nothing applied.")


if __name__ == "__main__":
    main()
