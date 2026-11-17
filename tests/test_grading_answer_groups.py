"""Answer facts through the real job runner, and group review with one decision per response."""
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from grading.models import plain_transcript
from rosters import connect
import test_frq_grading as grading_fixture
from frq_fixtures import finalize_rubrics

RULES = {"mode": "positive", "items": [
    {"id": "R1", "description": "Correct Answer: $$x=6$$ and work to justify answer.", "points": 10},
    {"id": "R2", "description": "Set up the equation correctly", "points": 4},
    {"id": "R3", "description": "Correct answer, but no work to justify answer.", "points": 2},
    {"id": "R4", "description": "No Answer or Completely Incorrect", "points": 0}]}
WORKED = ["3x - 8 = 10", "3x = 10 + 8", "3x = 18", "x = 6"]
BARE = ["x = 6"]
WRONG = ["3x - 8 = 10", "3x = 2", "x = 2/3", "so x = 5"]


class ScriptedProvider(grading_fixture.FakeProvider):
    """Reads each student's own lines, then proposes whatever the test scripted for that handwriting."""
    def __init__(self, papers, proposals, doubts=None):
        self.papers, self.proposals, self.reads, self.doubts = list(papers), proposals, {}, doubts or {}

    def recognize(self, image):
        lines = self.reads.setdefault(bytes(image), self.papers[len(self.reads) % len(self.papers)])
        return {"provider": "glm", "transcript": plain_transcript("\n".join(lines), confidence=.97), "raw": {"text": "\n".join(lines)}, "metadata": {"model": "fixture-ocr"}}

    def verify(self, payload, images):
        lines = self.reads[bytes(images[0])]
        return {"output": {"transcript": plain_transcript("\n".join(lines), confidence=.97), "visual_confidence": .97}, "raw": {}, "metadata": {"model": "fixture-verify"}}

    def grade(self, payload, images):
        lines = payload["transcript"]["lines"]
        written = [line["elements"][0]["text"] for line in lines]
        chosen = self.proposals[tuple(written)]
        last = lines[-1]
        evidence = [{"line_id": last["id"], "student_text": written[-1], "bbox": None, "crop": 0}]
        return {"output": {"applications": [{"rubric_item_id": item["id"], "applied": item["id"] in chosen, "reason": "Scripted", "origin_error_id": None,
                    "confidence": .98, "evidence": evidence if item["id"] in chosen else []} for item in payload["rubric"]["items"]],
                "interpretation": {"meaning": "Scripted", "error_graph": [], "final_answers": [{"part": payload["answer_parts"][0], "text": written[-1], "line_id": last["id"]}]},
                "confidence": .98, "rubric_ambiguities": list(self.doubts.get(tuple(written), [])), "review_reasons": []}, "raw": {"fixture": True}, "metadata": {"model": "fixture-grade"}}


