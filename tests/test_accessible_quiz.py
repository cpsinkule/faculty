"""QUIZZES "ACCESSIBLE COPY" (document_identity/accessible_quiz.py, requirement, 2026-10-27): a drafted quiz's version as a Word file made by pandoc from the structured quiz, its math Word equations,
its displayed arrays and tables real tables, no figure without alt text and no LaTeX left unread; the DR quizzes tab's read of which
approved quizzes have one, and the download, on a scratch workspace with the scripted model host of tests/test_quiz_drafts.py."""
import copy
import io
import os
import re
import unittest
import uuid
import zipfile

from document_identity import accessible_quiz, quiz_latex
from fake_openai import payload_of, prompt_name
import test_quiz_drafts as quiz_tests
from test_quiz_drafts import quiz_answer

HAS_PANDOC = os.path.exists(accessible_quiz.pandoc())


def figureless(payload, **more):
    """The scripted quiz with its figure taken out, so a screen reader can read every problem."""
    answer = quiz_answer(payload, **more)
    for problem in answer["problems"]:
        for version in problem["versions"]:
            version.update(figure="", figure_side="")
    return answer


def quiz(answer=None, letters=("A", "B")):
    return quiz_latex.clean(answer or figureless({"request": {"versions": list(letters)}}), list(letters),
                            title_line="MATH 140 Fall 2026 Chapter 3 Quiz", total=5)


def document(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name).decode() for name in ("word/document.xml", "docProps/core.xml")}


