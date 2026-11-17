"""Whose paper a stamped copy printed for no student is: read from the Name and UIN written at the top of its first page.

Requirement, 2026-09-28: a drafted quiz's versions are printed UNNAMED, each copy's stamp showing its version, "the student name will
have to be read". The stamps already say which printed copy (and so which version) a scanned page is; what is missing is the student.
For every page 1 of such a copy (a print job a quiz approval made, `quiz:` request IDs, or any job printed for the whole term with no
section, document_identity migration 013) that appears in a present scan, the worker reads the top band of the page with the
local OCR runtime (`faculty_ocr.recognize_covers`, the same reads as first pages without labels, several pages in one model load) and
keeps the reading (`copy_name_reads`, immutable). The reading is matched against the roster of the copy's own section, or for paper
printed for the whole term against the rosters of every section that job is for (requirement, 2026-10-01), by the bubble sheets' rule at the instructor's threshold. By the instructor's rule for first pages without labels (2026-09-24: "a wrong identity will
be flagged when an exam already exists for that person"), a confident match names the copy's student with no click, through the ordinary
`assign_copy` (actor `automatic`, its event naming the reading), never for a student who already has a copy of this assignment in the term
nor for two copies read as the same student. Anything unsure, conflicting or unreadable waits on the scan's Students page for the
instructor, who names it with the same `assign_copy`. Nothing here groups, grades or clears the scan's review; a copy with no student is
still assembled as a paper, and becomes that student's once named.
"""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import logging

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
import faculty_ocr
from faculty_ocr import BUSY, OcrError
from rosters import connect
from teach_inbox.service import PRESENT, encoded, sha

from . import jev_papers
from .layouts import CORNERS_PROFILE, SMALL_CORNERS_PROFILE
from .photocopies import UNNAMED_JOB
from .unlabeled import COVER_WORDS, MAX_TEXT, TITLE_BOX, cover_fields

RULE = "copy-names-v1"
BATCH = 48
PARALLEL = 4  # QUIZZES "NAMES ON UNNAMED STAMPED COPIES" > SIDE BY SIDE: pages whose Jev and Gemini requests are asked at once
LOG = logging.getLogger(__name__)
STATES = ("named", "identified", "conflict", "needs_review", "unread", "failed")


