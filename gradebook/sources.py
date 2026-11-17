"""Pulling a column's scores from this term's grading of its assignment (README "Pulling from grading").

A column linked to an assignment reads the students' totals from up to three sources of the term's exam of that assignment,
each by ITS OWN rule, never re-decided here:

  frq     assisted FRQ grading: final grades only, by `final_decisions` (the export's and the Gradescope sender's one rule),
          summed per student over the questions of THEIR paper: the printed document version of the paper and its exam version
          (`paper_version`), exactly as the export's totals are (`review.export`, `GradescopeAdapter.totals`). A student counts only
          when every expected question is final; a paper whose version is unsettled is never counted.
  bubble  bubble sheets scored against the plan's answer key by the Bubble sheets page's own scoring (`_bubble_view`): exactly one
          scored sheet recorded for the student. Scans still in Inbox review are only counted, as they are there.
  exams   the Exams page's manual grading (`exam_workflow.grading._summary`): a finalized or released total that is not stale.

Everything here READS. The caller holds the roster lifecycle lock (`frq.rosters.lock()`, which every service below re-enters in
the same thread) and passes an open catalog connection that is not inside a write transaction: `_bubble_view` opens its own
connection to read, and a read of the verified roster payload may need a separate catalog writer for storage reconciliation.
Nothing in grading, the Exams page or the Inbox is changed by a preview or a pull; the one table a pull writes is the gradebook's
own, and that is the service's job, not this module's.

`plan` is pure: it turns the totals into the preview (`PullPlan`) with the gradebook's one rule for what may change a cell
(`common.change_action`), so an apply can recompute it and compare fingerprints.

`maximum` says what a linked column is worth from the plan's own setup (its pinned questions, answer key and Exams-page
rubric), never from a student's grades; the service uses it for a new linked column's points and the view shows it.
"""
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from exam_links import ExamLinkService
from grading import participation
from grading.exam_versions import GRADABLE
from teach_inbox.service import PRESENT

from . import common
from .setup import PARTS

LABELS = {"frq": "FRQ", "bubble": "Bubble sheet", "exams": "Exams page"}
UNAVAILABLE = {
    "frq": "This assignment's grading plan has no FRQ questions yet, so FRQ grades are not pulled.",
    "bubble": "This assignment's grading plan has no bubble sheet answer key yet, so bubble sheet scores are not pulled.",
    "exams": "The Exams page has no grading rubric for this exam yet, so its grades are not pulled.",
}


def _decimal(value):
    """A grading score as an exact decimal: FRQ and Exams scores are floats (their own storage), bubble scores are text."""
    return Decimal(str(value))


def _plural(count, one):
    return f"{count} {one if count == 1 else one + 's'}"


def _links(db, term_id, assignment_id=None):
    """One query: each exam of the term with its grading plan and which sources hold anything to pull.

    frq: the plan pins questions. bubble: the plan has a saved answer key (a sheet is scored only against one). exams: the Exams
    page's grading is set up for the exam (a pinned rubric, which every finalization needs) or has a finalization.
    """
    only, args = ("AND e.assignment_id=?", (term_id, assignment_id)) if assignment_id else ("", (term_id,))
    found = {}
    for row in db.execute(f"""SELECT e.id event_id,e.assignment_id,g.id plan_id,
            EXISTS(SELECT 1 FROM frq_assignment_questions q WHERE q.assignment_id=g.id) frq,
            EXISTS(SELECT 1 FROM frq_bubble_keys k WHERE k.assignment_id=g.id) bubble,
            (EXISTS(SELECT 1 FROM exam_grading_rubrics r WHERE r.event_id=e.id)
             OR EXISTS(SELECT 1 FROM exam_grade_finalizations f WHERE f.event_id=e.id)) exams
            FROM exam_events e LEFT JOIN frq_assignments g ON g.event_id=e.id WHERE e.term_id=? {only}
            ORDER BY e.created_at,e.id""", args):
        found[row["assignment_id"]] = {"plan_id": row["plan_id"], "event_id": row["event_id"],
                                       "parts": [part for part in PARTS if row[part]]}
    return found


def available(db, term_id):
    """{assignment_id: {plan_id, event_id, parts}} for every assignment of the term's course with an exam in this term.

    Cheap (one query, no roster payload, no grading rule run): the gradebook view shows it for every assignment."""
    return _links(db, term_id)


