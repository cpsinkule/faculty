"""Trial acceptance, the instructor's confirm/overturn review with its blind share, the benchmark report and the gate.

Real catalog, real job runner, the local fake host. The four states are never conflated: proposed, trial-accepted
(never final, never exported), automatically accepted (only under a retained report for the exact scope) and
instructor-confirmed.
"""
import json
import unittest
import uuid
import zipfile
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from rosters import connect
import test_grading_pipeline as pipeline
from test_grading_pipeline import BARE, CORRECT, INCONSISTENT
from frq_fixtures import finalize_rubrics


class AcceptanceCase(pipeline.Pipeline):
    def configure_acceptance(self, **settings):
        current = self.frq.settings()
        return self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], **settings}})

    def students(self, count):
        """More reviewed papers, so a class-sized sample exists."""
        with connect(self.path) as db:
            others = [r[0] for r in db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? ORDER BY enrollment_id", (self.section["id"], self.enrollment))]
        for index, enrollment in enumerate(others[:count - 1]):
            self.reviewed_source(batch=self.ready_batch(enrollment_id=enrollment), changed=f"student-{index}")
        return 1 + len(others[:count - 1])

    def acceptances(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("SELECT a.*,r.outcome FROM frq_acceptances a LEFT JOIN frq_acceptance_reviews r ON r.acceptance_id=a.id ORDER BY a.rowid")]

    def review(self, response_id, items, status="approved", **extra):
        value = self.frq.response(response_id)
        return self.frq.save_review(response_id, {"version": value["version"], "result_id": value["current_result_id"], "status": status, "confirm_review": True,
                                                  "selected_items": list(items), **extra})


class TrialAcceptance(AcceptanceCase):
    def test_trial_acceptance_needs_no_report_is_never_final_and_is_never_exported(self):
        response, _ = self.grade(CORRECT)
        accepted = self.acceptances()
        self.assertEqual([(a["kind"], a["outcome"], a["permission_id"]) for a in accepted], [("trial", None, None)])  # No gate, no report, no permission.
        listed = self.frq.responses(self.plan["id"])[0]
        self.assertEqual((listed["state"], listed["acceptance"]), ("trial_accepted", "trial"))
        self.assertEqual(self.frq.response(response["id"])["acceptance"]["kind"], "trial")
        with self.assertRaisesRegex(Conflict, "no current approved responses"):
            self.frq.export(self.plan["id"], "csv")  # A trial-accepted score is not a grade: nothing to export.
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_reviews").fetchone()[0], 0)  # And it wrote no instructor decision.
        self.assertEqual(self.frq.overview()["counts"].get("trial_accepted"), 1)

    def test_only_what_the_whole_policy_passed_is_accepted(self):
        for lines, chosen in ((INCONSISTENT, ("R1",)), (BARE, ("R1",)), (CORRECT, ("R2",))):
            with self.subTest(lines=lines):
                self.grade(lines, primary=chosen)
                self.assertEqual(self.acceptances(), [])  # Contradicted proposals or too little required work stay unaccepted.
                with connect(self.path) as db:
                    db.execute("UPDATE frq_tasks SET status='complete' WHERE status!='complete'")
        self.configure_acceptance(trial_acceptance=False)
        self.grade(CORRECT)
        self.assertEqual(self.acceptances(), [])
        self.assertEqual(self.frq.responses(self.plan["id"])[0]["state"], "proposed")

    def test_a_request_that_still_asks_for_shadow_mode_is_graded_like_any_other(self):
        """Shadow mode was removed on 2026-09-20. A tab opened before that may still send the flag, and saved settings still carry it."""
        current = self.frq.settings()
        self.assertNotIn("shadow", current["settings"])
        with connect(self.path) as db:  # Exactly what an earlier version saved.
            db.execute("UPDATE frq_settings SET settings_json=json_set(settings_json,'$.shadow',json('true')) WHERE id=1")
        self.assertNotIn("shadow", self.frq.settings()["settings"])  # It still loads, without the retired preference.
        self.configure_acceptance(blind_share=0.0)
        response, _ = self.grade(CORRECT)
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "response_ids": [response["id"]], "mode": "cloud_standard", "operation": "grade", "shadow": True}
        # This fixture's team predates Cloud Standard's required reviewers; what is tested is only the retired flag.
        with patch("grading.standard.freeze"):
            command["fingerprint"] = self.frq.preview(self.plan["id"], command)["fingerprint"]
            job = self.frq.create_job(self.plan["id"], command)
        self.run_all()
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT shadow FROM frq_jobs WHERE id=?", (job["id"],)).fetchone()[0], 0)
        shown = self.frq.response(response["id"])
        self.assertFalse(shown["hidden"])
        self.assertIn("applications", shown["result"])  # Nothing hides a proposal but a blind review.

    def test_switching_confidence_checks_off_is_part_of_the_scope_and_leaving_them_on_changes_no_scope(self):
        response, _ = self.grade(CORRECT)
        with connect(self.path) as db:
            task = dict(db.execute("SELECT * FROM frq_tasks WHERE id=?", (response["task"]["id"],)).fetchone())
            question = self.frq.question(db, self.plan["id"], self.qid)
        job = self.frq.job(task["job_id"])
        recorded = response["result"]["signals"]
        on, sha_on = self.frq.acceptance_scope(task, job, job["profile"], question)
        self.assertEqual((sha_on, "confidence_checks" in on["risk"]), (recorded["scope_sha256"], False))  # Exactly the scope it always was.
        before = {k: v for k, v in job["profile"]["risk"].items() if k != "confidence_checks"}  # A batch frozen before the switch existed.
        self.assertEqual(self.frq.acceptance_scope(task, job, {**job["profile"], "risk": before}, question)[1], sha_on)
        off, sha_off = self.frq.acceptance_scope(task, job, {**job["profile"], "risk": {**job["profile"]["risk"], "confidence_checks": False}}, question)
        self.assertEqual((off["risk"]["confidence_checks"], sha_off != sha_on), (False, True))  # Another policy: an earlier permission does not carry over.

    def test_the_instructors_review_confirms_or_overturns_and_that_record_is_the_report(self):
        self.configure_acceptance(blind_share=0.0)
        response, _ = self.grade(CORRECT)
        self.review(response["id"], ["R1"])
        self.assertEqual([a["outcome"] for a in self.acceptances()], ["confirmed"])
        report = self.frq.acceptance_report(self.plan["id"], self.qid)["reports"][0]
        self.assertEqual((report["accepted"], report["all"]["reviewed"], report["all"]["overturned"], report["seen"]["reviewed"], report["blind"]["reviewed"]), (1, 1, 0, 1, 0))
        self.assertEqual(self.frq.responses(self.plan["id"])[0]["state"], "approved")  # Now it IS the instructor's grade, and exportable.
        self.assertIn(b"Question", self.frq.export(self.plan["id"], "csv")[0])

    def test_an_overturned_acceptance_keeps_the_corrected_lines_and_names_the_signal_that_should_have_caught_it(self):
        self.configure_acceptance(blind_share=0.0)
        response, _ = self.grade(CORRECT)
        reviewed = self.review(response["id"], ["R2"], comment="The 6 is an 8.", reasoning_wrong=True)
        self.assertEqual([a["outcome"] for a in self.acceptances()], ["overturned"])
        self.assertEqual(sorted(a["rubric_item_id"] for a in reviewed["review"]["applications"] if a["applied"]), ["R2"])  # The corrected lines, in the ordinary review.
        self.assertEqual(sorted(a["rubric_item_id"] for a in reviewed["result"]["applications"] if a["applied"]), ["R1"])  # The AI's output is preserved beside it.
        report = self.frq.acceptance_report(self.plan["id"], self.qid)["reports"][0]
        self.assertEqual((report["all"]["overturned"], report["all"]["error_rate"]), (1, 1.0))
        self.assertEqual(report["overturned"][0]["response_id"], response["id"])
        self.assertTrue(any("adversarial reviewer" in line for line in report["overturned"][0]["should_have_caught"]))
        with connect(self.path) as db:
            feedback = dict(db.execute("SELECT * FROM frq_review_feedback").fetchone())
        self.assertEqual((feedback["reasoning_wrong"], feedback["override_reason"]), (1, "The 6 is an 8."))


