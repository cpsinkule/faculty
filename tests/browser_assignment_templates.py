"""Template uploads, explicit mapping revisions and reviewed multi-page crops.

Synthetic course/scans only. Native template conversion uses the configured
bounded document process; run with NVMe TMPDIR and an NVMe working directory.
"""
import copy
import io
import sys
import threading
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright
from reportlab.pdfgen import canvas
from waitress.server import create_server

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from document_identity.scans import ScanService
from test_exam_grading import ExamGradingTests, HEADERS
from test_template_conversion import docx_bytes


def blank_paper():
    output = io.BytesIO()
    paper = canvas.Canvas(output, pagesize=(612, 792))
    for number in (1, 2):
        paper.drawString(60, 725, f"Synthetic template, page {number}")
        paper.drawString(60, 680, "Problem 1: explain your reasoning in the space below.")
        paper.showPage()
    paper.save()
    return output.getvalue()


def wait_until(page, expression, timeout=15000):
    """Poll a page expression with page.evaluate. Under the page's CSP (script-src 'self') Playwright's wait_for_function
    may need eval for a string predicate once it has to poll, which the policy refuses."""
    for _ in range(timeout // 100):
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"Timed out waiting for {expression}")


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    raster = io.BytesIO()
    image = Image.new("RGB", (850, 1100), "white")
    ImageDraw.Draw(image).text((90, 150), "Synthetic reviewed student reasoning", fill="black")
    image.save(raster, format="PNG")
    original = ScanService._retain_page
    def retain(service, job, source, page):
        return original(service, job, source, {**page, "png": raster.getvalue()})
    fixture = ExamGradingTests("test_exact_reviewed_work_regions_history_and_hide_names")
    with patch.object(ScanService, "_retain_page", retain):
        fixture.setUp()
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100}, reduced_motion="reduce")
            page.set_default_timeout(15000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
            page.add_init_script("window.templateCsp=[];document.addEventListener('securitypolicyviolation',e=>templateCsp.push(e.violatedDirective))")
            page.goto(address + f"/#exams/{fixture.event['id']}")
            page.get_by_role("tab", name="Manual scores", exact=True).click()
            delayed_library = []
            def hold_first_library(route):
                if not delayed_library:
                    delayed_library.append(route)
                else:
                    route.continue_()
            page.route("**/api/assignment-templates?assignment_id=*", hold_first_library)
            page.get_by_role("button", name="Manage grading templates", exact=True).click()
            manager = page.locator("#assignment-template-manager")
            upload = page.locator("#assignment-template-upload")
            expect(upload).to_be_visible()
            upload.get_by_label("Template label", exact=True).fill("Main exam reference")
            upload.get_by_label("Template files", exact=True).set_input_files({"name": "exam.pdf", "mimeType": "application/pdf", "buffer": blank_paper()})
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/assignment-templates"), timeout=240000) as response:
                upload.get_by_role("button", name="Upload template", exact=True).click()
            assert response.value.status == 201, response.value.text()
            detail = response.value.json()
            expect(manager.get_by_role("heading", name="Main exam reference", exact=True)).to_be_visible()
            # A slow initial library response must not discard a newly uploaded
            # template, even when that initial response contains no templates.
            assert len(delayed_library) == 1
            delayed_library[0].fulfill(json={"templates": []})
            page.wait_for_timeout(150)
            expect(manager.get_by_role("heading", name="Main exam reference", exact=True)).to_be_visible()
            page.unroute("**/api/assignment-templates?assignment_id=*", hold_first_library)
            assert detail["page_count"] == 2
            assert fixture.grading.details(fixture.event["id"])["version"] == 1, "Upload cannot change the exam rubric"
            manager.get_by_role("button", name="Add problem", exact=True).click()
            mapping = page.locator("#assignment-template-mapping")
            mapping.get_by_label("Problem label", exact=True).fill("Explain the method")
            mapping.get_by_label("Page 2", exact=True).check()
            preview = mapping.locator(".template-page-frame img")
            expect(preview).to_have_attribute("alt", "Blank template page 2")
            wait_until(page, "document.querySelector('.template-page-frame img')?.naturalWidth > 0")
            preview.scroll_into_view_if_needed()
            box = preview.bounding_box()
            page.mouse.move(box["x"] + box["width"] * .1, box["y"] + box["height"] * .2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * .8, box["y"] + box["height"] * .65, steps=6)
            page.mouse.up()
            mapping.get_by_role("button", name="Add area on this page", exact=True).click()
            mapping.locator(".template-coordinates > summary").click()
            for label, value in (("Left", ".2"), ("Top", ".7"), ("Width", ".6"), ("Height", ".2")):
                mapping.get_by_label(label, exact=True).fill(value)
            with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/mapping")) as response:
                mapping.get_by_role("button", name="Save mapping", exact=True).click()
            assert response.value.status == 200, response.value.text()
            saved = response.value.json()
            regions = saved["questions"][0]["regions"]
            assert [region["page"] for region in regions] == [1, 2, 2], regions
            assert abs(regions[1]["region"]["x"] - .1) < .01, regions
            assert abs(regions[1]["region"]["height"] - .45) < .01, regions
            assert regions[2]["region"] == {"x": .2, "y": .7, "width": .6, "height": .2}
            expect(manager.get_by_role("link", name="Download OCR/VLM package", exact=True)).to_have_attribute("href", saved["package_url"])
            assert fixture.grading.details(fixture.event["id"])["version"] == 1, "Saving a template map cannot apply it"

            # Native DOCX and multi-file LaTeX use the same explicit upload flow.
            # Conversion preserves the deliberate source page count, and a
            # one-page template explains why this two-page rubric cannot use it.
            for label, files, entrypoint, page_count in (
                ("Word reference", [{"name": "paper.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "buffer": docx_bytes()}], "", 2),
                ("LaTeX reference", [
                    {"name": "exam.latex", "mimeType": "text/plain", "buffer": br"\documentclass{article}\begin{document}\input{part.tex}\end{document}"},
                    {"name": "part.tex", "mimeType": "text/plain", "buffer": b"One page only"},
                ], "exam.latex", 1),
            ):
                manager.locator(".template-upload > summary").click()
                upload.get_by_label("Template label", exact=True).fill(label)
                upload.get_by_label("Template files", exact=True).set_input_files(files)
                if entrypoint:
                    upload.locator("details > summary").click()
                    upload.get_by_label("Entry point", exact=True).fill(entrypoint)
                with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/assignment-templates"), timeout=240000) as response:
                    upload.get_by_role("button", name="Upload template", exact=True).click()
                assert response.value.status == 201, response.value.text()
                assert response.value.json()["page_count"] == page_count
                expect(manager.get_by_role("heading", name=label, exact=True)).to_be_visible()
                if page_count != 2:
                    expect(manager.get_by_text("This template has 1 page; the selected printed version has 2.", exact=False)).to_be_visible()
                    expect(manager.get_by_role("button", name="Apply saved mapping", exact=True)).to_be_disabled()

            # Loading a different template freezes the old editor so an edit
            # made during the request cannot be silently replaced on completion.
            manager.get_by_label("Saved template", exact=True).select_option(detail["id"])
            expect(mapping.get_by_label("Problem label", exact=True)).to_have_value("Explain the method")
            delayed_detail = []
            detail_url = f"**/api/assignment-templates/{detail['id']}"
            page.route(detail_url, lambda route: delayed_detail.append(route))
            mapping.get_by_role("button", name="Reload saved mapping", exact=True).click()
            expect(mapping.get_by_label("Problem label", exact=True)).to_be_disabled()
            expect(mapping.get_by_role("button", name="Add problem", exact=True)).to_be_disabled()
            expect(manager.get_by_label("Saved template", exact=True)).to_be_disabled()
            delayed_detail[0].continue_()
            expect(mapping.get_by_label("Problem label", exact=True)).to_be_enabled()
            expect(mapping.get_by_label("Problem label", exact=True)).to_have_value("Explain the method")
            page.unroute(detail_url)

            # A stale mapping cannot replace a separately saved revision, and
            # the editor retains the rejected draft until explicitly discarded.
            external = copy.deepcopy(saved["questions"])
            external[0]["label"] = "Retained newer problem"
            newer = fixture.client.put(f"/api/assignment-templates/{detail['id']}/mapping", headers=HEADERS,
                                       json={"version": saved["version"], "questions": external})
            assert newer.status_code == 200, newer.json
            mapping.get_by_label("Problem label", exact=True).fill("My unsaved draft")
            with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/mapping")) as response:
                mapping.get_by_role("button", name="Save mapping", exact=True).click()
            assert response.value.status == 409
            expect(mapping.locator(".form-error")).to_be_visible()
            expect(mapping.get_by_label("Problem label", exact=True)).to_have_value("My unsaved draft")
            page.once("dialog", lambda dialog: dialog.dismiss())
            manager.get_by_role("button", name="Close", exact=True).click()
            expect(manager).to_be_visible()
            page.once("dialog", lambda dialog: dialog.accept())
            manager.get_by_role("button", name="Close", exact=True).click()
            expect(manager).not_to_be_visible()
            page.get_by_role("button", name="Manage grading templates", exact=True).click()
            expect(mapping.get_by_label("Problem label", exact=True)).to_have_value("Retained newer problem")

            # One appearance, desktop only (DESIGN-LANGUAGE): reopened, the dialog keeps its draft and fits at 1440 and 1280.
            manager.get_by_role("button", name="Close", exact=True).click()
            page.get_by_role("button", name="Manage grading templates", exact=True).click()
            for width in (1440, 1280):
                page.set_viewport_size({"width": width, "height": 1100})
                expect(mapping.get_by_label("Problem label", exact=True)).to_have_value("Retained newer problem")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                dialog = page.locator("#dialog")
                assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
                mapping.locator(".template-page-frame").scroll_into_view_if_needed()
                dialog.screenshot(path=str(output / f"templates-{width}.png"))
            page.set_viewport_size({"width": 1440, "height": 1100})
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/grading/template")) as response:
                manager.get_by_role("button", name="Apply saved mapping", exact=True).click()
            assert response.value.status == 200, response.value.text()
            expect(manager).not_to_be_visible()
            page.locator("[data-grading-student]").select_option(f"{fixture.section['id']}/{fixture.enrollment}")
            expect(page.locator("[data-work-area]")).to_have_count(3)
            wait_until(page, "[...document.querySelectorAll('.exams-scan-image')].every(image=>image.naturalWidth > 0)")
            page.get_by_label("Show full page", exact=True).check()
            for frame in page.locator("[data-work-area]").all():
                size = frame.bounding_box()
                assert abs(size["width"] / size["height"] - 850 / 1100) < .01
            page.get_by_label("Show full page", exact=True).uncheck()
            page.locator(".exams-rubric > summary").click()
            page.locator('[data-q="feedback"]').fill("Keep reasoning visible.")
            page.get_by_role("button", name="Save rubric", exact=True).click()
            expect(page.locator('[data-q="feedback"]')).to_have_value("Keep reasoning visible.")
            retained = fixture.grading.details(fixture.event["id"])["questions"][0]
            assert retained["regions"] == newer.json["questions"][0]["regions"], retained

            # Grading templates are no longer prepared in the assignment window (they stay here, on Exams → Manual scores,
            # and in Grading): its Paper step points to Grading, and opening it creates no assignment or version. The window is
            # reached from the assignment's Create area (ASSIGNMENT-WORKSPACE; the former #documents leads to Assignments).
            before = fixture.client.get("/api/documents/catalog").json
            page.goto(address + "/#documents")
            # The list composes every assignment's progress (/api/assignments), slower than one page's catalog on a loaded machine.
            page.locator("[data-assignment-list]").get_by_role("link", name=fixture.assignment["title"], exact=True).click(timeout=60000)
            page.locator("[data-assignment-nav]").get_by_role("link", name="Create", exact=True).click()
            page.locator("#assignment-workspace").get_by_role("button", name="Add or revise paper", exact=True).click()
            wizard = page.locator("#assignment-wizard")
            expect(wizard.locator('[aria-current="step"]')).to_have_text("Paper")
            expect(wizard.locator(".dialog-header")).to_contain_text(f"MATH 140 · {fixture.assignment['title']}")
            expect(wizard.get_by_role("button", name="Manage grading templates", exact=True)).to_have_count(0)
            # Grading does not list this assignment yet (no plan): the line offers to set it up now, before the papers come back
            # (requirement, 2026-10-02), and links nowhere. Nothing is set up unless the button is pressed.
            expect(wizard.locator(".wizard-grading-link")).to_contain_text("Grading can be prepared now, before the papers come back")
            expect(wizard.get_by_role("button", name="Set up grading now", exact=True)).to_be_visible()
            expect(wizard.locator("[data-grading-link]")).to_have_count(0)
            wizard.get_by_role("button", name="Close dialog").click()
            after = fixture.client.get("/api/documents/catalog").json
            assert before["assignments"] == after["assignments"] and before["versions"] == after["versions"]
            assert page.evaluate("templateCsp") == []
            assert errors == [], errors
            browser.close()
        print("PASS: PDF/DOCX/LaTeX uploads, delayed library/detail responses, drawn/numeric multi-page regions, immutable mapping/stale drafts, explicit apply, multi-area grading, wizard return, desktop layout and CSP.")
    finally:
        server.close()
        fixture.tearDown()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
