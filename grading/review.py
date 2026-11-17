"""Human decisions are append-only and separate from machine proposals."""
import json
import tempfile
import threading
import zipfile
from collections import OrderedDict

from werkzeug.exceptions import Conflict, Forbidden, NotFound
from course_storage import timestamp
from rosters import connect

from .assignments import version
from .models import digest, encoded, items, number, text, transcript, uid
from .scoring import score
from . import arbitration, participation, risk, storage
from .providers.gradescope import GradescopeAdapter
from .rubric_quality import apply_quality, quality_flags

SOFTWARE = "faculty-frq-cloud-grading/1"

# (catalog, result id) -> what the listings read of a proposal. Results are immutable and retained (frq_result_immutable,
# frq_result_retained), so these never go stale, and each listing no longer walks about 20 KB of signals JSON per response.
# Everything that does change (reviews, acceptances, pins, tasks, the finalization) is still read on every request. Least recently
# used first out, one entry at a time: when the plans one Grading poll reads hold more results than the bound, each still keeps most
# of its own instead of every fill starting the cache over.
_FACTS, _FACTS_LOCK, FACTS_MAX = OrderedDict(), threading.Lock(), 20_000
FACTS_SQL = """SELECT id,state,applications_json,score_json,json_extract(signals_json,'$.rubric_quality.base_state'),
    json_extract(signals_json,'$.scope.rubric_quality'),json_extract(signals_json,'$.risk.components.committee'),
    json_extract(signals_json,'$.risk.components.second.agrees'),json_extract(signals_json,'$.risk.components.escalation.rubric_disagreement.agrees'),
    json_extract(signals_json,'$.rubric_ambiguities'),json_extract(signals_json,'$.second.rubric_ambiguities'),
    json_extract(signals_json,'$.risk.components.reading.blank_crops') FROM frq_results WHERE id IN ({})"""
NO_FACTS = {"state": None, "applied": (), "score_json": None, "base_state": None, "graded_under": None, "team_decision": None,
            "grader_agrees": None, "initial_grader_agrees": None, "doubts": None, "second_doubts": None, "blank": None}


def result_facts(db, catalog, ids):
    """{result_id: facts} for the given result IDs that exist: state, the applied rubric lines in their order, score_json, and the
    signals the listings read, as SQL gives them. Only missing IDs are read; the answer is built here, so another thread evicting
    an entry cannot take it from under it."""
    found, missing = {}, []
    with _FACTS_LOCK:
        for rid in dict.fromkeys(i for i in ids if i):
            fact = _FACTS.get((catalog, rid))
            if fact is None:
                missing.append(rid)
            else:
                _FACTS.move_to_end((catalog, rid))
                found[rid] = fact
    new = {}
    for start in range(0, len(missing), 500):
        chunk = missing[start:start + 500]
        for row in db.execute(FACTS_SQL.format(",".join("?" * len(chunk))), chunk):
            new[row[0]] = {"state": row[1], "applied": tuple(a["rubric_item_id"] for a in json.loads(row[2] or "[]") if a["applied"]),
                           "score_json": row[3], "base_state": row[4], "graded_under": row[5], "team_decision": row[6], "grader_agrees": row[7],
                           "initial_grader_agrees": row[8], "doubts": row[9], "second_doubts": row[10], "blank": row[11]}
    if new:
        with _FACTS_LOCK:
            for rid, fact in new.items():
                _FACTS[(catalog, rid)] = fact
                _FACTS.move_to_end((catalog, rid))
            while len(_FACTS) > FACTS_MAX:
                _FACTS.popitem(last=False)
        found.update(new)
    return found


def forget_result_facts():
    """For a test that rewrites a retained result (only possible with its immutability trigger dropped)."""
    with _FACTS_LOCK:
        _FACTS.clear()


