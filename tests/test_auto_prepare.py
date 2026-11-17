"""Answer areas and answer keys that confirm themselves (GRADING "ANSWER AREAS CONFIRM THEMSELVES" and "ANSWER KEYS SAVE THEMSELVES",
requirement, 2026-10-22), on the synthetic fixtures of the location checks and the rubric drafts."""
import json
import unittest
from unittest.mock import patch

from course_storage import timestamp
from grading import auto_prepare
from grading.models import digest, encoded, uid
from rosters import connect
import test_grading_locations
import test_grading_rubric_drafts

# Their fixtures, not their tests (a class imported by name would run its tests here too).
LocationWorkflow = test_grading_locations.LocationWorkflow
AcceptingADraftReadiesIt = test_grading_rubric_drafts.AcceptingADraftReadiesIt


def events(path, kind):
    with connect(path) as db:
        return [(r["actor"], json.loads(r["detail_json"])) for r in db.execute("SELECT actor,detail_json FROM frq_events WHERE kind=? ORDER BY id", (kind,))]


def area_confirmations(path):
    with connect(path) as db:
        return [(r["question_id"], r["actor"], r["manual_review"], json.loads(r["detail_json"])["notes"])
                for r in db.execute("SELECT * FROM frq_location_reviews ORDER BY rowid")]


class AreasConfirmThemselves(unittest.TestCase):
    managed = False
    document_version, ready_batch, reviewed_source = LocationWorkflow.document_version, LocationWorkflow.ready_batch, LocationWorkflow.reviewed_source
    configure_rubric, queue = LocationWorkflow.configure_rubric, LocationWorkflow.queue
    setUp, upload, question, finish_check = LocationWorkflow.setUp, LocationWorkflow.upload, LocationWorkflow.question, LocationWorkflow.finish_check

    def test_a_suggested_area_is_confirmed_as_suggested_by_the_worker_and_the_question_grades(self):
        self.upload()
        check = self.finish_check()
        self.assertEqual(check["status"], "needs_confirmation")
        self.assertTrue(self.frq.auto_prepare(self.plan["id"]))
        q = self.question()
        self.assertEqual((q["location_check"]["status"], q["location_check"]["label"]), ("confirmed", "Locations confirmed automatically"))
        self.assertEqual(q["content"]["regions"], check["result"]["regions"])
        self.assertEqual(area_confirmations(self.path), [(self.qid, "automatic", 0, "")])
        self.assertEqual([actor for actor, _ in events(self.path, "template_locations_confirmed")], ["automatic"])
        self.queue()  # A confirmed area is what lets the question grade.
        self.assertFalse(self.frq.auto_prepare(self.plan["id"]))  # Nothing left to confirm.

    def test_an_area_that_needs_review_is_confirmed_too_with_its_note(self):
        self.upload()
        self.reader.points = self.reader.ocr_points = 8
        check = self.finish_check()
        self.assertEqual(check["status"], "needs_review")
        self.frq.auto_prepare(self.plan["id"])
        self.assertEqual(self.question()["location_check"]["status"], "confirmed")
        self.assertEqual(area_confirmations(self.path), [(self.qid, "automatic", 1, auto_prepare.NOTE)])

    def test_a_failed_check_and_a_graded_question_wait_for_the_instructor(self):
        self.reader.fail_reading = True
        self.upload()
        self.assertEqual(self.finish_check()["status"], "failed")
        self.assertFalse(self.frq.auto_prepare(self.plan["id"]))
        self.reader.fail_reading = False
        q = self.question()
        self.frq.retry_location(self.plan["id"], self.qid, {"check_id": q["location_check"]["id"], "input_sha256": q["location_check"]["input_sha256"]})
        self.assertEqual(self.finish_check()["status"], "needs_confirmation")
        with patch.object(self.frq, "graded_yet", return_value=True):
            self.assertFalse(self.frq.auto_prepare(self.plan["id"]))
        self.assertEqual(area_confirmations(self.path), [])

    def test_boxes_from_gradescope_confirm_themselves_though_the_reading_failed(self):
        """BOXES FROM GRADESCOPE (2026-10-25: Exam 2's page 8 could not be read for Versions A and B): areas imported from
        Gradescope's outline are suggested as they are and confirmed even when the check failed; other failed checks still wait."""
        self.reader.fail_reading = True
        template = self.upload()
        boxes = [{"page": 1, "region": {"x": .1, "y": .1, "width": .8, "height": .3}, "privacy_masks": []}]
        q = self.question()
        with connect(self.path) as db:
            clean = {**q["content"], "template_id": template["id"], "regions": boxes, "regions_from": "gradescope"}
            revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM frq_question_revisions WHERE question_id=?", (self.qid,)).fetchone()[0]
            rid = uid()
            db.execute("INSERT INTO frq_question_revisions VALUES(?,?,?,?,?,?)", (rid, self.qid, revision, encoded(clean), digest(clean), timestamp()))
            db.execute("UPDATE frq_assignment_questions SET question_revision_id=?,version=version+1 WHERE assignment_id=? AND question_id=?",
                       (rid, self.plan["id"], self.qid))
        check = self.finish_check()
        self.assertEqual((check["status"], check["result"]["regions"], check["result"]["regions_from"]), ("failed", boxes, "gradescope"))
        self.assertTrue(self.frq.auto_prepare(self.plan["id"]))
        q = self.question()
        self.assertEqual((q["location_check"]["status"], q["content"]["regions"]), ("confirmed", boxes))
        self.assertEqual(area_confirmations(self.path), [(self.qid, "automatic", 1, auto_prepare.NOTE)])

    def test_the_worker_step_looks_at_a_current_assignment_once_its_record_changes(self):
        self.upload()
        self.finish_check()
        self.assertTrue(self.frq.auto_prepare_one())
        self.assertEqual(self.question()["location_check"]["status"], "confirmed")
        self.assertFalse(self.frq.auto_prepare_one())  # Looked at moments ago: nothing is due.


