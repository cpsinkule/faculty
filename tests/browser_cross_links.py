"""Cross-links between the pages of one assessment's life, driven through the real UI with synthetic data only.

One exam (MATH 140 · Exam 1, section 501) has a seating chart dated a few days ahead, a DR-center booking, a Response
Card issue sent from the chart, a grading plan and a gradebook column. Every link the information architecture promises
between their owning pages is followed by a click and must land on the right page:
grading <-> exam hub, gradebook column -> Grading and exam hub, seating chart <-> exam hub, the Accommodations DR group
-> exam hub and chart, Home's Coming up -> exam hub and DR schedule, a Response Card issue -> its chart and exam, the
grading PDF link -> the assignment window's Paper step, and the Settings hub rows -> the controls they name.
Nothing here writes through a link: they only navigate (every action keeps its own page and checks).
"""
import io
import re
import sys
import threading
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from workspace_steps import overview_link  # ASSIGNMENT-WORKSPACE "ONE NAVIGATION"
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from exam_workflow.overview import campus_today
from rosters import connect
import test_exam_workflow as workflow
from test_dr_exams import booking, workbook as dr_workbook
from test_exam_workflow import HEADERS


def build():
    """The synthetic assessment. Returns the unittest fixture (app, ids) with everything linked to one exam."""
    fixture = workflow.ExamWorkflowTests("test_scope_is_permanent_and_cross_term_sections_are_rejected")
    fixture.setUp()
    chart = fixture.chart()
    exam_day = (date.fromisoformat(campus_today()) + timedelta(days=4)).isoformat()
    with connect(fixture.path) as db:
        db.execute("UPDATE seating_charts SET exam_date=?,exam_time='7:05 PM' WHERE id=?", (exam_day, chart["id"]))
    # A DR booking of Alice's for this exam: 'Test 1' is Exam 1 by canonical name, so it links to the assignment.
    content = dr_workbook([booking(Section="501", Date=date.fromisoformat(exam_day))])
    preview = fixture.client.post("/api/accommodations/exams/preview", data={"file": (io.BytesIO(content), "dr.xlsx"), "term": "null"}, headers=HEADERS)
    assert preview.status_code == 200, preview.json
    response = fixture.client.post("/api/accommodations/exams/import", data={"file": (io.BytesIO(content), "dr.xlsx"), "term": "null",
        "preview_hash": preview.json["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
    assert response.status_code == 202, response.json
    fixture.inbox.process_one()
    # A Response Card issue sent from the chart (seated students only), in the exam's assignment context.
    cards = fixture.app.extensions["response_cards"]
    template = cards.save_template({"name": "Seat notice", "definition_id": fixture.term["definition_id"], "schema": {"title": "{{course.code}} seat", "fields": [
        {"id": "ack", "type": "acknowledgment", "label": "I have read my seat.", "required": True}]}})
    version = cards.freeze(template["id"], template["version"])
    issue = cards.issue({"request_id": str(uuid.uuid4()), "version_id": version["id"], "term_id": fixture.term["id"], "section_ids": [fixture.section["id"]],
                         "assignment_id": fixture.assignment["id"], "seating_chart_id": chart["id"]})
    # Grading hangs off the exam; the gradebook column references the course-wide assignment.
    plan = fixture.app.extensions["frq_grading"].create_assignment({"event_id": fixture.event["id"]})
    fixture.app.extensions["gradebook"].link_assignment(fixture.term["id"], {"assignment_id": fixture.assignment["id"], "request_id": str(uuid.uuid4())})
    # Course documents: a rubric CSV that a question revision of Quiz 1 (Version B) cites, and a plain syllabus.
    definition = {"level": "course", "id": fixture.term["definition_id"]}
    for name, content in (("3.csv", b"Question 3 rubric"), ("syllabus.txt", b"Syllabus")):
        fixture.inbox.submit({"action": "document.archive", "target": definition, "metadata": {}}, [{"filename": name, "content": content}])
        fixture.inbox.process_one()
    quiz = fixture.documents.create_assignment(fixture.term["definition_id"], "quiz-1", "Quiz 1")
    quiz_version = fixture.documents.register_stamp_pages(quiz["id"], 1)
    now = "2026-09-26T00:00:00+00:00"
    with connect(fixture.path) as db:
        artifact = db.execute("SELECT a.id FROM inbox_artifacts a WHERE a.filename='3.csv' AND a.role='input'").fetchone()[0]
        db.execute("INSERT INTO frq_questions(id,assignment_version_id,question_key,created_at,exam_version) VALUES('xq3',?,'3',?,'B')", (quiz_version["id"], now))
        db.execute("INSERT INTO frq_question_revisions VALUES('xqr3','xq3',1,?,'x',?)", ('{"title": "Question 3"}', now))
        db.execute("INSERT INTO frq_rubric_revisions VALUES('xr3','xq3',1,'',?,'[]','x','x','{}',?)", (artifact, now))
    fixture.chart_record, fixture.issue, fixture.plan, fixture.exam_day = chart, issue, plan, exam_day
    return fixture


def main():
    Path("test-results").mkdir(exist_ok=True)
    fixture = build()
    event, plan, chart, issue = fixture.event, fixture.plan, fixture.chart_record, fixture.issue
    term, assignment = fixture.term, fixture.assignment
    hub = f"#exams/{event['id']}"
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    checked = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(15000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # The page's CSP forbids eval, so waits use URL assertions rather than wait_for_function polling.
            hash_is = lambda value: expect(page).to_have_url(re.compile(re.escape(value) + "$"))
            hash_starts = lambda value: expect(page).to_have_url(re.compile(re.escape(value)))

            def on_hub():
                hash_is(hub)
                expect(page.locator("#page-title")).to_have_text("Exam 1")
                expect(page.locator(".exams-stage").first).to_be_visible()

            # 1. Grading -> exam record: inside the assignment its tabs are the only navigation (ASSIGNMENT-WORKSPACE "ONE
            #    NAVIGATION"), so the record is the overview's Sittings, students and makeups; then the hub's Grading tile back to the plan.
            page.goto(f"{address}/#grading/{plan['id']}")
            overview_link(page, "Sittings, students and makeups").click()
            hash_is(f"{hub}?tab=sittings")
            page.goto("about:blank")  # the record keeps its tab across a hash change: its overview opens on a fresh load
            page.goto(f"{address}/{hub}")
            on_hub()
            grading_tile = page.locator('.exams-stage[data-stage="grading"] .card-foot a')
            href = grading_tile.get_attribute("href")
            # Until a scan is associated the plan waits for the Inbox; the tile says where the work is.
            assert href.startswith(f"#grading/{plan['id']}") or href.startswith("#inbox"), href
            grading_tile.click()
            hash_starts(href.split("?")[0])
            checked.append("grading <-> exam hub")

            # 2. Exam hub -> seating chart (Seating tile), and the chart back to the hub.
            page.goto(f"{address}/{hub}")
            on_hub()
            seating_tile = page.locator('.exams-stage[data-stage="seating"] .card-foot a')
            expect(seating_tile).to_have_attribute("href", f"#seating/{chart['id']}")
            seating_tile.click()
            hash_is(f"#seating/{chart['id']}")
            exam_link = page.locator(".seating-exam-link a")
            expect(exam_link).to_have_attribute("href", hub)
            exam_link.click()
            on_hub()
            checked.append("seating chart <-> exam hub")

            # 3. Exam hub -> Return (stage key `recorded`: the gradebook column of this assignment, pull preview once) and the column's menu back.
            recorded = page.locator('.exams-stage[data-stage="recorded"] .card-foot a')
            expect(recorded).to_have_attribute("href", f"#gradebook/{term['id']}?assignment={assignment['id']}&pull=1")
            recorded.click()
            # The gradebook is each section's tab (2026-09-28): an exam of several sections lists them, each keeping the pull.
            hash_starts("#course/")
            chooser = page.locator('[data-gradebook-page="sections"] a')
            chooser.or_(page.locator("dialog[open]")).first.wait_for()
            if chooser.count():
                chooser.first.click()
            dialog = page.locator("dialog[open]")
            expect(dialog).to_be_visible()
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            page.locator("th[data-item] .gb-head-button").filter(has_text="Exam 1").first.click()
            menu = page.locator("[data-gradebook-menu]")
            expect(menu.locator('[data-menu-action="grading"]')).to_have_attribute("href", f"#grading/{plan['id']}")
            expect(menu.locator('[data-menu-action="exam"]')).to_have_attribute("href", hub)
            menu.locator('[data-menu-action="exam"]').click()
            on_hub()
            page.goto(f"{address}/#gradebook/{term['id']}")
            hash_starts("#course/")
            chooser = page.locator('[data-gradebook-page="sections"] a')
            chooser.or_(page.locator("th[data-item]")).first.wait_for()
            if chooser.count():
                chooser.first.click()
            page.locator("th[data-item] .gb-head-button").filter(has_text="Exam 1").first.click()
            page.locator("[data-gradebook-menu]").locator('[data-menu-action="grading"]').click()
            hash_starts(f"#grading/{plan['id']}")
            expect(page.locator("#page-title")).to_have_text("Exam 1")
            checked.append("gradebook column -> Grading and exam hub; hub Return -> gradebook pull")

            # 4. Exam hub's Exam day (DR bookings) -> the Accommodations DR schedule on this exam's group,
            #    whose links lead to the chart and back to the hub.
            page.goto(f"{address}/{hub}")
            on_hub()
            dr = f"#accommodations?tab=exams&course={quote('MATH 140', safe='')}&exam={quote('Exam 1', safe='')}&when=all"
            day = page.locator('.exams-stage[data-stage="exam_day"] .card-foot a')
            expect(day).to_have_attribute("href", dr)
            day.click()
            hash_starts("#accommodations")
            root = page.locator("#accommodations-section")
            expect(root.get_by_text("Example, Alice").first).to_be_visible()
            expect(root.locator(f'a[href="#seating/{chart["id"]}"]').first).to_be_visible()
            root.get_by_role("link", name=re.compile(r"^Exam hub")).first.click()
            on_hub()
            checked.append("accommodations DR group -> exam hub and chart; hub Exam day -> DR schedule")

            # 5. Home's Coming up -> the exam hub (and the DR row -> its schedule).
            page.goto(f"{address}/#overview")
            coming = page.locator("#home-exams")
            expect(coming.locator(f'a[href="{hub}"]').first).to_be_visible()
            # The DR row names the same exam (its booking says 'Test 1'), so it leads to the hub too.
            expect(coming.locator('.home-coming-item[data-kind="dr"] .home-coming-source')).to_have_attribute("href", hub)
            coming.locator(f'a[href="{hub}"]').first.click()
            on_hub()
            # The Calendar's DR event opens the DR schedule filtered to its course and exam.
            page.goto(f"{address}/#calendar?date={fixture.exam_day}")
            page.locator("[data-calendar-event]").filter(has_text="Test 1").first.click()
            schedule = page.locator("#dialog").get_by_role("link", name=re.compile(r"^Open DR exam schedule"))
            expect(schedule).to_have_attribute("href", f"#accommodations?tab=exams&course={quote('MATH 140', safe='')}&exam={quote('Test 1', safe='')}&when=all")
            schedule.click()
            hash_starts("#accommodations")
            expect(page.locator("#accommodations-section").get_by_text("Example, Alice").first).to_be_visible()
            checked.append("Home Coming up -> exam hub; Calendar DR event -> DR schedule")

            # 6. A Response Card issue -> its chart and its exam.
            page.goto(f"{address}/#response-cards/issue/{issue['id']}")
            links = page.locator(".rc-issue-links")
            expect(links.locator(f'a[href="#seating/{chart["id"]}"]')).to_be_visible()
            links.locator(f'a[href="{hub}"]').click()
            on_hub()
            page.go_back()
            page.locator(f'.rc-issue-links a[href="#seating/{chart["id"]}"]').click()
            hash_is(f"#seating/{chart['id']}")
            # The hub's Notices tile leads to that issue.
            page.goto(f"{address}/{hub}")
            on_hub()
            notices = page.locator('.exams-stage[data-stage="notices"] .card-foot a')
            expect(notices).to_have_attribute("href", re.compile(rf"^#response-cards/issue/{issue['id']}"))
            checked.append("response-card issue -> chart and exam; hub Notices -> issue")

            # 7. Once a cleared scan puts the plan on Grading (stood in for here: this check is about links, not the entry
            #    gate), its context row still leads to the hub, and 'Add the PDF' (no paper registered yet) opens the
            #    assignment window at its Paper step.
            grading = fixture.app.extensions["frq_grading"]
            fixture.enterContext(patch.object(grading, "grading_entry", return_value={"ready": True, "cleared_scans": 1, "pending_scans": 0, "inbox_href": "#inbox"}))
            page.goto(f"{address}/#grading/{plan['id']}?tab=rubrics")
            expect(page.locator("[data-grading-exam]")).to_have_attribute("href", hub)
            # Inside an assignment its paper's step is Create (2026-10-18 one set of words; never "Assignments & printing").
            pdf = page.locator("a[data-grading-add-paper]")
            expect(pdf).to_have_text(re.compile(r"^Add (the PDF|paper) in Create$"))
            # The assignment's Paper step (the term may ride along: the window opens on this term's copy).
            expect(pdf).to_have_attribute("href", re.compile(rf"^#documents\?assignment={re.escape(assignment['id'])}(&term=[^&]+)?&step=version$"))
            pdf.click()
            expect(page.locator('#assignment-wizard .wizard-steps [aria-current="step"]')).to_contain_text("Paper")
            page.keyboard.press("Escape")
            checked.append("grading PDF link -> assignment window Paper step")

            # 8. Course documents on the course home: rubric sources grouped under their assignment and version, the rest
            #    under Other documents, each with its Submission history as an icon link.
            page.goto(f"{address}/#course/{fixture.group['id']}/home")
            documents = page.locator("details.course-documents")
            expect(documents.locator("[data-course-documents-count]")).to_have_text("2")
            documents.locator("summary").first.click()
            resources = page.locator('[data-inbox-resources="course"]')
            quiz = resources.locator(".inbox-resource-group").filter(has_text="Quiz 1 · Version B rubrics")
            expect(quiz).to_have_count(1)
            expect(quiz.locator("summary")).to_contain_text("1")
            quiz.locator("summary").click()
            expect(quiz.locator(".inbox-artifact")).to_contain_text("3.csv")
            expect(quiz.locator(".inbox-artifact small")).to_contain_text("Question 3")
            other = resources.locator(".inbox-resource-group").filter(has_text="Other documents")
            other.locator("summary").click()
            expect(other.locator(".inbox-artifact")).to_contain_text("syllabus.txt")
            expect(other.get_by_role("link", name="Submission history of syllabus.txt")).to_have_attribute("href", re.compile(r"^#inbox/job/"))
            checked.append("course documents grouped by assignment and version")

            # 9. The Settings hub rows open the controls they name.
            def settings_row(name):
                page.goto(f"{address}/#overview")
                page.locator('.sidebar [data-action="settings"]').click()
                page.locator("#dialog").get_by_role("link", name=re.compile(rf"^{re.escape(name)}")).click()

            settings_row("Grading models and compute")
            expect(page.locator("#dialog")).to_contain_text("Compute settings")
            expect(page).not_to_have_url(re.compile(r"settings=1"))
            page.keyboard.press("Escape")
            settings_row("Email delivery")
            hash_is("#response-cards")
            expect(page.locator("#rc-email-settings")).to_have_attribute("open", "")
            settings_row("Codes & API access")
            expect(page.locator("#dialog")).to_contain_text("Submission codes")
            hash_is("#inbox")
            page.keyboard.press("Escape")
            # Recipients are chosen per print job (Export by email): the row opens the full print-jobs list, which a bare #documents
            # no longer is (it leads to Assignments, ASSIGNMENT-WORKSPACE).
            settings_row("Print email recipients")
            hash_is("#documents?tab=batches")
            expect(page.locator("#documents-section")).to_be_visible()
            checked.append("Settings hub rows -> Compute settings, Email settings, Codes & API access, print jobs")

            page.screenshot(path="test-results/cross-links.png", full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.close()
        fixture.doCleanups()
    print("Cross-links followed: " + "; ".join(checked))


if __name__ == "__main__":
    main()
