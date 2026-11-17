"""The Cloud tier's disagreement arbitrator: Final Arbitrator Mode's v2 rule, frozen by every Cloud batch (`standard.freeze`).

Final Arbitrator Mode began as an optional setting with its own model (v1, requirement, 2026-09-21); the Cloud tier now always
arbitrates this way, with its additional reviewer, and the optional setting, v1 and the Jev tie-breaker were removed on 2026-09-23.
Results that recorded them stay readable (`basis`).

The teams run exactly as they always do. A response that would then WAIT FOR THE INSTRUCTOR (flagged for review, or proposed but
withheld from automatic finalization) is put before one chosen model together with everything known about it: the original
inputs and crops, every earlier model's complete validated assessment, each team's decision, and every flag. It must decide:

1. `adopt`: one earlier assessment's rubric ticks are the accurate final grade. That complete, validated assessment stands.
2. `revise`: none of them is, and it poses its own complete set of ticks, validated like any grader's.
3. `unreliable`: the problems are so severe that no reliable grade can be determined. The response stays with the instructor.

What this is NOT. It never averages scores or merges votes line by line: the standing grade is always ONE complete assessment, and
Faculty calculates the points. It finalizes nothing by its say-so alone: a ruling settles only the flags the arbitrator was SHOWN
and marked resolved. The final ticks go through every deterministic check again (`jobs.scored`), and any flag that is new, or that
the arbitrator left standing, keeps the response with the instructor, with the ruling on its review card. No model can settle the
rubric finalization (an exclusion the final ticks break, a question still open), an unscorable selection, a blank crop
(direct-reading-v2), or an instructor's own review; under Cloud Standard, no flag of `risk.instructor_only` either.
A failed or malformed ruling leaves the response exactly as the team left it.

Everything lives INSIDE the existing `grading` stage as two more retained steps (`grade:arbiter:check`, `grade:arbiter`): receipts
of the term-owned response, no new stage, table or owner. A batch frozen without the rule runs as it always did.
"""
import copy
import re

from rosters import connect

from . import config, recognition, rubric_quality
from . import risk as risk_rules

# THE ADDITIONAL REVIEWER IS THE ARBITRATOR (requirement, 2026-09-21). The team runs per usual, except that its third grader is
# never bought for an ordinary tie-break: the response that would wait goes to arbitration AT ONCE, and the tier's additional
# reviewer is the model that arbitrates it. So that connection is called at most once per response, and only to arbitrate.
RULE = "final-arbitrator-v2"  # Frozen per batch and named in its acceptance scope: another rule is another scope.
PROMPT = "arbitrate"
CHECK, STEP = "grade:arbiter:check", "grade:arbiter"
DECISIONS = ("adopt", "revise", "unreliable")
_ROLE = re.compile(r"^(?:primary|second|secondary|verifier|reviewer|arbiter|Second grader(?: \([a-z]+\))?|Additional reviewer): ", re.I)


def enabled(settings):
    """Whether this batch froze the arbitrator (every Cloud batch does, `standard.freeze`)."""
    return settings.get("final_arbitrator_rule") == RULE and bool(settings.get("final_arbitrator"))


def reviewer_arbitrates(settings):
    """Whether the team's additional reviewer is the arbitrator, so no third assessment is bought for an ordinary tie-break."""
    return enabled(settings)


def arbitrator(settings, tier):
    """The connection that arbitrates this round, or None: the tier's additional reviewer."""
    return config.roles(settings, tier).get("verifier")


def scope(settings):
    """What an acceptance scope says about the arbitrator, or None. Its prompt is named here and not among the team prompts, so
    the scope of a batch WITHOUT the mode is the scope it always had."""
    if not enabled(settings):
        return None
    from .providers.local_vlm import prompt
    # The model is the tier's additional reviewer, already named with its own reasoning choice in `scope["teams"]`.
    return {"rule": RULE, "arbitrator": "additional_reviewer", "prompt": prompt(PROMPT)[1]["prompt_sha256"]}


def _same(text):
    """One flag, whichever grader's name a rule put in front of it: rulings are matched on what the flag SAYS."""
    return _ROLE.sub("", str(text)).strip()


