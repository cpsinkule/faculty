"""A scan's page when a read fails, while it polls and while the Inbox is hidden; a failed inspection's card (synthetic data).

Run from the project root: python tests/browser_scan_refresh.py
"""
import io
import json
import re
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader, PdfWriter
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_course_pages import seed_course

FIXTURES = Path(__file__).resolve().parents[1] / "document_identity/fixtures"


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(temporary)
        seed_course(app.test_client(), sections=[501, 502])
        inbox, scans = app.extensions["inbox"], app.extensions["document_scans"]
        writer = PdfWriter()
        writer.add_page(PdfReader(FIXTURES / "letter.pdf").pages[0])
        content = io.BytesIO()
        writer.write(content)
        submit = lambda name, data: inbox.submit({"action": "scan.receive"}, [{"filename": name, "content": data}], source="email", sender="scanner",
                                                 original=b"retained-email:" + data, idempotency_key=str(uuid.uuid4()), authenticated=True,
                                                 instructor_review_required=True)
        # A file that is not a PDF fails its inspection with Faculty's reason; then a scan waits for the worker (none runs here), so
        # its page looks again every 5 s.
        broken = submit("scan.pdf", b"not a PDF")
        assert scans.process_one()
        waiting = submit("waiting.pdf", content.getvalue())
        message = waiting["message_id"]
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(15000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # A failed inspection (inbox-7): why, above the card, and Retry as the card's one action instead of a way to the
                # Scan stage. The original stays; Retry queues the inspection again and says so.
                page.goto(f"{address}/#inbox/job/{broken['id']}")
                failed = page.locator(".document-recognition > .callout")
                expect(failed).to_contain_text("Its pages could not be checked")
                expect(failed).to_contain_text("Scanner inspection currently accepts PDF attachments. The original is kept.")
                card = page.locator("[data-scan-card]")
                expect(card.locator("[data-scan-next-action]")).to_have_count(0)
                expect(page.locator("[data-scan-content] .primary")).to_have_text(["Retry inspection"])
                expect(card.get_by_role("button", name="Retry inspection")).to_be_visible()
                for width in (1440, 1280):  # desktop only (DESIGN-LANGUAGE): it fits down to 1280 px
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Failed scan overflow at {width}"
                    page.screenshot(path=str(output / f"scan-inspection-failed-{width}.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                card.get_by_role("button", name="Retry inspection").click()
                expect(page.locator("#toast")).to_contain_text("Inspection queued again.")
                assert scans.review_item(broken["message_id"])["decode"]["status"] == "pending"
                # The first read of a scan's page fails (missed: a bare message that nothing ever replaced): the page keeps its
                # frame and says so with Try again, and the Inbox's later reads draw into it.
                reads, failing = [], {"left": 1}
                def route(request_route):
                    reads.append(time.time())
                    if failing["left"] > 0:
                        failing["left"] -= 1
                        return request_route.fulfill(status=503, content_type="application/json", body=json.dumps({"error": "Simulated failure."}))
                    request_route.continue_()
                page.route(re.compile(rf".*/api/documents/messages/{message}(\?view=summary)?$"), route)
                page.goto(f"{address}/#inbox/job/{waiting['id']}")
                notice = page.locator("[data-scan-content] .callout")
                expect(notice).to_contain_text("This scan's page could not be read")
                expect(notice).to_contain_text("Simulated failure.")
                expect(page.locator(".document-scan-review [data-scan-content]")).to_have_count(1)
                notice.get_by_role("button", name="Try again").click()
                expect(page.locator("[data-scan-card]")).to_be_visible()
                expect(page.locator("[data-scan-content] .callout")).to_have_count(0)
                # A failed look while the scan waits says so beside Updated, and the look carries on (bugs-frontend-4).
                updated = page.locator("[data-scan-updated]")
                failing["left"] = 1
                expect(updated).to_contain_text(re.compile(r"^Couldn't refresh at .* · trying again$"), timeout=12000)
                expect(updated).to_contain_text(re.compile(r"^Updated "), timeout=20000)
                # Nothing is read while the Inbox is hidden behind another page (bugs-frontend-6); coming back reads at once.
                page.evaluate("() => { location.hash = '#grading'; }")
                expect(page.locator("#inbox-section")).to_be_hidden()
                page.wait_for_timeout(1000)
                before = len(reads)
                page.wait_for_timeout(12000)
                assert len(reads) == before, f"{len(reads) - before} reads of the scan while the Inbox was hidden"
                page.evaluate(f"() => {{ location.hash = '#inbox/job/{waiting['id']}'; }}")
                expect(page.locator("[data-scan-card]")).to_be_visible()
                page.wait_for_timeout(1500)
                assert len(reads) > before, "the scan was not read again on coming back"
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Failed inspection card, failed first read with Try again, failed poll that carries on, and no reads while the Inbox is hidden passed.")


if __name__ == "__main__":
    main()
