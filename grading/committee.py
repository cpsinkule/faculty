"""Conditional grading teams, retained inside the existing per-response grading stage.

These are receipts of the term-owned response, not new owners. A decision is always
one complete, validated model assessment; never a mixture of rubric votes or scores.
Old jobs without the frozen workflow marker continue through the legacy pipeline.
"""
from . import arbitration, config, grader_deductions, recognition, risk, rubric_quality, standard
from .providers.base import ProviderError
from .reading_context import for_graders

WORKFLOW = "grading-teams-v1"  # Named by migration 013's guard on team acceptances: rules that change get their own key below.
# Requirement, 2026-09-21: the additional assessment is bought only where a third VOTE can settle something. New batches freeze
# this rule in their profile; a batch submitted without it finishes under the rule it ran with, and its history reads as it ran.
# third-vote-v2 (2026-09-20): do not buy when both scores are the question maximum; the third settles on credit-bearing lines.
# third-vote-v3 (2026-09-22): the reviewer is bought ONLY where the two graders DISAGREE, in both cloud tiers.
LEGACY_THIRD_RULE = "third-vote-v1"
CREDIT_THIRD_RULE = "third-vote-v2"
THIRD_RULE = "third-vote-v3"
THIRD_RULES = (LEGACY_THIRD_RULE, CREDIT_THIRD_RULE, THIRD_RULE)
CREDIT_RULES = (CREDIT_THIRD_RULE, THIRD_RULE)  # From v2 on, a vote is compared on credit-bearing lines and a full-credit pair buys nothing.
STANDARD = "cloud_standard"


def credit_only(rule):
    """Whether this batch's frozen rule compares a vote on credit-bearing lines alone."""
    return rule in CREDIT_RULES


def _credit_bearing(opinion, rubric):
    """Applied nonzero-point lines. A zero-point-only split is not a different grade."""
    points = {item["id"]: item["points"] for item in rubric["items"]}
    return frozenset(a["rubric_item_id"] for a in opinion["applications"]
                     if a.get("applied") and abs(points.get(a["rubric_item_id"], 0)) > 1e-9)


def _full_credit(pair, maximum):
    score = pair.get("primary_score") if pair else None
    return bool(pair and pair["same_score"] and score is not None and maximum is not None
                and abs(score - maximum) < 1e-9)


def enabled(settings):
    return settings.get("grading_workflow") == WORKFLOW


def third_votes(primary, second, *, maximum=None, rule=None, families=True):
    """Why a third assessment could settle this response, or [] when it could not.

    Measured on 2026-09-20: 54 of 130 additional assessments were bought for pairs that agreed on every rubric line, because
    of doubts no vote can settle (rubric wording, an ambiguous reading, alignment, a grader's own request for a look). 33 of
    the 34 that finished stayed flagged for exactly those doubts. They still flag the response and block acceptance; they
    are no longer a purchase order. A reading problem the second grader reports is the same: 30 agreeing pairs bought the
    reviewer for one, all 15 that finished came back as the same proposal, and all 15 stayed flagged, because that problem
    blocks the response whatever a third grader selects. Replayed over that batch, this rule buys 69 assessments, not 130.
    third-vote-v2 does not buy when both scores already equal the question maximum (full-credit packaging). Same-score
    partial credit still buys: those lines change the number.

    third-vote-v3 (requirement, 2026-09-22, both cloud tiers): the additional reviewer is called ONLY where the two graders
    DISAGREE. A missing, failed or unscorable assessment stays with the instructor. In a batch frozen with
    `standard.FAMILY_RULE` model families do not affect agreement or voting (requirement, 2026-09-23); before it, an agreement
    inside one family was no disagreement either and bought nothing.
    """
    pair = decide(primary, second, families=families)
    if pair["status"] == "resolved":
        return []
    differ = bool(pair["pair"]) and not pair["pair"]["agrees"]
    if credit_only(rule) and _full_credit(pair["pair"], maximum):
        return []
    if rule == THIRD_RULE and not differ:
        return []
    return [
        "The primary and secondary graders differ on " + ", ".join(pair["pair"]["disagreements"]) + "." if differ
        else "No completed secondary assessment is available." if not second or second.get("failed")
        else "The two graders share a model family, so their agreement is one vote." if pair["pair"]
        else "A required primary or secondary assessment is unscorable."]


