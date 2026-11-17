"""First-page labels in Scanned page review: preview, identify, per-page notes and undo in the real browser.

Run from the project root: python tests/browser_sequence.py
"""
import io
import json
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader, PdfWriter
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_course_pages import seed_course
from bubble_fixture import pdf as sheets_pdf, sheet

FIXTURES = Path(__file__).resolve().parents[1] / "document_identity/fixtures"


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(temporary)
        seed_course(app.test_client(), sections=[501, 502])
        documents, scans, inbox = app.extensions["documents"], app.extensions["document_scans"], app.extensions["inbox"]
        catalog = documents.catalog()
        assignment = documents.create_assignment(catalog["definitions"][0]["id"], "exam-1", "Exam 1")
        version = documents.register_version(assignment["id"], "A", (FIXTURES / "letter.pdf").read_bytes(),
                                             {"main.tex": (FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
        batch = documents.create_batch(version["id"], catalog["sections"][0]["id"], 3, str(uuid.uuid4()))
        assert documents.build_one()
        printed = PdfReader(io.BytesIO(documents.download(batch["id"])))
        plain = PdfReader(io.BytesIO((FIXTURES / "letter.pdf").read_bytes()))
        # Three exams labeled on page 1 only; the third has an extra sheet, so it must be left alone.
        writer = PdfWriter()
        for copy, extra in ((0, 0), (1, 0), (2, 1)):
            writer.add_page(printed.pages[copy * 3])
            for page in (1, 2) + (2,) * extra:
                writer.add_page(plain.pages[page])
        content = io.BytesIO()
        writer.write(content)
        job = inbox.submit({"action": "scan.receive"}, [{"filename": "exams.pdf", "content": content.getvalue()}], source="email", sender="scanner",
                           original=b"retained-email:" + content.getvalue(), idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        assert scans.process_one()
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
                # The third exam's extra sheet makes the labels uneven, so nothing was grouped automatically and the instructor decides.
                assert scans.review_item(job["message_id"])["sequence"]["runs"] == []
                page.goto(f"{address}/#inbox/job/{job['id']}")
                # The scan is a card with its flow graph (requirement, 2026-09-24): what it is, and each stage opening its own page.
                recognition = page.locator(".document-recognition")
                expect(recognition).to_have_attribute("data-verdict", "recognized")
                card = recognition.locator("[data-scan-card]")
                expect(card.locator("h3")).to_have_text("Exam 1 · MATH 140 · Section 501")
                # A filed scan opens in its assignment's shell (ASSIGNMENT-WORKSPACE): the page title is the assignment's and the trail
                # runs through Assignments to its Collect area. The scan is still named by what it is, on its card (above) and in the
                # browser tab, with the file and course as context; no Upload button here.
                expect(page.locator("#page-title")).to_have_text("Exam 1")
                expect(page.locator("#page-context")).to_contain_text("MATH 140 · Fall 2026")
                expect(page.locator("#page-context")).to_contain_text("exams.pdf")
                expect(page.locator("#breadcrumb li")).to_have_text(["Home", "Assignments", "Exam 1", "Collect"])
                expect(page).to_have_title(re.compile(r"^(\(\d+\) )?Exam 1 · MATH 140 · Section 501 · "))
                expect(page.locator("#assignment-workspace [data-assignment-editor] > #inbox-section")).to_be_visible()
                expect(page.locator("#primary-action")).to_be_hidden()
                # The card's one button says the goal of the stage that needs the instructor, in the stage's own words.
                expect(card.locator("[data-scan-next-action]")).to_have_text(re.compile(r"^[A-Z].* →$"))
                expect(card.locator("[data-pipeline-step='papers'] a")).to_have_attribute("title", re.compile("7 pages without a label follow the labeled first pages: check the sides per paper and preview the grouping."))
                content, flow = page.locator("[data-scan-content]"), page.locator("[data-scan-flow]")
                stage = lambda key: flow.locator(f"[data-pipeline-step='{key}'] a").click()
                # Assignment: what the labels say the scan is for.
                card.locator("[data-pipeline-step='assignment'] a").click()
                expect(content).to_contain_text("Exam 1 A · MATH 140")
                expect(content).to_contain_text("3 of 3 issued papers appear in this scan (3 labeled pages). Every issued paper has appeared in a scan.")
                expect(content).to_contain_text("10 pages: 7 without a label, 0 blank.")
                # Scan: every page, folded (a section's scan is hundreds of them); "no label" is expected here, not an error: the pages
                # wait for grouping on Papers, so none of them needs a look on this page.
                stage("scan")
                expect(content).to_contain_text("3 with a label read, 0 identified by position or by hand, 7 with no label, 0 blank")
                expect(page.locator("[data-scan-pages-attention]")).to_have_count(0)
                expect(page.locator(".document-scan-page")).to_have_count(0)  # drawn only once the fold is opened
                page.locator("[data-scan-all-pages] > summary").click()
                expect(page.locator(".document-scan-page h4").nth(1)).to_have_text("Attachment page 2 · no label read")
                expect(page.locator(".document-scan-page")).to_have_count(10)
                expect(page.locator(".document-scan-page").nth(1)).to_contain_text("Not matched to a page of any paper yet")
                # Review, before grouping (inbox-1): the papers the grouping would complete are folded under one line that sends
                # the instructor to Papers, and the page has one primary, the next step's.
                stage("review")
                expect(content.locator(".primary")).to_have_count(1)
                expect(content.locator("[data-scan-next-step] .primary")).to_have_count(1)
                needs = content.locator("[data-scan-needs-assemblies]")
                expect(needs.locator("> summary")).to_contain_text("Papers missing pages · 3")
                assert not needs.evaluate("node => node.open")
                expect(page.locator(".document-assembly")).to_have_count(0)
                needs.locator("> summary").click()
                expect(needs.get_by_role("link", name="Papers", exact=True)).to_have_attribute("href", f"#inbox/job/{job['id']}/papers")
                expect(page.locator(".document-assembly")).to_have_count(3)
                expect(page.locator(".document-assembly").first).to_contain_text("Pages missing: 2, 3")
                expect(content.locator(".primary")).to_have_count(1)  # each paper's Save is an ordinary button
                # Papers: nothing grouped yet, so grouping by hand is open.
                stage("papers")
                expect(page).to_have_url(re.compile(rf"#inbox/job/{job['id']}/papers$"))
                # Papers opens like every stage page (GOAL FIRST): its title and the stage's words; nothing was grouped, so nothing
                # claims that nothing is left out. The graph says where it sits and the browser tab names the stage; the header's
                # trail ends in the assignment's Collect area (ASSIGNMENT-WORKSPACE).
                expect(content.locator(".students-page-heading h3")).to_have_text("Papers")  # the stage's name (2026-10-18)
                expect(content.locator(".students-page-heading p")).to_contain_text("7 pages without a label follow the labeled first pages")
                expect(content).not_to_contain_text("Nothing is left out of the grouping.")
                expect(page.locator("#breadcrumb li").last).to_have_text("Collect")
                expect(page).to_have_title(re.compile(r"^(\(\d+\) )?Papers · Exam 1 · MATH 140 · Section 501 · "))
                expect(flow.locator("[data-scan-flow-position]")).to_contain_text(re.compile(r"Stage \d of \d · Papers"))
                panel = page.locator("[data-papers-page]")
                expect(panel.locator("[data-sequence-manual]")).to_contain_text("In this scan the labels are usually 3 sides apart.")
                # Papers offers its own Group button for the papers that add up, so the other ways to group start folded: one
                # grouping control at a time (2026-10-18: every other way is under one "Other ways to group").
                other_ways = page.locator("[data-papers-other-ways]")
                if page.locator("[data-papers-group]").count():
                    assert not other_ways.evaluate("node => node.open")
                    other_ways.locator("> summary").click()
                assert other_ways.evaluate("node => node.open")
                sides = panel.get_by_label("Scanned sides per paper")
                expect(sides).to_have_value("3")
                panel.get_by_role("button", name="Preview").click()
                expect(panel.locator("[data-sequence-counts]")).to_contain_text("2 papers ready (2 papers to group, 4 pages to identify) · 0 already grouped · 1 needs review · 0 pages outside any labeled paper.")
                rows = panel.locator("tbody tr")
                expect(rows).to_have_count(3)
                expect(rows.nth(0)).to_contain_text("Ready: 3 sides become this student's paper; page 2 → 2, page 3 → 3")
                expect(rows.nth(2)).to_contain_text("Not grouped. 4 sides arrive before the next label, which is not a whole number of 3-side papers.")
                page.screenshot(path=str(output / "sequence-preview.png"), full_page=True)
                # Changing the count discards the preview; only an exact preview can be applied.
                sides.fill("4")
                expect(panel.get_by_role("button", name="Group 2 papers and identify 4 pages")).to_have_count(0)
                sides.fill("3")
                panel.get_by_role("button", name="Preview").click()
                panel.get_by_role("button", name="Group 2 papers and identify 4 pages").click()
                expect(panel.locator("[data-sequence-run]")).to_contain_text("grouped 2 papers and identified 4 pages by position at 3 sides per paper")
                # The paper left out heads the page, with why and each side a picture of the retained page (requirement, 2026-09-24).
                check = page.locator("[data-papers-page]")
                expect(check.locator("[data-papers-item]")).to_have_count(1)
                expect(check.locator("[data-papers-item] h5")).to_have_text("Copy 3 pages 7–10")
                expect(check.locator(".students-page-group > h4")).to_have_text(["Held for you 1"])
                expect(check).to_contain_text("4 sides arrive before the next label, which is not a whole number of 3-side papers.")
                # Its way out is named: keep what was read as a partial paper, on Review.
                expect(check.get_by_role("link", name="Review", exact=True)).to_have_attribute("href", f"#inbox/job/{job['id']}/review")
                expect(check.locator("[data-papers-group]")).to_have_count(0)  # nothing ready, so nothing to group
                sides_shown = check.locator(".papers-check-side img")
                expect(sides_shown).to_have_count(4)
                sides_shown.first.scroll_into_view_if_needed()
                expect(sides_shown.first).to_have_js_property("complete", True)
                assert sides_shown.first.evaluate("image => image.naturalWidth > 0")
                page.screenshot(path=str(output / "sequence-papers-to-check.png"), full_page=True)
                panel.get_by_text("2 grouped papers").click()
                # A grouped paper opens in the app's file viewer (VIEWER); its Download saves the PDF itself.
                panel.get_by_role("link", name="Paper PDF").first.click()
                viewer = page.locator("dialog.file-viewer")
                with page.expect_download() as capture:
                    viewer.get_by_role("link", name="Download").click()
                viewer.get_by_role("button", name="Close the viewer").click()
                assert len(PdfReader(capture.value.path()).pages) == 3
                # Scan: the pages of the paper left out need a look, drawn open; grouped pages say so under All pages.
                stage("scan")
                attention = page.locator("[data-scan-pages-attention] .document-scan-page")
                expect(attention).to_have_count(3)
                expect(attention.locator("h4")).to_have_text(["Attachment page 8 · no label read", "Attachment page 9 · no label read", "Attachment page 10 · no label read"])
                page.locator("[data-scan-all-pages] > summary").click()
                every = page.locator("[data-scan-all-pages] .document-scan-page")
                expect(every).to_have_count(10)
                expect(page.locator("[data-scan-all-pages] [data-sequence-note]")).to_have_count(4)
                expect(page.locator("[data-scan-all-pages] [data-packet-note]")).to_have_count(6)
                expect(content).to_contain_text("3 with a label read, 4 identified by position or by hand, 3 with no label, 0 blank")
                expect(every.nth(0)).to_contain_text("Side 1 of 3 in copy 1's grouped paper.")
                expect(every.nth(1).locator("h4")).to_have_text("Attachment page 2 · identified by position or by hand")
                expect(every.nth(1)).to_contain_text("Copy 1, page 2")
                expect(every.nth(1)).to_contain_text("Identified by position: side 2 of the paper labeled on attachment page 1.")
                expect(every.nth(7)).to_contain_text("Not matched to a page of any paper yet")
                # Review: the assembled paper with missing pages comes first, open; the others are folded and drawn when opened.
                stage("review")
                expect(page.locator(".document-assembly")).to_have_count(1)
                expect(page.locator(".document-assembly").filter(has_text="Copy 3")).to_contain_text("Pages missing: 2, 3")
                page.locator("[data-scan-assemblies] > summary").click()
                expect(page.locator(".document-assembly")).to_have_count(3)
                expect(page.locator(".document-assembly").filter(has_text="Copy 1")).to_contain_text("Pages missing: none")
                # Back on the card, the Papers stage names the paper left out.
                page.get_by_role("link", name="Back to scan").click()
                expect(page).to_have_url(re.compile(rf"#inbox/job/{job['id']}$"))
                expect(card.locator("[data-pipeline-step='papers'] a")).to_have_attribute("title", re.compile(r"1 paper has more sides than the 3 per paper, so it was not grouped: pages 7–10 \(4 sides\)."))
                # Uneven labels are never asked about on opening, but the instructor may still say this paper has first-page labels only.
                expect(page.locator("#dialog")).not_to_be_visible()
                card.locator("[data-pipeline-step='assignment'] a").click()
                expect(content.locator('[data-label-mode="every_page"]')).to_contain_text("only first-page labels were read")
                # Desktop only (DESIGN-LANGUAGE, 2026-10-03): no overflow down to the app's 1180px floor.
                for width in (1440, 1180):
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Scan page overflow at {width}"
                page.set_viewport_size({"width": 1440, "height": 1100})
                # Undo, from the Papers page, where the grouping history is folded once the scan was grouped.
                stage("papers")
                panel.locator("[data-sequence-history] > summary").click()
                panel.get_by_role("button", name="Undo").click()
                expect(panel.locator("[data-sequence-run]")).to_contain_text("undone (2 papers ungrouped, 4 pages cleared)")
                stage("scan")
                expect(page.locator("[data-sequence-note]")).to_have_count(0)
                expect(page.locator("[data-packet-note]")).to_have_count(0)
                # Two recovery forms at once (dom-patch.js keyed lists): grouped again, the paper left out has three pages that need
                # a look. The instructor opens two of them and types into each, then records the first page's alias: that page leaves
                # the list, and each open form keeps its own page, its fold and what was typed in it.
                stage("papers")
                if not page.locator("[data-papers-other-ways]").evaluate("node => node.open"):
                    page.locator("[data-papers-other-ways] > summary").click()
                panel.get_by_role("button", name="Preview").click()
                panel.get_by_role("button", name="Group 2 papers and identify 4 pages").click()
                expect(page.locator("[data-papers-page] .students-page-group > h4")).to_have_text(["Held for you 1"])
                stage("scan")
                attention = page.locator("[data-scan-pages-attention] .document-scan-page")
                expect(attention).to_have_count(3)
                ids = {row["source_page_index"]: row["id"] for row in scans.review_item(job["message_id"])["pages"]}
                manifest = json.loads(documents.download(batch["id"], manifest=True))["pages"]
                alias = {(row["copy_number"], row["page_number"]): row["recovery_alias"] for row in manifest}
                stamp = {(row["copy_number"], row["page_number"]): row["stamp_id"] for row in manifest}
                recover = lambda index: page.locator(f'form[data-recover="{ids[index]}"]')
                typed = {9: alias[(3, 3)], 10: "A1-0000000000"}
                for index in (9, 10, 8):
                    card = attention.filter(has=page.locator("h4", has_text=re.compile(rf"^Attachment page {index} ·")))
                    # A page that needs a look has its recovery drawn open (2026-10-18).
                    expect(card.get_by_text("Recover its label from the printed code", exact=True)).to_be_visible()
                    assert recover(index).evaluate("form => form.closest('details').open"), index
                    recover(index).get_by_label("Code printed beside the label (A1-…)").fill(typed.get(index, alias[(3, 2)]))
                    recover(index).get_by_label("Why", exact=True).fill(f"Attachment page {index}")
                recover(8).get_by_role("button", name="Record correction").click()
                expect(attention).to_have_count(2)
                for index in (9, 10):
                    assert recover(index).evaluate("form => form.closest('details').open"), index
                    expect(recover(index).get_by_label("Code printed beside the label (A1-…)")).to_have_value(typed[index])
                    expect(recover(index).get_by_label("Why", exact=True)).to_have_value(f"Attachment page {index}")
                recover(9).get_by_role("button", name="Record correction").click()
                expect(attention).to_have_count(1)
                rows = {row["source_page_index"]: row for row in scans.review_item(job["message_id"])["pages"]}
                assert (rows[8]["page_stamp_id"], rows[9]["page_stamp_id"], rows[10]["page_stamp_id"]) == (stamp[(3, 2)], stamp[(3, 3)], None)
                expect(recover(10).get_by_label("Code printed beside the label (A1-…)")).to_have_value(typed[10])
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    # Pages that need a look (2026-10-02): a doubtful label is listed even while the pages without a label wait for Papers, and a
    # bubble sheet in a labeled scan is the Bubble sheets stage's, not a page with "no label read".
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(temporary)
        seed_course(app.test_client(), sections=[501, 502])
        documents, scans, inbox = app.extensions["documents"], app.extensions["document_scans"], app.extensions["inbox"]
        catalog = documents.catalog()
        assignment = documents.create_assignment(catalog["definitions"][0]["id"], "exam-1", "Exam 1")
        version = documents.register_version(assignment["id"], "A", (FIXTURES / "letter.pdf").read_bytes(),
                                             {"main.tex": (FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
        batch = documents.create_batch(version["id"], catalog["sections"][0]["id"], 3, str(uuid.uuid4()))
        assert documents.build_one()
        replaced = documents.create_batch(version["id"], catalog["sections"][0]["id"], 1, str(uuid.uuid4()))
        assert documents.build_one()
        old = PdfReader(io.BytesIO(documents.download(replaced["id"])))  # printed, then replaced by a reprint
        documents.transition(replaced["id"], "supersede")
        printed = PdfReader(io.BytesIO(documents.download(batch["id"])))
        plain = PdfReader(io.BytesIO((FIXTURES / "letter.pdf").read_bytes()))
        bubble = PdfReader(io.BytesIO(sheets_pdf([sheet({1: "A", 2: "C"})])))

        def submit(pages, name):
            writer = PdfWriter()
            for source in pages:
                writer.add_page(source)
            content = io.BytesIO()
            writer.write(content)
            job = inbox.submit({"action": "scan.receive"}, [{"filename": name, "content": content.getvalue()}], source="email", sender="scanner",
                               original=b"retained-email:" + content.getvalue(), idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
            while scans.process_one():
                pass
            return job

        # Two first-page-labeled exams, the second with an extra sheet (nothing is grouped automatically), then a page of a replaced print job.
        doubtful = submit([printed.pages[0], plain.pages[1], plain.pages[2], printed.pages[3], plain.pages[1], plain.pages[2], plain.pages[2], old.pages[0]], "doubtful.pdf")
        # The third exam labeled on every page, and a bubble sheet.
        with_sheet = submit([printed.pages[6], printed.pages[7], printed.pages[8], bubble.pages[0]], "with-sheet.pdf")
        steps = scans.review_item(doubtful["message_id"])["overview"]["steps"]
        assert any(step["kind"] == "todo" and step["tool"] == "exams" for step in steps), steps  # its pages wait for Papers
        assert any(step["kind"] == "check" and step["tool"] == "pages" for step in steps), steps  # and its label needs a look
        assert any(row.get("bubble_sheet") for row in scans.review_item(with_sheet["message_id"])["pages"])
        assert not [step for step in scans.review_item(with_sheet["message_id"])["overview"]["steps"] if step["tool"] in ("pages", "exams")]
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{doubtful['id']}/pages")
                content = page.locator("[data-scan-content]")
                expect(content).to_contain_text("1 with a label that needs review")
                attention = page.locator("[data-scan-pages-attention] .document-scan-page")
                expect(attention.locator("h4")).to_have_text(["Attachment page 8 · label from replaced paper"])
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{with_sheet['id']}/pages")
                expect(content).to_contain_text("3 with a label read, 0 identified by position or by hand, 1 with no label")
                expect(page.locator("[data-scan-pages-attention]")).to_have_count(0)
                page.locator("[data-scan-all-pages] > summary").click()
                expect(page.locator("[data-scan-all-pages] .document-scan-page")).to_have_count(4)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    # Evenly spaced labels on paper issued a label per page: grouped as soon as it is inspected, and opening the scan asks
    # whether the print job is labeled on the first page only. Confirming resolves the pages reported as missing.
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(temporary)
        seed_course(app.test_client(), sections=[501, 502])
        documents, scans, inbox = app.extensions["documents"], app.extensions["document_scans"], app.extensions["inbox"]
        catalog = documents.catalog()
        assignment = documents.create_assignment(catalog["definitions"][0]["id"], "exam-1", "Exam <1>")
        version = documents.register_version(assignment["id"], "A", (FIXTURES / "letter.pdf").read_bytes(), {"main.tex": (FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
        batch = documents.create_batch(version["id"], catalog["sections"][0]["id"], 6, str(uuid.uuid4()))
        assert documents.build_one()
        printed = PdfReader(io.BytesIO(documents.download(batch["id"])))
        plain = PdfReader(io.BytesIO((FIXTURES / "letter.pdf").read_bytes()))

        def even(copies):
            writer = PdfWriter()
            for copy in copies:
                writer.add_page(printed.pages[copy * 3])
                writer.add_page(plain.pages[1])
                writer.add_page(plain.pages[2])
            content = io.BytesIO()
            writer.write(content)
            job = inbox.submit({"action": "scan.receive"}, [{"filename": "even.pdf", "content": content.getvalue()}], source="email", sender="scanner",
                               original=b"retained-email:" + content.getvalue(), idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
            assert scans.process_one()
            return job

        first, second = even(range(3)), even(range(3, 6))
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text)
                        if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                # The Inbox lists a scan as a card with its flow graph, the same card its own page opens with (requirement, 2026-09-24).
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox")
                tile = page.locator(".inbox-scan-tile.has-card").filter(has=page.locator(f'h3 a[href="#inbox/job/{first["id"]}"]'))
                expect(tile.locator("h3")).to_have_text("Exam <1> · MATH 140 · Section 501")
                expect(tile.locator(".grading-card-heading .eyebrow")).to_have_text("9 pages · 3 papers")
                expect(tile.locator("[data-pipeline-step='papers'] a")).to_have_attribute("title", re.compile("This scan has one label per paper, but its print job expects a label on every page."))
                expect(tile.locator(".inbox-badge")).to_have_text("Needs you")
                expect(tile).to_contain_text("even.pdf")
                expect(page.locator(".inbox-scan-tile.has-card")).to_have_count(2)
                scan_page = f"http://127.0.0.1:{server.effective_port}/#inbox/job/{first['id']}"
                page.goto(scan_page)
                dialog, content, flow = page.locator("#dialog"), page.locator("[data-scan-content]"), page.locator("[data-scan-flow]")
                panel = page.locator("[data-papers-page]")
                stage = lambda key: flow.locator(f"[data-pipeline-step='{key}'] a").click()
                question = page.locator("[data-label-question]")
                # Opening the scan no longer asks in a dialog (2026-10-18): its next step leads to Papers, where the question is one
                # line with the dialog's two choices, naming the print job. Titles are data, shown escaped.
                expect(content.locator("[data-scan-card]")).to_be_visible()
                expect(page.locator(".inbox-scan-tile")).to_have_count(0)  # the list's cards are gone: this is the scan's own page
                expect(dialog).not_to_be_visible()
                expect(content.locator("[data-scan-card] [data-scan-next-action]")).to_have_attribute("href", f"#inbox/job/{first['id']}/papers")
                page.locator("[data-scan-card] [data-pipeline-step='papers'] a").click()
                expect(question).to_contain_text("One label per paper? This scan has one label per paper, but Exam <1> A · Section 501 expects a label on every page.")
                expect(question.get_by_role("button", name="Use first-page labels only")).to_be_visible()
                expect(question.get_by_role("button", name="Keep expecting a label on every page")).to_be_visible()
                question.get_by_text("What this changes").click()
                expect(question).to_contain_text("Exam <1> A · Section 501 was issued with a label on each of its 3 pages. This scan has 3 first-page labels, each 3 sides after the one before, and no label read on any other page.")
                page.screenshot(path=str(output / "sequence-one-label-offer.png"))
                page.set_viewport_size({"width": 1180, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "One-label question overflows at 1180"
                page.set_viewport_size({"width": 1440, "height": 1100})
                assert not page.locator("[data-papers-other-ways]").evaluate("node => node.open")  # Grouped on arrival: the other ways stay out of the way.
                expect(dialog).not_to_be_visible()
                # Grouped on arrival: the run history with its Undo is folded away.
                assert not panel.locator("[data-sequence-history]").evaluate("node => node.open")
                expect(panel.locator("[data-sequence-run]")).to_have_text(re.compile(r"automatically, from the even spacing of the labels, grouped 3 papers at 3 sides per paper"))
                expect(panel.get_by_text("3 grouped papers")).to_be_visible()
                stage("review")
                expect(page.locator(".document-assembly").first).to_contain_text("Pages missing: 2, 3")
                stage("assignment")
                page.get_by_role("button", name="Refresh page review").click()
                expect(content.locator('[data-label-mode="every_page"]')).to_contain_text("expects a label on each of its 3 pages, and only first-page labels were read.")
                expect(dialog).not_to_be_visible()
                # Unanswered, it is still asked on Papers. Declined: remembered for this scan.
                stage("papers")
                question.get_by_role("button", name="Keep expecting a label on every page").click()
                expect(page.locator("#toast")).to_contain_text("This scan will not ask again")
                expect(question).to_have_count(0)
                assert documents.get_batch(batch["id"])["label_mode"] == "every_page"
                page.goto(scan_page)
                page.reload()
                expect(page.locator("[data-scan-card]")).to_be_visible()
                expect(dialog).not_to_be_visible()
                # The instructor can still choose it from the Assignment page, and switch back.
                page.locator("[data-scan-card] [data-pipeline-step='assignment'] a").click()
                expect(content.locator('[data-label-mode="every_page"]')).to_be_visible()
                content.get_by_role("button", name="Use first-page labels only").click()
                expect(content.locator('[data-label-mode="first_page"]')).to_contain_text("is labeled on the first page only: nothing is expected on pages 2–3 of its copies.")
                stage("review")
                page.locator("[data-scan-assemblies] > summary").click()  # nothing is missing now: every paper is folded
                expect(page.locator(".document-assembly")).to_have_count(3)
                expect(page.locator(".document-assembly").first).to_contain_text("Pages missing: none")
                stage("assignment")
                content.get_by_role("button", name="Expect a label on every page again").click()
                expect(content.locator('[data-label-mode="every_page"]')).to_be_visible()
                stage("review")
                expect(page.locator(".document-assembly").first).to_contain_text("Pages missing: 2, 3")
                # Another scan of the same print job asks for itself. Confirmed, the conflicts resolve and the review gate stays.
                second_page = f"http://127.0.0.1:{server.effective_port}/#inbox/job/{second['id']}"
                page.goto(f"{second_page}/papers")
                question.get_by_role("button", name="Use first-page labels only").click()
                expect(question).to_have_count(0)
                expect(dialog).not_to_be_visible()
                page.goto(second_page)
                expect(page.locator("[data-scan-card] [data-pipeline-step=\"review\"] a")).to_have_attribute("aria-label", re.compile(r"^Review: (Not started|Needs you)\."))
                page.locator("[data-scan-card] [data-pipeline-step='assignment'] a").click()
                expect(content.locator('[data-label-mode="first_page"]')).to_be_visible()
                stage("review")
                page.locator("[data-scan-assemblies] > summary").click()
                expect(page.locator(".document-assembly").first).to_contain_text("Pages missing: none")
                found = documents.get_batch(batch["id"])
                assert found["label_mode"] == "first_page" and found["events"][-1]["kind"] == "label_mode", found["events"]
                page.goto(second_page)
                page.reload()
                expect(page.locator("[data-scan-card]")).to_be_visible()
                expect(dialog).not_to_be_visible()
                # The answer also gave each grouped exam's other sides their position as their page: a second, separately undoable run.
                page.locator("[data-scan-card] [data-pipeline-step='scan'] a").click()
                page.locator("[data-scan-all-pages] > summary").click()
                expect(page.locator("[data-scan-all-pages] .document-scan-page").nth(1)).to_contain_text("Identified by position: side 2 of the paper labeled on attachment page 1.")
                stage("papers")
                expect(panel.locator("[data-sequence-run]")).to_have_count(2)
                panel.locator("[data-sequence-history] > summary").click()
                panel.get_by_role("button", name="Undo").last.click()
                expect(panel.locator("[data-sequence-run]").last).to_contain_text("undone (3 papers ungrouped, 0 pages cleared)")
                # Deleting from the Inbox warns first and wants an explicit confirmation; nothing is erased, and it can be restored.
                # A scan's Start over and Delete sit in its '⋯' menu beside the state chip, away from its next step.
                page.goto(second_page)
                page.locator(".inbox-detail-heading").get_by_role("button", name="Scan actions").click()
                expect(page.get_by_role("menuitem", name="Start over…")).to_be_enabled()
                # Cancel sits on the scan's own page only, and asks first: a cancelled scan leaves Current for good.
                page.get_by_role("menuitem", name="Cancel scan…").click()
                cancelling = page.locator("#inbox-cancel-form")
                expect(cancelling).to_contain_text("Cancel this scan?")
                expect(cancelling).to_contain_text("Start over")
                cancelling.get_by_role("button", name="Keep it").click()
                expect(dialog).not_to_be_visible()
                assert inbox.get(second["id"])["status"] != "cancelled"
                page.locator(".inbox-detail-heading").get_by_role("button", name="Scan actions").click()
                page.get_by_role("menuitem", name="Delete from Inbox…").click()
                warning = page.locator("#inbox-delete-form")
                expect(warning).to_contain_text("Delete from the Inbox?")
                expect(warning).to_contain_text("This takes the submission out of the Inbox.")
                expect(warning).to_contain_text("it does not erase anything")
                warning.get_by_role("button", name="Keep it").click()
                expect(dialog).not_to_be_visible()
                assert inbox.get(second["id"])["deleted"] is None
                # The same confirmation is available on each scan's card in the list, from the same '⋯' menu in its header.
                page.get_by_role("link", name="Back to Teaching Inbox", exact=True).click()
                tile = page.locator(".inbox-scan-tile").filter(has=page.locator(f'h3 a[href="#inbox/job/{second["id"]}"]'))
                expect(tile.get_by_role("button", name="Delete scan", exact=True)).to_have_count(0)
                menu = tile.locator(".scan-card-corner").get_by_role("button", name="Scan actions", exact=True)
                delete_scan = tile.get_by_role("menuitem", name="Delete from Inbox…")
                expect(menu).to_be_visible()
                expect(tile.get_by_role("menuitem", name=re.compile("^Cancel (job|scan)"))).to_have_count(0)  # never one click from the list
                expect(tile.locator('a button')).to_have_count(0)
                # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): no overflow down to the 1180px floor.
                for width in (1440, 1180):
                    page.set_viewport_size({'width': width, 'height': 1100})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Scan tile overflow at {width}'
                    page.screenshot(path=f'test-results/inbox-scan-delete-{width}.png', full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1100})
                menu.focus()
                page.keyboard.press('Enter')
                expect(delete_scan).to_be_visible()
                delete_scan.focus()
                page.keyboard.press('Enter')
                expect(page).to_have_url(re.compile(r'/#inbox$'))
                expect(warning).to_contain_text('even.pdf')
                expect(warning).to_contain_text('its labeled pages no longer mark papers as seen')
                warning.get_by_role("button", name="Keep it").click()
                expect(dialog).not_to_be_visible()
                expect(menu).to_be_focused()
                assert inbox.get(second["id"])["deleted"] is None
                menu.click()
                delete_scan.click()
                warning.get_by_role("button", name="Delete from Inbox").click()  # Without the tick the browser refuses to submit.
                expect(dialog).to_be_visible()
                warning.get_by_label("Reason").fill("Scanned twice <b>")
                warning.get_by_label("I understand, delete it from the Inbox.").check()
                warning.get_by_role("button", name="Delete from Inbox").click()
                expect(page.locator("#toast")).to_contain_text("Deleted from the Inbox.")
                expect(page.locator("#toast").get_by_role("button", name="Restore", exact=True)).to_be_visible()
                expect(page.locator(".inbox-scan-tile.has-card")).to_have_count(1)  # Back in the list, which no longer shows it...
                assert inbox.get(first['id'])['deleted'] is None
                assert inbox.get(second['id'])['deleted'] is not None
                page.get_by_role("button", name=re.compile(r"^Deleted\s*1$")).click()  # ...except under its own tab.
                expect(page.locator(".inbox-job-row")).to_have_count(1)
                expect(page.locator(".inbox-job-row .inbox-badge")).to_have_text("Deleted")
                page.locator(".inbox-job-row").click()
                notice = page.locator(".inbox-deleted-notice")
                expect(notice).to_contain_text("Deleted from the Inbox on")
                expect(notice).to_contain_text("Reason given: Scanned twice <b>.")
                expect(page.locator(".document-scan-review")).to_have_count(0)
                notice.get_by_role("button", name="Restore to the Inbox").click()
                expect(page.locator(".document-scan-review")).to_be_visible()
                expect(page.locator(".inbox-deleted-notice")).to_have_count(0)
                assert inbox.get(second["id"])["deleted"] is None
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    print("Scan summary in the Inbox list and on the scan's page, delete from the Inbox with warning and restore, First-page labels: automatic grouping of evenly spaced labels and its undo, the one-label question (dismissed, declined, confirmed, switched back), recognition summary, suggested side count, preview, discarded stale preview, grouping, exam PDF, per-page notes, review gate, undo and mobile layout, keyed recovery forms, and the pages that need a look passed.")


if __name__ == "__main__":
    main()
