"""Regrade requests on multiple choice (REGRADES "BUBBLE SHEETS"; requirement, 2026-10-28). Gradescope keeps a bubble-sheet exam's versions off its assignments list, so the capture reads their regrade
requests by the versions Faculty linked; a request is placed on the sheet Faculty uploaded for that student; a decision keeps
Faculty's reading or changes it (`answer`), saved as a review of the sheet; Apply corrects Gradescope through MC CORRECTIONS, then
posts the reply. Real sheets drawn and read as in test_gradescope_sheets; Gradescope is the loopback fake."""
import json
import unittest
import uuid

from test_bubble_sheets import HEADERS, KEY
from test_gradescope_mc_corrections import CorrectionCase

from grading.certification_change import OneChange, row_content
from grading.regrade_mc import parse_answer, rescored
from grading.regrade_requests import question_number
from rosters import connect


class Helpers(unittest.TestCase):
    def test_a_question_number_is_read_from_the_title(self):
        self.assertEqual([question_number(t) for t in ("3: Question 3", "Question 12", "12.1: Part", "Intro")], [3, 12, 12, None])

    def test_an_answer_is_letters_a_to_e_sorted_once(self):
        self.assertEqual([parse_answer(v) for v in ("B", "DB", "")], ["B", "BD", ""])
        for bad in ("F", "BB", 3, None, "ABCDEA"):
            with self.assertRaises(ValueError):
                parse_answer(bad)

    def test_a_new_reading_is_scored_with_the_key(self):
        found = {"answer": "D", "scoring": "exact", "points": "4", "earned": "4", "sheet_earned": "40"}
        self.assertEqual(rescored(found, "E"), ("0", "36"))
        self.assertEqual(rescored({**found, "earned": "0", "sheet_earned": "36"}, "D"), ("4", "40"))


class OneSheet(unittest.TestCase):
    """CERTIFY JUST THIS CHANGE for one student's sheet (ONE BUBBLE SHEET), on the rows as Certification records them."""

    def row(self, earned, bubble, frq="38", status="scored"):
        return {"section_id": "s", "enrollment_id": "e1", "status": status, "reason": None, "earned": earned, "possible": "100",
                "exam_version": "A", "dr_date": None, "parts_json": {"bubble": {"earned": bubble, "possible": "60", "review_id": None},
                                                                     "frq": {"earned": frq, "possible": "40", "questions": {}}}}

    def other(self, earned="90"):
        return {**self.row(earned, "52"), "enrollment_id": "e2"}

    def state(self, now, certified, decisions="d", others=None):
        others = others or (self.other(), self.other())
        return {"records": [now, others[0]], "participants": [], "certified_rows": {"s:e1": certified, "s:e2": others[1]}, "markers": {"decisions": decisions},
                "latest": {"id": "c1", "decisions_sha256": "d"}}

    def test_only_this_students_multiple_choice_changed(self):
        change = OneChange._sheet_change(self.state(self.row("98", "60"), self.row("94", "56")), "s:e1")
        self.assertEqual((change["from"], change["to"], change["same"], change["only"]), ("56", "60", False, True))
        self.assertEqual(len(change["fingerprint"]), 64)

    def test_another_students_row_or_a_written_grade_makes_it_more_than_one_change(self):
        moved = OneChange._sheet_change(self.state(self.row("98", "60"), self.row("94", "56"), others=(self.other("91"), self.other())), "s:e1")
        self.assertFalse(moved["only"])
        written = OneChange._sheet_change(self.state(self.row("98", "60"), self.row("94", "56"), decisions="other"), "s:e1")
        self.assertFalse(written["only"])
        frq = OneChange._sheet_change(self.state(self.row("99", "60", frq="39"), self.row("94", "56")), "s:e1")
        self.assertFalse(frq["only"])  # this row's free response changed too

    def test_a_row_without_multiple_choice_in_the_certification_is_refused(self):
        certified = self.row("38", "0")
        certified["parts_json"] = {"frq": certified["parts_json"]["frq"]}
        self.assertIn("not part of the certification", OneChange._sheet_change(self.state(self.row("98", "60"), certified), "s:e1")["reason"])

    def test_the_bubble_part_and_the_total_can_be_left_out_of_a_rows_content(self):
        self.assertEqual(row_content(self.row("98", "60"), part_left_out="bubble"), row_content(self.row("94", "56"), part_left_out="bubble"))
        self.assertNotEqual(row_content(self.row("98", "60")), row_content(self.row("94", "56")))


