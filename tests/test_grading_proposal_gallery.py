"""GRADING (requirement, 2026-10-03): every response of one proposed-line group side by side, the rest approved after looking at each.
The queue decides the group; each response keeps its own review; anything that changed refuses the whole approval."""
import json
import unittest

from werkzeug.exceptions import Conflict

from grading.answer_groups import AnswerGroupsMixin
from rosters import connect
from test_grading_answer_groups import BARE, WORKED
from test_grading_pipeline import CORRECT
import test_grading_direct_read as direct_fixture
import test_grading_review_queue as queue_fixture


class ProposalGalleryTests(unittest.TestCase):
    managed = False
    document_version = queue_fixture.ReviewQueueTests.document_version
    ready_batch = queue_fixture.ReviewQueueTests.ready_batch
    reviewed_source = queue_fixture.ReviewQueueTests.reviewed_source
    configure_rubric = queue_fixture.ReviewQueueTests.configure_rubric
    grade = queue_fixture.ReviewQueueTests.grade
    setUp = queue_fixture.ReviewQueueTests.setUp

    def largest_group(self):
        question = next(q for q in self.frq.review_queue(self.plan["id"])["questions"] if q["question_id"] == self.qid)
        return max(question["groups"], key=lambda g: g["size"])

    def approve(self, gallery, members, **extra):
        return self.frq.approve_proposal_group(self.plan["id"], {"question_id": self.qid, "group": gallery["group"], "applied": gallery["applied"],
            "responses": [{"id": m["id"], "version": m["version"], "result_id": m["result_id"]} for m in members],
            "confirm_review": True, "inspected_each": True, **extra})

    def reviews(self):
        with connect(self.path) as db:
            return [tuple(row) for row in db.execute("SELECT response_id,status FROM frq_reviews ORDER BY rowid")]

    def test_the_rest_of_a_group_is_approved_with_its_own_lines_one_review_each(self):
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R2"}})
        group = self.largest_group()
        self.assertEqual(group["size"], 2)
        gallery = self.frq.proposal_gallery(self.plan["id"], self.qid, group["id"])
        self.assertEqual((gallery["applied"], gallery["eligible"]), (["R2"], 2))
        self.assertTrue(all(m["images"] and m["eligible"] and not m["why"] for m in gallery["members"]))
        # The client's own route answers the same.
        client = self.app.test_client()
        self.assertEqual(client.get(f"/api/grading/assignments/{self.plan['id']}/proposal-gallery?question_id={self.qid}&group={group['id']}").get_json()["eligible"], 2)
        result = self.approve(gallery, gallery["members"])
        self.assertEqual(result, {"approved": 2, "remaining": 0})
        self.assertEqual(sorted(self.reviews()), sorted((m["id"], "approved") for m in gallery["members"]))
        states = {row["id"]: row["state"] for row in self.frq.responses(self.plan["id"])}
        self.assertEqual({states[m["id"]] for m in gallery["members"]}, {"approved"})
        self.assertEqual(self.frq.review_queue(self.plan["id"])["waiting"], 0)

    def test_a_response_with_a_rubric_concern_is_shown_but_reviewed_alone(self):
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R2"}}, {tuple(BARE): ["Unclear whether R2 needs the written step."]})
        gallery = self.frq.proposal_gallery(self.plan["id"], self.qid, self.largest_group()["id"])
        alone = [m for m in gallery["members"] if not m["eligible"]]
        self.assertEqual(len(alone), 1)
        self.assertIn("rubric", alone[0]["reason_kinds"])
        self.assertIn("rubric concern", alone[0]["why"])
        with self.assertRaises(Conflict):
            self.approve(gallery, gallery["members"])
        self.assertEqual(self.reviews(), [])
        self.assertEqual(self.approve(gallery, [m for m in gallery["members"] if m["eligible"]]), {"approved": 1, "remaining": 1})

    def test_anything_changed_refuses_the_whole_approval_and_saves_nothing(self):
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R2"}})
        gallery = self.frq.proposal_gallery(self.plan["id"], self.qid, self.largest_group()["id"])
        stale = [{**gallery["members"][0], "version": gallery["members"][0]["version"] - 1}, gallery["members"][1]]
        with self.assertRaises(Conflict):
            self.approve(gallery, stale)
        moved = [{**gallery["members"][0], "result_id": "another-result"}, gallery["members"][1]]
        with self.assertRaises(Conflict):
            self.approve(gallery, moved)
        with self.assertRaises(Conflict):
            self.approve(gallery, gallery["members"], applied=["R2", "R3"])
        with self.assertRaises(Conflict):
            self.approve(gallery, gallery["members"], group="lines-not-a-group")
        with self.assertRaises(ValueError):
            self.approve(gallery, gallery["members"], inspected_each=False)
        with self.assertRaises(ValueError):
            self.approve(gallery, [gallery["members"][0], gallery["members"][0]])
        self.assertEqual(self.reviews(), [])
        # A decision the instructor started in the desk takes the response out of the gallery's approval.
        first = gallery["members"][0]
        self.frq.save_review(first["id"], {"version": first["version"], "result_id": first["result_id"], "status": "draft", "confirm_review": True, "selected_items": ["R2"]})
        again = self.frq.proposal_gallery(self.plan["id"], self.qid, self.largest_group()["id"])
        self.assertFalse(next(m for m in again["members"] if m["id"] == first["id"])["eligible"])


