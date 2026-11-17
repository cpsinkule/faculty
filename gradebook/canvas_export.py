"""Sync with Canvas, To Canvas (docs/decisions/canvas.md SYNC WITH CANVAS; requirement, 2026-10-06). Pure: the gradebook service reads Canvas and Faculty and records; this
module plans what each Canvas assignment of a column FACULTY OWNS receives, and which grades wait for the instructor.

Faculty owns every column except one Canvas grades (`canvas` "webassign": WebAssign's through Canvas; "quiz": a Canvas quiz; the
From-Canvas half's, `canvas_import.plan(sync=...)`) and one the instructor keeps off Canvas (`canvas` "off"). Columns Faculty grades itself (linked
to grading) are Faculty's too: their grades go to Canvas.

Where a column goes, per Canvas course: the Canvas assignment an import or a sync matched it to (`gradebook_canvas_links`, the
latest per assignment, and the assignments a sync created), else the assignment Faculty's Canvas copy made for it (an assignment
of the copy naming the column, pushed to that course). Two assignments for one column in one course are never guessed between,
and one assignment takes one column's grades.

Each student is found through their permanent membership: the Canvas course linked to THEIR section, the student there whose SIS
user ID is their nine-digit UIN exactly. Dropped students are never sent.

What Faculty sends: a scored cell its points, an excused cell "excused", a missing cell 0 (Canvas's bulk update has no
"missing"), an empty cell Faculty counts as 0 a 0 (below), any other empty cell nothing, or, where Canvas still shows what Faculty
last knew there, the grade taken out.

ZEROS GO TO CANVAS (requirement, 2026-10-09). `zeros`: the empty cells the gradebook counts
as 0 (gradebook.md ZERO ONCE DUE: the grid's muted 0s; a cell that waits for a makeup, a DR booking or a paper never is one). Such a
cell's value is a 0 (`ZERO`, marked `zero` so its receipt says why), compared and sent by THE RULE like any value: Canvas's blank
takes it, a grade Canvas holds that Faculty knew is replaced by it, a grade Faculty never knew is still filled into the cell (a
column Faculty does not grade) or held. Once the cell stops counting (a makeup owed, a later due date), Canvas's 0 is a grade
Faculty knew there, so the next sync takes it out (`agreed`: at a sync that left it in step, an empty cell whose latest accepted
send was such a 0 stood at that 0). A column Canvas grades (WebAssign's, a Canvas quiz's) gets ONLY these 0s, and
only where Canvas shows no grade at all (`zeros_only` on its plan entries): Canvas's own grades always stand there, and the
From-Canvas half reads the 0 back into the cell like any Canvas grade. Such a column is never created in Canvas or published.

THE RULE, per student (requirement, 2026-10-06: a grade changed in Canvas since the last sync is HELD, the rest syncs). Canvas's
grade is read as entered, before its late penalty (what Faculty sends), rounded to four decimals like an import; the score
after the penalty counts as the same grade. Then:
- Canvas shows Faculty's value: same.
- Canvas shows what Faculty last KNEW there (`agreed`): the latest of what Canvas showed after Faculty's accepted send, a value an
  import from Canvas wrote into the cell, and the cell's value at the last sync that left this column and course in step (with
  the value of a later uncertain send beside it): nobody changed it in Canvas, so Faculty's value goes (add, update, or clear when
  the cell is empty). Where Faculty knew nothing, Canvas's blank is what it knew.
- Anything else was changed in Canvas. Into a cell Faculty never filled (no value, or emptied by an import) of a column Faculty
  does not grade itself, Canvas's grade is FILLED (requirement, 2026-10-06). Otherwise it is HELD, sent only when the instructor picks Keep Faculty's, or taken into Faculty
  when they pick Take Canvas's (not for a column Faculty grades itself: change it in grading).
- A submission waiting for review in Canvas is left alone.
- Canvas showing, after Faculty sent this very value, the same grade to the hundredth (its own form of it) is the same grade.

Nothing is sent to an assignment that is not graded in points or is worth other points than the column; a course the instructor
did not tick this time is planned and shown but not sent.

AN UNPUBLISHED ASSIGNMENT IS PUBLISHED FIRST (requirement, 2026-10-07): Sync's plan (`publishing`) marks such a column `publish` instead of
naming a problem, and its summary offers it a tick; `choose` sends it only when ticked (`publish`), and its batch then carries
`publish`, so the send publishes the assignment before its grades. Any other plan (one student's cell, `canvas_cell`) still names
the problem: only Sync publishes.

A COLUMN WITHOUT A CANVAS ASSIGNMENT IS CREATED THERE (requirement, 2026-10-06): where its category names
exactly one assignment group of the course and Canvas has no assignment of its title, the sync creates it (the column's title and
points, published, no submission, in that group) once the column has grades to send, records it (`gradebook_canvas_created`) and
sends its values. A column Faculty's Canvas copy names is made from the copy (docs/decisions/canvas.md COLUMN ASSIGNMENTS,
requirement, 2026-10-06: a new column's assignment is in the copy right away, and the next Sync makes it in Canvas): when the copy's
assignment is one the sync can make exactly as the copy has it (`from_copy`: published, points, no tool, no description, one due
date), it is created with the copy's group, dates and submission, and the copy's link is recorded as a push would; any other is
left to the copy's push.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from canonical_json import ascii_digest

from .canvas_import import NINE, WAITING
from .common import PLACES
from .iclicker_import import CATEGORY_NAMES

RULE = "gradebook-canvas-sync-v1"
ACTIONS = ("add", "update", "clear", "same", "held", "fill")
COUNTS = (*ACTIONS, "waiting", "empty", "not_in_canvas", "dropped", "not_applicable", "blocked")


def digest(value):
    return ascii_digest(value)


def _decimal(text):
    try:
        return Decimal(str(text))
    except (InvalidOperation, ValueError):
        return None


ZERO = {"status": "scored", "points": "0", "zero": True}  # an empty cell the gradebook counts as 0 (ZEROS GO TO CANVAS)


def faculty_value(cell):
    """What a cell sends: {"status": "scored", "points"} or {"status": "excused"}; None for an empty cell."""
    if not cell or cell["status"] == "cleared":
        return None
    if cell["status"] == "excused":
        return {"status": "excused", "points": None}
    if cell["status"] == "missing":
        return {"status": "scored", "points": "0"}
    return {"status": "scored", "points": cell["points"]}


def canvas_value(submission, entered=False):
    """What Canvas shows for a student: excused, a score (rounded half up to four decimals, as an import records it), or None.
    `entered` reads the grade as entered, before Canvas's late penalty."""
    if not submission:
        return None
    if submission.get("excused"):
        return {"status": "excused", "points": None}
    score = submission.get("entered_score") if entered and submission.get("entered_score") is not None else submission.get("score")
    if score is None:
        return None
    number = _decimal(score)
    if number is None or not number.is_finite():
        return {"status": "scored", "points": str(score)}
    return {"status": "scored", "points": _text(number.quantize(PLACES, ROUND_HALF_UP))}


