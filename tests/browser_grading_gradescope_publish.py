"""Certification and Export: Publish grades on Gradescope (AGENTS.md "Sending grades to Gradescope", PUBLISHING GRADES; instructor,
2026-10-02).

The page runs against the synthetic FRQ fixture app. Its first look is the real, unconfigured routes (no sign-in file here). Everything
configured is answered by browser route stubs shaped like `grading/gradescope_publish.py`'s status, preview, publish, cancel and
retry: nothing here reaches Gradescope, a fake of it, or a sign-in file."""
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
state = {"previews": [], "published": [], "retried": [], "polls": 0}
PUBLICATIONS = []


def row(key, section, **over):
    return {"key": key, "part": key.split(":")[0], "section_id": key.split(":")[1], "section": section, "course_id": "1413801", "course": f"140-{section}",
            "target_id": "409214", "title": "Quiz <3>", "kind": "assignment_container", "versions": [{"letter": "A", "published": False}, {"letter": "B", "published": False}],
            "submissions": 78, "graded": 100, "published": False, "partly": False, "left_out": False, "problem": "", "warning": "", **over}


def preview(leave_out):
    """A preview shaped like `PublishMixin._publish_plan`'s view: section 507 to publish, 508 already published, 514's bubble-sheet exam
    not linked."""
    rows = [row("written:s507", 507, graded=87.5, warning="Gradescope shows it 87.5% graded: students would see the questions nobody graded there without a score."),
            row("written:s508", 508, published=True, title="Quiz 3"),
            row("sheets:s514", 514, target_id="", title="", problem="No Gradescope bubble-sheet exam is linked for this section yet: send its sheets first.")]
    for r in rows:
        if r["key"] in leave_out:
            r.update(left_out=True, submissions=0, graded=None, warning="")
    publish = sum(1 for r in rows if not r["left_out"] and not r["problem"] and not r["published"])
    return {"assignment_id": plan_id, "configured": True, "problem": "", "rule": "gradescope-publish-v1", "rows": rows, "problems": [],
            "warnings": ["These grades are not certified yet.", "4 final grades are not on Gradescope as Faculty has them (not sent, or changed since sent): students would see what Gradescope has now."],
            "certification": "none", "running": None, "publish": publish, "ready": bool(publish),
            "fingerprint": uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(sorted(leave_out))).hex * 2}


def gradescope(route):
    request, path = route.request, route.request.url.split(address, 1)[-1].split("?")[0]
    body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
    if path == BASE + "/publications":
        state["polls"] += 1
        if PUBLICATIONS and PUBLICATIONS[0]["status"] == "running" and state["polls"] > 3:  # it finishes after a poll or two
            PUBLICATIONS[0].update(status="complete")
            PUBLICATIONS[0]["units"][0].update(status="accepted")
        route.fulfill(json={"assignment_id": plan_id, "configured": True, "problem": "", "publications": PUBLICATIONS})
    elif path == BASE + "/publish/preview":
        state["previews"].append(body)
        route.fulfill(json=preview(body.get("leave_out") or []))
    elif path == BASE + "/publish":
        state["published"].append(body)
        state["polls"] = 0
        PUBLICATIONS.insert(0, {"id": "p1", "status": "running", "created_at": "2026-10-02T15:00:00+00:00", "updated_at": "2026-10-02T15:00:00+00:00", "plan": {},
                                "units": [{"id": "u1", "part": "written", "sections": ["507"], "course": "140-507", "title": "Quiz <3>", "status": "sending", "error": ""}]})
        route.fulfill(status=201, json={"publication_id": "p1", "request_id": body["request_id"], "status": "pending", "created_at": "2026-10-02T15:00:00+00:00", "plan": {}})
    elif re.fullmatch(re.escape(BASE) + r"/publications/[^/]+/retry", path):
        state["retried"].append(path)
        PUBLICATIONS[0]["units"][0].update(status="pending", error="")
        route.fulfill(json={"publication_id": "p1", "status": "running"})
    elif path == BASE and request.method == "GET":
        route.fulfill(json={"assignment_id": plan_id, "configured": True, "problem": "", "coverage": {"papers": {"total": 42, "sent": 42},
                            "grades": {"final": 520, "sent": 516, "changed": 0, "not_sent": 4}}, "targets": [], "sends": [], "creations": []})
    else:
        route.fallback()


