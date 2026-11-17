"""The WebAssign import of the morning sync (README.md "The morning sync"; docs/decisions/canvas.md WEBASSIGN NIGHTLY and NIGHTLY
SYNC; requirement, 2026-10-04).

WebAssign sends each score to Canvas itself (Cengage's LTI sync: as a student submits and as a score is edited in WebAssign), and
Faculty has no way into WebAssign of its own (Cengage offers instructors no API, and LTI sends grades only to the course's LMS). So
each campus morning, for a term whose first Sync with Canvas is still the instructor's, Faculty runs Sync with Canvas's From-Canvas
half BY ITSELF for the assignments WebAssign grades (`canvas.web.graded_by_webassign`: the Canvas assignment hands its work to
Cengage's tool); a term already synced gets the whole morning sync, whose From-Canvas half does the same. Nothing names a column or
a course: a new term's WebAssign assignments are found the same way.

- It reads the linked courses with the kept Canvas session, keeps only WebAssign's assignments and Canvas's quizzes (instructor,
  2026-10-06: Canvas quizzes are Canvas's, like WebAssign) and plans them exactly as the
  instructor's own Sync with Canvas does with no choice made: the remembered match, else the one column of the same title, else
  a new column in the category named after the assignment group. Jev is not asked. An assignment the titles cannot place (several
  columns, no points) waits for the instructor's own Sync with Canvas, and the rest is imported.
- It writes through that import's own apply (`GradebookService._source_apply`) as Sync with Canvas's From-Canvas half does
  (requirement, 2026-10-06: Canvas ALWAYS wins for WebAssign; docs/decisions/canvas.md SYNC WITH CANVAS): the column MIRRORS
  Canvas, its score written over any value (a typed one included) and a blank in Canvas emptying the cell; Canvas's missing flag is
  not recorded as missing, excused is excused, a submission waiting for review changes nothing, and Faculty's own grading never
  takes a WebAssign assignment. A column it writes to is marked WebAssign's (`canvas` "webassign": read-only in Faculty, never
  sent to Canvas). The request (summary `automatic: "webassign"`) is on the Changes page; a night with nothing to write records no
  request.
- The morning sync records each attempt (gradebook/canvas_nightly.py); the 10 pm import's `gradebook_nightly_runs` rows (migration
  007, owned by the term, append-only) are read as history.

- DUE DATES FOLLOW CANVAS (requirement, 2026-10-05). WebAssign sends a changed due date to Canvas itself, and a Canvas
  quiz's is Canvas's own, so each morning also moves, for WebAssign's assignments and Canvas quizzes only:
  each linked gradebook column's due date to Canvas's (the latest campus date of its linked courses), unless the column's date
  was last set by someone other than Canvas (`follow_gradebook`); and Faculty's Canvas copy (`CanvasContentService.
  follow_due_dates`, with WebAssign's available-from and until dates too: requirement, 2026-10-17), so a later push never offers
  the old date again. Each is one revision whose request ID starts with
  FOLLOWED; a night that moved nothing records nothing, and a failure here never holds back the scores.

It never writes to Canvas or WebAssign, and holds no lock while Canvas is read.
"""
import json
import logging
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict

from canvas.web import graded_by_webassign
from course_storage import exclusive
from rosters import connect

from . import canvas_import, common

LOG = logging.getLogger(__name__)
LOCK = "6723d2e1-bc42-4cda-b989-9ed5ce2d5e68"  # one nightly round at a time, across processes
SOURCE = "webassign"
WAITING = ("ambiguous", "points")              # suggestions that leave an assignment for the instructor's own choice
FOLLOWED = "webassign-due:"                    # request IDs of the revisions that moved due dates to Canvas's
PUBLISHED = "canvas-published:"                # request IDs of the revisions that published the copy's assignment as Canvas had
MIRROR = {"mirror": None}                      # Sync with Canvas's From-Canvas half: what Canvas grades mirrors Canvas


def webassign_only(read, quizzes=False):
    """The courses' read (`CanvasService.read_grades`) keeping only the assignments WebAssign grades (with `quizzes`, Canvas's
    quizzes too: Canvas grades them itself, requirement, 2026-10-06), and how many there are."""
    kept, count = {}, 0
    for course_id, data in read.items():
        groups = []
        for group in data["groups"]:
            assignments = [a for a in group["assignments"] if graded_by_webassign(a.get("tool", "")) or (quizzes and a.get("quiz"))]
            count += len(assignments)
            groups.append({**group, "assignments": assignments})
        kept[course_id] = {**data, "groups": groups}
    return kept, count


