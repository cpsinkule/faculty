"""Exact recovery-label identification retained by the source Inbox owner.

An unambiguous alias may identify a page automatically under the instructor's
policy. OCR never clears the Inbox review gate or authorizes an action.
"""
import json
import os
import re
import uuid
from contextlib import ExitStack

from werkzeug.exceptions import Conflict, Forbidden, NotFound

from course_storage import timestamp
from faculty_ocr import recognize_aliases, recognize_bands
from rosters import connect
from teach_inbox.service import encoded, sha
from . import tokens
from .layouts import CORNERS_PROFILE, SMALL_CORNERS_PROFILE
from .storage import publish

MAX_PER_MESSAGE = 60
STICKER_ALIAS = re.compile(r"A1-[0-9A-Z]{10}")  # candidates only: `tokens.valid_alias` and an exact resolution decide


class AliasOcrService:
    def __init__(self, scans):
        self.scans = scans
        self.inbox, self.rosters, self.path = scans.inbox, scans.rosters, scans.path
        self.enabled = os.environ.get("FACULTY_ALIAS_OCR_ENABLED") == "1"

    @staticmethod
    def eligible(page):
        # Blank pages, unknown/conflicting/malformed teaching references, and
        # observations with even one decoded barcode never enter this fallback.
        return (page["status"] == "unreadable" and page["page_stamp_id"] is None
                and not json.loads(page["candidates_json"]))

    def enqueue_message(self, db, message_id):
        """Queue the fallback once a scan is inspected, only where a printed alias can be expected.

        The reader looks at the compact corner layout's two alias positions. A
        scan whose identified pages come from peel-off labels (a stamps-only
        export) and none from corner-layout paper is an exam labeled by hand:
        its other pages carry no barcode by design, and a sticker is not at
        those positions, so hundreds of reads would find nothing and hold the
        GPU for an hour. Paper stamped with the 7 mm corner codes (2026-10-18) prints no alias either: a scan of such paper
        and labels only is skipped the same way. Every other scan keeps the fallback, bounded per scan.
        """
        if not self.enabled:
            return 0
        if self.sticker_scan(db, message_id):
            return 0
        # A page recognized as a bubble sheet by its positioning marks was never issued a label or alias to find.
        sheets = self.scans.bubble_detector.sheet_pages(db, message_id) if self.scans.bubble_detector else set()
        queued = 0
        for page in db.execute("SELECT * FROM scan_pages WHERE message_id=? ORDER BY source_artifact_id,source_page_index", (message_id,)).fetchall():
            if queued >= MAX_PER_MESSAGE:
                break
            if self.eligible(page) and page["id"] not in sheets:
                queued += db.execute("INSERT OR IGNORE INTO scan_alias_ocr(scan_page_id,message_id,status,created_at,updated_at) VALUES(?,?,'pending',?,?)",
                                     (page["id"], message_id, timestamp(), timestamp())).rowcount
        return queued

    @staticmethod
    def sticker_scan(db, message_id):
        """A scan whose identified pages come from peel-off labels only (`enqueue_message`): its alias is printed on the sticker, under
        the code, wherever the sticker went, not at the corner layout's alias positions."""
        kinds = {(row["corners"], row["labels"] or row["small"]) for row in db.execute(
            """SELECT json_extract(b.configuration_json,'$.id')=? corners, json_extract(v.compiler_json,'$.generator')='faculty-stamps-only' labels,
                      json_extract(b.configuration_json,'$.id')=? small
               FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id JOIN printed_copies c ON c.id=p.printed_copy_id
               JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
               WHERE sp.message_id=?""", (CORNERS_PROFILE, SMALL_CORNERS_PROFILE, message_id))}
        return any(labels for _, labels in kinds) and not any(corners and not labels for corners, labels in kinds)

    def enqueue_first_pages(self, message_id, scan_page_ids):
        """Read the sticker's printed alias on the first page of a paper whose label was not read (2026-10-24: home-scanned Exam 2
        stickers whose code ran off the sticker's top edge held whole stacks for the instructor's alias). Only on a sticker scan, only
        for pages the grouping found where a paper should start, once each; the alias must still resolve exactly."""
        if not self.enabled or not scan_page_ids:
            return 0
        with self.rosters.lock(), connect(self.path) as db:
            if not self.sticker_scan(db, message_id):
                return 0
            queued = 0
            for page in db.execute(f"SELECT * FROM scan_pages WHERE message_id=? AND id IN ({','.join('?' * len(scan_page_ids))})",
                                   (message_id, *scan_page_ids)).fetchall():
                if self.eligible(page):
                    queued += db.execute("INSERT OR IGNORE INTO scan_alias_ocr(scan_page_id,message_id,status,created_at,updated_at) VALUES(?,?,'pending',?,?)",
                                         (page["id"], message_id, timestamp(), timestamp())).rowcount
            return queued

    def retry(self, scan_id, actor="workspace"):
        if not self.enabled:
            raise Conflict("Printed-label OCR is not enabled on this server.")
        with self.rosters.lock(), ExitStack() as locks, connect(self.path) as db:
            row = db.execute("SELECT * FROM scan_alias_ocr WHERE scan_page_id=?", (scan_id,)).fetchone()
            if not row:
                raise NotFound("No OCR attempt exists for this page.")
            locks.enter_context(self.scans.documents.files.owner_lock(row["message_id"]))
            page, job = self._source(db, scan_id)
            if not self.eligible(page):
                raise Conflict("This page no longer needs recovery-label OCR.")
            if not db.execute("UPDATE scan_alias_ocr SET status='pending',error='',updated_at=? WHERE scan_page_id=? AND status='failed'",
                              (timestamp(), scan_id)).rowcount:
                raise Conflict("Only failed OCR attempts can be retried.")
            self.inbox.event(db, job["id"], "alias_ocr_retry", f"{actor} requested another recovery-label reading; instructor review remains required.")

    def _source(self, db, scan_id):
        page = db.execute("SELECT * FROM scan_pages WHERE id=?", (scan_id,)).fetchone()
        message = self.inbox.message(db, page["message_id"])
        job = db.execute("SELECT id,action FROM inbox_jobs WHERE message_id=?", (page["message_id"],)).fetchone()
        if not message["authenticated"] or not job or job["action"] != "scan.receive":
            raise Forbidden("Recovery-label OCR requires authenticated scanner intake.")
        return page, job

    def summarize(self, result):
        # The model gets pixels and a fixed text-recognition prompt only. No
        # expected aliases, catalog data, fuzzy matching or character repair.
        aliases = sorted({read["text"].strip() for read in result["reads"]
                          if not read["truncated"] and tokens.valid_alias(read["text"].strip())})
        summary = {"outcome": "no_match", "aliases": aliases, "needs_review": True}
        if len(aliases) > 1:
            summary["outcome"] = "conflict"
        elif aliases:
            match = self.scans.documents.resolve_token(aliases[0], authenticated=True, alias=True)
            if self.scans.resolution(match) == "resolved":
                summary.update(outcome="matched", alias=aliases[0], match=match, needs_review=False)
        return summary

    def process_one(self):
        if not self.enabled:
            return False
        with self.inbox.worker_lock():
            with self.rosters.lock(), ExitStack() as locks:
                with connect(self.path) as db:
                    owners = [r[0] for r in db.execute("SELECT DISTINCT message_id FROM scan_alias_ocr WHERE status IN ('pending','running') ORDER BY message_id")]
                for owner in owners:
                    locks.enter_context(self.scans.documents.files.owner_lock(owner))
                with connect(self.path) as db:
                    db.execute("UPDATE scan_alias_ocr SET status='failed',error='OCR worker interrupted; explicit retry required.',updated_at=? WHERE status='running'", (timestamp(),))
                    row = db.execute("SELECT * FROM scan_alias_ocr WHERE status='pending' ORDER BY created_at,scan_page_id LIMIT 1").fetchone()
                    if not row:
                        return False
                    row = dict(row)
                    db.execute("UPDATE scan_alias_ocr SET status='running',attempts=attempts+1,updated_at=? WHERE scan_page_id=?", (timestamp(), row["scan_page_id"]))
            try:
                self._read(row)
            except Exception:
                with self.rosters.lock(), self.scans.documents.files.owner_lock(row["message_id"]), connect(self.path) as db:
                    error = "Recovery-label OCR failed or exceeded its limits. Review the retained page or explicitly retry."
                    db.execute("UPDATE scan_alias_ocr SET status='failed',error=?,updated_at=? WHERE scan_page_id=? AND status='running'", (error, timestamp(), row["scan_page_id"]))
                    job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (row["message_id"],)).fetchone()
                    self.inbox.event(db, job["id"], "alias_ocr_failed", error)
            return True

    def _read(self, queued):
        scan_id, message_id = queued["scan_page_id"], queued["message_id"]
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            page, _ = self._source(db, scan_id)
            if not self.eligible(page):
                self._skip(db, scan_id)
                return
            page = dict(page)
        _, content = self.inbox.read_artifact(page["page_artifact_id"])
        if sha(content) != page["content_sha256"]:
            raise ValueError("Retained page checksum differs.")
        result = recognize_aliases(content)
        # On a sticker scan the alias is under the sticker's code, not at the corner positions: when those show none, the top band.
        if not any(tokens.valid_alias((read.get("text") or "").strip()) for read in result["reads"]):
            with connect(self.path) as db:
                sticker = self.sticker_scan(db, message_id)
            if sticker:
                result = {**self.read_sticker(content), "corner_reads": result["reads"]}
        if result["source_sha256"] != page["content_sha256"]:
            raise ValueError("OCR source checksum differs.")
        with self.rosters.lock(), ExitStack() as locks:
            with self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
                current, job = self._source(db, scan_id)
                if not self.eligible(current):
                    self._skip(db, scan_id)
                    return
                message = self.inbox.message(db, message_id)
            summary = self.summarize(result)
            if summary["outcome"] == "matched":
                # Like barcode identification, new OCR observations can flag
                # changed content on earlier messages. Lock every affected
                # owner; inbox.files below acquires the current owner's lock.
                with connect(self.path) as db:
                    owners = [r[0] for r in db.execute(
                        "SELECT DISTINCT message_id FROM scan_pages WHERE page_stamp_id=? AND message_id!=? ORDER BY message_id",
                        (summary["match"]["stamp_id"], message_id))]
                for owner in owners:
                    locks.enter_context(self.scans.documents.files.owner_lock(owner))
                summary["outcome"] = "resolved"
            summary["instructor_review_required"] = bool(message["instructor_review_required"])
            attempt = queued["attempts"] + 1
            artifact_id = str(uuid.uuid5(uuid.UUID(scan_id), f"alias-ocr:{attempt}"))
            report = {"scan_page_id": scan_id, "message_id": message_id,
                      "source_artifact_id": page["page_artifact_id"], "attempt": attempt,
                      "created_at": timestamp(), "ocr": result, "review": summary}
            payload = encoded(report).encode()
            relative = f"ocr/{scan_id}/attempt-{attempt}.json"
            # The verified files context takes this owner's lock itself. Keep
            # the lifecycle lock, but do not recursively flock the same owner.
            with self.inbox.files(message, write=True) as root:
                publish(root / relative, payload)
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("INSERT INTO inbox_artifacts VALUES(?,?,?,?,?,?,?,?,?)",
                               (artifact_id, message_id, job["id"], f"scan-alias-ocr-{page['source_page_index']}-attempt-{attempt}.json",
                                relative, "output", len(payload), sha(payload), timestamp()))
                    if summary["outcome"] == "resolved":
                        self._resolve(db, current, summary["match"])
                        self.inbox.event(db, job["id"], "automatic_page_recovery", encoded({
                            "actor": "alias_ocr", "scan_page_id": scan_id, "stamp_id": summary["match"]["stamp_id"],
                            "artifact_id": artifact_id, "method": "exact_printed_alias", "confirmation_required": False}))
                    db.execute("UPDATE scan_alias_ocr SET status='complete',artifact_id=?,result_json=?,error='',updated_at=? WHERE scan_page_id=? AND status='running'",
                               (artifact_id, encoded(summary), timestamp(), scan_id))
                    self.inbox.event(db, job["id"], "alias_ocr_complete", encoded({"scan_page_id": scan_id,
                        "outcome": summary["outcome"], "artifact_id": artifact_id,
                        "identity_confirmation_required": summary["needs_review"],
                        "instructor_review_required": bool(message["instructor_review_required"])}))

    @staticmethod
    def read_sticker(content):
        """The page's top band, where a first page's sticker sits: every alias printed in it is a read (the band's own text is kept
        in the report). Measured on the three clipped Exam 2 stickers of 2026-10-24: each alias read exactly, about 7 s a page."""
        result = recognize_bands(content, band="top")
        found = []
        for read in result["reads"]:
            text = re.sub(r"\s+", "", (read.get("text") or "").upper())
            found += [{"text": alias, "truncated": False, "rotation": read.get("rotation"), "band": "top"} for alias in STICKER_ALIAS.findall(text)]
        return {**result, "band_reads": result["reads"], "reads": found}

    def _resolve(self, db, page, match):
        """Caller holds lifecycle and all affected owner locks plus transaction.

        Preserve the same duplicate/changed-content rules as barcode intake.
        The report, identity and audit events commit together; original barcode
        candidates/decoder evidence remain immutable.
        """
        stamp_id, when = match["stamp_id"], timestamp()
        review_state, duplicate = self.scans._identity_state(db, page["id"], stamp_id, page["content_sha256"], when)
        db.execute("UPDATE scan_pages SET page_stamp_id=?,status='resolved',review_state=?,duplicate_of_scan_page_id=?,updated_at=? WHERE id=?",
                   (stamp_id, review_state, duplicate, when, page["id"]))

    @staticmethod
    def _skip(db, scan_id):
        db.execute("UPDATE scan_alias_ocr SET status='skipped',updated_at=? WHERE scan_page_id=?", (timestamp(), scan_id))
