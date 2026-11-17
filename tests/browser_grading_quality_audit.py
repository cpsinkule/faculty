"""Final Quality Audit on Certification and Export (grading/quality_audit.py, static/grading-certification-audit.js), against the synthetic
FRQ fixture app with the certification read and the audit routes answered by browser stubs shaped like the server's (synthetic names).
The header offers the audit as a SECONDARY action beside the one primary; the start dialog names the model and says nothing is changed;
a running audit shows its progress and a Stop; a finished one lists its findings question by question, each response a link to that
student's grade on that question, with the suggestion and what the scan check saw."""
import json
import re
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
plan_id = fixture.plan["id"]
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
BASE = f"/api/grading/assignments/{plan_id}/certification"


def student(row, name):
    return {"row": row, "name": name, "section": "507", "exam_version": "A", "participant_version": 1, "expectation": "expected", "collection": "complete",
            "status": "complete", "reason": "", "frq": {"state": "complete", "earned": "8", "possible": "10", "final": 1, "expected": 1}, "bubble": {"state": "none"},
            "total": {"earned": "8", "possible": "10", "pct": 80}, "scores": ["8"], "flagged": 0, "zero_questions": 0, "dr": [],
            "gradescope": {"paper": True, "grades": {"sent": 1, "changed": 0, "not_sent": 0}, "sheet": None}, "certified": None, "changed_since": False}


STUDENTS = [student("s507:0000000001", "Ada Synthetic"), student("s507:0000000002", "Ben Synthetic"), student("s507:0000000003", "Cy Synthetic")]
stats = {"n": 3, "mean": 8, "median": 8, "sd": 0.5, "min": 7, "max": 9, "histogram": [], "excluded": {"zero": 0, "missing": 0, "left_out": 0, "excused": 0}}
VIEW = {"plan_id": plan_id, "title": "Synthetic exam", "parts": ["frq"], "possible": {"frq": "10", "total": "10"}, "versions": [], "sections": [{"section_id": "s507", "number": "507"}],
        "certification": {"state": "none", "id": None, "created_at": None, "changed": [], "gradebook": None}, "history": [],
        "stats": {"total": stats, "frq": stats, "by_version": {}, "by_section": {}},
        "questions": [{"question_id": "q1", "title": "Question 19", "printed_number": "19", "version": "A", "max": "2", "n_final": 3, "not_final": 0, "mean": 1, "median": 1,
                       "sd": .5, "pct_full": 0, "pct_zero": 30, "automatic": 3, "instructor": 0, "flagged": 0, "overridden": 0,
                       "gradescope": {"sent": 0, "changed": 0, "not_sent": 0, "unsendable": None}}],
        "bubble_questions": {}, "students": STUDENTS,
        "counts": {"participants": 3, "complete": 3, "missing": 0, "flagged": 0, "zero_somewhere": 0, "changed_since": 0, "dr": 0, "excused": 0, "not_on_gradescope": 0},
        "checks": [], "blocking": 0, "to_confirm": 0, "gradescope": {"configured": False, "written": {}, "sheets": {}, "live": False}, "live": False}
ref = lambda n, score: {"ref": f"R{n}", "response_id": f"resp-{n}", "row": STUDENTS[n - 1]["row"], "score": score, "lines": [], "decided_by": "automatic"}
RUNNING = {"id": "audit-1", "status": "running", "cancelled": False, "error": "", "created_at": "2026-09-27T22:00:00+00:00", "updated_at": "2026-09-27T22:00:05+00:00",
           "model": "subscription-claude-opus-5-5", "rule": "quality-audit-v1", "questions": 1, "responses": 3, "progress": {"requests": 1, "read": 0, "checked": 0}}
