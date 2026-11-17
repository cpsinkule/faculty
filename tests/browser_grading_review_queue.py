"""Continuous review keeps version context, pending work, saved places and explicit grade decisions."""
import re
import sqlite3
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_review_queue import ReviewQueueBlindTests, ReviewQueueTests, ReviewQueueVersionTests
from test_grading_answer_groups import BARE, WORKED
from test_grading_pipeline import CORRECT
from frq_fixtures import finalize_rubrics

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixtures, servers = [], []


def serve(fixture):
    fixture.setUp()
    fixtures.append(fixture)
    server = create_server(fixture.app, host="127.0.0.1", port=0)
    servers.append(server)
    threading.Thread(target=server.run, daemon=True).start()
    return f"http://127.0.0.1:{server.effective_port}"


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        # The review desk reads its group from one question's part of the queue (question_id). Answered here first as a server that
        # cuts the queue to the question answers: naming that question, with only it and no count of other work, so a next step
        # taken from such an answer would be wrong and the desk must read the whole queue for that. Without `cut`, as a server
        # that ignores the parameter answers: the whole queue, which serves the next step as it is.
        queue_reads, cut = [], {"on": True}

        def question_queue(route):
            url = route.request.url
            queue_reads.append(urlsplit(url).query)
            query = parse_qs(urlsplit(url).query)
            question = query.get("question_id")
            if not question:
                return route.continue_()
            if not cut["on"]:
                whole = {key: value for key, value in query.items() if key != "question_id"}
                return route.continue_(url=urlsplit(url)._replace(query=urlencode(whole, doseq=True)).geturl())
            response = route.fetch()
            body = response.json()
            body["questions"] = [q for q in body["questions"] if q["question_id"] == question[0]]
            body["waiting"], body["other_waiting"] = sum(q["waiting"] for q in body["questions"]), 0
            body["question_id"] = question[0]
            route.fulfill(response=response, json=body)
        page.route(re.compile(r".*/review-queue(\?.*)?$"), question_queue)
        # A synthetic flag review, only where a step below sets one: the panel's closed line and its polls while grades are scored.
        flag = {"body": None, "reads": 0}

        def flag_resolution(route):
            if flag["body"] is None:
                return route.continue_()
            flag["reads"] += 1
            route.fulfill(json=flag["body"])
        page.route(re.compile(r".*/flag-resolution$"), flag_resolution)

        # GRADING: direct entry keeps version identity, and a question boundary is explicit.
        versions = ReviewQueueVersionTests("test_each_exam_version_keeps_its_own_question_rubric_and_piles")
        address = serve(versions)
        versions.graded()
        plan = versions.plan["id"]
        flag["body"] = {"resolution": None, "history": [], "preview": {"questions": [], "responses": 0, "left_out": [], "model": None, "chunk": 25}}
        page.goto(f"{address}/#grading/{plan}?tab=review")
        # Opening Review lands on the first waiting response in printed order (requirement, 2026-10-03): no overview first.
        expect(page.locator(".grading-review-form")).to_be_visible()
        expect(page.locator("[data-review-heading]")).to_contain_text("Version A")
        assert "response=" in page.url
        expect(page.locator("[data-flag-review]")).to_have_count(0)
        expect(page.locator("[data-workspace-questions] section")).to_have_count(2)
        expect(page.locator("[data-workspace-total]")).to_have_text("2 left")
        expect(page.locator("[data-workspace-progress]")).to_contain_text("2 to review")
        expect(page.locator("[data-review-grade-summary]")).not_to_have_attribute("open", "")
        page.locator("[data-workspace-questions] section").nth(1).locator("a").click()
        expect(page.locator(".grading-review-form")).to_be_visible()
        expect(page.locator("[data-review-heading]")).to_contain_text("Version B")
        expect(page.locator(".grading-breadcrumb [aria-current]")).to_have_text("Item 1")
        expect(page.locator("[data-decision-score]")).to_have_text("8 / 10")
        expect(page.locator("[data-desk-left]")).to_contain_text("1 response left")
        page.wait_for_load_state("networkidle")
        queue_reads.clear()
        page.get_by_role("button", name="Approve & next", exact=True).click()
        expect(page.locator(".grading-review-work .next-step")).to_contain_text("Version A")
        expect(page.locator(".grading-review-work .next-step a")).to_be_focused()
        assert any(f"question_id={versions.qb}" in query for query in queue_reads), queue_reads
        assert any(query == "details=review" for query in queue_reads), queue_reads
        assert not versions.frq.responses(plan, versions.qa)[0]["review_status"]
        # The finished question folds into Completed questions below the workspace.
        completed = page.locator("[data-review-completed]")
        expect(completed).not_to_have_attribute("open", "")
        completed.locator(":scope > summary").click()
        expect(completed.locator("li")).to_contain_text("No review needed")
        expect(completed.locator("li")).to_contain_text("1 reviewed by you")
        completed.locator("li a").click()
        page.get_by_role("link", name="See all responses", exact=True).click()
        expect(page.get_by_label("Question", exact=True)).to_have_value(versions.qb)
        expect(page.locator(".grading-review-form")).to_be_visible()
        flag["body"] = None

        # Shared rubric concerns precede the queue, and exact proposed-line groups remain selectable.
        groups = ReviewQueueTests("test_waiting_responses_are_gathered_by_the_exact_lines_the_ai_proposed")
        address = serve(groups)
        groups.grade([WORKED, BARE], {tuple(WORKED): {"R1"}, tuple(BARE): {"R2", "R3"}},
                     {tuple(WORKED): ["R1 overlaps R2; the rubric does not say whether they stack."], tuple(BARE): ["Unclear whether R2 may be selected together with R1."]})
        plan = groups.plan["id"]
        overview = f"{address}/#grading/{plan}?tab=review"
        page.goto(overview)
        expect(page.locator(".grading-review-form")).to_be_visible()
        # The question's shared rubric concern is shown with it, and marked on its row in the rail.
        expect(page.locator("[data-workspace-questions] li")).to_contain_text("rubric concern")
        shared = page.locator("[data-workspace-concerns]")
        shared.locator(":scope > summary").click()
        expect(shared.locator(".grading-rubric-doubt-list li")).to_have_count(1)
        expect(shared.locator(".grading-rubric-doubt-list .grading-chip")).to_have_text("2 responses")
        expect(shared.get_by_role("link", name="Open the full rubric finalization", exact=True)).to_have_attribute("href", f"#grading/{plan}?tab=answer-key&focus={groups.qid}&open=quality")
        expect(shared.get_by_role("button", name="Regrade 2 affected responses", exact=True)).to_be_visible()
        first_id = page.locator(".grading-review-form").get_attribute("id")
        first_route = page.url
        expect(page.locator('[name="review-group"] option')).to_have_count(2)
        expect(page.locator("[data-workspace-concerns]")).to_be_visible()
        # The reason filter never treats a filtered-out question as complete.
        page.get_by_label("Filter by reason", exact=True).select_option("blind")
        expect(page.locator(".grading-review-work")).to_contain_text("No more responses match this filter")
        expect(page.locator(".grading-review-work")).to_contain_text("2 responses still need review")
        page.get_by_role("button", name="Show all reasons", exact=True).click()
        expect(page.locator(".grading-review-form")).to_have_attribute("id", first_id)
        # Exact line shortcuts reveal evidence without ticking the rubric.
        before = page.locator('.grading-rubric-choice input:checked').count()
        page.locator('[data-review-line]').first.click()
        assert page.locator('.grading-rubric-choice input:checked').count() == before
        expect(page.locator('.grading-item-reason[open]').first).to_be_visible()
        # Same tab resume: entering Review again, and a full reload, come back to the same response.
        page.goto(overview)
        expect(page.locator(".grading-review-form")).to_have_attribute("id", first_id)
        assert page.url.split("#", 1)[1] == first_route.split("#", 1)[1], (page.url, first_route)
        page.reload()
        expect(page.locator(".grading-review-form")).to_have_attribute("id", first_id)
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): no horizontal overflow at a desktop width.
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"review-workspace-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        page.set_viewport_size({"width": 1500, "height": 1100})
        sent = []
        page.on("request", lambda request: sent.append(request.post_data_json) if request.method == "POST" and request.url.endswith("/review") else None)
        # Later is read-only, switches groups, retains the count, and is guarded against unsaved edits.
        page.locator(".grading-review-notes summary").first.click()
        # GRADING "WHO SEES A COMMENT" (2026-11-11): the comment and the adjustment say who reads them; the field's name is its label alone.
        notes = page.locator(".grading-review-notes").first
        expect(notes.locator("label.field [data-seen-by='you']")).to_have_text("Only you see this")
        notes.locator("summary", has_text="Adjust points").click()
        expect(notes.locator("[data-seen-by='adjustment']")).to_have_text("Students see the points on Gradescope, not the reason. Only a line added to every grade shows its wording there.")
        notes.screenshot(path=str(OUTPUT / "review-notes-who-sees.png"))
        page.get_by_label("Comment / manual grade reason", exact=True).fill("Check this later")
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.get_by_role("button", name="Later", exact=True).click()
        expect(page.locator(".grading-review-form")).to_have_attribute("id", first_id)
        assert not sent
        page.get_by_role("button", name="Save as draft", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Saved as a draft")
        expect(page.locator(".grading-review-form")).to_have_attribute("id", first_id)
        writes = len(sent)
        page.get_by_role("button", name="Later", exact=True).click()
        expect(page.locator(".grading-review-form")).not_to_have_attribute("id", first_id)
        expect(page.locator("[data-desk-left]")).to_contain_text("2 responses left")
        assert len(sent) == writes
        assert groups.frq.review_queue(plan)["waiting"] == 2
        page.get_by_role("button", name="Approve & next", exact=True).click()
        expect(page.locator(".grading-review-work")).to_contain_text("1 response left for later")
        assert groups.frq.review_queue(plan)["waiting"] == 1
        page.get_by_role("button", name="Review responses left for later", exact=True).click()
        expect(page.locator(".grading-review-form")).to_have_attribute("id", first_id)
        expect(page.get_by_label("Comment / manual grade reason", exact=True)).to_have_value("Check this later")
        # Leave the last response waiting to exercise the existing flag review and all-responses utilities below.
        page.goto(overview)
        expect(page.locator(".grading-review-form")).to_be_visible()
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): no horizontal overflow at a desktop width.
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"review-queue-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        page.set_viewport_size({"width": 1500, "height": 1100})
        # A flag outside a Cloud team batch: the flag review is one closed line saying why.
        flag["body"] = {"resolution": None, "history": [], "preview": {"questions": [], "responses": 0,
                        "left_out": [{"why": "Outside a Cloud team batch", "responses": 1}], "model": None, "chunk": 25}}
        page.reload()
        flag_line = page.locator(".grading-flag-review-slot [data-flag-review] > details")
        expect(flag_line.locator("summary")).to_have_text("Flag review · nothing it can rule on now")
        expect(flag_line).not_to_have_attribute("open", "")
        # It sits below the batch line and right above the workspace.
        assert page.evaluate("""() => { const slot = document.querySelector('.grading-flag-review-slot'), versions = document.querySelector('.grading-review-workspace');
          return Boolean(slot.compareDocumentPosition(versions) & Node.DOCUMENT_POSITION_FOLLOWING)
            && Boolean(document.querySelector('[data-review-batch]').compareDocumentPosition(slot) & Node.DOCUMENT_POSITION_FOLLOWING); }""")
        # GRADING FLAG REVIEW PROGRESS: polls show actual replies and calls, including more than the old 22-call estimate.
        flag["body"] = {"resolution": {"id": "fr-progress", "status": "running", "model": "chatgpt-gpt-6.1-sol", "questions": 16,
                        "responses": 324, "progress": {"requests": 36, "answered": 29, "running": 7}}, "preview": {}, "history": []}
        page.reload()
        status = page.locator(".grading-flag-review-slot [role=status]")
        expect(status).to_contain_text("29 replies received. 7 requests in progress.")
        expect(status).not_to_contain_text("29 of 36")
        for width in (1440, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"flag-review-progress-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        stop = status.get_by_role("button", name="Stop", exact=True)
        stop.focus()
        flag["body"]["resolution"]["progress"] = {"requests": 36, "answered": 30, "running": 6}
        expect(status).to_contain_text("30 replies received. 6 requests in progress.", timeout=12000)
        expect(stop).to_be_focused()
        flag["body"]["resolution"].update(status="pending", progress={"requests": 37, "answered": 30, "running": 0,
            "wait": {"kind": "usage_limit", "until": "2099-10-26T02:20:00+00:00"}})
        expect(status).to_contain_text("Subscription usage limit reached; retry scheduled for", timeout=12000)
        expect(stop).to_be_focused()
        page.screenshot(path=str(OUTPUT / "flag-review-paused-1280.png"), full_page=True)
        # A finished review whose grades are still being scored polls every 5 s: a ruling the instructor opened stays open, and an
        # unchanged poll writes nothing (grading-review-8).
        response = lambda ref, final: {"ref": ref, "outcome": "adopted", "final": final, "flags": [{"id": "F1", "resolved": True}], "ruling": {"reason": "The work supports the grade."}, "score_before": 4}
        def resolution(final):
            return {"resolution": {"id": "fr1", "status": "complete", "cancelled": False, "updated_at": "2026-10-02T15:00:00+00:00", "model": "Synthetic reviewer",
                                   "questions": 1, "responses": len(final), "progress": {"requests": 1, "answered": 1}, "finalized": final.count("finalized"),
                                   "totals": {"responses": len(final), "adopted": len(final), "revised": 0, "unreliable": 0},
                                   "result": {"questions": [{"question_id": groups.qid, "label": "Question 1", "responses": [response(f"R{i + 1}", f) for i, f in enumerate(final)]}]}},
                    "history": [], "preview": {"questions": [], "responses": 0, "left_out": [], "model": "Synthetic reviewer", "chunk": 25}}
        flag["body"] = resolution(["finalized", "scoring", "scoring"])
        page.reload()
        last = page.locator(".grading-flag-review-slot [data-flag-review] > details")
        expect(last.locator("> summary")).to_contain_text("1 of 3 flagged grades finalized so far")
        expect(last).to_have_attribute("open", "")  # just finished: open while its grades are scored
        page.wait_for_load_state("networkidle")  # the page's second render after its state read is done
        ruling = last.locator("[data-flag-review-question]")
        ruling.locator("summary").click()
        expect(ruling).to_have_attribute("open", "")
        page.evaluate("""() => { window.__flagWrites = 0; window.__flagRuling = document.querySelector('[data-flag-review-question]');
          new MutationObserver(list => { window.__flagWrites += list.length; }).observe(document.querySelector('.grading-flag-review-slot'), {subtree: true, childList: true, characterData: true, attributes: true}); }""")
        reads = flag["reads"]
        for _ in range(140):  # one 5 s poll with the same answer
            if flag["reads"] > reads:
                break
            page.wait_for_timeout(50)
        assert flag["reads"] > reads, flag
        page.wait_for_timeout(300)
        assert page.evaluate("window.__flagWrites") == 0, page.evaluate("window.__flagWrites")
        flag["body"] = resolution(["finalized", "finalized", "scoring"])  # still scoring: patched in place, the ruling stays open
        expect(last.locator("> summary")).to_contain_text("2 of 3 flagged grades finalized so far", timeout=12000)
        expect(ruling).to_have_attribute("open", "")
        assert page.evaluate("window.__flagRuling === document.querySelector('[data-flag-review-question]')")
        flag["body"] = resolution(["finalized", "finalized", "held"])  # scored: the queue is read again for its counts; still open
        expect(last.locator("> summary")).not_to_contain_text("so far", timeout=12000)
        expect(last.locator("> summary")).to_contain_text("2 of 3 flagged grades finalized")
        expect(last).to_have_attribute("open", "")
        expect(ruling).to_have_attribute("open", "")
        flag["body"] = None
        page.goto(f"{address}/#grading/{plan}?tab=review")
        page.get_by_role("link", name="All responses, approved grades and review tools →", exact=True).click()
        expect(page.get_by_label("Show responses", exact=True)).to_be_visible()
        page.get_by_role("link", name="← Review", exact=True).click()
        expect(page.locator(".grading-review-form")).to_be_visible()
        expect(page.locator("[data-workspace-total]")).to_have_text("1 left")

        # GRADING: approving the final member of one group continues directly to the next group's own rubric.
        pairs = ReviewQueueTests("test_waiting_responses_are_gathered_by_the_exact_lines_the_ai_proposed")
        address = serve(pairs)
        pairs.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R3"}})
        page.goto(f"{address}/#grading/{pairs.plan['id']}?tab=review")
        expect(page.locator(".grading-review-form")).to_be_visible()
        old_group = page.get_by_label("Proposed rubric group", exact=True).input_value()
        page.get_by_role("button", name="Approve & next", exact=True).click()
        expect(page.locator("[data-desk-left]")).to_contain_text("1 response left")
        assert page.get_by_label("Proposed rubric group", exact=True).input_value() != old_group
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert "response=" in page.url
        # The last one: the question is done and nothing else waits, so the next step is certification; entering Review again
        # shows that next step in the workspace instead of a response.
        page.get_by_role("button", name="Approve & next", exact=True).click()
        expect(page.locator(".grading-review-work .next-step")).to_contain_text("No responses need review")
        page.goto(f"{address}/#grading/{pairs.plan['id']}?tab=review")
        expect(page.locator(".grading-review-work .next-step")).to_contain_text("No responses need review")
        expect(page.locator(".grading-review-work .next-step a")).to_have_attribute("href", f"#grading/{pairs.plan['id']}?tab=export")
        expect(page.locator(".grading-review-form")).to_have_count(0)
        expect(page.locator("[data-workspace-total]")).to_have_text("0 left")
        assert "response=" not in page.url

        # Arbitrary uploaded IDs retain their displayed rubric numbers, never their suffixes or sort order.
        arbitrary = ReviewQueueTests("test_waiting_responses_are_gathered_by_the_exact_lines_the_ai_proposed")
        address = serve(arbitrary)
        ids = ['full-credit', 'z-first-step', '83fe91', 'R5_11', '7', 'R5_2', 'other.part', 'line-9', 'beta', 'empty', 'a-final-step']
        rules = {'mode': 'positive', 'items': [{'id': item_id, 'description': f'Criterion {i + 1}', 'points': 4 if i == 1 else 1 if i == 10 else 0} for i, item_id in enumerate(ids)]}
        q = arbitrary.frq.assignment(arbitrary.plan['id'])['questions'][0]
        q = arbitrary.frq.import_rubric(arbitrary.plan['id'], arbitrary.qid, {'version': q['version'], 'source_text': 'An unrelated uploaded rubric', 'rules': rules})['questions'][0]
        arbitrary.frq.approve_rubric(arbitrary.plan['id'], arbitrary.qid, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'], 'acknowledge_rules': True})
        finalize_rubrics(arbitrary.frq, arbitrary.plan['id'])
        arbitrary.grade([WORKED, BARE], {tuple(WORKED): {ids[1], ids[10]}, tuple(BARE): {ids[10]}})
        page.goto(f"{address}/#grading/{arbitrary.plan['id']}?tab=review&question={arbitrary.qid}")
        pair = page.locator('a.grading-review-group').filter(has_text='Criterion 2')
        expect(pair.locator('.grading-rubric-number')).to_have_text(['2.', '11.'])
        pair.click()
        expect(page.locator('.grading-breadcrumb [aria-current]')).to_have_text('Items 2 + 11')
        expect(page.locator('.grading-rubric-choice .grading-rubric-number')).to_have_text([f'{i}.' for i in range(1, 12)])
        page.locator(f'.grading-rubric-choice input[value="{ids[1]}"]').uncheck()
        page.get_by_role('button', name='Approve & next', exact=True).click()
        expect(page.locator('[data-desk-left]')).to_contain_text('1 response left')
        assert sent[-1]['selected_items'] == [ids[10]], sent

        # Blind work reveals neither proposed lines nor reason categories before the instructor's own decision.
        hidden = ReviewQueueBlindTests("test_a_proposal_hidden_for_a_blind_review_is_never_sorted_by_its_lines")
        address = serve(hidden)
        hidden.configure_acceptance(blind_share=1.0)
        hidden.grade(CORRECT)
        page.goto(f"{address}/#grading/{hidden.plan['id']}?tab=review")
        expect(page.locator('.grading-review-form')).to_be_visible()
        expect(page.locator('.grading-rubric-choice input:checked')).to_have_count(0)
        expect(page.locator('[data-review-line]')).to_have_count(0)
        expect(page.locator('[data-workspace-concerns]')).to_have_count(0)
        expect(page.locator('[data-workspace-members]')).to_contain_text('Blind review')
        expect(page.locator('.grading-review-scope')).not_to_contain_text('points')

        # Batch isolation survives entry, reload, scoped saves, and the boundary to other batches.
        scoped = ReviewQueueTests("test_batch_review_never_borrows_flags_from_an_earlier_attempt_or_another_response")
        address = serve(scoped)
        scoped.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R2"}},
                     {tuple(WORKED): ["Unclear rubric wording"], tuple(BARE): ["Unclear rubric wording"]})
        batch = scoped.regrade_one()
        for _ in range(60):
            if not scoped.frq.process_one():
                break
        page.goto(f"{address}/#grading/{scoped.plan['id']}?tab=review&job={batch['id']}")
        expect(page.locator('[data-workspace-total]')).to_have_text('1 left')
        expect(page.locator('.grading-flag-review-slot')).to_have_count(0)  # assignment-wide only
        page.reload()
        expect(page.locator('[data-desk-left]')).to_contain_text('1 response left')
        assert f'job={batch["id"]}' in page.url
        page.wait_for_load_state('networkidle')
        queue_reads.clear()
        page.get_by_role('button', name='Approve & next', exact=True).click()
        expect(page.locator('.grading-review-work .next-step')).to_contain_text('Other batches need review')
        assert all(f'job_id={batch["id"]}' in query for query in queue_reads), queue_reads
        assert any('question_id=' in query for query in queue_reads), queue_reads
        expect(page.locator('.grading-review-work .next-step a')).to_have_attribute('href', f'#grading/{scoped.plan["id"]}?tab=review')
        assert scoped.frq.review_queue(scoped.plan['id'])['waiting'] == 1

        # GRADING (2026-10-03): a shared rubric concern is settled beside the desk, then only the responses that raised it are graded
        # again as an ordinary batch. Nothing is approved by either step.
        settle = ReviewQueueTests("test_waiting_responses_are_gathered_by_the_exact_lines_the_ai_proposed")
        address = serve(settle)
        settle.grade([WORKED, BARE], {tuple(WORKED): {"R1"}, tuple(BARE): {"R2", "R3"}},
                     {tuple(WORKED): ["R1 overlaps R2; the rubric does not say whether they stack."]})
        plan = settle.plan["id"]
        doubted = sorted(m["id"] for q in settle.frq.review_queue(plan, review_details=True)["questions"] for g in q["groups"] for m in g["members"] if "rubric" in m.get("reason_kinds", []))
        assert len(doubted) == 1, doubted
        before = settle.frq.rubric_quality(plan)["review"]["id"]
        page.goto(f"{address}/#grading/{plan}?tab=review")
        expect(page.locator(".grading-review-form")).to_be_visible()
        concerns = page.locator("[data-workspace-concerns]")
        concerns.locator(":scope > summary").click()
        concerns.get_by_role("button", name="Clarify the rule here", exact=True).click()
        editor = concerns.locator("[data-concern-editor]")
        expect(editor).to_be_visible()
        # Only the lines the concern names, not the whole rubric.
        named = sorted({item for q in settle.frq.review_queue(plan)["questions"] for d in q["rubric_doubts"] for item in d["items"]})
        expect(editor.locator("[data-concern-line]")).to_have_count(len(named))
        line = named[0]
        editor.locator(f'[data-concern-line="{line}"]').fill("R1 and R2 stack only when both steps are written.")
        editor.get_by_role("button", name="Save the rule", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("Rule saved for this question.")
        after = settle.frq.rubric_quality(plan)["review"]
        assert after["id"] != before and after["policy"]["questions"][0]["clarifications"][line] == "R1 and R2 stack only when both steps are written.", after
        jobs_before = {j["id"] for j in settle.frq.overview()["jobs"]}
        concerns.get_by_role("button", name="Regrade 1 affected response", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("1 response sent to be graded again")
        new_jobs = [j for j in settle.frq.overview()["jobs"] if j["id"] not in jobs_before]
        assert len(new_jobs) == 1, new_jobs
        assert sorted(t["response_id"] for t in settle.frq.job(new_jobs[0]["id"])["tasks"]) == doubted
        with sqlite3.connect(settle.path) as db:
            assert db.execute("SELECT COUNT(*) FROM frq_reviews").fetchone()[0] == 0

        # GRADING FLAG REVIEW (prompt v3, 2026-10-03): a response the flag review left with the instructor shows the mark to look at and one
        # choice per reading; a choice ticks exactly its lines and approves through the ordinary review route.
        readings_case = ReviewQueueTests("test_waiting_responses_are_gathered_by_the_exact_lines_the_ai_proposed")
        address = serve(readings_case)
        readings_case.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R3"}})
        plan = readings_case.plan["id"]
        note = {"reason": "Look at the last digit on the answer line: 6 or 8.", "at": "2026-10-03T00:00:00+00:00",
                "readings": [{"reading": "the digit is 6", "lines": ["R1"], "score": 10, "max_score": 10}, {"reading": "the digit is 8", "lines": ["R3"], "score": 2, "max_score": 10},
                             # Two marks in doubt give four choices (live, 2026-11-14: only the first two showed, both "(x-3)").
                             {"reading": "the digit is 6; the symbol before 12e^{3x} at the right end of the line is a minus", "lines": ["R2"], "score": 6, "max_score": 10},
                             {"reading": "the digit is 8; the symbol before 12e^{3x} at the right end of the line is '='", "lines": ["R2", "R3"], "score": 0, "max_score": 10}],
                # GRADING FLAG REVIEW "THE SPOT TO LOOK AT" (2026-11-14): the marks to read, outlined on the crop.
                "look": [{"label": "last digit on the answer line", "crop": 0, "bbox": [0.25, 0.5, 0.5, 0.75]}]}
        def with_note(route):
            response = route.fetch()
            body = response.json()
            route.fulfill(response=response, json={**body, "flag_review": note})
        page.route(re.compile(r".*/api/grading/responses/[^/?]+\?details=review$"), with_note)
        page.goto(f"{address}/#grading/{plan}?tab=review")
        readings = page.locator("[data-flag-readings]")
        expect(readings).to_contain_text("Look at the last digit on the answer line: 6 or 8.")
        expect(readings.locator("[data-flag-reading]")).to_have_count(4)
        # EVERY choice is on the page (requirement, 2026-11-14). On a short, narrow window too.
        page.set_viewport_size({"width": 1300, "height": 700})
        hidden = """panel => { const box = panel.getBoundingClientRect();
            return [...panel.querySelectorAll("[data-flag-reading]")].filter(b => { const r = b.getBoundingClientRect(); return r.bottom > box.bottom + 1 || r.top < box.top - 1; }).length; }"""
        assert page.locator(".grading-review-reasons").evaluate(hidden) == 0
        page.set_viewport_size({"width": 1500, "height": 1100})
        # The spot is outlined on its crop where the review said, grown 1.2% of the crop each way, with nothing filled in; it is shown
        # enlarged beside the choices (requirement, 2026-11-14), that view
        # scrolls to the outline, and the outline comes along to the crop's full-size view.
        spot = page.locator('.grading-image[data-crop="0"] .grading-flag-spot')
        expect(spot).to_have_count(1)
        expect(spot).to_have_attribute("aria-label", "Outlined to read: last digit on the answer line")
        placed = """node => { const s = node.getBoundingClientRect(), i = node.parentElement.querySelector("img").getBoundingClientRect();
            return [(s.left - i.left) / i.width, (s.top - i.top) / i.height, s.width / i.width, s.height / i.height,
                    getComputedStyle(node).backgroundColor, getComputedStyle(node).pointerEvents]; }"""
        page.locator('.grading-image[data-crop="0"] img').evaluate("img => img.complete && img.naturalWidth > 0 || new Promise(done => { img.onload = done; })")
        def on_the_marks(values):
            assert all(abs(got - want) < 0.01 for got, want in zip(values[:4], (0.238, 0.488, 0.274, 0.274))), values
            assert values[4] in ("rgba(0, 0, 0, 0)", "transparent") and values[5] == "none", values
        on_the_marks(spot.evaluate(placed))
        view = readings.locator("[data-flag-views] [data-flag-view]")
        expect(view).to_have_count(1)
        expect(view).to_have_attribute("data-placed", "true")
        expect(readings.locator(".grading-flag-view figcaption")).to_have_text("last digit on the answer line")
        # The view shows the part of the scan from 10% to 65% across and 40% to 85% down (the box and what surrounds it), undistorted,
        # with the outline on the same marks; it sits beside the choices, not above them.
        shown = view.evaluate("""button => { const b = button.getBoundingClientRect(), i = button.querySelector("img").getBoundingClientRect(),
            m = button.querySelector(".grading-flag-view-mark").getBoundingClientRect(), c = document.querySelector(".grading-flag-choices").getBoundingClientRect();
            return [(b.left - i.left) / i.width, (b.top - i.top) / i.height, b.width / i.width, b.height / i.height,
                    (m.left - b.left) / b.width, (m.top - b.top) / b.height, m.width / b.width, m.height / b.height, c.left >= b.right, b.height]; }""")
        assert all(abs(got - want) < 0.012 for got, want in zip(shown[:8], (0.10, 0.40, 0.55, 0.45, 0.251, 0.196, 0.498, 0.609))), shown
        assert shown[8] is True and 40 < shown[9] <= 9 * 16 + 2, shown
        view.click()
        expect(spot).to_be_in_viewport()
        # THE REST OF THE DESK KEEPS ITS SIZE (requirement, 2026-11-14): the note shown whole, views and all, takes nothing from the paper and the rubric. In a window like the
        # instructor's and in a short one, their box is as tall as under an ordinary note (70dvh less 7rem) or taller, the rubric's
        # lines are in it, and the choices stay beside the views.
        for width, height in ((1000, 900), (1300, 700), (1500, 1100)):
            page.set_viewport_size({"width": width, "height": height})
            sizes = page.evaluate("""() => { const r = e => document.querySelector(e).getBoundingClientRect(), layout = r(".grading-evidence-layout"),
                line = r(".grading-rubric-items .grading-rubric-choice"), views = r(".grading-flag-views"), choices = r(".grading-flag-choices"), note = r(".grading-review-reasons");
                return [layout.height, r(".grading-review-scroll").height, r(".grading-evidence").height, line.top >= layout.top && line.bottom <= layout.bottom,
                        choices.left >= views.right && choices.top < views.bottom, layout.top >= note.bottom]; }""")
            assert sizes[0] >= 0.7 * height - 7 * 16 - 2 and sizes[1] > 120 and sizes[2] > 300, (width, height, sizes)
            assert sizes[3] is True and sizes[4] is True and sizes[5] is True, (width, height, sizes)
        page.set_viewport_size({"width": 1500, "height": 1100})
        page.locator('.grading-image[data-crop="0"] .grading-crop-open').click()
        enlarged = page.locator(".grading-crop-dialog .grading-crop-frame .grading-flag-spot")
        expect(enlarged).to_have_count(1)
        page.locator(".grading-crop-dialog img").evaluate("img => img.complete && img.naturalWidth > 0 || new Promise(done => { img.onload = done; })")
        on_the_marks(enlarged.evaluate(placed))
        page.locator(".grading-crop-dialog [data-close]").click()
        expect(page.locator(".grading-crop-dialog")).to_have_count(0)
        with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/review")) as posted:
            readings.locator('[data-flag-reading="1"]').click()  # "the digit is 8" alone: the fourth choice also begins with those words
        assert posted.value.post_data_json["selected_items"] == ["R3"] and posted.value.post_data_json["status"] == "approved", posted.value.post_data_json
        expect(page.locator("#toast")).to_contain_text("pproved")
        page.unroute(re.compile(r".*/api/grading/responses/[^/?]+\?details=review$"), with_note)
        assert readings_case.frq.review_queue(plan)["waiting"] == 1

        # GRADING (2026-10-03): a proposed-line group's responses side by side; the rest approved after looking at each, one review
        # each. No key approves or ticks; the tick clears when what is shown changes; a response set aside is reviewed alone next.
        gallery_case = ReviewQueueTests("test_waiting_responses_are_gathered_by_the_exact_lines_the_ai_proposed")
        address = serve(gallery_case)
        gallery_case.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R2"}})
        plan = gallery_case.plan["id"]
        page.goto(f"{address}/#grading/{plan}?tab=review")
        expect(page.locator(".grading-review-form")).to_be_visible()
        posted = []
        page.on("request", lambda request: posted.append(request.url) if request.method == "POST" else None)
        page.get_by_role("button", name="See all 2 in this group", exact=True).click()
        gallery = page.locator(".grading-gallery")
        expect(gallery.locator("[data-gallery-member]")).to_have_count(2)
        expect(page.locator(".grading-review-form")).to_have_count(0)
        approve = gallery.get_by_role("button", name="Approve 2 responses", exact=True)
        expect(approve).to_be_disabled()
        page.keyboard.press("Alt+a")
        page.wait_for_timeout(200)
        assert not posted, posted
        tick = gallery.locator("[data-gallery-inspected]")
        tick.check()
        expect(approve).to_be_enabled()
        first = gallery.locator("[data-gallery-member]").first.get_attribute("data-gallery-member")
        gallery.locator(f'[data-gallery-aside="{first}"]').click()
        expect(gallery.locator("[data-gallery-inspected]")).not_to_be_checked()
        expect(gallery.get_by_role("button", name="Approve 1 response", exact=True)).to_be_disabled()
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): no horizontal overflow at a desktop width.
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            page.screenshot(path=str(OUTPUT / f"review-gallery-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
        page.set_viewport_size({"width": 1500, "height": 1100})
        gallery.locator("[data-gallery-inspected]").check()
        gallery.get_by_role("button", name="Approve 1 response", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("1 response approved")
        # The one set aside is next, alone in the desk.
        expect(page.locator(".grading-review-form")).to_be_visible()
        assert f"response={first}" in page.url, page.url
        with sqlite3.connect(gallery_case.path) as db:
            assert db.execute("SELECT COUNT(*) FROM frq_reviews WHERE status='approved'").fetchone()[0] == 1
        assert gallery_case.frq.review_queue(plan)["waiting"] == 1
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Review workspace passed: direct entry, rail progress, empty state, concerns settled beside the desk, the proposal gallery, compact queue, exact version/group identity, resume, Later, filters, guarded drafts, continued groups, shared concerns, blind isolation, scoped reads, responsive layout and flag-review polling.")
finally:
    for server in servers:
        server.close()
    for fixture in fixtures:
        fixture.doCleanups()