def graded_by_term(db):
    """{term_id: {assignment_id, ...}}: every term's assignments whose exam there has something to pull (the same three
    sources `_links` checks), for the term list's "not in this gradebook" count. One query over every term, catalog rows only:
    no roster payload, no grading rule run, no lock."""
    found = {}
    for row in db.execute("""SELECT e.term_id,e.assignment_id FROM exam_events e LEFT JOIN frq_assignments g ON g.event_id=e.id
            WHERE EXISTS(SELECT 1 FROM frq_assignment_questions q WHERE q.assignment_id=g.id)
               OR EXISTS(SELECT 1 FROM frq_bubble_keys k WHERE k.assignment_id=g.id)
               OR EXISTS(SELECT 1 FROM exam_grading_rubrics r WHERE r.event_id=e.id)
               OR EXISTS(SELECT 1 FROM exam_grade_finalizations f WHERE f.event_id=e.id)"""):
        found.setdefault(row["term_id"], set()).add(row["assignment_id"])
    return found


def without_column(graded, setup):
    """How many of `graded` (assignment IDs with something to pull) no live column of `setup` references."""
    referenced = {item.get("assignment_id") for item in (setup or {}).get("items", []) if item.get("assignment_id") and not item.get("archived")}
    return len(set(graded or ()) - referenced)


# ----- when grading last changed -----------------------------------------------------------------------------------------
# A hint for the grid's Pull icon: "grading changed since the last pull". The latest time anything a pull reads could have
# changed, per part, from catalog timestamps only (no roster payload, no grading rule run, no lock): FRQ instructor decisions
# (drafts excluded), automatic acceptances and their suspensions; the bubble answer key, completed reads of the exam's sheets and
# their reviews; the Exams page's finalizations and releases. It never pulls, and it says nothing about WHAT changed.

# Bubble sheets count as the pull reads them (`grading.bubble_keys._bubble_runs`): each PRESENT scan's latest complete read. A scan
# the instructor deleted in the Inbox counts nowhere (AGENTS.md), so its reads and reviews never move this hint.
_LATEST_PRESENT = f"""r.status='complete' AND {PRESENT.format('r.message_id')}
    AND r.id=(SELECT n.id FROM bubble_runs n WHERE n.message_id=r.message_id AND n.status='complete' ORDER BY n.created_at DESC,n.id DESC LIMIT 1)"""

CHANGES = {
    "frq": ("""SELECT MAX(v.created_at) FROM frq_reviews v JOIN frq_responses r ON r.id=v.response_id
               WHERE r.assignment_id=:plan AND v.status!='draft'""",
            "SELECT MAX(created_at) FROM frq_all_acceptances WHERE assignment_id=:plan",
            """SELECT MAX(e.created_at) FROM frq_team_acceptance_events e JOIN frq_team_acceptances a ON a.id=e.acceptance_id
               WHERE a.assignment_id=:plan""",
            """SELECT MAX(x.created_at) FROM frq_team_acceptance_reinstatements x JOIN frq_team_acceptances a ON a.id=x.acceptance_id
               WHERE a.assignment_id=:plan"""),
    "bubble": ("SELECT MAX(created_at) FROM frq_bubble_keys WHERE assignment_id=:plan",
               f"""SELECT MAX(r.updated_at) FROM bubble_runs r JOIN exam_events e ON e.assignment_id=r.assignment_id AND e.term_id=r.term_id
                   WHERE e.id=:event AND {_LATEST_PRESENT}""",
               f"""SELECT MAX(v.created_at) FROM bubble_reviews v JOIN bubble_sheets s ON s.id=v.sheet_id JOIN bubble_runs r ON r.id=s.run_id
                   JOIN exam_events e ON e.assignment_id=r.assignment_id AND e.term_id=r.term_id WHERE e.id=:event AND {_LATEST_PRESENT}"""),
    "exams": ("SELECT MAX(created_at) FROM exam_grade_finalizations WHERE event_id=:event",
              "SELECT MAX(created_at) FROM exam_grade_releases WHERE event_id=:event"),
}


def _instant(value):
    """A stored timestamp as an aware UTC datetime, or None when it is not one (a naive one is read as UTC)."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def changed_at(db, link, parts):
    """The latest change of the chosen `parts` of one `available()` entry, as UTC ISO text, or None when nothing is recorded.
    A table this workspace does not have counts as nothing. Once the plan has a certification, a pull reads THAT (`certified`),
    so what it would pull changes only with a newer certification: its time is the answer (Certification and Export, 2026-09-26)."""
    names = {"plan": (link or {}).get("plan_id"), "event": (link or {}).get("event_id")}
    latest_certified = latest_certification(db, names["plan"]) if names["plan"] else None
    if latest_certified:
        moment = _instant(latest_certified["created_at"])
        return moment.astimezone(timezone.utc).isoformat() if moment else None
    return live_changed_at(db, link, parts)


def live_changed_at(db, link, parts):
    """`changed_at` of live grading, certification or not: the view's hint that grades may have changed since a certification."""
    names = {"plan": (link or {}).get("plan_id"), "event": (link or {}).get("event_id")}
    chosen, latest = set(parts or ()), None
    for part in PARTS:
        if part not in chosen:
            continue
        for query in CHANGES[part]:
            if (":plan" in query and not names["plan"]) or (":event" in query and not names["event"]):
                continue
            try:
                row = db.execute(query, names).fetchone()
            except sqlite3.OperationalError:
                continue
            moment = _instant(row[0]) if row and row[0] else None
            if moment and (latest is None or moment > latest):
                latest = moment
    return latest.astimezone(timezone.utc).isoformat() if latest else None


