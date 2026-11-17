"""A durable, rule-based picture of how much a proposal can be trusted. It orders and thins review; it never grades.

There is no labeled data yet, so this is a deterministic policy over independent evidence, not a learned score:
reader agreement, the answer key, visible work, a separate second assessment, the
adversarial reviewer, symbolic checks, alignment and output validity. A model's own confidence is one weak
input among these and never the gate. Every COMPONENT signal is persisted with the result (not only the
verdict), so a calibration model trained on the instructor's adjudications can replace this policy later
without losing history.

No signal here selects, adds or removes a rubric line or approves a response. `escalate` says where more
compute is worth spending; `blocks` says why a proposal may not be accepted unseen (trial or real);
`level` orders the instructor's review.
"""
POLICY = "risk-rules-v15"  # v13: a blank crop is a block no policy waives; the narrow policy keeps a legacy pair's disagreement.
# v14 (2026-09-22): the key flag says whether the answer DIFFERS from the key or could not be compared with it (KEY_DIFFERS, KEY_UNREAD),
# and a reading doubt about work crossed out entirely is no reading doubt (`models.reading_ambiguities`).
# v15 (2026-09-23): in a batch frozen with `standard.FAMILY_RULE` model families are audit metadata only: votes count assessments and
# no family makes a second grader or reviewer dependent. Batches frozen before it keep one vote per lineage and `ONE_LINEAGE`.
# WHAT HOLDS A RESPONSE BACK (requirement, 2026-09-21, after reading all 13 disputed papers of batch 09d6eee3 by hand).
# `all_checks` is what Faculty always did: every check below blocks automatic finalization.
# `disagreement_and_work` keeps only the two that were measured to earn their reviews, and waives the rest:
#   - the grading team did not agree (60% of those papers had a score another team disputed, against 8% of the unflagged ones);
#   - full credit was proposed where the rubric wants work and little is visible (the only flag on BOTH papers read as
#     genuinely over-awarded, 5 of the 7 wrong points, and it fired on just 3 of that batch's 235 responses).
# Waiving the other 87 flags of that batch was worth 3 points of certain error and ~4 of debatable generosity, against 121
# reviews. Every waived check is still RUN and RECORDED (`waived`), shown on the review card and kept in the signals; only its
# power to block is removed. Two things are never waived, because they are not opinions: a selection that cannot be scored at
# all, and the instructor's own confirmed rubric finalization (`rubric_quality`, applied outside this policy).
REVIEW_POLICIES = ("all_checks", "disagreement_and_work")
# Full credit without a key match. Of 22 such flags a flag reviewer was shown on the trial (batch 1c1ce6cb, 2026-09-22), 20 were
# answers the check could not read (units inside an ordered pair, a word before the value, a sentence), not other values; one sentence
# for both told the reviewer and the instructor that all 22 were wrong answers. Both still block: only the words say which it is.
# The two graders' disagreement, said plainly (the arbitrator is shown the statement naming the lines instead, `arbitration.flags`).
DISAGREE = "The primary grader and the second grader disagree."
KEY_DIFFERS = "Full credit was proposed, but the final answer does not match a confirmed answer key."
KEY_UNREAD = "Full credit was proposed, but the final answer could not be compared with the confirmed answer key."
NO_KEY = "Full credit was proposed, but this question has no confirmed answer key."
WORK_BLOCK = "Full credit was proposed, but the rubric requires supporting work and little work is visible."
UNSCORABLE = "The proposed rubric lines cannot be combined under the approved rules."
# A crop the reader found nothing written on (direct-reading-v2, requirement, 2026-09-22): the instructor decides whether the question
# was left blank, so the response waits and is never finalized automatically, under either policy. The words are exactly those of
# `recognition.review_state`, so the card shows one reason and a ruling's flag is the same flag.
BLANK_PREFIX = "The reader found no handwriting on"


def blank_reason(count):
    return f"{BLANK_PREFIX} {'this response’s area' if count == 1 else f'{count} of this response’s areas'}; check whether it was left blank."


def blank(text):
    return str(text).strip().startswith(BLANK_PREFIX)


