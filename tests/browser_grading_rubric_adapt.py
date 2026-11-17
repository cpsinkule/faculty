"""Adapt Version A's rubrics to Version B in the browser: the dialog, the adaptation in Version B's template slot, the review beside
Version A's lines, and one created question that keeps Version A's line IDs and takes the instructor's comment."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_rubric_adapt import RubricAdaptation

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = RubricAdaptation("test_the_versions_are_paired_by_task_and_every_line_keeps_its_id_and_points")
fixture.setUp()
reference = fixture.reference["rubric"]["rules"]["items"]
fixture.comment(fixture.reference, reference[1]["id"], "Accept any equivalent fraction.")
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"


def screenshots(page, name):
    """One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width."""
    page.screenshot(path=str(OUTPUT / f"{name}-1500.png"), animations="disabled")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error"
                and not message.location.get("url", "").endswith("/templates/prepare") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 and not response.url.endswith("/templates/prepare") else None)
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics")
        page.wait_for_load_state("networkidle")
        adapt = page.get_by_role("button", name="Adapt Version A’s rubrics to other versions", exact=True)
        adapt.click()
        dialog = page.locator("#dialog")
        target = dialog.locator("[data-target]")
        expect(target).to_have_count(1)
        expect(target).to_contain_text("Version B")
        expect(target.get_by_label("Adapt Version A’s rubrics to Version B")).to_be_checked()
        expect(dialog).to_contain_text("never by number alone")
        target.get_by_label("Blank template for Version B").select_option(fixture.blank["id"])
        screenshots(page, "rubric-adapt-dialog")
        dialog.get_by_role("button", name="Adapt rubrics", exact=True).click()
        expect(dialog).not_to_be_visible()
        slots = fixture.slots()
        slot = page.locator("[data-template-slot]").nth([s["key"] for s in fixture.frq.assignment_templates(fixture.plan["id"])["slots"]].index(slots["B"]["key"]))
        expect(slot.locator(".grading-rubric-draft")).to_contain_text("Adapting Version A’s rubrics")
        expect(slot.get_by_role("button", name="Cancel adapting", exact=True)).to_be_visible()
        assert len(fixture.uploaded_questions()) == 1, "Adapting creates nothing"
        fixture.frq.process_cloud()
        expect(slot.locator(".grading-rubric-draft")).to_contain_text("1 question adapted from Version A, 1 could not be", timeout=20000)
        expect(slot.locator("[data-draft-mode]")).to_contain_text("keeps the scoring of Version A’s rubric")
        slot.get_by_role("button", name="Review and create adapted questions", exact=True).click()
        expect(dialog.locator("fieldset.rubric-draft")).to_have_count(1)
        draft = dialog.locator("fieldset.rubric-draft")
        expect(draft.locator("legend")).to_contain_text("from Version A’s Question 18")
        expect(draft.locator("[data-reference]")).to_have_count(len(reference))
        expect(draft.locator("[data-line]").nth(1).locator("[data-reference]")).to_contain_text("Your comment there: Accept any equivalent fraction.")
        expect(dialog.get_by_label("Copy my comments on Version A’s rubric lines to the same lines of the questions I create")).to_be_checked()
        expect(dialog).to_contain_text("Problem 18 could not be adapted")
        screenshots(page, "rubric-adapt-review")
        draft.get_by_label("Question draft 0 line 1", exact=True).fill("Full credit for $$y = 7$$")
        page.get_by_role("button", name="Create selected questions", exact=True).click()
        expect(dialog).not_to_be_visible()
        # ACCEPTING A DRAFT READIES IT (2026-10-11): Version A's uploaded rubric is still the instructor's to confirm, and the toast says so.
        expect(page.locator("#toast")).to_contain_text("1 question created. Next, confirm the blank templates. The rubric check starts by itself once every rubric is confirmed for review")
        expect(page.locator("[data-rubric-question]")).to_have_count(2)
        created = next(q for q in fixture.uploaded_questions() if q["exam_version"] == "B")
        rules = created["rubric"]["rules"]
        assert [i["id"] for i in rules["items"]] == [i["id"] for i in reference], rules["items"]
        assert [i["points"] for i in rules["items"]] == [i["points"] for i in reference]
        assert rules["items"][0]["description"] == "Full credit for $$y = 7$$"
        assert created["rubric"]["compiler"]["name"] == "model-adapted-v1" and created["rubric"]["approved_at"] is None
        expect(slot.locator(".grading-rubric-draft")).to_contain_text("1 created")
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Rubric adaptation browser passed: dialog, progress in the version's slot, review beside the reference lines, comments, created question, no sideways scroll on the desktop.")
finally:
    server.close()
