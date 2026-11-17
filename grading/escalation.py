"""Shared grading evidence and bounded cloud calls.

New jobs dispatch to committee.py (the Cloud tier's team). The old component sequence below remains for jobs whose frozen
profiles predate grading-teams-v1; retained opinions are never rewritten. High Assurance, which it could escalate to, was
removed on 2026-09-23, so it never escalates.
"""
from course_storage import timestamp
from rosters import connect

from . import answers, config, recognition, risk, symbolic
from .cloud import RetryLater
from .models import encoded, line_texts, struck_lines, uid
from .reading_context import for_graders
from .providers.base import ProviderError



class EscalationMixin:
    def evidence(self, task, settings, content, rubric, reading, verified, crops, graded, second=None, verifier=None, roles=None, escalation=None, committee=None):
        """Deterministic facts about a proposal, and what the risk policy makes of them. Used by the interim check and the final scoring alike."""
        key, rubric_roles = content.get("answer_key", []), answers.rubric_roles(rubric)
        from . import direct_read
        # Only INDEPENDENT readings answer "do two readers agree": the reader's own free-text pass is the same model on the
        # same image, so with direct reading there is nothing to compare and `readers_agree` stays None.
        found = answers.facts(graded["interpretation"].get("final_answers", []), verified["transcript"], key,
                              direct_read.independent(reading["candidates"]), content["problem"],
                              symbolic_forms=settings["symbolic_enabled"])
        if (graded.get("one_shot") or {}).get("workflow") == config.ONE_SHOT_WORKFLOW:
            found["readers_agree"] = None  # A One-shot result (removed 2026-09-23): no independent reading was requested.
        found.update(key=bool(key), work_required=rubric_roles["work_required"],
                     full_credit=bool(graded["score"]) and graded["score"]["score"] >= content["max_points"])
        applied = {a["rubric_item_id"] for a in graded["applications"] if a["applied"]}
        contradictions = answers.reasons(found, rubric_roles, applied, bool(key), key)
        checks = symbolic.check_lines(line_texts(verified["transcript"], struck_lines(verified["transcript"]))) if settings["symbolic_enabled"] else []
        from . import arbitration, standard
        # Cloud Standard: a ruling's own refused-then-re-asked answer is a receipt of that ruling, never a new flag it was not shown
        # (counted, it made every such ruling `unsettled`). The rejections of the steps before its check still count.
        rulings = (arbitration.STEP, standard.STEP) if standard.enabled(settings) else ()
        with connect(self.path) as db:
            rejected = db.execute("SELECT COUNT(*) FROM frq_rejected_outputs WHERE task_id=?" + (" AND stage NOT IN (?,?)" if rulings else ""),
                                  (task["id"], *rulings)).fetchone()[0]
        parts = risk.components(reading, verified, graded, crops, found, symbolic.summary(checks) if settings["symbolic_enabled"] else None,
                                second if second and not second.get("failed") else None, verifier if verifier and not verifier.get("failed") else None,
                                roles=roles, rubric=rubric, rejected=rejected, escalation=escalation, settings=settings, committee=committee)
        verdict = risk.assess(parts, settings["risk"], contradictions)
        for role, opinion in (("second grader", second), ("adversarial reviewer", verifier)):
            if opinion and opinion.get("failed"):
                # A failed check is absent evidence. It is said plainly and the response is looked at; it never counts as agreement.
                verdict["reasons"].append(f"The {role} could not complete ({opinion['error']}); review this response yourself.")
                verdict["blocks"].append(f"The {role} failed.")
        return found, applied, contradictions, checks, parts, verdict

    def cloud_grading_unit(self, task, job, settings, owner, roles, existing, content, rubric, reading, verified, crops, images, question=None):
        tier = self.compute(job)

        def ask(key, role, endpoint, prompt, payload, pictures, validate, optional, call_tier=None):
            try:
                result = self.cloud_call({**owner, "step": key, "tier": call_tier or owner.get("tier", "")}, role, endpoint, {"prompt": prompt, "payload": payload, "images": pictures}, settings)
                try:
                    value = validate(result["output"])
                except (ValueError, TypeError, AttributeError, KeyError):
                    raise ProviderError("A rubric opinion failed its output contract. Nothing from it was accepted.", raw=result["raw"], metadata=result["metadata"]) from None
                return key, {**value, "provider_response": result["raw"], "endpoint": endpoint["name"], "model": endpoint["model"], "lineage": endpoint["lineage"]}, result["metadata"], False
            except ProviderError as error:
                if not optional:
                    raise  # The primary grader has no substitute; a malformed answer is asked again by the lane's own bounded rule.
                if error.raw is not None:
                    # An independent opinion that breaks its contract is asked again a bounded number of times, with each receipt kept.
                    with self.rosters.lock(), connect(self.path) as db:
                        db.execute("INSERT INTO frq_rejected_outputs VALUES(?,?,?,?,?,?,?)", (uid(), task["id"], key, task["attempt"], encoded(error.raw), encoded(error.metadata), timestamp()))
                        seen = db.execute("SELECT COUNT(*) FROM frq_rejected_outputs WHERE task_id=? AND stage=? AND attempt=?", (task["id"], key, task["attempt"])).fetchone()[0]
                    if seen < min(3, settings["max_retries"]):
                        raise RetryLater(1, "An independent opinion broke its output contract; it is asked again.") from None
                # A grader cut off at the gateway's time limit is marked so, with the attempt it belonged to: the team then fails the
                # response for the instructor to retry, and a retry asks that grader again (`committee.cut_off`).
                cut = {"stream_duration": True, "task_attempt": owner.get("task_attempt", 0)} if error.confirmation.get("stream_duration") else {}
                return key, {"failed": True, "error": str(error), "endpoint": endpoint["name"], "model": endpoint["model"], "lineage": endpoint["lineage"], **cut}, {"failed_role": role}, False

        from . import committee
        if committee.enabled(settings) and tier in config.TIERS:
            return committee.run(self, task, job, settings, owner, existing, content, rubric, reading, verified, crops, images, ask)
        # Frozen jobs queued before grading-teams-v1 retain their original workflow.
        payload = self.grade_payload(task["response_id"], content, rubric, verified["transcript"], for_graders(reading, verified, crops, settings))
        graded = lambda pictures: lambda output: recognition.validate_grade(output, verified["transcript"], rubric, content["max_points"], max(1, len(pictures)))
        def second_opinion(key, endpoint):
            pictures = images if endpoint["modality"] == "vision" else []
            prompt = "second_vision" if pictures else "transcript_grade"
            step, value, metadata, advance = ask(key, "second", endpoint, prompt, payload, pictures, graded(pictures), True)
            value["input_mode"] = "images_and_transcript" if pictures else "transcript_only"
            return step, value, metadata, advance
        if "grade:primary" not in existing:
            return ask("grade:primary", "primary", roles["primary"], "grade", payload, images, graded(images), False)
        if roles.get("second") and "grade:second" not in existing:
            return second_opinion("grade:second", roles["second"])
        if "grade:check" not in existing:
            found, _, contradictions, checks, parts, verdict = self.evidence(task, settings, content, rubric, reading, verified, crops, existing["grade:primary"],
                                                                             existing.get("grade:second"), None, roles)
            return "grade:check", {"escalation": {"escalated": False, "depth": 0, "from": tier, "to": tier,
                "reasons": [], "considered": verdict["escalate"], "maximum_depth": 0,
                **({"rubric_disagreement": parts["second"]} if parts["second"] and not parts["second"]["agrees"] else {})},
                "level": verdict["level"], "symbolic": symbolic.summary(checks) if checks else None}, {"policy": risk.POLICY, "checker": symbolic.CHECKER}, False
        escalation = existing["grade:check"]["escalation"]
        proposal = existing["grade:primary"]
        second = existing.get("grade:second")
        opinions = {"second": second, "verifier": existing.get("grade:verifier"), "escalation": escalation, "standard_primary": None,
                    "roles": {role: {"endpoint": e["name"], "model": e["model"], "lineage": e["lineage"]} for role, e in roles.items() if e}}
        strip = lambda value: {k: v for k, v in value.items() if k != "provider_response"} if value else value
        opinions = {key: strip(value) if key in ("second", "verifier", "standard_primary") else value for key, value in opinions.items()}
        return "grading", {**proposal, "opinions": opinions}, {"grading": "cloud-opinions-v1", "tier": tier, "escalated": escalation["escalated"]}, True