DONE = {**RUNNING, "status": "complete", "progress": {"requests": 1, "read": 1, "checked": 1},
        "totals": {"findings": 1, "confirmed": 1, "not_confirmed": 0, "checks": 1, "high": 1, "questions_failed": 0},
        "result": {"rule": "quality-audit-v1", "summary": "1 finding across 1 question; 1 held up against the scans, 0 did not. 1 automatic check to look at.",
                   "totals": {"findings": 1, "confirmed": 1, "not_confirmed": 0, "checks": 1, "high": 1, "questions_failed": 0},
                   "checks": [{"kind": "same_answer", "question_id": "q1", "title": "Question 19 · Version A", "detail": "2 responses give the final answer “$280,000” but received 2 different sets of rubric lines.",
                               "responses": [ref(2, 0.75), ref(3, 0)]}],
                   "questions": [{"question_id": "q1", "label": "Question 19 · Version A", "exam_version": "A", "responses": 3, "summaries": ["Revenue credit is applied unevenly."], "error": "",
                                  "line_text": {"R2_3": {"text": "Correct break-even revenue, but the incorrect break-even quantity", "points": 0.75}, "R2_6": {"text": "No Answer or Completely Incorrect", "points": 0}},
                                  "findings": [{"kind": "inconsistent", "severity": "high", "refs": [ref(2, 0.75), ref(3, 0)], "lines": ["R2_3"], "changed": [],
                                                "explanation": "Both wrote $280,000 as the revenue; one received R2_3, the other R2_6.",
                                                "suggestions": [{"ref": "R3", "response_id": "resp-3", "row": STUDENTS[2]["row"], "add": ["R2_3"], "remove": ["R2_6"], "note": "same as R2"}],
                                                "confirm": {"verdict": "confirmed", "explanation": "Both scans show $280,000 on the answer line.",
                                                            "suggestions": [{"ref": "R3", "response_id": "resp-3", "row": STUDENTS[2]["row"], "add": ["R2_3"], "remove": ["R2_6"], "note": ""}]}}]}]}}
DONE['result']['questions'][0]['image_sample'] = {
    'selected': 2, 'read': 1, 'refs': [ref(2, 0.75), ref(3, 0)], 'changed': [],
    'error': 'One selected image was unavailable.',
    'readings': [{'ref': 'R2', 'work': '<sample> x = 100', 'reason': 'Visible writing', 'uncertain': True}]}
DONE['result']['questions'][0]['findings'][0]['source'] = 'image_sample'
DONE['result']['questions'][0]['findings'][0]['confirm']['shown_refs'] = ['R2']
state = {"audit": None, "posts": [], "cancels": 0}


