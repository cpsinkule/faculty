"""Certification and Export, the sixth grading stage (requirement, 2026-09-26). GRADING.md "Certification and Export".

`CertificationService(frq, gradescope, gradebook)` (critique C5: its own service, built in `app.py` after the three it reads; the
Inbox worker shares `create_app`, so it is constructed there too; the worker calls it only through `certification_automatic.py`,
which certifies late papers by itself, GRADING "LATE PAPERS CERTIFY THEMSELVES"). Everything but `certify` READS:

- `view(aid)` the page (statistics, questions, students, provisional checks): one read under the roster lifecycle lock, the roster
  payload read once (`_participants`), grading read through the workflow's own summary (`workflow_summary`, which already counts
  new, failed, pending and version work) and ONE SQL projection of every response's decided result. The checks here are
  provisional: the certify preview checks everything again, exactly.
- `question(aid, qid)`, `student(aid, section_id, enrollment_id)`, `search(aid, data)`: drill-downs. Names come from the verified
  roster; UIN digits only ever arrive in a POST body and are answered with names (never a UIN in JSON, a URL, a log or an event).
- `preview(aid, data)`: the exact state (`final_decisions` once, `gradebook.sources.totals` on it, every check, the snapshot, the
  gradebook plan of every column the certification may record in) and the fingerprint a certify must name, with the absences
  recorded for each student about to receive 0 beside them (display only, in no digest; requirement, 2026-10-02). Writes nothing.
- `adjust_preview(aid, qid, data)` / `adjust(aid, qid, data)` (grading/batch_adjustments.py, 2026-09-27): a named point adjustment
  added to every final grade of one question in one section, or an earlier batch's line edited or removed, one ordinary review
  each, preview then apply by fingerprint; `question` names each grade's adjustments and lists the question's batches.
- `certify(aid, data)`: the preview again under the lifecycle lock (payload reads first), refuses a changed fingerprint, any block
  and any unconfirmed check, then ONE `BEGIN IMMEDIATE` re-checks only catalog high-water marks (critique C3) and writes the
  immutable certification (migration 029), its student rows, the gradebook's values (through the gradebook's own plan and
  `change_action`) and an `frq_events` receipt. Idempotent by `request_id`; nothing is partial.

Nothing here decides, finalizes, publishes, emails or sends a grade, and it never writes `exam_participants` ("No scan is not an
inferred absence"): final grades stay the reviews' and acceptances' own; a certified 0 is the instructor's explicit confirmation.
"""
import json
import re
from decimal import Decimal

from werkzeug.exceptions import Conflict, NotFound

from absences import makeups
from course_storage import timestamp
from exam_workflow.given_dates import given_dates
from gradebook import common, sources
from rosters import connect

from . import certification_rules as rules
from . import gradescope_after
from . import participation
from . import progress
from .batch_adjustments import BatchAdjustmentMixin, grade_adjustments, same_line
from .exam_versions import DECIDE
from .models import digest, encoded, uid

HISTORY = 10
WAIT_VERSION = ("checking", "waiting_blank")
DIGITS = re.compile(r"[0-9]{4,12}")
ACCEPTANCES = ("frq_acceptances", "frq_team_acceptances", "frq_one_shot_acceptances", "frq_tag_team_acceptances")
ACCEPTANCE_EVENTS = (("frq_team_acceptance_events", "frq_team_acceptances"), ("frq_one_shot_acceptance_events", "frq_one_shot_acceptances"),
                     ("frq_tag_team_acceptance_events", "frq_tag_team_acceptances"))
UNITS = ("frq_gradescope_uploads", "frq_gradescope_grades", "frq_gradescope_sheet_uploads")
SENDS = ("frq_gradescope_sends", "frq_gradescope_sheet_sends")


def campus_today():
    from exam_workflow.overview import campus_today as today
    return today()


class Refused(Conflict):
    """A certify refused by the checks themselves (a block, or a confirmation missing): the page redraws them from `checks`."""

    def __init__(self, description, checks=()):
        super().__init__(description)
        self.checks = list(checks)


def _row(section_id, enrollment_id):
    return common.row_key(section_id, enrollment_id)


