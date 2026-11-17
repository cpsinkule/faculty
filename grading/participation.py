"""Graded for participation (GRADING "GRADED FOR PARTICIPATION"; requirement, 2026-11-01).

"I want to add an option to the assignment drafter that is "graded for participation". this shouldn't change anything in the
pipeline itself, but after grading, it should give everyone that has a submission full credit. I still want the rubric lines to be
shown so that students get feedback on what they did, but maybe a blanket adjustment at the end or something like that automatically
applied"; shown the plan, "i like everything", and "graded for participation assignments should default to only one version".

The setting is owned by the TERM grading plan (`frq_participation`, migration 052, append-only: the latest row stands; none is off).
Grading never reads it: rubrics, models, flags, reviews, acceptances, training examples and metrics keep the real scores, and a batch
adjustment or a regrade, which write reviews, start from the real grade. What COUNTS a final grade reads it: the gradebook's totals
(`gradebook.sources`, so Certification and Export, its certified rows and every pull), the export, Sending grades to Gradescope and its
coverage, and the Gradescope comparison. There each final grade below its question's worth gets one more line, "Graded for
participation", worth what brings it to full credit, and the grade counts as the question's worth. The line is never stored on a
grade: it is worked out on every read, so a regrade, a flag review or a late paper gets it without anything applied again, and
turning the setting off takes it away. Only a final grade gets it, so a student with no paper still has no grade (ZERO ONCE DUE),
and a paper is a submission however little is written on it.

The line is the question's worth less the grade's UNCLAMPED score (before the rubric's floor): Gradescope adds a point adjustment
before its own floor, so the same line gives exactly the worth there too, and the comment says what was added. Gradescope receives it
as part of the grade's point adjustment and, like a batch line, as the grade's comment ("+2.5 Graded for participation").

Written questions only: a bubble sheet is scored against its key, and MC corrections set Gradescope to that score, so an assignment
with a saved bubble-sheet key cannot be graded for participation (and Certification and Export blocks while it has both).

THE CERTIFICATION COUNTS THEM TOO (requirement, 2026-11-12): a certification records its grades as they count, and whatever asks whether it
still stands reads them the same way (`progress.decision_state` counts the rows it is given), so only a grade's lines or the setting
changing reopens it. Certify just this change reads the grade as certified with its line (`certification_change.certified_content`).

THE WAITING GRADES APPROVE THEMSELVES (requirement, 2026-11-11). Grading and flagging are unchanged. What
then waits for the instructor's review on such a plan (`WAITING`, the states Certification and Export counts as waiting for review)
is approved by the worker as the graders left it (`approve_waiting`, `auto_participation_one`): an ordinary approved review of the
grade's own result, actor `automatic`, its comment `APPROVED`, with the transcript, the ticked lines and the score the result
proposed, so the flags stay on that result where every page shows them. No flag review is sent by itself on such a plan
(`auto_flag_review`). Left for the instructor: an answer with no proposed score, a draft, an answer marked unreadable or rejected,
a failed answer, and Certify. The approval is Faculty's, not the instructor's: it is no training example, no measure of the
graders, and no decision of theirs in the review metrics (`APPROVAL`). A late paper it settled certifies itself like any late paper
whose grades are cleared (same day: "i want late participation-only assignments to be auto certified as well").
"""
import json
import logging
import time

from werkzeug.exceptions import Conflict, Forbidden, NotFound

from course_storage import timestamp
from rosters import connect

from .models import encoded, finite_decimal, uid
from .progress import amount
from .scoring import score

LOG = logging.getLogger(__name__)
REASON = "Graded for participation"
APPROVED = "Graded for participation: Faculty approved this grade as the graders left it, flags and all; the answer was not looked at."
# A review `v` that is such an approval, for the readers that count only the instructor's own decisions; its one parameter is APPROVED.
APPROVAL = "(v.actor='automatic' AND v.comment=?)"
WAITING = ("proposed", "grading_review", "ocr_review", "both", "trial_accepted")  # what Certify counts as "waiting for review"
LOOK_EVERY = 60  # seconds between two looks at one assignment whose activity changed
BUBBLE = ("Graded for participation covers written questions only, and this assignment has a bubble-sheet answer key. "
          "Its bubble sheets would keep their own scores.")


