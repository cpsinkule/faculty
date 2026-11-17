"""Validate independent interpretation/application proposals and review signals."""
from . import answers
from .review_rules import LEGACY_CONDITION, condition_reasons, current_condition_reasons, reading_resolution
from .models import identifier, items, located, model_geometry, number, quotable_lines, reading_ambiguities, struck_lines, text
from .scoring import score


# Said of every response of such a rubric: a fact about the RUBRIC for the instructor, never evidence about one response.
RUBRIC_EXCEPTIONS = LEGACY_CONDITION
# What `validate_grade` and `review_state` say themselves, by name (requirement, 2026-10-11: the review card shows one line per cause, and
# `review_queue.reason_causes` tells the causes apart by these names, never by matching words). Naming only: the words are unchanged.
UNDECIDED = "The model did not decide every rubric item."
ERROR_LINE = "The error graph names an unavailable transcript line."
FOLLOW_THROUGH = "Follow-through conflict: dependent errors share an originating error."
OCR_DISAGREE = "OCR readings still disagree; confirm the unresolved reading."
VISUAL = "Visual reading needs confirmation."
OCR_UNSURE = "An OCR provider reported low confidence."
BOUNDARIES = "Check response boundaries and template alignment."
APPLICATION = "The proposed rubric application needs review."


def citation_audit(reason):
    """Faculty's check of a quotation, not a grader asking for a look."""
    return reason.endswith("evidence does not quote the retained transcript.") or reason.endswith(
        "no supported evidence; the selection requires instructor review.") or reason == (
        "A reported final answer does not quote the retained transcript.")


def low_ocr_confidence(candidates, settings):
    """Whether a reader doubts its own reading: its per-symbol rate where it reports one, its whole-result confidence otherwise."""
    from . import risk
    if not risk.confidence_checks(settings):
        return False
    floor = (settings.get("risk") or {}).get("min_ocr_rate", risk.DEFAULTS["min_ocr_rate"])
    rated = [c["confidence_rate"] for c in candidates if c.get("confidence_rate") is not None]
    plain = [c["transcript"]["confidence"] for c in candidates if c.get("confidence_rate") is None and c["transcript"]["confidence"] is not None]
    return any(r < floor for r in rated) or any(c < settings["ocr_threshold"] for c in plain)


