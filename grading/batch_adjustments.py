"""Batch adjustments on Certification and Export (requirement, 2026-09-27). GRADING.md "Certification
and Export".

A question belongs to one version (`frq_questions.exam_version`), so a batch is one question of the term grading plan in one section,
or in every section. The instructor names the line (the adjustment's reason) and its points, and every final grade there (the
export's own rule, `final_decisions`) receives ONE ordinary review of theirs: the transcript, rubric lines, earlier adjustments,
status and comment of the grade that counts now, plus this adjustment. The score is Faculty's own calculation (`scoring.score`: an
adjustment is added before the rubric's floor and ceiling, like every adjustment). The rubric never changes, so nothing is regraded
and no other section moves; Gradescope receives the line as the grade's point adjustment.

Three actions share one path, each a preview then an apply:
- `add` (a section or `all`, the line's points and wording): every final grade there gets the line; one that already carries it,
  or whose score the question's bounds hold, is left alone.
- `edit` (`batch_id`, new points and wording): the grades THAT batch changed which still carry its line get the line changed in
  place; `remove` (`batch_id`): they lose it. A grade edited by hand since, no longer final, or no longer carrying the line exactly
  is left alone. Each edit or removal is a batch of its own (`of` names the one it changed), so it can be changed again.

- `adjust_preview(aid, qid, data)` writes nothing: each final grade before and after, every other response in scope left alone
  with its reason, and the fingerprint an apply must name.
- `adjust(aid, qid, data)` needs that fingerprint, a request ID and `confirmation: "adjust"`. Under the roster lifecycle lock it plans
  again (409 when anything changed), then ONE `BEGIN IMMEDIATE` re-checks each response row and the question's rubric pin and
  writes every review with its feedback row and events, and one `grades_adjusted` receipt. The same request ID answers with that
  receipt; the same ID with other content is refused. Nothing is partial.
- `adjustment_batches(receipts, sections, carried)` (a staticmethod) lists the question's batches for the page, each with how many of
  its grades still carry its line.

A line in every section worth the whole question is full credit for everyone (`full_credit_lines`): Faculty gives it by itself to later
papers, in that question's later versions too, and to answers held for review (`reach_full_credit`; GRADING "A FULL-CREDIT LINE
REACHES LATER PAPERS"; requirement, 2026-11-04). Every other line reaches only the grades final when it was applied.

It is not a correction of the models: an automatic acceptance it replaces is not recorded as confirmed or overturned (an adjustment
for a misprint says nothing about how they graded), each review's feedback row names the batch, and the review burden counts a batch
as one action. A later regrade of the question replaces these grades like any other decision; the instructor applies the line again.
"""
import json
import re
from decimal import Decimal

from werkzeug.exceptions import Conflict, Forbidden, NotFound

from course_storage import timestamp
from rosters import connect

from .models import digest, encoded, number, text, uid
from .progress import amount, decision_rows
from .rubric_quality import quality_flags
from .scoring import score

RULE = "batch-adjustment-v1"
CONFIRM = "adjust"
EVENT = "grades_adjusted"
EVERY = "all"
ACTIONS = ("add", "edit", "remove")
HELD = "held"  # `kind` of an answer waiting for review, for a full-credit line alone (`_held_decision`)
AUTOMATIC_COMMENT = "Full credit for everyone on this question: Faculty added your line by itself; the answer was not looked at."
REASON_LIMIT = 200
REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
BATCH = re.compile(r"[A-Za-z0-9-]{1,64}")
# Why a response is not final, in the words the certification page's drawer uses.
NOT_FINAL = {"stale": "To grade again after a rubric change", "pending": "Not graded yet", "draft": "A draft review takes it out of final",
             "unreadable": "Marked unreadable: record a grade", "rejected": "Rejected: record a grade", "trial_accepted": "Trial-accepted: not final"}


