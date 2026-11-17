"""MC LABELS (docs/decisions/gradescope.md; requirement, 2026-10-28). Label answers renames each of Gradescope's bubble-sheet answer items so it says whether it is right
and what the answer is, by Faculty's key; only the description changes, a lost answer is settled by reading only, and Faculty still
reads a renamed item. Gradescope is the loopback fake, its versions laid out as the real Exam 2 read 2026-10-28: the key's answer worth
the question's points, every other answer met and "Blank" worth 0."""
import json
import unittest

from test_gradescope_sheets import SheetCase
from test_bubble_sheets import HEADERS, KEY

from grading.gradescope_mc_corrections import answer_letters
from grading.gradescope_mc_labels import answer_label
from rosters import connect


class Labels(unittest.TestCase):
    def test_a_label_says_whether_the_answer_is_right_and_what_the_answer_is(self):
        self.assertEqual(answer_label("D", "D", "exact", "4"), "D: correct")
        self.assertEqual(answer_label("A", "D", "exact", "4"), "A: incorrect (correct answer: D)")
        self.assertEqual(answer_label("", "D", "exact", "4"), "Blank: no answer (correct answer: D)")
        self.assertEqual(answer_label("AC", "D", "exact", "4"), "A, C: incorrect (correct answer: D)")
        self.assertEqual(answer_label("A", "AC", "partial", "2.5"), "A: partial credit (correct answer: A and C)")
        self.assertEqual(answer_label("AC", "AC", "partial", "2.5"), "A, C: correct")
        self.assertEqual(answer_label("D", "BD", "either", "4"), "D: correct")
        self.assertEqual(answer_label("A", "BD", "either", "4"), "A: incorrect (correct answer: B or D)")
        self.assertEqual(answer_label("", "ABCDE", "either", "4"), "Blank: correct (every answer accepted)")

    def test_faculty_still_reads_the_letters_of_a_labelled_item(self):
        self.assertEqual([answer_letters(d) for d in ("A: incorrect (correct answer: D)", "Blank: no answer (correct answer: D)", "A, C: correct",
                                                      "Question 7: work shown")], ["A", "", "AC", None])


