"""Gradescope after certifying, in a real page (GRADESCOPE "AFTER CERTIFYING"; requirement, 2026-10-12).

Against the REAL certification service and Gradescope sender on the graded workspace of tests/test_grading_certification.py (Alice
and Bob, section 501; a fake Gradescope; synthetic names and UINs). The instructor sent everything once, then changed one grade.
The Certify dialog offers its Gradescope tick, on: the group not on Gradescope needs no answer, the footer beside Certify says what
it sets off, unticking puts the old question back. Certified with the tick, the page says Faculty is on it; the sender's turns
(taken here, as its thread takes them) send the grade and publish, and the page says so by itself. Then the page's line for a held
section, drawn from the server's own read with a held section added, which names the header's Publish button in words.
Desktop only, one appearance (DESIGN-LANGUAGE)."""
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect  # noqa: E402
from waitress.server import create_server  # noqa: E402
from rosters import connect  # noqa: E402
from test_grading_certification import Certify  # noqa: E402
import test_gradescope_send  # noqa: E402

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
TICK = "Send to Gradescope and publish the grades after certifying"

Certify.setUpClass()
real = Certify("test_the_page_names_students_counts_their_grades_and_never_a_uin")
real.setUp()
real.send()
real.sender.drain()  # the instructor's own send: the section's assignment is linked and both papers are there
alice = real.frq.response(next(r["id"] for r in real.frq.responses(real.aid, real.q[("q20", "A")]) if r["enrollment_id"] == real.enrollment))
real.frq.save_review(alice["id"], {"version": alice["version"], "result_id": alice["current_result_id"], "status": "approved", "confirm_review": True,
                                   "selected_items": ["R3_3"]})  # one grade changed since: it is not on Gradescope as Faculty has it