# ----- the most a student can earn ---------------------------------------------------------------------------------------
# What a linked column's points should be: read from the grading plan's own setup, never from any student's grades. Each part
# reads a handful of catalog rows (no roster payload, no grading rule run, no lock of its own), so the view can show it for
# every assignment. A part whose maximum cannot be read adds nothing; `maximum` is None when no part is known.

def _points(value):
    """A stored maximum (a JSON number or text) as an exact decimal, or None when it is not a positive finite number."""
    try:
        points = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return points if points.is_finite() and points > 0 else None


def _frq_maximum(db, plan_id):
    """The FRQ questions of ONE paper: per printed document version, its shared questions (exam version '') plus one exam
    version's own, as `_frq_parts` expects them of a student. Versions whose totals differ give the largest."""
    shared, lettered = {}, {}
    for row in db.execute("""SELECT q.assignment_version_id document,q.exam_version letter,json_extract(r.content_json,'$.max_points') max_points
            FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id JOIN frq_question_revisions r ON r.id=p.question_revision_id
            WHERE p.assignment_id=?""", (plan_id,)):
        points = _points(row["max_points"])
        if points is None:
            return None  # a question without a readable maximum: the paper's total is not known
        if row["letter"]:
            versions = lettered.setdefault(row["document"], {})
            versions[row["letter"]] = versions.get(row["letter"], Decimal(0)) + points
        else:
            shared[row["document"]] = shared.get(row["document"], Decimal(0)) + points
    totals = [shared.get(document, Decimal(0)) + extra for document in set(shared) | set(lettered)
              for extra in (lettered.get(document) or {"": Decimal(0)}).values()]
    return max(totals) if totals else None


def _bubble_maximum(db, plan_id):
    """The latest answer key's total for one version (each answered question's own points, else the key's default); the
    largest when versions differ. A key without answered questions says nothing."""
    row = db.execute("SELECT key_json FROM frq_bubble_keys WHERE assignment_id=? ORDER BY revision DESC LIMIT 1", (plan_id,)).fetchone()
    if not row:
        return None
    key = json.loads(row["key_json"])
    default = (key.get("defaults") or {}).get("points")
    totals = []
    for version in key.get("versions") or []:
        points = [_points(question.get("points", default)) for question in version.get("questions") or []]
        if points and None not in points:
            totals.append(sum(points, Decimal(0)))
    return max(totals) if totals else None


def _exams_maximum(db, event_id):
    """The Exams page's pinned rubric for each document version of the exam: the largest total of its questions' points.
    An exam graded there without a pinned rubric is not read (its totals live in each finalization)."""
    totals = []
    for row in db.execute("""SELECT r.questions_json FROM exam_grading_rubrics s JOIN exam_rubric_versions r ON r.id=s.rubric_id
            WHERE s.event_id=?""", (event_id,)):
        points = [_points(question.get("max_points")) for question in json.loads(row["questions_json"])]
        if points and None not in points:
            totals.append(sum(points, Decimal(0)))
    return max(totals) if totals else None


def maxima(db, link):
    """{part: Decimal} for each available part of one `available()` entry whose maximum can be read."""
    found = {}
    for part in link.get("parts") or ():
        if part == "frq" and link.get("plan_id"):
            value = _frq_maximum(db, link["plan_id"])
        elif part == "bubble" and link.get("plan_id"):
            value = _bubble_maximum(db, link["plan_id"])
        elif part == "exams" and link.get("event_id"):
            value = _exams_maximum(db, link["event_id"])
        else:
            value = None
        if value is not None:
            found[part] = value
    return found


def total(found, parts):
    """The sum of the known maxima of the chosen parts, as points text, or None when none is known (or it is not a points
    value a column can hold)."""
    known = [found[part] for part in PARTS if part in set(parts or ()) and part in found]
    if not known:
        return None
    value = sum(known, Decimal(0))
    return common.points_text(value) if 0 < value <= common.MAX_POINTS else None


def maximum(db, term_id, assignment_id, parts):
    """The most points a student can earn from `parts` of this term's grading of `assignment_id` (str), or None when no chosen
    part is known. Pure catalog reads without a lock of its own: FRQ is one paper's pinned questions, bubble sheets the latest
    key's total, the Exams page its pinned rubric's; for each, the largest over versions."""
    link = _links(db, term_id, assignment_id).get(assignment_id)
    return total(maxima(db, link), parts) if link else None


