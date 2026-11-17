"""The Recordings page in the real shell (RECORDINGS, step 1): `#recordings` after one capture round against the loopback
stand-in of the Plaud intake (tests/fake_intake.py). What waits for Keep or Don't keep comes first and is the page's one orange mark; a
recording filed by the calendar is named by what it is filed under; Keep opens one dialog and the row moves without the page
being redrawn; Check now only asks. ONLY WHAT IS FACULTY'S IS KEPT (2026-11-14): what the calendar shows to be nothing of
Faculty's is not on the page (only how many there are); Don't keep takes a waiting one away at the press and erases a kept one
after asking; Ask me again brings all of them back to be decided; the sidebar and Home count what waits. Synthetic data,
invented people; nothing reaches Plaud.
RECORDINGS_SCREENSHOTS=<folder on /mnt> keeps the pictures taken at 1440 x 900."""
import io
import json
import os
import re
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
from test_recordings import NOW, ROWS, SUMMARY, TALK, TUE, WED, FakeGoogle, campus
from test_rosters import csv_bytes
from test_storage import HEADERS


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

        def tick():   # capture's clock: a second later each time it is read
            clock[0] += timedelta(seconds=1)
            return clock[0]

        record.now = tick
        google = record.google = FakeGoogle()
        google.add("MATH 140 - all instructors", TUE, "10:00", "11:15")
        google.add("MATH 664 lecture", TUE, "12:40", "13:55")
        fake.add("rec-meeting", campus(TUE, "10:02"), minutes=71, title="11-10 Meeting", transcript=TALK, notes=SUMMARY)
        fake.add("rec-664", campus(TUE, "12:41"), minutes=75, title="Lecture 21", transcript=TALK)
        fake.add("rec-evening", campus(WED, "13:30"), minutes=24, title="<i>Quick chat</i>", transcript=TALK)
        fake.add("rec-night", campus(TUE, "21:00"), minutes=9, title="Voice note", transcript=TALK)
        with record.lock():
            assert record.round()["status"] == "ok"
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_context(reduced_motion="reduce", viewport={"width": 1440, "height": 900}).new_page()
                page.set_default_timeout(15000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#recordings")
                section = page.locator("#recordings-section")
                expect(page.locator("#page-title")).to_have_text("Recordings")
                expect(section.locator(".recording-group .recording-row")).to_have_count(3)   # two wait, one is kept
                # What needs you first, and the only orange: the header's chip and the group's count.
                expect(page.locator(".page-heading")).to_contain_text("2 recordings to keep or not")
                groups = section.locator(".recording-group")
                expect(groups.nth(0).locator("h3")).to_contain_text("Keep or not")
                expect(groups.nth(0)).to_contain_text("Faculty could not tell whether these belong to your teaching. Nothing of them is fetched until you say.")
                waiting = groups.nth(0).locator(".recording-row").first
                expect(waiting).to_contain_text("Wed, Nov 11 · 1:30 PM · 24 min")
                expect(waiting).to_contain_text("No calendar entry at that time.")
                expect(waiting).to_contain_text("Plaud calls it “<i>Quick chat</i>”.")   # shown as text, never as markup
                assert section.locator("i").count() == 0
                # ONE framed step where it waits (Keep), and Don't keep beside it as words.
                expect(waiting.locator("button.button")).to_have_text("Keep…")
                expect(waiting.locator("button.text-button")).to_have_text("Don’t keep")
                filed = groups.nth(1).locator(".recording-row")
                expect(filed).to_contain_text("MATH 140 · Fall 2026")
                expect(filed).to_contain_text("Tue, Nov 10 · 10:02 AM · 1 h 11 min")
                expect(filed).to_contain_text("Transcript kept")
                expect(filed).not_to_contain_text("11-10 Meeting")
                expect(filed.locator("button.button")).to_have_count(0)   # a kept row has words only
                assert groups.nth(0).bounding_box()["y"] < 450, "the work starts on the first screen"
                expect(section.locator('.chip[data-tone="warning"]')).to_have_count(1)
                # NOT KEPT: the lecture of another course is not on the page at all. Only how many there are, and the way back.
                gone = section.locator("[data-recordings-not-kept]")
                expect(gone).to_contain_text("1 recording at Plaud is not kept. Faculty holds nothing of it.")
                expect(gone.get_by_role("button", name="Ask me again")).to_be_visible()
                expect(section).not_to_contain_text("Lecture 21")
                expect(section).not_to_contain_text("12:41 PM")
                expect(section.locator("details")).to_have_count(0)
                # The sidebar counts what waits, as Home's card does.
                expect(page.locator("#nav-badge-recordings")).to_have_text(re.compile(r"^2 "))
                if shots:
                    page.screenshot(path=os.path.join(shots, "recordings-1440.png"), full_page=True)
                # A quiet poll that finds the same list redraws nothing: the row is the same element afterwards.
                waiting.evaluate("row => { row.dataset.kept = 'same'; }")
                page.evaluate("() => window.dispatchEvent(new Event('focus'))")
                page.wait_for_timeout(600)
                assert waiting.get_attribute("data-kept") == "same"
                # Keep: one dialog; a term; the row leaves Keep or not.
                waiting.get_by_role("button", name="Keep…", exact=True).click()
                form = page.locator("#dialog[open] #recording-file-form")
                expect(form).to_contain_text("Keep this recording")
                expect(form.locator('[data-file-part="student"]')).to_be_visible()
                assert form.locator('select[name="kind"] option').evaluate_all("options => options.map(option => option.value)") == [
                    "student", "term", "course", "training", "class_day", "memo"]   # not keeping is not a kind of filing
                if shots:
                    page.wait_for_timeout(400)
                    page.screenshot(path=os.path.join(shots, "recordings-keep-1440.png"))
                form.locator('select[name="kind"]').select_option("term")
                expect(form.locator('[data-file-part="term"]')).to_be_visible()
                expect(form.locator('[data-file-part="student"]')).to_be_hidden()
                form.get_by_role("button", name="Keep", exact=True).click()
                expect(page.locator("#dialog")).not_to_be_visible()
                expect(groups.nth(0).locator(".recording-row")).to_have_count(1)
                expect(groups.nth(1).locator(".recording-row")).to_have_count(2)
                expect(page.locator(".page-heading")).to_contain_text("1 recording to keep or not")
                expect(groups.nth(1).locator(".recording-row").first).to_contain_text("Fetched at the next check.")
                # Keeping asked for a round: the page says so; when the worker has run it, the row says what was kept, in place.
                expect(section.locator(".recording-capture")).to_have_text("Checking Plaud now…")
                clock[0] += timedelta(minutes=5)
                record.run_due()
                expect(groups.nth(1).locator(".recording-row").first).to_contain_text("Transcript kept")
                expect(section.locator("[data-recordings-check]")).to_be_enabled()
                # Check now only asks; nothing is fetched by the page.
                before = len(fake.calls())
                section.locator("[data-recordings-check]").click()
                expect(section.locator(".recording-capture")).to_have_text("Checking Plaud now…")
                assert len(fake.calls()) == before
                clock[0] += timedelta(minutes=5)
                record.run_due()
                # Don't keep, of the one that still waits: nothing was fetched, so it goes at the press, without a question.
                groups.nth(0).locator(".recording-row").get_by_role("button", name="Don’t keep").click()
                expect(section.locator(".recording-group")).to_have_count(1)
                expect(page.locator(".page-heading")).not_to_contain_text("to keep or not")
                expect(section.locator('.chip[data-tone="warning"]')).to_have_count(0)
                expect(gone).to_contain_text("2 recordings at Plaud are not kept. Faculty holds nothing of them.")
                expect(section).not_to_contain_text("Voice note")
                assert ("show", "rec-night") not in fake.asked()
                assert ("show", "rec-664") not in fake.asked()
                expect(page.locator("#nav-badge-recordings")).to_be_hidden()   # the page told the shell: the rail is read again at once
                # Don't keep, of one Faculty has kept: it is erased, so it says what goes and what stays, and asks first.
                kept = section.locator(".recording-group .recording-row", has_text="Tue, Nov 10 · 10:02 AM")
                expect(kept.locator("button.text-button.danger")).to_have_text("Don’t keep")
                kept.get_by_role("button", name="Don’t keep").click()
                ask = page.locator("#dialog[open] #recording-discard-form")
                expect(ask).to_contain_text("Don’t keep this recording?")
                expect(ask).to_contain_text("MATH 140 · Fall 2026 · Tue, Nov 10 · 10:02 AM · 1 h 11 min")
                expect(ask).to_contain_text("Faculty erases its transcript and what it read from it. What you approved from it stays, and the recording stays in your Plaud archive and at Plaud.")
                if shots:
                    page.wait_for_timeout(400)
                    page.screenshot(path=os.path.join(shots, "recordings-erase-1440.png"))
                ask.get_by_role("button", name="Cancel").click()
                expect(page.locator("#dialog")).not_to_be_visible()
                expect(kept).to_have_count(1)   # Cancel erases nothing
                kept.get_by_role("button", name="Don’t keep").click()
                page.locator("#dialog[open] #recording-discard-form").get_by_role("button", name="Erase it").click()
                expect(page.locator("#dialog")).not_to_be_visible()
                expect(kept).to_have_count(0)
                expect(section.locator(".recording-group .recording-row")).to_have_count(1)
                expect(gone).to_contain_text("3 recordings at Plaud are not kept.")
                with record.rosters.lock():
                    assert not (Path(app.config["DATABASE"]).parent / "recordings").exists() or \
                        len(list((Path(app.config["DATABASE"]).parent / "recordings").iterdir())) == 1   # only the kept one's files are left
                if shots:
                    page.screenshot(path=os.path.join(shots, "recordings-not-kept-1440.png"), full_page=True)
                # Ask me again: Plaud is listed again, and every one of the three waits for the instructor. The calendar decides
                # nothing for them: not that the lecture is not theirs, and not that the meeting it once filed is.
                fake.clear()
                gone.get_by_role("button", name="Ask me again").click()
                expect(section.locator(".recording-capture")).to_have_text("Checking Plaud now…")
                clock[0] += timedelta(minutes=5)
                record.run_due()
                expect(groups.nth(0).locator("h3")).to_contain_text("Keep or not")
                expect(groups.nth(0).locator(".recording-row")).to_have_count(3)
                expect(groups.nth(0)).to_contain_text("The calendar entry at that time names no course of yours. You asked to be asked about this one again.")
                expect(groups.nth(0).locator(".recording-row", has_text="You asked to be asked about this one again.")).to_have_count(3)
                expect(section.locator("[data-recordings-not-kept]")).to_have_count(0)
                assert fake.asked("show") == [] and fake.asked("audio") == []   # still nothing read from the archive
                # Reached from the rail (TODAY, after Follow-up, since 2026-11-14: the instructor, "Yes, add it"), which marks it
                # as the page shown and counts what waits, and from the palette, where it is one of the Pages and listed once.
                page.goto(f"{address}/#overview")
                entry = page.locator('#workspace-nav [data-view="recordings"]')
                expect(entry).to_contain_text("Recordings")
                expect(page.locator("#nav-badge-recordings")).to_have_text(re.compile(r"^3 "))
                expect(page.locator("#home-attention")).to_contain_text("Recordings to keep or not")
                entry.click()
                expect(page.locator("#page-title")).to_have_text("Recordings")
                expect(entry).to_have_attribute("aria-current", "page")
                page.goto(f"{address}/#overview")
                expect(entry).not_to_have_attribute("aria-current", "page")
                page.keyboard.press("Control+k")
                page.keyboard.type("Recordings")
                page.wait_for_timeout(300)
                expect(page.locator("#palette-list .palette-label").get_by_text("Recordings", exact=True)).to_have_count(1)
                page.keyboard.press("Escape")
                page.keyboard.press("Control+k")
                page.keyboard.type("Recordings")
                page.wait_for_timeout(300)
                page.keyboard.press("Enter")
                expect(page.locator("#page-title")).to_have_text("Recordings")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
    print("Recordings page checks passed.")


if __name__ == "__main__":
    main()
