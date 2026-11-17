"""FRQ instructor workflow with real APIs/catalog and synthetic inference only."""
import re
import sys
import json
import uuid
import threading
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from workspace_steps import open_scan_records, open_step  # ASSIGNMENT-WORKSPACE "ONE NAVIGATION"
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests
from test_gradescope_rubrics import encode, export_rows
from fake_openai import FakeHost
from frq_fixtures import finalize_rubrics
from grading import config
from rosters import connect

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
# The single-grader regrade below exercises the retained legacy job format,
# like CloudCase's transport tests; current Cloud has its own team checks.
fixture.enterContext(patch("grading.standard.freeze"))
# Cloud tiers reach a local fake OpenAI-compatible host: real HTTP, no credentials, nothing leaves this machine.
fake_host = FakeHost().start()
settings = fixture.frq.settings()
hosted = {**config.ENDPOINT, "name": "fixture-vision", "base_url": fake_host.url, "model": "fixture/vision", "lineage": "fixture", "price_in": .1, "price_out": .4}
fixture.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "endpoints": [hosted], "yardstick": "",
    "tiers": {tier: {"reader": "fixture-vision", "primary": "fixture-vision", "second": "", "verifier": ""} for tier in config.TIERS}}})
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        # The first step's page also prepares the saved PDF as a template; this fixture has no converter, and that page has its own check.
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 and not response.url.endswith("/templates/prepare") else None)
        # The Grading index is retired (2026-10-18): its address opens Assignments; an assignment's grading opens by its own address.
        page.goto(address + "/#grading?view=list")
        page.wait_for_function("location.hash.startsWith('#assignments')")
        expect(page.locator('.sidebar [data-view="assignments"]')).to_have_attribute("aria-current", "page")
        expect(page.locator('.sidebar [data-view="grading"]')).to_have_count(0)
        page.goto(address + f"/#grading/{fixture.plan['id']}")
        open_step(page, "rubrics")
        expect(page.locator(".grading-workflow a strong")).to_have_text(["Rubrics & Templates", "Answer areas", "Key & Rubric Finalization", "Grade", "Review", "Certify"])
        expect(page.locator("[data-setup-checklist]")).to_have_count(0)
        page.get_by_text("Question settings", exact=True).click()
        page.get_by_role("button", name="Edit question / solution", exact=True).click()
        expect(page.get_by_label("Width (%)", exact=True)).to_have_value("80")
        page.get_by_role("button", name="Add answer area", exact=True).click()
        expect(page.locator("[data-region]")).to_have_count(2)
        page.locator("#dialog").get_by_role("button", name="Close", exact=True).click()
        open_step(page, "rubrics")
        if page.locator(".grading-question-details").get_attribute("open") is None:
            page.locator(".grading-question-details > summary").click()
        page.get_by_role("button", name="Import revision", exact=True).click()
        source = encode(export_rows())
        page.get_by_label("Or read a source file", exact=True).set_input_files({"name": "synthetic.CSV", "mimeType": "text/csv", "buffer": source.encode()})
        page.get_by_role("button", name="Read source file", exact=True).click()
        expect(page.locator("[data-source-status]")).to_contain_text("4 items · positive scoring · 10 points")
        expect(page.get_by_label("Scoring", exact=True)).to_have_value("positive")
        expect(page.get_by_label("Format", exact=True)).to_have_value("csv")
        expect(page.get_by_label("Original rubric text", exact=True)).to_have_value(source.replace("\r\n", "\n"))
        page.get_by_role("button", name="Interpret rubric", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        lines = page.locator(".grading-rubric-edit-lines li")  # Each line editable before the rubric reviewer (2026-09-24).
        expect(lines).to_have_count(4)
        expect(lines.first).to_contain_text("Item 1")
        expect(lines.first.get_by_label("Item 1 points", exact=True)).to_have_value("10")
        expect(lines.first.get_by_label("Item 1 wording", exact=True)).to_have_value("Full credit")
        expect(lines.last.locator(".grading-rubric-line-preview")).to_contain_text("No answer")
        expect(page.locator("[data-rubric-question] .grading-warning")).to_contain_text("Gradescope exports omit item groups")
        expect(page.locator("[data-rubric-question]").first).to_contain_text("Approved when you finalize the rubrics")
        expect(page.get_by_role("button", name="Approve rubric", exact=True)).to_have_count(0)  # Approval belongs to the rubric finalization.
        page.get_by_role("button", name="Import revision", exact=True).click()
        page.get_by_label("Original rubric text", exact=True).fill("-1 Arithmetic error")
        page.get_by_role("button", name="Interpret rubric", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        # The mandatory finalization (its own browser check covers the dialog) approves the rubric and opens grading.
        finalize_rubrics(fixture.frq, fixture.plan["id"])
        page.get_by_role("button", name="Refresh", exact=True).click()
        expect(page.locator(".grading-approved")).to_be_visible()

        question = fixture.frq.assignment(fixture.plan["id"])["questions"][0]
        prepared = fixture.frq.queue_preparation(fixture.plan["id"], "compile", {"question_id": question["question_id"], "question_version": question["version"]}, str(uuid.uuid4()))
        with connect(fixture.path) as db:
            db.execute("UPDATE frq_preparations SET status='complete',result_json=? WHERE id=?",
                       (json.dumps({"source_text": "-1 Arithmetic error", "rules": question["rubric"]["rules"]}), prepared["id"]))
        # The history lives on Rubrics & Templates (with the rubrics it prepares), not on the Grade step.
        open_step(page, "jobs")
        expect(page.locator(".grading-content").get_by_text("Rubric preparation history", exact=True)).to_have_count(0)
        page.evaluate("location.hash = location.hash.split('?')[0] + '?tab=rubrics&focus=preparations'")
        history = page.locator("[data-preparation-history]")
        expect(history).to_have_attribute("open", "")
        expect(history.locator("summary")).to_contain_text("Rubric preparation history")
        history.get_by_role("button", name="Inspect result", exact=True).click()
        expect(page.locator("#dialog")).to_be_visible()
        expect(page.get_by_label("Original rubric text", exact=True)).to_have_value("-1 Arithmetic error")
        expect(page.get_by_label("Responses", exact=True)).to_have_count(0)
        page.keyboard.press("Escape")

        def create_batch(cloud=False, operation="recognize", expected="Ready for review"):
            open_step(page, "jobs")
            # The dialog asks for its own preview on opening, and Start waits for it. That first answer is held until the check is
            # made: a fast server answers before it, and Start is enabled as soon as a preview with eligible responses arrives.
            preview, held = re.compile(rf".*/api/grading/assignments/{fixture.plan['id']}/preview$"), []
            page.route(preview, lambda route: held.append(route), times=1)
            with page.expect_request(preview):
                page.get_by_role("button", name="Grade new responses", exact=True).click()
            start = page.get_by_role("button", name="Start grading batch", exact=True)
            expect(start).to_be_disabled()
            expect(start).to_have_attribute("title", "Checking which responses are eligible…")
            while not held:
                page.wait_for_timeout(50)
            held[0].continue_()
            expect(page.get_by_label("For cloud processing, I inspected")).to_have_count(0)  # No cloud consent or crop approval exists (2026-09-19).
            if cloud:
                page.get_by_label("Grading method", exact=True).select_option("cloud_standard")
            page.get_by_text("Regrading options", exact=True).click()
            page.get_by_label("Recompute", exact=True).select_option(operation)
            page.get_by_role("button", name="Refresh preview", exact=True).click()
            expect(page.locator("[data-batch-preview]")).to_contain_text("1 response eligible")
            if cloud:  # The estimate is shown before anything is submitted; no approval step exists, and no canary (removed 2026-09-28).
                expect(page.locator(".grading-batch-price")).to_contain_text("Estimated cost$0.00")
                expect(page.locator(".grading-batch-price")).not_to_contain_text("spending cap")  # Information only (2026-09-23).
                expect(page.locator("[data-batch-preview]")).not_to_contain_text("canary")
                expect(page.locator("#dialog")).not_to_contain_text("I inspected")
            page.get_by_role("button", name="Start grading batch", exact=True).click()
            expect(page.locator("#dialog")).not_to_be_visible()
            for _ in range(20):
                if not fixture.frq.process_one():
                    break
            page.locator('#grading-section > .grading-heading').get_by_role("button", name="Refresh", exact=True).click()
            expect(page.locator("[data-job]").first).to_contain_text(expected)
            if cloud:
                job = page.locator("[data-job]").first
                expect(job).to_contain_text("Cloud")
                expect(job).to_contain_text("1 response · 1 completed")
                expect(job).to_contain_text("recorded of $0.00 estimated")
                expect(job).not_to_contain_text("Canary")
                job.locator('[data-job-disclosure="run"] > summary').click()
                job.get_by_role("button", name="See what is sent", exact=True).click()
                expect(page.locator(".grading-cloud-preview").first).to_be_visible()
                page.locator("#dialog").get_by_role("button", name="Done", exact=True).click()
                expect(page.locator("#dialog")).not_to_be_visible()
                assert len(fake_host.requests) == 1, "Grading again with retained recognition sends one grading request."

        create_batch()
        open_step(page, "review")
        # Review opens on the waiting response; every response, with its filters and tools, is one link away.
        expect(page.locator("[data-workspace-total]")).to_have_text("1 left")
        expect(page.locator(".grading-review-workspace .grading-review-form")).to_be_visible()
        page.get_by_role("link", name="All responses, approved grades and review tools →", exact=True).click()
        expect(page.locator(".grading-review-form")).to_be_visible()
        expect(page.locator("[data-score]")).to_have_text("9 / 10")
        expect(page.locator(".grading-guide")).to_have_count(0)
        page.get_by_text("Review several responses together", exact=True).click()
        page.get_by_role("button", name="Review proposed grades", exact=True).click()
        expect(page.locator("#dialog")).to_contain_text("Approve response 1")
        page.locator("#dialog").get_by_role("button", name="Close", exact=True).click()
        # Keys (grun:P1, P7): on the buttons as aria-keyshortcuts with visible hints outside their names; the reasons are open.
        expect(page.get_by_role("button", name="Approve grade", exact=True)).to_have_attribute("aria-keyshortcuts", "Alt+A")
        expect(page.get_by_role("button", name="← Previous", exact=True)).to_have_attribute("aria-keyshortcuts", "Alt+P")
        expect(page.locator(".grading-review-work")).not_to_have_attribute("aria-live", "polite")
        expect(page.locator("[data-review-status]")).to_contain_text("response 1 of 1")
        page.locator("[data-transcript-editor] summary").click()
        page.get_by_label("Visible student work — preserve written errors", exact=True).focus()
        page.keyboard.press("Alt+p")  # Works while typing; the first response has no previous one.
        expect(page.locator(".grading-review-form")).to_be_visible()
        page.keyboard.press("1")  # A digit in a text field is typing, never a rubric line.
        expect(page.locator('[name="item"]')).to_be_checked()
        page.get_by_label("Visible student work — preserve written errors", exact=True).fill("3x - 8 = 10\nx = 7")
        page.locator(".grading-item-reason summary").first.click()
        page.locator('[data-evidence-item="R1"]').click()
        expect(page.locator(".grading-highlight")).to_be_visible()
        page.get_by_label("Visible student work — preserve written errors", exact=True).fill("3x - 8 = 10\nx = 8")
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.get_by_role("link", name="Continue to Certify", exact=True).click()
        expect(page.get_by_label("Visible student work — preserve written errors", exact=True)).to_have_value("3x - 8 = 10\nx = 8")
        page.locator("[data-review-heading]").focus()
        page.keyboard.press("1")  # Rubric line 1, by its displayed number.
        expect(page.locator('[name="item"]')).not_to_be_checked()
        expect(page.locator("[data-score]")).to_have_text("10 / 10")
        # The key hint on the cream primary takes the button's own label colour (review fix GR-2: muted grey was 1.82:1 in dark).
        assert page.evaluate("""(() => { const b = document.querySelector('.grading-review-form button.primary');
            return getComputedStyle(b.querySelector('kbd')).color === getComputedStyle(b).color; })()""")
        # No "I inspected…" tick (requirement, 2026-09-27): Alt+A, like the Approve click, approves at once.
        expect(page.get_by_label("I inspected the handwriting, reading, rubric selections and adjustments")).to_have_count(0)
        page.keyboard.press("Alt+a")
        expect(page.locator("#toast")).to_contain_text("Grade approved")
        # The browse view opened on Needs review; with nothing left there it says so and offers every response.
        # Said once, by the empty state; the queue above it adds no sentence of its own (review fix GR-5).
        expect(page.locator(".grading-review-work .empty-state")).to_contain_text("Nothing here needs your review")
        expect(page.locator(".grading-review-queue")).not_to_contain_text("No responses match")
        page.get_by_role("button", name="Show all responses", exact=True).click()
        expect(page.locator(".grading-review-queue")).to_contain_text("Approved")
        expect(page.get_by_label("Visible student work — preserve written errors", exact=True)).to_have_value("3x - 8 = 10\nx = 8")
        page.get_by_label("Show responses", exact=True).select_option("needs-review")
        expect(page.locator(".grading-review-work .empty-state")).to_contain_text("Nothing here needs your review")
        expect(page.locator(".grading-review-form")).to_have_count(0)
        page.get_by_label("Show responses", exact=True).select_option("approved")
        expect(page.locator(".grading-review-form")).to_be_visible()
        page.get_by_role("link", name="Continue to Certify", exact=True).click()
        expect(page.locator(".grading-step-header h3")).to_have_text("Certify")
        # The files stay on the page, below the analysis (requirement, 2026-09-26): one click per file.
        with page.expect_download() as downloaded:
            page.get_by_role("button", name="Scores per question", exact=True).click()
        assert downloaded.value.suggested_filename == "approved-grades.csv", downloaded.value.suggested_filename
        assert Path(downloaded.value.path()).read_text().startswith("Name,Section ID,Enrollment ID,Question,Score")
        open_step(page, "review")
        page.get_by_role("link", name="All responses, approved grades and review tools →", exact=True).click()
        page.get_by_text("Regrading and diagnostics", exact=True).click()
        page.get_by_role("button", name="Compare with approved work", exact=True).click()
        expect(page.locator("#dialog")).to_contain_text("exact score agreement")
        page.locator("#dialog").get_by_role("button", name="Close", exact=True).first.click()
        # Its only response keeps the decision already made, so nothing waits: the card says so and shows Finished (the stored
        # status stays review_ready).
        create_batch(cloud=True, operation="grade", expected="Finished")
        expect(page.locator("[data-job]").first.locator("[data-batch-outcome]")).to_have_text("1 graded · nothing waits for you")
        open_step(page, "review")
        page.get_by_role("link", name="All responses, approved grades and review tools →", exact=True).click()
        expect(page.get_by_label("Visible student work — preserve written errors", exact=True)).to_have_value("3x - 8 = 10\nx = 8")
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): no dark theme or phone width to check.
        page.screenshot(path=str(OUTPUT / "grading-1500.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        # The six grading steps; scan review precedes them in the Inbox and is reached by its own utility link below.
        for tab in ("rubrics", "locations", "answer-key", "jobs", "review", "export"):
            open_step(page, tab)
            expect(page.locator('.grading-tabs a[aria-current="step"]')).to_have_attribute("href", re.compile(rf"tab={tab}(&|$)"))
            expect(page.locator(".grading-content")).not_to_be_empty()
            expect(page.locator(".grading-guide")).to_have_count(0)
            if tab == "templates":
                # This fixture's synthetic canonical PDF has no retained bytes (browser_grading_templates.py covers the step with
                # real ones), so preparing a template from it is refused. The step must say so and offer a retry, never hang;
                # exactly those refusals are expected here, and any other HTTP error still fails the run.
                expect(page.get_by_role("button", name="Retry preparation", exact=True).first).to_be_visible()
                refused = [entry for entry in bad if entry[0] == 503 and entry[1].endswith("/templates/prepare")]
                assert refused and len(refused) == len(bad), bad
                bad.clear()
            for width in (1500, 1280):
                page.set_viewport_size({"width": width, "height": 1100})
                page.screenshot(path=str(OUTPUT / f"workflow-{tab}-{width}.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (tab, width)
                # The step graph (DESIGN-LANGUAGE §4) numbers its six steps in order, each with its label and a status word or
                # progress fraction, and every step stays in view without scrolling the graph sideways.
                graph = page.locator(".grading-workflow .grading-pipeline")
                expect(graph.locator(".grading-pipeline-number")).to_have_text([str(n) for n in range(1, 7)])
                expect(graph.locator("li > a > strong")).to_have_count(6)
                expect(graph.locator("li > a > small:is(.grading-pipeline-status, .grading-pipeline-fraction)")).to_have_count(6)
                scroller = page.locator(".grading-workflow .grading-pipeline-scroll")
                assert scroller.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (tab, width)
        page.set_viewport_size({"width": 1500, "height": 1100})
        # Scan records stays reachable without being a numbered step: no step is selected while it is open.
        open_scan_records(page)
        expect(page.locator(".grading-content")).to_contain_text("Scan records for")
        expect(page.locator('.grading-tabs a[aria-current="step"]')).to_have_count(0)
        page.get_by_role("button", name="Compute settings", exact=True).click()
        expect(page.get_by_role("tabpanel", name="Defaults", exact=True)).to_be_visible()
        page.get_by_role("tab", name="Connections", exact=False).click()
        expect(page.locator("[data-endpoint]")).to_have_count(1)
        expect(page.locator("[data-endpoint]").first).to_contain_text("Not tested")
        with page.expect_response(lambda response: response.url.endswith("/probe") and response.request.method == "POST"):
            page.get_by_role("button", name="Test fixture-vision", exact=True).click()
        fixture.frq.process_cloud()
        page.get_by_role("button", name="Refresh status", exact=True).click()
        expect(page.locator("[data-endpoint]").first).to_contain_text("Test passed")
        page.get_by_role("button", name="Edit fixture-vision", exact=True).click()
        page.get_by_text("Last connection test", exact=True).click()
        expect(page.locator("#dialog")).to_contain_text("structured output yes · image input yes · served by FakeUpstream")
        page.locator("#dialog").get_by_role("button", name="Close", exact=True).click()
        page.get_by_role("button", name="Add connection", exact=True).click()
        added = page.locator("#dialog")
        added.get_by_label("Connection name", exact=True).fill("fixture-text")
        added.get_by_label("Model ID", exact=True).fill("fixture/text")
        expect(added.get_by_label("Model family", exact=True)).to_have_count(0)
        added.get_by_label("Base URL", exact=True).fill(fake_host.url)
        added.get_by_label("Key variable on the server", exact=True).fill("")
        added.get_by_text("Capabilities and provider", exact=True).click()
        added.get_by_label("Input", exact=True).select_option("text")
        added.get_by_role("button", name="Apply to settings", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        expect(page.locator("[data-endpoint]")).to_have_count(2)
        page.get_by_role("button", name="Save compute settings", exact=True).click()
        expect(page.locator(".grading-settings [role=alert]")).not_to_be_visible()
        expect(page.locator("[data-endpoint]").nth(1)).to_contain_text("fixture-text")
        # Picking Cloud brings its own team's model selectors up under the picker.
        page.get_by_role("tab", name="Defaults", exact=True).click()
        page.locator('input[name="default_tier"][value="cloud_standard"]').check()
        page.route('**/api/grading/model-catalog', lambda route: route.fulfill(json={'models': [], 'checked_at': None, 'notice': 'Synthetic offline catalogue'}))
        page.locator('[name="provider-tier-cloud_standard-second"]').select_option('custom')
        page.get_by_role('button', name="Browse models for Cloud Second grader", exact=True).click()
        page.locator('#dialog input[value="@connection/fixture-text"]').check()
        page.locator('#dialog').get_by_role('button', name='Use model', exact=True).click()
        page.get_by_label("Primary grader", exact=True).first.select_option("fixture-text")
        page.get_by_role("button", name="Save compute settings", exact=True).click()
        expect(page.locator(".grading-settings [role=alert]")).to_contain_text("needs a vision endpoint")  # A text-only model cannot be the grader that sees the image.
        assert [status for status, _ in bad] == [400], bad
        bad.clear()  # That refusal was the point; any other HTTP error still fails the run.
        page.get_by_label("Primary grader", exact=True).first.select_option("fixture-vision")
        # Back to the local scheme for the batches below. Its team's choices leave the screen but stay in the form, so the
        # models just chosen are still what is saved.
        page.locator('input[name="default_tier"][value="local"]').check()
        expect(page.get_by_label("Primary grader", exact=True).first).to_be_hidden()
        with page.expect_response(lambda response: response.url.endswith("/api/grading/settings") and response.request.method == "PUT") as saved:
            page.get_by_role("button", name="Save compute settings", exact=True).click()
        assert saved.value.status == 200, saved.value.status
        expect(page.locator(".grading-settings [role=alert]")).not_to_be_visible()
        assert fixture.frq.settings()["settings"]["tiers"]["cloud_standard"]["second"] == "fixture-text"
        page.get_by_role("button", name="Close compute settings", exact=True).click()
        expect(page.locator("#grading-settings-dialog")).not_to_be_visible()
        expect(page.locator("#grading-section .grading-content")).to_be_visible()
        fixture.fake.fail = True
        create_batch(operation="grade", expected="Failed")
        expect(page.locator("[data-job]").first).to_contain_text("Synthetic grader unavailable")
        # ONE RETRY (2026-10-18): the batch card states the count and links Review's Failed to grade, where Retry is.
        expect(page.locator("[data-job]").first.locator("[data-job-failed]")).to_have_text("1 failed response: retry in Review →")
        expect(page.locator("[data-job]").first.get_by_role("button", name="Retry 1 failed response", exact=True)).to_have_count(0)
        # THE CERTIFY STEP WAITS FOR A LATE SCAN (2026-10-11): with the plan's read saying a later scan is still in its Inbox checks
        # and no certification current, the button at the head of All responses leads to that scan, with the reason as its title;
        # read again without it, the button is the way on to certification as before.
        plan_read = re.compile(rf".*/api/grading/assignments/{fixture.plan['id']}(\?.*)?$")

        def late_scan(route):
            if route.request.method != "GET":
                return route.continue_()
            response = route.fetch()
            body = response.json()
            body["workflow"]["awaiting_review"] = 1
            body["workflow"]["scans"] = [*(body["workflow"].get("scans") or []), {"job_id": "late-scan", "inbox_ready": False}]
            body["workflow"]["certification"] = {**(body["workflow"].get("certification") or {}), "state": "none"}
            route.fulfill(response=response, json=body)

        all_responses = f"{address}/#grading/{fixture.plan['id']}?tab=review&browse=all"
        # A step reuses the plan's read for 10 s, so Refresh reads it again each time.
        refresh = page.locator('[data-grading-action="refresh"]').first
        page.route(plan_read, late_scan)
        page.goto(all_responses)
        onward = page.locator("[data-review-onward]")
        expect(onward).to_have_text("Continue to Certify")
        refresh.click()
        expect(onward).to_have_text("Finish the scan checks")
        expect(onward).to_have_attribute("data-review-onward", "scan")
        expect(onward).to_have_attribute("href", re.compile(r"^#assignments/[^/]+/[^/?]+\?area=collect&scans=waiting$"))
        expect(onward).to_have_attribute("title", "A later scan of this assignment still waits for scan checks: certify once its papers are graded and reviewed.")
        page.unroute(plan_read)
        refresh.click()
        expect(onward).to_have_text("Continue to Certify")
        expect(onward).to_have_attribute("href", f"#grading/{fixture.plan['id']}?tab=export")
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("FRQ grading browser workflow passed: rubric import/approval, local/cloud jobs, evidence, edits, approval, export; no browser errors.")
except Exception:
    print("Browser errors:", errors if "errors" in globals() else [])
    print("HTTP errors:", bad if "bad" in globals() else [])
    raise
finally:
    server.close()
    fake_host.stop()
    fixture.doCleanups()
