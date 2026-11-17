"""Export step: the Send to Gradescope panel (AGENTS.md "Sending grades to Gradescope").

The page runs against the synthetic FRQ fixture app. Its first look is the real, unconfigured status route (no sign-in file here).
Everything configured is answered by browser route stubs shaped like `grading/gradescope_sync.py`'s status, preview, send,
cancel, retry and problems: nothing here reaches Gradescope, a fake of it, or a sign-in file."""
import json
import re
import sys
import threading
import uuid
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
BASE = f"/api/grading/gradescope/assignments/{plan_id}"

COURSES = [{"id": "1413801", "short": "140-507", "name": "MATH 140 Fall 2026", "term": "Fall 2026"},
           {"id": "1413802", "short": "140-508", "name": "MATH 140 Fall 2026", "term": "Fall 2026"},
           {"id": "1413803", "short": "140-509", "name": "MATH 140 Fall 2026", "term": "Fall 2026"},  # no assignment for this yet
           # A server bug sending the coordinator's course must still never put it on the page.
           {"id": "1000001", "short": "Math 140 for Instructors", "name": "Coordinator", "term": "Fall 2026"}]
EXAMS = {"1413801": [{"id": "8681208", "title": "Test 1", "versions": ["A", "B"]}, {"id": "8681300", "title": "Quiz <2>", "versions": [""]}],
         "1413802": [{"id": "8681400", "title": "Test 1", "versions": ["A", "B"]}]}


def preview(targets, papers_only=False, replace=False):
    """A preview shaped like `GradescopeSender._compose`'s view for the choices asked for (papers only: no grade planned; replace:
    the grades someone else made on Gradescope that this send would replace, 2026-10-01)."""
    sections = []
    for sid, number, course in (("s507", 507, "1413801"), ("s508", 508, "1413802")):
        ask = targets.get(sid) or {}
        course_id = ask.get("course_id") or course
        if ask.get("leave_out"):
            sections.append({"section_id": sid, "section": number, "course_id": course_id, "course": "", "proposed": False, "containers": [],
                             "container_id": ask.get("container_id") or "", "container": "", "versions": {}, "problem": "", "left_out": True})
            continue
        exams = EXAMS.get(course_id, [])
        container = ask.get("container_id") or (exams[0]["id"] if exams else "")
        chosen = next((e for e in exams if e["id"] == container), None)
        sections.append({"section_id": sid, "section": number, "course_id": course_id, "course": course_id, "proposed": not ask,
                         "containers": exams, "container_id": chosen["id"] if chosen else "", "container": chosen["title"] if chosen else "",
                         "versions": {"A": "1", "B": "2"} if chosen else {}, "problem": "" if chosen else "Choose the Gradescope assignment this goes to in this course."})
        if state["lacking"] and sid == "s508" and chosen:  # 2026-10-06: a paper of this section is in a version its set lacks
            sections[-1].update(problem=f"{chosen['title']} has no version C.", missing_versions=["C"])
    # An exam whose grades are published (requirement, 2026-09-26): a warning on its section and one more question before sending.
    published = []
    for section in sections:
        if state["published"] and section["section_id"] == "s507" and section["container_id"]:
            section.update(published=True, warning=f"Grades on {section['container']} are published on Gradescope: students see each paper and grade as soon as Faculty writes it. Sending asks you to confirm this exam.")
            published.append({"section_id": "s507", "section": 507, "course": "140-507 · MATH 140 Fall 2026", "container_id": section["container_id"],
                              "container": section["container"], "papers": 40, "grades": 504})
    fingerprint = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(targets, sort_keys=True) + ("papers" if papers_only else "") + ("replace" if replace else "")).hex * 2
    if papers_only:
        return {"assignment_id": plan_id, "configured": True, "problem": "", "courses": COURSES, "sections": sections, "published": published,
                "summary": {"papers": {"upload": 40, "already": 2, "skipped": 3}, "grades": {"send": 0, "update": 0, "already": 0, "refused": 0, "held": 0},
                            "final": 566, "responses": 640, "not_final": 74},
                "questions": [], "skipped": [{"name": "Student <One>", "section": 507, "reason": "No current reviewed paper."}], "refused": [], "warnings": [],
                "fingerprint": fingerprint, "running": None, "papers_only": True,
                "targets": {s["section_id"]: {"course_id": s["course_id"], "container_id": s["container_id"]} for s in sections if s["container_id"] and not s.get("left_out")}}
    view = {"assignment_id": plan_id, "configured": True, "problem": "", "courses": COURSES, "sections": sections, "published": published, "papers_only": False,
            "summary": {"papers": {"upload": 40, "already": 2, "skipped": 3, **({"left_out": 5} if any(s.get("left_out") for s in sections) else {})},
                        "grades": {"send": 500, "update": 4, "already": 10, "refused": 7, "held": 45, **({"left_out": 60} if any(s.get("left_out") for s in sections) else {})},
                        "final": 566, "responses": 640, "not_final": 74},
            "questions": [{"version": "", "faculty": "q1", "gradescope": "Question 1", "send": 500, "update": 4, "already": 10, "refused": 7, "held": 45,
                           "reasons": [{"reason": "Gradescope would give 3 for these items; Faculty's grade is <4>.", "count": 7}]}],
            "skipped": [{"name": "Student <One>", "section": 507, "reason": "No current reviewed paper."}],
            "refused": [{"name": "Student Two", "section": 508, "question": "q1", "reason": "No rubric line is checked."}],
            "warnings": ["q1: R2 is worded differently on Gradescope (“Correct <setup>”)."],
            "fingerprint": fingerprint, "running": None, "targets": {s["section_id"]: {"course_id": s["course_id"], "container_id": s["container_id"]} for s in sections if s["container_id"] and not s.get("left_out")}}
    if replace:
        view["replace_others"] = True
        view["summary"]["grades"].update(send=490, replace=10)
        view["questions"][0].update(send=490, replace=10)
        view["replacing"] = {"grades": 10, "score_changes": 9, "same": 126, "by": [{"name": "Ada <Fair>", "grades": 10}],
                             "listed": [{"name": "Student <Three>", "section": 508, "question": "q1", "gradescope": "0.5", "by": "Ada <Fair>", "faculty": "1"}]}
        view["replacements"] = REPLACEMENTS
    return view


