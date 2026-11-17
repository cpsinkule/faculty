"""Measure before building the committee: comparisons on the instructor's own graded work, with their uncertainty.

Everything here is offline arithmetic on an exported dataset (or on retained catalog rows handed in by the
service). "Better" means better beyond noise on the SAME responses, so every comparison against the default
primary grader carries a confidence interval, and a challenger within noise does not replace a default that is
cheaper, self-hostable and fine-tunable. For automatic acceptance the number that matters is the error rate
among accepted responses with its UPPER bound, not average accuracy. Nothing here trains or grades by itself.
"""
import math
import re

from .acceptance import upper_bound
from .models import digest

SPLITS = (("train", .6), ("calibration", .2), ("test", .2))
SPLIT_RULE = "enrollment-prefix-v1"  # Names how a record's `split` was derived; a changed derivation needs a new name.


def wilson(successes, n, z=1.96):
    """A 95% interval for a proportion that behaves at 0, at 1 and for small n."""
    if not n:
        return None
    p, denominator = successes / n, 1 + z * z / n
    centre, spread = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [max(0.0, (centre - spread) / denominator), min(1.0, (centre + spread) / denominator)]


def split_of(student):
    """Every response of one student lands in ONE split, so a student's handwriting never leaks from training into the test."""
    position = int(digest(f"student-split:{student}")[:12], 16) / 16 ** 12
    for name, share in SPLITS:
        if position < share:
            return name
        position -= share
    return SPLITS[-1][0]


def enrollment_split(enrollment_id):
    """The split of a student in EVERY export, from their permanent enrollment ID (server side, at export).

    The per-export pseudonym changes with each export on purpose, so a split derived from it moved about half of the students
    between exports: a model trained on one export's train split met its own students in a later export's test split. A ten-digit
    enrollment ID begins with the student's workspace prefix, which never changes and is the same in every course and term
    (`student_identities`), so the split is derived from that prefix and a student who returns in another term stays in the same
    split. Students who happen to share a prefix share a split, which never separates anyone's responses. No workspace secret
    exists to key this hash; only the split's NAME is exported (under two bits), never the digest, the prefix or the ID.
    """
    key = enrollment_id[:5] if re.fullmatch(r"[1-9][0-9]{9}", enrollment_id or "") else enrollment_id
    return split_of(f"{SPLIT_RULE}:{key}")


def record_split(record):
    """A dataset record's split: the stable one exported with it, else (datasets exported before it existed) its pseudonym's."""
    names = {name for name, _ in SPLITS}
    if record.get("split") in names:
        return record["split"]
    return split_of(record["student"]) if record.get("student") else None


def exact(expected, proposed):
    if not expected.get("score") or not proposed.get("score"):
        return None
    return abs(expected["score"]["score"] - proposed["score"]["score"]) < 1e-8


def failed(record):
    """A grading attempt that produced no score: a failed or malformed answer, or a selection that cannot be scored. It is a WRONG
    answer for every agreement figure (dropping it made a challenger that failed on everything the baseline got wrong look perfect).
    A reading-only candidate (no rubric decisions at all, and not marked failed) is not a grading attempt and counts nowhere here."""
    proposed = record["proposed"]
    return bool(record.get("failed") or record.get("malformed") or proposed.get("failed") or ("applications" in proposed and not proposed.get("score")))


def verdicts(records):
    """{response_id: agrees} of one profile, a failed attempt counting as disagreement, and the failed responses themselves."""
    found, failures = {}, set()
    for record in records:
        if not record["expected"].get("score"):
            continue  # No instructor score to agree with.
        if failed(record):
            found[record["response_id"]] = False
            failures.add(record["response_id"])
        elif (agrees := exact(record["expected"], record["proposed"])) is not None:
            found[record["response_id"]] = agrees
    return found, failures


def _binomial_tail(k, n):
    """P(X <= k) for X ~ Binomial(n, 1/2), exactly."""
    return sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n


def paired(baseline, challenger, baseline_failed=(), challenger_failed=()):
    """Exact-score agreement of a challenger against the baseline on the responses BOTH graded. rows: {response_id: bool}.

    Only the discordant responses say which is better, so the test is McNemar's EXACT test on them (a binomial test at one half),
    and the interval is its exact conditional interval: the Clopper-Pearson interval of the challenger's share of the discordant
    responses, turned into a difference of agreement. The two agree by construction. The earlier normal approximation called 4 to 0
    in 40 "better beyond noise", where the exact probability of so lopsided a split by chance is .125. A failed attempt is a wrong
    answer here, and the failures on each side are also counted on their own.
    """
    shared = sorted(set(baseline) & set(challenger))
    n = len(shared)
    if not n:
        return {"responses": 0, "difference": None, "interval_95": None, "p_value": None, "verdict": "no shared responses"}
    only_challenger = sum(challenger[r] and not baseline[r] for r in shared)
    only_baseline = sum(baseline[r] and not challenger[r] for r in shared)
    discordant = only_challenger + only_baseline
    difference = (only_challenger - only_baseline) / n
    if discordant:
        p_value = min(1.0, 2 * _binomial_tail(min(only_challenger, only_baseline), discordant))
        low = 1 - upper_bound(discordant - only_challenger, discordant, .975)  # Two-sided 95%: 2.5% in each tail.
        high = upper_bound(only_challenger, discordant, .975)
        interval = [discordant * (2 * low - 1) / n, discordant * (2 * high - 1) / n]
    else:
        p_value, interval = 1.0, [0.0, 0.0]  # Right and wrong on exactly the same responses: nothing tells them apart.
    verdict = "better beyond noise" if interval[0] > 0 else "worse beyond noise" if interval[1] < 0 else "within noise: keep the default"
    return {"responses": n, "baseline_agreement": sum(baseline[r] for r in shared) / n, "challenger_agreement": sum(challenger[r] for r in shared) / n,
            "only_challenger_right": only_challenger, "only_baseline_right": only_baseline, "difference": difference, "interval_95": interval,
            "p_value": p_value, "test": "exact McNemar", "baseline_failed": len(set(baseline_failed) & set(shared)),
            "challenger_failed": len(set(challenger_failed) & set(shared)), "verdict": verdict}


