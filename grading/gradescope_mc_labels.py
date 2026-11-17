"""MC LABELS (docs/decisions/gradescope.md; requirement, 2026-10-28).

Gradescope's bubble-sheet grader makes one rubric item per answer it meets, named by the letters filled ("A", "A, C", "Blank"; read on
the real site 2026-10-28: Exam 2's 140-508 Version A Question 1 has "D" worth 4, Gradescope's key, and "A" worth 0, added as sheets
came in). A student shown "+0 A" cannot tell what the answer was. Label answers on Gradescope renames each such item so it says
whether it is right and what the answer is, from FACULTY'S key (its score is the grade, MC CORRECTIONS):

    "D: correct"   "A: incorrect (correct answer: D)"   "Blank: no answer (correct answer: D)"
    "A: partial credit (correct answer: A and C)"        "B: incorrect (correct answer: C or D)"

Only the description changes (the PUT of Gradescope's own rubric editor): the item's points, its group, which sheets it is applied
to and every score stay as they are; nothing is published, commented or regraded. `answer_letters` still reads a renamed item, so MC
CORRECTIONS and MC regrades find it. An item that is not one of Gradescope's answers (a person's own) is left alone.

One run labels every linked section (`_mc_targets`), one sender unit per section (kind "mc_labels", migration 050). A rename whose
answer is lost makes the unit uncertain: it is settled by READING Gradescope only, never by renaming again (the instructor's Label
answers again does that, as a new run).

AUTOMATICALLY TOO (requirement, 2026-10-28): a
bubble-sheet send that completes queues a run that waits `AFTER_SEND` (Gradescope grades the sheets, and makes their answers' items,
in the minutes after they arrive), and every MC check whose last section read settles queues one (answers Gradescope met since). Not
while a run of the plan is open, and never after a run that did not finish (failed or refused): that is the instructor's Label
answers again. Only for exams linked from `AUTOMATIC_FROM` on, or a plan the instructor labelled once: an exam students already saw
(Exam 1, Exam 2) is renamed only by the instructor's own Label answers."""
import json
import logging
import time
from decimal import Decimal

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from rosters import connect

from . import gradescope_web as gw
from .bubble_keys import LETTERS, question_score
from .gradescope_mc import TRIES, WAIT_FOR_SHEETS
from .gradescope_mc_corrections import READ_TRIES, answer_letters
from .models import uid

LOG = logging.getLogger(__name__)
OPEN = ("pending", "sending", "uncertain")
AFTER_SEND = 600  # seconds an automatic run waits after a bubble-sheet send completes, for Gradescope to grade the sheets
AUTOMATIC_FROM = "2026-10-28T14:05:44+00:00"  # the instructor's "yes for future exams"


def answer_label(letters, answer, scoring, points):
    """What a bubble-sheet rubric item for `letters` ("" a blank) says on Gradescope, by Faculty's key for its question: whether it
    is right, and the answer when it is not wholly right. The letters come first, so `answer_letters` still reads it."""
    shown = ", ".join(letters) if letters else "Blank"
    if scoring == "either" and set(answer) == set(LETTERS):
        return f"{shown}: correct (every answer accepted)"
    earned, points = question_score(letters, answer, scoring, points), Decimal(points)
    if earned == points:
        return f"{shown}: correct"
    keyed = (" or " if scoring == "either" else " and ").join(answer)
    why = "partial credit" if earned > 0 else "incorrect" if letters else "no answer"
    return f"{shown}: {why} (correct answer: {keyed})"


def wanted_labels(rubric, key):
    """{item id: (question number, before, after)} for every item of one version's rubric (`Session.rubric_editor`'s props) that is one
    of Gradescope's answers on a question of `key` ({number: {"answer", "scoring", "points"}}) and does not say its label yet; and
    counts of those already labelled and of the items left alone."""
    numbers = {str(q["id"]): int(q.get("index") or 0) for q in rubric.get("questions") or [] if not q.get("parent_id")}
    wanted, counts = {}, {"labelled": 0, "left": 0}
    for item in rubric.get("rubric_items") or []:
        question = key.get(numbers.get(str(item.get("question_id"))))
        letters = answer_letters(item.get("description"))
        if question is None or letters is None or item.get("group_id"):
            counts["left"] += 1
            continue
        after = answer_label(letters, question["answer"], question["scoring"], question["points"])
        if str(item.get("description") or "").strip() == after:
            counts["labelled"] += 1
        else:
            wanted[str(item["id"])] = (numbers[str(item["question_id"])], str(item.get("description") or ""), after)
    return wanted, counts


