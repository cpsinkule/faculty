"""GRADING FLAG REVIEW DEDUCTIONS: term-owned metadata retained with the existing ruling/result.

Only explicit deductions in the approved rubric/guidance become choices. The model decides applicability;
Faculty owns the amounts, computes the score and checks it again before automatic acceptance.
"""
import math
import re
from decimal import Decimal

from .models import digest
from .scoring import score

RULE = "approved-flag-deductions-v1"
TRANSIENT = "flag_review_adjustments"
_NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
# Ordinary negative rubric items already deduct their points. Vague penalties never authorize an
# invented amount (GRADING "FLAG REVIEW DEDUCTIONS"). Keep the full source condition beside each choice.
_DEDUCTION = re.compile(
    rf"(?:(?:coordinator\s+deduction\s*:\s*(?:take\s+off|deduct|subtract)\s+|"
    rf"(?:additional|extra)\s+deduction\s*(?:of\s+|:\s*)?)"
    rf"(?P<amount>{_NUMBER})\s*(?:-\s*)?points?\b|"
    rf"(?:additional|extra)\s+(?P<amount_after>{_NUMBER})\s*(?:-\s*)?points?\s+deduction\b)", re.I)
_TOTAL = re.compile(
    rf"(?:\b(?:final\s+(?:score|grade)(?:\s+is)?|(?:score|grade|total)(?:\s+(?:is|of))?)|"
    rf"^\s*(?:award|give|uphold))\s*[:=]?\s*"
    rf"(?P<amount>{_NUMBER})(?:\s*/\s*(?P<maximum>{_NUMBER})|\s+points?\b|(?=[.!;,]?\s*$))", re.I)


def rules_of(rubric):
    """Stable choices from explicit approved amounts, with the complete condition beside the quotation."""
    rules, seen = [], set()
    for item in rubric.get("items", []):
        if item.get("points", 0) < 0:
            continue
        for field in ("description", "notes", "exceptions", "clarification"):
            context = str(item.get(field) or "")
            for match in _DEDUCTION.finditer(context):
                amount = Decimal(match["amount"] or match["amount_after"])
                if amount <= 0:
                    continue
                key = (item["id"], str(amount.normalize()))
                if key in seen:
                    continue
                seen.add(key)
                rules.append({"id": "D" + digest(key)[:16], "item_id": item["id"], "points": -float(amount),
                              "authorization": match[0], "condition": context})
    return rules


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("GRADING: the flag review must report a finite expected score.")
    return Decimal(str(value))


def validate(raw, row, ruling):
    """Leaving a case with the instructor never requires a made-up score or deduction choice."""
    if ruling["decision"] == "unreliable":
        return
    rules = {r["id"]: r for r in row["deduction_rules"]}
    decisions = raw.get("deductions")
    if not isinstance(decisions, list):
        raise ValueError("GRADING: the flag review must decide every approved deduction.")
    seen, adjustments, recorded = set(), [], []
    for choice in decisions:
        if (not isinstance(choice, dict) or set(choice) != {"id", "applied", "reason"}
                or choice.get("id") not in rules or choice["id"] in seen or type(choice.get("applied")) is not bool):
            raise ValueError("GRADING: unknown, repeated or malformed flag-review deduction.")
        seen.add(choice["id"])
        rule = rules[choice["id"]]
        why = choice.get("reason")
        if not isinstance(why, str) or not why.strip():
            raise ValueError("GRADING: explain whether each approved deduction applies.")
        recorded.append({**rule, "applied": choice["applied"], "reason": why[:2000]})
        if choice["applied"]:
            adjustments.append({"points": rule["points"], "reason": f"{rule['item_id']}: {why}"[:4000]})
    if seen != set(rules):
        raise ValueError("GRADING: the flag review omitted an approved deduction decision.")
    assessment = row["existing"][ruling["adopt"]] if ruling["decision"] == "adopt" else ruling["assessment"]
    computed = score(row["rubric"], assessment["applications"], row["content"]["max_points"], adjustments)
    expected = _amount(raw.get("expected_score"))
    if expected != Decimal(str(computed["score"])):
        raise ValueError(f"GRADING: the flag review expects {expected:g} points but its rubric lines and deductions give {computed['score']:g}.")
    for match in _TOTAL.finditer(ruling.get("reason", "")):
        if match["maximum"] and Decimal(match["maximum"]) != Decimal(str(row["content"]["max_points"])):
            continue
        if Decimal(match["amount"]) != expected:
            raise ValueError("GRADING: the flag review's explanation contradicts its calculated final score.")
    claimed = {-float(Decimal(m["amount"] or m["amount_after"])) for m in _DEDUCTION.finditer(ruling.get("reason", ""))}
    if claimed - {a["points"] for a in adjustments}:
        raise ValueError("GRADING: the flag review describes a deduction it did not apply.")
    ruling["scoring"] = {"rule": RULE, "expected_score": float(expected), "adjustments": adjustments, "deductions": recorded}


def mismatch(scoring, actual):
    """The exact adjustments must survive the carrier's ordinary scoring stage."""
    return bool(scoring and (not actual or actual.get("score") != scoring["expected_score"]
                             or actual.get("adjustments", []) != scoring["adjustments"]))
