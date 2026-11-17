"""The term gradebook in a real browser, end to end against the real server: navigation (sidebar, Home, a course page's Home
and section tabs, Grading's and the calendar's links), the compact page header with the grid's actions, the guided start and
"Add all as columns", the key, dropped scores and collapsed categories, Setup and the dialogs in a tab whose modules can no
longer be fetched (a restart), the setup editor (one save with new categories and columns, drafts,
a save from another window, removal refused, archiving), spreadsheet editing (keys, EX/MI, Esc, Delete, paste, copy, sort,
search, a stale write), the column, history, import, link and pull dialogs (conflicts included), the CSV export and the
UIN-free JSON, accommodations and DR bookings beside names, the one appearance at a desktop's width, and touch. No console error, page
error or CSP violation. Synthetic data only: two sections, six fake students, one bubble-sheet exam scored by the real
reader with a saved answer key, so a pull brings real grades.

Run from the project root: python tests/browser_gradebook.py (manage_checks.py browser --browser gradebook).
"""
import io
import json
import re
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from workspace_steps import overview_link  # ASSIGNMENT-WORKSPACE "ONE NAVIGATION"
from app import create_app  # noqa: E402
from bubble_fixture import pdf, sheet  # noqa: E402
from browser_theme import contrast  # noqa: E402
from rosters import connect  # noqa: E402
from scheduling.clock import campus_today  # noqa: E402
from test_accommodations import workbook as accommodations_workbook  # noqa: E402
from test_bubble_sheets import HEADERS, KEY, STUDENTS  # noqa: E402
from test_dr_exams import booking, workbook as dr_workbook  # noqa: E402
from test_rosters import COLUMNS, csv_bytes  # noqa: E402

# Section 501 holds test_bubble_sheets.STUDENTS (Alice, Bob, Carol and Dylan) and these two; section 502 has Sam alone. The
# gradebook is a section tab's view since 2026-09-28 (no combined grid), so the check drives Section 501's.
# Jamie's roster name carries markup: it must be shown as text everywhere and never run.
INJECTION = '<img src=x onerror="window.gradebookInjection=1">'
OTHERS = (("900000011", f"Park, Jamie {INJECTION}"), ("900000022", "Quinn, Riley"))
OTHER_KEY = {**KEY, 1: "A", 2: "AB"}
SAM = ("900000033", "Lee, Sam")
IMPORT_CSV = ("UIN,Name,HW 1,Project\n"
              "987650001,\"Nguyen, Carol\",9,18\n"
              "555000111,\"O'Brien, Dylan\",EX,15\n"
              "999999999,Nobody Here,5,5\n").encode()


