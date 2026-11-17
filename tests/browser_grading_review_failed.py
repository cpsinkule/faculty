"""Review lists the responses that failed to grade, every batch's, with their reason and each batch's retry."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from fake_openai import default_answer
from test_grading_review_failed import FailedToGrade

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = FailedToGrade("test_a_failed_response_is_listed_with_its_reason_until_a_retry_grades_it")
fixture.setUp()
fixture.host.script = lambda body, host: ("status", 402)
old_job = fixture.queue()
fixture.run_all()
question = fixture.add_question()
job = fixture.queue(questions=[question])
fixture.run_all()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{address}/#grading/{fixture.plan['id']}?tab=review")
        section = page.locator("[data-review-failed]")
        expect(section).to_contain_text("Failed to grade")
        expect(section).to_contain_text("2 responses")
        expect(section).to_contain_text("no credits")
        # Every batch's failures, newest first: the newer batch (Question 2) hides nothing of the older one (Question 1).
        expect(section.locator("[data-failed-batch]")).to_have_count(2)
        assert section.locator("[data-failed-batch]").evaluate_all("nodes => nodes.map(n => n.dataset.failedBatch)") == [job["id"], old_job["id"]]
        expect(section).not_to_have_attribute("open", "")
        expect(section.locator('[data-grading-action="retry-failed"]')).to_have_count(2)  # one per batch, folded until opened
        expect(section.get_by_role("button", name="Retry 1 failed response", exact=True)).not_to_be_visible()
        expect(page.locator(".grading-review-work .callout")).to_contain_text("2 responses failed to grade")
        expect(page.locator("[data-workspace-total]")).to_have_text("0 left · 2 failed")
        # Neither question is complete: their only responses have no grade.
        for title in ("Question 1", "Question 2"):
            row = page.locator("[data-workspace-questions] li").filter(has_text=title)
            expect(row).to_contain_text("1 failed")
        expect(page.locator("[data-review-completed]")).to_have_count(0)
        # Failed to grade heads the page, above the workspace (GRADING "Failed to grade").
        assert page.evaluate("""() => Boolean(document.querySelector('[data-review-failed]').compareDocumentPosition(document.querySelector('.grading-review-workspace'))
          & Node.DOCUMENT_POSITION_FOLLOWING)""")
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): one quiet row at two desktop widths.
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"review-failed-{width}.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            assert section.bounding_box()["height"] < 64
        page.set_viewport_size({"width": 1500, "height": 1100})
        # Nothing waits for review, so the failures are the next task: the workspace's one action opens them; certification is a footer link.
        work = page.locator(".grading-review-work")
        expect(work.locator(".button")).to_have_text("Review 2 failed responses")
        expect(work.locator(".next-step")).to_have_count(0)
        work.get_by_role("button", name="Review 2 failed responses", exact=True).click()
        expect(section).to_have_attribute("open", "")
        expect(section.locator(":scope > summary")).to_be_focused()
        section.locator(":scope > summary").focus()
        page.keyboard.press("Enter")
        expect(section).not_to_have_attribute("open", "")
        page.keyboard.press("Enter")
        expect(section).to_have_attribute("open", "")
        expect(section.locator(".grading-review-failed-reasons").first).to_be_visible()
        fixture.host.script = lambda body, host: default_answer(body)
        # Each batch retries its own: the newer one's retry leaves the older one's failure listed.
        section.locator(f'[data-failed-batch="{job["id"]}"]').get_by_role("button", name="Retry 1 failed response", exact=True).click()
        expect(page.locator("[data-review-failed] [data-failed-batch]")).to_have_count(1)
        expect(page.locator("[data-review-failed] [data-failed-batch]")).to_have_attribute("data-failed-batch", old_job["id"])
        expect(page.locator("[data-review-failed] > summary")).to_contain_text("1 response")
        assert [t["status"] for t in fixture.frq.job(job["id"])["tasks"]] == ["pending"]
        assert [t["status"] for t in fixture.frq.job(old_job["id"])["tasks"]] == ["failed"]
        # open=failed (Home's and the question page's link) opens it straight away.
        page.goto(f"{address}/#grading/{fixture.plan['id']}?tab=review&open=failed")
        expect(page.locator("[data-review-failed]")).to_have_attribute("open", "")
        # ONE RETRY (2026-10-18): the Grade step names the failures under its lead and links Failed to grade; the older batch's card
        # (Earlier batches opens by itself) states its count and links there too. No batch card has a Retry of its own.
        page.goto(f"{address}/#grading/{fixture.plan['id']}?tab=jobs")
        expect(page.locator("[data-grade-failed]")).to_contain_text("1 response failed to grade and will not be graded again by itself: retry it under Failed to grade in Review.")
        failed_link = page.locator("[data-grade-failed]").get_by_role("link", name="Open Failed to grade", exact=True)
        expect(failed_link).to_have_attribute("href", f"#grading/{fixture.plan['id']}?tab=review&open=failed")
        expect(page.locator(".grading-batch-history")).to_have_attribute("open", "")
        expect(page.locator(f'[data-job="{old_job["id"]}"] [data-job-failed]')).to_have_text("1 failed response: retry in Review →")
        expect(page.get_by_role("button", name="Retry 1 failed response", exact=True)).to_have_count(0)
        failed_link.click()
        expect(page.locator("[data-review-failed]")).to_have_attribute("open", "")
        assert not errors, errors
        browser.close()
finally:
    server.close()
