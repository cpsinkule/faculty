"""Certify just this change (requirement, 2026-10-07).
GRADING.md "Certification and Export", REGRADES "APPLY".

A certified plan whose ONE response's grade changed since its latest certification gets a new certification that is the latest one
with that student's grade as it is now: the same checks and confirmations as the instructor gave them, every student row (the others
exactly as certified), `amends_id` naming the certification it amends, and the gradebook write of that ONE cell through the
gradebook's own plan (`certification_options` on that student's row alone: every other student reads "not in the certification" and
is left as it is). Refused only for this grade itself: not certified, not on Gradescope yet, this student's other grades changed too,
or the column the grades were certified into gone.

A REGRADE NEVER ASKS TO CERTIFY AGAIN (REGRADES, requirement, 2026-11-16). LIVE OR CARRIED MARKERS, as late papers certify themselves: when nothing
else changed since the latest certification (no other grade, row, block or confirmation) it keeps the live markers and the plan
reads current again; otherwise it still writes this one grade and CARRIES the latest's markers (the decisions marker live when this
grade is the only decision that changed), so the plan still reads "changed since certification" and everyone else's change keeps
waiting, exactly as it did, for the automatic certification or the instructor's Certify. `summary.amends.markers` says which, and
`others` names who else moved. A REGRADE CARRIES THE CHAIN (2026-11-16): when the latest is a carried certification Faculty made by
itself (`certification_automatic.carried_chain`), a confirmation is compared as that one compares it, its pending students counted
as they stood before; implied, it is no move, and a live change keeps the lists as they are now. A carried change leaves the lists
as they were, and the next automatic certification follows the chain through it.

- `check(aid, response_id)` (reads, under the lifecycle lock): whether it can be done, what it changes (`from`, `to`) and the
  fingerprint the certify names. The Apply preview's forecast; the Gradescope coverage and a running send are checked when it runs.
- `amend(aid, data)`: `request_id` (uuid), `fingerprint`, `confirmation: "certify-change"`, `response_id`, optional `replace` (the
  student's typed gradebook value is replaced) and `message_id` (the regrade request). The exact state again, one `BEGIN IMMEDIATE`
  that re-checks the catalog high-water marks, exactly as `certify`; idempotent by request ID, nothing partial.
- ONE BUBBLE SHEET (REGRADES "BUBBLE SHEETS", requirement, 2026-10-28): `check_sheet(aid, row)` and `amend` with `sheet_row` in place of
  `response_id` do the same for one student's multiple-choice score (their sheet's reading changed by a regrade decision): every
  other row as certified, this row's other parts too, and no free-response grade changed (the decisions marker). The
  multiple-choice checks that say where Gradescope differs (`gradescope_mc_corrected`, `gradescope_mc_differs`) never stop it: they
  are about Gradescope's copy, which the request's own Gradescope step corrects, never the grade. A Gradescope receipt is not
  asked for: the sheet is on Gradescope, which is how the request was placed.
- A DROP NEVER STOPS IT (GRADING "A DROP NEVER ASKS TO CERTIFY AGAIN", requirement, 2026-11-03, of Edda's Quiz 6 regrade stopped at
  Gradebook by five classmates' Q-drops): a student certified 0 with no work and dropped since is no row of the live grades and has
  left the live "no work receive 0" list, which is no change of anything certified. `dropped_no_work` names them; the confirmation
  still holds with them counted as they were, and their rows are kept in the new certification exactly as certified. A late paper's
  certification (`certification_automatic`) keeps its markers live the same way.
"""
import json
import uuid

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from rosters import connect

from . import certification_rules as rules
from . import participation
from . import progress
from .certification import Refused, _row
from .models import digest, encoded, finite_decimal, uid

