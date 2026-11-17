"""Attach in the Mail page's compose window, in a real browser (requirement, 2026-10-01). A file from the computer and a file from Faculty's picker are listed, one can be
removed, the Faculty file opens in the app's file viewer (VIEWER: its pages, Download, Close) rather than downloading, the review names
both, and Send delivers exactly those files in that order. The picker's listing is a route stub naming a
real Faculty file route; Gmail is the fake from test_gmail_mail; nothing reaches Google."""
import sys
import tempfile
import threading
from pathlib import Path

from flask import Response
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gmail_mail import ACCOUNT, FakeGmail, FakeTokens, connected_gmail_app, message

def two_pages():
    import io
    from PIL import Image, ImageDraw
    pages = [Image.new("L", (850, 1100), 255) for _ in range(2)]
    for number, page in enumerate(pages, 1):
        ImageDraw.Draw(page).text((80, 80), f"Limits, page {number}", fill=0)
    buffer = io.BytesIO()
    pages[0].save(buffer, "PDF", save_all=True, append_images=pages[1:], resolution=100)
    return buffer.getvalue()


NOTES = two_pages()
SYLLABUS = b"%PDF-1.4\n% syllabus\n%%EOF\n"


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = connected_gmail_app(directory)
        # A Faculty file route for the picker's item (the attachment is read through it at the preview and the send).
        app.add_url_rule("/api/test-files/limits.pdf", "test_files.limits",
                         lambda: Response(NOTES, mimetype="application/pdf", headers={"Content-Disposition": 'inline; filename="1.2 Limits.pdf"'}))
        app.test_client().get("/").close()
        mail = app.extensions["gmail_mail"]
        google = FakeGmail()
        mail.client.http, mail.google.http = google, FakeTokens()
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        asked = []
        syllabus = Path(directory) / "Syllabus.pdf"
        syllabus.write_bytes(SYLLABUS)
        stray = Path(directory) / "stray.txt"
        stray.write_text("not this one")

        def files(route):
            asked.append(route.request.url.split("?", 1)[1])
            route.fulfill(json={"kind": "notes", "note": "", "groups": [{"label": "MATH 140 · Calculus notes", "items": [
                {"route": "/api/test-files/limits.pdf", "name": "1.2 Limits.pdf", "title": "1.2 Limits", "size": len(NOTES), "detail": "pages 3–7"}]}]})

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/mail/threads?*", lambda route: route.fulfill(json={"threads": [], "next": "", "tab": "students", "query": ""}))
                page.route("**/api/mail/files?*", files)
                page.route("**/api/mail/sections", lambda route: route.fulfill(json={"sections": [
                    {"group_id": "first", "number": 501, "code": "MATH 140", "term_label": "Fall", "students": 1},
                    {"group_id": "second", "number": 502, "code": "MATH 140", "term_label": "Fall", "students": 2},
                ]}))
                audiences = []
                page.route("**/api/mail/audience?*", lambda route: audiences.append(route))
                page.goto(f"{address}/#mail?compose=1")
                compose = page.locator("form.mail-compose")
                expect(compose).to_be_visible()
                # MAIL: a slower answer for the previous section must never supply the chosen section's recipients.
                compose.locator("details summary").click()
                section = compose.locator('[name="audience-section"]')
                expect(section.locator("option")).to_have_count(3)
                section.select_option("0")
                page.wait_for_function("document.querySelector('[data-mail-add-students]').disabled")
                section.select_option("1")
                for _ in range(100):
                    if len(audiences) == 2:
                        break
                    page.wait_for_timeout(20)
                assert len(audiences) == 2, "both audience reads must be held"
                audiences[1].fulfill(json={"students": [{"email": "second-a@tamu.edu"}, {"email": "second-b@tamu.edu"}], "tas": [], "skipped": []})
                expect(compose.locator("[data-mail-add-students]")).to_have_text("Add 2 students to Bcc")
                audiences[0].fulfill(json={"students": [{"email": "first@tamu.edu"}], "tas": [], "skipped": []})
                page.wait_for_timeout(100)
                expect(compose.locator("[data-mail-add-students]")).to_have_text("Add 2 students to Bcc")
                compose.locator("[data-mail-add-students]").click()
                expect(compose.locator('[name="bcc"]')).to_have_value("second-a@tamu.edu, second-b@tamu.edu")
                compose.locator('[name="bcc"]').fill("")
                compose.locator('input[name="to"]').fill("lee@tamu.edu")
                compose.locator('input[name="subject"]').fill("Notes for Thursday")
                compose.locator('textarea[name="body"]').fill("Both files are attached.")
                compose.locator("[data-mail-attach-file]").set_input_files([str(syllabus), str(stray)])
                attached = compose.locator(".mail-attached li")
                expect(attached).to_have_count(2)
                attached.nth(1).get_by_role("button", name="Remove stray.txt").click()
                expect(attached).to_have_count(1)
                expect(attached.first).to_contain_text("Syllabus.pdf")
                expect(attached.first).to_contain_text("from your computer")

                compose.get_by_role("button", name="Attach from Faculty…").click()
                compose.get_by_role("button", name="Lecture notes").click()
                expect(compose.locator(".mail-library-items li")).to_contain_text("1.2 Limits")
                compose.locator("[data-mail-pick]").click()
                expect(compose.locator("[data-mail-pick]")).to_have_text("Attached")
                expect(attached).to_have_count(2)
                expect(attached.nth(1).get_by_role("link", name="1.2 Limits.pdf")).to_have_attribute("href", "/api/test-files/limits.pdf")
                # A Faculty file opens in the app's viewer (VIEWER), over the compose window, never as a download.
                downloads = []
                page.on("download", lambda item: downloads.append(item))
                attached.nth(1).get_by_role("link", name="1.2 Limits.pdf").click()
                viewer = page.locator("dialog.file-viewer")
                expect(viewer).to_be_visible()
                expect(viewer.locator("#file-viewer-title")).to_have_text("1.2 Limits.pdf")
                expect(viewer.locator(".file-viewer-head p")).to_contain_text("PDF · 2 pages")
                pages = viewer.locator("figure.file-viewer-page img")
                expect(pages).to_have_count(2)
                page.wait_for_function("[...document.querySelectorAll('figure.file-viewer-page img')].every(img => img.complete && img.naturalWidth > 0)")
                expect(viewer.get_by_role("link", name="Download")).to_have_attribute("href", "/api/test-files/limits.pdf")
                viewer.get_by_role("button", name="Actual size").click()
                expect(viewer.get_by_role("button", name="Fit to window")).to_have_attribute("aria-pressed", "true")
                page.screenshot(path=str(output / "file-viewer-pdf.png"))
                viewer.get_by_role("button", name="Close the viewer").click()
                expect(viewer).to_be_hidden()
                expect(compose).to_be_visible()  # the compose window is still there, as it was
                assert downloads == [], downloads
                assert any(item.startswith("kind=papers&q=lee%40tamu.edu") for item in asked), asked  # papers start from the To address
                assert any(item.startswith("kind=notes") for item in asked), asked
                page.screenshot(path=str(output / "mail-compose-attach.png"))
                page.set_viewport_size({"width": 1280, "height": 1000})  # a laptop's width: nothing scrolls sideways
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "the page scrolls sideways at 1280 px"
                attached.first.scroll_into_view_if_needed()
                page.screenshot(path=str(output / "mail-compose-attach-1280.png"))
                page.set_viewport_size({"width": 1440, "height": 1000})

                compose.get_by_role("button", name="Review").click()
                review = page.locator("form.mail-review")
                expect(review).to_contain_text("Syllabus.pdf")
                expect(review).to_contain_text("1.2 Limits.pdf")
                expect(review).to_contain_text("2 attachments")
                expect(review.get_by_role("button", name="Remove")).to_have_count(0)
                page.screenshot(path=str(output / "mail-review-attach.png"))
                review.get_by_role("button", name="Send to 1 recipient").click()
                expect(page.get_by_text("Sent to 1 recipient.")).to_be_visible()
                expect(review).to_be_hidden()  # the closed dialog keeps its form, unseen
                assert len(google.sent) == 1, google.sent
                sent = [(part.get_filename(), part.get_content()) for part in google.parsed().iter_attachments()]
                assert sent == [("Syllabus.pdf", SYLLABUS), ("1.2 Limits.pdf", NOTES)], sent
                # A Mail thread's attachment opens in the viewer, fetched from Gmail when opened (MAIL-ATTACHMENTS-READ).
                google.threads["d4"] = [message("abc123", "Ann Lee <lee@tamu.edu>", ACCOUNT, "My note", "Attached.", files=[("doctor note.pdf", len(NOTES))],
                                                extra=[("Message-ID", "<d4@mail.example>")])]
                google.attachments["att"] = NOTES
                page.unroute("**/api/mail/threads?*")  # that pattern also matches /threads/<id>: the fake Gmail answers now
                page.goto(f"{address}/#mail?thread=d4")
                note = page.get_by_role("link", name="doctor note.pdf")
                expect(note).to_have_attribute("data-view-file", "/api/mail/messages/abc123/parts/1")
                note.click()
                viewer = page.locator("dialog.file-viewer")
                expect(viewer.locator("#file-viewer-title")).to_have_text("doctor note.pdf")
                expect(viewer.locator("figure.file-viewer-page")).to_have_count(2)  # a sender's file: pictures of its pages
                expect(viewer.get_by_role("link", name="Download")).to_have_attribute("href", "/api/mail/messages/abc123/parts/1")
                viewer.get_by_role("button", name="Close the viewer").click()
                assert downloads == [], downloads
                # FRONTEND: a preview started in a compose window cannot replace a later window after Cancel.
                held_preview = []
                page.route("**/api/mail/preview", lambda route: held_preview.append(route))
                page.goto(f"{address}/#mail?compose=1")
                expect(compose).to_be_visible()
                compose.locator('[name="to"]').fill("lee@tamu.edu")
                compose.locator('[name="subject"]').fill("Cancelled preview")
                compose.locator('[name="body"]').fill("Do not reopen this preview.")
                compose.get_by_role("button", name="Review", exact=True).click()
                for _ in range(100):
                    if held_preview:
                        break
                    page.wait_for_timeout(20)
                assert held_preview, "the preview read must be held"
                # The header's Close stays available while the form's Review request is in flight.
                page.locator("#dialog").get_by_role("button", name="Close dialog", exact=True).click()
                expect(page.locator("#dialog")).not_to_be_visible()
                page.locator("#primary-action").click()
                expect(compose).to_be_visible()
                compose.locator('[name="subject"]').fill("New draft stays here")
                held_preview[0].continue_()
                page.wait_for_timeout(300)
                expect(compose.locator('[name="subject"]')).to_have_value("New draft stays here")
                expect(page.locator("form.mail-review")).to_have_count(0)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("mail compose attach ok")


if __name__ == "__main__":
    main()
