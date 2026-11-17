"""DEPARTMENT MAKEUP EXAMS (ABSENCES; absences/department_makeups.py and makeup_form.py; requirement, 2026-10-22), on a
scratch workspace with a fake Google Forms host and a fake Gmail; dates in 2036 so no real day passes them:

- the form is read by its questions' titles; a required question Faculty does not know, or one it answers that is gone, stops it;
- a student owes a request when an excused absence covers the day an exam (an assignment with a seating chart) was given to their
  section; a quiz, an undecided absence and one that misses the day owe none;
- the response carries the instructor, course, section, the roster's last and first name, the UIN, the first session after the
  absence, the exam's rules and the instructor's TAMU email; nothing is sent without "submit" and the preview's fingerprint;
- Google's answer is recorded once: accepted (no makeup sitting is made: the department runs it), refused (a new card may
  try again) or unknown (never sent again); a request ID replays its answer;
- receipts read from Gmail confirm Faculty's request, and one the instructor made on the form stands for that student's request
  and gives the next card the exam's rules;
- on the Follow-up page it is an Approve card whose session and rules are dropdowns, and the preparation's facts say what is owed;
- once Google accepts a request, the student is emailed once from the instructor's Gmail (the fake in tests/test_gmail_mail.py) with
  the makeup's day, time and room and that missing it means a 0 (STUDENT NOTICE, 2026-10-22); never resent by itself."""
import base64
import datetime as dt
import html
import json
import os
import tempfile
import time
import uuid
from unittest import mock

from test_absences import Base
from test_gmail_mail import ACCOUNT, FakeGmail, FakeTokens
from test_followup_suggestions import ScriptedEngine  # noqa: F401 - the follow-up base's engine (imported for its patch)
from test_storage import HEADERS

from absences import department_makeups as department_module
from absences import makeup_form
from assistant import policy
from response_cards.gmail import MAIL_SCOPES, SCOPES
from rosters import connect

EXAM_DAY = "2036-10-22"
TODAY = dt.date(2036, 10, 23)
SESSIONS = ["Monday, October 20, 2036 7:30 PM-9:30 PM; BLOC 169", "Friday, October 24, 2036 4:30 PM-6:30 PM; BLOC 169",
            "Monday, October 27, 2036 7:30 PM-9:30 PM; BLOC 169", "Friday, October 31, 2036 4:30 PM-6:30 PM; BLOC 169",
            "Friday, December 5, 2036 4:30 PM-6:30 PM; BLOC 169; Reading Day"]
FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSefwFFFYIxrl2AZFszGtPU2AF6i9CterzooUibge2RLJa629A/viewform"
RULES = {"time": ["50 min", "60 min", "75 min", "2 hours"], "scantron": ["Provided by Instructor", "882-E", "815-E", "TAMU", "None"],
         "documentation": ["Yes", "No"], "calculator": ["No Calculator Allowed", "Any Calculator is fine", "TI-83 or TI-84 Graphing Calculator Only"],
         "reset": ["Yes, only before the exam", "Yes, before AND after the exam", "No reset necessary or N/A"]}
CHOSEN = {"time": "75 min", "scantron": "Provided by Instructor", "calculator": "TI-83 or TI-84 Graphing Calculator Only",
          "reset": "Yes, before AND after the exam"}


def form_page(extra=None, drop=None):
    """A public form page shaped as Google serves the department's (FB_PUBLIC_LOAD_DATA_; ids as on the real form)."""
    def text(item, entry, title, required=True):
        return [item, title, None, 0, [[entry, None, 1 if required else 0]]]

    def choice(item, entry, title, options, kind=2, other=False, targets=None):
        opts = [[o, None, (targets or {}).get(o), None, 0] for o in options] + ([["", None, None, None, 1]] if other else [])
        return [item, title, None, kind, [[entry, opts, 1]]]
    items = [
        choice(640095302, 211328525, "You must be the instructor of the course to schedule a make up exam. Are you the instructor of the course? ",
               ["Yes", "No"], targets={"Yes": 1706442012, "No": 2129720842}),
        [2129720842, "Only Instructors Can Fill this Out", None, 8, None],
        [1997722352, "Students and other instructional support staff cannot fill out this form.", None, 6, None],
        [1706442012, "Makeup Exam Information", "Fill out the information below.", 8, None],
        text(1516021289, 717335134, "Instructor's Last Name"), text(668467946, 1261361684, "Instructor's First Name"),
        text(1061833507, 213234033, "Math Course Number (e.g. 151)"), text(1844350677, 505773546, "Section Number"),
        text(1061476617, 2147182980, "Student's Last Name"), text(44078057, 1786728933, "Student's First Name"),
        text(1240136373, 1031047010, "Student's UIN"),
        choice(42712261, 1932510432, "Select the date this student will take the make-up exam.", SESSIONS, kind=3),
        choice(670517589, 1270488888, "Time Allotted", RULES["time"], other=True),
        choice(731208399, 300209411, "Scantron Type", RULES["scantron"]),
        choice(1646892292, 2091324714, "Attach Excuse/Documentation", RULES["documentation"]),
        choice(1606732182, 1736007589, "Information about Calculators", RULES["calculator"]),
        choice(285243529, 193982968, "Reset calculator memory?", RULES["reset"]),
        [1744261299, "Any Special Instructions?", None, 1, [[176801956, None, 0]]],
    ]
    if drop:
        items = [i for i in items if drop not in str(i[1])]
    if extra:
        items.append(extra)
    info = ["Please fill out the information below.", items, ["Your information has been recorded and you should receive a confirmation email.", 1, 0, 0, 0],
            None, None, None, None, None, "Fall 2036 Makeup Exam Request Form "]
    data = json.dumps([None, info, "/forms", "Fall 2036 Makeup Exam Request Form"])
    return (f"<html><script>var FB_PUBLIC_LOAD_DATA_ = {data};</script>"
            '<input type="hidden" name="fvv" value="1"><input type="hidden" name="partialResponse" value="[null,null,&quot;-91&quot;]">'
            '<input type="hidden" name="pageHistory" value="0"><input type="hidden" name="token" value="t-0">'
            '<input type="hidden" name="fbzx" value="-91"></html>')


SESSION = {"cookies": {"SID": "kept-sid", "__Secure-1PSIDTS": "old"}, "email": "instructor@tamu.edu", "sign_in": "s-1"}


