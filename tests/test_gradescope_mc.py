"""The MC check (docs/decisions/gradescope.md THE MC CHECK; requirement, 2026-10-06). Gradescope's grades download is read in the
layout read on the real site (Common Exam 1 MC of 140-507, 2026-10-06) and compared with Faculty's own scoring of the same sheets,
question by question; nothing is ever written to Gradescope. Real sheets are drawn and read as in test_gradescope_sheets; Gradescope
is the loopback fake."""
import io
import json
import unittest
import uuid
import zipfile

from test_gradescope_sheets import ALICE, BOB, CAROL, SheetCase, UINS
from test_bubble_sheets import HEADERS, KEY

from course_storage import timestamp
from grading import certification_rules as rules
from grading import gradescope_web as gw
from grading.gradescope_mc import compare, why
from rosters import connect

REAL_COLUMNS = ["First Name", "Last Name", "SID", "Email", "Sections", "Total Score", "Max Points", "Status", "Submission ID", "Submission Time",
                "Lateness (H:M:S)", "View Count", "Submission Count", "1: Question 1 (4.0 pts)", "6: Question 6 (4.0 pts)", "14: Question 14 (2.0 pts)"]


def download(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, rows in files.items():
            archive.writestr(name, "\n".join(",".join(row) for row in rows) + "\n")
    return buffer.getvalue()


class Download(unittest.TestCase):
    """`gradescope_web.container_scores`: the real download's layout, and nothing compared from a layout Faculty does not know."""

    def test_the_real_layout_is_read_and_missing_rows_are_no_sheet(self):
        data = download({
            "Common_Exam_1_-_Multiple_Choice_Version_A_scores.csv": [REAL_COLUMNS,
                ["A", "One", "123456789", "a@x", "math-140-507", "32.0", "10.0", "Graded", "430710547", "2026-09-25 17:58:22 -0500", "00:00:00", "3", "1", "4.0", "0.0", "2.0"],
                ["B", "Two", "223456780", "b@x", "math-140-507", "", "10.0", "Missing"]],
            "Common_Exam_1_-_Multiple_Choice_Version_B_scores.csv": [REAL_COLUMNS,
                ["A", "One", "123456789", "a@x", "math-140-507", "", "10.0", "Missing"],
                ["B", "Two", "223456780", "b@x", "math-140-507", "26.75", "10.0", "Graded", "430710075", "2026-09-25 17:57:13 -0500", "00:00:00", "0", "1", "4.0", "4.0", "0.5"]],
            "Common_Exam_1_-_Multiple_Choice_Version__scores.csv": [REAL_COLUMNS, ["C", "Three", "987650001", "c@x", "math-140-507", "0.0", "10.0", "Graded", "430711053",
                                                                                   "2026", "00:00:00", "1", "1", "0.0", "0.0", "0.0"]],
            "Common_Exam_1_-_Multiple_Choice_Version_Set_Scores.csv": [["First Name", "Last Name", "SID", "Total Score", "Version"]]})
        self.assertEqual(gw.container_scores(data), {
            "123456789": {"version": "A", "total": "32", "status": "Graded", "submission": "430710547", "questions": {"1": "4", "6": "0", "14": "2"}},
            "223456780": {"version": "B", "total": "26.75", "status": "Graded", "submission": "430710075", "questions": {"1": "4", "6": "4", "14": "0.5"}},
            "987650001": {"version": "?", "total": "0", "status": "Graded", "submission": "430711053", "questions": {"1": "0", "6": "0", "14": "0"}}})

    def test_an_unknown_layout_compares_nothing(self):
        for data in (b"not a zip", download({"readme.txt": [["x"]]}),
                     download({"X_Version_A_scores.csv": [["First Name", "SID", "Total Score", "Status"]]}),  # no Submission ID
                     download({"X_Version_A_scores.csv": [REAL_COLUMNS, ["A", "B", "123456789", "", "", "lots", "", "Graded", "1"]]})):
            with self.assertRaises(gw.GradescopeError):
                gw.container_scores(data)


class Compare(unittest.TestCase):
    """`gradescope_mc.compare`: version, total and every question; a blank on an every-letter question says why it differs."""

    @staticmethod
    def sheet(enrollment, version, lines, name="Example, Bob", section=508):
        earned = sum(float(line[2]) for line in lines)
        return {"sheet_id": f"sheet-{enrollment}", "enrollment_id": enrollment, "status": "scored", "student": name, "section": section, "version": version,
                "earned": f"{earned:g}", "questions": [{"number": n, "marked": marked, "answer": answer, "scoring": scoring, "points": "4", "earned": got}
                                                       for n, marked, got, answer, scoring in lines]}

    def test_the_two_exam_1_students_differ_by_the_blank_on_the_question_everyone_is_given(self):
        mine = self.sheet("e1", "B", [(1, "B", "4", "B", "exact"), (6, "", "4", "ABCDE", "either")])
        theirs = {"version": "B", "total": "4", "status": "Graded", "submission": "1", "questions": {"1": "4", "6": "0"}}
        found = compare([mine], {"s508": {"students": {"e1": theirs}, "unmatched": 0}}, {"e1": "s508"})
        self.assertEqual(found["counts"], {"compared": 1, "agree": 0, "differ": 1, "not_on_gradescope": 0, "not_graded": 0, "faculty_unscored": 0, "unmatched": 0})
        differ = found["differ"][0]
        self.assertEqual((differ["row"], differ["name"], differ["section"], differ["gradescope"], differ["faculty"]),
                         ("s508:e1", "Example, Bob", 508, {"version": "B", "total": "4"}, {"version": "B", "total": "8"}))
        self.assertEqual(differ["questions"], [{"number": 6, "gradescope": "0", "faculty": "4",
                                                "why": "left blank; your key gives this question to everyone, Gradescope gives a blank 0"}])
        self.assertIn("Q6: Gradescope 0, Faculty 4", differ["detail"])

    def test_agreement_versions_and_what_is_only_on_one_side(self):
        agree = self.sheet("e1", "A", [(1, "B", "4", "B", "exact")], name="Example, Alice")
        other_version = self.sheet("e2", "A", [(1, "B", "4", "B", "exact")], name="Example, Carol")
        unread = self.sheet("e3", "B", [(1, "C", "0", "B", "exact")], name="Example, Dylan")
        not_sent = self.sheet("e4", "A", [(1, "B", "4", "B", "exact")], name="Example, Eve")
        reads = {"s1": {"students": {
            "e1": {"version": "A", "total": "4.0", "status": "Graded", "questions": {"1": "4.0"}},
            "e2": {"version": "B", "total": "0", "status": "Graded", "questions": {"1": "0"}},
            "e3": {"version": "?", "total": "0", "status": "Graded", "questions": {}},
            "e5": {"version": "A", "total": "4", "status": "Graded", "questions": {"1": "4"}},  # Faculty has no sheet scored for them
            "e6": {"version": "A", "total": None, "status": "Ungraded", "questions": {}}}, "unmatched": 2}}
        sheets = [agree, other_version, unread, not_sent, {**self.sheet("e6", "A", []), "status": "scored"}]
        found = compare(sheets, reads, {e: "s1" for e in ("e1", "e2", "e3", "e4", "e5", "e6")})
        self.assertEqual(found["counts"], {"compared": 3, "agree": 1, "differ": 2, "not_on_gradescope": 1, "not_graded": 1, "faculty_unscored": 1, "unmatched": 2})
        details = {d["name"]: d["detail"] for d in found["differ"]}
        self.assertEqual(details, {"Example, Carol": "Gradescope has Version B, Faculty Version A: Gradescope 0, Faculty 4",
                                   "Example, Dylan": "Gradescope could not read the version on this sheet (Faculty: Version B); it scored nothing"})
        self.assertEqual(why({"marked": "C", "answer": "B", "scoring": "exact"}), "Faculty read “C”; your key: B")


class Check(SheetCase):
    """The sender reads Gradescope's scores (one read per linked section), keeps them, and the panels compare them with Faculty's."""

    def setUp(self):
        super().setUp()
        key = {"defaults": {"points": "4", "scoring": "exact"},
               "versions": [{"letter": letter, "questions": [{"number": n, "answer": answer} for n, answer in KEY.items()]} for letter in "AB"]}
        self.frq.save_bubble_key(self.aid, {"request_id": "key-1", "base_revision": None, **key})
        self.send()
        self.assertEqual(self.sender.drain(), 3)  # the three sheets go up first
        for uin in (ALICE[0], BOB[0], CAROL[0]):
            letter = self.on_gradescope(uin)[0]
            # Gradescope scores the sheets with its own key: every question right, except Bob's question 3.
            self.fake.score_sheet(self.versions[letter], self.users[uin], {n: (0.0 if uin == BOB[0] and n == 3 else 4.0) for n in KEY})

    def reads(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_mc_checks ORDER BY created_at,rowid")]

    def test_check_mc_reads_gradescope_and_names_the_student_it_scored_differently(self):
        before = self.sender.sheet_status(self.aid)["mc_check"]
        self.assertEqual((before["checked"], before["running"], before["counts"]), (False, False, None))
        answer = self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/mc-check", json={}, headers=HEADERS)
        self.assertEqual(answer.status_code, 202, answer.json)
        self.assertTrue(answer.json["running"])
        saves = len(self.fake.requests)
        # The read, then its correction of Bob's question 3 (MC CORRECTIONS, test_gradescope_mc_corrections), which this fake's
        # versions, laid out without questions, refuse: nothing is written here.
        self.assertEqual(self.sender.drain(), 2)
        read = self.reads()[-1]
        self.assertEqual((read["status"], read["send_id"], read["course_id"], read["container_id"]), ("read", None, str(self.course), str(self.exam)))
        stored = json.loads(read["scores_json"])
        self.assertEqual(stored["unmatched"], 0)
        self.assertFalse(any(uin in read["scores_json"] for uin in UINS))  # kept by enrollment, never by UIN
        self.assertEqual([r["method"] for r in self.fake.requests[saves:] if r["method"] != "GET"], [])  # a read, nothing written
        for kind in ("", "/sheets"):
            mc = self.client.get(f"/api/grading/gradescope/assignments/{self.aid}{kind}").json["mc_check"]
            self.assertEqual((mc["checked"], mc["running"], mc["sections"]), (True, False, [501]))
            self.assertEqual(mc["counts"], {"compared": 3, "agree": 2, "differ": 1, "not_on_gradescope": 0, "not_graded": 0, "faculty_unscored": 0, "unmatched": 0})
            differ = mc["differ"][0]
            self.assertEqual((differ["name"], differ["gradescope"]["total"], differ["faculty"]["total"]), ("Example, Bob", "36", "40"))
            self.assertEqual([(q["number"], q["gradescope"], q["faculty"]) for q in differ["questions"]], [(3, "0", "4")])
            self.assertFalse(any(uin in json.dumps(mc) for uin in UINS))
        self.assertNothingForbidden()

    def test_a_written_send_that_completes_queues_the_check_after_it(self):
        now = timestamp()
        with connect(self.app.config["DATABASE"]) as db:  # a written send whose papers and grades are all done
            db.execute("INSERT INTO frq_gradescope_sends VALUES(?,?,?,?,?,?,'running','workspace',?,?)",
                       ("send-1", self.aid, str(uuid.uuid4()), "x" * 64, "f" * 64, "{}", now, now))
        self.assertEqual(self.sender.drain(), 2)  # the send settles, then its MC check is read (and its correction tried)
        read = self.reads()[-1]
        self.assertEqual((read["status"], read["send_id"]), ("read", "send-1"))
        self.assertEqual(self.sender.mc_check_status(self.aid)["counts"]["differ"], 1)
        # A key fixed in Faculty needs no new read: the comparison uses Faculty's scoring as it is now.
        key = {"defaults": {"points": "4", "scoring": "exact"}, "versions": [
            {"letter": "A", "questions": [{"number": n, "answer": a} for n, a in KEY.items()]},
            {"letter": "B", "questions": [{"number": n, "answer": a if n != 3 else "A"} for n, a in KEY.items()]}]}
        self.frq.save_bubble_key(self.aid, {"request_id": "key-2", "base_revision": 1, **key})
        self.assertEqual(self.sender.mc_check_status(self.aid)["counts"]["differ"], 0)

    def test_a_read_waits_for_a_bubble_sheet_send_still_running_and_one_check_at_a_time(self):
        self.sender.mc_check_start(self.aid)
        self.sender.mc_check_start(self.aid)  # one waiting already: nothing more is queued
        self.assertEqual(len(self.reads()), 1)
        now = timestamp()
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("INSERT INTO frq_gradescope_sheet_sends VALUES(?,?,?,?,?,?,'running','workspace',?,?)", ("sheets-live", self.aid, str(uuid.uuid4()), "x" * 64,
                                                                                                           "f" * 64, "{}", now, now))
            unit = dict(self.sender._mc_next(db, "", (), now))
        self.sender._mc_unit(unit)  # the sender's own turn (a drain would first settle that send, which has nothing left)
        self.assertEqual((self.reads()[-1]["status"], bool(self.reads()[-1]["not_before"]), self.reads()[-1]["attempts"]), ("pending", True, 0))  # waits for the sheets

    def test_a_download_gradescope_refuses_fails_the_read_for_check_mc_again(self):
        self.fake.fail("GET", r"/scores/csv\.zip$", ("status", 404))
        self.sender.mc_check_start(self.aid)
        self.sender.drain()
        read = self.reads()[-1]
        self.assertEqual(read["status"], "failed")
        mc = self.sender.mc_check_status(self.aid)
        self.assertEqual((mc["checked"], mc["failed"][0]["section"]), (False, 501))
        self.assertIn("404", mc["failed"][0]["error"])


class Certification(unittest.TestCase):
    """A student scored differently is a check to confirm in Certification, from the last read only."""

    def test_differences_are_a_confirm_check_and_an_unread_exam_is_information(self):
        differ = [{"row": "s508:e1", "name": "Example, Bob", "section": 508, "detail": "Gradescope 76.75, Faculty 80.75 · Q6: Gradescope 0, Faculty 4 (left blank)"}]
        facts = {"parts": ["frq", "bubble"], "gradescope": {"configured": True, "mc_check": {"checked": True, "differ": differ}}}
        found = [c for c in rules._gradescope_checks(facts, "") if c["id"].startswith("gradescope_mc")]
        self.assertEqual([(c["id"], c["level"], c["count"], c["rows"]) for c in found], [("gradescope_mc_differs", "confirm", 1, differ)])
        self.assertIn("digest", found[0])
        facts["gradescope"]["mc_check"] = {"checked": False, "differ": []}
        facts["gradescope"]["sheets"] = {"totals": {"sent": 3}}
        self.assertEqual([c["id"] for c in rules._gradescope_checks(facts, "") if c["id"].startswith("gradescope_mc")], ["gradescope_mc_unchecked"])


if __name__ == "__main__":
    unittest.main()
