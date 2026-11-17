"""What came out of a recording, in the real shell (RECORDINGS, step 3): `#recordings/<id>` after one capture round against the
stand-in of the Plaud intake (tests/fake_intake.py), read by a SCRIPTED engine in the Assistant's place (tests/test_recordings_reading.py).
The page opens on what the meeting left the instructor with: what needs them first and alone in orange, the transcript folded;
an item plays its quote from the kept audio; Done folds it away; Remember keeps a standing rule as a memory in words the
instructor may edit; a date not on the calendar is an Approve card and the entry is made only by Approve; Read again is a new
reading; the coordinator is a name; a moment in the address unfolds the transcript; the list counts what each recording needs.
Synthetic data, invented people; no model, Plaud or Google is reached.
RECORDINGS_SCREENSHOTS=<folder on /mnt> keeps the pictures taken at 1440 x 900."""
import io
import json
import os
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest import mock

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from app import create_app
from browser_course_pages import seed_course
from browser_recording_page import AUDIO, TONE, until
from fake_intake import FakeIntake
from test_assistant_memory import answering
from test_recordings import NOW, ROWS, TUE, FakeGoogle, campus
from test_recordings_page import OUTLINE, SUMMARY
from test_recordings_reading import MEETING, Engine, answer
from test_rosters import csv_bytes
from test_storage import HEADERS
from rosters import connect