CONFIRMATION = "certify-change"
BOOK_CHECKS = ("gradebook_column", "gradebook_kept", "gradebook_sections")
# Where others' copies stand on Gradescope (requirement, 2026-11-03; as `certification_automatic.CARRIED`): a send after certifying
# reopens nothing. This grade's own copy is asked for by itself, and any other grade or paper that moved is refused on its own.
COVERAGE_CHECKS = ("gradescope_written", "gradescope_sheets")
MC_CHECKS = ("gradescope_mc_corrected", "gradescope_mc_differs")  # where Gradescope's copy differs: never the grade (ONE BUBBLE SHEET)
CHANGED = {"rule": "the certification rule changed", "bubble_sheets": "the multiple-choice sheets or key changed",
           "students": "the students or their papers changed", "dr_date_passed": "a DR exam date passed", "makeup_ended": "a makeup absence changed",
           "new_work": "new work came in"}


def certified_content(db, entry):
    """(kind, score, selected, adjustments) of a certified question entry, read from the review or result its decision names, as
    `progress.decision_rows` reads a live one; None when it names neither. The participation line is never stored on a grade
    (GRADING "GRADED FOR PARTICIPATION"), so an entry certified at its worth whose stored grade is below it was certified with the
    line, and is read with it as a live one is (`participation.counted_entry`)."""
    kind, _, ident = str((entry or {}).get("decision") or "").partition(":")
    if kind == "review":
        row = db.execute("SELECT score_json FROM frq_reviews WHERE id=?", (ident,)).fetchone()
    elif kind == "automatic":
        row = db.execute("SELECT score_json FROM frq_results WHERE id=?", ((entry or {}).get("result_id"),)).fetchone()
    else:
        return None
    if not row:
        return None
    score, selected, adjustments = progress.score_content(row[0])
    worth = finite_decimal(entry.get("max"))
    if worth is not None and finite_decimal(entry.get("score")) == worth:
        counted = participation.counted_entry({"kind": kind, "score": score, "adjustments": list(adjustments)}, worth)
        score, adjustments = counted["score"], tuple(counted["adjustments"])
    return (kind, score, selected, adjustments)


def certified_parts(record):
    """A certified row's `parts_json` as a dict of its parts."""
    parts = (record or {}).get("parts_json")
    return json.loads(parts) if isinstance(parts, str) else dict(parts or {})


def part_content(name, part, without=None):
    """One part of a certified row as compared: the free-response part lists each question's [score, max, response_id], without the
    question `without` (and the points it added up to)."""
    part = dict(part or {})
    if name == "frq":
        part["questions"] = {qid: [q.get("score"), q.get("max"), q.get("response_id")] for qid, q in sorted((part.get("questions") or {}).items())
                             if qid != without}
        if without:
            part.pop("earned", None)
    return part


def row_content(record, without=None, part_left_out=None):
    """What a certified student row IS (critique C7: never which record holds a grade): status, points, version, DR date and every
    part with its questions' scores. `without`: a question left out (with the free-response and total points it adds up to);
    `part_left_out`: a whole part left out, with the total (ONE BUBBLE SHEET: "bubble")."""
    if record is None:
        return None
    shaped = {}
    for name, part in sorted(certified_parts(record).items()):
        if name == part_left_out:
            continue
        shaped[name] = part_content(name, part, without)
    earned = None if without or part_left_out else record["earned"]
    return json.dumps([record["status"], record["reason"] or "", earned, record["possible"], record["exam_version"] or "", record["dr_date"], shaped],
                      sort_keys=True, default=str)


def score_text(content):
    return content[1] if content else None


LEFT_OUT = {"makeup": "left out until their makeup", "dr_ahead": "left out until their DR exam",
            "duplicate_membership": "left out as a duplicate membership"}


def standing(record):
    """A student's certified row in a few words, as the confirmations name it."""
    if record is None:
        return "not in the assignment"
    status, reason = record["status"], record["reason"] or ""
    if status == "zero":
        return "0 with no work"
    if status == "left_out":
        return LEFT_OUT.get(reason, "left out")
    if status == "scored":
        return "graded with a part missing" if reason == "missing_part" else "graded"
    return status


