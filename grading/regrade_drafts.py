"""A reply to a Gradescope regrade request, drafted by a model for the instructor to edit (requirement, 2026-10-07).

The panel asks for a draft (`request`) when a request opens without one for the grade as it is now, and again after the instructor
changes the grade. The model reads the student's request, the question (problem, answer key, solution), the rubric lines applied and
missed with their points, the reading of the student's work and the crops themselves, and the grade (with the earlier one when it
changed). It writes the reply's body and one sentence on whether the student's point holds (`assessment`: evidence for the
instructor, never a grade). Nothing identifying reaches the model: the student's roster name and any nine-digit number in their words
are replaced, and Faculty adds "Hi <first name>," itself. The draft is a suggestion: the page never puts it over text the instructor
typed, and nothing is sent anywhere until the instructor posts a reply.

A draft is an ordinary cloud call owned by its row (`frq_cloud_calls.regrade_draft_id`), claimed by the grading cloud dispatcher in the
same turn as rubric and quiz-draft work (`grading/cloud.py` calls `claim_runs`, `run`, `recover_runs`, `fail_run`): its receipt is
kept, an answer already bought is replayed, and an answer the backend refuses is asked once more with the reason. The model is the
Rubric drafting and review model, which must read images.
"""
from __future__ import annotations

import json
import re

from werkzeug.exceptions import NotFound

from course_storage import timestamp
from rosters import connect

from . import config, progress, rubrics, storage
from .models import digest, uid
from .regrade_requests import MAX_REPLY  # the validator's limit; a draft is capped under it so a posted draft is accepted

RULE = "regrade-reply-v1"
STEP = "regrade_reply"
PROMPT = "regrade_reply"
MAX_IMAGES = 6
IMAGES = 12_000_000
TOKENS = 4096
NINE = re.compile(r"(?<!\d)\d{9}(?!\d)")


def strip_names(text, name):
    """The student's words with their roster name's parts and any nine-digit number taken out (the model gets no identifier)."""
    found = NINE.sub("[ID]", str(text or ""))
    for part in sorted({p for p in re.split(r"[\s,]+", str(name or "")) if len(p) >= 2}, key=len, reverse=True):
        found = re.sub(rf"(?i)(?<![\w]){re.escape(part)}(?![\w])", "[name]", found)
    return found


def first_name(name):
    """"Example, Alice" -> "Alice"; "Alice Example" -> "Alice"."""
    name = str(name or "").strip()
    if "," in name:
        name = name.split(",", 1)[1].strip()
    return (name.split() or [""])[0]


def read_reply(output):
    if not isinstance(output, dict):
        raise ValueError("Return a JSON object with reply and assessment.")
    reply, assessment = output.get("reply"), output.get("assessment")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("The reply is empty.")
    if not isinstance(assessment, str):
        raise ValueError("Give the assessment as one sentence.")
    if len(reply) > MAX_REPLY - 100:
        raise ValueError(f"The reply is longer than {MAX_REPLY - 100} characters.")
    return {"reply": reply.strip(), "assessment": " ".join(assessment.split())[:1000]}


