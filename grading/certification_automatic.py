"""Late papers certify themselves (requirement, 2026-10-09). GRADING "LATE PAPERS
CERTIFY THEMSELVES".

A student is LATE when the plan's latest certification gave them no grade of their own work (a confirmed 0, left out until a DR exam
or a makeup, a part confirmed as 0, or no row: a participant added since) and their work is now graded: every part complete, every
response final by `final_decisions` and cleared (no flag, or settled by the arbitrator, the flag reviewer or a flag review, or decided
by the instructor's own review), named by no block and by no check the instructor has to confirm, their gradebook cell not a value the
instructor typed, and no standing "Add a line to every grade…" line of their question and section missing from their grade (it reached
only the grades final when it was applied; 2026-10-09 review). A rescan that changes an already certified grade is not late: it waits
for the instructor, as before.

`run(aid)` (the worker's `auto_certify_one`, under the lifecycle lock, as `certify` reads): the exact state once (`_evaluate`), then
ONE new certification that supersedes the latest: the late students' rows as they are now, every other row exactly as certified, the
gradebook write of the late students' cells only (the gradebook's own plan on those rows, as "Certify just this change" writes one),
and the Gradescope sends to queue for them (`frq_gradescope_automatic_sends`, migration 045; the sender records each as an ordinary
send, actor `automatic`). Idempotent by a request ID derived from what it certifies; one `BEGIN IMMEDIATE` that re-checks the
catalog high-water marks, as `certify` does.

LIVE OR CARRIED MARKERS. The certification is honest about the rest of the plan: it keeps the LIVE markers (the plan reads current)
only when, the late students aside, the plan is exactly what the base certification certified (its decisions, papers and sheets
rebuilt without the late students digest to the base's markers, every other row equals its certified row, nothing blocks, no new
work, and every check the instructor confirmed still holds with the late students counted as they were). Otherwise it CARRIES the
base's markers (and its gradebook hint): the plan still reads "changed since certification" and the instructor's own uncertified
changes, and any late paper still waiting, keep waiting for their Certify; `amends_id` then says it covers nothing new
(CERTIFIED-ARCHIVE). The base is the last certification with live markers; `summary.automatic.pending` lists who Faculty certified
since it, so the next one rebuilds the same comparison.

MAKEUP CHANGES CERTIFY THEMSELVES (requirement, 2026-11-14). The same run also
takes, with no paper at all, a student the latest certification left out until their makeup who now counts 0 (the absence is not
excused, withdrawn or moved, or the makeup was recorded missed: `zero`), a student it gave 0 with no work whose excused or undecided
absence now covers the day it was given (`left_out`: their cell is cleared), and a student left out until their makeup who dropped
with no work (`gone`: their row is not kept). `makeup_change` names the first two; `summary.automatic.changes` says which row was
which. A 0 is never given while a paper or sheet in the assignment's scans has no student yet (it may be theirs).
"""
import json
import uuid

from werkzeug.exceptions import Conflict

from absences import makeups
from course_storage import timestamp
from gradebook import sources
from rosters import connect

from . import batch_adjustments
from . import certification_rules as rules
from . import gradescope_after
from . import progress
from .certification import _row
from .certification_change import as_they_were, certified_parts, dropped_no_work, part_content, row_content
from .models import digest, encoded, finite_decimal, uid

ACTOR = "automatic"
NAMESPACE = uuid.UUID("6f1d1c4e-5a6b-4f0e-9a43-6c1b7a2e9d10")
# Late at the base: what the latest certification gave a student whose own work had not come yet.
LATE = {"zero": ("no_paper", "absent"), "left_out": ("dr_ahead", "makeup")}
# Confirm checks whose confirmation carries over untouched: the Gradescope decisions and the MC check (a Gradescope send after
# certifying reopens nothing), and the gradebook's (this plans its own few rows).
CARRIED = ("gradescope_written", "gradescope_sheets", "gradescope_mc_differs", "gradebook_column", "gradebook_kept", "gradebook_sections")
# Not CARRIED for who waits (`_find`): a late student whose own sheet has an open Gradescope correction still waits for the instructor to look
# at it (GRADESCOPE "MC CORRECTIONS"; AGENTS.md SCOPE). But Gradescope's copy moving is no unconfirmed check of the plan: a live or
# settled correction never turns the markers carried or the plan "changed" (requirement, 2026-11-06), so these lines keep the checks
# the base confirmed and leave this one out of the markers' reading.
UNSTORED = CARRIED + ("gradescope_mc_corrected",)
WAIT = ("inbox_pending", "gradescope_live")  # a plan-wide block that may hide a late paper's second copy, or moves what is on Gradescope
ROW_CHECKS = ("zero_no_work", "zero_missing_part", "dr_ahead", "makeup_ahead", "duplicate_membership")  # the confirmations that keep rows
# MAKEUP CHANGES CERTIFY THEMSELVES: the confirm check that lists a student for the very change Faculty certifies (it holds nothing up).
OWN_CHECK = {"zero": "zero_no_work", "left_out": "makeup_ahead"}