def dropped_no_work(state):
    """A DROP NEVER STOPS IT: {row: certified record} of the students certified 0 with no work, dropped from the roster since, whom
    the live grades therefore leave out (`certification._evaluate`, "never a confirmed 0")."""
    live = {_row(r["section_id"], r["enrollment_id"]) for r in state["records"]}
    dropped = {_row(p["section_id"], p["enrollment_id"]) for p in state["participants"] if p.get("dropped")}
    return {row: record for row, record in state["certified_rows"].items() if record["status"] == "zero" and row in dropped and row not in live}


def as_they_were(gone):
    """`dropped_no_work`'s students as "no work receive 0" lists them: its confirmation names them for as long as their rows stand."""
    return [{"row": row, "status": record["status"], "reason": record["reason"]} for row, record in gone.items()]


def _confirmation_holds(check, entry, state, gone):
    """Whether what the instructor confirmed still stands: the same digest, or "no work receive 0" with the dropped counted as they were."""
    if entry.get("digest") == check.get("digest"):
        return True
    if check["id"] != "zero_no_work" or not gone:
        return False
    members = rules.student_members("zero_no_work", state["students"])[1] + rules.student_members("zero_no_work", as_they_were(gone))[1]
    return rules.members_digest(members) == entry.get("digest")


def who_moved(state, limit=3):
    """WHO MOVED (requirement, 2026-10-21): each student whose standing
    differs from the certification's, by name, e.g. "Rylee Smith (507) is now left out until their makeup, was 0 with no work"."""
    names = {s["row"]: s for s in state.get("students") or []}
    now = {_row(r["section_id"], r["enrollment_id"]): r for r in state["records"]}
    before, gone = state["certified_rows"], dropped_no_work(state)
    moved = []
    for key in sorted((set(now) | set(before)) - set(gone)):
        was, is_ = standing(before.get(key)), standing(now.get(key))
        if was != is_:
            who = names.get(key) or {}
            label = (who.get("name") or "A student") + (f" ({who['section']})" if who.get("section") else "")
            moved.append(f"{label} is now {is_}, was {was}")
    moved.sort()
    if len(moved) > limit:
        moved = moved[:limit] + [f"{len(moved) - limit} more"]
    return moved


