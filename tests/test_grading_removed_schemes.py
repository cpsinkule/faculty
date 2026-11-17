"""The schemes removed on 2026-09-23 (instructor: "I've settled on the current cloud standard"): Jev Routing, One-shot, Tag-Team,
Cloud High Assurance, the plain Nebius mode, and the options Final Arbitrator Mode v1 and the Jev tie-breaker.

Nothing can start, resume or retry their batches, and nothing is sent for them; what they produced stays readable and final."""
import json
import unittest

from werkzeug.exceptions import Conflict

from grading import config
from grading.providers.base import ProviderError
from grading.providers.cloud import provider
from rosters import connect
import test_grading_cloud as cloud_fixture

# How a batch of each removed scheme is recognized: its frozen profile and its stored tier (every cloud job is stored as mode nebius).
HISTORICAL = {
    "one_shot": ({"grading_workflow": config.ONE_SHOT_WORKFLOW, "one_shot_model": "alpha-vision"}, ""),
    "tag_team": ({"grading_workflow": config.TAG_TEAM_WORKFLOW, "tag_team_model": "alpha-vision", "tag_team_reviewer": "gamma-vision"}, ""),
    "jev_routing": ({"grading_workflow": config.ONE_SHOT_WORKFLOW, "jev_routing_rule": "jev-routing-v2"}, ""),
    "cloud_high_assurance": ({}, "cloud_high_assurance"),
    "nebius": ({"nebius_model": "Qwen/Qwen3-VL-30B-A3B-Instruct"}, ""),
}
OPTIONS = {
    "Final Arbitrator Mode v1": {"final_arbitrator": True, "final_arbitrator_rule": "final-arbitrator-v1", "final_arbitrator_model": "gamma-vision"},
    "The Jev tie-breaker": {"jev_tiebreak": True, "jev_tiebreak_rule": "jev-tiebreak-v3"},
}


class RemovedSchemes(cloud_fixture.CloudCase):
    def refreeze(self, job_id, changes, tier=None):
        """Rewrite a batch's frozen profile, from the one it was submitted with, as if it had run under a removed scheme or option."""
        with connect(self.path) as db:
            row = db.execute("SELECT profile_json,tier FROM frq_jobs WHERE id=?", (job_id,)).fetchone()
            original = self.submitted.setdefault(job_id, (json.loads(row["profile_json"]), row["tier"]))
            profile = {**original[0], **changes}
            if not changes.get("grading_workflow") and tier is not None:
                profile.pop("grading_workflow", None)
            db.execute("UPDATE frq_jobs SET profile_json=?,tier=? WHERE id=?", (json.dumps(profile), original[1] if tier is None else tier, job_id))

    def setUp(self):
        super().setUp()
        self.submitted = {}

    def test_a_new_batch_of_a_removed_scheme_is_refused_before_anything_is_queued(self):
        self.assertEqual(config.MODES, ("local", "hybrid", "cloud_standard"))  # Cloud keeps its key: profiles and a CHECK freeze it.
        for scheme, label in config.REMOVED_SCHEMES.items():
            with self.subTest(scheme=scheme), self.assertRaisesRegex(ValueError, f"^{label} was removed on 2026-09-23"):
                self.frq.preview(self.plan["id"], {"question_ids": [self.qid], "mode": scheme})
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_jobs").fetchone()[0], 0)

    def test_a_batch_of_a_removed_scheme_cannot_resume_or_be_retried_and_sends_nothing(self):
        for scheme, (changes, tier) in HISTORICAL.items():
            with self.subTest(scheme=scheme):
                job = self.queue(selection="all")
                self.refreeze(job["id"], changes, tier)
                self.host.requests.clear()
                self.run_all()
                done = self.frq.job(job["id"])
                self.assertEqual(done["compute"], scheme)
                self.assertEqual([t["status"] for t in done["tasks"]], ["failed"])
                self.assertRegex(done["tasks"][0]["error"], "was removed on 2026-09-23, so this batch cannot continue or be retried")
                self.assertEqual(self.host.requests, [])
                with self.assertRaisesRegex(Conflict, "was removed on 2026-09-23"):
                    self.frq.retry_job(job["id"])

    def test_a_batch_that_froze_a_removed_option_cannot_resume_either(self):
        # Those were options of a team batch, so its compute is still a tier: resumed, it would be graded by rules it never froze.
        for label, changes in OPTIONS.items():
            with self.subTest(option=label):
                job = self.queue(selection="all")
                self.refreeze(job["id"], changes)
                self.host.requests.clear()
                self.run_all()
                done = self.frq.job(job["id"])
                self.assertEqual((done["compute"], [t["status"] for t in done["tasks"]]), ("cloud_standard", ["failed"]))
                self.assertTrue(done["tasks"][0]["error"].startswith(f"{label}, which this batch froze, was removed on 2026-09-23"))
                self.assertEqual(self.host.requests, [])
                with self.assertRaisesRegex(Conflict, "which this batch froze, was removed"):
                    self.frq.retry_job(job["id"])

    def test_what_a_removed_scheme_graded_stays_readable_and_final(self):
        job = self.queue()
        self.run_all()
        response = self.frq.response(self.frq.job(job["id"])["tasks"][0]["response_id"])
        approved = self.frq.save_review(response["id"], {"version": response["version"], "result_id": response["current_result_id"],
            "status": "approved", "selected_items": [], "confirm_review": True, "comment": "Synthetic approval"})
        exported = json.loads(self.frq.export(self.plan["id"], "audit")[0])
        for scheme, (changes, tier) in HISTORICAL.items():
            with self.subTest(scheme=scheme):
                self.refreeze(job["id"], changes, tier)
                self.assertEqual(self.frq.job(job["id"])["compute"], scheme)
                after = self.frq.response(response["id"])
                self.assertEqual((after["result"]["score"], after["review"]["id"]), (approved["result"]["score"], approved["review"]["id"]))
                self.assertEqual(self.frq.review_queue(self.plan["id"])["questions"][0]["approved"], 1)
                again = json.loads(self.frq.export(self.plan["id"], "audit")[0])
                self.assertEqual([r["final"] for r in again["records"]], [r["final"] for r in exported["records"]])
                self.assertEqual(again["skipped"], [])

    def test_saved_preferences_of_removed_schemes_still_load(self):
        old = {"default_tier": "cloud_high_assurance", "one_shot_model": "x", "tag_team_model": "x", "tag_team_reviewer": "x",
               "jev_routing_model_3": "x", "jev_tiebreak": True, "jev_tiebreak_confidence": .9, "final_arbitrator": True,
               "final_arbitrator_model": "x", "escalation_enabled": True,
               "role_reasoning": {"cloud_high_assurance": {"verifier": "on"}}}
        clean = config.validate(old)
        self.assertEqual(clean["default_tier"], "cloud_standard")
        self.assertFalse(set(old) - {"default_tier", "role_reasoning"} & set(clean))
        self.assertNotIn("cloud_high_assurance", clean["tiers"])
        self.assertNotIn("cloud_high_assurance", clean["role_reasoning"])
        # A stored Jev connection never falls through to the chat client, which would post a grading payload; its key stays reserved.
        with self.assertRaisesRegex(ProviderError, "Jev connection was removed"):
            provider({**config.ENDPOINT, "name": "jev", "kind": "system_one"})
        self.assertRegex(config.key_problem({"key_env": "TYPESAFE_API_KEY", "base_url": "https://example.invalid/v1"}), "no connection may name it")


if __name__ == "__main__":
    unittest.main()