class AnswerGroupWorkflow(unittest.TestCase):
    managed = False
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric

    def setUp(self):
        grading_fixture.FrqWorkflowTests.setUp(self)
        patch.stopall()  # Replace the fixture's provider; keep its extraction stub.
        from frq_fixtures import page
        self.pages = iter(range(1000))
        def extract(scan, region, template=None, masks=()):
            image = page() + bytes([next(self.pages)])  # Each student's crop is its own bytes, as in real scans.
            return {"metadata": {"registered": True, "crop_bounds": [0, 0, 100, 100]}, "images": {k: image for k in ("original", "grayscale", "contrast", "ink", "cloud")}}
        self.enterContext(patch("grading.extraction.extract", side_effect=extract))
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        q = self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "source_text": "Coordinator rubric", "rules": RULES})["questions"][0]
        self.frq.approve_rubric(self.plan["id"], self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"], "acknowledge_rules": True})
        finalize_rubrics(self.frq, self.plan["id"])
        with connect(self.path) as db:
            self.second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? ORDER BY enrollment_id",
                                     (self.section["id"], self.enrollment)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=self.second), changed="second")

    def save_key(self, **changes):
        plan = self.frq.assignment(self.plan["id"])
        q = plan["questions"][0]
        return self.frq.save_answer_key(plan["id"], {"version": plan["version"], "questions": [{"question_id": self.qid, "version": q["version"],
            "answer_key": [{"label": "x", "accept": ["x=6"]}], "worked_solution": "3x = 18, so x = 6", **changes}]})

    def grade(self, papers, proposals, doubts=None):
        self.enterContext(patch.object(self.frq, "provider", return_value=ScriptedProvider(papers, proposals, doubts)))
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "mode": "local", "operation": "recognize"}
        command["fingerprint"] = self.frq.preview(self.plan["id"], command)["fingerprint"]
        job = self.frq.create_job(self.plan["id"], command)
        for _ in range(60):
            if not self.frq.process_one():
                break
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        return self.frq.answer_groups(self.plan["id"], self.qid)

    def test_the_key_is_proposed_from_the_rubric_and_saving_it_resets_nothing_the_instructor_approved(self):
        before = self.frq.assignment(self.plan["id"])["questions"][0]
        self.assertEqual(before["answer"]["proposed_key"], [{"label": "", "accept": ["x=6"]}])
        self.assertEqual((before["answer"]["roles"]["full"], before["answer"]["roles"]["no_work"], before["answer"]["roles"]["work_required"]), ("R1", "R3", True))
        after = self.save_key()["questions"][0]
        self.assertEqual((after["rubric_id"], after["rubric"]["approved_at"]), (before["rubric_id"], before["rubric"]["approved_at"]))
        self.assertNotEqual(after["question_revision_id"], before["question_revision_id"])
        self.assertEqual((after["content"]["answer_key"], after["content"]["worked_solution"], after["content"]["regions"]),
                         ([{"label": "x", "accept": ["x=6"]}], "3x = 18, so x = 6", before["content"]["regions"]))
        # Saving the same key again writes nothing; a stale or empty key is refused.
        self.assertEqual(self.save_key()["questions"][0]["question_revision_id"], after["question_revision_id"])
        with self.assertRaises(ValueError):
            self.save_key(answer_key=[{"label": "x", "accept": []}])
        with self.assertRaises(Conflict):
            self.frq.save_answer_key(self.plan["id"], {"version": 0, "questions": [{"question_id": self.qid, "version": after["version"], "answer_key": []}]})

    def test_the_same_correct_answer_is_split_by_visible_work_and_never_credited_by_the_match(self):
        self.save_key()
        # The model wants full credit for both students. Only one of them showed the work the rubric pays for.
        listing = self.grade([WORKED, BARE], {tuple(WORKED): {"R1"}, tuple(BARE): {"R1"}})
        self.assertEqual((listing["responses"], len(listing["groups"])), (2, 2))
        worked = next(g for g in listing["groups"] if g["work"] == "shown")
        bare = next(g for g in listing["groups"] if g["work"] == "none")
        self.assertEqual((worked["check"], worked["readers_agree"], worked["applied"], worked["flagged"], worked["work_required"]), ("match", True, ["R1"], 0, True))
        self.assertEqual((bare["check"], bare["applied"], bare["flagged"], bare["members"][0]["state"]), ("match", ["R1"], 1, "grading_review"))
        self.assertIn("Full credit was proposed with little visible work; check that the work justifies the answer.", bare["members"][0]["reasons"])
        self.assertEqual(len(worked["members"][0]["images"]), 1)
        self.assertNotIn("enrollment_id", worked["members"][0])  # Groups name no student.
        # Nothing was approved by the match: every response still waits for the instructor.
        self.assertEqual([r["review_status"] for r in self.frq.responses(self.plan["id"], self.qid)], [None, None])
        member = bare["members"][0]
        command = {"question_id": self.qid, "group": bare["id"], "responses": [{"id": member["id"], "version": member["version"]}], "selected_items": ["R3"], "confirm_review": True}
        with self.assertRaisesRegex(ValueError, "Look at every response"):
            self.frq.approve_group(self.plan["id"], command)
        with self.assertRaises(Conflict):
            self.frq.approve_group(self.plan["id"], {**command, "inspected_each": True, "group": worked["id"]})
        with self.assertRaises(Conflict):
            self.frq.approve_group(self.plan["id"], {**command, "inspected_each": True, "responses": [{"id": member["id"], "version": member["version"] + 1}]})
        # The instructor moves the bare answer to the rubric's own no-work line, and accepts the worked one as proposed.
        self.assertEqual(self.frq.approve_group(self.plan["id"], {**command, "inspected_each": True}), {"approved": 1, "remaining": 0})
        keep = worked["members"][0]
        self.frq.approve_group(self.plan["id"], {"question_id": self.qid, "group": worked["id"], "responses": [{"id": keep["id"], "version": keep["version"]}],
            "selected_items": ["R1"], "confirm_review": True, "inspected_each": True})
        scores = {r["id"]: self.frq.response(r["id"])["review"] for r in self.frq.responses(self.plan["id"], self.qid)}
        self.assertEqual((scores[member["id"]]["score"]["score"], scores[member["id"]]["status"]), (2, "approved"))
        self.assertEqual(scores[keep["id"]]["score"]["score"], 10)
        self.assertIn("the instructor chose these rubric lines", scores[member["id"]]["comment"])
        self.assertEqual(self.frq.answer_groups(self.plan["id"], self.qid)["groups"], [])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='answer_group_approved'").fetchone()[0], 2)

    def test_a_proposal_that_contradicts_the_key_is_sent_to_the_instructor(self):
        self.save_key()
        listing = self.grade([WRONG, WORKED], {tuple(WRONG): {"R1"}, tuple(WORKED): {"R2"}})
        by_check = {g["check"]: g for g in listing["groups"]}
        self.assertIn("The final answer matches the key and work is shown, but full credit was not proposed; check the selection.", by_check["match"]["members"][0]["reasons"])
        self.assertEqual([g["flagged"] for g in listing["groups"]], [1])
        # Full credit for work whose own lines contradict each other (3x = 2, then x = 5) is no contact-sheet decision:
        # the mathematics contradicts the proposal itself, so that response is reviewed one by one.
        self.assertEqual((sorted(by_check), listing["individual"]), (["match"], 1))
        wrong = next(self.frq.response(r["id"]) for r in self.frq.responses(self.plan["id"], self.qid) if r["state"] != "proposed"
                     and "x = 5" in self.frq.response(r["id"])["result"]["transcript"]["plain_text"])
        self.assertIn("Credit for a correct answer was proposed, but the final answer does not match the answer key.", wrong["result"]["reasons"])
        self.assertTrue(any("mathematically inconsistent" in reason for reason in wrong["result"]["reasons"]))

    def test_without_a_key_answers_still_group(self):
        listing = self.grade([WORKED, WORKED], {tuple(WORKED): {"R1"}})
        self.assertEqual([(g["size"], g["check"], g["has_key"], g["answers"]) for g in listing["groups"]], [(2, "unknown", False, ["x = 6"])])

    def test_an_answer_the_model_did_not_quote_is_dropped_and_reviewed(self):
        class Inventive(ScriptedProvider):
            def grade(self, payload, images):
                result = super().grade(payload, images)
                result["output"]["interpretation"]["final_answers"] = [{"part": "x", "text": "x = 6", "line_id": "line-1"}]  # Line 1 says something else.
                return result
        self.save_key()
        self.enterContext(patch.object(self.frq, "provider", return_value=Inventive([WRONG, WRONG], {tuple(WRONG): {"R4"}})))
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "mode": "local", "operation": "recognize"}
        command["fingerprint"] = self.frq.preview(self.plan["id"], command)["fingerprint"]
        self.frq.create_job(self.plan["id"], command)
        for _ in range(60):
            if not self.frq.process_one():
                break
        group, = self.frq.answer_groups(self.plan["id"], self.qid)["groups"]
        self.assertEqual((group["answers"], group["check"]), ([], "unknown"))
        self.assertIn("A reported final answer does not quote the retained transcript.", group["members"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
