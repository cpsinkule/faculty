"""Cloud Standard's disagreement arbitrator and standing-grade flag review.

Workspace model preferences freeze on the existing term-owned batch. All checks,
requests and rulings are receipts of its response, inside the grading stage.
Historical batches without this rule retain their original workflow.

The flag reviewer only ever upholds a grade two complete assessments agreed on (third-vote-v3), and never over a flag only
the instructor settles (`risk.instructor_only`). Otherwise it is not asked, and the check step and the review card say why
(`barred`). A batch frozen before `FAMILY_RULE` also required the two graders to be of different model families and kept a
reviewer from upholding or adopting its own family's grade; it finishes that way.
"""
import copy

from rosters import connect
from . import arbitration, config, grader_deductions, risk

RULE = "cloud-standard-v2"
CHECK, STEP = "grade:flag-review:check", "grade:flag-review"
PROMPT = "flag_review"
# The primary grader of a batch frozen with this rule is sent the crops under a contract written for it (`primary_vision`): check the
# transcript against the image and report reading issues, as the second grader does. Measured on the trial's batch 1c1ce6cb (240
# responses, 2026-09-22): the primary, asked under `grade` (a contract written for a text-first local grader), raised no reading
# issue and no rubric doubt of its own on any response, in that batch or the two before it, while the second grader reported
# misreadings the arbitrator then sent to the instructor. A batch frozen without the key keeps `grade`, and a text-only primary
# always does; the contract travels with the batch so it finishes the way it was submitted.
PRIMARY_RULE = "primary-vision-v1"
PRIMARY_PROMPT = "primary_vision"
# MODEL FAMILIES DECIDE NOTHING (requirement, 2026-09-23). Before this, two graders of one family agreeing were one vote (never
# "resolved"), a same-family second grader was a risk block, and neither the arbitrator nor the flag reviewer could finalize a grade
# its own family produced. On the trial batch 5b5e4d4e that last bar alone held 30 of 240 responses back, and the instructor asked
# for every family rule to go. A batch frozen with this key ignores families everywhere; lineages are still recorded on every
# assessment, receipt and review card. A batch frozen without it finishes under the family rules it ran with.
FAMILY_RULE = "family-blind-v1"
# THE REVIEWERS DO NOT SEE WHICH MODEL GRADED (requirement, 2026-09-24). Before this, the arbitrator and the flag reviewer read every
# earlier assessment with its connection, model ID and family beside it. On the trial's batch 58b01405 the arbitrator adopted the
# vision primary's grade in 71% of its rulings and the text-only second's in 8%, and an earlier arbitrator leaned the same way toward
# the other vision grader; the records cannot tell a model's name from what its grader could see as the cause, and only the second
# is evidence. A batch frozen with this key sends each assessment by its label with `input_mode` (whether that grader saw the crops),
# never who made it (`arbitration.IDENTITY`); the model is still recorded on every assessment, receipt and review card. A batch
# frozen without the key finishes as it ran.
IDENTITY_RULE = "model-blind-v1"


def enabled(settings):
    return settings.get("cloud_standard_rule") == RULE


def family_blind(settings):
    """Whether this batch froze `FAMILY_RULE`: no check anywhere asks which model family produced an assessment."""
    return settings.get("family_rule") == FAMILY_RULE


def model_blind(settings):
    """Whether this batch froze `IDENTITY_RULE`: a model ruling on earlier assessments is never told which models made them."""
    return settings.get("identity_rule") == IDENTITY_RULE


def freeze(settings):
    settings.update(cloud_standard_rule=RULE, primary_rule=PRIMARY_RULE, family_rule=FAMILY_RULE, identity_rule=IDENTITY_RULE, final_arbitrator=True,
                    final_arbitrator_rule=arbitration.RULE, settled_notes_rule=arbitration.SETTLED_RULE,
                    grader_deductions_rule=grader_deductions.RULE, jev_tiebreak=False,
                    escalation_enabled=False)


def primary_contract(settings, pictures):
    """The prompt the primary grader is asked under: its own vision contract when the batch froze it and it is sent the crops."""
    return PRIMARY_PROMPT if pictures and settings.get("primary_rule") == PRIMARY_RULE else "grade"


def require_models(settings):
    roles = config.roles(settings, "cloud_standard")
    missing = [label for role, label in (("reader", "handwriting reader"), ("primary", "primary grader"),
               ("second", "second grader"), ("verifier", "additional reviewer"), ("flag_reviewer", "flag reviewer"))
               if not roles.get(role)]
    if missing:
        raise ValueError("Choose the Cloud " + ", ".join(missing) + " in Compute settings before starting a batch.")


def disagreement(decision):
    pair = decision.get("pair")
    return bool(pair and not pair["agrees"])


