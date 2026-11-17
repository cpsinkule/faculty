"""Browser verification of course storage transitions, using temporary directories."""

import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from test_storage import temporary_storage
from course_code_checks import fill_course_code
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server


def main():
    with tempfile.TemporaryDirectory() as directory:
        storage = temporary_storage(directory)
        server = create_server(create_app(storage=storage), host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}/#all-courses"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1080})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(address)
                expect(page.locator("#course-storage-summary")).to_contain_text("4:00 am Central time")
                page.get_by_role("button", name="Add a course", exact=True).first.click()
                page.get_by_label("Course name", exact=True).fill("Biology storage check")
                fill_course_code(page, "BIO 101")
                page.get_by_label("Term", exact=True).select_option("Fall")
                page.get_by_label("Year", exact=True).fill("2026")
                page.locator('[name="section"]').fill("1")
                page.get_by_role("button", name="Add course", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator(".course-storage")).to_contain_text("Preparing NVMe copy")
                edit = page.get_by_role("button", name="Edit Biology storage check", exact=True)
                expect(edit).to_be_disabled()
                expect(page.locator(".course-card")).to_contain_text("Storage is moving; settings return when it finishes.")
                expect(page.locator("#course-storage-summary .callout")).to_contain_text("Course storage is being updated")
                storage.reconcile_all()
                page.reload()
                # A course simply working on NVMe shows no storage line; the list shows one quiet line.
                expect(page.locator(".course-card .course-storage")).to_have_count(0)
                expect(page.locator("#course-storage-summary .storage-line")).to_contain_text("4:00 am Central time")
                course = storage.rows()[0]
                with storage.course_files(course["id"], write=True) as path:
                    (path / "roster.csv").write_text("name\nStudent\n")
                storage.backup_all()
                page.reload()
                expect(page.locator("#course-storage-summary")).to_contain_text("Last backup verified")
                expect(page.locator("#course-storage-summary .callout")).to_have_count(0)
                edit.click()
                page.get_by_label("Current course", exact=True).uncheck()
                expect(page.locator("#current-course-hint")).to_contain_text("verified before the NVMe copy is removed")
                page.get_by_role("button", name="Save changes", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                expect(page.locator(".course-storage")).to_contain_text("Copying and verifying")
                with patch.object(storage, "snapshot", side_effect=OSError("Test HDD unavailable")):
                    storage.reconcile_all()
                page.reload()
                expect(page.locator("#course-storage-summary")).to_contain_text("Course storage needs attention")
                expect(page.locator(".course-storage")).to_contain_text("Test HDD unavailable")
                assert storage.hot_path(course["id"]).exists()
                storage.reconcile_all()
                page.reload()
                expect(page.locator(".course-storage")).to_contain_text("Stored on HDD")
                expect(page.locator(".course-storage")).to_contain_text("This course’s HDD copy verified")
                assert not storage.hot_path(course["id"]).exists()
                # A course marked not current moves to the "Terms not current" group of All courses, which opens to show it.
                expect(page.locator("details.course-other-terms")).to_have_attribute("open", "")
                edit.click()
                page.get_by_label("Current course", exact=True).check()
                page.get_by_role("button", name="Save changes", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                storage.reconcile_all()
                page.reload()
                expect(page.locator(".course-card .course-storage")).to_have_count(0)
                expect(edit).to_be_enabled()
                with storage.course_files(course["id"]) as path:
                    assert (path / "roster.csv").read_text() == "name\nStudent\n"
                output = Path("test-results")
                output.mkdir(exist_ok=True)
                page.screenshot(path=str(output / "storage-desktop.png"), full_page=True)
                # Desktop only (DESIGN-LANGUAGE, 2026-10-03): the narrowest desktop window does not scroll sideways.
                page.set_viewport_size({"width": 1280, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / "storage-1280.png"), full_page=True)
                assert not errors, errors
                browser.close()
                print("Storage browser checks passed: pending, backup, failure, archive, restore, 1280px desktop.")
        finally:
            server.close()


if __name__ == "__main__":
    main()
