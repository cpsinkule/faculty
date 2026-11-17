"""The review desk's Show student (requirement, 2026-09-29): the Review step stays
anonymous, and one click names one response's student and section. On the real graded workspace of `test_gradescope_send`."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gradescope_send as graded  # noqa: E402
from rosters import connect  # noqa: E402


class ShowStudent(graded.GradescopeCase):
    def test_one_response_names_its_student_and_section_and_nothing_else(self):
        with connect(self.path) as db:
            row = db.execute("""SELECT r.id,r.section_id,r.enrollment_id,s.number FROM frq_responses r JOIN course_sections s ON s.id=r.section_id
                WHERE r.assignment_id=? LIMIT 1""", (self.aid,)).fetchone()
            uin = db.execute("SELECT uin FROM student_enrollments WHERE id=?", (row["enrollment_id"],)).fetchone()[0]
        answer = self.client.get(f"/api/grading/assignments/{self.aid}/responses/{row['id']}/student")
        self.assertEqual(answer.status_code, 200, answer.get_json())
        found = answer.get_json()
        self.assertEqual(set(found), {"name", "section"})
        self.assertTrue(found["name"].strip())
        self.assertEqual(found["section"], row["number"])
        body = answer.get_data(as_text=True)
        self.assertNotIn(row["enrollment_id"], body)
        self.assertNotIn(uin, body)
        # The anonymous review read itself still names nobody.
        review = self.client.get(f"/api/grading/responses/{row['id']}").get_data(as_text=True)
        self.assertNotIn(found["name"], review)

    def test_a_response_of_another_assignment_is_not_found(self):
        with connect(self.path) as db:
            rid = db.execute("SELECT id FROM frq_responses WHERE assignment_id=? LIMIT 1", (self.aid,)).fetchone()[0]
        self.assertEqual(self.client.get(f"/api/grading/assignments/{self.aid}/responses/nothing-here/student").status_code, 404)
        self.assertEqual(self.client.get(f"/api/grading/assignments/not-a-plan/responses/{rid}/student").status_code, 404)


def load_tests(loader, tests, pattern):
    """Only this file's own tests: the graded workspace's cases run in their own file."""
    return unittest.TestSuite(ShowStudent(name) for name in loader.getTestCaseNames(ShowStudent) if name in vars(ShowStudent))


if __name__ == "__main__":
    unittest.main()