# ----- one part of one student -------------------------------------------------------------------------------------------

def _part(state, detail="", earned=None, possible=None):
    return {"state": state, "earned": earned, "possible": possible, "detail": detail}


def _frq_student(expected, finals, found):
    """One student's FRQ part. `expected`: [(question_id, max points)] of their paper, or None when their paper's printed version
    is not settled; `finals`: {question_id: final score dict} of their final grades; `found`: their `paper_version` result.
    Complete only when EVERY expected question is final: a partial sum is never a total (the export's rule)."""
    if found["status"] not in GRADABLE:
        return _part("waiting", found.get("reason") or "This paper's exam version is not settled.")
    if expected is None:
        return _part("waiting", "the printed version of this student's paper is not settled")
    if not expected:
        return _part("waiting", "no question of this paper's version is in the grading plan")
    final = [finals[qid] for qid, _ in expected if qid in finals]
    if len(final) < len(expected):
        return _part("waiting", f"{len(final)} of {len(expected)} questions final")
    earned = sum((_decimal(score["score"]) for score in final), Decimal(0))
    possible = sum((_decimal(score.get("max_score", maximum)) for score, (_, maximum) in zip(final, expected)), Decimal(0))
    return _part("complete", f"{len(final)} of {len(expected)} questions final", common.points_text(earned), common.points_text(possible))


def _bubble_student(sheets):
    """One student's bubble part from the sheets recorded for them (normally none or one)."""
    if not sheets:
        return _part("none", "no bubble sheet")
    if len(sheets) > 1:
        # The reader flags equal claims itself; a review may still name one student on two sheets. Nobody's to settle but the instructor's.
        return _part("waiting", f"{'two' if len(sheets) == 2 else len(sheets)} sheets claim this student")
    sheet = sheets[0]
    if sheet["status"] == "scored":
        return _part("complete", f"Version {sheet['version']} sheet" if sheet["version"] else "sheet scored",
                     common.points_text(_decimal(sheet["earned"])), common.points_text(_decimal(sheet["possible"])))
    label = {"needs_review": "the sheet needs review", "no_version": "no version on the sheet", "no_key": "no answer key for the sheet's version"}
    return _part("waiting", f"{label.get(sheet['status'], sheet['status'])}: {sheet['reason']}" if sheet.get("reason") else label.get(sheet["status"], sheet["status"]))


def _exams_student(summary, collection):
    """One participant's Exams part from the Exams page's own summary of them."""
    status = summary["status"]
    if status in ("finalized", "released"):
        # The summary's total is a float sum; the current marks it sums are added again exactly (they are the finalized ones).
        earned = sum((_decimal(mark["score"]) for mark in summary["marks"].values() if mark["current"]), Decimal(0))
        return _part("complete", status, common.points_text(earned), common.points_text(_decimal(summary["max_points"])))
    if status == "stale":
        return _part("waiting", "the Exams page grade is stale: its rubric or reviewed paper changed since it was graded")
    if status == "blocked":
        if not summary["marks"] and collection.get("status") in ("no_scan", "absent", "excused"):
            return _part("none", "no paper")
        return _part("waiting", summary["source_error"] or "the paper is not ready for grading")
    if status == "in_progress":
        return _part("waiting", f"{summary['graded_count']} of {summary['question_count']} questions graded on the Exams page, not finalized")
    return _part("waiting", "not graded on the Exams page yet")


def _combine(parts, *, excused=False, absent=False, dr=False):
    """A student's state over the requested parts that are available: complete only when every one is, none when none holds
    anything, else waiting with every reason. Reasons name their part only when there is more than one."""
    if not parts:
        state, earned, possible, reasons = "none", None, None, ["nothing to pull yet"]
    elif all(part["state"] == "complete" for part in parts.values()):
        state, reasons = "complete", []
        earned = common.points_text(sum((Decimal(part["earned"]) for part in parts.values()), Decimal(0)))
        possible = common.points_text(sum((Decimal(part["possible"]) for part in parts.values()), Decimal(0)))
    else:
        state = "none" if all(part["state"] == "none" for part in parts.values()) else "waiting"
        earned = possible = None
        several = len(parts) > 1
        reasons = [f"{LABELS[name]}: {part['detail']}" if several else part["detail"] for name, part in parts.items() if part["state"] != "complete"]
    if state != "complete":
        # Why a student may have nothing here, from the records that explain it; never a reason to guess a score.
        reasons += [text for flag, text in ((dr, "DR-center booking for this exam"), (absent, "marked absent on the Exams page"),
                                            (excused, "marked excused on the Exams page")) if flag]
    return {"state": state, "earned": earned, "possible": possible, "parts": parts, "reason": "; ".join(r for r in reasons if r),
            "excused": excused, "absent": absent, "dr": dr}