class LabelCase(SheetCase):
    def setUp(self):
        super().setUp()
        self.items = {}
        for letter, version in self.versions.items():
            for n, answer in KEY.items():
                question = self.fake.add_question(version, n, f"Question {n}", 4.0, [])
                # As Gradescope makes them: the key's answer first, then each answer it met, then "Blank"; one a person added.
                for position, (points, said) in enumerate([(4.0, answer), (0.0, "A" if answer != "A" else "B"), (0.0, "Blank")]
                                                           + ([(1.0, "Work shown")] if n == 1 else [])):
                    self.items[(letter, n, said)] = self.fake._item(question, position, points, said, None)
        key = {"defaults": {"points": "4", "scoring": "exact"},
               "versions": [{"letter": letter, "questions": [{"number": n, "answer": answer} for n, answer in KEY.items()]} for letter in "AB"]}
        self.frq.save_bubble_key(self.aid, {"request_id": "key-1", "base_revision": None, **key})
        self.send()
        self.assertEqual(self.sender.drain(), 3)

    def label(self):
        answer = self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/mc-labels", json={}, headers=HEADERS)
        self.assertEqual(answer.status_code, 202, answer.json)
        return answer.json

    def runs(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_mc_labels ORDER BY created_at,rowid")]

    def said(self, letter, n, original):
        return self.fake.items[self.items[(letter, n, original)]]["description"]


class LabelAnswers(LabelCase):
    def test_each_answer_says_whether_it_is_right_and_what_the_answer_is_and_nothing_else_changes(self):
        weights = {i: dict(item) for i, item in self.fake.items.items()}
        # The send queued a run of its own, waiting for Gradescope to grade the sheets ("yes for future exams"); Label answers makes it go now.
        self.assertEqual([(r["status"], r["actor"], bool(r["not_before"])) for r in self.runs()], [("pending", "automatic", True)])
        self.sender.drain()
        self.assertEqual(self.fake.renamed, [])
        self.label()
        self.sender.drain()
        answer = KEY[2]
        self.assertEqual((self.said("A", 2, answer), self.said("A", 2, "A"), self.said("B", 2, "Blank")),
                         (f"{answer}: correct", f"A: incorrect (correct answer: {answer})", f"Blank: no answer (correct answer: {answer})"))
        self.assertEqual(self.said("A", 1, "Work shown"), "Work shown")  # a person's own item is left alone
        self.assertEqual(self.said("C", 2, answer), answer)  # Version C has no key in Faculty: left as Gradescope named it
        # Only descriptions changed: points, groups, positions stay; nothing graded, published or commented.
        self.assertEqual({i: (w["weight"], w["group"], w["position"]) for i, w in weights.items()},
                         {i: (w["weight"], w["group"], w["position"]) for i, w in self.fake.items.items()})
        self.assertEqual(len(self.fake.renamed), 2 * len(KEY) * 3)
        self.assertNothingForbidden()
        runs = self.runs()
        self.assertEqual([(r["status"], r["actor"]) for r in runs], [("accepted", "automatic")])
        receipt = json.loads(runs[0]["receipt_json"])
        self.assertEqual((receipt["counts"]["renamed"], receipt["counts"]["missing"]), (60, 0))
        self.assertTrue(all(i["landed"] for i in receipt["items"]))
        labels = self.sender.mc_check_status(self.aid)["labels"]
        self.assertEqual((labels["running"], labels["renamed"], [(s["section"], s["state"]) for s in labels["sections"]]), (False, 60, [(501, "accepted")]))
        # Labelling again finds every answer labelled: a new run, nothing renamed.
        self.label()
        self.sender.drain()
        self.assertEqual([r["status"] for r in self.runs()], ["accepted", "accepted"])
        self.assertEqual(len(self.fake.renamed), 60)
        self.assertEqual(json.loads(self.runs()[-1]["receipt_json"])["counts"]["labelled"], 60)

    def test_a_rename_whose_answer_was_lost_is_read_back_never_sent_again(self):
        self.fake.fail("PUT", r"/rubric_items/\d+$", "drop_before")  # the connection closes, nothing kept
        self.label()
        self.sender.drain()
        run = self.runs()[-1]
        # An MC check read after a run that did not finish queues nothing by itself: finishing is the instructor's Label again.
        self.assertEqual(self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/mc-check", json={}, headers=HEADERS).status_code, 202)
        self.sender.drain()
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(run["status"], "failed")
        self.assertIn("still carry only their letters", run["error"])
        self.assertEqual(self.fake.renamed, [])  # read back only: nothing was renamed by itself
        # One that landed although its answer was lost is found there by the read back; the instructor's Label again finishes.
        self.fake.fail("PUT", r"/rubric_items/\d+$", "drop_after")
        self.label()
        self.sender.drain()
        self.assertEqual(self.runs()[-1]["status"], "failed")
        self.assertEqual(len(self.fake.renamed), 1)
        self.label()
        self.sender.drain()
        self.assertEqual(self.runs()[-1]["status"], "accepted")
        self.assertEqual(json.loads(self.runs()[-1]["receipt_json"])["counts"]["renamed"], 59)

    def test_every_mc_check_labels_the_answers_gradescope_met_since(self):
        self.label()
        self.sender.drain()
        # Gradescope meets a new answer on a later sheet and makes its item, named by its letters only.
        question = self.fake.items[self.items[("A", 2, KEY[2])]]["question"]
        new = self.fake._item(question, 9, 0.0, "E" if KEY[2] != "E" else "C", None)
        self.assertEqual(self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/mc-check", json={}, headers=HEADERS).status_code, 202)
        self.sender.drain()
        self.assertEqual(self.fake.items[new]["description"], f"{'E' if KEY[2] != 'E' else 'C'}: incorrect (correct answer: {KEY[2]})")
        self.assertEqual([(r["status"], r["actor"]) for r in self.runs()], [("accepted", "automatic"), ("accepted", "automatic")])
        self.assertEqual(json.loads(self.runs()[-1]["receipt_json"])["counts"]["renamed"], 1)

    def test_an_exam_linked_before_the_instructor_said_yes_is_labelled_only_by_their_own_button(self):
        # As Exam 2 (linked 2026-10-26): a plan whose exams were linked before "yes for future exams" queues nothing by itself.
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("DROP TRIGGER frq_gradescope_sheet_target_immutable")
            db.execute("UPDATE frq_gradescope_sheet_targets SET created_at='2026-10-26T12:30:11+00:00'")
            db.execute("DROP TRIGGER frq_gradescope_mc_label_retained")
            db.execute("DELETE FROM frq_gradescope_mc_labels")
        self.assertEqual(self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/mc-check", json={}, headers=HEADERS).status_code, 202)
        self.sender.drain()
        self.assertEqual((self.runs(), self.fake.renamed), ([], []))
        # Once the instructor labelled it, later answers are labelled by themselves.
        self.label()
        self.sender.drain()
        self.assertEqual(self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/mc-check", json={}, headers=HEADERS).status_code, 202)
        self.sender.drain()
        self.assertEqual([r["actor"] for r in self.runs()], ["workspace", "automatic"])

    def test_a_plan_with_no_linked_bubble_sheet_exam_queues_nothing(self):
        before = self.runs()  # the send's own run
        other = self.client.post("/api/grading/gradescope/assignments/nope/mc-labels", json={}, headers=HEADERS)
        self.assertEqual(other.status_code, 404)
        self.assertEqual(self.runs(), before)


if __name__ == "__main__":
    unittest.main()