def routes(route):
    request, path = route.request, route.request.url.split(address, 1)[-1].split("?")[0]
    if path == BASE and request.method == "GET":
        route.fulfill(json=VIEW)
    elif path == BASE + "/audit" and request.method == "GET":
        route.fulfill(json={"audit": state["audit"], "history": [], "model": "subscription-claude-opus-5-5"})
    elif path == BASE + "/audit" and request.method == "POST":
        state["posts"].append(json.loads(request.post_data or "{}"))
        state["audit"] = RUNNING
        route.fulfill(json=RUNNING)
    elif path == BASE + "/audit/audit-1/cancel" and request.method == "POST":
        state["cancels"] += 1
        state["audit"] = {**RUNNING, "status": "failed", "cancelled": True, "error": "Stopped by the instructor."}
        route.fulfill(json={"audit": state["audit"], "history": [], "model": "subscription-claude-opus-5-5"})
    else:
        route.fallback()


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        page.route(re.compile(re.escape(address + BASE) + r"(/.*)?(\?.*)?$"), routes)
        page.goto(f"{address}/#grading/{plan_id}?tab=export")
        root = page.locator(".grading-certification")
        # A secondary action beside the one primary; no audit yet, so no section.
        run = root.locator("[data-cert-audit-run]")
        expect(run).to_have_text("Final quality audit…")
        expect(root.locator("[data-cert-main] .button.primary")).to_have_count(1)
        assert "primary" not in (run.get_attribute("class") or "")
        expect(root.locator("[data-cert-audit]")).to_have_count(0)
        # The dialog names the model and says nothing is changed; starting sends only a request ID.
        run.click()
        dialog = page.locator("#dialog")
        expect(dialog).to_contain_text("subscription-claude-opus-5-5")
        expect(dialog).to_contain_text("It changes no grade and does not hold back certifying.")
        expect(dialog).to_contain_text("before seeing the saved transcript or grade")
        dialog.get_by_role("button", name="Start audit").click()
        audit = root.locator("[data-cert-audit]")
        expect(audit).to_contain_text("Auditing 3 final grades of 1 question")
        assert list(state["posts"][0]) == ["request_id"], state["posts"]
        # Stop ends it and says so.
        audit.get_by_role("button", name="Stop audit").click()
        expect(audit).to_contain_text("The audit was stopped")
        assert state["cancels"] == 1
        # A finished audit: its findings, each response a link to that student's grade on that question.
        state["audit"] = DONE
        page.reload()
        audit = root.locator("[data-cert-audit]")
        expect(audit.locator("[data-cert-audit-summary]")).to_contain_text("1 finding across 1 question")
        finding = audit.locator("[data-cert-audit-finding]")
        expect(finding).to_contain_text("Similar work graded differently")
        expect(finding).to_contain_text("High")
        expect(finding).to_contain_text("Found in the image sample")
        expect(finding).to_contain_text("The scan check covered 1 of these 2 responses.")
        expect(finding).to_contain_text("Held up against the scans")
        expect(finding).to_contain_text("Both wrote $280,000 as the revenue")
        links = finding.locator(".certification-audit-responses [data-cert-response-open]")
        expect(links).to_have_count(2)
        expect(links.nth(1)).to_contain_text("Cy Synthetic")
        assert links.nth(1).get_attribute("data-question-id") == "q1"
        expect(finding.locator(".certification-audit-suggestions")).to_contain_text("Cy Synthetic: add R2_3 (+0.75); remove R2_6 (0)")
        finding.locator("details summary").click()
        expect(finding).to_contain_text("Both scans show $280,000 on the answer line.")
        # One finding at a time (2026-09-29): what held up is shown first; the other piles are a chip away, never one long list.
        expect(audit.locator("[data-cert-audit-finding]")).to_have_count(1)
        expect(audit.locator("[data-cert-audit-position]")).to_contain_text("1 of 1 · Question 19 · Version A")
        expect(audit.locator('[data-cert-audit-check="same_answer"]')).to_have_count(0)
        audit.locator('[data-filter-group="cert-audit"] [data-filter="checks"]').click()
        expect(audit.locator('[data-cert-audit-check="same_answer"]')).to_contain_text("Same final answer, different rubric lines")
        expect(audit.locator("[data-cert-audit-finding]")).to_have_count(0)
        audit.locator('[data-filter-group="cert-audit"] [data-filter="samples"]').click()
        expect(audit.get_by_text('Image sample · 1 of 2 read', exact=True)).to_be_visible()
        expect(audit).to_contain_text('One selected image was unavailable.')
        audit.get_by_text('R2 · reading before grades · uncertain', exact=True).click()
        expect(audit.locator('pre')).to_have_text('<sample> x = 100')
        expect(audit.locator('sample')).to_have_count(0)
        page.screenshot(path=str(OUTPUT / "grading-quality-audit.png"), full_page=True)
        # A finished report is one fold (certify-11): open while nothing is certified after it; certified later, it is folded on the
        # next visit, and the instructor's own opening is remembered.
        fold = audit.locator("#cert-fold-audit")
        expect(fold).to_have_attribute("open", "")
        expect(fold.locator(":scope > summary")).to_contain_text("Final quality audit")
        expect(fold.locator(":scope > summary .disclosure-hint")).to_contain_text("1 finding · 1 held up against the scans")
        VIEW["certification"] = {"state": "current", "id": "c1", "created_at": "2026-09-28T00:00:00+00:00", "changed": [],
                                 "gradebook": {"title": "Synthetic exam", "item_id": "i1", "written": 3, "kept": 0}}
        page.reload()
        expect(fold.locator(":scope > summary")).to_be_visible()
        expect(fold).not_to_have_attribute("open", "")
        expect(audit.locator("[data-cert-audit-finding]")).to_be_hidden()
        fold.locator(":scope > summary").click()
        expect(audit.locator("[data-cert-audit-finding]")).to_be_visible()
        page.reload()
        expect(audit.locator("[data-cert-audit-finding]")).to_be_visible()
        assert not errors, errors
        browser.close()
        print("Quality audit page passed: secondary action, start dialog, running and stopped states, findings linked to each student's grade.")
finally:
    server.close()
    fixture.doCleanups()
