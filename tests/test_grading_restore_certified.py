"""Putting back the certified grades a later batch replaced (`AcceptanceMixin.restore_certified`; requirement, 2026-09-29). Against the local fake host."""
import json

from werkzeug.exceptions import Conflict

from rosters import connect
import test_grading_teams as teams
from test_grading_pipeline import CORRECT, Pipeline


class RestoreCertified(Pipeline):
    legacy_workflow = False
    opinions = teams.GradingTeams.opinions

    def certify(self, response):
        """A certification of the plan naming this response's automatic grade, as Certification and Export records one."""
        result = response["result"]["id"]
        with connect(self.path) as db:
            acceptance = db.execute("SELECT id FROM frq_all_acceptances WHERE result_id=? AND kind='automatic' AND active=1", (result,)).fetchone()[0]
            member = db.execute("SELECT section_id,enrollment_id,question_id FROM frq_responses WHERE id=?", (response["id"],)).fetchone()
            db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
                participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradebook_json,gradescope_json,supersedes_id,actor,created_at)
                VALUES('c1',?,'c1','s','f','certify-v1','["frq"]','d','b','p','n','{}','[]','{}','{}','{}',NULL,'workspace','2026-09-26T00:00:00Z')""", (self.plan["id"],))
            parts = {"frq": {"earned": "10", "possible": "10", "questions": {member["question_id"]: {
                "decision": f"automatic:{acceptance}", "max": "10", "response_id": response["id"], "result_id": result, "score": "10"}}}}
            db.execute("""INSERT INTO frq_certification_students(certification_id,section_id,enrollment_id,status,reason,earned,possible,exam_version,parts_json)
                VALUES('c1',?,?,'scored','','10','10','',?)""", (member["section_id"], member["enrollment_id"], json.dumps(parts)))
            db.commit()
        return result

    def latest_job(self):
        with connect(self.path) as db:
            return db.execute("SELECT id FROM frq_jobs ORDER BY rowid DESC LIMIT 1").fetchone()[0]

    def current(self, response_id):
        with connect(self.path) as db:
            return db.execute("SELECT current_result_id FROM frq_responses WHERE id=?", (response_id,)).fetchone()[0]

    def test_the_certified_grade_is_pointed_at_again_and_nothing_is_erased(self):
        first, _ = self.grade(CORRECT)
        self.assertEqual(first["acceptance"]["kind"], "automatic")
        certified = self.certify(first)
        again, _ = self.grade(CORRECT)  # the regrade nobody asked for
        job = self.latest_job()
        replaced = self.current(first["id"])
        self.assertNotEqual(replaced, certified)
        done = self.frq.restore_certified(job, "restore-1")
        self.assertEqual(([r["to_result_id"] for r in done["restored"]], done["skipped"], done["replayed"]), ([certified], [], False))
        self.assertEqual(self.current(first["id"]), certified)
        response = self.frq.response(first["id"])
        self.assertEqual((response["acceptance"]["kind"], response["result"]["id"]), ("automatic", certified))  # final again, as certified
        with connect(self.path) as db:  # the regrade's result stays on record
            self.assertTrue(db.execute("SELECT 1 FROM frq_results WHERE id=?", (replaced,)).fetchone())
            event, = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE kind='certified_grades_restored'")]
        self.assertEqual((event["restored"][0]["from_result_id"], event["certification_id"]), (replaced, "c1"))
        self.assertTrue(self.frq.restore_certified(job, "restore-1")["replayed"])  # the same request returns what it did
        self.assertEqual(self.frq.restore_certified(job, "restore-2")["restored"], [])  # nothing of the batch is current any more

    def test_a_grade_decided_since_or_a_batch_still_running_is_left_alone(self):
        first, _ = self.grade(CORRECT)
        self.certify(first)
        self.grade(CORRECT)
        job = self.latest_job()
        response = self.frq.response(first["id"])
        self.frq.save_review(first["id"], {"version": response["version"], "result_id": response["current_result_id"], "status": "approved",
                                           "selected_items": ["R1"], "confirm_review": True})
        done = self.frq.restore_certified(job, "restore-1")
        self.assertEqual((done["restored"], [s["why"] for s in done["skipped"]]), ([], ["you have decided it since"]))
        with connect(self.path) as db:
            db.execute("UPDATE frq_jobs SET status='running' WHERE id=?", (job,))
            db.commit()
        with self.assertRaises(Conflict):
            self.frq.restore_certified(job, "restore-2")
