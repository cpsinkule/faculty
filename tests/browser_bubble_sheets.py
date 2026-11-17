"""Bubble sheets in the real browser: ask for a read, see clean and unclear sheets, review one, confirm a suggestion.

Run from the project root: python tests/browser_bubble_sheets.py
"""
import io
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from bubble_fixture import other_page, pdf, sheet
from test_rosters import COLUMNS, csv_bytes

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
KEY = {number: "ABCDE"[number % 5] for number in range(1, 11)}
ROSTER = (("123456789", "Example, Alice"), ("223456780", "Example, Bob"), ("987650001", "<b>Nguyen</b>, Carol"), ("980000444", "Zamora, Dana"))


def expect_counts(panel, clean, needs_review, reviewed):
    """The scan's next action remains readable without opening its details."""
    labels = {"clean": r"(?:read\s+cleanly|clean)", "needs_review": r"(?:needs?\s+review|to\s+review)", "reviewed": r"reviewed\s+by\s+you"}
    for key, count in (("clean", clean), ("needs_review", needs_review), ("reviewed", reviewed)):
        node = panel.locator(f'[data-bubble-count="{key}"]')
        expect(node).to_be_visible(timeout=15000)
        label = labels[key]
        expect(node).to_have_text(re.compile(rf"^\s*(?:{count}\s*{label}|{label}\s*{count})\s*$", re.I), timeout=15000)


def expect_identity(panel, uin, confidence, review, none):
    """Sources of student identification remain inspectable in Reading details."""
    details = panel.locator("[data-bubble-reading-details]")
    labels = {"uin": r"\bUIN\b", "confidence": r"digits|name|confidence", "review": r"instructor|you|review", "none": r"not identified|unmatched|unidentified"}
    for key, count in (("uin", uin), ("confidence", confidence), ("review", review), ("none", none)):
        node = details.locator(f'[data-bubble-identity="{key}"]')
        expect(node).to_have_count(1)
        expect(node.locator("dd")).to_have_text(str(count))
        expect(node.locator("dt")).to_contain_text(re.compile(labels[key], re.I))


