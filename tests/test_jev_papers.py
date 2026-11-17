"""JEV PAPERS (requirement, 2026-10-15): Jev looks at scanned pages (Jev 1.14, images and Box) to find first pages and read whose paper
each is. Synthetic pages and rosters; no provider is reached."""
import io
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from app import create_app
from rosters import connect
from jev import client
from document_identity import jev_papers


def picture(size=(1700, 2200)):
    out = io.BytesIO()
    Image.new("L", size, 255).save(out, format="PNG")
    return out.getvalue()


STUDENTS = {"1000000001": {"name": "Dawson, Calvin Allan", "uin": "900000305", "section_id": "s514"},
            "1000000002": {"name": "Sanders, Calvin C", "uin": "900000302", "section_id": "s515"},
            "1000000003": {"name": "Porter, Gina", "uin": "900000304", "section_id": "s514"},
            "1000000004": {"name": "Porter, Gina", "uin": "111222333", "section_id": "s515"}}


class FakeJev:
    """Answers each question from `script` {question name prefix: option words}; both orders answer alike unless `split` names one."""
    def __init__(self, script, split=()):
        self.script, self.split, self.calls = script, set(split), []

    def ask(self, purpose, owner, state, questions, rule, enrollment=None, attempt="", images=(), receipt=None):
        self.calls.append((purpose, sorted(questions), len(images), receipt))
        answers = {}
        for name, question in questions.items():
            base = name.split("__")[0]
            wanted = self.script.get(base, next(iter(question["criteria"])))
            if base in self.split and name.endswith("__reversed"):
                wanted = next(o for o in question["criteria"] if o != wanted)
            if wanted not in question["criteria"]:
                wanted = next(o for o in question["criteria"] if o in (jev_papers.NONE, jev_papers.NO_UIN))
            answers[name] = {"type": "choice", "choice": wanted, "confidence": .9, "probabilities": {}}
        result = {"model": client.IMAGE_MODEL, "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}
        result = receipt["result"](result) if receipt else result
        return {"status": "complete", "id": f"call{len(self.calls)}", **result}


CHECK = {"status": "complete", "orientation": "upright", "name_box": {"top": .05, "bottom": .1, "left": .1, "right": .5},
         "uin_box": {"top": .1, "bottom": .15, "left": .5, "right": .9}}


class Identify(unittest.TestCase):
    def test_real_names_and_sentinel_cannot_collide_with_generated_labels(self):
        names = ["Lee", "Lee", "Lee (2)", jev_papers.NONE]
        students = {str(i): {"name": name, "uin": f"{i:09}", "section_id": "section"} for i, name in enumerate(names, 1)}
        labels = jev_papers._labels(students, students)
        self.assertEqual(set(labels.values()), set(students))
        self.assertEqual(labels["Lee (2)"], "3")
        self.assertEqual(labels["Lee (3)"], "2")
        self.assertNotIn(jev_papers.NONE, labels)
        found = jev_papers.identify(FakeJev({"roster0_0": "Lee (3)", "name": "Lee (3)", "uin": "000000002"}), "page", picture(), CHECK, students)
        self.assertEqual(found["enrollment_id"], "2")

    def test_a_student_two_rosters_pick_is_settled_by_the_name_and_uin_rounds(self):
        # Measured 2026-10-15: "Calvin Dawson" drew Calvin Sanders from the other section's roster at .47; the rounds among the
        # candidates settle it, and the UIN round agrees.
        jev = FakeJev({"roster0_0": "Dawson, Calvin Allan", "roster1_0": "Sanders, Calvin C", "name": "Dawson, Calvin Allan", "uin": "900000305"})
        found = jev_papers.identify(jev, "page", picture(), CHECK, STUDENTS)
        self.assertEqual((found["enrollment_id"], found["candidates"], found["name_round"], found["uin_round"]),
                         ("1000000001", ["1000000001", "1000000002"], "1000000001", "1000000001"))
        roster, named, numbered = jev.calls
        self.assertEqual([call[0] for call in jev.calls], [jev_papers.STUDENT_PURPOSE] * 3)
        self.assertEqual(roster[1], ["roster0_0", "roster0_0__reversed", "roster1_0", "roster1_0__reversed"])
        self.assertEqual((named[1], numbered[1]), (["name", "name__reversed"], ["uin", "uin__reversed"]))

    def test_one_round_decides_when_the_other_holds_and_contradictions_wait(self):
        # Katia wrote a shorter name than her roster name: the name round's orders disagree, and the UIN round decides.
        held = FakeJev({"roster0_0": "Dawson, Calvin Allan", "roster1_0": "Sanders, Calvin C", "uin": "900000302"}, split={"name"})
        self.assertEqual(jev_papers.identify(held, "page", picture(), CHECK, STUDENTS)["enrollment_id"], "1000000002")
        # No UIN written: the UIN round chooses none of them, and the name round decides.
        blank = FakeJev({"roster0_0": "Porter, Gina", "roster1_0": jev_papers.NONE, "name": "Porter, Gina", "uin": jev_papers.NO_UIN})
        self.assertEqual(jev_papers.identify(blank, "page", picture(), CHECK, STUDENTS)["enrollment_id"], "1000000003")
        # The name and the UIN point to different students: nobody is identified.
        split = FakeJev({"roster0_0": "Dawson, Calvin Allan", "roster1_0": "Sanders, Calvin C", "name": "Dawson, Calvin Allan", "uin": "900000302"})
        found = jev_papers.identify(split, "page", picture(), CHECK, STUDENTS)
        self.assertEqual((found["enrollment_id"], found["reason"]), (None, "The name and the UIN point to different students."))
        # Nobody on any roster: no rounds are asked.
        nobody = FakeJev({"roster0_0": jev_papers.NONE, "roster1_0": jev_papers.NONE})
        self.assertEqual(jev_papers.identify(nobody, "page", picture(), CHECK, STUDENTS)["enrollment_id"], None)
        self.assertEqual(len(nobody.calls), 1)
        # Two students share a name: each is its own option, and the receipt maps both apart.
        twins = FakeJev({"roster0_0": "Porter, Gina", "roster1_0": "Porter, Gina (2)", "name": "Porter, Gina (2)", "uin": "111222333"})
        self.assertEqual(jev_papers.identify(twins, "page", picture(), CHECK, STUDENTS)["enrollment_id"], "1000000004")

    def test_an_unchecked_page_is_not_matched(self):
        jev = FakeJev({})
        self.assertEqual(jev_papers.identify(jev, "page", picture(), {"status": "failed"}, STUDENTS)["status"], "not_asked")
        self.assertEqual(jev.calls, [])


class WhichAssignment(unittest.TestCase):
    ASSIGNMENTS = [{"id": "a1", "title": "Quiz 3", "description": jev_papers.describe("MATH 140 Fall 2026 Chapter 3 Quiz")},
                   {"id": "a2", "title": "Exam 1", "description": jev_papers.describe("")}]

    def test_real_assignment_suffix_and_none_option_remain_distinct(self):
        assignments = [{"id": str(i), "title": name} for i, name in enumerate(["Quiz", "Quiz", "Quiz (2)", jev_papers.NO_ASSIGNMENT], 1)]
        for label, expected in (("Quiz", "1"), ("Quiz (3)", "2"), ("Quiz (2)", "3"), (jev_papers.NO_ASSIGNMENT, None),
                                (jev_papers.NO_ASSIGNMENT + " (2)", "4")):
            with self.subTest(label=label):
                found = jev_papers.which_assignment(FakeJev({"assignment": label}), "page", picture(), assignments)
                self.assertEqual(found["assignment_id"], expected)

    def test_an_assignment_counts_only_when_both_orders_choose_it(self):
        found = jev_papers.which_assignment(FakeJev({"assignment": "Quiz 3"}), "page", picture(), self.ASSIGNMENTS)
        self.assertEqual((found["assignment_id"], found["none"]), ("a1", False))
        none = jev_papers.which_assignment(FakeJev({"assignment": jev_papers.NO_ASSIGNMENT}), "page", picture(), self.ASSIGNMENTS)
        self.assertEqual((none["assignment_id"], none["none"]), (None, True))
        split = jev_papers.which_assignment(FakeJev({"assignment": "Quiz 3"}, split={"assignment"}), "page", picture(), self.ASSIGNMENTS)
        self.assertEqual((split["assignment_id"], split["none"]), (None, False))
        jev = FakeJev({"assignment": "Exam 1"})
        jev_papers.which_assignment(jev, "page", picture(), self.ASSIGNMENTS)
        self.assertEqual(jev.calls[0][0], jev_papers.ASSIGNMENT_PURPOSE)
        self.assertEqual(jev_papers.describe(""), "No blank paper is on file yet.")
        self.assertEqual(len(jev_papers.describe("x " * 400)) - len('Its blank paper begins: ""'), jev_papers.DESCRIBED)


class Client(unittest.TestCase):
    def test_a_check_over_pictures_asks_jev_114_with_the_images_header_and_validates_boxes(self):
        questions = {"name": {"type": "box", "instructions": "Find the name."}, "has_name": {"type": "noul", "instructions": "A name?"}}
        answer = {"model": client.IMAGE_MODEL, "usage": {"input_tokens": 3, "output_tokens": 2},
                  "answers": {"name": {"type": "box", "box": {"top": .1, "bottom": .2, "left": .3, "right": .6}, "confidence": .8},
                              "has_name": {"type": "noul", "noul": .97}}}
        reply = Mock(status=200)
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        reply.read = Mock(return_value=json.dumps(answer).encode())
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "synthetic-secret", "FACULTY_JEV_ENABLED": "1"}), \
                patch("jev.client.open_request", return_value=reply) as post:
            result = client.evaluate("A page.", questions, [client.prepare_image(picture((3400, 4400)))])
        request = post.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual((body["model"], request.get_header("Typesafe-danger-zone-images")), (client.IMAGE_MODEL, "true"))
        ref = body["state"]["image"]
        self.assertEqual((body["state"]["description"], list(body["assets"]), body["assets"][ref]["format"]), ("A page.", [ref], "png"))
        shrunk = Image.open(io.BytesIO(__import__("base64").b64decode(body["assets"][ref]["data_base64"])))
        self.assertLessEqual(max(shrunk.size), 1568)
        self.assertEqual(result["answers"]["name"]["box"]["left"], .3)
        # A box with its edges out of order or outside the picture is no answer.
        for bad in ({"top": .3, "bottom": .2, "left": .1, "right": .2}, {"top": .1, "bottom": 1.2, "left": .1, "right": .2}, {"top": .1}):
            with self.subTest(bad=bad), self.assertRaises(client.DecisionError):
                client.validate({**answer, "answers": {**answer["answers"], "name": {"type": "box", "box": bad, "confidence": .5}}},
                                questions, client.IMAGE_MODEL)
        # A text check still asks the pinned text model, without the header.
        text = {**answer, "model": client.MODEL, "answers": {"has_name": answer["answers"]["has_name"]}}
        reply.read = Mock(return_value=json.dumps(text).encode())
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "synthetic-secret", "FACULTY_JEV_ENABLED": "1"}), \
                patch("jev.client.open_request", return_value=reply) as post:
            client.evaluate("Words.", {"has_name": questions["has_name"]})
        self.assertEqual((json.loads(post.call_args.args[0].data)["model"], post.call_args.args[0].get_header("Typesafe-danger-zone-images")),
                         (client.MODEL, None))

    def test_small_pictures_are_padded_never_stretched(self):
        small = Image.open(io.BytesIO(client.prepare_image(picture((600, 120)))))
        self.assertEqual(small.size, (600, 280))


