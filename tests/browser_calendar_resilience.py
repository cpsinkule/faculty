"""Calendar failure recovery and delayed-request races in an isolated browser."""
import json
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app


HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
FEED = "**/api/calendar?*"


def wait_pending(page, pending, count=1):
    # The request handler deliberately holds the response; let Playwright dispatch
    # it without depending on the server's speed or sleeping for a fixed request.
    for _ in range(100):
        if len(pending) >= count:
            return
        page.wait_for_timeout(20)
    raise AssertionError(f"Expected {count} delayed calendar requests, saw {len(pending)}")


def reply(client, route):
    response = client.get("/api/calendar?" + route.request.url.split("?", 1)[1])
    assert response.status_code == 200, response.json
    route.fulfill(status=200, content_type="application/json", body=json.dumps(response.json))


def main():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        client = app.test_client()
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
                url = address + "/#calendar?month=2026-09&date=2026-09-15&view=agenda&span=month"
                root = page.locator("#calendar-section")

                def unavailable(route):
                    route.fulfill(status=503, content_type="application/json",
                                  body=json.dumps({"error": "Synthetic calendar unavailable"}))

                # An entirely new workspace still has a useful error/retry path,
                # and adding a workspace event never requires a course first.
                page.route(FEED, unavailable)
                page.goto(url)
                expect(root.locator("[data-calendar-error]")).to_contain_text("Synthetic calendar unavailable")
                expect(root.get_by_role("button", name="Try again", exact=True)).to_be_visible()
                page.unroute(FEED, unavailable)
                root.get_by_role("button", name="Try again", exact=True).click()
                expect(page.locator("#calendar-month-title")).to_have_text("September 2026")
                expect(root).to_contain_text("No events match this month and these filters.")
                root.get_by_role("button", name="Add an event", exact=True).click()
                form = page.locator("#calendar-event-form")
                expect(form.get_by_role("combobox", name="Scope", exact=True)).to_have_value("workspace")
                expect(form.locator('select[name="definition_id"]')).to_be_disabled()
                form.get_by_label("Title", exact=True).fill("Month 09 event")
                form.get_by_role("button", name="Save event", exact=True).click()
                expect(form).not_to_be_visible()
                expect(root.locator(".calendar-event")).to_contain_text("Month 09 event")
                for month in ("10", "11"):
                    response = client.post("/api/calendar/events", json={"request_id": str(uuid.uuid4()),
                        "date": f"2026-{month}-15", "title": f"Month {month} event"}, headers=HEADERS)
                    assert response.status_code == 201, response.json

                # A failed new-month request cannot turn into an apparently empty
                # month when a view or filter changes. The error stays visible.
                page.route(FEED, unavailable)
                root.get_by_role("button", name="Next month", exact=True).click()
                expect(root.locator("[data-calendar-error]")).to_be_visible()
                # The view switch's Month (the Agenda's Day/Week/Month range, since 0591ff7, has a Month of its own).
                root.get_by_role("group", name="Calendar view").get_by_role("button", name="Month", exact=True).click()
                expect(root.locator("[data-calendar-error]")).to_be_visible()
                root.get_by_role("combobox", name="Event type", exact=True).select_option("custom")
                expect(root.locator("[data-calendar-error]")).to_be_visible()
                page.unroute(FEED, unavailable)
                root.get_by_role("button", name="Refresh", exact=True).click()
                expect(page.locator("#calendar-month-title")).to_have_text("October 2026")
                expect(root.locator("[data-calendar-error]")).to_be_hidden()
                root.get_by_role("button", name="Agenda", exact=True).click()
                root.get_by_role("button", name="Month", exact=True).last.click()  # Agenda opens on one day; these checks read the month
                expect(root.locator(".calendar-event")).to_contain_text("Month 10 event")

                # A late response from an earlier month cannot overwrite the
                # requested month, even when it arrives after the newer response.
                page.goto(url)
                expect(root.locator(".calendar-event")).to_contain_text("Month 09 event")
                pending = []
                page.route(FEED, lambda route: pending.append(route))
                root.get_by_role("button", name="Next month", exact=True).click()
                wait_pending(page, pending)
                root.get_by_role("button", name="Next month", exact=True).click()
                wait_pending(page, pending, 2)
                reply(client, pending.pop())
                expect(page.locator("#calendar-month-title")).to_have_text("November 2026")
                expect(root.locator(".calendar-event")).to_contain_text("Month 11 event")
                reply(client, pending.pop())
                expect(page.locator("#calendar-month-title")).to_have_text("November 2026")
                expect(root.locator(".calendar-event")).to_contain_text("Month 11 event")
                page.unroute(FEED)

                # Add waits for its catalog. Opening a newer event dialog during
                # that wait supersedes the original request to open a create form.
                page.goto(url)
                expect(root.locator(".calendar-event")).to_contain_text("Month 09 event")
                page.route(FEED, lambda route: pending.append(route))
                page.get_by_role("button", name="Add event", exact=True).click()
                wait_pending(page, pending)
                root.locator(".calendar-event").click()
                expect(page.locator("#dialog h2")).to_have_text("Month 09 event")
                reply(client, pending.pop())
                expect(root.locator("[data-calendar-error]")).to_be_hidden()
                expect(page.locator("#dialog h2")).to_have_text("Month 09 event")
                expect(page.locator("#calendar-event-form")).to_have_count(0)
                page.unroute(FEED)

                # Arrow navigation remains usable after loading the adjoining
                # month; the new grid must retain focus on the selected day.
                page.locator("#dialog").get_by_role("button", name="Close", exact=True).click()
                page.goto(address + "/#calendar?month=2026-09&date=2026-09-30&view=month")
                september = root.locator('.calendar-day-number[data-calendar-day="2026-09-30"]')
                expect(september).to_be_visible()
                september.focus()
                september.press("ArrowRight")
                expect(page.locator("#calendar-month-title")).to_have_text("October 2026")
                october = root.locator('.calendar-day-number[data-calendar-day="2026-10-01"]')
                expect(october).to_be_focused()
                october.press("ArrowLeft")
                expect(page.locator("#calendar-month-title")).to_have_text("September 2026")
                expect(september).to_be_focused()
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Calendar resilience: empty workspace, initial retry, failed navigation, retained error, delayed month response, superseded Add dialog and keyboard month boundaries passed.")


if __name__ == "__main__":
    main()
