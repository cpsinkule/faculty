"""Canvas modules and pages for a term's sections (canvas/README.md "Modules and pages"; the rules are `canvas/content.py`'s).

Owners: Faculty's copy is TERM-owned (`canvas_content_revisions`); what Faculty knows of each Canvas course's objects is append-only
per (term, Canvas course, object) (`canvas_content_links`); a push is the receipt of one approved plan (`canvas_content_pushes`).
A file added to a module item is kept by the term too (`canvas/files.py`), and each upload of it into a course is a receipt.
The Canvas courses are those the term's sections link (`canvas_course_links`, section-owned); a course several sections link is
one course here, and an object limited to some sections goes to a course when any of its sections is one of them.

Lock order as elsewhere: Canvas is read and written with NO lock held; Faculty's own reads and each write take the roster lifecycle
lock, then ONE `BEGIN IMMEDIATE`. A preview writes nothing anywhere. A push reads Canvas again, plans again and is refused (409)
unless the plan is the one the instructor approved; its writes then run in a thread of this process, one course after another,
each operation recorded as it lands. A push this process no longer runs (a restart) is `interrupted`: the next preview reads Canvas
as it is, so nothing is written twice (what a lost answer created is matched by its name).
"""
from __future__ import annotations

import copy
import json
import re
import threading
import uuid

from werkzeug.exceptions import Conflict, NotFound

from course_groups import navigation_id
from course_storage import timestamp
from rosters import connect
from seating import term_label

from . import column_assignments
from . import content as model
from . import web
from .service import _link

PARALLEL = 4  # courses read at once
RESTARTING = "Faculty is restarting. Push again in a minute; nothing was written to Canvas."


def _request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,120}", value):
        raise ValueError("Send a request ID (8 to 120 letters, digits, dots, colons, dashes or underscores).")
    return value


WINDOW = ("unlock_at", "lock_at")  # WebAssign's available dates, which it writes into Canvas itself (follow_due_dates)


def _moved(assignment, moves, sections, everyone):
    """The assignment with Canvas's due dates for the courses in `moves` ({course id: instant}; `sections` {course id: [section
    ids]}); every other section of the term (`everyone`) keeps its date. One date for every section is the default alone; else
    the old default stays while a section still has it (otherwise the most common date, the later on a tie), the rest per section."""
    dates = {s: assignment["section_due"].get(s, assignment["due_at"]) for s in everyone}
    for cid, due in moves.items():
        dates.update({s: due for s in sections[cid]})
    values = list(dates.values())
    if len(set(values)) <= 1:
        return {**assignment, "due_at": values[0] if values else assignment["due_at"], "section_due": {}}
    default = assignment["due_at"] if assignment["due_at"] in values else max(set(values), key=lambda v: (values.count(v), v or ""))
    return {**assignment, "due_at": default, "section_due": {s: d for s, d in sorted(dates.items()) if d and d != default}}


