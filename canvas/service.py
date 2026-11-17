"""Canvas integration (requirement, 2026-09-27; AGENTS.md "Canvas"): each section's Canvas course, linked once on its course page,
and what Faculty reads there with the instructor's kept Canvas session (`canvas.web`).

Owners: the link is SECTION-owned (`canvas_course_links`, append-only, latest row counts). The roster comparison is a VIEW: it
reads Faculty's catalog (UINs of the section's memberships) and Canvas's enrollments and stores nothing. JSON never carries a UIN;
names come from Faculty's verified roster payload or, for students only Canvas lists, from Canvas.

Lock order: Canvas is read with no lock held; the roster lifecycle lock is taken for Faculty's own reads and the link's write,
which is one `BEGIN IMMEDIATE` checking `base` (409 when the link changed since the page read it).
"""
from __future__ import annotations

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from werkzeug.exceptions import BadGateway, Conflict, HTTPException, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from rosters import connect
from seating import student_name, term_label

from . import web
from .stopping import Writes


def _section(db, section_id):
    row = db.execute("""SELECT s.id,s.number,s.term_id,t.term,t.term_other,t.year,t.code FROM course_sections s
        JOIN course_terms t ON t.id=s.term_id WHERE s.id=?""", (str(section_id),)).fetchone()
    if not row:
        raise NotFound("That section was not found.")
    return {"section_id": row["id"], "section": row["number"], "term_id": row["term_id"], "term_label": term_label(row), "code": row["code"] or ""}