class CertificationService(BatchAdjustmentMixin):
    def __init__(self, frq, gradescope, gradebook):
        self.frq, self.gradescope, self.gradebook = frq, gradescope, gradebook
        self.rosters, self.path = frq.rosters, frq.path

    # ----- the one read ------------------------------------------------------------------------------------------------------
    def _read(self, db, aid, exact=False):
        """Everything the page (provisional) or the certify preview (`exact`) decides on, from ONE read: the caller holds the
        lifecycle lock and `db` is an open connection with no transaction (the roster payload read may reconcile storage)."""
        frq = self.frq
        plan = frq.plan(db, aid)
        event = frq.exams._event(db, plan["event_id"])
        participants = frq.exams._participants(db, event)  # the verified roster payload, once: names
        collection = frq.exams._collection(db, event, participants)
        shared, quality = {}, frq.rubric_quality_status(db, aid)  # one memo and one finalization status for the listing and the counts
        responses = frq._responses(db, plan, event, participants, shared=shared, quality=quality)
        pins = [row[0] for row in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY rowid", (aid,))]
        questions = [frq.question(db, aid, qid) for qid in pins]
        for q in questions:  # once per question, as the assignment page does: the workflow counts and its setup read these
            q["location_check"] = frq.location_status(db, aid, q, memo=shared)
        details = {}
        workflow = frq.workflow_summary(db, {**plan, "questions": questions, "rubric_quality": quality}, responses, context=(event, participants, collection),
                                        details=details, shared=shared, export=False)  # `export`: read by no step of the page
        # GRADING "GRADED FOR PARTICIPATION": the page shows each final grade as it counts.
        taking_part = {"enabled": participation.enabled(db, aid), "bubble_key": participation.has_bubble_key(db, aid)}
        decisions = participation.counted_entries(db, aid, details["decisions"]) if taking_part["enabled"] else details["decisions"]
        link = sources.available(db, plan["term_id"]).get(plan["source_assignment_id"]) or {"plan_id": aid, "event_id": event["id"], "parts": []}
        parts = list(link.get("parts") or [])
        maxima = sources.maxima(db, link)
        brief = frq.bubble_brief(db, plan)
        bubble = None
        if "bubble" in parts or brief["sheets"]:
            _, bubble = frq._bubble_view(aid)  # its own connection, re-entering this lock
        people = {(p["section_id"], p["enrollment_id"]): p for p in participants}
        for p in participants:
            frq.paper_version(db, plan, p, shared)  # memoized on the participant; writes nothing
        state = {"plan": plan, "event": event, "participants": participants, "people": people, "collection": collection, "responses": responses,
                 "questions": questions, "workflow": workflow, "details": details, "decisions": decisions, "parts": parts,
                 "maxima": maxima, "bubble": bubble, "today": campus_today(),
                 "projection": self._projection(db, aid), "dr": progress.dr_bookings(db, plan),
                 "additions": progress.roster_additions(db, event["id"]), "latest": progress.latest_certification(db, aid),
                 # MAKEUP OWED (requirement, 2026-10-07): whose excused or undecided absence covers the day it was given (ABSENCES' read).
                 "makeups": makeups.covering(db, plan["term_id"], event["id"], plan["source_assignment_id"], people),
                 # ABSENCES MAKEUPS "MISSED" (requirement, 2026-10-31): whose makeup the instructor recorded as missed (their 0 says so).
                 "missed": {key[1:] for key in makeups.missed(db, event["id"])}, "participation": taking_part}
        state["certified_rows"] = {_row(r["section_id"], r["enrollment_id"]): r for r in sources.certified_records(db, state["latest"]["id"])} if state["latest"] else {}
        state["addition_names"] = self._names(db, event, state["additions"]) if state["additions"] else {}
        state["gradescope"] = self._gradescope(db, state)
        state["inbox"] = self._inbox(db, state)
        if exact:
            state["final"] = participation.counted_decisions(db, aid, frq.final_decisions(db, aid))  # GRADING "GRADED FOR PARTICIPATION"
        state["totals"], state["expected"] = self._totals(db, state, exact)
        state["students"] = self._students(db, state)
        return state

    def _names(self, db, event, keys):
        """{(section_id, enrollment_id): {name, section}} of roster members who are not participants (roster additions), from the
        verified roster payload (the caller holds the lifecycle lock and no transaction)."""
        from seating import student_name
        term = dict(db.execute("SELECT * FROM course_terms WHERE id=?", (event["term_id"],)).fetchone())
        with self.rosters.files(term) as root:
            payload = self.rosters.read_payload(root, term["roster_initialized"])
        numbers = dict(db.execute("SELECT id,number FROM course_sections WHERE term_id=?", (term["id"],)).fetchall())
        return {(section, enrollment): {"name": student_name({**payload["sections"].get(section, {}).get("students", {}).get(enrollment, {}), "id": enrollment}),
                                        "section": numbers.get(section, "")} for section, enrollment in keys}

    @staticmethod
    def _projection(db, aid, question_id=None, student=None):
        """ONE SQL read of every response's decided result: for an instructor's approved or manual review the result it was made
        on (critique C8: never a newer proposal), else the current result; its original state, the finalization's base state, the
        models' flags and outcomes, the automatic acceptance's basis and whether anyone reviewed it."""
        where, args = "r.assignment_id=?", [aid]
        if question_id:
            where, args = where + " AND r.question_id=?", args + [question_id]
        if student:
            where, args = where + " AND r.section_id=? AND r.enrollment_id=?", args + list(student)
        found = {}
        for row in db.execute(f"""SELECT r.id,r.question_id,r.section_id,r.enrollment_id,v.status review_status,v.result_id review_result,v.score_json review_score,
                v.actor review_actor,v.created_at review_at,{participation.APPROVAL} approved_alone,x.id result_id,x.state,x.score_json result_score,
                json_extract(x.signals_json,'$.rubric_quality.base_state') base_state,json_array_length(x.reasons_json) reasons,
                json_extract(x.signals_json,'$.arbiter.outcome') arbiter,json_extract(x.signals_json,'$.flag_review.outcome') flag_review,
                json_extract(x.signals_json,'$.flag_resolution.outcome') flag_resolution,
                json_extract(x.signals_json,'$.jev.outcome') jev,json_extract(x.signals_json,'$.choice.rule') choice,json_extract(x.signals_json,'$.committee.status') committee,
                json_array_length(json_extract(x.signals_json,'$.risk.blocks')) risk_blocks,json_extract(x.signals_json,'$.risk.individual') risk_individual,
                COALESCE(json_array_length(json_extract(x.signals_json,'$.risk.waived.blocks')),0)+COALESCE(json_array_length(json_extract(x.signals_json,'$.risk.waived.reasons')),0) risk_waived,
                json_extract(x.signals_json,'$.answer.group') answer_group,json_extract(x.signals_json,'$.answer.answers[0].text') answer_text
            FROM frq_responses r LEFT JOIN frq_reviews v ON v.id=r.current_review_id
            LEFT JOIN frq_results x ON x.id=CASE WHEN v.status IN ('approved','manual') THEN v.result_id ELSE r.current_result_id END
            WHERE {where}""", [participation.APPROVED, *args]):
            found[row["id"]] = dict(row)
        accepted = {}
        for row in db.execute("""SELECT a.result_id,a.id,a.basis,EXISTS(SELECT 1 FROM frq_all_acceptance_reviews v WHERE v.acceptance_id=a.id) reviewed
                FROM frq_all_acceptances a WHERE a.assignment_id=? AND a.kind='automatic'""", (aid,)):
            accepted[row["result_id"]] = dict(row)  # the automatic one only (a result may also carry a trial acceptance)
        for row in found.values():
            row["acceptance"] = accepted.get(row["result_id"])
            row["flags"] = rules.flags(row)
            row["settled"] = rules.settled(row)
        return found

    def _gradescope(self, db, state):
        """Receipts only (Gradescope is never contacted): the written grades' and papers' coverage, the sheets', whether a send is
        live, and the previous certification's decisions (for the "do not send" reason's prefill)."""
        from .gradescope_sync import GradescopeSender
        aid = state["plan"]["id"]
        written = GradescopeSender.coverage_detail(db, aid, state["responses"], state["participants"], state["decisions"])
        sheets = self.gradescope.sheet_coverage_detail(db, aid, state["plan"]) if "bubble" in state["parts"] else None
        live = any(db.execute(f"SELECT 1 FROM {table} WHERE assignment_id=? AND status IN ('pending','running') LIMIT 1", (aid,)).fetchone() for table in SENDS) \
            or any(db.execute(f"SELECT 1 FROM {table} WHERE assignment_id=? AND status IN ('sending','uncertain') LIMIT 1", (aid,)).fetchone() for table in UNITS)
        sent = any(db.execute(f"SELECT 1 FROM {table} WHERE assignment_id=? AND status='accepted' LIMIT 1", (aid,)).fetchone() for table in UNITS)
        previous = json.loads(state["latest"]["gradescope_json"]) if state["latest"] else None
        # THE MC CHECK (gradescope_mc): Gradescope's last read of the bubble-sheet scores beside Faculty's, from the records only.
        mc = self.gradescope.mc_compare(db, aid, state["bubble"]) if "bubble" in state["parts"] else None
        return {"configured": bool(self.gradescope.settings.status().get("configured")), "live": bool(live), "anything_sent": bool(sent),
                "written": written, "sheets": sheets, "previous": previous, "mc_check": mc}

    def _inbox(self, db, state):
        """What may still be in the Inbox for this plan: linked scans not cleared, bubble sheets in scans under review, first pages
        found by OCR that no student is registered to yet (a no-paper student's may be one)."""
        scans, waiting = state["details"].get("scans") or [], state["workflow"].get("awaiting_review", 0)
        unregistered = 0
        for scan in scans:
            try:
                found = self.frq.exams.scans.unlabeled.status(db, scan["message_id"])
            except (AttributeError, KeyError):
                found = None
            if found:
                # Every registration comes from a plan (document_identity/unlabeled.py), so `registered` is part of `planned`: the
                # first pages waiting are the scan overview's own count (document_identity/scans.py: covers - not_cover - planned).
                unregistered += max(0, found["covers"] - found["planned"] - found["not_cover"])
        bubble_waiting = sum(scan["sheets"] for scan in (state["bubble"] or {}).get("waiting_scans") or [])
        pending = next((s for s in scans if not s.get("inbox_ready")), None)
        return {"awaiting": waiting, "bubble_waiting": bubble_waiting, "unregistered": unregistered,
                "href": f"#inbox/job/{pending['job_id']}" if pending else "#inbox"}

    def _totals(self, db, state, exact):
        """Each participant's parts in the gradebook's own shape (`sources.totals`). Exact for the preview and certify
        (`final_decisions`); provisional for the page, from the listing's final states and the one decision read."""
        rows = [{"row": _row(p["section_id"], p["enrollment_id"]), "section_id": p["section_id"], "enrollment_id": p["enrollment_id"],
                 "section": p["section_number"]} for p in state["participants"]]
        if exact:
            decisions = state["final"]
        else:
            maxima = {q["question_id"]: q["content"].get("max_points") for q in state["questions"]}
            pseudo = [{"row": {"section_id": e["section_id"], "enrollment_id": e["enrollment_id"], "question_id": e["question_id"]},
                       "final": {"score": {"score": e["score"], "max_score": maxima.get(e["question_id"])}}}
                      for e in state["decisions"].values() if e["kind"] and e["score"] is not None]
            decisions = (state["plan"], pseudo, [])
        expected = {}
        if not state["parts"]:
            return {"plan_id": state["plan"]["id"], "event_id": state["event"]["id"], "parts": [], "warnings": [],
                    "students": {r["row"]: sources._combine({}) for r in rows}}, expected
        found = sources.totals(self.frq, db, state["plan"]["term_id"], state["plan"]["source_assignment_id"], state["parts"], rows=rows,
                               decisions=decisions, people=state["people"], bubble_view=state["bubble"], expected=expected)
        return found, expected

    def _sheets(self, state):
        """{row: [sheet]} of the plan's bubble sheets of cleared scans placed on participants as the gradebook places them (enrollment
        and section number), and the sheets placed on no participant: matched to nobody, to someone who is not in this exam, or to a
        student in two sections of it with no section of the sheet's (critique C.2: each may be a zero student's)."""
        by_enrollment, placed, unmatched = {}, {}, []
        for p in state["participants"]:
            by_enrollment.setdefault(p["enrollment_id"], []).append(p)
        for sheet in (state["bubble"] or {}).get("sheets") or []:
            if not sheet.get("enrollment_id"):
                unmatched.append(sheet)
                continue
            candidates = by_enrollment.get(sheet["enrollment_id"], [])
            chosen = [p for p in candidates if str(p["section_number"]) == str(sheet.get("section"))] or (candidates if len(candidates) == 1 else [])
            if len(chosen) == 1:
                placed.setdefault(_row(chosen[0]["section_id"], chosen[0]["enrollment_id"]), []).append(sheet)
            else:
                reason = ("Matched to a student in two sections of this exam; the sheet's section is neither" if candidates
                          else "Matched to someone who is not in this exam")
                unmatched.append({**sheet, "reason": reason, "outside": True})
        return placed, unmatched

    def _students(self, db, state):
        """Every participant, classified (`rules.classify`) with what the page and the snapshot need."""
        parts, today = state["totals"]["parts"], state["today"]
        placed, unmatched = self._sheets(state)
        state["unmatched_sheets"] = unmatched
        bookings = {}
        for booking in state["dr"]:
            bookings.setdefault((booking["section_id"], booking["enrollment_id"]), []).append(booking)
        memberships = {}
        for p in state["participants"]:
            memberships.setdefault(p["enrollment_id"], []).append(p)
        by_student = {}
        for rid, entry in state["decisions"].items():
            by_student.setdefault((entry["section_id"], entry["enrollment_id"]), []).append(rid)
        students = []
        for p in state["participants"]:
            key = (p["section_id"], p["enrollment_id"])
            row = _row(*key)
            total = state["totals"]["students"].get(row) or sources._combine({})
            found = p.get("_exam_version") or {}
            sheets = placed.get(row, [])
            others = [o for o in memberships.get(p["enrollment_id"], []) if o is not p]
            duplicate = None
            if others:
                duplicate = {"other_has_work": any((o.get("collection") or {}).get("status") in rules.WORK or placed.get(_row(o["section_id"], o["enrollment_id"]))
                                                   for o in others)}
            student = {"row": row, "section_id": p["section_id"], "enrollment_id": p["enrollment_id"], "name": p["name"], "section": p["section_number"],
                       "expectation": p["expectation"], "participant_version": p["version"], "collection": (p.get("collection") or {}).get("status"),
                       "sheets": len(sheets), "dr": bookings.get(key, []), "makeup": state["makeups"].get(key), "missed_makeup": key in state.get("missed", ()), "duplicate": duplicate, "parts": total.get("parts") or {},
                       "exam_version": found.get("version") or "", "paper_version": {k: found.get(k) for k in ("status", "version", "reason")}}
            student.update(rules.classify(student, parts, today, state["maxima"]))
            if p.get("dropped") and student["status"] == "zero":
                # Dropped from the roster with no work at all (rosters.set_dropped, requirement, 2026-09-27): not part of this
                # certification, never a confirmed 0. A dropped student with any work is certified like anyone else.
                state["dropped_left_out"] = state.get("dropped_left_out", 0) + 1
                continue
            frq_part = (total.get("parts") or {}).get("frq") or {}
            student["frq_version"] = (found.get("version") or "") if frq_part.get("state", "none") != "none" else ""
            scored_sheet = next((s for s in sheets if s.get("status") == "scored"), None)
            student["bubble_version"] = (scored_sheet or {}).get("version") or ""
            student["sheet"] = scored_sheet or (sheets[0] if sheets else None)
            mine = [state["projection"].get(rid) or {} for rid in by_student.get(key, [])]
            student["flagged"] = sum(1 for m in mine if m.get("flags") and not m.get("settled"))
            student["settled"] = sum(1 for m in mine if m.get("settled"))
            student["zero_questions"] = sum(1 for rid in by_student.get(key, []) if state["decisions"][rid]["kind"] and rules.dec(state["decisions"][rid]["score"]) == 0)
            previous = state["certified_rows"].get(row)
            student["certified"] = {"status": previous["status"], "earned": previous["earned"]} if previous else None
            now = (student["status"], rules.text(student["earned"]) if student["status"] in ("scored", "zero") else None)
            student["changed_since"] = bool(state["latest"]) and (not previous or (previous["status"], rules.text(previous["earned"])) != now)
            students.append(student)
        return students

    # ----- facts and checks --------------------------------------------------------------------------------------------------
    def _question_rows(self, state):
        coverage = state["gradescope"]["written"]["questions"]
        per = {}
        for rid, entry in state["decisions"].items():
            projection = state["projection"].get(rid) or {}
            acceptance = projection.get("acceptance") or {}
            per.setdefault(entry["question_id"], []).append({
                "final": bool(entry["kind"]), "score": entry["score"], "kind": entry["kind"], "status": entry["state"],
                "basis": rules.basis(acceptance.get("basis"), projection) if entry["kind"] == "automatic" else None,
                "flags": projection.get("flags") or [], "settled": bool(projection.get("settled")), "reviewed": bool(acceptance.get("reviewed")),
                "overridden": rules.overridden(projection.get("review_status"), *self._selections(projection))})
        rows = []
        for q in state["questions"]:
            content = q["content"]
            item = {"question_id": q["question_id"], "title": content.get("title") or q["question_key"], "printed_number": content.get("printed_number"),
                    "version": q["exam_version"] or "", "max": content.get("max_points")}
            entry = coverage.get(q["question_id"]) or {}
            rows.append(rules.question_row(item, per.get(q["question_id"], []), {**entry, "unsendable": entry.get("unsendable") or
                                                                              state["gradescope"]["written"]["unsendable"].get(q["question_id"])}))
        return sorted(rows, key=lambda r: (r["version"], self._number(r["printed_number"]), r["title"]))

    @staticmethod
    def _number(value):
        try:
            return (0, float(value), "")
        except (TypeError, ValueError):
            return (1, 0.0, str(value or ""))

    @staticmethod
    def _selections(projection):
        """(review selected, review adjustments, result selected, result adjustments) of a decided response."""
        review = json.loads(projection.get("review_score") or "null") or {}
        result = json.loads(projection.get("result_score") or "null") or {}
        return review.get("selected") or [], review.get("adjustments") or [], result.get("selected") or [], result.get("adjustments") or []

    def _facts(self, state, exact=False):
        plan, students = state["plan"], state["students"]
        names = {s["row"]: {"name": s["name"], "section": s["section"]} for s in students}
        workflow, details = state["workflow"], state["details"]
        responses = {r["id"]: r for r in state["responses"]}
        titles = {q["question_id"]: q["content"].get("title") or q["question_key"] for q in state["questions"]}
        no_grade = []
        for r in state["responses"]:
            if r["state"] in rules.NO_GRADE:
                who = names.get(_row(r["section_id"], r["enrollment_id"])) or {}
                no_grade.append({"row": _row(r["section_id"], r["enrollment_id"]), "name": who.get("name", ""), "section": who.get("section", ""),
                                 "question": titles.get(r["question_id"], ""), "status": r["state"], "response_id": r["id"]})
        versions = [s for s in students if (s["paper_version"] or {}).get("status") in DECIDE + WAIT_VERSION and s["collection"] in rules.WORK]
        papers = [s for s in students if s["collection"] in ("needs_review", "missing_pages")]
        bubble = None
        if "bubble" in state["totals"]["parts"] or (state["bubble"] and state["bubble"].get("sheets")):
            view = state["bubble"] or {}
            placed, _ = self._sheets(state)
            unsettled = []
            for s in view.get("sheets") or []:
                if s.get("status") in ("needs_review", "no_version", "no_key"):
                    unsettled.append({"name": s.get("student") or "", "section": s.get("section") or "", "status": s["status"], "reason": s.get("reason") or "",
                                      "sheet_id": s.get("sheet_id"), "row": None})
            duplicates = [next(x for x in students if x["row"] == row) for row, sheets in placed.items() if len(sheets) > 1]
            bubble = {"part": "bubble" in state["totals"]["parts"] or bool(view.get("sheets")), "sheets": unsettled, "duplicates": duplicates,
                      "beyond_read": view.get("beyond_read") or [], "has_key": bool(view.get("key")),
                      "unmatched": [{"sheet_id": s.get("sheet_id"), "reason": s.get("reason") or "", "job_id": s.get("job_id"), "page": s.get("page"),
                                     "outside": bool(s.get("outside"))}
                                    for s in state.get("unmatched_sheets") or []]}
        failed = details.get("failed") or set()
        facts = {"plan_id": plan["id"], "event_id": plan["event_id"], "parts": state["totals"]["parts"], "students": students, "names": names,
                 "question_titles": titles,
                 "unclaimed": {"unmatched_sheets": len(state.get("unmatched_sheets") or []),
                               "unassigned_papers": state["collection"].get("unassigned_received_count", 0),
                               "unregistered_first_pages": state["inbox"]["unregistered"]},
                 "work": {"states": dict(workflow["responses"]), "new": workflow.get("new_responses", 0) - workflow.get("regrade_responses", 0),
                          "failed": len(failed), "pending_tasks": workflow.get("pending", 0)},
                 "no_grade": no_grade, "versions": versions, "inbox": state["inbox"], "papers_in_review": papers, "bubble": bubble,
                 "roster_additions": [{"row": _row(*key), **state["addition_names"].get(key, {"name": "", "section": ""})} for key in state["additions"]],
                 "gradescope": state["gradescope"], "questions": state["question_rows"],
                 "rubric_quality": {"state": ((workflow.get("setup") or {}).get("rubric_quality") or {}).get("state"),
                                    "not_standing": ((workflow.get("setup") or {}).get("rubric_quality") or {}).get("held") or []},
                 "by_version": state["stats"]["by_version"], "possible_by_version": self._possible_by_version(state),
                 "training_waiting": state.get("training_waiting", 0), "bubble_suspects": state.get("bubble_suspects") or [],
                 "answer_groups": self._answer_groups(state),
                 "participation": {**state["participation"], "lines": sum(1 for e in state["decisions"].values() if e.get("participation")),
                                   # The waiting grades Faculty approved as they were (GRADING "THE WAITING GRADES APPROVE THEMSELVES").
                                   "approved": sum(1 for rid, e in state["decisions"].items()
                                                   if e["kind"] == "review" and (state["projection"].get(rid) or {}).get("approved_alone"))}}
        if exact:
            skipped = []
            for s in state["final"][2]:
                response = responses.get(s["response_id"])
                row = _row(response["section_id"], response["enrollment_id"]) if response else None
                who = names.get(row) or {"name": "", "section": ""}
                skipped.append({"row": row, "name": who["name"], "section": who["section"], "reason": s["reason"], "response_id": s["response_id"],
                                "question": titles.get((response or {}).get("question_id"), "")})
            facts["skipped"] = skipped
            facts["out_of_range"] = state["out_of_range"]
            facts["changes"] = state.get("changes") or []
        return facts

    def _possible_by_version(self, state):
        found = {}
        for q in state["questions"]:
            if q["exam_version"]:
                found.setdefault(q["exam_version"], Decimal(0))
        shared = sum((Decimal(str(q["content"].get("max_points") or 0)) for q in state["questions"] if not q["exam_version"]), Decimal(0))
        for q in state["questions"]:
            if q["exam_version"]:
                found[q["exam_version"]] += Decimal(str(q["content"].get("max_points") or 0))
        return {letter: value + shared for letter, value in found.items()}

    def _answer_groups(self, state):
        """Responses with the same final answer (the answer check's group) but different final scores, per question (critique C.4)."""
        titles = {q["question_id"]: q["content"].get("title") or q["question_key"] for q in state["questions"]}
        groups = {}
        for rid, entry in state["decisions"].items():
            projection = state["projection"].get(rid) or {}
            if entry["kind"] and projection.get("answer_group"):
                group = groups.setdefault((entry["question_id"], projection["answer_group"]), {"question_id": entry["question_id"], "group": projection["answer_group"],
                                                                                            "title": titles.get(entry["question_id"], ""),
                                                                                            "answer": (projection.get("answer_text") or "")[:120], "scores": {}})
                group["scores"][entry["score"]] = group["scores"].get(entry["score"], 0) + 1
        return [g for g in groups.values() if len(g["scores"]) > 1]

    def _stats(self, state):
        students = state["students"]
        complete = [s for s in students if s["status"] == "scored" and s["reason"] != "missing_part"]
        part = lambda name: rules.stats([((s["parts"].get(name) or {}).get("earned"), (s["parts"].get(name) or {}).get("possible")) for s in students
                                         if (s["parts"].get(name) or {}).get("state") == "complete"])
        return {"total": rules.total_stats(students), **{name: part(name) for name in state["totals"]["parts"]},
                "by_version": rules.grouped(complete, "exam_version"), "by_section": rules.grouped(complete, "section")}

    def _extras(self, db, state):
        """Information that moves on its own (training capture) or that reads the bubble key: kept out of the fingerprint (C4)."""
        plan = state["plan"]
        taken = {(r[0], r[1]) for r in db.execute("SELECT decision_kind,decision_id FROM frq_training_examples WHERE assignment_id=?", (plan["id"],))}
        state["training_waiting"] = sum(1 for e in state["decisions"].values() if e["kind"] and (e["kind"], e["decision_id"]) not in taken)
        view = state["bubble"] or {}
        state["bubble_suspects"] = rules.bubble_key_suspects(view.get("key"), view.get("stats")) if view.get("key") else []

    def _checks(self, db, state, exact=False):
        state["question_rows"] = self._question_rows(state)
        state["stats"] = self._stats(state)
        self._extras(db, state)
        facts = self._facts(state, exact)
        return rules.build_checks(facts, provisional=not exact)

    # ----- the page ---------------------------------------------------------------------------------------------------------
    def view(self, aid):
        with self.rosters.lock(), connect(self.path) as db:
            state = self._read(db, aid)
            checks = self._checks(db, state)
            history = [self._brief(dict(r)) for r in db.execute("SELECT * FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT ?", (aid, HISTORY))]
            state["automatic"] = self.frq.auto_certify_state(db, aid, self.frq.settings()["settings"])
            # What Faculty is about to certify by itself (requirement, 2026-11-14: the page asked for their Certify meanwhile).
            if state["latest"] and state["automatic"]["enabled"] and not state["automatic"].get("reason"):
                from .certification_automatic import WAIT, forecast  # it imports this module
                wait = next((c["title"] for c in checks if c["level"] == "block" and c["id"] in WAIT), None)
                unclaimed = any(c["id"] == "zero_no_work" and c.get("warning") for c in checks)
                state["automatic_next"] = {**forecast(state, {w["row"] for w in state["automatic"]["waiting"]}, unclaimed), "wait": wait}
            # Faculty's own sends of late papers (GRADESCOPE "AUTOMATIC LATE-PAPER SEND"): why one waits or was not sent, per student.
            state["automatic_sends"] = self.gradescope.automatic_sends(db, aid)
            state["unpublished_versions"] = self.gradescope.unpublished_versions(db, aid)
            # GRADESCOPE "AFTER CERTIFYING": what Faculty is doing on Gradescope by itself, did, or holds and why. While it works, or
            # holds a section, its own line says so and the older "not published" notice of a late paper's version is not repeated.
            state["gradescope_runs"] = self.gradescope.after_runs(db, aid)
            if any(run["state"] == "working" or run.get("held") for run in state["gradescope_runs"]):
                state["unpublished_versions"] = []
        return self._page(state, checks, history)

    @staticmethod
    def _automatic(state):
        """LATE PAPERS CERTIFY THEMSELVES on the page: whether it is on, whom Faculty certified by itself since the instructor last
        certified (the latest certification's own list), and the late papers still waiting with why (names from the roster read)."""
        found = state["automatic"]
        names = {s["row"]: s for s in state["students"]}
        person = lambda row: {"row": row, "name": (names.get(row) or {}).get("name", ""), "section": (names.get(row) or {}).get("section", "")}  # noqa: E731
        latest = state["latest"] or {}
        auto = json.loads(latest.get("summary_json") or "{}").get("automatic") if latest.get("actor") == "automatic" else None
        # MAKEUP CHANGES CERTIFY THEMSELVES: which of them were a makeup's change (`zero`, `left_out`; a row no longer kept is not named).
        kinds = (auto or {}).get("since_changes") or (auto or {}).get("changes") or {}
        took = [row for row in (auto or {}).get("since_instructor") or (auto or {}).get("rows") or [] if kinds.get(row) != "gone"]
        ahead = state.get("automatic_next") or {"papers": [], "changes": {}, "wait": None}
        mine = set(ahead["papers"]) | set(ahead["changes"])
        coming = {"papers": [person(row) for row in ahead["papers"]],
                  **{kind: [person(row) for row, k in sorted(ahead["changes"].items()) if k == kind] for kind in ("zero", "left_out")}}
        return {"enabled": found["enabled"], "waiting": [{**person(w["row"]), "reason": w.get("reason", "")} for w in found["waiting"]],
                "latest": {"at": latest["created_at"], "students": [{**person(row), **({"change": kinds[row]} if kinds.get(row) else {})} for row in took],
                           "markers": auto.get("markers")} if auto else None,
                # What Faculty certifies by itself once the work has settled, so the page does not ask for a Certify meanwhile. `rest`:
                # something else changed that waits for the instructor; `wait`: the plan-wide check Faculty waits for first.
                **({"next": {**coming, "rest": any(s["changed_since"] and s["row"] not in mine for s in state["students"]),
                             **({"wait": ahead["wait"]} if ahead.get("wait") else {})}} if mine else {}),
                # Why the whole plan waits for the instructor's Certify (its parts changed, a student left the exam), only when it does.
                **({"reason": found["reason"]} if found.get("reason") else {})}

    def _page(self, state, checks, history):
        plan, workflow = state["plan"], state["workflow"]
        written, sheets = state["gradescope"]["written"], state["gradescope"]["sheets"]
        students, questions = [], state["question_rows"]
        order = [q["question_id"] for q in questions]
        by_student = {}
        for rid, entry in state["decisions"].items():
            by_student.setdefault(_row(entry["section_id"], entry["enrollment_id"]), {})[entry["question_id"]] = entry
        version_of = {q["question_id"]: q["version"] for q in questions}
        gradescope_students, automatic_sends = {}, state.get("automatic_sends") or {}
        for s in sorted(state["students"], key=lambda s: (s["name"].casefold(), str(s["section"]), s["enrollment_id"])):
            parts = s["parts"]
            frq, bub = parts.get("frq") or {}, parts.get("bubble") or {}
            expected = state["expected"].get((s["section_id"], s["enrollment_id"]))
            mine = by_student.get(s["row"], {})
            scores = [(mine.get(qid) or {}).get("score") if (mine.get(qid) or {}).get("kind") else None
                      for qid in order if version_of[qid] in ("", s["exam_version"]) and (s["exam_version"] or not version_of[qid])]
            covered = written["students"].get(s["row"]) or {"paper": False, "grades": {"sent": 0, "changed": 0, "not_sent": 0}}
            sheet_sent = None if sheets is None else sheets["students"].get(s["row"])
            uncovered = bool(covered["grades"]["changed"] or covered["grades"]["not_sent"] or (frq.get("state") == "complete" and not covered["paper"])
                             or sheet_sent is False)
            gradescope_students[s["row"]] = {"uncovered": uncovered}
            # Faculty's own send of this student's late paper: waiting, or not sent and why (only when there is one: GRADESCOPE
            # "AUTOMATIC LATE-PAPER SEND"; the reason was recorded and never shown, 2026-10-09 review).
            note = self.gradescope.automatic_note(automatic_sends.get(s["row"]), uncovered) if automatic_sends.get(s["row"]) else None
            total = None
            if s["status"] in ("scored", "zero") and s.get("possible"):
                possible = rules.dec(s["possible"])
                total = {"earned": rules.text(s["earned"]), "possible": rules.text(possible),
                         "pct": rules.rounded(rules.dec(s["earned"]) * 100 / possible) if possible else None}
            students.append({"row": s["row"], "name": s["name"], "section": s["section"], "exam_version": s["exam_version"],
                             "participant_version": s["participant_version"], "expectation": s["expectation"], "collection": s["collection"],
                             "status": s["view"], "reason": s["detail"],
                             "frq": {"state": frq.get("state", "none"), "earned": frq.get("earned"), "possible": frq.get("possible"),
                                     "final": sum(1 for qid in (expected or []) if (mine.get(qid) or {}).get("kind")), "expected": len(expected or [])},
                             "bubble": {"state": bub.get("state", "none"), "earned": bub.get("earned"), "possible": bub.get("possible"),
                                        "sheet_status": (s.get("sheet") or {}).get("status")},
                             "total": total, "scores": scores, "flagged": s["flagged"], "settled": s["settled"], "zero_questions": s["zero_questions"],
                             "dr": [{k: b.get(k) for k in ("date", "start_time", "end_time", "campus", "exam_type")} for b in s["dr"]],
                             "gradescope": {"paper": covered["paper"], "grades": covered["grades"], "sheet": sheet_sent, "uncovered": uncovered,
                                            **({"automatic": note} if note else {})},
                             "certified": s["certified"], "changed_since": s["changed_since"]})
        view = state["bubble"] or {}
        key = view.get("key") or {}
        bubble_questions = {}
        for version in key.get("versions") or []:
            per = (view.get("stats") or {}).get(version["letter"]) or {}
            rows = []
            for q in version.get("questions") or []:
                entry = per.get(str(q["number"])) or {}
                count = entry.get("sheets", 0)
                rows.append({"number": q["number"], "answer": q.get("answer"), "sheets": count,
                             "correct_pct": rules.rounded(Decimal(entry.get("full", 0)) * 100 / count) if count else None,
                             "common_wrong": entry.get("common") if entry.get("common") and entry.get("common") != q.get("answer") else None})
            bubble_questions[version["letter"]] = rows
        counts = {**rules.page_counts(state["students"], gradescope_students), "dropped_left_out": state.get("dropped_left_out", 0)}
        # While Faculty's own Gradescope work after a certification is under way the page keeps following it (GRADESCOPE "AFTER CERTIFYING").
        live = bool(workflow.get("pending")) or state["gradescope"]["live"] or any(
            run["state"] == "working" or run.get("publishing") for run in state.get("gradescope_runs") or [])
        possible = {name: rules.text(value) for name, value in state["maxima"].items() if name in state["totals"]["parts"]}
        possible["total"] = sources.total(state["maxima"], state["totals"]["parts"])
        versions = sorted({q["version"] for q in questions if q["version"]} | {v["letter"] for v in key.get("versions") or []})
        return {"plan_id": plan["id"], "title": plan["title"], "event_id": plan["event_id"], "term_id": plan["term_id"],
                "parts": state["totals"]["parts"], "possible": possible, "versions": versions,
                "sections": [{"section_id": row[0], "number": row[1]} for row in sorted({(p["section_id"], p["section_number"]) for p in state["participants"]},
                                                                                      key=lambda pair: str(pair[1]))],
                "certification": workflow["certification"], "history": history, "stats": state["stats"], "questions": questions,
                "automatic": self._automatic(state),
                "bubble_questions": bubble_questions, "students": students, "counts": counts, "checks": checks, **rules.counts(checks),
                "participation": {k: state["participation"][k] for k in ("enabled", "bubble_key")},
                "gradescope": {"configured": state["gradescope"]["configured"], "live": state["gradescope"]["live"],
                               "written": {"papers": {"total": written["totals"]["papers"]["students"], "sent": written["totals"]["papers"]["sent"]},
                                           "grades": {k: written["totals"][k] for k in ("final", "sent", "changed", "not_sent")}},
                               "sheets": (sheets or {}).get("totals"),
                               # A version a late paper went to by itself that is not published although its set is (PUBLISHING GRADES).
                               **({"unpublished_versions": state["unpublished_versions"]} if state.get("unpublished_versions") else {}),
                               # What Faculty does on Gradescope by itself after a certification (GRADESCOPE "AFTER CERTIFYING").
                               **({"after": state["gradescope_runs"]} if state.get("gradescope_runs") else {})},
                "live": live}

    @staticmethod
    def _brief(row):
        summary = json.loads(row.get("summary_json") or "{}")
        book = json.loads(row.get("gradebook_json") or "{}")
        return {"id": row["id"], "created_at": row["created_at"], "supersedes_id": row["supersedes_id"], "rule": row["rule"],
                # Certify just this change (REGRADES, 2026-10-07): the history says which one grade it changed.
                **({"amends": {k: summary["amends"].get(k) for k in ("from", "to", "question_id")}} if row.get("amends_id") and summary.get("amends") else {}),
                # LATE PAPERS CERTIFY THEMSELVES: who certified it (Faculty by itself, or the instructor) and how many late papers it took.
                "by": "faculty" if row.get("actor") == "automatic" else "you",
                **({"automatic": {"students": len(summary["automatic"].get("rows") or []), "markers": summary["automatic"].get("markers")}}
                   if summary.get("automatic") else {}),
                **{k: summary.get(k, 0) for k in ("students", "scored", "zero", "excused", "left_out", "left_out_dr")},
                **({"left_out_makeup": summary["left_out_makeup"]} if summary.get("left_out_makeup") else {}),
                "gradebook": {k: book.get(k) for k in ("item_id", "title", "written", "kept", "created_column")} if book else None}

    # ----- drill-downs --------------------------------------------------------------------------------------------------------
    def question(self, aid, qid):
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            question = self.frq.question(db, aid, qid)
            event = self.frq.exams._event(db, plan["event_id"])
            participants = self.frq.exams._participants(db, event)
            self.frq.exams._collection(db, event, participants)
            responses = self.frq._responses(db, plan, event, participants, qid)
            only = [r["id"] for r in responses]  # this question's responses: the reads below look up nothing else
            decisions = participation.counted_entries(db, aid, progress.decision_rows(db, aid, responses, only=only))
            projection = self._projection(db, aid, question_id=qid)
            states = self._coverage(db, aid, responses, decisions, only=only)
            receipts = self._adjustment_receipts(db, aid, qid)
        names = {(p["section_id"], p["enrollment_id"]): p for p in participants}
        # The grade's own adjustments, then the participation line it counts with (GRADING "GRADED FOR PARTICIPATION").
        adjustments = {rid: grade_adjustments(decisions.get(rid), projection.get(rid) or {})
                       + participation.line(decisions.get(rid)) for rid in decisions}
        carried = lambda ids, line: sum(1 for rid in ids if any(same_line(a, line) for a in adjustments.get(rid) or []))
        rules_ = (question["rubric"] or {}).get("rules") or {}
        items = rules_.get("items") or []
        rubric = []
        for position, item in enumerate(items, 1):
            try:
                number = json.loads(item.get("original_source_text") or "{}").get("rubric_number")
            except (ValueError, TypeError, AttributeError):
                number = None
            rubric.append({"id": item["id"], "number": str(number) if number else str(position), "description": item.get("description", ""),
                           "points": item.get("points")})
        rows, finals, selections = [], [], []
        for r in responses:
            entry, found = decisions.get(r["id"]) or {}, projection.get(r["id"]) or {}
            acceptance = found.get("acceptance") or {}
            person = names.get((r["section_id"], r["enrollment_id"])) or {}
            final = bool(entry.get("kind"))
            if final:
                finals.append(entry["score"])
                selections.append(entry["selected"])
            state = states.get(r["id"])
            rows.append({"response_id": r["id"], "row": _row(r["section_id"], r["enrollment_id"]), "name": person.get("name", ""),
                         "section": person.get("section_number", ""), "score": entry.get("score") if final else None, "state": r["state"],
                         "decided": {"kind": "instructor" if entry.get("kind") == "review" else entry.get("kind") or "none",
                                     "status": found.get("review_status") if entry.get("kind") == "review" else "automatic" if final else r["state"],
                                     "basis": rules.basis(acceptance.get("basis"), found) if entry.get("kind") == "automatic" else None},
                         "flagged": bool(found.get("flags")) and not found.get("settled"), "settled": bool(found.get("settled")), "flags": found.get("flags") or [],
                         "overridden": rules.overridden(found.get("review_status"), *self._selections(found)), "selected": entry.get("selected") or [],
                         "gradescope": state or "none", "answer_group": found.get("answer_group"),
                         "adjustments": [{"points": rules.text(a["points"]), "reason": a["reason"]} for a in adjustments.get(r["id"]) or []] if final else []})
        rows.sort(key=lambda row: (row["name"].casefold(), str(row["section"])))
        summary = rules.stats([(score, question["content"].get("max_points")) for score in finals])
        summary["histogram"] = rules.score_histogram(finals)
        return {"question": {"question_id": qid, "title": question["content"].get("title") or question["question_key"],
                             "printed_number": question["content"].get("printed_number"), "version": question["exam_version"] or "",
                             "max": rules.text(question["content"].get("max_points")), "rubric": rubric, "stats": summary},
                "lines": rules.line_frequencies(selections, items), "responses": rows,
                "batches": self.adjustment_batches(receipts, {p["section_id"]: p["section_number"] for p in participants}, carried)}

    def response_student(self, aid, rid):
        """Who wrote one response, for the review desk's Show student (requirement, 2026-09-29). The Review step stays anonymous: the name is fetched only when the instructor asks for this one response, and it
        carries the name and the section number from the verified roster, never a UIN or an enrollment ID."""
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            row = db.execute("SELECT section_id,enrollment_id FROM frq_responses WHERE id=? AND assignment_id=?", (rid, aid)).fetchone()
            if not row:
                raise NotFound("That response is not part of this assignment.")
            key = (row["section_id"], row["enrollment_id"])
            found = self._names(db, self.frq.exams._event(db, plan["event_id"]), [key])[key]
        return {"name": found["name"], "section": found["section"]}

    def student(self, aid, section_id, enrollment_id):
        key = (section_id, enrollment_id)
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            event = self.frq.exams._event(db, plan["event_id"])
            participants = self.frq.exams._participants(db, event)
            person = next((p for p in participants if (p["section_id"], p["enrollment_id"]) == key), None)
            if not person:
                raise NotFound("This student is not a participant of this exam.")
            paper = self.frq.exams._participant_paper(db, event, section_id, enrollment_id)
            person["collection"] = paper["collection"]
            found = self.frq.paper_version(db, plan, person, {})
            responses = self.frq._responses(db, plan, event, [person], student=key)
            only = [r["id"] for r in responses]  # this student's responses: the reads below look up nothing else
            decisions = participation.counted_entries(db, aid, progress.decision_rows(db, aid, responses, only=only))
            projection = self._projection(db, aid, student=key)
            signals, reasons = {}, {}
            decided = [p["result_id"] for p in projection.values() if p["result_id"]]
            if decided:  # the decided results' flags in full, for this one student's questions (at most a few dozen rows)
                for r in db.execute("SELECT id,signals_json,reasons_json FROM frq_results WHERE id IN (%s)" % ",".join("?" * len(decided)), decided):
                    signals[r["id"]], reasons[r["id"]] = json.loads(r["signals_json"] or "{}"), json.loads(r["reasons_json"] or "[]")
            pins = [row[0] for row in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY rowid", (aid,))]
            questions = [self.frq.question(db, aid, qid) for qid in pins]
            document = self.frq.manual._paper_version(db, event["id"], person)
            coverage = self._coverage(db, aid, responses, decisions, only=only)
            bookings = [b for b in progress.dr_bookings(db, plan) if (b["section_id"], b["enrollment_id"]) == key]
            latest = progress.latest_certification(db, aid)
            certified = db.execute("SELECT * FROM frq_certification_students WHERE certification_id=? AND section_id=? AND enrollment_id=?",
                                   (latest["id"], section_id, enrollment_id)).fetchone() if latest else None
            view = self._student_sheets(db, plan, enrollment_id)
        letter = found.get("version") or ""
        by_question = {r["question_id"]: r for r in responses}
        shown = []
        for q in questions:
            mine = by_question.get(q["question_id"])
            relevant = mine or (q["assignment_version_id"] == document and q["exam_version"] in ("", letter))
            if not relevant:
                continue
            entry = decisions.get(mine["id"]) if mine else {}
            found_ = projection.get(mine["id"]) if mine else {}
            acceptance = (found_ or {}).get("acceptance") or {}
            sig = signals.get((found_ or {}).get("result_id")) or {}
            risk = sig.get("risk") or {}
            missing = None
            if not mine:
                missing = paper["collection"]["issues"][0] if paper["collection"].get("issues") else \
                    "No paper was received" if paper["collection"]["status"] in ("no_scan", "absent", "excused") else "Not graded yet"
            elif not (entry or {}).get("kind"):
                missing = {"stale": "To grade again after a rubric change", "pending": "Not graded yet", "draft": "A draft review takes it out of final",
                           "unreadable": "Marked unreadable: record a grade", "rejected": "Rejected: record a grade"}.get(mine["state"], "Waiting for review")
            shown.append({"question_id": q["question_id"], "title": q["content"].get("title") or q["question_key"], "printed_number": q["content"].get("printed_number"),
                          "version": q["exam_version"] or "", "max": rules.text(q["content"].get("max_points")), "response_id": mine["id"] if mine else None,
                          "state": mine["state"] if mine else "none", "score": (entry or {}).get("score") if (entry or {}).get("kind") else None,
                          "decided": {"kind": "instructor" if (entry or {}).get("kind") == "review" else (entry or {}).get("kind") or "none",
                                      "status": (found_ or {}).get("review_status") if (entry or {}).get("kind") == "review" else "automatic" if (entry or {}).get("kind") else None,
                                      "basis": rules.basis(acceptance.get("basis"), found_ or {}) if (entry or {}).get("kind") == "automatic" else None,
                                      "actor": (found_ or {}).get("review_actor") if (entry or {}).get("kind") == "review" else "acceptance-policy" if (entry or {}).get("kind") else None,
                                      "at": (found_ or {}).get("review_at") if (entry or {}).get("kind") == "review" else None},
                          "flags": {"names": (found_ or {}).get("flags") or [], "settled": bool((found_ or {}).get("settled")), "original_state": (found_ or {}).get("state"),
                                    "base_state": (found_ or {}).get("base_state"), "reasons": reasons.get((found_ or {}).get("result_id")) or [],
                                    "risk_blocks": risk.get("blocks") or [], "risk_waived": risk.get("waived") or {}, "risk_individual": bool(risk.get("individual")),
                                    "arbiter": {k: (sig.get("arbiter") or {}).get(k) for k in ("outcome", "reason", "flags")} if sig.get("arbiter") else None,
                                    "flag_review": {k: (sig.get("flag_review") or {}).get(k) for k in ("outcome", "reason", "flags")} if sig.get("flag_review") else None,
                                    "committee": {k: (sig.get("committee") or {}).get(k) for k in ("status", "reason", "review_reasons")} if sig.get("committee") else None},
                          "later_proposal": bool(mine and (entry or {}).get("kind") == "review" and mine["current_result_id"] != (entry or {}).get("result_id")),
                          "missing_reason": missing, "gradescope": coverage.get(mine["id"], "none") if mine else "none",
                          **({"participation": rules.text((entry or {})["participation"])} if (entry or {}).get("participation") else {})})
        shown.sort(key=lambda q: (q["version"], self._number(q["printed_number"]), q["title"]))
        sheet = None
        if view:
            mine = [s for s in view["sheets"] if s.get("enrollment_id") == enrollment_id and str(s.get("section")) == str(person["section_number"])]
            if mine:
                s = mine[0]
                sheet = {"sheet_id": s["sheet_id"], "status": s["status"], "version": s.get("version") or s.get("marked_version") or "", "earned": s.get("earned"),
                         "possible": s.get("possible"), "reason": s.get("reason") or "", "identified_by": s.get("identified_by"), "wrong": s.get("wrong") or [],
                         "job_id": s.get("job_id"), "page": s.get("page"), "href": f"#grading/{aid}?tab=bubble", "several": len(mine) > 1}
        return {"student": {"row": _row(*key), "name": person["name"], "section": person["section_number"], "exam_version": letter,
                            "participant_version": person["version"], "expectation": person["expectation"], "reason": person.get("reason") or "",
                            "collection": paper["collection"]["status"], "collection_issues": paper["collection"].get("issues") or [],
                            "paper_version": {k: found.get(k) for k in ("status", "version", "reason")}},
                "parts": {"frq": self._frq_summary(shown), "bubble": sheet}, "questions": shown,
                "dr": [{k: b.get(k) for k in ("date", "start_time", "end_time", "campus", "exam_type")} for b in bookings],
                "certified": {"status": certified["status"], "reason": certified["reason"], "earned": certified["earned"], "possible": certified["possible"],
                              "exam_version": certified["exam_version"], "questions": (json.loads(certified["parts_json"]).get("frq") or {}).get("questions") or {},
                              "certification_id": latest["id"], "created_at": latest["created_at"]} if certified else None}

    @staticmethod
    def _coverage(db, aid, responses, decisions, only=None):
        """{response_id: sent|changed|not_sent} of final responses from Gradescope receipts, `coverage_detail`'s rule: a changed
        decision whose accepted receipt checked exactly the same items, adjustment and score is `sent` (critique C7). `only`: the IDs
        of `responses` when they are a few of the plan's (a drill-down), to read those alone."""
        from .gradescope_sync import GradescopeSender
        states = GradescopeSender._grade_coverage(db, aid, responses, only=only)
        changed = [rid for rid, state in states.items() if state == "changed"]
        if changed:
            comments = GradescopeSender.expected_comments(db, aid)
            receipts = {}
            for r in db.execute(f"""SELECT response_id,items_json,adjustment,expected_score FROM frq_gradescope_grades WHERE assignment_id=? AND status='accepted'
                    {progress.ONLY.format('response_id') if only is not None else ''} ORDER BY created_at,rowid""", (aid, *progress.only_args(only))):
                receipts[r["response_id"]] = r
            for rid in changed:
                if GradescopeSender._same_content(receipts.get(rid), decisions.get(rid), comments.get(rid)):
                    states[rid] = "sent"
        return states

    @staticmethod
    def _frq_summary(shown):
        """The free-response part of one student's detail: how many of their questions are final, and the total once all are."""
        final = [q for q in shown if q["score"] is not None]
        complete = bool(shown) and len(final) == len(shown)
        return {"final": len(final), "expected": len(shown),
                "earned": rules.text(sum((rules.dec(q["score"]) for q in final), Decimal(0))) if complete else None,
                "possible": rules.text(sum((rules.dec(q["max"]) or Decimal(0) for q in shown), Decimal(0))) if shown else None}

    def _student_sheets(self, db, plan, enrollment_id):
        """This student's sheets of the plan's cleared scans, scored by the Bubble sheets page's own rule (`_bubble_scores`) with how
        each was identified (review, uin, confidence), or None when there is no bubble reader. Each sheet's present value is read
        once and only this student's are scored (review P2: the drawer read and scored every sheet of the plan twice)."""
        reader = getattr(self.frq.inbox, "bubble_sheets", None)
        if not reader:
            return None
        loaded, memo = [], {}  # memo: each exam's booklet versions read once for every run
        for run in self.frq._bubble_runs(db, plan):
            if run["instructor_review_required"]:
                continue  # scans still in the Inbox join grading after their review
            items = [item for item in reader._sheets(db, run["id"], memo) if item["current"]["enrollment_id"] == enrollment_id]
            if items:
                loaded.append((run, items, reader._students(db, run)))
        if not loaded:
            return {"sheets": []}
        return self.frq._bubble_scores(self.frq.bubble_key(db, plan["id"]), loaded, self.frq.version_letters(db, plan))

    def search(self, aid, data):
        """Students whose UIN is closest to the digits typed (4 to 12), by name and section only: the digits come in the POST body
        and go nowhere else (no URL, log, event or error text), and no UIN is ever answered."""
        if not isinstance(data, dict) or set(data) - {"digits"}:
            raise ValueError("Send the digits to search for.")
        typed = data.get("digits")
        if not isinstance(typed, str) or not DIGITS.fullmatch(typed):
            raise ValueError("Type 4 to 12 digits to search by UIN.")
        from bubble_sheets import matching
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, aid)
            event = self.frq.exams._event(db, plan["event_id"])
            participants = self.frq.exams._participants(db, event)
            uins = dict(db.execute("""SELECT e.id,e.uin FROM student_enrollments e JOIN exam_participants p ON p.enrollment_id=e.id WHERE p.event_id=?""",
                                   (event["id"],)).fetchall())
        students = [{"uin": uins.get(p["enrollment_id"]) or "", "enrollment_id": p["enrollment_id"], "section_id": p["section_id"],
                     "section_number": p["section_number"], "name": p["name"]} for p in participants if uins.get(p["enrollment_id"])]
        return {"matches": [{"row": _row(m["section_id"], m["enrollment_id"]), "name": m["name"], "section": m["section_number"],
                             "similarity": m["similarity"]} for m in matching.uin_search(typed, students)]}

    # ----- the exact state: preview and certify -------------------------------------------------------------------------------
    def _evaluate(self, aid, *, replace=(), column=None, category_id=None, gradebook=True):
        """The certify preview's exact state (writes nothing). The caller holds the lifecycle lock; no transaction is open.
        `gradebook=False` (LATE PAPERS CERTIFY THEMSELVES, which plans the gradebook for a few rows itself): the grades, checks and
        markers only, with the gradebook's context; no column plans, no gradebook checks and no fingerprint."""
        with connect(self.path) as db:
            state = self._read(db, aid, exact=True)
            plan = state["plan"]
            state["out_of_range"] = self._out_of_range(state)
            state["records"] = self._records(db, state)
            state["snapshot_sha256"] = rules.snapshot_digest(state["records"])
            names = {s["row"]: {"name": s["name"], "section": s["section"]} for s in state["students"]}
            state["changes"] = rules.changes(list(state["certified_rows"].values()), state["records"], names) if state["latest"] else []
            checks = self._checks(db, state, exact=True)
            state["markers"] = {"decisions": digest(progress.decision_state(db, aid, state["responses"], state["decisions"])),
                                "bubble": progress.bubble_marker(db, plan),
                                "participants": progress.participants_marker(db, plan, state["collection"]["students"], state["totals"]["parts"])}
        context = self.gradebook._context(plan["term_id"])  # the verified roster payload, outside any transaction
        with connect(self.path) as db:
            exam_sections = [r[0] for r in db.execute("SELECT section_id FROM exam_event_sections WHERE event_id=?", (plan["event_id"],))]
            if not gradebook:
                state.update(context=context, exam_sections=exam_sections, book=None, option=None, checks=checks)
                return state
            book = self.gradebook.certification_options(db, context, assignment_id=plan["source_assignment_id"], plan_id=plan["id"], event_id=plan["event_id"],
                                                        title=plan["title"], records=state["records"], certified_parts=state["totals"]["parts"],
                                                        exam_sections=exam_sections, replace=replace, selected=None, category_id=category_id)
        state["context"], state["exam_sections"], state["book"] = context, exam_sections, book
        option = self._option(book, column)
        if replace and option:
            with connect(self.path) as db:
                book = self.gradebook.certification_options(db, context, assignment_id=plan["source_assignment_id"], plan_id=plan["id"], event_id=plan["event_id"],
                                                            title=plan["title"], records=state["records"], certified_parts=state["totals"]["parts"],
                                                            exam_sections=exam_sections, replace=replace, selected=option["key"], category_id=category_id)
            state["book"], option = book, self._option(book, column)
        state["option"] = option
        book_checks = self._book_checks(state, option)
        state["checks"] = sorted(checks + book_checks, key=lambda c: rules.LEVEL_ORDER[c["level"]])
        written = state["gradescope"]["written"]
        sheets = state["gradescope"]["sheets"]
        state["gradescope_members"] = {"written": rules.members_digest(rules.gradescope_members("written", written)),
                                       "sheets": rules.members_digest(rules.gradescope_members("sheets", sheets)) if sheets else None}
        state["fingerprint"] = rules.fingerprint(parts=state["totals"]["parts"], snapshot_sha256=state["snapshot_sha256"], checks=checks,
                                                 gradescope=state["gradescope_members"],
                                                 gradebook={"revision": book["revision"], "options": {o["key"]: o["plan"]["fingerprint"] for o in book["options"]}})
        return state

    @staticmethod
    def _option(book, column):
        """The chosen option: the one the request names (`{action, item_id?, parts?}`), else the first (the default)."""
        if not book["options"]:
            return None
        if not column:
            return book["options"][0]
        for option in book["options"]:
            given = option["column"]
            if given.get("action") == column.get("action") and given.get("item_id") == column.get("item_id") and \
                    (given.get("parts") or "certified") == (column.get("parts") or "certified"):
                return option
        raise Conflict("That gradebook column is not one this certification can record in. Check again.")

    def _book_checks(self, state, option):
        """The gradebook's part of the checks, for the chosen column (kept out of the fingerprint's check list: every column the
        certification may record in has its own plan fingerprinted, so a choice in the dialog needs no new preview)."""
        if not option:
            return []
        found, item = [], option["item"]
        action, parts = option["column"]["action"], ", ".join(rules.LABELS[p] for p in item["link"]["parts"])
        titles = {"create": f"Create the gradebook column “{item['title']}”", "use": f"Record in the column “{item['title']}”",
                  "link": f"Link the column “{item['title']}” to this assignment", "parts": f"Change what “{item['title']}” records",
                  "ok": f"Record in “{item['title']}”"}
        if option["writes_setup"] or option["column"].get("parts") == "keep":
            before = next((i for i in state["book"]["setup"].get("items", []) if i["id"] == item["id"]), item)
            had = ", ".join(rules.LABELS[p] for p in (before.get("link") or {}).get("parts", []))
            base = f"{common.points_text(item['points'])} points · {parts}" + (" · the exam's sections only" if item.get("sections") else "")
            detail = {"create": base,
                      "use": f"It will record this assignment's certified grades ({parts}).",
                      "link": f"It will record this assignment's certified grades ({parts}).",
                      "parts": f"It records {had} now; it will record {parts}.",
                      "ok": f"It keeps its parts ({parts}): only the certified values of those parts are recorded."}[action]
            extra = {}
            if action == "create":
                # A new column's category (rest-shell-1): named here, and said when it would not count toward Overall. The category
                # is in no fingerprint or digest, so the dialog chooses it without a new preview; each category's words come along.
                names = {c["id"]: c["name"] for c in state["book"].get("categories") or [] if c["id"]}
                category = item.get("category_id") if item.get("category_id") in names else None
                if category is None and state["book"].get("mode") == "weighted":
                    detail = f"{base}. Uncategorized: it will not count toward Overall until it has a category."
                elif names:  # a gradebook without categories has none to name
                    detail = f"{base} · {names.get(category, 'Uncategorized')}"
                if names:
                    extra["category_details"] = {**{cid: f"{base} · {name}" for cid, name in names.items()}, "": f"{base} · Uncategorized"}
            # The other options are the preview's own `gradebook.options`, not repeated inside every option's check (a term with 79
            # unlinked columns sent 6,400 copies); the check's members, and so its digest, never held them.
            found.append(rules.check("gradebook_column", "confirm", titles[action], detail, count=1,
                                     members=[option["key"], item["title"], common.points_text(item["points"]), ",".join(item["link"]["parts"]), ",".join(item.get("sections") or [])],
                                     **extra))
        if option["kept"]:
            found.append(rules.check("gradebook_kept", "confirm", f"{rules.plural(len(option['kept']), 'value')} you typed in the gradebook stay",
                                     "Tick Replace for a student to record the certified value instead; unticked rows stay as you typed them.",
                                     rows=[{"row": r["row"], "name": r["name"], "section": r["section"],
                                            "detail": f"typed {r['current'].get('points') or r['current'].get('status')} · certified {r['proposed'].get('points') or r['proposed'].get('status')}"}
                                           for r in option["kept"]],
                                     members=[f"{r['row']}|{(r['current'] or {}).get('id')}" for r in option["kept"]]))
        if option["outside"]:
            names = {s["row"]: s for s in state["students"]}
            found.append(rules.check("gradebook_sections", "confirm", f"{rules.plural(len(option['outside']), 'student')} outside the column's sections",
                                     "The column does not apply to their section, so their certified grade is not recorded there.",
                                     rows=[rules.person(names.get(row) or {"row": row}, "Section not in the column") for row in option["outside"]], members=option["outside"]))
        if option["imported"]:
            found.append(rules.check("gradebook_imported_replaced", "info", f"{rules.plural(len(option['imported']), 'imported value')} replaced",
                                     "Values an import wrote (a Gradescope CSV, say) are replaced by the certified ones.",
                                     rows=[{"row": r["row"], "name": r["name"], "section": r["section"],
                                            "detail": f"{r['current'].get('points') or r['current'].get('status')} → {r['proposed'].get('points') or r['proposed'].get('status')}"}
                                           for r in option["imported"]]))
        if option["cleared"]:
            found.append(rules.check("gradebook_cleared", "info", f"{rules.plural(len(option['cleared']), 'earlier certified value')} cleared",
                                     "These students are left out now (a DR exam still ahead); the value an earlier certification recorded no longer stands.",
                                     rows=[{"row": r["row"], "name": r["name"], "section": r["section"], "detail": "cleared"} for r in option["cleared"]]))
        return found

    def _out_of_range(self, state):
        names = {(s["section_id"], s["enrollment_id"]): s for s in state["students"]}
        found = []
        for decision in state["final"][1]:
            row, final = decision["row"], decision["final"]
            rubric = ((decision.get("question") or {}).get("rubric") or {}).get("rules") or {}
            score = rules.dec((final.get("score") or {}).get("score"))
            floor, ceiling = rules.dec(rubric.get("floor")), rules.dec(rubric.get("ceiling"))
            if score is None or (floor is not None and score < floor) or (ceiling is not None and score > ceiling):
                who = names.get((row["section_id"], row["enrollment_id"])) or {}
                found.append({"row": _row(row["section_id"], row["enrollment_id"]), "name": who.get("name", ""), "section": who.get("section", ""),
                              "detail": f"{(decision.get('question') or {}).get('content', {}).get('title', '')}: {rules.text(score) if score is not None else 'no score'}",
                              "response_id": row["id"]})
        for s in state["students"]:
            if s["status"] in ("scored", "zero") and rules.dec(s["earned"]) is not None and rules.dec(s["possible"]) is not None and rules.dec(s["earned"]) > rules.dec(s["possible"]):
                found.append({"row": s["row"], "name": s["name"], "section": s["section"], "detail": f"{s['earned']} of {s['possible']}", "response_id": None})
        return found

    def _records(self, db, state):
        """The snapshot: one certified row per participant a block does not name (none while anything blocks)."""
        by_student = {}
        for decision in state["final"][1]:
            row, final = decision["row"], decision["final"]
            by_student.setdefault((row["section_id"], row["enrollment_id"]), []).append({
                "question_id": row["question_id"], "score": final["score"]["score"], "max": final["score"].get("max_score"), "kind": decision["kind"],
                "decision_id": final["id"], "result_id": row["result_id"], "response_id": row["id"]})
        key_revision = ((state["bubble"] or {}).get("key") or {}).get("revision")
        reviews = {}
        sheet_ids = [s["sheet"]["sheet_id"] for s in state["students"] if s.get("sheet") and s["sheet"].get("sheet_id")]
        if sheet_ids:
            for row in db.execute("SELECT sheet_id,MAX(id) FROM bubble_reviews WHERE sheet_id IN (%s) GROUP BY sheet_id" % ",".join("?" * len(sheet_ids)), sheet_ids):
                reviews[row[0]] = row[1]
        records = []
        for s in state["students"]:
            if s["status"] == "blocked":
                continue
            parts = {}
            if s["status"] in ("scored", "zero"):
                for name in state["totals"]["parts"]:
                    part = s["parts"].get(name) or {}
                    if s["status"] == "scored" and part.get("state") == "complete":
                        if name == "frq":
                            expected = set(state["expected"].get((s["section_id"], s["enrollment_id"])) or [])
                            chosen = [d for d in by_student.get((s["section_id"], s["enrollment_id"]), []) if d["question_id"] in expected]
                            parts[name] = rules.frq_part("complete", chosen, part["earned"], part["possible"])
                        elif name == "bubble":
                            sheet = dict(s.get("sheet") or {})
                            sheet["review_id"] = reviews.get(sheet.get("sheet_id"))
                            parts[name] = rules.bubble_part("complete", sheet, key_revision, part["earned"], part["possible"])
                        else:
                            parts[name] = {"state": "complete", "earned": rules.text(part["earned"]), "possible": rules.text(part["possible"])}
                    else:
                        why = {"frq": "no paper", "bubble": "no bubble sheet", "exams": "no paper"}[name]
                        parts[name] = rules.zero_part(state["maxima"].get(name, Decimal(0)), why)
            records.append(rules.record(s, s, parts))
        return records

    def preview(self, aid, data):
        """The certify preview: everything checked exactly, the fingerprint a certify names. Writes nothing (seconds on a big exam:
        `final_decisions` once, then the gradebook's totals on it)."""
        data = data if isinstance(data, dict) else {}
        if set(data) - {"replace", "gradebook"}:
            raise ValueError("The preview takes the rows to replace and the gradebook column.")
        book = data.get("gradebook") or {}
        if not isinstance(book, dict) or set(book) - {"replace", "column", "category_id"}:
            raise ValueError("The gradebook choices are the rows to replace, the column and the category.")
        replace = data.get("replace", book.get("replace")) or []
        if not isinstance(replace, list):
            raise ValueError("Name the rows to replace.")
        for row in replace:
            common.split_row(row)
        column = book.get("column") or None
        if column is not None and (not isinstance(column, dict) or set(column) - {"action", "parts", "item_id"}
                                   or not all(value is None or isinstance(value, str) for value in column.values())):
            raise ValueError("Choose the gradebook column.")
        if book.get("category_id") is not None and not isinstance(book["category_id"], str):
            raise ValueError("Choose a category of this term's gradebook.")
        with self.rosters.lock():
            state = self._evaluate(aid, replace=set(replace), column=column, category_id=book.get("category_id"))
            with connect(self.path) as db:
                self._absences_beside_zeros(db, state)
        return self._preview_view(state)

    @staticmethod
    def _absences_beside_zeros(db, state):
        """Display only (requirement, 2026-10-02). Each listed row of the students about to receive 0 (`zero_no_work`) gets `absences`: every absence recorded for
        that SECTION MEMBERSHIP in the plan's term, read through the owner's own `absences.service.standing` (catalog only), each
        {first_date, last_date, dates, status} and, when the assessment has a date for the student and the absence is not
        withdrawn (a withdrawn absence claims nothing), `covers` (that date falls within the absence). That date is the student's
        own sitting's when the Exams page names one with a date (a makeup, say); else the earliest exam date of the assignment's
        seating charts naming their section, leaving out a DR exam's chart and a chart tied to a makeup or DR sitting; else the
        event's first main sitting with a date (EXAMS "DATE GIVEN", `exam_workflow.given_dates`). Only the preview
        reads this; rows are in no digest (the check's members are `row|reason`), so the fingerprint and every digest stay what
        they were."""
        check = next((c for c in state["checks"] if c["id"] == "zero_no_work"), None)
        keys = {s["row"]: (s["section_id"], s["enrollment_id"]) for s in state["students"]}
        rows = [r for r in (check or {}).get("rows") or [] if r.get("row") in keys]
        if not rows:
            return
        from absences.service import standing
        plan = state["plan"]
        found = {}
        for absence in standing(db, plan["term_id"], [keys[r["row"]] for r in rows]):
            found.setdefault(_row(absence["section_id"], absence["enrollment_id"]), []).append(absence)
        # The day it was given to each student: EXAMS "DATE GIVEN" (exam_workflow/given_dates.py), the order this dialog first used.
        dates = given_dates(db, plan["event_id"], plan["term_id"], plan["source_assignment_id"], [keys[r["row"]] for r in rows])
        for r in rows:
            given = dates.get(keys[r["row"]])
            r["absences"] = [{"first_date": a["first_date"], "last_date": a["last_date"], "dates": a["dates"], "status": a["status"],
                              **({"covers": a["first_date"] <= given <= a["last_date"]} if given and a["status"] != "withdrawn" else {})}
                             for a in found.get(r["row"], [])]

    def _preview_view(self, state):
        summary = rules.summary(state["records"])
        confirm = [c for c in state["checks"] if c["level"] == "confirm"]
        option, book = state["option"], state["book"]
        return {"fingerprint": state["fingerprint"], "rule": rules.RULE, "parts": state["totals"]["parts"],
                "snapshot": {**{k: summary[k] for k in ("students", "scored", "zero", "excused", "left_out", "left_out_dr", "missing_part", "stats")},
                             **({"left_out_makeup": summary["left_out_makeup"]} if summary["left_out_makeup"] else {})},
                "checks": state["checks"], **rules.counts(state["checks"]), "confirm": confirm,
                "gradescope": {"configured": state["gradescope"]["configured"], "written": state["gradescope"]["written"]["totals"],
                               "sheets": (state["gradescope"]["sheets"] or {}).get("totals"),
                               # GRADESCOPE "AFTER CERTIFYING": the dialog offers "Send to Gradescope and publish the grades".
                               **({"after": True} if self._after_offered(state["totals"]["parts"]) else {})},
                "gradebook": {"term_id": state["plan"]["term_id"], "revision": book["revision"],
                              "options": [{"key": o["key"], "column": o["column"], "item": self._item(o["item"]), "counts": o["plan"]["counts"],
                                           "kept": len(o["kept"]), "outside": len(o["outside"]), "checks": self._book_checks(state, o)}
                                          for o in book["options"]],
                              "selected": option["key"] if option else None, "item": self._item(option["item"]) if option else None,
                              "column": option["column"] if option else None, "counts": option["plan"]["counts"] if option else {},
                              "rows": [{k: r[k] for k in ("row", "name", "section", "action", "proposed", "current", "reason")} for r in option["plan"]["rows"][:rules.ROWS]] if option else [],
                              "kept": option["kept"] if option else [], "imported": option["imported"] if option else [],
                              "cleared": option["cleared"] if option else [], "warnings": option["plan"]["warnings"] if option else [],
                              # Additive (rest-shell-1): what the Certify dialog's Category choice for a new column offers and proposes.
                              "mode": book["mode"], "categories": book["categories"], "proposed_category_id": book["proposed_category_id"]},
                "changes": state["changes"], "latest": state["latest"]["id"] if state["latest"] else None}

    @staticmethod
    def _item(item):
        return {"id": None if item["id"].startswith("new:") else item["id"], "title": item["title"], "points": item["points"],
                "parts": list(item["link"]["parts"]), "sections": list(item.get("sections") or []), "category_id": item.get("category_id")}

    # ----- certify ------------------------------------------------------------------------------------------------------------
    def _after_offered(self, parts):
        """Whether Faculty may send to Gradescope and publish by itself after this certification (GRADESCOPE "AFTER CERTIFYING"): the
        Compute settings switch is on, Gradescope is set up, and the plan has a part that goes there."""
        return (bool(self.frq.settings()["settings"].get(gradescope_after.SWITCH, True)) and bool(self.gradescope.settings.status().get("configured"))
                and bool({"frq", "bubble"} & set(parts)))

    def _marks(self, db, plan):
        """Catalog high-water marks of everything a certified grade is read from (critique C3): re-read INSIDE the certify
        transaction, where no roster payload may be read. Writers that take the lifecycle lock are excluded by certify holding it;
        these catch the rest (the cloud dispatcher, the Gradescope sender, the Inbox worker)."""
        aid = plan["id"]

        def one(sql, *args):
            row = db.execute(sql, args).fetchone()
            return tuple(row) if row else None
        marks = {"responses": one("SELECT COUNT(*),COALESCE(SUM(version),0),MAX(rowid) FROM frq_responses WHERE assignment_id=?", aid),
                 "results": one("SELECT COUNT(*),MAX(x.rowid) FROM frq_results x JOIN frq_responses r ON r.id=x.response_id WHERE r.assignment_id=?", aid),
                 "reviews": one("SELECT COUNT(*),MAX(v.rowid) FROM frq_reviews v JOIN frq_responses r ON r.id=v.response_id WHERE r.assignment_id=?", aid),
                 "tasks": one("""SELECT COUNT(*),MAX(t.rowid),SUM(t.status='complete'),SUM(t.status='failed'),SUM(t.status IN ('pending','running'))
                     FROM frq_tasks t JOIN frq_jobs j ON j.id=t.job_id WHERE j.assignment_id=?""", aid),
                 "acceptances": [one(f"SELECT COUNT(*),MAX(rowid) FROM {table} WHERE assignment_id=?", aid) for table in ACCEPTANCES],
                 "acceptance_events": [one(f"SELECT COUNT(*),MAX(e.id) FROM {events} e JOIN {owners} a ON a.id=e.acceptance_id WHERE a.assignment_id=?", aid)
                                       for events, owners in ACCEPTANCE_EVENTS]
                                      + [one("""SELECT COUNT(*),MAX(x.created_at) FROM frq_team_acceptance_reinstatements x JOIN frq_team_acceptances a
                                                ON a.id=x.acceptance_id WHERE a.assignment_id=?""", aid)],
                 "permissions": one("""SELECT COUNT(*),MAX(e.id) FROM frq_acceptance_permission_events e JOIN frq_acceptance_permissions p ON p.id=e.permission_id
                     WHERE p.assignment_id=?""", aid),
                 "acceptance_reviews": one("""SELECT COUNT(*) FROM frq_all_acceptance_reviews v JOIN frq_all_acceptances a ON a.id=v.acceptance_id
                     WHERE a.assignment_id=?""", aid),
                 "pins": [tuple(r) for r in db.execute("SELECT question_id,question_revision_id,rubric_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY question_id", (aid,))],
                 "quality": [one(f"SELECT COUNT(*),MAX(rowid) FROM {table} WHERE assignment_id=?", aid) for table in ("frq_rubric_quality_reviews", "frq_rubric_quality_checks")],
                 "versions": [one("SELECT COUNT(*),MAX(rowid) FROM frq_version_reviews WHERE assignment_id=?", aid),
                              one("SELECT COUNT(*),MAX(updated_at),SUM(status='complete') FROM frq_version_checks WHERE assignment_id=?", aid)],
                 "keys": one("SELECT MAX(revision) FROM frq_bubble_keys WHERE assignment_id=?", aid),
                 "bubble": progress.bubble_marker(db, plan),
                 "gradescope": [[tuple(r) for r in db.execute(f"SELECT status,COUNT(*) FROM {table} WHERE assignment_id=? GROUP BY status ORDER BY status", (aid,))]
                                for table in UNITS + SENDS],
                 "certification": (progress.latest_certification(db, aid) or {}).get("id")}
        # Who the grades are for and their papers, from the catalog alone (no names): the same participants marker a certification keeps.
        event = self.frq.exams._event(db, plan["event_id"])
        people = sources._participants(self.frq, db, event)
        students = [{"section_id": p["section_id"], "enrollment_id": p["enrollment_id"], **p["collection"]} for p in people.values()]
        link = sources.available(db, plan["term_id"]).get(plan["source_assignment_id"]) or {}
        marks["participants"] = progress.participants_marker(db, plan, students, link.get("parts") or [])
        marks["setup"] = db.execute("SELECT MAX(revision) FROM gradebook_setups WHERE term_id=?", (plan["term_id"],)).fetchone()[0]
        return digest(marks)

    def certify(self, aid, data, actor="workspace"):
        request_id, request, request_sha = rules.parse_request(data)  # 400 before anything heavy is read
        book = request["gradebook"]
        with self.rosters.lock():
            with connect(self.path) as db:
                prior = db.execute("SELECT * FROM frq_certifications WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone()
                if prior:
                    if prior["request_sha256"] != request_sha:
                        raise Conflict("This request ID was used for a different certification.")
                    return {"certification": self._brief(dict(prior)), "replay": True}
                plan = self.frq.plan(db, aid)
                marks = self._marks(db, plan)  # BEFORE the heavy reads: anything written during them shows as a changed mark
            state = self._evaluate(aid, replace=set(book["replace"]), column=book["column"] or None, category_id=book["category_id"])
            if state["fingerprint"] != request["fingerprint"]:
                raise Refused("The grades changed since the preview. Check again.", state["checks"])
            blocks = [c for c in state["checks"] if c["level"] == "block"]
            if blocks:
                raise Refused("Finish these before certifying: " + "; ".join(c["title"] for c in blocks) + ".", state["checks"])
            missing = rules.unconfirmed(state["checks"], request)
            if missing:
                raise Refused("Confirm before certifying: " + "; ".join(missing) + ".", state["checks"])
            # GRADESCOPE "AFTER CERTIFYING": the instructor's tick in the dialog. Faculty then sends what is not there and publishes.
            after = bool(request.get("gradescope_after"))
            if after and not self._after_offered(state["totals"]["parts"]):
                raise Refused("Faculty cannot send to Gradescope by itself for this assignment now. Check again.", state["checks"])
            for item in state["checks"]:
                if item["level"] == "confirm" and item.get("requires") == "decision":
                    group = "written" if item["id"] == "gradescope_written" else "sheets"
                    if request["gradescope"][group]["decision"] != "do_not_send" and not after:
                        raise Refused("Send these to Gradescope first, then check again, or choose not to send them with a reason.", state["checks"])
            option = state["option"]
            if option is None:
                raise Refused("There is no gradebook column to record in. Check again.", state["checks"])
            records = state["records"]
            summary = rules.summary(records)
            checks_record = [{k: c.get(k) for k in ("id", "level", "title", "count", "digest")} for c in state["checks"]]
            confirmations = {c["id"]: {"digest": c["digest"], "count": c["count"]} for c in state["checks"] if c["level"] == "confirm"}
            for c in state["checks"]:
                if c["level"] == "confirm" and c["id"] in ("zero_no_work", "zero_missing_part", "dr_ahead", "makeup_ahead", "duplicate_membership", "gradebook_sections"):
                    confirmations[c["id"]]["rows"] = [r.get("row") for r in c.get("rows") or []]
            confirmations["gradebook_column"] = {**confirmations.get("gradebook_column", {}), "column": option["column"], "category_id": book["category_id"]}
            gradescope = rules.gradescope_record(state["checks"], request, {"written": state["gradescope"]["written"], "sheets": state["gradescope"]["sheets"] or {}},
                                                 state["gradescope"]["configured"])
            context, cid, now = state["context"], uid(), timestamp()
            gradebook_request = f"certify:{request_id}"
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if self._marks(db, plan) != marks:
                    raise Refused("The grades changed while they were being checked. Check again.", state["checks"])
                latest = progress.latest_certification(db, aid)
                if (latest or {}).get("id") != (state["latest"] or {}).get("id"):
                    raise Refused("Another certification was recorded meanwhile. Check again.", state["checks"])
                # The gradebook's cheap "may have changed" hint compares this catalog-only digest, never timestamps (review F4).
                summary["grade_marker"] = progress.grade_marker(db, aid, state["totals"]["parts"])
                prepared = self.gradebook.prepare_certification(db, context, term_id=plan["term_id"], option=option, revision=state["book"]["revision"],
                                                                cells=option["cells"], request_id=gradebook_request)
                db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
                    participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,supersedes_id,actor,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (cid, aid, request_id, request_sha, state["fingerprint"], progress.RULE, encoded(state["totals"]["parts"]),
                            state["markers"]["decisions"], state["markers"]["bubble"], state["markers"]["participants"], state["snapshot_sha256"],
                            encoded(summary), encoded(checks_record), encoded(confirmations), encoded(gradescope), encoded(prepared["receipt"]),
                            latest["id"] if latest else None, actor, now))
                for record in records:
                    db.execute("""INSERT INTO frq_certification_students(certification_id,section_id,enrollment_id,status,reason,earned,possible,exam_version,parts_json,dr_date)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""", (cid, record["section_id"], record["enrollment_id"], record["status"], record["reason"], record["earned"],
                                                       record["possible"], record["exam_version"], encoded(record["parts_json"]), record["dr_date"]))
                stored_book = self.gradebook.certification_options(db, context, assignment_id=plan["source_assignment_id"], plan_id=plan["id"],
                                                                   event_id=plan["event_id"], title=plan["title"], records=sources.certified_records(db, cid),
                                                                   certified_parts=json.loads(encoded(state["totals"]["parts"])), exam_sections=state["exam_sections"],
                                                                   replace=set(book["replace"]), selected=option["key"], category_id=book["category_id"])
                stored = next((o for o in stored_book["options"] if o["key"] == option["key"]), None)
                if stored is None:
                    raise Conflict("The gradebook column changed while certifying. Check again.")
                receipt = self.gradebook.record_certification(db, context, prepared=prepared, option=option, stored=stored, request_sha=request_sha,
                                                              certification_id=cid, actor=actor)
                if after:  # in this transaction: nothing is lost between certifying and Gradescope
                    # Each group's decision only: the instructor's reason for not sending stays with the certification.
                    gradescope_after.queue_run(db, cid, aid, "certified", {g: {"decision": v["decision"]} for g, v in request["gradescope"].items()}, actor, now)
                self.frq.event(db, aid, "grades_certified", {"certification_id": cid, **{k: summary[k] for k in ("students", "scored", "zero", "excused", "left_out")},
                                                             "gradebook": {k: receipt[k] for k in ("item_id", "written", "kept", "created_column")},
                                                             "gradescope": {g: gradescope[g]["decision"] for g in ("written", "sheets")},
                                                             **({"gradescope_after": True} if after else {})}, actor)
            with connect(self.path) as db:
                certification = self._brief(dict(db.execute("SELECT * FROM frq_certifications WHERE id=?", (cid,)).fetchone()))
            # COLUMN ASSIGNMENTS (docs/decisions/canvas.md): a column the certification made gets its assignment in Faculty's copy.
            self.gradebook.columns_changed(plan["term_id"], actor)
        if after:
            self.gradescope.wake.set()
        # RUBRIC MEMORIES (GRADING, 2026-10-14): the instructor's own Certify asks for one pass over this assignment's rubric questions,
        # answers and grader doubts. Queued after the certification is recorded; nothing about it can undo or fail the certification.
        if getattr(self.frq, "rubric_memory", None) is not None:
            self.frq.rubric_memory.after_certified(aid)
        return {"certification": certification, "replay": False}
