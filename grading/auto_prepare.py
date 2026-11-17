"""Answer areas and answer keys that confirm themselves (requirement, 2026-10-22).

The worker looks at each current assignment whose record changed (`_auto_prepare_marker`) and, for every question that has no grade and no
decision yet and while no batch of the assignment is waiting or running:

1. ANSWER AREAS CONFIRM THEMSELVES. A finished check that suggests areas (`needs_confirmation`, and `needs_review`, whose issues
   the instructor acknowledged with "it's fine" every time) is confirmed exactly as suggested, through the ordinary
   `_confirm_location` with actor `automatic`: its template, printed number and suggested areas. A failed check, a check without
   suggested areas, one with no printed number, and areas that would change privacy masks still wait for the instructor.
2. KEYS ARE READ. A question with a confirmed area, no saved key, no key its rubric proposes and no reading yet has its key read
   from the blank exam, the ordinary reading (`_queue_key_readings`, the button's path, conditions and refusals), request ID
   `auto-key:<location confirmation>:<question>`, so one confirmation asks once. A refusal (no reviewer model) leaves the button.
3. ANSWER KEYS SAVE THEMSELVES. The key the page shows as "not saved yet" (the rubric's proposal, else the latest reading under
   the current rubric: `key_stands` 'unsaved') is saved through the ordinary `save_answer_key` with actor `automatic`.

A question that already has grades keeps its proposals for the instructor: a new question revision would send its grades back as
new work. Everything a confirmation and a saved key do afterwards (the carried area, the key read after a drafted rubric's area,
the rubric check that starts by itself) is unchanged, and the page's buttons stay for whatever waits.
"""
import logging
import time

from werkzeug.exceptions import Conflict, NotFound

from rosters import connect
from . import answers
from .auto_grade import current_plan_ids
from .rubric_generation import key_stands

LOG = logging.getLogger(__name__)

LOOK_EVERY = 30      # seconds between two looks at one assignment whose record changed
RECHECK_EVERY = 900  # every current assignment is looked at this often anyway
KEYS_REQUEST = "auto-key"
CONFIRMABLE = ("needs_confirmation", "needs_review")
NOTE = "Confirmed automatically as suggested (answer areas confirm themselves, requirement, 2026-10-22)."


