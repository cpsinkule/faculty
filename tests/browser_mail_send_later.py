"""Send later on the Mail page in a real browser (MAIL-SEND-LATER, 2026-11-07): the review's Send later… shows a campus date and time
and turns Send into Schedule, which keeps the message without sending it; the Scheduled tab lists it with Send now and Cancel, and
Cancel asks first. The round that sends it is not started (checks never run it by the clock). Nothing reaches Google."""
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gmail_mail import FakeGmail, FakeTokens, connected_gmail_app


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = connected_gmail_app(directory)
        app.test_client().get("/").close()
        mail = app.extensions["gmail_mail"]
        google = FakeGmail()
        mail.client.http, mail.google.http = google, FakeTokens()
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, timezone_id="America/Chicago")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#mail?compose=1")
                compose = page.locator("form.mail-compose")
                compose.locator("input[name=to]").fill("alex@tamu.edu")
                compose.locator("input[name=subject]").fill("Exam 2 reminder")
                compose.locator("textarea[name=body]").fill("Exam 2 is Thursday in HECC 207.")
                compose.get_by_role("button", name="Review").click()

                review = page.locator("form.mail-review")
                expect(review.get_by_role("button", name="Send to 1 recipient")).to_be_visible()
                expect(review.locator("[data-send-later]")).to_be_hidden()
                review.get_by_role("button", name="Send later…").click()
                when = review.locator("input[name=send_at]")
                expect(when).to_be_visible()
                expect(review.get_by_role("button", name="Send later…")).to_be_hidden()
                when.fill("2030-01-15T08:00")  # beyond 180 days: refused, nothing kept
                review.get_by_role("button", name="Schedule").click()
                expect(review.locator(".form-error")).to_contain_text("within 180 days")
                tomorrow = page.evaluate("""() => { const d = new Date(Date.now() + 86400000);
                    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T08:00`; }""")
                when.fill(tomorrow)
                page.screenshot(path=str(output / "mail-send-later-review.png"))
                review.get_by_role("button", name="Schedule").click()
                expect(page.locator("#toast")).to_contain_text("Scheduled for")
                expect(review).to_be_hidden()
                assert not [call for call in google.calls if call[0] == "POST"], google.calls  # nothing sent

                page.get_by_role("button", name="Scheduled").click()
                item = page.locator(".scheduled-list li")
                expect(item).to_have_count(1)
                expect(item).to_contain_text("Exam 2 reminder")
                expect(item).to_contain_text("to alex@tamu.edu · Sends")
                expect(item.locator(".chip")).to_have_text("Scheduled")
                page.screenshot(path=str(output / "mail-scheduled.png"))
                item.get_by_role("button", name="Cancel").click()
                confirm = page.locator("[data-later-confirm]")
                expect(confirm).to_contain_text("will not be sent")
                confirm.get_by_role("button", name="Cancel the message").click()
                expect(item.locator(".chip")).to_have_text("Cancelled")
                expect(item.get_by_role("button", name="Send now")).to_have_count(0)
                assert not [call for call in google.calls if call[0] == "POST"], google.calls
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Send later on the Mail page passed.")


if __name__ == "__main__":
    main()
