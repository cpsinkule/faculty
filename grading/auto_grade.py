"""Grading batches that start by themselves (requirement, 2026-09-28).

The worker looks at each current assignment's new work: exactly what the batch dialog's first choice, "New, reset or changed since
grading", would grade (`JobsMixin.preview` with `selection: new`), for every question a batch may take now (an approved rubric,
confirmed answer areas, and a rubric finalization that stands for it, `JobsMixin.batch_finalization`). Once that set has stayed the
same for `SETTLE` seconds (a scan's papers arrive over a few minutes as they are assembled and their versions confirmed), it starts
the ordinary batch the dialog would start (`create_job`, the default mode of Compute settings, reading and grading), under a request
ID derived from that set, with actor `automatic`. Everything a batch does is unchanged: its preview, fingerprint, frozen settings,
retries, flags and review.

It never starts one while a batch of the assignment is queued or running, while the switch is off, or while the assignment's last
batch was cancelled, or reset since (the instructor is choosing what grades it next): starting a batch
themselves resumes it. A batch that cannot start (no models chosen, say) is recorded once as `automatic_batch_not_started` with the
reason, which the Grade step shows; nothing is retried until the work or the settings change. Flagged responses wait for review as
before, and failed ones for the batch's own Retry: neither is new work.
"""
import json
import logging
import time

from werkzeug.exceptions import Conflict, Forbidden

from rosters import connect
from . import config
from .models import digest

LOG = logging.getLogger(__name__)

LOOK_EVERY = 60     # seconds between two looks at one assignment whose activity changed
RECHECK_EVERY = 900  # every current assignment is looked at this often anyway
SETTLE = 120        # the new work must stay the same this long before its batch starts
PREFIX = "auto:"    # request IDs of automatic batches
CARRIERS = "flag-resolution:%"  # batches the flag review makes to carry its rulings: never the instructor's choice of what grades next


def current_plan_ids(db):
    """Current-term plans, most recently updated first (the workers' fairness order)."""
    return [row[0] for row in db.execute("""SELECT a.id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id
        JOIN course_terms t ON t.id=e.term_id WHERE t.is_current=1 AND t.deleted_at IS NULL ORDER BY a.updated_at DESC,a.id""")]


