"""Response-specific review evidence derived from retained readings and approved rubrics.

No retained owner: these summaries are saved in the existing term response result.
Original OCR candidates, rubric wording and instructor decisions stay immutable.
"""
from .consensus import tokens
from .models import reading_ambiguities

VERSION = "review-rules-v2"
CONDITION_PREFIX = "Rubric condition "
LEGACY_CONDITION = "This rubric contains conditional exceptions; inspect their application."


def reading_resolution(reading, verified, crops, settings):
    """A clear image reading agreeing with a complete independent OCR candidate settles that pair.

Require agreement across the whole retained reading, not a matching digit elsewhere,
confidence alone, the expected answer, or a model's claim that it resolved something.
Mixed readings that match no full candidate remain unresolved. Original agreement
and confidence values are kept separately for acceptance and measurement.
"""
    original = reading["consensus"]["disagreements"]
    result = {"total": len(original), "resolved": 0, "unresolved": len(original), "details": []}
    if not original:
        return result
    final = verified["transcript"]
    visible = tokens(final.get("latex") or final.get("plain_text", ""))
    elements = [el for line in final["lines"] for el in line["elements"]]
    consistent = tokens("\n".join(el.get("latex") or el.get("text", "") for el in elements)) == visible
    clear = bool(visible and consistent and not reading_ambiguities(final) and not any(el.get("state") == "unclear" for el in elements)
                 and crops["crops"] and all(c["metadata"]["registered"] for c in crops["crops"]))
    human = bool(verified.get("human_corrected"))
    confidence = verified.get("visual_confidence")
    # With reported confidence switched off by the instructor, the clear, consistent, registered reading above decides alone.
    from .risk import confidence_checks
    clear = clear and (human or not confidence_checks(settings) or (confidence is not None and confidence >= settings["visual_threshold"]))
    matches = {c["provider"] for c in reading["candidates"] if visible == tokens(c["transcript"].get("latex") or c["transcript"]["plain_text"])} if clear else set()
    for index, difference in enumerate(original):
        pair = difference.get("providers", [])
        matched = sorted(matches.intersection(pair)) if len(set(pair)) >= 2 else []
        settled = clear and (human or bool(matched))
        result["details"].append({"index": index, "status": "resolved" if settled else "unresolved",
                                  "basis": "instructor correction" if settled and human else "image reading agrees with a complete OCR candidate" if settled else "unresolved reading difference",
                                  "matching_providers": matched})
    result["resolved"] = sum(d["status"] == "resolved" for d in result["details"])
    result["unresolved"] -= result["resolved"]
    return result


def condition_checks(rubric, applications):
    """Only positively identified restrictions are irrelevant when their line is unselected.

Free-form exceptions (including 'unless' and caps) may affect omitted credit and
stay for review. A structured disposition is an instructor-authored assertion in
an approved rubric revision, never a model/compiler inference.
"""
    import re
    from .rubrics import conditional
    chosen = {a["rubric_item_id"]: a["applied"] for a in applications}
    result = []
    for item in (rubric or {}).get("items", []):
        if not conditional(item):
            continue
        condition = item["exceptions"].strip()
        restriction = bool(re.fullmatch(r"(?:apply(?: this (?:item|deduction|credit))? )?only if [^.?!;\n]+[.]?", condition, re.I))
        inactive = restriction and chosen.get(item["id"]) is False
        state = "enforced" if item.get("condition_handling") == "structured" else "inactive" if inactive else "needs_review"
        result.append({"item_id": item["id"], "state": state, "condition": condition})
    return result


def condition_reasons(rubric, applications):
    return [f"{CONDITION_PREFIX}{c['item_id']}: {c['condition']} — check its application to this response."
            for c in condition_checks(rubric, applications) if c["state"] == "needs_review"]


def current_condition_reasons(reasons, rubric, applications, previous=()):
    return [r for r in reasons if r != LEGACY_CONDITION and r not in previous] + condition_reasons(rubric, applications)
