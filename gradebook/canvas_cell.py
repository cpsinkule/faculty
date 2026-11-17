"""One student's cell of one gradebook column, sent to Canvas (requirement, 2026-10-07).

Beside Sync with Canvas, never instead of it: the To-Canvas half of SYNC WITH CANVAS for ONE column and ONE student (THE RULE, its
`agreed`, held grades and receipts unchanged), with no From-Canvas half, no fill, no take, no column created and no `sync.in_step`
written (it leaves the column and course exactly as in step as they were). It reads ONE Canvas course: its assignments, students and
groups, and the submissions of the column's own assignment.

- `preview(svc, term_id, data)` ({item_id, row}): writes nothing. {action: add|update|clear|same|held|waiting|empty|none, canvas
  (what Canvas shows), faculty (the cell's value), problems, notes, assignment, fingerprint, ready}.
- `send(svc, term_id, data, actor)` ({item_id, row, fingerprint, request_id (8 to 120 characters), confirmation: "send-cell"}):
  registered with `canvas.writes` before Canvas is read (a restart waits for it), refused while any send of the term runs, read again
  (a different fingerprint is a 409: Canvas or the gradebook changed), sent only when the plan is exactly that one value, recorded
  as ONE `gradebook_canvas_sends` row (section_id: the student's section) and sent by the sync's own runner (`_run_send`: a receipt
  before Canvas is asked, settled by reading Canvas back, never sent again by itself). Idempotent by request ID.

A CANVAS QUIZ'S GRADE (requirement, 2026-10-13, whose request to give one student 2/2 on a video quiz the Assistant could only answer
with a card to change Faculty; docs/decisions/canvas.md ONE CELL). A quiz Canvas grades (`canvas` "quiz") stays Canvas's: Faculty's
cell is never typed into, and one student's grade is set in CANVAS, on the quiz's own assignment, where the next Sync with Canvas
reads it back into the cell like any Canvas grade. WebAssign's columns are not changed this way (WebAssign sends its score again).

- `quiz_preview(svc, term_id, data)` ({item_id, row, points}): writes nothing. {action: add|update|same|waiting|none, canvas (what
  Canvas shows), faculty (the cell), points, possible, assignment, problems, notes, fingerprint, ready}.
- `quiz_send(svc, term_id, data, actor)` ({item_id, row, points, fingerprint, request_id, confirmation: "set-quiz-grade"}): the
  same registration, refusals, ONE send row and runner as `send`, for that one value. It WAITS for the receipt (at most QUIZ_WAIT
  seconds) and answers {send, receipt, replay, status: accepted|failed|uncertain}, so what follows it (a reply telling the student)
  never goes ahead of a grade Canvas did not take. Nothing is written to the cell.
"""
import json
import threading
import uuid

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect

from . import canvas_export, common

CONFIRMATION = "send-cell"
SENDING = ("add", "update", "clear")
QUIZ_CONFIRMATION = "set-quiz-grade"
QUIZ_RULE = "gradebook-canvas-quiz-grade-v1"
QUIZ_WAIT = 180  # seconds a quiz grade's answer waits for its receipt (the bulk update's SEND_WAIT, then Canvas read back)


def _parse(data, sending=False):
    if not isinstance(data, dict):
        raise ValueError("Name the column and the student.")
    item_id, row = data.get("item_id"), data.get("row")
    if not isinstance(item_id, str) or not item_id or not isinstance(row, str) or ":" not in row:
        raise ValueError("Name the column and the student.")
    if not sending:
        return item_id, row
    if data.get("confirmation") != CONFIRMATION:
        raise ValueError("Confirm sending this grade to Canvas.")
    request_id, fingerprint = data.get("request_id"), data.get("fingerprint")
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 120:
        raise ValueError("Request ID must be 8 to 120 characters.")
    if not isinstance(fingerprint, str) or not 1 <= len(fingerprint) <= 128:
        raise ValueError("Look at the preview before sending.")
    return item_id, row