class AutoPrepareMixin:
    def _auto_prepare_marker(self, db, aid):
        """A cheap sign that something which may ready an area or a key happened: the plan's record, its checks and its readings."""
        plan = db.execute("SELECT version FROM frq_assignments WHERE id=?", (aid,)).fetchone()
        return (plan["version"], db.execute("SELECT MAX(id) FROM frq_events WHERE assignment_id=?", (aid,)).fetchone()[0],
                db.execute("SELECT MAX(updated_at) FROM frq_location_checks WHERE assignment_id=?", (aid,)).fetchone()[0],
                db.execute("SELECT MAX(updated_at) FROM frq_rubric_generations WHERE assignment_id=?", (aid,)).fetchone()[0])

    def auto_prepare_one(self):
        """One look per worker turn at most, at the current assignment most due for one. True when it confirmed, queued or saved."""
        now, state = time.monotonic(), getattr(self, "_auto_prepare", None)
        if state is None:
            state = self._auto_prepare = {}
        with self.rosters.lock(), connect(self.path) as db:
            plans = current_plan_ids(db)
            due = None
            for aid in plans:
                mine = state.setdefault(aid, {})
                marker = self._auto_prepare_marker(db, aid)
                if now - mine.get("looked", -RECHECK_EVERY) < (LOOK_EVERY if marker != mine.get("marker") else RECHECK_EVERY):
                    continue
                due = (aid, marker)
                break
        for gone in set(state) - set(plans):
            state.pop(gone, None)
        if not due:
            return False
        aid, marker = due
        state[aid].update(looked=now, marker=marker)
        return self.auto_prepare(aid)

    def auto_prepare(self, aid):
        """Confirm the suggested areas, read the keys still unread and save the keys shown of one assignment (module docstring)."""
        confirmed = self._auto_confirm_areas(aid)
        if confirmed:
            LOG.info("Assignment %s: %d answer areas confirmed automatically", aid, len(confirmed))
            self.after_answer_areas(aid, confirmed)  # as after the instructor's own confirmation (`confirm_location`)
        queued = self._auto_read_keys(aid)
        saved = self._auto_save_keys(aid)
        if saved:
            LOG.info("Assignment %s: %d answer keys saved automatically", aid, saved)
        return bool(confirmed or queued or saved)

    def _auto_prepare_questions(self, db, aid):
        """The questions of the assignment the worker may prepare now: none while a batch of it waits or runs, never one with grades."""
        if db.execute("SELECT 1 FROM frq_jobs WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone():
            return []
        return [row[0] for row in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY rowid", (aid,))
                if not self.graded_yet(db, aid, row[0])]

    def _auto_confirm_areas(self, aid):
        confirmed = []
        with self.rosters.lock():
            with connect(self.path) as db:
                qids = self._auto_prepare_questions(db, aid)
            for qid in qids:
                # One confirmation per transaction, as the instructor's own: one that is refused leaves only its question waiting.
                with connect(self.path) as db:
                    q = self.question(db, aid, qid)
                    status = self.location_status(db, aid, q)
                    context, result = status["context"], status["result"] or {}
                    regions = result.get("regions") or []
                    # Areas read from Gradescope's outline are the boxes Gradescope itself grades with (GRADING "ANSWER AREAS CONFIRM
                    # THEMSELVES" › BOXES FROM GRADESCOPE, 2026-10-25): a reading of the blank that failed does not hold them back.
                    gradescope = result.get("regions_from") == "gradescope" and regions == q["content"]["regions"]
                    confirmable = status["status"] in CONFIRMABLE or (gradescope and status["status"] == "failed")
                    if not confirmable or not context["template"] or not regions or not context["printed_number"]:
                        continue
                    if regions != q["content"]["regions"] and any(r.get("privacy_masks") for r in q["content"]["regions"]):
                        continue  # Changing privacy masks stays the instructor's look.
                    manual = status["status"] != "needs_confirmation"
                    try:
                        self._confirm_location(db, aid, q, status, {"confirm_locations": True, "template_id": context["template"]["id"],
                            "printed_number": context["printed_number"], "regions": regions, "notes": NOTE if manual else "",
                            "manual_review": manual}, actor="automatic")
                    except (ValueError, Conflict, NotFound) as error:
                        LOG.info("Assignment %s: the answer area of question %s waits for the instructor: %s", aid, qid, error)
                        db.rollback()
                        continue
                    confirmed.append(qid)
        return confirmed

    def _auto_read_keys(self, aid):
        with self.rosters.lock(), connect(self.path) as db:
            facts = self._setup_facts(db, aid, self._auto_prepare_questions(db, aid))
        wanted = [(qid, fact["location_check"]["review_id"]) for qid, fact in facts.items()
                  if fact["location_check"]["status"] == "confirmed" and key_stands(fact["answer"]) == "unread"]
        queued = []
        for qid, confirmation in wanted:
            try:
                queued += self._queue_key_readings(aid, f"{KEYS_REQUEST}:{confirmation}", [qid], actor="automatic")
            except (ValueError, Conflict, NotFound):
                continue  # No reviewer model, or nothing to read the problem from: the button stays.
        return queued

    def _auto_save_keys(self, aid):
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            facts = self._setup_facts(db, aid, self._auto_prepare_questions(db, aid))
            entries = []
            for qid, fact in facts.items():
                answer = fact["answer"]
                if key_stands(answer) != "unsaved":
                    continue
                # The key the page shows (`shownKey`): the rubric's proposal, else the latest reading under the current rubric.
                try:
                    key = answers.clean_key(answer["proposed_key"] or answer["read_key"]["key"])
                except ValueError:
                    continue  # A proposal the editor would refuse as it stands waits for the instructor's edit.
                entries.append({"question_id": qid, "version": self.question(db, aid, qid)["version"], "answer_key": key})
        if not entries:
            return 0
        try:
            self.save_answer_key(aid, {"version": plan["version"], "questions": entries}, actor="automatic")
        except (ValueError, Conflict, NotFound) as error:
            LOG.info("Assignment %s: the answer keys shown wait for the instructor: %s", aid, error)
            return 0
        return len(entries)