class McRegrades(CorrectionCase):
    """Bob's sheet is on Gradescope in Version B; Gradescope scored his Question 3 0, Faculty reads D (the key) and gives 4."""

    def setUp(self):
        super().setUp()
        self.record = self.app.extensions["communications"]
        self.record.gradescope_settings = self.settings
        self.app.extensions["regrade_panel"].sender.settings = self.settings
        self.request = self.fake.add_regrade(self.bob["id"], "I filled in D for question 3, Bob Example 223456780.")
        with self.record.lock():
            self.assertEqual(self.record.round("gradescope")["status"], "ok")
        self.base = f"/api/grading/assignments/{self.aid}/regrade-requests"

    def get(self, path=""):
        answer = self.client.get(self.base + path)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def post(self, path, body=None, status=200):
        answer = self.client.post(self.base + path, json=body or {}, headers=HEADERS)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def requested(self):
        return self.get()["requests"][0]["id"]

    def decide(self, body, status=200):
        message = self.requested()
        preview = self.post(f"/{message}/decide/preview", body)
        return preview, self.post(f"/{message}/decide", {**body, "fingerprint": preview["fingerprint"], "request_id": str(uuid.uuid4()),
                                                         "confirmation": "decide"}, status)

    def steps(self, view):
        return [(s["step"], s["status"]) for s in view["steps"]]

    def test_a_request_on_a_bubble_sheet_version_is_captured_and_placed_on_the_students_sheet(self):
        listed = self.get()["requests"]
        self.assertEqual(len(listed), 1)
        item = listed[0]
        self.assertEqual((item["kind"], item["number"], item["exam_version"], item["matched"], item["state"]), ("mc", 3, "B", True, "open"))
        detail = self.get(f"/{item['id']}")
        sheet = detail["sheet"]
        self.assertEqual((sheet["marked"], sheet["answer"], sheet["earned"], sheet["points"]), (KEY[3], KEY[3], "4", "4"))
        self.assertEqual(sheet["picture"], f"/api/bubble-sheets/sheets/{sheet['sheet_id']}/parts/q3")
        self.assertEqual(self.client.get(sheet["picture"]).status_code, 200)
        self.assertEqual(detail["student"]["name"], "Example, Bob")
        self.assertEqual(self.get("/panel")["requests"][0]["faculty"], {"points": "4", "of": "4"})
        # Follow-up's popup reads the panel through the conversation's key.
        with connect(self.app.config["DATABASE"]) as db:
            thread = db.execute("SELECT thread_key FROM comm_messages WHERE id=?", (item["id"],)).fetchone()[0]
        work = self.client.get(f"/api/grading/regrade-work?key=gradescope_regrade:{thread}").json
        self.assertEqual((work["kind"], work["plan_id"], work["message_id"]), ("mc", self.aid, item["id"]))

    def test_keeping_the_reading_corrects_gradescope_then_posts_the_reply(self):
        preview, started = self.decide({"decision": "keep", "reply": "Hi Bob, your sheet shows D, which is right: 4 points.", "reason": "D is filled."})
        self.assertEqual([(s["step"], s.get("from"), s.get("to")) for s in preview["steps"]], [("gradescope", "0", "4"), ("reply", None, None)])
        self.assertTrue(any("not certified" in n for n in preview["notes"]))
        run = started["apply"]
        self.assertEqual(self.steps(run), [("gradescope", "waiting"), ("reply", "pending")])
        self.assertEqual([r for r in self.fake.requests if r["method"] == "PUT"], [])  # no reply before Gradescope shows 4
        self.sender.drain()  # the read, the correction, the read again
        done = self.post(f"/applies/{run['id']}/continue")
        self.assertEqual(self.steps(done), [("gradescope", "done"), ("reply", "done")])
        self.assertEqual(self.fake.state(self.bob)[3], 4.0)
        self.assertEqual((self.request["staff_comment"], self.request["closed"]), ("Hi Bob, your sheet shows D, which is right: 4 points.", True))
        self.assertEqual(self.get(f"/{self.requested()}")["state"], "answered")
        self.assertEqual((self.fake.forbidden_hits, self.fake.publish_hits, self.fake.comment_hits), ([], [], []))

    def test_changing_the_reading_saves_a_review_of_the_sheet_and_the_score_follows_the_key(self):
        preview, started = self.decide({"decision": "change", "answer": "E", "reply": "Hi Bob, your sheet shows E for question 3.",
                                        "reason": "E is filled, not D."})
        grade = preview["steps"][0]
        self.assertEqual((grade["step"], grade["from"], grade["to"], grade["read"], grade["answer"]), ("grade", "4", "0", KEY[3], "E"))
        self.assertEqual([s["step"] for s in preview["steps"]], ["grade", "reply"])  # Gradescope already shows 0: nothing to correct
        self.assertEqual(self.steps(started["apply"]), [("reply", "done")])
        sheet = self.get(f"/{self.requested()}")["sheet"]
        self.assertEqual((sheet["marked"], sheet["earned"]), ("E", "0"))
        with connect(self.app.config["DATABASE"]) as db:
            review = db.execute("SELECT answers_json, version, note, enrollment_id FROM bubble_reviews ORDER BY id DESC LIMIT 1").fetchone()
        answers = json.loads(review["answers_json"])
        self.assertEqual((answers[2], answers[0], review["version"]), ("E", KEY[1], "B"))
        self.assertIn("question 3 read as E", review["note"])
        self.assertTrue(review["enrollment_id"])

    def test_faculty_changes_its_own_adjustment_when_its_reading_changes(self):
        self.check_mc()
        self.sender.drain()  # Gradescope's 0 corrected to Faculty's 4 (Blank + 4)
        self.assertEqual(self.fake.state(self.bob)[3], 4.0)
        preview, started = self.decide({"decision": "change", "answer": "E", "reply": "Hi Bob, your sheet shows E for question 3.", "reason": "E."})
        self.assertEqual([(s["step"], s.get("from"), s.get("to")) for s in preview["steps"] if s["step"] == "gradescope"], [("gradescope", "4", "0")])
        self.sender.drain()
        done = self.post(f"/applies/{started['apply']['id']}/continue")
        self.assertEqual(self.steps(done), [("gradescope", "done"), ("reply", "done")])
        applied, points, _, score = self.fake.state(self.bob)
        self.assertEqual((score, points), (0.0, None))  # Faculty's own +4 taken away
        latest = self.corrections()[-1]
        self.assertEqual((latest["status"], latest["gradescope_points"], latest["faculty_points"]), ("accepted", "4", "0"))

    def test_a_decision_on_a_bubble_sheet_names_an_answer_never_rubric_lines(self):
        message = self.requested()
        for body, said in (({"decision": "change", "lines": ["x"], "reply": "Hi"}, "never rubric lines"),
                           ({"decision": "change", "answer": "Z", "reply": "Hi"}, "A to E"),
                           ({"decision": "change", "answer": KEY[3], "reply": "Hi"}, 'propose "keep"')):
            answer = self.client.post(f"{self.base}/{message}/decide/preview", json=body, headers=HEADERS)
            self.assertEqual(answer.status_code, 400, answer.json)
            self.assertIn(said, answer.json["error"])