@unittest.skipUnless(HAS_PANDOC, "pandoc is not installed (FACULTY_PANDOC or /mnt/nvme/Teaching/tools/pandoc)")
class AccessibleCopy(unittest.TestCase):
    def test_a_version_becomes_a_word_file_a_screen_reader_reads(self):
        drafted = quiz()
        source = accessible_quiz.source(drafted, "B")
        content, info = accessible_quiz.build(drafted, "B", drafted["title_line"])
        xml = document(content)
        body, core = xml["word/document.xml"], xml["docProps/core.xml"]
        # Titled and in English, one heading per kind of problem and one per problem.
        self.assertIn("<dc:title>MATH 140 Fall 2026 Chapter 3 Quiz</dc:title>", core)
        self.assertIn("<dc:language>en-US</dc:language>", core)
        self.assertEqual(re.findall(r'<w:pStyle w:val="(Heading\d)"', body), ["Heading1", "Heading2", "Heading1", "Heading2"])
        self.assertIn("Problem 1 (Section 3.3), 2 points", re.sub(r"<[^>]+>", "", body))
        # The math is Word equations, never LaTeX text: version B's 3x = 6, the choices and the displayed x + 2y = 20.
        self.assertEqual(info, {"rule": accessible_quiz.RULE, "problems": 2, "equations": info["equations"]})
        self.assertGreaterEqual(info["equations"], 8)
        self.assertNotIn("\\", re.sub(r"<[^>]+>", "", body))
        self.assertNotIn("$", re.sub(r"<[^>]+>", "", body).replace("$40", ""))
        # Choices and parts are lettered lists; the correct choice, solutions and final answers are never in it, nor the version.
        self.assertIn(r"\begin{enumerate}[(a)]", source)
        for hidden in ("Divide by", "Substitute.", "correct", "Version"):
            self.assertNotIn(hidden, re.sub(r"<[^>]+>", "", body))
        # Layout only is dropped: the scaffold's \vspace leaves its "Solution:" prompt.
        self.assertIn(r"\textbf{Solution:}", source)
        self.assertNotIn("vspace", source)

    def test_a_figure_or_unread_latex_has_no_copy(self):
        # The scripted quiz's graph: no alt text is written for a figure yet, so that version has no copy.
        drafted = quiz(quiz_answer({"request": {"versions": ["A"]}}), ["A"])
        with self.assertRaises(accessible_quiz.Unavailable) as refused:
            accessible_quiz.build(drafted, "A", drafted["title_line"])
        self.assertEqual(refused.exception.reasons, ["Problem 2 has a figure, and Faculty does not write alt text for figures yet"])
        # A command pandoc keeps as raw LaTeX would be spelled out or lost: refused, naming it.
        boxed = copy.deepcopy(quiz())
        boxed["problems"][1]["content"]["A"]["scaffold"] = r"\fbox{Answer here}"
        with self.assertRaisesRegex(accessible_quiz.Unavailable, r"LaTeX the converter cannot read: \\fbox"):
            accessible_quiz.build(boxed, "A", boxed["title_line"])
        # Math pandoc cannot make a Word equation of (a box inside an equation), and text it cannot read at all.
        unread = copy.deepcopy(quiz())
        unread["problems"][0]["content"]["A"]["statement"] = r"Solve $\fbox{3}x = 6$ for $x$."
        with self.assertRaisesRegex(accessible_quiz.Unavailable, r"Math the converter cannot read: \\fbox\{3\}x = 6"):
            accessible_quiz.build(unread, "A", unread["title_line"])
        unread["problems"][0]["content"]["A"]["statement"] = r"Solve $\frac{1}{x$ for $x$."
        with self.assertRaisesRegex(accessible_quiz.Unavailable, "The converter could not read it"):
            accessible_quiz.build(unread, "A", unread["title_line"])

    def test_one_problem_per_page(self):
        """Each problem after
        the first starts a new page, before its section heading where the kind of problem changes; the rest of the page is the room
        to work. The break is a page break run, never an empty paragraph to read out."""
        drafted = copy.deepcopy(quiz())
        drafted["problems"].append(copy.deepcopy(drafted["problems"][-1]))
        drafted["problems"][-1]["number"] = 3
        content, _ = accessible_quiz.build(drafted, "A", drafted["title_line"])
        body = document(content)["word/document.xml"]
        order = [match.group(1) or "page" for match in re.finditer(r'<w:br w:type="page" />|<w:br w:type="page"/>|<w:pStyle w:val="(Heading\d)"', body)]
        self.assertEqual([problem["kind"] for problem in drafted["problems"]], ["multiple_choice", "free_response", "free_response"])
        self.assertEqual(order, ["Heading1", "Heading2", "page", "Heading1", "Heading2", "page", "Heading2"])
        self.assertNotIn(accessible_quiz.PAGE, body)
        self.assertEqual(accessible_quiz.source(drafted, "A").count(accessible_quiz.PAGE), 2)

    def test_tables_tableaus_and_blanks(self):
        """Quiz 4's simplex tableau (a displayed array with a rule) and its column-typed tables become plain tables whose header row
        names the columns; an answer blank reads "[blank]"."""
        drafted = copy.deepcopy(quiz())
        tableau = (r"\[\begin{array}{ccc|c} x & y & s_1 & \text{constant}\\ 1 & -2 & 1 & 0\\ \hline -4 & -7 & 0 & 1 \end{array}\]")
        table = (r"\begin{tabular}{|*{2}{>{\centering\arraybackslash}p{1.2cm}|}|>{\centering\arraybackslash}p{1.6cm}|}\hline Basic & Non-basic & Total"
                 r"\\ \hline 1 & 2 & 3\\ \hline\end{tabular}")
        drafted["problems"][1]["content"]["A"].update(left=tableau + table, scaffold=r"$x=$\underline{\hspace{1.1cm}}\hfill $y=$\underline{\hspace{1.1cm}}\\[7mm]")
        source = accessible_quiz.source(drafted, "A")
        self.assertIn(r"\begin{tabular}{cccc}$x$ & $y$ & $s_1$ & $\text{constant}$ \\ \hline $1$ & $-2$ & $1$ & $0$ \\ $-4$ & $-7$ & $0$ & $1$ \\ \end{tabular}", source)
        self.assertIn(r"\begin{tabular}{lll}\hline Basic & Non-basic & Total", source)
        # An equation left open for the answer takes the blank as its right side: Word drew "(x, y) = ¿" for the missing side
        # (requirement, 2026-10-27).
        self.assertIn(r"$x= \text{[blank]}$ $y= \text{[blank]}$", source)
        content, _ = accessible_quiz.build(drafted, "A", drafted["title_line"])
        body = document(content)["word/document.xml"]
        self.assertEqual(body.count("<w:tbl>"), 2)
        self.assertEqual(body.count("<w:tblHeader"), 2)
        self.assertIn("[blank]", re.sub(r"<[^>]+>", "", body))
        self.assertNotRegex(body, r"<m:t>=</m:t></m:r></m:oMath>")  # no equation ends in "="
        self.assertEqual(accessible_quiz.columns(r"|*{6}{>{\centering\arraybackslash}p{1.2cm}|}|X|r@{}"), "lllllllr")