class FakeForms:
    """docs.google.com: GET serves the form (to the signed-in account when the kept session's cookies come with it), POST records a
    response only from a signed-in account (Google's 401 otherwise, as on 2026-10-22), or fails as told."""

    def __init__(self):
        self.page, self.posts, self.steps, self.fail, self.cookies = form_page(), [], [], None, []

    def __call__(self, method, url, data=None, timeout=30, cookies=None):
        assert url.startswith("https://docs.google.com/forms/d/e/"), url
        self.cookies.append(dict(cookies or {}))
        signed = (cookies or {}).get("SID") == "kept-sid"
        renewed = {"__Secure-1PSIDTS": "new"} if signed else {}
        if method == "GET":
            return 200, self.page.replace("</html>", f'<div data-user-email-address="{"instructor@tamu.edu" if signed else ""}"></div></html>'), renewed
        if data.get("continue") == "1":  # PAGE BY PAGE: the first page's Next, handing on its token and the answers so far
            self.steps.append(dict(data))
            if not signed:
                return 401, "<title>Page Not Found</title>", {}
            assert data.get("token") == "t-0" and data.get("emailAddress") == "instructor@tamu.edu" and "entry.1031047010" not in data, data
            return 200, (self.page + '<input type="hidden" name="partialResponse" value="[[[null,211328525,[&quot;Yes&quot;],0]]]">'
                         '<input type="hidden" name="pageHistory" value="0,2"><input type="hidden" name="token" value="t-2">'), renewed
        self.posts.append(dict(data))
        assert data.get("token") == "t-2" and data.get("pageHistory") == "0,2" and "entry.211328525" not in data, data
        if self.fail == "timeout":
            raise TimeoutError("timed out")
        if self.fail == "refused":
            return 400, "Bad request", {}
        if self.fail == "invalid":  # the form shown again with the question it did not accept (Google's 400)
            return 400, (self.page + '<div jsmodel="CP1oW" data-params="%.@.[1240136373,&quot;Student\'s UIN&quot;,null,0]">'
                         '<div><span class="RHiWt">This is a required question</span></div></div>'), {}
        if not signed or self.fail == "signed_out":
            return 401, "<title>Page Not Found</title>", {}
        return 200, '<div class="freebirdFormviewerViewResponseConfirmationMessage">Your information has been recorded and you should receive a confirmation email.</div>', renewed


def receipt_html(answers, email="instructor@tamu.edu"):
    """A Google Forms receipt as Gmail holds it (the markup of the instructor's 2026-10-22 receipts)."""
    blocks = []
    for title, value, options in answers:
        head = f'<h2 dir="auto" style="color: #202124;">{html.escape(title)}<span style="color: #d93025;" aria-label="Required question"> *</span></h2>'
        if options:
            body = "".join(f'<table><tr><td><div role="radio" aria-checked="{"true" if o == value else "false"}" aria-label="{html.escape(o)}" style="x"></div></td>'
                           f'<td>{html.escape(o)}</td></tr></table>' for o in options)
        elif title.startswith("Select the date"):
            body = f'<table dir="auto"><tr><td><div style="color: #202124;font-size: 14px; word-break: break-word; min-width: 100px; margin: 0 8px;">{html.escape(value)}</div></td></tr></table>'
        else:
            body = f'<div style="width: 100%;"><div dir="auto" style="white-space: pre-wrap;border-bottom: 1px dotted;">{html.escape(value)}</div></div>'
        blocks.append(f'<div>{head}</div><div>{body}</div>')
    return (f"<html><body><h1>Thanks for filling out this form: <a>Fall 2036 Makeup Exam Request Form</a></h1>Here's what was received."
            f'<div dir="ltr">Your email (<b>{email}</b>) was recorded when you submitted this form.</div>{"".join(blocks)}</body></html>')


def receipt_of(uin, section, session, rules=CHOSEN):
    return receipt_html([
        ("You must be the instructor of the course to schedule a make up exam. Are you the instructor of the course?", "Yes", ["Yes", "No"]),
        ("Instructor's Last Name", "Sinkule", None), ("Instructor's First Name", "Christopher", None),
        ("Math Course Number (e.g. 151)", "140", None), ("Section Number", section, None), ("Student's Last Name", "Example", None),
        ("Student's First Name", "Bob", None), ("Student's UIN", uin, None),
        ("Select the date this student will take the make-up exam.", session, None),
        ("Time Allotted", rules["time"], RULES["time"]), ("Scantron Type", rules["scantron"], RULES["scantron"]),
        ("Attach Excuse/Documentation", "Yes", RULES["documentation"]), ("Information about Calculators", rules["calculator"], RULES["calculator"]),
        ("Reset calculator memory?", rules["reset"], RULES["reset"]), ("Any Special Instructions?", "", None)])


class FakeReceipts:
    def __init__(self):
        self.messages = []

    def add(self, markup, at):
        self.messages.append({"id": f"{len(self.messages) + 1:x}a0", "internalDate": str(int(at.timestamp() * 1000)),
                              "payload": {"mimeType": "multipart/alternative", "parts": [
                                  {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(markup.encode()).decode()}}]}})

    def threads(self, query, page_token="", limit=20):
        assert query.startswith("from:forms-receipts-noreply@google.com"), query
        return [{"id": "abc1", "messages": [{"id": m["id"]} for m in self.messages]}] if self.messages else [], ""

    def thread(self, thread_id):
        return {"id": thread_id, "messages": self.messages}


class FormTests(Base):
    def test_the_form_is_read_by_its_titles_and_the_response_records_the_tamu_email(self):
        form = makeup_form.parse(form_page())
        self.assertEqual((form["page_history"], form["hidden"]["fbzx"], form["questions"]["session"]["options"]), ("0,2", "-91", SESSIONS))
        body = makeup_form.fields(form, {"instructor": "Yes", "uin": "001234567", "time": "113 min", "session": SESSIONS[2],
                                         "calculator": RULES["calculator"][0], "instructions": ""})
        self.assertEqual(body["emailAddress"], "instructor@tamu.edu")
        self.assertEqual((body["entry.1270488888"], body["entry.1270488888.other_option_response"]), ("__other_option__", "113 min"))
        self.assertEqual((body["entry.1932510432"], body["entry.1031047010"], body["pageHistory"]), (SESSIONS[2], "001234567", "0,2"))
        self.assertNotIn("entry.176801956", body)
        with self.assertRaisesRegex(ValueError, "not one of the form's choices"):
            makeup_form.fields(form, {"scantron": "Blue book"})
        # A required question Faculty does not know, or one it answers that is gone: never guessed.
        with self.assertRaisesRegex(makeup_form.FormChanged, "Favorite color"):
            makeup_form.parse(form_page(extra=[1, "Favorite color", None, 0, [[9, None, 1]]]))
        with self.assertRaisesRegex(makeup_form.FormChanged, "Calculators"):
            makeup_form.parse(form_page(drop="Calculators"))
        self.assertEqual(makeup_form.session_parts(SESSIONS[-1]),
                         {"date": "2036-12-05", "start": "16:30", "end": "18:30", "place": "BLOC 169", "note": "Reading Day"})
        self.assertEqual((department_module.time_choice(RULES["time"], 75, 1.5), department_module.time_choice(RULES["time"], 50, 1.5)),
                         (("113 min", 113), ("75 min", 75)))
        self.assertEqual(department_module.split_name("Example, Alice Marie"), ("Example", "Alice Marie"))

    def test_a_receipt_reads_back_what_was_chosen(self):
        found = makeup_form.receipt_fields(makeup_form.receipt_answers(receipt_of("123456789", "507", SESSIONS[2])))
        self.assertEqual({k: found[k] for k in ("uin", "section", "session", "time", "calculator", "email", "instructor")},
                         {"uin": "123456789", "section": "507", "session": SESSIONS[2], "time": "75 min", "email": "instructor@tamu.edu",
                          "calculator": "TI-83 or TI-84 Graphing Calculator Only", "instructor": "Yes"})
        self.assertEqual(makeup_form.receipt_answers("<p>hello</p>"), {})