class Receipts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.jev = self.app.extensions["jev"]

    def test_a_roster_check_keeps_enrollment_ids_and_picture_hashes_never_names_or_pictures(self):
        seen = []

        def evaluate(state, questions, images=()):
            seen.append((state, sorted(questions), len(images)))
            return {"model": client.IMAGE_MODEL, "usage": {"input_tokens": 1, "output_tokens": 1},
                    "answers": {name: {"type": "choice", "choice": "Dawson, Calvin Allan", "confidence": .95, "probabilities": {}} for name in questions}}

        students = {key: STUDENTS[key] for key in ("1000000001", "1000000003")}
        jev = FakeJev({})
        jev.ask = self.jev.ask
        with patch("jev.client.available", return_value=True), patch("jev.client.evaluate", side_effect=evaluate):
            found = jev_papers.identify(jev, "page-1", picture(), {**CHECK, "uin_box": CHECK["name_box"]}, students)
            again = jev_papers.identify(jev, "page-1", picture(), {**CHECK, "uin_box": CHECK["name_box"]}, students)
        self.assertEqual(found["enrollment_id"], "1000000001")
        self.assertEqual(again["enrollment_id"], "1000000001")
        self.assertEqual(len(seen), 3)  # The roster, name and UIN rounds, asked once; the repeat reads their receipts.
        with connect(self.app.config["DATABASE"]) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM jev_calls ORDER BY created_at")]
        self.assertEqual({row["model"] for row in rows}, {client.IMAGE_MODEL})
        kept = json.dumps(rows)
        for words in ("Dawson", "Porter", "900000305"):
            self.assertNotIn(words, kept)
        self.assertEqual(json.loads(rows[0]["result_json"])["answers"]["roster0_0"]["choice"], "1000000001")


