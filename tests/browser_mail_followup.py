"""MAIL-FOLLOW-UP in a real browser (requirement, 2026-10-10): a student's thread leaves the Mail page's Students list
once its follow-up is Resolved, stays in All mail, and comes back when the student writes again. The real roster, record and
follow-up service over the fake Gmail; nothing reaches Google."""
import io
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_course_pages import seed_course
from communications.record import record
from rosters import connect
from test_gmail_mail import ACCOUNT, FakeGmail, FakeTokens, connected_gmail_app, message
from test_rosters import csv_bytes
from test_storage import HEADERS


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = connected_gmail_app(directory)
        client = app.test_client()
        seed_course(client, sections=[507, 508])
        group = client.get("/api/state").json["course_groups"][0]["id"]
        assert client.post(f"/api/course-groups/{group}/sections/507/roster", data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS).status_code == 201
        mail = app.extensions["gmail_mail"]
        gmail = FakeGmail()
        mail.client.http, mail.google.http = gmail, FakeTokens()
        gmail.threads["f1"] = [message("s1", "Alice Example <alice@example.test>", ACCOUNT, "Missed quiz", "I was sick on Tuesday.")]
        with connect(app.config["DATABASE"]) as db:
            alice = db.execute("SELECT id FROM student_enrollments WHERE uin='001234567'").fetchone()[0]
            term = db.execute("SELECT term_id FROM course_sections LIMIT 1").fetchone()[0]
            record(db, "gmail", "s1", {"sent_at": "2026-10-01T12:00:00Z", "author_role": "student", "author": {"name": "Alice"}, "recipients": [],
                   "subject": "Missed quiz", "body_text": "I was sick on Tuesday.", "attachments": [], "context": {"thread_id": "f1"}},
                   [(alice, term, "author", "roster_address")], "f1")
            db.commit()
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#mail")
                rows = page.locator(".mail-list .mail-thread")
                # A conversation no follow-up tracks is the instructor's to answer: listed.
                expect(rows).to_have_count(1)
                expect(rows.first).to_contain_text("Example, Alice")
                suggestion = client.get("/api/student-support", query_string={"kind": "followup"}).json["items"][0]
                saved = client.post("/api/student-support", json={**suggestion, "state": "resolved", "request_id": "resolved"}, headers=HEADERS)
                assert saved.status_code == 201, saved.json
                # Its follow-up is Resolved: the thread leaves Students, and the page says where such mail goes.
                page.reload()
                expect(page.get_by_text("No mail from students")).to_be_visible()
                expect(page.get_by_text("or their follow-up no longer waits for your reply")).to_be_visible()
                expect(rows).to_have_count(0)
                page.screenshot(path=str(output / "mail-followup-settled.png"))
                # It is still in All mail, and still opens.
                page.locator('[data-mail-tab="all"]').click()
                expect(page.locator('[data-mail-thread="f1"]')).to_contain_text("Missed quiz")
                page.locator('[data-mail-thread="f1"]').click()
                expect(page.locator(".mail-reader-head h2")).to_have_text("Missed quiz")
                # Alice writes again: a message the follow-up has not seen is never hidden.
                gmail.threads["f1"].append(message("s2", "Alice Example <alice@example.test>", ACCOUNT, "Re: Missed quiz", "Can I make it up?", millis="1790000300000"))
                mail.client._summaries.clear()  # the fake reports one history ID; Gmail's changes with a new message
                page.goto(f"{address}/#mail")
                page.reload()
                expect(rows).to_have_count(1)
                expect(rows.first).to_contain_text("Example, Alice")
                page.screenshot(path=str(output / "mail-followup-back.png"))
                assert [call for call in gmail.calls if call[0] != "GET"] == [], "Gmail was changed"
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("mail follow-up ok")


if __name__ == "__main__":
    main()
