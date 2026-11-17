"""Google Calendar sync in a real browser (AGENTS.md "Google Calendar sync"): Connect goes to Google's sign-in and comes back to
the Calendar, the first sync runs at once, a Google event shows read-only and opens in Google, and Remove takes Faculty's events
out of Google. A fake Google answers everything; nothing leaves this machine.
"""
import re
import sys
import threading
import urllib.parse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from browser_calendar import no_overflow, watch_errors
from response_cards.gmail import SCOPES
from test_google_calendar import ACCOUNT, Base, chicago


class Seed(Base):
    def runTest(self):
        pass


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    fixture = Seed()
    fixture.setUp()
    fixture.write_token(sorted(SCOPES))  # connected for Response Card mail only, as the live workspace is
    fixture.custom(title="Office planning", date=fixture.day(2))
    fixture.google.events["dentist"] = {"id": "dentist", "summary": "Dentist", "location": "Main St",
                                        "start": {"dateTime": chicago(fixture.day(1), "15:30:00")}, "end": {"dateTime": chicago(fixture.day(1), "16:30:00")}}
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(reduced_motion="reduce", viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.set_default_timeout(15000)
            errors = []
            watch_errors(page, errors)
            signin = []
            page.route("https://accounts.google.com/**", lambda route: (signin.append(route.request.url),
                       route.fulfill(status=200, content_type="text/html", body="<p>Google sign-in (test)</p>")))
            page.goto(address + "/#calendar")
            root = page.locator("#calendar-section")
            button = root.locator("[data-calendar-google]")
            expect(button).to_contain_text("Connect")
            expect(root.locator('.calendar-legend [data-kind="google"]')).to_have_count(0)  # no empty type before the sync shows any
            button.click()
            dialog = page.locator("#dialog")
            expect(dialog.get_by_text(ACCOUNT)).to_be_visible()
            dialog.screenshot(path=str(output / "calendar-google-connect.png"))
            dialog.get_by_role("button", name="Connect Google Calendar", exact=True).click()
            page.wait_for_url(re.compile(r"accounts\.google\.com"))
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(signin[-1]).query))
            assert "https://www.googleapis.com/auth/calendar.events" in query["scope"].split(), query
            # Google sends the instructor back to Faculty's registered callback, which returns to the Calendar.
            page.goto(f"{address}/api/response-cards/gmail/callback?state={query['state']}&code=good")
            page.wait_for_url(re.compile(r"#calendar"))
            expect(page.locator("#toast")).to_contain_text("Synced with Google Calendar")
            expect(dialog.get_by_text(re.compile(r"Synced (just now|\d+ minutes? ago)"))).to_be_visible()
            expect(dialog.get_by_label("Your events", exact=True)).to_be_checked()
            expect(dialog.get_by_label(re.compile(r"prof@tamu.edu"))).to_be_checked()
            assert "google=1" not in page.url, page.url
            live = fixture.google.live()
            assert [item["summary"] for item in live.values() if item["id"] != "dentist"] == ["Office planning"], live
            dialog.get_by_role("button", name="Close", exact=True).click()
            for width in (1440, 1280):
                page.set_viewport_size({"width": width, "height": 1000})
                root.locator("[data-calendar-google]").click()
                expect(dialog.get_by_role("button", name="Sync now", exact=True)).to_be_visible()
                expect(dialog.locator('input[name="calendar"]')).to_have_count(2)
                no_overflow(page)
                assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
                dialog.screenshot(path=str(output / f"calendar-google-settings-{width}.png"))
                dialog.get_by_role("button", name="Close", exact=True).click()
            page.set_viewport_size({"width": 1440, "height": 1000})
            expect(root.locator("[data-calendar-google]")).to_contain_text("Synced")
            expect(root.locator('.calendar-legend [data-kind="google"]')).to_be_visible()
            root.get_by_role("button", name="Agenda", exact=True).click()
            root.get_by_role("button", name="Month", exact=True).last.click()  # Agenda opens on one day; these checks read the month
            dentist = root.locator('[data-calendar-event="google:primary:dentist"]').first
            expect(dentist).to_contain_text("3:30–4:30 PM")
            page.screenshot(path=str(output / "calendar-google-agenda.png"), full_page=True)
            dentist.click()
            link = dialog.get_by_role("link", name="Open in Google Calendar", exact=True)
            expect(link).to_have_attribute("target", "_blank")
            expect(link).to_have_attribute("href", "https://www.google.com/calendar/event?eid=dentist")
            expect(dialog.get_by_text("This event comes from your Google Calendar")).to_be_visible()
            dialog.get_by_role("button", name="Close", exact=True).click()
            root.locator("[data-calendar-google]").click()
            dialog.get_by_role("button", name="Remove Faculty's events…", exact=True).click()
            dialog.get_by_role("button", name="Remove 1 event", exact=True).click()
            expect(page.locator("#toast")).to_contain_text("removed from Google Calendar")
            assert [item["id"] for item in fixture.google.live().values()] == ["dentist"], fixture.google.live()
            expect(dialog.get_by_label("Your events", exact=True)).not_to_be_checked()
            assert not errors, errors
            print("Google Calendar sync browser check passed.")
            browser.close()
    finally:
        server.close()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