from test_certification_automatic import BubbleLateTests  # noqa: E402


class SheetAmend(BubbleLateTests):
    """CERTIFY JUST THIS CHANGE of one student's multiple choice (ONE BUBBLE SHEET) on a certified two-part plan, through the service:
    the new certification amends the latest with that row alone, and the gradebook cell follows."""

    def test_one_students_sheet_is_certified_by_itself(self):
        self.certify()
        self.assertEqual(self.cells()[self.row1], ("scored", "12", "grading"))
        self.bubble[self.enrollment] = ("A", "1", 501)  # a regrade decision read one of Alice's answers again: 2 -> 1
        change = OneChange(self.service)
        found = change.check_sheet(self.aid, self.row1)
        self.assertEqual((found["possible"], found["from"], found["to"]), (True, "2", "1"), found)
        done = change.amend(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "certify-change", "fingerprint": found["fingerprint"],
                                       "sheet_row": self.row1, "message_id": "m1"})
        self.assertEqual(self.cells()[self.row1], ("scored", "11", "grading"))
        latest = self.certified()[-1]
        amends = json.loads(latest["summary_json"])["amends"]
        self.assertEqual((amends["part"], amends["row"], amends["from"], amends["to"], amends["message_id"]), ("bubble", self.row1, "2", "1", "m1"))
        self.assertEqual(latest["id"], done["certification"]["id"])
        self.assertEqual(self.state()["state"], "current")
        self.assertTrue(change.check_sheet(self.aid, self.row1)["done"])

    def test_two_students_sheets_changed_still_certifies_this_one_alone(self):
        # A REGRADE NEVER ASKS TO CERTIFY AGAIN (requirement, 2026-11-16): the other sheet keeps waiting, its cell as certified.
        self.certify()
        other = self.cells()[self.row2]
        self.bubble.update({self.enrollment: ("A", "1", 501), self.second: ("B", "2", 501)})
        change = OneChange(self.service)
        found = change.check_sheet(self.aid, self.row1)
        self.assertEqual((found["possible"], found["reasons"]), (True, []), found)
        done = change.amend(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "certify-change", "fingerprint": found["fingerprint"],
                                       "sheet_row": self.row1})
        self.assertEqual(done["markers"], "carried")
        self.assertEqual((self.cells()[self.row1], self.cells()[self.row2]), (("scored", "11", "grading"), other))
        self.assertEqual(self.state()["state"], "changed")


for _name in [n for n in BubbleLateTests.__dict__ if n.startswith("test_")]:
    setattr(SheetAmend, _name, None)  # BubbleLateTests' own tests run in their module
del BubbleLateTests


if __name__ == "__main__":
    unittest.main()