def command(data, question):
    """The instructor's line: {section_id: a section ID or "all", points, reason}, bounded like any adjustment of this question."""
    if not isinstance(data, dict):
        raise ValueError("Supply a JSON object.")
    section = data.get("section_id")
    if not isinstance(section, str) or not section or len(section) > 64:
        raise ValueError("Choose a section, or every section.")
    maximum = question["content"]["max_points"]
    points = number(data.get("points"), "Adjustment points", -maximum, maximum)
    if points == 0:
        raise ValueError("An adjustment of 0 points changes no grade.")
    value = Decimal(str(points)).normalize()
    if value.as_tuple().exponent < -2:
        raise ValueError("Give the points with at most two decimals.")
    points = int(value) if value == value.to_integral() else float(value)  # 2 and 2.0 are one line (and one fingerprint)
    reason = text(data.get("reason"), "The line's wording", REASON_LIMIT, False).strip()
    return {"section_id": section, "points": points, "reason": reason}


def same_line(adjustment, line):
    return str(adjustment.get("reason") or "").strip() == line["reason"] and Decimal(str(adjustment.get("points"))) == Decimal(str(line["points"]))


def public_line(line):
    return {"points": amount(line["points"]), "reason": line["reason"]} if line else None


def applications_of(final, rules):
    """The grade's rubric decisions as the instructor's review keeps them (`save_review`'s shape for unchanged lines): every rubric
    line in order, its own reason and evidence retained, decided by the instructor."""
    prior = {a["rubric_item_id"]: a for a in final["applications"]}
    return [{"rubric_item_id": item["id"], "applied": bool(prior.get(item["id"], {}).get("applied")),
             "reason": prior.get(item["id"], {}).get("reason", "Instructor selection"), "evidence": prior.get(item["id"], {}).get("evidence", []),
             "actor": "workspace", "origin_error_roots": prior.get(item["id"], {}).get("origin_error_roots", []),
             "origin_error_id": prior.get(item["id"], {}).get("origin_error_id")} for item in rules["items"]]


def adjusted(earlier, old, new):
    """The grade's adjustments after the action, or (None, (code, reason)): `old` None adds `new`; otherwise `old`'s first match is
    replaced by `new` in place, or removed when `new` is None. Pure."""
    earlier = list(earlier or [])
    if old is None:
        if any(same_line(a, new) for a in earlier):
            return None, ("already", "Already has this adjustment")
        return [*earlier, {"points": new["points"], "reason": new["reason"]}], None
    at = next((i for i, a in enumerate(earlier) if same_line(a, old)), None)
    if at is None:
        return None, ("gone", "No longer carries this line (changed since)")
    rest = earlier[:at] + earlier[at + 1:]
    if new is None:
        return rest, None
    if any(same_line(a, new) for a in rest):
        return None, ("already", "Already has this adjustment")
    return [*rest[:at], {"points": new["points"], "reason": new["reason"]}, *rest[at:]], None


def change(final, rules, maximum, line, old=None):
    """(new score, None) for one final grade after the action (add `line`; with `old`, replace it by `line`, or remove it when `line`
    is None), or (None, (code, reason)) when the grade is left alone. An added line that the bounds keep from moving the score is
    left alone; an edit or a removal always writes (the grade's own adjustment changes). Pure."""
    before = final["score"] or {}
    after_adjustments, why = adjusted(before.get("adjustments"), old, line)
    if why:
        return None, why
    try:
        after = score(rules, applications_of(final, rules), maximum, after_adjustments)
    except ValueError as error:
        return None, ("unscorable", f"Its grade cannot be recalculated: {error}")
    if old is None and Decimal(str(after["score"])) == Decimal(str(before.get("score"))):
        return None, ("bounded", f"The question's point bounds keep it at {amount(after['score'])}")
    return after, None


