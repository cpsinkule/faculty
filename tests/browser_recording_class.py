"""A recorded class, in the real shell (RECORDINGS "Classes", step 5): `#recordings/<id>` after one capture round against the
stand-in of the Plaud intake (tests/fake_intake.py), read by a SCRIPTED engine in the Assistant's place
(tests/test_recordings_classes.py). The page opens on what the class reached beside what was planned for that section's day:
a day planned 5.6 to 5.8 and recorded through 5.7 is orange and carries ONE card that would move 5.8 to the next class day;
the section's Class schedule page shows the same on that day; the plan changes only by Approve; a day told to the class that
disagrees with Faculty's records needs the instructor with both said; a dismissed section is no longer reached, and a
declined move leaves the plan.
Synthetic data, an invented class; no model, Plaud or Google is reached.
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
from test_recordings import NOW, ROWS, WED, FakeGoogle, campus
from test_recordings_classes import FRI, LESSON, OUTLINE, SEVEN, ClassEngine, lesson
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
            sections = {row["number"]: row["id"] for row in db.execute("SELECT id, number FROM course_sections")}
            term = db.execute("SELECT id FROM course_terms").fetchone()[0]

        def post(path, **body):
            answer = client.post(path, headers=HEADERS, json={"request_id": str(uuid.uuid4()), **body})
            assert answer.status_code == 200, answer.json
            return answer.json

        # Section 507 meets Monday, Wednesday and Friday at 8, and Wednesday was planned 5.6 to 5.8.
        post(f"/api/scheduling/terms/{term}", base_revision=0, first_day="2026-08-24", last_day="2026-12-03", entries=[
            {"kind": "class", "weekdays": [0, 2, 4], "start": "08:00", "end": "08:50", "room": "HELD 107", "section_id": sections[507]}])
        post(f"/api/scheduling/terms/{term}/use", confirmation="use", fingerprint=client.get(f"/api/scheduling/terms/{term}/use").json["fingerprint"])
        plan = f"/api/scheduling/terms/{term}/class-schedule"
        post(plan, base_revision=0, topics={WED: {"notes": ["5.6", "5.7", "5.8"], "note": ""}},
             set=[{"kind": "homework", "title": "Homework 5.6", "sections": [], "date": FRI},
                  {"kind": "exam", "title": "Exam 3", "sections": [], "date": "2026-11-19"}])
        record = app.extensions["recordings"]

        def tick():
            clock[0] += timedelta(seconds=1)
            return clock[0]

        record.now = tick
        record.google = FakeGoogle()
        fake.add("rec-class", campus(WED, "08:01"), minutes=49, title="Recording", transcript=LESSON, audio=TONE.read_bytes())
        engine = ClassEngine()
        engine.answer = lesson
        frq = app.extensions["frq_grading"]
        record.reading.connection = lambda: {"name": "scripted", "kind": "hosted", "model": "scripted-model"}
        patches = [mock.patch.object(frq, "cloud_provider", return_value=engine), answering(),
                   mock.patch("course_notes.service.current_sections", return_value=OUTLINE)]
        for patch in patches:
            patch.__enter__()
        server = None
        try:
            with record.lock():
                assert record.round()["status"] == "ok"
            recording = client.get("/api/recordings").json["recordings"][0]
            assert (recording["name"], recording["for_you"]) == ("Class · MATH 140 Section 507", 3), recording
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
                row = section.locator(".recording-row", has_text="Class · MATH 140 Section 507")
                expect(row.locator('.chip[data-tone="warning"]')).to_have_text("3 for you")
                row.locator("a.recording-open").click()
                expect(page).to_have_url(f"{address}/#recordings/{recording['id']}")
                # The class's day first: what was reached beside what was planned, and in orange what was not reached.
                outcome = section.locator("[data-recording-outcome]")
                day = outcome.locator("[data-recording-class]")
                expect(day.locator(".recording-head")).to_contain_text("Section 507 · Wed, Nov 11")
                expect(day.locator(".recording-class-rows dd").nth(0)).to_have_text("5.6 Exponential Functions · 5.7 Combining Functions · 5.8 Logarithms")
                expect(day.locator(".recording-class-rows dd").nth(1)).to_have_text("5.6 Exponential Functions · 5.7 Combining Functions")
                expect(day.locator("[data-class-differs]")).to_have_text("Not reached: 5.8 Logarithms.")
                # A class has its own groups: no coordinator, no student, and nothing but its two kinds.
                expect(outcome.locator('[data-items="you"] .recording-head')).to_have_text("Needs you 2")
                expect(outcome.locator('[data-items="covered"] .recording-head')).to_have_text("What the class reached 2")
                expect(outcome.locator('[data-items="told"] .recording-head')).to_have_text("Told the class 1")
                expect(outcome.get_by_role("button", name="Name the coordinator")).to_have_count(0)
                expect(outcome.locator(".recording-student")).to_have_count(0)
                expect(outcome.locator('[data-items="you"]')).to_contain_text("You told the class Wed, Nov 18. Faculty has Exam 3 given Thu, Nov 19 (the class schedule's plan).")
                expect(outcome.locator('details[data-disclosure-key="recording-dropped"] summary')).to_contain_text("Left out by the quote check")
                # Orange: the day's unreached section and the count of what needs the instructor, beside the waiting card.
                assert section.locator('[data-tone="warning"]').count() - section.locator('.assistant-card [data-tone="warning"]').count() == 2
                # THE MOVE IS OFFERED: one card, the Assistant's own, saying in plain lines what it would write.
                card = day.locator(".assistant-card")
                expect(card).to_have_count(1)
                expect(card).to_contain_text("Move 5.8 Logarithms from Wed, Nov 11 to Fri, Nov 13 on Section 507's class schedule")
                expect(card.locator(".recording-card-lines li")).to_have_text(["Wed, Nov 11 5.6, 5.7", "Fri, Nov 13 5.8"])
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-class-1440.png"), full_page=True)
                # THE CLASS DAY SHOWS IT on the section's own Class schedule page.
                day.get_by_role("link", name="Class schedule").click()
                expect(page).to_have_url(f"{address}/#course/{group}/section/507/schedule")
                recorded = page.locator(f'[data-recorded-day="{WED}"]')
                expect(recorded.locator("a")).to_have_text("Reached 5.6, 5.7")
                expect(recorded.locator('.chip[data-tone="warning"]')).to_have_text("Not reached 5.8")
                if shots:
                    page.screenshot(path=os.path.join(shots, "class-schedule-recorded-1440.png"), full_page=True)
                assert client.get(plan).json["revision"] == 1   # nothing changed by itself
                recorded.locator("a").click()
                expect(page).to_have_url(f"{address}/#recordings/{recording['id']}")
                # The plan changes only by Approve: 5.8 moves to Friday for this section, and the day is no longer orange.
                card.get_by_role("button", name="Approve", exact=True).click()
                expect(day).to_contain_text("Reached what was planned for this day.")
                expect(day.locator("[data-class-differs]")).to_have_count(0)
                expect(day.locator(".assistant-card")).to_have_count(0)
                topics = client.get(plan).json["topics"]
                assert (topics[WED]["sections"][sections[507]]["notes"], topics[FRI]["sections"][sections[507]]["notes"]) == (["5.6", "5.7"], ["5.8"]), topics
                expect(outcome.locator('[data-items="you"] .recording-head')).to_have_text("Needs you 2")   # the two days told to the class
                # A section dismissed as not so is no longer reached: the day is orange again, and the move is theirs to propose.
                outcome.locator(".recording-item", has_text=SEVEN).get_by_role("button", name="Dismiss").click()
                expect(day.locator("[data-class-differs]")).to_have_text("Not reached: 5.7 Combining Functions.")
                day.get_by_role("button", name="Propose the move to Fri, Nov 13").click()
                again = day.locator(".assistant-card")
                expect(again.locator(".recording-card-lines li")).to_have_text(["Wed, Nov 11 5.6", "Fri, Nov 13 5.7, 5.8"])
                # Declined, the plan stands and the day asks nothing more.
                again.get_by_role("button", name="Decline").click()
                expect(day).to_contain_text("Not reached: 5.7 Combining Functions. You declined the move, so the plan stands.")
                expect(day.locator("[data-class-differs]")).to_have_count(0)
                assert client.get(plan).json["topics"][WED]["sections"][sections[507]]["notes"] == ["5.6", "5.7"]
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-class-declined-1440.png"), full_page=True)
                page.goto(f"{address}/#course/{group}/section/507/schedule")
                recorded = page.locator(f'[data-recorded-day="{WED}"]')
                expect(recorded).to_contain_text("Reached 5.6 not reached 5.7, plan left as it is")
                expect(recorded.locator('.chip[data-tone="warning"]')).to_have_count(0)
                expect(page.locator(f'tr[data-date="{FRI}"] .cs-topics')).to_contain_text("5.8 Logarithms")
                assert {command for command, _ in fake.asked()} <= {"list", "show", "audio", "status", "check-now"}   # nothing was sent to Plaud or the intake but the five reads
                assert not errors, errors
                browser.close()
        finally:
            for patch in reversed(patches):
                patch.__exit__(None, None, None)
            if server:
                server.close()
            fake.stop()
    print("Recording class checks passed.")


if __name__ == "__main__":
    main()