def barred(decision, standing, proposal, shown, reviewer, families=True):
    """Why no flag review may finalize this standing grade, or "". The response then stays exactly as the team left it.

    third-vote-v3: a team that never compared two complete assessments of different families (one missing, failed or
    unscorable, or an agreement inside one family) is the instructor's; no model upholds what one family alone produced. So is a
    standing grade no retained assessment is, one the reviewer's own family produced (checked here, at run time, so saved
    settings always load), and one carrying a flag only the instructor settles (`risk.instructor_only`), which no ruling clears.
    With `families` False (`family_blind`) the reviewer's own family is no bar, and the team's agreement never depended on one."""
    pair = decision.get("pair")
    if decision.get("status") != "resolved":
        # An unresolved AGREEMENT exists only in a batch frozen before `FAMILY_RULE`: two graders of one family.
        return (("The two graders agree but share a model family, so their agreement is one vote" if pair and pair.get("agrees")
                 else "The grading team never compared two complete assessments or has not resolved their disagreement")
                + "; no model may finalize this grade, so the instructor takes it.")
    if not standing:
        return "The standing grade is none of the retained assessments, so no model may uphold it; the instructor takes it."
    if reviewer is not None and families:
        family = str(reviewer.get("lineage") or "").casefold()
        if not family or family == str(proposal.get("lineage") or "").casefold():
            return (f"The flag reviewer is of the model family that produced the standing grade ({reviewer.get('lineage') or 'unrecorded'}), "
                    "and a family upholding its own grade is one vote; the instructor takes it.")
    reserved = [flag["text"] for flag in shown if risk.instructor_only(flag["text"])]
    return "Only the instructor can settle: " + "; ".join(reserved[:3]) if reserved else ""


def validate(raw, shown):
    """The fourth model may uphold the standing grade, never select or create another."""
    if not isinstance(raw, dict) or raw.get("decision") not in ("finalize", "manual_review"):
        raise ValueError("The flag reviewer must decide finalize or manual_review.")
    if set(raw) - {"decision", "reason", "flag_rulings"}:
        raise ValueError("The flag reviewer cannot change the standing grade.")
    if not isinstance(raw.get("reason"), str) or not raw["reason"].strip():
        raise ValueError("The flag reviewer must explain its decision.")
    known = {flag["id"] for flag in shown}
    rulings = {}
    if not isinstance(raw.get("flag_rulings"), list):
        raise ValueError("The flag reviewer must address the review flags.")
    for entry in raw["flag_rulings"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("flag"), str) or entry["flag"] not in known or entry["flag"] in rulings
                or type(entry.get("resolved")) is not bool or not isinstance(entry.get("note"), str) or not entry["note"].strip()):
            raise ValueError("Each flag ruling must name one shown flag and explain whether it still requires review.")
        rulings[entry["flag"]] = {"resolved": entry["resolved"], "note": entry["note"][:2000]}
    return {"decision": raw["decision"], "reason": raw["reason"][:4000], "rulings": rulings}


def run(service, task, job, settings, existing, content, rubric, reading, verified, crops, images, ask,
        payload, panels, decision, proposal, final_opinions, tier):
    if disagreement(decision):
        # This branch always terminates here, including failed/unreliable rulings
        # and new flags found when the arbitrator's selections are scored again.
        return arbitration.run(service, task, job, settings, existing, content, rubric, reading, verified,
                               crops, images, ask, payload, panels, decision, proposal, final_opinions, tier)
    endpoint = config.roles(settings, tier).get("flag_reviewer")
    if CHECK not in existing:
        with service.rosters.lock(), connect(service.path) as db:
            question = {"question_id": db.execute("SELECT question_id FROM frq_responses WHERE id=?", (task["response_id"],)).fetchone()[0]}
        output = service.scored(task, job, settings, question, content, rubric, reading, verified, crops,
                                {**copy.deepcopy(proposal), "opinions": final_opinions}, scope=False)[0]
        # A spare that stood in for its grader has its own label, so the standing grade always has one (`arbitration.assessed`).
        standing = arbitration.label_of(existing, proposal, arbitration.candidates(existing))
        shown = [{"id": f"F{n}", "text": text, "about": [standing]}
                 for n, text in enumerate(arbitration.flags(output), 1)] if arbitration.waits(output) else []
        why = barred(decision, standing, proposal, shown, endpoint, not family_blind(settings)) if shown else ""
        # Why nothing was sent is on the record; the flags stay on the check step and on the review card. What Faculty compared with
        # the answer key is kept beside them, so a flag about the key is ruled on with the key and its comparison in view.
        return "step", (CHECK, {"needed": bool(shown) and not why, **({"not_asked": why} if why else {}), "standing": standing, "flags": shown,
                                **({"answers": {standing: arbitration.answer_facts(output)}} if standing else {})},
                        {"rule": RULE}, False)
    check = existing[CHECK]
    # A check retained before these bars existed meets them here, before anything is sent.
    why = check.get("not_asked") or (barred(decision, check["standing"], proposal, check["flags"], endpoint, not family_blind(settings))
                                     if check["needed"] and STEP not in existing else "")
    if why:
        return "final", proposal, {**final_opinions, "flag_review": {"rule": RULE, "outcome": "not_asked", "reason": why, "standing": check["standing"], "flags": []}}
    if not check["needed"]:
        return "final", proposal, final_opinions
    if STEP not in existing and endpoint:
        material = arbitration.material(payload, existing, panels, check["standing"], check["flags"], content, check.get("answers"),
                                        blind=model_blind(settings))
        return "step", ask(STEP, "flag_reviewer", endpoint, PROMPT, material,
                           images if endpoint["modality"] == "vision" else [],
                           lambda raw: validate(raw, check["flags"]), True, call_tier=tier)
    ruling = existing.get(STEP) or {"failed": True, "error": "No flag reviewer is configured."}
    outcome = ("failed" if ruling.get("failed") else "manual_review" if ruling["decision"] == "manual_review"
               else "finalized" if all(ruling["rulings"].get(f["id"], {}).get("resolved") for f in check["flags"]) else "unsettled")
    # The same bars hold over a ruling asked before they existed, with the family of the model that actually answered.
    why = (barred(decision, check["standing"], proposal, check["flags"], {"lineage": ruling.get("lineage")}, not family_blind(settings))
           if outcome == "finalized" else "")
    if why:
        outcome = "unsettled"
    record = {"rule": RULE, "outcome": outcome, "reason": "; ".join(filter(None, [why, ruling.get("reason") or ruling.get("error", "")])),
              "standing": check["standing"], "endpoint": ruling.get("endpoint"), "model": ruling.get("model"), "lineage": ruling.get("lineage"),
              "flags": [{**flag, **ruling.get("rulings", {}).get(flag["id"], {"resolved": False, "note": ""})} for flag in check["flags"]]}
    # Keep the original team decision for scoring: changing it here could erase
    # a flag before the fourth model's exact ruling is reapplied.
    return "final", proposal, {**final_opinions, "flag_review": record}