def shows(submission):
    """The forms of what Canvas shows: the grade as entered, then the score after its late penalty when that differs."""
    found = [canvas_value(submission, entered=True)]
    other = canvas_value(submission)
    if not same(other, found[0]):
        found.append(other)
    return found


def _text(value):
    number = _decimal(value)
    if number is None:
        return str(value)
    text = f"{number:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def assignment_summary(target):
    """The assignment a plan shows for its column (Sync's, and the quiz grade's)."""
    return {"id": target["id"], "name": target["name"], "points": _text(target["points"]) if target["points"] is not None else None,
            "published": target["published"], "automatic": not target["post_manually"]}


def points_problems(target, item):
    """Why `target` (an assignment Canvas has) cannot take the column's grades in points, in the order a plan lists them (Sync's, and
    the quiz grade's): not graded in points, not worth the column's points."""
    found = []
    if target["grading_type"] in ("pass_fail", "not_graded"):
        found.append(f"“{target['name']}” is not graded in points in Canvas")
    if target["points"] is None or _decimal(target["points"]) != _decimal(item["points"]):
        found.append(f"“{target['name']}” is worth {_text(target['points']) if target['points'] is not None else 'no'} points "
                     f"in Canvas and {_text(item['points'])} in the gradebook")
    return found


def same(a, b):
    if a is None or b is None:
        return a is b
    if a["status"] != b["status"]:
        return False
    return a["status"] == "excused" or _decimal(a["points"]) == _decimal(b["points"])


