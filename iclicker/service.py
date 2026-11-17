"""iClicker (requirement, 2026-09-28; AGENTS.md "iClicker"): each section's iClicker course, chosen once in its Import from
iClicker, and what Faculty reads there with the saved sign-in (`iclicker.web`). The instructor asked for scores only: the
gradebook's Import from iClicker (gradebook/iclicker_import.py) is the one use; there is no roster comparison and no write.

Owners: the link is SECTION-owned (`iclicker_course_links`, append-only, latest row counts). Lock order: iClicker is read with no
lock held; the link's write is one `BEGIN IMMEDIATE` under the roster lifecycle lock, checking `base` (409 when it changed since
the page read it).
"""
from __future__ import annotations

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from werkzeug.exceptions import BadGateway, Conflict, NotFound

from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from rosters import connect
from seating import term_label

from . import web


def _section(db, section_id):
    row = db.execute("""SELECT s.id,s.number,s.term_id,t.term,t.term_other,t.year,t.code FROM course_sections s
        JOIN course_terms t ON t.id=s.term_id WHERE s.id=?""", (str(section_id),)).fetchone()
    if not row:
        raise NotFound("That section was not found.")
    return {"section_id": row["id"], "section": row["number"], "term_id": row["term_id"], "term_label": term_label(row), "code": row["code"] or ""}


def _link(db, section_id):
    row = db.execute("SELECT * FROM iclicker_course_links WHERE section_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (section_id,)).fetchone()
    return {"course_id": row["course_id"], "name": row["course_name"], "term": row["term"], "linked_at": row["created_at"]} if row else None


def _words(value):
    return set(re.findall(r"[a-z]+|\d+", (value or "").lower()))


def propose(courses, section):
    """The ONE current iClicker course whose name carries this section's number and its course number, in this term: its term
    ("Fall 2026") when iClicker has one, else the season and year in its name ("Math-140-507-Fall-26"). None when none or several
    fit. Nothing is guessed beyond that."""
    number, course_digits = str(int(section["section"])), set(re.findall(r"\d+", section["code"]))
    season_year = _words(section["term_label"])
    season = {w for w in season_year if not w.isdigit()}
    years = {w for w in season_year if w.isdigit()}
    fitting = []
    for course in courses:
        if course["archived"]:
            continue
        numbers = set(re.findall(r"\d+", course["name"]))
        numbers |= {n.lstrip("0") or "0" for n in numbers}
        if number not in numbers or (course_digits and not course_digits <= numbers):
            continue
        if course["term"]:
            if not season_year <= _words(course["term"]):
                continue
        elif not season <= _words(course["name"]) or not any(y in numbers or y[-2:] in numbers for y in years):
            continue
        fitting.append(course)
    return fitting[0] if len(fitting) == 1 else None


class IClickerService:
    def __init__(self, rosters, settings=None):
        self.rosters, self.path = rosters, rosters.path
        self.settings = settings or web.Settings.environment()
        with self.rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                apply_sql(db, "iclicker", migration.stem, migration.read_text(encoding="utf-8"))

    def client(self):
        return web.Client(self.settings)

    @staticmethod
    def _iclicker(error):
        """An iClicker failure as the page shows it: 400 when it is not set up here, else 502 with iClicker's reason. Never 401,
        404 or 409, which the app reserves (there is no sign-in window: the saved sign-in is used as it is)."""
        if isinstance(error, web.NotSetUp):
            raise ValueError(str(error)) from None
        if isinstance(error, web.SignInRefused):
            raise BadGateway(str(error)) from None
        raise BadGateway(f"iClicker could not be read: {error}") from None

    def status(self):
        """Whether iClicker is set up here, from the sign-in file's presence alone: iClicker is not contacted."""
        return self.settings.status()

    # --- a section's iClicker course ----------------------------------------------------------------------------------------
    def section_link(self, section_id):
        """The section's linked iClicker course, from Faculty's own records: iClicker is not contacted."""
        with connect(self.path) as db:
            section = _section(db, section_id)
            return {**section, **self.status(), "linked": _link(db, section["section_id"])}

    def section_courses(self, section_id):
        """The instructor's iClicker courses, this term's first, with Faculty's proposal for this section. Reads iClicker; writes nothing."""
        view = self.section_link(section_id)
        try:
            with self.client() as iclicker:
                courses = iclicker.courses()
        except web.IClickerError as error:
            self._iclicker(error)
        proposed = propose(courses, view)
        courses.sort(key=lambda c: (c["archived"], not (proposed and c["id"] == proposed["id"]), c["name"].casefold()))
        return {**view, "courses": courses, "proposed": proposed["id"] if proposed else ""}

    def link_section(self, section_id, data, actor="workspace"):
        """Records the section's iClicker course. `base` names the course the page showed ('' for none): anything newer is a 409.
        The course must be one iClicker lists as the instructor's, read now."""
        data = data if isinstance(data, dict) else {}
        course_id = data.get("course_id")
        if not isinstance(course_id, str) or not web.ID.fullmatch(course_id):
            raise ValueError("Choose an iClicker course.")
        base = data.get("base", "")
        if not isinstance(base, str):
            raise ValueError("Name the course the page showed as base ('' for none).")
        view = self.section_link(section_id)
        try:
            with self.client() as iclicker:
                course = next((c for c in iclicker.courses() if c["id"] == course_id), None)
        except web.IClickerError as error:
            self._iclicker(error)
        if not course:
            raise ValueError("That iClicker course is not one of yours.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = _link(db, view["section_id"])
            if (current["course_id"] if current else "") != base:
                raise Conflict("This section's iClicker course was changed elsewhere. Look at it again.")
            if not current or (current["course_id"], current["name"], current["term"]) != (course_id, course["name"], course["term"]):
                db.execute("INSERT INTO iclicker_course_links VALUES(?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), view["section_id"], course_id, course["name"], course["term"], actor, timestamp()))
            linked = _link(db, view["section_id"])
        return {**view, "linked": linked}

    # --- scores, read for the gradebook's iClicker import --------------------------------------------------------------------
    def term_links(self, term_id):
        """{section_id: linked course} of a term's sections, from Faculty's records (iClicker is not contacted)."""
        with connect(self.path) as db:
            sections = [row["id"] for row in db.execute("SELECT id FROM course_sections WHERE term_id=?", (str(term_id),))]
            return {section_id: link for section_id in sections if (link := _link(db, section_id))}

    def read_scores(self, course_ids):
        """Each course's roster and polling sessions (with every student's points), read with NO lock held, the courses at once
        (one sign-in serves them all). Anything iClicker answers otherwise is a 502 with its reason."""
        def one(course_id):
            with self.client() as iclicker:
                return {"roster": iclicker.roster(course_id), "sessions": iclicker.sessions(course_id)}
        ids = sorted(set(course_ids))
        try:
            with self.client() as iclicker:
                iclicker.sign_in()  # once, before the parallel reads share the token
            with ThreadPoolExecutor(max_workers=4) as pool:
                return dict(zip(ids, pool.map(one, ids)))
        except web.IClickerError as error:
            self._iclicker(error)