def latest(db, term_id, canvas_status=None):
    """The term's latest nightly run as the gradebook page shows it; None when there has been none. `waiting` leaves out the
    assignments the instructor has matched or skipped since; `signed_in_since` says a sign-in to Canvas came after a run that
    found Canvas signed out (`canvas_status`: CanvasService.status(), read only for such a run)."""
    row = db.execute("""SELECT night,status,detail_json,created_at FROM gradebook_nightly_runs WHERE term_id=? AND source=?
        ORDER BY created_at DESC,rowid DESC LIMIT 1""", (term_id, SOURCE)).fetchone()
    if not row:
        return None
    detail = json.loads(row["detail_json"])
    decided = {f"{r[0]}:{r[1]}" for r in db.execute("SELECT course_id,assignment_id FROM gradebook_canvas_links WHERE term_id=? AND created_at>?",
                                                   (term_id, row["created_at"]))}
    # One title once: the same WebAssign assignment waits in every section's course ("Graphing Practice" in all four, 2026-10-04).
    # `waiting_keys` names each course's assignment for Leave out (GradebookService.canvas_skip).
    open_ = [entry for entry in detail.get("waiting", []) if entry["key"] not in decided]
    found = {"night": row["night"], "status": row["status"], "at": row["created_at"],
             "waiting": list(dict.fromkeys(entry["title"] for entry in open_)), "waiting_keys": open_}
    found.update({key: detail[key] for key in ("written", "new_columns", "problem") if key in detail})
    if row["status"] == "signed_out" and canvas_status is not None:
        saved = (canvas_status() or {}).get("saved_at")
        found["signed_in_since"] = bool(saved and saved > row["created_at"][:19] + "Z")
    return found


def due_origins(db, term_id, item_ids):
    """{column ID: the request ID of the setup revision that last changed its due date}, None for a column made without one and
    never given one since (no date of the instructor's to keep)."""
    origins, last = {}, {}
    for request_id, items in db.execute("""SELECT request_id,json_extract(setup_json,'$.items') FROM gradebook_setups WHERE term_id=?
            ORDER BY revision""", (term_id,)):
        for item in json.loads(items or "[]"):
            if item["id"] not in item_ids:
                continue
            due = item.get("due_date") or ""
            if item["id"] not in last:
                origins[item["id"]] = request_id if due else None
            elif last[item["id"]] != due:
                origins[item["id"]] = request_id
            last[item["id"]] = due
    return origins