# One disagreement between the two graders reached the arbitrator as three flags (trial batch 1c1ce6cb, 2026-09-22, on all 51
# arbitrated responses): the check's bare "disagree.", the same check naming the lines, and the team's own reason naming them again.
# The one naming the lines is kept; the other two say nothing it does not. A disagreement never reappears once a ruling stands (the
# team is then resolved), so a ruling on the kept flag settles all it ever said.
DISAGREE = risk_rules.DISAGREE
DISAGREE_ON = "The primary grader and the second grader disagree on "
TEAM_DIFFERS = "The primary and secondary graders differ on "
# What `apply` says when the ruling's own grade is scored again and a flag still stands, by the words it begins with (2026-10-11: the
# review card's causes read it, `review_queue.reason_causes`); the words are unchanged.
UNSETTLED = "The final arbitrator's grade raised or left a flag it did not settle: "


def flags(output):
    """Every reason a scored proposal would wait for the instructor, in a stable order, without repeats."""
    risk = output["signals"].get("risk") or {}
    found = [*risk.get("blocks", []), *output.get("reasons", []), *((output["signals"].get("rubric_quality") or {}).get("reasons") or [])]
    if output.get("score") is None:
        found.append("The proposed rubric lines cannot be combined under the approved rules.")
    found = list(dict.fromkeys(_same(text) for text in found if str(text).strip()))
    if any(text.startswith(DISAGREE_ON) for text in found):
        found = [text for text in found if text != DISAGREE and not text.startswith(TEAM_DIFFERS)]
    return found


# A SETTLED NOTE STAYS SETTLED (requirement, 2026-10-26). A grader's own
# requests and rubric doubts reach a ruling by their words, under the grader's name ("second: …"), beside a sentence that names only
# who asked (`risk.SECOND_ASKED`). When the ruling adopts that grader's assessment, the scored grade asks the same thing as the
# primary's (`risk.GRADER_ASKED`, `risk.WORDING`), a sentence the ruling was never shown, and the grade waited although every word
# behind it was settled: 16 of the 19 Exam 2 grades the flag review settled on 2026-10-25 came back so (the instructor kept 14).
# Frozen per batch; a batch without it finishes as it ran.
SETTLED_RULE = "settled-notes-v1"


def settled_notes(settings):
    return (settings or {}).get("settled_notes_rule") == SETTLED_RULE


def renamed_notes(standing, resolved, proposal, theirs=lambda text: False):
    """`standing` without the sentences that only say a grader asked, or found the wording unclear, when every request or doubt
    of the proposal behind them was shown and ruled resolved (`resolved`, matched as `_same`). A sentence only the instructor
    settles (`theirs`) always stands, and so does a generic sentence with any word behind it the ruling did not settle."""
    if not proposal:
        return standing
    from .recognition import RUBRIC_EXCEPTIONS
    from .review_rules import CONDITION_PREFIX
    doubts = [str(d) for d in proposal.get("rubric_ambiguities") or []]
    asked = [str(r) for r in proposal.get("review_reasons") or []
             if r != RUBRIC_EXCEPTIONS and not str(r).startswith(CONDITION_PREFIX) and r not in doubts]
    settled = lambda texts: bool(texts) and all(_same(t) in resolved for t in texts)
    behind = {risk_rules.GRADER_ASKED: asked, risk_rules.WORDING: doubts}
    return [text for text in standing if theirs(text) or text not in behind or not settled(behind[text])]


def answer_facts(output):
    """What Faculty compared with the confirmed answer key for one scored assessment, for a model ruling on a flag about it.
    Evidence only: `result` match | mismatch | unknown (Faculty could not read the notation, or nothing was reported), never a grade."""
    found = (output.get("signals") or {}).get("answer") or {}
    return {"result": found.get("check", "unknown"), "compared": [{"part": a.get("part", ""), "text": a.get("text", "")} for a in found.get("answers", [])],
            "work": found.get("work")}


def waits(output):
    """Whether this scored proposal would wait for the instructor instead of being finalized by the team."""
    risk = output["signals"].get("risk") or {}
    committee = output["signals"].get("committee") or {}
    return bool(output["state"] != "proposed" or risk.get("blocks") or risk.get("individual") or output.get("score") is None
                or (output["signals"].get("rubric_quality") or {}).get("reasons") or committee.get("status") != "resolved")


def candidates(existing):
    """{label: step key} of every earlier COMPLETE, scorable assessment of the team."""
    return {label: key for label, key in assessed(existing).items() if existing[key].get("score") is not None}


