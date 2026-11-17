"""Regrade requests on the Return stage in a real browser (REGRADES, requirement, 2026-10-07): the Regrade requests sub-tab with its
needs-you count, the list naming the student, one request open in place (their words as text, where the grade is, their work with
Change grade), the model's draft in the reply box (never over typed text), Apply's preview then its run to the end, the published
confirmation, Mark handled, the Review step without the old fold, and the narrowest desktop page (1180px, the app's floor). The page
runs against the synthetic FRQ fixture app; the regrade routes are browser stubs shaped like `grading/regrade_apply.py`'s answers, so
nothing here reaches Gradescope, Canvas or a model."""
import json
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import expect, sync_playwright  # noqa: E402
from waitress.server import create_server  # noqa: E402
from test_grading_review_failed import FailedToGrade  # noqa: E402

# A plan whose responses were graded by the fixture's fake host: a response the panel shows (its crops and rubric) and the desk opens.
fixture = FailedToGrade("test_a_failed_response_is_listed_with_its_reason_until_a_retry_grades_it")
fixture.setUp()
fixture.queue()
fixture.run_all()
plan_id = fixture.plan["id"]
response_id = next(r["id"] for r in fixture.app.extensions["frq_grading"].responses(plan_id) if r.get("current_result_id"))
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
BASE = f"/api/grading/assignments/{plan_id}/regrade-requests"
state = {"state": "open", "applied": [], "continued": 0, "drafts": 0, "marks": [], "suggest": False, "approved": []}
SUGGESTED = "Hi Alice,\n\nYou were right about the setup: Question 20 is now 6 of 6."
DRAFT = "Hi Alice,\n\nYou lost 2 points because the final answer is not the exact value."
STEPS = [{"step": "gradescope", "title": "Gradescope", "from": "4", "to": "6", "published": ["Common Exam 1 - Partial Credit"]},
         {"step": "reply", "title": "Reply", "expect": "6"},
         {"step": "gradebook", "title": "Gradebook", "from": "72", "to": "74", "typed": False, "replace": False, "question": {"from": "4", "to": "6"}},
         {"step": "canvas", "title": "Canvas", "from": "72", "to": "74", "course": "MATH-140-508"}]


def item():
    return {"id": "m1", "created_at": "2026-10-06T22:13:40+00:00", "text": "I still got 15.27: <b>see my work</b>.", "question_title": "Question 20",
            "assignment_title": "Common Exam 1", "url": "https://www.gradescope.com/courses/1/questions/2/submissions/3/grade", "reply": None,
            "state": state["state"], "replies": [], "mark": None, "response_id": response_id, "question_id": "q", "exam_version": "A",
            "question": "Question 20", "matched": True, "how": "Faculty's grade on Gradescope", "reason": "", "can_reply": state["state"] == "open",
            "student": {"name": "Example, Alice", "section": 508}, "faculty": {"points": "4", "of": "6"}, "apply": None}


def counts():
    return {"open": int(state["state"] == "open"), "answered": int(state["state"] == "answered"), "handled": int(state["state"] == "handled")}


def run_view(done):
    if done:
        steps = [{**s, "status": "done", "detail": ""} for s in STEPS]
    else:
        steps = [{**STEPS[0], "status": "waiting", "detail": "Faculty is writing the grade on Gradescope."}] + [{**s, "status": "pending", "detail": ""} for s in STEPS[1:]]
    return {"id": "run1", "created_at": "2026-10-07T10:00:00+00:00", "steps": steps, "notes": [], "finished": done, "waiting": not done, "stopped": False,
            "next": None if done else "gradescope"}


