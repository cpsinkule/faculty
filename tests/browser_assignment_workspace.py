"""ASSIGNMENT-WORKSPACE: one assessment shell over the existing owning editors.

Run with ``python3 manage_checks.py browser --browser assignment_workspace``.
The catalog, templates, scans and model replies are synthetic; every runtime file
is under the runner's isolated NVMe scratch and no external service is called.
"""
import sys
import re
import json
import threading
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]

from playwright.sync_api import expect, sync_playwright  # noqa: E402
from waitress.server import create_server  # noqa: E402
from test_grading_templates import AssignmentTemplateWorkflow  # noqa: E402
from test_grading_locations import confirm_templates  # noqa: E402
from frq_fixtures import confirm_for_review  # noqa: E402
from frq_fixtures import page as page_image  # noqa: E402
from rosters import connect  # noqa: E402
from shell_checks import check_sentence_case  # noqa: E402


fixture = AssignmentTemplateWorkflow("test_uploaded_reference_waits_for_warning_before_any_reading")
fixture.setUp()
for letter in "AB":
    for number in (18, 19):
        fixture.upload(f"1_Question_{number}.csv", exam_version=letter)
fixture.template()
confirm_templates(fixture.frq, fixture.plan["id"])
for _ in range(40):
    if not fixture.frq.process_location_one():
        break
confirm_for_review(fixture.frq, fixture.plan["id"])
fixture.frq.sync_scan(fixture.job["message_id"])

