"""Exercise Gmail spreadsheet export through Faculty's real browser and APIs.

Uses a temporary catalog and fake response/mail providers. The registered print
version is a catalog fixture; this test does not render or qualify printed paper.
Run: .venv/bin/python tests/browser_seating_mail_merge.py
"""
import io
import sys
import threading
from pathlib import Path

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rosters import connect
from response_cards.mail import MailConfig
from test_seating_mail_merge import COLUMNS, FakeGmail, SeatingMailMergeTests


class BrowserFixture(SeatingMailMergeTests):
    def setUp(self):
        super().setUp()
        self.gmail = FakeGmail()
        self.cards.mail.gmail = self.gmail
        self.cards.mail.transport = self.cards.mail._post
        self.cards.mail.relay = lambda payload: self.relay({'id': payload['mail_id'], **payload})
        self.cards.mail.config = MailConfig('https://relay.example.test/message',
                                           'test-secret-' * 4, provider='resend')

    def confirm(self, **extra):
        # Leave the fixture unconfirmed so the browser exercises the new choice.
        return self.chart


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    fixture = BrowserFixture("test_confirm_publishes_without_email_then_workbook_has_complete_invitations")
    fixture.setUp()
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.set_default_timeout(10000)
            errors, confirmations, downloads, resends = [], [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text)
                    if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
            page.on("request", lambda request: confirmations.append(request.post_data_json)
                    if request.method == "POST" and request.url.endswith(fixture.route + "/confirm") else None)
            page.on("request", lambda request: resends.append(request.post_data_json)
                    if request.method == "POST" and request.url.endswith("/resend") else None)
            page.on("download", lambda download: downloads.append(download))
            page.goto(address + "/#seating/" + fixture.chart["id"])
            expect(page.locator(".seating-view")).to_be_visible()
            expect(page.get_by_role("button", name="Gmail mail merge", exact=True)).to_have_count(0)

            page.get_by_role("button", name="Confirm for printing", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_contain_text("Exam PDF version")
            dialog.get_by_role("button", name="Continue to response cards", exact=True).click()
            expect(dialog.get_by_label("Invitation delivery", exact=True)).to_have_value("faculty")
            expect(dialog.locator('[name="card_delivery"] option[value="faculty"]')).to_have_text("Resend (default)")
            expect(dialog.locator('[name="card_delivery"] option[value="gmail_direct"]')).to_have_text("TAMU Gmail — instructor@tamu.edu")
            dialog.get_by_label("Invitation delivery", exact=True).select_option("gmail_direct")
            expect(dialog).to_contain_text("Faculty sends these invitations directly from instructor@tamu.edu")
            expect(dialog).to_contain_text("automatic confirmation emails keep using the default provider")
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            page.get_by_role("button", name="Confirm for printing", exact=True).click()
            dialog.get_by_role("button", name="Continue to response cards", exact=True).click()
            expect(dialog.get_by_label("Invitation delivery", exact=True)).to_have_value("faculty")
            publish = dialog.locator('[name="publish_cards"]')
            expect(publish).to_be_checked()
            expect(dialog.get_by_label("Publish the cards and email each student their link now", exact=False)).to_be_checked()
            publish.uncheck()
            expect(dialog.get_by_label("Invitation delivery", exact=True)).to_be_disabled()
            expect(dialog).to_contain_text("Keep the cards in Faculty only")
            publish.check()
            dialog.get_by_label("Invitation delivery", exact=True).select_option("gmail")
            expect(dialog).to_contain_text("without queuing invitations in Faculty")
            dialog.get_by_label("Exam date", exact=True).fill("2026-10-01")
            dialog.get_by_label("Exam time", exact=True).fill("7:00 PM")
            dialog.get_by_role("button", name="Confirm, export, and prepare cards", exact=True).click()

            merge = page.locator("#seating-mail-merge")
            expect(merge).to_be_visible()
            expect(merge.locator(".seating-merge-counts")).to_contain_text("9 ready to export")
            assert confirmations[-1]["publish_cards"] is True
            assert confirmations[-1]["email_cards"] is False
            assert confirmations[-1]["prepare_cards"] is True
            assert confirmations[-1]["mail_provider"] == "default"
            expect(merge.get_by_label("Mail merge audience", exact=True)).to_have_value("unsent")
            expect(merge.locator(".data-table tbody tr")).to_have_count(9)
            expect(merge.locator(".data-table")).to_contain_text("DR testing")
            expect(merge.locator("[data-merge-subject]")).to_have_text("IMPORTANT: Information about your upcoming Test 1 for MATH 140")
            expect(merge).to_contain_text("@Message")
            expect(merge).to_contain_text("pasting that text alone does not insert a merge field")
            expect(merge).to_contain_text("Exporting does not send email")
            expect(merge).to_contain_text("FirstName contains the generic value Student for everyone")
            expect(merge).to_contain_text("no student names, including for previously emailed cards")
            expect(merge.locator(".data-table")).to_contain_text("Example, Bob")
            assert "/r/" not in merge.inner_html()
            with connect(fixture.path) as db:
                assert db.execute("SELECT COUNT(*) FROM response_card_mail").fetchone()[0] == 0
            assert fixture.relay.sent == []

            # Download real XLSX bytes; every first-tab cell is literal text.
            with page.expect_download() as received:
                merge.get_by_role("button", name="Download .xlsx", exact=True).click()
            book = load_workbook(io.BytesIO(Path(received.value.path()).read_bytes()))
            assert received.value.suggested_filename.endswith(".xlsx")
            assert book.sheetnames[:2] == ["Recipients", "Instructions"]
            rows = list(book["Recipients"].values)
            assert rows[0] == COLUMNS and len(rows) == 10
            assert rows[0] == ("Email", "FirstName", "Course", "Section", "Exam", "ResponseCardLink", "Message")
            recipients = [dict(zip(rows[0], row)) for row in rows[1:]]
            initial_messages = {row["Email"]: row["Message"] for row in recipients}
            assert all(row["FirstName"] == "Student" for row in recipients)
            assert all(row["ResponseCardLink"] in row["Message"] and "/r/" in row["ResponseCardLink"] for row in recipients)
            assert all(row["Message"].startswith("Hello,\n\n") for row in recipients)
            assert all(name not in row["Message"] for row in recipients for name in ("Alice", "Bob", "Carol", "Example,"))
            with connect(fixture.path) as db:
                for card in db.execute("SELECT * FROM response_card_recipients"):
                    assert card["student_name"] not in initial_messages[card["email"]]
                    assert initial_messages[card["email"]] == fixture.cards.mail.invitation(db, card)["text"]
            assert all(cell.data_type == "s" for row in book["Recipients"] for cell in row if cell.value)
            expect(merge.locator("[data-merge-result]")).to_contain_text("delivery records are unchanged")
            with connect(fixture.path) as db:
                assert db.execute("SELECT COUNT(*) FROM response_card_mail").fetchone()[0] == 0
                assert {row[0] for row in db.execute("SELECT delivery_status FROM response_card_recipients")} == {"not_sent"}

            # A changed audience invalidates the exact preview; no stale file saves.
            bob = fixture.recipient()
            with connect(fixture.path) as db:
                db.execute("UPDATE response_card_recipients SET delivery_status='sent' WHERE id=?", (bob["id"],))
            before = len(downloads)
            with page.expect_response(lambda response: "/mail-merge/download?" in response.url) as changed:
                merge.get_by_role("button", name="Download .xlsx", exact=True).click()
            assert changed.value.status == 409
            expect(merge.locator('[role="alert"]')).to_contain_text("Refresh the preview")
            expect(merge.get_by_role("button", name="Download .xlsx", exact=True)).to_be_disabled()
            assert len(downloads) == before
            merge.get_by_role("button", name="Refresh preview", exact=True).click()
            expect(merge.locator(".seating-merge-counts")).to_contain_text("8 ready to export")
            merge.locator(".seating-merge-skipped > summary").click()
            expect(merge.locator(".seating-merge-skipped")).to_contain_text("Already emailed")
            merge.get_by_label("Mail merge audience", exact=True).select_option("pending")
            expect(merge.locator(".seating-merge-counts")).to_contain_text("9 ready to export")
            expect(merge.locator(".data-table")).to_contain_text("Sent")
            # Earlier delivery changes the audience, never the original message.
            with page.expect_download() as received_again:
                merge.get_by_role("button", name="Download .xlsx", exact=True).click()
            pending_book = load_workbook(io.BytesIO(Path(received_again.value.path()).read_bytes()))
            pending_rows = list(pending_book["Recipients"].values)
            pending_recipients = [dict(zip(pending_rows[0], row)) for row in pending_rows[1:]]
            assert {row["Email"]: row["Message"] for row in pending_recipients} == initial_messages
            assert all(row["FirstName"] == "Student" and "second copy" not in row["Message"] for row in pending_recipients)

            with connect(fixture.path) as db:
                db.execute("UPDATE response_card_recipients SET delivery_status='queued' WHERE student_name='Example, Alice'")
                db.execute("UPDATE response_card_recipients SET delivery_status='unconfirmed' WHERE student_name='Example, Carol'")
            merge.get_by_role("button", name="Refresh preview", exact=True).click()
            expect(merge.locator(".seating-merge-counts")).to_contain_text("8 ready to export")
            merge.locator(".seating-merge-skipped > summary").click()
            expect(merge.locator(".seating-merge-skipped")).to_contain_text("already queued")
            expect(merge.locator(".data-table").first).to_contain_text("Delivery unconfirmed")
            page.screenshot(path=str(output / "seating-mail-merge-desktop.png"))
            # The narrowest desktop window on a laptop-height screen (desktop only; there is no phone layout): the dialog
            # fits and its download stays in view.
            page.set_viewport_size({"width": 1280, "height": 800})
            expect(merge).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert merge.evaluate("el => el.scrollWidth <= el.clientWidth")
            expect(merge.get_by_role("button", name="Download .xlsx", exact=True)).to_be_in_viewport()
            page.screenshot(path=str(output / "seating-mail-merge-1280.png"))

            # Reopening starts with the safer audience; empty exports stay disabled.
            merge.get_by_role("button", name="Close", exact=True).click()
            page.get_by_role("button", name="Gmail mail merge", exact=True).click()
            expect(merge.get_by_label("Mail merge audience", exact=True)).to_have_value("unsent")
            expect(merge.locator(".seating-merge-counts")).to_contain_text("6 ready to export")
            with connect(fixture.path) as db:
                db.execute("UPDATE response_card_recipients SET status='verified'")
            merge.get_by_role("button", name="Refresh preview", exact=True).click()
            expect(merge.locator(".seating-merge-counts")).to_contain_text("0 ready to export")
            expect(merge.get_by_role("button", name="Download .xlsx", exact=True)).to_be_disabled()
            assert fixture.relay.sent == []

            # Direct Gmail is an explicit choice on the next confirmation. It
            # queues real catalog jobs but both transports remain local fakes.
            merge.get_by_role("button", name="Close", exact=True).click()
            page.set_viewport_size({"width": 1440, "height": 1100})
            with connect(fixture.path) as db:
                db.execute("UPDATE response_card_recipients SET status='issued',delivery_status='not_sent'")
            page.reload()
            page.get_by_role("button", name="Export again as new print jobs", exact=True).click()
            dialog.get_by_role("button", name="Continue to response cards", exact=True).click()
            expect(dialog.get_by_label("Invitation delivery", exact=True)).to_have_value("faculty")
            dialog.get_by_label("Invitation delivery", exact=True).select_option("gmail_direct")
            dialog.get_by_role("button", name="Confirm, export, and prepare cards", exact=True).click()
            expect(page.locator("#seating-cards")).not_to_be_visible()
            expect(merge).to_have_count(0)
            assert confirmations[-1]["publish_cards"] is True
            assert confirmations[-1]["email_cards"] is True
            assert confirmations[-1]["mail_provider"] == "gmail"
            with connect(fixture.path) as db:
                jobs = [dict(row) for row in db.execute("SELECT * FROM response_card_mail")]
            assert len(jobs) == 9
            assert {job["provider"] for job in jobs} == {"gmail"}
            assert {job["gmail_sender"] for job in jobs} == {"instructor@tamu.edu"}
            while fixture.cards.mail.process_one():
                pass
            assert len(fixture.gmail.sent) == 9
            assert fixture.relay.sent == []
            assert fixture.cards.mail.provider == "resend"

            # Completing one Gmail action never remembers it as a default.
            page.reload()
            page.get_by_role("button", name="Export again as new print jobs", exact=True).click()
            dialog.get_by_role("button", name="Continue to response cards", exact=True).click()
            expect(dialog.get_by_label("Invitation delivery", exact=True)).to_have_value("faculty")
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            for cancel in (True, False):
                page.get_by_role("button", name="Response card details for Example, Bob", exact=True).click()
                dialog.get_by_role("button", name="Resend email", exact=True).click()
                expect(dialog.get_by_label("Send from", exact=True)).to_have_value("default")
                expect(dialog.locator('[name="mail_provider"] option[value="gmail"]')).to_have_text("TAMU Gmail — instructor@tamu.edu")
                dialog.get_by_label("Send from", exact=True).select_option("gmail")
                dialog.get_by_role("button", name="Cancel" if cancel else "Resend email", exact=True).click()
            expect(page.locator("#seating-resend")).not_to_be_visible()
            assert resends[-1] == {"mail_provider": "gmail"}
            with connect(fixture.path) as db:
                pending = [dict(row) for row in db.execute("SELECT * FROM response_card_mail WHERE status='pending'")]
            assert len(pending) == 1 and pending[0]["provider"] == "gmail"
            assert pending[0]["gmail_sender"] == "instructor@tamu.edu"
            assert fixture.cards.mail.provider == "resend"
            assert not errors, errors
            browser.close()
        print("PASS: optional direct Gmail confirmation/resend reset to Resend by default, Gmail mail merge publishes without mail, instructor-only name preview, generic name-free original invitations across audiences, literal XLSX cells, 409 revalidation, audience exclusions, mobile layout; no browser errors or real sends.")
    finally:
        server.close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