# EVERY SENTENCE `assess` SAYS HAS A NAME (requirement, 2026-10-11: the review card shows one line per cause). One cause reached the
# card two or three times in different words (a disagreement as DISAGREE, as the sentence naming the lines and as the team's own
# reason; a grader's reading issue as its block, its note and the team's `role: …` copy), and `review_queue.reason_causes` tells the
# causes apart by these names, never by matching words. Naming only: not a character of a sentence changed, and nothing about what
# escalates, blocks or is said. A parametrised sentence is named by the words it begins with, as BLANK_PREFIX is.
CHOICE_UNSURE = "The multiple-choice reader was unsure."
READERS_DIFFER = "Independent readers disagree on what is written."
READER_UNSURE = "A reader reported low confidence."
NO_OCR = "No independent OCR reading stands beside the vision reader."
OCR_APART = "The independent readings do not agree closely enough."
HANDWRITING = "The handwriting is ambiguous in places."
CROP_UNSURE = "The crop may not show the whole response (alignment is uncertain)."
ATTEMPTS = "The student made several distinct solution attempts."
GRADER_UNSURE = "The grader was uncertain about a rubric decision."
GRADER_ASKED = "The grader’s evidence was incomplete or it asked for review."
INCONSISTENT = "The grader found the work inconsistent, yet full credit was proposed."
INCONSISTENT_WHY = "The grader found the work internally inconsistent or its final answer unsupported by the work, yet full credit was proposed; check what the rubric pays for."
SYMBOLIC_FAILS = "A symbolic check contradicts work that was proposed for full credit."
SYMBOLIC_PREFIX = "The written work is mathematically inconsistent, yet full credit was proposed: "
ANSWER_INCONSISTENT = "A correct final answer is inconsistent with the shown work."
CONTRADICTS = "The proposal contradicts the answer facts (the key, the visible work or the rubric’s own no-work line)."
MALFORMED = "A model answer was malformed or truncated and had to be asked again."
SECOND_READING = "The second grader found an unresolved reading or layout problem."
SECOND_READING_PREFIX = "Second grader ("
SECOND_ASKED = "The second grader requested instructor review."
SECOND_PREFIX = "Second grader: "
SECOND_FAILED = "The second grader failed."
OBJECTION = "The adversarial reviewer raised a material objection."
OBJECTION_WHY = "The adversarial reviewer raised a material objection; see its evidence."
IMAGE_DIFFERS = "The adversarial reviewer saw the image differ from the transcript."
ONE_READING = "The final answer rests on one reading."
ONE_SHOT_PREFIX = "One-shot grader ("
MATH_ERROR = "The written work contains a mathematical error."
# FLAGS NO MODEL RULING MAY RESOLVE (cloud-standard-v2). The final arbitrator and the flag reviewer may be SHOWN them, but only the
# instructor settles them (`arbitration.apply` keeps them standing): a team that never compared two complete assessments
# (third-vote-v3 leaves incomplete pairs to the instructor), a blank crop, a rubric condition, and rubric wording a grader
# found unclear (it fools every model alike). `committee.decide` takes its reasons for an incomplete pair from here.
NO_SECOND = "No completed secondary assessment is available; review this response individually."
NO_PRIMARY = "A required primary assessment is missing, failed, or unscorable; review this response individually."
INCOMPLETE = "A required primary or secondary assessment is missing, failed, or unscorable; review this response individually."
NOBODY_ELSE = "No independent second grader looked at this response."
# Only a batch frozen before `standard.FAMILY_RULE` raises it: there a same-lineage second grader is no independent vote.
ONE_LINEAGE = "The second grader shares the primary grader’s lineage, so its agreement is not an independent vote."
CONDITION = "A rubric condition still needs instructor judgment for this response."
WORDING = "The grader found the rubric’s wording unclear for this response."
# A GRADE THAT TICKS NOTHING (requirement, 2026-09-24). A real zero ticks a zero-point line ("No Answer or Completely Incorrect"), so a
# grade that ticks no line at all on a rubric that has one is a broken answer, not a grade: in trial batch 097ee949 an arbitrator wrote
# "R1_1 applies alone" and marked every line unapplied, and both graders of another response returned nothing; both were finalized at 0.
# Such a grade waits for the instructor under every review policy and no ruling settles it; its score is unchanged. Frozen per batch
# (`EMPTY_SELECTION_RULE`), so a batch from before the rule finishes as it ran. A rubric without a zero-point line has no other way to
# give zero, and is left alone.
# A NEGATIVE RUBRIC'S ZERO-POINT LINE IS "CORRECT" (v2, 2026-10-19). There ticking nothing scores FULL credit, and the zero-point line
# is the correct-answer line, not "No answer": v1 told the instructor this rubric "has a line for no answer" on twelve full-credit
# grades, and, being theirs alone, kept them from the flag review, which would have asked for the whole page when the answer ran past
# the crop (Version C problem 3, 2026-10-19). v2 says what happened (`EMPTY_FULL`). Like rubric wording, the arbitrator and the flag
# reviewer never settle it, but the flag review (`flag_resolution`) is sent it and may tick a line; the hold itself still comes last, so
# an empty grade it upholds still waits for the instructor.
EMPTY_SELECTION_RULE = "empty-selection-v2"
EMPTY_SELECTION_RULES = ("empty-selection-v1", EMPTY_SELECTION_RULE)
EMPTY_SELECTION = "No rubric line was selected, although this rubric has a line for no answer or a completely incorrect answer; check the grade."
EMPTY_FULL = "No rubric line was selected, so this grade gives full credit although no grader ticked the rubric’s correct-answer line; check the grade."
INSTRUCTOR_ONLY = (NO_SECOND, NO_PRIMARY, INCOMPLETE, NOBODY_ELSE, SECOND_FAILED, ONE_LINEAGE, CONDITION, WORDING, EMPTY_SELECTION, EMPTY_FULL)


