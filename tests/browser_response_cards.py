"""Exercise the real private Response Cards builder, issuance, and dashboard.

Run: .venv/bin/python tests/browser_response_cards.py
Only the remote transport is replaced; private routes, catalog, roster ownership,
rendering, same-origin checks, and synchronization use the real application.
"""
import io
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from response_cards.remote import RemoteConfig
from response_cards.mail import MailConfig
from test_rosters import csv_bytes

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}


class RemoteFixture:
    config = RemoteConfig("https://respond.example.test", "test-fixture-credential-" * 3)

    def __init__(self):
        self.cards = {}
        self.events = []

    def event(self, card_id, status, submission=None):
        data = {"status": status}
        if submission:
            data["submission"] = submission
        self.events.append({"cursor": len(self.events) + 1, "card_id": card_id, "data": data})

    def call(self, method, path, data=None):
        if method == "PUT":
            key = path.rsplit("/", 1)[-1]
            self.cards[key] = data
            return {"id": key, "published": True}
        if method == "GET":
            after = int(parse_qs(urlsplit(path).query)["after"][0])
            return {"events": self.events[after:], "cursor": len(self.events), "has_more": False}
        key = path.split("/")[-2]
        self.event(key, "revoked" if data["action"] == "revoke" else "submitted")
        return {"id": key, "applied": True, "action": data["action"]}