aid = real.aid
server = create_server(real.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
REAL = f"/api/grading/assignments/{aid}/certification"


def published():
    return [real.fake.assignments[a]["published"] for a in [real.container, *real.versions.values()]]


def work(turns=20):
    """The Gradescope sender's turns, time passing between them, until nothing Faculty does by itself is left."""
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    for _ in range(turns):
        real.sender.drain()
        real.ready_now()
        with connect(real.path) as db:
            for table in ("frq_gradescope_automatic_runs", "frq_gradescope_publication_units"):
                db.execute(f"UPDATE {table} SET not_before=? WHERE not_before IS NOT NULL AND status IN ('pending','uncertain')", (past,))
            left = db.execute("""SELECT (SELECT COUNT(*) FROM frq_gradescope_automatic_runs WHERE status!='done')
                + (SELECT COUNT(*) FROM frq_gradescope_publications WHERE status IN ('pending','running'))""").fetchone()[0]
        if not left:
            return
    raise AssertionError("Faculty's Gradescope work did not finish")


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(20000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)

        page.goto(f"{address}/#grading/{aid}?tab=export")
        root = page.locator(".grading-certification")
        expect(root.locator("[data-cert-certify]")).to_be_visible()
        expect(root.locator("[data-cert-after]")).to_have_count(0)  # nothing was certified: nothing is said about Faculty's own work
        root.locator("[data-cert-certify]").click()
        dialog = page.locator("#dialog")
        expect(dialog.locator("[data-certify-summary]")).to_contain_text("2 students: 2 scored")
        certify = dialog.get_by_role("button", name="Certify", exact=True)

        # The tick is offered, on. The grade that is not on Gradescope then needs no answer, and "Do not send these" stays.
        after = dialog.locator("[data-certify-after]")
        expect(after.locator("legend")).to_have_text("Gradescope")
        tick = after.get_by_label(TICK)
        expect(tick).to_be_checked()
        written = dialog.locator('[data-certify-confirm="gradescope_written"]')
        expect(written.locator("[data-certify-after-sends]")).to_have_text("Faculty sends these to Gradescope by itself after you certify.")
        expect(written.locator("[data-certify-send]")).to_have_count(0)
        expect(written.get_by_label("Do not send these to Gradescope")).not_to_be_checked()
        expect(dialog.locator("[data-certify-column]")).to_contain_text("Nobody is emailed.")
        expect(dialog.locator("[data-certify-column]")).not_to_contain_text("Nothing is published")
        # Unticked, the dialog asks what it always asked; ticked again (focus kept on the tick), Faculty's line is back.
        tick.uncheck()
        expect(dialog.locator("[data-certify-after-tick]")).to_be_focused()
        expect(written.locator("[data-certify-after-sends]")).to_have_count(0)
        expect(written.locator('[data-certify-send="written"]')).to_be_visible()
        expect(after).to_contain_text("Nothing goes to Gradescope by itself")
        expect(dialog.locator("[data-certify-final-text]")).not_to_contain_text("Gradescope")
        dialog.locator("[data-certify-after-tick]").check()
        expect(written.locator("[data-certify-after-sends]")).to_be_visible()
        # Each remaining group has its one tick; then the footer beside Certify says what the click sets off.
        for box in dialog.locator("[data-certify-tick]").all():
            box.check()
        expect(dialog.locator("[data-certify-missing]")).to_have_text("")
        expect(dialog.locator("[data-certify-final-text]")).to_have_text(re.compile(r"^Certify records these grades in the gradebook column “.+”\. Faculty then sends them to Gradescope and publishes them\.$"))
        expect(certify).to_be_enabled()
        assert page.evaluate("(() => { const b = document.querySelector('#dialog .dialog-body'); return b.scrollWidth <= b.clientWidth + 1; })()")
        assert page.evaluate("document.querySelectorAll('#dialog [style]:not([style=\"\"])').length") == 0  # CSP: no inline style
        page.screenshot(path=str(OUTPUT / "gradescope-after-dialog-1500.png"), full_page=False)
        page.set_viewport_size({"width": 1280, "height": 800})
        assert page.evaluate("(() => { const b = document.querySelector('#dialog .dialog-body'); return b.scrollWidth <= b.clientWidth + 1; })()")
        page.screenshot(path=str(OUTPUT / "gradescope-after-dialog-1280.png"), full_page=False)
        page.set_viewport_size({"width": 1500, "height": 1100})

        before = len(real.fake.requests)
        with page.expect_response(lambda r: r.url.endswith(REAL) and r.request.method == "POST") as posted:
            certify.click()
        assert posted.value.status == 201, posted.value.text()
        sent = posted.value.request.post_data_json
        assert sent["gradescope_after"] is True and sent["gradescope"] == {"written": {"decision": "send"}}, sent
        expect(dialog).to_be_hidden()
        assert [r for r in real.fake.requests[before:] if r["method"] != "GET"] == [], "certifying itself writes nothing to Gradescope"
        assert published() == [False] * 4
        # Certified: the page says Faculty is on it, in one quiet line.
        expect(root.locator("[data-cert-certified]")).to_have_text("Certified")
        working = root.locator('[data-cert-after="working"]')
        expect(working).to_contain_text("Faculty is sending this to Gradescope and publishing it by itself")
        expect(root.locator("[data-cert-band] .grading-warning")).to_have_count(0)
        page.screenshot(path=str(OUTPUT / "gradescope-after-working-1500.png"), full_page=False)

        # The sender's turns: the changed grade goes, then Gradescope's own Publish All Grades, read back.
        work()
        assert published() == [True] * 4, published()
        with connect(real.path) as db:
            assert [tuple(r) for r in db.execute("SELECT actor,status FROM frq_gradescope_publications")] == [("automatic", "complete")]
            assert db.execute("SELECT COUNT(*) FROM frq_gradescope_sends WHERE actor='automatic' AND status='complete'").fetchone()[0] == 1
        assert real.fake.forbidden_hits == []
        # The page was following Faculty's work and says what it did, without a reload.
        done = root.locator('[data-cert-after="published"]')
        expect(done).to_have_text("Faculty published the grades on Gradescope by itself: section 501.", timeout=45000)
        expect(root.locator('[data-cert-after="working"]')).to_have_count(0)
        expect(root.locator("[data-cert-band] .grading-warning")).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(OUTPUT / "gradescope-after-published-1500.png"), full_page=False)
        # The Gradescope panel's cards say who did it.
        # (The fold was open since the page loaded, a grade not being on Gradescope: its panels read again as Faculty's work moved.)
        fold = root.locator("#cert-fold-gradescope")
        if fold.get_attribute("open") is None:
            fold.locator(":scope > summary").click()
        expect(fold.locator("[data-gradescope-send] [data-gradescope-automatic]").first).to_have_text("Sent by Faculty by itself, after you certified.", timeout=30000)
        expect(fold.locator("[data-gradescope-publication] [data-gradescope-automatic]")).to_have_text("Published by Faculty by itself, after you certified.", timeout=30000)
        expect(root.locator("[data-cert-band] > .callout")).to_contain_text("Students see these grades on Gradescope where they are published")
        expect(fold.locator("[data-gradescope-publication] h4")).to_have_text("Published")
        fold.screenshot(path=str(OUTPUT / "gradescope-after-panel-1500.png"))

        # A held section: the server's own read with one held section added. Its line is a warning with why, and names the header's
        # own Publish grades on Gradescope… (it publishes nothing itself).
        held = {"section": 501, "part": "written", "reason": "Gradescope shows it 96% graded: students would see the questions nobody graded there without a score."}

        def with_held(route):
            answer = route.fetch()
            view = answer.json()
            view["gradescope"]["after"] = [{"kind": "certified", "state": "done", "published": [], "held": [held],
                                           "notes": ["Section 502: its Gradescope course is not linked yet."]}]
            route.fulfill(response=answer, json=view)
        page.route(re.compile(re.escape(address + REAL) + r"(\?.*)?$"), lambda route: with_held(route) if route.request.method == "GET" else route.fallback())
        page.reload()
        line = root.locator('[data-cert-after="held"]')
        expect(line).to_contain_text("Faculty did not publish section 501 on Gradescope: Gradescope shows it 96% graded")
        expect(line).to_have_class(re.compile(r"\bgrading-warning\b"))
        expect(root.locator('[data-cert-after="notes"] li')).to_have_text("Section 502: its Gradescope course is not linked yet.")
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
            page.screenshot(path=str(OUTPUT / f"gradescope-after-held-{width}.png"), full_page=False)
        page.set_viewport_size({"width": 1500, "height": 1100})
        # One place to publish (2026-10-18 review item 10): the line names the header's button in words and holds no button itself.
        expect(line).to_contain_text("Use Publish grades on Gradescope… at the top of Certify")
        expect(line.get_by_role("button")).to_have_count(0)
        expect(root.locator("[data-cert-publish]")).to_be_visible()
        expect(page.locator("#dialog")).to_be_hidden()  # nothing was published or even asked
        assert len(real.fake.published()) == 1, "only Faculty's own publication"
        assert page.evaluate("document.querySelectorAll('[data-cert-band] [style]:not([style=\"\"])').length") == 0
        assert not errors, errors
        assert not bad, bad
        browser.close()
finally:
    server.close()
    real.doCleanups()
    test_gradescope_send.tearDownModule()
print("gradescope after certifying ok")
