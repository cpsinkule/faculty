"""CONFIRMED BY A LATER READ (docs/decisions/canvas.md NIGHTLY SYNC; requirement, 2026-10-10).

A value Faculty sends is recorded `uncertain` before Canvas is asked and settled by reading Canvas back
(`GradebookService._send_batch`). Canvas sometimes applies its bulk update after Faculty stops waiting (`SEND_WAIT`): the read-back
still shows the old grade and the receipt stays uncertain, although nothing is wrong. Reading Canvas again is never a resend
(AGENTS.md OUTSIDE SERVICES: an uncertain write is never sent again by itself), so a later read may say what happened:

- `record`: every value whose LATEST receipt is uncertain and which the read shows as Faculty sent it gets one more receipt,
  `accepted`, on the send that sent it (`gradebook_canvas_sent` is append-only: the uncertain receipt stays as history). Anything
  else stays as it is: a grade Canvas does not show yet is still uncertain, waits for the instructor's own Sync and is never sent
  again by itself.
- Who reads: every Sync with Canvas (its own read, in its own transaction), the morning run (its one read of the linked courses,
  `read`), and THE MORNING'S FOLLOW-UP (`follow`): while the morning sync's send is still the term's last sync and holds
  unconfirmed values, each later round of that morning (canvas_nightly.py, until 08:00) reads only those assignments again,
  records what Canvas now shows, and then carries out the posts that sync withheld in a course only because a grade it sent there
  was not confirmed (`canvas_posting.follow_posts`: POSTING GRADES, still the morning sync's own posting).
- `left`: how many of a send's values Canvas is still not known to hold, which is what the gradebook's notice counts.
"""
import json
import logging
import uuid

from werkzeug.exceptions import HTTPException

from course_storage import timestamp
from rosters import connect

from . import canvas_export, posting_policy

LOG = logging.getLogger(__name__)


def waiting(db, term_id, send_id=None):
    """The receipts a later read may confirm: each value whose latest receipt of the term is `uncertain` (those of `send_id` alone
    when given)."""
    return db.execute(f"""SELECT s.* FROM gradebook_canvas_sent s WHERE s.term_id=? AND s.state='uncertain' {'AND s.send_id=?' if send_id else ''}
        AND s.rowid=(SELECT x.rowid FROM gradebook_canvas_sent x WHERE x.term_id=s.term_id AND x.course_id=s.course_id
        AND x.assignment_id=s.assignment_id AND x.canvas_user_id=s.canvas_user_id ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1)
        ORDER BY s.created_at,s.rowid""", (term_id, send_id) if send_id else (term_id,)).fetchall()


def record(db, term_id, canvas):
    """Inside the caller's transaction: an `accepted` receipt for every waiting value `canvas` shows as it was sent. `canvas`
    {course: {"submissions": {(assignment, user): submission}, "read": the assignments read (absent: every one)}}
    (`canvas_export.from_read`, `CanvasService.read_for_send`). A student Canvas lists no submission for is not known. Returns
    how many it confirmed."""
    confirmed = 0
    for row in waiting(db, term_id):
        data = canvas.get(row["course_id"])
        if data is None or ("read" in data and row["assignment_id"] not in data["read"]):
            continue
        submission = data["submissions"].get((row["assignment_id"], row["canvas_user_id"]))
        if submission is None or not canvas_export.agrees(json.loads(row["sent_json"]), submission):
            continue
        db.execute("INSERT INTO gradebook_canvas_sent VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), row["send_id"], term_id, row["section_id"], row["enrollment_id"], row["item_id"], row["score_ref"],
                    row["course_id"], row["assignment_id"], row["canvas_user_id"], row["sent_json"],
                    json.dumps(canvas_export.canvas_value(submission, entered=True)), "accepted", timestamp()))
        confirmed += 1
    return confirmed


def left(db, term_id, send_id):
    """How many of a send's values Canvas is still not known to hold: those whose latest receipt of the term is not `accepted`
    (catalog only, one query)."""
    return db.execute("""SELECT COUNT(*) FROM (SELECT DISTINCT course_id,assignment_id,canvas_user_id FROM gradebook_canvas_sent
        WHERE term_id=? AND send_id=?) c WHERE (SELECT x.state FROM gradebook_canvas_sent x WHERE x.term_id=? AND x.course_id=c.course_id
        AND x.assignment_id=c.assignment_id AND x.canvas_user_id=c.canvas_user_id ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1)!='accepted'""",
                      (term_id, send_id, term_id)).fetchone()[0]


def read(svc, term_id, canvas):
    """A read Faculty made anyway (the morning run's): what it confirms is recorded, best effort (never fails the run). Returns
    how many it confirmed."""
    try:
        with svc.rosters.lock(), connect(svc.path) as db:
            if not waiting(db, term_id):
                return 0
            db.execute("BEGIN IMMEDIATE")
            return record(db, term_id, canvas)
    except Exception:  # noqa: BLE001 - the receipts stay uncertain, as they were
        LOG.exception("What Canvas now shows of unconfirmed grades could not be recorded")
        return 0


def follow(svc, term_id, send_id):
    """THE MORNING'S FOLLOW-UP of one send (the module's words): reads again, with no lock held, only the assignments that send
    left unconfirmed, records what Canvas now shows (and each one's posting, the grid's mark), then carries out the posts it
    withheld for them. Nothing while the send or its posts still run, once a later sync has taken over, or while Faculty stops;
    a read Canvas refuses changes nothing and the next round tries again. Returns {confirmed, posts} or None when it did nothing."""
    from . import canvas_posting
    if svc.canvas.writes.closing or svc._send_running(send_id) or svc.canvas.writes.running(canvas_posting.after_key(send_id)):
        return None
    with connect(svc.path) as db:
        send = db.execute("SELECT * FROM gradebook_canvas_sends WHERE id=? AND term_id=?", (send_id, term_id)).fetchone()
        last = svc._last_sync(db, term_id)
        if not send or not last or last["request_id"] != send["request_id"]:
            return None
        rows = waiting(db, term_id, send_id)
        if not rows and not canvas_posting.withheld(db, send_id):
            return None
    wanted, columns = {}, {}
    for row in rows:
        wanted.setdefault(row["course_id"], set()).add(row["assignment_id"])
        columns[(row["course_id"], row["assignment_id"])] = row["item_id"]
    confirmed = 0
    if wanted:
        try:
            fresh = svc.canvas.read_for_send(set(wanted), lambda course, _: wanted.get(course, set()))
        except HTTPException as error:  # signed out, or Canvas failing: nothing changes, and the next round reads again
            LOG.warning("The morning sync could not read Canvas again for its unconfirmed grades: %s", error.description)
            return None
        index = posting_policy.by_assignment(fresh)
        marks = []
        for (course, assignment), item_id in sorted(columns.items()):
            shown = next((a for a in fresh[course]["assignments"] if a["id"] == assignment), None)
            if shown is not None:
                seen = posting_policy.counts(index.get((course, assignment)) or [])
                marks.append({"item_id": item_id, "course_id": course, "assignment_id": assignment, "manual": bool(shown.get("post_manually")),
                              "graded": seen["graded"], "hidden": seen["hidden"]})
        with svc.rosters.lock(), connect(svc.path) as db:
            db.execute("BEGIN IMMEDIATE")
            confirmed = record(db, term_id, fresh)
            canvas_posting.record(db, term_id, marks, "nightly")
    return {"confirmed": confirmed, "posts": canvas_posting.follow_posts(svc, send, term_id)}