def main():
    shots = os.environ.get("RECORDINGS_SCREENSHOTS")
    with tempfile.TemporaryDirectory() as directory:
        clock = [NOW]
        fake = FakeIntake(directory).start(clock=lambda: clock[0])
        clean = {k: v for k, v in os.environ.items() if not k.startswith("FACULTY_")}
        with mock.patch.dict(os.environ, {**clean, "FACULTY_PLAUD_INTAKE": str(fake.command)}, clear=True):
            app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        course = seed_course(client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        group = client.get("/api/state").json["course_groups"][0]["id"]
        for number, rows in ROWS.items():
            made = client.post(f"/api/course-groups/{group}/sections/{number}/roster",
                               data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
            assert made.status_code == 201, made.json
        with connect(app.config["DATABASE"]) as db:
            db.execute("UPDATE settings SET teacher_name='Chris Example' WHERE id=1")
            term = db.execute("SELECT id FROM course_terms").fetchone()[0]
            sections = [row[0] for row in db.execute("SELECT id FROM course_sections")]
        exams = app.extensions["absences"].exams
        assignment = app.extensions["documents"].create_assignment(course["definition_id"], "exam-3", "Exam 3")["id"]
        exam = exams.create({"term_id": term, "assignment_id": assignment, "section_ids": sections})
        exams.save_sitting(exam["id"], {"version": exam["version"], "label": "Exam 3", "kind": "main", "date": "2026-11-19"})
        record = app.extensions["recordings"]

        def tick():
            clock[0] += timedelta(seconds=1)
            return clock[0]

        record.now = tick
        google = record.google = FakeGoogle()
        google.add("MATH 140 - all instructors", TUE, "10:00", "11:15")
        fake.add("rec-meeting", campus(TUE, "10:02"), minutes=71, title="11-10 Meeting", transcript=MEETING, outline=OUTLINE, notes=SUMMARY,
                 audio=TONE.read_bytes())
        engine = Engine()
        engine.answer = answer
        frq = app.extensions["frq_grading"]
        record.reading.connection = lambda: {"name": "scripted", "kind": "hosted", "model": "scripted-model"}
        patches = [mock.patch.object(frq, "cloud_provider", return_value=engine), answering()]
        for patch in patches:
            patch.__enter__()
        server = None
        try:
            with record.lock():
                assert record.round()["status"] == "ok"
            recording = client.get("/api/recordings").json["recordings"][0]
            assert recording["for_you"] == 6, recording
            server = create_server(app, host="127.0.0.1", port=0)
            threading.Thread(target=server.run, daemon=True).start()
            address = f"http://127.0.0.1:{server.effective_port}"
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_context(reduced_motion="reduce", viewport={"width": 1440, "height": 900}).new_page()
                page.set_default_timeout(15000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # The list says what each recording needs of the instructor, and its header says it first.
                page.goto(f"{address}/#recordings")
                section = page.locator("#recordings-section")
                row = section.locator(".recording-row", has_text="MATH 140 · Fall 2026")
                expect(row.locator('.chip[data-tone="warning"]')).to_have_text("6 for you")
                expect(page.locator(".page-heading")).to_contain_text("6 items from your recordings for you")
                row.locator("a.recording-open").click()
                expect(page).to_have_url(f"{address}/#recordings/{recording['id']}")
                # The page opens on what came out of it: what needs the instructor first, the only orange; the transcript folded.
                outcome = section.locator("[data-recording-outcome]")
                you = outcome.locator('[data-items="you"]')
                expect(you.locator(".recording-head")).to_have_text("Needs you 6")
                expect(outcome.locator(".recording-capture")).to_contain_text("It took “Chris” as you.")
                # Orange only on what needs the instructor: the count of it, and the Approve card that waits among it.
                assert section.locator('.chip[data-tone="warning"]').count() - section.locator('.assistant-card .chip[data-tone="warning"]').count() == 1
                assert you.bounding_box()["y"] < 500, "the work starts on the first screen"
                fold = section.locator("details[data-recording-fold]")
                expect(fold.locator("summary")).to_contain_text("Transcript · 13 lines")
                assert fold.get_attribute("open") is None
                expect(outcome.locator('[data-items="answers"] .recording-item')).to_have_count(1)
                expect(outcome.locator('[data-items="rulings"] .recording-item')).to_contain_text("Exam 3 · question 5")
                expect(outcome.locator('[data-items="others"] .recording-item')).to_contain_text("For the coordinator")
                done = outcome.locator('details[data-disclosure-key="recording-done"]')
                assert done.get_attribute("open") is None
                expect(outcome.locator('details[data-disclosure-key="recording-dropped"] summary')).to_contain_text("Left out by the quote check")
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-reading-1440.png"), full_page=True)
                # An item plays its quote from the audio from the archive, and says what Faculty's own records found.
                seating = you.locator(".recording-item", has_text="Rebuild the Exam 3 seating charts")
                expect(seating.locator(".recording-item-found")).to_contain_text("Faculty has no seating chart built for Exam 3 yet.")
                seating.get_by_role("button", name="Play from 0:04").click()
                until(page, f"!{AUDIO}.paused && {AUDIO}.currentTime > 4.2 && {AUDIO}.currentTime < 8")
                page.evaluate(f"{AUDIO}.pause()")
                # Done folds it away; the count follows; Open again brings it back.
                seating.get_by_role("button", name="Done", exact=True).click()
                expect(you.locator(".recording-head")).to_have_text("Needs you 5")
                done.locator("summary").click()
                moved = done.locator(".recording-item", has_text="Rebuild the Exam 3 seating charts")
                expect(moved.locator(".recording-item-found")).to_have_text("You marked it done.")
                moved.get_by_role("button", name="Open again").click()
                expect(you.locator(".recording-head")).to_have_text("Needs you 6")
                # Remember: a standing rule becomes a memory only here, in words the instructor may edit first.
                rule = you.locator(".recording-item", has_text="Late work is never accepted after solutions are posted.")
                rule.get_by_role("button", name="Remember…").click()
                form = page.locator("#dialog[open] #recording-remember-form")
                expect(form).to_contain_text("Avery Quill said, at 0:32: “From now on, late work is never accepted after solutions are posted.”")
                expect(form.locator('select[name="scope"] option')).to_have_text(["MATH 140 · Fall 2026", "MATH 140, every term", "Everything"])
                form.locator('textarea[name="content"]').fill("Late work is not accepted once solutions are posted.")
                form.get_by_role("button", name="Remember", exact=True).click()
                expect(page.locator("#dialog")).not_to_be_visible()
                expect(done.locator(".recording-item", has_text="Late work is never accepted")).to_contain_text("Kept as a memory.")
                with connect(app.config["DATABASE"]) as db:
                    kept = [tuple(row) for row in db.execute("SELECT content, source, scope FROM assistant_memory_latest")]
                assert kept == [("Late work is not accepted once solutions are posted.", "recording", "term")], kept
                # A date not on the calendar is an Approve card, drawn here as everywhere; the entry is made only by Approve.
                meeting = you.locator(".recording-item", has_text="The Exam 3 grading meeting is Thursday at 3 pm")
                expect(meeting.locator(".recording-item-found").first).to_contain_text("Not on your calendar on Thu, Nov 12.")
                card = meeting.locator(".recording-item-card .assistant-card")
                expect(card).to_contain_text("Exam 3 grading meeting")
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-reading-card-1440.png"), full_page=True)
                card.get_by_role("button", name="Approve", exact=True).click()
                approved = done.locator(".recording-item", has_text="The Exam 3 grading meeting is Thursday at 3 pm")
                expect(approved.locator(".recording-item-found").first).to_contain_text("On your calendar that day: “Exam 3 grading meeting” 3:00 PM.")
                events = client.get("/api/calendar", query_string={"start": "2026-11-12", "end": "2026-11-12"}).json["events"]
                assert [event["title"] for event in events if event["source"] == "custom"] == ["Exam 3 grading meeting"], events
                # The coordinator is a name, kept for the term.
                outcome.get_by_role("button", name="Name the coordinator").click()
                who = page.locator("#dialog[open] #recording-coordinator-form")
                who.locator('input[name="name"]').fill("Avery Quill")
                who.get_by_role("button", name="Save name").click()
                expect(outcome.get_by_role("button", name="Coordinator: Avery Quill")).to_be_visible()
                # Read again: asked for here, made by the worker, and shown by the page's own quiet refresh.
                outcome.get_by_role("button", name="Read again").click()
                expect(outcome.locator(".recording-capture")).to_contain_text("Faculty is reading it again now.")
                expect(outcome.get_by_role("button", name="Read again")).to_be_disabled()
                record.run_due()
                assert engine.asked[-1]["payload"]["facts"]["coordinator"] == "Avery Quill"
                page.evaluate("() => window.dispatchEvent(new Event('focus'))")
                expect(outcome.get_by_role("button", name="Read again")).to_be_enabled()
                expect(outcome.locator(".recording-capture")).not_to_contain_text("reading it again")
                expect(done.locator(".recording-item", has_text="Late work is never accepted")).to_contain_text("Kept as a memory.")   # it stayed answered
                # A moment in the address unfolds the transcript at that line (a moment inside the nine seconds of test audio).
                page.goto(f"{address}/#recordings/{recording['id']}?t=8000")
                expect(section.locator("details[data-recording-fold]")).to_have_attribute("open", "")
                expect(section.locator('.recording-line[data-line="2"]')).to_have_attribute("aria-current", "true")
                # Find unfolds it too.
                page.goto(f"{address}/#recordings")
                expect(section.locator("[data-recordings-search]")).to_be_visible()
                page.goto(f"{address}/#recordings/{recording['id']}")
                expect(section.locator("details[data-recording-fold]")).not_to_have_attribute("open", "")
                section.locator("[data-recording-find]").fill("answer key")
                expect(section.locator("details[data-recording-fold]")).to_have_attribute("open", "")
                expect(section.locator("[data-recording-found]")).to_have_text("1 line found. Enter goes to the next.")
                assert {command for command, _ in fake.asked()} <= {"list", "show", "audio", "status", "check-now"}   # nothing was sent to Plaud or the intake but the five reads
                assert not errors, errors
                browser.close()
        finally:
            for patch in reversed(patches):
                patch.__exit__(None, None, None)
            if server:
                server.close()
            fake.stop()
    print("Recording reading checks passed.")


if __name__ == "__main__":
    main()
