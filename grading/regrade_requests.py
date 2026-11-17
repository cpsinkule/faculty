"""Regrade requests on the Review step (requirement, 2026-09-30; AGENTS.md "Regrade requests"): each Gradescope regrade request the
communications record captured, placed on the Review step of the term grading plan it concerns and beside the graded response, and
answered from Faculty (the instructor's choice: "Faculty posts my reply").

Finding a request's response. The capture keeps each request as a message (`comm_messages` channel `gradescope_regrade`, external
id `<course>:<question submission>:<request>:request`, its grading page's address in the revision's context, the student linked by
their SID). It is placed by Faculty's own records, never guessed: first the accepted grade Faculty sent for that student's question
(`frq_gradescope_grades` joined to its upload, same course, Gradescope assignment and question), else the plan and version the
section's Gradescope exam is linked to (`frq_gradescope_targets`) with the question Faculty made there (`frq_gradescope_created_questions`)
or, for a rubric imported from Gradescope, the ONE question of that version printing the number the Gradescope question's title
prints; then that student's response. Anything else stays unmatched, with the reason, and is answered on Gradescope.

A request is open until a reply is captured, Faculty's own reply is accepted, the request was captured already closed, or the
instructor marks it handled (`frq_regrade_marks`, append-only, reopened the same way).

Replying (the grading page's "Close & Respond", read on the real site 2026-09-30: one JSON PUT to the page's `regrade_request`
address plus the request's ID, which answers and closes it). The preview reads the page (writes nothing) and says whether the request
is still open, what Gradescope shows for the question and what Faculty's final grade is, with a fingerprint. The reply names that
fingerprint, a request ID and `confirmation: "reply"` (checked here), reads the page again (409 when anything changed), records the
reply BEFORE sending (`frq_regrade_replies`), sends it once and reads the page back: `accepted` only when the request shows exactly
that reply. An outcome that cannot be read is `uncertain` and is settled by reading (Check on Gradescope), never by sending again; the
same request ID returns what it did. Nothing here changes a grade: a changed grade goes to Gradescope through the ordinary send.
Faculty never touches Math 140 for Instructors (`Session.guard`) and never visits /logout.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from werkzeug.exceptions import Conflict, NotFound

from canonical_json import ascii_digest
from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect

from . import gradescope_web as gw
from . import progress

GRADE_PAGE = re.compile(r"^https://[^/]+/courses/(\d+)/questions/(\d+)/submissions/(\d+)/grade$|^http://127\.0\.0\.1:\d+/courses/(\d+)/questions/(\d+)/submissions/(\d+)/grade$")
MAX_REPLY = 5000


def _digest(value):
    return ascii_digest(value, default=str)


def _has(db, *tables):
    # Counted in SQL: reading every table's name into Python was 100 ms of the Assignments list (148 calls; PERFORMANCE, 2026-11-01).
    wanted = sorted(set(tables))
    return db.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ({','.join('?' * len(wanted))})",
                      wanted).fetchone()[0] == len(wanted)


def _address(url):
    """(course, question, question submission) of a grading page's address, or None."""
    match = GRADE_PAGE.match(str(url or ""))
    if not match:
        return None
    groups = [group for group in match.groups() if group is not None]
    return tuple(groups)


def captured(db):
    """Every captured regrade request with its reply (if captured), student and grading page: [{...}], oldest first."""
    if not _has(db, "comm_messages", "comm_revisions", "comm_links"):
        return []
    rows = db.execute("""SELECT m.id, m.external_id, r.sent_at, r.body_text, r.context_json, r.author_json FROM comm_messages m
        JOIN comm_revisions r ON r.message_id=m.id AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
        WHERE m.channel='gradescope_regrade' ORDER BY r.sent_at, m.id""").fetchall()
    replies = {row["external_id"][:-len(":reply")]: row for row in rows if row["external_id"].endswith(":reply")}
    found = []
    for row in rows:
        if not row["external_id"].endswith(":request"):
            continue
        key = row["external_id"][:-len(":request")]
        parts = key.split(":")
        context = json.loads(row["context_json"] or "{}")
        where = _address(context.get("url"))
        link = db.execute("SELECT enrollment_id, term_id FROM comm_links WHERE message_id=? AND role='author' ORDER BY enrollment_id LIMIT 1",
                          (row["id"],)).fetchone()
        if len(parts) != 3 or not where or not link:
            continue
        reply = replies.get(key)
        found.append({"id": row["id"], "key": key, "course_id": where[0], "gradescope_question_id": where[1], "question_submission_id": where[2],
                      "gradescope_assignment_id": str(context.get("assignment_id") or ""), "gradescope_request_id": parts[2],
                      "created_at": row["sent_at"], "text": row["body_text"], "question_title": str(context.get("question") or ""),
                      "assignment_title": str(context.get("assignment") or ""), "url": context.get("url"),
                      "captured_state": context.get("state") or "", "enrollment_id": link["enrollment_id"], "term_id": link["term_id"],
                      "reply": {"text": reply["body_text"], "at": reply["sent_at"],
                                "by": (json.loads(reply["author_json"] or "{}").get("name") or "")} if reply else None})
    return found


