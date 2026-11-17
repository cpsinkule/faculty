"""A final answer is evidence for review and grouping. It never awards credit."""
import unittest

from grading import answers, rubrics
from grading.models import plain_transcript


def rubric(*lines, maximum=6):
    return rubrics.validate({"mode": "positive", "items": [{"id": f"R{n}", "description": d, "points": p} for n, (p, d) in enumerate(lines, 1)]}, maximum)


MULTI_STEP = rubric((6, "Correct Answer: $$C(x)=21.5x+8829$$ or $$C(x)=21.5(x-3270)+79134$$"), (1, "Recognized the ordered pair $$(3270, 79134)$$."),
                    (2, "Used the correct formula for slope and calculated it correctly"), (1, "Correct Answer, but no work to justify answer."),
                    (0, "No Answer or Completely Incorrect"))


def transcript(*lines):
    value = plain_transcript("\n".join(lines))
    return value, {line["id"]: "\n".join(el["text"] for el in line["elements"]) for line in value["lines"]}


class Normalization(unittest.TestCase):
    def test_spellings_of_one_answer_compare_equal(self):
        for left, right in (("$$\\dfrac{168}{11}$$ units", "168/11"), ("3 \\times 2", "3x2"), ("3 × 2", "3 x 2"), ("(40,000, 280,000)", "(40000,280000)"),
                            ("x = 6", "6"), ("y=-4", "−4"), ("C(x) = 21.5x + 8829", "21.5x+8829"), ("6.0", "6"), ("\\frac{1}{2}", "0.5"), ("Not Possible.", "not possible")):
            with self.subTest(left=left, right=right):
                self.assertTrue(answers.equivalent(left, right))

    def test_different_answers_never_compare_equal(self):
        for left, right in (("(280000, 40000)", "(40000, 280000)"), ("2x3", "3x2"), ("15.27", "168/11"), ("-4", "4"), ("", "6"), ("21.5x+8829", "21.5x-8829")):
            with self.subTest(left=left, right=right):
                self.assertFalse(answers.equivalent(left, right))


