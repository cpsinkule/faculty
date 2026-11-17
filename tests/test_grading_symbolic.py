"""Deterministic symbolic checks: evidence about what was written, parsed by a closed grammar, never by eval."""
import unittest

from grading import answers, symbolic


def lines(*texts):
    return [{"id": f"line-{i+1}", "text": text} for i, text in enumerate(texts)]


class ClosedGrammar(unittest.TestCase):
    def test_equivalent_forms_the_real_rubric_lists_as_alternatives(self):
        for left, right in (("21.5(x-3270)+79134", "21.5x+8829"), ("x^2-1", "(x-1)(x+1)"), (r"\frac{168}{11}", "168/11"), ("40,000", "40000"),
                            ("sqrt(16)", "4"), ("e^(2x)", "exp(2x)"), ("x**2", "x^2"), ("2(x+1)", "2x+2"), (r"3 \cdot x", "3x")):
            with self.subTest(left=left):
                self.assertEqual(symbolic.equivalent(left, right)["result"], "pass", (left, right))
        for left, right in (("2x+1", "2x+2"), ("x^2", "x^3"), ("1/3", "0.33")):
            self.assertEqual(symbolic.equivalent(left, right)["result"], "fail", (left, right))  # No tolerance: 0.33 is not a third.

    def test_untrusted_text_is_never_evaluated_and_unknown_notation_is_indeterminate(self):
        hostile = ("__import__('os').system('true')", "x.__class__", "lambda: 1", "open('/etc/passwd')", "9**9**9**9", "1/0", "x" * 500,
                   "(" * 60 + "x" + ")" * 60, "the answer is six", "x = = 3", "∫ x dx", "[[1,2],[3,4]]", "")
        for text in hostile:
            with self.subTest(text=text[:30]):
                found = symbolic.equivalent(text, "1")
                self.assertEqual(found["result"], "indeterminate")
                self.assertTrue(found["explanation"])
        with self.assertRaises(symbolic.Unparsed):
            symbolic.parse("x^(9^9)")
        self.assertEqual(str(symbolic.parse("21.5")), "43/2")  # Exact, never a float.

    def test_every_check_is_a_record_for_the_audit(self):
        found = symbolic.equivalent("2x", "x+x")
        self.assertEqual(set(found), {"type", "input", "result", "explanation", "checker"})
        self.assertEqual((found["type"], found["input"], found["checker"]), ("equivalent_forms", {"left": "2x", "right": "x+x"}, symbolic.CHECKER))


class WrittenWork(unittest.TestCase):
    def test_the_inconsistent_response_of_the_goal_is_found_inconsistent(self):
        checks = symbolic.check_lines(lines("3x - 8 = 10", "3x = 18", "x = 8"))
        summary = symbolic.summary(checks)
        self.assertEqual((summary["result"], summary["fail"]), ("fail", 2))
        self.assertIn("“3x = 18” gives x = 6, but “x = 8” gives x = 8.", summary["contradictions"])  # 18 / 3 is not 8
        self.assertIn("With x = 8 the first equation reads 16 = 10, which is false.", summary["contradictions"])  # 3(8) - 8 is not 10
        first = next(c for c in checks if c["type"] == "equation_chain" and c["result"] == "fail")
        self.assertEqual(first["input"]["to"], {"line_id": "line-3", "text": "x = 8"})  # It quotes the line; it never repairs it.

    def test_clear_correct_work_passes_without_a_single_failure(self):
        summary = symbolic.summary(symbolic.check_lines(lines("3x - 8 = 10", "3x = 10 + 8", "3x = 18", "x = 6")))
        self.assertEqual((summary["result"], summary["fail"], summary["indeterminate"]), ("pass", 0, 0))

    def test_an_arithmetic_slip_is_located_where_it_happens(self):
        checks = symbolic.check_lines(lines("2x - 3 = 7", "2x = 4", "x = 2"))
        failed = [c for c in checks if c["result"] == "fail"]
        self.assertEqual(failed[0]["input"]["to"]["line_id"], "line-2")  # 7 + 3 is not 4; the later step x = 2 follows from it correctly.
        self.assertEqual([c["result"] for c in checks if c["type"] == "equation_chain"], ["fail", "pass"])
        self.assertEqual(symbolic.summary(symbolic.check_lines(lines("18/3 = 8")))["contradictions"], ["The two sides are different numbers: 6 is not 8."])

    def test_ordinary_algebra_is_not_called_a_contradiction(self):
        self.assertEqual(symbolic.summary(symbolic.check_lines(lines("x^2 = 4", "x = 2")))["fail"], 0)  # Keeping one root is a domain choice.
        self.assertEqual(symbolic.summary(symbolic.check_lines(lines("x^2 = -1", "x = 5")))["fail"], 2)

    def test_prose_and_unknown_notation_are_indeterminate_never_guessed(self):
        summary = symbolic.summary(symbolic.check_lines(lines("Divide both sides by 3.", "x = 8", "so the answer is 8 units")))
        self.assertEqual((summary["result"], summary["fail"]), ("indeterminate", 0))
        checks = symbolic.check_lines(lines("∫ 2x dx = x^2 + C"))
        self.assertEqual([c["result"] for c in checks], ["indeterminate"])
        self.assertEqual(symbolic.check_lines([]), [])