# What the preview showed each replacement replacing, which the send carries back exactly (2026-10-01).
REPLACEMENTS = [[f"response-{n}", {"applied": ["9001"], "points": None, "comments": None, "score": "0.5", "by": "Ada <Fair>"}] for n in range(10)]
SENDS = []
# What is on Gradescope across every send, shaped like `GradescopeSender._coverage`.
COVERAGE = {"papers": {"total": 42, "sent": 40}, "grades": {"final": 520, "sent": 500, "changed": 4, "not_sent": 16}}
state = {"send_fail": 1, "send_conflict": 0, "sent": [], "previews": [], "cancelled": [], "retried": [], "sheet_previews": [], "sheet_sent": [], "published": False,
         "unlinked": False, "lacking": False, "create_previews": [], "created": [], "create_retried": []}
CREATIONS = []


def create_preview(body):
    """A creation preview shaped like `CreateMixin._creation_plan`'s view (requirement, 2026-09-27)."""
    title = body.get("title") or "Test <1>"
    if state["lacking"]:  # adding the version a section's set lacks (2026-10-06)
        return {"assignment_id": plan_id, "configured": True, "problem": "", "title": title, "ready": True, "running": None, "rule": "gradescope-create-v1",
                "versions": [{"letter": "C", "title": f"{title} Version C", "pages": 12, "questions": 8, "lines": 57}],
                "courses": [{"course_id": "1413802", "course": "140-508 · MATH 140 Fall 2026", "sections": [508], "problem": "",
                             "adds": {"container": "Test 1", "has": ["A", "B"], "letters": ["C"]}}],
                "sections": [{"section_id": "s507", "section": 507, "course_id": "1413801", "course": "140-507", "reason": "Already goes to an assignment on Gradescope."},
                             {"section_id": "s508", "section": 508, "course_id": "1413802", "course": "140-508", "reason": ""}],
                "problems": [], "warnings": [], "fingerprint": uuid.uuid5(uuid.NAMESPACE_URL, "add" + title).hex * 2}
    return {"assignment_id": plan_id, "configured": True, "problem": "", "title": title, "ready": True, "running": None, "rule": "gradescope-create-v1",
            "versions": [{"letter": letter, "title": f"{title} Version {letter}", "pages": 12, "questions": 8, "lines": 57} for letter in "AB"],
            "courses": [{"course_id": "1413803", "course": "140-509 · MATH 140 Fall 2026", "sections": [509], "problem": ""}],
            "sections": [{"section_id": "s507", "section": 507, "course_id": "1413801", "course": "140-507", "reason": "Already goes to an assignment on Gradescope."},
                         {"section_id": "s509", "section": 509, "course_id": "1413803", "course": "140-509", "reason": ""}],
            "problems": [], "warnings": ["Question 23 (Version A): its line groups become plain items on Gradescope; a grade Gradescope would score differently is refused when sending, never changed."],
            "fingerprint": uuid.uuid5(uuid.NAMESPACE_URL, "create" + title + json.dumps(body.get("targets") or {}, sort_keys=True)).hex * 2}
SHEET_SENDS = []


def sheet_preview(targets):
    """A bubble-sheet preview shaped like `SheetsMixin._sheet_compose`'s view."""
    exam = {"id": "401156", "title": "Common Exam 1 - Multiple Choice", "versions": ["A", "B", "C"]}
    left = (targets.get("s507") or {}).get("leave_out")
    section = {"section_id": "s507", "section": 507, "course_id": "1413801", "course": "140-507", "proposed": not targets, "containers": [] if left else [exam],
               "container_id": "" if left else "401156", "container": "" if left else exam["title"], "versions": {} if left else {"A": "1", "B": "2", "C": "3"},
               "problem": "", **({"left_out": True} if left else {})}
    return {"assignment_id": plan_id, "configured": True, "problem": "", "courses": COURSES, "sections": [section],
            "summary": {"sheets": {"upload": 0 if left else 80, "already": 0, "skipped": 2, **({"left_out": 80} if left else {})}, "read": 82, "waiting": 5},
            "skipped": [{"name": "", "section": "", "page": 12, "reason": "No student could be matched from the ID and name read."},
                        {"name": "Student <Five>", "section": 507, "page": 30, "reason": "No version was read on this sheet. Record it in the sheet's review in the Inbox."}],
            "warnings": [], "fingerprint": uuid.uuid5(uuid.NAMESPACE_URL, "sheets" + json.dumps(targets, sort_keys=True)).hex * 2, "running": None,
            "targets": {} if left else {"s507": {"course_id": "1413801", "container_id": "401156"}}}


def send_row(send_id, status, uploads, grades):
    return {"id": send_id, "status": status, "created_at": "2026-09-25T15:00:00+00:00", "updated_at": "2026-09-25T15:01:00+00:00",
            "plan": {"targets": [{"section": 507, "course": "140-507", "container": "Test 1"}], "summary": {"papers": {"upload": 40}, "grades": {"send": 500, "update": 4}}},
            "counts": {"uploads": uploads, "grades": grades}, "waiting": "Waiting to try again: Gradescope did not answer." if status == "running" else ""}


