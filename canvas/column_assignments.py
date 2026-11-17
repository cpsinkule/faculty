"""Every gradebook column stands for an assignment in an Assignment Group (requirement, 2026-10-06). docs/decisions/canvas.md COLUMN ASSIGNMENTS; canvas/README.md.

A derived aggregate (a category's percentage, the overall, the letter) is computed by the gradebook and is never a column of the
setup, so every column counts, except an archived one and one the instructor keeps off Canvas (`canvas` "off"). Of the rest:
- CANVAS HOLDS IT: a column Canvas grades itself (`canvas` "webassign" or "quiz"), or one Faculty knows a Canvas assignment of (an
  import or a sync linked it, `gradebook_canvas_links`; a sync created it, `gradebook_canvas_created`). When no assignment of the
  copy stands for it (a Canvas quiz, whose assignment the quiz keeps; an outside tool's; one made in Canvas after the copy began),
  the Assignments tab lists it read-only in its category's group (`overview` "canvas_only"). The copy never takes it.
- NO CATEGORY: it has no Assignment Group to go in; the Assignments tab names it (`overview` "ungrouped") for the instructor to
  give it one in the gradebook setup.
- FACULTY'S (Add column, an iClicker session, a file import's new column, a certified plan's column): when no assignment of the
  copy stands for it, `adopt` gives it one. The copy's assignment that stands for it by its link, else by its name when that name
  stands for nothing else (canvas/placement.py `existing`), is linked to it; else a new one is added: the column's title and
  points, its category as the Assignment Group, its sections, due 11:59 PM campus time on its due date, published, no submission
  (what Sync with Canvas creates, GRADEBOOK "SYNC WITH CANVAS"). It is a revision of the copy through the copy's own `save`, under
  the roster lifecycle lock; Canvas is not contacted. Sync with Canvas then creates it in each course as the copy has it and sends
  its grades (gradebook/canvas_export.py `from_copy`; `created_link` records the copy's link as a push would), or the push does.
- SECTIONS FOLLOW THE COLUMN (requirement, 2026-10-17): a column gains sections (an iClicker session as each section's class
  meets) and Sync creates its assignment in each course it sends to; `adopt` then widens the copy's assignment to those courses'
  sections (`uncovered`), so the push never offers to delete what Faculty itself made there.
"""
from __future__ import annotations

import copy
import json
import logging
import uuid

from rosters import connect

from . import content as model
from .placement import DEFAULT_CLOCK, _setup, _table, campus_instant, existing

log = logging.getLogger(__name__)
CANVAS_OWNED = ("webassign", "quiz")


def _counted(item):
    return not item.get("archived") and item.get("canvas") != "off"


def _known(db, term_id):
    """{column: {course: Canvas assignment}} of the columns Faculty knows a Canvas assignment of: the latest link of each Canvas
    assignment (a later one may skip it or give it to another column, gradebook/migrations/002) and the ones a sync created."""
    known = {}
    rows = []
    if _table(db, "gradebook_canvas_links"):
        rows += db.execute("""SELECT item_id,course_id,assignment_id FROM gradebook_canvas_links l WHERE term_id=? AND rowid=(SELECT x.rowid
            FROM gradebook_canvas_links x WHERE x.term_id=l.term_id AND x.course_id=l.course_id AND x.assignment_id=l.assignment_id
            ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1)""", (term_id,)).fetchall()
    if _table(db, "gradebook_canvas_created"):
        rows += db.execute("SELECT item_id,course_id,assignment_id FROM gradebook_canvas_created WHERE term_id=?", (term_id,)).fetchall()
    for row in rows:
        if row["item_id"] and row["assignment_id"]:
            known.setdefault(row["item_id"], {})[str(row["course_id"])] = str(row["assignment_id"])
    return known


