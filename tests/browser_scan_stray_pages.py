"""A page in no paper set aside, and a held paper grouped by hand, in the real browser (requirement, 2026-10-02).

The two dialogs ("Not part of any paper…" and "Choose its sides…") at 1440 and 1280 px (desktop only, one appearance): what they
show, what they send, what the pages say afterwards, and Undo. The Scan page offers setting a page aside only while its step says the page is
in no paper, and a blank back ticked past the pages issued keeps its place (paper printed two-sided). Synthetic scans only.

Run from the project root: python tests/browser_scan_stray_pages.py
"""
import io
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_course_pages import seed_course

FIXTURES = Path(__file__).resolve().parents[1] / "document_identity/fixtures"


def sheet(text):
    mark = io.BytesIO()
    canvas = Canvas(mark, pagesize=(612, 792))
    canvas.setFont("Helvetica", 34)
    canvas.drawString(90, 520, text)
    canvas.save()
    return PdfReader(mark).pages[0]


def seed(app):
    seed_course(app.test_client(), sections=[501, 502])
    documents, scans, inbox = app.extensions["documents"], app.extensions["document_scans"], app.extensions["inbox"]
    catalog = documents.catalog()
    plain = PdfReader(io.BytesIO((FIXTURES / "letter.pdf").read_bytes()))

    def submit(writer, name):
        content = io.BytesIO()
        writer.write(content)
        job = inbox.submit({"action": "scan.receive"}, [{"filename": name, "content": content.getvalue()}], source="cli", sender="test",
                           idempotency_key=str(uuid.uuid4()), instructor_review_required=True, large_scans=True)
        while scans.process_one():
            pass
        return job

    # Labeled on every page: a TA's header sheet on top of two complete papers.
    exam = documents.create_assignment(catalog["definitions"][0]["id"], "exam-2", "Exam 2")
    version = documents.register_version(exam["id"], "A", (FIXTURES / "letter.pdf").read_bytes(),
                                         {"main.tex": (FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
    batch = documents.create_batch(version["id"], catalog["sections"][1]["id"], 2, str(uuid.uuid4()))
    assert documents.build_one()
    writer = PdfWriter()
    writer.add_page(sheet("SECTION 502 - EXAM 2"))
    for page in PdfReader(io.BytesIO(documents.download(batch["id"]))).pages:
        writer.add_page(page)
    stray = submit(writer, "exam2-section502.pdf")
    # Labeled on the first page only: copy 2 holds a scratch sheet between its sides, so it does not add up and is held.
    quiz = documents.create_assignment(catalog["definitions"][0]["id"], "quiz-5", "Quiz 5")
    stamps = documents.register_stamp_pages(quiz["id"], 1)
    labels_batch = documents.create_batch(stamps["id"], catalog["sections"][0]["id"], 3, str(uuid.uuid4()))
    assert documents.build_one()
    labels = PdfReader(io.BytesIO(documents.download(labels_batch["id"])))
    writer = PdfWriter()
    for copy, sides in enumerate([[1, 2], [1, "S", 2], [1, 2]]):
        writer.add_page(labels.pages[copy])
        for side in sides:
            writer.add_page(sheet("scratch work") if side == "S" else plain.pages[side])
    held = submit(writer, "quiz5-section501.pdf")
    # Group the papers that add up, as the instructor's Group button does, so copy 2 is the one held.
    check = scans.papers_to_check(held["message_id"])
    scans.sequence.apply(held["message_id"], check["sides"], check["fingerprint"], turn=True)
    # Labeled on the first page (the instructor's answer for this print job) and printed two-sided: every paper ends on a blank
    # back, and copy 2 holds a scratch sheet as well.
    exam3 = documents.create_assignment(catalog["definitions"][0]["id"], "exam-3", "Exam 3")
    version3 = documents.register_version(exam3["id"], "A", (FIXTURES / "letter.pdf").read_bytes(),
                                          {"main.tex": (FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
    duplex_batch = documents.create_batch(version3["id"], catalog["sections"][0]["id"], 3, str(uuid.uuid4()))
    assert documents.build_one()
    documents.set_label_mode(duplex_batch["id"], "first_page")
    printed = PdfReader(io.BytesIO(documents.download(duplex_batch["id"])))
    writer = PdfWriter()
    for copy, sides in enumerate([[1, 2, None], [1, "S", 2, None], [1, 2, None]]):
        writer.add_page(printed.pages[copy * 3])
        for side in sides:
            if side is None:
                writer.add_blank_page(612, 792)
            else:
                writer.add_page(sheet("scratch work") if side == "S" else plain.pages[side])
    duplex = submit(writer, "exam3-section501.pdf")
    check = scans.papers_to_check(duplex["message_id"])
    scans.sequence.apply(duplex["message_id"], check["sides"], check["fingerprint"], turn=True)
    return scans, stray, held, duplex


def open_steps(scans, message_id):
    """The scan's open steps, its review aside (it passes by itself; nothing here is about it)."""
    return [step["text"] for step in scans.review_item(message_id)["overview"]["steps"]
            if step["kind"] in ("todo", "check") and step["action"] != "grading_review"]


def main():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(temporary)
        scans, stray, held, duplex = seed(app)
        server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f"http://127.0.0.1:{server.effective_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                # The second pass, at the narrowest desktop width, takes the other branches (the default reason, a side set aside from
                # its own button).
                for width, height in ((1440, 900), (1280, 900)):
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    page.set_default_timeout(10000)
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("console", lambda message: errors.append(message.text)
                            if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
                    fits = lambda: page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    content = page.locator("[data-scan-content]")
                    dialog = page.locator("#dialog")

                    # 1. The stray sheet: its step says what it is, its Scan page points to Papers (2026-10-18: setting a page aside
                    # lives on one stage), and Papers offers "Not part of any paper…".
                    page.goto(f"{address}/#inbox/job/{stray['id']}/pages")
                    expect(content.locator("[data-scan-next-step]")).to_contain_text(
                        "1 written page carries no label and belongs to no paper (page 1). If one is a student's page, recover its label from "
                        "its printed alias; otherwise set it aside.")
                    attention = page.locator("[data-scan-pages-attention] .document-scan-page")
                    expect(attention).to_have_count(1)
                    expect(attention.locator("h4")).to_have_text("Attachment page 1 · no label read")
                    expect(attention.get_by_role("button", name="Not part of any paper…")).to_have_count(0)
                    attention.get_by_role("link", name="Set it aside on Papers").click()
                    expect(page).to_have_url(re.compile(rf"#inbox/job/{stray['id']}/papers$"))
                    content.get_by_role("button", name="Not part of any paper…").click()
                    form = dialog.locator("#scan-set-aside")
                    expect(form.locator("#dialog-title")).to_have_text("Not part of any paper?")
                    expect(form.get_by_label("Section header sheet")).to_be_checked()
                    box = form.bounding_box()
                    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (width, box)
                    expect(form.get_by_role("button", name="Set aside")).to_be_in_viewport()
                    page.screenshot(path=str(output / f"stray-set-aside-dialog-{width}.png"))
                    if width == 1440:
                        # The instructor's own words: "Something else" asks for them before anything is sent.
                        form.get_by_label("Something else").check()
                        other = form.get_by_label("In your words")
                        expect(other).to_be_visible()
                        form.get_by_role("button", name="Set aside").click()
                        expect(form).to_be_visible()
                        other.fill("TA's section header")
                    form.get_by_role("button", name="Set aside").click()
                    expect(dialog).not_to_be_visible()
                    reason = "TA's section header" if width == 1440 else "Section header sheet"
                    listed = content.locator("[data-set-aside-list]")
                    expect(listed.locator("li")).to_have_count(1)
                    expect(listed.locator("li")).to_contain_text(f"Page 1{reason}")
                    expect(content.locator("[data-papers-strays]")).to_have_count(0)
                    expect(listed.get_by_role("button", name="Undo")).to_be_focused()
                    assert open_steps(scans, stray["message_id"]) == [], open_steps(scans, stray["message_id"])
                    assert fits(), f"Papers page overflow at {width}"
                    page.screenshot(path=str(output / f"stray-set-aside-{width}.png"), full_page=True)
                    # The Scan page says so, and where the list is.
                    page.goto(f"{address}/#inbox/job/{stray['id']}/pages")
                    expect(page.locator("[data-scan-pages-attention]")).to_have_count(0)
                    expect(content).to_contain_text("1 with no label (1 set aside)")
                    expect(content.locator("[data-set-aside-elsewhere]")).to_contain_text("1 page set aside as part of no paper: see them on Papers.")
                    assert fits(), f"Scan page overflow at {width}"
                    # Undo puts the page back where it was, with its step.
                    content.locator("[data-set-aside-elsewhere]").get_by_role("link", name="see them on Papers").click()
                    content.locator("[data-set-aside-list]").get_by_role("button", name="Undo").click()
                    expect(content.get_by_role("button", name="Not part of any paper…")).to_have_count(1)
                    expect(content.locator("[data-set-aside-list]")).to_have_count(0)
                    assert len(open_steps(scans, stray["message_id"])) == 1

                    # 2. The held paper: Choose its sides…, ticked in order, grouped as its copy, and undone. Its Scan page offers no
                    # "Not part of any paper…": its pages without a label wait for Papers, where the grouping shows which are in none.
                    page.goto(f"{address}/#inbox/job/{held['id']}/pages")
                    expect(page.locator("[data-scan-pages-attention] .document-scan-page").first).to_be_visible()
                    page.locator("[data-scan-all-pages] > summary").click()
                    expect(page.locator("[data-scan-pages] .document-scan-page")).to_have_count(10)
                    expect(page.get_by_role("button", name="Not part of any paper…")).to_have_count(0)
                    page.goto(f"{address}/#inbox/job/{held['id']}/papers")
                    check = page.locator("[data-papers-page]")
                    held_group = check.locator(".students-page-group").filter(has=page.locator("h4", has_text="Held for you"))
                    expect(held_group.locator("> .field-hint")).to_contain_text(
                        "Group it by hand under Other ways to group, or accept what was read as a paper with pages missing on Review.")
                    expect(held_group.locator("[data-papers-item] h5")).to_have_text("Copy 2 pages 4–7")
                    # Choose its sides… is one of the other ways to group, open while there is nothing to group the usual way.
                    other_ways = check.locator("[data-papers-other-ways]")
                    expect(other_ways).to_have_attribute("open", "")
                    expect(held_group.get_by_role("button", name="Choose its sides…")).to_have_count(0)
                    other_ways.get_by_role("button", name="Choose its sides…").click()
                    form = dialog.locator("#papers-choose")
                    expect(form.locator("#dialog-title")).to_have_text("Choose the sides of Copy 2")
                    sides = form.locator("[data-choose-side]")
                    expect(sides).to_have_count(4)
                    expect(sides.locator(".checkbox-field")).to_have_text(["Side 1 · attachment page 4 · first page", "Side 2 · attachment page 5",
                                                                          "Side 3 · attachment page 6", "Side 4 · attachment page 7"])
                    expect(sides.nth(0).locator("input")).to_be_checked()
                    expect(sides.nth(0).locator("input")).to_be_disabled()
                    summary = form.locator("[data-choose-summary]")
                    expect(summary).to_have_text("1 side ticked: page 1 of copy 2. Papers here have 3 sides. Tick at least one more side.")
                    group = form.get_by_role("button", name="Group as Copy 2")
                    expect(group).to_be_disabled()
                    box = page.locator("#dialog").bounding_box()
                    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (width, box)
                    sides.nth(1).locator("input").check()
                    sides.nth(3).locator("input").check()
                    expect(summary).to_have_text("3 sides ticked: pages 1–3 of copy 2. Papers here have 3 sides.")
                    expect(form.locator("[data-choose-page]")).to_have_text(["Page 1", "Page 2", "Left as scanned", "Page 3"])
                    expect(form.get_by_label("A student added a sheet")).to_be_checked()
                    page.screenshot(path=str(output / f"held-choose-dialog-{width}.png"))
                    group.click()
                    expect(dialog).not_to_be_visible()
                    by_hand = check.locator("[data-papers-by-hand]")
                    expect(by_hand.locator("[data-papers-item] .field-hint").first).to_have_text("Grouped by hand: A student added a sheet. Undo")
                    expect(by_hand.locator("figcaption")).to_have_text(["Page 1 · attachment page 4", "Page 2 · attachment page 5",
                                                                        "Page 3 · attachment page 7", "Attachment page 6"])
                    expect(by_hand.get_by_role("button", name="Undo")).to_be_focused()
                    expect(check.locator(".students-page-group > h4")).to_have_text(["Grouped by hand 1"])
                    assert fits(), f"Papers page overflow at {width}"
                    page.screenshot(path=str(output / f"held-grouped-{width}.png"), full_page=True)
                    if width == 1440:
                        # Undone like any grouping: the paper is held again, as scanned.
                        by_hand.get_by_role("button", name="Undo").click()
                        expect(check.locator(".students-page-group > h4")).to_have_text(["Held for you 1"])
                        expect(check.get_by_role("button", name="Choose its sides…")).to_be_visible()
                    else:
                        # The side left as scanned is set aside from its own button: nothing is left out of the grouping then.
                        by_hand.get_by_role("button", name="Not part of any paper…").click()
                        form = dialog.locator("#scan-set-aside")
                        expect(form.locator(".dialog-header p")).to_have_text("Attachment page 6")
                        form.get_by_label("Scratch paper").check()
                        form.get_by_role("button", name="Set aside").click()
                        expect(dialog).not_to_be_visible()
                        expect(check.locator("[data-set-aside-list] li")).to_contain_text("Page 6Scratch paper")
                        expect(check.locator("[data-papers-facts]")).to_contain_text("Nothing is left out of the grouping.")
                        assert open_steps(scans, held["message_id"]) == [], open_steps(scans, held["message_id"])
                        assert fits(), f"Papers page overflow at {width}"
                        page.screenshot(path=str(output / f"held-left-set-aside-{width}.png"), full_page=True)
                    if width == 1440:
                        # 3. Printed two-sided: the blank back keeps its place past the 3 pages issued and becomes no page, as on every
                        # other paper of the scan; a written side there is refused before anything is sent.
                        page.goto(f"{address}/#inbox/job/{duplex['id']}/papers")
                        check.get_by_role("button", name="Choose its sides…").click()
                        form = dialog.locator("#papers-choose")
                        sides, summary = form.locator("[data-choose-side]"), form.locator("[data-choose-summary]")
                        expect(sides).to_have_count(5)
                        for index in (1, 3, 4):
                            sides.nth(index).locator("input").check()
                        expect(form.locator("[data-choose-page]")).to_have_text(["Page 1", "Page 2", "Left as scanned", "Page 3", "Blank · no page"])
                        expect(summary).to_have_text("4 sides ticked: pages 1–3 of copy 2, and 1 blank side. It was issued 3 pages. Papers here have 4 sides.")
                        group = form.get_by_role("button", name="Group as Copy 2")
                        expect(group).to_be_enabled()
                        sides.nth(2).locator("input").check()
                        expect(summary).to_have_text("5 sides ticked: pages 1–4 of copy 2, and 1 blank side. It was issued 3 pages. Papers here have 4 sides. "
                                                     "Untick 1 side: page 4 is past the pages issued.")
                        expect(group).to_be_disabled()
                        page.screenshot(path=str(output / f"held-duplex-over-{width}.png"))
                        sides.nth(2).locator("input").uncheck()
                        group.click()
                        expect(dialog).not_to_be_visible()
                        expect(check.locator("[data-papers-by-hand] figcaption")).to_have_text(
                            ["Page 1 · attachment page 5", "Page 2 · attachment page 6", "Page 3 · attachment page 8", "Blank · attachment page 9",
                             "Attachment page 7"])
                        expect(check.locator("[data-papers-facts]")).to_have_text("4 sides per paper: 2 papers grouped, and 1 by hand.")
                    assert not errors, errors
                    context.close()
                browser.close()
        finally:
            server.close()


if __name__ == "__main__":
    main()