def batch_lines(db, aid):
    """{response_id: [line]}: every line a batch added or set (`add`, `edit`) on each response it changed, from the receipts."""
    found = {}
    for row in db.execute("SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind=? ORDER BY id", (aid, EVENT)):
        receipt = json.loads(row[0])
        if receipt.get("action", "add") == "remove":
            continue
        for entry in receipt["reviews"]:
            found.setdefault(entry["response_id"], []).append({"points": receipt["points"], "reason": receipt["reason"]})
    return found


def standing_lines(receipts):
    """[{batch_id, question_id, section_id, points, reason}]: the lines still in force, from the `grades_adjusted` receipts in order.
    Each add/edit chain (`of` names the batch an edit or a removal changed) stands as its last line, unless it ends in a removal.
    A line reaches only the grades final when it was applied, so a late paper graded afterwards lacks it: Faculty does not certify
    that paper by itself (GRADING "LATE PAPERS CERTIFY THEMSELVES"). Pure."""
    found = {}
    for r in receipts:
        action = r.get("action", "add")
        if action != "add":
            found.pop(r.get("of"), None)
        if action != "remove":
            found[r["batch_id"]] = {"batch_id": r["batch_id"], "question_id": r["question_id"], "section_id": r["section_id"],
                                    "points": r["points"], "reason": r["reason"], "created_at": r.get("created_at", "")}
    return list(found.values())


def full_credit_lines(standing, questions):
    """{question_id: line}: the full-credit line each question's papers get by themselves (GRADING "A FULL-CREDIT LINE REACHES LATER
    PAPERS"). A question's own standing line in every section worth at least the whole question counts. A version added since (a
    makeup's) has the line of its siblings when every version that existed at the first such line carries one, so a line on one
    version alone (that version's misprint) reaches no other. `questions`: {question_id, question_key, exam_version, created_at,
    max_points}. Pure."""
    worth = {q["question_id"]: Decimal(str(q["max_points"])) for q in questions}
    full = {line["question_id"]: line for line in standing
            if line["section_id"] == EVERY and line["question_id"] in worth and Decimal(str(line["points"])) >= worth[line["question_id"]]}
    found = dict(full)
    versions = {}
    for q in questions:
        if q["exam_version"] and q["question_key"].endswith(f"-{q['exam_version']}"):
            versions.setdefault(q["question_key"][:-len(q["exam_version"]) - 1], []).append(q)
    for group in versions.values():
        lines = [full[q["question_id"]] for q in group if q["question_id"] in full]
        if not lines:
            continue
        first = min(line["created_at"] for line in lines)
        if any(q["question_id"] not in full and q["created_at"] <= first for q in group):
            continue
        latest = max(lines, key=lambda line: line["created_at"])
        for q in group:
            if q["question_id"] not in full and Decimal(str(latest["points"])) >= worth[q["question_id"]]:
                found[q["question_id"]] = {**latest, "question_id": q["question_id"]}
    return found


def comment_text(adjustments, lines):
    """What a grade's Gradescope comment says (requirement, 2026-09-27): each of its adjustments that is a batch line of that response, in the grade's
    order, one row each ("+1.5 Misprint in part (c)"), or None. Adjustments typed at the review desk and a manual grade's reason are
    never sent (they can be the instructor's notes to themself)."""
    rows = []
    for a in adjustments or []:
        if any(same_line(a, line) for line in lines or []):
            value = Decimal(str(a.get("points")))
            rows.append(f"{'-' if value < 0 else '+'}{amount(abs(value))} {str(a.get('reason') or '').strip()}")
    return "\n".join(rows) or None


def grade_adjustments(entry, found):
    """[{points, reason}] of a decided response (`progress.decision_rows` entry, `CertificationService._projection` row), or []."""
    if not entry or not entry.get("kind"):
        return []
    raw = found.get("review_score") if entry["kind"] == "review" else found.get("result_score")
    return [{"points": a.get("points"), "reason": a.get("reason") or ""} for a in (json.loads(raw or "null") or {}).get("adjustments") or []]


