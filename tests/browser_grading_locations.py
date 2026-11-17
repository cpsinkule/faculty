"""Automatic check, visual correction, required confirmation and manual failure review."""
import re
import sys
import threading
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_locations import LocationWorkflow
from test_gradescope_rubrics import encode, export_rows

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
HIDE = """() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
  document.dispatchEvent(new Event('visibilitychange')); }"""
SHOW = """() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
  document.dispatchEvent(new Event('visibilitychange')); }"""
fixture = LocationWorkflow("test_automatic_discovery_after_template_upload_gates_grading_and_keeps_draft")
fixture.setUp()
fixture.upload()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        reads = Counter()
        page.on("request", lambda request: reads.update([urlsplit(request.url).path]))
        plan_path = f"/api/grading/assignments/{fixture.plan['id']}"
        page.goto(address+f"/#grading/{fixture.plan['id']}?tab=locations")
        expect(page.locator("[data-location-check]")).to_contain_text("automatically")
        # While the check waits, the page looks at the local work (a cheap read) and reads the whole assignment only when that
        # changes (or every 15 s): the worker starting on the blank exam shows. A hidden tab reads nothing, and reads the assignment
        # at once when shown again.
        fixture.frq.process_location_one()
        expect(page.locator("[data-location-check]")).to_contain_text("0 of 2 pages read", timeout=15000)
        reads.clear()
        page.wait_for_timeout(7000)
        assert reads[plan_path] == 0 and reads[plan_path + "/local-work"] >= 2, reads
        page.evaluate(HIDE)
        page.wait_for_timeout(500)
        reads.clear()
        page.wait_for_timeout(6000)
        assert not reads, reads
        with page.expect_request(lambda request: urlsplit(request.url).path == plan_path):
            page.evaluate(SHOW)
        # A whole read that fails is tried again at the next 3 s look, not at the 15 s one: the error does not stay up.
        failed = []
        whole = re.compile(re.escape(address + plan_path) + "$")
        page.route(whole, lambda route: route.continue_() if failed else (failed.append(route.request.url), route.fulfill(status=503, json={"error": "Busy for a moment."})))
        page.evaluate(HIDE)
        page.wait_for_timeout(3500)
        page.evaluate(SHOW)
        expect(page.locator("[data-location-check] [role=status]")).to_contain_text("Verification status could not refresh")
        with page.expect_request(lambda request: urlsplit(request.url).path == plan_path, timeout=6000):
            pass
        expect(page.locator("[data-location-check]")).not_to_contain_text("could not refresh")
        page.unroute(whole)
        bad[:] = [entry for entry in bad if entry != (503, address + plan_path)]
        errors[:] = [error for error in errors if "status of 503" not in error]
        fixture.finish_check()
        expect(page.get_by_role("button", name="Review answer area", exact=True)).to_be_visible(timeout=15000)
        page.get_by_role("button", name="Review answer area", exact=True).click()
        expect(page.locator("#dialog")).to_contain_text("Printed reading: 10 points")
        expect(page.locator("[data-location-page='1'] .grading-highlight")).to_have_count(1)
        expect(page.get_by_label("Printed question number", exact=True)).to_have_value("1")
        page.get_by_role("button", name="Add answer area", exact=True).click()
        expect(page.get_by_label("Area to draw", exact=True).locator("option")).to_have_count(2)
        page.locator('[data-region="1"]').get_by_role("button", name="Remove area", exact=True).click()
        expect(page.get_by_label("Area to draw", exact=True).locator("option")).to_have_count(1)
        page.get_by_role("button", name="Confirm answer area", exact=True).click()
        expect(page.locator("#dialog [role=alert]")).to_contain_text("I checked every part and continuation page")
        expect(page.locator("#dialog")).to_be_visible()
        image = page.locator("[data-location-page='1']")
        image.scroll_into_view_if_needed()
        rect = image.bounding_box()
        page.mouse.move(rect["x"]+rect["width"]*.15, rect["y"]+rect["height"]*.2)
        page.mouse.down()
        page.mouse.move(rect["x"]+rect["width"]*.85, rect["y"]+rect["height"]*.7)
        page.mouse.up()
        page.wait_for_function("Math.abs(Number(document.querySelector('[aria-label=\"Left (%)\"]').value) - 15) < .25")
        expect(page.locator("[data-location-page='1'] .grading-highlight")).to_have_css("position", "absolute")
        assert page.locator("[data-location-page='1'] .grading-highlight").bounding_box()["width"] > rect["width"]*.65
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.locator("#dialog").evaluate("node => { node.scrollTop = 0; node.querySelector('.dialog-body').scrollTop = 0; }")
        page.screenshot(path=str(OUTPUT/"locations-1500.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.get_by_label("I checked every part and continuation page: I inspected and confirm every answer area, including all parts and continuation pages").check()
        page.get_by_role("button", name="Confirm answer area", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        expect(page.locator("[data-location-check]")).to_contain_text("Answer area confirmed")
        expect(page.locator("[data-location-check] .grading-area-facts")).to_contain_text("Matched when confirmed")
        confirmed = fixture.question()
        assert abs(confirmed["content"]["regions"][0]["region"]["x"]-.15) < .01
        replacement = fixture.upload("Corrected template")
        page.locator('#grading-section > .grading-heading').get_by_role("button", name="Refresh", exact=True).click()
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=templates")
        page.get_by_label('Template for A', exact=True).select_option(replacement['id'])
        page.get_by_role('button', name='Continue to answer areas').click()
        page.get_by_label('Reset the existing answer areas for the changed templates; I will confirm new areas').check()
        page.get_by_role('button', name='Confirm and continue').click()
        expect(page.get_by_role('heading', name='Answer areas', exact=True)).to_be_visible()
        chosen = fixture.question()
        assert chosen["content"]["regions"] == []
        assert chosen["rubric_id"] == confirmed["rubric_id"]
        assert chosen["location_check"]["status"] == "pending"
        fixture.configure_rubric(.5)
        fixture.reader.points = fixture.reader.ocr_points = 8
        fixture.finish_check()
        page.locator('#grading-section > .grading-heading').get_by_role("button", name="Refresh", exact=True).click()
        page.get_by_role("button", name="Review answer area", exact=True).click()
        expect(page.locator("#dialog")).to_contain_text("printed total is 8; the rubric maximum is 10")
        page.get_by_label("I checked every part and continuation page: I inspected and confirm every answer area, including all parts and continuation pages").check()
        page.locator("#dialog").get_by_label("Required explanation of the manual resolution", exact=True).fill("The printed total is a typo; the coordinator confirmed ten points.")
        page.get_by_role("button", name="Confirm answer area", exact=True).click()
        expect(page.locator("#dialog [role=alert]")).to_contain_text("Confirm your manual review")
        page.locator("#dialog").get_by_label("I manually reviewed and resolved the failed question and point checks").check()
        page.get_by_role("button", name="Confirm answer area", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        expect(page.locator("[data-location-check]")).to_contain_text("Reviewed manually")
        # One template decision covers imported questions that have no areas yet.
        for number in ("18", "19"):
            fixture.frq.upload_rubric(fixture.plan["id"], f"1_Question_{number}.csv", encode(export_rows()).encode(), {"request_id": str(uuid.uuid4())})
        reviewed, *imported = fixture.frq.assignment(fixture.plan["id"])["questions"]
        page.locator('#grading-section > .grading-heading').get_by_role("button", name="Refresh", exact=True).click()
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=templates")
        page.get_by_role('button', name='Continue to answer areas').click()
        page.get_by_role('button', name='Confirm and continue').click()
        expect(page.get_by_role('heading', name='Answer areas', exact=True)).to_be_visible()
        after = fixture.frq.assignment(fixture.plan["id"])["questions"]
        assert [q["content"]["template_id"] for q in after[1:]] == [replacement["id"]]*2
        assert after[0]["question_revision_id"] == reviewed["question_revision_id"]
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Template verification browser passed: automatic status, exact printed points, drawing, required confirmation, template choice reruns OCR, manual mismatch review, no sideways scroll on the desktop.")
finally:
    server.close()
    fixture.doCleanups()
