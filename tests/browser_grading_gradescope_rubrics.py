"""Rubrics & Templates: Import from Gradescope (GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE"), end to end against FakeGradescope.

An empty plan; the Gradescope course has "Quiz 4" with three questions, one of them without rubric items. The dialog proposes the
course, lists the course's assignments, reads the chosen one, ticks what can be imported and imports it; Faculty then shows the two
draft questions. Nothing is written to Gradescope."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from fake_gradescope import EMAIL, PASSWORD, FakeGradescope
from grading import gradescope_web as gw
from rosters import connect
from test_grading_uploads import RubricUploads

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = RubricUploads("test_eight_csvs_create_exact_drafts_from_empty_plan")
fixture.setUp()
fake = FakeGradescope().start()
# The section's own course this term, as Faculty proposes one (its short name ends in the section number).
with connect(fixture.rosters.path) as db:
    term = fixture.frq.exams._event(db, fixture.plan["event_id"]).get("term_label") or ""
course = fake.add_course(f"140-{fixture.section['number']}", "Mathematics for Business and Social Sciences", term)
fake.add_course("140-999", "Another section", term)
fake.add_assignment(course, "Homework 1", 1)
quiz = fake.add_assignment(course, "Quiz 4", 2)
fake.add_question(quiz, 1, "Question 1", 4, [(4, "Correct"), (2, "Setup right, arithmetic wrong"), (0, "No answer")])
fake.add_question(quiz, 2, "Question 2", 6, [("Part (a)", [(3, "Correct derivative"), (1, "Product rule attempted")]), (3, "Part (b) correct")])
fake.add_question(quiz, 3, "Question 3", 2, [])
# A bubble-sheet exam with versions A and B on the coordinator's course (read only), keyed there.
coordinator = fake.add_course("Math 140 for Instructors", "Math 140 for Instructors", term)
sheets, sheet_versions = fake.add_container(coordinator, "Common Exam 2 - Multiple Choice", "AB", 1, bubble_sheet=True)
for letter, answers in (("A", "BCA"), ("B", "DAE")):
    for number, answer in enumerate(answers, 1):
        fake.add_question(sheet_versions[letter], number, f"Question {number}", 4, [], mc=(answer, "exact"))
# Exam 2's written half beside it: choosing it proposes the bubble-sheet half, imported with it.
written = fake.add_assignment(coordinator, "Common Exam 2 - Partial Credit", 2)
fake.add_question(written, 1, "Question 26", 4, [(4, "Correct"), (0, "Incorrect"), (-1, "Not written as ordered pairs")])
credentials = os.path.join(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")), "gradescope.json")
descriptor = os.open(credentials, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
os.write(descriptor, json.dumps({"email": EMAIL, "password": PASSWORD}).encode())
os.close(descriptor)
fixture.app.extensions["gradescope"].settings = gw.Settings(credentials_file=credentials, base=fake.url, pace=0)
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
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" and "400 (BAD REQUEST)" not in message.text
                and not message.location.get("url", "").endswith("/templates/prepare") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 500 and not response.url.endswith("/templates/prepare") else None)
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics")
        expect(page.locator("[data-rubric-question]")).to_have_count(0)
        expect(page.locator(".rubric-upload-first")).to_contain_text("import the rubrics straight from Gradescope")
        page.get_by_role("button", name="Import from Gradescope", exact=True).click()
        dialog = page.locator("#dialog")
        # The only course is proposed; no assignment of its title is, so the dialog asks for it.
        expect(dialog.get_by_label("Gradescope course")).to_have_value(str(course))
        expect(dialog).to_contain_text("Choose the Gradescope assignment")
        dialog.get_by_label("Gradescope assignment").select_option(str(quiz))
        expect(dialog.locator('[name="question"]')).to_have_count(3)
        expect(dialog.locator('[name="question"]:checked')).to_have_count(2)
        expect(dialog.locator('[name="question"]:disabled')).to_have_count(1)
        expect(dialog).to_contain_text("no rubric items on Gradescope yet")
        expect(dialog).to_contain_text("Blank template on Gradescope: 2 pages")
        expect(dialog.get_by_label("Also import the blank template and answer areas")).to_be_checked()
        page.screenshot(path=str(OUTPUT / "rubric-import-gradescope-1500.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        before = [r for r in fake.requests if r["method"] != "GET" and r["path"] != "/login"]
        dialog.get_by_role("button", name="Import", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.locator("[data-rubric-question]")).to_have_count(2)
        questions = fixture.uploaded_questions()
        assert sorted(q["content"]["printed_number"] for q in questions) == ["1", "2"], questions
        second = next(q for q in questions if q["content"]["printed_number"] == "2")
        assert [i["description"] for i in second["rubric"]["rules"]["items"]] == ["Part (a): Correct derivative", "Part (a): Product rule attempted", "Part (b) correct"]
        assert [r for r in fake.requests if r["method"] != "GET" and r["path"] != "/login"] == before == [], "a read of Gradescope only"
        # The blank template came along as an Assignment Template, and each question's Gradescope box is its answer area on it.
        templates = {q["content"]["template_id"] for q in questions}
        assert len(templates) == 1 and None not in templates, questions
        assert all(q["content"]["regions_from"] == "gradescope" and len(q["content"]["regions"]) == 1 for q in questions), questions
        assert any(t["id"] in templates for t in fixture.frq.assignment_templates(fixture.plan["id"])["slots"][0]["options"])
        # Again: both are already in Faculty, so nothing is ticked.
        page.get_by_role("button", name="Import from Gradescope", exact=True).click()
        dialog.get_by_label("Gradescope assignment").select_option(str(quiz))
        expect(dialog.locator('[name="question"]')).to_have_count(3)
        expect(dialog.locator('[name="question"]:checked')).to_have_count(0)
        expect(dialog).to_contain_text("already in Faculty")
        page.get_by_role("button", name="Close", exact=True).click()
        # The bubble sheet key, from the coordinator's course: listed last, read only, saved as the plan's answer key.
        page.get_by_role("button", name="Import from Gradescope", exact=True).click()
        dialog.get_by_label("Gradescope course").select_option(str(coordinator))
        expect(dialog.get_by_label("Gradescope course")).to_contain_text("coordinator’s course (read only)")
        expect(dialog.get_by_label("Gradescope assignment")).to_contain_text("bubble sheet answer key")  # the course's own list, read
        dialog.get_by_label("Gradescope assignment").select_option(str(sheets))
        expect(dialog.locator('[data-gradescope-key-version]')).to_have_count(2)
        expect(dialog.locator('[data-gradescope-key-version="B"]')).to_contain_text("1 D · 2 A · 3 E")
        expect(dialog.get_by_label("Also import the blank template and answer areas")).to_be_hidden()
        page.screenshot(path=str(OUTPUT / "bubble-key-import-gradescope-1500.png"), full_page=True)
        dialog.get_by_role("button", name="Import", exact=True).click()
        expect(dialog).not_to_be_visible()
        with connect(fixture.rosters.path) as db:
            key = fixture.frq.bubble_key(db, fixture.plan["id"])
        assert [[q["answer"] for q in v["questions"]] for v in key["versions"]] == [["B", "C", "A"], ["D", "A", "E"]], key
        assert key["defaults"] == {"points": "4", "scoring": "exact"}, key
        assert not fake.forbidden_hits and all(r["method"] == "GET" for r in fake.coordinator_reads), fake.forbidden_hits
        # Exam 2's rubrics and its bubble sheet key in one import (the key now replaces the revision just saved).
        page.get_by_role("button", name="Import from Gradescope", exact=True).click()
        dialog.get_by_label("Gradescope course").select_option(str(coordinator))
        expect(dialog.get_by_label("Gradescope assignment")).to_contain_text("Partial Credit")
        dialog.get_by_label("Gradescope assignment").select_option(str(written))
        expect(dialog.get_by_label("Also import the answer key of")).to_have_value(str(sheets))
        expect(dialog.locator('[data-gradescope-companion] [data-gradescope-key-version]')).to_have_count(2)
        expect(dialog.locator('[name="question"]:checked')).to_have_count(1)  # the -1 line imports too
        page.screenshot(path=str(OUTPUT / "exam-with-key-import-gradescope-1500.png"), full_page=True)
        dialog.get_by_role("button", name="Import", exact=True).click()
        expect(dialog).not_to_be_visible()
        with connect(fixture.rosters.path) as db:
            again = fixture.frq.bubble_key(db, fixture.plan["id"])
        assert again["revision"] == key["revision"], (again, key)  # the same key: nothing new to save
        q26 = next(q for q in fixture.uploaded_questions() if q["content"]["printed_number"] == "26")
        assert [i["points"] for i in q26["rubric"]["rules"]["items"]] == [4, 0, -1], q26
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Gradescope rubric import browser passed: course proposed, assignment chosen, two rubrics imported with the blank template and their answer areas, one refused with its reason, nothing written to Gradescope; the bubble sheet key imported from the coordinator's course.")
finally:
    server.close()
    fake.stop()
    fixture.doCleanups()