class BlindShare(AcceptanceCase):
    def test_a_blind_acceptance_hides_the_ai_decision_until_the_instructor_has_graded_and_the_gate_uses_that_share(self):
        self.configure_acceptance(blind_share=1.0)
        response, _ = self.grade(CORRECT)
        hidden = self.frq.response(response["id"])
        self.assertTrue(hidden["hidden"] and hidden["acceptance"]["blind"])
        for secret in ("applications", "score", "signals", "interpretation", "reasons"):
            self.assertNotIn(secret, hidden["result"])
        self.assertEqual(self.frq.answer_groups(self.plan["id"], self.qid)["groups"], [])  # A contact sheet would show the proposal, so blind ones are reviewed alone.
        self.assertEqual(self.frq.acceptance_queue(self.plan["id"])["blind"], 1)
        self.review(response["id"], ["R1"])
        report = self.frq.acceptance_report(self.plan["id"], self.qid)["reports"][0]
        self.assertEqual((report["blind"]["reviewed"], report["blind"]["overturned"], report["seen"]["reviewed"]), (1, 0, 0))
        self.assertAlmostEqual(report["blind"]["upper_bound_95"], .95)  # One clean review proves almost nothing, and the report says so.
        self.assertEqual(report["blind"]["more_reviews_needed"], 298)
        self.assertFalse(report["blind"]["eligible"])

    def test_looking_at_the_proposal_first_makes_the_review_count_as_seen_not_blind(self):
        self.configure_acceptance(blind_share=1.0)
        response, _ = self.grade(CORRECT)
        self.assertIn("applications", self.frq.response(response["id"], reveal=True)["result"])
        self.review(response["id"], ["R1"])
        report = self.frq.acceptance_report(self.plan["id"], self.qid)["reports"][0]
        self.assertEqual((report["blind"]["reviewed"], report["seen"]["reviewed"]), (0, 1))