# ----- reading the sources --------------------------------------------------------------------------------------------------

def _participants(frq, db, event):
    """The exam's participants with their collected paper, from the catalog alone (no roster payload, no names): the same
    collection the Exams page gives each (`_collection`), computed from the exam's copies read once."""
    people = {}
    for row in db.execute("""SELECT p.*,s.number section_number FROM exam_participants p JOIN course_sections s ON s.id=p.section_id
            WHERE p.event_id=? ORDER BY s.number,p.enrollment_id""", (event["id"],)):
        people[(row["section_id"], row["enrollment_id"])] = dict(row)
    copies = frq.exams._copies(db, event)
    for person in people.values():
        person["collection"] = frq.exams._person_collection(db, event, person, copies)
    return people


def _frq_parts(frq, db, plan_id, event_id, people, keys, warnings, decisions=None, expected_out=None):
    """`decisions`: `final_decisions(db, plan_id)` when the caller already ran it (Certification and Export runs it once for its
    snapshot and these totals). `expected_out`, when a dict, receives each student's expected question IDs (None: unsettled)."""
    # GRADING "GRADED FOR PARTICIPATION": what a final grade counts as (already counted grades stay as they are).
    plan, decisions, skipped = participation.counted_decisions(db, plan_id, decisions if decisions is not None else frq.final_decisions(db, plan_id))
    if skipped:
        warnings.append(f"{_plural(len(skipped), 'FRQ grade')} that looked final {'is' if len(skipped) == 1 else 'are'} not counted "
                        f"(for example: {skipped[0]['reason']})")
    finals = {}
    for decision in decisions:
        row = decision["row"]
        finals.setdefault((row["section_id"], row["enrollment_id"]), {})[row["question_id"]] = decision["final"]["score"]
    questions = [dict(row) for row in db.execute("""SELECT p.question_id,q.assignment_version_id,q.exam_version,json_extract(r.content_json,'$.max_points') max_points
            FROM frq_assignment_questions p JOIN frq_questions q ON q.id=p.question_id JOIN frq_question_revisions r ON r.id=p.question_revision_id
            WHERE p.assignment_id=? ORDER BY p.rowid""", (plan_id,))]
    documents = {}
    for row in db.execute("""SELECT DISTINCT r.section_id,r.enrollment_id,q.assignment_version_id FROM frq_responses r
            JOIN frq_questions q ON q.id=r.question_id WHERE r.assignment_id=?""", (plan_id,)):
        documents.setdefault((row["section_id"], row["enrollment_id"]), set()).add(row["assignment_version_id"])
    shared, result = {}, {}
    for key in keys:
        if key not in documents:
            result[key] = _part("none", "no graded paper")
            continue
        person = people.get(key)
        if person is None:
            result[key] = _part("waiting", "this student is no longer a participant in this exam")
            continue
        found = frq.paper_version(db, plan, person, shared)
        # The printed document version of the paper, as grading reads it (`manual._source`); the responses' own when the paper
        # names none (no current reviewed paper), and unsettled when they disagree.
        document = frq.manual._paper_version(db, event_id, person) or (next(iter(documents[key])) if len(documents[key]) == 1 else None)
        letter = found.get("version") or ""
        expected = None if document is None else [(q["question_id"], q["max_points"]) for q in questions
                                                  if q["assignment_version_id"] == document and q["exam_version"] in ("", letter)]
        result[key] = _frq_student(expected, finals.get(key, {}), found)
        if expected_out is not None:
            expected_out[key] = None if expected is None else [qid for qid, _ in expected]
    return result


def _bubble_parts(frq, plan_id, rows, warnings, view=None):
    if view is None:
        _, view = frq._bubble_view(plan_id)  # re-enters the caller's lifecycle lock; reads through its own connection
    in_inbox = sum(scan["sheets"] for scan in view["waiting_scans"])
    if in_inbox:
        warnings.append(f"{_plural(in_inbox, 'sheet')} {'is' if in_inbox == 1 else 'are'} in scans still under Inbox review.")
    by_enrollment = {}
    for row in rows:
        by_enrollment.setdefault(row["enrollment_id"], []).append(row)
    claims, unmatched, outside, ambiguous = {}, 0, 0, 0
    for sheet in view["sheets"]:
        if not sheet["enrollment_id"]:
            unmatched += 1
            continue
        candidates = by_enrollment.get(sheet["enrollment_id"], [])
        # The sheet names the enrollment and the section number the reader found it in; an enrollment in two sections of the
        # term goes to that section's row, never to both.
        chosen = [row for row in candidates if str(row["section"]) == str(sheet["section"])] or (candidates if len(candidates) == 1 else [])
        if not candidates:
            outside += 1
        elif len(chosen) != 1:
            ambiguous += 1
        else:
            claims.setdefault(chosen[0]["row"], []).append(sheet)
    if unmatched:
        warnings.append(f"{_plural(unmatched, 'bubble sheet')} {'is' if unmatched == 1 else 'are'} not matched to a student yet (review them in the Inbox).")
    if outside:
        warnings.append(f"{_plural(outside, 'bubble sheet')} {'names' if outside == 1 else 'name'} a student who is not on this term's roster.")
    if ambiguous:
        warnings.append(f"{_plural(ambiguous, 'bubble sheet')} could not be placed in one section of a student enrolled in several.")
    return {row["row"]: _bubble_student(claims.get(row["row"], [])) for row in rows}