class ReviewMixin:
    def comparisons(self, db, response_id, source_sha256, rubric_id):
        """Latest candidate per exact model/settings profile on the same evidence."""
        candidates = {}
        for row in db.execute("""SELECT x.*,t.id tid,j.mode,j.tier,j.profile_json FROM frq_results x
                JOIN frq_tasks t ON t.id=x.task_id JOIN frq_jobs j ON j.id=t.job_id
                WHERE x.response_id=? AND t.source_sha256=? AND t.rubric_id=? AND t.status='complete'
                ORDER BY x.rowid""", (response_id, source_sha256, rubric_id)):
            profile = json.loads(row["profile_json"])
            from . import config
            # A tier is named by its primary grader's endpoint; vendors and models stay in the frozen profile. A batch of a scheme
            # removed on 2026-09-23 is named from its own frozen profile, exactly as it was graded.
            scheme = config.historical_scheme(profile)
            model = ((json.loads(row["signals_json"]).get("jev_routing") or {}).get("endpoint", "") if scheme == "jev_routing"
                     else profile.get("tag_team_model", "") if scheme == "tag_team" else profile.get("one_shot_model", "") if scheme == "one_shot"
                     else (profile.get("tiers", {}).get(row["tier"]) or {}).get("primary", "") if row["tier"]
                     else profile["nebius_model" if row["mode"] == "nebius" else "local_grader_model"])
            name = self.compute(dict(row)) + " / " + model + " / " + digest(profile)[:10]
            task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (row["tid"],)).fetchone())
            candidates[name + " / grader"] = self.decode_result(row)
            recognition = self.step(db, task, "recognizing")
            for candidate in recognition["candidates"]:
                candidates[name + " / " + candidate["provider"] + " only"] = {"transcript": candidate["transcript"]}
            verified = self.step(db, task, "reconciling")
            candidates[name + (" / same-call reading" if scheme else " / verifier")] = {"transcript": verified["transcript"]}
        return candidates

    def metrics(self, aid):
        from .metrics import report
        from .review_metrics import signal_report
        with self.rosters.lock(), connect(self.path) as db:
            self.plan(db, aid)
            profiles = {}
            # The instructor's decisions are the truth the graders are measured by: never a grade Faculty approved for participation.
            for row in db.execute(f"""SELECT v.*,r.id response_id FROM frq_responses r JOIN frq_reviews v ON v.id=r.current_review_id
                    WHERE r.assignment_id=? AND v.status IN ('approved','manual') AND NOT {participation.APPROVAL}""", (aid, participation.APPROVED)):
                final = self.decode_result(row)
                for key, candidate in self.comparisons(db, row["response_id"], row["source_sha256"], row["rubric_id"]).items():
                    profiles.setdefault(key, []).append({"expected": final, "proposed": candidate, "rubric": self.rubric(db, row["rubric_id"])["rules"]})
            burden = self.burden(db, aid)
            signals = signal_report(db, aid)
        return {"profiles": {key: report(rows) for key, rows in profiles.items()}, "burden": burden, "review_signals": signals}

    @staticmethod
    def burden(db, aid):
        """What the goal is measured by: the instructor's work per response, not the share graded without a person."""
        from datetime import datetime
        reviews = [dict(r) for r in db.execute("""SELECT v.*,x.state proposal_state,x.applications_json proposed,x.transcript_json read FROM frq_reviews v
            JOIN frq_responses r ON r.id=v.response_id LEFT JOIN frq_results x ON x.id=v.result_id WHERE r.assignment_id=? AND v.status!='draft' ORDER BY v.rowid""", (aid,))]
        groups = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='answer_group_approved' ORDER BY id", (aid,))]
        # A batch adjustment on Certification and Export (grading/batch_adjustments.py) is ONE action of the instructor's, however
        # many reviews it wrote, and it says nothing about false alarms: its reviews count as decisions only.
        batches = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='grades_adjusted' ORDER BY id", (aid,))]
        batched = {entry["review_id"] for batch in batches for entry in batch.get("reviews") or []}
        # Nor is a waiting grade Faculty approved on a plan graded for participation the instructor's work, or a false alarm.
        batched |= {r["id"] for r in reviews if r["actor"] == "automatic" and r["comment"] == participation.APPROVED}
        everything, reviews = reviews, [r for r in reviews if r["id"] not in batched]
        by_group = sum(r["comment"].startswith("Approved with its answer group") for r in reviews)
        automatic = db.execute("""SELECT COUNT(*) FROM frq_all_acceptances a JOIN frq_responses r ON r.id=a.response_id AND r.current_result_id=a.result_id
            WHERE a.assignment_id=? AND a.kind='automatic' AND a.active=1 AND r.current_review_id IS NULL""", (aid,)).fetchone()[0]
        decided = len({r["response_id"] for r in everything}) + automatic
        stamps = sorted(datetime.fromisoformat(r[0]) for r in db.execute("""SELECT created_at FROM frq_events WHERE assignment_id=? AND actor='workspace'
            AND kind IN ('response_reviewed','answer_group_approved','exam_version_confirmed')""", (aid,)))
        # Time at the desk: gaps between consecutive decisions, a gap beyond five minutes counting as a break, not as work.
        minutes = sum(min((b - a).total_seconds(), 300) for a, b in zip(stamps, stamps[1:])) / 60
        actions = len(groups) + len(batches) + len(reviews) - by_group
        def unchanged(r):
            proposed = sorted(a["rubric_item_id"] for a in json.loads(r["proposed"] or "[]") if a["applied"])
            chosen = sorted(a["rubric_item_id"] for a in json.loads(r["applications_json"]) if a["applied"])
            return r["status"] == "approved" and proposed == chosen and r["transcript_json"] == r["read"]
        flagged = [r for r in reviews if r["proposal_state"] and r["proposal_state"] != "proposed"]
        per_question = {}
        for group in groups:
            entry = per_question.setdefault(group["question_id"], {"groups": 0, "responses": 0})
            entry["groups"] += 1
            entry["responses"] += group["approved"]
        share = lambda n: n / decided if decided else None
        return {"responses_decided": decided, "approved_by_group": share(by_group), "approved_individually": share(len({r["response_id"] for r in reviews}) - by_group if decided else 0),
            "accepted_automatically": share(automatic), "instructor_actions": actions, "actions_per_100_responses": 100 * actions / decided if decided else None,
            "minutes_at_the_desk": round(minutes, 1), "minutes_per_100_responses": round(100 * minutes / decided, 1) if decided else None,
            "answer_groups": {q: {**v, "responses_per_group": v["responses"] / v["groups"]} for q, v in per_question.items()},
            # Group purity: members the instructor removed from a group before approving it, and groups whose lines they changed.
            "group_members_removed": sum(g["of"] - g["approved"] for g in groups), "groups_regraded": sum(bool(g["selection_changed"]) for g in groups),
            # False alarms: responses flagged for review that the instructor then approved exactly as proposed.
            "flagged": len(flagged), "false_alarms": sum(unchanged(r) for r in flagged), "false_alarm_rate": sum(unchanged(r) for r in flagged) / len(flagged) if flagged else None,
            "note": "Minutes are estimated from the times of your decisions; they describe this reviewed sample only."}

    def preview_score(self, rid, data):
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_responses WHERE id=?", (rid,)).fetchone()
            if not row:
                raise NotFound("Response not found.")
            version(data, row["version"])
            question = self.question(db, row["assignment_id"], row["question_id"])
            selections = items(data.get("selected_items", []), "Rubric selections")
            return score(question["rubric"]["rules"], [{"rubric_item_id": key, "applied": True} for key in selections],
                         question["content"]["max_points"], data.get("adjustments", []))

    def responses(self, aid, question_id=None):
        with self.rosters.lock(), connect(self.path) as db:
            plan = self.plan(db, aid)
            event, participants = self.manual._context(db, plan["event_id"])
            return self._responses(db, plan, event, participants, question_id)

    def _responses(self, db, plan, event, participants, question_id=None, shared=None, student=None, quality=None):
        """`responses()` on reads the caller already made (Certification and Export, 2026-09-26: one roster payload read per page,
        not one per piece). `participants` are the exam's, each with its `collection` (`manual._context`, or `_participants` then
        `_collection`); `shared` is the per-read memo `source` and `paper_version` keep, with the questions read (`read_questions`) and,
        for every student, the opt-in `prefetch`; `student` (section_id, enrollment_id) reads one student's responses only; `quality`
        is the plan's rubric finalization status when the caller read it in this read. The caller holds the lifecycle lock."""
        aid = plan["id"]
        section, enrollment = student or (None, None)
        members = {(p["section_id"], p["enrollment_id"]): p for p in participants}
        shared = {} if shared is None else shared
        # This read's questions, each read once (performance pass, 2026-10-02): an assignment read already holds every one in
        # `plan["questions"]`, and what this listing reads stays in the memo for `workflow_summary`, which reads every question next.
        questions = shared.setdefault("read_questions", {})
        for question in plan.get("questions") or ():
            questions.setdefault(question["question_id"], question)
        if student is None:
            shared.setdefault("prefetch", {})  # a pass over every paper: their documents and registrations in one read
        disagreements, flagged = {}, {}  # both pure: one answer per distinct input in this listing
        quality = self.rubric_quality_status(db, aid) if quality is None else quality
        # The current result's id stands in the place of its state; the result's own facts come from `result_facts`.
        # The acceptances are those of this plan's current results only: the join reads no other, and the filter goes into every arm
        # of the view on its result index instead of building every plan's acceptances for each listing (performance pass,
        # 2026-10-02: 22 ms to 8 ms for a 2,701-response exam). The rows, and so their pinned order, are the same.
        rows = db.execute("""SELECT r.*,q.question_key,x.id proposal_state,v.status review_status,acc.kind acceptance,acc.blind,acc.audit,acc.active acceptance_active,
            v.rubric_id review_rubric_id,p.rubric_id pinned_rubric_id,rt.question_revision_id reviewed_question_revision,
            p.question_revision_id pinned_question_revision,rt.source_sha256 reviewed_source,
            xt.source_sha256 proposal_source,xt.question_revision_id proposal_revision,xt.rubric_id proposal_rubric
            FROM frq_responses r JOIN frq_questions q ON q.id=r.question_id
            JOIN frq_assignment_questions p ON p.assignment_id=r.assignment_id AND p.question_id=r.question_id
            LEFT JOIN frq_results x ON x.id=r.current_result_id LEFT JOIN frq_reviews v ON v.id=r.current_review_id
            LEFT JOIN (SELECT * FROM frq_all_acceptances WHERE result_id IN (SELECT current_result_id FROM frq_responses WHERE assignment_id=?)) acc
                ON acc.result_id=r.current_result_id
            LEFT JOIN frq_results rx ON rx.id=v.result_id LEFT JOIN frq_tasks rt ON rt.id=rx.task_id
            LEFT JOIN frq_tasks xt ON xt.id=x.task_id
            WHERE r.assignment_id=? AND (? IS NULL OR r.question_id=?) AND (? IS NULL OR (r.section_id=? AND r.enrollment_id=?))
            ORDER BY q.question_key,r.section_id,r.enrollment_id""", (aid, aid, question_id, question_id, section, section, enrollment)).fetchall()
        facts = result_facts(db, str(self.path), [row["proposal_state"] for row in rows])
        result = []
        for row in rows:
            item = dict(row)
            fact = facts.get(item['proposal_state']) or NO_FACTS
            item['proposal_state'] = fact['base_state'] or fact['state']
            compared = (fact['team_decision'], fact['grader_agrees'], fact['initial_grader_agrees'])
            if compared not in disagreements:
                disagreements[compared] = self.selection_disagrees(*compared)
            disagrees = disagreements[compared]
            # A grade counts only if it was made under today's rubric, question and paper; anything else is stale and waits to be
            # graded again (`outdated`).
            review_current = item["review_status"] and item["review_rubric_id"] == item["pinned_rubric_id"] and item["reviewed_question_revision"] == item["pinned_question_revision"]
            proposal_current = item["proposal_state"] and item["proposal_rubric"] == item["pinned_rubric_id"] and item["proposal_revision"] == item["pinned_question_revision"]
            if review_current or proposal_current:
                try:
                    if item["question_id"] not in questions:
                        questions[item["question_id"]] = self.question(db, aid, item["question_id"])
                    participant = members.get((item["section_id"], item["enrollment_id"]))
                    source = participant and digest(self.source(db, plan, questions[item["question_id"]], participant, event, shared))
                except (Conflict, Forbidden, ValueError):
                    source = None
                review_current = review_current and bool(source) and source == item["reviewed_source"]
                proposal_current = proposal_current and bool(source) and source == item["proposal_source"]
            if item["review_status"] and not review_current and proposal_current:
                # Graded again after the rubric, question or paper changed (2026-09-25): the new proposal waits for the instructor.
                # Their earlier decision stays in the history and no longer counts; it is never exported.
                item["earlier_review_status"], item["review_status"] = item["review_status"], None
            stale = not review_current if item["review_status"] else bool(item["proposal_state"]) and not proposal_current
            item["state"] = "stale" if stale else item["review_status"] or item["proposal_state"] or "pending"
            if item["state"] == "proposed" and item["acceptance"]:
                # Never conflated with a proposal or with the instructor's grade: a test, or final under a retained report.
                item["state"] = "trial_accepted" if item["acceptance"] == "trial" else "auto_accepted" if item['acceptance_active'] else 'grading_review'
            flags = None
            if item['proposal_state']:
                selection = (item['question_id'], item['proposal_rubric'], fact['applied'])
                if selection not in flagged:  # read only below, never changed: rows with one selection share one answer
                    applications = [{'rubric_item_id': line, 'applied': True} for line in fact['applied']]  # all `quality_flags` reads
                    flagged[selection] = quality_flags(quality, item['question_id'], item['proposal_rubric'], applications)
                flags = flagged[selection]
            if not stale and not item['review_status'] and item['proposal_state'] and (disagrees or flags['reasons']):
                item['state'] = 'both' if item['proposal_state'] in ('ocr_review', 'both') else 'grading_review'
            elif item['state'] == 'auto_accepted' and not self.same_finalization(db, fact['graded_under'], flags, item['question_id'], item['proposal_rubric']):
                # Final only where the confirmation it was made under gave its question the guidance standing now, as the export says.
                item['state'] = 'grading_review'
            result.append(item)
        return result

    @staticmethod
    def selection_disagrees(team_decision, grader_agrees, initial_grader_agrees):
        """The list's cheap reading of `risk.selection_disagreement`: the same rule the review card and the answer groups apply.

        A recorded team resolution settles a disagreement. Under third-vote-v2/v3 the additional reviewer settles by matching one
        side on every credit-bearing line, so the pair's own comparison still says they differed on a zero-point line; counting
        that pair as a disagreement kept a finalized grade waiting in the list, the queue and the progress while it was exported
        as automatic. Only the committee record and the two comparisons are read from SQL, never the whole receipt.
        """
        flag = lambda value: None if value is None else bool(value)  # SQLite returns JSON booleans as 1/0.
        if team_decision:
            components = {"committee": json.loads(team_decision)}
        else:
            components = {"second": {"agrees": flag(grader_agrees)}, "escalation": {"rubric_disagreement": {"agrees": flag(initial_grader_agrees)}}}
        return bool(risk.selection_disagreement(components))

    def response(self, rid, reveal=False, *, review_details=False):
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_responses WHERE id=?", (rid,)).fetchone()
            if not row:
                raise NotFound("Response not found.")
            value = dict(row)
            question = self.question(db, row["assignment_id"], row["question_id"])
            result = db.execute("SELECT * FROM frq_results WHERE id=?", (row["current_result_id"],)).fetchone()
            review = db.execute("SELECT * FROM frq_reviews WHERE id=?", (row["current_review_id"],)).fetchone()
            value.update(question=question, result=self.decode_result(result) if result else None,
                review=self.decode_result(review) if review else None, crops=[], current=False, error="", hidden=False)
            if result:
                task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (result["task_id"],)).fetchone())
                value["recognition"] = self.step(db, task, "recognizing")
                value["verification"] = self.step(db, task, "reconciling")
                value["crops"] = self.response_crops(db, task, value["verification"])["crops"]
                value["source"] = json.loads(task["source_json"])
                value["task"] = {k: task[k] for k in ("id", "job_id", "rubric_id", "question_revision_id", "source_sha256", "status")}
                value['result'] = risk.apply_disagreement_review(apply_quality(value['result'], self.rubric_quality_status(db, row['assignment_id']), row['question_id'], task['rubric_id']))
                accepted = self.acceptance_of(db, result["id"])
                flags = value['result']['signals'].get('rubric_quality') or {}
                # Final exactly as the listing and the export say: active, no rubric-finalization reason, and made under guidance that
                # still stands for its question (2026-09-25). An active acceptance that is held is not presented as final.
                final = bool(accepted and accepted['active']) and not flags.get('reasons') and self.same_finalization(
                    db, value['result']['signals'].get('scope', {}).get('rubric_quality'), flags, row['question_id'], task['rubric_id'])
                value["acceptance"] = {"id": accepted["id"], "kind": accepted["kind"], "blind": bool(accepted["blind"]), "audit": bool(accepted["audit"]),
                                       "outcome": accepted["outcome"], 'basis': arbitration.basis(value['result']['signals'], accepted['basis']), 'active': bool(accepted['active']),
                                       'final': final} if accepted else None
                blind = bool(accepted and accepted["blind"] and not accepted["outcome"])
                if blind and reveal and not review:
                    # Someone who sees a grade before judging tends to agree with it: a revealed review no longer counts as blind.
                    self.event(db, row["assignment_id"], "blind_revealed", {"response_id": rid, "result_id": result["id"]})
                # The one reason a proposal is hidden: a blind acceptance the instructor has not judged yet.
                value["hidden"] = bool(blind and not review and not reveal)
                try:
                    self.validate_task(db, task)
                    value["current"] = task["status"] == "complete"
                except (Conflict, Forbidden, ValueError) as error:
                    value["error"] = getattr(error, "description", str(error))
                if review and value["current"] and review["result_id"] != result["id"]:
                    # A decision made under an earlier rubric, question or paper gives way to a proposal graded under today's
                    # (2026-09-25). It stays in the history below; the instructor decides the new proposal.
                    try:
                        self.validate_task(db, dict(db.execute("SELECT t.* FROM frq_results x JOIN frq_tasks t ON t.id=x.task_id WHERE x.id=?", (review["result_id"],)).fetchone()))
                    except (Conflict, Forbidden, NotFound, ValueError):
                        value["earlier_review"], value["review"] = value["review"], None
                if value["hidden"]:
                    value["result"] = {k: v for k, v in value["result"].items() if k not in ("applications", "score", "interpretation", "signals", "reasons")}
                elif review_details:
                    # GRADING/PERFORMANCE, opt-in like the queue's `reason_kinds` (requirement, 2026-10-11, one line per cause on the
                    # review card): the reasons the desk lists for this very result, gathered by cause. Display only. Never for a
                    # hidden proposal, whose reasons say which lines the graders weighed; the default body is unchanged.
                    from .review_queue import reason_causes  # It imports this module.
                    value["reason_causes"] = reason_causes(value["result"], (question.get("rubric") or {}).get("rules"), question["content"].get("max_points"))
            value["history"] = [self.decode_result(r) for r in db.execute("SELECT * FROM frq_reviews WHERE response_id=? ORDER BY rowid DESC", (rid,))]
            # What the flag review left with the instructor about this very proposal: the mark to look at and, when it named them, the
            # grade each reading gives (one click each in the desk). Absent otherwise, so other responses read as before.
            if result and question.get("rubric") and not value["hidden"] and not review:
                note = self.flag_review_note(db, row["assignment_id"], rid, result["id"], question["rubric"]["rules"], question["content"]["max_points"])
                if note:
                    value["flag_review"] = note
        return value

    def save_review(self, rid, data):
        with self.rosters.lock(), connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_responses WHERE id=?", (rid,)).fetchone()
            if not row:
                raise NotFound("Response not found.")
            version(data, row["version"])
            if data.get("result_id") != row["current_result_id"] or not row["current_result_id"]:
                raise Conflict("Inspect the current completed proposal before recording a decision.")
            result = db.execute("SELECT * FROM frq_results WHERE id=?", (row["current_result_id"],)).fetchone()
            task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (result["task_id"],)).fetchone())
            if task["status"] != "complete":
                raise Conflict("Failed or unfinished tasks cannot produce approved grades.")
            plan, question, participant = self.validate_task(db, task)
            status = data.get("status")
            if status not in ("draft", "approved", "manual", "unreadable", "rejected"):
                raise ValueError("Choose a review action.")
            current_transcript = transcript(data.get("transcript", json.loads(result["transcript_json"])))
            selections = items(data.get("selected_items", []), "Rubric selections")
            if any(not isinstance(selection, str) for selection in selections):
                raise ValueError("Choose rubric item identifiers.")
            if len(set(selections)) != len(selections):
                raise ValueError("Rubric selections cannot repeat.")
            rules = question["rubric"]["rules"]
            ids = {i["id"] for i in rules["items"]}
            if not set(selections) <= ids:
                raise ValueError("Unknown rubric item selected.")
            comment = text(data.get("comment", ""), "Review comment", 20000)
            proposed = {a["rubric_item_id"]: a for a in json.loads(result["applications_json"])}
            unchanged_text = current_transcript == json.loads(result["transcript_json"])
            applications = []
            for item in rules["items"]:
                prior = proposed.get(item["id"], {})
                retained = unchanged_text and prior.get("applied") == (item["id"] in selections)
                applications.append({"rubric_item_id": item["id"], "applied": item["id"] in selections,
                    "reason": prior.get("reason", "Instructor selection") if retained else "Instructor selection",
                    "evidence": prior.get("evidence", []) if retained else [], "actor": "workspace",
                    "origin_error_roots": prior.get("origin_error_roots", []) if retained else [],
                    "origin_error_id": prior.get("origin_error_id") if retained else None})
            adjustments = data.get("adjustments", [])
            numeric = None if status in ("unreadable", "rejected") else score(rules, applications, question["content"]["max_points"], adjustments)
            if status == "manual":
                manual = number(data.get("manual_score"), "Manual score", rules["floor"], rules["ceiling"])
                text(comment, "Manual grade reason", 20000, False)
                # The adjustment is added BEFORE the rubric's floor/ceiling, so it is measured from the unclamped sum: F(8) with S(2)
                # is 10 before the ceiling makes it 8, and a manual 6 needs -4, not -2. Exact decimals, so 7.3 to 6.1 is -1.2.
                from decimal import Decimal
                needed = float(Decimal(str(manual)) - Decimal(str(numeric["unclamped_score"])))
                if abs(needed) > question["content"]["max_points"]:
                    raise ValueError(f"The selected rubric lines add up to {numeric['unclamped_score']:g} before the point bounds, too far from a manual score of {manual:g} "
                                     "for one adjustment. Untick the lines that do not apply, then set the manual score.")
                adjustments = [*numeric["adjustments"], {"points": needed, "reason": comment}]
                numeric = score(rules, applications, question["content"]["max_points"], adjustments)
                if numeric["score"] != manual:
                    raise ValueError("Manual score conflicts with the approved scoring bounds.")
            if status in ("approved", "manual") and data.get("confirm_review") is not True:
                raise ValueError("Confirm that you inspected the response and rubric selections.")
            if status in ("unreadable", "rejected"):
                numeric = None
            review_id = uid()
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO frq_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (review_id, rid, result["id"], task["rubric_id"],
                task["source_sha256"], encoded(current_transcript), encoded(applications), encoded(numeric), status, comment, "workspace", timestamp()))
            db.execute("UPDATE frq_responses SET current_review_id=?,version=version+1 WHERE id=?", (review_id, rid))
            quality = quality_flags(self.rubric_quality_status(db, row['assignment_id']), row['question_id'], task['rubric_id'], applications)
            if quality['enabled']:
                self.event(db, row['assignment_id'], 'rubric_quality_manual_review', {'review_id': review_id, 'quality': quality})
            # The instructor's look at an accepted response IS the measurement: confirmed or overturned, blind or seen.
            # Both writers of blind_revealed record the response's own plan, so the plan's events index finds them without a scan.
            revealed = db.execute("SELECT 1 FROM frq_events WHERE assignment_id=? AND kind='blind_revealed' AND json_extract(detail_json,'$.result_id')=?",
                                  (row["assignment_id"], result["id"])).fetchone()
            self.record_acceptance_review(db, result, review_id, status, selections, numeric["adjustments"] if numeric else [], blind_kept=not revealed)
            if status != "draft":
                # Corrections are future evaluation and training data: kept BESIDE the AI's own output, which is never overwritten.
                before = {a["rubric_item_id"] for a in json.loads(result["applications_json"]) if a["applied"]}
                db.execute("INSERT INTO frq_review_feedback VALUES(?,?,?,?,?,?,?)", (review_id, int(data.get("reasoning_wrong") is True), comment, int(not unchanged_text),
                    encoded({"added": sorted(set(selections) - before), "removed": sorted(before - set(selections))}),
                    encoded({"result_id": result["id"], "task_id": task["id"], "job_id": task["job_id"], "rubric_id": task["rubric_id"], "question_revision_id": task["question_revision_id"],
                             "scope_sha256": json.loads(result["signals_json"]).get("scope_sha256"), "models": json.loads(result["signals_json"]).get("models")}), timestamp()))
            self.event(db, plan["id"], "response_reviewed", {"response_id": rid, "review_id": review_id, "result_id": result["id"], "status": status})
        return self.response(rid)

    def approve_many(self, aid, data):
        """Prevalidate a set of explicit approvals; retain each decision's receipt."""
        commands = items(data.get("responses"), "Responses to approve", 100)
        if not commands or data.get("confirm_review") is not True:
            raise ValueError("Select and inspect the proposed responses before bulk approval.")
        selected = set()
        for command in commands:
            if not isinstance(command, dict) or not isinstance(command.get("id"), str):
                raise ValueError("Choose responses by their identifiers.")
            if command["id"] in selected:
                raise ValueError("Choose each response at most once.")
            selected.add(command["id"])
        # Prevalidate every response while the lifecycle lock prevents mutation.
        with self.rosters.lock():
            reviews = []
            for command in commands:
                value = self.response(command.get("id"))
                if value["assignment_id"] != aid or not value["current"] or value["hidden"] or value["review"] or value["result"].get("state") != "proposed":
                    raise Conflict("Bulk approval accepts only current, high-confidence, unreviewed proposals that are not waiting for a blind review.")
                version(command, value["version"])
                reviews.append((value, command))
            # Each saved decision has its own durable audit. A failed write is
            # reported and prior explicit approvals remain inspectable.
            return [self.save_review(v["id"], {"version": v["version"], "result_id": v["current_result_id"],
                "status": "approved", "confirm_review": True, "selected_items": [a["rubric_item_id"] for a in v["result"]["applications"] if a["applied"]]}) for v, _ in reviews]

    def audit_steps(self, db, task, seen=None):
        seen = set() if seen is None else seen
        if task["id"] in seen:
            raise ValueError("Grading provenance contains a circular dependency.")
        seen.add(task["id"])
        prior_steps = []
        if task.get("reuse_result_id"):
            prior = db.execute("SELECT t.* FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id WHERE r.id=?", (task["reuse_result_id"],)).fetchone()
            prior_steps = self.audit_steps(db, dict(prior), seen) if prior else []
        return prior_steps + [self.decode_result(r) for r in db.execute("SELECT * FROM frq_steps WHERE task_id=? ORDER BY rowid", (task["id"],))] + [
            {"rejected": True, **self.decode_result(r)} for r in db.execute("SELECT * FROM frq_rejected_outputs WHERE task_id=? ORDER BY rowid", (task["id"],))]

    def final_decisions(self, db, aid, response_id=None, question_id=None, section_id=None):
        """(plan, decisions, skipped): the ONE rule for which grades are final, shared by every export and by Sending grades to
        Gradescope (`gradescope_sync`), so what is sent is exactly what an export would say. Final is an instructor review marked
        approved or manual whose task completed, or an ACTIVE automatic acceptance that no review replaced and that the current
        rubric finalization still covers; each is checked again with `validate_task` (a stale grade is never final). Every other
        grade that looked final is in `skipped` with its reason. Reads the catalog only; the caller holds the lifecycle lock.
        Each decision: {kind review|automatic, row, task, question, participant, final[, proposal]}. With `response_id`, the same
        rule for that one response (a sender checks a grade is still final just before writing it); with `question_id` (and
        `section_id`), for one question's responses (in one section): a batch adjustment reads only what it may change."""
        plan = self.plan(db, aid)
        only, args = "", [aid]
        for column, value in (("r.id", response_id), ("r.question_id", question_id), ("r.section_id", section_id)):
            if value:
                only, args = f"{only} AND {column}=?", [*args, value]
        rows = db.execute(f"""SELECT r.*,v.id review_id,v.result_id FROM frq_responses r
            JOIN frq_reviews v ON v.id=r.current_review_id WHERE r.assignment_id=? {only} AND v.status IN ('approved','manual') ORDER BY r.question_id,r.section_id,r.enrollment_id""", args).fetchall()
        # One memo for this read (it writes nothing): each plan, question, location status, event and participant is read once for
        # every grade that shares it, and every grade's own checks still run (`validate_task`).
        decisions, skipped, status, memo = [], [], None, {}
        for row in rows:
            task = dict(db.execute("SELECT t.* FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id WHERE r.id=?", (row["result_id"],)).fetchone())
            try:
                _, question, participant = self.validate_task(db, task, memo=memo)
                if task["status"] != "complete":
                    raise Conflict("The source grading task did not complete.")
            except (ValueError, Conflict, Forbidden) as error:
                skipped.append({"response_id": row["id"], "reason": getattr(error, "description", str(error))})
                continue
            review = self.decode_result(db.execute("SELECT * FROM frq_reviews WHERE id=?", (row["review_id"],)).fetchone())
            decisions.append({"kind": "review", "row": row, "task": task, "question": question, "participant": participant, "final": review})
        # Automatically accepted scores identify their basis: team agreement or an enabled benchmark permission.
        # A trial acceptance is a test and is never exported; a suspended or revoked permission withholds its unreviewed scores.
        # For one response the join also names its current result as a constant, which SQLite pushes into every arm of the view
        # instead of reading every acceptance (the join and r.id=? already imply it; its placeholder comes before WHERE's).
        pin = " AND a.result_id=(SELECT current_result_id FROM frq_responses WHERE id=?)" if response_id else ""
        for row in db.execute(f"""SELECT r.*,a.id acceptance_id,a.result_id,a.permission_id,a.basis,a.active,a.created_at accepted_at FROM frq_responses r
                JOIN frq_all_acceptances a ON a.result_id=r.current_result_id AND a.kind='automatic'{pin}
                WHERE r.assignment_id=? {only} AND r.current_review_id IS NULL ORDER BY r.question_id,r.section_id,r.enrollment_id""",
                ([response_id] if response_id else []) + args).fetchall():
            task = dict(db.execute("SELECT t.* FROM frq_results r JOIN frq_tasks t ON t.id=r.task_id WHERE r.id=?", (row["result_id"],)).fetchone())
            try:
                if not row['active']:
                    raise Conflict("Automatic acceptance for this question is suspended; this score waits for your review.")
                _, question, participant = self.validate_task(db, task, memo=memo)
            except (ValueError, Conflict, Forbidden) as error:
                skipped.append({"response_id": row["id"], "reason": getattr(error, "description", str(error))})
                continue
            proposal = self.decode_result(db.execute("SELECT * FROM frq_results WHERE id=?", (row["result_id"],)).fetchone())
            status = status if status is not None else self.rubric_quality_status(db, aid)  # the same for every row of this read
            quality = quality_flags(status, row['question_id'], task['rubric_id'], proposal['applications'])
            # Made under an earlier confirmation: final only where it gave this question the guidance that stands now (2026-09-25).
            if quality['reasons'] or not self.same_finalization(db, proposal['signals'].get('scope', {}).get('rubric_quality'), quality, row['question_id'], task['rubric_id']):
                skipped.append({'response_id': row['id'], 'reason': 'The current rubric quality policy requires instructor review.'})
                continue
            basis = arbitration.basis(proposal['signals'], row['basis'])
            final = {"id": row["acceptance_id"], "status": "automatic", "actor": "acceptance-policy", "transcript": proposal["transcript"], "applications": proposal["applications"],
                         "score": proposal["score"], 'basis': basis,
                         "comment": "Finalized automatically by the final arbitrator's ruling on a flagged response, with every flag it was shown resolved; not looked at by the instructor." if basis == 'final_arbitrator'
                         else "Finalized automatically as left blank: the reader found every answer area empty and Jev, looking at the same areas, said the problem was left blank; not looked at by the instructor." if basis == 'jev_blank'
                         else "Finalized automatically after the flag reviewer upheld the unchanged standing grade and resolved every shown flag; not looked at by the instructor." if basis == 'flag_review'
                         else "Finalized automatically after Jev broke the grading team's tie: its answer for every rubric line any model ticked equals one complete assessment, and all checks passed; not looked at by the instructor." if basis == 'jev_tiebreak'
                         else "Finalized automatically after the multiple-choice reader: it read the marked option in both option orders, the question is multiple choice by its rubric, and all applicable checks passed; not looked at by the instructor." if basis == 'choice_reader'
                         else "Finalized automatically after flag review: the flag review of the question ruled on every flag it was shown, and all checks passed; not looked at by the instructor." if basis == 'flag_resolution'
                         else f"Finalized automatically by the One-shot model assigned through Jev Routing (difficulty {proposal['signals']['jev_routing']['difficulty']}, {proposal['signals']['jev_routing']['endpoint']}) after applicable checks passed; not looked at by the instructor." if proposal['signals'].get('jev_routing')
                         else "Finalized automatically by the One-shot model after applicable checks passed; not looked at by the instructor." if row['basis'] == 'one_shot'
                         else "Finalized automatically by Tag-Team: one model graded the images and the second reviewed its assessment; all applicable checks passed. Not looked at by the instructor." if row['basis'] == 'tag_team'
                         else "Finalized automatically after grading-team agreement and passing checks; not looked at by the instructor." if basis == 'team_consensus' else "Accepted automatically under a retained benchmark report; not looked at by the instructor.", "created_at": row["accepted_at"]}
            decisions.append({"kind": "automatic", "row": row, "task": task, "question": question, "participant": participant, "final": final, "proposal": proposal})
        return plan, decisions, skipped

    def export(self, aid, kind="audit"):
        if kind not in ("audit", "csv", "totals", "dataset", "entry"):
            raise ValueError("Choose CSV, audit, dataset or Gradescope entry output.")
        with self.rosters.lock(), connect(self.path) as db:
            # GRADING "GRADED FOR PARTICIPATION": the export says what each grade counts as.
            plan, decisions, skipped = participation.counted_decisions(db, aid, self.final_decisions(db, aid))
            # Names from the verified term roster, read ONCE for the export under the lock this export holds; the per-response
            # check (`validate_task`) reads the catalog alone and carries no name.
            names = {(p["section_id"], p["enrollment_id"]): p["name"] for p in self.exams._participants(db, self.exams._event(db, plan["event_id"]))}
            records = []
            for decision in decisions:
                row, task, question, participant, final = (decision[k] for k in ("row", "task", "question", "participant", "final"))
                if decision["kind"] == "review":
                    noted = db.execute("SELECT reasoning_wrong,override_reason,transcript_corrected,lines_changed_json FROM frq_review_feedback WHERE review_id=?", (row["review_id"],)).fetchone()
                    final["feedback"] = dict(noted) if noted else None
                    proposal = self.decode_result(db.execute("SELECT * FROM frq_results WHERE id=?", (row["result_id"],)).fetchone())
                else:
                    proposal = decision["proposal"]
                # A total needs every question of the student's printed version AND exam version; another version's questions are not theirs.
                paper = self.paper_version(db, plan, participant)["version"] or ""
                relevant_questions = [r[0] for r in db.execute("""SELECT q.question_key FROM frq_assignment_questions p
                    JOIN frq_questions q ON q.id=p.question_id WHERE p.assignment_id=? AND q.assignment_version_id=? AND q.exam_version IN ('',?)""",
                    (aid, question["assignment_version_id"], paper))]
                records.append({"response_id": row["id"], "section_id": row["section_id"], "enrollment_id": row["enrollment_id"],
                    "name": names.get((row["section_id"], row["enrollment_id"]), ""), "question_key": question["question_key"], "exam_version": question["exam_version"], "question": question["content"],
                    "predictions": self.comparisons(db, row["id"], task["source_sha256"], task["rubric_id"]),
                    "rubric": question["rubric"], "final": final, "proposal": proposal, "source": json.loads(task["source_json"]),
                    "crops": self.response_crops(db, task)["crops"], "steps": self.audit_steps(db, task), "task": task, "expected_questions": relevant_questions})
            if not records:
                raise Conflict("There are no current approved responses to export.")
            snapshot = {"assignment_id": aid, "title": plan["title"], "records": records, "skipped": skipped,
                "created_at": timestamp(), "publication": "Exported locally; never published externally."}
            export_id = uid()
            text = encoded(snapshot)  # encoded once: its digest is the digest of these very bytes
            db.execute("INSERT INTO frq_exports VALUES(?,?,?,?,?,?)", (export_id, aid, text, digest(text.encode()), kind, timestamp()))
            self.event(db, aid, "exported", {"export_id": export_id, "kind": kind, "responses": len(records), "skipped": len(skipped)})
        snapshot["export_id"] = export_id
        if kind == "csv":
            return GradescopeAdapter().csv(snapshot).encode(), "text/csv", "approved-grades.csv"
        if kind == "totals":
            return GradescopeAdapter().totals_csv(snapshot).encode(), "text/csv", "assignment-totals.csv"
        if kind == "dataset":
            from .benchmark import enrollment_split
            stream = tempfile.TemporaryFile(prefix="faculty-grading-export-")
            try:
                with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                    dataset = []
                    for record in records:
                        if record["final"]["status"] == "automatic":
                            continue  # Nobody looked at it: it is not a label, and must never become evaluation or training truth.
                        images = []
                        for index, crop in enumerate(record["crops"]):
                            path = f"images/{record['response_id']}-{index}.png"
                            archive.writestr(path, storage.read(self.inbox, crop["images"]["original"]))
                            images.append(path)
                        signals = record["proposal"].get("signals") or {}
                        verdict = signals.get("risk") or {}
                        dataset.append({"response_id": record["response_id"], "images": images, "question": record["question"],
                            # Opaque and stable within this export only: every response of one student shares it, and no two exports can be joined by it.
                            "student": digest(f"{export_id}:{record['section_id']}:{record['enrollment_id']}")[:16], "exam_version": record.get("exam_version", ""),
                            # The split is the STUDENT's in every export (`benchmark.enrollment_split`): derived from the permanent enrollment
                            # ID, never from this export, so a model trained on one export's train split is never tested on its own students.
                            "split": enrollment_split(record["enrollment_id"]),
                            "would_accept": record["proposal"].get("state") == "proposed" and not verdict.get("blocks") and not verdict.get("individual") and bool(verdict),
                            "ocr_disagreements": (verdict.get("components") or {}).get("ocr", {}).get("disagreements"),
                            "unregistered": any(not crop["metadata"]["registered"] for crop in record["crops"]),
                            # Enough to reconstruct a correction: readings, AI output, risk features, models, prompts, and what the instructor changed and why.
                            "provenance": {"ocr": next((s["output"]["candidates"] for s in record["steps"] if s.get("stage") == "recognizing" and not s.get("rejected")), []),
                                "models": signals.get("models"), "scope_sha256": signals.get("scope_sha256"), "risk": verdict, "second": signals.get("second"), "verifier": signals.get("verifier"),
                                "prompts": {s["stage"]: s["provider"].get("prompt") for s in record["steps"] if not s.get("rejected") and isinstance(s.get("provider"), dict) and s["provider"].get("prompt")},
                                "software": SOFTWARE, "feedback": record["final"].get("feedback")},
                            "predictions": record["predictions"],
                            "rubric": record["rubric"], "transcript": record["final"]["transcript"], "proposal": record["proposal"],
                            "final": {k: record["final"][k] for k in ("transcript", "applications", "score", "comment")}})
                    archive.writestr("dataset.json", encoded({"format": "faculty-frq-dataset-v1", "records": dataset,
                        "note": "Local evaluation dataset; handwriting may contain identifying information. No training has been performed. Use each record's `split`, which keeps a student in one split across exports; never split by response."}))
                stream.seek(0)
                return stream, "application/zip", "grading-dataset.zip"
            except BaseException:
                stream.close()
                raise
        if kind == "entry":
            return encoded(GradescopeAdapter().entry(snapshot)).encode(), "application/json", "gradescope-entry.json"
        return encoded(snapshot).encode(), "application/json", "grading-audit.json"
