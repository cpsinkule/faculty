"""MC corrections (docs/decisions/gradescope.md MC CORRECTIONS; requirement, 2026-10-22). After an MC check read, a question Gradescope scores differently from Faculty is corrected on Gradescope
by a point adjustment on its grading page (the rubric item Gradescope applied stays), read back, and listed; the scores are read again.
Real sheets are drawn and read as in test_gradescope_sheets; Gradescope is the loopback fake, its bubble-sheet versions laid out as
the real one read 2026-10-22 (one rubric item per answer and "Blank")."""
import json
import unittest

from test_gradescope_sheets import ALICE, BOB, CAROL, SheetCase, UINS
from test_bubble_sheets import HEADERS, KEY

from grading import certification_rules as rules
from grading.gradescope_mc_corrections import answer_letters, correctable
from rosters import connect


class Correctable(unittest.TestCase):
    def test_only_questions_of_the_version_both_sides_filed_the_sheet_under(self):
        same = {"gradescope": {"version": "B", "total": "50"}, "faculty": {"version": "B", "total": "54"},
                "questions": [{"number": 6, "gradescope": "0", "faculty": "4"}, {"number": 9, "gradescope": "—", "faculty": "4"}]}
        self.assertEqual([(n, str(g), str(f)) for n, g, f in correctable(same)], [(6, "0", "4")])  # a question only one side has is not
        self.assertEqual(correctable({**same, "gradescope": {"version": "A", "total": "50"}}), [])
        self.assertEqual(correctable({**same, "gradescope": {"version": "?", "total": "0"}, "questions": []}), [])
        # A question nobody graded there is Faculty's to score, whatever Download Grades showed for it.
        ungraded = {**same, "questions": [{"number": 7, "gradescope": "not graded", "faculty": "4", "ungraded": True}]}
        self.assertEqual([(n, g, str(f)) for n, g, f in correctable(ungraded)], [(7, None, "4")])

    def test_a_rubric_item_stands_for_the_letters_it_names(self):
        self.assertEqual([answer_letters(d) for d in ("B", "A, B, C, D, E", "Blank", "C, A", "Question 7", "")], ["B", "ABCDE", "", "AC", None, None])


