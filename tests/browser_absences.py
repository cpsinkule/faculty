"""Excused absences in a real browser (AGENTS.md "Excused absences"): record an absence from the page's header with the student picker,
add documentation, decide it excused (which changes no column but iClicker sessions, and no exam record), filter the list, see the student's
absences on their gradebook card, and keep the narrowest desktop window (1280px) from scrolling sideways. Synthetic course, roster and gradebook only."""
import io
import re
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from app import create_app  # noqa: E402
from browser_course_pages import seed_course  # noqa: E402
from rosters import connect  # noqa: E402
from test_rosters import csv_bytes  # noqa: E402
from test_storage import HEADERS  # noqa: E402


def main():
    # The makeup sitting must lie ahead of the day the check runs: once its day has passed, the row reads "Paper not in" and offers
    # "Missed makeup…" instead of the sitting (the absence and exam dates stay fixed, as in the past).
    ahead = datetime.now(ZoneInfo("America/Chicago")).date() + timedelta(days=14)
    makeup_day, makeup_words = ahead.isoformat(), f"{ahead:%a, %b} {ahead.day}"
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = app.test_client()
        client.get("/").close()
        course = seed_course(client, sections=[507, 508])
        group = client.get("/api/state").json["course_groups"][0]["id"]
        for number in (507, 508):
            answer = client.post(f"/api/course-groups/{group}/sections/{number}/roster", data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS)
            assert answer.status_code == 201, answer.json
        with connect(app.config["DATABASE"]) as db:
            term = db.execute("SELECT term_id FROM course_sections LIMIT 1").fetchone()[0]
            sections = {r["number"]: r["id"] for r in db.execute("SELECT id, number FROM course_sections")}
            bob = db.execute("SELECT id FROM student_enrollments WHERE uin='123456789'").fetchone()[0]
            alice = db.execute("SELECT id FROM student_enrollments WHERE uin='001234567'").fetchone()[0]
        # RULE 7 NOTICE (2026-10-05): Alice wrote on Wed, Sep 16, the day before her absence; the instructor's reply is not hers.
        from communications.record import record
        with app.extensions["rosters"].lock(), connect(app.config["DATABASE"]) as db:
            for ident, at, author in (("n1", "2026-09-16T15:00:00Z", "student"), ("n2", "2026-09-16T16:00:00Z", "instructor")):
                record(db, "gmail", ident, {"sent_at": at, "author_role": author, "author": {"name": "Alice"}, "recipients": [],
                                            "subject": "Out sick Thursday" if author == "student" else "Re: Out sick Thursday",
                                            "body_text": "I have the flu.", "attachments": [], "context": {"thread_id": "n"}},
                       [(alice, term, "author" if author == "student" else "to", "roster_address")], "n")
            db.commit()
        saved = client.post(f"/api/gradebook/terms/{term}/setup", headers=HEADERS, json={"base_revision": None, "request_id": "setup",
                            "setup": {"items": [{"title": "Quiz 3", "points": "10", "due_date": "2026-09-17"}, {"title": "HW 5", "points": "20", "due_date": "2026-10-02"}]}})
        assert saved.status_code == 200, saved.json
        assignment = app.extensions["documents"].create_assignment(course["definition_id"], "exam-1", "Exam 1")
        exams = app.extensions["exams"]
        event = exams.create({"term_id": term, "assignment_id": assignment["id"], "section_ids": list(sections.values())})
        exams.save_sitting(event["id"], {"version": event["version"], "label": "Main", "date": "2026-09-18", "time": "7 PM", "kind": "main"})

        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.goto(address + "/#absences")
                expect(page.locator("#absences-section .empty-state")).to_contain_text("No absences recorded yet")
                expect(page.locator(".sidebar nav [data-view='absences']")).to_have_attribute("aria-current", "page")

                # Record one from the header's primary action: the student picker finds Alice of Section 507.
                page.locator("#primary-action").click()
                dialog = page.locator("#absence-new-form")
                expect(dialog).to_be_visible()
                picker = dialog.locator("[data-picker-text]")
                picker.fill("alice 507")
                page.get_by_role("option", name="Example, Alice · section 507").click()
                dialog.locator("[name=first_date]").fill("2026-09-17")
                dialog.locator("[name=last_date]").fill("2026-09-18")
                dialog.locator("[name=reason]").select_option("illness")
                dialog.locator("[name=details]").fill("Flu <b>not bold</b>")
                dialog.get_by_role("button", name="Record absence").click()
                detail = page.locator("#absence-form")
                expect(detail).to_be_visible()
                expect(detail.locator(".dialog-header, header").first).to_contain_text("Example, Alice")
                detail.locator("[data-doc-upload]").set_input_files({"name": "note.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n% note\n"})
                expect(detail.locator(".absence-docs")).to_contain_text("note.pdf")
                # The document opens in the app's file viewer over the absence (VIEWER), never as a download.
                downloads = []
                page.on("download", lambda item: downloads.append(item))
                detail.locator(".absence-docs").get_by_role("link", name="note.pdf").click()
                viewer = page.locator("dialog.file-viewer")
                expect(viewer.locator("#file-viewer-title")).to_have_text("note.pdf")
                expect(viewer.locator(".file-viewer-none")).to_contain_text("could not be shown")  # not a real PDF: offered for Download
                expect(viewer.get_by_role("link", name="Download")).to_be_visible()
                viewer.get_by_role("button", name="Close the viewer").click()
                expect(viewer).to_be_hidden()
                expect(detail).to_be_visible()
                assert downloads == [], downloads

                # RULE 7 NOTICE: none on record until their own message is chosen; the instructor's reply is not offered.
                expect(detail.get_by_label(re.compile("^A death"))).not_to_be_checked()  # DEATHS WAIT: the form says which absence is one
                Path("test-results").mkdir(exist_ok=True)
                detail.get_by_label(re.compile("^A death")).scroll_into_view_if_needed()
                page.screenshot(path="test-results/absence-facts.png")
                notice = detail.locator("section[aria-labelledby='absence-notice-title']")
                expect(notice).to_contain_text("No notice on record")
                expect(notice).to_contain_text("Rule 7: notice before the absence, or by the end of Tue, Sep 22 when it could not come before; "
                                               "documentation by the end of Wed, Sep 23.")
                notice.get_by_role("button", name="Choose their message…").click()
                expect(notice.locator("[data-notice]")).to_have_count(1)
                notice.get_by_role("button", name="Out sick Thursday").click()
                expect(notice).to_contain_text("Chosen: Out sick Thursday, Wed, Sep 16, 10:00 AM. Save changes to keep it.")

                # Decide it excused: the gradebook (Quiz 3 is due Sep 17, not an iClicker session) and Exam 1's record (Sep 18) stay as
                # they were, and the absence offers no step that excuses work (requirement, 2026-10-01; ABSENCES NO-GRADEBOOK). The
                # decision says what Excused does to iClicker sessions (ICLICKER-CREDIT); with no iClicker import there is none to list.
                expect(detail.locator(".absence-decision")).to_contain_text("Excused gives every iClicker session within these dates full credit")
                detail.get_by_label("Excused", exact=True).check()
                detail.locator("[name=decision_note]").fill("Doctor's note received")
                detail.get_by_role("button", name="Save changes").click()
                expect(page.locator("#toast")).to_contain_text("Absence saved")
                expect(notice).to_contain_text("Wed, Sep 16, 10:00 AM · Notice in advance")
                Path("test-results").mkdir(exist_ok=True)
                notice.scroll_into_view_if_needed()
                page.screenshot(path="test-results/absence-notice.png")
                expect(detail.get_by_text("Work this absence covers")).to_have_count(0)
                expect(detail.get_by_role("button", name=re.compile("^Excuse"))).to_have_count(0)
                expect(detail.locator("[data-absence-iclicker]")).to_have_count(0)
                assert client.get(f"/api/gradebook/terms/{term}").json["cells"] == {}
                assert {p["expectation"] for p in exams.get(event["id"])["participants"]} == {"expected"}
                # MAKEUPS (2026-10-03): Exam 1 was given on Sep 18, within the excused absence, and no paper came back: a makeup is owed.
                work = detail.locator("[data-absence-work]")
                expect(work).to_contain_text("Exam 1")
                expect(work).to_contain_text("Makeup owed")
                detail.get_by_role("button", name="Close", exact=True).click()

                # The page opens on Waiting, first of the decision chips (requirement, 2026-10-08); with nothing waiting it says so and
                # offers every absence, which All (second) also shows.
                statuses = page.locator("[data-filter-group='absence-status'] [data-filter]")
                expect(statuses).to_have_text(["Waiting 0", "All 1", "Excused 1", "Not excused 0", "Withdrawn 0"])
                expect(statuses.first).to_have_attribute("aria-pressed", "true")
                expect(page.locator("#absences-section .empty-state")).to_contain_text("Nothing waits for your decision")
                page.locator("#absences-section .empty-state").get_by_role("button", name="Show all absences").click()
                assert "status=all" in page.url
                rows = page.locator("#absences-section tbody tr")
                expect(rows).to_have_count(1)
                expect(rows.first).to_contain_text("Excused")
                expect(rows.first).to_contain_text("Flu <b>not bold</b>")  # shown as text
                expect(rows.first.locator("td[data-label='Notice']")).to_have_text("In advance")
                # The Makeups view groups what is owed by assignment; Schedule makeup places Alice on a new makeup sitting through the
                # Exams page's own routes, and she stays owed until a paper comes back (the instructor's choice).
                page.locator("[data-filter-group='absence-view']").get_by_role("button", name="Makeups owed 1").click()
                assert "view=makeups" in page.url
                owed = page.locator("[data-makeup-group]")
                expect(owed).to_have_count(1)
                expect(owed).to_contain_text("Exam 1")
                expect(owed).to_contain_text("given Fri, Sep 18")
                expect(owed.locator("tbody tr")).to_contain_text("Example, Alice")
                # MAKEUP BOOKING (2026-10-04): an exam record with no seating chart is a quiz; while the term's cards are off its row
                # offers "Send booking card", and the switch above the groups says makeups are booked by hand.
                switch = page.locator("[data-makeup-booking]")
                expect(switch).to_contain_text("Quiz makeups are booked by hand")
                expect(owed.locator("tbody tr").get_by_role("button", name="Send booking card")).to_be_visible()
                owed.get_by_role("button", name="Schedule makeup…").click()
                schedule = page.locator("#makeup-schedule-form")
                expect(schedule).to_be_visible()
                schedule.locator("[name=date]").fill(makeup_day)
                schedule.locator("[name=location]").fill("BLOC 164")
                # An exact start is asked for (2026-10-05): without one nothing is saved.
                schedule.get_by_role("button", name="Schedule").click()
                expect(schedule.locator(".form-error")).to_contain_text("Choose when the makeup starts.")
                assert not [s for s in exams.get(event["id"])["sittings"] if s["kind"] == "makeup"]
                schedule.get_by_label("Starts", exact=True).fill("14:00")
                schedule.get_by_role("button", name="Schedule").click()
                expect(page.locator("#toast")).to_contain_text("1 student placed on the makeup sitting")
                expect(owed.locator("tbody tr .makeup-when")).to_have_text(f"{makeup_words} · 2:00 pm · BLOC 164")
                assert [s["time"] for s in exams.get(event["id"])["sittings"] if s["kind"] == "makeup"] == ["14:00"]
                # A dated sitting set by hand reads Booked (ABSENCES BOOKED BY HAND, 2026-10-16) in place of "Makeup owed"; she is still owed
                # until a paper comes back, which is what "Missed makeup…" is offered on.
                expect(owed.locator("tbody tr")).to_contain_text("Booked")
                expect(owed.locator("tbody tr").get_by_role("button", name="Missed makeup…")).to_be_visible()
                placed = [p for p in exams.get(event["id"])["participants"] if p["sitting_id"]]
                assert [(p["section_id"], p["expectation"]) for p in placed] == [(sections[507], "expected")], placed
                # KEEP THEIR SITTING (2026-10-06): opened again, Alice is already on a makeup sitting, so she starts unticked and her
                # row says where she is; ticking her for a new sitting says that Schedule moves her. Nothing is written on Cancel.
                owed.get_by_role("button", name="Schedule makeup…").click()
                expect(schedule).to_be_visible()
                alice_box = schedule.locator("[data-makeup-student]").first
                expect(alice_box).not_to_be_checked()
                expect(schedule.locator("[data-makeup-where]")).to_have_text("· already on this sitting")
                expect(schedule.locator("[data-makeup-moving]")).to_be_hidden()
                schedule.get_by_label("A new makeup sitting").check()
                expect(schedule.locator("[data-makeup-where]")).to_have_text(f"· on {makeup_words} · 2:00 PM · BLOC 164; tick to move them")
                alice_box.check()
                expect(schedule.locator("[data-makeup-moving]")).to_have_text("Schedule moves 1 ticked student off another makeup sitting.")
                schedule.get_by_role("button", name="Cancel").click()
                expect(schedule).to_be_hidden()
                assert [p["sitting_id"] for p in exams.get(event["id"])["participants"] if p["sitting_id"]] == [placed[0]["sitting_id"]]
                # Turning cards on asks first and names who gets one now: nobody, since Alice's makeup sitting is set by hand.
                expect(owed.locator("tbody tr").get_by_role("button", name="Send booking card")).to_have_count(0)
                switch.get_by_role("button", name="Turn on…").click()
                asked = page.locator("#makeup-booking-form")
                expect(asked).to_contain_text("No card goes out now")
                asked.get_by_role("button", name="Turn on").click()
                expect(page.locator("#toast")).to_contain_text("Quiz makeup cards turned on")
                expect(switch).to_contain_text("Quiz makeups book themselves")
                expect(switch.get_by_role("button", name="Turn off")).to_be_visible()
                page.locator("[data-filter-group='absence-view']").get_by_role("button", name=re.compile("^Absences")).click()
                expect(rows).to_have_count(1)
                page.locator("[data-filter-group='absence-status']").get_by_role("button", name="Waiting 0").click()
                expect(page.locator("#absences-section .empty-state")).to_contain_text("Nothing waits for your decision")
                assert "status=" not in page.url
                page.locator("#absences-search").fill("nobody")
                expect(page.locator("#absences-section .empty-state")).to_contain_text("No absences match")
                page.locator("#absences-search").fill("")

                # The student's gradebook card lists the absence; opened from the card, the absence offers the way back to it.
                page.goto(address + f"/#course/{group}/section/507")
                page.locator("[data-student]").filter(has_text="Example, Alice").first.click()
                card = page.locator("#gradebook-student-form")
                expect(card.locator("[data-absences-card]")).to_contain_text("Thu, Sep 17 – Fri, Sep 18")
                expect(card.locator("[data-absences-card]")).to_contain_text("Excused")
                # TOTAL ABSENCES: the days missed, counted once each and split by decision, the days folded below.
                total = card.locator(".absence-total")
                expect(total.locator(".absence-total-line")).to_have_text("2 absences 2 excused")
                total.locator("summary").click()
                expect(total.locator(".absence-total-list li")).to_have_count(2)
                expect(total.locator(".absence-total-list li").first).to_have_text(re.compile(r"Thu, Sep 17\s+Excused\s+Absence Thu, Sep 17 – Fri, Sep 18"))
                card.locator("[data-absences-card]").screenshot(path="test-results/absence-total.png")
                card.locator("[data-absences-card]").get_by_role("button", name="Thu, Sep 17 – Fri, Sep 18").click()
                expect(detail).to_be_visible()
                detail.get_by_role("button", name="Back to Example, Alice").click()
                expect(card).to_be_visible()
                expect(card.locator("[data-absences-card]")).to_contain_text("Excused")
                card.get_by_role("button", name="Close", exact=True).click()

                # TOTAL ABSENCES › ON THE ABSENCES PAGE: Totals by student, one group a section, most days first; a count opens to its days.
                page.goto(address + "/#absences?view=totals")
                groups = page.locator(".absence-totals-section")
                expect(groups.first.locator("h3")).to_have_text("Section 507")
                alice_total = groups.first.locator(".absence-totals-student").first
                expect(alice_total.locator("summary")).to_contain_text("Example, Alice")
                expect(alice_total.locator(".absence-totals-count")).to_have_text("2 absences")
                expect(alice_total.locator(".absence-total-list")).to_be_hidden()
                alice_total.locator("summary").click()
                expect(alice_total.locator(".absence-total-list li")).to_have_count(2)
                expect(page.locator(".absences-toolbar [data-filter-group='absence-status']")).to_have_count(0)
                page.locator("[data-absences-list]").screenshot(path="test-results/absence-totals.png")

                # The page keeps its place through the shell's refresh (it was redrawn whole every 30 s): forty more absences, the
                # table scrolled, then a refresh that finds one more absence patches the list in place.
                for day in range(1, 41):
                    made = client.post("/api/absences", headers=HEADERS, json={"section_id": sections[507], "enrollment_id": bob, "request_id": f"many-{day}",
                                       "first_date": f"2026-08-{day % 28 + 1:02d}", "last_date": f"2026-08-{day % 28 + 1:02d}", "reason": "illness",
                                       "details": f"Absence {day}", "status": "pending", "decision_note": ""})
                    assert made.status_code in (200, 201), made.json
                page.goto(address + "/#absences")
                expect(page.locator("#absences-section tbody tr")).to_have_count(40)  # Waiting: Alice's excused one is under All
                scroller = page.locator("#absences-section .roster-table-scroll")
                scroller.evaluate("node => { node.scrollTop = 400; }")
                kept = scroller.evaluate("node => node.scrollTop")
                assert kept > 0, "the absences table should scroll inside its region"
                made = client.post("/api/absences", headers=HEADERS, json={"section_id": sections[507], "enrollment_id": bob, "request_id": "many-41",
                                   "first_date": "2026-07-01", "last_date": "2026-07-01", "reason": "illness", "details": "One more",
                                   "status": "pending", "decision_note": ""})
                assert made.status_code in (200, 201), made.json
                with page.expect_response(lambda r: r.url.endswith("/api/absences") and r.request.method == "GET"):
                    page.evaluate('window.dispatchEvent(new Event("focus"))')
                expect(page.locator("#absences-section tbody tr")).to_have_count(41)
                assert scroller.evaluate("node => node.scrollTop") == kept, "the refresh reset the table's scroll"

                # Recorded in the evening on campus (8:30 pm CDT is already Oct 2 in UTC): the dates default to the campus day.
                evening = browser.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="America/Chicago")
                late = evening.new_page()
                late.clock.set_fixed_time("2026-10-02T01:30:00Z")
                late.goto(address + "/#absences")
                expect(late.locator("#absences-section tbody tr").first).to_be_visible()
                late.locator("#primary-action").click()
                expect(late.locator("#absence-new-form [name=first_date]")).to_have_value("2026-10-01")
                expect(late.locator("#absence-new-form [name=last_date]")).to_have_value("2026-10-01")
                evening.close()

                # Desktop only (DESIGN-LANGUAGE, 2026-10-03): at 1280px the page does not scroll sideways, the Decision chip is
                # on screen and the Reason column is not squeezed.
                narrow = browser.new_page(viewport={"width": 1280, "height": 900})
                narrow.goto(address + "/#absences")
                expect(narrow.locator("#absences-section tbody tr")).to_have_count(41)
                width = narrow.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                assert width <= 0, f"the page scrolls sideways by {width}px at 1280px"
                chip = narrow.locator("#absences-section tbody tr").first.locator("td[data-label='Decision'] > span").first
                box = chip.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= 1280, box
                reason = narrow.locator("#absences-section tbody tr").first.locator("td[data-label='Reason']").bounding_box()
                assert reason and reason["width"] > 200, f"the Reason column is squeezed to {reason}"
                narrow.close()
                Path("test-results").mkdir(exist_ok=True)
                page.goto(address + "/#absences")
                page.screenshot(path="test-results/absences-desktop.png", full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Absence browser checks passed: record, document, choose the Rule 7 notice, decide (gradebook untouched), makeups owed and scheduled, filters, gradebook card and the 1280px desktop width.")


if __name__ == "__main__":
    main()
