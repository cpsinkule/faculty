"""PICTURE-SEARCH on the real Inbox page (requirement, 2026-10-10): words that describe
something show the pages and pictures nearest them above the list: first what Faculty's own words say (a question bank
picture), then the scanned pages that look like it; each opens in the viewer and links to where it belongs; a filename search
shows no strip; Clear search takes it away. Drawn pages on synthetic data and a stand-in embedder: no model is reached.
PICTURE_SEARCH_SCREENSHOTS=<folder on /mnt> keeps the pictures taken at 1440 and 1280."""
import io
import os
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from app import create_app
from browser_course_pages import seed_course
from faculty_embedding import client as embedding
from rosters import connect
from test_storage import HEADERS

KINDS = ("graph", "writing", "matrix")


def page_picture(kind, variant):
    """A letter page at scan proportions; its first pixel says which kind it is, for the stand-in."""
    image = Image.new("L", (850, 1100), 255)
    pen = ImageDraw.Draw(image)
    pen.text((70, 60), f"Question {variant + 1}", fill=0)
    if kind == "graph":
        pen.line((150, 250, 150, 800), fill=0, width=4)
        pen.line((150, 800, 720, 800), fill=0, width=4)
        pen.line((190, 300 + 20 * variant, 680, 740), fill=0, width=5)
        pen.line((190, 740, 680, 300 + 30 * variant), fill=0, width=5)
    elif kind == "matrix":
        for row in range(3):
            for column in range(4):
                pen.rectangle((200 + column * 110, 300 + row * 110, 280 + column * 110, 380 + row * 110), outline=0, width=3)
        pen.line((180, 280, 180, 620), fill=0, width=5)
        pen.line((660, 280, 660, 620), fill=0, width=5)
    else:
        for line in range(14):
            pen.line((90, 220 + line * 55, 760 - (line * 37 + variant * 50) % 300, 220 + line * 55), fill=110, width=6)
    image.putpixel((0, 0), 181 + KINDS.index(kind))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def unit(position, size=8):
    vector = np.zeros(size, dtype=np.float32)
    vector[position] = 1
    return vector


def picture_vectors(pictures, timeout=20, path=None):
    return np.stack([unit(Image.open(io.BytesIO(data)).getpixel((0, 0)) - 181) for data in pictures])


def text_vectors(texts, role="query", timeout=1.5, path=None):
    rows = []
    for text in texts:
        row = sum((unit(index) for index, kind in enumerate(KINDS) if kind in text.lower()), np.zeros(8, dtype=np.float32))
        rows.append(row / np.linalg.norm(row) if row.any() else unit(7))
    return np.stack(rows)