class CanvasContentService:
    def __init__(self, canvas):
        self.canvas = canvas  # CanvasService: the kept session, section links and the migrations
        self.rosters, self.path = canvas.rosters, canvas.path
        self._active, self._cancel = {}, set()
        self._claims = {}  # Canvas course ID: how many times a push claimed it or finished with it in this process (see `claim`)
        self._guard = threading.Lock()
        from .column_assignments import ColumnAssignments
        from .files import ContentFiles
        from .placement import Placement
        from .section_content import CanvasSectionContent
        self.placement = Placement(self)  # "Put on Canvas": a Faculty assignment or gradebook column into the copy
        self.columns = ColumnAssignments(self)  # COLUMN ASSIGNMENTS: every gradebook column's assignment in the copy
        self.section = CanvasSectionContent(self)  # one section's Canvas course edited directly
        self.files = ContentFiles(self)  # FILES: the files kept for module items; a push puts them in each course's Files

    # --- Faculty's records ---------------------------------------------------------------------------------------------------
    def _term(self, db, term_id):
        term = db.execute("SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL", (str(term_id),)).fetchone()
        if not term:
            raise NotFound("That course term was not found.")
        return term

    def group_term(self, group_id):
        """The term a course page's group ID names (the group is a navigation projection of one code and term)."""
        with connect(self.path) as db:
            for term in db.execute("SELECT * FROM course_terms WHERE deleted_at IS NULL"):
                if navigation_id({"id": term["id"], "code": term["code"], "term": term["term"], "term_other": term["term_other"],
                                  "year": term["year"]}) == group_id:
                    return term["id"]
        raise NotFound("That course and term were not found.")

    def group_place(self, group_id, section=None):
        """{term_id} of a course page's group, and with a section number that section's permanent ID (`section_id`)."""
        term_id = self.group_term(group_id)
        if section in (None, ""):
            return {"term_id": term_id}
        with connect(self.path) as db:
            row = db.execute("SELECT id FROM course_sections WHERE term_id=? AND number=?", (term_id, str(section))).fetchone()
        if not row:
            raise NotFound("That section is not part of this course term.")
        return {"term_id": term_id, "section_id": row["id"]}

    def _targets(self, db, term_id):
        """[{course_id, name, sections: [{id, number}]}] of the Canvas courses the term's sections link, and the unlinked sections."""
        courses, unlinked = {}, []
        for section in db.execute("SELECT id,number FROM course_sections WHERE term_id=? ORDER BY number", (term_id,)):
            link = _link(db, section["id"])
            if not link:
                unlinked.append({"id": section["id"], "number": section["number"]})
                continue
            entry = courses.setdefault(link["course_id"], {"course_id": link["course_id"], "name": link["name"], "sections": []})
            entry["sections"].append({"id": section["id"], "number": section["number"]})
        return sorted(courses.values(), key=lambda c: c["sections"][0]["number"]), unlinked

    @staticmethod
    def _latest(db, term_id):
        row = db.execute("SELECT * FROM canvas_content_revisions WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone()
        return (json.loads(row["content_json"]), row["revision"], row["created_at"]) if row else (None, None, None)

    @staticmethod
    def _links(db, term_id):
        """{course id: {(kind, plan key): latest link row}}."""
        found = {}
        for row in db.execute("""SELECT * FROM canvas_content_links l WHERE term_id=? AND rowid=(SELECT x.rowid FROM canvas_content_links x
                WHERE x.term_id=l.term_id AND x.course_id=l.course_id AND x.kind=l.kind AND x.plan_key=l.plan_key
                ORDER BY x.created_at DESC, x.rowid DESC LIMIT 1)""", (term_id,)):
            found.setdefault(row["course_id"], {})[(row["kind"], row["plan_key"])] = dict(row)
        return found

    def _link_row(self, db, term_id, course_id, kind, key, canvas_id, source, snap, push_id=None, actor="workspace"):
        db.execute("INSERT INTO canvas_content_links VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), term_id, str(course_id), kind, key, str(canvas_id or ""), source, json.dumps(snap or {}, sort_keys=True),
                    push_id, actor, timestamp()))

    def _sections(self, db, term_id):
        return {row["id"] for row in db.execute("SELECT id FROM course_sections WHERE term_id=?", (term_id,))}

    def _push_row(self, db, push_id):
        row = db.execute("SELECT * FROM canvas_content_pushes WHERE id=?", (push_id,)).fetchone()
        return self._push_view(row) if row else None

    def _push_view(self, row):
        status = row["status"]
        if status == "running" and self.canvas.owner and row["id"] not in self._active:  # CanvasService.owner
            with connect(self.path) as db:
                db.execute("UPDATE canvas_content_pushes SET status='interrupted', finished_at=? WHERE id=? AND status='running'", (timestamp(), row["id"]))
            status = "interrupted"
        return {"id": row["id"], "revision": row["revision"], "status": status, "progress": json.loads(row["progress_json"] or "{}"),
                "created_at": row["created_at"], "finished_at": row["finished_at"], "actor": row["actor"]}

    def view(self, term_id):
        """The term's copy, its Canvas courses and the latest push, from Faculty's records alone (Canvas is not contacted)."""
        with self.rosters.lock(), connect(self.path) as db:
            term = self._term(db, term_id)
            content, revision, saved_at = self._latest(db, term["id"])
            targets, unlinked = self._targets(db, term["id"])
            push = db.execute("SELECT * FROM canvas_content_pushes WHERE term_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (term["id"],)).fetchone()
            sections = [dict(r) for r in db.execute("SELECT id,number FROM course_sections WHERE term_id=? ORDER BY number", (term["id"],))]
            pushed = self._pushed_courses(db, term["id"])
            # COLUMN ASSIGNMENTS: the gradebook's columns whose assignment Canvas holds and the copy does not keep, and those without
            # a category (additive; read-only on the Assignments tab).
            columns = column_assignments.overview(db, term["id"], content) if content is not None else {"canvas_only": [], "ungrouped": []}
            files = self.files.shown(db, term["id"])
        return {"term": {"id": term["id"], "code": term["code"], "term_label": term_label(term)}, "group": self._group(term), "columns": columns,
                "files": files,
                "content": content, "revision": revision, "saved_at": saved_at, "courses": targets, "unlinked": unlinked, "sections": sections,
                "push": self._push_view(push) if push else None, "canvas": self.canvas.status(),
                "pushed_courses": [t["course_id"] for t in targets if t["course_id"] in pushed]}

    def outline(self, term_id):
        """The copy in outline, from Faculty's records alone: each module with its ID and its items' titles (no page bodies), the
        files the term keeps and the Canvas courses. What the assistant reads to place a file (canvas FILES, FROM THE ASSISTANT)."""
        with self.rosters.lock(), connect(self.path) as db:
            term = self._term(db, term_id)
            content, revision, saved_at = self._latest(db, term["id"])
            targets, _ = self._targets(db, term["id"])
            files = self.files.shown(db, term["id"])
            pushed = self._pushed_courses(db, term["id"])
        modules = [{"id": m["id"], "name": m["name"], "published": m["published"], "unlock_at": m["unlock_at"], "sections": m["sections"],
                    "items": [{"type": i["type"], "title": i["title"], "published": i["published"]} for i in m["items"]]}
                   for m in (content or {}).get("modules", [])]
        return {"term": {"id": term["id"], "code": term["code"], "term_label": term_label(term)}, "revision": revision, "saved_at": saved_at,
                "modules": modules, "files": files, "courses": targets, "pushed_courses": [t["course_id"] for t in targets if t["course_id"] in pushed]}

    @staticmethod
    def _group(term):
        """The course page's group ID of a term (a navigation projection, never an owner)."""
        return navigation_id({"id": term["id"], "code": term["code"], "term": term["term"], "term_other": term["term_other"], "year": term["year"]})

    @staticmethod
    def _pushed_courses(db, term_id):
        """The Canvas courses a push of this term's copy has written to (what makes a first push go to one section first)."""
        return {row[0] for row in db.execute("SELECT DISTINCT course_id FROM canvas_content_links WHERE term_id=? AND source='pushed'", (term_id,))}

    def overview(self):
        """Every current term's Canvas state from Faculty's records alone (Canvas is not contacted): its sections and their linked
        courses, Faculty's copy, what each course has received, and the one next step (the sidebar's Canvas page)."""
        terms = []
        with self.rosters.lock(), connect(self.path) as db:
            rows = db.execute("SELECT * FROM course_terms WHERE deleted_at IS NULL AND is_current=1 ORDER BY code, year DESC, term").fetchall()
            for term in rows:
                targets, unlinked = self._targets(db, term["id"])
                content, revision, saved_at = self._latest(db, term["id"])
                pushed = self._pushed_courses(db, term["id"])
                push = db.execute("SELECT * FROM canvas_content_pushes WHERE term_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (term["id"],)).fetchone()
                sections = []
                for section in db.execute("SELECT id,number FROM course_sections WHERE term_id=? ORDER BY number", (term["id"],)):
                    link = _link(db, section["id"])
                    sections.append({"id": section["id"], "number": section["number"],
                                     "course": {"course_id": link["course_id"], "name": link["name"]} if link else None})
                courses = [{**t, "pushed": t["course_id"] in pushed} for t in targets]
                if not targets:
                    step = "link_sections"
                elif content is None:
                    step = "start_copy"
                elif not pushed & {t["course_id"] for t in targets}:
                    step = "push_first"
                elif any(not c["pushed"] for c in courses):
                    step = "push_rest"
                else:
                    step = "up_to_date"
                terms.append({"term": {"id": term["id"], "code": term["code"], "term_label": term_label(term)},
                              "group": self._group(term),
                              "sections": sections, "courses": courses, "unlinked": unlinked,
                              "copy": {"revision": revision, "saved_at": saved_at, "source_course": content.get("source_course", "")} if content else None,
                              "push": self._push_view(push) if push else None, "next": step})
        return {"terms": terms, "canvas": self.canvas.status()}

    # --- reading Canvas ------------------------------------------------------------------------------------------------------
    def _read(self, course_ids):
        """{course id: content.Course}, the courses read at once, each with its own use of the kept session. No lock is held."""
        def one(course_id):
            with self.canvas.client() as client:
                files, file_info = client.course_files(course_id)
                return model.Course({"course_id": course_id, "modules": client.modules(course_id), "pages": client.pages(course_id),
                                     "assignments": client.assignments(course_id), "groups": client.groups(course_id),
                                     "named": {kind: client.named(course_id, kind) for kind in ("Quiz", "Discussion")}, "files": files,
                                     "file_info": file_info})
        return self.canvas.read_each(course_ids, one, PARALLEL)

    # --- starting and saving Faculty's copy ----------------------------------------------------------------------------------
    def start(self, term_id, data, actor="workspace"):
        """Faculty's copy made from one of the term's Canvas courses. Allowed while the term has no copy, or with `base_revision`
        naming the latest (starting again). Every object of that course is recorded as paired with its part of the copy."""
        data = data if isinstance(data, dict) else {}
        request_id = _request_id(data.get("request_id"))
        course_id = str(data.get("course_id") or "")
        with connect(self.path) as db:
            term = self._term(db, term_id)
            targets, _ = self._targets(db, term["id"])
            done = db.execute("SELECT revision FROM canvas_content_revisions WHERE term_id=? AND request_id=?", (term["id"], request_id)).fetchone()
        if done:
            return self.view(term["id"])
        if course_id not in {t["course_id"] for t in targets}:
            raise ValueError("Choose the Canvas course of one of this term's sections.")
        course = self._read([course_id])[course_id]
        content, pairs, skipped = model.from_canvas(course, timestamp())
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            _, latest, _ = self._latest(db, term["id"])
            if latest != data.get("base_revision"):
                raise Conflict("Faculty's copy was saved from another window since you opened it. Reload to see it.")
            self._record(db, term["id"], latest, content, request_id, {"start": course_id}, actor)
            plan_items = {i["id"]: i for m in content["modules"] for i in m["items"]}
            plan_pages = {p["id"]: p for p in content["pages"]}
            plan_modules = {m["id"]: m for m in content["modules"]}
            plan_assignments = {a["id"]: a for a in content["assignments"]}
            for kind, key, canvas_id in pairs:
                if kind == "assignment":
                    sent = model.assignment_plan_state(plan_assignments[key])
                    now = model.assignment_canvas_state(course.assignment_by_id[canvas_id], course.groups)
                elif kind == "page":
                    sent, now = model.page_plan_state(plan_pages[key]), model.page_canvas_state(course.page_by_id[canvas_id])
                elif kind == "module":
                    module = plan_modules[key]
                    sent = model.module_plan_state(module)
                    now = model.module_state(course.module_by_id[canvas_id])
                else:
                    sent, now = model.item_plan_state(plan_items[key]), model.item_canvas_state(course.item_by_id[canvas_id][1])
                self._link_row(db, term["id"], course_id, kind, key, canvas_id, "matched", model.snapshot(sent, now), actor=actor)
        # The other courses: what is already alike there is recorded too (read now; a course that cannot be read is left for its
        # first preview, where anything that differs is asked about).
        try:
            found = self._plan(term["id"], {})
        except Exception:  # noqa: BLE001 - the copy is saved; matching the others is a convenience
            found = None
        if found and found["revision"] == (latest or 0) + 1:
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                self._record_alike(db, term["id"], found, None, actor)
        column_assignments.adopt_quietly(self, term["id"], actor)  # COLUMN ASSIGNMENTS: the gradebook's own columns join the new copy
        return {**self.view(term["id"]), "skipped": skipped}

    def _record(self, db, term_id, latest, content, request_id, request, actor):
        body = json.dumps(content, sort_keys=True, ensure_ascii=False)
        db.execute("INSERT INTO canvas_content_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), term_id, (latest or 0) + 1, body, model.digest(content), request_id, model.digest(request), actor, timestamp()))

    # SCHEDULING "CANVAS OFFICE HOURS" (stage 1f): the copy's Office Hours page drafted from the Term schedule.
    def office_hours(self, term_id):
        """{available, page, differs, current, draft, lines, revision}: the copy's Office Hours page beside its draft from the term's
        saved Term schedule (Faculty's records alone; Canvas is not contacted)."""
        from scheduling.service import ScheduleService
        from . import office_hours as oh
        with connect(self.path) as db:
            term = self._term(db, term_id)
            content, revision, _ = self._latest(db, term["id"])
            schedule = ScheduleService.revision(db, term["id"])
        found = oh.find(content) if content else None
        if not found or not schedule["revision"]:
            return {"available": False, "page": bool(found), "schedule": bool(schedule["revision"]), "revision": revision}
        draft = oh.draft(found[1].get("body"), schedule["entries"])
        return {"available": True, "page": True, "schedule": True, "revision": revision, "title": found[1].get("title"),
                "differs": oh.text(found[1].get("body")) != oh.text(draft), "draft": draft,
                "office": oh.lines(schedule["entries"], "office_hours"), "meeting": oh.lines(schedule["entries"], "meeting_hours")}

    def put_office_hours(self, term_id, data, actor="workspace"):
        """The instructor's "Put in Faculty's copy": a revision of the copy with the Office Hours page's draft, against the copy's
        revision they saw (`base_revision`) and `confirmation: "put"`. Canvas itself changes only on the ordinary Push."""
        from . import office_hours as oh
        data = data if isinstance(data, dict) else {}
        if data.get("confirmation") != "put":
            raise ValueError("Confirm with put.")
        proposal = self.office_hours(term_id)
        if not proposal["available"]:
            raise Conflict("There is no Office Hours page in Faculty's copy, or no saved Term schedule, to draft it from.")
        with connect(self.path) as db:
            content, revision, _ = self._latest(db, self._term(db, term_id)["id"])
        index, page = oh.find(content)
        pages = [dict(p) for p in content["pages"]]
        pages[index] = {**page, "body": proposal["draft"]}
        return self.save(term_id, {"request_id": data.get("request_id"), "base_revision": data.get("base_revision"),
                                   "content": {**content, "pages": pages}}, actor)

    def save(self, term_id, data, actor="workspace"):
        """A new revision of Faculty's copy. `base_revision` names the revision it was made on (409 when another came since); the
        same request ID and content returns what it did; an identical copy records nothing."""
        data = data if isinstance(data, dict) else {}
        request_id = _request_id(data.get("request_id"))
        request_sha = model.digest({"base_revision": data.get("base_revision"), "content": data.get("content")})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            term = self._term(db, term_id)
            old = db.execute("SELECT request_sha256 FROM canvas_content_revisions WHERE term_id=? AND request_id=?", (term["id"], request_id)).fetchone()
            if old:
                if old[0] != request_sha:
                    raise Conflict("This request ID was used for a different save.")
            else:
                current, latest, _ = self._latest(db, term["id"])
                if current is None:
                    raise Conflict("Start Faculty's copy from one of the term's Canvas courses first.")
                if latest != data.get("base_revision"):
                    raise Conflict("Faculty's copy was saved from another window since you opened it. Reload to see it, then make your changes again.")
                content = model.normalize(data.get("content"), current, sections=self._sections(db, term["id"]),
                                          files={key: meta["name"] for key, meta in self.files.kept(db, term["id"]).items()})
                if model.digest(content) != model.digest(current):
                    body = json.dumps(content, sort_keys=True, ensure_ascii=False)
                    db.execute("INSERT INTO canvas_content_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                               (str(uuid.uuid4()), term["id"], latest + 1, body, model.digest(content), request_id, request_sha, actor, timestamp()))
        return self.view(term["id"])

    # --- pairing by hand ---------------------------------------------------------------------------------------------------------
    def pair(self, term_id, data, actor="workspace"):
        """The instructor's word on one object in one course: `canvas_id` is the same thing as Faculty's `plan_key` there, or ''
        (it is new there: never matched by name). Recorded at once; the next preview uses it."""
        data = data if isinstance(data, dict) else {}
        kind, key, course_id, canvas_id = data.get("kind"), data.get("plan_key"), str(data.get("course_id") or ""), str(data.get("canvas_id") or "")
        if kind not in model.KINDS or not isinstance(key, str) or not re.fullmatch(r"\d{1,20}", course_id) or not re.fullmatch(r"\d{0,20}", canvas_id):
            raise ValueError("Name the course, the kind of object, Faculty's object and the Canvas one.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            term = self._term(db, term_id)
            content, _, _ = self._latest(db, term["id"])
            targets, _ = self._targets(db, term["id"])
            keys = {"module": {m["id"] for m in (content or {}).get("modules", [])},
                    "assignment": {a["id"] for a in (content or {}).get("assignments", [])},
                    "item": {i["id"] for m in (content or {}).get("modules", []) for i in m["items"]},
                    "page": {p["id"] for p in (content or {}).get("pages", [])}}
            if key not in keys.get(kind, ()):
                raise ValueError("That is not part of Faculty's copy.")
            if course_id not in {t["course_id"] for t in targets}:
                raise ValueError("That Canvas course is not one of this term's.")
            self._link_row(db, term["id"], course_id, kind, key, canvas_id, "paired" if canvas_id else "unpaired", {}, actor=actor)
        return {"ok": True}

    # --- WebAssign's due dates (the nightly WebAssign import, gradebook/webassign_nightly.py) -----------------------------------
    def follow_due_dates(self, term_id, prefix, request_id, quizzes=False):
        """WEBASSIGN DUE DATES (docs/decisions/canvas.md; requirement, 2026-10-05): WebAssign sends a changed due date to Canvas
        itself, so a WebAssign assignment of the copy takes Canvas's due date for a course when (1) Faculty has synced it with
        that course (its link has a snapshot), (2) Canvas's date there differs and is not empty, and (3) no revision since that
        sync other than this step's own (request ID starting with `prefix`) changed the copy's date for that course's sections:
        a date the instructor changed in Faculty and has not pushed yet is theirs, and the push asks about it as before. Sections
        sharing one date keep it as the default; the rest become per-section dates. One revision; a course whose Canvas
        assignment changed only its due date since the sync is recorded as alike again, so the next push finds nothing to do.
        Reads Canvas's assignments (no lock held); writes nothing to Canvas. Returns how many assignments moved. `quizzes`: a Canvas
        quiz of the copy (a New Quiz, an outside tool) follows Canvas's due date the same way (requirement, 2026-10-08). AVAILABILITY (requirement, 2026-10-17): WebAssign writes its available-from and until dates
        into Canvas too, so a WebAssign assignment (not a quiz) also takes Canvas's `unlock_at` and `lock_at` under the same
        conditions, when every synced course's Canvas holds the same one (the copy has one for all sections)."""
        with self.rosters.lock(), connect(self.path) as db:
            term = self._term(db, term_id)
            content, revision, _ = self._latest(db, term["id"])
            if content is None:
                return 0
            targets, _ = self._targets(db, term["id"])
            links = self._links(db, term["id"])
        tools = [a for a in content["assignments"] if a["external"]]

        def synced(cid, a):
            """The course's link of the assignment when Faculty has synced it there (pushed or found alike: a snapshot)."""
            link = links.get(cid, {}).get(("assignment", a["id"]))
            if link and link["canvas_id"] and link["source"] in ("pushed", "matched") and json.loads(link["snapshot_json"] or "{}").get("sent"):
                return link
            return None

        course_ids = [t["course_id"] for t in targets if any(synced(t["course_id"], a) for a in tools)]
        if not course_ids:
            return 0
        read = self._read_assignments(course_ids)
        sections = {t["course_id"]: [s["id"] for s in t["sections"]] for t in targets}
        moves, opens = {}, {}
        for a in tools:
            seen = {}  # WebAssign's available dates in each synced course's Canvas, and the earliest sync
            for cid in course_ids:
                link = synced(cid, a)
                row = read[cid][0].get(link["canvas_id"]) if link else None
                if not row or not (web.graded_by_webassign(row.get("tool", "")) or (quizzes and row.get("quiz"))):
                    continue
                if web.graded_by_webassign(row.get("tool", "")):
                    for field in WINDOW:
                        seen.setdefault(field, []).append(model.instant(row[field]))
                    seen["since"] = min(seen.get("since", link["created_at"]), link["created_at"])
                canvas_due, (mine, problem) = model.instant(row["due_at"]), model.due_for(a, sections[cid])
                if problem or not canvas_due or canvas_due == mine:
                    continue
                if self._date_changed_since(term["id"], a["id"], sections[cid], link["created_at"], prefix):
                    continue
                moves.setdefault(a["id"], {})[cid] = canvas_due
            for field in WINDOW:
                values = set(seen.get(field, []))
                if len(values) != 1 or None in values or values == {model.instant(a[field])}:
                    continue  # nothing synced, the courses differ (the push asks), Canvas has none, or nothing moved
                if self._date_changed_since(term["id"], a["id"], [], seen["since"], prefix, field):
                    continue
                opens.setdefault(a["id"], {})[field] = values.pop()
        if not moves and not opens:
            return 0
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current, latest, _ = self._latest(db, term["id"])
            if latest != revision:
                return 0  # the copy was saved while Canvas was read: the next night follows Canvas's due dates
            everyone = sorted(self._sections(db, term["id"]))
            old = {a["id"]: a for a in current["assignments"]}
            new = {aid: _moved(old[aid], moves[aid], sections, everyone) if aid in moves else old[aid] for aid in {*moves, *opens}}
            new = {aid: {**a, **opens.get(aid, {})} for aid, a in new.items()}
            self._record(db, term["id"], latest, {**current, "assignments": [new.get(a["id"], a) for a in current["assignments"]]},
                         request_id, {"follow": moves, **({"available": opens} if opens else {})}, "workspace")
            # Recorded as alike again where only the due date changed in Canvas since the sync and the copy had not changed: the
            # next push then neither asks about it nor counts it as Faculty's edit.
            now_links = self._links(db, term["id"])
            for aid in new:
                for cid in course_ids:
                    link, before = synced(cid, old[aid]), now_links.get(cid, {}).get(("assignment", aid))
                    row = read[cid][0].get(link["canvas_id"]) if link else None
                    if not row or not before or before["id"] != link["id"]:
                        continue
                    snap = json.loads(link["snapshot_json"])
                    state = model.assignment_canvas_state(row, read[cid][1])
                    if snap["sent"] != model.digest(model.assignment_plan_state(old[aid])) or \
                            snap["canvas"] != model.digest({**state, "due_at": model.due_for(old[aid], sections[cid])[0],
                                                            **{f: model.instant(old[aid][f]) for f in opens.get(aid, {})}}):
                        continue
                    self._link_row(db, term["id"], cid, "assignment", aid, link["canvas_id"], "matched",
                                   model.snapshot(model.assignment_plan_state(new[aid]), state))
        return len({*moves, *opens})

    def follow_published(self, term_id, request_id):
        """PUBLISHED IN CANVAS STANDS (docs/decisions/canvas.md; requirement, 2026-11-03, of Quiz 3, put on Canvas unpublished on
        Oct 2 and published there since, asked about in every section): an assignment the copy holds unpublished and last pushed as
        it stands, with Canvas publishing it in EVERY course it goes to, is published in the copy too, one revision for the term, so
        no later push can take it back. Only some courses published: the copy stays, since publishing it would publish the rest; the
        plan still does not ask about those. Reads Canvas's assignments (no lock held); writes nothing to Canvas. Each link in step
        is recorded as published, as PUBLISH FROM SYNC does. Returns how many assignments followed."""
        with self.rosters.lock(), connect(self.path) as db:
            term = self._term(db, term_id)
            content, revision, _ = self._latest(db, term["id"])
            if content is None:
                return 0
            targets, _ = self._targets(db, term["id"])
            links = self._links(db, term["id"])

        def pushed_as_it_stands(cid, a):
            """The course's link of the assignment, when the copy has not changed it since it was last pushed or found alike there."""
            link = links.get(cid, {}).get(("assignment", a["id"]))
            if link and link["canvas_id"] and link["source"] in ("pushed", "matched") \
                    and json.loads(link["snapshot_json"] or "{}").get("sent") == model.digest(model.assignment_plan_state(a)):
                return link
            return None

        waiting = {}  # {assignment id: {course id: link}}
        for a in content["assignments"]:
            if a["published"]:
                continue
            going = [t["course_id"] for t in targets if model.applies(a, [s["id"] for s in t["sections"]])]
            held = {cid: pushed_as_it_stands(cid, a) for cid in going}
            if held and all(held.values()):
                waiting[a["id"]] = held
        if not waiting:
            return 0
        read = self._read_assignments(sorted({cid for held in waiting.values() for cid in held}))
        waiting = {aid: held for aid, held in waiting.items()
                   if all((read[cid][0].get(link["canvas_id"]) or {}).get("published") for cid, link in held.items())}
        if not waiting:
            return 0
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current, latest, _ = self._latest(db, term["id"])
            if latest != revision:
                return 0  # the copy was saved while Canvas was read: the next morning looks again
            draft = copy.deepcopy(current)
            for a in draft["assignments"]:
                if a["id"] in waiting:
                    a["published"] = True
            new = model.normalize(draft, current, sections=self._sections(db, term["id"]))
            self._record(db, term["id"], latest, new, request_id, {"published_in_canvas": sorted(waiting)}, "workspace")
            old, now = {a["id"]: a for a in current["assignments"]}, {a["id"]: a for a in new["assignments"]}
            for aid, held in waiting.items():
                for cid, link in held.items():
                    row = read[cid][0][link["canvas_id"]]
                    state = model.assignment_canvas_state(row, read[cid][1])
                    if json.loads(link["snapshot_json"])["canvas"] == model.digest({**state, "published": old[aid]["published"]}):
                        self._link_row(db, term["id"], cid, "assignment", aid, link["canvas_id"], "matched",
                                       model.snapshot(model.assignment_plan_state(now[aid]), state))
        return len(waiting)

    def _date_changed_since(self, term_id, assignment_id, sections, since, prefix, field=None):
        """Whether a revision after `since` (a link's time) that is not the nightly step's own changed the assignment's due date
        for `sections` (with `field`, its `unlock_at` or `lock_at` instead). A copy without the assignment at that time counts as
        changed (nothing to compare with)."""
        with connect(self.path) as db:
            rows = db.execute("""SELECT created_at,request_id,json_extract(content_json,'$.assignments') FROM canvas_content_revisions
                WHERE term_id=? AND revision>=COALESCE((SELECT MAX(revision) FROM canvas_content_revisions WHERE term_id=? AND created_at<=?),1)
                ORDER BY revision""", (term_id, term_id, since)).fetchall()
        before = None
        for position, (at, request_id, assignments) in enumerate(rows):
            found = next((a for a in json.loads(assignments or "[]") if a["id"] == assignment_id), None)
            if found is None:
                return True
            due = model.instant(found.get(field)) if field else model.due_for(found, sections)
            if position and due != before and not request_id.startswith(prefix):
                return True
            before = due
        return not rows

    def _read_assignments(self, course_ids):
        """{course id: ({assignment id: assignment row}, {group id: name})}, read at once with the kept session, no lock held."""
        def one(course_id):
            with self.canvas.client() as client:
                return {a["id"]: a for a in client.assignments(course_id)}, client.groups(course_id)
        return self.canvas.read_each(course_ids, one, PARALLEL)

    # --- preview and push ------------------------------------------------------------------------------------------------------
    def _plan(self, term_id, choices, courses=None):
        """Plans a push to the term's Canvas courses, or to `courses` alone (a first push to one section, checked in Canvas before
        the rest): the fingerprint then names them."""
        with self.rosters.lock(), connect(self.path) as db:
            term = self._term(db, term_id)
            content, revision, _ = self._latest(db, term["id"])
            targets, unlinked = self._targets(db, term["id"])
            links = self._links(db, term["id"])
            kept, receipts = self.files.kept(db, term["id"]), self.files.receipts(db, [t["course_id"] for t in targets])
        if content is None:
            raise Conflict("Start Faculty's copy from one of the term's Canvas courses first.")
        if not targets:
            raise Conflict("Link the sections' Canvas courses first (on each section tab).")
        only = self._course_filter(courses, targets)
        choices = self._choices(choices, targets)
        # Every course is read and matched even for a push to some: page links to the others are rewritten through them.
        courses = self._read([t["course_id"] for t in targets])
        sections = {t["course_id"]: [s["id"] for s in t["sections"]] for t in targets}
        matches = {cid: model.match(content, course, links.get(cid, {}), sections[cid]) for cid, course in courses.items()}
        plans = model.plan(content, courses, links, sections, matches, choices, kept, receipts)
        if only is not None:
            targets = [t for t in targets if t["course_id"] in only]
            plans = {cid: plan for cid, plan in plans.items() if cid in only}
            choices = {cid: chosen for cid, chosen in choices.items() if cid in only}
        return {"term": term, "content": content, "revision": revision, "targets": targets, "unlinked": unlinked, "links": links,
                "courses": courses, "matches": matches, "plans": plans, "choices": choices, "only": only,
                "fingerprint": model.fingerprint(revision if only is None else {"revision": revision, "courses": only}, plans, choices)}

    @staticmethod
    def _course_filter(raw, targets):
        """None (every course), or the sorted Canvas course IDs a push is limited to: each one of the term's."""
        if raw is None:
            return None
        if not isinstance(raw, list) or not raw or any(not isinstance(c, str) for c in raw):
            raise ValueError("Choose at least one Canvas course to push to.")
        known = {t["course_id"] for t in targets}
        if any(c not in known for c in raw):
            raise ValueError("That Canvas course is not one of this term's.")
        return sorted(set(raw))

    @staticmethod
    def _choices(raw, targets):
        raw = raw if isinstance(raw, dict) else {}
        clean = {}
        for target in targets:
            entry = raw.get(target["course_id"]) or {}
            if not isinstance(entry, dict):
                raise ValueError("Send each course's choices as an object.")
            replace = entry.get("replace") or []
            remove = entry.get("remove")
            if replace != "all" and (not isinstance(replace, list) or any(not isinstance(k, str) for k in replace)):
                raise ValueError("Name what to replace as a list, or all.")
            if remove is not None and (not isinstance(remove, list) or any(not isinstance(k, str) for k in remove)):
                raise ValueError("Name what to remove as a list.")
            clean[target["course_id"]] = {"replace": replace if replace == "all" else sorted(set(replace)),
                                          "remove": None if remove is None else sorted(set(remove))}
        return clean

    def preview(self, term_id, data):
        """What a push would do in each course, and its fingerprint. Reads Canvas; writes nothing anywhere."""
        data = data if isinstance(data, dict) else {}
        found = self._plan(term_id, data.get("choices"), data.get("courses"))
        return self._shown(found)

    def _shown(self, found):
        courses = []
        for target in found["targets"]:
            plan = found["plans"][target["course_id"]]
            courses.append({**target, **{k: plan[k] for k in ("counts", "attention", "blocked", "extra", "unclaimed")},
                            "new": [e for e in plan["entries"] if e["verdict"] == "new"],
                            "ops": [{k: v for k, v in op.items() if k in ("op", "label", "fields", "moves")} for op in plan["ops"]],
                            "remove": sorted(e["key"] for e in plan["extra"] if e["remove"]),
                            "matched_by_name": [e for e in plan["entries"] if e["how"] == "name"]})
        return {"revision": found["revision"], "fingerprint": found["fingerprint"], "courses": courses, "unlinked": found["unlinked"],
                "choices": found["choices"], "only": found["only"]}

    def push(self, term_id, data, actor="workspace"):
        """Runs the approved plan: `fingerprint` from the preview, `choices` as previewed, `request_id` and `confirmation: "push"`."""
        data = data if isinstance(data, dict) else {}
        request_id = _request_id(data.get("request_id"))
        if data.get("confirmation") != "push":
            raise ValueError("Confirm the push.")
        request = {"fingerprint": data.get("fingerprint"), "choices": data.get("choices")}
        if data.get("courses") is not None:
            request["courses"] = data.get("courses")
        request_sha = model.digest(request)
        with connect(self.path) as db:
            term = self._term(db, term_id)
            old = db.execute("SELECT * FROM canvas_content_pushes WHERE term_id=? AND request_id=?", (term["id"], request_id)).fetchone()
            if old:
                if old["request_sha256"] != request_sha:
                    raise Conflict("This request ID was used for a different push.")
                return self._push_view(old)
            running = db.execute("SELECT id FROM canvas_content_pushes WHERE term_id=? AND status='running'", (term["id"],)).fetchall()
        if any(row["id"] in self._active for row in running):
            raise Conflict("A push to Canvas is already running for this term.")
        stamp = self.stamp()  # before the plan reads Canvas
        found = self._plan(term["id"], data.get("choices"), data.get("courses"))
        if found["fingerprint"] != data.get("fingerprint"):
            raise Conflict("Canvas or Faculty's copy changed since the preview. Look at the preview again.")
        push_id = str(uuid.uuid4())
        progress = {"courses": {cid: {"total": len(p["ops"]), "done": 0, "failed": 0, "errors": []} for cid, p in found["plans"].items()},
                    "current": ""}
        self.claim(push_id, found["plans"], stamp)
        try:
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM canvas_content_pushes WHERE term_id=? AND request_id=?", (term["id"], request_id)).fetchone():
                    raise Conflict("This push was just started from another window.")
                plan_json = json.dumps({cid: p["ops"] for cid, p in found["plans"].items()}, sort_keys=True)
                db.execute("INSERT INTO canvas_content_pushes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (push_id, term["id"], found["revision"], request_id, request_sha, found["fingerprint"], plan_json, "running",
                            json.dumps(progress), actor, timestamp(), None))
                self._record_alike(db, term["id"], found, push_id, actor)
            thread = threading.Thread(target=self._run, args=(push_id, term["id"], found, progress, actor), name=f"canvas-push-{push_id[:8]}", daemon=True)
            thread.start()
        except BaseException:
            self.release(push_id)
            raise
        with connect(self.path) as db:
            return self._push_row(db, push_id)

    def stamp(self):
        """How many times a push has claimed or finished with each Canvas course: taken before a plan reads Canvas, for `claim`."""
        with self._guard:
            return dict(self._claims)

    def claim(self, push_id, course_ids, stamp):
        """Takes these Canvas courses for one push (of a term's copy, or a section's direct edit), checked and taken in one step so
        that two pushes approved together cannot both pass (2026-10-01 audit: both made the same page). 409 while another push is
        writing to one of them, or when another one claimed or finished with it since `stamp`: this plan may have read Canvas
        before that push's writes, and would make them again."""
        course_ids = set(course_ids)
        with self._guard:
            if self._active and course_ids & set().union(*self._active.values()):
                raise Conflict("Faculty is pushing to that Canvas course right now. Wait for it to finish, then look at the preview again.")
            if any(self._claims.get(cid, 0) != stamp.get(cid, 0) for cid in course_ids):
                raise Conflict("Another push wrote to that Canvas course while this one was being checked. Look at the preview again.")
            # A restart waits for the push (canvas/stopping.py); none starts once Faculty is stopping.
            self.canvas.writes.begin(f"push:{push_id}", "a push to Canvas", RESTARTING)
            for cid in course_ids:
                self._claims[cid] = self._claims.get(cid, 0) + 1
            self._active[push_id] = course_ids

    def release(self, push_id):
        """A claimed push that never started (its record could not be written)."""
        with self._guard:
            self._active.pop(push_id, None)
        self.canvas.writes.end(f"push:{push_id}")

    def _record_alike(self, db, term_id, found, push_id, actor):
        """Objects already alike in Canvas and in Faculty's copy that Faculty had no record of: recorded now, so that a later edit on
        either side is told apart (Faculty's edit is then an update, one made in Canvas is asked about)."""
        for cid, plan in found["plans"].items():
            for entry in plan["entries"]:
                if entry["verdict"] == "same" and entry["canvas_id"]:
                    link = found["links"].get(cid, {}).get((entry["kind"], entry["key"].split(":", 1)[1]))
                    if not link or link["canvas_id"] != entry["canvas_id"] or not json.loads(link["snapshot_json"] or "{}"):
                        snap = self._same_snapshot(found, cid, entry)
                        self._link_row(db, term_id, cid, entry["kind"], entry["key"].split(":", 1)[1], entry["canvas_id"], "matched", snap, push_id, actor)

    def _same_snapshot(self, found, cid, entry):
        kind, key = entry["kind"], entry["key"].split(":", 1)[1]
        course, content = found["courses"][cid], found["content"]
        if kind == "assignment":
            assignment = next(a for a in content["assignments"] if a["id"] == key)
            return model.snapshot(model.assignment_plan_state(assignment),
                                  model.assignment_canvas_state(course.assignment_by_id[entry["canvas_id"]], course.groups))
        if kind == "page":
            page = next(p for p in content["pages"] if p["id"] == key)
            return model.snapshot(model.page_plan_state(page), model.page_canvas_state(course.page_by_id[entry["canvas_id"]]))
        if kind == "module":
            module = next(m for m in content["modules"] if m["id"] == key)
            return model.snapshot(model.module_plan_state(module), model.module_state(course.module_by_id[entry["canvas_id"]]))
        item = next(i for m in content["modules"] for i in m["items"] if i["id"] == key)
        return model.snapshot(model.item_plan_state(item), model.item_canvas_state(course.item_by_id[entry["canvas_id"]][1]))

    def cancel(self, term_id, push_id):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM canvas_content_pushes WHERE id=? AND term_id=?", (push_id, str(term_id))).fetchone()
        if not row:
            raise NotFound("That push was not found.")
        self._cancel.add(push_id)
        with connect(self.path) as db:
            return self._push_row(db, push_id)

    def status(self, term_id, push_id):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM canvas_content_pushes WHERE id=? AND term_id=?", (push_id, str(term_id))).fetchone()
            if not row:
                raise NotFound("That push was not found.")
            return self._push_view(row)

    # --- the push itself ---------------------------------------------------------------------------------------------------------
    def _progress(self, push_id, progress, status=None):
        with connect(self.path) as db:
            if status:
                db.execute("UPDATE canvas_content_pushes SET progress_json=?, status=?, finished_at=? WHERE id=? AND status='running'",
                           (json.dumps(progress), status, timestamp(), push_id))
            else:
                db.execute("UPDATE canvas_content_pushes SET progress_json=? WHERE id=? AND status='running'", (json.dumps(progress), push_id))

    def _run(self, push_id, term_id, found, progress, actor, executor=None, writer=None):
        """Runs an approved plan, one course after another: `executor` the class doing one course (Push; a direct edit's
        SectionPush) and `writer(push_id, progress, status=None)` the one recording progress (this term's pushes by default)."""
        executor, writer = executor or Push, writer or self._progress
        status = "done"
        try:
            for cid in found["plans"]:
                if push_id in self._cancel:
                    status = "cancelled"
                    break
                try:
                    with self.canvas.client() as client:
                        executor(self, push_id, term_id, cid, found, progress, client, actor, writer).run()
                except _Cancelled:
                    status = "cancelled"
                    break
                except web.SignedOut as error:
                    progress["courses"][cid]["errors"].append({"label": "Canvas", "message": str(error)})
                    progress["signed_out"] = True
                    status = "failed"
                    break
        except Exception as error:  # noqa: BLE001 - the push must end with a status, whatever happened
            progress["error"] = f"{type(error).__name__}: {error}"[:500]
            status = "failed"
        finally:
            progress["current"] = ""
            try:
                writer(push_id, progress, status)
            finally:  # the courses are free again even when the last record could not be written (2026-10-04 review)
                with self._guard:
                    for cid in self._active.pop(push_id, ()):
                        self._claims[cid] = self._claims.get(cid, 0) + 1  # a plan read while it wrote is stale (`claim`)
                    self._cancel.discard(push_id)
                self.canvas.writes.end(f"push:{push_id}")


class _Cancelled(Exception):
    pass


class Push:
    """One course of one push: the operations in order, each recorded when Canvas answers. What a failed operation would have
    written is reported and the rest go on; objects that depend on it (an item of a module that could not be made) fail with it."""

    def __init__(self, service, push_id, term_id, course_id, found, progress, client, actor, writer=None):
        self.s, self.push_id, self.term_id, self.cid = service, push_id, term_id, course_id
        self.write = writer or service._progress
        self.found, self.progress, self.client, self.actor = found, progress, client, actor
        self.course = found["courses"][course_id]
        self.content = found["content"]
        self.map = {kind: dict(found["matches"][course_id][0][kind]) for kind in model.KINDS}
        self.pages = {p["id"]: p for p in self.content["pages"]}
        self.modules = {m["id"]: m for m in self.content["modules"]}
        self.items = {i["id"]: i for m in self.content["modules"] for i in m["items"]}
        self.assignments = {a["id"]: a for a in self.content.get("assignments", [])}
        self.base = f"/api/v1/courses/{int(course_id)}"
        # The pages and assignments this push makes here: a link to one not made yet is written once it exists (see run).
        self.creating = {op["key"] for op in found["plans"][course_id]["ops"] if op["op"] in ("create_page", "create_assignment")}
        self.relink = []  # (kind, plan key) of the pages and assignments written while a link of theirs waited for one of those

    # bookkeeping
    def _mine(self):
        return self.progress["courses"][self.cid]

    def _check_cancel(self):
        if self.push_id in self.s._cancel:
            raise _Cancelled()

    def _record(self, kind, key, canvas_id, sent, canvas_state, source="pushed"):
        with self.s.rosters.lock(), connect(self.s.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self.s._link_row(db, self.term_id, self.cid, kind, key, canvas_id, source,
                             model.snapshot(sent, canvas_state) if sent is not None else {}, self.push_id, self.actor)

    def run(self):
        ops = self.found["plans"][self.cid]["ops"]
        order = ["upload_file", "create_group", "create_assignment", "update_assignment", "create_page", "update_page", "create_module", "update_module", "delete_item", "replace_item", "create_item",
                 "move_item", "update_item", "delete_module", "delete_page", "delete_assignment", "order_modules", "order_items"]
        for name in order:
            for op in [o for o in ops if o["op"] == name]:
                self._check_cancel()
                self.progress["current"] = f"{op.get('label') or op['op']}"
                try:
                    getattr(self, name)(op)
                    self._mine()["done"] += 1
                except web.SignedOut:
                    raise
                except (web.CanvasError, _Skip) as error:
                    self._mine()["failed"] += 1
                    self._mine()["errors"].append({"label": op.get("label") or op["op"], "message": str(error)[:300]})
                    self.creating.discard(op.get("key"))  # not made: a link to it is a problem from now on, never written as it was
                self.write(self.push_id, self.progress)
        self._relink()

    def _relink(self):
        """The pages and assignments this push wrote whose links name a page or assignment it made later: written again now that
        those exist. One whose link still cannot be found here (what it names could not be made) keeps a link to another section's
        course: it is reported as a failure (2026-10-04 review: it was skipped silently), and the next preview writes it again
        (content._relinked) while what it names is still to be made."""
        for kind, key in dict.fromkeys(self.relink):
            self._check_cancel()
            entry, field = (self.pages[key], "body") if kind == "page" else (self.assignments[key], "description")
            canvas_id = self.map[kind][key]
            label = entry["title"] if kind == "page" else entry["name"]
            body, problems = self._render(entry, field)
            current = self.course.page_by_id[canvas_id]["body"] if kind == "page" else self.course.assignment_by_id[canvas_id]["description"]
            if not problems and model.same_html(body, current):
                continue
            self._mine()["total"] += 1
            if problems:
                self._mine()["failed"] += 1
                self._mine()["errors"].append({"label": label, "message": ("a link could not be written here (" + "; ".join(problems)
                                                                           + "): it still points at another section's course. The next push writes it.")[:300]})
            else:
                try:
                    if kind == "page":
                        self._write_page(key, {"body": body}, canvas_id)
                    else:
                        answer = self.client.write("PUT", f"{self.base}/assignments/{int(canvas_id)}", {"assignment": {"description": body}})
                        self._record_assignment(key, answer)
                    self._mine()["done"] += 1
                except web.SignedOut:
                    raise
                except web.CanvasError as error:
                    self._mine()["failed"] += 1
                    self._mine()["errors"].append({"label": label, "message": str(error)[:300]})
            self.write(self.push_id, self.progress)

    # files (canvas FILES)
    section_id = None  # a direct push names its section on each upload's receipt (SectionPush)

    def upload_file(self, op):
        """One kept file into this course's Files at its folder and name. Canvas's answer is a receipt as it comes: `uploaded`,
        `failed` (Canvas refused) or `uncertain` (no answer: the file may be there; nothing sends it again but another approved
        push, whose preview reads Canvas first). A new file whose items are all unpublished is unpublished at once, so it never
        waits published in Files for its item; one that takes another's place keeps that file's state, as Canvas does."""
        row, content = self.s.files.read(self.term_id, op["key"])
        folder = op["path"].rpartition("/")[0].removeprefix(model.FILES_ROOT).strip("/")
        try:
            made = self.client.upload_course_file(self.cid, row["name"], row["media_type"], content, folder, replace=op["replace"])
        except web.SignedOut:
            raise
        except web.CanvasError as error:
            refused = error.refused
            self.s.files.record(op, self.cid, "failed" if refused else "uncertain", self.push_id, self.actor,
                                http_status=error.status, detail=str(error), section_id=self.section_id)
            raise
        renamed = bool(made["name"]) and made["name"] != row["name"]
        self.s.files.record(op, self.cid, "uploaded", self.push_id, self.actor, canvas_file_id=made["id"],
                            detail=f"Canvas named it {made['name']}" if renamed else "", section_id=self.section_id)
        if renamed:
            raise _Skip(f"Canvas had a file of this name by the time this one arrived and kept this one as “{made['name']}”. "
                        "Delete one of the two in Canvas's Files, then push again.")
        for other in [fid for fid, path in self.course.files.items() if path == op["path"]]:
            self.course.files.pop(other)  # the file it took the place of
            self.course.file_info.pop(other, None)
        self.course.files[made["id"]] = op["path"]
        self.course.file_info[made["id"]] = {"size": made["size"], "locked": made["locked"]}
        self.course.file_by_path = model.files_by_path(self.course.files)
        if op.get("locked") and not made["locked"]:
            try:
                self.client.lock_file(made["id"], True)
            except web.SignedOut:
                raise
            except web.CanvasError as error:
                raise _Skip(f"It is in Canvas's Files, but Canvas did not unpublish it there ({error}) Its item unpublishes it.") from None
            self.course.file_info[made["id"]]["locked"] = True

    def _file_target(self, item):
        """The Canvas content an item names here, or a _Skip saying why not (its kept file could not be put in this course)."""
        target, reason = self.course.resolve(item)
        if target is None:
            raise _Skip("its file could not be put in this course's Files" if (item.get("content") or {}).get("file") else reason)
        return target

    # assignments
    def create_group(self, op):
        answer = self.client.write("POST", f"{self.base}/assignment_groups", {"name": op["name"]})
        self.course.groups[str(answer["id"])] = str(answer.get("name") or op["name"])
        self.course.group_by_name = model._unique((model.fold(n), g) for g, n in self.course.groups.items())

    def _assignment_payload(self, assignment):
        sections = self.found["plans"][self.cid]["sections"]
        due, problem = model.due_for(assignment, sections)
        if problem:
            raise _Skip(problem)
        description, problems = self._render(assignment, "description")  # links to what this push makes later: see _relink
        if problems:
            raise _Skip("its links changed since the preview: " + "; ".join(problems))
        payload = {"name": assignment["name"], "description": description, "points_possible": float(assignment["points"]),
                   "grading_type": assignment["grading_type"], "due_at": due, "unlock_at": assignment["unlock_at"],
                   "lock_at": assignment["lock_at"], "published": assignment["published"],
                   "omit_from_final_grade": assignment["omit_from_final_grade"]}
        if not assignment["external"]:
            payload["submission_types"] = assignment["submission_types"]
        if assignment["group"]:
            group = self.course.group_by_name.get(model.fold(assignment["group"]))
            if group in (None, model._MANY):
                raise _Skip(f"its Assignment Group “{assignment['group']}” could not be made or is not unique here")
            payload["assignment_group_id"] = group
        return payload

    def _record_assignment(self, key, answer):
        row = web.assignment_row(answer)
        self.course.assignments = [a for a in self.course.assignments if a["id"] != row["id"]] + [row]
        self.course.assignment_by_id[row["id"]] = row
        self.course.named["Assignment"][row["id"]] = row["name"]
        self.course.named_by_name["Assignment"] = model._unique((model.fold(n), i) for i, n in self.course.named["Assignment"].items())
        self.map["assignment"][key] = row["id"]
        self._record("assignment", key, row["id"], model.assignment_plan_state(self.assignments[key]),
                     model.assignment_canvas_state(row, self.course.groups))
        if self._waits(self.assignments[key], "description"):
            self.relink.append(("assignment", key))

    def create_assignment(self, op):
        answer = self.client.write("POST", f"{self.base}/assignments", {"assignment": self._assignment_payload(self.assignments[op["key"]])})
        self._record_assignment(op["key"], answer)

    def update_assignment(self, op):
        answer = self.client.write("PUT", f"{self.base}/assignments/{int(op['canvas_id'])}",
                                   {"assignment": self._assignment_payload(self.assignments[op["key"]])})
        self._record_assignment(op["key"], answer)

    def delete_assignment(self, op):
        self.client.write("DELETE", f"{self.base}/assignments/{int(op['canvas_id'])}")
        self._forget("assignment", op["canvas_id"])

    # pages
    def _render(self, page, field="body"):
        """The body for this course, links to what this push makes here and has not made yet left for `_relink`."""
        matches = {cid: m[0] for cid, m in self.found["matches"].items()}
        matches[self.cid] = self.map
        return model.render(page[field], self.cid, self.found["courses"], matches, self._waiting())

    def _waiting(self):
        return {key for key in self.creating if key not in self.map["page"] and key not in self.map["assignment"]}

    def _waits(self, entry, field):
        """Whether a link of the page's or assignment's names one of those this push makes here and has not made yet."""
        waiting = self._waiting()
        if not waiting:
            return False
        matches = {cid: m[0] for cid, m in self.found["matches"].items()}
        matches[self.cid] = self.map
        args = (entry[field], self.cid, self.found["courses"], matches)
        return model.render(*args, frozenset())[1] != model.render(*args, waiting)[1]

    def _write_page(self, key, fields, canvas_id=None):
        page = self.pages[key]
        payload = {"wiki_page": fields}
        answer = self.client.write("PUT" if canvas_id else "POST", f"{self.base}/pages/{int(canvas_id)}" if canvas_id else f"{self.base}/pages", payload)
        row = web.page_row(answer)
        self.course.pages = [p for p in self.course.pages if p["id"] != row["id"]] + [row]
        self.course.page_by_id[row["id"]] = row
        self.course.page_by_url[row["url"]] = row
        self.map["page"][key] = row["id"]
        self._record("page", key, row["id"], model.page_plan_state(page), model.page_canvas_state(row))
        if self._waits(page, "body"):
            self.relink.append(("page", key))
        return row

    def create_page(self, op):
        page = self.pages[op["key"]]
        body, problems = self._render(page)  # links to what this push makes later are written once they exist (_relink)
        if problems:
            raise _Skip("its links changed since the preview: " + "; ".join(problems))
        self._write_page(op["key"], {"title": page["title"], "body": body, "published": page["published"], "front_page": page["front_page"]})

    def update_page(self, op):
        page = self.pages[op["key"]]
        body, problems = self._render(page)
        if problems:
            raise _Skip("its links changed since the preview: " + "; ".join(problems))
        self._write_page(op["key"], {"title": page["title"], "body": body, "published": page["published"], "front_page": page["front_page"]},
                         op["canvas_id"])

    # modules
    def _module_payload(self, module):
        return {"name": module["name"], "unlock_at": module["unlock_at"], "require_sequential_progress": module["require_sequential_progress"]}

    def _record_module(self, key, answer):
        module = self.modules[key]
        row = web.module_row(answer)
        self.map["module"][key] = row["id"]
        self._record("module", key, row["id"], model.module_plan_state(module), model.module_state(row))

    # Publishing or unpublishing a module in Canvas also publishes or unpublishes every item in it, and so every page and
    # assignment they point to (context_modules_api_controller#update: publish_items!), unless `skip_content_tags` says the
    # module alone, as Canvas's own "Publish module only" does. Faculty keeps each item's flag itself, so it sends a module's
    # flag only when the copy changed it, and always for the module alone (2026-10-04 review: a module renamed in Faculty would
    # have published the exam information pages its Weeks 9 and 13 hold unpublished).
    @staticmethod
    def _module_published(published):
        return {"published": published, "skip_content_tags": True}

    def create_module(self, op):
        module = self.modules[op["key"]]
        payload = {k: v for k, v in self._module_payload(module).items() if v is not None}
        answer = self.client.write("POST", f"{self.base}/modules", {"module": payload})
        if bool(answer.get("published")) != module["published"]:
            answer = self.client.write("PUT", f"{self.base}/modules/{int(answer['id'])}", {"module": self._module_published(module["published"])})
        self._record_module(op["key"], answer)

    def update_module(self, op):
        module = self.modules[op["key"]]
        payload = self._module_payload(module)
        if "published" in op.get("fields", {}):
            payload.update(self._module_published(module["published"]))
        answer = self.client.write("PUT", f"{self.base}/modules/{int(op['canvas_id'])}", {"module": payload})
        self._record_module(op["key"], answer)

    # items
    def _item_payload(self, item):
        payload = {"title": item["title"], "indent": item["indent"]}
        if item["type"] in ("ExternalUrl", "ExternalTool"):
            payload.update(external_url=item["external_url"], new_tab=item["new_tab"])
        return payload

    def _record_item(self, key, answer):
        row = web.item_row(answer)
        self.map["item"][key] = row["id"]
        self._record("item", key, row["id"], model.item_plan_state(self.items[key]), model.item_canvas_state(row))

    def _module_of(self, item_key):
        module_key = next(m["id"] for m in self.content["modules"] if any(i["id"] == item_key for i in m["items"]))
        canvas_id = self.map["module"].get(module_key)
        if not canvas_id:
            raise _Skip("its module could not be made in this course")
        return canvas_id

    def create_item(self, op):
        item = self.items[op["key"]]
        module_id = self._module_of(op["key"])
        payload = {**self._item_payload(item), "type": item["type"]}
        if item["type"] == "Page":
            canvas_page = self.map["page"].get(item["page"])
            if not canvas_page:
                raise _Skip("its page could not be made in this course")
            payload["page_url"] = self.course.page_by_id[canvas_page]["url"]
        elif (item.get("content") or {}).get("assignment"):
            target = self.map["assignment"].get(item["content"]["assignment"])
            if not target:
                raise _Skip("its assignment could not be made in this course")
            payload["content_id"] = target
        elif item["type"] != "SubHeader" and item["type"] != "ExternalUrl":
            payload["content_id"] = self._file_target(item)
        answer = self.client.write("POST", f"{self.base}/modules/{int(module_id)}/items", {"module_item": payload})
        # An item of a page or an assignment is published with it (model.follows_content): asking for the item's flag would
        # publish or unpublish the page or assignment itself.
        if not model.follows_content(item) and bool(answer.get("published")) != item["published"]:
            answer = self.client.write("PUT", f"{self.base}/modules/{int(module_id)}/items/{int(answer['id'])}",
                                       {"module_item": {"published": item["published"]}})
        self._record_item(op["key"], answer)
        self._file_published(item, answer)

    def _file_published(self, item, answer):
        """FILES: a File item publishes its file with it; Canvas keeps both unpublished while the course asks for usage rights the
        file does not have. Said plainly instead of leaving the instructor to find the item unpublished."""
        if item["type"] == "File" and item["published"] and not answer.get("published"):
            raise _Skip("Canvas kept it unpublished (its Files may ask for usage rights first). Publish it in Canvas's Files.")

    def _current_module(self, canvas_item):
        found = self.course.item_by_id.get(canvas_item)
        return found[0]["id"] if found else None

    def update_item(self, op):
        item = self.items[op["key"]]
        module_id = self._current_module(op["canvas_id"]) or self._module_of(op["key"])
        payload = self._item_payload(item)
        if "published" in op.get("fields", {}) and not model.follows_content(item):
            payload["published"] = item["published"]  # only when the copy changed it (see create_item)
        answer = self.client.write("PUT", f"{self.base}/modules/{int(module_id)}/items/{int(op['canvas_id'])}", {"module_item": payload})
        self._record_item(op["key"], answer)
        if "published" in payload:
            self._file_published(item, answer)

    def move_item(self, op):
        target = self._module_of(op["key"])
        module_id = self._current_module(op["canvas_id"])
        answer = self.client.write("PUT", f"{self.base}/modules/{int(module_id)}/items/{int(op['canvas_id'])}",
                                   {"module_item": {"module_id": target}})
        self.course.item_by_id[op["canvas_id"]] = ({"id": target}, web.item_row(answer))
        self._record_item(op["key"], answer)

    def replace_item(self, op):
        item = self.items[op["key"]]
        if (item.get("content") or {}).get("file"):
            self._file_target(item)  # before the old item is deleted: its file must be in this course
        module_id = self._current_module(op["canvas_id"])
        self.client.write("DELETE", f"{self.base}/modules/{int(module_id)}/items/{int(op['canvas_id'])}")
        self.map["item"].pop(op["key"], None)
        self.create_item(op)

    # removals
    def delete_item(self, op):
        module_id = self._current_module(op["canvas_id"])
        self.client.write("DELETE", f"{self.base}/modules/{int(module_id)}/items/{int(op['canvas_id'])}")
        self._forget("item", op["canvas_id"])

    def delete_module(self, op):
        self.client.write("DELETE", f"{self.base}/modules/{int(op['canvas_id'])}")
        self._forget("module", op["canvas_id"])

    def delete_page(self, op):
        self.client.write("DELETE", f"{self.base}/pages/{int(op['canvas_id'])}")
        self._forget("page", op["canvas_id"])

    def _forget(self, kind, canvas_id):
        for (k, key), link in self.found["links"].get(self.cid, {}).items():
            if k == kind and link["canvas_id"] == canvas_id:
                self._record(kind, key, "", None, None, source="removed")

    # order
    def order_modules(self, op):
        current = [m["id"] for m in sorted((web.module_row(m) for m in self.client.listing(f"{self.base}/modules")), key=lambda m: m["position"])]
        managed = [self.map["module"][key] for key in op["order"] if key in self.map["module"]]
        desired = managed + [mid for mid in current if mid not in managed]
        self._place(current, desired, lambda mid, position: self.client.write("PUT", f"{self.base}/modules/{int(mid)}", {"module": {"position": position}}))

    def order_items(self, op):
        module_id = self.map["module"].get(op["key"])
        if not module_id:
            raise _Skip("its module could not be made in this course")
        current = [i["id"] for i in self.client.module_items(self.cid, module_id)]
        managed = [self.map["item"][key] for key in op["order"] if key in self.map["item"] and self.map["item"][key] in current]
        desired = managed + [iid for iid in current if iid not in managed]
        self._place(current, desired, lambda iid, position: self.client.write(
            "PUT", f"{self.base}/modules/{int(module_id)}/items/{int(iid)}", {"module_item": {"position": position}}))

    def _place(self, current, desired, put):
        order = list(current)
        for index, key in enumerate(desired):
            if order.index(key) != index:
                self._check_cancel()
                put(key, index + 1)
                order.remove(key)
                order.insert(index, key)


class _Skip(Exception):
    """An operation that cannot be done in this course (reported, never retried by itself)."""
