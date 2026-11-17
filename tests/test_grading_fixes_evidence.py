"""Review of 2026-09-22: evidence about a final answer must never be pieced together, glued or guessed.

Every case below reproduced a confirmed bug: a quotation accepted as a fragment of other characters, an answer's value changed by
its spelling, two numbers read as a product, a reading "agreeing" with a longer number, a crossed-out answer kept, and inputs
that hung the scoring stage or threw out of it. Synthetic contracts only; nothing here measures handwriting.
"""
import time
import unittest

from grading import answers, recognition, rubrics, symbolic
from grading.models import plain_transcript, quotable_lines, quotes, span, transcript


def reading(*lines):
    """A retained transcript; each line is a list of (text, state) elements."""
    element = lambda value, state: {"type": "math", "text": value, "latex": "", "bbox": None, "crop": 0, "confidence": .9, **({"state": state} if state else {})}
    return transcript({"plain_text": "", "latex": "", "confidence": .9, "ambiguities": [],
                       "lines": [{"id": f"line-{n}", "elements": [element(*el) for el in els]} for n, els in enumerate(lines, 1)]})


class QuotationsAreFoundAsWritten(unittest.TestCase):
    def test_a_number_is_never_cut_out_of_a_longer_one(self):
        for quotation, line in (("12", "x = -12"), ("y = 2x + 3", "y = 2x + 38"), ("x = 0.5", "x = 0.55"), ("x = 5", "x = 5.5"),
                                ("5", "0.5"), ("5", "x=.5"), ("3", "x \\leq -3"), ("40", "x = 40,000"), ("206", "46,206")):
            with self.subTest(quotation=quotation, line=line):
                self.assertFalse(quotes(quotation, line))
        # A whole number is still found beside a sentence's full stop, a subtraction or another number the student kept apart.
        for quotation, line in (("x = 5", "x = 5."), ("12", "5-12"), ("-190,000", "P(x)=42x-190,000"), ("30x/30 = -60/30", "20x + 27,y = 95     30x/30 = -60/30"),
                                ("x = 5", "so x = 5, y = 3"), ("0.07", "y - 1780 = 0.07(x - 32,571)")):
            with self.subTest(quotation=quotation, line=line):
                self.assertTrue(quotes(quotation, line))

    def test_rows_elements_cells_and_the_two_spellings_are_never_glued(self):
        for quotation, line in (("x = 25", "x = 2\n5x = 10"), ("x = 12", "x = 1 \\\\ 2y = 3"), ("5x = 10", "x = 5\nx = 10"),
                                ("5x", "x = 5\x1fx = 5"), ("[32 4; 45 9]", "[3 & 24 \\\\ 45 & 9]"), ("324", "3 & 24"), ("231/8", "23\\frac{1}{8}"),
                                ("231/8", "23 1/8")):
            with self.subTest(quotation=quotation, line=line):
                self.assertFalse(quotes(quotation, line))
        # What was always found still is: a sentence over two rows, an alignment mark, a matrix quoted row by row, a mixed number.
        aligned = "\\begin{aligned} 3x - 8 &= 10 \\\\ 3x &= 18 \\end{aligned}"
        for quotation, line in (("Not possible because the columns differ", "Not possible because the\ncolumns differ"), ("3x = 18", aligned), ("3x=18", aligned),
                                ("x=5", "x =\n5"), ("[3 24; 45 9]", "[3 & 24 \\\\ 45 & 9]"), ("23 1/8", "23\\frac{1}{8}"), ("27/2", "\\frac{27}{2}x")):
            with self.subTest(quotation=quotation, line=line):
                self.assertTrue(quotes(quotation, line))

    def test_a_parenthesis_that_changes_the_value_is_never_dropped(self):
        for quotation, line in (("23/4", "2(3)/4"), ("x-3/2", "x(-3)/2"), ("27/2x", "\\frac{27}{2x}"), ("1/23", "(1/2)3"), ("(27/2x)", "\\frac{27}{2}x")):
            with self.subTest(quotation=quotation, line=line):
                self.assertFalse(quotes(quotation, line))

    def test_a_typed_arrow_joins_rows_the_graders_way(self):
        work = "20(-2) + 27y = 95\n-40 + 27y = 95\n27y = 135"
        self.assertTrue(quotes("20(-2)+27y=95 -> -40+27y=95", work))
        self.assertFalse(quotes("20(-2)+27y=95 -> -40+27y=96", work))

    def test_a_sentence_runs_on_through_whole_lines_and_names_every_line_it_takes(self):
        lines = {"a": "x =", "b": "7", "c": "5"}
        self.assertEqual(span("x = 7", lines, "a"), ("a", ["a", "b"]))
        self.assertEqual(span("x = 5", lines, "a"), ("a", ["a", "c"]))  # "7" is passed over as side work
        self.assertIsNone(span("x = 75", {"a": "x = 7", "b": "5"}, "a"))  # two lines never make one number


