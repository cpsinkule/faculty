"""Certification and Export, the sixth grading step (AGENTS.md "Assisted FRQ grading", requirement, 2026-09-26).

Part one runs against the synthetic FRQ fixture app with the certification read, its UIN search and its preview answered by browser
route stubs shaped like the server's view (GRADING.md, spec §3.1/§3.4/§3.5), with synthetic names: the page itself, how it leads
with the analysis, keeps UIN digits out of the address, the markup and the status line, and keeps the files and Gradescope as
secondary sections.

Part two runs against the REAL certification service on the graded Gradescope workspace of tests/test_grading_certification.py
(two graded students, a third who joined without a paper, a fake Gradescope; synthetic names and UINs): the question drill-down, the
student search and drawer, the review desk (approved and manual only), the Certify dialog with the zero, the Gradescope "do not send"
and the gradebook column confirmed, an idempotent retry after a lost answer, the gradebook values it recorded, a change after
certifying reopening the step, the gradebook's pull text, the docked drawer's width and CSP. Desktop only, one appearance
(DESIGN-LANGUAGE, requirement, 2026-10-03: no dark mode, no phone layout)."""
import json
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
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
DIGITS = "40771"


def stats(n, mean, bins=(0, 0, 0, 0, 1, 1, 2, 4, 2, 1)):
    return {"n": n, "mean": mean, "median": mean, "sd": 7.5, "min": 41, "max": 98,
            "histogram": [{"from": i * 10, "to": i * 10 + 10, "count": c} for i, c in enumerate(bins)], "excluded": {"zero": 1, "missing": 0, "left_out": 0, "excused": 0}}


def student(row, name, section="507", **extra):
    base = {"row": row, "name": name, "section": section, "exam_version": "A", "participant_version": 1, "expectation": "expected", "collection": "complete",
            "status": "complete", "reason": "", "frq": {"state": "complete", "earned": "8", "possible": "10", "final": 1, "expected": 1},
            "bubble": {"state": "none"}, "total": {"earned": "8", "possible": "10", "pct": 80}, "scores": ["8"], "flagged": 0, "zero_questions": 0, "dr": [],
            "gradescope": {"paper": True, "grades": {"sent": 1, "changed": 0, "not_sent": 0}, "sheet": None}, "certified": None, "changed_since": False}
    base.update(extra)
    return base


STUDENTS = [student(f"s507:{i:010d}", f"Student {chr(65 + i)}{'<x>' if i == 3 else ''}", "507" if i % 2 else "508") for i in range(1, 25)]
STUDENTS.append(student("s507:9999999999", "Zola, Émile", status="missing", reason="No paper scanned", frq={"state": "none"}, total=None, scores=[None],
                        gradescope={"paper": False, "grades": {"sent": 0, "changed": 0, "not_sent": 0}, "sheet": None}))
VIEW = {"plan_id": plan_id, "title": "Synthetic exam", "parts": ["frq"], "possible": {"frq": "10", "total": "10"}, "versions": [],
        "sections": [{"section_id": "s507", "number": "507"}, {"section_id": "s508", "number": "508"}],
        "certification": {"state": "none", "id": None, "created_at": None, "changed": [], "gradebook": None},
        # An earlier certification's row: its seven-column table sits in the Details, which must not widen a phone's page.
        "history": [{"id": "c0", "created_at": "2026-09-20T15:00:00+00:00", "supersedes_id": None, "rule": "certify-v1", "students": 25, "scored": 24, "zero": 1,
                     "excused": 0, "left_out": 0, "left_out_dr": 0, "gradebook": {"item_id": "i0", "title": "Synthetic exam (an earlier column)", "written": 25, "kept": 0}}],
        "stats": {"total": stats(24, 7.9), "frq": stats(24, 7.9), "by_version": {}, "by_section": {"507": stats(12, 7.7), "508": stats(12, 8.1)}},
        "questions": [{"question_id": q["question_id"], "title": q["content"]["title"], "printed_number": "1", "version": "", "max": "10", "n_final": 24, "not_final": 0,
                       "mean": 7.9, "median": 8, "sd": 1.2, "pct_full": 25, "pct_zero": 4, "automatic": 20, "instructor": 4, "flagged": 3, "overridden": 1,
                       "gradescope": {"sent": 24, "changed": 0, "not_sent": 0, "unsendable": None}} for q in fixture.plan.get("questions", [])] or [
                      {"question_id": "q1", "title": "Question 1", "printed_number": "1", "version": "", "max": "10", "n_final": 24, "not_final": 0, "mean": 7.9, "median": 8,
                       "sd": 1.2, "pct_full": 25, "pct_zero": 4, "automatic": 20, "instructor": 4, "flagged": 3, "overridden": 1, "gradescope": {"sent": 24, "changed": 0, "not_sent": 0, "unsendable": None}}],
        "bubble_questions": {}, "students": STUDENTS,
        "counts": {"participants": 25, "complete": 24, "missing": 1, "flagged": 3, "zero_somewhere": 0, "changed_since": 0, "dr": 0, "excused": 0, "not_on_gradescope": 0},
        "participation": {"enabled": False, "bubble_key": False},
        "checks": [{"id": "zero_no_work", "level": "confirm", "title": "Students with no work receive 0", "detail": "One student has no paper.", "count": 1, "digest": "d",
                    "rows": [{"row": "s507:9999999999", "name": "Zola, Émile", "section": "507", "detail": "No paper scanned"}], "provisional": True},
                   {"id": "automatic_unchecked", "level": "info", "title": "Automatic grades nobody looked at", "detail": "Spot-check a few.", "count": 20, "provisional": True}],
        "blocking": 0, "to_confirm": 1, "gradescope": {"configured": False, "written": {}, "sheets": {}, "live": False}, "live": False}
state = {"searches": [], "previews": 0, "reads": 0, "live": False, "view_extra": {}}
HIDE = """() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
  document.dispatchEvent(new Event('visibilitychange')); }"""
SHOW = """() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
  document.dispatchEvent(new Event('visibilitychange')); }"""