class DepartmentBase(Base):
    def setUp(self):
        mail_dir = tempfile.TemporaryDirectory()
        self.addCleanup(mail_dir.cleanup)
        client_file, token_file = os.path.join(mail_dir.name, "client.json"), os.path.join(mail_dir.name, "token.json")
        with open(client_file, "w") as handle:
            json.dump({"web": {"client_id": "client-1.apps.googleusercontent.com", "client_secret": "secret", "project_id": "faculty-mail-1"}}, handle)
        with open(token_file, "w") as handle:
            json.dump({"refresh_token": "refresh-1", "email": ACCOUNT, "scopes": sorted(set(SCOPES) | set(MAIL_SCOPES)),
                       "connected_at": "2026-09-16T00:00:00+00:00"}, handle)
        with mock.patch.dict(os.environ, {"FACULTY_GMAIL_CLIENT_FILE": client_file, "FACULTY_GMAIL_TOKEN_FILE": token_file}):
            super().setUp()
        self.mail = self.app.extensions["gmail_mail"]
        self.google = FakeGmail()
        self.mail.client.http = self.google
        self.mail.google.http = FakeTokens()
        self.department = self.app.extensions["absences"].department
        self.forms = FakeForms()
        self.department.form.http = self.forms
        self.renewed = []
        self.session = dict(SESSION)  # GOOGLE SIGN-IN: Faculty's kept sign-in (None: signed out)
        self.department.form.session = lambda: self.session
        self.department.form.renew = lambda sign_in, changed: self.renewed.append((sign_in, changed))
        documents = self.app.extensions["documents"]
        self.exam = documents.create_assignment(self.definition, "test-2", "Exam 2")
        self.quiz = documents.create_assignment(self.definition, "quiz-5", "Quiz 5")
        now = "2036-10-01T00:00:00+00:00"
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("INSERT INTO rooms(id,name,created_at,updated_at) VALUES('room-1','HECC 100',?,?)", (now, now))
            db.execute("INSERT INTO seating_charts(id,assignment_id,term_id,room_id,exam_date,exam_time,created_at,updated_at) VALUES('chart-1',?,?,'room-1',?,'7:05PM',?,?)",
                       (self.exam["id"], self.term, EXAM_DAY, now, now))
            db.executemany("INSERT INTO seating_chart_sections VALUES('chart-1',?)", [(self.sections[507],), (self.sections[508],)])
            db.commit()
            self.bob = db.execute("SELECT m.enrollment_id FROM roster_memberships m JOIN student_enrollments e ON e.id=m.enrollment_id "
                                  "WHERE e.uin='123456789' AND m.section_id=?", (self.sections[507],)).fetchone()[0]
        clock = mock.patch.object(department_module, "campus_today", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)
        saved = self.client.put(f"/api/absences/department-makeups/terms/{self.term}/form", headers=HEADERS,
                                json={"request_id": "form-1", "form_url": FORM_URL, "revision": 0})
        self.assertEqual(saved.status_code, 200, saved.json)

    def absence(self, enrollment, request_id, first="2036-10-21", last="2036-10-25", status="excused", details=""):
        made = self.client.post("/api/absences", json={"section_id": self.sections[507], "enrollment_id": enrollment, "first_date": first,
                                                       "last_date": last, "reason": "illness", "request_id": request_id,
                                                       **({"details": details} if details else {})}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        if status == "pending":
            return made.json
        decided = self.revise(made.json, request_id + "-d", status=status)
        self.assertEqual(decided.status_code, 200, decided.json)
        return decided.json

    def another_exam(self, day="2036-11-02"):
        """Exam 3 of the same term, seated for section 507 on `day`."""
        exam = self.app.extensions["documents"].create_assignment(self.definition, "test-3", "Exam 3")
        now = "2036-10-01T00:00:00+00:00"
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("INSERT INTO seating_charts(id,assignment_id,term_id,room_id,exam_date,exam_time,created_at,updated_at) "
                       "VALUES('chart-3',?,?,'room-1',?,'7:05PM',?,?)", (exam["id"], self.term, day, now, now))
            db.execute("INSERT INTO seating_chart_sections VALUES('chart-3',?)", (self.sections[507],))
            db.commit()
        return exam

    def rows(self):
        answer = self.client.get("/api/absences/department-makeups")
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json["makeups"]

    def preview(self, absence, **fields):
        return self.client.post(f"/api/absences/{absence['id']}/department-makeup/preview", json={"assignment_id": self.exam["id"], **fields},
                                headers=HEADERS)

    def submit(self, absence, request_id="s1", fingerprint=None, confirmation="submit", **fields):
        if fingerprint is None:
            fingerprint = self.preview(absence, **fields).json["fingerprint"]
        return self.client.post(f"/api/absences/{absence['id']}/department-makeup", headers=HEADERS, json={
            "assignment_id": self.exam["id"], "request_id": request_id, "fingerprint": fingerprint, "confirmation": confirmation, **fields})


class RequestTests(DepartmentBase):
    def test_only_an_excused_absence_over_an_exam_day_owes_a_request(self):
        self.absence(self.alice, "a1")
        self.absence(self.bob, "b1", status="pending")
        self.absence(self.bob, "b2", first="2036-10-26", last="2036-10-26")  # misses the exam's day
        [row] = self.rows()
        self.assertEqual((row["enrollment_id"], row["title"], row["given_date"], row["state"], row["name"]),
                         (self.alice, "Exam 2", EXAM_DAY, "owed", "Example, Alice"))
        self.assertNotIn("uin", row)
        with connect(self.app.config["DATABASE"]) as db:  # a quiz (no seating chart) is never one
            self.assertEqual(set(self.department.exams_of(db, self.term)), {self.exam["id"]})

    def test_the_preview_fills_the_form_from_the_records_and_asks_only_for_the_exam_rules(self):
        absence = self.absence(self.alice, "a1")
        shown = self.preview(absence)
        self.assertEqual(shown.status_code, 200, shown.json)
        answers = shown.json["answers"]
        self.assertEqual({k: answers[k] for k in ("instructor", "last", "first", "course", "section", "student_last", "student_first", "uin", "session")},
                         {"instructor": "Yes", "last": "Sinkule", "first": "Christopher", "course": "140", "section": "507",
                          "student_last": "Example", "student_first": "Alice", "uin": "001234567", "session": SESSIONS[2]})
        self.assertEqual(answers["documentation"], "No")  # nothing on file
        self.assertEqual((shown.json["ready"], shown.json["missing"], shown.json["email"]),
                         (False, ["Time allotted", "Scantron", "Calculators", "Reset calculator memory"], "instructor@tamu.edu"))
        self.assertEqual(shown.json["choices"]["session"], SESSIONS[1:])  # only sessions after today
        refused = self.submit(absence)
        self.assertEqual((refused.status_code, refused.json["error"]), (400, "Choose Time allotted, Scantron, Calculators, Reset calculator memory on the card first."))
        self.assertEqual(self.forms.posts, [])
        with mock.patch.object(department_module.DepartmentMakeups, "_factor", return_value=(1.5, "1.5x extra time")):
            longer = self.preview(absence, **CHOSEN)
        self.assertEqual((longer.json["answers"]["time"], longer.json["base_time"], longer.json["ready"]), ("113 min", "75 min", True))
        self.assertEqual(self.preview(absence, session="Someday").json["error"], "Choose one of the form's makeup sessions.")
        self.assertEqual(self.preview(absence, session=SESSIONS[0]).json["error"], "That makeup session has already passed.")

    def test_no_session_before_the_day_they_can_return(self):
        # NOT BEFORE THEIR RETURN (2026-10-22, Lucia Rosales Lozano: booked Fri Oct 23 though her note cleared her for Oct 24).
        absence = self.absence(self.alice, "a1", details="Her note documents illness October 21–25 and return October 28.")
        shown = self.preview(absence, **CHOSEN).json
        self.assertEqual(shown["answers"]["session"], SESSIONS[3])  # Fri Oct 31, not Mon Oct 27
        self.assertIn({"key": "not_before", "label": "Not before", "value": "Tue, Oct 28 (when they can return)"}, shown["shown"])
        self.assertEqual(self.preview(absence, not_before="2036-10-24", **CHOSEN).json["answers"]["session"], SESSIONS[2])
        early = self.preview(absence, session=SESSIONS[2], **CHOSEN).json
        self.assertIn("before Tue, Oct 28", early["warnings"][0])

    def test_a_request_is_sent_once_and_makes_no_sitting(self):
        exams = self.app.extensions["exams"]
        event = exams.create({"term_id": self.term, "assignment_id": self.exam["id"], "section_ids": [self.sections[507]]})
        exams.save_sitting(event["id"], {"version": event["version"], "label": "Exam 2", "kind": "main", "date": EXAM_DAY})
        absence = self.absence(self.alice, "a1")
        self.assertEqual(self.submit(absence, confirmation="send", **CHOSEN).status_code, 400)
        self.assertEqual(self.submit(absence, fingerprint="stale", **CHOSEN).status_code, 409)
        sent = self.submit(absence, **CHOSEN)
        self.assertEqual(sent.status_code, 200, sent.json)
        self.assertEqual((sent.json["outcome"], sent.json["session"], sent.json["email"]), ("accepted", SESSIONS[2], "instructor@tamu.edu"))
        [post], [step] = self.forms.posts, self.forms.steps
        self.assertEqual((step["emailAddress"], post["entry.1031047010"], post["entry.2147182980"], post["entry.1270488888"]),
                         ("instructor@tamu.edu", "001234567", "Example", "75 min"))
        self.assertEqual(self.submit(absence, **CHOSEN).json["id"], sent.json["id"])  # the request ID replays its answer
        again = self.submit(absence, request_id="s2", **CHOSEN)
        self.assertEqual(again.status_code, 409, again.json)
        self.assertEqual(len(self.forms.posts), 1)
        self.assertEqual(self.rows()[0]["state"], "requested")
        with connect(self.app.config["DATABASE"]) as db:
            stored = db.execute("SELECT answers_json, base_minutes FROM absence_department_requests").fetchone()
        self.assertNotIn("Alice", stored[0])  # no name in the catalog
        self.assertEqual(stored[1], 75)
        # NO SITTING (2026-10-22): the department runs it, so nothing is put on Exams or the calendar.
        self.assertEqual([s for s in exams.get(event["id"])["sittings"] if s["kind"] == "makeup"], [])
        # The next student's card starts from this exam's rules.
        bob = self.absence(self.bob, "b1")
        self.assertEqual({k: self.preview(bob).json["answers"][k] for k in CHOSEN}, CHOSEN)

    def test_the_rules_last_sent_in_the_term_are_the_next_exam_s_and_documentation_is_the_student_s_own(self):
        # THE TERM'S RULES (2026-11-12): "the only thing that should change is the student info, exam number, and date".
        alice = self.absence(self.alice, "a1")
        self.assertEqual(self.submit(alice, documentation="Yes", **CHOSEN).status_code, 200)
        exam3 = self.another_exam()
        later = self.absence(self.bob, "b3", first="2036-11-02", last="2036-11-02")
        shown = self.preview(later, assignment_id=exam3["id"]).json
        self.assertEqual(({k: shown["answers"][k] for k in CHOSEN}, shown["ready"], shown["base_time"], shown["answers"]["session"]),
                         (CHOSEN, True, "75 min", SESSIONS[4]))
        self.assertEqual(shown["answers"]["documentation"], "No")  # Bob's own (nothing on file), whatever Alice's request said
        # An exam's own last request comes before the term's.
        changed = {**CHOSEN, "calculator": "Any Calculator is fine"}
        self.assertEqual(self.submit(later, request_id="s3", assignment_id=exam3["id"], **changed).status_code, 200)
        self.assertEqual(self.preview(self.absence(self.bob, "b1")).json["answers"]["calculator"], CHOSEN["calculator"])
        again = self.absence(self.alice, "a3", first="2036-11-02", last="2036-11-02")
        self.assertEqual(self.preview(again, assignment_id=exam3["id"]).json["answers"]["calculator"], "Any Calculator is fine")

    def test_a_receipt_of_the_instructors_own_gives_a_later_exam_of_the_term_its_rules(self):
        gmail = FakeReceipts()
        self.department.gmail = mock.Mock(ready=lambda: True, client=gmail, google=mock.Mock(connected=False))
        own = {**CHOSEN, "time": "60 min"}
        gmail.add(receipt_of("001234567", "507", SESSIONS[1], own), dt.datetime(2036, 10, 21, 13, 35, tzinfo=dt.timezone.utc))
        gmail.add(receipt_of("999999999", "507", SESSIONS[1]), dt.datetime(2036, 10, 22, 13, 35, tzinfo=dt.timezone.utc))  # no student of this term
        self.department._receipts_at = 0.0
        with mock.patch.object(department_module.time, "monotonic", return_value=time.monotonic() + 10 ** 6):
            self.assertTrue(self.department.process_one())
        exam3 = self.another_exam()
        later = self.absence(self.bob, "b3", first="2036-11-02", last="2036-11-02")
        shown = self.preview(later, assignment_id=exam3["id"]).json
        self.assertEqual(({k: shown["answers"][k] for k in CHOSEN}, shown["ready"]), (own, True))

    def test_a_refused_request_may_be_tried_again_but_an_unknown_one_never(self):
        absence = self.absence(self.alice, "a1")
        self.forms.fail = "refused"
        failed = self.submit(absence, **CHOSEN)
        self.assertEqual((failed.status_code, failed.json["outcome"]), (502, "failed"))
        self.assertEqual(self.rows()[0]["state"], "failed")
        self.forms.fail = "timeout"
        lost = self.submit(absence, request_id="s2", **CHOSEN)
        self.assertEqual((lost.status_code, lost.json["outcome"]), (504, "uncertain"))
        self.forms.fail = None
        self.assertEqual(self.submit(absence, request_id="s3", **CHOSEN).status_code, 409)
        self.assertEqual(len(self.forms.posts), 2)

    def test_an_undecided_absence_is_shown_but_never_sent(self):
        absence = self.absence(self.alice, "a1", status="pending")
        shown = self.preview(absence, **CHOSEN)
        self.assertEqual(shown.status_code, 200, shown.json)
        self.assertIn("waiting for your decision", shown.json["warnings"][0])
        self.assertEqual(self.submit(absence, **CHOSEN).status_code, 409)
        self.assertEqual(self.forms.posts, [])

    def test_receipts_confirm_requests_and_stand_for_the_instructors_own(self):
        gmail = FakeReceipts()
        self.department.gmail = mock.Mock(ready=lambda: True, client=gmail, google=mock.Mock(connected=False))
        alice = self.absence(self.alice, "a1")
        self.absence(self.bob, "b1")
        self.forms.fail = "refused"  # a refused request for the same session is never the one a receipt confirms
        self.assertEqual(self.submit(alice, request_id="s0", **CHOSEN).status_code, 502)
        self.forms.fail = None
        self.assertEqual(self.submit(alice, **CHOSEN).status_code, 200)
        gmail.add(receipt_of("001234567", "507", SESSIONS[2]), dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5))
        own = {**CHOSEN, "time": "60 min"}
        gmail.add(receipt_of("123456789", "507", SESSIONS[3], own), dt.datetime(2036, 10, 21, 13, 35, tzinfo=dt.timezone.utc))
        self.department._receipts_at = 0.0
        with mock.patch.object(department_module.time, "monotonic", return_value=time.monotonic() + 10 ** 6):
            self.assertTrue(self.department.process_one())
        rows = {r["enrollment_id"]: r for r in self.rows()}
        self.assertEqual((rows[self.alice]["state"], rows[self.alice]["request"]["confirmed"]), ("requested", True))
        self.assertEqual((rows[self.bob]["state"], rows[self.bob]["receipt"]["session"]), ("on_form", SESSIONS[3]))
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM absence_department_receipts").fetchone()[0], 2)
        with mock.patch.object(department_module.time, "monotonic", return_value=time.monotonic() + 10 ** 7):
            self.assertFalse(self.department.process_one())  # each receipt is kept once

    def test_the_assistant_proposes_it_and_only_the_instructor_sends_it(self):
        rule = policy.base_rule("absences.department_makeup")
        self.assertEqual((rule.tier, rule.confirmation, rule.preview, rule.request_id), ("approve", "submit", "absences.department_makeup_preview", True))
        self.assertEqual(policy.base_rule("absences.department_makeup_preview").tier, "read")
        self.assertIn("absences.department_form", policy.REQUEST_IDS)


