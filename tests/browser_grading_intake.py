"""Scan → assignment → explicit review → assignment grading, through real APIs."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_intake import GradingIntakeTests
from rosters import connect
from unittest.mock import patch
from test_assignment_templates import prepared
from assignment_templates import read_verified

fixture = GradingIntakeTests("test_unknown_assignment_create_is_atomic_and_idempotent")
fixture.setUp()
with connect(fixture.path) as db:
    db.execute('UPDATE inbox_messages SET instructor_review_required=1 WHERE id=?', (fixture.job['message_id'],))
fixture.frq.sync_scan(fixture.inspected())
fixture.enterContext(patch('document_identity.template_conversion.convert_template', side_effect=prepared))
fixture.enterContext(patch('assignment_templates.read_verified', side_effect=lambda root, path, checksum:
    b'Synthetic canonical PDF' if path == 'canonical.pdf' else read_verified(root, path, checksum)))
arrival = fixture.arrival()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
output = Path("test-results")
output.mkdir(exist_ok=True)
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        page.goto(address + f"/#inbox/job/{arrival['id']}/assignment")  # the scan's Assignment stage
        page.get_by_role("button", name="Choose assignment", exact=True).click()
        # The dialog is named for its step, and with nothing suggested it starts on no assignment: one must be chosen (it used to
        # preselect the first in sort order, a certified one on real data).
        expect(page.locator("#dialog")).to_contain_text("Choose the assignment")
        expect(page.locator("#dialog").get_by_label("Assignment", exact=True)).to_have_value("")
        page.locator("#dialog").get_by_role("button", name="Save assignment", exact=True).click()
        expect(page.locator("#dialog")).to_be_visible()  # the required choice holds the save back
        page.locator("#dialog").get_by_label("Assignment", exact=True).select_option("new")
        page.get_by_label("New assignment title", exact=True).fill("Quiz 2")
        page.get_by_role("button", name="Save assignment", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        expect(page.locator("[data-scan-content]")).to_contain_text("Assignment: Quiz 2")
        # Before any of its scans clears the scan checks, the plan's steps are open all the same (item 12, 2026-10-18): rubrics come
        # from the paper, not from scans. One line over them says what waits and links the assignment's waiting scans on Collect (I2).
        listed = page.request.get(address + "/api/grading?view=navigation&health=0").json()
        quiz = next(w for w in listed["waiting"] if w["title"] == "Quiz 2")
        assert quiz["assignment_id"] and quiz["term_id"], quiz  # additive I2 fields of the waiting list
        page.goto(address + f"/#grading/{quiz['id']}")
        notice = page.locator("[data-grading-entry]")
        expect(notice).to_contain_text("Waiting for scan checks on 1 scan.")
        expect(notice).to_contain_text("Rubrics, templates and answer areas can be prepared now")
        expect(notice.get_by_role("link", name="Finish the scan checks →", exact=True)).to_have_attribute(
            "href", f"#assignments/{quiz['assignment_id']}/{quiz['term_id']}?area=collect&scans=waiting")
        expect(page.locator("#grading-section")).not_to_contain_text("Finish Inbox review first")
        expect(page.locator("[data-grading-workflow] .grading-pipeline li")).to_have_count(6)
        expect(page.locator(".grading-step-header h3")).to_have_text("Rubrics & Templates")
        page.goto(address + f"/#inbox/job/{fixture.job['id']}/review")  # the scan's Review stage
        # The page loads before its buttons act: clicking before that did nothing (a race in this check).
        expect(page.locator("[data-scan-content]")).to_contain_text("Exam 1")
        page.get_by_role('button', name='Finish review & open grading', exact=True).click()
        page.get_by_label('I have checked this scan and its assignment', exact=True).check()
        page.locator('#dialog').get_by_role('button', name='Finish review & open grading', exact=True).click()
        expect(page.locator('#page-title')).to_have_text('Exam 1')  # The shell header names the assignment; the page repeats no heading.
        expect(page.locator('#grading-section h2')).to_have_count(0)
        expect(page.locator('#breadcrumb')).to_contain_text('Assignments')  # The shell's trail (ASSIGNMENT-WORKSPACE).
        page.locator('#breadcrumb').get_by_role('link', name='Assignments', exact=True).click()
        expect(page.locator('#page-title')).to_have_text('Assignments')
        # The retired Grading index's address opens Assignments (2026-10-18).
        page.goto(address + "/#grading?view=list")
        page.wait_for_function("location.hash.startsWith('#assignments')")
        page.screenshot(path=str(output / "grading-assignments.png"), full_page=True)
        # A later arrival goes to the very same assignment, not a new grading plan.
        next_scan = fixture.arrival()
        page.goto(address + f"/#inbox/job/{next_scan['id']}/assignment")
        page.get_by_role("button", name="Choose assignment", exact=True).click()
        assignment = fixture.assignment["id"]
        page.locator("#dialog").get_by_label("Assignment", exact=True).select_option(assignment)
        page.get_by_role("button", name="Save assignment", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        read = page.request.get(address + f"/api/grading/assignments/{fixture.plan['id']}").json()
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics")
        notice = page.locator("[data-scan-checks-notice]")
        expect(notice).to_contain_text("Waiting for scan checks on 1 scan.")
        expect(notice.locator("a")).to_have_attribute("href", f"#assignments/{read['source_assignment_id']}/{read['term_id']}?area=collect&scans=waiting")
        graph = page.locator("[data-grading-workflow]")
        expect(graph.locator(".grading-pipeline li")).to_have_count(6)
        expect(graph.locator('[data-pipeline-step="papers"]')).to_have_count(0)
        # Rubrics are in; the first step stays the next task until the templates it also holds are confirmed.
        expect(graph.locator('[data-pipeline-step="template"]')).to_have_count(0)
        expect(graph.locator('[data-pipeline-step="rubrics"] a')).to_have_attribute("aria-current", "step")
        expect(graph.locator('[data-pipeline-step="rubrics"] strong')).to_have_text("Rubrics & Templates")
        expect(graph.locator('[data-pipeline-step="export"] strong')).to_have_text("Certify")
        expect(graph.locator('[data-pipeline-step="export"] .grading-pipeline-status')).to_have_text("Not started")
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.screenshot(path=str(output / "grading-pipeline-1440.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Pipeline overflows the page"
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=export")
        expect(page.locator('.grading-workflow a[href$="tab=export"]')).to_have_attribute("aria-current", "step")
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=papers")
        expect(page.locator(".grading-workflow [aria-current=step]")).to_have_count(0)
        expect(page.locator(".grading-workflow-position")).to_have_count(0)
        expect(page.locator(".grading-paper-steps li")).to_have_count(2)
        expect(page.locator(".grading-scan-records .section-lead")).to_contain_text("1 waiting for scan checks")
        page.screenshot(path=str(output / "grading-arrivals-1440.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Scan records page overflows"
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=jobs")
        page.get_by_role("button", name="Grade new responses", exact=True).click()
        expect(page.get_by_label("Responses", exact=True)).to_have_value("new")
        # This plan's answer areas were never confirmed, so the dialog's preview is refused (409) and says why. Waited for: a
        # faster server answers before the checks below, a slower one after them (performance pass, 2026-09-29).
        expect(page.locator("[data-batch-preview] [role=alert]")).to_contain_text("Confirm this question’s template locations")
        bad = [(status, url) for status, url in bad if not (status == 409 and url.endswith(f"/api/grading/assignments/{fixture.plan['id']}/preview"))]
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Scan intake browser passed: creation, association, review handoff, later arrivals, assignment cards, numbered step graph, desktop layout and new-only default.")
finally:
    server.close()
    fixture.doCleanups()