def _renamed(text):
    return str(text).replace("The final arbitrator's grade raised", "The standing grade raised").replace("final arbitrator", "flag reviewer")


# `arbitration.UNSETTLED` as this module says it of a standing grade the flag reviewer upheld (2026-10-11, for the review card's causes).
UNSETTLED = _renamed(arbitration.UNSETTLED)


def apply(output, record):
    """Revalidate every standing flag; never let a ruling clear unseen or structural problems."""
    if record["outcome"] == "not_asked":
        # Nobody reviewed the flags (`barred`): the response stays exactly as the team and the checks left it, and says why.
        result = copy.deepcopy(output)
        result["signals"]["flag_review"] = record
        return result
    if record["outcome"] != "finalized":
        result = copy.deepcopy(output)
        signals = result["signals"]
        signals["flag_review"] = record
        said = {"manual_review": "The flag reviewer found that the flags require manual review: ",
                "failed": "The flag reviewer could not complete: ",
                "unsettled": "The flag reviewer left a flag unresolved: "}[record["outcome"]] + record["reason"]
        result["reasons"] = list(dict.fromkeys([*result["reasons"], said]))
        signals["risk"] = {**signals["risk"], "individual": True,
                           "blocks": list(dict.fromkeys([*signals["risk"].get("blocks", []), said]))}
        if result["state"] == "proposed":
            result["state"] = "grading_review"
        return result
    mapped = {**record, "outcome": "adopted"}
    result = arbitration.apply(output, mapped)
    applied = result["signals"].pop("arbiter")
    outcome = "finalized" if applied["outcome"] == "adopted" else applied["outcome"]
    result["signals"]["flag_review"] = {**applied, "outcome": outcome}
    result["reasons"] = [_renamed(r) for r in result["reasons"]]
    verdict = result["signals"]["risk"]
    verdict["blocks"] = [_renamed(r) for r in verdict.get("blocks", [])]
    if (result["signals"].get("committee") or {}).get("reason"):
        result["signals"]["committee"] = {**result["signals"]["committee"], "reason": _renamed(result["signals"]["committee"]["reason"])}
    if outcome == "finalized":
        if "arbitrated" in verdict:
            verdict["flag_reviewed"] = verdict.pop("arbitrated")
        decision = result["signals"]["committee"]
        result["signals"]["committee"] = {**decision, "status": "resolved", "team_status": decision["status"],
            "resolved_by": "flag_reviewer", "reason": "The flag reviewer found sufficient evidence to finalize the unchanged standing grade."}
    return result


def scope(settings):
    from .providers.local_vlm import prompt
    return {"rule": RULE, "arbitrator_trigger": "grader_disagreement", "arbiter_flags": "manual_review",
            "arbitrator_prompt": prompt(arbitration.PROMPT)[1]["prompt_sha256"],
            "flag_review_prompt": prompt(PROMPT)[1]["prompt_sha256"],
            # Named only for a batch frozen with it, so the scope of a batch without it is the scope it always had.
            **({"primary_rule": PRIMARY_RULE, "primary_prompt": prompt(PRIMARY_PROMPT)[1]["prompt_sha256"]}
               if settings.get("primary_rule") == PRIMARY_RULE else {}),
            **({"family_rule": FAMILY_RULE} if family_blind(settings) else {}),
            **({"identity_rule": IDENTITY_RULE} if model_blind(settings) else {}),
            **({"settled_notes_rule": arbitration.SETTLED_RULE} if arbitration.settled_notes(settings) else {}),
            **({"grader_deductions_rule": grader_deductions.RULE} if grader_deductions.enabled(settings) else {})}
