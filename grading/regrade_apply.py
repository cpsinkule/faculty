"""Answering a Gradescope regrade request in one panel on the Return stage, and carrying the decision everywhere (instructor,
2026-10-07: "let's brainstorm how to make this easier and more user friendly, and also ensure that the result of my decision is
reflected in the gradebook and also in gradescope"; asked: certify just this change, include Canvas, the Return stage with names, a
reply drafted by a model). REGRADES.

READING (`listing`, `detail`): the plan's requests with each student's name and section (the Return stage is not anonymous) and
Faculty's score; one request with where its grade is: Faculty's final grade, what Faculty last put on Gradescope (its receipt), the
certified total, the gradebook cell and what Faculty last put in Canvas, the latest reply draft and the latest Apply. Catalog only.

APPLY. The preview (`preview`, reads Gradescope and Canvas, writes nothing) lists only the steps something differs on, each from the
module that owns it, in order:
1. `gradescope`: that one grade (`GradescopeSender` with `only_responses`, its published confirmation as the send dialog's).
2. `reply`: the reply, posted only once Gradescope shows Faculty's score (`expect_score`); it answers and closes the request.
3. `gradebook`: CERTIFY JUST THIS CHANGE (`certification_change.OneChange`), whatever else changed since certifying (A REGRADE NEVER
   ASKS TO CERTIFY AGAIN: the others keep waiting); only this grade's own state (not certified, this student's other grades changed
   too) stops it, and the preview names why.
4. `canvas`: that student's cell (`gradebook.canvas_cell`), sent only when the cell holds the value the preview forecast and Canvas
   still shows what it showed.
`apply` names the preview's fingerprint, a request ID (uuid), `confirmation: "apply"`, the reply and the published exams confirmed;
everything is read again (409 when anything changed), the approval is recorded (`frq_regrade_applies`, immutable) and the first step
starts. The page, while it is open, posts `continue` once a step's receipt settles; nothing runs by itself after a restart, and a
closed page leaves the run waiting with Continue. Each step's request ID is derived from the run, the step and its attempt
(`uuid5`), so a repeated post never writes twice; each step appends what it did (`frq_regrade_apply_steps`) naming its own receipt.
A stopped step says why and waits for the instructor's Retry (or for Sync with Canvas, the Gradescope panel or Certify again).
"""
from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal

from werkzeug.exceptions import Conflict, HTTPException, NotFound

from course_storage import timestamp
from gradebook import canvas_cell, common
from rosters import connect

from . import gradescope_web as gw
from . import progress
from .certification import Refused
from .certification_change import CONFIRMATION as CHANGE, OneChange, certified_content, score_text
from .models import digest, uid
from .regrade_mc import McApplyMixin, question as mc_question
from .regrade_requests import RegradeRequests, captured, match

CONFIRMATION = "apply"
NAMESPACE = uuid.UUID("6d1c3f52-8b0e-4d1a-9a57-2f1e0b9c7a31")
STEPS = ("gradescope", "reply", "gradebook", "canvas")
TITLES = {"grade": "Grade", "gradescope": "Gradescope", "reply": "Reply", "gradebook": "Gradebook", "canvas": "Canvas"}


def text(value):
    return progress.amount(value)


def run_plan(run):
    """A recorded Apply's plan (its kind and, for multiple choice, its sheet)."""
    value = run["plan_json"]  # a dict or a catalog row
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def value_text(value):
    """A gradebook or Canvas value as the panel says it: "74", "Excused", "Missing", or None."""
    if not value:
        return None
    if value.get("status") == "scored":
        return text(value.get("points"))
    return {"excused": "Excused", "missing": "Missing", "cleared": None}.get(value.get("status"), value.get("status"))