class WebAssignNightly:
    def __init__(self, gradebook, storage=None, content=None):
        self.gradebook, self.storage, self.path = gradebook, storage, gradebook.path
        self.content = content  # CanvasContentService: Faculty's Canvas copy, whose WebAssign due dates follow Canvas too

    def lock(self):
        return exclusive(self.storage, LOCK, Path(self.path).parent / ".webassign-nightly.lock", "The nightly WebAssign import is running now.")

    def _import(self, term_id, night, links, kept, count):
        """The night's import from Canvas of WebAssign's assignments: (status, detail, the request it recorded or None)."""
        gradebook = self.gradebook
        options = canvas_import.normalize_options(None)
        for attempt in (1, 2):
            try:
                with gradebook.rosters.lock():
                    context = gradebook._context(term_id)
                    with connect(self.path) as db:
                        plan = gradebook._source_plan("canvas", db, context, links, kept, options, sync=MIRROR)[0]
            except ValueError as error:  # e.g. two remembered assignments of one course going to one column
                return "failed", {"assignments": count, "problem": str(error)[:500]}, None
            waiting = [{"key": column["key"], "title": column["title"]} for column in plan["columns"]
                       if column["mapping"]["action"] == "skip" and column["suggested"] in WAITING]
            detail = {"assignments": count, "waiting": waiting}
            if not plan["ready"]:
                return "needs_choice", {**detail, "problem": " ".join(plan["warnings"][-2:])[:500]}, None
            written = sum(1 for change in plan["changes"] if change["action"] in ("add", "update", "clear") and not change["compare"])
            if not written and not plan["new_items"] and not plan["remember"] and not plan["sync"]["mark"]:
                return "unchanged", detail, None
            request_id = f"webassign-nightly:{night}:{uuid.uuid4().hex[:12]}"
            try:
                gradebook._source_apply("canvas", term_id, {"options": plan["options"], "fingerprint": plan["fingerprint"],
                                                            "request_id": request_id}, given=(links, kept), note={"automatic": SOURCE},
                                        sync=MIRROR)
            except Conflict:
                if attempt == 1:
                    continue  # the gradebook changed between the plan and the write: planned once more against it
                return "failed", {**detail, "problem": "The gradebook kept changing while the import ran."}, None
            except ValueError as error:
                return "failed", {**detail, "problem": str(error)[:500]}, None
            with connect(self.path) as db:
                reference = db.execute("SELECT id FROM gradebook_requests WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()[0]
            return "imported", {**detail, "written": written, "new_columns": len(plan["new_items"])}, reference

    def _follow(self, term_id, night, links, kept):
        """DUE DATES FOLLOW CANVAS: the gradebook's columns, then Faculty's Canvas copy. What moved is counted in the night's
        detail; a failure is logged and named there and never changes the night's status (the scores are what it is for)."""
        found = {}
        try:
            moved = self.follow_gradebook(term_id, night, links, kept)
        except Exception:  # noqa: BLE001 - recorded with the night; the import's own outcome stands
            LOG.exception("The nightly WebAssign import could not move the gradebook's due dates")
            found["due_problem"] = "The gradebook's due dates could not be updated."
        else:
            if moved:
                found["due_dates"] = moved
        if self.content is not None:
            try:
                moved = self.content.follow_due_dates(term_id, FOLLOWED, f"{FOLLOWED}{night}:{uuid.uuid4().hex[:12]}", quizzes=True)
            except Exception:  # noqa: BLE001 - as above (Canvas signed out or failing, the copy saved meanwhile)
                LOG.exception("The nightly WebAssign import could not move the due dates of Faculty's Canvas copy")
                found["copy_due_problem"] = "The due dates of Faculty's Canvas copy could not be updated."
            else:
                if moved:
                    found["copy_due_dates"] = moved
            try:  # PUBLISHED IN CANVAS STANDS (docs/decisions/canvas.md): the copy follows, as due dates do
                published = self.content.follow_published(term_id, f"{PUBLISHED}{night}:{uuid.uuid4().hex[:12]}")
            except Exception:  # noqa: BLE001 - as above
                LOG.exception("Faculty's Canvas copy could not follow what Canvas has published")
                found["copy_published_problem"] = "Faculty's Canvas copy could not follow what Canvas has published."
            else:
                if published:
                    found["copy_published"] = published
        return found

    def follow_gradebook(self, term_id, night, links, kept):
        """Each column linked to a WebAssign assignment or a Canvas quiz (`kept`) takes Canvas's due date: the latest campus date among its linked courses
        (the sections' Canvas courses may differ; a column has one date). A column whose date was last set by anything but an
        import from Canvas or this step is the instructor's and kept (`due_origins`); a Canvas assignment without a due date
        changes nothing. One setup revision (request ID FOLLOWED…) for the night; returns how many columns moved."""
        from .service import _setup  # here: the service imports this module
        courses = {link["course_id"] for link in links.values()}
        dates = {canvas_import.assignment_key(course_id, a["id"]): canvas_import.campus_date(a.get("due_at"))
                 for course_id, data in kept.items() if course_id in courses for group in data["groups"] for a in group["assignments"]}
        dates = {key: date for key, date in dates.items() if date}
        if not dates:
            return 0
        gradebook = self.gradebook
        with gradebook.rosters.lock():
            context = gradebook._context(term_id)
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                wanted = {}
                for course_id, assignment_id, item_id in db.execute("""SELECT l.course_id,l.assignment_id,l.item_id FROM gradebook_canvas_links l
                        WHERE l.term_id=? AND l.rowid=(SELECT x.rowid FROM gradebook_canvas_links x WHERE x.term_id=l.term_id
                        AND x.course_id=l.course_id AND x.assignment_id=l.assignment_id ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1)""",
                                                                    (term_id,)):
                    date = dates.get(canvas_import.assignment_key(course_id, assignment_id))
                    if item_id and date:
                        wanted[item_id] = max(wanted.get(item_id, ""), date)
                current, meta = _setup(db, term_id)
                moving = {item["id"]: wanted[item["id"]] for item in current["items"]
                          if item["id"] in wanted and (item.get("due_date") or "") != wanted[item["id"]]}
                if not moving:
                    return 0
                imported = {row[0] for row in db.execute("""SELECT request_id FROM gradebook_requests WHERE term_id=? AND kind='import'
                    AND json_extract(summary_json,'$.source')='canvas'""", (term_id,))}
                origins = due_origins(db, term_id, set(moving))
                moving = {item_id: date for item_id, date in moving.items()
                          if origins.get(item_id) is None or origins[item_id].startswith(FOLLOWED) or origins[item_id] in imported}
                if not moving:
                    return 0
                setup = gradebook._normalize(db, context, {**current, "items": [
                    {**item, "due_date": moving.get(item["id"], item.get("due_date") or "")} for item in current["items"]]}, current)
                if common.encoded(setup) == common.encoded(current):
                    return 0
                gradebook._record_setup(db, term_id, meta["revision"], setup, f"{FOLLOWED}{night}:{uuid.uuid4().hex[:12]}",
                                        common.digest({"follow": sorted(moving.items())}), "workspace")
        LOG.info("Nightly WebAssign import of term %s: %d column due date(s) now follow Canvas", term_id, len(moving))
        return len(moving)