def zero_lines(rubric):
    """The rubric's zero-point lines ("No Answer or Completely Incorrect" and the like)."""
    from decimal import Decimal, InvalidOperation
    found = []
    for item in (rubric or {}).get("items", []):
        try:
            if Decimal(str(item.get("points"))) == 0:
                found.append(item["id"])
        except (InvalidOperation, KeyError, TypeError, ValueError):
            continue
    return found


def empty_selection(settings, applications, rubric):
    """The zero-point lines of a rubric on which this batch's grade ticked nothing at all, or [] (see EMPTY_SELECTION)."""
    if settings.get("empty_selection_rule") not in EMPTY_SELECTION_RULES or any(a.get("applied") for a in applications or []):
        return []
    return zero_lines(rubric)


def empty_reason(settings, rubric):
    """What a grade that ticks nothing is held with: on a negative rubric under v2 it is full credit nobody ticked (`EMPTY_FULL`)."""
    negative = (rubric or {}).get("mode") == "negative"
    return EMPTY_FULL if negative and settings.get("empty_selection_rule") == EMPTY_SELECTION_RULE else EMPTY_SELECTION


def hold_empty(output, zero, settings, rubric):
    """Keep a grade that ticks nothing for review, the way a ruling's standing flag is kept: a reason, a block, individual review."""
    reason = empty_reason(settings, rubric)
    signals = output["signals"]
    signals["risk"] = {**signals.get("risk", {}), "individual": True, "level": "high",
                       "blocks": list(dict.fromkeys([*signals.get("risk", {}).get("blocks", []), reason]))}
    signals["empty_selection"] = {"rule": settings["empty_selection_rule"], "zero_lines": zero}
    output["reasons"] = list(dict.fromkeys([*output["reasons"], reason]))
    output["state"] = "both" if output["state"] in ("ocr_review", "both") else "grading_review"
    if "rubric_quality" in signals:
        # `apply_quality` projects the finalization again when the result is retained, from the state it saw first: the hold is
        # part of that base state, or the projection would put a grade nothing else held back to "proposed" (2026-10-19).
        quality = signals["rubric_quality"]
        base = quality.get("base_state", output["state"])
        signals["rubric_quality"] = {**quality, "base_state": "both" if base in ("ocr_review", "both") else "grading_review"}
    return output


def instructor_only(text):
    """Whether this flag is one only the instructor may settle, whatever a model ruled about it."""
    from .review_rules import CONDITION_PREFIX, LEGACY_CONDITION
    text = str(text).strip()
    return text in INSTRUCTOR_ONLY or text == LEGACY_CONDITION or text.startswith((BLANK_PREFIX, CONDITION_PREFIX))


def review_policy(settings):
    """Which checks may hold a response back. Takes whole settings or the risk policy; a batch frozen before this keeps `all_checks`."""
    policy = settings.get("risk") if isinstance(settings.get("risk"), dict) else settings
    value = policy.get("review_policy", DEFAULTS["review_policy"])
    return value if value in REVIEW_POLICIES else DEFAULTS["review_policy"]
# min_ocr_rate: a reader's per-symbol confidence (Mathpix `confidence_rate`). PROVISIONAL, from 78 real responses of the trial:
# the lowest tenth sat below .93 (median .993), and five of the six readings the vision reader itself called ambiguous were
# among the low ones. The whole-result `confidence` was below .85 for 53 of the 78 and says little about handwriting.
# Calibrate it from corrected transcripts (`frq_review_feedback.transcript_corrected`) once they exist.
DEFAULTS = {"min_confidence": .9, "min_ocr_agreement": .98, "min_ocr_rate": .93, "require_second_grader": True, "require_key_match": True,
            "require_work_shown": True, "verifier_routes": False, "max_escalation_depth": 1,
            # The instructor's switch (2026-09-20) for every check of a MODEL-REPORTED confidence number: the reader's, the OCR
            # provider's, the graders'. Off, those numbers flag, escalate and block nothing; they are still recorded. What a model
            # NAMES (an ambiguity, an unclear mark, a review reason), reader agreement and every contradiction are not confidence.
            "confidence_checks": True,
            # Which checks may block automatic finalization: see REVIEW_POLICIES above. Default unchanged.
            "review_policy": "all_checks"}


