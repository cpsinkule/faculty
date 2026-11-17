"""Exams printed without labels in a scan: find their covers, read who wrote them, and register them as that student's paper.

Requirement, 2026-09-24: DR-center and makeup exams are printed without the peel-off label and arrive in one scan with bubble
sheets and labeled paper. On an explicit request from the scan's page (the assignment and the scanned sides per exam), the local
OCR runtime reads the top of the unlabeled pages where a cover should be, up to BATCH (48) pages per worker turn in one model
load: the side count is the cadence, so after a cover the next page read is `sides` pages on, each page of a batch being taken
for a cover until its reading says otherwise (`next_batch`). Where that page is no cover every page after it is read in
turn, and at the end every unread page that LOOKS like a cover found (`LIKE`, measured on the first real scan: covers resemble
each other at 0.80 or more across versions, no other page reached 0.44) is read too, one page per turn, so a cover hidden by
two short exams is still found. A cover's fields (name, UIN, section) and its printed version are untrusted drafts, escaped wherever shown.

Who wrote it is matched against the roster of the assignment's sections by the bubble sheets' rule (`bubble_sheets.matching`) at
the instructor's threshold. By the instructor's decision (same day: "a wrong identity will be flagged when an exam already exists
for that person") a confident match identifies the student with no click, the ONE place besides the exact-alias fallback where a
reading identifies paper. It never does for a student who already has a scanned paper of this assignment, nor for two covers read
as one student: those wait for the instructor, as does anything unsure. The instructor may name the student or say a page is no
cover; the latest word counts, and the machine reading stays as it was.

Registering is the instructor's request with the fingerprint of what they saw. Each identified cover takes the page 1 identity of
that student's stamps-only label, as if the label had been stuck on: an issued label of this assignment no scan has shown is reused,
otherwise a new label job of the stamps-only version is issued for the student's section (never printed) and attached to the exam.
The existing first-page grouping (`SequenceService`) then gives the other sides their position, and the Inbox review assembles them.
Nothing here clears the review gate or grades. The printed version is evidence, except that the instructor's own Register click
confirms it for grading (`ExamVersionsMixin.registered_version`, requirement, 2026-09-26) unless the blank exams point elsewhere.
"""
import copy
import io
import json
import logging
import re
import threading
import uuid
from collections import OrderedDict
from contextlib import ExitStack

from werkzeug.exceptions import Conflict, Forbidden, NotFound

from course_storage import timestamp
import faculty_ocr
from faculty_ocr import BUSY, OcrError
from rosters import connect
from teach_inbox.service import PRESENT, encoded, sha

from . import jev_papers

# v2 (2026-09-24): the version is also read from the top band the name and UIN come from. Measured on the Chapter 2 Quiz's real
# first pages: the title box read no version on 10 of 10 (the quiz's version line sits above it), the top band read it on 6 of 6.
# v3 (2026-09-24, requirement: speed it up): first pages are read BATCH pages at a time in one model load (both regions of a
# page in the same load, the title box from the 300 DPI page render), and turned 180 degrees only when the upright band lacks
# the printed Name and UIN. v4 (same day, instructor: faster still): crops of one kind are read as a batch in one generation,
# and the title box only on a first page whose band gave no version. What a reading means is unchanged from v2. Since v4 the
# pages are rendered in parallel while the model loads, 48 per load; the crops are the same, so the rule is too. (Crops from the
# retained 200 DPI page image were tried and rejected: 2 of 22 handwritten names read worse than from the 300 DPI render.)
RULE = "unlabeled-covers-v4"
TITLE_BOX = (.12, .17, .88, .36)  # where the printed "Version A" line sits on exam covers, as fractions of the page
BATCH = 48          # first pages read per model load and worker turn (starting the reader and loading the model take ~7 s)
COVER_WORDS = ("name", "UIN")  # the printed field names a first page carries
LIKE = .62          # cover likeness at which an unread page is read too (covers >= .80, other pages <= .44 on the first scan)
MAX_TEXT = 2000
MAX_LIVE = 10       # live runs across all scans before new requests are refused
MATCHES_REMEMBERED = 4096  # first-page matches kept in memory for the scan page's refreshes (`_current_match`)
LOG = logging.getLogger(__name__)
STATES = ("registered", "registering", "identified", "conflict", "needs_review", "not_cover")


def _clean(value, limit):
    return re.sub(r"\s+", " ", str(value or "")).strip(" _.:-")[:limit]


def cover_fields(text):
    """(is_cover, fields) from what OCR read at the top of a page: a cover carries the printed Name and UIN field labels."""
    text = str(text or "")[:MAX_TEXT]
    # "VIN" is how the OCR reads a handwritten "UIN" label (2026-10-17, a take-home first page with no printed fields).
    cover = bool(re.search(r"\bname\b", text, re.I) and re.search(r"\b[UV]IN\b", text, re.I))
    stop = r"(?=\b[UV]IN\b|\bsection\b|\binstructor\b|$)"
    name = re.search(r"name(?:\s*\(printed\))?\s*[:\-]?[ \t]*(.*?)" + stop, text, re.I | re.M)
    uin = re.search(r"\b[UV]IN\b\s*[:#.\-]?[ \t]*(.*?)" + r"(?=\bsection\b|\binstructor\b|$)", text, re.I | re.M)
    section = re.search(r"\bsection(?:\s*(?:number|no\.?|#))?\s*[:#.\-]?[ \t]*(.*?)(?=\binstructor\b|$)", text, re.I | re.M)
    return cover, {"name": _clean(name.group(1) if name else "", 120), "uin": _clean(uin.group(1) if uin else "", 40),
                   "section": _clean(section.group(1) if section else "", 40)}


VERSION = r"version\s*[:#.\-]?\s*([A-Z])(?![A-Za-z])"


def printed_version(text):
    found = re.search(VERSION, str(text or "")[:MAX_TEXT], re.I)
    return found.group(1).upper() if found else ""


def next_read(attachments, reads, sides, likeness=None):
    """The next page to read, or None. `attachments`: pages in scan order (dicts with id, status); `reads`: {page id: cover}."""
    for pages in attachments:
        i, n = 0, len(pages)
        while i < n:
            page = pages[i]
            if page["status"] == "blank":
                i += 1
                continue
            if page["id"] not in reads:
                return page
            if reads[page["id"]]:
                j = i + sides
                if j == n:
                    break  # This exam reaches the end of its attachment.
                if j < n and pages[j]["status"] != "blank":
                    if pages[j]["id"] not in reads:
                        return pages[j]
                    if reads[pages[j]["id"]]:
                        i = j
                        continue
            i += 1
    if likeness:
        covers = [page for pages in attachments for page in pages if reads.get(page["id"])]
        for pages in attachments:
            for page in pages:
                if page["status"] != "blank" and page["id"] not in reads and covers and likeness(page, covers) >= LIKE:
                    return page
    return None


def next_batch(attachments, reads, sides, size):
    """The next `size` pages the side count points to, for one model load. Each is taken for a first page, as the cadence
    expects, until its reading says otherwise: a page read on that assumption that is no first page costs its reading only, and
    the next turn goes on from what was actually read. The look-alike search at the end (`LIKE`) stays one page at a time."""
    assumed, batch = dict(reads), []
    while len(batch) < size:
        page = next_read(attachments, assumed, sides, None)
        if page is None:
            break
        batch.append(page)
        assumed[page["id"]] = True
    return batch