def close(a, b):
    """Two scores alike to the hundredth (Canvas's own form of a grade Faculty sent with more decimals)."""
    if not a or not b or a["status"] != "scored" or b["status"] != "scored":
        return False
    x, y = _decimal(a["points"]), _decimal(b["points"])
    return x is not None and y is not None and abs(x - y) < Decimal("0.005")


def agreed(revisions, receipts, synced_at):
    """What Canvas may show without anyone having changed it there since Faculty last knew it (THE RULE above). `revisions` the
    cell's revisions in order [{"value": faculty_value, "at", "canvas": written by an import from Canvas}]; `receipts` Faculty's
    sends to this assignment and student in order [{"sent", "canvas", "state", "at"}]; `synced_at` the time of the last sync that
    left this column and course in step (or None). Returns a list of values (None: Canvas's blank)."""
    events = []
    for revision in revisions:
        if revision["canvas"]:
            events.append((revision["at"], revision["value"]))
    for receipt in receipts:
        if receipt["state"] == "accepted":
            events.append((receipt["at"], receipt["canvas"]))
    if synced_at and not any(receipt["at"] >= synced_at for receipt in receipts):
        # The cell as it stood at that sync, unless the sync sent it (its receipts then say what Canvas holds).
        before = [revision for revision in revisions if revision["at"] <= synced_at]
        value = before[-1]["value"] if before else None
        accepted = [receipt for receipt in receipts if receipt["state"] == "accepted"]
        if value is None and accepted and (accepted[-1]["sent"] or {}).get("zero"):
            value = accepted[-1]["canvas"]  # ZEROS GO TO CANVAS: an empty cell stood at the 0 Faculty counted and sent, nothing since
        events.append((synced_at, value))
    if not events:
        return [None]
    at, value = max(events, key=lambda event: event[0])
    found = [value]
    for receipt in receipts:  # a later send whose outcome Faculty could not read: Canvas may show either
        if receipt["state"] == "uncertain" and receipt["at"] >= at:
            found.append(receipt["sent"])
    return found


def targets(setup, links, canvas, remembered, copied):
    """{(item id, course id): assignment row or {"problem": ...}}. `remembered` {"<course>:<assignment>": {"item_id"}} (imports,
    syncs, and the assignments a sync created); `copied` {course id: {item id: assignment id}} (Faculty's Canvas copy)."""
    found = {}
    by_course = {}
    for key, value in remembered.items():
        course, assignment = key.split(":")
        if value.get("item_id"):
            by_course.setdefault(course, {}).setdefault(value["item_id"], set()).add(assignment)
    courses = {link["course_id"] for link in links.values()}
    for course in courses:
        rows = {a["id"]: a for a in canvas[course]["assignments"]}
        chosen = {item: {a for a in ids if a in rows} for item, ids in by_course.get(course, {}).items()}
        for item, assignment in (copied.get(course) or {}).items():
            if assignment in rows and not chosen.get(item):
                chosen[item] = {assignment}
        # One Canvas assignment holds one column's grades. An import's match and the copy's link can name two columns for one
        # assignment, and the later send would put one column's values over the other's (2026-10-04 review): neither is sent.
        live = [item for item in setup.get("items", []) if not item.get("archived")]
        takers = {}
        for item in live:
            for assignment in chosen.get(item["id"]) or ():
                takers.setdefault(assignment, []).append(item)
        for item in setup.get("items", []):
            ids = chosen.get(item["id"]) or set()
            shared = sorted({other["title"] for a in ids for other in takers.get(a, []) if other["id"] != item["id"]})
            if len(ids) > 1:
                names = ", ".join(sorted(f"“{rows[a]['name']}”" for a in ids))
                found[(item["id"], course)] = {"problem": f"it is matched to more than one Canvas assignment here ({names})"}
            elif shared and not item.get("archived"):
                found[(item["id"], course)] = {"problem": f"its Canvas assignment “{rows[next(iter(ids))]['name']}” also takes the column "
                                               f"{', '.join(f'“{t}”' for t in shared)} here, and one assignment holds one column's grades: "
                                               "link one of them to another assignment (the copy's assignment in Canvas content)"}
            elif ids:
                found[(item["id"], course)] = rows[next(iter(ids))]
    return found