def confidence_checks(settings):
    """Whether reported confidence numbers are used at all. Takes whole settings or the risk policy; older frozen batches have no key."""
    policy = settings.get("risk") if isinstance(settings.get("risk"), dict) else settings
    return policy.get("confidence_checks", True) is not False


def settings(value):
    from .models import number
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("Unknown risk threshold.")
    result = {**DEFAULTS, **value}
    for key in ("min_confidence", "min_ocr_agreement", "min_ocr_rate"):
        number(result[key], key, 0, 1)
    for key in ("require_second_grader", "require_key_match", "require_work_shown", "verifier_routes", "confidence_checks"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} must be a boolean.")
    if type(result["max_escalation_depth"]) is not int or not 0 <= result["max_escalation_depth"] <= 1:
        raise ValueError("Escalation depth is 0 (never) or 1 (once); a response never escalates twice.")
    if result["review_policy"] not in REVIEW_POLICIES:
        raise ValueError("Review policy is every check, or the grading team's disagreement and full credit on little work.")
    return result


def compare(primary, other):
    """Where two independent rubric applications differ. The review screen names these lines."""
    first = {a["rubric_item_id"]: a["applied"] for a in primary["applications"]}
    second = {a["rubric_item_id"]: a["applied"] for a in other["applications"]}
    lines = sorted(item for item in first.keys() | second.keys() if first.get(item) != second.get(item))
    # An approved deduction one grader took off and the other did not is a different grade too (GRADING "GRADERS TAKE OFF APPROVED
    # DEDUCTIONS"); named by the line it belongs to. Assessments that decided none compare exactly as before.
    from .grader_deductions import applied, label
    both = {"deductions": [*(primary.get("deductions") or []), *(other.get("deductions") or [])]}
    lines += label(both, applied(primary) ^ applied(other))
    score = lambda graded: graded["score"]["score"] if graded.get("score") else None
    # Equal totals are useful context only. Every different rubric decision is a disagreement,
    # including equal-point alternatives, zero-point items and differences absorbed by a ceiling.
    same = score(primary) is not None and score(primary) == score(other)
    return {"agrees": not lines and set(first) == set(second), "same_score": same, "disagreements": lines, "primary_score": score(primary), "other_score": score(other)}


def selection_disagreement(components):
    """A recorded team resolution settles disagreement; older unresolved receipts stay flagged."""
    committee = components.get("committee")
    if committee:
        if committee["status"] == "resolved":
            return None
        for comparison in [committee.get("pair"), *committee.get("third_comparisons", {}).values()]:
            if comparison and comparison.get("agrees") is False:
                return comparison
        return None
    for comparison in (components.get("second"), (components.get("escalation") or {}).get("rubric_disagreement")):
        if comparison and comparison.get("agrees") is False:
            return comparison
    return None


def disagreement_reason(comparison):
    message = f"The primary grader and the second grader disagree on {', '.join(comparison['disagreements']) or 'the rubric lines'}; review the rubric selections individually."
    if comparison.get("same_score"):
        message += " Their total scores match, but their selections do not."
    return message


def apply_disagreement_review(result):
    """Project the current disagreement rule over older receipts without rewriting grades or audit history."""
    from copy import deepcopy
    verdict = result.get("signals", {}).get("risk") or {}
    comparison = selection_disagreement(verdict.get("components") or {})
    if not comparison or verdict.get("policy") == POLICY:
        return result
    result = deepcopy(result)
    reason = disagreement_reason(comparison)
    result["signals"]["rubric_disagreement_review"] = {"policy": POLICY, "reason": reason}
    verdict = result["signals"]["risk"]
    verdict.update(level="high", individual=True)
    for field in ("blocks", "reasons"):
        verdict[field] = list(dict.fromkeys([*verdict.get(field, []), reason]))
    result["reasons"] = list(dict.fromkeys([*result["reasons"], reason]))
    result["state"] = "both" if result["state"] in ("ocr_review", "both") else "grading_review"
    return result


