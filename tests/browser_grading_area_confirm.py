"""Answer areas: flip through the questions, confirm the one whose check needs review in its dialog, then Confirm all for the
rest in one click. The flagged question is never part of Confirm all, and the next question needing its area opens after a
confirmation."""
import sys
import threading
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_locations import LocationWorkflow
from test_gradescope_rubrics import encode, export_rows

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = LocationWorkflow("test_automatic_discovery_after_template_upload_gates_grading_and_keeps_draft")
fixture.setUp()
for number in ("2", "3"):
    fixture.frq.upload_rubric(fixture.plan["id"], f"1_Question_{number}.csv", encode(export_rows(question=int(number))).encode(), {"request_id": str(uuid.uuid4())})
# Question 2's printed total (6) is not its rubric's maximum (10); questions 1 and 3 match.
fixture.reader.extra = [("2", 6), ("3", 10)]
fixture.upload()


def questions():
    return {q["location_check"]["context"]["printed_number"]: q for q in fixture.frq.assignment(fixture.plan["id"])["questions"]}


server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    before = fixture.finish_checks()
    assert [before[n]["location_check"]["status"] for n in "123"] == ["needs_confirmation", "needs_review", "needs_confirmation"], before
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=locations")
        panel = {n: page.locator(f'[data-rubric-question="{before[n]["question_id"]}"]') for n in "123"}
        expect(panel["1"]).to_be_visible()
        expect(panel["1"].locator(".grading-highlight")).to_have_count(1)
        # Confirm N answer areas and what it covers sit ONCE in the step header, never in each question's card (2026-09-26 overhaul).
        header = page.locator('[data-step-header="locations"]')
        expect(header.get_by_role("heading", name="Answer areas", exact=True)).to_be_visible()
        expect(header.locator("[data-step-lead]")).to_have_text("0 of 3 answer areas confirmed.")  # One sentence (review fix GS-D4).
        expect(header.get_by_role("button", name="Confirm 2 answer areas", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Confirm 2 answer areas", exact=True)).to_have_count(1)
        expect(header.locator("[data-location-all-summary]")).to_contain_text("Not included: " + before["2"]["content"]["title"])
        expect(panel["1"].locator("[data-location-all-summary]")).to_have_count(0)
        expect(panel["1"]).to_contain_text("Printed total read")
        page.get_by_role("button", name="Next question →", exact=True).click()
        expect(panel["2"]).to_be_visible()
        # The flagged question is confirmed on its own, with the manual review and its explanation.
        panel["2"].get_by_role("button", name="Review answer area", exact=True).click()
        dialog = page.locator("#dialog")
        expect(dialog).to_contain_text("printed total is 6; the rubric maximum is 10")
        # The required tick and the refusal sit in the footer beside Confirm (grading-setup-4): confirming without the tick shows
        # the refusal there, in view, without scrolling the window.
        footer = dialog.locator(".dialog-footer")
        expect(footer.get_by_label("I checked every part and continuation page: I inspected and confirm every answer area, including all parts and continuation pages")).to_be_visible()
        expect(footer).to_contain_text("I checked every part and continuation page")
        dialog.get_by_label("I manually reviewed and resolved the failed question and point checks").check()
        dialog.get_by_label("Required explanation of the manual resolution", exact=True).fill("The printed total is a typo; the coordinator confirmed ten points.")
        dialog.get_by_role("button", name="Confirm answer area", exact=True).click()
        expect(footer.locator(".form-error")).to_contain_text("I checked every part and continuation page")
        expect(footer.locator(".form-error")).to_be_in_viewport()
        dialog.get_by_label("I checked every part and continuation page: I inspected and confirm every answer area, including all parts and continuation pages").check()
        dialog.get_by_role("button", name="Confirm answer area", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.locator("#toast")).to_contain_text("answer area confirmed. 2 still need confirming")
        expect(panel["3"]).to_be_visible()
        assert questions()["2"]["location_check"]["label"] == "Locations confirmed · points reviewed manually"
        expect(header.get_by_role("button", name="Confirm 2 answer areas", exact=True)).to_be_visible()
        # Nothing is left out any more: the header adds no line; what Confirm all covers is its title (review fix GS-D4).
        expect(header.locator("[data-location-all-summary]")).to_have_count(0)
        expect(header.get_by_role("button", name="Confirm 2 answer areas", exact=True)).to_have_attribute("title", "Confirms the 2 suggested answer areas not yet confirmed whose printed points match, as their previews show them.")
        # A confirmed card says how its printed points were settled instead of "Not available", and drops the instruction.
        expect(panel["2"].locator(".grading-area-facts")).to_contain_text("Printed pointsReviewed manually")
        expect(panel["2"]).not_to_contain_text("Printed total read")
        expect(panel["2"]).not_to_contain_text("Include every part")
        expect(header.locator("[data-step-lead]")).to_contain_text("1 of 3 answer areas confirmed.")
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.screenshot(path=str(OUTPUT / "area-confirm-1500.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        header.get_by_role("button", name="Confirm 2 answer areas", exact=True).click()
        expect(page.locator("#toast")).to_contain_text("2 answer areas confirmed.")
        expect(header.get_by_role("link", name="Continue to Key & Rubric Finalization →")).to_be_visible()
        expect(header.locator("[data-step-lead]")).to_have_text("3 of 3 answer areas confirmed.")
        expect(page.get_by_role("button", name="Confirm 2 answer areas", exact=True)).to_have_count(0)
        expect(page.locator(".grading-setup-back")).to_have_count(0)  # The duplicate "Change Assignment Templates" link is gone.
        # All answer areas: every question's area on the blank pages; choosing one opens its question.
        whole = page.locator("[data-whole-exam]")
        expect(whole.locator("summary")).to_contain_text("All answer areas")
        whole.locator("summary").click()
        areas = whole.locator("[data-whole-question]")
        expect(areas).to_have_count(3)
        expect(whole.locator('[data-whole-question][data-state="confirmed"]')).to_have_count(3)
        for _ in range(100):  # The blank pages load when the disclosure opens.
            if page.evaluate("() => [...document.querySelectorAll('[data-whole-exam] img')].every(img => img.complete && img.naturalHeight > 0)"):
                break
            page.wait_for_timeout(100)
        box = areas.first.bounding_box()
        assert box and box["width"] > 20 and box["height"] > 10, box
        target = before["3"]["question_id"]
        whole.locator(f'[data-whole-question="{target}"]').click()
        expect(panel["3"]).to_be_visible()
        expect(panel["1"]).to_be_hidden()
        page.screenshot(path=str(OUTPUT / "area-confirm-whole-exam.png"), full_page=True)
        after = questions()
        for n in "13":
            assert after[n]["location_check"]["label"] == "Locations confirmed · printed points match", after[n]["location_check"]
            assert [r["region"] for r in after[n]["content"]["regions"]] == [r["region"] for r in before[n]["location_check"]["result"]["regions"]]
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Answer area confirmation browser passed: flagged question confirmed in its dialog and left out of Confirm all, "
              "next question opened, Confirm all for the matching rest, no sideways scroll on the desktop.")
finally:
    server.close()
    fixture.doCleanups()