def fold(text):
    return " ".join(str(text or "").split()).casefold()


def from_copy(entry, item):
    """Whether the sync may create the copy's assignment for this column itself, exactly as the copy has it: published, graded in
    points with the column's points, Canvas's own (no outside tool), no description and one due date for every section. Anything
    more is the push's to make (it renders descriptions and per-section dates for each course)."""
    if not entry:
        return False
    try:
        same_points = _decimal(entry.get("points")) == _decimal(item.get("points"))
    except (InvalidOperation, TypeError, ValueError):
        same_points = False
    return (entry.get("published") is True and not entry.get("external") and entry.get("grading_type") == "points" and same_points
            and not (entry.get("description") or "").strip() and not entry.get("section_due") and bool((entry.get("group") or "").strip()))


def creatable(item, course, canvas, categories, entry=None):
    """What creating the column's Canvas assignment in this course would make ({name, points, group, group_id}, with `copy` when
    made from the copy's assignment `entry`), or why it cannot be made there ({"problem"})."""
    category = (entry or {}).get("group") or categories.get(item.get("category_id") or "")
    if not category:
        return {"problem": "put the column in a category first: its Canvas assignment goes in the Assignment Group of that name"}
    named = canvas[course].get("groups") or {}
    groups = [gid for gid, name in named.items() if fold(name) == fold(category)]
    if not groups and fold(category) in CATEGORY_NAMES:
        # Checks for Understanding goes by either name, "Category" or not: import accepts both, so the Canvas group may have the
        # other one (docs/decisions/iclicker.md; 2026-10-01 audit).
        groups = [gid for gid, name in named.items() if fold(name) in CATEGORY_NAMES]
    if len(groups) != 1:
        return {"problem": f"Canvas has {'no' if not groups else 'more than one'} Assignment Group named “{category}” in this course"}
    if any(fold(a["name"]) == fold(item["title"]) for a in canvas[course]["assignments"]):
        return {"problem": f"Canvas already has an assignment named “{item['title']}” here: choose it for the column in Sync with Canvas"}
    group = category if fold(named[groups[0]]) == fold(category) else named[groups[0]]
    made = {"name": item["title"], "points": _text(item["points"]), "group": group, "group_id": groups[0]}
    if entry:
        made["name"] = entry["name"]
        made["copy"] = {"key": entry["id"], "submission_types": entry.get("submission_types") or ["none"],
                        **{k: entry.get(k) for k in ("due_at", "unlock_at", "lock_at")}, "omit_from_final_grade": bool(entry.get("omit_from_final_grade"))}
    return made


def owned(item):
    """Whether Sync with Canvas sends this column: Faculty's, not archived, not Canvas's (WebAssign's, a Canvas quiz's), not kept off
    Canvas."""
    return not item.get("archived") and item.get("canvas") not in ("off", "webassign", "quiz")


