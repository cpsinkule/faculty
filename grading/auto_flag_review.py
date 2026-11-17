"""The flag review that runs by itself (requirement, 2026-10-11). Measured that day on the two
assignments graded since the flag review existed: pressed, it left them 33 of 137 held grades (Quiz 3) and 14 of 58 (Quiz 4).

After a grading batch of an assignment finishes, the worker looks at the held grades a flag review would be sent
(`FlagResolutionMixin.candidates`, unchanged, so everything reserved to the instructor is left out exactly as for the button) and keeps
the FRESH ones: a grade from a grading batch that finished within `WINDOW`, that no flag review was ever sent. `SETTLE` seconds later,
no batch of the assignment having started meanwhile, it requests the ordinary flag review for those of them that still wait
(`queue_flag_resolution`, actor `automatic`, a request ID derived from the set), so a decision the instructor made in between stands. Everything a flag review does is unchanged: its model, prompt and rule, its carrier
batch, the checks its rulings go through again and the acceptance gate.

It never starts one while a batch or a flag review of the assignment is queued or running, while a batch is about to start by itself
(one review then follows both), while the switch is off, or while the assignment's last batch was cancelled or reset (the instructor
is choosing what grades it next). It never follows a flag review's own carrier batch or a "Recalculate points" batch, and it never
sends a grade twice: a grade any earlier review was sent, whether that review settled it, left it, failed or was stopped, is the
instructor's or the button's from then on (PIPELINE: an uncertain outcome is never retried automatically). Held grades older than
`WINDOW` are not swept up either, which is also what keeps the batches graded before this existed with the button.

A review that cannot start (no model that reads images is chosen) is recorded once as `automatic_flag_review_not_started` with the
reason, which the Review step shows; nothing is tried again until the grades or the settings change.

None starts by itself on a plan graded for participation (requirement, 2026-11-11): its held grades are approved as they are. The button still sends one.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from werkzeug.exceptions import Conflict

from rosters import connect
from . import participation
from .auto_grade import CARRIERS, current_plan_ids
from .flag_resolution import RULE
from .models import digest

LOG = logging.getLogger(__name__)

LOOK_EVERY = 60               # seconds between two looks at one assignment
SETTLE = 120                  # the fresh held grades must stay the same this long before their review is requested
WINDOW = timedelta(hours=24)  # a batch that finished longer ago is no longer followed: its held grades wait for the button
PREFIX = "auto:"              # request IDs of automatic flag reviews
REFUSED = "automatic_flag_review_not_started"


class AutoFlagReviewMixin:
    def _flag_review_waits(self, db, aid):
        """Why no flag review starts by itself for this assignment now, or None. Catalog reads only."""
        if db.execute("SELECT 1 FROM frq_jobs WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone():
            return "batch"
        if db.execute("SELECT 1 FROM frq_flag_resolutions WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone():
            return "review"
        if participation.enabled(db, aid):
            return "participation"  # its waiting grades are approved as they are (GRADING "THE WAITING GRADES APPROVE THEMSELVES")
        return self.auto_grade_paused(db, aid)

    def _flag_review_fresh(self, db, aid, found=None):
        """The held grades a flag review would be sent now that the automatic one follows: rows of `candidates` whose grade came from
        a grading batch (never a carrier or "Recalculate points" batch) that finished within `WINDOW`, and that no flag review of the
        plan was ever sent. The caller holds the lifecycle lock; `found` is `candidates`' answer when the caller already has it."""
        by_question, _ = found if found is not None else self.candidates(db, aid)
        if not by_question:
            return []
        seen = set()
        for (raw,) in db.execute("SELECT input_json FROM frq_flag_resolutions WHERE assignment_id=?", (aid,)):
            seen.update(r["result_id"] for q in json.loads(raw)["questions"] for r in q["responses"])
        recent = {row[0] for row in db.execute(f"""SELECT id FROM frq_jobs WHERE assignment_id=? AND operation<>'rescore' AND request_id NOT LIKE '{CARRIERS}'
            AND status IN ('review_ready','failed') AND cancel_requested=0 AND updated_at>?""", (aid, (datetime.now(timezone.utc) - WINDOW).isoformat()))}
        return [row for rows in by_question.values() for row in rows if row["job_id"] in recent and row["result_id"] not in seen]

    @staticmethod
    def _flag_review_key(fresh, version):
        """What a refusal was about: these grades under these saved settings."""
        return digest([sorted(row["result_id"] for row in fresh), RULE, version])

    def auto_flag_review_state(self, db, aid, current, found=None):
        """What the Review step says about the automatic flag review of this assignment. Catalog reads only; `current` is `settings()`."""
        enabled = bool(current["settings"].get("auto_flag_review", True))
        waits = self._flag_review_waits(db, aid)
        due = self._flag_review_fresh(db, aid, found) if enabled and not waits else []
        not_started = None
        if due:
            refused = db.execute("SELECT detail_json,created_at FROM frq_events WHERE assignment_id=? AND kind=? ORDER BY id DESC LIMIT 1", (aid, REFUSED)).fetchone()
            detail = json.loads(refused["detail_json"]) if refused else {}
            if detail.get("key") == self._flag_review_key(due, current["version"]):
                not_started = {"reason": detail.get("reason", ""), "at": refused["created_at"]}
        return {"enabled": enabled, "waits": waits, "not_started": not_started, "settle_seconds": SETTLE,
                "due": {"responses": len(due), "response_ids": sorted(row["response_id"] for row in due)}}

    @staticmethod
    def _flag_review_marker(db, aid):
        """A cheap sign that the fresh held grades may have changed: a batch of the plan moved, or a flag review was requested."""
        return (db.execute("SELECT MAX(updated_at) FROM frq_jobs WHERE assignment_id=?", (aid,)).fetchone()[0],
                db.execute("SELECT MAX(rowid) FROM frq_flag_resolutions WHERE assignment_id=?", (aid,)).fetchone()[0])

    def auto_flag_review_one(self):
        """One look per worker turn at most: request a settled flag review, or look at the one assignment most due for a look."""
        current = self.settings()
        if not current["settings"].get("auto_flag_review", True):
            return False
        now, state = time.monotonic(), getattr(self, "_auto_flag_review", None)
        if state is None:
            state = self._auto_flag_review = {}
        batches = getattr(self, "_auto_grade", None) or {}
        with self.rosters.lock(), connect(self.path) as db:
            plans = current_plan_ids(db)
            due = None
            for aid in plans:
                mine = state.setdefault(aid, {})
                if self._flag_review_waits(db, aid) or (batches.get(aid) or {}).get("pending"):
                    mine.pop("pending", None)  # A batch or a review runs, or a batch is about to: the settle starts again afterwards.
                    mine.pop("marker", None)
                    continue
                pending = mine.get("pending")
                if pending and now - pending["since"] >= SETTLE:
                    due = ("start", aid)
                    break
                marker = (current["version"], self._flag_review_marker(db, aid))  # Saved settings are new ground too (a model chosen).
                if pending or marker == mine.get("marker") or now - mine.get("looked", -LOOK_EVERY) < LOOK_EVERY:
                    continue
                due = due or ("look", aid, marker)
        for gone in set(state) - set(plans):
            state.pop(gone, None)
        if not due:
            return False
        aid, mine = due[1], state[due[1]]
        if due[0] == "look":
            mine["looked"], mine["marker"] = now, due[2]
            with self.rosters.lock(), connect(self.path) as db:
                fresh = self._flag_review_fresh(db, aid)
            if fresh:
                key = self._flag_review_key(fresh, current["version"])
                if mine.get("refused") != key:  # Refused for exactly these grades and settings: the reason is on the Review step.
                    mine["pending"] = {"key": key, "since": now, "results": sorted(row["result_id"] for row in fresh)}
            return False  # A look is no work done: the turn goes on to the worker's other queues.
        pending = mine.pop("pending")
        try:
            endpoint, _ = self._resolution_model()
        except ValueError as error:
            self._auto_flag_review_refused(aid, mine, pending, error)
            return False
        try:
            # Exactly the grades that settled, less any the instructor decided meanwhile; nothing that arrived since (its own look follows).
            started = self.queue_flag_resolution(aid, {"request_id": f"{PREFIX}{aid}:{digest([pending['results'], endpoint['name'], RULE])[:32]}"},
                                                 actor="automatic", only=set(pending["results"]))
        except (Conflict, ValueError):
            mine.pop("marker", None)  # A review started meanwhile, or none of these grades still waits: look again when something moves.
            return False
        LOG.info("Assignment %s: flag review %s requested automatically for %d held grades", aid, started["id"], started["responses"])
        return True

    def _auto_flag_review_refused(self, aid, mine, pending, error):
        reason = str(getattr(error, "description", error))
        mine["refused"] = pending["key"]
        with self.rosters.lock(), connect(self.path) as db:
            last = db.execute("SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind=? ORDER BY id DESC LIMIT 1", (aid, REFUSED)).fetchone()
            if last and json.loads(last[0]).get("key") == pending["key"]:
                return  # Already recorded for exactly these grades and settings (before a restart).
            LOG.info("Assignment %s: a flag review could not start automatically: %s", aid, reason)
            self.event(db, aid, REFUSED, {"reason": reason, "responses": len(pending["results"]), "key": pending["key"]}, "automatic")