class AutoGradeMixin:
    def batch_finalization(self, quality, questions):
        """(review id, held) for a batch of `questions`: the rubric finalization their graders read, and the questions it does not stand
        for, each with its reason. One question's changed rubric holds back only that question (requirement, 2026-09-28): a question
        grades while its finalization stands (`rubric_quality_status['standing']`, the plan's confirmation or the latest one that
        covered its current input) and no heavier combination is left open."""
        standing, review_ids, held = quality.get("standing") or {}, set(), []
        for q in questions:
            entry = standing.get(q["question_id"])
            if entry and entry["rubric_id"] == q["rubric_id"] and not entry.get("open"):
                review_ids.add(entry["review_id"])
                continue
            held.append({"question_id": q["question_id"], "reason": "its lines still allow more points than it is worth; mark the alternatives in Key & Rubric Finalization"
                         if entry and entry.get("open") else "its rubric changed since the rubric finalization was confirmed; confirm it again"})
        if len(review_ids) > 1:
            raise Conflict("These questions stand under different rubric finalizations. Confirm the finalization again before grading them together.")
        return (review_ids.pop() if review_ids else None), held

    @staticmethod
    def auto_grade_paused(db, aid):
        """Why the assignment's next batch is the instructor's to start, or None: its last batch was cancelled, or reset since (they are
        choosing what grades it next). Starting a batch themselves clears it."""
        latest = db.execute(f"""SELECT j.id,j.status,j.cancel_requested,j.created_at
                FROM frq_jobs j WHERE j.assignment_id=? AND j.request_id NOT LIKE '{CARRIERS}' ORDER BY j.created_at DESC,j.rowid DESC LIMIT 1""",
                            (aid,)).fetchone()
        if not latest:
            return None
        if latest["cancel_requested"] or latest["status"] == "cancelled":
            return "cancelled"
        reset = db.execute("SELECT MAX(created_at) FROM frq_job_resets WHERE assignment_id=?", (aid,)).fetchone()[0]
        if reset and reset > latest["created_at"]:
            return "reset"
        return None

    def auto_grade_state(self, db, aid, settings):
        """What the Grade step says about automatic grading for this assignment. Catalog reads only."""
        latest = db.execute("SELECT created_at FROM frq_jobs WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (aid,)).fetchone()
        refused = db.execute("""SELECT detail_json,created_at FROM frq_events WHERE assignment_id=? AND kind='automatic_batch_not_started'
                                ORDER BY id DESC LIMIT 1""", (aid,)).fetchone()
        not_started = None
        if refused and (not latest or refused["created_at"] > latest["created_at"]):
            detail = json.loads(refused["detail_json"])
            not_started = {"reason": detail.get("reason", ""), "responses": detail.get("responses", 0), "at": refused["created_at"]}
        return {"enabled": bool(settings.get("auto_grade", True)), "paused": self.auto_grade_paused(db, aid), "not_started": not_started,
                "settle_seconds": SETTLE}

    def _auto_grade_marker(self, db, aid):
        """A cheap sign that something which may change an assignment's new work happened: its own record (every batch, confirmation,
        decision and upload writes an event or touches it), its exam's reviewed papers and the students named on its copies (a paper
        named by hand after its scan went on to grading, requirement, 2026-11-06)."""
        plan = db.execute("SELECT version,event_id FROM frq_assignments WHERE id=?", (aid,)).fetchone()
        return (plan["version"], db.execute("SELECT MAX(id) FROM frq_events WHERE assignment_id=?", (aid,)).fetchone()[0],
                db.execute("SELECT MAX(updated_at) FROM frq_jobs WHERE assignment_id=?", (aid,)).fetchone()[0],
                db.execute("""SELECT MAX(ar.rowid) FROM assembly_revisions ar JOIN printed_copies c ON c.id=ar.printed_copy_id
                              JOIN exam_event_batches eb ON eb.batch_id=c.print_batch_id WHERE eb.event_id=?""", (plan["event_id"],)).fetchone()[0],
                db.execute("SELECT MAX(created_at) FROM submission_choices").fetchone()[0],
                db.execute("""SELECT MAX(e.rowid) FROM document_events e JOIN printed_copies c ON c.id=e.copy_id
                              JOIN exam_event_batches eb ON eb.batch_id=c.print_batch_id WHERE eb.event_id=? AND e.kind='copy_assignment'""",
                           (plan["event_id"],)).fetchone()[0])

    def _auto_grade_command(self, db, aid, settings):
        """The dialog's default batch for this assignment's gradable questions, or None when no question may be graded now."""
        quality = self.rubric_quality_status(db, aid)
        questions = []
        for (qid,) in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY rowid", (aid,)):
            q = self.question(db, aid, qid)
            if not q["rubric"] or not q["rubric"]["approved_at"]:
                continue
            try:
                self.require_location_review(db, aid, q)
            except (ValueError, Conflict):
                continue
            questions.append(q)
        _, held = self.batch_finalization(quality, questions)
        held = {item["question_id"] for item in held}
        qids = [q["question_id"] for q in questions if q["question_id"] not in held]
        if not qids:
            return None
        return {"selection": "new", "question_ids": qids, "mode": settings["default_tier"], "operation": "recognize"}

    def auto_grade_one(self):
        """One look per worker turn at most: start a settled batch, or look at the one assignment most due for a look."""
        current = self.settings()
        settings = current["settings"]
        if not settings.get("auto_grade", True):
            return False
        now, state = time.monotonic(), getattr(self, "_auto_grade", None)
        if state is None:
            state = self._auto_grade = {}
        with self.rosters.lock(), connect(self.path) as db:
            plans = current_plan_ids(db)
            busy = {row[0] for row in db.execute("SELECT DISTINCT assignment_id FROM frq_jobs WHERE status IN ('pending','running')")}
            queued = db.execute("SELECT COUNT(*) FROM frq_jobs WHERE mode='nebius' AND status IN ('pending','running')").fetchone()[0]
            due = None
            for aid in plans:
                mine = state.setdefault(aid, {})
                if aid in busy or self.auto_grade_paused(db, aid):
                    mine.pop("pending", None)  # Its batch runs, or the instructor is choosing: the settle starts again afterwards.
                    continue
                pending = mine.get("pending")
                if pending and now - pending["since"] >= SETTLE:
                    due = ("start", aid)
                    break
                marker = (current["version"], self._auto_grade_marker(db, aid))  # Saved settings are new ground too.
                if pending or now - mine.get("looked", -RECHECK_EVERY) < (LOOK_EVERY if marker != mine.get("marker") else RECHECK_EVERY):
                    continue
                due = due or ("look", aid, marker)
        for gone in set(state) - set(plans):
            state.pop(gone, None)
        if not due:
            return False
        aid, mine = due[1], state[due[1]]
        if due[0] == "look":
            mine["looked"], mine["marker"] = now, due[2]
            self._auto_grade_look(aid, settings, mine, now)
            return False
        pending = mine.pop("pending")
        if settings["default_tier"] in config.TIERS and queued >= settings["max_cloud_jobs"]:
            mine["pending"] = pending  # The workspace's cap on cloud batches at once: wait for one to finish.
            return False
        try:
            job = self.create_job(aid, pending["command"], actor="automatic")
        except Conflict as error:
            # Something changed since the look (a new paper, a decision, a setting): look again, and settle anew.
            mine["looked"] = -RECHECK_EVERY
            if "request ID was already used" in str(getattr(error, "description", error)):
                self._auto_grade_refused(aid, mine, pending, error)
            return False
        except (ValueError, Forbidden) as error:
            self._auto_grade_refused(aid, mine, pending, error)
            return False
        LOG.info("Assignment %s: grading batch %s started automatically for %d responses", aid, job["id"], pending["responses"])
        return True

    def _auto_grade_look(self, aid, settings, mine, now):
        with self.rosters.lock(), connect(self.path) as db:
            command = self._auto_grade_command(db, aid, settings)
        if not command:
            return False
        try:
            preview = self.preview(aid, command)
        except (ValueError, Conflict, Forbidden) as error:
            self._auto_grade_refused(aid, mine, {"key": digest(command), "responses": 0}, error)
            return False
        eligible = preview["eligible"]
        if not eligible:
            return False
        # The preview's fingerprint names the work, the settings, the assignment's revision and the finalization: what a refusal was
        # about, and what the batch is keyed by (the same work in the same settings is one request, whatever happens to it).
        key = digest([preview["fingerprint"], command["mode"]])
        if mine.get("refused") == key:
            return False  # Refused for exactly this work and these settings: the reason is on the Grade step until something changes.
        mine["pending"] = {"key": key, "since": now, "responses": len(eligible),
                           "command": {**command, "fingerprint": preview["fingerprint"], "request_id": f"{PREFIX}{aid}:{key[:32]}"}}
        return False  # A look is no work done: the turn goes on to the worker's other queues.

    def _auto_grade_refused(self, aid, mine, pending, error):
        reason = str(getattr(error, "description", error))
        if mine.get("refused") == pending["key"]:
            return  # Already recorded for exactly this work and these settings.
        mine["refused"] = pending["key"]
        LOG.info("Assignment %s: a grading batch could not start automatically: %s", aid, reason)
        with self.rosters.lock(), connect(self.path) as db:
            self.event(db, aid, "automatic_batch_not_started", {"reason": reason, "responses": pending.get("responses", 0)}, "automatic")