def label_of(existing, proposal, labels):
    """The label of the standing assessment: the retained step it IS, else one equal to it; "" when none is (never a guess)."""
    return (next((label for label, key in labels.items() if existing[key] is proposal), "")
            or next((label for label, key in labels.items() if existing[key] == proposal), ""))


def _guarded(record):
    """Whether this ruling was made under Cloud Standard, where some flags are the instructor's alone (`risk.instructor_only`).
    Historical arbitrator batches keep the rulings they ran with, except over a blank crop (`apply`)."""
    from . import standard
    return standard.RULE in (record.get("rule"), record.get("cloud_standard_rule"))


def _independent(ruling, opinion):
    """Before `standard.FAMILY_RULE`: a ruling for an assessment of its OWN model family is that family voting for itself."""
    mine = str(ruling.get("lineage") or "").casefold()
    return bool(mine) and mine != str(opinion.get("lineage") or "").casefold()


# Who made an assessment. Under `standard.IDENTITY_RULE` a model ruling on earlier assessments reads each by its label and
# `input_mode` only (requirement, 2026-09-24): which model graded is no evidence about the student's work.
IDENTITY = ("endpoint", "model", "lineage")


def _public(opinion, blind=False):
    return {k: v for k, v in opinion.items() if k not in ("provider_response", "opinions") and not (blind and k in IDENTITY)}


def material(payload, existing, panels, standing, shown, content=None, answers=None, blind=False):
    """What the arbitrator (and Cloud Standard's flag reviewer) reads beside the original grading inputs.

    The graders are never shown the answer key (`grade_payload`). A model asked to rule on a flag ABOUT the key is: the confirmed key
    and what Faculty compared with it (`answer_facts`), retained on the check step. Measured on the trial's batch 1c1ce6cb: the flag
    reviewer ruled on 22 "does not match a confirmed answer key" flags without either, and said so in its notes. A check retained
    before the facts were kept sends what it always sent. `blind` (`standard.model_blind`) leaves out which model made each assessment."""
    labels = candidates(existing)
    return {**payload, "assessments": {label: _public(existing[key], blind) for label, key in labels.items()}, "standing": standing,
            "decisions": [{"team": "standard", **{k: panel["decision"].get(k) for k in ("status", "reason", "source", "supporter", "pair", "third_comparisons")}} for panel in panels],
            "flags": shown,
            **({"answer_key": [{"label": part.get("label", ""), "accept": list(part.get("accept", []))} for part in (content or {}).get("answer_key") or []],
                "answer_checks": answers} if answers is not None else {})}


def validate(raw, labels, shown, graded):
    """The ruling, or ValueError. `graded` validates an embedded assessment exactly as a grader's is validated."""
    if not isinstance(raw, dict) or raw.get("decision") not in DECISIONS:
        raise ValueError("The final arbitrator must decide adopt, revise or unreliable.")
    decision, adopt = raw["decision"], raw.get("adopt") or ""
    if decision == "adopt" and (not isinstance(adopt, str) or adopt not in labels):
        raise ValueError("The final arbitrator adopted an assessment that does not exist.")
    known = {flag["id"] for flag in shown}
    rulings = {}
    for entry in raw.get("flag_rulings") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("flag"), str) or entry["flag"] not in known or type(entry.get("resolved")) is not bool:
            raise ValueError("A flag ruling names an unknown flag or does not say whether it is resolved.")
        rulings[entry["flag"]] = {"resolved": entry["resolved"], "note": str(entry.get("note") or "")[:2000]}
    value = {"decision": decision, "adopt": adopt if decision == "adopt" else "", "reason": str(raw.get("reason") or "")[:4000], "rulings": rulings,
             "confidence": raw.get("confidence") if isinstance(raw.get("confidence"), (int, float)) and not isinstance(raw.get("confidence"), bool) else None}
    if decision == "revise":
        value["assessment"] = graded({**{k: raw.get(k) for k in ("interpretation", "applications", "confidence", "rubric_ambiguities", "review_reasons", "reading_issues")},
            **({"full_credit_eligible": raw["full_credit_eligible"]} if "full_credit_eligible" in raw else {})})
    return value