def main():
    shots = os.environ.get("PICTURE_SEARCH_SCREENSHOTS")
    with tempfile.TemporaryDirectory() as folder, patch.object(embedding, "configured", return_value=True), \
            patch.object(embedding, "picture_vectors", picture_vectors), patch.object(embedding, "text_vectors", text_vectors):
        app = create_app(str(Path(folder) / "data"))
        client = app.test_client()
        course = seed_course(client, sections=[507, 508])
        # Course material with words of its own: a bank question's picture, found by what the question says.
        question = client.post(f"/api/question-bank/definitions/{course['definition_id']}/questions", headers=HEADERS, json={
            "request_id": "w1", "content": {"kind": "free_response", "statement": "Sketch the supply and demand graph and mark the equilibrium.", "points": 3}}).json
        kept = client.post(f"/api/question-bank/questions/{question['id']}/pictures", headers=HEADERS, data={
            "request_id": "p1", "caption": "A supply and demand graph.", "file": (io.BytesIO(page_picture("graph", 9)), "graph.png")})
        assert kept.status_code == 200, kept.json
        inbox, scans, search = app.extensions["inbox"], app.extensions["document_scans"], app.extensions["picture_search"]
        job = inbox.submit({"action": "scan.receive"}, [{"filename": "quiz-3-section-507.pdf", "content": b"%PDF-fixture " + uuid.uuid4().bytes}],
                           source="email", sender="scanner", original=b"retained email", idempotency_key=str(uuid.uuid4()), authenticated=True,
                           instructor_review_required=True)
        source = next(a for a in job["artifacts"] if a["role"] == "input")
        kinds = ["writing", "graph", "writing", "matrix", "graph", "writing", "graph", "writing"]
        for index, kind in enumerate(kinds, 1):
            scans._retain_page(job, source, {"index": index, "candidates": [], "blank": False, "pdf": b"%PDF page " + uuid.uuid4().bytes,
                                             "png": page_picture(kind, index), "decoder": {}})
        with connect(app.extensions["rosters"].path) as db:
            for table in ("document_decode_jobs", "scan_alias_ocr", "scan_version_sorts", "scan_unlabeled_runs"):
                db.execute(f"UPDATE {table} SET status='complete' WHERE message_id=? AND status IN ('pending','running')", (job["message_id"],))
        while search.process_one():
            pass
        assert search.status()["by_source"] == {"bank": 1, "scan": len(kinds)}, search.status()
        server = create_server(app, host="127.0.0.1", port=0, threads=12)
        threading.Thread(target=server.run, daemon=True).start()
        base = f"http://127.0.0.1:{server.effective_port}"
        errors = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("window.cspViolations=[]; document.addEventListener('securitypolicyviolation', e => cspViolations.push(e.violatedDirective));")
                page.goto(base + "/#inbox")
                box = page.locator("#inbox-search")
                expect(box).to_have_attribute("placeholder", "Search filename, course, sender, job ID, or describe a page")
                strip = page.locator("details.picture-strip")
                expect(strip).to_have_count(0)

                # Words that describe a page: no submission is called that, and the pages that look like it are the answer.
                box.fill("supply and demand graph")
                box.press("Enter")
                expect(strip).to_have_count(1, timeout=15000)
                expect(strip.locator("summary")).to_contain_text("Pages and pictures for “supply and demand graph”")
                expect(strip.locator("figure")).to_have_count(4)  # the bank picture and the three graphs: nothing far is added
                expect(strip).to_have_attribute("open", "")
                first = strip.locator("figure").nth(0)
                # What Faculty's own words say comes first: the bank picture; then the scans that look like a graph (pages 2, 5, 7).
                expect(first.locator("figcaption a")).to_have_text("A supply and demand graph.")
                expect(first.locator("figcaption small")).to_have_text("Question bank · MATH 140")
                assert sorted(strip.locator("figure figcaption a").all_inner_texts()[1:4]) == ["Scan page 2", "Scan page 5", "Scan page 7"]
                page.wait_for_function("[...document.querySelectorAll('.picture-strip-page img')].slice(0, 4).every(i => i.complete && i.naturalWidth > 0)")
                # The strip survives the list's own polls: the same nodes, not a redraw.
                page.evaluate("document.querySelector('.picture-strip-page').dataset.kept = '1'")
                page.wait_for_timeout(5000)
                expect(strip.locator("figure").nth(0)).to_have_attribute("data-kept", "1")
                if shots:
                    page.screenshot(path=str(Path(shots) / "picture-search-1440.png"), full_page=True)
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.screenshot(path=str(Path(shots) / "picture-search-1280.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "the strip must not widen the page"

                # A picture opens in the viewer: the bank's picture, then a scanned page at reading size.
                viewer = page.locator("dialog.file-viewer")
                for number in (0, 1):
                    strip.locator("figure").nth(number).locator("a").first.click()
                    expect(viewer).to_be_visible(timeout=15000)
                    expect(viewer.locator("img")).to_be_visible(timeout=15000)
                    if shots and number:
                        page.screenshot(path=str(Path(shots) / "picture-search-viewer-1280.png"))
                    page.keyboard.press("Escape")
                    expect(viewer).to_be_hidden()

                # A filename finds its submission and asks for no pages.
                box.fill("quiz-3-section-507.pdf")
                box.press("Enter")
                expect(page.locator("#inbox-search-summary")).to_contain_text("matching “quiz-3-section-507.pdf”", timeout=15000)
                expect(strip).to_have_count(0)
                # Words that also match a submission: the list is the answer, and the pages wait folded on one line.
                box.fill("quiz graph")
                box.press("Enter")
                expect(page.locator("#inbox-search-summary")).to_contain_text("matching “quiz graph”", timeout=15000)
                box.fill("matrix")
                box.press("Enter")
                expect(strip.locator("summary")).to_contain_text("Pages and pictures for “matrix”", timeout=15000)
                expect(strip.locator("figure figcaption a").nth(0)).to_have_text("Scan page 4")
                # Its caption opens the scan's pages; Clear search takes the strip away.
                page.get_by_role("button", name="Clear search").click()
                expect(strip).to_have_count(0, timeout=15000)
                box.fill("matrix")
                box.press("Enter")
                expect(strip).to_have_count(1, timeout=15000)
                strip.locator("figure figcaption a").nth(0).click()
                page.wait_for_function(f"location.hash === '#inbox/job/{job['id']}/pages'")
                # By a picture: the instructor's own file finds the pages that look like it; nothing of it is kept.
                page.goto(base + "/#inbox")
                expect(box).to_be_visible(timeout=15000)
                box.fill("matrix")
                box.press("Enter")
                expect(strip).to_have_count(1, timeout=15000)
                page.set_input_files("[data-inbox-like]", {"name": "my photo.png", "mimeType": "image/png", "buffer": page_picture("matrix", 3)})
                expect(strip.locator("summary")).to_contain_text("Pages and pictures like “my photo.png”", timeout=15000)
                expect(strip.locator("figure figcaption a").nth(0)).to_have_text("Scan page 4")  # the matrix page
                expect(strip).to_have_attribute("open", "")
                expect(box).to_have_value("")  # a picture is its own search: the list is everything again
                expect(page.locator(".inbox-job-list")).to_contain_text("quiz-3-section-507.pdf")
                assert search.status()["by_source"] == {"bank": 1, "scan": len(kinds)}, search.status()
                if shots:
                    page.screenshot(path=str(Path(shots) / "picture-search-like-1280.png"), full_page=True)
                page.get_by_role("button", name="Clear search").click()
                expect(strip).to_have_count(0, timeout=15000)
                violations = page.evaluate("window.cspViolations")
                assert not violations, violations
                browser.close()
            assert not errors, errors
        finally:
            server.close()
    print("Picture search browser checks passed.")


if __name__ == "__main__":
    main()
