"""Grade step: reset a finished batch, see its responses become new work, and put the proposals back."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_batch_resets import BatchResetTests

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = BatchResetTests("test_a_reset_is_put_back_while_nothing_newer_exists")
fixture.setUp()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    job, _ = fixture.grade_all()
    aid = fixture.aid
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        page.goto(f"{address}/#grading/{aid}?tab=jobs")
        card = page.locator(f'[data-job="{job["id"]}"]')
        expect(card).to_contain_text("2 responses")
        expect(card.locator("[data-batch-reset]")).to_have_count(0)
        card.locator('[data-job-disclosure="run"] > summary').click()
        card.get_by_role("button", name="Reset batch…", exact=True).click()
        dialog = page.locator("#dialog")
        expect(dialog).to_contain_text("2 proposals of this batch will be withdrawn")
        expect(dialog).to_contain_text("Nothing is erased")
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.screenshot(path=str(OUTPUT / "batch-reset-dialog-1500.png"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        # The tick is a courtesy; the server has its own confirmation and the exact preview.
        dialog.get_by_role("button", name="Reset batch", exact=True).click()
        expect(dialog.locator("[role=alert]")).to_contain_text("Tick the box to reset this batch")
        assert fixture.states() == ["proposed", "proposed"] or "pending" not in fixture.states(), fixture.states()
        dialog.get_by_label("Withdraw these proposals so I can grade the responses again").check()
        dialog.get_by_role("button", name="Reset batch", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.locator("#toast")).to_contain_text("Batch reset")
        notice = page.locator(f'[data-job="{job["id"]}"] [data-batch-reset]')
        expect(notice).to_contain_text("2 proposals withdrawn so the responses can be graded again")
        expect(notice).to_contain_text("2 responses still wait for a new batch")
        expect(page.locator(f'[data-job="{job["id"]}"]').get_by_role("button", name="Reset batch…", exact=True)).to_have_count(0)  # Nothing left to withdraw.
        assert fixture.states() == ["pending", "pending"], fixture.states()
        page.screenshot(path=str(OUTPUT / "batch-reset-notice-1500.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        # Review has nothing of them.
        page.goto(f"{address}/#grading/{aid}?tab=review")
        expect(page.locator(".grading-heading .section-lead")).to_contain_text("No graded response is waiting for your review")
        page.goto(f"{address}/#grading/{aid}?tab=jobs")
        # The way back, while nothing newer exists.
        notice.get_by_role("button", name="Put the proposals back", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("2 proposals put back")
        expect(notice).to_contain_text("then restored: 2 proposals put back")
        assert "pending" not in fixture.states(), fixture.states()
        page.goto(f"{address}/#grading/{aid}?tab=review")
        expect(page.locator(".grading-heading .section-lead")).to_contain_text("2 responses wait for your review")
        # Reset once more and grade again from the dialog. It checks eligibility by itself: seen live, a Start button that
        # stayed disabled under a busy cursor until "Find eligible responses" was clicked read as a frozen page.
        page.goto(f"{address}/#grading/{aid}?tab=jobs")
        card.locator('[data-job-disclosure="run"] > summary').click()
        card.get_by_role("button", name="Reset batch…", exact=True).click()
        # A closed dialog keeps its last form, ticked box included, until the next one replaces it: wait for the fresh one.
        tick = dialog.get_by_label("Withdraw these proposals so I can grade the responses again")
        expect(dialog).to_be_visible()
        expect(tick).not_to_be_checked()
        tick.check()
        dialog.get_by_role("button", name="Reset batch", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(notice).to_contain_text("2 responses still wait for a new batch")
        notice.get_by_role("button", name="Grade them again", exact=True).click()
        expect(dialog.get_by_label("Responses", exact=True)).to_have_value("new")
        expect(dialog.get_by_label("Responses", exact=True).locator("option").first).to_have_text("New, reset or changed since grading")
        start = dialog.get_by_role("button", name="Start grading batch", exact=True)
        expect(dialog.locator("[data-batch-preview]")).to_contain_text("2 responses eligible")  # Nothing was clicked for this.
        expect(start).to_be_enabled()
        dialog.get_by_label("Question", exact=True).select_option(index=1)  # A change looks again by itself too.
        expect(dialog.locator("[data-batch-preview]")).to_contain_text("2 responses eligible")
        expect(start).to_be_enabled()
        start.click()
        expect(page.locator("#toast")).to_contain_text("Grading batch queued")
        for _ in range(60):
            if not fixture.frq.process_one():
                break
        assert fixture.states() == ["proposed", "proposed"] or "pending" not in fixture.states(), fixture.states()
        jobs = {r["id"]: fixture.frq.response(r["id"])["task"]["job_id"] for r in fixture.frq.responses(aid, fixture.qid)}
        assert job["id"] not in jobs.values() and len(set(jobs.values())) == 1, jobs  # Both responses now hold the new batch's proposals.
        page.locator('#grading-section [data-grading-action="refresh"]').click()
        expect(page.locator("[data-job]")).to_have_count(2)
        page.locator('.grading-batch-history > summary').click()
        expect(notice).to_contain_text("All of them have been graded again since")
        expect(notice.get_by_role("button")).to_have_count(0)
        # With nothing left to grade, Start says why it cannot be used, under an ordinary cursor.
        page.locator('#grading-section [data-grading-action="new-job"]').click()
        expect(dialog.locator("[data-batch-preview]")).to_contain_text("Every response was already graded (2).")
        expect(start).to_be_disabled()
        expect(start).to_have_attribute("title", "Every response was already graded (2). To grade them again, choose “All eligible, including regrading” under Responses.")
        expect(dialog.get_by_role("button", name="Grade them again instead", exact=True)).to_be_visible()
        assert start.evaluate("node => getComputedStyle(node).cursor") == "not-allowed"
        page.screenshot(path=str(OUTPUT / "batch-dialog-nothing-eligible.png"))
        dialog.get_by_role("button", name="Close", exact=True).click()
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Batch reset browser passed: confirmation with counts, server-side control, reset notice, restore, a rerun started from a dialog that checks eligibility by itself, the disabled Start explained, no sideways scroll on the desktop.")
except Exception:
    print("Browser errors:", errors if "errors" in globals() else [])
    print("HTTP errors:", bad if "bad" in globals() else [])
    if "dialog" in globals():
        try:
            print("Dialog says:", dialog.locator("[role=alert]").all_inner_texts())
        except Exception:
            pass
    raise
finally:
    server.close()
    fixture.doCleanups()