class BlankAnswerRule(unittest.TestCase):
    """Blank answers in one gallery (requirement, 2026-10-11): which held blank answers may be approved together, from the stored result."""

    @staticmethod
    def result(*, blank=1, registered=True, score=0, lines=(), agrees=True, empty=False):
        committee = {"status": "resolved" if agrees else "unresolved", "pair": {"agrees": agrees, "disagreements": [] if agrees else ["R4"]}}
        reading = {"registered": registered, **({"blank_crops": blank} if blank else {})}
        signals = {"risk": {"components": {"reading": reading, "committee": committee}}, **({"empty_selection": {"zero_lines": ["R4"]}} if empty else {})}
        return {"signals_json": json.dumps(signals), "score_json": json.dumps({"score": score, "selected": ["R4"]}), "transcript_json": json.dumps({"lines": list(lines)})}

    def test_everything_must_follow_from_the_empty_reading(self):
        rule = AnswerGroupsMixin.blank_answer
        self.assertEqual(rule(self.result(), 1), (True, True))
        self.assertEqual(rule(self.result(blank=2), 2), (True, True))
        self.assertEqual(rule(self.result(blank=0), 1), (False, False))                # not held for a blank answer at all
        self.assertEqual(rule(self.result(blank=1), 2), (True, False))                 # one of its two areas has writing
        self.assertEqual(rule(self.result(lines=[{"id": "line-1"}]), 1), (True, False))  # something was transcribed
        self.assertEqual(rule(self.result(score=2), 1), (True, False))                 # credit proposed beside an empty area
        self.assertEqual(rule(self.result(registered=False), 1), (True, False))        # the crop may be misaligned: it can look empty
        self.assertEqual(rule(self.result(agrees=False), 1), (True, False))            # the graders did not agree
        self.assertEqual(rule(self.result(empty=True), 1), (True, False))              # no line ticked is the instructor's alone
        old = self.result()
        old["signals_json"] = json.dumps({"risk": {"components": {"reading": {"blank_crops": 1}}}})  # a result from before `registered` was kept
        self.assertEqual(rule(old, 1), (True, False))


class BlankAnswers(direct_fixture.DirectReading):
    """The same through the pipeline: a reading that found nothing, graded by the team, then the gallery."""

    def blank(self, **ticks):
        response, _ = self.grade(CORRECT, script=lambda name, body: ("raw", "   ", "stop") if name == "read" else None, **ticks)
        self.assertIn(response["result"]["state"], AnswerGroupsMixin.BLANK_STATES)
        return response

    def gallery(self, response, only=None):
        queue = self.frq.review_queue(self.plan["id"], None, response["question_id"], review_details=True)
        group = next(g for q in queue["questions"] for g in q["groups"] if any(m["id"] == response["id"] for m in g["members"]))
        return self.frq.proposal_gallery(self.plan["id"], response["question_id"], group["id"], None, only)

    def test_a_blank_zero_the_graders_agree_on_is_approved_from_the_gallery_with_both_crops_shown(self):
        response = self.blank(primary=("R4",))
        gallery = self.gallery(response, "blank")
        member, = gallery["members"]
        self.assertEqual((gallery["only"], gallery["eligible"], member["blank"], member["eligible"], member["why"]), ("blank", 1, True, True, ""))
        self.assertEqual(len(member["contrast"]), len(member["images"]))
        self.assertTrue(any("no handwriting" in reason for reason in member["reasons"]))  # what holds it is still said on the tile
        self.assertEqual(self.gallery(response)["members"], gallery["members"])  # the group's ordinary gallery lists it the same
        with self.assertRaises(ValueError):
            self.gallery(response, "everything")
        done = self.frq.approve_proposal_group(self.plan["id"], {"question_id": response["question_id"], "group": gallery["group"], "applied": gallery["applied"],
            "only": "blank", "responses": [{"id": member["id"], "version": member["version"], "result_id": member["result_id"]}], "confirm_review": True, "inspected_each": True})
        self.assertEqual(done, {"approved": 1, "remaining": 0})
        with connect(self.path) as db:
            review = db.execute("SELECT status,comment,applications_json FROM frq_reviews WHERE response_id=?", (response["id"],)).fetchone()
            event = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='proposal_group_approved' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual((review["status"], review["comment"]), ("approved", "Confirmed blank in its gallery after looking at each crop."))
        self.assertEqual([a["rubric_item_id"] for a in json.loads(review["applications_json"]) if a["applied"]], ["R4"])
        self.assertEqual((event["approved"], event["blank"]), (1, 1))

    def test_credit_proposed_for_an_empty_area_is_shown_and_opened_alone(self):
        response = self.blank()  # the graders tick the full-credit line although nothing was read
        gallery = self.gallery(response, "blank")
        member, = gallery["members"]
        self.assertEqual((gallery["eligible"], member["blank"], member["eligible"]), (0, True, False))
        self.assertIn("more than that holds it", member["why"])
        with self.assertRaises(Conflict):
            self.frq.approve_proposal_group(self.plan["id"], {"question_id": response["question_id"], "group": gallery["group"], "applied": gallery["applied"],
                "only": "blank", "responses": [{"id": member["id"], "version": member["version"], "result_id": member["result_id"]}], "confirm_review": True, "inspected_each": True})
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_reviews").fetchone()[0], 0)


def load_tests(loader, tests, pattern):
    # Only this module's own scenarios: the direct reading's and the pipeline's are run by their own modules.
    return unittest.TestSuite(case(name) for case in (ProposalGalleryTests, BlankAnswerRule, BlankAnswers) for name in case.__dict__ if name.startswith("test_"))


if __name__ == "__main__":
    unittest.main()