class AnswersKeepTheirValue(unittest.TestCase):
    def test_a_mixed_number_is_one_number_or_unknown(self):
        for written, other, verdict in (("23 1/8", "185/8", "same"), ("23\\frac{1}{8}", "185/8", "same"), ("23 1/8", "28.875", "different"),
                                        ("-2 1/2", "-5/2", "same"), ("3 1/2x", "7x/2", "unknown"), ("2 3/2", "7/2", "unknown")):
            with self.subTest(written=written, other=other):
                self.assertEqual(answers.compare(written, other, []), verdict)
        self.assertEqual(answers.normalize("decreases by 23 1/8 units"), "decreasesby185/8")

    def test_a_parenthesis_stays_where_removing_it_would_change_the_value(self):
        for written, other in (("2(3)/4", "23/4"), ("x(-3)/2", "x-3/2"), ("(-2)^2/4", "-2^2/4"), ("27/(2x)", "(27/2)x")):
            with self.subTest(written=written):
                self.assertNotEqual(answers.normalize(written), answers.normalize(other))
                self.assertNotEqual(answers.compare(written, other, []), "same")
        # The documented spellings of one value stay one spelling.
        for written, other in (("\\frac{43}{2}x", "(43/2)x"), ("(168)/(11)", "168/11"), ("(4z)/4", "4z/4"), ("$$\\dfrac{168}{11}$$ units", "168/11")):
            with self.subTest(written=written):
                self.assertTrue(answers.equivalent(written, other))
        self.assertTrue(answers.equivalent("6130.10", "6130.1"))
        self.assertEqual(answers.compare("9t+10", "9t+18", []), "different")

    def test_a_trailing_ellipsis_and_two_numbers_side_by_side_are_unknown(self):
        self.assertEqual(answers.compare("0.333...", "0.333", []), "unknown")
        self.assertEqual(answers.compare("0.333…", "1/3", []), "unknown")
        self.assertEqual(answers.compare("1 500x", "1500x", []), "unknown")
        self.assertEqual(answers.compare("Not Possible.", "not possible"), "same")  # a full stop still ends a sentence


class TheClosedGrammarNeverMultipliesTwoNumbers(unittest.TestCase):
    def test_numbers_side_by_side_brackets_and_scientific_notation_are_indeterminate(self):
        for left, right in (("[3 4]", "[4 3]"), ("3 4x", "12x"), ("1 500x", "500x"), ("3.4.5", "1.7"), ("23\\frac{1}{8}", "23/8"), ("1e5", "e*5"),
                            ("0.333...", "0.333"), ("64x-[28x+14000]", "36x-14000")):
            with self.subTest(left=left):
                self.assertEqual(symbolic.equivalent(left, right)["result"], "indeterminate")
        self.assertEqual(symbolic.equivalent("2(x+1)", "2x+2")["result"], "pass")  # a product still reads as one
        self.assertEqual(symbolic.equivalent("2\\frac{x}{3}", "2x/3")["result"], "pass")
        self.assertEqual(symbolic.CHECKER, "symbolic-v2")


class TwoReadersAgreeOnWholeValues(unittest.TestCase):
    KEY = [{"label": "", "accept": ["x=12"]}]

    def agree(self, answer, other):
        value = plain_transcript(answer)
        by_id = {line["id"]: line["elements"][0]["text"] for line in value["lines"]}
        found, _ = answers.final_answers([{"part": "", "text": answer, "line_id": "line-1"}], by_id)
        return answers.facts(found, value, self.KEY, [{"transcript": plain_transcript(other)}])["readers_agree"]

    def test_a_longer_number_or_another_sign_is_another_reading(self):
        for other in ("x=-12", "x=125", "x = 17\nC = 120", "x = 1\n2y = 3", "x=0.12"):
            with self.subTest(other=other):
                self.assertIs(self.agree("x = 12", other), False)
        for other in ("x = 12", "so x=12 units", "x = 12.0"):
            with self.subTest(other=other):
                self.assertIs(self.agree("x = 12", other), True)
        self.assertIs(self.agree("y = -4", "y = 3-4"), False)