def outcome(ruling, shown):
    """adopted | revised | unreliable | unsettled | failed. A grade stands only when EVERY shown flag was ruled resolved."""
    if not ruling or ruling.get("failed"):
        return "failed"
    if ruling["decision"] == "unreliable":
        return "unreliable"
    if ruling.get("review_required"):
        return "unsettled"
    if any(not (ruling["rulings"].get(flag["id"]) or {}).get("resolved") for flag in shown):
        return "unsettled"  # It named a grade and left a flag standing (or unanswered): that is not a reliable grade.
    return "adopted" if ruling["decision"] == "adopt" else "revised"


def run(service, task, job, settings, existing, content, rubric, reading, verified, crops, images, ask, payload, panels, decision, proposal, final_opinions, tier):
    """One retained component per turn, like the team's own steps. Returns ("step", tuple) to retain, or ("final", proposal, opinions)."""
    labels = candidates(existing)
    standing = label_of(existing, proposal, labels)
    if CHECK not in existing:
        with service.rosters.lock(), connect(service.path) as db:
            question = {"question_id": db.execute("SELECT question_id FROM frq_responses WHERE id=?", (task["response_id"],)).fetchone()[0]}
        def scored(opinion, opinions):
            return service.scored(task, job, settings, question, content, rubric, reading, verified, crops, {**copy.deepcopy(opinion), "opinions": opinions}, scope=False)[0]
        now = scored(proposal, final_opinions)
        found = {text: [standing] for text in flags(now)} if waits(now) else {}
        answers = {standing: answer_facts(now)} if standing else {}
        if found:
            # Each other assessment's own deterministic flags too, so that adopting it is a ruling on flags that were SHOWN.
            for label, key in labels.items():
                if label != standing:
                    other = scored(existing[key], {**final_opinions, "second": None})
                    answers[label] = answer_facts(other)
                    for text in flags(other):
                        found.setdefault(text, []).append(label)
        shown = [{"id": f"F{n}", "text": text, "about": about} for n, (text, about) in enumerate(found.items(), 1)]
        chosen = arbitrator(settings, tier)
        if shown and not chosen:
            # v2 with no additional reviewer configured for this round: nothing can arbitrate, and the response simply waits.
            shown, why = [], f"No additional reviewer is configured for {tier}, so there was nobody to arbitrate."
            return "step", (CHECK, {"needed": False, "not_asked": why, "standing": standing, "state": now["state"], "flags": [], "assessments": list(labels)}, {"rule": RULE}, False)
        return "step", (CHECK, {"needed": bool(shown), "standing": standing, "state": now["state"], "flags": shown, "assessments": list(labels),
                                "answers": answers, **({"arbitrator": chosen["name"]} if chosen else {})}, {"rule": RULE}, False)
    check = existing[CHECK]
    if not check["needed"]:
        return "final", proposal, final_opinions if not check.get("not_asked") else {**final_opinions, "arbiter": {"rule": RULE, "outcome": "not_asked", "reason": check["not_asked"], "flags": []}}
    endpoint = arbitrator(settings, tier)
    if STEP not in existing:
        pictures = images if endpoint["modality"] == "vision" else []
        def graded(output):
            value = recognition.validate_grade(output, verified["transcript"], rubric, content["max_points"], max(1, len(pictures)))
            if {a["rubric_item_id"] for a in value["applications"]} != {item["id"] for item in rubric["items"]}:
                raise ValueError("A final set of ticks must decide every rubric item.")
            if settings.get("full_credit_rule") in rubric_quality.FULL_CREDIT_RULES:
                value = rubric_quality.full_credit_alone(value, content.get("rubric_guidance"), rubric, content["max_points"], settings["full_credit_rule"])
            return value
        def validate_ruling(output):
            value = validate(output, labels, check["flags"], graded)
            if value["decision"] == "revise" and payload.get("deductions"):
                # The arbitrator is not asked about the approved deductions the graders decide: its own grade leaves them to the flag
                # review or the instructor (GRADING "GRADERS TAKE OFF APPROVED DEDUCTIONS").
                from .grader_deductions import UNDECIDED
                value["assessment"]["review_reasons"] = list(dict.fromkeys([*value["assessment"]["review_reasons"], UNDECIDED]))
            from . import standard
            if standard.enabled(settings):
                # An explicit doubt from this arbitrator always belongs to the
                # instructor, even if it also marked the same earlier flag resolved.
                notes = [output.get(name, []) for name in ("review_reasons", "rubric_ambiguities", "reading_issues")]
                if any(not isinstance(items, list) for items in notes) or any(not isinstance(note, str) for items in notes[:2] for note in items) \
                        or any(not isinstance(issue, dict) or not isinstance(issue.get("description"), str) for issue in notes[2]):
                    raise ValueError("The arbitrator's review concerns must follow the grading contract.")
                concerns = [*notes[0], *notes[1], *(issue["description"] for issue in notes[2])]
                if concerns:
                    value["review_required"] = concerns
            return value
        from . import standard
        return "step", ask(STEP, "arbiter", endpoint, PROMPT, material(payload, existing, panels, check["standing"], check["flags"], content, check.get("answers"),
                                                                        blind=standard.model_blind(settings)), pictures,
                           validate_ruling, True, call_tier=tier)
    ruling = existing[STEP]
    result = outcome(ruling, check["flags"])
    from . import standard
    guarded = standard.enabled(settings)
    # Cloud Standard frozen before `standard.FAMILY_RULE`: an arbitrator of the adopted assessment's own family was that family
    # voting for itself, one vote. A family-blind batch lets it adopt any assessment (requirement, 2026-09-23).
    family = (guarded and not standard.family_blind(settings) and result == "adopted" and ruling["adopt"] in labels
              and not _independent(ruling, existing[labels[ruling["adopt"]]]))
    if family:
        result = "unsettled"
    record = {"rule": RULE, "outcome": result, "decision": ruling.get("decision"), "adopted": ruling.get("adopt", ""), "reason": ruling.get("reason") or ruling.get("error", ""),
              "flags": [{**flag, **(ruling.get("rulings", {}).get(flag["id"]) or {"resolved": False, "note": ""})} for flag in check["flags"]],
              "standing_before": check["standing"], "endpoint": ruling.get("endpoint"), "model": ruling.get("model"), "lineage": ruling.get("lineage"),
              **({"cloud_standard_rule": standard.RULE} if guarded else {})}
    if family:
        record["same_family"] = True
        record["reason"] = "; ".join([f"It adopted {ruling['adopt']}, an assessment of its own model family ({ruling.get('lineage') or 'unrecorded'}), "
                                      "and a family voting for itself is one vote", record["reason"]])
    if ruling.get("review_required"):
        record["review_required"] = ruling["review_required"]
        record["reason"] = "; ".join([record["reason"], *ruling["review_required"]])
    if result not in ("adopted", "revised"):
        return "final", proposal, {**final_opinions, "arbiter": record}
    final = existing[labels[ruling["adopt"]]] if result == "adopted" else {**ruling["assessment"], "endpoint": ruling["endpoint"], "model": ruling["model"], "lineage": ruling["lineage"],
                                                                           "input_mode": "images_and_transcript" if endpoint["modality"] == "vision" else "transcript_only", "saw_other_decisions": True}
    # `signals.committee` records resolution and provenance; every team's own decision stays, as it ran, in `panels`.
    committee = {**decision, "status": "resolved", "team_status": decision["status"], "resolved_by": "final_arbitrator", "source": ruling["adopt"] or "arbiter", "supporter": "arbiter",
                 "reason": ("The final arbitrator adopted the " + ruling["adopt"] + " assessment as the accurate final grade." if result == "adopted"
                            else "The final arbitrator found no earlier assessment accurate and posed the final rubric selections.")}
    roles = {**final_opinions.get("roles", {}), "primary": {"endpoint": final.get("endpoint"), "model": final.get("model"), "lineage": final.get("lineage")},
             "arbiter": {"endpoint": ruling["endpoint"], "model": ruling["model"], "lineage": ruling["lineage"]}}
    roles.pop("second", None)
    return "final", final, {**final_opinions, "second": None, "roles": roles, "committee": committee, "arbiter": record}


