"""SCHEDULING "SEND TO AIM" (scheduling/aim_send.py, requirement, 2026-10-27): the instructor's Send adds each ready DR
quiz's date to its section in AIM (type Quiz, the day and 20 minutes, never a time), records its "Prescheduled in AIM" mark once
AIM lists it, uploads the accessible copy to the students' requests AIM lists for it, keeps a receipt of every write, and holds an
unconfirmed one for Retry.

AIM is a fake built from AIM's own pages (tests/fixtures/aim: Exam Dates, a section's List Exam Dates form and Upcoming Exams, saved
from the instructor's AIM on 2026-10-27 with every student name, number and opaque key replaced). Nothing reaches AIM or the network;
the quizzes, DR quiz takers and Gmail are those of tests/test_dr_quiz_notices.py."""
import datetime as dt
import hashlib
import json
import re
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl, urlsplit

import test_dr_quiz_notices as notice_tests
from test_storage import HEADERS

from assistant import policy
from rosters import connect
from scheduling import aim_send

PAGES = Path(__file__).parent / "fixtures" / "aim"
BASE = "https://aim.test/tamu/instructor/"
SECTIONS = {"525928": "507", "525917": "508", "524476": "514", "524475": "515"}
COPY = {"draft_id": "d1", "approval_id": "a1", "version": "A", "ready": True, "reasons": [], "title": "Quiz",
        "content": b"PK\x03\x04 accessible copy", "filename": "Quiz-accessible.docx"}
COPY["sha256"] = hashlib.sha256(COPY["content"]).hexdigest()


def page(name):
    return (PAGES / f"{name}.html").read_text()


class Answer:
    def __init__(self, url, markup, status=200):
        self.url, self._markup, self.status, self.ok = url, markup, status, 200 <= status < 300

    def text(self):
        return self._markup

    def body(self):
        return self._markup.encode()


class FakeAim:
    """AIM's three pages as saved from the instructor's AIM, with the dates and requests a test adds, behind `get` and `post` as the
    signed-in browser's request context answers them."""

    def __init__(self):
        self.dates, self.requests, self.uploaded, self.posts = [], [], {}, []
        self.save = "list"      # list: the saved date shows in the list; drop: AIM answers but lists nothing; error: AIM answers 500
        self.upload = "count"   # count: the uploaded requests leave AIM's count of exams without files; same: the count stays
        self._cid = None

    def dates_page(self):
        markup = page("dates")
        template = re.search(r'<tr class="ar">(?:(?!<tr).)*?<td>507</td><td>Test 2</td>(?:(?!<tr).)*?</tr>', markup, re.S).group(0)
        rows = "".join(re.sub(r"<td>507</td><td>Test 2</td><td>\s*10/22/2026", f"<td>{section}</td><td>{kind}</td><td>{day}", template)
                       for section, kind, day in self.dates)
        return markup.replace(template, template + rows, 1)

    def upcoming_page(self):
        markup = page("upcoming")
        rows = re.findall(r'<tr class="ar">(?:(?!<tr).)*?</tr>', markup, re.S)
        template, extra = rows[0], []
        for index, (section, kind, day, name) in enumerate(self.requests):
            number = f"ctl{60 + index:02d}"
            row = re.sub(r"GV([_$])ctl\d\d", lambda m: f"GV{m.group(1)}{number}", template)
            row = re.sub(r"<td>140</td><td>\d{3}</td>", f"<td>140</td><td>{section}</td>", row)
            row = re.sub(r"Student \w+", name, row)
            row = re.sub(r"<td>Test 3</td><td>\s*\d\d/\d\d/\d{4}", f"<td>{kind}</td><td>{day}", row)
            extra.append(row)
        markup = markup.replace(rows[-1], rows[-1] + "".join(extra), 1)
        count = 23 + len(self.requests) - sum(1 for key in self.uploaded if key.startswith("ctl6"))
        return re.sub(r'(Number of Exams without Files Uploaded</span>\s*<span class="value">)23', rf"\g<1>{count}", markup)

    def get(self, address, timeout=None):
        parts = urlsplit(address)
        query = dict(parse_qsl(parts.query))
        name = parts.path.rsplit("/", 1)[-1]
        if name == "Testing.aspx" and query.get("Step") == "Dates":
            return Answer(address, self.dates_page())
        if name == "Testing.aspx" and query.get("Step") == "Exams":
            return Answer(address, self.upcoming_page())
        if name == "InstructionAdd.aspx" and query.get("Step") == "ExamTypes" and query.get("CID") in SECTIONS:
            self._cid = query["CID"]
            return Answer(address, page("add-507").replace("CID=525928", f"CID={query['CID']}"))
        return Answer(address, "Not found", 404)

    def post(self, action, data=None, multipart=None, headers=None, timeout=None):
        fields = dict(parse_qsl(data, keep_blank_values=True)) if data is not None else dict(multipart)
        target = fields["__EVENTTARGET"]
        self.posts.append((urlsplit(action).path.rsplit("/", 1)[-1], target, fields))
        if target == aim_send.SAVE:
            if self.save == "error":
                return Answer(action, "Server Error", 500)
            if self.save == "list":
                self.dates.append((SECTIONS[dict(parse_qsl(urlsplit(action).query))["CID"]], "Quiz", fields[aim_send.DATE]))
            return Answer(action, page("add-507"))
        if target == aim_send.UPLOAD_BUTTON:
            if self.upload == "count":
                for key in fields:
                    if key.endswith("$ChkBox"):
                        self.uploaded[key.split("$")[-2]] = fields[aim_send.UPLOAD_FILE]
            return Answer(action, self.upcoming_page())
        return Answer(action, self.upcoming_page())


