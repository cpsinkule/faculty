"""MC corrections (requirement, 2026-10-22). Faculty's scoring of a bubble sheet is the grade; Gradescope's own scoring is the check.

When an MC check read (`gradescope_mc`) finds a question of a student's sheet that Gradescope scores differently from Faculty, in the
same version, the sender corrects Gradescope by itself: on that question's grading page it leaves the rubric item Gradescope applied
as it is and sets the point adjustment that makes Gradescope's score Faculty's (read on the real site 2026-10-22: a bubble-sheet
question's grading page is the written questions' SubmissionGrader, one rubric item per answer and "Blank"; Sophie Shaw's Q6 in
140-508 went from 0 to 4 with Blank applied and +4, and the exam's Download Grades file then showed 54). It reads the page back; when
every correction of the plan is settled it reads the scores again, so the comparison shows what Gradescope shows now. Every
correction is listed for the instructor to look at (the Gradescope panels and Certification). A question nobody graded there
(MC CORRECTIONS "UNGRADED", 2026-10-28) gets the rubric item that is the answer Faculty read, so it shows Faculty's score too.

Never: a version Gradescope filed differently (or "?"), a sheet on Gradescope that is not the one Faculty uploaded, a question
someone adjusted by hand there, a score that changed since the read, or one Gradescope's bounds cannot give. Each is refused with
its reason, and the difference stays a check to confirm. Nothing is resent by itself: a correction that did not land, or whose
outcome is unknown and could not be read, fails; the instructor's Check MC tries it again.
"""
import json
import logging
import time
from decimal import Decimal

from course_storage import timestamp
from rosters import connect

from . import gradescope_web as gw
from .gradescope_mc import NOT_GRADED, TRIES, compare, dec, points_str, why
from .models import uid

LOG = logging.getLogger(__name__)
OPEN = ("pending", "sending", "uncertain")
STANDING = OPEN + ("accepted",)  # a correction that stands for its question: never queued twice
READ_TRIES = 5  # failed reads before anything was written (a page Gradescope would not show), a minute apart, before it fails


def answer_letters(description):
    """The answer a bubble-sheet rubric item stands for, as sorted letters: "B" is "B", "A, B, C, D, E" is "ABCDE", "Blank" is ""
    (read on the real site 2026-10-28: one item per answer Gradescope grouped, described by its letters); None for anything else.
    An item Faculty labelled (MC LABELS: "A: incorrect (correct answer: B)") stands for the letters before its colon."""
    text = str(description or "").split(":", 1)[0].strip()
    if text.casefold() == "blank":
        return ""
    letters = [part.strip() for part in text.split(",")]
    return "".join(sorted(letters)) if letters and all(len(p) == 1 and p in "ABCDE" for p in letters) else None


def correctable(differ):
    """The questions of one student's difference (`gradescope_mc.compare`) Faculty can correct on Gradescope: both scores known, in
    the version both sides filed the sheet under, and the questions nobody graded there (gradescope points None).
    [(number, gradescope points, faculty points)]."""
    if (differ.get("gradescope") or {}).get("version") != (differ.get("faculty") or {}).get("version") or not (differ.get("faculty") or {}).get("version"):
        return []
    found = []
    for question in differ.get("questions") or []:
        theirs, ours = dec(question.get("gradescope")), dec(question.get("faculty"))
        if question.get("ungraded") and ours is not None:
            found.append((int(question["number"]), None, ours))  # nobody graded it there: Faculty's score goes there too
        elif theirs is not None and ours is not None and theirs != ours:
            found.append((int(question["number"]), theirs, ours))
    return found


