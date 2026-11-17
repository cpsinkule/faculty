"""Files for Canvas modules in a real browser (canvas FILES; canvas/README.md "Files"): on the course's Canvas content page, add a
file to a module from the instructor's computer, save, and push: the preview names the file put in each course's Files, and the
push leaves it there with a published module item. Then a corrected file takes its place, and one section's own Canvas page adds
a file to that course alone. Synthetic data only; Canvas is the loopback stand-in."""
import io
import os
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
from canvas import web
from fake_canvas import FakeCanvas
from test_rosters import STUDENTS, csv_bytes
from test_storage import HEADERS

REVIEW = b"%PDF-1.4 review sheet " + b"x" * 2000
FIXED = b"%PDF-1.4 review sheet, corrected " + b"y" * 3000
SHOTS = os.environ.get("CANVAS_FILES_SCREENSHOTS", "")


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def main():
    with tempfile.TemporaryDirectory() as directory:
        fake = FakeCanvas().start()
        a = fake.add_course("MATH 140 507", "MATH 140 507", "Fall 2026")
        b = fake.add_course("MATH 140 508", "MATH 140 508", "Fall 2026")
        for course in (a, b):
            fake.add_file(course, "course files/Template", "banner.png")
            fake.add_module(course, "Course Materials", items=[{"type": "SubHeader", "title": "Syllabus"}])
            fake.add_module(course, "Week 1")
        app = create_app(os.path.join(directory, "data"))
        client = app.test_client()
        client.get("/").close()
        settings = web.Settings(session_file=os.path.join(directory, "config", "canvas-session.json"), base=fake.url)
        app.extensions["canvas"].settings = settings
        web.save(settings, fake.session(), user={"name": "Pat Instructor"})
        seed_course(client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        group = client.get("/api/state").json["course_groups"][0]
        for number, chosen in ((507, STUDENTS[:1]), (508, STUDENTS[1:])):
            answer = client.post(f"/api/course-groups/{group['id']}/sections/{number}/roster", data={"file": (io.BytesIO(csv_bytes(chosen)), "roster.csv")}, headers=HEADERS)
            assert answer.status_code == 201, answer.json
        term = client.get("/api/gradebook").json["terms"][0]
        sections = {s["number"]: s["id"] for s in term["sections"]}
        for number, course in ((507, a), (508, b)):
            assert client.post(f"/api/canvas/sections/{sections[number]}", json={"course_id": course, "base": ""}, headers=HEADERS).status_code == 200
        assert client.post(f"/api/canvas/content/{term['id']}/start", json={"course_id": a, "request_id": "browser-files-start", "base_revision": None},
                           headers=HEADERS).status_code == 200
        review, fixed = Path(directory) / "Review 2.pdf", Path(directory) / "Review 2 (fixed typo).pdf"
        review.write_bytes(REVIEW)
        fixed.write_bytes(FIXED)
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"

        def week(course):
            return next(m for m in fake.modules[course] if m["name"] == "Week 1")

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda d: d.accept())
                page.goto(f"{address}/#course/{group['id']}/home/canvas")
                host, dialog = page.locator("[data-canvas-content-host]"), page.locator("#dialog")
                expect(host.locator("[data-cc-savebar]")).to_contain_text("revision", timeout=20000)
                # Add a file to Week 1: "File…" among the new items opens the window; the file is kept in Faculty at once.
                host.locator("summary", has_text="Week 1").click()
                module = host.locator('[data-cc-module="1"]')
                module.locator("[data-cc-add-type]").select_option("File")
                module.get_by_role("button", name="Add item").click()
                expect(dialog.get_by_role("heading", name="Add a file")).to_be_visible()
                dialog.get_by_role("button", name="Add to the module").click()
                expect(dialog.locator(".form-error")).to_have_text("Choose a file.")
                dialog.locator('input[type="file"]').set_input_files(str(review))
                dialog.get_by_label("Folder in Canvas’s Files").fill("course files/Handouts")
                dialog.get_by_role("button", name="Add to the module").click()
                expect(dialog.locator(".form-error")).to_contain_text("Name the folder as it is inside Canvas’s Files")
                dialog.get_by_label("Folder in Canvas’s Files").fill("Handouts")
                shot(page, "1-add-a-file")
                dialog.get_by_role("button", name="Add to the module").click()
                expect(dialog).to_be_hidden(timeout=20000)
                row = module.locator(".cc-item")
                expect(row).to_have_count(1)
                expect(row.locator(".cc-content")).to_have_text("Handouts/Review 2.pdf · 2 KB")
                expect(row.locator("[data-cc-item-title]")).to_have_value("Review 2.pdf")
                expect(row.locator("[data-cc-item-published]")).to_be_checked()
                expect(row.get_by_role("link", name="Download")).to_have_attribute("href", f"/api/canvas/content/{term['id']}/files/" + client.get(f"/api/canvas/content/{term['id']}").json["files"][0]["id"])
                expect(host.locator("[data-cc-savebar]")).to_contain_text("Unsaved changes")
                assert fake.course_uploads == []  # nothing reaches Canvas before a push
                row.locator("[data-cc-item-title]").fill("Review for Exam 2")
                shot(page, "2-file-item")
                host.get_by_role("button", name="Save", exact=True).click()
                expect(host.locator("[data-cc-savebar]")).to_contain_text("Saved", timeout=20000)
                # Push to both courses: the preview names the file and the item.
                host.get_by_role("button", name="Push to Canvas…").click()
                expect(dialog.locator(".cc-course")).to_have_count(1, timeout=30000)
                dialog.locator("[data-cc-target]").nth(1).check()
                expect(dialog.locator(".cc-course")).to_have_count(2, timeout=30000)
                for index in (0, 1):
                    course = dialog.locator(".cc-course").nth(index)
                    expect(course.locator(".cc-summary")).to_have_text("1 file put in Canvas’s Files · 1 item added")
                    expect(course).to_contain_text("File Handouts/Review 2.pdf")
                shot(page, "3-push-preview")
                dialog.locator("[data-cc-push]").click()
                expect(dialog).to_be_hidden(timeout=30000)
                expect(host.locator("[data-cc-push-note]")).to_contain_text("4 changes", timeout=30000)  # a file and an item in each course
                expect(host.locator("[data-cc-push-note]")).not_to_contain_text("failure")
                for course in (a, b):
                    kept = fake.file_at(course, "course files/Handouts", "Review 2.pdf")
                    item, = week(course)["items"]
                    assert (item["type"], item["title"], item["content_id"], item["published"], kept["locked"]) == ("File", "Review for Exam 2", kept["id"], True, False), item
                assert [u["content"] for u in fake.course_uploads] == [REVIEW, REVIEW]
                # A corrected file: Replace keeps the name it has in Canvas, whatever the new file is called.
                row = host.locator('[data-cc-module="1"] .cc-item')
                row.get_by_role("button", name="Replace the file of Review for Exam 2").click()
                expect(dialog.get_by_role("heading", name="Replace the file")).to_be_visible()
                expect(dialog).to_contain_text("It keeps the name Review 2.pdf in Canvas.")
                dialog.locator('input[type="file"]').set_input_files(str(fixed))
                dialog.get_by_role("button", name="Replace", exact=True).click()
                expect(dialog).to_be_hidden(timeout=20000)
                expect(row.locator(".cc-content")).to_have_text("Handouts/Review 2.pdf · 3 KB")
                host.get_by_role("button", name="Save", exact=True).click()
                expect(host.locator("[data-cc-savebar]")).to_contain_text("Saved", timeout=20000)
                host.get_by_role("button", name="Push to Canvas…").click()
                expect(dialog.locator(".cc-course")).to_have_count(2, timeout=30000)
                expect(dialog.locator(".cc-course .cc-summary").first).to_have_text("1 file put in Canvas’s Files")
                expect(dialog).not_to_contain_text("Needs your decision")  # Faculty's own file: replaced without a question
                dialog.locator("[data-cc-push]").click()
                expect(dialog).to_be_hidden(timeout=30000)
                expect(host.locator("[data-cc-push-note]")).to_contain_text("2 changes", timeout=30000)
                for course in (a, b):
                    kept = fake.file_at(course, "course files/Handouts", "Review 2.pdf")
                    assert kept["size"] == len(FIXED) and week(course)["items"][0]["content_id"] == kept["id"]
                    assert len([f for f in fake.files[course].values() if f["display_name"] == "Review 2.pdf"]) == 1
                # A file someone else put in Canvas under that name is asked about, with both sizes.
                mine = fake.file_at(b, "course files/Handouts", "Review 2.pdf")
                del fake.files[b][mine["id"]]
                theirs = fake.add_file(b, "course files/Handouts", "Review 2.pdf", size=512)
                week(b)["items"][0]["content_id"] = theirs
                host.get_by_role("button", name="Push to Canvas…").click()
                asked = dialog.locator("[data-conflict-key]", has_text="File Handouts/Review 2.pdf")
                expect(asked).to_contain_text("Replaced in Canvas since Faculty put its file there", timeout=30000)
                expect(asked.get_by_role("button", name="Ask assistant to prepare a resolution")).to_have_count(0)
                asked.get_by_text("Compare changes", exact=True).click()
                expect(asked.locator(".cc-diff-text").nth(0)).to_have_text("3 KB")
                expect(asked.locator(".cc-diff-text").nth(1)).to_have_text("512 bytes")
                expect(asked.locator("[data-conflict-value]")).to_have_count(0)
                shot(page, "4-asked")
                expect(dialog.locator("[data-cc-push]")).to_have_text("Nothing to push")
                asked.locator("[data-cc-replace]").check()
                expect(dialog.locator("[data-cc-push]")).to_have_text("Push 1 change", timeout=30000)
                dialog.get_by_role("button", name="Cancel").click()
                assert fake.files[b][theirs]["size"] == 512  # left alone
                # One section's own Canvas page: a file for that course alone, at the top of its Files.
                page.goto(f"{address}/#course/{group['id']}/section/507/canvas")
                expect(host.locator("[data-cc-savebar]")).to_contain_text("As in Canvas", timeout=30000)
                host.locator("summary", has_text="Course Materials").click()
                module = host.locator('[data-cc-module="0"]')
                module.locator("[data-cc-add-type]").select_option("File")
                module.get_by_role("button", name="Add item").click()
                dialog.locator('input[type="file"]').set_input_files(str(review))
                dialog.get_by_role("button", name="Add to the module").click()
                expect(dialog).to_be_hidden(timeout=20000)
                expect(module.locator(".cc-item .cc-content").last).to_have_text("Review 2.pdf · 2 KB")
                module.locator("[data-cc-item-published]").last.uncheck()
                host.get_by_role("button", name="Review and push…").click()
                expect(dialog.locator(".cc-course .cc-summary")).to_have_text("1 file put in Canvas’s Files · 1 item added", timeout=30000)
                dialog.locator("[data-cc-push]").click()
                expect(host.locator("[data-cc-savebar]")).to_contain_text("As in Canvas", timeout=30000)
                top = fake.file_at(a, "course files/unfiled", "Review 2.pdf")  # where Canvas puts an upload naming no folder
                item = next(m for m in fake.modules[a] if m["name"] == "Course Materials")["items"][-1]
                assert (item["content_id"], item["published"], top["locked"]) == (top["id"], False, True), (item, top)  # unpublished, file and item
                assert fake.file_at(b, "course files/unfiled", "Review 2.pdf") is None
                expect(host.locator('[data-cc-module="0"] .cc-item .cc-content').last).to_have_text("course files/unfiled/Review 2.pdf")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"
                assert not errors, errors
                browser.close()
        finally:
            server.close()
            fake.stop()
    print("Canvas files browser check passed")


if __name__ == "__main__":
    main()
