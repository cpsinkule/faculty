"""THE MORNING SYNC (README.md "The morning sync"; docs/decisions/canvas.md NIGHTLY SYNC; requirement, 2026-10-08).

Each campus morning from HOUR (after the 04:00 nightly backup), in the WEB process (its Canvas writes are registered there, so a
restart waits for them: canvas/stopping.py), every current term whose sections link a Canvas course is run once:

1. Canvas is read once (no lock held). What it shows of each column's posting is kept for the grid's mark.
2. DUE DATES FOLLOW CANVAS for the assignments Canvas grades (WebAssign's and Canvas quizzes'): the gradebook's columns and
   Faculty's Canvas copy (webassign_nightly.WebAssignNightly._follow), before anything is planned, so "after its due date" reads
   Canvas's date. An assignment Canvas has published in every course it goes to is published in Faculty's Canvas copy too
   (`CanvasContentService.follow_published`, docs/decisions/canvas.md PUBLISHED IN CANVAS STANDS).
3. A term a Sync with Canvas has written to: Sync with Canvas by itself (`GradebookService.canvas_sync_nightly`) for the Canvas
   courses a sync has written to before, as the instructor's Sync does by default; held grades wait for the instructor, and
   nothing an earlier uncertain write may have done is asked again. A term whose first sync is still the instructor's: only
   WebAssign's and Canvas quizzes' scores are imported, as the 10 pm import did (`_import`).
4. One `gradebook_canvas_nightly_runs` row per attempt (migration 013). A morning that could not read Canvas (signed out, Canvas
   failing) or whose sync failed is tried again each hour until END; the gradebook names what needs the instructor.
5. CONFIRMED BY A LATER READ (gradebook/canvas_confirm.py; requirement, 2026-10-10, after a morning whose five grades reached
   Canvas minutes after Faculty stopped waiting): the morning's read settles every value an earlier send left uncertain that
   Canvas now shows, before anything is planned; and while this morning's own send still holds unconfirmed values, each later
   round until END reads only those assignments again and then carries out the posts it withheld for them (`_follow`). Reading is
   never a resend: a value Canvas still does not show waits for the instructor's own Sync, as before.

The 10 pm WebAssign import's rows (`gradebook_nightly_runs`, migration 007) stay as history; its loop no longer runs.
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from werkzeug.exceptions import HTTPException, ServiceUnavailable

from course_storage import timestamp
import rounds
from rosters import connect

from . import canvas_confirm, canvas_export, canvas_import, webassign_nightly

LOG = logging.getLogger(__name__)
LOCK = webassign_nightly.LOCK  # one morning round at a time, across processes (an old Inbox worker's 10 pm loop included)
HOUR = 5                       # the campus hour a morning's sync starts (after the 04:00 backup and its lock)
END = 8                        # the campus hour after which a morning that could not run waits for the next one
ROUND = 300                    # seconds between the worker's looks at the clock
RETRY = 3600                   # seconds before a morning that could not read Canvas, or whose sync failed, is tried again
RETRIED = ("signed_out", "failed")
TABLE = "gradebook_canvas_nightly_runs"


def latest(gradebook, db, term_id, canvas_status=None):
    """The term's latest morning run as the gradebook page shows it (the 10 pm WebAssign import's before there was one); None when
    there has been none. `waiting` leaves out the assignments the instructor has matched or skipped since; `signed_in_since` says
    a sign-in to Canvas came after a run that found Canvas signed out; `send` how the sync's send ended; `posts_failed` the
    columns whose post or hide still did not take (`canvas_posting.outstanding`: one withheld for unconfirmed grades no longer
    counts once the follow-up carried it out), only while that sync is the term's last (a later Sync has tried again)."""
    row = db.execute(f"SELECT * FROM {TABLE} WHERE term_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (term_id,)).fetchone()
    if not row:
        return webassign_nightly.latest(db, term_id, canvas_status)
    detail = json.loads(row["detail_json"])
    decided = {f"{r[0]}:{r[1]}" for r in db.execute("SELECT course_id,assignment_id FROM gradebook_canvas_links WHERE term_id=? AND created_at>?",
                                                   (term_id, row["created_at"]))}
    open_ = [entry for entry in detail.get("waiting", []) if entry["key"] not in decided]
    found = {"night": row["night"], "kind": row["kind"], "status": row["status"], "at": row["created_at"],
             "waiting": list(dict.fromkeys(entry["title"] for entry in open_)), "waiting_keys": open_}
    found.update({key: detail[key] for key in ("written", "new_columns", "problem", "held", "unsure", "sending", "taken", "creating",
                                               "posting", "publishing", "due_dates") if detail.get(key)})
    if row["send_ref"]:
        from . import canvas_posting
        send = db.execute("SELECT * FROM gradebook_canvas_sends WHERE id=?", (row["send_ref"],)).fetchone()
        if send:
            view = gradebook._send_view(send)
            found["send"] = {key: view[key] for key in ("id", "status", "progress")}
            last = gradebook._last_sync(db, term_id)
            failed = canvas_posting.outstanding(canvas_posting.for_send(gradebook, db, row["send_ref"])) if last and last["ref"] == row["request_ref"] else 0
            if failed:
                found["posts_failed"] = failed
    if row["status"] == "signed_out" and canvas_status is not None:
        saved = (canvas_status() or {}).get("saved_at")
        found["signed_in_since"] = bool(saved and saved > row["created_at"][:19] + "Z")
    return found


def _seconds_since(stamp, now):
    try:
        return (now - datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return float("inf")


class CanvasNightly:
    def __init__(self, gradebook, storage=None, content=None):
        self.gradebook, self.storage, self.path = gradebook, storage, gradebook.path
        # The 10 pm import's own steps (its score import, the due dates that follow Canvas) and its lock.
        self.webassign = webassign_nightly.WebAssignNightly(gradebook, storage, content)
        self._stop = None

    def lock(self):
        return self.webassign.lock()

    def start(self):
        """The web process's thread (app.py, `__main__`): never started by the checks, which call `round` with their own clock."""
        self._stop = threading.Event()
        threading.Thread(target=self.worker, args=(self._stop,), name="canvas-morning-sync", daemon=True).start()

    def worker(self, stop):
        LOG.info("The morning sync with Canvas is on: from %02d:00 until %02d:00 campus time", HOUR, END)
        rounds.repeat(stop, self._locked_round, ROUND, LOG, "The morning sync with Canvas is waiting for storage",
                      "The morning sync with Canvas stopped unexpectedly")

    def _locked_round(self):
        with self.lock():
            self.round()

    def _terms(self):
        """Current terms of which a section has linked a Canvas course (the term's own links decide which courses are read)."""
        with connect(self.path) as db:
            return [row[0] for row in db.execute("""SELECT DISTINCT t.id FROM course_terms t JOIN course_sections s ON s.term_id=t.id
                JOIN canvas_course_links l ON l.section_id=s.id WHERE t.is_current=1 AND t.deleted_at IS NULL ORDER BY t.id""")]

    def round(self, now=None):
        """From HOUR until END campus time, each current term's morning once; one that could not read Canvas, or whose sync failed,
        again after RETRY seconds. Holds LOCK (the caller's). Returns {term_id: status} of the terms it ran."""
        canvas = self.gradebook.canvas
        if canvas is None or not canvas.settings.configured:
            return {}
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(canvas_import.CAMPUS)
        if not HOUR <= local.hour < END:
            return {}
        night, ran = local.date().isoformat(), {}
        for term_id in self._terms():
            # A RESTART WAITS FOR THE SYNC (requirement, 2026-10-17, replacing the fixed 05:00-08:00 no-restart window): the term's
            # morning registers with the process's Canvas writes for as long as it reads, plans, sends or follows up, so a stop waits
            # for it and the restart check (`GET /api/assistant/restart-check`) names it. Between rounds nothing is running.
            key = f"morning:{term_id}"
            try:
                canvas.writes.begin(key, "the morning sync with Canvas", "Faculty is restarting.")
            except ServiceUnavailable:
                break  # Faculty is stopping: the next process runs the rest
            try:
                with connect(self.path) as db:
                    last = db.execute(f"SELECT status,created_at,send_ref FROM {TABLE} WHERE term_id=? AND night=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                                      (term_id, night)).fetchone()
                if last and (last["status"] not in RETRIED or _seconds_since(last["created_at"], now) < RETRY):
                    self._follow(term_id, last["send_ref"])
                    continue
                try:
                    status = self.run(term_id, night)
                except ServiceUnavailable:
                    break  # stopping while it read Canvas: nothing was written or recorded, the next process tries again
                if status:
                    ran[term_id] = status
            finally:
                canvas.writes.end(key)
        return ran

    def _follow(self, term_id, send_id):
        """CONFIRMED BY A LATER READ: what this morning's send left unconfirmed, read again (`canvas_confirm.follow`). Never fails
        the round: the receipts stay as they were and the next round reads again."""
        if not send_id:
            return None
        try:
            return canvas_confirm.follow(self.gradebook, term_id, send_id)
        except Exception:  # noqa: BLE001 - logged; nothing was asked of Canvas that its own record does not hold
            LOG.exception("The morning sync could not follow up its unconfirmed grades")
            return None

    def run(self, term_id, night):
        """One term's morning: read Canvas, follow its due dates, then sync (or, before the term's first sync, import Canvas's own
        scores), and record the attempt."""
        gradebook = self.gradebook
        try:
            links = gradebook._source_links("canvas", term_id)
        except ValueError:
            return None  # no section of the term links a course any more
        linked = {link["course_id"] for link in links.values()}
        with connect(self.path) as db:
            written = [course for course in gradebook._written_courses(db, term_id) if course in linked]
        kind = "sync" if written else "import"
        try:
            read = gradebook.canvas.read_grades(linked)
        except HTTPException as error:  # 401 signed out (CanvasSignedOut), 502 Canvas could not be read
            return self._record(term_id, night, kind, "signed_out" if error.code == 401 else "failed", {"problem": str(error.description)[:500]})
        except Exception as error:  # the network itself: recorded, so the morning is tried again in an hour, not every round
            LOG.exception("The morning sync could not read Canvas")
            return self._record(term_id, night, kind, "failed", {"problem": f"Canvas could not be read ({type(error).__name__})."})
        from . import canvas_posting
        canvas_posting.record_read(gradebook, term_id, links, read, "nightly")  # the grid's mark (best effort)
        # CONFIRMED BY A LATER READ: a value an earlier send could not read back that Canvas now shows is settled before the plan.
        canvas_confirm.read(gradebook, term_id, canvas_export.from_read(read))
        graded, count = webassign_nightly.webassign_only(read, quizzes=True)  # Canvas's own grades: WebAssign's and Canvas quizzes'
        followed = self.webassign._follow(term_id, night, links, graded)  # their due dates first: "after its due date" reads Canvas's
        reference = send = None
        if written:
            try:
                status, detail, reference, send = gradebook.canvas_sync_nightly(term_id, night, links, read, written)
            except ServiceUnavailable:
                raise
            except HTTPException as error:  # the targets read again: signed out, or Canvas failing
                status, detail = ("signed_out" if error.code == 401 else "failed"), {"problem": str(error.description)[:500]}
            except Exception as error:  # noqa: BLE001 - recorded, and tried again in an hour
                LOG.exception("The morning sync of term %s failed", term_id)
                status, detail = "failed", {"problem": f"{type(error).__name__}: {error}"[:500]}
            if status == "needs_choice" and count:
                # The sync waits for the instructor's choice: WebAssign's and Canvas quizzes' scores still come in.
                imported, more, _ = self.webassign._import(term_id, night, links, graded, count)
                detail["import"] = {"status": imported, **{key: more[key] for key in ("written", "new_columns") if key in more}}
        elif count:
            status, detail, reference = self.webassign._import(term_id, night, links, graded, count)
        else:
            status, detail = "none", {}
        detail.update(followed)
        return self._record(term_id, night, kind, status, detail, reference, send)

    def _record(self, term_id, night, kind, status, detail, request_ref=None, send_ref=None):
        with self.gradebook.rosters.lock(), connect(self.path) as db:
            db.execute(f"INSERT INTO {TABLE}(id,term_id,night,kind,status,detail_json,request_ref,send_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), term_id, night, kind, status, json.dumps(detail, sort_keys=True), request_ref, send_ref, timestamp()))
        if status in RETRIED:
            LOG.warning("Morning sync of term %s: %s (%s)", term_id, status, detail.get("problem", ""))
        return status
