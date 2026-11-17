"""Provenance observations and explicit assembly review on existing Inbox owners."""
import io
import json
import logging
import re
import uuid
from contextlib import ExitStack, nullcontext
from pathlib import Path

from werkzeug.exceptions import Conflict, Forbidden, NotFound

from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT, encoded, sha
from . import process, tokens
from .separation import SeparationService, listed as separation_titles
from .service import identifier, text_field
from .storage import publish

LOG = logging.getLogger(__name__)


DECODE_CHUNK = 20
INSPECTION_FAILED = "PDF inspection failed or exceeded its limits. Originals remain available for instructor review."
# Faculty's own reasons an inspection stops (decode_message, process.run and the scan limits in media.py): only these are shown to
# the instructor word for word (inbox-7). Any other ValueError, a library's that the sandbox passes on ("not enough image data"),
# keeps the generic sentence.
INSPECTION_REASONS = re.compile(
    r"(There are no scan attachments to inspect|Scanner inspection currently accepts PDF attachments"
    r"|Document processing exceeded its time limit|Document processing failed or exceeded its resource limits"
    r"|Document parsing or validation failed|PDF page count exceeds the processing limit|Unsupported or oversized scan page"
    r"|Scan raster exceeds the pixel limit|Unsupported raster resolution|Too many barcode (locations|candidates); page inspection requires manual review"
    r"|Scans must be unencrypted PDFs of at most \d+ pages|Inspect at most \d+ scan pages at a time"
    r"|Extracted scan artifacts exceed the total size limit)\.")
NO_ORIGINAL = "There are no scan attachments to inspect."


def inspection_error(cause):
    """What a failed inspection says: Faculty's own reason, and that the original is kept when there is one; else the generic sentence."""
    reason = str(cause) if type(cause) is ValueError else ""
    if not INSPECTION_REASONS.fullmatch(reason):
        return INSPECTION_FAILED
    return reason if reason == NO_ORIGINAL else f"{reason} The original is kept."


def paper_sections(paper):
    """The section IDs a recognized print job's paper is for: its section, or the sections a job printed for the whole term lists."""
    return [paper["section_id"]] if paper["section_id"] else [section["id"] for section in paper["for_sections"]]