def disclose(details, opened=True):
    if details.evaluate("node => node.open") != opened:
        details.locator("> summary").click()
    assert details.evaluate("node => node.open") == opened


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(temporary)
        client = app.test_client()
        assert client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [501, 502], "multiple_sections": True}, headers=HEADERS).status_code == 201
        state = client.get("/api/state").json
        rows = [["", name, uin, "Registered", "", "", "", "", "", "", "", f"s{index}@example.test"] for index, (uin, name) in enumerate(ROSTER)]
        assert client.post(f"/api/course-groups/{state['course_groups'][0]['id']}/sections/501/roster", data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS).status_code == 201
        assert client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "test-1", "title": "Test 1"}, headers=HEADERS).status_code == 201
        inbox, scans, bubbles = (app.extensions[name] for name in ("inbox", "document_scans", "bubble_sheets"))
        images = [sheet(KEY, "A"), other_page(), sheet({**KEY, 4: ""}, "B", faint={4: "D"}), sheet(KEY, "A", upside_down=True), sheet(KEY, "B")]
        job = inbox.submit({"action": "scan.receive"}, [{"filename": "sheets.pdf", "content": pdf(images)}], source="email", sender="scanner",
                           idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        assert scans.process_one()
        reads = [("123456789", "Alice Example"), ("223456180", "Bob Example"), ("98", "<script>alert(1)</script>"), ("987650007", "Carol Nguyen")]
        ocr = lambda content, boxes: {"reads": [{"text": text} for pair in reads for text in pair][:len(boxes)], "seconds": 0}
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text)
                        if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                page.on("dialog", lambda dialog: errors.append("unexpected dialog: " + dialog.message))
                # The scan is a card with its flow graph (requirement, 2026-09-24): the positioning marks say what these pages are as
                # soon as the scan is inspected, and the sheets have a stage of their own; whose they are is still to be said.
                page.goto(f"{address}/#inbox/job/{job['id']}")
                recognition = page.locator(".document-recognition")
                expect(recognition).to_have_attribute("data-verdict", "bubble_sheets")
                card = recognition.locator("[data-scan-card]")
                expect(card.locator("h3")).to_have_text("4 bubble sheets")
                expect(card.locator("[data-pipeline-step]")).to_have_count(5)
                assert card.locator("[data-pipeline-step]").evaluate_all("nodes => nodes.map(node => node.dataset.pipelineStep)") == ["scan", "assignment", "sheets", "review", "grading"]
                expect(card.locator("[data-pipeline-step='sheets'] a")).to_have_attribute("title", re.compile("Say whose 4 bubble sheets these are: choose the assignment and sections, then read them."))
                expect(page.locator(".bubble-sheets")).to_have_count(0)  # the reader is on its own page, not the card's
                assert not page.locator("[data-inbox-record]").evaluate("node => node.open")
                expect(page.locator(".inbox-audit")).to_be_hidden()
                page.goto(f"{address}/#inbox/job/{job['id']}/assignment")
                expect(page.locator("[data-scan-content]")).to_contain_text("Recognized as bubble sheets from their positioning marks: 4 of 5 pages are Gradescope Bubble Sheet v2020.05.01 sheets.")
                expect(page.locator("[data-scan-content]").get_by_role("button", name="Choose assignment", exact=True)).to_be_visible()
                page.locator("[data-scan-flow] [data-pipeline-step='sheets'] a").click()
                expect(page).to_have_url(re.compile(rf"#inbox/job/{job['id']}/sheets$"))
                sheets_page = page.locator("[data-scan-content]")
                expect(sheets_page.locator(".students-page-heading")).to_contain_text("Say whose 4 bubble sheets these are")
                panel = page.locator(".bubble-sheets")
                expect(panel).to_contain_text("Gradescope Bubble Sheet v2020.05.01")
                expect(panel).to_contain_text("carries no Faculty label")
                expect(page.locator('[data-bubble-slot="last"] .bubble-sheets')).to_be_visible()
                expect(panel.locator("[data-bubble-detected]")).to_contain_text("4 pages of this scan were recognized as bubble sheets by their positioning marks.")
                expect(panel.locator("[data-bubble-detected]")).to_be_visible()
                # Nothing to choose about sections: each sheet takes its section from the student it is matched to.
                expect(panel.locator('input[name="section"]')).to_have_count(0)
                # Nothing is chosen for the instructor: the assignment starts unchosen (no association, read or suggestion), and the
                # roster it is matched against shows once it is chosen.
                choice = panel.get_by_label("Bubble sheet assignment")
                expect(choice).to_have_value("")
                expect(choice.locator("option:checked")).to_have_text("Choose the assignment…")
                expect(panel.locator("[data-bubble-sections]")).to_have_text("Choose the assignment to see whose roster the sheets are matched against.")
                choice.select_option(label="MATH 140 · Test 1")
                expect(panel.locator("[data-bubble-sections]")).to_contain_text("Sheets are matched against everyone in Fall 2026 · Sections 501, 502.")
                # The reader sits on the Bubble sheets page; refreshing the page around it must leave its forms its own.
                page.get_by_role("button", name="Refresh page review").click()
                page.wait_for_load_state("networkidle")
                # The count is left to the sheets: the question most of them have their last mark on.
                expect(panel.get_by_label("Questions on the sheet")).to_have_attribute("placeholder", "Automatic")
                expect(panel.get_by_label("Questions on the sheet")).to_have_value("")
                panel.get_by_role("button", name="Read bubble sheets").click()
                expect(panel.locator("[data-bubble-status]")).to_contain_text("Queued.")
                assert not panel.locator("[data-bubble-form]").evaluate("node => node.open")
                expect(panel.get_by_label("Questions on the sheet")).to_be_hidden()
                with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_fields", side_effect=ocr):
                    assert bubbles.process_one()
                # A misread digit does not hold a sheet back: at 85% or more from the near UIN and the name, the student is identified.
                expect_counts(panel, clean=2, needs_review=2, reviewed=0)
                reading_details = panel.locator("[data-bubble-reading-details]")
                settings = panel.locator("[data-bubble-identification-settings]")
                assert not reading_details.evaluate("node => node.open")
                assert not settings.evaluate("node => node.open")
                expect_identity(panel, uin=1, confidence=2, review=0, none=1)
                expect(reading_details.locator("[data-bubble-detected]")).to_be_hidden()
                expect(reading_details.locator('[data-bubble-identity="uin"]')).to_be_hidden()
                # The compact result still exposes its evidence through a keyboard-operable disclosure.
                reading_details.locator("> summary").focus()
                page.keyboard.press("Enter")
                assert reading_details.evaluate("node => node.open")
                expect(reading_details.locator('[data-bubble-identity="uin"]')).to_be_visible()
                expect(reading_details.locator("[data-bubble-detected]")).to_contain_text("4 pages of this scan were recognized as bubble sheets by their positioning marks.")
                expect(reading_details.locator("[data-bubble-detected]")).to_be_visible()
                expect(panel.get_by_role("button", name=re.compile("Confirm suggested"))).to_have_count(0)
                # The threshold is the instructor's own setting. Raised to 95%, the two 94% and 90% matches wait as suggestions again;
                # back at 70% they are identified. Nothing is read again either way.
                control = panel.get_by_label("Identify a student automatically at this confidence, in percent")
                expect(control).to_be_hidden()
                disclose(settings)
                expect(control).to_be_visible()
                expect(control).to_have_value("70")
                control.fill("95")
                panel.get_by_role("button", name="Apply").click()
                expect(page.locator("#toast")).to_contain_text("identified automatically at 95% or more")
                expect_counts(panel, clean=1, needs_review=3, reviewed=0)
                expect_identity(panel, uin=1, confidence=0, review=0, none=3)
                assert reading_details.evaluate("node => node.open")
                expect(reading_details).to_contain_text("95%")
                assert settings.evaluate("node => node.open")
                control = panel.get_by_label("Identify a student automatically at this confidence, in percent")
                control.fill("70")
                panel.get_by_role("button", name="Apply").click()
                expect_counts(panel, clean=2, needs_review=2, reviewed=0)
                expect_identity(panel, uin=1, confidence=2, review=0, none=1)
                expect(reading_details.locator('[data-bubble-identity="label"]')).to_have_count(0)  # Only a read with a labeled sheet gains the row.
                # A sheet identified by its Scantron label is counted in the breakdown (the server adds identity.label only then).
                def with_label(route):
                    response = route.fetch()
                    body = response.json()
                    if body.get("run"):
                        body["run"]["identity"]["label"] = 2
                    route.fulfill(response=response, json=body)
                page.route("**/api/bubble-sheets/messages/*", with_label)
                panel.get_by_role("button", name="Apply").click()
                label_row = reading_details.locator('[data-bubble-identity="label"]')
                expect(label_row).to_have_count(1)
                expect(label_row.locator("dt")).to_have_text("Scantron label")
                expect(label_row.locator("dd")).to_have_text("2")
                page.unroute("**/api/bubble-sheets/messages/*", with_label)
                panel.get_by_role("button", name="Apply").click()
                expect(reading_details.locator('[data-bubble-identity="label"]')).to_have_count(0)
                expect_identity(panel, uin=1, confidence=2, review=0, none=1)
                disclose(settings, False)
                disclose(reading_details, False)
                expect(panel.get_by_label("Identify a student automatically at this confidence, in percent")).to_be_hidden()
                # A link to ONE sheet (the grading Certification page's drawer: `sheets?sheet=<id>`) opens that sheet's review.
                sheet_id, sheet_page = page.evaluate("""async (mid) => { const data = await (await fetch(`/api/bubble-sheets/messages/${mid}`)).json();
                    const sheet = data.run.sheets[data.run.sheets.length - 1]; return [sheet.id, sheet.page]; }""", job["message_id"])
                page.goto(f"{address}/#inbox/job/{job['id']}/sheets?sheet={sheet_id}")
                page.reload()
                expect(page.locator("#dialog #bubble-review")).to_be_visible()
                expect(page.locator("#dialog")).to_contain_text(f"Bubble sheet on page {sheet_page}")
                page.keyboard.press("Escape")
                expect(page.locator("#dialog")).to_be_hidden()
                page.goto(f"{address}/#inbox/job/{job['id']}/sheets")
                page.reload()
                expect(panel.locator(".bubble-table")).to_be_visible()
                # The page follows the read by itself: what is left is the review, and it says for whom the sheets were read.
                heading = sheets_page.locator(".students-page-heading")
                expect(heading).to_contain_text("Review 2 bubble sheets of 4 that could not be read or matched with confidence (read for Test 1 · Section 501).")
                expect(heading).not_to_contain_text("Say whose")
                page.get_by_role("button", name="Refresh page review").click()
                expect(heading).to_contain_text("Review 2 bubble sheets of 4")
                assert not reading_details.evaluate("node => node.open")
                assert not settings.evaluate("node => node.open")
                rows = panel.locator(".bubble-table tbody tr")
                expect(rows).to_have_count(2)
                expect(rows.nth(0)).to_contain_text("BCD·ABCDEA")
                expect(rows.nth(0)).to_contain_text("a faint or erased mark at D")
                expect(rows.nth(0)).to_contain_text("Example, Bobidentified at 94%")
                expect(rows.nth(1)).to_contain_text("No student could be matched")
                expect(panel.get_by_role("region", name="Bubble sheet readings")).to_have_attribute("tabindex", "0")
                expect(page.locator("#toast")).to_be_hidden(timeout=10000)
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(output / "bubble-sheets-results.png"), full_page=True)
                # Supporting details stay folded in the narrowest desktop window while counts and review controls remain reachable.
                for width in (1280,):
                    page.set_viewport_size({"width": width, "height": 1000})
                    expect_counts(panel, clean=2, needs_review=2, reviewed=0)
                    expect(sheets_page.get_by_role("button", name="Review all (2)")).to_be_visible()
                    expect(panel.get_by_role("link", name="Readings (CSV)")).to_be_visible()
                    expect(reading_details.locator("[data-bubble-detected]")).to_be_hidden()
                    expect(panel.get_by_label("Identify a student automatically at this confidence, in percent")).to_be_hidden()
                    page.evaluate("window.scrollTo(0, 0)")
                    page.screenshot(path=str(output / f"bubble-sheets-results-{width}.png"), full_page=True)
                    overflow = page.evaluate("""() => ({width: innerWidth, document: document.documentElement.scrollWidth,
                      elements: [...document.querySelectorAll('body *')].filter(node => node.getBoundingClientRect().right > innerWidth + 1)
                        .slice(0, 12).map(node => ({tag: node.tagName, classes: node.className, right: node.getBoundingClientRect().right}))})""")
                    assert overflow["document"] <= width, f"Bubble sheet results overflow: {overflow}"
                    for count in panel.locator("[data-bubble-count]").all():
                        box = count.bounding_box()
                        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
                page.set_viewport_size({"width": 1440, "height": 1100})
                # Review all walks the unresolved sheets in page order inside one pop-up. Skipping moves on, stopping loses nothing,
                # and the button starts again from the first sheet that still needs review.
                form = page.locator("#bubble-review")
                # One Review all, the page's: the panel on the page draws no second one (UX overhaul 2026-09-26).
                expect(panel.get_by_role("button", name=re.compile("Review all"))).to_have_count(0)
                expect(page.locator(".bubble-panel-title")).to_have_count(0)
                sheets_page.get_by_role("button", name="Review all (2)").click()
                expect(form.locator("h2")).to_have_text("Bubble sheet on page 3")
                expect(form).to_contain_text("Reviewing all · 2 sheets left in this scan")
                expect(form.get_by_role("button", name="Save and next")).to_be_visible()
                expect(form.get_by_label("Question 4 answer")).to_be_focused()  # Straight to the one thing in doubt on this sheet.
                form.get_by_role("button", name="Skip").click()
                expect(form.locator("h2")).to_have_text("Bubble sheet on page 4")
                expect(form.get_by_label("Student")).to_be_focused()
                form.get_by_role("button", name="Stop").click()
                expect(page.locator("#dialog")).not_to_be_visible()
                expect_counts(panel, clean=2, needs_review=2, reviewed=0)
                # Review the unclear sheet: the flagged question is highlighted, the suggested student preselected.
                rows.nth(0).get_by_role("button", name="Review").click()
                form = page.locator("#bubble-review")
                # Only what is in doubt is open: question 4. The identified student, the version and nine questions are confident,
                # so they sit collapsed and locked where a stray keystroke cannot change them.
                needs = form.locator("[data-needs-review]")
                expect(needs).to_contain_text("Needs your review")
                expect(needs.locator('[data-item="student"]')).to_have_count(0)
                expect(needs.locator('[data-item="version"]')).to_have_count(0)
                expect(needs.locator(".bubble-answer")).to_have_count(1)
                confident = form.locator("[data-confident]")
                expect(confident.locator("> summary")).to_have_text("Confident reads: the student, the version, 9 questions")
                expect(confident).to_contain_text("Identified automatically from the digits and the name read, at 94% confidence.")
                assert not confident.evaluate("node => node.open")
                expect(form.get_by_label("Question 1 answer")).to_be_hidden()
                expect(form.locator(".bubble-answer.flagged")).to_have_count(1)
                expect(form.locator(".bubble-answer.flagged")).to_contain_text("faint or erased mark at D")
                # What is unclear is shown as the piece of the scan it came from: the flagged row, the ID and name boxes, the version row.
                def loaded(selector):  # Polled from here: the page's Content Security Policy forbids Playwright's in-page polling.
                    for _ in range(50):
                        if page.evaluate("s => { const images = [...document.querySelectorAll(s)]; return images.length > 0 && images.every(i => i.complete && i.naturalWidth > 200); }", selector):
                            return
                        page.wait_for_timeout(100)
                    raise AssertionError(f"Crops did not load: {selector}")
                expect(form.locator(".bubble-answer.flagged img.bubble-crop")).to_have_attribute("alt", "Question 4 on the scan")
                expect(form.locator(".bubble-identity img.bubble-crop")).to_have_count(2)
                loaded("#bubble-review [data-needs-review] img.bubble-crop")
                expect(form.locator("[data-needs-review] img.bubble-crop")).to_have_count(1)  # Question 4 alone; the name and ID boxes wait with the confident reads.
                # The snapshot is also where the answer is given: a click on the bubble that counts, no typing.
                answer, choices = form.get_by_label("Question 4 answer"), form.locator('[data-pick="q4"] .bubble-choice')
                expect(choices).to_have_count(5)
                boxes = [choice.bounding_box() for choice in choices.all()]
                picture = form.locator('[data-pick="q4"] img').bounding_box()
                assert all(a["x"] + a["width"] <= b["x"] + 1 for a, b in zip(boxes, boxes[1:])) and boxes[0]["x"] >= picture["x"] and boxes[-1]["x"] + boxes[-1]["width"] <= picture["x"] + picture["width"] + 1, boxes
                form.get_by_role("button", name="Question 4: D").click()
                expect(answer).to_have_value("D")
                expect(form.get_by_role("button", name="Question 4: D")).to_have_attribute("aria-pressed", "true")
                form.get_by_role("button", name="Question 4: B").click()  # Another bubble replaces it...
                expect(answer).to_have_value("B")
                form.get_by_role("button", name="Question 4: D").click(modifiers=["Shift"])  # ...Shift adds a second, for a real double mark...
                expect(answer).to_have_value("BD")
                form.get_by_role("button", name="Question 4: B").click(modifiers=["Shift"])
                form.get_by_role("button", name="Question 4: D").click()  # ...and clicking the only chosen bubble again leaves a blank.
                expect(answer).to_have_value("")
                answer.fill("c")  # Typing still works, and the snapshot follows it.
                expect(form.get_by_role("button", name="Question 4: C")).to_have_attribute("aria-pressed", "true")
                expect(form.locator('[data-confident] img.bubble-crop[alt="Version row on the scan"]')).to_have_count(1)
                # Opening the confident reads is for comparing: fields stay locked until deliberately unlocked.
                confident.locator("> summary").click()
                expect(form.get_by_label("Question 1 answer")).to_be_disabled()
                expect(form.get_by_label("Version", exact=True)).to_be_disabled()
                form.get_by_role("button", name="Show question 9 on the scan").click()
                expect(form.locator('img.bubble-crop[alt="question 9 on the scan"]')).to_be_visible()
                expect(form.get_by_role("button", name="question 9 on the scan: A")).to_be_disabled()  # A confident read is not changed by a stray click.
                form.get_by_role("button", name="Unlock these to edit").click()
                expect(form.get_by_label("Question 1 answer")).to_be_enabled()
                expect(confident).to_contain_text("Unlocked: changes here replace what was read.")
                expect(form.get_by_role("button", name="question 9 on the scan: A")).to_be_enabled()
                form.get_by_label("Question 10 answer").fill("e")  # A deliberate manual entry.
                expect(form.get_by_label("Student")).to_have_value(re.compile(r"^Example, Bob · Section 501$"))
                expect(form.get_by_label("Version", exact=True)).to_have_value("B")
                expect(form.locator(".bubble-page img")).to_be_visible()
                form.get_by_label("Question 4 answer").fill("d")
                page.screenshot(path=str(output / "bubble-sheets-review.png"))
                form.get_by_role("button", name="Save review").click()
                expect(page.locator("#dialog")).not_to_be_visible()
                expect_counts(panel, clean=2, needs_review=1, reviewed=1)
                expect_identity(panel, uin=1, confidence=1, review=1, none=1)
                # OCR text and roster names are data: shown escaped, never run. The upside-down sheet is shown upright.
                panel.locator(".bubble-table tbody tr").nth(0).get_by_role("button", name="Review").click()
                expect(form).to_contain_text("name “<script>alert(1)</script>”")
                expect(form.locator(".bubble-page.upside-down")).to_have_count(1)
                # Here only the student is in doubt; every mark read cleanly, so all ten questions and the version are tucked away.
                expect(form.locator("[data-needs-review] .bubble-answer")).to_have_count(0)
                expect(form.locator("[data-confident] > summary")).to_have_text("Confident reads: the version, 10 questions")
                # Nobody was identified, so the picker starts on the closest student still free, with her confidence, not on nobody.
                # The three students already recorded for other sheets are not offered: they sit apart, still selectable.
                # The picker is a search box (requirement, 2026-09-24): opening it lists the closest students first, then the rest.
                student = form.get_by_label("Student")
                expect(student).to_have_value(re.compile(r"^Zamora, Dana · Section 501$"))
                expect(form.locator('input[name="student"]')).to_have_value(re.compile(r"\|"))
                student.click()
                expect(form.locator('[role="option"][data-group="Closest to what was read"]')).to_have_text([re.compile(r"Zamora, Dana · Section 501 \d+%")])
                expect(form).to_contain_text("The closest student still free is preselected; check it against the snapshots before saving.")
                expect(form.locator(".student-picker-list")).to_contain_text("<b>Nguyen</b>, Carol")
                taken = form.locator('[role="option"][data-group="Already matched to another sheet"]')
                expect(taken).to_have_count(3)
                # Digits typed are matched against the roster's UINs on the server; names come back, never the UIN.
                student.fill("98000044")
                expect(form.locator('[role="option"][data-group="Closest UINs"]').first).to_have_text(re.compile(r"^Zamora, Dana · Section 501 UIN 100%"))
                assert "980000444" not in form.inner_html()
                student.press("Escape")  # Closes the list only, and puts back what was chosen.
                expect(page.locator("#dialog")).to_be_visible()
                expect(student).to_have_value(re.compile(r"^Zamora, Dana · Section 501$"))
                form.get_by_role("button", name="Cancel").click()
                panel.get_by_label("Show sheets").select_option("all")
                expect(panel.locator(".bubble-table tbody tr")).to_have_count(4)
                # The readings open in the app's file viewer as a table (VIEWER); its Download saves the CSV itself.
                panel.get_by_role("link", name="Readings (CSV)").click()
                viewer = page.locator("dialog.file-viewer")
                expect(viewer.locator(".file-viewer-table thead")).to_contain_text("Status")
                with page.expect_download() as capture:
                    viewer.get_by_role("link", name="Download").click()
                viewer.get_by_role("button", name="Close the viewer").click()
                text = Path(capture.value.path()).read_text(encoding="utf-8-sig")
                # Locked confident values were saved as read (B,C,D … and version B); the flagged 4 and the deliberate 10 as entered.
                assert '3,reviewed,"Example, Bob",223456780,501,instructor,B,B,C,D,D,A,B,C,D,E,E,' in text and "\n4,needs_review,,,,,A,B,C" in text and "'" not in text, text
                assert '5,clean,"<b>Nguyen</b>, Carol",987650001,501,"digits and name read, 90%",B,' in text and "1,clean,\"Example, Alice\",123456789,501,exact UIN,A," in text, text
                # One sheet is left. Review all from the page: saving it finishes the scan, and what was saved earlier stayed saved.
                expect(sheets_page.get_by_role("button", name="Review all (1)")).to_be_visible()
                sheets_page.get_by_role("button", name="Review all (1)").click()
                expect(form.locator("h2")).to_have_text("Bubble sheet on page 4")
                expect(form).to_contain_text("Reviewing all · 1 sheet left in this scan")
                form.get_by_label("Note").fill("No roster student wrote this sheet")
                form.get_by_role("button", name="Save and next").click()
                expect(page.locator("#dialog")).not_to_be_visible()
                expect(page.locator("#toast")).to_contain_text("Every bubble sheet in this scan is reviewed.")
                expect_counts(panel, clean=2, needs_review=0, reviewed=2)
                expect(panel.get_by_role("button", name=re.compile("Review all"))).to_have_count(0)
                expect(sheets_page.get_by_role("button", name=re.compile("Review all"))).to_have_count(0)
                for width in (1440, 1280):
                    page.set_viewport_size({"width": width, "height": 1000})
                    expect_counts(panel, clean=2, needs_review=0, reviewed=2)
                    expect(panel.get_by_role("link", name="Readings (CSV)")).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Bubble sheet panel overflow at {width}"
                # With every sheet reviewed, the list opens on every sheet rather than an empty "Needs review" view.
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.reload()
                expect(page.locator(".bubble-sheets").get_by_label("Show sheets")).to_have_value("all")
                expect(page.locator(".bubble-sheets .bubble-table tbody tr")).to_have_count(4)
                # The panel sits inside the page's one frame.
                expect(page.locator(".scan-sheets-frame [data-bubble-slot] .bubble-sheets")).to_have_count(1)
                # With every sheet reviewed, the review is what is left, on its own page; the scan's card says it awaits it.
                page.locator("[data-scan-flow] [data-pipeline-step='review'] a").click()
                expect(page.locator("[data-scan-content]").get_by_role("button", name="Finish review & open grading", exact=True)).to_be_visible()
                # Nothing earlier is still open, so the Review page lists nothing before its Finish button.
                expect(page.locator("[data-scan-still-open]")).to_have_count(0)
                page.goto(f"{address}/#inbox/job/{job['id']}")
                expect(page.locator("[data-scan-card] [data-pipeline-step=\"review\"] a")).to_have_attribute("aria-label", re.compile(r"^Review: (Not started|Needs you)\."))
                # Finished, the Review page says so in one line, with the way on to grading.
                page.locator("[data-scan-card] [data-pipeline-step='review'] a").click()
                page.locator("[data-scan-content]").get_by_role("button", name="Finish review & open grading", exact=True).click()
                finish = page.get_by_role("dialog")
                finish.get_by_label("I have checked this scan and its assignment").check()
                finish.get_by_role("button", name="Finish review & open grading", exact=True).click()
                expect(page).to_have_url(re.compile(r"#grading/"))
                page.goto(f"{address}/#inbox/job/{job['id']}/review")
                expect(page.locator("[data-scan-reviewed]")).to_have_text("Reviewed · Open grading")
                expect(page.locator("[data-scan-content]").get_by_role("button", name="Finish review & open grading")).to_have_count(0)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Bubble sheets: review all (skip, stop, resume, finish), request, queued status, semantic counts, keyboard-operable folded reading details, highlighted review, clickable bubbles, saved review, escaped OCR text, disclosed identification threshold, already matched students set apart, CSV, review gate and the 1280px desktop layout passed.")


if __name__ == "__main__":
    main()
