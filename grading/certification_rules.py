"""Pure rules of Certification and Export, the sixth grading stage (requirement, 2026-09-26).

No catalog, no lock, no roster payload: `grading/certification.py` (the service) reads everything once and hands it here, so each
rule is unit-tested on plain values. Nothing here decides a grade: final grades are `final_decisions`' (the export's ONE rule) and
the bubble key's; this module only counts them, classifies each participant, lists what the instructor must finish or confirm,
and builds the immutable snapshot a certification records.

Shapes (the interface with the service and the page; GRADING.md "Certification and Export" has the JSON):

* `Stats` = {n, mean, median, sd, min, max, possible, histogram: [{from, to, count}] (10 bins of percent of possible, the last
  [90, 100] closed), excluded: {...}}. Numbers are Decimal computations returned as JSON numbers rounded to 2 decimals.
* `Check` = {id, level: block|confirm|info, title, detail, count, digest?, rows?: [{row?, name, section, detail}] (at most
  `ROWS`), action?: {label, href} | {label, kind}, requires?: tick|decision, provisional?, prefill?}.
* A classified student (`classify`) = {status: scored|zero|excused|left_out|blocked, reason, block (the check that names a
  blocked student), view (complete|missing|missing_part|in_progress|excused|dr_ahead|makeup|left_out), detail, missing_parts, dr_date,
  earned, possible}. INVARIANT (critique C9): every participant is exactly one of scored, zero, excused or left_out, or a
  block names them.
"""
import statistics
import uuid
from decimal import ROUND_HALF_UP, Decimal

from gradebook import common

from . import arbitration
from .models import digest, finite_decimal

RULE = "certify-v1"
ROWS = 400  # rows listed per check; the count is always the whole
LEVEL_ORDER = {"block": 0, "confirm": 1, "info": 2}  # checks are listed block first, then confirm, then info
LABELS = {"frq": "Free response", "bubble": "Multiple choice", "exams": "Exams page"}
FINAL = ("approved", "manual", "auto_accepted")
NO_GRADE = ("unreadable", "rejected")  # an instructor decision that records no score (critique C1: never certified as 0)
WORK = ("complete", "partial", "needs_review", "missing_pages")  # collection statuses that mean a paper was received
ARBITER_FLAGS = ("adopted", "revised", "unreliable", "unsettled", "failed")
FLAG_REVIEW_FLAGS = ("finalized", "manual_review", "unsettled", "failed")
ZERO_QUESTION_MIN = 5  # a question needs this many final grades before "every one is 0" says anything
VERSION_GAP = Decimal(10)  # percentage points between two versions' mean totals
VERSION_GAP_MIN = 10  # students per version before a gap is reported
REASON_LIMIT = 500


# ----- numbers ----------------------------------------------------------------------------------------------------------------
dec = finite_decimal


def text(value):
    """Points as the gradebook writes them (4, 2.5, 8.3333), or None."""
    number = dec(value)
    return None if number is None else common.points_text(number)


def rounded(value):
    """A JSON number with at most 2 decimals, or None."""
    number = dec(value)
    return None if number is None else float(number.quantize(Decimal("0.01"), ROUND_HALF_UP))


