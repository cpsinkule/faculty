"""The term gradebook's records and the service behind `/api/gradebook` (gradebook/README.md).

Ownership (AGENTS.md, Course data hierarchy): a setup revision and the request log belong to the permanent TERM
(`course_terms.id`); each score revision belongs to one permanent SECTION MEMBERSHIP `(section_id, enrollment_id)` of that
term. A column may reference a course-wide `assignments.id` and, through it, this term's grading of that assignment; nothing
is copied from it, nothing is attached to `courses.id`, and nothing in grading, rosters or accommodations is ever changed here.

Lock order (AGENTS.md, Roster identity and writes): the roster lifecycle lock, then the verified roster payload read
(`rosters.files`, which may reconcile storage with its own catalog writer), and only then a catalog `BEGIN IMMEDIATE`.
Every write is idempotent by its request ID: the same ID with the same content returns what it did, other content is a 409.
JSON never carries a UIN; only the instructor's CSV export does, and imports match on it on the server.
"""
import collections
import csv
import io
import json
import logging
import threading
import time
import uuid
from datetime import date as datetime_date
from decimal import Decimal
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound, ServiceUnavailable

from catalog_migrations import apply_sql, migration_transaction
from course_groups import navigation_id
from course_storage import decode_course, timestamp
from jev import matching as jev_matching
from rosters import connect, term_key
from seating import student_name, term_label

from . import blanks, canvas_confirm, canvas_export, canvas_import, canvas_nightly, canvas_posting, canvas_snapshot, changes as change_log, common, compute, excused_credit, iclicker_after_class, iclicker_credit, iclicker_import, imports, posting_policy, setup as setups, sources, webassign_nightly

log = logging.getLogger(__name__)

MAX_CHANGES = 5000
MAX_NOTE = 1000
MAX_REPLACE = 20000
RECENT = 20
# A column added by linking an assignment is worth what one paper of its grading plan is worth (`sources.maximum`: the pinned
# FRQ questions of one version, the answer key's total, the Exams page's rubric, the largest version for each); 100 points
# when none of that is known yet. The instructor sets the column's points in its settings, and a pull says so whenever a
# student's possible points differ from the column's.
LINK_POINTS = "100"
TERM_ORDER = {"Spring": 1, "Summer I": 2, "Summer (Full)": 2, "Summer II": 3, "Fall": 4}
CHANGED = "The gradebook changed since the preview. Preview again."
STOPPING_SYNC = "Faculty is restarting. Sync again in a minute; nothing was written."
SYNC_SLOW = 20  # seconds: a Sync with Canvas answering slower than this says in the journal where its time went
# What an import preview read from Canvas or iClicker, kept so that Import writes what the preview showed (instructor,
# 2026-10-02, after WebAssign grades syncing into Canvas refused an Import read a second time): by (kind, term, section,
# fingerprint), the latest PREVIEWED_KEEP previews, least recently used first, for PREVIEWED_SECONDS. An outside service's
# snapshot, never the catalog's: Import plans it against the catalog as it is then, so a gradebook change is still a 409.
# Memory only (it holds student names and IDs), read-only once kept.
_PREVIEWED = collections.OrderedDict()
_PREVIEWED_LOCK = threading.Lock()
PREVIEWED_KEEP, PREVIEWED_SECONDS = 4, 30 * 60


def _keep_previewed(key, links, read):
    with _PREVIEWED_LOCK:
        _PREVIEWED[key] = (time.monotonic(), links, read)
        _PREVIEWED.move_to_end(key)
        while len(_PREVIEWED) > PREVIEWED_KEEP:
            _PREVIEWED.popitem(last=False)


def _previewed(key, drop=False):
    """(links, read) the preview of this key read, None when not kept (another process, a restart, too old); `drop` forgets it."""
    with _PREVIEWED_LOCK:
        found = _PREVIEWED.pop(key, None) if drop else _PREVIEWED.get(key)
        if found is None or time.monotonic() - found[0] > PREVIEWED_SECONDS:
            _PREVIEWED.pop(key, None)
            return None
        if not drop:
            _PREVIEWED.move_to_end(key)
        return found[1], found[2]
# The Changes page (README.md "Changes"): entries per page, newest first; a request that wrote at most INLINE_CELLS cells (of
# the section) lists them with the page, a larger one when the instructor opens it.
CHANGES_PAGE = 50
INLINE_CELLS = 5
# A setup revision an import or a certification wrote shares that request's ID (a certification's column adds ":column"): the
# Changes page shows it with that request, not on its own.
ATTACHED = """SELECT s.id AS setup_id, r.id AS request_ref FROM gradebook_setups s JOIN gradebook_requests r ON r.term_id=s.term_id
    AND r.request_id IN (s.request_id, CASE WHEN s.request_id LIKE '%:column' THEN substr(s.request_id, 1, length(s.request_id) - 7) END)
    WHERE s.term_id=:term"""
# The term's setup revisions and requests, newest first. On a section tab a request is listed when it wrote one of the section's
# cells, wrote a setup revision (shared by every section), or wrote nothing and was not started on another section's tab.
ENTRIES = f"""WITH attached AS ({ATTACHED}), entries AS (
    SELECT 'request' AS type, r.id, r.created_at, r.rowid AS position FROM gradebook_requests r WHERE r.term_id=:term
        AND (:section IS NULL
             OR EXISTS (SELECT 1 FROM gradebook_scores g WHERE g.request_ref=r.id AND g.section_id=:section)
             OR EXISTS (SELECT 1 FROM attached a WHERE a.request_ref=r.id)
             OR (NOT EXISTS (SELECT 1 FROM gradebook_scores g WHERE g.request_ref=r.id)
                 AND COALESCE(json_extract(r.summary_json, '$.section_id'), :section) = :section))
    UNION ALL
    SELECT 'setup', s.id, s.created_at, s.rowid FROM gradebook_setups s WHERE s.term_id=:term AND s.id NOT IN (SELECT setup_id FROM attached))
SELECT * FROM entries WHERE :at IS NULL OR (created_at, type, position) < (:at, :type, :position)
ORDER BY created_at DESC, type DESC, position DESC LIMIT :limit"""


class StaleCells(Conflict):
    """Some changed cells were saved from another window after the browser read them: the whole batch is refused."""

    def __init__(self, stale):
        super().__init__("Some of these cells were changed in another window since you opened the gradebook. "
                         "Reload to see them, then make your changes again.")
        self.stale = stale


def _request_id(value, label="Request ID"):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} must be 1 to 128 characters.")
    return value


def _object(data, allowed, label):
    if not isinstance(data, dict):
        raise ValueError(f"Send the {label} as a JSON object.")
    if set(data) - set(allowed):
        raise ValueError(f"The {label} has an unsupported field.")
    return data