class GoogleSignInTests(DepartmentBase):
    def test_nothing_is_recorded_or_sent_without_the_google_sign_in(self):
        absence = self.absence(self.alice, "a1")
        self.session = None
        shown = self.preview(absence, **CHOSEN)
        self.assertEqual((shown.json["ready"], shown.json["missing"]), (False, ["Faculty's Google sign-in"]))
        self.assertIn("Sign in to Google", shown.json["warnings"][0])
        refused = self.submit(absence, **CHOSEN)
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Google sign-in for the makeup form has ended", refused.json["error"])
        self.assertEqual(self.forms.posts, [])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM absence_department_requests").fetchone()[0], 0)

    def test_the_kept_sign_in_goes_with_every_call_and_google_refusing_it_asks_for_a_new_one(self):
        absence = self.absence(self.alice, "a1")
        self.assertEqual(self.submit(absence, **CHOSEN).status_code, 200)
        self.assertTrue(all(c.get("SID") == "kept-sid" for c in self.forms.cookies))
        self.assertIn(("s-1", {"__Secure-1PSIDTS": "new"}), self.renewed)  # Google's renewed cookies are kept
        bob = self.absence(self.bob, "b1")
        self.forms.fail = "signed_out"
        failed = self.submit(bob, request_id="s2", **CHOSEN)
        self.assertEqual((failed.status_code, failed.json["outcome"]), (502, "failed"))
        self.assertIn("Sign in to Google", failed.json["error"])
        self.forms.fail = "invalid"
        said = self.submit(bob, request_id="s3", **CHOSEN)
        self.assertEqual((said.status_code, said.json["error"]), (502, "Google Forms did not accept the response: Student's UIN: "
                                                                        "This is a required question. Nothing was recorded."))

    def test_a_sign_in_google_no_longer_honours_reads_as_ended(self):
        # SIGN-IN ENDED (2026-10-23): kept a day earlier, the sign-in no longer opened the form signed in, yet the page said signed in.
        from absences import google_signin
        path = os.path.join(self.temp.name, "google.json")
        with mock.patch.dict(os.environ, {"FACULTY_GOOGLE_FORMS_SESSION_FILE": path}):
            kept = google_signin.save({"SID": "gone"}, "instructor@tamu.edu")
            self.department.form.session = google_signin.load
            self.assertTrue(self.client.get("/api/absences/department-makeups/google").json["signed_in"])
            with mock.patch.object(makeup_form, "http_text", self.forms):
                checked = self.client.get("/api/absences/department-makeups/google?check=1").json
            self.assertEqual((checked["signed_in"], checked["ended"]), (False, True))
            google_signin.observed(kept["sign_in"], "instructor@tamu.edu")
            self.assertEqual(self.client.get("/api/absences/department-makeups/google").json["signed_in"], True)

    def test_an_http_sign_in_refusal_marks_the_kept_session_ended(self):
        # ABSENCES GOOGLE SIGN-IN: redirects and authentication failures are also ended sessions.
        from absences import google_signin
        path = os.path.join(self.temp.name, "google-http-refused.json")
        with mock.patch.dict(os.environ, {"FACULTY_GOOGLE_FORMS_SESSION_FILE": path}):
            for status in (302, 401, 403):
                google_signin.save({"SID": "gone"}, "instructor@tamu.edu")
                with mock.patch.object(makeup_form, "http_text", return_value=(status, "", {})):
                    checked = self.client.get("/api/absences/department-makeups/google?check=1").json
                self.assertEqual((checked["signed_in"], checked["ended"]), (False, True))

    def test_the_sign_in_window_keeps_google_s_session_privately_and_the_assistant_never_drives_it(self):
        from absences import google_signin
        path = os.path.join(self.temp.name, "google.json")
        with mock.patch.dict(os.environ, {"FACULTY_GOOGLE_FORMS_SESSION_FILE": path}):
            self.assertEqual(self.client.get("/api/absences/department-makeups/google").json["signed_in"], False)
            google_signin.save({"SID": "a"}, "instructor@tamu.edu")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            google_signin.renew("other", {"SID": "b"})  # a sign-in since replaced never writes over the new one
            kept = google_signin.load()
            self.assertEqual(kept["cookies"], {"SID": "a"})
            google_signin.renew(kept["sign_in"], {"SID": "b"})
            self.assertEqual(google_signin.load()["cookies"], {"SID": "b"})
            listed = self.client.get("/api/absences/department-makeups/google").json
            self.assertEqual((listed["signed_in"], listed["email"]), (True, "instructor@tamu.edu"))
            self.assertNotIn("cookies", listed)
            forgot = self.client.post("/api/absences/department-makeups/google/forget", headers=HEADERS, json={})
            self.assertEqual((forgot.status_code, forgot.json["signed_in"], os.path.exists(path)), (200, False, False))
        with mock.patch.dict(os.environ, {"FACULTY_GOOGLE_FORMS_SESSION_FILE": "", "FACULTY_CANVAS_SESSION_FILE": "/x/canvas-session.json"}):
            self.assertEqual(google_signin.session_file(), "/x/google-forms-session.json")
        browser = google_signin.GoogleBrowser.__new__(google_signin.GoogleBrowser)
        self.assertEqual(browser._classify("https://accounts.google.com/v3/signin/identifier", "Sign in", [{"type": "email", "name": "identifier"}]), "google_email")
        self.assertEqual(browser._classify(FORM_URL, "Fall 2036 Makeup Exam Request Form", []), "google")
        self.assertEqual(browser._classify("https://login.microsoftonline.com/x", "Enter password", [{"type": "password"}]), "password")
        self.assertIn("Email=instructor%40tamu.edu", google_signin.start_url(FORM_URL))
        for name in ("start", "credentials", "answer", "input", "show", "close"):
            self.assertEqual(policy.base_rule(f"department_signin.signin_{name}").tier, "never")
        self.assertEqual(policy.base_rule("department_signin.forget_session").tier, "never")


