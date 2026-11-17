"""Exam-sized grading views: responsive hierarchy, batch history, and polling while using controls."""
import copy
import re
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from workspace_steps import open_step  # ASSIGNMENT-WORKSPACE "ONE NAVIGATION"
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_review_queue import ReviewQueueVersionTests

fixture = ReviewQueueVersionTests("test_each_exam_version_keeps_its_own_question_rubric_and_piles")
fixture.setUp()
server = None
output = Path("test-results")
output.mkdir(exist_ok=True)
try:
    fixture.graded()
    aid = fixture.plan["id"]
    plan = fixture.frq.assignment(aid, include_activity=True)  # What the page asks for: the assignment with its own batches.
    overview = fixture.frq.overview(navigation=True, include_health=False)
    queue = fixture.frq.review_queue(aid)
    # Expand only the read-only browser projection; no invented classroom records are written.
    questions, entries = [], []
    for original in plan["questions"]:
        for number, waiting in zip(range(18, 26), (14, 4, 9, 6, 10, 10, 9, 13)):
            q = copy.deepcopy(original)
            q.update(question_id=f"{original['question_id']}-{number}", question_key=f"q{number}")
            q["content"]["title"] = f"Question {number}"
            if q.get("location_check", {}).get("context"):
                q["location_check"]["context"]["printed_number"] = number
            questions.append(q)
            entries.append(dict(question_id=q["question_id"], responses=161, waiting=waiting,
                                approved=6, not_graded=155 - waiting, flagged=max(0, waiting - 4),
                                doubted=1 if number == 18 else 0, groups=[dict(id=f"lines-{number}", size=waiting,
                                    members=[{"id": f"response-{q['question_id']}-{i}"} for i in range(waiting)],
                                    kind="proposal", applied=[], items=[], states={}, flagged=0, scores=[])]))
    plan["questions"] = questions
    # ASSIGNMENT-WORKSPACE: the shell titles the page with the course-wide assignment it resolves the step to.
    title = fixture.app.extensions["assignment_workspace"].resolve(f"#grading/{aid}")["context"]["title"]
    plan["workflow"]["awaiting_review"] = 3
    plan["workflow"].update(pending=3, failed=2, review_responses=sum(e["waiting"] for e in entries))
    queue.update(questions=entries, waiting=plan["workflow"]["review_responses"])
    job = fixture.frq.job(overview["jobs"][0]["id"])
    job.update(status="running", mode="nebius", tier=None, runtime_seconds=76559, cost=1.8793,
               estimated_cost=1.51, notice="Canary check skipped: 0 of the 40 approved responses it needs exist so far. It never blocks a batch.")
    job["progress"].update(total=240, completed=235, queued=1, running=2, failed=2,
                           awaiting_review=90, in_flight=2, retried=1, stages={"grading": 2, "scoring": 1})
    job["tasks"] = [{**job["tasks"][0], "status": "failed", "error": "Synthetic provider failure. Retry this response."}]
    older = copy.deepcopy(job)
    older.update(id="earlier-batch", status="cancelled", cancel_requested=True)
    older["progress"].update(completed=93, cancelled=146, queued=0, running=0, failed=1, stages={})
    overview["jobs"] = [job, older]
    overview["preparations"] = []
    plan["activity"] = {**plan["activity"], "jobs": [job, older], "preparations": []}  # A step reads its batches from here.
    # Two papers missing pages, and nothing new: the Grade step says both (grun:P4, P11).
    plan["workflow"].update(new_responses=0, regrade_responses=0, blocked_responses=46, version_decisions=0)
    plan["workflow"]["collection"] = {**plan["workflow"].get("collection", {}), "no_scan": 0, "partial": 0, "missing_pages": 2, "needs_review": 0}
    entries[0]["automatic"] = 12  # Grades finalized automatically, for a spot-check.
    entries[0]["approved"] = 12
    spot_rows = [{"id": f"auto-{i}", "question_id": questions[0]["question_id"], "question_key": "q18", "state": "auto_accepted"} for i in range(30)]
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    address = f"http://127.0.0.1:{server.effective_port}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, writes = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: writes.append(request.url) if request.method not in ("GET", "HEAD") else None)
        # The page asks for the assignment with its activity (`?include=activity`); the synthetic plan answers either way.
        page.route(re.compile(rf".*/api/grading/assignments/{aid}(\?.*)?$"), lambda route: route.fulfill(json=plan))
        page.route(re.compile(rf".*/api/grading/assignments/{aid}/review-queue(\?.*)?$"), lambda route: route.fulfill(json=queue))
        page.route("**/api/grading?view=navigation", lambda route: route.fulfill(json=overview))
        page.route(f"**/api/grading/jobs/{job['id']}", lambda route: route.fulfill(json=job))
        page.route("**/api/grading/jobs/earlier-batch", lambda route: route.fulfill(json=older))
        page.route(f"**/api/grading/assignments/{aid}/responses", lambda route: route.fulfill(json={"responses": spot_rows}))
        page.route("**/api/grading/responses/auto-*", lambda route: route.fulfill(status=503, json={"error": "Synthetic: not needed here."}))
        # The workspace opens on the first synthetic response; its desk is not what this check is about.
        page.route("**/api/grading/responses/response-*", lambda route: route.fulfill(status=503, json={"error": "Synthetic: not needed here."}))
        for tab in ("review", "jobs"):
            page.goto(f"{address}/#grading/{aid}?tab={tab}")
            expect(page.locator(".grading-stage-heading h3")).to_have_text("Review grades" if tab == "review" else "Grade responses")
            if tab == "review":
                # Review opens on the first waiting response; every question with work is a row in the rail.
                expect(page.locator("[data-workspace-questions] section")).to_have_count(2)
                expect(page.locator("[data-workspace-questions] li")).to_have_count(16)
                first = entries[0]
                page.wait_for_function("id => location.hash.endsWith(id)", arg=f"&question={first['question_id']}&group={first['groups'][0]['id']}&response={first['groups'][0]['members'][0]['id']}")
                expect(page.locator("[data-spot-check-link]")).to_have_attribute("href", f"#grading/{aid}?tab=review&browse=all&status=automatic&spot=10")
            else:
                expect(page.locator("[data-grade-lead]")).to_have_text("Nothing new to grade.")
                expect(page.get_by_role("button", name="Grade new responses", exact=True)).not_to_have_class(re.compile(r"\bprimary\b"))
                expect(page.locator("[data-grade-blocked] .callout-title")).to_have_text("2 papers are missing pages, so 46 responses cannot be graded.")
                expect(page.locator("[data-grade-blocked]").get_by_role("link", name="Open Collection")).to_have_attribute("href", f"#exams/{plan['event_id']}?tab=collection")
                expect(page.locator(".grading-batch:visible")).to_have_count(1)
                # ONE RETRY (2026-10-18): the card states the count and links Review's Failed to grade.
                expect(page.locator(".grading-batch:visible [data-job-failed]")).to_have_text("2 failed responses: retry in Review →")
                expect(page.get_by_role("button", name="Retry 2 failed responses", exact=True)).to_have_count(0)
                # A skipped check of an old batch is routine: said under Batch details, not on the card.
                expect(page.locator(".grading-batch:visible [data-job-disclosure=run] [data-batch-check]")).to_be_attached()
                expect(page.locator(".grading-warning")).to_have_count(0)
            # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): the step at desktop widths down to the 1180px floor.
            for width in (1500, 1280, 1180):
                page.set_viewport_size({"width": width, "height": 1100})
                page.evaluate("document.fonts.ready")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (tab, width)
                expect(page.locator('.grading-workflow [aria-current="step"]')).to_be_in_viewport()
                page.screenshot(path=str(output / f"grading-layout-{tab}-{width}.png"), full_page=True)
            page.set_viewport_size({"width": 1500, "height": 1100})
        # Polling keeps disclosures and keyboard focus even when a response count changes the summary.
        card = page.locator(f'[data-job="{job["id"]}"]')
        run = card.locator('[data-job-disclosure="run"]')
        # A running batch's Cancel is on the card itself, not inside its details (grun:P19).
        expect(card.locator(".grading-batch-actions").get_by_role("button", name="Cancel remaining work", exact=True)).to_be_visible()
        expect(run.get_by_role("button", name="Cancel remaining work", exact=True)).to_have_count(0)
        # A cancelled batch is never claimed again, so the click asks first; closing the question sends nothing (review fix GRUN-F3).
        card.locator(".grading-batch-actions").get_by_role("button", name="Cancel remaining work", exact=True).click()
        expect(page.locator("#dialog")).to_contain_text("Cancel the remaining 3 responses?")
        expect(page.locator("#dialog")).to_contain_text("Nothing already graded is lost")
        page.locator("#dialog").get_by_role("button", name="Close", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        # A settled batch says its outcome once: the facts row does not repeat what waits, failed or was cancelled (review fix GR-1).
        page.locator(".grading-batch-history > summary").click()
        earlier = page.locator('[data-job="earlier-batch"]')
        expect(earlier.locator("[data-batch-outcome]")).to_contain_text("146 cancelled")
        expect(earlier.locator(".grading-batch-facts")).to_have_text("")
        page.locator(".grading-batch-history > summary").click()
        run.locator("summary").click()
        responses = card.locator('[data-job-disclosure="responses"]')
        responses.locator("summary").click()
        responses.locator("summary").focus()
        job["progress"]["failed"] = 3
        expect(responses.locator("summary")).to_contain_text("3 failed responses")
        expect(responses).to_have_attribute("open", "")
        expect(run).to_have_attribute("open", "")
        expect(responses.locator("summary")).to_be_focused()
        page.locator(".grading-batch-history > summary").focus()
        page.keyboard.press("Enter")
        expect(page.locator('[data-job="earlier-batch"]')).to_be_visible()
        expect(page.locator('[data-job="earlier-batch"] .grading-badge').first).to_have_text("Cancelled")
        # The assignment is the shell header's title, with its course and term and a trail to the step's area; no heading repeats it.
        expect(page.locator("#page-title")).to_have_text(title)
        expect(page.locator("#breadcrumb")).to_contain_text("Assignments")
        expect(page.locator("#breadcrumb li[aria-current=page]")).to_have_text("Grade")
        expect(page.locator("#grading-section h2")).to_have_count(0)
        # Models are one click from the Grade step.
        page.get_by_role("button", name="Models: Compute settings", exact=True).click()
        expect(page.get_by_role("dialog", name="Compute settings", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        # Another step keeps the heading and the graph on screen while it loads (content-only loading): whether it reads the plan
        # again or reuses the read of the last 10 s (perf-1), its content waits behind a busy skeleton until its own read is drawn.
        slow = []
        held_plan, held_queue = re.compile(rf".*/api/grading/assignments/{aid}\?include=activity$"), f"**/api/grading/assignments/{aid}/review-queue"
        hold_plan, hold_queue = (lambda route: slow.append((route, plan))), (lambda route: slow.append((route, queue)))
        page.route(held_plan, hold_plan)
        page.route(held_queue, hold_queue)
        open_step(page, "review")
        expect(page.locator(".grading-content")).to_have_attribute("aria-busy", "true")
        expect(page.locator('.grading-workflow [aria-current="step"]')).to_be_visible()
        expect(page.locator("#page-title")).to_have_text(title)
        page.unroute(held_plan, hold_plan)
        page.unroute(held_queue, hold_queue)
        for route, answer in slow:
            route.fulfill(json=answer)
        expect(page.locator(".grading-stage-heading h3")).to_have_text("Review grades")
        expect(page.locator(".grading-content")).not_to_have_attribute("aria-busy", "true")
        # Refresh redraws the step on screen in place: no skeleton, and the page stays where the instructor scrolled, even at its very
        # end (the held height keeps the bottom margin). Pressed from the keyboard, Refresh keeps keyboard focus (the bar is drawn again).
        page.set_viewport_size({"width": 1500, "height": 600})
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        y = page.evaluate("scrollY")
        assert y > 100, y
        page.evaluate("window.__tiles = document.querySelector('.grading-review-workspace')")
        page.evaluate("document.querySelector('#grading-section [data-grading-action=refresh]').focus({preventScroll: true})")
        with page.expect_response(re.compile(rf".*/api/grading/assignments/{aid}/review-queue\?details=review$")):
            page.keyboard.press("Enter")
        expect(page.locator(".grading-content")).not_to_have_attribute("aria-busy", "true")
        page.wait_for_function("() => window.__tiles !== document.querySelector('.grading-review-workspace')")
        expect(page.locator(".grading-content .loading-skeleton")).to_have_count(0)
        expect(page.locator('#grading-section [data-grading-action="refresh"]')).to_be_focused()
        assert abs(page.evaluate("scrollY") - y) <= 2, (y, page.evaluate("scrollY"))
        page.set_viewport_size({"width": 1500, "height": 1100})
        # A spot-check draws 10 of the question's automatic grades at random and steps through them in the desk.
        page.locator("[data-review-grade-summary] > summary").click()
        page.locator("[data-spot-check-link]").click()
        expect(page.locator("[data-spot-check]")).to_contain_text("Spot-check: 10 automatic grades of this assignment, drawn at random from 30 grades")
        expect(page.get_by_label("Response", exact=True).locator("option")).to_have_count(10)
        expect(page.get_by_label("Show responses", exact=True)).to_have_value("automatic")
        drawn = page.get_by_label("Response", exact=True).locator("option").evaluate_all("nodes => nodes.map(n => n.value)")
        assert len(set(drawn)) == 10 and set(drawn) <= {row["id"] for row in spot_rows}, drawn
        # Workspace settings' link opens Compute settings over Assignments (the Grading index is retired, 2026-10-18) and leaves the
        # address clean.
        page.goto(f"{address}/#grading?settings=1")
        expect(page.get_by_role("dialog", name="Compute settings", exact=True)).to_be_visible()
        assert "settings=1" not in page.url, page.url
        page.keyboard.press("Escape")
        expect(page.locator("#page-title")).to_have_text("Assignments")
        expect(page.locator("#grading-section")).not_to_be_visible()
        assert not writes, writes
        assert not errors, errors
        browser.close()
        print("Grading layout passed: 16 questions, separate versions, six desktop views, batch history, visible failures, disclosures and keyboard focus through polling.")
finally:
    if server:
        server.close()
    fixture.doCleanups()
