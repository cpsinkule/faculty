"""The review workspace's blank-answer gallery and the flag review that starts by itself: what the page shows of both, and what each saves."""
import base64
import re
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from rosters import connect
from test_grading_proposal_gallery import BlankAnswers
from test_grading_pipeline import CORRECT
from test_rosters import STUDENTS, csv_bytes
from fake_openai import DIRECT_READING, images_of
from frq_fixtures import page as crop_image

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixtures, servers = [], []
CONFIRMED = "Confirmed blank in its gallery after looking at each crop."


def serve(fixture):
    fixture.setUp()
    fixtures.append(fixture)
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    servers.append(server)
    threading.Thread(target=server.run, daemon=True).start()
    return f"http://127.0.0.1:{server.effective_port}"


class BlankAndFlagged(BlankAnswers):
    """Five papers of one question through the direct-reading pipeline, every grader ticking the zero line, so one proposed-line group
    holds them all. In the order the queue lists them (the section's students by enrollment):
      0  written work, graded by a batch that finished two days ago: held by a grading check, the button's to send, never due by itself;
      1  no handwriting found: a blank answer that may be confirmed with the others;
      2  the same;
      3  no handwriting found on a crop that may be misaligned: shown with the blank answers, opened alone;
      4  written work, graded just now: held by the same grading check, and due for the flag review that starts by itself.
    """
    EARLIER, BLANK, MISALIGNED, FRESH = 0, (1, 2), 3, 4
    MORE = [["", f"Example, {name}", uin, "Registered", "MATH", "U1", "", "N", "", "", "", f"{name.lower()}@example.test"]
            for name, uin in (("Carol", "223456789"), ("Dave", "323456789"), ("Erin", "423456789"))]

    def setUp(self):
        # The shared fixture's section has two students: five from the start, so the first by enrollment is still the one it scans.
        with patch("test_exam_grading.csv_bytes", lambda: csv_bytes(STUDENTS + self.MORE)):
            super().setUp()
        with connect(self.path) as db:
            self.students = [row[0] for row in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? ORDER BY enrollment_id", (self.section["id"],))]
        assert len(self.students) == 5 and self.students[0] == self.enrollment, self.students
        self.scanned = {b"fixture-png-1-": self.EARLIER}  # each paper's first scanned page, by its bytes
        settings = self.frq.settings()
        self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "rubric_reviewer": "gamma-vision"}})

    def crop(self, scan, region, template=None, masks=()):
        """Each paper's crop is its own bytes, ending in its number; an empty answer area for the papers nothing is written on."""
        paper = self.scanned[bytes(scan)]
        image = crop_image(template=paper in (*self.BLANK, self.MISALIGNED)) + bytes([paper])
        return {"metadata": {"registered": paper != self.MISALIGNED, "crop_bounds": [0, 0, 100, 100]},
                "images": {kind: image for kind in ("original", "grayscale", "contrast", "ink", "cloud")}}

    def queue(self, *args, **kwargs):
        patch("grading.extraction.extract", side_effect=self.crop).start()  # over the one crop `grade` gives every paper
        return super().queue(*args, **{"selection": "new", **kwargs})

    def reading(self, name, body):
        if name != "read":
            return None
        paper = base64.b64decode(images_of(body)[0]["image_url"]["url"].split(",", 1)[1])[-1]
        return ("raw", DIRECT_READING if paper in (self.EARLIER, self.FRESH) else "   ", "stop")

    def graded(self):
        """Both batches. Returns the response of each paper, by its number."""
        self.grade(CORRECT, primary=("R4",), script=self.reading)
        with connect(self.path) as db:  # that batch finished two days ago: past the window the automatic flag review follows
            db.execute("UPDATE frq_jobs SET updated_at=? WHERE assignment_id=?", ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), self.plan["id"]))
        for paper, enrollment in enumerate(self.students[1:], 1):
            self.reviewed_source(batch=self.ready_batch(enrollment_id=enrollment), changed=f"paper-{paper}")
            self.scanned[f"fixture-png-1-paper-{paper}".encode()] = paper
        self.grade(CORRECT, primary=("R4",), script=self.reading)
        rows = {row["enrollment_id"]: row["id"] for row in self.frq.responses(self.plan["id"])}
        return [rows[enrollment] for enrollment in self.students]

    def starts_by_itself(self):
        """The worker's own turns once the two minutes are over: a look, then the request."""
        self.frq._auto_flag_review = {}  # grading ran the worker's turns, whose look already noted these grades
        with patch("grading.auto_flag_review.SETTLE", 0), patch("grading.auto_flag_review.LOOK_EVERY", 0):
            return [self.frq.auto_flag_review_one() for _ in range(3)]


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        posted, flag_reads = [], []
        page.on("request", lambda request: posted.append(request.url) if request.method == "POST" else None)
        page.on("request", lambda request: flag_reads.append(request) if request.method == "GET" and request.url.endswith("/flag-resolution") else None)

        case = BlankAndFlagged("test_a_blank_zero_the_graders_agree_on_is_approved_from_the_gallery_with_both_crops_shown")
        address = serve(case)
        papers = case.graded()
        plan = case.plan["id"]
        earlier, blanks, misaligned, fresh = papers[case.EARLIER], [papers[n] for n in case.BLANK], papers[case.MISALIGNED], papers[case.FRESH]
        # What the server says, which everything below is read against: one group of five, three of them blank answers, and a flag
        # review that could be sent two grades and starts by itself for the one just graded.
        group, = case.frq.review_queue(plan, None, case.qid, review_details=True)["questions"][0]["groups"]
        assert [m["id"] for m in group["members"]] == papers and group["applied"] == ["R4"], group
        assert [m["id"] for m in group["members"] if "blank" in m["reason_kinds"]] == [*blanks, misaligned], group
        said = case.frq.flag_resolution(plan)
        assert (said["preview"]["responses"], said["preview"]["model"], said["automatic"]["due"]) == (2, "gamma-vision", {"responses": 1, "response_ids": [fresh]}), said
        assert said["resolution"] is None and said["automatic"]["not_started"] is None, said

        page.goto(f"{address}/#grading/{plan}?tab=review")
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert f"response={earlier}" in page.url, page.url
        page.wait_for_load_state("networkidle")  # the page's second render after its state read is done
        page.evaluate("window.__sameDocument = true")
        del flag_reads[:-1]  # from here until the reload, each read of the flag review follows the one before by its own timer
        member = lambda response: page.locator(f'[data-review-member="{response}"] small')

        # FRONTEND (2026-10-11, "The step graph follows the review"): the graph above the step is fed by the plan read, which nothing
        # in the workspace made again, so it read "Review 0% · 0/5" beside a rail at 4 of 5 final until a reload. Each change is now
        # followed by ONE read of the plan, a second later. It is held here (never altered) until the page has settled on what it
        # shows next, so what the read's answer changes is exact: the graph's own nodes, patched; not the response, not the focus.
        fraction = page.locator('.grading-workflow [data-pipeline-step="review"] .grading-pipeline-fraction')
        expect(fraction).to_have_text("0% · 0/5")
        expect(page.locator("[data-workspace-progress] small")).to_have_text("0 of 5 final · 5 to review")
        page.evaluate("""window.__graph = document.querySelector('.grading-workflow [data-pipeline-step="review"] a')""")
        plan_reads, the_plan = [], re.compile(rf"/api/grading/assignments/{re.escape(plan)}$")  # the read with no `include`: no step's own
        page.route(the_plan, lambda route: plan_reads.append(route))

        def graph_follows(was, now, rail, focus):
            for _ in range(100):  # the read starts a second after the change
                if plan_reads:
                    break
                page.wait_for_timeout(50)
            assert len(plan_reads) == 1, [route.request.url for route in plan_reads]
            expect(page.locator("[data-workspace-progress] small")).to_have_text(rail)
            expect(fraction).to_have_text(was)  # until the plan answers
            focus.focus()
            expect(focus).to_be_focused()
            page.evaluate("window.__shown = document.querySelector('.grading-review-work').firstElementChild")
            address = page.url
            plan_reads.pop().continue_()
            expect(fraction).to_have_text(now)
            expect(focus).to_be_focused()
            assert page.evaluate("""() => window.__graph === document.querySelector('.grading-workflow [data-pipeline-step="review"] a')
                && window.__shown.isConnected && window.__shown === document.querySelector('.grading-review-work').firstElementChild"""), now
            assert page.url == address and not plan_reads, (page.url, address, plan_reads)

        # GRADING (2026-10-11, "Flag review runs itself"): the offer says that it starts by itself, for which of the grades it could
        # be sent, names the model, and keeps the button for a run they ask for. The blank answers are never sent.
        slot = page.locator(".grading-flag-review-slot [data-flag-review]")
        row = slot.locator(".grading-flag-review-row")
        expect(row).to_contain_text("Flag review · gamma-vision can rule on 2 flagged grades in 1 question; anything it cannot settle stays with you.")
        expect(row).to_contain_text("It starts by itself about two minutes after the batch for the 1 just graded; the button sends all 2 now.")
        expect(row.locator("[data-flag-review-start]")).to_have_text("Send 2 flagged grades to gamma-vision")
        expect(row.locator("[data-flag-review-start]")).to_be_enabled()
        expect(slot.locator('[data-flag-review-fold="how"] .disclosure-hint')).to_have_text("3 it cannot rule on")
        # The response it is about to rule on is labelled in the list; the earlier one, which only the button would send, is not.
        expect(page.locator("[data-review-member]")).to_have_count(5)
        expect(member(fresh)).to_have_text("With the flag review")
        expect(member(earlier)).to_have_text("Grading check")
        for response in (*blanks, misaligned):
            expect(member(response)).to_have_text("Reading check · Possibly blank")

        # GRADING (2026-10-11, "Blank answers in one gallery"): the group offers its blank answers together, and the reason filter
        # lists them.
        expect(page.locator("[data-review-gallery]")).to_have_text("See all 5 in this group")
        see_blank = page.locator("[data-review-gallery-blank]")
        expect(see_blank).to_have_text("See the 3 possibly blank answers")
        reason = page.get_by_label("Filter by reason", exact=True)
        expect(reason.locator('option[value="blank"]')).to_have_text("Possibly blank")
        reason.select_option(label="Possibly blank")
        expect(page.locator("[data-review-member]")).to_have_count(3)
        assert page.locator("[data-review-member]").evaluate_all("nodes => nodes.map(node => node.dataset.reviewMember)") == [*blanks, misaligned]
        expect(page.locator("[data-workspace-members] > p")).to_contain_text("3 responses shown in this group")
        expect(page.locator('[data-review-member][aria-current="true"]')).to_have_attribute("data-review-member", blanks[0])
        expect(page.locator(".grading-review-form")).to_be_visible()
        reason.select_option(label="All reasons")
        expect(page.locator("[data-review-member]")).to_have_count(5)
        expect(page.locator('[data-review-member][aria-current="true"]')).to_have_attribute("data-review-member", earlier)
        expect(page.locator(".grading-review-form")).to_be_visible()

        # The gallery's blank view: only the blank answers, each its original crop alone, no models' reasons (requirement, 2026-10-17).
        def open_blank_gallery():
            with page.expect_request(lambda request: "/proposal-gallery?" in request.url) as listed:
                see_blank.click()
            query = parse_qs(urlsplit(listed.value.url).query)
            assert (query["only"], query["group"], query["question_id"]) == (["blank"], [group["id"]], [case.qid]), query
            expect(page.locator(".grading-gallery [data-gallery-member]")).to_have_count(3)
        open_blank_gallery()
        gallery = page.locator(".grading-gallery")
        expect(page.locator(".grading-review-form")).to_have_count(0)
        expect(gallery.locator("h4")).to_have_text("3 possibly blank answers in this group")
        expect(page.locator("[data-workspace-status]")).to_have_text("The possibly blank answers of this group. 3 responses.")
        listed = case.frq.proposal_gallery(plan, case.qid, group["id"], None, "blank")
        assert [(m["id"], m["eligible"]) for m in listed["members"]] == [(blanks[0], True), (blanks[1], True), (misaligned, False)], listed
        for number, shown in enumerate(listed["members"], 1):
            tile = gallery.locator(f'[data-gallery-member="{shown["id"]}"]')
            expect(tile.locator("figcaption strong")).to_have_text(f"Response {number}")
            assert len(shown["images"]) == len(shown["contrast"]) == 1 and shown["images"] != shown["contrast"], shown
            assert tile.locator("img").evaluate_all("nodes => nodes.map(node => node.getAttribute('src'))") == shown["images"]
            expect(tile.get_by_role("button", name=f"Enlarge Response {number} handwriting", exact=True)).to_have_count(1)
            expect(tile.get_by_role("button", name=f"Enlarge Response {number} in high contrast", exact=True)).to_have_count(0)
            expect(tile.locator(".grading-gallery-reasons")).to_have_count(0)
            expect(tile.get_by_role("button", name="Open alone", exact=True)).to_be_visible()
        page.wait_for_function("() => [...document.querySelectorAll('.grading-gallery [data-gallery-member] img')].every(img => img.complete && img.naturalWidth > 0)")
        # More than the empty reading holds the third: it is shown greyed, says why, and cannot be set aside or confirmed here.
        alone = gallery.locator(f'[data-gallery-member="{misaligned}"]')
        expect(alone).to_have_class(re.compile(r"\bgrading-gallery-alone\b"))
        expect(alone.locator("figcaption")).to_contain_text("The reader found no handwriting on it, but more than that holds it: open it alone.")
        expect(alone.locator("[data-gallery-aside]")).to_have_count(0)
        expect(gallery.locator("[data-gallery-aside]")).to_have_count(2)
        # Its own tick; nothing is confirmed before it, and no keyboard shortcut ticks it or approves (FRONTEND "Keyboard").
        tick = gallery.get_by_label("I looked at every crop shown here", exact=True)
        confirm = gallery.get_by_role("button", name="Confirm 2 blank answers", exact=True)
        expect(tick).not_to_be_checked()
        expect(confirm).to_be_disabled()
        before = len(posted)
        page.keyboard.press("Alt+a")
        page.wait_for_timeout(200)
        assert len(posted) == before, posted[before:]
        expect(tick).not_to_be_checked()
        expect(confirm).to_be_disabled()
        # The tick clears whenever what is shown changes.
        tick.check()
        expect(confirm).to_be_enabled()
        gallery.locator(f'[data-gallery-aside="{blanks[0]}"]').click()
        expect(gallery.locator("[data-gallery-inspected]")).not_to_be_checked()
        expect(gallery.get_by_role("button", name="Confirm 1 blank answer", exact=True)).to_be_disabled()
        gallery.locator(f'[data-gallery-aside="{blanks[0]}"]').click()
        expect(confirm).to_be_disabled()
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): no horizontal overflow at a desktop width.
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"review-blank-gallery-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        page.set_viewport_size({"width": 1500, "height": 1100})
        # The one that must be opened alone opens in the desk; the gallery is one click away again.
        alone.get_by_role("button", name="Open alone", exact=True).click()
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert f"response={misaligned}" in page.url, page.url
        open_blank_gallery()
        assert len(posted) == before, posted[before:]
        # Confirming sends only the blank answers that may be confirmed together, and each gets its own review.
        gallery.get_by_label("I looked at every crop shown here", exact=True).check()
        with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/proposal-gallery/approve")) as approval:
            gallery.get_by_role("button", name="Confirm 2 blank answers", exact=True).click()
        sent = approval.value.post_data_json
        assert (sent["only"], sent["group"], sent["applied"], sent["confirm_review"], sent["inspected_each"]) == ("blank", group["id"], ["R4"], True, True), sent
        assert [r["id"] for r in sent["responses"]] == blanks, sent
        expect(page.locator("#toast")).to_contain_text("2 blank answers confirmed; 1 still to review.")
        with sqlite3.connect(case.path) as db:
            reviews = db.execute("SELECT response_id,status,comment FROM frq_reviews ORDER BY rowid").fetchall()
        assert sorted(reviews) == sorted((response, "approved", CONFIRMED) for response in blanks), reviews
        assert case.frq.review_queue(plan)["waiting"] == 3
        # Back in the desk, on the first response that is the instructor's: one blank answer is left, so no gallery of them.
        expect(page.locator(".grading-review-form")).to_be_visible()
        expect(page.locator('[data-review-member][aria-current="true"]')).to_have_attribute("data-review-member", earlier)
        expect(page.locator("[data-workspace-total]")).to_have_text("3 left")
        expect(page.locator("[data-review-gallery]")).to_have_text("See all 3 in this group")
        expect(page.locator("[data-review-gallery-blank]")).to_have_count(0)
        approve = page.get_by_role("button", name="Approve & next", exact=True)
        graph_follows("0% · 0/5", "40% · 2/5", "2 of 5 final · 3 to review", approve)

        # GRADING (2026-10-11): what the flag review is about to rule on is stepped past like Later. Deciding the two that are the
        # instructor's leaves only that one: it still waits and still counts, and one button brings it back.
        page.get_by_role("button", name="Approve & next", exact=True).click()
        expect(page.locator('[data-review-member][aria-current="true"]')).to_have_attribute("data-review-member", misaligned)
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert f"response={misaligned}" in page.url, page.url
        graph_follows("40% · 2/5", "60% · 3/5", "3 of 5 final · 2 to review", approve)
        page.get_by_role("button", name="Approve & next", exact=True).click()
        work = page.locator(".grading-review-work")
        expect(work.locator(".empty-state")).to_contain_text("1 response with the flag review")
        expect(work.locator(".empty-state")).to_contain_text("What it cannot settle comes back here; they still count as work to review.")
        expect(page.locator(".grading-review-form")).to_have_count(0)
        expect(member(fresh)).to_have_text("With the flag review")
        expect(page.locator("[data-workspace-total]")).to_have_text("1 left")
        assert case.frq.review_queue(plan)["waiting"] == 1
        with sqlite3.connect(case.path) as db:
            assert db.execute("SELECT COUNT(*) FROM frq_reviews WHERE response_id=?", (fresh,)).fetchone()[0] == 0
        # The last of the instructor's own: the graph says 4 of 5 with the empty state's button still in focus, as a reload would.
        graph_follows("60% · 3/5", "80% · 4/5", "4 of 5 final · 1 to review", work.locator("[data-review-ruled]"))
        page.unroute(the_plan)
        # The row keeps itself current while a review is due, one read every 15 s and no reload: the earlier grade was just decided,
        # so the one left is all a review could be sent.
        expect(row).to_contain_text("gamma-vision can rule on 1 flagged grade in 1 question; anything it cannot settle stays with you. It starts by itself about two minutes after the batch; the button starts it now.", timeout=25000)
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): no horizontal overflow at a desktop width.
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"review-with-flag-review-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        page.set_viewport_size({"width": 1500, "height": 1100})
        ruled = work.locator("[data-review-ruled]")
        expect(ruled).to_have_text("Review them yourself")
        ruled.click()
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert f"response={fresh}" in page.url, page.url
        expect(member(fresh)).to_have_text("Grading check")

        # The review starts by itself: the row turns into its progress on the next read, with nothing pressed.
        assert case.starts_by_itself() == [False, True, False]
        started = case.frq.flag_resolution(plan)
        assert (started["resolution"]["status"], started["resolution"]["automatic"], started["resolution"]["response_ids"], started["automatic"]["due"]["responses"]) == ("pending", True, [fresh], 0), started
        expect(row).to_contain_text("Reviewing 1 flagged grade in 1 question · gamma-vision: 0 replies received. Queued to start. Additional checks may follow. Grades update when the review finishes.", timeout=25000)
        expect(row).to_have_attribute("role", "status")
        expect(row.get_by_role("button", name="Stop", exact=True)).to_be_visible()
        expect(slot.locator("[data-flag-review-start]")).to_have_count(0)
        assert page.evaluate("window.__sameDocument") is True
        assert not [url for url in posted if url.endswith("/flag-resolution")], posted
        # While a review was due and none ran, the next read came 15 s later, never sooner (a running one is read every 5 s).
        reads = [(request.timing["startTime"], request.response().json()) for request in flag_reads]
        waits = [later - sooner for (sooner, answer), (later, _) in zip(reads, reads[1:]) if answer["automatic"]["due"]["responses"] and not answer["resolution"]]
        assert len(waits) >= 2 and all(14000 <= wait < 25000 for wait in waits), (waits, [start for start, _ in reads])
        # The response on screen stays open; entering Review again, the one being ruled on is labelled from the running review.
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert f"response={fresh}" in page.url, page.url
        page.reload()
        expect(row).to_contain_text("Reviewing 1 flagged grade in 1 question")
        expect(member(fresh)).to_have_text("With the flag review")
        expect(fraction).to_have_text("80% · 4/5")  # what the graph said before the reload is what the server says
        page.wait_for_load_state("networkidle")
        # The address named this response, so it stays open although the flag review is ruling on it.
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert f"response={fresh}" in page.url, page.url
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): no horizontal overflow at a desktop width.
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"review-flag-review-running-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        page.set_viewport_size({"width": 1500, "height": 1100})

        # ENTERING REVIEW (requirement, 2026-10-11). Opened without naming a response, Review chooses the one
        # to show (here the place this tab remembers) before the flag review's read answers. That read names it and nothing was done
        # with it: Review moves on, here to the state that says where the response is, and the address stops naming it.
        page.goto(f"{address}/#grading/{plan}?tab=review")
        expect(work.locator(".empty-state")).to_contain_text("1 response with the flag review")
        expect(page.locator(".grading-review-form")).to_have_count(0)
        expect(member(fresh)).to_have_text("With the flag review")
        expect(page.locator("[data-workspace-total]")).to_have_text("1 left")
        assert "response=" not in page.url, page.url
        # The same entry with that read held back until the instructor has done something in the workspace: the response is
        # labelled when the read comes in, and stays open. The read is only delayed, never replaced.
        held = []
        page.route(re.compile(r".*/flag-resolution$"), lambda route: held.append(route), times=1)
        page.reload()
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert f"response={fresh}" in page.url, page.url
        expect(member(fresh)).to_have_text("Grading check")  # nothing says yet that the flag review has it
        page.locator(".grading-review-notes summary").first.click()
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        assert len(held) == 1, held
        held[0].continue_()
        expect(member(fresh)).to_have_text("With the flag review")
        expect(row).to_contain_text("Reviewing 1 flagged grade in 1 question")
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert f"response={fresh}" in page.url, page.url
        with sqlite3.connect(case.path) as db:  # stepping past decided nothing
            assert db.execute("SELECT COUNT(*) FROM frq_reviews WHERE response_id=?", (fresh,)).fetchone()[0] == 0
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Review blank answers and automatic flag review passed: the blank-answer button and reason filter, the gallery's original crop alone per tile, its own tick, no key approving, one review per confirmed blank, the row that says it starts by itself, the response labelled and stepped past, Review them yourself, 15 s reads, the row turning into progress, and the response Review opened on entry left for the flag review unless it was touched or named.")
finally:
    for server in servers:
        server.close()
    for fixture in fixtures:
        fixture.doCleanups()