class KeysSaveThemselves(unittest.TestCase):
    managed = False
    KEY, OPEN = AcceptingADraftReadiesIt.KEY, AcceptingADraftReadiesIt.OPEN
    document_version, ready_batch, reviewed_source = AcceptingADraftReadiesIt.document_version, AcceptingADraftReadiesIt.ready_batch, AcceptingADraftReadiesIt.reviewed_source
    upload, uploaded_questions, template, configure = AcceptingADraftReadiesIt.upload, AcceptingADraftReadiesIt.uploaded_questions, AcceptingADraftReadiesIt.template, AcceptingADraftReadiesIt.configure
    draft, keep, setUp, accept, question = AcceptingADraftReadiesIt.draft, AcceptingADraftReadiesIt.keep, AcceptingADraftReadiesIt.setUp, AcceptingADraftReadiesIt.accept, AcceptingADraftReadiesIt.question
    readings, checks, suggested_areas, written_rows = AcceptingADraftReadiesIt.readings, AcceptingADraftReadiesIt.checks, AcceptingADraftReadiesIt.suggested_areas, AcceptingADraftReadiesIt.written_rows

    def test_drafted_rubrics_go_from_suggested_areas_to_saved_keys_and_the_rubric_check_without_a_click(self):
        self.numbers, self.written, self.reader.extra = ('18', '19'), {'19': self.OPEN}, [('19', 10)]
        made = {r['draft']: r['question_id'] for r in self.accept(self.draft(), 'accept-1')['results']}
        self.suggested_areas()
        self.frq.auto_prepare(self.aid)
        self.assertEqual({self.question(qid)['location_check']['status'] for qid in made.values()}, {'confirmed'})
        # Question 18's rubric proposes its key, saved as shown; question 19's is read first, by the reading a confirmed area asks for.
        self.assertEqual(self.question(made['18'])['content']['answer_key'], [{'label': '', 'accept': ['x = 6']}])
        [reading] = self.readings()
        self.assertEqual(reading['request_id'], f"area-confirmed:{reading['confirmation']}:{made['19']}")
        self.assertEqual(self.question(made['19'])['content']['answer_key'], [])
        self.frq.process_cloud()
        self.frq.auto_prepare(self.aid)
        self.assertEqual(self.question(made['19'])['content']['answer_key'], [{'label': '', 'accept': ['x = 6']}])
        saved = events(self.path, 'answer_key_saved')
        self.assertEqual(sorted((actor, detail['question_id'], detail['location_carried_forward']) for actor, detail in saved),
                         sorted([('automatic', made['18'], True), ('automatic', made['19'], True)]))
        self.assertEqual({q['location_check']['status'] for q in self.uploaded_questions()}, {'confirmed'})  # The areas are carried.
        self.assertTrue(self.checks())  # The plan was ready, so the rubric check started by itself.
        before = self.written_rows()
        self.assertFalse(self.frq.auto_prepare(self.aid))  # Nothing is saved twice.
        self.assertEqual(self.written_rows(), before)

    def test_an_uploaded_rubric_without_a_key_has_it_read_and_saved(self):
        self.numbers, self.reader.extra = ('18',), [('20', 10)]
        self.upload('1_Question_20.csv')
        [q] = self.uploaded_questions()
        self.assertEqual(q['answer']['proposed_key'], [])  # "Full credit" names no answer.
        self.suggested_areas()
        self.frq.auto_prepare(self.aid)
        [reading] = self.readings()
        self.assertEqual(reading['request_id'], f"{auto_prepare.KEYS_REQUEST}:{reading['confirmation']}:{q['question_id']}")
        self.assertEqual([actor for actor, _ in events(self.path, 'answer_key_reading_queued')], ['automatic'])
        self.frq.process_cloud()
        self.frq.auto_prepare(self.aid)
        self.assertEqual(self.question(q['question_id'])['content']['answer_key'], [{'label': '', 'accept': ['x = 6']}])
        self.assertEqual([actor for actor, _ in events(self.path, 'answer_key_saved')], ['automatic'])

    def test_a_question_with_grades_keeps_its_proposal_for_the_instructor(self):
        self.numbers = ('18',)
        made = {r['draft']: r['question_id'] for r in self.accept(self.draft(), 'accept-1')['results']}
        self.suggested_areas()
        with patch.object(self.frq, 'graded_yet', return_value=True):
            before = self.written_rows()
            self.assertFalse(self.frq.auto_prepare(self.aid))
            self.assertEqual(self.written_rows(), before)
        self.assertEqual(self.question(made['18'])['content']['answer_key'], [])


del LocationWorkflow, AcceptingADraftReadiesIt  # fixtures only: their tests run in their own modules


if __name__ == "__main__":
    unittest.main()