class AimPagesTest(unittest.TestCase):
    """The readers on AIM's own pages, as saved."""

    def test_exam_dates_and_each_sections_form(self):
        listed, pages = aim_send.exam_dates(page("dates"), BASE)
        self.assertEqual(len(listed), 16)
        self.assertIn(("507", "test 2", "2026-10-22"), listed)
        self.assertIn(("515", "final", "2026-12-09"), listed)
        self.assertEqual(pages["508"], BASE + "InstructionAdd.aspx?Step=ExamTypes&From=Class&CID=525917&ID=OPAQUE")
        self.assertEqual(set(pages), {"507", "508", "514", "515"})

    def test_upcoming_requests_and_their_count(self):
        rows = aim_send.requests(page("upcoming"))
        self.assertEqual(len(rows), 23)
        self.assertEqual({key: rows[1][key] for key in ("section", "type", "date")}, {"section": "515", "type": "Test 3", "date": "2026-11-19"})
        self.assertTrue(rows[1]["box"].endswith("GV$ctl03$ChkBox"))
        self.assertNotIn("Student", json.dumps(rows))  # a request is its row's digest; no name leaves the page
        self.assertEqual(aim_send.without_files(page("upcoming")), 23)

    def test_the_add_form_has_a_quiz_type_and_an_optional_time(self):
        markup = page("add-507")
        self.assertIn(("1", "Quiz"), aim_send.options(markup, aim_send.TYPE))
        self.assertEqual(aim_send.options(markup, aim_send.HOUR)[0], ("", "Select"))
        from aim_import import parse_form
        self.assertTrue({aim_send.SAVE, aim_send.DATE, aim_send.LENGTH, aim_send.HOUR, aim_send.MINUTE} <= parse_form(markup).names)


