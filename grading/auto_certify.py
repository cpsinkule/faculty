"""Late papers certify themselves: the worker's step (requirement, 2026-10-09; GRADING "LATE PAPERS CERTIFY THEMSELVES",
`certification_automatic.py`). A switch in Compute settings, on by default ("Certify late papers by themselves").

The worker looks at each current assignment that has a certification, at most once a minute after its activity changed (and every
`RECHECK_EVERY` anyway while a late paper waits): a cheap catalog test first (a student the latest certification gave no grade of
their own work now has a response, a bubble sheet or a paper), and once that activity has stayed the same for `SETTLE` seconds, the
exact state (`LateCertification.run`, under the lifecycle lock as Certify reads). Nothing is looked at while a batch of the assignment
is waiting or running, or a flag review of it is. An exact look that found nothing of anyone's waits for new activity, one that failed
waits `RECHECK_EVERY`, and one that met a write meanwhile (`changed`) looks again at the next turn (2026-10-09 review). Why late papers still wait is recorded once per reason as
`automatic_certification_not_made` (row keys and reasons only), which Certification and Export shows.

MAKEUP CHANGES CERTIFY THEMSELVES (requirement, 2026-11-14): the cheap test is also true when a student left out until their makeup
is no longer owed one (`progress.makeup_ended`) or a student given 0 with no work is now owed one (`makeups.covering`), and the
activity marker also follows the absences' decisions, the makeups recorded missed and the roster's drops, which write no event of
the assignment.
"""
import json
import logging
import time

from rosters import connect

from .models import digest

LOG = logging.getLogger(__name__)

LOOK_EVERY = 60      # seconds between two looks at one assignment whose activity changed
RECHECK_EVERY = 900  # an assignment whose late papers wait is looked at this often anyway
SETTLE = 300         # the activity must stay the same this long (written work, review and acceptance land in sequence)