class ScanService:
    def __init__(self, documents):
        self.documents = documents
        self.inbox, self.rosters, self.path = documents.inbox, documents.rosters, documents.path
        from .alias_ocr import AliasOcrService
        from .version_sort import VersionSortService
        from .sequence import SequenceService
        from .unlabeled import UnlabeledService
        self.alias_ocr = AliasOcrService(self)
        self.version_sort = VersionSortService(self)
        self.sequence = SequenceService(self)
        self.unlabeled = UnlabeledService(self)
        from .copy_names import CopyNameService
        self.copy_names = CopyNameService(self)  # Stamped papers printed for no student: whose each is, read from its first page.
        from .photocopies import PhotocopyService
        self.photocopies = PhotocopyService(self)  # A copy's stamps photocopied onto several papers: each paper gets its own copy.
        from .assignment_finder import AssignmentFinder
        self.assignment_finder = AssignmentFinder(self)  # A scan without labels: its assignment, from its template and title or the calendar.
        self.separation = SeparationService(self)  # A scan whose labels name two assignments: one new scan for each (INBOX "SEPARATING A SCAN").
        from .places import PlaceService
        self.places = PlaceService(self)  # A page whose code did not read, known by its place between its own copy's labels.
        self.exams = None  # ExamService: a label job issued for unlabeled exams joins its exam.
        self.jev = None  # JevService, when the workspace has Jev: JEV PAPERS (first pages and whose paper each is)
        self.bubble_detector = None  # BubbleSheetService, when the workspace reads bubble sheets: which unlabeled pages are sheets.
        self.grading = None

    def retry(self, message_id, actor="workspace"):
        with self.rosters.lock(), self.documents.files.owner_lock(message_id), connect(self.path) as db:
            message = self.inbox.message(db, message_id)
            if not message["authenticated"]:
                raise Forbidden("Sender authentication failed; retry cannot override it.")
            job = db.execute("SELECT id,action FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
            if not job or job["action"] != "scan.receive":
                raise ValueError("Page inspection requires a retained scan.receive submission.")
            existing = db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()
            if not existing:
                db.execute("INSERT INTO document_decode_jobs(message_id,status,created_at,updated_at) VALUES(?,'pending',?,?)", (message_id, timestamp(), timestamp()))
                self.inbox.event(db, job["id"], "decode_requested", f"{actor} requested page inspection; instructor review remains required.")
                return
            changed = db.execute("UPDATE document_decode_jobs SET status='pending',attempts=attempts+1,error='',updated_at=? WHERE message_id=? AND status='failed'", (timestamp(), message_id)).rowcount
            if not changed:
                raise Conflict("Only failed decode jobs can be retried.")
            self.inbox.event(db, job["id"], "decode_retry", f"{actor} requested another inspection; instructor review remains required.")

    def process_one(self):
        # Same cross-process worker lock as Inbox execution and receipt handling.
        with self.inbox.worker_lock():
            with self.rosters.lock(), ExitStack() as locks:
                with connect(self.path) as db:
                    owners = [r[0] for r in db.execute("SELECT message_id FROM document_decode_jobs WHERE status IN ('pending','running') ORDER BY message_id")]
                for owner in owners:
                    locks.enter_context(self.documents.files.owner_lock(owner))
                with connect(self.path) as db:
                    db.execute("UPDATE document_decode_jobs SET status='failed',error='Decode worker interrupted; explicit retry required.',updated_at=? WHERE status='running'", (timestamp(),))
                    # PIPELINE: a yielded scan goes behind other pending scans, as well as releasing the other worker queues.
                    row = db.execute("SELECT message_id FROM document_decode_jobs WHERE status='pending' ORDER BY updated_at,created_at,message_id LIMIT 1").fetchone()
                    if not row:
                        return False
                    message_id = row[0]
                    db.execute("UPDATE document_decode_jobs SET status='running',attempts=MAX(attempts,1),updated_at=? WHERE message_id=?", (timestamp(), message_id))
            try:
                complete = self.decode_message(message_id)
                error = ""
            except ValueError as cause:
                # Faculty's own reason ("Scanner inspection currently accepts PDF attachments.", "PDF page count exceeds the processing
                # limit.") tells the instructor whether a retry can work or the PDF must be split. Anything else keeps the generic sentence.
                complete = False
                error = inspection_error(cause)
            except Exception:
                complete = False
                error = INSPECTION_FAILED
            detection_failed = False
            if not error and self.bubble_detector:
                # Bubble sheets carry no label; their positioning marks say what they are before anything goes looking for one.
                try:
                    self.bubble_detector.detect_message(message_id, limit=DECODE_CHUNK)
                    with connect(self.path) as db:
                        complete = complete and not self.bubble_detector._counts(db, message_id)["pending"]
                except Exception:
                    detection_failed = True
            with self.rosters.lock(), self.documents.files.owner_lock(message_id), connect(self.path) as db:
                db.execute("UPDATE document_decode_jobs SET status=?,error=?,updated_at=? WHERE message_id=?",
                           ("failed" if error else "complete" if complete else "pending", error, timestamp(), message_id))
                job_id = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()[0]
                if detection_failed:
                    self.inbox.event(db, job_id, "bubble_sheet_detection_skipped",
                                     "Looking for bubble sheets did not complete; it is tried again by the Inbox worker.")
                if not error and complete:
                    self.alias_ocr.enqueue_message(db, message_id)
                if error or complete:
                    self.inbox.event(db, job_id, "page_inspection", error or "Page provenance attached. No action was authorized or executed; instructor review remains required.")
            if not complete and not error:
                return True  # PIPELINE: retained pages are the cursor; only a fully finished chunk is resumed automatically.
            if not error:
                # Paper labeled on every page: a page whose codes did not read, between its own copy's labels, takes its page by its
                # place, and the empty backs there are set aside (DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS", requirement, 2026-11-14).
                try:
                    self.places.apply(message_id)
                except Exception:
                    LOG.exception("Pages by their place on scan %s did not complete", message_id)
                    with self.rosters.lock(), connect(self.path) as db:
                        self.inbox.event(db, job_id, "pages_by_place_skipped", "Giving unread pages their copy's page by their place did not complete; "
                                                                              "the Inbox worker tries again when the scan changes.")
                # Exams labeled on the first page only: evenly spaced labels leave nothing to decide, so group them now.
                try:
                    self.sequence.auto_group(message_id)
                except Exception:
                    with self.rosters.lock(), connect(self.path) as db:
                        self.inbox.event(db, job_id, "sequence_grouping_skipped", "Automatic grouping by label spacing did not complete; group this scan from Scanned page review.")
            if not error and self.grading:
                self.grading.sync_scan(message_id)
            if not error:
                try:
                    self.apply_start_over(message_id)
                except Exception:
                    LOG.exception("Applying the choices carried by started-over scan %s failed", message_id)
                    with self.rosters.lock(), connect(self.path) as db:
                        self.inbox.event(db, job_id, "scan_start_over_applied", encoded({"carried": [], "notes": [
                            "The choices carried from the scan it started over could not be applied; make them on this scan's pages."]}))
                try:
                    self.separation.apply(message_id)  # a scan separated by its printed titles is filed under the assignment they name
                except Exception:
                    LOG.exception("Filing scan %s under the assignment its printed titles name failed; it is found like any scan without labels", message_id)
            return True

    def start_over_plan(self, db, message_id):
        """What a scan started over carries to its new scan (`InboxService.start_over`): the assignment and sections it was filed
        under, and the search for papers without labels with its sides, versions and automatic registration. Choices only: no page,
        reading, registration or grouping is carried; the new scan makes its own."""
        carry = {}
        plan = self.grading.scan_status(db, message_id, fingerprint=False)["plan"] if self.grading else None
        if plan:
            carry["association"] = {"term_id": plan["term_id"], "assignment_id": plan["source_assignment_id"], "section_ids": sorted(plan["section_ids"])}
        run = self.unlabeled._run(db, message_id)
        if run:
            carry["unlabeled"] = {"assignment_id": run["assignment_id"], "sides": run["sides"], "versions": run["versions"] or "",
                                  "register_confident": bool(run["register_confident"])}
        return carry

    def apply_start_over(self, message_id):
        """Once a scan started over is inspected, apply the choices it carried (its assignment, then the search for papers without
        labels), once: an Inbox event records what was applied and why anything was not. Nothing here identifies or clears anything
        the ordinary association and search would not."""
        with connect(self.path) as db:
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
            row = db.execute("SELECT detail FROM inbox_events WHERE job_id=? AND kind='scan_started_over' ORDER BY rowid LIMIT 1", (job["id"],)).fetchone() if job else None
            if not row or db.execute("SELECT 1 FROM inbox_events WHERE job_id=? AND kind='scan_start_over_applied'", (job["id"],)).fetchone():
                return False
            carry = json.loads(row["detail"]).get("carry") or {}
        notes = []
        association = carry.get("association")
        if association and self.grading:
            try:
                with connect(self.path) as db:
                    fingerprint = self.grading.scan_status(db, message_id)["fingerprint"]
                self.grading.associate_scan(message_id, {**association, "request_id": f"start-over:{message_id}", "fingerprint": fingerprint})
            except (ValueError, Conflict, Forbidden, NotFound) as error:
                notes.append(f"Its assignment was not carried over: {error}")
        reading = carry.get("unlabeled")
        if reading:
            try:
                self.unlabeled.request(message_id, {"assignment_id": reading["assignment_id"], "sides": reading["sides"]},
                                       versions=reading["versions"], register_confident=reading["register_confident"])
            except (ValueError, Conflict, Forbidden, NotFound) as error:
                notes.append(f"Papers without labels were not looked for again: {error}")
        with self.rosters.lock(), connect(self.path) as db:
            self.inbox.event(db, job["id"], "scan_start_over_applied", encoded({"carried": sorted(carry), "notes": notes}))
        return True

    @staticmethod
    def resolution(provenance):
        if provenance is None:
            return "unknown"
        if provenance["batch_status"] == "cancelled" or provenance["state"] == "cancelled":
            return "cancelled"
        if provenance["superseded_at"] or provenance["state"] == "superseded":
            return "superseded"
        if provenance["batch_status"] != "ready" or provenance["state"] != "issued":
            return "unissued"
        return "resolved"

    def classify(self, page):
        teaching = [candidate["payload"] for candidate in page["candidates"] if candidate["payload"].startswith(("T", "t1-"))]
        if not teaching:
            return None, "blank" if page["blank"] and not page["candidates"] else "unreadable"
        if len(set(teaching)) != 1:
            return None, "ambiguous"
        try:
            tokens.parse_token(teaching[0])
        except ValueError:
            return None, "malformed"
        provenance = self.documents.resolve_token(teaching[0], authenticated=True)
        if provenance is None:
            with connect(self.path) as db:
                if self.documents.sheet_label(db, teaching[0]):
                    # A Scantron label (migration 015) is no page of a paper: the sheet stays an unlabeled page, exactly as an
                    # unlabeled bubble sheet was, and a bubble sheet read takes its student from the label kept in `candidates_json`.
                    return None, "unreadable"
        return provenance, self.resolution(provenance)

    def decode_message(self, message_id):
        """Retain at most DECODE_CHUNK pages; return whether every attachment is inspected (PIPELINE).

        The cursor is the source Inbox message's existing scan_pages receipts. A yielded job is pending; a process that exits
        during extraction leaves running work for the ordinary explicit retry. No new payload or catalog owner is introduced.
        """
        job = self.inbox.get_by_message(message_id)
        if job["action"] != "scan.receive" or not job["authenticated"]:
            raise Forbidden("Only authenticated scanner intake can receive automatic page inspection.")
        inputs = [file for file in job["artifacts"] if file["role"] == "input"]
        if not inputs:
            raise ValueError("There are no scan attachments to inspect.")
        # Every file is counted first, so the Inbox can show how far inspection has got ("480 of 652").
        with connect(self.path) as db:
            recorded_total = db.execute("SELECT pages_total FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()[0]
        totals = []
        for source in inputs:
            if len(inputs) == 1 and recorded_total is not None:
                totals.append(recorded_total)  # Immutable input: the common single-PDF scan needs counting only once.
                continue
            name, content = self.inbox.read_artifact(source["id"])
            if Path(name).suffix.lower() != ".pdf" or not content.startswith(b"%PDF-"):
                raise ValueError("Scanner inspection currently accepts PDF attachments.")
            totals.append(process.run("pages", content)["total"])
        with self.rosters.lock(), self.documents.files.owner_lock(message_id), connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET pages_total=? WHERE message_id=?", (sum(totals), message_id))
        worked = 0
        for source, total in zip(inputs, totals):
            # A section's scan is hundreds of pages: inspect it in bounded pieces, skipping pieces already retained.
            for first in range(1, total + 1, DECODE_CHUNK):
                last = min(total, first + DECODE_CHUNK - 1)
                with connect(self.path) as db:
                    retained = {row[0] for row in db.execute("SELECT source_page_index FROM scan_pages WHERE source_artifact_id=? AND source_page_index BETWEEN ? AND ?",
                                                           (source["id"], first, last))}
                if len(retained) == last - first + 1:
                    continue
                if worked >= DECODE_CHUNK:
                    return False
                first = next(index for index in range(first, last + 1) if index not in retained)
                end = last
                last = min(last, first + DECODE_CHUNK - worked - 1)
                content = self.inbox.read_artifact(source["id"])[1]
                for page in process.run("extract", content, {"first": first, "last": last}):
                    self._retain_page(job, source, page)
                worked += last - first + 1
                if last < end:
                    return False
        return True

    def _retain_page(self, job, source, page):
        with self.rosters.lock(), ExitStack() as locks:
            with connect(self.path) as db:
                if db.execute("SELECT 1 FROM scan_pages WHERE source_artifact_id=? AND source_page_index=?", (source["id"], page["index"])).fetchone():
                    return
                message = self.inbox.message(db, job["message_id"])
            provenance, state = self.classify(page)
            stamp_id = provenance["stamp_id"] if provenance else None
            # Pixel equality is not exact-byte equality: different PDF content
            # can render identically at the working resolution. Preserve it as
            # a conflict unless the retained extracted page bytes match.
            content_hash = sha(page["pdf"])
            decoder = {**page["decoder"], "content_hash": "sha256:extracted-page-pdf"}
            # A new observation can flag observations on earlier messages too.
            # Hold each affected message owner before publishing that change;
            # inbox.files below acquires the current message's owner itself.
            with connect(self.path) as db:
                other_owners = [r[0] for r in db.execute(
                    "SELECT DISTINCT message_id FROM scan_pages WHERE page_stamp_id=? AND message_id!=? ORDER BY message_id",
                    (stamp_id, message["id"]))]
            for owner in other_owners:
                locks.enter_context(self.documents.files.owner_lock(owner))
            ids = {kind: str(uuid.uuid5(uuid.UUID(message["id"]), f"{source['id']}:{page['index']}:{kind}")) for kind in ("pdf", "png", "observation")}
            with self.inbox.files(message, write=True) as root:
                artifacts = []
                for kind in ("pdf", "png"):
                    relative = f"scans/{source['id']}/{page['index']}.{kind}"
                    publish(root / relative, page[kind])
                    artifacts.append((ids[kind], message["id"], job["id"], f"scan-page-{page['index']}.{kind}", relative,
                                      "output", len(page[kind]), sha(page[kind]), timestamp()))
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    duplicate = db.execute("SELECT id FROM scan_pages WHERE page_stamp_id=? AND content_sha256=? AND NOT EXISTS(SELECT 1 FROM inbox_deletions gone WHERE gone.message_id=scan_pages.message_id AND gone.restored_at IS NULL) ORDER BY created_at,id LIMIT 1", (stamp_id, content_hash)).fetchone() if stamp_id else None
                    changed = db.execute("SELECT 1 FROM scan_pages WHERE page_stamp_id=? AND content_sha256!=? AND NOT EXISTS(SELECT 1 FROM inbox_deletions gone WHERE gone.message_id=scan_pages.message_id AND gone.restored_at IS NULL) LIMIT 1", (stamp_id, content_hash)).fetchone() if stamp_id else None
                    review_state = "ignored" if state == "blank" else "duplicate" if duplicate else "conflict" if changed else "candidate"
                    if changed and not duplicate:
                        db.execute("UPDATE scan_pages SET review_state='conflict',updated_at=? WHERE page_stamp_id=? AND review_state='candidate'", (timestamp(), stamp_id))
                    db.executemany("INSERT INTO inbox_artifacts VALUES(?,?,?,?,?,?,?,?,?)", artifacts)
                    db.execute("""INSERT INTO scan_pages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (ids["observation"], message["id"], source["id"], page["index"], stamp_id, encoded(page["candidates"]), state,
                                ids["pdf"], ids["png"], content_hash, encoded(decoder), duplicate[0] if duplicate else None, review_state,
                                message["received_at"], timestamp(), timestamp(), timestamp()))

    def review_item(self, message_id, heavy=True):
        """The scan's page review. `heavy=False` is its summary for the stage pages that show neither every page row nor every
        assembled paper: `page_count` stands in for `pages` and `assemblies`, and everything else is computed exactly the same."""
        with connect(self.path) as db:
            message = self.inbox.message(db, message_id)
            decode = db.execute("SELECT * FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()
            page_count = None if heavy else db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=?", (message_id,)).fetchone()[0]
            pages = [] if not heavy else [dict(r) for r in db.execute("""SELECT sp.*,p.page_number,c.id copy_id,c.copy_number,b.id batch_id,b.status batch_status,
                b.superseded_at,a.title,v.label version_label,t.code,t.term,t.term_other,t.year,s.number section_number
                FROM scan_pages sp LEFT JOIN page_stamps p ON p.id=sp.page_stamp_id
                LEFT JOIN printed_copies c ON c.id=p.printed_copy_id LEFT JOIN print_batches b ON b.id=c.print_batch_id
                LEFT JOIN assignment_versions v ON v.id=b.assignment_version_id LEFT JOIN assignments a ON a.id=v.assignment_id
                LEFT JOIN course_sections s ON s.id=b.section_id LEFT JOIN course_terms t ON t.id=b.term_id
                WHERE sp.message_id=? ORDER BY sp.source_artifact_id,sp.source_page_index""", (message_id,))]
            readings = {r["scan_page_id"]: dict(r) for r in db.execute("SELECT * FROM scan_alias_ocr WHERE message_id=?", (message_id,))} if heavy else {}
            for page in pages:
                reading = readings.get(page["id"])
                if reading:
                    reading["result"] = json.loads(reading.pop("result_json"))
                    reading["can_retry"] = self.alias_ocr.enabled and reading["status"] == "failed" and self.alias_ocr.eligible(page)
                page["alias_ocr"] = reading
            # Pages identified by their position after a first-page label say so, unlike barcode or alias identities.
            positions = {r["scan_page_id"]: dict(r) for r in db.execute(
                """SELECT q.scan_page_id,q.page_stamp_id,q.run_id,q.side,a.source_page_index label_page FROM scan_sequence_pages q
                   JOIN scan_sequence_runs r ON r.id=q.run_id JOIN scan_pages a ON a.id=q.anchor_scan_page_id
                   WHERE r.message_id=? AND r.undone_at IS NULL ORDER BY r.created_at""", (message_id,))} if heavy else {}
            grouped = {r["scan_page_id"]: dict(r) for r in db.execute(
                """SELECT q.scan_page_id,q.side,k.id packet_id,k.sides,c.copy_number FROM scan_packet_pages q JOIN scan_packets k ON k.id=q.packet_id
                   JOIN scan_sequence_runs r ON r.id=k.run_id JOIN printed_copies c ON c.id=k.printed_copy_id
                   WHERE k.message_id=? AND r.undone_at IS NULL""", (message_id,))} if heavy else {}
            # A bubble sheet carries no label or alias to recover: the Scan page leaves it to the Bubble sheets stage. Said only of
            # sheets, so a scan without one reads exactly as before.
            sheets = self.bubble_detector.sheet_pages(db, message_id) if heavy and self.bubble_detector else set()
            # Where else a page's label was read, for a page that needs a look because of it (requirement, 2026-10-24): the same label on another scanned page, with different content or the same, in a scan still
            # in the Inbox. Read only for those pages.
            twice = [page["page_stamp_id"] for page in pages if page["page_stamp_id"] and page["review_state"] in ("conflict", "duplicate")]
            others = {}
            for start in range(0, len(twice), 500):
                chunk = twice[start:start + 500]
                for row in db.execute(f"""SELECT sp.id,sp.page_stamp_id,sp.message_id,sp.source_page_index,sp.review_state,j.id job_id,m.received_at
                        FROM scan_pages sp JOIN inbox_messages m ON m.id=sp.message_id JOIN inbox_jobs j ON j.message_id=m.id
                        WHERE sp.page_stamp_id IN ({','.join('?' * len(chunk))}) AND {PRESENT.format('sp.message_id')}
                        ORDER BY m.received_at,sp.source_page_index""", chunk):
                    others.setdefault(row["page_stamp_id"], []).append(dict(row))
            for page in pages:
                if page["id"] in {o["id"] for o in others.get(page["page_stamp_id"], [])}:
                    page["also_scanned"] = [o for o in others[page["page_stamp_id"]] if o["id"] != page["id"]]
                position = positions.get(page["id"])
                page["sequence"] = position if position and position["page_stamp_id"] == page["page_stamp_id"] else None
                page["packet"] = grouped.get(page["id"])
                if page["id"] in sheets:
                    page["bubble_sheet"] = True
            sequence = self.sequence.summary(db, message_id)
            recognition = self.recognition(db, message_id)
            label_modes = self.sequence.label_modes(db, message_id)
            overview = self.overview(db, message_id, recognition, label_modes)
            grading = self.grading.scan_status(db, message_id) if self.grading else None
            assemblies = [self.assembly(copy_id, db) for copy_id in sorted({p["copy_id"] for p in pages if p["copy_id"]})]
            aside = self.set_aside_pages(db, message_id)
        return {"message_id": message_id, "grading": grading, "overview": overview, "sequence": sequence, "recognition": recognition, "label_modes": label_modes, "instructor_review_required": bool(message["instructor_review_required"]),
                "decode": dict(decode) if decode else None, **({"pages": pages, "assemblies": assemblies} if heavy else {"page_count": page_count}),
                **({"set_aside": aside} if aside else {}),
                "version_sort": self.version_sort.summary(message_id), "unlabeled": self.unlabeled.summary(message_id),
                "copy_names": self.copy_names.summary(message_id) if overview.get("copy_names") else None}

    def recognition(self, db, message_id, memo=None):
        """What a scan is for, read from what is printed on it: whose paper it holds, how much of it, and what does not fit.

        `verdict` is `recognized` when the current paper names one assignment in one
        term, whichever of its sections sat it together (`sections` lists them);
        `mixed` when current labels name different assignments, or no label is of
        current paper; `bubble_sheets` when no label resolved but pages were
        recognized as bubble sheets by their positioning marks; and `unrecognized`
        otherwise. Bubble sheets carry no label, so whose they are stays the
        instructor's explicit association (`bubble_sheets.read`). Nothing is
        inferred from names.

        A doubtful label (unknown, malformed, ambiguous) and a label of paper that
        is not current (cancelled, superseded, unissued) are flags on their own
        pages, not on the scan (requirement, 2026-09-28): the pages behind them are held
        by the grouping and the assembly rules, and the rest of the scan goes on.
        `papers` lists current paper first; `current_papers` is what the scan is for.
        """
        papers = []
        for row in db.execute("""SELECT b.id batch_id,b.status,b.superseded_at,a.id assignment_id,a.title,v.label version_label,t.id term_id,t.code,t.term,t.term_other,t.year,s.id section_id,s.number section_number,
                                        COUNT(DISTINCT c.id) copies,COUNT(*) pages
                                 FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id JOIN printed_copies c ON c.id=p.printed_copy_id
                                 JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                                 JOIN assignments a ON a.id=v.assignment_id LEFT JOIN course_sections s ON s.id=b.section_id JOIN course_terms t ON t.id=b.term_id
                                 WHERE sp.message_id=? GROUP BY b.id ORDER BY a.title,s.number""", (message_id,)).fetchall():
            item = dict(row)
            if not item["section_id"]:
                # Paper printed for the whole term (migration 013) names no section; the job lists the sections it is for.
                item["for_sections"] = self.documents.job_sections(db, item["batch_id"])
            item["current"] = item.pop("status") == "ready" and not item["superseded_at"]
            item["issued"] = db.execute("SELECT COUNT(*) FROM printed_copies WHERE print_batch_id=?", (row["batch_id"],)).fetchone()[0]
            # Papers of this print job that no scan has shown yet, in this arrival or any other.
            item["unseen"] = [dict(r) for r in db.execute(
                f"""SELECT c.copy_number,c.caption,c.enrollment_id FROM printed_copies c WHERE c.print_batch_id=? AND NOT EXISTS(
                       SELECT 1 FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE p.printed_copy_id=c.id AND {PRESENT.format('sp.message_id')}) ORDER BY c.copy_number""", (row["batch_id"],))]
            papers.append(item)
        # A page the instructor set aside as part of no paper (2026-10-02) is no page without a label left to place or to read.
        states = {row[0]: row[1] for row in db.execute(
            """SELECT CASE WHEN status='unreadable' AND review_state='ignored' THEN 'set_aside' ELSE status END state,COUNT(*)
               FROM scan_pages WHERE message_id=? GROUP BY state""", (message_id,))}
        doubtful = {key: states[key] for key in ("unknown", "malformed", "ambiguous", "cancelled", "superseded", "unissued") if states.get(key)}
        papers.sort(key=lambda item: not item["current"])  # Current paper first: it is what the scan is for.
        # Sections that sit one exam together are scanned together: one assignment in one term is one purpose. Labels of paper that
        # is not current, and labels that did not resolve, are flags on their own pages (`doubtful_labels`), never the scan's purpose.
        scopes = {(item["assignment_id"], item["term_id"]) for item in papers if item["current"]}
        # `memo`: the caller's per-read memo of booklet versions, shared by every scan of one view (PERFORMANCE).
        sheets = self.bubble_detector.detected(db, message_id, memo) if self.bubble_detector else None
        verdict = ("recognized" if len(scopes) == 1 else "mixed") if papers else \
            "bubble_sheets" if sheets and sheets["sheets"] else "unrecognized"
        # What the identities came from (requirement, 2026-09-24).
        labels_read = states.get("resolved", 0)
        first_pages_read = db.execute("""SELECT COUNT(*) FROM scan_unlabeled_registrations g JOIN scan_pages sp ON sp.id=g.scan_page_id
                                         WHERE sp.message_id=? AND g.basis!='superseded'""", (message_id,)).fetchone()[0]
        # Paper printed for the whole term is for the sections its job lists.
        numbers = {number for item in papers if item["current"] or not scopes
                   for number in ([item["section_number"]] if item["section_id"] else [s["number"] for s in item["for_sections"]])}
        return {"verdict": verdict, "papers": papers, "current_papers": [item for item in papers if item["current"]],
                "sections": sorted(numbers), "doubtful_labels": doubtful,
                "labels_read": labels_read, "first_pages_read": first_pages_read,
                "pages": sum(states.values()), "without_label": states.get("unreadable", 0), "blank": states.get("blank", 0), "bubble_sheets": sheets,
                **({"set_aside": states["set_aside"]} if states.get("set_aside") else {})}

    # ----- when a scan is deleted from, or restored to, the Inbox -------------------------------

    @staticmethod
    def before_removal(db, message_id):
        """A scan can leave the Inbox unless something recorded depends on it, or the worker is still busy with it."""
        if db.execute("SELECT 1 FROM assembly_pages ap JOIN scan_pages sp ON sp.id=ap.scan_page_id WHERE sp.message_id=? LIMIT 1", (message_id,)).fetchone():
            raise Conflict("Pages of this scan are part of a reviewed assembly, which is a recorded submission. It cannot be deleted from the Inbox.")
        busy = db.execute("""SELECT (SELECT COUNT(*) FROM document_decode_jobs WHERE message_id=:m AND status IN ('pending','running'))
                                  + (SELECT COUNT(*) FROM scan_alias_ocr WHERE message_id=:m AND status IN ('pending','running'))
                                  + (SELECT COUNT(*) FROM scan_version_sorts WHERE message_id=:m AND status IN ('pending','running'))
                                  + (SELECT COUNT(*) FROM scan_unlabeled_runs WHERE message_id=:m AND status IN ('pending','running'))""", {"m": message_id}).fetchone()[0]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bubble_runs'")}
        if "bubble_runs" in tables:
            busy += db.execute("SELECT COUNT(*) FROM bubble_runs WHERE message_id=? AND status IN ('pending','running')", (message_id,)).fetchone()[0]
        if busy:
            raise Conflict("The Inbox worker is still inspecting or reading this scan. Wait for it to finish, then delete it.")

    @staticmethod
    def visibility_changed(db, message_id):
        """Set right what the other scans say once this one left, or came back: duplicates and changed content are relative.

        For every label this scan shows, the observations still in the Inbox are
        compared afresh by the intake rule: the first of each content is a
        candidate, later ones repeat it, and different contents conflict. Pages
        in a reviewed assembly, and pages the instructor rejected, keep their state.
        """
        when = timestamp()
        for (stamp_id,) in db.execute("SELECT DISTINCT page_stamp_id FROM scan_pages WHERE message_id=? AND page_stamp_id IS NOT NULL", (message_id,)).fetchall():
            rows = db.execute(f"""SELECT sp.id,sp.content_sha256,sp.review_state,EXISTS(SELECT 1 FROM assembly_pages ap WHERE ap.scan_page_id=sp.id) kept
                                 FROM scan_pages sp WHERE sp.page_stamp_id=? AND sp.status IN ('resolved','manual') AND {PRESENT.format('sp.message_id')}
                                 ORDER BY sp.created_at,sp.id""", (stamp_id,)).fetchall()
            contents = {row["content_sha256"] for row in rows if row["review_state"] != "rejected"}
            first = {}
            for row in rows:
                original = first.setdefault(row["content_sha256"], row["id"])
                if row["kept"] or row["review_state"] not in ("candidate", "duplicate", "conflict"):
                    continue
                state = "duplicate" if original != row["id"] else "conflict" if len(contents) > 1 else "candidate"
                db.execute("UPDATE scan_pages SET review_state=?,duplicate_of_scan_page_id=?,updated_at=? WHERE id=?", (state, original if state == "duplicate" else None, when, row["id"]))

    GROUPED = """EXISTS(SELECT 1 FROM scan_packet_pages q JOIN scan_packets k ON k.id=q.packet_id JOIN scan_sequence_runs n ON n.id=k.run_id
                        WHERE q.scan_page_id=sp.id AND n.undone_at IS NULL)"""

    @staticmethod
    def _file_numbers(db, message_id):
        """Each attachment of the scan's pages by its 1-based number, in the (filename, artifact id) order the grouping reads them
        (`sequence._rows`, `ungrouped`): the `file` a page shows is the same wherever the review names it."""
        return {row[0]: number for number, row in enumerate(db.execute(
            "SELECT DISTINCT f.id,f.filename FROM scan_pages sp JOIN inbox_artifacts f ON f.id=sp.source_artifact_id WHERE sp.message_id=? ORDER BY f.filename,f.id",
            (message_id,)), start=1)}

    def ungrouped(self, db, message_id, papers=True):
        """Stretches of consecutive pages with no identity that no grouped paper holds, bubble sheets and their blank backs aside.

        Each says its attachment (`file`, 1-based in the order the grouping reads them), its pages, how many of them are written but
        unidentified, and `paper` (with `papers`): the ungrouped first page right before it, whose paper the stretch would continue.
        Read only; the overview asks for `paper` only when it says what was left out of the grouping.
        """
        from .sequence import sheet_back, sheet_pages  # as in __init__: sequence is imported where it is used
        sheets = sheet_pages(db, message_id, self.bubble_detector)
        sides = dict(db.execute("SELECT source_artifact_id,COUNT(*) FROM scan_pages WHERE message_id=? GROUP BY source_artifact_id", (message_id,)).fetchall())
        files = self._file_numbers(db, message_id)
        stretches, previous = [], None
        # A page the instructor set aside (review state `ignored`) is part of no paper by their word: like a blank side, it is in its
        # stretch but no page left to group (requirement, 2026-10-02).
        for row in map(dict, db.execute(f"""SELECT sp.id,sp.source_artifact_id,sp.source_page_index,
                                        CASE WHEN sp.status='unreadable' AND sp.review_state='ignored' THEN 'set_aside' ELSE sp.status END status
                                 FROM scan_pages sp JOIN inbox_artifacts f ON f.id=sp.source_artifact_id
                                 WHERE sp.message_id=? AND sp.status IN ('unreadable','blank') AND NOT {self.GROUPED}
                                 ORDER BY f.filename,sp.source_artifact_id,sp.source_page_index""", (message_id,))):
            after_sheet = sheet_back(previous, row, sheets, sides[row["source_artifact_id"]] % 2 == 0)
            previous = row
            if row["id"] in sheets or after_sheet:
                continue
            last = stretches[-1] if stretches else None
            if last and last["artifact"] == row["source_artifact_id"] and last["last_page"] + 1 == row["source_page_index"]:
                last["last_page"] = row["source_page_index"]
                last["unidentified"] += row["status"] == "unreadable"
            else:
                stretches.append({"artifact": row["source_artifact_id"], "file": files[row["source_artifact_id"]], "first_page": row["source_page_index"],
                                  "last_page": row["source_page_index"], "unidentified": int(row["status"] == "unreadable"), "paper": None})
        # A paper that repeats one kept from another scan (its first page `rejected`: `SequenceService._repeat`, or the instructor's
        # reviewed paper chose the other copy) needs nothing: its sides are no stretch to look at.
        repeated = lambda stretch: db.execute("""SELECT 1 FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE sp.source_artifact_id=?
            AND sp.source_page_index=? AND sp.status IN ('resolved','manual') AND p.page_number=1 AND sp.review_state='rejected'""",
            (stretch["artifact"], stretch["first_page"] - 1)).fetchone()
        stretches = [stretch for stretch in stretches if stretch["unidentified"] and not repeated(stretch)]
        for stretch in stretches[:200] if papers else []:
            before = db.execute(f"""SELECT sp.source_page_index,p.page_number,{self.GROUPED} grouped FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                                    WHERE sp.source_artifact_id=? AND sp.source_page_index=? AND sp.status IN ('resolved','manual')""",
                                (stretch["artifact"], stretch["first_page"] - 1)).fetchone()
            if before and before["page_number"] == 1 and not before["grouped"]:
                stretch["paper"] = {"first_page": before["source_page_index"], "sides": stretch["last_page"] - before["source_page_index"] + 1}
                # A front side right before a first page that is the back of the same sheet (odd, then even): scanned back side first.
                after = db.execute(f"""SELECT p.page_number,{self.GROUPED} grouped FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                                        WHERE sp.source_artifact_id=? AND sp.source_page_index=? AND sp.status IN ('resolved','manual')""",
                                    (stretch["artifact"], stretch["last_page"] + 1)).fetchone()
                stretch["turned"] = bool(after and after["page_number"] == 1 and not after["grouped"] and stretch["last_page"] % 2 == 1)
        return stretches

    @staticmethod
    def _labeled_throughout(db, message_id):
        """Whether every copy on the scan is labeled on every page and has every page read (in any scan still in the Inbox): the
        papers no grouping makes, as `_in_no_paper` says of the overview's copies."""
        copies = db.execute(f"""SELECT b.label_mode='every_page' AND COALESCE(json_extract(v.compiler_json,'$.generator'),'')!='faculty-stamps-only'
                AND (SELECT COUNT(DISTINCT p2.page_number) FROM page_stamps p2 JOIN scan_pages s2 ON s2.page_stamp_id=p2.id WHERE p2.printed_copy_id=c.id
                     AND s2.status IN ('resolved','manual') AND s2.review_state!='rejected' AND {PRESENT.format('s2.message_id')})>=v.page_count
            FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
            WHERE c.id IN (SELECT p.printed_copy_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE sp.message_id=?)""",
                            (message_id,)).fetchall()
        return bool(copies) and all(copy[0] for copy in copies)

    def papers_to_check(self, message_id):
        """What the scan's Papers to check page shows (requirement, 2026-09-24): the papers the grouping holds back and why, the sheets scanned back side first
        that the instructor's own grouping would put in order (with the paper before each, which gets its own sides back), and pages in
        no paper, each with its student and every side's retained image. `ready`/`fingerprint` are the plan the page's Group button
        applies through the ordinary grouping endpoint. Since 2026-10-02 (instructor) a held paper whose page 1 label was read says how
        it can be grouped by hand (`choose`: the fingerprint of the sides shown, for `SequenceService.group_held`), a paper grouped by
        hand is listed with its run and the sides left as scanned (`kind` `by_hand`), a side in no paper says whether it can be set
        aside (`can_set_aside`; never on a scan deleted from the Inbox), a side set aside says so (`set_aside`), and `set_aside`
        lists the pages set aside, with their reasons. `grouped` counts the papers a grouping of the scan made; papers grouped by
        hand are listed apart, with `assembled` once one is a reviewed paper (its grouping then stays). Read only: nothing is
        grouped, identified or decided here."""
        from .sequence import HELD_PAPER, set_aside as sequence_set_aside  # as in __init__: sequence is imported where it is used
        with self.rosters.lock(), connect(self.path) as db:
            self.inbox.message(db, message_id)
            sides = self.sequence.plan_sides(db, message_id)
            grouped = db.execute("""SELECT COUNT(*) FROM scan_packets k JOIN scan_sequence_runs r ON r.id=k.run_id
                                    WHERE k.message_id=? AND r.undone_at IS NULL AND COALESCE(json_extract(r.summary_json,'$.origin'),'')!=?""",
                                 (message_id, HELD_PAPER)).fetchone()[0]
            aside = self.set_aside_pages(db, message_id)
            result = {"message_id": message_id, "sides": sides, "grouped": grouped, "ready": 0, "turned": 0, "fingerprint": None, "files": 0, "papers": [],
                      **({"set_aside": aside} if aside else {})}
            # NOTHING TO GROUP (take-home, 2026-11-11: "it says i need to group 74 pages"): papers labeled on every page, every page of
            # them read, are put together from their labels, so with no grouping of the scan made there is no plan to offer; the side
            # count their labels suggest is no reason for one. The page then lists only the pages in no paper and those set aside.
            if sides and not grouped and (self._labeled_throughout(db, message_id) or self.places.holds_every_unread_side(db, message_id)) and not db.execute(
                    "SELECT 1 FROM scan_sequence_runs WHERE message_id=? AND undone_at IS NULL LIMIT 1", (message_id,)).fetchone():
                result["sides"] = sides = None
            if not sides:
                return result
            rows = self.sequence._rows(db, message_id)
            plan = self.sequence._plan(db, message_id, sides, turn=True, rows=rows)
            # A paper finished by the next stack's first pages lists those sides too (INBOX "BATCHES SCANNED IN STACKS").
            pages = {row["id"]: row for row in [*rows, *plan.get("continuation_rows", [])]}
            at = {(page["source_artifact_id"], page["source_page_index"]): page for page in rows}
            _, held = self.sequence._packets(db, message_id)
            files = self._file_numbers(db, message_id)
            by_hand = {row["packet_id"]: dict(row) for row in db.execute(
                """SELECT k.id packet_id,r.id run_id,json_extract(r.summary_json,'$.reason') reason,r.actor,r.created_at FROM scan_packets k
                   JOIN scan_sequence_runs r ON r.id=k.run_id WHERE k.message_id=? AND r.undone_at IS NULL AND json_extract(r.summary_json,'$.origin')=?""",
                (message_id, HELD_PAPER))}
            present = db.execute(f"SELECT {PRESENT.format('?')}", (message_id,)).fetchone()[0]
            exams = plan["exams"]
            gives = {id(exams[i - 1]) for i, exam in enumerate(exams) if exam.get("turned") and exam["ready"] and i and not exams[i - 1]["problems"]
                     and not exams[i - 1].get("turned") and not exams[i - 1].get("by_hand") and exams[i - 1].get("attachment") == exam.get("attachment")}
            items = []

            def side_list(ids, first_id=None, offer=False):
                # `offer`: a side in no paper may be set aside as part of no paper (a written page with no label and no identity, in no
                # grouped paper and no bubble sheet, on a scan still in the Inbox); the server checks again when asked. A side already
                # set aside says so, wherever it is listed.
                listed = []
                for pid in ids:
                    page = pages[pid]
                    side = {"scan_page_id": pid, "page": page["source_page_index"], "status": page["status"], "first": pid == first_id}
                    if sequence_set_aside(page):
                        side["set_aside"] = True
                    elif offer and present and page["status"] == "unreadable" and not page["page_stamp_id"] and not held.get(pid) and not page.get("sheet"):
                        side["can_set_aside"] = True
                    listed.append(side)
                return listed
            for exam in exams:
                span = [at[(exam["attachment"], index)]["id"] for index in range(exam["first_page"], exam["last_page"] + 1)] if exam.get("attachment") else []
                if exam.get("by_hand"):
                    # Grouped by hand: its sides as the instructor ticked them, each the page its place makes it, and the sides left as
                    # scanned, which a page set aside leaves (it is listed with the pages set aside).
                    # Once it is a reviewed paper its grouping stays (`assembled`: undo is refused, the paper is changed on Review).
                    run = by_hand.get(exam["packet_id"], {})
                    left = [pid for pid in span if pid not in exam["by_hand"] and pages[pid]["review_state"] != "ignored"]
                    items.append({"kind": "by_hand", "file": files[exam["attachment"]], "first_page": exam["first_page"], "last_page": exam["last_page"],
                                  "sides": len(exam["by_hand"]), "copy_number": exam["copy_number"], "enrollment_id": exam["enrollment_id"], "student": None,
                                  "ready": False, "turned": None, "gives_back": False, "grouped_earlier": True, "problems": [],
                                  "run_id": run.get("run_id"), "reason": run.get("reason") or "", "grouped_at": run.get("created_at"),
                                  "assembled": any(pages[pid]["reviewed"] for pid in exam["by_hand"]),
                                  "pages": [{**side, "side": n} for n, side in enumerate(side_list(exam["by_hand"], exam["anchor_scan_page_id"]), start=1)],
                                  "left": side_list(left, offer=True)})
                    continue
                turned = exam.get("turned") and exam["ready"]
                if not exam["problems"] and not turned and id(exam) not in gives:
                    continue  # grouped already, or plainly ready: nothing to look at
                ids = [member["scan_page_id"] for member in exam["members"]] if exam["members"] and not exam["problems"] else span
                item = {"kind": "paper", "file": files[exam["attachment"]], "first_page": exam["first_page"], "last_page": exam["last_page"],
                        "sides": len(ids), "copy_number": exam["copy_number"], "enrollment_id": exam["enrollment_id"], "student": None,
                        "ready": exam["ready"], "turned": exam.get("turned") if turned else None, "gives_back": id(exam) in gives,
                        "grouped_earlier": bool(exam.get("packet_id")), "problems": exam["problems"],
                        "pages": side_list(ids, exam["anchor_scan_page_id"])}
                if exam["problems"] and not exam["ready"]:
                    # Held: grouped by hand when its page 1 label was read (the instructor ticks its sides; DOCUMENTS, 2026-10-02).
                    choice, refusal = self.sequence.held_choice(db, message_id, exam, [pages[pid] for pid in span], held)
                    item.update({"choose": choice} if choice else {"choose_refused": refusal})
                items.append(item)
            for stretch in plan["runs"]:
                ids = [at[(stretch["attachment"], index)]["id"] for index in range(stretch["first_page"], stretch["last_page"] + 1)]
                items.append({"kind": "pages", "file": files[stretch["attachment"]], "first_page": stretch["first_page"], "last_page": stretch["last_page"],
                              "sides": len(ids), "copy_number": None, "enrollment_id": None, "student": None, "ready": False, "turned": None,
                              "gives_back": False, "grouped_earlier": False, "problems": [f"These pages are in no paper: {stretch['where']}."],
                              "pages": side_list(ids, offer=True)})
            # Names from the verified term roster, read once per term, as the Find students page shows them.
            copies = {item["enrollment_id"] for item in items if item["enrollment_id"]}
            sections = {}
            # A copy of paper printed for the whole term (migration 013) is in its student's own section of that term.
            for row in db.execute("""SELECT DISTINCT c.enrollment_id,s.id,s.number,s.term_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                                      JOIN printed_copies c ON c.id=p.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
                                      JOIN course_sections s ON s.id=b.section_id OR (b.section_id IS NULL AND s.term_id=b.term_id
                                        AND s.id IN (SELECT m.section_id FROM roster_memberships m WHERE m.enrollment_id=c.enrollment_id))
                                      WHERE sp.message_id=? AND c.enrollment_id IS NOT NULL""", (message_id,)):
                if row["enrollment_id"] in copies:
                    sections[row["enrollment_id"]] = dict(row)
            names = {}
            for term_id in {row["term_id"] for row in sections.values()}:
                chosen = {row["id"]: row["number"] for row in sections.values() if row["term_id"] == term_id}
                names.update(self.documents.seating._students(db, term_id, [{"id": key, "number": value} for key, value in chosen.items()]))
            for item in items:
                student = names.get(item["enrollment_id"])
                item["student"] = student["name"] if student else None
                item["section_number"] = student["section_number"] if student else None
            items.sort(key=lambda item: (item["file"], item["first_page"]))
            result.update(ready=plan["counts"]["ready"], turned=plan["counts"]["turned"], fingerprint=plan["fingerprint"],
                          files=len({item["file"] for item in items}), papers=items)
        return result

    def page_image(self, message_id, scan_page_id, width=700):
        """One scanned page of this scan, scaled to `width` for looking at on a page (Papers to check). Cut on request; nothing is stored."""
        from PIL import Image
        with connect(self.path) as db:
            self.inbox.message(db, message_id)
            row = db.execute("SELECT raster_artifact_id FROM scan_pages WHERE id=? AND message_id=?", (scan_page_id, message_id)).fetchone()
            if not row:
                raise NotFound("That page is not part of this scan.")
        _, content = self.inbox.read_artifact(row["raster_artifact_id"])
        image = Image.open(io.BytesIO(content))
        # A grayscale PNG with no ancillary chunks (no gamma, resolution, profile or interlace) that already fits the requested size
        # is what the thumbnail would be, pixel for pixel: the retained raster opened at full size is sent as it is.
        if image.format == "PNG" and image.mode == "L" and not image.info and image.width <= width and image.height <= width * 2:
            return content
        image = image.convert("L")
        image.thumbnail((width, width * 2))
        buffer = io.BytesIO()
        # Without optimize: its exhaustive compression search cost four times the encoding for about 2% fewer bytes.
        image.save(buffer, "PNG")
        return buffer.getvalue()

    @staticmethod
    def _where_list(stretches):
        """Where stretches of pages are, as the instructor reads a scan: "page 1, pages 4–5 of file 2" (six at most, then how many more)."""
        files = len({stretch["file"] for stretch in stretches}) > 1
        items = [f"{f'page {s['first_page']}' if s['first_page'] == s['last_page'] else f'pages {s['first_page']}–{s['last_page']}'}"
                 f"{f' of file {s['file']}' if files else ''}" for s in stretches]
        shown = ", ".join(items[:6])
        return f"{shown} and {len(items) - 6} more" if len(items) > 6 else shown

    @staticmethod
    def _in_no_paper(db, message_id, stretches, copies):
        """Whether every page left without a label is in no paper that a grouping could place it in: each stretch comes before the
        first label of its file (or its file has none), or every copy on the scan is labeled on every page and has every page."""
        if copies and all(copy["label_mode"] == "every_page" and not copy["stamps_only"] and copy["seen"] >= copy["page_count"] for copy in copies):
            return True
        firsts = dict(db.execute("""SELECT source_artifact_id,MIN(source_page_index) FROM scan_pages WHERE message_id=? AND status NOT IN ('unreadable','blank')
                                    GROUP BY source_artifact_id""", (message_id,)).fetchall())
        return all(stretch["artifact"] not in firsts or stretch["last_page"] < firsts[stretch["artifact"]] for stretch in stretches)

    @staticmethod
    def _ungrouped_text(stretches, sides):
        files = len({stretch["file"] for stretch in stretches}) > 1
        def where(first, last, file):
            return f"{f'page {first}' if first == last else f'pages {first}–{last}'}{f' of file {file}' if files else ''}"
        def listed(items):
            shown = ", ".join(items[:6])
            return f"{shown} and {len(items) - 6} more" if len(items) > 6 else shown
        pages = sum(stretch["unidentified"] for stretch in stretches)
        if not all(stretch["paper"] for stretch in stretches):
            # Pages in no grouped paper: a student's page whose label was not read, or a sheet that is no one's (2026-10-02).
            return (f"{pages} page{'' if pages == 1 else 's'} {'is' if pages == 1 else 'are'} in no grouped paper: "
                    f"{ScanService._where_list(stretches)}. Recover a label from its printed alias, or set aside a page that belongs to no paper.")
        turned = [stretch for stretch in stretches if stretch.get("turned") and stretch["paper"]["sides"] == sides + 1]
        if turned and len(turned) == len(stretches):
            many = len(turned)
            return (f"{many} sheet{'' if many == 1 else 's'} {'looks' if many == 1 else 'look'} scanned back side first: "
                    f"{listed([where(s['last_page'], s['last_page'] + 1, s['file']) for s in turned])}. {'Its' if many == 1 else 'Each one’s'} page 2 "
                    f"came before its page 1, so the papers on either side were not grouped. Check them, then group them.")
        papers = [(stretch["paper"], stretch) for stretch in stretches]
        many = len(papers)
        counts = [paper["sides"] for paper, _ in papers]
        more = min(counts) > sides
        how = "more sides than" if more else "fewer sides than" if max(counts) < sides else "a number of sides other than"
        items = [f"{where(paper['first_page'], stretch['last_page'], stretch['file'])} ({paper['sides']} sides)" for paper, stretch in papers]
        return (f"{many} paper{'' if many == 1 else 's'} {'has' if many == 1 else 'have'} {how} the {sides} per paper, so "
                f"{'it was' if many == 1 else 'they were'} not grouped: {listed(items)}. "
                f"{('It' if many == 1 else 'Each') + ' may hold an extra sheet: look' if more else 'Look'} before grading.")

    # A scan's flow graph (requirement, 2026-09-24): key, label, tooltip title, and the scan's page for that stage.
    STAGES = (("scan", "Scan", "Check the scanned pages", "pages"), ("assignment", "Assignment", "Choose the assignment", "assignment"),
              ("students", "Students", "Find whose paper each is", "students"), ("sheets", "Bubble sheets", "Read the bubble sheets", "sheets"),
              ("papers", "Papers", "Group the pages into papers", "papers"), ("review", "Review", "Review the scan", "review"),
              ("grading", "Grading", "Grade the papers", None))
    TOOL_STAGE = {"pages": "scan", "sort": "scan", "unlabeled": "students", "copy_names": "students", "bubble": "sheets", "exams": "papers", "assemblies": "review"}
    ACTION_STAGE = {"grading_associate": "assignment", "label_mode": "papers", "set_aside": "papers", "unlabeled_continue": "students", "papers": "papers",
                    "grading_review": "review", "grading_open": "grading"}

    def _stages(self, steps, placed, applies, finished, progress, href=None):
        """The overview's steps as the stages of the scan's flow graph. Each step belongs to one stage (by its action, then its tool, or
        as `placed`). A stage with a step waiting for the instructor or the worker is `started`, and its status says which; otherwise it
        is what `finished` says (`done`, `failed` when the worker stopped, or `idle`). Derived only, like everything in the overview."""
        grouped = {}
        for step in steps:
            key = placed.get(id(step), self.ACTION_STAGE.get(step["action"]) or self.TOOL_STAGE.get(step["tool"]) or "scan")
            if key:
                grouped.setdefault(key, []).append(step)
        stages = []
        for key, label, title, page in self.STAGES:
            if not applies.get(key):
                continue
            mine = grouped.get(key, [])
            kinds = {step["kind"] for step in mine}
            pressing = [step for step in mine if step["kind"] in ("check", "todo", "wait")]
            if pressing:
                state = "started"
                # One word for "needs you" across the Inbox and Assignments (2026-10-18): a check and a to-do read alike; `urgent` tells them apart.
                status = "Needs you" if kinds & {"check", "todo"} else "Working"
                detail = " ".join(step["text"] for step in pressing)
            elif "done" in kinds:
                state, status, detail = "done", "Done", " ".join(step["text"] for step in mine if step["kind"] == "done")
            else:
                state, detail = finished.get(key, ("idle", ""))
                status = {"done": "Done", "failed": "Failed", "idle": "Not started"}[state]
            notes = [step["text"] for step in mine if step["kind"] == "info"]
            if state == "idle" and notes:
                detail = ""  # what the notes say is more to the point than the stage's generic wording
            stages.append({"key": key, "label": label, "title": title, "page": page, "state": state, "status": status,
                           "urgent": "check" in kinds or "todo" in kinds, "detail": " ".join([detail, *notes]).strip(),
                           "progress": progress.get(key) if state == "started" else None, "href": href if key == "grading" else None})
        return stages

    def overview(self, db, message_id, recognition=None, label_modes=None, memo=None):
        """What a scan is and what is left for the instructor: one source for the Inbox list and the scan's own page.

        Everything here is derived from retained records; nothing is decided,
        identified or cleared. `steps` come most pressing first: `todo` only the
        instructor can do, `check` deserves a look, `wait` is the worker's,
        `done`, and `info`. `tool` names the part of Scanned page review a step
        points at. No roster name appears: the list shows this to anyone glancing
        at the screen.
        """
        def plural(count, word, many=None):
            return f"{count} {word if count == 1 else many or word + 's'}"

        def sections(numbers):
            numbers = [str(number) for number in numbers]
            return f"Section {numbers[0]}" if len(numbers) == 1 else f"Sections {', '.join(numbers[:-1])} and {numbers[-1]}"

        decode = db.execute("SELECT status,error,pages_total FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()
        status = decode["status"] if decode else "not queued"
        r = recognition or self.recognition(db, message_id, memo)
        # Until every page is inspected, that is all a scan says: how far it has got. Nothing else can be done with it yet
        # (choosing its assignment waits for a complete inspection), and a partial count must not pass for the scan.
        if not r["pages"] or status in ("pending", "running", "failed"):
            done, total = r["pages"], decode["pages_total"] if decode else None
            has_pdf = db.execute("SELECT 1 FROM inbox_artifacts WHERE message_id=? AND role!='output' AND lower(filename) LIKE '%.pdf' LIMIT 1", (message_id,)).fetchone()
            if status in ("pending", "running"):
                headline, kind = "This scan is being inspected", "wait"
                text = (f"Checking its pages for labels and bubble sheets: {done} of {total} done." if status == "running" and total
                        else f"Checking its pages for labels and bubble sheets: {plural(done, 'page')} done so far." if status == "running" and done
                        else "Its pages are being separated and checked for labels." if status == "running"
                        else f"Waiting for the worker to continue checking its pages ({done} of {total} done)." if done and total
                        else "Waiting for the worker to start checking its pages.")
            elif not has_pdf:
                headline, kind, text = "No scanned pages in this arrival", "done", "Nothing to inspect: this arrival holds no PDF."
            elif status == "complete":
                headline, kind, text = "No pages were found in this scan", "check", "Its PDF held no pages. Look at the original under Files."
            elif done:
                headline, kind = "This scan's inspection stopped", "todo"
                text = f"Checking its pages stopped after {done} of {total}. Open it and retry the inspection." if total else \
                    f"Checking its pages stopped after {plural(done, 'page')}. Open it and retry the inspection."
            elif status == "failed" and (decode["error"] or "").strip():
                # Why it failed, in the failure's own words, and what to do when a retry cannot help (inbox-7).
                reason = decode["error"].strip().split(". ")[0].rstrip(".")
                headline, kind = "This scan could not be inspected", "todo"
                text = f"Its pages could not be checked: {reason}. Retry the inspection; if it fails again, upload a readable copy of the PDF."
            else:
                headline, kind, text = "This scan could not be inspected" if status == "failed" else "This scan has not been inspected", "todo", "Open it and start the inspection again."
            facts = [plural(total, "page")] if total else [plural(done, "page")] if done else []
            step = {"kind": kind, "text": text, "tool": None, "action": None, **({"holds": "scan"} if kind != "done" else {})}
            # An arrival without a PDF has nothing to file, review or grade: its graph is its Scan stage alone (a view; nothing stored).
            later = bool(self.grading and has_pdf)
            stages = self._stages([step], {}, {"scan": True, "assignment": later, "review": later, "grading": later}, {},
                                  {"scan": {"completed": done, "total": total, "unit": "pages"}} if total else {})
            if kind == "todo" and done:
                stages[0].update(state="failed", status="Failed", progress=None)
            return {"verdict": "pending", "headline": headline, "facts": facts, "state": kind, "steps": [step], "inspection": status, "stages": stages}
        sheets, steps = r["bubble_sheets"], []
        current = r.get("current_papers", r["papers"])

        # `holds="scan"` marks the few open steps that concern the WHOLE scan (its assignment, labels naming two assignments): only
        # they, and the worker's own `wait` steps, hold back its review. Every other open step is about some of its papers, sheets
        # or pages, and the rest of the scan goes on to grading without them (requirement, 2026-09-28). `review_blockers` reads this.
        def add(kind, text, tool=None, action=None, holds=None):
            steps.append({"kind": kind, "text": text, "tool": tool, "action": action, **({"holds": holds} if holds else {})})

        grading_step, grading, placed, separating = None, None, {}, False
        unlabeled = self.unlabeled.status(db, message_id)
        # First pages read for an assignment the scan is not filed under yet (inbox-5): named, never associated automatically.
        read_for = None
        if self.grading:
            grading = self.grading.scan_status(db, message_id, fingerprint=False)
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
            found = self.assignment_finder.latest(db, job[0]) if job and not r["papers"] else None
            if not grading["plan"] and unlabeled is not None:
                row = db.execute("SELECT title FROM assignments WHERE id=?", (unlabeled["assignment_id"],)).fetchone()
                read_for = row["title"] if row else None
            # INBOX "SEPARATING A SCAN": a scan without labels whose first pages name more than one assignment is separated by the titles
            # printed on its pages. The worker's from its first title read on; a scan it leaves alone says why beside the choice.
            by_title = self.separation.plan(db, message_id) if found and not found.get("chosen") and not grading["plan"] else None
            separating = bool(by_title and not by_title["reason"] and (not by_title["reading"] or by_title["reading"]["done"]))
            if separating and by_title["reading"]:
                add("wait", "Its first pages name more than one assignment. Faculty is reading the title printed on every page to separate them: "
                            f"{by_title['reading']['done']} of {by_title['reading']['total']} read.")
            elif separating:
                add("wait", f"Its first pages name more than one assignment ({separation_titles(by_title['titles'])}). "
                            "Faculty is separating these pages into one scan for each.")
            elif not grading["plan"] and r["verdict"] != "mixed":
                reason = (found or {}).get("reason")
                add("todo", "Choose the assignment and term for this scan, or create an assignment."
                    + (f" It was not chosen automatically: {reason[:1].lower() + reason[1:]}" if reason and not (found or {}).get("chosen") else "")
                    + (f" Faculty did not separate these pages by itself: {by_title['reason']}." if by_title and by_title["reason"] else "")
                    + (f" First pages {'are being' if unlabeled['status'] in ('pending', 'running') else 'were'} read for {read_for}." if read_for else ""),
                    action="grading_associate", holds="scan")
            elif grading["plan"]:
                title = grading["plan"]["title"]
                chosen = (found or {}).get("chosen")
                if chosen and chosen["assignment_id"] == grading["plan"]["source_assignment_id"]:
                    why = (f"{chosen.get('agreeing', 0)} of its first pages look like that assignment's blank template and print its title"
                           if chosen.get("basis") == "template" else
                           f"Jev read {chosen.get('agreeing', 0)} of its pages as that assignment, and its {chosen.get('pages')} pages are whole papers of {chosen.get('sides')}"
                           if chosen.get("basis") == "jev" else
                           f"it is the only exam{' with a bubble-sheet answer key' if chosen.get('answer_key') else ''} on the calendar ({', '.join(chosen.get('dates') or [])}) in the two weeks before these bubble sheets arrived"
                           + (f", and the sheets answer its key's {chosen['questions']} questions" if chosen.get("questions") else ""))
                    add("info", f"{title} was chosen automatically: {why}. If that is wrong, change the assignment.", action="grading_associate")
                if any(p["assignment_id"] != grading["plan"]["source_assignment_id"] or p["term_id"] != grading["plan"]["term_id"]
                       or not set(paper_sections(p)) <= set(grading["plan"]["section_ids"]) for p in current):
                    add("check", "The scan's labels no longer match its grading assignment or sections. Confirm the association again.", action="grading_associate", holds="scan")
                elif grading["review_required"]:
                    grading_step = (title, "grading_review")
                else:
                    grading_step = (title, "grading_open")

        doubtful = ", ".join(f"{count} {state}" for state, count in r["doubtful_labels"].items())
        if r["verdict"] == "mixed" and len({(p["assignment_id"], p["term_id"]) for p in current}) > 1:
            # INBOX "SEPARATING A SCAN": the worker makes one scan for each assignment by itself; a scan it leaves alone says why.
            separation = self.separation.plan(db, message_id)
            several = f"The labels name more than one assignment ({separation_titles(separation['titles'])})" if separation else "The labels name more than one assignment"
            if separation and not separation["reason"]:
                add("wait", f"{several}. Faculty is separating these pages into one scan for each.")
            elif separation:
                add("check", f"{several}, and Faculty did not separate these pages by itself: {separation['reason']}. {separation['advice']}".strip(), "pages", holds="scan")
            else:
                add("check", f"{several}. Check that these pages belong together.", "pages", holds="scan")
        elif r["verdict"] == "mixed":
            add("check", f"No label on these pages is of current paper{f' ({doubtful})' if doubtful else ''}: they are cancelled, superseded or unread.", "pages", holds="scan")
        elif doubtful:
            # Per page (requirement, 2026-09-28): the pages behind these labels are held by the grouping and the assembly rules.
            add("check", f"{plural(sum(r['doubtful_labels'].values()), 'label')} {'needs' if sum(r['doubtful_labels'].values()) == 1 else 'need'} a look ({doubtful}). "
                "Only the pages behind them wait; the rest of the scan goes on.", "pages")
        # Once first pages are being looked for (a new assignment made from this scan starts that itself), that tool says what is left.
        unrecognized = r["verdict"] == "unrecognized" and not (sheets and sheets["pending"]) and unlabeled is None and not separating
        offers = [job for job in (self.sequence.label_modes(db, message_id) if label_modes is None else label_modes) if job["offer"]]
        if offers:
            add("todo", "This scan has one label per paper, but its print job expects a label on every page.", None, "label_mode")
        copies = db.execute(f"""SELECT v.page_count,b.label_mode,EXISTS(SELECT 1 FROM assembly_revisions ar WHERE ar.printed_copy_id=c.id) reviewed,
                (SELECT ar.partial FROM assembly_revisions ar WHERE ar.printed_copy_id=c.id ORDER BY ar.revision DESC LIMIT 1) latest_partial,
                (SELECT json_array_length(ar.selection_json) FROM assembly_revisions ar WHERE ar.printed_copy_id=c.id ORDER BY ar.revision DESC LIMIT 1) latest_pages,
                json_extract(v.compiler_json,'$.generator')='faculty-stamps-only' stamps_only,
                (SELECT COUNT(DISTINCT p2.page_number) FROM page_stamps p2 JOIN scan_pages s2 ON s2.page_stamp_id=p2.id WHERE p2.printed_copy_id=c.id
                   AND s2.status IN ('resolved','manual') AND s2.review_state!='rejected' AND (b.label_mode='every_page' OR p2.page_number=1)
                   AND {PRESENT.format('s2.message_id')}) seen,
                -- Some of its pages are in ANOTHER scan still waiting for its Inbox review (a batch scanned in stacks, one paper across
                -- two files, requirement, 2026-10-24): the copy is assembled when that scan passes, so it is no question for this one.
                EXISTS(SELECT 1 FROM page_stamps p3 JOIN scan_pages s3 ON s3.page_stamp_id=p3.id JOIN inbox_messages m3 ON m3.id=s3.message_id
                   WHERE p3.printed_copy_id=c.id AND s3.message_id!=? AND m3.instructor_review_required AND s3.status IN ('resolved','manual')
                   AND s3.review_state!='rejected'
                   AND {PRESENT.format('s3.message_id')}) elsewhere
            FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
            WHERE c.id IN (SELECT p.printed_copy_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE sp.message_id=?)""", (message_id, message_id)).fetchall()
        expected = lambda copy: copy["page_count"] if copy["label_mode"] == "every_page" else 1
        runs = db.execute("""SELECT r.sides,r.actor,(SELECT COUNT(*) FROM scan_packets k WHERE k.run_id=r.id) packets,
                                    COALESCE(json_extract(r.summary_json,'$.origin'),'')='held_paper' by_hand FROM scan_sequence_runs r
                             WHERE r.message_id=? AND r.undone_at IS NULL ORDER BY r.created_at""", (message_id,)).fetchall()
        packets = sum(run["packets"] for run in runs)
        by_hand = sum(run["packets"] for run in runs if run["by_hand"])  # papers the instructor grouped by hand (2026-10-02)
        if packets and not by_hand:
            add("done", f"{plural(packets, 'paper')} grouped{' automatically' if any(run['actor'] == 'automatic' for run in runs) else ''}, {runs[0]['sides']} sides each.", "exams")
        elif packets:
            counted = [run for run in runs if not run["by_hand"]]
            add("done", "; ".join(([f"{plural(packets - by_hand, 'paper')} grouped{' automatically' if any(run['actor'] == 'automatic' for run in counted) else ''}, "
                                    f"{counted[0]['sides']} sides each"] if packets > by_hand else []) + [f"{plural(by_hand, 'paper')} grouped by hand"]) + ".", "exams")
        stretches = self.ungrouped(db, message_id, papers=bool(packets and r["papers"]))
        loose = sum(stretch["unidentified"] for stretch in stretches)
        no_paper = pending = False
        # A PAGE BETWEEN ITS OWN LABELS (DOCUMENTS, requirement, 2026-11-14): on paper labeled on every page, a page whose codes did not read
        # between its own copy's labels is named for what it is, never offered as a grouping ("the grouping button misleads"). Faculty
        # gives it its page by itself where the place decides it (`PlaceService`); a place it cannot decide asks the instructor.
        places = self.places.unread(db, message_id) if r["papers"] and loose else []
        for place in places:
            add("info" if place["pending"] else "todo", place["text"], "pages" if place.get("shuffled") else "exams", None if place["pending"] else "set_aside")
        no_paper = bool(places)  # no grouping makes these papers, so the stage counts none grouped (as for a stray sheet)
        given, backs = self.places.done(db, message_id) if r["papers"] else ([], 0)
        if given or backs:
            named = "; ".join(f"page {item['page']} is copy {item['copy_number']}'s page {item['page_number']}, {item['between']}" for item in given[:3])
            words = [f"By {'its' if len(given) == 1 else 'their'} place, {named}{f', and {len(given) - 3} more' if len(given) > 3 else ''} "
                     f"({'its codes' if len(given) == 1 else 'their codes'} did not read)."] if given else []
            if backs:
                words.append(f"{plural(backs, 'empty back')} inside {'a paper' if backs == 1 else 'papers'} {'was' if backs == 1 else 'were'} set aside.")
            add("done", " ".join(words), "exams")
        if places:
            inside = lambda stretch: any(place["file"] == stretch["file"] and place["first_side"] <= stretch["first_page"]
                                         and stretch["last_page"] <= place["last_side"] for place in places)
            stretches = [stretch for stretch in stretches if not inside(stretch)]
        rest = sum(stretch["unidentified"] for stretch in stretches)
        if r["papers"] and rest:
            # Grouping is still to come while no grouping of the scan ran (a paper grouped by hand is not one) and pages wait behind a
            # first page nothing grouped yet.
            pending = packets == by_hand and (not packets or any(stretch["paper"] for stretch in stretches))
            # After grouping, say which papers were left alone and why, where the pages show it (requirement, 2026-09-24).
            if not pending:
                add("check", self._ungrouped_text(stretches, runs[-1]["sides"]), "exams", "papers")
            elif self._in_no_paper(db, message_id, stretches, copies):
                # A stray sheet (a TA's section header on top of the stack, a scratch page among papers labeled on every page): no
                # grouping can place it, so its way out is its label, or setting it aside (requirement, 2026-10-02).
                no_paper = True
                add("todo", f"{plural(rest, 'written page')} {'carries' if rest == 1 else 'carry'} no label and {'belongs' if rest == 1 else 'belong'} to no paper "
                    f"({self._where_list(stretches)}). If one is a student's page, recover its label from its printed alias; otherwise set it aside.", "pages", "set_aside")
            else:
                add("todo", f"{plural(rest, 'page')} without a label {'follows' if rest == 1 else 'follow'} the labeled first pages: check the sides per paper and preview the grouping.", "exams")
        # No label on any page and more than one page left: papers printed without labels are the likely answer, so finding whose paper
        # each is becomes THE next step and sorting or alias recovery the alternatives (requirement, 2026-09-26). One stray page (a cover sheet,
        # say) is no paper: there the tools stay as they were.
        without_labels = unlabeled is None and loose > 1 and not r["papers"]
        if unrecognized:
            if without_labels:
                add("info", "If these pages do carry labels that could not be read, recover a page from its printed alias; to split them into "
                    "one PDF per printed version instead, sort them by printed version.", "sort")
            else:
                add("todo", "Nothing printed on these pages says what they are. Sort papers printed without labels by their printed version, or recover a labeled page from its printed alias.", "sort")
        # Exams printed without labels (2026-09-24): found, read and registered on request; counted from the tables alone.
        linked = grading["plan"] if grading else None
        if unlabeled is None:
            if without_labels and not separating:
                # How many papers that makes, from one blank template's length (a suggestion, like the sides per paper it fills in).
                sides = self.unlabeled.paper_sides(db, linked["source_assignment_id"]) if linked else None
                size = (f" ({'' if loose % sides == 0 else 'about '}{plural(max(1, round(loose / sides)), 'paper')} of {sides} sides, "
                        f"the length of {linked['title']}'s blank template)") if sides else ""
                add("todo", f"{plural(loose, 'page')} carry no label and are not bubble sheets: they look like papers printed without labels{size}. "
                    "Find whose paper each is from the name and UIN on its first page.", "unlabeled")
        elif unlabeled["status"] in ("pending", "running"):
            add("wait", f"Looking for first pages: {plural(unlabeled['pages_read'], 'page')} read, {plural(unlabeled['covers'], 'first page')} found.", "unlabeled")
        elif unlabeled["status"] in ("failed", "cancelled"):
            reason = (unlabeled["error"] or "").split(". ")[0].rstrip(".")
            add("todo", f"Looking for first pages stopped after {plural(unlabeled['pages_read'], 'page')} read and {plural(unlabeled['covers'], 'first page')} found"
                f"{f' ({reason[:1].lower() + reason[1:]})' if reason else ''}. Continue from where it stopped.", "unlabeled", "unlabeled_continue")
        else:
            waiting = unlabeled["covers"] - unlabeled["not_cover"] - unlabeled["planned"]
            if not unlabeled["covers"]:
                add("check", "No first page with the printed Name and UIN fields was found among the pages without a label.", "unlabeled")
            if waiting > 0:
                add("todo", f"Check who wrote {plural(waiting, 'paper')} found without a label, then register {'it' if waiting == 1 else 'them'}.", "unlabeled")
            if unlabeled["planned"] > unlabeled["registered"]:
                add("wait", f"Registering {plural(unlabeled['planned'] - unlabeled['registered'], 'paper')} found without a label: waiting for {'its label' if unlabeled['planned'] - unlabeled['registered'] == 1 else 'their labels'} to be issued.", "unlabeled")
            elif unlabeled["registered"] and waiting <= 0:
                add("done", f"{plural(unlabeled['registered'], 'paper')} found without a label {'is' if unlabeled['registered'] == 1 else 'are'} registered to {'its student' if unlabeled['registered'] == 1 else 'their students'}.", "unlabeled")
        # Stamped papers printed for no student (a drafted quiz, 2026-09-28): the worker reads each one's name, and names those a confident
        # match settles; the rest wait for the instructor. A paper without its student holds back only itself.
        named = self.copy_names.status(db, message_id)
        if named:
            if named["unread"]:
                steps.append({"kind": "info", "progress": True, "tool": "copy_names", "action": None, "text":
                              f"Reading the names written on {plural(named['total'], 'stamped paper')} printed without a student: "
                              f"{named['total'] - named['unread']} of {named['total']} done."})
            if named["waiting"]:
                add("todo", f"Say whose {plural(named['waiting'], 'stamped paper')} {'is' if named['waiting'] == 1 else 'are'}: "
                    f"the name written on {'it' if named['waiting'] == 1 else 'them'} could not be matched with confidence.", "copy_names")
            if named["named"]:
                add("done", f"{plural(named['named'], 'stamped paper')} named from the name and UIN written on {'it' if named['named'] == 1 else 'them'}.", "copy_names")
        # A paper the instructor already saved (a partial one too) is settled: its missing pages are no longer a question (bugs-backend-1).
        missing = sum(1 for copy in copies if not copy["reviewed"] and copy["seen"] < expected(copy))
        # Pages not grouped yet are the Papers step's (DOCUMENTS); the copies they would complete are flagged once a grouping ran, or at
        # once when no grouping can place those pages (`no_paper`).
        if missing and not offers and not (pending and not no_paper):
            add("check", f"{plural(missing, 'copy', 'copies')} {'has' if missing == 1 else 'have'} pages that no scan has shown with a label.", "assemblies")
        # A page that arrived after the instructor accepted its paper as partial is not silently left out: that paper is flagged on
        # its own (PIPELINE) until its reviewed paper is saved again. With first-page labels `seen` is at most 1, so never for them.
        grown = sum(1 for copy in copies if copy["reviewed"] and copy["latest_partial"] and copy["seen"] > (copy["latest_pages"] or 0))
        if grown and not offers:
            add("check", f"{plural(grown, 'paper')} accepted with missing pages {'has' if grown == 1 else 'have'} more pages now: save "
                f"{'its' if grown == 1 else 'each one’s'} reviewed paper again to include them.", "assemblies")
        # Paper labeled on every page with every page here becomes its student's reviewed paper when the scan passes review
        # (`SequenceService.assemble_reviewed`, 2026-09-28); what is left after that needs the per-copy assembly review.
        passed = bool(grading and grading["plan"] and not grading["review_required"])
        complete = [copy for copy in copies if copy["label_mode"] == "every_page" and not copy["stamps_only"] and copy["seen"] >= expected(copy) and not copy["reviewed"]]
        later = sum(1 for copy in complete if copy["elsewhere"]) if passed else 0
        ready = len(complete) - later
        if later:
            add("info", f"{plural(later, 'copy', 'copies')} {'continues' if later == 1 else 'continue'} in another scan that has not passed its review yet, and "
                f"{'becomes its student’s reviewed paper' if later == 1 else 'become their students’ reviewed papers'} once it does.", "assemblies")
        if ready and passed:
            add("check", f"{plural(ready, 'copy', 'copies')} {'has' if ready == 1 else 'have'} every page but {'was' if ready == 1 else 'were'} not assembled automatically "
                "(another copy of the same student, or a page that arrived twice with different content): save each one's reviewed paper.", "assemblies")
        elif ready:
            add("info", f"{plural(ready, 'copy', 'copies')} {'has' if ready == 1 else 'have'} every page and "
                f"{'becomes its student’s reviewed paper' if ready == 1 else 'become their students’ reviewed papers'} when the scan passes review.", "assemblies")
        changed = db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=? AND review_state='conflict'", (message_id,)).fetchone()[0]
        if changed:
            add("check", f"{plural(changed, 'labeled page')} also arrived in another scan with different content, usually a re-scan.", "pages")
        if sheets and sheets["sheets"]:
            count, read = plural(sheets["sheets"], "bubble sheet"), sheets["read"]
            whose = f"{read['title']} · {sections(read['sections'])}" if read else ""
            if not read:
                add("todo", f"Say whose {count} these are: choose the assignment and sections, then read them.", "bubble", "associate")
            elif read["status"] != "complete":
                add("wait", f"Reading {count} for {whose}: {min(read.get('done', 0), sheets['sheets'])} of {sheets['sheets']} done." if read["status"] == "running"
                    else f"Waiting for the worker to read {count} for {whose}.", "bubble")
            elif read["counts"]["needs_review"]:
                add("todo", f"Review {plural(read['counts']['needs_review'], 'bubble sheet')} of {sheets['sheets']} that could not be read or matched with confidence (read for {whose}).", "bubble")
            else:
                add("done", f"All {count} read for {whose}.", "bubble")
        elif sheets and sheets["pending"]:
            add("wait", f"Checking the pages without a label for bubble sheets: {sheets['checked']} of {sheets['checked'] + sheets['pending']} done.")
        # Background reading the worker is doing on this scan, with how far it has got. Shown only (`progress`): neither ever held a
        # scan back from review or grading, and showing it must not start to (a `wait` step would).
        labels = {row[0]: row[1] for row in db.execute("SELECT status,COUNT(*) FROM scan_alias_ocr WHERE message_id=? GROUP BY status", (message_id,))}
        if labels.get("pending", 0) + labels.get("running", 0):
            steps.append({"kind": "info", "progress": True, "tool": None, "action": None, "text":
                          f"Reading the printed labels on pages whose mark could not be read: {sum(labels.values()) - labels.get('pending', 0) - labels.get('running', 0)} of {sum(labels.values())} done."})
        sorting = db.execute("SELECT attempt,status,pages_total FROM scan_version_sorts WHERE message_id=? ORDER BY attempt DESC LIMIT 1", (message_id,)).fetchone()
        if sorting and sorting["status"] in ("pending", "running"):
            read_pages = db.execute("SELECT COUNT(*) FROM scan_version_reads WHERE message_id=? AND attempt=? AND status IN ('complete','failed')",
                                    (message_id, sorting["attempt"])).fetchone()[0]
            steps.append({"kind": "info", "progress": True, "tool": "sort", "action": None, "text":
                          f"Sorting by printed version: {read_pages} of {sorting['pages_total']} pages read." if sorting["status"] == "running" and sorting["pages_total"]
                          else "Waiting for the worker to sort by printed version."})
        unseen = sum(len(paper["unseen"]) for paper in current)
        if unseen:
            add("info", f"{plural(unseen, 'issued paper')} {'has' if unseen == 1 else 'have'} not appeared in any scan yet.")
            placed[id(steps[-1])] = "papers"
        if grading_step:
            # Only the scan-wide steps and the worker's own work hold the review back; flagged papers stay behind on their own.
            holding = any(s["kind"] == "wait" or s["kind"] in ("todo", "check") and s.get("holds") == "scan" for s in steps)
            flagged = any(s["kind"] in ("todo", "check") for s in steps)
            title = grading_step[0]
            if grading_step[1] == "grading_open":
                add("done", f"Assignment: {title}. Its papers are in grading{', and those flagged here join them once you settle them' if flagged else ''}.",
                    action="grading_open")
            elif holding:
                add("info", f"Assignment: {title}. The review passes by itself once the steps for the whole scan are done"
                    f"{'; flagged papers stay here for you' if flagged else ''}.", action="grading_review")
            else:
                add("todo", f"Assignment: {title}. The review passes by itself shortly, or finish it now"
                    f"{': flagged papers stay here for you, and the rest go on to grading' if flagged else ' to continue to grading'}.", action="grading_review")
        order = ("todo", "check", "wait", "done", "info")
        steps.sort(key=lambda step: order.index(step["kind"]))
        state = next((kind for kind in ("todo", "check", "wait") if any(step["kind"] == kind for step in steps)), "done")
        plan = grading["plan"] if grading else None
        identified = sum(paper["copies"] for paper in r["papers"])
        source = ("their labels and the names on their first pages" if r["labels_read"] and r["first_pages_read"]
                  else "the names and UINs on their first pages" if r["first_pages_read"] else "their labels")
        firsts = db.execute("""SELECT COUNT(*) FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id
                               WHERE sp.message_id=? AND p.page_number=1 AND sp.status IN ('resolved','manual')""", (message_id,)).fetchone()[0]
        progress = {}
        if unlabeled and unlabeled["covers"] - unlabeled["not_cover"] > 0:
            progress["students"] = {"completed": unlabeled["registered"], "total": unlabeled["covers"] - unlabeled["not_cover"], "unit": "papers"}
        # Papers grouped of first pages found. Not where the pages left belong to no paper a grouping could make (`no_paper`): papers
        # labeled on every page are never grouped, so a stray page there read "0/90" beside 90 papers put together (2026-11-11).
        if firsts and not no_paper:
            progress["papers"] = {"completed": min(packets, firsts), "total": firsts, "unit": "papers"}
        reviewed = plan and not grading["review_required"]
        automatic = reviewed and self._reviewed_automatically(db, message_id)
        stages = self._stages(steps, placed, {
            "scan": True, "assignment": bool(self.grading), "students": bool(r["papers"] or unlabeled is not None or loose > 1),
            "sheets": bool(sheets and sheets["sheets"]), "papers": bool(r["papers"] or unlabeled is not None),
            "review": bool(self.grading), "grading": bool(self.grading)}, {
            "scan": ("done", f"{plural(r['pages'], 'page')} checked; none carries a label." if without_labels else f"{plural(r['pages'], 'page')} checked."),
            "assignment": ("done", f"Assignment: {plan['title']}.") if plan else ("idle", "Choose the assignment once the labels are checked."),
            "students": ("done", f"{plural(identified, 'paper')} identified from {source}.") if identified else ("idle", "Whose papers these are comes next."),
            "papers": ("done", f"{plural(packets, 'paper')} grouped.") if packets else ("done", "Each page carries its own label.") if r["papers"] and (not loose or no_paper)
                      else ("idle", "The pages are grouped into papers once their students are found."),
            "review": ("done", ("Passed automatically" if automatic else "Reviewed")
                               + (": flagged papers wait in their own stages." if state != "done" else ": nothing needed checking." if automatic else "."))
                      if reviewed else ("idle", "It passes by itself once the steps for the whole scan are done."),
            "grading": ("idle", "Grading starts once the scan is reviewed.")}, progress, grading.get("href") if grading else None)
        if state == "done":
            steps.insert(0, {"kind": "done", "text": "Nothing here needs you.", "tool": None, "action": None})
        titles = sorted({paper["title"] for paper in r["papers"]})
        if r["verdict"] == "bubble_sheets":
            headline = plural(sheets["sheets"], "bubble sheet") + (f" · {sheets['read']['title']} · {sections(sheets['read']['sections'])}" if sheets["read"] else "")
        elif r["papers"]:
            headline = f"{titles[0] if len(titles) == 1 else 'Several assignments'} · {r['papers'][0]['code']} · {sections(r['sections'])}"
        elif linked and (without_labels or unlabeled is not None):
            headline = f"{linked['title']} · papers without labels"
        elif read_for:
            headline = f"{read_for} · papers without labels"
        else:
            headline = "Unrecognized scan"
        return {"verdict": r["verdict"], "headline": headline, "facts": [plural(r["pages"], "page")] + ([plural(packets, "paper")] if packets else []),
                "state": state, "steps": steps, "inspection": status, "stages": stages, **({"copy_names": named} if named else {})}

    @staticmethod
    def _reviewed_automatically(db, message_id):
        row = db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id WHERE j.message_id=? AND e.kind='reviewed'
                            ORDER BY e.rowid DESC LIMIT 1""", (message_id,)).fetchone()
        try:
            return bool(row) and json.loads(row[0]).get("by") == "automatic"
        except (TypeError, ValueError, AttributeError):
            return False

    @staticmethod
    def _identity_state(db, scan_id, stamp_id, content_sha256, when):
        """Barcode intake's duplicate/changed-content rule for a page given an identity after intake (by hand, or by its position
        after a first-page label): among the observations of that label in scans still in the Inbox (PRESENT), the first of each
        content is a candidate, a later one repeats it, and different content conflicts, marking the other candidates too.
        Returns (review_state, duplicate_of_scan_page_id)."""
        present = PRESENT.format("scan_pages.message_id")
        duplicate = db.execute(f"""SELECT id FROM scan_pages WHERE page_stamp_id=? AND content_sha256=? AND id!=? AND {present}
                                    ORDER BY created_at,id LIMIT 1""", (stamp_id, content_sha256, scan_id)).fetchone()
        changed = db.execute(f"SELECT 1 FROM scan_pages WHERE page_stamp_id=? AND content_sha256!=? AND id!=? AND {present} LIMIT 1",
                             (stamp_id, content_sha256, scan_id)).fetchone()
        if changed and not duplicate:
            db.execute("UPDATE scan_pages SET review_state='conflict',updated_at=? WHERE page_stamp_id=? AND review_state='candidate' AND id!=?",
                       (when, stamp_id, scan_id))
        return ("duplicate" if duplicate else "conflict" if changed else "candidate"), (duplicate[0] if duplicate else None)

    def recover(self, scan_id, alias, reason, actor="workspace"):
        reason = text_field(reason, "Correction reason", 1000)
        with self.rosters.lock(), ExitStack() as locks:
            provenance = self.documents.resolve_token(alias, authenticated=True, alias=True)
            if not provenance:
                raise ValueError("The exact recovery alias did not resolve.")
            with connect(self.path) as db:
                scan = db.execute("SELECT sp.*,m.authenticated FROM scan_pages sp JOIN inbox_messages m ON m.id=sp.message_id WHERE sp.id=?", (scan_id,)).fetchone()
                if not scan:
                    raise NotFound("Scan page not found.")
                if not scan["authenticated"]:
                    raise Forbidden("Manual recovery cannot override sender authentication failure.")
                locks.enter_context(self.documents.files.owner_lock(scan["message_id"]))
                if db.execute("SELECT 1 FROM assembly_pages WHERE scan_page_id=?", (scan_id,)).fetchone():
                    raise Conflict("This observation is retained in a reviewed assembly and cannot be reassigned.")
                state, when = self.resolution(provenance), timestamp()
                # The intake rule, like every other way a page gets its identity (bugs-backend-7): scans deleted from the Inbox never
                # count, a repeat of the same content is a duplicate, and different content conflicts.
                review_state, duplicate = self._identity_state(db, scan_id, provenance["stamp_id"], scan["content_sha256"], when)
                db.execute("UPDATE scan_pages SET page_stamp_id=?,status=?,review_state=?,duplicate_of_scan_page_id=?,updated_at=? WHERE id=?",
                           (provenance["stamp_id"], "manual" if state == "resolved" else state, review_state, duplicate, when, scan_id))
                job_id = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (scan["message_id"],)).fetchone()[0]
                self.inbox.event(db, job_id, "manual_page_recovery", encoded({"actor": actor, "scan_id": scan_id, "previous_stamp_id": scan["page_stamp_id"], "stamp_id": provenance["stamp_id"], "reason": reason}))
        return self.review_item(scan["message_id"])

    def read_faint_marks(self, message_id, write=False, actor="workspace"):
        """FAINT MARKS (DOCUMENTS) for a scan inspected before them: its pages that showed no code, have no identity, are grouped
        into no paper, set aside by nobody and read by no first-page search are read again from the retained attachment, and those
        whose code now reads are listed: {checked, read: [{scan_page_id, page, token, state}]}. With `write` each takes that code's
        identity as a recovery gives it (status `manual`, the intake rule's review state, `ScanService._identity_state`; the original
        observation stays as read), and the scan gets one Inbox event `faint_marks_read` naming every page with what was read now;
        nothing else changes, and the worker carries on from there (PHOTOCOPIED
        STAMPS, names, grading). Run the dry run first: the write reads the pages again and touches only those still as they were."""
        job = self.inbox.get_by_message(message_id)
        if job["action"] != "scan.receive" or not job["authenticated"]:
            raise Forbidden("Only authenticated scanner intake can be read again.")
        unread = f"""sp.message_id=? AND sp.status='unreadable' AND sp.page_stamp_id IS NULL AND sp.review_state='candidate' AND NOT {self.GROUPED}
                     AND NOT EXISTS(SELECT 1 FROM scan_unlabeled_reads r WHERE r.scan_page_id=sp.id)"""
        with connect(self.path) as db:
            if db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()[0] != "complete":
                raise Conflict("The Inbox worker is still inspecting this scan. Wait for it to finish.")
            rows = db.execute(f"SELECT sp.id,sp.source_artifact_id,sp.source_page_index FROM scan_pages sp WHERE {unread} ORDER BY 2,3", (message_id,)).fetchall()
        found = []
        for source in sorted({row["source_artifact_id"] for row in rows}):
            wanted = {row["source_page_index"]: row["id"] for row in rows if row["source_artifact_id"] == source}
            content = self.inbox.read_artifact(source)[1]
            indexes, spans = sorted(wanted), []
            for index in indexes:
                if spans and index == spans[-1][1] + 1 and index - spans[-1][0] < DECODE_CHUNK:
                    spans[-1][1] = index
                else:
                    spans.append([index, index])
            for first, last in spans:
                for page in process.run("extract", content, {"first": first, "last": last}):
                    if page["index"] not in wanted or not page["decoder"].get("threshold"):
                        continue
                    provenance, state = self.classify(page)
                    if provenance:
                        found.append({"scan_page_id": wanted[page["index"]], "page": page["index"], "state": state,
                                      "token": next(c["payload"] for c in page["candidates"] if c["payload"].startswith(("T", "t1-"))),
                                      "stamp_id": provenance["stamp_id"], "candidates": page["candidates"], "decoder": page["decoder"]})
        if write and found:
            with self.rosters.lock(), ExitStack() as locks:
                with connect(self.path) as db:
                    stamps = sorted({item["stamp_id"] for item in found})
                    owners = {row[0] for row in db.execute(f"SELECT DISTINCT message_id FROM scan_pages WHERE page_stamp_id IN ({','.join('?' * len(stamps))})", stamps)}
                for owner in sorted(owners | {message_id}):
                    locks.enter_context(self.documents.files.owner_lock(owner))
                with connect(self.path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    still = {row[0] for row in db.execute(f"SELECT sp.id FROM scan_pages sp WHERE {unread}", (message_id,))}
                    found = [item for item in found if item["scan_page_id"] in still]
                    when = timestamp()
                    for item in found:
                        content = db.execute("SELECT content_sha256 FROM scan_pages WHERE id=?", (item["scan_page_id"],)).fetchone()[0]
                        review_state, duplicate = self._identity_state(db, item["scan_page_id"], item["stamp_id"], content, when)
                        # The original observation (its candidates and decoder) stays as intake read it; the reading is the event's.
                        db.execute("UPDATE scan_pages SET page_stamp_id=?,status=?,review_state=?,duplicate_of_scan_page_id=?,updated_at=? WHERE id=?",
                                   (item["stamp_id"], "manual" if item["state"] == "resolved" else item["state"], review_state, duplicate,
                                    when, item["scan_page_id"]))
                    if found:
                        self.inbox.event(db, job["id"], "faint_marks_read", encoded({"actor": actor, "pages": [
                            {"scan_page_id": item["scan_page_id"], "page": item["page"], "stamp_id": item["stamp_id"],
                             "candidates": item["candidates"], "decoder": item["decoder"]} for item in found]}))
            if found and self.grading:
                try:
                    self.grading.sync_scan(message_id)
                except Exception:  # noqa: BLE001 - the grading catch-up tries again
                    LOG.exception("Linking scan %s after its faint marks were read failed", message_id)
        return {"checked": len(rows), "read": [{key: item[key] for key in ("scan_page_id", "page", "token", "state")} for item in found]}

    def read_blank_sides(self, message_id, write=False, actor="workspace"):
        """GREY PAPER (DOCUMENTS) for a scan inspected before it: its sides that showed no code, have no identity, are grouped into
        no paper, part of no reviewed paper, set aside by nobody and read by no first-page search are measured again from their
        retained pictures (`media.blank_side`), and those that are empty are listed: {checked, blank: [{scan_page_id, page}]}. With
        `write` each becomes a blank side as intake makes one (status `blank`, review state `ignored`; its picture and what was read
        on it stay), and the scan gets one Inbox event `blank_sides_read` naming them; nothing else changes. Run the dry run first:
        the write measures again and touches only the sides still as they were."""
        from PIL import Image
        from .media import blank_side
        job = self.inbox.get_by_message(message_id)
        if job["action"] != "scan.receive" or not job["authenticated"]:
            raise Forbidden("Only authenticated scanner intake can be read again.")
        unread = f"""sp.message_id=? AND sp.status='unreadable' AND sp.page_stamp_id IS NULL AND sp.review_state='candidate'
                     AND sp.candidates_json IN ('','[]') AND NOT {self.GROUPED}
                     AND NOT EXISTS(SELECT 1 FROM assembly_pages a WHERE a.scan_page_id=sp.id)
                     AND NOT EXISTS(SELECT 1 FROM scan_unlabeled_reads r WHERE r.scan_page_id=sp.id)"""
        with connect(self.path) as db:
            if db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()[0] != "complete":
                raise Conflict("The Inbox worker is still inspecting this scan. Wait for it to finish.")
            rows = db.execute(f"SELECT sp.id,sp.source_page_index,sp.raster_artifact_id FROM scan_pages sp WHERE {unread} ORDER BY sp.source_artifact_id,2",
                              (message_id,)).fetchall()
        found = [{"scan_page_id": row["id"], "page": row["source_page_index"]} for row in rows
                 if blank_side(Image.open(io.BytesIO(self.inbox.read_artifact(row["raster_artifact_id"])[1])))]
        if write and found:
            with self.rosters.lock(), self.documents.files.owner_lock(message_id), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                still = {row[0] for row in db.execute(f"SELECT sp.id FROM scan_pages sp WHERE {unread}", (message_id,))}
                found = [item for item in found if item["scan_page_id"] in still]
                when = timestamp()
                db.executemany("UPDATE scan_pages SET status='blank',review_state='ignored',updated_at=? WHERE id=?",
                               [(when, item["scan_page_id"]) for item in found])
                if found:
                    self.inbox.event(db, job["id"], "blank_sides_read", encoded({"actor": actor, "pages": found}))
        return {"checked": len(rows), "blank": found}

    # ----- a page set aside as part of no paper (requirement, 2026-10-02) ----------------------------------------------------

    def set_aside_pages(self, db, message_id):
        """The pages of this scan the instructor set aside as part of no paper, each with its reason (the latest `page_set_aside`
        Inbox event): kept and listed, never counted as a page left to group. Empty for nearly every scan, at the cost of one query."""
        rows = db.execute("""SELECT sp.id,sp.source_artifact_id,sp.source_page_index FROM scan_pages sp JOIN inbox_artifacts f ON f.id=sp.source_artifact_id
                             WHERE sp.message_id=? AND sp.status='unreadable' AND sp.review_state='ignored'
                             ORDER BY f.filename,sp.source_artifact_id,sp.source_page_index""", (message_id,)).fetchall()
        if not rows:
            return []
        files = self._file_numbers(db, message_id)
        reasons = {}
        for event in db.execute("""SELECT e.detail,e.at FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                                   WHERE j.message_id=? AND e.kind='page_set_aside' ORDER BY e.rowid""", (message_id,)):
            detail = json.loads(event["detail"])
            reasons[detail["scan_page_id"]] = {"reason": detail.get("reason", ""), "actor": detail.get("actor", ""), "at": event["at"]}
        return [{"scan_page_id": row["id"], "page": row["source_page_index"], "file": files[row["source_artifact_id"]], "files": len(files),
                 **reasons.get(row["id"], {"reason": "", "actor": "", "at": None})} for row in rows]

    def set_aside(self, scan_page_id, data, actor="workspace"):
        """Set a page aside as part of no paper, or (`undo: true`) take that back (requirement, 2026-10-02).

        Only a page with no label read and no identity, that is written (not blank), no grouped paper holds, no reviewed paper
        contains and no bubble sheet is: its review state becomes `ignored`, the state blank pages carry, which the grouping, the
        copy names and assembly already leave alone, so it no longer flags its scan. Nothing is deleted: the page, its images and its
        place in the scan stay. Under the lifecycle lock, then the scan's owner lock. Each change is an Inbox event naming the actor
        and the reason (`page_set_aside`, `page_set_aside_undone`); undo restores the state it had. The same `request_id` again
        changes nothing and answers with the page as it is now."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        undo, request_id = data.get("undo", False), data.get("request_id")
        if type(undo) is not bool:
            raise ValueError("Say whether to set the page aside or to undo that.")
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 200):
            raise ValueError("A request ID is 1–200 characters.")
        reason = text_field(data.get("reason"), "Reason", 1000) if not undo or data.get("reason") is not None else ""
        kind = "page_set_aside_undone" if undo else "page_set_aside"
        with self.rosters.lock(), ExitStack() as locks:
            with connect(self.path) as db:
                row = db.execute("SELECT message_id FROM scan_pages WHERE id=?", (scan_page_id,)).fetchone()
                if not row:
                    raise NotFound("Scan page not found.")
                message_id = row[0]
            locks.enter_context(self.documents.files.owner_lock(message_id))
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                page = db.execute(f"""SELECT sp.*,m.authenticated,{PRESENT.format('m.id')} present FROM scan_pages sp JOIN inbox_messages m ON m.id=sp.message_id
                                      WHERE sp.id=?""", (scan_page_id,)).fetchone()
                job = db.execute("SELECT id,action FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
                if not job or job["action"] != "scan.receive":
                    raise ValueError("Pages are set aside on a retained scan.")
                if not page["authenticated"]:
                    raise Forbidden("Sender authentication failed; its pages cannot be reviewed.")
                events = [json.loads(item[0]) for item in db.execute(
                    "SELECT detail FROM inbox_events WHERE job_id=? AND kind IN ('page_set_aside','page_set_aside_undone') ORDER BY rowid", (job["id"],))]
                mine = [event for event in events if event.get("scan_page_id") == scan_page_id]
                if request_id and any(event.get("request_id") == request_id and event.get("kind", kind) == kind for event in mine):
                    return self._set_aside_result(page, mine)  # the same request again: already done, nothing more to write
                if not page["present"]:
                    raise Conflict("This scan was deleted from the Inbox. Restore it first.")
                where = f"Attachment page {page['source_page_index']}"
                if page["status"] == "blank":
                    raise Conflict(f"{where} is blank: a blank side needs nothing, and is never counted as a page left to group.")
                if page["status"] != "unreadable" or page["page_stamp_id"]:
                    raise Conflict(f"{where} has a label or an identity, so it belongs to a paper. Only a written page with no label read can be set aside.")
                if undo:
                    if page["review_state"] != "ignored":
                        raise Conflict(f"{where} is not set aside.")
                    previous = next((event.get("previous") for event in reversed(mine) if event.get("kind", "page_set_aside") == "page_set_aside"), None)
                    state = previous if previous in ("candidate", "conflict", "duplicate") else "candidate"
                else:
                    if page["review_state"] == "ignored":
                        raise Conflict(f"{where} is already set aside.")
                    if db.execute("SELECT 1 FROM assembly_pages WHERE scan_page_id=?", (scan_page_id,)).fetchone():
                        raise Conflict(f"{where} is part of a reviewed paper.")
                    grouped = db.execute(f"SELECT 1 FROM scan_pages sp WHERE sp.id=? AND {self.GROUPED}", (scan_page_id,)).fetchone()
                    if grouped:
                        raise Conflict(f"{where} is part of a grouped paper. Undo that grouping first, or choose that paper's sides by hand.")
                    if self.bubble_detector and scan_page_id in self.bubble_detector.sheet_pages(db, message_id):
                        raise Conflict(f"{where} is a bubble sheet: it is read on the Bubble sheets page.")
                    state = "ignored"
                when = timestamp()
                changed = db.execute("UPDATE scan_pages SET review_state=?,updated_at=? WHERE id=? AND status='unreadable' AND page_stamp_id IS NULL AND review_state=?",
                                     (state, when, scan_page_id, page["review_state"])).rowcount
                if changed != 1:
                    raise Conflict(f"{where} changed meanwhile. Reload the page.")
                event = {"actor": actor, "kind": kind, "scan_page_id": scan_page_id, "page": page["source_page_index"],
                         **({"reason": reason} if reason else {}), **({"previous": page["review_state"]} if not undo else {"restored": state}),
                         **({"request_id": request_id} if request_id else {})}
                self.inbox.event(db, job["id"], kind, encoded(event))
                return self._set_aside_result(dict(page, review_state=state), [*mine, event])

    @staticmethod
    def _set_aside_result(page, events):
        latest = next((event for event in reversed(events) if event.get("kind", "page_set_aside") == "page_set_aside"), {})
        aside = page["review_state"] == "ignored"
        return {"scan_page_id": page["id"], "message_id": page["message_id"], "page": page["source_page_index"], "set_aside": aside,
                "reason": latest.get("reason", "") if aside else ""}

    def assembly(self, copy_id, db=None):
        # `db`: the caller's open catalog connection. A scan's page reads hundreds of copies, and opening a connection for each
        # (the catalog's schema is read again every time) made that most of a second.
        with nullcontext(db) if db is not None else connect(self.path) as db:
            row = db.execute("""SELECT c.*,b.section_id,b.status batch_status,b.superseded_at,b.label_mode,v.page_count,v.id version_id,v.label version_label,
                a.id assignment_id,a.title FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id JOIN assignments a ON a.id=v.assignment_id WHERE c.id=?""", (copy_id,)).fetchone()
            if not row:
                raise NotFound("Printed copy not found.")
            copy = dict(row)
            # Where each observation was scanned (its file and attachment page), so the review names pages as the instructor sees them.
            observations = [dict(r) for r in db.execute(f"""SELECT sp.id,sp.message_id,p.page_number,sp.content_sha256,sp.review_state,
                sp.duplicate_of_scan_page_id,sp.status,sp.page_artifact_id,sp.source_page_index,f.filename source_filename
                FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id LEFT JOIN inbox_artifacts f ON f.id=sp.source_artifact_id
                WHERE p.printed_copy_id=? AND ({PRESENT.format('sp.message_id')} OR EXISTS(SELECT 1 FROM assembly_pages kept WHERE kept.scan_page_id=sp.id))
                ORDER BY p.page_number,sp.created_at,sp.id""", (copy_id,))]
            revisions = [dict(r) for r in db.execute("SELECT * FROM assembly_revisions WHERE printed_copy_id=? ORDER BY revision", (copy_id,))]
            # The overview's "has more pages now" check for this paper (bugs-backend-1), by its rule: distinct pages read in scans still
            # in the Inbox, against the pages its latest partial review kept.
            latest = revisions[-1] if revisions else None
            seen = db.execute(f"""SELECT COUNT(DISTINCT p.page_number) FROM page_stamps p JOIN scan_pages sp ON sp.page_stamp_id=p.id
                WHERE p.printed_copy_id=? AND sp.status IN ('resolved','manual') AND sp.review_state!='rejected' AND (?='every_page' OR p.page_number=1)
                AND {PRESENT.format('sp.message_id')}""",
                (copy_id, copy["label_mode"])).fetchone()[0] if latest and latest["partial"] else 0
            # In id order, the order the id index gave before printed_copies_enrollment (document_identity 012) existed.
            other_copies = [r[0] for r in db.execute("""SELECT DISTINCT c.id FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id JOIN assembly_revisions ar ON ar.printed_copy_id=c.id
                WHERE c.enrollment_id=? AND v.assignment_id=? AND c.id!=? ORDER BY c.id""", (copy["enrollment_id"], copy["assignment_id"], copy_id))] if copy["enrollment_id"] else []
            choice = db.execute("SELECT assembly_revision_id FROM submission_choices WHERE enrollment_id=? AND assignment_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (copy["enrollment_id"], copy["assignment_id"])).fetchone()
        pages = []
        # Paper labeled on the first page only is expected to show that page; any other page actually observed is still listed.
        expected = [1] if copy["label_mode"] == "first_page" else list(range(1, copy["page_count"] + 1))
        for number in sorted(set(expected) | {obs["page_number"] for obs in observations}):
            available = [obs for obs in observations if obs["page_number"] == number]
            accepted = [obs for obs in available if obs["status"] in ("resolved", "manual") and obs["review_state"] != "rejected"]
            pages.append({"page_number": number, "observations": available, "missing": not accepted,
                          "conflict": len({obs["content_sha256"] for obs in accepted}) > 1})
        return {**copy, "pages": pages, "expected_pages": expected, "missing": [p["page_number"] for p in pages if p["missing"] and p["page_number"] in expected],
                "conflicts": [p["page_number"] for p in pages if p["conflict"]], "revisions": revisions,
                "other_reviewed_copies": other_copies, "submission_choice_required": bool(other_copies) and not choice,
                "selected_submission_revision_id": choice[0] if choice else None,
                "grown": bool(latest and latest["partial"] and seen > len(json.loads(latest["selection_json"])))}

    def select_assembly(self, copy_id, selections, partial, reason, actor="workspace"):
        reason = text_field(reason, "Review reason", 1000)
        if not isinstance(selections, list) or not selections or any(not isinstance(s, str) for s in selections) or type(partial) is not bool:
            raise ValueError("Choose scan observations and explicitly indicate whether this is a partial assembly.")
        with self.rosters.lock(), ExitStack() as locks:
            assembly = self.assembly(copy_id)
            if assembly["batch_status"] != "ready" or assembly["superseded_at"]:
                raise Conflict("Cancelled, unissued, or superseded paper requires review and cannot be accepted as a submission.")
            all_obs = {obs["id"]: obs for page in assembly["pages"] for obs in page["observations"]}
            if any(s not in all_obs or all_obs[s]["status"] not in ("resolved", "manual") for s in selections):
                raise ValueError("Every selected observation must resolve to this printed copy.")
            chosen = sorted((all_obs[s] for s in selections), key=lambda s: s["page_number"])
            if len({s["page_number"] for s in chosen}) != len(chosen):
                raise ValueError("Choose only one observation for each printed page.")
            if not set(assembly["expected_pages"]) <= {s["page_number"] for s in chosen} and not partial:
                raise Conflict("Missing pages require explicit approval as a partial assembly.")
            frozen = [{"scan_page_id": s["id"], "page_number": s["page_number"], "content_sha256": s["content_sha256"]} for s in chosen]
            digest = sha(encoded(frozen).encode())
            # Selection also updates rejected/duplicate alternatives on other
            # messages, so lock all affected observations' owners.
            with connect(self.path) as db:  # the copy's section, or its term for a job printed for the whole term (migration 013)
                paper_owner = db.execute("""SELECT COALESCE(b.section_id,b.term_id) FROM printed_copies c
                    JOIN print_batches b ON b.id=c.print_batch_id WHERE c.id=?""", (copy_id,)).fetchone()[0]
            for owner in sorted({paper_owner, *(s["message_id"] for s in all_obs.values())}):
                locks.enter_context(self.documents.files.owner_lock(owner))
            with connect(self.path) as db:
                for s in chosen:
                    if not db.execute("SELECT authenticated FROM inbox_messages WHERE id=?", (s["message_id"],)).fetchone()[0]:
                        raise Forbidden("Failed sender authentication cannot be overridden by assembly review.")
                # Exact-byte repeats reuse the prior revision, including when the
                # instructor chooses a duplicate observation from a new arrival.
                for old in assembly["revisions"]:
                    old_pages = json.loads(old["selection_json"])
                    if [(p["page_number"], p["content_sha256"]) for p in old_pages] == [(p["page_number"], p["content_sha256"]) for p in frozen]:
                        return old
                revision = {"id": identifier(), "printed_copy_id": copy_id, "revision": len(assembly["revisions"]) + 1,
                            "selection_json": encoded(frozen), "selection_sha256": digest, "partial": int(partial),
                            "actor": actor, "reason": reason, "created_at": timestamp()}
                db.execute("INSERT INTO assembly_revisions VALUES(?,?,?,?,?,?,?,?,?)", tuple(revision.values()))
                for s in chosen:
                    db.execute("INSERT INTO assembly_pages VALUES(?,?,?,?)", (revision["id"], s["id"], s["page_number"], timestamp()))
                    db.execute("""UPDATE scan_pages SET review_state=CASE WHEN content_sha256=? THEN 'duplicate' ELSE 'rejected' END,updated_at=?
                        WHERE id!=? AND page_stamp_id=(SELECT page_stamp_id FROM scan_pages WHERE id=?)""", (s["content_sha256"], timestamp(), s["id"], s["id"]))
                    db.execute("UPDATE scan_pages SET review_state='selected',updated_at=? WHERE id=?", (timestamp(), s["id"]))
                    job_id = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (s["message_id"],)).fetchone()[0]
                    self.inbox.event(db, job_id, "assembly_review", encoded({"actor": actor, "revision_id": revision["id"], "partial": partial, "reason": reason}))
                self.documents.event(db, "assembly_selected", actor, {"revision_id": revision["id"], "reason": reason}, batch_id=assembly["print_batch_id"], copy_id=copy_id)
                return revision

    def choose_submission(self, revision_id, reason, actor="workspace"):
        reason = text_field(reason, "Submission choice reason", 1000)
        with self.rosters.lock(), ExitStack() as locks, connect(self.path) as db:
            row = db.execute("""SELECT ar.id,c.enrollment_id,v.assignment_id FROM assembly_revisions ar JOIN printed_copies c ON c.id=ar.printed_copy_id
                JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                WHERE ar.id=? AND b.status='ready' AND b.superseded_at IS NULL""", (revision_id,)).fetchone()
            if not row or not row["enrollment_id"]:
                raise ValueError("A submission choice requires an administratively assigned enrollment.")
            section = db.execute("SELECT COALESCE(b.section_id,b.term_id) FROM assembly_revisions ar JOIN printed_copies c ON c.id=ar.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id WHERE ar.id=?", (revision_id,)).fetchone()[0]
            locks.enter_context(self.documents.files.owner_lock(section))
            db.execute("INSERT INTO submission_choices VALUES(?,?,?,?,?,?,?)", (identifier(), row["enrollment_id"], row["assignment_id"], revision_id, actor, reason, timestamp()))
        return {"assembly_revision_id": revision_id}

    def student_papers(self, enrollment_id, assignment_id):
        """One student's papers of one assignment (requirement, 2026-09-30): every printed
        copy of theirs with its latest reviewed paper, or the pages seen so far when none was reviewed, as page-image routes. Read
        only; `assembly` is the rule for which pages count (scans deleted from the Inbox count only inside a reviewed paper)."""
        if not isinstance(enrollment_id, str) or not enrollment_id or not isinstance(assignment_id, str) or not assignment_id:
            raise ValueError("Name the student's enrollment and the assignment.")
        with connect(self.path) as db:
            title = db.execute("SELECT title FROM assignments WHERE id=?", (assignment_id,)).fetchone()
            if not title:
                raise NotFound("Assignment not found.")
            copies = [row[0] for row in db.execute("""SELECT c.id FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id WHERE c.enrollment_id=? AND v.assignment_id=?
                ORDER BY b.created_at,c.id""", (enrollment_id, assignment_id))]
            jobs = {}

            def page(message_id, scan_page_id, number):
                if message_id not in jobs:
                    found = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
                    jobs[message_id] = found[0] if found else None
                return {"page_number": number, "scan_page_id": scan_page_id, "job_id": jobs[message_id],
                        "image": f"/api/documents/messages/{message_id}/pages/{scan_page_id}/image.png"}
            papers = []
            for copy_id in copies:
                found = self.assembly(copy_id, db)
                latest = found["revisions"][-1] if found["revisions"] else None
                paper = {"copy_id": copy_id, "version": found["version_label"], "section_id": found["section_id"],
                         "seat": found["seat_label"] or None, "label_mode": found["label_mode"],
                         "batch": "superseded" if found["superseded_at"] else found["batch_status"],
                         "chosen": found["selected_submission_revision_id"] == latest["id"] if latest and found["other_reviewed_copies"] else None}
                if latest:
                    chosen = sorted(json.loads(latest["selection_json"]), key=lambda item: item["page_number"])
                    messages = {row["id"]: row["message_id"] for row in db.execute(
                        f"SELECT id,message_id FROM scan_pages WHERE id IN ({','.join('?' * len(chosen))})", [item["scan_page_id"] for item in chosen])} if chosen else {}
                    paper["reviewed"] = {"revision_id": latest["id"], "revision": latest["revision"], "partial": bool(latest["partial"]),
                                         "actor": latest["actor"], "at": latest["created_at"],
                                         "pdf": f"/api/documents/assemblies/{latest['id']}/download",
                                         "pages": [page(messages[item["scan_page_id"]], item["scan_page_id"], item["page_number"])
                                                   for item in chosen if item["scan_page_id"] in messages]}
                else:
                    seen = []
                    for entry in found["pages"]:
                        accepted = [obs for obs in entry["observations"] if obs["status"] in ("resolved", "manual") and obs["review_state"] != "rejected"]
                        if accepted:
                            seen.append({**page(accepted[0]["message_id"], accepted[0]["id"], entry["page_number"]), "conflict": entry["conflict"]})
                    paper["reviewed"] = None
                    paper["seen"] = seen
                    paper["missing"] = found["missing"]
                papers.append(paper)
        return {"assignment_id": assignment_id, "title": title[0], "enrollment_id": enrollment_id, "papers": papers,
                "note": "" if papers else "No paper of this student was printed for this assignment, or none was scanned with its label."}

    def download_assembly(self, revision_id):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM assembly_revisions WHERE id=?", (revision_id,)).fetchone()
            if not row:
                raise NotFound("Assembly revision not found.")
            selection = json.loads(row["selection_json"])
            artifact_ids = [db.execute("SELECT page_artifact_id FROM scan_pages WHERE id=?", (s["scan_page_id"],)).fetchone()[0] for s in selection]
        # Source PDFs here are generated by the bounded parser, still parse only
        # within that boundary when assembling (see process merge operation).
        contents = [self.inbox.read_artifact(a)[1] for a in artifact_ids]
        return process.merge_pages(contents)
