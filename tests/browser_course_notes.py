"""Course notes in a real browser (AGENTS.md "Course notes"): from the course Home's Lecture notes line, upload a notes PDF through
the page's one primary action; the sections come back grouped by chapter (one found from its heading alone), a section opens its own
PDF in the app's file viewer and a topic opens it at its page; the search filters sections and topics; a newer upload is shown first with the
earlier one listed; Remove (confirmed) and Restore only hide and show; the page fits the narrowest desktop window (1280px). Synthetic notes only."""
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import create_app
from browser_course_pages import seed_course
from test_course_notes import OUTLINE, PAGES, notes_pdf


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        seed_course(client)
        group = client.get("/api/state").json["course_groups"][0]
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{address}/#course/{group['id']}/home")
                line = page.locator(".course-notes-link")
                expect(line.get_by_role("link", name="Lecture notes, split into their sections")).to_be_visible()
                expect(line.locator("[data-notes-status]")).to_have_text("Not uploaded yet")
                line.get_by_role("link").click()
                expect(page.locator("#page-title")).to_have_text("Lecture notes")
                expect(page.locator("#page-context")).to_have_text("MATH 140 · every term")
                expect(page.get_by_role("heading", name="No lecture notes yet")).to_be_visible()

                # Upload: the header's one primary action, a PDF, saved and split at once.
                primary = page.locator("#primary-action")
                expect(primary).to_have_text("Upload notes")
                primary.click()
                dialog = page.locator("#dialog")
                dialog.get_by_label("Notes PDF").set_input_files({"name": "Test notes.pdf", "mimeType": "application/pdf", "buffer": notes_pdf(PAGES, OUTLINE)})
                dialog.get_by_role("button", name="Upload and split").click()
                expect(page.locator("#toast")).to_contain_text("Saved 4 sections, each as its own PDF.")
                expect(dialog).not_to_be_visible()
                expect(primary).to_have_text("Upload new notes")
                chapters = page.locator(".notes-chapter")
                expect(chapters.locator("h3")).to_have_text(["Chapter 1Matrices", "Chapter 2Functions"])
                expect(page.locator(".notes-section")).to_have_count(5)
                relations = page.locator(".notes-section").filter(has_text="Relations and Functions")
                link = relations.locator(".notes-section-link")
                href = link.get_attribute("href")
                expect(link).to_have_attribute("data-view-file", href)  # opens in the app's file viewer (VIEWER), not a new tab
                assert href.endswith("/parts/4/2-1-relations-and-functions.pdf"), href
                expect(relations.locator(".chip")).to_have_text("From its heading")
                expect(relations.locator(".notes-pages")).to_have_text("2 pages")
                expect(relations.get_by_role("link", name="Using Function Notation")).to_have_attribute("href", f"{href}#page=2")
                opened = page.request.get(address + href)
                assert opened.status == 200 and opened.headers["content-type"] == "application/pdf", opened.headers
                relations.get_by_role("link", name="Using Function Notation").click()
                viewer = page.locator("dialog.file-viewer")
                expect(viewer.locator("#file-viewer-title")).to_have_text("2.1 Relations and Functions")
                expect(viewer.locator(".file-viewer-head p")).to_contain_text("PDF · 2 pages")
                second = viewer.locator('[data-page="2"]')
                expect(second).to_be_in_viewport()  # the topic opens at its page
                page.wait_for_function("document.querySelector('dialog.file-viewer [data-page=\"2\"] img').naturalWidth > 0")
                viewer.get_by_role("button", name="Close the viewer").click()
                expect(viewer).to_be_hidden()
                # Where the browser shows PDFs itself (headless Chromium does not say so: it is told), the notes are Faculty's own and
                # open in the browser's PDF viewer, framed from /api/viewer/pdf at the topic's page; only that response may be framed.
                page.evaluate("Object.defineProperty(navigator, 'pdfViewerEnabled', {value: true, configurable: true})")
                with page.expect_response(lambda response: "/api/viewer/pdf?" in response.url) as framed:
                    relations.get_by_role("link", name="Using Function Notation").click()
                frame = viewer.locator("iframe.file-viewer-frame")
                expect(frame).to_have_attribute("src", re.compile(r"^/api/viewer/pdf\?route=.*&sha256=[0-9a-f]{64}#page=2$"))
                expect(viewer.get_by_role("button", name="Actual size")).to_have_count(0)  # the browser's own zoom
                answer = framed.value
                assert answer.status == 200 and answer.headers["content-type"] == "application/pdf", answer.headers
                assert answer.headers["x-frame-options"] == "SAMEORIGIN" and answer.headers["content-security-policy"] == "frame-ancestors 'self'", answer.headers
                viewer.get_by_role("button", name="Close the viewer").click()
                expect(viewer).to_be_hidden()
                expect(page.get_by_text("How the sections were found")).to_be_visible()

                # Search: sections and their topics, in any order of words; nothing found offers to clear it.
                search = page.get_by_role("searchbox", name="Find a section or topic")
                search.fill("notation function")
                expect(page.locator(".notes-section:visible")).to_have_count(1)
                expect(page.locator(".notes-chapter:visible h3")).to_have_text(["Chapter 2Functions"])
                search.fill("eigenvalues")
                expect(page.get_by_role("heading", name="No section or topic matches")).to_be_visible()
                page.get_by_role("button", name="Clear search").click()
                expect(page.locator(".notes-section:visible")).to_have_count(5)

                # A newer upload is shown first; the earlier one is listed and can be looked at.
                primary.click()
                expect(dialog).to_contain_text("The new notes replace Test notes.pdf on this page")
                dialog.get_by_label("Notes PDF").set_input_files({"name": "Shorter.pdf", "mimeType": "application/pdf", "buffer": notes_pdf(PAGES[:5], OUTLINE[:5], title="Shorter Notes")})
                dialog.get_by_role("button", name="Upload and split").click()
                expect(page.locator(".notes-edition strong")).to_have_text("Shorter Notes")
                page.get_by_text("Earlier uploads", exact=True).click()
                page.get_by_role("button", name="Show its sections").click()
                expect(page.locator(".notes-page .callout")).to_contain_text("An earlier upload")
                expect(page.locator(".notes-section")).to_have_count(5)
                page.locator(".notes-page .callout").get_by_role("button", name="Back to the current notes").click()
                expect(page.locator(".notes-edition strong")).to_have_text("Shorter Notes")

                # Remove asks first and only hides; Restore brings it back.
                page.get_by_role("button", name="More actions for these notes").click()
                page.get_by_role("menuitem", name="Remove from the course page…").click()
                expect(dialog).to_contain_text("The PDF and its sections are kept")
                dialog.get_by_role("button", name="Remove", exact=True).click()
                expect(page.locator(".notes-edition strong")).to_have_text("Test Lecture Notes")
                expect(page.locator("#toast")).to_contain_text("The notes were removed from the course page.")
                page.get_by_text("Removed uploads", exact=True).click()
                page.locator(".notes-others").get_by_role("button", name="Restore").click()
                expect(page.locator(".notes-edition strong")).to_have_text("Shorter Notes")
                expect(page.locator(".notes-chapter h3").first).to_have_text("Chapter 1Matrices")  # a short PDF's running header is found too
                page.screenshot(path=str(output / "course-notes.png"), full_page=True)

                # The course Home's line counts the shown notes' sections.
                page.goto(f"{address}/#course/{group['id']}/home")
                expect(page.locator(".course-notes-link [data-notes-status]")).to_contain_text("2 sections · uploaded")

                # The narrowest desktop window (DESIGN-LANGUAGE, 2026-10-03): no sideways scrolling.
                narrow = browser.new_page(viewport={"width": 1280, "height": 900})
                narrow.on("pageerror", lambda error: errors.append(str(error)))
                narrow.goto(f"{address}/#course/{group['id']}/home/notes")
                expect(narrow.locator(".notes-section")).to_have_count(3)
                width = narrow.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                assert width[0] <= width[1], width
                narrow.screenshot(path=str(output / "course-notes-1280.png"), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Course notes browser check passed.")


if __name__ == "__main__":
    main()
