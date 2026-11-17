"""Truthful badges: the Teaching Inbox badge equals what Inbox Current says needs the instructor, and a deleted scan
counts nowhere (no badge, no tab-title count, no Home count) until it is restored.

The badge comes from /api/workspace/overview's derived Inbox card (the Inbox's own filing, PRESENT only); Current's
status line comes from InboxService.listing. Both are views: nothing here writes a derived state.
"""
import io
import re
import sys
import tempfile
import threading
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app


def blank_pdf(shade):
    buffer = io.BytesIO()
    Image.new("L", (850, 1100), shade).save(buffer, format="PDF", resolution=100)
    return buffer.getvalue()


def badge_number(page):
    badge = page.locator("#nav-badge-inbox")
    if not badge.is_visible():
        return 0
    return int(re.match(r"^(\d+)", badge.inner_text()).group(1))


def current_needs(page, expected=None):
    """needs you + failed from Inbox Current's status line (the badge's share of Current). The page is reloaded so the
    list is read afresh; with `expected`, waits until Current says it (no failed jobs in this fixture)."""
    page.goto(page.url.split("#")[0] + "#inbox")
    page.reload()
    summary = page.locator("#inbox-search-summary")
    # Current lists scans only (2026-10-18, review item 4) and names the other submissions that need you after them.
    line = r"^(\d+) needs? you · \d+ working · (\d+) failed(?: · (\d+) other submissions? needs? you)?\s*$"
    expect(summary).to_have_text(re.compile(line))
    # textContent, as to_have_text above reads it: on screen the strip is an uppercase label (CSS), which inner_text() returns.
    if expected is not None:
        def total():
            needs, failed, other = re.match(line, summary.text_content()).groups()
            return int(needs) + int(failed) + int(other or 0)
        for _ in range(50):
            if total() == expected:
                break
            page.wait_for_timeout(100)
    needs, failed, other = re.match(line, summary.text_content()).groups()
    return int(needs) + int(failed) + int(other or 0)


def settle_badge(page, expected):
    # The shell reads the overview on Home at once and elsewhere shortly after a refresh; Home is the reliable read.
    page.goto(page.url.split("#")[0] + "#overview")
    if expected:
        expect(page.locator("#nav-badge-inbox")).to_have_text(re.compile(rf"^{expected} needs? attention$"), timeout=15000)
        expect(page).to_have_title(re.compile(rf"^\({expected}\) Home · "))
    else:
        expect(page.locator("#nav-badge-inbox")).to_be_hidden(timeout=15000)
        expect(page).not_to_have_title(re.compile(r"^\(\d+\)"))


def main():
    Path("test-results").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        inbox, scans = app.extensions["inbox"], app.extensions["document_scans"]
        # Two blank scans the worker has inspected: each waits for the instructor (pages without labels).
        first = inbox.submit({"action": "scan.receive"}, [{"filename": "Section 501.pdf", "content": blank_pdf(255)}], source="web",
                             sender="workspace", idempotency_key="badge-a", instructor_review_required=True, large_scans=True)
        inbox.submit({"action": "scan.receive"}, [{"filename": "Section 502.pdf", "content": blank_pdf(240)}], source="web",
                     sender="workspace", idempotency_key="badge-b", instructor_review_required=True, large_scans=True)
        # A finished scan keeps its stored needs_review for good and must count nowhere.
        inbox.submit({"action": "scan.receive"}, [{"filename": "finished.txt", "content": b"no pages here"}], instructor_review_required=True)
        # One unauthenticated submission waiting for review counts once.
        inbox.submit({"action": "document.archive", "target": {"level": "course", "id": "none"}, "metadata": {}},
                     [{"filename": "badge-note.txt", "content": b"Unauthenticated"}], authenticated=False)
        while scans.process_one():
            pass
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(address + "/#inbox")
                expected = current_needs(page)
                assert expected == 3, f"two scans and one submission need the instructor, Current says {expected}"
                settle_badge(page, expected)
                assert badge_number(page) == expected
                home_inbox = page.locator('#home-attention [data-need^="scan:"], #home-attention [data-need="inbox:submissions"]')
                expect(home_inbox).to_have_count(3)
                # Deleting a scan: it leaves Current, the badge, the tab title and Home together.
                version = inbox.get(first["id"])["version"]
                inbox.remove(first["id"], version, "delete", reason="badge truth test")
                expected_after = current_needs(page, expected - 1)
                settle_badge(page, expected_after)
                assert badge_number(page) == expected_after
                expect(home_inbox).to_have_count(2)
                expect(page.locator(f'#home-attention [data-need="scan:{first["id"]}"]')).to_have_count(0)
                # No other badge picked the deleted scan up.
                for view in ("grading", "exams", "response-cards", "documents", "seating"):
                    expect(page.locator(f"#nav-badge-{view}")).to_be_hidden()
                page.goto(address + "/#inbox?status=deleted")
                expect(page.locator(".inbox-job-list > *")).to_have_count(1)
                # Restoring brings it back everywhere.
                inbox.restore(first["id"])
                assert current_needs(page, expected) == expected
                settle_badge(page, expected)
                page.screenshot(path="test-results/badges-home.png", full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Badge checks passed: the Inbox badge and tab title equal Current's needs-you count, and a deleted scan counts nowhere until restored.")


if __name__ == "__main__":
    main()
