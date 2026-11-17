"""Explicit instructor-approved exports of existing section print artifacts.

Only this private workflow queues sends. The instructor's connected Gmail account (the Response Cards connection, wired in
app.py) sends the mail (requirement, 2026-11-01). Network
uncertainty never triggers an automatic resend.
"""
from contextlib import ExitStack
from dataclasses import dataclass
import html
import os
import re
import uuid

from werkzeug.exceptions import Conflict, ServiceUnavailable

from course_storage import timestamp
from rosters import connect
from .service import EMAILED_COPY
from teach_inbox.service import encoded, sha

DEFAULT_RECIPIENT = "instructor@tamu.edu"
# Includes headroom for base64/MIME line folding; seating chart PDFs share the limit.
# Never replace a too-large attachment with a public link.
MAX_PDF_BYTES = 3_500_000
ADDRESS = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}\Z")


def address(value):
    if not isinstance(value, str) or value != value.strip() or len(value) > 254 or not ADDRESS.fullmatch(value):
        raise ValueError("Enter one email address, without a display name or additional recipients.")
    local, domain = value.rsplit("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in value or any(
            label.startswith("-") or label.endswith("-") or len(label) > 63 for label in domain.split(".")):
        raise ValueError("Enter a valid email address.")
    return local + "@" + domain.lower()


@dataclass
class EmailExportConfig:
    approved: tuple = (DEFAULT_RECIPIENT,)

    @classmethod
    def environment(cls):
        approved = tuple(dict.fromkeys([DEFAULT_RECIPIENT, *[
            address(v.strip()) for v in os.environ.get("FACULTY_PRINT_EMAIL_APPROVED", "").split(",") if v.strip()]]))
        return cls(approved)


class DeliveryError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


UNCONFIRMED = "Delivery could not be confirmed. Check your mailbox before sending again."