class AimSendTest(notice_tests.DrQuizNoticesTest):
    def setUp(self):
        super().setUp()
        self.schedule = self.app.extensions["scheduling"]
        self.aim = FakeAim()
        self.writer = aim_send.AimWriter(self.aim, BASE)

    def ready(self, copy=True):
        with connect(self.app.config["DATABASE"]) as db:
            found = aim_send.plan(db, self.term, {})
        return [{**quiz, "copy": dict(COPY, title=quiz["title"]) if copy else None} for quiz in found["quizzes"]]

    def send(self, quizzes=None, run="run-00000001"):
        recorder = aim_send.Recorder(self.schedule, self.term, run, "workspace")
        return aim_send.run(self.writer, quizzes if quizzes is not None else self.ready(), recorder)

    def sends(self):
        with connect(self.app.config["DATABASE"]) as db:
            return aim_send._sends(db, self.term)

    def did(self, report):
        return [(line["title"], line["did"]) for line in report]

    def test_send_adds_each_date_marks_it_and_uploads_to_registered_students(self):
        report = self.send()
        # The three ready quizzes of 507 (Quiz 9 has no date; 508 has no DR quiz taker), each a Quiz of 20 minutes on its day, at
        # 10 minutes after 507's class ends that day (08:50, so 9:00; requirement, 2026-10-27).
        saves = [fields for _, target, fields in self.aim.posts if target == aim_send.SAVE]
        self.assertEqual([(f[aim_send.TYPE], f[aim_send.DATE], f[aim_send.LENGTH], f[aim_send.HOUR], f[aim_send.MINUTE]) for f in saves],
                         [("1", "10/23/2026", "20", "09", "00"), ("1", "10/26/2026", "20", "09", "00"), ("1", "10/28/2026", "20", "09", "00")])
        self.assertEqual({path for path, target, _ in self.aim.posts if target == aim_send.SAVE}, {"InstructionAdd.aspx"})
        self.assertEqual(self.did(report), [("Quiz 5", "date_added"), ("Quiz 5", "marked"), ("Quiz 6", "date_added"), ("Quiz 6", "marked"),
                                            ("Quiz 7", "date_added"), ("Quiz 7", "marked"), ("Quiz 5", "upload_waiting"),
                                            ("Quiz 6", "upload_waiting"), ("Quiz 7", "upload_waiting")])
        # Each mark is DR QUIZ NOTICES' own, so the section's DR quiz takers are emailed to register.
        self.assertEqual([(row["action"], row["date"], row["actor"]) for row in self.table("dr_quiz_marks")],
                         [("mark", "2026-10-23", "workspace"), ("mark", "2026-10-26", "workspace"), ("mark", "2026-10-28", "workspace")])
        self.assertEqual([(s["kind"], s["date"], s["status"]) for s in self.sends()],
                         [("date", "2026-10-23", "accepted"), ("date", "2026-10-26", "accepted"), ("date", "2026-10-28", "accepted")])
        # Two students register for Quiz 5: the next Send adds nothing and uploads the copy to both requests, once.
        self.aim.requests += [("507", "Quiz", "10/23/2026", "Student Alpha"), ("507", "Quiz", "10/23/2026", "Student Beta"),
                              ("508", "Quiz", "10/23/2026", "Student Gamma")]
        report = self.send(run="run-00000002")
        self.assertEqual(self.did(report)[:3], [("Quiz 5", "date_listed"), ("Quiz 6", "date_listed"), ("Quiz 7", "date_listed")])
        self.assertIn(("Quiz 5", "uploaded"), self.did(report))
        uploads = [fields for _, target, fields in self.aim.posts if target == aim_send.UPLOAD_BUTTON]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(sorted(key.split("$")[-2] for key in uploads[0] if key.endswith("$ChkBox")), ["ctl60", "ctl61"])  # not 508's
        self.assertEqual(uploads[0][aim_send.UPLOAD_FILE]["buffer"], COPY["content"])
        self.assertEqual(uploads[0][aim_send.UPLOAD_TITLE], "Quiz 5 (accessible copy)")
        [upload] = [s for s in self.sends() if s["kind"] == "upload"]
        self.assertEqual((upload["status"], len(json.loads(upload["rows"])), upload["file_sha256"]), ("accepted", 2, COPY["sha256"]))
        self.assertNotIn("Student", upload["rows"])
        # Nothing new: no post at all. A third student registers: only their request gets the copy.
        posts = len(self.aim.posts)
        self.send(run="run-00000003")
        self.assertEqual([target for _, target, _ in self.aim.posts[posts:] if target in (aim_send.SAVE, aim_send.UPLOAD_BUTTON)], [])
        self.aim.requests.append(("507", "Quiz", "10/23/2026", "Student Delta"))
        self.send(run="run-00000004")
        last = [fields for _, target, fields in self.aim.posts if target == aim_send.UPLOAD_BUTTON][-1]
        self.assertEqual([key.split("$")[-2] for key in last if key.endswith("$ChkBox")], ["ctl63"])

    def test_the_time_is_ten_minutes_after_the_sections_class_ends_that_day(self):
        self.assertEqual([quiz["aim_time"] for quiz in self.ready()], ["09:00", "09:00", "09:00"])
        from scheduling.class_schedule import TermRead
        with connect(self.app.config["DATABASE"]) as db:
            memo = TermRead(db, self.term)
            quiz = self.ready()[0]
            # Rounded up to AIM's five-minute steps; a day with no class takes the quiz row's own end; none at all, no time.
            with mock.patch.object(memo, "meetings", return_value=({quiz["date"]: [{"time": "8:00 AM", "end_time": "9:47 AM"}]}, set())):
                self.assertEqual(aim_send.sitting(memo, quiz), "10:00")
            with mock.patch.object(memo, "meetings", return_value=({}, set())):
                self.assertEqual(aim_send.sitting(memo, {**quiz, "end": "13:20"}), "13:30")
                self.assertEqual(aim_send.sitting(memo, {**quiz, "end": ""}), "")
                self.assertEqual(aim_send.sitting(memo, {**quiz, "end": "23:55"}), "")
        report = self.send([{**quiz, "aim_time": "", "copy": None}])
        self.assertEqual(self.did(report), [("Quiz 5", "date_failed")])
        self.assertIn("no class end time", report[0]["detail"])
        self.assertEqual([t for _, t, _ in self.aim.posts if t == aim_send.SAVE], [])

    def test_a_date_aim_already_lists_is_only_marked(self):
        self.aim.dates.append(("507", "Quiz", "10/26/2026"))
        report = self.send(self.ready(copy=False))
        self.assertIn(("Quiz 6", "date_listed"), self.did(report))
        self.assertEqual([f[aim_send.DATE] for _, target, f in self.aim.posts if target == aim_send.SAVE], ["10/23/2026", "10/28/2026"])
        self.assertEqual(len(self.table("dr_quiz_marks")), 3)

    def test_an_unconfirmed_date_waits_for_retry_and_a_later_read_settles_it(self):
        self.aim.save = "drop"  # AIM answers, but its list does not show the date
        report = self.send(self.ready(copy=False)[:1])
        self.assertEqual(self.did(report), [("Quiz 5", "date_uncertain")])
        self.assertEqual(self.table("dr_quiz_marks"), [])  # no mark, so no student is emailed
        [held] = self.sends()
        self.assertEqual(held["status"], "uncertain")
        # Never posted again by itself.
        posts = len(self.aim.posts)
        self.assertEqual(self.did(self.send(self.ready(copy=False)[:1], run="run-00000002")), [("Quiz 5", "date_held")])
        self.assertEqual([t for _, t, _ in self.aim.posts[posts:] if t == aim_send.SAVE], [])
        # The plan shows it held; Retry releases it, and the next Send posts it again.
        with connect(self.app.config["DATABASE"]) as db:
            found = aim_send.plan(db, self.term, {})
        self.assertEqual(found["quizzes"][0]["date_send"]["status"], "uncertain")
        retried = self.client.post(f"/api/scheduling/terms/{self.term}/aim-send/retry", json={"request_id": "r1", "send_id": held["id"]}, headers=HEADERS)
        self.assertEqual(retried.status_code, 200, retried.get_data(as_text=True))
        self.assertEqual(retried.json["quizzes"][0]["date_send"]["status"], "released")
        again = self.client.post(f"/api/scheduling/terms/{self.term}/aim-send/retry", json={"request_id": "r2", "send_id": held["id"]}, headers=HEADERS)
        self.assertEqual(again.status_code, 409)
        self.aim.save = "list"
        self.assertEqual(self.did(self.send(self.ready(copy=False)[:1], run="run-00000003")), [("Quiz 5", "date_added"), ("Quiz 5", "marked")])
        # An uncertain write that AIM's list shows later is settled by that read, without a post.
        self.aim.save = "drop"
        self.send(self.ready(copy=False)[1:2], run="run-00000004")
        self.aim.dates.append(("507", "Quiz", "10/26/2026"))
        posts = len(self.aim.posts)
        self.assertEqual(self.did(self.send(self.ready(copy=False)[1:2], run="run-00000005")), [("Quiz 6", "date_listed"), ("Quiz 6", "marked")])
        self.assertEqual([t for _, t, _ in self.aim.posts[posts:] if t == aim_send.SAVE], [])
        self.assertEqual([s["status"] for s in self.sends() if s["date"] == "2026-10-26"], ["accepted"])

    def test_a_refused_date_is_failed_and_an_unconfirmed_upload_held(self):
        self.aim.save = "error"
        self.assertEqual(self.did(self.send(self.ready()[:1])), [("Quiz 5", "date_failed")])
        self.assertEqual([s["status"] for s in self.sends()], ["failed"])
        self.aim.save, self.aim.upload = "list", "same"
        self.aim.requests.append(("507", "Quiz", "10/23/2026", "Student Alpha"))
        report = self.send(self.ready()[:1], run="run-00000002")
        self.assertEqual(self.did(report), [("Quiz 5", "date_added"), ("Quiz 5", "marked"), ("Quiz 5", "upload_uncertain")])
        posts = len(self.aim.posts)
        self.assertEqual(self.did(self.send(self.ready()[:1], run="run-00000003"))[-1], ("Quiz 5", "upload_held"))
        self.assertEqual([t for _, t, _ in self.aim.posts[posts:] if t == aim_send.UPLOAD_BUTTON], [])

    def test_a_page_not_as_mapped_stops_before_anything_is_posted(self):
        with mock.patch.object(self.aim, "dates_page", return_value="<html><table><tr><td>Nothing</td></tr></table></html>"):
            with self.assertRaisesRegex(aim_send.Stopped, "no table with Section, Type, Exam Date"):
                self.send()
        self.assertEqual(self.aim.posts, [])
        with mock.patch.object(self.aim, "get", return_value=Answer(BASE + "Login.aspx", "Access Policy")):
            with self.assertRaisesRegex(aim_send.Stopped, "access policy"):
                self.send()

    def test_routes_plan_start_and_policy(self):
        plan = self.client.get(f"/api/scheduling/terms/{self.term}/aim-send")
        self.assertEqual(plan.status_code, 200, plan.get_data(as_text=True))
        self.assertEqual([(q["title"], q["date"], q["minutes"], q["copy"], q["copy_reason"], q["date_send"]) for q in plan.json["quizzes"]],
                         [("Quiz 5", "2026-10-23", 20, None, "Not drafted in Faculty", None), ("Quiz 6", "2026-10-26", 20, None, "Not drafted in Faculty", None),
                          ("Quiz 7", "2026-10-28", 20, None, "Not drafted in Faculty", None)])
        self.assertEqual(plan.json["fingerprint"], self.quizzes()["fingerprint"])
        route = f"/api/scheduling/terms/{self.term}/aim-send"
        stale = self.client.post(route, json={"request_id": "r1", "fingerprint": "0" * 64, "saved": True}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        made = []

        class Browser:
            id = "s1"

            def __init__(self, portal, netid, password, *, recorder, quizzes, importer):
                made.append((portal, netid, password, recorder, quizzes))

            def status(self):
                return {"id": "s1", "state": "starting"}

            def close(self):
                pass

            def expired(self):
                return False

        with mock.patch.object(aim_send, "browser_class", return_value=Browser), \
             mock.patch("aim_import.saved_sign_in", return_value=("netid1", "secret")):
            started = self.client.post(route, json={"request_id": "r2", "fingerprint": plan.json["fingerprint"], "saved": True}, headers=HEADERS)
        self.assertEqual(started.status_code, 201, started.get_data(as_text=True))
        [(portal, netid, password, recorder, quizzes)] = made
        self.assertEqual((netid, password, [q["title"] for q in quizzes]), ("netid1", "secret", ["Quiz 5", "Quiz 6", "Quiz 7"]))
        self.assertIs(self.app.extensions["aim_sessions"].get("s1").__class__, Browser)
        self.assertNotIn("secret", started.get_data(as_text=True))
        # The assistant never starts it (an AIM sign-in window); Retry is an Approve card with a request ID.
        self.assertEqual(policy.base_rule("scheduling.aim_send_start", {"term_id": self.term}).tier, "never")
        self.assertIn("scheduling.aim_send_retry", policy.REQUEST_IDS)
        self.assertEqual(policy.base_rule("scheduling.aim_send_retry", {"term_id": self.term}).tier, "approve")
        self.assertEqual(self.client.post(route, json={"request_id": "r3", "fingerprint": "x"}, headers={}).status_code, 403)


class AimSendAutomationTest(AimSendTest):
    """What makes it "fairly automated" (requirement, 2026-10-27): the bookings import itself after a send, every send is a run with
    its end, Home counts the DR quizzes a Send would act on and the writes waiting for Retry, and the restart check waits for a
    send."""

    def overview(self):
        found = self.client.get("/api/workspace/overview")
        self.assertEqual(found.status_code, 200, found.get_data(as_text=True))
        return next((card for card in found.json["attention"] if card["key"] == "aim_send"), None)

    def test_home_counts_what_a_send_would_do_and_what_waits_for_retry(self):
        card = self.overview()
        self.assertEqual((card["label"], card["count"], card["href"], card["verb"]), ("Send to AIM", 3, "#accommodations?tab=quizzes", "Send"))
        self.assertEqual(card["detail"], "3 DR quizzes to send to AIM.")
        self.send(self.ready(copy=False))
        self.assertIsNone(self.overview())
        # A quiz moved to a new day is due again; a write AIM did not confirm waits for Retry instead.
        self.aim.save = "drop"
        quiz7 = next(i for i in self.plan()["items"] if i["title"] == "Quiz 7")
        self.changed(set=[{**quiz7, "date": "2026-10-30"}])
        self.assertEqual(self.overview()["count"], 1)
        self.send([q for q in self.ready(copy=False) if q["title"] == "Quiz 7"], run="run-00000002")
        card = self.overview()
        self.assertEqual((card["label"], card["count"], card["detail"], card["verb"]), ("AIM writes to check", 1, "1 AIM write waiting for Retry.", "Check"))

    def test_upload_is_due_daily_until_every_dr_student_has_the_file(self):
        """to the card staying up every day until each DR student's request has its file."""
        quiz = {"key": "k", "title": "Quiz 6", "section": 507, "dr_students": 2, "date": "2026-10-26", "assignment_id": "a1",
                "date_send": {"status": "accepted"}, "uploads": []}
        found = lambda **changes: {"quizzes": [{**quiz, **changes}]}  # noqa: E731
        # Every day from the date's acceptance until the day before the quiz, however far ahead.
        self.assertEqual([bool(aim_send.due(found(), [], day)) for day in ("2026-10-12", "2026-10-22", "2026-10-25", "2026-10-26", "2026-10-27")],
                         [True, True, True, False, False])
        self.assertEqual(aim_send.due(found(), [], "2026-10-20")[0]["why"], "upload")
        # Until accepted uploads cover a request for each of the section's DR quiz takers; an unconfirmed upload does not count.
        one = {"status": "accepted", "requests": 1}
        self.assertTrue(aim_send.due(found(uploads=[one, {"status": "uncertain", "requests": 1}]), [], "2026-10-20"))
        self.assertEqual(aim_send.due(found(uploads=[one, one]), [], "2026-10-20"), [])
        self.assertEqual(aim_send.due(found(uploads=[{"status": "accepted", "requests": 2}]), [], "2026-10-20"), [])
        # Once a send of the term ended sent today, not again until tomorrow; a quiz not drafted in Faculty has no copy to upload.
        self.assertEqual(aim_send.due(found(), [{"day": "2026-10-20", "state": "sent"}], "2026-10-20"), [])
        self.assertTrue(aim_send.due(found(), [{"day": "2026-10-19", "state": "sent"}], "2026-10-20"))
        self.assertEqual(aim_send.due(found(assignment_id=None), [], "2026-10-20"), [])
        self.assertEqual(aim_send.due(found(date_send={"status": "uncertain"}), [], "2026-10-20"), [])
        self.assertEqual(aim_send.due(found(date_send={"status": "released"}), [], "2026-10-20")[0]["why"], "date")

    def test_the_bookings_import_themselves_and_each_send_ends(self):
        from test_dr_exams import booking, workbook
        dr_exams, inbox = self.app.extensions["dr_exams"], self.app.extensions["inbox"]
        export = {"name": "Exams_20261027.xlsx", "content": workbook([booking(**{"Exam Type": "Quiz", "Date": dt.datetime(2026, 10, 23)})])}
        imported = aim_send.import_bookings(dr_exams, inbox, export, "run-00000001")
        self.assertTrue(imported["imported"])
        self.assertTrue(inbox.process_one())
        self.assertEqual(inbox.get(imported["job"])["status"], "completed", inbox.get(imported["job"]))
        stranger = {"name": "Exams.xlsx", "content": workbook([booking(UIN="999999999", **{"First Name": "Nobody"})])}
        self.assertEqual(aim_send.import_bookings(dr_exams, inbox, stranger, "run-00000002")["imported"], False)

        # The browser's end: after the writes, the export is imported and the send ends "done"; the run records how it ended.
        recorder = aim_send.Recorder(self.schedule, self.term, "run-00000003", "workspace")
        recorder.started("instructor", 3)
        browser = self.browser(recorder, export)
        browser._on_aim(None, "", [], 0)
        self.assertEqual((browser.state, browser.status()["imported"]["imported"]), ("done", True))
        self.assertEqual([line["did"] for line in browser.status()["sent"]][:2], ["date_added", "marked"])
        with connect(self.app.config["DATABASE"]) as db:
            runs = aim_send._runs(db, self.term)
        self.assertEqual([(r["trigger"], r["day"], r["quizzes"], r["state"]) for r in runs], [("instructor", "2026-10-12", 3, "sent")])
        # A page not as mapped ends the run stopped, and the export still goes to the import.
        recorder = aim_send.Recorder(self.schedule, self.term, "run-00000004", "workspace")
        recorder.started("instructor", 3)
        browser = self.browser(recorder, export)
        with mock.patch.object(self.aim, "dates_page", return_value="<table><tr><td>x</td></tr></table>"):
            browser._on_aim(None, "", [], 0)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(aim_send._runs(db, self.term)[-1]["state"], "stopped")
        self.assertEqual(browser.status()["sent"][-1]["did"], "stopped")
        # The restart check waits while a send signs in or writes.
        sessions = self.app.extensions["aim_sessions"]
        sessions.install(lambda: browser)
        browser.state = "signing_in"
        self.assertEqual(self.app.extensions["assistant"].coding.restart_check()["busy"].get("AIM sends"), 1)
        browser.state = "done"
        self.assertNotIn("AIM sends", self.app.extensions["assistant"].coding.restart_check()["busy"])

    def browser(self, recorder, exported):
        """An AimSendBrowser whose thread never starts, signed in to the fake AIM, exporting `exported`."""
        with mock.patch("aim_import.threading.Thread"):
            browser = aim_send.browser_class()(BASE, None, None, recorder=recorder, quizzes=self.ready(copy=False),
                                               importer=lambda found: aim_send.import_bookings(self.app.extensions["dr_exams"],
                                                                                               self.app.extensions["inbox"], found, recorder.run_id))
        browser._context = mock.Mock(request=self.aim)

        def export(kinds):
            browser.state, browser.result = "exported", {"term": "", "files": {"exams": {"kind": "exams", "size": 1, **exported}}, "problems": []}
        browser._export = export
        return browser


def load_tests(loader, tests, pattern):
    """This module's own tests only: AimSendTest borrows DR QUIZ NOTICES' set-up, not its tests."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(AimPagesTest))
    for case in (AimSendTest, AimSendAutomationTest):
        suite.addTests(case(name) for name in sorted(vars(case)) if name.startswith("test_"))
    return suite


if __name__ == "__main__":
    unittest.main()