def seed(app, client):
    """A current course with sections 501 and 502, both rosters, the course-wide assignment Test 1 and its bubble sheets:
    three sheets read by the real reader (OCR patched), the scan's Inbox review cleared and an answer key saved. Riley has an
    accommodation and Dylan a DR-center booking for Test 1, both imported the ordinary way (Inbox jobs)."""
    inbox, scans, bubbles, frq = (app.extensions[name] for name in ("inbox", "document_scans", "bubble_sheets", "frq_grading"))
    created = client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [501, 502],
                                                "multiple_sections": True, "is_current": True}, headers=HEADERS)
    assert created.status_code == 201, created.json
    state = client.get("/api/state").json
    group = state["course_groups"][0]["id"]
    rosters = {501: [(s["uin"], s["name"]) for s in STUDENTS] + list(OTHERS), 502: [SAM]}
    for number, students in rosters.items():
        rows = [["", name, uin, "Registered", "", "", "", "", "", "", "", f"s{number}{index}@example.test"] for index, (uin, name) in enumerate(students)]
        response = client.post(f"/api/course-groups/{group}/sections/{number}/roster",
                               data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS)
        assert response.status_code == 201, response.json
    assignment = client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "test-1",
                                                                  "title": "Test 1"}, headers=HEADERS).json
    job = inbox.submit({"action": "scan.receive"}, [{"filename": "sheets.pdf", "content": pdf([sheet(KEY, "A"), sheet(OTHER_KEY, "B"), sheet({**KEY, 3: ""}, "A")])}],
                       source="email", sender="scanner", idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
    assert scans.process_one()
    response = client.post(f"/api/bubble-sheets/messages/{job['message_id']}", json={"assignment_id": assignment["id"], "question_count": 10}, headers=HEADERS)
    assert response.status_code == 202, response.json
    reads = [("123456789", "Alice Example"), ("223456780", "Bob Example"), ("987650001", "Carol Nguyen")]
    ocr = lambda content, boxes: {"reads": [{"text": text} for pair in reads for text in pair][:len(boxes)], "seconds": 0}
    with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_fields", side_effect=ocr):
        assert bubbles.process_one()
    with connect(app.config["DATABASE"]) as db:
        status = frq.scan_status(db, job["message_id"])
    frq.review_scan(job["message_id"], {"confirm_review": True, "fingerprint": status["fingerprint"]})
    answers = lambda key: [{"number": n, "answer": key[n]} for n in range(1, 11)]
    frq.save_bubble_key(status["plan"]["id"], {"request_id": str(uuid.uuid4()), "base_revision": None, "defaults": {"points": "2", "scoring": "exact"},
                                              "versions": [{"letter": "A", "questions": answers(KEY)}, {"letter": "B", "questions": answers(OTHER_KEY)}]})
    for route, content in (("/api/accommodations", accommodations_workbook([["Quinn", "Riley", "900000022", "r@example.test", "MATH", "140", "501", "Read",
                                                                             "2026-09-13", "Yes", "", "Synthetic"]])),
                           ("/api/accommodations/exams", dr_workbook([booking(Section="501", UIN="555000111", **{"Last Name": "O'Brien", "First Name": "Dylan",
                                                                                                                  "Exam Type": "Test 1"})]))):
        preview = client.post(f"{route}/preview", data={"file": (io.BytesIO(content), "export.xlsx"), "term": "null"}, headers=HEADERS)
        assert preview.status_code == 200, preview.json
        queued = client.post(f"{route}/import", data={"file": (io.BytesIO(content), "export.xlsx"), "term": "null", "preview_hash": preview.json["preview_hash"],
                                                     "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
        assert queued.status_code == 202, queued.json
        assert inbox.process_one() and inbox.get(queued.json["id"])["status"] == "completed", inbox.get(queued.json["id"])
    with connect(app.config["DATABASE"]) as db:
        term = db.execute("SELECT term_id FROM course_sections WHERE number=501").fetchone()[0]
        sections = dict(db.execute("SELECT number,id FROM course_sections WHERE term_id=?", (term,)).fetchall())
        uins = [row[0] for row in db.execute("SELECT uin FROM student_enrollments")]
    # Quiz 2 is graded in Faculty too (an exam of this term whose plan has an answer key, 5 × 2 points), and Final is not yet:
    # the guided start lists Quiz 2 beside Test 1 and folds Final under "Plan ahead".
    definition = state["course_definitions"][0]["id"]
    quiz = client.post("/api/documents/assignments", json={"definition_id": definition, "key": "quiz-2", "title": "Quiz 2"}, headers=HEADERS).json
    event = frq.exams.create({"term_id": term, "assignment_id": quiz["id"], "section_ids": list(sections.values())})
    quiz_plan = frq.create_assignment({"event_id": event["id"]})
    frq.save_bubble_key(quiz_plan["id"], {"request_id": str(uuid.uuid4()), "base_revision": None, "defaults": {"points": "2", "scoring": "exact"},
                                          "versions": [{"letter": "A", "questions": [{"number": n, "answer": KEY[n]} for n in range(1, 6)]}]})
    final = client.post("/api/documents/assignments", json={"definition_id": definition, "key": "final", "title": "Final"}, headers=HEADERS).json
    return {"group": group, "term": term, "sections": sections, "assignment": assignment, "uins": uins, "plan": status["plan"]["id"],
            "quiz": quiz, "final": final}


def setup_behind(client, base, change):
    """Another window saves the setup: `change(setup)` edits the latest one, which is saved on its own revision."""
    latest = client.get(base).json["setup"]
    setup = {key: value for key, value in latest.items() if key not in ("revision", "created_at", "actor")}
    change(setup)
    response = client.post(f"{base}/setup", json={"base_revision": latest["revision"], "request_id": str(uuid.uuid4()), "setup": setup}, headers=HEADERS)
    assert response.status_code == 200, response.json
    return response.json["setup"]["revision"]


def notes(title, text):
    def change(setup):
        next(item for item in setup["items"] if item["title"] == title)["notes"] = text
    return change


def wait_until(page, expression, timeout=15000):
    """Poll a page expression with page.evaluate. Under the page's CSP (script-src 'self') Playwright's wait_for_function needs
    eval for a string predicate once it has to poll, which the policy refuses: it could only pass on its first check."""
    for _ in range(timeout // 100):
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"Timed out waiting for {expression}")


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate(
        "({viewport: innerWidth, document: document.documentElement.scrollWidth})")


def main():
    expect.set_options(timeout=10000)  # a roster read per request; a busy machine can take more than the default 5 s
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(temporary)
        client = app.test_client()
        data = seed(app, client)
        term, sections = data["term"], data["sections"]
        base = f"/api/gradebook/terms/{term}"
        view = lambda: client.get(base).json
        first = view()
        rows = {student["name"].split(",")[1].split()[0]: student["row"] for student in first["students"] if student["section_id"] == sections[501]}
        assert list(rows) == ["Alice", "Bob", "Carol", "Dylan", "Jamie", "Riley"], [s["name"] for s in first["students"]]
        server = create_server(app, host="127.0.0.1", port=0, threads=8)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text)
                        if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                watch = """window.gradebookCspViolations = [];
                  document.addEventListener('securitypolicyviolation', event =>
                    window.gradebookCspViolations.push([event.violatedDirective, event.blockedURI]));"""
                page.add_init_script(watch)
                score_posts, view_gets = [], []
                page.on("request", lambda request: score_posts.append(request.post_data)
                        if request.method == "POST" and request.url.endswith(f"{base}/scores") else None)
                page.on("request", lambda request: view_gets.append(request.url)
                        if request.method == "GET" and "/api/gradebook/terms/" in request.url and "/export.csv" not in request.url
                        and "/history" not in request.url and "/grading-check" not in request.url else None)
                toast = page.locator("#toast")
                grid_page = page.locator('[data-gradebook-page="grid"]')
                cell = lambda item, name: page.locator(f'td[data-cell][data-item="{item}"][data-row="{rows[name]}"]')
                result = lambda kind, name: page.locator(f'tr[data-row="{rows[name]}"] td[data-result-cell="{kind}"]')
                focused = lambda: page.evaluate("(() => { const n = document.activeElement; return n && n.matches('td') ? [n.dataset.item || n.dataset.resultCell, n.dataset.row] : [n && n.tagName, ''] })()")
                # The grid's actions live in the shell's compact page header: Add column, Import scores and Setup. Add column is the
                # primary only while the gradebook has no column (DESIGN-LANGUAGE §1.4: setup is never the main action).
                heading = page.locator(".page-heading")
                add_column = heading.get_by_role("button", name="Add column", exact=True)
                open_setup = lambda: heading.locator(".page-heading-secondary").get_by_role("link", name="Setup", exact=True).click()
                open_import = lambda: heading.locator(".page-heading-secondary").get_by_role("button", name="Import scores", exact=True).click()
                # Setup's way back to the grid is the header's trail (no separate back link).
                back_to_grid = lambda: page.locator("#breadcrumb").get_by_role("link", name="Gradebook", exact=True).click()
                # The gradebook lives in the course page (requirement, 2026-09-28): every section on the course Home's Gradebook view,
                # one section on each section tab's. Earlier #gradebook addresses are replaced by these.
                group = data["group"]
                # A section tab IS its gradebook, where the roster was (the roster is a link away).
                home_gb, home_re = f"#course/{group}/section/501", re.escape(f"#course/{group}/section/501")

                # ---- Navigation: the sidebar, the term list, a term's grid -----------------------------------------------------------
                page.goto(f"{address}/#home")
                expect(page.locator('a.nav-item[data-view="gradebook"]')).to_have_count(0)  # no sidebar entry any more
                expect(page.locator('.home-shortcuts a[href="#gradebook"]')).to_have_count(0)
                page.goto(f"{address}/#gradebook")  # an earlier address: the gradebooks are on the course pages now
                expect(page).to_have_url(re.compile(r"#current-courses$"))
                page.goto(f"{address}/#home")
                page.locator(".home-course-gradebook").first.click()  # no section named: the sections to choose from
                expect(page).to_have_url(re.compile(re.escape(f"#course/{group}/home/gradebook") + "$"))
                expect(page.locator('[data-gradebook-page="sections"] a')).to_have_text(["Section 501", "Section 502"])
                expect(page.locator("[data-gradebook-grid]")).to_have_count(0)  # no combined grid on the course Home
                expect(page.locator(".course-views")).to_have_count(0)
                page.locator('[data-gradebook-page="sections"]').get_by_role("link", name="Section 501").click()
                expect(page).to_have_url(re.compile(rf"{home_re}$"))
                expect(grid_page.locator("[data-gradebook-grid] tbody tr")).to_have_count(6)
                # The compact header names the term; the trail leads back to every gradebook.
                expect(heading).to_have_attribute("data-variant", "compact")
                expect(page.locator("#page-title")).to_have_text("MATH 140 · Section 501")
                # The band's label repeated the trail ("MATH 140 · Fall 2026 · Gradebook"), so it is left out (DESIGN-LANGUAGE §5, Ledger).
                expect(page.locator("#page-context")).to_be_hidden()
                expect(page.locator("#breadcrumb li")).to_have_text(["Home", "Courses", "MATH 140 · Fall 2026", "Section 501", "Gradebook"])
                expect(page.locator("#breadcrumb").get_by_role("link", name="MATH 140 · Fall 2026", exact=True)).to_have_attribute("href", f"#course/{group}/home")
                expect(page.locator("#section-roster")).to_have_count(0)  # the roster is a link from the section's work
                expect(page.locator("[data-course-roster-link]")).to_have_attribute("href", f"{home_gb}/roster")
                # While the guided start offers graded assignments, its button is the page's one primary: Add column steps back.
                expect(page.locator("#primary-action")).to_be_hidden()
                expect(heading.locator(".page-heading-secondary").get_by_role("button", name="Add column", exact=True)).to_be_visible()
                expect(page).to_have_title(re.compile(r"^Gradebook · MATH 140 · Fall 2026 · Section 501"))
                expect(grid_page.locator(".button.primary")).to_have_count(1)  # the guided start's "Add all 2 as columns"
                # The guided start: one panel, three ways; what is graded in Faculty first, then what can be planned ahead.
                start = page.locator(".gradebook-start")
                expect(start).to_contain_text("No columns yet")
                ready = start.locator('[data-gradebook-way="grading"] > .gradebook-assignments li')
                expect(ready).to_have_count(2)
                expect(ready.nth(0)).to_contain_text("Quiz 2")
                expect(ready.nth(0)).to_contain_text("Bubble · 10 pts")
                expect(ready.nth(1)).to_contain_text("Test 1")
                expect(ready.nth(1).get_by_role("button", name="Add and pull Test 1")).to_be_visible()
                expect(start.locator("[data-gradebook-add-all]")).to_have_text("Add all 2 as columns")
                later = start.locator(".gradebook-plan-ahead")
                expect(later.locator("summary")).to_contain_text("Plan ahead")
                expect(later.locator("summary")).to_contain_text("1 not graded yet")
                expect(later).not_to_have_attribute("open", "")
                expect(later.locator("li")).to_contain_text("Final")
                expect(start.locator('[data-gradebook-way="import"]').get_by_role("button", name="Import scores")).to_be_visible()
                expect(start.locator('[data-gradebook-way="setup"]').get_by_role("link", name="Open Setup")).to_have_attribute("href", f"{home_gb}/setup")
                expect(page.locator(".gradebook-unlinked")).to_have_count(0)  # the start panel holds that list until a column exists
                expect(page.locator("[data-gradebook-key]")).to_be_hidden()  # no mark to explain yet
                accommodation = grid_page.locator(f'tr[data-row="{rows["Riley"]}"] .gb-acc')
                expect(accommodation.locator("[aria-hidden]")).to_have_text("ACC")
                expect(accommodation.locator(".sr-only")).to_have_text("Accommodations: Extra Time 1.50x")  # what a screen reader hears
                expect(accommodation).to_have_attribute("title", "Accommodations: Extra Time 1.50x")
                expect(grid_page.locator(f'tr[data-row="{rows["Jamie"]}"] .gb-name-text')).to_have_attribute("title", f"Park, Jamie {INJECTION}")
                expect(grid_page.locator(".gb-acc")).to_have_count(1)
                expect(grid_page.locator(f'tr[data-row="{rows["Jamie"]}"] .gb-name-text')).to_have_text(f"Park, Jamie {INJECTION}")
                # A section tab shows that section's gradebook only (no section choice), its export that section's.
                page.goto(f"{address}/#course/{group}/section/502?from=all-courses")
                expect(grid_page.locator("[data-gradebook-section]")).to_have_count(0)
                expect(grid_page.locator("[data-gradebook-grid] tbody tr")).to_have_count(1)
                expect(grid_page.locator("[data-gradebook-export]")).to_have_attribute("href", f"{base}/export.csv?section={sections[502]}")
                expect(page.locator("#page-title")).to_have_text("MATH 140 · Section 502")
                # The setup is the term's: an address naming no section opens it from the section opened last (502 just now).
                page.goto(f"{address}/#gradebook/{term}/setup")
                expect(page).to_have_url(re.compile(re.escape(f"#course/{group}/section/502/setup") + "$"))
                expect(page.locator(".gbs .gbs-state")).to_have_text("Not saved yet · the default setup")
                # An earlier address with a section opens that section's tab.
                page.goto(f"{address}/#gradebook/{term}?section={sections[501]}")
                expect(page).to_have_url(re.compile(rf"{home_re}$"))
                # The roster is the Roster link's page, which links back to the gradebook.
                page.locator("[data-course-roster-link]").click()
                expect(page.locator("#section-roster")).to_contain_text("Park, Jamie")
                expect(page.locator("[data-gradebook-grid]")).to_have_count(0)
                page.locator(".course-work").get_by_role("link", name="Section 501 gradebook").click()
                expect(page).to_have_url(re.compile(rf"{home_re}$"))
                expect(grid_page.locator("[data-gradebook-grid] tbody tr")).to_have_count(6)

                # ---- Setup: two categories and two columns in one save, weighted -----------------------------------------------------
                open_setup()
                setup = page.locator(".gbs")
                expect(setup.locator(".gbs-state")).to_have_text("Not saved yet · the default setup")
                expect(page.locator("#page-title")).to_have_text("Gradebook setup")
                expect(page.locator("#breadcrumb li")).to_have_text(["Home", "Courses", "MATH 140 · Fall 2026", "Section 501", "Gradebook", "Setup"])
                expect(page.locator("#breadcrumb").get_by_role("link", name="Gradebook", exact=True)).to_have_attribute("href", home_gb)
                expect(page.locator("#primary-action")).to_be_hidden()
                for name, weight, drop in (("Homework", "40", "1"), ("Exams", "60", "0")):
                    setup.locator('[data-act="cat-add"]').click()
                    row = setup.locator(".gbs-categories tbody tr").last
                    row.locator('[data-field="cat.name"]').fill(name)
                    row.locator('[data-field="cat.weight"]').fill(weight)
                    row.locator('[data-field="cat.drop"]').fill(drop)
                for title, category, points in (("HW 1", "Homework", "10"), ("Midterm", "Exams", "100")):
                    setup.locator('[data-act="item-add"]').click()
                    row = setup.locator(".gbs-items tbody tr[data-key]").last  # a column's row (its "More" row follows it)
                    row.locator('[data-field="item.title"]').fill(title)
                    row.locator('[data-field="item.category"]').select_option(label=category)
                    row.locator('[data-field="item.points"]').fill(points)
                setup.locator('input[name="gbs-mode"][value="weighted"]').check()
                expect(setup.locator("[data-weights]")).to_have_text("Weights add up to 100.")
                # The Columns table fits its panel: sections, the linked assignment and notes open under a row's More; Remove is a
                # quiet × with the same name; each panel says one line and keeps the rest under "How it counts".
                columns_region = setup.locator('[data-scroll="items"]')
                assert columns_region.evaluate("n => n.scrollWidth <= n.clientWidth + 1"), columns_region.evaluate("n => [n.scrollWidth, n.clientWidth]")
                first_row = setup.locator(".gbs-items tbody tr[data-key]").first
                expect(first_row.get_by_role("button", name="Remove HW 1")).to_have_text("×")
                more_row = setup.locator(".gbs-items tr.gbs-more").first
                expect(more_row).to_be_hidden()
                first_row.locator('[data-act="item-more"]').click()
                expect(more_row).to_be_visible()
                expect(more_row.locator('[data-field="item.assignment"]')).to_be_visible()
                page.screenshot(path=str(output / "gradebook-setup-desktop.png"), full_page=True)
                expect(first_row.locator('[data-act="item-more"]')).to_have_attribute("aria-expanded", "true")
                first_row.locator('[data-act="item-more"]').click()
                expect(more_row).to_be_hidden()
                expect(setup.locator(".gbs-how")).to_have_count(4)  # with "How posting in Canvas works" (POSTING GRADES, 2026-10-08)
                expect(setup.locator(".gbs-how").first).not_to_have_attribute("open", "")
                expect(setup.locator(".gbs-panel.card")).to_have_count(4)
                # Unsaved setup changes survive navigation inside the workspace; only unloading the tab asks first.
                assert page.evaluate("() => { const e = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(e); return e.defaultPrevented; }")
                expect(setup.locator(".gbs-state")).to_have_text("Unsaved changes")
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"{base}/setup")) as saved:
                    setup.locator('[data-act="save"]').click()
                assert saved.value.status == 200, saved.value.text()
                sent = json.loads(saved.value.request.post_data)
                assert sent["base_revision"] is None and len(sent["setup"]["categories"]) == 2 and len(sent["setup"]["items"]) == 2, sent
                assert all("ref" in category and "id" not in category for category in sent["setup"]["categories"]), sent
                expect(page).to_have_url(re.compile(rf"{home_re}$"))
                expect(toast).to_contain_text("Gradebook setup saved")
                saved_view = view()
                assert saved_view["setup"]["revision"] == 1 and saved_view["setup"]["mode"] == "weighted", saved_view["setup"]
                items = {item["title"]: item["id"] for item in saved_view["setup"]["items"]}
                cats = {category["name"]: category for category in saved_view["setup"]["categories"]}
                assert [item["category_id"] for item in saved_view["setup"]["items"]] == [cats["Homework"]["id"], cats["Exams"]["id"]]
                assert (cats["Homework"]["weight"], cats["Homework"]["drop_lowest"], cats["Exams"]["weight"]) == ("40", 1, "60"), cats
                bands = grid_page.locator(".gb-band")
                expect(bands).to_have_text(["Homework · 40% · drop 1", "Exams · 60%", "Categories", "Results"])
                expect(grid_page.locator(".gb-heads .gb-head-title")).to_have_text(["HW 1", "Midterm", "Homework %", "Exams %", "Overall", "Letter"])
                # Once a column exists, the assignments without one wait in a closed disclosure.
                expect(page.locator(".gradebook-start")).to_have_count(0)
                expect(page.locator(".gradebook-unlinked > summary")).to_contain_text("Assignments without a column")
                expect(page.locator(".gradebook-unlinked")).not_to_have_attribute("open", "")
                # The revision is shown when setup is opened again (also on a reload of its own address).
                open_setup()
                expect(page).to_have_url(re.compile(rf"{home_re}/setup$"))
                page.reload()
                expect(setup.locator(".gbs-state")).to_have_text(re.compile(r"^Saved · revision 1"))
                assert not page.evaluate("() => { const e = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(e); return e.defaultPrevented; }")
                expect(setup.locator("[data-scale-preview]")).to_contain_text("Preview from today’s percentages")
                # An unsaved draft survives a visit to the grid, which says so, and can be discarded.
                exams_weight = setup.locator(".gbs-categories tbody tr").nth(1).locator('[data-field="cat.weight"]')
                exams_weight.fill("65")
                expect(setup.locator(".gbs-state")).to_have_text("Unsaved changes")
                expect(setup.locator("[data-weights]")).to_contain_text("Weights add up to 105, not 100.")
                back_to_grid()
                expect(grid_page.locator("[data-gradebook-unsaved-setup]")).to_contain_text("not saved yet")
                grid_page.locator("[data-gradebook-unsaved-setup]").get_by_role("link", name="Continue in Setup").click()
                expect(exams_weight).to_have_value("65")
                setup.locator('[data-act="discard"]').click()
                expect(toast).to_contain_text("Setup changes discarded.")
                expect(exams_weight).to_have_value("60")
                expect(setup.locator(".gbs-state")).to_have_text(re.compile(r"^Saved · revision 1"))
                # A save from another window wins: this page's save is refused, nothing is overwritten, and the saved setup loads.
                exams_weight.fill("70")
                revision = setup_behind(client, base, notes("HW 1", "Collected in class"))
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"{base}/setup")) as refused:
                    setup.locator('[data-act="save"]').click()
                assert refused.value.status == 409, refused.value.text()
                expect(setup.locator("[data-feedback]")).to_contain_text("Nothing was saved from this page.")
                expect(exams_weight).to_have_value("70")
                setup.locator('[data-act="reload"]').click()
                expect(toast).to_contain_text("The saved setup is shown.")
                expect(setup.locator(".gbs-state")).to_have_text(re.compile(rf"^Saved · revision {revision}"))
                expect(exams_weight).to_have_value("60")
                expect(setup.locator('.gbs-items [data-field="item.notes"]').first).to_have_value("Collected in class")
                assert view()["setup"]["revision"] == revision == 2
                back_to_grid()
                expect(page).to_have_url(re.compile(rf"{home_re}$"))
                expect(grid_page.locator("[data-gradebook-unsaved-setup]")).to_have_count(0)

                # ---- Editing: Enter moves down, Tab right, EX, MI, Esc, Delete -------------------------------------------------------
                hw, mid = items["HW 1"], items["Midterm"]
                cell(hw, "Alice").click()
                page.keyboard.type("8")
                expect(cell(hw, "Alice").locator("input.gb-input")).to_have_value("8")
                page.keyboard.press("Enter")
                expect(cell(hw, "Alice")).to_have_text("8")
                expect(cell(hw, "Alice")).to_have_class(re.compile(r"\bsrc-instructor\b"))
                assert focused() == [hw, rows["Bob"]], focused()
                expect(result("overall", "Alice")).to_have_text("80.00%")
                expect(result("letter", "Alice")).to_have_text("B")
                key = page.locator("[data-gradebook-key]")
                expect(key).to_be_visible()
                expect(key.locator(".gb-key")).to_have_count(1)  # only the marks the grid shows
                expect(key.locator('.gb-key[data-mark="instructor"]')).to_have_attribute("title", "Typed in Faculty")
                expect(key.locator('.gb-key[data-mark="instructor"] [aria-hidden="true"]').last).to_have_text("Typed")
                # The first score write is listed under Recent changes, from the write's own answer: the view is not read again.
                expect(grid_page.locator(".gradebook-activity summary")).to_contain_text("Recent changes")
                gets = len(view_gets)
                page.wait_for_timeout(700)
                assert len(view_gets) == gets, view_gets[gets:]
                page.keyboard.type("9")
                page.keyboard.press("Tab")
                expect(cell(hw, "Bob")).to_have_text("9")
                assert focused() == [mid, rows["Bob"]], focused()
                page.keyboard.type("ex")
                page.keyboard.press("Enter")
                expect(cell(mid, "Bob")).to_have_text("EX")
                expect(cell(mid, "Bob")).to_have_class(re.compile(r"\bst-excused\b"))
                expect(result("overall", "Bob")).to_have_text("90.00%")
                expect(result("letter", "Bob")).to_have_text("A")
                assert focused() == [mid, rows["Carol"]], focused()
                page.keyboard.type("mi")
                page.keyboard.press("Enter")
                expect(cell(mid, "Carol")).to_have_class(re.compile(r"\bst-missing\b"))
                expect(cell(mid, "Carol")).to_contain_text("MI")
                expect(result("overall", "Carol")).to_have_text("0.00%")
                expect(result("letter", "Carol")).to_have_text("F")
                assert focused() == [mid, rows["Dylan"]], focused()
                before = len(score_posts)
                page.keyboard.type("55")
                page.keyboard.press("Escape")
                expect(cell(mid, "Dylan").locator("input")).to_have_count(0)
                expect(cell(mid, "Dylan")).to_have_text("")
                assert focused() == [mid, rows["Dylan"]], focused()
                page.keyboard.press("ArrowUp")
                assert focused() == [mid, rows["Carol"]], focused()
                page.keyboard.press("Delete")
                expect(cell(mid, "Carol")).to_have_text("")
                expect(result("overall", "Carol")).to_have_text("—")
                expect(result("letter", "Carol")).to_have_text("—")
                wait_until(page, "() => !document.querySelector('td.is-pending')")
                assert len(score_posts) == before + 1, score_posts[before:]  # Esc sent nothing; Delete sent one clear
                assert json.loads(score_posts[-1])["changes"][0]["status"] == "cleared"
                current = view()["cells"]
                assert current[hw][rows["Alice"]]["points"] == "8" and current[mid][rows["Bob"]]["status"] == "excused", current
                assert rows["Carol"] not in current.get(mid, {}), current
                # Something that is not a score is refused in place: nothing is sent and the editor stays open.
                before = len(score_posts)
                cell(mid, "Dylan").click()
                page.keyboard.type("abc")
                page.keyboard.press("Enter")
                expect(toast).to_contain_text("is not a score")
                expect(cell(mid, "Dylan").locator("input.gb-input")).to_have_attribute("aria-invalid", "true")
                page.keyboard.press("Escape")
                expect(cell(mid, "Dylan").locator("input")).to_have_count(0)
                assert len(score_posts) == before
                # Arrow keys: Up from the first row reaches the column headings; Alt+Down opens a column's options.
                cell(hw, "Alice").click()
                page.keyboard.press("ArrowUp")
                assert page.evaluate("document.activeElement.closest('th')?.dataset.item") == hw
                page.keyboard.press("Alt+ArrowDown")
                expect(page.locator("#gradebook-column-menu")).to_be_visible()
                expect(page.locator("#gradebook-column-menu")).to_have_attribute("role", "dialog")
                expect(page.locator("#gradebook-column-menu .gradebook-menu-title")).to_contain_text("HW 1")
                page.keyboard.press("Escape")
                expect(page.locator("#gradebook-column-menu")).to_be_hidden()
                expect(grid_page.locator(f'th[data-item="{hw}"] .gb-head-button')).to_be_focused()  # focus returns to the heading
                # Left from the first heading reaches the corner's sort buttons; Down goes back to the first student.
                page.keyboard.press("ArrowLeft")
                expect(grid_page.locator('.gb-corner [data-sort="section"]')).to_be_focused()
                page.keyboard.press("ArrowLeft")
                expect(grid_page.locator('.gb-corner [data-sort="name"]')).to_be_focused()
                page.keyboard.press("ArrowDown")
                assert focused() == [hw, rows["Alice"]], focused()
                # The whole grid is one tab stop: Tab leaves it, and Shift+Tab comes back to the same cell.
                inside = "Boolean(document.activeElement.closest('[data-gradebook-scroll]'))"
                page.keyboard.press("Tab")
                assert not page.evaluate(inside)
                page.keyboard.press("Shift+Tab")
                assert focused() == [hw, rows["Alice"]], focused()
                page.keyboard.press("Shift+Tab")
                assert not page.evaluate(inside)
                cell(hw, "Alice").click()
                # Find a student, then sort by the overall percentage (blanks last) and back by name.
                search = grid_page.locator("[data-gradebook-search]")
                search.fill("bob")
                expect(grid_page.locator("[data-gradebook-grid] tbody tr:visible")).to_have_count(1)
                expect(grid_page.locator("[data-gradebook-summary]")).to_contain_text("1 of 6 students")
                search.fill("")
                expect(grid_page.locator("[data-gradebook-grid] tbody tr:visible")).to_have_count(6)
                names = lambda: [text.split()[1] for text in grid_page.locator("tbody .gb-name-text").all_inner_texts()]
                grid_page.locator('th[data-result="overall"] [data-sort]').click()
                expect(grid_page.locator('th[data-result="overall"]')).to_have_attribute("aria-sort", "ascending")
                assert names()[:2] == ["Alice", "Bob"], names()
                grid_page.locator('th[data-result="overall"] [data-sort]').click()
                expect(grid_page.locator('th[data-result="overall"]')).to_have_attribute("aria-sort", "descending")
                assert names()[:2] == ["Bob", "Alice"], names()
                grid_page.locator('.gb-corner [data-sort="name"]').click()
                assert names() == ["Alice", "Bob", "Carol", "Dylan", "Jamie", "Riley"], names()

                # ---- Column dialog: add a column, edit its points; the header menu's statistics --------------------------------------
                add_column.click()
                form = page.locator("#gradebook-column-form")
                expect(form).to_be_visible()
                form.locator('[name="title"]').fill("Quiz 1")
                form.locator('[name="category_id"]').select_option(label="Homework")
                # Choosing a linked assignment suggests grading's maximum (10 questions × 2 points) and fills only a blank field.
                assignment_select = form.locator('[data-field="col.assignment"]')
                assignment_select.select_option(data["assignment"]["id"])
                expect(form.locator('[name="points"]')).to_have_value("20")
                expect(form.locator("[data-maximum]")).to_have_text("Grading’s maximum: 20 points")
                form.locator('[name="points"]').fill("5")
                expect(form.locator("[data-use-maximum]")).to_have_text("Use 20")
                assignment_select.select_option("")
                expect(form.locator("[data-maximum]")).to_be_hidden()
                assignment_select.select_option(data["assignment"]["id"])
                expect(form.locator('[name="points"]')).to_have_value("5")
                assignment_select.select_option("")
                expect(form.locator('[name="title"]')).to_have_value("Quiz 1")
                # Another window saves the setup first: this column is not saved, the entries stay, and a reload lets it save.
                revision = setup_behind(client, base, notes("Midterm", "Room HECC 105"))
                form.locator('button[type="submit"]').click()
                expect(form.locator("[data-conflict]")).to_contain_text("This column was not saved")
                form.locator("[data-conflict] [data-reload]").click()
                expect(form.locator("[data-conflict]")).to_contain_text(f"The saved setup (revision {revision}) is loaded")
                expect(form.locator('[name="title"]')).to_have_value("Quiz 1")
                form.locator('button[type="submit"]').click()
                expect(form).to_be_hidden()
                expect(toast).to_contain_text("Column “Quiz 1” added")
                items = {item["title"]: item["id"] for item in view()["setup"]["items"]}
                quiz = items["Quiz 1"]
                expect(grid_page.locator(".gb-heads .gb-head-title")).to_have_text(["HW 1", "Quiz 1", "Midterm", "Homework %", "Exams %", "Overall", "Letter"])
                head = grid_page.locator(f'th.gb-head[data-item="{quiz}"]')
                expect(head.locator(".gb-head-meta")).to_have_text("5 pts")
                head.locator("[data-column-menu]").click()
                menu = page.locator("#gradebook-column-menu")
                expect(menu).to_be_visible()
                menu.get_by_role("button", name="Edit column").click()
                form = page.locator("#gradebook-column-form")
                expect(form.locator('[name="points"]')).to_have_value("5")
                form.locator('[name="points"]').fill("6")
                due = datetime.now(ZoneInfo("America/Chicago")).date()  # this month, so the calendar shows it
                form.locator('[name="due_date"]').fill(due.isoformat())
                form.locator('button[type="submit"]').click()
                expect(form).to_be_hidden()
                expect(head.locator(".gb-head-meta")).to_have_text(f"6 pts · {due.strftime('%b')} {due.day}")
                expect(head.locator(".gb-head-button")).to_be_focused()  # the save redrew the grid; focus returns to the heading
                assert view()["setup"]["revision"] == revision + 2
                assert next(item for item in view()["setup"]["items"] if item["title"] == "Midterm")["notes"] == "Room HECC 105"
                grid_page.locator(f'th.gb-head[data-item="{hw}"] [data-column-menu]').click()
                stats = menu.locator("[data-gradebook-stats]")
                expect(stats).to_be_visible()
                expect(stats.locator("div", has=page.locator("dt", has_text="Scored")).locator("dd")).to_have_text("2")
                expect(stats.locator("div", has=page.locator("dt", has_text="Empty")).locator("dd")).to_have_text("4")
                expect(stats.locator("div", has=page.locator("dt", has_text="Mean")).locator("dd")).to_contain_text("8.5")
                expect(stats.locator("div", has=page.locator("dt", has_text="High")).locator("dd")).to_have_text("9")
                page.keyboard.press("Escape")
                expect(menu).to_be_hidden()

                # ---- Paste a 2 x 3 block: one request, six cells ---------------------------------------------------------------------
                before = len(score_posts)
                cell(hw, "Jamie").click()
                cell(hw, "Jamie").evaluate("""(td) => { const data = new DataTransfer(); data.setData('text/plain', '7\\t4\\t88\\r\\n10\\t5\\t91\\r\\n');
                    td.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true})); }""")
                expect(toast).to_contain_text("Pasted 6 scores")
                for name, values in (("Jamie", ("7", "4", "88")), ("Riley", ("10", "5", "91"))):
                    for item, value in zip((hw, quiz, mid), values):
                        expect(cell(item, name)).to_have_text(value)
                assert len(score_posts) == before + 1, score_posts[before:]
                assert len(json.loads(score_posts[-1])["changes"]) == 6
                # Homework drops the lower of HW 1 (70%) and Quiz 1 (4/6): 70% · 40 + 88% · 60 = 80.80%.
                expect(result("overall", "Jamie")).to_have_text("80.80%")
                expect(result("letter", "Jamie")).to_have_text("B")
                expect(result("overall", "Riley")).to_have_text("94.60%")
                expect(result("letter", "Riley")).to_have_text("A")
                # Jamie's Quiz 1 is the dropped one: dimmed and struck, and a screen reader hears it.
                expect(cell(quiz, "Jamie")).to_have_class(re.compile(r"\bis-dropped\b"))
                expect(cell(hw, "Jamie")).not_to_have_class(re.compile(r"\bis-dropped\b"))
                cell(quiz, "Jamie").click()
                expect(cell(quiz, "Jamie")).to_have_attribute("aria-label", re.compile(r"Quiz 1: 4 of 6 points, dropped"))
                expect(key.locator('[data-mark="dropped"]')).to_be_visible()
                # A category collapses to its percentage (view memory only) and opens again from its own heading.
                homework = saved_view["setup"]["categories"][0]["id"]
                grid_page.locator(f'.gb-band [data-band-toggle="{homework}"]').click()
                expect(grid_page.locator(".gb-heads .gb-head-title")).to_have_text(["Homework %", "Midterm", "Homework %", "Exams %", "Overall", "Letter"])
                expect(grid_page.locator(".gb-band")).to_have_text(["Homework · 40% · drop 1", "Exams · 60%", "Categories", "Results"])
                expect(grid_page.locator(f'tr[data-row="{rows["Jamie"]}"] td[data-result-cell="band:{homework}"]')).to_have_text("70.00%")
                expect(grid_page.locator("[data-gradebook-summary]")).to_contain_text("3 columns")  # hidden columns still count
                grid_page.locator(f'.gb-collapsed-head [data-band-toggle="{homework}"]').click()
                expect(grid_page.locator(".gb-heads .gb-head-title")).to_have_text(["HW 1", "Quiz 1", "Midterm", "Homework %", "Exams %", "Overall", "Letter"])
                expect(grid_page.locator(f'th.gb-head[data-item="{hw}"] .gb-head-button')).to_be_focused()  # the category's first column
                # From the keyboard: a column's menu collapses its category, and the collapsed heading opens it again.
                page.keyboard.press("Alt+ArrowDown")
                expect(page.locator("#gradebook-column-menu")).to_be_visible()
                page.locator("#gradebook-column-menu").get_by_role("button", name="Collapse Homework to its percentage").click()
                expect(grid_page.locator(f'.gb-collapsed-head [data-band-toggle="{homework}"]')).to_be_focused()
                page.keyboard.press("Enter")
                expect(grid_page.locator(f'th.gb-head[data-item="{hw}"] .gb-head-button')).to_be_focused()

                # ---- A stale write: the cell changed behind the page's back ----------------------------------------------------------
                behind = client.post(f"{base}/scores", json={"request_id": "behind-the-page", "changes": [
                    {"item_id": hw, "row": rows["Alice"], "base": view()["cells"][hw][rows["Alice"]]["id"], "status": "scored", "points": "6"}]}, headers=HEADERS)
                assert behind.status_code == 200, behind.json
                expect(cell(hw, "Alice")).to_have_text("8")  # the page still shows what it read
                cell(hw, "Alice").click()
                page.keyboard.type("7")
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"{base}/scores")) as stale:
                    page.keyboard.press("Enter")
                assert stale.value.status == 409 and stale.value.json()["stale"] == [{"item_id": hw, "row": rows["Alice"]}], stale.value.text()
                expect(toast).to_contain_text("This score was changed elsewhere; the latest is shown.")
                expect(cell(hw, "Alice")).to_have_text("6")
                expect(result("overall", "Alice")).to_have_text("60.00%")
                assert view()["cells"][hw][rows["Alice"]]["points"] == "6"

                # ---- History: revisions with their sources; a note -------------------------------------------------------------------
                cell(hw, "Alice").dblclick()
                history = page.locator("#gradebook-history-form")
                expect(history).to_be_visible()
                entries = history.locator(".gbd-history li")
                expect(entries).to_have_count(2)
                expect(entries.first).to_contain_text("6")
                expect(entries.first).to_contain_text("current")
                expect(entries.first).to_contain_text("Typed in Faculty")
                expect(entries.nth(1)).to_contain_text("8")
                expect(history.locator('[name="points"]')).to_have_value("6")
                history.locator('[name="note"]').fill("Late, accepted with a penalty")
                history.locator('button[type="submit"]').click()
                expect(toast).to_contain_text("Cell saved")
                expect(entries).to_have_count(3)
                expect(entries.first).to_contain_text("Late, accepted with a penalty")
                # A change saved from another window meanwhile: this one is refused and the latest value is filled in.
                behind = client.post(f"{base}/scores", json={"request_id": "behind-the-history", "changes": [
                    {"item_id": hw, "row": rows["Alice"], "base": view()["cells"][hw][rows["Alice"]]["id"], "status": "scored", "points": "6.5"}]}, headers=HEADERS)
                assert behind.status_code == 200, behind.json
                history.locator('[name="points"]').fill("7")
                history.locator('button[type="submit"]').click()
                expect(history.locator("[data-conflict]")).to_contain_text("latest value is shown")
                expect(history.locator('[name="points"]')).to_have_value("6.5")
                expect(entries).to_have_count(4)
                history.locator('[name="points"]').fill("6")
                history.locator('button[type="submit"]').click()
                expect(entries).to_have_count(5)
                expect(entries.first).to_contain_text("Late, accepted with a penalty")  # the note stays with the cell
                history.get_by_role("button", name="Close", exact=True).click()
                expect(history).to_be_hidden()
                expect(cell(hw, "Alice")).to_have_text("6")
                expect(cell(hw, "Alice")).to_have_class(re.compile(r"\bhas-note\b"))
                assert view()["cells"][hw][rows["Alice"]]["note"] == "Late, accepted with a penalty"

                # ---- Import a generic sheet: an existing column, a new one and a row that matches nobody -----------------------------
                open_import()
                dialog = page.locator("#gradebook-import-form")
                expect(dialog).to_be_visible()
                dialog.locator('input[name="file"]').set_input_files({"name": "grades.csv", "mimeType": "text/csv", "buffer": IMPORT_CSV})
                expect(dialog.locator(".gbd-meta").first).to_contain_text("Spreadsheet with a UIN column")
                mapping = dialog.locator("[data-mapping]")
                expect(mapping).to_have_count(2)
                expect(mapping.nth(0)).to_have_value(f"item:{hw}")
                expect(mapping.nth(1)).to_have_value("new")
                expect(dialog.locator("details[data-keep='unmatched'] summary")).to_contain_text("1 row that matches no student")
                dialog.locator("details[data-keep='unmatched'] summary").click()
                expect(dialog.locator("details[data-keep='unmatched']")).to_contain_text("Nobody Here")
                assert "999999999" not in dialog.inner_text()
                # A generic sheet says nothing of a new column's worth: the instructor gives it before anything is imported.
                new_points = dialog.locator("[data-new-points]")
                expect(new_points).to_have_value("")
                expect(dialog.locator("[data-footnote]")).to_contain_text("needs its points")
                expect(dialog.locator('button[type="submit"]')).to_be_disabled()
                new_points.fill("20")
                new_points.press("Tab")
                expect(dialog.locator('[data-count="add"] dd')).to_have_text("4")
                submit = dialog.locator('button[type="submit"]')
                expect(submit).to_have_text("Import 4 scores · 1 new column")
                # The changes open on what Import writes (the 4 scores), not every cell the file was compared with.
                expect(dialog.locator("[data-filter-action]")).to_have_value("change")
                expect(dialog.locator("[data-rows] tbody tr")).to_have_count(4)
                expect(submit).to_be_enabled()
                submit.click()
                expect(dialog).to_be_hidden()
                expect(toast).to_contain_text("Imported 4 scores from grades.csv · 1 new column")
                items = {item["title"]: item["id"] for item in view()["setup"]["items"]}
                project = items["Project"]
                expect(cell(hw, "Carol")).to_have_text("9")
                expect(cell(hw, "Carol")).to_have_class(re.compile(r"\bsrc-import\b"))
                expect(cell(hw, "Dylan")).to_have_text("EX")
                expect(cell(project, "Carol")).to_have_text("18")
                expect(cell(project, "Dylan")).to_have_text("15")
                expect(grid_page.locator(".gb-band")).to_have_text(["Homework · 40% · drop 1", "Exams · 60%", "Uncategorized · not counted", "Categories", "Results"])
                # In a weighted gradebook the new column, without a category, counts toward nothing: the grid says so once, on its
                # heading too, and its action opens that column's settings, where the category is chosen.
                uncounted = grid_page.locator("[data-gradebook-uncounted]")
                expect(uncounted).to_have_text(re.compile("“Project” has no category, so it does not count toward Overall\\."))
                expect(uncounted).to_have_attribute("data-tone", "warning")
                expect(grid_page.locator(f'th.gb-head[data-item="{project}"] .gb-head-meta')).to_have_text("20 pts · not counted (no category)")
                uncounted.get_by_role("button", name="Choose its category").click()
                column_form = page.locator("#gradebook-column-form")
                expect(column_form).to_be_visible()
                expect(column_form).to_contain_text("Project")
                column_form.get_by_role("button", name="Cancel", exact=True).click()
                expect(column_form).to_be_hidden()
                cell(hw, "Carol").click()
                page.keyboard.press("Shift+F10")
                expect(history.locator(".gbd-history li").first).to_contain_text("Imported from grades.csv")
                history.get_by_role("button", name="Close", exact=True).click()
                # The server names uncounted columns term-wide; a section's grid names only its own. Section 502's View is given a
                # Project limited to 501 (in the response only: nothing is saved), so neither its grid nor its notice shows it.
                def project_only_501(route):
                    response = route.fetch()
                    body = response.json()
                    assert project in body["results"].get("uncounted", []), body["results"].get("uncounted")
                    for item in body["setup"]["items"]:
                        if item["id"] == project:
                            item["sections"] = [sections[501]]
                    route.fulfill(status=response.status, json=body, headers={key: value for key, value in response.headers.items()
                                                                              if key.lower() not in ("content-encoding", "content-length")})
                section_502_view = lambda url: "/api/gradebook/terms/" in url and f"section={sections[502]}" in url and "/history" not in url and "/grading-check" not in url
                page.route(section_502_view, project_only_501)
                page.goto(f"{address}/#course/{group}/section/502")
                expect(grid_page.locator("[data-gradebook-grid] tbody tr")).to_have_count(1)
                expect(grid_page.locator(f'th.gb-head[data-item="{hw}"]')).to_have_count(1)
                expect(grid_page.locator(f'th.gb-head[data-item="{project}"]')).to_have_count(0)
                expect(grid_page.locator("[data-gradebook-uncounted]")).to_have_count(0)
                page.unroute(section_502_view, project_only_501)
                page.goto(f"{address}/{home_gb}")
                expect(grid_page.locator("[data-gradebook-uncounted]")).to_contain_text("“Project” has no category")

                # ---- Link Test 1 and pull its bubble-sheet scores --------------------------------------------------------------------
                grid_page.locator(".gradebook-unlinked > summary").click()
                expect(grid_page.locator(".gradebook-unlinked li")).to_contain_text(["Test 1"])
                # The assignment's Gradebook link (its overview; ASSIGNMENT-WORKSPACE "ONE NAVIGATION") names the assignment; the grid
                # offers to add its column.
                page.goto(f"{address}/#grading/{data['plan']}?tab=bubble")
                overview_link(page, "Gradebook").click()
                # No section named: the course's sections to choose from, each keeping the assignment.
                expect(page.locator('[data-gradebook-page="sections"] a')).to_have_text(["Section 501", "Section 502"])
                # The section opened last is the chooser's one primary, marked outside its link (browser memory only).
                last_opened = page.locator('[data-gradebook-page="sections"] [data-gradebook-last]')
                expect(last_opened.locator("a")).to_have_text("Section 501")
                expect(last_opened.locator("a")).to_have_class(re.compile(r"\bprimary\b"))
                expect(last_opened.locator("small")).to_have_text("Last opened")
                expect(page.locator('[data-gradebook-page="sections"] .button.primary')).to_have_count(1)
                page.locator('[data-gradebook-page="sections"]').get_by_role("link", name="Section 501").click()
                expect(page).to_have_url(re.compile(rf"{home_re}\?assignment={data['assignment']['id']}$"))
                notice = grid_page.locator("[data-gradebook-assignment-notice]")
                expect(notice).to_contain_text("Test 1 has no column in this gradebook yet.")
                notice.get_by_role("button", name="Add a column for it").click()
                link = page.locator("#gradebook-link-form")
                expect(link).to_contain_text("Test 1")
                expect(link).to_contain_text("The column starts at 20 points")
                link.locator('[name="category_id"]').select_option(label="Exams")
                link.locator('button[type="submit"]').click()
                expect(link.locator("[role=status]")).to_contain_text("is linked to Test 1")
                # Grading has recorded grades since the column was added (its answer key, the sheets read): a dot on the Pull icon.
                expect(grid_page.locator("th.gb-linked .gb-pull")).to_have_class(re.compile(r"\bis-changed\b"))
                link.locator("[data-pull]").click()
                pull = page.locator("#gradebook-pull-form")
                expect(pull.locator('[data-count="add"] dd')).to_have_text("3")
                expect(pull.locator('[data-count="none"] dd')).to_have_text("3")
                expect(pull.locator('[data-action-row="add"]')).to_have_count(3)
                expect(pull.locator('tr', has_text="O'Brien, Dylan")).to_contain_text("DR-center booking")
                expect(pull.locator('button[type="submit"]')).to_have_text("Pull 3 scores")
                pull.locator('button[type="submit"]').click()
                expect(pull).to_be_hidden()
                expect(toast).to_contain_text("Pulled 3 scores")
                linked = next(item for item in view()["setup"]["items"] if item.get("assignment_id") == data["assignment"]["id"])
                for name, value in (("Alice", "20"), ("Bob", "20"), ("Carol", "18")):
                    expect(cell(linked["id"], name)).to_have_text(value)
                    expect(cell(linked["id"], name)).to_have_class(re.compile(r"\bsrc-grading\b"))
                expect(cell(linked["id"], "Dylan")).to_have_text("DR")  # no score: a DR-center booking for this assignment
                expect(cell(linked["id"], "Dylan").locator(".gb-dr")).to_have_count(1)
                expect(cell(linked["id"], "Jamie")).to_have_text("")
                expect(grid_page.locator("[data-gradebook-assignment-notice]")).to_have_count(0)
                # A linked heading is one block: a blue rule and a small Pull icon; its meta line is the plain one.
                linked_head = grid_page.locator(f'th.gb-head[data-item="{linked["id"]}"]')
                expect(linked_head).to_have_class(re.compile(r"\bgb-linked\b"))
                expect(linked_head.locator(".gb-pull")).to_have_accessible_name("Pull Test 1 from grading")
                expect(linked_head.locator(".gb-pull")).not_to_have_class(re.compile(r"\bis-changed\b"))  # pulled after grading last changed
                assert grid_page.locator(".gb-heads").bounding_box()["height"] <= 90, grid_page.locator(".gb-heads").bounding_box()
                expect(key.locator('[data-mark="linked"]')).to_be_visible()
                expect(key.locator('[data-mark="grading"]')).to_be_visible()
                expect(key.locator('[data-mark="dr"]')).to_be_visible()
                assert linked["points"] == "20", linked
                expect(grid_page.locator(f'th.gb-head[data-item="{linked["id"]}"] .gb-head-meta')).to_have_text("20 pts")
                grid_page.locator(f'th.gb-head[data-item="{linked["id"]}"] [data-column-menu]').click()
                menu.get_by_role("button", name="Edit column").click()
                form = page.locator("#gradebook-column-form")
                expect(form.locator("[data-maximum]")).to_have_text("Grading’s maximum: 20 points")
                form.locator('[name="points"]').fill("25")
                form.locator("[data-use-maximum]").click()
                expect(form.locator('[name="points"]')).to_have_value("20")
                form.get_by_role("button", name="Cancel", exact=True).click()
                expect(form).to_be_hidden()
                expect(grid_page.locator(f'th.gb-head[data-item="{linked["id"]}"]')).to_have_class(re.compile(r"\bis-target\b"))
                # A second pull from the column's own button finds nothing new to write.
                grid_page.locator(f'[data-pull="{linked["id"]}"]').click()
                expect(pull.locator('[data-count="same"] dd')).to_have_text("3")
                expect(pull.locator('button[type="submit"]')).to_be_disabled()
                expect(pull.locator("[data-footnote]")).to_have_text("Nothing to write.")
                pull.get_by_role("button", name="Cancel", exact=True).click()
                expect(pull).to_be_hidden()
                # A value typed by hand is kept unless its Replace is ticked; a change between the preview and the pull is
                # refused, nothing is written, and the preview is read again with the tick kept.
                cell(linked["id"], "Alice").click()
                page.keyboard.type("15")
                page.keyboard.press("Enter")
                expect(cell(linked["id"], "Alice")).to_have_text("15")
                expect(cell(linked["id"], "Alice")).to_have_class(re.compile(r"\bsrc-instructor\b"))
                # DIFFERS FROM GRADING (GRADEBOOK, 2026-10-07): the typed 15 is not grading's 20. The check read after the write marks
                # the cell and its heading, counts it in the attention bar and names it in one notice, with no fix to offer (a pull
                # keeps a typed value); Show puts the cell in focus.
                differs = grid_page.locator("[data-gradebook-differs]")
                expect(cell(linked["id"], "Alice")).to_have_class(re.compile(r"\bis-differs\b"))
                expect(linked_head).to_have_class(re.compile(r"\bgb-differs\b"))
                expect(linked_head.locator("[data-head-differs]")).to_have_text("1 differs")
                expect(differs.locator(".callout-title")).to_have_text("1 grade here differs from Grading")
                expect(differs.locator("li")).to_contain_text("Test 1 · 1 value you typed differs from Grading")
                expect(differs.get_by_role("button", name="Pull", exact=True)).to_have_count(0)
                expect(grid_page.locator('[data-attention="differs"]')).to_have_text("1 grade differs from Grading")
                expect(key.locator('[data-mark="differs"]')).to_be_visible()
                cell(linked["id"], "Bob").hover()
                cell(linked["id"], "Alice").hover()
                expect(cell(linked["id"], "Alice")).to_have_attribute("title", re.compile("Differs from Grading, which has 20"))
                differs.get_by_role("button", name="Show", exact=True).click()
                assert focused() == [linked["id"], rows["Alice"]], focused()
                grid_page.locator(f'[data-pull="{linked["id"]}"]').click()
                expect(pull.locator('[data-count="kept"] dd')).to_have_text("1")
                expect(pull.locator('button[type="submit"]')).to_be_disabled()
                pull.locator(f'[data-replace="{rows["Alice"]}"]').check()
                expect(pull.locator('button[type="submit"]')).to_have_text("Pull 1 score")
                behind = client.post(f"{base}/scores", json={"request_id": "behind-the-pull", "changes": [
                    {"item_id": linked["id"], "row": rows["Alice"], "base": view()["cells"][linked["id"]][rows["Alice"]]["id"], "status": "scored", "points": "16"}]}, headers=HEADERS)
                assert behind.status_code == 200, behind.json
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/pull")) as refused:
                    pull.locator('button[type="submit"]').click()
                assert refused.value.status == 409, refused.value.text()
                expect(pull.locator(".gbd-note")).to_contain_text("Nothing was written.")
                expect(pull.locator('[data-action-row="kept"]')).to_contain_text("16")
                expect(pull.locator(f'[data-replace="{rows["Alice"]}"]')).to_be_checked()
                expect(pull.locator('button[type="submit"]')).to_have_text("Pull 1 score")
                pull.locator('button[type="submit"]').click()
                expect(pull).to_be_hidden()
                expect(toast).to_contain_text("Pulled 1 score")
                expect(cell(linked["id"], "Alice")).to_have_text("20")
                expect(cell(linked["id"], "Alice")).to_have_class(re.compile(r"\bsrc-grading\b"))
                # Grading's value is back: nothing differs, and the marks go.
                expect(grid_page.locator("td.is-differs")).to_have_count(0)
                expect(differs.locator(".callout")).to_have_count(0)
                expect(linked_head.locator("[data-head-differs]")).to_have_count(0)
                cell(linked["id"], "Alice").click()
                grid_page.locator("[data-cell-history]").click()
                expect(history.locator(".gbd-history li")).to_have_count(4)
                expect(history.locator(".gbd-history li").first).to_contain_text("Pulled from grading (Bubble sheets)")
                expect(history.locator(".gbd-history li").nth(1)).to_contain_text("Typed in Faculty")
                history.get_by_role("button", name="Close", exact=True).click()
                # A pulled score cleared by hand stays cleared: the next pull shows it as Cleared and keeps it until Replace.
                cell(linked["id"], "Bob").click()
                page.keyboard.press("Delete")
                expect(cell(linked["id"], "Bob")).to_have_text("")
                expect(grid_page.locator("td.is-pending")).to_have_count(0)
                cleared_cell = client.get(f"{base}/history?item={linked['id']}&row={rows['Bob']}").json["revisions"][0]
                assert (cleared_cell["status"], cleared_cell["source"]) == ("cleared", "instructor"), cleared_cell

                grid_page.locator(f'[data-pull="{linked["id"]}"]').click()
                bob = pull.locator('tr[data-action-row="kept"]', has_text="Example, Bob")
                expect(bob).to_contain_text("Cleared by you")
                expect(bob).to_contain_text("You cleared this cell in the gradebook; tick Replace to fill it.")
                assert "undefined" not in bob.inner_text() and "null" not in bob.inner_text(), bob.inner_text()
                expect(pull.locator('button[type="submit"]')).to_be_disabled()
                expect(pull.locator(f'[data-replace="{rows["Bob"]}"]')).to_have_accessible_name("Fill Example, Bob’s cleared cell")
                pull.locator(f'[data-replace="{rows["Bob"]}"]').check()
                expect(pull.locator('button[type="submit"]')).to_have_text("Pull 1 score")
                pull.locator('button[type="submit"]').click()
                expect(pull).to_be_hidden()
                expect(cell(linked["id"], "Bob")).to_have_text("20")
                expect(cell(linked["id"], "Bob")).to_have_class(re.compile(r"\bsrc-grading\b"))

                # Grading's Export step links here with pull=1: the pull PREVIEW opens once (pulling stays a click) and the address
                # drops pull=1, so a reload or Back does not open it again.
                page.goto(f"{address}/{home_gb}?assignment={data['assignment']['id']}&pull=1")
                expect(pull).to_be_visible()
                expect(pull.locator('[data-count="same"] dd')).to_have_text("3")
                expect(page).to_have_url(re.compile(rf"{home_re}\?assignment={data['assignment']['id']}$"))
                pull.get_by_role("button", name="Cancel", exact=True).click()
                expect(pull).to_be_hidden()
                # Without a column for the assignment, the notice's "Add a column for it" takes the focus; nothing opens by itself.
                page.goto(f"{address}/{home_gb}?assignment={data['final']['id']}&pull=1")
                final_notice = grid_page.locator("[data-gradebook-assignment-notice]")
                expect(final_notice).to_contain_text("Final has no column in this gradebook yet.")
                expect(final_notice.get_by_role("button", name="Add a column for it")).to_be_focused()
                expect(page).to_have_url(re.compile(rf"{home_re}\?assignment={data['final']['id']}$"))
                expect(pull).to_be_hidden()
                page.goto(f"{address}/{home_gb}")
                expect(grid_page.locator("[data-gradebook-assignment-notice]")).to_have_count(0)

                # ---- The Changes page (README "Changes"; requirement, 2026-10-01): every change to the section and the setup -----
                posts = len(score_posts)
                grid_page.locator("[data-gradebook-changes-link]").click()
                expect(page).to_have_url(re.compile(rf"{home_re}/changes$"))
                changes_page = page.locator('[data-gradebook-page="changes"]')
                expect(page.locator("#page-title")).to_have_text("Gradebook changes")  # the title is printed on the page's plate
                expect(page.locator("#breadcrumb").get_by_role("link", name="Gradebook", exact=True)).to_have_attribute("href", home_gb)
                # Newest first: the pull that filled Bob's cleared cell names the cell; the file import names its file and the
                # column it made; the first setup revision is the oldest entry.
                pulled = changes_page.locator('.gbc-entry[data-origin="pull"]').first
                expect(pulled.locator("summary")).to_contain_text("Pulled")
                expect(pulled.locator("summary")).to_contain_text("Example, Bob")
                expect(pulled.locator("summary")).to_contain_text("— → 20")
                imported = changes_page.locator('.gbc-entry[data-origin="file"]').first
                expect(imported.locator("summary")).to_contain_text("Imported grades.csv")
                imported.locator("summary").click()
                expect(imported).to_contain_text("Added column “Project”")
                expect(imported.locator(".data-table")).to_contain_text("Nguyen, Carol")
                # Show older reads the next page (50 a page) until the first change of all is on screen; open entries stay open.
                older = changes_page.locator("[data-gbc-older]")
                for _ in range(20):
                    if not older.count():
                        break
                    older.click()
                    expect(changes_page.locator("[data-gbc-older][disabled]")).to_have_count(0)
                assert not older.count()
                expect(imported.locator("details")).to_have_attribute("open", "")
                expect(changes_page.locator(".gbc-entry").last).to_have_attribute("data-origin", "setup")
                expect(changes_page.locator(".gbc-entry").last.locator("summary")).to_contain_text("Setup changed")
                assert "undefined" not in changes_page.inner_text() and "null" not in changes_page.inner_text(), changes_page.inner_text()[:400]
                no_overflow(page)
                assert len(score_posts) == posts  # reading the changes writes nothing
                back_to_grid()
                expect(page).to_have_url(re.compile(rf"{home_re}$"))
                expect(grid_page).to_be_visible()

                # A paste of a row (a section's view shows only columns that apply to its students, so none is skipped).
                posts = len(score_posts)
                cell(hw, "Riley").evaluate("""(td) => { const data = new DataTransfer(); data.setData('text/plain', '10\\t6\\t91\\t\\t20');
                    td.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true})); }""")
                # It replaces a value already there (Quiz 1: 5 → 6), so it asks first and writes nothing until confirmed.
                paste = page.locator("#gradebook-paste-form")
                expect(paste).to_be_visible()
                expect(paste.locator(".gbd-add-list li")).to_have_count(1)
                expect(paste.locator(".gbd-add-list li")).to_contain_text("Quinn, Riley")
                expect(paste.locator(".gbd-add-list li")).to_contain_text("Quiz 1: 5 → 6")
                assert len(score_posts) == posts
                paste.locator('button[type="submit"]').click()
                expect(paste).to_be_hidden()
                # Toasts stack (at most three); the newest is the paste's.
                expect(toast.locator(".toast-item").last).to_have_text(re.compile(r"^Pasted \d+ scores? \(3 already had that value\)\.$"))
                expect(cell(quiz, "Riley")).to_have_text("6")
                # Ctrl+C copies a cell as it would be typed.
                copied = cell(hw, "Dylan").evaluate("""(td) => { const data = new DataTransfer();
                    td.dispatchEvent(new ClipboardEvent('copy', {clipboardData: data, bubbles: true, cancelable: true})); return data.getData('text/plain'); }""")
                assert copied == "EX", copied
                # Recent changes list every kind of write, newest first.
                grid_page.locator(".gradebook-activity summary").click()
                activity = grid_page.locator(".gradebook-activity li")
                expect(activity.first).to_contain_text("Scores entered")
                expect(activity.first).to_contain_text("Quiz 1")  # the paste above wrote Riley's Quiz 1 only
                expect(activity.filter(has_text="Pull from grading").first).to_contain_text("Test 1")
                expect(activity.filter(has_text="Import").first).to_contain_text("grades.csv")
                # A column's menu sorts by it: lowest first, excused and empty cells after every value.
                grid_page.locator(f'th.gb-head[data-item="{hw}"] [data-column-menu]').click()
                menu.get_by_role("button", name="Sort by this column, lowest first").click()
                expect(grid_page.locator(f'th.gb-head[data-item="{hw}"]')).to_have_attribute("aria-sort", "ascending")
                assert names() == ["Alice", "Jamie", "Bob", "Carol", "Riley", "Dylan"], names()
                grid_page.locator(f'th.gb-head[data-item="{hw}"] [data-column-menu]').click()
                menu.get_by_role("button", name="Sort by this column, highest first").click()
                assert names() == ["Riley", "Bob", "Carol", "Jamie", "Alice", "Dylan"], names()
                grid_page.locator('.gb-corner [data-sort="name"]').click()
                # A second press on an open menu's heading closes it and does not open it again; scrolling the grid closes it.
                trigger = grid_page.locator(f'th.gb-head[data-item="{hw}"] [data-column-menu]')
                trigger.click()
                expect(menu).to_be_visible()
                expect(trigger).to_have_attribute("aria-expanded", "true")
                trigger.click()
                expect(menu).to_be_hidden()
                page.wait_for_timeout(500)
                expect(menu).to_be_hidden()
                expect(trigger).to_have_attribute("aria-expanded", "false")
                trigger.click()
                expect(menu).to_be_visible()
                grid_page.locator("[data-gradebook-scroll]").evaluate("node => { node.scrollLeft += 60; }")
                expect(menu).to_be_hidden()

                # ---- Column options: excuse the empty cells in one write (a DR-center booking stays empty unless ticked) -------------
                # The count is the rows shown now: a name search narrows it, and the dialog says the hidden rows are left alone.
                grid_page.locator("[data-gradebook-search]").fill("jamie")
                grid_page.locator(f'th.gb-head[data-item="{linked["id"]}"] [data-column-menu]').click()
                menu.get_by_role("button", name="Excuse 1 empty cell…", exact=True).click()
                fill = page.locator("#gradebook-fill-form")
                expect(fill).to_contain_text("1 empty cell of Test 1 becomes excused")
                expect(fill).to_contain_text("hidden by the search or a filter are left alone")
                fill.get_by_role("button", name="Cancel").click()
                expect(fill).to_be_hidden()
                grid_page.locator("[data-gradebook-search]").fill("")
                grid_page.locator(f'th.gb-head[data-item="{linked["id"]}"] [data-column-menu]').click()
                menu.get_by_role("button", name="Excuse 2 empty cells (+1 with a DR booking)…").click()
                expect(fill).to_contain_text("2 empty cells of Test 1 become excused")
                expect(fill).to_contain_text("Every student shown now")
                expect(fill.locator("fieldset")).to_contain_text("O'Brien, Dylan")
                expect(fill.locator('button[type="submit"]')).to_have_text("Excuse 2 cells")
                page.screenshot(path=str(output / "gradebook-fill.png"))
                fill.locator('[name="include_dr"]').check()
                expect(fill.locator('button[type="submit"]')).to_have_text("Excuse 3 cells")
                fill.locator('[name="include_dr"]').uncheck()
                before = len(score_posts)
                fill.locator('button[type="submit"]').click()
                expect(fill).to_be_hidden()
                expect(cell(linked["id"], "Jamie")).to_have_text("EX")
                expect(cell(linked["id"], "Riley")).to_have_text("EX")
                expect(cell(linked["id"], "Dylan")).to_have_text("DR")
                expect(grid_page.locator("td.is-pending")).to_have_count(0)
                expect(toast.locator(".toast-item").last).to_contain_text("Excused 2 empty cells in Test 1")
                assert len(score_posts) == before + 1 and len(json.loads(score_posts[-1])["changes"]) == 2, score_posts[before:]

                # ---- A student's record: what the grid holds for them, from the keyboard; Change opens that score's history --------
                cell(hw, "Jamie").click()
                page.keyboard.press("Home")
                page.keyboard.press("ArrowLeft")
                name_button = grid_page.locator(f'tr[data-row="{rows["Jamie"]}"] .gb-name-button')
                expect(name_button).to_be_focused()
                overall_jamie = result("overall", "Jamie").inner_text()
                page.keyboard.press("Enter")
                record = page.locator("#gradebook-student-form")
                expect(record).to_be_visible()
                expect(record).to_contain_text(f"Park, Jamie {INJECTION}")
                expect(record.locator(".gbd-record-facts dd").first).to_have_text(overall_jamie)
                # The grades open folded, one row per category (its percentage, its columns, what was dropped), the columns
                # without a category last; a category's row opens its columns beneath it (requirement, 2026-10-06).
                grades = record.get_by_role("region", name="Grades")
                folds = grades.locator("[data-record-fold]")
                columns_row = lambda title: grades.locator(".gbd-record-items tr", has_text=title)  # a column, not its category
                expect(grades.locator(".gbd-record-category th")).to_have_text(["Homework", "Exams", "Uncategorized"])
                expect(grades.locator('[data-record-fold][aria-expanded="false"]')).to_have_count(3)
                expect(grades.locator(".gbd-record-items:visible")).to_have_count(0)
                jamie = view()["results"]["students"][rows["Jamie"]]
                homework_id = next(category["id"] for category in view()["setup"]["categories"] if category["name"] == "Homework")
                homework_row = grades.locator(".gbd-record-category", has_text="Homework")
                expect(homework_row.locator("td")).to_have_text([f"{jamie['categories'][homework_id]['percent']}%", "2 columns · Dropped: Quiz 1"])
                page.screenshot(path=str(output / "gradebook-record-folded.png"))
                grades.get_by_role("button", name="Homework", exact=True).click()
                expect(folds.first).to_have_attribute("aria-expanded", "true")
                expect(columns_row("Quiz 1")).to_contain_text("dropped")
                expect(columns_row("Midterm")).to_be_hidden()
                grades.locator(".gbd-record-category", has_text="Exams").locator("td").last.click()  # the whole row opens it
                expect(columns_row("Test 1")).to_contain_text("Excused")
                grades.get_by_role("button", name="Uncategorized", exact=True).focus()
                page.keyboard.press("Enter")
                expect(columns_row("Project")).to_be_visible()
                grades.get_by_role("button", name="Exams", exact=True).click()  # and folds it again
                expect(columns_row("Test 1")).to_be_hidden()
                page.screenshot(path=str(output / "gradebook-record.png"))
                record.get_by_role("button", name="Change HW 1").click()
                expect(history).to_be_visible()
                expect(history).to_contain_text("HW 1 · Section 501")
                # Back to the record: the categories open before stay open; a fresh open folds them all again.
                history.get_by_role("button", name=re.compile("^Back to ")).click()
                expect(record).to_be_visible()
                expect(columns_row("HW 1")).to_be_visible()
                expect(columns_row("Test 1")).to_be_hidden()
                record.get_by_role("button", name="Close", exact=True).click()
                expect(record).to_be_hidden()
                name_button.click()
                expect(record).to_be_visible()
                expect(grades.locator(".gbd-record-items:visible")).to_have_count(0)
                record.get_by_role("button", name="Close", exact=True).click()
                expect(history).to_be_hidden()

                # ---- The attention strip: letters, "Below" a letter and the blanks past due (view memory only) --------------------
                # The server's clock, the campus date (`scheduling.clock`): after 7 pm in College Station UTC is already tomorrow, and
                # Quiz 1, due on the campus date it was given above, is due today there, not past due.
                today = campus_today()
                def due_midterm(setup):
                    next(item for item in setup["items"] if item["title"] == "Midterm")["due_date"] = (today - timedelta(days=1)).isoformat()
                setup_behind(client, base, due_midterm)
                grid_page.locator('[data-gradebook-action="refresh"]').click()
                expect(toast.locator(".toast-item").last).to_have_text("Gradebook refreshed.")
                current = view()
                def due(item):
                    # GRADEBOOK "ZERO ONCE DUE": a counted, live column, neither extra credit nor a replacement; linked to grading,
                    # once its plan is certified (Test 1's is not); any other, once its due date is before the campus date.
                    if item.get("archived") or not item.get("counts", True) or item.get("extra_credit") or item.get("replaces_lowest"):
                        return False
                    if (item.get("link") or {}).get("kind") == "grading" and item.get("assignment_id"):
                        return bool(current["links"][item["id"]]["certification"])
                    return bool(item.get("due_date")) and item["due_date"] < today.isoformat()
                overdue = [(item["id"], student["name"].split(",")[1].split()[0]) for item in current["setup"]["items"] for student in current["students"]
                           if student["section_id"] == sections[501]
                           if due(item) and (not item.get("sections") or student["section_id"] in item["sections"])
                           and student["row"] not in current["cells"].get(item["id"], {})
                           and not (item.get("assignment_id") and item["assignment_id"] in student["dr"])]
                assert any(item == mid for item, _ in overdue), overdue
                # GRADEBOOK ZERO ONCE DUE (a new term's default): those are exactly the empty cells the server counts as 0.
                assert current["setup"]["ungraded"] == "due"
                zeros = [(item_id, student["name"].split(",")[1].split()[0]) for student in current["students"] if student["section_id"] == sections[501]
                         for item_id, reason in current["results"]["blanks"].get(student["row"], {}).items() if reason == "zero"]
                assert sorted(zeros) == sorted(overdue), (zeros, overdue)
                for item_id, name in overdue:
                    expect(cell(item_id, name)).to_have_class(re.compile(r"\bis-zeroed\b"))
                    expect(cell(item_id, name)).to_have_text("0")
                expect(key.locator('[data-mark="zeroed"]')).to_be_visible()
                strip = grid_page.locator("[data-gradebook-attention]")
                due_chip = strip.locator('[data-attention="overdue"]')
                expect(due_chip).to_have_text(f"{len(overdue)} empty cell{'' if len(overdue) == 1 else 's'} counted as 0")
                due_chip.click()
                expect(due_chip).to_have_attribute("aria-pressed", "true")
                expect(grid_page.locator("[data-gradebook-grid] tbody tr:visible")).to_have_count(len({name for _, name in overdue}))
                for item_id, name in overdue:
                    expect(cell(item_id, name)).to_have_class(re.compile(r"\bis-overdue\b"))
                expect(key.locator('[data-mark="overdue"]')).to_be_visible()
                strip.get_by_role("button", name="Show every student").click()
                expect(grid_page.locator("[data-gradebook-grid] tbody tr:visible")).to_have_count(6)
                expect(grid_page.locator("td.is-overdue")).to_have_count(0)
                letters = {}
                for student in current["students"]:
                    if student["section_id"] != sections[501]:
                        continue  # the grid is section 501's (a 502 blank past due now gives that student a letter too)
                    letter = current["results"]["students"][student["row"]]["letter"]
                    if letter:
                        letters[letter] = letters.get(letter, 0) + 1
                scale = [entry["letter"] for entry in current["setup"]["scale"]]
                top = next(letter for letter in scale if letter in letters)
                chip = strip.locator(f'[data-attention="letter:{top}"]')
                expect(chip).to_contain_text(str(letters[top]))
                chip.click()
                expect(grid_page.locator("[data-gradebook-grid] tbody tr:visible")).to_have_count(letters[top])
                expect(grid_page.locator("[data-gradebook-summary]")).to_contain_text(f"{letters[top]} of 6 students")
                chip.click()  # a second press clears it
                expect(grid_page.locator("[data-gradebook-grid] tbody tr:visible")).to_have_count(6)
                below = sum(count for letter, count in letters.items() if scale.index(letter) > scale.index(top))
                strip.locator("[data-attention-below]").select_option(top)
                expect(grid_page.locator("[data-gradebook-grid] tbody tr:visible")).to_have_count(below)
                strip.locator("[data-attention-below]").select_option("")
                expect(grid_page.locator("[data-gradebook-grid] tbody tr:visible")).to_have_count(6)

                # ---- An unknown outcome: the write is kept, its cell marked, and only "Check now" sends it again (same request ID) ---
                scores_url = re.compile(rf".*/api/gradebook/terms/{term}/scores$")
                def lost_answer(route):
                    route.fetch()  # the server makes the write ...
                    route.abort()  # ... and its answer never arrives
                page.route(scores_url, lost_answer)
                before = len(score_posts)
                cell(mid, "Riley").click()
                page.keyboard.type("92")
                page.keyboard.press("Enter")
                unknown = grid_page.locator("[data-gradebook-uncertain-notice]")
                expect(unknown).to_contain_text("The outcome of 1 change is unknown")
                expect(cell(mid, "Riley")).to_have_class(re.compile(r"\bis-uncertain\b"))
                page.screenshot(path=str(output / "gradebook-uncertain.png"))
                page.unroute(scores_url, lost_answer)
                page.wait_for_timeout(500)
                assert len(score_posts) == before + 1, score_posts[before:]  # never sent again by itself
                unknown.get_by_role("button", name="Check now").click()
                expect(unknown).to_have_count(0)
                expect(cell(mid, "Riley")).to_have_text("92")
                expect(cell(mid, "Riley")).not_to_have_class(re.compile(r"\bis-uncertain\b"))
                assert len(score_posts) == before + 2 and json.loads(score_posts[-1]) == json.loads(score_posts[-2]), score_posts[before:]
                revisions = client.get(f"{base}/history?item={mid}&row={rows['Riley']}").json["revisions"]
                assert [revision["points"] for revision in revisions[:2]] == ["92", "91"], revisions  # written once

                # ---- Export CSV: the only place with UINs ----------------------------------------------------------------------------
                export = grid_page.locator("[data-gradebook-export]")
                expect(export).to_have_attribute("href", f"{base}/export.csv?section={sections[501]}")
                expect(export).to_have_attribute("data-view-file", f"{base}/export.csv?section={sections[501]}")  # opens as a table (VIEWER)
                download = page.request.get(address + export.get_attribute("href"))
                assert download.status == 200 and download.headers["content-type"].startswith("text/csv"), download.headers
                text = download.body().decode("utf-8-sig")
                header = text.splitlines()[0]
                # Setup order (the grid groups them in category bands).
                assert header.startswith("Name,UIN,Section,HW 1 (10),Midterm (100),Quiz 1 (6),Project (20),Test 1 (20),"), header
                assert header.endswith("Homework %,Exams %,Overall %,Letter"), header
                for uin in data["uins"]:
                    assert (uin in text) == (uin != SAM[0]), uin  # this section's students only
                grid_json = page.request.get(address + base).text()
                assert not any(uin in grid_json for uin in data["uins"]), "a UIN reached the gradebook's JSON"
                assert len(text.splitlines()) == 7, text  # a header and Section 501's six students

                # A 0-point extra-credit column whose only score was cleared: the View's scored_items still names it.
                setup_behind(client, base, lambda setup: setup["items"].append({"title": "Bonus", "points": "0", "extra_credit": True, "category_id": None}))
                bonus = next(item["id"] for item in view()["setup"]["items"] if item["title"] == "Bonus")
                wrote = client.post(f"{base}/scores", json={"request_id": "bonus-set", "changes": [
                    {"item_id": bonus, "row": rows["Bob"], "base": None, "status": "scored", "points": "3"}]}, headers=HEADERS)
                assert wrote.status_code == 200, wrote.json
                wrote = client.post(f"{base}/scores", json={"request_id": "bonus-clear", "changes": [
                    {"item_id": bonus, "row": rows["Bob"], "base": wrote.json["cells"][bonus][rows["Bob"]]["id"], "status": "cleared"}]}, headers=HEADERS)
                assert wrote.status_code == 200 and bonus in view()["scored_items"] and rows["Bob"] not in view()["cells"].get(bonus, {}), wrote.json
                # Setup refuses to remove a column with scores (the server says why); archiving hides it and keeps its scores.
                open_setup()
                bonus_row = setup.locator(".gbs-items tbody tr", has=page.locator('[data-field="item.title"][value="Bonus"]'))
                expect(bonus_row.locator("th small")).to_have_text("Cleared scores (history kept)")
                bonus_row.locator('[data-act="item-remove"]').click()
                expect(setup.locator(".gbs-removed")).to_contain_text("its scores were cleared but their history stays, so the server will keep it")
                setup.locator('.gbs-removed [data-act="undo"]').click()
                expect(setup.locator(".gbs-removed")).to_have_count(0)
                project_row = setup.locator(".gbs-items tbody tr", has=page.locator('[data-field="item.title"][value="Project"]'))
                project_row.locator('[data-act="item-remove"]').click()
                expect(setup.locator(".gbs-removed")).to_contain_text("it has scores, so the server will keep it")
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"{base}/setup")) as refused:
                    setup.locator('[data-act="save"]').click()
                assert refused.value.status == 400, refused.value.text()
                expect(setup.locator("[data-feedback]")).to_contain_text("“Project” has scores and cannot be removed: archive it instead.")
                setup.locator('.gbs-removed [data-act="undo"]').click()
                project_row.locator('[data-field="item.archived"]').check()
                setup.locator('[data-act="save"]').click()
                expect(page).to_have_url(re.compile(rf"{home_re}"))
                expect(grid_page.locator(f'th.gb-head[data-item="{project}"]')).to_have_count(0)
                expect(grid_page.locator(".gb-band")).to_have_text(["Homework · 40% · drop 1", "Exams · 60%", "Uncategorized · not counted", "Categories", "Results"])
                cell(bonus, "Carol").click()
                page.keyboard.type("2")
                page.keyboard.press("Enter")
                expect(cell(bonus, "Carol")).to_have_text("2")
                expect(cell(bonus, "Carol")).not_to_have_class(re.compile(r"\bis-over\b"))
                open_setup()
                project_row.locator('[data-field="item.archived"]').uncheck()
                setup.locator('[data-act="save"]').click()
                expect(cell(project, "Carol")).to_have_text("18")

                # A column's due date is a deadline on the calendar, whose link focuses the column; an unknown term is "not found".
                page.goto(f"{address}/#calendar")
                page.locator("#calendar-section").get_by_role("button", name="Agenda", exact=True).click()
                page.locator(f'[data-calendar-event="gradebook:{quiz}"]:visible').first.click()
                expect(page.locator("#dialog")).to_contain_text("Quiz 1 due")
                page.locator("#dialog [data-calendar-source]").click()
                page.locator('[data-gradebook-page="sections"]').get_by_role("link", name="Section 501").click()  # the column's section
                expect(page).to_have_url(re.compile(rf"{home_re}\?item={quiz}$"))
                expect(grid_page.locator(f'th.gb-head[data-item="{quiz}"]')).to_have_class(re.compile(r"\bis-target\b"))
                expect(grid_page.locator("th.gb-head.is-target")).to_have_count(1)
                page.goto(f"{address}/#gradebook/no-such-term")
                expect(page.locator("#gradebook-section")).to_contain_text("Gradebook not found")
                page.wait_for_timeout(1000)  # a 404 makes the shell refresh; the error page must not load the address again
                assert not any("no-such-term" in url for url in view_gets), [url for url in view_gets if "no-such-term" in url]  # resolved from the list
                page.goto(f"{address}/{home_gb}")
                expect(grid_page.locator("[data-gradebook-grid] tbody tr")).to_have_count(6)

                # ---- The desktop width (text contrast measured on every kind of cell, a missing one included) -------------------------
                cell(linked["id"], "Jamie").click()
                page.keyboard.type("mi")
                page.keyboard.press("Enter")
                expect(cell(linked["id"], "Jamie")).to_have_class(re.compile(r"\bst-missing\b"))
                # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): the page never scrolls sideways, the grid scrolls in
                # its region.
                page.set_viewport_size({"width": 1280, "height": 1000})
                expect(grid_page.locator("[data-gradebook-grid]")).to_be_visible()
                no_overflow(page)
                region = grid_page.locator("[data-gradebook-scroll]")
                overflow = region.evaluate("n => ({x: getComputedStyle(n).overflowX, wide: n.scrollWidth > n.clientWidth})")
                assert overflow["x"] == "auto", overflow
                # "Letter" fits its narrow column on one line: never broken mid-word.
                letter = grid_page.locator("th.gb-r-letter .gb-head-title")
                assert letter.evaluate("n => n.getClientRects().length === 1 && n.scrollWidth <= n.clientWidth + 1 && n.offsetHeight < 1.5 * parseFloat(getComputedStyle(n).lineHeight)"), \
                    letter.evaluate("n => [n.getClientRects().length, n.scrollWidth, n.clientWidth, n.offsetHeight, getComputedStyle(n).lineHeight]")
                # Two categories fit beside Overall and Letter: all four stay at the right edge, whole, never a sliver.
                region.evaluate("node => { node.scrollLeft = 0; }")
                assert region.evaluate("node => node.querySelector('table').classList.contains('gb-freeze-cats')")
                frame = region.bounding_box()
                for head in grid_page.locator(".gb-heads .gb-result-head").all():
                    box = head.bounding_box()
                    assert frame["x"] <= box["x"] and box["x"] + box["width"] <= frame["x"] + frame["width"] + 1, (frame, box)
                expect(region).to_have_class(re.compile(r"\bis-overlapped\b"))  # the frozen block's edge is shaded
                expect(toast).to_be_hidden(timeout=10000)
                region.screenshot(path=str(output / "gradebook-grid-wide.png"))
                for selector in (".gb-head-title", ".gb-head-meta", ".gb-band", ".gb-name-meta", "td.st-scored", "td.st-excused", "td.st-missing",
                                 ".gb-pull", ".gb-acc", ".gb-dr", "td.gb-r-overall", "td.is-dropped", ".gradebook-summary", ".gradebook-key",
                                 ".gb-sort"):
                    ratio = contrast(page, f"[data-gradebook-grid] {selector}" if not selector.startswith(".gradebook") else selector)
                    assert ratio >= 4.5, (selector, ratio)
                page.screenshot(path=str(output / "gradebook-desktop.png"), full_page=True)
                open_setup()
                expect(page.locator(".gbs .gbs-state")).to_have_text(re.compile(r"^Saved · revision"))
                no_overflow(page)
                expect(toast).to_be_hidden(timeout=10000)
                page.screenshot(path=str(output / "gradebook-setup-desktop-saved.png"))
                back_to_grid()
                # Dialogs with their previews fit the window; their tables scroll inside themselves.
                open_import()
                dialog.locator('input[name="file"]').set_input_files({"name": "grades.csv", "mimeType": "text/csv", "buffer": IMPORT_CSV})
                expect(dialog.locator('[data-count="same"] dd')).to_have_text("4")  # the file again: every value is already there
                expect(dialog.locator("[data-filter-action]")).to_have_value("all")  # nothing to write: everything compared is listed
                expect(dialog.locator("[data-rows] tbody tr")).to_have_count(4)
                no_overflow(page)
                assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth"), dialog.evaluate("node => [node.scrollWidth, node.clientWidth]")
                page.screenshot(path=str(output / "gradebook-import-again.png"))
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                grid_page.locator(f'[data-pull="{linked["id"]}"]').click()
                expect(pull.locator('[data-count="same"] dd')).to_have_text("3")
                no_overflow(page)
                assert pull.evaluate("node => node.scrollWidth <= node.clientWidth"), pull.evaluate("node => [node.scrollWidth, node.clientWidth]")
                pull.get_by_role("button", name="Cancel", exact=True).click()
                # A student's record fits too, every category open: its table is read, not scrolled sideways.
                grid_page.locator(f'tr[data-row="{rows["Jamie"]}"] .gb-name-button').click()
                record_form = page.locator("#gradebook-student-form")
                expect(record_form).to_be_visible()
                for fold in record_form.locator("[data-record-fold]").all():
                    fold.click()
                expect(record_form.locator('[data-record-fold][aria-expanded="false"]')).to_have_count(0)
                no_overflow(page)
                assert record_form.evaluate("node => node.scrollWidth <= node.clientWidth + 1"), record_form.evaluate("node => [node.scrollWidth, node.clientWidth]")
                for table in record_form.locator(".gbd-record-table .data-table").all():
                    assert table.evaluate("node => node.scrollWidth <= node.clientWidth + 1"), table.evaluate("node => [node.scrollWidth, node.clientWidth]")
                page.screenshot(path=str(output / "gradebook-record-desktop.png"))
                record_form.get_by_role("button", name="Close", exact=True).click()
                expect(record_form).to_be_hidden()

                # The grid is as tall as the window below its own top; one that starts below half the window (notices, the work
                # list above it) is as tall as the window instead, so once scrolled to it shows a screenful of rows, not a short box.
                fitted = """() => { const s = document.querySelector('[data-gradebook-scroll]'); const top = Math.round(s.getBoundingClientRect().top + scrollY);
                  return s.style.getPropertyValue('--gb-top') === `${top > innerHeight / 2 ? 0 : top}px`; }"""
                for height in (560, 2400):
                    page.set_viewport_size({"width": 1440, "height": height})
                    page.evaluate("() => window.scrollTo(0, 0)")
                    wait_until(page, fitted)
                    fit = grid_page.locator("[data-gradebook-scroll]").evaluate("n => ({top: Math.round(n.getBoundingClientRect().top + scrollY), set: n.style.getPropertyValue('--gb-top'), max: parseFloat(getComputedStyle(n).maxHeight), inner: innerHeight})")
                    if height == 560:
                        assert fit["top"] > height / 2 and fit["set"] == "0px" and fit["max"] >= 0.8 * fit["inner"], fit
                    else:
                        assert fit["top"] <= height / 2 and fit["set"] == f"{fit['top']}px" and fit["max"] >= fit["inner"] - fit["top"] - 24, fit
                # The section's work list (and its TAs, Gradescope and Canvas rows) arrive after the grid draws and push it down:
                # the grid measures its top again when they do, so it is never taller than the window below it.
                # (Section 502's student gets a Homework score behind the page first, for the setup's term-wide counts below.)
                row_502 = next(student["row"] for student in first["students"] if student["section_id"] == sections[502])
                wrote = client.post(f"{base}/scores", json={"request_id": "hw-502", "changes": [
                    {"item_id": hw, "row": row_502, "base": None, "status": "scored", "points": "5"}]}, headers=HEADERS)
                assert wrote.status_code == 200, wrote.json
                page.set_viewport_size({"width": 1440, "height": 2000})
                page.reload()
                expect(grid_page.locator("[data-gradebook-scroll]")).to_be_visible()
                expect(page.locator("[data-course-work-rows] :is(details, .course-work-empty)").first).to_be_attached()
                page.evaluate("() => window.scrollTo(0, 0)")
                wait_until(page, fitted)
                page.set_viewport_size({"width": 1280, "height": 1000})

                # Setup's "Find a column" (a long Columns table) hides rows by title, or shows only the Uncategorized ones; the draft,
                # its order and Save are unchanged. A category's whole name is the select's title.
                open_setup()
                expect(setup.locator(".gbs-state")).to_have_text(re.compile(r"^Saved · revision"))
                find = setup.locator("[data-gbs-find]")
                expect(find).to_be_visible()
                shown_rows = setup.locator(".gbs-items tbody.gbs-item:visible")
                total = setup.locator(".gbs-items tbody.gbs-item").count()
                assert total > 5, total
                find.fill("mid")
                expect(shown_rows).to_have_count(1)
                expect(shown_rows.locator('[data-field="item.title"]')).to_have_value("Midterm")
                expect(shown_rows.locator('[data-field="item.category"]')).to_have_attribute("title", "Exams")
                expect(setup.locator("[data-gbs-find-count]")).to_have_text(f"Showing 1 of {total} columns.")
                expect(setup.locator(".gbs-state")).to_have_text(re.compile(r"^Saved · revision"))
                expect(setup.locator('[data-act="save"]')).to_be_disabled()
                find.fill("")
                expect(shown_rows).to_have_count(total)
                term_view = view()
                latest = term_view["setup"]
                # Opened from Section 501's tab (whose View holds only 501's cells), each column still counts its scores term-wide.
                wide = [item["id"] for item in latest["items"] if any(not row.startswith(f"{sections[501]}:") for row in term_view["cells"].get(item["id"], {}))]
                assert wide, "a column with a score outside Section 501"
                for item_id in wide:
                    scored = len(term_view["cells"][item_id])
                    expect(setup.locator(f'tbody.gbs-item[data-item-key="{item_id}"] th small')).to_have_text(f"{scored} score{'' if scored == 1 else 's'}")
                known = {category["id"] for category in latest["categories"]}
                loose = [item["title"] for item in latest["items"] if item.get("category_id") not in known]
                only = lambda value: setup.locator(f'[data-filter-group="gbs-only"] [data-filter="{value}"]')
                only("uncategorized").click()
                expect(only("uncategorized")).to_have_attribute("aria-pressed", "true")
                expect(shown_rows.locator('[data-field="item.title"]')).to_have_count(len(loose))
                assert sorted(field.input_value() for field in shown_rows.locator('[data-field="item.title"]').all()) == sorted(loose), loose
                expect(shown_rows.locator('[data-field="item.category"]').first).to_have_attribute("title", "Uncategorized")
                # Choosing a category under "Uncategorized" keeps that row in view and the filter on, so the next loose column is
                # one pick away; pressing the filter again lets the categorized row go. Discarding leaves the setup as saved.
                edited = shown_rows.first
                edited_title = edited.locator('[data-field="item.title"]').input_value()
                category = edited.locator('[data-field="item.category"]')
                category.select_option(index=1)
                expect(category).to_be_focused()
                expect(category).not_to_have_attribute("title", "Uncategorized")
                expect(only("uncategorized")).to_have_attribute("aria-pressed", "true")
                expect(only("uncategorized").locator("b")).to_have_text(str(len(loose) - 1))
                expect(shown_rows.locator('[data-field="item.title"]')).to_have_count(len(loose))
                expect(setup.locator("[data-gbs-find-count]")).to_have_text(f"Showing {len(loose)} of {total} columns.")
                expect(setup.locator(".gbs-state")).not_to_have_text(re.compile(r"^Saved · revision"))
                only("uncategorized").click()
                expect(only("uncategorized")).to_have_attribute("aria-pressed", "true")
                expect(shown_rows.locator('[data-field="item.title"]')).to_have_count(len(loose) - 1)
                assert edited_title not in [field.input_value() for field in shown_rows.locator('[data-field="item.title"]').all()], edited_title
                setup.locator('[data-act="discard"]').click()
                expect(toast).to_contain_text("Setup changes discarded.")
                expect(shown_rows.locator('[data-field="item.title"]')).to_have_count(len(loose))
                only("").click()
                expect(shown_rows).to_have_count(total)
                expect(setup.locator('[data-act="save"]')).to_be_disabled()
                back_to_grid()

                # On a touch screen (a desktop's width: the app is desktop only) a tap focuses a score and a second tap edits it; the
                # column menu stays inside the window.
                touch_screen = browser.new_context(viewport={"width": 1280, "height": 900}, has_touch=True)
                touch = touch_screen.new_page()
                touch.add_init_script(watch)
                touch.on("pageerror", lambda error: errors.append(str(error)))
                touch.on("console", lambda message: errors.append(message.text)
                         if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                touch.goto(f"{address}/{home_gb}")
                target = touch.locator(f'td[data-cell][data-item="{mid}"][data-row="{rows["Dylan"]}"]')
                target.tap()
                expect(target).to_be_focused()
                target.tap()
                expect(target.locator("input.gb-input")).to_be_visible()
                touch.keyboard.type("73")
                touch.keyboard.press("Enter")
                expect(target).to_have_text("73")
                touch.locator(f'th.gb-head[data-item="{mid}"] [data-column-menu]').tap()
                touch_menu = touch.locator("#gradebook-column-menu")
                expect(touch_menu).to_be_visible()
                box = touch_menu.bounding_box()
                assert box["x"] >= 0 and box["x"] + box["width"] <= 1280, box
                assert touch.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert touch.evaluate("gradebookCspViolations") == [] and touch.evaluate("window.gradebookInjection") is None
                touch_screen.close()
                assert view()["cells"][mid][rows["Dylan"]]["points"] == "73"

                # ---- "Add all as columns": one setup save for every graded assignment without a column -----------------------------
                # Test 1's column is archived elsewhere, so Test 1 and Quiz 2 both wait for a column again.
                def archive_linked(setup):
                    next(item for item in setup["items"] if item["id"] == linked["id"])["archived"] = True
                setup_behind(client, base, archive_linked)
                grid_page.locator('[data-gradebook-action="refresh"]').click()
                expect(toast.locator(".toast-item").last).to_have_text("Gradebook refreshed.")
                unlinked = grid_page.locator(".gradebook-unlinked")
                unlinked.locator(":scope > summary").click()
                expect(unlinked.locator("[data-gradebook-add-all]")).to_have_text("Add all 2 as columns")
                expect(unlinked.locator(".gradebook-plan-ahead summary")).to_contain_text("Not graded yet")
                unlinked.locator("[data-gradebook-add-all]").click()
                add_all = page.locator("#gradebook-add-all-form")
                expect(add_all.locator(".gbd-add-list li")).to_have_count(2)
                expect(add_all.locator(".gbd-add-list li").nth(0)).to_contain_text("Quiz 2")
                expect(add_all.locator(".gbd-add-list li").nth(0)).to_contain_text("Bubble sheets · 10 points")
                expect(add_all.locator(".gbd-add-list li").nth(1)).to_contain_text("Test 1")
                expect(add_all.locator('button[type="submit"]')).to_have_text("Add 2 columns")
                # Another window saves first: nothing is added, and after a reload the same list is added in one save.
                revision = setup_behind(client, base, notes("Midterm", "Room HECC 209"))
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"{base}/setup")) as refused:
                    add_all.locator('button[type="submit"]').click()
                assert refused.value.status == 409, refused.value.text()
                expect(add_all.locator("[data-conflict]")).to_contain_text("Nothing was added.")
                add_all.locator("[data-conflict] [data-reload]").click()
                expect(add_all.locator("[data-conflict]")).to_contain_text(f"The saved setup (revision {revision}) is loaded")
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"{base}/setup")) as added:
                    add_all.locator('button[type="submit"]').click()
                assert added.value.status == 200, added.value.text()
                sent = json.loads(added.value.request.post_data)
                assert sent["base_revision"] == revision and sent["request_id"] and sent["request_id"] != json.loads(refused.value.request.post_data)["request_id"], sent
                new_items = [item for item in sent["setup"]["items"] if "id" not in item]
                assert [(item["title"], item["assignment_id"], item["link"], item["points"]) for item in new_items] == [
                    ("Quiz 2", data["quiz"]["id"], {"kind": "grading", "parts": ["bubble"]}, "10"),
                    ("Test 1", data["assignment"]["id"], {"kind": "grading", "parts": ["bubble"]}, "20")], new_items
                expect(add_all).to_be_hidden()
                expect(toast.locator(".toast-item").last).to_contain_text("Added 2 linked columns")
                after = view()
                assert after["setup"]["revision"] == revision + 1, after["setup"]["revision"]
                expect(grid_page.locator("th.gb-head.gb-linked")).to_have_count(2)  # nothing was pulled: the columns are empty
                expect(grid_page.locator(".gradebook-unlinked > summary")).to_contain_text("Assignments without a column")
                expect(grid_page.locator(".gradebook-unlinked [data-gradebook-add-all]")).to_have_count(0)

                # ---- The grading scheme (GRADEBOOK GRADING SCHEME): the course Home's Grades row, one scheme for every section ------
                page.goto(f"{address}/#course/{group}/home")
                scheme_row = page.locator("[data-grading-scheme-row]")
                scheme_line = scheme_row.locator("[data-grading-scheme-status]")
                expect(scheme_line).to_have_text("Standard · A 90% · B 80% · C 70% · D 60% · F below 60%")
                scheme_row.locator("[data-grading-scheme]").click()
                scheme = page.locator("#gradebook-scheme-form")
                expect(scheme.locator(".dialog-header")).to_contain_text("every section")
                expect(scheme.locator("[data-scheme-upper]")).to_have_text(["100%", "< 90%", "< 80%", "< 70%", "< 60%"])
                expect(scheme.locator("[data-scheme-min]")).to_have_count(4)  # the last letter runs down to 0%: not a field
                expect(scheme.locator('button[type="submit"]')).to_be_disabled()  # nothing changed yet
                expect(scheme.locator("[data-scheme-preview]")).to_contain_text("Preview from today’s percentages")
                scheme.locator('[data-scheme-min="2"]').fill("67")
                expect(scheme.locator('[data-scheme-upper="3"]')).to_have_text("< 67%")  # the next range follows the typing
                scheme.locator('[data-scheme-min="3"]').fill("57")
                expect(scheme.locator('[data-scheme-upper="4"]')).to_have_text("< 57%")
                # A minimum out of order is caught before anything is sent, and the mark goes once it is fixed.
                scheme.locator('[data-scheme-min="1"]').fill("95")
                scheme.locator('button[type="submit"]').click()
                expect(scheme.locator('[data-scheme-min="1"]')).to_have_attribute("aria-invalid", "true")
                expect(scheme.locator(".form-error")).to_contain_text("minimums must go down")
                scheme.locator('[data-scheme-min="1"]').fill("80")
                expect(scheme.locator(".form-error")).to_be_hidden()
                expect(scheme.locator('[data-scheme-min="1"]')).not_to_have_attribute("aria-invalid", "true")
                # Another window saves the setup first: nothing is saved, and the reload shows the saved scheme to change again.
                revision = setup_behind(client, base, notes("Midterm", "Room HECC 210"))
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"{base}/setup")) as refused:
                    scheme.locator('button[type="submit"]').click()
                assert refused.value.status == 409, refused.value.text()
                expect(scheme.locator("[data-conflict]")).to_contain_text("Nothing was saved.")
                scheme.locator("[data-conflict] [data-reload]").click()
                expect(scheme.locator("[data-conflict]")).to_contain_text(f"(setup revision {revision}) is shown")
                expect(scheme.locator('[data-scheme-min="2"]')).to_have_value("70")
                scheme.locator('[data-scheme-min="2"]').fill("67")
                scheme.locator('[data-scheme-min="3"]').fill("57")
                before_scheme = view()
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"{base}/setup")) as saved_scheme:
                    scheme.locator('button[type="submit"]').click()
                assert saved_scheme.value.status == 200, saved_scheme.value.text()
                canvas_like = [{"letter": "A", "min": "90"}, {"letter": "B", "min": "80"}, {"letter": "C", "min": "67"},
                               {"letter": "D", "min": "57"}, {"letter": "F", "min": "0"}]
                sent = json.loads(saved_scheme.value.request.post_data)
                assert sent["setup"] == {"scale": canvas_like} and sent["base_revision"] == revision and "section_id" not in sent, sent
                expect(scheme).to_be_hidden()
                expect(toast.locator(".toast-item").last).to_contain_text("Grading scheme saved")
                expect(scheme_line).to_have_text("A 90% · B 80% · C 67% · D 57% · F below 57%")
                after_scheme = view()
                assert after_scheme["setup"]["scale"] == canvas_like and after_scheme["setup"]["revision"] == revision + 1
                kept = ("mode", "categories", "items", "ungraded", "letter_rounding")
                assert {key: after_scheme["setup"][key] for key in kept} == {key: before_scheme["setup"][key] for key in kept}
                page.reload()  # the line is read from the term list, not only from the save
                expect(scheme_line).to_have_text("A 90% · B 80% · C 67% · D 57% · F below 57%")
                # The setup page, from either section, holds the same scheme under the same name, written the same way.
                page.goto(f"{address}/#course/{group}/section/502/setup")
                expect(setup.locator("#gbs-scale-title")).to_have_text("Grading scheme")
                expect(setup.locator("[data-scale-upper]")).to_have_text(["100%", "< 90%", "< 80%", "< 67%", "< 57%"])
                setup.locator('[data-field="scale.min"][data-index="2"]').fill("68")
                expect(setup.locator('[data-scale-upper="3"]')).to_have_text("< 68%")
                setup.locator('[data-act="discard"]').click()
                expect(setup.locator('[data-scale-upper="3"]')).to_have_text("< 67%")
                no_overflow(page)

                # ---- A tab from before a restart ------------------------------------------------------------------------------------
                # The setup editor and the dialogs are warmed when the workspace starts: once they are in, a restart that retires
                # their build prefix (here: every later fetch of them fails) no longer stops Setup or a dialog in this tab.
                late = []
                blocked = re.compile(r".*/gradebook-(setup|dialogs)\.js(\?.*)?$")  # their build prefix is gone
                def watch_errors(target):
                    target.on("pageerror", lambda error: late.append(str(error)))
                    target.on("console", lambda message: late.append(message.text) if message.type == "error"
                              and not message.text.startswith("Failed to load resource") else None)
                warm = browser.new_page(viewport={"width": 1280, "height": 1000})
                warm.add_init_script(watch)
                watch_errors(warm)
                warm.goto(f"{address}/#calendar")  # not the gradebook: the modules are warmed for the whole workspace
                wait_until(warm, """() => ['gradebook-setup.js', 'gradebook-dialogs.js'].every(name =>
                    performance.getEntriesByType('resource').some(entry => entry.name.endsWith('/' + name)))""")
                warm.route(blocked, lambda route: route.abort())
                warm.evaluate(f"location.hash = '{home_gb}/setup'")
                expect(warm.locator(".gbs .gbs-state")).to_have_text(re.compile(r"^Saved · revision"))
                warm.evaluate(f"location.hash = '{home_gb}'")
                warm.locator(".page-heading-secondary").get_by_role("button", name="Import scores", exact=True).click()
                expect(warm.locator("#gradebook-import-form")).to_be_visible()
                expect(warm.locator("#app-updated")).to_have_count(0)
                warm.close()
                # A tab whose modules can no longer be fetched at all says so, and offers a reload that keeps the address.
                stale = browser.new_page(viewport={"width": 1280, "height": 1000})
                stale.add_init_script(watch)
                watch_errors(stale)
                stale.route(blocked, lambda route: route.abort())
                stale.goto(f"{address}/{home_gb}/setup")
                failed = stale.locator("[data-gradebook-setup-failed]")
                expect(failed).to_contain_text("Faculty was updated since this tab opened. Reload to continue.")
                expect(failed.get_by_role("button", name="Reload")).to_be_visible()
                expect(stale.locator("#app-updated")).to_contain_text("Faculty was updated since this tab opened")
                stale.evaluate(f"location.hash = '{home_gb}'")
                expect(stale.locator("[data-gradebook-grid] tbody tr")).to_have_count(6)
                stale.locator(".page-heading-secondary").get_by_role("button", name="Import scores", exact=True).click()
                updated = stale.locator("#toast .toast-item").last
                expect(updated).to_contain_text("Faculty was updated since this tab opened. Reload to continue.")
                expect(updated.get_by_role("button", name="Reload")).to_be_visible()
                stale.unroute(blocked)
                with stale.expect_navigation():
                    updated.get_by_role("button", name="Reload").click()
                expect(stale).to_have_url(re.compile(rf"{home_re}$"))  # the address is kept
                expect(stale.locator("[data-gradebook-grid] tbody tr")).to_have_count(6)
                stale.locator(".page-heading-secondary").get_by_role("button", name="Import scores", exact=True).click()
                expect(stale.locator("#gradebook-import-form")).to_be_visible()
                assert not [message for message in late if "dynamically imported module" not in message], late
                stale.close()

                assert page.evaluate("window.gradebookInjection") is None, "a roster name ran as markup"
                assert page.evaluate("gradebookCspViolations") == [], page.evaluate("gradebookCspViolations")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("PASS: Gradebook navigation, setup, grid editing, paste, stale writes, column/history/import/pull dialogs, export, desktop width and touch.")


if __name__ == "__main__":
    main()
