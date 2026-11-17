"""Putting one of Faculty's assignments, or a gradebook column, on Canvas (requirement, 2026-09-30). canvas/README.md "Putting a Faculty assignment on Canvas".

A placement is a CHANGE OF FACULTY'S TERM COPY (`canvas_content_revisions`, the owner of the term's Canvas assignments and modules):
the copy's assignment (a new one, or the one that already stands for this Faculty assignment or column, found by the link, else by
its name) and an Assignment item in the chosen module. It is saved through the copy's ordinary `save` (normalize, `base_revision`,
request ID) and never contacts Canvas: the ordinary preview and push take it to each section's course.

Dates are campus wall clock (America/Chicago), converted here, never in the browser. The proposal reads Faculty's records only.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, time, timezone
from scheduling.clock import CAMPUS_ZONE

from werkzeug.exceptions import Conflict, NotFound

from rosters import connect

from . import content as model

CAMPUS = CAMPUS_ZONE
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
CLOCK = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
DEFAULT_CLOCK = "23:59"


def campus_instant(day, clock):
    """The UTC instant Canvas takes for a campus date and wall-clock time ("" or None: no date)."""
    if day in (None, ""):
        return None
    if not isinstance(day, str) or not DAY.fullmatch(day):
        raise ValueError("Give the due date as a date.")
    clock = clock or DEFAULT_CLOCK
    found = CLOCK.fullmatch(clock) if isinstance(clock, str) else None
    if not found:
        raise ValueError("Give the due time as hours and minutes.")
    try:
        date = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("That due date is not a date.") from None
    local = datetime.combine(date, time(int(found.group(1)), int(found.group(2))), tzinfo=CAMPUS)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def campus_parts(instant):
    """(campus date, "HH:MM") of a stored instant, ("", "") for none."""
    if not instant:
        return "", ""
    moment = datetime.strptime(instant, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(CAMPUS)
    return moment.date().isoformat(), moment.strftime("%H:%M")


def _table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _setup(db, term_id):
    """The term's latest gradebook setup (columns and categories), or an empty one."""
    if not _table(db, "gradebook_setups"):
        return {"items": [], "categories": []}
    row = db.execute("SELECT setup_json FROM gradebook_setups WHERE term_id=? ORDER BY revision DESC LIMIT 1", (term_id,)).fetchone()
    return json.loads(row["setup_json"]) if row else {"items": [], "categories": []}


def _exam_date(db, term_id, assignment_id):
    """The campus date the term's exam of this assignment is held (its seating chart, else its main sitting), or ""."""
    row = db.execute("SELECT exam_date FROM seating_charts WHERE term_id=? AND assignment_id=? AND exam_date!='' ORDER BY exam_date LIMIT 1",
                     (term_id, assignment_id)).fetchone() if _table(db, "seating_charts") else None
    if row:
        return row["exam_date"]
    if _table(db, "exam_sittings"):
        row = db.execute("""SELECT s.date FROM exam_sittings s JOIN exam_events e ON e.id=s.event_id WHERE e.term_id=? AND e.assignment_id=?
            AND s.kind='main' AND s.date!='' ORDER BY s.date LIMIT 1""", (term_id, assignment_id)).fetchone()
        if row:
            return row["date"]
    return ""


def _maximum(db, term_id, assignment_id):
    """The most points one paper of the term's grading of this assignment is worth, or None (gradebook/sources.py's rule)."""
    try:
        from gradebook import sources
        return sources.maximum(db, term_id, assignment_id, sources.PARTS)
    except Exception:  # noqa: BLE001 - a proposal; the instructor types the points when grading has none
        return None


def existing(content, faculty_assignment=None, gradebook_item=None, name=None):
    """The copy's assignment that stands for this Faculty assignment or column: its link first, else the one of this name."""
    assignments = (content or {}).get("assignments", [])
    for key, value in (("faculty_assignment", faculty_assignment), ("gradebook_item", gradebook_item)):
        if value:
            found = next((a for a in assignments if a.get(key) == value), None)
            if found:
                return found
    if name:
        # By name only when it stands for nothing else: another Faculty assignment's or column's is never taken over.
        return next((a for a in assignments if model.fold(a["name"]) == model.fold(name)
                     and a.get("faculty_assignment") in (None, faculty_assignment) and a.get("gradebook_item") in (None, gradebook_item)), None)
    return None


def module_of(content, key):
    """The module an Assignment item of the copy points at `key` from, or None."""
    return next((m for m in (content or {}).get("modules", []) for i in m["items"] if (i.get("content") or {}).get("assignment") == key), None)