def _link(db, section_id):
    row = db.execute("SELECT * FROM canvas_course_links WHERE section_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (section_id,)).fetchone()
    return {"course_id": row["course_id"], "name": row["course_name"], "code": row["course_code"], "term": row["term"],
            "linked_at": row["created_at"]} if row else None


def _words(value):
    return set(re.findall(r"[a-z]+|\d+", (value or "").lower()))


def propose(courses, section):
    """The ONE Canvas course whose name or code carries this section's number and its course number, in this term (season and
    year); None when none or several fit. Nothing is guessed beyond that."""
    number, course_digits = str(int(section["section"])), set(re.findall(r"\d+", section["code"]))
    season_year = _words(section["term_label"])
    fitting = []
    for course in courses:
        text = f"{course['name']} {course['code']} {course['sis']}"
        numbers = set(re.findall(r"\d+", text))
        numbers |= {n.lstrip("0") or "0" for n in numbers}
        if number not in numbers or (course_digits and not course_digits <= numbers):
            continue
        if course["term"] and not season_year <= _words(course["term"]):
            continue
        fitting.append(course)
    return fitting[0] if len(fitting) == 1 else None


class CanvasSignedOut(HTTPException):
    """401: Canvas needs the instructor to sign in (no kept session, or Canvas refused it). The page offers Sign in to Canvas."""
    code = 401


class CanvasService:
    def __init__(self, rosters, settings=None):
        self.rosters, self.path = rosters, rosters.path
        self.settings = settings or web.Settings.environment()
        self.writes = Writes()  # this process's writes to Canvas, which a stop waits for (canvas/stopping.py)
        # Whether this process runs Faculty's Canvas writes: only the web process (app.py `__main__`, where the morning sync starts).
        # Only it settles a send, post or push it no longer runs as `interrupted`; another process (the Inbox worker's daily
        # reconciliation read, a command) shows the record as it is, never ending a write the web process still runs (2026-10-09).
        self.owner = False
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "canvas", migration.stem, migration.read_text(encoding="utf-8"))

    def client(self, patient=False):
        """`patient`: waits out Canvas's throttle once (web.Client), for a send of many assignments at once (Sync with Canvas)."""
        return web.Client(self.settings, patient=patient)

    @staticmethod
    def _canvas(error):
        """A Canvas failure as the page shows it: 401 signed out (the page offers Sign in to Canvas), else 502 could not be read."""
        if isinstance(error, web.SignedOut):
            raise CanvasSignedOut(str(error)) from None
        raise BadGateway(f"Canvas could not be read: {error}") from None

    def read_each(self, ids, one, workers=4):
        """{id: one(id)} for each id, read at once in `workers` threads with NO lock held. A Canvas failure is shown as `_canvas` does."""
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                return dict(zip(ids, pool.map(one, ids)))
        except web.CanvasError as error:
            self._canvas(error)

    # --- the kept session -------------------------------------------------------------------------------------------------
    def status(self):
        """Whether Canvas is set up and a session is kept, from the file alone: Canvas is not contacted."""
        found = self.settings.status()
        return {**found, "url": self.settings.base}

    def check(self):
        """Asks Canvas who the kept session belongs to (one read); a session Canvas refuses reports signed out."""
        try:
            with self.client() as canvas:
                me = canvas.me()
        except web.SignedOut as error:
            return {**self.status(), "signed_in": False, "valid": False, "problem": str(error)}
        except web.CanvasError as error:
            self._canvas(error)
        return {**self.status(), "valid": True, "user": {"name": me["name"]}}

    def forget(self):
        web.forget(self.settings)
        return self.status()

    # --- a section's Canvas course ----------------------------------------------------------------------------------------
    def section_link(self, section_id):
        """The section's linked Canvas course, from Faculty's own records: Canvas is not contacted."""
        with self.rosters.lock(), connect(self.path) as db:
            section = _section(db, section_id)
            return {**section, **self.status(), "linked": _link(db, section["section_id"])}

    def section_courses(self, section_id):
        """The instructor's Canvas courses (where they teach), with Faculty's proposal for this section. Reads Canvas; writes nothing."""
        view = self.section_link(section_id)
        try:
            with self.client() as canvas:
                courses = canvas.courses()
        except web.CanvasError as error:
            self._canvas(error)
        courses.sort(key=lambda c: (not bool(view["term_label"]) or not _words(view["term_label"]) <= _words(c["term"]), c["term"], c["name"].casefold()))
        proposed = propose(courses, view)
        return {**view, "courses": [{k: c[k] for k in ("id", "name", "code", "term", "state")} for c in courses],
                "proposed": proposed["id"] if proposed else ""}

    def link_section(self, section_id, data, actor="workspace"):
        """Records the section's Canvas course. `base` names the course the page showed ('' for none): anything newer is a 409.
        The course must be one Canvas lists as the instructor's, read now."""
        data = data if isinstance(data, dict) else {}
        course_id = str(data.get("course_id") or "")
        if not re.fullmatch(r"\d{1,20}", course_id):
            raise ValueError("Choose a Canvas course.")
        if not isinstance(data.get("base", ""), str):
            raise ValueError("Name the course the page showed as base ('' for none).")
        base = data.get("base") or ""
        view = self.section_link(section_id)
        try:
            with self.client() as canvas:
                course = next((c for c in canvas.courses() if c["id"] == course_id), None)
        except web.CanvasError as error:
            self._canvas(error)
        if not course:
            raise Conflict("That Canvas course is not one where you teach.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = _link(db, view["section_id"])
            if (current["course_id"] if current else "") != base:
                raise Conflict("This section's Canvas course was changed elsewhere. Look at it again.")
            if not current or (current["course_id"], current["name"], current["code"], current["term"]) != (course_id, course["name"], course["code"], course["term"]):
                db.execute("INSERT INTO canvas_course_links VALUES(?,?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), view["section_id"], course_id, course["name"], course["code"], course["term"], actor, timestamp()))
            linked = _link(db, view["section_id"])
        return {**view, "linked": linked}

    # --- the roster, compared ---------------------------------------------------------------------------------------------
    def _faculty_roster(self, section):
        """{uin: (enrollment ID, name)} of the section's CURRENT roster, the same of its dropped students, and {uin: section number}
        of the term's other sections' current rosters (catalog UINs, names from the verified roster payload)."""
        with self.rosters.lock():
            with connect(self.path) as db:
                term = db.execute("SELECT * FROM course_terms WHERE id=?", (section["term_id"],)).fetchone()
                members = [dict(r) for r in db.execute("""SELECT m.section_id,m.enrollment_id,e.uin,s.number,(d.enrollment_id IS NOT NULL) dropped
                    FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id JOIN student_enrollments e ON e.id=m.enrollment_id
                    LEFT JOIN roster_dropped d ON d.section_id=m.section_id AND d.enrollment_id=m.enrollment_id WHERE s.term_id=?""",
                                                          (section["term_id"],))]
            with self.rosters.files(term) as root:
                payload = self.rosters.read_payload(root, term["roster_initialized"])
        mine, dropped, others = {}, {}, {}
        for member in members:
            if member["section_id"] == section["section_id"]:
                record = payload["sections"].get(member["section_id"], {}).get("students", {}).get(member["enrollment_id"], {})
                entry = (member["enrollment_id"], student_name({**record, "id": member["enrollment_id"]}))
                (dropped if member["dropped"] else mine)[member["uin"]] = entry
            elif not member["dropped"]:
                others.setdefault(member["uin"], member["number"])
        return mine, dropped, others

    def roster(self, section_id):
        """Faculty's section roster beside its Canvas course's students, matched by UIN (Canvas's SIS user ID) exactly. Read only."""
        view = self.section_link(section_id)
        linked = view["linked"]
        if not linked:
            raise Conflict("Link this section's Canvas course first.")
        try:
            with self.client() as canvas:
                students = canvas.students(linked["course_id"])
                sections = {s["id"]: s["name"] for s in canvas.sections(linked["course_id"])}
        except web.CanvasError as error:
            self._canvas(error)
        mine, dropped, others = self._faculty_roster(view)
        canvas_by_uin, no_id = {}, []
        for student in students:
            if student["state"] not in ("active", "invited"):
                continue
            entry = {"name": student["name"], "canvas_section": sections.get(student["section_id"], "")}
            uin = student["sis"] if re.fullmatch(r"\d{9}", student["sis"]) else ""
            if uin:
                canvas_by_uin.setdefault(uin, entry)
            else:
                no_id.append(entry)
        person = lambda pair: {"enrollment_id": pair[0], "name": pair[1]}
        both = sorted((mine[u][1] for u in mine if u in canvas_by_uin), key=str.casefold)
        # Only in Faculty: the instructor may drop them from this section (the roster's own drop, source "canvas").
        only_faculty = sorted((person(mine[u]) for u in mine if u not in canvas_by_uin), key=lambda e: e["name"].casefold())
        # Dropped here while Canvas still lists them: offered back with Restore.
        dropped_in_canvas = sorted((person(dropped[u]) for u in dropped if u in canvas_by_uin), key=lambda e: e["name"].casefold())
        elsewhere = sorted(({**canvas_by_uin[u], "faculty_section": others[u]} for u in canvas_by_uin if u not in mine and u in others),
                           key=lambda e: e["name"].casefold())
        only_canvas = sorted((canvas_by_uin[u] for u in canvas_by_uin if u not in mine and u not in others and u not in dropped),
                             key=lambda e: e["name"].casefold())
        return {"section_id": view["section_id"], "section": view["section"], "course": linked, "canvas_sections": sorted(sections.values()),
                "counts": {"faculty": len(mine), "canvas": len(canvas_by_uin) + len(no_id), "both": len(both), "only_faculty": len(only_faculty),
                           "only_canvas": len(only_canvas), "other_section": len(elsewhere), "no_id": len(no_id),
                           "dropped": len(dropped), "dropped_in_canvas": len(dropped_in_canvas)},
                "only_faculty": only_faculty, "only_canvas": only_canvas, "other_section": elsewhere, "dropped_in_canvas": dropped_in_canvas,
                "no_id": sorted(no_id, key=lambda e: e["name"].casefold()),
                "ids_shared": bool(canvas_by_uin) or not students}

    # --- grades, read for the gradebook's Canvas import ----------------------------------------------------------------------
    def term_links(self, term_id):
        """{section_id: linked course} of a term's sections, from Faculty's records (Canvas is not contacted)."""
        with connect(self.path) as db:
            sections = [row["id"] for row in db.execute("SELECT id FROM course_sections WHERE term_id=?", (str(term_id),))]
            return {section_id: link for section_id in sections if (link := _link(db, section_id))}

    def read_for_send(self, course_ids, wanted):
        """For Sending to Canvas, read with NO lock held, the courses at once: each course's assignments and student enrollments,
        and the submissions of only the assignments `wanted(course_id, assignments)` names (a course's every submission is
        thousands of rows)."""
        def one(course_id):
            with self.client() as canvas:
                assignments, students = canvas.assignments(course_id), canvas.students(course_id)
                submissions = {}
                requested = sorted(wanted(course_id, assignments), key=int)
                for assignment_id in requested:
                    for user, row in canvas.assignment_submissions(course_id, assignment_id).items():
                        submissions[(assignment_id, user)] = row
                # The assignment groups, for a column whose Canvas assignment the send would create in its category's group.
                return {"assignments": assignments, "students": students, "submissions": submissions, "groups": canvas.groups(course_id),
                        "read": requested}
        return self.read_each(sorted(set(course_ids), key=int), one)

    def read_grades(self, course_ids):
        """Each course's assignment groups, student enrollments and submissions, read with the kept session and NO lock held, the
        courses at once (a course's every submission is a long paged listing: read one after another, MATH 140's four courses
        kept Sync with Canvas's summary waiting over three minutes, 2026-10-06). A signed-out session is a 401 for the page to
        offer Sign in to Canvas; anything else Canvas answers is a 502."""
        def one(course_id):
            with self.client() as canvas:
                return {"groups": canvas.assignment_groups(course_id), "students": canvas.students(course_id),
                        "submissions": canvas.submissions(course_id)}
        return self.read_each(sorted(set(course_ids), key=int), one)
