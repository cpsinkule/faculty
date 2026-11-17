"""Draft a quiz with a model in the browser: the new-draft window, the draft's page with every version's pages and rubrics, asking for
changes, and approving it into print (requirement, 2026-09-28). A loopback model host answers; LaTeX is compiled for real."""
import re
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from fake_openai import payload_of, prompt_name
from test_course_notes import notes_pdf
from test_quiz_drafts import QuizDrafts, resolve_answer, review_answer, reviewing_a_draft

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = QuizDrafts("test_fragments_cannot_change_the_page_or_read_files")
fixture.setUp()
# RUBRIC QUESTIONS AT DRAFT REVIEW (requirement, 2026-10-11): the rubric check a revision ends with raises one decision about problem
# 1, named once for both versions, and the reviewer applies the instructor's answer when the draft's page sends it.
DECISION = {"versions": ["A", "B"], "item_ids": [{"version": v, "item_ids": ["R1"]} for v in "AB"], "version_notes": [],
            "message": "The line names the choice and its value: does a circled letter with nothing else earn it?", "title": "Does the letter alone count?",
            "decision": "Should a circled (b) with nothing else earn the points?", "group": "",
            "choices": [{"label": "The letter alone", "feedback": "A circled (b) alone earns the points.", "consequence": ""},
                        {"label": "Letter and value", "feedback": "Require the letter and its value.", "consequence": ""}]}


def scripted(body, host):
    name, payload = prompt_name(body), payload_of(body)
    if name == "rubric_quality_versions" and reviewing_a_draft(fixture.path) and payload["problem"]["versions"][0]["question"]["printed_number"] == "1":
        return review_answer(payload, errors=[DECISION])
    if name == "rubric_quality_resolve":
        return resolve_answer(payload, how="The circled letter alone earns the points.", clarify={"R1": "A circled (b) alone earns the points."})
    return fixture.answer(body, host)


fixture.host.script = scripted
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
HIDE = """() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
  document.dispatchEvent(new Event('visibilitychange')); }"""
SHOW = """() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
  document.dispatchEvent(new Event('visibilitychange')); }"""
notes = OUTPUT / "quiz-material.txt"
notes.write_text("Linear programming: corner points, bounded regions, the Method of Corners.")
# The course's lecture notes, so the window offers their sections (QUIZZES "NOTES SECTIONS FOR THE WRITER", 2026-10-28).
fixture.app.extensions["course_notes"].upload(fixture.term["definition_id"], notes_pdf(
    [["Lecture Notes"], ["Linear Programming", "3.1 Setting Up Linear Programming Problems", "Define the decision variables."],
     ["3.2 Graphing Systems of Linear Inequalities", "Shade the feasible region S."]],
    [(0, "3.1 Setting Up Linear Programming Problems", 2), (0, "3.2 Graphing Systems of Linear Inequalities", 3)]), "notes.pdf", "notes-1")


def wait_until(page, expression, timeout=20000):
    """Poll a page expression with page.evaluate. Under the page's CSP (script-src 'self') Playwright's wait_for_function
    needs eval for a string predicate once it has to poll, which the policy refuses (a slow image load failed this run)."""
    for _ in range(timeout // 100):
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"Timed out waiting for {expression}")


