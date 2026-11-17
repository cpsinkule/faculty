"""Memos, training and search by meaning, in the real shell (RECORDINGS "Memos, training, search by meaning", step 6) after capture
rounds against the stand-in of the Plaud intake (tests/fake_intake.py), with a SCRIPTED engine in the Assistant's place for the
memo's reading and for the run of one of its requests (tests/test_recordings_memos.py), and a STAND-IN embedder.

A memo the instructor filed opens on what they asked: each request with its words, the Assistant's answer and the ONE card
its run made (a calendar entry, automatic in chat, a card here), which only Approve writes; the request still with the
Assistant says so; the rule they said offers Remember. A memo with two voices says in orange why nothing was put to the
Assistant, and Ask the Assistant is their press. A recording filed as a course's training from the filing window is listed on
the course's page and is not read. Describing what was said finds the passage that says it without sharing a word.
Synthetic data, invented words; no model, Plaud, Google or embedding worker is reached.
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
from faculty_embedding import client as embedding
from recordings import meaning
from test_assistant_memory import answering
from test_recordings import NOW, ROWS, TUE, WED, FakeGoogle, campus
from test_recordings_memos import HOURS, MEMO, MISSING, RULE, THU, MemoBase, MemoEngine, StandIn, Turn
from test_recordings_page import SAID
from test_recordings_reading import said
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
        definition = seed_course(client, code="MATH 140", sections=[507, 508], multiple_sections=True)["definition_id"]
        group = client.get("/api/state").json["course_groups"][0]["id"]
        for number, rows in ROWS.items():
            made = client.post(f"/api/course-groups/{group}/sections/{number}/roster",
                               data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
            assert made.status_code == 201, made.json
        with connect(app.config["DATABASE"]) as db:
            db.execute("UPDATE settings SET teacher_name='Chris Example' WHERE id=1")

        def post(path, **body):
            answer = client.post(path, headers=HEADERS, json={"request_id": str(uuid.uuid4()), **body})
            assert answer.status_code == 200, answer.json
            return answer.json

        record, assistant = app.extensions["recordings"], app.extensions["assistant"]

        def tick():
            clock[0] += timedelta(seconds=1)
            return clock[0]

        def round_():
            with record.lock():
                assert record.round()["status"] == "ok"

        def found(plaud_id):
            with connect(app.config["DATABASE"]) as db:
                return db.execute("SELECT id FROM recordings WHERE plaud_id=?", (plaud_id,)).fetchone()[0]

        record.now = tick
        record.google = FakeGoogle()
        worker = StandIn()
        frq = app.extensions["frq_grading"]
        record.reading.connection = lambda: {"name": "scripted", "kind": "hosted", "model": "scripted-model"}
        patches = [mock.patch.object(frq, "cloud_provider", return_value=MemoEngine()), answering(),
                   mock.patch.object(assistant.followups, "endpoint", return_value={"name": "Synthetic", "kind": "hosted", "model": "fake"}),
                   mock.patch.object(embedding, "configured", lambda: True), mock.patch.object(embedding, "text_vectors", worker.text_vectors),
                   mock.patch.object(embedding, "status", worker.status), mock.patch.object(meaning, "PASSAGE", 30)]
        for patch in patches:
            patch.__enter__()
        server = None
        try:
            # Three recordings that fell in no calendar entry: a memo, a memo with a second voice, and a training session.
            fake.add("rec-memo", campus(WED, "20:00"), minutes=1, title="Note", transcript=MEMO, audio=TONE.read_bytes())
            fake.add("rec-two", campus(WED, "20:30"), minutes=1, title="Note 2", audio=TONE.read_bytes(),
                     transcript=MEMO + [said(16, "Speaker 2", "Speaker 2", "Hey, are you coming to lunch?")])
            fake.add("rec-training", campus(TUE, "15:00"), minutes=50, title="Grader training", transcript=SAID, audio=TONE.read_bytes())
            round_()
            memo, two, training = found("rec-memo"), found("rec-two"), found("rec-training")
            post(f"/api/recordings/{memo}/file", kind="memo")
            post(f"/api/recordings/{two}/file", kind="memo")
            round_()
            # The web process's dispatcher runs the first request of the first memo: a calendar entry, which waits as a card.
            with mock.patch("assistant.conversations.engine_for", return_value=Turn(MemoBase.office_hours)):
                assert assistant.followups.dispatch_one()
                for _ in range(1000):
                    if not assistant.workflows.busy():
                        break
                    time.sleep(.01)
            listed = {row["id"]: row for row in client.get("/api/recordings").json["recordings"]}
            assert (listed[memo]["name"], listed[memo]["for_you"], listed[two]["for_you"]) == ("Memo", 2, 3), listed
            server = create_server(app, host="127.0.0.1", port=0)
            threading.Thread(target=server.run, daemon=True).start()
            address = f"http://127.0.0.1:{server.effective_port}"
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_context(reduced_motion="reduce", viewport={"width": 1440, "height": 900}).new_page()
                page.set_default_timeout(15000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#recordings/{memo}")
                section = page.locator("#recordings-section")
                outcome = section.locator("[data-recording-outcome]")
                # A MEMO: one voice, taken as the instructor's; what they asked, and the rule they said.
                expect(outcome.locator(".recording-reading .recording-capture")).to_contain_text("One voice, taken as yours.")
                expect(outcome.locator('[data-items="asked"] .recording-head')).to_have_text("You asked 2 1 for you")
                expect(outcome.locator('[data-items="rules"] .recording-head')).to_have_text("Rules you said 1 1 for you")
                expect(outcome.get_by_role("button", name="Name the coordinator")).to_have_count(0)
                hours = outcome.locator(".recording-item", has_text=HOURS)
                expect(hours.locator(".recording-item-quote q")).to_have_text("Add office hours this Thursday from two to three in Blocker 220.")
                expect(hours.locator(".recording-ask .recording-capture")).to_have_text("The Assistant answered. 1 change waits for your approval.")
                expect(hours.locator(".recording-ask-answer")).to_have_text("I prepared the entry (waiting_for_approval). It waits for your approval.")
                card = hours.locator(".assistant-card")
                expect(card).to_have_count(1)
                expect(card).to_contain_text("Add office hours on Thursday, Nov 12 from 2 to 3 PM, as your memo asked.")
                missing = outcome.locator(".recording-item", has_text=MISSING)
                expect(missing.locator(".recording-ask .recording-capture")).to_have_text("Waiting for the Assistant.")
                expect(missing.get_by_role("button", name="Ask the Assistant")).to_have_count(0)
                expect(outcome.locator(".recording-item", has_text=RULE).get_by_role("button", name="Remember…")).to_have_count(1)
                # Orange: the two "for you" counts only (the card is the Assistant's own).
                assert section.locator('[data-tone="warning"]').count() - section.locator('.assistant-card [data-tone="warning"]').count() == 2
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-memo-1440.png"), full_page=True)
                # Nothing is on the calendar until Approve; Approve writes it through the Calendar's own route.
                on_thursday = lambda: [event for event in client.get("/api/calendar", query_string={"start": THU, "end": THU}).json["events"]  # noqa: E731
                                       if event["title"] == "Office hours"]
                assert on_thursday() == []
                card.get_by_role("button", name="Approve", exact=True).click()
                expect(hours.locator(".recording-ask")).to_contain_text("Its changes: 1 approved and done.")
                expect(hours.locator(".assistant-card")).to_have_count(0)
                assert [(event["date"], event["time"]) for event in on_thursday()] == [(THU, "14:00")]
                # Ask again carries a sentence of the instructor's own; the request is with the Assistant again.
                hours.get_by_role("button", name="Ask again…").click()
                dialog = page.locator("#dialog")
                expect(dialog).to_contain_text("Changes of that answer you have not approved yet are withdrawn; what you approved stands.")
                dialog.locator('textarea[name="guidance"]').fill("Make it the Zoom room instead.")
                dialog.get_by_role("button", name="Ask again", exact=True).click()
                expect(hours.locator(".recording-ask .recording-capture")).to_have_text("Waiting for the Assistant.")
                # Done settles a request: it is folded under Already done.
                missing.get_by_role("button", name="Done").click()
                expect(outcome.locator('[data-items="asked"] .recording-head')).to_have_text("You asked 1")
                expect(outcome.locator('details[data-disclosure-key="recording-done"] summary')).to_contain_text("Already done")

                # NOT SURE: two voices. The page says why in orange, nothing was asked, and the press is the instructor's.
                page.goto(f"{address}/#recordings/{two}")
                expect(outcome.locator(".recording-reading .recording-capture")).to_contain_text("Plaud heard 2 voices in this memo")
                expect(outcome.locator('.recording-reading .recording-capture[data-tone="warning"]')).to_have_count(1)
                first = outcome.locator(".recording-item", has_text=HOURS)
                expect(first.locator('.recording-ask .recording-capture[data-tone="warning"]')).to_contain_text("Nothing was put to the Assistant")
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-memo-not-sure-1440.png"), full_page=True)
                first.get_by_role("button", name="Ask the Assistant").click()
                expect(first.locator(".recording-ask .recording-capture")).to_have_text("Waiting for the Assistant.")
                with connect(app.config["DATABASE"]) as db:
                    asked = db.execute("SELECT COUNT(*) FROM assistant_preparations r JOIN recording_items i ON i.id=r.target WHERE r.kind='memo' AND i.recording_id=?", (two,)).fetchone()[0]
                assert asked == 1, asked   # that one request, and no other

                # A TRAINING, kept from the filing window under its course (Keep, since 2026-11-14: ONLY WHAT IS FACULTY'S IS KEPT).
                page.goto(f"{address}/#recordings")
                waiting = section.locator('.recording-row[data-filing="needs_filing"]')
                expect(waiting).to_have_count(1)
                waiting.get_by_role("button", name="Keep…", exact=True).click()
                form = page.locator("#recording-file-form")
                form.locator('select[name="kind"]').select_option("training")
                expect(form.locator('select[name="definition_id"]')).to_be_visible()
                expect(form).to_contain_text("Faculty does not read a training for items.")
                expect(form.locator('[data-file-part="student"]')).to_be_hidden()
                form.get_by_role("button", name="Keep", exact=True).click()
                expect(section.locator(".recording-row", has_text="Training · MATH 140")).to_have_count(1)
                round_()   # fetched now; a training is kept, and never read
                assert client.get(f"/api/recordings/{training}").json["reading"]["state"] == "not_read"
                # The course's page links its trainings, and the list shows them alone.
                page.goto(f"{address}/#course/{group}/home")
                link = page.locator(".course-trainings-link")
                expect(link.locator("[data-trainings-status]")).to_have_text("1 recording · latest Tue, Nov 10")
                link.get_by_role("link", name="Training recordings").click()
                expect(page).to_have_url(f"{address}/#recordings?course={definition}")
                listed = section.locator("[data-recordings-trainings]")
                expect(listed.locator(".recording-head")).to_have_text("Training · MATH 140 1")
                expect(listed.locator(".recording-row")).to_have_count(1)
                expect(section.locator(".recording-row", has_text="Memo")).to_have_count(0)
                if shots:
                    page.screenshot(path=os.path.join(shots, "recordings-trainings-1440.png"), full_page=True)
                listed.locator("a.recording-open").click()
                expect(page).to_have_url(f"{address}/#recordings/{training}")
                expect(outcome).to_contain_text("A training is kept for reference under its course and is not read for items.")
                expect(outcome.get_by_role("button", name="Read again")).to_have_count(0)

                # SEARCH BY MEANING: the worker's turns read the kept transcripts; a description finds what was said.
                while record.index_turn():
                    record.meaning._next = 0
                page.goto(f"{address}/#recordings")
                search = section.locator("[data-recordings-search]")
                search.fill("where students sit")
                near = section.locator("[data-recordings-nearest]")
                expect(near.locator(".recording-head")).to_contain_text("Nearest by meaning")
                expect(near).to_contain_text("Nearest is not a finding: read them.")
                expect(near.locator(".recording-hit").first).to_contain_text("And the seating charts go out Thursday, 100% of them.")
                expect(section.locator("[data-recordings-found]")).to_contain_text("No kept recording has a line with every one of those words.")
                if shots:
                    page.screenshot(path=os.path.join(shots, "recordings-nearest-1440.png"), full_page=True)
                near.locator(".recording-hit a.recording-open").first.click()
                expect(page).to_have_url(f"{address}/#recordings/{training}?t=3200")
                assert {command for command, _ in fake.asked()} <= {"list", "show", "audio", "status", "check-now"}   # nothing was sent to Plaud or the intake but the five reads
                assert not errors, errors
                browser.close()
        finally:
            for patch in reversed(patches):
                patch.__exit__(None, None, None)
            if server:
                server.close()
            fake.stop()
    print("Recording memo checks passed.")


if __name__ == "__main__":
    main()
