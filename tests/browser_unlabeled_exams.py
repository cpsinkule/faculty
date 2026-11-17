"""The scan page's panel for exams printed without labels, with scripted OCR: read, choose a student, register. No GPU or real data."""
import sys
import re
import threading
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from document_identity import unlabeled
from bubble_fixture import other_page, pdf, sheet
from test_unlabeled_exams import KEY, UnlabeledExams, cover_text

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = UnlabeledExams("test_covers_are_read_matched_and_registered_as_their_students_paper")
fixture.setUp()
white = Image.new("L", (1700, 2200), 255)
job = fixture.submit(pdf([other_page("cover a"), other_page("q1 a"), other_page("q2 a"), sheet(KEY), white,
                          other_page("cover b"), other_page("q1 b"), other_page("q2 b")]))
message = job["message_id"]
fixture.texts = {1: cover_text("Alice Example", "123456789"), 6: cover_text("<img src=x onerror=alert(1)>", "")}
fixture.titles = ["Common Exam I Version A, Fall 2026", "Common Exam I Version C, Fall 2026"]
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
try:
    with sync_playwright() as playwright, patch("faculty_ocr.available", return_value=True):
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1300, "height": 1000})
        page.set_default_timeout(15000)
        errors, dialogs = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        # Goal first (requirement, 2026-09-26): no page of this scan carries a label, so its Scan page leads with the next step and a
        # button to it, shows the first pages small, and folds the page-by-page readings (still all there).
        page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{job['id']}/pages")
        next_step = page.locator("[data-scan-next-step]")
        expect(next_step).to_contain_text("Next step ·")
        expect(next_step.get_by_role("link")).to_have_attribute("href", re.compile(rf"#inbox/job/{job['id']}/"))
        first = page.locator("[data-scan-first-look] img").first
        expect(first).to_be_visible()
        assert first.evaluate("image => image.decode().then(() => image.naturalWidth)") > 0
        expect(page.locator("[data-scan-all-pages]")).not_to_have_attribute("open", "")
        expect(page.locator(".document-scan-page")).to_have_count(0)  # drawn only while the fold is open
        expect(page.locator("[data-scan-pages-attention]")).to_have_count(0)  # unread labels here are the Students stage's
        page.locator("[data-scan-all-pages] > summary").click()
        expect(page.locator(".document-scan-page")).to_have_count(8)
        page.screenshot(path=str(OUTPUT / "scan-page-without-labels.png"), full_page=True)
        page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{job['id']}")
        # The scan is a card with its flow graph (requirement, 2026-09-24); its Students stage opens the page that finds first pages.
        card = page.locator("[data-scan-card]")
        expect(card.locator("[data-pipeline-step='students'] a")).to_have_attribute("title", re.compile("not bubble sheets"))
        card.locator("[data-pipeline-step='students'] a").click()
        expect(page).to_have_url(re.compile(rf"#inbox/job/{job['id']}/students$"))
        settings = page.locator("[data-unlabeled-settings]")
        expect(settings).to_have_attribute("open", "")
        form = settings.locator("form[data-unlabeled]")
        form.get_by_label("Scanned sides per paper").fill("3")
        # Never the first assignment in sort order: with none suggested, the instructor chooses it (inbox-5).
        assignment = form.get_by_label("Assignment")
        expect(assignment).to_have_value("")
        expect(form.get_by_role("button", name="Find first pages")).not_to_have_class(re.compile(r"\bprimary\b"))  # the banner's is
        form.get_by_role("button", name="Find first pages").click()
        expect(page.locator("[data-unlabeled-status]")).to_have_count(0)  # the required choice holds the request back
        assignment.select_option(label="MATH 140 · Test 1")
        form.get_by_role("button", name="Find first pages").click()
        expect(page.locator("[data-unlabeled-status]")).to_contain_text("Looking for first pages for Test 1")
        expect(page.get_by_role("button", name="Stop looking for first pages")).to_be_visible()
        # What the run looks for is said as facts beside its progress.
        expect(page.locator("[data-unlabeled-facts] .chip")).to_have_text(["Test 1", "3 sides per paper"])
        # Stopped, the page says so and what it got through, with Continue; it never claims every paper is matched. The scan is not
        # filed under an assignment yet, so the next step's banner (Assignment) holds the page's one primary and Continue waits
        # beside it as an ordinary button (inbox-5).
        page.get_by_role("button", name="Stop looking for first pages").click()
        stopped = page.locator(".students-run-stopped")
        expect(stopped).to_contain_text("0 pages read · 0 first pages found")
        expect(page.locator("[data-students-page] .button.primary")).to_have_text(["Assignment →"])
        expect(stopped.get_by_role("button", name="Continue looking for first pages")).to_be_visible()
        expect(page.locator("[data-students-page]")).not_to_contain_text("No paper needs you")
        expect(page.locator("[data-students-page]").get_by_role("link", name=re.compile("Review matched papers"))).to_have_count(0)
        stopped.get_by_role("button", name="Continue looking for first pages").click()
        expect(page.locator("[data-unlabeled-status]")).to_contain_text("Looking for first pages for Test 1")
        fixture.batches = []
        with patch("faculty_ocr.recognize_covers", side_effect=fixture.covers), patch.object(unlabeled.UnlabeledService, "_likeness", return_value=0.0):
            for _ in range(10):
                fixture.scans.unlabeled.process_one()
        page.get_by_role("button", name="Refresh page review").click()
        students = page.locator("[data-students-page]")
        rows = students.locator("tr[data-unlabeled-row]")
        expect(rows).to_have_count(1)
        expect(students).to_contain_text("Who wrote these?")
        expect(rows.nth(0)).to_contain_text("Check who wrote it")
        expect(rows.nth(0)).to_contain_text("<img src=x onerror=alert(1)>")  # What was read is text, never markup.
        expect(students.get_by_role("button", name="Register 1 identified paper")).to_be_enabled()
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.screenshot(path=str(OUTPUT / "unlabeled-students-1300.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        students.get_by_role("link", name=re.compile(r"^Review matched papers \(1\)")).click()
        expect(page).to_have_url(re.compile(rf"#inbox/job/{job['id']}/students/matched$"))
        matched = page.locator("[data-students-page]")
        expect(matched).to_contain_text("Identified, not registered yet")
        match_rows = matched.locator("tr[data-unlabeled-row]")
        expect(match_rows).to_have_count(1)
        expect(match_rows.nth(0)).to_contain_text("Identified by an exact roster UIN.")
        # The cover loads lazily: wait (bounded) until it has loaded with real pixels instead of reading its state at once. The wait
        # polls from the test: the app's CSP (script-src 'self') stops wait_for_function's in-page polling, so a predicate not yet
        # true at its first look failed at once instead of waiting.
        cover = match_rows.nth(0).locator(".document-unlabeled-cover img")
        cover.scroll_into_view_if_needed()
        expect(cover).to_have_js_property("complete", True, timeout=10000)
        assert cover.evaluate("image => image.naturalWidth") > 0
        page.screenshot(path=str(OUTPUT / "unlabeled-matched-1300.png"), full_page=True)
        # A click enlarges the first page in the grading pages' crop viewer (requirement, 2026-10-02).
        matched.get_by_role("button", name=re.compile(r"^Enlarge Top of the first page")).click()
        zoom = page.locator("dialog.grading-crop-dialog")
        expect(zoom).to_be_visible()
        expect(zoom.locator("img")).to_have_js_property("complete", True, timeout=10000)
        assert zoom.locator("img").evaluate("image => image.naturalWidth") > 0
        zoom.get_by_role("button", name="Close").click()
        expect(zoom).to_have_count(0)
        # The student picker is a search box (requirement, 2026-09-24): a name typed narrows the roster, in any word order. A choice
        # can be taken back in the same control: back to the reading, the box says "As read" again. A match corrected here stays here.
        def pick(row, typed, option):
            box = row.get_by_role("combobox")
            box.click()
            expect(box).to_have_value("")  # Opening the box empties it for typing (requirement, 2026-09-24).
            box.fill(typed)
            row.get_by_role("option", name=option).click()
        pick(match_rows.nth(0), "bob exam", re.compile(r"^Example, Bob · section 501"))
        expect(match_rows.nth(0)).to_contain_text("You chose this student.")
        pick(match_rows.nth(0), "as read", re.compile(r"^As read"))
        expect(match_rows.nth(0)).to_contain_text("Identified by an exact roster UIN.")
        expect(match_rows.nth(0).get_by_role("combobox")).to_have_value(re.compile(r"^As read: Example, Alice"))
        # Escape puts back what was chosen; typing never saves anything by itself.
        box = match_rows.nth(0).get_by_role("combobox")
        box.click()
        box.fill("nguyen")
        expect(match_rows.nth(0).get_by_role("option")).to_have_text([re.compile(r"^Nguyen, Carol · section 501")])
        box.press("Escape")
        expect(box).to_have_value(re.compile(r"^As read: Example, Alice"))
        # Clicked again: empty for typing, the current choice greyed as its placeholder, and put back when nothing is chosen.
        box.click()
        expect(box).to_have_value("")
        expect(box).to_have_attribute("placeholder", re.compile(r"^As read: Example, Alice"))
        box.press("Escape")
        expect(box).to_have_value(re.compile(r"^As read: Example, Alice"))
        expect(box).to_have_attribute("placeholder", "Type a name, section or UIN…")
        # Back to the papers that need a name: the instructor names the student for the unreadable cover by typing the UIN written
        # on it. The server finds the closest roster UINs and sends back names and sections only. It stays in view, as their choice.
        matched.get_by_role("link", name=re.compile(r"Papers that need you \(1\)")).click()
        expect(page).to_have_url(re.compile(rf"#inbox/job/{job['id']}/students$"))
        pick(rows.nth(0), "223456780", re.compile(r"^Example, Bob · section 501 UIN 100%"))
        expect(students).to_contain_text("No paper needs you.")
        expect(students).to_contain_text("Chosen by you, not registered yet")
        expect(rows.nth(0)).to_contain_text("You chose this student.")
        # Both papers are then registered, and the matches say so.
        students.get_by_role("button", name="Register 2 identified papers").click()
        expect(rows).to_have_count(0)
        students.get_by_role("link", name=re.compile(r"^Review matched papers \(2\)")).click()
        expect(match_rows).to_have_count(2)
        expect(match_rows.nth(0)).to_contain_text("Waiting for its label")
        assert fixture.documents.build_one()
        assert fixture.scans.unlabeled.process_one()
        page.reload()
        expect(match_rows.nth(0)).to_contain_text("Registered")
        expect(match_rows.nth(1)).to_contain_text("Registered")
        page.get_by_role("link", name="Back to scan").click()
        expect(page).to_have_url(re.compile(rf"#inbox/job/{job['id']}$"))
        students_stage = page.locator("[data-scan-card] [data-pipeline-step='students']")
        expect(students_stage).to_have_class("is-done")
        expect(students_stage.locator("a")).to_have_attribute("title", re.compile("registered to their students"))
        assert not errors, errors
        assert not dialogs, dialogs
        browser.close()
        print("Unlabeled exams browser passed: request, scripted reading, Find students and Matched papers pages, escaped OCR text, cover images, chosen students, registration, grouping, no sideways scroll on the desktop.")
finally:
    server.close()
    fixture.doCleanups()
