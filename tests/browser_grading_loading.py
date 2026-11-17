"""Real shell navigation must not duplicate slow grading reads or wait for health; an assignment's steps share its plan read for
10 s unless something was written since."""
import re
import sys
from datetime import datetime, timedelta, timezone
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from workspace_steps import open_scan_records, open_step  # ASSIGNMENT-WORKSPACE "ONE NAVIGATION"
from flask import Response
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests
from unittest.mock import patch
from assignment_templates import read_verified
from test_assignment_templates import prepared

fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
job, _ = fixture.queue()
value = fixture.finish(job)
aid = fixture.plan["id"]
# The Rubrics & Templates step imports the fixture version's saved PDF as its blank template; that version is synthetic, so it
# stands in as in browser_grading_intake and _templates (otherwise the import answers 503 in the background).
fixture.enterContext(patch('assignment_templates.read_verified', side_effect=lambda root, path, checksum:
    b'Synthetic canonical PDF' if path == 'canonical.pdf' else read_verified(root, path, checksum)))
fixture.enterContext(patch('document_identity.template_conversion.convert_template', side_effect=prepared))
fail_health = True


application = fixture.app.wsgi_app


def slow_reads(environ, start_response):
    if environ["REQUEST_METHOD"] == "GET" and environ["PATH_INFO"].startswith("/api/grading"):
        # Ensure the shell's /api/state refresh arrives while grading is loading.
        time.sleep(.15)
        if environ["PATH_INFO"] == "/api/grading/health" and fail_health:
            return Response('{"error":"Synthetic health check unavailable."}', status=503, mimetype="application/json")(environ, start_response)
    return application(environ, start_response)


