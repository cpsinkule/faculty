"""iClicker after class (docs/decisions/iclicker.md AFTER CLASS; gradebook/README.md "iClicker after class"; requirement, 2026-10-06).

Beside the web process (it holds the iClicker sign-in, iclicker.md SIGN-IN), a look at the campus clock every ROUND seconds. For
each current term that uses its Term schedule (scheduling/service.py `current_use`), today's class meetings (`occurrences`, which
skips the schedule's days off) say when each section's class ends; DELAY after the last one, a section that links an iClicker course
gets Import from iClicker BY ITSELF for that section: the plan the instructor's own import makes with no choice made (a session's
remembered column, else the one column of the same title, else a new 10-point column in Checks for Understanding), with one
difference the instructor chose: a roster student who did not answer one of TODAY'S sessions is recorded missing, 0 (only today's,
and only a session someone answered: a student added later never gets 0s for earlier classes, and an empty session makes no
column). It writes through that import's own apply (`GradebookService._source_apply`): values typed in Faculty and other imports'
values kept, the sessions' dates recorded and the excused absences' full credit given in the same transaction (ABSENCES
ICLICKER-CREDIT), so an absence excused for today is credited when today's class ends.

Each attempt is a `gradebook_iclicker_runs` row (migration 008, owned by the section, append-only) naming the sessions iClicker had
started that day (what Follow-ups read: was an iClicker given, and when). A section's day is done once a run read iClicker and
no session of the day was still running; a running session is looked at again every RUNNING seconds, a failure or a session waiting
for a column every RETRY seconds, until campus midnight. The next class day's run brings in anything missed (it reads every
session). Faculty writes nothing to iClicker or Canvas here, and holds no lock while iClicker is read.
"""
import json
import logging
import threading
import uuid
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path

from werkzeug.exceptions import Conflict, HTTPException

from course_storage import exclusive, timestamp
import rounds
from rosters import connect
from scheduling import service as scheduling
from scheduling.clock import CAMPUS_ZONE

from . import iclicker_import

LOG = logging.getLogger(__name__)
LOCK = "3c0f2a8e-6b1d-4e57-9d2c-8f4a1e7b5c90"  # one after-class round at a time, across processes
ROUND = 120                                    # seconds between the worker's looks at the clock
DELAY = timedelta(minutes=10)                  # after a section's class ends, before its sessions are read
RUNNING = 15 * 60                              # seconds before a day with a session still running is looked at again
RETRY = 3600                                   # seconds before a day that failed (or waits for a column) is tried again
DONE = ("imported", "unchanged", "not_linked")
NOTE = {"automatic": "iclicker"}


