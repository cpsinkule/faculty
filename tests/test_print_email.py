"""Email export invariants. Provider calls are replaced; no mail leaves tests."""
from concurrent.futures import ThreadPoolExecutor
from email import message_from_bytes
import base64
import hashlib
import tempfile
import unittest
import uuid
from unittest.mock import patch

from app import create_app
from document_identity.email_exports import EmailExportConfig, DeliveryError, MAX_PDF_BYTES
from response_cards.gmail import GmailConnection
from rosters import connect
from test_storage import HEADERS, temporary_storage


class PrintEmailTests(unittest.TestCase):
    managed = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = temporary_storage(self.temp.name) if self.managed else None
        self.app = create_app(storage=self.storage) if self.storage else create_app(self.temp.name)
        self.client = self.app.test_client()
        self.client.get("/").close()
        response = self.client.post("/api/courses",json={"name":"Calculus","code":"MATH 140","term":"Fall","year":2026,"sections":[507],"is_current":True},headers=HEADERS)
        self.assertEqual(response.status_code,201)
        if self.storage: self.storage.reconcile_all()
        self.documents = self.app.extensions["documents"]
        self.exports = self.app.extensions["print_email_exports"]
        self.exports.config = EmailExportConfig(("instructor@tamu.edu","owner@example.test"))
        # Export by email sends from the Response Cards' Gmail connection (requirement, 2026-11-01); the app wires the same one.
        self.assertIs(self.exports.gmail, self.app.extensions["response_cards"].mail.gmail)
        self.gmail = FakeGmail()
        self.exports.gmail = self.gmail
        self.path = self.app.config["DATABASE"]
        catalog = self.documents.catalog()
        assignment = self.documents.create_assignment(catalog["definitions"][0]["id"],"email-test","Print email test")
        version = self.client.post("/api/documents/versions/fixture",json={"assignment_id":assignment["id"],"label":"A","paper":"letter"},headers=HEADERS).json
        self.batch = self.documents.create_batch(version["id"],catalog["sections"][0]["id"],1,str(uuid.uuid4()))
        self.documents.build_one()
        self.batch = self.documents.get_batch(self.batch["id"])
        self.assertEqual(self.batch["status"],"ready")
        self.route = f"/api/documents/batches/{self.batch['id']}/email"

    def submit(self, recipient="instructor@tamu.edu", request_id=None):
        return self.exports.submit(self.batch["id"],recipient,self.batch["output_sha256"],request_id or str(uuid.uuid4()))

    def receipt(self, job, content):
        self.assertEqual(content,self.documents.download(job["batch_id"]))
        return {"export_id":job["id"],"attachment_sha256":job["output_sha256"],"status":"accepted","message_id":"test-message-1"}

    def job(self, export_id):
        with connect(self.path) as db:
            return dict(db.execute("SELECT * FROM print_email_exports WHERE id=?",(export_id,)).fetchone())

    def test_session_free_api_defaults_approved_and_custom_recipient(self):
        options = self.client.get(self.route).json
        self.assertEqual(options["default_recipient"],"instructor@tamu.edu")
        self.assertIn("owner@example.test",options["approved_recipients"])
        self.assertTrue(options["custom_recipient_allowed"])
        fresh = self.app.test_client()
        self.assertEqual(fresh.get(self.route).status_code,200)
        body = {"recipient":"custom@example.test","output_sha256":self.batch["output_sha256"],"request_id":str(uuid.uuid4())}
        self.assertEqual(fresh.post(self.route,json=body).status_code,403)
        self.assertEqual(fresh.post(self.route,json=body,headers={**HEADERS,'Origin':'https://elsewhere.test'}).status_code,403)
        response = fresh.post(self.route,json=body,headers=HEADERS)
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json["recipient"],"custom@example.test")
        self.assertEqual(options["sender"],"instructor@example.test")

    def test_concurrent_duplicate_requests_and_exact_confirmation(self):
        request_id = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(lambda _: self.submit(request_id=request_id),range(4)))
        self.assertEqual(len({j["id"] for j in jobs}),1)
        with self.assertRaises(Exception): self.submit("different@example.test",request_id)
        with patch.object(self.exports,"send",side_effect=self.receipt) as send:
            self.assertTrue(self.exports.process_one())
            self.assertFalse(self.exports.process_one())
            self.assertEqual(self.submit(request_id=request_id)["id"],jobs[0]["id"])
            send.assert_called_once()
        job = self.job(jobs[0]["id"])
        self.assertEqual(job["status"],"accepted")
        self.assertEqual(job["approved_by"],"workspace")
        self.assertTrue(job["provider_receipt_json"])

    def test_invalid_recipient_stale_pdf_and_size_limit_do_not_queue(self):
        for invalid in ("name <one@example.test>","one@example.test,two@example.test","a\r\nBcc:b@example.test","a..b@example.test","bad@-example.test"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): self.submit(invalid)
        with self.assertRaises(Exception):
            self.exports.submit(self.batch["id"],"owner@example.test","0"*64,str(uuid.uuid4()))
        content=b"%PDF-"+b"x"*MAX_PDF_BYTES
        with patch.object(self.documents,"download",return_value=content), self.assertRaisesRegex(ValueError,"attachment limit"):
            self.exports.submit(self.batch["id"],"owner@example.test",hashlib.sha256(content).hexdigest(),str(uuid.uuid4()))
        with connect(self.path) as db: self.assertEqual(db.execute("SELECT COUNT(*) FROM print_email_exports").fetchone()[0],0)

    def test_superseded_pending_export_does_not_send(self):
        job=self.submit()
        self.documents.transition(self.batch["id"],"supersede")
        with patch.object(self.exports,"send") as send:
            self.exports.process_one()
            send.assert_not_called()
        self.assertEqual(self.job(job["id"])["status"],"failed")

    def test_uncertain_delivery_and_crash_never_resend_automatically(self):
        job=self.submit()
        with patch.object(self.exports,"send",side_effect=DeliveryError("Delivery unconfirmed",True)) as send:
            self.exports.process_one()
            self.assertFalse(self.exports.process_one())
            send.assert_called_once()
        self.assertEqual(self.job(job["id"])["status"],"uncertain")
        second=self.submit()
        with self.documents.batch_lock(self.batch["id"]),connect(self.path) as db:
            db.execute("UPDATE print_email_exports SET status='sending' WHERE id=?",(second["id"],))
        with patch.object(self.exports,"send") as send:
            self.exports.process_one()
            send.assert_not_called()
        self.assertEqual(self.job(second["id"])["status"],"uncertain")

    def test_provider_receipt_survives_completion_crash(self):
        job=self.submit()
        with patch.object(self.exports,"send",side_effect=self.receipt), patch.object(self.exports,"finish",side_effect=RuntimeError("Injected completion interruption")):
            with self.assertRaises(RuntimeError): self.exports.process_one()
        self.assertTrue(self.job(job["id"])["provider_receipt_json"])
        with patch.object(self.exports,"send") as send:
            self.exports.process_one()
            send.assert_not_called()
        self.assertEqual(self.job(job["id"])["status"],"accepted")

    def test_gmail_attaches_exact_bytes_and_preserves_recipient(self):
        job=self.job(self.submit("custom@example.test")["id"])
        pdf=self.documents.download(self.batch["id"])
        sent=[]
        connection=GmailConnection()
        connection.access_token=lambda force=False, expected_email=None: "token"
        connection.http=lambda url, body, headers, timeout: (sent.append(body) or (200, {"id": "gmail-message-1"}))
        with patch.object(GmailConnection,"email",new="instructor@example.test"):
            self.exports.gmail=connection
            receipt=self.exports.send(job,pdf)
        message=message_from_bytes(base64.urlsafe_b64decode(sent[0]["raw"]))
        self.assertEqual(message["To"],"custom@example.test")
        self.assertIn("instructor@example.test",message["From"])
        attachment=[part for part in message.walk() if part.get_content_type()=="application/pdf"]
        self.assertEqual(attachment[0].get_payload(decode=True),pdf)
        self.assertEqual(attachment[0].get_filename(),f"print-{self.batch['id']}.pdf")
        self.assertEqual(receipt,{"export_id":job["id"],"attachment_sha256":job["output_sha256"],"status":"accepted",
                                  "message_id":"gmail-message-1","provider":"gmail"})

    def test_disconnected_gmail_and_failures_are_honest(self):
        self.gmail.connected=False
        options=self.exports.options(self.batch["id"])
        self.assertFalse(options["configured"])
        self.assertEqual(options["sender"],"")
        with self.assertRaisesRegex(Exception,"Connect Gmail"): self.submit()
        self.gmail.connected=True
        job=self.job(self.submit()["id"])
        from response_cards.gmail import DeliveryError as GmailError
        for failure,uncertain,words in ((GmailError("Delivery could not be confirmed.",uncertain=True),True,"Check your mailbox"),
                                        (GmailError("Google could not be reached; sending will retry.",unavailable=True),False,
                                         "Google could not be reached. No email was sent."),
                                        (TimeoutError(),True,"Check your mailbox")):
            self.gmail.failure=failure
            with self.assertRaises(DeliveryError) as caught: self.exports.send(job,b"%PDF-1.7")
            self.assertEqual(caught.exception.uncertain,uncertain)
            self.assertEqual(str(caught.exception).count(words),1)
            self.assertNotIn("will retry",str(caught.exception))
        self.gmail.failure=None
        self.gmail.answer={"mail_id":"another","status":"accepted","message_id":"x"}
        with self.assertRaises(DeliveryError) as caught: self.exports.send(job,b"%PDF-1.7")
        self.assertTrue(caught.exception.uncertain)


class FakeGmail:
    """Stands in for the Response Cards' Gmail connection; no mail leaves tests."""
    def __init__(self):
        self.connected, self.email, self.failure, self.answer, self.sent = True, "instructor@example.test", None, None, []

    def send(self, payload, expected_email=None):
        self.sent.append(payload)
        if self.failure: raise self.failure
        return self.answer or {"mail_id":payload["mail_id"],"status":"accepted","message_id":"fake-gmail-1"}


class ManagedPrintEmailTests(PrintEmailTests):
    managed = True