def _exams_parts(frq, db, event, people, keys):
    result = {}
    for key in keys:
        person = people.get(key)
        if person is None:
            result[key] = _part("none", "not in this exam")
            continue
        rubric = frq.manual._student_rubric(db, event["id"], person)
        result[key] = _exams_student(frq.manual._summary(db, event, person, rubric), person["collection"])
    return result


def totals(frq, db, term_id, assignment_id, parts, *, rows, decisions=None, people=None, bubble_view=None, expected=None):
    """Each student's total from the chosen parts of this term's grading of `assignment_id`.

    `frq` is the FRQ grading service (app.extensions["frq_grading"]); the caller holds `frq.rosters.lock()` and passes `db`, an
    open catalog connection NOT inside a write transaction. `rows` are the term's students: [{row, section_id, enrollment_id,
    section}]. Returns {plan_id, event_id, parts (the requested parts that are available), students: {row: {state, earned,
    possible, parts: {part: {state, earned, possible, detail}}, reason, excused, absent, dr}}, warnings}. Writes nothing.

    Reads a caller already made may be handed in, so nothing is read twice (Certification and Export): `decisions` the plan's
    `final_decisions`, `people` {(section_id, enrollment_id): participant with its `collection`}, `bubble_view` the plan's
    `_bubble_view`; `expected`, a dict, receives each student's expected FRQ question IDs. The answer is the same either way.
    """
    requested = [part for part in PARTS if part in set(parts or ())]
    if len(requested) != len(set(parts or ())):
        raise ValueError("Pull from FRQ grading, bubble sheets or the Exams page.")
    link = _links(db, term_id, assignment_id).get(assignment_id) or {"plan_id": None, "event_id": None, "parts": []}
    warnings = []
    if not link["event_id"]:
        warnings.append("This assignment has no exam in this term, so there is no grading to pull from.")
    used = [part for part in requested if part in link["parts"]]
    warnings += [UNAVAILABLE[part] for part in requested if part not in used and link["event_id"]]
    keys = [(row["section_id"], row["enrollment_id"]) for row in rows]
    sections = [row[0] for row in db.execute("SELECT id FROM course_sections WHERE term_id=?", (term_id,))]
    # DR bookings link to the course-wide assignment, exam or not. Per section, so an enrollment in two sections of the term is
    # flagged only where the booking is.
    dr = {(section, enrollment) for section in sections for enrollment in ExamLinkService.excluded(db, [section], assignment_id)}
    by_part, flags = {}, {}
    if link["event_id"]:
        event = frq.exams._event(db, link["event_id"])
        for row in db.execute("SELECT section_id,enrollment_id,expectation FROM exam_participants WHERE event_id=?", (event["id"],)):
            flags[(row["section_id"], row["enrollment_id"])] = row["expectation"]
        given = people
        people = {}
        if "frq" in used or "exams" in used:
            people = given if given is not None else _participants(frq, db, event)
        if "frq" in used:
            frq_parts = _frq_parts(frq, db, link["plan_id"], event["id"], people, keys, warnings, decisions, expected)
            by_part["frq"] = {row["row"]: frq_parts[key] for row, key in zip(rows, keys)}
        if "bubble" in used:
            by_part["bubble"] = _bubble_parts(frq, link["plan_id"], rows, warnings, bubble_view)
        if "exams" in used:
            exams_parts = _exams_parts(frq, db, event, people, keys)
            by_part["exams"] = {row["row"]: exams_parts[key] for row, key in zip(rows, keys)}
    students = {}
    for row, key in zip(rows, keys):
        students[row["row"]] = _combine({part: by_part[part][row["row"]] for part in used},
                                        excused=flags.get(key) == "excused", absent=flags.get(key) == "absent", dr=key in dr)
    return {"plan_id": link["plan_id"], "event_id": link["event_id"], "parts": used, "students": students, "warnings": warnings}