def components(reading, verified, graded, crops, answer, symbolic, second=None, verifier=None, *, roles=None, rubric=None, rejected=0, escalation=None, settings=None, committee=None):
    """Everything the policy looks at, as plain facts. Absent evidence is recorded as absent, never as agreement."""
    roles, elements = roles or {}, [el for line in verified["transcript"]["lines"] for el in line["elements"]]
    from .recognition import RUBRIC_EXCEPTIONS
    from .models import NOTHING_TRANSCRIBED, reading_ambiguities
    from .review_rules import CONDITION_PREFIX, condition_checks, reading_resolution
    from . import direct_read
    resolution = reading_resolution(reading, verified, crops, settings or {"visual_threshold": .85})
    confidences = [c["transcript"]["confidence"] for c in reading["candidates"] if c["transcript"]["confidence"] is not None]
    rates = [c["confidence_rate"] for c in reading["candidates"] if c.get("confidence_rate") is not None]
    unrated = [c["transcript"]["confidence"] for c in reading["candidates"] if c.get("confidence_rate") is None and c["transcript"]["confidence"] is not None]
    applied = [a for a in graded["applications"] if a["applied"]]
    lineage = lambda role: (roles.get(role) or {}).get("lineage", "")
    blank_crops = sum(len(c.get("blank_crops", [])) for c in reading["candidates"])  # Counted as `recognition.review_state` counts them.
    reading_doubts = reading_ambiguities(verified["transcript"])
    if crops["crops"] and blank_crops == len(crops["crops"]) and not verified["transcript"]["lines"]:
        # GRADING "JEV BLANK ANSWERS": the empty-transcript sentinel repeats the
        # blank-crop hold; it is not evidence of a separate handwriting problem.
        # Keep it in the transcript/review reasons, and keep every actual doubt.
        reading_doubts = [reason for reason in reading_doubts if reason != NOTHING_TRANSCRIBED]
    result = {
        # INDEPENDENT readings only. With direct reading the one candidate is the reader's own free-text pass on the same
        # image, which answers nothing about whether a second reader saw the same thing (`grading/direct_read.py`).
        "ocr": {"providers": len(direct_read.independent(reading["candidates"])),
                **({"direct": direct_read.rule(settings or {})} if len(direct_read.independent(reading["candidates"])) != len(reading["candidates"]) else {}),
                "agreement": reading["consensus"]["agreement"], "disagreements": resolution["unresolved"], "original_disagreements": resolution["total"], "resolved_disagreements": resolution["resolved"],
                "min_confidence": min(confidences) if confidences else None, "min_rate": min(rates) if rates else None,
                "min_unrated_confidence": min(unrated) if unrated else None},
        "reading": {"visual_confidence": verified.get("visual_confidence"), "ambiguities": len(reading_doubts),
                    "crossed_out": sum(el.get("state") == "crossed_out" for el in elements), "unclear": sum(el.get("state") == "unclear" for el in elements),
                    "registered": all(crop["metadata"]["registered"] for crop in crops["crops"]), "human_corrected": bool(verified.get("human_corrected")), "lineage": lineage("reader"),
                    **({"blank_crops": blank_crops} if blank_crops else {})},
        "primary": {"confidence": graded["confidence"], "min_item_confidence": min((a["confidence"] for a in applied), default=None), "review_reasons": sum(reason != RUBRIC_EXCEPTIONS and not reason.startswith(CONDITION_PREFIX) and reason not in graded.get("rubric_ambiguities", []) for reason in graded["review_reasons"]),
                    "rubric_ambiguities": len(graded.get("rubric_ambiguities", [])),
                    "scored": graded["score"] is not None, "work_consistent": graded["interpretation"].get("work_consistent", "unclear"),
                    "attempts": graded["interpretation"].get("attempts", 1), "ambiguous_items": sum(bool(a.get("ambiguity")) for a in graded["applications"]), "lineage": lineage("primary")},
        "answer": {"check": answer["check"], "readers_agree": answer["readers_agree"], "work": answer["work"], "work_required": answer.get("work_required", False),
                   "key": bool(answer.get("key")), "full_credit": bool(answer.get("full_credit"))},
        "symbolic": symbolic or {"result": "not_run", "fail": 0, "pass": 0, "indeterminate": 0, "contradictions": []},
        "second": None, "verifier": None, "output": {"rejected_outputs": rejected},
        "rubric": {"exceptions": any(c["state"] == "needs_review" for c in condition_checks(rubric, graded["applications"])), "conditions": condition_checks(rubric, graded["applications"]), "warnings": len((rubric or {}).get("warnings", []))},
        "escalation": escalation or {"escalated": False, "depth": 0, "reasons": []}}
    # A family-blind batch (`standard.family_blind`, requirement, 2026-09-23) never asks whether a second opinion is of another family.
    from .standard import family_blind
    blind = family_blind(settings or {})
    # There, every second opinion is a separate assessment; before it, only one of another lineage was independent.
    independence = lambda role: ({"independent": True, "separate_assessment": True} if blind
                                 else {"independent": bool(lineage(role)) and lineage(role) != lineage("primary")})
    if second:
        result["second"] = {**compare(graded, second), "lineage": lineage("second"), **independence("second"),
                            "work_consistent": second["interpretation"].get("work_consistent", "unclear"),
                            "input_mode": second.get("input_mode", "transcript_only"),
                            "reading_issues": second.get("reading_issues", []), "review_reasons": second.get("review_reasons", [])}
    if verifier:
        material = [o for o in verifier["objections"] if o["severity"] == "material"]
        result["verifier"] = {"material": len(material), "minor": len(verifier["objections"]) - len(material), "no_material_contradiction": verifier["no_material_contradiction"],
                              "transcript_disagreements": len(verifier["transcript_disagreements"]), "lineage": lineage("verifier"),
                              **independence("verifier")}
    if blind:
        # Count the actual assessments; family metadata never combines or disqualifies them.
        voters = [result["primary"]["lineage"]]
        if result["second"] and result["second"]["agrees"]:
            voters.append(result["second"]["lineage"])
        result["votes"] = {"basis": "assessments", "lineages": sorted(set(filter(None, voters))), "count": len(voters)}
    else:
        # Batches frozen before the family rule: agreeing models that share a lineage are one vote.
        voters = {result["primary"]["lineage"] or "primary"}
        if result["second"] and result["second"]["agrees"]:
            voters.add(result["second"]["lineage"] or "primary")
        result["votes"] = {"lineages": sorted(voters), "count": len(voters)}
    from .config import ONE_SHOT_WORKFLOW  # A One-shot result (removed 2026-09-23), recalculated: its one call was its only reading.
    if (graded.get("one_shot") or {}).get("workflow") == ONE_SHOT_WORKFLOW:
        result["one_shot"] = {"workflow": ONE_SHOT_WORKFLOW, "reading_issues": graded.get("reading_issues", [])}
    if committee:
        result["committee"] = committee
    if (graded.get("choice") or {}).get("rule"):
        # A multiple-choice read (grading/choice.py): one reader read the marked option; its own gate (both orders at the frozen
        # cutoff) is the only confidence that decides.
        read = graded["choice"]
        result["choice"] = {"rule": read["rule"], "choice": read["choice"], "confidence": read["confidence"], "orders_agree": read["orders_agree"]}
    return result