server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
output = Path("test-results")
output.mkdir(exist_ok=True)
assignment = fixture.assignment["id"]
plan = fixture.plan["id"]
term = fixture.plan["term_id"]
canonical = f"#assignments/{assignment}/{term}"

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(15000)
        errors, bad, writes = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        page.on("request", lambda request: writes.append((request.method, request.url)) if request.method not in ("GET", "HEAD") else None)
        page.clock.install()

        def load(hash_value):
            page.goto(address + "/" + hash_value)

        shell = page.locator("#assignment-workspace")
        nav = shell.get_by_role("navigation", name="Assignment workspace", exact=True)

        nav_tops = []

        def assert_context(area):
            expect(shell).to_be_visible()
            expect(page.locator("#page-title")).to_have_text("Exam 1")
            expect(page.locator("#breadcrumb")).to_contain_text("Assignments")
            expect(nav.locator('[aria-current="page"]')).to_have_text(area)
            expect(nav.get_by_role("link", name="Needs you & progress", exact=True)).to_have_attribute("href", canonical)
            # No subtitle repeats the area or the title, so moving between the tabs never moves them under the pointer
            # (2026-10-04 review: the bar sat 61 px lower where the shell printed the area's name). Compared on each tab's own
            # page; an owner page reached another way may carry its own header row (an exam record's sentence, the
            # gradebook's Add column), arriving with its read.
            subtitle = page.locator("#page-subtitle")
            if subtitle.is_visible():
                assert subtitle.inner_text().strip() not in (area, "Exam 1"), ("the subtitle repeats", area)
            if page.evaluate("location.hash") == nav.locator('[aria-current="page"]').get_attribute("href"):
                nav_tops.append(nav.evaluate("node => Math.round(node.getBoundingClientRect().top + scrollY)"))
                assert len(set(nav_tops)) == 1, ("the area tabs moved", area, nav_tops)

        # The canonical list is also the destination of earlier section bookmarks.
        load("#assignments")
        rows = shell.locator("[data-assignment-list] .assignment-row")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("Exam 1")
        # The title is the row's one link (no second "Open assignment"); the whole row opens it.
        expect(rows.first.get_by_role("link")).to_have_count(1)
        expect(rows.first.get_by_role("link", name="Exam 1", exact=True)).to_have_attribute("href", canonical)
        expect(page.locator('.sidebar [data-view="assignments"]')).to_have_attribute("aria-current", "page")
        expect(page.locator('.sidebar [data-view="documents"], .sidebar [data-view="exams"], .sidebar [data-view="grading"]')).to_have_count(0)
        search = shell.get_by_role("searchbox", name="Search", exact=True)
        search.fill("Exam")
        page.evaluate("document.querySelector('[data-filter=\"query\"]').dataset.retainedProbe = 'typed'")
        with page.expect_response(lambda response: response.url.endswith("/api/assignments") and response.request.method == "GET"):
            page.clock.fast_forward(31000)
        expect(search).to_have_value("Exam")
        expect(search).to_have_attribute("data-retained-probe", "typed")
        expect(search).to_be_focused()
        expect(rows).to_have_count(1)

        # A failed summary read keeps the last list and typing, then recovers on its next poll.
        page.route("**/api/assignments", lambda route: route.fulfill(status=503, content_type="application/json",
            body=json.dumps({"error": "Synthetic summary refresh failure"})))
        with page.expect_response(lambda response: response.url.endswith("/api/assignments") and response.status == 503):
            page.clock.fast_forward(31000)
        expect(shell.locator("[data-assignment-error]")).to_contain_text("Assignment could not be refreshed")
        expect(search).to_have_value("Exam")
        expect(search).to_have_attribute("data-retained-probe", "typed")
        expect(search).to_be_focused()
        expect(rows).to_have_count(1)
        page.unroute("**/api/assignments")
        with page.expect_response(lambda response: response.url.endswith("/api/assignments") and response.status == 200):
            page.clock.fast_forward(31000)
        expect(shell.locator("[data-assignment-error]")).to_be_empty()
        search.fill("No matching assignment")
        expect(rows).to_have_count(0)
        expect(shell).to_contain_text("No assignments match")
        search.fill("")
        shell.locator('[data-filter="stage"]').select_option("preparation")
        expect(rows).to_have_count(1)
        shell.locator('[data-filter="stage"]').select_option("")

        # INDEX TABS (requirement, 2026-10-09): Pending, Certified and Archived replace the Status select, at the top of the list.
        # Pending holds the term's uncertified assignment; nothing is certified or of an earlier term yet.
        tabs = shell.get_by_role("group", name="Assignments by status", exact=True)
        expect(shell.locator('[data-filter="status"]')).to_have_count(0)
        expect(tabs.get_by_role("button")).to_have_text(["Pending 1", "Certified 0", "Archived 0"])
        expect(tabs.get_by_role("button", name="Pending 1", exact=True)).to_have_attribute("aria-pressed", "true")
        assert tabs.evaluate("node => node.getBoundingClientRect().bottom") <= shell.locator(".assignment-filters").evaluate(
            "node => node.getBoundingClientRect().top"), "the tabs sit above the filters"
        tabs.get_by_role("button", name="Certified 0", exact=True).click()
        expect(tabs.get_by_role("button", name="Certified 0", exact=True)).to_have_attribute("aria-pressed", "true")
        expect(rows).to_have_count(0)
        expect(shell.locator("[data-assignment-list]")).to_contain_text("Nothing certified yet")
        tabs.get_by_role("button", name="Archived 0", exact=True).click()
        expect(shell.locator("[data-assignment-list]")).to_contain_text("No earlier terms")
        tabs.get_by_role("button", name="Pending 1", exact=True).click()
        expect(rows).to_have_count(1)
        # THE BADGE CAN BE READ ON THE INDEX (2026-11-02): the cards the Assignments badge counts lead the page, by name, on every tab;
        # a card of another page stays out, and nothing is drawn when none waits.
        needs = shell.locator("[data-assignment-needs]")
        overview = page.request.get(address + "/api/workspace/overview").json()
        overview["attention"] = []
        page.route("**/api/workspace/overview", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(overview)))
        with page.expect_response(lambda response: response.url.endswith("/api/workspace/overview") and response.status == 200):
            page.clock.fast_forward(31000)
        expect(needs.get_by_role("heading")).to_have_count(0)
        overview["attention"] = [
            {"key": "grading:p:finalization", "kind": "review", "label": "Review the rubric check · Quiz 6", "count": 1, "href": "#grading/p?tab=answer-key&open=quality",
             "detail": "The rubric check finished. Review and approve it before grading.", "verb": "Review"},
            {"key": "absences", "kind": "review", "label": "Absences waiting for your decision", "count": 4, "href": "#absences?status=pending", "verb": "Decide"}]
        with page.expect_response(lambda response: response.url.endswith("/api/workspace/overview") and response.status == 200):
            page.clock.fast_forward(31000)
        expect(needs.get_by_role("heading", name="Needs you", exact=True)).to_be_visible()
        expect(needs.locator("li")).to_have_count(1)
        expect(needs.locator("li")).to_contain_text("Review the rubric check · Quiz 6")
        expect(needs.locator("li")).to_contain_text("The rubric check finished.")
        expect(needs.get_by_role("link", name="Review", exact=True)).to_have_attribute("href", "#grading/p?tab=answer-key&open=quality")
        expect(page.locator("#nav-badge-assignments")).to_contain_text("1")
        # A listing with a certified assignment, one whose certification changed (back in Pending) and two of earlier terms in two
        # courses (synthetic rows over the real one; nothing is written). The quiet refresh redraws the tabs in place.
        listing = page.request.get(address + "/api/assignments").json()
        real = listing["assignments"][0]
        certified_at = {"certified_at": "2026-10-09T15:00:00+00:00", "certification_id": "synthetic"}
        synthetic = lambda title, **over: {**real, "assignment_id": f"synthetic-{title}", "title": title, "href": f"#assignments/synthetic-{title}/t",
                                           "needs_you": [], "paper": {}, **over}
        listing["assignments"] = [
            {**real, "archived": certified_at, "summary": "Grades certified", "needs_you": []},
            synthetic("Quiz 3", summary="3 responses to review", workflow={"certification": {"state": "changed"}}),
            # Not started (no paper, only its setup ahead): its next step in muted words, no orange block.
            synthetic("Exam 8", summary="", needs_you=[{"key": "paper", "label": "Create or upload the assignment paper", "href": "#x", "count": None, "unit": "setup"},
                                                       {"key": "ready:print", "label": "No print jobs yet", "href": "#x", "count": None, "unit": "setup"}]),
            synthetic("Quiz 1", is_current=0, term="Spring", year=2026, term_label="Spring 2026", archived=certified_at, summary="Grades certified"),
            synthetic("Exam 9", is_current=0, definition_id="synthetic-course", code="MATH 151", term="Fall", year=2025, term_label="Fall 2025",
                      archived=None, summary="Prepare the paper and grading"),
        ]
        page.route("**/api/assignments", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(listing)))
        with page.expect_response(lambda response: response.url.endswith("/api/assignments") and response.status == 200):
            page.clock.fast_forward(31000)
        expect(tabs.get_by_role("button")).to_have_text(["Pending 2", "Certified 1", "Archived 2"])
        expect(rows).to_have_count(2)
        expect(rows.first).to_contain_text("Exam 8")
        expect(rows.first).to_contain_text("Create or upload the assignment paper")
        expect(rows.first.locator(".assignment-row-waits")).to_have_count(0)
        expect(rows.nth(1)).to_contain_text("Quiz 3")
        expect(rows.nth(1)).to_contain_text("Changed since certification")
        expect(rows.nth(1).get_by_role("link", name="Quiz 3", exact=True)).to_have_attribute("href", "#assignments/synthetic-Quiz 3/t")
        tabs.get_by_role("button", name="Certified 1", exact=True).click()
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("Exam 1")
        expect(rows.first).to_contain_text("Grades certified · Oct 9")
        # A certified row opens its certification on every student's grades; one back in Pending kept its overview (above).
        expect(rows.first.get_by_role("link", name="Exam 1", exact=True)).to_have_attribute("href", f"#grading/{plan}?tab=export&open=students")
        expect(rows.first.locator(".assignment-row-waits")).to_have_count(0)
        # Archived is stratified by course: each course a region under its code, its rows naming only their term.
        tabs.get_by_role("button", name="Archived 2", exact=True).click()
        courses = shell.locator("[data-assignment-list] > .assignment-block")
        expect(courses).to_have_count(2)
        expect(courses.get_by_role("heading", level=2)).to_have_text([real["code"], "MATH 151"])
        expect(courses.first.get_by_role("heading", level=3)).to_have_text(["Quiz 1"])
        expect(courses.first.locator(".assignment-row")).to_contain_text("Spring 2026")
        expect(courses.first.locator(".assignment-row")).not_to_contain_text(f"{real['code']} ·")
        expect(courses.nth(1).locator(".assignment-row")).to_contain_text("Exam 9")
        page.screenshot(path=str(output / "assignment-index-archived-1440.png"), full_page=True, animations="disabled")
        shell.locator('[data-filter="course"]').select_option(fixture.assignment["definition_id"])
        expect(tabs.get_by_role("button")).to_have_text(["Pending 2", "Certified 1", "Archived 1"])
        expect(courses).to_have_count(1)
        shell.locator('[data-filter="course"]').select_option("")
        tabs.get_by_role("button", name="Pending 2", exact=True).click()
        page.unroute("**/api/assignments")
        page.unroute("**/api/workspace/overview")
        with page.expect_response(lambda response: response.url.endswith("/api/assignments") and response.status == 200):
            page.clock.fast_forward(31000)
        expect(tabs.get_by_role("button")).to_have_text(["Pending 1", "Certified 0", "Archived 0"])
        expect(rows).to_have_count(1)

        # Creation still opens the owning wizard, with no write until a save.
        page.get_by_role("button", name="Create assignment", exact=True).click()
        page.get_by_role("button", name="Create paper and print", exact=True).click()
        expect(page.locator("#assignment-wizard")).to_be_visible()
        # It opens on the field to type into, not on its close button.
        expect(page.locator("#assignment-wizard").get_by_label("Assignment title", exact=True)).to_be_focused()
        page.keyboard.press("Escape")
        expect(page.locator("#assignment-wizard")).to_be_hidden()
        # The palette's New exam opens the exam record dialog over Assignments (a bare #exams redirects there).
        page.keyboard.press("Control+k")
        page.get_by_role("combobox", name="Go to a page, course, assignment or action").fill("New exam")
        page.keyboard.press("Enter")
        expect(page.locator("#exams-dialog")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#dialog")).to_be_hidden()
        assert page.evaluate("location.hash").startswith("#assignments")
        expect(shell.get_by_role("link", name="All exam records", exact=True)).to_have_attribute("href", "#exams?view=list")
        expect(shell.get_by_role("link", name="All print jobs", exact=True)).to_have_attribute("href", "#documents?tab=batches")
        # A click anywhere on the row opens it (its centre is the summary column, away from the title: the title's link
        # covers the row).
        rows.first.click()
        assert_context("Needs you & progress")
        # The overview (critique 2026-10-05): Needs you leads, Readiness follows, Running now shows only while something runs and
        # Recent activity is a fold at the end.
        for title in ("Needs you", "Readiness"):
            expect(shell.get_by_role("heading", name=title, exact=True)).to_be_visible()
        expect(shell.locator(".assignment-overview > :is(section, details)").first.get_by_role("heading")).to_have_text("Needs you")
        activity = shell.locator("details.assignment-activity")
        if activity.count():
            expect(activity).not_to_have_attribute("open", "")
            expect(activity.locator("summary")).to_contain_text("Recent activity")
        # The rows are named as the tabs are (2026-10-18).
        expect(shell.locator(".assignment-readiness dt")).to_have_text([re.compile(r"Paper$"), re.compile(r"Print$"), re.compile(r"Collect$"),
                                                                        re.compile(r"Grade$"), re.compile(r"Certify$"), re.compile(r"Return$")])
        # What runs by itself (item 9): one muted line from the Compute settings, opening them.
        automatic = shell.locator("[data-assignment-automatic]")
        expect(automatic).to_contain_text("Runs by itself: grading")
        expect(automatic.get_by_role("link", name="Compute settings", exact=True)).to_have_attribute("href", f"#grading/{plan}?tab=jobs&settings=1")
        # The quiet refresh keeps the delivery receipts read on opening (?delivery=0); Refresh progress reads them again. The page reads
        # them after drawing (2026-11-01), from Return's own read: the detail is always asked for without them.
        detail_url = f"/api/assignments/{assignment}/{term}"
        receipts_url = f"/api/teaching-review/{plan}/delivery"
        receipts = []
        page.on("request", lambda request: receipts.append(request.url) if request.url.endswith(receipts_url) else None)
        with page.expect_request(lambda request: request.url.endswith(detail_url + "?delivery=0")):
            page.clock.fast_forward(31000)
        expect(shell.locator(".assignment-readiness")).to_contain_text("gradebook column")
        quiet = len(receipts)
        with page.expect_request(lambda request: request.url.endswith(detail_url + "?delivery=0")), \
                page.expect_request(lambda request: request.url.endswith(receipts_url)):
            shell.get_by_role("button", name="Refresh progress", exact=True).click()
        expect(shell.locator(".assignment-readiness")).to_contain_text("gradebook column")
        assert len(receipts) == quiet + 1, receipts  # the quiet refresh read none

        # All three document actions use the existing assignment and term.
        nav.get_by_role("link", name="Create", exact=True).click()
        assert_context("Create")
        expect(shell.get_by_role("heading", name="Paper and printing", exact=True)).to_be_visible()
        for action in ("Add or revise paper", "Print copies", "Seating and arrangements"):
            shell.get_by_role("button", name=action, exact=True).click()
            expect(page.locator("#assignment-wizard")).to_be_visible()
            expect(page.locator("#assignment-wizard")).to_contain_text("Exam 1")
            page.keyboard.press("Escape")
            expect(page.locator("#assignment-wizard")).to_be_hidden()
            assert_context("Create")
        # Quiet refreshes keep the tool buttons in place; a click still runs its action once.
        for _ in range(2):
            with page.expect_response(lambda response: "/api/canvas/content/" in response.url):
                page.clock.fast_forward(31000)
            page.wait_for_timeout(200)
        exam_reads = []
        page.on("request", lambda request: exam_reads.append(request.url) if "/api/exams?summary=0" in request.url else None)
        shell.get_by_text("Assignment tools", exact=True).click()
        shell.locator("[data-workspace-rename]").click()
        expect(page.locator("#assignment-rename-form").get_by_label("Title", exact=True)).to_have_value("Exam 1")
        page.keyboard.press("Escape")
        expect(page.locator("#dialog")).to_be_hidden()
        shell.locator("[data-workspace-given-on]").click()
        expect(page.locator("#assignment-given-on-form").get_by_label("Given on", exact=True)).to_have_value("")
        page.wait_for_timeout(300)
        assert len(exam_reads) == 1, exam_reads
        page.keyboard.press("Escape")
        expect(page.locator("#dialog")).to_be_hidden()
        assert_context("Create")

        # Prepare mounts the real rubric/answer-area editor inside this shell.
        nav.get_by_role("link", name="Prepare", exact=True).click()
        assert_context("Prepare")
        expect(shell.locator("[data-assignment-editor] > #grading-section")).to_be_visible()
        board = shell.locator("[data-setup-questions]")
        expect(board.locator("[data-question-choice]")).to_have_count(4)
        shown = board.locator("[data-question-panel]:visible")
        expect(shown).to_have_count(1)
        expect(shown.get_by_role("heading", name="Question and solution", exact=True)).to_be_visible()
        expect(shown.locator("[data-location-check]")).to_be_visible()
        page.get_by_role("button", name="Next question →", exact=True).click()
        selected = shown.get_attribute("data-question-panel")
        refresh = shell.locator('#grading-section > .grading-heading').get_by_role("button", name="Refresh", exact=True)
        with page.expect_response(lambda response: f"/api/grading/assignments/{plan}?include=activity" in response.url):
            refresh.click()
        # Type only once that refresh has drawn the step: a fill racing its redraw fills a box the redraw has just replaced
        # (its draft is kept, but the box on screen was drawn before it; 1 of 113 under 8-way load, 2026-10-03).
        expect(shell.locator("#grading-section > .grading-content")).not_to_have_attribute("aria-busy", "true")
        expect(shown).to_have_attribute("data-question-panel", selected)

        # The owner's in-memory rubric draft and question survive leaving and returning.
        wording = shown.locator('textarea[name^="description:"]').first
        wording.fill("Unsaved wording retained while checking grading")
        page.clock.fast_forward(31000)
        expect(wording).to_have_value("Unsaved wording retained while checking grading")
        page.once("dialog", lambda dialog: dialog.accept())
        nav.get_by_role("link", name="Grade", exact=True).click()
        assert_context("Grade")
        nav.get_by_role("link", name="Prepare", exact=True).click()
        assert_context("Prepare")
        expect(shown).to_have_attribute("data-question-panel", selected)
        expect(wording).to_have_value("Unsaved wording retained while checking grading")
        shown.get_by_role("button", name="Discard changes", exact=True).click()

        # An answer-key draft survives a background tick; dismissing it writes nothing.
        shown.get_by_role("link", name="Answer key and rubric finalization", exact=True).click()
        assert_context("Prepare")
        expect(shown).to_have_attribute("data-question-panel", selected)
        shown.get_by_role("button", name="Edit this answer key", exact=True).click()
        editor = page.get_by_label("Accepted final answers", exact=True)
        editor.fill("x=42")
        page.clock.fast_forward(31000)
        expect(editor).to_have_value("x=42")
        expect(editor).to_be_focused()
        page.keyboard.press("Escape")
        expect(page.locator("#dialog")).to_be_hidden()

        nav.get_by_role("link", name="Grade", exact=True).click()
        assert_context("Grade")
        expect(shell.locator("[data-assignment-editor] > #grading-section")).to_be_visible()
        expect(shell.get_by_role("button", name="Grade new responses", exact=True)).to_be_visible()
        # The grading page's own links (All assignments, Scan records, ...) are go-links: sentence case (DESIGN-LANGUAGE §1.12).
        check_sentence_case(page, "Grade inside Assignments")
        # Moving between areas keeps the owner editor in the shell (never moved out and back) and focus on the link.
        page.evaluate("""() => { window.ownerMoves = 0; const owner = document.querySelector('#grading-section');
          new MutationObserver(list => { for (const m of list) for (const n of m.addedNodes) if (n === owner) window.ownerMoves++; })
            .observe(document.body, {childList: true, subtree: true}); }""")
        nav.get_by_role("link", name="Certify", exact=True).focus()
        page.keyboard.press("Enter")
        assert_context("Certify")
        expect(nav.get_by_role("link", name="Certify", exact=True)).to_be_focused()
        assert page.evaluate("window.ownerMoves") == 0
        expect(shell.locator("[data-assignment-editor] > #grading-section")).to_be_visible()
        nav.get_by_role("link", name="Return", exact=True).click()
        assert_context("Return")
        expect(shell.locator("[data-assignment-editor] > #teaching-review-section")).to_be_visible()
        shell.locator('.support-tabs').get_by_role('link', name='Teaching review', exact=True).click()
        assert_context('Return')
        expect(shell.locator('[data-review-assessment]')).to_be_hidden()
        expect(page.locator('.sidebar [data-view="assignments"]')).to_have_attribute('aria-current', 'page')
        shell.locator('.support-tabs').get_by_role('link', name='Grade delivery', exact=True).click()
        assert_context('Return')
        expect(shell.get_by_role("heading", name="Delivery checklist", exact=True)).to_be_visible()
        # EACH CANVAS SITUATION BY ITSELF (2026-10-11): with no column and no send the line says so, never "0 accepted cell receipts".
        expect(shell.locator('.delivery-checklist dl > div').filter(has_text='Sent to Canvas')).to_contain_text('Nothing sent')
        # POSTED IN CANVAS (2026-10-11): its own line, which says the gradebook has not read Canvas for it yet, with the gradebook's link.
        posted_line = shell.locator('.delivery-checklist dl > div').filter(has_text='Posted in Canvas')
        expect(posted_line).to_contain_text('Not read from Canvas yet')
        expect(posted_line.get_by_role('link')).to_have_count(0)  # one gradebook link, on "Recorded locally" (C2, 2026-10-18)
        expect(shell.locator('.delivery-checklist').get_by_role('link', name='Choose a gradebook column')).to_have_attribute('href', f'#gradebook/{term}?assignment={assignment}')

        nav.get_by_role("link", name="Collect", exact=True).click()
        assert_context("Collect")
        expect(shell.get_by_role("heading", name="Collected papers", exact=True)).to_be_visible()
        expect(shell.get_by_role("button", name="Upload scans", exact=True)).to_be_visible()
        exceptions = shell.locator('[data-collection-exceptions]')
        # THE ZEROS ARE SAID AT CERTIFY (2026-11-05): a paper missing is a plain count in the lead and nobody needs attention.
        expect(exceptions.get_by_role('heading', name='Students', exact=True)).to_be_visible()
        expect(shell.locator('.assignment-lead')).to_contain_text('· 1 without a scanned paper')
        expect(shell.locator('.assignment-lead strong')).to_have_count(0)
        expect(nav.get_by_role("link", name="Collect", exact=True)).not_to_have_attribute("data-mark", "needs")
        expect(exceptions.locator('[data-collection-choice][data-needs]')).to_have_count(0)
        # COLLECT'S ACTION IS IN THE ROW (2026-10-11): with a paper missing the exam record's collection is still linked, the caution
        # is said once, and the student without a paper is decided in their own row with nothing to unfold.
        expect(shell.get_by_role('link', name='Student collection record', exact=True)).to_have_attribute('href', f"#exams/{fixture.event['id']}?tab=collection")
        expect(shell.locator('.button.primary:visible')).to_have_count(0)
        # I1: an assignment's Upload scans opens Collect's own upload in place, once; the address drops the request first.
        load(f"#assignments/{assignment}/{term}?area=collect&upload=1")
        expect(page.locator('#inbox-scan-upload')).to_be_visible()
        assert page.evaluate("location.hash") == f"#assignments/{assignment}/{term}?area=collect"
        page.locator('#inbox-scan-upload').get_by_role('button', name='Close', exact=True).click()
        expect(page.locator('#inbox-scan-upload')).to_be_hidden()
        assert_context("Collect")
        # I2: `scans=waiting` brings the list of scans still in their scan checks into view (none wait here, and it says so).
        load(f"#assignments/{assignment}/{term}?area=collect&scans=waiting")
        expect(shell.locator('[data-assignment-waiting-scans]')).to_be_visible()
        expect(page.locator('.sidebar [data-view="assignments"]')).to_have_attribute('aria-current', 'page')
        assert_context("Collect")
        exceptions = shell.locator('[data-collection-exceptions]')
        expect(exceptions.get_by_role('heading', name='Students', exact=True)).to_be_visible()
        expect(exceptions.get_by_text('A missing paper alone is not an absence or an excuse', exact=False)).to_have_count(1)
        exceptions.get_by_label('Papers', exact=True).select_option('no_scan')
        exceptions.locator('[data-collection-choice]').first.click()
        correction = exceptions.locator('.collection-student').first
        expect(correction).to_contain_text('No scanned paper')
        expect(correction.locator('summary')).to_have_count(0)
        form = correction.locator('form')
        expect(form.locator('[name=expectation]')).to_be_visible()
        expect(form.get_by_role('button', name='Save expectation', exact=True)).to_be_visible()
        # ASSIGNMENT-WORKSPACE COMPACT COLLECT: selection and filters retain drafts by permanent membership.
        choices = exceptions.locator('[data-collection-choice]')
        missing_key = choices.first.get_attribute('data-collection-choice')
        form.locator('[name=reason]').fill('Unsaved collection decision')
        exceptions.get_by_label('Papers', exact=True).select_option('')
        expect(choices).to_have_count(2)
        complete_choice = choices.filter(has_text='Complete paper').first
        complete_key = complete_choice.get_attribute('data-collection-choice')
        complete_choice.click()
        selected = exceptions.locator('.collection-student')
        expect(selected).to_contain_text('Complete paper')
        selected.locator('[name=reason]').fill('Checked her paper')
        saved = lambda response: response.request.method == 'PATCH' and response.status == 200
        for round_, words in enumerate(('Checked her paper', 'Checked it twice'), start=2):
            selected.locator('[name=reason]').fill(words)
            with page.expect_response(saved):
                selected.get_by_role('button', name='Save expectation', exact=True).click()
            expect(selected.locator('form')).to_have_attribute('data-version', str(round_))
            expect(selected.get_by_role('button', name='Save expectation', exact=True)).to_be_disabled()
        selected.locator('[name=reason]').fill('Never saved')
        exceptions.locator(f'[data-collection-choice="{missing_key}"]').click()
        form = selected.locator('form')
        expect(form.locator('[name=reason]')).to_have_value('Unsaved collection decision')
        exceptions.get_by_label('Find a student', exact=True).fill('No matching student')
        expect(choices).to_have_count(0)
        expect(form.locator('[name=reason]')).to_have_value('Unsaved collection decision')
        expect(exceptions.locator('[data-collection-outside]')).to_be_visible()
        exceptions.get_by_label('Find a student', exact=True).fill('')
        page.clock.fast_forward(31000)
        expect(form.locator('[name=reason]')).to_have_value('Unsaved collection decision')
        original_expectation = form.locator('[name=expectation]').input_value()
        person = next(p for p in fixture.exams.get(fixture.event['id'])['participants']
            if p['section_id'] == form.get_attribute('data-section') and p['enrollment_id'] == form.get_attribute('data-collection-student'))
        fixture.app.extensions['exams'].update_participant(fixture.event['id'], person['section_id'], person['enrollment_id'],
            {'version': person['version'], 'expectation': original_expectation, 'reason': 'Concurrent decision'})
        with page.expect_response(lambda response: response.status == 409):
            form.get_by_role('button', name='Save expectation', exact=True).click()
        expect(form.locator('.form-error')).to_contain_text('changed')
        expect(form.locator('[name=reason]')).to_have_value('Unsaved collection decision')
        form.get_by_role('button', name='Discard changes and reload', exact=True).click()
        expect(selected.locator('[name=reason]')).to_have_value('Concurrent decision')
        exceptions.locator(f'[data-collection-choice="{complete_key}"]').click()
        expect(selected.locator('[name=reason]')).to_have_value('Never saved')
        selected.get_by_role('button', name='Discard changes and reload', exact=True).click()
        expect(selected.locator('[name=reason]')).to_have_value('Checked it twice')
        expect(exceptions).not_to_have_attribute('data-collection-dirty', '')
        for width in (1440, 1280):
            page.set_viewport_size({'width': width, 'height': 1000})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(output / f'assignment-collect-compact-{width}.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1000})
        # An interrupted read retains the desk and provides an actual retry on this page.
        fail_exam = lambda route: route.fulfill(status=503, json={'error': 'Synthetic collection read failed'})
        page.route(f"**/api/exams/{fixture.event['id']}", fail_exam)
        with page.expect_response(lambda response: response.status == 503):
            page.clock.fast_forward(31000)
        expect(exceptions.get_by_role('button', name='Try again', exact=True)).to_be_visible()
        page.unroute(f"**/api/exams/{fixture.event['id']}", fail_exam)
        exceptions.get_by_role('button', name='Try again', exact=True).click()
        expect(exceptions.get_by_role('button', name='Try again', exact=True)).to_have_count(0)
        bad[:] = [entry for entry in bad if entry[0] != 409 and entry != (503, f"{address}/api/exams/{fixture.event['id']}")]
        writes.clear()  # The two saves and the deliberate, rejected correction above are the only expected writes here.
        shell.get_by_text('All scans', exact=True).click()
        scan = shell.locator(f'a[href="#inbox/job/{fixture.job["id"]}"]:visible')
        scan.first.click()
        assert_context("Collect")
        expect(shell.locator("[data-assignment-editor] > #inbox-section")).to_be_visible()
        nav.get_by_role("link", name="Needs you & progress", exact=True).click()
        assert_context("Needs you & progress")

        # UPLOAD THE SCANS / NOTHING SCANNED YET (2026-10-11): the same two reads with no paper back and the server's item (its rule
        # is test_assignment_workspace's). The overview lists it, Collect's tab carries it, and Collect leads with the papers
        # expected and Upload scans as its one primary: nobody is "without a scanned paper" and no row opens its decision.
        def unscanned_detail(route):
            response = route.fetch()
            body = response.json()
            body['scans'] = []
            body['workflow']['collection'] = {**{key: 0 for key in body['workflow']['collection']}, 'no_scan': 2}
            for stage in body['lifecycle']['stages']:
                if stage['key'] == 'collected':
                    stage.update(state='idle', detail='No papers scanned · 2 students expected', counts={'expected': 2, 'received': 0})
            body['needs_you'] = [{'key': 'upload', 'label': 'Upload the scans', 'href': canonical + '?area=collect', 'count': None, 'unit': 'papers'}]
            route.fulfill(response=response, json=body)

        def unscanned_exam(route):
            response = route.fetch()
            body = response.json()
            for student in body['reconciliation']['students']:
                student.pop('assembly_revision_id', None)
                student.update(status='no_scan', copies=[], issues=[], missing_pages=[])
            route.fulfill(response=response, json=body)

        page.route(f"**/api/assignments/{assignment}/{term}*", unscanned_detail)
        page.route(f"**/api/exams/{fixture.event['id']}", unscanned_exam)
        shell.get_by_role("button", name="Refresh progress", exact=True).click()
        expect(shell.get_by_role("link", name="Upload the scans", exact=True)).to_have_attribute("href", canonical + "?area=collect")
        collect_tab = nav.get_by_role("link", name="Collect", exact=True)
        expect(collect_tab).to_have_attribute("data-mark", "needs")
        expect(collect_tab).to_have_attribute("title", "Collect: 1 decision waits for you")
        shell.get_by_role("link", name="Upload the scans", exact=True).click()
        assert_context("Collect")
        expect(shell.locator('.assignment-lead')).to_have_text('No papers scanned · 2 students expected')
        expect(shell.locator('.button.primary:visible')).to_have_count(1)
        expect(shell.get_by_role("button", name="Upload scans", exact=True)).to_have_class(re.compile(r"\bprimary\b"))
        expect(exceptions.get_by_role('heading', name='No papers scanned yet', exact=True)).to_be_visible()
        expect(exceptions.get_by_role('heading', name=re.compile('Students needing attention'))).to_have_count(0)
        expect(exceptions.locator('form:visible')).to_have_count(1)
        exceptions.get_by_label('Papers', exact=True).select_option('')
        expect(exceptions.locator('[data-collection-choice]')).to_have_count(2)
        expect(exceptions.locator('[data-collection-choice][data-needs]')).to_have_count(0)
        page.unroute(f"**/api/assignments/{assignment}/{term}*")
        page.unroute(f"**/api/exams/{fixture.event['id']}")
        load(canonical)
        assert_context("Needs you & progress")
        expect(shell.get_by_role("link", name="Upload the scans", exact=True)).to_have_count(0)

        # A MAKEUP VERSION WAITING TO BE APPROVED (2026-11-05; its rule is test_makeup_drafts's): the overview lists it, Create's tab carries
        # it, its row opens the draft window once over Create, whose button stays in view with the address dropping the request first.
        def makeup_detail(route):
            response = route.fetch()
            body = response.json()
            body['needs_you'] = [{'key': 'makeup', 'label': 'Approve makeup version E', 'href': canonical + '?area=create&makeup=1', 'count': None, 'unit': 'setup'}]
            route.fulfill(response=response, json=body)

        page.route(f"**/api/assignments/{assignment}/{term}*", makeup_detail)
        shell.get_by_role("button", name="Refresh progress", exact=True).click()
        create_tab = nav.get_by_role("link", name="Create", exact=True)
        expect(create_tab).to_have_attribute("data-mark", "needs")
        shell.get_by_role("link", name="Approve makeup version E", exact=True).click()
        expect(page.locator('#makeup-drafts')).to_be_visible()
        assert page.evaluate("location.hash") == canonical + "?area=create"
        page.locator('#makeup-drafts').get_by_role('button', name='Close', exact=True).click()
        expect(page.locator('#makeup-drafts')).to_be_hidden()
        assert_context("Create")
        expect(shell.get_by_role("button", name="Approve makeup version E…", exact=True)).to_be_visible()
        expect(shell.locator('[data-workspace-makeup]')).to_have_count(1)

        # NO COPIES TO ASK FOR (GRADING "MAKEUP VERSIONS", 2026-11-05): with booking cards on the approval window has no copies field and
        # sends 0; without cards it keeps "Copies to print", one for each student who owes the makeup.
        cards = [True]
        makeup_routes = {
            "**/api/grading/assignments/*/makeups": {"event_id": "e1", "title": "Quiz 7", "drafts": [{"id": "d1", "state": "ready"}]},
            "**/api/quiz-drafts/d1": {"id": "d1", "state": "ready", "makeup": {"letter": "E", "source": "A"}, "live": None, "approved": None, "approvals": [],
                                      "latest_complete": "r1", "revisions": [{"id": "r1", "number": 1, "status": "complete",
                                                                              "result": {"versions": {"E": {"pages": [], "page_count": 2}}, "quiz": {"problems": [], "total": 10}, "rubrics": []}}]},
            "**/api/exams/e1": {"term_id": "t1", "sittings": []},
        }
        def answers(answer):
            return lambda route: route.fulfill(json=answer)

        for pattern, answer in makeup_routes.items():
            page.route(pattern, answers(answer))
        page.route("**/api/absences/makeups*", lambda route: route.fulfill(json={
            "makeups": [{"event_id": "e1", "term_id": "t1", "state": "owed", "name": "Student One", "section": 501, "section_id": "s1", "enrollment_id": "n1"},
                        {"event_id": "e1", "term_id": "t1", "state": "owed", "name": "Student Two", "section": 501, "section_id": "s1", "enrollment_id": "n2"}],
            "booking": {"terms": {"t1": {"cards": cards[0]}}}}))
        shell.get_by_role("button", name="Approve makeup version E…", exact=True).click()
        approve = page.locator('#makeup-approve')
        expect(approve.get_by_text("students book their own time on their card", exact=False).first).to_be_visible()
        expect(approve.get_by_role("spinbutton")).to_have_count(0)
        expect(approve.get_by_text("Extra unnamed copies")).to_have_count(0)
        expect(approve.locator('[data-approve-summary]')).to_contain_text("prints no copy now")
        page.locator('#makeup-drafts').get_by_role('button', name='Close', exact=True).click()
        cards[0] = False
        shell.get_by_role("button", name="Approve makeup version E…", exact=True).click()
        expect(approve.get_by_label("Copies to print")).to_have_value("2")
        expect(approve.locator('[data-approve-summary]')).to_contain_text("prints 2 unnamed copies")
        page.locator('#makeup-drafts').get_by_role('button', name='Close', exact=True).click()

        # A SCRAPPED DRAFT LEAVES NO FOOTER BUTTONS: with the decision forms in view the footer's two ways are hidden; scrapping the draft in
        # place draws the create form, where neither has a form to bring into view, so neither comes back.
        scrapped = [False]
        def scrapping(route):
            scrapped[0] = True
            route.fulfill(json={"ok": True})

        page.route("**/api/grading/assignments/*/makeups", lambda route: route.fulfill(json={
            "event_id": "e1", "title": "Quiz 7", "drafts": [{"id": "d1", "state": "scrapped" if scrapped[0] else "ready"}],
            "letter": "E", "sources": [{"key": "a", "label": "A", "problems": 1, "points": 5}], "model": "m", "certified": False}))
        page.route("**/api/quiz-drafts/d1/scrap", scrapping)
        shell.get_by_role("button", name="Approve makeup version E…", exact=True).click()
        jumps = page.locator('#makeup-drafts [data-makeup-jump]')
        expect(approve).to_be_visible()
        expect(jumps).to_have_count(2)
        for index in range(2):
            expect(jumps.nth(index)).to_be_hidden()
        page.locator('#makeup-drafts [data-makeup-scrap]').click()
        page.locator('#makeup-drafts [data-makeup-scrap-yes]').click()
        expect(page.locator('#makeup-create')).to_be_visible()
        page.wait_for_timeout(500)  # an observer's late report of the removed form
        for index in range(2):
            expect(jumps.nth(index)).to_be_hidden()
        page.locator('#makeup-drafts').get_by_role('button', name='Close', exact=True).click()
        page.unroute("**/api/quiz-drafts/d1/scrap")
        writes.clear()  # The scrap above was answered by the route, never sent.
        for pattern in [*makeup_routes, "**/api/absences/makeups*"]:
            page.unroute(pattern)
        page.unroute(f"**/api/assignments/{assignment}/{term}*")
        load(canonical)
        assert_context("Needs you & progress")

        # WHY GRADING WAITS and the Delivery row's Canvas (2026-10-11), drawn from the overview's read with responses waiting (the
        # field's own rule is test_assignment_workspace's): a reason that is the instructor's reads in ink and opens the Grade step;
        # a batch that starts by itself is said quietly, with no link.
        waiting_state = {'enabled': True, 'paused': 'cancelled', 'not_started': None, 'settle_seconds': 120}

        def waiting_detail(route):
            response = route.fetch()
            body = response.json()
            body['workflow']['new_responses'] = 12
            body['auto_grade'] = dict(waiting_state)
            body['needs_you'] = [item for item in body['needs_you'] if item['key'] != 'preparation']
            route.fulfill(response=response, json=body)

        def waiting_receipts(route):
            response = route.fetch()
            body = response.json()
            body['columns'] = [{'id': 'column', 'title': 'Exam 1', 'recorded_cells': 30, 'without_receipt': 3,
                                'receipts': {'accepted': 24, 'changed': 2, 'uncertain': 1}, 'last_receipt': None}]
            route.fulfill(response=response, json=body)

        page.route(f"**/api/assignments/{assignment}/{term}*", waiting_detail)
        page.route(f"**/api/teaching-review/{plan}/delivery", waiting_receipts)
        shell.get_by_role("button", name="Refresh progress", exact=True).click()
        wait = shell.locator('.assignment-wait')
        expect(wait).to_have_attribute('data-yours', '')
        expect(wait).to_contain_text('Automatic grading is paused because the last batch was cancelled. Starting a batch yourself resumes it.')
        expect(wait.get_by_role('link', name='Open Grade', exact=True)).to_have_attribute('href', f'#grading/{plan}?tab=jobs')
        expect(shell.locator('.assignment-waiting')).to_contain_text('12 responses ready for grading')
        expect(shell.locator('.assignment-readiness')).to_contain_text('In 1 gradebook column · sent to Canvas, 6 to check · not published on Gradescope')
        waiting_state['paused'] = None
        shell.get_by_role("button", name="Refresh progress", exact=True).click()
        expect(wait).to_contain_text('Grading starts by itself about 2 minutes after')
        expect(wait).not_to_have_attribute('data-yours', '')
        expect(wait.get_by_role('link')).to_have_count(0)
        page.unroute(f"**/api/assignments/{assignment}/{term}*")
        page.unroute(f"**/api/teaching-review/{plan}/delivery")
        shell.get_by_role("button", name="Refresh progress", exact=True).click()
        expect(shell.locator('.assignment-readiness')).to_contain_text('Not in a gradebook column yet · not published on Gradescope')

        # Canonical query entry and direct old deep links retain the same context.
        for old, area, owner in (
            (f"#grading/{plan}?tab=locations", "Prepare", "grading"),
            (f"#exams/{fixture.event['id']}?tab=collection", "Collect", "exams"),
            (f"#grade-delivery/{plan}", "Return", "teaching-review"),
        ):
            load(old)
            assert_context(area)
            expect(shell.locator(f"[data-assignment-editor] > #{owner}-section")).to_be_visible()
        for old in ("#documents", "#exams", "#grading"):
            load(old)
            expect(page.locator("#page-title")).to_have_text("Assignments")
            assert page.evaluate("location.hash").startswith("#assignments")
            if old == "#grading":
                expect(shell.locator('[data-filter="stage"]')).to_have_value("grading")
        # The full lists keep their own addresses, so Home, Settings and Assignments can link them.
        load("#exams?view=list")
        expect(page.locator("#page-title")).to_have_text("Exams")
        expect(shell).to_be_hidden()
        load("#documents?tab=batches&status=attention")
        expect(page.locator("#document-tab-batches")).to_have_attribute("aria-selected", "true")
        expect(page.locator("#document-status")).to_have_value("attention")
        assert page.evaluate("location.hash") == "#documents?tab=batches&status=attention"
        # I7 (2026-10-18): the Grading index is retired, so the strip no longer links "All grading"; the unscoped Teaching review,
        # which left the sidebar, is linked there instead.
        load("#assignments")
        every = shell.get_by_role("navigation", name="Every record")
        expect(every.get_by_role("link", name="All grading", exact=True)).to_have_count(0)
        expect(every.get_by_role("link", name="Teaching review", exact=True)).to_have_attribute("href", "#teaching-review")
        # A past term's course page opens the course's Archived tab; links that named the Status select still land on its tab.
        tabs = shell.get_by_role("group", name="Assignments by status", exact=True)
        load(f"#assignments?course={fixture.assignment['definition_id']}&status=archived")
        expect(shell.locator('[data-filter="course"]')).to_have_value(fixture.assignment["definition_id"])
        expect(tabs.get_by_role("button", name="Archived 0", exact=True)).to_have_attribute("aria-pressed", "true")
        load(f"#assignments?course={fixture.assignment['definition_id']}&status=current")
        expect(tabs.get_by_role("button", name="Pending 1", exact=True)).to_have_attribute("aria-pressed", "true")
        load(f"#assignments?course={fixture.assignment['definition_id']}&status=all")
        expect(tabs.get_by_role("button", name="Archived 0", exact=True)).to_have_attribute("aria-pressed", "true")

        load(canonical)
        assert_context("Needs you & progress")
        for width in (1440, 1280):
            page.set_viewport_size({"width": width, "height": 1000})
            page.screenshot(path=str(output / f"assignment-workspace-{width}.png"), full_page=True, animations="disabled")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width

        # A linked gradebook column keeps Return through the older URL's course redirect.
        linked = fixture.app.extensions["gradebook"].link_assignment(term, {"assignment_id": assignment, "request_id": str(uuid.uuid4())})
        load(f"#gradebook/{term}?item={linked['item_id']}")
        assert_context("Return")
        expect(shell.locator("[data-assignment-editor] > #course-detail")).to_be_visible()
        assert page.evaluate("location.hash").startswith("#course/")
        load(f"#gradebook/{term}")
        expect(shell).to_be_hidden()
        assert not errors, errors
        assert bad == [(503, address + "/api/assignments")], bad
        assert not writes, writes  # ASSIGNMENT-WORKSPACE: navigation and summary refresh remain read-only.

        # An association changes the current scan's assignment without a hash change or a remount.
        arrival = fixture.inbox.submit({"action": "scan.receive"}, [{"filename": "unlabeled.pdf", "content": b"%PDF-unlabeled-workspace"}],
            source="email", sender="synthetic-scanner", original=b"synthetic scan", idempotency_key=str(uuid.uuid4()),
            authenticated=True, instructor_review_required=True)
        arrival = fixture.inbox.get(arrival["id"])
        original = next(artifact for artifact in arrival["artifacts"] if artifact["role"] == "input")
        fixture.scans._retain_page(arrival, original, {"index": 1, "pdf": b"synthetic-page", "png": page_image(),
            "decoder": {}, "blank": False, "candidates": []})
        with connect(fixture.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (arrival["message_id"],))
        another = fixture.documents.create_assignment(fixture.assignment["definition_id"], "another-assignment", "Another assignment")
        scan_route = f"#inbox/job/{arrival['id']}/assignment"
        load(scan_route)
        expect(page.get_by_role("button", name="Choose assignment", exact=True)).to_be_visible()
        page.evaluate("window.assignmentEvents = []; document.addEventListener('faculty:scan-association-changed', event => window.assignmentEvents.push(event.detail))")
        scan_editor = page.locator(".document-scan-review")
        scan_editor.evaluate("node => node.dataset.retainedProbe = 'same-scan-editor'")
        page.get_by_role("button", name="Choose assignment", exact=True).click()
        page.locator("#dialog").get_by_label("Assignment", exact=True).select_option(assignment)
        page.locator("#dialog").get_by_role("button", name="Save assignment", exact=True).click()
        expect(page.locator("#dialog")).to_be_hidden()
        assert_context("Collect")
        expect(scan_editor).to_have_attribute("data-retained-probe", "same-scan-editor")
        assert page.evaluate("location.hash") == scan_route
        page.get_by_role("button", name="Change assignment", exact=True).click()
        expect(page.locator("#dialog")).to_be_visible()
        page.locator("#dialog").get_by_label("Assignment", exact=True).select_option(another["id"])
        expect(page.locator("#dialog").get_by_label("Assignment", exact=True)).to_have_value(another["id"])
        with page.expect_response(lambda response: response.url.endswith("/associate") and response.request.method == "POST") as changed:
            page.locator("#dialog").get_by_role("button", name="Save assignment", exact=True).click()
        assert changed.value.json()["plan"]["source_assignment_id"] == another["id"], changed.value.json()
        expect(page.locator("#dialog")).to_be_hidden()
        try:
            expect(page.locator("#page-title")).to_have_text("Another assignment")
        except AssertionError:
            print("Association context diagnostic:", json.dumps({
                "events": page.evaluate("window.assignmentEvents"),
                "hash": page.evaluate("location.hash"),
                "scan": scan_editor.locator(".scan-association").inner_text(),
                "nav": nav.inner_text(),
                "server_plan": fixture.frq.scan_context(arrival["message_id"])["plan"],
            }))
            raise
        expect(nav.get_by_role("link", name="Needs you & progress", exact=True)).to_have_attribute("href", f"#assignments/{another['id']}/{term}")
        expect(scan_editor).to_have_attribute("data-retained-probe", "same-scan-editor")
        expect(shell.locator("[data-assignment-editor] > #inbox-section")).to_be_visible()
        assert page.evaluate("location.hash") == scan_route
        assert writes == [("POST", f"{address}/api/grading/scans/{arrival['message_id']}/associate")] * 2, writes

        # Unlinking the current gradebook column clears only the surrounding assignment context.
        load(f"#gradebook/{term}?item={linked['item_id']}")
        assert_context("Return")
        course_editor = page.locator("#course-detail")
        course_editor.evaluate("node => node.dataset.retainedProbe = 'same-course-editor'")
        gradebook_route = page.evaluate("location.hash")
        course_editor.locator(f'th.gb-head[data-item="{linked["item_id"]}"] [data-column-menu]').click()
        page.locator("#gradebook-column-menu").get_by_role("button", name="Edit column", exact=True).click()
        column_form = page.locator("#gradebook-column-form")
        column_form.locator('[data-field="col.assignment"]').select_option("")
        column_form.get_by_role("button", name="Save column", exact=True).click()
        expect(column_form).to_be_hidden()
        expect(shell).to_be_hidden()
        expect(course_editor).to_be_visible()
        expect(course_editor).to_have_attribute("data-retained-probe", "same-course-editor")
        assert page.evaluate("location.hash") == gradebook_route
        assert writes[-1] == ("POST", f"{address}/api/gradebook/terms/{term}/setup"), writes
        assert len(writes) == 3, writes
        assert not errors, errors
        assert bad == [(503, address + "/api/assignments")], bad
        browser.close()
        print("Assignment workspace passed: list filters, quiet refresh, creation and print controls, shared prepare/grade/certify/return/collect editors, answer-key draft, legacy routes and desktop widths.")
finally:
    server.close()
    fixture.doCleanups()
