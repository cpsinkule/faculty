"""A student's recorded meeting, in the real shell (RECORDINGS "Student meetings", step 4): `#recordings/<id>` after one capture
round against the stand-in of the Plaud intake (tests/fake_intake.py), read by a SCRIPTED engine in the Assistant's place
(tests/test_recordings_student_meetings.py). The page says who the meeting was with and draws their two cards as the
Assistant's own; what was promised goes on their follow-up only by Approve, and a private note is saved only by Approve; an
appointment recorded as a no-show is the page's orange and the list's; the students-and-parts window gives each of two students
their part; one student's part is shown alone at the address their timeline opens.
Synthetic data, invented people; no model, Plaud or Google is reached.
RECORDINGS_SCREENSHOTS=<folder on /mnt> keeps the pictures taken at 1440 x 900."""
import io
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from app import create_app
from browser_course_pages import seed_course
from browser_recording_page import TONE
from fake_intake import FakeIntake
from test_assistant_memory import answering
from test_recordings import ALICE, BOB, NOW, ROWS, TUE, FakeGoogle, campus
from test_recordings_reading import Engine
from test_recordings_student_meetings import POSTED, REGRADE, VISIT, visit
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
        seed_course(client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        group = client.get("/api/state").json["course_groups"][0]["id"]
        for number, rows in ROWS.items():
            made = client.post(f"/api/course-groups/{group}/sections/{number}/roster",
                               data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
            assert made.status_code == 201, made.json
        with connect(app.config["DATABASE"]) as db:
            db.execute("UPDATE settings SET teacher_name='Chris Example' WHERE id=1")
            students = {row["uin"]: row["id"] for row in db.execute("SELECT id, uin FROM student_enrollments")}
        alice, bob = students[ALICE], students[BOB]
        booked = client.post("/api/student-support", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "kind": "appointment", "enrollment_id": alice, "title": "Exam 2 questions",
            "date": TUE, "time": "14:00", "end_time": "14:30"})
        assert booked.status_code == 201, booked.json
        record = app.extensions["recordings"]

        def tick():
            clock[0] += timedelta(seconds=1)
            return clock[0]

        record.now = tick
        record.google = FakeGoogle()
        fake.add("rec-visit", campus(TUE, "14:02"), minutes=12, title="Office hours", transcript=VISIT, audio=TONE.read_bytes())
        engine = Engine()
        engine.answer = visit
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
            assert (recording["name"], recording["for_you"]) == ("Example, Alice · MATH 140 · Fall 2026", 3), recording
            server = create_server(app, host="127.0.0.1", port=0)
            threading.Thread(target=server.run, daemon=True).start()
            address = f"http://127.0.0.1:{server.effective_port}"
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_context(reduced_motion="reduce", viewport={"width": 1440, "height": 900}).new_page()
                page.set_default_timeout(15000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#recordings")
                section = page.locator("#recordings-section")
                row = section.locator(".recording-row", has_text="Example, Alice · MATH 140 · Fall 2026")
                expect(row.locator('.chip[data-tone="warning"]')).to_have_text("3 for you")
                row.locator("a.recording-open").click()
                expect(page).to_have_url(f"{address}/#recordings/{recording['id']}")
                # Who the meeting was with, and what it left, in their words for each side. Never a coordinator, never a rule.
                outcome = section.locator("[data-recording-outcome]")
                student = outcome.locator(".recording-student")
                expect(student.locator(".recording-head")).to_have_text("Example, Alice Section 507")
                expect(outcome.locator('[data-items="you"] .recording-head')).to_have_text("Needs you 3")
                expect(outcome.locator('[data-items="others"] .recording-head')).to_have_text("They said they would 1")
                expect(outcome.locator('[data-items="answers"] .recording-head')).to_have_text("They told you 1")
                expect(outcome.get_by_role("button", name="Name the coordinator")).to_have_count(0)
                expect(outcome.locator('details[data-disclosure-key="recording-dropped"] summary')).to_contain_text("Left out by the quote check")
                assert section.locator('.chip[data-tone="warning"]').count() - section.locator('.assistant-card .chip[data-tone="warning"]').count() == 1
                # Her two cards, the Assistant's own, each saying in plain lines what it would do.
                cards = student.locator(".assistant-card")
                expect(cards).to_have_count(2)
                promised = cards.filter(has_text="Track 4 things said in your meeting with Example, Alice")
                expect(promised.locator(".recording-card-lines li")).to_have_count(4)
                expect(promised.locator(".recording-card-lines li", has_text=REGRADE)).to_contain_text("Yours")
                expect(promised.locator(".recording-card-lines")).to_contain_text("Check back Tue, Nov 17")
                note = cards.filter(has_text="Add a private note about Example, Alice")
                expect(note.locator(".recording-card-text")).to_contain_text("Recorded meeting on Tue, Nov 10.")
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-student-meeting-1440.png"), full_page=True)
                # What was promised goes on her follow-up only by Approve; the page then asks nothing more of it.
                assert [entry for entry in client.get("/api/student-support", query_string={"kind": "followup", "enrollment_id": alice}).json["items"] if entry["id"]] == []
                promised.get_by_role("button", name="Approve", exact=True).click()
                expect(student).to_contain_text("4 things from this meeting are on their follow-up.")
                expect(outcome.locator('[data-items="you"]')).to_have_count(0)
                (followup,) = [entry for entry in client.get("/api/student-support", query_string={"kind": "followup", "enrollment_id": alice}).json["items"] if entry["id"]]
                assert (followup["title"], len(followup["commitments"])) == ("Meeting on Tue, Nov 10", 4), followup
                # A private note of the meeting, saved only by Approve.
                assert client.get(f"/api/student-notes/students/{alice}").json["notes"] == []
                student.locator(".assistant-card").filter(has_text="Add a private note").get_by_role("button", name="Approve", exact=True).click()
                expect(student).to_contain_text("A note of this meeting is in your notes about them.")
                assert len(client.get(f"/api/student-notes/students/{alice}").json["notes"]) == 1
                expect(student.locator(".assistant-card")).to_have_count(0)
                # The appointment is recorded as a no-show although a recording began in it: orange here and in the list.
                saved = client.post(f"/api/student-support/{booked.json['id']}", headers=HEADERS, json={
                    "request_id": str(uuid.uuid4()), "base_revision": 1, "kind": "appointment", "enrollment_id": alice, "title": "Exam 2 questions",
                    "date": TUE, "time": "14:00", "end_time": "14:30", "state": "no_show"})
                assert saved.status_code == 200, saved.json
                page.goto(f"{address}/#recordings")
                expect(section.locator(".recording-row", has_text="Example, Alice").locator('.chip[data-tone="warning"]')).to_have_text("Appointment a no-show")
                page.goto(f"{address}/#recordings/{recording['id']}")
                expect(outcome.locator("[data-student-off]")).to_contain_text("is recorded as a no-show, yet this recording began in it.")
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-student-no-show-1440.png"), full_page=True)
                # Two students in one recording: each is given their part in the window, by minutes and seconds.
                outcome.get_by_role("button", name="Students and parts…").click()
                form = page.locator("#dialog[open] #recording-students-form")
                rows = form.locator("[data-part-row]")
                expect(rows).to_have_count(1)
                assert rows.nth(0).locator('select[name="student"]').input_value() == alice
                rows.nth(0).locator('input[name="from"]').fill("0:00")
                rows.nth(0).locator('input[name="to"]').fill("0:28")
                form.get_by_role("button", name="Add a student").click()
                rows.nth(1).locator('select[name="student"]').select_option(bob)
                rows.nth(1).locator('input[name="from"]').fill("0:28")
                rows.nth(1).locator('input[name="to"]').fill("0:20")
                form.get_by_role("button", name="Save", exact=True).click()
                expect(form.locator(".form-error")).to_contain_text("the end after the start")   # the server's own words
                rows.nth(1).locator('input[name="to"]').fill("0:36")
                form.get_by_role("button", name="Save", exact=True).click()
                expect(page.locator("#dialog")).not_to_be_visible()
                heads = outcome.locator(".recording-student .recording-head")
                expect(heads).to_have_text(["Example, Alice Section 507 · 0:00 to 0:28", "Example, Bob Section 507 · 0:28 to 0:36"])
                # It is read again for two students; each item then says whose it is.
                with record.lock():
                    assert record.round()["status"] == "ok"
                page.evaluate("() => window.dispatchEvent(new Event('focus'))")
                page.goto(f"{address}/#recordings")
                page.goto(f"{address}/#recordings/{recording['id']}")
                his = outcome.locator(".recording-item", has_text=POSTED)
                expect(his.locator(".recording-item-tags")).to_contain_text("Example, Bob")
                expect(outcome.locator(".recording-student").nth(1).locator(".assistant-card")).to_have_count(2)
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-two-students-1440.png"), full_page=True)
                # His part alone, at the address his timeline opens: his lines, what was said in them, and nothing of hers.
                entry = next(found for found in client.get(f"/api/communications/students/{bob}").json["entries"] if found["channel"] == "meeting")
                assert entry["context"]["href"] == f"#recordings/{recording['id']}?t=28000&to=36000", entry
                page.goto(f"{address}/{entry['context']['href']}")
                expect(outcome.locator("[data-recording-stretch]")).to_contain_text("Showing one part of this recording, 0:28 to 0:36.")
                expect(outcome.locator(".recording-student .recording-head")).to_have_text(["Example, Bob Section 507 · 0:28 to 0:36"])
                expect(outcome.locator(".recording-item")).to_have_count(1)
                expect(outcome).not_to_contain_text(REGRADE)
                expect(section.locator("details[data-recording-fold] summary")).to_contain_text("Transcript of this part · 2 lines")
                expect(section.locator(".recording-line")).to_have_count(2)
                outcome.get_by_role("link", name="Show the whole recording").click()
                expect(outcome.locator("[data-recording-stretch]")).to_have_count(0)
                expect(outcome.locator(".recording-student")).to_have_count(2)
                expect(section.locator(".recording-line")).to_have_count(9)
                assert {command for command, _ in fake.asked()} <= {"list", "show", "audio", "status", "check-now"}   # nothing was sent to Plaud or the intake but the five reads
                assert not errors, errors
                browser.close()
        finally:
            for patch in reversed(patches):
                patch.__exit__(None, None, None)
            if server:
                server.close()
            fake.stop()
    print("Recording student meeting checks passed.")


if __name__ == "__main__":
    main()