def gradescope(route):
    request, path = route.request, route.request.url.split(address, 1)[-1].split("?")[0]
    body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
    if path == BASE + "/create/preview":
        state["create_previews"].append(body)
        route.fulfill(json=create_preview(body))
    elif path == BASE + "/create":
        state["created"].append(body)
        CREATIONS.insert(0, {"id": "c1", "title": body["title"], "status": "running", "created_at": "2026-09-27T01:00:00+00:00", "updated_at": "2026-09-27T01:00:00+00:00",
                             "units": [{"course_id": "1413803", "course": "140-509", "status": "sending", "step": f"Rubric of Question 20 ({body['title']} Version A)", "error": "", "container_id": None}]})
        route.fulfill(status=201, json={"creation_id": "c1", "request_id": body["request_id"], "status": "pending", "title": body["title"], "created_at": "2026-09-27T01:00:00+00:00", "plan": {}})
    elif re.fullmatch(re.escape(BASE) + r"/creations/[^/]+/retry", path):
        state["create_retried"].append(path)
        CREATIONS[0]["units"][0].update(status="pending", error="")
        route.fulfill(json={"creation_id": "c1", "status": "running"})
    elif path == BASE + "/sheets" and request.method == "GET":
        route.fulfill(json={"assignment_id": plan_id, "configured": True, "problem": "", "targets": [], "sends": SHEET_SENDS,
                            "coverage": {"sheets": {"matched": 80, "sent": 0, "waiting": 5}}})
    elif path == BASE + "/sheets/preview":
        state["sheet_previews"].append(body)
        route.fulfill(json=sheet_preview(body.get("targets") or {}))
    elif path == BASE + "/sheets/send":
        state["sheet_sent"].append(body)
        SHEET_SENDS.insert(0, {"id": "sheets-1", "status": "running", "created_at": "2026-09-25T16:00:00+00:00", "updated_at": "2026-09-25T16:00:00+00:00",
                               "plan": {"targets": [{"section": 507, "container": "Common Exam 1 - Multiple Choice"}], "summary": {"sheets": {"upload": 80}}},
                               "counts": {"sheets": {"accepted": 20, "pending": 60}}, "waiting": ""})
        route.fulfill(status=201, json={"send_id": "sheets-1", "request_id": body["request_id"], "status": "pending", "created_at": "2026-09-25T16:00:00+00:00", "plan": {}})
    elif path == BASE + "/publications":  # the Publish grades section below the panels (its own browser check covers it)
        route.fulfill(json={"assignment_id": plan_id, "configured": True, "problem": "", "publications": []})
    elif path == BASE and request.method == "GET":
        state["reads"] = state.get("reads", 0) + 1
        route.fulfill(json={"assignment_id": plan_id, "configured": True, "problem": "", "coverage": COVERAGE, "targets": [
            {"section_id": "s507", "section": 507, "course_id": "1413801", "course_title": "140-507", "container_id": "8681208", "container_title": "Test 1"},
            *([{"section_id": "s509", "section": 509, "course_id": "1413803", "course_title": "140-509", "container_id": "", "container_title": ""}] if state["unlinked"] else [])],
            "sends": SENDS, "creations": CREATIONS})
    elif path == BASE + "/preview":
        state["previews"].append(body)
        route.fulfill(json=preview(body.get("targets") or {}, body.get("papers_only") is True, body.get("replace_others") is True))
    elif path == BASE + "/send":
        state["sent"].append(body)
        if state["send_fail"]:
            state["send_fail"] -= 1
            route.abort()  # the answer never arrives: the page must offer Retry request with the same request ID
        elif state["send_conflict"]:
            state["send_conflict"] -= 1
            route.fulfill(status=409, json={"error": "Something changed since the preview, in Faculty or on Gradescope."})
        else:
            send_id = f"send-{len(SENDS) + 1}"
            if body.get("papers_only"):
                row = send_row(send_id, "running", {"accepted": 10, "pending": 30}, {})
                row["plan"].update(papers_only=True, summary={"papers": {"upload": 40}, "grades": {"send": 0, "update": 0}})
                SENDS.insert(0, row)
            else:
                SENDS.insert(0, send_row(send_id, "running", {"accepted": 10, "pending": 30}, {"accepted": 100, "pending": 404}))
                if body.get("replace_others"):
                    SENDS[0]["plan"].update(replace_others=True, replacing=body.get("replacing"), summary={"papers": {"upload": 40}, "grades": {"send": 490, "update": 4, "replace": 10}})
            route.fulfill(status=201, json={"send_id": send_id, "request_id": body["request_id"], "status": "pending", "created_at": "2026-09-25T15:00:00+00:00",
                                            "plan": {"papers_only": True} if body.get("papers_only") else {}})
    elif path.endswith("/cancel"):
        state["cancelled"].append(path)
        SENDS[0] = send_row(SENDS[0]["id"], "cancelled", {"accepted": 10, "cancelled": 30}, {"accepted": 100, "cancelled": 404})
        route.fulfill(json={"send_id": SENDS[0]["id"], "status": "cancelled"})
    elif path.endswith("/retry"):
        state["retried"].append(path)
        route.fulfill(json={"send_id": "send-1", "status": "running"})
    elif path.endswith("/problems"):
        route.fulfill(json={"send_id": path.split("/")[-2], "problems": [
            {"name": "Student <Three>", "section": 507, "status": "failed", "question": "", "reason": "Gradescope did not keep the paper."},
            {"name": "Student Four", "section": 508, "status": "present", "question": "q1", "reason": "Someone graded this question on Gradescope; Faculty never overwrites it."}]})
    else:
        route.fulfill(status=404, json={"error": "unexpected " + path})


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 and not response.url.endswith("/templates/prepare") else None)

        # Not set up: the real route says so, with the way to set it up, and there is nothing to press.
        page.goto(f"{address}/#grading/{plan_id}?tab=export")
        # Certify (requirement, 2026-09-26; named for its tab, 2026-10-18): the analysis leads; Gradescope and the files are secondary sections below it.
        expect(page.locator(".grading-certification .grading-step-header h3")).to_have_text("Certify")
        fold = page.locator("#cert-fold-gradescope")
        expect(fold).to_be_visible()
        if fold.get_attribute("open") is None:
            fold.locator(":scope > summary").click()
        panel = page.locator(".grading-gradescope")
        expect(panel).to_contain_text("Gradescope is not set up for this Faculty.")
        expect(panel).not_to_contain_text("Faculty instance.")  # The server's restatement of the title is not repeated (review fix GR-6).
        expect(panel).to_contain_text("python3 ~/.config/faculty/save-gradescope.py")
        expect(panel.get_by_role("button", name="Send to Gradescope…", exact=True)).to_have_count(0)
        # The downloads stay beside the training data, below Gradescope, whatever Gradescope says.
        expect(page.locator("[data-cert-secondary] > #cert-fold-gradescope ~ .certification-files .grading-export-columns [data-export-downloads]")).to_be_visible()
        # Certifying records the grades in the gradebook, so the old "Record in the term gradebook" link is gone; the context row keeps its links.
        expect(page.locator("[data-export-gradebook]")).to_have_count(0)
        expect(page.locator(".grading-context [data-grading-exam]")).to_have_attribute("href", f"#exams/{fixture.plan['event_id']}")
        expect(page.locator("[data-export-downloads]").get_by_role("button", name="Scores per question", exact=True)).to_be_visible()

        # Set up, with an earlier send that has failures.
        SENDS.append(send_row("send-1", "complete", {"accepted": 38, "failed": 1, "present": 1}, {"accepted": 480, "failed": 2, "refused": 3}))
        page.route("**/api/grading/gradescope/**", gradescope)
        page.reload()
        expect(panel.get_by_role("button", name="Send to Gradescope…", exact=True)).to_be_visible()
        # One place to send (2026-10-18 review item 10): the fold's panel holds the page's only Send to Gradescope….
        expect(page.get_by_role("button", name="Send to Gradescope…", exact=True)).to_have_count(1)
        # The headline is what is on Gradescope across every send, not the latest send's own counts.
        expect(panel.locator("[data-gradescope-coverage]")).to_contain_text("40 of 42 papers · 500 of 520 final grades are on Gradescope")
        expect(panel.locator("[data-gradescope-coverage]")).to_contain_text("16 final grades not sent yet")
        expect(panel.locator("[data-gradescope-coverage]")).to_contain_text("4 changed since they were sent")
        expect(panel.get_by_role("button", name="Send to Gradescope…", exact=True)).to_have_class(re.compile(r"\bprimary\b"))
        expect(panel).to_contain_text("Finished")
        expect(panel.locator('[data-gradescope-send="send-1"]')).to_contain_text("40 of 40 papers finished")
        expect(panel.locator('[data-gradescope-send="send-1"] [data-gradescope-state="present"]')).to_contain_text("1 left alone (already on Gradescope)")
        expect(panel.locator('[data-gradescope-send="send-1"] [data-gradescope-state="failed"]').first).to_contain_text("1 did not go through")
        panel.get_by_text("Show problems · 7", exact=True).click()
        expect(panel.locator("[data-gradescope-problem-list] table")).to_contain_text("Student <Three>")
        expect(panel.locator("[data-gradescope-problem-list] table")).to_contain_text("did not go through")
        panel.get_by_role("button", name="Retry failed (3)", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Sending again only what did not go through.")
        assert state["retried"] == [BASE + "/sends/send-1/retry"], state["retried"]

        # The preview: proposals preselected, never the coordinator's course, and every count and reason shown.
        panel.get_by_role("button", name="Send to Gradescope…", exact=True).click()
        dialog = page.locator("#dialog")
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("40 to upload")
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("504 to send")
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("74 of 640 recorded responses have no final grade yet")
        course = dialog.get_by_label("Gradescope course for section 507", exact=True)
        expect(course).to_have_value("1413801")
        expect(dialog.get_by_label("Gradescope assignment for section 507", exact=True)).to_have_value("8681208")
        options = course.locator("option").all_inner_texts()
        assert not any("instructors" in option.lower() for option in options), options
        expect(dialog).to_contain_text("Proposed by Faculty")
        expect(dialog).to_contain_text("7 × Gradescope would give 3 for these items; Faculty's grade is <4>.")
        dialog.get_by_text("Refused grades · 7", exact=True).click()
        expect(dialog.locator("[data-gradescope-refused]")).to_contain_text("No rubric line is checked.")
        # Changing the course asks again with that course and no exam, so the server proposes one.
        before = len(state["previews"])
        course.select_option("1413802")
        expect(dialog.get_by_label("Gradescope assignment for section 507", exact=True)).to_have_value("8681400")
        assert state["previews"][before]["targets"]["s507"] == {"course_id": "1413802", "container_id": ""}, state["previews"][before]
        # A section can be left out of a send (requirement, 2026-09-25: send one section first and check it). It keeps its choices.
        before = len(state["previews"])
        dialog.locator('[data-gradescope-leave="s508"]').check()
        expect(dialog.locator('[data-gradescope-section="s508"]')).to_contain_text("Left out: none of this section’s papers or grades are sent this time.")
        assert state["previews"][before]["targets"]["s508"] == {"course_id": "1413802", "container_id": "8681400", "leave_out": True}, state["previews"][before]
        expect(dialog.get_by_label("Gradescope course for section 508", exact=True)).to_be_disabled()
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("5 in sections left out of this send")
        expect(dialog.locator('[data-gradescope-leave="s508"]')).to_be_checked()

        # THE BUBBLE SHEETS TOO (2026-10-26): the sheets' own preview is read beside this one, and its part goes with the send,
        # ticked by default; unticked, the send is the written one alone, and its confirmation says so.
        sheets = dialog.locator("[data-gradescope-sheets]")
        expect(sheets).to_contain_text("80 to upload · 2 skipped · 5 in scans still waiting for scan checks")
        expect(sheets.get_by_label("Also upload 80 bubble sheets to the bubble-sheet exam, before the papers", exact=True)).to_be_checked()
        expect(dialog.get_by_label("I have checked the preview. Faculty uploads these papers and sets these grades on Gradescope, after uploading the bubble sheets; nothing is published.", exact=True)).to_be_visible()
        sheets.get_by_label("Also upload 80 bubble sheets to the bubble-sheet exam, before the papers", exact=True).uncheck()

        # The tick is required; a lost answer offers only Retry request, with the same request ID.
        send = dialog.get_by_role("button", name="Send to Gradescope", exact=True)
        send.click()
        expect(dialog.locator(".form-error")).to_contain_text("Tick the box")
        dialog.get_by_label("I have checked the preview. Faculty uploads these papers and sets these grades on Gradescope; nothing is published.", exact=True).check()
        send.click()
        retry = dialog.get_by_role("button", name="Retry request", exact=True)
        expect(retry).to_be_visible()
        expect(dialog.locator(".form-error")).to_contain_text("Faculty could not confirm whether this send was recorded.")
        expect(send).to_be_disabled()
        first = state["sent"][0]
        assert "sheets" not in first, first  # unticked: the written send alone
        assert first["confirmation"] == "send" and first["targets"]["s507"] == {"course_id": "1413802", "container_id": "8681400"}, first
        assert first["targets"]["s508"] == {"course_id": "1413802", "container_id": "8681400", "leave_out": True}, first
        page.screenshot(path=str(OUTPUT / "grading-gradescope-dialog.png"))
        # Closing and reopening keeps the unanswered request: nothing new is sent without Retry request.
        dialog.get_by_role("button", name="Close", exact=True).click()
        panel.get_by_role("button", name="Send to Gradescope…", exact=True).click()
        expect(retry).to_be_visible()
        expect(send).to_be_disabled()
        retry.click()
        expect(dialog).not_to_be_visible()
        assert len(state["sent"]) == 2 and state["sent"][1] == first, state["sent"]
        expect(page.locator("#toast")).to_contain_text("Send approved.")
        expect(panel.locator('[data-gradescope-send="send-2"]')).to_contain_text("Sending")
        expect(panel.locator('[data-gradescope-send="send-2"]')).to_contain_text("10 of 40 papers finished")
        expect(panel.locator('[data-gradescope-send="send-2"]')).to_contain_text("Waiting to try again")
        # A running send is read again every few seconds while the page is open; the folds the instructor opened stay open through
        # each redraw (certify-3: a poll closed them).
        rules, links = panel.locator('[data-gradescope-fold="rules"]'), panel.locator('[data-gradescope-fold="links"]')
        rules.locator(":scope > summary").click()
        links.locator(":scope > summary").click()
        expect(rules).to_have_attribute("open", "")
        expect(links).to_have_attribute("open", "")
        SENDS[0]["counts"]["uploads"] = {"accepted": 25, "uncertain": 1, "pending": 14}
        expect(panel.locator('[data-gradescope-send="send-2"]')).to_contain_text("25 of 40 papers finished", timeout=12000)
        expect(panel.locator('[data-gradescope-send="send-2"] [data-gradescope-state="uncertain"]')).to_contain_text("1 checking")
        expect(rules).to_have_attribute("open", "")
        expect(links).to_have_attribute("open", "")
        # QUIET REFRESH (FRONTEND.md): a poll that reads the same send changes nothing in the panel, a problems list the instructor
        # opened included (its table keeps its scroll and selection); the buttons keep working across a poll (delegated).
        live_send = panel.locator('[data-gradescope-send="send-2"]')
        live_send.get_by_text(re.compile(r"^Show problems · "), exact=False).click()
        expect(live_send.locator("[data-gradescope-problem-list] table")).to_contain_text("Student <Three>")
        panel.evaluate("""node => { node.querySelector('[data-gradescope-problem-list] table').dataset.probe = "kept"; window.__panelChanges = 0;
            window.__panelLog = []; window.__panelObserver = new MutationObserver(list => { window.__panelChanges += list.length; list.forEach(r => window.__panelLog.push(r.type + ' ' + (r.attributeName || '') + ' ' + (r.target.outerHTML || r.target.nodeValue || '').slice(0, 120))); });
            window.__panelObserver.observe(node, {childList: true, subtree: true, attributes: true, characterData: true}); }""")
        seen = state["reads"]
        for _ in range(150):
            if state["reads"] >= seen + 2:
                break
            page.wait_for_timeout(200)
        assert state["reads"] >= seen + 2, (seen, state["reads"])
        page.wait_for_timeout(500)
        assert page.evaluate("window.__panelChanges") == 0, page.evaluate("window.__panelLog")
        expect(live_send.locator("[data-gradescope-problem-list] table")).to_have_attribute("data-probe", "kept")
        page.evaluate("window.__panelObserver.disconnect()")
        expect(rules).to_have_attribute("open", "")

        # A changed preview (409) is read again under a new request ID and needs another tick and click.
        state["send_conflict"] = 1
        SENDS[0]["status"] = "complete"  # let the next send through
        panel.get_by_role("button", name="Send to Gradescope…", exact=True).click()
        dialog.get_by_label("I have checked the preview. Faculty uploads these papers and sets these grades on Gradescope, after uploading the bubble sheets; nothing is published.", exact=True).check()
        previews = len(state["previews"])
        send.click()
        expect(dialog).to_contain_text("Something changed since the preview")
        expect(dialog).to_contain_text("Nothing was sent.")
        expect(dialog.locator('[name="understood"]')).not_to_be_checked()
        assert len(state["previews"]) == previews + 1
        dialog.locator('[name="understood"]').check()
        send.click()
        expect(dialog).not_to_be_visible()
        # Ticked by default, the send names the bubble-sheet preview it showed: its fingerprint and the exams it resolved.
        last = state["sent"][-1]
        assert last["sheets"]["fingerprint"] == sheet_preview({})["fingerprint"] and last["sheets"]["targets"] == {"s507": {"course_id": "1413801", "container_id": "401156"}}, last
        assert state["sent"][-1]["request_id"] != state["sent"][-2]["request_id"], state["sent"][-2:]
        expect(panel.locator("[data-gradescope-earlier] > summary")).to_have_text("Earlier sends · 2")

        # Cancel while running, after the instructor confirms.
        page.once("dialog", lambda prompt: prompt.accept())
        panel.locator('[data-gradescope-send="send-3"]').get_by_role("button", name="Cancel", exact=True).click()
        expect(panel.locator('[data-gradescope-send="send-3"]')).to_contain_text("Cancelled")
        assert state["cancelled"] == [BASE + "/sends/send-3/cancel"], state["cancelled"]
        expect(panel.locator('[data-gradescope-problems="send-3"]')).to_have_count(0)  # cancelled units are counted, not listed

        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            panel.scroll_into_view_if_needed()
            page.screenshot(path=str(OUTPUT / f"grading-gradescope-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        page.set_viewport_size({"width": 1280, "height": 800})
        panel.get_by_role("button", name="Send to Gradescope…", exact=True).click()
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("40 to upload")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(OUTPUT / "grading-gradescope-dialog-1280.png"))
        dialog.get_by_role("button", name="Close", exact=True).click()

        # Grades already published on Gradescope: a warning, then a popup over the send dialog; only its confirm button sends, and
        # the send names the exam it confirmed (the server's control). Cancel and Escape send nothing and keep the send dialog open.
        page.set_viewport_size({"width": 1500, "height": 1100})
        state["published"] = True
        SENDS[0]["status"] = "complete"
        panel.get_by_role("button", name="Send to Gradescope…", exact=True).click()
        expect(dialog.locator('[data-gradescope-published="s507"]')).to_contain_text("Grades on Test 1 are published on Gradescope")
        expect(dialog.locator("[data-gradescope-published-note]")).to_contain_text("Grades are already published on Gradescope for Section 507")
        dialog.locator('[name="understood"]').check()
        sent = len(state["sent"])
        popup = page.locator(".grading-gradescope-published-dialog")
        send.click()
        expect(popup).to_be_visible()
        expect(popup.get_by_role("heading", name="Send to a published assignment?", exact=True)).to_be_visible()
        expect(popup).to_contain_text("Section 507 → Test 1 (140-507 · MATH 140 Fall 2026): 40 papers and 504 grades")
        expect(popup.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
        page.screenshot(path=str(OUTPUT / "grading-gradescope-published.png"))
        popup.get_by_role("button", name="Cancel", exact=True).click()
        expect(popup).to_have_count(0)
        expect(dialog).to_be_visible()
        send.click()
        expect(popup).to_be_visible()
        page.keyboard.press("Escape")
        expect(popup).to_have_count(0)
        expect(dialog).to_be_visible()
        assert len(state["sent"]) == sent, state["sent"][sent:]
        send.click()
        popup.get_by_role("button", name="Send to the published assignment", exact=True).click()
        expect(dialog).not_to_be_visible()
        assert len(state["sent"]) == sent + 1 and state["sent"][-1]["published"] == ["8681208"], state["sent"][-1]
        state["published"] = False
        expect(panel.locator('[data-gradescope-send="send-4"]')).to_contain_text("Sending")
        SENDS[0]["status"] = "complete"

        # Replacing grades someone else made on Gradescope (requirement, 2026-10-01): off in a new
        # dialog; ticking it reads the preview again, which lists each grade it would replace and who made it; Send asks once more in
        # its own popup (Cancel sends nothing), and the send names how many it replaces (the server's control).
        panel.get_by_role("button", name="Send to Gradescope…", exact=True).click()
        choice = dialog.get_by_role("checkbox", name="Replace grades someone else made on Gradescope", exact=True)
        expect(choice).not_to_be_checked()
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("504 to send")
        expect(dialog.locator("[data-gradescope-replacing]")).to_have_count(0)
        assert "replace_others" not in state["previews"][-1], state["previews"][-1]
        choice.check()
        replacing = dialog.locator("[data-gradescope-replacing]")
        expect(replacing).to_contain_text("Grades this send replaces · 10")
        expect(replacing).to_contain_text("Student <Three>")
        expect(replacing).to_contain_text("Ada <Fair>")
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("504 to send")
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("10 of them replace a grade someone else made on Gradescope")
        expect(dialog.locator("[data-gradescope-replacing-note]")).to_contain_text("This send replaces 10 grades someone else made on Gradescope")
        assert state["previews"][-1].get("replace_others") is True, state["previews"][-1]
        dialog.locator('[name="understood"]').check()
        sent = len(state["sent"])
        popup = page.locator(".grading-gradescope-published-dialog")
        send.click()
        expect(popup.get_by_role("heading", name="Replace 10 grades on Gradescope?", exact=True)).to_be_visible()
        expect(popup).to_contain_text("Ada <Fair>: 10 grades")
        expect(popup).to_contain_text("9 scores change; the other 1 grade keeps its score")
        expect(popup.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
        page.screenshot(path=str(OUTPUT / "grading-gradescope-replacing.png"))
        popup.get_by_role("button", name="Cancel", exact=True).click()
        expect(popup).to_have_count(0)
        expect(dialog).to_be_visible()
        assert len(state["sent"]) == sent, state["sent"][sent:]
        send.click()
        popup.get_by_role("button", name="Replace 10 grades", exact=True).click()
        expect(dialog).not_to_be_visible()
        body = state["sent"][-1]
        assert len(state["sent"]) == sent + 1 and body["replace_others"] is True and body["replacing"] == 10 and "papers_only" not in body, body
        assert body["replacements"] == REPLACEMENTS, body  # the replacements as the preview showed them: the send does not read them again
        expect(panel.locator(f'[data-gradescope-send="send-{len(SENDS)}"]')).to_contain_text("504 grades to set, 10 grades someone else made on Gradescope replaced")
        SENDS[0]["status"] = "complete"
        panel.get_by_role("button", name="Send to Gradescope…", exact=True).click()  # a new dialog starts with the choice off again
        expect(dialog.get_by_role("checkbox", name="Replace grades someone else made on Gradescope", exact=True)).not_to_be_checked()
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("504 to send")
        dialog.get_by_role("button", name="Close", exact=True).click()

        # Creating the assignment on Gradescope (requirement, 2026-09-27; any assignment, not only exams): offered while a section has
        # none linked, previewed, ticked, then followed on the panel; a course where Faculty stopped says why and offers Retry.
        expect(panel.get_by_role("button", name="Create the assignment on Gradescope…", exact=True)).to_have_count(0)  # every section linked
        state["unlinked"] = True
        page.reload()
        create = panel.get_by_role("button", name="Create the assignment on Gradescope…", exact=True)
        expect(create).to_be_visible()
        expect(create).not_to_have_class(re.compile(r"\bprimary\b"))
        create.click()
        dialog = page.locator("#dialog")
        versions = dialog.locator("[data-gradescope-create-versions]")
        expect(dialog.get_by_label("Name on Gradescope", exact=True)).to_have_value("Exam 1")  # the assignment's own title, to start with
        expect(versions).to_contain_text("Exam 1 Version A")
        expect(versions).to_contain_text("12 pages (its blank) · 8 questions with their answer boxes · 57 rubric lines")
        expect(dialog).to_contain_text("The versions are grouped as one assignment named “Exam 1”")
        expect(dialog.locator("[data-gradescope-create-courses]")).to_contain_text("140-509 · MATH 140 Fall 2026")
        expect(dialog).to_contain_text("Sections not included · 1")
        make = dialog.get_by_role("button", name="Create on Gradescope", exact=True)
        make.click()
        expect(dialog.locator(".form-error")).to_contain_text("Tick the box")
        name = dialog.get_by_label("Name on Gradescope", exact=True)
        name.fill("Quiz <9>")
        name.press("Tab")
        expect(versions).to_contain_text("Quiz <9> Version A")  # escaped, shown as typed
        assert state["create_previews"][-1]["title"] == "Quiz <9>", state["create_previews"][-1]
        dialog.locator('[name="understood"]').check()
        page.screenshot(path=str(OUTPUT / "grading-gradescope-create.png"))
        make.click()
        expect(dialog).not_to_be_visible()
        expect(page.locator("#toast")).to_contain_text("Faculty is making the assignment on Gradescope.")
        made = state["created"][-1]
        assert made["confirmation"] == "create" and made["title"] == "Quiz <9>" and made["fingerprint"], made
        card = panel.locator('[data-gradescope-creation="c1"]')
        expect(card).to_contain_text("Making it")
        expect(card).to_contain_text("working · Rubric of Question 20 (Quiz <9> Version A)")
        expect(create).to_have_count(0)  # not offered again while it runs
        CREATIONS[0]["units"][0].update(status="failed", error="The outline of Quiz <9> Version A on Gradescope is not the one Faculty made (was it edited there?).")
        expect(card).to_contain_text("Stopped in 1 course", timeout=12000)
        expect(card).to_contain_text("The outline of Quiz <9> Version A on Gradescope is not the one Faculty made")
        card.get_by_role("button", name="Retry (1)", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Trying again where it stopped.")
        assert state["create_retried"] == [BASE + "/creations/c1/retry"], state["create_retried"]
        page.set_viewport_size({"width": 1280, "height": 900})
        card.scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(OUTPUT / "grading-gradescope-create-card-1280.png"), full_page=True)
        page.set_viewport_size({"width": 1500, "height": 1100})
        # The send dialog offers it too, with its own choices, when a chosen course has no assignment.
        panel.get_by_role("button", name="Send to Gradescope…", exact=True).click()
        dialog.get_by_label("Gradescope course for section 508", exact=True).select_option("1413803")
        offer = dialog.locator("[data-gradescope-create-offer]")
        expect(offer).to_contain_text("Section 508's Gradescope course has no assignment for this yet?")
        offer.get_by_role("button", name="Create the assignment on Gradescope…", exact=True).click()
        expect(dialog.locator("[data-gradescope-create-versions]")).to_be_visible()
        assert state["create_previews"][-1]["targets"]["s508"] == {"course_id": "1413803", "container_id": ""}, state["create_previews"][-1]
        dialog.get_by_role("button", name="Close", exact=True).click()
        CREATIONS[0].update(status="complete")
        CREATIONS[0]["units"][0].update(status="complete", step="Made Quiz <9>", error="")
        state["unlinked"] = False
        # A section whose papers are in a version its Gradescope set lacks (2026-10-06: a makeup's Version E): the warning stays
        # on that section only, and the offer adds the version to the set it already goes to.
        state["lacking"] = True
        panel.get_by_role("button", name="Send to Gradescope…", exact=True).click()
        offer = dialog.locator("[data-gradescope-create-offer]")
        expect(offer).to_contain_text("Section 508 has papers in Version C, which “Test 1” lacks. Faculty can add it there, with its questions and rubric.")
        expect(dialog.get_by_text("Test 1 has no version C.", exact=True)).to_have_count(1)
        page.screenshot(path=str(OUTPUT / "grading-gradescope-add-version-offer.png"))
        offer.get_by_role("button", name="Add Version C on Gradescope…", exact=True).click()
        expect(dialog.locator("[data-gradescope-create-courses]")).to_contain_text("Section 508 · adds Version C to “Test 1” (it has A, B)")
        expect(dialog.get_by_text("I checked this. Faculty adds Version C to “Test 1” in 1 Gradescope course; nothing is published and no student is emailed.", exact=True)).to_be_visible()
        assert state["create_previews"][-1]["targets"]["s508"] == {"course_id": "1413802", "container_id": "8681400"}, state["create_previews"][-1]
        dialog.get_by_role("button", name="Close", exact=True).click()
        state["lacking"] = False

        # Papers without grades (requirement, 2026-09-28). "Upload papers only…" opens the send dialog on that choice; changing it reads
        # the preview again, and the send names the papers-only preview it was shown.
        SENDS[0]["status"] = "complete"
        papers = panel.get_by_role("button", name="Upload papers only…", exact=True)
        expect(papers).to_be_visible()
        expect(papers).not_to_have_class(re.compile(r"\bprimary\b"))
        papers.click()
        dialog = page.locator("#dialog")
        mode = dialog.locator("[data-gradescope-mode]")
        expect(mode.get_by_role("radio", name=re.compile("^Papers only"))).to_be_checked()
        expect(dialog.get_by_role("checkbox", name="Replace grades someone else made on Gradescope", exact=True)).to_be_disabled()  # no grade, nothing replaced
        expect(dialog.locator("[data-gradescope-papers-only]")).to_contain_text("0 sent · papers only")
        expect(dialog.locator("[data-gradescope-papers-only]")).to_contain_text("566 final grades stay in Faculty until you send papers and grades.")
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("40 to upload")
        expect(dialog.get_by_text("Per question", exact=True)).to_have_count(0)
        upload = dialog.get_by_role("button", name="Upload papers", exact=True)
        expect(upload).to_be_visible()
        assert state["previews"][-1].get("papers_only") is True, state["previews"][-1]
        page.screenshot(path=str(OUTPUT / "grading-gradescope-papers.png"))
        mode.get_by_role("radio", name=re.compile("^Papers and final grades")).check()
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("504 to send")
        expect(dialog.get_by_role("button", name="Send to Gradescope", exact=True)).to_be_visible()
        assert "papers_only" not in state["previews"][-1], state["previews"][-1]
        mode.get_by_role("radio", name=re.compile("^Papers only")).check()
        expect(dialog.locator("[data-gradescope-papers-only]")).to_be_visible()
        shown = preview(state["previews"][-1]["targets"], True)["fingerprint"]
        dialog.get_by_label("I have checked the preview. Faculty uploads these papers to Gradescope and sets no grade, after uploading the bubble sheets; nothing is published.", exact=True).check()
        sent = len(state["sent"])
        upload.click()
        expect(dialog).not_to_be_visible()
        assert len(state["sent"]) == sent + 1 and state["sent"][-1]["papers_only"] is True and state["sent"][-1]["fingerprint"] == shown, state["sent"][-1]
        expect(page.locator("#toast")).to_contain_text("Upload approved. Faculty uploads the papers in the background and sends no grades")
        expect(panel.locator(f'[data-gradescope-send="send-{len(SENDS)}"]')).to_contain_text("40 papers to upload, papers only (no grades)")
        SENDS[0]["status"] = "complete"
        page.set_viewport_size({"width": 1280, "height": 800})
        papers.click()
        expect(dialog.locator("[data-gradescope-papers-only]")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(OUTPUT / "grading-gradescope-papers-1280.png"))
        dialog.get_by_role("button", name="Close", exact=True).click()
        page.set_viewport_size({"width": 1500, "height": 1100})

        # Bubble sheets, upload only, from the Bubble sheets page: the panel lives below the key and survives the key's redraws.
        page.set_viewport_size({"width": 1500, "height": 1100})
        page.goto(f"{address}/#grading/{plan_id}?tab=bubble")
        sheets = page.locator('[data-gradescope-kind="sheets"]')
        expect(sheets).to_contain_text("Send bubble sheets to Gradescope")
        expect(sheets).to_contain_text("Upload only: Gradescope reads the bubbles and scores them with its own answer key")
        expect(sheets.locator("[data-gradescope-coverage]")).to_contain_text("0 of 80 matched sheets are on Gradescope")
        page.locator(".bubble-key-letter").first.click()  # the key editor draws itself again
        expect(page.locator(".bubble-key-state")).to_contain_text("Unsaved changes")
        expect(sheets).to_have_count(1)
        sheets.get_by_role("button", name="Send bubble sheets to Gradescope…", exact=True).click()
        dialog = page.locator("#dialog")
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("80 to upload")
        expect(dialog.locator("[data-gradescope-summary]")).to_contain_text("5 more in scans still waiting for scan checks (not sent)")
        expect(dialog.locator("[data-gradescope-summary]")).not_to_contain_text("grade")
        dialog.get_by_text("Skipped sheets · 2", exact=True).click()
        expect(dialog.locator("[data-gradescope-skipped]")).to_contain_text("Sheet on scan page 12")
        expect(dialog.locator("[data-gradescope-skipped]")).to_contain_text("Student <Five>")
        expect(dialog.get_by_label("Gradescope assignment for section 507", exact=True)).to_have_value("401156")
        expect(dialog).to_contain_text("Proposed by Faculty from the course name and the exam’s title.")
        dialog.get_by_label("I have checked the preview. Faculty uploads these bubble sheets to Gradescope; Gradescope scores them, and nothing is published.", exact=True).check()
        dialog.get_by_role("button", name="Send bubble sheets to Gradescope", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.locator("#toast")).to_contain_text("Send approved. Faculty uploads the sheets in the background")
        assert len(state["sheet_sent"]) == 1 and state["sheet_sent"][0]["confirmation"] == "send", state["sheet_sent"]
        assert state["sheet_sent"][0]["targets"]["s507"] == {"course_id": "1413801", "container_id": "401156"}, state["sheet_sent"]
        expect(sheets.locator('[data-gradescope-send="sheets-1"]')).to_contain_text("20 of 80 sheets finished")
        expect(sheets.locator('[data-gradescope-send="sheets-1"]')).to_contain_text("Approved: 80 sheets to upload")
        sheets.scroll_into_view_if_needed()
        page.screenshot(path=str(OUTPUT / "grading-gradescope-sheets.png"), full_page=True)

        assert not errors, errors
        # The stubbed 409 was the point; the aborted request never reached a server. Anything else is a failure.
        assert [status for status, _ in bad] == [409], bad
        browser.close()
        print("Gradescope panel passed: not set up, preview, forbidden course hidden, lost answer retried with the same request, 409 re-preview, cancel, retry, problems, published-exam confirmation, replacing others' grades with its confirmation, creating the assignment on Gradescope, papers only, and 1500/1280 px desktop widths.")
finally:
    server.close()
    fixture.doCleanups()