def certification(route):
    request, path = route.request, route.request.url.split(address, 1)[-1].split("?")[0]
    if path == BASE and request.method == "GET":
        state["reads"] += 1
        route.fulfill(json={**VIEW, "live": state["live"], **state["view_extra"]})
    elif path == BASE + "/students/search" and request.method == "POST":
        body = json.loads(request.post_data or "{}")
        state["searches"].append(body)
        route.fulfill(json={"matches": [{"row": STUDENTS[4]["row"], "name": STUDENTS[4]["name"], "section": STUDENTS[4]["section"], "similarity": 0.92}]})
    elif path == BASE + "/preview" and request.method == "POST":
        state["previews"] += 1
        route.fulfill(json={"fingerprint": "f", "rule": "certify-v1", "parts": ["frq"], "snapshot": {"students": 25, "scored": 24, "zero": 1, "excused": 0, "left_out": 0},
                            "checks": [], "blocking": 0, "confirm": [{"id": "zero_no_work", "digest": "d", "title": "Students with no work receive 0", "count": 1,
                                                                    "rows": [{"row": "s507:9999999999", "name": "Zola, Émile", "section": "507", "detail": "No paper scanned"}], "requires": "tick"}],
                            "gradescope": {}, "gradebook": {}, "changes": []})
    else:
        route.fallback()


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad, urls = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 and not response.url.endswith("/templates/prepare") else None)
        page.on("request", lambda request: urls.append(request.url))
        page.route(re.compile(re.escape(address + BASE) + r"(/.*)?(\?.*)?$"), certification)

        page.goto(f"{address}/#grading/{plan_id}?tab=export")
        root = page.locator(".grading-certification")
        # The step is named for its tab (canonical words, 2026-10-18); the graph's sixth node says so too.
        expect(root.locator(".grading-step-header h3")).to_have_text("Certify")
        expect(page.locator('.grading-workflow [data-pipeline-step="export"] strong')).to_have_text("Certify")
        expect(root.locator("[data-step-lead]")).to_have_text("24 of 25 students have every grade. Check them here, then certify.")
        # ONE primary, the search beside it, and a one-line band; the checks wait in its Details.
        expect(root.locator("[data-cert-main] .button.primary")).to_have_count(1)
        expect(root.locator("[data-cert-main] .button.primary")).to_have_text("Certify grades…")
        expect(root.locator("[data-cert-band] > .callout")).to_contain_text("Certifying records 0 for 1 student")
        expect(root.locator("[data-cert-band] > .callout")).to_contain_text("1 student has no grade: you confirm each 0 when you certify.")
        expect(root.locator("[data-cert-band] > .callout")).to_have_attribute("data-tone", "warning")
        details = root.locator("#cert-fold-details")
        expect(details.locator('[data-cert-checks="confirm"]')).to_be_hidden()
        details.locator(":scope > summary").click()
        expect(details.locator('[data-cert-checks="confirm"]')).to_contain_text("Students with no work receive 0 (1)")
        expect(details).to_contain_text("Earlier certifications")
        expect(details.locator('[data-cert-checks="info"]')).to_contain_text("Automatic grades nobody looked at (20)")
        # GRADED FOR PARTICIPATION (2026-11-01): the setting under the band, saved through its own route and read back.
        saved = []

        def taking_part(route):
            on = json.loads(route.request.post_data)["enabled"]
            saved.append(on)
            state["view_extra"] = {"participation": {"enabled": on, "bubble_key": False}}
            route.fulfill(json={"enabled": on, "bubble_key": False})
        page.route(f"**/api/grading/assignments/{plan_id}/participation", taking_part)
        box = root.locator("[data-cert-participation]")
        expect(box).not_to_be_checked()
        box.check()
        expect(page.locator("#toast")).to_contain_text("everyone with a paper now gets full credit")
        expect(root.locator("[data-cert-participation]")).to_be_checked()
        root.locator("[data-cert-participation]").uncheck()
        expect(root.locator("[data-cert-participation]")).not_to_be_checked()
        assert saved == [True, False], saved
        page.unroute(f"**/api/grading/assignments/{plan_id}/participation")
        state["view_extra"] = {}
        # Analysis first: cards and the distribution, then the Questions | Students panel, then Gradescope and the files.
        order = page.evaluate("""() => ['[data-cert-band]', '.certification-cards', '.certification-distribution', '#cert-panel', '#cert-fold-gradescope', '[data-export-downloads]', '[data-training-panel]']
            .map(s => document.querySelector(s)).map((node, i, all) => i === 0 || Boolean(all[i - 1].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING))""")
        assert all(order), order
        expect(root.locator('[data-cert-card="complete"]')).to_contain_text("24 / 25")
        expect(root.locator('[data-cert-card="mean"]')).to_contain_text("7.9 / 10")
        expect(root.locator("[data-cert-histogram] rect")).to_have_count(10)
        expect(root.locator(".certification-facts")).to_contain_text("Median")
        # No painting beside the summary since 2026-10-04 (instructor: "remove paintings"); the distribution follows the summary.
        expect(root.locator(".painting, .certification-painting")).to_have_count(0)
        below = page.evaluate("""() => { const box = s => document.querySelector(s).getBoundingClientRect();
            return box('.certification-distribution').top >= box('.certification-cards').bottom; }""")
        assert below, "the distribution follows the counts"
        root.locator('[data-filter-group="cert-dist"] [data-filter="section:508"]').click()
        expect(root.locator(".certification-facts")).to_contain_text("8.1")
        # Questions, sortable; the panel keeps what was chosen through a redraw.
        panel = root.locator("#cert-panel")
        expect(panel.locator("tbody tr")).to_have_count(len(VIEW["questions"]))
        panel.locator('[data-cert-sort="mean"]').click()
        expect(panel.locator('th[aria-sort="descending"]')).to_contain_text("Mean")
        expect(panel.locator('[data-cert-sort="mean"]')).to_be_focused()
        # A card opens its filter: Missing a grade shows the one student without one.
        root.locator('[data-cert-card="missing"]').click()
        expect(panel.locator('[data-filter-group="cert-panel"] [aria-pressed="true"]')).to_contain_text("Students")
        expect(panel.locator('[data-filter-group="cert-students"] [aria-pressed="true"]')).to_contain_text("Missing a grade")
        expect(panel.locator("tbody tr")).to_have_count(1)
        expect(panel.locator("tbody tr")).to_contain_text("Zola, Émile")
        expect(panel.locator("tbody tr")).to_contain_text("No paper scanned")
        panel.locator('[data-filter-group="cert-students"] [data-filter="all"]').click()
        expect(panel.locator("tbody tr")).to_have_count(25)
        # Names are escaped where they are shown.
        expect(panel.locator("tbody")).to_contain_text("Student D<x>")
        # '/' focuses the search (the shell's rule for a page's [data-page-search]); words filter names in any order, accents ignored.
        page.locator("body").click(position={"x": 5, "y": 5})
        page.keyboard.press("/")
        search = root.locator("[data-cert-search]")
        expect(search).to_be_focused()
        search.fill("emile zo")
        expect(panel.locator("tbody tr")).to_have_count(1)
        expect(root.locator("[data-cert-search-status]")).to_have_text("1 students match")
        # Four or more digits go to the server in a POST body and come back as names: never in the address, the markup or a status.
        search.fill(DIGITS)
        expect(panel.locator("tbody tr")).to_have_count(1)
        expect(panel.locator("tbody tr[data-cert-match]")).to_contain_text(STUDENTS[4]["name"])
        assert state["searches"][-1] == {"digits": DIGITS}, state["searches"]
        assert not any(DIGITS in url for url in urls), [u for u in urls if DIGITS in u]
        assert DIGITS not in page.url
        assert DIGITS not in page.content()
        expect(root.locator("[data-cert-search-status]")).to_have_text("1 students match")
        search.fill("")
        expect(panel.locator("tbody tr")).to_have_count(25)
        # FRONTEND: Show all invalidates an in-flight UIN search, so its late answer cannot filter the restored table.
        held_search = []
        page.route("**/certification/students/search", lambda route: held_search.append(route))
        search.fill("nobody matches this name")
        expect(panel.locator("[data-cert-show-all]")).to_be_visible()
        with page.expect_request(lambda request: request.url.endswith("/certification/students/search")):
            search.fill(DIGITS)
        panel.locator("[data-cert-show-all]").click()
        expect(panel.locator("tbody tr")).to_have_count(25)
        assert len(held_search) == 1
        held_search[0].fulfill(json={"matches": [{"row": STUDENTS[4]["row"]}]})
        expect(root.locator("[data-cert-search-status]")).to_have_text("")
        expect(panel.locator("tbody tr")).to_have_count(25)
        page.unroute("**/certification/students/search")
        # Scrolled, a row's name leaves with its row: only the heading row sticks (the names piled up under it, 2026-10-01).
        moved = page.evaluate("""() => { const box = document.querySelector('#cert-panel .data-table'); box.scrollTop = 400;
            const row = box.querySelector('tbody tr'), name = row.querySelector('th');
            const offset = Math.round(name.getBoundingClientRect().top - row.getBoundingClientRect().top); box.scrollTop = 0;
            return {scrolled: box.scrollHeight > box.clientHeight, offset}; }""")
        assert moved == {"scrolled": True, "offset": 0}, moved
        # The files and Gradescope stay, below the analysis.
        expect(page.locator("[data-export-downloads]").get_by_role("button", name="Scores per question", exact=True)).to_be_visible()
        expect(page.locator("#cert-fold-gradescope > summary")).to_contain_text("Gradescope")
        expect(page.locator("[data-export-gradebook]")).to_have_count(0)
        # The Gradescope panels read nothing while their fold is closed (perf-2); opening it reads the status once.
        gradescope_status = f"/api/grading/gradescope/assignments/{plan_id}"
        assert not any("/api/grading/gradescope/" in url for url in urls), [url for url in urls if "/api/grading/gradescope/" in url]
        fold = page.locator("#cert-fold-gradescope")
        assert fold.get_attribute("open") is None
        with page.expect_response(lambda response: response.url.split("?")[0].endswith(gradescope_status)):
            fold.locator(":scope > summary").click()
        expect(page.locator(".grading-gradescope")).to_contain_text("Gradescope is not set up")
        expect(page.locator(".grading-gradescope-publish")).to_contain_text("Publishing on Gradescope")
        assert len([url for url in urls if url.split("?")[0].endswith(gradescope_status)]) == 1, urls
        fold.locator(":scope > summary").click()
        # Certify grades… opens the preview (which writes nothing) and says who receives 0.
        root.locator("[data-cert-certify]").click()
        dialog = page.locator("#dialog")
        expect(dialog.locator("[data-certify-summary]")).to_contain_text("25 students: 24 scored, 1 receive 0")
        expect(dialog.locator('[data-certify-confirm="zero_no_work"]')).to_contain_text("Zola, Émile")
        assert state["previews"] == 1
        dialog.get_by_role("button", name="Close", exact=True).first.click()
        # CSP: no inline style attribute anywhere in the page's markup.
        assert page.evaluate("document.querySelectorAll('[data-cert-main] [style]').length") == 0
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"certification-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        page.set_viewport_size({"width": 1500, "height": 1100})
        # While something is under way the page reads itself every 20 s; a hidden tab reads nothing, and shown again it reads at
        # once and goes on every 20 s, one read at a time.
        state["live"] = True
        live = browser.new_page(viewport={"width": 1500, "height": 1100})
        live.set_default_timeout(15000)
        live.on("pageerror", lambda error: errors.append(str(error)))
        live.route(re.compile(re.escape(address + BASE) + r"(/.*)?(\?.*)?$"), certification)
        instant = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
        live.clock.install(time=instant)
        live.clock.pause_at(instant)
        live.goto(f"{address}/#grading/{plan_id}?tab=export")
        expect(live.locator(".grading-certification [data-step-lead]")).to_be_visible()
        state["reads"] = 0
        with live.expect_response(lambda response: response.url.endswith(BASE)):
            live.clock.fast_forward(21000)
        live.evaluate(HIDE)
        live.clock.fast_forward(21000)
        live.clock.fast_forward(60000)
        live.wait_for_timeout(500)
        assert state["reads"] == 1, state
        with live.expect_response(lambda response: response.url.endswith(BASE)):
            live.evaluate(SHOW)
        for _ in range(2):
            with live.expect_response(lambda response: response.url.endswith(BASE)):
                live.clock.fast_forward(21000)
        live.wait_for_timeout(500)
        assert state["reads"] == 4, state
        live.close()

        # The downloads follow a read (missed:certify[2]): with no final grade they wait; once one is final, the next live read enables
        # them without a reload.
        state["view_extra"] = {"questions": [{**q, "automatic": 0, "instructor": 0} for q in VIEW["questions"]]}
        later = browser.new_page(viewport={"width": 1500, "height": 1100})
        later.set_default_timeout(15000)
        later.on("pageerror", lambda error: errors.append(str(error)))
        later.route(re.compile(re.escape(address + BASE) + r"(/.*)?(\?.*)?$"), certification)
        later.clock.install(time=instant)
        later.clock.pause_at(instant)
        later.goto(f"{address}/#grading/{plan_id}?tab=export")
        downloads = later.locator("[data-export-downloads]")
        expect(downloads).to_contain_text("Nothing to download until a grade is final.")
        expect(downloads.get_by_role("button", name="Scores per question", exact=True)).to_be_disabled()
        later.evaluate("() => { window.__notReloaded = true; }")
        state["view_extra"] = {}
        with later.expect_response(lambda response: response.url.endswith(BASE)):
            later.clock.fast_forward(21000)
        expect(downloads.get_by_role("button", name="Scores per question", exact=True)).to_be_enabled()
        expect(downloads).not_to_contain_text("Nothing to download")
        assert later.evaluate("() => window.__notReloaded === true")
        later.close()

        # What Faculty is about to certify by itself is said in place of asking for a Certify (requirement, 2026-11-14; GRADING "MAKEUP
        # CHANGES CERTIFY THEMSELVES"): the lead names it, Certify again… stays but is no longer the primary, and afterwards the
        # header names whom Faculty gave 0.
        zola = {"row": "s507:9999999999", "name": "Zola, Émile", "section": "507"}
        changed = {"state": "changed", "id": "c1", "created_at": "2026-09-26T15:00:00+00:00", "changed": ["makeup_ended"], "gradebook": {"title": "Synthetic exam"}}
        state["view_extra"] = {"certification": changed, "automatic": {"enabled": True, "waiting": [], "latest": None,
                                                                         "next": {"papers": [], "zero": [zola], "left_out": [], "rest": False}}}
        ahead = browser.new_page(viewport={"width": 1440, "height": 900})
        ahead.set_default_timeout(15000)
        ahead.on("pageerror", lambda error: errors.append(str(error)))
        ahead.route(re.compile(re.escape(address + BASE) + r"(/.*)?(\?.*)?$"), certification)
        ahead.goto(f"{address}/#grading/{plan_id}?tab=export")
        expect(ahead.locator("[data-step-lead]")).to_have_text("Faculty does this by itself in a few minutes: gives Zola, Émile 0 (makeup ended).")
        again = ahead.get_by_role("button", name="Certify again…")
        expect(again).to_be_visible()
        assert "primary" not in (again.get_attribute("class") or ""), again.get_attribute("class")
        ahead.screenshot(path=str(OUTPUT / "certification-faculty-about-to.png"), full_page=False)
        state["view_extra"] = {"certification": {**changed, "state": "current", "changed": []},
                               "automatic": {"enabled": True, "waiting": [], "latest": {"at": "2026-09-27T15:00:00+00:00", "markers": "live", "students": [{**zola, "change": "zero"}]}}}
        ahead.reload()
        expect(ahead.locator("[data-step-lead]")).to_contain_text("Faculty gave Zola, Émile 0 after their makeup ended by itself")
        ahead.locator("#cert-fold-faculty summary").click()
        expect(ahead.locator("[data-cert-faculty-list]")).to_contain_text("Given 0: their makeup ended")
        ahead.screenshot(path=str(OUTPUT / "certification-faculty-gave-0.png"), full_page=False)
        state["view_extra"] = {}
        ahead.close()

        # A send that settles is followed without a reload (certify-2): the panel's poll sees it end, the page reads itself again, and
        # the On Gradescope card and the fold's hint say what is there now.
        state["live"] = False
        sent = {"status": "running"}

        def coverage(done):
            return {"papers": {"total": 24, "sent": 24 if done else 10}, "grades": {"final": 24, "sent": 24 if done else 10, "changed": 0, "not_sent": 0 if done else 14}}

        def following(route):
            path, done = route.request.url.split(address, 1)[-1].split("?")[0], sent["status"] == "complete"
            units = {"accepted": 24} if done else {"accepted": 10, "pending": 14}
            if path == gradescope_status and route.request.method == "GET":
                route.fulfill(json={"assignment_id": plan_id, "configured": True, "problem": "", "targets": [], "creations": [], "coverage": coverage(done),
                                    "sends": [{"id": "send-1", "status": sent["status"], "created_at": "2026-09-25T15:00:00+00:00", "updated_at": "2026-09-25T15:01:00+00:00",
                                               "plan": {"targets": [], "summary": {"papers": {"upload": 24}, "grades": {"send": 24}}},
                                               "counts": {"uploads": units, "grades": units}, "waiting": ""}]})
            elif path == gradescope_status + "/publications":
                route.fulfill(json={"assignment_id": plan_id, "configured": True, "problem": "", "publications": []})
            else:
                route.fulfill(status=404, json={"error": "unexpected " + path})

        def configured(done):
            written = coverage(done)
            return {"gradescope": {"configured": True, "live": not done, "sheets": None,
                                   "written": {"papers": written["papers"], "grades": written["grades"]}},
                    "counts": {**VIEW["counts"], "not_on_gradescope": 0 if done else 14}}
        state["view_extra"] = configured(False)
        follow = browser.new_page(viewport={"width": 1500, "height": 1100})
        follow.set_default_timeout(15000)
        follow.on("pageerror", lambda error: errors.append(str(error)))
        follow.route(re.compile(re.escape(address + BASE) + r"(/.*)?(\?.*)?$"), certification)
        follow.route("**/api/grading/gradescope/**", following)
        follow.goto(f"{address}/#grading/{plan_id}?tab=export")
        card, hint = follow.locator('[data-cert-card="gradescope"]'), follow.locator("#cert-fold-gradescope > summary .disclosure-hint")
        expect(card).to_contain_text("10 grades · 10 papers")
        expect(follow.locator('[data-gradescope-send="send-1"]')).to_contain_text("Sending")  # open by itself: grades are not there yet
        expect(hint).to_have_text("10 of 24 final grades there")
        reads = state["reads"]
        sent["status"], state["view_extra"] = "complete", configured(True)
        expect(card).to_contain_text("24 grades · 24 papers", timeout=15000)
        expect(hint).to_have_text("24 of 24 final grades there")
        expect(follow.locator('[data-gradescope-send="send-1"]')).to_contain_text("Finished")
        assert state["reads"] == reads + 1, (reads, state["reads"])  # one read of the page for the send's end, no reload
        follow.close()
        assert not errors, errors
        assert not bad, bad
        browser.close()
