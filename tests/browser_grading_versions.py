"""Deciding a paper's version redraws the whole step: the assignment's notice, its "Decide versions" action and the graph count the
paper as decided at once (grading-setup-6). The dialog's confirmation tick and its refusal sit in the footer beside the button."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_exam_versions import ExamVersionTests

fixture = ExamVersionTests("test_a_paper_that_matches_the_other_version_waits_for_the_instructor_and_is_never_switched")
fixture.setUp()
server = None
output = Path("test-results")
output.mkdir(exist_ok=True)
try:
    # Seated for Version A, but the second student's pages look like Version B: that paper waits for the instructor's decision.
    fixture.seat(enrollment="A", second="A")
    fixture.looks_like[fixture.second] = "B"
    fixture.two_versions()
    fixture.preview()
    fixture.compare_all()
    aid = fixture.plan["id"]
    assert fixture.frq.assignment(aid)["workflow"]["version_decisions"] == 1
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{address}/#grading/{aid}?tab=rubrics&focus=versions")
        notice = page.locator("#grading-section [data-version-decisions]")
        expect(notice).to_contain_text("1 paper needs your version decision.")
        expect(page.locator("#grading-section").get_by_text("Decide versions").first).to_be_visible()
        panel = page.locator("[data-exam-versions]")
        expect(panel.get_by_role("button", name="Decide", exact=True)).to_be_visible()
        panel.get_by_role("button", name="Decide", exact=True).click()
        dialog = page.locator("#dialog")
        expect(dialog.get_by_role("heading", name="Which version did this student write?")).to_be_visible()
        dialog.get_by_label("Assignment version", exact=True).select_option("B")
        # The tick and the refusal are in the footer, beside the button: a long dialog still shows why nothing happened.
        footer = dialog.locator(".dialog-footer")
        expect(footer.get_by_label("I looked at this paper and it is this version")).to_be_visible()
        footer.get_by_role("button", name="Record this paper’s version", exact=True).click()
        expect(footer.locator(".form-error")).to_have_text("Choose the version and confirm that you looked at the paper.")
        expect(footer.locator(".form-error")).to_be_in_viewport()
        # One appearance, desktop only (DESIGN-LANGUAGE): a desktop window and a shorter laptop screen.
        for width, height in ((1440, 900), (1280, 720)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(200)
            expect(footer.locator(".form-error")).to_be_in_viewport()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
            page.screenshot(path=str(output / f"grading-version-dialog-{width}x{height}.png"))
        page.set_viewport_size({"width": 1440, "height": 900})
        footer.get_by_label("I looked at this paper and it is this version").check()
        footer.get_by_role("button", name="Record this paper’s version", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Recorded this paper’s version.")
        expect(dialog).not_to_be_visible()
        # The whole step was read again: no notice, no "Decide versions", and nothing waits in the panel.
        expect(notice).to_have_count(0)
        expect(page.locator("#grading-section").get_by_text("Decide versions")).to_have_count(0)
        expect(panel.get_by_role("button", name="Decide", exact=True)).to_have_count(0)
        assert fixture.frq.assignment(aid)["workflow"]["version_decisions"] == 0
        assert not errors, errors
        browser.close()
        print("Grading versions passed: recording a paper's version redraws the step (notice, Decide versions, panel), with the tick and refusal in the dialog footer.")
finally:
    if server:
        server.close()
    fixture.doCleanups()