class TheGate(AcceptanceCase):
    def evidence(self, clean, overturned=0):
        """Blind reviews as retained records of one scope: the statistics are about counts, so the fixture writes counts."""
        response, _ = self.grade(CORRECT)
        first = self.acceptances()[0]
        with connect(self.path) as db:
            db.execute("UPDATE frq_tasks SET status='complete' WHERE status!='complete'")
            dict(db.execute("SELECT * FROM frq_results WHERE id=?", (first["result_id"],)).fetchone())
        self.review(response["id"], ["R1"])
        review_id = self.frq.response(response["id"])["review"]["id"]
        with connect(self.path) as db:
            db.execute("DROP TRIGGER IF EXISTS frq_acceptance_result_scope")
            db.execute("DROP INDEX IF EXISTS frq_acceptances_result")
            for index in range(clean + overturned - 1):
                identifier = str(uuid.uuid4())
                db.execute("INSERT INTO frq_acceptances VALUES(?,?,?,?,?,?,?,?,?,1,0,NULL,?)", (identifier, f"fixture-result-{index}", first["response_id"], first["assignment_id"], first["question_id"],
                    "trial", first["scope_sha256"], first["scope_json"], first["policy"], first["created_at"]))
                db.execute("INSERT INTO frq_acceptance_reviews VALUES(?,?,?,?,1,'[]',?)", (str(uuid.uuid4()), identifier, review_id, "overturned" if index < overturned else "confirmed", first["created_at"]))
        return first["scope_sha256"]

    def test_real_automatic_acceptance_cannot_be_enabled_without_the_report_or_with_too_little_evidence(self):
        self.configure_acceptance(blind_share=1.0)
        with self.assertRaisesRegex(Conflict, "No benchmark report exists"):
            self.frq.enable_acceptance(self.plan["id"], self.qid, {"scope_sha256": "0" * 64, "confirm_automatic_acceptance": True})
        scope = self.evidence(clean=30)
        with self.assertRaisesRegex(Conflict, "0 overturned in 30 blind reviews puts the upper 95% bound of the error rate at 9.5%.*269 more"):
            self.frq.enable_acceptance(self.plan["id"], self.qid, {"scope_sha256": scope, "confirm_automatic_acceptance": True})
        with self.assertRaises(ValueError):
            self.frq.enable_acceptance(self.plan["id"], self.qid, {"scope_sha256": scope})  # Never without the explicit confirmation.
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_acceptance_permissions").fetchone()[0], 0)

    def test_with_enough_blind_evidence_acceptance_is_scoped_audited_distinguishable_and_revoked_when_its_inputs_change(self):
        self.configure_acceptance(blind_share=1.0, audit_share=1.0)
        scope = self.evidence(clean=300)
        enabled = self.frq.enable_acceptance(self.plan["id"], self.qid, {"scope_sha256": scope, "confirm_automatic_acceptance": True})["reports"][0]
        self.assertEqual((enabled["permission"]["status"], enabled["blind"]["eligible"]), ("enabled", True))
        with connect(self.path) as db:
            retained = dict(db.execute("SELECT * FROM frq_benchmark_reports").fetchone())
        self.assertEqual((retained["scope_sha256"], json.loads(retained["report_json"])["blind"]["reviewed"]), (scope, 300))
        # The next response of the same scope is final without a look, says so everywhere, and is sampled for audit.
        self.reviewed_source(batch=self.ready_batch(enrollment_id=self.other_student()), changed="second")
        self.host.requests.clear()
        self.queue()
        self.run_all()
        automatic = [a for a in self.acceptances() if a["kind"] == "automatic"]
        self.assertEqual([(a["audit"], a["blind"], bool(a["permission_id"])) for a in automatic], [(1, 0, True)])
        states = {r["enrollment_id"]: r["state"] for r in self.frq.responses(self.plan["id"])}
        self.assertEqual(states[self.other_student()], "auto_accepted")
        exported = json.loads(self.frq.export(self.plan["id"], "audit")[0])["records"]
        self.assertEqual(sorted(r["final"]["status"] for r in exported), ["approved", "automatic"])  # Distinguishable from instructor-confirmed grading.
        # GRADING: automatically accepted responses are left out of the dataset, and so are their images (nothing in the zip is unreferenced).
        stream = self.frq.export(self.plan["id"], "dataset")[0]
        with stream, zipfile.ZipFile(stream) as archive:
            kept = json.loads(archive.read("dataset.json"))["records"]
            images = sorted(n for n in archive.namelist() if n.startswith("images/"))
        self.assertEqual(len(kept), 1)
        self.assertTrue(images)
        self.assertEqual(images, sorted(path for record in kept for path in record["images"]))
        self.assertEqual(self.frq.acceptance_queue(self.plan["id"])["audit"], 1)
        # Changing the rubric (or the question, prompts, models or policy) is another scope: the permission no longer applies.
        plan = self.frq.assignment(self.plan["id"])
        q = plan["questions"][0]
        q = self.frq.import_rubric(plan["id"], self.qid, {"version": q["version"], "source_text": "Coordinator rubric, revised", "rules": {**pipeline.RULES, "notes": "revised"}})["questions"][0]
        self.frq.approve_rubric(plan["id"], self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"], "acknowledge_rules": True})
        finalize_rubrics(self.frq, plan["id"])
        with connect(self.path) as db:
            db.execute("UPDATE frq_tasks SET status='complete' WHERE status!='complete'")
        self.queue()
        self.run_all()
        latest = self.acceptances()[-1]
        self.assertEqual((latest["kind"], latest["scope_sha256"] != scope), ("trial", True))

    def test_a_failed_audit_suspends_acceptance_for_that_question(self):
        self.configure_acceptance(blind_share=1.0, audit_share=1.0)
        scope = self.evidence(clean=300)
        self.frq.enable_acceptance(self.plan["id"], self.qid, {"scope_sha256": scope, "confirm_automatic_acceptance": True})
        self.reviewed_source(batch=self.ready_batch(enrollment_id=self.other_student()), changed="second")
        self.queue()
        self.run_all()
        audited = next(a for a in self.acceptances() if a["kind"] == "automatic")
        self.review(audited["response_id"], ["R2"], comment="Audit: the reading was wrong.")
        report = self.frq.acceptance_report(self.plan["id"], self.qid, scope)["reports"][0]
        self.assertEqual(report["permission"]["status"], "suspended")
        with connect(self.path) as db:
            self.assertIsNone(self.frq.permission(db, scope))  # Back to trial acceptance until the instructor has looked again.
        exported = json.loads(self.frq.export(self.plan["id"], "audit")[0])["records"]
        self.assertEqual(sorted(r["final"]["status"] for r in exported), ["approved", "approved"])  # The overturned audit is exported as the instructor decided it.
        # The responses the instructor already decided are never accepted over by a newer proposal.
        before = len(self.acceptances())
        with connect(self.path) as db:
            db.execute("UPDATE frq_tasks SET status='complete' WHERE status!='complete'")
        self.queue()
        self.run_all()
        self.assertEqual(len(self.acceptances()), before)

    def test_suspending_the_whole_assessment_suspends_every_permission(self):
        scope = self.evidence(clean=300)
        self.frq.enable_acceptance(self.plan["id"], self.qid, {"scope_sha256": scope, "confirm_automatic_acceptance": True})
        self.reviewed_source(batch=self.ready_batch(enrollment_id=self.other_student()), changed="second")
        self.queue()
        self.run_all()
        self.frq.export(self.plan["id"], "csv")
        before = self.frq.assignment(self.plan["id"])["workflow"]
        self.assertEqual(before["responses"].get("auto_accepted"), 1)
        self.assertTrue(before["export"]["current"])
        self.assertEqual(before["certification"]["state"], "none")  # an export is not a certification (the sixth stage, 2026-09-26)
        with connect(self.path) as db:
            self.frq.suspend_acceptance(db, self.plan["id"], "Suspended for the whole assessment.")
        after = self.frq.assignment(self.plan["id"])["workflow"]
        self.assertEqual(after["responses"].get("auto_accepted", 0), 0)
        self.assertGreater(after["review_responses"], 0)
        self.assertFalse(after["export"]["current"])
        report = self.frq.acceptance_report(self.plan["id"], self.qid, scope)["reports"][0]
        self.assertEqual((report["permission"]["status"], report["permission"]["events"][-1]["reason"]), ("suspended", "Suspended for the whole assessment."))

    def other_student(self):
        with connect(self.path) as db:
            return db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? ORDER BY enrollment_id", (self.section["id"], self.enrollment)).fetchone()[0]


class GroupsAndTrial(AcceptanceCase):
    def test_trial_accepted_responses_are_reviewed_through_answer_groups_with_what_the_policy_relied_on(self):
        self.configure_acceptance(blind_share=0.0)
        self.students(2)
        self.grade(CORRECT)
        listing = self.frq.answer_groups(self.plan["id"], self.qid)
        self.assertEqual([(g["size"], g["accepted"], g["check"], g["work"], g["independent_votes"]) for g in listing["groups"]], [(2, "trial", "match", "shown", 2)])
        group = listing["groups"][0]
        approved = self.frq.approve_group(self.plan["id"], {"question_id": self.qid, "group": group["id"], "confirm_review": True, "inspected_each": True,
            "selected_items": group["applied"], "responses": [{"id": m["id"], "version": m["version"]} for m in group["members"]]})
        self.assertEqual(approved["approved"], 2)
        self.assertEqual([a["outcome"] for a in self.acceptances()], ["confirmed", "confirmed"])  # One look each, recorded as the measurement.


class Burden(AcceptanceCase):
    def test_the_goal_is_measured_by_the_instructors_work_per_response(self):
        self.configure_acceptance(blind_share=0.0)
        self.students(2)
        self.grade(CORRECT)
        group = self.frq.answer_groups(self.plan["id"], self.qid)["groups"][0]
        self.frq.approve_group(self.plan["id"], {"question_id": self.qid, "group": group["id"], "confirm_review": True, "inspected_each": True,
            "selected_items": group["applied"], "responses": [{"id": m["id"], "version": m["version"]} for m in group["members"]]})
        burden = self.frq.metrics(self.plan["id"])["burden"]
        self.assertEqual((burden["responses_decided"], burden["approved_by_group"], burden["approved_individually"], burden["instructor_actions"]), (2, 1.0, 0.0, 1))
        self.assertEqual((burden["actions_per_100_responses"], burden["group_members_removed"], burden["groups_regraded"], burden["false_alarms"]), (50.0, 0, 0, 0))
        self.assertEqual(burden["answer_groups"][self.qid], {"groups": 1, "responses": 2, "responses_per_group": 2.0})


if __name__ == "__main__":
    unittest.main()