def _plan(svc, term_id, item_id, row):
    """The To-Canvas plan of that column for that student, from one read of their Canvas course. Returns (answer, out, student)."""
    from .service import _cells, _setup
    if svc.canvas is None:
        raise ValueError("Canvas is not set up in this workspace.")
    links = svc._source_links("canvas", term_id)
    section_id = common.split_row(row)[0]
    link = links.get(section_id)
    if not link:
        raise ValueError("This student's section has no Canvas course linked.")
    course = link["course_id"]
    links = {s: l for s, l in links.items() if l["course_id"] == course}
    with connect(svc.path) as db:
        setup = _setup(db, term_id)[0]
        remembered = svc._remembered(db, term_id, "canvas", sync=True)
        copied, named = svc._copy_links(db, term_id)
    if not any(item["id"] == item_id for item in setup.get("items", [])):
        raise NotFound("That gradebook column was not found.")

    def wanted(course_id, assignments):
        target = canvas_export.targets(setup, links, {course_id: {"assignments": assignments}}, remembered, copied).get((item_id, course_id)) or {}
        return {target["id"]} if target.get("id") else set()
    read = svc.canvas.read_for_send({course}, wanted)  # no lock held
    with svc.rosters.lock():
        context = svc._context(term_id)
        student = next((s for s in context["students"] if s["row"] == row), None)
        if student is None:
            raise NotFound("That student is not in this term's gradebook.")
        with connect(svc.path) as db:
            setup = _setup(db, term_id)[0]
            out = canvas_export.plan(setup=setup, cells=_cells(db, term_id), students=[{k: student[k] for k in ("row", "section_id", "section", "name", "uin", "dropped")}],
                                     links=links, canvas=read, remembered=svc._remembered(db, term_id, "canvas", sync=True), copied=svc._copy_links(db, term_id)[0],
                                     revisions=svc._revisions(db, term_id), receipts=svc._receipts(db, term_id), synced=svc._synced(db, term_id),
                                     courses=[course], pending=named, item_id=item_id)
    entry = next((c for c in out["columns"] if c["key"] == f"{item_id}:{course}"), None)
    answer = {"item_id": item_id, "row": row, "course_id": course, "course": link.get("name", course), "fingerprint": out["fingerprint"],
              "action": "none", "canvas": None, "faculty": None, "problems": [], "notes": [], "assignment": None, "ready": False}
    if entry is None:
        answer["problems"].append("This column is not sent to Canvas.")
        return answer, out, student
    answer.update(assignment=entry["assignment"], problems=list(entry["problems"]), notes=list(entry["notes"]))
    if (entry["assignment"] or {}).get("new"):
        answer["problems"].append("The column has no Canvas assignment yet: Sync with Canvas makes it.")
    for value in entry["values"]:
        answer.update(action=value["action"], faculty=value["value"])
    for held in entry["held"]:
        answer.update(action="held", faculty=held["faculty"], canvas=held["canvas"])
    if answer["action"] == "none":
        counted = [name for name, count in entry["counts"].items() if count]
        answer["action"] = counted[0] if counted else "none"
    target = entry["assignment"] or {}
    user = next((v["user"] for v in [*entry["values"], *entry["held"]]), None)
    if user is None and target.get("id"):
        user = next((e["user_id"] for e in read[course]["students"] if e.get("sis") == student.get("uin")), None)
    if target.get("id") and user and answer["action"] != "held":
        answer["canvas"] = canvas_export.shows(read[course]["submissions"].get((target["id"], user)))[0]
    if answer["faculty"] is None:
        with connect(svc.path) as db:
            answer["faculty"] = canvas_export.faculty_value((_cells(db, term_id).get(item_id) or {}).get(row))
    answer["ready"] = answer["action"] in SENDING and not answer["problems"] and entry["send"]
    return answer, out, student


def preview(svc, term_id, data):
    item_id, row = _parse(data)
    return _plan(svc, term_id, item_id, row)[0]


def _view(svc, row):
    return svc._send_view(row)