def carried_chain(db, latest):
    """(base, rows_base, pending) of the plan's latest certification: `base` the certification whose markers the next comparison
    starts from, `rows_base` the one whose rows say how the `pending` students stood before Faculty certified them by itself.
    A LIVE latest is its own base with nothing pending. A carried certification by Faculty names its `markers_of` (and, since
    2026-11-16, `rows_of`) and `pending`. A REGRADE CARRIES THE CHAIN (requirement, 2026-11-16, of Quiz 8's confirmations left as
    they were before Faculty certified a makeup by itself, then copied by a regrade's certification): a certification of one
    changed grade that carried its markers (`summary.amends.markers`; one made before that date was always live) stands on the chain
    of the certification it amends: its own markers (the regrade's decisions in them) are the base, the chain's rows and pending stay."""
    def load(cid):
        row = db.execute("SELECT * FROM frq_certifications WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None
    cert, through = latest, False
    for _ in range(64):  # a chain of amendments is short; never loop on a broken one
        summary = json.loads(cert["summary_json"] or "{}")
        auto = summary.get("automatic") or {}
        if auto.get("markers") == "carried":
            source = load(auto["markers_of"])
            rows = load(auto.get("rows_of") or auto["markers_of"])
            if source is None or rows is None:
                break
            return (latest if through else source), rows, list(auto.get("pending") or [])
        amends = summary.get("amends")
        if auto or not cert.get("amends_id") or (amends or {}).get("markers") != "carried":
            break
        cert, through = load(cert["amends_id"]), True
        if cert is None:
            break
    return latest, latest, []


class _Changed(Exception):
    """Something was written while this was read: nothing is recorded; the next look reads again."""


def _had(record, part):
    """Whether a certified row has the part as the student's own complete work."""
    return bool(record) and (certified_parts(record).get(part) or {}).get("state") == "complete"


def _split(row):
    section_id, _, enrollment_id = row.partition(":")
    return section_id, enrollment_id


def late_base(record):
    """Whether a certified row gave the student no grade of their own work (or there is no row)."""
    if record is None:
        return True
    if record["reason"] in LATE.get(record["status"], ()):
        return True
    return record["status"] == "scored" and record["reason"] == "missing_part"


def makeup_change(base, student):
    """MAKEUP CHANGES CERTIFY THEMSELVES: what a makeup that ended or began since the certification makes of a student with no
    work, or None. `zero`: certified left out until their makeup, now 0 (GRADING "MAKEUP OWED": the absence claims nothing any more,
    or the makeup was recorded missed). `left_out`: certified 0 with no work, now left out until their makeup."""
    if not base:
        return None
    if base["status"] == "left_out" and base["reason"] == "makeup" and student.get("status") == "zero":
        return "zero"
    if base["status"] == "zero" and base["reason"] in LATE["zero"] and student.get("status") == "left_out" and student.get("reason") == "makeup":
        return "left_out"
    return None


def cleared(projection):
    """A final grade is cleared unless it is still flagged for review: the instructor's own review decided it, or it carries no flag
    (a check the review policy waived is no flag), or a reviewing model settled its flags (the arbitrator or the flag reviewer,
    `rules.settled`), or a flag review of its question ruled on it. A waiting grade Faculty approved on a plan graded for
    participation (GRADING "THE WAITING GRADES APPROVE THEMSELVES") is cleared as a review is (requirement, 2026-11-11): its flags cannot change the full credit it counts as."""
    if projection.get("review_status") in ("approved", "manual"):
        return True
    held = set(projection.get("flags") or []) - {"waived"}
    return not held or bool(projection.get("settled")) or projection.get("flag_resolution") in ("adopted", "revised")


def forecast(state, held=(), unclaimed=False):
    """What Faculty is about to certify by itself, for the page (requirement, 2026-11-14: the page asked them to certify again
    during the few minutes Faculty waits for the work to settle): {papers: [row], changes: {row: zero|left_out|gone}} from the
    page's own provisional read, by the rule `_find` applies exactly afterwards. `held`: the rows already recorded as waiting with
    a reason. `unclaimed`: a paper or sheet of the scans has no student yet, so no 0 is promised. Empty while the graded parts
    changed or nothing was certified. The exact look may still hold a row (a typed value, a score in the gradebook); it is then
    recorded as waiting and leaves this list."""
    latest = state.get("latest")
    if not latest or state["totals"]["parts"] != json.loads(latest["parts_json"]):
        return {"papers": [], "changes": {}}
    answers = {}
    for rid, entry in state["decisions"].items():
        answers.setdefault((entry["section_id"], entry["enrollment_id"]), []).append((rid, entry))
    parts, held = state["totals"]["parts"], set(held)
    dropped = {_row(p["section_id"], p["enrollment_id"]) for p in state["participants"] if p.get("dropped")}
    papers, changes = [], {}
    for s in state["students"]:
        row = s["row"]
        base = state["certified_rows"].get(row)
        if row in held:
            continue
        change = makeup_change(base, s)
        if change:
            if not (change == "zero" and unclaimed):
                changes[row] = change
        elif late_base(base) and s["status"] == "scored" and not s["reason"]:
            missing = [n for n in parts if (certified_parts(base).get(n) or {}).get("state") != "complete"] if base else list(parts)
            mine = answers.get((s["section_id"], s["enrollment_id"]), [])
            if any(rules.part_work(n, (s["parts"] or {}).get(n), s["collection"], s.get("sheets", 0)) for n in missing) \
                    and all(entry["kind"] and cleared(state["projection"].get(rid) or {}) for rid, entry in mine):
                papers.append(row)
    live = {s["row"] for s in state["students"]}
    for row, base in state["certified_rows"].items():
        if row not in live and row in dropped and base["status"] == "left_out" and base["reason"] == "makeup":
            changes[row] = "gone"
    return {"papers": papers, "changes": changes}


def _holds(cell):
    """What a gradebook cell holds that Faculty's own 0 must not replace, in words, or None: a score above 0, or a mark that is
    no score (excused, missing). An empty or cleared cell and a 0 hold nothing."""
    if not cell or cell.get("status") == "cleared":
        return None
    if cell.get("status") == "scored":
        return f"they have {cell['points']} for it in the gradebook" if (finite_decimal(cell.get("points")) or 0) > 0 else None
    return f"their gradebook cell is marked {cell.get('status')}"


def _part_view(name, part):
    return json.dumps(part_content(name, part), sort_keys=True, default=str)


class LateCertification:
    def __init__(self, certification):
        self.service = certification
        self.frq, self.gradebook, self.gradescope = certification.frq, certification.gradebook, certification.gradescope
        self.rosters, self.path = certification.rosters, certification.path

    # ----- who is late ----------------------------------------------------------------------------------------------------------
    def _find(self, state, standing=()):
        """{late: [row], changes: {row: zero|left_out|gone}, waiting: [{row, reason}], wait: plan-wide reason or None}. `standing`:
        the batch lines still in force (`batch_adjustments.standing_lines`). `changes`: MAKEUP CHANGES CERTIFY THEMSELVES."""
        waits = [c["title"] for c in state["checks"] if c["level"] == "block" and c["id"] in WAIT]
        if waits:
            return {"late": [], "changes": {}, "waiting": [], "wait": waits[0]}
        named, unclaimed = {}, ""
        for c in state["checks"]:
            if c["level"] == "block" or (c["level"] == "confirm" and c["id"] not in CARRIED):
                for r in c.get("rows") or []:
                    if r.get("row"):
                        # The student's own line of the check (2026-10-09 review: a whole-plan title with its count read as theirs).
                        named.setdefault(r["row"], []).append((c["id"], r.get("detail") or c["title"]))
            if c["id"] == "zero_no_work":
                unclaimed = c.get("warning") or ""  # a paper or sheet of this assignment's scans with no student yet
        records = {_row(r["section_id"], r["enrollment_id"]): r for r in state["records"]}
        decided = {d["row"]["id"]: d for d in state["final"][1]}
        questions = {q["question_id"]: q for q in state["questions"]}
        skipped = {s["response_id"] for s in state["final"][2]}
        mine = {}
        for r in state["responses"]:
            mine.setdefault((r["section_id"], r["enrollment_id"]), []).append(r["id"])
        dropped = {(p["section_id"], p["enrollment_id"]) for p in state["participants"] if p.get("dropped")}
        parts = state["totals"]["parts"]
        late, waiting, changes = [], [], {}
        for s in state["students"]:
            row, key = s["row"], (s["section_id"], s["enrollment_id"])
            base = state["certified_rows"].get(row)
            change = makeup_change(base, s)
            if change:
                # Listed by the check of this very change, which Faculty certifies; any other check that names them waits for the instructor.
                held = next((detail for check_id, detail in named.get(row, ()) if check_id != OWN_CHECK[change]), None)
                if not held and change == "zero" and unclaimed:
                    held = f"Not given 0 yet: {unclaimed}"
                if held:
                    waiting.append({"row": row, "reason": held})
                else:
                    changes[row] = change
                continue
            if not late_base(base):
                continue
            missing = [n for n in parts if (certified_parts(base).get(n) or {}).get("state") != "complete"] if base else list(parts)
            if not any(rules.part_work(n, (s["parts"] or {}).get(n), s["collection"], s.get("sheets", 0)) for n in missing):
                continue  # nothing of theirs has come: what was certified stands
            responses = mine.get(key, [])
            reason = None
            if key in dropped:
                reason = "Dropped from the roster"
            elif row in named:
                reason = named[row][0][1]
            elif s["status"] != "scored" or s["reason"] or row not in records:
                reason = s.get("detail") or "Not every part is graded yet"
            elif any(rid not in decided or rid in skipped for rid in responses):
                reason = "Not every question is graded yet"
            elif not all(cleared(state["projection"].get(rid) or {}) for rid in responses):
                reason = "Still flagged for review"
            elif standing and (missing_line := self._missing_line(responses, s["section_id"], decided, questions, standing)):
                reason = f"A line you added to every grade of {missing_line} is not on this paper; certify it yourself."
            elif base and any(_part_view(n, certified_parts(base).get(n)) != _part_view(n, certified_parts(records[row]).get(n)) for n in parts if _had(base, n)):
                reason = "A part certified before has changed: certify the assignment again"
            if reason:
                waiting.append({"row": row, "reason": reason})
            else:
                late.append(row)
        # Left out until their makeup, then dropped with no work: no row of the live grades (a dropped student is never a confirmed 0).
        live = {s["row"] for s in state["students"]}
        for row, base in state["certified_rows"].items():
            if row not in live and base["status"] == "left_out" and base["reason"] == "makeup" and _split(row) in dropped:
                changes[row] = "gone"
        return {"late": late, "changes": changes, "waiting": waiting, "wait": None}

    @staticmethod
    def _missing_line(responses, section_id, decided, questions, standing):
        """The title of a question whose standing batch line ("Add a line to every grade…", in the student's section or every one)
        this student's final grade lacks and would move by, or None (2026-10-09 review: a misprint line never reached a late
        paper, which Faculty then certified, posted and sent at the lower score). Faculty adds only a full-credit line itself
        (`batch_adjustments.reach_full_credit`): for any other the student waits for the instructor. A line the question's bounds would hold changes nothing and holds nothing up."""
        for rid in responses:
            decision = decided.get(rid)
            if not decision:
                continue
            qid = decision["row"]["question_id"]
            lines = [line for line in standing if line["question_id"] == qid and line["section_id"] in (batch_adjustments.EVERY, section_id)]
            if not lines:
                continue
            question = questions.get(qid) or {}
            title = (question.get("content") or {}).get("title") or question.get("question_key") or "this question"
            if not question.get("rubric"):
                return title
            for line in lines:
                _, why = batch_adjustments.change(decision["final"], question["rubric"]["rules"], question["content"]["max_points"], line)
                if why is None or why[0] not in ("already", "bounded"):
                    return title
        return None

    # ----- live or carried ------------------------------------------------------------------------------------------------------
    def _as_base(self, row, record, state, students):
        """The late student as the base certification classified them, for the student checks' members (None: no row then)."""
        if record is None:
            return None
        s = students.get(row) or {}  # none: a student left out until their makeup who dropped (`gone`)
        parts = certified_parts(record)
        return {"row": row, "status": record["status"], "reason": record["reason"], "dr_date": record["dr_date"], "dr": s.get("dr") or [], "makeup": s.get("makeup"),
                "missing_parts": [n for n in state["totals"]["parts"] if (parts.get(n) or {}).get("state") == "zero"] if record["reason"] == "missing_part" else []}

    def _confirmations(self, state, latest, base_rows, late_all):
        """The confirmations a LIVE certification keeps: every check the instructor confirmed, either unchanged or implied by what they
        confirmed (its members now, with the late students as they were then, are exactly the confirmed ones). None when one is not.
        The late students are counted as they were whatever they are now: a late paper has left the lists, and a student of MAKEUP
        CHANGES CERTIFY THEMSELVES is in one for the very change Faculty certifies (a check nobody confirmed that lists only them
        is implied too). What is kept names the lists as they are now, so the next comparison starts from this certification."""
        stored = json.loads(latest["confirmations_json"] or "{}")
        students = {s["row"]: s for s in state["students"]}
        others = [s for s in state["students"] if s["row"] not in late_all]
        live = {c["id"]: c for c in state["checks"] if c["level"] == "confirm" and c["id"] not in UNSTORED}
        earlier = [b for b in (self._as_base(row, base_rows.get(row), state, students) for row in sorted(late_all)) if b]
        # A DROP NEVER STOPS IT: the dropped stay named by what is kept, as their rows stay, so a later drop or change reads it alike.
        gone = as_they_were({row: r for row, r in dropped_no_work(state).items() if row not in late_all})
        kept = dict(stored)
        for check_id in sorted(set(live) | {k for k in stored if k not in UNSTORED}):
            entry, check = stored.get(check_id), live.get(check_id)
            if check and entry and entry.get("digest") == check.get("digest"):
                continue
            if check_id not in rules.STUDENT_CHECKS:
                return None
            standing = rules.student_members(check_id, gone)
            was = rules.student_members(check_id, others)[1] + standing[1] + rules.student_members(check_id, earlier)[1]
            if not entry:
                if was:
                    return None
            elif rules.members_digest(was) != entry.get("digest") and not self._makeup_implied(check_id, entry, others, earlier):
                return None
            members = (rules.student_members(check_id, state["students"])[1] if check else []) + standing[1]
            if check or standing[1]:
                kept[check_id] = {"digest": rules.members_digest(members), "count": (check or {}).get("count", 0) + len(standing[0]),
                                  **({"rows": [r.get("row") for r in [*(check or {}).get("rows", []), *standing[0]]]} if check_id in ROW_CHECKS else {})}
            else:
                kept.pop(check_id, None)
        return kept

    @staticmethod
    def _makeup_implied(check_id, entry, others, earlier):
        """Whether "Leave these students out until their makeup" is implied although its digest moved: the same students, now plus
        the late ones as they were (`others`: everyone but the late students), when only an absence's decision moved (MAKEUP OWED: undecided at Certify, excused afterwards
        reopens nothing). Its members spell the decision (`row|absence|decision`), so the rows are compared instead; the manual
        Certify's digest is unchanged. Whether each one left out is still covered is `_live`'s `makeups.covering` (an absence not
        excused, withdrawn or moved reads `makeup_ended`). A stored row list cut at the cap is never taken as complete."""
        if check_id != "makeup_ahead" or entry.get("rows") is None or entry.get("count") != len(entry["rows"]):
            return False
        now = {s["row"] for s in rules.student_members(check_id, others)[0]}
        was = {b["row"] for b in rules.student_members(check_id, earlier)[0]}
        return set(entry["rows"]) == now | was

    def _live(self, db, state, composed, latest, base, base_rows, late_all):
        """Whether the plan, these late students certified, is exactly what the base certified (see the module docstring)."""
        plan, records = state["plan"], {_row(r["section_id"], r["enrollment_id"]): r for r in state["records"]}
        if latest["rule"] != progress.RULE or any(c["level"] == "block" for c in state["checks"]):
            return False
        if any(state["workflow"].get(k) for k in ("new_responses", "decision_responses", "awaiting_review", "pending")):
            return False
        if any(row_content(records.get(k)) != row_content(composed.get(k)) for k in (set(records) | set(composed)) - set(dropped_no_work(state))):
            return False
        keys = {_split(row) for row in late_all}
        papers_late = {row for row in late_all if not (_had(base_rows.get(row), "frq") or _had(base_rows.get(row), "exams"))}
        decided = {rid: e for rid, e in state["decisions"].items()
                   if not ((e["section_id"], e["enrollment_id"]) in keys and not _had(base_rows.get(_row(e["section_id"], e["enrollment_id"])), "frq"))}
        if digest(progress.decision_state(db, plan["id"], state["responses"], decided)) != base["decisions_sha256"]:
            return False
        expectations = {(p["section_id"], p["enrollment_id"]): p["expectation"] for p in state["participants"]}
        papers, override = [], {}
        for paper in state["collection"]["students"]:
            key = (paper["section_id"], paper["enrollment_id"])
            row = _row(*key)
            record = base_rows.get(row)
            if row in late_all and record is None:
                override[key] = None  # a participant added since the base
                continue
            if row in papers_late:
                expected = "absent" if record["reason"] == "absent" else expectations.get(key, "expected")
                if expected != expectations.get(key):
                    override[key] = expected
                paper = {**paper, "status": expected if expected != "expected" else "no_scan", "assembly_revision_id": None}
            papers.append(paper)
        if progress.participants_changed(db, plan, papers, json.loads(base["parts_json"]), base, override=override):
            return False
        without = frozenset(enrollment for section, enrollment in keys if not _had(base_rows.get(_row(section, enrollment)), "bubble"))
        if progress.bubble_marker(db, plan, without=without) != base["bubble_sha256"]:
            return False
        if any(r["status"] == "left_out" and r["reason"] == "dr_ahead" and r["dr_date"] and r["dr_date"] < state["today"] for r in composed.values()):
            return False
        waiting = [_split(row) for row, r in composed.items() if r["status"] == "left_out" and r["reason"] == "makeup"]
        if waiting and set(waiting) - set(makeups.covering(db, plan["term_id"], plan["event_id"], plan["source_assignment_id"], waiting)):
            return False
        return self._confirmations(state, latest, base_rows, late_all) is not None

    # ----- Gradescope -----------------------------------------------------------------------------------------------------------
    def _gradescope(self, db, plan, late, records, certified):
        """{row: {written|sheets: {send, papers_only, why}}} for each late student's NEW part: sent the way this assignment was sent to
        Gradescope in their section (papers and grades, or papers only), else not, with why."""
        configured = bool(self.gradescope.settings.status().get("configured"))
        written = {t["section_id"]: t for t in self.gradescope._targets(db, plan)}
        sheets = {t["section_id"]: t for t in self.gradescope._sheet_targets(db, plan)}
        found = {}
        for row in late:
            section, _ = _split(row)
            parts, entry = certified_parts(records[row]), {}
            if (parts.get("frq") or {}).get("state") == "complete" and not _had(certified.get(row), "frq"):
                entry["written"] = self._uses(db, plan["id"], section, configured, written.get(section), "written")
            if (parts.get("bubble") or {}).get("state") == "complete" and not _had(certified.get(row), "bubble"):
                entry["sheets"] = self._uses(db, plan["id"], section, configured, sheets.get(section), "sheets")
            if entry:
                found[row] = entry
        return found

    @staticmethod
    def _uses(db, aid, section, configured, target, kind):
        if not configured:
            return {"send": False, "papers_only": False, "why": "Gradescope is not set up."}
        if not target or not target.get("container_id"):
            return {"send": False, "papers_only": False, "why": "This section has no Gradescope assignment linked for it."}
        if kind == "sheets":
            sent = db.execute("SELECT 1 FROM frq_gradescope_sheet_uploads WHERE assignment_id=? AND section_id=? AND status='accepted' LIMIT 1",
                              (aid, section)).fetchone()
            return {"send": bool(sent), "papers_only": False, "why": "" if sent else "No bubble sheet of this section was sent to Gradescope."}
        papers = db.execute("SELECT 1 FROM frq_gradescope_uploads WHERE assignment_id=? AND section_id=? AND status='accepted' LIMIT 1", (aid, section)).fetchone()
        if not papers:
            return {"send": False, "papers_only": False, "why": "No paper of this section was sent to Gradescope."}
        grades = db.execute("""SELECT 1 FROM frq_gradescope_grades g JOIN frq_gradescope_uploads u ON u.id=g.upload_id WHERE g.assignment_id=? AND u.section_id=?
            AND g.status='accepted' LIMIT 1""", (aid, section)).fetchone()
        return {"send": True, "papers_only": not grades, "why": "" if grades else "Only papers were sent to Gradescope for this section."}

    # ----- certify them ---------------------------------------------------------------------------------------------------------
    def run(self, aid, actor=ACTOR):
        """Certify the plan's late students, if any. Returns {state: none|wait|waiting|done|replay|changed, ...}: `wait` names a plan-wide
        reason (nothing is recorded for it), `waiting` the late papers that wait for something and why, `done` the certification."""
        with self.rosters.lock():
            with connect(self.path) as db:
                plan = self.frq.plan(db, aid)
                if not progress.latest_certification(db, aid):
                    return {"state": "none"}
                marks = self.service._marks(db, plan)
                standing = batch_adjustments.standing_lines(self.service._adjustment_receipts(db, aid))
            state = self.service._evaluate(aid, gradebook=False)
            latest = state["latest"]
            if state["totals"]["parts"] != json.loads(latest["parts_json"]):
                return {"state": "waiting", "late": [], "waiting": [], "reason": "The assignment's graded parts changed since it was certified: certify it again."}
            found = self._find(state, standing)
            if found["wait"]:
                return {"state": "wait", "reason": found["wait"]}
            # MAKEUP CHANGES CERTIFY THEMSELVES: `late` is every row this certification takes as it is now (a late paper, or a
            # makeup's 0 or leaving out); `gone` the rows it no longer keeps; `changes` says which of them is which.
            changes, waiting = dict(found["changes"]), found["waiting"]
            gone = sorted(row for row, kind in changes.items() if kind == "gone")
            late = found["late"] + [row for row, kind in changes.items() if kind != "gone"]
            if not late and not gone:
                return {"state": "waiting", "late": [], "waiting": waiting}
            records = {_row(r["section_id"], r["enrollment_id"]): r for r in state["records"]}
            participants = {(p["section_id"], p["enrollment_id"]) for p in state["participants"]}
            if any(_split(row) not in participants for row in state["certified_rows"]):
                return {"state": "waiting", "late": [], "waiting": waiting,
                        "reason": "A student certified before is no longer part of this exam: certify the assignment again."}
            context, item_id = state["context"], (json.loads(latest["gradebook_json"] or "{}") or {}).get("item_id")

            def options(db, rows, selected=None):
                return self.gradebook.certification_options(db, context, assignment_id=plan["source_assignment_id"], plan_id=plan["id"],
                                                            event_id=plan["event_id"], title=plan["title"], records=rows,
                                                            certified_parts=state["totals"]["parts"], exam_sections=state["exam_sections"],
                                                            replace=set(), selected=selected, category_id=None)
            with connect(self.path) as db:
                for _ in range(2):  # a typed value or a section outside the column takes that student out; plan the others again
                    book = options(db, [records[row] for row in late])
                    option = next((o for o in book["options"] if o["item"]["id"] == item_id and not o["writes_setup"]), None)
                    if option is None:
                        return {"state": "waiting", "late": [], "waiting": waiting + [{"row": row, "reason": "The gradebook column the grades were certified into changed"}
                                                                                      for row in late]}
                    out = {r["row"]: "A value you typed in the gradebook stays" for r in option["kept"]}
                    out.update({row: "Outside the gradebook column's sections" for row in option["outside"]})
                    # A SCORE STANDS (requirement, 2026-11-14): Faculty's own 0 goes only into a cell that is empty or already 0, never over a score
                    # above 0 or an excused or missing mark, wherever it came from (an import or a pull would be replaced).
                    for planned in option["plan"]["rows"]:
                        if changes.get(planned["row"]) == "zero" and (held := _holds(planned.get("current"))):
                            out.setdefault(planned["row"], f"Not given 0: {held}; certify it yourself.")
                    if not out.keys() & set(late):
                        break
                    waiting += [{"row": row, "reason": out[row]} for row in late if row in out]
                    late = [row for row in late if row not in out]
                    if not late and not gone:
                        return {"state": "waiting", "late": [], "waiting": waiting}
                else:
                    return {"state": "waiting", "late": [], "waiting": waiting}
                changes = {row: kind for row, kind in changes.items() if kind == "gone" or row in late}
                certified = {row: {**r, "parts_json": certified_parts(r)} for row, r in state["certified_rows"].items() if row not in gone}
                composed = {**certified, **{row: records[row] for row in late}}
                auto = json.loads(latest["summary_json"] or "{}").get("automatic") or {}
                base, rows_base, pending = carried_chain(db, latest)
                base_rows = {_row(r["section_id"], r["enrollment_id"]): r for r in sources.certified_records(db, rows_base["id"])}
                late_all = set(pending) | set(late) | set(gone)
                live = self._live(db, state, composed, latest, base, base_rows, late_all)
                confirmations = self._confirmations(state, latest, base_rows, late_all) if live else json.loads(latest["confirmations_json"] or "{}")
                sends = self._gradescope(db, plan, late, records, state["certified_rows"])
            rows = sorted(composed.values(), key=lambda r: (r["section_id"], r["enrollment_id"]))
            summary = rules.summary(rows)
            taken = sorted(set(late) | set(gone))
            since = (auto.get("since_instructor") or []) if latest["actor"] == ACTOR else []
            # Which of the rows Faculty certified since the instructor's Certify were a makeup's change, by its latest kind (a
            # student left out by itself whose makeup paper then came is a late paper).
            since_changes = {row: kind for row, kind in (auto.get("since_changes") or {}).items() if row not in late} if latest["actor"] == ACTOR else {}
            since_changes.update(changes)
            summary["automatic"] = {"rows": taken, "markers": "live" if live else "carried", "markers_of": None if live else base["id"],
                                    **({} if live or rows_base["id"] == base["id"] else {"rows_of": rows_base["id"]}),
                                    "pending": [] if live else sorted(late_all), "since_instructor": sorted(set(since) | set(taken)),
                                    "waiting": [w["row"] for w in waiting],
                                    **({"changes": changes} if changes else {}), **({"since_changes": since_changes} if since_changes else {})}
            contents = [row_content(records.get(row)) for row in taken]
            request_id = str(uuid.uuid5(NAMESPACE, f"auto-certify:{latest['id']}:{','.join(taken)}:{digest(contents)}"))
            request_sha = digest({"latest": latest["id"], "late": taken, "records": contents})
            if live:
                checks_record = [{k: c.get(k) for k in ("id", "level", "title", "count", "digest")} for c in state["checks"] if c["id"] not in UNSTORED] \
                    + [c for c in json.loads(latest["checks_json"] or "[]") if c.get("id") in UNSTORED]
                markers = state["markers"]
            else:
                checks_record = json.loads(latest["checks_json"] or "[]")
                markers = {"decisions": latest["decisions_sha256"], "bubble": latest["bubble_sha256"], "participants": latest["participants_sha256"]}
            gradescope = {**json.loads(latest["gradescope_json"] or "{}"), "automatic": sends}
            cid, now = uid(), timestamp()
            after_on = bool(self.frq.settings()["settings"].get(gradescope_after.SWITCH, True))  # read before the transaction
            try:
                with connect(self.path) as db:
                    prior = db.execute("SELECT * FROM frq_certifications WHERE assignment_id=? AND request_id=?", (aid, request_id)).fetchone()
                    if prior:
                        return {"state": "replay", "certification_id": prior["id"]}
                    db.execute("BEGIN IMMEDIATE")
                    if self.service._marks(db, plan) != marks or (progress.latest_certification(db, aid) or {}).get("id") != latest["id"]:
                        raise _Changed()
                    summary["grade_marker"] = progress.grade_marker(db, aid, state["totals"]["parts"]) if live else \
                        json.loads(latest["summary_json"] or "{}").get("grade_marker")
                    prepared = self.gradebook.prepare_certification(db, context, term_id=plan["term_id"], option=option, revision=book["revision"],
                                                                    cells=option["cells"], request_id=f"certify:{request_id}")
                    db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
                        participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,supersedes_id,actor,created_at,amends_id)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (cid, aid, request_id, request_sha, request_sha, progress.RULE, latest["parts_json"], markers["decisions"], markers["bubble"],
                                markers["participants"], rules.snapshot_digest(rows), encoded(summary), encoded(checks_record), encoded(confirmations),
                                encoded(gradescope), encoded(prepared["receipt"]), latest["id"], actor, now, None if live else latest["id"]))
                    for r in rows:
                        db.execute("""INSERT INTO frq_certification_students(certification_id,section_id,enrollment_id,status,reason,earned,possible,exam_version,parts_json,dr_date)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""", (cid, r["section_id"], r["enrollment_id"], r["status"], r["reason"], r["earned"], r["possible"],
                                                           r["exam_version"], encoded(certified_parts(r)), r["dr_date"]))
                    stored_rows = [r for r in sources.certified_records(db, cid) if _row(r["section_id"], r["enrollment_id"]) in set(late)]
                    stored = next((o for o in options(db, stored_rows, option["key"])["options"] if o["key"] == option["key"]), None)
                    if stored is None:
                        raise _Changed()
                    receipt = self.gradebook.record_certification(db, context, prepared=prepared, option=option, stored=stored, request_sha=request_sha,
                                                                  certification_id=cid, actor=actor)
                    queued = 0
                    for row, entry in sorted(sends.items()):
                        section, enrollment = _split(row)
                        for kind, decision in sorted(entry.items()):
                            if decision["send"]:
                                db.execute("""INSERT INTO frq_gradescope_automatic_sends(id,certification_id,assignment_id,section_id,enrollment_id,kind,papers_only,status,
                                    attempts,error,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'pending',0,'',?,?,?)""",
                                           (uid(), cid, aid, section, enrollment, kind, int(decision["papers_only"]), actor, now, now))
                                queued += 1
                    # GRADESCOPE "AFTER CERTIFYING": once the late papers are there, Faculty publishes the version they went to where
                    # the instructor had already published its set. Only for a written paper it sends, and only while the switch is on.
                    published = sorted(row for row, entry in sends.items() if (entry.get("written") or {}).get("send"))
                    if published and after_on:
                        gradescope_after.queue_run(db, cid, aid, "late", {"rows": published}, actor, now)
                    self.frq.event(db, aid, "grades_certified", {"certification_id": cid, "automatic": {k: summary["automatic"][k] for k in ("rows", "markers", "changes")
                                                                                                         if k in summary["automatic"]},
                                                                 "gradebook": {k: receipt[k] for k in ("item_id", "written", "kept", "created_column")},
                                                                 "gradescope": queued}, actor)
            except (_Changed, Conflict):
                return {"state": "changed"}
        if queued:
            self.gradescope.wake.set()
        return {"state": "done", "certification_id": cid, "late": taken, "changes": changes, "markers": "live" if live else "carried",
                "waiting": waiting, "gradescope": queued}
