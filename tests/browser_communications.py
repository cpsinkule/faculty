"""A student's communications record in a real browser (AGENTS.md "Communications record"): the roster's student window shows the
newest entries of the student's record and opens the timeline; the timeline says how each channel's capture stands, filters by
channel, searches, offers the kept attachment and the original as downloads and Mail's own thread, and fits a laptop's window. The gradebook's
student record opens the same timeline. A file the student linked from Google Drive says why it was not fetched, and the status
offers the one click that lets Faculty read Drive (LINKED DRIVE FILES; Google's sign-in is a stub here). Synthetic mail from a fake
Gmail only."""
import io
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from communications import record as communications_record
from browser_course_pages import seed_course
from test_communications import ACCOUNT, NOTE, ROWS, FakeGmailRaw, mail, millis, wrapped
from test_gmail_mail import FakeTokens, connected_gmail_app
from test_rosters import csv_bytes
from test_storage import HEADERS


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = connected_gmail_app(directory)
        client = app.test_client()
        client.get("/").close()
        seed_course(client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        group = client.get("/api/state").json["course_groups"][0]
        for number, rows in ROWS.items():
            answer = client.post(f"/api/course-groups/{group['id']}/sections/{number}/roster",
                                 data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
            assert answer.status_code == 201, answer.json
        record = app.extensions["communications"]
        gmail = FakeGmailRaw()
        record.reader.http, record.google.http = gmail, FakeTokens()
        # Her second note is a Google Drive link, as the mail filter delivers it; Faculty may not read Drive here.
        linked = f"\n\n 2nd Dr Note.pdf\n<{wrapped('https://drive.google.com/file/d/' + NOTE)}>\n"
        gmail.add("a1", mail("Alice Example <alice@tamu.edu>", ACCOUNT, "Exam 1 question", "Can I take the exam later?" + linked,
                             files=[("note.pdf", b"%PDF-1.4 doctor's note", "application/pdf")]), at=millis(20), thread="c1000000")
        gmail.add("a2", mail(f"Prof <{ACCOUNT}>", "alice@email.tamu.edu", "Re: Exam 1 question", "Yes, see the DR center.\n" * 60),
                  at=millis(21), labels=("SENT",), thread="c1000000")
        # A message to the whole class (here: to more than one student, MASS lowered for the synthetic roster) is folded.
        gmail.add("b9", mail(f"Prof <{ACCOUNT}>", "alice@email.tamu.edu, bob@email.tamu.edu", "Mistake in Example 5", "I chose the wrong pivot."),
                  at=millis(22), labels=("SENT",))
        # A second class-wide message right after it: the two fold together into one line while nothing is filtered.
        gmail.add("b8", mail(f"Prof <{ACCOUNT}>", "alice@email.tamu.edu, bob@email.tamu.edu", "Office hours moved", "Thursday this week."),
                  at=millis(23), labels=("SENT",))
        communications_record.MASS = 1
        with record.lock():
            assert record.round("gmail")["status"] == "ok"
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
                page.goto(f"{address}/#course/{group['id']}/section/507/roster")
                page.get_by_role("button", name="Example, Alice", exact=True).click()
                quick = page.locator("#dialog")
                expect(quick.get_by_role("heading", name="Communications")).to_be_visible()
                summary = quick.locator("[data-comm-summary] .comm-summary li")
                expect(summary).to_have_count(2)  # the class-wide message is left out of the newest three
                expect(summary.first).to_contain_text("You: Re: Exam 1 question")
                expect(quick.get_by_role("button", name="Open the timeline (4)")).to_be_visible()
                # The card leads with who they are; the imported roster fields are folded below the student's notes and messages.
                expect(quick.locator(".student-facts")).to_have_text("UIN 001234567 · alice@email.tamu.edu · Section 507")
                record_fold = quick.locator("details.student-roster-record")
                expect(record_fold).not_to_have_attribute("open", "")
                expect(record_fold.locator(".private-student-id")).not_to_be_visible()
                expect(quick.get_by_role("button", name="Write to Alice…")).to_be_visible()
                quick.get_by_role("button", name="Communications…").click()

                timeline = page.locator("#dialog")
                expect(timeline.locator("#dialog-title")).to_have_text("Communications with Example, Alice")
                expect(timeline.locator(".comm-status li")).to_have_count(4)
                drive = timeline.locator(".comm-status li").nth(3)
                expect(drive).to_contain_text("Google Drive")
                expect(drive).to_contain_text("A file a student links from Google Drive is not fetched")
                expect(drive.get_by_role("button", name="Allow Faculty to read it")).to_be_visible()
                expect(timeline.locator(".comm-status li").first).to_contain_text("4 kept · captured")
                expect(timeline.locator(".comm-status")).to_contain_text("Canvas is not set up for this Faculty instance.")
                # Two class-wide messages in a row: one folded line for both; opened, each is its own folded line saying who it
                # reached, and opening one shows the message.
                run = timeline.locator(".comm-mass-run")
                expect(run).to_have_count(1)
                expect(run.locator("summary").first).to_contain_text("2 class-wide messages")
                mass = timeline.locator(".comm-mass").filter(has_text="Mistake in Example 5")
                expect(mass).not_to_be_visible()
                run.locator("summary").first.click()
                expect(timeline.locator(".comm-mass")).to_have_count(2)
                expect(mass.locator("summary")).to_contain_text("to 2 students")
                expect(mass.locator("pre")).not_to_be_visible()
                mass.locator("summary").click()
                expect(mass.locator("pre")).to_have_text("I chose the wrong pivot.\n")
                mass.locator("summary").click()
                # A filter redraws the list: the run stays open.
                timeline.get_by_role("button", name="All 4").click()
                expect(timeline.locator(".comm-mass-run details").first).to_have_attribute("open", "")
                entries = timeline.locator(".comm-entry:not(.comm-mass)")
                expect(entries).to_have_count(2)
                expect(entries.first.locator(".comm-entry-head strong")).to_have_text("You")
                expect(entries.nth(1).locator(".comm-entry-head strong")).to_have_text("Example, Alice")
                # A long message starts folded, a short one open.
                expect(entries.first.locator("details.comm-text")).not_to_have_attribute("open", "")
                expect(entries.nth(1).locator("details.comm-text")).to_have_attribute("open", "")
                # FRONTEND quiet refresh keeps a message the instructor opened when only the status/filter is redrawn.
                entries.first.locator("details.comm-text summary").click()
                timeline.get_by_role("button", name="All 4").click()
                expect(entries.first.locator("details.comm-text")).to_have_attribute("open", "")
                # Filters and search.
                timeline.get_by_role("button", name="Canvas 0").click()
                expect(timeline.get_by_role("heading", name="No entries match")).to_be_visible()
                timeline.get_by_role("button", name="All 4").click()
                timeline.get_by_label("Search").fill("take exam later")
                expect(timeline.locator(".comm-entry")).to_have_count(1)
                expect(timeline.get_by_label("Search")).to_be_focused()
                # Editing in the middle must not move the caret to the end on each search update.
                timeline.get_by_label("Search").press("Home")
                timeline.get_by_label("Search").press("ArrowRight")
                timeline.get_by_label("Search").press("x")
                assert timeline.get_by_label("Search").evaluate("input => input.selectionStart") == 2
                # A search lists every match on its own: no run.
                timeline.get_by_label("Search").fill("pivot")
                expect(timeline.locator(".comm-mass-run")).to_have_count(0)
                expect(timeline.locator(".comm-mass")).to_have_count(1)
                timeline.get_by_label("Search").fill("")
                # The kept attachment and the original are downloads at their routes; the Gmail thread opens in Mail.
                alice = entries.nth(1)
                note = alice.get_by_role("link", name="note.pdf")
                answer = page.request.get(address + note.get_attribute("href"))
                assert answer.status == 200 and answer.body() == b"%PDF-1.4 doctor's note", answer.status
                assert answer.headers["content-disposition"].startswith("attachment;"), answer.headers
                original = page.request.get(address + alice.get_by_role("link", name="Original message").get_attribute("href"))
                assert original.body() == gmail.messages["a1"]["raw"]
                expect(alice.get_by_role("link", name="Open in Mail")).to_have_attribute("href", "#mail?thread=c1000000")
                # The file she linked from Google Drive is listed with her attachments: not fetched, and why (no link to open).
                expect(alice.locator("li").filter(has_text="2nd Dr Note.pdf")).to_contain_text("not fetched from Google Drive: Faculty may not read your Google Drive yet")
                expect(alice.get_by_role("link", name="2nd Dr Note.pdf")).to_have_count(0)
                # Clicked, the original opens in the app's file viewer as an email (VIEWER), with Download; nothing is downloaded.
                downloads = []
                page.on("download", lambda item: downloads.append(item))
                alice.get_by_role("link", name="Original message").click()
                viewer = page.locator("dialog.file-viewer")
                expect(viewer.locator("#file-viewer-title")).to_have_text("Original message")
                expect(viewer.locator(".file-viewer-head p")).to_contain_text("Email")
                expect(viewer.locator(".file-viewer-headers")).to_contain_text("Exam 1 question")
                expect(viewer.locator(".file-viewer-headers")).to_contain_text("alice@tamu.edu")
                expect(viewer.locator(".file-viewer-text")).to_contain_text("Can I take the exam later?")
                expect(viewer.get_by_role("link", name="Download")).to_have_attribute("href", alice.get_by_role("link", name="Original message").get_attribute("href"))
                viewer.get_by_role("button", name="Close the viewer").click()
                expect(viewer).to_be_hidden()
                assert downloads == [], downloads
                expect(timeline.get_by_role("button", name="Search Gmail now…")).to_be_visible()
                expect(timeline.get_by_role("button", name="Write to Alice…")).to_be_visible()
                expect(timeline.get_by_role("button", name="Back to Example, Alice")).to_be_visible()
                # Capture now asks the worker; the button says so until it has run.
                timeline.get_by_role("button", name="Capture now").click()
                expect(timeline.get_by_role("button", name="Capturing…")).to_be_disabled()
                expect(timeline.locator(".comm-status li").first).to_contain_text("Capturing now…")
                page.screenshot(path=str(output / "communications-timeline.png"), full_page=True)

                # A laptop's window (desktop only): the timeline fits the width.
                page.set_viewport_size({"width": 1280, "height": 900})
                width = page.evaluate("document.querySelector('#dialog').scrollWidth <= document.querySelector('#dialog').clientWidth + 1")
                assert width, "the timeline scrolls sideways at 1280 px"
                page.screenshot(path=str(output / "communications-1280.png"))
                page.set_viewport_size({"width": 1440, "height": 1000})
                # Back to the card it was opened from; Write to Alice… there opens Mail's compose window to the roster address.
                timeline.get_by_role("button", name="Back to Example, Alice").click()
                expect(quick.locator("#dialog-title")).to_have_text("Example, Alice")
                expect(quick.get_by_role("heading", name="Communications")).to_be_visible()
                # The card came back in place: the keyboard is on its Close, not on the page behind the window.
                expect(quick.locator('.dialog-footer [data-action="close"]')).to_be_focused()
                quick.get_by_role("button", name="Write to Alice…").click()
                compose = page.locator("#dialog form.mail-compose")
                expect(compose).to_be_visible()
                expect(compose.locator("[name=to]")).to_have_value("alice@email.tamu.edu")
                expect(compose.locator(".dialog-header")).to_contain_text("To Example, Alice")
                compose.get_by_role("button", name="Cancel", exact=True).click()
                expect(timeline).not_to_be_visible()
                assert not errors, errors
                # Allowing Drive is one click: Google's sign-in (a stub here) is asked for read-only Drive, for the account connected.
                page.route("https://accounts.google.com/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<title>Google</title>"))
                page.get_by_role("button", name="Example, Alice", exact=True).click()
                quick.get_by_role("button", name="Communications…").click()
                timeline.get_by_role("button", name="Allow Faculty to read it").click()
                page.wait_for_url("https://accounts.google.com/**")
                asked = urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query)
                assert "https://www.googleapis.com/auth/drive.readonly" in asked["scope"][0].split(), asked
                assert asked["login_hint"] == [ACCOUNT] and asked["include_granted_scopes"] == ["true"], asked
                browser.close()
        finally:
            server.close()
    print("communications timeline ok")


if __name__ == "__main__":
    main()