class CorrectionCase(SheetCase):
    """Three sheets on Gradescope, whose versions have questions as on the real site; Gradescope scored Bob's Question 3 0, Faculty 4."""

    def setUp(self):
        super().setUp()
        # As on the real site: each version's questions are worth 4, one rubric item per answer worth 4 and "Blank" worth 0.
        self.questions = {}
        for letter, version in self.versions.items():
            for n in KEY:
                self.questions[(letter, n)] = self.fake.add_question(version, n, f"Question {n}", 4.0,
                                                                     [*((4.0, answer) for answer in "ABCDE"), (0.0, "Blank")])
        key = {"defaults": {"points": "4", "scoring": "exact"},
               "versions": [{"letter": letter, "questions": [{"number": n, "answer": answer} for n, answer in KEY.items()]} for letter in "AB"]}
        self.frq.save_bubble_key(self.aid, {"request_id": "key-1", "base_revision": None, **key})
        self.send()
        self.assertEqual(self.sender.drain(), 3)  # the three sheets go up first
        for uin in (ALICE[0], BOB[0], CAROL[0]):
            letter = self.on_gradescope(uin)[0]
            # Gradescope scores the sheets with its own key: every question right, except Bob's question 3, which Faculty gives 4.
            self.fake.score_sheet(self.versions[letter], self.users[uin], {n: (0.0 if uin == BOB[0] and n == 3 else 4.0) for n in KEY})
        self.bob = self.fake.qsub_of(self.versions["B"], self.users[BOB[0]], self.questions[("B", 3)])

    def corrections(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_mc_corrections ORDER BY created_at,rowid")]

    def reads(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_mc_checks ORDER BY created_at,rowid")]

    def check_mc(self):
        answer = self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/mc-check", json={}, headers=HEADERS)
        self.assertEqual(answer.status_code, 202, answer.json)

    def certification(self):
        mc = self.sender.mc_check_status(self.aid)
        facts = {"parts": ["bubble"], "gradescope": {"configured": True, "mc_check": mc}}
        return {c["id"]: c for c in rules._gradescope_checks(facts, "") if c["id"].startswith("gradescope_mc")}


class Corrections(CorrectionCase):
    def test_check_mc_corrects_gradescope_to_faculty_reads_it_back_and_lists_it(self):
        applied_before = self.fake.state(self.bob)[0]
        self.check_mc()
        self.sender.drain()  # the read, its correction, then the read again
        fix = self.corrections()
        self.assertEqual([(f["status"], f["question_number"], f["version"], f["gradescope_points"], f["faculty_points"], f["adjustment"], f["actor"])
                          for f in fix], [("accepted", 3, "B", "0", "4", "4", "automatic")])
        self.assertFalse(any(uin in json.dumps(fix) for uin in UINS))
        # Only the point adjustment changed: the item Gradescope applied ("Blank") and its comment stay; one save, nothing else written.
        applied, points, comments, score = self.fake.state(self.bob)
        self.assertEqual((applied, float(points), comments, score), (applied_before, 4.0, "", 4.0))
        self.assertEqual(len(self.fake.saves()), 1)
        self.assertEqual(json.loads(fix[0]["receipt_json"])["score"], "4")
        self.assertEqual([(r["status"], r["actor"]) for r in self.reads()], [("read", "workspace"), ("read", "automatic")])  # read again once
        mc = self.sender.mc_check_status(self.aid)
        self.assertEqual((mc["counts"]["differ"], mc["counts"]["agree"]), (0, 3))
        self.assertEqual([(c["name"], c["number"], c["gradescope"], c["faculty"], c["state"], c["settled"]) for c in mc["corrections"]],
                         [("Example, Bob", 3, "0", "4", "accepted", True)])
        # SETTLED (requirement, 2026-11-01): the read after it shows Gradescope agreeing, so Certification no longer lists it.
        self.assertEqual(self.certification(), {})
        # Until a read after the correction says so, it is flagged to look at; nothing left scored differently.
        reads = self.sender._mc_reads
        def before(db, aid):  # the last read as one made before the correction landed
            found, failed, running = reads(db, aid)
            return {k: {**v, "at": "2000-01-01T00:00:00+00:00"} for k, v in found.items()}, failed, running
        self.sender._mc_reads = before
        found = self.certification()
        self.assertEqual(sorted(found), ["gradescope_mc_corrected"])
        self.assertEqual((found["gradescope_mc_corrected"]["level"], found["gradescope_mc_corrected"]["count"]), ("confirm", 1))
        self.assertIn("Q3: Gradescope 0 → 4", found["gradescope_mc_corrected"]["rows"][0]["detail"])
        self.assertIn("corrected on Gradescope", found["gradescope_mc_corrected"]["rows"][0]["detail"])
        self.sender._mc_reads = reads
        # Checking again finds them agreeing: nothing more is corrected, nothing more written.
        self.check_mc()
        self.sender.drain()
        self.assertEqual((len(self.corrections()), len(self.fake.saves())), (1, 1))
        self.assertEqual((self.fake.forbidden_hits, self.fake.publish_hits, self.fake.comment_hits), ([], [], []))  # never published, no comment

    def test_a_hand_adjustment_on_gradescope_is_left_alone_and_stays_to_confirm(self):
        applied = self.fake.state(self.bob)[0]
        self.fake.grade(self.bob["id"], applied, points=1.0, comments="", by="Pat Grader")  # someone adjusted it there: 1
        self.check_mc()
        self.sender.drain()
        fix = self.corrections()
        self.assertEqual([(f["status"], f["gradescope_points"]) for f in fix], [("refused", "1")])
        self.assertIn("Someone put a point adjustment (1)", fix[0]["error"])
        self.assertEqual(self.fake.saves(), [])
        self.assertEqual(len(self.reads()), 1)  # nothing landed: nothing to read again
        found = self.certification()
        self.assertEqual(sorted(found), ["gradescope_mc_corrected", "gradescope_mc_differs"])
        self.assertIn("not corrected: Someone put a point adjustment", found["gradescope_mc_corrected"]["rows"][0]["detail"])
        # The instructor's own Check MC tries once more (a new record); an automatic read never does.
        self.check_mc()
        self.sender.drain()
        self.assertEqual([f["status"] for f in self.corrections()], ["refused", "refused"])

    def test_a_correction_whose_answer_was_lost_is_read_back_never_sent_twice(self):
        self.fake.fail("POST", r"/save_grade$", "drop_before")  # the connection closes, nothing kept
        self.check_mc()
        self.sender.drain()
        fix = self.corrections()[-1]
        self.assertEqual(fix["status"], "failed")
        self.assertIn("did not keep the correction", fix["error"])
        self.assertEqual(len(self.fake.saves()), 1)
        self.assertEqual(self.fake.state(self.bob)[3], 0.0)
        # One that landed although its answer was lost is found there by the read back.
        self.fake.fail("POST", r"/save_grade$", "drop_after")
        self.check_mc()
        self.sender.drain()
        self.assertEqual([f["status"] for f in self.corrections()], ["failed", "accepted"])
        self.assertEqual(self.fake.state(self.bob)[3], 4.0)

    def ungrade_bob(self):
        """Gradescope's reader leaves Bob's question 3 for a person (Exam 2's 515, 2026-10-28): no evaluation, no item, no score."""
        self.bob.update(evaluations=None, points=None, comments=None, graded=False, by=None)

    def test_a_question_nobody_graded_on_gradescope_gets_facultys_answer_and_score(self):
        self.ungrade_bob()
        self.check_mc()
        self.sender.drain()  # the read finds the question ungraded on its page, grades it, then reads again
        fix = self.corrections()
        self.assertEqual([(f["status"], f["question_number"], f["gradescope_points"], f["faculty_points"], f["adjustment"]) for f in fix],
                         [("accepted", 3, "", "4", "0")])
        applied, points, comments, score = self.fake.state(self.bob)
        item = self.fake.items[applied[0]]
        self.assertEqual((len(applied), item["description"], points, score), (1, KEY[3], None, 4.0))  # the answer Faculty read, no adjustment
        self.assertEqual(len(self.fake.saves()), 1)
        mc = self.sender.mc_check_status(self.aid)
        self.assertEqual((mc["counts"]["not_graded"], mc["counts"]["agree"], mc["counts"]["differ"]), (0, 3, 0))  # graded there now
        self.assertEqual([(c["number"], c["gradescope"], c["faculty"], c["state"]) for c in mc["corrections"]], [(3, "not graded", "4", "accepted")])
        self.assertEqual(self.certification(), {})  # settled: Gradescope graded it as Faculty does, read since
        self.assertEqual((self.fake.publish_hits, self.fake.comment_hits), ([], []))

    def test_two_filled_bubbles_gradescope_has_no_answer_for_are_left_to_grade_there(self):
        self.ungrade_bob()
        self.sender._mc_marked = lambda unit, upload: "AB"  # what Faculty read: two bubbles, which Gradescope did not group
        self.check_mc()
        self.sender.drain()
        fix = self.corrections()
        self.assertEqual([f["status"] for f in fix], ["refused"])
        self.assertIn("no answer of its own for what Faculty read (A, B); grade it there", fix[0]["error"])
        self.assertEqual(self.fake.saves(), [])
        mc = self.sender.mc_check_status(self.aid)
        self.assertEqual(mc["counts"]["not_graded"], 1)
        self.assertEqual([(q["number"], q["gradescope"]) for q in mc["differ"][0]["questions"]], [(3, "not graded")])
        self.assertIn("not corrected: Gradescope has no answer of its own", self.certification()["gradescope_mc_corrected"]["rows"][0]["detail"])

    def test_a_refused_question_gradescope_later_graded_as_faculty_does_is_no_longer_listed(self):
        # Exam 2's four Version C makeup sheets (2026-10-31): read seconds after upload, before Gradescope's reader had made their
        # answers' items, so 27 were refused; Gradescope graded them itself minutes later, as Faculty scores them, and the Certify box
        # still listed all 84 corrections (requirement, 2026-11-01).
        self.ungrade_bob()
        self.sender._mc_marked = lambda unit, upload: "AB"
        self.check_mc()
        self.sender.drain()
        self.assertEqual([f["status"] for f in self.corrections()], ["refused"])
        self.assertIn("gradescope_mc_corrected", self.certification())
        self.fake.score_sheet(self.versions["B"], self.users[BOB[0]], {n: 4.0 for n in KEY})  # Gradescope grades it itself: 4
        self.check_mc()
        self.sender.drain()
        mc = self.sender.mc_check_status(self.aid)
        self.assertEqual((mc["counts"]["agree"], mc["counts"]["differ"]), (3, 0))
        self.assertEqual([(c["state"], c["settled"]) for c in mc["corrections"]], [("refused", True)])  # the panel keeps it, settled
        self.assertEqual(self.certification(), {})
        self.assertEqual(self.fake.saves(), [])

    def test_a_sheet_replaced_on_gradescope_is_never_corrected(self):
        submission = self.fake.submission_of(self.versions["B"], self.users[BOB[0]])
        submission["filename"] = "someone-else.pdf"
        self.check_mc()
        self.sender.drain()
        self.assertEqual([f["status"] for f in self.corrections()], ["refused"])
        self.assertEqual(self.fake.saves(), [])


if __name__ == "__main__":
    unittest.main()