class GmailFixture:
    """Exercise connection controls without Google credentials or requests."""
    configured = True
    connected = False
    email = None

    def __init__(self):
        self.started = []
        self.disconnected = 0
        self.sent = []

    def status(self):
        return {"configured": self.configured, "connected": self.connected,
                "email": self.email, "connected_at": "2026-09-16T12:00:00Z" if self.connected else None}

    def start(self, login_hint=""):
        self.started.append(login_hint)
        return "/#response-cards"

    def disconnect(self):
        self.disconnected += 1
        self.connected, self.email = False, None
        return self.status()

    def send(self, payload, expected_email=None):
        assert self.connected
        assert expected_email is None or expected_email.casefold() == self.email.casefold()
        self.sent.append(payload)
        return {"mail_id": payload["mail_id"], "status": "accepted", "message_id": "fake-gmail-receipt"}


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        mailer = app.extensions["response_cards"].mail
        mailer.config = MailConfig("https://relay.example.test/message", "test-secret-" * 4, provider="resend")
        gmail = mailer.gmail = GmailFixture()
        mail_calls = []

        def fake_mail(payload):
            mail_calls.append(payload)
            return {"mail_id": payload["mail_id"], "status": "accepted", "message_id": "fake-relay-receipt"}

        mailer.relay = fake_mail
        client = app.test_client()
        result = client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                    "sections": [507, 508], "multiple_sections": True}, headers=HEADERS)
        assert result.status_code == 201, result.json
        group = client.get("/api/state").json["course_groups"][0]
        for number in (507, 508):
            result = client.post(f"/api/course-groups/{group['id']}/sections/{number}/roster",
                                 data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS)
            assert result.status_code == 201, result.json
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text)
                        if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                page.goto(address + "/#response-cards")
                expect(page.locator("#page-title")).to_have_text("Response Cards")
                expect(page.get_by_role("link", name="Response Cards", exact=True)).to_have_attribute("aria-current", "page")
                # Until the service is set up only its warning says sync waits for it; no second, disabled sync line.
                expect(page.get_by_role("button", name="Sync now")).to_have_count(0)
                expect(page.locator(".rc-sync-line")).to_have_count(0)
                expect(page.locator(".rc-notice")).to_contain_text("not configured")
                expect(page.locator(".rc-intro")).to_have_count(0)
                expect(page.locator("#primary-action")).to_have_text("New template")
                expect(page.locator(".rc-delivery")).to_contain_text("through Resend")
                expect(page.locator(".rc-delivery")).not_to_contain_text("200 messages")
                expect(page.locator("#rc-mail-test")).to_have_count(1)
                # Email delivery is one status line; its settings open from Workspace settings' #response-cards?email link.
                expect(page.locator(".rc-delivery-line")).to_contain_text("Sent through Resend")
                expect(page.locator("#rc-email-settings")).not_to_have_attribute("open", "")
                page.goto(address + "/#response-cards?email")
                expect(page.locator("#rc-email-settings")).to_have_attribute("open", "")
                page.wait_for_function("!location.hash.includes('?')")
                expect(page.get_by_role("button", name="Connect Gmail", exact=True)).to_be_visible()
                expect(page.locator(".rc-delivery")).to_contain_text("Resend remains the default")
                assert gmail.started == [] and not mail_calls
                with page.expect_response(lambda response: response.url.endswith("/gmail/connect")) as connected:
                    page.get_by_role("button", name="Connect Gmail", exact=True).click()
                assert connected.value.status == 200
                assert gmail.started == [mailer.config.reply_to]
                assert mailer.provider == mailer.config.provider == "resend"
                # The OAuth result is a fake account state; no Google calls occur.
                gmail.connected, gmail.email = True, "instructor@example.test"
                page.reload()
                expect(page.locator(".rc-delivery")).to_contain_text("Gmail is connected as instructor@example.test")
                expect(page.locator(".rc-delivery")).to_contain_text("Resend remains the default")
                expect(page.locator("#rc-mail-test")).to_have_count(1)
                expect(page.get_by_role("button", name="Connect Gmail", exact=True)).to_have_count(0)
                # A fold opened by the one-shot ?email link is not remembered (ui.js rememberDisclosures keeps only the
                # instructor's own clicks, 2026-10-02), so after the reload the settings are opened by hand.
                settings = page.locator("#rc-email-settings")
                if settings.get_attribute("open") is None:
                    settings.locator(":scope > summary").click()
                expect(page.get_by_role("button", name="Disconnect Gmail", exact=True)).to_be_visible()
                assert mailer.provider == mailer.config.provider == "resend" and not mail_calls
                page.get_by_role("button", name="Disconnect Gmail", exact=True).click()
                expect(page.locator("#toast")).to_have_text("Gmail disconnected.")
                expect(page.get_by_role("button", name="Connect Gmail", exact=True)).to_be_visible()
                assert gmail.disconnected == 1
                assert mailer.provider == mailer.config.provider == "resend" and not mail_calls
                gmail.connected, gmail.email = True, "instructor@example.test"
                page.reload()
                # Each send starts with the workspace default; choosing Gmail applies only to that action.
                page.get_by_role("button", name="Send a test email", exact=True).click()
                dialog = page.get_by_role("dialog")
                expect(dialog.get_by_label("Send from", exact=True)).to_have_value("default")
                expect(dialog).to_contain_text(mailer.config.reply_to)
                dialog.get_by_role("button", name="Send test email", exact=True).click()
                expect(dialog).not_to_be_visible()
                assert len(mail_calls) == 1 and not gmail.sent
                assert mail_calls[0]["recipient"] == mailer.config.reply_to
                page.get_by_role("button", name="Send a test email", exact=True).click()
                dialog.get_by_label("Send from", exact=True).select_option("gmail")
                expect(dialog.locator("#rc-mail-test-recipient")).to_have_text("To: instructor@example.test")
                dialog.get_by_role("button", name="Send test email", exact=True).click()
                expect(dialog).not_to_be_visible()
                assert len(mail_calls) == 1 and len(gmail.sent) == 1
                assert gmail.sent[0]["recipient"] == "instructor@example.test"
                assert mailer.provider == mailer.config.provider == "resend"
                # The narrowest desktop window (desktop only since 2026-10-03): the dialog reopens on the default sender and
                # the page never scrolls sideways.
                page.set_viewport_size({"width": 1280, "height": 900})
                page.get_by_role("button", name="Send a test email", exact=True).click()
                expect(dialog.get_by_label("Send from", exact=True)).to_have_value("default")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                assert len(mail_calls) == 1 and len(gmail.sent) == 1
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.locator("#primary-action").click()
                expect(page.locator("#page-title")).to_have_text("New template")
                page.get_by_label("Template name", exact=True).fill("Exam check-in")
                page.get_by_label("Card title", exact=True).fill("Hello {{student.first_name}}")
                page.get_by_label("Introduction", exact=True).fill("Please review the instructions.")
                page.get_by_label("Confirmation message", exact=True).fill("Thank you {{student.first_name}}.")

                def add(kind, label):
                    page.get_by_label("New field type", exact=True).select_option(kind)
                    page.get_by_role("button", name="Add field", exact=True).click()
                    field = page.locator(".rc-field-editor").last
                    field.get_by_label("Information text" if kind == "static" else "Question or statement", exact=True).fill(label)
                    return field

                add("static", "Your section is {{student.section}}.")
                attendance = add("single_select", "Will you attend?")
                attendance.get_by_label("Choices, one per line", exact=True).fill("Yes\nNo")
                attendance.get_by_label("Required response", exact=True).check()
                reason = add("long_text", "Explain your absence")
                reason.get_by_text("Visibility and verification", exact=True).click()
                reason.get_by_label("Show this field", exact=True).select_option(index=1)
                reason.get_by_label("When that answer equals", exact=True).fill("No")
                expect(page.locator("#rc-preview").get_by_label("Explain your absence", exact=False)).not_to_be_visible()
                page.locator("#rc-preview").get_by_label("Will you attend?", exact=False).select_option("No")
                expect(page.locator("#rc-preview").get_by_label("Explain your absence", exact=False)).to_be_visible()
                page.locator("#rc-preview").get_by_label("Will you attend?", exact=False).select_option("Yes")
                expect(page.locator("#rc-preview").get_by_label("Explain your absence", exact=False)).not_to_be_visible()
                # A dependency cannot be moved before the field that controls it.
                reason.get_by_role("button", name="Move field 3 up", exact=True).click()
                expect(page.locator("#toast")).to_contain_text("before its question")
                confirm = add("confirmation", "Enter your section")
                confirm.get_by_text("Visibility and verification", exact=True).click()
                confirm.get_by_label("Expected answer, optional", exact=True).fill("{{student.section}}")
                confirm.get_by_label("Verification", exact=True).select_option("exact")
                expect(confirm.get_by_label("On mismatch", exact=True)).to_have_value("ALLOW_AND_FLAG")
                add("acknowledgment", "I have read the instructions.")
                # Controls reorder independent fields without losing their values.
                page.get_by_role("button", name="Move field 5 up", exact=True).click()
                expect(page.locator(".rc-field-editor").nth(3).get_by_label("Question or statement", exact=True)).to_have_value("I have read the instructions.")
                page.get_by_role("button", name="Save draft", exact=True).click()
                expect(page.locator("#rc-save-status")).to_have_text("Saved draft")
                template = client.get("/api/response-cards/templates").json["templates"][0]
                assert len(template["schema"]["fields"]) == 5
                assert template["schema"]["confirmation_message"] == "Thank you {{student.first_name}}."
                # One state-driven primary in the sticky footer: Save draft → Freeze as version 1 → Issue version 1….
                expect(page.locator("#rc-primary")).to_have_text("Freeze as version 1")
                page.get_by_role("button", name="Freeze as version 1", exact=True).click()
                expect(page.get_by_role("button", name="Issue version 1", exact=True)).to_be_visible()
                expect(page.locator("#rc-primary")).to_have_text("Issue version 1…")
                first_version = client.get(f"/api/response-cards/templates/{template['id']}").json["versions"][0]
                # Saved draft edits leave the frozen version unchanged.
                page.get_by_label("Card title", exact=True).fill("Changed draft title")
                expect(page.locator("#rc-primary")).to_have_text("Save draft")
                expect(page.locator("#rc-save-status")).to_have_text("Unsaved changes")
                page.get_by_role("button", name="Save draft", exact=True).click()
                expect(page.locator("#rc-save-status")).to_have_text("Saved draft")
                saved = client.get(f"/api/response-cards/templates/{template['id']}").json
                assert saved["versions"][0]["schema"]["title"] == "Hello {{student.first_name}}"
                expect(page.locator("#rc-primary")).to_have_text("Freeze as version 2")
                page.get_by_role("button", name="Issue version 1", exact=True).click()
                dialog = page.get_by_role("dialog")
                expect(dialog.get_by_label("Section 507", exact=False)).to_be_checked()
                dialog.get_by_label("Section 508", exact=False).uncheck()
                dialog.get_by_label("Audience", exact=True).select_option("students")
                dialog.get_by_label("Example, Alice · 507", exact=True).check()
                expect(dialog.get_by_label("Example, Alice · 508", exact=True)).not_to_be_visible()
                dialog.get_by_role("button", name="Create issue", exact=True).click()
                expect(dialog).not_to_be_visible()
                # The header names the issue; student variables stay literal (each card has its own).
                expect(page.locator("#page-title")).to_have_text("Hello {{student.first_name}}")
                expect(page.locator(".rc-table tbody tr")).to_have_count(1)
                expect(page.locator(".rc-table tbody")).to_contain_text("Example, Alice")
                expect(page.locator(".rc-table tbody")).not_to_contain_text("Example, Bob")
                # Without the response service Publish is a disabled secondary that says why, not a greyed-out primary.
                publish = page.locator(".page-heading-secondary").get_by_role("button", name="Publish 1 card", exact=True)
                expect(publish).to_be_disabled()
                expect(publish).to_have_attribute("title", "The public response service is not configured.")
                expect(page.locator("#primary-action")).to_be_hidden()
                expect(page.locator(".rc-table tbody")).to_contain_text("Not sent")
                issue = client.get("/api/response-cards/issues").json["issues"][0]
                assert issue["version_id"] == first_version["id"]
                assert issue["counts"]["total"] == 1
                assert not page.context.cookies(), "Private Faculty should not require an application login cookie"

                remote = RemoteFixture()
                app.extensions["response_cards"].remote = remote
                page.reload()
                expect(page.locator("#primary-action")).to_be_enabled()
                page.locator("#primary-action").click()
                expect(page.locator(".rc-table tbody")).to_contain_text("Published")
                expect(page.get_by_role("button", name="Copy link", exact=True)).to_be_visible()
                card_id = next(iter(remote.cards))
                expect(page.locator("#primary-action")).to_have_text("Email 1 student")
                page.locator("#primary-action").click()
                expect(dialog.get_by_role("heading", name="Email links to students")).to_be_visible()
                expect(dialog.get_by_label("Send from", exact=True)).to_have_value("default")
                dialog.get_by_label("Send from", exact=True).select_option("gmail")
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                assert len(mail_calls) == 1 and len(gmail.sent) == 1
                page.locator("#primary-action").click()
                expect(dialog.get_by_label("Send from", exact=True)).to_have_value("default")
                dialog.get_by_label("Send from", exact=True).select_option("gmail")
                with page.expect_response(lambda response: response.url.endswith(f"/issues/{issue['id']}/send")) as sent:
                    dialog.get_by_role("button", name="Send emails", exact=True).click()
                assert sent.value.request.post_data_json == {"mail_provider": "gmail"}
                expect(dialog).not_to_be_visible()
                expect(page.locator(".rc-table tbody")).to_contain_text("Queued")
                # Every card is emailed: no primary is left, only the done line.
                expect(page.locator("#primary-action")).to_be_hidden()
                expect(page.locator(".rc-done")).to_contain_text("1 card published · 1 emailed")
                assert len(mail_calls) == 1 and len(gmail.sent) == 1
                assert mailer.process_one()
                assert len(mail_calls) == 1 and len(gmail.sent) == 2
                assert mailer.provider == mailer.config.provider == "resend"
                page.reload()
                expect(page.locator(".rc-table tbody")).to_contain_text("Sent")
                expect(page.get_by_role("button", name="Copy emails (1 awaiting)", exact=True)).to_be_enabled()
                # The select keeps each stored status on its own (here: the card not opened yet), in the address too.
                page.get_by_label("Response status", exact=True).select_option("issued")
                expect(page.locator("#rc-recipient-count")).to_have_text("1 of 1 recipients")
                page.wait_for_function("location.hash.endsWith('?status=issued')")
                page.get_by_label("Response status", exact=True).select_option("")
                # Per-card Resend (the server's resend_block decides); each dialog starts with the workspace default.
                page.get_by_role("button", name="Resend email", exact=True).click()
                expect(dialog.get_by_label("Send from", exact=True)).to_have_value("default")
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                assert len(mail_calls) == 1 and len(gmail.sent) == 2
                fields = template["schema"]["fields"]
                remote.event(card_id, "needs_review", {"id": "submission-browser-1", "request_id": "browser-submit-1", "status": "needs_review",
                    "submitted_at": "2026-09-14T18:00:00Z", "answers": {fields[1]["id"]: "Yes", fields[-1]["id"]: "508", fields[3]["id"]: True},
                    "verification": {fields[-1]["id"]: {"matched": False}}})
                page.get_by_role("button", name="Sync now", exact=True).click()
                expect(page.locator(".rc-table tbody")).to_contain_text("Needs review")
                expect(page.locator(".rc-table tbody")).to_contain_text("Sent")
                # The flagged row names the answer that differed; the history marks it without showing the expected value.
                expect(page.locator(".rc-differs")).to_have_text("Differs: Enter your section")
                expect(page.locator(".rc-sync-line")).to_contain_text("last synced")
                page.get_by_role("button", name="View responses", exact=True).click()
                expect(page.get_by_role("dialog")).to_contain_text("508")
                expect(page.get_by_role("dialog")).to_contain_text("Will you attend?")
                expect(page.get_by_role("dialog").locator('.rc-mark[data-matched="false"]')).to_have_count(1)
                expect(page.get_by_role("dialog").locator("pre")).to_have_count(0)
                page.get_by_role("dialog").get_by_role("button", name="Close", exact=True).click()
                # Filter chips with counts; the select stays for keyboard users.
                expect(page.locator('[data-filter-group="rc-status"] [data-filter="needs_review"]')).to_have_text("Flagged 1")
                expect(page.locator('[data-filter-group="rc-status"] [data-filter="awaiting"]')).to_have_count(0)
                page.locator('[data-filter-group="rc-status"] [data-filter="needs_review"]').click()
                expect(page.locator("#rc-recipient-count")).to_have_text("1 of 1 recipients")
                expect(page.get_by_label("Response status", exact=True)).to_have_value("needs_review")
                page.wait_for_function("location.hash.endsWith('?status=needs_review')")
                page.locator('[data-filter-group="rc-status"] [data-filter=""]').click()
                # Answers open in the app's file viewer as a table built in the page (VIEWER); its Download saves the page's CSV: no UIN
                # confirmation, verification or expected values, cells escaped.
                page.get_by_role("button", name="Answers (CSV)", exact=True).click()
                viewer = page.locator("dialog.file-viewer")
                expect(viewer.locator(".file-viewer-table thead")).to_contain_text("Answers differing from the card")
                expect(viewer.locator(".file-viewer-table tbody")).to_contain_text("Example, Alice")
                with page.expect_download() as download:
                    viewer.get_by_role("link", name="Download").click()
                viewer.get_by_role("button", name="Close the viewer").click()
                expect(viewer).to_be_hidden()
                answers = Path(download.value.path()).read_text(encoding="utf-8")
                assert answers.splitlines()[0].startswith("Student,Section,Email,Response status,Submitted at,Answers differing from the card,"), answers
                assert "Will you attend?" in answers and "Example, Alice" in answers and "Enter your section" in answers, answers
                assert "matched" not in answers and "{{" not in answers and "confirm_uin" not in answers, answers
                # The list's Needs attention strip covers every current term and opens the flagged rows.
                page.goto(address + "/#response-cards?attention")
                expect(page.locator("#rc-attention")).to_contain_text("1 flagged answer")
                expect(page.locator("#rc-attention-title")).to_be_focused()
                expect(page.locator(".rc-issue")).to_contain_text("1 of 1 responded")
                page.locator("#rc-attention").get_by_role("link", name="Hello {{student.first_name}}").click()
                expect(page.locator('[data-filter-group="rc-status"] [data-filter="needs_review"]')).to_have_attribute("aria-pressed", "true")
                page.locator('[data-filter-group="rc-status"] [data-filter=""]').click()
                page.wait_for_function("!location.hash.includes('?')")
                page.get_by_label("Find a recipient", exact=True).fill("Bob")
                expect(page.locator("#rc-recipient-count")).to_have_text("0 of 1 recipients")
                page.get_by_label("Find a recipient", exact=True).fill("")
                page.get_by_role("button", name="Revoke", exact=True).click()
                page.get_by_role("dialog").get_by_role("button", name="Revoke card", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator(".rc-table tbody")).to_contain_text("Revoked")
                expect(page.get_by_role("button", name="Revoke", exact=True)).to_be_disabled()
                expect(page.locator(".rc-issue-status")).to_contain_text("1 of 1 responded")
                page.screenshot(path=str(output / "response-cards-dashboard.png"), full_page=True)
                page.set_viewport_size({"width": 1280, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.get_by_role("link", name="All response cards", exact=True).click()
                page.get_by_role("link", name="Exam check-in", exact=False).click()
                expect(page.get_by_label("Template name", exact=True)).to_have_value("Exam check-in")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator('#response-cards-section [style]:not([style=""])').count() == 0
                page.screenshot(path=str(output / "response-cards-builder-1280.png"), full_page=True)
                # All untrusted text remains literal and no inline styles break the CSP.
                page.get_by_label("Card title", exact=True).fill('<img src=x onerror="alert(1)">')
                expect(page.locator("#rc-preview h3")).to_have_text('<img src=x onerror="alert(1)">')
                assert page.locator("#rc-preview img").count() == 0
                # Multi-choice conditions and checkbox checks retain their answer types.
                page.get_by_label("Card title", exact=True).fill("Changed draft title")
                page.get_by_role("button", name="Save draft", exact=True).click()
                expect(page.locator("#rc-save-status")).to_have_text("Saved draft")
                multi = add("multi_select", "Choose topics")
                multi.get_by_label("Choices, one per line", exact=True).fill("Limits\nDerivatives")
                conditional = add("short_text", "Tell me about both topics")
                conditional.get_by_text("Visibility and verification", exact=True).click()
                conditional.get_by_label("Show this field", exact=True).select_option(index=5)
                conditional.get_by_label("When that answer equals", exact=True).fill("Limits\nDerivatives")
                expect(page.locator("#rc-preview").get_by_label("Tell me about both topics", exact=False)).not_to_be_visible()
                page.locator("#rc-preview").get_by_label("Limits", exact=True).check()
                expect(page.locator("#rc-preview").get_by_label("Tell me about both topics", exact=False)).not_to_be_visible()
                page.locator("#rc-preview").get_by_label("Derivatives", exact=True).check()
                expect(page.locator("#rc-preview").get_by_label("Tell me about both topics", exact=False)).to_be_visible()
                check = add("boolean", "Confirm yes")
                check.get_by_text("Visibility and verification", exact=True).click()
                check.get_by_label("Expected answer, optional", exact=True).fill("true")
                page.get_by_role("button", name="Save draft", exact=True).click()
                expect(page.locator("#rc-save-status")).to_have_text("Saved draft")
                typed = client.get(f"/api/response-cards/templates/{template['id']}").json["schema"]["fields"]
                assert typed[-2]["show_if"]["equals"] == ["Limits", "Derivatives"]
                assert typed[-1]["verification"]["expected"] is True
                assert not errors, errors
                assert len(mail_calls) == 1 and len(gmail.sent) == 2, "Only the explicit fixture email actions should send"
                assert mailer.provider == mailer.config.provider == "resend"
                browser.close()
        finally:
            server.close()
    print("Response Cards browser checks passed: optional Gmail tests and issue sending preserve the Resend default using fake transports; builder, conditions, frozen version, scoped issue, publication, sync, history, revoke, no sideways scroll at 1280, CSP.")


if __name__ == "__main__":
    main()