def cut_off(opinion):
    """Whether a grader's opinion is a failure at the gateway's time limit (`providers.base.STREAM_DURATION`)."""
    return bool(opinion and opinion.get("failed") and opinion.get("stream_duration"))


def initial_roles(settings, mode):
    return config.roles(settings, mode)


def assessment(value):
    """Only the validated assessment, not vendor responses or hidden reasoning."""
    return {k: v for k, v in value.items() if k != "provider_response"} if value else None


def _evidence(opinion):
    """A teammate's citation audit is not a request for a look, and is not the standing quotation."""
    if not opinion:
        return opinion
    kept = [r for r in opinion.get("review_reasons", []) if not recognition.citation_audit(r)]
    return opinion if kept == list(opinion.get("review_reasons", [])) else {**opinion, "review_reasons": kept}


def _standing(decision, opinions, third):
    proposal = opinions[decision["source"]]
    supporter = opinions.get(decision["supporter"])
    if decision.get("source") == decision.get("supporter") and third and third is not proposal:
        supporter = third
    return proposal, _evidence(supporter)


def decide(primary, second, third=None, *, required=False, independent=True, rubric=None, rule=None, families=True):
    """Compare every yes/no selection, including zero-point and ceiling-absorbed lines.

    third-vote-v2 lets the additional reviewer settle by matching one side on every credit-bearing line. The full
    line-by-line comparison stays on the receipt. When that match is credit-bearing only, the standing grade is the
    matched side (not the third's extra zero-point ticks).

    `families` False (`standard.family_blind`, requirement, 2026-09-23): two graders of one model family agreeing is an agreement,
    and a third may support an assessment of its own family. Otherwise a family agreeing with itself is one vote, as before.
    """
    valid = lambda opinion: bool(opinion and not opinion.get("failed") and opinion.get("score") is not None)
    pair = risk.compare(primary, second) if valid(primary) and valid(second) else None
    result = {"status": "unresolved", "source": "primary", "supporter": None,
              "pair": pair, "third_comparisons": {}, "third_independent": independent, "reason": ""}
    if not required and pair and pair["agrees"] and (not families or primary.get('lineage', '').casefold() != second.get('lineage', '').casefold()):
        result.update(status="resolved", supporter="second", reason="The primary and secondary graders agree on every rubric selection.")
        return result
    if required and not valid(third):
        result["reason"] = "The additional reviewer is required but is missing, failed, or returned an unscorable assessment."
        return result
    if valid(third):
        result["source"] = "verifier"
        sides = {"primary": primary, "second": second}
        for role, opinion in sides.items():
            if valid(opinion):
                result["third_comparisons"][role] = risk.compare(third, opinion)
        # Before family-blind-v1 even an informed tie-break had to be from a different family than the assessment it
        # supports (a model agreeing with its own family was one vote); batches frozen that way still finish that way.
        def supports(role, comparison):
            opinion = sides[role]
            if families and not (third.get("lineage", "").casefold() and third["lineage"].casefold() != opinion.get("lineage", "").casefold()):
                return False
            if comparison["agrees"]:
                return True
            return bool(credit_only(rule) and rubric and _credit_bearing(third, rubric) == _credit_bearing(opinion, rubric))
        matches = [role for role, comparison in result["third_comparisons"].items() if supports(role, comparison)]
        if not valid(primary) or not valid(second):
            result['reason'] = risk.INCOMPLETE
        elif matches:
            side, all_lines = matches[0], result["third_comparisons"][matches[0]]["agrees"]
            # The standing grade is the matched side: its ticks, and its quotations. The third is the vote, not the
            # citation record (measured 2026-09-21: an empty evidence slot on the third listed as a failed quote).
            result.update(status="resolved", source=side, supporter=side if all_lines else "verifier",
                          reason="The additional reviewer agrees with the " + side + " grader on every "
                          + ("rubric selection." if all_lines else "credit-bearing rubric selection."))
        else:
            result["reason"] = "The additional reviewer does not agree with either independent assessment on all rubric selections; review this response individually."
    else:
        # No third was bought: report the incomplete pair or the disagreement awaiting arbitration. Only a batch frozen before
        # `standard.FAMILY_RULE` can end here with an agreement, one inside one model family; it keeps the words it was made with.
        result["reason"] = (risk.NO_SECOND if not valid(second) else risk.NO_PRIMARY if not valid(primary)
                            else ("The primary and secondary graders differ on " + (", ".join(pair["disagreements"]) or "the rubric selections")
                                  + "; review this response individually.") if not pair["agrees"]
                            else "The grading team has no agreement between two model families; review this response individually.")
    return result