class CopyNameService:
    def __init__(self, scans):
        self.scans, self.documents, self.inbox, self.rosters, self.path = scans, scans.documents, scans.inbox, scans.rosters, scans.path

    # ----- the worker -------------------------------------------------------------------------
    def _waiting(self, db, limit, message_id=None):
        """Page 1 of copies issued to no student, in present scans, not yet read under this rule: the oldest first. AFTER THE
        INSPECTION (requirement, 2026-11-14): never a page of a scan whose inspection is pending or running, so its first pages are
        read up to BATCH in one model load once it ends, not a few after each piece of it."""
        # CROSS JOIN keeps this join order: from the few quiz and term print jobs (a case-insensitive LIKE cannot use an index, and without
        # statistics the planner otherwise scans every scan page). Same rows; ORDER BY sp.rowid still decides the order.
        return [dict(row) for row in db.execute(f"""SELECT sp.id scan_page_id,sp.message_id,sp.page_artifact_id,sp.raster_artifact_id,sp.content_sha256,
                c.id copy_id,b.configuration_json FROM print_batches b CROSS JOIN printed_copies c ON c.print_batch_id=b.id
                CROSS JOIN page_stamps p ON p.printed_copy_id=c.id AND p.page_number=1 CROSS JOIN scan_pages sp ON sp.page_stamp_id=p.id
                WHERE c.enrollment_id IS NULL AND sp.status IN ('resolved','manual') AND sp.review_state NOT IN ('rejected','ignored')
                  AND {UNNAMED_JOB} AND {PRESENT.format('sp.message_id')}
                  AND NOT EXISTS(SELECT 1 FROM copy_name_reads r WHERE r.scan_page_id=sp.id AND r.rule=?)
                  AND NOT EXISTS(SELECT 1 FROM document_decode_jobs d WHERE d.message_id=sp.message_id AND d.status IN ('pending','running'))
                  {'AND sp.message_id=?' if message_id else ''}
                ORDER BY sp.rowid LIMIT ?""", (RULE, *([message_id] if message_id else []), limit))]

    def process_one(self):
        """Read up to BATCH first pages in one model load, keep the readings, then name the copies a confident match settles."""
        with self.inbox.worker_lock():
            with self.rosters.lock(), connect(self.path) as db:
                pages = self._waiting(db, BATCH)
            if not pages:
                return False
            readings = {}
            try:
                contents = []
                for page in pages:
                    _, content = self.inbox.read_artifact(page["page_artifact_id"])
                    if sha(content) != page["content_sha256"]:
                        raise ValueError("Retained page checksum differs.")
                    contents.append(content)
                # A copy stamped with the two corner codes (the default since 2026-10-01) has one at its top right either way up:
                # its stamp boxes are whited out first, so the band read holds the page's own words, never a code or its alias.
                profiles = [json.loads(page["configuration_json"] or "{}").get("id") for page in pages]
                corners = ["small" if profile == SMALL_CORNERS_PROFILE else profile == CORNERS_PROFILE for profile in profiles]
                result = faculty_ocr.recognize_covers(contents, list(TITLE_BOX), list(COVER_WORDS), title_unless=r".", blank_corners=corners)
                for page, content, read, blanked in zip(pages, contents, result["pages"], corners):
                    if read["source_sha256"] != sha(content):
                        raise ValueError("OCR source checksum differs.")
                    bands = [{"rotation": item["rotation"], "text": (item.get("text") or "")[:MAX_TEXT]} for item in read["reads"] if item["kind"] == "band"]
                    chosen = next(((band, cover_fields(band["text"])[1]) for band in bands if cover_fields(band["text"])[0]), None)
                    # A band without the printed field names is still the top of this copy's first page: take what it says.
                    band, fields = chosen or (bands[0] if bands else {"rotation": 0, "text": ""}, cover_fields(bands[0]["text"] if bands else "")[1])
                    readings[page["scan_page_id"]] = ("complete", {"rule": RULE, "reads": bands, "rotation": band["rotation"], "fields": fields,
                                                                   "field_names": bool(chosen), "model_id": result.get("model_id"), "revision": result.get("revision"),
                                                                   **({"stamp_corners_blanked": True} if blanked is not False else {})})
            except OcrError as error:
                if BUSY in str(error):
                    return False  # another explicit OCR call holds the model: the pages stay next
                readings = {page["scan_page_id"]: ("failed", {"rule": RULE, "error": "The name could not be read: " + str(error)[:300]}) for page in pages}
            except Exception as error:  # noqa: BLE001 - a page that cannot be read waits for the instructor, never blocks the queue
                LOG.exception("Reading names on stamped copies %s failed", [page["scan_page_id"] for page in pages])
                readings = {page["scan_page_id"]: ("failed", {"rule": RULE, "error": "The name could not be read." if not isinstance(error, ValueError) else str(error)})
                            for page in pages}
            self._jev(pages, readings)
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                for page in pages:
                    status, reading = readings[page["scan_page_id"]]
                    db.execute("INSERT OR IGNORE INTO copy_name_reads VALUES(?,?,?,?,?,?,?)",
                               (sha(f"{page['scan_page_id']}:{RULE}".encode())[:32], page["scan_page_id"], page["copy_id"], RULE, status, encoded(reading), timestamp()))
            for message_id in dict.fromkeys(page["message_id"] for page in pages):
                self.name_confident(message_id)
            return True

    def _jev(self, pages, readings):
        """JEV PAPERS (requirement, 2026-10-15): Jev looks at each first page
        read here and reads whose paper it is against the copy's own roster (`jev_papers.page_check`, `identify`), kept in the reading.
        Asked outside the catalog locks; Jev not set up, or failing, leaves the OCR reading as it is. SIDE BY SIDE (instructor,
        2026-11-14): the pages' requests are asked PARALLEL at a time, each page's own in order; the rosters are read here first."""
        jev = self.scans.unlabeled._jev()
        if not jev:
            return
        rosters, asks = {}, []
        for page in pages:
            status, reading = readings[page["scan_page_id"]]
            if status != "complete":
                continue
            try:
                with self.rosters.lock(), connect(self.path) as db:
                    copy = dict(db.execute("""SELECT c.id copy_id,b.id batch_id,b.section_id,s.number section_number,b.term_id FROM printed_copies c
                        JOIN print_batches b ON b.id=c.print_batch_id LEFT JOIN course_sections s ON s.id=b.section_id WHERE c.id=?""",
                        (page["copy_id"],)).fetchone())
                    students = {k: v for k, v in self._roster(db, copy, rosters).items() if not v.get("dropped")}
            except Exception:
                LOG.exception("Jev could not read the name on stamped copy page %s; the OCR reading stands", page["scan_page_id"])
                continue
            asks.append((page, reading, students))

        def ask(entry):
            page, reading, students = entry
            try:
                _, picture = self.inbox.read_artifact(page["raster_artifact_id"])
                check = jev_papers.page_check(jev, page["scan_page_id"], picture)
                reading.update(jev_rule=jev_papers.RULE, jev_page=check)
                if check.get("status") == "complete":
                    reading["jev_student"] = {**jev_papers.identify(jev, page["scan_page_id"], picture, check, students),
                                              "roster": jev_papers.roster_key(students)}
            except Exception:
                LOG.exception("Jev could not read the name on stamped copy page %s; the OCR reading stands", page["scan_page_id"])

        if asks:
            with ThreadPoolExecutor(max_workers=PARALLEL, thread_name_prefix="copy-names-jev") as pool:
                list(pool.map(ask, asks))

    def name_confident(self, message_id):
        """Name every copy of this scan whose reading identifies its student with nothing against it (the rule above)."""
        with self.rosters.lock():
            with connect(self.path) as db:
                view = self._view(db, message_id)
            for copy in view["copies"]:
                if copy["state"] != "identified":
                    continue
                try:
                    self.documents.assign_copy(copy["copy_id"], copy["pick"],
                        f"Read from the name and UIN written on the paper ({copy['basis_text']}); reading {copy['reading_id']}.", actor="automatic")
                except (ValueError, Conflict, NotFound) as error:
                    LOG.warning("Naming copy %s from its reading failed: %s", copy["copy_id"], error)

    # ----- what the instructor sees ----------------------------------------------------------
    def _copies(self, db, message_id):
        """Copies whose page 1 is on this scan and that were printed for no student, or were named from a reading: with the latest reading."""
        rows = [dict(row) for row in db.execute(f"""SELECT sp.id scan_page_id,sp.source_page_index,c.id copy_id,c.copy_number,c.enrollment_id,c.caption,
                b.id batch_id,b.section_id,s.number section_number,b.term_id,v.label version_label,v.assignment_id,a.title,
                (SELECT r.id FROM copy_name_reads r WHERE r.scan_page_id=sp.id ORDER BY r.created_at DESC,r.rowid DESC LIMIT 1) reading_id
                FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id AND p.page_number=1 JOIN printed_copies c ON c.id=p.printed_copy_id
                JOIN print_batches b ON b.id=c.print_batch_id LEFT JOIN course_sections s ON s.id=b.section_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id JOIN assignments a ON a.id=v.assignment_id
                WHERE sp.message_id=? AND sp.status IN ('resolved','manual') AND sp.review_state NOT IN ('rejected','ignored')
                  AND {UNNAMED_JOB} AND {PRESENT.format('sp.message_id')}
                  AND (c.enrollment_id IS NULL OR EXISTS(SELECT 1 FROM copy_name_reads r WHERE r.printed_copy_id=c.id OR r.scan_page_id=sp.id))
                ORDER BY sp.source_artifact_id,sp.source_page_index""", (message_id,))]
        for row in rows:
            reading = db.execute("SELECT status,reading_json FROM copy_name_reads WHERE id=?", (row["reading_id"],)).fetchone() if row["reading_id"] else None
            row["reading"] = {"status": reading["status"], **json.loads(reading["reading_json"])} if reading else None
        return rows

    def status(self, db, message_id):
        """Counts for the scan's overview, from the catalog alone (no roster, no name): None when the scan has no such copy."""
        row = db.execute(f"""SELECT COUNT(DISTINCT c.id) total, COUNT(DISTINCT CASE WHEN c.enrollment_id IS NOT NULL THEN c.id END) named,
                COUNT(DISTINCT CASE WHEN c.enrollment_id IS NULL AND NOT EXISTS(SELECT 1 FROM copy_name_reads r WHERE r.scan_page_id=sp.id AND r.rule=?) THEN c.id END) unread
                FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id AND p.page_number=1 JOIN printed_copies c ON c.id=p.printed_copy_id
                JOIN print_batches b ON b.id=c.print_batch_id
                WHERE sp.message_id=? AND sp.status IN ('resolved','manual') AND sp.review_state NOT IN ('rejected','ignored')
                  AND {UNNAMED_JOB} AND {PRESENT.format('sp.message_id')}
                  AND (c.enrollment_id IS NULL OR EXISTS(SELECT 1 FROM copy_name_reads r WHERE r.printed_copy_id=c.id OR r.scan_page_id=sp.id))""", (RULE, message_id)).fetchone()
        if not row or not row["total"]:
            return None
        return {"total": row["total"], "named": row["named"], "unread": row["unread"], "waiting": row["total"] - row["named"] - row["unread"]}

    def _threshold(self, db):
        return self.scans.unlabeled._threshold(db)

    def _pool(self, db, copy):
        """Whose paper a copy can be: its section's roster; for paper printed for the whole term (no section, migration 013) the
        rosters of the sections that job is for. (key, sections)."""
        if copy["section_id"]:
            return copy["section_id"], [{"id": copy["section_id"], "number": copy["section_number"]}]
        return copy["batch_id"], self.documents.job_sections(db, copy["batch_id"])

    def _roster(self, db, copy, rosters):
        key, sections = self._pool(db, copy)
        if key not in rosters:
            rosters[key] = self.documents.seating._students(db, copy["term_id"], sections)
        return rosters[key]

    def _view(self, db, message_id):
        """Every such copy of the scan with its reading, its match and its state. Needs the roster lock (names come from the roster)."""
        from bubble_sheets import matching
        copies = self._copies(db, message_id)
        threshold = self._threshold(db)
        rosters, matches, pools = {}, {}, {}
        for copy in copies:
            students = self._roster(db, copy, rosters)
            reading = copy["reading"]
            if reading and reading["status"] == "complete":
                # Remembered per reading and roster, as first pages' are: the view is drawn on every worker turn and page refresh, and
                # matching every read copy again took 7 s at 106 copies of a 582-page Quiz 6 scan (2026-10-31), growing with each turn.
                if id(students) not in pools:
                    present = {key: s for key, s in students.items() if not s.get("dropped")}
                    pools[id(students)] = (present, self.scans.unlabeled._roster_key(present))
                present, roster = pools[id(students)]
                matches[copy["copy_id"]] = self.scans.unlabeled._current_match(reading["fields"], present, roster)
        # A student who has a copy of the assignment in this term already (named on paper, or by an earlier reading or choice).
        holders = {}
        for copy in copies:
            key = (copy["assignment_id"], copy["term_id"])
            if key not in holders:
                holders[key] = {row[0]: row[1] for row in db.execute("""SELECT c.enrollment_id,c.id FROM printed_copies c JOIN print_batches b ON b.id=c.print_batch_id
                    JOIN assignment_versions v ON v.id=b.assignment_version_id
                    WHERE v.assignment_id=? AND b.term_id=? AND c.enrollment_id IS NOT NULL AND b.status!='cancelled' AND b.superseded_at IS NULL""", key)}
        picks = {}
        for copy in copies:
            # Whose paper the reading says it is: the OCR's confident match, else Jev's (JEV PAPERS); none where the two disagree.
            match, reading = matches.get(copy["copy_id"]), copy["reading"] or {}
            jev = (reading.get("jev_student") or {}).get("enrollment_id")
            if jev and jev not in {k for k, s in self._roster(db, copy, rosters).items() if not s.get("dropped")}:
                jev = None
            ocr = match["enrollment_id"] if match and matching.identified(match, threshold) else None
            picks[copy["copy_id"]] = (None, "disagree") if ocr and jev and ocr != jev else (ocr, "ocr") if ocr else (jev, "jev") if jev else (None, None)
        wanted, papers = {}, {}
        for copy in copies:
            papers[copy["copy_id"]] = papers.get(copy["copy_id"], 0) + 1
        for copy in copies:
            pick = picks[copy["copy_id"]][0]
            if copy["enrollment_id"] is None and pick:
                wanted.setdefault((copy["assignment_id"], pick), []).append(copy["copy_id"])
        found = []
        for copy in copies:
            students = self._roster(db, copy, rosters)
            match, reading = matches.get(copy["copy_id"]), copy["reading"]
            who = ((reading or {}).get("jev_student") or {}).get("reader") or "Jev"  # DOCUMENTS "JEV PAPERS" > GEMINI MATCHES THE STUDENT
            entry = {k: copy[k] for k in ("scan_page_id", "copy_id", "copy_number", "section_id", "section_number", "version_label", "title", "reading_id")}
            entry.update(page=copy["source_page_index"] + 1, fields=(reading or {}).get("fields", {}), error=(reading or {}).get("error", ""),
                         match={k: match[k] for k in ("state", "enrollment_id", "confidence", "notes")} | {"candidates": [
                             {"enrollment_id": c["enrollment_id"], "name": c["name"], "section_number": c["section_number"], "confidence": c["confidence"]}
                             for c in match["candidates"][:3]]} if match else None,
                         students=[{"enrollment_id": s["enrollment_id"], "name": s["name"], "section_number": s["section_number"]}
                                   for s in sorted(students.values(), key=lambda s: s["name"].casefold()) if not s.get("dropped")])
            if copy["enrollment_id"]:
                student = students.get(copy["enrollment_id"])
                entry.update(state="named", student={"enrollment_id": copy["enrollment_id"], "name": student["name"] if student else "",
                                                     "section_number": student["section_number"] if student and not copy["section_id"] else copy["section_number"]})
            elif not reading:
                entry["state"] = "unread"
            elif reading["status"] == "failed":
                entry["state"] = "failed"
            elif papers[copy["copy_id"]] > 1:
                # PHOTOCOPIED STAMPS: one copy's page 1 on several papers is no one student's until each paper has its own copy.
                entry["state"] = "conflict"
                entry["conflict"] = "This copy's stamps are on more than one paper: they were photocopied."
            elif picks[copy["copy_id"]][0]:
                pick, source = picks[copy["copy_id"]]
                holder = holders[(copy["assignment_id"], copy["term_id"])].get(pick)
                twice = len(wanted.get((copy["assignment_id"], pick), [])) > 1
                entry["state"] = "conflict" if holder or twice else "identified"
                entry["conflict"] = ("This student already has a copy of this assignment." if holder else
                                     "Another paper was read as the same student." if twice else "")
                entry["pick"], entry["basis"] = pick, source
            else:
                entry["state"] = "needs_review"
                if picks[copy["copy_id"]][1] == "disagree":
                    entry["conflict"] = f"{who} reads the handwritten name and UIN as another student's. Check them, then choose the student."
                elif (reading.get("jev_student") or {}).get("reason"):
                    entry["conflict"] = f"{who}: " + reading["jev_student"]["reason"]
            if picks[copy["copy_id"]][1] == "jev" and (entry.get("pick") or copy["enrollment_id"]) == picks[copy["copy_id"]][0]:
                entry["basis_text"] = f"{who}'s reading of the handwritten name and UIN"
            elif match:
                entry["basis_text"] = ("an exact roster UIN" if match["state"] == "matched" else f"a {round(match['confidence'] * 100)}% match")
            found.append(entry)
        return {"copies": found, "threshold": threshold, "rule": RULE,
                "counts": {state: sum(1 for c in found if c["state"] == state) for state in STATES}}

    def summary(self, message_id):
        with self.rosters.lock(), connect(self.path) as db:
            self.scans.unlabeled._job(db, message_id)
            view = self._view(db, message_id)
        for copy in view["copies"]:
            copy.pop("reading_id", None)
        return view

    def assign(self, message_id, data):
        """The instructor names a copy of this scan: its page 1 must be on the scan, the student on the copy's own section's roster
        (for paper printed for the whole term, on the roster of a section of that term)."""
        copy_id, enrollment_id = data.get("printed_copy_id"), data.get("enrollment_id")
        if not isinstance(copy_id, str) or not isinstance(enrollment_id, str):
            raise ValueError("Choose the paper and its student.")
        with self.rosters.lock():
            with connect(self.path) as db:
                self.scans.unlabeled._job(db, message_id)
                copy = next((c for c in self._copies(db, message_id) if c["copy_id"] == copy_id), None)
                if not copy:
                    raise NotFound("That paper is not a stamped paper of this scan waiting for its student.")
            self.documents.assign_copy(copy_id, enrollment_id, "Named by the instructor from the name written on the scanned paper.")
        return self.summary(message_id)

    def search_students(self, message_id, data):
        """Students of a copy's section (its job's sections, for paper printed for the whole term) whose UIN is closest to digits
        typed into its picker; names and sections only."""
        from bubble_sheets import matching
        typed, copy_id = data.get("uin"), data.get("printed_copy_id")
        if not isinstance(typed, str) or len(typed) > 40:
            raise ValueError("Type part of a UIN.")
        with self.rosters.lock(), connect(self.path) as db:
            self.scans.unlabeled._job(db, message_id)
            copy = next((c for c in self._copies(db, message_id) if c["copy_id"] == copy_id), None)
            if not copy:
                raise NotFound("That paper is not part of this scan.")
            students = self._roster(db, copy, {})
        return {"students": matching.uin_search(typed, list(students.values()))}

    def crop(self, message_id, scan_page_id):
        """The top of one such first page, upright, for the instructor to read the name and UIN. Cut on request; nothing is stored."""
        from PIL import Image
        with self.rosters.lock(), connect(self.path) as db:
            self.scans.unlabeled._job(db, message_id)
            copy = next((c for c in self._copies(db, message_id) if c["scan_page_id"] == scan_page_id), None)
            row = db.execute("SELECT raster_artifact_id FROM scan_pages WHERE id=?", (scan_page_id,)).fetchone() if copy else None
        if not row:
            raise NotFound("That page is not the first page of a stamped paper of this scan.")
        _, content = self.inbox.read_artifact(row["raster_artifact_id"])
        image = Image.open(io.BytesIO(content)).convert("L")
        if (copy["reading"] or {}).get("rotation") == 180:
            image = image.rotate(180)
        width, height = image.size
        part = image.crop((0, 0, width, int(height * 0.18)))
        part.thumbnail((1100, 1100))
        buffer = io.BytesIO()
        part.save(buffer, "PNG")
        return buffer.getvalue()
