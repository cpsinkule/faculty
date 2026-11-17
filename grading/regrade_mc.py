"""Regrade requests on multiple choice (REGRADES "BUBBLE SHEETS"; requirement, 2026-10-28). Faculty's scoring of a bubble sheet is the grade (GRADESCOPE "MC CORRECTIONS"), so a request about a bubble-sheet
question is answered from Faculty's reading of the student's sheet, like a written answer's from its grade.

A request is placed on the sheet Faculty uploaded for that student in that version (`regrade_requests.sheet_match`). What Faculty knows
about it: the bubbles it read for the question (the sheet's latest review, else the machine reading), the instructor's key for the
version (answer, scoring, points) and what that earns, the sheet's total, and the picture of that question's row on the scan.

A decision is "keep" (the reading stands) or "change" with `answer`: the letters the student filled, as the instructor reads the sheet
("" a blank). A change is saved as a review of the sheet (BUBBLE-SHEETS: the instructor's reading of every question, the others as
they are), so Faculty's score follows from the key. A key that is wrong is the instructor's to fix on the Bubble sheets page, for
everyone; the decision never gives points by hand.

Apply's steps are the written answer's, each from the module that owns it:
1. `gradescope`: Faculty's score for the question reaches Gradescope through MC CORRECTIONS: Apply asks for a read of Gradescope's
   scores (`_mc_queue`, as the instructor's Check MC, so a correction that failed before is tried again), the read queues the
   correction and the sender makes it. The step settles from the correction's own receipt, or from the read when Gradescope already
   shows the score. A published exam is written to, as MC CORRECTIONS does.
2. `reply`: posted once Gradescope shows Faculty's score for the question (`expect_score`); it answers and closes the request.
3. `gradebook`: CERTIFY JUST THIS CHANGE for the student's sheet (`OneChange.check_sheet`, `amend` with `sheet_row`).
4. `canvas`: the student's cell, as for a written answer.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from gradebook import common
from rosters import connect

from . import progress
from .bubble_keys import points_text, question_score
from .certification_change import CONFIRMATION as CHANGE
from .models import digest
from .regrade_requests import RegradeRequests

LETTERS = "ABCDE"
SETTLE = 60  # seconds after a read settles before a missing correction is a stop (the read queues its corrections right after it)


def text(value):
    return progress.amount(value)


def seconds_since(stamp):
    """Seconds since an ISO timestamp of the catalog (a large number when it cannot be read)."""
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return 10 ** 9
    return (datetime.now(timezone.utc) - (when if when.tzinfo else when.replace(tzinfo=timezone.utc))).total_seconds()


def answer_words(answer):
    """"B", "B and D" or "a blank", as the card says what Faculty reads."""
    return " and ".join(answer) if answer else "a blank"


def parse_answer(value):
    """The letters a decision reads for the question, sorted and each once ("" a blank)."""
    if not isinstance(value, str) or len(value) > len(LETTERS) or any(ch not in LETTERS for ch in value) or len(set(value)) != len(value):
        raise ValueError('A change names "answer": the letters the student filled for this question (A to E, "" for a blank).')
    return "".join(sorted(value))


def question(view, placed):
    """Faculty's reading and scoring of the placed question from a `_bubble_view` (private fields included), or {reason}."""
    sheet = next((s for s in (view or {}).get("sheets") or [] if s.get("sheet_id") == placed.get("sheet_id")), None)
    if not sheet:
        return {"reason": "Faculty no longer has this bubble sheet's reading."}
    found = {"sheet_id": sheet["sheet_id"], "number": placed["number"], "version": sheet.get("version") or placed.get("exam_version") or "",
             "enrollment_id": sheet.get("enrollment_id"), "status": sheet.get("status")}
    if sheet.get("status") != "scored":
        return {**found, "reason": sheet.get("reason") or "Faculty does not score this sheet yet."}
    line = next((q for q in sheet.get("questions") or [] if int(q.get("number") or 0) == int(placed["number"])), None)
    if not line:
        return {**found, "reason": f"Question {placed['number']} is not in your key for Version {found['version']}."}
    return {**found, "marked": line["marked"], "answer": line["answer"], "scoring": line["scoring"], "points": text(line["points"]),
            "earned": text(line["earned"]), "sheet_earned": text(sheet["earned"]), "sheet_possible": text(sheet["possible"])}


def picture(found):
    """The question's row of the student's scan (`BubbleSheetService.part`), or None."""
    return f"/api/bubble-sheets/sheets/{found['sheet_id']}/parts/q{int(found['number'])}" if found.get("sheet_id") and found.get("number") else None