def _seconds_since(stamp, now):
    try:
        return (now - datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return float("inf")


def today_sessions(read, day):
    """The sessions of the read course started on that campus date: [{id, title, running}] (polls only), titles as iClicker
    names them; never a student."""
    found = []
    for course in read.values():
        for session in course["sessions"]:
            when = iclicker_import._instant(session["started"]) if session.get("started") else None
            if when and when.date().isoformat() == day and iclicker_import.scored(session):
                found.append({"id": session["id"], "title": (session.get("name") or "iClicker session")[:120], "running": not session["ended"]})
    return found


def latest(db, section_id):
    """The section's latest after-class run: {class_date, status, at, sessions, running, problem, waiting}."""
    row = db.execute("""SELECT class_date,status,detail_json,created_at FROM gradebook_iclicker_runs WHERE section_id=?
        ORDER BY class_date DESC,created_at DESC,rowid DESC LIMIT 1""", (section_id,)).fetchone()
    if not row:
        return None
    detail = json.loads(row["detail_json"])
    return {"class_date": row["class_date"], "status": row["status"], "at": row["created_at"], **{key: detail[key] for key in
            ("sessions", "running", "problem", "waiting", "written", "new_columns") if key in detail}}


class IClickerAfterClass:
    def __init__(self, gradebook, storage=None):
        self.gradebook, self.storage, self.path = gradebook, storage, gradebook.path
        self._stop = None

    def lock(self):
        return exclusive(self.storage, LOCK, Path(self.path).parent / ".iclicker-after-class.lock", "The after-class iClicker import is running now.")

    def start(self):
        """The web process's thread (app.py, `__main__`): never started by the checks, which call `round` with their own clock."""
        self._stop = threading.Event()
        threading.Thread(target=self.worker, args=(self._stop,), name="iclicker-after-class", daemon=True).start()

    def worker(self, stop):
        LOG.info("iClicker is imported %d minutes after each section's class", DELAY.seconds // 60)
        rounds.repeat(stop, self._locked_round, ROUND, LOG, "The after-class iClicker import is waiting for storage",
                      "The after-class iClicker import stopped unexpectedly")

    def _locked_round(self):
        with self.lock():
            self.round()

    def _due(self, now):
        """[(term, section, day)] whose class ended DELAY ago today and whose day is not done (nor tried too recently)."""
        local = now.astimezone(CAMPUS_ZONE)
        day, due = local.date().isoformat(), []
        with connect(self.path) as db:
            terms = [row[0] for row in db.execute("""SELECT DISTINCT t.id FROM course_terms t JOIN course_sections s ON s.term_id=t.id
                JOIN iclicker_course_links l ON l.section_id=s.id WHERE t.is_current=1 AND t.deleted_at IS NULL ORDER BY t.id""")]
            for term_id in terms:
                if not scheduling.current_use(db, term_id):
                    continue  # the Term schedule says when classes end; a term not using it has no automatic import
                ends = {}
                for meeting in scheduling.occurrences(db, term_id, day, day):
                    if meeting["schedule_kind"] == "class" and meeting.get("section_id") and meeting.get("end_time"):
                        ends[meeting["section_id"]] = max(ends.get(meeting["section_id"], ""), meeting["end_time"])
                for section_id, end in sorted(ends.items()):
                    try:
                        hour, minute = (int(part) for part in end.split(":"))
                    except ValueError:
                        continue
                    if local < datetime.combine(local.date(), clock_time(hour, minute), CAMPUS_ZONE) + DELAY:
                        continue
                    last = db.execute("""SELECT status,detail_json,created_at FROM gradebook_iclicker_runs WHERE section_id=? AND class_date=?
                        ORDER BY created_at DESC,rowid DESC LIMIT 1""", (section_id, day)).fetchone()
                    if last:
                        running = json.loads(last["detail_json"]).get("running", 0)
                        if last["status"] in DONE and not running:
                            continue
                        wait = RUNNING if last["status"] in DONE else RETRY
                        if _seconds_since(last["created_at"], now) < wait:
                            continue
                    due.append((term_id, section_id, day))
        return due

    def round(self, now=None):
        """Each section whose class ended DELAY ago today, once its day is not done. Holds LOCK (the caller's). Returns
        {section_id: status} of the sections it ran."""
        iclicker = self.gradebook.iclicker
        if iclicker is None or not iclicker.settings.configured:
            return {}
        now = now or datetime.now(timezone.utc)
        return {section_id: self.run(term_id, section_id, day) for term_id, section_id, day in self._due(now)}

    def run(self, term_id, section_id, day):
        """One section's class day: read its iClicker course, plan with no choice, write what the plan says, record the attempt."""
        gradebook = self.gradebook
        try:
            links = gradebook._source_links("iclicker", term_id, section_id)
        except ValueError:
            return self._record(term_id, section_id, day, "not_linked", {})
        try:
            read = gradebook.iclicker.read_scores({link["course_id"] for link in links.values()})
        except HTTPException as error:  # iClicker failures are 502 with their reason; not set up is 400
            return self._record(term_id, section_id, day, "failed", {"problem": str(error.description)[:500]})
        except Exception as error:  # the network itself: recorded, so the day is tried again in an hour, not every round
            LOG.exception("The after-class iClicker import could not read iClicker")
            return self._record(term_id, section_id, day, "failed", {"problem": f"iClicker could not be read ({type(error).__name__})."})
        sessions = today_sessions(read, day)
        detail = {"sessions": [{"id": s["id"], "title": s["title"]} for s in sessions], "running": sum(1 for s in sessions if s["running"])}
        status, more, reference = self._import(term_id, section_id, day, links, read)
        return self._record(term_id, section_id, day, status, {**detail, **more}, reference)

    def _import(self, term_id, section_id, day, links, read):
        gradebook = self.gradebook
        options = iclicker_import.normalize_options({"missing_as_missing": True})
        for attempt in (1, 2):
            try:
                with gradebook.rosters.lock():
                    context = gradebook._scoped(gradebook._context(term_id), section_id)
                    with connect(self.path) as db:
                        plan = gradebook._source_plan("iclicker", db, context, links, read, options, missing_on={day})[0]
            except ValueError as error:
                return "failed", {"problem": str(error)[:500]}, None
            waiting = sorted({column["title"] for column in plan["columns"] if column["mapping"]["action"] == "skip" and column["suggested"] == "ambiguous"})
            detail = {"waiting": waiting} if waiting else {}
            if not plan["ready"]:
                return "needs_choice", {**detail, "problem": " ".join(plan["warnings"][-2:])[:500]}, None
            written = sum(1 for change in plan["changes"] if change["action"] in ("add", "update") and not change["compare"])
            if not written and not plan["new_items"] and not plan["remember"] and not plan.get("widen"):
                return ("needs_choice" if waiting else "unchanged"), detail, None
            request_id = f"iclicker-after-class:{day}:{uuid.uuid4().hex[:12]}"
            try:
                gradebook._source_apply("iclicker", term_id, {"options": plan["options"], "fingerprint": plan["fingerprint"],
                                                              "request_id": request_id, "section_id": section_id},
                                        given=(links, read), note=NOTE, missing_on={day})
            except Conflict:
                if attempt == 1:
                    continue  # the gradebook changed between the plan and the write: planned once more against it
                return "failed", {**detail, "problem": "The gradebook kept changing while the import ran."}, None
            except ValueError as error:
                return "failed", {**detail, "problem": str(error)[:500]}, None
            with connect(self.path) as db:
                reference = db.execute("SELECT id FROM gradebook_requests WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()[0]
            return "imported", {**detail, "written": written, "new_columns": len(plan["new_items"])}, reference
        return "failed", {"problem": "The gradebook kept changing while the import ran."}, None

    def _record(self, term_id, section_id, day, status, detail, request_ref=None):
        with connect(self.path) as db:
            db.execute("INSERT INTO gradebook_iclicker_runs VALUES(?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), term_id, section_id, day, status, json.dumps(detail, sort_keys=True), request_ref, timestamp()))
        return status
