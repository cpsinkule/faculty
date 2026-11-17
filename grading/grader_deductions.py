"""GRADERS TAKE OFF APPROVED DEDUCTIONS (requirement, 2026-10-26).

On Exam 2 (2026-10-25) 83 of the 357 held answers carried a coordinator deduction the graders had recognized ("Coordinator deduction:
take off 0.25 point for writing an inequality and 12000 in the objective function") and could only ask about, because a grader returned
no adjustments; 60 of them were held for nothing else, and the flag review then took off exactly that amount on 53. The choices are the
flag review's (`flag_adjustments.rules_of`): only a deduction the approved rubric or confirmed guidance states with its amount. A grader
decides whether each applies, never the amount; Faculty takes it off with the ordinary decimal scorer, as the grade's adjustments.
Frozen per batch (`RULE`, `standard.freeze`): a batch without it, and a rubric that states no amount, grade exactly as before.
"""
from . import flag_adjustments

RULE = "grader-deductions-v1"
ROLES = ("primary", "second")  # The two graders; a ruling decides deductions in the flag review (`flag_adjustments`), never here.
# What an arbitrator's own grade says when the rubric states deductions it was not asked about: the grade waits for a ruling on them.
UNDECIDED = "The final arbitrator's own grade did not decide the rubric's approved deductions; the flag review or the instructor decides them."


def enabled(settings):
    return (settings or {}).get("grader_deductions_rule") == RULE


def offered(rubric, settings):
    """The deductions a grader of this batch decides: [{id, item_id, points, condition}], [] when none or not frozen."""
    if not enabled(settings):
        return []
    return [{k: rule[k] for k in ("id", "item_id", "points", "condition")} for rule in flag_adjustments.rules_of(rubric)]


def decide(raw, choices):
    """The grader's decisions on `choices` as recorded beside its assessment, and the adjustments they make. ValueError when one is
    unknown, repeated, malformed or missing: the answer breaks its contract and is asked again, like any other."""
    if not choices:
        return [], []
    known = {choice["id"]: choice for choice in choices}
    decisions = raw.get("deductions")
    if not isinstance(decisions, list):
        raise ValueError("A grader must decide every approved deduction.")
    recorded, adjustments = [], []
    for entry in decisions:
        if (not isinstance(entry, dict) or set(entry) - {"id", "applied", "reason"} or entry.get("id") not in known
                or any(r["id"] == entry["id"] for r in recorded) or type(entry.get("applied")) is not bool
                or not isinstance(entry.get("reason"), str) or not entry["reason"].strip()):
            raise ValueError("A grader returned an unknown, repeated or malformed deduction decision.")
        choice = known[entry["id"]]
        recorded.append({**choice, "applied": entry["applied"], "reason": entry["reason"][:2000]})
        if entry["applied"]:
            adjustments.append({"points": choice["points"], "reason": f"{choice['item_id']}: {entry['reason']}"[:4000]})
    if {r["id"] for r in recorded} != set(known):
        raise ValueError("A grader left an approved deduction undecided.")
    return recorded, adjustments


def adjustments(graded):
    """The adjustments a grade's own deduction decisions make, for scoring it again ("Recalculate points")."""
    return [{"points": d["points"], "reason": f"{d['item_id']}: {d['reason']}"[:4000]} for d in (graded or {}).get("deductions") or [] if d.get("applied")]


def applied(opinion):
    """The ids of the deductions an assessment applied, for comparing two graders (`risk.compare`)."""
    return {d["id"] for d in (opinion or {}).get("deductions") or [] if d.get("applied")}


def label(opinion, ids):
    """How a disagreement names deductions: "deduction on R2_1" for each id, by the line it belongs to."""
    lines = {d["id"]: d["item_id"] for d in (opinion or {}).get("deductions") or []}
    return [f"deduction on {lines.get(i, i)}" for i in sorted(ids)]
