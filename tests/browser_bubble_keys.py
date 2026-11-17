"""Bubble sheet answer key in the real browser: click and type each version's key, save it, see the sheets scored and the item analysis.

Run from the project root: python tests/browser_bubble_keys.py
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
from bubble_fixture import pdf, sheet
from rosters import connect
from test_rosters import COLUMNS, csv_bytes

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
KEY = {number: "ABCDE"[number % 5] for number in range(1, 11)}  # B C D E A B C D E A
OTHER = {**KEY, 1: "A", 2: "AB"}
ROSTER = (("123456789", "Example, Alice"), ("223456780", "Example, Bob"), ("987650001", "<b>Nguyen</b>, Carol"), ("980000444", "Zamora, Dana"))


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
        assignment = client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        inbox, scans, bubbles, frq = (app.extensions[name] for name in ("inbox", "document_scans", "bubble_sheets", "frq_grading"))
        images = [sheet(KEY, "A"), sheet(OTHER, "B"), sheet({**KEY, 3: ""}, "A"), sheet({**OTHER, 2: "B"}, "B")]
        job = inbox.submit({"action": "scan.receive"}, [{"filename": "sheets.pdf", "content": pdf(images)}], source="email", sender="scanner",
                           idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        assert scans.process_one()
        assert client.post(f"/api/bubble-sheets/messages/{job['message_id']}", json={"assignment_id": assignment["id"], "question_count": 10}, headers=HEADERS).status_code == 202
        reads = [("123456789", "Alice Example"), ("223456780", "Bob Example"), ("987650001", "Carol Nguyen"), ("980000444", "Dana Zamora")]
        ocr = lambda content, boxes: {"reads": [{"text": text} for pair in reads for text in pair][:len(boxes)], "seconds": 0}
        with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_fields", side_effect=ocr):
            assert bubbles.process_one()
        with connect(app.config["DATABASE"]) as db:
            status = frq.scan_status(db, job["message_id"])
        frq.review_scan(job["message_id"], {"confirm_review": True, "fingerprint": status["fingerprint"]})
        plan = status["plan"]["id"]
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
                # The Grading index is retired (2026-10-18); the assignment's Bubble sheets step opens directly.
                page.goto(f"{address}/#grading/{plan}?tab=bubble")
                key = page.locator("[data-bubble-key]")
                expect(key.locator(".bubble-key-state")).to_have_text("Not saved yet")
                expect(page.locator("[data-grading-bubble]")).to_have_attribute("aria-current", "page")
                # Both versions were marked on two sheets each, so the key starts with tabs A and B; the sheets were read with 10 questions.
                expect(key.locator('[role="tab"]')).to_have_text([re.compile(r"Version A\s*0"), re.compile(r"Version B\s*0")])
                expect(key.locator(".bubble-key-question")).to_have_count(10)  # as many as the sheets were read with
                expect(key.locator(".bubble-key-toolbar")).to_contain_text("the sheets were read with 10 questions")
                scores = page.locator(".bubble-scores")
                expect(scores.locator("tbody tr")).to_have_count(4)
                expect(scores).to_contain_text("<b>Nguyen</b>, Carol")  # roster text is escaped, never markup
                expect(scores.locator("tbody tr").first).to_contain_text("Save an answer key to score this sheet.")

                # Version A is typed: with a question's letter focused, each letter answers it and the next question takes focus.
                key.locator('[data-number="1"] [data-letter="A"]').focus()
                page.keyboard.type("bcdeabcdea")
                for number, letter in KEY.items():
                    expect(key.locator(f'[data-number="{number}"] [data-letter="{letter}"]')).to_have_attribute("aria-pressed", "true")
                key.locator("[data-show-all]").click()
                expect(key.locator(".bubble-key-question")).to_have_count(100)
                key.locator("[data-show-fewer]").click()
                expect(key.locator(".bubble-key-state")).to_have_text("Unsaved changes")
                # Version B is clicked. A second click on the only chosen letter clears it; Shift-click adds a second letter.
                key.locator('[role="tab"][data-version="B"]').click()
                for number, letter in OTHER.items():
                    key.locator(f'[data-number="{number}"] [data-letter="{letter[0]}"]').click()
                key.locator('[data-number="3"] [data-letter="D"]').click()
                expect(key.locator('[data-number="3"] [aria-pressed="true"]')).to_have_count(0)
                key.locator('[data-number="3"] [data-letter="D"]').click()
                key.locator('[data-number="2"] [data-letter="B"]').click(modifiers=["Shift"])
                expect(key.locator('[data-number="2"] [aria-pressed="true"]')).to_have_count(2)
                # Question 2 of version B is scored Partial: two correct letters, so one of them earns half its points.
                key.locator('[data-question-settings="2"]').click()
                dialog = page.locator("#grading-dialog")
                expect(dialog.locator('input[name="letter"]:checked')).to_have_count(2)
                dialog.locator('select[name="scoring"]').select_option("partial")
                dialog.locator('input[name="points"]').fill("2")
                dialog.locator('button[type="submit"]').click()
                expect(key.locator('[data-number="2"] .bubble-key-custom')).to_have_text("Partial · 2 pts")
                key.locator('[data-default-scoring="exact"]').click()
                key.locator("[data-default-points]").fill("1")
                page.screenshot(path=str(output / "bubble-key-draft.png"), full_page=True)
                key.locator("[data-save]").click()
                expect(key.locator(".bubble-key-state")).to_have_text(re.compile(r"^Saved · revision 1 · "))
                # Saved, the key is one compact line per version with one button to change it (the editor closes).
                expect(key.locator("[data-save]")).to_have_count(0)
                expect(key.locator("[data-edit-key]")).to_be_visible()
                expect(key.locator('[data-compact-version="A"]')).to_contain_text("1 B C D E A")
                expect(key.locator('[data-compact-version="B"]')).to_contain_text("(AB)")
                expect(page.locator("[data-bubble-summary]")).to_contain_text("Key saved · revision 1")
                expect(page.locator("[data-bubble-summary]")).to_contain_text("4 scored")
                # The Gradescope panel sits between the key and the scores.
                assert page.evaluate("""() => { const [k, g, s] = ['[data-bubble-page]', '[data-gradescope-host]', '[data-bubble-scores]'].map(q => document.querySelector(q));
                    return Boolean(k.compareDocumentPosition(g) & Node.DOCUMENT_POSITION_FOLLOWING) && Boolean(g.compareDocumentPosition(s) & Node.DOCUMENT_POSITION_FOLLOWING); }""")

                rows = {row.locator("td").first.inner_text(): row for row in scores.locator("tbody tr").all()}
                expected = {"Example, Alice": ("A", "10 / 10", "none"), "Example, Bob": ("B", "11 / 11", "none"),
                            "<b>Nguyen</b>, Carol": ("A", "9 / 10", "3"), "Zamora, Dana": ("B", "10 / 11", "2")}
                for name, (version, score, missed) in expected.items():
                    cells = rows[name].locator("td")
                    expect(cells.nth(2)).to_have_text(version)
                    expect(cells.nth(3)).to_have_text(score)
                    expect(cells.nth(4)).to_have_text(missed)
                expect(scores.locator('.stat[data-bubble-count="clean"] > strong')).to_have_text("4")  # Shared stat tiles (review fix GR-4).
                expect(scores.locator(".bubble-score-averages")).to_contain_text("Version A: average 9.5 of 10 (95%) over 2 sheets")
                expect(scores.get_by_role("link", name="Scores (CSV)")).to_have_attribute("data-view-file", re.compile(r"/bubble-sheets/scores\.csv$"))
                # Search and order change the rows only; the controls keep their focus.
                scores.locator("[data-score-search]").fill("carol")
                expect(scores.locator("tbody tr")).to_have_count(1)
                expect(scores.locator("[data-score-search]")).to_be_focused()
                scores.locator("[data-score-search]").fill("")
                scores.locator("[data-score-sort]").select_option("low")
                expect(scores.locator("tbody tr").first.locator("td").first).to_have_text("<b>Nguyen</b>, Carol")
                scores.locator("[data-score-filter]").select_option("unscored")
                expect(scores.locator(".empty-state")).to_contain_text("No sheets in this view.")  # The shared empty state (review fix GR-4).
                page.screenshot(path=str(output / "bubble-key-saved.png"), full_page=True)

                # Item analysis: closed until opened, read once on the first opening, and kept while the key and sheets stay the same.
                reads = []
                page.on("request", lambda request: reads.append(request.url) if "/bubble-sheets/analysis" in request.url else None)
                fold = page.locator("[data-bubble-analysis-fold]")
                expect(fold.locator("[data-analysis-body]")).to_be_hidden()
                assert not reads, reads
                # Between the key and Gradescope.
                assert page.evaluate("""() => { const [k, a, g] = ['[data-bubble-page]', '[data-bubble-analysis]', '[data-gradescope-host]'].map(q => document.querySelector(q));
                    return Boolean(k.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING) && Boolean(a.compareDocumentPosition(g) & Node.DOCUMENT_POSITION_FOLLOWING); }""")
                fold.locator(":scope > summary").click()
                analysis = fold.locator("[data-analysis-body]")
                lead = analysis.locator('[data-analysis-version="A"] .bubble-analysis-lead')
                expect(lead).to_have_text("2 sheets · average 95% (SD 7.1) · reliability and discrimination need at least 10 scored sheets")
                expect(analysis.locator('[data-analysis-version="A"] tbody tr')).to_have_count(10)
                expect(analysis.locator('[data-analysis-version="B"] tbody tr')).to_have_count(10)
                expect(analysis.locator('[data-analysis-version="B"] h4')).to_have_text("Version B")
                expect(analysis.get_by_role("link", name="Item analysis (CSV)")).to_have_attribute("data-view-file", re.compile(r"/bubble-sheets/analysis\.csv$"))
                # Question 3 of Version A: the key's D (Alice) and a blank (Carol); the bars' lengths come through the CSSOM.
                third = analysis.locator('[data-analysis-question="A:3"]')
                expect(third.locator("td").nth(1)).to_have_text("50%")
                expect(third.locator(".bubble-choice")).to_have_count(6)
                expect(third.locator(".bubble-choice[data-key]")).to_contain_text("D")
                share = third.locator(".bubble-choice[data-key] .bubble-choice-bar").evaluate("bar => bar.getBoundingClientRect().width / bar.parentElement.getBoundingClientRect().width")
                assert abs(share - 0.5) < 0.02, share
                expect(analysis.locator('[data-analysis-question="B:2"] td').first).to_have_text("AB Partial")
                assert len(reads) == 1, reads
                page.screenshot(path=str(output / "bubble-item-analysis.png"), full_page=True)
                third.evaluate("row => row.dataset.kept = '1'")
                fold.locator(":scope > summary").click()
                fold.locator(":scope > summary").click()
                expect(third).to_be_visible()
                expect(third).to_have_attribute("data-kept", "1")
                assert len(reads) == 1, reads  # opened again from what was read
                # A question opens in the key editor, focused on its answer; Done closes the editor again.
                third.locator("[data-analysis-look]").click()
                expect(key.locator('[data-number="3"] [aria-pressed="true"]')).to_be_focused()
                expect(key.locator('[role="tab"][data-version="A"]')).to_have_attribute("aria-selected", "true")
                key.locator("[data-close-editor]").click()
                expect(key.locator("[data-edit-key]")).to_be_visible()

                # An unsaved change can be discarded; the saved key comes back. "Edit answer key" opens the editor first.
                key.locator("[data-edit-key]").click()
                scores.locator("[data-score-filter]").select_option("all")
                scores.locator("tbody tr").first.evaluate("row => row.dataset.kept = '1'")
                key.locator('[role="tab"][data-version="A"]').click()
                key.locator('[data-number="5"] [data-letter="C"]').click()
                expect(key.locator(".bubble-key-state")).to_have_text("Unsaved changes")
                expect(scores.locator('tbody tr[data-kept="1"]')).to_have_count(1)  # letter clicks never redraw the table
                key.locator("[data-discard]").click()
                expect(key.locator('[data-number="5"] [data-letter="A"]')).to_have_attribute("aria-pressed", "true")
                expect(key.locator(".bubble-key-state")).to_have_text(re.compile(r"^Saved · revision 1"))

                # A save from another window wins: this window's save is refused and nothing is overwritten.
                read_before = len(reads)
                saved = client.get(f"/api/grading/assignments/{plan}/bubble-sheets").json["key"]
                other = client.post(f"/api/grading/assignments/{plan}/bubble-sheets/key", headers=HEADERS,
                                    json={"request_id": "other-window", "base_revision": 1, "defaults": {**saved["defaults"], "points": "2"}, "versions": saved["versions"]})
                assert other.status_code == 201, other.json
                key.locator('[data-number="5"] [data-letter="C"]').click()
                key.locator("[data-save]").click()
                expect(page.locator("#toast")).to_contain_text("saved from another window")
                assert client.get(f"/api/grading/assignments/{plan}/bubble-sheets").json["key"]["revision"] == 2

                # At the narrowest desktop window (1280) nothing scrolls sideways.
                page.set_viewport_size({"width": 1280, "height": 900})
                page.goto(f"{address}/#grading/{plan}?tab=bubble")
                expect(page.locator("[data-bubble-key] .bubble-key-state")).to_have_text(re.compile(r"^(Saved · revision 2|Unsaved changes)"))
                assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth"), "horizontal page scroll at 1280"
                # The item analysis after a reload: read again on opening, its table inside its own scrolling region.
                page.reload()
                expect(key.locator(".bubble-key-state")).to_have_text(re.compile(r"^Saved · revision 2"))
                fold = page.locator("[data-bubble-analysis-fold]")
                fold.locator(":scope > summary").click()
                third = fold.locator('[data-analysis-question="A:3"]')
                expect(third).to_be_visible()
                assert len(reads) == read_before + 1, reads
                assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth"), "horizontal page scroll at 1280 with the item analysis open"
                page.screenshot(path=str(output / "bubble-item-analysis-1280.png"), full_page=True)
                # A key saved from the question it opens is analysed again at once: question 3 of Version A is now keyed A.
                third.locator("[data-analysis-look]").click()
                key.locator('[data-number="3"] [data-letter="A"]').click()
                key.locator("[data-save]").click()
                expect(key.locator(".bubble-key-state")).to_have_text(re.compile(r"^Saved · revision 3"))
                expect(third.locator("td").first).to_have_text("A")
                assert len(reads) == read_before + 2, reads
                # FRONTEND: editing while Save waits keeps the newer draft and does not submit it twice.
                held = []

                def hold_save(route):
                    response = route.fetch()
                    held.append((route, response))

                page.route("**/bubble-sheets/key", hold_save)
                key.locator("[data-edit-key]").click()
                key.locator("[data-default-points]").fill("3")
                key.locator("[data-save]").click()
                key.locator("[data-default-points]").fill("4")
                expect(key.locator("[data-save]")).to_be_disabled()
                assert len(held) == 1
                held[0][0].fulfill(response=held[0][1])
                expect(key.locator(".bubble-key-state")).to_have_text("Unsaved changes")
                expect(key.locator("[data-default-points]")).to_have_value("4")
                expect(key.locator("[data-save]")).to_be_enabled()
                saved = client.get(f"/api/grading/assignments/{plan}/bubble-sheets").json["key"]
                assert (saved["revision"], saved["defaults"]["points"]) == (4, "3")
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Bubble sheet answer key browser check passed.")


if __name__ == "__main__":
    main()
