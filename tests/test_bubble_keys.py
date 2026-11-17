"""Bubble sheet grading: an answer key per version on the term's grading plan, scored the way Gradescope scores bubble sheets."""
import csv
import io
import tempfile
import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from app import create_app
from bubble_fixture import pdf, sheet
from grading.bubble_keys import normalize_key, points_text, question_score, score_sheet
from rosters import connect
from test_bubble_sheets import HEADERS, KEY, STUDENTS
from test_rosters import COLUMNS, csv_bytes


class ScoringRuleTests(unittest.TestCase):
    def test_exact_partial_and_either_follow_gradescope(self):
        score = lambda marked, answer, rule, points="4": points_text(question_score(marked, answer, rule, Decimal(points)))
        # Exact: only exactly the key's letters.
        self.assertEqual([score(m, "B", "exact") for m in ("B", "", "C", "BC")], ["4", "0", "0", "0"])
        self.assertEqual([score(m, "BD", "exact") for m in ("BD", "B", "BCD")], ["4", "0", "0"])
        # Partial: a share per correct letter, nothing once any other letter is marked; rounded to the cent.
        self.assertEqual([score(m, "ABD", "partial") for m in ("ABD", "A", "AB", "AC", "")], ["4", "1.33", "2.67", "0", "0"])
        # Either/Or: any correct letter is full points, but another letter beside it earns nothing.
        self.assertEqual([score(m, "AC", "either") for m in ("C", "AC", "CE", "B", "")], ["4", "4", "0", "0", "0"])
        # Every letter keyed under Either/Or gives the question to everyone, a blank included (v2); fewer letters, or another rule, do not.
        self.assertEqual([score(m, "ABCDE", "either") for m in ("A", "DE", "ABCDE", "")], ["4", "4", "4", "4"])
        self.assertEqual([score("", "ABCD", "either"), score("", "ABCDE", "partial"), score("", "ABCDE", "exact")], ["0", "0", "0"])
        self.assertEqual(score("B", "B", "exact", "2.5"), "2.5")

    def test_a_sheet_is_scored_on_its_keyed_questions_only(self):
        version = {"letter": "A", "questions": [{"number": 1, "answer": "B"}, {"number": 3, "answer": "AC", "scoring": "partial", "points": "2"},
                                                {"number": 12, "answer": "E"}]}
        result = score_sheet(["B", "D", "A", "E"], version, {"points": "1", "scoring": "exact"})
        self.assertEqual((result["earned"], result["possible"]), ("2", "4"))  # Question 2 has no key; question 12 was never read.
        self.assertEqual([(line["number"], line["earned"], line["read"]) for line in result["questions"]], [(1, "1", True), (3, "1", True), (12, "0", False)])

    def test_the_key_is_validated_and_stored_in_one_form(self):
        good = {"defaults": {"points": 4, "scoring": "exact"}, "versions": [
            {"letter": "A", "questions": [{"number": 2, "answer": "DB"}, {"number": 1, "answer": "C", "points": "2.50", "scoring": "either"}]}, {"letter": "B", "questions": []}]}
        self.assertEqual(normalize_key(good), {"rule": "bubble-scoring-v2", "defaults": {"points": "4", "scoring": "exact"}, "versions": [
            {"letter": "A", "questions": [{"number": 1, "answer": "C", "points": "2.5", "scoring": "either"}, {"number": 2, "answer": "BD"}]}, {"letter": "B", "questions": []}]})
        version = lambda *questions, letter="A": {"defaults": {"points": "1", "scoring": "exact"}, "versions": [{"letter": letter, "questions": list(questions)}]}
        for bad in ({"defaults": {"points": "1", "scoring": "most"}, "versions": [{"letter": "A", "questions": []}]},
                    {"defaults": {"points": "-1", "scoring": "exact"}, "versions": [{"letter": "A", "questions": []}]},
                    {"defaults": {"points": "0.125", "scoring": "exact"}, "versions": [{"letter": "A", "questions": []}]},
                    {"defaults": {"points": True, "scoring": "exact"}, "versions": [{"letter": "A", "questions": []}]},
                    {"defaults": {"points": "1", "scoring": "exact"}, "versions": []},
                    version(letter="B"), version({"number": 1, "answer": "F"}), version({"number": 1, "answer": ""}), version({"number": 1, "answer": "AA"}),
                    version({"number": 0, "answer": "A"}), version({"number": 101, "answer": "A"}), version({"number": 1, "answer": "A"}, {"number": 1, "answer": "B"}),
                    version({"number": "1", "answer": "A"}), version({"number": 1, "answer": "A", "scoring": "loose"}), version({"number": 1, "answer": "A", "points": "1001"})):
            with self.assertRaises(ValueError, msg=bad):
                normalize_key(bad)


class BubbleGradingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.client = self.app.test_client()
        self.inbox, self.scans, self.bubbles, self.frq = (self.app.extensions[name] for name in ("inbox", "document_scans", "bubble_sheets", "frq_grading"))
        created = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [501, 502], "multiple_sections": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        state = self.client.get("/api/state").json
        rows = [["", student["name"], student["uin"], "Registered", "", "", "", "", "", "", "", f"s{index}@example.test"] for index, student in enumerate(STUDENTS)]
        self.assertEqual(self.client.post(f"/api/course-groups/{state['course_groups'][0]['id']}/sections/501/roster",
                                          data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS).status_code, 201)
        self.assignment = self.client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "test-1", "title": "Test 1"}, headers=HEADERS).json

    def read(self, images, texts):
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "sheets.pdf", "content": pdf(images)}], source="email", sender="scanner",
                                idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        self.assertTrue(self.scans.process_one())
        message = job["message_id"]
        self.assertEqual(self.client.post(f"/api/bubble-sheets/messages/{message}", json={"assignment_id": self.assignment["id"], "question_count": 10}, headers=HEADERS).status_code, 202)
        ocr = lambda content, boxes: {"reads": [{"text": text} for pair in texts for text in pair][:len(boxes)], "seconds": 0}
        with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_fields", side_effect=ocr):
            self.assertTrue(self.bubbles.process_one())
        return message

    def clear(self, message):
        """The instructor's Inbox review of the scan, the ordinary way."""
        with connect(self.app.config["DATABASE"]) as db:
            fingerprint = self.frq.scan_status(db, message)["fingerprint"]
        return self.frq.review_scan(message, {"confirm_review": True, "fingerprint": fingerprint})

    def test_a_key_scores_every_reviewed_sheet_by_its_version_and_scans_in_the_inbox_wait(self):
        other = {**KEY, 1: "A", 2: "AB"}  # Version B's key, below, differs on questions 1 and 2; one sheet double-marks question 2.
        message = self.read([sheet(KEY, "A"), sheet(other, "B"), sheet({**KEY, 3: ""}, "A")],
                            [("123456789", "Alice Example"), ("223456780", "Bob Example"), ("987650001", "Carol Nguyen")])
        states = {item["page"]: item["status"] for item in self.client.get(f"/api/bubble-sheets/messages/{message}").json["run"]["sheets"]}
        self.assertEqual(states, {1: "clean", 2: "clean", 3: "clean"})
        with connect(self.app.config["DATABASE"]) as db:
            plan_id = self.frq.scan_status(db, message)["plan"]["id"]
        base = f"/api/grading/assignments/{plan_id}/bubble-sheets"
        # Before the scan's Inbox review nothing is listed or scored: its sheets are only counted.
        view = self.client.get(base).json
        self.assertEqual((view["key"], view["sheets"], view["counts"]["in_inbox"], view["suggested_versions"]), (None, [], 3, ["A"]))
        self.clear(message)
        view = self.client.get(base).json
        self.assertEqual([(s["student"], s["status"]) for s in view["sheets"]], [("Example, Alice Marie", "no_key"), ("Example, Bob", "no_key"), ("Nguyen, Carol", "no_key")])
        # A new key starts with the versions marked on at least two sheets: one B is not yet a version (the instructor adds it).
        self.assertEqual((view["suggested_versions"], view["read_questions"]), (["A"], 10))
        self.assertNotIn("123456789", self.client.get(base).get_data(as_text=True))  # UINs stay in the instructor's CSV.

        answers = lambda key: [{"number": n, "answer": key[n]} for n in range(1, 11)]
        key = {"defaults": {"points": "2", "scoring": "exact"}, "versions": [
            {"letter": "A", "questions": answers(KEY)}, {"letter": "B", "questions": [*answers({**other, 2: "B"})[:1], {"number": 2, "answer": "AB", "scoring": "partial"}, *answers(other)[2:]]}]}
        save = lambda body, **extra: self.client.post(f"{base}/key", json={**body, "request_id": extra.get("request_id", str(uuid.uuid4())), "base_revision": extra.get("base")}, headers=HEADERS)
        first = save(key, request_id="r1")
        self.assertEqual((first.status_code, first.json["key"]["revision"]), (201, 1), first.json)
        scored = {s["student"]: s for s in first.json["sheets"]}
        self.assertEqual({name: (s["version"], s["earned"], s["possible"], s["wrong"]) for name, s in scored.items()},
                         {"Example, Alice Marie": ("A", "20", "20", []), "Example, Bob": ("B", "20", "20", []), "Nguyen, Carol": ("A", "18", "20", [3])})
        self.assertEqual(first.json["stats"]["A"]["3"], {"sheets": 2, "full": 1, "common": "D", "common_count": 1})
        # A lost response is recovered by sending the same request again; the same ID for other content, or a stale base, is refused.
        self.assertEqual(save(key, request_id="r1").json["key"]["revision"], 1)
        self.assertEqual(save({**key, "defaults": {"points": "1", "scoring": "exact"}}, request_id="r1").status_code, 409)
        self.assertEqual(save(key).status_code, 409)  # base None, but revision 1 exists
        self.assertEqual(save(key, base=1).json["key"]["revision"], 1)  # the same key again records nothing
        one = save({"defaults": {"points": "1", "scoring": "exact"}, "versions": [{"letter": "A", "questions": answers(KEY)[:5]}]}, base=1).json
        # One version: every sheet takes it, whatever its version bubble says.
        self.assertEqual((one["key"]["revision"], sorted((s["version"], s["earned"]) for s in one["sheets"])), (2, [("A", "3"), ("A", "4"), ("A", "5")]))
        self.assertEqual(self.client.post(f"{base}/key", json={"request_id": "x", "base_revision": 2, "defaults": {}, "versions": []}, headers=HEADERS).status_code, 400)

        # A question keyed past the count the sheets were read with cannot be earned, and the page says so.
        beyond = save({"defaults": {"points": "1", "scoring": "exact"}, "versions": [{"letter": "A", "questions": [*answers(KEY), {"number": 12, "answer": "C"}]}]}, base=2).json
        self.assertEqual((beyond["beyond_read"], beyond["fewest_read"]), ([12], 10))
        export = self.client.get(f"{base}/scores.csv")
        rows = list(csv.reader(io.StringIO(export.data.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Name", "UIN", "Section", "Version", "Score", "Max points", "Status", *[f"Q{n}" for n in (*range(1, 11), 12)], "Scan page", "Note"])
        self.assertEqual(rows[3][:8], ["Nguyen, Carol", "987650001", "501", "A", "9", "11", "scored", "1"])
        self.assertEqual(rows[3][9], "0")  # question 3 was left blank
        self.assertIn("Test 1 - bubble sheet scores.csv", export.headers["Content-Disposition"])

        # A later scan waits in the Inbox, counted but not scored, and a sheet whose version has no key says so.
        later = self.read([sheet(KEY, "C")], [("555000111", "Dylan O'Brien")])
        view = self.client.get(base).json
        self.assertEqual((view["counts"]["in_inbox"], len(view["sheets"])), (1, 3))
        self.clear(later)
        two = save({"defaults": {"points": "1", "scoring": "exact"}, "versions": [{"letter": "A", "questions": answers(KEY)}, {"letter": "B", "questions": []}]}, base=3).json
        dylan = next(s for s in two["sheets"] if s["student"] == "O'Brien, Dylan James")
        self.assertEqual((dylan["status"], dylan["reason"]), ("no_key", "Version C has no answers in the key yet."))
        bob = next(s for s in two["sheets"] if s["student"] == "Example, Bob")
        self.assertEqual((bob["status"], bob["reason"]), ("no_key", "Version B has no answers in the key yet."))

        # Revisions are retained as saved; the grading plan's card and page know about the sheets and the key.
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([row[0] for row in db.execute("SELECT revision FROM frq_bubble_keys WHERE assignment_id=? ORDER BY revision", (plan_id,))], [1, 2, 3, 4])
            for statement in ("UPDATE frq_bubble_keys SET key_json='{}'", "DELETE FROM frq_bubble_keys"):
                with self.assertRaises(Exception):
                    db.execute(statement)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='bubble_key_saved'").fetchone()[0], 4)
        self.assertEqual(self.client.get(f"/api/grading/assignments/{plan_id}").json["bubble"], {"sheets": 4, "scans": 2, "key_revision": 4})
        # The certification drawer reads one student's own sheets, scored by this same rule, with how each was identified (review P2).
        _, full = self.frq._bubble_view(plan_id)
        carol = next(s for s in full["sheets"] if s["student"] == "Nguyen, Carol")
        with connect(self.app.config["DATABASE"]) as db:
            section_id = db.execute("""SELECT p.section_id FROM exam_participants p JOIN course_sections c ON c.id=p.section_id
                WHERE p.enrollment_id=? AND c.number=?""", (carol["enrollment_id"], carol["section"])).fetchone()[0]
        drawn = self.client.get(f"/api/grading/assignments/{plan_id}/certification/students/{section_id}/{carol['enrollment_id']}", headers=HEADERS)
        self.assertEqual(drawn.status_code, 200, drawn.json)
        bubble = drawn.json["parts"]["bubble"]
        self.assertEqual({k: bubble[k] for k in ("sheet_id", "status", "version", "earned", "possible", "wrong")},
                         {k: carol[k] for k in ("sheet_id", "status", "version", "earned", "possible", "wrong")})
        self.assertEqual(bubble["identified_by"], carol["identified_by"])
        self.assertIn(bubble["identified_by"], ("uin", "confidence", "review"))
        self.assertNotIn(carol["uin"], drawn.get_data(as_text=True))
        # Scoring never touches the readings or the Inbox: no review was written for the instructor.
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bubble_reviews").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