finally:
    server.close()
    fixture.doCleanups()
print("certification page (stubbed) ok")


# ----- Part two: the real certification service ---------------------------------------------------------------------------------
import sqlite3  # noqa: E402
from test_grading_certification import Certify  # noqa: E402
import test_gradescope_send  # noqa: E402

Certify.setUpClass()
real = Certify("test_the_page_names_students_counts_their_grades_and_never_a_uin")
real.setUp()
real.add_carol()  # a third student who joined the roster after the exam: a participant with no paper
aid, q20 = real.aid, real.q[("q20", "A")]
alice, bob, carol = real.name(real.row1), real.name(real.row2), "Example, Carol"
UINS = [*real.uins.values(), "234567890"]
server = create_server(real.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
REAL = f"/api/grading/assignments/{aid}/certification"


def catalog(sql, *args):
    with sqlite3.connect(real.path) as db:
        return db.execute(sql, args).fetchall()


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(20000)
        errors, bad, urls = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        page.on("request", lambda request: urls.append(request.url))

        with page.expect_response(lambda r: r.url.endswith(REAL) and r.request.method == "GET") as first:
            page.goto(f"{address}/#grading/{aid}?tab=export")
        body = first.value.text()
        assert not any(uin in body for uin in UINS), "the page read names no UIN"
        root = page.locator(".grading-certification")
        expect(root.locator("[data-step-lead]")).to_have_text("2 of 3 students have every grade. Check them here, then certify.")
        expect(root.locator("[data-cert-band] > .callout")).to_contain_text("Certifying records 0 for 1 student")

        # A question's panel: its rubric in the original wording with how often each line is in a final grade, and its responses.
        root.locator(f'[data-cert-question-open="{q20}"]').click()
        panel = root.locator(f'[data-cert-question-panel="{q20}"]')
        expect(panel.locator("#cert-question-title")).to_contain_text("Question 20 · Version A")
        expect(panel.locator('[data-rubric-line="R3_2"]')).to_contain_text("Set up the equation correctly")
        expect(panel.locator('[data-rubric-line="R3_2"] td').nth(2)).to_have_text("1")
        assert f"question={q20}" in page.url, page.url
        responses = panel.locator("tbody:has(tr[data-response])")
        expect(responses.locator("tr")).to_have_count(1)
        expect(responses).to_contain_text(alice)
        expect(responses).to_contain_text("You · approved")
        expect(responses).to_contain_text("Changed by you")  # the model ticked R3_1; the instructor chose R3_2
        panel.locator('[data-filter-group="cert-responses"] [data-filter="instructor"]').click()
        expect(responses.locator("tr")).to_have_count(1)
        # Hide names: the list says "Response 1" (a browser preference).
        panel.locator("[data-cert-hide-names]").check()
        expect(responses).not_to_contain_text(alice)
        expect(responses).to_contain_text("Response 1")
        panel.locator("[data-cert-hide-names]").uncheck()
        expect(responses).to_contain_text(alice)
        expect(panel.locator("[data-cert-rubric-link]")).to_have_attribute("href", f"#grading/{aid}?tab=answer-key&focus={q20}")
        page.keyboard.press("Escape")
        expect(root.locator("[data-cert-question-panel]")).to_have_count(0)
        assert "question=" not in page.url, page.url
        expect(root.locator(f'[data-cert-question-open="{q20}"]')).to_be_focused()

        # The student search: words filter names; four or more digits go in a POST body and come back as names.
        search = root.locator("[data-cert-search]")
        search.fill("carol")
        table = root.locator("#cert-panel")
        expect(table.locator("tbody tr")).to_have_count(1)
        expect(table.locator("tbody tr")).to_contain_text("No paper")
        expect(table.locator("tbody tr [data-cert-excuse]")).to_have_text("Excuse…")
        digits = real.uins[real.second]
        with page.expect_response(lambda r: r.url.endswith("/students/search")) as found:
            search.fill(digits)
        assert not any(uin in found.value.text() for uin in UINS)
        expect(table.locator("tbody tr[data-cert-match]").first).to_contain_text(bob)
        assert digits not in page.url and digits not in page.content()
        assert not any(digits in url for url in urls)
        search.fill("")
        expect(table.locator("tbody tr")).to_have_count(3)

        # The drawer: the student's questions, one opened read only with the models' flags, then the desk (approved and manual only).
        table.locator(f'[data-cert-open="{real.row1}"]').click()
        drawer = root.locator(".certification-drawer")
        expect(drawer.locator("#cert-drawer-title")).to_have_text(alice)
        expect(drawer).to_contain_text("2 of 2 questions final")
        # Beside the docked drawer the files follow their section's width, not the window's: no file's line squeezed to a word
        # beside its button (two panels with lines about 40 px wide, requirement, 2026-10-01).
        lines = page.evaluate("""() => [...document.querySelectorAll('.certification-files .grading-export-list li > span')]
            .filter(line => line.checkVisibility()).map(line => Math.round(line.getBoundingClientRect().width))""")
        assert lines and min(lines) >= 200, lines
        page.locator(".certification-files").screenshot(path=str(OUTPUT / "certification-files-beside-drawer.png"))
        drawer.locator(f'[data-cert-q-toggle="{q20}"]').click()
        item = drawer.locator(f'[data-cert-q="{q20}"]')
        expect(item.locator(".certification-response")).to_contain_text("Set up the equation correctly")
        # Goal first (2026-10-06, "a wall of text"): the grade, its lines and Change grade in one row before the paper; the other
        # lines, the reading and the flags fold closed.
        expect(item.locator(".certification-verdict [data-cert-change]")).to_be_visible()
        expect(item.locator(".certification-reading")).not_to_have_attribute("open", "")
        order = item.evaluate("""node => [...node.querySelectorAll('.certification-verdict, .certification-crops, .certification-fold')]
            .map(el => el.classList.contains('certification-verdict') ? 'grade' : el.classList.contains('certification-crops') ? 'paper' : 'fold')""")
        assert order[0] == "grade" and "grade" not in order[1:] and order.index("fold") > (order.index("paper") if "paper" in order else 0), order
        item.screenshot(path=str(OUTPUT / "certification-drawer-question.png"))
        item.locator("[data-cert-change]").click()
        desk = item.locator("[data-cert-desk]")
        expect(desk.locator("[data-desk-heading]")).to_contain_text(alice)
        expect(desk.get_by_role("button", name="Save as draft", exact=True)).to_have_count(0)  # no draft: the draft trap
        expect(desk.get_by_role("button", name="Mark unreadable", exact=True)).to_have_count(0)
        expect(desk.get_by_role("button", name="No credit (manual 0)", exact=True)).to_be_visible()
        item.locator("[data-cert-desk-cancel]").click()
        expect(item.locator("[data-cert-change]")).to_be_visible()
        page.keyboard.press("Escape")
        expect(root.locator(".certification-drawer")).to_have_count(0)
        expect(table.locator(f'[data-cert-open="{real.row1}"]')).to_be_focused()

        # A weighted gradebook with a hand-made "Exam 1" (in a category of weight 0) and homework: the column is one select and a new
        # column's category is proposed (rest-shell-1, certify-1).
        real.gradebook.save_setup(real.term_id, {"base_revision": real.setup()[0], "request_id": str(uuid.uuid4()), "setup": {"mode": "weighted",
            "categories": [{"ref": "exams", "name": "Semester Exams", "weight": "60"}, {"ref": "other", "name": "Other", "weight": "0"}],
            "items": [{"title": "Exam 1", "points": "18", "category_id": "other"}, {"title": "Homework 1", "points": "5"}]}})
        categories = {c["name"]: c["id"] for c in real.setup()[1]["categories"]}
        hand = next(i["id"] for i in real.setup()[1]["items"] if i["title"] == "Exam 1")
        # Carol, about to receive 0, has two absences recorded; the exam was given on its main sitting's date (requirement, 2026-10-02:
        # the dialog shows them beside her 0, and excuses nothing). Both are not excused: an excused or undecided one over that day
        # would leave her out until her makeup instead (MAKEUP OWED, 2026-10-07).
        real.exams.save_sitting(real.event["id"], {"version": real.exams.get(real.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        for first, last, status in (("2026-09-24", "2026-09-25", "not_excused"), ("2026-09-08", "2026-09-08", "not_excused")):
            real.absence(real.carol, first, last, status)
        recorded = real.absence_rows()

        # Certify: the zero, Gradescope not sent (with a reason) and the new gradebook column each need their answer.
        root.locator("[data-cert-certify]").click()
        dialog = page.locator("#dialog")
        expect(dialog.locator("[data-certify-summary]")).to_contain_text("3 students: 2 scored, 1 receive 0")
        previews = len([url for url in urls if url.endswith(REAL + "/preview")])
        choice = dialog.locator('select[name="certify-column"]')
        # The one column with the assignment's name is the default (requirement, 2026-10-02): no category to choose, no second column,
        # one line saying where the values go and what becomes of those already there (nothing typed there yet), and, since its
        # category has weight 0, the warning that it does not count toward Overall (review: the default must not hide it).
        expect(choice).to_have_value("use:" + hand)
        assert choice.locator("optgroup").evaluate_all("groups => groups.map(g => [g.label, g.children.length])") == [["Same name", 1], ["New column", 1], ["Other columns (1)", 1]]
        expect(dialog.locator('input[name="certify-column"]')).to_have_count(0)
        category = dialog.locator("[data-certify-category]")
        expect(category).to_have_count(0)
        expect(dialog.locator("[data-certify-duplicate]")).to_have_count(0)
        expect(dialog.locator(".certification-gradebook .grading-warning")).to_have_count(1)
        expect(dialog.locator("[data-certify-uncounted]")).to_have_text("“Exam 1” will not count toward the overall grade: this gradebook is weighted by category, and its "
                                                                       "category “Other” has weight 0. Move it to a counted category in the gradebook's Setup, or choose a new column here.")
        expect(dialog.locator("[data-certify-existing]")).to_have_text("Certifying records into the existing column “Exam 1” in Other, which has this assignment's name. "
                                                                      "Nothing there was typed in Faculty for these students; values imported from Canvas or a file are replaced.")
        expect(dialog.locator('[data-certify-confirm="gradebook_column"] legend')).to_have_text("Record in the column “Exam 1”")
        # Carol's absences beside her 0 and Excuse…, the one covering the exam's date marked; nothing about them can be acted on here.
        carol_row = dialog.locator(f'[data-certify-confirm="zero_no_work"] tr[data-certify-row="{real.row3}"]')
        expect(carol_row.locator("[data-certify-excuse]")).to_have_text("Excuse…")
        expect(carol_row.locator("[data-certify-absences] li")).to_have_text(["Absence recorded Tue, Sep 8 · not excused",
                                                                               "Absence recorded Thu, Sep 24 – Fri, Sep 25 · not excused Covers the day it was given"])
        expect(carol_row.locator("[data-certify-absence-covers]")).to_have_attribute("data-tone", "neutral")
        expect(carol_row.locator("[data-certify-absences] :is(a, button, input)")).to_have_count(0)
        for width, height in ((1440, 900), (1280, 800)):
            page.set_viewport_size({"width": width, "height": height})
            carol_row.scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
            page.screenshot(path=str(OUTPUT / f"certification-real-dialog-absences-{width}.png"), full_page=False)
            dialog.locator("[data-certify-gradebook]").scroll_into_view_if_needed()
            page.screenshot(path=str(OUTPUT / f"certification-real-dialog-same-name-{width}.png"), full_page=False)
        page.set_viewport_size({"width": 1500, "height": 1100})
        # A new column instead: its category is proposed (the same-named column's), and the dialog warns it makes a second "Exam 1".
        choice.select_option("create")
        expect(choice).to_be_focused()
        expect(dialog.locator("[data-certify-existing]")).to_have_count(0)
        expect(category).to_have_value(categories["Other"])  # the same-named column's category, proposed
        expect(dialog.locator("[data-certify-uncounted]")).to_have_text("Exam 1 will not count toward the overall grade: this gradebook is weighted by category, and “Other” has weight 0.")
        # A new column makes a second "Exam 1" beside the hand-made one: the dialog says so, and where to choose it instead.
        duplicate = dialog.locator("[data-certify-duplicate]")
        expect(duplicate).to_have_text("A column named “Exam 1” already exists (Other). A new column makes a second “Exam 1”, in the same category. "
                                       "Choose it under Same name to record there instead.")
        category.select_option(categories["Semester Exams"])
        expect(dialog.locator("[data-certify-uncounted]")).to_have_count(0)
        expect(duplicate).to_have_text("A column named “Exam 1” already exists (Other). A new column makes a second “Exam 1”. Choose it under Same name to record there instead.")
        expect(dialog.locator("[data-certify-column]")).to_contain_text("“Exam 1” · 18 points · Semester Exams")
        expect(dialog.locator('[data-certify-confirm="gradebook_column"] p.supporting-copy')).to_have_text("18 points · Free response · Semester Exams")
        expect(category).to_be_focused()
        choice.select_option("use:" + hand)  # the hand-made column keeps its own category: nothing to choose
        expect(category).to_have_count(0)
        expect(duplicate).to_have_count(0)
        expect(dialog.locator('[data-certify-confirm="gradebook_column"] legend')).to_have_text("Record in the column “Exam 1”")
        choice.select_option("create")
        expect(category).to_have_value(categories["Semester Exams"])
        expect(choice).to_be_focused()
        assert len([url for url in urls if url.endswith(REAL + "/preview")]) == previews, "choosing a column or a category needs no new preview"
        # Check again (as after Excuse…) keeps the column and category chosen: both columns are called "Exam 1", so the default
        # coming back would go unseen.
        with page.expect_response(lambda r: r.url.endswith(REAL + "/preview")):
            dialog.locator("[data-certify-again-footer]").click()
        expect(dialog.locator("[data-certify-summary]")).to_be_visible()
        expect(choice).to_have_value("create")
        expect(category).to_have_value(categories["Semester Exams"])
        page.screenshot(path=str(OUTPUT / "certification-real-dialog-column.png"), full_page=False)
        certify = dialog.get_by_role("button", name="Certify", exact=True)
        expect(certify).to_be_disabled()
        # GRADESCOPE "AFTER CERTIFYING" (2026-10-12): Gradescope is set up, so the dialog offers its tick, on. This certification is
        # made without it (browser_grading_gradescope_after certifies with it): unticked, the dialog asks what it always asked.
        expect(dialog.locator("[data-certify-after-tick]")).to_be_checked()
        dialog.locator("[data-certify-after-tick]").uncheck()
        expect(dialog.locator("[data-certify-after-sends]")).to_have_count(0)
        zero = dialog.locator('[data-certify-confirm="zero_no_work"]')
        expect(zero).to_contain_text(carol)
        zero.get_by_label("This student receives 0").check()
        written = dialog.locator('[data-certify-confirm="gradescope_written"]')
        written.get_by_label("Do not send these to Gradescope").check()
        written.locator("[data-certify-reason]").fill("Rehearsal: not sent from the synthetic workspace.")
        column = dialog.locator('[data-certify-confirm="gradebook_column"]')
        column.locator("[data-certify-tick]").check()
        # Another category changes the column's words: its tick is asked again (as a column switch asks).
        category.select_option(categories["Other"])
        expect(column.locator("[data-certify-tick]")).not_to_be_checked()
        category.select_option(categories["Semester Exams"])
        expect(column.locator("[data-certify-tick]")).not_to_be_checked()
        column.locator("[data-certify-tick]").check()
        # No final tick (requirement, 2026-10-11): the last group answered, Certify is offered and says where it records.
        expect(dialog.locator("[data-certify-final]")).to_have_count(0)
        expect(dialog.locator("[data-certify-final-text]")).to_contain_text("Certify records these grades in the gradebook")
        expect(certify).to_be_enabled()
        # Enter on a tick is not a click on Certify: the dialog cancels it before the form could submit.
        assert column.locator("[data-certify-tick]").evaluate(
            "box => !box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}))")
        # The answer to the certify request is lost once: the page keeps the request and offers only its explicit retry.
        lost = {"status": None}

        def lose(route):
            if route.request.method == "POST" and lost["status"] is None:
                lost["status"] = route.fetch().status  # the server records it; the page never hears back
                route.abort()
            else:
                route.fallback()
        page.route(re.compile(re.escape(address + REAL) + r"$"), lose)
        certify.click()
        expect(dialog.locator("[data-certify-error]")).to_contain_text("No answer came back")
        assert lost["status"] == 201, lost
        page.unroute(re.compile(re.escape(address + REAL) + r"$"))
        with page.expect_response(lambda r: r.url.endswith(REAL) and r.request.method == "POST") as retried:
            dialog.get_by_role("button", name="Retry request", exact=True).click()
        assert retried.value.status == 200 and retried.value.json()["replay"] is True
        expect(page.locator("#toast")).to_contain_text("already recorded")
        expect(dialog).to_be_hidden()
        assert catalog("SELECT COUNT(*) FROM frq_certifications WHERE assignment_id=?", aid) == [(1,)]
        assert catalog("SELECT COUNT(*) FROM gradebook_requests WHERE request_id LIKE 'certify:%'") == [(1,)]
        assert real.absence_rows() == recorded, "the absences shown were only shown"
        expect(page.locator('.grading-workflow [data-pipeline-step="export"]')).to_have_class(re.compile(r"\bis-done\b"))
        expect(root.locator("[data-cert-certified]")).to_have_text("Certified")
        expect(root.locator("[data-step-lead]")).to_have_text(re.compile(r"^Certified \w+ \d+ · 3 students: 2 scored, 1 given 0\.$"))
        # The new column is in the category chosen, and the band opens it (certify-5).
        made = next(i for i in real.setup()[1]["items"] if i.get("assignment_id") == real.assignment["id"])
        assert (made["title"], made["category_id"]) == ("Exam 1", categories["Semester Exams"]), made
        expect(root.locator("[data-cert-band] > .callout").get_by_role("link", name="Open the gradebook column")).to_have_attribute("href", f"#gradebook/{real.term_id}?item={made['id']}")
        # Carol was certified at 0: the card says so, without a warning (certify-6).
        expect(root.locator('[data-cert-card="missing"]')).to_contain_text("Certified 0 · no work")
        expect(root.locator('[data-cert-card="missing"]')).not_to_have_attribute("data-attention", "")
        # The gradebook holds the certified values: Carol's confirmed 0 among them (a scored 0, never "missing").
        cells = real.cells()
        assert cells[real.row3][:2] == ("scored", "0") and cells[real.row3][2] == "grading", cells
        assert cells[real.row1][0] == "scored" and cells[real.row2][0] == "scored", cells

        # A change after certifying goes through the desk as an ordinary review and reopens the step.
        root.locator('[data-filter-group="cert-panel"] [data-filter="students"]').click()
        table.locator(f'[data-cert-open="{real.row1}"]').click()
        drawer.locator(f'[data-cert-q-toggle="{q20}"]').click()
        item.locator("[data-cert-change]").click()
        item.locator('input[name="item"][value="R3_2"]').uncheck()
        item.locator('input[name="item"][value="R3_3"]').check()
        item.get_by_role("button", name="Approve grade").click()  # the click is the confirmation (no "I inspected" tick since 2026-09-27)
        expect(page.locator("#toast")).to_contain_text("Grade approved.")
        expect(root.locator("[data-cert-band] > .callout")).to_contain_text("Grades changed since the certification")
        expect(root.locator("[data-cert-certify]")).to_have_text("Certify again…")
        expect(page.locator('.grading-workflow [data-pipeline-step="export"]')).not_to_have_class(re.compile(r"\bis-done\b"))
        expect(item.locator(".certification-q-toggle strong")).to_have_text("2 / 10")
        # The open drawer follows the page's new read: the student's certified line now says it changed since.
        expect(drawer.locator("[data-cert-drawer-certified]")).to_contain_text("changed since")
        page.keyboard.press("Escape")
        # "Changed since certification" lists exactly that student.
        table.locator('[data-filter-group="cert-students"] [data-filter="changed"]').click()
        expect(table.locator("tbody tr")).to_have_count(1)
        expect(table.locator("tbody tr")).to_contain_text(alice)
        table.locator('[data-filter-group="cert-students"] [data-filter="all"]').click()

        # A line added to every final grade of the question in its section (2026-09-27): preview first, then one review per grade.
        root.locator('[data-filter-group="cert-panel"] [data-filter="questions"]').click()
        root.locator(f'[data-cert-question-open="{q20}"]').click()
        panel.locator(f'[data-cert-adjust="{q20}"]').click()
        adjust = page.locator("#dialog")
        expect(adjust.locator(".dialog-header")).to_contain_text("Add a line to every grade")
        expect(adjust.locator('select[name="section"]')).to_have_value(real.section["id"])  # the exam's one section is the choice
        adjust.locator('input[name="reason"]').fill("Misprint in the problem")
        adjust.locator('input[name="points"]').fill("1")
        before = catalog("SELECT COUNT(*) FROM frq_reviews")
        adjust.get_by_role("button", name="Preview", exact=True).click()
        expect(adjust.locator("[data-adjust-summary]")).to_have_text("1 final grade in section 501 gets “Misprint in the problem” (+1).")
        expect(adjust.locator("[data-adjust-change]")).to_contain_text(alice)
        expect(adjust.locator("[data-adjust-change] td").nth(1)).to_have_text("2")
        expect(adjust.locator("[data-adjust-change] td").nth(2)).to_have_text("3")
        assert catalog("SELECT COUNT(*) FROM frq_reviews") == before, "the preview writes nothing"
        # Another line is another request: editing it asks for a new preview.
        adjust.locator('input[name="points"]').fill("1.5")
        expect(adjust.get_by_role("button", name="Preview", exact=True)).to_be_visible()
        expect(adjust.locator("[data-adjust-summary]")).to_have_count(0)
        adjust.locator('input[name="points"]').fill("1")
        adjust.get_by_role("button", name="Preview", exact=True).click()
        page.screenshot(path=str(OUTPUT / "certification-real-adjust.png"), full_page=False)
        body_fits = "(() => { const b = document.querySelector('#dialog .dialog-body'); return b.scrollWidth <= b.clientWidth + 1; })()"
        assert page.evaluate(body_fits), "the dialog's body needs no sideways scroll"
        page.set_viewport_size({"width": 1280, "height": 800})
        assert page.evaluate(body_fits), "nor at 1280"
        page.screenshot(path=str(OUTPUT / "certification-real-adjust-1280.png"), full_page=False)
        page.set_viewport_size({"width": 1500, "height": 1100})
        with page.expect_response(lambda r: r.url.endswith(f"/questions/{q20}/adjustments") and r.request.method == "POST") as applied:
            adjust.get_by_role("button", name="Add to 1 grade", exact=True).click()
        assert applied.value.status == 201, applied.value.text()
        expect(page.locator("#toast")).to_contain_text("Added “Misprint in the problem” (+1) to 1 grade.")
        expect(adjust).to_be_hidden()
        expect(panel.locator("tbody tr[data-response]")).to_contain_text("3 / 10")
        assert catalog("SELECT COUNT(*) FROM frq_reviews") == [(before[0][0] + 1,)]
        assert catalog("SELECT COUNT(*) FROM frq_events WHERE kind='grades_adjusted'") == [(1,)]
        # The line is named beside the grade's rubric lines, in the panel and in the student's drawer, and listed with Edit… and Remove….
        expect(panel.locator("tbody tr[data-response] [data-cert-adjustment]")).to_have_text("+1 Misprint in the problem")
        batches = panel.locator("[data-cert-batches] li[data-cert-batch]")
        expect(batches).to_have_count(1)
        expect(batches).to_contain_text("section 501 · on 1 of 1 grade it changed")
        panel.screenshot(path=str(OUTPUT / "certification-real-adjust-panel.png"))
        panel.locator("tbody tr[data-response] [data-cert-response-open]").click()
        toggle = drawer.locator(f'[data-cert-q-toggle="{q20}"]')
        expect(toggle).to_be_visible()
        if toggle.get_attribute("aria-expanded") != "true":
            toggle.click()
        adjustment = drawer.locator(f'[data-cert-q="{q20}"] [data-cert-adjustment]')
        expect(adjustment.locator(".certification-line-points")).to_have_text("+1")
        expect(adjustment.locator(".certification-line-text")).to_contain_text("Misprint in the problem")
        page.keyboard.press("Escape")
        expect(root.locator(".certification-drawer")).to_have_count(0)
        # Edit…: the line's points change on the grade that still carries it.
        batches.locator("[data-cert-batch-edit]").click()
        expect(adjust.locator(".dialog-header")).to_contain_text("Change a line on every grade")
        expect(adjust.locator('input[name="reason"]')).to_have_value("Misprint in the problem")
        adjust.locator('input[name="points"]').fill("2")
        adjust.get_by_role("button", name="Preview", exact=True).click()
        expect(adjust.locator("[data-adjust-summary]")).to_have_text("1 grade has “Misprint in the problem” (+1) changed to “Misprint in the problem” (+2).")
        adjust.get_by_role("button", name="Change on 1 grade", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Changed “Misprint in the problem” (+1) to “Misprint in the problem” (+2) on 1 grade.")
        expect(panel.locator("tbody tr[data-response]")).to_contain_text("4 / 10")
        expect(panel.locator("tbody tr[data-response] [data-cert-adjustment]")).to_have_text("+2 Misprint in the problem")
        # Remove…: previewed as the dialog opens; the confirmation is the footer's danger button.
        batches.locator("[data-cert-batch-remove]").click()
        expect(adjust.locator(".dialog-header")).to_contain_text("Remove a line from every grade")
        expect(adjust.locator("[data-adjust-summary]")).to_have_text("“Misprint in the problem” (+2) comes off 1 grade.")
        remove = adjust.get_by_role("button", name="Remove from 1 grade", exact=True)
        expect(remove).to_have_class(re.compile(r"\bdanger\b"))
        page.screenshot(path=str(OUTPUT / "certification-real-adjust-remove.png"), full_page=False)
        remove.click()
        expect(page.locator("#toast")).to_contain_text("Removed “Misprint in the problem” (+2) from 1 grade.")
        expect(panel.locator("tbody tr[data-response]")).to_contain_text("2 / 10")
        expect(panel.locator("tbody tr[data-response] [data-cert-adjustment]")).to_have_count(0)
        expect(panel.locator("[data-cert-batches]")).to_contain_text("No batch line is on any grade now.")
        expect(panel.locator("[data-cert-batch-history]")).to_have_count(3)
        assert catalog("SELECT COUNT(*) FROM frq_events WHERE kind='grades_adjusted'") == [(3,)]
        page.keyboard.press("Escape")

        # The gradebook column pulls the certification, not live grading, and says grading may have changed since.
        term = real.term_id
        item_id = catalog("SELECT item_id FROM gradebook_scores WHERE term_id=? LIMIT 1", term)[0][0]
        page.goto(f"{address}/#gradebook/{term}")
        page.locator(f'[data-pull="{item_id}"]').click()
        pull = page.locator("#gradebook-pull-form")
        expect(pull.locator("[data-pull-source]")).to_contain_text("Values come from the certification of")
        expect(pull.locator("[data-certification-changed]")).to_contain_text("Grades may have changed in Grading since that certification")
        pull.get_by_role("button", name="Cancel", exact=True).click()
        page.locator(f'[data-pull="{item_id}"]').focus()

        # A certified grade leads to its paper: the student's record has Paper, and Certification and Export opens that student's
        # drawer (the row travels in memory, never in the address).
        page.locator(f'tr[data-row="{real.row1}"] .gb-name-button').click()
        record = page.locator("#gradebook-student-form")
        expect(record).to_be_visible()
        for fold in record.locator("[data-record-fold]").all():  # the record opens folded to its categories
            fold.click()
        record.locator(f'[data-paper="{item_id}"]').click()
        expect(page).to_have_url(f"{address}/#grading/{aid}?tab=export")
        expect(root.locator(".certification-drawer")).to_have_count(1)
        assert real.row1 not in page.url
        page.keyboard.press("Escape")
        expect(root.locator(".certification-drawer")).to_have_count(0)
        # The cell's history offers the same (See the graded paper) beside Back to the record.
        page.goto(f"{address}/#gradebook/{term}")
        page.locator(f'tr[data-row="{real.row1}"] .gb-name-button').click()
        for fold in record.locator("[data-record-fold]").all():
            fold.click()
        record.locator(f'[data-change="{item_id}"]').click()
        history = page.locator("#gradebook-history-form")
        expect(history.get_by_role("button", name="See the graded paper")).to_be_visible()
        history.get_by_role("button", name=re.compile("^Back to ")).click()
        expect(record).to_be_visible()
        record.get_by_role("button", name="Close", exact=True).click()

        # A certified assignment's row on Assignments opens here on every student's grades (requirement, 2026-10-09): `open=students`
        # chooses Students with All once, leaves the address, and keeps the tab the instructor last chose for an ordinary visit.
        page.evaluate("localStorage.setItem('faculty:certification:tab', 'questions')")
        page.goto(f"{address}/#grading/{aid}?tab=export&open=students")
        expect(root.locator('[data-filter-group="cert-panel"] [data-filter="students"]')).to_have_attribute("aria-pressed", "true")
        expect(root.locator('[data-filter-group="cert-students"] [data-filter="all"]')).to_have_attribute("aria-pressed", "true")
        expect(table.locator("tbody tr")).to_have_count(3)
        expect(page).to_have_url(f"{address}/#grading/{aid}?tab=export")
        assert page.evaluate("localStorage.getItem('faculty:certification:tab')") == "questions"
        page.screenshot(path=str(OUTPUT / "certification-open-students-1500.png"), full_page=False)

        # The narrowest desktop window (1280): the drawer docks beside the tables without a horizontal page scroll, closes with
        # its ×; no inline style attribute (CSP).
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{address}/#grading/{aid}?tab=export")
        expect(root.locator("[data-step-lead]")).to_contain_text("keeps the values certified")
        root.locator('[data-filter-group="cert-panel"] [data-filter="students"]').click()
        table.locator(f'[data-cert-open="{real.row3}"]').click()
        expect(drawer.get_by_role("button", name="Close", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(OUTPUT / "certification-real-drawer-1280.png"), full_page=False)
        drawer.get_by_role("button", name="Close", exact=True).click()
        expect(root.locator(".certification-drawer")).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        # (An empty style attribute is the CSSOM's trace, e.g. a cleared inline size; the markup itself carries none.)
        styled = page.evaluate("[...document.querySelectorAll('[data-cert-main] [style]:not([style=\"\"]), [data-cert-tables] [style]:not([style=\"\"])')].map(n => n.outerHTML.slice(0, 200))")
        assert not styled, styled
        page.set_viewport_size({"width": 1500, "height": 1100})
        page.screenshot(path=str(OUTPUT / "certification-real-1500.png"), full_page=True)
        assert not errors, errors
        assert not bad, bad
        browser.close()
finally:
    server.close()
    real.doCleanups()
    test_gradescope_send.tearDownModule()
print("certification page (real service) ok")