def _plain(value):
    """A JSON-ready copy (sets become sorted lists) so an options object can be fingerprinted."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _term(db, term_id):
    row = db.execute("SELECT * FROM course_terms WHERE id=?", (term_id,)).fetchone() if isinstance(term_id, str) else None
    if not row or row["deleted_at"]:
        raise NotFound("This term's gradebook no longer exists.")
    return dict(row)


def _setup(db, term_id):
    """The term's latest setup revision (the one that counts) and its revision facts; the default before the first save."""
    row = db.execute("SELECT * FROM gradebook_setups WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone()
    if not row:
        return setups.default_setup(), {"revision": None, "created_at": None, "actor": None}
    return json.loads(row["setup_json"]), {"revision": row["revision"], "created_at": row["created_at"], "actor": row["actor"]}


def _cell(row):
    return {"id": row["id"], "status": row["status"], "points": row["points"], "note": row["note"],
            "source": row["source"], "actor": row["actor"], "at": row["created_at"]}


def _cells(db, term_id):
    """Every cell's latest revision (the highest seq), cleared ones included: `{item_id: {row: cell}}`."""
    cells = {}
    for row in db.execute("""SELECT g.* FROM gradebook_scores g JOIN (SELECT item_id,section_id,enrollment_id,MAX(seq) seq
            FROM gradebook_scores WHERE term_id=? GROUP BY item_id,section_id,enrollment_id) l
            ON l.item_id=g.item_id AND l.section_id=g.section_id AND l.enrollment_id=g.enrollment_id AND l.seq=g.seq
            WHERE g.term_id=?""", (term_id, term_id)):
        cells.setdefault(row["item_id"], {})[common.row_key(row["section_id"], row["enrollment_id"])] = _cell(row)
    return cells


def _visible(cells):
    """Cells as the view shows them: a cleared cell is an empty one."""
    visible = {}
    for item_id, rows in cells.items():
        kept = {row: cell for row, cell in rows.items() if cell["status"] != "cleared"}
        if kept:
            visible[item_id] = kept
    return visible


def _latest_id(db, term_id, item_id, row):
    section_id, enrollment_id = common.split_row(row)
    found = db.execute("""SELECT id FROM gradebook_scores WHERE term_id=? AND item_id=? AND section_id=? AND enrollment_id=?
        ORDER BY seq DESC LIMIT 1""", (term_id, item_id, section_id, enrollment_id)).fetchone()
    return found["id"] if found else None


def _unchanged(cell, value):
    """Whether a typed change says exactly what the cell already holds (status, exact points and note)."""
    if value["status"] == "cleared":
        return cell is None or cell["status"] == "cleared"
    if cell is None or cell["status"] != value["status"] or cell["note"] != value["note"]:
        return False
    return value["status"] != "scored" or Decimal(cell["points"]) == Decimal(value["points"])


def _change(value):
    """One typed change, checked for shape only; what it may change is checked against the term inside the transaction."""
    if not isinstance(value, dict) or set(value) - {"item_id", "row", "base", "status", "points", "note"}:
        raise ValueError("Each change names a column, a student, the cell's revision and a status.")
    item_id = value.get("item_id")
    if not isinstance(item_id, str) or not 1 <= len(item_id) <= 100:
        raise ValueError("Name the column of each change.")
    row = value.get("row")
    common.split_row(row)
    base = value.get("base")
    if base is not None and (not isinstance(base, str) or not 1 <= len(base) <= 64):
        raise ValueError("Name the revision of each cell as the gradebook showed it.")
    status = value.get("status")
    if status not in common.STATUSES:
        raise ValueError("A cell is scored, excused, missing or cleared.")
    clean = {"item_id": item_id, "row": row, "base": base, "status": status}
    if status == "scored":
        clean["points"] = common.parse_points(value.get("points"), "Points")
    elif value.get("points") not in (None, ""):
        raise ValueError("Only a scored cell has points.")
    if "note" in value:
        note = value["note"]
        if not isinstance(note, str) or "\x00" in note or len(note.strip()) > MAX_NOTE:
            raise ValueError(f"A note has at most {MAX_NOTE} characters.")
        clean["note"] = note.strip()
    return clean


def _public_plan(plan):
    """An import plan as the browser sees it: never a UIN, even one read from the uploaded file."""
    result = dict(plan)
    for key in ("changes", "unmatched"):
        if isinstance(result.get(key), list):
            result[key] = [{k: v for k, v in entry.items() if k != "uin"} if isinstance(entry, dict) else entry for entry in result[key]]
    return result


def _new_item(title, category_id, points, **extra):
    return {"title": title, "category_id": category_id, "points": points, "due_date": "", "counts": True, "extra_credit": False,
            "archived": False, "sections": [], "assignment_id": None, "link": None, "notes": "", **extra}


def _proposed(value):
    """A pull's or an import's proposed value, in the canonical form a score revision stores. `cleared` is proposed only by a
    certified plan, for a cell an earlier certification wrote whose student the latest one left out (critique C11)."""
    if not isinstance(value, dict) or value.get("status") not in ("scored", "excused", "missing", "cleared"):
        raise ValueError("A proposed grade is scored, excused or missing.")
    if value["status"] == "scored":
        return {"status": "scored", "points": common.parse_points(value.get("points"), "Proposed points")}
    return {"status": value["status"], "points": None}


def protect_references(db, previous, replacement=None):
    """Keep a visible offering for every term with a gradebook and every section number with scores.

    Offering identity edits (code, term/year, sections) create different permanent owners. They must not silently detach
    a gradebook from its term or scores from their section; the instructor can archive the offering by marking it not
    current. The caller holds the roster lifecycle lock and the catalog transaction.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gradebook_setups'").fetchone():
        return
    key = term_key(previous)
    term = db.execute("SELECT id FROM course_terms WHERE definition_id=? AND term=? AND term_other=? AND year=?", key).fetchone()
    if not term:
        return
    kept = db.execute("""SELECT EXISTS(SELECT 1 FROM gradebook_setups WHERE term_id=?)
        OR EXISTS(SELECT 1 FROM gradebook_scores WHERE term_id=?)""", (term["id"], term["id"])).fetchone()[0]
    if not kept:
        return
    used = {row[0] for row in db.execute("""SELECT DISTINCT s.number FROM gradebook_scores g
        JOIN course_sections s ON s.id=g.section_id WHERE g.term_id=?""", (term["id"],))}
    offerings = [decode_course(row) for row in db.execute("SELECT * FROM courses WHERE deleted_at IS NULL AND id!=?", (previous["id"],))]
    if replacement:
        offerings.append(replacement)
    covering = [offering for offering in offerings if offering.get("definition_id") and term_key(offering) == key]
    covered = {number for offering in covering for number in offering["sections"]}
    if not covering or used - covered:
        raise Conflict("This change would detach a gradebook. Keep its course code, term/year and sections; "
                       "mark the course not current to archive it.")


def calendar_events(db, start, end):
    """Each current setup's column due dates in [start, end], as read-only deadlines of their term (`workspace_calendar`)."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gradebook_setups'").fetchone():
        return []
    from workspace_calendar import source_event
    events, numbers = [], {}
    for row in db.execute("""SELECT g.term_id,g.setup_json,t.definition_id,t.code,t.term,t.term_other,t.year
            FROM gradebook_setups g JOIN course_terms t ON t.id=g.term_id WHERE t.deleted_at IS NULL
            AND g.revision=(SELECT MAX(x.revision) FROM gradebook_setups x WHERE x.term_id=g.term_id)"""):
        for item in json.loads(row["setup_json"]).get("items", []):
            due = item.get("due_date") or ""
            if item.get("archived") or not due or not start <= due <= end:
                continue
            if row["term_id"] not in numbers:
                numbers[row["term_id"]] = {section: number for section, number in db.execute(
                    "SELECT id,number FROM course_sections WHERE term_id=?", (row["term_id"],))}
            sections = sorted(numbers[row["term_id"]][section] for section in item.get("sections") or [] if section in numbers[row["term_id"]])
            event = source_event("gradebook", {**dict(row), "id": item["id"], "date": due, "time": "", "location": ""},
                                 title=f"{item['title']} due", sections=sections, href=f"#gradebook/{row['term_id']}?item={item['id']}")
            event["category"] = "deadline"
            events.append(event)
    return events


def _cursor(value):
    """Where an older page starts: the `next` of the page before ("<created_at>|<type>|<position>"), or None for the newest."""
    if value in (None, ""):
        return None
    parts = value.split("|") if isinstance(value, str) and len(value) <= 120 else []
    if len(parts) != 3 or not parts[0] or parts[1] not in ("request", "setup") or not parts[2].isdigit():
        raise ValueError("Ask for older changes with the page's own Show older button.")
    return parts[0], parts[1], int(parts[2])


def _section_of(sections, section_id):
    """A section of the term (`sections`: {id: number}) to narrow a read to, or None for the whole term."""
    if section_id in (None, ""):
        return None
    if section_id not in sections:
        raise ValueError("Choose a section of this term.")
    return section_id


def _written(db, refs, section=None):
    """{request: [cell]} of the cells these requests wrote (only `section`'s when given), in the order written, each with the
    revision it replaced: `{item_id, row, seq, before: {status, points, note}|None, after: {status, points, note}}`."""
    found = {}
    if not refs:
        return found
    marks = ",".join("?" * len(refs))
    for row in db.execute(f"""SELECT g.request_ref,g.item_id,g.section_id,g.enrollment_id,g.seq,g.status,g.points,g.note,
            p.status AS was_status,p.points AS was_points,p.note AS was_note FROM gradebook_scores g
            LEFT JOIN gradebook_scores p ON p.id=g.replaces_id WHERE g.request_ref IN ({marks}){" AND g.section_id=?" if section else ""}
            ORDER BY g.rowid""", [*refs, *([section] if section else [])]):
        found.setdefault(row["request_ref"], []).append({
            "item_id": row["item_id"], "row": common.row_key(row["section_id"], row["enrollment_id"]), "seq": row["seq"],
            "before": {"status": row["was_status"], "points": row["was_points"], "note": row["was_note"]} if row["was_status"] else None,
            "after": {"status": row["status"], "points": row["points"], "note": row["note"]}})
    return found


def _recent(db, term_id):
    """The term's latest writes (score batches, imports, pulls), newest first: the View's `requests`."""
    return [{"id": row["id"], "kind": row["kind"], "created_at": row["created_at"], "actor": row["actor"], "summary": json.loads(row["summary_json"])}
            for row in db.execute("SELECT * FROM gradebook_requests WHERE term_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?", (term_id, RECENT))]


class GradebookService:
    def __init__(self, rosters, frq=None, accommodations=None, canvas=None, iclicker=None):
        self.rosters, self.frq, self.accommodations, self.path = rosters, frq, accommodations, rosters.path
        self.canvas = canvas  # CanvasService: each linked section's Canvas course, for "Sync with Canvas"
        self.iclicker = iclicker  # IClickerService: reads each linked section's iClicker course for "Import from iClicker"
        self._cancel_sends = set()  # Canvas sends asked to stop (the ones running are in self.canvas.writes)
        self._syncs = set()  # (term, request ID) of each Sync with Canvas still working: A LOST ANSWER asks after it
        self.canvas_content = None  # CanvasContentService (app.py): COLUMN ASSIGNMENTS, Faculty's Canvas copy
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "gradebook", migration.stem, migration.read_text(encoding="utf-8"))

    def columns_changed(self, term_id, actor="workspace"):
        """COLUMN ASSIGNMENTS (docs/decisions/canvas.md; requirement, 2026-10-06): after a write that may have added a column, each of Faculty's columns without one
        gets its assignment in Faculty's Canvas copy right away (canvas/column_assignments.py, through the copy's own save; Canvas
        is not contacted). Called once the write's transaction is over; a failure is logged and never fails the write."""
        if self.canvas_content is None:
            return []
        self.link_copy_columns_quietly(term_id, actor)
        from canvas.column_assignments import adopt_quietly
        return adopt_quietly(self.canvas_content, term_id, actor)

    def link_copy_columns(self, term_id, actor="workspace"):
        """GRADED IN FACULTY, LINKED (docs/decisions/canvas.md COLUMN ASSIGNMENTS; requirement, 2026-11-07): each column Faculty's Canvas copy ties
        to one of this course's Faculty assignments (`canvas.column_assignments.grading_links`) is linked to its grading, as Link and
        Certify link one, in ONE setup revision (request `grading-link:<uuid>`). Its empty cells then count only once its plan is
        certified, and it posts by the category's "when certified" rule, never by its due date. Returns the titles linked."""
        if self.canvas_content is None:
            return []
        from canvas.column_assignments import grading_links
        with self.rosters.lock():
            with connect(self.path) as db:
                setup, _ = _setup(db, term_id)
                content, _, _ = self.canvas_content._latest(db, term_id)
                if not grading_links(db, term_id, setup, content, self._grading_parts(db, term_id)):
                    return []
            context = self._context(term_id)
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                setup, meta = _setup(db, term_id)
                content, _, _ = self.canvas_content._latest(db, term_id)
                found = grading_links(db, term_id, setup, content, self._grading_parts(db, term_id))
                if not found:
                    return []
                items = [{**item, "assignment_id": found[item["id"]][0], "link": {"kind": "grading", "parts": found[item["id"]][1]}}
                         if item["id"] in found else item for item in setup.get("items", [])]
                normalized = self._normalize(db, context, {**setup, "items": items}, setup)
                self._record_setup(db, term_id, meta["revision"], normalized, f"grading-link:{uuid.uuid4().hex}", common.digest(found), actor)
        return [item["title"] for item in setup.get("items", []) if item["id"] in found]

    def link_copy_columns_quietly(self, term_id, actor="workspace"):
        """`link_copy_columns` after a write or before a sync: a failure is logged and never fails what called it."""
        try:
            return self.link_copy_columns(term_id, actor)
        except Exception:  # noqa: BLE001 - the next write or sync tries again
            log.exception("Linking term %s's columns to Faculty's grading failed", term_id)
            return []

    @staticmethod
    def _grading_parts(db, term_id):
        """{assignment of this term's course: the parts its grading in this term has} (`sources.available`, none for an assignment
        not graded yet)."""
        parts = {assignment: (link.get("parts") or []) for assignment, link in sources.available(db, term_id).items()}
        definition = db.execute("SELECT definition_id FROM course_terms WHERE id=?", (term_id,)).fetchone()[0]
        return {row[0]: parts.get(row[0], []) for row in db.execute("SELECT id FROM assignments WHERE definition_id=?", (definition,))}

    # Reading ----------------------------------------------------------------------------------------------------------

    def _context(self, term_id):
        """The term, its sections and its students (names from the VERIFIED roster payload, UINs from the registry).

        The caller holds the roster lock and has no catalog transaction open: the payload read may reconcile storage.
        """
        with connect(self.path) as db:
            term = _term(db, term_id)
            sections = [dict(row) for row in db.execute("SELECT id,number FROM course_sections WHERE term_id=? ORDER BY number,id", (term["id"],))]
            members = [dict(row) for row in db.execute("""SELECT m.section_id,m.enrollment_id,e.uin,s.number,(d.enrollment_id IS NOT NULL) dropped
                FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id JOIN student_enrollments e ON e.id=m.enrollment_id
                LEFT JOIN roster_dropped d ON d.section_id=m.section_id AND d.enrollment_id=m.enrollment_id
                WHERE s.term_id=?""", (term["id"],))]
        with self.rosters.files(term) as root:
            payload = self.rosters.read_payload(root, term["roster_initialized"])
        students = []
        for member in members:
            record = payload["sections"].get(member["section_id"], {}).get("students", {}).get(member["enrollment_id"], {})
            fields = {str(key).casefold().strip(): str(value).strip() for key, value in (record.get("fields") or {}).items()}
            students.append({"row": common.row_key(member["section_id"], member["enrollment_id"]), "section_id": member["section_id"],
                             "enrollment_id": member["enrollment_id"], "section": member["number"], "uin": member["uin"],
                             "name": student_name({**record, "id": member["enrollment_id"]}), "dropped": bool(member["dropped"]),
                             # The roster's email, for matching an iClicker account without a UIN (server only: never in JSON).
                             "email": fields.get("email") or fields.get("email address") or fields.get("e-mail") or ""})
        students.sort(key=lambda student: (student["name"].casefold(), student["section"], student["enrollment_id"]))
        return {"term": term, "sections": sections, "students": students, "by_row": {student["row"]: student for student in students}}

    @staticmethod
    def _scoped(context, section_id):
        """The context narrowed to ONE section of the term, for a pull, an import or a Canvas import started from that section's
        tab (requirement, 2026-09-28): its students only, so nothing is previewed or written for another
        section. `None` or "" keeps the whole term. The setup stays the term's."""
        if section_id in (None, ""):
            return context
        if not isinstance(section_id, str) or not any(section["id"] == section_id for section in context["sections"]):
            raise ValueError("Choose a section of this term.")
        students = [student for student in context["students"] if student["section_id"] == section_id]
        return {**context, "students": students, "by_row": {student["row"]: student for student in students}, "section_id": section_id,
                "all_students": context["students"]}

    @staticmethod
    def _summary(term, sections, students, setup, revision, graded=()):
        """A term's TermSummary. `graded`: the assignments of this term with grading to pull (`sources.graded_by_term`), of which
        `graded_without_column` counts those no live column references (read-only, for the term list)."""
        return {"id": term["id"], "definition_id": term["definition_id"], "code": term["code"], "name": term["name"],
                "group_id": navigation_id({"id": term["id"], "code": term["code"], "term": term["term"],
                                           "term_other": term["term_other"], "year": term["year"]}),
                "term_label": term_label(term), "is_current": bool(term["is_current"]),
                "sections": [{"id": section["id"], "number": section["number"]} for section in sections],
                "students": students, "items": sum(1 for item in setup.get("items", []) if not item.get("archived")), "revision": revision,
                "graded_without_column": sources.without_column(graded, setup)}

    def _results(self, db, term_id, setup, students, cells):
        """The term's results. Under `ungraded: "due"` (GRADEBOOK "ZERO ONCE DUE") the empty cells that count as 0 or wait are read
        first (`blanks.read`: catalog only, on `db`, against the campus date), so the grid, a save's answer and the CSV agree."""
        from scheduling import clock
        found = blanks.read(db, getattr(self.frq, "exams", None), term_id, setup, students, cells, clock.campus_today().isoformat(),
                            self._iclicker_sessions(db, term_id) if setup.get("ungraded") == "due" else ())
        return compute.compute(setup, students=[{"row": student["row"], "section_id": student["section_id"]} for student in students],
                               cells=cells, blanks=found)

    def _extras(self, db, context):
        """{(section_id, enrollment_id): accommodation names and DR-linked assignments} of the students the View shows. Read here,
        not in `_context`: the writes, pulls, imports and exports that share `_context` show neither."""
        extras = {}
        if self.accommodations is None:
            return extras
        rosters = {}
        for student in context["students"]:
            rosters.setdefault(student["section_id"], []).append({"id": student["enrollment_id"]})
        for section_id, students in rosters.items():
            for student in self.accommodations.attach({"section_id": section_id, "students": students}, db=db)["students"]:
                items = (student.get("accommodations") or {}).get("items") or []
                extras[(section_id, student["id"])] = {
                    "accommodations": [item["name"] for item in items],
                    # Only which assignments a current DR-center booking is linked to: no booking detail reaches the grid.
                    "dr": sorted({exam["assignment"]["id"] for exam in student.get("dr_exams", []) if exam.get("assignment")})}
        return extras

    def _view(self, db, context, section_cells=False):
        """The View of `context`. `section_cells` (GET `?section=<id>&cells=section`, opt-in, PERFORMANCE "OPT-IN AND ADDITIVE
        API"): a section's View keeps only that section's rows in `cells` (every column key stays, possibly `{}`) and adds
        `cell_counts`, each column's term-wide count of visible cells, which the shared setup shows. Results already read only
        the section's students, so nothing else changes; without it the body is as before."""
        term, students = context["term"], context["students"]
        extras = self._extras(db, context)
        photos = self.photos.latest(term["id"], db) if getattr(self, "photos", None) else {}  # checksum prefixes only (student_photos)
        setup, meta = _setup(db, term["id"])
        cells = _visible(_cells(db, term["id"]))
        counts = None
        if section_cells and context.get("section_id"):
            counts = {item_id: len(column) for item_id, column in cells.items()}
            rows = context["by_row"]
            cells = {item_id: {row: cell for row, cell in column.items() if row in rows} for item_id, column in cells.items()}
        available = sources.available(db, term["id"])
        # What one paper of each assignment's grading is worth, per part: a few catalog reads per assignment with an exam here.
        maxima = {assignment_id: sources.maxima(db, source) for assignment_id, source in available.items()}
        items = setup.get("items", [])
        assignments = []
        for row in db.execute("SELECT id,title,assignment_key FROM assignments WHERE definition_id=? ORDER BY title COLLATE NOCASE,id",
                              (term["definition_id"],)):
            source = available.get(row["id"]) or {}
            assignments.append({"id": row["id"], "title": row["title"], "key": row["assignment_key"], "plan_id": source.get("plan_id"),
                                "event_id": source.get("event_id"), "parts": list(source.get("parts") or []),
                                "maximum": sources.total(maxima.get(row["id"], {}), source.get("parts")),
                                "items": [item["id"] for item in items if item.get("assignment_id") == row["id"] and not item.get("archived")]})
        pulls = {}
        for row in db.execute("SELECT created_at,actor,summary_json FROM gradebook_requests WHERE term_id=? AND kind='pull' ORDER BY created_at DESC,rowid DESC",
                              (term["id"],)):
            summary = json.loads(row["summary_json"])
            pulls.setdefault(summary.get("item_id"), {"at": row["created_at"], "actor": row["actor"], "counts": summary.get("counts")})
        links = {}
        for item in items:
            if (item.get("link") or {}).get("kind") == "grading" and item.get("assignment_id"):
                source = available.get(item["assignment_id"]) or {}
                links[item["id"]] = {"plan_id": source.get("plan_id"), "event_id": source.get("event_id"),
                                     "parts_available": list(source.get("parts") or []),
                                     "maximum": sources.total(maxima.get(item["assignment_id"], {}), item["link"]["parts"]),
                                     "last_pull": pulls.get(item["id"]),
                                     # Read-only hint: when what this column pulls last changed in grading (catalog timestamps); once
                                     # the plan is certified, when it was last certified (a pull reads the certification).
                                     "grading_changed_at": sources.changed_at(db, source, item["link"]["parts"]) if source else None,
                                     "certification": self._certification_hint(db, source, item["link"]["parts"])}
        # Columns with ANY score revision, cleared ones included: exactly the ones `setup.normalize` refuses to remove.
        scored_items = [row[0] for row in db.execute("SELECT DISTINCT item_id FROM gradebook_scores WHERE term_id=? ORDER BY item_id", (term["id"],))]
        graded = [assignment_id for assignment_id, source in available.items() if source.get("parts")]
        return {"term": self._summary(term, context["sections"], len(students), setup, meta["revision"], graded),
                "sections": [{"id": section["id"], "number": section["number"]} for section in context["sections"]],
                "setup": {**meta, **setup},
                # The enrollment ID (already inside `row`) opens the student's photo and communications record from their record.
                "students": [{**{key: student[key] for key in ("row", "section_id", "enrollment_id", "section", "name")},
                              **extras.get((student["section_id"], student["enrollment_id"]), {"accommodations": [], "dr": []}), "dropped": student["dropped"],
                              **({"photo": photos[student["enrollment_id"]]} if student["enrollment_id"] in photos else {})}
                             for student in students],
                "cells": cells, **({"cell_counts": counts} if counts is not None else {}), "results": self._results(db, term["id"], setup, students, cells),
                "assignments": assignments, "links": links, "requests": _recent(db, term["id"]), "scored_items": scored_items,
                "canvas": self._canvas_summary(db, term["id"], setup), "iclicker": self._iclicker_summary(db, term["id"]),
                # POSTING GRADES: how many of each column's grades Canvas hid from students when Faculty last read it, each column's
                # posting choice or rule and what the next sync does about it (only when any read or choice)
                **({"canvas_posting": posting} if (posting := self._posting_marks(db, term["id"], setup)) else {})}

    def _posting_marks(self, db, term_id, setup):
        chosen = canvas_posting.choices(db, term_id)
        return canvas_posting.marks(db, term_id, canvas_posting.policy(self, db, term_id, setup, chosen), chosen)

    def _canvas_summary(self, db, term_id, setup=None):
        """Catalog only: how many of the term's sections link a Canvas course (whether Sync with Canvas is offered), whether the
        term was ever started from Canvas (`started`: any assignment remembered), the last sync (`last_sync`: when, what it
        sent, how many grades it left held, and how its send ended) and how many cells of the columns Faculty sends changed since
        (`changed_since`). A send that ended with values Canvas did not confirm also says how many are STILL not known to be in
        Canvas (`send.unconfirmed`: CONFIRMED BY A LATER READ, canvas_confirm.py), which is what the page's notice counts."""
        if self.canvas is None:
            return None
        linked = db.execute("""SELECT COUNT(DISTINCT l.section_id) FROM canvas_course_links l JOIN course_sections s ON s.id=l.section_id
            WHERE s.term_id=?""", (term_id,)).fetchone()[0]
        # Leave out (`canvas_skip`) records an import request that imports nothing: not the last import.
        last = db.execute("""SELECT created_at FROM gradebook_requests WHERE term_id=? AND kind='import'
            AND json_extract(summary_json,'$.source')='canvas' AND json_extract(summary_json,'$.skipped') IS NULL
            ORDER BY created_at DESC,rowid DESC LIMIT 1""", (term_id,)).fetchone()
        started = db.execute("SELECT 1 FROM gradebook_canvas_links WHERE term_id=? LIMIT 1", (term_id,)).fetchone() is not None
        # A term's first sync that writes to Canvas starts with one section ticked (the instructor's "one real section first").
        sent_before = db.execute("SELECT 1 FROM gradebook_canvas_sends WHERE term_id=? LIMIT 1", (term_id,)).fetchone() is not None
        courses = {row[0]: row[1] for row in db.execute("""SELECT l.section_id,l.course_id FROM canvas_course_links l JOIN course_sections s
            ON s.id=l.section_id WHERE s.term_id=? AND l.rowid=(SELECT x.rowid FROM canvas_course_links x WHERE x.section_id=l.section_id
            ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1) AND l.course_id IS NOT NULL AND l.course_id!=''""", (term_id,))}
        synced = self._last_sync(db, term_id)
        changed = None
        if synced:
            owned = [item["id"] for item in (setup or _setup(db, term_id)[0]).get("items", []) if canvas_export.owned(item)]
            marks = ",".join("?" * len(owned))
            changed = db.execute(f"""SELECT COUNT(*) FROM (SELECT DISTINCT item_id,section_id,enrollment_id FROM gradebook_scores
                WHERE term_id=? AND created_at>? AND request_ref!=? AND item_id IN ({marks}))""", (term_id, synced["at"], synced["ref"], *owned)).fetchone()[0] if owned else 0
            send = db.execute("SELECT * FROM gradebook_canvas_sends WHERE term_id=? AND request_id=?", (term_id, synced["request_id"])).fetchone()
            shown = {k: v for k, v in self._send_view(send).items() if k in ("id", "status", "progress")} if send else None
            if shown and (shown["progress"].get("failed") or shown["progress"].get("uncertain")):
                shown["unconfirmed"] = canvas_confirm.left(db, term_id, send["id"])  # a later read may have confirmed them
            synced = {key: synced.get(key) for key in ("at", "sending", "held", "taken", "creating")} | (
                {"automatic": synced["automatic"]} if synced.get("automatic") else {}) | ({"send": shown} if shown else {})
        # The morning sync's latest run (canvas_nightly.py; before it, the nightly WebAssign import's), only once there has been one.
        nightly = canvas_nightly.latest(self, db, term_id, self.canvas.status)
        return {"linked_sections": linked, "last_import": last[0] if last else None, "started": started, "sent_before": sent_before,
                "courses": courses, "last_sync": synced,
                **({"changed_since": changed} if changed is not None else {}), **({"nightly": nightly} if nightly else {})}

    def _iclicker_summary(self, db, term_id):
        """Whether "Import from iClicker" is offered (iClicker set up here), which sections link a course (catalog only; the
        section's link is chosen in the import itself) and when scores were last imported."""
        if self.iclicker is None or not self.iclicker.settings.configured:
            return None
        linked = [row[0] for row in db.execute("""SELECT DISTINCT l.section_id FROM iclicker_course_links l JOIN course_sections s ON s.id=l.section_id
            WHERE s.term_id=? ORDER BY l.section_id""", (term_id,))]
        last = db.execute("""SELECT created_at FROM gradebook_requests WHERE term_id=? AND kind='import'
            AND json_extract(summary_json,'$.source')='iclicker' ORDER BY created_at DESC,rowid DESC LIMIT 1""", (term_id,)).fetchone()
        # ICLICKER AFTER CLASS: each linked section's latest after-class run, and whether the term's schedule lets them run.
        from scheduling import service as scheduling
        runs = {section_id: run for section_id in linked if (run := iclicker_after_class.latest(db, section_id))}
        return {"linked_sections": linked, "last_import": last[0] if last else None, "after_class": runs,
                "scheduled": bool(scheduling.current_use(db, term_id))}

    @staticmethod
    def _certification_hint(db, source, parts):
        """{id, created_at, may_have_changed} of the plan's latest certification, or None: a cheap hint (critique D), never the exact
        state, which only Grading's Certification and Export step computes. It compares the certification's catalog-only grade marker
        (`grading.progress.grade_marker`: stored grade content, rubric pins, participants, DR bookings, bubble marker) with the
        catalog now (review F4: approving a grade unchanged moves nothing; a rubric change, an excuse or a DR booking does). A
        certification without the marker falls back to comparing grading's timestamps."""
        found = sources.latest_certification(db, (source or {}).get("plan_id"))
        if not found:
            return None
        try:
            summary = json.loads(found.get("summary_json") or "{}")
        except ValueError:
            summary = {}
        if summary.get("grade_marker"):
            from grading.progress import grade_marker
            try:
                certified_parts = json.loads(found.get("parts_json") or "[]")
            except ValueError:
                certified_parts = []
            changed = grade_marker(db, source["plan_id"], certified_parts) != summary["grade_marker"]
        else:
            live = sources.live_changed_at(db, source, parts)
            certified = sources._instant(found["created_at"])
            changed = bool(live and certified and sources._instant(live) > certified)
        return {"id": found["id"], "created_at": found["created_at"], "may_have_changed": bool(changed)}

    def _render(self, context, section_cells=False):
        with connect(self.path) as db:
            return self._view(db, context, section_cells)

    def catalog(self):
        """Every retained term, current ones first, then the newest: counts only, never a name. Catalog rows alone, so no roster
        lifecycle lock: the list must not wait behind a pull that holds it while it reads a whole exam's grades. Each row also
        carries the term's grading scheme (`scale`, additive: the course Home shows it; GRADEBOOK GRADING SCHEME)."""
        with connect(self.path) as db:
            terms = [dict(row) for row in db.execute("SELECT * FROM course_terms WHERE deleted_at IS NULL")]
            sections = {}
            for row in db.execute("SELECT id,term_id,number FROM course_sections ORDER BY number,id"):
                sections.setdefault(row["term_id"], []).append(dict(row))
            students = dict(db.execute("""SELECT s.term_id,COUNT(*) FROM roster_current m JOIN course_sections s ON s.id=m.section_id
                GROUP BY s.term_id""").fetchall())
            latest = {row["term_id"]: (json.loads(row["setup_json"]), row["revision"]) for row in db.execute(
                """SELECT g.term_id,g.revision,g.setup_json FROM gradebook_setups g
                WHERE g.revision=(SELECT MAX(x.revision) FROM gradebook_setups x WHERE x.term_id=g.term_id)""")}
            graded = sources.graded_by_term(db)
        terms.sort(key=lambda term: (not term["is_current"], -term["year"], -TERM_ORDER.get(term["term"], 0), term["code"], term["id"]))
        summaries = []
        for term in terms:
            setup, revision = latest.get(term["id"], (None, None))
            setup = setup or setups.default_setup()
            summaries.append({**self._summary(term, sections.get(term["id"], []), students.get(term["id"], 0), setup, revision,
                                              graded.get(term["id"], ())), "scale": setup["scale"]})
        return {"terms": summaries}

    def view(self, term_id, section_id=None, section_cells=False):
        """The term's View, or ONE section's (a section tab, 2026-09-28): that section's students and statistics over them only.
        `section_cells` (opt-in) keeps only that section's cells and adds the term-wide `cell_counts` (`_view`)."""
        with self.rosters.lock():
            return self._render(self._scoped(self._context(term_id), section_id), section_cells)

    def history(self, term_id, item_id, row):
        if not isinstance(item_id, str) or not 1 <= len(item_id) <= 100:
            raise ValueError("Name a column of this gradebook.")
        section_id, enrollment_id = common.split_row(row)
        with self.rosters.lock(), connect(self.path) as db:
            _term(db, term_id)
            revisions = [{**{key: row[key] for key in ("id", "seq", "status", "points", "note", "source", "actor", "created_at")},
                          "request": {"kind": row["kind"], "summary": json.loads(row["summary_json"])}}
                         for row in db.execute("""SELECT g.*,r.kind,r.summary_json FROM gradebook_scores g JOIN gradebook_requests r ON r.id=g.request_ref
                             WHERE g.term_id=? AND g.item_id=? AND g.section_id=? AND g.enrollment_id=? ORDER BY g.seq DESC""",
                                               (term_id, item_id, section_id, enrollment_id))]
        return {"revisions": revisions}

    # Changes (README.md "Changes"; requirement, 2026-10-01) -------------------------------------------------------------------

    def changes(self, term_id, section_id=None, before=None):
        """One page of the gradebook's changes, newest first: each setup revision with what it changed from the one before, and
        each score write, import, pull or certification with what made it and how many cells it added, changed or cleared (on a
        section tab, of that section; `elsewhere` counts the rest). A setup revision an import or a certification wrote comes
        with that request. Catalog rows only, so no roster lifecycle lock (like `catalog`): student names and column titles are
        the View's on screen, and this read never waits behind a pull."""
        cursor = _cursor(before)
        with connect(self.path) as db:
            term = _term(db, term_id)
            sections = {identifier: number for identifier, number in db.execute("SELECT id,number FROM course_sections WHERE term_id=?", (term["id"],))}
            section = _section_of(sections, section_id)
            at, kind, position = cursor or (None, None, None)
            rows = db.execute(ENTRIES, {"term": term["id"], "section": section, "at": at, "type": kind, "position": position,
                                        "limit": CHANGES_PAGE + 1}).fetchall()
            page = rows[:CHANGES_PAGE]
            refs = [row["id"] for row in page if row["type"] == "request"]
            marks = ",".join("?" * len(refs))
            requests = {row["id"]: row for row in db.execute(f"SELECT * FROM gradebook_requests WHERE id IN ({marks})", refs)} if refs else {}
            counts = {ref: {"here": 0, "added": 0, "changed": 0, "cleared": 0, "elsewhere": 0, "items": set()} for ref in refs}
            for row in db.execute(f"""SELECT g.request_ref,g.section_id,g.item_id,g.status!='cleared' AS now,
                    (p.id IS NOT NULL AND p.status!='cleared') AS was,COUNT(*) AS n FROM gradebook_scores g
                    LEFT JOIN gradebook_scores p ON p.id=g.replaces_id WHERE g.request_ref IN ({marks}) GROUP BY 1,2,3,4,5""", refs) if refs else ():
                count = counts[row["request_ref"]]
                if section and row["section_id"] != section:
                    count["elsewhere"] += row["n"]
                    continue
                count["here"] += row["n"]
                count["items"].add(row["item_id"])
                count["cleared" if not row["now"] else "changed" if row["was"] else "added"] += row["n"]
            small = [ref for ref in refs if 0 < counts[ref]["here"] <= INLINE_CELLS]
            cells = _written(db, small, section)
            attached = {row["request_ref"]: row["setup_id"] for row in db.execute(ATTACHED, {"term": term["id"]}) if row["request_ref"] in requests}
            wanted = [row["id"] for row in page if row["type"] == "setup"] + list(attached.values())
            revisions = {row["id"]: row for row in db.execute(
                f"SELECT * FROM gradebook_setups WHERE id IN ({','.join('?' * len(wanted))})", wanted)} if wanted else {}
            earlier = sorted({row["revision"] - 1 for row in revisions.values()} - {0})
            previous = {row["revision"]: json.loads(row["setup_json"]) for row in db.execute(
                f"SELECT revision,setup_json FROM gradebook_setups WHERE term_id=? AND revision IN ({','.join('?' * len(earlier))})",
                [term["id"], *earlier])} if earlier else {}
            titles = {identifier: title for identifier, title in db.execute("SELECT id,title FROM assignments WHERE definition_id=?",
                                                                             (term["definition_id"],))}

        def revision(row):
            return {"revision": row["revision"], "changes": change_log.setup_changes(
                previous.get(row["revision"] - 1), json.loads(row["setup_json"]), sections=sections, assignments=titles,
                made_at=row["created_at"])}

        result = []
        for row in page:
            if row["type"] == "setup":
                setup = revisions[row["id"]]
                # The nightly WebAssign import's due dates (webassign_nightly.FOLLOWED) say so; every other entry is as it was.
                automatic = {"automatic": "webassign"} if setup["request_id"].startswith(webassign_nightly.FOLLOWED) else {}
                result.append({"id": row["id"], "type": "setup", "created_at": setup["created_at"], "actor": setup["actor"], **automatic,
                               **revision(setup)})
                continue
            request, count = requests[row["id"]], counts[row["id"]]
            summary = json.loads(request["summary_json"])
            result.append({"id": row["id"], "type": "request", "kind": request["kind"], "origin": change_log.origin(request["kind"], request["request_id"], summary),
                           "created_at": request["created_at"], "actor": request["actor"], "summary": summary,
                           "cells": {key: count[key] for key in ("here", "added", "changed", "cleared", "elsewhere")}, "items": sorted(count["items"]),
                           "detail": cells.get(row["id"], []) if row["id"] in small else None,
                           "setup": revision(revisions[attached[row["id"]]]) if row["id"] in attached else None})
        last = page[-1] if len(rows) > CHANGES_PAGE else None
        return {"changes": result, "next": f"{last['created_at']}|{last['type']}|{last['position']}" if last else None}

    def change_cells(self, term_id, request_ref, section_id=None):
        """Every cell one request wrote (on a section tab, that section's), each with the value it replaced: what the Changes
        page shows when the instructor opens a request too large to list with the page."""
        with connect(self.path) as db:
            term = _term(db, term_id)
            sections = {identifier: number for identifier, number in db.execute("SELECT id,number FROM course_sections WHERE term_id=?", (term["id"],))}
            section = _section_of(sections, section_id)
            if not isinstance(request_ref, str) or not db.execute("SELECT 1 FROM gradebook_requests WHERE id=? AND term_id=?",
                                                                  (request_ref, term["id"])).fetchone():
                raise NotFound("This change is not one of this gradebook's.")
            return {"cells": _written(db, [request_ref], section).get(request_ref, [])}

    # Setup ------------------------------------------------------------------------------------------------------------

    def _normalize(self, db, context, data, base, server=False):
        term = context["term"]
        scored = {row[0] for row in db.execute("SELECT DISTINCT item_id FROM gradebook_scores WHERE term_id=?", (term["id"],))}
        assignments = {row[0] for row in db.execute("SELECT id FROM assignments WHERE definition_id=?", (term["definition_id"],))}
        return setups.normalize(data, base, scored_items=scored, sections={section["id"] for section in context["sections"]},
                                assignments=assignments, server=server)

    @staticmethod
    def _record_setup(db, term_id, revision, setup, request_id, request_sha, actor):
        db.execute("""INSERT INTO gradebook_setups(id,term_id,revision,setup_json,sha256,request_id,request_sha256,actor,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), term_id, (revision or 0) + 1, common.encoded(setup), common.digest(setup),
                                           request_id, request_sha, actor, timestamp()))

    def save_setup(self, term_id, data, actor="workspace"):
        _object(data, ("base_revision", "request_id", "setup", "section_id"), "gradebook setup")
        request_id = _request_id(data.get("request_id"))
        base = data.get("base_revision")
        if base is not None and (type(base) is not int or base < 1):
            raise ValueError("Name the setup revision these changes were made on.")
        if not isinstance(data.get("setup"), dict):
            raise ValueError("Send the gradebook setup.")
        # The request as sent: new columns get server IDs, so the normalized setup of a replay would differ.
        request_sha = common.digest({"base_revision": base, "setup": data["setup"]})
        with self.rosters.lock():
            context = self._context(term_id)
            scoped = self._scoped(context, data.get("section_id"))
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute("SELECT request_sha256 FROM gradebook_setups WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
                if old:
                    if old[0] != request_sha:
                        raise Conflict("This request ID was used for a different gradebook setup.")
                else:
                    current, meta = _setup(db, term_id)
                    if meta["revision"] != base:
                        raise Conflict("The gradebook setup was saved from another window since you opened it. "
                                       "Reload to see it, then make your changes again.")
                    setup = self._normalize(db, context, data["setup"], current)
                    if common.encoded(setup) != common.encoded(current):  # Saving the same setup again records nothing.
                        self._record_setup(db, term_id, meta["revision"], setup, request_id, request_sha, actor)
            self.columns_changed(term_id, actor)  # COLUMN ASSIGNMENTS: a column added here gets its assignment in Faculty's copy
            # A section tab's View (its students, and statistics over them); the setup itself is the term's.
            return self._render(scoped)

    def link_assignment(self, term_id, data, actor="workspace"):
        """A column linked to this term's grading of a course-wide assignment: the existing one, else a new setup revision."""
        _object(data, ("assignment_id", "request_id", "category_id", "section_id"), "link")
        request_id = _request_id(data.get("request_id"))
        assignment_id, category_id = data.get("assignment_id"), data.get("category_id")
        if not isinstance(assignment_id, str) or not 1 <= len(assignment_id) <= 100:
            raise ValueError("Choose an assignment of this course.")
        if category_id is not None and (not isinstance(category_id, str) or not 1 <= len(category_id) <= 100):
            raise ValueError("Choose a category of this gradebook, or none.")
        request_sha = common.digest({"link": assignment_id, "category_id": category_id})
        with self.rosters.lock():
            context = self._context(term_id)
            scoped = self._scoped(context, data.get("section_id"))
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                assignment = db.execute("SELECT id,title FROM assignments WHERE id=? AND definition_id=?",
                                        (assignment_id, context["term"]["definition_id"])).fetchone()
                if not assignment:
                    raise NotFound("This assignment is not one of this course's.")
                old = db.execute("SELECT request_sha256,setup_json FROM gradebook_setups WHERE term_id=? AND request_id=?",
                                 (term_id, request_id)).fetchone()
                if old:
                    if old["request_sha256"] != request_sha:
                        raise Conflict("This request ID was used for a different change.")
                    item_id = next(item["id"] for item in json.loads(old["setup_json"])["items"] if item.get("assignment_id") == assignment_id)
                else:
                    setup, meta = _setup(db, term_id)
                    linked = next((item for item in setup.get("items", []) if item.get("assignment_id") == assignment_id and not item.get("archived")), None)
                    if linked:
                        item_id = linked["id"]
                    else:
                        parts = list((sources.available(db, term_id).get(assignment_id) or {}).get("parts") or []) or ["frq"]
                        item = self._link_item(db, term_id, assignment_id, assignment["title"], parts, category_id)
                        normalized, item_id = self._added(db, context, setup, item)
                        self._record_setup(db, term_id, meta["revision"], normalized, request_id, request_sha, actor)
            self.columns_changed(term_id, actor)  # COLUMN ASSIGNMENTS
            return {"item_id": item_id, **self._render(scoped)}

    @staticmethod
    def _link_item(db, term_id, assignment_id, title, parts, category_id, sections=()):
        """A new column linked to this term's grading of an assignment: titled as the assignment, worth one paper of its plan
        (`sources.maximum`, else LINK_POINTS). Linking an assignment and certifying its grades both make it this way."""
        points = sources.maximum(db, term_id, assignment_id, parts) or LINK_POINTS
        return _new_item(title[:120], category_id, points, assignment_id=assignment_id, link={"kind": "grading", "parts": list(parts)},
                         sections=sorted(sections))

    def _added(self, db, context, setup, item):
        """(normalized setup with `item` appended, the new column's server-made ID)."""
        normalized = self._normalize(db, context, {**setup, "items": [*setup.get("items", []), item]}, setup)
        known = {entry["id"] for entry in setup.get("items", [])}
        return normalized, next(entry["id"] for entry in normalized["items"] if entry["id"] not in known)

    # Certification (Certification and Export, grading/certification.py; requirement, 2026-09-26) ------------------------------

    def certification_options(self, db, context, *, assignment_id, plan_id, event_id, title, records, certified_parts, exam_sections,
                              replace=(), selected=None, category_id=None):
        """Where a certification may record its values, each with the plan of what it would write (pure over the same certified
        `records` and this term's cells: the certify preview passes the prospective snapshot, the certify apply the stored rows, and
        both answer the same, critique C2). Catalog reads only, so it runs inside the certify transaction too.

        Options (`key`, `column` as the certify request names it):
          ok:<id>      the live column linked to the assignment with the certified parts;
          parts:<id>   that column when its linked parts differ, changed to the certified parts (the default), and
          keep:<id>    the same column keeping its parts (critique C10: it takes only the certified values of ITS parts);
          link:<id>    the live column that references the assignment without a link, linked with the certified parts;
          create       no live column references the assignment: a new one, titled as the assignment, worth one paper, for the
                       exam's sections when they are fewer than the term's; its ID is `new:<assignment>` until written;
          use:<id>     or an existing live column that references nothing (a hand-made "Test 1", say), given the assignment and link.
        Without a column of its own the default (the first option) is `create`, unless EXACTLY ONE `use:` column has the
        assignment's title, spaces and case aside (the Canvas-imported "Quiz 4", say): that one comes first and is the default
        (requirement, 2026-10-02). Two such columns, or none: `create` first. The other `use:` columns follow in the setup's order either way.
        Every option's plan is fingerprinted without the Replace ticks; `selected` (a key) gets its rows with `replace` applied.

        Also the setup's `mode` and `categories` ({id, name, weight, counted}; Uncategorized, id None, last), where `counted` says whether
        a column there counts toward Overall (weighted mode counts a category with a weight above 0 and never Uncategorized), and
        `proposed_category_id` for a new column (the Certify dialog preselects it; the instructor chooses): the category of a live
        unlinked column with the assignment's title, else of the most recent live grading-linked column whose title begins with the
        same word ("Quiz 2" proposes its category for "Quiz 3"), else None. A proposal only: `category_id` (None: Uncategorized)
        is still what a new column gets, and it is in no fingerprint."""
        setup, meta = _setup(db, context["term"]["id"])
        cells = _cells(db, context["term"]["id"])
        live = [item for item in setup.get("items", []) if not item.get("archived")]
        own = [item for item in live if item.get("assignment_id") == assignment_id]
        linked = next((item for item in own if (item.get("link") or {}).get("kind") == "grading"), None)
        link = {"kind": "grading", "parts": [part for part in setups.PARTS if part in certified_parts]}
        folded = canvas_export.fold
        # A column Canvas grades (WebAssign's, a Canvas quiz's) is never one Faculty's grading fills (SYNC WITH CANVAS).
        free = [item for item in live if not item.get("assignment_id") and not item.get("link") and item.get("canvas") not in setups.OWNED]
        same = [item for item in free if folded(item["title"]) == folded(title)]
        choices = []
        if linked:
            if linked["link"]["parts"] == link["parts"]:
                choices.append(("ok:" + linked["id"], {"action": "ok", "item_id": linked["id"]}, linked, False))
            else:
                choices.append(("parts:" + linked["id"], {"action": "parts", "item_id": linked["id"], "parts": "certified"}, {**linked, "link": link}, True))
                choices.append(("keep:" + linked["id"], {"action": "ok", "item_id": linked["id"], "parts": "keep"}, linked, False))
        elif own:
            choices.append(("link:" + own[0]["id"], {"action": "link", "item_id": own[0]["id"]}, {**own[0], "link": link}, True))
        else:
            term_sections = {section["id"] for section in context["sections"]}
            only = set(exam_sections) if set(exam_sections) < term_sections else set()
            fresh = {**self._link_item(db, context["term"]["id"], assignment_id, title, link["parts"], category_id, only), "id": "new:" + assignment_id}
            uses = [("use:" + item["id"], {"action": "use", "item_id": item["id"]}, {**item, "assignment_id": assignment_id, "link": link}, True)
                    for item in free]
            # The one same-titled column first, so it is the default (the instructor's decision of 2026-10-02); else a new column.
            named = [choice for choice in uses if len(same) == 1 and choice[0] == "use:" + same[0]["id"]]
            choices += [*named, ("create", {"action": "create"}, fresh, True), *(choice for choice in uses if choice[0] not in {c[0] for c in named})]
        options = []
        for key, column, item, writes_setup in choices:
            existing = cells.get(item["id"], {})
            written = set() if item["id"].startswith("new:") else sources.certified_cells(db, context["term"]["id"], item["id"])
            clear = {row for row, cell in existing.items() if cell["id"] in written}
            totals = sources.certified_totals(records, item["link"]["parts"], certified_parts=certified_parts, plan_id=plan_id, event_id=event_id,
                                              clear=clear)
            students = [student for student in context["students"] if setups.applies(item, student["section_id"])]
            shown = [{k: student[k] for k in ("row", "section_id", "section", "name")} for student in students]
            ticks = set(replace) if key == selected else set()
            plan = sources.plan(item, totals, students=shown, cells=existing, options={"replace": ticks, "absent_as_missing": False},
                                revision=meta["revision"])
            outside = [common.row_key(r["section_id"], r["enrollment_id"]) for r in records
                       if r["status"] != "left_out" and not setups.applies(item, r["section_id"])]
            kept = [r for r in plan["rows"] if r["proposed"] and common.change_action(r["current"], r["proposed"]) == "kept"]
            options.append({"key": key, "column": column, "item": item, "writes_setup": writes_setup, "plan": plan, "outside": outside, "cells": cells,
                            "kept": [{"row": r["row"], "name": r["name"], "section": r["section"], "current": r["current"], "proposed": r["proposed"],
                                      "reason": r["reason"]} for r in kept],
                            "imported": [{"row": r["row"], "name": r["name"], "section": r["section"], "current": r["current"], "proposed": r["proposed"]}
                                         for r in plan["rows"] if r["action"] == "update" and (r["current"] or {}).get("source") == "import"],
                            "cleared": [{"row": r["row"], "name": r["name"], "section": r["section"], "current": r["current"]}
                                        for r in plan["rows"] if (r["proposed"] or {}).get("status") == "cleared" and r["action"] == "update"]})
        mode, known = setup.get("mode") or "points", {category["id"] for category in setup.get("categories", [])}
        categories = [{"id": category["id"], "name": category["name"], "weight": category["weight"],
                       "counted": mode != "weighted" or Decimal(category["weight"]) > 0} for category in setup.get("categories", [])]
        categories.append({"id": None, "name": "Uncategorized", "weight": None, "counted": mode == "points"})
        first = (folded(title).split() or [""])[0]
        alike = [item for item in reversed(live) if (item.get("link") or {}).get("kind") == "grading" and (folded(item["title"]).split() or [""])[0] == first]
        proposed = next((item["category_id"] for item in same + alike if item.get("category_id") in known), None)
        return {"revision": meta["revision"], "setup": setup, "cells": cells, "options": options, "mode": mode, "categories": categories,
                "proposed_category_id": proposed}

    def prepare_certification(self, db, context, *, term_id, option, revision, cells, request_id):
        """Phase one of recording a certification's values, INSIDE the caller's `BEGIN IMMEDIATE` under the roster lifecycle lock,
        before anything is written: the setup revision and every cell the chosen `option` replaces must be what the read saw
        (409 otherwise, as a pull), the request IDs must be unused, and the column's setup revision (when the option needs one) is
        normalized now, so the new column's ID and the gradebook request's ID are known BEFORE the immutable certification row that
        names them is inserted. Writes nothing. The answer is the receipt `gradebook_json` keeps, plus what `record_certification`
        writes (`setup`, `writes`)."""
        setup, meta = _setup(db, term_id)
        item = option["item"]
        writes = [(row["row"], _proposed(row["proposed"])) for row in option["plan"]["rows"] if row["action"] in ("add", "update")]
        if meta["revision"] != revision or any(
                _latest_id(db, term_id, item["id"], row) != (cells.get(item["id"], {}).get(row) or {}).get("id") for row, _ in writes):
            raise Conflict(CHANGED)
        if db.execute("SELECT 1 FROM gradebook_requests WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone() or \
                db.execute("SELECT 1 FROM gradebook_setups WHERE term_id=? AND request_id=?", (term_id, request_id + ":column")).fetchone():
            raise Conflict("This request ID was used for a different change.")
        normalized, created, setup_revision = None, False, meta["revision"]
        if option["writes_setup"]:
            if item["id"].startswith("new:"):
                normalized, item_id = self._added(db, context, setup, {k: v for k, v in item.items() if k != "id"})
                created = True
            else:
                normalized = self._normalize(db, context, {**setup, "items": [item if entry["id"] == item["id"] else entry for entry in setup["items"]]}, setup)
                item_id = item["id"]
            item = setups.find_item(normalized, item_id)
            setup_revision = (meta["revision"] or 0) + 1
        replaced = {row for row, _ in writes}
        receipt = {"term_id": term_id, "item_id": item["id"], "title": item["title"], "created_column": created, "column_action": option["column"]["action"],
                   "column": option["column"], "parts": list(item["link"]["parts"]), "points": item["points"], "setup_revision": setup_revision,
                   "request_id": request_id, "gradebook_request_id": str(uuid.uuid4()), "counts": option["plan"].get("counts") or {},
                   "written": len(writes), "kept": sum(1 for row in option["kept"] if row["row"] not in replaced),
                   "replace": sorted(row["row"] for row in option["kept"] if row["row"] in replaced), "cleared": len(option["cleared"]),
                   "outside": len(option["outside"]), "fingerprint": option["plan"]["fingerprint"]}
        return {"receipt": receipt, "setup": normalized, "item": item, "writes": writes, "revision": meta["revision"]}

    def record_certification(self, db, context, *, prepared, option, stored, request_sha, certification_id, actor="workspace"):
        """Phase two, in the same transaction, after the certification and its student rows are inserted: `stored` is the chosen
        option recomputed from those stored rows (`certification_options` on `sources.certified_records`), which must plan exactly
        what the preview's snapshot did (critique C2), else 409 and everything rolls back. Then the column's setup revision, ONE
        request (kind `pull`, source `grading`, summary naming the certification) and the `add`/`update` cells, by `change_action`'s
        rule; hand-typed and instructor-cleared cells stay unless the instructor ticked Replace."""
        if stored["plan"]["fingerprint"] != option["plan"]["fingerprint"]:
            raise Conflict("The gradebook values of the certification differ from its preview. Check again.")
        receipt, term_id = prepared["receipt"], context["term"]["id"]
        if prepared["setup"] is not None:
            self._record_setup(db, term_id, prepared["revision"], prepared["setup"], receipt["request_id"] + ":column", request_sha, actor)
        item = prepared["item"]
        summary = {"item_id": item["id"], "assignment_id": item["assignment_id"], "plan_id": option["plan"].get("plan_id"), "parts": option["plan"].get("parts"),
                   "counts": receipt["counts"], "written": receipt["written"], "fingerprint": receipt["fingerprint"], "certification_id": certification_id,
                   "source": "certification"}
        reference = self._record_request(db, term_id, receipt["request_id"], request_sha, "pull", summary, actor, identifier=receipt["gradebook_request_id"])
        cells = option["cells"]
        for row, value in prepared["writes"]:
            self._write_proposed(db, context, term_id, item, row, cells, value, "grading", reference, actor)
        return receipt

    # Scores -----------------------------------------------------------------------------------------------------------

    @staticmethod
    def _record_request(db, term_id, request_id, request_sha, kind, summary, actor, identifier=None):
        identifier = identifier or str(uuid.uuid4())
        db.execute("""INSERT INTO gradebook_requests(id,term_id,request_id,request_sha256,kind,summary_json,actor,created_at)
            VALUES(?,?,?,?,?,?,?,?)""", (identifier, term_id, request_id, request_sha, kind, common.encoded(summary), actor, timestamp()))
        return identifier

    @staticmethod
    def _record_score(db, term_id, item_id, row, cell, value, source, request_ref, actor):
        """One new revision of one cell: seq follows the cell's latest revision, which it replaces."""
        section_id, enrollment_id = common.split_row(row)
        identifier = str(uuid.uuid4())
        db.execute("""INSERT INTO gradebook_scores(id,term_id,item_id,section_id,enrollment_id,seq,status,points,note,source,request_ref,
            replaces_id,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (identifier, term_id, item_id, section_id, enrollment_id,
                    db.execute("""SELECT COALESCE(MAX(seq),0)+1 FROM gradebook_scores WHERE term_id=? AND item_id=? AND section_id=?
                        AND enrollment_id=?""", (term_id, item_id, section_id, enrollment_id)).fetchone()[0],
                    value["status"], value["points"], value["note"], source, request_ref, cell["id"] if cell else None, actor, timestamp()))
        return identifier

    @staticmethod
    def _request(db, term_id, request_id, request_sha, kind):
        """A request already recorded under this ID: the same content is a replay, other content a 409."""
        prior = db.execute("SELECT id,request_sha256,kind FROM gradebook_requests WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        if prior and (prior["request_sha256"] != request_sha or prior["kind"] != kind):
            raise Conflict("This request ID was used for a different change.")
        return prior

    def write_scores(self, term_id, data, actor="workspace"):
        _object(data, ("request_id", "changes", "section_id"), "score changes")
        request_id = _request_id(data.get("request_id"))
        changes = data.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
            raise ValueError(f"Send 1 to {MAX_CHANGES} changed cells at a time.")
        clean = [_change(change) for change in changes]
        if len({(change["item_id"], change["row"]) for change in clean}) != len(clean):
            raise ValueError("A cell is changed at most once in one save.")
        request_sha = common.digest({"changes": clean})
        with self.rosters.lock():
            context = self._scoped(self._context(term_id), data.get("section_id"))  # a section tab writes only its own students
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                prior = self._request(db, term_id, request_id, request_sha, "scores")
                reference = prior["id"] if prior else None
                if not prior:
                    setup, _ = _setup(db, term_id)
                    current = _cells(db, term_id)
                    stale, writes = [], []
                    for change in clean:
                        item = setups.find_item(setup, change["item_id"])
                        if not item:
                            raise ValueError("A changed column is no longer in this gradebook. Reload it.")
                        if item.get("archived"):
                            raise ValueError(f"“{item['title']}” is archived. Restore it before changing its scores.")
                        if item.get("canvas") in setups.OWNED:
                            # SYNC WITH CANVAS (requirement, 2026-10-06: Canvas always wins for WebAssign and Canvas quizzes): read-only here.
                            raise ValueError(f"“{item['title']}” is graded in Canvas"
                                             f"{' by WebAssign: change its scores in WebAssign' if item['canvas'] == 'webassign' else ': change its scores in Canvas'}.")
                        student = context["by_row"].get(change["row"])
                        if not student:
                            raise ValueError("A changed row is not a student of this term's roster.")
                        if not setups.applies(item, student["section_id"]):
                            raise ValueError(f"“{item['title']}” does not apply to section {student['section']}.")
                        cell = current.get(change["item_id"], {}).get(change["row"])
                        # The browser sees a cleared cell as empty, so an empty base still matches it.
                        if change["base"] != (cell["id"] if cell else None) and not (change["base"] is None and cell and cell["status"] == "cleared"):
                            stale.append({"item_id": change["item_id"], "row": change["row"]})
                            continue
                        # A change without a note keeps the cell's note; a cleared cell is empty, note included.
                        note = "" if change["status"] == "cleared" else change.get("note", cell["note"] if cell and cell["status"] != "cleared" else "")
                        value = {"status": change["status"], "points": change.get("points"), "note": note}
                        if not _unchanged(cell, value):
                            writes.append((change, cell, value))
                    if stale:
                        raise StaleCells(stale)
                    if writes:  # A save that changes nothing records nothing.
                        statuses = [value["status"] for _, _, value in writes]
                        summary = {"cells": len(writes), "counts": {status: statuses.count(status) for status in common.STATUSES},
                                   "items": sorted({change["item_id"] for change, _, _ in writes})}
                        reference = self._record_request(db, term_id, request_id, request_sha, "scores", summary, actor)
                        for change, cell, value in writes:
                            self._record_score(db, term_id, change["item_id"], change["row"], cell, value, "instructor", reference, actor)
                # What this request wrote (again, for a replay), cleared cells included.
                written = {}
                for row in db.execute("SELECT * FROM gradebook_scores WHERE request_ref=?", (reference,)) if reference else ():
                    written.setdefault(row["item_id"], {})[common.row_key(row["section_id"], row["enrollment_id"])] = _cell(row)
            with connect(self.path) as db:
                setup, _ = _setup(db, term_id)
                cells = _visible(_cells(db, term_id))
                recent = _recent(db, term_id)
                results = self._results(db, term_id, setup, context["students"], cells)
            # The latest writes come along, so the page's "Recent changes" needs no second read of the whole view.
            return {"cells": written, "results": results, "requests": recent}

    # Pulls ------------------------------------------------------------------------------------------------------------

    @staticmethod
    def _pull_options(data, extra=()):
        _object(data, ("replace", "absent_as_missing", "section_id", *extra), "pull")
        replace = data.get("replace", [])
        if not isinstance(replace, list) or len(replace) > MAX_REPLACE:
            raise ValueError("List the students whose hand-entered grades a pull may replace.")
        for row in replace:
            common.split_row(row)
        absent = data.get("absent_as_missing", False)
        if type(absent) is not bool:
            raise ValueError("Say whether an absent student is recorded missing.")
        return {"replace": set(replace), "absent_as_missing": absent, "section_id": data.get("section_id") or ""}

    @staticmethod
    def _linked(setup, item_id):
        item = setups.find_item(setup, item_id)
        if not item:
            raise NotFound("This column is no longer in the gradebook.")
        if item.get("archived"):
            raise ValueError(f"“{item['title']}” is archived. Restore it before pulling its grades.")
        if (item.get("link") or {}).get("kind") != "grading" or not item.get("assignment_id"):
            raise ValueError(f"Link “{item['title']}” to an assignment's grading before pulling its grades.")
        return item

    def _pull_plan(self, db, context, item, options, revision, cells):
        """What a pull would write. Reads grading (seconds on a big exam) on an open connection, outside any transaction. Once the
        plan has a certification, the pull reads the LATEST CERTIFICATION instead (`sources.certified`, catalog rows only): the
        gradebook keeps the certified values until the instructor certifies again (Certification and Export, 2026-09-26). A plan
        never certified is pulled live, exactly as before."""
        students = [student for student in context["students"] if setups.applies(item, student["section_id"])]
        link = sources.available(db, context["term"]["id"]).get(item["assignment_id"]) or {}
        totals = None
        if link.get("plan_id"):
            ids = sources.certified_cells(db, context["term"]["id"], item["id"])
            clear = {row for row, cell in cells.get(item["id"], {}).items() if cell["id"] in ids}
            totals = sources.certified(db, link["plan_id"], item["link"]["parts"], clear=clear)
        if totals is None:
            totals = sources.totals(self.frq, db, context["term"]["id"], item["assignment_id"], item["link"]["parts"],
                                    rows=[{key: student[key] for key in ("row", "section_id", "enrollment_id", "section")} for student in students])
        return sources.plan(item, totals, students=[{key: student[key] for key in ("row", "section_id", "section", "name")} for student in students],
                            cells=cells.get(item["id"], {}), options=options, revision=revision)

    def pull_preview(self, term_id, item_id, data):
        options = self._pull_options(data)
        with self.rosters.lock():
            context = self._scoped(self._context(term_id), options.get("section_id"))
            with connect(self.path) as db:
                setup, meta = _setup(db, term_id)
                item = self._linked(setup, item_id)
                return self._pull_plan(db, context, item, options, meta["revision"], _cells(db, term_id))

    def pull(self, term_id, item_id, data, actor="workspace"):
        options = self._pull_options(data, ("fingerprint", "request_id"))
        request_id = _request_id(data.get("request_id"))
        fingerprint = data.get("fingerprint")
        if not isinstance(fingerprint, str) or not 1 <= len(fingerprint) <= 128:
            raise ValueError("Preview the pull before applying it.")
        request_sha = common.digest({"kind": "pull", "item_id": item_id, "replace": sorted(options["replace"]),
                                     "absent_as_missing": options["absent_as_missing"], "fingerprint": fingerprint,
                                     **({"section_id": options["section_id"]} if options["section_id"] else {})})
        with self.rosters.lock():
            context = self._scoped(self._context(term_id), options.get("section_id"))
            with connect(self.path) as db:
                if not self._request(db, term_id, request_id, request_sha, "pull"):
                    setup, meta = _setup(db, term_id)
                    item = self._linked(setup, item_id)
                    current = _cells(db, term_id)
                    plan = self._pull_plan(db, context, item, options, meta["revision"], current)
                    if plan["fingerprint"] != fingerprint:
                        raise Conflict("The grades changed since the preview. Preview again.")
                    writes = [(row["row"], _proposed(row["proposed"])) for row in plan["rows"] if row["action"] in ("add", "update")]
                    db.execute("BEGIN IMMEDIATE")
                    # Grading was read outside this transaction: what it replaces must still be what the preview saw.
                    if _setup(db, term_id)[1]["revision"] != meta["revision"] or any(
                            _latest_id(db, term_id, item["id"], row) != (current.get(item["id"], {}).get(row) or {}).get("id") for row, _ in writes):
                        raise Conflict(CHANGED)
                    counts = plan.get("counts") or {}
                    summary = {"item_id": item["id"], "assignment_id": item["assignment_id"], "plan_id": plan.get("plan_id"),
                               "parts": plan.get("parts", item["link"]["parts"]), "counts": counts, "written": len(writes), "fingerprint": fingerprint,
                               **({"section_id": options["section_id"]} if options["section_id"] else {})}
                    if plan.get("certification_id"):
                        summary.update(certification_id=plan["certification_id"], source="certification")
                    reference = self._record_request(db, term_id, request_id, request_sha, "pull", summary, actor)
                    for row, value in writes:
                        self._write_proposed(db, context, term_id, item, row, current, value, "grading", reference, actor)
            return self._render(context)

    def _write_proposed(self, db, context, term_id, item, row, current, value, source, reference, actor, mirror=False):
        """An import's or a pull's value; the cell keeps its note, which neither of them proposes. A column WebAssign grades
        through Canvas takes only Canvas's own scores (`mirror`: Sync with Canvas and the nightly WebAssign import)."""
        if not item or item.get("archived"):
            raise Conflict(CHANGED)
        if item.get("canvas") in setups.OWNED and not mirror:
            raise ValueError(f"“{item['title']}” is graded in Canvas: its scores come from Canvas.")
        student = context["by_row"].get(row)
        if not student or not setups.applies(item, student["section_id"]):
            raise ValueError(f"“{item['title']}” does not apply to one of the students it would change.")
        cell = current.get(item["id"], {}).get(row)
        # A cleared cell is empty, note included, as when the instructor clears it.
        note = cell["note"] if cell and cell["status"] != "cleared" and value["status"] != "cleared" else ""
        self._record_score(db, term_id, item["id"], row, cell, {**value, "note": note}, source, reference, actor)

    # Imports ----------------------------------------------------------------------------------------------------------

    def _import_plan(self, db, context, parsed, options):
        setup, meta = _setup(db, context["term"]["id"])
        current = _cells(db, context["term"]["id"])
        plan = imports.plan(parsed, options=options, setup=setup, revision=meta["revision"], cells=current,
                            students=[{key: student[key] for key in ("row", "section_id", "section", "name", "uin")}
                                      for student in context.get("all_students", context["students"])])
        return plan, setup, meta, current

    def import_preview(self, term_id, content, filename, options_text):
        parsed = imports.parse(content, filename)
        options = imports.normalize_options(options_text)
        with self.rosters.lock():
            context = self._scoped(self._context(term_id), options.get("section_id"))
            with connect(self.path) as db:
                return _public_plan(self._import_plan(db, context, parsed, options)[0])

    def import_apply(self, term_id, content, filename, options_text, fingerprint, request_id, actor="workspace"):
        request_id = _request_id(request_id)
        if not isinstance(fingerprint, str) or not 1 <= len(fingerprint) <= 128:
            raise ValueError("Preview the file before importing it.")
        parsed = imports.parse(content, filename)
        options = imports.normalize_options(options_text)
        request_sha = common.digest({"kind": "import", "sha256": parsed["sha256"], "filename": parsed["filename"],
                                     "options": _plain(options), "fingerprint": fingerprint})
        with self.rosters.lock():
            context = self._scoped(self._context(term_id), options.get("section_id"))
            with connect(self.path) as db:
                if not self._request(db, term_id, request_id, request_sha, "import"):
                    plan, setup, meta, current = self._import_plan(db, context, parsed, options)
                    if plan["fingerprint"] != fingerprint:
                        raise Conflict("The file, its options or the gradebook changed since the preview. Preview it again.")
                    if not plan.get("ready"):
                        raise ValueError("Resolve the preview's issues before importing this file.")
                    writes = [change for change in plan["changes"] if change["action"] in ("add", "update")]
                    db.execute("BEGIN IMMEDIATE")
                    if _setup(db, term_id)[1]["revision"] != meta["revision"] or any(
                            change["item_id"] and _latest_id(db, term_id, change["item_id"], change["row"])
                            != (current.get(change["item_id"], {}).get(change["row"]) or {}).get("id") for change in writes):
                        raise Conflict(CHANGED)
                    columns = {column["key"]: column["mapping"]["item_id"] for column in plan.get("columns", [])
                               if (column.get("mapping") or {}).get("action") == "item"}
                    new_items = plan.get("new_items") or []
                    if new_items and db.execute("SELECT 1 FROM gradebook_setups WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone():
                        # The setup revision below takes this request ID: one a setup save or a link already took is another change.
                        raise Conflict("This request ID was used for a different change.")
                    if new_items:
                        # New columns are created in this same transaction, as ONE setup revision named by this request.
                        added = [_new_item(item["title"], item.get("category_id"), item["points"]) for item in new_items]
                        normalized = self._normalize(db, context, {**setup, "items": [*setup.get("items", []), *added]}, setup)
                        known = {item["id"] for item in setup.get("items", [])}
                        created = [item["id"] for item in normalized["items"] if item["id"] not in known]
                        if len(created) != len(new_items):
                            raise RuntimeError("The new gradebook columns could not be matched to the file's columns.")
                        columns.update({item["column"]: identifier for item, identifier in zip(new_items, created)})
                        self._record_setup(db, term_id, meta["revision"], normalized, request_id, request_sha, actor)
                        setup = normalized
                    summary = {"filename": parsed["filename"], "sha256": parsed["sha256"], "format": parsed["format"], "columns": columns,
                               "counts": plan.get("counts") or {}, "written": len(writes)}
                    reference = self._record_request(db, term_id, request_id, request_sha, "import", summary, actor)
                    for change in writes:
                        target = change["item_id"] or columns.get(change["column"])
                        item = setups.find_item(setup, target) if target else None
                        self._write_proposed(db, context, term_id, item, change["row"], current, _proposed(change["proposed"]), "import", reference, actor)
            self.columns_changed(term_id, actor)  # COLUMN ASSIGNMENTS: a file's new column gets its assignment in Faculty's copy
            return self._render(context)

    # Canvas and iClicker imports (README.md, "Importing from Canvas", "Importing from iClicker") ------------------------------
    # One path for both: iClicker's sessions are planned by the Canvas planner (gradebook/iclicker_import.py), so what is
    # previewed, fingerprinted, checked again and written is decided once. Each source remembers its matches in its own table.

    SOURCES = {"canvas": {"name": "Canvas", "table": "gradebook_canvas_links", "column": "assignment_id",
                          "where": "Link one on its section tab first."},
               "iclicker": {"name": "iClicker", "table": "gradebook_iclicker_links", "column": "session_id",
                            "where": "Choose its iClicker course in Import from iClicker first."}}

    @classmethod
    def _remembered(cls, db, term_id, kind, sync=False):
        """The latest remembered match of each imported column: {"<course>:<source id>": {"item_id": str|None}}. For Canvas, an
        assignment a sync (or an earlier send) created for a column is that column's too, unless an import decided otherwise;
        for Sync with Canvas (`sync`), so is an assignment Faculty's Canvas copy made for a column and pushed, as the To-Canvas half
        reads it (`canvas_export.targets`), so that neither half takes it for an assignment of its own."""
        source = cls.SOURCES[kind]
        found = {}
        for row in db.execute(f"""SELECT course_id,{source["column"]} AS source_id,item_id FROM {source["table"]} WHERE term_id=?
                ORDER BY created_at,rowid""", (term_id,)):
            found[f"{row['course_id']}:{row['source_id']}"] = {"item_id": row["item_id"]}
        if kind == "canvas":
            for row in db.execute("SELECT course_id,assignment_id,item_id FROM gradebook_canvas_created WHERE term_id=? ORDER BY created_at,rowid", (term_id,)):
                found.setdefault(f"{row['course_id']}:{row['assignment_id']}", {"item_id": row["item_id"]})
            if sync:
                for course, chosen in cls._copy_links(db, term_id)[0].items():
                    for item_id, assignment_id in chosen.items():
                        found.setdefault(f"{course}:{assignment_id}", {"item_id": item_id})
        return found

    def _source_links(self, kind, term_id, section_id=None):
        """The term's linked courses (or ONE section's): {section_id: link}."""
        source = self.SOURCES[kind]
        service = self.canvas if kind == "canvas" else self.iclicker
        if service is None:
            raise ValueError(f"{source['name']} is not set up in this workspace.")
        with connect(self.path) as db:
            _term(db, term_id)
        links = service.term_links(term_id)
        if section_id:
            links = {key: link for key, link in links.items() if key == section_id}
            if not links:
                raise ValueError(f"This section has no {source['name']} course linked yet. {source['where']}")
        if not links:
            raise ValueError(f"No section of this term links a {source['name']} course yet. {source['where']}")
        return links

    def _source_read(self, kind, term_id, section_id=None, links=None):
        """The linked courses and what they hold, read with NO lock held (a course can take a minute)."""
        links = links or self._source_links(kind, term_id, section_id)
        service = self.canvas if kind == "canvas" else self.iclicker
        ids = {link["course_id"] for link in links.values()}
        return links, service.read_grades(ids) if kind == "canvas" else service.read_scores(ids)

    def _source_plan(self, kind, db, context, links, read, options, hints=None, sync=None, missing_on=None):
        if context.get("section_id"):
            links = {section_id: link for section_id, link in links.items() if section_id == context["section_id"]}
        term_id = context["term"]["id"]
        setup, meta = _setup(db, term_id)
        current, remembered = _cells(db, term_id), self._remembered(db, term_id, kind, sync is not None)
        students = [{key: student[key] for key in ("row", "section_id", "section", "name", "uin", "dropped")} for student in context["students"]]
        everyone = context.get("all_students", context["students"])
        term_uins = {student["uin"] for student in everyone if student.get("uin")}
        if kind == "canvas":
            sent = {(canvas_import.assignment_key(course, assignment), receipt["row"]): receipt["own"]
                    for (course, assignment, _user), receipt in self._sent(db, term_id).items() if receipt["own"]}
            plan = canvas_import.plan(read, options=options, links=links, setup=setup, revision=meta["revision"], cells=current,
                                      remembered=remembered, sent=sent, students=students, term_uins=term_uins, hints=hints,
                                      origins=self._origins(db, term_id, current), sync=sync)
        else:
            plan = iclicker_import.plan(read, options=options, links=links, setup=setup, revision=meta["revision"], cells=current,
                                        remembered=remembered, students=students, term_uins=term_uins, origins=self._origins(db, term_id, current),
                                        missing_on=missing_on,
                                        term_students=[{"uin": student.get("uin"), "email": student.get("email", "")} for student in everyone])
            # The excused absences' full credit the import gives (ABSENCES ICLICKER-CREDIT), in the fingerprint: an absence decided
            # between the preview and Import asks for a new preview, so the preview's line is what Import writes.
            excused = self._excused_preview(db, context, plan, read)
            if excused:
                plan["excused"] = excused
                plan["fingerprint"] = common.digest({"plan": plan["fingerprint"], "excused": excused})
        return plan, setup, meta, current, remembered

    @staticmethod
    def _origins(db, term_id, current):
        """{cell revision id: `changes.origin`} of the term's cells whose latest value an import or a pull wrote (`current` is
        `_cells`): what Import from iClicker and from Canvas read to keep the values they did not write themselves (KEEPS OTHERS).
        A value an excused absence's credit PUT BACK (ABSENCES ICLICKER-CREDIT) is the revision before the fill, restored: it
        keeps that revision's origin; so does one an iClicker credit put back (CREDIT BEFORE THE IMPORT)."""
        made = {row["id"]: change_log.origin(row["kind"], row["request_id"], json.loads(row["summary_json"]))
                for row in db.execute("SELECT id,kind,request_id,summary_json FROM gradebook_requests WHERE term_id=?", (term_id,))}
        revisions = {row["id"]: (row["request_ref"], row["replaces_id"])
                     for row in db.execute("SELECT id,request_ref,replaces_id FROM gradebook_scores WHERE term_id=?", (term_id,))}

        def origin(revision):
            while revision in revisions:
                found = made.get(revisions[revision][0])
                if found not in ("absence", "iclicker_credit"):
                    return found if found != "typed" else None
                fill = revisions[revision][1]  # a put-back replaces the fill, which replaced the revision it restores
                revision = revisions[fill][1] if fill in revisions else None
            return None

        found = {cell["id"]: origin(cell["id"]) for rows in current.values() for cell in rows.values() if cell["source"] != "instructor"}
        return {revision: where for revision, where in found.items() if where}

    @staticmethod
    def _source_options(kind, value):
        return canvas_import.normalize_options(value) if kind == "canvas" else iclicker_import.normalize_options(value)

    @staticmethod
    def _askable(plan, setup):
        """The columns Jev may name a gradebook column for (JEV CANVAS-MATCH) and the setup they are asked against."""
        return [column for column in plan["columns"] if column.get("askable")], setup.get("items", []), setup.get("categories", [])

    def _source_preview(self, kind, term_id, data):
        _object(data, ("options", "section_id"), f"{self.SOURCES[kind]['name']} import")
        options = self._source_options(kind, data.get("options"))
        links, read = self._source_read(kind, term_id, data.get("section_id"))
        with self.rosters.lock():
            context = self._scoped(self._context(term_id), data.get("section_id"))
            with connect(self.path) as db:
                plan = self._source_plan(kind, db, context, links, read, options)[0]
        _keep_previewed((kind, term_id, data.get("section_id") or "", plan["fingerprint"]), links, read)
        return plan

    def _source_apply(self, kind, term_id, data, actor="workspace", given=None, note=None, sync=None, missing_on=None):
        """Import exactly what the preview showed. `given` (links, read) is a read the caller made itself (the nightly WebAssign
        import, gradebook/webassign_nightly.py; iClicker after class), used while the sections still link those courses; `note`
        joins the request's summary (`automatic`); `sync` plans Canvas as Sync with Canvas's From-Canvas half does (the nightly
        WebAssign import mirrors Canvas: `{"mirror": None}`); `missing_on` the campus dates whose sessions record a student who did
        not join as missing (iClicker after class)."""
        source = self.SOURCES[kind]
        name = source["name"]
        _object(data, ("options", "fingerprint", "request_id", "section_id"), f"{name} import")
        request_id = _request_id(data.get("request_id"))
        fingerprint = data.get("fingerprint")
        if not isinstance(fingerprint, str) or not 1 <= len(fingerprint) <= 128:
            raise ValueError(f"Preview the {name} import before importing it.")
        options = self._source_options(kind, data.get("options"))
        request_sha = common.digest({"kind": kind, "options": _plain(options), "fingerprint": fingerprint,
                                     **({"section_id": data["section_id"]} if data.get("section_id") else {})})
        with connect(self.path) as db:
            _term(db, term_id)
            replay = self._request(db, term_id, request_id, request_sha, "import")
        if replay:
            with self.rosters.lock():
                return self._render(self._scoped(self._context(term_id), data.get("section_id")))
        # What the preview read, so Import writes what it showed (requirement, 2026-10-02); read again only when it is not kept
        # here or the sections now link other courses.
        key = (kind, term_id, data.get("section_id") or "", fingerprint)
        links = self._source_links(kind, term_id, data.get("section_id"))
        previewed = given if given is not None else _previewed(key)
        if previewed is not None and previewed[0] == links:
            read = previewed[1]
        else:
            previewed = None
            links, read = self._source_read(kind, term_id, data.get("section_id"), links)
        with self.rosters.lock():
            context = self._scoped(self._context(term_id), data.get("section_id"))
            with connect(self.path) as db:
                if not self._request(db, term_id, request_id, request_sha, "import"):
                    plan, setup, meta, current, remembered = self._source_plan(kind, db, context, links, read, options, sync=sync, missing_on=missing_on)
                    if plan["fingerprint"] != fingerprint:
                        raise Conflict(CHANGED if previewed is not None else f"{name} or the gradebook changed since the preview. Preview it again.")
                    if not plan.get("ready"):
                        raise ValueError(f"Resolve the preview's issues before importing from {name}.")
                    db.execute("BEGIN IMMEDIATE")
                    self._import_write(db, context, kind, term_id, plan, setup, meta, current, remembered, read, request_id, request_sha, actor,
                                       {**({"section_id": data["section_id"]} if data.get("section_id") else {}), **(note or {})})
            _previewed(key, drop=True)
            self.columns_changed(term_id, actor)  # COLUMN ASSIGNMENTS: an iClicker session's new column gets its assignment
            return self._render(context)

    def _import_write(self, db, context, kind, term_id, plan, setup, meta, current, remembered, read, request_id, request_sha, actor,
                      note=None, taken=()):
        """Writes an import plan inside the caller's BEGIN IMMEDIATE, after checking that the gradebook is as the plan read it:
        ONE setup revision (new categories and columns, columns widened to more sections, columns marked WebAssign's), the
        `import` request (`note` joins its summary), the remembered matches, and the `add`, `update` and `clear` changes as source
        `import`; `taken` [{item_id, row, value}] are Sync with Canvas's Take Canvas's picks, written the same way (value None: the
        cell emptied). Returns the request's ID."""
        source = self.SOURCES[kind]
        name = source["name"]
        writes = [change for change in plan["changes"] if change["action"] in ("add", "update", "clear") and not change["compare"]]
        cells = [(change["item_id"], change["row"]) for change in writes if change["item_id"]] + [(t["item_id"], t["row"]) for t in taken]
        if _setup(db, term_id)[1]["revision"] != meta["revision"] or self._remembered(db, term_id, kind, plan.get("sync") is not None) != remembered or any(
                _latest_id(db, term_id, item_id, row) != (current.get(item_id, {}).get(row) or {}).get("id") for item_id, row in cells):
            raise Conflict(CHANGED)
        targets = {column["key"]: column["mapping"]["item_id"] for column in plan["columns"] if column["mapping"]["action"] == "item"}
        new_items = plan.get("new_items") or []
        widen = plan.get("widen") or {}
        mark = (plan.get("sync") or {}).get("mark") or {}
        created = {}
        if new_items or widen or mark:
            if db.execute("SELECT 1 FROM gradebook_setups WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone():
                raise Conflict("This request ID was used for a different change.")
            # New categories and columns, columns widened to more sections, and columns an assignment Canvas grades goes to (SYNC
            # WITH CANVAS: Canvas's scores are theirs from now on): ONE setup revision named by this request, in this transaction.
            refs = {common.digest(name.casefold())[:32]: name for name in plan.get("new_categories") or []}
            by_ref = {name.casefold(): ref for ref, name in refs.items()}
            due = {column["key"]: column["due_date"] for column in plan["columns"]}
            added = [_new_item(entry["title"], entry["category_id"] or by_ref.get((entry["category_name"] or "").casefold()) or
                               next((c["id"] for c in setup.get("categories", []) if c["name"].casefold() == (entry["category_name"] or "").casefold()), None),
                               entry["points"], due_date=next((due[key] for key in entry["columns"] if due.get(key)), ""),
                               sections=list(entry.get("sections") or []), **({"canvas": entry["canvas"]} if entry.get("canvas") else {}))
                     for entry in new_items]
            items = [{**item, "sections": sorted(set(item["sections"]) | set(widen[item["id"]]))} if item["id"] in widen and item.get("sections") else item
                     for item in setup.get("items", [])]
            items = [{**item, "canvas": mark[item["id"]]} if item["id"] in mark else item for item in items]
            normalized = self._normalize(db, context, {**setup, "categories": [*setup.get("categories", []), *({"ref": ref, "name": name} for ref, name in refs.items())],
                                                       "items": [*items, *added]}, setup, server=bool(mark) or any(entry.get("canvas") for entry in new_items))
            known = {item["id"] for item in setup.get("items", [])}
            fresh = [item["id"] for item in normalized["items"] if item["id"] not in known]
            if len(fresh) != len(new_items):
                raise RuntimeError(f"The new gradebook columns could not be matched to {name}'s.")
            for entry, identifier in zip(new_items, fresh):
                for key in entry["columns"]:
                    created[key] = identifier
            self._record_setup(db, term_id, meta["revision"], normalized, request_id, request_sha, actor)
            setup = normalized
        targets.update(created)
        counts = plan.get("counts") or {}
        summary = {"source": kind, "courses": [course["id"] for course in plan.get("courses", [])], "columns": targets,
                   **({"widened": {key: len(value) for key, value in widen.items()}} if widen else {}),
                   **({"marked": sorted(mark)} if mark else {}),
                   "counts": counts, "written": len(writes) + len(taken), "new_columns": len(new_items), "fingerprint": plan["fingerprint"],
                   **(note or {})}
        reference = self._record_request(db, term_id, request_id, request_sha, "import", summary, actor)
        remember = {**plan.get("remember", {}), **created}
        for key in sorted(remember):
            course_id, source_id = key.split(":")
            db.execute(f"""INSERT INTO {source["table"]}(id,term_id,course_id,{source["column"]},item_id,request_ref,actor,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), term_id, course_id, source_id, remember[key], reference, actor, timestamp()))
        for change in writes:
            target = change["item_id"] or created.get(change["column"])
            item = setups.find_item(setup, target) if target else None
            self._write_proposed(db, context, term_id, item, change["row"], current, _proposed(change["proposed"]), "import", reference, actor,
                                 mirror=bool(change.get("mirror")))
        for pick in taken:
            value = pick["value"] if pick["value"] is not None else {"status": "cleared", "points": None}
            self._write_proposed(db, context, term_id, setups.find_item(setup, pick["item_id"]), pick["row"], current, _proposed(value),
                                 "import", reference, actor)
        if kind == "iclicker":
            # The sessions' dates, then the excused absences' full credit for the students this import covers
            # (ABSENCES ICLICKER-CREDIT), in this same transaction.
            self._record_session_dates(db, term_id, iclicker_import.session_dates(read), reference, actor)
            members = [(student["section_id"], student["enrollment_id"]) for student in context["students"]]
            self.credit_excused(db, term_id, members, actor)
            # ICLICKER CREDIT BEFORE THE IMPORT: credits the instructor gave before these sessions had columns.
            self.credit_iclicker(db, term_id, members, actor)
        return reference

    # Excused absences' iClicker credit (docs/decisions/absences.md ICLICKER-CREDIT; requirement, 2026-10-01) ------------------------

    @staticmethod
    def _record_session_dates(db, term_id, dates, reference, actor):
        """Records the campus date of each session an iClicker import read, when it is new or differs from the latest recorded."""
        known = {}
        for row in db.execute("SELECT course_id,session_id,session_date FROM gradebook_iclicker_sessions WHERE term_id=? ORDER BY created_at,rowid", (term_id,)):
            known[(row["course_id"], row["session_id"])] = row["session_date"]
        for (course_id, session_id), date in sorted(dates.items()):
            if known.get((course_id, session_id)) != date:
                db.execute("""INSERT INTO gradebook_iclicker_sessions(id,term_id,course_id,session_id,session_date,request_ref,actor,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), term_id, course_id, session_id, date, reference, actor, timestamp()))

    @staticmethod
    def _iclicker_sessions(db, term_id):
        """[{course_id, session_id, date, item_id}] of every session an import recorded a date for, with the column its scores go
        to (the latest remembered match; None when it is skipped)."""
        dates, columns = {}, {}
        for row in db.execute("SELECT course_id,session_id,session_date FROM gradebook_iclicker_sessions WHERE term_id=? ORDER BY created_at,rowid", (term_id,)):
            dates[(row["course_id"], row["session_id"])] = row["session_date"]
        for row in db.execute("SELECT course_id,session_id,item_id FROM gradebook_iclicker_links WHERE term_id=? ORDER BY created_at,rowid", (term_id,)):
            columns[(row["course_id"], row["session_id"])] = row["item_id"]
        return [{"course_id": course_id, "session_id": session_id, "date": date, "item_id": columns.get((course_id, session_id))}
                for (course_id, session_id), date in sorted(dates.items())]

    @staticmethod
    def _iclicker_courses(db, term_id):
        """{section_id: iClicker course} of the term's sections that link one (the latest link of each, iclicker/service.py)."""
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='iclicker_course_links'").fetchone():
            return {}
        found = {}
        for row in db.execute("""SELECT l.section_id,l.course_id FROM iclicker_course_links l JOIN course_sections s ON s.id=l.section_id
                WHERE s.term_id=? ORDER BY l.created_at,l.rowid""", (term_id,)):
            found[row["section_id"]] = row["course_id"]
        return found

    @staticmethod
    def _iclicker_items(db, term_id):
        """Every column an iClicker session's scores ever went to (the only columns an absence's credit reads or writes)."""
        return [row[0] for row in db.execute("SELECT DISTINCT item_id FROM gradebook_iclicker_links WHERE term_id=? AND item_id IS NOT NULL ORDER BY item_id",
                                             (term_id,))]

    @staticmethod
    def _credit_history(db, term_id, item_ids, members=None, source="absence", key="absence_id"):
        """{(item_id, row): [revision, …]} oldest first, of these columns' cells of these memberships (None: every student), each
        revision marked with the absence and action that wrote it when an absence's credit did (`excused_credit.plan`); with
        `source` "iclicker_credit" and `key` "credit_id", an iClicker credit's own writes instead (`iclicker_credit`)."""
        wanted = None if members is None else {common.row_key(section_id, enrollment_id) for section_id, enrollment_id in members}
        sections = sorted({row.split(":")[0] for row in wanted}) if wanted is not None else None
        history = {}
        if not item_ids or sections == []:
            return history
        for row in db.execute(f"""SELECT g.id,g.item_id,g.section_id,g.enrollment_id,g.status,g.points,g.note,g.source,r.summary_json
                FROM gradebook_scores g JOIN gradebook_requests r ON r.id=g.request_ref
                WHERE g.term_id=? AND g.item_id IN ({",".join("?" * len(item_ids))})
                {f"AND g.section_id IN ({','.join('?' * len(sections))})" if sections is not None else ""} ORDER BY g.seq""",
                              [term_id, *item_ids, *(sections or [])]):
            cell = (row["item_id"], common.row_key(row["section_id"], row["enrollment_id"]))
            if wanted is not None and cell[1] not in wanted:
                continue
            summary = json.loads(row["summary_json"])
            mine = summary.get("source") == source
            history.setdefault(cell, []).append({"id": row["id"], "status": row["status"], "points": row["points"], "note": row["note"],
                                                 "source": row["source"], "absence": summary.get(key) if mine else None,
                                                 "action": summary.get("action") if mine else None})
        return history

    def _excused_preview(self, db, context, plan, read):
        """What an iClicker import's own `credit_excused` will fill once its scores are in, for the preview's "When you press
        Import" (requirement, 2026-10-02): `excused_credit` on the term as it will stand after the
        import (its new columns, matches, widened sections and values laid over the catalog's), so the line says what Import
        writes. Also how many of the cells the import keeps already hold an absence's credit. None when there is nothing to say."""
        from absences.service import dates_text, standing
        term_id = context["term"]["id"]
        members = [(student["section_id"], student["enrollment_id"]) for student in context["students"]]
        absences = standing(db, term_id, members)
        if not absences:
            return None
        setup, _ = _setup(db, term_id)
        items = {item["id"]: dict(item) for item in setup.get("items", [])}
        for item_id, sections in (plan.get("widen") or {}).items():
            if item_id in items and items[item_id].get("sections"):
                items[item_id]["sections"] = sorted(set(items[item_id]["sections"]) | set(sections))
        target = {}  # each session column's gradebook column once imported ("new:<key>" for one the import creates)
        for column in plan["columns"]:
            mapping = column["mapping"]
            if mapping["action"] == "item":
                target[column["key"]] = mapping["item_id"]
            elif mapping["action"] == "new":
                target[column["key"]] = f"new:{column['key']}"
                items[target[column["key"]]] = {"id": target[column["key"]], "title": mapping.get("title") or column["title"],
                                                "points": mapping.get("points") or column["points"], "archived": False, "sections": []}
        sessions = {(session["course_id"], session["session_id"]): session for session in self._iclicker_sessions(db, term_id)}
        dates = iclicker_import.session_dates(read)
        for column in plan["columns"]:
            course_id, session_id = column["key"].split(":", 1)
            if (course_id, session_id) in dates:
                sessions[(course_id, session_id)] = {"course_id": course_id, "session_id": session_id, "date": dates[(course_id, session_id)],
                                                     "item_id": target.get(column["key"])}
        history = self._credit_history(db, term_id, sorted(set(self._iclicker_items(db, term_id)) | {i for i in target.values() if not i.startswith("new:")}),
                                       members)
        for change in plan["changes"]:
            item_id = change["item_id"] or target.get(change["column"])
            if change["action"] in ("add", "update") and not change.get("compare") and item_id:
                value = _proposed(change["proposed"])
                history.setdefault((item_id, change["row"]), []).append({"id": None, "status": value["status"], "points": value["points"],
                                                                         "note": "", "source": "import", "absence": None, "action": None})
        courses = self._iclicker_courses(db, term_id)
        desired = excused_credit.covered(absences, courses, list(sessions.values()), items)
        fills = [write for write in excused_credit.plan(desired, history, items) if write["action"] == excused_credit.FILL]
        kept = sum(1 for change in plan["changes"] if change["action"] == "kept" and change.get("current")
                   and (history.get((change["item_id"], change["row"])) or [{}])[-1].get("action") == excused_credit.FILL
                   and history[(change["item_id"], change["row"])][-1]["id"] == change["current"]["id"])
        if not fills and not kept:
            return None
        day = {(session["course_id"], session["item_id"]): session["date"] for session in sessions.values() if session["item_id"]}
        students = {}
        for write in fills:
            date = day.get((courses.get(write["row"].split(":")[0]), write["item_id"]), "")
            students.setdefault(write["row"], []).append({"title": items[write["item_id"]]["title"], "date": date,
                                                          "day": dates_text(date, date) if date else ""})
        by_row = context["by_row"]
        credit = sorted(({"name": by_row[row]["name"], "section": by_row[row]["section"],
                          "sessions": sorted(found, key=lambda session: (session["date"], session["title"].casefold()))}
                         for row, found in students.items() if row in by_row), key=lambda entry: (entry["name"].casefold(), entry["section"]))
        return {"cells": len(fills), "points": common.points_text(max((Decimal(str(items[w["item_id"]]["points"])) for w in fills), default=Decimal(10))),
                "students": credit, "kept": kept}

    def credit_excused(self, db, term_id, members=None, actor="workspace"):
        """Brings the iClicker cells of these `(section_id, enrollment_id)` memberships (None: every student of the term) in line
        with their excused absences (`excused_credit`): full credit for each session an excused absence covers, and what a fill
        replaced put back where none covers it any more. Runs in the CALLER's `BEGIN IMMEDIATE` under the roster lifecycle lock
        (an absence's save, an iClicker import), so what changed it and what it writes commit together. Each absence's fills, and
        each absence's put-backs, are one `scores` request whose summary says `source: "absence"`. Returns {"filled", "put_back"}."""
        from absences.service import standing
        counts = {excused_credit.FILL: 0, excused_credit.PUT_BACK: 0}
        linked = self._iclicker_items(db, term_id)
        if not linked:
            return {"filled": 0, "put_back": 0}  # no iClicker import yet: no session to credit, no fill to put back
        absences = standing(db, term_id, members)
        setup, _ = _setup(db, term_id)
        items = {item["id"]: item for item in setup.get("items", [])}
        desired = excused_credit.covered(absences, self._iclicker_courses(db, term_id), self._iclicker_sessions(db, term_id), items)
        writes = excused_credit.plan(desired, self._credit_history(db, term_id, linked, members), items)
        known = {absence["id"]: absence for absence in absences}
        batches = {}
        for write in writes:
            batches.setdefault((write["absence_id"], write["action"]), []).append(write)
        for (absence_id, action), batch in sorted(batches.items()):
            absence = known.get(absence_id) or {}
            cells = [(write["item_id"], write["row"], write["value"]) for write in batch]
            summary = {"source": "absence", "absence_id": absence_id, "action": action, "rule": excused_credit.RULE,
                       "dates": absence.get("dates", ""), "cells": len(batch), "items": sorted({write["item_id"] for write in batch}),
                       **({"section_id": absence["section_id"]} if absence.get("section_id") else {})}
            reference = self._record_request(db, term_id, f"absence:{absence_id}:{action}:{uuid.uuid4().hex}",
                                             common.digest({"absence_id": absence_id, "action": action, "cells": cells}), "scores", summary, actor)
            for write in batch:
                self._record_score(db, term_id, write["item_id"], write["row"], write["cell"], write["value"], write["value"]["source"], reference, actor)
            counts[action] += len(batch)
        return {"filled": counts[excused_credit.FILL], "put_back": counts[excused_credit.PUT_BACK]}

    def absence_credit(self, db, absence):
        """The iClicker columns an absence gives full credit now (cells whose latest revision is its fill), each with its session's
        date: what the absence's window lists (catalog only)."""
        rows = db.execute("""SELECT g.item_id,g.points FROM gradebook_scores g JOIN gradebook_requests r ON r.id=g.request_ref
            WHERE g.term_id=? AND g.section_id=? AND g.enrollment_id=? AND json_extract(r.summary_json,'$.source')='absence'
              AND json_extract(r.summary_json,'$.absence_id')=? AND json_extract(r.summary_json,'$.action')=?
              AND g.seq=(SELECT MAX(x.seq) FROM gradebook_scores x WHERE x.term_id=g.term_id AND x.item_id=g.item_id
                         AND x.section_id=g.section_id AND x.enrollment_id=g.enrollment_id)""",
                          (absence["term_id"], absence["section_id"], absence["enrollment_id"], absence["id"], excused_credit.FILL)).fetchall()
        if not rows:
            return []
        setup, _ = _setup(db, absence["term_id"])
        titles = {item["id"]: item["title"] for item in setup.get("items", [])}
        course = self._iclicker_courses(db, absence["term_id"]).get(absence["section_id"])
        dates = {session["item_id"]: session["date"] for session in self._iclicker_sessions(db, absence["term_id"])
                 if session["course_id"] == course and session["item_id"]}
        return sorted(({"item_id": row["item_id"], "title": titles.get(row["item_id"], "An iClicker column"), "points": row["points"],
                        "date": dates.get(row["item_id"], "")} for row in rows), key=lambda cell: (cell["date"], cell["title"].casefold()))

    # iClicker credit given before the session is imported (docs/decisions/iclicker.md CREDIT BEFORE THE IMPORT; instructor,
    # 2026-10-21: "they have to email this to me before the end of their class period, so followup hasn't had time to actually
    # import that clicker question yet") -------------------------------------------------------------------------------------

    @staticmethod
    def _iclicker_credits(db, term_id, members=None):
        """Each iClicker credit of the term (of these `(section_id, enrollment_id)` memberships, when given) as its latest row
        stands: [{credit_id, section_id, enrollment_id, class_date, state, reason, created_at}], oldest class date first."""
        wanted = None if members is None else {(str(section), str(enrollment)) for section, enrollment in members}
        latest = {}
        for row in db.execute("""SELECT credit_id,section_id,enrollment_id,class_date,state,reason,created_at FROM gradebook_iclicker_credits
                WHERE term_id=? ORDER BY created_at,rowid""", (term_id,)):
            if wanted is None or (row["section_id"], row["enrollment_id"]) in wanted:
                latest[row["credit_id"]] = dict(row)
        return sorted(latest.values(), key=lambda credit: (credit["class_date"], credit["credit_id"]))

    def credit_iclicker(self, db, term_id, members=None, actor="workspace"):
        """Brings the iClicker cells of these memberships (None: every student) in line with their iClicker credits
        (`iclicker_credit`), as `credit_excused` does for excused absences, in the CALLER's transaction under the roster lifecycle
        lock. Each credit's fills, and its put-backs, are one `scores` request whose summary says `source: "iclicker_credit"`.
        Returns {"filled", "put_back"}."""
        counts = {excused_credit.FILL: 0, excused_credit.PUT_BACK: 0}
        credits = self._iclicker_credits(db, term_id, members)
        linked = self._iclicker_items(db, term_id)
        if not credits or not linked:
            return {"filled": 0, "put_back": 0}
        setup, _ = _setup(db, term_id)
        items = {item["id"]: item for item in setup.get("items", [])}
        desired = iclicker_credit.covered(credits, self._iclicker_courses(db, term_id), self._iclicker_sessions(db, term_id), items)
        history = self._credit_history(db, term_id, linked, members, source="iclicker_credit", key="credit_id")
        writes = excused_credit.plan(desired, history, items, noted=iclicker_credit.note)
        known = {credit["credit_id"]: credit for credit in credits}
        batches = {}
        for write in writes:
            batches.setdefault((write["absence_id"], write["action"]), []).append(write)
        for (credit_id, action), batch in sorted(batches.items()):
            credit = known.get(credit_id) or {}
            cells = [(write["item_id"], write["row"], write["value"]) for write in batch]
            summary = {"source": "iclicker_credit", "credit_id": credit_id, "action": action, "rule": iclicker_credit.RULE,
                       "class_date": credit.get("class_date", ""), "cells": len(batch), "items": sorted({write["item_id"] for write in batch}),
                       **({"section_id": credit["section_id"]} if credit.get("section_id") else {})}
            reference = self._record_request(db, term_id, f"iclicker-credit:{credit_id}:{action}:{uuid.uuid4().hex}",
                                             common.digest({"credit_id": credit_id, "action": action, "cells": cells}), "scores", summary, actor)
            for write in batch:
                self._record_score(db, term_id, write["item_id"], write["row"], write["cell"], write["value"], write["value"]["source"], reference, actor)
            counts[action] += len(batch)
        return {"filled": counts[excused_credit.FILL], "put_back": counts[excused_credit.PUT_BACK]}

    def _credit_view(self, db, term_id, credit):
        """A credit as its routes answer it: the credit, and the cells it gives full credit now ({item_id, title, points})."""
        setup, _ = _setup(db, term_id)
        titles = {item["id"]: item["title"] for item in setup.get("items", [])}
        given = db.execute("""SELECT g.item_id,g.points FROM gradebook_scores g JOIN gradebook_requests r ON r.id=g.request_ref
            WHERE g.term_id=? AND g.section_id=? AND g.enrollment_id=? AND json_extract(r.summary_json,'$.source')='iclicker_credit'
              AND json_extract(r.summary_json,'$.credit_id')=? AND json_extract(r.summary_json,'$.action')=?
              AND g.seq=(SELECT MAX(x.seq) FROM gradebook_scores x WHERE x.term_id=g.term_id AND x.item_id=g.item_id
                         AND x.section_id=g.section_id AND x.enrollment_id=g.enrollment_id)""",
                           (term_id, credit["section_id"], credit["enrollment_id"], credit["credit_id"], excused_credit.FILL)).fetchall()
        return {"credit": {key: credit[key] for key in ("credit_id", "section_id", "enrollment_id", "class_date", "state", "reason")},
                "given": [{"item_id": row["item_id"], "title": titles.get(row["item_id"], "An iClicker column"), "points": row["points"]}
                          for row in given]}

    def iclicker_credit(self, term_id, data, actor="workspace"):
        """Gives one student full credit for their section's iClicker polls of one class day, now or once they are imported
        (`credit_iclicker`). `class_date` is a campus date up to today; `reason` (at most 200 characters) is the cell's note. The
        student's section must link an iClicker course. A credit already waiting for that student and day is answered as it is."""
        from scheduling.clock import campus_today
        _object(data, ("request_id", "section_id", "enrollment_id", "class_date", "reason"), "iClicker credit")
        request_id = _request_id(data.get("request_id"))
        day = data.get("class_date")
        try:
            valid = isinstance(day, str) and len(day) == 10 and datetime_date.fromisoformat(day).isoformat() == day
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Name the class day as YYYY-MM-DD.")
        if day > campus_today().isoformat():
            raise ValueError("Give iClicker credit for a class day up to today.")
        reason = " ".join(data.get("reason").split()) if isinstance(data.get("reason"), str) else ""
        if not 1 <= len(reason) <= iclicker_credit.MAX_REASON:
            raise ValueError(f"Say why in 1 to {iclicker_credit.MAX_REASON} characters (it is the grade's note).")
        row = common.row_key(str(data.get("section_id") or ""), str(data.get("enrollment_id") or ""))
        section_id, enrollment_id = common.split_row(row)
        request_sha = common.digest({"row": row, "class_date": day, "reason": reason})
        with self.rosters.lock():
            context = self._context(term_id)
            student = context["by_row"].get(row)
            if not student:
                raise ValueError("That student is not in this section of the term's roster.")
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute("SELECT * FROM gradebook_iclicker_credits WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
                if prior and prior["request_sha256"] != request_sha:
                    raise Conflict("This request ID was used for a different change.")
                if not self._iclicker_courses(db, term_id).get(section_id):
                    raise ValueError(f"Section {student['section']} links no iClicker course: link one in its Import from iClicker first.")
                credit_id = prior["credit_id"] if prior else next(
                    (credit["credit_id"] for credit in self._iclicker_credits(db, term_id, [(section_id, enrollment_id)])
                     if credit["class_date"] == day and credit["state"] == "waiting"), None)
                if not credit_id:
                    credit_id = str(uuid.uuid4())
                    db.execute("""INSERT INTO gradebook_iclicker_credits(id,credit_id,term_id,section_id,enrollment_id,class_date,state,reason,
                        request_id,request_sha256,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (str(uuid.uuid4()), credit_id, term_id, section_id, enrollment_id, day, "waiting", reason, request_id, request_sha,
                                actor, timestamp()))
                counts = self.credit_iclicker(db, term_id, [(section_id, enrollment_id)], actor)
                credit = next(c for c in self._iclicker_credits(db, term_id, [(section_id, enrollment_id)]) if c["credit_id"] == credit_id)
                return {**self._credit_view(db, term_id, credit), **counts}

    def iclicker_credit_withdraw(self, term_id, credit_id, data, actor="workspace"):
        """Takes back an iClicker credit: what it filled gets back what it replaced (`credit_iclicker`), and it fills nothing more."""
        _object(data, ("request_id",), "iClicker credit")
        request_id = _request_id(data.get("request_id"))
        request_sha = common.digest({"withdraw": credit_id})
        with self.rosters.lock():
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute("SELECT * FROM gradebook_iclicker_credits WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
                if prior and (prior["request_sha256"] != request_sha or prior["credit_id"] != credit_id):
                    raise Conflict("This request ID was used for a different change.")
                credit = next((c for c in self._iclicker_credits(db, term_id) if c["credit_id"] == credit_id), None)
                if not credit:
                    raise NotFound("This iClicker credit no longer exists.")
                if credit["state"] == "waiting":
                    db.execute("""INSERT INTO gradebook_iclicker_credits(id,credit_id,term_id,section_id,enrollment_id,class_date,state,reason,
                        request_id,request_sha256,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (str(uuid.uuid4()), credit_id, term_id, credit["section_id"], credit["enrollment_id"], credit["class_date"],
                                "withdrawn", credit["reason"], request_id, request_sha, actor, timestamp()))
                counts = self.credit_iclicker(db, term_id, [(credit["section_id"], credit["enrollment_id"])], actor)
                credit = next(c for c in self._iclicker_credits(db, term_id) if c["credit_id"] == credit_id)
                return {**self._credit_view(db, term_id, credit), **counts}

    MAX_SKIPS = 200

    def canvas_skip(self, term_id, data, actor="workspace"):
        """LEFT OUT OF IMPORTS (requirement, 2026-10-04): Canvas assignments the nightly
        WebAssign import and Sync with Canvas leave out from now on, remembered by Canvas ID like an import's own skip
        (`gradebook_canvas_links`, item NULL), without reading Canvas or writing a score. `assignments` [{key, title}]: the key
        "<course>:<assignment>" of a course a section of the term links, the title only for the record. An assignment already
        left out is not recorded again; choosing a column for it in Sync with Canvas brings it back."""
        _object(data, ("assignments", "request_id", "section_id"), "skip")  # section_id: which view to answer with
        request_id = _request_id(data.get("request_id"))
        entries = data.get("assignments")
        if not isinstance(entries, list) or not 1 <= len(entries) <= self.MAX_SKIPS:
            raise ValueError(f"Name 1 to {self.MAX_SKIPS} Canvas assignments to leave out.")
        chosen = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("key"), str) or not canvas_import.KEY.fullmatch(entry["key"]):
                raise ValueError("Each assignment is named by its Canvas course and assignment.")
            title = entry.get("title")
            if not isinstance(title, str) or not 0 < len(title.strip()) <= canvas_import.MAX_TITLE:
                raise ValueError("Each assignment needs its title.")
            chosen[entry["key"]] = " ".join(title.split())
        courses = {link["course_id"] for link in self._source_links("canvas", term_id).values()}
        if any(key.split(":")[0] not in courses for key in chosen):
            raise ValueError("Leave out only assignments of the Canvas courses this term's sections link.")
        request_sha = common.digest({"skip": sorted(chosen.items())})
        with self.rosters.lock():
            context = self._scoped(self._context(term_id), data.get("section_id"))
            with connect(self.path) as db:
                if not self._request(db, term_id, request_id, request_sha, "import"):
                    db.execute("BEGIN IMMEDIATE")
                    if self._request(db, term_id, request_id, request_sha, "import"):
                        raise Conflict("This skip was just recorded from another window.")
                    remembered = self._remembered(db, term_id, "canvas")
                    new = [key for key in sorted(chosen) if key not in remembered or remembered[key]["item_id"] is not None]
                    if new:
                        summary = {"source": "canvas", "skipped": sorted({chosen[key] for key in new}), "assignments": len(new),
                                   "courses": sorted({key.split(":")[0] for key in new}, key=int), "written": 0, "new_columns": 0}
                        reference = self._record_request(db, term_id, request_id, request_sha, "import", summary, actor)
                        for key in new:
                            course_id, assignment_id = key.split(":")
                            db.execute("""INSERT INTO gradebook_canvas_links(id,term_id,course_id,assignment_id,item_id,request_ref,actor,created_at)
                                VALUES(?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), term_id, course_id, assignment_id, None, reference, actor, timestamp()))
            return self._render(context)

    def iclicker_preview(self, term_id, data):
        return self._source_preview("iclicker", term_id, data)

    def iclicker_apply(self, term_id, data, actor="workspace"):
        return self._source_apply("iclicker", term_id, data, actor)

    # Sync with Canvas (README.md "Syncing with Canvas"; docs/decisions/canvas.md SYNC WITH CANVAS; requirement, 2026-10-06) ------------------------------------------------

    @staticmethod
    def _sent(db, term_id):
        """{(course, assignment, canvas user): the latest receipt} of the term's sends, with `own`: what Canvas held after Faculty's
        latest ACCEPTED write there (None when none was). A later failed or uncertain send does not hide it (2026-10-04 review: it
        made an import propose Canvas's older value back, and a send call Faculty's own value a grade entered in Canvas)."""
        found = {}
        for row in db.execute("""SELECT * FROM gradebook_canvas_sent WHERE term_id=? ORDER BY created_at,rowid""", (term_id,)):
            key = (row["course_id"], row["assignment_id"], row["canvas_user_id"])
            canvas = json.loads(row["canvas_json"])
            found[key] = {"sent": json.loads(row["sent_json"]), "canvas": canvas, "state": row["state"],
                          "row": common.row_key(row["section_id"], row["enrollment_id"]),
                          "own": canvas if row["state"] == "accepted" else (found.get(key) or {}).get("own")}
        return found

    @staticmethod
    def _receipts(db, term_id):
        """{(course, assignment, canvas user): Faculty's sends there, in order [{"sent", "canvas", "state", "at"}]}: what
        `canvas_export.agreed` reads."""
        found = {}
        for row in db.execute("""SELECT course_id,assignment_id,canvas_user_id,sent_json,canvas_json,state,created_at FROM gradebook_canvas_sent
                WHERE term_id=? ORDER BY created_at,rowid""", (term_id,)):
            found.setdefault((row["course_id"], row["assignment_id"], row["canvas_user_id"]), []).append(
                {"sent": json.loads(row["sent_json"]), "canvas": json.loads(row["canvas_json"]), "state": row["state"], "at": row["created_at"]})
        return found

    @staticmethod
    def _revisions(db, term_id):
        """{(item, row): [{"value", "at", "canvas"}]}: every revision of the term's cells in order, `canvas` when an import from
        Canvas wrote it (a sync's From-Canvas half and the nightly WebAssign import included): what `canvas_export.agreed` reads."""
        made = {row["id"]: change_log.origin(row["kind"], row["request_id"], json.loads(row["summary_json"])) == "canvas"
                for row in db.execute("SELECT id,kind,request_id,summary_json FROM gradebook_requests WHERE term_id=?", (term_id,))}
        found = {}
        for row in db.execute("""SELECT item_id,section_id,enrollment_id,status,points,request_ref,created_at FROM gradebook_scores
                WHERE term_id=? ORDER BY item_id,section_id,enrollment_id,seq""", (term_id,)):
            found.setdefault((row["item_id"], common.row_key(row["section_id"], row["enrollment_id"])), []).append(
                {"value": canvas_export.faculty_value({"status": row["status"], "points": row["points"]}), "at": row["created_at"],
                 "canvas": made.get(row["request_ref"], False)})
        return found

    @staticmethod
    def _synced(db, term_id):
        """{(column, course): when the latest sync left them in step}, from each sync's summary (`sync.in_step` {course: [columns]})."""
        found = {}
        for row in db.execute("""SELECT created_at,json_extract(summary_json,'$.sync.in_step') FROM gradebook_requests WHERE term_id=?
                AND kind='import' AND json_extract(summary_json,'$.sync') IS NOT NULL ORDER BY created_at,rowid""", (term_id,)):
            for course, items in json.loads(row[1] or "{}").items():
                for item_id in items:
                    found[(item_id, course)] = row[0]
        return found

    @staticmethod
    def _last_sync(db, term_id):
        row = db.execute("""SELECT id,created_at,request_id,json_extract(summary_json,'$.sync') FROM gradebook_requests WHERE term_id=?
            AND kind='import' AND json_extract(summary_json,'$.sync') IS NOT NULL ORDER BY created_at DESC,rowid DESC LIMIT 1""", (term_id,)).fetchone()
        return {"ref": row[0], "at": row[1], "request_id": row[2], **json.loads(row[3])} if row else None

    @staticmethod
    def _copy_links(db, term_id):
        """({course: {column: Canvas assignment}}, {column: the copy's assignment}): the assignments Faculty's Canvas copy made for
        gradebook columns and pushed (canvas/README.md), and every column the copy names, so that a sync never creates one twice
        (it makes it from the copy when `canvas_export.from_copy`, else the push does)."""
        try:
            row = db.execute("SELECT content_json FROM canvas_content_revisions WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone()
        except Exception:  # noqa: BLE001 - a catalog without Canvas's tables has no copy
            return {}, {}
        if not row:
            return {}, {}
        entries = {a["gradebook_item"]: a for a in json.loads(row[0]).get("assignments", []) if a.get("gradebook_item")}
        wanted = {a["id"]: item for item, a in entries.items()}
        found = {}
        for link in db.execute("""SELECT course_id,plan_key,canvas_id FROM canvas_content_links l WHERE term_id=? AND kind='assignment'
                AND rowid=(SELECT x.rowid FROM canvas_content_links x WHERE x.term_id=l.term_id AND x.course_id=l.course_id AND x.kind=l.kind
                AND x.plan_key=l.plan_key ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1)""", (term_id,)):
            if link["plan_key"] in wanted and link["canvas_id"]:
                found.setdefault(link["course_id"], {})[wanted[link["plan_key"]]] = link["canvas_id"]
        return found, entries

    @staticmethod
    def _sync_known(remembered, plan):
        """The links the To-Canvas half reads: the remembered ones, with the matches this sync makes to columns that exist."""
        known = dict(remembered)
        for key, item_id in (plan.get("remember") or {}).items():
            known[key] = {"item_id": item_id}
        return known

    @staticmethod
    def _sync_courses(value, links):
        """The courses a sync sends to: None for every linked course, else some of them."""
        if value is None:
            return None
        linked = {link["course_id"] for link in links.values()}
        if not isinstance(value, list) or not value or len(value) > 50 or any(not isinstance(c, str) or c not in linked for c in value):
            raise ValueError("Tick the sections whose Canvas courses this sync writes to.")
        return sorted(set(value), key=int)

    @staticmethod
    def _sync_choices(value):
        """The instructor's picks for held grades: {"<column>:<course>:<row>": "faculty" | "canvas"}."""
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > MAX_REPLACE or any(
                not isinstance(k, str) or len(k) > 300 or v not in ("faculty", "canvas") for k, v in value.items()):
            raise ValueError("Each held grade is kept as Faculty has it or taken from Canvas.")
        return dict(sorted(value.items()))

    @staticmethod
    def _sync_publish(value, setup, actor):
        """PUBLISH FROM SYNC (requirement, 2026-10-07): the columns ticked under Publish in Canvas, whose
        unpublished Canvas assignment the Sync publishes before sending their grades: [column ID], sorted. Publishing is the
        instructor's, never the assistant's (like posting grades)."""
        if value is None or value == []:
            return []
        if actor == "assistant":
            raise ValueError("Publishing an assignment in Canvas is the instructor's: tick it in Sync with Canvas yourself.")
        live = {item["id"] for item in setup.get("items", []) if not item.get("archived") and item.get("canvas") != "off"}
        if not isinstance(value, list) or len(value) > 500 or any(not isinstance(v, str) or v not in live for v in value):
            raise ValueError("Tick columns of this gradebook to publish in Canvas.")
        return sorted(set(value))

    def _sync_plan(self, db, context, links, read, sendread, options, courses, hints=None, automatic=False):
        """Both halves, planned from the catalog as it is: the From-Canvas half (`canvas_import.plan(sync=...)`) and the To-Canvas
        half against the cells as they will be after it (`sendread`: the summary's read in the To-Canvas shape, with what was read
        again laid over it), and what it does about posting (POSTING GRADES: `out["posting"]`, posting_policy.steps, from the same
        read and the setup as the From-Canvas half leaves it). `automatic`: the morning sync's plan (NIGHTLY SYNC). Returns (from
        plan, to plan, setup, meta, current, remembered, fingerprint)."""
        term_id = context["term"]["id"]
        plan, setup, meta, current, remembered = self._source_plan("canvas", db, context, links, read, options, hints, sync={})
        after = {item_id: dict(rows) for item_id, rows in current.items()}
        for change in plan["changes"]:
            if change["action"] in ("add", "update", "clear") and change["item_id"]:
                after.setdefault(change["item_id"], {})[change["row"]] = {"id": None, **change["proposed"], "source": "import"}
        students = [{key: student[key] for key in ("row", "section_id", "section", "name", "uin", "dropped")} for student in context["students"]]
        copied, named = self._copy_links(db, term_id)
        known = self._sync_known(remembered, plan)
        # ZEROS GO TO CANVAS (requirement, 2026-10-09): the empty cells the grid shows as 0 once the From-Canvas half is in.
        shown = self._results(db, term_id, setup, context["students"], _visible(after)).get("blanks") or {}
        zeros = frozenset((item_id, row) for row, marks in shown.items() for item_id, why in marks.items() if why == "zero")
        out = canvas_export.plan(setup=setup, cells=after, students=students, links=links, canvas=sendread, remembered=known,
                                 copied=copied, revisions=self._revisions(db, term_id), receipts=self._receipts(db, term_id),
                                 synced=self._synced(db, term_id), courses=courses, pending=named, publishing=True, automatic=automatic,
                                 zeros=zeros)
        # The columns this sync's From-Canvas half makes have no ID yet: the next sync plans their posting.
        out["posting"] = posting_policy.steps(setup, canvas_export.targets(setup, links, sendread, known, copied), sendread, out,
                                              policy=canvas_posting.policy(self, db, term_id, setup), courses=courses,
                                              unsure=canvas_posting.unsure(db, term_id) if automatic else frozenset(), automatic=automatic)
        fingerprint = common.digest({"rule": canvas_export.RULE, "from": plan["fingerprint"], "to": out["fingerprint"],
                                     "posting": posting_policy.digest(out["posting"])})
        return plan, out, setup, meta, current, remembered, fingerprint

    def _sync_hints(self, db, context, links, read, options):
        """Jev's stored picks for this plan's first-time matches (the instructor's own sync; the assistant's never asks)."""
        first = self._source_plan("canvas", db, context, links, read, options, sync={})
        return jev_matching.stored(db, context["term"]["id"], *self._askable(*first[:2]))

    def _sync_ask(self, term_id, links, read, options, jev):
        """JEV CANVAS-MATCH: first-time matches the titles could not place are asked of Jev with NO lock held (requests take up to
        15 s); its picks are stored, and every plan of the instructor's sync reads them (`_sync_hints`). Returns what was asked
        about, for the summary's own plan to read the picks of without planning again."""
        with self.rosters.lock():
            context = self._context(term_id)
            with connect(self.path) as db:
                first = self._source_plan("canvas", db, context, links, read, options, sync={})
        asked = self._askable(*first[:2])
        if asked[0]:
            jev_matching.suggest(jev, term_id, *asked)
        return asked

    @staticmethod
    def _posting_shown(found):
        """POSTING GRADES in the summary: per column and action, in how many Canvas courses the sync does it, how many grades it shows or
        hides there, and what waits (and why)."""
        shown = {}
        for step in found:
            entry = shown.setdefault((step["item_id"], step["action"]), {"item_id": step["item_id"], "title": step["title"], "action": step["action"],
                                                                         "why": step["why"], "courses": 0, "waiting": 0, "grades": 0, "waits": []})
            if step["waits"]:
                entry["waiting"] += 1
                entry["waits"] += [w for w in step["waits"] if w not in entry["waits"]]
            else:
                entry["courses"] += 1
                entry["grades"] += step["hidden"] if step["action"] == "post" else step["shown"] if step["action"] == "hide" else 0
            if step["new"]:
                entry["new"] = True
        order = {"switch": 0, "post": 1, "hide": 2}
        return sorted(shown.values(), key=lambda e: (order[e["action"]], e["title"].casefold()))

    @staticmethod
    def _sync_shown(plan, out, fingerprint, courses):
        """What the summary shows: the counts and choices, never a UIN."""
        def zeros(column):
            return sum(1 for value in column["values"] if (value["value"] or {}).get("zero"))
        to_canvas = [{key: column[key] for key in ("key", "item_id", "title", "course_id", "course", "sections", "assignment", "problems",
                                                   "notes", "counts", "create", "ticked", "send", "compare")}
                     | {"to_send": len(column["values"]), "clears": sum(1 for v in column["values"] if v["action"] == "clear"),
                        "fills": len(column["fills"])}
                     | ({"publish": True} if column.get("publish") else {})  # its unpublished assignment: published first when ticked
                     | ({"zeros": zeros(column)} if zeros(column) else {})  # empty cells counted 0, sent as 0 (ZEROS GO TO CANVAS)
                     | ({"zeros_only": True} if column.get("zeros_only") else {})  # Canvas grades it: only Faculty's 0s go
                     for column in out["columns"]]
        held = [{key: h[key] for key in ("key", "item_id", "course_id", "title", "name", "section", "canvas", "faculty", "take")} for h in out["held"]]
        from_columns = []
        for column in plan["columns"]:
            column = {key: value for key, value in column.items() if key != "askable"}
            from_columns.append(column)
        changes = [change for change in plan["changes"] if change["action"] in ("add", "update", "clear")]
        return {"fingerprint": fingerprint, "ready": plan["ready"], "options": plan["options"], "courses": plan["courses"],
                "ticked": courses, "held": held, "columns": to_canvas, "posting_plan": GradebookService._posting_shown(posting_policy.settle(
                    out.get("posting") or [], out, {}, {c["item_id"] for c in out["columns"] if c.get("publish") and c["send"] and (c["values"] or c["held"])})),
                "to_canvas": {"totals": out["totals"], "sending": out["sending"], "creating": out["creating"], "filling": out["filling"],
                              **({"unsure": out["unsure"]} if "unsure" in out else {})},
                "from_canvas": {"columns": from_columns, "new_items": plan["new_items"], "new_categories": plan["new_categories"],
                                "counts": plan["counts"], "warnings": plan["warnings"], "changes": changes, "unreadable": plan["unreadable"],
                                "not_in_canvas": plan["not_in_canvas"], "only_canvas": plan["only_canvas"], "sync": plan.get("sync"),
                                "unlinked_items": [entry for entry in plan["unlinked_items"] if not entry["compare_only"]], "widen": plan.get("widen") or {}}}

    def canvas_sync_preview(self, term_id, data, jev=None):
        """SYNC WITH CANVAS, the summary: what Faculty would send to Canvas, what it would take from Canvas (Canvas's own grades:
        WebAssign's and Canvas quizzes', the cells Faculty never filled, the assignments it links for the first time) and the grades
        changed in Canvas that wait for the instructor. ONE read of every linked course with no lock held; writes nothing anywhere.
        `jev`: the JEV adapter, given only for the instructor's own requests (JEV CANVAS-MATCH)."""
        data = data if isinstance(data, dict) else {}
        _object(data, ("options", "courses", "section_id"), "sync")
        if self.canvas is None:
            raise ValueError("Canvas is not set up in this workspace.")
        options = canvas_import.normalize_options(data.get("options"))
        links, read = self._source_read("canvas", term_id)
        courses = self._sync_courses(data.get("courses"), links)
        asked = self._sync_ask(term_id, links, read, options, jev) if jev is not None else None
        with self.rosters.lock():
            context = self._context(term_id)
            with connect(self.path) as db:
                hints = jev_matching.stored(db, term_id, *asked) if asked is not None else {}
                plan, out, *_, fingerprint = self._sync_plan(db, context, links, read, canvas_export.from_read(read), options, courses, hints)
        _keep_previewed(("sync", term_id, "", fingerprint), links, read)
        return self._sync_shown(plan, out, fingerprint, courses)

    def canvas_sync(self, term_id, data, actor="workspace", jev=None, automatic=False):
        """SYNC WITH CANVAS, the instructor's Sync: the summary's `fingerprint`, the same `options` and `courses`, the picks for
        held grades (`held`), a `request_id` (8 to 128 characters) and `confirmation: "sync"`. Both halves are planned again from the
        summary's own read (a gradebook change since is a 409); then the assignments the sync writes to or takes from are read again
        with no lock held, and a change there is a 409 too, nothing written. Both are recorded in ONE transaction (the `import`
        request, with the sync in its summary, and the send), then the send runs in a thread of this process (`_run_send`):
        receipts before Canvas is asked, settled by reading Canvas back, never sent again by themselves. POSTING GRADES: every sync
        also carries out each column's posting choice or rule as its summary showed it (`out["posting"]`, in the fingerprint): the
        switches to manual posting before its grades, the posts and hides after them (canvas_posting.after_sync). `automatic`: the
        morning sync (NIGHTLY SYNC), whose plan leaves out what an earlier uncertain write may have done. Answers the sync, its
        send and the section's gradebook."""
        started = time.monotonic()
        data = data if isinstance(data, dict) else {}
        _object(data, ("options", "courses", "held", "fingerprint", "request_id", "confirmation", "section_id", "post", "publish"), "sync")
        if data.get("confirmation") != "sync":
            raise ValueError("Confirm the sync.")
        if self.canvas is None:
            raise ValueError("Canvas is not set up in this workspace.")
        request_id = _request_id(data.get("request_id"))
        if len(request_id) < 8:
            raise ValueError("Request ID must be 8 to 128 characters.")
        fingerprint = data.get("fingerprint")
        if not isinstance(fingerprint, str) or not 1 <= len(fingerprint) <= 128:
            raise ValueError("Read the summary before syncing.")
        options = canvas_import.normalize_options(data.get("options"))
        held = self._sync_choices(data.get("held"))
        links = self._source_links("canvas", term_id)
        courses = self._sync_courses(data.get("courses"), links)
        if data.get("post"):  # the ticks before 2026-10-08: an older page
            raise ValueError("Posting in Canvas now happens at every sync, from each column's choice or its category's rule: reload the page.")
        with connect(self.path) as db:
            setup_now = _setup(db, term_id)[0]
            publish = self._sync_publish(data.get("publish"), setup_now, actor)
        request_sha = common.digest({"sync": fingerprint, "options": _plain(options), "courses": courses, "held": held,
                                     **({"publish": publish} if publish else {}), **({"automatic": True} if automatic else {})})
        with connect(self.path) as db:
            _term(db, term_id)
            if self._request(db, term_id, request_id, request_sha, "import"):
                return self._sync_answer(term_id, request_id, data.get("section_id"))
            running = [r["id"] for r in db.execute("SELECT id FROM gradebook_canvas_sends WHERE term_id=? AND status='running'", (term_id,))]
        if any(self._send_running(r) for r in running):
            raise Conflict("A sync with Canvas is already sending grades for this term.")
        send_id = str(uuid.uuid4())
        # Registered before Canvas is read, so a restart waits for the whole sync (canvas/stopping.py), and refused once Faculty is
        # stopping. Requirement, 2026-10-06: a restart while the sync still read Canvas had cut
        # its request off, and the page could not read the answer. The record then never shows it interrupted while it runs.
        self.canvas.writes.begin(self._send_key(send_id), "grades synced with Canvas", STOPPING_SYNC)
        self._syncs.add((term_id, request_id))
        batches, doing, progress, phases = [], [], None, {}
        try:
            key = ("sync", term_id, "", fingerprint)
            previewed = _previewed(key)
            if previewed is not None and previewed[0] == links:
                read = previewed[1]
            else:
                links, read = self._source_read("canvas", term_id, links=links)
                phases["Canvas read"] = time.monotonic()
            changed = Conflict("Canvas or the gradebook changed since the summary. Sync again.")
            sendread = canvas_export.from_read(read)
            with self.rosters.lock():
                context = self._context(term_id)
                # GRADEBOOK: reject a foreign section before committing the sync or starting any Canvas write.
                self._scoped(context, data.get("section_id"))
                with connect(self.path) as db:
                    hints = self._sync_hints(db, context, links, read, options) if jev is not None else {}
                    found = self._sync_plan(db, context, links, read, sendread, options, courses, hints, automatic)
            if found[-1] != fingerprint:
                raise changed
            self._sync_stopping()
            targets = canvas_export.written(found[1], *canvas_export.choose(found[1], held, publish))
            if targets:
                # What Sync writes to or takes from is read again, no lock held: a grade changed there since the summary is a 409.
                sendread = canvas_export.overlay(sendread, self.canvas.read_for_send(set(targets), lambda course, _: targets.get(course, set())))
            phases["planned and read again"] = time.monotonic()
            # CANVAS AS SYNC FOUND IT (requirement, 2026-10-08): the compressed copy this sync keeps, built with no lock held.
            kept = canvas_snapshot.build(sendread, context["students"], links, targets)
            with self.rosters.lock():
                context = self._context(term_id)
                with connect(self.path) as db:
                    plan, out, setup, meta, current, remembered, again = self._sync_plan(db, context, links, read, sendread, options, courses, hints,
                                                                                         automatic)
                    if again != fingerprint:
                        raise changed
                    if not plan.get("ready"):
                        raise ValueError("Resolve what needs your choice before syncing.")
                    batches, taken = canvas_export.choose(out, held, publish)
                    # POSTING GRADES: the steps the summary showed, a post waiting while a grade of it here waits for the instructor.
                    doing = [step for step in posting_policy.settle(out["posting"], out, held, publish) if not step["waits"]]
                    created = {step["key"] for step in doing if step["action"] == "switch" and step["new"]}
                    for batch in batches:
                        if batch.get("create") and batch["key"] in created:
                            batch["switch"] = True  # the assignment it creates posts by hand, as its category does here
                    # A restart asked for while the sync read Canvas: it ends here, before anything is recorded or written, rather
                    # than start a send the stop may cut off halfway (it waits at most STOP_WAIT seconds).
                    self._sync_stopping()
                    db.execute("BEGIN IMMEDIATE")
                    if self._request(db, term_id, request_id, request_sha, "import"):
                        raise Conflict("This sync was just started from another window.")
                    # CANVAS: two summaries can finish reading at once; claim the term again in the write transaction.
                    if any(self._send_running(row[0]) for row in db.execute(
                            "SELECT id FROM gradebook_canvas_sends WHERE term_id=? AND status='running'", (term_id,))):
                        raise Conflict("A sync with Canvas is already sending grades for this term.")
                    if any(_latest_id(db, term_id, batch["item_id"], value["row"]) != value["score_ref"] for batch in batches for value in batch["values"]):
                        raise Conflict(CHANGED)
                    sending = sum(len(batch["values"]) for batch in batches)
                    publishing = sum(1 for batch in batches if batch.get("publish"))
                    posting = {action: sum(1 for step in doing if step["action"] == action) for action in ("switch", "post", "hide")}
                    note = {"sync": {"send": send_id if batches or doing else None, "sending": sending, "taken": len(taken),
                                     "held": len(out["held"]) - sum(1 for h in out["held"] if held.get(h["key"])),
                                     "creating": sum(1 for batch in batches if batch.get("create")), "courses": courses,
                                     **({"publishing": publishing} if publishing else {}),
                                     **({"posting": {k: v for k, v in posting.items() if v}} if doing else {}),
                                     **({"automatic": True} if automatic else {}), **({"unsure": out["unsure"]} if out.get("unsure") else {}),
                                     # Canvas's columns that only take Faculty's 0s are never in step: Canvas grades them.
                                     "in_step": {course: sorted(c["item_id"] for c in out["columns"] if canvas_export.goes(c, publish) and not c.get("zeros_only")
                                                                and c["course_id"] == course)
                                                 for course in sorted({c["course_id"] for c in out["columns"] if canvas_export.goes(c, publish)
                                                                       and not c.get("zeros_only")}, key=int)}},
                            **({"automatic": "canvas-sync"} if automatic else {})}
                    reference = self._import_write(db, context, "canvas", term_id, plan, setup, meta, current, remembered, read, request_id,
                                                   request_sha, actor, note, taken)
                    canvas_snapshot.record(db, term_id, reference, kept)
                    # POSTING GRADES: what this sync read of each column's posting in Canvas, kept for the grid's mark.
                    # The setup as this sync just wrote it (its new columns and links count), in the same transaction.
                    canvas_posting.record(db, term_id, canvas_posting.read_rows(canvas_posting.where(self, db, term_id, _setup(db, term_id)[0], links,
                                                                                                     sendread), sendread), "nightly" if automatic else "sync")
                    # CONFIRMED BY A LATER READ: a value an earlier send could not read back that this sync's read shows as sent.
                    canvas_confirm.record(db, term_id, sendread)
                    manual = {(c["item_id"], c["course_id"]): not (c.get("assignment") or {}).get("automatic", True) for c in out["columns"]}
                    for batch in batches:
                        batch["manual"] = manual.get((batch["item_id"], batch["course_id"]), False)
                    if batches or doing:
                        switching = sum(1 for step in doing if step["action"] == "switch")
                        progress = {"total": sending, "accepted": 0, "failed": 0, "uncertain": 0, "assignments": len(batches),
                                    "done_assignments": 0, "current": "", "errors": [], "created": 0, **({"published": 0} if publishing else {}),
                                    **({"switched": 0} if switching else {})}
                        db.execute("INSERT INTO gradebook_canvas_sends VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                   (send_id, term_id, None, request_id, request_sha, fingerprint, json.dumps(batches), "running",
                                    json.dumps(progress), actor, timestamp(), None))
            if batches or doing:
                # The send (its switches, then its grades), then (POSTING GRADES) what Canvas shows of each sent assignment, then the
                # posts and hides; a restart waits for them too.
                self.canvas.writes.begin(canvas_posting.after_key(send_id), "grades posted in Canvas after a sync", STOPPING_SYNC)
                threading.Thread(target=canvas_posting.after_sync, args=(self, send_id, term_id, batches, progress, doing, actor,
                                                                         "nightly" if automatic else "sync"),
                                 name=f"canvas-sync-{send_id[:8]}", daemon=True).start()
            phases["recorded"] = time.monotonic()
        except BaseException:
            self.canvas.writes.end(self._send_key(send_id))
            self.canvas.writes.end(canvas_posting.after_key(send_id))
            raise
        finally:
            self._syncs.discard((term_id, request_id))
        if not (batches or doing):
            self.canvas.writes.end(self._send_key(send_id))
        _previewed(key, drop=True)
        # GRADED IN FACULTY, LINKED: a column this sync made for an assignment the copy ties to a Faculty assignment is linked at once,
        # before any later sync counts its empty cells or posts it by its due date.
        self.link_copy_columns_quietly(term_id, actor)
        answer = self._sync_answer(term_id, request_id, data.get("section_id"))
        # A LOST ANSWER: how long the page waited, in the journal (waitress logs no requests).
        took = time.monotonic() - started
        if took >= SYNC_SLOW:
            marks, last = [], started
            for name, at in phases.items():
                marks.append(f"{name} {at - last:.1f} s")
                last = at
            log.warning("Sync with Canvas %s took %.1f s: %s, answered %.1f s", request_id[:8], took, ", ".join(marks), time.monotonic() - last)
        return answer

    def canvas_sync_lookup(self, term_id, request_id, section_id=None, lost=None):
        """SYNC WITH CANVAS, A LOST ANSWER: what became of the instructor's Sync `request_id` when the page could not read its answer
        (none came in time, or one it could not read). "recorded": the sync's own answer, as its Sync would have answered (its
        send, the section's gradebook); "working": it is still reading Canvas or recording; "unknown": no sync of that request ID
        was recorded or is running, so nothing was written and the same Sync may be pressed again (the request ID answers once).
        A read: nothing is asked of Canvas. `lost` (the page's own words for what it got, first ask only) goes to the journal."""
        request_id = _request_id(request_id)
        with connect(self.path) as db:
            _term(db, term_id)
            row = db.execute("SELECT kind,summary_json FROM gradebook_requests WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        working = (term_id, request_id) in self._syncs
        state = "recorded" if row and row["kind"] == "import" and "sync" in json.loads(row["summary_json"]) else "working" if working else "unknown"
        if lost:
            log.warning("The page lost the answer to Sync with Canvas %s (%s): it is %s", request_id[:8], str(lost)[:120], state)
        if state == "recorded":
            return {"state": state, **self._sync_answer(term_id, request_id, section_id)}
        return {"state": state}

    @staticmethod
    def _written_courses(db, term_id):
        """The Canvas courses a Sync with Canvas has written to (a grade Canvas took, or an assignment it created; one student's cell
        is not a sync): the only ones the morning sync writes to (NIGHTLY SYNC: a course's first write is the instructor's)."""
        return sorted({row[0] for row in db.execute("""SELECT DISTINCT s.course_id FROM gradebook_canvas_sent s JOIN gradebook_canvas_sends d
            ON d.id=s.send_id WHERE d.term_id=? AND d.section_id IS NULL AND s.state='accepted' UNION SELECT c.course_id FROM gradebook_canvas_created c
            JOIN gradebook_canvas_sends d ON d.id=c.send_id WHERE d.term_id=? AND d.section_id IS NULL""", (term_id, term_id))}, key=int)

    def canvas_sync_nightly(self, term_id, night, links, read, courses):
        """NIGHTLY SYNC (docs/decisions/canvas.md; requirement, 2026-10-08): the
        term's Sync with Canvas by itself, from the morning's own read (`links`, `read`), writing to `courses` (the ones a sync has
        written to before). It is the instructor's Sync with the summary's defaults: new columns for Canvas's new assignments,
        Canvas's grades in the cells Faculty never filled, Canvas assignments created and unpublished ones published (as the
        summary ticks them), each column's posting; no held grade decided, Jev not asked, and nothing sent again that an earlier
        send could not read back (`automatic`). Returns (status, detail, request ID, send ID): "synced"; "unchanged" (nothing to
        do, nothing recorded); "needs_choice" (the From-Canvas half waits for the instructor); "failed"."""
        options = canvas_import.normalize_options(None)
        sendread = canvas_export.from_read(read)
        detail = {}
        # GRADED IN FACULTY, LINKED: a column the copy ties to a Faculty assignment counts and posts as its grading does, before the plan.
        self.link_copy_columns_quietly(term_id, "automatic")
        for attempt in (1, 2):
            with self.rosters.lock():
                context = self._context(term_id)
                with connect(self.path) as db:
                    plan, out, *_, fingerprint = self._sync_plan(db, context, links, read, sendread, options, courses, automatic=True)
                    unsure = {row[0] for row in db.execute("""SELECT p.item_id FROM gradebook_canvas_published p WHERE p.term_id=? AND p.state='uncertain'
                        AND p.rowid=(SELECT x.rowid FROM gradebook_canvas_published x WHERE x.term_id=p.term_id AND x.item_id=p.item_id
                        AND x.course_id=p.course_id ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1)""", (term_id,))}
            waiting = [{"key": column["key"], "title": column["title"]} for column in plan["columns"]
                       if column["mapping"]["action"] == "skip" and column["suggested"] in webassign_nightly.WAITING]
            detail = {"courses": courses, "waiting": waiting}
            if not plan["ready"]:
                return "needs_choice", {**detail, "problem": " ".join(plan["warnings"][-2:])[:500]}, None, None
            # The summary's default ticks: each unpublished assignment with grades to send, unless its last publish is uncertain.
            publish = sorted({column["item_id"] for column in out["columns"] if column.get("publish") and column["send"] and column["values"]} - unsure)
            written = sum(1 for change in plan["changes"] if change["action"] in ("add", "update", "clear") and not change["compare"])
            sending = sum(len(column["values"]) for column in out["columns"] if canvas_export.goes(column, publish))
            doing = [step for step in posting_policy.settle(out["posting"], out, {}, publish) if not step["waits"]]  # as Sync settles it
            if not (written or plan["new_items"] or plan["remember"] or (plan.get("sync") or {}).get("mark") or sending or out["filling"] or doing):
                return "unchanged", {**detail, "held": len(out["held"]), **({"unsure": out["unsure"]} if out.get("unsure") else {})}, None, None
            request_id = f"canvas-nightly:{night}:{uuid.uuid4().hex[:12]}"
            _keep_previewed(("sync", term_id, "", fingerprint), links, read)
            try:
                answer = self.canvas_sync(term_id, {"fingerprint": fingerprint, "courses": courses, "publish": publish, "request_id": request_id,
                                                    "confirmation": "sync"}, automatic=True)
            except Conflict as error:
                if attempt == 1:
                    continue  # the gradebook changed between the plan and the write: planned once more against it
                return "failed", {**detail, "problem": str(error.description)[:500]}, None, None
            except ValueError as error:
                return "failed", {**detail, "problem": str(error)[:500]}, None, None
            with connect(self.path) as db:
                reference = db.execute("SELECT id FROM gradebook_requests WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()[0]
            sync = answer["sync"]
            detail.update({key: sync[key] for key in ("sending", "held", "taken", "creating", "posting", "unsure", "publishing", "written", "new_columns")
                           if sync.get(key)})
            return "synced", detail, reference, (answer.get("send") or {}).get("id")
        return "failed", {**detail, "problem": "The gradebook kept changing while the sync ran."}, None, None

    def _sync_answer(self, term_id, request_id, section_id=None):
        """A recorded sync: its summary, its send (progress) when it sent anything, and the section's gradebook."""
        with connect(self.path) as db:
            row = db.execute("SELECT created_at,summary_json FROM gradebook_requests WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
            send = db.execute("SELECT * FROM gradebook_canvas_sends WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        summary = json.loads(row["summary_json"]) if row else {}
        with self.rosters.lock():
            gradebook = self._render(self._scoped(self._context(term_id), section_id))
        return {"sync": {"at": row["created_at"] if row else None, **{k: v for k, v in (summary.get("sync") or {}).items() if k != "in_step"},
                         "written": summary.get("written", 0), "new_columns": summary.get("new_columns", 0)},
                # The send as its status route shows it: with the posts after it (POSTING GRADES), which may still run when the send
                # had nothing to send.
                "send": self.canvas_send_status(term_id, send["id"]) if send else None, "gradebook": gradebook}

    @staticmethod
    def _send_key(send_id):
        return f"send:{send_id}"

    def _send_running(self, send_id):
        return bool(self.canvas) and self.canvas.writes.running(self._send_key(send_id))

    def _sync_stopping(self):
        """A sync still reading Canvas when Faculty is asked to stop ends with nothing recorded or written (503)."""
        if self.canvas.writes.closing:
            raise ServiceUnavailable(STOPPING_SYNC)

    def _send_receipts(self, send_id, term_id, batch, decide):
        """One receipt per value of a batch, in one transaction: `decide(value)` -> (state, what Canvas shows). Returns the states."""
        states = []
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            for value in batch["values"]:
                state, now = decide(value)
                states.append(state)
                section_id, enrollment_id = common.split_row(value["row"])
                db.execute("INSERT INTO gradebook_canvas_sent VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), send_id, term_id, section_id, enrollment_id, batch["item_id"], value["score_ref"],
                            batch["course_id"], batch["assignment_id"], value["user"], json.dumps(value["value"]),
                            json.dumps(now), state, timestamp()))
        return states

    def _send_view(self, row):
        # Only the process that runs Canvas writes settles a send it no longer runs (CanvasService.owner, 2026-10-09 review).
        if row["status"] == "running" and self.canvas is not None and self.canvas.owner and not self._send_running(row["id"]):
            # Read again once this process says it no longer sends it: a send records how it ended BEFORE it leaves
            # `canvas.writes`, so a row read just before that showed a finished send "interrupted" (seen under load, 2026-10-06).
            with connect(self.path) as db:
                db.execute("UPDATE gradebook_canvas_sends SET status='interrupted', finished_at=? WHERE id=? AND status='running'", (timestamp(), row["id"]))
                row = db.execute("SELECT * FROM gradebook_canvas_sends WHERE id=?", (row["id"],)).fetchone()
        return {"id": row["id"], "status": row["status"], "section_id": row["section_id"], "progress": json.loads(row["progress_json"] or "{}"),
                "created_at": row["created_at"], "finished_at": row["finished_at"]}

    def canvas_send_status(self, term_id, send_id):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM gradebook_canvas_sends WHERE id=? AND term_id=?", (send_id, term_id)).fetchone()
            if not row:
                raise NotFound("That send was not found.")
            posts = canvas_posting.for_send(self, db, send_id)  # POSTING GRADES: the posts after sending (additive, only when any)
            return {**self._send_view(row), **({"posts": posts, "posting": self.canvas.writes.running(canvas_posting.after_key(send_id))} if posts
                                               or self.canvas.writes.running(canvas_posting.after_key(send_id)) else {})}

    def canvas_send_cancel(self, term_id, send_id):
        self.canvas_send_status(term_id, send_id)
        self._cancel_sends.add(send_id)
        return self.canvas_send_status(term_id, send_id)

    def _send_progress(self, send_id, progress, status=None):
        with connect(self.path) as db:
            if status:
                db.execute("UPDATE gradebook_canvas_sends SET progress_json=?,status=?,finished_at=? WHERE id=? AND status='running'",
                           (json.dumps(progress), status, timestamp(), send_id))
            else:
                db.execute("UPDATE gradebook_canvas_sends SET progress_json=? WHERE id=? AND status='running'", (json.dumps(progress), send_id))

    SEND_WAIT = 120  # seconds Canvas's bulk update may take before its result is only read back
    SEND_COURSES = 4  # Canvas courses one send writes to at once; each course's own assignments go one after another

    def _create_assignment(self, client, send_id, term_id, batch):
        """Creates the column's Canvas assignment of an approved send, then records it: (assignment ID, "") or (None, why not).
        Canvas is read first, so an assignment of that name made since the preview is never doubled; an answer that was lost is
        settled by reading Canvas again (the one new assignment of that name, if there is exactly one, is the one asked for)."""
        from canvas import web
        spec, course = batch["create"], batch["course_id"]
        before = client.assignments(course)
        if any(canvas_export.fold(a["name"]) == canvas_export.fold(spec["name"]) for a in before):
            return None, f"Canvas now has an assignment named “{spec['name']}”: nothing was created. Preview again."
        payload = {"name": spec["name"], "points_possible": float(spec["points"]), "grading_type": "points", "submission_types": ["none"],
                   "published": True, "assignment_group_id": int(spec["group_id"])}
        made = spec.get("copy")  # COLUMN ASSIGNMENTS: made as Faculty's copy has it (canvas_export.from_copy)
        if made:
            payload.update({"submission_types": made["submission_types"], "omit_from_final_grade": made["omit_from_final_grade"],
                            **{k: made[k] for k in ("due_at", "unlock_at", "lock_at") if made.get(k)}})
        adopted = False
        try:
            answer = client.write("POST", f"/api/v1/courses/{int(course)}/assignments", {"assignment": payload})
            assignment_id = str(answer.get("id") or "") if isinstance(answer, dict) else ""
        except web.SignedOut:
            raise
        except web.CanvasError as error:
            if error.refused:
                return None, f"Canvas refused to create “{spec['name']}”: {error}"
            # No answer, a server error, or a success Faculty could not read: it may exist now, and is adopted when it does.
            known = {a["id"] for a in before}
            found = [a for a in client.assignments(course) if a["id"] not in known and canvas_export.fold(a["name"]) == canvas_export.fold(spec["name"])]
            if len(found) != 1:
                return None, f"Canvas's answer to creating “{spec['name']}” was lost and Faculty cannot tell whether it was made: look in Canvas."
            assignment_id, adopted = found[0]["id"], True
        if not assignment_id.isdigit():
            return None, f"Canvas's answer to creating “{spec['name']}” could not be read: look in Canvas."
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("INSERT INTO gradebook_canvas_created VALUES(?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), send_id, term_id, batch["item_id"], course, assignment_id,
                        json.dumps({**spec, "adopted": adopted}), timestamp()))
        if made and self.canvas_content is not None:
            # The copy's link, with the snapshot a push records, so the next push finds it in step (canvas/column_assignments.py).
            try:
                row = next((a for a in client.assignments(course) if a["id"] == assignment_id), None)
                if row:
                    from canvas.column_assignments import created_link
                    created_link(self.canvas_content, term_id, course, made["key"], row, {spec["group_id"]: spec["group"]})
            except web.SignedOut:
                raise
            except Exception:  # noqa: BLE001 - the assignment and its record stand; the next push matches it by name
                log.exception("could not record Faculty's copy link of the assignment Sync created (%s in course %s)", made["key"], course)
        return assignment_id, ""

    def _publish_receipt(self, send_id, term_id, batch, state, row):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("INSERT INTO gradebook_canvas_published VALUES(?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), send_id, term_id, batch["item_id"], batch["course_id"], batch["assignment_id"], state,
                        json.dumps({"name": row["name"], "published": row["published"]} if row else None), timestamp()))

    def _publish_assignment(self, client, send_id, term_id, batch):
        """PUBLISH FROM SYNC: publishes the column's unpublished Canvas assignment of an approved send before its grades go: "" once
        Canvas shows it published, else why not (nothing is then sent). Canvas is read first (one published since the summary is
        left as it is); a receipt (`gradebook_canvas_published`) is recorded uncertain BEFORE Canvas is asked and settled by reading
        the assignment back, and nothing is asked again by itself. When the assignment is one of Faculty's Canvas copy, the copy's
        assignment is published too (canvas/column_assignments.py `published_by_sync`), so a later push never takes it back."""
        from canvas import web
        course, assignment_id, name = batch["course_id"], batch["assignment_id"], batch["publish"]["name"]
        find = lambda: next((a for a in client.assignments(course) if a["id"] == assignment_id), None)
        before = find()
        if before is None:
            return f"“{name}” is no longer in Canvas: nothing was sent. Sync again."
        if before["published"]:
            return ""
        self._publish_receipt(send_id, term_id, batch, "uncertain", before)
        row, problem = None, ""
        try:
            answer = client.write("PUT", f"/api/v1/courses/{int(course)}/assignments/{int(assignment_id)}", {"assignment": {"published": True}})
            row = web.assignment_row(answer) if isinstance(answer, dict) and answer.get("id") is not None else None
        except web.SignedOut:
            raise  # the uncertain receipt stands
        except web.CanvasError as error:
            problem = f"Canvas refused to publish “{name}”: {error}" if error.refused else ""
        if row is None or not row["published"]:
            try:
                row = find()  # Canvas's answer was lost or did not say: what Canvas shows now settles it
            except web.SignedOut:
                raise
            except web.CanvasError:
                row = None
        state = "uncertain" if row is None else "accepted" if row["published"] else "failed"
        self._publish_receipt(send_id, term_id, batch, state, row)
        if state != "accepted":
            return problem or (f"Canvas did not publish “{name}”: nothing was sent. Look in Canvas, then sync again." if state == "failed"
                               else f"Canvas's answer to publishing “{name}” could not be read: nothing was sent. Look in Canvas.")
        if self.canvas_content is not None:
            try:
                from canvas.column_assignments import published_by_sync
                published_by_sync(self.canvas_content, term_id, course, before, row, client.groups(course), send_id)
            except web.SignedOut:
                raise
            except Exception:  # noqa: BLE001 - Canvas has it published either way; a later push asks about the difference
                log.exception("could not publish Faculty's copy of the assignment Sync published (%s in course %s)", assignment_id, course)
        return ""

    def _policy_receipt(self, send_id, term_id, step, state, row):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("INSERT INTO gradebook_canvas_policies VALUES(?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), send_id, term_id, step["item_id"], step["course_id"], step["assignment_id"], state,
                        json.dumps({"name": row["name"], "post_manually": row["post_manually"]} if row else None), timestamp()))

    def _switch_policy(self, client, send_id, term_id, step):
        """POSTING GRADES, MANUAL POSTING (requirement, 2026-10-08): the column's Canvas assignment in this course set to post grades by hand before its
        grades go: "" once Canvas shows it so, else why not. Canvas is read first (one that posts by hand already is left as it
        is); a receipt (`gradebook_canvas_policies`) is recorded uncertain BEFORE Canvas is asked and settled by reading the
        assignment back, and nothing is asked again by itself."""
        from canvas import web
        course, assignment_id, name = step["course_id"], step["assignment_id"], step["title"]
        find = lambda: next((a for a in client.assignments(course) if a["id"] == assignment_id), None)
        before = find()
        if before is None:
            return f"“{name}” is no longer in Canvas: nothing was sent there. Sync again."
        if before["post_manually"]:
            return ""
        self._policy_receipt(send_id, term_id, step, "uncertain", before)
        row, problem = None, ""
        try:
            client.set_post_policy(assignment_id, manual=True)
        except web.SignedOut:
            raise  # the uncertain receipt stands
        except web.CanvasError as error:
            problem = f"Canvas refused to make “{name}” post by hand: {error}" if error.refused else ""
        try:
            row = find()  # what Canvas shows now settles it
        except web.SignedOut:
            raise
        except web.CanvasError:
            row = None
        state = "uncertain" if row is None else "accepted" if row["post_manually"] else "failed"
        self._policy_receipt(send_id, term_id, step, state, row)
        if state != "accepted":
            return problem or (f"Canvas still posts “{name}” automatically: its grades were not sent there. Look in Canvas, then sync again."
                               if state == "failed" else f"Canvas's answer to posting “{name}” by hand could not be read: its grades were not sent there.")
        return ""

    def _run_send(self, send_id, term_id, batches, progress, switches=None):
        """The send of a Sync with Canvas: each Canvas course in a thread of its own, at most SEND_COURSES at once, and each
        course's assignments one after another (requirement, 2026-10-06: one after another, 20 assignments in MATH 140's four
        courses took seven minutes, long enough for a restart to cut off). A cancel, a signed-out session or a failure in one
        course stops every course before its next assignment; what Canvas was already asked is still read back (`_send_batch`).
        `switches` {course: [posting step]}: the course's assignments switched to post by hand first (POSTING GRADES); a column
        whose switch did not take sends nothing there (its values are failed, so the next sync knows Canvas never took them)."""
        from concurrent.futures import ThreadPoolExecutor

        from canvas import web  # the Canvas client; imported here so the gradebook never needs Canvas to load
        courses = {course: [] for course in (switches or {})}
        for batch in batches:
            courses.setdefault(batch["course_id"], []).append(batch)
        counting, stop, sending, outcome = threading.Lock(), threading.Event(), {}, set()

        def note(errors=(), **counts):
            """Adds `counts` and `errors` to the send's progress and records it, one thread at a time."""
            with counting:
                for key, value in counts.items():
                    progress[key] += value
                progress["errors"].extend(errors)
                progress["current"] = ", ".join(dict.fromkeys(sending.values()))  # the assignments being sent now
                self._send_progress(send_id, progress)

        def course(course_id, own):
            try:
                with self.canvas.client(patient=True) as client:
                    kept, switched = set(), set()  # columns whose assignment here still posts automatically, or now by hand
                    for step in (switches or {}).get(course_id, []):
                        if send_id in self._cancel_sends or stop.is_set():
                            break
                        problem = self._switch_policy(client, send_id, term_id, step)
                        if problem:
                            kept.add(step["item_id"])
                            note([{"label": step["title"], "message": problem[:300]}])
                        else:
                            switched.add(step["item_id"])
                            note(switched=1)
                    for batch in own:
                        if batch["item_id"] in kept:
                            self._send_receipts(send_id, term_id, batch, lambda value: ("failed", None))
                            note(failed=len(batch["values"]), done_assignments=1)
                            continue
                        if batch["item_id"] in switched:
                            batch["manual"] = True
                        if send_id in self._cancel_sends:
                            outcome.add("cancelled")
                            stop.set()
                        if stop.is_set():
                            return
                        with counting:
                            sending[course_id] = batch["title"]
                        note()
                        self._send_batch(client, send_id, term_id, batch, note)
            except web.SignedOut as error:
                stop.set()
                outcome.add("failed")
                with counting:  # recorded at once: the page offers Sign in to Canvas while other courses finish what they sent
                    if not progress.get("signed_out"):  # once, however many courses find it
                        progress["errors"].append({"label": "Canvas", "message": str(error)})
                    progress["signed_out"] = True
                    self._send_progress(send_id, progress)
            except Exception as error:  # noqa: BLE001 - the send must end with a status
                stop.set()
                outcome.add("failed")
                note([{"label": "Faculty", "message": f"{type(error).__name__}: {error}"[:300]}])
            finally:
                with counting:
                    sending.pop(course_id, None)

        try:
            # The workers keep this thread's name ("canvas-sync-..."): what tells the send's reads from the summary's.
            with ThreadPoolExecutor(max_workers=min(self.SEND_COURSES, len(courses)) or 1, thread_name_prefix=threading.current_thread().name) as pool:
                for done in [pool.submit(course, course_id, own) for course_id, own in courses.items()]:
                    done.result()
        except Exception as error:  # noqa: BLE001 - the send must end with a status
            outcome.add("failed")
            note([{"label": "Faculty", "message": f"{type(error).__name__}: {error}"[:300]}])
        finally:
            status = "failed" if "failed" in outcome else "cancelled" if "cancelled" in outcome else "done"
            with counting:
                progress["current"] = ""
                self._send_progress(send_id, progress, status)
            self._cancel_sends.discard(send_id)
            self.canvas.writes.end(self._send_key(send_id))

    def _send_batch(self, client, send_id, term_id, batch, note):
        """One assignment of a send: one Canvas bulk update, followed until Canvas finishes, then READ BACK: each value is recorded
        with what Canvas shows (accepted when it shows what was sent; otherwise failed, or uncertain when Canvas never said it
        finished). `note(errors, **counts)` adds to the send's progress. A signed-out session is raised once its receipts stand."""
        from canvas import web
        if batch.get("create"):
            batch["assignment_id"], problem = self._create_assignment(client, send_id, term_id, batch)
            if not batch["assignment_id"]:
                # Nothing was sent: the assignment could not be made.
                note([{"label": batch["title"], "message": problem[:300]}], failed=len(batch["values"]), done_assignments=1)
                return
            note(created=1)
            if batch.get("switch"):
                # POSTING GRADES: the assignment just made posts by hand, as its column's category does here, before any grade.
                problem = self._switch_policy(client, send_id, term_id, {"item_id": batch["item_id"], "title": batch["title"],
                                                                         "course_id": batch["course_id"], "assignment_id": batch["assignment_id"]})
                if problem:
                    self._send_receipts(send_id, term_id, batch, lambda value: ("failed", None))
                    note([{"label": batch["title"], "message": problem[:300]}], failed=len(batch["values"]), done_assignments=1)
                    return
                batch["manual"] = True
                note(switched=1)
        if batch.get("publish"):
            problem = self._publish_assignment(client, send_id, term_id, batch)
            if problem:
                # Nothing was sent: the assignment is not published. Its values are failed, as when Canvas refuses an update, so the
                # next sync knows Canvas never took them (this sync counted the column in step).
                self._send_receipts(send_id, term_id, batch, lambda value: ("failed", None))
                note([{"label": batch["title"], "message": problem[:300]}], failed=len(batch["values"]), done_assignments=1)
                return
            note(published=1)
        grades = {v["user"]: canvas_export.grade_data(v) for v in batch["values"]}
        # Every value is recorded as uncertain BEFORE Canvas is asked, and settled once Canvas is read back: a send cut off in
        # between (the session ending while Canvas applies it, a stop) still holds a receipt for every value it may have written
        # (2026-10-04 review; OUTSIDE SERVICES).
        self._send_receipts(send_id, term_id, batch, lambda value: ("uncertain", None))
        finished, problem, answer = False, "", None
        try:
            answer = client.update_grades(batch["course_id"], batch["assignment_id"], grades)
            deadline = time.monotonic() + self.SEND_WAIT
            while True:
                state = str(answer.get("workflow_state") or "")
                if state == "completed":
                    finished = True
                    break
                if state == "failed":
                    finished, problem = True, str(answer.get("message") or "Canvas could not apply the grades.")
                    break
                if time.monotonic() > deadline or not answer.get("id"):
                    break
                time.sleep(1)
                answer = client.progress(answer["id"])
        except web.SignedOut:
            if answer is None:  # Canvas refused the update itself: nothing was applied
                self._send_receipts(send_id, term_id, batch, lambda value: ("failed", None))
                note(failed=len(batch["values"]))
            else:  # Canvas took the update: the uncertain receipts stand
                note(uncertain=len(batch["values"]))
            raise
        except web.CanvasError as error:
            problem = str(error)
            # Canvas refusing the update itself (4xx) applied nothing: the values are failed, not "still working".
            finished = finished or (answer is None and error.refused)
        try:
            shown = client.assignment_submissions(batch["course_id"], batch["assignment_id"])
            batch["posted"] = canvas_posting.counts(shown.values())  # POSTING GRADES: the grid's mark after the send
        except web.SignedOut:
            note(uncertain=len(batch["values"]))  # sent, never read back: the uncertain receipts stand
            raise
        except web.CanvasError as error:
            shown, problem = None, problem or str(error)

        def settled(value):
            now = canvas_export.canvas_value(shown.get(value["user"]), entered=True) if shown is not None else None
            if shown is None:
                return "uncertain", None  # nothing could be read back
            if canvas_export.agrees(value["value"], shown.get(value["user"])):
                return "accepted", now
            if finished or (problem and not shown):
                return "failed", now
            return "uncertain", now  # Canvas never said it finished, and does not show it yet
        states = collections.Counter(self._send_receipts(send_id, term_id, batch, settled))
        note([{"label": batch["title"], "message": problem[:300]}] if problem else (), done_assignments=1, **states)

    # Export -----------------------------------------------------------------------------------------------------------

    def export_csv(self, term_id, section_id=None):
        """The instructor's file: one row per student with UIN, each column's value, category and overall percentages."""
        with self.rosters.lock():
            context = self._context(term_id)
            with connect(self.path) as db:
                setup, _ = _setup(db, term_id)
                cells = _visible(_cells(db, term_id))
                results = self._results(db, term_id, setup, context["students"], cells)
        section = None
        if section_id not in (None, ""):
            section = next((section for section in context["sections"] if section["id"] == section_id), None)
            if not section:
                raise ValueError("Choose a section of this term.")
        items = [item for item in setup.get("items", []) if not item.get("archived")]
        categories = setup.get("categories", [])
        safe = lambda value: "'" + value if value and value[0] in "=+-@\t\r" else value  # spreadsheet formula prefixes
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "UIN", "Section", *[safe(f"{item['title']} ({item['points']})") for item in items],
                         *[safe(f"{category['name']} %") for category in categories], "Overall %", "Letter"])
        shown = {"excused": "EX", "missing": "MI"}
        for student in context["students"]:
            if section and student["section_id"] != section["id"]:
                continue
            result = results["students"].get(student["row"]) or {}
            values = []
            for item in items:
                cell = cells.get(item["id"], {}).get(student["row"]) if setups.applies(item, student["section_id"]) else None
                values.append("" if not cell else cell["points"] if cell["status"] == "scored" else shown[cell["status"]])
            percents = [((result.get("categories") or {}).get(category["id"]) or {}).get("percent") or "" for category in categories]
            writer.writerow([safe(student["name"]), student["uin"], student["section"], *values, *percents,
                             result.get("percent") or "", safe(result.get("letter") or "")])
        term = context["term"]
        label = f"{term['code']} {term_label(term)}" + (f" section {section['number']}" if section else "") + " gradebook"
        name = "".join(char if char.isalnum() or char in " -_" else "-" for char in label).strip() or "gradebook"
        return output.getvalue().encode("utf-8-sig"), f"{name}.csv"