def canvas_grades(item):
    """A live column Canvas grades (WebAssign's, a Canvas quiz's): Sync sends it only the 0s Faculty counts (ZEROS GO TO CANVAS)."""
    return not item.get("archived") and item.get("canvas") in ("webassign", "quiz")


def plan(*, setup, cells, students, links, canvas, remembered, copied, revisions, receipts, synced, courses=None, pending=frozenset(), item_id=None,
         publishing=False, automatic=False, zeros=frozenset()):
    """The To-Canvas plan. `students` [{row, section_id, section, name, uin, dropped}]; `links` {section_id: {course_id, name}};
    `canvas` {course: {"assignments", "groups" {id: name}, "students", "submissions" {(assignment, user): submission}, "read"}}
    (`CanvasService.read_for_send`); `cells` the cells as they will be after the From-Canvas half; `revisions` {(item, row): the
    cell's revisions for `agreed`}; `receipts` {(course, assignment, user): Faculty's sends there, in order}; `synced` {(item,
    course): the time of the last sync that left them in step}; `courses` the courses to send to this time (None: all); `pending`
    the columns Faculty's Canvas copy names but has not pushed yet, {column: the copy's assignment} (never created twice: the
    sync makes it from the copy when `from_copy`, else the push does). The plan's `held` lists the grades changed in Canvas;
    `choose` turns the instructor's picks into what is sent and taken. `item_id`: that column alone (one student's cell from a
    regrade request's Apply, `canvas_cell`); `setup` stays whole, for `targets`. `publishing`: Sync's plan, where an unpublished
    assignment is marked `publish` (published first when ticked) rather than a problem. `automatic`: the morning sync's plan
    (NIGHTLY SYNC), which never sends again by itself a value whose latest send Faculty could not read back (AGENTS.md OUTSIDE
    SERVICES): such a value is counted `unsure` on its column and waits for the instructor's own Sync. `zeros` {(item, row)}: the
    empty cells the gradebook counts as 0, sent as 0 (ZEROS GO TO CANVAS)."""
    categories = {c["id"]: c["name"] for c in setup.get("categories", [])}
    where = targets(setup, links, canvas, remembered, copied)
    users = {}
    for course, read in canvas.items():
        users[course] = {}
        for enrollment in read["students"]:
            if enrollment["state"] in ("active", "invited") and NINE.fullmatch(enrollment["sis"] or ""):
                users[course].setdefault(enrollment["sis"], enrollment["user_id"])
    sections_of = {}
    for section_id, link in links.items():
        sections_of.setdefault(link["course_id"], set()).add(section_id)
    columns, held = [], []
    zeroed = {zero_item for zero_item, _ in zeros}
    for item in setup.get("items", []):
        if item_id is not None and item["id"] != item_id:
            continue
        only_zeros = not owned(item)  # Canvas's column: only Faculty's counted 0s go, into Canvas's blanks
        if only_zeros and not (canvas_grades(item) and item["id"] in zeroed):
            continue
        compare = (item.get("link") or {}).get("kind") == "grading"
        for section in sorted({s["section_id"] for s in students}):
            link = links.get(section)
            if not link:
                continue
            course = link["course_id"]
            key = f"{item['id']}:{course}"
            if any(c["key"] == key for c in columns):
                continue  # one entry per column and course (a course several sections link)
            if item.get("sections") and not set(item["sections"]) & sections_of.get(course, set()):
                continue  # the column is for other sections' students only
            course_students = [s for s in students if links.get(s["section_id"], {}).get("course_id") == course]
            ticked = courses is None or course in courses
            entry = {"key": key, "item_id": item["id"], "title": item["title"], "course_id": course, "course": link.get("name", course),
                     "sections": sorted({s["section"] for s in course_students}), "assignment": None, "problems": [], "notes": [],
                     "counts": {a: 0 for a in COUNTS}, "values": [], "held": [], "fills": [], "create": None, "ticked": ticked, "compare": compare,
                     **({"unsure": 0} if automatic else {}), **({"zeros_only": True} if only_zeros else {})}
            target = where.get((item["id"], course))
            if only_zeros and target is None:
                continue  # never created: the column has no assignment in this course
            if target is None:
                copied_entry = pending.get(item["id"]) if isinstance(pending, dict) else None
                makes = from_copy(copied_entry, item)
                offer = creatable(item, course, canvas, categories, copied_entry if makes else None)
                entry["create"] = offer
                if item["id"] in pending and not makes:
                    entry["problems"].append("Faculty's Canvas copy has its assignment: push the copy (Canvas content) to make it there")
                elif "problem" in offer:
                    entry["problems"].append(f"it has no Canvas assignment in this course, and Faculty cannot create one: {offer['problem']}")
                else:
                    entry["assignment"] = {"id": None, "name": offer["name"], "points": offer["points"], "published": True, "automatic": True,
                                           "new": True, "group": offer["group"]}
                    how = "as Faculty's copy has it" if makes else "no submission"
                    entry["notes"].append(f"Faculty creates “{offer['name']}” in Canvas first (Assignment Group “{offer['group']}”, {offer['points']} points, "
                                          f"published, {how}); students may see its grades as soon as they are sent")
            elif "problem" in target:
                entry["problems"].append(target["problem"])
            else:
                entry["assignment"] = assignment_summary(target)
                if not target["published"] and publishing and not only_zeros:
                    entry["publish"] = True  # published first when the instructor ticks it (`choose`)
                    entry["notes"].append(f"“{target['name']}” is not published in Canvas: ticked under Publish in Canvas, Faculty publishes it "
                                          "first (students then see the assignment), then sends its grades")
                elif not target["published"]:
                    entry["problems"].append(f"“{target['name']}” is not published in Canvas, so it cannot take grades")
                entry["problems"].extend(points_problems(target, item))
                if "read" in canvas[course] and target["id"] not in canvas[course]["read"]:
                    entry["problems"].append("its Canvas grades were not read (the column was linked while Faculty read Canvas): sync again")
                if not target["post_manually"]:
                    entry["notes"].append("Canvas posts this assignment's grades automatically: students see them as soon as they are sent")
            for student in course_students:
                if item.get("sections") and student["section_id"] not in item["sections"]:
                    entry["counts"]["not_applicable"] += 1
                    continue
                if student["dropped"]:
                    entry["counts"]["dropped"] += 1
                    continue
                cell = (cells.get(item["id"]) or {}).get(student["row"])
                value = faculty_value(cell)
                zero = value is None and (item["id"], student["row"]) in zeros
                if only_zeros and not zero:
                    continue
                if zero:
                    value = dict(ZERO)
                user = users.get(course, {}).get(student.get("uin") or "")
                if not user:
                    entry["counts"]["not_in_canvas" if value is not None and not zero else "empty"] += 1
                    continue
                if entry["assignment"] is None:
                    entry["counts"]["blocked"] += value is not None  # a grade with nowhere to go (the column's problem says why)
                    continue
                new = entry["assignment"].get("new")
                submission = None if new else canvas[course]["submissions"].get((target["id"], user))
                if (submission or {}).get("state") in WAITING:
                    entry["counts"]["waiting"] += 1
                    continue
                now = shows(submission)
                history = [] if new else receipts.get((course, target["id"], user), [])
                last = history[-1] if history else None
                if only_zeros and now[0] is not None:
                    continue  # Canvas grades this column: its grade stands, and the From-Canvas half reads it
                if only_zeros:
                    action = "add"
                elif any(same(value, form) for form in now):
                    action = "same"
                elif last and same(last["sent"], value) and any(close(value, form) for form in now):
                    action = "same"  # Canvas keeps its own form of what Faculty sent
                elif any(same(known, form) for known in agreed(revisions.get((item["id"], student["row"]), []), history,
                                                                synced.get((item["id"], course))) for form in now):
                    action = "add" if now[0] is None else "clear" if value is None else "update"
                elif (value is None or zero) and not compare and (cell is None or (cell["status"] == "cleared" and cell.get("source") != "instructor")):
                    action = "fill"  # Faculty never filled it: Canvas's grade comes in
                else:
                    action = "held"
                if action == "same" and value is None:
                    entry["counts"]["empty"] += 1
                    continue
                if automatic and action in ("add", "update", "clear") and last and last["state"] == "uncertain":
                    entry["unsure"] += 1  # its last send was never read back: the instructor's own Sync decides
                    continue
                entry["counts"][action] += 1
                sent = {"row": student["row"], "section_id": student["section_id"], "user": user, "value": value,
                        "score_ref": cell.get("id") if cell else None, "action": action,
                        **({"unexcuse": True} if (now[0] or {}).get("status") == "excused" and (value or {}).get("status") != "excused" else {})}
                if action in ("add", "update", "clear"):
                    entry["values"].append(sent)
                elif action == "fill":
                    entry["fills"].append({"key": f"{key}:{student['row']}", "item_id": item["id"], "row": student["row"], "value": now[0]})
                elif action == "held":
                    entry["held"].append({**sent, "key": f"{key}:{student['row']}", "item_id": item["id"], "course_id": course,
                                          "title": item["title"], "name": student["name"], "section": student["section"],
                                          "canvas": now[0], "faculty": value, "take": not compare})
            if only_zeros and not (entry["values"] or entry["counts"]["blocked"] or entry.get("unsure")):
                continue  # nothing of Faculty's to send here
            columns.append(entry)
    for entry in columns:
        entry["send"] = entry["ticked"] and not entry["problems"] and entry["assignment"] is not None
        if entry["send"]:
            held.extend(entry["held"])
    fingerprint = digest({"rule": RULE, "courses": None if courses is None else sorted(courses), **({"item": item_id} if item_id is not None else {}),
                          **({"automatic": True} if automatic else {}),
                          "columns": [{k: c[k] for k in ("key", "assignment", "problems", "notes", "send", "counts", "create")} |
                                      ({"unsure": c["unsure"]} if automatic else {}) |
                                      {"values": [(v["row"], v["user"], v["value"], v["action"]) for v in c["values"]],
                                       "held": [(h["row"], h["user"], h["value"], h["canvas"]) for h in c["held"]],
                                       "fills": [(f["row"], f["value"]) for f in c["fills"]]} for c in columns]})
    totals = {a: sum(c["counts"][a] for c in columns if c["send"]) for a in COUNTS}
    return {"columns": columns, "held": held, "fingerprint": fingerprint, "totals": totals,
            **({"unsure": sum(c["unsure"] for c in columns if c["send"])} if automatic else {}),
            "filling": sum(len(c["fills"]) for c in columns if not c["problems"]),
            "sending": sum(len(c["values"]) for c in columns if c["send"]),
            "creating": sum(1 for c in columns if c["send"] and c["values"] and c["assignment"].get("new"))}