def screenshots(page, name):
    # One appearance, desktop only (DESIGN-LANGUAGE): no overflow at 1500 or 1280.
    for width in (1500, 1280):
        page.set_viewport_size({"width": width, "height": 1000})
        page.screenshot(path=str(OUTPUT / f"{name}-{width}.png"), animations="disabled", full_page=width == 1500)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (name, width)
    page.set_viewport_size({"width": 1500, "height": 1100})


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(20000)
        page.clock.install()
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        page.goto(address + "/#assignments")
        # Create assignment is the shared starting menu (ASSIGNMENT-WORKSPACE "Workflow clarity", 2026-10-09): its model draft
        # closes the menu and opens the draft window.
        dialog = page.locator("#dialog")
        page.get_by_role("button", name="Create assignment", exact=True).click()
        dialog.get_by_role("button", name="Draft a quiz with a model", exact=True).click()
        expect(dialog.get_by_role("heading", name="Draft a quiz with a model")).to_be_visible()
        dialog.get_by_label("Title", exact=True).fill("Chapter 3 Quiz")
        dialog.get_by_label("Points", exact=True).fill("5")
        # GRADED FOR PARTICIPATION (2026-11-01): the box sets one version, and unticked the usual four, until a number is typed.
        expect(dialog.get_by_label("Versions", exact=True)).to_have_value("4")
        dialog.get_by_label("Graded for participation", exact=True).check()
        expect(dialog.get_by_label("Versions", exact=True)).to_have_value("1")
        dialog.get_by_label("Graded for participation", exact=True).uncheck()
        expect(dialog.get_by_label("Versions", exact=True)).to_have_value("4")
        dialog.get_by_label("Versions", exact=True).fill("2")
        # Negative scoring is listed first and chosen by default (requirement, 2026-10-28).
        expect(dialog.locator("select[name=mode]")).to_have_value("negative")
        dialog.locator("select[name=mode]").select_option("positive")  # the fake model writes this draft's rubrics adding credit
        expect(dialog.get_by_label("Format")).to_have_value("One multiple-choice problem and three free-response problems.")
        dialog.get_by_label("Format").fill("One multiple-choice problem and one free-response problem.")
        dialog.get_by_label("Instructions for the model").fill("Sections 3.1-3.3: one multiple choice, one free response.")
        dialog.get_by_label("Material").set_input_files(str(notes))
        # Lecture notes to read: the notes' sections under their chapter, ticked as the instructions name them ("Sections 3.1-3.3");
        # once one is changed by hand, typing no longer changes them, and the ones ticked are the draft's.
        expect(dialog.locator('input[name="notes_sections"]')).to_have_count(2)
        expect(dialog.locator(".quiz-draft-notes-pick")).to_contain_text("Chapter 3 · Linear Programming")
        expect(dialog.locator('input[name="notes_sections"]:checked')).to_have_count(2)
        dialog.get_by_label("Instructions for the model").fill("Section 3.1: one multiple choice, one free response.")
        expect(dialog.locator('input[name="notes_sections"]:checked')).to_have_count(1)
        dialog.get_by_label("3.1 Setting Up Linear Programming Problems").uncheck()
        dialog.get_by_label("3.2 Graphing Systems of Linear Inequalities").check()
        dialog.get_by_label("Instructions for the model").fill("Sections 3.1-3.3: one multiple choice, one free response.")
        expect(dialog.get_by_label("3.1 Setting Up Linear Programming Problems")).not_to_be_checked()
        screenshots(page, "quiz-draft-new")
        dialog.get_by_role("button", name="Draft the quiz", exact=True).click()
        expect(dialog).not_to_be_visible()
        wait_until(page, "location.hash.startsWith('#documents/quiz/')")
        draft_id = page.evaluate("location.hash").rsplit("/", 1)[-1]
        assert fixture.frq.quiz_draft(draft_id)["notes_sections"] == ["3.2"]
        expect(page.locator(".quiz-draft-status")).to_contain_text("Revision 1")
        fixture.frq.process_cloud()
        expect(page.locator("#quiz-draft-heading")).to_have_text("Revision 1", timeout=30000)
        # Ready for review: the header offers Approve and Ask for changes (which brings the form below every problem into view),
        # the crumbs start at Home, and the unified Assignments list links the draft (ASSIGNMENT-WORKSPACE).
        expect(page.locator("#breadcrumb")).to_contain_text("Home")
        page.locator(".page-heading").get_by_role("button", name="Ask for changes", exact=True).click()
        expect(page.locator("[data-quiz-changes] textarea[name=feedback]")).to_be_focused()
        page.goto(address + "/#assignments")
        ready_link = page.locator("[data-assignment-drafts]").get_by_role("link", name="Chapter 3 Quiz", exact=True)
        expect(ready_link).to_be_visible()
        ready_link.click()
        wait_until(page, f"location.hash === '#documents/quiz/{draft_id}'")
        expect(page.locator("#quiz-draft-heading")).to_have_text("Revision 1")
        expect(page.locator(".quiz-draft-problem")).to_have_count(2)
        expect(page.locator(".quiz-draft-pages img")).to_have_count(1)
        wait_until(page, "[...document.querySelectorAll('.quiz-draft-pages img')].every(img => img.complete && img.naturalWidth > 0)")
        expect(page.locator(".quiz-draft-rubric").first).to_contain_text("Correct choice")
        page.get_by_role("tab", name="Version B", exact=True).click()
        expect(page.locator(".quiz-draft-pages img").first).to_have_attribute("alt", "Version B, page 1")
        # The answer of the version shown sits beside each problem, read without opening its rubric (2026-10-06).
        expect(page.locator(".quiz-draft-problem .review-said dt:first-of-type")).to_have_text(["Answer", "Answer"])
        answers = page.locator(".quiz-draft-problem .review-said dd:first-of-type")
        expect(answers).to_have_count(2)
        expect(answers.nth(0)).to_contain_text("(b)")
        expect(answers.nth(1)).to_contain_text("(b)")
        expect(answers.nth(1)).to_contain_text("8.5")
        # Under it, the answer key read for that version while the quiz was drafted, as a saved key is typeset: what approving
        # saves with the problem's question (requirement, 2026-10-11).
        expect(page.locator(".quiz-draft-problem .review-said dt:nth-of-type(2)")).to_have_text(["Answer key", "Answer key"])
        keys = page.locator(".quiz-draft-problem .review-said dd:nth-of-type(2)")
        expect(keys.nth(0)).to_contain_text("or")  # the letter or the entry itself
        expect(keys.nth(1)).to_contain_text("Part b:")
        expect(keys.nth(1)).to_contain_text("8.5")
        # A problem's change box opens on request: none is drawn open.
        expect(page.locator("[data-problem-comment]:visible")).to_have_count(0)
        # RUBRIC QUESTIONS AT DRAFT REVIEW (2026-10-11): the revision ended with the rubric check Key & Rubric Finalization would run.
        # The page says how many decisions are open and shows them in the finalization's own decisions view, read only: one
        # decision for both versions, its affected lines with their guidance and no field to edit, no rubric pop-up.
        expect(page.locator("[data-rubric-lead]")).to_have_text("1 to decide1 rubric decision open. Answer it below, or later in Key & Rubric Finalization.")
        rubric_check = page.locator("[data-rubric-check]")
        expect(rubric_check.get_by_role("heading", name="Rubric check", exact=True)).to_be_visible()
        expect(rubric_check.get_by_role("heading", name="Does the letter alone count?", exact=True)).to_be_visible()
        expect(rubric_check.locator("[data-decision-applies]")).to_contain_text("Applies to Versions A and B")
        expect(rubric_check.locator("[data-reference-row]")).to_have_count(2)
        expect(rubric_check.locator("[data-reference-row] textarea, [data-show-rubric]")).to_have_count(0)
        expect(rubric_check.locator("[data-resolve-scope]")).to_have_text("Sends all 1 decision to the reviewer; it settles them itself. Nothing is approved until you approve.")
        # Under each rubric line of the version shown, the guidance the check holds for it: what the approval's box confirms "as shown".
        page.locator('[data-problem="1"] details.review-fold:has(.quiz-draft-rubric) > summary').click()
        expect(page.locator('[data-problem="1"] [data-guidance="rule"]').first).to_have_text("Full-credit line: never selected with another line.")
        expect(page.locator('[data-problem="1"] [data-guidance="clarification"]')).to_contain_text("For the graders: Apply only when no other rubric item applies")
        screenshots(page, "quiz-draft-page")
        # A page opens larger in the file viewer, at that page (requirement, 2026-10-01).
        page.get_by_role("link", name="Open Version B, page 1 larger", exact=True).click()
        viewer = page.locator("dialog.file-viewer")
        expect(viewer).to_be_visible()
        expect(viewer.locator("figure.file-viewer-page, iframe.file-viewer-frame").first).to_be_visible(timeout=20000)
        viewer.get_by_role("button", name="Close the viewer").click()
        expect(viewer).not_to_be_visible()
        # Asking for changes: a comment under problem 2 is sent with the rest.
        fixture.harder = True
        page.get_by_role("button", name="Suggest a change to problem 2", exact=True).click()
        expect(page.get_by_label("Changes to problem 2", exact=True)).to_be_focused()
        page.get_by_label("Changes to problem 2", exact=True).fill("Ask for the intercepts too.")
        expect(page.locator("[data-comment-scope]")).to_have_text("Also sends your changes to problem 2.")
        page.get_by_role("button", name="Ask the model for these changes", exact=True).click()
        expect(page.locator(".quiz-draft-status")).to_contain_text("Revision 2")
        # A failed read while the model works keeps the page, says so once above the status, and reads again (bugs-frontend-4).
        draft_read = f"**/api/quiz-drafts/{draft_id}"
        page.route(draft_read, lambda route: route.fulfill(status=503, json={"error": "Synthetic outage"}))
        expect(page.locator("[data-quiz-status] .quiz-draft-stale")).to_contain_text("Couldn't refresh. Showing the last loaded results.", timeout=10000)
        expect(page.locator(".quiz-draft-status")).to_contain_text("Revision 2")
        page.unroute(draft_read)
        bad[:] = [item for item in bad if item[0] != 503]
        errors[:] = [item for item in errors if "503" not in item]
        fixture.frq.process_cloud()
        expect(page.locator("#quiz-draft-heading")).to_have_text("Revision 2", timeout=30000)
        expect(page.locator(".quiz-draft-stale")).to_have_count(0)
        expect(page.locator(".quiz-draft-revision .review-changed")).to_contain_text("What changed in revision 2")
        # Problem 1 reads as before, so its open rubric decision came with it unasked. Picking an answer drafts the feedback; a
        # version tab redraws the page and the unsent answer stays; one button sends it: a revision that changes nothing of the quiz.
        expect(page.locator("[data-rubric-lead]")).to_contain_text("1 rubric decision open")
        rubric_check = page.locator("[data-rubric-check]")
        rubric_check.get_by_label("The letter alone", exact=True).check()
        expect(rubric_check.get_by_label("Feedback to the reviewer")).to_have_value("A circled (b) alone earns the points.")
        expect(rubric_check.locator("[data-resolve-scope]")).to_contain_text("Sends all 1 decision to the reviewer, with your answer on 1")
        page.get_by_role("tab", name="Version A", exact=True).click()
        expect(rubric_check.get_by_label("Feedback to the reviewer")).to_have_value("A circled (b) alone earns the points.")
        screenshots(page, "quiz-draft-rubric-decision")
        with page.expect_request(lambda request: request.url.endswith("/rubric-answers") and request.method == "POST") as answered:
            rubric_check.get_by_role("button", name="Send my answers to the reviewer", exact=True).click()
        assert answered.value.post_data_json["responses"] == [{"concern": n, "response": "A circled (b) alone earns the points."} for n in (0, 1)], answered.value.post_data_json
        expect(page.locator(".quiz-draft-status")).to_contain_text("The rubric reviewer is applying your answers")
        fixture.frq.process_cloud()
        expect(page.locator("#quiz-draft-heading")).to_have_text("Revision 3", timeout=30000)
        expect(page.locator("[data-rubric-lead]")).to_have_text("No rubric decision is open. Proposed for the graders: 6 clarifications and 0 exclusions, with your answer applied.")
        expect(rubric_check.locator("[data-rubric-clear]")).to_have_text("Nothing needs a decision.")
        expect(rubric_check.locator("details.review-fold > summary")).to_contain_text("Answered decisions")
        expect(page.locator(".quiz-draft-problem")).to_have_count(2)  # the quiz itself is as revision 2 left it
        # Approving: the sections it is for and the copies of each version, printed as ONE stack for them (instructor,
        # 2026-10-01), confirmed with a tick. Until a count is typed, the copies follow the ticked sections' students.
        page.locator(".page-heading").get_by_role("button", name="Approve and print…", exact=True).click()
        expect(dialog.get_by_role("heading", name="Approve and send to printing")).to_be_visible()
        ticks = dialog.locator("input[name=section]")
        expect(ticks).to_have_count(2)
        expect(dialog.locator("input[name=section]:checked")).to_have_count(2)
        students = sum(int(tick.get_attribute("data-students")) for tick in ticks.all())
        # QUIZZES "SHARED STAMPS": offered, unticked, and saying what it cannot catch.
        expect(dialog.get_by_label("Each student must use their own copy: report any stamp that comes back twice", exact=True)).not_to_be_checked()
        expect(dialog.locator("#quiz-own-copies-hint")).to_have_text("The papers are still graded. This only catches copying when both papers come back.")
        counts = lambda: [int(dialog.get_by_label(f"Copies of Version {v}", exact=True).input_value()) for v in "AB"]
        assert sum(counts()) == students and counts()[0] - counts()[1] in (0, 1), counts()
        dialog.get_by_label("Section 502").uncheck()
        assert sum(counts()) == int(ticks.nth(0).get_attribute("data-students")), counts()
        dialog.get_by_label("Copies of Version A", exact=True).fill("2")
        dialog.get_by_label("Copies of Version B", exact=True).fill("1")
        expect(dialog.locator("[data-copy-total]")).to_have_text("3 copies in all.")
        # The window's first line says what approving will do as it stands (2026-10-06).
        # Since 2026-10-11 it also says how many answer keys approving saves: the keys shown beside the problems.
        # Since the same day ("Rubric questions at draft review") it says what is left in Key & Rubric Finalization: the check made
        # here goes with the approval, and ONE unticked box offers to record the rubric finalization with it.
        prints = "Approving prints 3 copies of Versions A and B exactly as shown for Section 501, as one stack, sets up its grading and saves the 4 answer keys shown. "
        expect(dialog.locator("[data-approve-summary]")).to_have_text(prints + "The rubric check made here goes with it: Key & Rubric Finalization waits only for your confirmation.")
        dialog.get_by_role("button", name="Approve and send to printing", exact=True).click()
        expect(dialog.locator(".form-error")).to_contain_text("Tick that you checked")
        dialog.get_by_label("I checked every version and its rubrics.").check()
        expect(dialog.locator(".form-error")).to_be_hidden()  # the refusal goes once the tick is given
        finalize = dialog.get_by_label("Also confirm the rubric guidance as shown (6 clarifications, 0 exclusions, your answer to 1 decision). This is the rubric finalization.")
        expect(finalize).not_to_be_checked()
        finalize.check()
        expect(dialog.locator("[data-approve-summary]")).to_have_text(prints + "It also records the rubric finalization: nothing is left to do in Key & Rubric Finalization.")
        # 'Given on' (requirement, 2026-10-02): optional, empty by default, after the sections.
        expect(dialog.get_by_label("Given on (optional)", exact=True)).to_have_value("")
        dialog.get_by_label("Given on (optional)", exact=True).fill("2026-10-08")
        screenshots(page, "quiz-draft-approve")
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        expect(dialog).not_to_be_visible()
        # The approval itself from a browser on the campus clock late in the evening of Oct 7 (04:30 UTC on Oct 8): the date sent and
        # kept is the typed one.
        late = browser.new_context(viewport={"width": 1500, "height": 1100}, timezone_id="America/Chicago")
        evening = late.new_page()
        evening.clock.set_fixed_time("2026-10-08T04:30:00Z")
        evening.set_default_timeout(20000)
        evening.on("pageerror", lambda error: errors.append(str(error)))
        evening.goto(address + f"/#documents/quiz/{draft_id}")
        assert evening.evaluate("new Date().getDate()") == 7
        evening.locator(".page-heading").get_by_role("button", name="Approve and print…", exact=True).click()
        form = evening.locator("#quiz-draft-approve")
        form.get_by_label("Section 502").uncheck()
        form.get_by_label("Copies of Version A", exact=True).fill("2")
        form.get_by_label("Copies of Version B", exact=True).fill("1")
        form.get_by_label("Given on (optional)", exact=True).fill("2026-10-08")
        form.get_by_label("I checked every version and its rubrics.").check()
        form.locator("[data-finalize] input").check()  # the one tick that is the rubric finalization
        with evening.expect_request(lambda request: request.url.endswith("/approve") and request.method == "POST") as sent:
            form.get_by_role("button", name="Approve and send to printing", exact=True).click()
        assert sent.value.post_data_json["given_on"] == "2026-10-08", sent.value.post_data_json
        assert sent.value.post_data_json["save_keys"] is True, sent.value.post_data_json  # this page showed the keys: its approval saves them
        shown = next(r for r in fixture.frq.quiz_draft(draft_id)["revisions"] if r["number"] == 3)["result"]["rubric_check"]["sha256"]
        assert sent.value.post_data_json["finalization"] == {"confirm": True, "check_sha256": shown}, sent.value.post_data_json
        expect(form).not_to_be_visible()
        late.close()
        fixture.frq.process_cloud()
        page.reload()  # this page did not approve it: it reads the draft again
        expect(page.locator(".quiz-draft-status")).to_contain_text("Approved and sent to printing", timeout=30000)
        # Given on: the exam record's main sitting on the typed date, said on the draft's page.
        expect(page.locator(".quiz-draft-status")).to_contain_text("Given on Thu, Oct 8")
        given = fixture.frq.exams.get(fixture.frq.quiz_draft(draft_id)["approved"]["result"]["event_id"])["sittings"]
        assert [(s["kind"], s["date"]) for s in given] == [("main", "2026-10-08")], given
        expect(page.locator(".quiz-draft-status")).to_contain_text("printed as one stack for Section 501")
        expect(page.locator(".quiz-draft-links")).to_contain_text("Every version interleaved (PDF)")
        # ASSIGNMENT-WORKSPACE: approval's next step opens this assignment's canonical summary.
        expect(page.locator("#primary-action")).to_have_text("Open assignment")
        approved = fixture.frq.quiz_draft(draft_id)["approved"]["result"]
        canonical = f"#assignments/{approved['assignment_id']}/{fixture.term['id']}"
        expect(page.locator(".quiz-draft-links").get_by_role("link", name="Open print jobs", exact=True)).to_have_attribute(
            "href", f"#documents?jobs={approved['assignment_id']}")
        # The check made on the draft is the grading plan's, and the ticked box recorded its finalization with the approval.
        expect(page.locator(".quiz-draft-status")).to_contain_text("Its rubric finalization was recorded with the approval: nothing waits in Key & Rubric Finalization.")
        assert fixture.frq.rubric_quality(approved["plan_id"])["state"] == "confirmed"
        expect(page.locator(".quiz-draft-status")).to_contain_text("Its 4 answer keys were saved with the questions.")
        # Put it on Canvas… sits on the links' line: no themed text-button padding or 32px height in that row.
        canvas_button = page.locator(".quiz-draft-links [data-quiz-canvas]")
        expect(canvas_button).to_have_css("padding-left", "0px")
        expect(canvas_button).to_have_css("min-height", "0px")
        screenshots(page, "quiz-draft-approved")
        page.locator("#primary-action").click()
        wait_until(page, f"location.hash === '{canonical}'")
        workspace = page.locator("#assignment-workspace")
        navigation = workspace.get_by_role("navigation", name="Assignment workspace", exact=True)
        expect(navigation.locator('[aria-current="page"]')).to_have_text("Needs you & progress")
        expect(workspace.get_by_role("heading", name="Readiness", exact=True)).to_be_visible()
        expect(page.locator("#page-title")).to_have_text("Chapter 3 Quiz")
        expect(workspace.locator(".assignment-readiness")).to_contain_text("Not certified")
        page.goto(address + f"/#documents/quiz/{draft_id}")
        # The grading plan is set up before any scan: its page opens from here on Key & Rubric Finalization.
        page.locator(".quiz-draft-links").get_by_role("link", name="Open Key & Rubric Finalization").click()
        wait_until(page, "location.hash.startsWith('#grading/')")
        expect(page.locator("#page-title")).to_contain_text("Chapter 3 Quiz")  # the plate carries the title (DESIGN-LANGUAGE §5)
        expect(page.locator("#grading-section")).to_contain_text("Key & Rubric Finalization")
        expect(page.locator("#grading-section")).to_contain_text("4 of 4 questions")
        expect(workspace.locator('[data-assignment-editor] > #grading-section')).to_be_visible()
        expect(navigation.get_by_role("link", name="Needs you & progress", exact=True)).to_have_attribute("href", canonical)
        navigation.get_by_role("link", name="Prepare", exact=True).click()
        question = workspace.locator('[data-question-panel]:visible')
        expect(question.locator('.assignment-question-context')).to_contain_text("Which value")
        expect(question.locator('.assignment-question-context')).to_contain_text("Worked solution")
        expect(question.locator('[data-location-check]')).to_contain_text("Answer area confirmed")
        screenshots(page, "quiz-draft-grading")
        page.goto(address + "/#assignments")
        card = workspace.locator('[data-assignment-list] .assignment-row').filter(has_text="Chapter 3 Quiz")
        expect(card.get_by_role("link", name="Chapter 3 Quiz", exact=True)).to_have_attribute("href", canonical)
        expect(workspace.locator('[data-assignment-drafts]')).not_to_contain_text("Chapter 3 Quiz")
        card.get_by_role("link", name="Chapter 3 Quiz", exact=True).click()
        navigation.get_by_role("link", name="Create", exact=True).click()
        source_link = workspace.get_by_role("link", name="Approved quiz and source", exact=True)
        expect(source_link).to_have_attribute("href", f"#documents/quiz/{draft_id}")
        source_link.click()
        expect(page.locator(".quiz-draft-status")).to_contain_text("Approved and sent to printing")
        navigation.get_by_role("link", name="Create", exact=True).click()
        # Print jobs: the approval's jobs are ONE group, its versions interleaved in one print file (requirement, 2026-10-01); each row names its version.
        group = workspace.locator(".document-export").filter(has_text="Chapter 3 Quiz")
        expect(group).to_have_count(1)
        expect(group.locator(".document-export-facts")).to_contain_text("Versions A, B interleaved")
        expect(group.locator(".document-job-title strong")).to_have_text(["Version A", "Version B"])
        expect(group.locator('[data-print-files] a.button, [data-print-files-note]').first).to_be_visible()
        stack = group.get_by_role("link", name="Print PDF")
        if stack.count():
            assert "/api/documents/stack?batches=" in stack.get_attribute("href"), stack.get_attribute("href")
        # Canonical Create refreshes the owning catalog quietly; a hidden tab reads nothing.
        catalog = lambda request: request.url.endswith("/api/documents/catalog")
        reads = []
        page.on("request", lambda request: reads.append(request.url) if catalog(request) else None)
        with page.expect_request(catalog):
            page.clock.fast_forward(31000)
        page.evaluate(HIDE)
        page.wait_for_timeout(500)
        reads.clear()
        page.clock.fast_forward(61000)
        assert not reads, reads
        with page.expect_request(catalog):
            page.evaluate(SHOW)
            page.clock.fast_forward(31000)
        # The worker prepares the jobs independently of the unfinished rubric finalization.
        while fixture.documents.build_one():
            pass
        page.reload()
        expect(group.get_by_role("link", name="Print PDF", exact=True)).to_be_visible()
        expect(group.locator(".document-export-facts")).to_contain_text("3 copies")
        # The approval's older Open print jobs URL resolves this assignment inside its shell. Its stack's Print PDF opens the
        # file viewer (print-1: the job list was joined by a raw comma, which the viewer refused, so the click did nothing).
        page.goto(address + f"/#documents?jobs={approved['assignment_id']}")
        expect(navigation.get_by_role("link", name="Needs you & progress", exact=True)).to_have_attribute("href", canonical)
        expect(workspace.locator('[data-assignment-editor] > #documents-section')).to_be_visible()
        group = workspace.locator(".document-export").filter(has_text="Chapter 3 Quiz")
        stack = group.get_by_role("link", name="Print PDF", exact=True)
        expect(stack).to_have_attribute("href", re.compile(r"^/api/documents/stack\?batches=[^,]+%2C[^,]+$"))
        stack.click()
        viewer = page.locator("dialog.file-viewer[open]")
        expect(viewer.locator("figure.file-viewer-page, iframe.file-viewer-frame").first).to_be_visible(timeout=30000)
        viewer.get_by_role("button", name="Close the viewer").click()
        # A version's window leads with the stack; its own file and exports are folded and say they are this version's only (print-4).
        first_job = group.locator("[data-batch]").first.get_attribute("data-batch")
        group.locator("[data-batch]").first.click()
        detail = page.locator("#document-batch-detail")
        expect(detail.get_by_role("heading", name="Print", exact=True)).to_be_visible()
        expect(detail.locator("[data-stack-note]")).to_contain_text("Version A of 2 printed as one stack")
        assert not detail.locator("[data-version-only]").evaluate("node => node.open"), "This version's own files start folded"
        detail.locator("[data-version-only] > summary").click()
        expect(detail.locator("[data-version-only]")).to_contain_text("Exports send this version’s PDF only.")
        screenshots(page, "quiz-draft-stack-job")
        # Superseding one version warns that it ends the stack file (wording only).
        detail.get_by_role("button", name="Job actions", exact=True).click()
        page.get_by_role("menuitem", name="Supersede print job…", exact=True).click()
        expect(page.locator("#document-supersede [data-stack-warning]")).to_contain_text("ends the interleaved stack file")
        page.locator("#document-supersede").get_by_role("button", name="Back to print job", exact=True).click()
        # Print more copies of every version: one new term job per version, one request, so they group as a new stack.
        before = {b["id"] for b in fixture.documents.catalog()["batches"]}
        page.locator("#document-batch-detail").get_by_role("button", name="Print more copies…", exact=True).click()
        reprint = page.locator("#document-reprint")
        expect(reprint).to_contain_text("for Version A only, unless you choose every version")
        reprint.get_by_label("Extra unassigned copies of every version (one stack)", exact=True).check()
        reprint.get_by_label("Number of extra copies (of each version)", exact=True).fill("1")
        reprint.get_by_role("button", name="Create print job", exact=True).click()
        expect(page.locator("#document-batch-detail")).to_be_visible()
        made = [b for b in fixture.documents.catalog()["batches"] if b["id"] not in before]
        assert len(made) == 2 and len({b["assignment_version_id"] for b in made}) == 2 and len({b["export"] for b in made}) == 1, made
        assert all(b["quantity"] == 1 and not b["section_id"] for b in made), made
        # The new job's window already knows its stack (the catalog is read before it opens).
        expect(page.locator("#document-batch-detail").get_by_role("heading", name="Print", exact=True)).to_be_visible()
        expect(page.locator("#document-batch-detail [data-stack-note]")).to_contain_text("of 2 printed as one stack")
        page.get_by_role("button", name="Close dialog", exact=True).click()
        expect(workspace.locator(".document-export").filter(has_text="Chapter 3 Quiz")).to_have_count(2)
        # Once one version's job is superseded the stack is no longer printed whole: that job's window says it prints nothing
        # more, and the other version's window prints its own file (never "print the stack" from a job that prints nothing).
        listed = {b["id"]: b for b in fixture.documents.catalog()["batches"]}
        sibling = next(b["id"] for b in listed.values() if b["export"] == listed[first_job]["export"] and b["id"] != first_job)
        fixture.documents.transition(first_job, "supersede")
        page.goto(address + f"/#documents?batch={first_job}")
        detail = page.locator("#document-batch-detail")
        expect(detail.get_by_role("heading", name="Printed as one stack", exact=True)).to_be_visible()
        expect(detail.locator("[data-stack-note]")).to_contain_text("This job was superseded and prints nothing more; the other versions still print from their own jobs.")
        expect(detail.get_by_role("link", name="Open paper PDF", exact=True)).to_have_count(0)
        screenshots(page, "quiz-draft-stack-ended")
        page.get_by_role("button", name="Close dialog", exact=True).click()
        page.goto(address + f"/#documents?batch={sibling}")
        detail = page.locator("#document-batch-detail")
        expect(detail.get_by_role("heading", name="Print this job", exact=True)).to_be_visible()
        expect(detail.locator("[data-stack-note]")).to_contain_text("The stack file is no longer available")
        expect(detail.get_by_role("link", name="Open paper PDF", exact=True)).to_be_visible()
        screenshots(page, "quiz-draft-stack-broken")
        page.get_by_role("button", name="Close dialog", exact=True).click()
        assert not errors, errors
        assert not bad, bad
        # A link to a draft that does not exist: a way back, and no read after read (bugs-frontend-1).
        page.goto(address + "/#documents/quiz/no-such-draft")
        expect(page.locator("#quiz-draft-root .empty-state")).to_contain_text("This quiz draft is not available")
        page.wait_for_timeout(500)
        drafts_read = []
        page.on("request", lambda request: drafts_read.append(request.url) if "/api/quiz-drafts/no-such-draft" in request.url else None)
        page.wait_for_timeout(3000)
        assert len(drafts_read) <= 1, len(drafts_read)
        page.locator("#quiz-draft-root").get_by_role("link", name=re.compile(r"^Assignments")).click()
        wait_until(page, "location.hash === '#assignments'")
        errors[:] = [item for item in errors if "404" not in item]
        bad[:] = [item for item in bad if "no-such-draft" not in item[1]]
        assert not errors, errors
        assert not bad, bad
        browser.close()
    print("Quiz draft browser checks passed.")
finally:
    server.close()
    fixture.doCleanups()