@unittest.skipUnless(HAS_PANDOC, "pandoc is not installed (FACULTY_PANDOC or /mnt/nvme/Teaching/tools/pandoc)")
class AccessibleCopyRoutes(unittest.TestCase):
    """The DR quizzes tab's read (GET /api/quiz-drafts/accessible-copies?term_id=) and the download of an approved quiz's copy."""
    setUp, draft, approved = quiz_tests.QuizDrafts.setUp, quiz_tests.QuizDrafts.draft, quiz_tests.QuizDrafts.approved

    def answer(self, body, host):
        if prompt_name(body) == "quiz_write" and not self.figure:
            return figureless(payload_of(body), harder=self.harder)
        return quiz_tests.QuizDrafts.answer(self, body, host)

    def copies(self):
        response = self.client.get("/api/quiz-drafts/accessible-copies", query_string={"term_id": self.term["id"]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json["assignments"]

    def test_an_approved_quiz_has_a_word_copy_to_download(self):
        self.figure = False
        self.assertEqual(self.copies(), {})  # nothing approved yet
        done = self.approved()
        draft_id = next(iter(self.frq.quiz_drafts(None)))["id"]
        approval_id = self.frq.quiz_draft(draft_id)["approved"]["id"]
        self.assertEqual(self.copies(), {done["assignment_id"]: {"draft_id": draft_id, "approval_id": approval_id, "title": "Chapter 3 Quiz",
                                                                 "version": "A", "ready": True, "reasons": []}})
        response = self.client.get(f"/api/quiz-drafts/{draft_id}/approvals/{approval_id}/accessible/B")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, accessible_quiz.MEDIA_TYPE)
        self.assertIn('filename=Chapter-3-Quiz-accessible.docx', response.headers["Content-Disposition"])
        self.assertIn("<m:oMath>", document(response.data)["word/document.xml"])
        self.assertEqual(self.client.get(f"/api/quiz-drafts/{draft_id}/approvals/{approval_id}/accessible/Z").status_code, 404)
        self.assertEqual(self.client.get(f"/api/quiz-drafts/{draft_id}/approvals/{uuid.uuid4()}/accessible/A").status_code, 404)
        # Another term's read, and a term that does not exist, list nothing.
        self.assertEqual(self.client.get("/api/quiz-drafts/accessible-copies", query_string={"term_id": "no-such-term"}).json, {"assignments": {}})

    def test_a_quiz_with_a_figure_says_why_it_has_none(self):
        self.figure = True
        done = self.approved()
        [entry] = self.copies().values()
        self.assertEqual((entry["version"], entry["ready"], entry["reasons"]),
                         ("A", False, ["Problem 2 has a figure, and Faculty does not write alt text for figures yet"]))
        refused = self.client.get(f"/api/quiz-drafts/{entry['draft_id']}/approvals/{entry['approval_id']}/accessible/A")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("alt text", refused.get_data(as_text=True))
        self.assertTrue(done["assignment_id"])


if __name__ == "__main__":
    unittest.main()