class BlankAnswers(unittest.TestCase):
    """JEV BLANK ANSWERS (grading/jev_blank.py): which held zeros Jev is shown, and what its answer does."""
    def output(self, **change):
        from grading import risk
        reason = risk.blank_reason(1)
        base = {"state": "ocr_review", "score": {"score": 0}, "transcript": {"lines": []}, "reasons": [reason],
                "signals": {"risk": {"blocks": [reason], "individual": True, "components": {"reading": {"blank_crops": 1},
                                                                                           "committee": {"status": "resolved"}}}}}
        for key, value in change.items():
            base[key] = value
        return base

    def test_only_a_zero_held_by_the_empty_reading_alone_is_eligible(self):
        from grading import jev_blank, risk
        crops = {"crops": [{"metadata": {"registered": True}}]}
        self.assertTrue(jev_blank.eligible(self.output(), crops))
        # AN UNALIGNED CROP (2026-11-06): its own hold is part of the check; Jev is shown the crop as it was cut.
        unaligned = self.output(signals={"risk": {**self.output()["signals"]["risk"], "blocks": [risk.blank_reason(1), risk.CROP_UNSURE]}})
        self.assertTrue(jev_blank.eligible(unaligned, {"crops": [{"metadata": {"registered": False}}]}))
        self.assertTrue(jev_blank.eligible(self.output(), {"crops": [{"metadata": {"registered": False}}]}))
        for name, output, areas in (
                ("credit", self.output(score={"score": 1}), crops),
                ("something transcribed", self.output(transcript={"lines": [{"text": "x"}]}), crops),
                ("a second hold", self.output(state="both"), crops),
                ("another block", self.output(signals={"risk": {"blocks": [risk.blank_reason(1), risk.EMPTY_SELECTION], "components": {"reading": {"blank_crops": 1}}}}), crops),
                ("one area of two empty", self.output(), {"crops": [{"metadata": {"registered": True}}] * 2}),
                ("a rubric concern", self.output(signals={"risk": self.output()["signals"]["risk"], "rubric_quality": {"reasons": ["x"]}}), crops)):
            with self.subTest(name):
                self.assertFalse(jev_blank.eligible(output, areas))

    def test_jev_at_the_cutoff_settles_the_empty_reading_and_anything_else_leaves_it(self):
        from grading import jev_blank
        for noul, status, settled in ((.8, "complete", True), (.79, "complete", False), (None, "uncertain", False)):
            with self.subTest(noul=noul, status=status):
                found = {"rule": jev_blank.RULE, "status": status, **({"noul": noul} if noul is not None else {})}
                output, verdict = jev_blank.apply(self.output(), {"blocks": ["held"], "individual": True}, found)
                self.assertEqual(output["state"], "proposed" if settled else "ocr_review")
                self.assertEqual((verdict["blocks"], output["signals"]["risk"]["blocks"] == []), ([] if settled else ["held"], settled))
                self.assertEqual(output["signals"]["jev_blank"].get("outcome"), "blank" if settled else None)


if __name__ == "__main__":
    unittest.main()