def goes(entry, publish=()):
    """Whether Sync sends this column of its plan: ticked, nothing in the way, and, when its Canvas assignment is not published,
    ticked under Publish in Canvas (`publish`, column IDs)."""
    return entry["send"] and (not entry.get("publish") or entry["item_id"] in publish)


def choose(found, choices, publish=()):
    """The instructor's picks for held grades ({"<item>:<course>:<row>": "faculty" | "canvas"}): the batches to send (one per
    assignment with something to send) and the values to take into Faculty ([{item_id, row, value, key}], value None: empty): the
    picks, and every cell Faculty never filled that Canvas fills (of a column whose assignment can take its grades). A pick must
    name a held grade of this plan; Take Canvas's is refused for a column Faculty grades itself. `publish`: the columns whose
    unpublished Canvas assignment is published first (any other such column sends nothing; its batch says `publish`)."""
    held = {h["key"]: h for h in found["held"]}
    if set(choices) - set(held):
        raise ValueError("A choice names a grade that is no longer held. Sync again.")
    taken = [fill for entry in found["columns"] if not entry["problems"] for fill in entry["fills"]]
    batches = []
    for entry in found["columns"]:
        if not entry["send"]:
            continue
        left = not goes(entry, publish)  # its assignment stays unpublished: Keep Faculty's picks wait with it
        values = list(entry["values"])
        for h in entry["held"]:
            pick = choices.get(h["key"])
            if pick == "faculty":
                values.append({k: h[k] for k in ("row", "section_id", "user", "value", "score_ref") if k in h}
                              | ({"unexcuse": True} if h.get("unexcuse") else {}) | {"action": "replace"})
            elif pick == "canvas":
                if not h["take"]:
                    raise ValueError(f"“{h['title']}” is graded in Faculty: change {h['name']}'s grade in grading, or keep Faculty's.")
                taken.append({"item_id": h["item_id"], "row": h["row"], "value": h["canvas"], "key": h["key"]})
            elif pick is not None:
                raise ValueError("Each held grade is kept as Faculty has it, taken from Canvas, or left for later.")
        if values and not left:
            assignment = entry["assignment"]
            batches.append({"key": entry["key"], "item_id": entry["item_id"], "title": entry["title"], "course_id": entry["course_id"],
                            "assignment_id": assignment["id"], "values": values,
                            **({"create": {k: entry["create"][k] for k in ("name", "points", "group", "group_id", "copy") if k in entry["create"]}}
                               if assignment.get("new") else {}),
                            **({"publish": {"name": assignment["name"]}} if entry.get("publish") else {})})
    return batches, taken