class RubricRoles(unittest.TestCase):
    def test_the_rubric_says_which_lines_are_full_credit_and_no_work(self):
        roles = answers.rubric_roles(MULTI_STEP)
        self.assertEqual((roles["full"], roles["no_work"], roles["zero"], roles["steps"], roles["work_required"]), ("R1", "R4", "R5", ["R2", "R3"], True))
        short = rubric((2, "Correct Answer: Break-Even Point is $$(40000, 280000)$$"), (.75, "Correct quantity, but incorrect revenue"), (0, "No Answer"), maximum=2)
        self.assertEqual((answers.rubric_roles(short)["no_work"], answers.rubric_roles(short)["work_required"]), (None, False))
        asks = rubric((8, "Correct Answers: $$x=6$$ and $$y = -4$$ and work to justify answer."), (0, "No Answers"), maximum=8)
        self.assertTrue(answers.rubric_roles(asks)["work_required"])
        # Two lines worth the maximum leave full credit undecided rather than guessed.
        self.assertIsNone(answers.rubric_roles(rubric((6, "Correct"), (6, "Also correct")))["full"])

    def test_a_key_is_proposed_from_the_full_credit_line_for_the_instructor_to_confirm(self):
        self.assertEqual(answers.key_from_rubric(MULTI_STEP), [{"label": "", "accept": ["C(x)=21.5x+8829", "C(x)=21.5(x-3270)+79134"]}])
        both = rubric((8, "Correct Answers: $$x=6$$ and $$y = -4$$ and work to justify answer."), (0, "None"), maximum=8)
        self.assertEqual(answers.key_from_rubric(both), [{"label": "x", "accept": ["x=6"]}, {"label": "y", "accept": ["y = -4"]}])
        self.assertEqual(answers.key_from_rubric(rubric((4, "Correct Answers: a. three by two, b. Not possible"), (0, "None"), maximum=4)), [])

    def test_lettered_parts_and_explanations_are_read_as_the_key_and_never_as_alternatives(self):
        # Requirement, 2026-09-25: Confirm all saves the key each question shows, so a line's explanation must not become an answer.
        read = lambda line: answers.key_from_rubric(rubric((4, line), (0, "No answer"), maximum=4))
        self.assertEqual(read("Correct Answers: a. $$3 \\times 2$$, b. Not possible because the dimensions of $$GH^{T}$$ differ from those of $$6I$$."),
                         [{"label": "a", "accept": ["3 \\times 2"]}, {"label": "b", "accept": ["Not possible"]}])
        self.assertEqual(read("Correct Answers: a. Not possible because the columns of $$(8G-2H)$$ differ from the rows of $$J$$.  b. $$2 \\times 2$$"),
                         [{"label": "a", "accept": ["Not possible"]}, {"label": "b", "accept": ["2 \\times 2"]}])
        # "where t is any real number" explains the answer; it is not another accepted answer.
        self.assertEqual(read("Correct Answer: $$(x,y,z) = (8t+12, -5t+15,t)$$ where $$t$$ is any real number, with work to justify answer."),
                         [{"label": "", "accept": ["(x,y,z) = (8t+12, -5t+15,t)"]}])
        self.assertEqual(read("Correct Answer: $$(t, 2t)$$ or $$(s/2, s)$$, where $$t$$ is any real number"), [{"label": "", "accept": ["(t, 2t)", "(s/2, s)"]}])
        # A matrix written after `M=` in words cannot be read, so nothing is proposed for the question.
        self.assertEqual(read("Correct Answers: a. $$M=$$ rows are silver and gold, columns are Cal and Anna.  b. $6130.10 and work to justify all answers."), [])
        # A lettered part joined by "and" is not two alternatives: nothing is guessed.
        self.assertEqual(read("Correct Answers: a. $$x=1$$ and $$y=2$$, b. $$z=3$$"), [])

    def test_keys_are_validated(self):
        self.assertEqual(answers.clean_key([{"label": " a ", "accept": ["3x2", "3x2", " "]}]), [{"label": "a", "accept": ["3x2"]}])
        for bad in ([{"label": "a", "accept": []}], ["3x2"], [{"label": "a", "accept": [" "]}]):
            with self.assertRaises(ValueError):
                answers.clean_key(bad)


