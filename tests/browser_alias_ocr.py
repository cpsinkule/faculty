"""Real review UI with a controlled OCR result; no production data or GPU."""
import io
import json
import re
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader, PdfWriter
from waitress.server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from browser_course_pages import seed_course
from teach_inbox.service import sha


def main():
    with tempfile.TemporaryDirectory() as temporary:
        app=create_app(temporary); seed_course(app.test_client(),sections=[507],multiple_sections=False)
        docs=app.extensions["documents"]; scans=app.extensions["document_scans"]; inbox=app.extensions["inbox"]
        scans.alias_ocr.enabled=True
        catalog=docs.catalog(); fixtures=Path(__file__).resolve().parents[1]/"document_identity/fixtures"
        assignment=docs.create_assignment(catalog["definitions"][0]["id"],"ocr","OCR <test>")
        version=docs.register_version(assignment["id"],"A",(fixtures/"letter-corners.pdf").read_bytes(),
            {"main.tex":(fixtures/"letter-corners.tex").read_bytes()},{"fixture":"letter-corners"})
        batch=docs.create_batch(version["id"],catalog["sections"][0]["id"],1,"browser-ocr")
        docs.build_one()
        entry=json.loads(docs.download(batch["id"],manifest=True))["pages"][0]
        writer=PdfWriter(); writer.add_page(PdfReader(fixtures/"letter-corners.pdf").pages[0])
        data=io.BytesIO(); writer.write(data)
        job=inbox.submit({"action":"scan.receive"},[{"filename":"scan.pdf","content":data.getvalue()}],
            source="email",sender="scanner",instructor_review_required=True)
        scans.process_one()
        with patch("document_identity.alias_ocr.recognize_aliases",side_effect=RuntimeError("test failure")):
            scans.alias_ocr.process_one()
        server=create_server(app,host="127.0.0.1",port=0)
        threading.Thread(target=server.run,daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                page=browser.new_page(viewport={"width":1200,"height":1000},accept_downloads=True)
                errors=[]; page.on("pageerror",lambda error:errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{job['id']}/pages")  # the Scan stage: every page and its recovery
                # The page whose label could not be read, nor its printed alias, needs a look: it is drawn open, with its retry.
                expect(page.locator("[data-scan-pages-attention] .document-scan-page")).to_have_count(1)
                # A fold the instructor opened stays open, with what was typed in it, when the page redraws (bugs-frontend-7).
                # Its recovery is drawn open on a page that needs a look (2026-10-18: not three folds deep).
                assert page.locator("form[data-recover]").evaluate("form => form.closest('details').open")
                page.locator("form[data-recover]").get_by_label("Why", exact=True).fill("Typed before the redraw")
                page.get_by_role("button",name="Read the code again").click()
                expect(page.locator("[data-alias-ocr-status]")).to_contain_text("Reading the code printed beside its label")
                assert page.locator("form[data-recover]").evaluate("form => form.closest('details').open")
                expect(page.locator("form[data-recover]").get_by_label("Why", exact=True)).to_have_value("Typed before the redraw")
                page.get_by_text("Recover its label from the printed code",exact=True).click()  # folded again by the instructor only
                def recognize(content):
                    return {"source_sha256":sha(content),"model_id":"browser-test-double","needs_review":True,
                            "reads":[{"text":entry["recovery_alias"],"truncated":False},
                                     {"text":"<img src=x onerror=alert(1)>","truncated":False}]}
                with patch("document_identity.alias_ocr.recognize_aliases",side_effect=recognize):
                    assert inbox.work_once()
                page.get_by_role("button",name="Refresh page review").click()
                # Identified, the page no longer needs a look: it is under All pages, folded until opened.
                expect(page.locator("[data-scan-pages-attention]")).to_have_count(0)
                page.locator("[data-scan-all-pages] > summary").click()
                form=page.locator("form[data-recover]")
                expect(page.locator(".document-scan-page h4")).to_contain_text("label read")
                expect(page.locator("[data-alias-ocr-status]")).to_have_text(f"Identified from the code printed beside its label, {entry['recovery_alias']}.")
                expect(form).not_to_be_visible()
                expect(page.get_by_text("Check the code that was read",exact=True)).to_have_count(0)
                expect(page.locator(".document-scan-page")).not_to_contain_text("Not matched to a page of any paper yet")
                expect(page.locator(".document-scan-page")).to_contain_text("OCR <test>")
                assert not page.locator(".document-scan-page img, .document-scan-page test").count()
                # The report opens in the app's file viewer (VIEWER); its Download saves the report itself.
                page.get_by_role("link",name="Label reading report",exact=True).click()
                viewer=page.locator("dialog.file-viewer")
                expect(viewer.locator(".file-viewer-text")).to_contain_text("needs_review")
                with page.expect_download() as capture:
                    viewer.get_by_role("link",name="Download").click()
                viewer.get_by_role("button",name="Close the viewer").click()
                report=json.loads(Path(capture.value.path()).read_bytes())
                assert report["review"]["needs_review"] is False
                assert report["review"]["instructor_review_required"] is True
                assert scans.review_item(job["message_id"])["pages"][0]["page_stamp_id"] == entry["stamp_id"]
                for width in (1280,1200):  # desktop only (DESIGN-LANGUAGE): it fits down to the 1180 px floor
                    page.set_viewport_size({"width":width,"height":1000})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"),f"OCR review overflow at {width}"
                Path("test-results").mkdir(exist_ok=True)
                page.screenshot(path="test-results/alias-ocr-automatic.png",full_page=True)
                # Manual corrections remain available but are never required
                # to accept an exact automatic OCR match.
                page.get_by_text("Recover its label from the printed code",exact=True).click()
                form.get_by_label("Code printed beside the label (A1-…)").fill(entry["recovery_alias"])
                form.get_by_label("Why", exact=True).fill("Instructor rechecked the printed label")
                form.get_by_role("button",name="Record correction").click()
                expect(page.locator(".document-scan-page h4")).to_contain_text("identified by position or by hand")
                expect(page.locator("[data-alias-ocr-status]")).to_have_count(0)
                assert scans.review_item(job["message_id"])["pages"][0]["page_stamp_id"] == entry["stamp_id"]
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{job['id']}")
                expect(page.locator("[data-scan-card] [data-pipeline-step=\"review\"] a")).to_have_attribute("aria-label", re.compile(r"^Review: (Not started|Needs you)\."))  # the review gate stays
                # A scan's files, command and history wait behind one disclosure, closed until asked for.
                record = page.locator("[data-inbox-record]")
                expect(record.locator("> summary")).to_have_text("Files, submission details and history")
                assert not record.evaluate("node => node.open")
                record.locator("> summary").click()
                group=page.locator('[data-inbox-file-group="ocr"]')
                expect(group).to_be_visible()
                expect(group).not_to_have_attribute("open", "")
                # A scan whose pages all lack a label leaves them to Students, but a failed reading is still a look here: its Retry
                # is only on the page card, and Home counts it until then (2026-10-06, a reading cut off by the out-of-memory killer).
                writer=PdfWriter()
                for index in (1,2): writer.add_page(PdfReader(fixtures/"letter-corners.pdf").pages[index])
                data=io.BytesIO(); writer.write(data)
                unlabeled=inbox.submit({"action":"scan.receive"},[{"filename":"unlabeled.pdf","content":data.getvalue()}],
                    source="email",sender="scanner",instructor_review_required=True)
                scans.process_one()
                with patch("document_identity.alias_ocr.recognize_aliases",side_effect=RuntimeError("test failure")):
                    while scans.alias_ocr.process_one(): pass
                assert [p["alias_ocr"]["can_retry"] for p in scans.review_item(unlabeled["message_id"])["pages"]] == [True,True]
                page.goto(f"http://127.0.0.1:{server.effective_port}/#inbox/job/{unlabeled['id']}/pages")
                attention=page.locator("[data-scan-pages-attention] .document-scan-page")
                expect(attention).to_have_count(2)
                expect(attention.get_by_role("button",name="Read the code again")).to_have_count(2)
                attention.first.get_by_role("button",name="Read the code again").click()
                expect(attention).to_have_count(1)  # queued again, it waits under All pages like any page without a label
                assert [p["alias_ocr"]["status"] for p in scans.review_item(unlabeled["message_id"])["pages"]] == ["pending","failed"]
                page.locator("[data-scan-all-pages] > summary").click()
                expect(page.locator("[data-scan-pages] [data-alias-ocr-status]").first).to_contain_text("Reading the code printed beside its label")
                assert not errors,errors
                browser.close()
        finally:
            server.close()
    print("Automatic OCR identity without confirmation, optional manual correction, explicit retry, report download, escaping, review gate and responsive UI checks passed.")


if __name__ == "__main__":
    main()