def grade_data(value):
    """One student's entry of Canvas's bulk update for a planned value."""
    if value["value"] is None:
        return {"posted_grade": "", **({"excuse": False} if value.get("unexcuse") else {})}
    if value["value"]["status"] == "excused":
        return {"excuse": True}
    return {"posted_grade": value["value"]["points"], **({"excuse": False} if value.get("unexcuse") else {})}


def from_read(read):
    """A full read of the courses (`CanvasService.read_grades`: assignment groups, students, every submission) in the shape `plan`
    reads (`CanvasService.read_for_send`), so the summary plans both halves from one read."""
    found = {}
    for course, data in read.items():
        found[course] = {"assignments": [a for group in data["groups"] for a in group["assignments"] if a.get("state") != "deleted"],
                         "groups": {group["id"]: group["name"] for group in data["groups"]}, "students": data["students"],
                         "submissions": {(s["assignment_id"], s["user_id"]): s for s in data["submissions"]}}
    return found


def written(found, batches, taken):
    """{course: {assignment ids}} a sync writes to or takes from: what it reads again before writing."""
    targets = {}
    for batch in batches:
        # An assignment the sync creates: its course's assignments are read again (one made there since is never doubled).
        targets.setdefault(batch["course_id"], set())
        if batch["assignment_id"]:
            targets[batch["course_id"]].add(batch["assignment_id"])
    keys = {t["key"] for t in taken}
    for entry in found["columns"]:
        if entry["assignment"] and entry["assignment"].get("id") and any(f["key"] in keys for f in [*entry["fills"], *entry["held"]]):
            targets.setdefault(entry["course_id"], set()).add(entry["assignment"]["id"])
    return targets


def overlay(read, fresh):
    """The summary's read with what was read again (`read_for_send` of the written assignments) laid over it."""
    found = dict(read)
    for course, data in fresh.items():
        again = set(data["read"])
        found[course] = {**read[course], "assignments": data["assignments"], "groups": data["groups"], "students": data["students"],
                         "submissions": {**{key: value for key, value in read[course]["submissions"].items() if key[0] not in again},
                                         **data["submissions"]}}
    return found


def agrees(value, submission):
    """Whether Canvas, read back, shows the value sent (as entered, or as scored after its late penalty)."""
    return any(same(value, form) for form in shows(submission))