def _copy_canvas(db, term_id):
    """{(course, Canvas assignment): the copy's plan key} from the latest link of each of the copy's assignments."""
    found = {}
    for row in db.execute("""SELECT course_id,plan_key,canvas_id FROM canvas_content_links l WHERE term_id=? AND kind='assignment'
            AND rowid=(SELECT x.rowid FROM canvas_content_links x WHERE x.term_id=l.term_id AND x.course_id=l.course_id AND x.kind=l.kind
            AND x.plan_key=l.plan_key ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1)""", (term_id,)):
        if row["canvas_id"]:
            found[(str(row["course_id"]), str(row["canvas_id"]))] = row["plan_key"]
    return found


def stands_for(content, item, known=None, copy_canvas=None):
    """The copy's assignment that stands for the column: its link, its name (when that name stands for nothing else), or the
    Canvas assignment Faculty knows of it (the copy may call it otherwise: "Exam I Grade" for the column "Exam 1")."""
    found = existing(content, item.get("assignment_id"), item["id"], item["title"])
    if found or not known or not copy_canvas:
        return found
    keys = {copy_canvas.get((course, aid)) for course, aid in (known.get(item["id"]) or {}).items()} - {None}
    return next((a for a in (content or {}).get("assignments", []) if a["id"] in keys), None)


def wanted(setup, content, known):
    """The columns `adopt` gives an assignment of the copy, in the setup's order: [(column, the copy's unlinked assignment of
    its name or None)]. Pure."""
    categories = {c["id"]: c["name"] for c in setup.get("categories", [])}
    out = []
    for item in setup.get("items", []):
        if not _counted(item) or item.get("canvas") in CANVAS_OWNED or item["id"] in known:
            continue
        if not categories.get(item.get("category_id") or "", "").strip():
            continue
        found = existing(content, item.get("assignment_id"), item["id"], item["title"])
        if found and found.get("gradebook_item") == item["id"]:
            continue
        if found and found.get("gradebook_item") not in (None, item["id"]):
            found = None  # it stands for another column: never taken over (`adopt` then leaves a clashing name alone)
        out.append((item, found))
    return out


def grading_links(db, term_id, setup, content, graded):
    """GRADED IN FACULTY, LINKED (COLUMN ASSIGNMENTS; requirement, 2026-11-07): {column: (Faculty assignment, parts)} for
    each live column that references no assignment, is not Canvas's own nor kept off Canvas, and holds no grades, whose assignment
    in the copy (`stands_for`: its link, its name, or the Canvas assignment Faculty knows of it) stands for one of this course's
    Faculty assignments that no live column references, when no other such column stands for it too. `graded`: the course's
    assignment IDs, each with the parts its grading in this term has (`gradebook.sources.available`; none: FRQ, as Link does).
    Catalog reads only."""
    if not content or not setup.get("items"):
        return {}
    live = [item for item in setup["items"] if not item.get("archived")]
    referenced = {item["assignment_id"] for item in live if item.get("assignment_id")}
    candidates = [item for item in live if not item.get("assignment_id") and not item.get("link")
                  and item.get("canvas") not in (*CANVAS_OWNED, "off")]
    if not candidates:
        return {}
    known, copy_canvas = _known(db, term_id), _copy_canvas(db, term_id)
    scored = {row[0] for row in db.execute("SELECT DISTINCT item_id FROM gradebook_scores WHERE term_id=?", (term_id,))}
    wanted = {}
    for item in candidates:
        if item["id"] in scored:
            continue  # its grades are someone's: Certify's "use" choice is the instructor's
        entry = stands_for(content, item, known, copy_canvas) or {}
        faculty = entry.get("faculty_assignment")
        if faculty and faculty in graded and faculty not in referenced and entry.get("gradebook_item") in (None, item["id"]):
            wanted.setdefault(faculty, []).append(item["id"])
    return {items[0]: (faculty, list(graded[faculty]) or ["frq"]) for faculty, items in wanted.items() if len(items) == 1}


def entry_for(item, group):
    """A new assignment of the copy for a column (see the module docstring)."""
    due = campus_instant(item["due_date"], DEFAULT_CLOCK) if item.get("due_date") else None
    return {"ref": f"column-{item['id']}"[:64], "name": item["title"], "description": "", "points": str(item["points"]),
            "grading_type": "points", "submission_types": ["none"], "external": False, "group": group, "due_at": due, "section_due": {},
            "unlock_at": None, "lock_at": None, "published": True, "omit_from_final_grade": False, "gradebook_item": item["id"],
            "faculty_assignment": item.get("assignment_id"), "sections": list(item.get("sections") or [])}