class CrossedOutWorkIsNeverTheFinalAnswer(unittest.TestCase):
    RULES = rubrics.validate({"mode": "positive", "items": [{"id": "R1", "description": "Correct answer", "points": 2}]}, 2)

    def grade(self, value, answer, line="line-1", evidence=("x = 5", "line-1")):
        raw = {"applications": [{"rubric_item_id": "R1", "applied": True, "reason": "", "evidence": [{"line_id": evidence[1], "student_text": evidence[0], "bbox": None, "crop": 0}],
                                 "origin_error_id": None, "confidence": .95, "ambiguity": None}],
               "interpretation": {"error_graph": [], "final_answers": [{"part": "", "text": answer, "line_id": line}]}, "confidence": .95,
               "rubric_ambiguities": [], "review_reasons": []}
        return recognition.validate_grade(raw, value, self.RULES, 2, 1)

    def test_a_struck_element_of_a_line_is_never_the_final_answer(self):
        value = reading([("x = 7", "crossed_out"), ("x = 5", None)])
        struck = self.grade(value, "x = 7", evidence=("x = 7", "line-1"))
        self.assertEqual(struck["interpretation"]["final_answers"], [])
        self.assertIn("A reported final answer quotes work the student crossed out.", struck["review_reasons"])
        self.assertEqual(len(struck["applications"][0]["evidence"]), 1)  # Rubric evidence may still cite the struck work.
        kept = self.grade(value, "x = 5")
        self.assertEqual([a["text"] for a in kept["interpretation"]["final_answers"]], ["x = 5"])
        self.assertEqual(kept["review_reasons"], [])

    def test_a_sentence_never_runs_on_through_a_struck_line(self):
        value = reading([("x =", None)], [("7", "crossed_out")], [("5", None)])
        struck = self.grade(value, "x = 7", evidence=("x =", "line-1"))
        self.assertEqual(struck["interpretation"]["final_answers"], [])
        self.assertIn("A reported final answer is on a line the student crossed out.", struck["review_reasons"])
        kept = self.grade(value, "x = 5", evidence=("x =", "line-1"))
        self.assertEqual([(a["text"], a["line_id"]) for a in kept["interpretation"]["final_answers"]], [("x = 5", "line-1")])

    def test_the_unstruck_lines_leave_out_only_what_was_crossed_out(self):
        value = reading([("x = 7", "crossed_out"), ("x = 5", "inserted")], [("y = 2", None)])
        self.assertEqual(quotable_lines(value, crossed_out=False), {"line-1": "x = 5\x1fx = 5", "line-2": "y = 2\x1fy = 2"})
        self.assertEqual(quotable_lines(value)["line-1"], "x = 7\nx = 5\x1fx = 7\nx = 5")
        kept, reasons = answers.final_answers([{"part": "", "text": "x = 7", "line_id": "line-1"}], quotable_lines(value))
        self.assertEqual((len(kept), reasons), (1, []))  # Without the unstruck lines a caller keeps the earlier behaviour.


class NothingHangsOrThrows(unittest.TestCase):
    def quickly(self, call, seconds=5):
        started = time.monotonic()
        result = call()
        self.assertLess(time.monotonic() - started, seconds)
        return result

    def test_huge_numbers_and_expansions_are_unknown_at_once(self):
        self.assertEqual(self.quickly(lambda: answers.compare("1e99999999", "5", [])), "unknown")
        self.assertEqual(self.quickly(lambda: answers.compare("1e9999999", "5")), "unknown")
        self.assertEqual(self.quickly(lambda: answers.compare("(p+q+r+s+t+u+v)^12", "x", [])), "unknown")
        self.assertEqual(self.quickly(lambda: symbolic.equivalent("(p+q+r+s+t+u+v)^12", "x"))["result"], "indeterminate")
        self.assertEqual(answers.compare("2e3", "2000"), "same")  # a small exponent is still a plain number

    def test_a_number_too_large_to_print_is_an_indeterminate_record(self):
        for huge in ("x=(((9^12)^12)^12)^12", "x=((9^12)^12)^12", "((((((((9^12)^12)^12)^12)^12)^12)^12)^12)=x"):
            checks = self.quickly(lambda: symbolic.check_lines([{"id": "l1", "text": "3x=18"}, {"id": "l2", "text": huge}]))
            self.assertTrue(checks and all(c["result"] == "indeterminate" for c in checks), checks)
        summary = symbolic.summary(symbolic.check_lines([{"id": "l1", "text": "3x - 8 = 10"}, {"id": "l2", "text": "3x = 18"}, {"id": "l3", "text": "x = 6"}]))
        self.assertEqual((summary["result"], summary["fail"]), ("pass", 0))


if __name__ == "__main__":
    unittest.main()