class BatchAdjustmentMixin:
    """Mixed into `CertificationService` (self.frq, self.rosters, self.path, self._coverage)."""

    # ----- receipts ----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _adjustment_receipts(db, aid, qid=None):
        rows = db.execute("SELECT detail_json,created_at FROM frq_events WHERE assignment_id=? AND kind=? ORDER BY id", (aid, EVENT)).fetchall()
        found = [{**json.loads(r[0]), "created_at": r[1]} for r in rows]
        return [r for r in found if qid is None or r["question_id"] == qid]

    def _adjustment_receipt(self, db, aid, request_id):
        return next((r for r in self._adjustment_receipts(db, aid) if r.get("request_id") == request_id), None)

    @staticmethod
    def adjustment_batches(receipts, sections, carried):
        """The question's batches for the page, oldest first: what each did, where, to how many grades, and how many of its grades
        still carry its line (`carried(response_ids, line)`; an edit's or an addition's own line, nothing for a removal)."""
        batches = []
        for r in receipts:
            action = r.get("action", "add")
            line = {"points": r["points"], "reason": r["reason"]}
            responses = [entry["response_id"] for entry in r["reviews"]]
            batches.append({"batch_id": r["batch_id"], "action": action, "of": r.get("of"), "section_id": r["section_id"],
                            "section": "" if r["section_id"] == EVERY else sections.get(r["section_id"], ""), **public_line(line),
                            "old": public_line(r.get("old")), "created_at": r["created_at"], "changed": len(responses),
                            "carrying": 0 if action == "remove" else carried(responses, line)})
        return batches

    # ----- the request -------------------------------------------------------------------------------------------------------
    def _adjustment_request(self, db, aid, qid, question, data):
        """{action, section_id, line, old, of, scope}: what the instructor asked, checked. `scope` is the batch's responses for an edit
        or a removal (None: every response of the section)."""
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        action = data.get("action", "add")
        if action not in ACTIONS:
            raise ValueError("Add, edit or remove a line.")
        if action == "add":
            line = command({k: data.get(k) for k in ("section_id", "points", "reason")}, question)
            return {"action": "add", "section_id": line["section_id"], "line": {k: line[k] for k in ("points", "reason")}, "old": None, "of": None, "scope": None}
        batch_id = data.get("batch_id")
        if not isinstance(batch_id, str) or not BATCH.fullmatch(batch_id):
            raise ValueError("Name the batch to change.")
        batch = next((r for r in self._adjustment_receipts(db, aid, qid) if r["batch_id"] == batch_id), None)
        if not batch:
            raise NotFound("This question has no such batch.")
        if batch.get("action", "add") == "remove":
            raise ValueError("A removal has no line of its own to change.")
        old = {"points": batch["points"], "reason": batch["reason"]}
        line = None
        if action == "edit":
            found = command({"section_id": batch["section_id"], "points": data.get("points"), "reason": data.get("reason")}, question)
            line = {k: found[k] for k in ("points", "reason")}
            if same_line(line, old):
                raise ValueError("Change the line's wording or its points.")
        return {"action": action, "section_id": batch["section_id"], "line": line, "old": old, "of": batch_id,
                "scope": {entry["response_id"] for entry in batch["reviews"]}}

    @staticmethod
    def _request_digest(qid, request):
        if request["action"] == "add":  # unchanged since the first additions
            return digest({"rule": RULE, "question_id": qid, "section_id": request["section_id"], **request["line"]})
        return digest({"rule": RULE, "question_id": qid, **{k: request[k] for k in ("action", "of", "section_id", "line", "old")}})

    # ----- the plan ----------------------------------------------------------------------------------------------------------
    def _adjustment_plan(self, db, aid, qid, question, request):
        """Everything a batch decides on, from catalog reads plus the verified roster payload (the caller holds the lifecycle lock and
        no transaction). `changes` keep their decision (`_decision`, `_score`) for the write; they never reach the page."""
        frq = self.frq
        plan = frq.plan(db, aid)
        if not question["rubric"]:
            raise Conflict("This question has no rubric yet.")
        event = frq.exams._event(db, plan["event_id"])
        participants = frq.exams._participants(db, event)
        frq.exams._collection(db, event, participants)
        sections = {p["section_id"]: p["section_number"] for p in participants}
        section = None if request["section_id"] == EVERY else request["section_id"]
        if section and section not in sections:
            raise ValueError("Choose a section of this exam.")
        people = {(p["section_id"], p["enrollment_id"]): p for p in participants}
        scope = request["scope"]
        responses = [r for r in frq._responses(db, plan, event, participants, qid)
                     if (not section or r["section_id"] == section) and (scope is None or r["id"] in scope)]
        _, decisions, skipped = frq.final_decisions(db, aid, question_id=qid, section_id=section)
        found = {d["row"]["id"]: d for d in decisions}
        held = {s["response_id"]: s["reason"] for s in skipped}
        coverage = self._coverage(db, aid, responses, decision_rows(db, aid, responses))
        rules, maximum = question["rubric"]["rules"], question["content"]["max_points"]
        changes, left = [], []
        for r in responses:
            person = people.get((r["section_id"], r["enrollment_id"])) or {}
            who = {"response_id": r["id"], "row": f"{r['section_id']}:{r['enrollment_id']}", "name": person.get("name", ""),
                   "section": person.get("section_number", sections.get(r["section_id"], ""))}
            decision = found.get(r["id"]) or (self._held_decision(db, r) if request.get("automatic") else None)
            if not decision:
                left.append({**who, "code": "not_final", "state": r["state"], "score": None,
                             "reason": held.get(r["id"]) or NOT_FINAL.get(r["state"], "Waiting for your review")})
                continue
            final = decision["final"]
            if request.get("automatic") and final["status"] == "manual":
                left.append({**who, "code": "manual", "state": r["state"], "score": amount((final["score"] or {}).get("score")), "reason": "A grade you typed stays yours"})
                continue
            after, why = change(final, rules, maximum, request["line"], request["old"])
            if why and why[0] == "bounded" and decision["kind"] == HELD:
                after, why = score(rules, applications_of(final, rules), maximum, [*(final["score"].get("adjustments") or []), request["line"]]), None  # the line settles its hold
            if why:
                left.append({**who, "code": why[0], "state": r["state"], "score": amount((final["score"] or {}).get("score")), "reason": why[1]})
                continue
            changes.append({**who, "kind": decision["kind"], "decision_id": final["id"], "result_id": decision["row"]["result_id"],
                            "version": r["version"], "current_review_id": r["current_review_id"], "current_result_id": r["current_result_id"],
                            "before": amount(final["score"]["score"]), "after": amount(after["score"]),
                            "later_proposal": decision["kind"] == "review" and r["current_result_id"] != decision["row"]["result_id"],
                            "gradescope": coverage.get(r["id"], "none"), "_decision": decision, "_score": after})
        by_name = lambda item: (str(item["name"]).casefold(), str(item["section"]))
        changes.sort(key=by_name)
        left.sort(key=by_name)
        fingerprint = digest({"rule": RULE, "assignment_id": aid, "question_id": qid, "rubric_id": question["rubric_id"],
                              "question_version": question["version"], **{k: request[k] for k in ("action", "of", "section_id", "line", "old")},
                              "changes": [[c["response_id"], c["version"], c["current_review_id"], c["current_result_id"], c["kind"], c["decision_id"],
                                           c["before"], c["after"]] for c in changes],
                              "left": [[item["response_id"], item["code"], item["state"]] for item in left]})
        return {"plan": plan, "sections": sections, "changes": changes, "left": left, "fingerprint": fingerprint}

    def _held_decision(self, db, row):
        """An answer waiting for review as a decision a full-credit line can be written on (`kind` HELD), or None: its current result
        completed under the rubric that stands and no review of the instructor's is on it. Reads the catalog only."""
        if not row["current_result_id"] or row["current_review_id"]:
            return None
        result = db.execute("SELECT * FROM frq_results WHERE id=?", (row["current_result_id"],)).fetchone()
        task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (result["task_id"],)).fetchone())
        try:
            self.frq.validate_task(db, task)
        except (ValueError, Conflict, Forbidden):
            return None
        proposal = self.frq.decode_result(result)
        if task["status"] != "complete" or not proposal.get("score"):
            return None
        final = {"id": result["id"], "status": "automatic", "transcript": proposal["transcript"], "applications": proposal["applications"], "score": proposal["score"]}
        return {"kind": HELD, "row": {"id": row["id"], "result_id": result["id"]}, "task": task, "final": final}

    @staticmethod
    def _adjustment_view(question, request, found):
        """What the page shows of a plan: names and sections (the page names students), never a UIN or a decision's contents."""
        public = lambda c: {k: v for k, v in c.items() if not k.startswith("_")}
        codes = {}
        for item in found["left"]:
            codes[item["code"]] = codes.get(item["code"], 0) + 1
        shown = request["line"] or request["old"]
        return {"rule": RULE, "question": {"question_id": question["question_id"], "title": question["content"].get("title") or question["question_key"],
                                           "version": question["exam_version"] or "", "max": amount(question["content"]["max_points"])},
                "action": request["action"], "batch_id": request["of"], "old": public_line(request["old"]),
                "section_id": request["section_id"], "section": "" if request["section_id"] == EVERY else found["sections"].get(request["section_id"], ""),
                **public_line(shown), "changes": [public(c) for c in found["changes"]], "left": found["left"],
                "counts": {"change": len(found["changes"]), "left": len(found["left"]), **codes,
                           "on_gradescope": sum(1 for c in found["changes"] if c["gradescope"] in ("sent", "changed"))},
                "fingerprint": found["fingerprint"]}

    def adjust_preview(self, aid, qid, data):
        with self.rosters.lock(), connect(self.path) as db:
            question = self.frq.question(db, aid, qid)
            request = self._adjustment_request(db, aid, qid, question, data)
            found = self._adjustment_plan(db, aid, qid, question, request)
        return self._adjustment_view(question, request, found)

    @staticmethod
    def _adjustment_result(receipt, replay):
        return {"replay": replay, "batch_id": receipt["batch_id"], "action": receipt.get("action", "add"), "of": receipt.get("of"),
                "question_id": receipt["question_id"], "section_id": receipt["section_id"], "points": amount(receipt["points"]), "reason": receipt["reason"],
                "old": public_line(receipt.get("old")), "changed": len(receipt["reviews"]),
                "reviews": [{k: r[k] for k in ("response_id", "review_id", "before", "after")} for r in receipt["reviews"]]}

    # ----- the apply ---------------------------------------------------------------------------------------------------------
    def adjust(self, aid, qid, data):
        if not isinstance(data, dict):
            raise ValueError("Supply a JSON object.")
        request_id, fingerprint = data.get("request_id"), data.get("fingerprint")
        if not isinstance(request_id, str) or not REQUEST.fullmatch(request_id):
            raise ValueError("Send a request ID.")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("Preview the adjustment first.")
        if data.get("confirmation") != CONFIRM:
            raise ValueError('Confirm the adjustment ("adjust").')
        frq = self.frq
        with self.rosters.lock(), connect(self.path) as db:
            question = frq.question(db, aid, qid)
            receipt = self._adjustment_receipt(db, aid, request_id)
            request = self._adjustment_request(db, aid, qid, question, data) if not receipt else None
            if receipt:
                # The same request answers with what it recorded (its batch may be changed since, so it is compared by its digest).
                try:
                    asked = self._request_digest(qid, self._adjustment_request(db, aid, qid, question, data))
                except ValueError:
                    asked = None
                if asked != receipt["request_sha256"]:
                    raise Conflict("This request ID was already used for another adjustment. Preview again.")
                return self._adjustment_result(receipt, True)
            found = self._adjustment_plan(db, aid, qid, question, request)
            if found["fingerprint"] != fingerprint:
                raise Conflict("Grades of this question changed since the preview. Preview again.")
            if not found["changes"]:
                raise ValueError("No final grade would change.")
            return self._adjustment_result(self._write_adjustment(db, aid, qid, question, request, found, request_id), False)

    def _write_adjustment(self, db, aid, qid, question, request, found, request_id):
        """The batch's reviews and its receipt, in ONE `BEGIN IMMEDIATE`. The caller holds the lifecycle lock and read `found` under it."""
        frq, automatic = self.frq, bool(request.get("automatic"))
        with db:
            db.execute("BEGIN IMMEDIATE")
            # The plan was read under the lifecycle lock; the catalog high-water marks it rests on are checked once more here.
            pin = db.execute("SELECT rubric_id,version FROM frq_assignment_questions WHERE assignment_id=? AND question_id=?", (aid, qid)).fetchone()
            if not pin or (pin["rubric_id"], pin["version"]) != (question["rubric_id"], question["version"]):
                raise Conflict("This question's rubric changed since the preview. Preview again.")
            for c in found["changes"]:
                row = db.execute("SELECT version,current_review_id,current_result_id FROM frq_responses WHERE id=?", (c["response_id"],)).fetchone()
                if not row or tuple(row) != (c["version"], c["current_review_id"], c["current_result_id"]):
                    raise Conflict("A grade of this question changed since the preview. Preview again.")
            batch_id, status = uid(), frq.rubric_quality_status(db, aid)
            reviews = [self._adjusted_review(db, aid, qid, question, c, request, status, batch_id) for c in found["changes"]]
            shown = request["line"] or request["old"]
            receipt = {"batch_id": batch_id, "action": request["action"], "of": request["of"], "request_id": request_id,
                       "request_sha256": self._request_digest(qid, request), "rule": RULE, "fingerprint": found["fingerprint"], "question_id": qid,
                       "rubric_id": question["rubric_id"], "section_id": request["section_id"], **shown, "old": request["old"],
                       "reviews": reviews, "left": len(found["left"]), **({"automatic": True} if automatic else {})}
            frq.event(db, aid, EVENT, receipt, "automatic" if automatic else "workspace")
        return receipt

    # ----- a full-credit line reaches later papers (GRADING "A FULL-CREDIT LINE REACHES LATER PAPERS") -------------------------------
    def reach_full_credit(self, aid):
        """Give the plan's full-credit lines to every answer lacking them; the number of grades changed. The caller knows no batch or
        flag review of the plan runs. A grade changed meanwhile waits for the next look (`Conflict`)."""
        frq, changed = self.frq, 0
        with self.rosters.lock(), connect(self.path) as db:
            standing = standing_lines(self._adjustment_receipts(db, aid))
            if not standing:
                return 0
            questions = [{"question_id": row["question_id"], "question_key": row["question_key"], "exam_version": row["exam_version"], "created_at": row["created_at"],
                          "max_points": json.loads(row["content_json"])["max_points"]} for row in db.execute(
                """SELECT aq.question_id,q.question_key,q.exam_version,q.created_at,r.content_json FROM frq_assignment_questions aq
                   JOIN frq_questions q ON q.id=aq.question_id JOIN frq_question_revisions r ON r.id=aq.question_revision_id WHERE aq.assignment_id=?""", (aid,))]
            for qid, line in full_credit_lines(standing, questions).items():
                question = frq.question(db, aid, qid)
                if not question["rubric"]:
                    continue
                request = {"action": "add", "section_id": EVERY, "line": {"points": line["points"], "reason": line["reason"]}, "old": None, "of": None, "scope": None, "automatic": True}
                found = self._adjustment_plan(db, aid, qid, question, request)
                if found["changes"]:
                    try:
                        receipt = self._write_adjustment(db, aid, qid, question, request, found, f"full-credit:{found['fingerprint'][:48]}")
                    except Conflict:
                        continue
                    changed += len(receipt["reviews"])
        return changed

    def _adjusted_review(self, db, aid, qid, question, planned, request, status, batch_id):
        """One ordinary review of the instructor's, written exactly as `save_review` writes one (review, response pointer, rubric
        finalization note, feedback row, `response_reviewed` event), on the result the counted grade was made on."""
        frq, decision = self.frq, planned["_decision"]
        final, task, rid = decision["final"], decision["task"], planned["response_id"]
        result = db.execute("SELECT * FROM frq_results WHERE id=?", (planned["result_id"],)).fetchone()
        rules = question["rubric"]["rules"]
        applications = applications_of(final, rules)
        after_adjustments, why = adjusted(final["score"].get("adjustments"), request["old"], request["line"])
        numeric = score(rules, applications, question["content"]["max_points"], after_adjustments) if not why else None
        if not numeric or amount(numeric["score"]) != planned["after"]:
            raise Conflict("A grade of this question changed since the preview. Preview again.")
        review = decision["kind"] == "review"
        state = final["status"] if review else "approved"
        automatic = bool(request.get("automatic"))
        comment = (final.get("comment") or "") if review else (AUTOMATIC_COMMENT if automatic else "")
        review_id = uid()
        db.execute("INSERT INTO frq_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (review_id, rid, result["id"], task["rubric_id"], task["source_sha256"],
                   encoded(final["transcript"]), encoded(applications), encoded(numeric), state, comment, "automatic" if automatic else "workspace", timestamp()))
        db.execute("UPDATE frq_responses SET current_review_id=?,version=version+1 WHERE id=?", (review_id, rid))
        quality = quality_flags(status, qid, task["rubric_id"], applications)
        if quality["enabled"]:
            frq.event(db, aid, "rubric_quality_manual_review", {"review_id": review_id, "quality": quality})
        if not review:
            accepted = frq.acceptance_of(db, result["id"])
            if accepted and accepted["blind"] and not accepted["outcome"]:
                # Its grade is now visible in the review: a later look is no blind judgment (as `response` records on a reveal).
                frq.event(db, aid, "blind_revealed", {"response_id": rid, "result_id": result["id"], "batch_adjustment": batch_id})
        wrong = db.execute("SELECT reasoning_wrong FROM frq_review_feedback WHERE review_id=?", (final["id"],)).fetchone() if review else None
        before = {a["rubric_item_id"] for a in json.loads(result["applications_json"]) if a["applied"]}
        chosen = {a["rubric_item_id"] for a in applications if a["applied"]}
        signals = json.loads(result["signals_json"])
        mark = {"batch_id": batch_id, "action": request["action"], "of": request["of"], "line": request["line"], "old": request["old"],
                **(request["line"] or request["old"])}
        db.execute("INSERT INTO frq_review_feedback VALUES(?,?,?,?,?,?,?)", (review_id, int(bool(wrong and wrong[0])), comment,
                   int(final["transcript"] != json.loads(result["transcript_json"])),
                   encoded({"added": sorted(chosen - before), "removed": sorted(before - chosen), "batch_adjustment": mark}),
                   encoded({"result_id": result["id"], "task_id": task["id"], "job_id": task["job_id"], "rubric_id": task["rubric_id"],
                            "question_revision_id": task["question_revision_id"], "scope_sha256": signals.get("scope_sha256"), "models": signals.get("models"),
                            "batch_adjustment": batch_id, "replaces": {"kind": decision["kind"], "id": final["id"]}}), timestamp()))
        frq.event(db, aid, "response_reviewed", {"response_id": rid, "review_id": review_id, "result_id": result["id"], "status": state, "batch_adjustment": batch_id},
                  "automatic" if automatic else "workspace")
        return {"response_id": rid, "review_id": review_id, "replaces": {"kind": decision["kind"], "id": final["id"]}, "before": planned["before"], "after": planned["after"]}