def assess(c, policy, contradictions=()):
    """escalate: spend more compute. blocks: never accept unseen. reasons: say it to the instructor. level: order the review."""
    escalate, blocks, reasons = [], [], []
    ocr, reading, primary, answer, symbolic, second, verifier = c["ocr"], c["reading"], c["primary"], c["answer"], c["symbolic"], c["second"], c["verifier"]
    single = bool(c.get("one_shot"))
    # Direct reading: no OCR was bought, so the two checks that ask what a SECOND reader saw have nothing to answer them.
    # They are dropped by name into the verdict, never quietly satisfied by the reader's own first pass.
    direct, dropped = bool(c["ocr"].get("direct")), []
    # GRADING MULTIPLE-CHOICE READER: a confident read of a confirmed multiple-choice question asked no OCR and no grading team. The two
    # checks that ask for an independent reading and a second grader are dropped by name (`waived.choice_reader`), and the reported
    # confidences, which its own gate already decided, flag nothing; every other check stands. A gate that failed holds it.
    read, chosen = c.get("choice"), []
    if read and not (read.get("orders_agree") and read.get("confidence") is not None):
        blocks.append(CHOICE_UNSURE)
    if ocr["disagreements"]:
        escalate.append(READERS_DIFFER)
    # A reader's per-symbol rate where it reports one. Its whole-result confidence (the chance an entire block is perfect) is low for
    # almost any handwriting, so it is used only for a reader that reports nothing better.
    low_rate = ocr.get("min_rate") is not None and ocr["min_rate"] < policy.get("min_ocr_rate", DEFAULTS["min_ocr_rate"])
    unrated = ocr["min_unrated_confidence"] if "min_unrated_confidence" in ocr else ocr["min_confidence"]
    reported = confidence_checks(policy) and not read
    if reported and (low_rate or (unrated is not None and unrated < policy["min_confidence"] - .05)):
        escalate.append(READER_UNSURE)
    if ocr["providers"] < 1 and read:
        chosen.append(NO_OCR)
    elif ocr["providers"] < 1 and not single and not direct:
        blocks.append(NO_OCR)
    elif ocr["providers"] < 1 and direct:
        # The instructor turned the OCR reading off for this batch. The check is not answered by the reader's own pass: it is
        # removed, by name, and kept on the record (`signals.risk.waived.direct_reading`) with the mode in the acceptance scope.
        dropped.append(NO_OCR)
    elif ocr["agreement"] is not None and ocr["agreement"] < policy["min_ocr_agreement"]:
        blocks.append(OCR_APART)
    if (reported and (reading["visual_confidence"] is None or reading["visual_confidence"] < policy["min_confidence"] - .05)) or reading["ambiguities"] or reading["unclear"]:
        escalate.append(HANDWRITING)
    # Crossed-out work is retained evidence, not a concern by itself. Active-work
    # ambiguity and a grader quoting a struck final answer still have their own checks.
    if not reading["registered"]:
        escalate.append(CROP_UNSURE)
    if primary["attempts"] and primary["attempts"] > 1:
        escalate.append(ATTEMPTS)
    if (reported and (primary["confidence"] < policy["min_confidence"] or (primary["min_item_confidence"] is not None and primary["min_item_confidence"] < policy["min_confidence"]))) or primary["ambiguous_items"]:
        escalate.append(GRADER_UNSURE)
    if not primary["scored"]:
        escalate.append(UNSCORABLE)
    if primary["review_reasons"]:
        escalate.append(GRADER_ASKED)
    if primary.get("rubric_ambiguities"):
        # Wording the rubric leaves open fools every model alike: another model cannot settle it, only the instructor can.
        blocks.append(WORDING)
    if reading.get("blank_crops"):
        # A block, not a reason: the reading reason itself comes from `recognition.review_state`, and a reason here would read as a
        # grading doubt too. It is never waived (below) and buys no compute.
        blocks.append(blank_reason(reading["blank_crops"]))
    # An error the proposal already accounts for (partial credit for wrong work) is ordinary grading, recorded and not escalated:
    # half a class makes some error. What matters is evidence that CONTRADICTS the proposal: full credit for inconsistent work.
    if primary["work_consistent"] == "no" and answer["full_credit"]:
        escalate.append(INCONSISTENT)
        reasons.append(INCONSISTENT_WHY)
    if symbolic["fail"] and answer["full_credit"]:
        escalate.append(SYMBOLIC_FAILS)
        reasons.append(SYMBOLIC_PREFIX + " ".join(symbolic["contradictions"][:2]))
    if answer["check"] == "match" and (symbolic["fail"] or primary["work_consistent"] == "no"):
        escalate.append(ANSWER_INCONSISTENT)
    if contradictions:
        escalate.append(CONTRADICTS)
    if c["output"]["rejected_outputs"]:
        escalate.append(MALFORMED)
    disagreement = selection_disagreement(c)
    differs = bool(disagreement)
    second_reading = bool(second and second.get("reading_issues"))
    if second:
        if second_reading:
            blocks.append(SECOND_READING)
            reasons.extend(f"{SECOND_READING_PREFIX}{issue['kind']}): {issue['description']}" for issue in second["reading_issues"])
        if second.get("review_reasons"):
            blocks.append(SECOND_ASKED)
            reasons.extend(f"{SECOND_PREFIX}{reason}" for reason in second["review_reasons"])
        if not second.get("independent", True):
            blocks.append(ONE_LINEAGE)  # Never in a family-blind batch, where every second opinion is a separate assessment.
    elif policy["require_second_grader"] and read:
        chosen.append(NOBODY_ELSE)
    elif policy["require_second_grader"] and not single and not ((c.get("committee") or {}).get("pair")):
        # An unresolved team has no SUPPORTER, which is not the same as nobody having looked: where a second grader's assessment was
        # compared (`pair`), the team's own reason is the block (25 of 25 such responses carried this wrong sentence, 2026-09-21).
        blocks.append(NOBODY_ELSE)
    if differs:
        escalate.append(DISAGREE)
        reasons.append(disagreement_reason(disagreement))
    committee = c.get("committee") or {}
    team_unresolved = committee.get("status") == "unresolved"
    if team_unresolved:
        reasons.append(committee["reason"])
        blocks.append(committee["reason"])
    if committee.get("review_reasons"):
        reasons.extend(committee["review_reasons"])
        blocks.extend(committee["review_reasons"])
    if verifier and (verifier["material"] or not verifier["no_material_contradiction"]):
        blocks.append(OBJECTION)
        if policy["verifier_routes"]:
            reasons.append(OBJECTION_WHY)
    if verifier and verifier["transcript_disagreements"]:
        blocks.append(IMAGE_DIFFERS)
    # A wrong final answer is expected for many partial-credit proposals. Apply
    # this acceptance gate only when the deterministic total reaches full marks.
    if policy["require_key_match"] and answer["full_credit"] and not (answer["key"] and answer["check"] == "match"):
        blocks.append(NO_KEY if not answer["key"] else KEY_DIFFERS if answer["check"] == "mismatch" else KEY_UNREAD)
    if answer["readers_agree"] is False and not single and not direct:  # One-shot and direct reading have no independent reader to compare.
        blocks.append(ONE_READING)
    elif answer["readers_agree"] is False and direct:
        dropped.append(ONE_READING)
    if policy["require_work_shown"] and answer["full_credit"] and answer["work_required"] and answer["work"] != "shown":
        blocks.append(WORK_BLOCK)
    if c["rubric"]["exceptions"]:
        blocks.append(CONDITION)
    if single:
        for issue in c["one_shot"]["reading_issues"]:
            reason = f"{ONE_SHOT_PREFIX}{issue['kind']}): {issue['description']}"
            blocks.append(reason)
            reasons.append(reason)
    blocks.extend(escalate)
    if symbolic["fail"] and not answer["full_credit"]:
        blocks.append(MATH_ERROR)
    severe = bool(team_unresolved or (symbolic["fail"] and answer["full_credit"]) or differs or (verifier and verifier["material"])
                  or (primary["work_consistent"] == "no" and answer["full_credit"]) or not primary["scored"] or contradictions)
    # Where independent evidence disagrees with the proposal itself, one look at a contact sheet is not enough.
    individual = bool(team_unresolved or committee.get("review_reasons") or differs or second_reading or (second and second.get("review_reasons")) or (symbolic["fail"] and answer["full_credit"]) or not primary["scored"]
                      or (policy["verifier_routes"] and verifier and verifier["material"]))
    verdict = {"policy": POLICY, "thresholds": policy, "level": "high" if severe else "elevated" if escalate or second_reading or (second and second.get("review_reasons")) else "low", "individual": individual,
               "escalate": list(dict.fromkeys(escalate)), "blocks": list(dict.fromkeys(blocks)), "reasons": list(dict.fromkeys(reasons))}
    if dropped:
        verdict["waived"] = {**verdict.get("waived", {}), "direct_reading": {"rule": c["ocr"]["direct"], "blocks": list(dict.fromkeys(dropped))}}
    if chosen:
        verdict["waived"] = {**verdict.get("waived", {}), "choice_reader": {"rule": read["rule"], "blocks": list(dict.fromkeys(chosen))}}
    if review_policy(policy) == "disagreement_and_work" and not single:
        # The narrow policy. Everything above was computed and stays on the record; only what may HOLD THE RESPONSE BACK changes.
        kept = {WORK_BLOCK} if WORK_BLOCK in verdict["blocks"] else set()
        if team_unresolved:
            kept.update({committee["reason"], *committee.get("review_reasons", [])} & set(verdict["blocks"]))
        elif differs:
            # A job without a team (legacy, local): its two graders' disagreement IS "the grading team did not agree".
            kept.update({DISAGREE, disagreement_reason(disagreement)})
        if not primary["scored"]:
            kept.add(UNSCORABLE)  # Not an opinion: there is no score to finalize.
        kept.update(b for b in verdict["blocks"] if blank(b))  # Not an opinion either: the instructor decides whether it was left blank.
        # Merged, never replaced: the checks direct reading dropped by name stay on the record beside what this policy waives.
        verdict["waived"] = {**verdict.get("waived", {}), "blocks": [b for b in verdict["blocks"] if b not in kept],
                             "reasons": [r for r in verdict["reasons"] if r not in kept], "policy": "disagreement_and_work"}
        verdict["blocks"] = [b for b in verdict["blocks"] if b in kept]
        verdict["reasons"] = [r for r in verdict["reasons"] if r in kept] + ([WORK_BLOCK] if WORK_BLOCK in kept else [])
        verdict["individual"] = bool(team_unresolved or differs or WORK_BLOCK in kept or not primary["scored"])
        verdict["level"] = "high" if verdict["blocks"] else "low"
    return verdict