class Facts(unittest.TestCase):
    KEY = [{"label": "", "accept": ["C(x)=21.5x+8829"]}]

    def facts(self, lines, answer, readers=None, key=None):
        value, by_id = transcript(*lines)
        found, problems = answers.final_answers([{"part": "", "text": answer, "line_id": value["lines"][-1]["id"]}], by_id)
        self.assertEqual(problems, [])
        candidates = [{"transcript": plain_transcript(reader)} for reader in (readers if readers is not None else ["\n".join(lines)])]
        return answers.facts(found, value, self.KEY if key is None else key, candidates)

    def test_one_reading_means_two_readers_really_differ_on_a_final_answer(self):
        """Measured 2026-09-21: of 32 such flags, 9 were responses with no final answer at all and 2 were one fraction spelled two ways."""
        work = ["m = 43/2", "C(x) = (43/2)x + 8829"]
        key = [{"label": "", "accept": ["C(x)=(43/2)x+8829"]}]
        # One reader's parenthesized fraction is the other's \frac: the same reading.
        self.assertTrue(self.facts(work, "C(x) = (43/2)x + 8829", readers=["m=\\frac{43}{2}\nC(x)=\\frac{43}{2} x+8829"], key=key)["readers_agree"])
        # A parenthesis that changes the value is a different reading: 27/(2x) is not (27/2)x.
        self.assertIs(self.facts(["C(x) = 27/(2x) + 8541"], "C(x) = 27/(2x) + 8541", readers=["C(x)=\\frac{27}{2} x+8541"], key=key)["readers_agree"], False)
        self.assertIs(self.facts(work, "C(x) = (43/2)x + 8829", readers=["C(x)=(48/2)x+8829"], key=key)["readers_agree"], False)
        # No final answer reported (a blank response): there is nothing two readers could differ on, and nothing is flagged for it.
        value, _ = transcript("Break-Even Point: ______")
        blank = answers.facts([], value, key, [{"transcript": plain_transcript("Break-Even Point:")}])
        self.assertIsNone(blank["readers_agree"])
        self.assertEqual(answers.reasons(blank, {"full": "R1", "no_work": "R3", "work_required": True}, {"R4"}, True), [])

    def test_a_matching_answer_is_split_by_how_much_work_is_visible(self):
        worked = self.facts(["m = (79134-69889)/(3270-2840)", "m = 21.5", "C - 79134 = 21.5(x - 3270)", "C(x) = 21.5x + 8829"], "C(x) = 21.5x + 8829")
        bare = self.facts(["C(x) = 21.5x + 8829"], "C(x) = 21.5x + 8829")
        self.assertEqual((worked["check"], worked["work"], worked["work_lines"], worked["readers_agree"]), ("match", "shown", 3, True))
        self.assertEqual((bare["check"], bare["work"], bare["work_lines"]), ("match", "none", 0))
        # The same answer never shares a group across different amounts of work or different proposals.
        self.assertNotEqual(answers.signature("q", worked, ["R1"]), answers.signature("q", bare, ["R1"]))
        self.assertNotEqual(answers.signature("q", worked, ["R1"]), answers.signature("q", worked, ["R2", "R3"]))
        same = self.facts(["a=1", "b=2", "c=3", "C(x)=21.5x+8829"], "C(x)=21.5x + 8829")
        self.assertEqual(answers.signature("q", worked, ["R1"]), answers.signature("q", same, ["R1"]))
        # Both match the key, yet the rubric pays for writing C(x)=, so the bare right-hand side is its own group.
        bare_value = self.facts(["a=1", "b=2", "c=3", "21.5x+8829"], "21.5x+8829")
        self.assertEqual(bare_value["check"], "match")
        self.assertNotEqual(answers.signature("q", worked, ["R1"]), answers.signature("q", bare_value, ["R1"]))

    def test_printed_problem_text_is_not_counted_as_work(self):
        value, by_id = transcript("22. (6 pts) A company has total costs", "C(x)=21.5x+8829")
        found, _ = answers.final_answers([{"part": "", "text": "C(x)=21.5x+8829", "line_id": "line-2"}], by_id)
        self.assertEqual(answers.facts(found, value, self.KEY, [], problem="22. (6 pts) A company has total costs of $79,134")["work_lines"], 0)

    def test_contradictions_with_a_proposal_become_review_reasons_and_no_fact_awards_credit(self):
        roles = answers.rubric_roles(MULTI_STEP)
        bare = self.facts(["C(x) = 21.5x + 8829"], "C(x) = 21.5x + 8829")
        worked = self.facts(["m = 21.5", "point (3270, 79134)", "C - 79134 = 21.5(x-3270)", "C(x) = 21.5x + 8829"], "C(x) = 21.5x + 8829")
        wrong = self.facts(["m = 2.15", "b = 8829", "so", "C(x) = 2.15x + 8829"], "C(x) = 2.15x + 8829")
        single = self.facts(["m = 21.5", "b = 8829", "so", "C(x) = 21.5x + 8829"], "C(x) = 21.5x + 8829", readers=["m = 21.5 b = 8829 so C(x) = 21.5x + 8329"])
        self.assertEqual(answers.reasons(worked, roles, {"R1"}, True), [])
        self.assertEqual(answers.reasons(bare, roles, {"R4"}, True), [])  # Correct answer without work, on its own rubric line.
        self.assertIn("little visible work", answers.reasons(bare, roles, {"R1"}, True)[0])
        self.assertIn("does not match the answer key", answers.reasons(wrong, roles, {"R1"}, True)[0])
        self.assertIn("full credit was not proposed", answers.reasons(worked, roles, {"R2", "R3"}, True)[0])
        self.assertTrue(any("although work is visible" in reason for reason in answers.reasons(worked, roles, {"R4"}, True)))
        self.assertIn("one reading only", answers.reasons(single, roles, {"R1"}, True)[0])
        # Without a confirmed key nothing is asserted about correctness; thin work is still noticed.
        unknown = self.facts(["C(x) = 21.5x + 8829"], "C(x) = 21.5x + 8829", key=[])
        self.assertEqual(unknown["check"], "unknown")
        self.assertEqual(len(answers.reasons(unknown, roles, {"R1"}, False)), 1)

    def test_an_answer_this_check_cannot_read_is_unknown_and_never_accused_of_contradicting_the_key(self):
        # Every case is from the first real papers graded in the trial.
        key = [{"label": "", "accept": ["C(x)=13.5x+8541", "C(x)=\\frac{27}{2}x+8541"]}]
        def check(written, accepted=key):
            value, by_id = transcript("work", written)
            found, _ = answers.final_answers([{"part": "", "text": written, "line_id": "line-2"}], by_id)
            return answers.facts(found, value, accepted)["check"]
        self.assertEqual(check("Function: C(x) = 27/2 x + 8541"), "match")       # the printed label of the answer line
        self.assertEqual(check("C(x)=13.5 x+\\$ 8541 ."), "match")               # an escaped currency sign and a full stop
        self.assertEqual(check("C(x) = $13.5 per item * x + $8541"), "unknown")  # units inside the formula: not read, so not judged
        self.assertEqual(check("C(x) = 13.53x + 8487.6"), "mismatch")            # read, and another function
        self.assertEqual(check("y = 0.07x - 499.97"), "mismatch")
        sentence = [{"label": "", "accept": ["\\dfrac{152}{11}"]}]
        self.assertEqual(check("When y decreases by 19 units, x decreases by 152/11.", sentence), "unknown")
        self.assertEqual(check("152/11", sentence), "match")
        self.assertEqual(check("-152/11", sentence), "mismatch")
        self.assertEqual(check("increases", [{"label": "", "accept": ["decreases"]}]), "mismatch")
        roles = answers.rubric_roles(MULTI_STEP)
        value, by_id = transcript("m = 13.5", "point (2790, 46206)", "C - 46206 = 13.5(x - 2790)", "C(x) = $13.5 per item * x + $8541")
        found, _ = answers.final_answers([{"part": "", "text": "C(x) = $13.5 per item * x + $8541", "line_id": "line-4"}], by_id)
        self.assertEqual(answers.reasons(answers.facts(found, value, key), roles, {"R1"}, True), [])

    def test_partial_lines_that_add_up_to_full_marks_for_a_wrong_answer_are_flagged(self):
        roles = answers.rubric_roles(MULTI_STEP)
        value, by_id = transcript("m = 0.07", "y - 1780 = 0.07(x - 32571)", "so", "y = 0.07x - 499.97")
        found, _ = answers.final_answers([{"part": "", "text": "y = 0.07x - 499.97", "line_id": "line-4"}], by_id)
        facts = {**answers.facts(found, value, [{"label": "", "accept": ["C(x)=13.5x+8541"]}]), "full_credit": True}
        self.assertTrue(any("add up to full marks" in reason for reason in answers.reasons(facts, roles, {"R2", "R3"}, True)))
        self.assertFalse(any("add up to full marks" in reason for reason in answers.reasons({**facts, "full_credit": False}, roles, {"R2", "R3"}, True)))

    def test_a_key_in_words_is_matched_by_an_answer_that_begins_with_those_words(self):
        """A question that asks to "write not possible and explain why" is answered by a sentence; every one was `unknown` to the key."""
        for written in ("Not possible, dimensions do not match.", "NOT POSSIBLE \\( GH^{T} = 3 \\times 4 \\)", "not possible"):
            self.assertEqual(answers.compare(written, "not possible"), "same", written)
        for written, verdict in (("This is not possible because the sizes differ", "unknown"), ("Not possibly", "unknown"), ("DNE. The result is 3 × 4", "unknown"),
                                 ("possible, since the inner sizes agree", "unknown")):
            self.assertEqual(answers.compare(written, "not possible"), verdict, written)
        self.assertEqual(answers.compare("not possible", "possible"), "unknown")  # Never matched by words it merely contains; and a phrase is not a word.
        self.assertEqual(answers.compare("increases", "decreases"), "different")
        self.assertFalse(answers.worded("3 x 2 because", "3x2"))  # Words only: a value is never matched by its beginning.
        key = [{"label": "a", "accept": ["not possible"]}, {"label": "b", "accept": ["2 \\times 2"]}]
        value, by_id = transcript("Not possible, the insides differ", "2 × 2")
        parts = lambda a, b: answers.final_answers([{"part": a, "text": "Not possible, the insides differ", "line_id": "line-1"}, {"part": b, "text": "2 × 2", "line_id": "line-2"}], by_id)[0]
        self.assertEqual(answers.facts(parts("a", "b"), value, key)["check"], "match")
        self.assertEqual(answers.facts(parts("", "Dimensions"), value, key)["check"], "match")  # A label that names no key part is no evidence.
        # An answer reported for part b never answers the key's part a, however well its words fit.
        self.assertEqual(answers.facts(parts("b", "b")[:1], value, key)["check"], "mismatch")

    def test_an_ordered_triple_is_its_components_and_a_renamed_parameter_is_never_another_value(self):
        """2026-09-21: all 7 full-credit answers to one version of a question were `unknown`: the closed grammar reads ONE expression."""
        key = "(x,y,z) = (-4t+20, 9t+18,t)"
        for written in ("(20 - 4t, 18 + 9t, t)", "$(x, y, z) = (20 - 4t, 18 + 9t, t)$", "(-4(t)+20, 9(t)+18, t)", "(20-4t, 18+9t, t) where t is any real number",
                        "\\left(20-4t,\\ 18+9t,\\ t\\right), for all real numbers of t"):
            self.assertEqual(answers.compare(written, key, []), "same", written)
        self.assertEqual(answers.compare("(40,000, 280,000)", "(40000, 280000)", []), "same")
        self.assertEqual(answers.compare("(80,000/3, 240,000)", "(\\frac{80000}{3}, 240000)", []), "same")
        # Understood, and another value: tuples of NUMBERS, every one of them read, and one differs.
        for written, accepted in (("(40000, 260000)", "(40000, 280000)"), ("(280000, 40000)", "(40000, 280000)"), ("(80,000, $3)", "(80000, 240000)")):
            self.assertEqual(answers.compare(written, accepted, []), "different", written)
        # A parametric answer is never accused: another name for the parameter, or the same solutions with x as the parameter, is no
        # other value, and no comparison of components can tell it from a wrong one. Nor are a pair for a triple, equations instead of
        # the triple, words before it, or two tuples.
        for written in ("(20-4z, 18+9z, z)", "(t, -\\frac{9}{4}t + 63, -\\frac{1}{4}t + 5)", "(20-4t, 18-9t, t)", "(20-4t, 18+9t)", "x=-4t+20, y=9t+18, z=t",
                        "Dependent z=t (20-4t, 18+9t, t)", "(20-4t, 18+9t, t) (1, 2, 3)", "(20-4t, 18+9t, t) when t = 2"):
            self.assertEqual(answers.compare(written, key, []), "unknown", written)
        self.assertEqual(answers.compare("(40000, 280000, 1)", "(40000, 280000)", []), "unknown")
        self.assertEqual(answers.compare("(x-3)(x+2)", "(x+2)(x-3)", []), "same")  # A product is an expression, as before.

    def test_the_readers_text_wrapper_is_not_the_students_first_word(self):
        """2026-09-21: `\\text{not possible, you can't subtract ...}` began with "text" and was unknown to a key in words it answers."""
        for written in ("\\text{not possible, you can't subtract a } 3 \\times 2 \\text{ from a } 3 \\times 4", "\\text { Not possible } 3 \\times 4", "\\( \\text{Not possible} \\)"):
            self.assertEqual(answers.compare(written, "not possible"), "same", written)
        self.assertEqual(answers.compare("\\text{This is not possible}", "not possible"), "unknown")  # Still from the first word.

    def test_two_readers_are_compared_on_values_and_on_the_numbers_of_a_sentence_or_a_matrix(self):
        """Next batch: 15 of 36 one-reading flags existed only because a sentence or the part (a) matrix was reported as a final answer."""
        key = [{"label": "b", "accept": ["6130.10"]}]
        def agree(answer, reader):
            value, by_id = transcript(answer)
            found, _ = answers.final_answers([{"part": "b", "text": answer, "line_id": "line-1"}], by_id)
            return answers.facts(found, value, key, [{"transcript": plain_transcript(reader)}])["readers_agree"]
        self.assertIsNone(agree("Not possible, dimensions do not match", "Not passible, dimensiors tonot match"))  # No value: nothing two readers could differ on.
        self.assertTrue(agree("Anna has $6130.10 in precious metals", "Ana hos \\$ 6130.1 in precions metols"))   # The number, whoever spells the words.
        self.assertIs(agree("Anna has $6130.10 in precious metals", "Anna has \\$ 6180.10 in precious metals"), False)
        self.assertTrue(agree("\\begin{bmatrix} 20 & 35 \\\\ 2 & 1 \\end{bmatrix}", "[\\begin{array}{cc}20 & 35 \\\\ 2 & 1\\end{array}]"))
        self.assertIs(agree("\\begin{bmatrix} 20 & 35 \\\\ 2 & 1 \\end{bmatrix}", "[\\begin{array}{cc}20 & 85 \\\\ 2 & 1\\end{array}]"), False)
        self.assertTrue(agree("(40000 totes, 280000)", "(40,000 tates, 280,000)"))
        self.assertTrue(agree("6130.10", "6130.1"))                          # One number, with or without its zero.
        self.assertIs(agree("(-4t+20, 9t+18, t)", "(-4t+20, 9t+10, t)"), False)  # A value is still compared whole: this is a real difference.
        self.assertIsNone(agree("______", "Dimensions: ____"))

    def test_a_key_for_one_part_is_not_the_whole_answer(self):
        """10 of 13 "matches the key, but full credit was not proposed" flags were one question whose key answers part b only."""
        parts = rubric((8, "Correct Answers: a. the matrix, b. $6130.10 and work to justify all answers."), (2, "Part A: Correct matrix labels"), (3, "Part B: Correct product"), (0, "No Answers"), maximum=8)
        roles = answers.rubric_roles(parts)
        self.assertEqual(roles["parts"], ["a", "b"])
        facts = {"check": "match", "readers_agree": True, "work": "shown", "full_credit": False}
        accused = lambda key: any("full credit was not proposed" in r for r in answers.reasons(facts, roles, {"R3"}, True, key))
        self.assertFalse(accused([{"label": "b", "accept": ["6130.10"]}]))                                   # Part a lost the points; this key says nothing about it.
        self.assertTrue(accused([{"label": "a", "accept": ["M"]}, {"label": "b", "accept": ["6130.10"]}]))   # Every part is in the key.
        self.assertTrue(accused([{"label": "x", "accept": ["6"]}]))                                          # Not part letters: taken as the whole answer, as before.
        self.assertTrue(accused([{"label": "", "accept": ["6130.10"]}]))
        self.assertTrue(any("full credit was not proposed" in r for r in answers.reasons(facts, roles, {"R3"}, True)))  # A caller without the key: as before.

    def test_every_part_must_be_answered_and_unquoted_answers_are_refused(self):
        key = [{"label": "x", "accept": ["x=6"]}, {"label": "y", "accept": ["y=-4"]}]
        value, by_id = transcript("8y = -32", "y = -4", "x = 6")
        both, _ = answers.final_answers([{"part": "y", "text": "y = -4", "line_id": "line-2"}, {"part": "x", "text": "x = 6", "line_id": "line-3"}], by_id)
        self.assertEqual(answers.facts(both, value, key)["check"], "match")
        self.assertEqual(answers.facts(both[:1], value, key)["check"], "mismatch")
        self.assertEqual(answers.facts([], value, key)["check"], "unknown")
        kept, problems = answers.final_answers([{"part": "x", "text": "x = 5", "line_id": "line-3"}, {"part": "y", "text": "y = -4", "line_id": "missing"}], by_id)
        self.assertEqual((kept, len(problems)), ([], 2))


if __name__ == "__main__":
    unittest.main()
