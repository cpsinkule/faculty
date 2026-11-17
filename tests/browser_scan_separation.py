"""INBOX "SEPARATING A SCAN" in the browser: a scan naming two assignments reads Working with what Faculty is doing, then is two
scans named by assignment, and waits under Deleted with its reason (synthetic data).

Run from the project root: python tests/browser_scan_separation.py
"""
import io
import sys
import tempfile
import threading
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
        inbox, scans, documents = app.extensions["inbox"], app.extensions["document_scans"], app.extensions["documents"]
        catalog = documents.catalog()
        printed = {}
        for title in ("Quiz 6", "Quiz 7"):
            assignment = documents.create_assignment(catalog["definitions"][0]["id"], title.lower().replace(" ", "-"), title)
            version = documents.register_version(assignment["id"], "A", (FIXTURES / "letter.pdf").read_bytes(),
                                                 {"main.tex": (FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
            batch = documents.create_batch(version["id"], catalog["sections"][0]["id"], 2, str(uuid.uuid4()))
            assert documents.build_one()
            printed[title] = PdfReader(io.BytesIO(documents.download(batch["id"])))
        # Two Quiz 7 papers with one Quiz 6 paper between them, as a stack of makeups comes off the copier.
        writer = PdfWriter()
        for page in [*printed["Quiz 7"].pages[:3], *printed["Quiz 6"].pages[:3], *printed["Quiz 7"].pages[3:6]]:
            writer.add_page(page)
        content = io.BytesIO()
        writer.write(content)
        source = inbox.submit({"action": "scan.receive"}, [{"filename": "SKM_makeups.pdf", "content": content.getvalue()}], source="email",
                              sender="scanner", original=b"retained-email:" + content.getvalue(), idempotency_key=str(uuid.uuid4()),
                              authenticated=True, instructor_review_required=True)
        while scans.process_one():
            pass
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.set_default_timeout(20000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # Before the worker's turn: the scan is the worker's, not the instructor's, and says what is happening.
                page.goto(f"{address}/#inbox")
                tile = page.locator(".inbox-scan-tile")
                expect(tile).to_have_count(1)
                expect(tile).to_have_attribute("data-scan-state", "wait")
                expect(tile.locator(".inbox-badge")).to_have_text("Working")
                expect(tile.locator(".inbox-scan-step")).to_have_text(
                    "The labels name more than one assignment (Quiz 7 and Quiz 6). Faculty is separating these pages into one scan for each.")
                for width in (1440, 1280):  # desktop only (DESIGN-LANGUAGE): it fits down to 1280 px
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Inbox overflow at {width}"
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.screenshot(path=str(output / "scan-separation-before.png"), full_page=True)
                # The worker's turn, then the new scans' inspection: the list shows one scan per assignment, each named by its file.
                assert scans.separation.process_one()
                while scans.process_one():
                    pass
                tiles = page.locator(".inbox-scan-tile")
                expect(tiles).to_have_count(2)
                expect(page.locator(".inbox-scan-tile", has_text="SKM_makeups (Quiz 7).pdf")).to_contain_text("Quiz 7 · MATH 140 · Section 501")
                expect(page.locator(".inbox-scan-tile", has_text="SKM_makeups (Quiz 6).pdf")).to_contain_text("Quiz 6 · MATH 140 · Section 501")
                expect(page.locator(".inbox-scan-tile", has_text="Several assignments")).to_have_count(0)
                for width in (1440, 1280):
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Inbox overflow at {width}"
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.screenshot(path=str(output / "scan-separation-after.png"), full_page=True)
                # The scan it came from is under Deleted, says why, and can be restored.
                page.goto(f"{address}/#inbox/job/{source['id']}")
                notice = page.locator(".inbox-deleted-notice")
                expect(notice).to_contain_text("Reason given: Separated by assignment: its pages are in a new scan for each of Quiz 7 and Quiz 6.")
                expect(page.get_by_role("button", name="Restore to the Inbox")).to_be_visible()
                page.screenshot(path=str(output / "scan-separation-deleted.png"), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Scan separation browser checks passed: the Working tile, one scan per assignment, the deleted scan's reason")


if __name__ == "__main__":
    main()