class Placement:
    def __init__(self, service):
        self.s = service  # CanvasContentService

    def _faculty(self, db, term, assignment_id):
        row = db.execute("SELECT id,title,definition_id FROM assignments WHERE id=?", (str(assignment_id),)).fetchone()
        if not row or row["definition_id"] != term["definition_id"]:
            raise NotFound("That assignment is not one of this course's.")
        return {"id": row["id"], "title": row["title"]}

    def _column(self, setup, item_id):
        column = next((i for i in setup.get("items", []) if i.get("id") == item_id and not i.get("archived")), None)
        if not column:
            raise NotFound("That gradebook column is not in this term's gradebook.")
        return column

    def _resolve(self, db, term, assignment_id=None, item_id=None):
        """(Faculty assignment or None, gradebook column or None, setup): a column names its assignment, and an assignment its column."""
        setup = _setup(db, term["id"])
        column = self._column(setup, item_id) if item_id else None
        if column and column.get("assignment_id") and not assignment_id:
            assignment_id = column["assignment_id"]
        faculty = self._faculty(db, term, assignment_id) if assignment_id else None
        if faculty and not column:
            column = next((i for i in setup.get("items", []) if i.get("assignment_id") == faculty["id"] and not i.get("archived")), None)
        return faculty, column, setup

    def proposal(self, term_id, assignment_id=None, item_id=None):
        """What "Put on Canvas" would place, from Faculty's records alone: the copy's state, the assignment it would update (or
        None: a new one), the prefilled fields, the copy's modules and assignment groups, and the term's Canvas courses."""
        if not assignment_id and not item_id:
            raise ValueError("Name the assignment or the gradebook column to put on Canvas.")
        with self.s.rosters.lock(), connect(self.s.path) as db:
            term = self.s._term(db, term_id)
            content, revision, _ = self.s._latest(db, term["id"])
            targets, unlinked = self.s._targets(db, term["id"])
            faculty, column, setup = self._resolve(db, term, assignment_id, item_id)
            name = (faculty or {}).get("title") or (column or {}).get("title") or ""
            found = existing(content, (faculty or {}).get("id"), (column or {}).get("id"), name)
            categories = {c["id"]: c["name"] for c in setup.get("categories", [])}
            points = (column or {}).get("points") or (_maximum(db, term["id"], faculty["id"]) if faculty else None) or ""
            due_day = (column or {}).get("due_date") or (_exam_date(db, term["id"], faculty["id"]) if faculty else "")
            pushed = self.s._pushed_courses(db, term["id"])
        if found:
            day, clock = campus_parts(found["due_at"])
            home = module_of(content, found["id"])
            fields = {"name": found["name"], "points": found["points"], "group": found["group"], "due_date": day, "due_time": clock,
                      "submission": (found["submission_types"] or ["none"])[0] if not found["external"] else "", "external": found["external"],
                      "published": found["published"], "module": home["id"] if home else ""}
        else:
            fields = {"name": name, "points": str(points), "group": categories.get((column or {}).get("category_id") or "", ""),
                      "due_date": due_day or "", "due_time": DEFAULT_CLOCK if due_day else "", "submission": "on_paper", "external": False,
                      "published": False, "module": ""}
        groups = sorted({a["group"] for a in (content or {}).get("assignments", []) if a["group"]} | {n for n in categories.values() if n},
                        key=model.fold)
        state = "no_courses" if not targets else "not_started" if content is None else "ready"
        return {"state": state, "term": {"id": term["id"], "code": term["code"]}, "group": self.s._group(term), "revision": revision, "assignment": faculty,
                "column": {"id": column["id"], "title": column["title"]} if column else None,
                "existing": {"key": found["id"], "name": found["name"], "linked": bool(found.get("faculty_assignment") or found.get("gradebook_item"))} if found else None,
                "fields": fields, "modules": [{"id": m["id"], "name": m["name"]} for m in (content or {}).get("modules", [])], "groups": groups,
                "courses": targets, "unlinked": unlinked, "pushed_courses": [t["course_id"] for t in targets if t["course_id"] in pushed]}

    def place(self, term_id, data, actor="workspace"):
        """Saves the placement as a new revision of the copy (see the module docstring). `data`: base_revision, request_id,
        assignment_id and/or item_id, fields {name, points, group, due_date, due_time, submission, published}, module {id} or
        {new_name} or null (leave the modules as they are). Returns the copy's view and the placed assignment's `key`."""
        data = data if isinstance(data, dict) else {}
        from .content_service import _request_id
        request_id = _request_id(data.get("request_id"))
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        with self.s.rosters.lock(), connect(self.s.path) as db:
            term = self.s._term(db, term_id)
            content, revision, _ = self.s._latest(db, term["id"])
            done = db.execute("SELECT 1 FROM canvas_content_revisions WHERE term_id=? AND request_id=?", (term["id"], request_id)).fetchone()
            faculty, column, _ = self._resolve(db, term, data.get("assignment_id"), data.get("item_id"))
        link = {"faculty_assignment": (faculty or {}).get("id"), "gradebook_item": (column or {}).get("id")}
        if done:  # the same request again: what it saved
            found = existing(content, link["faculty_assignment"], link["gradebook_item"], fields.get("name"))
            return {**self.s.view(term["id"]), "key": found["id"] if found else None}
        if content is None:
            raise Conflict("Start Faculty's copy of the term's Canvas content first.")
        # A group is required: Canvas puts an assignment made without one in the course's FIRST group (requirement, 2026-10-02: a
        # certified Quiz 3 column without a category was saved with a blank group and landed in Checks for Understanding).
        if not (fields.get("group") if isinstance(fields.get("group"), str) else "").strip():
            raise ValueError("Choose its Assignment Group: Canvas puts an assignment without one in the course's first Assignment Group.")
        if revision != data.get("base_revision"):
            raise Conflict("Faculty's copy of the Canvas content was saved from another window since you opened this. Open it again.")
        name = fields.get("name") if isinstance(fields.get("name"), str) else ""
        draft = copy.deepcopy(content)
        found = existing(draft, link["faculty_assignment"], link["gradebook_item"], name)
        entry = found or {"ref": f"placed-{request_id}"[:64], "description": "", "grading_type": "points", "external": False, "section_due": {},
                          "unlock_at": None, "lock_at": None, "omit_from_final_grade": False, "sections": []}
        entry.update(name=name.strip(), points=str(fields.get("points") if fields.get("points") is not None else "").strip(),
                     group=fields["group"].strip(), due_at=campus_instant(fields.get("due_date"), fields.get("due_time")),
                     published=fields.get("published") is True)
        if not entry.get("external"):
            submission = fields.get("submission") or "on_paper"
            entry["submission_types"] = [submission]
        # The link: a column or assignment the copy's assignment stood for is kept; this one is added.
        for key, value in link.items():
            if value:
                entry[key] = value
        if not found:
            draft["assignments"].append(entry)
        key = entry.get("id") or entry["ref"]
        self._module(draft, key, entry, data.get("module"))
        self.s.save(term["id"], {"base_revision": revision, "request_id": request_id, "content": draft}, actor=actor)
        with connect(self.s.path) as db:
            saved, _, _ = self.s._latest(db, term["id"])
        placed = existing(saved, link["faculty_assignment"], link["gradebook_item"], entry["name"])
        return {**self.s.view(term["id"]), "key": placed["id"] if placed else None}

    @staticmethod
    def _module(draft, key, entry, choice):
        """Puts an Assignment item for `key` in the chosen module (made when new), moving the one that points at it elsewhere; its
        title is the assignment's (Canvas names an assignment's item after the assignment)."""
        for module in draft["modules"]:
            for item in module["items"]:
                if (item.get("content") or {}).get("assignment") == key:
                    item["title"] = entry["name"]
        if choice in (None, ""):
            return
        if not isinstance(choice, dict):
            raise ValueError("Choose the module, or a name for a new one.")
        if choice.get("new_name"):
            name = str(choice["new_name"]).strip()
            if not name:
                raise ValueError("Name the new module.")
            target = {"name": name, "published": entry["published"], "unlock_at": None, "require_sequential_progress": False, "sections": [], "items": []}
            draft["modules"].append(target)
        else:
            target = next((m for m in draft["modules"] if m.get("id") == choice.get("id")), None)
            if target is None:
                raise ValueError("That module is not in Faculty's copy any more. Open the dialog again.")
        current = module_of(draft, key)
        if current is target:
            return
        item = None
        if current is not None:
            item = next(i for i in current["items"] if (i.get("content") or {}).get("assignment") == key)
            current["items"].remove(item)
        target["items"].append(item or {"type": "Assignment", "title": entry["name"], "indent": 0, "published": entry["published"],
                                        "content": {"assignment": key}, "sections": []})

    def status(self, term_id):
        """Per assignment of the copy that stands for a Faculty assignment or a gradebook column: in which of the term's Canvas
        courses it is (`pushed`), changed in the copy since it was pushed there (`changed`), or not yet (`not_yet`). Faculty's
        records only."""
        with self.s.rosters.lock(), connect(self.s.path) as db:
            term = self.s._term(db, term_id)
            content, revision, _ = self.s._latest(db, term["id"])
            targets, _ = self.s._targets(db, term["id"])
            links = self.s._links(db, term["id"])
        rows = []
        for a in (content or {}).get("assignments", []):
            if not a.get("faculty_assignment") and not a.get("gradebook_item"):
                continue
            courses = {}
            for target in targets:
                if not model.applies(a, [s["id"] for s in target["sections"]]):
                    continue
                link = links.get(target["course_id"], {}).get(("assignment", a["id"]))
                snap = json.loads(link["snapshot_json"] or "{}") if link and link["canvas_id"] else {}
                courses[target["course_id"]] = ("not_yet" if not snap else
                                                "pushed" if snap.get("sent") == model.digest(model.assignment_plan_state(a)) else "changed")
            rows.append({"key": a["id"], "name": a["name"], "faculty_assignment": a.get("faculty_assignment"), "gradebook_item": a.get("gradebook_item"),
                         "courses": courses, "pushed": sum(1 for v in courses.values() if v == "pushed"), "total": len(courses)})
        return {"configured": bool(self.s.canvas.status().get("configured")), "started": content is not None, "revision": revision,
                "courses": targets, "assignments": rows}