class NoticeTests(DepartmentBase):
    def test_the_student_is_told_once_from_your_gmail(self):
        absence = self.absence(self.alice, "a1")
        sent = self.submit(absence, **CHOSEN)
        self.assertEqual(sent.status_code, 200, sent.json)
        self.assertEqual(sent.json["notice"], {"status": "accepted", "detail": ""})
        [mail] = [self.google.parsed(i) for i in range(len(self.google.sent))]
        self.assertEqual((mail["To"], mail["Cc"], mail["Subject"]), ("alice@example.test", None,
                         "MATH 140 Exam 2 makeup: Mon, Oct 27, 7:30 to 9:30 PM, BLOC 169"))
        self.assertIn(ACCOUNT, mail["From"])
        self.assertEqual([line for line in mail.get_content().splitlines() if line], [
            "Hello,", "You are scheduled to take your MATH 140 Exam 2 makeup on Monday, October 27, from 7:30 to 9:30 PM in BLOC 169.",
            "You must take the makeup at this time. If you do not, you will receive a 0 on Exam 2.",
            "Bring your student ID: the makeup testing room admits no one without it.", "Professor Sinkule"])
        self.assertEqual(self.submit(absence, **CHOSEN).json["notice"]["status"], "accepted")  # a replay tells no one again
        self.department._notices_at = 0.0
        self.assertFalse(self.department._notify_due())
        self.assertEqual(len(self.google.sent), 1)
        self.assertEqual(self.rows()[0]["request"]["notice"]["status"], "accepted")

    def test_a_notice_that_could_not_go_at_once_goes_from_the_worker_and_a_lost_one_is_never_resent(self):
        absence = self.absence(self.alice, "a1")
        with mock.patch.object(department_module.DepartmentMakeups, "_notify", side_effect=RuntimeError("Gmail away")):
            self.assertEqual(self.submit(absence, **CHOSEN).json["notice"], None)
        self.assertEqual(self.google.sent, [])
        self.department._notices_at = 0.0
        self.assertTrue(self.department.process_one())
        self.assertEqual(len(self.google.sent), 1)
        bob = self.absence(self.bob, "b1")
        self.google.lose_answer = True
        self.assertEqual(self.submit(bob, request_id="s2", **CHOSEN).json["notice"]["status"], "uncertain")
        self.department._notices_at = 0.0
        self.assertFalse(self.department._notify_due())
        self.assertEqual(len(self.google.sent), 2)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM absence_department_notices").fetchone()[0], 2)