def _plan_questions(db, plan_id):
    rows = db.execute("""SELECT p.question_id, q.exam_version, r.content_json FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id
        JOIN frq_question_revisions r ON r.id=p.question_revision_id WHERE p.assignment_id=?""", (plan_id,)).fetchall()
    return [{"question_id": row["question_id"], "exam_version": row["exam_version"] or "", "content": json.loads(row["content_json"])} for row in rows]


def question_number(title):
    """The number of a Gradescope question from its title: "3: Question 3" is 3 (its outline index), "Question 3" is 3; None without one."""
    title = str(title or "")
    lead = re.match(r"\s*(\d+)(?:\.\d+)*\s*:", title)
    numbers = re.findall(r"\d+", title)
    return int(lead.group(1)) if lead else int(numbers[-1]) if numbers else None


def sheet_match(db, request):
    """A request on a bubble-sheet exam's version (REGRADES "BUBBLE SHEETS"; requirement, 2026-10-28), or None when the
    request's Gradescope assignment is not one: {kind: "mc", plan_id, exam_version, number, sheet_id, sheet_upload_id, section_id, how},
    or the same with {reason} when Faculty did not put this student's sheet in that version. Placed by Faculty's own records only:
    the exam Faculty linked (`frq_gradescope_sheet_targets`, each plan and section's latest link) and the sheet it uploaded for that
    student there (`frq_gradescope_sheet_uploads`, accepted); the question by its number, as MC CORRECTIONS finds it."""
    if not _has(db, "frq_gradescope_sheet_targets", "frq_gradescope_courses", "frq_gradescope_sheet_uploads"):
        return None
    targets = {}
    for row in db.execute("""SELECT t.assignment_id, t.section_id, t.versions_json FROM frq_gradescope_sheet_targets t
            JOIN frq_gradescope_courses c ON c.id=t.course_link_id WHERE c.course_id=? ORDER BY t.created_at, t.rowid""", (request["course_id"],)):
        versions = json.loads(row["versions_json"] or "{}")
        targets[(row["assignment_id"], row["section_id"])] = next(
            (letter for letter, gs_assignment in versions.items() if str(gs_assignment) == request["gradescope_assignment_id"]), None)
    found = {(plan, letter) for (plan, _), letter in targets.items() if letter is not None}
    if not found:
        return None
    if len({plan for plan, _ in found}) != 1:
        return {"kind": "mc", "reason": "Several of Faculty's assignments are linked to this Gradescope bubble-sheet exam."}
    plan_id, letter = sorted(found)[0]
    number = question_number(request["question_title"])
    placed = {"kind": "mc", "plan_id": plan_id, "exam_version": letter, "number": number}
    if number is None:
        return {**placed, "reason": "Gradescope's question title names no question number."}
    upload = db.execute("""SELECT id, sheet_id, section_id FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND enrollment_id=? AND course_id=?
        AND gradescope_assignment_id=? AND status='accepted' ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                        (plan_id, request["enrollment_id"], request["course_id"], request["gradescope_assignment_id"])).fetchone()
    if not upload:
        return {**placed, "reason": "Faculty did not put this student's bubble sheet in that version on Gradescope."}
    return {**placed, "sheet_id": upload["sheet_id"], "sheet_upload_id": upload["id"], "section_id": upload["section_id"],
            "how": "the bubble sheet Faculty put on Gradescope"}


def placed_on(placed):
    """Whether a request is placed on something Faculty graded: a response, or (multiple choice) the student's bubble sheet."""
    return bool(placed.get("response_id") or placed.get("sheet_id"))


def match(db, request):
    """{plan_id, response_id, question_id, exam_version, how} for a captured request, or {reason} when Faculty's records cannot
    place it. Never a guess: Faculty's own grade receipt, else its linked exam and one question of that version. A request on a
    bubble-sheet exam is placed on the student's sheet (`sheet_match`, kind "mc")."""
    sheet = sheet_match(db, request)
    if sheet is not None:
        return sheet
    if _has(db, "frq_gradescope_grades", "frq_gradescope_uploads"):
        # PERFORMANCE (2026-11-01): the student's uploads first (CROSS JOIN), then their grades by `upload_id`; joined the other way
        # SQLite walked every accepted grade per request (117 ms of the Assignments list's 41 requests). Ties on created_at, which no
        # live request has, go to the grade recorded last.
        row = db.execute("""SELECT g.assignment_id, g.response_id FROM frq_gradescope_uploads u CROSS JOIN frq_gradescope_grades g ON u.id=g.upload_id
            WHERE u.course_id=? AND u.gradescope_assignment_id=? AND g.gradescope_question_id=? AND u.enrollment_id=? AND g.status='accepted'
            ORDER BY g.created_at DESC, g.rowid DESC LIMIT 1""", (request["course_id"], request["gradescope_assignment_id"], request["gradescope_question_id"],
                                                  request["enrollment_id"])).fetchone()
        if row:
            response = db.execute("SELECT question_id FROM frq_responses WHERE id=?", (row["response_id"],)).fetchone()
            question = db.execute("SELECT exam_version FROM frq_questions WHERE id=?", (response["question_id"],)).fetchone() if response else None
            if response:
                return {"plan_id": row["assignment_id"], "response_id": row["response_id"], "question_id": response["question_id"],
                        "exam_version": (question["exam_version"] if question else "") or "", "how": "Faculty's grade on Gradescope"}
    if not _has(db, "frq_gradescope_targets", "frq_gradescope_courses"):
        return {"reason": "Faculty has not linked this Gradescope assignment to an assignment of its own."}
    targets = {}
    for row in db.execute("""SELECT t.assignment_id, t.section_id, t.versions_json, t.created_at FROM frq_gradescope_targets t
            JOIN frq_gradescope_courses c ON c.id=t.course_link_id WHERE c.course_id=? ORDER BY t.created_at""", (request["course_id"],)):
        versions = json.loads(row["versions_json"] or "{}")
        # The latest link of each plan and section counts: the version whose Gradescope assignment this is, or None.
        targets[(row["assignment_id"], row["section_id"])] = next(
            (letter for letter, gs_assignment in versions.items() if str(gs_assignment) == request["gradescope_assignment_id"]), None)
    plans = {plan for (plan, _), letter in targets.items() if letter is not None}
    if len(plans) != 1:
        return {"reason": "Faculty has not linked this Gradescope assignment to an assignment of its own." if not plans
                else "Several of Faculty's assignments are linked to this Gradescope assignment."}
    plan_id = plans.pop()
    letter = next(letter for (plan, _), letter in targets.items() if plan == plan_id and letter is not None)
    question_id = None
    if _has(db, "frq_gradescope_created_questions"):
        row = db.execute("""SELECT question_id FROM frq_gradescope_created_questions WHERE course_id=? AND gradescope_assignment_id=?
            AND gradescope_question_id=? ORDER BY created_at DESC LIMIT 1""", (request["course_id"], request["gradescope_assignment_id"],
                                                                          request["gradescope_question_id"])).fetchone()
        question_id = row["question_id"] if row else None
    how = "the question Faculty made on Gradescope"
    if not question_id:
        numbers = re.findall(r"\d+", request["question_title"].split(":", 1)[-1])
        candidates = [q for q in _plan_questions(db, plan_id) if (q["exam_version"] or "") == (letter or "")
                      and str(q["content"].get("printed_number") or "").strip() in numbers[-1:]]
        if len(candidates) != 1:
            return {"plan_id": plan_id, "exam_version": letter or "", "reason": "No single question of this version prints the number of the Gradescope question."}
        question_id, how = candidates[0]["question_id"], "the question's printed number"
    row = db.execute("SELECT id FROM frq_responses WHERE assignment_id=? AND question_id=? AND enrollment_id=? ORDER BY id LIMIT 1",
                     (plan_id, question_id, request["enrollment_id"])).fetchone()
    if not row:
        return {"plan_id": plan_id, "question_id": question_id, "exam_version": letter or "", "reason": "Faculty has no response of this student to that question."}
    return {"plan_id": plan_id, "response_id": row["id"], "question_id": question_id, "exam_version": letter or "", "how": how}


class RegradeRequests:
    def __init__(self, frq, sender):
        self.frq, self.sender, self.rosters, self.path = frq, sender, frq.rosters, frq.path

    # -- Reading ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _replies(db, message_id):
        rows = db.execute("SELECT * FROM frq_regrade_replies WHERE message_id=? ORDER BY created_at, rowid", (message_id,)).fetchall()
        result = []
        for row in rows:
            outcome = db.execute("SELECT status, detail, created_at FROM frq_regrade_reply_outcomes WHERE reply_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                                 (row["id"],)).fetchone()
            result.append({"id": row["id"], "text": row["reply"], "at": row["created_at"], "status": outcome["status"] if outcome else "uncertain",
                           "detail": outcome["detail"] if outcome else "No answer was recorded; check on Gradescope."})
        return result

    @staticmethod
    def _state(db, plan_id, request):
        replies = RegradeRequests._replies(db, request["id"]) if _has(db, "frq_regrade_replies") else []
        mark = db.execute("SELECT action, note, created_at FROM frq_regrade_marks WHERE assignment_id=? AND message_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                          (plan_id, request["id"])).fetchone() if _has(db, "frq_regrade_marks") else None
        answered = request["reply"] is not None or any(reply["status"] == "accepted" for reply in replies) or request["captured_state"] == "closed"
        state = "answered" if answered else "handled" if mark and mark["action"] == "handled" else "open"
        return state, replies, dict(mark) if mark else None

    def listing(self, aid, response_id=None):
        """This plan's regrade requests, open first then newest (no student name: the Review step is anonymous), with counts."""
        with connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            questions = {q["question_id"]: q for q in _plan_questions(db, aid)}
            items = []
            for request in captured(db):
                if request["term_id"] != plan["term_id"]:
                    continue
                placed = match(db, request)
                if placed.get("plan_id") != aid or (response_id and placed.get("response_id") != response_id):
                    continue
                state, replies, mark = self._state(db, aid, request)
                question = questions.get(placed.get("question_id"))
                items.append({key: request[key] for key in ("id", "created_at", "text", "question_title", "assignment_title", "url", "reply")}
                             | {"state": state, "replies": replies, "mark": mark, "response_id": placed.get("response_id"),
                                "question_id": placed.get("question_id"), "exam_version": placed.get("exam_version", ""),
                                "question": (question["content"].get("title") if question else "") or request["question_title"],
                                "matched": placed_on(placed), "how": placed.get("how", ""), "reason": placed.get("reason", ""),
                                **({"kind": "mc", "number": placed.get("number")} if placed.get("kind") == "mc" else {}),
                                "can_reply": state == "open" and request["gradescope_request_id"].isdigit()})
        items.sort(key=lambda item: item["created_at"], reverse=True)
        items.sort(key=lambda item: item["state"] != "open")  # open first, each group newest first
        return {"assignment_id": aid, "requests": items, "counts": {state: sum(1 for item in items if item["state"] == state) for state in ("open", "answered", "handled")}}

    @staticmethod
    def open_counts(db):
        """{plan id: open requests} over current terms, and how many open requests Faculty could not place (Home's Needs you)."""
        counts, unplaced = {}, 0
        if not _has(db, "comm_messages", "frq_assignments", "exam_events", "course_terms"):
            return counts, unplaced
        current = {row[0] for row in db.execute("SELECT id FROM course_terms WHERE is_current=1 AND deleted_at IS NULL")}
        for request in captured(db):
            if request["term_id"] not in current:
                continue
            placed = match(db, request)
            plan_id = placed.get("plan_id")
            if not plan_id:
                if request["reply"] is None and request["captured_state"] != "closed":
                    unplaced += 1
                continue
            state, _, _ = RegradeRequests._state(db, plan_id, request)
            if state == "open":
                counts[plan_id] = counts.get(plan_id, 0) + 1
        return counts, unplaced

    def _request(self, db, aid, message_id):
        request = next((item for item in captured(db) if item["id"] == message_id), None)
        if not request:
            raise NotFound("That regrade request is not in the communications record.")
        placed = match(db, request)
        if placed.get("plan_id") != aid:
            raise NotFound("That regrade request is not about this assignment.")
        return request, placed

    # -- Replying -----------------------------------------------------------------------------------------------------------
    def _session(self):
        if not self.sender.settings.configured:
            raise ValueError("Gradescope's saved sign-in is not set up for this Faculty instance.")
        return self.sender.session_factory()

    @staticmethod
    def _find(page, request):
        return next((item for item in page["requests"] if str(item.get("id")) == request["gradescope_request_id"]), None)

    def _faculty_score(self, aid, response_id):
        if not response_id:
            return None
        with self.rosters.lock(), connect(self.path) as db:
            _, decisions, _ = self.frq.final_decisions(db, aid, response_id=response_id)
        for decision in decisions:
            score = (decision["final"].get("score") or {})
            return {"points": score.get("score"), "of": score.get("max_score"), "kind": decision["kind"]}
        return None

    def _look(self, session, aid, message_id, reply):
        """What a reply would do, read from Gradescope now: (request, placed, page, found, preview)."""
        with connect(self.path) as db:
            request, placed = self._request(db, aid, message_id)
        if not request["gradescope_request_id"].isdigit():
            raise ValueError("Gradescope did not name this request, so Faculty cannot answer it: reply on Gradescope.")
        page = session.regrade_page(request["course_id"], request["gradescope_question_id"], request["question_submission_id"])
        found = self._find(page, request)
        if found is None:
            raise Conflict("Gradescope's grading page no longer shows this regrade request.")
        preview = {"message_id": message_id, "open": bool(found["open"]), "gradescope_reply": gw.comment(found.get("staff_comment")) or "",
                   "gradescope_score": page["score"], "faculty": self._faculty_score(aid, placed.get("response_id")),
                   "question": page["question"].get("title") or request["question_title"], "url": request["url"],
                   "reply": reply, "reply_sha256": hashlib.sha256(reply.encode()).hexdigest()}
        preview["fingerprint"] = _digest({"message": message_id, "request": request["gradescope_request_id"], "open": preview["open"],
                                          "gradescope_reply": preview["gradescope_reply"], "reply": preview["reply_sha256"]})
        return request, placed, page, found, preview

    @staticmethod
    def _text(data):
        reply = (data or {}).get("reply")
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Write your reply to the student.")
        reply = reply.replace("\r\n", "\n").strip()
        if len(reply) > MAX_REPLY:
            raise ValueError(f"A reply is at most {MAX_REPLY} characters.")
        return reply

    def preview(self, aid, message_id, data):
        """What replying would do (writes nothing anywhere): whether the request is still open on Gradescope, the question's score
        there and Faculty's final grade, and the fingerprint the reply must name."""
        reply = self._text(data)
        session = self._session()
        try:
            *_, preview = self._look(session, aid, message_id, reply)
        finally:
            session.close()
        return preview

    def reply(self, aid, message_id, data, actor="workspace"):
        """Posts the reply to Gradescope (Close & Respond) once, recorded before it is sent and read back after."""
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        if data.get("confirmation") != "reply":
            raise ValueError('Confirm the reply with confirmation "reply".')
        reply = self._text(data)
        with connect(self.path) as db:
            done = db.execute("SELECT * FROM frq_regrade_replies WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone()
            if done:
                if done["message_id"] != message_id or done["reply"] != reply:
                    raise Conflict("This request ID already sent another reply. Close the window and open it again.")
                return {"reply": next(item for item in self._replies(db, message_id) if item["id"] == done["id"]), "repeated": True}
        expect = data.get("expect_score")
        if expect is not None and (not isinstance(expect, str) or progress.amount(expect) is None):
            raise ValueError("Say the score Gradescope must show as text.")
        session = self._session()
        try:
            return self._send(session, aid, message_id, reply, data.get("fingerprint"), request_id, actor, expect)
        finally:
            session.close()

    def _send(self, session, aid, message_id, reply, fingerprint, request_id, actor, expect=None):
        request, placed, page, found, preview = self._look(session, aid, message_id, reply)
        if preview["fingerprint"] != fingerprint:
            raise Conflict("The regrade request changed on Gradescope since the preview. Look at it again before replying.")
        if not preview["open"]:
            raise Conflict("This regrade request was already answered on Gradescope.")
        if expect is not None and progress.amount(page["score"]) != progress.amount(expect):
            # A regrade request's Apply (REGRADES): the reply closes the request, so it goes only once the student sees the new grade.
            raise Conflict(f"Gradescope shows {progress.amount(page['score']) or 'no score'} for the question, not {progress.amount(expect)}: "
                           "the reply waits until the grade is there.")
        reply_id = str(uuid.uuid4())
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO frq_regrade_replies(id,assignment_id,response_id,message_id,course_id,question_id,question_submission_id,
                          gradescope_request_id,reply,reply_sha256,fingerprint,request_id,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (reply_id, aid, placed.get("response_id"), message_id, request["course_id"], request["gradescope_question_id"],
                        request["question_submission_id"], request["gradescope_request_id"], reply, preview["reply_sha256"], preview["fingerprint"],
                        request_id, actor, timestamp()))
            db.commit()
        detail = ""
        try:
            session.reply_regrade(page, request["course_id"], request["gradescope_request_id"], reply)
        except gw.GradescopeError as error:  # refused, busy or unanswered: the page, read back, says what happened
            detail = str(error)
        status, detail = self._read_back(session, request, reply, detail)
        self._outcome(reply_id, status, detail)
        with connect(self.path) as db:
            return {"reply": next(item for item in self._replies(db, message_id) if item["id"] == reply_id), "repeated": False}

    def _read_back(self, session, request, reply, detail=""):
        """(status, detail) from the page as it is now: accepted only when the request shows exactly this reply."""
        try:
            page = session.regrade_page(request["course_id"], request["gradescope_question_id"], request["question_submission_id"])
        except gw.GradescopeError as error:
            return "uncertain", f"Gradescope could not be read back ({error}); check on Gradescope."
        found = self._find(page, request)
        if found is not None and gw.comment(found.get("staff_comment")) == gw.comment(reply):
            return "accepted", ""
        return ("failed", detail or "Gradescope does not show the reply.") if found is not None else ("uncertain", "Gradescope no longer shows the request.")

    def _outcome(self, reply_id, status, detail):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO frq_regrade_reply_outcomes VALUES (?,?,?,?,?)", (str(uuid.uuid4()), reply_id, status, str(detail or "")[:1000], timestamp()))
            db.commit()

    def check(self, aid, reply_id):
        """Settles an uncertain reply by reading Gradescope (never by sending again)."""
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_regrade_replies WHERE id=? AND assignment_id=?", (reply_id, aid)).fetchone()
            if not row:
                raise NotFound("That reply is not part of this assignment.")
            current = next(item for item in self._replies(db, row["message_id"]) if item["id"] == reply_id)
            if current["status"] != "uncertain":
                return {"reply": current}
            request, _ = self._request(db, aid, row["message_id"])
        session = self._session()
        try:
            status, detail = self._read_back(session, request, row["reply"])
        finally:
            session.close()
        self._outcome(reply_id, status, detail)
        with connect(self.path) as db:
            return {"reply": next(item for item in self._replies(db, row["message_id"]) if item["id"] == reply_id)}

    def mark(self, aid, message_id, data, actor="workspace"):
        """Marks a request handled (answered elsewhere, or needing no answer), or reopens it. A record; Gradescope is not contacted."""
        data = data if isinstance(data, dict) else {}
        request_id, action = data.get("request_id"), data.get("action")
        valid_request_id(request_id)
        if action not in ("handled", "reopened"):
            raise ValueError("Mark the request handled, or reopen it.")
        note = data.get("note") or ""
        if not isinstance(note, str) or len(note) > 1000:
            raise ValueError("The note is at most 1000 characters.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._request(db, aid, message_id)
            previous = db.execute("SELECT message_id,action,note FROM frq_regrade_marks WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone()
            if previous and (previous["message_id"], previous["action"], previous["note"]) != (message_id, action, note.strip()):
                raise Conflict("This request ID already records a different regrade mark.")
            if not previous:
                db.execute("INSERT INTO frq_regrade_marks VALUES (?,?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), aid, message_id, action, note.strip(), request_id, actor, timestamp()))
            db.commit()
        return self.listing(aid)
