"""Check the answer key read from the rubric, then review by answer group: thin work is set apart, nothing is credited by a match."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_answer_groups import AnswerGroupWorkflow, BARE, WORKED

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = AnswerGroupWorkflow("test_the_same_correct_answer_is_split_by_visible_work_and_never_credited_by_the_match")
fixture.setUp()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        plan_id = fixture.plan["id"]
        page.goto(f"{address}/#grading/{plan_id}?tab=answer-key")
        # The answer key is its own workflow step (the separate setup guide is gone); the rubric's own full-credit line proposes it.
        expect(page.locator(".grading-guide")).to_have_count(0)
        expect(page.locator('.grading-workflow [data-pipeline-step="key"] a')).to_have_attribute("aria-current", "step")
        page.get_by_role("button", name="Edit the whole answer key", exact=True).click()
        dialog = page.locator("#dialog")
        expect(dialog).to_contain_text("It never awards credit")
        expect(dialog).to_contain_text("Read from the rubric — check it")
        expect(dialog).to_contain_text("has its own line for a correct answer without it (Item 3)")
        key = dialog.get_by_label("Accepted final answers", exact=True)
        expect(key).to_have_value("x=6")
        key.fill("x=6 | 6")
        dialog.locator("summary", has_text="Worked solution and problem text").click()
        dialog.get_by_label("Worked solution (shown to the grader)", exact=True).fill("3x = 18, so x = 6")
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.screenshot(path=str(OUTPUT/"answer-key-1500.png"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        dialog.get_by_role("button", name="Save answer key", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.locator("#toast")).to_contain_text("Rubric approvals and confirmed locations are unchanged")
        saved = fixture.frq.assignment(plan_id)["questions"][0]
        assert saved["content"]["answer_key"] == [{"label": "", "accept": ["x=6", "6"]}], saved["content"]["answer_key"]
        assert saved["rubric"]["approved_at"], "The approved rubric must survive saving the key"

        # Two students write the same correct answer; the model proposes full credit for both.
        fixture.grade([WORKED, BARE], {tuple(WORKED): {"R1"}, tuple(BARE): {"R1"}})
        page.goto(f"{address}/#grading/{plan_id}?tab=review&browse=all")
        page.get_by_text("Review several responses together", exact=True).click()
        page.get_by_role("button", name="Grade by answer group", exact=True).click()
        expect(dialog).to_contain_text("2 responses in 2 groups")
        rows = dialog.locator(".grading-group-list li")
        expect(rows).to_have_count(2)
        thin = rows.filter(has_text="No work visible")
        expect(thin).to_contain_text("Matches your key")
        expect(thin).to_contain_text("1 flagged for you")
        thin.get_by_role("button", name="Look at 1 response", exact=True).click()
        expect(dialog).to_contain_text("This rubric pays for the work")
        expect(dialog.locator(".grading-contact-sheet figure")).to_have_count(1)
        expect(dialog.locator(".grading-contact-sheet figcaption")).to_contain_text("Full credit was proposed with little visible work")
        expect(dialog.locator(".grading-contact-sheet img")).to_have_count(1)
        page.screenshot(path=str(OUTPUT/"answer-group-1500.png"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        # Approval needs the instructor's own look; then they move this group to the rubric's no-work line.
        dialog.get_by_role("button", name="Approve the ticked responses", exact=True).click()
        expect(dialog.locator("[role=alert]")).to_contain_text("Look at every ticked response first")
        dialog.get_by_label("Item 1 · 10 points · Correct Answer", exact=False).uncheck()
        dialog.get_by_label("Item 3 · 2 points · Correct answer, but no work", exact=False).check()
        dialog.get_by_label("I looked at every ticked response, including its work").check()
        dialog.get_by_role("button", name="Approve the ticked responses", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("1 approved")
        # Back on the list, the worked group remains and is accepted as proposed.
        expect(dialog).to_contain_text("1 responses in 1 groups")
        dialog.get_by_role("button", name="Look at 1 response", exact=True).click()
        expect(dialog).to_contain_text("Work shown")
        dialog.get_by_label("I looked at every ticked response, including its work").check()
        dialog.get_by_role("button", name="Approve the ticked responses", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Every group of this question is reviewed")
        expect(dialog).not_to_be_visible()
        scores = sorted(fixture.frq.response(r["id"])["review"]["score"]["score"] for r in fixture.frq.responses(plan_id, fixture.qid))
        assert scores == [2, 10], scores
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Answer group browser passed: key read from the rubric, approvals kept, thin work set apart and warned, group moved to the no-work line, per-response receipts, no sideways scroll on the desktop.")
finally:
    server.close()
    fixture.doCleanups()