fixture.app.wsgi_app = slow_reads
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(15000)
        calls, errors = Counter(), []

        writes = []

        def record(req):
            url = urlsplit(req.url)
            if req.method not in ("GET", "HEAD"):
                writes.append(url.path)
            if url.path.startswith("/api/grading"):
                calls[url.path + ("?" + url.query if url.query else "")] += 1

        page.on("request", record)
        page.on("pageerror", lambda error: errors.append(str(error)))
        # Date.now() stands still (timers still run), so "within 10 s" does not depend on how fast this machine is.
        instant = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
        page.clock.set_fixed_time(instant)
        # The Grading index is retired (2026-10-18): compute availability is Compute settings' own section, opened over Assignments,
        # and health is read only when that section is opened.
        page.goto(address + "/#grading?settings=1")
        page.wait_for_function("location.hash.startsWith('#assignments')")
        settings = page.locator("#grading-settings-dialog")
        expect(settings).to_be_visible()
        assert calls["/api/grading?health=0"] == 0, calls
        assert calls["/api/grading/health"] == 0, calls
        settings.get_by_role("tab", name="Availability and costs").click()
        expect(settings.locator("[data-compute-health]")).to_contain_text("Synthetic health check unavailable")
        fail_health = False
        settings.locator("[data-compute-health]").get_by_role("button", name="Try again").click()
        expect(settings.locator("[data-compute-health] .grading-health")).to_be_visible()
        assert calls["/api/grading/health"] == 2, calls
        settings.locator("[data-settings-close]").click()
        # Normal navigation reads the selected assignment with its own activity, in one request, and not the whole menu (1a5d86b).
        calls.clear()
        page.evaluate("hash => location.hash = hash", f"#grading/{aid}?tab=rubrics")
        expect(page.locator('.grading-workflow a[aria-current="step"]')).to_contain_text("Rubrics")
        expect(page.get_by_role("heading", name="Rubrics", exact=True)).to_be_visible()
        assert calls["/api/grading?view=navigation"] == 0, calls
        assert calls[f"/api/grading/assignments/{aid}?include=activity"] == 1, calls
        assert calls["/api/grading?health=0"] == calls["/api/grading/health"] == 0, calls
        expect(page.locator("#dialog")).not_to_be_visible()
        # The step prepares its blank template in the background (a write): let it finish.
        page.wait_for_load_state("networkidle")
        prepared = bool(writes)
        calls.clear()
        # Review is a step of Grade (ASSIGNMENT-WORKSPACE "ONE NAVIGATION"): the Grade tab first, then its own step.
        open_step(page, "jobs")
        expect(page.locator("[data-grade-lead]")).to_be_visible()
        assert calls["/api/grading?view=navigation"] == 0, calls
        # A write since the last plan read (the template preparation) means the next step reads the plan again.
        assert calls[f"/api/grading/assignments/{aid}?include=activity"] == (1 if prepared else 0), (calls, writes)
        calls.clear()
        open_step(page, "review")
        expect(page.locator(".grading-review-workspace")).to_be_visible()
        assert calls["/api/grading?view=navigation"] == 0, calls
        # The workspace reads the queue once, with the review details it navigates by, and opens the waiting response.
        assert calls[f"/api/grading/assignments/{aid}/review-queue?details=review"] == 1, calls
        assert calls[f"/api/grading/assignments/{aid}/responses"] == 0, calls
        # Another step of the same assignment within 10 s, with nothing written since, reuses that plan read (perf-1).
        calls.clear()
        open_step(page, "jobs")
        expect(page.locator("[data-grade-lead]")).to_be_visible()
        assert calls[f"/api/grading/assignments/{aid}?include=activity"] == 0, calls
        # A write made on the page (the batch dialog's preview is a POST) forgets it: the next step reads the plan again.
        with page.expect_response(re.compile(rf".*/api/grading/assignments/{aid}/preview$")):
            page.get_by_role("button", name="Grade new responses", exact=True).click()
        page.locator("#dialog").get_by_role("button", name="Close", exact=True).click()
        calls.clear()
        open_step(page, "review")
        expect(page.locator(".grading-review-workspace")).to_be_visible()
        assert calls[f"/api/grading/assignments/{aid}?include=activity"] == 1, calls
        # A plan read that a write overtook is not kept: Refresh's read is held while the batch dialog's preview (a write) is made
        # and answered, then let go; the next step within 10 s reads the plan again rather than reuse what was read before the write.
        open_step(page, "jobs")
        expect(page.locator("[data-grade-lead]")).to_be_visible()
        held = []
        activity = re.compile(rf".*/api/grading/assignments/{aid}\?include=activity$")
        hold = lambda route: held.append(route)
        page.route(activity, hold)
        with page.expect_request(activity):
            page.locator('#grading-section [data-grading-action="refresh"]').click()
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        assert len(held) == 1, held
        # The step stays on screen while it is read again, so its button still opens the batch dialog.
        with page.expect_response(re.compile(rf".*/api/grading/assignments/{aid}/preview$")):
            page.get_by_role("button", name="Grade new responses", exact=True).click()
        page.unroute(activity, hold)
        with page.expect_response(activity):
            held[0].continue_()
        expect(page.locator(".grading-content")).not_to_have_attribute("aria-busy", "true")
        page.locator("#dialog").get_by_role("button", name="Close", exact=True).click()
        calls.clear()
        open_step(page, "review")
        expect(page.locator(".grading-review-workspace")).to_be_visible()
        assert calls[f"/api/grading/assignments/{aid}?include=activity"] == 1, calls
        # And after 10 s the plan is read again.
        page.clock.set_fixed_time(instant + timedelta(seconds=11))
        calls.clear()
        open_step(page, "rubrics")
        expect(page.get_by_role("heading", name="Rubrics", exact=True)).to_be_visible()
        assert calls[f"/api/grading/assignments/{aid}?include=activity"] == 1, calls
        open_step(page, "review")
        expect(page.locator(".grading-review-workspace")).to_be_visible()
        # Every response with its filters: one list and the one response shown.
        calls.clear()
        page.get_by_role("link", name="All responses, approved grades and review tools →", exact=True).click()
        expect(page.locator(f"#review-{value['id']}")).to_be_visible()
        assert calls[f"/api/grading/assignments/{aid}/responses"] == 1, calls
        assert calls[f"/api/grading/responses/{value['id']}?details=review"] == 1, calls
        assert calls[f"/api/grading/assignments/{aid}/review-queue"] == 0, calls
        # An explicit refresh still obtains new data.
        calls.clear()
        open_step(page, "review")
        expect(page.locator(".grading-review-workspace")).to_be_visible()
        calls.clear()
        # The step stays on screen while it is read again (no skeleton), so wait for the read itself.
        with page.expect_response(re.compile(rf".*/api/grading/assignments/{aid}/review-queue\?details=review$")):
            page.locator('#grading-section [data-grading-action="refresh"]').click()
        expect(page.locator(".grading-review-workspace")).to_be_visible()
        assert calls[f"/api/grading/assignments/{aid}?include=activity"] == 1, calls
        assert calls[f"/api/grading/assignments/{aid}/review-queue?details=review"] == 1, calls
        # A failed load can be retried, and leaving mid-load cannot paint an old tab. (Past the 10 s, so Scan records reads the plan.)
        page.clock.set_fixed_time(instant + timedelta(seconds=30))
        page.route(re.compile(rf".*/api/grading/assignments/{aid}(\?.*)?$"), lambda route: route.fulfill(status=503, content_type="application/json", body='{"error":"Synthetic read failure"}'), times=1)
        open_scan_records(page)
        expect(page.locator('#grading-section [role="alert"]')).to_have_text("Synthetic read failure")
        page.locator("#grading-section").get_by_role("button", name="Try again").click()
        expect(page.get_by_role("heading", name="Scan records for Exam 1", exact=True)).to_be_visible()
        expect(page.locator('.grading-workflow [aria-current="step"]')).to_have_count(0)
        page.evaluate("location.hash='#grading?view=list'")
        page.wait_for_function("location.hash.startsWith('#assignments')")
        assert calls["/api/grading?health=0"] == 0, calls
        page.evaluate("location.hash='#tools'")
        expect(page.locator("#grading-section")).not_to_be_visible()
        page.evaluate("hash => location.hash=hash", f"#grading/{aid}?tab=review")
        expect(page.locator(".grading-review-workspace")).to_be_visible()
        expect(page.locator('.grading-workflow a[aria-current="step"]')).to_contain_text("Review")
        assert not errors, errors
        browser.close()
        print("Grading loading passed: one read per navigation, a plan read shared by steps for 10 s until a write, deferred health and retries, fresh refresh, and safe route changes.")
finally:
    server.close()
    fixture.doCleanups()
