"""Daily-work Home, page-scoped reads, stable refreshes and cached frontend files."""
import re
import sys
import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from exam_workflow.overview import campus_today
from browser_seating import seed
from browser_theme import contrast


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory)
        seed(app)
        exams = app.extensions["exams"]
        catalog = exams.catalog()
        term = catalog["terms"][0]
        assignment = catalog["assignments"][0]
        sections = [section["id"] for section in catalog["sections"] if section["term_id"] == term["id"]]
        exam = exams.create({"term_id": term["id"], "assignment_id": assignment["id"], "section_ids": sections})
        exams.save_sitting(exam["id"], {"version": exam["version"], "label": "Main sitting", "kind": "main",
            # Tomorrow on the campus wall clock, as Coming up counts it (the server's own clock can be a day ahead).
            "date": (date.fromisoformat(campus_today()) + timedelta(days=1)).isoformat(), "time": "8:35 AM", "location": "HECC 105"})
        scan = app.extensions["inbox"].submit({"action": "scan.receive"}, [{"filename":"private-student-name.pdf", "content":b"%PDF-home-fixture"}], instructor_review_required=True)
        waiting = app.extensions["inbox"].submit({"action": "document.archive", "target": {"level": "course", "id": "none"}, "metadata": {}},
                                                 [{"filename": "private-other-name.txt", "content": b"Unauthenticated"}], authenticated=False)
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width":1440, "height":1080}, timezone_id="Pacific/Honolulu")
                page = context.new_page()
                errors, reads = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                page.on("request", lambda req: reads.append(req.url) if "/api/state" in req.url else None)
                page.goto(address)
                # Needs you: what needs the instructor, each with its next step and a button to where it is done. The fresh
                # scan is the worker's (being inspected), so it is under Working on it with its received time.
                need = page.locator('#home-attention [data-need="inbox:submissions"]')
                expect(need).to_contain_text("Submissions that need you")
                expect(need.locator(".home-need-action")).to_have_attribute("href", "#inbox?status=submissions")
                expect(need.locator(".home-need-action")).to_have_attribute("aria-label", "Open Submissions: 1 Submissions that need you")
                expect(page.locator("#home-scans")).to_contain_text("Working on it")
                expect(page.locator("#home-scans")).to_contain_text("received")
                expect(page.locator("#home-courses")).to_contain_text("MATH 140")
                # Current courses: each current section links to its own tab, and the term to its gradebook.
                expect(page.locator("#home-courses .home-section-chip")).to_have_text(["507", "508"])
                expect(page.locator("#home-courses .home-section-chip").first).to_have_attribute("href", re.compile(r"^#course/.+/section/507\?from=current-courses$"))
                expect(page.locator("#home-courses .home-course-gradebook")).to_have_attribute("href", re.compile(r"^#course/[^/]+/home/gradebook$"))
                # No painting on the course card since 2026-10-04 (instructor: "remove paintings"); the code is the link's own words.
                course = page.locator("#home-courses .home-course-main").first
                expect(course.locator(".home-course-code")).to_have_text("MATH 140")
                expect(course).to_have_accessible_name(re.compile(r"^MATH 140 Calculus"))
                expect(page.locator(".painting")).to_have_count(0)
                # Coming up reads the calendar feed: the sitting tomorrow, with its countdown, its calendar day and its exam.
                coming = page.locator("#home-exams .home-coming-item").first
                expect(coming).to_contain_text("8:35 AM")
                expect(coming).to_contain_text("tomorrow")
                expect(coming.locator(".home-coming-main")).to_have_attribute("href", re.compile(r"^#calendar\?month=\d{4}-\d{2}&view=month&date=\d{4}-\d{2}-\d{2}$"))
                expect(coming.locator(".home-coming-source")).to_have_attribute("href", "#exams/" + exam["id"])
                # The owning page's link sits under the words, not beside them, so the title keeps the narrow column's width;
                # it stays its own target over the row's calendar link.
                copy, source = coming.locator(".home-coming-copy").bounding_box(), coming.locator(".home-coming-source").bounding_box()
                assert abs(source["x"] - copy["x"]) <= 1 and source["y"] >= copy["y"] + copy["height"] - 1, (copy, source)
                assert page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.closest('a')?.classList.contains('home-coming-source')",
                                     [source["x"] + source["width"] / 2, source["y"] + source["height"] / 2])
                # Every Needs you action ends on the column's right edge, so the arrows stand in one line.
                rights = page.locator("#home-attention .home-need-action").evaluate_all("links => links.map(a => Math.round(a.getBoundingClientRect().right))")
                assert len(set(rights)) == 1, rights
                # The hero says what needs the instructor and what is next; its one primary action uploads scans, and new work
                # starts beside it (journey-10): Create assignment and Draft a quiz open their owning page's windows.
                expect(page.locator("#primary-action")).to_have_text("Upload scanned assignment")
                secondary = page.locator(".page-heading-secondary a")
                expect(secondary).to_have_text(["Create assignment", "Draft a quiz"])
                assert secondary.evaluate_all("links => links.map(link => link.getAttribute('href'))") == ["#documents?create=1", "#documents?quiz=new"]
                expect(page.locator("#overview-content").get_by_role("link", name="Create assignment", exact=True)).to_have_count(0)
                expect(page.locator("#page-subtitle")).to_contain_text("need")
                expect(page.locator("#page-subtitle")).to_contain_text("Next: ")
                # Workspace health states itself while closed.
                expect(page.locator("#home-operations-status")).to_contain_text("Worker")
                expect(page.locator("#home-scans a")).to_have_attribute("href", "#inbox/job/" + scan["id"])
                expect(page.locator(".tools-section")).not_to_be_visible()
                assert "private-student-name" not in page.locator("#overview-content").inner_text()
                assert "private-other-name" not in page.locator("#overview-content").inner_text()
                assert waiting["status"] == "needs_review"
                assert reads and all(url.endswith("?include=core") for url in reads), reads
                assert not page.locator("#home-operations").evaluate("el => el.open")

                # Refresh identical data without replacing cards or losing focus/disclosures.
                page.locator("#home-operations > summary").click()
                page.evaluate("window.savedCourseNode = document.querySelector('.home-course-main'); window.savedCourseNode.focus()")
                page.evaluate("window.savedNeed = document.querySelector('.home-attention-item')")
                with page.expect_response("**/api/workspace/overview"):
                    page.evaluate("window.dispatchEvent(new Event('focus'))")
                assert page.evaluate("window.savedCourseNode === document.querySelector('.home-course-main') && document.activeElement === window.savedCourseNode")
                assert page.evaluate("window.savedNeed === document.querySelector('.home-attention-item')")
                assert page.locator("#home-operations").evaluate("el => el.open")
                page.locator("#home-operations > summary").click()

                # One appearance, desktop widths only (requirement, 2026-10-03).
                for width, height in ((1440, 1080), (1280, 800)):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                    for selector in ("#home-attention-title", ".home-course strong", ".home-coming-date", ".home-coming-copy small", ".home-attention-item", ".home-need-title", ".home-need-detail", "#home-scans small", "#home-operations-status"):
                        assert contrast(page, selector) >= 4.5, (width, selector)
                    # The approved layout: Needs you beside Coming up, In progress beside Current courses.
                    needs, coming = page.locator(".home-attention").bounding_box(), page.locator(".home-coming").bounding_box()
                    assert abs(needs["y"] - coming["y"]) < 2 and needs["x"] < coming["x"], (width, needs, coming)
                    page.screenshot(path=str(output / f"home-{width}.png"), full_page=True)

                page.set_viewport_size({"width":1440, "height":1080})
                with page.expect_response("**/api/state?include=core,rooms"):
                    page.get_by_role("link", name="Rooms", exact=True).click()
                expect(page.locator(".room-card")).to_contain_text("HECC 105")
                # The sidebar entry (the breadcrumb also links Home).
                page.locator(".sidebar").get_by_role("link", name="Home", exact=True).click()
                page.evaluate("location.hash = 'overview?operations'")
                expect(page.locator("#home-operations")).to_have_attribute("open", "")
                expect(page.locator("#home-queues")).to_contain_text("Scan inspection")
                # Only queues with work are listed; the idle ones are one line away.
                expect(page.locator("#home-queues")).not_to_contain_text("Rubric drafts")
                page.locator("#home-queues").get_by_role("button", name="Show all queues", exact=True).click()
                expect(page.locator("#home-queues")).to_contain_text("Rubric drafts")
                page.locator("#home-queues").get_by_role("button", name="Show active queues only", exact=True).click()
                expect(page.locator("#home-queues")).not_to_contain_text("Rubric drafts")
                page.locator('.sidebar [data-action="settings"]').click()
                expect(page.locator("#settings-storage")).to_contain_text("Course backups are not configured")
                page.keyboard.press("Escape")

                # Quick actions the sidebar lacks open the owning page's own dialog.
                page.locator("#overview-content").get_by_role("link", name="Add calendar event", exact=True).click()
                expect(page.locator("#calendar-event-form")).to_be_visible()
                assert "add=1" not in page.evaluate("location.hash")
                page.locator("#calendar-event-form").get_by_role("button", name="Cancel", exact=True).click()
                page.locator(".sidebar").get_by_role("link", name="Home", exact=True).click()
                # Create assignment (the hero's) links to #documents?create=1, which leads to Assignments (ASSIGNMENT-WORKSPACE): that
                # page opens Create assignment's own window once (the wizard and all its checks are that page's) and drops the query,
                # so Back or a reload never opens it again.
                page.locator('.page-heading-secondary a[href="#documents?create=1"]').click()
                expect(page.locator('#assignment-wizard .wizard-steps [aria-current="step"]')).to_have_text("Details")
                expect(page.locator("#page-title")).to_have_text("Assignments")
                assert page.evaluate("location.hash") == "#assignments", page.evaluate("location.hash")
                page.keyboard.press("Escape")
                expect(page.locator("#assignment-wizard")).to_be_hidden()
                page.reload()
                expect(page.locator("#assignment-workspace [data-assignment-list]")).to_be_visible()
                page.wait_for_load_state("networkidle")
                expect(page.locator("dialog[open]")).to_have_count(0)
                # Draft a quiz (the hero's other link) opens the model's window from the same page, also once.
                page.locator(".sidebar").get_by_role("link", name="Home", exact=True).click()
                page.locator('.page-heading-secondary a[href="#documents?quiz=new"]').click()
                expect(page.locator("dialog[open]")).to_contain_text("Draft a quiz with a model")
                assert page.evaluate("location.hash") == "#assignments", page.evaluate("location.hash")
                page.keyboard.press("Escape")
                page.locator(".sidebar").get_by_role("link", name="Home", exact=True).click()

                # A second load reuses immutable bytes (no route interception/cache disabling).
                page.reload()
                expect(page.locator("#home-courses")).to_contain_text("Calculus")
                page.wait_for_function("performance.getEntriesByType('resource').filter(e => e.name.includes('/assets/')).length > 15")
                assets = page.evaluate("performance.getEntriesByType('resource').filter(e => e.name.includes('/assets/')).map(e => ({url:e.name, bytes:e.transferSize}))")
                assert all(asset["bytes"] == 0 for asset in assets), assets

                # In progress holds the place it will take while Grading is read (rest-shell-6: it appeared later and pushed Coming
                # up down). With no current grading plan nothing can show, so no place is held from the first overview on, whether
                # Grading then answers or fails (review fix, 2026-10-02: the panel appeared, then left).
                def held_page(handler=None):
                    held_grading = []
                    slow = context.new_page()
                    slow.route("**/api/grading?health=0", lambda route: held_grading.append(route))
                    slow.goto(address)
                    expect(slow.locator("#home-attention [data-need]").first).to_be_visible()
                    expect(slow.locator("#home-exams .home-coming-item").first).to_be_visible()
                    for _ in range(50):
                        if held_grading:
                            break
                        slow.wait_for_timeout(100)
                    assert held_grading, "Home reads Grading after its first paint"
                    return slow, held_grading[0]

                for outcome in ("answer", "fail"):
                    slow, route = held_page()
                    expect(slow.locator("#home-progress-panel")).to_be_hidden()
                    route.continue_() if outcome == "answer" else route.abort()
                    slow.wait_for_timeout(300)
                    expect(slow.locator("#home-progress-panel")).to_be_hidden()
                    # Current courses keeps the narrow column under Coming up; with In progress hidden it used to slide into the
                    # wide column.
                    courses, coming = slow.locator(".home-teaching").bounding_box(), slow.locator(".home-coming").bounding_box()
                    assert abs(courses["x"] - coming["x"]) <= 1 and abs(courses["width"] - coming["width"]) <= 1, (courses, coming)
                    slow.close()
                # One current plan that Grading lists and nobody has started (here a plan Grading's answer lists as a quiz drafted in
                # Faculty would be, with nothing done): one line is held, and Coming up does not move when Grading answers.
                plan = app.extensions["frq_grading"].create_assignment({"event_id": exam["id"]})
                setup = {"questions": [], "templates": [], "exam_versions": {"letters": [], "graded": [], "blanks": {}},
                         "rubric_quality": {"state": "missing", "readiness": {}}, "template_setup": None, "preparation": {}}
                listed = {**{key: plan[key] for key in ("id", "title", "code", "term", "term_other", "year", "version")},
                          "entry": {"ready": True, "cleared_scans": 0, "pending_scans": 0, "set_up_by_quiz_draft": True}, "questions": [], "templates": [],
                          "workflow": {"scans": [], "responses": {}, "collection": {}, "new_responses": 0, "pending": 0, "failed": 0,
                                       "review_responses": 0, "version_decisions": 0, "certification": {"state": "none"}, "setup": setup}}
                slow, route = held_page()
                expect(slow.locator("#home-progress-panel")).to_be_visible()
                expect(slow.locator("#home-progress")).to_contain_text("Reading grading")
                # What follows In progress (Workspace health, under the second row) keeps its place when Grading answers.
                coming_top = slow.locator(".home-operations").bounding_box()["y"]
                body = route.fetch().json()
                route.fulfill(json={**body, "assignments": [*body["assignments"], listed]})
                expect(slow.locator("#home-progress .home-grading-idle")).to_contain_text(f"{plan['title']}: grading not started")
                # The retired Grading index's place is the Assignments index's grading filter (I7, 2026-10-18).
                expect(slow.locator("#home-progress-links").get_by_role("link", name="All grading")).to_have_attribute("href", "#assignments?filter=grading")
                moved = slow.locator(".home-operations").bounding_box()["y"] - coming_top
                assert abs(moved) <= 2, f"Workspace health moved {moved} px when Grading answered"
                slow.screenshot(path=str(output / "home-progress-held-1440.png"))
                slow.close()

                # Cold-start route change must trigger a scoped follow-up, not wait for polling.
                cold = context.new_page()
                held = []
                def intercept(route):
                    if not held:
                        held.append(route)
                    else:
                        route.continue_()
                cold.route("**/api/state?include=core", intercept)
                cold.goto(address)
                cold.wait_for_function("document.querySelector('#page-title').textContent === 'Your teaching day.'")
                cold.get_by_role("link", name="Rooms", exact=True).click()
                with cold.expect_response("**/api/state?include=core,rooms"):
                    held[0].continue_()
                expect(cold.locator(".room-card")).to_contain_text("HECC 105")
                cold.close()
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Home checks passed: actual course/exam/scan records, no student filenames, scoped reads, unchanged DOM/focus, one appearance at 1440 and 1280 in the approved two-column layout, campus clock display, health disclosure, cached assets, and cold-start navigation.")


if __name__ == "__main__":
    main()
