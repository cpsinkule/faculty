"""Changed confirmed seats stay orange until their updated notice is sent.

Runs against a temporary catalog and fake publication/mail providers; no real
email or printing. Run: .venv/bin/python tests/browser_seating_updates.py
"""
import sys
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server
from werkzeug.exceptions import ServiceUnavailable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rosters import connect
from response_cards.mail import DeliveryError
from test_seating_updates import SeatingUpdateTests


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    fixture = SeatingUpdateTests("test_swap_marks_both_students_and_only_queues_their_new_cards")
    fixture.setUp()
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    endpoint = fixture.route + "/seat-updates"
    old = fixture.recipient()
    # A completed original card must not keep the moved seat green.
    with connect(fixture.path) as db:
        db.execute("UPDATE response_card_recipients SET status='verified' WHERE id=?", (old["id"],))
        before_prints = db.execute("SELECT COUNT(*) FROM print_batches").fetchone()[0]
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.set_default_timeout(10000)
            page.add_init_script("""window.seatUpdateCsp = [];
                document.addEventListener('securitypolicyviolation', e =>
                    window.seatUpdateCsp.push(e.violatedDirective));""")
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.post_data_json)
                    if request.method == "POST" and request.url.endswith(endpoint) else None)
            page.goto(address + "/#seating/" + fixture.chart["id"])
            expect(page.locator(".seating-view")).to_be_visible()
            action = page.get_by_role("button", name="Send updated seat cards", exact=True)
            expect(action).to_have_count(0)
            current = fixture.current()
            bob = fixture.student(chart=current)
            expect(page.locator(f'[data-seat="{bob["seat_id"]}"]')).to_have_attribute("data-card-state", "complete")
            target = next(seat for seat in current["room"]["seats"]
                          if not seat["student"] and not seat["broken"] and not seat["blocked"])
            # The roster action highlights the selected student, with no
            # destination form. Cancelling cannot invalidate a completed card.
            page.locator(f'[data-move="{bob["enrollment_id"]}"]').click()
            source = page.locator(f'[data-seat="{bob["seat_id"]}"]')
            selection = page.locator(".seating-move-selection")
            expect(source).to_have_attribute("aria-pressed", "true")
            expect(page.locator(".seat.move-source")).to_have_count(1)
            expect(selection).to_contain_text("Example, Bob")
            expect(page.locator("#seating-move")).to_have_count(0)
            page.keyboard.press("Escape")
            expect(selection).to_be_hidden()
            expect(source).to_have_attribute("data-card-state", "complete")
            assert fixture.current()["version"] == current["version"]
            assert fixture.recipient()["status"] == "verified"
            # The card popover's Move or swap action enters the same selection
            # mode; clicking the empty destination performs the actual move.
            source.click()
            page.locator(".seat-popover").get_by_role("button", name="Move or swap", exact=True).click()
            expect(source).to_have_attribute("aria-pressed", "true")
            expect(page.locator(".seat-popover")).to_have_count(0)
            tile = page.locator(f'[data-seat="{target["id"]}"]')
            tile.click()
            expect(selection).to_be_hidden()
            expect(page.locator(".seat.move-source")).to_have_count(0)
            expect(tile).to_have_attribute("data-card-state", "update_pending")
            # The screen theme draws card states as outlines and labels on the neutral seat (seating-theme.css): an
            # orange frame and label from --seat-update over --seat-update-bg. (The old assertion expected the
            # pre-theme orange fill, which the theme replaced; the tile was right, the assertion was stale.)
            page.mouse.move(0, 0)
            colors = tile.evaluate("""el => {
                const probe = document.createElement('span');
                probe.style.cssText = 'color: var(--seat-update); background-color: var(--seat-update-bg)';
                el.closest('.seating-view').appendChild(probe);
                const expected = getComputedStyle(probe);
                const result = {tile: getComputedStyle(el).color, frame: getComputedStyle(el.querySelector('.seat-frame')).color,
                                background: getComputedStyle(el).backgroundColor, orange: expected.color, paper: expected.backgroundColor};
                probe.remove();
                return result;
            }""")
            assert colors["tile"] == colors["frame"] == colors["orange"], colors
            assert colors["background"] == colors["paper"], colors
            assert colors["orange"] not in (colors["paper"], "rgba(0, 0, 0, 0)"), colors
            expect(page.locator(".room-stat.card-update-pending")).to_have_text("1 seat update not sent")
            expect(page.locator(".seat-legend")).to_contain_text("Seat update not sent")
            expect(page.locator("#seating-next-title")).to_have_text("1 student needs an updated seat card")
            # The list says what the sidebar badge sends the instructor there for (read in a second tab).
            listing = browser.new_page(viewport={"width": 1440, "height": 1000})
            listing.goto(address + "/#seating")
            expect(listing.locator("#page-subtitle")).to_have_text("1 room chart · 1 needs attention")
            expect(listing.locator(".seating-card .seating-flag-updates")).to_have_text("1 updated seat card to send")
            listing.close()
            tile.scroll_into_view_if_needed()
            page.screenshot(path=str(output / "seating-seat-update-orange.png"))
            row = page.locator(".seating-table tbody tr").filter(has_text="Example, Bob")
            expect(row.locator(".seating-card-state.update_pending")).to_have_text("Seat update not sent")
            tile.click()
            popup = page.locator(".seat-popover.update_pending")
            expect(popup).to_contain_text("Seat update not sent")
            expect(popup.locator("[data-pop-resend]")).to_have_count(0)
            expect(popup.locator("[data-pop-link-text]")).to_have_count(0)
            popup.get_by_role("button", name="Close", exact=True).click()
            assert len(fixture.mail_rows()) == 9
            assert fixture.client.get(f"/api/response-cards/cards/{old['id']}/link").status_code == 409
            action.click()
            modal = page.locator("#seating-seat-updates")
            expect(modal.get_by_label("Send from", exact=True)).to_have_value("default")
            expect(modal.get_by_role("region", name="Updated seat card recipients")).to_contain_text("Example, Bob")
            expect(modal.locator("tbody tr")).to_have_count(1)
            expect(modal.locator("tbody")).to_contain_text(target["label"])
            card_preview = modal.locator("[data-seat-update-card-preview]")
            preview_student = modal.get_by_label("Preview updated card for", exact=True)
            message = modal.locator(".seating-update-message")
            def assert_card_preview(name, seat):
                option = preview_student.locator("option").filter(has_text=name)
                expect(option).to_have_count(1)
                expect(option).to_contain_text(seat)
                preview_student.select_option(option.get_attribute("value"))
                expect(message).to_contain_text(f"updated to {seat}.")
                expect(message).to_contain_text("Enter your UIN")
                expect(message).not_to_contain_text("Bob")
                expect(message).not_to_contain_text("Student, 4")
                expect(modal).not_to_contain_text("[new seat]")
            assert_card_preview("Example, Bob", target["label"])
            send = modal.locator("[data-seat-update-send]")
            expect(send).to_have_text("Send updated emails")
            expect(send).to_be_enabled()
            assert requests == [] and len(fixture.mail_rows()) == 9
            # A fresh review always starts with the default provider.
            modal.get_by_label("Send from", exact=True).select_option("gmail")
            modal.get_by_role("button", name="Close", exact=True).click()
            action.click()
            expect(modal.get_by_label("Send from", exact=True)).to_have_value("default")

            # Another move makes this reviewed version stale; no email is queued.
            fixture.move("Student, 4")
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith(endpoint)) as stale:
                send.click()
            assert stale.value.status == 409, stale.value.text()
            expect(modal.locator('[role="alert"]')).to_contain_text("Refresh")
            expect(send).to_be_disabled()
            expect(send).to_have_css("cursor", "not-allowed")
            expect(card_preview).to_be_hidden()
            assert len(fixture.mail_rows()) == 9
            # Refresh discards the obsolete card text while the current seats
            # load; the disabled Send button must not imply a queued send.
            pending_previews = []
            page.route("**" + fixture.route, lambda route: pending_previews.append(route), times=1)
            modal.get_by_role("button", name="Refresh preview", exact=True).click()
            expect(modal.locator("[data-seat-update-preview]")).to_have_attribute("aria-busy", "true")
            expect(card_preview).to_be_hidden()
            expect(send).to_be_disabled()
            expect(send).to_have_css("cursor", "not-allowed")
            expect(modal.locator("[data-seat-update-refresh]")).to_have_css("cursor", "wait")
            assert len(pending_previews) == 1
            preview_response = pending_previews[0].fetch()
            assert preview_response.status == 200, preview_response.text()
            pending_previews[0].fulfill(response=preview_response)
            expect(modal.locator("tbody tr")).to_have_count(2)
            expect(send).to_be_enabled()
            expect(modal.locator('[aria-busy="true"]')).to_have_count(0)
            other = fixture.student("Student, 4")
            assert_card_preview("Student, 4", other["seat"])
            assert_card_preview("Example, Bob", target["label"])

            # Refresh must not silently fall back from a disconnected chosen Gmail.
            provider = modal.get_by_label("Send from", exact=True)
            provider.select_option("gmail")
            fixture.gmail.connected = False
            modal.get_by_role("button", name="Refresh preview", exact=True).click()
            expect(modal.locator("[data-seat-update-provider]")).to_contain_text("Gmail is disconnected")
            expect(provider).to_have_value("gmail")
            expect(send).to_be_disabled()
            expect(send).to_have_css("cursor", "not-allowed")
            provider.select_option("default")
            expect(send).to_be_enabled()
            fixture.gmail.connected = True
            modal.get_by_role("button", name="Refresh preview", exact=True).click()
            expect(provider.locator('option[value="gmail"]')).to_contain_text("instructor@tamu.edu")
            provider.select_option("gmail")
            # The narrowest desktop window on a laptop-height screen (desktop only; there is no phone layout).
            page.set_viewport_size({"width": 1280, "height": 800})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert modal.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            expect(send).to_be_in_viewport()
            page.screenshot(path=str(output / "seating-seat-updates-1280.png"))
            page.set_viewport_size({"width": 1440, "height": 1100})

            # Lose the response after the real endpoint commits. Explicit retry
            # after closing/reopening must reuse precisely the same command.
            pending_sends = []
            page.route("**" + endpoint, lambda route: pending_sends.append(route), times=1)
            send.click()
            expect(send).to_have_text("Queueing…")
            expect(send).to_be_disabled()
            expect(send).to_have_css("cursor", "wait")
            expect(send).to_have_attribute("aria-busy", "true")
            expect(message).not_to_contain_text("[new seat]")
            assert len(pending_sends) == 1
            response = pending_sends[0].fetch()
            assert response.status in (200, 201), response.text()
            pending_sends[0].fulfill(status=502, content_type="text/plain", body="Temporary proxy error")
            expect(modal.locator('[role="alert"]')).to_contain_text("could not confirm")
            first_payload = requests[-1]
            assert set(first_payload) == {"version", "request_id", "mail_provider"}
            assert first_payload["mail_provider"] == "gmail"
            uuid.UUID(first_payload["request_id"])
            queued = [row for row in fixture.mail_rows() if row["status"] == "pending"]
            assert len(queued) == 2 and all(row["provider"] == "gmail" for row in queued)
            assert fixture.gmail.sent == []
            expect(provider).to_be_disabled()
            expect(send).to_be_disabled()
            expect(send).to_have_css("cursor", "not-allowed")
            expect(modal.locator('[aria-busy="true"]')).to_have_count(0)
            expect(modal.get_by_role("button", name="Refresh preview", exact=True)).to_be_disabled()
            modal.get_by_role("button", name="Close", exact=True).click()
            action.click()
            expect(provider).to_have_value("gmail")
            modal.get_by_role("button", name="Retry request", exact=True).click()
            expect(modal.locator("[data-seat-update-result]")).to_contain_text("2 updated seat emails queued")
            expect(send).to_have_text("Emails queued")
            expect(send).to_be_disabled()
            expect(send).to_have_css("cursor", "not-allowed")
            expect(modal.locator('[aria-busy="true"]')).to_have_count(0)
            assert requests[-1] == first_payload
            assert len(fixture.mail_rows()) == 11
            expect(page.locator('.seat[data-card-state="update_pending"]')).to_have_count(2)
            expect(modal.locator("tbody")).to_contain_text("Queued; sending not confirmed")
            with connect(fixture.path) as db:
                assert db.execute("SELECT COUNT(*) FROM print_batches").fetchone()[0] == before_prints
            fixture.drain()
            assert len(fixture.gmail.sent) == 2
            for message in fixture.gmail.sent:
                assert message["subject"].startswith("UPDATED:")
                assert message["text"].startswith("Hello,\n\n")
                assert "Bob" not in message["text"] and "Student, 4" not in message["text"]
            modal.get_by_role("button", name="Refresh preview", exact=True).click()
            expect(modal).to_contain_text("No updated seat cards are waiting")
            expect(card_preview).to_be_hidden()
            expect(send).to_be_disabled()
            expect(send).to_have_css("cursor", "not-allowed")
            expect(page.locator('.seat[data-card-state="update_pending"]')).to_have_count(0)
            modal.get_by_role("button", name="Close", exact=True).click()
            page.reload()
            expect(page.locator(".seating-view")).to_be_visible()
            expect(page.locator('.seat[data-card-state="update_pending"]')).to_have_count(0)
            expect(action).to_have_count(0)
            assert fixture.cards.mail.status()["provider"] == "resend"

            # A later update opens a new request with the normal default again.
            fixture.move()
            page.reload()
            action.click()
            expect(provider).to_have_value("default")
            send.click()
            expect(modal.locator("[data-seat-update-result]")).to_contain_text("1 updated seat email queued")
            expect(send).to_have_text("Emails queued")
            expect(send).to_have_css("cursor", "not-allowed")
            assert requests[-1]["request_id"] != first_payload["request_id"]
            assert requests[-1]["mail_provider"] == "default"
            queued = [row for row in fixture.mail_rows() if row["status"] == "pending"]
            assert len(queued) == 1 and queued[0]["provider"] == "resend"
            modal.get_by_role("button", name="Close", exact=True).click()
            fixture.relay.fail = DeliveryError("Simulated updated-seat delivery failure", False)
            fixture.drain()
            page.reload()
            expect(page.locator('.seat[data-card-state="update_pending"]')).to_have_count(1)
            action.click()
            expect(modal.locator("tbody")).to_contain_text("Sending failed")
            expect(send).to_be_enabled()
            modal.get_by_role("button", name="Close", exact=True).click()
            fixture.relay.fail = None

            # Uncertain delivery requires a separate individual resend decision.
            card = fixture.recipient()
            with connect(fixture.path) as db:
                db.execute("UPDATE response_card_recipients SET delivery_status='unconfirmed' WHERE id=?", (card["id"],))
                db.execute("UPDATE response_card_mail SET status='unconfirmed' WHERE card_id=? AND status='failed'", (card["id"],))
            page.reload()
            action.click()
            expect(modal).to_contain_text("check whether the student received it")
            expect(send).to_be_disabled()
            modal.get_by_role("button", name="Close", exact=True).click()
            page.get_by_role("button", name="Response card details for Example, Bob", exact=True).click()
            page.locator("#seating-card-details").get_by_role("button", name="Resend email", exact=True).click()
            resend = page.locator("#seating-resend")
            expect(resend).to_contain_text("Check with the student before sending another copy")
            resend.get_by_role("button", name="Cancel", exact=True).click()

            # A failed old-link revocation is stated honestly and the successful
            # command receipt can contain zero queued plus visible error details.
            remote_call = fixture.cards.remote.call
            def unavailable_control(method, path, data=None):
                if method == "POST" and path.endswith("/control"):
                    raise ServiceUnavailable("Response service is temporarily unavailable")
                return remote_call(method, path, data)
            with patch.object(fixture.cards.remote, "call", side_effect=unavailable_control):
                fixture.move()
                page.reload()
                action.click()
                expect(modal.locator("tbody")).to_contain_text("Old link revocation pending")
                expect(send).to_be_enabled()
                send.click()
                expect(modal.locator("[data-seat-update-result]")).to_contain_text("0 updated seat emails queued")
                expect(modal.locator('[role="alert"]')).to_be_visible()
                expect(send).to_be_disabled()
                expect(modal.get_by_role("button", name="Retry request", exact=True)).to_be_hidden()
                assert not any(row["status"] == "pending" for row in fixture.mail_rows())
            assert page.evaluate("window.seatUpdateCsp") == []
            assert errors == [], errors
            browser.close()
        print("PASS: moved completed cards turn orange; actual-seat previews omit student names; busy cursor only during network work; queued button becomes idle; review is inert; stale review and disconnected Gmail cannot send; queued/failed stay orange; lost receipt retries without duplicates; sending confirmed clears orange; old-link revocation errors visible; default Resend retained; mobile/CSP; no real mail.")
    finally:
        server.close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
