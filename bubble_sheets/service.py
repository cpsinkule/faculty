"""Bubble sheet readings of a retained scan: a queued read, the instructor's review, and an export.

Ownership: a run belongs to the existing source Inbox message and references
the course-wide assignment, the term and the permanent sections the instructor
chose for it; a sheet references its existing scan page; a review references a
permanent (section, enrollment) roster membership. They are catalog records in
the nightly verified backup. Nothing here grades, releases, emails, identifies
paper, or clears the Inbox review gate: Gradescope stays the grader, and this is
the instructor's own record to compare with it.

A machine reading is immutable. Whatever it was unsure about (a faint or erased
mark, several marks, a missing version, a student it could not match
exactly by UIN) waits for a review; reviews are append-only and the latest one
is the sheet's current value.
"""
import csv
import io
import json
import re
import uuid

from PIL import Image
from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT, encoded

from . import matching, reader

# BUBBLE-SHEETS / INBOX: "each present scan's latest complete read", the run a query over sheets reads (with PRESENT and
# `r.status='complete'` beside it); `r` is the bubble_runs row.
LATEST_READ = "r.created_at=(SELECT MAX(n.created_at) FROM bubble_runs n WHERE n.message_id=r.message_id AND n.status='complete')"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bubble_runs (
 id TEXT PRIMARY KEY NOT NULL, message_id TEXT NOT NULL REFERENCES inbox_messages(id),
 assignment_id TEXT NOT NULL REFERENCES assignments(id), term_id TEXT NOT NULL REFERENCES course_terms(id),
 question_count INTEGER NOT NULL CHECK(question_count BETWEEN 1 AND 100), profile TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')), error TEXT NOT NULL DEFAULT '',
 summary_json TEXT NOT NULL DEFAULT '{}', actor TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS bubble_runs_active ON bubble_runs(message_id) WHERE status IN ('pending','running');
CREATE TABLE IF NOT EXISTS bubble_run_sections (
 run_id TEXT NOT NULL REFERENCES bubble_runs(id), section_id TEXT NOT NULL REFERENCES course_sections(id), PRIMARY KEY(run_id,section_id)
);
CREATE TABLE IF NOT EXISTS bubble_sheets (
 id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL REFERENCES bubble_runs(id), scan_page_id TEXT NOT NULL REFERENCES scan_pages(id),
 reading_json TEXT NOT NULL, id_text TEXT NOT NULL DEFAULT '', name_text TEXT NOT NULL DEFAULT '', match_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL, UNIQUE(run_id,scan_page_id)
);
CREATE TABLE IF NOT EXISTS bubble_reviews (
 id INTEGER PRIMARY KEY, sheet_id TEXT NOT NULL REFERENCES bubble_sheets(id), section_id TEXT, enrollment_id TEXT,
 answers_json TEXT NOT NULL, version TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
CREATE INDEX IF NOT EXISTS bubble_reviews_sheet ON bubble_reviews(sheet_id,id);
CREATE TABLE IF NOT EXISTS bubble_detections (
 scan_page_id TEXT NOT NULL REFERENCES scan_pages(id), detector TEXT NOT NULL, message_id TEXT NOT NULL REFERENCES inbox_messages(id),
 kind TEXT NOT NULL CHECK(kind IN ('sheet','unsure','other','failed')), detail_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(scan_page_id,detector)
);
CREATE INDEX IF NOT EXISTS bubble_detections_message ON bubble_detections(message_id,detector,kind);
CREATE TRIGGER IF NOT EXISTS bubble_detection_immutable BEFORE UPDATE ON bubble_detections BEGIN SELECT RAISE(ABORT,'Machine readings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS bubble_detection_retained BEFORE DELETE ON bubble_detections BEGIN SELECT RAISE(ABORT,'Machine readings are retained'); END;
CREATE TABLE IF NOT EXISTS bubble_matches (
 sheet_id TEXT NOT NULL REFERENCES bubble_sheets(id), matcher TEXT NOT NULL, match_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(sheet_id,matcher)
);
CREATE TRIGGER IF NOT EXISTS bubble_match_immutable BEFORE UPDATE ON bubble_matches BEGIN SELECT RAISE(ABORT,'Machine readings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS bubble_match_retained BEFORE DELETE ON bubble_matches BEGIN SELECT RAISE(ABORT,'Machine readings are retained'); END;
CREATE TABLE IF NOT EXISTS bubble_settings (
 id INTEGER PRIMARY KEY, auto_identify REAL NOT NULL CHECK(auto_identify BETWEEN 0.5 AND 1), actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS bubble_setting_immutable BEFORE UPDATE ON bubble_settings BEGIN SELECT RAISE(ABORT,'Settings are append-only'); END;
CREATE TRIGGER IF NOT EXISTS bubble_setting_retained BEFORE DELETE ON bubble_settings BEGIN SELECT RAISE(ABORT,'Settings are retained'); END;
CREATE TRIGGER IF NOT EXISTS bubble_sheet_immutable BEFORE UPDATE ON bubble_sheets BEGIN SELECT RAISE(ABORT,'Machine readings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS bubble_sheet_retained BEFORE DELETE ON bubble_sheets BEGIN SELECT RAISE(ABORT,'Machine readings are retained'); END;
CREATE TRIGGER IF NOT EXISTS bubble_review_immutable BEFORE UPDATE ON bubble_reviews BEGIN SELECT RAISE(ABORT,'Reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS bubble_review_retained BEFORE DELETE ON bubble_reviews BEGIN SELECT RAISE(ABORT,'Reviews are retained'); END;
"""
# A changed rule gets a new name, and pages are looked at again under it. marks-3 (2026-09-25): where the resemblance's proposal does
# not hold, the positioning marks and the rings settle the page on their own (`reader.settle`). marks-4 (same day): where the
# proposal's rings do not clearly confirm its page, the page whose rings do is recorded (same verdicts as marks-3). marks-5
# (2026-10-24): a page within 3% of Letter is scaled to it first (`reader.letter`); marks-4 looked only at exact Letter rasters, so
# every home-scanned page was `other` unseen. What marks-4 really looked at is carried over as it was (`CARRIED`): the same rule on
# the same Letter raster; only the pages it passed over for their size are looked at again.
DETECTOR = f"{reader.PROFILE}/marks-5"
CARRIED = f"""INSERT OR IGNORE INTO bubble_detections SELECT scan_page_id,'{DETECTOR}',message_id,kind,detail_json,created_at FROM bubble_detections
    WHERE detector='{reader.PROFILE}/marks-4' AND NOT (kind='other' AND json_extract(detail_json,'$.score')=0 AND json_extract(detail_json,'$.ring') IS NULL)"""
OCR_BATCH = 30  # sheets per model load: two handwriting boxes each, inside the OCR runtime's 64-box limit


class BubbleSheetService:
    def __init__(self, scans, seating):
        self.scans, self.seating = scans, seating
        self.inbox, self.rosters, self.path = scans.inbox, scans.rosters, scans.path
        with self.rosters.lock(), connect(self.path) as db:
            db.executescript(SCHEMA)
            db.execute(CARRIED)
            if "question_source" not in {row[1] for row in db.execute("PRAGMA table_info(bubble_runs)")}:
                db.execute("ALTER TABLE bubble_runs ADD COLUMN question_source TEXT NOT NULL DEFAULT 'entered' CHECK(question_source IN ('entered','inferred'))")
        scans.bubble_detector = self  # Page inspection asks which unlabeled pages are bubble sheets before anything else looks at them.

    # ----- recognizing sheets by their positioning marks ------------------------------------

    def detect_message(self, message_id, limit=20):
        """Look once at a bounded set of pages carrying no label: is each a bubble sheet?

        The page type is all this decides. Whose sheets they are stays the
        instructor's explicit choice; nothing is read, matched or identified here.
        """
        with connect(self.path) as db:
            pages = [dict(row) for row in db.execute(
                """SELECT sp.id,sp.raster_artifact_id FROM scan_pages sp WHERE sp.message_id=? AND sp.status='unreadable'
                   AND NOT EXISTS(SELECT 1 FROM bubble_detections b WHERE b.scan_page_id=sp.id AND b.detector=?)
                   ORDER BY sp.source_artifact_id,sp.source_page_index LIMIT ?""", (message_id, DETECTOR, limit))]
        found = []
        for page in pages:
            try:
                raster = reader.decode(self.inbox.read_artifact(page["raster_artifact_id"])[1])
                result = reader.detect(raster)
                result = {**result, "score": float(result["score"]), "markers": int(result["markers"])}
            except Exception:
                result = {"kind": "failed"}  # Recorded, so one unreadable image cannot hold the worker on this scan forever.
            found.append((page["id"], result))
        counts = {kind: sum(1 for _, result in found if result["kind"] == kind) for kind in ("sheet", "unsure", "other", "failed")}
        if found:
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                when = timestamp()
                db.executemany("INSERT OR IGNORE INTO bubble_detections VALUES(?,?,?,?,?,?)",
                               [(page_id, DETECTOR, message_id, result["kind"], encoded(result), when) for page_id, result in found])
                if counts["sheet"] or counts["unsure"]:
                    job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
                    self.inbox.event(db, job["id"], "bubble_sheets_recognized", encoded({
                        "template": reader.template()["label"], "method": "corner positioning marks", "pages_checked": len(found), "sheets": counts["sheet"],
                        "unsure": counts["unsure"], "associated": False, "instructor_review_required": True}))
        return counts

    def _detect_next(self):
        """Catch up a bounded set of retained pages. The caller holds the worker lock (PIPELINE)."""
        with connect(self.path) as db:
            row = db.execute(f"""SELECT sp.message_id FROM scan_pages sp JOIN document_decode_jobs d ON d.message_id=sp.message_id
                                WHERE d.status='complete' AND sp.status='unreadable' AND {PRESENT.format('sp.message_id')}
                                AND NOT EXISTS(SELECT 1 FROM bubble_detections b WHERE b.scan_page_id=sp.id AND b.detector=?)
                                ORDER BY sp.created_at,sp.message_id LIMIT 1""", (DETECTOR,)).fetchone()
        if not row:
            return False
        self.detect_message(row[0])
        return True

    def sheet_pages(self, db, message_id):
        """Scan pages recognized as bubble sheets: they carry no printed label or alias to look for."""
        return {row[0] for row in db.execute("SELECT scan_page_id FROM bubble_detections WHERE message_id=? AND detector=? AND kind='sheet'", (message_id, DETECTOR))}

    def _counts(self, db, message_id):
        """How many of the scan's unlabeled pages were checked, and how many are bubble sheets."""
        kinds = {row[0]: row[1] for row in db.execute("SELECT kind,COUNT(*) FROM bubble_detections WHERE message_id=? AND detector=? GROUP BY kind", (message_id, DETECTOR))}
        pending = db.execute("""SELECT COUNT(*) FROM scan_pages sp WHERE sp.message_id=? AND sp.status='unreadable'
                                AND NOT EXISTS(SELECT 1 FROM bubble_detections b WHERE b.scan_page_id=sp.id AND b.detector=?)""", (message_id, DETECTOR)).fetchone()[0]
        return {"template": reader.template()["label"], "sheets": kinds.get("sheet", 0), "unsure": kinds.get("unsure", 0),
                "checked": sum(kinds.values()), "pending": pending}

    def detected(self, db, message_id, memo=None):
        """For `ScanService.recognition`: how many unlabeled pages are bubble sheets, and whether they were given an assignment yet.
        `memo`: the caller's per-read memo of booklet versions (`_sheets`), so one view's scans read each exam's booklets once."""
        counts = self._counts(db, message_id)
        run = db.execute("""SELECT r.id,r.status,a.title FROM bubble_runs r JOIN assignments a ON a.id=r.assignment_id
                            WHERE r.message_id=? AND r.status!='failed' ORDER BY r.created_at DESC,r.id LIMIT 1""", (message_id,)).fetchone()
        read = {"title": run["title"], "status": run["status"], "sections": [row[0] for row in db.execute(
            "SELECT s.number FROM bubble_run_sections x JOIN course_sections s ON s.id=x.section_id WHERE x.run_id=? ORDER BY s.number", (run["id"],))]} if run else None
        if read and read["status"] != "complete":  # How far the worker has got.
            read["done"] = db.execute("SELECT COUNT(*) FROM bubble_sheets WHERE run_id=?", (run["id"],)).fetchone()[0]
        if read and read["status"] == "complete":  # What is left for the instructor on this read, and whose sheets these turned out to be.
            sheets = self._sheets(db, run["id"], memo)
            states = [item["current"]["status"] for item in sheets]
            read["counts"] = {status: states.count(status) for status in ("clean", "needs_review", "reviewed")}
            found = {item["current"]["section_id"] for item in sheets if item["current"]["section_id"]}
            if found:  # The sections follow from the students identified, not from a choice made beforehand.
                read["sections"] = [row[0] for row in db.execute(f"SELECT number FROM course_sections WHERE id IN ({','.join('?' * len(found))}) ORDER BY number", sorted(found))]
        return {**counts, "read": read}

    # ----- requesting -----------------------------------------------------------------------

    def _job(self, db, message_id):
        message = self.inbox.message(db, message_id)
        job = db.execute("SELECT id,action FROM inbox_jobs WHERE message_id=?", (message_id,)).fetchone()
        if not message["authenticated"] or not job or job["action"] != "scan.receive":
            raise ValueError("Bubble sheets are read from an authenticated, retained scan.")
        return job

    def request(self, message_id, data, actor="workspace"):
        if not isinstance(data, dict):
            raise ValueError("The request must be a JSON object.")
        # The count may be left to the sheets: the question most of them have their last mark on (see `reader.infer_question_count`).
        count, inferred = data.get("question_count"), data.get("question_count") in (None, "")
        if not inferred and (type(count) is not int or not 1 <= count <= 100):
            raise ValueError("Enter how many questions the bubble sheet has, between 1 and 100, or leave it empty to infer it from the sheets.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db, message_id)
            decode = db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()
            if not decode or decode["status"] != "complete":
                raise Conflict("Wait for page inspection to finish before reading bubble sheets.")
            # The scan says nothing about whose sheets these are: the instructor names the assignment and sections.
            # Which section a sheet belongs to follows from the student it is matched to, so the instructor names the assignment only:
            # the sheets are matched against every section of that course in its current term (the most recent term when none is
            # current). Naming sections stays possible, for a term that is not the current one.
            chosen = data.get("section_ids")
            if chosen is not None and (not isinstance(chosen, list) or any(not isinstance(section, str) for section in chosen)):
                raise ValueError("Choose the sections for these bubble sheets.")
            if not isinstance(data.get("assignment_id"), str) or not db.execute("SELECT 1 FROM assignments WHERE id=?", (data["assignment_id"],)).fetchone():
                raise ValueError("Choose a saved assignment for these bubble sheets.")
            grading = getattr(self.scans, "grading", None)
            linked = grading.scan_status(db, message_id, fingerprint=False)["plan"] if grading else None
            if linked:
                if data.get("assignment_id") != linked["source_assignment_id"]:
                    raise Conflict("Use this scan's associated assignment, or change its assignment from the scan summary first.")
                if chosen and set(chosen) != set(linked["section_ids"]):
                    raise Conflict("Use the sections already chosen for this scan's assessment.")
                chosen = linked["section_ids"]
            if chosen in (None, []):
                chosen = [row[0] for row in db.execute(
                    """SELECT s.id FROM course_sections s JOIN course_terms t ON t.id=s.term_id JOIN assignments a ON a.definition_id=t.definition_id
                       WHERE a.id=? AND t.deleted_at IS NULL AND t.id=(SELECT n.id FROM course_terms n WHERE n.definition_id=t.definition_id AND n.deleted_at IS NULL
                           AND EXISTS(SELECT 1 FROM course_sections x WHERE x.term_id=n.id) ORDER BY n.is_current DESC,n.year DESC,n.id DESC LIMIT 1)
                       ORDER BY s.number""", (data["assignment_id"],))] or None
                if chosen is None:
                    raise ValueError("This assignment's course has no sections with a roster to match the sheets against.")
            assignment, sections, term_id = self.seating._validate_scope(db, data["assignment_id"], chosen)
            if db.execute("SELECT 1 FROM bubble_runs WHERE message_id=? AND status IN ('pending','running')", (message_id,)).fetchone():
                raise Conflict("This scan's bubble sheets are already being read.")
            run_id, when = str(uuid.uuid4()), timestamp()
            # Until the sheets are read an inferred count is unknown: every row is read (100), and the run takes the inferred count then.
            db.execute("INSERT INTO bubble_runs(id,message_id,assignment_id,term_id,question_count,question_source,profile,status,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'pending',?,?,?)",
                       (run_id, message_id, assignment["id"], term_id, 100 if inferred else count, "inferred" if inferred else "entered", reader.PROFILE, actor, when, when))
            db.executemany("INSERT INTO bubble_run_sections VALUES(?,?)", [(run_id, section["id"]) for section in sections])
            self.inbox.event(db, job["id"], "bubble_sheets_requested", encoded({"actor": actor, "run_id": run_id, "assignment": assignment["title"],
                             "sections": [section["number"] for section in sections], "question_count": count, "instructor_review_required": True}))
        if grading:
            grading.sync_scan(message_id)
        self.inbox.wake.set()
        return self.summary(message_id)

    # ----- reading (Inbox worker) -------------------------------------------------------------

    def process_one(self):
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("UPDATE bubble_runs SET status='failed',error='The worker was interrupted while reading; request the read again.',updated_at=? WHERE status='running'", (timestamp(),))
                run = db.execute("SELECT * FROM bubble_runs WHERE status='pending' ORDER BY created_at,id LIMIT 1").fetchone()
                if run:
                    run = dict(run)
                    db.execute("UPDATE bubble_runs SET status='running',updated_at=? WHERE id=?", (timestamp(), run["id"]))
                    pages = [dict(row) for row in db.execute(
                        """SELECT sp.id,sp.source_page_index,sp.status,sp.raster_artifact_id,f.filename FROM scan_pages sp JOIN inbox_artifacts f ON f.id=sp.source_artifact_id
                           WHERE sp.message_id=? ORDER BY f.filename,sp.source_artifact_id,sp.source_page_index""", (run["message_id"],))]
                    students = list(self._students(db, run).values())
                    labels = self._labels(db, run["message_id"], run["assignment_id"])
                    # BUBBLE-SHEETS / INBOX / HIERARCHY: only present scans of this assignment in this term may supply evidence.
                    others = [row[0] for row in db.execute(f"""SELECT json_extract(b.reading_json,'$.last_marked') FROM bubble_sheets b JOIN bubble_runs r ON r.id=b.run_id
                                  WHERE r.assignment_id=? AND r.term_id=? AND r.message_id!=? AND {PRESENT.format('r.message_id')}
                                    AND r.status='complete' AND json_extract(b.reading_json,'$.aligned')=1 AND {LATEST_READ}""",
                                                            (run["assignment_id"], run["term_id"], run["message_id"]))] if run["question_source"] == "inferred" else []
            if not run:
                # Nothing asked for: bring retained reads under the current matching rule, then catch up on scans that
                # arrived before sheets were recognized.
                return self._rematch_next() or self._detect_next()
            try:
                sheets, summary = self._read(run, pages, students, others, labels)
                error = ""
            except reader.Undecided as undecided:
                sheets, summary, error = [], {}, str(undecided)
            except Exception:
                sheets, summary, error = [], {}, "Reading the bubble sheets failed or exceeded its limits. The scan is unchanged; request the read again."
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                when, carried = timestamp(), 0
                for sheet in sheets:
                    sheet_id = str(uuid.uuid4())
                    db.execute("INSERT INTO bubble_sheets VALUES(?,?,?,?,?,?,?,?)", (sheet_id, run["id"], sheet["scan_page_id"], encoded(sheet["reading"]),
                               sheet["id_text"], sheet["name_text"], encoded(sheet["match"]), when))
                    db.execute("INSERT INTO bubble_matches VALUES(?,?,?,?)", (sheet_id, matching.MATCHER, encoded(sheet["match"]), when))
                    # Reading a scan again must not cost the instructor their work: the latest review of this very page, made on an
                    # earlier read of the same assignment and question count, is carried over (a student only while still on the chosen rosters).
                    earlier = db.execute("""SELECT v.* FROM bubble_reviews v JOIN bubble_sheets b ON b.id=v.sheet_id JOIN bubble_runs r ON r.id=b.run_id
                                            WHERE b.scan_page_id=? AND r.id!=? AND r.assignment_id=? AND r.question_count=? ORDER BY v.id DESC LIMIT 1""",
                                         (sheet["scan_page_id"], run["id"], run["assignment_id"], summary.get("question_count", run["question_count"]))).fetchone()
                    if earlier:
                        kept = earlier["enrollment_id"] and db.execute("SELECT 1 FROM bubble_run_sections WHERE run_id=? AND section_id=?", (run["id"], earlier["section_id"])).fetchone()
                        db.execute("INSERT INTO bubble_reviews(sheet_id,section_id,enrollment_id,answers_json,version,note,actor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                                   (sheet_id, earlier["section_id"] if kept else None, earlier["enrollment_id"] if kept else None, earlier["answers_json"], earlier["version"],
                                    earlier["note"], f"carried over from {earlier['actor']}'s review of {earlier['created_at'][:10]}", when))
                        carried += 1
                db.execute("UPDATE bubble_runs SET status=?,error=?,summary_json=?,question_count=?,updated_at=? WHERE id=?",
                           ("failed" if error else "complete", error, encoded(summary), summary.get("question_count", run["question_count"]), when, run["id"]))
                job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (run["message_id"],)).fetchone()
                self.inbox.event(db, job["id"], "bubble_sheets_read", error or encoded({"run_id": run["id"], **summary, "reviews_carried_over": carried, "graded": False, "instructor_review_required": True}))
            return True

    def _rematch_next(self):
        """One retained read whose sheets were matched under an earlier rule: match them again from the text already read.

        No page is read again and no review is touched. A machine reading stays
        immutable: the new match is appended under the current `MATCHER`, and
        `_sheets` prefers it. The caller holds the worker lock.
        """
        with self.rosters.lock(), connect(self.path) as db:
            run = db.execute("""SELECT r.* FROM bubble_runs r WHERE r.status='complete' AND EXISTS(SELECT 1 FROM bubble_sheets b WHERE b.run_id=r.id AND NOT EXISTS(
                                    SELECT 1 FROM bubble_matches m WHERE m.sheet_id=b.id AND m.matcher=?)) ORDER BY r.created_at,r.id LIMIT 1""", (matching.MATCHER,)).fetchone()
            if not run:
                return False
            students = list(self._students(db, dict(run)).values())
            sheets = [dict(row) for row in db.execute("""SELECT b.id,b.scan_page_id,b.reading_json,b.id_text,b.name_text,b.match_json FROM bubble_sheets b WHERE b.run_id=? AND NOT EXISTS(
                                                             SELECT 1 FROM bubble_matches m WHERE m.sheet_id=b.id AND m.matcher=?)""", (run["id"], matching.MATCHER))]
            labels = self._labels(db, run["message_id"], run["assignment_id"])
        # Nothing was read from a sheet that could not be aligned or when OCR was unavailable: its recorded match, and its reason, stand.
        # A Scantron label names the student even then.
        matches = [(sheet["id"], encoded(matching.match(sheet["id_text"], sheet["name_text"], students, labels.get(sheet["scan_page_id"])))
                    if labels.get(sheet["scan_page_id"]) or (json.loads(sheet["reading_json"]).get("aligned") and (sheet["id_text"] or sheet["name_text"]))
                    else sheet["match_json"]) for sheet in sheets]
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            when = timestamp()
            db.executemany("INSERT OR IGNORE INTO bubble_matches VALUES(?,?,?,?)", [(sheet_id, matching.MATCHER, match, when) for sheet_id, match in matches])
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (run["message_id"],)).fetchone()
            self.inbox.event(db, job["id"], "bubble_sheets_rematched", encoded({"run_id": run["id"], "matcher": matching.MATCHER, "sheets": len(matches),
                             "pages_read_again": 0, "reviews_changed": 0, "instructor_review_required": True}))
        return True

    def _students(self, db, run):
        sections = [dict(row) for row in db.execute("SELECT s.id,s.number FROM bubble_run_sections x JOIN course_sections s ON s.id=x.section_id WHERE x.run_id=? ORDER BY s.number", (run["id"],))]
        return self.seating._students(db, run["term_id"], sections)

    @staticmethod
    def _labels(db, message_id, assignment_id):
        """{scan_page_id: enrollment_id} for this scan's pages carrying a Scantron label (DOCUMENTS "SCANTRON LABELS") printed
        for a named copy of this assignment in a ready, current print job. Scan intake keeps such a page unlabeled, and its
        decoded payload in `candidates_json`."""
        from document_identity.service import DocumentService
        found = {}
        for row in db.execute("SELECT id,candidates_json FROM scan_pages WHERE message_id=? AND status='unreadable' AND candidates_json!='[]'", (message_id,)):
            payloads = {item.get("payload") for item in json.loads(row["candidates_json"]) if str(item.get("payload", "")).startswith("T")}
            if len(payloads) != 1:
                continue
            label = DocumentService.sheet_label(db, payloads.pop())
            if label and label["enrollment_id"] and label["assignment_id"] == assignment_id and label["batch_status"] == "ready" and not label["superseded_at"]:
                found[row["id"]] = label["enrollment_id"]
        return found

    def _read(self, run, pages, students, other_last_marks=(), labels=None):
        found, inferring = [], run["question_source"] == "inferred"
        for page in pages:
            if page["status"] == "blank":
                continue
            # At Letter size, so the handwriting crops below come from the raster the reading measured (`reader.letter`).
            raster = reader.letter(reader.decode(self.inbox.read_artifact(page["raster_artifact_id"])[1]), run["profile"])
            if raster.shape != tuple(reversed(reader.template(run["profile"])["pixels"])):
                continue
            reading = reader.read_page(raster, 100 if inferring else run["question_count"], run["profile"])
            if reading["kind"] != "other":
                found.append({"scan_page_id": page["id"], "reading": reading, "raster": raster, "id_text": "", "name_text": "", "ocr_read": False})
        readable = [sheet for sheet in found if sheet["reading"].get("aligned")]
        question_count, agreement = run["question_count"], None
        if inferring:
            marks = [sheet["reading"]["last_marked"] for sheet in readable]
            if sum(1 for mark in marks if mark) < 10:
                marks += list(other_last_marks)
            question_count, agreement = reader.infer_question_count(marks)
            for sheet in found:
                sheet["reading"] = reader.trim(sheet["reading"], question_count)
        run = {**run, "question_count": question_count}
        ocr_note = ""
        try:
            from faculty_ocr import OcrError, available, recognize_fields
            if not available():
                ocr_note = "Local OCR is not installed, so no ID or name was read."
        except Exception:
            ocr_note = "Local OCR is not installed, so no ID or name was read."
        for start in range(0, len(readable) if not ocr_note else 0, OCR_BATCH):
            batch, crops, boxes, top = readable[start:start + OCR_BATCH], [], [], 0
            for sheet in batch:
                for field in ("id", "name"):
                    crop = reader.field_image(sheet["raster"], sheet["reading"]["rotation"], sheet["reading"]["fields"][field])
                    crops.append(crop)
                    boxes.append([0, top, crop.width, top + crop.height])
                    top += crop.height + 8
            composite = Image.new("L", (max(crop.width for crop in crops), top), 255)
            for crop, box in zip(crops, boxes):
                composite.paste(crop, (0, box[1]))
            buffer = io.BytesIO()
            composite.save(buffer, "PNG")
            try:
                reads = recognize_fields(buffer.getvalue(), boxes)["reads"]
            except (OcrError, ValueError):
                ocr_note = "Local OCR could not read the handwriting boxes; match these sheets by hand."
                break
            for index, sheet in enumerate(batch):
                # OCR output is untrusted draft text: bounded here, compared as data, escaped wherever shown.
                sheet["id_text"], sheet["name_text"] = reads[2 * index]["text"][:120], reads[2 * index + 1]["text"][:120]
                sheet["ocr_read"] = True
        labels = labels or {}
        for sheet in found:
            label = labels.get(sheet["scan_page_id"])
            sheet["match"] = matching.match(sheet["id_text"], sheet["name_text"], students, label) if sheet["reading"].get("aligned") or label else \
                {"state": "unmatched", "method": None, "enrollment_id": None, "section_id": None, "confidence": 0.0, "candidates": [], "notes": []}
            # Only a sheet no successful OCR call covered (requirement, 2026-11-06): one a batch before the failing one read keeps its match.
            if ocr_note and sheet["reading"].get("aligned") and not sheet["ocr_read"]:
                sheet["match"]["notes"] = [ocr_note]  # The reason nothing was read matters more than the fact that it was not.
            sheet.pop("raster")
            sheet.pop("ocr_read")
        states = [sheet["match"]["state"] for sheet in found]
        last = [sheet["reading"]["last_marked"] for sheet in found if sheet["reading"].get("aligned")]
        return found, {"pages": len(pages), "sheets": len(found), "matched": states.count("matched"), "suggested": states.count("suggested"),
                       "unmatched": states.count("unmatched"), "mark_review": sum(1 for sheet in found if sheet["reading"]["review"]),
                       "usual_last_question": max(set(last), key=last.count) if last else None, "students": len(students),
                       "question_count": question_count, "question_agreement": agreement}

    # ----- current values -------------------------------------------------------------------

    # ----- the instructor's setting -----------------------------------------------------------

    @staticmethod
    def auto_identify(db):
        """The confidence at which a suggested student is identified without asking: the instructor's latest choice, 70% until one is made."""
        row = db.execute("SELECT auto_identify FROM bubble_settings ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else matching.AUTO_IDENTIFY

    def set_auto_identify(self, value, actor="workspace"):
        """Workspace-wide and effective at once: identification is derived from the retained match each time it is shown."""
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not matching.LOWEST_THRESHOLD <= value <= 1:
            raise ValueError(f"Choose a confidence between {round(matching.LOWEST_THRESHOLD * 100)}% and 100%.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if abs(self.auto_identify(db) - value) > 1e-9:
                db.execute("INSERT INTO bubble_settings(auto_identify,actor,created_at) VALUES(?,?,?)", (round(float(value), 2), actor, timestamp()))
            return {"auto_identify": self.auto_identify(db)}

    @staticmethod
    def _current(sheet, review, context):
        """The sheet's present value: the latest review if any, else what the machine read and whom it identified.

        `identified_by` says how the student was named: `review`, `label` (the sheet's Scantron label), `uin` (exact),
        or `confidence` (a suggestion at the instructor's threshold or more,
        identified without asking). `context` is what the other sheets say:
        `yields` when a stronger claim elsewhere already holds this sheet's
        student, `duplicate` when an equal claim does, and `taken`, the students
        other sheets of the assignment are already recorded for.
        """
        reading, match, threshold = sheet["reading"], sheet["match"], context["threshold"]
        machine = [item["answer"] if item["state"] in ("single", "multiple") else "" for item in reading.get("answers", [])]
        if sheet.get("repeat_of") and not review:
            # The same sheet scanned twice (`_repeats`): it needs nothing, is nobody's, and is not scored; the first scan's sheet is.
            return {"enrollment_id": None, "section_id": None, "answers": machine, "version": "", "reviewed_at": None, "note": "",
                    "identified_by": None, "repeat_of": sheet["repeat_of"], "reasons": [], "status": "repeat",
                    "flags": {"identity": False, "version": False, "questions": [], "unread": False}}
        if review:
            value = {"enrollment_id": review["enrollment_id"], "section_id": review["section_id"], "answers": json.loads(review["answers_json"]),
                     "version": review["version"], "reviewed_at": review["created_at"], "note": review["note"],
                     "identified_by": "review" if review["enrollment_id"] else None}
            reasons, sure = [], bool(review["enrollment_id"])
        else:
            sure = matching.identified(match, threshold) and not context["yields"]
            value = {"enrollment_id": match["enrollment_id"] if sure else None, "section_id": match["section_id"] if sure else None, "answers": machine,
                     "version": reading.get("version", {}).get("answer", "") if reading.get("version", {}).get("state") == "single" else "", "reviewed_at": None, "note": "",
                     "identified_by": ("label" if match["state"] == "matched" and match["method"] == "label" else "uin" if match["state"] == "matched" else "confidence") if sure else None}
            # What the page as a whole was unsure about is kept from the reading; what each question needs is decided afresh from the
            # retained mark strengths, so a rule that changes reaches readings made before it. Blanks and marks past the last question
            # are nobody's concern, and clear rings under the positioning marks settle a middling page resemblance.
            reasons = [reason for reason in reading["review"] if not reason.startswith(("Question ", "Marks beyond question"))]
            if reading.get("aligned") and reading.get("alignment", {}).get("ring", 0) >= reader.RING_SURE:
                reasons = [reason for reason in reasons if not reason.startswith("This page only partly resembles the bubble sheet")]
            reasons += [reason for reason in map(reader.question_reason, reading.get("answers", [])) if reason]
            # A sheet its Scantron label names takes its student's version from the seating chart, as that student's booklet does
            # (requirement, 2026-10-24: 94 of 109 Exam 2 sheets had no version bubbled); a letter bubbled against the chart is asked.
            # THE BOOKLET'S VERSION (requirement, 2026-10-25): where the student's exam booklet has a settled version (page 1 read, or
            # the instructor's decision), that is the student's version, not the seat's (Exam 2: 68 of 312 booklets differed).
            member = (match["section_id"], match["enrollment_id"])
            booklet = context.get("booklets", {}).get(member) if value["identified_by"] == "label" else None
            seat = {booklet} if booklet else context.get("seats", {}).get(member, ()) if value["identified_by"] == "label" else ()
            if len(seat) == 1:
                (seat,) = seat
                bubbled = reading.get("version", {})
                if bubbled.get("state") in ("single", "multiple") and seat not in bubbled.get("answer", ""):
                    reasons.append(f"Version {' and '.join(bubbled['answer'])} is bubbled, but " + (f"this student's exam booklet is Version {seat}." if booklet
                                   else f"the seating chart gave this student Version {seat}."))
                    context = {**context, "version_conflict": True}
                elif bubbled.get("state") != "single" and reading.get("aligned"):
                    value.update(version=seat, version_from="booklet" if booklet else "seating_chart")
                    reasons = [reason for reason in reasons if reason not in BubbleSheetService.VERSION_REASONS and not reason.endswith(" version bubbles are filled.")]
            taken = match["enrollment_id"] in context["taken"] if match["enrollment_id"] else False
            if sure:
                pass
            elif taken:
                # A student already recorded for another sheet of this assignment is not offered again (the instructor's rule).
                reasons.append("The closest student on the roster is already matched to another sheet of this assignment.")
            elif match["state"] == "suggested":
                reasons.append(f"The student is a suggestion ({round(match['confidence'] * 100)}% from the {'name' if match['method'] == 'name' else 'digits' if match['method'] == 'uin' else 'digits and name'} read), not an exact UIN match." if not match["notes"] else match["notes"][0])
            elif match["state"] in ("matched", "unmatched"):
                reasons.append(match["notes"][0] if match["notes"] else "No student could be matched from the ID and name read.")
        if context["duplicate"] and value["enrollment_id"]:
            reasons.append("Another sheet in this scan is recorded for the same student." if context["duplicate"] == "scan"
                           else "A sheet in another scan of this assignment is recorded for the same student.")
        # Exactly which parts wait for the instructor, so the review can open those and keep confident reads out of the way.
        unread = not review and not reading.get("aligned")
        value["flags"] = {"identity": bool(context["duplicate"] and value["enrollment_id"]) or (not review and not sure),
                          "version": unread or (not review and (reading.get("version", {}).get("state") != "single" and not value.get("version_from")
                                                                or bool(context.get("version_conflict")))),
                          "questions": [] if review else [item["question"] for item in reading.get("answers", []) if item["state"] == "unsure"],
                          "unread": unread}
        value["reasons"] = reasons
        value["status"] = "needs_review" if reasons else "reviewed" if review else "clean"
        return value

    VERSION_REASONS = ("No version bubble is filled.", "The version bubble is faint or erased.")

    @staticmethod
    def _claim(item, threshold):
        """Whom a sheet is recorded for and how firmly: the instructor's review, an exact UIN, or a confident match."""
        if item["review"]:
            return (item["review"]["enrollment_id"], 3) if item["review"]["enrollment_id"] else (None, 0)
        if item.get("repeat_of"):
            return None, 0
        if matching.identified(item["match"], threshold):
            return item["match"]["enrollment_id"], 2 if item["match"]["state"] == "matched" else 1
        return None, 0

    def _load(self, db, run_id):
        # Each sheet's latest review is named in the same statement as the sheets; reviews are immutable and retained, so reading
        # those rows by id afterwards gives exactly what that snapshot held (one read per run instead of one per sheet).
        rows = db.execute("""SELECT b.*,m.match_json current_match_json,sp.source_page_index,sp.raster_artifact_id,sp.page_artifact_id,
                                    (SELECT MAX(v.id) FROM bubble_reviews v WHERE v.sheet_id=b.id) latest_review_id
                             FROM bubble_sheets b JOIN scan_pages sp ON sp.id=b.scan_page_id LEFT JOIN bubble_matches m ON m.sheet_id=b.id AND m.matcher=?
                             WHERE b.run_id=? ORDER BY sp.source_artifact_id,sp.source_page_index""", (matching.MATCHER, run_id)).fetchall()
        wanted = [row["latest_review_id"] for row in rows if row["latest_review_id"] is not None]
        reviews = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            reviews.update((row["id"], row) for row in db.execute(f"SELECT * FROM bubble_reviews WHERE id IN ({','.join('?' * len(chunk))})", chunk))
        sheets = []
        for row in rows:
            item = dict(row)
            latest = item.pop("latest_review_id")
            # The match under the current rule when there is one; the match recorded at the read until the worker has caught up.
            item["reading"], item["match"] = json.loads(item.pop("reading_json")), json.loads(item.pop("current_match_json") or item["match_json"])
            item.pop("match_json")
            # Each question is decided from its retained mark strengths by today's rule; the strengths themselves never change.
            for answer in item["reading"].get("answers", []):
                if "marks" in answer:
                    answer["state"], answer["answer"], answer["unsure"] = reader.decide([answer["marks"][letter] for letter in "ABCDE"])
            item["review"] = reviews.get(latest)
            sheets.append(item)
        return sheets

    def _claims(self, db, run_id, threshold, repeats=frozenset()):
        """`_claim` of each sheet of another run: the same sheets as `_load`, reading only what a claim looks at (the latest
        review's student and the match), not the readings. A sheet that `repeats` an earlier one claims nobody."""
        return [self._claim({"review": {"enrollment_id": row["enrollment_id"]} if row["review_id"] is not None else None,
                             "repeat_of": row["id"] in repeats, "match": json.loads(row["current_match_json"] or row["match_json"])}, threshold)
                for row in db.execute("""SELECT b.id,b.match_json,m.match_json current_match_json,v.id review_id,v.enrollment_id
                                         FROM bubble_sheets b JOIN scan_pages sp ON sp.id=b.scan_page_id LEFT JOIN bubble_matches m ON m.sheet_id=b.id AND m.matcher=?
                                         LEFT JOIN bubble_reviews v ON v.id=(SELECT MAX(x.id) FROM bubble_reviews x WHERE x.sheet_id=b.id)
                                         WHERE b.run_id=?""", (matching.MATCHER, run_id))]

    @staticmethod
    def _repeats(db, assignment_id):
        """{sheet_id: {"message_id", "page"}} for each sheet that repeats an earlier one: the same sheet scanned twice (instructor,
        2026-10-24, "automatic": Tessa Pearson's and Hana Blakely's papers went through the home scanner in two stacks). A Scantron
        label is one sticker, so two sheets carrying it are one sheet unless their marks differ: same label (or a sheet the
        instructor reviewed as that label's student, or one whose label was not read and whose handwriting points to them), the same reading of every question, in the latest complete reads of scans
        still in the Inbox, and not two clear, different version letters
        (requirement, 2026-10-25). A sheet the instructor reviewed keeps it, else
        the first scan received (then the earlier page); a reviewed sheet is theirs, never a repeat."""
        # Whose sheet it is: the instructor's review when it names a student, else the student its Scantron label was printed for. A
        # sheet whose label was not read but which the instructor reviewed as that student is the same sheet too (instructor,
        # 2026-10-25: "I reviewed this one, why is it still saying it needs review"; Eva Marsh's rescan had no label read).
        # With neither, the student the ID and name read point to (matched or suggested), as long as another sheet of that student is
        # held by a label or a review: Lyle Ensor's sheet, its label unread, was suggested from his handwriting while his labeled
        # rescan held him, and their marks were identical. A labeled copy is kept before one named only by its handwriting.
        rows = []
        for row in db.execute(f"""SELECT * FROM (SELECT b.id,r.message_id,sp.source_page_index page,m.received_at,v.id IS NOT NULL reviewed,
                    (SELECT c.enrollment_id FROM json_each(CASE WHEN json_valid(sp.candidates_json) THEN sp.candidates_json ELSE '[]' END) j
                        JOIN sheet_labels l ON l.token=json_extract(j.value,'$.payload') JOIN printed_copies c ON c.id=l.printed_copy_id LIMIT 1) labeled,
                    v.enrollment_id reviewed_for,
                    CASE WHEN json_extract(COALESCE(mm.match_json,b.match_json),'$.state') IN ('matched','suggested')
                         THEN json_extract(COALESCE(mm.match_json,b.match_json),'$.enrollment_id') END read_as
                FROM bubble_runs r JOIN inbox_messages m ON m.id=r.message_id JOIN bubble_sheets b ON b.run_id=r.id
                JOIN scan_pages sp ON sp.id=b.scan_page_id
                LEFT JOIN bubble_reviews v ON v.id=(SELECT MAX(x.id) FROM bubble_reviews x WHERE x.sheet_id=b.id)
                LEFT JOIN bubble_matches mm ON mm.sheet_id=b.id AND mm.matcher=?
                WHERE r.assignment_id=? AND r.status='complete' AND {PRESENT.format('r.message_id')}
                  AND {LATEST_READ})""",
                (matching.MATCHER, assignment_id)):
            row = dict(row)
            held = row["reviewed_for"] or row["labeled"]
            row.update(student=held or (None if row["reviewed"] else row["read_as"]), held=bool(held))
            if row["student"]:
                rows.append(row)
        firm = {row["student"] for row in rows if row["held"]}
        rows = [row for row in rows if row["held"] or row["student"] in firm]
        by_token = {}
        for row in rows:
            by_token.setdefault(row["student"], []).append(row)
        twice = [row for group in by_token.values() if len(group) > 1 for row in group]
        if not twice:
            return {}
        readings = {row[0]: json.loads(row[1]) for row in db.execute(
            f"SELECT id,reading_json FROM bubble_sheets WHERE id IN ({','.join('?' * len(twice))})", [row["id"] for row in twice])}

        def marks(sheet_id):
            reading = readings[sheet_id]
            answers = [reader.decide([answer["marks"][letter] for letter in "ABCDE"])[:2] if "marks" in answer else (answer["state"], answer["answer"])
                       for answer in reading.get("answers", [])]
            version = reading.get("version", {})
            return bool(reading.get("aligned")), tuple(map(tuple, answers)), version.get("answer") if version.get("state") == "single" else None

        def same(one, other):
            # The version bubble is not the student's version (the booklet's is, THE BOOKLET'S VERSION), and a faint one reads blank
            # in one scan and unsure in the next: only two clear, different letters tell two sheets apart.
            return one[:2] == other[:2] and (None in (one[2], other[2]) or one[2] == other[2])
        def agrees(sheet_id, reviewed_id):
            # The instructor's reviewed answers stand for that sheet: a copy reading every question as they decided it, or in doubt
            # where it does not, is the same sheet.
            decided = json.loads(db.execute("SELECT answers_json FROM bubble_reviews WHERE sheet_id=? ORDER BY id DESC LIMIT 1", (reviewed_id,)).fetchone()[0])
            aligned, answers, _ = marks(sheet_id)
            return aligned and all(state == "unsure" or answer == chosen for (state, answer), chosen in zip(answers, decided))
        found = {}
        for group in by_token.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda row: (not row["reviewed"], not row["held"], row["received_at"], row["page"], row["id"]))
            first = group[0]
            for row in group[1:]:
                if not row["reviewed"] and marks(row["id"])[0] and (same(marks(row["id"]), marks(first["id"])) or first["reviewed"] and row["held"] and agrees(row["id"], first["id"])):
                    found[row["id"]] = {"message_id": first["message_id"], "page": first["page"]}
        return found

    def _sheets(self, db, run_id, memo=None):
        """The run's sheets with their present values, each seen in the light of the others.

        A student is recorded for one sheet per assignment. A confident match
        yields to a sheet that holds the same student by exact UIN or by the
        instructor's review, here or in another scan of the assignment, and the
        student is then not offered again. Equal claims are nobody's to settle
        but the instructor's: both sheets are flagged.
        """
        sheets, threshold = self._load(db, run_id), self.auto_identify(db)
        run = db.execute("SELECT assignment_id,message_id,term_id FROM bubble_runs WHERE id=?", (run_id,)).fetchone()
        # `memo`: one view's per-read memo, so its scans share each exam's booklets, repeats and every other run's claims (PERFORMANCE).
        memo = memo if memo is not None else {}
        if ("repeats", run["assignment_id"]) not in memo:
            memo[("repeats", run["assignment_id"])] = self._repeats(db, run["assignment_id"])
        repeats = memo[("repeats", run["assignment_id"])]
        for item in sheets:
            if item["id"] in repeats:
                item["repeat_of"] = repeats[item["id"]]
        seats = {}  # {(section, student): the versions the seating charts of this exam and term gave them}
        for row in db.execute("""SELECT a.section_id,a.enrollment_id,v.exam_version FROM seating_charts c JOIN seating_assignments a ON a.chart_id=c.id
                                 JOIN seating_exam_versions v ON v.chart_id=c.id AND v.seat_id=a.seat_id WHERE c.assignment_id=? AND c.term_id=?""",
                              (run["assignment_id"], run["term_id"])):
            seats.setdefault((row[0], row[1]), set()).add(row[2])
        # THE BOOKLET'S VERSION: each student's settled booklet version, read once for every scan of a view (`memo`).
        hook = getattr(self, "booklet_versions", None)
        exam = (run["assignment_id"], run["term_id"])
        if exam not in memo:
            memo[exam] = hook(db, *exam) if hook else {}
        booklets = memo[exam]
        elsewhere = {}
        for other in db.execute(f"""SELECT r.id FROM bubble_runs r WHERE r.assignment_id=? AND r.message_id!=? AND r.status='complete' AND {PRESENT.format('r.message_id')} AND {LATEST_READ}""",
                                (run["assignment_id"], run["message_id"])).fetchall():
            if ("claims", other["id"]) not in memo:
                memo[("claims", other["id"])] = self._claims(db, other["id"], threshold, repeats)
            for student, strength in memo[("claims", other["id"])]:
                if student:
                    elsewhere[student] = max(elsewhere.get(student, 0), strength)
        claims = [self._claim(item, threshold) for item in sheets]
        for index, item in enumerate(sheets):
            student, strength = claims[index]
            here = [other for position, (who, other) in enumerate(claims) if student and who == student and position != index]
            there = elsewhere.get(student, 0) if student else 0
            firm = 2  # an exact UIN or the instructor's review; a confident match (1) gives way to either
            yields = strength == 1 and max(here + [there]) >= firm
            rivals_here, rivals_there = [other for other in here if other >= min(strength, firm)], there >= min(strength, firm)
            duplicate = None if not student or yields else "scan" if rivals_here else "assignment" if rivals_there else None
            context = {"threshold": threshold, "yields": yields, "duplicate": duplicate, "seats": seats, "booklets": booklets,
                       "taken": {who for position, (who, other) in enumerate(claims) if who and position != index} | set(elsewhere)}
            item["taken"] = context["taken"]
            item["current"] = self._current(item, item["review"], context)
        return sheets

    def summary(self, message_id):
        """What Scanned page review shows: the form's choices, and the latest read with one line per sheet."""
        with self.rosters.lock(), connect(self.path) as db:
            self.inbox.message(db, message_id)
            row = db.execute("SELECT * FROM bubble_runs WHERE message_id=? ORDER BY created_at DESC,id LIMIT 1", (message_id,)).fetchone()
            run = None
            if row:
                run = {**dict(row), "summary": json.loads(row["summary_json"])}
                run.pop("summary_json")
                assignment = db.execute("SELECT a.title,d.code FROM assignments a JOIN course_definitions d ON d.id=a.definition_id WHERE a.id=?", (row["assignment_id"],)).fetchone()
                run.update(title=assignment["title"], code=assignment["code"],
                           sections=[r[0] for r in db.execute("SELECT s.number FROM bubble_run_sections x JOIN course_sections s ON s.id=x.section_id WHERE x.run_id=? ORDER BY s.number", (row["id"],))])
                names = {key: value["name"] for key, value in self._students(db, run).items()} if row["status"] == "complete" else {}
                run["sheets"] = [{"id": item["id"], "page": item["source_page_index"], "status": item["current"]["status"], "reasons": item["current"]["reasons"],
                                  "student": names.get(item["current"]["enrollment_id"], ""), "match": item["match"]["state"], "confidence": item["match"]["confidence"],
                                  "identified_by": item["current"]["identified_by"],
                                  "version": item["current"]["version"], "answers": "".join(answer if len(answer) == 1 else "·" if not answer else f"({answer})" for answer in item["current"]["answers"])}
                                 for item in self._sheets(db, row["id"])] if row["status"] == "complete" else []
                run["counts"] = {status: sum(1 for sheet in run["sheets"] if sheet["status"] == status) for status in ("clean", "needs_review", "reviewed")}
                # How each sheet's student is known right now (reviews included), for the summary line.
                run["identity"] = {key: sum(1 for sheet in run["sheets"] if (sheet["identified_by"] or "none") == key) for key in ("uin", "confidence", "review", "none")}
                labeled = sum(1 for sheet in run["sheets"] if sheet["identified_by"] == "label")
                if labeled:  # PERFORMANCE: reads of scans without Scantron labels stay byte-identical.
                    run["identity"]["label"] = labeled
                run["auto_identify"] = self.auto_identify(db)
            decoded = db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message_id,)).fetchone()
            detected = self._counts(db, message_id)  # the read itself is shown above, so `detected`'s account of it is not needed
            grading_service = getattr(self.scans, "grading", None)
            grading = grading_service.scan_status(db, message_id, fingerprint=False)["plan"] if grading_service else None
            # Only a starting point for the form: the assignment whose labels were scanned most recently. The instructor still chooses.
            recent = db.execute(f"""SELECT v.assignment_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id JOIN printed_copies c ON c.id=p.printed_copy_id
                                   JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                                   WHERE {PRESENT.format('sp.message_id')} ORDER BY sp.created_at DESC,sp.id LIMIT 1""").fetchone()
        catalog = self.seating.catalog(rooms=False)  # only its assignments and sections are shown
        return {"message_id": message_id, "run": run, "suggested_assignment_id": recent[0] if recent else None, "detected": {key: detected[key] for key in ("sheets", "unsure", "checked", "pending")}, "can_request": bool(decoded and decoded["status"] == "complete") and not (run and run["status"] in ("pending", "running")),
                "grading": grading, "assignments": catalog["assignments"], "sections": catalog["sections"], "template": reader.template()["label"]}

    def sheet(self, sheet_id):
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("SELECT run_id FROM bubble_sheets WHERE id=?", (sheet_id,)).fetchone()
            if not row:
                raise NotFound("That bubble sheet reading was not found.")
            run = dict(db.execute("SELECT * FROM bubble_runs WHERE id=?", (row["run_id"],)).fetchone())
            item = next(sheet for sheet in self._sheets(db, run["id"]) if sheet["id"] == sheet_id)
            students = sorted(self._students(db, run).values(), key=lambda student: student["name"].casefold())
        item.pop("review")
        taken = item.pop("taken")
        # A student already recorded for another sheet of this assignment is not offered: marked here so the picker can set them apart.
        for candidate in item["match"].get("candidates", []):
            candidate["taken"] = candidate["enrollment_id"] in taken
        return {**item, "question_count": run["question_count"], "message_id": run["message_id"], "targets": reader.choice_targets(run["profile"]),
                "students": [{**{key: student[key] for key in ("enrollment_id", "section_id", "section_number", "name")}, "taken": student["enrollment_id"] in taken} for student in students]}

    def search_students(self, sheet_id, data):
        """Students of this sheet's read whose UIN is closest to digits typed into its student picker; names and sections only."""
        typed = data.get("uin") if isinstance(data, dict) else None
        if not isinstance(typed, str) or len(typed) > 40:
            raise ValueError("Type part of a UIN.")
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("SELECT run_id FROM bubble_sheets WHERE id=?", (sheet_id,)).fetchone()
            if not row:
                raise NotFound("That bubble sheet reading was not found.")
            run = dict(db.execute("SELECT * FROM bubble_runs WHERE id=?", (row["run_id"],)).fetchone())
            students = self._students(db, run)
        return {"students": matching.uin_search(typed, list(students.values()))}

    def part(self, sheet_id, part):
        """A PNG of the place on the scan a question is about, read through the verified Inbox storage manager."""
        if not isinstance(part, str) or not (part in ("id", "name", "version") or re.fullmatch(r"q(?:[1-9]|[1-9][0-9]|100)", part)):
            raise ValueError("Choose the ID, the name, the version or a question of this sheet.")
        with connect(self.path) as db:
            row = db.execute("SELECT sp.raster_artifact_id,r.profile FROM bubble_sheets b JOIN scan_pages sp ON sp.id=b.scan_page_id JOIN bubble_runs r ON r.id=b.run_id WHERE b.id=?", (sheet_id,)).fetchone()
        if not row:
            raise NotFound("That bubble sheet reading was not found.")
        raster = reader.decode(self.inbox.read_artifact(row["raster_artifact_id"])[1])
        buffer = io.BytesIO()
        reader.part_image(raster, part, row["profile"]).save(buffer, "PNG")
        return buffer.getvalue()

    # ----- review and export ----------------------------------------------------------------

    def review(self, sheet_id, data, actor="workspace"):
        if not isinstance(data, dict):
            raise ValueError("The request must be a JSON object.")
        answers, version, note = data.get("answers"), data.get("version", ""), data.get("note", "")
        valid = lambda text, limit: isinstance(text, str) and len(text) <= limit and text == "".join(sorted(set(text))) and set(text) <= set("ABCDE")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("Notes must be 500 characters or fewer.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            sheet = db.execute("SELECT b.id,b.run_id,r.question_count,r.message_id FROM bubble_sheets b JOIN bubble_runs r ON r.id=b.run_id WHERE b.id=?", (sheet_id,)).fetchone()
            if not sheet:
                raise NotFound("That bubble sheet reading was not found.")
            if not isinstance(answers, list) or len(answers) != sheet["question_count"] or not all(valid(answer, 5) for answer in answers) or not valid(version, 1):
                raise ValueError(f"Give {sheet['question_count']} answers using the letters A to E (empty for blank), and a version letter or none.")
            section_id, enrollment_id = data.get("section_id") or None, data.get("enrollment_id") or None
            if (section_id is None) != (enrollment_id is None):
                raise ValueError("Choose a student from the roster, or none.")
            if enrollment_id and not db.execute("""SELECT 1 FROM roster_memberships m JOIN bubble_run_sections x ON x.section_id=m.section_id
                                                  WHERE x.run_id=? AND m.section_id=? AND m.enrollment_id=?""", (sheet["run_id"], section_id, enrollment_id)).fetchone():
                raise ValueError("That student is not on the roster of the sections chosen for this read.")
            db.execute("INSERT INTO bubble_reviews(sheet_id,section_id,enrollment_id,answers_json,version,note,actor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (sheet_id, section_id, enrollment_id, encoded(answers), version, note.strip(), actor, timestamp()))
            job = db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (sheet["message_id"],)).fetchone()
            self.inbox.event(db, job["id"], "bubble_sheet_reviewed", encoded({"actor": actor, "sheet_id": sheet_id, "student_recorded": bool(enrollment_id)}))
        return self.sheet(sheet_id)

    def export_csv(self, run_id):
        """The instructor's record, one row per sheet, for comparison with Gradescope's responses export."""
        with self.rosters.lock(), connect(self.path) as db:
            run = db.execute("SELECT r.*,a.title FROM bubble_runs r JOIN assignments a ON a.id=r.assignment_id WHERE r.id=? AND r.status='complete'", (run_id,)).fetchone()
            if not run:
                raise NotFound("That bubble sheet read was not found.")
            students, sheets = self._students(db, dict(run)), self._sheets(db, run_id)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Scan page", "Status", "Name", "UIN", "Section", "Identified by", "Version", *[f"Q{number}" for number in range(1, run["question_count"] + 1)], "Needs review"])
        evidence = {"uin": "digits read", "name": "name read", "uin+name": "digits and name read"}
        how = lambda item: {"label": "Scantron label", "uin": "exact UIN", "review": "instructor", "confidence": f"{evidence.get(item['match']['method'], 'digits and name read')}, {round(item['match']['confidence'] * 100)}%"}.get(item["current"]["identified_by"], "")
        safe = lambda text: "'" + text if text and text[0] in "=+-@\t\r" else text  # spreadsheet formula prefixes
        for item in sheets:
            student, current = students.get(item["current"]["enrollment_id"], {}), item["current"]
            writer.writerow([item["source_page_index"], current["status"], safe(student.get("name", "")), student.get("uin", ""), student.get("section_number", ""), how(item), current["version"],
                             *current["answers"], *([""] * (run["question_count"] - len(current["answers"]))), safe(" ".join(current["reasons"]))])
        name = "".join(char if char.isalnum() or char in " -_" else "-" for char in run["title"]).strip() or "bubble-sheets"
        return output.getvalue().encode("utf-8-sig"), f"{name} - bubble sheet readings.csv"