class UnlabeledService:
    def __init__(self, scans):
        self.scans = scans
        self.inbox, self.rosters, self.path = scans.inbox, scans.rosters, scans.path
        self._signatures = {}
        self._matches, self._matches_lock = OrderedDict(), threading.Lock()

    # ----- scope ----------------------------------------------------------------------------

    def _job(self, db, message_id):
        message = self.inbox.message(db, message_id)
        job = db.execute("SELECT id,action FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
        if not message["authenticated"] or not job or job["action"] != "scan.receive":
            raise Forbidden("Finding papers without labels requires authenticated scanner intake.")
        return job

    def _scope(self, db, message_id, assignment_id, section_ids=None):
        """The assignment, its term and the sections whose roster is matched: the scan's grading association when it has one, else
        every section of the course's current term (the most recent when none is current), as for bubble sheets."""
        if not isinstance(assignment_id, str) or not db.execute("SELECT 1 FROM assignments WHERE id=?", (assignment_id,)).fetchone():
            raise ValueError("Choose the saved assignment these papers belong to.")
        grading = self.scans.grading
        linked = grading.scan_status(db, message_id, fingerprint=False)["plan"] if grading else None
        if linked:
            if assignment_id != linked["source_assignment_id"]:
                raise Conflict("Use this scan's associated assignment, or change its assignment from the scan summary first.")
            section_ids = linked["section_ids"]
        if not section_ids:
            section_ids = [row[0] for row in db.execute(
                """SELECT s.id FROM course_sections s JOIN course_terms t ON t.id=s.term_id JOIN assignments a ON a.definition_id=t.definition_id
                   WHERE a.id=? AND t.deleted_at IS NULL AND t.id=(SELECT n.id FROM course_terms n WHERE n.definition_id=t.definition_id AND n.deleted_at IS NULL
                       AND EXISTS(SELECT 1 FROM course_sections x WHERE x.term_id=n.id) ORDER BY n.is_current DESC,n.year DESC,n.id DESC LIMIT 1)
                   ORDER BY s.number""", (assignment_id,))]
            if not section_ids:
                raise ValueError("This assignment's course has no sections with a roster to match the first pages against.")
        return self.scans.documents.seating._validate_scope(db, assignment_id, section_ids)

    def _attachments(self, db, message_id):
        """The pages a cover may be among, per attachment in scan order: unlabeled or blank, never a bubble sheet or its blank back."""
        detector = self.scans.bubble_detector
        sheets = detector.sheet_pages(db, message_id) if detector else set()
        attachments, previous = {}, None
        for row in db.execute("""SELECT id,source_artifact_id,source_page_index,status,page_artifact_id,raster_artifact_id,content_sha256,page_stamp_id FROM scan_pages
                                 WHERE message_id=? ORDER BY source_artifact_id,source_page_index""", (message_id,)):
            row = dict(row)
            back = previous and previous["id"] in sheets and previous["source_artifact_id"] == row["source_artifact_id"] and row["status"] == "blank"
            previous = row
            if row["id"] in sheets or back or row["status"] not in ("unreadable", "blank") or row["page_stamp_id"]:
                continue
            attachments.setdefault(row["source_artifact_id"], []).append(row)
        return list(attachments.values())

    def _run(self, db, message_id):
        row = db.execute("SELECT * FROM scan_unlabeled_runs WHERE message_id=? ORDER BY attempt DESC LIMIT 1", (message_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def paper_sides(db, assignment_id):
        """How many sides one paper of the assignment has, from its blank templates (the Assignment Templates of grading): the page
        count most of them share, or None when it has none. Only a suggestion for the sides per paper, which the instructor sets.
        An assignment with a DR copy (GRADING "DR COPIES") is printed with labels everywhere else, so its papers without labels are
        the DR center's: the sides of its newest DR copy."""
        copy = db.execute("""SELECT json_extract(compiler_json,'$.dr_copy.pages') FROM assignment_versions WHERE assignment_id=?
                             AND json_extract(compiler_json,'$.dr_copy.version_id') IS NOT NULL ORDER BY created_at DESC,id DESC LIMIT 1""",
                          (assignment_id,)).fetchone()
        if copy and copy[0]:
            return copy[0]
        row = db.execute("""SELECT page_count FROM assignment_templates WHERE assignment_id=? AND page_count>0
                            GROUP BY page_count ORDER BY COUNT(*) DESC,page_count DESC LIMIT 1""", (assignment_id,)).fetchone()
        return row[0] if row else None

    # ----- requests -------------------------------------------------------------------------

    def request(self, message_id, data, actor="workspace", versions="", register_confident=False):
        """Queue reading the scan's first pages. `versions` and `register_confident` come only from an assignment created from this scan
        (`grading.intake`): the letters its papers may carry, and that what is identified with confidence is registered by itself."""
        sides = data.get("sides")
        if not isinstance(versions, str) or not re.fullmatch(r"(|[A-Z]{2,26})", versions):
            raise ValueError("Versions are letters A to Z.")
        if type(sides) is not int or not 1 <= sides <= 200:
            raise ValueError("Enter the scanned sides per paper, between 1 and 200.")
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db, message_id)
            decode = db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()
            if not decode or decode["status"] != "complete":
                raise Conflict("Wait for page inspection to finish before looking for papers without labels.")
            assignment, sections, term_id = self._scope(db, message_id, data.get("assignment_id"), data.get("section_ids"))
            current = self._run(db, message_id)
            if current and current["assignment_id"] == assignment["id"]:
                # Looking again (after a stop, say) keeps what the first run of this assignment was asked to do.
                versions, register_confident = versions or current["versions"], register_confident or bool(current["register_confident"])
            if current and current["status"] in ("pending", "running"):
                raise Conflict("Papers without labels are already being looked for in this scan.")
            if db.execute("SELECT COUNT(*) FROM scan_unlabeled_runs WHERE status IN ('pending','running')").fetchone()[0] >= MAX_LIVE:
                raise Conflict("Too many scans are waiting to be read. Try again when they finish.")
            if not any(self._attachments(db, message_id)):
                raise ValueError("This scan has no unlabeled pages that are not bubble sheets.")
            run_id, now = str(uuid.uuid4()), timestamp()
            # After a stop or a failure, asking again for the same assignment CONTINUES: the pages already read, and the instructor's
            # word on them, come with the new attempt (as the same immutable readings), so reading goes on where it stopped.
            # A finished run asked again (at another side count, say) continues the same way: its pages need not be read twice.
            carried = current if current and current["status"] in ("failed", "cancelled", "complete") and current["assignment_id"] == assignment["id"] else None
            db.execute("""INSERT INTO scan_unlabeled_runs(id,message_id,attempt,assignment_id,term_id,sides,status,error,actor,created_at,updated_at,
                          versions,register_confident) VALUES(?,?,?,?,?,?,'pending','',?,?,?,?,?)""",
                       (run_id, message_id, (current["attempt"] if current else 0) + 1, assignment["id"], term_id, sides, actor, now, now,
                        versions, int(bool(register_confident))))
            if carried:
                db.execute("""INSERT INTO scan_unlabeled_reads(run_id,scan_page_id,cover,reading_json,created_at)
                              SELECT ?,scan_page_id,cover,reading_json,created_at FROM scan_unlabeled_reads WHERE run_id=?""", (run_id, carried["id"]))
                db.execute("""INSERT INTO scan_unlabeled_reviews(run_id,scan_page_id,decision,enrollment_id,section_id,actor,created_at)
                              SELECT ?,scan_page_id,decision,enrollment_id,section_id,actor,created_at FROM scan_unlabeled_reviews WHERE run_id=? ORDER BY id""",
                           (run_id, carried["id"]))
            self.inbox.event(db, job["id"], "unlabeled_exams_requested", encoded({"run_id": run_id, "assignment_id": assignment["id"], "term_id": term_id,
                             "sides": sides, "rule": RULE, "instructor_review_required": True,
                             **({"continues_run": carried["id"]} if carried else {}),
                             **({"versions": versions} if versions else {}), **({"register_confident": True} if register_confident else {})}))
        return self.summary(message_id)

    def cancel(self, message_id, actor="workspace"):
        """Stop looking for first pages (requirement, 2026-09-24). The batch being read is finished and kept, like everything read
        before it; nothing is registered by the stopped run, and asking again starts a new one."""
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db, message_id)
            run = self._run(db, message_id)
            if not run or run["status"] not in ("pending", "running"):
                raise Conflict("First pages are not being looked for in this scan.")
            db.execute("UPDATE scan_unlabeled_runs SET status='cancelled',error=?,updated_at=? WHERE id=?",
                       ("Stopped by the instructor. Ask again to look for first pages.", timestamp(), run["id"]))
            self.inbox.event(db, job["id"], "unlabeled_exams_stopped", encoded({"run_id": run["id"], "actor": actor}))
        return self.summary(message_id)

    def review(self, message_id, data, actor="workspace"):
        """The instructor's word on one cover: {scan_page_id, decision: student|not_cover|read, enrollment_id?}."""
        decision = data.get("decision")
        if decision not in ("student", "not_cover", "read"):
            raise ValueError("Choose a student, say the page is not a first page, or go back to what was read.")
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            run = self._run(db, message_id)
            students = self._students(db, run) if run and decision == "student" else {}  # Verified roster reads come before the transaction.
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db, message_id)
            run = self._run(db, message_id)
            page_id = data.get("scan_page_id")
            if not run or not db.execute("SELECT 1 FROM scan_unlabeled_reads WHERE run_id=? AND scan_page_id=? AND cover=1", (run["id"], page_id)).fetchone() \
                    and page_id not in self._loose(db, run):
                raise NotFound("That page is not a first page found in this scan.")
            if db.execute("SELECT 1 FROM scan_unlabeled_plans WHERE run_id=? AND scan_page_id=?", (run["id"], page_id)).fetchone():
                raise Conflict("This first page is already registered. Its paper is corrected like any labeled paper, under Change assignment.")
            enrollment = section = None
            if decision == "student":
                enrollment = data.get("enrollment_id")
                if enrollment not in students:
                    raise ValueError("Choose a student on this assignment's rosters.")
                section = students[enrollment]["section_id"]
            db.execute("INSERT INTO scan_unlabeled_reviews(run_id,scan_page_id,decision,enrollment_id,section_id,actor,created_at) VALUES(?,?,?,?,?,?,?)",
                       (run["id"], page_id, decision, enrollment, section, actor, timestamp()))
            self.inbox.event(db, job["id"], "unlabeled_cover_reviewed", encoded({"run_id": run["id"], "scan_page_id": page_id, "decision": decision,
                             "instructor_review_required": True}))
        return self.summary(message_id)

    def register(self, message_id, data, actor="workspace"):
        """Register every identified cover not yet registered, exactly as the instructor last saw them (`fingerprint`)."""
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            job = self._job(db, message_id)
            run = self._run(db, message_id)
            if not run or run["status"] != "complete":
                raise Conflict("Wait until every first page has been read.")
            view = self._view(db, run)
            if data.get("fingerprint") != view["fingerprint"]:
                raise Conflict("The first pages changed. Look at them again before registering.")
            ready = [cover for cover in view["covers"] if cover["state"] == "identified"]
            if not ready:
                raise ValueError("No identified first page is waiting to be registered.")
            # JEV MIXED SCANS: each paper is registered under its own assignment (the run's, unless Jev read it as another of the course's).
            versions = {aid: self._stamps_version(db, aid) for aid in {cover["assignment_id"] for cover in ready}}
            reuse = {cover["scan_page_id"]: self._unscanned_copy(db, run, cover["student"]["enrollment_id"], cover["assignment_id"]) for cover in ready}
        new = {}
        for cover in ready:
            if not reuse[cover["scan_page_id"]]:
                new.setdefault((cover["assignment_id"], cover["student"]["section_id"]), []).append(cover)
        batches = {}
        for aid in sorted({aid for aid, _ in new}):
            # GRADING "DR COPIES": a quiz drafted in Faculty is printed with labels everywhere but at the DR center, so its paper
            # without labels is the DR center's copy and is registered under that copy's own document (made now when it is not
            # there yet), where its version's questions grade it on the copy's pages. A copy that cannot be made refuses the
            # registration, saying why: under the plain label sheet the paper could never be graded.
            copy = self.scans.grading.dr_copy(aid, run["term_id"]) if getattr(self.scans, "grading", None) else None
            if copy:
                versions[aid] = copy
            elif not versions[aid]:
                versions[aid] = self.scans.documents.register_stamp_pages(aid, 1, actor=actor)["id"]
        for (aid, section_id), covers in sorted(new.items()):
            students = sorted(cover["student"]["enrollment_id"] for cover in covers)
            # The run's own assignment keeps the request ID it always had; another assignment's names that assignment.
            request = f"unlabeled:{run['id']}:{section_id}:" + ("" if aid == run["assignment_id"] else f"{aid}:") + sha(encoded(students).encode())[:16]
            batches[(aid, section_id)] = (self.scans.documents.create_batch(versions[aid], section_id, len(students), request, students=students, actor=actor)["id"], request)
        with self.rosters.lock(), self.scans.documents.files.owner_lock(message_id), connect(self.path) as db:
            current = self._run(db, message_id)
            if not current or current["id"] != run["id"] or current["status"] != "complete" \
                    or self._view(db, current)["fingerprint"] != view["fingerprint"]:
                raise Conflict("The first pages changed while their labels were prepared. Look at them again before registering.")
            if not db.execute(f"SELECT {PRESENT.format('?')}", (message_id,)).fetchone()[0]:
                raise Conflict("This scan was deleted from the Inbox. Restore it first.")
            for cover in ready:
                copy = reuse[cover["scan_page_id"]]
                if copy and copy != self._unscanned_copy(db, current, cover["student"]["enrollment_id"], cover["assignment_id"]):
                    raise Conflict("An unused label was taken while these labels were prepared. Look at the first pages again before registering.")
            db.execute("BEGIN IMMEDIATE")
            now = timestamp()
            for cover in ready:
                student, copy = cover["student"], reuse[cover["scan_page_id"]]
                batch, request = batches.get((cover["assignment_id"], student["section_id"]), (None, f"unlabeled:{run['id']}:reuse")) if not copy else (None, f"unlabeled:{run['id']}:reuse")
                db.execute("INSERT OR IGNORE INTO scan_unlabeled_plans VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (run["id"], cover["scan_page_id"], student["enrollment_id"], student["section_id"], cover["basis"], copy, batch, request, actor, now))
            self.inbox.event(db, job["id"], "unlabeled_exams_registration_requested", encoded({"run_id": run["id"], "covers": len(ready),
                             "reused_labels": sum(1 for cover in ready if reuse[cover["scan_page_id"]]), "new_label_jobs": sorted(batch for batch, _ in batches.values()),
                             "instructor_review_required": True}))
        self.apply(message_id)
        return self.summary(message_id)

    # ----- registration ---------------------------------------------------------------------

    @staticmethod
    def _stamps_version(db, assignment_id):
        row = db.execute("""SELECT id FROM assignment_versions WHERE assignment_id=? AND json_extract(compiler_json,'$.generator')='faculty-stamps-only'
                            ORDER BY created_at DESC LIMIT 1""", (assignment_id,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _unscanned_copy(db, run, enrollment_id, assignment_id=None):
        """An issued, current stamps-only label of this assignment and student that no scan has shown and no review holds."""
        row = db.execute(f"""SELECT c.id FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
            WHERE c.enrollment_id=? AND v.assignment_id=? AND b.term_id=? AND b.status='ready' AND b.superseded_at IS NULL
              AND json_extract(v.compiler_json,'$.generator')='faculty-stamps-only'
              AND EXISTS(SELECT 1 FROM page_stamps p WHERE p.printed_copy_id=c.id AND p.page_number=1 AND p.state='issued' AND p.symbology!='sequence')
              AND NOT EXISTS(SELECT 1 FROM page_stamps p JOIN scan_pages o ON o.page_stamp_id=p.id WHERE p.printed_copy_id=c.id
                             AND {PRESENT.format('o.message_id')})
              AND NOT EXISTS(SELECT 1 FROM assembly_revisions ar WHERE ar.printed_copy_id=c.id)
            ORDER BY b.created_at,c.copy_number LIMIT 1""", (enrollment_id, assignment_id or run["assignment_id"], run["term_id"])).fetchone()
        return row[0] if row else None

    def apply(self, message_id=None):
        """Give each requested cover its page 1 identity once its label is issued; then attach new label jobs and group the scan.
        Returns whether anything was applied."""
        applied, finished = 0, []
        with self.rosters.lock(), ExitStack() as locks:
            with connect(self.path) as db:
                owners = [row[0] for row in db.execute(
                    """SELECT DISTINCT r.message_id FROM scan_unlabeled_plans p JOIN scan_unlabeled_runs r ON r.id=p.run_id
                       WHERE (? IS NULL OR r.message_id=?) AND NOT EXISTS(SELECT 1 FROM scan_unlabeled_registrations g WHERE g.scan_page_id=p.scan_page_id)""",
                    (message_id, message_id))]
            if not owners:
                return False
            for owner in sorted(owners):
                locks.enter_context(self.scans.documents.files.owner_lock(owner))
            db = locks.enter_context(connect(self.path))
            db.execute("BEGIN IMMEDIATE")
            for owner in owners:
                run = self._run(db, owner)
                job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (owner,)).fetchone()
                # PLANS OF EVERY RUN (requirement, 2026-11-06): looking again makes a run that carries no plans, so a plan still waiting for its
                # label job belongs to an earlier run of the message. Each is applied under its own run (the exam versions join on it); the
                # newest run's plan of a page comes first and is the one taken (a page has one registration).
                waiting, taken = set(), set()
                for plan in db.execute("""SELECT p.* FROM scan_unlabeled_plans p JOIN scan_unlabeled_runs r ON r.id=p.run_id WHERE r.message_id=?
                                          AND NOT EXISTS(SELECT 1 FROM scan_unlabeled_registrations g WHERE g.scan_page_id=p.scan_page_id)
                                          ORDER BY r.attempt DESC,p.scan_page_id""", (owner,)).fetchall():
                    if plan["scan_page_id"] in taken:
                        continue
                    # By copy number, the order the batch's own index gave before printed_copies_enrollment (document_identity 012).
                    copy = plan["printed_copy_id"] or (db.execute("SELECT c.id FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id WHERE b.id=? AND b.status='ready' AND c.enrollment_id=? ORDER BY c.copy_number",
                                                                  (plan["print_batch_id"], plan["enrollment_id"])).fetchone() or [None])[0]
                    stamp = db.execute("SELECT id FROM page_stamps WHERE printed_copy_id=? AND page_number=1 AND state='issued' AND symbology!='sequence'", (copy,)).fetchone() if copy else None
                    if not stamp:
                        waiting.add(plan["scan_page_id"])
                        continue
                    page = db.execute("SELECT * FROM scan_pages WHERE id=?", (plan["scan_page_id"],)).fetchone()
                    taken.add(page["id"])
                    if page["page_stamp_id"] or db.execute("SELECT 1 FROM assembly_pages WHERE scan_page_id=?", (page["id"],)).fetchone():
                        # Identified some other way since the request (an alias, say): that stands, and nothing is overwritten.
                        self.inbox.event(db, job["id"], "unlabeled_cover_not_registered", encoded({"run_id": plan["run_id"], "scan_page_id": page["id"],
                                         "reason": "The page was identified another way after the request."}))
                        db.execute("INSERT INTO scan_unlabeled_registrations VALUES(?,?,?,?,?,?,?)",
                                   (page["id"], plan["run_id"], copy, page["page_stamp_id"] or stamp[0], plan["enrollment_id"], "superseded", timestamp()))
                        continue
                    duplicate = db.execute(f"""SELECT id FROM scan_pages WHERE id!=? AND page_stamp_id=? AND content_sha256=? AND {PRESENT.format('scan_pages.message_id')}
                                               ORDER BY created_at,id LIMIT 1""", (page["id"], stamp[0], page["content_sha256"])).fetchone()
                    db.execute("UPDATE scan_pages SET page_stamp_id=?,status='manual',review_state='candidate',duplicate_of_scan_page_id=?,updated_at=? WHERE id=?",
                               (stamp[0], duplicate[0] if duplicate else None, timestamp(), page["id"]))
                    db.execute("INSERT INTO scan_unlabeled_registrations VALUES(?,?,?,?,?,?,?)",
                               (page["id"], plan["run_id"], copy, stamp[0], plan["enrollment_id"], plan["basis"], timestamp()))
                    self.inbox.event(db, job["id"], "unlabeled_cover_registered", encoded({"run_id": plan["run_id"], "scan_page_id": page["id"], "printed_copy_id": copy,
                                     "basis": plan["basis"], "instructor_review_required": True}))
                    applied += 1
                if not (waiting - taken):
                    finished.append(run)
        for run in finished:
            self._attach_and_group(run)
        return bool(applied or finished)

    def _attach_and_group(self, run):
        """New label jobs join the exam (when it exists), and the scan is grouped at the requested side count."""
        exams = self.scans.exams
        with connect(self.path) as db:
            # Each new label job joins the exam of its own assignment (JEV MIXED SCANS: a paper of another assignment in the scan).
            batches = [(row[0], row[1]) for row in db.execute("""SELECT DISTINCT p.print_batch_id,v.assignment_id FROM scan_unlabeled_plans p
                JOIN print_batches b ON b.id=p.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                WHERE p.run_id IN (SELECT id FROM scan_unlabeled_runs WHERE message_id=?) AND p.print_batch_id IS NOT NULL""", (run["message_id"],))]
            events = {aid: db.execute("SELECT id,version FROM exam_events WHERE assignment_id=? AND term_id=?", (aid, run["term_id"])).fetchone()
                      for aid in {aid for _, aid in batches}} if exams else {}
            attached = {row[0] for row in db.execute("SELECT batch_id FROM exam_event_batches")} if any(events.values()) else set()
        notes = []
        for batch, aid in batches:
            event = events.get(aid)
            if event and batch not in attached:
                try:
                    current = exams.get(event["id"])
                    exams.attach_batch(event["id"], {"version": current["version"], "batch_id": batch})
                except (ValueError, Conflict) as error:
                    notes.append(f"A new label job could not be attached to this assignment's term: {error}")
        sequence = self.scans.sequence
        try:
            preview = sequence.preview(run["message_id"], run["sides"])
            sequence.apply(run["message_id"], run["sides"], preview["fingerprint"], actor=run["actor"], origin="unlabeled")
        except (ValueError, Conflict) as error:
            notes.append(f"The scan could not be grouped at {run['sides']} sides: {error}")
        else:
            # Registered after the scan passed its review (the rest of it already went on to grading): these papers follow them now
            # (2026-09-28). Before the review this does nothing; the review assembles them.
            try:
                sequence.assemble_reviewed(run["message_id"], "automatic", quiet=True)
            except Exception:
                LOG.exception("Papers registered on reviewed scan %s could not be assembled; the worker tries again", run["message_id"])
        if notes:
            with self.rosters.lock(), connect(self.path) as db:
                job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (run["message_id"],)).fetchone()
                self.inbox.event(db, job["id"], "unlabeled_exams_grouping", encoded({"run_id": run["id"], "notes": notes}))

    # ----- reading --------------------------------------------------------------------------

    def _signature(self, page):
        """A cover's likeness: the top half of the page, small and normalized. Cached by content, since pages never change."""
        import numpy
        from PIL import Image
        cached = self._signatures.get(page["content_sha256"])
        if cached is None:
            _, content = self.inbox.read_artifact(page["raster_artifact_id"])
            image = Image.open(io.BytesIO(content)).convert("L").resize((68, 88))
            values = numpy.asarray(image, dtype=float)[:44]
            values = values - values.mean()
            norm = numpy.linalg.norm(values)
            cached = values / norm if norm else values
            if len(self._signatures) > 4000:
                self._signatures.clear()
            self._signatures[page["content_sha256"]] = cached
        return cached

    def _likeness(self, page, covers):
        mine = self._signature(page)
        return max(float((mine * self._signature(cover)).sum()) for cover in covers)

    def process_one(self):
        """One batch of pages read per worker turn (one model load), or one registration step, under the shared execution lock."""
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                for row in db.execute("SELECT * FROM scan_unlabeled_runs WHERE status='running'").fetchall():
                    # Interrupted mid-batch: keep finished readings; an explicit request continues.
                    db.execute("UPDATE scan_unlabeled_runs SET status='failed',error=?,updated_at=? WHERE id=?",
                               ("Reading was interrupted. Ask again to read the scan's first pages.", timestamp(), row["id"]))
                    job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (row["message_id"],)).fetchone()
                    self.inbox.event(db, job["id"], "unlabeled_exams_failed", "Reading was interrupted; what was read is kept with the run.")
                run = db.execute("SELECT * FROM scan_unlabeled_runs WHERE status='pending' ORDER BY created_at LIMIT 1").fetchone()
            if not run:
                return self.apply()
            run = dict(run)
            with self.rosters.lock(), self.scans.documents.files.owner_lock(run["message_id"]), connect(self.path) as db:
                reads = self._reads(db, run)
                attachments = self._attachments(db, run["message_id"])
            try:
                covers = {key: value["cover"] for key, value in reads.items()}
                batch = next_batch(attachments, covers, run["sides"], BATCH)
                if not batch:
                    page = next_read(attachments, covers, run["sides"], self._likeness)
                    batch = [page] if page else []
            except Exception:
                self._fail(run, "Comparing the pages with the first pages found failed. Ask again to read the scan's first pages.")
                return True
            if not batch:
                if self._finish(run) and run.get("register_confident"):
                    self._register_confident(run)
                return True
            with self.rosters.lock(), connect(self.path) as db:
                if not db.execute("UPDATE scan_unlabeled_runs SET status='running',updated_at=? WHERE id=? AND status='pending'", (timestamp(), run["id"])).rowcount:
                    return True
            try:
                readings = self._read_batch(batch)
            except OcrError as error:
                with self.rosters.lock(), connect(self.path) as db:
                    db.execute("UPDATE scan_unlabeled_runs SET status='pending',updated_at=? WHERE id=? AND status='running'", (timestamp(), run["id"]))
                if BUSY in str(error):
                    return False  # Another explicit OCR call holds the model; the page stays next.
                self._fail(run, "Reading a page failed or exceeded its limits. Ask again to read the scan's first pages.")
                return True
            except Exception:
                LOG.exception("Reading unlabeled pages %s failed", [page["id"] for page in batch])
                self._fail(run, "Reading a page failed or exceeded its limits. Ask again to read the scan's first pages.")
                return True
            # The roster at the time of the reading; the view matches again against the current one (Jev's match is kept as read).
            students = {}
            if any(reading["cover"] for reading in readings.values()):
                with self.rosters.lock(), connect(self.path) as db:
                    students = self._students(db, run)
            course = self._course_assignments(run) if any(reading["cover"] for reading in readings.values()) and self._jev() else []
            for page in batch:
                reading = readings[page["id"]]
                if reading["cover"]:
                    found = self._jev_student(page["id"], reading, students)  # Asked outside the catalog locks.
                    if found:
                        reading["jev_student"] = found
                    seen = self._jev_assignment(page["id"], reading, course) if len(course) > 1 else None
                    if seen:
                        # Whether Jev was shown how the run's own assignment begins: without its blank paper, "None" is no evidence.
                        reading["jev_assignment"] = {**seen, "run_blank": any(a["id"] == run["assignment_id"] and a.get("blank") for a in course)}
            with self.rosters.lock(), self.scans.documents.files.owner_lock(run["message_id"]), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                for page in batch:
                    reading = readings[page["id"]]
                    if reading["cover"]:
                        reading["match"] = self._match(reading["fields"], students)
                    db.execute("INSERT OR IGNORE INTO scan_unlabeled_reads VALUES(?,?,?,?,?)", (run["id"], page["id"], int(reading["cover"]), encoded(reading), timestamp()))
                # A run stopped while this batch was read stays stopped; the pages read are kept with it.
                db.execute("UPDATE scan_unlabeled_runs SET status='pending',updated_at=? WHERE id=? AND status='running'", (timestamp(), run["id"]))
            return True

    def _jev(self):
        """The workspace's Jev receipts (JEV PAPERS), when Jev is set up; None leaves the OCR reading alone."""
        jev = self.scans.jev
        from jev import client
        return jev if jev and client.available() else None

    def _jev_page(self, page):
        """JEV PAPERS: Jev's look at the whole page (whether a student wrote a name on it, which way up it is, where the name and UIN
        are), or {} when Jev is not set up. A failed or uncertain check decides nothing, and the OCR reading stands."""
        jev = self._jev()
        if not jev:
            return {}
        try:
            _, picture = self.inbox.read_artifact(page["raster_artifact_id"])
            return jev_papers.page_check(jev, page["id"], picture)
        except Exception:
            LOG.exception("Jev could not look at page %s; the OCR reading stands", page["id"])
            return {"status": "failed"}

    def _jev_student(self, page_id, reading, students):
        """JEV PAPERS: whose paper Jev reads this first page as, against the roster at the time of the reading, or None when Jev did not
        look at the page."""
        jev = self._jev()
        check = reading.get("jev_page") or {}
        if not jev or check.get("status") != "complete":
            return None
        try:
            with connect(self.path) as db:
                raster = db.execute("SELECT raster_artifact_id FROM scan_pages WHERE id=?", (page_id,)).fetchone()[0]
            _, picture = self.inbox.read_artifact(raster)
            found = jev_papers.identify(jev, page_id, picture, check, students)
        except Exception:
            LOG.exception("Jev could not match first page %s; it waits for the instructor", page_id)
            who = jev_papers.reader()
            found = {"status": "failed", "enrollment_id": None, "reason": f"{who} did not finish matching the name.",
                     **({} if who == "Jev" else {"reader": who})}
        return {**found, "roster": jev_papers.roster_key(students)}

    def _course_assignments(self, run):
        """JEV MIXED SCANS: the assignments of the run's course, each named with how its blank paper begins (its first template's first
        page: the text layer, else the title lines the assignment finder reads), for Jev's choice of a page's assignment."""
        with connect(self.path) as db:
            rows = db.execute("""SELECT a.id,a.title,(SELECT x.id FROM assignment_templates x WHERE x.assignment_id=a.id ORDER BY x.created_at LIMIT 1) template_id
                FROM assignments a WHERE a.definition_id=(SELECT definition_id FROM assignments WHERE id=?) ORDER BY a.title,a.id""", (run["assignment_id"],)).fetchall()
        found = []
        for row in rows:
            text = ""
            if row["template_id"]:
                try:
                    text = self.scans.assignment_finder.blank_description(row["template_id"])
                except Exception:  # noqa: BLE001 - an assignment whose blank cannot be read is still offered by its name
                    LOG.warning("The blank paper of assignment %s could not be read for Jev", row["id"], exc_info=True)
            found.append({"id": row["id"], "title": row["title"], "description": jev_papers.describe(text), "blank": bool(text.strip())})
        return found

    def _jev_assignment(self, page_id, reading, course):
        """JEV MIXED SCANS (requirement, 2026-10-15): which of the course's assignments Jev reads this first page as, or None when
        Jev did not look at the page."""
        jev = self._jev()
        if not jev or (reading.get("jev_page") or {}).get("status") != "complete":
            return None
        try:
            with connect(self.path) as db:
                raster = db.execute("SELECT raster_artifact_id FROM scan_pages WHERE id=?", (page_id,)).fetchone()[0]
            _, picture = self.inbox.read_artifact(raster)
            return jev_papers.which_assignment(jev, page_id, picture, course)
        except Exception:
            LOG.exception("Jev could not read which assignment first page %s is", page_id)
            return {"status": "failed", "assignment_id": None}

    def _read_batch(self, pages):
        """{page id: reading} for pages read in one model load. A reading is what v2 kept for one page: the band reads, whether it
        is a first page, and for a first page its fields, rotation, title and version."""
        contents = []
        for page in pages:
            _, content = self.inbox.read_artifact(page["page_artifact_id"])
            if sha(content) != page["content_sha256"]:
                raise ValueError("Retained page checksum differs.")
            contents.append(content)
        # The title box is read only where the band gave no version (a quiz prints it in the band, an exam cover in the box).
        result = faculty_ocr.recognize_covers(contents, list(TITLE_BOX), list(COVER_WORDS), title_unless=VERSION)
        readings = {}
        for page, content, read in zip(pages, contents, result["pages"]):
            if read["source_sha256"] != sha(content):
                raise ValueError("OCR source checksum differs.")
            bands = [{"rotation": item["rotation"], "text": (item.get("text") or "")[:MAX_TEXT]} for item in read["reads"] if item["kind"] == "band"]
            titles = {item["rotation"]: (item.get("text") or "")[:MAX_TEXT] for item in read["reads"] if item["kind"] == "title"}
            chosen = next(((band, *cover_fields(band["text"])) for band in bands if cover_fields(band["text"])[0]), None)
            check = self._jev_page(page)
            if not chosen and check.get("first_page"):
                # JEV PAPERS: Jev sees a handwritten name on a page whose top carries no printed Name and UIN (a student who wrote
                # the name in a corner, or a page with no Name line). It is a first page; the band it was read upright (or turned,
                # as Jev saw the page) still gives what the OCR read there.
                turned = 180 if check.get("orientation") == "upside down" else 0
                band = next((band for band in bands if band["rotation"] == turned), bands[0] if bands else {"rotation": turned, "text": ""})
                chosen = (band, True, cover_fields(band["text"])[1])
                check = {**check, "found_first_page": True}
            reading = {"rule": RULE, "reads": bands, "cover": bool(chosen), "model_id": result.get("model_id"), "revision": result.get("revision"),
                       "source_sha256": read["source_sha256"], "crop_profile": result.get("crop_profile"),
                       **({"jev_rule": jev_papers.RULE, "jev_page": check} if check else {})}
            if chosen:
                band, _, fields = chosen
                title = titles.get(band["rotation"], "")
                # Either reading may find the version: exam covers print it in the title box, quizzes in the top band. One reading
                # alone counts; two that disagree count as none, and the paper's version waits for the instructor.
                in_band, in_title = printed_version(band["text"]), printed_version(title)
                reading.update(rotation=band["rotation"], fields=fields, title=title, title_read=band["rotation"] in titles,
                               version=in_band if in_band == in_title or not in_title else in_title if not in_band else "",
                               versions_read={"band": in_band, "title": in_title})
            readings[page["id"]] = reading
        return readings

    def _fail(self, run, error):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE scan_unlabeled_runs SET status='failed',error=?,updated_at=? WHERE id=? AND status IN ('pending','running')", (error, timestamp(), run["id"]))
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (run["message_id"],)).fetchone()
            self.inbox.event(db, job["id"], "unlabeled_exams_failed", error)

    def _finish(self, run):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("UPDATE scan_unlabeled_runs SET status='complete',updated_at=? WHERE id=? AND status='pending'", (timestamp(), run["id"])).rowcount:
                covers = db.execute("SELECT COUNT(*) FROM scan_unlabeled_reads WHERE run_id=? AND cover=1", (run["id"],)).fetchone()[0]
                job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (run["message_id"],)).fetchone()
                self.inbox.event(db, job["id"], "unlabeled_exams_read", encoded({"run_id": run["id"], "covers": covers, "instructor_review_required": True}))
                return True
        return False

    def _register_confident(self, run):
        """A run started with an assignment made from its scan registers by itself what it identified (requirement, 2026-09-24): an exact roster UIN or a match at the bubble-sheet threshold, and nothing in conflict. Anything unsure,
        conflicting or unreadable still waits for the instructor's word and Register. The review gate is untouched."""
        with self.rosters.lock(), connect(self.path) as db:
            view = self._view(db, self._run(db, run["message_id"]))
        if not any(cover["state"] == "identified" for cover in view["covers"]):
            return
        try:
            self.register(run["message_id"], {"fingerprint": view["fingerprint"]}, actor="automatic")
        except (ValueError, Conflict) as error:
            with self.rosters.lock(), connect(self.path) as db:
                job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (run["message_id"],)).fetchone()
                self.inbox.event(db, job["id"], "unlabeled_exams_not_registered", encoded({"run_id": run["id"], "reason": str(error)}))

    # ----- what the instructor sees -----------------------------------------------------------

    @staticmethod
    def _loose(db, run):
        """LOOSE FIRST PAGES (requirement, 2026-10-17): pages this run read as no
        first page that are in no paper once its search finished and the scan was grouped (written, no identity, not set aside, and
        not a page Jev sees as blank: 2026-10-17, "it thinks blank pages should have a name"). Each
        is listed with the first pages, so the instructor can name its student and register it like any first page."""
        if run["status"] != "complete" or not db.execute("SELECT 1 FROM scan_sequence_runs WHERE message_id=? AND undone_at IS NULL LIMIT 1",
                                                         (run["message_id"],)).fetchone():
            return set()
        return {row[0] for row in db.execute("""SELECT r.scan_page_id FROM scan_unlabeled_reads r JOIN scan_pages sp ON sp.id=r.scan_page_id
            WHERE r.run_id=? AND r.cover=0 AND sp.status='unreadable' AND sp.page_stamp_id IS NULL AND sp.review_state!='ignored'
              AND COALESCE(json_extract(r.reading_json,'$.jev_page.orientation'),'')!='blank'""", (run["id"],))}

    def _reads(self, db, run):
        return {row["scan_page_id"]: {"cover": bool(row["cover"]), **json.loads(row["reading_json"])}
                for row in db.execute("SELECT * FROM scan_unlabeled_reads WHERE run_id=?", (run["id"],))}

    def _students(self, db, run):
        """Who a first page may belong to: the sections chosen for this scan's assessment when it has one (requirement, 2026-09-24:
        the students of the sections ticked when the assignment was made), else every section of the run's term."""
        grading = self.scans.grading
        linked = grading.scan_status(db, run["message_id"], fingerprint=False)["plan"] if grading else None
        if linked and linked["source_assignment_id"] == run["assignment_id"] and linked["term_id"] == run["term_id"] and linked["section_ids"]:
            chosen = list(linked["section_ids"])
            sections = [dict(row) for row in db.execute(f"SELECT id,number FROM course_sections WHERE id IN ({','.join('?' * len(chosen))}) ORDER BY number", chosen)]
        else:
            sections = [dict(row) for row in db.execute("SELECT id,number FROM course_sections WHERE term_id=? ORDER BY number", (run["term_id"],))]
        return self.scans.documents.seating._students(db, run["term_id"], sections)

    def search_students(self, message_id, data):
        """Students whose UIN is closest to digits typed into a first page's student picker; names and sections only."""
        from bubble_sheets import matching
        typed = data.get("uin") if isinstance(data, dict) else None
        if not isinstance(typed, str) or len(typed) > 40:
            raise ValueError("Type part of a UIN.")
        with self.rosters.lock(), connect(self.path) as db:
            self._job(db, message_id)
            run = self._run(db, message_id)
            if not run:
                raise NotFound("First pages have not been looked for in this scan.")
            students = self._students(db, run)
        return {"students": matching.uin_search(typed, list(students.values()))}

    @staticmethod
    def _match(fields, students):
        from bubble_sheets import matching
        return matching.match(fields.get("uin", ""), fields.get("name", ""), list(students.values()))

    @staticmethod
    def _roster_key(students):
        """Everything a match depends on besides the reading: each student matched against, in the order they are matched."""
        return sha(encoded([[s["enrollment_id"], s["section_id"], s["section_number"], s["uin"], s["name"]] for s in students.values()]).encode())

    def _current_match(self, fields, students, roster):
        """The match against the current roster, remembered while the reading and the roster stay the same. A view is drawn on every
        refresh of the scan page, and one first page against a few hundred students takes about 20 ms (2026-09-24: 326 first pages
        made the page take 7 s). A match is a pure function of these inputs, so a remembered one is the same answer."""
        key = (roster, fields.get("uin", ""), fields.get("name", ""))
        with self._matches_lock:
            known = self._matches.get(key)
            if known is not None:
                self._matches.move_to_end(key)
                return copy.deepcopy(known)
        result = self._match(fields, students)
        with self._matches_lock:
            self._matches[key] = result
            while len(self._matches) > MATCHES_REMEMBERED:
                self._matches.popitem(last=False)
        return copy.deepcopy(result)

    def _threshold(self, db):
        detector = self.scans.bubble_detector
        if detector:
            return detector.auto_identify(db)
        from bubble_sheets import matching
        return matching.AUTO_IDENTIFY

    def _scanned(self, db, run, assignment_id=None):
        """Students who already have a scanned paper of this assignment in this term, other than covers this scan registered (in any
        attempt: 2026-10-17, a cover registered by the first attempt was held by the second as its own student's "other paper")."""
        return {row[0] for row in db.execute(f"""SELECT DISTINCT c.enrollment_id FROM scan_pages o JOIN page_stamps p ON p.id=o.page_stamp_id
            JOIN printed_copies c ON c.id=p.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
            JOIN assignment_versions v ON v.id=b.assignment_version_id
            WHERE v.assignment_id=? AND b.term_id=? AND c.enrollment_id IS NOT NULL AND o.status IN ('resolved','manual')
              AND {PRESENT.format('o.message_id')}
              AND NOT EXISTS(SELECT 1 FROM scan_unlabeled_registrations g JOIN scan_unlabeled_runs r ON r.id=g.run_id
                             WHERE g.scan_page_id=o.id AND r.message_id=?)""",
            (assignment_id or run["assignment_id"], run["term_id"], run["message_id"]))}

    def _view(self, db, run):
        """Every cover with its reading, its student and its state; the fingerprint names exactly what registration would do."""
        from bubble_sheets import matching
        students = self._students(db, run)
        threshold = self._threshold(db)
        scanned = {run["assignment_id"]: self._scanned(db, run)}
        titles = dict(db.execute("SELECT id,title FROM assignments WHERE definition_id=(SELECT definition_id FROM assignments WHERE id=?)",
                                 (run["assignment_id"],)).fetchall())
        reviews = {}
        for row in db.execute("SELECT * FROM scan_unlabeled_reviews WHERE run_id=? ORDER BY id", (run["id"],)):
            reviews[row["scan_page_id"]] = dict(row)
        plans = {row["scan_page_id"]: dict(row) for row in db.execute("SELECT * FROM scan_unlabeled_plans WHERE run_id=?", (run["id"],))}
        # Registered by any attempt on this scan: a cover registered before the instructor looked again stays registered.
        registered = {row["scan_page_id"]: dict(row) for row in db.execute("""SELECT g.* FROM scan_unlabeled_registrations g JOIN scan_unlabeled_runs r
                                                                              ON r.id=g.run_id WHERE r.message_id=?""", (run["message_id"],))}
        pages = {row["id"]: dict(row) for row in db.execute("SELECT id,source_page_index,source_artifact_id FROM scan_pages WHERE message_id=?", (run["message_id"],))}
        roster = self._roster_key(students)
        # A reading kept before `run_blank` was recorded: whether the run's assignment has a blank paper on file now.
        blank_now = bool(db.execute("SELECT 1 FROM assignment_templates WHERE assignment_id=? LIMIT 1", (run["assignment_id"],)).fetchone())
        covers, loose = [], self._loose(db, run)
        for page_id, reading in sorted(self._reads(db, run).items(), key=lambda item: (pages[item[0]]["source_artifact_id"], pages[item[0]]["source_page_index"])):
            review = reviews.get(page_id)
            if not reading["cover"] and page_id not in loose and not (review and review["decision"] == "student"):
                continue
            held = plans.get(page_id) or registered.get(page_id)
            # Registered, or waiting for its label: its student is the one registration asked for, which it claims against the other
            # first pages. Nothing depends on matching it again and the page shows no match for it (2026-09-24: matching the 321
            # registered first pages of a section's scan on every refresh took 7 s).
            match = None if held else self._current_match(reading.get("fields") or {}, students, roster)
            jev_pick = (reading.get("jev_student") or {}).get("enrollment_id")
            who = (reading.get("jev_student") or {}).get("reader") or "Jev"  # GEMINI MATCHES THE STUDENT: who read whose paper it is
            # JEV MIXED SCANS: the paper's assignment is the run's unless Jev, in both orders, read it as another of the course's.
            seen = dict(reading.get("jev_assignment") or {})
            if seen.get("none") and not seen.get("run_blank", blank_now):
                seen["none"] = False  # JEV MIXED SCANS: Jev never saw the run's own assignment, so "None of these" holds nothing
            assignment = seen["assignment_id"] if seen.get("assignment_id") in titles else run["assignment_id"]
            if held:
                state, student, basis = "identified", held["enrollment_id"], held["basis"]
            elif review and review["decision"] == "not_cover":
                state, student, basis = "not_cover", None, "review"
            elif review and review["decision"] == "student":
                student, basis = review["enrollment_id"], "review"
                state = "identified"
            elif seen.get("none"):
                # JEV MIXED SCANS: Jev reads the page as none of the course's assignments; the instructor decides.
                state, student, basis = "needs_review", None, None
            elif matching.identified(match, threshold) and jev_pick and jev_pick != match["enrollment_id"]:
                # JEV PAPERS: the OCR reading and Jev name different students; the instructor decides.
                state, student, basis = "needs_review", None, None
            elif matching.identified(match, threshold):
                student, basis = match["enrollment_id"], "uin" if match["state"] == "matched" else "confidence"
                state = "identified"
            elif jev_pick:
                state, student, basis = "identified", jev_pick, "jev"
            else:
                state, student, basis = "needs_review", None, None
            # A loose page had no name or UIN read at all: what the match says about an empty reading is no news (LOOSE FIRST PAGES).
            notes = list((match or {}).get("notes") or []) if reading["cover"] else []
            if not reading["cover"] and not review:
                notes.append("Read as no first page, and it is in no paper: nothing on it was read for a name. If it is one, choose its "
                             "student (search by the UIN written on it); if not, say so.")
            if jev_pick and match and matching.identified(match, threshold) and jev_pick != match["enrollment_id"] and not held and not review:
                notes.append(f"{who} reads the handwritten name and UIN as another student's. Check them, then choose the student.")
            elif state == "needs_review" and not review and (reading.get("jev_student") or {}).get("reason"):
                notes.append(f"{who}: " + reading["jev_student"]["reason"])
            if seen.get("none") and not held and not review:
                notes.append("Jev reads this page as none of this course's assignments. Check it, then choose the student or say it is not a first page.")
            if assignment != run["assignment_id"]:
                notes.append(f"Jev reads this paper as {titles[assignment]}; it is registered under {titles[assignment]}.")
            read = reading.get("version") or ""
            if assignment == run["assignment_id"] and run.get("versions") and (not read or read not in run["versions"]):  # ("" is in every string)
                # Identity does not depend on it; grading asks the instructor for this paper's version.
                notes.append(f"Version {read} is not one of this assignment's versions ({', '.join(run['versions'])})." if read
                             else f"No version ({', '.join(run['versions'])}) was read on this first page.")
            if student and student not in students:
                state, notes = "needs_review", notes + ["That student is no longer on these rosters."]
                student = None
            turned = 180 if (reading.get("jev_page") or {}).get("orientation") == "upside down" else 0
            covers.append({"scan_page_id": page_id, "page": pages[page_id]["source_page_index"], "fields": reading.get("fields") or {},
                           "version": reading.get("version") or "", "rotation": reading.get("rotation", turned), "match": match,
                           "enrollment_id": student, "basis": basis, "state": state, "notes": notes, "reviewed": bool(review),
                           "assignment_id": assignment, "assignment_title": titles.get(assignment, ""),
                           **({"reader": who} if basis == "jev" and who != "Jev" else {})})
        claimed = {}
        for cover in covers:
            if cover["enrollment_id"] and cover["state"] == "identified":
                claimed.setdefault((cover["assignment_id"], cover["enrollment_id"]), []).append(cover)
        for cover in covers:
            student = cover["enrollment_id"]
            if cover["assignment_id"] not in scanned:
                scanned[cover["assignment_id"]] = self._scanned(db, run, cover["assignment_id"])
            if cover["scan_page_id"] in registered:
                cover["state"] = "registered"
            elif cover["scan_page_id"] in plans:
                cover["state"] = "registering"
            elif cover["state"] == "identified" and len(claimed.get((cover["assignment_id"], student), [])) > 1:
                cover["state"] = "conflict"
                cover["notes"].append("Another first page in this scan is read as the same student.")
                cover["other_paper"] = True  # OTHER PAPER: the flag opens the paper it is compared with
            elif cover["state"] == "identified" and student in scanned[cover["assignment_id"]]:
                if cover["basis"] == "review":
                    cover["notes"].append("This student already has a scanned paper of this assignment; both will be listed for review.")
                else:
                    cover["state"] = "conflict"
                    cover["notes"].append("This student already has a scanned paper of this assignment. Check the name and UIN, then choose the student.")
                cover["other_paper"] = True
            plan = plans.get(cover["scan_page_id"])
            chosen = (plan or {}).get("enrollment_id") or student
            cover["student"] = ({"enrollment_id": chosen, "section_id": students[chosen]["section_id"], "name": students[chosen]["name"],
                                 "section_number": students[chosen]["section_number"]} if chosen in students else None)
        ready = [cover for cover in covers if cover["state"] == "identified"]
        # A paper of the run's own assignment keeps the fingerprint entry it always had; another assignment's names it (JEV MIXED SCANS).
        fingerprint = sha(encoded([[cover["scan_page_id"], cover["student"]["enrollment_id"], cover["basis"],
                                    *([] if cover["assignment_id"] == run["assignment_id"] else [cover["assignment_id"]])] for cover in ready]).encode())
        return {"covers": covers, "fingerprint": fingerprint, "threshold": threshold,
                "students": sorted(({"enrollment_id": key, "name": value["name"], "section_number": value["section_number"]} for key, value in students.items()),
                                   key=lambda item: item["name"].casefold())}

    def summary(self, message_id):
        """The panel's data, or None for a submission this tool does not apply to (unauthenticated, or not a scan)."""
        with self.rosters.lock(), connect(self.path) as db:
            try:
                self._job(db, message_id)
            except Forbidden:
                return None
            run = self._run(db, message_id)
            candidates = sum(len(pages) for pages in self._attachments(db, message_id))
            assignments = self._assignments(db, message_id)
            # One paper's length, suggested for the sides per paper when the scan is filed under an assignment with blank templates.
            linked = next((a for a in assignments if a["linked"]), None)
            result = {"rule": RULE, "run": None, "candidates": candidates, "covers": [], "counts": {}, "fingerprint": None, "students": [],
                      "runtime_available": faculty_ocr.available(), "assignments": assignments,
                      "paper_sides": self.paper_sides(db, linked["id"]) if linked else None}
            if not run:
                return result
            reads = db.execute("SELECT COUNT(*),SUM(cover) FROM scan_unlabeled_reads WHERE run_id=?", (run["id"],)).fetchone()
            assignment = db.execute("SELECT title FROM assignments WHERE id=?", (run["assignment_id"],)).fetchone()
            result["run"] = {**{key: run[key] for key in ("id", "attempt", "status", "error", "sides", "assignment_id", "term_id", "created_at", "updated_at",
                                                        "versions")}, "register_confident": bool(run["register_confident"]),
                             "title": assignment["title"] if assignment else "", "pages_read": reads[0], "covers_found": reads[1] or 0}
            if run["status"] in ("complete", "failed", "cancelled"):
                view = self._view(db, run)
                result.update(covers=view["covers"], fingerprint=view["fingerprint"], students=view["students"], threshold=view["threshold"],
                              counts={state: sum(1 for cover in view["covers"] if cover["state"] == state) for state in STATES})
            return result

    def _assignments(self, db, message_id):
        """What the request can name: the scan's associated assignment, else the assignments of courses with a current term."""
        grading = self.scans.grading
        linked = grading.scan_status(db, message_id, fingerprint=False)["plan"] if grading else None
        if linked:
            return [{"id": linked["source_assignment_id"], "title": linked["title"], "code": linked.get("code", ""), "linked": True}]
        return [dict(row) | {"linked": False} for row in db.execute(
            """SELECT a.id,a.title,d.code FROM assignments a JOIN course_definitions d ON d.id=a.definition_id
               WHERE EXISTS(SELECT 1 FROM course_terms t JOIN course_sections s ON s.term_id=t.id WHERE t.definition_id=a.definition_id
                            AND t.deleted_at IS NULL AND t.is_current=1)
               ORDER BY d.code,a.title,a.id""")]

    def status(self, db, message_id):
        """What the scan's overview says, from the tables alone (it is shown on every Inbox poll): no roster read, no matching."""
        run = self._run(db, message_id)
        if not run:
            return None
        row = db.execute("""SELECT COUNT(*) pages,COALESCE(SUM(cover),0) covers,
                              (SELECT COUNT(*) FROM scan_unlabeled_plans p WHERE p.run_id=:r) planned,
                              (SELECT COUNT(*) FROM scan_unlabeled_registrations g WHERE g.run_id=:r) registered,
                              (SELECT COUNT(DISTINCT v.scan_page_id) FROM scan_unlabeled_reviews v WHERE v.run_id=:r AND v.decision='not_cover'
                                 AND v.id=(SELECT MAX(w.id) FROM scan_unlabeled_reviews w WHERE w.run_id=v.run_id AND w.scan_page_id=v.scan_page_id)) not_cover
                            FROM scan_unlabeled_reads WHERE run_id=:r""", {"r": run["id"]}).fetchone()
        return {"status": run["status"], "error": run["error"], "pages_read": row["pages"], "covers": row["covers"], "planned": row["planned"],
                "registered": row["registered"], "not_cover": row["not_cover"], "sides": run["sides"], "assignment_id": run["assignment_id"]}

    def other_paper(self, message_id, scan_page_id):
        """OTHER PAPER (requirement, 2026-10-17): what a flagged first page is compared with. The other first pages of this scan read as the same
        student, and each scanned paper of the assignment the student already has elsewhere, its pages in order. Read only."""
        with self.rosters.lock(), connect(self.path) as db:
            self._job(db, message_id)
            run = self._run(db, message_id)
            if not run or run["status"] not in ("complete", "failed", "cancelled"):
                raise NotFound("First pages have not been looked for in this scan.")
            covers = self._view(db, run)["covers"]
            cover = next((c for c in covers if c["scan_page_id"] == scan_page_id), None)
            if not cover or not cover["enrollment_id"]:
                raise NotFound("That first page is not read as any student.")
            student, assignment = cover["enrollment_id"], cover["assignment_id"]
            here = [{"scan_page_id": c["scan_page_id"], "page": c["page"], "state": c["state"]} for c in covers
                    if c["scan_page_id"] != scan_page_id and c["enrollment_id"] == student and c["assignment_id"] == assignment
                    and c["state"] in ("identified", "conflict", "registering", "registered")]
            rows = db.execute(f"""SELECT o.id,o.message_id,o.source_page_index,p.page_number,c.id copy_id,c.copy_number,f.filename
                FROM scan_pages o JOIN page_stamps p ON p.id=o.page_stamp_id JOIN printed_copies c ON c.id=p.printed_copy_id
                JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                LEFT JOIN inbox_artifacts f ON f.id=o.source_artifact_id
                WHERE v.assignment_id=? AND b.term_id=? AND c.enrollment_id=? AND o.status IN ('resolved','manual')
                  AND o.review_state NOT IN ('duplicate','rejected','ignored') AND {PRESENT.format('o.message_id')}
                  AND NOT EXISTS(SELECT 1 FROM scan_unlabeled_registrations g JOIN scan_unlabeled_runs r ON r.id=g.run_id
                                 WHERE g.scan_page_id=o.id AND r.message_id=?)
                ORDER BY c.copy_number,c.id,p.page_number,o.created_at,o.id""", (assignment, run["term_id"], student, run["message_id"])).fetchall()
        papers = OrderedDict()
        for row in rows:
            paper = papers.setdefault(row["copy_id"], {"copy_number": row["copy_number"], "pages": []})
            paper["pages"].append({"scan_page_id": row["id"], "page_number": row["page_number"], "attachment_page": row["source_page_index"],
                                   "message_id": row["message_id"], "filename": row["filename"] or ""})
        name = (cover.get("student") or {}).get("name", "")
        return {"student": name, "this_page": cover["page"], "here": here, "papers": list(papers.values())}

    def crop(self, message_id, scan_page_id):
        """The top of one cover, upright, for the instructor to read the name and UIN. Cut on request; nothing is stored."""
        from PIL import Image
        with self.rosters.lock(), connect(self.path) as db:
            self._job(db, message_id)
            run = self._run(db, message_id)
            row = db.execute("""SELECT sp.raster_artifact_id,r.reading_json,r.cover FROM scan_unlabeled_reads r JOIN scan_pages sp ON sp.id=r.scan_page_id
                                WHERE r.run_id=? AND r.scan_page_id=?""", (run["id"] if run else "", scan_page_id)).fetchone()
            if not row or not row["cover"] and scan_page_id not in self._loose(db, run) and not db.execute(
                    "SELECT 1 FROM scan_unlabeled_reviews WHERE run_id=? AND scan_page_id=? AND decision='student'", (run["id"], scan_page_id)).fetchone():
                raise NotFound("That page is not a first page found in this scan.")
        _, content = self.inbox.read_artifact(row["raster_artifact_id"])
        image = Image.open(io.BytesIO(content)).convert("L")
        reading = json.loads(row["reading_json"])
        check = reading.get("jev_page") or {}
        if check.get("found_first_page"):
            # JEV PAPERS: a first page only Jev found may carry the name anywhere; show where Jev saw the name and UIN.
            width, height = image.size
            boxes = [check["name_box"], check["uin_box"]]
            part = image.crop((int(max(0, min(b["left"] for b in boxes) - .04) * width), int(max(0, min(b["top"] for b in boxes) - .04) * height),
                               int(min(1, max(b["right"] for b in boxes) + .04) * width), int(min(1, max(b["bottom"] for b in boxes) + .04) * height)))
            if reading.get("rotation") == 180:
                part = part.rotate(180)
            part.thumbnail((1100, 1100))
            buffer = io.BytesIO()
            part.save(buffer, "PNG")
            return buffer.getvalue()
        if reading.get("rotation", 180 if check.get("orientation") == "upside down" else 0) == 180:
            image = image.rotate(180)  # a loose page has no reading's rotation: Jev's look says which way up it is
        width, height = image.size
        part = image.crop((0, 0, width, int(height * TITLE_BOX[3])))
        part.thumbnail((1100, 1100))
        buffer = io.BytesIO()
        part.save(buffer, "PNG")
        return buffer.getvalue()
