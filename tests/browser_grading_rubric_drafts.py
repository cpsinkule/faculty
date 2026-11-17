"""Draft every version's rubrics at once in the browser: choose each version's template, one button, review each problem's versions
side by side with one point value per line, then create questions (requirement, 2026-09-25)."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_rubric_versions import RubricVersions

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = RubricVersions("test_one_request_per_problem_writes_every_version_with_the_same_lines")
fixture.setUp()
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
        # The first step's page also prepares the saved PDF as a template; that has its own checks.
        page.on("console", lambda message: errors.append(message.text) if message.type == "error"
                and not message.location.get("url", "").endswith("/templates/prepare") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 and not response.url.endswith("/templates/prepare") else None)
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics")
        expect(page.locator("[data-rubric-question]")).to_have_count(0)
        expect(page.locator(".rubric-upload-first")).to_contain_text("draft the rubrics of every version at once")
        page.wait_for_load_state("networkidle")  # The saved PDF's preparation redraws the slots once it settles.
        slots = page.locator("[data-template-slot]")
        expect(slots).to_have_count(2)
        # One button for the whole assignment; a version slot has none of its own.
        expect(page.locator('[data-grading-action="draft-rubrics"]')).to_have_count(0)
        block = page.locator("[data-draft-all]")
        dialog = page.locator("#dialog")
        for index in range(2):
            slots.nth(index).locator(f'[name="template-{index}"]').select_option(fixture.blank["id"])
        block.get_by_role("button", name="Draft rubrics for all versions", exact=True).click()
        dialog.get_by_role("button", name="Close", exact=True).click()
        assert fixture.frq.assignment_templates(fixture.plan["id"])["generations"] == [], "Opening the dialog queues nothing"
        block.get_by_role("button", name="Draft rubrics for all versions", exact=True).click()
        expect(dialog.get_by_label("Scoring", exact=True)).to_have_value("positive")
        expect(dialog.locator(".grading-rule-list li")).to_have_count(2)
        expect(dialog).not_to_contain_text("No blank template chosen")
        screenshots(page, "rubric-draft-all-dialog")
        dialog.get_by_role("button", name="Draft all versions", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(block).to_contain_text("Drafting rubrics")
        expect(block.get_by_role("button", name="Cancel drafting", exact=True)).to_be_visible()
        fixture.frq.process_cloud()
        expect(block).to_contain_text("2 problems drafted for all 2 versions", timeout=20000)
        assert fixture.uploaded_questions() == [], "Drafting creates nothing"
        block.get_by_role("button", name="Review and create questions", exact=True).click()
        problems = dialog.locator("fieldset.rubric-family")
        expect(problems).to_have_count(2)
        first, second = problems.nth(0), problems.nth(1)
        expect(first.locator(".rubric-family-version")).to_have_count(2)
        expect(first.locator("[data-line]").first.locator("[data-wording]")).to_have_count(2)
        # Each line's clarification for the graders is folded under it, per version, never part of the wording (2026-09-28).
        expect(first.locator("[data-line]").first.locator("[data-clarification]")).to_have_count(2)
        first.locator("[data-line]").nth(1).locator("summary").click()
        first.get_by_label("Problem 1 line 2 Version A clarification", exact=True).fill("Any equivalent of 3x = 18 counts, scaled or rearranged.")
        expect(first.locator("[data-line]").first.locator('input[type="number"]')).to_have_count(1)  # One point value per line.
        expect(first.locator(".rubric-draft-preview .katex").first).to_be_visible()
        screenshots(page, "rubric-draft-all-review")
        first.get_by_label("Problem 1 line 2 Version B", exact=True).fill("Adds 8 to both sides of $$3x - 8 = 13$$")
        expect(first.locator("[data-line]").nth(1).locator(".katex").nth(1)).to_be_visible()
        # LINKED VERSIONS (2026-10-02): each line shows the writer's mark across the versions, and the instructor may change it.
        expect(first.get_by_label("Problem 1 line 3 across the versions", exact=True)).to_have_value("related")
        expect(first.get_by_label("Problem 1 line 1 across the versions", exact=True)).to_have_value("same")
        first.get_by_label("Problem 1 line 2 across the versions", exact=True).select_option("related")
        first.get_by_label("Problem 1 line 2 points", exact=True).fill("4")
        first.get_by_label("Problem 1 line 3 points", exact=True).fill("6")
        first.get_by_role("button", name="Add a line", exact=True).click()
        expect(first.locator("[data-line]")).to_have_count(5)
        first.get_by_label("Problem 1 line 5 Version A", exact=True).fill("Writes the answer as a sentence")
        second.get_by_label("Create for Version B").uncheck()
        page.get_by_role("button", name="Create selected questions", exact=True).click()
        expect(dialog).not_to_be_visible()
        # ACCEPTING A DRAFT READIES IT (2026-10-11): the toast says what the rubric check waits for, and step 1 asks for no confirmation.
        expect(page.locator("#toast")).to_contain_text("3 questions created. Next, confirm the blank templates. The rubric check starts by itself once the answer areas are confirmed and the answer keys are saved.")
        expect(page.locator("[data-rubric-question]")).to_have_count(3)
        expect(page.locator("[data-step-lead]")).to_contain_text("Every rubric is confirmed for review.")
        expect(page.get_by_role("button", name="Confirm all for rubric review", exact=True)).to_have_count(0)
        questions = {(q["exam_version"], q["content"]["printed_number"]): q["rubric"]["rules"]["items"] for q in fixture.uploaded_questions()}
        assert sorted(questions) == [("A", "1"), ("A", "2"), ("B", "1")], sorted(questions)
        assert [(i["id"], i["points"]) for i in questions["A", "1"]] == [("R1", 10), ("R2", 4), ("R3", 6), ("R4", 0), ("R5", 0)], questions["A", "1"]
        # The line added for Version A alone is left out of Version B, whose lines keep the same IDs and points.
        assert [(i["id"], i["points"]) for i in questions["B", "1"]] == [("R1", 10), ("R2", 4), ("R3", 6), ("R4", 0)], questions["B", "1"]
        assert questions["B", "1"][1]["description"] == "Adds 8 to both sides of $$3x - 8 = 13$$"
        kinds = next(q["rubric"]["compiler"]["line_kinds"] for q in fixture.uploaded_questions() if (q["exam_version"], q["content"]["printed_number"]) == ("B", "1"))
        assert kinds == {"R1": "same", "R2": "related", "R3": "related", "R4": "same"}, kinds  # the added line was not marked
        # The clarifications became the created lines' comments for the rubric review; the rubric itself carries none.
        comments = {(q["exam_version"], q["content"]["printed_number"]): q["question_id"] for q in fixture.uploaded_questions()}
        readiness = fixture.frq.rubric_quality(fixture.plan["id"])["readiness"]
        assert readiness[comments["A", "1"]]["comments"].get("R2") == "Any equivalent of 3x = 18 counts, scaled or rearranged.", readiness[comments["A", "1"]]
        assert all("clarification" not in item for items in questions.values() for item in items)
        expect(block).to_contain_text("3 questions created")
        # A question that already exists offers its own box to replace only its rubric (requirement, 2026-09-27), never ticked for
        # you; Select all ticks and unticks every open box; its number names the question and cannot be edited.
        before = {(q["exam_version"], q["content"]["printed_number"]): q["rubric_id"] for q in fixture.uploaded_questions()}
        block.locator("summary", has_text="Draft rubrics with AI").click()  # Folded once every question has a rubric.
        block.get_by_role("button", name="Review drafts", exact=True).click()
        first, second = problems.nth(0), problems.nth(1)
        replace = first.get_by_label("Replace its current rubric with this draft")
        expect(replace).to_have_count(2)
        expect(replace.first).not_to_be_checked()
        expect(first.get_by_label("Version A question number", exact=True)).to_have_attribute("readonly", "")
        select_all = dialog.get_by_label("Select all", exact=True)
        expect(select_all).to_have_js_property("indeterminate", True)  # Problem 2 Version B is still ticked to create.
        select_all.check()
        for box in (replace.first, replace.nth(1), second.get_by_label("Replace its current rubric with this draft"), second.get_by_label("Create for Version B")):
            expect(box).to_be_checked()
        select_all.uncheck()
        expect(replace.first).not_to_be_checked()
        expect(second.get_by_label("Create for Version B")).not_to_be_checked()
        replace.first.check()  # One by one still works.
        expect(select_all).to_have_js_property("indeterminate", True)
        page.get_by_role("button", name="Create or replace selected", exact=True).click()
        expect(dialog).not_to_be_visible()
        after = {(q["exam_version"], q["content"]["printed_number"]): q for q in fixture.uploaded_questions()}
        assert sorted(after) == sorted(before), sorted(after)
        assert after["A", "1"]["rubric_id"] != before["A", "1"], "Version A's rubric was replaced"
        assert [(i["id"], i["points"]) for i in after["A", "1"]["rubric"]["rules"]["items"]] == [("R1", 10), ("R2", 5), ("R3", 5), ("R4", 0)], after["A", "1"]["rubric"]
        assert after["A", "1"]["rubric"]["compiler"]["name"] == "draft-replaced-v1", after["A", "1"]["rubric"]["compiler"]
        assert (after["B", "1"]["rubric_id"], after["A", "2"]["rubric_id"]) == (before["B", "1"], before["A", "2"]), "Only the ticked version changed"
        # Key & Rubric Finalization says what the check of these accepted drafts waits for (the replaced rubric is asked again) instead
        # of asking to run it (ACCEPTING A DRAFT READIES IT, 2026-10-11).
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=answer-key")
        expect(page.locator("[data-step-lead]")).to_contain_text("The rubric check starts by itself once every rubric is confirmed for review, the answer areas are confirmed and the answer keys are saved.")
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Rubric drafting browser passed: one button for every version, template checks, side-by-side review with shared points, per-version wording, created questions, one version's rubric replaced, no sideways scroll on the desktop.")
finally:
    server.close()
