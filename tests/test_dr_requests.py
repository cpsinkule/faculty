"""SCHEDULING "DR REQUEST EMAILS" (scheduling/dr_requests.py, requirement, 2026-11-01): the DR Testing Center's request emails are read from Gmail and kept once; a DR quiz's request opens the student's
Follow-up with one Approve card, whose approval starts Send to AIM's own upload of that quiz (the sign-in window then shows Duo);
once AIM accepted the upload, Faculty resolves the follow-up.

The quizzes, DR quiz takers and Gmail are those of tests/test_dr_quiz_notices.py (507's DR quiz takers Alice, Carol and Dana; campus
today 2026-10-12). Emails follow the DR center's own wording (2026-11-01, with the name, UIN and CRN replaced). Nothing reaches AIM,
Google or the network."""
import json
import uuid
from unittest import mock

import test_dr_quiz_notices as notice_tests
from test_aim_send import COPY
from test_dr_quiz_notices import ALICE, BOB, DANA
from test_gmail_mail import ACCOUNT, message
from test_storage import HEADERS

from assistant import policy
from scheduling import aim_send

SENDER = "DR Testing Center <info@disability.tamu.edu>"
MADE = {key: COPY[key] for key in ("draft_id", "approval_id", "version", "ready", "reasons", "title")}


def request_text(uin, section="507", kind="Quiz", day="Friday, October 23, 2026", late=False):
    return ("No action is needed at this time. This email is a notification that the student listed below has submitted a request "
            "to take your exam in the Testing Center.\n\n" + ("Late Request: Yes\n\n" if late else "") +
            f"Student: Example Student (UIN: {uin})\n\nCourse: MATH 140.{section} - MATH FOR BUSINESS AND SOC SCI (CRN: 100001)\n\n"
            f"Campus: College Station\n\nExam Type: {kind}\n\nDate: {day}\n\nStart Time: 03:00 PM\n\nStandard Length of Exam: 20 Minutes\n\n"
            "End Time: 03:30 PM (30)\n\nLocation: SSB Suite 111 (Seat Assigned at Check-in)\n\nAccommodations:\nExtra Time 1.50x\n\n"
            "Please use the following link to upload your exam to our secure website. Link to upload exam: Upload Exam (or copy and "
            "paste the following link to your browser: https://cascade.accessiblelearning.com/TAMU/ExamFile.aspx?ID=E1&Key=K1).\n")