class PrintEmailExports:
    def __init__(self, documents, config=None, gmail=None):
        self.documents = documents
        self.rosters, self.path = documents.rosters, documents.path
        self.config = config or EmailExportConfig.environment()
        self.gmail = gmail  # response_cards.gmail.GmailConnection, set in app.py once Response Cards exist

    @property
    def configured(self):
        return bool(self.gmail is not None and self.gmail.connected)

    @staticmethod
    def command_hash(batch_id, recipient, digest):
        return sha(encoded({"batch_id": batch_id, "recipient": recipient, "output_sha256": digest}).encode())

    def options(self, batch_id):
        batch = self.documents.get_batch(batch_id)
        size = len(self.documents.download(batch_id))
        with connect(self.path) as db:
            history = [dict(r) for r in db.execute("""SELECT id,recipient,status,error,created_at,updated_at
                FROM print_email_exports WHERE batch_id=? ORDER BY created_at DESC,id DESC LIMIT 30""", (batch_id,))]
        return {"configured": self.configured, "sender": self.gmail.email if self.configured else "", "default_recipient": DEFAULT_RECIPIENT,
                "approved_recipients": list(self.config.approved), "custom_recipient_allowed": True,
                "attachment_name": f"print-{batch_id}.pdf", "attachment_bytes": size,
                "max_attachment_bytes": MAX_PDF_BYTES, "output_sha256": batch["output_sha256"], "exports": history}

    def submit(self, batch_id, recipient, expected_sha256, request_id, actor="workspace"):
        recipient = address(recipient)
        try:
            if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError("An email export requires a unique request ID.") from None
        digest = self.command_hash(batch_id, recipient, expected_sha256)
        with connect(self.path) as db:
            existing = db.execute("SELECT * FROM print_email_exports WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["command_sha256"] != digest:
                    raise Conflict("This request ID already approves a different email export.")
                return self.public(existing)
        if not self.configured:
            raise ServiceUnavailable("Connect Gmail on the Response Cards page to email print PDFs. You can still download the print PDF.")
        content = self.documents.download(batch_id)
        if sha(content) != expected_sha256:
            raise Conflict("The print PDF changed. Reopen Export by email before sending.")
        if len(content) > MAX_PDF_BYTES:
            raise ValueError("This PDF exceeds the email attachment limit (3.3 MiB). Use Download print PDF.")
        with self.documents.batch_lock(batch_id), connect(self.path) as db:
            batch = db.execute("SELECT * FROM print_batches WHERE id=?", (batch_id,)).fetchone()
            if batch["status"] != "ready" or batch["superseded_at"] or batch["output_sha256"] != expected_sha256:
                raise Conflict("Only a validated, non-superseded print job can be emailed.")
            if self.documents.emailed_copy(db, batch_id):
                raise Conflict(EMAILED_COPY)
            existing = db.execute("SELECT * FROM print_email_exports WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["command_sha256"] != digest:
                    raise Conflict("This request ID already approves a different email export.")
                return self.public(existing)
            now, export_id = timestamp(), str(uuid.uuid4())
            db.execute("""INSERT INTO print_email_exports
                (id,batch_id,recipient,output_sha256,command_sha256,request_id,approved_by,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'pending',?,?)""", (export_id,batch_id,recipient,expected_sha256,digest,request_id,actor,now,now))
            self.documents.event(db, "email_export_approved", actor, {"export_id": export_id, "recipient": recipient,
                                  "command_sha256": digest}, batch_id=batch_id)
            return self.public(db.execute("SELECT * FROM print_email_exports WHERE id=?", (export_id,)).fetchone())

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("id", "batch_id", "recipient", "status", "error", "created_at", "updated_at")}

    def process_one(self):
        with self.documents.worker_lock():
            with self.rosters.lock(), ExitStack() as locks:
                with connect(self.path) as db:
                    sections = [r[0] for r in db.execute("""SELECT DISTINCT COALESCE(b.section_id,b.term_id) owner FROM print_email_exports e
                        JOIN print_batches b ON b.id=e.batch_id WHERE e.status IN ('pending','sending') ORDER BY owner""")]
                for section in sections:
                    locks.enter_context(self.documents.files.owner_lock(section))
                with connect(self.path) as db:
                    interrupted = db.execute("SELECT * FROM print_email_exports WHERE status='sending'").fetchall()
                    for row in interrupted:
                        status = "accepted" if row["provider_receipt_json"] else "uncertain"
                        error = "" if status == "accepted" else "Sending was interrupted. Check your mailbox before creating another email export."
                        db.execute("UPDATE print_email_exports SET status=?,error=?,updated_at=? WHERE id=?", (status,error,timestamp(),row["id"]))
                        self.documents.event(db, "email_export_"+status, "worker", {"export_id":row["id"]}, batch_id=row["batch_id"])
                    row = db.execute("SELECT * FROM print_email_exports WHERE status='pending' ORDER BY created_at,id LIMIT 1").fetchone()
                    if row is None:
                        return bool(interrupted)
                    job = dict(row)
                    db.execute("UPDATE print_email_exports SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?", (timestamp(),job["id"]))
            sending = False
            try:
                content = self.documents.download(job["batch_id"])
                if sha(content) != job["output_sha256"] or len(content) > MAX_PDF_BYTES:
                    raise ValueError("The retained PDF failed email export verification.")
                if self.command_hash(job["batch_id"],job["recipient"],job["output_sha256"]) != job["command_sha256"]:
                    raise ValueError("The approved email command failed verification.")
                if not self.configured:
                    raise ValueError("Gmail is not connected.")
                sending = True
                receipt = self.send(job, content)
            except DeliveryError as error:
                self.finish(job, "uncertain" if error.uncertain else "failed", str(error))
            except Exception:
                self.finish(job, "uncertain" if sending else "failed",
                            UNCONFIRMED if sending else
                            "The print PDF is unavailable, superseded, or failed verification. No email was sent.")
            else:
                # Commit the provider acknowledgement separately before marking
                # completion. Recovery uses this receipt without sending again.
                with self.documents.batch_lock(job["batch_id"]), connect(self.path) as db:
                    db.execute("UPDATE print_email_exports SET provider_receipt_json=?,updated_at=? WHERE id=?",
                               (encoded(receipt),timestamp(),job["id"]))
                self.finish(job, "accepted", "")
            return True

    def finish(self, job, status, error):
        with self.documents.batch_lock(job["batch_id"]), connect(self.path) as db:
            db.execute("UPDATE print_email_exports SET status=?,error=?,updated_at=? WHERE id=?", (status,error,timestamp(),job["id"]))
            self.documents.event(db, "email_export_"+status, "worker", {"export_id":job["id"],"recipient":job["recipient"]}, batch_id=job["batch_id"])

    def send(self, job, content):
        batch = self.documents.get_batch(job["batch_id"])
        where = f"Section {batch['section_number']}" if batch["section_id"] else "All sections"  # a job printed for the whole term
        subject = " ".join(f"{batch['code']} · {batch['title']} · {batch['version_label']} · {where}".split())[:300]
        text = (f"Your print job is attached: {batch['quantity']} copies, {batch['page_count']} pages per copy.\n\n"
                "Print all pages at Actual size / 100%, with the printer copy count set to 1. "
                "Create a new print batch for additional physical copies.")
        from response_cards.gmail import DeliveryError as GmailError
        try:
            receipt = self.gmail.send({"mail_id": job["id"], "recipient": job["recipient"], "subject": subject, "text": text,
                                       "html": "<p>"+html.escape(text).replace("\n\n", "</p><p>")+"</p>",
                                       "pdf_attachment": content, "pdf_filename": f"print-{job['batch_id']}.pdf"})
        except GmailError as error:
            # Only a send Google never took is "Not sent"; anything else waits for the instructor to check (no automatic resend).
            # Gmail's own wording promises a retry that print exports never make; keep only its reason.
            reason = str(error).split(";")[0].strip().rstrip(".")
            raise DeliveryError(UNCONFIRMED if error.uncertain else f"{reason}. No email was sent.", error.uncertain) from None
        except Exception:
            raise DeliveryError(UNCONFIRMED, True) from None
        if (not isinstance(receipt, dict) or receipt.get("mail_id") != job["id"] or receipt.get("status") != "accepted"
                or not isinstance(receipt.get("message_id"), str) or not 1 <= len(receipt["message_id"]) <= 1000):
            raise DeliveryError(UNCONFIRMED, True)
        return {"export_id": job["id"], "attachment_sha256": job["output_sha256"], "status": "accepted",
                "message_id": receipt["message_id"], "provider": "gmail"}