class RegradeDrafts:
    def __init__(self, frq, regrades, certification=None):
        self.frq, self.regrades, self.certification = frq, regrades, certification
        self.rosters, self.path = frq.rosters, frq.path

    # -- The dispatcher's side (grading/cloud.py) -----------------------------------------------------------------------------
    @staticmethod
    def claim_runs(db, now, limit):
        units = []
        if limit <= 0:
            return units
        for row in db.execute("""SELECT * FROM frq_regrade_drafts WHERE status='pending' AND COALESCE(not_before,'')<=? ORDER BY updated_at,rowid LIMIT ?""",
                              (now, limit)).fetchall():
            db.execute("UPDATE frq_regrade_drafts SET status='running', attempts=attempts+1, updated_at=? WHERE id=?", (now, row["id"]))
            units.append(("regrade_draft", dict(row), None))
        return units

    @staticmethod
    def recover_runs(db):
        db.execute("UPDATE frq_regrade_drafts SET status='pending', updated_at=? WHERE status='running'", (timestamp(),))

    @staticmethod
    def fail_run(db, draft_id, error):
        db.execute("UPDATE frq_regrade_drafts SET status='failed', error=?, updated_at=? WHERE id=? AND status='running'", (str(error)[:1000], timestamp(), draft_id))

    def run(self, task):
        from .cloud import RetryLater, later
        from .providers.base import ProviderError
        profile, given = json.loads(task["profile_json"]), json.loads(task["input_json"])
        status, found, error, after = "complete", None, "", None
        try:
            images = self._images(given["result_id"])
            found = self._ask(task, profile["endpoint"], profile["settings"], {**given["payload"], "pictures": len(images)}, images)
        except RetryLater as waiting:
            status, after = "pending", later(waiting.seconds)
        except (ProviderError, ValueError, TypeError, KeyError, NotFound, OSError) as failure:
            status, error = "failed", f"The model could not draft a reply: {failure}"[:1000]
        reply = None
        if found:
            greeting = f"Hi {given['first_name']},\n\n" if given.get("first_name") else ""
            reply = (greeting + found["reply"])[:MAX_REPLY]
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE frq_regrade_drafts SET status=?, reply=?, assessment=?, error=?, not_before=?, updated_at=? WHERE id=? AND status='running'""",
                       (status, reply, (found or {}).get("assessment"), error, after, timestamp(), task["id"]))
            db.commit()

    def _ask(self, task, endpoint, settings, payload, images):
        refused = None
        for attempt in (0, 1):
            body = payload if not attempt else {**payload, "refused": f"Your previous answer was refused: {refused}"}
            reply = self.frq.cloud_call({"regrade_draft_id": task["id"], "step": STEP, "task_attempt": attempt}, "rubric_reviewer", endpoint,
                                        {"prompt": PROMPT, "payload": body, "images": images, **({"reask": True} if attempt else {})}, settings)
            try:
                return read_reply(reply["output"])
            except (ValueError, TypeError, KeyError, AttributeError) as failure:
                refused = failure
        raise ValueError(str(refused))

    def _images(self, result_id):
        """The crops the grade was given on, as the review desk shows them (at most MAX_IMAGES, IMAGES bytes)."""
        with connect(self.path) as db:
            row = db.execute("SELECT t.* FROM frq_results x JOIN frq_tasks t ON t.id=x.task_id WHERE x.id=?", (result_id,)).fetchone()
            if not row:
                return []
            crops = self.frq.response_crops(db, dict(row))["crops"]
        pictures, size = [], 0
        for crop in crops[:MAX_IMAGES]:
            picture = storage.read(self.frq.inbox, crop["images"].get("original") or crop["images"]["cloud"])
            if size + len(picture) > IMAGES:
                break
            pictures.append(picture)
            size += len(picture)
        return pictures

    # -- The page's side ------------------------------------------------------------------------------------------------------
    def _model(self):
        settings = self.frq.settings()["settings"]
        endpoint = config.rubric_reviewer(settings)
        if not endpoint:
            raise ValueError("Choose a Rubric drafting and review model in Compute settings first: it drafts the reply.")
        if endpoint["modality"] != "vision":
            raise ValueError(f"{endpoint['name']} cannot read images, and the reply is drafted from the student's work. Choose a Rubric drafting and review model that reads images.")
        return {**endpoint, "max_tokens": max(endpoint.get("max_tokens") or 0, TOKENS)}, settings

    def inputs(self, db, aid, message_id, name=""):
        """(request, placed, input) of a draft for the grade as it is now: what the model reads, without anything identifying."""
        request, placed = self.regrades._request(db, aid, message_id)
        rid = placed.get("response_id")
        if not rid:
            raise ValueError("This regrade request is not linked to a response, so there is nothing to draft from: answer it on Gradescope.")
        _, decisions, _ = self.frq.final_decisions(db, aid, response_id=rid)
        if not decisions:
            raise ValueError("This response has no final grade, so there is nothing to explain yet.")
        decision = decisions[0]
        question, final = decision["question"], decision["final"]
        score = final.get("score") or {}
        content = question["content"]
        rules = (question.get("rubric") or {}).get("rules") or {}
        selected = set(score.get("selected") or [a.get("rubric_item_id") for a in score.get("applications") or [] if a.get("applied")])
        result_id = decision["row"]["result_id"]
        transcript = db.execute("SELECT transcript_json FROM frq_results WHERE id=?", (result_id,)).fetchone()
        earlier = self.earlier(db, aid, rid)
        payload = {"request": strip_names(request["text"], name),
                   "question": {"title": content.get("title") or "", "printed_number": content.get("printed_number") or "", "max_points": content.get("max_points"),
                                "problem": content.get("problem") or "", "answer_key": content.get("expected_answer") or "",
                                "solution": content.get("instructor_solution") or content.get("coordinator_solution") or content.get("worked_solution") or ""},
                   "rubric_lines": [{"points": item.get("points"), "description": item.get("description") or "", "applied": item.get("id") in selected}
                                    for item in rules.get("items") or []],
                   "adjustments": [{"points": a.get("points"), "reason": a.get("reason") or ""} for a in score.get("adjustments") or []],
                   # GRADING "PART CAPS": a capped part's lines can add up to more than the grade lost, so the caps and what they gave back go too.
                   **({"part_caps": rubrics.part_caps_of(rules)} if rubrics.part_caps_of(rules) else {}),
                   "grade": {"score": progress.amount(score.get("score")), "of": progress.amount(score.get("max_score")),
                             **({"returned_by_part_caps": progress.amount(score["part_caps"])} if score.get("part_caps") else {})},
                   "earlier_score": progress.amount(earlier) if earlier is not None and progress.amount(earlier) != progress.amount(score.get("score")) else None,
                   "reading": json.loads(transcript[0]) if transcript and transcript[0] else None}
        kind = "change" if payload["earlier_score"] is not None else "keep"
        given = {"rule": RULE, "kind": kind, "result_id": result_id, "first_name": first_name(name), "payload": payload}
        return request, placed, given

    @staticmethod
    def earlier(db, aid, rid):
        """The score the latest certification recorded for this response, or None."""
        latest = db.execute("SELECT id FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (aid,)).fetchone()
        if not latest:
            return None
        row = db.execute("""SELECT s.parts_json FROM frq_certification_students s JOIN frq_responses r ON r.section_id=s.section_id AND r.enrollment_id=s.enrollment_id
            WHERE s.certification_id=? AND r.id=?""", (latest[0], rid)).fetchone()
        if not row:
            return None
        for entry in ((json.loads(row[0]).get("frq") or {}).get("questions") or {}).values():
            if entry.get("response_id") == rid:
                return entry.get("score")
        return None

    def _name(self, aid, rid):
        if self.certification is None or not rid:
            return ""
        try:
            return self.certification.response_student(aid, rid)["name"]
        except NotFound:
            return ""

    def request(self, aid, message_id, actor="workspace"):
        """Asks for a draft of the reply for the grade as it is now, unless one is already waiting, running or written for it."""
        with connect(self.path) as db:
            _, placed = self.regrades._request(db, aid, message_id)
        name = self._name(aid, placed.get("response_id"))
        with self.rosters.lock(), connect(self.path) as db:
            _, placed, given = self.inputs(db, aid, message_id, name)
            input_sha = digest({k: v for k, v in given.items() if k != "first_name"})
            same = db.execute("""SELECT id FROM frq_regrade_drafts WHERE message_id=? AND input_sha256=? AND status!='failed' ORDER BY rowid DESC LIMIT 1""",
                              (message_id, input_sha)).fetchone()
            if same:
                return self.latest(db, aid, message_id)
        endpoint, settings = self._model()
        now = timestamp()
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            # REGRADES "DRAFT": two panels may both pass the read above while the model settings are being read. Recheck under
            # the insertion lock so one request/grade queues only one billable draft.
            same = db.execute("""SELECT id FROM frq_regrade_drafts WHERE message_id=? AND input_sha256=? AND status!='failed' ORDER BY rowid DESC LIMIT 1""",
                              (message_id, input_sha)).fetchone()
            if same:
                return self.latest(db, aid, message_id)
            db.execute("""INSERT INTO frq_regrade_drafts(id,assignment_id,response_id,message_id,kind,input_sha256,input_json,profile_json,status,actor,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?)""",
                       (uid(), aid, placed["response_id"], message_id, given["kind"], input_sha, json.dumps(given, sort_keys=True, default=str),
                        json.dumps({"endpoint": endpoint, "settings": settings}, sort_keys=True, default=str), actor, now, now))
            db.commit()
            found = self.latest(db, aid, message_id)
        wake = getattr(getattr(self.frq, "inbox", None), "wake", None)
        if wake is not None:
            wake.set()
        return found

    @staticmethod
    def latest(db, aid, message_id):
        row = db.execute("SELECT * FROM frq_regrade_drafts WHERE assignment_id=? AND message_id=? ORDER BY rowid DESC LIMIT 1", (aid, message_id)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "kind": row["kind"], "status": row["status"], "reply": row["reply"], "assessment": row["assessment"], "error": row["error"],
                "input_sha256": row["input_sha256"], "created_at": row["created_at"], "updated_at": row["updated_at"]}