def run(service, task, job, settings, owner, existing, content, rubric, reading, verified, crops, images, ask):
    mode = service.compute(job)
    payload = service.grade_payload(task["response_id"], content, rubric, verified["transcript"], for_graders(reading, verified, crops, settings))
    # The approved deductions the two graders decide (GRADING "GRADERS TAKE OFF APPROVED DEDUCTIONS"), only in a batch frozen with it.
    choices = grader_deductions.offered(payload["rubric"], settings)
    if choices:
        payload = {**payload, "deductions": choices}

    def call(key, role, endpoint, tier, *, informed=False, opinions=None, optional=None):
        pictures = images if endpoint["modality"] == "vision" else []
        contract = ("adjudicate" if informed else "third_grade" if role == "verifier" else standard.primary_contract(settings, pictures) if role == "primary"
                    else "second_vision" if pictures else "transcript_grade")
        material = {**payload, "grader_assessments": opinions} if informed else payload
        def validate(output):
            value = recognition.validate_grade(output, verified["transcript"], rubric, content["max_points"], max(1, len(pictures)),
                                               choices if role in grader_deductions.ROLES and not informed else ())
            if {a["rubric_item_id"] for a in value["applications"]} != {item["id"] for item in rubric["items"]}:
                raise ValueError("A team assessment must decide every rubric item.")
            if settings.get("full_credit_rule") in rubric_quality.FULL_CREDIT_RULES:
                value = rubric_quality.full_credit_alone(value, content.get("rubric_guidance"), rubric, content["max_points"], settings["full_credit_rule"])
            return value
        key, value, metadata, advance = ask(key, role, endpoint, contract, material, pictures, validate, role != "primary" if optional is None else optional, call_tier=tier)
        value.update(input_mode="images_and_transcript" if pictures else "transcript_only", saw_other_decisions=informed)
        return key, value, metadata, advance

    def evaluate(proposal, supporter, roles, decision=None):
        proposal_roles = {**roles, "primary": proposal, "second": supporter}
        return service.evidence(task, settings, content, rubric, reading, verified, crops, proposal,
                                supporter, None, proposal_roles, committee=decision)[-1]

    panels = []
    # High Assurance's second team was removed on 2026-09-23: one team, never escalated. The record keeps its shape.
    escalation = {"escalated": False, "depth": 0, "from": STANDARD, "to": STANDARD, "reasons": [],
                  "considered": [], "maximum_depth": 0}
    tier, prefix = STANDARD, "grade:"
    roles = config.roles(settings, tier)
    primary_key, second_key, third_key = (prefix + role for role in ("primary", "second", "verifier"))
    # An optional spare for the two graders (requirement, 2026-09-21). It is asked ONLY for a request that has exhausted its
    # own attempt cap, on its own retained step and with its own budget, and only once. Nothing is switched permanently:
    # the next response starts again with the configured grader, because these keys belong to this task alone.
    spare = lambda role: config.fallback(settings, tier, role)
    primary_spare, second_spare = (prefix + role + ":fallback" for role in ("primary", "second"))
    # A grader cut off at the gateway's time limit in an EARLIER attempt is asked again: the instructor retried the response.
    again = lambda key: cut_off(existing.get(key)) and existing[key].get("task_attempt", 0) < task["attempt"]
    if primary_key not in existing or again(primary_key):
        # With a spare configured the primary's own failure is retained instead of failing the task, so the spare can be asked.
        return call(primary_key, "primary", roles["primary"], tier, optional=bool(spare("primary")))
    if existing[primary_key].get("failed") and spare("primary") and (primary_spare not in existing or again(primary_spare)):
        return call(primary_spare, "primary", spare("primary"), tier, optional=True)
    if roles.get("second") and (second_key not in existing or again(second_key)):
        return call(second_key, "second", roles["second"], tier)
    if (existing.get(second_key) or {}).get("failed") and spare("second") and (second_spare not in existing or again(second_spare)):
        return call(second_spare, "second", spare("second"), tier, optional=True)
    # The completed spare stands in for the grader it replaced; if it failed too, the record of the last attempt is kept.
    primary = existing[primary_spare] if existing.get(primary_spare) else existing[primary_key]
    second = existing[second_spare] if existing.get(second_spare) else existing.get(second_key)
    if primary.get("failed"):
        raise ProviderError(f"The primary grader could not complete this response ({primary.get('error', 'no reason recorded')}). Retry the batch when it can.")
    if cut_off(second):
        # Not a missing opinion to leave with the instructor: the grader never finished. The response fails, is listed under Failed to
        # grade, and the batch's Retry asks the second grader again (requirement, 2026-09-27).
        raise ProviderError(f"The second grader could not finish this response ({second.get('error', 'no reason recorded')}). Retry the batch's failed responses when ready.", fixable=False)
    # Every check and receipt must name the model that actually graded, including a fallback.
    roles = {**roles, "primary": spare("primary") if existing.get(primary_spare) else roles["primary"],
             **({"second": spare("second") if existing.get(second_spare) else roles["second"]} if roles.get("second") else {})}
    check_key = prefix + "check"
    if check_key not in existing:
        verdict = evaluate(primary, second, roles)
        # Acceptance-only requirements (e.g. no answer key) do not cause extra
        # model calls. Actual grading/reading concerns do.
        reasons = list(dict.fromkeys([*verdict["escalate"], *verdict["reasons"],
            *([] if second and not second.get("failed") else ["No completed secondary assessment is available."])]))
        reasons.extend(primary.get('rubric_ambiguities', []))
        reported = risk.confidence_checks(settings)
        if second and not second.get('failed') and ((reported and second['confidence'] < settings['risk']['min_confidence'])
                or any(a.get('ambiguity') or (reported and a['applied'] and a['confidence'] < settings['risk']['min_confidence']) for a in second['applications'])):
            reasons.append('The secondary grader was uncertain about a rubric decision.')
        # The additional reviewer is the ARBITRATOR in this mode, so no third assessment is bought for an ordinary
        # tie-break: a response that would wait goes straight to arbitration, and that connection is called once, there.
        reserved = arbitration.reviewer_arbitrates(settings)
        if standard.enabled(settings):
            pair = decide(primary, second, families=not standard.family_blind(settings))
            return check_key, {"needs_third": False, "reasons": [], "noted": reasons,
                "arbitrator_reserved": [pair["reason"]] if standard.disagreement(pair) else [],
                "escalation": escalation}, {"workflow": WORKFLOW, "cloud_standard_rule": standard.RULE}, False
        if settings.get("third_rule") in THIRD_RULES:
            # The doubts above stay on the record and on the review card (`noted`); only a vote that can settle something is
            # bought, and under third-vote-v3 only a disagreement between the two graders is one.
            votes = third_votes(primary, second, maximum=content["max_points"], rule=settings["third_rule"],
                                families=not standard.family_blind(settings))
            return check_key, {"needs_third": bool(votes) and not reserved, "reasons": [] if reserved else votes, "noted": reasons,
                               **({"arbitrator_reserved": votes} if reserved else {}), "escalation": escalation}, {"workflow": WORKFLOW, "third_rule": settings["third_rule"]}, False
        return check_key, {"needs_third": bool(reasons) and not reserved, "reasons": [] if reserved else reasons,
                           **({"arbitrator_reserved": reasons} if reserved else {}), "escalation": escalation}, {"workflow": WORKFLOW}, False
    check = existing[check_key]
    if check["needs_third"] and roles.get("verifier") and third_key not in existing:
        opinions = {"primary": assessment(primary), "secondary": assessment(second)}
        return call(third_key, "verifier", roles["verifier"], tier, informed=True, opinions=opinions)
    third = existing.get(third_key)
    decision_key = prefix + "decision"
    if decision_key not in existing:
        decision = {**decide(primary, second, third, required=check["needs_third"], independent=False,
                            rubric=rubric, rule=settings.get("third_rule"), families=not standard.family_blind(settings)),
                    "workflow": WORKFLOW, "tier": tier, "third_required": check["needs_third"]}
        opinions = {"primary": primary, "second": second, "verifier": third}
        proposal, supporter = _standing(decision, opinions, third)
        # Explicit reading/interpretation concerns cannot disappear just because
        # two selection maps match. The full second round may settle first-round
        # doubts; within a round, preserve every grader's concrete request.
        # Faculty's citation audit stays on the assessment that offered the quotation; it is not a team concern.
        concerns = []
        for role, opinion in opinions.items():
            if opinion and not opinion.get("failed"):
                concerns.extend(f"{role}: {r}" for r in opinion.get("review_reasons", []) if not recognition.citation_audit(r))
                concerns.extend(f"{role}: {issue['description']}" for issue in opinion.get("reading_issues", []))
        decision["review_reasons"] = list(dict.fromkeys(concerns))
        verdict = evaluate(proposal, supporter, roles, decision)
        remaining = list(dict.fromkeys([*verdict["escalate"], *verdict["reasons"]]))
        # A grader explicitly requesting a look is a reason to escalate even
        # if its rubric selections agree with another grader.
        if decision["status"] != "resolved":
            remaining.append(decision["reason"])
        next_escalation = {**escalation, "considered": remaining}
        return decision_key, {"committee": decision, "escalation": next_escalation}, {"workflow": WORKFLOW}, False
    recorded = existing[decision_key]
    decision, escalation = recorded["committee"], recorded["escalation"]
    opinions = {"primary": primary, "second": second, "verifier": third}
    panels.append({"tier": tier, "decision": decision, "opinions": {r: assessment(v) for r, v in opinions.items() if v}})
    proposal, supporter = _standing(decision, opinions, third)
    chosen_roles = {"reader": initial_roles(settings, mode)["reader"], "primary": proposal, "second": supporter}
    # Keep original role names and all opinions in panels. These effective roles
    # describe the complete proposal and its matching supporter for risk checks.
    final_opinions = {"second": assessment(supporter), "roles": {r: {"endpoint": e.get("endpoint", e.get("name")),
        "model": e["model"], "lineage": e["lineage"]} for r, e in chosen_roles.items() if e},
        "committee": decision, "panels": panels, "escalation": escalation}
    if standard.enabled(settings) or arbitration.enabled(settings):
        # The team has run as it always does. Only a response that would now wait for the instructor goes on to the arbitrator (and,
        # in a Cloud batch, the flag reviewer), one retained component per turn, and neither replaces these panels.
        run_review = standard.run if standard.enabled(settings) else arbitration.run
        kind, *rest = run_review(service, task, job, settings, existing, content, rubric, reading, verified, crops, images, ask, payload,
                                      panels, decision, proposal, final_opinions, tier)
        if kind == "step":
            return rest[0]
        proposal, final_opinions = rest
    return "grading", {**proposal, "opinions": final_opinions}, {"workflow": WORKFLOW, "tier": tier}, True