def detail():
    return {**item(), "student": {"name": "Example, Alice", "section": 508},
            "where": {"faculty": {"points": "4", "of": "6"}, "row": "s:e", "gradescope": {"points": "4", "state": "sent"},
                      "certified": {"earned": "72", "possible": "100", "status": "scored"}, "gradebook": {"value": "72", "source": "grading"},
                      "canvas": {"value": "72", "state": "accepted", "at": "2026-10-06T19:14:00+00:00"}, "item_id": "i", "term_id": "t"},
            "draft": {"id": "d1", "kind": "keep", "status": "complete", "reply": DRAFT, "assessment": "15.27 is 107/7 rounded; the line asks for the exact value."}
            if state["drafts"] else None,
            "apply": run_view(state["continued"] > 0) if state["applied"] or state["approved"] else None,
            # REGRADES "FOLLOW-UP": Follow-up's one suggestion, shown on Return too.
            "suggestion": {"state": "ready", "run": "run-f", "href": "#follow-ups", "action": {
                "id": "card1", "seen": 5, "decision": "change", "lines": ["R3_1"], "adjustment": None, "reply": SUGGESTED,
                "reason": "The setup line applies: their setup matches the key.", "from": "4", "to": "6", "of": "6",
                "steps": [{"step": "grade", "from": "4", "to": "6", "added": ["R3_1"], "removed": []}, *STEPS], "notes": []}}
            if state["suggest"] and not state["approved"] else None}


def route(page):
    page.route(f"**{BASE}", lambda r: r.fulfill(json={"assignment_id": plan_id, "requests": [{k: v for k, v in item().items() if k not in ("student", "faculty", "apply")}],
                                                      "counts": counts()}))
    page.route(f"**{BASE}/panel", lambda r: r.fulfill(json={"assignment_id": plan_id, "requests": [item()], "counts": counts()}))
    page.route(f"**{BASE}/m1", lambda r: r.fulfill(json=detail()))

    def draft(r):
        state["drafts"] += 1
        r.fulfill(json=detail()["draft"])
    page.route(f"**{BASE}/m1/draft", draft)
    page.route(f"**{BASE}/m1/apply/preview", lambda r: r.fulfill(json={"message_id": "m1", "response_id": response_id, "steps": STEPS, "notes": [],
                                                                         "published": ["900"], "replace": False, "fingerprint": "f" * 64, "where": detail()["where"]}))

    def apply(r):
        body = json.loads(r.request.post_data)
        assert body["confirmation"] == "apply" and body["fingerprint"] == "f" * 64 and body["published"] == ["900"], body
        state["applied"].append(body)
        r.fulfill(json=run_view(False))
    page.route(f"**{BASE}/m1/apply", apply)

    def advance(r):
        state["continued"] += 1
        state["state"] = "answered"
        r.fulfill(json=run_view(True))
    page.route(f"**{BASE}/applies/run1/continue", advance)

    def mark(r):
        state["marks"].append(json.loads(r.request.post_data)["action"])
        state["state"] = "handled"
        r.fulfill(json={"assignment_id": plan_id, "requests": [item()], "counts": counts()})
    page.route(f"**{BASE}/m1/mark", mark)

    def approve(r):
        state["approved"].append(json.loads(r.request.post_data))
        r.fulfill(json={"stale": False, "status": 200, "result": {"status": "started", "apply": run_view(False)}, "action": {"id": "card1"}})
    page.route("**/api/assistant/actions/card1/approve", approve)