def certification(route):
    """The real certification read, saying Gradescope is set up (the checks have no sign-in file): the header then offers Publish."""
    if route.request.method != "GET":
        return route.fallback()
    response = route.fetch()
    body = response.json()
    body["gradescope"]["configured"] = True
    route.fulfill(response=response, json=body)


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 and not response.url.endswith("/templates/prepare") else None)

        def open_fold():
            fold = page.locator("#cert-fold-gradescope")
            expect(fold).to_be_visible()
            if fold.get_attribute("open") is None:
                fold.locator(":scope > summary").click()

        # Not set up (the real routes): the section says so and offers nothing to press.
        page.goto(f"{address}/#grading/{plan_id}?tab=export")
        open_fold()
        section = page.locator(".grading-gradescope-publish")
        expect(section).to_contain_text("Publishing on Gradescope")
        expect(section).to_contain_text("Gradescope is not set up for this Faculty")
        expect(section.get_by_role("button", name="Publish grades on Gradescope…", exact=True)).to_have_count(0)
        header = page.locator("[data-step-actions]")
        # The fixture's grading is unfinished (a response not graded, a student without a grade), so the header's primary leads to
        # those checks instead of Certify grades… (ASSIGNMENT-WORKSPACE "Workflow clarity", 2026-10-09).
        expect(header.get_by_role("button", name=re.compile(r"^Resolve \d+ checks?$"))).to_be_visible()
        expect(header.get_by_role("button", name="Certify grades…", exact=True)).to_have_count(0)
        expect(header.get_by_role("button", name="Publish grades on Gradescope…", exact=True)).to_have_count(0)  # only where it can work
        expect(page.locator(".grading-gradescope")).to_have_count(1)  # the send panel's own locator still finds one panel

        # Set up: the section sits after the send panel, in the same fold.
        page.route("**/api/grading/gradescope/**", gradescope)
        page.route(re.compile(re.escape(f"/api/grading/assignments/{plan_id}/certification") + r"$"), certification)
        page.reload()
        open_fold()
        expect(page.locator("[data-gradescope-host] + [data-gradescope-publish-host], [data-gradescope-sheets-host] + [data-gradescope-publish-host]")).to_have_count(1)
        # ONE PLACE TO PUBLISH (2026-10-18 review item 10): the header holds Publish grades on Gradescope…; the fold's section shows what
        # was published (with Cancel and Retry) and names the header's button in words.
        expect(section.get_by_role("button", name="Publish grades on Gradescope…", exact=True)).to_have_count(0)
        expect(section.locator("[data-gradescope-publish-where]")).to_have_text("Publish with Publish grades on Gradescope… at the top of this page.")
        start = page.locator("[data-step-actions]").get_by_role("button", name="Publish grades on Gradescope…", exact=True)
        expect(start).to_be_visible()
        expect(page.get_by_role("button", name="Publish grades on Gradescope…", exact=True)).to_have_count(1)
        section.get_by_text("How publishing works", exact=True).click()
        expect(section).to_contain_text("Gradescope emails nobody for publishing, and Faculty never unpublishes")
        start.click()
        dialog = page.locator("#dialog")
        expect(dialog).to_contain_text("Faculty reads Gradescope and shows what it would publish.")
        table = dialog.get_by_role("region", name="What would be published on Gradescope")
        expect(table).to_contain_text("Quiz <3> (versions A, B)")
        expect(table).to_contain_text("87.5% graded: students would see the questions nobody graded")
        expect(table.locator('[data-gradescope-publish-key="written:s508"]')).to_contain_text("Published")
        expect(table.locator('[data-gradescope-publish-key="written:s508"] input')).to_have_count(0)  # nothing to choose there
        expect(table.locator('[data-gradescope-publish-key="sheets:s514"]')).to_contain_text("No Gradescope bubble-sheet exam is linked")
        expect(dialog.locator("[data-gradescope-publish-warnings]")).to_contain_text("These grades are not certified yet.")
        submit = dialog.get_by_role("button", name="Publish grades", exact=True)
        # Leaving 507 out reads the preview again without it: nothing is left to publish.
        dialog.get_by_label("Publish Written grades of section 507", exact=True).uncheck()
        expect(dialog).to_contain_text("Nothing to publish")
        assert state["previews"][-1] == {"leave_out": ["written:s507"]}, state["previews"]
        expect(submit).to_be_disabled()
        dialog.get_by_label("Publish Written grades of section 507", exact=True).check()
        expect(dialog.get_by_label("I checked this. Faculty publishes 1 Gradescope assignment; students then see their grades there.", exact=True)).to_be_visible()
        assert state["previews"][-1] == {"leave_out": []}, state["previews"]
        for width, name in ((1500, "wide"), (1280, "1280")):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"grading-gradescope-publish-preview-{name}.png"))
        page.set_viewport_size({"width": 1500, "height": 1100})
        # The tick first; then one more question in its own popup. Cancel publishes nothing.
        submit.click()
        expect(dialog.locator(".form-error")).to_contain_text("Tick the box")
        dialog.get_by_label("I checked this. Faculty publishes 1 Gradescope assignment; students then see their grades there.", exact=True).check()
        submit.click()
        popup = page.locator("dialog.grading-gradescope-published-dialog")
        expect(popup).to_contain_text("Publish 1 assignment on Gradescope?")
        expect(popup).to_contain_text("Section 507 → Quiz <3> (Written grades, 78 submissions)")
        expect(popup).not_to_contain_text("Section 508")
        expect(popup).to_contain_text("These grades are not certified yet.")
        expect(popup).to_contain_text("Gradescope emails nobody for publishing.")
        expect(popup.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
        page.screenshot(path=str(OUTPUT / "grading-gradescope-publish-confirm.png"))
        popup.get_by_role("button", name="Cancel", exact=True).click()
        expect(popup).to_have_count(0)
        assert state["published"] == [], state["published"]
        submit.click()
        popup.get_by_role("button", name="Publish grades", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.locator("#toast")).to_contain_text("Faculty is publishing the grades on Gradescope")
        [sent] = state["published"]
        assert sent["confirmation"] == "publish" and sent["leave_out"] == [] and re.fullmatch(r"[0-9a-f]{64}", sent["fingerprint"]), sent
        card = section.locator('[data-gradescope-publication="p1"]')
        expect(card).to_contain_text("Publishing")
        expect(card).to_contain_text("Section 507 Written grades → Quiz <3> (140-507) · publishing")
        # QUIET REFRESH (FRONTEND.md): a poll that reads the same status changes nothing in the section (its elements are the same ones).
        section.evaluate("""node => { node.querySelector("[data-gradescope-publication]").dataset.probe = "kept"; window.__publishChanges = 0;
            window.__publishObserver = new MutationObserver(list => { window.__publishChanges += list.length; });
            window.__publishObserver.observe(node, {childList: true, subtree: true, attributes: true, characterData: true}); }""")
        seen = state["polls"]
        for _ in range(100):
            if state["polls"] >= seen + 2:
                break
            page.wait_for_timeout(200)
        assert state["polls"] >= 3, state["polls"]
        page.wait_for_timeout(500)
        assert page.evaluate("window.__publishChanges") == 0, "an unchanged poll rewrote the publish section"
        expect(card).to_have_attribute("data-probe", "kept")  # the same element, not a new one
        page.evaluate("window.__publishObserver.disconnect()")
        expect(card).to_contain_text("Published", timeout=20000)  # its poll follows it to the end
        expect(card).to_contain_text("· published")
        # How publishing works, opened before publishing, stayed open through every poll's redraw (certify-3).
        expect(section.locator('[data-gradescope-fold="publish-rules"]')).to_have_attribute("open", "")
        assert state["polls"] > 1, state["polls"]
        section.scroll_into_view_if_needed()
        page.screenshot(path=str(OUTPUT / "grading-gradescope-publish.png"), full_page=True)

        # One that did not go says why, with Retry.
        PUBLICATIONS[0].update(status="complete")
        PUBLICATIONS[0]["units"][0].update(status="failed", error="Gradescope's Review Grades page has no Publish Grades form Faculty recognizes; nothing was sent.")
        page.reload()
        open_fold()
        expect(card).to_contain_text("Not published in 1 assignment")
        expect(card).to_contain_text("no Publish Grades form Faculty recognizes")
        card.get_by_role("button", name="Retry (1)", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Publishing again where it did not go through.")
        assert state["retried"] == [BASE + "/publications/p1/retry"], state["retried"]

        # The same dialog from the step's header (requirement, 2026-10-02), seen without opening the
        # fold; a publication started there opens the fold so its progress is in view.
        PUBLICATIONS.clear()
        page.reload()
        fold = page.locator("#cert-fold-gradescope")
        expect(section.locator("[data-gradescope-publish-where]")).to_be_attached()
        if fold.get_attribute("open") is not None:
            fold.locator(":scope > summary").click()
        expect(fold).not_to_have_attribute("open", "")
        header = page.locator("[data-step-actions]")
        button = header.get_by_role("button", name="Publish grades on Gradescope…", exact=True)
        expect(button).to_be_visible()
        expect(header.locator(".button.primary")).to_have_count(1)  # Publish is secondary: the step's own action stays the one primary
        page.set_viewport_size({"width": 1280, "height": 900})
        page.screenshot(path=str(OUTPUT / "grading-gradescope-publish-header-1280.png"))
        page.set_viewport_size({"width": 1500, "height": 1100})
        button.click()
        expect(dialog).to_contain_text("Faculty reads Gradescope and shows what it would publish.")
        dialog.get_by_label("I checked this. Faculty publishes 1 Gradescope assignment; students then see their grades there.", exact=True).check()
        dialog.get_by_role("button", name="Publish grades", exact=True).click()
        popup.get_by_role("button", name="Publish grades", exact=True).click()
        expect(dialog).not_to_be_visible()
        assert len(state["published"]) == 2 and state["published"][-1]["confirmation"] == "publish", state["published"]
        expect(fold).to_have_attribute("open", "")
        expect(section.locator('[data-gradescope-publication="p1"]')).to_contain_text("Section 507 Written grades → Quiz <3> (140-507)")

        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Publish grades on Gradescope passed: not set up, preview rows (published, unlinked, graded share), leave out and back, the tick, the confirm popup (Cancel publishes nothing), the publish request, progress to Published, Retry, the header's button opening the fold, at 1500 and 1280 px.")
finally:
    server.close()
    fixture.doCleanups()