class McCorrectionMixin:
    """Mixed into `GradescopeSender` beside `McCheckMixin`: queues a correction after a read, works it as a sender unit (kind
    "mc_corrections"), and lists the corrections for the pages."""

    # --- queueing, right after a read settles ----------------------------------------------------------------------------------
    def _mc_correct_after_read(self, read_id):
        """Queue a correction for every question the read found Gradescope scoring differently. Never raises: the read stands."""
        try:
            self._mc_correct_queue(read_id)
        except Exception:
            LOG.exception("Queueing corrections of Gradescope's multiple-choice scores stopped unexpectedly")

    def _mc_correct_queue(self, read_id):
        with self.rosters.lock():
            with connect(self.path) as db:
                read = db.execute("SELECT * FROM frq_gradescope_mc_checks WHERE id=? AND status='read'", (read_id,)).fetchone()
                if not read:
                    return 0
                read, aid = dict(read), read["assignment_id"]
            view = self.frq._bubble_view(aid)[1]  # Faculty's scoring as it is now (the lock is re-entered, as Certification does)
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                members = {r[1]: r[0] for r in db.execute("""SELECT m.section_id,m.enrollment_id FROM roster_memberships m JOIN course_sections s ON s.id=m.section_id
                    WHERE s.term_id=?""", (self.frq.plan(db, aid)["term_id"],))}
                uploads = {r["enrollment_id"]: dict(r) for r in db.execute("""SELECT * FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND status='accepted'
                    ORDER BY created_at,rowid""", (aid,))}
                found = compare((view or {}).get("sheets") or [], {read["section_id"]: json.loads(read["scores_json"])}, members,
                                {e: u["sheet_id"] for e, u in uploads.items()})
                queued, now = 0, timestamp()
                for differ in found["differ"]:
                    enrollment = differ["row"].split(":", 1)[1]
                    upload = uploads.get(enrollment)
                    if not upload or upload["section_id"] != read["section_id"] or upload["course_id"] != read["course_id"]:
                        continue  # not a sheet Faculty put in this section's exam: nothing of Faculty's to correct there
                    for number, theirs, ours in correctable(differ):
                        earlier = [r["status"] for r in db.execute("""SELECT status FROM frq_gradescope_mc_corrections WHERE assignment_id=? AND enrollment_id=?
                            AND question_number=? AND version=? AND faculty_points=? AND sheet_upload_id=?""",
                                                               (aid, enrollment, number, differ["faculty"]["version"], points_str(ours), upload["id"]))]
                        if any(s in STANDING for s in earlier):
                            continue
                        if earlier and read["actor"] != "workspace":
                            continue  # one that failed or was refused is tried again only on the instructor's own Check MC
                        db.execute("""INSERT INTO frq_gradescope_mc_corrections(id,read_id,assignment_id,section_id,enrollment_id,sheet_upload_id,course_id,version,
                            question_number,gradescope_points,faculty_points,adjustment,status,attempts,not_before,receipt_json,error,actor,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,'pending',0,NULL,NULL,'','automatic',?,?)""",
                                   (uid(), read_id, aid, upload["section_id"], enrollment, upload["id"], read["course_id"], differ["faculty"]["version"],
                                    number, "" if theirs is None else points_str(theirs), points_str(ours), now, now))
                        queued += 1
                if queued:
                    self.frq.event(db, aid, "gradescope_mc_corrections", {"read_id": read_id, "questions": queued}, "automatic")
        if queued:
            self.wake.set()
        return queued

    # --- the sender's unit ----------------------------------------------------------------------------------------------------
    def _mc_fix_next(self, db, skip, ids, now):
        return db.execute(f"""SELECT 'mc_corrections' kind,* FROM frq_gradescope_mc_corrections WHERE status IN ('pending','uncertain') {skip}
            AND (not_before IS NULL OR not_before<=?) ORDER BY created_at,rowid LIMIT 1""", (*ids, now)).fetchone()

    def _mc_fix_set(self, unit_id, status, *, error="", receipt=None, not_before=None, adjustment=None, settle=True):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            sets, args = ["status=?", "error=?", "updated_at=?", "not_before=?"], [status, error[:2000], timestamp(), not_before]
            if receipt is not None:
                sets.append("receipt_json=?")
                args.append(json.dumps(receipt, sort_keys=True))
            if adjustment is not None:
                sets.append("adjustment=?")
                args.append(adjustment)
            changed = db.execute(f"UPDATE frq_gradescope_mc_corrections SET {','.join(sets)} WHERE id=? AND status IN ('pending','sending','uncertain')",
                                 (*args, unit_id)).rowcount
            if changed and settle and status in ("accepted", "failed", "refused"):
                self._mc_fix_settled(db, unit_id)

    def _mc_fix_settled(self, db, unit_id):
        """When the plan's last open correction settles and one landed: read Gradescope's scores again (once), so the pages compare
        what Gradescope shows now."""
        aid = db.execute("SELECT assignment_id FROM frq_gradescope_mc_corrections WHERE id=?", (unit_id,)).fetchone()[0]
        if db.execute(f"SELECT 1 FROM frq_gradescope_mc_corrections WHERE assignment_id=? AND status IN ({','.join('?' * len(OPEN))}) LIMIT 1",
                      (aid, *OPEN)).fetchone():
            return
        landed = db.execute("""SELECT 1 FROM frq_gradescope_mc_corrections c WHERE c.assignment_id=? AND c.status='accepted' AND c.updated_at >
            COALESCE((SELECT MAX(m.created_at) FROM frq_gradescope_mc_checks m WHERE m.assignment_id=c.assignment_id AND m.section_id=c.section_id), '') LIMIT 1""",
                            (aid,)).fetchone()
        if landed:
            self._mc_queue(db, aid, None, "automatic")
            self.wake.set()

    def _mc_fix_unit(self, unit):
        """One correction. Never raises: a busy Gradescope or a refused sign-in waits like any unit; everything else settles it."""
        from .gradescope_sync import PAUSE, Processing, PROCESSING_WAIT, later
        if unit["status"] == "pending":
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if not db.execute("UPDATE frq_gradescope_mc_corrections SET status='sending',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'",
                                  (timestamp(), unit["id"])).rowcount:
                    return
            unit = {**unit, "status": "sending", "attempts": unit["attempts"] + 1}
        try:
            self._mc_fix_work(unit)
        except gw.SignInRefused as error:
            self._paused = (time.monotonic() + PAUSE, self._sign_in_stamp())
            self._drop_session()
            self._mc_fix_wait(unit, str(error), later(PAUSE))
        except gw.GradescopeBusy as error:
            delay = self._pause(error)
            if unit["attempts"] >= TRIES and unit["status"] != "uncertain":
                self._mc_fix_set(unit["id"], "failed", error=f"Gradescope stayed busy through {unit['attempts']} tries. Check MC again when it is quieter.")
            else:
                self._mc_fix_wait(unit, f"Gradescope is busy; Faculty tries again in {delay} s.", later(delay))
        except Processing as error:
            if unit["status"] != "uncertain" and unit["attempts"] >= READ_TRIES:
                self._mc_fix_set(unit["id"], "failed", error=f"{error} Check MC again to try once more.")
            else:
                self._mc_fix_wait(unit, str(error), later(PROCESSING_WAIT))
        except gw.GradescopeError as error:
            # A read before anything was written (every write is caught where it is made): nothing changed on Gradescope.
            if unit["status"] == "uncertain" or unit["attempts"] < READ_TRIES:
                self._mc_fix_wait(unit, str(error), later(60))
            else:
                self._mc_fix_set(unit["id"], "failed", error=f"{error} Check MC again to try once more.")
        except Exception:
            LOG.exception("Correcting a multiple-choice score on Gradescope stopped unexpectedly")
            if unit["status"] == "uncertain":
                self._mc_fix_wait(unit, "Faculty stopped unexpectedly while reading it.", later(60))
            else:
                self._mc_fix_set(unit["id"], "failed", error="Faculty stopped unexpectedly before changing anything on Gradescope. Check MC again to try once more.")

    def _mc_fix_wait(self, unit, reason, when):
        """Nothing was written by this turn: a claimed correction waits again; an uncertain one stays uncertain, for a read only."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("""UPDATE frq_gradescope_mc_corrections SET status=CASE WHEN status='uncertain' THEN 'uncertain' ELSE 'pending' END,not_before=?,
                error=?,updated_at=? WHERE id=? AND status IN ('pending','sending','uncertain')""", (when, f"Waiting: {reason}"[:2000], timestamp(), unit["id"]))

    def _mc_fix_page(self, session, unit, upload):
        """The question's grading page on Faculty's own sheet, found by its number in the version's outline."""
        questions = [q for q in session.outline(upload["course_id"], upload["gradescope_assignment_id"])["questions"]
                     if not q.get("parent_id") and str(q.get("index")) == str(unit["question_number"])]
        if len(questions) != 1:
            return None, f"Gradescope's Version {unit['version']} has no single question {unit['question_number']}."
        question_id = str(questions[0]["id"])
        qsub = (json.loads(upload["receipt_json"] or "{}").get("questions") or {}).get(question_id)
        if not qsub:
            qsub = session.submission(upload["course_id"], upload["gradescope_assignment_id"], upload["gradescope_submission_id"])["questions"].get(question_id)
        if not qsub:
            return None, f"Gradescope has no question {unit['question_number']} on this sheet."
        return session.grader(upload["course_id"], question_id, qsub), None

    def _mc_fix_work(self, unit):
        from .gradescope_sync import text_amount
        with connect(self.path) as db:
            upload = dict(db.execute("SELECT * FROM frq_gradescope_sheet_uploads WHERE id=?", (unit["sheet_upload_id"],)).fetchone())
        session = self._session_now()
        wanted, read = Decimal(unit["faculty_points"]), (Decimal(unit["gradescope_points"]) if unit["gradescope_points"] else None)
        if unit["status"] == "uncertain":
            return self._mc_fix_check(session, unit, upload, wanted, read, after_write=False)
        if upload["status"] != "accepted" or not self._still_ours(session, upload):
            return self._mc_fix_set(unit["id"], "refused", error="The sheet on Gradescope is no longer the one Faculty uploaded; nothing was changed.")
        grader, problem = self._mc_fix_page(session, unit, upload)
        if problem:
            return self._mc_fix_set(unit["id"], "refused", error=problem)
        score, points = grader["score"], dec(grader["points"]) if grader["points"] not in (None, "") else None
        if score is not None and score == wanted:
            return self._mc_fix_set(unit["id"], "accepted", error="It was already as Faculty scores it.", receipt=self._mc_fix_receipt(grader))
        if read is None:
            return self._mc_fix_grade(session, unit, upload, grader, wanted)
        if score is None:
            return self._mc_fix_set(unit["id"], "refused", error="Gradescope has not scored this question; nothing was changed.")
        if points not in (None, Decimal(0)) and points != self._mc_own_adjustment(unit):
            return self._mc_fix_set(unit["id"], "refused", error=f"Someone put a point adjustment ({text_amount(points)}) on this question on Gradescope; Faculty leaves it alone.")
        if score != read:
            return self._mc_fix_set(unit["id"], "refused", error=f"Gradescope's score for this question changed since it was read ({text_amount(read)}, now {text_amount(score)}); Check MC again.")
        return self._mc_fix_put(session, unit, upload, grader, set(grader["applied"]), wanted, read)

    def _mc_fix_grade(self, session, unit, upload, grader, wanted):
        """A question nobody graded on Gradescope (MC CORRECTIONS "UNGRADED"; requirement, 2026-10-28): the rubric item that is the answer Faculty read on the sheet is applied, with the point
        adjustment that makes it Faculty's score (none when the item gives it). Refused, writing nothing, when someone graded it since
        the read, or when Gradescope has no single item for what Faculty read (two filled bubbles Gradescope did not group)."""
        from .gradescope_sync import text_amount
        if grader["evaluation_id"] is not None or grader["applied"] or grader["score"] is not None:
            shown = text_amount(grader["score"]) if grader["score"] is not None else "a grade"
            return self._mc_fix_set(unit["id"], "refused", error=f"Someone graded this question on Gradescope since it was read ({shown}); Check MC again.")
        marked = self._mc_marked(unit, upload)
        if marked is None:
            return self._mc_fix_set(unit["id"], "refused", error="Faculty no longer has a reading of this question on its sheet; nothing was changed.")
        wanted_letters = "".join(sorted(marked))
        matches = [item for item, description in grader["descriptions"].items() if answer_letters(description) == wanted_letters]
        if len(matches) != 1:
            said = ", ".join(marked) if marked else "a blank"
            return self._mc_fix_set(unit["id"], "refused", error=f"Gradescope has no answer of its own for what Faculty read ({said}); grade it there.")
        return self._mc_fix_put(session, unit, upload, grader, {matches[0]}, wanted, None)

    def _mc_fix_put(self, session, unit, upload, grader, applied, wanted, read):
        """Write the rubric items `applied` with the point adjustment that makes the question Faculty's score, the one place a
        correction is sent to Gradescope: recorded before the request leaves, an uncertain outcome read back and never resent."""
        from .gradescope_sync import text_amount
        question = grader["question"] or {}
        if question.get("weight") in (None, ""):
            return self._mc_fix_set(unit["id"], "refused", error="Gradescope's grading page no longer says what this question is worth; nothing was changed.")
        items = [{"id": item, "points": weight} for item, weight in grader["items"].items()]
        adjustment = wanted - gw.gradescope_score(question, items, applied, Decimal(0))
        if gw.gradescope_score(question, items, applied, adjustment) != wanted:
            return self._mc_fix_set(unit["id"], "refused", error=f"Gradescope cannot give {text_amount(wanted)} for this question (it is worth {text_amount(question['weight'])}); nothing was changed.")
        self._mc_fix_set(unit["id"], "sending", adjustment=text_amount(adjustment), settle=False)  # recorded before the request leaves
        unit = {**unit, "adjustment": text_amount(adjustment)}
        try:
            # A correction changes only the point adjustment: the rubric item Gradescope applied and its comment stay as they are.
            # Faculty's own earlier adjustment that is no longer needed is taken away (a regrade decision that changed Faculty's reading).
            session.save_grade(grader, applied, adjustment if adjustment else None, grader["comments"])
        except gw.GradescopeBusy as error:
            if error.outcome != "unknown":
                raise  # 429 or 503: not saved; it goes again after the wait
        except gw.GradescopeError as error:
            if error.outcome != "unknown":
                return self._mc_fix_set(unit["id"], "failed", error=f"{error} Check MC again to try once more.")
        return self._mc_fix_check(session, {**unit, "status": "uncertain"}, upload, wanted, read, after_write=True)

    def _mc_own_adjustment(self, unit):
        """The point adjustment Faculty itself last put on this question of this sheet (its latest accepted correction's receipt), or
        None. Faculty may change its own adjustment when its score changes (REGRADES "BUBBLE SHEETS": a regrade decision corrected the
        reading); anyone else's is left alone."""
        with connect(self.path) as db:
            row = db.execute("""SELECT receipt_json FROM frq_gradescope_mc_corrections WHERE sheet_upload_id=? AND question_number=? AND status='accepted'
                AND id!=? ORDER BY updated_at DESC, rowid DESC LIMIT 1""", (unit["sheet_upload_id"], unit["question_number"], unit["id"])).fetchone()
        points = (json.loads(row["receipt_json"] or "{}") or {}).get("points") if row else None
        return dec(points) if points not in (None, "") else None

    def _mc_marked(self, unit, upload):
        """The letters Faculty read for this question on the sheet it uploaded ("" a blank), or None when it has no reading of it."""
        view = self.frq._bubble_view(unit["assignment_id"])[1] or {}
        sheet = next((s for s in view.get("sheets") or [] if s.get("sheet_id") == upload["sheet_id"]), None)
        line = next((q for q in (sheet or {}).get("questions") or [] if int(q.get("number") or 0) == unit["question_number"]), None)
        return None if line is None else "".join(ch for ch in str(line.get("marked") or "") if ch in "ABCDE")

    def _mc_ungraded(self, session, upload, numbers):
        """Which of `numbers` nobody graded on this sheet on Gradescope, from each question's grading page (the MC read's look at a
        sheet Gradescope has not finished grading). A question Faculty cannot find there is left out."""
        found = []
        for number in numbers:
            grader, problem = self._mc_fix_page(session, {"question_number": number, "version": upload["exam_version"]}, upload)
            if not problem and grader["evaluation_id"] is None and not grader["applied"] and grader["score"] is None:
                found.append(number)
        return found

    def _mc_fix_check(self, session, unit, upload, wanted, read, *, after_write):
        """Read the question back: Faculty's score there is accepted; Gradescope's own score untouched means it did not land, which
        fails (never resent by itself); anything else is someone else's and is left for the instructor to look at."""
        from .gradescope_sync import text_amount
        try:
            grader, problem = self._mc_fix_page(session, unit, upload)
        except gw.GradescopeError as error:
            if after_write:
                self._mc_fix_set(unit["id"], "uncertain", error=f"Gradescope did not answer when Faculty checked the correction ({error}); it will look again.")
                return
            raise
        if problem:
            return self._mc_fix_set(unit["id"], "failed", error=problem)
        score = grader["score"]
        if score is not None and score == wanted:
            return self._mc_fix_set(unit["id"], "accepted", receipt=self._mc_fix_receipt(grader))
        if (score == read if read is not None else score is None and not grader["applied"]) and grader["points"] in (None, ""):
            return self._mc_fix_set(unit["id"], "failed", error="Gradescope did not keep the correction. Check MC again to try once more.")
        shown = text_amount(score) if score is not None else "no score"
        return self._mc_fix_set(unit["id"], "failed", error=f"Gradescope shows {shown} for this question now, not Faculty's {text_amount(wanted)}. Look at it on Gradescope.")

    @staticmethod
    def _mc_fix_receipt(grader):
        from .gradescope_sync import text_amount
        return {"applied": sorted(grader["applied"]), "points": None if grader["points"] in (None, "") else text_amount(grader["points"]),
                "score": text_amount(grader["score"] or 0), "at": timestamp()}

    # --- what the pages show (Faculty's records only) -------------------------------------------------------------------------
    @staticmethod
    def mc_corrections(db, aid, view=None):
        """Every correction of the plan, the latest per student question, for the instructor to look at: who, which question, both
        scores, where it is and why it did not go through. Names come from Faculty's scored sheets (`view`), as the comparison's."""
        names = {s.get("enrollment_id"): s for s in ((view or {}).get("sheets") or []) if s.get("enrollment_id")}
        rows, seen = [], set()
        for row in db.execute("""SELECT c.*,s.number FROM frq_gradescope_mc_corrections c JOIN course_sections s ON s.id=c.section_id
                WHERE c.assignment_id=? ORDER BY c.created_at DESC,c.rowid DESC""", (aid,)).fetchall():
            key = (row["enrollment_id"], row["question_number"])
            if key in seen:
                continue
            seen.add(key)
            sheet = names.get(row["enrollment_id"]) or {}
            line = next((q for q in sheet.get("questions") or [] if int(q.get("number") or 0) == row["question_number"]), None)
            rows.append({"row": f"{row['section_id']}:{row['enrollment_id']}", "enrollment_id": row["enrollment_id"], "name": sheet.get("student") or "",
                         "section": row["number"], "version": row["version"], "number": row["question_number"], "gradescope": row["gradescope_points"] or NOT_GRADED,
                         "faculty": row["faculty_points"], "state": row["status"], "error": row["error"] if row["status"] != "accepted" else "",
                         "why": why(line) if line else "", "at": row["updated_at"]})
        rows.sort(key=lambda r: (str(r["section"]), r["name"].casefold(), r["number"]))
        return rows