class DrRequestsTest(notice_tests.DrQuizNoticesTest):
    def setUp(self):
        super().setUp()
        self.requests = self.app.extensions["dr_requests"]
        self.followups = self.app.extensions["dr_requests"].followups
        real = aim_send.plan

        def with_copy(db, term_id, copies):
            # Each ready quiz has an approved accessible copy (QUIZZES "ACCESSIBLE COPY"); the quiz drafts are not what is tested.
            found = real(db, term_id, copies)
            for quiz in found["quizzes"]:
                quiz.update(copy=dict(MADE, title=quiz["title"]), copy_reason="")
            return found
        for target in ("scheduling.aim_send.plan", "scheduling.dr_requests.plan"):
            patcher = mock.patch(target, side_effect=with_copy)
            patcher.start()
            self.addCleanup(patcher.stop)
        frq = self.app.extensions["frq_grading"]
        patcher = mock.patch.object(frq, "quiz_accessible_copy", return_value=(COPY["content"], COPY["filename"]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def inbox_mail(self, threads):
        self.google.threads = {key: [message(*args, **kwargs) for args, kwargs in found] for key, found in threads.items()}

    def turn(self):
        """One worker turn, with its waits behind it."""
        self.requests._read_at = self.requests._look_at = float("-inf")
        return self.requests.process_one()

    def kept(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM dr_exam_requests ORDER BY received_at, gmail_message_id")]

    def opened(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM dr_exam_request_followups ORDER BY created_at")]

    def quiz(self, title):
        with self.db() as db:
            return next(q for q in aim_send.plan(db, self.term, {})["quizzes"] if q["key"] == self.key(title))

    def followup(self, enrollment):
        items = self.client.get(f"/api/student-support?kind=followup&suggestions=1&enrollment_id={enrollment}").json["items"]
        return [item for item in items if item["id"]]

    def test_request_emails_are_kept_once_and_a_dr_quiz_request_opens_one_followup(self):
        alice, dana = self.enrollment(ALICE), self.enrollment(DANA)
        self.inbox_mail({
            "d1": [(("e1", SENDER, ACCOUNT, "[DR] New Exam Request", request_text(ALICE, late=True)), {"millis": "1792857600000"})],
            "d2": [(("e2", SENDER, ACCOUNT, "[DR] Testing Request Approved", "The following request to schedule an exam has been approved "
                     "by the Testing Center.\n\nStatus: Approved\n\n" + request_text(ALICE).split("\n\n", 1)[1]), {"millis": "1792861200000"})],
            "d3": [(("e3", SENDER, ACCOUNT, "[DR] New Exam Request", request_text(DANA, kind="Test 2", day="Thursday, October 22, 2026")), {})],
            "d4": [(("e4", "Someone <someone@example.test>", ACCOUNT, "[DR] New Exam Request", request_text(DANA)), {})],  # not the DR center
            "d5": [(("e5", SENDER, ACCOUNT, "[DR] No Show", "Student: Example Student (UIN: 001234567)"), {})],
            "d6": [(("e6", SENDER, ACCOUNT, "[DR] New Exam Request", request_text(BOB, section="508")), {})],  # 508: no DR quiz
        })
        self.assertEqual(self.requests.read(), 4)
        kept = self.kept()
        self.assertEqual([(k["gmail_message_id"], k["kind"], k["uin"], k["section"], k["exam_type"], k["date"], k["start"], k["late"])
                          for k in kept],
                         [("e3", "new", DANA, "507", "Test 2", "2026-10-22", "15:00", 0), ("e6", "new", BOB, "508", "Quiz", "2026-10-23", "15:00", 0),
                          ("e1", "new", ALICE, "507", "Quiz", "2026-10-23", "15:00", 1), ("e2", "approved", ALICE, "507", "Quiz", "2026-10-23", "15:00", 0)])
        self.assertEqual({k["gmail_message_id"]: k["enrollment_id"] for k in kept}["e1"], alice)
        self.assertNotIn("Example Student", json.dumps(kept))  # never the name
        # Read again: nothing new is kept, and no email is fetched whole again (those that were no request included).
        full = lambda: len([c for c in self.google.calls if c[2].get("format") == ["full"]])  # noqa: E731
        fetched = full()
        self.assertEqual(self.requests.read(), 0)
        self.assertEqual((len(self.kept()), full()), (4, fetched))
        # Alice's Quiz 5 request (two emails) opens one Follow-up; Dana's exam request and Bob's 508 request open none.
        self.assertTrue(self.requests.look())
        self.assertFalse(self.requests.look())
        self.assertFalse(self.turn())
        [link] = self.opened()
        self.assertEqual((link["gmail_message_id"], link["enrollment_id"], link["section_id"], link["date"]), ("e1", alice, self.s507, "2026-10-23"))
        self.assertEqual(self.followup(dana), [])
        [item] = self.followup(alice)
        self.assertEqual((item["title"], item["state"], item["attention"], item["next"]),
                         ("Upload Quiz 5's accessible copy to AIM", "todo", True, "Approve the upload; AIM's sign-in asks for your Duo."))
        self.assertIn("Friday, October 23 (section 507, a late request)", item["details"])
        [card] = [a for a in item["suggestions"]["actions"] if a.get("pending")]
        self.assertEqual((card["endpoint"], card["body"]["key"], card["body"]["enrollment_id"]), ("scheduling.aim_upload", self.key("Quiz 5"), alice))
        self.assertEqual((card["effect"]["title"], card["effect"]["button"]), ("Upload Quiz 5's accessible copy to AIM", "Approve upload"))
        self.assertTrue(card["effect"]["own"])
        self.assertNotIn("confirmation", card["body"])
        # The card is Approve, takes the instructor's confirmation and a request ID; the route refuses it without them.
        rule = policy.base_rule("scheduling.aim_upload", {"term_id": self.term})
        self.assertEqual((rule.tier, rule.confirmation, rule.request_id), ("approve", "upload", True))
        self.assertEqual(policy.base_rule("scheduling.aim_send_start", {"term_id": self.term}).tier, "never")
        route = f"/api/scheduling/terms/{self.term}/aim-send/uploads"
        self.assertEqual(self.client.post(route, json={"request_id": "r1", "key": self.key("Quiz 5")}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post(route, json={"request_id": "r1", "key": "x", "confirmation": "upload"}, headers=HEADERS).status_code, 409)

    def test_approving_starts_the_upload_and_an_accepted_upload_resolves_it(self):
        alice = self.enrollment(ALICE)
        self.inbox_mail({"d1": [(("e1", SENDER, ACCOUNT, "[DR] New Exam Request", request_text(ALICE)), {})]})
        while self.turn():
            pass
        [item] = self.followup(alice)
        [card] = [a for a in item["suggestions"]["actions"] if a.get("pending")]
        made = []

        class Browser:
            id = "s1"

            def __init__(self, portal, netid, password, *, recorder, quizzes, importer):
                made.append((netid, password, recorder, quizzes))

            def status(self):
                return {"id": "s1", "state": "starting"}

            def close(self):
                pass

            def expired(self):
                return False

        with mock.patch.object(aim_send, "browser_class", return_value=Browser), \
             mock.patch("aim_import.saved_sign_in", return_value=("netid1", "secret")):
            approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.get_data(as_text=True))
        self.assertEqual(approved.json["action"]["status"], "succeeded")
        # The send is Send to AIM's own, of Quiz 5 only, with the saved sign-in and its copy made now; the page follows session s1.
        self.assertEqual(approved.json["result"]["id"], "s1")
        self.assertEqual(approved.json["result"]["aim_upload"], {"title": "Quiz 5", "section": 507, "date": "2026-10-23"})
        [(netid, password, recorder, quizzes)] = made
        self.assertEqual((netid, password, [q["title"] for q in quizzes], quizzes[0]["copy"]["content"]), ("netid1", "secret", ["Quiz 5"], COPY["content"]))
        self.assertNotIn("secret", approved.get_data(as_text=True))
        with self.db() as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT trigger, quizzes FROM aim_send_runs")], [("instructor", 1)])
        # Still open until AIM accepts the upload: then Faculty resolves it, once.
        self.assertFalse(self.turn())
        self.assertEqual(self.followup(alice)[0]["state"], "todo")
        send = recorder.begin(self.quiz("Quiz 5"), "upload", copy=COPY, rows=["0" * 64])
        recorder.outcome(send, "accepted", "AIM counts 1 fewer exams without files.")
        self.assertTrue(self.turn())
        [done] = self.followup(alice)
        self.assertEqual(done["state"], "resolved")
        self.assertIn("AIM accepted the accessible copy of Quiz 5", done["details"])
        self.assertFalse(self.turn())

    def test_a_send_that_ended_without_uploading_gets_a_fresh_card(self):
        """AGAIN (requirement, 2026-11-01): the
        approved card read done, the window never opened and Duo's push timed out. Once that send ends without uploading, the same
        follow-up gets a fresh card, at most three in all; a send still signing in is waited for."""
        alice = self.enrollment(ALICE)
        self.inbox_mail({"d1": [(("e1", SENDER, ACCOUNT, "[DR] New Exam Request", request_text(ALICE)), {})]})
        while self.turn():
            pass
        recorders = []

        class Browser:
            id = "s1"

            def __init__(self, portal, netid, password, *, recorder, quizzes, importer):
                recorders.append(recorder)

            def status(self):
                return {"id": self.id, "state": "starting"}

            def close(self):
                pass

            def expired(self):
                return False

        def pending():
            [item] = self.followup(alice)
            return [a for a in item["suggestions"]["actions"] if a.get("pending")]
        for tried in range(3):
            [card] = pending()
            self.assertEqual(card["body"].get("attempt"), tried + 1 if tried else None)
            with mock.patch.object(aim_send, "browser_class", return_value=Browser), \
                 mock.patch("aim_import.saved_sign_in", return_value=("netid1", "secret")):
                approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
            self.assertEqual(approved.json["action"]["status"], "succeeded", approved.get_data(as_text=True))
            self.assertFalse(self.turn())  # still signing in: nothing new
            self.assertEqual(pending(), [])
            if tried == 1:  # a restart cut the send off: no end is recorded, and it is over once STALE has passed
                with mock.patch("scheduling.dr_requests.STALE", -60):
                    self.assertEqual(self.turn(), True)
                continue
            recorders[-1].end("closed", "The window closed, or the browser ended, before the send finished.")
            self.assertEqual(self.turn(), tried < 2)
        self.assertEqual(pending(), [])  # three tries: the follow-up is left for the instructor
        self.assertEqual(self.followup(alice)[0]["state"], "todo")

    def test_a_followup_the_instructor_saved_is_theirs(self):
        alice = self.enrollment(ALICE)
        self.inbox_mail({"d1": [(("e1", SENDER, ACCOUNT, "[DR] New Exam Request", request_text(ALICE)), {})]})
        while self.turn():
            pass
        [item] = self.followup(alice)
        saved = self.client.post(f"/api/student-support/{item['id']}", headers=HEADERS, json={
            "title": item["title"], "details": "I uploaded it myself.", "state": "waiting", "date": "", "base_revision": item["revision"],
            "request_id": str(uuid.uuid4())})
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        recorder = aim_send.Recorder(self.app.extensions["scheduling"], self.term, "run-00000009", "workspace")
        recorder.started("instructor", 1)
        recorder.outcome(recorder.begin(self.quiz("Quiz 5"), "upload", copy=COPY, rows=["0" * 64]), "accepted")
        self.assertFalse(self.turn())
        self.assertEqual(self.followup(alice)[0]["state"], "waiting")


for _name in dir(notice_tests.DrQuizNoticesTest):  # DR QUIZ NOTICES' own tests run in their module only
    if _name.startswith("test_"):
        setattr(DrRequestsTest, _name, None)