class CardTests(DepartmentBase):
    """The Follow-up card: proposed by the preparation, its session and rules dropdowns, sent by Approve."""

    def setUp(self):
        super().setUp()
        self.assistant = self.app.extensions["assistant"]
        self.suggestions = self.assistant.followups
        self.suggestions.interval = 0
        self.suggestions.automatic = False
        model = mock.patch("assistant.conversations.config.assistant", return_value={"kind": "hosted", "name": "Scripted", "reasoning_choice": "high"})
        model.start()
        self.addCleanup(model.stop)

    def run_session(self):
        from communications.record import record
        with self.app.extensions["rosters"].lock(), connect(self.assistant.database) as db:
            record(db, "gmail", "m1", {"sent_at": "2036-10-21T15:00:00Z", "author_role": "student", "author": {"name": "Alice"}, "recipients": [],
                                      "subject": "Exam 2", "body_text": "I have the flu and will miss Exam 2 tomorrow.", "attachments": [],
                                      "context": {"thread_id": "t1"}}, [(self.alice, self.term, "author", "roster_address")], "t1")
            db.commit()
        self.suggestions.process_one()
        run = self.client.get("/api/student-support?kind=followup&suggestions=1").json["items"][0]["suggestions"]
        with connect(self.assistant.database) as db:
            self.suggestions.context.remember(db, self.suggestions.get(db, run["id"]))
        session = self.assistant.open_session("test")
        session.update(_proposal_only=True, _followup_run=run["id"])
        return session, run

    def cards(self):
        return self.client.get("/api/student-support?kind=followup&suggestions=1").json["items"][0]["suggestions"]["actions"]

    def test_an_absence_excused_on_its_card_brings_the_request_card_into_the_same_follow_up(self):
        # AFTER THE EXCUSE (2026-10-22, Walter Parr's follow-up): the round recorded the absence, so no request card could name it.
        session, run = self.run_session()
        with connect(self.assistant.database) as db:
            notice = db.execute("SELECT id FROM comm_messages WHERE external_id='m1'").fetchone()[0]
        made, _ = self.assistant.run_tool(session, "faculty_write", {"method": "POST", "path": "/api/absences", "body": {
            "section_id": self.sections[507], "enrollment_id": self.alice, "first_date": "2036-10-21", "last_date": "2036-10-24",
            "reason": "illness", "status": "pending", "notice_message_id": notice, "request_id": "$new_request_id"},
            "summary": "Record Alice's absence"})
        card = next(a for a in self.cards() if a["id"] == json.loads(made)["action_id"])
        chosen = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", headers=HEADERS, json={
            "action_id": card["id"], "seen": card["seen"], "request_id": uuid.uuid4().hex, "status": "excused"})
        self.assertEqual(chosen.status_code, 200, chosen.json)
        card = next(a for a in self.cards() if a["id"] == chosen.json["action_id"])
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        [request] = [a for a in self.cards() if a["endpoint"] == "absences.department_makeup"]
        self.assertEqual((request["pending"], request["body"]["assignment_id"], request["effect"]["title"]),
                         (True, self.exam["id"], "Request a department makeup of Exam 2"))
        self.assertEqual(self.forms.posts, [])  # waits for Send request
        again = next(a for a in self.cards() if a["endpoint"] == "absences.create")
        self.suggestions._department_after(run["id"], again["id"])  # never a second card for the same exam
        self.assertEqual(len([a for a in self.cards() if a["endpoint"] == "absences.department_makeup"]), 1)
        # TRY AGAIN: a request that failed (here: no exam rules chosen yet; Google's refusal likewise) is followed by a fresh card.
        failed = self.client.post(f"/api/assistant/actions/{request['id']}/approve", json={"seen": request["seen"]}, headers=HEADERS)
        self.assertEqual(failed.status_code, 200, failed.json)
        cards = [a for a in self.cards() if a["endpoint"] == "absences.department_makeup"]
        self.assertEqual(sorted(a["status"] for a in cards), ["failed", "proposed"])
        # The refused request its fresh card replaced holds back no later step (a reply's Send said "include the preceding work").
        with connect(self.assistant.database) as db:
            refused = self.assistant._view(*self.assistant.action(next(a["id"] for a in cards if a["status"] == "failed")))
            self.assertFalse(self.suggestions.in_plan(db, refused))
        self.assertEqual(self.forms.posts, [])

    def test_the_card_names_the_response_lets_the_instructor_choose_and_sends_on_approve(self):
        absence = self.absence(self.alice, "a1")
        session, run = self.run_session()
        with connect(self.assistant.database) as db:
            facts = "\n".join(self.suggestions._department_facts(db, run | {"enrollment_id": self.alice}, {"id": self.term}, [self.sections[507]]))
        self.assertIn(f"Propose POST /api/absences/{absence['id']}/department-makeup", facts)
        made, _ = self.assistant.run_tool(session, "faculty_write", {"method": "POST", "path": f"/api/absences/{absence['id']}/department-makeup",
                                                                    "body": {"assignment_id": self.exam["id"], "request_id": "$new_request_id"},
                                                                    "summary": "Request Alice's department makeup of Exam 2"})
        made = json.loads(made)
        self.assertEqual(made["outcome"], "waiting_for_approval", made)
        [card] = self.cards()
        effect = card["effect"]
        self.assertEqual((effect["title"], effect["button"], effect["sends"]), ("Request a department makeup of Exam 2", "Send request", True))
        self.assertIn("instructor@tamu.edu", effect["audience"])
        lines = {c["label"]: c for c in effect["changes"]}
        self.assertEqual(lines["Makeup session"]["choice"]["value"], SESSIONS[2])
        self.assertEqual(lines["Time allotted"]["choice"]["options"][0], {"value": "", "label": "Choose…"})
        self.assertEqual(self.forms.posts, [])
        # Each choice is a new waiting card in the old one's place; nothing is sent by choosing.
        for field, value in (("session", SESSIONS[3]), *CHOSEN.items()):
            card = self.cards()[0]
            chosen = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", headers=HEADERS, json={
                "action_id": card["id"], "seen": card["seen"], "request_id": uuid.uuid4().hex, "choice": {"field": field, "value": value}})
            self.assertEqual(chosen.status_code, 200, chosen.json)
        refused = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", headers=HEADERS, json={
            "action_id": self.cards()[0]["id"], "seen": self.cards()[0]["seen"], "request_id": "x", "choice": {"field": "uin", "value": "1"}})
        self.assertEqual(refused.status_code, 400)
        [card] = self.cards()
        self.assertEqual((card["body"]["session"], card["body"]["time"]), (SESSIONS[3], "75 min"))
        self.assertEqual(self.forms.posts, [])
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        [post], [step] = self.forms.posts, self.forms.steps
        self.assertEqual((post["entry.1932510432"], step["emailAddress"]), (SESSIONS[3], "instructor@tamu.edu"))
        self.assertEqual(self.rows()[0]["state"], "requested")

    # ONCE THE EXAM IS SEATED (2026-11-03, Jon Bellamy's absence over an Exam 3 with no seating chart yet).
    def seated(self, enrollment=None, day="2036-11-02"):
        """An excused absence over the day of an exam not seated yet, then its seating chart."""
        absence = self.absence(enrollment or self.alice, uuid.uuid4().hex, first=day, last=day)
        self.turn(False)
        self.assertEqual(self.followups(), [])
        return absence, self.another_exam(day)

    def turn(self, expected):
        self.department._offers_at = 0.0
        with mock.patch.object(department_module, "OFFER_SETTLE", 0):
            self.assertEqual(self.department.offer_one(), expected)

    def followups(self):
        return self.client.get("/api/student-support?kind=followup&suggestions=1").json["items"]

    def card_cards(self):
        return [a for item in self.followups() for a in (item.get("suggestions") or {}).get("actions", [])
                if a["endpoint"] == "absences.department_makeup"]

    def test_an_exam_seated_later_brings_one_request_card_and_nothing_is_sent(self):
        absence, exam = self.seated()
        self.turn(True)
        [item] = self.followups()
        self.assertEqual((item["title"], item["state"]), ("Request the department makeup of Exam 3", "todo"))
        [card] = self.card_cards()
        self.assertTrue(card["pending"])
        self.assertEqual((card["path"], card["body"]["assignment_id"]), (f"/api/absences/{absence['id']}/department-makeup", exam["id"]))
        self.assertEqual(card["effect"]["button"], "Send request")
        self.assertEqual(self.forms.posts, [])  # waits for Send request
        self.turn(False)  # one card, not another a minute later
        self.assertEqual(len(self.card_cards()), 1)
        with connect(self.assistant.database) as db:  # a preparation for this student is told, so it proposes no second one
            run = {"enrollment_id": self.alice, "id": "x"}
            facts = "\n".join(self.suggestions._department_facts(db, run, {"id": self.term}, [self.sections[507]]))
        self.assertIn("its request card already waits on Follow-up. Propose no request.", facts)
        self.assertNotIn("Propose POST", facts)

    def test_the_absence_s_own_approval_goes_first(self):
        self.seated()
        self.department._offers_at = 0.0
        self.assertFalse(self.department.offer_one())  # decided just now: AFTER THE EXCUSE adds its card to that follow-up
        self.assertEqual(self.followups(), [])

    def test_sending_it_resolves_the_follow_up(self):
        self.seated()
        self.turn(True)
        run = self.followups()[0]["suggestions"]["id"]
        for field, value in CHOSEN.items():
            card = next(a for a in self.card_cards() if a["pending"])
            chosen = self.client.post(f"/api/student-support/followups/suggestions/{run}/edit", headers=HEADERS, json={
                "action_id": card["id"], "seen": card["seen"], "request_id": uuid.uuid4().hex, "choice": {"field": field, "value": value}})
            self.assertEqual(chosen.status_code, 200, chosen.json)
        card = next(a for a in self.card_cards() if a["pending"])
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(len(self.forms.posts), 1)
        self.turn(True)
        self.assertEqual(self.followups()[0]["state"], "resolved")
        self.assertIn("Google accepted", self.followups()[0]["details"])
        self.turn(False)

    def test_a_request_sent_fills_in_the_cards_still_waiting(self):
        # THE TERM'S RULES (2026-11-12: five cards waited after Exam 3 was seated; the first was filled out and sent, the rest
        # still said "Choose…").
        self.seated()
        self.absence(self.bob, "b3", first="2036-11-02", last="2036-11-02")
        self.turn(True)
        self.turn(True)
        first, other = self.card_cards()
        for card in (first, other):
            self.assertEqual({c["label"]: c for c in card["effect"]["changes"]}["Time allotted"]["choice"]["value"], "")
        run = next(i["suggestions"]["id"] for i in self.followups() if any(a["id"] == first["id"] for a in i["suggestions"]["actions"]))
        for field, value in CHOSEN.items():
            chosen = self.client.post(f"/api/student-support/followups/suggestions/{run}/edit", headers=HEADERS, json={
                "action_id": first["id"], "seen": first["seen"], "request_id": uuid.uuid4().hex, "choice": {"field": field, "value": value}})
            self.assertEqual(chosen.status_code, 200, chosen.json)
            first = next(a for a in self.card_cards() if a["id"] == chosen.json["action_id"])
        approved = self.client.post(f"/api/assistant/actions/{first['id']}/approve", json={"seen": first["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(len(self.forms.posts), 1)
        other = next(a for a in self.card_cards() if a["id"] == other["id"])
        lines = {c["label"]: c for c in other["effect"]["changes"]}
        self.assertEqual((other["status"], other["pending"]), ("previewed", True))
        self.assertEqual({k: lines[makeup_form.LABELS[k]]["choice"]["value"] for k in CHOSEN}, CHOSEN)
        self.assertFalse([n for n in other["effect"]["notes"] if n.startswith("Choose")])
        sent = self.client.post(f"/api/assistant/actions/{other['id']}/approve", json={"seen": other["seen"]}, headers=HEADERS)  # one click
        self.assertEqual((sent.status_code, len(self.forms.posts)), (200, 2), sent.json)

    def test_a_declined_card_is_not_offered_again_and_an_expired_one_is(self):
        self.seated()
        self.turn(True)
        [card] = self.card_cards()
        self.assistant._event(card["id"], "expired", "faculty", {"after_hours": 24})  # never the instructor's decision
        self.turn(True)
        self.assertEqual(sorted(a["status"] for a in self.card_cards()), ["expired", "proposed"])
        self.assertEqual(len(self.followups()), 1)
        fresh = next(a for a in self.card_cards() if a["pending"])
        self.assertEqual(self.client.post(f"/api/assistant/actions/{fresh['id']}/decline", headers=HEADERS, json={}).status_code, 200)
        self.turn(False)
        self.assertEqual(sorted(a["status"] for a in self.card_cards()), ["declined", "expired"])

    def test_nothing_owed_any_more_resolves_the_follow_up_and_withdraws_its_card(self):
        absence, _ = self.seated()
        self.turn(True)
        self.assertEqual(self.revise(absence, "not-excused", status="not_excused").status_code, 200)
        self.turn(True)
        [item] = self.followups()
        self.assertEqual(item["state"], "resolved")
        self.assertIn("No department makeup is owed any more", item["details"])
        self.suggestions.process_one()  # the follow-up changed: its waiting card is withdrawn
        self.assertFalse(any(a["pending"] for a in self.card_cards()))

    def test_a_form_that_cannot_be_read_opens_nothing_until_it_can(self):
        self.seated()
        with mock.patch.object(self.department, "preview", side_effect=ValueError("The form is closed.")), self.assertLogs("absences.department_makeups", "ERROR"):
            self.turn(False)
        self.assertEqual(self.followups(), [])
        self.turn(True)
        self.assertEqual(len(self.card_cards()), 1)

    def test_a_card_in_a_student_s_own_follow_up_stands(self):
        absence, exam = self.seated()
        session, _ = self.run_session()
        made, _ = self.assistant.run_tool(session, "faculty_write", {"method": "POST", "path": f"/api/absences/{absence['id']}/department-makeup",
                                                                    "body": {"assignment_id": exam["id"], "request_id": "$new_request_id"},
                                                                    "summary": "Request Alice's department makeup of Exam 3"})
        self.assertEqual(json.loads(made)["outcome"], "waiting_for_approval", made)
        self.turn(False)
        self.assertEqual(len(self.card_cards()), 1)