class McLabelMixin:
    """Mixed into `GradescopeSender` beside `McCheckMixin`: queues a run, works it as sender units (kind "mc_labels"), and says where
    it is for the panels."""

    # --- queueing ---------------------------------------------------------------------------------------------------------------
    def _mc_label_queue(self, db, aid, actor, not_before=None):
        """One unit per linked section, inside the caller's transaction. None, queueing nothing, while a run of the plan is open, or
        for an automatic run when there is nothing to label, no key, or the plan's latest run did not finish."""
        if db.execute(f"SELECT 1 FROM frq_gradescope_mc_labels WHERE assignment_id=? AND status IN ({','.join('?' * len(OPEN))}) LIMIT 1",
                      (aid, *OPEN)).fetchone():
            return None
        targets = self._mc_targets(db, self.frq.plan(db, aid))
        if actor != "workspace":
            last = db.execute("SELECT run_id FROM frq_gradescope_mc_labels WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (aid,)).fetchone()
            if not targets or not self.frq.bubble_key(db, aid) or (last and db.execute(
                    "SELECT 1 FROM frq_gradescope_mc_labels WHERE run_id=? AND status IN ('failed','refused') LIMIT 1", (last["run_id"],)).fetchone()):
                return None
            future = db.execute("SELECT 1 FROM frq_gradescope_sheet_targets WHERE assignment_id=? AND created_at>=? LIMIT 1", (aid, AUTOMATIC_FROM)).fetchone()
            if not future and not db.execute("SELECT 1 FROM frq_gradescope_mc_labels WHERE assignment_id=? AND actor='workspace' LIMIT 1", (aid,)).fetchone():
                return None  # an exam students already saw: renamed only by the instructor's own Label answers
        run_id, now = uid(), timestamp()
        for target in targets:
            db.execute("""INSERT INTO frq_gradescope_mc_labels(id,run_id,assignment_id,section_id,course_id,container_id,versions_json,status,attempts,
                not_before,receipt_json,error,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'pending',0,?,NULL,'',?,?,?)""",
                       (uid(), run_id, aid, target["section_id"], target["course_id"], target["container_id"],
                        json.dumps(target["versions"], sort_keys=True), not_before, actor, now, now))
        self.frq.event(db, aid, "gradescope_mc_labels", {"run_id": run_id, "sections": len(targets)}, actor)
        return run_id

    def _mc_label_after_read(self, read_id):
        """After an MC check read settles: when it was the check's last section, label what Gradescope met since. Never raises."""
        try:
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                read = db.execute("SELECT assignment_id,check_id FROM frq_gradescope_mc_checks WHERE id=?", (read_id,)).fetchone()
                if read and not db.execute("SELECT 1 FROM frq_gradescope_mc_checks WHERE check_id=? AND status IN ('pending','sending') LIMIT 1",
                                           (read["check_id"],)).fetchone():
                    queued = self._mc_label_queue(db, read["assignment_id"], "automatic")
                else:
                    queued = None
            if queued:
                self.wake.set()
        except Exception:
            LOG.exception("Queueing the labels of Gradescope's multiple-choice answers stopped unexpectedly")

    def mc_label_start(self, aid, actor="workspace"):
        """The instructor's Label answers on Gradescope: one unit per linked section, unless a run of the plan is still open."""
        if not self.settings.status().get("configured"):
            raise Conflict("Gradescope is not set up for this Faculty, so its rubrics cannot be changed.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            plan = self.frq.plan(db, aid)
            targets = self._mc_targets(db, plan)
            if not targets:
                raise Conflict("No section links this assignment’s bubble-sheet exam on Gradescope yet: send the bubble sheets first.")
            if not self.frq.bubble_key(db, aid):
                raise Conflict("This assignment has no answer key in Faculty yet, so Faculty cannot say which answers are right.")
            if not self._mc_label_queue(db, aid, actor):
                # A run waiting for Gradescope to grade the sheets (AFTER_SEND) goes now: the instructor asked for it.
                db.execute("UPDATE frq_gradescope_mc_labels SET not_before=NULL,updated_at=? WHERE assignment_id=? AND status='pending'", (timestamp(), aid))
        self.wake.set()
        return self.mc_label_status(aid)

    # --- the sender's unit ------------------------------------------------------------------------------------------------------
    def _mc_label_next(self, db, skip, ids, now):
        return db.execute(f"""SELECT 'mc_labels' kind,* FROM frq_gradescope_mc_labels WHERE status IN ('pending','uncertain') {skip}
            AND (not_before IS NULL OR not_before<=?) ORDER BY created_at,rowid LIMIT 1""", (*ids, now)).fetchone()

    def _mc_label_set(self, unit_id, status, *, error="", receipt=None, not_before=None):
        with self.rosters.lock(), connect(self.path) as db:
            sets, args = ["status=?", "error=?", "updated_at=?", "not_before=?"], [status, error[:2000], timestamp(), not_before]
            if receipt is not None:
                sets.append("receipt_json=?")
                args.append(json.dumps(receipt, sort_keys=True))
            db.execute(f"UPDATE frq_gradescope_mc_labels SET {','.join(sets)} WHERE id=? AND status IN ('pending','sending','uncertain')", (*args, unit_id))

    def _mc_label_wait(self, unit, reason, when, receipt=None):
        """Nothing more was written by this turn: a claimed unit waits again; an uncertain one stays uncertain, for a read only."""
        with self.rosters.lock(), connect(self.path) as db:
            db.execute(f"""UPDATE frq_gradescope_mc_labels SET status=CASE WHEN status='uncertain' THEN 'uncertain' ELSE 'pending' END,not_before=?,
                error=?,updated_at=?{',receipt_json=?' if receipt is not None else ''} WHERE id=? AND status IN ('pending','sending','uncertain')""",
                       (when, f"Waiting: {reason}"[:2000], timestamp(), *((json.dumps(receipt, sort_keys=True),) if receipt is not None else ()), unit["id"]))

    def _mc_label_unit(self, unit):
        """One section's labels. Never raises: a busy Gradescope or a refused sign-in waits like any unit; everything else settles it."""
        from .gradescope_sync import PAUSE, later
        if unit["status"] == "pending":
            with self.rosters.lock(), connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM frq_gradescope_sheet_sends WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1",
                              (unit["assignment_id"],)).fetchone():
                    db.execute("UPDATE frq_gradescope_mc_labels SET not_before=?,updated_at=? WHERE id=? AND status='pending'",
                               (later(WAIT_FOR_SHEETS), timestamp(), unit["id"]))
                    return  # the sheets go first: Gradescope makes their answers' items as it grades them
                if not db.execute("UPDATE frq_gradescope_mc_labels SET status='sending',attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'",
                                  (timestamp(), unit["id"])).rowcount:
                    return
            unit = {**unit, "status": "sending", "attempts": unit["attempts"] + 1}
        receipt = json.loads(unit.get("receipt_json") or "null") or {"items": []}
        try:
            self._mc_label_work(unit, receipt)
        except gw.SignInRefused as error:
            self._paused = (time.monotonic() + PAUSE, self._sign_in_stamp())
            self._drop_session()
            self._mc_label_wait(unit, str(error), later(PAUSE), receipt)
        except gw.GradescopeBusy as error:
            delay = self._pause(error)
            if unit["attempts"] >= TRIES and unit["status"] != "uncertain":
                self._mc_label_set(unit["id"], "failed", receipt=receipt,
                                   error=f"Gradescope stayed busy through {unit['attempts']} tries. Label answers again when it is quieter.")
            else:
                self._mc_label_wait(unit, f"Gradescope is busy; Faculty tries again in {delay} s.", later(delay), receipt)
        except gw.GradescopeError as error:
            # A read (every rename is caught where it is made): nothing more changed on Gradescope.
            if unit["status"] == "uncertain" or unit["attempts"] < READ_TRIES:
                self._mc_label_wait(unit, str(error), later(60), receipt)
            else:
                self._mc_label_set(unit["id"], "failed", receipt=receipt, error=f"{error} Label answers again to try once more.")
        except Exception:
            LOG.exception("Labelling multiple-choice answers on Gradescope stopped unexpectedly")
            if unit["status"] == "uncertain":
                self._mc_label_wait(unit, "Faculty stopped unexpectedly while reading it.", later(60), receipt)
            else:
                self._mc_label_set(unit["id"], "failed", receipt=receipt, error="Faculty stopped unexpectedly. Label answers again to finish.")

    def _mc_label_key(self, aid):
        """Faculty's key as it is now: {version letter: {question number: {"answer", "scoring", "points"}}}."""
        with connect(self.path) as db:
            key = self.frq.bubble_key(db, aid)
        defaults = (key or {}).get("defaults") or {}
        return {v["letter"]: {q["number"]: {"answer": q["answer"], "scoring": q.get("scoring", defaults.get("scoring")),
                                             "points": q.get("points", defaults.get("points"))} for q in v["questions"]}
                for v in (key or {}).get("versions") or []}

    def _mc_label_work(self, unit, receipt):
        session = self._session_now()
        keys = self._mc_label_key(unit["assignment_id"])
        versions = json.loads(unit["versions_json"] or "{}")
        done = {(i["version"], i["item"]) for i in receipt["items"]}
        totals = {"labelled": 0, "left": 0, "renamed": len(receipt["items"]), "missing": 0}
        for letter, assignment in sorted(versions.items()):
            if letter not in keys:
                continue  # a version Faculty has no key for is left as Gradescope named it
            editor = session.rubric_editor(unit["course_id"], assignment)
            wanted, counts = wanted_labels(editor["props"], keys[letter])
            totals["labelled"] += counts["labelled"] - sum(1 for v, _ in done if v == letter)
            totals["left"] += counts["left"]
            if unit["status"] == "uncertain":
                lost = receipt.get("uncertain") or {}
                if lost.get("version") == letter and lost.get("item") not in wanted:
                    receipt["items"].append({**lost, "landed": True})  # its answer was lost, but it is there
                    totals["renamed"] += 1
                    totals["labelled"] -= 1
                totals["missing"] += len(wanted)  # settled by reading only: a rename that did not land is the instructor's to try again
                continue
            items = {str(i["id"]): i for i in editor["props"].get("rubric_items") or []}
            for item_id, (number, before, after) in sorted(wanted.items(), key=lambda entry: (entry[1][0], entry[0])):
                entry = {"version": letter, "number": number, "item": item_id, "before": before, "after": after}
                try:
                    session.rename_rubric_item(unit["course_id"], editor, items[item_id]["question_id"], items[item_id], after)
                except gw.GradescopeBusy as error:
                    if error.outcome != "unknown":
                        raise  # 429 or 503: not renamed; the unit goes again after the wait and reads first
                    return self._mc_label_set(unit["id"], "uncertain", receipt={**receipt, "uncertain": entry},
                                              error="Gradescope did not answer while Faculty renamed an item; it will read the rubric to see whether it landed.")
                except gw.GradescopeError as error:
                    if error.outcome == "unknown":
                        return self._mc_label_set(unit["id"], "uncertain", receipt={**receipt, "uncertain": entry},
                                                  error="Gradescope did not answer while Faculty renamed an item; it will read the rubric to see whether it landed.")
                    return self._mc_label_set(unit["id"], "failed", receipt=receipt, error=f"{error} Label answers again to try once more.")
                if (letter, item_id) not in done:
                    totals["renamed"] += 1
                receipt["items"] = [i for i in receipt["items"] if (i["version"], i["item"]) != (letter, item_id)] + [entry]
            # Read back: Gradescope's answer to each rename is not trusted as the outcome.
            again, _ = wanted_labels(session.rubric_editor(unit["course_id"], assignment)["props"], keys[letter])
            landed = set(wanted) - set(again)
            for entry in receipt["items"]:
                if entry["version"] == letter and entry["item"] in wanted:
                    entry["landed"] = entry["item"] in landed
            totals["missing"] += len(again)
        receipt = {**receipt, "counts": totals, "at": timestamp()}
        receipt.pop("uncertain", None)
        if totals["missing"]:
            return self._mc_label_set(unit["id"], "failed", receipt=receipt,
                                      error=f"{totals['missing']} answers on Gradescope still carry only their letters. Label answers again to finish.")
        self._mc_label_set(unit["id"], "accepted", receipt=receipt)

    # --- what the panels show (Faculty's records only) --------------------------------------------------------------------------
    def mc_label_status(self, aid):
        """The plan's latest Label answers run, one row per section: where it is, how many answers it renamed, and why one did not
        finish. None when the plan never labelled."""
        with self.rosters.lock(), connect(self.path) as db:
            run = db.execute("SELECT run_id FROM frq_gradescope_mc_labels WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (aid,)).fetchone()
            if not run:
                return None
            rows = db.execute("""SELECT l.*,s.number FROM frq_gradescope_mc_labels l JOIN course_sections s ON s.id=l.section_id
                WHERE l.run_id=? ORDER BY s.number""", (run["run_id"],)).fetchall()
        sections = []
        for row in rows:
            receipt = json.loads(row["receipt_json"] or "null") or {}
            counts = receipt.get("counts") or {}
            sections.append({"section": row["number"], "state": row["status"], "renamed": counts.get("renamed", len(receipt.get("items") or [])),
                             "labelled": counts.get("labelled", 0), "error": row["error"] if row["status"] != "accepted" else "", "at": row["updated_at"]})
        return {"running": any(s["state"] in OPEN for s in sections), "sections": sections,
                "renamed": sum(s["renamed"] for s in sections), "at": max((s["at"] for s in sections), default="")}