def breakdown(records):
    """Where the errors are, so independence is added where they are: another reader, a second grader, or rubric structure."""
    counts = {"responses": 0, "correct": 0, "misread_handwriting": 0, "wrong_rubric_line": 0, "ambiguous_rubric": 0, "alignment": 0, "malformed_output": 0}
    for record in records:
        expected, proposed = record["expected"], record["proposed"]
        if failed(record):
            counts["responses"] += 1
            counts["malformed_output"] += 1  # A failed or unscorable attempt is an error of its own kind, never left out.
            continue
        if "applications" not in proposed:
            continue
        counts["responses"] += 1
        truth = {a["rubric_item_id"] for a in expected["applications"] if a["applied"]}
        chosen = {a["rubric_item_id"] for a in proposed["applications"] if a["applied"]}
        if truth == chosen:
            counts["correct"] += 1
        elif record.get("malformed"):
            counts["malformed_output"] += 1
        elif record.get("unregistered"):
            counts["alignment"] += 1
        elif expected["transcript"]["plain_text"] != proposed["transcript"]["plain_text"]:
            counts["misread_handwriting"] += 1  # The instructor corrected the reading: the rubric was applied to something the student did not write.
        elif record.get("rubric_ambiguities") or record["rubric"].get("warnings"):
            counts["ambiguous_rubric"] += 1
        else:
            counts["wrong_rubric_line"] += 1
    errors = counts["responses"] - counts["correct"]
    dominant = max(("misread_handwriting", "wrong_rubric_line", "ambiguous_rubric", "alignment", "malformed_output"), key=lambda k: counts[k]) if errors else None
    advice = {"misread_handwriting": "Reading errors dominate: add another independent reader before another grader.",
              "wrong_rubric_line": "Rubric application dominates: compare a separate second assessment against the approved rubric.",
              "ambiguous_rubric": "Rubric ambiguity dominates: confirm rubric groups and exclusions; every model reads the same ambiguity the same way.",
              "alignment": "Alignment dominates: use a faithful blank exam (the printed PDF or a scanned blank) before spending on models.",
              "malformed_output": "Malformed output dominates: use a host with schema-constrained output for this model."}.get(dominant, "No errors in this sample.")
    return {**counts, "errors": errors, "dominant": dominant, "advice": advice}


def grading(records):
    """The grading metrics of one profile, each with the uncertainty that belongs to it.

    Agreement counts every grading attempt: a failed or unscorable one is a wrong answer (`failed_responses` says how many). The
    score error is measured on the scored attempts only, since a failure has no score to be off by."""
    scored = [(r, exact(r["expected"], r["proposed"])) for r in records if "applications" in r["proposed"] and not failed(r)]
    scored = [(r, agrees) for r, agrees in scored if agrees is not None]
    failures = [r for r in records if failed(r) and r["expected"].get("score")]
    attempts = len(scored) + len(failures)
    agree = sum(agrees for _, agrees in scored)
    deltas = [abs(r["expected"]["score"]["score"] - r["proposed"]["score"]["score"]) for r, _ in scored]
    accepted = [(r, agrees) for r, agrees in scored if r.get("would_accept")]
    wrong = sum(not agrees for _, agrees in accepted)
    seconds = [r["seconds"] for r in records if r.get("seconds") is not None]
    costs = [r["cost"] for r in records if r.get("cost") is not None]
    n = len(scored)
    return {"scored_responses": n, "failed_responses": len(failures), "graded_attempts": attempts,
            "exact_score_agreement": agree / attempts if attempts else None, "exact_score_agreement_95": wilson(agree, attempts),
            "mean_absolute_score_error": sum(deltas) / n if n else None, "errors_of_a_point_or_more": sum(d >= 1 for d in deltas),
            "automatic_accept_coverage": len(accepted) / attempts if attempts else None, "accepted": len(accepted), "accepted_wrong": wrong,
            "error_rate_among_accepted": wrong / len(accepted) if accepted else None,
            "error_rate_among_accepted_upper_95": upper_bound(wrong, len(accepted)) if accepted else None,
            # A failure always needs a person, so it counts as a review.
            "human_review_rate": (sum(r["proposed"].get("state") != "proposed" for r, _ in scored) + len(failures)) / attempts if attempts else None,
            "ocr_disagreement_rate": sum(bool(r.get("ocr_disagreements")) for r in records) / len(records) if records else None,
            "median_latency_seconds": sorted(seconds)[len(seconds) // 2] if seconds else None,
            "estimated_cost": round(sum(costs), 6) if costs else None, "cost_per_response": round(sum(costs) / len(costs), 6) if costs else None}


def ensemble(predictions, lineages):
    """Majority vote per rubric line, with one vote per completed model assessment."""
    assessments = {}
    for name, prediction in predictions.items():
        if "applications" in prediction:
            assessments[name] = prediction
    if len(assessments) < 2:
        return None
    votes = {}
    for prediction in assessments.values():
        for application in prediction["applications"]:
            votes.setdefault(application["rubric_item_id"], []).append(application["applied"])
    return {"models": sorted(assessments), "lineages": sorted({lineages.get(name) for name in assessments if lineages.get(name)}), "applications": [{"rubric_item_id": item, "applied": sum(v) * 2 > len(v), "unanimous": len(set(v)) == 1} for item, v in votes.items()]}
