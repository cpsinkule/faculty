"""Instructor grading of exact, reviewed paper revisions.

Rubrics are course-wide immutable assignment/version resources, explicitly pinned
by each term exam. Marks, finalizations and releases belong to the exam's retained
section/enrollment membership. All writes use the roster lifecycle lock and the
catalog transaction; no payload folders, mail or autonomous processors are used.
"""
import csv
import io
import json
import math
import re
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict, Forbidden, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from rosters import connect
from teach_inbox.service import PRESENT, encoded, sha


def _text(value, label, maximum, *, blank=False):
    if not isinstance(value, str) or len(value) > maximum or (not blank and not value.strip()):
        raise ValueError(f"{label} must be {'at most' if blank else '1–'} {maximum} characters.")
    return value.strip()


def _number(value, label, maximum=100000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number between 0 and {maximum}.")
    return float(value)


def _version(data, current):
    if type(data.get("version")) is not int or data["version"] != current:
        raise Conflict("Grading changed since this view was opened. Refresh and review before saving.")


def _safe_csv(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) or value.startswith(("\t", "\r", "\n")) else value


def _question_regions(question):
    """Read old single-page rubrics without rewriting their immutable JSON."""
    return question.get("regions") or [{"page": question["page"], "region": question["region"]}]


class GradingService:
    def __init__(self, exams):
        self.exams, self.rosters, self.path = exams, exams.rosters, exams.path
        with self.rosters.lock(), connect(self.path) as db:
            db.executescript(Path(__file__).with_name("grading.sql").read_text())
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exam_grading_settings'").fetchone():
                db.execute("""INSERT OR IGNORE INTO exam_grading_rubrics
                    SELECT s.event_id,r.assignment_version_id,s.rubric_id,s.version
                    FROM exam_grading_settings s JOIN exam_rubric_versions r ON r.id=s.rubric_id""")
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted(Path(__file__).with_name("template_migrations").glob("*.sql")):
                apply_sql(db, "exam_templates", migration.stem, migration.read_text(encoding="utf-8"))

    @staticmethod
    def _event_log(db, event_id, kind, data, section_id=None, enrollment_id=None):
        db.execute("INSERT INTO exam_grading_events(event_id,section_id,enrollment_id,kind,detail_json,created_at) VALUES(?,?,?,?,?,?)",
                   (event_id, section_id, enrollment_id, kind, encoded(data), timestamp()))

    def _context(self, db, event_id):
        event = self.exams._event(db, event_id)
        participants = self.exams._participants(db, event)
        self.exams._collection(db, event, participants)
        return event, participants

    @staticmethod
    def _rubric(db, event_id, assignment_version_id=None):
        row = db.execute("""SELECT r.*,s.version FROM exam_grading_rubrics s
            JOIN exam_rubric_versions r ON r.id=s.rubric_id WHERE s.event_id=?
            AND (? IS NULL OR s.assignment_version_id=?) ORDER BY r.created_at DESC,r.rowid DESC LIMIT 1""",
            (event_id, assignment_version_id, assignment_version_id)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["questions"] = json.loads(result.pop("questions_json"))
        provenance = db.execute("SELECT * FROM exam_rubric_template_sources WHERE rubric_id=?", (result["id"],)).fetchone()
        result["template_revision_id"] = provenance["template_revision_id"] if provenance else None
        result["template"] = json.loads(provenance["template_json"]) if provenance else None
        return result

    @staticmethod
    def _questions(data, page_count):
        questions = data.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= 100:
            raise ValueError("Configure between 1 and 100 questions.")
        result, ids, total_regions = [], set(), 0
        for item in questions:
            if not isinstance(item, dict):
                raise ValueError("Each question must be an object.")
            identifier = item.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identifier) or identifier in ids:
                raise ValueError("Question IDs must be distinct letters, digits, underscores or hyphens.")
            ids.add(identifier)
            maximum = _number(item.get("max_points"), "Question points")
            if maximum <= 0:
                raise ValueError("Question points must be greater than zero.")
            selections = item.get("regions", [{"page": item.get("page", 1),
                "region": item.get("region", {"x": 0, "y": 0, "width": 1, "height": 1})}])
            if not isinstance(selections, list) or not 1 <= len(selections) <= 100:
                raise ValueError("A question needs between 1 and 100 page regions.")
            total_regions += len(selections)
            if total_regions > 1000:
                raise ValueError("A rubric accepts at most 1000 question regions in total.")
            regions = []
            for selection in selections:
                if not isinstance(selection, dict) or set(selection) - {"page", "region"}:
                    raise ValueError("Each question region needs a page and an optional rectangle.")
                page = selection.get("page")
                if type(page) is not int or not 1 <= page <= page_count:
                    raise ValueError("Question page must exist in the selected document version.")
                region = selection.get("region", {"x": 0, "y": 0, "width": 1, "height": 1})
                if not isinstance(region, dict) or set(region) != {"x", "y", "width", "height"}:
                    raise ValueError("A question region needs x, y, width and height.")
                region = {key: _number(value, "Region coordinate", 1) for key, value in region.items()}
                if region["width"] <= 0 or region["height"] <= 0 or region["x"] + region["width"] > 1.00000001 or region["y"] + region["height"] > 1.00000001:
                    raise ValueError("Question regions must stay within the page.")
                regions.append({"page": page, "region": region})
            criteria = item.get("criteria", [])
            if not isinstance(criteria, list) or len(criteria) > 50:
                raise ValueError("A question accepts at most 50 rubric criteria.")
            cleaned, criterion_ids = [], set()
            for criterion in criteria:
                if not isinstance(criterion, dict):
                    raise ValueError("Each rubric criterion must be an object.")
                cid = criterion.get("id")
                if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", cid) or cid in criterion_ids:
                    raise ValueError("Criterion IDs must be distinct letters, digits, underscores or hyphens.")
                criterion_ids.add(cid)
                cleaned.append({"id": cid, "label": _text(criterion.get("label"), "Criterion label", 500),
                                "points": _number(criterion.get("points"), "Criterion points", maximum)})
            if cleaned and not math.isclose(sum(c["points"] for c in cleaned), maximum, abs_tol=1e-8):
                raise ValueError("Rubric criterion points must add up to the question maximum.")
            feedback = item.get("feedback", [])
            if not isinstance(feedback, list) or len(feedback) > 50:
                raise ValueError("A question accepts at most 50 reusable feedback comments.")
            result.append({"id": identifier, "label": _text(item.get("label"), "Question label", 300),
                           "max_points": maximum, "page": regions[0]["page"], "region": regions[0]["region"],
                           "regions": regions, "criteria": cleaned,
                           "feedback": [_text(comment, "Reusable feedback", 4000) for comment in feedback]})
        return result

    def configure(self, event_id, data):
        if not isinstance(data, dict) or any(key in data for key in ("template_revision_id", "template", "template_id")):
            raise ValueError("Apply a retained template through the template action; rubric edits cannot set its provenance.")
        return self._configure(event_id, data)

    def apply_template(self, event_id, data):
        if not isinstance(data, dict) or set(data) != {"assignment_version_id", "template_revision_id", "version"}:
            raise ValueError("Choose a document version, template revision, and current rubric version.")
        revision_id = data["template_revision_id"]
        if not isinstance(revision_id, str) or not revision_id:
            raise ValueError("Choose an immutable template revision.")
        return self._configure(event_id, data, template_revision_id=revision_id)

    def _configure(self, event_id, data, *, template_revision_id=None):
        with self.rosters.lock(), connect(self.path) as db:
            event, participants = self._context(db, event_id)
            db.execute("BEGIN IMMEDIATE")
            rubric = self._rubric(db, event_id, data.get("assignment_version_id"))
            _version(data, rubric["version"] if rubric else 0)
            document = db.execute("SELECT * FROM assignment_versions WHERE id=? AND assignment_id=?",
                                  (data.get("assignment_version_id"), event["assignment_id"])).fetchone()
            if not document:
                raise ValueError("Choose a document version belonging to this exam's assignment.")
            template = None
            if template_revision_id:
                source = db.execute("""SELECT r.id revision_id,r.revision,r.questions_json,r.sha256,
                    t.id,t.assignment_id,t.label,t.page_count FROM assignment_template_revisions r
                    JOIN assignment_templates t ON t.id=r.template_id WHERE r.id=?""", (template_revision_id,)).fetchone()
                if not source or source["assignment_id"] != event["assignment_id"]:
                    raise ValueError("Choose a template revision belonging to this exam's assignment.")
                if source["page_count"] != document["page_count"]:
                    raise ValueError("The template and selected printed document must have the same page count.")
                retained_questions = json.loads(source["questions_json"])
                if sha(encoded(retained_questions).encode()) != source["sha256"]:
                    raise Conflict("The retained template mapping failed its checksum verification.")
                questions = self._questions({"questions": retained_questions}, document["page_count"])
                template = {key: source[key] for key in ("id", "label", "revision_id", "revision", "sha256", "page_count")}
            else:
                questions = self._questions(data, document["page_count"])
            if (rubric and self._questions({"questions": rubric["questions"]}, document["page_count"]) == questions
                    and rubric["assignment_version_id"] == document["id"]
                    and (template_revision_id is None or rubric["template_revision_id"] == template_revision_id)):
                # Saving unchanged fields retains their exact source and current
                # grades. Only an explicit apply action selects another template.
                return rubric
            now = timestamp()
            provenance = {"template_revision_id": template_revision_id, "template_sha256": template["sha256"]} if template else None
            digest = sha(encoded({"assignment_version_id": document["id"], "questions": questions,
                                  **({"template_source": provenance} if provenance else {})}).encode())
            shared = db.execute("""SELECT id FROM exam_rubric_versions WHERE assignment_id=?
                AND assignment_version_id=? AND sha256=? ORDER BY revision LIMIT 1""",
                (event["assignment_id"], document["id"], digest)).fetchone()
            if shared:
                rid = shared["id"]
            else:
                revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM exam_rubric_versions WHERE assignment_id=?",
                                      (event["assignment_id"],)).fetchone()[0]
                rid = str(uuid.uuid4())
                db.execute("INSERT INTO exam_rubric_versions VALUES(?,?,?,?,?,?,?)",
                           (rid, event["assignment_id"], document["id"], revision, encoded(questions), digest, now))
                if template:
                    db.execute("INSERT INTO exam_rubric_template_sources VALUES(?,?,?,?)",
                               (rid, template_revision_id, template["sha256"], encoded(template)))
            db.execute("""INSERT INTO exam_grading_rubrics VALUES(?,?,?,1) ON CONFLICT(event_id,assignment_version_id)
                DO UPDATE SET rubric_id=excluded.rubric_id,version=exam_grading_rubrics.version+1""", (event_id, document["id"], rid))
            if rubric:
                # Even a still-ungraded student's open form must become stale
                # when the rubric changes under it.
                for participant in participants:
                    if self._paper_version(db, event_id, participant) != document["id"]:
                        continue
                    key = (event_id, participant["section_id"], participant["enrollment_id"])
                    db.execute("""INSERT OR IGNORE INTO exam_grade_states(event_id,section_id,enrollment_id,updated_at)
                        VALUES(?,?,?,?)""", (*key, now))
                    db.execute("""UPDATE exam_grade_states SET version=version+1,updated_at=?
                        WHERE event_id=? AND section_id=? AND enrollment_id=?""", (now, *key))
            # Retain finalized/released pointers for audit. Their source checks
            # mark them stale until the instructor explicitly reopens/regrades.
            self._event_log(db, event_id, "rubric_configured", {"rubric_id": rid, "previous_id": rubric["id"] if rubric else None,
                                                             "template_revision_id": template_revision_id})
            return self._rubric(db, event_id, document["id"])

    # GRADING "DR COPIES": the document a print job's papers count as. The DR center's copy of a version (a label-only document
    # naming the version it copies, `DocumentService.register_dr_copy`) counts as that version; every other job as its own document.
    # `b` is the print job, `v` its document.
    PAPER_DOCUMENT = "COALESCE(json_extract(v.compiler_json,'$.dr_copy.version_id'),b.assignment_version_id)"

    @classmethod
    def _paper_version(cls, db, event_id, participant):
        selected = participant.get("collection", {}).get("assembly_revision_id")
        if selected:
            row = db.execute(f"""SELECT {cls.PAPER_DOCUMENT} FROM assembly_revisions ar
                JOIN printed_copies c ON c.id=ar.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id
                WHERE ar.id=?""", (selected,)).fetchone()
            return row[0] if row else None
        versions = [r[0] for r in db.execute(f"""SELECT DISTINCT {cls.PAPER_DOCUMENT} FROM print_batches b
            JOIN printed_copies c ON c.print_batch_id=b.id JOIN exam_event_batches eb ON eb.batch_id=b.id
            JOIN assignment_versions v ON v.id=b.assignment_version_id
            WHERE eb.event_id=? AND (b.section_id=? OR b.section_id IS NULL) AND c.enrollment_id=? AND b.status='ready' AND b.superseded_at IS NULL""",
            (event_id, participant["section_id"], participant["enrollment_id"]))]
        return versions[0] if len(versions) == 1 else None

    def _student_rubric(self, db, event_id, participant):
        version_id = self._paper_version(db, event_id, participant)
        return self._rubric(db, event_id, version_id) if version_id else None

    @staticmethod
    def _participant(participants, section_id, enrollment_id):
        result = next((p for p in participants if p["section_id"] == section_id and p["enrollment_id"] == enrollment_id), None)
        if not result:
            raise NotFound("This student is not a participant in this exam and section.")
        return result

    @staticmethod
    def _state(db, event_id, participant):
        row = db.execute("SELECT * FROM exam_grade_states WHERE event_id=? AND section_id=? AND enrollment_id=?",
                         (event_id, participant["section_id"], participant["enrollment_id"])).fetchone()
        return dict(row) if row else {"event_id": event_id, "section_id": participant["section_id"],
                                    "enrollment_id": participant["enrollment_id"], "version": 0,
                                    "finalized_id": None, "released_id": None}

    @staticmethod
    def _marks(db, event_id, participant):
        rows = db.execute("""SELECT * FROM exam_mark_revisions WHERE event_id=? AND section_id=? AND enrollment_id=?
            ORDER BY question_id,revision""", (event_id, participant["section_id"], participant["enrollment_id"]))
        result = {}
        for row in rows:
            mark = dict(row)
            mark["criterion_ids"] = json.loads(mark.pop("criterion_ids_json"))
            result[mark["question_id"]] = mark
        return result

    # A paper's assembly revision with its print job and document, when the job is one of the exam's (`_source`).
    _ASSEMBLY = """SELECT ar.*,b.assignment_version_id,b.section_id,b.status,b.superseded_at,c.enrollment_id,
            v.assignment_id,v.page_count,v.compiler_json,b.term_id FROM assembly_revisions ar
            JOIN printed_copies c ON c.id=ar.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
            JOIN exam_event_batches eb ON eb.batch_id=b.id AND eb.event_id=?
            JOIN assignment_versions v ON v.id=b.assignment_version_id"""
    # An assembly's pages with their scan, Inbox message, printed page and raster ({} adds columns, `{where}` picks the revisions).
    _PAGES = """SELECT sp.id,sp.content_sha256,sp.raster_artifact_id,sp.message_id,
                sp.status,m.authenticated,m.instructor_review_required,p.printed_copy_id,p.page_number,p.state,
                a.message_id artifact_message_id,a.sha256 raster_sha256{}
                FROM {from}assembly_pages ap JOIN scan_pages sp ON sp.id=ap.scan_page_id
                JOIN inbox_messages m ON m.id=sp.message_id JOIN page_stamps p ON p.id=sp.page_stamp_id
                JOIN inbox_artifacts a ON a.id=sp.raster_artifact_id
                WHERE {where}"""

    @classmethod
    def _assemblies(cls, db, event, memo):
        """{revision id: (its `_source` row, {(scan_page_id, page_number): its page row})} for every assembly of the exam's print jobs,
        in two reads kept in `memo` (one read's memo, never across a write) for a pass over every paper (PERFORMANCE, 2026-10-02:
        two reads per paper were about 2,000 of the Grading index's statements). Each row is exactly what `_source`'s own query for
        that revision gives."""
        key = ("assemblies", event["id"])
        if key not in memo:
            found = {row["id"]: (row, {}) for row in db.execute(cls._ASSEMBLY, (event["id"],))}
            for page in db.execute(cls._PAGES.replace("{from}", """exam_event_batches eb JOIN printed_copies c ON c.print_batch_id=eb.batch_id
                    JOIN assembly_revisions ar ON ar.printed_copy_id=c.id JOIN """).replace("{where}", "eb.event_id=? AND ap.revision_id=ar.id").format(
                    ",ap.scan_page_id ap_scan_page_id,ap.page_number ap_page_number,ap.revision_id ap_revision_id"), (event["id"],)):
                held = found.get(page["ap_revision_id"])
                if held is not None:
                    held[1][(page["ap_scan_page_id"], page["ap_page_number"])] = page
            memo[key] = found
        return memo[key]

    def _source(self, db, event, participant, rubric, expected=None, memo=None):
        """The paper's current reviewed assembly, every check made. `memo`: a dict kept for ONE read over every paper of the exam
        (`JobsMixin.source` when its caller opted in with `prefetch`): each assembly and its pages then come from `_assemblies`, and
        one not found there is read alone as before."""
        collection = participant.get("collection", {})
        revision_id = collection.get("assembly_revision_id")
        if not revision_id:
            issues = collection.get("issues", [])
            detail = "; ".join(str(item) for item in issues[:3])
            raise Conflict("Select a current reviewed submission and approve its source Inbox messages before grading." + (" " + detail if detail else ""))
        if expected is not None and expected != revision_id:
            raise Conflict("The selected submission changed. Open the current reviewed paper before grading.")
        known = self._assemblies(db, event, memo).get(revision_id) if memo is not None else None
        source = known[0] if known else db.execute(self._ASSEMBLY + " WHERE ar.id=?", (event["id"], revision_id)).fetchone()
        # A copy of paper printed for the whole term (no section, document_identity migration 013) is its student's, in their section.
        if not source or source["section_id"] not in (None, participant["section_id"]) or source["enrollment_id"] != participant["enrollment_id"] or source["term_id"] != event["term_id"] or source["assignment_id"] != event["assignment_id"]:
            raise Conflict("The reviewed source does not belong to this exam and roster membership.")
        if source["status"] != "ready" or source["superseded_at"]:
            raise Conflict("This paper is no longer a current issued copy.")
        if not rubric:
            raise Conflict("Configure a grading rubric before opening student work.")
        # GRADING "DR COPIES": for a caller that lays its answer areas out on the DR center's copy (`rubric["dr_copies"]`, assisted
        # grading), the copy of the rubric's version is that version's paper, and `dr_copy` in the answer says which copy it is.
        copy = None
        if source["assignment_version_id"] != rubric["assignment_version_id"]:
            copy = json.loads(source["compiler_json"] or "{}").get("dr_copy") if rubric.get("dr_copies") else None
            if not copy or copy.get("version_id") != rubric["assignment_version_id"]:
                raise Conflict("The selected paper uses a different document version from this rubric.")
        selection = json.loads(source["selection_json"])
        if sha(encoded(selection).encode()) != source["selection_sha256"]:
            raise Conflict("The retained assembly failed its selection checksum check.")
        pages = []
        query = self._PAGES.replace("{from}", "").replace("{where}", "ap.revision_id=?")
        # Every page of the assembly in one read (performance audit, 2026-09-29), keyed by its own page and number (unique per
        # revision); a selected page not found that way is asked for alone as before. Every check below is unchanged.
        found = known[1] if known else {(row["ap_scan_page_id"], row["ap_page_number"]): row for row in db.execute(
            query.format(",ap.scan_page_id ap_scan_page_id,ap.page_number ap_page_number"), (revision_id,))}
        for chosen in selection:
            page = found.get((chosen["scan_page_id"], chosen["page_number"])) or db.execute(query.format("") + " AND ap.scan_page_id=? AND ap.page_number=?",
                (revision_id, chosen["scan_page_id"], chosen["page_number"])).fetchone()
            if not page or page["content_sha256"] != chosen["content_sha256"] or page["printed_copy_id"] != source["printed_copy_id"] or page["page_number"] != chosen["page_number"] or page["artifact_message_id"] != page["message_id"] or page["state"] != "issued" or page["status"] not in ("resolved", "manual"):
                raise Conflict("The reviewed page no longer matches its retained source and printed identity.")
            if not page["authenticated"]:
                raise Forbidden("Failed source authentication cannot be overridden by grading review.")
            if page["instructor_review_required"]:
                raise Conflict("Explicitly review the source Inbox message before grading.")
            pages.append({"page_number": page["page_number"], "scan_page_id": page["id"],
                          "raster_url": f"/api/inbox/artifacts/{page['raster_artifact_id']}/download",
                          "raster_sha256": page["raster_sha256"]})
        # A stamps-only version counts its label sheet, not the exam behind it; that paper's length is checked
        # against the blank exam where one is chosen (FRQ grading) and its pages must still be consecutive from 1.
        labels_only = json.loads(source["compiler_json"] or "{}").get("generator") == "faculty-stamps-only"
        expected = len(pages) if labels_only else source["page_count"]
        if not pages or len({p["page_number"] for p in pages}) != len(pages) or (not source["partial"] and len(pages) != expected) \
                or (labels_only and not source["partial"] and {p["page_number"] for p in pages} != set(range(1, len(pages) + 1))):
            raise Conflict("Missing pages require an explicitly reviewed partial assembly.")
        return {**dict(source), "pages": pages, **({"dr_copy": copy} if copy else {})}

    @staticmethod
    def _current(mark, rubric, source):
        return bool(mark and rubric and source and mark["rubric_id"] == rubric["id"] and
                    mark["assembly_revision_id"] == source["id"] and mark["assembly_sha256"] == source["selection_sha256"])

    def _summary(self, db, event, participant, rubric, anonymous=False, number=1):
        state = self._state(db, event["id"], participant)
        source, error = None, ""
        try:
            source = self._source(db, event, participant, rubric)
        except (Conflict, Forbidden) as problem:
            error = problem.description
        marks = self._marks(db, event["id"], participant)
        for mark in marks.values():
            mark["current"] = self._current(mark, rubric, source)
        questions = rubric["questions"] if rubric else []
        current = [marks[q["id"]] for q in questions if q["id"] in marks and marks[q["id"]]["current"]]
        finalization = db.execute("SELECT * FROM exam_grade_finalizations WHERE id=?", (state["finalized_id"],)).fetchone()
        released = db.execute("SELECT * FROM exam_grade_releases WHERE id=?", (state["released_id"],)).fetchone()
        stale = any(q["id"] in marks and not marks[q["id"]]["current"] for q in questions)
        if finalization and not self._current(finalization, rubric, source):
            stale = True
        status = "stale" if stale else "blocked" if error else "released" if released else "finalized" if finalization else "in_progress" if current else "ungraded"
        source_jobs = [row[0] for row in db.execute(f"""SELECT DISTINCT j.id FROM inbox_jobs j
            JOIN scan_pages sp ON sp.message_id=j.message_id JOIN page_stamps p ON p.id=sp.page_stamp_id
            JOIN printed_copies c ON c.id=p.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
            JOIN exam_event_batches eb ON eb.batch_id=b.id WHERE eb.event_id=? AND (b.section_id=? OR b.section_id IS NULL) AND c.enrollment_id=?
            AND {PRESENT.format('sp.message_id')} ORDER BY j.id""", (event["id"], participant["section_id"], participant["enrollment_id"]))]
        return {"section_id": participant["section_id"], "enrollment_id": participant["enrollment_id"],
                "assignment_version_id": self._paper_version(db, event["id"], participant),
                "section_number": participant.get("section_number"), "name": f"Paper {number}" if anonymous else participant.get("name", "Student"),
                "version": state["version"], "status": status, "source_error": error, "source_job_ids": source_jobs,
                "template_revision_id": rubric["template_revision_id"] if rubric else None,
                "template": rubric["template"] if rubric else None,
                "assembly_revision_id": source["id"] if source else None,
                "marks": marks, "graded_count": len(current), "question_count": len(questions),
                "total": sum(mark["score"] for mark in current), "max_points": sum(q["max_points"] for q in questions),
                "finalized_at": finalization["created_at"] if finalization else None,
                "released_at": released["created_at"] if released else None,
                "release_id": released["id"] if released else None}

    def details(self, event_id, anonymous=False, assignment_version_id=None):
        with self.rosters.lock(), connect(self.path) as db:
            event, participants = self._context(db, event_id)
            if assignment_version_id and not db.execute("SELECT 1 FROM assignment_versions WHERE id=? AND assignment_id=?", (assignment_version_id, event["assignment_id"])).fetchone():
                raise NotFound("This document version does not belong to the exam's assignment.")
            rubric = self._rubric(db, event_id, assignment_version_id)
            participants.sort(key=lambda p: (p["section_id"], p["enrollment_id"]))
            questions = rubric["questions"] if rubric else []
            versions = [dict(row) for row in db.execute("SELECT id,label,page_count FROM assignment_versions WHERE assignment_id=? ORDER BY created_at,id", (event["assignment_id"],))]
            return {"event_id": event_id, "version": rubric["version"] if rubric else 0,
                    "assignment_version_id": assignment_version_id or (rubric["assignment_version_id"] if rubric else None),
                    "rubrics": [self._rubric(db, event_id, row[0]) for row in db.execute("SELECT assignment_version_id FROM exam_grading_rubrics WHERE event_id=?", (event_id,))],
                    "rubric": rubric, "questions": questions, "assignment_versions": versions,
                    "template_revision_id": rubric["template_revision_id"] if rubric else None,
                    "template": rubric["template"] if rubric else None,
                    "max_points": sum(q["max_points"] for q in questions),
                    "participants": [self._summary(db, event, p, self._student_rubric(db, event_id, p), anonymous, index + 1) for index, p in enumerate(participants)],
                    "anonymous": bool(anonymous), "anonymity_note": "Roster names are hidden; names written or printed on scanned pages remain visible." if anonymous else ""}

    def work(self, event_id, section_id, enrollment_id, question_id, anonymous=False):
        with self.rosters.lock(), connect(self.path) as db:
            event, participants = self._context(db, event_id)
            participants.sort(key=lambda p: (p["section_id"], p["enrollment_id"]))
            participant = self._participant(participants, section_id, enrollment_id)
            rubric = self._student_rubric(db, event_id, participant)
            source = self._source(db, event, participant, rubric)
            question = next((q for q in rubric["questions"] if q["id"] == question_id), None)
            if not question:
                raise NotFound("Question not found in the current rubric.")
            summary = self._summary(db, event, participant, rubric, anonymous, participants.index(participant) + 1)
            history = []
            for row in db.execute("""SELECT id,revision,rubric_id,assembly_revision_id,assembly_sha256,score,feedback,
                criterion_ids_json,created_at FROM exam_mark_revisions WHERE event_id=? AND section_id=? AND enrollment_id=?
                AND question_id=? ORDER BY revision DESC""", (event_id, section_id, enrollment_id, question_id)):
                record = dict(row)
                record["criterion_ids"] = json.loads(record.pop("criterion_ids_json"))
                history.append(record)
            page_map = {page["page_number"]: page for page in source["pages"]}
            question_regions = [{"page": item["page"], "region": item["region"],
                                 "raster_url": page_map.get(item["page"], {}).get("raster_url"),
                                 "raster_sha256": page_map.get(item["page"], {}).get("raster_sha256"),
                                 "scan_page_id": page_map.get(item["page"], {}).get("scan_page_id"),
                                 "missing": item["page"] not in page_map} for item in _question_regions(question)]
            return {"participant": summary, "question": question, "version": summary["version"], "rubric_id": rubric["id"],
                    "assembly_revision_id": source["id"], "assembly_sha256": source["selection_sha256"],
                    "template_revision_id": rubric["template_revision_id"], "template": rubric["template"],
                    "pages": source["pages"], "region": question["region"],
                    "question_regions": question_regions,
                    "page_missing": any(region["missing"] for region in question_regions),
                    "partial": bool(source["partial"]), "mark": summary["marks"].get(question_id), "history": history,
                    "anonymity_note": "Roster names are hidden; scan images may identify the student." if anonymous else ""}

    def _write_context(self, db, event_id, data, *, source=True):
        event, participants = self._context(db, event_id)
        # The verified roster owner is read before the catalog write transaction;
        # the lifecycle lock keeps the resulting membership/source view stable.
        db.execute("BEGIN IMMEDIATE")
        participant = self._participant(participants, data.get("section_id"), data.get("enrollment_id"))
        rubric = self._student_rubric(db, event_id, participant)
        state = self._state(db, event_id, participant)
        _version(data, state["version"])
        paper = self._source(db, event, participant, rubric, data.get("assembly_revision_id")) if source else None
        db.execute("INSERT OR IGNORE INTO exam_grade_states(event_id,section_id,enrollment_id,updated_at) VALUES(?,?,?,?)",
                   (event_id, participant["section_id"], participant["enrollment_id"], timestamp()))
        return event, participant, rubric, state, paper

    @staticmethod
    def _bump(db, state, *, finalization=None, release=None):
        db.execute("""UPDATE exam_grade_states SET version=version+1,finalized_id=?,released_id=?,updated_at=?
            WHERE event_id=? AND section_id=? AND enrollment_id=?""",
            (finalization, release, timestamp(), state["event_id"], state["section_id"], state["enrollment_id"]))

    def save_mark(self, event_id, data):
        with self.rosters.lock(), connect(self.path) as db:
            event, participant, rubric, state, source = self._write_context(db, event_id, data)
            if not data.get("assembly_revision_id"):
                raise ValueError("Save the exact reviewed assembly revision shown while grading.")
            if state["finalized_id"]:
                raise Conflict("Reopen this student's finalized grade with a reason before changing marks.")
            question = next((q for q in rubric["questions"] if q["id"] == data.get("question_id")), None)
            if not question:
                raise ValueError("Choose a question in the current rubric.")
            score = _number(data.get("score"), "Score", question["max_points"])
            criteria = data.get("criterion_ids", [])
            allowed = {c["id"]: c for c in question["criteria"]}
            if not isinstance(criteria, list) or any(not isinstance(c, str) for c in criteria) or len(set(criteria)) != len(criteria) or any(c not in allowed for c in criteria):
                raise ValueError("Choose distinct criteria from this question's rubric.")
            if allowed and not math.isclose(score, sum(allowed[c]["points"] for c in criteria), abs_tol=1e-8):
                raise ValueError("The score must equal the points awarded by the selected rubric criteria.")
            feedback = _text(data.get("feedback", ""), "Feedback", 12000, blank=True)
            observed = {p["page_number"] for p in source["pages"]}
            missing = any(region["page"] not in observed for region in _question_regions(question))
            if missing and data.get("acknowledge_missing") is not True:
                raise Conflict("This question's page is missing from the reviewed partial submission. Explicitly acknowledge that before assigning a score.")
            revision = db.execute("""SELECT COALESCE(MAX(revision),0)+1 FROM exam_mark_revisions
                WHERE event_id=? AND section_id=? AND enrollment_id=? AND question_id=?""",
                (event_id, participant["section_id"], participant["enrollment_id"], question["id"])).fetchone()[0]
            mark_id = str(uuid.uuid4())
            db.execute("INSERT INTO exam_mark_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (mark_id, event_id, participant["section_id"], participant["enrollment_id"], question["id"], revision,
                        rubric["id"], source["id"], source["selection_sha256"], score, feedback, encoded(criteria), int(missing), timestamp()))
            self._bump(db, state)
            self._event_log(db, event_id, "mark_saved", {"mark_id": mark_id, "question_id": question["id"]}, participant["section_id"], participant["enrollment_id"])
            return self._summary(db, event, participant, rubric)

    def finalize(self, event_id, data):
        with self.rosters.lock(), connect(self.path) as db:
            event, participant, rubric, state, source = self._write_context(db, event_id, data)
            if not data.get("assembly_revision_id"):
                raise ValueError("Finalize the exact reviewed assembly revision shown while grading.")
            if state["finalized_id"]:
                raise Conflict("This grade is already finalized. Reopen it explicitly before finalizing again.")
            marks = self._marks(db, event_id, participant)
            if any(not self._current(marks.get(q["id"]), rubric, source) for q in rubric["questions"]):
                raise Conflict("Grade every question against the current rubric and reviewed submission before finalizing.")
            snapshot = {"event_id": event_id, "section_id": participant["section_id"], "enrollment_id": participant["enrollment_id"],
                        "rubric_id": rubric["id"], "assignment_version_id": rubric["assignment_version_id"],
                        "assembly_revision_id": source["id"], "assembly_sha256": source["selection_sha256"],
                        "questions": [{"id": q["id"], "label": q["label"], "score": marks[q["id"]]["score"],
                                       "max_points": q["max_points"], "feedback": marks[q["id"]]["feedback"],
                                       "criterion_ids": marks[q["id"]]["criterion_ids"], "criteria": q["criteria"],
                                       "mark_id": marks[q["id"]]["id"]} for q in rubric["questions"]]}
            snapshot["total"] = sum(q["score"] for q in snapshot["questions"])
            snapshot["max_points"] = sum(q["max_points"] for q in snapshot["questions"])
            fid = str(uuid.uuid4())
            db.execute("INSERT INTO exam_grade_finalizations VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (fid, event_id, participant["section_id"], participant["enrollment_id"], rubric["id"], source["id"],
                        source["selection_sha256"], encoded(snapshot), sha(encoded(snapshot).encode()), timestamp()))
            self._bump(db, state, finalization=fid)
            self._event_log(db, event_id, "grade_finalized", {"finalization_id": fid}, participant["section_id"], participant["enrollment_id"])
            return self._summary(db, event, participant, rubric)

    def reopen(self, event_id, data):
        reason = _text(data.get("reason"), "Reopening reason", 2000)
        with self.rosters.lock(), connect(self.path) as db:
            event, participant, rubric, state, _ = self._write_context(db, event_id, data, source=False)
            if not state["finalized_id"]:
                raise Conflict("Only a finalized grade needs reopening.")
            self._event_log(db, event_id, "grade_reopened", {"reason": reason, "finalization_id": state["finalized_id"], "release_id": state["released_id"]}, participant["section_id"], participant["enrollment_id"])
            self._bump(db, state)
            return self._summary(db, event, participant, rubric)

    @staticmethod
    def _release_row(row):
        snapshot = json.loads(row["snapshot_json"])
        if sha(encoded(snapshot).encode()) != row["sha256"]:
            raise Conflict("The released feedback failed its retained checksum check.")
        return {**snapshot, "id": row["id"], "finalization_id": row["finalization_id"], "sha256": row["sha256"], "created_at": row["created_at"]}

    def release(self, event_id, data):
        request_id = _text(data.get("request_id"), "Release request ID", 200)
        request_hash = sha(encoded({key: data.get(key) for key in ("section_id", "enrollment_id", "version") } | {"event_id": event_id}).encode())
        with self.rosters.lock(), connect(self.path) as db:
            previous = db.execute("SELECT * FROM exam_grade_releases WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise Conflict("This release request ID was already used for different feedback.")
                return self._release_row(previous)
            event, participant, rubric, state, source = self._write_context(db, event_id, data)
            finalization = db.execute("SELECT * FROM exam_grade_finalizations WHERE id=?", (state["finalized_id"],)).fetchone()
            if not finalization or not self._current(finalization, rubric, source):
                raise Conflict("Finalize all marks against the current reviewed source and rubric before releasing feedback.")
            if state["released_id"]:
                raise Conflict("This finalized grade has already been released.")
            snapshot = json.loads(finalization["snapshot_json"])
            if sha(encoded(snapshot).encode()) != finalization["sha256"]:
                raise Conflict("The finalized grade failed its retained checksum check.")
            rid = str(uuid.uuid4())
            db.execute("INSERT INTO exam_grade_releases VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (rid, event_id, participant["section_id"], participant["enrollment_id"], finalization["id"], request_id,
                        request_hash, finalization["snapshot_json"], finalization["sha256"], timestamp()))
            self._bump(db, state, finalization=finalization["id"], release=rid)
            self._event_log(db, event_id, "feedback_released", {"release_id": rid}, participant["section_id"], participant["enrollment_id"])
            return self._release_row(db.execute("SELECT * FROM exam_grade_releases WHERE id=?", (rid,)).fetchone())

    def feedback(self, event_id, release_id):
        """Only current, explicitly released feedback is eligible for distribution."""
        with self.rosters.lock(), connect(self.path) as db:
            snapshot = self.release_snapshot(release_id, db=db)
            if snapshot["event_id"] != event_id:
                raise NotFound("Released feedback not found in this exam.")
            return snapshot

    def release_snapshot(self, release_id, *, db=None):
        """Read a current release; supplied-connection callers hold lifecycle lock."""
        if db is None:
            with self.rosters.lock(), connect(self.path) as connection:
                return self.release_snapshot(release_id, db=connection)
        row = db.execute("SELECT * FROM exam_grade_releases WHERE id=?", (release_id,)).fetchone()
        if not row:
            raise NotFound("Released feedback not found.")
        event, participants = self._context(db, row["event_id"])
        participant = self._participant(participants, row["section_id"], row["enrollment_id"])
        state = self._state(db, event["id"], participant)
        rubric = self._student_rubric(db, event["id"], participant)
        source = self._source(db, event, participant, rubric)
        finalization = db.execute("SELECT * FROM exam_grade_finalizations WHERE id=?", (state["finalized_id"],)).fetchone()
        if state["released_id"] != release_id or not self._current(finalization, rubric, source):
            raise Conflict("This release was reopened or its rubric/submission changed. Review and release current feedback before distributing it.")
        return self._release_row(row)

    def export(self, event_id):
        """Current released results only; draft, stale and reopened grades stay out."""
        with self.rosters.lock(), connect(self.path) as db:
            event, participants = self._context(db, event_id)
            rubrics = [self._rubric(db, event_id, row[0]) for row in db.execute("SELECT assignment_version_id FROM exam_grading_rubrics WHERE event_id=?", (event_id,))]
            question_labels = {}
            for rubric in rubrics:
                for question in rubric["questions"]:
                    question_labels.setdefault(question["id"], question["label"])
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(["Student", "Section", "Enrollment ID", "Document version", "Score", "Maximum", *[_safe_csv(label) for label in question_labels.values()], "Release ID"])
            for participant in participants:
                rubric = self._student_rubric(db, event_id, participant)
                state = self._state(db, event_id, participant)
                if not state["released_id"]:
                    continue
                try:
                    source = self._source(db, event, participant, rubric)
                except (Conflict, Forbidden):
                    continue
                finalization = db.execute("SELECT * FROM exam_grade_finalizations WHERE id=?", (state["finalized_id"],)).fetchone()
                if not self._current(finalization, rubric, source):
                    continue
                release = db.execute("SELECT * FROM exam_grade_releases WHERE id=?", (state["released_id"],)).fetchone()
                snapshot = self._release_row(release)
                by_id = {q["id"]: q for q in snapshot["questions"]}
                writer.writerow([_safe_csv(participant.get("name", "Student")), participant.get("section_number", ""),
                                 participant["enrollment_id"], snapshot["assignment_version_id"], snapshot["total"], snapshot["max_points"],
                                 *[by_id[qid]["score"] if qid in by_id else "" for qid in question_labels], snapshot["id"]])
            return output.getvalue()