class RegradeApply(McApplyMixin):
    def __init__(self, regrades, sender, certification, gradebook, drafts=None):
        self.regrades, self.sender, self.certification, self.gradebook, self.drafts = regrades, sender, certification, gradebook, drafts
        self.change = OneChange(certification)
        self.frq, self.rosters, self.path = regrades.frq, regrades.rosters, regrades.path

    # ----- reading ----------------------------------------------------------------------------------------------------------------
    def _faculty(self, db, aid, rid):
        if not rid:
            return None
        _, decisions, _ = self.frq.final_decisions(db, aid, response_id=rid)
        for decision in decisions:
            score = decision["final"].get("score") or {}
            return {"points": text(score.get("score")), "of": text(score.get("max_score"))}
        return None

    def listing(self, aid):
        """The Return stage's list: the plan's requests (open first) with each student's name and section and Faculty's score."""
        found = self.regrades.listing(aid)
        with connect(self.path) as db:
            keys, sheets = {}, {}
            requests = {r["id"]: r for r in captured(db)} if any(item.get("kind") == "mc" for item in found["requests"]) else {}
            for item in found["requests"]:
                if item["response_id"]:
                    row = db.execute("SELECT section_id,enrollment_id FROM frq_responses WHERE id=?", (item["response_id"],)).fetchone()
                    keys[item["id"]] = (row["section_id"], row["enrollment_id"])
                elif item.get("kind") == "mc" and item["id"] in requests:
                    # REGRADES "BUBBLE SHEETS": the student's sheet; the student is the request's own (their Gradescope SID)
                    placed = match(db, requests[item["id"]])
                    if placed.get("sheet_id"):
                        keys[item["id"]] = (placed["section_id"], requests[item["id"]]["enrollment_id"])
                        sheets[item["id"]] = placed
        view = self.frq._bubble_view(aid)[1] if sheets else None
        names = {}
        if keys:
            with self.rosters.lock(), connect(self.path) as db:
                plan = self.frq.plan(db, aid)
                names = self.certification._names(db, self.frq.exams._event(db, plan["event_id"]), sorted(set(keys.values())))
        with self.rosters.lock(), connect(self.path) as db:
            for item in found["requests"]:
                who = names.get(keys.get(item["id"])) or {}
                item["student"] = {"name": who.get("name", ""), "section": who.get("section", "")} if who else None
                if item["id"] in sheets:
                    line = mc_question(view, sheets[item["id"]])
                    item["faculty"] = {"points": line.get("earned"), "of": line.get("points")} if line.get("earned") is not None else None
                else:
                    item["faculty"] = self._faculty(db, aid, item["response_id"])
                run = self._latest_run(db, aid, item["id"])
                item["apply"] = {"id": run["id"], "created_at": run["created_at"]} if run else None
        return found

    @staticmethod
    def _latest_run(db, aid, message_id):
        row = db.execute("SELECT * FROM frq_regrade_applies WHERE assignment_id=? AND message_id=? ORDER BY rowid DESC LIMIT 1", (aid, message_id)).fetchone()
        return dict(row) if row else None

    def _where(self, db, aid, rid):
        """Where the grade is, from the catalog: Faculty, Faculty's Gradescope receipt, the certified total, the gradebook cell and
        Faculty's last Canvas receipt."""
        response = db.execute("SELECT section_id,enrollment_id,question_id FROM frq_responses WHERE id=?", (rid,)).fetchone()
        row = common.row_key(response["section_id"], response["enrollment_id"])
        where = {"faculty": self._faculty(db, aid, rid), "row": row}
        receipt = db.execute("""SELECT expected_score FROM frq_gradescope_grades WHERE response_id=? AND status='accepted' ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                             (rid,)).fetchone()
        sent = text(receipt["expected_score"]) if receipt else None
        # What Faculty last put on Gradescope against its final grade now (the sender reads Gradescope itself before it writes).
        state = "not_final" if not where["faculty"] else "not_sent" if sent is None else "sent" if sent == where["faculty"]["points"] else "changed"
        where["gradescope"] = {"points": sent, "state": state}
        return self._book(db, aid, response["section_id"], response["enrollment_id"], where)

    def _book(self, db, aid, section_id, enrollment_id, where):
        """The certified total, the gradebook cell and Faculty's last Canvas send of one student's row, added to `where`."""
        from gradebook.service import _cells
        plan = self.frq.plan(db, aid)
        row = where["row"]
        latest = progress.latest_certification(db, aid)
        certified = db.execute("SELECT status,earned,possible FROM frq_certification_students WHERE certification_id=? AND section_id=? AND enrollment_id=?",
                               (latest["id"], section_id, enrollment_id)).fetchone() if latest else None
        where["certified"] = {"earned": text(certified["earned"]), "possible": text(certified["possible"]), "status": certified["status"]} if certified else None
        item_id = (json.loads(latest["gradebook_json"] or "{}") or {}).get("item_id") if latest else None
        where["item_id"], where["term_id"] = item_id, plan["term_id"]
        cell = (_cells(db, plan["term_id"]).get(item_id) or {}).get(row) if item_id else None
        where["gradebook"] = {"value": value_text(cell), "source": cell.get("source")} if cell else None
        sent = db.execute("""SELECT canvas_json,sent_json,state,created_at FROM gradebook_canvas_sent WHERE item_id=? AND section_id=? AND enrollment_id=?
            ORDER BY created_at DESC,rowid DESC LIMIT 1""", (item_id, section_id, enrollment_id)).fetchone() if item_id else None
        where["canvas"] = {"value": value_text(json.loads(sent["canvas_json"]) if sent["canvas_json"] else None), "state": sent["state"], "at": sent["created_at"]} if sent else None
        return where

    def detail(self, aid, message_id):
        """One request for the panel: the request, the student, where the grade is, the latest draft and the latest Apply. Catalog only."""
        with connect(self.path) as db:
            request, placed = self.regrades._request(db, aid, message_id)
            state, replies, mark = self.regrades._state(db, aid, request)
        if placed.get("kind") == "mc":
            return self._mc_detail(aid, message_id, request, placed, state, replies, mark)
        rid = placed.get("response_id")
        student = self.certification.response_student(aid, rid) if rid else None
        with self.rosters.lock(), connect(self.path) as db:
            where = self._where(db, aid, rid) if rid else None
            draft = self.drafts.latest(db, aid, message_id) if self.drafts else None
            run = self._latest_run(db, aid, message_id)
            view = self._view(db, run) if run else None
        suggestion = self._suggestion(request) if state == "open" else None
        return {"id": message_id, "created_at": request["created_at"], "text": request["text"], "url": request["url"], "state": state,
                "replies": replies, "mark": mark, "reply": request["reply"], "question_title": request["question_title"],
                "response_id": rid, "question_id": placed.get("question_id"), "exam_version": placed.get("exam_version", ""),
                "matched": bool(rid), "reason": placed.get("reason", ""), "can_reply": state == "open" and request["gradescope_request_id"].isdigit(),
                "student": student, "where": where, "draft": draft, "apply": view, "suggestion": suggestion}

    def _suggestion(self, request):
        """Follow-up's suggestion for this request (REGRADES "FOLLOW-UP", one suggestion): {state: none|paused|preparing|ready|
        failed, action: the waiting decide card (id, seen, decision, lines, reply, reason, from, to, of, steps, notes) or None,
        href}. None when Follow-up is not part of this workspace."""
        followups = getattr(self, "followups", None)
        if followups is None:
            return None
        source_key = "gradescope_regrade:" + request["key"].rsplit(":", 1)[0]
        item_key = "conversation:" + source_key
        with connect(self.path) as db:
            paused = followups.paused(db)
            run = followups.latest(db, request["enrollment_id"], item_key)
            actions = followups._actions(db, run["id"]) if run else []
        state = run["state"] if run else "none"
        card = next((a for a in reversed(actions) if a["endpoint"] == "frq_regrade_panel.decide" and a["pending"]), None)
        shown = ((card or {}).get("preview") or {}).get("data") or {}
        found = {"state": "paused" if paused and state in ("none", "queued") else "preparing" if state in ("queued", "running")
                 else "failed" if state in ("failed", "interrupted") else "ready" if card else state,
                 "href": "#follow-ups?filter=regrades", "run": run["id"] if run else None, "action": None}
        if card:
            found["action"] = {"id": card["id"], "seen": card["seen"], **{k: card["body"].get(k) for k in ("decision", "lines", "adjustment", "reply", "reason")},
                               **{k: shown.get(k) for k in ("from", "to", "of", "steps", "notes")},
                               # REGRADES "BUBBLE SHEETS": the reading the card proposes, what Faculty reads now and the key
                               **({"answer": card["body"].get("answer"), "read": shown.get("read"), "key": shown.get("key"),
                                   "number": shown.get("number")} if shown.get("kind") == "mc" else {})}
        return found

    # ----- the preview ------------------------------------------------------------------------------------------------------------
    def _plan(self, aid, message_id, reply, replace=False):
        """Every step something differs on, each previewed by its owner (reads Gradescope and Canvas; writes nothing)."""
        with connect(self.path) as db:
            request, placed = self.regrades._request(db, aid, message_id)
        if placed.get("kind") == "mc":
            return self._mc_plan(aid, message_id, request, placed, reply, replace)
        rid = placed.get("response_id")
        with self.rosters.lock(), connect(self.path) as db:
            where = self._where(db, aid, rid) if rid else None
        steps, notes, published, fingerprints = [], [], [], {}
        faculty = (where or {}).get("faculty")
        expect = None
        if rid and faculty:
            view = self.sender.preview(aid, {"only_responses": [rid]}) if self.sender.settings.configured else None
            if view is None:
                notes.append("Gradescope's saved sign-in is not set up: change the grade there by hand.")
            else:
                grades = view["summary"].get("grades") or {}
                writes = grades.get("send", 0) + grades.get("update", 0)
                if view.get("running"):
                    steps.append({"step": "gradescope", "from": where["gradescope"]["points"], "to": faculty["points"],
                                  "problem": "A send to Gradescope is running for this assignment: Apply waits for it.", "href": f"#grading/{aid}?tab=export&open=gradescope"})
                elif writes == 1:
                    steps.append({"step": "gradescope", "from": where["gradescope"]["points"], "to": faculty["points"],
                                  "published": [p["container"] for p in view["published"]]})
                    published = [p["container_id"] for p in view["published"]]
                    fingerprints["gradescope"] = view["fingerprint"]
                    expect = faculty["points"]
                elif grades.get("already") == 1:
                    expect = faculty["points"]
                else:
                    reason = next((r["reason"] for r in view.get("refused") or []), None) or next((s["reason"] for s in view.get("skipped") or []), None) \
                        or next((s["problem"] for s in view.get("sections") or [] if s.get("problem")), None) or "Faculty cannot send this grade to Gradescope."
                    notes.append(f"Gradescope: {reason} Change it there by hand if it differs.")
        with connect(self.path) as db:
            open_now, _, _ = self.regrades._state(db, aid, request)
        if open_now == "open" and request["gradescope_request_id"].isdigit():
            if not reply:
                raise ValueError("Write your reply to the student.")
            found = self.regrades.preview(aid, message_id, {"reply": reply})
            if not found["open"]:
                notes.append("The request was already answered on Gradescope.")
            else:
                steps.append({"step": "reply", "expect": expect})
                fingerprints["reply"] = found["fingerprint"]
        if rid and faculty:
            change = self.change.check(aid, rid)
            if change["possible"]:
                gradebook = where.get("gradebook") or {}
                after = None
                if gradebook.get("source") == "grading" and re.fullmatch(r"-?\d+(\.\d+)?", gradebook.get("value") or "") \
                        and change["from"] is not None and change["to"] is not None:
                    after = text(Decimal(gradebook["value"]) + Decimal(change["to"]) - Decimal(change["from"]))
                typed = gradebook.get("source") == "instructor"
                steps.append({"step": "gradebook", "from": gradebook.get("value"), "to": after, "typed": typed, "replace": bool(replace and typed),
                              "question": {"from": change["from"], "to": change["to"]}})
                fingerprints["gradebook"] = change["fingerprint"]
                if after is not None and where.get("item_id") and not (typed and not replace):
                    self._canvas_step(where, after, steps, notes, fingerprints)
            else:
                # A GRADE AS CERTIFIED: the gradebook already holds it, so nothing else that changed since certifying stops this request.
                settled = change["done"] or change["same"]
                if change["state"] != "none" and not settled:
                    notes.append(" ".join(change["reasons"]))
                    steps.append({"step": "gradebook", "problem": " ".join(change["reasons"]), "href": f"#grading/{aid}?tab=export", "action": "Certify again…"})
                elif change["state"] == "none":
                    notes.append("The grades are not certified yet: they go to the gradebook when you certify.")
                cell = (where.get("gradebook") or {}).get("value")
                if settled and cell is not None and where.get("item_id"):
                    self._canvas_step(where, cell, steps, notes, fingerprints, only_if_differs=True)
        plan = {"message_id": message_id, "response_id": rid, "steps": steps, "notes": notes, "published": published, "replace": bool(replace),
                "reply_sha256": digest(reply or ""), "faculty": faculty}
        plan["fingerprint"] = digest({"steps": steps, "notes": notes, "published": published, "fingerprints": fingerprints, "reply": plan["reply_sha256"],
                                      "replace": plan["replace"]})
        return plan, fingerprints, where

    def _canvas_step(self, where, after, steps, notes, fingerprints, only_if_differs=False):
        try:
            found = canvas_cell.preview(self.gradebook, where["term_id"], {"item_id": where["item_id"], "row": where["row"]})
        except (ValueError, HTTPException) as error:  # not linked, signed out, unreachable: Canvas is left to Sync with Canvas
            notes.append(f"Canvas: {getattr(error, 'description', None) or error}")
            return
        now = value_text(found.get("canvas"))
        if only_if_differs and not found["ready"]:
            if found["action"] == "held":
                notes.append(f"Canvas shows {now}, which someone changed there: keep Faculty's in Sync with Canvas.")
            return
        if now == after:
            return
        problem = None
        if found["problems"]:
            problem = "; ".join(found["problems"])
        elif found["action"] == "held":
            problem = f"Canvas shows {now}, which someone changed there: keep Faculty's in Sync with Canvas."
        steps.append({"step": "canvas", "from": now, "to": after, "course": found.get("course"), "item_id": where["item_id"], "row": where["row"],
                      **({"problem": problem, "href": "#gradebook", "action": "Sync with Canvas"} if problem else {})})
        fingerprints["canvas"] = digest({"canvas": found.get("canvas"), "action": found["action"]})

    def preview(self, aid, message_id, data):
        data = data if isinstance(data, dict) else {}
        reply = self.regrades._text(data) if data.get("reply") else None
        replace = data.get("replace", False)
        if not isinstance(replace, bool):
            raise ValueError("Say replace as true or false.")
        plan, _, where = self._plan(aid, message_id, reply, replace)
        for step in plan["steps"]:
            step["title"] = TITLES[step["step"]]
        return {**plan, "where": where}

    # ----- apply -----------------------------------------------------------------------------------------------------------------
    def apply(self, aid, message_id, data, actor="workspace"):
        data = data if isinstance(data, dict) else {}
        request_id = str(data.get("request_id") or "")
        try:
            uuid.UUID(request_id)
        except ValueError:
            raise ValueError("Apply needs a request ID.") from None
        if data.get("confirmation") != CONFIRMATION:
            raise ValueError("Confirm what Apply changes.")
        fingerprint = str(data.get("fingerprint") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("Apply the preview you looked at.")
        reply = self.regrades._text(data) if data.get("reply") else None
        replace = data.get("replace", False)
        published = data.get("published")
        published = [] if published is None else published
        if not isinstance(replace, bool) or not isinstance(published, list) or not all(isinstance(c, str) for c in published):
            raise ValueError("Say replace as true or false and name the published assignments you confirmed.")
        confirmed = sorted(set(published))
        request_sha = digest({"message": message_id, "fingerprint": fingerprint, "reply": reply, "replace": replace, "published": confirmed})
        with connect(self.path) as db:
            earlier = db.execute("SELECT * FROM frq_regrade_applies WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone()
        if earlier:
            if earlier["request_sha256"] != request_sha or earlier["message_id"] != message_id:
                raise Conflict("That request ID was already used for a different Apply.")
            return self.advance(aid, earlier["id"])
        plan, fingerprints, _ = self._plan(aid, message_id, reply, replace)
        if plan["fingerprint"] != fingerprint:
            raise Conflict("Something changed since the preview, in Faculty, on Gradescope or in Canvas. Look at it again.")
        if sorted(plan["published"]) != confirmed:
            raise ValueError("Confirm writing to the published Gradescope assignment: students see the new grade at once.")
        blocked = [s for s in plan["steps"] if s.get("problem") and s["step"] == "gradescope"]
        if blocked:
            raise Conflict(blocked[0]["problem"])
        if not [s for s in plan["steps"] if not s.get("problem")]:
            raise Conflict("Nothing needs to change: everything already agrees and the request is answered.")
        return self._record_run(aid, message_id, plan, fingerprints, reply, request_id, request_sha, fingerprint, actor)

    def _record_run(self, aid, message_id, plan, fingerprints, reply, request_id, request_sha, fingerprint, actor, decision=None):
        """The approval recorded (immutable), then its first steps started. `decision`: the Follow-up card it came from."""
        run_id = uid()
        stored = {**plan, "fingerprints": fingerprints, "reply": reply, **({"decision": decision} if decision else {})}
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM frq_regrade_applies WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone():
                raise Conflict("This Apply was just started from another window.")
            live = self._latest_run(db, aid, message_id)
            if live and not self._view(db, live)["finished"]:
                raise Conflict("An earlier Apply of this request is still running: continue or cancel it first.")
            db.execute("""INSERT INTO frq_regrade_applies(id,assignment_id,response_id,message_id,request_id,request_sha256,fingerprint,plan_json,actor,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (run_id, aid, plan["response_id"], message_id, request_id, request_sha, fingerprint,
                                                 json.dumps(stored, sort_keys=True, default=str), actor, timestamp()))
            self.frq.event(db, aid, "regrade_apply", {"apply_id": run_id, "message_id": message_id, "steps": [s["step"] for s in plan["steps"] if not s.get("problem")],
                                                      **({"decision": decision.get("decision")} if decision else {})}, actor)
            db.commit()
        return self.advance(aid, run_id, actor=actor)

    # ----- a decision: a Follow-up card (REGRADES "FOLLOW-UP", 2026-10-07) ------------------------------------------------------
    @staticmethod
    def _decision(data):
        """{decision: keep|change, lines, adjustment, reply, reason} of a decide request, checked."""
        decision = data.get("decision")
        if decision not in ("keep", "change"):
            raise ValueError('Say "decision": "keep" or "change".')
        lines, adjustment = [], None
        if decision == "change":
            lines = data.get("lines")
            if not isinstance(lines, list) or not all(isinstance(line, str) and line for line in lines) or len(set(lines)) != len(lines):
                raise ValueError('A change names the rubric lines the grade should have ("lines", each once).')
            adjustment = data.get("adjustment")
            if adjustment is not None:
                if not isinstance(adjustment, dict) or isinstance(adjustment.get("points"), bool) or not isinstance(adjustment.get("points"), (int, float)) \
                        or not isinstance(adjustment.get("reason"), str) or not adjustment["reason"].strip() or not adjustment["points"]:
                    raise ValueError('An adjustment is {"points": a number other than 0, "reason": why}.')
                adjustment = {"points": adjustment["points"], "reason": " ".join(adjustment["reason"].split())[:500]}
        reply = RegradeRequests._text(data)
        reason = data.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError("Say why in at most 1000 characters.")
        return {"decision": decision, "lines": sorted(lines), "adjustment": adjustment, "reply": reply, "reason": " ".join(reason.split())}

    def _final_grade(self, db, aid, rid):
        """(lines, adjustments) of the response's final grade. A change keeps its point adjustments (a batch line from Certification
        and Export among them) and adds the decision's own: a regrade never drops a line given to the whole class."""
        score = ((self.frq.final_decisions(db, aid, response_id=rid)[1] or [{}])[0].get("final") or {}).get("score") or {}
        return sorted(score.get("selected") or []), [{"points": a.get("points"), "reason": a.get("reason")} for a in score.get("adjustments") or []]

    def _line_words(self, db, aid, rid, ids):
        """{line id: "2. Correctly labeled …"}: each named line as the rubric numbers and words it (its position, as the page does),
        math delimiters left out, for the card that names what a change adds or removes."""
        if not ids:
            return {}
        row = db.execute("SELECT question_id FROM frq_responses WHERE id=?", (rid,)).fetchone()
        items = ((self.frq.question(db, aid, row["question_id"]).get("rubric") or {}).get("rules") or {}).get("items") or []
        return {item["id"]: f"{n}. " + " ".join(re.sub(r"\$\$?|\\[()\[\]]", "", str(item.get("description") or "")).split())
                for n, item in enumerate(items, 1) if item.get("id") in ids}

    def decide_preview(self, aid, message_id, data):
        """What approving a decision would do (reads Gradescope and Canvas; writes nothing): the grade change, then the Apply steps
        forecast for the grade as it would be. The fingerprint pins the response's record, the decision, the reply, publication on
        Gradescope, the certification and what the gradebook and Canvas hold."""
        return self._decision_preview(aid, message_id, data)[0]

    def _decision_preview(self, aid, message_id, data):
        """The public preview and the exact response record it checked, for the version-checked review save."""
        data = data if isinstance(data, dict) else {}
        with connect(self.path) as db:
            request, placed = self.regrades._request(db, aid, message_id)
        if placed.get("kind") == "mc":
            preview, pinned = self._mc_decision_preview(aid, message_id, request, placed, data)
            for step in preview["steps"]:
                step["title"] = TITLES.get(step["step"], "Grade")
            return preview, pinned
        decision = self._decision(data)
        rid = placed.get("response_id")
        if not rid:
            raise ValueError("This regrade request is not linked to a response: answer it on Gradescope.")
        with self.rosters.lock(), connect(self.path) as db:
            response = dict(db.execute("SELECT id,version,current_result_id,current_review_id FROM frq_responses WHERE id=?", (rid,)).fetchone())
            where = self._where(db, aid, rid)
            selected, kept = self._final_grade(db, aid, rid)
            words = self._line_words(db, aid, rid, set(decision["lines"]) ^ set(selected)) if decision["decision"] == "change" else {}
        faculty = where.get("faculty")
        if not faculty:
            raise ValueError("This response has no final grade: grade it on the Review step first.")
        before, after, notes, steps = faculty["points"], faculty["points"], [], []
        if decision["decision"] == "change":
            proposed = self.frq.preview_score(rid, {"version": response["version"], "selected_items": decision["lines"],
                                                    "adjustments": [*kept, *([decision["adjustment"]] if decision["adjustment"] else [])]})
            after = text(proposed["score"])
            if decision["lines"] == selected and not decision["adjustment"]:
                raise ValueError('The grade already has exactly these lines: propose "keep".')
            steps.append({"step": "grade", "from": before, "to": after, "of": faculty["of"],
                          "added": sorted(set(decision["lines"]) - set(selected)), "removed": sorted(set(selected) - set(decision["lines"]))})
        published, fingerprints = [], {}
        if self.sender.settings.configured:
            view = self.sender.preview(aid, {"only_responses": [rid]})
            section = next(iter(view.get("sections") or []), {})
            grades = view["summary"].get("grades") or {}
            if view.get("running"):
                steps.append({"step": "gradescope", "from": where["gradescope"]["points"], "to": after,
                              "problem": "A send to Gradescope is running for this assignment: approve once it has finished.", "href": f"#grading/{aid}?tab=export&open=gradescope"})
            elif grades.get("held") or grades.get("refused"):
                reason = next((r["reason"] for r in view.get("refused") or []), None) or next((s["reason"] for s in view.get("skipped") or []), None) or "Faculty cannot send it."
                notes.append(f"Gradescope: {reason} Change it there by hand.")
            elif after != where["gradescope"]["points"] or grades.get("send") or grades.get("update"):
                if section.get("published"):
                    published = [section.get("container_id")]
                steps.append({"step": "gradescope", "from": where["gradescope"]["points"], "to": after,
                              "published": [section.get("container")] if section.get("published") else []})
            fingerprints["gradescope"] = digest({"section": {k: section.get(k) for k in ("course_id", "container_id", "published", "problem")},
                                                 "summary": view["summary"], "refused": view.get("refused"), "skipped": view.get("skipped")})
        else:
            notes.append("Gradescope's saved sign-in is not set up: change the grade there by hand.")
        found = self.regrades.preview(aid, message_id, {"reply": decision["reply"]})
        if not found["open"]:
            raise Conflict("This regrade request was already answered on Gradescope.")
        steps.append({"step": "reply"})
        fingerprints["reply"] = found["fingerprint"]
        check = self.change.check(aid, rid)
        cell = where.get("gradebook") or {}
        if check["state"] == "none":
            notes.append("The grades are not certified yet: they go to the gradebook when you certify.")
        elif check["done"] or check["possible"]:
            delta = Decimal(after) - Decimal(check["from"] if check["possible"] and check.get("from") is not None else before)
            forecast = text(Decimal(cell["value"]) + delta) if cell.get("source") == "grading" and re.fullmatch(r"-?\d+(\.\d+)?", cell.get("value") or "") else None
            if delta or check["possible"]:
                steps.append({"step": "gradebook", "from": cell.get("value"), "to": forecast, "typed": cell.get("source") == "instructor", "replace": False})
                if forecast is not None and where.get("item_id"):
                    self._canvas_step(where, forecast, steps, notes, fingerprints)
        elif check["same"] and after == before:
            pass  # A GRADE AS CERTIFIED: a kept grade the gradebook already holds has no gradebook step, whatever else changed since
        else:
            steps.append({"step": "gradebook", "problem": " ".join(check["reasons"]), "href": f"#grading/{aid}?tab=export", "action": "Certify again…"})
        for step in steps:
            step["title"] = TITLES.get(step["step"], "Grade")
        fingerprint = digest({"response": response, "decision": {k: decision[k] for k in ("decision", "lines", "adjustment")}, "reply": digest(decision["reply"]),
                              "to": after, "steps": steps, "fingerprints": fingerprints, "published": published,
                              "certification": [check.get("state"), check.get("fingerprint")], "cell": cell})
        # `current` (the final grade's lines and kept adjustments) and `line_words` are for the card and its edit form: the
        # fingerprint pins the response's record, which already fixes them.
        return {"message_id": message_id, "response_id": rid, **{k: decision[k] for k in ("decision", "lines", "adjustment", "reason")},
                "reply": decision["reply"], "from": before, "to": after, "of": faculty["of"], "steps": steps, "notes": notes,
                "published": published, "fingerprint": fingerprint, "current": {"lines": selected, "adjustments": kept}, "line_words": words}, response

    def decide(self, aid, message_id, data, actor="workspace"):
        """The approved decision: the preview again (409 when anything changed), the grade saved through the ordinary review when it
        changes, then the same Apply as Return's, recorded with the decision and started. Idempotent by request ID."""
        data = data if isinstance(data, dict) else {}
        request_id = str(data.get("request_id") or "")
        try:
            uuid.UUID(request_id)
        except ValueError:
            raise ValueError("A decision needs a request ID.") from None
        if data.get("confirmation") != "decide":
            raise ValueError("Confirm the decision.")
        fingerprint = str(data.get("fingerprint") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("Approve the preview you looked at.")
        with connect(self.path) as db:
            request, placed = self.regrades._request(db, aid, message_id)
        mc = placed.get("kind") == "mc"
        decision = self._mc_decision(data) if mc else self._decision(data)
        request_sha = digest({"message": message_id, "fingerprint": fingerprint, "decision": decision})
        with connect(self.path) as db:
            earlier = db.execute("SELECT * FROM frq_regrade_applies WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone()
        if earlier:
            if earlier["request_sha256"] != request_sha or earlier["message_id"] != message_id:
                raise Conflict("That request ID was already used for a different decision.")
            return {"status": "started", "apply": self.advance(aid, earlier["id"], actor=actor)}
        if mc:
            return self._mc_decide(aid, message_id, request, placed, data, request_id, request_sha, fingerprint, actor)
        preview, response = self._decision_preview(aid, message_id, data)
        if preview["fingerprint"] != fingerprint:
            raise Conflict("Something changed since the preview, in Faculty, on Gradescope or in Canvas. Look at it again.")
        blocked = [s for s in preview["steps"] if s.get("problem") and s["step"] == "gradescope"]
        if blocked:
            raise Conflict(blocked[0]["problem"])
        rid = preview["response_id"]
        if decision["decision"] == "change":
            # REGRADES: use the record pinned by the preview. Reading a newer version here silently overwrote a review saved
            # while the outside-service checks ran, although the instructor approved the earlier grade's fingerprint.
            self.frq.save_review(rid, {"version": response["version"], "result_id": response["current_result_id"], "status": "approved", "confirm_review": True,
                                       "selected_items": decision["lines"], "adjustments": [*preview["current"]["adjustments"], *([decision["adjustment"]] if decision["adjustment"] else [])],
                                       "comment": decision["reason"][:2000]})
        plan, fingerprints, _ = self._plan(aid, message_id, decision["reply"])
        if sorted(plan["published"]) != sorted(p for p in preview["published"] if p):
            plan["notes"] = [*plan.get("notes", []), "Gradescope's publication changed after the approval: the Gradescope step waits for you."]
            plan["steps"] = [{**s, "problem": "Publication on Gradescope changed since you approved: send it from the Gradescope panel."}
                             if s["step"] == "gradescope" else s for s in plan["steps"]]
        view = self._record_run(aid, message_id, plan, fingerprints, decision["reply"], request_id, request_sha, fingerprint, actor,
                                decision={k: decision[k] for k in ("decision", "lines", "adjustment", "reason")})
        return {"status": "started", "apply": view, "from": preview["from"], "to": preview["to"]}

    # ----- the steps ----------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _rows(db, run_id):
        found = {}
        for row in db.execute("SELECT * FROM frq_regrade_apply_steps WHERE apply_id=? ORDER BY created_at,rowid", (run_id,)):
            found.setdefault(row["step"], []).append(dict(row))
        return found

    def _record(self, run_id, step, status, detail="", receipt=None):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO frq_regrade_apply_steps VALUES(?,?,?,?,?,?,?)",
                       (uid(), run_id, step, status, str(detail or "")[:1000], json.dumps(receipt or {}, sort_keys=True), timestamp()))
            db.commit()

    @staticmethod
    def _request_id(run_id, step, attempt):
        return str(uuid.uuid5(NAMESPACE, f"{run_id}:{step}:{attempt}"))

    def _state(self, db, run, step, rows):
        """(status, detail, receipt) of one step from its own rows and receipts: pending, waiting, done, already, stopped."""
        mine = rows.get(step["step"]) or []
        last = mine[-1] if mine else None
        if step.get("problem"):
            return "stopped", step["problem"], {}
        if not last:
            return "pending", "", {}
        receipt = json.loads(last["receipt_json"] or "{}")
        if step["step"] == "gradebook" and last["status"] == "stopped":
            whole = self._certified_whole(db, run, step, last)
            if whole:
                return "already", "Certified with the whole assignment.", {"certification_id": whole}
        if last["status"] in ("done", "already", "stopped"):
            return last["status"], last["detail"], receipt
        if step["step"] == "gradescope" and run_plan(run).get("kind") == "mc":
            status, detail = self._mc_gradescope_state(db, run, run_plan(run), step, receipt)
            return status, detail, receipt
        if step["step"] == "gradescope":
            unit = db.execute("SELECT status,error FROM frq_gradescope_grades WHERE send_id=? AND response_id=? ORDER BY rowid DESC LIMIT 1",
                              (receipt.get("send_id"), run["response_id"])).fetchone()
            if not unit or unit["status"] in ("pending", "sending", "uncertain"):
                return "waiting", "Faculty is writing the grade on Gradescope." if not unit or unit["status"] != "uncertain" else \
                    "Faculty is checking whether the grade reached Gradescope.", receipt
            if unit["status"] == "accepted":
                return "done", "", receipt
            return "stopped", unit["error"] or f"Gradescope: {unit['status']}.", receipt
        if step["step"] == "reply":
            reply = next((r for r in self.regrades._replies(db, run["message_id"]) if r["id"] == receipt.get("reply_id")), None)
            if reply and reply["status"] == "accepted":
                return "done", "", receipt
            if reply and reply["status"] == "uncertain":
                return "stopped", reply["detail"] or "Faculty could not read Gradescope back: check on Gradescope.", {**receipt, "check": reply["id"]}
            return "stopped", (reply or {}).get("detail") or "Gradescope does not show the reply.", receipt
        if step["step"] == "canvas":
            sent = db.execute("SELECT status FROM gradebook_canvas_sends WHERE id=?", (receipt.get("send_id"),)).fetchone()
            got = db.execute("SELECT state FROM gradebook_canvas_sent WHERE send_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (receipt.get("send_id"),)).fetchone()
            if sent and sent["status"] == "running" and (not got or got["state"] == "uncertain"):
                return "waiting", "Faculty is writing the grade in Canvas.", receipt
            if got and got["state"] == "accepted":
                return "done", "", receipt
            return "stopped", "Canvas does not show the grade: send it with Sync with Canvas." if got else "The send to Canvas did not finish.", receipt
        return "waiting", "", receipt

    @staticmethod
    def _certified_whole(db, run, step, stopped):
        """CERTIFIED WITH THE WHOLE ASSIGNMENT (requirement, 2026-10-21): a stopped gradebook step is settled by a certification made
        after the stop whose row holds this response's grade as the run approved it; the run then goes on to Canvas. Returns that
        certification's ID, or None."""
        latest = progress.latest_certification(db, run["assignment_id"])
        target = (step.get("question") or {}).get("to")
        if not latest or target is None or latest["created_at"] <= stopped["created_at"]:
            return None
        sheet = run_plan(run).get("sheet")
        if sheet:  # REGRADES "BUBBLE SHEETS": the sheet's points as the run approved them
            record = db.execute("SELECT parts_json FROM frq_certification_students WHERE certification_id=? AND section_id=? AND enrollment_id=?",
                                (latest["id"], sheet["section_id"], sheet["enrollment_id"])).fetchone()
            bubble = (json.loads(record["parts_json"]).get("bubble") or {}) if record else {}
            return latest["id"] if text(bubble.get("earned")) == target else None
        found = db.execute("SELECT section_id,enrollment_id,question_id FROM frq_responses WHERE id=?", (run["response_id"],)).fetchone()
        record = db.execute("SELECT parts_json FROM frq_certification_students WHERE certification_id=? AND section_id=? AND enrollment_id=?",
                            (latest["id"], found["section_id"], found["enrollment_id"])).fetchone() if found else None
        entry = ((json.loads(record["parts_json"]).get("frq") or {}).get("questions") or {}).get(found["question_id"]) if record else None
        if not entry or entry.get("response_id") != run["response_id"]:
            return None
        return latest["id"] if score_text(certified_content(db, entry)) == target else None

    def _view(self, db, run):
        plan = json.loads(run["plan_json"])
        rows = self._rows(db, run["id"])
        cancelled = bool(rows.get("cancel"))
        steps = []
        for step in plan["steps"]:
            status, detail, receipt = self._state(db, run, step, rows)
            if cancelled and status == "pending":
                status, detail = "cancelled", "Cancelled before it started."
            steps.append({**{k: step.get(k) for k in ("step", "from", "to", "typed", "replace", "published", "course", "question", "href", "action")},
                          "title": TITLES[step["step"]], "status": status, "detail": detail,
                          **({"check": receipt["check"]} if receipt.get("check") else {}),
                          **({"send_id": receipt["send_id"]} if receipt.get("send_id") else {})})
        finished = cancelled or all(s["status"] in ("done", "already", "cancelled") or (s["status"] == "stopped" and plan["steps"][i].get("problem"))
                                    for i, s in enumerate(steps))
        nxt = next((s for s in steps if s["status"] not in ("done", "already", "cancelled")), None)
        return {"id": run["id"], "created_at": run["created_at"], "steps": steps, "notes": plan.get("notes") or [], "finished": finished,
                "waiting": bool(nxt and nxt["status"] == "waiting"), "stopped": bool(nxt and nxt["status"] == "stopped"),
                "next": nxt["step"] if nxt else None}

    def run(self, aid, run_id):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_regrade_applies WHERE id=? AND assignment_id=?", (run_id, aid)).fetchone()
            if not row:
                raise NotFound("That Apply was not found for this assignment.")
            return self._view(db, dict(row))

    def cancel(self, aid, run_id):
        self.run(aid, run_id)
        self._record(run_id, "cancel", "stopped", "Cancelled by the instructor.")
        return self.run(aid, run_id)

    def advance(self, aid, run_id, retry=None, actor="workspace"):
        """Starts every step whose turn it is: a synchronous one (the reply, the certification) finishes here and the next starts; a step
        that waits on its own runner (Gradescope's sender, Canvas's send) ends this call. `retry`: the stopped step to start again."""
        for _ in range(len(STEPS) + 1):
            with connect(self.path) as db:
                row = db.execute("SELECT * FROM frq_regrade_applies WHERE id=? AND assignment_id=?", (run_id, aid)).fetchone()
                if not row:
                    raise NotFound("That Apply was not found for this assignment.")
                run, view, rows = dict(row), self._view(db, dict(row)), self._rows(db, run_id)
            plan = json.loads(run["plan_json"])
            if view["finished"] or view["waiting"] or not view["next"]:
                return view
            step = next(s for s in plan["steps"] if s["step"] == view["next"])
            current = next(s for s in view["steps"] if s["step"] == view["next"])
            if current["status"] == "stopped":
                if retry != step["step"] or step.get("problem"):
                    return view
                retry = None  # one retry per call
            attempt = sum(1 for r in rows.get(step["step"], []) if r["status"] == "stopped" or r["status"] == "started")
            if not self._start(aid, run, plan, step, attempt, rows, actor):
                return self.run(aid, run_id)
        return self.run(aid, run_id)

    def _start(self, aid, run, plan, step, attempt, rows, actor):
        """Starts one step. Returns True when it finished here (the loop goes on to the next), False when it waits or stopped."""
        name, rid, request_id = step["step"], run["response_id"], self._request_id(run["id"], step["step"], attempt)
        try:
            if plan.get("kind") == "mc" and name == "gradescope":
                return self._mc_start_gradescope(aid, run, plan, step, actor)
            if plan.get("kind") == "mc" and name == "gradebook":
                return self._mc_start_gradebook(aid, run, plan, step, request_id, actor)
            if name == "gradescope":
                earlier = next((json.loads(r["receipt_json"]).get("send_id") for r in reversed(rows.get(name, [])) if r["status"] == "started"), None)
                if earlier and attempt:
                    try:
                        self.sender.retry(aid, earlier)
                        self._record(run["id"], name, "started", "Sent again.", {"send_id": earlier})
                        return False
                    except Conflict:
                        pass
                view = self.sender.preview(aid, {"only_responses": [rid]})
                grades = view["summary"]["grades"]
                faculty = self._faculty_now(aid, rid)
                if (faculty or {}).get("points") != step.get("to"):
                    raise Conflict(f"Faculty's grade is {(faculty or {}).get('points')} now, not the {step.get('to')} you approved: look at it again.")
                if grades.get("already") == 1 and not (grades.get("send") or grades.get("update")):
                    self._record(run["id"], name, "already", "Gradescope already shows it.")
                    return True
                sent = self.sender.send(aid, {"request_id": request_id, "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {},
                                              "only_responses": [rid], "published": sorted(plan.get("published") or [])})
                self._record(run["id"], name, "started", "", {"send_id": sent["send_id"]})
                return False
            if name == "reply":
                expect = step.get("expect")
                answer = self.regrades.reply(aid, run["message_id"], {"reply": plan["reply"], "fingerprint": plan["fingerprints"]["reply"], "request_id": request_id,
                                                                     "confirmation": "reply", **({"expect_score": expect} if expect else {})}, actor)
                reply = answer["reply"]
                status = "done" if reply["status"] == "accepted" else "stopped"
                self._record(run["id"], name, "started" if status == "done" else "stopped", reply.get("detail") or "", {"reply_id": reply["id"]})
                if status == "done":
                    self._record(run["id"], name, "done", "", {"reply_id": reply["id"]})
                return status == "done"
            if name == "gradebook":
                done = self.change.amend(aid, {"request_id": request_id, "confirmation": CHANGE, "fingerprint": plan["fingerprints"]["gradebook"],
                                               "response_id": rid, "replace": bool(step.get("replace")), "message_id": run["message_id"]}, actor)
                self._record(run["id"], name, "done", "", {"certification_id": done["certification"]["id"], "written": done.get("gradebook", {}).get("written")})
                return True
            if name == "canvas":
                found = canvas_cell.preview(self.gradebook, self._term(aid), {"item_id": step["item_id"], "row": step["row"]})
                if value_text(found.get("faculty")) != step.get("to"):
                    raise Conflict(f"The gradebook shows {value_text(found.get('faculty'))}, not {step.get('to')}: send it with Sync with Canvas.")
                if value_text(found.get("canvas")) == step.get("to"):
                    self._record(run["id"], name, "already", "Canvas already shows it.")
                    return True
                if value_text(found.get("canvas")) != step.get("from") or not found["ready"]:
                    raise Conflict("; ".join(found["problems"]) or f"Canvas shows {value_text(found.get('canvas'))} now, which someone changed there: "
                                   "keep Faculty's in Sync with Canvas.")
                sent = canvas_cell.send(self.gradebook, self._term(aid), {"item_id": step["item_id"], "row": step["row"], "fingerprint": found["fingerprint"],
                                                                         "request_id": "regrade-" + request_id, "confirmation": canvas_cell.CONFIRMATION}, actor)
                self._record(run["id"], name, "started", "", {"send_id": sent["send"]["id"]})
                return False
        except Refused as refused:
            self._record(run["id"], name, "stopped", refused.description)
            return False
        except (HTTPException, ValueError, gw.GradescopeError) as error:
            # Refused, changed, signed out or unreachable: the step stops with the reason and waits for the instructor's Retry.
            self._record(run["id"], name, "stopped", getattr(error, "description", None) or str(error))
            return False
        return False

    def _faculty_now(self, aid, rid):
        with self.rosters.lock(), connect(self.path) as db:
            return self._faculty(db, aid, rid)

    def _term(self, aid):
        with connect(self.path) as db:
            return self.frq.plan(db, aid)["term_id"]