def send(svc, term_id, data, actor="workspace"):
    from .service import STOPPING_SYNC, _latest_id
    item_id, row = _parse(data, sending=True)
    request_id, fingerprint = data["request_id"], data["fingerprint"]
    request_sha = common.digest({"cell": [item_id, row], "fingerprint": fingerprint})
    with connect(svc.path) as db:
        earlier = db.execute("SELECT * FROM gradebook_canvas_sends WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        if earlier:
            if earlier["request_sha256"] != request_sha:
                raise Conflict("That request ID was already used for a different send.")
            return {"send": _view(svc, earlier), "replay": True}
        running = [r["id"] for r in db.execute("SELECT id FROM gradebook_canvas_sends WHERE term_id=? AND status='running'", (term_id,))]
    if any(svc._send_running(r) for r in running):
        raise Conflict("A sync with Canvas is sending grades for this term: send this one when it has finished.")
    send_id = str(uuid.uuid4())
    svc.canvas.writes.begin(svc._send_key(send_id), "a grade sent to Canvas", STOPPING_SYNC)
    started = False
    try:
        answer, out, student = _plan(svc, term_id, item_id, row)
        if answer["fingerprint"] != fingerprint:
            raise Conflict("Canvas or the gradebook changed since the preview. Look at it again.")
        if not answer["ready"]:
            raise Conflict("; ".join(answer["problems"]) or "There is nothing to send to Canvas for this student.")
        batches, _ = canvas_export.choose(out, {})
        if sum(len(b["values"]) for b in batches) != 1:
            raise Conflict("Only this one grade can be sent this way. Use Sync with Canvas.")
        svc._sync_stopping()
        batch = batches[0]
        batch["manual"] = not (answer["assignment"] or {}).get("automatic", True)
        progress = {"total": 1, "accepted": 0, "failed": 0, "uncertain": 0, "assignments": 1, "done_assignments": 0, "current": "", "errors": [], "created": 0}
        with svc.rosters.lock(), connect(svc.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM gradebook_canvas_sends WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone():
                raise Conflict("This send was just started from another window.")
            if db.execute("SELECT 1 FROM gradebook_canvas_sends WHERE term_id=? AND status='running'", (term_id,)).fetchone():
                raise Conflict("A sync with Canvas is sending grades for this term: send this one when it has finished.")
            value = batch["values"][0]
            if _latest_id(db, term_id, batch["item_id"], value["row"]) != value["score_ref"]:
                raise Conflict("The gradebook changed since the preview. Look at it again.")
            db.execute("INSERT INTO gradebook_canvas_sends VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (send_id, term_id, student["section_id"], request_id, request_sha, fingerprint, json.dumps(batches), "running",
                        json.dumps(progress), actor, timestamp(), None))
        threading.Thread(target=svc._run_send, args=(send_id, term_id, batches, progress), name=f"canvas-cell-{send_id[:8]}", daemon=True).start()
        started = True  # `_run_send` ends the write when it finishes
    finally:
        if not started:
            svc.canvas.writes.end(svc._send_key(send_id))
    with connect(svc.path) as db:
        return {"send": _view(svc, db.execute("SELECT * FROM gradebook_canvas_sends WHERE id=?", (send_id,)).fetchone()), "replay": False}


def status(svc, term_id, send_id):
    """The send and the receipt of its one value: {send, receipt: {state, sent, canvas} | None}."""
    with connect(svc.path) as db:
        row = db.execute("SELECT * FROM gradebook_canvas_sends WHERE id=? AND term_id=?", (send_id, term_id)).fetchone()
        if not row:
            raise NotFound("That send was not found.")
        receipt = db.execute("SELECT state,sent_json,canvas_json FROM gradebook_canvas_sent WHERE send_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                             (send_id,)).fetchone()
        view = _view(svc, row)
    return {"send": view, "receipt": {"state": receipt["state"], "sent": json.loads(receipt["sent_json"]), "canvas": json.loads(receipt["canvas_json"])}
            if receipt else None}


# A CANVAS QUIZ'S GRADE ------------------------------------------------------------------------------------------------------------

def _quiz_parse(data, sending=False):
    if not isinstance(data, dict):
        raise ValueError("Name the quiz's column, the student and the grade.")
    item_id, row = _parse({"item_id": data.get("item_id"), "row": data.get("row")})
    points = common.parse_points(data.get("points"), "The grade")
    if sending:
        if data.get("confirmation") != QUIZ_CONFIRMATION:
            raise ValueError("Confirm setting this grade in Canvas.")
        request_id, fingerprint = data.get("request_id"), data.get("fingerprint")
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 120:
            raise ValueError("Request ID must be 8 to 120 characters.")
        if not isinstance(fingerprint, str) or not 1 <= len(fingerprint) <= 128:
            raise ValueError("Look at the preview before setting the grade.")
    return item_id, row, points


def _quiz_plan(svc, term_id, item_id, row, points):
    """What setting that grade on the quiz's Canvas assignment would do, from one read of the student's Canvas course. Returns
    (answer, batch or None, student)."""
    from . import setup as setups
    from .service import _cells, _setup
    if svc.canvas is None:
        raise ValueError("Canvas is not set up in this workspace.")
    links = svc._source_links("canvas", term_id)
    section_id = common.split_row(row)[0]
    link = links.get(section_id)
    if not link:
        raise ValueError("This student's section has no Canvas course linked.")
    course = link["course_id"]
    links = {s: l for s, l in links.items() if l["course_id"] == course}

    def column(db):
        setup = _setup(db, term_id)[0]
        item = setups.find_item(setup, item_id)
        if not item:
            raise NotFound("That gradebook column was not found.")
        if item.get("archived") or item.get("canvas") != "quiz":
            raise ValueError(f"“{item['title']}” is not a quiz Canvas grades: " + (
                "WebAssign grades it, so its score is changed in WebAssign." if item.get("canvas") == "webassign" and not item.get("archived")
                else "change its grade in the gradebook."))
        if canvas_export._decimal(points) > canvas_export._decimal(item["points"]):
            raise ValueError(f"“{item['title']}” is worth {common.points_text(item['points'])} points: the grade cannot be more.")
        return setup, item, svc._remembered(db, term_id, "canvas", sync=True), svc._copy_links(db, term_id)[0]

    with connect(svc.path) as db:
        setup, item, remembered, copied = column(db)

    def wanted(course_id, assignments):
        target = canvas_export.targets(setup, links, {course_id: {"assignments": assignments}}, remembered, copied).get((item_id, course_id)) or {}
        return {target["id"]} if target.get("id") else set()
    read = svc.canvas.read_for_send({course}, wanted)  # no lock held
    with svc.rosters.lock():
        context = svc._context(term_id)
        student = next((s for s in context["students"] if s["row"] == row), None)
        if student is None:
            raise NotFound("That student is not in this term's gradebook.")
        with connect(svc.path) as db:
            setup, item, remembered, copied = column(db)
            cell = (_cells(db, term_id).get(item_id) or {}).get(row)
    target = canvas_export.targets(setup, links, read, remembered, copied).get((item_id, course))
    value = {"status": "scored", "points": points}
    answer = {"item_id": item_id, "row": row, "course_id": course, "course": link.get("name", course), "title": item["title"], "points": points,
              "possible": common.points_text(item["points"]), "action": "none", "canvas": None, "faculty": canvas_export.faculty_value(cell),
              "assignment": None, "problems": [], "notes": [], "ready": False}
    problems, user, now = answer["problems"], None, [None]
    if not setups.applies(item, student["section_id"]):
        problems.append(f"“{item['title']}” does not apply to section {student['section']}")
    if student["dropped"]:
        problems.append("this student dropped the section, and a dropped student's grade is never sent")
    if target is None:
        problems.append("it has no Canvas assignment in this student's course")
    elif "problem" in target:
        problems.append(target["problem"])
    else:
        answer["assignment"] = canvas_export.assignment_summary(target)
        if not target["published"]:
            problems.append(f"“{target['name']}” is not published in Canvas, so it cannot take grades")
        problems.extend(canvas_export.points_problems(target, item))
        user = next((e["user_id"] for e in read[course]["students"] if e["state"] in ("active", "invited") and e.get("sis")
                     and e["sis"] == student.get("uin")), None)
        if not user:
            problems.append("this student is not in the Canvas course")
        else:
            submission = read[course]["submissions"].get((target["id"], user))
            now = canvas_export.shows(submission)
            answer["canvas"] = now[0]
            if (submission or {}).get("state") in canvas_export.WAITING:
                answer["action"] = "waiting"
                problems.append("Canvas is waiting for this student's quiz to be reviewed: grade it there")
            elif any(canvas_export.same(value, form) for form in now):
                answer["action"] = "same"
            else:
                answer["action"] = "add" if now[0] is None else "update"
        if not target["post_manually"]:
            answer["notes"].append("Canvas posts this quiz's grades automatically: the student sees the grade as soon as it is set")
    answer["notes"].append("The gradebook shows it after the next Sync with Canvas, which reads Canvas's grade back (each morning by itself)")
    answer["fingerprint"] = canvas_export.digest({"rule": QUIZ_RULE, "item": item_id, "row": row, "course": course, "assignment": answer["assignment"],
                                                  "user": user, "canvas": now, "value": value, "problems": problems, "cell": cell["id"] if cell else None})
    answer["ready"] = answer["action"] in ("add", "update") and not problems
    batch = None
    if answer["ready"]:
        excused = (now[0] or {}).get("status") == "excused"
        batch = {"key": f"{item_id}:{course}", "item_id": item_id, "title": item["title"], "course_id": course, "assignment_id": target["id"],
                 "manual": bool(target["post_manually"]),
                 "values": [{"row": row, "section_id": student["section_id"], "user": user, "value": value, "score_ref": cell["id"] if cell else None,
                             "action": "set", **({"unexcuse": True} if excused else {})}]}
    return answer, batch, student


def quiz_preview(svc, term_id, data):
    return _quiz_plan(svc, term_id, *_quiz_parse(data))[0]


def _quiz_answer(svc, term_id, send_id, replay):
    """The send and its one receipt, with the outcome on top: accepted (Canvas shows the grade), failed (Canvas did not take it, or
    was never asked) or uncertain (asked, and not read back yet)."""
    found = status(svc, term_id, send_id)
    receipt = found["receipt"]
    state = receipt["state"] if receipt else "uncertain" if found["send"]["status"] == "running" else "failed"
    return {**found, "replay": replay, "status": state}


def quiz_send(svc, term_id, data, actor="workspace"):
    from .service import STOPPING_SYNC, _latest_id
    item_id, row, points = _quiz_parse(data, sending=True)
    request_id, fingerprint = data["request_id"], data["fingerprint"]
    request_sha = common.digest({"quiz-grade": [item_id, row, points], "fingerprint": fingerprint})
    with connect(svc.path) as db:
        earlier = db.execute("SELECT * FROM gradebook_canvas_sends WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
        if earlier and earlier["request_sha256"] != request_sha:
            raise Conflict("That request ID was already used for a different send.")
        running = [r["id"] for r in db.execute("SELECT id FROM gradebook_canvas_sends WHERE term_id=? AND status='running'", (term_id,))]
    if earlier:
        return _quiz_answer(svc, term_id, earlier["id"], True)
    if any(svc._send_running(r) for r in running):
        raise Conflict("A sync with Canvas is sending grades for this term: set this one when it has finished.")
    send_id = str(uuid.uuid4())
    svc.canvas.writes.begin(svc._send_key(send_id), "a quiz grade set in Canvas", STOPPING_SYNC)
    runner = None
    try:
        answer, batch, student = _quiz_plan(svc, term_id, item_id, row, points)
        if answer["fingerprint"] != fingerprint:
            raise Conflict("Canvas or the gradebook changed since the preview. Look at it again.")
        if not answer["ready"]:
            raise Conflict("; ".join(answer["problems"]) or "Canvas already shows this grade for this student.")
        svc._sync_stopping()
        batches = [batch]
        progress = {"total": 1, "accepted": 0, "failed": 0, "uncertain": 0, "assignments": 1, "done_assignments": 0, "current": "", "errors": [], "created": 0}
        with svc.rosters.lock(), connect(svc.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM gradebook_canvas_sends WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone():
                raise Conflict("This send was just started from another window.")
            if db.execute("SELECT 1 FROM gradebook_canvas_sends WHERE term_id=? AND status='running'", (term_id,)).fetchone():
                raise Conflict("A sync with Canvas is sending grades for this term: set this one when it has finished.")
            if _latest_id(db, term_id, item_id, row) != batch["values"][0]["score_ref"]:
                raise Conflict("The gradebook changed since the preview. Look at it again.")
            db.execute("INSERT INTO gradebook_canvas_sends VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (send_id, term_id, student["section_id"], request_id, request_sha, fingerprint, json.dumps(batches), "running",
                        json.dumps(progress), actor, timestamp(), None))
        runner = threading.Thread(target=svc._run_send, args=(send_id, term_id, batches, progress), name=f"canvas-cell-{send_id[:8]}", daemon=True)
        runner.start()  # `_run_send` ends the write when it finishes
    finally:
        if runner is None:
            svc.canvas.writes.end(svc._send_key(send_id))
    runner.join(QUIZ_WAIT)
    return _quiz_answer(svc, term_id, send_id, False)