class OneChange:
    def __init__(self, certification):
        self.service = certification
        self.frq, self.gradebook = certification.frq, certification.gradebook
        self.rosters, self.path = certification.rosters, certification.path

    # ----- the change ---------------------------------------------------------------------------------------------------------
    def _change(self, db, aid, latest, responses, decisions, rid):
        """This response's grade as certified and as it is now, and whether it is the ONLY grade that changed: the live grades with
        its entry put back to what the certification recorded digest to the certification's own decisions marker."""
        found = db.execute("SELECT section_id,enrollment_id,question_id FROM frq_responses WHERE id=? AND assignment_id=?", (rid, aid)).fetchone()
        if not found:
            raise NotFound("That response is not part of this assignment.")
        change = {"response_id": rid, "question_id": found["question_id"], "row": _row(found["section_id"], found["enrollment_id"]),
                  "section_id": found["section_id"], "enrollment_id": found["enrollment_id"], "reason": ""}
        live = decisions.get(rid) or {}
        change["to"] = live.get("score") if live.get("kind") else None
        if not latest:
            return {**change, "reason": "The grades are not certified yet: they go to the gradebook when you certify."}
        record = db.execute("SELECT parts_json FROM frq_certification_students WHERE certification_id=? AND section_id=? AND enrollment_id=?",
                            (latest["id"], found["section_id"], found["enrollment_id"])).fetchone()
        entry = ((json.loads(record["parts_json"]).get("frq") or {}).get("questions") or {}).get(found["question_id"]) if record else None
        certified = certified_content(db, entry) if entry and entry.get("response_id") == rid else None
        change["from"] = score_text(certified)
        if certified is None:
            return {**change, "reason": "This grade was not part of the certification: certify again to record it."}
        if not live.get("kind"):
            return {**change, "reason": "This response has no final grade now."}
        now = (live["kind"], live["score"], tuple(live["selected"]), tuple(live["adjustments"]))
        change["same"] = now[1:] == certified[1:]
        swapped = {**decisions, rid: {**live, "kind": certified[0], "score": certified[1], "selected": list(certified[2]), "adjustments": list(certified[3])}}
        change["only"] = digest(progress.decision_state(db, aid, responses, swapped)) == latest["decisions_sha256"]
        change["from_to"] = {"from": list(certified[1:]), "to": list(now[1:])}
        change["fingerprint"] = digest({"certification": latest["id"], "response_id": rid, **change["from_to"]})
        return change

    @staticmethod
    def _reasons(change):
        """Why this grade cannot be certified by itself (empty when it can): only the grade's own state. What else changed since
        certifying never stops it (A REGRADE NEVER ASKS TO CERTIFY AGAIN); `_others` says whether it carries the markers."""
        return [change["reason"]] if change.get("reason") else []

    @staticmethod
    def _others(certification, change):
        """What changed since certifying besides this grade (empty: nothing). A sheet's change (ONE BUBBLE SHEET) is itself a change
        of the multiple-choice sheets: `only` says whether it is the only one."""
        others = [] if change.get("only") else ["other grades"]
        mine = ("grades", "bubble_sheets") if change.get("sheet") else ("grades",)
        return others + [CHANGED.get(c, c) for c in (certification or {}).get("changed") or [] if c not in mine]

    def check(self, aid, response_id):
        """The Apply preview's forecast (writes nothing): {state: none|current|changed, possible, done, same, reasons, from, to,
        fingerprint, row}. `done`: the latest certification already holds this grade as it is (certified again meanwhile). `same`: the
        grade is as the latest certification recorded it, whatever else changed since (REGRADES "A GRADE AS CERTIFIED")."""
        with self.rosters.lock(), connect(self.path) as db:
            state = self.service._read(db, aid)
            certification = state["workflow"]["certification"]
            change = self._change(db, aid, state["latest"], state["responses"], state["decisions"], response_id)
        done = bool(change.get("same")) and certification.get("state") == "current"
        reasons = [] if done else self._reasons(change)
        if not reasons and not done and change.get("same"):
            reasons = ["The grade is as certified."]
        return {"state": certification.get("state") or "none", "possible": not reasons and not done, "done": done, "reasons": reasons,
                "same": bool(change.get("same")),
                **{k: change.get(k) for k in ("from", "to", "fingerprint", "row", "question_id")}}

    @staticmethod
    def _sheet_change(state, row):
        """ONE BUBBLE SHEET: this student's row as certified and as it is now, and whether it is the ONLY thing that changed: every
        other row as certified, this row's other parts as certified, and no free-response grade changed."""
        section_id, _, enrollment_id = str(row or "").partition(":")
        change = {"row": row, "section_id": section_id, "enrollment_id": enrollment_id, "sheet": True, "reason": ""}
        records = {_row(r["section_id"], r["enrollment_id"]): r for r in state["records"]}
        now, latest = records.get(row), state["latest"]

        def bubble(record):
            return certified_parts(record).get("bubble") or None
        change["to"] = progress.amount((bubble(now) or {}).get("earned")) if bubble(now) else None
        if not latest:
            return {**change, "reason": "The grades are not certified yet: they go to the gradebook when you certify."}
        certified_rows = state["certified_rows"]
        certified = certified_rows.get(row)
        change["from"] = progress.amount((bubble(certified) or {}).get("earned")) if bubble(certified) else None
        if not bubble(certified):
            return {**change, "reason": "This student's multiple choice was not part of the certification: certify again to record it."}
        if not bubble(now):
            return {**change, "reason": "This student's bubble sheet has no score now."}
        change["same"] = row_content(now) == row_content(certified)
        others = [key for key in (set(records) | set(certified_rows)) - set(dropped_no_work(state))
                  if key != row and row_content(records.get(key)) != row_content(certified_rows.get(key))]
        change["only"] = not others and row_content(now, part_left_out="bubble") == row_content(certified, part_left_out="bubble") \
            and state["markers"]["decisions"] == latest["decisions_sha256"]
        change["fingerprint"] = digest({"certification": latest["id"], "row": row, "from": row_content(certified), "to": row_content(now)})
        return change

    def check_sheet(self, aid, row):
        """`check` for one student's multiple-choice score (ONE BUBBLE SHEET); `from` and `to` are the sheet's points."""
        with self.rosters.lock():
            state = self.service._evaluate(aid, gradebook=False)
        certification = state["workflow"]["certification"]
        change = self._sheet_change(state, row)
        done = bool(change.get("same")) and certification.get("state") == "current"
        reasons = [] if done else self._reasons(change)
        if not reasons and not done and change.get("same"):
            reasons = ["The grade is as certified."]
        return {"state": certification.get("state") or "none", "possible": not reasons and not done, "done": done, "reasons": reasons,
                "same": bool(change.get("same")), **{k: change.get(k) for k in ("from", "to", "fingerprint", "row")}}

    # ----- certify it -----------------------------------------------------------------------------------------------------------
    @staticmethod
    def parse(data):
        if not isinstance(data, dict):
            raise ValueError("Send the change to certify as a JSON object.")
        request_id = str(data.get("request_id") or "")
        try:
            uuid.UUID(request_id)
        except ValueError:
            raise ValueError("Certifying needs a request ID.") from None
        if data.get("confirmation") != CONFIRMATION:
            raise ValueError("Confirm certifying this change.")
        fingerprint, rid, sheet = str(data.get("fingerprint") or ""), str(data.get("response_id") or ""), str(data.get("sheet_row") or "")
        if len(fingerprint) != 64 or bool(rid) == bool(sheet):
            raise ValueError("Name the response (or, for multiple choice, the student's row) and the fingerprint of the change you looked at.")
        replace = data.get("replace", False)
        if not isinstance(replace, bool):
            raise ValueError("Say replace as true or false.")
        message = data.get("message_id")
        if message is not None and not (isinstance(message, str) and 1 <= len(message) <= 64):
            raise ValueError("Name the regrade request by its ID.")
        request = {"request_id": request_id, "fingerprint": fingerprint, "response_id": rid, "replace": replace, "message_id": message}
        if sheet:  # ONE BUBBLE SHEET; a free-response request digests exactly as before
            request = {**request, "response_id": None, "sheet_row": sheet}
        return request, digest(request)

    def amend(self, aid, data, actor="workspace"):
        request, request_sha = self.parse(data)
        request_id, rid = request["request_id"], request["response_id"]
        with self.rosters.lock():
            with connect(self.path) as db:
                prior = db.execute("SELECT * FROM frq_certifications WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone()
                if prior:
                    if prior["request_sha256"] != request_sha:
                        raise Conflict("This request ID was used for a different certification.")
                    return {"certification": self.service._brief(dict(prior)), "replay": True}
                plan = self.frq.plan(db, aid)
                marks = self.service._marks(db, plan)
            state = self.service._evaluate(aid)
            latest = state["latest"]
            sheet = request.get("sheet_row")
            if sheet:
                change, coverage = self._sheet_change(state, sheet), "sheet"
            else:
                with connect(self.path) as db:
                    change = self._change(db, aid, latest, state["responses"], state["decisions"], rid)
                    coverage = self.service._coverage(db, aid, state["responses"], state["decisions"], only=[rid]).get(rid)
            reasons = self._reasons(change)
            if reasons:
                raise Refused(" ".join(reasons), state["checks"])
            if request["fingerprint"] not in self._fingerprints(aid, change):
                raise Conflict("The grade changed since the preview. Look at it again.")
            if not sheet and coverage != "sent":
                raise Refused("Send this grade to Gradescope first: a certification records grades that are on Gradescope.", state["checks"])
            records = {_row(r["section_id"], r["enrollment_id"]): r for r in state["records"]}
            certified = state["certified_rows"]
            target = change["row"]
            left_out = {"part_left_out": "bubble"} if sheet else {"without": change["question_id"]}
            # No row differs when the grade's lines changed and its points did not (always so on a plan graded for participation).
            if target not in records or row_content(records[target], **left_out) != row_content(certified[target], **left_out):
                raise Refused("This student's other grades changed since you certified too, so this one cannot go to the gradebook by itself: "
                              "certify the whole assignment again.", state["checks"])
            # A REGRADE NEVER ASKS TO CERTIFY AGAIN: everything below only decides whether the markers are live or carried.
            blocks = [c for c in state["checks"] if c["level"] == "block"]
            confirmed = json.loads(latest["confirmations_json"] or "{}")
            exempt = BOOK_CHECKS + COVERAGE_CHECKS + (MC_CHECKS if sheet else ())
            gone = dropped_no_work(state)
            moved = [c["title"] for c in state["checks"] if c["level"] == "confirm" and c["id"] not in exempt
                     and not _confirmation_holds(c, confirmed.get(c["id"]) or {}, state, gone)]
            confirmations = latest["confirmations_json"]
            if moved:
                # A REGRADE CARRIES THE CHAIN: a carried certification Faculty made by itself kept the lists as the instructor confirmed
                # them; with its students counted as they stood before, the lists now are what was confirmed, as LATE PAPERS reads them.
                from .certification_automatic import LateCertification, carried_chain
                from gradebook import sources
                with connect(self.path) as db:
                    _, rows_base, pending = carried_chain(db, latest)
                    base_rows = {_row(r["section_id"], r["enrollment_id"]): r for r in sources.certified_records(db, rows_base["id"])} if pending else {}
                rebuilt = LateCertification(self.service)._confirmations(state, latest, base_rows, set(pending)) if pending else None
                if rebuilt is not None:
                    moved, confirmations = [], encoded(rebuilt)
            differ = sorted(key for key in (set(records) | set(certified)) - set(gone) if row_content(records.get(key)) != row_content(certified.get(key)))
            others = self._others(state["workflow"]["certification"], change) + [c["title"] for c in blocks] + moved \
                + ["other students' rows"] * bool(set(differ) - {target})
            live = not others
            if live:
                rows = [*state["records"], *({**g, "parts_json": json.loads(g["parts_json"])} for g in gone.values())]
                markers = state["markers"]
            else:
                confirmations = latest["confirmations_json"]  # a carried chain goes on: its lists stay as they were confirmed
                composed = {row: {**r, "parts_json": certified_parts(r)} for row, r in certified.items()}
                composed[target] = records[target]
                rows = sorted(composed.values(), key=lambda r: (r["section_id"], r["enrollment_id"]))
                decisions = state["markers"]["decisions"] if change.get("only") and not sheet else latest["decisions_sha256"]
                markers = {"decisions": decisions, "bubble": latest["bubble_sha256"], "participants": latest["participants_sha256"]}
            record, plan, context = records[target], state["plan"], state["context"]
            item_id = (json.loads(latest["gradebook_json"] or "{}") or {}).get("item_id")
            replace = {target} if request["replace"] else set()

            def options(db, rows, selected=None):
                return self.gradebook.certification_options(db, context, assignment_id=plan["source_assignment_id"], plan_id=plan["id"],
                                                            event_id=plan["event_id"], title=plan["title"], records=rows,
                                                            certified_parts=state["totals"]["parts"], exam_sections=state["exam_sections"],
                                                            replace=replace, selected=selected, category_id=None)
            with connect(self.path) as db:
                book = options(db, [record])
                option = next((o for o in book["options"] if o["item"]["id"] == item_id and not o["writes_setup"]), None)
                if option and replace:
                    book = options(db, [record], option["key"])
                    option = next(o for o in book["options"] if o["key"] == option["key"])
            if not option:
                raise Refused("The gradebook column these grades were certified into changed: certify the whole assignment again.", state["checks"])
            summary = rules.summary(rows)
            if sheet:
                summary["amends"] = {"certification_id": latest["id"], "part": "bubble", "row": target, "from": change["from"], "to": change["to"],
                                     "message_id": request["message_id"]}
                gradescope = {**json.loads(latest["gradescope_json"] or "{}"), "amended": {"row": target, "part": "bubble"}}
            else:
                summary["amends"] = {"certification_id": latest["id"], "response_id": rid, "question_id": change["question_id"], "row": target,
                                     "from": change["from"], "to": change["to"], "message_id": request["message_id"]}
                gradescope = {**json.loads(latest["gradescope_json"] or "{}"), "amended": {"response_id": rid, "coverage": coverage}}
            summary["amends"]["markers"] = "live" if live else "carried"
            if not live:
                summary["amends"]["others"] = others
                summary["amends"]["moved"] = who_moved(state)
            cid, now = uid(), timestamp()
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if self.service._marks(db, plan) != marks:
                    raise Refused("The grades changed while they were being checked. Look again.", state["checks"])
                if (progress.latest_certification(db, aid) or {}).get("id") != latest["id"]:
                    raise Refused("Another certification was recorded meanwhile. Look again.", state["checks"])
                summary["grade_marker"] = progress.grade_marker(db, aid, state["totals"]["parts"]) if live else \
                    json.loads(latest["summary_json"] or "{}").get("grade_marker")
                prepared = self.gradebook.prepare_certification(db, context, term_id=plan["term_id"], option=option, revision=book["revision"],
                                                                cells=option["cells"], request_id=f"certify:{request_id}")
                db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
                    participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,supersedes_id,actor,created_at,amends_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (cid, aid, request_id, request_sha, request["fingerprint"], progress.RULE, encoded(state["totals"]["parts"]),
                            markers["decisions"], markers["bubble"], markers["participants"], state["snapshot_sha256"] if live else rules.snapshot_digest(rows),
                            encoded(summary), latest["checks_json"], confirmations, encoded(gradescope), encoded(prepared["receipt"]),
                            latest["id"], actor, now, latest["id"]))
                for r in rows:
                    db.execute("""INSERT INTO frq_certification_students(certification_id,section_id,enrollment_id,status,reason,earned,possible,exam_version,parts_json,dr_date)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""", (cid, r["section_id"], r["enrollment_id"], r["status"], r["reason"], r["earned"], r["possible"],
                                                       r["exam_version"], encoded(r["parts_json"]), r["dr_date"]))
                from gradebook import sources
                stored_rows = [r for r in sources.certified_records(db, cid) if _row(r["section_id"], r["enrollment_id"]) == target]
                stored = next((o for o in options(db, stored_rows, option["key"])["options"] if o["key"] == option["key"]), None)
                if stored is None:
                    raise Conflict("The gradebook column changed while certifying. Look again.")
                receipt = self.gradebook.record_certification(db, context, prepared=prepared, option=option, stored=stored, request_sha=request_sha,
                                                              certification_id=cid, actor=actor)
                self.frq.event(db, aid, "grades_certified", {"certification_id": cid, "amends": summary["amends"],
                                                             "gradebook": {k: receipt[k] for k in ("item_id", "written", "kept", "created_column")}}, actor)
            with connect(self.path) as db:
                certification = self.service._brief(dict(db.execute("SELECT * FROM frq_certifications WHERE id=?", (cid,)).fetchone()))
        return {"certification": certification, "replay": False, "markers": "live" if live else "carried",
                "gradebook": {k: receipt[k] for k in ("item_id", "title", "written", "kept")}}

    def _fingerprints(self, aid, change):
        """The fingerprints a preview of this same change (its response or row, from and to) could have named: one per certification
        of the plan, so a certification Faculty made by itself between the preview and this step (a late paper) never turns the
        same change into "changed since the preview"."""
        with connect(self.path) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM frq_certifications WHERE assignment_id=?", (aid,))]
        found = {change["fingerprint"]}
        if change.get("from_to"):  # a sheet's fingerprint names whole rows as certified: only the latest's
            found.update(digest({"certification": cid, "response_id": change["response_id"], **change["from_to"]}) for cid in ids)
        return found