def enabled(db, aid):
    """Whether the plan is graded for participation now: its latest row, else not."""
    row = db.execute("SELECT enabled FROM frq_participation WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (aid,)).fetchone()
    return bool(row and row[0])


def has_bubble_key(db, aid):
    return db.execute("SELECT 1 FROM frq_bubble_keys WHERE assignment_id=? LIMIT 1", (aid,)).fetchone() is not None


def record(db, aid, on, actor):
    """Record the setting unless it stands already; inside the caller's transaction. Returns whether a row was written."""
    if on and has_bubble_key(db, aid):
        raise Conflict(BUBBLE)
    if bool(on) == enabled(db, aid):
        return False
    db.execute("INSERT INTO frq_participation VALUES(?,?,?,?,?)", (uid(), aid, 1 if on else 0, actor, timestamp()))
    return True


_decimal = finite_decimal


def _number(value):
    value = value.normalize()
    return int(value) if value == value.to_integral() else float(value)


def counted(score):
    """A final grade's score dict as it counts: below its worth (`max_score`), with the participation line added to its adjustments,
    the score its worth and `participation` the line's points; otherwise as it is. Pure; counting it again changes nothing."""
    if not isinstance(score, dict):
        return score
    worth, current = _decimal(score.get("max_score")), _decimal(score.get("score"))
    if worth is None or current is None or current >= worth:
        return score
    base = _decimal(score.get("unclamped_score"))
    points = worth - (current if base is None else base)
    if points <= 0:
        return score
    return {**score, "score": _number(worth), "unclamped_score": _number(worth), "participation": _number(points),
            "adjustments": [*(score.get("adjustments") or []), {"points": _number(points), "reason": REASON}]}


def line(score):
    """[the participation line] of a counted score dict, as a batch line names it (`batch_adjustments.comment_text`), or []."""
    points = (score or {}).get("participation") if isinstance(score, dict) else None
    return [{"points": points, "reason": REASON}] if points else []


def counted_decisions(db, aid, found):
    """`final_decisions`' answer (plan, decisions, skipped) with each decision's final score counted when the plan is graded for
    participation; otherwise exactly the answer given. The decisions are copies: the review or acceptance read stays as it was."""
    plan, decisions, skipped = found
    if not decisions or not enabled(db, aid):
        return found
    shown = []
    for decision in decisions:
        final = decision["final"]
        score = counted(final.get("score"))
        shown.append(decision if score is final.get("score") else {**decision, "final": {**final, "score": score}})
    return plan, shown, skipped


def maxima(db, aid):
    """{question_id: worth} of the plan's pinned questions."""
    return {r[0]: r[1] for r in db.execute("""SELECT p.question_id,json_extract(r.content_json,'$.max_points') FROM frq_assignment_questions p
        JOIN frq_question_revisions r ON r.id=p.question_revision_id WHERE p.assignment_id=?""", (aid,))}


def counted_entry(entry, worth):
    """A `progress.decision_rows` entry (scores as exact text) as it counts, the line's points in `participation`; a grade not final
    or at its worth as it is. The entry keeps no unclamped score, so the line here is the worth less the score."""
    if not entry or not entry.get("kind"):
        return entry
    worth, current = _decimal(worth), _decimal(entry.get("score"))
    if worth is None or current is None or current >= worth:
        return entry
    points = amount(worth - current)
    return {**entry, "score": amount(worth), "adjustments": [*(entry.get("adjustments") or []), points], "participation": points}


def counted_entries(db, aid, decisions):
    """{response_id: entry} of `progress.decision_rows`, counted when the plan is graded for participation; otherwise as given."""
    if not decisions or not enabled(db, aid):
        return decisions
    worth = maxima(db, aid)
    return {rid: counted_entry(entry, worth.get((entry or {}).get("question_id"))) for rid, entry in decisions.items()}


def stored_comment_score(score_json):
    """The counted score dict of a stored score_json text (a review's or a result's), for the Gradescope comment it expects."""
    return counted(json.loads(score_json or "null") or {})


class ParticipationMixin:
    def participation(self, aid):
        """{enabled, bubble_key}: whether the plan is graded for participation, and whether a bubble-sheet key keeps it from being."""
        with connect(self.path) as db:
            self.plan(db, aid)
            return {"enabled": enabled(db, aid), "bubble_key": has_bubble_key(db, aid)}

    def set_participation(self, aid, data, *, actor="workspace"):
        """The instructor's setting ({enabled: true|false}). Nothing is graded again: the next read of the final grades counts them."""
        on = (data or {}).get("enabled")
        if type(on) is not bool:
            raise ValueError("Say whether this assignment is graded for participation (enabled: true or false).")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self.plan(db, aid)
            if record(db, aid, on, actor):
                self.event(db, aid, "participation_set", {"enabled": on}, actor)
        return self.participation(aid)

    # ----- the waiting grades approve themselves (requirement, 2026-11-11) -------------------------------------------------------
    @staticmethod
    def _participation_waits(db, aid):
        """Why nothing is approved by itself on this plan now, or None: a batch or a flag review of it is queued or running (its
        grades are still being made), or its last batch was cancelled or reset (the instructor is choosing what grades it next)."""
        if db.execute("SELECT 1 FROM frq_jobs WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone():
            return "batch"
        if db.execute("SELECT 1 FROM frq_flag_resolutions WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone():
            return "review"
        return None

    def approve_waiting(self, aid):
        """Approve every grade of a plan graded for participation that waits for the instructor's review, as the graders left it;
        how many were approved. Each is checked as a review of the instructor's is (`validate_task`: the rubric, question and paper
        that stand; a completed task), and one whose lines do not add up to its proposed score is left waiting. One transaction
        under the lifecycle lock; one `participation_approved` event names every review written."""
        with self.rosters.lock(), connect(self.path) as db:
            if not enabled(db, aid) or has_bubble_key(db, aid) or self._participation_waits(db, aid) or self.auto_grade_paused(db, aid):
                return 0
            plan = self.plan(db, aid)
            event = self.exams._event(db, plan["event_id"])
            participants = self.exams._participants(db, event)
            self.exams._collection(db, event, participants)
            memo, planned = {}, []
            for row in self._responses(db, plan, event, participants):
                if row["state"] not in WAITING or row["review_status"] or not row["current_result_id"]:
                    continue
                result = db.execute("SELECT * FROM frq_results WHERE id=?", (row["current_result_id"],)).fetchone()
                task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (result["task_id"],)).fetchone())
                try:
                    _, question, _ = self.validate_task(db, task, memo=memo)
                except (ValueError, Conflict, Forbidden, NotFound):
                    continue
                proposal = self.decode_result(result)
                proposed = proposal.get("score") or {}
                if task["status"] != "complete" or proposed.get("score") is None:
                    continue  # nothing to approve: no score was proposed
                rules = question["rubric"]["rules"]
                prior = {a["rubric_item_id"]: a for a in proposal["applications"]}
                applications = [{"rubric_item_id": item["id"], "applied": bool(prior.get(item["id"], {}).get("applied")),
                                 "reason": prior.get(item["id"], {}).get("reason", ""), "evidence": prior.get(item["id"], {}).get("evidence", []),
                                 "actor": "automatic", "origin_error_roots": prior.get(item["id"], {}).get("origin_error_roots", []),
                                 "origin_error_id": prior.get(item["id"], {}).get("origin_error_id")} for item in rules["items"]]
                try:
                    numeric = score(rules, applications, question["content"]["max_points"], proposed.get("adjustments") or [])
                except ValueError:
                    continue
                if amount(numeric["score"]) != amount(proposed["score"]):
                    continue  # its lines do not add up to what was proposed: that is no grade to approve unseen
                planned.append({"response_id": row["id"], "result_id": result["id"], "state": row["state"], "task": task,
                                "transcript": proposal["transcript"], "applications": applications, "score": numeric})
            if not planned:
                return 0
            db.execute("BEGIN IMMEDIATE")
            now, written = timestamp(), []
            for item in planned:
                review_id, task = uid(), item["task"]
                db.execute("INSERT INTO frq_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (review_id, item["response_id"], item["result_id"], task["rubric_id"],
                           task["source_sha256"], encoded(item["transcript"]), encoded(item["applications"]), encoded(item["score"]), "approved", APPROVED,
                           "automatic", now))
                db.execute("UPDATE frq_responses SET current_review_id=?,version=version+1 WHERE id=?", (review_id, item["response_id"]))
                self.event(db, aid, "response_reviewed", {"response_id": item["response_id"], "review_id": review_id, "result_id": item["result_id"],
                                                          "status": "approved", "participation": True}, "automatic")
                written.append({"response_id": item["response_id"], "review_id": review_id, "result_id": item["result_id"], "state": item["state"]})
            self.event(db, aid, "participation_approved", {"count": len(written), "reviews": written}, "automatic")
        LOG.info("Assignment %s is graded for participation: Faculty approved %d waiting grade(s) by itself", aid, len(written))
        return len(written)

    def auto_participation_one(self):
        """One assignment graded for participation per worker turn at most, once its activity changed and nothing of it runs
        (`approve_waiting`). A look reads the plan's whole listing, so it is made at most every `LOOK_EVERY` seconds."""
        now, state = time.monotonic(), self.__dict__.setdefault("_auto_participation", {"looked": -LOOK_EVERY, "markers": {}})
        if now - state["looked"] < LOOK_EVERY:
            return False
        state["looked"] = now
        due = None
        with self.rosters.lock(), connect(self.path) as db:
            plans = [row[0] for row in db.execute("""SELECT a.id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id JOIN course_terms t ON t.id=e.term_id
                WHERE t.is_current=1 AND t.deleted_at IS NULL AND EXISTS(SELECT 1 FROM frq_participation p WHERE p.assignment_id=a.id) ORDER BY a.id""")]
            for aid in plans:
                if not enabled(db, aid) or self._participation_waits(db, aid):
                    continue  # looked at again once its batch or review ends (their end moves the marker)
                marker = self._auto_grade_marker(db, aid)
                if marker != state["markers"].get(aid):
                    due, state["markers"][aid] = aid, marker
                    break
        return bool(due and self.approve_waiting(due))