def rescored(found, answer):
    """(question points, sheet points) were Faculty to read `answer` for the question."""
    earned = question_score(answer, found["answer"], found["scoring"], found["points"])
    total = Decimal(found["sheet_earned"]) - Decimal(found["earned"]) + earned
    return text(points_text(earned)), text(points_text(total))


class McApplyMixin:
    """Mixed into `RegradeApply`: the panel, the decision and Apply for a request placed on a bubble sheet (`placed["kind"] == "mc"`)."""

    def _mc_question(self, aid, placed):
        return question(self.frq._bubble_view(aid)[1], placed)

    def _mc_student(self, aid, section_id, enrollment_id):
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            key = (section_id, enrollment_id)
            found = self.certification._names(db, self.frq.exams._event(db, plan["event_id"]), [key])[key]
        return {"name": found["name"], "section": found["section"]}

    def _mc_where(self, db, aid, placed, enrollment_id, found):
        """Where the multiple-choice grade is, from the catalog: Faculty's score for the question and the sheet, what Faculty last made
        Gradescope show (its latest correction of the question), the certified total, the gradebook cell and Faculty's last Canvas send."""
        where = {"faculty": {"points": found.get("earned"), "of": found.get("points"), "sheet": found.get("sheet_earned"), "sheet_of": found.get("sheet_possible")}
                 if found.get("earned") is not None else None, "row": common.row_key(placed["section_id"], enrollment_id), "kind": "mc"}
        row = db.execute("""SELECT gradescope_points,faculty_points,status FROM frq_gradescope_mc_corrections WHERE assignment_id=? AND enrollment_id=? AND question_number=?
            ORDER BY created_at DESC,rowid DESC LIMIT 1""", (aid, enrollment_id, placed["number"])).fetchone()
        where["gradescope"] = {"points": row["faculty_points"] if row["status"] == "accepted" else (row["gradescope_points"] or None), "state": row["status"]} if row else None
        return self._book(db, aid, placed["section_id"], enrollment_id, where)

    def _mc_detail(self, aid, message_id, request, placed, state, replies, mark):
        found = self._mc_question(aid, placed) if placed.get("sheet_id") else {"reason": placed.get("reason")}
        student = self._mc_student(aid, placed["section_id"], request["enrollment_id"]) if placed.get("sheet_id") else None
        with self.rosters.lock(), connect(self.path) as db:
            where = self._mc_where(db, aid, placed, request["enrollment_id"], found) if placed.get("sheet_id") else None
            run = self._latest_run(db, aid, message_id)
            view = self._view(db, run) if run else None
        suggestion = self._suggestion(request) if state == "open" else None
        sheet = {k: found.get(k) for k in ("sheet_id", "number", "version", "marked", "answer", "scoring", "points", "earned", "sheet_earned", "sheet_possible", "reason")}
        sheet["picture"] = picture(found)
        return {"id": message_id, "created_at": request["created_at"], "text": request["text"], "url": request["url"], "state": state,
                "replies": replies, "mark": mark, "reply": request["reply"], "question_title": request["question_title"], "kind": "mc",
                "response_id": None, "question_id": None, "exam_version": placed.get("exam_version", ""), "number": placed.get("number"),
                "matched": bool(placed.get("sheet_id")), "reason": placed.get("reason", ""),
                "can_reply": state == "open" and request["gradescope_request_id"].isdigit(), "student": student, "where": where, "sheet": sheet,
                "draft": None, "apply": view, "suggestion": suggestion}

    # ----- the preview -----------------------------------------------------------------------------------------------------------
    def _mc_gradescope(self, aid, message_id, reply):
        """(open on Gradescope, Gradescope's score for the question now, the reply's fingerprint), read from the grading page."""
        found = self.regrades.preview(aid, message_id, {"reply": reply})
        return found["open"], text(found["gradescope_score"]), found["fingerprint"]

    def _mc_book_steps(self, aid, where, steps, notes, fingerprints, before, after, replace=False):
        """The gradebook and Canvas steps of a sheet whose points go from `before` to `after` (CERTIFY JUST THIS CHANGE, ONE BUBBLE
        SHEET). Returns the certification check it forecast from."""
        check = self.change.check_sheet(aid, where["row"])
        cell = where.get("gradebook") or {}
        number = lambda value: re.fullmatch(r"-?\d+(\.\d+)?", value or "")  # noqa: E731
        if check["state"] == "none":
            notes.append("The grades are not certified yet: they go to the gradebook when you certify.")
        elif check["possible"] or check["done"]:
            base = check["from"] if check["possible"] and check.get("from") is not None else before
            delta = Decimal(after) - Decimal(base) if after is not None and base is not None else None
            forecast = text(Decimal(cell["value"]) + delta) if delta is not None and cell.get("source") == "grading" and number(cell.get("value")) else None
            if delta or check["possible"]:
                typed = cell.get("source") == "instructor"
                steps.append({"step": "gradebook", "from": cell.get("value"), "to": forecast, "typed": typed, "replace": bool(replace and typed),
                              "question": {"from": check.get("from"), "to": after}})
                if check["possible"]:
                    fingerprints["gradebook"] = check["fingerprint"]
                if forecast is not None and where.get("item_id") and not (typed and not replace):
                    self._canvas_step(where, forecast, steps, notes, fingerprints)
        elif check["same"] and after == before:
            cell_value = cell.get("value")
            if cell_value is not None and where.get("item_id"):
                self._canvas_step(where, cell_value, steps, notes, fingerprints, only_if_differs=True)
        else:
            notes.append(" ".join(check["reasons"]))
            steps.append({"step": "gradebook", "problem": " ".join(check["reasons"]), "href": f"#grading/{aid}?tab=export", "action": "Certify again…"})
        return check

    def _mc_plan(self, aid, message_id, request, placed, reply, replace=False):
        """Every step something differs on for a multiple-choice request (reads Gradescope and Canvas; writes nothing)."""
        if not placed.get("sheet_id"):
            raise ValueError(placed.get("reason") or "Faculty cannot place this request on a bubble sheet: answer it on Gradescope.")
        found = self._mc_question(aid, placed)
        with self.rosters.lock(), connect(self.path) as db:
            where = self._mc_where(db, aid, placed, request["enrollment_id"], found)
        steps, notes, fingerprints = [], [], {}
        faculty = found.get("earned")
        with connect(self.path) as db:
            state, _, _ = self.regrades._state(db, aid, request)
        gradescope = None
        if state == "open" and request["gradescope_request_id"].isdigit():
            if not reply:
                raise ValueError("Write your reply to the student.")
            still, gradescope, fingerprints["reply"] = self._mc_gradescope(aid, message_id, reply)
            if not still:
                notes.append("The request was already answered on Gradescope.")
                fingerprints.pop("reply")
        if faculty is None:
            notes.append(f"Faculty has no score for this question: {found.get('reason')}")
        elif gradescope is not None and gradescope != faculty:
            if self.sender.settings.configured:
                steps.append({"step": "gradescope", "from": gradescope, "to": faculty})
            else:
                notes.append("Gradescope's saved sign-in is not set up: change the score there by hand.")
        if "reply" in fingerprints:
            steps.append({"step": "reply", "expect": faculty})
        if faculty is not None:
            self._mc_book_steps(aid, where, steps, notes, fingerprints, found["sheet_earned"], found["sheet_earned"], replace)
        sheet = {k: found.get(k) for k in ("sheet_id", "number", "version")} | {"section_id": placed["section_id"], "enrollment_id": request["enrollment_id"],
                                                                                "upload_id": placed.get("sheet_upload_id")}
        plan = {"kind": "mc", "message_id": message_id, "response_id": None, "sheet": sheet, "steps": steps, "notes": notes, "published": [],
                "replace": bool(replace), "reply_sha256": digest(reply or ""), "faculty": where.get("faculty")}
        plan["fingerprint"] = digest({"steps": steps, "notes": notes, "published": [], "fingerprints": fingerprints, "reply": plan["reply_sha256"],
                                      "replace": plan["replace"], "sheet": sheet})
        return plan, fingerprints, where

    # ----- a decision ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _mc_decision(data):
        """{decision: keep|change, answer, reply, reason} of a multiple-choice decide request, checked."""
        decision = data.get("decision")
        if decision not in ("keep", "change"):
            raise ValueError('Say "decision": "keep" or "change".')
        if data.get("lines") or data.get("adjustment"):
            raise ValueError('This request is about a bubble-sheet question: a change names "answer" (the letters the student filled), '
                             'never rubric lines or an adjustment.')
        answer = parse_answer(data.get("answer")) if decision == "change" else None
        reply = RegradeRequests._text(data)
        reason = data.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError("Say why in at most 1000 characters.")
        return {"decision": decision, "answer": answer, "reply": reply, "reason": " ".join(reason.split())}

    def _mc_decision_preview(self, aid, message_id, request, placed, data):
        decision = self._mc_decision(data)
        if not placed.get("sheet_id"):
            raise ValueError(placed.get("reason") or "This regrade request is not placed on a bubble sheet: answer it on Gradescope.")
        found = self._mc_question(aid, placed)
        if found.get("earned") is None:
            raise ValueError(f"Faculty has no score for this question: {found.get('reason')}")
        with self.rosters.lock(), connect(self.path) as db:
            where = self._mc_where(db, aid, placed, request["enrollment_id"], found)
        before, after, total = found["earned"], found["earned"], found["sheet_earned"]
        steps, notes, fingerprints = [], [], {}
        if decision["decision"] == "change":
            if decision["answer"] == found["marked"]:
                raise ValueError(f'Faculty already reads {answer_words(found["marked"])} for this question: propose "keep".')
            after, total = rescored(found, decision["answer"])
            steps.append({"step": "grade", "from": before, "to": after, "of": found["points"], "read": found["marked"], "answer": decision["answer"],
                          "sheet": {"from": found["sheet_earned"], "to": total, "of": found["sheet_possible"]}})
        still, gradescope, fingerprints["reply"] = self._mc_gradescope(aid, message_id, decision["reply"])
        if not still:
            raise Conflict("This regrade request was already answered on Gradescope.")
        if gradescope != after:
            if self.sender.settings.configured:
                steps.append({"step": "gradescope", "from": gradescope, "to": after})
            else:
                notes.append("Gradescope's saved sign-in is not set up: change the score there by hand.")
        steps.append({"step": "reply"})
        check = self._mc_book_steps(aid, where, steps, notes, fingerprints, found["sheet_earned"], total)
        pinned = {k: found.get(k) for k in ("sheet_id", "number", "version", "marked", "answer", "scoring", "points", "earned", "sheet_earned")}
        fingerprint = digest({"sheet": pinned, "decision": {k: decision[k] for k in ("decision", "answer")}, "reply": digest(decision["reply"]),
                              "to": after, "steps": steps, "fingerprints": fingerprints, "certification": [check.get("state"), check.get("fingerprint")],
                              "cell": where.get("gradebook") or {}})
        preview = {"message_id": message_id, "response_id": None, "kind": "mc", **{k: decision[k] for k in ("decision", "answer", "reason")},
                   "reply": decision["reply"], "from": before, "to": after, "of": found["points"], "read": found["marked"], "key": found["answer"],
                   "number": found["number"], "version": found["version"], "picture": picture(found), "steps": steps, "notes": notes, "published": [],
                   "fingerprint": fingerprint}
        return preview, {"sheet": pinned, "decision": decision}

    def _mc_decide(self, aid, message_id, request, placed, data, request_id, request_sha, fingerprint, actor):
        preview, pinned = self._mc_decision_preview(aid, message_id, request, placed, data)
        if preview["fingerprint"] != fingerprint:
            raise Conflict("Something changed since the preview, in Faculty, on Gradescope or in Canvas. Look at it again.")
        decision = pinned["decision"]
        if decision["decision"] == "change":
            self._mc_review(placed, request, pinned["sheet"], decision, actor)
        plan, fingerprints, _ = self._mc_plan(aid, message_id, request, placed, decision["reply"])
        view = self._record_run(aid, message_id, plan, fingerprints, decision["reply"], request_id, request_sha, fingerprint, actor,
                                decision={k: decision[k] for k in ("decision", "answer", "reason")})
        return {"status": "started", "apply": view, "from": preview["from"], "to": preview["to"]}

    def _mc_review(self, placed, request, pinned, decision, actor):
        """The decision's reading saved as a review of the sheet (BUBBLE-SHEETS): every other question, the version and the student as
        the sheet has them now, this question as the instructor reads it."""
        reader = self.frq.inbox.bubble_sheets
        item = reader.sheet(placed["sheet_id"])
        current = item["current"]
        if current.get("enrollment_id") != request["enrollment_id"]:
            raise Conflict("This bubble sheet is no longer this student's: look at it on the Bubble sheets page.")
        answers = list(current["answers"]) + [""] * max(0, item["question_count"] - len(current["answers"]))
        number = int(placed["number"])
        if number > len(answers) or answers[number - 1] != pinned["marked"]:
            raise Conflict("The sheet's reading of this question changed since the preview. Look at it again.")
        answers[number - 1] = decision["answer"]
        reader.review(placed["sheet_id"], {"answers": answers[:item["question_count"]], "version": current.get("version") or pinned["version"],
                                           "section_id": current.get("section_id"), "enrollment_id": current.get("enrollment_id"),
                                           "note": f"Regrade request: question {number} read as {answer_words(decision['answer'])}."[:500]}, actor)

    # ----- the steps -------------------------------------------------------------------------------------------------------------
    def _mc_gradescope_state(self, db, run, plan, step, receipt):
        """(status, detail) of a multiple-choice Gradescope step from MC CORRECTIONS' own records since the step started."""
        sheet, since = plan["sheet"], receipt.get("since") or run["created_at"]
        row = db.execute("""SELECT status,faculty_points,error FROM frq_gradescope_mc_corrections WHERE assignment_id=? AND enrollment_id=? AND question_number=?
            AND created_at>=? ORDER BY created_at DESC,rowid DESC LIMIT 1""", (run["assignment_id"], sheet["enrollment_id"], sheet["number"], since)).fetchone()
        if row:
            if row["status"] in ("pending", "sending"):
                return "waiting", "Faculty is correcting the score on Gradescope."
            if row["status"] == "uncertain":
                return "waiting", "Faculty is checking whether the correction reached Gradescope."
            if row["status"] == "accepted" and text(row["faculty_points"]) == step.get("to"):
                return "done", ""
            return "stopped", row["error"] or f"Gradescope: {row['status']}."
        if db.execute("SELECT 1 FROM frq_gradescope_mc_checks WHERE assignment_id=? AND status IN ('pending','sending') LIMIT 1", (run["assignment_id"],)).fetchone():
            return "waiting", "Faculty is reading Gradescope's multiple-choice scores."
        read = db.execute("""SELECT status,scores_json,error,updated_at FROM frq_gradescope_mc_checks WHERE assignment_id=? AND section_id=? AND updated_at>=?
            ORDER BY updated_at DESC,rowid DESC LIMIT 1""", (run["assignment_id"], sheet["section_id"], since)).fetchone()
        if not read:
            if not receipt.get("check_id"):  # no read was queued (no linked section) and none was running to wait for
                return "stopped", "Faculty could not ask Gradescope for its multiple-choice scores: no section links this exam's bubble sheets."
            return "waiting", "Faculty is reading Gradescope's multiple-choice scores."
        if read["status"] == "failed":
            return "stopped", read["error"] or "Faculty could not read Gradescope's scores."
        theirs = ((json.loads(read["scores_json"] or "{}").get("students") or {}).get(sheet["enrollment_id"]) or {})
        shown = text((theirs.get("questions") or {}).get(str(sheet["number"])))
        if shown == step.get("to") and str(theirs.get("version") or "") == sheet.get("version"):
            return "already", "Gradescope already shows it."
        if seconds_since(read["updated_at"]) < SETTLE:
            return "waiting", "Faculty is correcting the score on Gradescope."
        if str(theirs.get("version") or "") != sheet.get("version"):
            return "stopped", (f"Gradescope has this sheet under Version {theirs.get('version') or '?'}, Faculty under Version {sheet.get('version')}: "
                               "Faculty never corrects across versions; change it there by hand.")
        return "stopped", f"Gradescope shows {shown or 'no score'} for this question and Faculty could not correct it: look at the Gradescope panel's MC check."

    def _mc_start_gradescope(self, aid, run, plan, step, actor):
        """Asks for a read of Gradescope's scores (the instructor's Check MC, approved on this decision): the read queues the correction."""
        found = self._mc_question(aid, {"sheet_id": plan["sheet"]["sheet_id"], "number": plan["sheet"]["number"], "exam_version": plan["sheet"]["version"]})
        if found.get("earned") != step.get("to"):
            raise Conflict(f"Faculty's score for the question is {found.get('earned')} now, not the {step.get('to')} you approved: look at it again.")
        since = timestamp()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            check = self.sender._mc_queue(db, aid, None, "workspace")
            db.commit()
        self.sender.wake.set()
        self._record(run["id"], "gradescope", "started", "", {"since": since, "check_id": check})
        return False

    def _mc_start_gradebook(self, aid, run, plan, step, request_id, actor):
        done = self.change.amend(aid, {"request_id": request_id, "confirmation": CHANGE, "fingerprint": plan["fingerprints"]["gradebook"],
                                       "sheet_row": f"{plan['sheet']['section_id']}:{plan['sheet']['enrollment_id']}",
                                       "replace": bool(step.get("replace")), "message_id": run["message_id"]}, actor)
        self._record(run["id"], "gradebook", "done", "", {"certification_id": done["certification"]["id"], "written": done.get("gradebook", {}).get("written")})
        return True
