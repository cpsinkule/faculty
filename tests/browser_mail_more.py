"""Show more on the Mail page's Students list in a real browser (requirement, 2026-10-01): a failed Show more keeps the students already listed, says why next to Show
more, and Show more then asks for the same page again. The reader opens beside the list (desktop only, DESIGN-LANGUAGE), with no
phone "Back to the list". The mail listing is the browser's own route stub; nothing reaches Google."""
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gmail_mail import FakeGmail, FakeTokens, connected_gmail_app


def row(thread_id, name):
    return {"id": thread_id, "subject": f"Question from {name}", "snippet": "Can I come to office hours?", "from": [f"{name} <x@tamu.edu>"],
            "at": "2026-10-01T14:00:00Z", "count": 1, "unread": True,
            "student": {"name": name, "section": 507, "code": "MATH 140", "dropped": False}}


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = connected_gmail_app(directory)
        app.test_client().get("/").close()
        mail = app.extensions["gmail_mail"]
        mail.client.http, mail.google.http = FakeGmail(), FakeTokens()
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        asked, fail_next = [], [True]

        def listing(route):
            token = dict(item.split("=", 1) for item in route.request.url.split("?", 1)[1].split("&")).get("page_token", "")
            asked.append(token)
            if token == "p2" and fail_next[0]:
                fail_next[0] = False
                return route.abort("connectionreset")
            threads, more = ([row("a1", "Example, Alice")], "p2") if not token else ([row("b2", "Example, Bob")], "")
            # One page per scan, so the first read stops after page one and offers Show more.
            route.fulfill(json={"threads": threads, "next": more, "tab": "students", "query": "", "scan": {"want": 20, "pages": 1}})

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # Only the listing (a query on /api/mail/threads): a glob's "?" also matches the "/" of a thread's own read.
                page.route(lambda url: "/api/mail/threads?" in url, listing)
                page.goto(f"{address}/#mail")
                rows = page.locator(".mail-list .mail-thread")
                expect(rows).to_have_count(1)
                more = page.get_by_role("button", name="Show more")
                more.click()
                # The failed Show more keeps Alice, says why, and offers Show more again.
                expect(page.locator(".mail-more.field-hint")).to_contain_text("Could not read more: Can’t reach your server")
                expect(rows).to_have_count(1)
                expect(rows.first).to_contain_text("Example, Alice")
                expect(page.get_by_text("Mail could not be read")).to_have_count(0)
                page.screenshot(path=str(output / "mail-more-failed.png"))
                more.click()
                expect(rows).to_have_count(2)
                expect(rows.nth(1)).to_contain_text("Example, Bob")
                expect(page.locator(".mail-more.field-hint")).to_have_count(0)
                expect(more).to_have_count(0)
                assert asked == ["", "p2", "p2"], asked

                # The reader opens beside the list, the list still in view; there is no phone "Back to the list".
                page.goto(f"{address}/#mail?thread=a1")
                expect(page.locator(".mail-reader-head h2")).to_have_text("Question about Exam 1")
                expect(page.locator(".mail-reader-head h2")).to_be_in_viewport()
                expect(rows.first).to_be_in_viewport()
                expect(page.locator(".mail-back-to-list")).to_have_count(0)
                page.screenshot(path=str(output / "mail-reader.png"))
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("mail show more ok")


if __name__ == "__main__":
    main()
