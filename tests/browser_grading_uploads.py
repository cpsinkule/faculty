"""Upload several rubrics from an empty assessment, then confirm OCR locations."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from grading.models import plain_transcript
from test_grading_uploads import RubricUploads
from test_gradescope_rubrics import EXAMPLES, encode, export_rows
from test_grading_locations import heading, confirm_templates

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = RubricUploads("test_eight_csvs_create_exact_drafts_from_empty_plan")
fixture.setUp()
fixture.template()

def page_questions():
    start = 0 if fixture.reader.reads % 2 else 4
    return [(n+18, EXAMPLES[n][0]) for n in range(start, start+4)]

def recognize(image):
    fixture.reader.reads += 1
    source = "\n".join(f"{n}. ({maximum} points) Solve." for n, maximum in page_questions())
    return {"provider": "glm", "transcript": plain_transcript(source), "raw": {}, "metadata": {"model": "fixture"}}

def locate(payload, images):
    questions = [heading(str(n), f"({maximum} points)", header_bbox=[100,100+200*i,900,150+200*i],
        response_bbox=[100,100+200*i,900,280+200*i]) for i, (n, maximum) in enumerate(page_questions())]
    return {"output": {"questions": questions, "warnings": []}, "raw": {}, "metadata": {"model": "fixture"}}

fixture.reader.recognize = recognize
fixture.reader.locate = locate
files = [{"name": f"{i}_Question_{i+17}.csv", "mimeType": "text/csv", "buffer": encode(export_rows(maximum, points, question=i)).encode()}
    for i, (maximum, points) in enumerate(EXAMPLES, 1)]
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
        # The first step's page also prepares the saved PDF as a template; this fixture has no converter, and that page has its own check.
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" and "400 (BAD REQUEST)" not in message.text
                and not message.location.get("url", "").endswith("/templates/prepare") else None)
        page.on("response", lambda response: bad.append((response.status, response.url)) if response.status >= 500 and not response.url.endswith("/templates/prepare") else None)
        page.goto(address+f"/#grading/{fixture.plan['id']}?tab=rubrics")
        expect(page.locator("[data-rubric-question]")).to_have_count(0)
        page.get_by_role("button", name="Upload rubrics", exact=True).click()
        page.locator('[name="rubric_files"]').set_input_files(files)
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.screenshot(path=str(OUTPUT/"rubric-upload-1500.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.get_by_role("button", name="Upload and continue", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        expect(page.locator("[data-rubric-question]")).to_have_count(8)
        expect(page.locator(".grading-rubric-edit-lines li")).to_have_count(89)  # Every line, editable before the rubric reviewer.
        questions = fixture.uploaded_questions()
        assert [q["content"]["max_points"] for q in questions] == [4,2,2,4,6,8,6,8]
        assert all(q["content"]["regions"] == [] and q["rubric"]["approved_at"] is None for q in questions)
        confirm_templates(fixture.frq, fixture.plan['id'])
        page.locator('#grading-section > .grading-heading').get_by_role('button', name='Refresh', exact=True).click()
        for _ in range(90):
            fixture.frq.process_location_one()
            if all(q["location_check"]["status"] == "needs_confirmation" for q in fixture.uploaded_questions()):
                break
        assert fixture.reader.reads == 2, "One shared reading per template page"
        page.locator('.grading-workflow a[href$="tab=locations"]').click()
        expect(page.locator('[data-location-open]')).to_have_count(8)
        expect(page.get_by_role("button", name="Review answer area", exact=True)).to_have_count(1)
        # Rows stay compact and in printed order, each with its own next action (the separate setup guide is gone).
        expect(page.locator(".grading-guide")).to_have_count(0)
        assert page.locator("[data-rubric-question] h3").all_inner_texts() == [f"Question {n}" for n in range(18, 26)]
        expect(page.locator(".grading-question-details[open]")).to_have_count(0)
        expect(page.locator("[data-rubric-question]").first.locator(".grading-chip")).to_have_text(["Answer area ready to confirm", "Approved when you finalize the rubrics"])
        first = page.locator("[data-rubric-question]").first.locator(".grading-question-details")
        if first.get_attribute("open") is None:
            first.locator("summary").first.click()
        expect(first).to_have_attribute("open", "")
        # The rubric is shown as read from the file; it is approved later, by the rubric finalization.
        expect(page.locator("[data-rubric-question]").first.get_by_role("button", name="Approve rubric", exact=True)).to_have_count(0)
        expect(first.locator(".grading-rule-list li")).to_have_count(len(fixture.uploaded_questions()[0]["rubric"]["rules"]["items"]))
        page.get_by_role("button", name="Review answer area", exact=True).first.click()
        expect(page.locator("#dialog")).to_contain_text("Printed reading: 4 points")
        page.get_by_role("button", name="Confirm answer area", exact=True).click()
        expect(page.locator("#dialog .form-error")).to_contain_text("I checked every part and continuation page")
        page.get_by_label("I checked every part and continuation page: I inspected and confirm every answer area, including all parts and continuation pages").check()
        page.get_by_role("button", name="Confirm answer area", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        expect(page.locator("[data-location-check]").first).to_contain_text("Answer area confirmed")
        original = fixture.uploaded_questions()[0]
        page.locator('.grading-workflow a[href$="tab=rubrics"]').click()
        page.get_by_role("button", name="Upload rubrics", exact=True).click()
        page.locator('[name="rubric_files"]').set_input_files(files)
        page.get_by_role("button", name="Upload and continue", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        expect(page.locator("[data-rubric-question]")).to_have_count(8)
        assert fixture.uploaded_questions()[0] == original
        page.get_by_role("button", name="Upload rubrics", exact=True).click()
        page.locator('[name="rubric_files"]').set_input_files([
            {"name": "9_Question_26.csv", "mimeType": "text/csv", "buffer": encode(export_rows()).encode()},
            {"name": "broken.csv", "mimeType": "text/csv", "buffer": b"Not a rubric"}])
        page.get_by_role("button", name="Upload and continue", exact=True).click()
        expect(page.locator("#dialog .form-error")).to_contain_text("1 imported; 1 failed")
        expect(page.locator("[data-upload-results]")).to_contain_text("Question 26")
        page.get_by_role("button", name="Close", exact=True).click()
        expect(page.locator("[data-rubric-question]")).to_have_count(9)
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print("Rubric-first browser passed: empty plan, eight files/89 items, OCR, confirmation, repeat uploads, partial failures, no sideways scroll on the desktop.")
finally:
    server.close()
    fixture.doCleanups()