class AnswerKeyMatcher(unittest.TestCase):
    def test_another_form_of_the_key_matches_but_the_notation_keeps_its_own_group(self):
        transcript = {"lines": [{"id": "l1", "elements": [{"text": "C(x)=21.5(x-3270)+79134"}]}]}
        key = [{"label": "", "accept": ["C(x)=21.5x+8829"]}]
        written = [{"part": "", "text": "C(x)=21.5(x-3270)+79134", "line_id": "l1"}]
        found = answers.facts(written, transcript, key)
        self.assertEqual(found["check"], "match")
        self.assertEqual(found["symbolic"][0]["result"], "pass")
        # The same value in a different notation stays a separate group: whether notation earns credit is the rubric's business.
        expanded = answers.facts([{"part": "", "text": "C(x)=21.5x+8829", "line_id": "l1"}], {"lines": [{"id": "l1", "elements": [{"text": "C(x)=21.5x+8829"}]}]}, key)
        bare = answers.facts([{"part": "", "text": "21.5x+8829", "line_id": "l1"}], {"lines": [{"id": "l1", "elements": [{"text": "21.5x+8829"}]}]}, key)
        signatures = {answers.signature("q", f, {"R1"}) for f in (found, expanded, bare)}
        self.assertEqual((expanded["check"], bare["check"], len(signatures)), ("match", "match", 3))

    def test_a_symbolic_match_is_evidence_and_never_selects_a_rubric_line(self):
        roles = {"full": "R1", "no_work": "R3", "zero": "R4", "steps": ["R2"], "work_required": True}
        transcript = {"lines": [{"id": "l1", "elements": [{"text": "2(x+1)"}]}]}
        found = answers.facts([{"part": "", "text": "2(x+1)", "line_id": "l1"}], transcript, [{"label": "", "accept": ["2x+2"]}])
        self.assertEqual((found["check"], found["work"]), ("match", "none"))
        # Full credit proposed on a bare (symbolically) correct answer is still contradicted: the rubric pays for the work.
        self.assertIn("Full credit was proposed with little visible work; check that the work justifies the answer.", answers.reasons(found, roles, {"R1"}, True))
        self.assertNotIn("applied", found)
        self.assertEqual(answers.facts([{"part": "", "text": "2(x+1)", "line_id": "l1"}], transcript, [{"label": "", "accept": ["2x+2"]}], symbolic_forms=False)["check"], "unknown")  # Without the parser another form is not understood: not matched, and not accused.

    def test_work_that_simply_ends_early_is_unreadable_and_never_an_exception(self):
        """Measured 2026-09-22 on a real batch: an unclosed parenthesis raised IndexError out of the closed grammar, which
        `check_lines` does not catch, so it escaped and failed the whole scoring stage for that response. Anything this
        grammar cannot read is indeterminate; nothing it reads may throw."""
        for text in ("(12 + 8z", "x = (3", "sqrt(4", "3(", "((1+2)"):
            with self.assertRaises(symbolic.Unparsed, msg=text):
                symbolic.parse(text)
        # It still reads what it always read, and a whole line of such work is a record, not a crash.
        self.assertEqual(str(symbolic.parse("2(x+1)")), "2*x + 2")
        checks = symbolic.check_lines([{"id": "l1", "text": "(12 + 8z"}, {"id": "l2", "text": "2(x+1)"}])
        self.assertTrue(all(c["result"] != "fail" for c in checks), checks)

    def test_a_wrong_or_unparsed_answer_never_matches(self):
        # Understood and another value: a mismatch. Not understood: unknown. Neither ever matches.
        for text, expected in (("2x+3", "mismatch"), ("the same as before", "unknown"), ("2x+2 maybe", "unknown")):
            found = answers.facts([{"part": "", "text": text, "line_id": "l1"}], {"lines": [{"id": "l1", "elements": [{"text": text}]}]}, [{"label": "", "accept": ["2x+2"]}])
            self.assertEqual(found["check"], expected, text)


if __name__ == "__main__":
    unittest.main()