# ----- statistics -------------------------------------------------------------------------------------------------------------
def histogram(percents):
    """Ten bins of percent of possible: [0,10), [10,20) ... [90,100]; below 0 counts in the first, above 100 in the last."""
    bins = [{"from": 10 * i, "to": 10 * (i + 1), "count": 0} for i in range(10)]
    for percent in percents:
        index = min(9, max(0, int(Decimal(percent) // 10)))
        bins[index]["count"] += 1
    return bins


def stats(pairs, excluded=None):
    """`pairs`: [(earned, possible)] of the students (or responses) counted; each value text, number or Decimal. `sd` is the
    sample standard deviation (n - 1), None when n < 2. The histogram bins each value's percent of its OWN possible (versions may
    differ); a value without a positive possible is counted in the statistics but not in the histogram."""
    values, percents, possibles = [], [], {}
    for earned, possible in pairs:
        earned, possible = dec(earned), dec(possible)
        if earned is None:
            continue
        values.append(earned)
        if possible is not None and possible > 0:
            percents.append(earned * 100 / possible)
            possibles[possible] = possibles.get(possible, 0) + 1
    n = len(values)
    result = {"n": n, "mean": None, "median": None, "sd": None, "min": None, "max": None,
              "possible": rounded(max(possibles.items(), key=lambda kv: (kv[1], kv[0]))[0]) if possibles else None,
              "histogram": histogram(percents), "excluded": dict(excluded or {})}
    if n:
        mean = sum(values, Decimal(0)) / n
        result.update(mean=rounded(mean), median=rounded(statistics.median(values)), min=rounded(min(values)), max=rounded(max(values)))
        if n > 1:
            variance = sum(((v - mean) ** 2 for v in values), Decimal(0)) / (n - 1)
            result["sd"] = rounded(variance.sqrt())
    return result


def score_histogram(scores):
    """[{score: "0", count}] by distinct score value, lowest first (a question's distribution)."""
    counts = {}
    for score in scores:
        number = dec(score)
        if number is not None:
            counts[number] = counts.get(number, 0) + 1
    return [{"score": common.points_text(score), "count": count} for score, count in sorted(counts.items())]


def line_frequencies(selections, items):
    """How often each rubric line is in the FINAL selection: [{id, count, pct}] in the rubric's order. `selections` is one list of
    selected line IDs per final grade; `items` the rubric's lines (dicts with `id`)."""
    total = len(selections)
    counts = {}
    for selected in selections:
        for line in set(selected or []):
            counts[line] = counts.get(line, 0) + 1
    return [{"id": item["id"], "count": counts.get(item["id"], 0),
             "pct": rounded(Decimal(counts.get(item["id"], 0)) * 100 / total) if total else None} for item in items]


def grouped(students, key):
    """Stats of the scored students' totals per value of `key` (e.g. exam_version, section): {value: Stats}."""
    groups = {}
    for student in students:
        if student.get("status") == "scored" and student.get(key) not in (None, ""):
            groups.setdefault(str(student[key]), []).append((student["earned"], student["possible"]))
    return {value: stats(pairs) for value, pairs in sorted(groups.items())}


def total_stats(students):
    """Assignment totals: only students whose every part is complete (status scored, reason ''); confirmed zeros, missing parts,
    the left out and the excused are counted apart, never in `n`."""
    excluded = {"zero": 0, "missing_part": 0, "missing": 0, "left_out": 0, "excused": 0}
    pairs = []
    for student in students:
        status, reason = student.get("status"), student.get("reason")
        if status == "scored" and reason != "missing_part":
            pairs.append((student["earned"], student["possible"]))
        elif status == "scored":
            excluded["missing_part"] += 1
        elif status in ("zero", "left_out", "excused"):
            excluded[status] += 1
        else:
            excluded["missing"] += 1
    return stats(pairs, excluded)


# ----- the models' flags (critique C8) ------------------------------------------------------------------------------------------
def flags(row):
    """The ORIGINAL flags the models raised on the result a grade was decided on (for an instructor review its own result, never a
    newer proposal), as names: reading, grading, finalization, reasons, arbitrator, flag_reviewer, disagreement, risk, waived.
    `row` is the page's one SQL projection of that result: state (as stored), base_state (`rubric_quality.base_state`, the state
    before the finalization's rules), reasons (count), arbiter, flag_review, committee (outcomes/status), risk_blocks (count),
    risk_individual (truthy), risk_waived (truthy: checks waived by the review policy are still recorded)."""
    names = []
    state = row.get("state")
    original = row.get("base_state") or state
    if original in ("ocr_review", "both"):
        names.append("reading")
    if original in ("grading_review", "both"):
        names.append("grading")
    if state and original and state != original:
        names.append("finalization")
    if row.get("reasons"):
        names.append("reasons")
    if row.get("arbiter") in ARBITER_FLAGS:
        names.append("arbitrator")
    if row.get("flag_review") in FLAG_REVIEW_FLAGS:
        names.append("flag_reviewer")
    if row.get("committee") == "unresolved":
        names.append("disagreement")
    if row.get("risk_blocks") or row.get("risk_individual"):
        names.append("risk")
    if row.get("risk_waived"):
        names.append("waived")
    return names


SETTLING = {"arbitrator": ("arbiter", ("adopted", "revised")), "flag_reviewer": ("flag_review", ("finalized",))}


def settled(row):
    """Whether every flag a grade carries was settled by a reviewing model (2026-09-27: the instructor read "over 800 flags" on
    Quiz 2, and 765 of them were grades whose only mark was the arbitrator settling the graders' disagreement or the flag reviewer
    upholding the grade): its flags are only `arbitrator` and/or `flag_reviewer`, and each of those ruled (adopted or revised; upheld
    and finalized). An unreliable or unsettled ruling, a flag reviewer asking for manual review, or any other flag is not settled."""
    names = row.get("flags") or []
    if not names or set(names) - set(SETTLING):
        return False
    return all(row.get(field) in outcomes for name, (field, outcomes) in SETTLING.items() if name in names)


def basis(recorded, row):
    """How an automatic grade was finalized (team_consensus, final_arbitrator, flag_review, ...): `arbitration.basis` over the
    outcomes the SQL projection read, because the acceptance view says team_consensus for arbitrator and flag-reviewer grades."""
    signals = {"arbiter": {"outcome": row.get("arbiter")}, "flag_review": {"outcome": row.get("flag_review")}, "jev": {"outcome": row.get("jev")},
               "choice": {"rule": row.get("choice")}}
    return arbitration.basis(signals, recorded or "team_consensus")


def overridden(status, review_selected, review_adjustments, result_selected, result_adjustments):
    """Whether the instructor's decision differs from the proposal it reviewed: a manual score always does; an approval does when
    its selected lines or its adjustments differ."""
    if status == "manual":
        return True
    if status != "approved":
        return False
    points = lambda adjustments: sorted(text(a.get("points") if isinstance(a, dict) else a) or "0" for a in adjustments or [])
    return sorted(review_selected or []) != sorted(result_selected or []) or points(review_adjustments) != points(result_adjustments)


# ----- questions --------------------------------------------------------------------------------------------------------------
def question_row(question, responses, gradescope=None):
    """One row of the question table. `question`: {question_id, title, printed_number, version, max}. `responses`: that question's
    responses, each {final: bool, score, kind: review|automatic|None, status, basis, flags: [..], settled: bool (`settled`),
    overridden, reviewed: bool (an automatic grade someone looked at)}. `gradescope`: `coverage_detail`'s entry for the question."""
    maximum = dec(question.get("max"))
    final = [r for r in responses if r.get("final")]
    scores = [dec(r.get("score")) for r in final if dec(r.get("score")) is not None]
    automatic = [r for r in final if r.get("kind") == "automatic"]
    count = len(scores)
    full = sum(1 for s in scores if maximum is not None and s >= maximum)
    zero = sum(1 for s in scores if s == 0)
    bases = {}
    for r in automatic:
        bases[r.get("basis") or "team_consensus"] = bases.get(r.get("basis") or "team_consensus", 0) + 1
    summary = stats([(s, maximum) for s in scores])
    coverage = gradescope or {}
    return {"question_id": question["question_id"], "title": question.get("title") or "", "printed_number": question.get("printed_number"),
            "version": question.get("version") or "", "max": text(maximum), "n_final": len(final), "not_final": len(responses) - len(final),
            "mean": summary["mean"], "median": summary["median"], "sd": summary["sd"],
            "pct_full": rounded(Decimal(full) * 100 / count) if count else None, "pct_zero": rounded(Decimal(zero) * 100 / count) if count else None,
            "full": full, "zero": zero, "automatic": len(automatic), "instructor": len(final) - len(automatic), "basis": bases,
            "automatic_unchecked": sum(1 for r in automatic if not r.get("reviewed")),
            "flagged_finalized": sum(1 for r in automatic if r.get("flags") and (r.get("basis") in ("final_arbitrator", "flag_review"))),
            # Still flagged, and settled by a reviewing model (the arbitrator or the flag reviewer), counted apart (2026-09-27).
            "flagged": sum(1 for r in responses if r.get("flags") and not r.get("settled")), "settled": sum(1 for r in responses if r.get("settled")),
            "overridden": sum(1 for r in final if r.get("overridden")),
            "gradescope": {"sent": coverage.get("sent", 0) + coverage.get("same_content", 0), "changed": coverage.get("changed", 0),
                           "not_sent": coverage.get("not_sent", 0), "unsendable": coverage.get("unsendable")}}


# ----- students (critique C9) ---------------------------------------------------------------------------------------------------
def part_work(name, part, collection, sheets=0):
    """Whether a part has ANY work of the student: a received paper (the free-response and Exams-page parts), a sheet matched to
    them (multiple choice), or anything that part already holds. A part with no work may be confirmed as 0; any work may not."""
    if part and part.get("state") not in (None, "none"):
        return True
    if name == "bubble":
        return bool(sheets)
    return collection in WORK


def latest_date(bookings):
    dates = sorted(b["date"] for b in bookings or [] if b.get("date"))
    return dates[-1] if dates else None


def makeup_detail(makeup):
    """A left-out makeup student's detail: their absence and what it means for the grade."""
    if makeup.get("status") == "excused":
        return f"Makeup owed · absence {makeup.get('dates', '')} excused"
    return f"Absence {makeup.get('dates', '')} waiting for your decision · 0 if not excused"


def classify(student, parts, today, maxima=None):
    """Exactly one certified status for a participant, or the block that names them.

    `student`: {row, expectation expected|absent|excused, collection (its `_collection` status), missed_makeup (ABSENCES MAKEUPS
    "MISSED": the instructor recorded their makeup missed; only the 0's detail says so), sheets (sheets matched to them,
    any status), dr: [bookings with `date`], duplicate: None, or {"other_has_work": bool} when their enrollment is also a
    participant through another section, parts: {part: {state complete|waiting|none, earned, possible, detail}} (the gradebook's
    totals shape, `sources.totals` in a preview)}. `parts`: the certified parts. `today`: campus date. `maxima`: {part: Decimal} of
    one paper (`sources.maxima`), the possible of a part confirmed as 0 (critique C15)."""
    maxima = maxima or {}
    own = {name: (student.get("parts") or {}).get(name) or {"state": "none", "earned": None, "possible": None, "detail": ""} for name in parts}
    work = {name: part_work(name, own[name], student.get("collection"), student.get("sheets", 0)) for name in parts}
    dr_date = latest_date(student.get("dr"))
    ahead = bool(dr_date and dr_date >= today)
    expectation = student.get("expectation") or "expected"
    result = {"status": "blocked", "reason": "", "block": None, "view": "in_progress", "detail": "", "missing_parts": [], "dr_date": dr_date,
              "earned": None, "possible": None}
    paper = student.get("collection") in WORK
    if expectation in ("absent", "excused") and (paper or student.get("sheets")):
        which = "excused_with_paper" if paper else "excused_with_sheet"
        return {**result, "block": which, "detail": f"Marked {expectation} on the Exams page, but a {'paper' if paper else 'bubble sheet'} of theirs was received."}
    if expectation == "excused":
        return {**result, "status": "excused", "reason": "excused", "view": "excused", "detail": "Excused on the Exams page"}
    complete = [name for name in parts if own[name]["state"] == "complete"]
    if parts and len(complete) == len(parts):
        earned = sum((dec(own[n]["earned"]) for n in parts), Decimal(0))
        possible = sum((dec(own[n]["possible"]) for n in parts), Decimal(0))
        return {**result, "status": "scored", "view": "complete", "earned": text(earned), "possible": text(possible),
                "detail": "Every part is graded"}
    empty = [name for name in parts if not work[name]]
    if parts and len(empty) == len(parts):
        if (student.get("duplicate") or {}).get("other_has_work"):
            return {**result, "status": "left_out", "reason": "duplicate_membership", "view": "left_out",
                    "detail": "Also in another section of this exam, where their paper is"}
        if ahead:
            return {**result, "status": "left_out", "reason": "dr_ahead", "view": "dr_ahead", "detail": f"DR exam on {dr_date}"}
        makeup = student.get("makeup")
        if makeup:
            # MAKEUP OWED (requirement, 2026-10-07): an excused or undecided absence covers the day it was given; never 0 while it
            # waits. Not excused or withdrawn, it claims nothing, and the next certification gives the 0.
            return {**result, "status": "left_out", "reason": "makeup", "view": "makeup", "detail": makeup_detail(makeup)}
        possible = sum((maxima[n] for n in parts if maxima.get(n) is not None), Decimal(0))
        reason = "absent" if expectation == "absent" else "no_paper"
        detail = "Marked absent" if reason == "absent" else f"DR exam was {dr_date}; no paper found" if dr_date else \
            "Missed the makeup" if student.get("missed_makeup") else "No paper scanned"
        return {**result, "status": "zero", "reason": reason, "view": "missing", "earned": "0", "possible": text(possible), "detail": detail}
    if empty and len(empty) + len(complete) == len(parts):
        if ahead:
            return {**result, "status": "left_out", "reason": "dr_ahead", "view": "dr_ahead", "detail": f"DR exam on {dr_date}"}
        earned = sum((dec(own[n]["earned"]) for n in complete), Decimal(0))
        possible = sum((dec(own[n]["possible"]) for n in complete), Decimal(0)) + sum((maxima[n] for n in empty if maxima.get(n) is not None), Decimal(0))
        detail = "; ".join(f"No {LABELS[n].lower()} part" if n != "bubble" else "No bubble sheet" for n in empty)
        return {**result, "status": "scored", "reason": "missing_part", "view": "missing_part", "missing_parts": empty,
                "earned": text(earned), "possible": text(possible), "detail": detail}
    several = len(parts) > 1
    reasons = [f"{LABELS[n]}: {own[n].get('detail') or own[n]['state']}" if several else (own[n].get("detail") or own[n]["state"])
               for n in parts if own[n]["state"] != "complete"]
    if not parts:
        reasons = ["Nothing to certify"]
    return {**result, "block": "student_incomplete", "detail": "; ".join(reasons)}


def page_counts(students, gradescope_students=None):
    """The page's counts over classified students (each also with `flagged` and `zero_questions` counts and `changed_since`)."""
    gradescope_students = gradescope_students or {}
    return {"participants": len(students),
            "complete": sum(1 for s in students if s["view"] == "complete"),
            "missing": sum(1 for s in students if s["view"] in ("missing", "missing_part")),
            "in_progress": sum(1 for s in students if s["view"] == "in_progress"),
            "flagged": sum(s.get("flagged", 0) for s in students), "settled": sum(s.get("settled", 0) for s in students),
            "zero_somewhere": sum(1 for s in students if s.get("zero_questions")),
            "changed_since": sum(1 for s in students if s.get("changed_since")),
            "dr": sum(1 for s in students if s.get("dr")),
            "excused": sum(1 for s in students if s["view"] == "excused"),
            "not_on_gradescope": sum(1 for s in students if (gradescope_students.get(s["row"]) or {}).get("uncovered"))}


# ----- checks ---------------------------------------------------------------------------------------------------------------------
def members_digest(members):
    return digest(sorted(str(m) for m in members))


def check(id, level, title, detail="", *, rows=None, count=None, members=None, action=None, requires=None, **extra):
    """One check. A confirm check's `digest` covers its members (row keys by default), so the certify POST can prove the instructor
    confirmed exactly this list."""
    rows = list(rows or [])
    value = {"id": id, "level": level, "title": title, "detail": detail, "count": len(rows) if count is None else count}
    if rows:
        value["rows"] = rows[:ROWS]
    if level == "confirm":
        value["digest"] = members_digest(members if members is not None else [r.get("row") or r.get("id") for r in rows])
        value["requires"] = requires or "tick"
    if action:
        value["action"] = action
    value.update(extra)
    return value


def person(student, detail=None):
    return {"row": student.get("row"), "name": student.get("name", ""), "section": student.get("section", ""),
            "detail": student.get("detail", "") if detail is None else detail}


def plural(count, word, many=None):
    return f"{count:,} {word if count == 1 else many or word + 's'}"


# The confirm checks whose members are students (their digests name each student with what the instructor confirms about them).
STUDENT_CHECKS = ("zero_no_work", "zero_missing_part", "dr_ahead", "makeup_ahead", "duplicate_membership", "bubble_frq_version_mismatch")


def student_members(check_id, students):
    """(the students a student check lists, their member strings): the one place each check's members are spelled, so the
    certification and Faculty's own certification of late papers (LATE PAPERS CERTIFY THEMSELVES) digest them alike."""
    if check_id == "zero_no_work":
        chosen = [s for s in students if s.get("status") == "zero"]
        return chosen, [f"{s['row']}|{s['reason']}" for s in chosen]
    if check_id == "zero_missing_part":
        chosen = [s for s in students if s.get("status") == "scored" and s.get("reason") == "missing_part"]
        return chosen, [f"{s['row']}|{','.join(s.get('missing_parts') or [])}" for s in chosen]
    if check_id == "dr_ahead":
        chosen = [s for s in students if s.get("status") == "left_out" and s.get("reason") == "dr_ahead"]
        return chosen, [f"{s['row']}|{s['dr_date']}|{','.join(sorted(b.get('booking_id') or '' for b in s.get('dr') or []))}" for s in chosen]
    if check_id == "makeup_ahead":
        chosen = [s for s in students if s.get("status") == "left_out" and s.get("reason") == "makeup"]
        return chosen, [f"{s['row']}|{(s.get('makeup') or {}).get('absence_id', '')}|{(s.get('makeup') or {}).get('status', '')}" for s in chosen]
    if check_id == "duplicate_membership":
        chosen = [s for s in students if s.get("status") == "left_out" and s.get("reason") == "duplicate_membership"]
        return chosen, [s.get("row") for s in chosen]
    if check_id == "bubble_frq_version_mismatch":
        chosen = [s for s in students if s.get("frq_version") and s.get("bubble_version") and s["frq_version"] != s["bubble_version"]]
        return chosen, [f"{s['row']}|{s['frq_version']}|{s['bubble_version']}" for s in chosen]
    raise ValueError(f"Not a student check: {check_id}")


def _student_checks(facts, href):
    students = facts.get("students") or []
    found = []
    by_block = {}
    for student in students:
        if student.get("block"):
            by_block.setdefault(student["block"], []).append(student)
    for block, title, detail, action in (
            ("excused_with_paper", "Papers of students marked absent or excused",
             "A paper was received for a student the Exams page marks absent or excused: an excused student is never scored silently, nor given 0.",
             {"label": "Open collection", "href": f"#exams/{facts.get('event_id')}?tab=collection"}),
            ("excused_with_sheet", "Bubble sheets of students marked absent or excused",
             "A bubble sheet is matched to a student the Exams page marks absent or excused.", {"label": "Open bubble sheets", "href": f"{href}?tab=bubble"}),
            ("student_incomplete", "Students without every grade",
             "Each of these students is missing a grade that is not theirs to confirm as 0: finish what the reason names.", None)):
        members = by_block.get(block) or []
        if members:
            found.append(check(block, "block", f"{title} ({len(members):,})", detail, rows=[person(s) for s in members], action=action))
    zero, members = student_members("zero_no_work", students)
    if zero:
        hint = facts.get("unclaimed") or {}
        pieces = [plural(n, word, many) for n, word, many in ((hint.get("unmatched_sheets", 0), "unmatched bubble sheet", None),
                                                              (hint.get("unassigned_papers", 0), "received paper without a student", "received papers without a student"),
                                                              (hint.get("unregistered_first_pages", 0), "unregistered first page", None)) if n]
        found_count = sum(hint.get(k, 0) for k in ("unmatched_sheets", "unassigned_papers", "unregistered_first_pages"))
        warning = ("; ".join(pieces) + f" {'is' if found_count == 1 else 'are'} in this assignment's scans: one may be theirs.") if pieces else ""
        found.append(check("zero_no_work", "confirm", f"{plural(len(zero), 'student')} with no work {'receives' if len(zero) == 1 else 'receive'} 0",
                           "No paper and no bubble sheet. Excused students are never given 0; mark a student excused on the Exams page instead.",
                           rows=[person(s) for s in zero], members=members, warning=warning))
    partial, members = student_members("zero_missing_part", students)
    sheets_unclaimed = (facts.get("unclaimed") or {}).get("unmatched_sheets", 0)
    if partial:
        found.append(check("zero_missing_part", "confirm", f"{plural(len(partial), 'student')} with a part missing: that part counts 0",
                           "One part is graded and another has no work at all (no bubble sheet, or a sheet but no paper).",
                           rows=[person(s) for s in partial], members=members,
                           warning=(f"{plural(sheets_unclaimed, 'bubble sheet')} {'is' if sheets_unclaimed == 1 else 'are'} on no student of this exam: one may be theirs."
                                    if sheets_unclaimed and any("bubble" in (s.get("missing_parts") or []) for s in partial) else "")))
    ahead, members = student_members("dr_ahead", students)
    if ahead:
        rows = []
        for s in ahead:
            bookings = s.get("dr") or []
            detail = f"DR exam on {s['dr_date']}" + (f" · {len(bookings)} bookings" if len(bookings) > 1 else "")
            rows.append({**person(s, detail), "dr": [{k: b.get(k) for k in ("date", "start_time", "end_time", "campus", "exam_type")} for b in bookings]})
        found.append(check("dr_ahead", "confirm", f"{plural(len(ahead), 'DR student')} left out until their exam",
                           "Their DR exam is today or later and no paper was found. They are not recorded now; the step reopens once the date has passed.",
                           rows=rows, members=members))
    owed, members = student_members("makeup_ahead", students)
    if owed:
        found.append(check("makeup_ahead", "confirm", f"{plural(len(owed), 'student')} left out until their makeup",
                           "An excused or undecided absence covers the day it was given. Nothing is recorded for them now. "
                           "Mark the absence not excused and they count 0; certifying again records it.",
                           rows=[person(s) for s in owed], members=members))
    duplicate, _ = student_members("duplicate_membership", students)
    if duplicate:
        found.append(check("duplicate_membership", "confirm", f"{plural(len(duplicate), 'duplicate membership')} left out",
                           "The student is in two sections of this exam; the membership with no paper is left out, the other is certified.",
                           rows=[person(s) for s in duplicate]))
    mismatched, members = student_members("bubble_frq_version_mismatch", students)
    if mismatched:
        found.append(check("bubble_frq_version_mismatch", "confirm", f"{plural(len(mismatched), 'student')} bubbled another version than their paper",
                           "The bubble sheet is scored with its own version's key. A mis-bubbled version, or a sheet matched to the wrong student, "
                           "scores against the wrong key: check each in the bubble sheet review.",
                           rows=[person(s, f"Paper Version {s['frq_version']} · sheet Version {s['bubble_version']}") for s in mismatched],
                           members=members, action={"label": "Open bubble sheets", "href": f"{href}?tab=bubble"}))
    return found


def _grading_checks(facts, href):
    found = []
    work = facts.get("work")
    if work is not None:
        states = work.get("states") or {}
        open_states = {s: n for s, n in states.items() if s not in FINAL and s not in NO_GRADE and n}
        pieces = [plural(n, "response") + f" {label}" for n, label in (
            (sum(open_states.get(s, 0) for s in ("proposed", "grading_review", "ocr_review", "both", "trial_accepted")), "waiting for review"),
            (open_states.get("draft", 0), "with a draft review"), (open_states.get("stale", 0), "to grade again after a rubric change"),
            (open_states.get("pending", 0), "not graded yet"), (work.get("new", 0), "not graded yet (new)"),
            (work.get("failed", 0), "that failed to grade"), (work.get("pending_tasks", 0), "being graded now")) if n]
        if pieces:
            count = sum(open_states.values()) + work.get("new", 0) + work.get("failed", 0)
            found.append(check("grading_unfinished", "block", "Grading is not finished", "; ".join(pieces) + ".", count=count,
                               action={"label": "Review grades", "href": f"{href}?tab=review"} if not (work.get("new") or work.get("failed") or work.get("pending_tasks") or open_states.get("stale"))
                               else {"label": "Grade", "href": f"{href}?tab=jobs"}))
    no_grade = facts.get("no_grade") or []
    if no_grade:
        found.append(check("no_grade_recorded", "block", f"{plural(len(no_grade), 'question')} marked unreadable or rejected",
                           "These decisions record no score. Record a grade: approve, or save a manual 0 with a comment.",
                           rows=[{**person(r, f"{r.get('question', '')} · {r.get('status', '')}"), "response_id": r.get("response_id")} for r in no_grade]))
    versions = facts.get("versions") or []
    if versions:
        found.append(check("version_decisions", "block", f"{plural(len(versions), 'paper')} waiting for a version",
                           "A paper whose exam version is not settled is never graded.", rows=[person(s) for s in versions],
                           action={"label": "Decide versions", "href": f"{href}?tab=rubrics&focus=versions"}))
    inbox = facts.get("inbox") or {}
    pieces = [plural(n, word, many) for n, word, many in (
        (inbox.get("awaiting", 0), "scan waiting for its scan checks", "scans waiting for their scan checks"),
        (inbox.get("bubble_waiting", 0), "bubble sheet in a scan waiting for scan checks", "bubble sheets in scans waiting for scan checks"),
        (inbox.get("unregistered", 0), "first page not registered to a student", "first pages not registered to a student")) if n]
    if pieces:
        found.append(check("inbox_pending", "block", "Papers may still be in the Inbox", "; ".join(pieces) + ".",
                           count=sum(inbox.get(k, 0) for k in ("awaiting", "bubble_waiting", "unregistered")),
                           action={"label": "Open the Inbox", "href": inbox.get("href") or "#inbox"}))
    papers = facts.get("papers_in_review") or []
    if papers:
        found.append(check("papers_in_review", "block", f"{plural(len(papers), 'paper')} not settled in collection",
                           "Several copies, newer arrivals, conflicting observations or missing pages wait for review.",
                           rows=[person(s) for s in papers], action={"label": "Open collection", "href": f"#exams/{facts.get('event_id')}?tab=collection"}))
    return found


def _participation_checks(facts, href):
    """GRADING "GRADED FOR PARTICIPATION": what the setting does to these grades, and the bubble-sheet key it cannot cover."""
    taking_part = facts.get("participation") or {}
    if not taking_part.get("enabled"):
        return []
    if taking_part.get("bubble_key"):
        return [check("participation_bubble", "block", "Graded for participation covers written questions only",
                      "This assignment has a bubble-sheet answer key, and its bubble sheets would keep their own scores. Turn Graded for "
                      "participation off, or grade the multiple choice another way.", count=1)]
    lines, approved = taking_part.get("lines") or 0, taking_part.get("approved") or 0
    alone = (f" {plural(approved, 'grade')} that waited for your review {'was' if approved == 1 else 'were'} approved by Faculty as the graders left "
             f"{'it' if approved == 1 else 'them'}, without a look; {'its' if approved == 1 else 'their'} flags are kept.") if approved else ""
    return [check("participation", "info", "Graded for participation",
                  f"Every student with a paper gets full credit. {plural(lines, 'grade')} below full credit {'counts' if lines == 1 else 'count'} "
                  "with the line “Graded for participation”, worth what brings it to full credit; the rubric lines stay as feedback, "
                  f"on Gradescope too.{alone}", count=lines)]


def _bubble_checks(facts, href):
    bubble = facts.get("bubble")
    if not bubble or not bubble.get("part"):
        return []
    found, sheets = [], bubble.get("sheets") or []
    unsettled = [s for s in sheets if s.get("status") in ("needs_review", "no_version", "no_key")]
    duplicates = bubble.get("duplicates") or []
    beyond = bubble.get("beyond_read") or []
    no_key = not bubble.get("has_key") and sheets
    if unsettled or duplicates or beyond or no_key:
        detail = "; ".join(f"{plural(n, 'sheet')} {t}" for n, t in ((sum(1 for s in unsettled if s["status"] == state), t) for state, t in
                           (("needs_review", "need review"), ("no_version", "have no version"), ("no_key", "have no key for their version"))) if n)
        if duplicates:
            detail += ("; " if detail else "") + f"{plural(len(duplicates), 'student')} claimed by two sheets"
        if beyond:
            detail += ("; " if detail else "") + f"keyed questions {', '.join(str(n) for n in beyond)} were not read on every scan"
        if no_key:
            detail = "Save an answer key to score the sheets."
        rows = [{"name": s.get("name") or s.get("student") or "Unmatched sheet", "section": s.get("section", ""), "row": s.get("row"),
                 "detail": s.get("reason", ""), "sheet_id": s.get("sheet_id")} for s in unsettled] + [person(s, "Two sheets claim this student") for s in duplicates]
        found.append(check("bubble_unsettled", "block", "The multiple-choice part cannot be scored yet", detail + ("." if not detail.endswith(".") else ""),
                           rows=rows, count=len(unsettled) + len(duplicates) + len(beyond) + (1 if no_key else 0),
                           action={"label": "Open bubble sheets", "href": f"{href}?tab=bubble"}))
    unmatched = bubble.get("unmatched") or []
    students = facts.get("students") or []
    lacking = [s for s in students if s.get("status") == "zero" or ("bubble" in (s.get("missing_parts") or []))]
    if unmatched:
        rows = [{"name": "Sheet matched elsewhere" if s.get("outside") else "Unmatched sheet", "section": "", "detail": s.get("reason", ""),
                 "sheet_id": s.get("sheet_id"), "job_id": s.get("job_id"), "page": s.get("page")} for s in unmatched]
        if lacking:
            found.append(check("unmatched_sheets", "confirm", f"{plural(len(unmatched), 'bubble sheet')} on no student of this exam",
                               f"{plural(len(lacking), 'student')} without a sheet will receive 0 for it; one of these sheets may be theirs. "
                               "Match them in the bubble sheet review, or confirm none is.",
                               rows=rows, members=[s.get("sheet_id") for s in unmatched], action={"label": "Open bubble sheets", "href": f"{href}?tab=bubble"}))
        else:
            found.append(check("unmatched_sheets", "info", f"{plural(len(unmatched), 'bubble sheet')} on no student of this exam",
                               "Every student has a sheet, so none of these is missing from a grade.", rows=rows))
    return found


def _preview_checks(facts, href):
    found = []
    if facts.get("skipped") is not None and facts["skipped"]:
        found.append(check("looked_final_skipped", "block", f"{plural(len(facts['skipped']), 'grade')} that looked final are not",
                           "The export's rule refused them (for example a suspended automatic acceptance or a changed rubric).",
                           rows=[{**person(r, r.get("reason", "")), "response_id": r.get("response_id")} for r in facts["skipped"]]))
    if facts.get("out_of_range") is not None and facts["out_of_range"]:
        found.append(check("score_out_of_range", "block", f"{plural(len(facts['out_of_range']), 'score')} outside the possible range",
                           "A final score outside its rubric's floor and ceiling, or a total above its possible, is a data error: never certified.",
                           rows=[{**person(r, r.get("detail", "")), "response_id": r.get("response_id")} for r in facts["out_of_range"]]))
    return found


def _plan_checks(facts, href):
    found = []
    additions = facts.get("roster_additions") or []
    if additions:
        found.append(check("roster_additions", "block", f"{plural(len(additions), 'roster student')} not in this exam",
                           "They joined a section after the exam was made and would be left out entirely.",
                           rows=[person(s, "Not a participant of this exam") for s in additions],
                           action={"label": "Add new roster students", "href": f"#exams/{facts.get('event_id')}"}))
    gradescope = facts.get("gradescope") or {}
    if gradescope.get("live"):
        found.append(check("gradescope_live", "block", "A Gradescope send is still running",
                           "What is on Gradescope is moving; certify once the send has settled.", count=1))
    if not facts.get("students") or not facts.get("parts"):
        found.append(check("nothing_to_certify", "block", "Nothing to certify",
                           "This exam has no participants, or the plan has no graded part yet.", count=1))
    return found


def gradescope_members(kind, detail):
    """The exact set a "do not send" decision covers, as sorted keys: grades not on Gradescope (`response:<id>:<status>`), students
    without Faculty's paper there (`paper:<row>`), questions with answers that can never be sent (`question:<id>`; a version nobody
    has sat yet, such as a makeup's, has no written grade to leave off Gradescope: requirement, 2026-11-03, of Quiz 6's version E
    stopping a regrade); for sheets `sheet:<row>`."""
    if not detail:
        return []
    if kind == "sheets":
        return sorted(f"sheet:{row}" for row in detail.get("missing") or [])
    members = [f"response:{rid}:{status}" for rid, status in detail.get("uncovered") or []]
    members += [f"paper:{row}" for row in detail.get("papers_missing") or []]
    members += [f"question:{qid}" for qid in detail.get("unsendable") or {} if qid in (detail.get("questions") or {})]
    return sorted(members)


def prefill_reason(previous, kind, members, configured=True):
    """The reason to offer for "do not send" (critique C.9, instructor Q2): the previous certification's own reason when its uncovered
    set held every member of this one (unchanged or smaller); "Gradescope is not set up" when it is not; else nothing."""
    group = ((previous or {}).get(kind) or {})
    if group.get("decision") == "do_not_send" and group.get("reason") and set(members) <= set(group.get("members") or []):
        return group["reason"]
    return "" if configured else "Gradescope is not set up."


def _gradescope_checks(facts, href):
    gradescope = facts.get("gradescope")
    if not gradescope:
        return []
    found, configured = [], gradescope.get("configured", False)
    parts = facts.get("parts") or []
    written = gradescope.get("written")
    if "frq" in parts and written:
        members = gradescope_members("written", written)
        if members:
            never = written.get("never") or {}
            per_question = []
            # In the Questions table's order (version, then printed number), each titled with its version (review UX2): two versions'
            # "Question 20" are otherwise the same row twice.
            order = {row.get("question_id"): (index, row) for index, row in enumerate(facts.get("questions") or [])}
            for qid, q in sorted((written.get("questions") or {}).items(), key=lambda item: (order.get(item[0], (len(order), {}))[0], item[0])):
                missing = q.get("changed", 0) + q.get("not_sent", 0)
                if missing or q.get("unsendable"):
                    version = (order.get(qid, (0, {}))[1] or {}).get("version") or ""
                    title = (facts.get("question_titles") or {}).get(qid, "")
                    per_question.append({"question_id": qid, "title": f"{title} · Version {version}" if version else title, "version": version,
                                         "not_sent": q.get("not_sent", 0), "changed": q.get("changed", 0), "unsendable": q.get("unsendable")})
            names = facts.get("names") or {}
            students = [{"row": row, "name": (names.get(row) or {}).get("name", ""), "section": (names.get(row) or {}).get("section", ""),
                         "detail": never.get("papers", {}).get(row) or "No paper of theirs on Gradescope"} for row in written.get("papers_missing") or []]
            detail = (f"{written['totals']['not_sent'] + written['totals']['changed']:,} of {written['totals']['final']:,} final grades and "
                      f"{len(written.get('papers_missing') or [])} papers are not on Gradescope." if written.get("totals") else "")
            if not configured:
                detail = "Gradescope is not set up, so nothing can be sent from Faculty. " + detail
            sendable = configured and any(True for rid, _ in written.get("uncovered") or [] if rid not in (never.get("grades") or {}))
            found.append(check("gradescope_written", "confirm", "Written grades not on Gradescope", detail, rows=students, members=members,
                               count=len(members), requires="decision", questions=per_question, never=len(never.get("grades") or {}) + len(never.get("papers") or {}),
                               sendable=bool(sendable) or bool(configured and written.get("papers_missing")),
                               prefill=prefill_reason(gradescope.get("previous"), "written", members, configured),
                               action={"label": "Send to Gradescope…", "kind": "send_written"} if configured else None))
    sheets = gradescope.get("sheets")
    if "bubble" in parts and sheets:
        members = gradescope_members("sheets", sheets)
        if members:
            names = facts.get("names") or {}
            never = sheets.get("never") or {}
            rows = [{"row": row, "name": (names.get(row) or {}).get("name", ""), "section": (names.get(row) or {}).get("section", ""),
                     "detail": never.get(row) or "Sheet not uploaded"} for row in sheets.get("missing") or []]
            detail = f"{len(sheets.get('missing') or []):,} of {sheets.get('totals', {}).get('matched', 0):,} matched bubble sheets are not on Gradescope."
            if not configured:
                detail = "Gradescope is not set up, so nothing can be sent from Faculty. " + detail
            found.append(check("gradescope_sheets", "confirm", "Bubble sheets not on Gradescope", detail, rows=rows, members=members, count=len(members),
                               requires="decision", never=len(never), sendable=configured and len(never) < len(members),
                               prefill=prefill_reason(gradescope.get("previous"), "sheets", members, configured),
                               action={"label": "Send to Gradescope…", "kind": "send_sheets"} if configured else None))
    # THE MC CHECK (gradescope_mc; requirement, 2026-10-06): Gradescope scores the multiple choice with its own key and Faculty with
    # the instructor's; a student they score differently is confirmed here, from the last read (Certification never contacts Gradescope).
    # MC CORRECTIONS (requirement, 2026-10-22): Faculty corrects Gradescope itself where it
    # can; every correction is a check to confirm, and only a difference Faculty could not correct stays one. SETTLED (instructor,
    # 2026-11-01): a correction a later read shows Gradescope agreeing with is no longer listed; the Gradescope panel keeps them all.
    mc = gradescope.get("mc_check")
    corrections = [c for c in (mc or {}).get("corrections") or [] if not c.get("settled")]
    if "bubble" in parts and corrections:
        words = {"accepted": "corrected on Gradescope", "pending": "being corrected", "sending": "being corrected", "uncertain": "being checked on Gradescope",
                 "failed": "not corrected", "refused": "not corrected"}
        by_student = {}
        for c in corrections:
            reason = f" ({c['why']})" if c.get("why") else ""
            line = f"Q{c['number']}: Gradescope {c['gradescope']} → {c['faculty']}{reason} · {words.get(c['state'], c['state'])}"
            if c["state"] in ("failed", "refused") and c.get("error"):
                line += f": {c['error']}"
            entry = by_student.setdefault(c["row"], {"row": c["row"], "name": c["name"], "section": c["section"], "lines": [], "members": []})
            entry["lines"].append(line)
            entry["members"].append(f"{c['row']}:{c['number']}:{c['faculty']}")
        rows = [{"row": e["row"], "name": e["name"], "section": e["section"], "detail": " · ".join(e["lines"])} for e in by_student.values()]
        landed = sum(1 for c in corrections if c["state"] == "accepted")
        found.append(check("gradescope_mc_corrected", "confirm", "Multiple choice corrected on Gradescope",
                           f"Your bubble sheet scores are the grade. Where Gradescope scored a question differently, Faculty changed Gradescope’s score to "
                           f"yours ({plural(landed, 'question')} so far). Look at each sheet before you certify.", rows=rows,
                           members=[m for e in by_student.values() for m in e["members"]], count=len(corrections)))
    left = [d for d in (mc or {}).get("differ") or [] if not d.get("corrected")]
    if "bubble" in parts and mc and left:
        rows = [{"row": d["row"], "name": d["name"], "section": d["section"], "detail": d["detail"]} for d in left]
        found.append(check("gradescope_mc_differs", "confirm", "Multiple choice scored differently on Gradescope",
                           f"Gradescope scored {plural(len(rows), 'student')}’s bubble sheet differently from your key, and Faculty has not corrected it "
                           "there. Faculty certifies its own score. It corrects a question on Gradescope when it reads Gradescope’s scores (Check MC "
                           "in the Gradescope panel), but never a sheet Gradescope filed under another version; change those there by hand.",
                           rows=rows))
    elif "bubble" in parts and mc and not mc.get("checked") and gradescope.get("sheets") and (gradescope["sheets"].get("totals") or {}).get("sent"):
        found.append(check("gradescope_mc_unchecked", "info", "Multiple choice not yet compared with Gradescope",
                           "Faculty compares Gradescope’s multiple-choice scores with yours after the written grades are sent, or when you press "
                           "Check MC in the Gradescope panel.", count=0))
    return found


def _question_checks(facts, href):
    found = []
    questions = facts.get("questions") or []
    all_zero = [q for q in questions if q["n_final"] >= ZERO_QUESTION_MIN and q["zero"] == q["n_final"]]
    if all_zero:
        found.append(check("zero_question_all", "confirm", f"{plural(len(all_zero), 'question')} where every student scored 0",
                           "Often a key or rubric error. Confirm you checked each.",
                           rows=[{"name": q["title"], "section": "", "detail": f"Version {q['version']} · {q['n_final']} grades" if q["version"] else f"{q['n_final']} grades",
                                  "question_id": q["question_id"]} for q in all_zero],
                           members=[q["question_id"] for q in all_zero]))
    all_full = [q for q in questions if q["n_final"] >= ZERO_QUESTION_MIN and q["full"] == q["n_final"]]
    if all_full:
        found.append(check("question_all_full", "info", f"{plural(len(all_full), 'question')} where everyone got full credit", "",
                           rows=[{"name": q["title"], "section": "", "detail": q["version"], "question_id": q["question_id"]} for q in all_full]))
    unchecked = [q for q in questions if q.get("automatic_unchecked")]
    if unchecked:
        total = sum(q["automatic_unchecked"] for q in unchecked)
        found.append(check("automatic_unchecked", "info", f"{total:,} automatic grades nobody looked at",
                           "Finalized automatically by the grading team; spot-check a few per question.", count=total,
                           rows=[{"name": q["title"], "section": "", "detail": f"{q['automatic_unchecked']:,} automatic", "question_id": q["question_id"]} for q in unchecked]))
    settled = [q for q in questions if q.get("flagged_finalized")]
    if settled:
        total = sum(q["flagged_finalized"] for q in settled)
        found.append(check("flagged_finalized", "info", f"{total:,} flagged grades finalized by the arbitrator or the flag reviewer",
                           "The models flagged these and a reviewing model, not you, settled them.", count=total,
                           rows=[{"name": q["title"], "section": "", "detail": f"{q['flagged_finalized']:,}", "question_id": q["question_id"]} for q in settled]))
    groups = facts.get("answer_groups") or []
    inconsistent = [g for g in groups if len(g.get("scores") or {}) > 1]
    if inconsistent:
        found.append(check("answer_inconsistency", "info", f"{plural(len(inconsistent), 'answer group')} with different scores",
                           "The same final answer earned different scores; the work may justify it, or one grade may be off.",
                           rows=[{"name": g.get("title", ""), "section": "", "detail": f"{g.get('answer', '')}: " + ", ".join(f"{n}× {s}" for s, n in sorted(g["scores"].items())),
                                  "question_id": g.get("question_id"), "group": g.get("group")} for g in inconsistent]))
    return found


def bubble_key_suspects(key, stats_by_version, minimum=10):
    """Keyed questions whose answers suggest a key error: most sheets chose the same letter that is not the key's, or fewer than 20%
    were right. `key`: the bubble key ({versions: [{letter, questions: [{number, answer}]}]}); `stats_by_version`: `_bubble_view`'s
    `stats` ({letter: {number: {sheets, full, common, common_count}}})."""
    found = []
    for version in (key or {}).get("versions") or []:
        per = (stats_by_version or {}).get(version["letter"]) or {}
        for question in version.get("questions") or []:
            entry = per.get(str(question["number"])) or {}
            sheets = entry.get("sheets", 0)
            if sheets < minimum:
                continue
            wrong_majority = entry.get("common") and entry["common"] != question.get("answer") and entry.get("common_count", 0) * 2 > sheets
            if wrong_majority or entry.get("full", 0) * 5 < sheets:
                found.append({"version": version["letter"], "number": question["number"], "answer": question.get("answer"),
                              "common": entry.get("common"), "correct_pct": rounded(Decimal(entry.get("full", 0)) * 100 / sheets)})
    return found


def _info_checks(facts, href):
    found = []
    quality = facts.get("rubric_quality") or {}
    if quality and (quality.get("state") != "confirmed" or quality.get("not_standing")):
        found.append(check("rubric_finalization", "info", "A rubric check is waiting",
                           "Grades whose finalization no longer stands are not final and are counted as unfinished grading; the others stay final.",
                           action={"label": "Open the rubric finalization", "href": f"{href}?tab=answer-key&open=quality"}))
    by_version = facts.get("by_version") or {}
    means = {v: s for v, s in by_version.items() if s.get("n", 0) >= VERSION_GAP_MIN and s.get("mean") is not None and s.get("possible")}
    if len(means) > 1:
        pct = {v: Decimal(str(s["mean"])) * 100 / Decimal(str(s["possible"])) for v, s in means.items()}
        high, low = max(pct, key=pct.get), min(pct, key=pct.get)
        if pct[high] - pct[low] > VERSION_GAP:
            found.append(check("version_gap", "info", f"Version {high} averages {rounded(pct[high] - pct[low])} points (percent) above Version {low}",
                               "Check that the versions were equally hard and their keys are right.", count=1))
    possibles = facts.get("possible_by_version") or {}
    if len({text(v) for v in possibles.values() if v is not None}) > 1:
        found.append(check("version_max_differs", "info", "The versions are worth different totals",
                           ", ".join(f"Version {v}: {text(p)}" for v, p in sorted(possibles.items()) if p is not None) + ".", count=1))
    graded = [s for s in facts.get("students") or [] if s.get("dr") and s.get("status") == "scored"]
    if graded:
        found.append(check("dr_graded", "info", f"{plural(len(graded), 'DR student')} graded", "Their DR exam is past and their grade is in.", count=len(graded)))
    gradescope = facts.get("gradescope") or {}
    if gradescope.get("configured"):
        found.append(check("gradescope_published_unknown", "info", "Publication on Gradescope is known only in the send preview",
                           "Faculty reads only its own receipts here; it never contacts Gradescope from this page.", count=0))
    if gradescope.get("anything_sent"):
        found.append(check("gradescope_side_changes", "info", "Changes made on Gradescope are not seen here",
                           "Regrade requests or edits made on Gradescope after sending are invisible to Faculty; only the multiple-choice "
                           "scores are read again, by the MC check.", count=0))
    if facts.get("training_waiting"):
        found.append(check("training_waiting", "info", f"{plural(facts['training_waiting'], 'grade')} still being saved for training", "", count=facts["training_waiting"]))
    suspects = facts.get("bubble_suspects") or []
    if suspects:
        found.append(check("bubble_key_suspect", "info", f"{plural(len(suspects), 'multiple-choice question')} to check in the key",
                           "Most students chose the same other letter, or fewer than 20% were right.",
                           rows=[{"name": f"Version {s['version']} · question {s['number']}", "section": "",
                                  "detail": f"key {s['answer']} · most chose {s['common']} · {s['correct_pct']}% right"} for s in suspects],
                           action={"label": "Open the answer key", "href": f"{href}?tab=bubble"}))
    changes = facts.get("changes") or []
    if changes:
        found.append(check("changes_since", "info", f"{plural(len(changes), 'certified grade')} would change",
                           "Compared with the latest certification.", rows=[person(c, f"{(c.get('before') or {}).get('earned')} → {(c.get('after') or {}).get('earned')}")
                                                                              for c in changes]))
    return found


def build_checks(facts, provisional=False):
    """Every check computable from `facts` (a key that is absent is not checked). `provisional` marks a page read's checks: the
    certify preview checks everything again. Block first, then confirm, then info."""
    href = f"#grading/{facts.get('plan_id')}"
    found = (_student_checks(facts, href) + _grading_checks(facts, href) + _bubble_checks(facts, href) + _participation_checks(facts, href) + _preview_checks(facts, href)
             + _plan_checks(facts, href) + _gradescope_checks(facts, href) + _question_checks(facts, href) + _info_checks(facts, href))
    if provisional:
        for item in found:
            item["provisional"] = True
    return sorted(found, key=lambda c: LEVEL_ORDER[c["level"]])


def counts(checks):
    return {"blocking": sum(1 for c in checks if c["level"] == "block"), "to_confirm": sum(1 for c in checks if c["level"] == "confirm")}


# ----- the snapshot ---------------------------------------------------------------------------------------------------------------
def frq_part(state, decisions, earned=None, possible=None):
    """The free-response part of a certified row: each question's final decision by reference. `decisions`: [{question_id, score,
    max, kind review|automatic, decision_id, result_id, response_id}]."""
    questions = {d["question_id"]: {"score": text(d["score"]), "max": text(d.get("max")), "decision": f"{d['kind']}:{d['decision_id']}",
                                    "result_id": d.get("result_id"), "response_id": d.get("response_id")}
                 for d in sorted(decisions, key=lambda d: d["question_id"])}
    return {"state": state, "earned": text(earned), "possible": text(possible), "questions": questions}


def bubble_part(state, sheet=None, key_revision=None, earned=None, possible=None):
    sheet = sheet or {}
    return {"state": state, "earned": text(earned), "possible": text(possible), "sheet_id": sheet.get("sheet_id"), "review_id": sheet.get("review_id"),
            "key_revision": key_revision, "version": sheet.get("version") or ""}


def zero_part(possible, reason):
    return {"state": "zero", "earned": "0", "possible": text(possible), "reason": reason}


def record(student, classification, parts_json):
    """One `frq_certification_students` row (as a dict) from a classified student and the per-part detail the service built."""
    status = classification["status"]
    if status == "blocked":
        raise ValueError("A student a block names is never certified.")
    scored = status in ("scored", "zero")
    return {"section_id": student["section_id"], "enrollment_id": student["enrollment_id"], "status": status, "reason": classification["reason"],
            "earned": classification["earned"] if scored else None, "possible": classification["possible"] if scored else None,
            "exam_version": student.get("exam_version") or "", "parts_json": parts_json,
            "dr_date": classification.get("dr_date") if classification["reason"] == "dr_ahead" or status == "zero" else None}


def snapshot_digest(records):
    """Canonical digest of the certified rows, sorted by row key."""
    return digest(sorted(([r["section_id"], r["enrollment_id"], r["status"], r["reason"], r["earned"], r["possible"], r["exam_version"],
                           r["parts_json"], r["dr_date"]] for r in records), key=lambda r: (r[0], r[1])))


def summary(records):
    """`summary_json`: the counts and the statistics of the scored totals (students with a part confirmed as 0 apart)."""
    statuses = [r["status"] for r in records]
    students = [{"status": r["status"], "reason": r["reason"], "earned": r["earned"], "possible": r["possible"], "exam_version": r["exam_version"]}
                for r in records]
    per_part = {}
    for r in records:
        for name, part in (r["parts_json"] or {}).items():
            if part.get("state") == "complete":
                per_part.setdefault(name, []).append((part.get("earned"), part.get("possible")))
    return {"students": len(records), "scored": statuses.count("scored"), "zero": statuses.count("zero"), "excused": statuses.count("excused"),
            "left_out": statuses.count("left_out"),
            # Of those, the students whose DR exam is still ahead; the others are the empty one of two memberships (review F6).
            "left_out_dr": sum(1 for r in records if r["status"] == "left_out" and r["reason"] == "dr_ahead"),
            "left_out_makeup": sum(1 for r in records if r["status"] == "left_out" and r["reason"] == "makeup"),
            "missing_part": sum(1 for r in records if r["reason"] == "missing_part"),
            "stats": total_stats(students), "per_part": {name: stats(pairs) for name, pairs in sorted(per_part.items())}}


def changes(previous, records, names=None):
    """Rows whose certified status or earned points differ from the previous certification's (at most `ROWS`)."""
    names = names or {}
    before = {(r["section_id"], r["enrollment_id"]): r for r in previous or []}
    found = []
    for r in sorted(records, key=lambda r: (r["section_id"], r["enrollment_id"])):
        old = before.get((r["section_id"], r["enrollment_id"]))
        if not old or (old["status"], text(old["earned"])) != (r["status"], text(r["earned"])):
            row = common.row_key(r["section_id"], r["enrollment_id"])
            found.append({"row": row, "name": (names.get(row) or {}).get("name", ""), "section": (names.get(row) or {}).get("section", ""),
                          "before": {"status": old["status"], "earned": text(old["earned"])} if old else None,
                          "after": {"status": r["status"], "earned": text(r["earned"])}})
    return found[:ROWS]


def fingerprint(*, parts, snapshot_sha256, checks, gradescope, gradebook):
    """What a certify POST must name. Only BLOCK and CONFIRM checks count (critique C4): an info check (training still being saved,
    automatic grades nobody looked at) moves on its own and must never make "the grades changed" out of nothing. `gradescope`:
    {written: members digest, sheets: members digest}; `gradebook`: {item_id or "new:<assignment>", setup_revision, column_action,
    plan_fingerprint}."""
    return digest({"rule": RULE, "parts": list(parts), "snapshot": snapshot_sha256,
                   "checks": sorted([c["id"], c["level"], c.get("digest")] for c in checks if c["level"] in ("block", "confirm")),
                   "gradescope": gradescope, "gradebook": gradebook})


# ----- the certify request (6.1) ----------------------------------------------------------------------------------------------------
REQUEST_KEYS = {"request_id", "fingerprint", "confirmation", "acknowledged", "gradescope", "gradebook", "gradescope_after"}


def parse_request(data):
    """The certify body, checked before any heavy read (400 on anything malformed): {request_id (UUID), fingerprint, confirmation
    "certify", acknowledged {check_id: digest}, gradescope {written|sheets: {decision: send|do_not_send, reason}}, gradebook {replace:
    [row], category_id, column: {action, parts: keep|certified, item_id?}}, gradescope_after?: true}. Returns the normalized request
    and its sha (without the request ID), so a retry with the same content replays and other content is a 409. `gradescope_after`
    (GRADESCOPE "AFTER CERTIFYING": Faculty sends to Gradescope and publishes by itself afterwards) is named only when true, so a
    request without it keeps the sha it always had; a `send` decision is then one Faculty carries out, not one already made."""
    if not isinstance(data, dict):
        raise ValueError("Supply a JSON object.")
    unknown = set(data) - REQUEST_KEYS
    if unknown:
        raise ValueError(f"Unknown field: {sorted(unknown)[0]}.")
    if data.get("confirmation") != "certify":
        raise ValueError("Confirm the certification: nothing is certified without it.")
    try:
        request_id = str(uuid.UUID(str(data.get("request_id") or "")))
    except ValueError:
        raise ValueError("Certify needs a request ID.") from None
    fingerprint_value = data.get("fingerprint")
    if not isinstance(fingerprint_value, str) or not fingerprint_value:
        raise ValueError("Certify needs the preview's fingerprint.")
    acknowledged = data.get("acknowledged") or {}
    if not isinstance(acknowledged, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in acknowledged.items()):
        raise ValueError("Acknowledgements name each check and its digest.")
    decisions = {}
    groups = data.get("gradescope") or {}
    if not isinstance(groups, dict):
        raise ValueError("A Gradescope decision is for the written grades or the bubble sheets.")
    for group, value in groups.items():
        if group not in ("written", "sheets") or not isinstance(value, dict) or set(value) - {"decision", "reason"}:
            raise ValueError("A Gradescope decision is for the written grades or the bubble sheets.")
        decision, reason = value.get("decision"), value.get("reason") or ""
        if decision not in ("send", "do_not_send") or not isinstance(reason, str):
            raise ValueError("Choose to send to Gradescope or not to send, with a reason.")
        reason = reason.strip()
        if decision == "do_not_send" and not 1 <= len(reason) <= REASON_LIMIT:
            raise ValueError(f"Say why these are not sent to Gradescope (at most {REASON_LIMIT} characters).")
        decisions[group] = {"decision": decision, "reason": reason if decision == "do_not_send" else ""}
    book = data.get("gradebook") or {}
    if not isinstance(book, dict) or set(book) - {"replace", "category_id", "column"}:
        raise ValueError("The gradebook choices are the rows to replace, the category and the column.")
    replace = book.get("replace") or []
    if not isinstance(replace, list):
        raise ValueError("Name the rows to replace.")
    for row in replace:
        common.split_row(row)
    column = book.get("column") or {}
    if (not isinstance(column, dict) or set(column) - {"action", "parts", "item_id"} or column.get("parts", "certified") not in ("keep", "certified")
            or not all(value is None or isinstance(value, str) for value in column.values())):
        raise ValueError("Choose the gradebook column and whether its parts are kept.")
    category = book.get("category_id")
    if category is not None and not isinstance(category, str):
        raise ValueError("Choose a category of this term's gradebook.")
    after = data.get("gradescope_after", False)
    if type(after) is not bool:
        raise ValueError("Say whether Faculty sends to Gradescope and publishes after certifying.")
    request = {"fingerprint": fingerprint_value, "confirmation": "certify", "acknowledged": dict(sorted(acknowledged.items())),
               "gradescope": decisions, "gradebook": {"replace": sorted(set(replace)), "category_id": category, "column": column},
               **({"gradescope_after": True} if after else {})}
    return request_id, request, digest(request)


def unconfirmed(checks, request):
    """What the request has not confirmed, as titles: every confirm check shown must be acknowledged with its CURRENT digest, and each
    Gradescope group shown must have a decision. Empty when complete."""
    missing = []
    for item in checks:
        if item["level"] != "confirm":
            continue
        if item.get("requires") == "decision":
            group = "written" if item["id"] == "gradescope_written" else "sheets"
            if group not in request["gradescope"]:
                missing.append(item["title"])
        elif request["acknowledged"].get(item["id"]) != item["digest"]:
            missing.append(item["title"])
    return missing


def gradescope_record(checks, request, details, configured):
    """`gradescope_json`: per group the coverage, the members (IDs only, for the next certification's prefill) and the decision."""
    record_ = {"configured": bool(configured)}
    for group, check_id in (("written", "gradescope_written"), ("sheets", "gradescope_sheets")):
        item = next((c for c in checks if c["id"] == check_id), None)
        detail = details.get(group) or {}
        members = gradescope_members(group, detail) if detail else []
        decision = request["gradescope"].get(group) if item else None
        record_[group] = {"coverage": detail.get("totals") or {}, "uncovered_sha256": members_digest(members), "members": members,
                          "decision": (decision or {}).get("decision") or ("covered" if detail else "none"), "reason": (decision or {}).get("reason", "")}
    return record_