def assessed(existing):
    """{label: step key} of every earlier assessment of the team that did not fail, scorable or not.

    A grader's spare (`<step>:fallback`, asked only once its grader failed) is the assessment the team actually compared or stood
    on, so it is here under its own label (`standard.primary_fallback`) right after the failed original it replaced."""
    found = {}
    for role, name in (("primary", "primary"), ("second", "second"), ("verifier", "reviewer")):
        for key, label in (("grade:" + role, f"standard.{name}"), ("grade:" + role + ":fallback", f"standard.{name}_fallback")):
            opinion = existing.get(key)
            if opinion and not opinion.get("failed") and opinion.get("applications"):
                found[label] = key
    return found


def apply(output, record, proposal=None):
    """The ruling over the FINAL ticks as scored again: only flags that were shown AND resolved are settled. `proposal`: the scored
    grade's own requests and doubts, under `SETTLED_RULE` (`renamed_notes`), else None."""
    output = copy.deepcopy(output)
    signals = output["signals"]
    signals["arbiter"] = record
    if record["outcome"] == "not_asked":
        return output  # Nobody arbitrated: the response stays exactly as the team and the checks left it.
    if record["outcome"] not in ("adopted", "revised"):
        said = ("The final arbitrator's ruling cannot finalize a grade: " if record.get("same_family") else
                {"unreliable": "The final arbitrator found that no reliable grade can be determined: ", "unsettled": "The final arbitrator named a grade but left a flag standing: ",
                 "failed": "The final arbitrator could not complete: "}[record["outcome"]]) + (record.get("reason") or "no reason was given.")
        output["reasons"] = list(dict.fromkeys([*output["reasons"], said]))
        signals["risk"] = {**signals["risk"], "individual": True, "blocks": list(dict.fromkeys([*signals["risk"].get("blocks", []), said]))}
        if output["state"] == "proposed":
            output["state"] = "grading_review"
        return output
    # Shown, perhaps even marked resolved, but only the instructor settles these: under Cloud Standard `risk.instructor_only`, and
    # in every batch a blank crop, which direct-reading-v2 keeps out of automatic finalization whatever ran after the reading.
    from .risk import blank, instructor_only
    theirs = instructor_only if _guarded(record) else blank
    reserved = lambda texts: [text for text in texts if theirs(text)]
    resolved = {_same(flag["text"]) for flag in record["flags"] if flag.get("resolved") and not theirs(_same(flag["text"]))}
    standing = [text for text in flags({**output, "signals": {**signals, "rubric_quality": {}}}) if text not in resolved]
    standing = renamed_notes(standing, resolved, proposal, theirs)
    if standing or output.get("score") is None:
        record = {**record, "outcome": "unsettled", "open_flags": standing, **({"instructor_only": reserved(standing)} if reserved(standing) else {})}
        signals["arbiter"] = record
        said = UNSETTLED + "; ".join(standing[:3])
        output["reasons"] = list(dict.fromkeys([*output["reasons"], said]))
        signals["risk"] = {**signals["risk"], "individual": True, "blocks": list(dict.fromkeys([*signals["risk"].get("blocks", []), said]))}
        if signals.get("committee"):
            signals["committee"] = {**signals["committee"], "status": "unresolved", "reason": said}
        if output["state"] == "proposed":
            output["state"] = "grading_review"
        return output
    # Settled: what was flagged stays on the record with each ruling, and the grade goes to the ordinary acceptance gate.
    signals["risk"] = {**signals["risk"], "arbitrated": {"blocks": signals["risk"].get("blocks", []), "reasons": output["reasons"], "individual": bool(signals["risk"].get("individual")), "state": output["state"]},
                       "blocks": [], "individual": False}
    output["reasons"], output["state"] = [], "proposed"
    return output


def basis(signals, recorded="team_consensus"):
    """How an automatic finalization came about, for the review card and exports."""
    if recorded != "team_consensus":
        return recorded
    if ((signals or {}).get("jev_blank") or {}).get("outcome") == "blank":
        return "jev_blank"  # A zero for an answer the reader and Jev both saw left blank (grading/jev_blank.py).
    if ((signals or {}).get("choice") or {}).get("rule"):
        return "choice_reader"  # Read by the multiple-choice reader (grading/choice.py); no grading team was convened.
    if ((signals or {}).get("flag_resolution") or {}).get("outcome") in ("adopted", "revised"):
        return "flag_resolution"  # Settled by a flag review of the question (grading/flag_resolution.py).
    if ((signals or {}).get("arbiter") or {}).get("outcome") in ("adopted", "revised"):
        return "final_arbitrator"
    if ((signals or {}).get("flag_review") or {}).get("outcome") == "finalized":
        return "flag_review"
    # The Jev tie-breaker was removed on 2026-09-23; a grade it finalized before then is still named for it.
    return "jev_tiebreak" if ((signals or {}).get("jev") or {}).get("outcome") == "resolved" else recorded
