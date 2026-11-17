"""Confirmed seating charts can queue reviewed invitations through optional Gmail.

Uses a temporary catalog and fake Gmail/relay/publication providers. No real mail
is sent, and the registered print version is only a catalog fixture.
Run: .venv/bin/python tests/browser_seating_gmail.py
"""
import json
import sys
import threading
import time
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rosters import connect
from test_seating_mail_merge import SeatingGmailDeliveryTests


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    fixture = SeatingGmailDeliveryTests("test_omitted_provider_keeps_resend_even_with_gmail_connected")
    fixture.setUp()
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    route = fixture.route + "/gmail-send"

    def rows():
        return fixture.mail_rows()

    def is_preview(response):
        return response.request.method == "GET" and response.url.split("?", 1)[0].endswith(route)

    def is_send(response):
        return response.request.method == "POST" and response.url.endswith(route)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.set_default_timeout(10000)
            page.add_init_script("""window.gmailCspViolations = [];
                document.addEventListener('securitypolicyviolation', event =>
                    window.gmailCspViolations.push(event.violatedDirective));""")
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text)
                    if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
            page.on("request", lambda request: requests.append(request.post_data_json)
                    if request.method == "POST" and request.url.endswith(route) else None)
            page.goto(address + "/#seating/" + fixture.chart["id"])
            expect(page.locator(".seating-view")).to_be_visible()
            expect(page.get_by_role("button", name="Send with Gmail", exact=True)).to_have_count(0)

            # Establish independent delivery histories. The one active Resend job
            # is paused, so Gmail can run without delivering it during this test.
            fixture.confirm(email_cards=False)
            with connect(fixture.path) as db:
                cards = {row["student_name"]: dict(row) for row in db.execute("SELECT * FROM response_card_recipients")}
            fixture.cards.mail.queue_resend(cards["Example, Bob"]["id"])
            assert fixture.cards.mail.process_one()
            assert len(fixture.relay.sent) == 1
            fixture.cards.mail.queue_resend(cards["Student, 4"]["id"])
            with connect(fixture.path) as db:
                db.execute("UPDATE response_card_recipients SET status='verified' WHERE id=?", (cards["Student, 3"]["id"],))
                db.execute("UPDATE response_card_mail SET not_before=? WHERE status='pending'", (int(time.time()) + 600,))
            before, publication_count = rows(), len(fixture.cards.remote.calls)
            page.reload()
            button = page.get_by_role("button", name="Send with Gmail", exact=True)
            expect(button).to_be_visible()
            with page.expect_response(is_preview) as opened:
                button.click()
            preview = opened.value.json()
            dialog = page.locator("#seating-gmail-send")
            expect(dialog).to_be_visible()
            expect(dialog.locator("[data-gmail-sender]")).to_contain_text("instructor@tamu.edu")
            expect(dialog.get_by_label("Gmail send audience", exact=True)).to_have_value("unsent")
            expect(dialog.locator(".seating-merge-counts")).to_contain_text("6")
            included = dialog.locator(".data-table").first
            expect(included.locator("tbody tr")).to_have_count(6)
            expect(included).to_contain_text("Example, Alice")
            expect(included).to_contain_text("Example, Carol")
            for name in ("Example, Bob", "Student, 3", "Student, 4"):
                expect(included).not_to_contain_text(name)
            expect(dialog.get_by_role("button", name="Send 6 emails", exact=True)).to_be_enabled()
            assert preview["eligible_count"] == 6 and preview["skipped_count"] == 3
            assert preview["sender"] == "instructor@tamu.edu"
            assert "/r/" not in json.dumps(preview)
            assert "/r/" not in dialog.inner_html()
            assert rows() == before
            assert len(fixture.cards.remote.calls) == publication_count
            assert requests == [] and fixture.gmail.sent == []

            # Reminders include earlier accepted deliveries, while completed and
            # active cards remain excluded from the reviewed Gmail audience.
            with page.expect_response(is_preview) as pending:
                dialog.get_by_label("Gmail send audience", exact=True).select_option("pending")
            preview = pending.value.json()
            expect(included.locator("tbody tr")).to_have_count(7)
            expect(included).to_contain_text("Example, Bob")
            expect(included).not_to_contain_text("Student, 3")
            expect(included).not_to_contain_text("Student, 4")
            assert preview["eligible_count"] == 7 and rows() == before

            # A response arriving after preview prevents a stale batch send.
            with connect(fixture.path) as db:
                db.execute("UPDATE response_card_recipients SET status='verified' WHERE id=?", (cards["Student, 5"]["id"],))
            with page.expect_response(is_send) as changed:
                dialog.get_by_role("button", name="Send 7 emails", exact=True).click()
            assert changed.value.status == 409
            expect(dialog.locator('[role="alert"]')).to_contain_text("Refresh")
            expect(dialog.get_by_role("button", name="Send emails", exact=True)).to_be_disabled()
            assert requests[-1]["audience"] == "pending"
            assert requests[-1]["fingerprint"] == preview["fingerprint"]
            uuid.UUID(requests[-1]["request_id"])
            assert rows() == before and fixture.gmail.sent == []
            with page.expect_response(is_preview) as refreshed:
                dialog.get_by_role("button", name="Refresh preview", exact=True).click()
            preview = refreshed.value.json()
            expect(dialog.get_by_role("button", name="Send 6 emails", exact=True)).to_be_enabled()
            assert preview["eligible_count"] == 6

            # Losing the connection leaves a usable preview but no send action.
            fixture.gmail.connected = False
            with page.expect_response(is_preview) as disconnected:
                dialog.get_by_role("button", name="Refresh preview", exact=True).click()
            assert disconnected.value.json()["can_send"] is False
            expect(dialog.get_by_role("button", name="Send 6 emails", exact=True)).to_be_disabled()
            expect(dialog.locator('a[href="#response-cards?email"]')).to_be_visible()
            # Email settings open in a new tab, so this window and its preview stay for Refresh preview.
            expect(dialog.locator('a[href="#response-cards?email"]')).to_have_attribute("target", "_blank")
            assert rows() == before
            fixture.gmail.connected = True
            with page.expect_response(is_preview) as reconnected:
                dialog.get_by_role("button", name="Refresh preview", exact=True).click()
            preview = reconnected.value.json()
            expect(dialog.get_by_role("button", name="Send 6 emails", exact=True)).to_be_enabled()

            page.screenshot(path=str(output / "seating-gmail-desktop.png"))
            # The narrowest desktop window on a laptop-height screen (desktop only; there is no phone layout): the dialog
            # fits and its send action stays in view.
            page.set_viewport_size({"width": 1280, "height": 800})
            expect(dialog).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth")
            expect(dialog.get_by_role("button", name="Send 6 emails", exact=True)).to_be_in_viewport()
            page.screenshot(path=str(output / "seating-gmail-1280.png"))

            with connect(fixture.path) as db:
                expected = {card["email"]: fixture.cards.mail.invitation(db, card)["text"]
                            for name, card in cards.items() if name not in ("Student, 3", "Student, 4", "Student, 5")}

            # Let the server commit, then lose its receipt at the browser. The
            # instructor must retry that exact request, even after reopening.
            committed_receipts = []

            def lose_receipt(intercepted):
                receipt = intercepted.fetch()
                assert receipt.status in (200, 202)
                committed_receipts.append(receipt.json())
                intercepted.fulfill(status=502, content_type="application/json",
                                    body=json.dumps({"error": "The proxy lost the queue receipt."}))

            page.route(address + route, lose_receipt)
            dialog.get_by_role("button", name="Send 6 emails", exact=True).click()
            expect(dialog.get_by_role("button", name="Retry request", exact=True)).to_be_enabled()
            page.unroute(address + route, lose_receipt)
            uncertain_request = dict(requests[-1])
            queued_once = rows()
            assert len(queued_once) == len(before) + 6
            assert committed_receipts[0]["queued"] == 6
            assert committed_receipts[0]["request_id"] == uncertain_request["request_id"]
            assert fixture.gmail.sent == [] and len(fixture.relay.sent) == 1
            expect(dialog.get_by_role("button", name="Send emails", exact=True)).to_be_disabled()
            expect(dialog.get_by_role("button", name="Refresh preview", exact=True)).to_be_disabled()
            expect(dialog.get_by_label("Gmail send audience", exact=True)).to_be_disabled()
            expect(dialog.locator('[role="alert"]')).to_contain_text("same request will not queue duplicates")
            dialog.get_by_role("button", name="Close", exact=True).click()
            button.click()
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role("button", name="Retry request", exact=True)).to_be_enabled()
            expect(dialog.get_by_role("button", name="Send emails", exact=True)).to_be_disabled()
            expect(dialog.get_by_role("button", name="Refresh preview", exact=True)).to_be_disabled()
            expect(dialog.get_by_label("Gmail send audience", exact=True)).to_have_value("pending")
            expect(dialog.get_by_label("Gmail send audience", exact=True)).to_be_disabled()
            assert rows() == queued_once

            with page.expect_response(is_send) as sent:
                dialog.get_by_role("button", name="Retry request", exact=True).click()
            result = sent.value.json()
            assert sent.value.status in (200, 202), result
            assert result["queued"] == 6 and result["sender"] == "instructor@tamu.edu"
            assert requests[-1] == uncertain_request
            assert result == committed_receipts[0]
            assert rows() == queued_once
            assert set(requests[-1]) == {"audience", "fingerprint", "request_id"}
            assert requests[-1]["audience"] == "pending"
            assert requests[-1]["fingerprint"] == preview["fingerprint"]
            uuid.UUID(requests[-1]["request_id"])
            expect(dialog.locator("[data-gmail-result]")).to_contain_text("6 invitations queued through instructor@tamu.edu")
            expect(dialog.get_by_role("button", name="Send emails", exact=True)).to_be_disabled()
            new_jobs = [row for row in rows() if row["id"] not in {old["id"] for old in before}]
            assert len(new_jobs) == 6
            assert {row["provider"] for row in new_jobs} == {"gmail"}
            assert {row["gmail_sender"] for row in new_jobs} == {"instructor@tamu.edu"}
            assert {row["recipient"] for row in new_jobs} == set(expected)
            assert {row["status"] for row in new_jobs} == {"pending"}
            assert len(fixture.relay.sent) == 1 and fixture.gmail.sent == []
            assert fixture.cards.mail.provider == "resend"
            # The refresh behind the dialog shows the newly queued invitations.
            expect(page.locator(".seating-cards")).to_contain_text("5 emails waiting to send")
            expect(page.locator(".seating-cards")).to_contain_text("2 emails waiting to send")
            while fixture.cards.mail.process_one():
                pass
            assert len(fixture.gmail.sent) == 6 and len(fixture.relay.sent) == 1
            assert {mail["recipient"]: mail["text"] for mail in fixture.gmail.sent} == expected
            for mail in fixture.gmail.sent:
                assert mail["text"].startswith("Hello,\n\n")
                assert "second copy" not in mail["text"]
                for name in cards:
                    assert name not in mail["subject"] + mail["text"] + mail["html"]
            assert fixture.cards.mail.provider == "resend"
            assert page.evaluate("window.gmailCspViolations") == []
            assert not errors, errors
            browser.close()
        print("PASS: confirmed-only Gmail action, non-mutating preview, safe audiences, stale409 refresh, disconnected disabled, lost receipt retries the same request after reopening without duplicate jobs, exact queued Gmail batch, generic original invitations, Resend default retained, mobile/CSP; no real sends.")
    finally:
        server.close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
