"""One recording's page in the real shell (RECORDINGS, step 2): `#recordings/<id>` after one capture round against the loopback
stand-in of the Plaud intake (tests/fake_intake.py), with real Ogg Opus audio (tests/fixtures/recordings/tone.ogg: nine seconds of a
synthetic tone). A kept recording's name opens its page; a line's time plays the archive's file from that moment, asked for in
ranges; the line being said is marked, never in orange; Find marks lines; a speaker is named without the audio starting over;
Plaud's summary is folded as Plaud's words; a search of what was said opens the recording at the moment; leaving the page stops
the audio. Synthetic data, invented people; nothing reaches Plaud.
RECORDINGS_SCREENSHOTS=<folder on /mnt> keeps the picture taken at 1440 x 900."""
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
from fake_intake import FakeIntake
from test_recordings import NOW, ROWS, TUE, FakeGoogle, campus
from test_recordings_page import OUTLINE, SAID, SUMMARY
from test_rosters import csv_bytes
from test_storage import HEADERS

TONE = Path(__file__).parent / "fixtures" / "recordings" / "tone.ogg"
AUDIO = "document.querySelector('audio[data-recording-audio]')"


def until(page, expression, seconds=15):
    """Wait until `expression` is true in the page (the app's policy lets no script be evaluated from a string inside the page,
    so the page is asked again and again from here)."""
    end = time.time() + seconds
    while time.time() < end:
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"never true: {expression}")


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
            answer = client.post(f"/api/course-groups/{group}/sections/{number}/roster",
                                 data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
            assert answer.status_code == 201, answer.json
        record = app.extensions["recordings"]

        def tick():
            clock[0] += timedelta(seconds=1)
            return clock[0]

        record.now = tick
        google = record.google = FakeGoogle()
        google.add("MATH 140 - all instructors", TUE, "10:00", "11:15")
        fake.add("rec-meeting", campus(TUE, "10:02"), minutes=71, title="11-10 Meeting", transcript=SAID, outline=OUTLINE, notes=SUMMARY,
                 audio=TONE.read_bytes())
        with record.lock():
            assert record.round()["status"] == "ok"
        recording = client.get("/api/recordings").json["recordings"][0]
        assert recording["kept"] == ["transcript", "outline", "notes"], recording
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_context(reduced_motion="reduce", viewport={"width": 1440, "height": 900}).new_page()
                page.set_default_timeout(15000)
                errors, ranges = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: ranges.append(response.status) if response.url.endswith("/audio") else None)
                page.goto(f"{address}/#recordings")
                section = page.locator("#recordings-section")
                # A kept recording's name opens its own page.
                section.locator(".recording-row a.recording-open", has_text="MATH 140 · Fall 2026").click()
                expect(page).to_have_url(f"{address}/#recordings/{recording['id']}")
                expect(page.locator("#page-title")).to_have_text("MATH 140 · Fall 2026")
                expect(page.locator(".page-heading")).to_contain_text("Tue, Nov 10 · 10:02 AM · 1 h 11 min")
                lines = section.locator(".recording-line")
                expect(lines).to_have_count(3)
                expect(lines.nth(0)).to_contain_text("Chris")
                expect(lines.nth(1)).to_contain_text("Speaker 2")
                expect(lines.nth(2).locator(".recording-line-speaker")).to_have_text("Chris")
                expect(section.locator("[data-recording-cannot]")).to_be_hidden()   # this browser plays Ogg
                assert section.locator(".recording-transcript").bounding_box()["y"] < 450, "the work starts on the first screen"
                assert section.locator('.chip[data-tone="warning"]').count() == 0   # nothing here needs the instructor
                # A line's time plays the archive's file from that moment; the line being said is marked.
                page.evaluate(f"{AUDIO}.dataset.kept = 'same'")
                lines.nth(1).get_by_role("button", name="Play from 0:03").click()
                until(page, f"!{AUDIO}.paused && {AUDIO}.currentTime > 3.4 && {AUDIO}.currentTime < 6")
                expect(lines.nth(1)).to_have_attribute("aria-current", "true")
                assert any(status == 206 for status in ranges), ranges   # asked for in ranges
                until(page, f"{AUDIO}.currentTime > 6.2")
                expect(lines.nth(2)).to_have_attribute("aria-current", "true")   # it follows what is being said
                expect(lines.nth(1)).not_to_have_attribute("aria-current", "true")
                page.evaluate(f"{AUDIO}.pause()")
                # A quiet poll reads nothing again and leaves the player as it is.
                page.evaluate("() => window.dispatchEvent(new Event('focus'))")
                page.wait_for_timeout(500)
                assert page.evaluate(f"{AUDIO}.dataset.kept") == "same"
                # Find marks the lines that hold the words; Enter goes to the next.
                find = section.locator("[data-recording-find]")
                find.fill("seating CHARTS")
                expect(section.locator("[data-recording-found]")).to_have_text("1 line found. Enter goes to the next.")
                expect(lines.nth(1).locator("mark")).to_have_count(2)
                find.press("Enter")
                expect(lines.nth(1)).to_have_attribute("aria-current", "true")
                find.fill("nothing like this")
                expect(section.locator("[data-recording-found]")).to_have_text("No line holds every one of those words.")
                find.fill("")
                # A speaker is named here; the transcript says so and the player is not started over.
                position = page.evaluate(f"{AUDIO}.currentTime")
                section.locator('[data-speaker="Speaker 2"]').click()
                form = page.locator("#dialog[open] #recording-name-form")
                expect(form).to_contain_text("Plaud calls them “Speaker 2”. The name is kept in Faculty and never sent to Plaud.")
                form.locator('input[name="name"]').fill("Avery Quill")
                form.get_by_role("button", name="Save name").click()
                expect(page.locator("#dialog")).not_to_be_visible()
                expect(lines.nth(1).locator(".recording-line-speaker")).to_have_text("Avery Quill")
                expect(section.locator('[data-speaker="Speaker 2"]')).to_have_text("Avery Quill")
                assert page.evaluate(f"{AUDIO}.dataset.kept") == "same" and page.evaluate(f"{AUDIO}.currentTime") == position
                assert {command for command, _ in fake.asked()} <= {"list", "show", "audio", "status", "check-now"}   # nothing was sent to Plaud or the intake but the five reads
                # Plaud's own summary and outline: folded, said to be Plaud's, text and never markup; its topics play too.
                plaud = section.locator("details.recording-plaud")
                assert plaud.get_attribute("open") is None
                expect(plaud.locator("summary")).to_contain_text("Plaud's words, not Faculty's")
                if shots:
                    page.screenshot(path=os.path.join(shots, "recording-page-1440.png"), full_page=True)
                plaud.locator("summary").click()
                expect(plaud.locator(".recording-note")).to_contain_text("## Exam 3")
                plaud.get_by_role("button", name="Play from 0:06").click()
                until(page, f"!{AUDIO}.paused && {AUDIO}.currentTime > 6.1")
                # Leaving the page stops the audio.
                page.goto(f"{address}/#recordings")
                expect(section.locator("[data-recordings-search]")).to_be_visible()
                assert page.evaluate("document.querySelector('audio[data-recording-audio]') === null")
                # What was said is found by its words; a result opens the recording at that moment, ready to play.
                section.locator("[data-recordings-search]").fill("makeup room")
                hit = section.locator(".recording-hit")
                expect(hit).to_have_count(1)
                expect(hit).to_contain_text("MATH 140 · Fall 2026")
                expect(hit).to_contain_text("Tue, Nov 10 · at 0:06")
                expect(hit.locator("mark")).to_have_count(2)
                if shots:
                    page.screenshot(path=os.path.join(shots, "recordings-search-1440.png"))
                hit.locator("a.recording-open").click()
                expect(page).to_have_url(f"{address}/#recordings/{recording['id']}?t=6100")
                expect(lines.nth(2)).to_have_attribute("aria-current", "true")
                until(page, f"{AUDIO} && Math.abs({AUDIO}.currentTime - 6.1) < 0.05 && {AUDIO}.paused")
                expect(lines.nth(1).locator(".recording-line-speaker")).to_have_text("Avery Quill")   # the name was kept
                # Another page of the app: what was playing stops.
                lines.nth(0).get_by_role("button", name="Play from 0:00").click()
                until(page, f"!{AUDIO}.paused")
                page.evaluate("() => { window.heard = document.querySelector('audio[data-recording-audio]'); location.hash = '#overview'; }")
                until(page, "window.heard.paused")
                expect(page.locator("#page-title")).not_to_have_text("MATH 140 · Fall 2026")
                # A recording that is not there says so and offers the list.
                page.goto(f"{address}/#recordings/none")
                expect(section.locator(".callout")).to_contain_text("This recording no longer exists.")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
    print("Recording page checks passed.")


if __name__ == "__main__":
    main()