def validate_grade(raw, current_transcript, rubric, maximum, crop_count, deductions=()):
    """`deductions`: the approved deductions this grader was offered (`grader_deductions.offered`); its decisions are recorded on the
    assessment and the score takes off those it applied. With none offered, anything it says of deductions is ignored, as before."""
    if not isinstance(raw, dict):
        raise ValueError("Grader must return an object.")
    if "full_credit_eligible" in raw and type(raw["full_credit_eligible"]) is not bool:
        raise ValueError("Full-credit eligibility must be a boolean.")
    # What a quotation may be found in (`models.quotable_lines`). `models.quotes` decides: every row of the quotation, in the
    # student's own characters.
    lines = quotable_lines(current_transcript)
    available = {item["id"]: item for item in rubric["items"]}
    decisions, reasons, seen, unticked = [], [], set(), 0
    for application in items(raw.get("applications"), "Rubric decisions"):
        if not isinstance(application, dict):
            raise ValueError("Invalid rubric decision.")
        iid = application.get("rubric_item_id")
        if not isinstance(iid, str) or iid not in available or iid in seen or type(application.get("applied")) is not bool:
            raise ValueError("Model returned unsupported or duplicate rubric decisions.")
        seen.add(iid)
        evidence = []
        for ev in items(application.get("evidence", []), "Evidence", 100):
            if not isinstance(ev, dict):
                raise ValueError("Invalid quoted evidence.")
            quote = text(ev.get("student_text", ""), "Quoted evidence", 4000)
            if not quote.strip():
                # An empty slot is missing evidence, not a failed citation. Measured 2026-09-21: Gemini sent `{}` / ""
                # and 153 of 156 "does not quote" flags on that batch were this, listed as if a snippet had missed the page.
                continue
            lid = ev.get("line_id")
            crop = ev.get("crop", 0)
            if type(crop) is not int or not 0 <= crop < crop_count:
                raise ValueError("Evidence names an unavailable response crop.")
            found = located(quote, lines, lid)
            if found is None:
                # A line that was NOT ticked moves no score, so a quotation offered for it is a courtesy (requirement, 2026-09-21: 7 of
                # 82 refused quotations flagged a response for a line nobody proposed). Dropped and counted for the audit, never a doubt.
                if not application["applied"]:
                    unticked += 1
                elif not (rubric.get("mode") == "positive" and available[iid]["points"] == 0):
                    reasons.append(f"{iid}: evidence does not quote the retained transcript.")
                continue
            # Kept under the line where it was FOUND; what the grader named stays beside it when that was a neighbouring line.
            evidence.append({"line_id": found, "student_text": quote, **model_geometry(ev), "crop": crop, **({"named_line_id": lid} if found != lid else {})})
        # In a positive rubric a line worth nothing ("No answer or completely incorrect") says that nothing was earned: there is nothing
        # to quote for it, and on real papers it was a third of all "no evidence" alarms. Every line that moves the score still needs its quote.
        nothing_earned = rubric.get("mode") == "positive" and available[iid]["points"] == 0
        if application["applied"] and not evidence and not nothing_earned:
            reasons.append(f"{iid}: no supported evidence; the selection requires instructor review.")
        confidence = number(application.get("confidence"), "Application confidence", 0, 1)
        decisions.append({"rubric_item_id": iid, "applied": application["applied"], "reason": text(application.get("reason", "")),
            "evidence": evidence, "origin_error_id": identifier(application["origin_error_id"]) if application.get("origin_error_id") else None,
            "confidence": confidence, **({"ambiguity": text(application["ambiguity"], "Ambiguity", 4000)} if application.get("ambiguity") else {})})
    if set(available) != seen:
        reasons.append(UNDECIDED)
    interpretation = raw.get("interpretation", {})
    if not isinstance(interpretation, dict):
        raise ValueError("Invalid mathematical interpretation.")
    graph, ids = [], set()
    for error in items(interpretation.get("error_graph", []), "Error graph", 100):
        if not isinstance(error, dict):
            raise ValueError("Invalid error graph entry.")
        eid = identifier(error.get("id"))
        if eid in ids:
            raise ValueError("Error IDs must be unique.")
        ids.add(eid)
        graph.append({"id": eid, "caused_by": [identifier(v) for v in items(error.get("caused_by", []), "Error causes")],
            "line_ids": [identifier(v) for v in items(error.get("line_ids", []), "Error lines")],
            "description": text(error.get("description", ""))})
    final_answers, unquoted = answers.final_answers(interpretation.get("final_answers", []), lines, struck_lines(current_transcript),
                                                    quotable_lines(current_transcript, crossed_out=False))
    reasons.extend(unquoted)
    # grade-v3: the mathematical interpretation is separate from the rubric. Older receipts simply have no opinion.
    consistent = interpretation.get("work_consistent", "unclear")
    if consistent not in ("yes", "no", "unclear"):
        raise ValueError("Work consistency is yes, no or unclear.")
    attempts = interpretation.get("attempts", 1)
    if type(attempts) is not int or not 0 <= attempts <= 50:
        raise ValueError("Solution attempts must be a small whole number.")
    lookup = {e["id"]: e for e in graph}
    resolved = {}
    def root_errors(eid, path=()):
        if eid in path or eid not in lookup:
            raise ValueError("Error graph contains a cycle or an unknown cause.")
        if eid in resolved:
            return resolved[eid]
        parents = lookup[eid]["caused_by"]
        resolved[eid] = set().union(*(root_errors(p, (*path, eid)) for p in parents)) if parents else {eid}
        return resolved[eid]
    for error in graph:
        root_errors(error["id"])
        if not set(error["line_ids"]) <= set(lines):
            reasons.append(ERROR_LINE)
    penalized = []
    for decision in decisions:
        origin = decision["origin_error_id"]
        if decision["applied"] and origin:
            roots = root_errors(origin)
            decision["origin_error_roots"] = sorted(roots)
            if available[decision["rubric_item_id"]]["points"] < 0:
                for previous, previous_roots in penalized:
                    if roots & previous_roots and (available[previous]["follow_through"] or available[decision["rubric_item_id"]]["follow_through"]):
                        reasons.append(FOLLOW_THROUGH)
                penalized.append((decision["rubric_item_id"], roots))
    reasons.extend(text(v, "Review reason", 4000) for v in items(raw.get("review_reasons", []), "Review reasons"))
    ambiguities = [text(v, "Rubric ambiguity", 4000) for v in items(raw.get("rubric_ambiguities", []), "Rubric ambiguities")]
    reasons.extend(ambiguities)
    reasons = current_condition_reasons(reasons, rubric, decisions)
    from .grader_deductions import decide
    decided, adjustments = decide(raw, deductions)
    try:
        numeric = score(rubric, decisions, maximum, adjustments)
    except ValueError as error:
        numeric = None
        reasons.append(str(error))
    reading_issues = []
    for issue in items(raw.get("reading_issues", []), "Reading issues", 100):
        if not isinstance(issue, dict) or issue.get("kind") not in ("transcription", "layout", "unreadable"):
            raise ValueError("Invalid reading issue.")
        refs = [identifier(v) for v in items(issue.get("line_ids"), "Reading issue lines", 100)]
        if not set(refs) <= set(lines):
            raise ValueError("A reading issue names an unavailable transcript line.")
        reading_issues.append({"line_ids": refs, "kind": issue["kind"], "description": text(issue.get("description"), "Reading issue", 2000, False)})
    return {"applications": decisions, "reading_issues": reading_issues, "interpretation": {"meaning": text(interpretation.get("meaning", "")), "method": text(interpretation.get("method", ""), "Method", 4000),
            "work_consistent": consistent, "attempts": attempts, "error_graph": graph, "final_answers": final_answers},
        "score": numeric, "confidence": number(raw.get("confidence"), "Grading confidence", 0, 1),
        "rubric_ambiguities": ambiguities, "review_reasons": list(dict.fromkeys(reasons)), "condition_review_reasons": condition_reasons(rubric, decisions),
        **({"full_credit_eligible": raw["full_credit_eligible"]} if "full_credit_eligible" in raw else {}),
        **({"deductions": decided} if deductions else {}),
        **({"unticked_quotes_dropped": unticked} if unticked else {})}  # For the audit only; nothing reads it.