def overview(db, term_id, content):
    """What the Assignments tab shows beside the copy: `canvas_only` [{item_id, title, group, points, due_date, kind}] (columns
    whose assignment Canvas holds and the copy does not keep: kind "quiz", "webassign" or "canvas") and `ungrouped` [{item_id,
    title}] (columns without a category). Faculty's records only."""
    setup = _setup(db, term_id)
    if not setup.get("items"):
        return {"canvas_only": [], "ungrouped": []}
    categories = {c["id"]: c["name"] for c in setup.get("categories", [])}
    known, copy_canvas = _known(db, term_id), _copy_canvas(db, term_id)
    canvas_only, ungrouped = [], []
    for item in setup["items"]:
        if not _counted(item):
            continue
        group = categories.get(item.get("category_id") or "", "").strip()
        if not group:
            ungrouped.append({"item_id": item["id"], "title": item["title"]})
            continue
        if item.get("canvas") not in CANVAS_OWNED and item["id"] not in known:
            continue  # Faculty's: `adopt` gives it the copy's assignment
        if stands_for(content, item, known, copy_canvas):
            continue
        canvas_only.append({"item_id": item["id"], "title": item["title"], "group": group, "points": str(item["points"]),
                            "due_date": item.get("due_date") or "", "kind": item.get("canvas") if item.get("canvas") in CANVAS_OWNED else "canvas"})
    return {"canvas_only": canvas_only, "ungrouped": ungrouped}


def uncovered(content, known, copy_canvas, course_sections):
    """SECTIONS FOLLOW THE COLUMN (requirement, 2026-10-17): {the copy's assignment key: the sections to add}, for an assignment of the copy limited to some sections whose own
    link in a course outside them is the Canvas assignment Faculty knows as its column's there. Pure."""
    keys = {(course, aid): key for (course, aid), key in copy_canvas.items()}
    out = {}
    for a in (content or {}).get("assignments", []):
        if not a.get("sections") or not a.get("gradebook_item"):
            continue
        for course, aid in (known.get(a["gradebook_item"]) or {}).items():
            missing = [s for s in course_sections.get(course, []) if s not in a["sections"]]
            if missing and keys.get((course, aid)) == a["id"]:
                out.setdefault(a["id"], []).extend(missing)
    return out


class ColumnAssignments:
    def __init__(self, service):
        self.s = service  # CanvasContentService

    def adopt(self, term_id, actor="workspace"):
        """Gives every Faculty column without one its assignment in the copy (the module docstring): one revision of the copy, or
        none when nothing is missing or the term has no copy yet. Returns the titles given one."""
        with self.s.rosters.lock():
            with connect(self.s.path) as db:
                term = self.s._term(db, term_id)
                content, revision, _ = self.s._latest(db, term["id"])
                if content is None:
                    return []
                setup = _setup(db, term["id"])
                known = _known(db, term["id"])
                missing = wanted(setup, content, known)
                targets, _ = self.s._targets(db, term["id"])
                widen = uncovered(content, known, _copy_canvas(db, term["id"]),
                                  {str(t["course_id"]): [x["id"] for x in t["sections"]] for t in targets})
            if not missing and not widen:
                return []
            categories = {c["id"]: c["name"].strip() for c in setup.get("categories", [])}
            draft = copy.deepcopy(content)
            names = {model.fold(a["name"]) for a in draft["assignments"]}
            given = []
            for item, found in missing:
                if found is not None:
                    target = next(a for a in draft["assignments"] if a["id"] == found["id"])
                    target["gradebook_item"] = item["id"]
                    target["faculty_assignment"] = target.get("faculty_assignment") or item.get("assignment_id")
                elif model.fold(item["title"]) in names:
                    log.info("column %s has the name of another column's assignment of the copy: left for the instructor", item["id"])
                    continue
                else:
                    draft["assignments"].append(entry_for(item, categories[item["category_id"]]))
                    names.add(model.fold(item["title"]))
                given.append(item["title"])
            for key, sections in widen.items():
                target = next(a for a in draft["assignments"] if a["id"] == key)
                target["sections"] = sorted({*target["sections"], *sections})
            if given or widen:
                self.s.save(term["id"], {"base_revision": revision, "request_id": f"columns:{uuid.uuid4().hex}", "content": draft}, actor=actor)
            return given