def main():
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            route(page)
            page.goto(address + f"/#grade-delivery/{plan_id}?view=regrades")
            tab = page.locator("[data-review-regrades]")
            expect(tab).to_have_attribute("aria-current", "page")
            expect(tab).to_contain_text("Regrade requests 1")
            expect(tab).to_have_attribute("data-needs", "")
            row = page.locator("[data-regrade-open='m1']")
            expect(row).to_contain_text("Example, Alice · 508")
            expect(row).to_contain_text("Question 20 · Version A")
            panel = page.locator(".regrade-panel")
            expect(panel.locator("#regrade-panel-title")).to_have_text("Example, Alice · Section 508")
            expect(panel.locator(".grading-regrade-request")).to_have_text("I still got 15.27: <b>see my work</b>.")  # text, not markup
            expect(panel.locator("[data-regrade-where]")).to_contain_text("All agree")
            expect(panel.get_by_role("link", name="Open on Gradescope")).to_have_attribute("href", "https://www.gradescope.com/courses/1/questions/2/submissions/3/grade")
            expect(panel.locator("[data-regrade-work] [data-cert-change]")).to_be_visible()

            # The model's draft fills the empty reply; typed text is never replaced, and Use the draft brings it back.
            reply = panel.locator("[data-regrade-reply-text]")
            expect(reply).to_have_value(DRAFT)
            expect(panel.locator("[data-regrade-draft-note]")).to_contain_text("The model's view")
            reply.fill("Looked again: the exact value is required.")
            expect(panel.get_by_role("button", name="Use the draft")).to_be_visible()
            panel.get_by_role("button", name="Use the draft").click()
            expect(reply).to_have_value(DRAFT)

            # Apply: the preview lists every step in order; the published exam is confirmed; the run goes to the end while open.
            panel.get_by_role("button", name="Review what Apply does").click()
            preview = panel.locator("[data-regrade-apply-preview]")
            expect(preview.locator(".regrade-steps > li")).to_have_count(4)
            expect(preview).to_contain_text("This grade on Gradescope: 4 → 6 · published: the student sees it at once")
            expect(preview).to_contain_text("Gradebook, certifying just this change: 72 → 74")
            expect(preview).to_contain_text("Canvas (MATH-140-508): 72 → 74")
            assert state["applied"] == []
            page.once("dialog", lambda dialog: dialog.accept())
            preview.get_by_role("button", name="Apply", exact=True).click()
            expect(panel.locator("[data-regrade-run-view]")).to_contain_text("Apply · Working")
            page.evaluate("location.hash = '#overview'")
            expect(page.locator(".regrade-panel")).to_have_count(0)
            page.wait_for_timeout(3500)  # Past the panel's 3 s continuation interval.
            assert state["continued"] == 0, "REGRADES: a closed Return page leaves Apply waiting"
            page.evaluate(f"location.hash = '#grade-delivery/{plan_id}?view=regrades&request=m1'")
            expect(panel.locator("[data-regrade-run-view]")).to_contain_text("Apply · Finished", timeout=10000)
            expect(page.locator("#toast")).to_contain_text("Applied.")
            assert [a["reply"] for a in state["applied"]] == [DRAFT], state["applied"]
            assert state["continued"] >= 1
            expect(page.locator(".regrade-panel [data-regrade-reply-text]")).to_have_count(0)  # answered: the reply box goes

            # Change grade mounts the one review desk inline, approved and manual only.
            state.update(state="open", applied=[], continued=0)
            page.goto(address + f"/#grade-delivery/{plan_id}?view=regrades&request=m1")
            page.reload()
            panel = page.locator(".regrade-panel")
            edited_reply = "I checked the work again. This is my unfinished reply."
            panel.locator("[data-regrade-reply-text]").fill(edited_reply)
            panel.locator("[data-cert-change]").click()
            expect(panel.locator("[data-regrade-desk] form")).to_be_visible()
            panel.locator("[data-regrade-desk-cancel]").click()
            expect(panel.locator("[data-regrade-reply-text]")).to_have_value(edited_reply)
            panel.get_by_role("button", name="Mark handled").click()
            expect(page.locator("#toast")).to_contain_text("Marked handled.")
            assert state["marks"] == ["handled"]

            # Follow-up's suggestion (REGRADES "FOLLOW-UP"): shown on Return, its reply in the box, no separate draft, approved from here.
            state.update(state="open", suggest=True, drafts=0, continued=0)
            page.goto(address + f"/#grade-delivery/{plan_id}?view=regrades&request=m1")
            page.reload()
            panel = page.locator(".regrade-panel")
            block = panel.locator("[data-regrade-suggestion]")
            expect(block).to_contain_text("Follow-up suggests: Change the grade to 6 / 6")
            expect(block).to_contain_text("Rubric lines +R3_1")
            expect(panel.locator("[data-regrade-reply-text]")).to_have_value(SUGGESTED)
            expect(panel.get_by_role("button", name="Apply this reply myself…")).to_be_visible()
            assert state["drafts"] == 0, "no separate draft while Follow-up has a suggestion"
            page.once("dialog", lambda dialog: dialog.accept())
            block.get_by_role("button", name="Approve the suggestion").click()
            expect(panel.locator("[data-regrade-run-view]")).to_contain_text("Apply · Finished", timeout=10000)
            assert state["approved"] == [{"seen": 5}], state["approved"]

            # A delayed draft of one request must not become another student's reply.
            state.update(state="open", suggest=False, drafts=0, applied=[], approved=[], continued=0)
            second = {**detail(), "id": "m2", "student": {"name": "Example, Bob", "section": 508}, "draft": None, "apply": None}
            second_reply = "Hi Bob, this is the reply to your work."
            page.route(f"**{BASE}/panel", lambda r: r.fulfill(json={"assignment_id": plan_id, "requests": [item(), second], "counts": counts()}))
            page.route(f"**{BASE}/m2", lambda r: r.fulfill(json=second))
            page.route(f"**{BASE}/m2/draft", lambda r: r.fulfill(json={"status": "complete", "reply": second_reply}))
            held = []
            page.route(f"**{BASE}/m1/draft", lambda r: held.append(r))
            page.goto(address + f"/#grade-delivery/{plan_id}?view=regrades&request=m1")
            with page.expect_request(lambda r: r.url.endswith("/m1/draft")):
                page.reload()
            page.evaluate("window.__regradePage = document.querySelector('.regrade-page')")
            page.locator("[data-regrade-open='m2']").click()
            expect(panel.locator("[data-regrade-reply-text]")).to_have_value(second_reply)
            assert page.evaluate("window.__regradePage === document.querySelector('.regrade-page')"), "Changing only the request keeps the panel mounted"
            assert len(held) == 1
            with page.expect_response(lambda r: r.url.endswith("/m1/draft")):
                held[0].fulfill(json={"status": "complete", "reply": DRAFT, "assessment": "Alice's assessment"})
            page.evaluate("() => new Promise(requestAnimationFrame)")
            expect(panel.locator("[data-regrade-reply-text]")).to_have_value(second_reply)
            expect(panel.locator("[data-regrade-draft-note]")).not_to_contain_text("Alice's assessment")

            # Editing while Apply's preview waits invalidates the old preview.
            held_preview = []
            page.route(f"**{BASE}/m2/apply/preview", lambda r: held_preview.append(r))
            with page.expect_request(lambda r: r.url.endswith("/m2/apply/preview")):
                panel.get_by_role("button", name="Review what Apply does").click()
            panel.locator("[data-regrade-reply-text]").fill("My newer reply to Bob.")
            with page.expect_response(lambda r: r.url.endswith("/m2/apply/preview")):
                held_preview[0].fulfill(json={"steps": STEPS, "notes": [], "fingerprint": "stale", "published": []})
            page.evaluate("() => new Promise(requestAnimationFrame)")
            expect(panel.locator("[data-regrade-apply-preview]")).to_have_count(0)
            expect(panel.get_by_role("button", name="Review what Apply does")).to_be_visible()
            page.route(f"**{BASE}/m1/draft", lambda r: r.fulfill(json={"status": "complete", "reply": DRAFT}))

            # The Review step no longer carries the regrade requests.
            page.once("dialog", lambda dialog: dialog.accept())
            page.goto(address + f"/#grading/{plan_id}?tab=review")
            expect(page.locator(".grading-review-band")).to_be_visible()
            expect(page.locator("[data-workspace-regrades]")).to_have_count(0)

            # Desktop only (DESIGN-LANGUAGE, 2026-10-03): at the 1180px floor the panel never scrolls sideways.
            state.update(state="open")
            narrow = browser.new_page(viewport={"width": 1180, "height": 844})
            route(narrow)
            narrow.goto(address + f"/#grade-delivery/{plan_id}?view=regrades")
            expect(narrow.locator(".regrade-panel")).to_be_visible()
            width = narrow.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert width <= 0, f"the 1180px page scrolls sideways by {width}px"
            Path("test-results").mkdir(exist_ok=True)
            page.goto(address + f"/#grade-delivery/{plan_id}?view=regrades")
            expect(page.locator(".regrade-panel")).to_be_visible()
            page.screenshot(path="test-results/regrades-return.png", full_page=True)
            assert not errors, errors
            browser.close()
    finally:
        server.close()
        fixture.doCleanups()
    print("Regrade browser checks passed: Return sub-tab and count, named list, escaped words, where the grade is, draft never over typed text, "
          "Apply preview then run, published confirmation, the desk inline, mark handled, Review without the fold, the 1180px floor.")


if __name__ == "__main__":
    main()
