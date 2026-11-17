"""Exact OCR aliases identify pages automatically without approving scan actions."""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from app import create_app
from document_identity import tokens
from rosters import connect
from teach_inbox.service import sha
from test_storage import temporary_storage, HEADERS

FIXTURES = Path(__file__).resolve().parents[1] / "document_identity/fixtures"


class AliasOcrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = temporary_storage(self.temp.name)
        self.app = create_app(storage=self.storage)
        self.client = self.app.test_client(); self.client.get("/").close()
        response = self.client.post("/api/courses", json={"name":"OCR", "code":"MATH 140", "term":"Fall",
            "year":2026, "sections":[507], "is_current":True}, headers=HEADERS)
        self.assertEqual(response.status_code, 201)
        self.storage.reconcile_all()
        self.docs = self.app.extensions["documents"]
        self.scans = self.app.extensions["document_scans"]
        self.inbox = self.app.extensions["inbox"]
        self.ocr = self.scans.alias_ocr; self.ocr.enabled = True
        catalog = self.docs.catalog()
        assignment = self.docs.create_assignment(catalog["definitions"][0]["id"], "ocr", "OCR test")
        canonical = (FIXTURES / "letter-corners.pdf").read_bytes()
        version = self.docs.register_version(assignment["id"], "A", canonical,
            {"main.tex":(FIXTURES / "letter-corners.tex").read_bytes()}, {"fixture":"letter-corners"})
        self.batch = self.docs.create_batch(version["id"], catalog["sections"][0]["id"], 1, "ocr-test")
        self.assertTrue(self.docs.build_one())
        self.entries = json.loads(self.docs.download(self.batch["id"], manifest=True))["pages"]
        writer = PdfWriter(); writer.add_page(PdfReader(io.BytesIO(canonical)).pages[0])
        data = io.BytesIO(); writer.write(data); self.pdf = data.getvalue()

    def intake(self, authenticated=True):
        job = self.inbox.submit({"action":"scan.receive"}, [{"filename":"scan.pdf", "content":self.pdf}],
            source="email", sender="scanner", idempotency_key=str(uuid.uuid4()),
            authenticated=authenticated, instructor_review_required=True)
        self.scans.process_one()
        return job

    def page(self, job):
        return self.scans.review_item(job["message_id"])["pages"][0]

    def result(self, content, *texts):
        return {"source_sha256":sha(content), "model_id":"test-double", "revision":"test",
            "reads":[{"text":text, "truncated":False} for text in texts], "needs_review":True}

    def run_ocr(self, *texts):
        with patch("document_identity.alias_ocr.recognize_aliases", side_effect=lambda content:self.result(content, *texts)) as model:
            self.assertTrue(self.ocr.process_one())
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(model.call_args.args), 1)  # Pixels only, no expected alias.

    def test_exact_match_binds_page_without_confirmation_and_retains_evidence(self):
        job = self.intake(); before = self.page(job)
        alias = self.entries[0]["recovery_alias"]
        self.run_ocr(alias, alias)
        page = self.page(job); reading = page["alias_ocr"]
        self.assertEqual(reading["status"], "complete")
        self.assertEqual(reading["result"]["outcome"], "resolved")
        self.assertEqual(reading["result"]["alias"], alias)
        self.assertFalse(reading["result"]["needs_review"])
        for key in ("candidates_json", "decoder_json", "content_sha256", "page_artifact_id", "raster_artifact_id"):
            self.assertEqual(page[key], before[key])
        self.assertEqual(page["page_stamp_id"], self.entries[0]["stamp_id"])
        self.assertEqual(page["status"], "resolved")
        self.assertEqual(page["review_state"], "candidate")
        assembly = self.scans.assembly(self.entries[0]["copy_id"])
        self.assertEqual(assembly["missing"], [2, 3])
        self.assertEqual(assembly["revisions"], [])
        report_bytes = self.inbox.read_artifact(reading["artifact_id"])[1]
        report = json.loads(report_bytes)
        self.assertEqual(report["source_artifact_id"], page["page_artifact_id"])
        self.assertEqual(report["ocr"]["source_sha256"], page["content_sha256"])
        self.assertEqual(report["review"]["outcome"], "resolved")
        self.assertTrue(report["review"]["instructor_review_required"])
        self.assertEqual(self.storage.backup_all()["errors"], [])
        self.assertEqual(self.inbox.read_artifact(reading["artifact_id"])[1], report_bytes)
        route = f"/api/documents/scans/{page['id']}/recover"
        self.assertEqual(self.app.test_client().post(route,json={"alias":alias,"reason":"check"}).status_code,403)
        self.assertEqual(self.client.post(route,json={"alias":alias,"reason":""},headers=HEADERS).status_code,400)
        # Explicit corrections are still available after automatic recovery.
        response = self.app.test_client().post(route,json={"alias":self.entries[1]["recovery_alias"],"reason":"Corrected the page after inspection"},headers=HEADERS)
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.page(job)["page_stamp_id"],self.entries[1]["stamp_id"])
        self.assertTrue(response.json["instructor_review_required"])
        self.assertEqual(self.inbox.get(job["id"])["status"],"needs_review")
        with connect(self.docs.path) as db:
            for sql in ("UPDATE scan_alias_ocr SET result_json='{}'", "DELETE FROM scan_alias_ocr"):
                with self.assertRaises(sqlite3.IntegrityError): db.execute(sql)

    def test_barcode_or_blank_or_untrusted_source_never_queues_ocr(self):
        for candidates, blank in (([],True), ([{"payload":self.entries[0]["token"]}],False),
                ([{"payload":self.entries[0]["token"]},{"payload":self.entries[1]["token"]}],False),
                ([{"payload":tokens.generate_token()}],False), ([{"payload":"T1-invalid"}],False),
                ([{"payload":"unrelated barcode"}],False)):
            with self.subTest(candidates=candidates,blank=blank):
                from document_identity import process
                extracted = process.run("extract",self.pdf)
                extracted[0].update(candidates=candidates,blank=blank)
                # Inspection asks the sandbox for the page count, then for each bounded piece.
                with patch("document_identity.scans.process.run",side_effect=lambda operation,*a,**k:{"total":1} if operation=="pages" else extracted): job=self.intake()
                self.assertIsNone(self.page(job)["alias_ocr"])
        job = self.intake(authenticated=False)
        self.assertEqual(self.scans.review_item(job["message_id"])["pages"],[])
        with patch("document_identity.alias_ocr.recognize_aliases",side_effect=AssertionError("must not run")):
            self.assertFalse(self.ocr.process_one())

    def test_no_fuzzy_match_truncated_output_or_conflicting_alias_is_accepted(self):
        alias = self.entries[0]["recovery_alias"]
        for text in (alias.lower(),alias.replace("A1-","A1-O"),"Read this: "+alias, "<script>"+alias+"</script>",tokens.generate_alias()):
            self.assertEqual(self.ocr.summarize(self.result(b"x",text))["outcome"],"no_match")
        result=self.result(b"x",alias); result["reads"][0]["truncated"]=True
        self.assertEqual(self.ocr.summarize(result)["outcome"],"no_match")
        result=self.result(b"x",alias,self.entries[1]["recovery_alias"])
        self.assertEqual(self.ocr.summarize(result)["outcome"],"conflict")
        result=self.result(b"x",alias,tokens.generate_alias())
        self.assertEqual(self.ocr.summarize(result)["outcome"],"conflict")
        with patch.object(self.docs,"resolve_token",return_value={"batch_status":"cancelled","state":"cancelled"}):
            self.assertEqual(self.ocr.summarize(self.result(b"x",alias))["outcome"],"no_match")

    def test_ambiguous_or_unmatched_ocr_keeps_page_unidentified(self):
        for readings, outcome in (((self.entries[0]["recovery_alias"],self.entries[1]["recovery_alias"]),"conflict"),
                                  ((tokens.generate_alias(),),"no_match")):
            job=self.intake()
            self.run_ocr(*readings)
            page=self.page(job)
            self.assertEqual(page["alias_ocr"]["result"]["outcome"],outcome)
            self.assertTrue(page["alias_ocr"]["result"]["needs_review"])
            self.assertIsNone(page["page_stamp_id"])
            self.assertEqual(page["status"],"unreadable")

    def test_duplicate_and_changed_content_use_barcode_assembly_rules(self):
        alias=self.entries[0]["recovery_alias"]
        first=self.intake(); self.run_ocr(alias)
        second=self.intake(); self.run_ocr(alias)
        page=self.page(second)
        self.assertEqual(page["review_state"],"duplicate")
        self.assertEqual(page["duplicate_of_scan_page_id"],self.page(first)["id"])
        from reportlab.pdfgen.canvas import Canvas
        overlay=io.BytesIO(); canvas=Canvas(overlay,pagesize=(612,792))
        canvas.drawString(100,300,"Different student work"); canvas.save()
        writer=PdfWriter(); changed=writer.add_page(PdfReader(io.BytesIO(self.pdf)).pages[0])
        changed.merge_page(PdfReader(overlay).pages[0])
        output=io.BytesIO(); writer.write(output); self.pdf=output.getvalue()
        third=self.intake(); self.run_ocr(alias)
        self.assertEqual(self.page(first)["review_state"],"conflict")
        self.assertEqual(self.page(second)["review_state"],"duplicate")
        self.assertEqual(self.page(third)["review_state"],"conflict")
        self.assertIsNone(self.page(third)["duplicate_of_scan_page_id"])
        assembly=self.scans.assembly(self.entries[0]["copy_id"])
        self.assertEqual(assembly["conflicts"],[1])
        self.assertEqual(assembly["revisions"],[])

    def test_failed_publication_does_not_assign_identity(self):
        job=self.intake()
        with patch("document_identity.alias_ocr.publish",side_effect=OSError("disk full")):
            self.run_ocr(self.entries[0]["recovery_alias"])
        page=self.page(job)
        self.assertIsNone(page["page_stamp_id"])
        self.assertEqual(page["alias_ocr"]["status"],"failed")
        self.assertIsNone(page["alias_ocr"]["artifact_id"])

    def test_identity_and_report_roll_back_together_if_completion_fails(self):
        job=self.intake()
        event=self.inbox.event
        def fail_completion(db, job_id, kind, detail):
            if kind=="alias_ocr_complete":
                raise RuntimeError("interrupted before catalog commit")
            return event(db,job_id,kind,detail)
        with patch.object(self.inbox,"event",side_effect=fail_completion):
            self.run_ocr(self.entries[0]["recovery_alias"])
        page=self.page(job)
        self.assertIsNone(page["page_stamp_id"])
        self.assertIsNone(page["alias_ocr"]["artifact_id"])
        self.assertEqual(page["alias_ocr"]["status"],"failed")
        self.assertFalse(any(a['filename'].startswith('scan-alias-ocr-') for a in self.inbox.get(job['id'])['artifacts']))
        self.ocr.retry(page["id"])
        self.run_ocr(self.entries[0]["recovery_alias"])
        self.assertEqual(self.page(job)["status"],"resolved")

    def test_failed_attempt_requires_explicit_retry_without_session(self):
        job=self.intake(); scan_id=self.page(job)["id"]
        with patch("document_identity.alias_ocr.recognize_aliases",side_effect=RuntimeError("failure")):
            self.assertTrue(self.ocr.process_one())
        self.assertEqual(self.page(job)["alias_ocr"]["status"],"failed")
        self.assertFalse(self.ocr.process_one())
        route=f"/api/documents/scans/{scan_id}/alias-ocr/retry"
        self.assertEqual(self.app.test_client().post(route,json={}).status_code,403)
        self.assertEqual(self.app.test_client().post(route,json={},headers=HEADERS).status_code,200)
        self.run_ocr(self.entries[0]["recovery_alias"])
        self.assertEqual(self.page(job)["alias_ocr"]["attempts"],2)
        self.assertEqual(self.client.post(route,json={},headers=HEADERS).status_code,409)

    def test_interruption_and_manual_correction_during_ocr_do_not_overwrite_identity(self):
        job=self.intake(); page=self.page(job)
        with self.docs.rosters.lock(),self.docs.files.owner_lock(job["message_id"]),connect(self.docs.path) as db:
            db.execute("UPDATE scan_alias_ocr SET status='running',attempts=1 WHERE scan_page_id=?",(page["id"],))
        self.assertFalse(self.ocr.process_one())
        self.assertEqual(self.page(job)["alias_ocr"]["status"],"failed")
        self.ocr.retry(page["id"])
        def recognize(content):
            self.scans.recover(page["id"],self.entries[1]["recovery_alias"],"Instructor corrected during OCR")
            return self.result(content,self.entries[0]["recovery_alias"])
        with patch("document_identity.alias_ocr.recognize_aliases",side_effect=recognize):
            self.assertTrue(self.ocr.process_one())
        result=self.page(job)
        self.assertEqual(result["alias_ocr"]["status"],"skipped")
        self.assertIsNone(result["alias_ocr"]["artifact_id"])
        self.assertEqual(result["page_stamp_id"],self.entries[1]["stamp_id"])

    def test_disabled_feature_and_source_checksum_failure(self):
        self.ocr.enabled=False
        job=self.intake(); self.assertIsNone(self.page(job)["alias_ocr"])
        self.ocr.enabled=True
        job=self.intake()
        with patch.object(self.inbox,"read_artifact",return_value=("page.pdf",b"tampered")), \
             patch("document_identity.alias_ocr.recognize_aliases",side_effect=AssertionError("must not run")):
            self.assertTrue(self.ocr.process_one())
        self.assertEqual(self.page(job)["alias_ocr"]["status"],"failed")
        self.assertIsNone(self.page(job)["page_stamp_id"])


if __name__ == "__main__":
    unittest.main()
