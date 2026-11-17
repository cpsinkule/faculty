"""A student's name on a message in Mail opens their gradebook record in a real browser (requirement, 2026-09-30): the card a name
opens in the section's gradebook, for a message from the student's roster address and for a Canvas message sent on their behalf.
Synthetic mail from a fake Gmail only."""
import io
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_course_pages import seed_course
from test_communications import ROWS
from test_gmail_mail import ACCOUNT, FakeGmail, FakeTokens, connected_gmail_app, message
from test_rosters import csv_bytes
from test_storage import HEADERS


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = connected_gmail_app(directory)
        client = app.test_client()
        client.get("/").close()
        seed_course(client, code="MATH 140", sections=[507], multiple_sections=False)
        group = client.get("/api/state").json["course_groups"][0]
        answer = client.post(f"/api/course-groups/{group['id']}/sections/507/roster",
                             data={"file": (io.BytesIO(csv_bytes(ROWS[507])), "roster.csv")}, headers=HEADERS)
        assert answer.status_code == 201, answer.json
        term_id = next(t["id"] for t in client.get("/api/gradebook").json["terms"] if t["group_id"] == group["id"])
        saved = client.post(f"/api/gradebook/terms/{term_id}/setup", json={"base_revision": None, "request_id": "mail-record-setup",
                            "setup": {"items": [{"title": "Homework 1", "points": "10"}]}}, headers=HEADERS)
        assert saved.status_code == 200, saved.json
        mail = app.extensions["gmail_mail"]
        gmail = FakeGmail()
        gmail.threads = {
            "a1": [message("m1", "Alice Example <alice@tamu.edu>", ACCOUNT, "Exam 1 question", "Can I take the exam later?")],
            "c3": [message("m5", "Bob Example <notifications@instructure.com>", ACCOUNT,
                           "Bob Example (26 FALL MATH 140 507: CALCULUS) just sent you a message in Canvas.", "Subject: Absent\n\nI will miss class.")],
        }
        gmail.threads["a1"][0]["snippet"] = "Yes, I&#39;ll bring &lt;notes&gt; &amp; a pen. &lt;img src=x&gt;"
        mail.client.http, mail.google.http = gmail, FakeTokens()
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda text: errors.append(text.text) if text.type == "error" else None)
                for thread, name in (("a1", "Example, Alice"), ("c3", "Example, Bob")):
                    page.goto(f"{address}/#mail?thread={thread}")
                    if thread == "a1":
                        snippet = page.locator('[data-mail-thread="a1"] .mail-thread-snippet')
                        expect(snippet).to_have_text("Yes, I'll bring <notes> & a pen. <img src=x>")
                        expect(snippet.locator("img, notes")).to_have_count(0)  # MAIL-SUMMARY: still plain text
                    card = page.locator(".mail-reader .mail-student-card")
                    expect(card).to_contain_text("Section 507")
                    button = card.get_by_role("button", name=re.compile(rf"^{re.escape(name)}\s*: open the record$"))
                    expect(button).to_have_attribute("aria-haspopup", "dialog")
                    button.click()
                    record = page.locator("#gradebook-student-form")
                    expect(record).to_be_visible()
                    expect(page.locator("#dialog-title")).to_have_text(name)
                    expect(record).to_contain_text("Section 507")
                    expect(record).to_contain_text("Homework 1")
                    # Its Communications button opens the student's record (these students have no photo).
                    expect(page.locator("#dialog").get_by_role("button", name="Communications…")).to_be_visible()
                    if thread == "a1":
                        page.screenshot(path=str(output / "mail-student-record.png"))
                        # The record's timeline reaches their mail: Search Gmail now… (the student-mail window, which comes back to the
                        # record) and Write to Alice… (Mail's compose window, addressed from the roster).
                        page.locator("#dialog").get_by_role("button", name="Communications…").click()
                        timeline = page.locator("#dialog")
                        expect(timeline.locator("#dialog-title")).to_have_text("Communications with Example, Alice")
                        timeline.get_by_role("button", name="Search Gmail now…").click()
                        expect(page.locator("#dialog-title")).to_have_text("Mail with Example, Alice")
                        expect(page.locator("#dialog .mail-list .mail-thread").first).to_be_visible()
                        expect(page.locator("#dialog")).to_contain_text("alice@email.tamu.edu")
                        page.locator("#dialog").get_by_role("button", name="Back to Example, Alice").click()
                        expect(record).to_be_visible()
                        page.locator("#dialog").get_by_role("button", name="Communications…").click()
                        expect(timeline.get_by_role("button", name="Back to Example, Alice")).to_be_visible()
                        timeline.get_by_role("button", name="Write to Alice…").click()
                        compose = page.locator("#dialog form.mail-compose")
                        expect(compose.locator("[name=to]")).to_have_value("alice@email.tamu.edu")
                        compose.get_by_role("button", name="Cancel", exact=True).click()
                        expect(page.locator("#dialog")).not_to_be_visible()
                        # Cancelled while Gmail is read: an answer that fails afterwards opens nothing (the window stays closed).
                        held, student_mail = [], (lambda url: "/api/mail/student?" in url)
                        page.route(student_mail, lambda route: held.append(route))
                        button.click()
                        page.locator("#dialog").get_by_role("button", name="Communications…").click()
                        timeline.get_by_role("button", name="Search Gmail now…").click()
                        expect(page.locator("#dialog-title")).to_have_text("Mail with Example, Alice")
                        for _ in range(100):
                            if held:
                                break
                            page.wait_for_timeout(50)
                        assert held, "the student's mail was never asked for"
                        page.locator("#dialog").get_by_role("button", name="Cancel", exact=True).click()
                        expect(page.locator("#dialog")).not_to_be_visible()
                        held[0].fulfill(status=200, content_type="text/plain", body="not an answer")
                        page.wait_for_timeout(500)
                        expect(page.locator("#dialog")).not_to_be_visible()
                        page.unroute(student_mail)
                        button.click()
                        expect(record).to_be_visible()
                    page.locator("#dialog").get_by_role("button", name="Close", exact=True).first.click()
                    expect(page.locator("#dialog")).not_to_be_visible()
                    expect(page).to_have_url(f"{address}/#mail?thread={thread}")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("mail student record ok")


if __name__ == "__main__":
    main()