# ----- certified values (Certification and Export, 2026-09-26) ------------------------------------------------------------
# Once the instructor certifies a plan's grades (`grading/certification.py`, migration 029, owned by the TERM grading plan), the
# gradebook records the CERTIFIED values: the certification writes them in its own transaction, and an ordinary pull of the column
# reads the latest certification instead of live grading (the orchestrator's default "the gradebook keeps the certified values until
# the next certification"). A plan never certified is pulled live, as before. A certified 0 is the instructor's explicit confirmation
# recorded with the certification; a live pull never writes one.

def latest_certification(db, plan_id):
    """The plan's latest certification row, or None; a workspace without migration 029 has none."""
    if not plan_id or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='frq_certifications'").fetchone():
        return None
    row = db.execute("SELECT * FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (plan_id,)).fetchone()
    return dict(row) if row else None


def _certified_part(name, part):
    part = part or {}
    possible = part.get("possible") if part.get("possible") is not None else "0"
    if part.get("state") == "complete":
        return _part("complete", part.get("detail") or f"certified {LABELS[name]} grade", part.get("earned"), possible)
    if part.get("state") == "zero":
        return _part("complete", f"confirmed 0: {part.get('reason') or 'no work'}", "0", possible)
    return _part("waiting", f"{LABELS[name]} was not certified")


def certified_totals(records, parts, *, certified_parts=None, plan_id=None, event_id=None, certification_id=None, certified_at=None,
                     clear=()):
    """`totals()`'s shape from certified rows, pure (critique C2: the certify preview runs it on the prospective snapshot, the certify
    apply and every later pull on the stored rows, so both give the same plan). `records`: `frq_certification_students` rows as dicts
    (`parts_json` a dict or its JSON); `parts`: the column's linked parts, of which only those certified count (critique C10: a column
    linked to FRQ alone takes the FRQ part alone, a confirmed-0 part only where it is linked). `clear`: rows whose current cell an
    earlier certification wrote; a student left out now has that cell cleared (critique C11). `certification_id`, when given, is
    named in the plan's fingerprint (a newer certification makes an older pull preview stale); the certify step leaves it out."""
    requested = [part for part in PARTS if part in set(parts or ())]
    certified = [part for part in PARTS if part in set(certified_parts if certified_parts is not None else requested)]
    used = [part for part in requested if part in certified]
    warnings = [f"{LABELS[part]} was not part of the certification, so it is not recorded in this column." for part in requested if part not in certified]
    students = {}
    for record in records:
        row = common.row_key(record["section_id"], record["enrollment_id"])
        detail = record.get("parts_json") or {}
        if isinstance(detail, str):
            detail = json.loads(detail)
        status, dr = record["status"], bool(record.get("dr_date"))
        if status in ("scored", "zero"):
            if not used:
                students[row] = {**_combine({}), "reason": "nothing certified for this column's parts"}
                continue
            students[row] = _combine({name: _certified_part(name, detail.get(name)) for name in used}, dr=dr)
        elif status == "excused":
            students[row] = {**_combine({}, excused=True), "reason": "certified as excused (marked excused on the Exams page)"}
        else:
            why = f"left out until their DR exam on {record.get('dr_date')}" if record.get("reason") == "dr_ahead" else \
                "left out until their makeup" if record.get("reason") == "makeup" else "left out: their paper is in another section of this exam"
            students[row] = {**_combine({}, dr=dr), "reason": why, "clear": row in set(clear)}
    return {"plan_id": plan_id, "event_id": event_id, "parts": used, "students": students, "warnings": warnings,
            "certification_id": certification_id, "certified_at": certified_at, "absent_reason": "not in the certification"}


def certified_records(db, certification_id):
    return [dict(row) for row in db.execute("""SELECT section_id,enrollment_id,status,reason,earned,possible,exam_version,parts_json,dr_date
        FROM frq_certification_students WHERE certification_id=?""", (certification_id,))]


def certified(db, plan_id, parts, *, clear=()):
    """`totals()`'s shape from the plan's latest certification, catalog rows only; None when it has none."""
    found = latest_certification(db, plan_id)
    if not found:
        return None
    return certified_totals(certified_records(db, found["id"]), parts, certified_parts=json.loads(found["parts_json"]), plan_id=plan_id,
                            event_id=db.execute("SELECT event_id FROM frq_assignments WHERE id=?", (plan_id,)).fetchone()[0],
                            certification_id=found["id"], certified_at=found["created_at"], clear=clear)


def certified_cells(db, term_id, item_id):
    """IDs of this column's score revisions written by a request that names a certification (the certify step, or a pull of a
    certified plan): a left-out student's such cell is cleared by the next certification (critique C11)."""
    return {row[0] for row in db.execute("""SELECT g.id FROM gradebook_scores g JOIN gradebook_requests q ON q.id=g.request_ref
        WHERE g.term_id=? AND g.item_id=? AND g.source='grading' AND json_extract(q.summary_json,'$.certification_id') IS NOT NULL""",
                                         (term_id, item_id))}


# ----- the preview --------------------------------------------------------------------------------------------------------

COUNTED = ("add", "update", "same", "kept", "waiting", "none")


def plan(item, totals, *, students, cells, options, revision):
    """The PullPlan (README) for one linked column: what a pull would write to each of its students' cells, and why the others wait.

    Pure. `item` is the normalized setup item; `totals` is `totals(...)` for its assignment and parts; `students` are the term's
    students [{row, section_id, section, name}] in display order; `cells` {row: latest revision} of THIS column (absent = empty);
    `options` {"replace": set of rows whose hand-typed value may be replaced, "absent_as_missing": bool}; `revision` the setup
    revision the preview was made on.

    The fingerprint covers what the grading and the gradebook say (each row's proposal, its current cell and the action WITHOUT
    Replace), the column's points and parts, the setup revision and `absent_as_missing`, so an apply that recomputes a different
    plan is refused. It does NOT depend on `replace`: the instructor ticks Replace boxes after seeing a preview that took a long
    read of grading, and a tick only turns that row's `kept` into `update`, which the apply recomputes with the ticks it is sent.
    """
    sections = set(item.get("sections") or ())
    replace = set(options.get("replace") or ())
    absent_as_missing = bool(options.get("absent_as_missing"))
    points = Decimal(item["points"])
    rows, fingerprinted, counts, differ = [], [], dict.fromkeys(COUNTED, 0), {}
    for student in students:
        if sections and student["section_id"] not in sections:
            continue  # the column does not apply to this student's section
        row = student["row"]
        total = totals["students"].get(row) or {**_combine({}), "reason": totals.get("absent_reason") or "not on the roster the grades were read for"}
        current = cells.get(row)
        proposed, reason = None, total["reason"]
        if total["state"] == "complete":
            proposed, reason = {"status": "scored", "points": total["earned"]}, ""
            if Decimal(total["possible"]) != points:
                differ[total["possible"]] = differ.get(total["possible"], 0) + 1
        elif total["excused"]:
            # The Exams page excused them and there is no complete score: excused, not missing.
            proposed, reason = {"status": "excused", "points": None}, total["reason"]
        elif total["absent"] and absent_as_missing:
            proposed, reason = {"status": "missing", "points": None}, total["reason"]
        elif total.get("clear") and current and current["status"] != "cleared":
            # Left out of the latest certification (a DR exam still ahead, critique C11) while an earlier certification wrote this
            # cell: the certified value no longer stands, so it is cleared (a `grading` clear, which the next value simply fills).
            proposed = {"status": "cleared", "points": None}
        if proposed:
            untouched = common.change_action(current, proposed)
            action = common.change_action(current, proposed, replace_hand=row in replace)
            if action == "kept":
                hand = ("You cleared this cell in the gradebook; tick Replace to fill it." if current["status"] == "cleared"
                        else "You typed this value in the gradebook; tick Replace to overwrite it.")
                reason = "; ".join(r for r in (hand, reason) if r)
        else:
            untouched = action = "none" if total["state"] == "none" else "waiting"
        counts[action] += 1
        rows.append({"row": row, "name": student["name"], "section": student["section"], "action": action, "proposed": proposed,
                     "current": current, "detail": {"earned": total["earned"], "possible": total["possible"], "parts": total["parts"]},
                     "reason": reason})
        fingerprinted.append([row, untouched, proposed, current["id"] if current else None])
    warnings = list(totals["warnings"])
    if differ:
        many = sum(differ.values())
        values = ", ".join(sorted(differ, key=Decimal))
        warnings.append(f"{many} {'student' + chr(39) + 's' if many == 1 else 'students' + chr(39)} possible points ({values}) differ from the column's "
                        f"{common.points_text(points)} points; the points earned are recorded as they are.")
    parts = list(totals["parts"])
    basis = {"item_id": item["id"], "points": common.points_text(points), "parts": parts, "revision": revision,
             "absent_as_missing": absent_as_missing, "rows": sorted(fingerprinted, key=lambda entry: entry[0])}
    if totals.get("certification_id"):
        basis["certification_id"] = totals["certification_id"]  # a newer certification makes an older preview stale
    fingerprint = common.digest(basis)
    return {"fingerprint": fingerprint, "item_id": item["id"], "assignment_id": item.get("assignment_id"), "plan_id": totals["plan_id"],
            "parts": parts, "points": common.points_text(points), "rows": rows, "counts": counts, "warnings": warnings,
            **({"certification_id": totals["certification_id"], "certified_at": totals.get("certified_at")} if totals.get("certification_id") else {})}