def review_state(recognition, verification, grading, extraction, settings):
    ocr_reasons = reading_ambiguities(verification["transcript"])
    resolution = reading_resolution(recognition, verification, extraction, settings)
    if resolution["unresolved"]:
        ocr_reasons.append(OCR_DISAGREE)
    from . import risk
    # A multiple-choice read (grading/choice.py) passed its own gate, both orders at the batch's cutoff: the reported confidences
    # decide nothing more here, as `risk.assess` drops them for it.
    reported = risk.confidence_checks(settings) and not (grading.get("choice") or {}).get("rule")
    confidence = verification.get("visual_confidence")
    if reported and (confidence is None or confidence < settings["visual_threshold"]):
        ocr_reasons.append(VISUAL)
    provider_confidences = [c["transcript"]["confidence"] for c in recognition["candidates"]]
    if low_ocr_confidence(recognition["candidates"], settings):
        ocr_reasons.append(OCR_UNSURE)
    # A crop the reader found nothing written on (direct-reading-v2). Faculty decides nothing about it: the instructor sees
    # whether the question was left blank, and the response cannot be finalized automatically while this reason stands.
    blank = sum(len(c.get("blank_crops", [])) for c in recognition["candidates"])
    if blank:
        ocr_reasons.append(risk.blank_reason(blank))
    if any(not crop["metadata"]["registered"] for crop in extraction["crops"]):
        ocr_reasons.append(BOUNDARIES)
    grading_reasons = list(grading["review_reasons"])
    if reported and (grading["confidence"] < settings["grading_threshold"] or any(a["confidence"] < settings["grading_threshold"] for a in grading["applications"] if a["applied"])):
        grading_reasons.append(APPLICATION)
    state = "both" if ocr_reasons and grading_reasons else "ocr_review" if ocr_reasons else "grading_review" if grading_reasons else "proposed"
    return state, {"reading_resolution": resolution, "ocr_agreement": recognition["consensus"]["agreement"], "ocr_provider_confidences": provider_confidences,
        "ocr_provider_rates": [c.get("confidence_rate") for c in recognition["candidates"]],
        "visual_confidence": confidence, "grading_confidence": grading["confidence"], "rubric_ambiguities": grading["rubric_ambiguities"]}, list(dict.fromkeys(ocr_reasons + grading_reasons))