class AutoCertifyMixin:
    def auto_certify_state(self, db, aid, settings):
        """What Certification and Export says about late papers certifying themselves. Catalog reads only."""
        latest = db.execute("SELECT created_at FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (aid,)).fetchone()
        found = db.execute("SELECT detail_json,created_at FROM frq_events WHERE assignment_id=? AND kind='automatic_certification_not_made' ORDER BY id DESC LIMIT 1",
                           (aid,)).fetchone()
        waiting, reason = [], ""
        if found and latest and found["created_at"] >= latest["created_at"]:
            said = json.loads(found["detail_json"])
            waiting, reason = said.get("students") or [], said.get("reason") or ""
        # `reason`: why the whole plan waits for the instructor's Certify (its parts changed, a student left the exam), only when one does.
        return {"enabled": bool(settings.get("auto_certify", True)), "waiting": waiting, "settle_seconds": SETTLE, **({"reason": reason} if reason else {})}

    def _auto_certify_marker(self, db, aid):
        """A cheap sign that the assignment's late work may have moved: its latest certification, the batch marker (its events, batches
        and the exam's reviewed papers) and the bubble sheets read or reviewed anywhere."""
        latest = db.execute("SELECT id FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (aid,)).fetchone()
        bubble = (db.execute("SELECT MAX(rowid) FROM bubble_sheets").fetchone()[0], db.execute("SELECT MAX(id) FROM bubble_reviews").fetchone()[0]) \
            if self._has_table(db, "bubble_reviews") and self._has_table(db, "bubble_sheets") else None
        # MAKEUP CHANGES CERTIFY THEMSELVES: an absence decided, a makeup recorded missed and a drop write nothing of the assignment's.
        makeups = tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if self._has_table(db, table) else None
                        for table in ("absence_revisions", "absence_makeups_missed", "roster_drops"))  # append-only, small
        # The day it was given to each student (EXAMS "DATE GIVEN") moves with the exam record's sittings and who sits where.
        given = tuple(tuple(db.execute(f"""SELECT COUNT(*),MAX(x.updated_at) FROM {table} x JOIN frq_assignments a ON a.event_id=x.event_id
            WHERE a.id=?""", (aid,)).fetchone()) for table in ("exam_sittings", "exam_participants"))
        return (latest[0] if latest else None, self._auto_grade_marker(db, aid), bubble, makeups, given)

    @staticmethod
    def _has_table(db, name):
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)).fetchone())

    @staticmethod
    def _busy_plan_ids(db):
        """The plans whose batch or flag review (grading migration 033) is pending or running."""
        busy = {row[0] for row in db.execute("SELECT DISTINCT assignment_id FROM frq_jobs WHERE status IN ('pending','running')")}
        return busy | {row[0] for row in db.execute("SELECT DISTINCT assignment_id FROM frq_flag_resolutions WHERE status IN ('pending','running')")}

    def _late_candidates(self, db, aid):
        """Whether anything of a student the latest certification gave no grade of their own work has come (catalog only): a response
        of theirs where their written part was not certified as their own complete work, or a change of the plan's bubble sheets while
        a bubble part was not. Part-aware (2026-10-09 review): a student certified "scored, a part missing" for the bubble sheet holds
        written responses from the start, which kept this test true and the exact evaluation running every `RECHECK_EVERY` under the
        lifecycle lock. Cheap: no roster payload, no grading rule."""
        from . import progress
        latest = progress.latest_certification(db, aid)
        if not latest:
            return False
        plan = db.execute("SELECT a.id,a.event_id,e.assignment_id source_assignment_id,e.term_id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id WHERE a.id=?",
                          (aid,)).fetchone()
        late = "(c.status IN ('zero','left_out') OR c.reason='missing_part') AND COALESCE(json_extract(c.parts_json,'$.{part}.state'),'')<>'complete'"
        if db.execute(f"""SELECT 1 FROM frq_responses r WHERE r.assignment_id=? AND (
                EXISTS(SELECT 1 FROM frq_certification_students c WHERE c.certification_id=? AND c.section_id=r.section_id AND c.enrollment_id=r.enrollment_id
                       AND {late.format(part='frq')})
                OR NOT EXISTS(SELECT 1 FROM frq_certification_students c WHERE c.certification_id=? AND c.section_id=r.section_id AND c.enrollment_id=r.enrollment_id))
                LIMIT 1""", (aid, latest["id"], latest["id"])).fetchone():
            return True
        if "bubble" in json.loads(latest["parts_json"] or "[]") and db.execute(
                f"SELECT 1 FROM frq_certification_students c WHERE c.certification_id=? AND {late.format(part='bubble')} LIMIT 1", (latest["id"],)).fetchone() \
                and progress.bubble_marker(db, dict(plan)) != latest["bubble_sha256"]:
            return True
        # MAKEUP CHANGES CERTIFY THEMSELVES: a makeup that ended (a drop included), or one now owed by a student certified 0.
        if progress.makeup_ended(db, dict(plan), latest["id"]):
            return True
        zeros = [tuple(r) for r in db.execute("""SELECT section_id,enrollment_id FROM frq_certification_students WHERE certification_id=?
            AND status='zero' AND reason IN ('no_paper','absent')""", (latest["id"],))]
        if zeros:
            from absences import makeups
            return bool(makeups.covering(db, plan["term_id"], plan["event_id"], plan["source_assignment_id"], zeros))
        return False

    def auto_certify_one(self):
        """One look per worker turn at most: certify an assignment's settled late papers, or look at the one most due."""
        settings = self.settings()["settings"]
        certification = getattr(self, "certification", None)
        if not settings.get("auto_certify", True) or certification is None:
            return False
        now, state = time.monotonic(), getattr(self, "_auto_certify", None)
        if state is None:
            state = self._auto_certify = {}
        with self.rosters.lock(), connect(self.path) as db:
            plans = [row[0] for row in db.execute("""SELECT DISTINCT a.id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id
                JOIN course_terms t ON t.id=e.term_id JOIN frq_certifications c ON c.assignment_id=a.id
                WHERE t.is_current=1 AND t.deleted_at IS NULL ORDER BY a.id""")]
            busy = self._busy_plan_ids(db)
            due = None
            for aid in plans:
                mine = state.setdefault(aid, {})
                if aid in busy:
                    mine["since"] = now  # its batch or flag review runs: the settle starts again afterwards
                    continue
                if now - mine.get("looked", -LOOK_EVERY) < LOOK_EVERY:
                    continue
                marker = self._auto_certify_marker(db, aid)
                if marker != mine.get("marker"):
                    mine.update(marker=marker, since=now, looked=now)
                    continue
                mine["looked"] = now
                if mine.get("idle") == marker:
                    continue  # its exact state found nothing of anyone's come: only new activity (a new marker) can change that
                if mine.get("done") == marker and now - mine.get("checked", 0) < RECHECK_EVERY:
                    continue  # looked at exactly this activity already
                if now - mine.get("since", now) < SETTLE:
                    continue
                if not self._late_candidates(db, aid):
                    mine.update(done=marker, checked=now)
                    continue
                due = aid
                break
        for gone in set(state) - set(plans):
            state.pop(gone, None)
        if not due:
            return False
        mine = state[due]
        from .certification_automatic import LateCertification
        try:
            if certification.reach_full_credit(due):
                return True  # GRADING "A FULL-CREDIT LINE REACHES LATER PAPERS": the line first; these grades are new activity, so the settle starts again
            outcome = LateCertification(certification).run(due)
        except Exception:
            # A failing evaluation is looked at again after RECHECK_EVERY, not every minute (2026-10-09 review); `_guarded` logs it.
            mine.update(done=mine.get("marker"), checked=now)
            raise
        with self.rosters.lock(), connect(self.path) as db:
            marker = self._auto_certify_marker(db, due)
            # `changed` read something written meanwhile and recorded nothing: the next look reads again, not RECHECK_EVERY later.
            mine.update(done=None if outcome["state"] in ("done", "changed") else marker, checked=now)
            quiet = outcome["state"] == "waiting" and not outcome.get("late") and not outcome.get("waiting") and not outcome.get("reason")
            mine["idle"] = marker if quiet else None
            if outcome["state"] in ("waiting", "done"):
                self._auto_certify_waiting(db, due, outcome)
        if outcome["state"] == "done":
            LOG.info("Assignment %s: Faculty certified %d student(s) by itself, %d for a makeup's change (%s markers)", due, len(outcome["late"]),
                     len(outcome.get("changes") or {}), outcome["markers"])
            return True
        return False

    def auto_full_credit_one(self):
        """GRADING "A FULL-CREDIT LINE REACHES LATER PAPERS": one assignment per worker turn at most, once its activity changed and
        nothing of it runs. A look at the receipts costs a scan of the events, so it is made at most every `LOOK_EVERY` seconds."""
        certification = getattr(self, "certification", None)
        now, state = time.monotonic(), self.__dict__.setdefault("_auto_full_credit", {"looked": -LOOK_EVERY, "markers": {}})
        if certification is None or now - state["looked"] < LOOK_EVERY:
            return False
        state["looked"] = now
        due = None
        with self.rosters.lock(), connect(self.path) as db:
            plans = [row[0] for row in db.execute("""SELECT DISTINCT a.id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id JOIN course_terms t ON t.id=e.term_id
                JOIN frq_events v ON v.assignment_id=a.id AND v.kind='grades_adjusted' WHERE t.is_current=1 AND t.deleted_at IS NULL ORDER BY a.id""")]
            busy = self._busy_plan_ids(db)
            for aid in plans:
                marker = self._auto_grade_marker(db, aid)
                if aid not in busy and marker != state["markers"].get(aid):
                    due, state["markers"][aid] = aid, marker
                    break
        return bool(due and certification.reach_full_credit(due))

    def _auto_certify_waiting(self, db, aid, outcome):
        """Records why late papers wait (row keys and reasons only: no names or UINs), once per reason since the latest certification;
        an empty list is recorded only to clear one that said otherwise."""
        detail = {"reason": outcome.get("reason") or "", "students": sorted(outcome.get("waiting") or [], key=lambda w: w["row"])}
        key = digest(detail)
        latest = db.execute("SELECT created_at FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (aid,)).fetchone()
        last = db.execute("""SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='automatic_certification_not_made' AND created_at>=?
            ORDER BY id DESC LIMIT 1""", (aid, latest["created_at"] if latest else "")).fetchone()
        said = json.loads(last["detail_json"]) if last else None
        if (said or {}).get("key") == key or (not said and not detail["reason"] and not detail["students"]):
            return
        self.event(db, aid, "automatic_certification_not_made", {**detail, "key": key}, "automatic")
