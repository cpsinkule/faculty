"""Real review UI for printed-version sorting with a controlled OCR result; no GPU or production data."""
import io
import json
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader, PdfWriter
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_course_pages import seed_course
from teach_inbox.service import sha


def saved_from_viewer(page, link):
    """Open a file in the app's file viewer (VIEWER) and save it with the viewer's Download: the file's own bytes."""
    link.click()
    viewer = page.locator("dialog.file-viewer")
    expect(viewer.get_by_role("link", name="Download")).to_be_visible()
    with page.expect_download() as capture:
        viewer.get_by_role("link", name="Download").click()
    viewer.get_by_role("button", name="Close the viewer").click()
    return Path(capture.value.path()).read_bytes()


def main():
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(temporary); seed_course(app.test_client(), sections=[507], multiple_sections=False)
        scans = app.extensions["document_scans"]; inbox = app.extensions["inbox"]; sorter = scans.version_sort
        fixtures = Path(__file__).resolve().parents[1] / "document_identity/fixtures"
        letter = PdfReader(fixtures / "letter-corners.pdf").pages[0]
        writer = PdfWriter(); writer.add_metadata({"/Title": uuid.uuid4().hex})
        for index in range(1, 5):  # front, blank back, front, blank back
            writer.add_page(letter) if index % 2 else writer.add_blank_page(width=612, height=792)
        data = io.BytesIO(); writer.write(data)
        job = inbox.submit({"action": "scan.receive"}, [{"filename": "quizzes.pdf", "content": data.getvalue()}],
                           source="email", sender="scanner", instructor_review_required=True)
        assert scans.process_one()
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        try:
            with patch.object(sorter, "runtime_available", return_value=True), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1200, "height": 1000}, accept_downloads=True)
                errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{job['id']}")
                # The scan is a card with its flow graph and one clear next step (requirement, 2026-09-24).
                card = page.locator("[data-scan-card]")
                # Its one button names the goal of the stage that needs the instructor first: nothing printed says what these pages
                # are, so that is choosing the assignment (the button's words are the stage's own, as on the next-step banner).
                expect(card.locator("[data-scan-next-action]")).to_have_count(1)
                expect(card.locator("[data-scan-next-action]")).to_have_text("Choose the assignment →")
                expect(card.locator("[data-scan-next-action]")).to_have_attribute("href", re.compile(r"/assignment$"))
                output = Path("test-results")
                output.mkdir(exist_ok=True)
                # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.screenshot(path=str(output / "scan-review-1440.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                card.locator("[data-scan-next-action]").scroll_into_view_if_needed()
                expect(card.locator("[data-scan-next-action]")).to_be_visible()
                page.set_viewport_size({"width": 1200, "height": 1000})
                # The Scan stage's page holds the sort, folded until asked for; the graph on top marks where it is.
                card.locator("[data-pipeline-step='scan'] a").click()
                expect(page).to_have_url(re.compile(r"/pages$"))
                # A stage page that is not the current stage leads with the next step, in the same words as the card's button.
                expect(page.locator("[data-scan-next-step] .next-step-title")).to_have_text("Choose the assignment")
                expect(page.locator("[data-scan-flow] [data-pipeline-step='scan'] a")).to_have_attribute("aria-current", "step")
                panel = page.locator(".document-version-sort")
                expect(panel).to_contain_text("Sort by printed version")
                assert not page.locator("[data-version-sort-panel]").evaluate("node => node.open")
                # No page carries a label, so the sort is one of the "Other options" (goal first, 2026-09-26), folded until asked for.
                other = page.locator("[data-scan-other-options]")
                assert not other.evaluate("node => node.open")
                other.locator(":scope > summary").click()
                panel.locator(":scope > summary").focus()
                panel.locator(":scope > summary").press("Enter")
                expect(page.locator("[data-version-sort-panel]")).to_have_attribute("open", "")
                form = panel.locator("form[data-version-sort]")
                expect(form.get_by_label("Custom pattern (one capture group, case-insensitive)")).to_be_hidden()
                form.get_by_label("Printed label").select_option("custom")
                expect(form.get_by_label("Custom pattern (one capture group, case-insensitive)")).to_be_visible()
                form.get_by_label("Printed label").select_option("version-letter")
                form.get_by_label("Pages per quiz").fill("2")
                expect(form.get_by_label("Auto-rotate upside-down pages")).to_be_checked()
                form.get_by_role("button", name="Queue version sorting").click()
                expect(panel.locator("[data-version-sort-status]")).to_have_text("Queued for sorting · position 1 of 1 · 4 pages.")
                # The settings stay open through the queued re-render; the button is disabled while the sort is live.
                expect(panel.get_by_text("Sort again with different settings", exact=True)).to_be_visible()
                expect(panel.get_by_role("button", name="Queue version sorting")).to_be_disabled()
                panel.locator("form[data-version-sort]").get_by_label("Pages per quiz").fill("3")
                page.wait_for_timeout(6000)  # one live-polling re-render while still queued
                expect(panel.locator("[data-version-sort-status]")).to_have_text("Queued for sorting · position 1 of 1 · 4 pages.")
                expect(panel.locator("form[data-version-sort]").get_by_label("Pages per quiz")).to_have_value("3")  # typed values survive polling
                texts = iter([("Quiz 3   Version A  <img src=x onerror=alert(1)>", 0), ("VERSION: B", 180)])  # second sheet fed upside down
                def recognize(content, *, band):
                    assert band == "top"
                    text, rotation = next(texts)
                    return {"source_sha256": sha(content), "model_id": "browser-test-double", "revision": "test", "device": "cuda",
                            "seconds": 0.1, "crop_profile": "faculty-version-band-v1", "needs_review": True,
                            "reads": [{"rotation": 0, "band": band, "text": text if rotation == 0 else "", "truncated": False},
                                      {"rotation": 180, "band": band, "text": text if rotation == 180 else "", "truncated": False}]}
                with patch("document_identity.version_sort.recognize_bands", side_effect=recognize):
                    while inbox.work_once():
                        pass
                # The panel polls while a sort is live, so the finished result appears without a click.
                expect(panel.locator("[data-version-sort-status]")).to_have_text("Sorted 4 pages into 2 versions; 0 unsorted; 2 turned right way up.", timeout=15000)
                outputs = panel.locator(".document-version-outputs li")
                expect(outputs).to_have_count(2)
                expect(outputs.nth(0)).to_contain_text("Version A · 2 pages · 1 labeled")
                expect(outputs.nth(1)).to_contain_text("Version B · 2 pages · 1 labeled")
                # New artifacts refresh the whole job view; what the instructor opened and typed is kept.
                expect(panel.get_by_role("button", name="Queue version sorting")).to_be_enabled()
                expect(panel.locator("form[data-version-sort]").get_by_label("Pages per quiz")).to_have_value("3")
                expect(page.locator("[data-version-sort-panel]")).to_have_attribute("open", "")  # the whole job refresh also retains the disclosure
                expect(panel.get_by_role("button", name="Retry version sorting")).to_have_count(0)
                panel.get_by_text("Page-by-page readings", exact=True).click()
                rows = panel.locator(".document-version-pages tbody tr")
                expect(rows).to_have_count(4)
                expect(rows.nth(1)).to_contain_text("Blank side following the labeled page.")
                expect(rows.nth(0)).to_contain_text("As scanned")
                expect(rows.nth(2)).to_contain_text("Rotated 180° (from its label)")
                expect(rows.nth(3)).to_contain_text("Rotated 180° (with its quiz)")
                assert not page.locator(".document-version-sort img").count()
                assert len(PdfReader(io.BytesIO(saved_from_viewer(page, outputs.nth(0).get_by_role("link", name="PDF", exact=True)))).pages) == 2
                report = json.loads(saved_from_viewer(page, panel.get_by_role("link", name="Version sort report", exact=True)))
                assert report["pages"][0]["reads"][0]["text"].startswith("Quiz 3   Version A")
                assert report["instructor_review_required"] is True
                assert [p["assigned"] for p in report["pages"]] == ["A", "A", "B", "B"]
                assert [p["rotation"] for p in report["pages"]] == [0, 0, 180, 180]
                assert [p.rotation for p in PdfReader(io.BytesIO(saved_from_viewer(page, outputs.nth(1).get_by_role("link", name="PDF", exact=True)))).pages] == [180, 180]
                assert inbox.get(job["id"])["status"] == "needs_review"
                assert all(p["page_stamp_id"] is None for p in scans.review_item(job["message_id"])["pages"])
                for width in (1280, 1200):  # desktop only (DESIGN-LANGUAGE): nothing below 1180 px is laid out any more
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"version sort overflow at {width}"
                Path("test-results").mkdir(exist_ok=True)
                page.screenshot(path="test-results/version-sort-1200.png", full_page=True)
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{job['id']}")
                expect(page.locator("[data-scan-card] [data-pipeline-step=\"review\"] a")).to_have_attribute("aria-label", re.compile(r"^Review: (Not started|Needs you)\."))
                # A scan's files, command and history wait behind one disclosure on its own page, closed until asked for.
                record = page.locator("[data-inbox-record]")
                expect(record.locator("> summary")).to_have_text("Files, submission details and history")
                assert not record.evaluate("node => node.open")
                record.locator("> summary").click()
                group = page.locator('[data-inbox-file-group="sorted"]')
                expect(group).to_be_visible()
                expect(group).to_contain_text("3 files")
                # Filed under an assignment, pages without labels are that assignment's papers: the Assignment page says so and
                # names the assignment once, and the page header gains the course.
                client, headers = app.test_client(), {"X-Teaching-Request": "1", "Origin": "http://localhost"}
                state = client.get(f"/api/grading/scans/{job['message_id']}").json
                catalog = client.get("/api/grading/catalog").json
                term = catalog["terms"][0]
                quiz = app.extensions["documents"].create_assignment(term["definition_id"], "quiz-3", "Quiz 3")
                section = next(item for item in catalog["sections"] if item["term_id"] == term["id"])
                saved = client.post(f"/api/grading/scans/{job['message_id']}/associate", headers=headers, json={
                    "request_id": str(uuid.uuid4()), "fingerprint": state["fingerprint"], "term_id": term["id"],
                    "assignment_id": quiz["id"], "section_ids": [section["id"]]})
                assert saved.status_code == 200, saved.json
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{job['id']}/assignment")
                content = page.locator("[data-scan-content]")
                expect(content.locator(".scan-verdict")).to_have_text("No label says what these pages are. They are Quiz 3's papers, found from their first pages on the Students page.")
                expect(content.locator(".scan-association")).to_contain_text("Assignment: Quiz 3")
                expect(content).not_to_contain_text("Assignment: Quiz 3.")  # said once, by the association line
                expect(content).not_to_contain_text("cannot tell which assignment they belong to")
                # Filed, the scan opens in Quiz 3's shell (ASSIGNMENT-WORKSPACE): the page title is the assignment and the trail ends in
                # its Collect area; the browser tab still names the stage and the scan, and the graph says which stage this is.
                expect(page.locator("#page-title")).to_have_text("Quiz 3")
                expect(page.locator("#page-context")).to_contain_text("MATH 140 · Fall 2026")
                expect(page.locator("#breadcrumb li")).to_have_text(["Home", "Assignments", "Quiz 3", "Collect"])
                expect(page).to_have_title(re.compile(r"^(\(\d+\) )?Assignment · Quiz 3 · papers without labels · "))
                expect(page.locator("[data-scan-flow-position]")).to_contain_text(re.compile(r"Stage \d of \d · Assignment"))
                # Its Review page lists the earlier stages still open, each a link to its page, above the Finish button. Since 19d8a3d
                # only whole-scan steps hold the review; flagged papers stay behind on their own, and the line says so.
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{job['id']}/review")
                still = page.locator("[data-scan-still-open]")
                expect(still).to_contain_text(re.compile(r"^Flagged papers stay in: "))
                expect(still.get_by_role("link", name="Students")).to_have_attribute("href", f"#inbox/job/{job['id']}/students")
                expect(page.locator("[data-scan-content]").get_by_role("button", name="Finish review & open grading")).to_be_enabled()
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Version sorting request, queue status, live progress, downloads, escaping, review gate and desktop layout checks passed.")


if __name__ == "__main__":
    main()