def adopt_quietly(service, term_id, actor="workspace"):
    """`adopt` after a gradebook write: a failure is logged and never fails the write that made the column (the next one tries
    again)."""
    try:
        return service.columns.adopt(term_id, actor)
    except Exception:  # noqa: BLE001 - the column is kept either way; its assignment follows with the next write
        log.exception("could not give the gradebook's new columns their assignments in Faculty's copy (term %s)", term_id)
        return []


def created_link(service, term_id, course_id, key, row, groups, actor="workspace"):
    """Records that the copy's assignment `key` is Canvas's `row` in this course, made by Sync with Canvas from the copy: a link
    with the snapshot a push would have recorded, so the next push finds it in step (matched, not new)."""
    with service.rosters.lock(), connect(service.path) as db:
        db.execute("BEGIN IMMEDIATE")
        content, _, _ = service._latest(db, term_id)
        entry = next((a for a in (content or {}).get("assignments", []) if a["id"] == key), None)
        if entry is None:
            return False
        service._link_row(db, term_id, course_id, "assignment", key, row["id"], "matched",
                          model.snapshot(model.assignment_plan_state(entry), model.assignment_canvas_state(row, groups)), actor=actor)
    adopt_quietly(service, term_id, actor)  # SECTIONS FOLLOW THE COLUMN: the copy's assignment now covers this course's sections
    return True


def published_by_sync(service, term_id, course_id, before, row, groups, send_id, actor="workspace"):
    """PUBLISH FROM SYNC (docs/decisions/canvas.md SYNC WITH CANVAS; requirement, 2026-10-07): Sync with Canvas published Canvas's
    assignment `row` in this course (`before`: as Sync read it just before; `groups` the course's {group id: name}). When it is an
    assignment of Faculty's copy (the copy's latest link in this course), the copy's assignment is published too, a revision of
    the copy (normalized like any save, so its module items follow), and every course's push then keeps it published. Where the
    link was in step (the copy as last pushed, but unpublished, and Canvas as last seen), a new link records the published state,
    so the next push finds it in step; otherwise the push shows the difference as it would have. Canvas is not contacted.
    Returns the copy's key, or None when the copy does not hold it."""
    course_id = str(course_id)
    with service.rosters.lock(), connect(service.path) as db:
        db.execute("BEGIN IMMEDIATE")
        key = _copy_canvas(db, term_id).get((course_id, str(row["id"])))
        content, latest, _ = service._latest(db, term_id)
        entry = next((a for a in (content or {}).get("assignments", []) if a["id"] == key), None) if key else None
        if entry is None:
            return None
        if entry.get("published") is not True:
            draft = copy.deepcopy(content)
            next(a for a in draft["assignments"] if a["id"] == key)["published"] = True
            content = model.normalize(draft, content, sections=service._sections(db, term_id))
            service._record(db, term_id, latest, content, f"sync-publish:{send_id}:{key}"[:120], {"publish": key, "send": send_id}, actor)
            entry = next(a for a in content["assignments"] if a["id"] == key)
        link = service._links(db, term_id).get(course_id, {}).get(("assignment", key))
        snap = json.loads(link["snapshot_json"]) if link else {}
        unpublished = model.assignment_plan_state({**entry, "published": False})
        if snap.get("sent") == model.digest(unpublished) and snap.get("canvas") == model.digest(model.assignment_canvas_state(before, groups)):
            service._link_row(db, term_id, course_id, "assignment", key, row["id"], "matched",
                              model.snapshot(model.assignment_plan_state(entry), model.assignment_canvas_state(row, groups)), actor=actor)
    return key
