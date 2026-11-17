"""Retained automatic decisions, trial measurements, and instructor corrections.

New cloud teams finalize resolved assessments when every check passes, under the
instructor's 2026-09-20 policy. Separate term-response receipts identify that basis;
no benchmark is fabricated. Local and legacy jobs retain trial/benchmark gates.
All acceptances remain distinct from instructor reviews and never override them,
and automatically accepted work is never an evaluation/training label.
"""
import json
import math

from werkzeug.exceptions import Conflict, NotFound
from course_storage import timestamp
from rosters import connect

from . import direct_read
from .models import digest, encoded, number, text, uid
from .providers.local_vlm import prompt

PROMPTS = ("reconcile", "grade", "transcript_grade", "second_vision", "adversarial", "adjudicate", "third_grade")
# Risk keys whose DEFAULT value is left out of the scope, so a batch that did not change them keeps the exact scope, and the
# permissions resting on it, that it always had. A changed value is named and is therefore another scope.
RISK_QUIET = {"confidence_checks": True, "review_policy": "all_checks"}
# A REGRADE THE INSTRUCTOR STARTED REPLACES THEIR EARLIER DECISION (requirement, 2026-09-27). In a batch frozen with this rule that reads and grades (never one that only
# recalculates points from the instructor's own selections), a new grade that every check passes is finalized automatically even
# where the instructor had reviewed the response, stale or current, draft included: the review stays in `frq_reviews` and the history,
# `frq_responses.current_review_id` stops pointing at it, and a `review_superseded_by_regrade` event records which review gave way to
# which result. A regrade that is flagged waits for the instructor as before, beside any decision that still counts. It applies to Cloud
# grading teams, whose grades finalize by agreement; a benchmark permission (local and legacy workflows) is earned from the instructor's
# blind reviews, and a trial is a test, so neither is ever accepted over a decision. Measured on
# Quiz 2's regrade after its rubrics were replaced (2026-09-27): 40 responses passed every check and waited only because they had been
# reviewed once (36 under the replaced rubrics). A batch frozen without the rule finishes as it ran.
REVIEW_RULE = "regrade-supersedes-v1"


def upper_bound(errors, n, confidence=.95):
    """Exact (Clopper-Pearson) upper confidence bound of an error rate: the largest rate still consistent with `errors` in `n`."""
    if n <= 0:
        return 1.0
    if errors >= n:
        return 1.0

    def at_most(rate):  # P(X <= errors) for X ~ Binomial(n, rate)
        term = total = (1 - rate) ** n
        if term < 1e-250:
            # Start at the observed count when P(X=0) underflows. Every bisection rate is at least errors/n,
            # so walking downward only decreases the terms; a large sample cannot erase its confidence margin.
            term = math.exp(math.lgamma(n + 1) - math.lgamma(errors + 1) - math.lgamma(n - errors + 1)
                            + errors * math.log(rate) + (n - errors) * math.log1p(-rate))
            total = term
            for k in range(errors, 0, -1):
                term *= k / (n - k + 1) * (1 - rate) / rate
                total += term
                if term <= total * 1e-16:
                    break
            return total
        for k in range(errors):
            term *= (n - k) / (k + 1) * rate / (1 - rate)
            total += term
        return total
    low, high = errors / n, 1.0
    for _ in range(60):
        middle = (low + high) / 2
        low, high = (middle, high) if at_most(middle) > 1 - confidence else (low, middle)
    return high


def reviews_needed(errors, n, limit, confidence=.95):
    """How many MORE error-free reviews would bring the upper bound to the limit. None when the errors already seen rule it out in practice."""
    if upper_bound(errors, n, confidence) <= limit:
        return 0
    low, high = n, max(n, 1)
    while upper_bound(errors, high, confidence) > limit:
        high *= 2
        if high > 200000:
            return None
    while low + 1 < high:
        middle = (low + high) // 2
        low, high = (low, middle) if upper_bound(errors, middle, confidence) <= limit else (middle, high)
    return high - n


def supersedes(settings, job):
    """Whether this batch's new grades may replace the instructor's earlier decision (`REVIEW_RULE`)."""
    return settings.get("review_rule") == REVIEW_RULE and job.get("operation") != "rescore"


def sampled(identifier, share, salt):
    """A stable pseudo-random share of results: decided by the result's own random ID, so it cannot drift with re-runs."""
    return share > 0 and int(digest(f"{salt}:{identifier}")[:12], 16) / 16 ** 12 < share


