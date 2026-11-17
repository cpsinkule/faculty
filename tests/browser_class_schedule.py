"""A section's Class schedule in a real browser (SCHEDULING "CLASS SCHEDULE"): from the section tab's Schedule row, one table by date
with the section's class days, a day off, today marked and earlier weeks folded; a quiz read from its exam record and a due date
from the gradebook; the orange "Dates differ" mark where the gradebook and Faculty's Canvas copy disagree for one section, each
date a link to where it is changed; a planned date added for every section, then given each section its own room; topics typed
day by day with Enter; the course tabs leading from one section's schedule to the next. Fits the narrowest desktop window (1280px)
under the CSP. Synthetic course and records only."""
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import create_app
from browser_course_pages import seed_course
from canvas.placement import campus_instant
from rosters import connect
from scheduling import clock

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    today = clock.campus_today()
    monday = today - dt.timedelta(days=today.weekday())
    day = lambda weeks, weekday=0: (monday + dt.timedelta(weeks=weeks, days=weekday)).isoformat()  # noqa: E731
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        course = seed_course(client, sections=[507, 508])
        group = client.get("/api/state").json["course_groups"][0]
        with connect(app.config["DATABASE"]) as db:
            sections = {int(r[1]): r[0] for r in db.execute("SELECT id, number FROM course_sections")}
            term = db.execute("SELECT term_id FROM course_sections LIMIT 1").fetchone()[0]

        def post(url, body):
            answer = client.post(url, json={"request_id": str(uuid.uuid4()), **body}, headers=HEADERS)
            assert answer.status_code == 200, answer.json
            return answer.json

        # Both sections meet Mon/Wed/Fri for six weeks before this one and seven after; the Monday two weeks on is a day off.
        post(f"/api/scheduling/terms/{term}", {"base_revision": 0, "first_day": day(-6), "last_day": day(7, 4), "days_off": [day(2)], "entries": [
            {"kind": "class", "section_id": sections[507], "weekdays": [0, 2, 4], "start": "08:00", "end": "08:50", "room": "HELD 107"},
            {"kind": "class", "section_id": sections[508], "weekdays": [0, 2, 4], "start": "09:10", "end": "10:00", "room": "HELD 107"}]})
        use = f"/api/scheduling/terms/{term}/use"
        post(use, {"fingerprint": client.get(use).json["fingerprint"], "confirmation": "use"})
        # Quiz 4 was given last week (its exam record); HW 5 is due today, HW 6 next week.
        quiz = app.extensions["documents"].create_assignment(course["definition_id"], "quiz-4", "Quiz 4")
        exams = app.extensions["exams"]
        event = exams.create({"term_id": term, "assignment_id": quiz["id"], "section_ids": list(sections.values())})
        exams.save_sitting(event["id"], {"version": event["version"], "label": "Quiz 4", "kind": "main", "date": day(-1, 2), "time": "8:00 AM"})
        setup = post(f"/api/gradebook/terms/{term}/setup", {"base_revision": None, "setup": {"items": [
            {"title": "HW 5", "points": "10", "due_date": today.isoformat()}, {"title": "HW 6", "points": "10", "due_date": day(1, 0)}]}})
        items = {item["title"]: item["id"] for item in setup["setup"]["items"]}
        # Faculty's Canvas copy says HW 6 is due two days later for section 508 than its gradebook column: 508's blanks would count
        # zero early, so 508's schedule marks it; 507's agrees.
        copy = json.dumps({"modules": [], "pages": [], "assignments": [{
            "id": "copy-hw6", "name": "HW 6", "description": "", "points": "10", "grading_type": "points", "submission_types": ["none"],
            "external": False, "group": "Homework", "due_at": campus_instant(day(1, 0), "23:59"),
            "section_due": {sections[508]: campus_instant(day(1, 2), "23:59")}, "unlock_at": None, "lock_at": None, "published": True,
            "omit_from_final_grade": False, "gradebook_item": items["HW 6"], "faculty_assignment": None, "sections": []}]})
        with connect(app.config["DATABASE"]) as db:
            revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM canvas_content_revisions WHERE term_id=?", (term,)).fetchone()[0]
            db.execute("INSERT INTO canvas_content_revisions VALUES (?,?,?,?,?,?,?,?,?)",
                       (uuid.uuid4().hex, term, revision, copy, hashlib.sha256(copy.encode()).hexdigest(), uuid.uuid4().hex, "0" * 64, "test",
                        "2026-10-01T00:00:00+00:00"))
            for number, canvas_course in ((507, "111"), (508, "222")):
                db.execute("INSERT INTO canvas_course_links VALUES (?,?,?,?,?,?,?,?)",
                           (uuid.uuid4().hex, sections[number], canvas_course, f"MATH 140 {number}", f"MATH 140 {number}", "Fall 2026", "test",
                            "2026-08-20T00:00:00+00:00"))
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.add_init_script('window.cspViolations=[]; document.addEventListener("securitypolicyviolation", e=>window.cspViolations.push(e.violatedDirective));')
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#course/{group['id']}/section/507")
                row = page.locator("[data-class-schedule-row]")
                expect(row).to_contain_text("Schedule")
                row.get_by_role("link", name="Section 507 class schedule: class days, topics, and what is given or due").click()
                expect(page.locator("#page-title")).to_have_text("Class schedule · Section 507")
                expect(page.locator("#page-context")).to_be_hidden()  # the trail already names the course and term
                expect(page.locator("#page-subtitle")).to_contain_text("class days")
                primary = page.locator("#primary-action")
                expect(primary).to_have_text("Add a date")  # an empty plan: its one primary
                table = page.get_by_role("table", name="Class schedule", exact=True)
                expect(table.locator("thead")).to_contain_text("Given and due")
                # Today is marked; the weeks before this one are folded; the day off says so; the quiz and the due dates are read.
                current = table.locator('tr[aria-current="date"]')
                expect(current).to_have_count(1)
                expect(current).to_contain_text("HW 5")
                fold = page.locator("details.cs-earlier")
                expect(fold.locator("summary")).to_contain_text("Earlier")
                expect(fold.locator("summary")).to_contain_text("6 weeks")
                assert fold.get_attribute("open") is None
                expect(table.locator(f'tr[data-date="{day(2)}"]')).to_contain_text("No class")
                expect(table.locator(f'tr[data-date="{day(1, 2)}"]')).to_contain_text("8:00–8:50 AM · HELD 107")
                fold.locator("summary").click()
                given = fold.locator(f'tr[data-date="{day(-1, 2)}"]')
                expect(given).to_contain_text("Given")
                expect(given.get_by_role("link", name="Quiz 4")).to_have_attribute("href", f"#exams/{event['id']}")
                expect(given).to_contain_text("8:00 AM")
                due = table.locator(f'tr[data-date="{day(1, 0)}"]')
                expect(due.get_by_role("link", name="HW 6")).to_have_attribute("href", f"#course/{group['id']}/home/canvas")
                expect(due).to_contain_text("11:59 PM")
                expect(page.locator("[data-class-schedule] .chip[data-tone=warning]")).to_have_count(0)  # 507's dates agree
                page.screenshot(path=str(output / "class-schedule-507.png"), full_page=True)

                # A planned date for every section, then each section its own room.
                primary.click()
                form = page.locator("#class-schedule-row")
                expect(form.get_by_label("All sections")).to_be_checked()
                form.get_by_label("Title").fill("Exam 2")
                form.get_by_label("Kind").select_option("exam")
                form.get_by_label("Date", exact=True).fill(day(3, 1))
                form.get_by_label("Time", exact=True).fill("7:30 PM–9:30 PM")
                expect(form.locator("[data-scope]")).to_contain_text("Every section")
                form.get_by_role("button", name="Add to schedule").click()
                expect(page.locator("#toast")).to_contain_text("Added to the schedule.")
                planned = table.locator(f'tr[data-date="{day(3, 1)}"]')
                expect(planned.get_by_role("button", name="Exam 2")).to_be_visible()
                expect(planned.locator(".chip[data-tone=neutral]")).to_have_text("Planned")
                expect(planned).to_contain_text("7:30 PM–9:30 PM")
                expect(primary).to_be_hidden()  # the plan is no longer empty: Add a date is a secondary now
                planned.get_by_role("button", name="Exam 2").click()
                form = page.locator("#class-schedule-row")
                form.get_by_label("Sections differ").check()
                expect(form.get_by_label("Section 507 date")).to_have_value(day(3, 1))  # each section starts from the shared values
                form.get_by_label("Section 507 place").fill("ILCB 111")
                form.get_by_label("Section 508 place").fill("HELD 100")
                expect(form.locator("[data-scope]")).to_have_text("2 sections · same day, 2 rooms")
                page.screenshot(path=str(output / "class-schedule-row.png"))
                form.get_by_role("button", name="Save", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("Planned date saved.")
                expect(planned).to_contain_text("ILCB 111")
                stored = client.get(f"/api/scheduling/terms/{term}/class-schedule").json
                assert stored["revision"] == 2 and stored["items"][0]["section_values"] == {
                    sections[507]: {"place": "ILCB 111"}, sections[508]: {"place": "HELD 100"}}, stored
                assert (stored["items"][0]["date"], stored["items"][0]["time"]) == (day(3, 1), "7:30 PM–9:30 PM"), stored

                # Topics: one field per class day; Enter moves to the next; numbers first, then a note.
                page.get_by_role("button", name="Edit topics").click()
                # The field of today's class, else of the next class day, has the focus.
                first = table.locator(f'[data-topic="{today.isoformat()}"]') if today.weekday() in (0, 2, 4) else table.locator(".cs-day:not(.cs-past) [data-topic]").first
                expect(first).to_be_focused()
                first.fill("2.3 2.4 review")
                expect(primary).to_have_text("Save topics")
                first.press("Enter")
                second = page.locator("[data-topic]:focus")
                expect(second).to_have_count(1)
                assert second.get_attribute("data-topic") != first.get_attribute("data-topic")
                second.fill("2.5")
                typed = [first.get_attribute("data-topic"), second.get_attribute("data-topic")]
                primary.click()
                expect(page.locator("#toast")).to_contain_text("Topics saved.")
                expect(table.locator(f'tr[data-date="{typed[0]}"] .cs-topics')).to_have_text("2.3 · 2.4 · review")
                expect(table.locator(f'tr[data-date="{typed[1]}"] .cs-topics')).to_have_text("2.5")
                topics = client.get(f"/api/scheduling/terms/{term}/class-schedule").json["topics"]
                assert topics == {typed[0]: {"notes": ["2.3", "2.4"], "note": "review", "sections": {}},
                                  typed[1]: {"notes": ["2.5"], "note": "", "sections": {}}}, topics

                # Pre-fill: a planned homework row offers itself to the new gradebook column of its title, gives its due date, and
                # then follows that column (its line links to the gradebook and is no longer "Planned").
                base = client.get(f"/api/scheduling/terms/{term}/class-schedule").json["revision"]
                hw = next(row for row in post(f"/api/scheduling/terms/{term}/class-schedule", {"base_revision": base, "set": [
                    {"kind": "homework", "title": "HW 9", "date": day(4, 0)}]})["items"] if row["title"] == "HW 9")
                page.goto(f"{address}/#course/{group['id']}/section/507")
                page.locator(".page-heading").get_by_role("button", name="Add column", exact=True).click()
                column = page.locator("#gradebook-column-form")
                offered = column.get_by_label("From the schedule")
                expect(offered).to_be_visible()
                expect(offered).to_have_value("")
                column.locator('[name="title"]').fill("HW 9")
                expect(offered).to_have_value(hw["item"])  # chosen by its title
                expect(column.locator('[name="due_date"]')).to_have_value(day(4, 0))
                column.locator('[name="points"]').fill("10")
                page.screenshot(path=str(output / "class-schedule-prefill.png"))
                column.get_by_role("button", name="Add column", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("added")
                linked = next(row for row in client.get(f"/api/scheduling/terms/{term}/class-schedule").json["items"] if row["title"] == "HW 9")
                made = next(item for item in client.get(f"/api/gradebook/terms/{term}").json["setup"]["items"] if item["title"] == "HW 9")
                assert linked["link"] == {"gradebook_item": made["id"]} and made["due_date"] == day(4, 0), (linked, made)
                page.goto(f"{address}/#course/{group['id']}/section/507/schedule")
                table = page.get_by_role("table", name="Class schedule", exact=True)
                follows = table.locator(f'tr[data-date="{day(4, 0)}"]')
                expect(follows.get_by_role("link", name="HW 9")).to_have_attribute("href", f"#gradebook/{term}?item={made['id']}")
                expect(follows.locator(".chip")).to_have_count(0)
                expect(follows.get_by_role("button", name="The planned row HW 9 follows")).to_be_visible()

                # A planned quiz given on one day and due on another gives its DUE date to the new column (SCHEDULING "CLASS SCHEDULE: PRE-FILL").
                base = client.get(f"/api/scheduling/terms/{term}/class-schedule").json["revision"]
                quiz = next(row for row in post(f"/api/scheduling/terms/{term}/class-schedule", {"base_revision": base, "set": [
                    {"kind": "quiz", "title": "Take-home 3", "date": day(5, 0), "due_date": day(7, 0)}]})["items"] if row["title"] == "Take-home 3")
                assert quiz.get("due_date") == day(7, 0), quiz
                page.goto(f"{address}/#course/{group['id']}/section/507")
                page.locator(".page-heading").get_by_role("button", name="Add column", exact=True).click()
                column = page.locator("#gradebook-column-form")
                offered = column.get_by_label("From the schedule")
                column.locator('[name="title"]').fill("Take-home 3")
                expect(offered).to_have_value(quiz["item"])
                expect(column.locator('[name="due_date"]')).to_have_value(day(7, 0))
                expect(offered.locator("option:checked")).to_contain_text((lambda d: f"{d:%a}, {d:%b} {d.day}")(dt.date.fromisoformat(day(7, 0))))
                page.keyboard.press("Escape")
                expect(column).not_to_be_visible()
                page.goto(f"{address}/#course/{group['id']}/section/507/schedule")
                table = page.get_by_role("table", name="Class schedule", exact=True)

                # The course tabs lead from one section's schedule to the next: 508 has its own room and the one disagreement.
                page.get_by_role("tab", name="Section 508").click()
                expect(page.locator("#page-title")).to_have_text("Class schedule · Section 508")
                table = page.get_by_role("table", name="Class schedule", exact=True)
                expect(table.locator(f'tr[data-date="{day(3, 1)}"]')).to_contain_text("HELD 100")
                expect(table.locator(f'tr[data-date="{typed[0]}"] .cs-topics')).to_have_text("2.3 · 2.4 · review")  # the term's topics
                expect(page.locator("#page-subtitle")).to_contain_text("1 date differs")
                marked = table.locator(f'tr[data-date="{day(1, 2)}"]')
                expect(marked.locator(".chip[data-tone=warning]")).to_have_text("Dates differ")
                expect(marked.get_by_role("link", name="Gradebook")).to_have_attribute("href", f"#gradebook/{term}?item={items['HW 6']}")
                expect(marked.get_by_role("link", name="Canvas copy")).to_have_attribute("href", f"#course/{group['id']}/home/canvas")
                expect(page.locator("[data-class-schedule] .chip[data-tone=warning]")).to_have_count(2)  # the count above the table and the row's mark
                page.locator(".cs-attention").get_by_role("button", name="HW 6").click()
                expect(marked).to_be_focused()
                page.screenshot(path=str(output / "class-schedule-508.png"), full_page=True)
                page.get_by_role("tab", name="Home").click()
                expect(page.locator("#page-title")).to_have_text("Term schedule")

                # The narrowest desktop window: no sideways scroll; a second visit keeps the page (no loading flash).
                page.set_viewport_size({"width": 1280, "height": 900})
                page.goto(f"{address}/#course/{group['id']}/section/508/schedule")
                expect(page.locator("#page-title")).to_have_text("Class schedule · Section 508")
                expect(page.get_by_role("table", name="Class schedule", exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
                assert page.evaluate("document.querySelector('.class-schedule-table').scrollWidth <= document.querySelector('.workspace-table-wrap').clientWidth + 1")
                page.screenshot(path=str(output / "class-schedule-1280.png"), full_page=True)
                assert page.evaluate("window.cspViolations") == [], page.evaluate("window.cspViolations")
                assert errors == [], errors
                browser.close()
        finally:
            server.close()
    print("class schedule browser check passed")


if __name__ == "__main__":
    main()