class AcceptanceMixin:
    def acceptance_scope(self, task, job, settings, question):
        """Exactly what a benchmark report covers. Any change here is a different scope: an old permission simply no longer applies."""
        from . import config
        if self.is_cloud(job):
            named = {e["name"]: e for e in settings["endpoints"]}
            try:
                asked = {role: e["reasoning_choice"] for role, e in config.roles(settings, job["tier"]).items() if e and e.get("reasoning_choice")}
            except ValueError:
                asked = {}  # A connection removed since: the scope still names it, and no choice can be resolved for it.
            # A model asked to think harder is another grader, so the choice belongs to the scope wherever one exists.
            roles = {role: {"model": named[name]["model"], "lineage": named[name]["lineage"], "endpoint": name, "modality": named[name]["modality"],
                            **({"reasoning": asked[role]} if role in asked else {})} if name in named else {"endpoint": name, "model": settings["nebius_model"]}
                     for role, name in settings["tiers"][job["tier"]].items() if name}
        else:
            roles = {"reader": {"model": settings["local_verifier_model"]}, "primary": {"model": settings["local_grader_model"]}}
        from .risk import POLICY
        from .review_rules import VERSION
        scope = {"review_policy": {"risk": POLICY, "review": VERSION}, "question_id": question["question_id"], "question_revision_id": task["question_revision_id"], "rubric_id": task["rubric_id"],
                 "exam_version": question.get("exam_version", ""), "tier": self.compute(job), "roles": roles,
                 # A risk key at its default is omitted, so a batch that changed nothing keeps the scope it always had.
                 "risk": {k: v for k, v in settings["risk"].items() if v != RISK_QUIET.get(k, object())},
                 "symbolic": settings["symbolic_enabled"],
                 # Direct reading names its own prompt here, so a permission earned with the OCR draft never carries into a
                 # batch that read the paper without one, and the reading prompt's exact wording is part of the scope.
                 "prompts": {name: prompt(direct_read.reconcile_prompt(settings) if name == "reconcile" and direct_read.enabled(settings, self.compute(job)) else name)[1]["prompt_sha256"]
                             for name in PROMPTS + ((direct_read.PROMPT,) if direct_read.enabled(settings, self.compute(job)) else ())},
                 "thresholds": {k: settings[k] for k in ("ocr_threshold", "visual_threshold", "grading_threshold")}}
        if settings.get("rubric_finalization"):
            scope["rubric_quality"] = settings["rubric_finalization"]  # The confirmation this batch's graders read.
        if settings.get("full_credit_rule"):
            scope["full_credit_rule"] = settings["full_credit_rule"]  # How a ticked full-credit line is read: another rule is another scope.
        if settings.get("empty_selection_rule"):
            scope["empty_selection_rule"] = settings["empty_selection_rule"]  # A grade that ticks nothing waits: another rule is another scope.
        if direct_read.enabled(settings, self.compute(job)):
            # No OCR reading stood beside the vision reader, and the checks that ask for one were waived by name. A scope that
            # says so cannot be confused with one earned under OCR.
            scope["direct_reading"] = direct_read.rule(settings)
        if settings.get("reading_page_rule"):
            scope["reading_page_recovery"] = settings["reading_page_rule"]
            scope["prompts"].update({name: prompt(name)[1]["prompt_sha256"] for name in ("read_context", "reconcile_context")})
        from . import committee
        if committee.enabled(settings) and self.compute(job) in config.TIERS:
            tiers = [committee.STANDARD]
            scope["grading_workflow"] = committee.WORKFLOW
            if settings.get("third_rule"):
                scope["third_rule"] = settings["third_rule"]  # When the additional assessment is bought: another rule is another scope.
            # A model asked to think harder is another grader: the choice is part of the scope wherever one exists. A connection
            # without catalogue facts has none, so permissions from before this choice existed keep their scope.
            scope["teams"] = {tier: {role: {"model": e["model"], "lineage": e["lineage"], "endpoint": e["name"], "modality": e["modality"],
                                            **({"reasoning": e["reasoning_choice"]} if e.get("reasoning_choice") else {})}
                for role, e in config.roles(settings, tier).items() if e and (tier == committee.STANDARD or role != 'reader')} for tier in tiers}
            # A spare that may grade in this batch is another grader, so it belongs to the scope wherever one is configured.
            for tier in tiers:
                for role in config.FALLBACK_ROLES:
                    spare = config.fallback(settings, tier, role)
                    if spare:
                        scope["teams"][tier][role + "_fallback"] = {"model": spare["model"], "lineage": spare["lineage"], "endpoint": spare["name"],
                                                                    **({"reasoning": spare["reasoning_choice"]} if spare.get("reasoning_choice") else {})}
            scope['roles'] = scope['teams'][committee.STANDARD]
            scope["escalation_enabled"] = settings.get("escalation_enabled", False)  # Frozen by every Cloud batch; High Assurance is gone.
            from . import arbitration
            if arbitration.scope(settings):
                scope["final_arbitrator"] = arbitration.scope(settings)  # Who may rule on a flagged response, by which rule and prompt.
            from . import standard
            if standard.enabled(settings):
                scope["cloud_standard"] = standard.scope(settings)
            from . import flag_resolution
            if flag_resolution.scope(settings):
                scope["flag_resolution"] = flag_resolution.scope(settings)  # A flag review's carrier batch: its rule, model and prompt.
        return scope, digest(scope)

    def permission(self, db, scope_sha256):
        """The active permission for this exact scope, or None. Its latest event decides: enabled, suspended or revoked."""
        for row in db.execute("SELECT * FROM frq_acceptance_permissions WHERE scope_sha256=? ORDER BY rowid DESC", (scope_sha256,)):
            event = db.execute("SELECT kind FROM frq_acceptance_permission_events WHERE permission_id=? ORDER BY rowid DESC LIMIT 1", (row["id"],)).fetchone()
            if event and event[0] == "enabled":
                return dict(row)
        return None

    @staticmethod
    def scope_permissions(db, scope_sha256, kinds=("enabled",)):
        """Every permission of one exact scope whose latest event is one of `kinds`. A scope should hold one, but a double submit
        from before 2026-09-22 could enable two: revoking or suspending only the newest left the older one accepting unseen."""
        found = []
        for row in db.execute("SELECT id FROM frq_acceptance_permissions WHERE scope_sha256=? ORDER BY rowid", (scope_sha256,)).fetchall():
            event = db.execute("SELECT kind FROM frq_acceptance_permission_events WHERE permission_id=? ORDER BY rowid DESC LIMIT 1", (row["id"],)).fetchone()
            if event and event[0] in kinds:
                found.append(row["id"])
        return found

    def accept_result(self, db, task, job, settings, result_id, output):
        """Called with the result just retained. Accepts only what the whole risk policy passed; otherwise it stays a proposal."""
        verdict = output["signals"].get("risk") or {}
        quality = output['signals'].get('rubric_quality') or {}
        if quality.get('reasons'):
            return None
        # A grade made under an earlier confirmation is accepted only where that confirmation gave its question the guidance that
        # stands now (2026-09-25): one question's change never holds back another's in-flight grades.
        scope = output['signals'].get('scope', {})
        if quality.get('enabled') and not self.same_finalization(db, scope.get('rubric_quality'), quality, scope.get('question_id'), task['rubric_id']):
            return None
        if output["state"] != "proposed" or verdict.get("blocks") or verdict.get("individual") or output["score"] is None:
            return None
        scope, scope_sha = output["signals"]["scope"], output["signals"]["scope_sha256"]
        from . import committee
        if committee.enabled(settings) and (output['signals'].get('committee') or {}).get('status') == 'resolved':
            row = db.execute('SELECT assignment_id,question_id,current_review_id FROM frq_responses WHERE id=?', (task['response_id'],)).fetchone()
            if row['current_review_id']:
                if not supersedes(settings, job):
                    return None  # Even an instructor's draft remains theirs to finish.
                if db.execute('SELECT result_id FROM frq_reviews WHERE id=?', (row['current_review_id'],)).fetchone()[0] == result_id:
                    return None  # A decision made on THIS grade is the instructor's word on it, never replaced by it.
                self.supersede_review(db, task, job, result_id, row)
            identifier = uid()
            db.execute('INSERT INTO frq_team_acceptances VALUES(?,?,?,?,?,?,?,?,?)', (identifier, result_id, task['response_id'],
                row['assignment_id'], row['question_id'], scope_sha, encoded(scope), verdict['policy'], timestamp()))
            from . import arbitration
            basis = arbitration.basis(output['signals'])
            self.event(db, row['assignment_id'], 'team_grade_finalized', {'response_id': task['response_id'], 'result_id': result_id, 'basis': basis,
                'acceptance_id': identifier, 'scope_sha256': scope_sha, 'committee': output['signals']['committee']}, 'grading-policy')
            return {'id': identifier, 'kind': 'automatic', 'basis': basis, 'blind': False, 'audit': False}
        allowed = self.permission(db, scope_sha)
        if allowed:
            kind, blind, audit = "automatic", 0, int(sampled(result_id, allowed["audit_share"], "audit"))
        elif settings["trial_acceptance"]:
            kind, blind, audit = "trial", int(sampled(result_id, settings["blind_share"], "blind")), 0
        else:
            return None
        row = db.execute("""SELECT r.assignment_id,r.question_id,v.status FROM frq_responses r LEFT JOIN frq_reviews v ON v.id=r.current_review_id
            WHERE r.id=?""", (task["response_id"],)).fetchone()
        if row["status"] and row["status"] != "draft":
            # The instructor already decided this response; a newer proposal waits beside that decision and is never accepted over it. A
            # benchmark permission is earned from those very reviews, so `REVIEW_RULE` (Cloud grading teams) does not apply here.
            return None
        identifier = uid()
        db.execute("INSERT INTO frq_acceptances VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (identifier, result_id, task["response_id"], row["assignment_id"], row["question_id"],
            kind, scope_sha, encoded(scope), verdict["policy"], blind, audit, allowed["id"] if allowed else None, timestamp()))
        return {"id": identifier, "kind": kind, "blind": bool(blind), "audit": bool(audit)}

    def supersede_review(self, db, task, job, result_id, row):
        """The instructor's earlier decision gives way to the new grade of a regrade they started (`REVIEW_RULE`). Nothing is erased:
        the review row stays, only the response's pointer to the decision that counts moves, and the event names both."""
        review = db.execute("SELECT id,status,rubric_id,created_at FROM frq_reviews WHERE id=?", (row["current_review_id"],)).fetchone()
        db.execute("UPDATE frq_responses SET current_review_id=NULL,version=version+1 WHERE id=? AND current_review_id=?", (task["response_id"], review["id"]))
        self.event(db, row["assignment_id"], "review_superseded_by_regrade", {"response_id": task["response_id"], "review_id": review["id"],
            "review_status": review["status"], "review_rubric_id": review["rubric_id"], "reviewed_at": review["created_at"], "result_id": result_id,
            "job_id": job["id"], "rubric_id": task["rubric_id"], "rule": REVIEW_RULE}, "grading-policy")

    def refinalize_regraded(self, jid):
        """Apply `REVIEW_RULE` once to a batch that ran before it (requirement, 2026-09-27, for Quiz 2's regrade): each response whose
        CURRENT result came from this batch and that an earlier review alone kept from automatic finalization is accepted exactly as
        the rule would have accepted it (`accept_result`, which rechecks every condition). Only a review made BEFORE that result counts
        as earlier: one the instructor made on the new grade, or after it, is their word on it and stays (its first run on Quiz 2 replaced
        four such approvals, identical to the grade, before this check; they were restored). Returns what it finalized."""
        finalized = []
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            job = dict(db.execute("SELECT * FROM frq_jobs WHERE id=?", (jid,)).fetchone())
            if job["operation"] == "rescore":
                raise ValueError("A batch that only recalculated points never replaces the instructor's decisions.")
            settings = {**json.loads(job["profile_json"]), "review_rule": REVIEW_RULE}
            for row in db.execute("""SELECT t.*,x.id result_id FROM frq_responses r JOIN frq_results x ON x.id=r.current_result_id JOIN frq_tasks t ON t.id=x.task_id
                    WHERE t.job_id=? AND t.status='complete' AND r.current_review_id IS NOT NULL
                    AND NOT EXISTS(SELECT 1 FROM frq_all_acceptances a WHERE a.result_id=x.id)
                    AND NOT EXISTS(SELECT 1 FROM frq_reviews v WHERE v.id=r.current_review_id AND (v.result_id=x.id OR v.created_at>x.created_at))""", (jid,)).fetchall():
                task, result = dict(row), db.execute("SELECT * FROM frq_results WHERE id=?", (row["result_id"],)).fetchone()
                output = {"signals": json.loads(result["signals_json"]), "state": result["state"], "score": json.loads(result["score_json"])}
                if output["signals"].get("scope_sha256") and self.accept_result(db, task, job, settings, result["id"], output):
                    finalized.append(task["response_id"])
            if finalized:
                self.event(db, job["assignment_id"], "regraded_refinalized", {"job_id": jid, "responses": len(finalized), "rule": REVIEW_RULE}, "workspace")
        return {"job_id": jid, "finalized": finalized}

    @staticmethod
    def acceptance_of(db, result_id):
        row = db.execute("""SELECT a.*,r.outcome FROM frq_all_acceptances a LEFT JOIN frq_all_acceptance_reviews r ON r.acceptance_id=a.id WHERE a.result_id=?""", (result_id,)).fetchone()
        return dict(row) if row else None

    def record_acceptance_review(self, db, result, review_id, status, selections, adjustments, blind_kept=True):
        """The instructor looked at an accepted response: confirmed, or overturned. The corrected lines are in the ordinary review."""
        acceptance = self.acceptance_of(db, result["id"])
        if not acceptance or acceptance["outcome"] or status == "draft":
            return None
        proposed = sorted(a["rubric_item_id"] for a in json.loads(result["applications_json"]) if a["applied"])
        outcome = "confirmed" if status == "approved" and sorted(selections) == proposed and not adjustments else "overturned"
        signals = json.loads(result["signals_json"])
        # One-shot and Tag-Team (removed 2026-09-23) finalized grades of their own; a review of one is recorded beside it as before.
        table = {'team_consensus': 'frq_team_acceptance_reviews', 'one_shot': 'frq_one_shot_acceptance_reviews', 'tag_team': 'frq_tag_team_acceptance_reviews'}.get(acceptance['basis'], 'frq_acceptance_reviews')
        db.execute(f"INSERT INTO {table} VALUES(?,?,?,?,?,?,?)", (uid(), acceptance["id"], review_id, outcome, int(bool(acceptance["blind"]) and blind_kept),
            encoded(self.missed_signals(signals)) if outcome == "overturned" else "[]", timestamp()))
        if outcome == "overturned" and acceptance["kind"] == "automatic" and acceptance["permission_id"]:
            # A failed audit (or any overturned automatic acceptance) suspends acceptance for that question at once: every permission
            # of the scope it was accepted under, not only its own, so no other one keeps accepting unseen.
            for permission_id in dict.fromkeys([acceptance["permission_id"], *self.scope_permissions(db, acceptance["scope_sha256"])]):
                self._permission_event(db, permission_id, "suspended", "An automatically accepted response was overturned by the instructor.")
        return outcome

    @staticmethod
    def missed_signals(signals):
        """For an overturned acceptance: the nearest signals that did NOT fire. These are the policy's bugs, listed for the instructor."""
        c, found = (signals.get("risk") or {}).get("components") or {}, []
        if not c:
            return ["No risk components were retained."]
        if c["primary"]["confidence"] < .97 or (c["primary"]["min_item_confidence"] or 1) < .97:
            found.append(f"Grader confidence was {c['primary']['confidence']:.2f}, above the threshold but not high.")
        if c["symbolic"].get("indeterminate"):
            found.append(f"{c['symbolic']['indeterminate']} line(s) could not be checked symbolically.")
        if c["symbolic"].get("result") in ("indeterminate", "not_run"):
            found.append("No symbolic check could verify the work.")
        if c.get("choice"):
            found.append(f"The multiple-choice reader alone read the mark ({c['choice']['choice']}, confidence {c['choice']['confidence']:.2f}), "
                         "without OCR or a grading team.")
        elif signals.get("tag_team"):
            found.append("Tag-Team's reviewer accepted a grade after inspecting the first model's same-call reading and assessment.")
        elif c.get("one_shot"):
            found.append("One-shot used a single model, without independent OCR or another grader.")
        elif c["ocr"]["providers"] < 2:
            found.append("Only one OCR reader stood beside the vision reader.")
        if c["reading"]["visual_confidence"] is not None and c["reading"]["visual_confidence"] < .97:
            found.append(f"Reading confidence was {c['reading']['visual_confidence']:.2f}.")
        if c.get("choice"):
            pass  # The read is the one signal, named above: no team, reviewer or work was asked about.
        elif c.get("committee"):
            if not c["committee"].get("third_required"):
                found.append("The first two graders agreed; no additional reviewer was requested.")
        elif c.get("verifier") is None:
            found.append("No adversarial reviewer looked at this response (it was not escalated).")
        elif c["verifier"]["minor"]:
            found.append(f"The adversarial reviewer raised {c['verifier']['minor']} minor objection(s).")
        if c["primary"]["work_consistent"] == "unclear" and not c.get("choice"):
            found.append("The grader could not tell whether the work was consistent.")
        return found or ["No signal came close: every rule passed with margin. The policy needs a new rule for this kind of error."]

    @staticmethod
    def _permission_event(db, permission_id, kind, reason, actor="worker"):
        db.execute("INSERT INTO frq_acceptance_permission_events(permission_id,kind,reason,actor,created_at) VALUES(?,?,?,?,?)", (permission_id, kind, reason, actor, timestamp()))

    def suspend_acceptance(self, db, aid, reason, *, question_ids=None, keep=None):
        """Ends automatic acceptance until the instructor looks again; returns how many CURRENT automatic grades it took back to
        review (acceptances of a response's current result; older acceptances and permissions are suspended too but not counted).
        Without `question_ids` it ends every one of the assessment (a failed canary did, until the canary was removed on 2026-09-28).
        A changed rubric ends only its own question's
        (`question_ids`, 2026-09-25), and a new finalization only those whose question's guidance it changed: `keep(question_id,
        scope)` spares a permission (its report's scope) or an acceptance (its own scope) that should stand."""
        suspended = 0
        # Suspensions are recorded in a pinned order (question, then section and student, as the review listing orders them), so
        # the event history of one call does not depend on the query plan or on random acceptance IDs.
        for row in db.execute("""SELECT p.id,p.question_id,b.scope_json FROM frq_acceptance_permissions p
                LEFT JOIN frq_benchmark_reports b ON b.id=p.report_id WHERE p.assignment_id=? ORDER BY p.rowid""", (aid,)).fetchall():
            if question_ids is not None and row["question_id"] not in question_ids:
                continue
            latest = db.execute("SELECT kind FROM frq_acceptance_permission_events WHERE permission_id=? ORDER BY rowid DESC LIMIT 1", (row["id"],)).fetchone()
            if latest and latest[0] == "enabled" and not (keep and keep(row["question_id"], json.loads(row["scope_json"] or "{}"))):
                self._permission_event(db, row["id"], "suspended", reason)
        for row in db.execute("""SELECT a.id,a.basis,a.question_id,a.scope_json,(r.current_result_id=a.result_id AND r.current_review_id IS NULL) current
                FROM frq_all_acceptances a JOIN frq_responses r ON r.id=a.response_id JOIN frq_questions q ON q.id=a.question_id
                WHERE a.assignment_id=? AND a.basis IN ('team_consensus','one_shot','tag_team') AND a.active=1
                ORDER BY q.question_key,r.section_id,r.enrollment_id,a.created_at,a.id""", (aid,)).fetchall():
            if question_ids is not None and row["question_id"] not in question_ids:
                continue
            if keep and keep(row["question_id"], json.loads(row["scope_json"] or "{}")):
                continue
            table = {'one_shot': 'frq_one_shot_acceptance_events', 'tag_team': 'frq_tag_team_acceptance_events', 'team_consensus': 'frq_team_acceptance_events'}[row['basis']]
            db.execute(f"INSERT INTO {table}(acceptance_id,kind,reason,actor,created_at) VALUES(?,'suspended',?,'worker',?)", (row['id'], reason, timestamp()))
            suspended += bool(row["current"])
        return suspended

    def reinstate_guidance_suspensions(self, db, aid, reason, keep, request_id):
        """GUIDANCE PUT BACK (requirement, 2026-10-15: finalizing Quiz 2's makeup Version E changed Versions A-D's guidance and sent 612
        of their graded answers back to review). A team grade a rubric finalization suspended with `reason` (its guidance changed)
        counts again once a later finalization gives its question back the guidance it was graded under (`keep(question_id, scope)`,
        as `suspend_acceptance` reads it), while it is still the response's current result, the instructor has not reviewed it since,
        and nothing else holds it back. Inside the caller's transaction and lifecycle lock; suspension rows are never changed.
        Returns how many grades count again."""
        rows = db.execute("""SELECT e.id event_id,e.acceptance_id,a.question_id,a.scope_json,a.result_id,r.current_result_id,r.current_review_id
            FROM frq_team_acceptance_events e JOIN frq_team_acceptances a ON a.id=e.acceptance_id JOIN frq_responses r ON r.id=a.response_id
            WHERE a.assignment_id=? AND e.kind='suspended' AND e.reason=?
            AND NOT EXISTS(SELECT 1 FROM frq_team_acceptance_reinstatements x WHERE x.event_id=e.id) ORDER BY e.id""", (aid, reason)).fetchall()
        by_acceptance = {}
        for row in rows:
            by_acceptance.setdefault(row["acceptance_id"], []).append(row)
        now, reinstated = timestamp(), 0
        for acceptance_id, ours in by_acceptance.items():
            row = ours[0]
            standing = db.execute("""SELECT COUNT(*) FROM frq_team_acceptance_events e WHERE e.acceptance_id=?
                AND NOT EXISTS(SELECT 1 FROM frq_team_acceptance_reinstatements x WHERE x.event_id=e.id)""", (acceptance_id,)).fetchone()[0]
            if (standing != len(ours) or row["current_result_id"] != row["result_id"] or row["current_review_id"]
                    or not keep(row["question_id"], json.loads(row["scope_json"] or "{}"))):
                continue
            for suspension in ours:
                db.execute("INSERT INTO frq_team_acceptance_reinstatements VALUES(?,?,?,?,?,?,?)",
                           (uid(), suspension["event_id"], acceptance_id, "The rubric finalization gave its question back the guidance it was graded under.",
                            request_id, "workspace", now))
            reinstated += 1
        return reinstated

    # Reinstating what a CANCELLED canary suspended (requirement, 2026-09-28; migration 032) ------------------------------------
    @staticmethod
    def cancelled_canary_suspensions(db, job_id):
        """The suspensions a canary of this CANCELLED batch made although it measured nothing (settled failed only because the cancel
        closed its requests, before canary._settle_canary learned to skip such a run): (reinstatable, skipped). A suspension is
        reinstatable when its automatic grade is still the response's current result with no instructor review since, and the grade
        has no other suspension left standing. Read only."""
        job = db.execute("SELECT id,assignment_id,cancel_requested FROM frq_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or not job["cancel_requested"]:
            raise Conflict("Only a cancelled batch's canary can be undone this way.")
        endpoints = [r[0] for r in db.execute("""SELECT DISTINCT r.endpoint FROM frq_canary_runs r JOIN frq_canary_items i ON i.run_id=r.id
            WHERE r.job_id=? AND r.status='failed' AND i.error='Cancelled with its batch.'""", (job_id,))]
        moments = [r[0] for r in db.execute("""SELECT created_at FROM frq_events WHERE assignment_id=? AND kind='canary_failed'
            AND json_extract(detail_json,'$.job_id')=?""", (job["assignment_id"], job_id))]
        if not endpoints or not moments:
            return [], []
        from datetime import datetime, timedelta
        windows = [(datetime.fromisoformat(moment) - timedelta(seconds=2)).isoformat() for moment in moments]
        ends = [(datetime.fromisoformat(moment) + timedelta(seconds=2)).isoformat() for moment in moments]
        reasons = [f"Canary failed on {endpoint}." for endpoint in endpoints]
        rows = db.execute(f"""SELECT e.id event_id,e.acceptance_id,e.created_at,a.result_id,a.response_id,r.current_result_id,r.current_review_id
            FROM frq_team_acceptance_events e JOIN frq_team_acceptances a ON a.id=e.acceptance_id JOIN frq_responses r ON r.id=a.response_id
            WHERE a.assignment_id=? AND e.kind='suspended' AND e.reason IN ({','.join('?' for _ in reasons)})
            AND NOT EXISTS(SELECT 1 FROM frq_team_acceptance_reinstatements x WHERE x.event_id=e.id) ORDER BY e.id""",
            (job["assignment_id"], *reasons)).fetchall()
        chosen = [dict(row) for row in rows if any(start <= row["created_at"] <= end for start, end in zip(windows, ends))]
        ours = {}
        for row in chosen:
            ours[row["acceptance_id"]] = ours.get(row["acceptance_id"], 0) + 1
        reinstatable, skipped = [], []
        for row in chosen:
            standing = db.execute("""SELECT COUNT(*) FROM frq_team_acceptance_events e WHERE e.acceptance_id=?
                AND NOT EXISTS(SELECT 1 FROM frq_team_acceptance_reinstatements x WHERE x.event_id=e.id)""", (row["acceptance_id"],)).fetchone()[0]
            others = standing - ours[row["acceptance_id"]]  # suspensions of this grade that are not this cancelled canary's
            if row["current_result_id"] != row["result_id"]:
                skipped.append({**row, "why": "graded again since"})
            elif row["current_review_id"]:
                skipped.append({**row, "why": "reviewed by the instructor since"})
            elif others:
                skipped.append({**row, "why": "suspended for another reason too"})
            else:
                reinstatable.append(row)
        return reinstatable, skipped

    def restore_certified(self, job_id, request_id):
        """Put back the certified grades a later batch replaced (requirement, 2026-09-29). For each response whose CURRENT result came
        from batch `job_id`, with no instructor decision on it and nothing under way, the response points again at the result the plan's
        latest certification certified, where it certified an AUTOMATIC decision whose acceptance of that very result is still active.
        Nothing is erased: the batch's results, steps, costs and acceptances stay on record, and one `certified_grades_restored` event
        names every pointer moved and every response left alone, with why. The same request ID returns what it did.
        Returns {restored: [...], skipped: [{response_id, why}], replayed}."""
        request_id = text(request_id, "Request ID", 128, False)
        from .progress import latest_certification
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM frq_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                db.commit()
                raise NotFound("No such grading batch.")
            aid = job["assignment_id"]
            for row in db.execute("SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='certified_grades_restored'", (aid,)):
                done = json.loads(row[0])
                if done.get("request_id") == request_id:
                    db.commit()
                    return {**done, "replayed": True}
            if job["status"] in ("pending", "running"):
                db.commit()
                raise Conflict("Wait for the batch to finish before putting back what it replaced.")
            certification = latest_certification(db, aid)
            if not certification:
                db.commit()
                raise ValueError("This assignment has no certification to go back to.")
            certified = {}
            for row in db.execute("SELECT parts_json FROM frq_certification_students WHERE certification_id=?", (certification["id"],)):
                for entry in ((json.loads(row[0]).get("frq") or {}).get("questions") or {}).values():
                    certified[entry.get("response_id")] = entry
            restored, skipped = [], []
            for row in db.execute("""SELECT r.id,r.current_result_id,r.current_review_id FROM frq_responses r JOIN frq_results x ON x.id=r.current_result_id
                    JOIN frq_tasks t ON t.id=x.task_id WHERE t.job_id=? AND r.assignment_id=? ORDER BY r.id""", (job_id, aid)).fetchall():
                entry, why, acceptance = certified.get(row["id"]), None, None
                if not entry:
                    why = "the certification holds no grade of it"
                elif row["current_review_id"]:
                    why = "you have decided it since"
                elif db.execute("SELECT 1 FROM frq_tasks WHERE response_id=? AND status IN ('pending','running')", (row["id"],)).fetchone():
                    why = "work on it is under way"
                elif not str(entry.get("decision") or "").startswith("automatic:"):
                    why = "its certified grade was not an automatic one"
                else:
                    acceptance = entry["decision"].split(":", 1)[1]
                    if not db.execute("""SELECT 1 FROM frq_all_acceptances a JOIN frq_results x ON x.id=a.result_id WHERE a.id=? AND a.result_id=?
                            AND a.kind='automatic' AND a.active=1 AND x.response_id=?""", (acceptance, entry.get("result_id"), row["id"])).fetchone():
                        why = "its certified grade is no longer an active automatic acceptance"
                if why:
                    skipped.append({"response_id": row["id"], "why": why})
                    continue
                db.execute("UPDATE frq_responses SET current_result_id=?,version=version+1 WHERE id=? AND current_result_id=? AND current_review_id IS NULL",
                           (entry["result_id"], row["id"], row["current_result_id"]))
                restored.append({"response_id": row["id"], "from_result_id": row["current_result_id"], "to_result_id": entry["result_id"],
                                 "acceptance_id": acceptance})
            detail = {"request_id": request_id, "job_id": job_id, "certification_id": certification["id"], "restored": restored, "skipped": skipped}
            self.event(db, aid, "certified_grades_restored", detail, "workspace")
            if restored:
                self.touch(db, aid)
            db.commit()
        return {**detail, "replayed": False}

    def reinstate_cancelled_canary(self, job_id, request_id, actor="instructor"):
        """Records a reinstatement of every reinstatable suspension (above) in ONE transaction, with an Inbox-style event naming the
        batch and the counts; a repeated request ID reinstates nothing twice. The batch is marked cancelled again (the canary had
        overwritten its status with "failed"). Suspension rows are never changed or deleted."""
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("Name the request.")
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            done = db.execute("SELECT COUNT(*) FROM frq_team_acceptance_reinstatements WHERE request_id=?", (request_id,)).fetchone()[0]
            if done:
                return {"reinstated": done, "skipped": [], "replayed": True}
            job = db.execute("SELECT assignment_id FROM frq_jobs WHERE id=?", (job_id,)).fetchone()
            reinstatable, skipped = self.cancelled_canary_suspensions(db, job_id)
            now, reason = timestamp(), f"The canary of batch {job_id} was cancelled with its batch and measured nothing."
            for row in reinstatable:
                db.execute("INSERT INTO frq_team_acceptance_reinstatements VALUES(?,?,?,?,?,?,?)",
                           (uid(), row["event_id"], row["acceptance_id"], reason, request_id, actor, now))
            db.execute("""UPDATE frq_jobs SET status='cancelled',notice=?,updated_at=? WHERE id=? AND status='failed'""",
                       ("Cancelled during its quality check; the check measured nothing and suspended nothing.", now, job_id))
            self.event(db, job["assignment_id"], "acceptance_reinstated", {"job_id": job_id, "request_id": request_id,
                "reinstated": len(reinstatable), "skipped": {why: sum(1 for r in skipped if r["why"] == why) for why in {r["why"] for r in skipped}},
                "reason": reason}, actor)
            return {"reinstated": len(reinstatable), "skipped": skipped, "replayed": False}

    # The report: the instructor's review of what the policy accepted ---------------------------------------------------
    def acceptance_report(self, aid, question_id, scope_sha256=None, limit=None):
        """How the policy did on this question, per scope. The gate reads the BLIND share: reviews made before the AI's decision was shown."""
        settings = self.settings()["settings"]
        limit = settings["error_limit"] if limit is None else number(limit, "Error limit", .0001, .5)
        with self.rosters.lock(), connect(self.path) as db:
            self.question(db, aid, question_id)
            scopes = {}
            for row in db.execute("""SELECT a.*,r.outcome,r.missed_json,r.review_id,r.blind reviewed_blind,p.section_id,p.enrollment_id FROM frq_all_acceptances a
                    JOIN frq_responses p ON p.id=a.response_id LEFT JOIN frq_all_acceptance_reviews r ON r.acceptance_id=a.id
                    WHERE a.assignment_id=? AND a.question_id=? ORDER BY a.created_at,a.id""", (aid, question_id)):
                entry = scopes.setdefault(row["scope_sha256"], {"scope": json.loads(row["scope_json"]), "rows": []})
                entry["rows"].append(dict(row))
            reports = []
            for sha, entry in scopes.items():
                if scope_sha256 and sha != scope_sha256:
                    continue
                reviewed = [r for r in entry["rows"] if r["outcome"]]
                def tally(rows):
                    n, errors = len(rows), sum(r["outcome"] == "overturned" for r in rows)
                    return {"reviewed": n, "overturned": errors, "error_rate": errors / n if n else None, "upper_bound_95": upper_bound(errors, n) if n else None}
                blind = [r for r in reviewed if r["reviewed_blind"]]
                gate = tally(blind)
                gate.update(limit=limit, basis="Blind trial reviews: the instructor judged before seeing the AI's decision.",
                            more_reviews_needed=reviews_needed(gate["overturned"], gate["reviewed"], limit),
                            eligible=bool(gate["reviewed"]) and gate["upper_bound_95"] <= limit,
                            students=len({(r["section_id"], r["enrollment_id"]) for r in blind}))
                reports.append({"scope_sha256": sha, "scope": entry["scope"], "accepted": len(entry["rows"]), "trial": sum(r["kind"] == "trial" for r in entry["rows"]),
                    "automatic": sum(r["kind"] == "automatic" for r in entry["rows"]), "team_finalized": sum(r['basis'] == 'team_consensus' for r in entry['rows']),
                    "awaiting_review": sum(not r['outcome'] and (r['kind'] == 'trial' or r['audit']) for r in entry['rows']),
                    "all": tally(reviewed), "blind": gate, "seen": tally([r for r in reviewed if not r["reviewed_blind"]]),
                    "overturned": [{"response_id": r["response_id"], "blind": bool(r["reviewed_blind"]), "kind": r["kind"], "should_have_caught": json.loads(r["missed_json"] or "[]")}
                                   for r in reviewed if r["outcome"] == "overturned"],
                    "permission": self._permission_status(db, sha),
                    "note": "The observed rate alone is not enough: with no errors in n responses the true rate can still be about 3/n."})
        return {"question_id": question_id, "limit": limit, "reports": sorted(reports, key=lambda r: -r["accepted"])}

    def _permission_status(self, db, scope_sha256):
        row = db.execute("SELECT * FROM frq_acceptance_permissions WHERE scope_sha256=? ORDER BY rowid DESC LIMIT 1", (scope_sha256,)).fetchone()
        if not row:
            return {"status": "none"}
        events = [dict(e) for e in db.execute("SELECT kind,reason,actor,created_at FROM frq_acceptance_permission_events WHERE permission_id=? ORDER BY rowid", (row["id"],))]
        return {"status": events[-1]["kind"] if events else "none", "id": row["id"], "report_id": row["report_id"], "error_limit": row["error_limit"], "audit_share": row["audit_share"], "events": events}

    def enable_acceptance(self, aid, question_id, data):
        """Impossible without evidence: the retained report for this exact scope must put the upper bound at or below the limit."""
        scope_sha = text(data.get("scope_sha256"), "Scope", 64, False)
        if data.get("confirm_automatic_acceptance") is not True:
            raise ValueError("Confirm that scores of this question may become final without your look, within this report's scope.")
        settings = self.settings()["settings"]
        limit = number(data.get("error_limit", settings["error_limit"]), "Error limit", .0001, .5)
        audit = number(data.get("audit_share", settings["audit_share"]), "Audit share", .01, 1)
        # GRADING: the evidence and permission are one lifecycle-locked decision; a new review cannot slip between them.
        with self.rosters.lock():
            found = next((r for r in self.acceptance_report(aid, question_id, scope_sha, limit)["reports"]), None)
            if not found:
                raise Conflict("No benchmark report exists for this question and scope. Review trial-accepted responses first; that review is the report.")
            gate = found["blind"]
            if not gate["eligible"]:
                more = gate["more_reviews_needed"]
                raise Conflict(f"The evidence does not allow it yet: {gate['overturned']} overturned in {gate['reviewed']} blind reviews puts the upper 95% bound of the error rate at "
                               f"{(gate['upper_bound_95'] if gate['upper_bound_95'] is not None else 1):.1%}, above your limit of {limit:.1%}. "
                               + (f"About {more} more error-free blind reviews would reach it." if more else "With these errors the limit is out of reach; fix what the overturned responses show first."))
            with connect(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                # Already enabled for this exact scope (a second click, a retried request): nothing new is created, and the one
                # permission there is stays the one a revocation or a failed audit ends.
                if not self.scope_permissions(db, scope_sha):
                    self._enable_permission(db, aid, question_id, scope_sha, found, gate, limit, audit, data)
        return self.acceptance_report(aid, question_id, scope_sha)

    def _enable_permission(self, db, aid, question_id, scope_sha, found, gate, limit, audit, data):
        quality = self.rubric_quality_status(db, aid)
        expected_quality = quality['review']['id'] if quality['enabled'] and quality['state'] == 'confirmed' else None
        if (quality['enabled'] and not expected_quality) or found['scope'].get('rubric_quality') != expected_quality:
            raise Conflict('The rubric quality policy changed. Benchmark the current confirmed policy before enabling automatic acceptance.')
        report_id, permission_id, now = uid(), uid(), timestamp()
        db.execute("INSERT INTO frq_benchmark_reports VALUES(?,?,?,?,?,?,?,?)", (report_id, aid, question_id, scope_sha, encoded(found["scope"]), encoded(found), digest(found), now))
        db.execute("INSERT INTO frq_acceptance_permissions VALUES(?,?,?,?,?,?,?,?)", (permission_id, aid, question_id, scope_sha, report_id, limit, audit, now))
        self._permission_event(db, permission_id, "enabled", text(data.get("note", ""), "Note", 4000), "workspace")
        self.event(db, aid, "automatic_acceptance_enabled", {"question_id": question_id, "scope_sha256": scope_sha, "report_id": report_id, "upper_bound_95": gate["upper_bound_95"],
                                                             "blind_reviews": gate["reviewed"], "error_limit": limit, "audit_share": audit})

    def disable_acceptance(self, aid, question_id, data):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM frq_acceptance_permissions WHERE assignment_id=? AND question_id=? AND scope_sha256=? ORDER BY rowid",
                              (aid, question_id, data.get("scope_sha256"))).fetchall()
            if not rows:
                raise NotFound("No permission exists for this scope.")
            note = text(data.get("note", ""), "Note", 4000)
            # Every permission of the scope that is not revoked yet, enabled or suspended: none may accept unseen afterwards.
            ended = [pid for pid in self.scope_permissions(db, data.get("scope_sha256"), ("enabled", "suspended")) if pid in {r["id"] for r in rows}]
            for permission_id in ended or [rows[-1]["id"]]:
                self._permission_event(db, permission_id, "revoked", note, "workspace")
            self.event(db, aid, "automatic_acceptance_revoked", {"question_id": question_id, "scope_sha256": data.get("scope_sha256"), "permissions": ended or [rows[-1]["id"]]})
        return self.acceptance_report(aid, question_id, data.get("scope_sha256"))

    def acceptance_queue(self, aid, question_id=None):
        """What waits for the instructor's confirm-or-overturn look: blind ones first (graded before the AI's decision is shown), then audits, then the rest."""
        with self.rosters.lock(), connect(self.path) as db:
            rows = [dict(r) for r in db.execute("""SELECT a.id,a.response_id,a.result_id,a.kind,a.blind,a.audit,a.question_id,a.created_at FROM frq_all_acceptances a
                JOIN frq_responses p ON p.id=a.response_id AND p.current_result_id=a.result_id LEFT JOIN frq_all_acceptance_reviews r ON r.acceptance_id=a.id
                WHERE a.assignment_id=? AND (? IS NULL OR a.question_id=?) AND r.id IS NULL AND (a.kind='trial' OR a.audit=1)
                ORDER BY a.blind DESC,a.audit DESC,a.created_at,a.id""", (aid, question_id, question_id))]
        return {"waiting": rows, "blind": sum(r["blind"] for r in rows), "audit": sum(r["audit"] for r in rows)}
