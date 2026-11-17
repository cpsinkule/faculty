"""The communications record (AGENTS.md "Communications record") offline: Gmail, Canvas and Gradescope captured from loopback fakes
into the record, kept originals in verified storage (published, backed up, read back and checked), links to students only by roster
address, SIS ID or SID, Faculty's own sends shown by reference, and one timeline per student. Nothing here reaches the network, and
every fake records any request that would change something."""
import base64
import email
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.parse
import uuid
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from unittest import mock

from browser_course_pages import seed_course
from fake_canvas import FakeCanvas
from fake_gradescope import EMAIL as GS_EMAIL, PASSWORD as GS_PASSWORD, FakeGradescope
from test_gmail_mail import ACCOUNT, FakeTokens
from test_rosters import csv_bytes
from test_storage import HEADERS, temporary_storage

from app import create_app
from canvas import web as canvas_web
from communications import drive_links, gmail_capture, gradescope_capture, service as comm_service
from course_storage import StorageError
from grading import gradescope_web as gw
from response_cards.gmail import DRIVE_SCOPES, MAIL_SCOPES, SCOPES
from rosters import connect

ALICE, BOB, CAROL, DANA, DAN = "001234567", "123456789", "222333444", "333444555", "444555666"
ROWS = {507: [["", "Example, Alice", ALICE, "Registered", "MATH", "U1", "", "Y", "", "", "", "alice@email.tamu.edu"],
              ["", "Example, Bob", BOB, "Registered", "MATH", "U2", "", "N", "", "", "", "bob@email.tamu.edu"],
              ["", "Twin, Dana", DANA, "Registered", "MATH", "U2", "", "N", "", "", "", "family@email.tamu.edu"]],
        508: [["", "Example, Carol", CAROL, "Registered", "MATH", "U1", "", "Y", "", "", "", "carol@email.tamu.edu"],
              ["", "Twin, Dan", DAN, "Registered", "MATH", "U1", "", "Y", "", "", "", "family@email.tamu.edu"]]}
SEPT = 1790000000000  # 2026-09-21, inside Fall 2026's window (which opens July 1)


def millis(day):
    return int(datetime(2026, 9, day, 15, tzinfo=timezone.utc).timestamp() * 1000)


def mail(sender, to, subject, text, cc="", bcc="", files=()):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, to, subject
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc
    message["Message-ID"] = f"<{uuid.uuid4().hex}@mail.example>"
    message.set_content(text)
    for name, content, kind in files:
        main, sub = kind.split("/")
        message.add_attachment(content, maintype=main, subtype=sub, filename=name)
    return message.as_bytes()


def notice(name, where, conversation, text, subject="Question", message=""):
    """A Canvas message notification as Canvas lays it out (seen on real ones, 2026-09-30): the sender's name at Canvas's address,
    the course and section in the subject, a Subject line, the message, the reply line with the conversation's link, a footer."""
    link = f"https://canvas.tamu.edu/conversations/{conversation}"
    files = f"\nnote.pdf\nhttps://canvas.tamu.edu/files/9/download?verifier=v&location=conversation_message_{message}\n" if message else ""
    body = (f"Subject: {subject}\n\n{text}\n\nYou can reply to this message in Canvas by replying directly to this email, or by "
            f"clicking this link: {link} .  If you need help, see Canvas.{files}\n\nUpdate your notification settings\n"
            "https://canvas.tamu.edu/profile/communication")
    return mail(f"{name} <notifications@instructure.com>", ACCOUNT, f"{name} ({where}: MATH FOR BUSINESS) just sent you a message in Canvas.", body)


class FakeGmailRaw:
    """messages.list (a search by address fields and `after:`) and messages.get?format=raw; anything else fails the test."""

    def __init__(self):
        self.messages, self.calls, self.page = {}, [], 500

    def add(self, mid, raw, at=SEPT, labels=("INBOX",), thread="t" + "0" * 7):
        self.messages[mid] = {"raw": raw, "at": at, "labels": list(labels), "thread": thread}

    def _matches(self, found, query):
        after = int(query.split("after:")[1].split()[0])
        if found["at"] / 1000 <= after or {"DRAFT", "SPAM"} & set(found["labels"]):
            return False
        parsed = email.message_from_bytes(found["raw"], policy=policy.default)
        if 'subject:"' in query:  # Canvas's message notifications: from its domain, by a phrase of the subject
            phrase = query.split('subject:"')[1].split('"')[0].casefold()
            domain = query.split("from:@")[1].split()[0].casefold()
            return f"@{domain}" in str(parsed.get("From") or "").casefold() and phrase in str(parsed.get("Subject") or "").casefold()
        headers = " ".join(str(parsed.get(name) or "") for name in ("From", "To", "Cc", "Bcc")).casefold()
        wanted = {token.strip("()").casefold() for token in query.replace(":(", " ").split() if "@" in token}
        return any(address in headers for address in wanted)

    def __call__(self, method, url, body=None, headers=None, timeout=30):
        parts = urllib.parse.urlsplit(url)
        assert parts.hostname == "gmail.googleapis.com" and headers["Authorization"].startswith("Bearer ")
        path, query = parts.path.removeprefix("/gmail/v1/users/me"), urllib.parse.parse_qs(parts.query)
        self.calls.append((method, path, query))
        assert method == "GET", "the capture never changes Gmail"
        if path == "/messages":
            assert query["includeSpamTrash"] == ["true"] and "-in:spam" in query["q"][0] and "-in:drafts" in query["q"][0]
            ids = sorted(mid for mid, found in self.messages.items() if self._matches(found, query["q"][0]))
            start = int(query.get("pageToken", ["0"])[0])
            chunk = ids[start:start + self.page]
            return 200, {"messages": [{"id": mid, "threadId": self.messages[mid]["thread"]} for mid in chunk],
                         **({"nextPageToken": str(start + self.page)} if start + self.page < len(ids) else {})}
        if path.startswith("/messages/"):
            mid = path.rsplit("/", 1)[1]
            assert query["format"] == ["raw"]
            found = self.messages[mid]
            return 200, {"id": mid, "threadId": found["thread"], "labelIds": found["labels"], "internalDate": str(found["at"]),
                         "raw": base64.urlsafe_b64encode(found["raw"]).decode().rstrip("=")}
        raise AssertionError((method, path))


class Base(unittest.TestCase):
    storage = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        client_file, self.token_file = os.path.join(self.temp.name, "client.json"), os.path.join(self.temp.name, "token.json")
        with open(client_file, "w") as handle:
            json.dump({"web": {"client_id": "client-1.apps.googleusercontent.com", "client_secret": "secret", "project_id": "faculty-mail-1"}}, handle)
        with open(self.token_file, "w") as handle:
            json.dump({"refresh_token": "refresh-1", "email": ACCOUNT, "scopes": sorted(set(SCOPES) | set(MAIL_SCOPES)),
                       "connected_at": "2026-09-16T00:00:00+00:00"}, handle)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("FACULTY_")}
        with mock.patch.dict(os.environ, {**clean, "FACULTY_GMAIL_CLIENT_FILE": client_file, "FACULTY_GMAIL_TOKEN_FILE": self.token_file}, clear=True):
            if self.storage:
                os.makedirs(os.path.join(self.temp.name, "drives"))
                self.drives = temporary_storage(os.path.join(self.temp.name, "drives"))
                self.app = create_app(storage=self.drives)
            else:
                self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.client.get("/").close()
        self.record = self.app.extensions["communications"]
        self.gmail = FakeGmailRaw()
        self.record.reader.http = self.gmail
        self.record.google.http = FakeTokens()
        seed_course(self.client, code="MATH 140", sections=[507, 508], multiple_sections=True)
        self.group = self.client.get("/api/state").json["course_groups"][0]
        for number, rows in ROWS.items():
            answer = self.client.post(f"/api/course-groups/{self.group['id']}/sections/{number}/roster",
                                      data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
            self.assertEqual(answer.status_code, 201, answer.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.sections = {r["number"]: r["id"] for r in db.execute("SELECT id, number FROM course_sections")}
            self.term = db.execute("SELECT id FROM course_terms").fetchone()[0]
            self.students = {r["uin"]: r["id"] for r in db.execute("SELECT id, uin FROM student_enrollments")}

    def db(self):
        return connect(self.app.config["DATABASE"])

    def timeline(self, uin):
        answer = self.client.get(f"/api/communications/students/{self.students[uin]}")
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json["entries"]

    def capture(self, channel):
        with self.record.lock():
            return self.record.round(channel)


class GmailTests(Base):
    def add_mail(self):
        self.gmail.add("a1", mail("Alice Example <alice@tamu.edu>", ACCOUNT, "Exam 1 question", "Can I take the exam later?",
                                  files=[("note.pdf", b"%PDF-1.4 doctor's note", "application/pdf")]), at=millis(20), thread="c1" + "0" * 6)
        self.gmail.add("a2", mail(f"Prof <{ACCOUNT}>", "alice@email.tamu.edu", "Re: Exam 1 question", "Yes, see the DR center."),
                       at=millis(21), labels=("SENT",), thread="c1" + "0" * 6)
        self.gmail.add("b1", mail("Bob <bob@tamu.edu>", ACCOUNT, "Absence", "I was sick.", cc="carol@email.tamu.edu"), at=millis(22))
        self.gmail.add("old", mail("Alice Example <alice@tamu.edu>", ACCOUNT, "Summer question", "Before the term."),
                       at=int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000))
        self.gmail.add("f1", mail("Dana or Dan <family@email.tamu.edu>", ACCOUNT, "Which twin?", "Shared address."), at=millis(23))
        self.gmail.add("x1", mail("Registrar <reg@tamu.edu>", ACCOUNT, "Grades due", "Not a student."), at=millis(23))

    def test_mail_with_students_is_kept_whole_linked_by_roster_address_and_nothing_in_gmail_changes(self):
        self.add_mail()
        outcome = self.capture("gmail")
        self.assertEqual(outcome["status"], "ok", outcome)
        counts = json.loads(outcome["counts_json"])
        self.assertEqual((counts["new"], counts["skipped"]), (3, 1))  # a1, a2, b1; the twins' shared address names nobody
        self.assertTrue(all(call[0] == "GET" for call in self.gmail.calls))
        alice = self.timeline(ALICE)
        self.assertEqual([(e["subject"], e["author_role"], e["roles"]) for e in alice],
                         [("Re: Exam 1 question", "instructor", ["to"]), ("Exam 1 question", "student", ["author"])])
        self.assertEqual(alice[1]["text"].strip(), "Can I take the exam later?")
        self.assertEqual(alice[1]["attachments"][0]["name"], "note.pdf")
        # The attachment and the original come back from the kept message, checked.
        note = self.client.get(f"/api/communications/messages/{alice[1]['message_id']}/attachments/0")
        self.assertEqual((note.status_code, note.data), (200, b"%PDF-1.4 doctor's note"))
        self.assertIn("attachment;", note.headers["Content-Disposition"])
        self.assertEqual(note.headers["X-Content-Type-Options"], "nosniff")
        # COMMUNICATIONS THE TIMELINE (requirement, 2026-11-06): the download's own sandbox policy reaches the browser; the app-wide
        # policy does not replace it.
        sandbox = "default-src 'none'; sandbox; frame-ancestors 'none'"
        self.assertEqual(note.headers["Content-Security-Policy"], sandbox)
        original = self.client.get(f"/api/communications/messages/{alice[1]['message_id']}/original")
        self.assertEqual(original.data, self.gmail.messages["a1"]["raw"])
        self.assertEqual(original.headers["Content-Security-Policy"], sandbox)
        # Bob wrote, Carol was copied: each has it, with their own role.
        self.assertEqual([e["roles"] for e in self.timeline(BOB)], [["author"]])
        self.assertEqual([e["roles"] for e in self.timeline(CAROL)], [["cc"]])
        self.assertEqual(self.timeline(DANA), [])
        with self.db() as db:
            self.assertFalse(db.execute("SELECT 1 FROM comm_messages WHERE external_id IN ('old','x1','f1')").fetchone())
        # A second round finds the same mail and keeps nothing twice; no message is read again.
        reads = sum(1 for call in self.gmail.calls if call[1].startswith("/messages/"))
        again = self.capture("gmail")
        self.assertEqual(json.loads(again["counts_json"])["new"], 0)
        self.assertEqual(sum(1 for call in self.gmail.calls if call[1].startswith("/messages/")), reads)

    def test_searches_go_by_address_chunks_and_pages_and_a_student_added_later_is_linked_without_a_read(self):
        self.gmail.page = 1
        self.add_mail()
        self.gmail.add("a3", mail("Alice Example <alice@tamu.edu>", ACCOUNT, "Study group", "Eve and I have a question.", cc="eve@email.tamu.edu"),
                       at=millis(24))
        self.capture("gmail")
        searches = [call for call in self.gmail.calls if call[1] == "/messages"]
        self.assertTrue(any("pageToken" in call[2] for call in searches))
        self.assertTrue(all("alice@tamu.edu" in call[2]["q"][0] and "alice@email.tamu.edu" in call[2]["q"][0] for call in searches[:1]))
        # Registrar mail CC'ing a student who is added to the roster later is not the record's until then.
        self.gmail.add("e1", mail("Registrar <reg@tamu.edu>", ACCOUNT, "Enrollment", "Adding Eve.", cc="eve@email.tamu.edu"), at=millis(25))
        self.capture("gmail")
        with self.db() as db:
            self.assertFalse(db.execute("SELECT 1 FROM comm_messages WHERE external_id='e1'").fetchone())
        rows = [*ROWS[507], ["", "Example, Eve", "555666777", "Registered", "MATH", "U1", "", "Y", "", "", "", "eve@email.tamu.edu"]]
        answer = self.client.post(f"/api/course-groups/{self.group['id']}/sections/507/roster",
                                  data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
        self.assertIn(answer.status_code, (200, 201), answer.json)
        reads = [call[1] for call in self.gmail.calls if call[1].startswith("/messages/")]
        self.capture("gmail")
        with self.db() as db:
            eve = db.execute("SELECT id FROM student_enrollments WHERE uin='555666777'").fetchone()[0]
        entries = self.client.get(f"/api/communications/students/{eve}").json["entries"]
        self.assertEqual([(e["subject"], e["roles"]) for e in entries], [("Enrollment", ["cc"]), ("Study group", ["cc"])])
        # Study group was kept before Eve joined: it is linked from the kept addresses, not read again. Enrollment is new.
        self.assertEqual([call[1] for call in self.gmail.calls if call[1].startswith("/messages/")], [*reads, "/messages/e1"])

    def test_faculty_mail_page_sends_link_their_bcc_students_from_faculty_s_record(self):
        self.gmail.add("5e11d", mail(f"Prof <{ACCOUNT}>", ACCOUNT, "Class cancelled", "No class Friday."), at=millis(25), labels=("SENT",))
        now = "2026-09-25T15:00:00+00:00"
        with self.db() as db:
            db.execute("""INSERT INTO gmail_mail_sends(id, request_id, request_hash, sender, to_json, cc_json, bcc_json, subject, body_text,
                          actor, created_at) VALUES ('send-1','r1',?,?,?, '[]', ?, 'Class cancelled', 'No class Friday.', 'workspace', ?)""",
                       ("0" * 64, ACCOUNT, json.dumps([ACCOUNT]), json.dumps(["alice@email.tamu.edu", "carol@email.tamu.edu"]), now))
            db.execute("INSERT INTO gmail_mail_outcomes(send_id, status, message_id, thread_id, created_at) VALUES ('send-1','accepted','5e11d','5e11d',?)", (now,))
        self.capture("gmail")
        for uin in (ALICE, CAROL):
            entries = self.timeline(uin)
            self.assertEqual([(e["subject"], e["roles"]) for e in entries], [("Class cancelled", ["bcc"])])
        with self.db() as db:
            self.assertEqual({r[0] for r in db.execute("SELECT matched_by FROM comm_links")}, {"faculty_send"})

    def test_a_message_deleted_before_it_is_read_is_skipped_and_a_gmail_outage_waits(self):
        self.add_mail()
        fake = self.gmail

        def gone(method, url, body=None, headers=None, timeout=30):
            if url.split("?")[0].endswith("/messages/b1"):
                return 404, {"error": {"code": 404, "message": "Not Found"}}
            return fake(method, url, body, headers, timeout)

        self.record.reader.http = gone
        counts = json.loads(self.capture("gmail")["counts_json"])
        self.assertEqual((counts["new"], counts["gone"]), (2, 1))
        self.record.reader.http = lambda *args, **kwargs: (503, {"error": {"code": 503, "message": "Backend Error"}})
        outcome = self.capture("gmail")
        self.assertEqual((outcome["status"], outcome["detail"]), ("waiting", "Gmail is not answering right now."))

    def test_a_long_backlog_is_read_newest_first_over_rounds_and_a_slowdown_keeps_what_was_read(self):
        for n in range(7):
            self.gmail.add(f"{n + 10:x}0", mail("Alice Example <alice@tamu.edu>", ACCOUNT, f"Note {n}", "Hello."), at=millis(10 + n))
        fake, reads = self.gmail, []

        def limited(method, url, body=None, headers=None, timeout=30):
            if "/messages/" in url:
                reads.append(url.split("?")[0].rsplit("/", 1)[1])
                if len(reads) == 3:
                    return 429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "errors": [{"reason": "rateLimitExceeded"}]}}
            return fake(method, url, body, headers, timeout)

        self.record.reader.http = limited
        with mock.patch.object(comm_service, "READS", 4), mock.patch("gmail_mail.client.time.sleep"), mock.patch("gmail_mail.client.BACKOFF", ()):
            outcome = self.capture("gmail")
            self.assertEqual(outcome["status"], "waiting")
            self.assertIn("2 messages kept this round", outcome["detail"])
            self.assertEqual(reads[:2], ["100", "f0"])  # newest first
            outcome = self.capture("gmail")
            self.assertEqual((outcome["status"], json.loads(outcome["counts_json"])["backlog"]), ("ok", 1))
            self.assertIn("1 older messages still to read", outcome["detail"])
            self.assertLess(comm_service.parse_time(outcome["next_due_at"]) - comm_service.parse_time(outcome["last_round_at"]),
                            comm_service.timedelta(seconds=61))
            self.capture("gmail")
        self.assertEqual(len(self.timeline(ALICE)), 7)

    def test_canvas_message_emails_are_a_backup_linked_to_the_one_student_their_name_and_section_fit(self):
        self.gmail.add("c1", notice("Alice Example", "26 FALL MATH 140 507", "60123", "I have the flu.\nSorry.", subject="Flu"), at=millis(22))
        self.gmail.add("c2", notice("Carol Example", "26 FALL MATH 140 507", "60124", "Wrong section."), at=millis(22))  # Carol is in 508
        self.gmail.add("c3", notice("Example", "26 FALL MATH 140 507", "60125", "One word."), at=millis(22))
        self.gmail.add("c4", notice("Dan Twin", "26 FALL MATH 140 508", "60126", "Dan here."), at=millis(23))
        self.gmail.add("c5", mail("Canvas <notifications@instructure.com>", ACCOUNT, "Assignment Graded: Quiz 3", "Your grade."), at=millis(23))
        outcome = self.capture("gmail")
        counts = json.loads(outcome["counts_json"])
        self.assertEqual((counts["canvas_notices"], counts["canvas_notices_unmatched"]), (2, 2))
        alice = self.timeline(ALICE)
        self.assertEqual([(e["channel"], e["channel_label"], e["subject"], e["text"], e["author_role"], e["roles"]) for e in alice],
                         [("gmail", "Canvas message (email)", "Flu", "I have the flu.\nSorry.", "student", ["author"])])
        self.assertTrue(alice[0]["context"]["canvas_notice"] and alice[0]["original"])
        self.assertEqual((alice[0]["context"]["conversation_id"], alice[0]["context"]["url"]), ("60123", "https://canvas.tamu.edu/conversations/60123"))
        self.assertEqual([e["text"] for e in self.timeline(DAN)], ["Dan here."])
        self.assertEqual(self.timeline(CAROL), [])
        self.assertEqual(self.timeline(DANA), [])
        with self.db() as db:
            self.assertEqual({r[0] for r in db.execute("SELECT matched_by FROM comm_links")}, {"canvas_notice"})
            self.assertFalse(db.execute("SELECT 1 FROM comm_messages WHERE external_id='c5'").fetchone())
        # A notification from a student who is not on the roster yet is read again an hour later and then linked.
        self.gmail.add("c6", notice("Eve Example", "26 FALL MATH 140 507", "60127", "New here."), at=millis(24))
        self.capture("gmail")
        rows = [*ROWS[507], ["", "Example, Eve", "555666777", "Registered", "MATH", "U1", "", "Y", "", "", "", "eve@email.tamu.edu"]]
        answer = self.client.post(f"/api/course-groups/{self.group['id']}/sections/507/roster",
                                  data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
        self.assertIn(answer.status_code, (200, 201), answer.json)
        self.record._unlinkable_at -= comm_service.UNLINKABLE_FOR + 1
        self.capture("gmail")
        with self.db() as db:
            eve = db.execute("SELECT id FROM student_enrollments WHERE uin='555666777'").fetchone()[0]
        self.assertEqual([e["text"] for e in self.client.get(f"/api/communications/students/{eve}").json["entries"]], ["New here."])

    def test_waiting_when_gmail_is_not_connected_for_reading(self):
        with open(self.token_file, "w") as handle:
            json.dump({"refresh_token": "refresh-1", "email": ACCOUNT, "scopes": sorted(SCOPES), "connected_at": "2026-09-16T00:00:00+00:00"}, handle)
        outcome = self.capture("gmail")
        self.assertEqual((outcome["status"], outcome["detail"]), ("waiting", "Connect Gmail for reading on the Mail page."))
        self.assertEqual(self.gmail.calls, [])

    def test_parse_reads_html_only_mail_as_text_and_lists_attachments_by_part(self):
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = "A <a@tamu.edu>", ACCOUNT, "Hi"
        message.set_content("<p>Hello <b>there</b></p><script>x()</script>", subtype="html")
        message.add_attachment(b"PNGDATA", maintype="image", subtype="png", filename="work.png")
        parsed = gmail_capture.parse(message.as_bytes())
        self.assertEqual(parsed["text"], "Hello there")
        self.assertEqual([(a["name"], a["size"]) for a in parsed["attachments"]], [("work.png", 7)])
        self.assertEqual(gmail_capture.attachment(message.as_bytes(), parsed["attachments"][0]["part"])[2], b"PNGDATA")


NOTE, LETTER, MINE, FOLDER = "1mIt3EgEPVD_DVM5TgXxKM9zLR__LNRB9", "1DocOfAliceLetterToHerProfessor_abc", "1InstructorsOwnSolutionFile_xyz-01", "1FolderOfManyThingsNotOneFile_00000"


def wrapped(url, token="KwNVnqRv!CvZSqowVNsaO8eOy9LlEPo5vt-BFws"):
    """A link as TAMU's mail filter delivers it (Proofpoint URL Defense v3, the shape on the live record 2026-10-11)."""
    return f"https://urldefense.com/v3/__{url}__;!!{token}$"


class FakeDrive:
    """files.get (a file's name, kind and size, then its bytes) and files.export as a PDF; anything else fails the test."""

    def __init__(self):
        self.files, self.calls, self.down, self.stale = {}, [], False, ""

    def add(self, ident, name, kind, content, shared=True):
        self.files[ident] = {"name": name, "kind": kind, "content": content, "shared": shared}

    def __call__(self, url, headers=None, timeout=60, limit=65536):
        parts = urllib.parse.urlsplit(url)
        assert parts.scheme == "https" and parts.hostname == "www.googleapis.com" and parts.path.startswith("/drive/v3/files/"), url
        assert headers["Authorization"].startswith("Bearer ")
        query = urllib.parse.parse_qs(parts.query)
        ident, _, verb = parts.path.removeprefix("/drive/v3/files/").partition("/")
        self.calls.append((ident, verb or ("media" if query.get("alt") == ["media"] else "info")))
        if self.down:
            return 503, b'{"error": {"code": 503, "message": "Backend Error", "errors": [{"reason": "backendError"}]}}'
        if self.stale and headers["Authorization"] == self.stale:  # a token made before Drive was allowed
            return 403, json.dumps({"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                                              "errors": [{"reason": "insufficientPermissions"}], "status": "PERMISSION_DENIED"}}).encode()
        found = self.files.get(ident)
        if not found or not found["shared"]:  # Drive says "not found" for a file the account may not see
            return 404, json.dumps({"error": {"code": 404, "message": f"File not found: {ident}.", "errors": [{"reason": "notFound"}]}}).encode()
        native = found["kind"].startswith("application/vnd.google-apps.")
        if verb == "export":
            assert native and query["mimeType"] == ["application/pdf"]
            return 200, b"%PDF-1.4 exported " + found["content"]
        assert not verb, verb
        if query.get("alt") == ["media"]:
            assert not native
            return 200, found["content"][:limit + 1]
        assert query["fields"] == ["id,name,mimeType,size"]
        return 200, json.dumps({"id": ident, "name": found["name"], "mimeType": found["kind"],
                                **({} if native else {"size": str(len(found["content"]))})}).encode()


class DriveLinkTests(unittest.TestCase):
    def test_the_mail_filters_rewritten_links_are_put_back(self):
        self.assertEqual(drive_links.unwrapped(f"see <{wrapped('https://drive.google.com/file/d/' + NOTE)} >"),
                         f"see <https://drive.google.com/file/d/{NOTE} >")
        # Characters the filter replaced come back from its list: one for "*", a run for "**X" (A is two).
        self.assertEqual(drive_links.unwrapped("https://urldefense.com/v3/__https:/x.example/a*b**Ac__;IyMj!!tok$"), "https://x.example/a#b##c")
        self.assertEqual(drive_links.unwrapped("no link here"), "no link here")

    def test_a_file_is_found_by_its_id_in_the_words_written_now_once_with_the_name_gmail_wrote(self):
        text = "\n".join([
            "Please see my note.", "", "[image: PDF File]", f"<{wrapped('https://drive.google.com/file/d/' + NOTE)} >", "2nd Dr Note.pdf",
            f"<{wrapped('https://drive.google.com/file/d/' + NOTE + '/view?usp=drive_web')}>", " MR Medical Excuse 9-9-26.pdf",
            f"<{wrapped('https://drive.google.com/open?id=' + LETTER)}>",
            f"A folder https://drive.google.com/drive/folders/{FOLDER} and a published page https://docs.google.com/document/d/e/2PACX-1vQpublishedPageNotAFile00/pub",
            "On Mon, Oct 5, 2026 at 9:00 AM Prof <prof@tamu.edu> wrote:", f"> <{wrapped('https://drive.google.com/file/d/' + MINE + '/view?usp=drive_web')}>",
            f"https://docs.google.com/spreadsheets/d/{MINE}/edit"])
        self.assertEqual(drive_links.linked(text), [{"id": NOTE, "name": "2nd Dr Note.pdf"}, {"id": LETTER, "name": "MR Medical Excuse 9-9-26.pdf"}])
        self.assertEqual(drive_links.linked(f"> https://drive.google.com/file/d/{MINE}/view\nhttps://docs.google.com/document/d/{LETTER}/edit?usp=sharing"),
                         [{"id": LETTER, "name": ""}])
        many = "\n".join(f"https://drive.google.com/file/d/{'1' * 20}{n:02d}" for n in range(9))
        self.assertEqual(len(drive_links.linked(many)), drive_links.MAX_LINKS)


class DriveTests(Base):
    """LINKED DRIVE FILES: a file a student links from Google Drive is fetched and kept with their message's attachments."""

    def setUp(self):
        super().setUp()
        self.drive = FakeDrive()
        self.record.drive.http = self.drive

    def allow(self, drive=True):
        with open(self.token_file, "w") as handle:
            json.dump({"refresh_token": "refresh-1", "email": ACCOUNT, "connected_at": "2026-09-16T00:00:00+00:00",
                       "scopes": sorted(set(SCOPES) | set(MAIL_SCOPES) | (set(DRIVE_SCOPES) if drive else set()))}, handle)

    def note_mail(self, mid="d1", sender="Alice Example <alice@tamu.edu>", day=20, files=(("quiz.pdf", b"%PDF-1.4 her quiz", "application/pdf"),)):
        link = wrapped("https://drive.google.com/file/d/" + NOTE)
        self.gmail.add(mid, mail(sender, ACCOUNT, "Re: Sick", f"I am attaching my quiz.\n\n[image: PDF File]\n<{link} >\n2nd Dr Note.pdf\n<{link} >\n",
                                 files=list(files)), at=millis(day), thread="d1" + "0" * 6)

    def files(self, uin=ALICE, subject="Re: Sick"):
        entry = next(e for e in self.timeline(uin) if e["subject"] == subject)
        return entry, entry["attachments"]

    def test_a_linked_file_is_kept_with_the_messages_attachments_and_serves_as_an_absences_documentation(self):
        self.allow()
        self.drive.add(NOTE, "2nd Dr Note.pdf", "application/pdf", b"%PDF-1.4 the second doctor's note")
        self.note_mail()
        outcome = self.capture("gmail")
        self.assertEqual((outcome["status"], json.loads(outcome["counts_json"])["drive_files"]), ("ok", 1), outcome)
        entry, files = self.files()
        self.assertEqual((entry["revision"], entry["edited"]), (1, False))  # there from the message's first revision
        self.assertEqual(files, [{"index": 0, "name": "quiz.pdf", "type": "application/pdf", "size": 17, "kept": True},
                                 {"index": 1, "name": "2nd Dr Note.pdf", "type": "application/pdf", "size": 33, "kept": True, "linked": "google_drive"}])
        self.assertEqual(self.drive.calls, [(NOTE, "info"), (NOTE, "media")])  # linked twice in the message, read once
        note = self.client.get(f"/api/communications/messages/{entry['message_id']}/attachments/1")
        self.assertEqual((note.status_code, note.data), (200, b"%PDF-1.4 the second doctor's note"))
        self.assertIn("attachment;", note.headers["Content-Disposition"])
        self.assertEqual(self.client.get(f"/api/communications/messages/{entry['message_id']}/attachments/0").data, b"%PDF-1.4 her quiz")
        # Another round reads nothing from Drive again and writes no revision.
        self.capture("gmail")
        self.assertEqual((len(self.drive.calls), self.files()[0]["revision"]), (2, 1))
        # It is a file the student sent: an absence takes it as its documentation, by its place among the message's attachments.
        absence = self.client.post("/api/absences", headers=HEADERS, json={
            "section_id": self.sections[507], "enrollment_id": self.students[ALICE], "first_date": "2026-09-21", "last_date": "2026-09-21",
            "reason": "illness", "request_id": "linked-note", "documents": [{"message_id": entry["message_id"], "index": 1}]})
        self.assertEqual(absence.status_code, 201, absence.json)
        document = absence.json["documents"][0]
        self.assertEqual((document["name"], document["size"]), ("2nd Dr Note.pdf", 33))
        kept = self.client.get(f"/api/absences/{absence.json['id']}/documents/{document['id']}")
        self.assertEqual((kept.status_code, kept.data), (200, b"%PDF-1.4 the second doctor's note"))
        self.assertEqual(kept.headers["Content-Security-Policy"], "default-src 'none'; sandbox; frame-ancestors 'none'")

    def test_quoted_history_the_instructors_own_file_and_what_is_not_a_file_are_left_alone(self):
        self.allow()
        self.drive.add(LETTER, "Letter", "application/vnd.google-apps.document", b"letter")
        self.drive.add(MINE, "Solutions.pdf", "application/pdf", b"%PDF-1.4 the instructor's own")
        self.drive.add(FOLDER, "Homework", "application/vnd.google-apps.folder", b"")
        thread = "e1" + "0" * 6
        self.gmail.add("e1", mail(f"Prof <{ACCOUNT}>", "alice@email.tamu.edu", "Solutions", f"Here they are.\n Solutions.pdf\n<https://drive.google.com/file/d/{MINE}/view?usp=drive_web>"),
                       at=millis(18), labels=("SENT",), thread=thread)
        own = wrapped(f"https://drive.google.com/file/d/{MINE}/view?usp=drive_web")
        self.gmail.add("e2", mail("Alice Example <alice@tamu.edu>", ACCOUNT, "Re: Solutions",
                                  f"Thank you. My letter: https://docs.google.com/document/d/{LETTER}/edit?usp=sharing\n"
                                  f"and my folder https://drive.google.com/drive/folders/{FOLDER}\n\n> Here they are.\n>  Solutions.pdf\n> <{own}>"),
                       at=millis(19), thread=thread)
        # A reply that carries the instructor's link with no quote marks: still the instructor's file, linked first by them.
        self.gmail.add("e3", mail("Bob <bob@tamu.edu>", ACCOUNT, "Fwd: Solutions", f"Is this the right file?\n<{own}>"), at=millis(21), thread=thread)
        self.capture("gmail")
        self.assertEqual(self.files(ALICE, "Re: Solutions")[1], [
            {"index": 0, "name": "Letter.pdf", "type": "application/pdf", "size": 24, "kept": True, "linked": "google_drive"}])
        self.assertEqual(self.files(BOB, "Fwd: Solutions")[1], [])
        self.assertEqual(self.files(ALICE, "Solutions")[1], [])       # what the instructor sent is never fetched
        self.assertEqual(self.drive.calls, [(LETTER, "info"), (LETTER, "export")])

    def test_a_file_faculty_cannot_read_yet_says_why_and_is_fetched_once_it_can(self):
        self.note_mail()
        self.capture("gmail")
        entry, files = self.files()
        self.assertEqual(files[1], {"index": 1, "name": "2nd Dr Note.pdf", "kept": False, "reason": "Faculty may not read your Google Drive yet",
                                    "linked": "google_drive", "wait": "permission"})
        self.assertEqual(self.drive.calls, [])                        # nothing is asked of Drive without the permission
        refused = self.client.get(f"/api/communications/messages/{entry['message_id']}/attachments/1")
        self.assertEqual(refused.status_code, 404)
        self.assertIn("Faculty may not read your Google Drive yet", refused.json["error"])
        status = self.client.get("/api/communications/status").json["drive"]
        self.assertEqual((status["ready"], status["connected"]), (False, True))
        self.assertIn("not fetched", status["setup"])
        # Allowing it is the instructor's own click: Google is asked for read-only Drive, for the account already connected.
        started = self.client.post("/api/communications/drive/connect", json={}, headers=HEADERS)
        self.assertEqual(started.status_code, 200, started.json)
        asked = urllib.parse.parse_qs(urllib.parse.urlsplit(started.json["url"]).query)
        self.assertIn("https://www.googleapis.com/auth/drive.readonly", asked["scope"][0].split())
        self.assertEqual((asked["login_hint"], asked["include_granted_scopes"]), ([ACCOUNT], ["true"]))
        self.assertEqual(self.record.google.pending_purpose(), "drive")
        # Allowed, but the student shared the file with nobody: said so, and tried again once per worker run, not every round.
        self.allow()
        self.drive.add(NOTE, "2nd Dr Note.pdf", "application/pdf", b"%PDF-1.4 the second doctor's note", shared=False)
        self.capture("gmail")
        entry, files = self.files()
        self.assertEqual((entry["revision"], files[1]["kept"], files[1]["wait"], files[1]["name"]), (2, False, "sharing", "2nd Dr Note.pdf"))
        self.assertIn(f"does not show it to {ACCOUNT}", files[1]["reason"])
        self.capture("gmail")
        self.assertEqual(self.drive.calls, [(NOTE, "info")])
        # She shares it; the next worker run fetches it into the same place, as a revision whose words did not change.
        self.drive.files[NOTE]["shared"] = True
        self.record._drive_tried.clear()
        self.capture("gmail")
        entry, files = self.files()
        self.assertEqual((entry["revision"], entry["edited"], files[0]["name"]), (3, False, "quiz.pdf"))
        self.assertEqual(files[1], {"index": 1, "name": "2nd Dr Note.pdf", "type": "application/pdf", "size": 33, "kept": True, "linked": "google_drive"})
        self.assertEqual(self.client.get(f"/api/communications/messages/{entry['message_id']}/attachments/1").data, b"%PDF-1.4 the second doctor's note")
        self.assertTrue(self.client.get("/api/communications/status").json["drive"]["ready"])

    def test_allowing_drive_comes_back_to_follow_up_and_asks_for_a_gmail_round_now(self):
        started = self.client.post("/api/communications/drive/connect", json={}, headers=HEADERS).json["url"]
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(started).query)["state"][0]
        self.record.google.http = FakeTokens(scope=" ".join(sorted(set(SCOPES) | set(MAIL_SCOPES) | set(DRIVE_SCOPES))))
        back = self.client.get(f"/api/response-cards/gmail/callback?state={state}&code=granted")
        self.assertEqual((back.status_code, back.headers["Location"]), (302, "/#follow-ups"))
        status = self.client.get("/api/communications/status").json
        self.assertEqual((status["drive"]["ready"], status["drive"]["setup"], status["channels"]["gmail"]["requested"]), (True, "", True))
        # Google refusing the permission says so, naming Drive, and nothing is connected anew.
        self.allow(drive=False)
        started = self.client.post("/api/communications/drive/connect", json={}, headers=HEADERS).json["url"]
        refused = self.client.get("/api/response-cards/gmail/callback?error=access_denied")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Google Drive was not connected", refused.get_data(as_text=True))
        self.assertFalse(self.client.get("/api/communications/status").json["drive"]["ready"])

    def test_a_message_kept_before_faculty_read_drive_gets_its_linked_file_and_drive_not_answering_is_tried_next_round(self):
        self.allow()
        self.note_mail()
        with mock.patch.object(self.record, "_link_drive"), mock.patch.object(self.record, "_linked_later"):
            self.capture("gmail")                                     # as the capture kept it before this rule
        self.assertEqual([file["name"] for file in self.files()[1]], ["quiz.pdf"])
        self.drive.add(NOTE, "2nd Dr Note.pdf", "application/pdf", b"%PDF-1.4 the second doctor's note")
        self.drive.down = True
        self.capture("gmail")
        entry, files = self.files()
        self.assertEqual((entry["revision"], files[1]["kept"], files[1]["wait"], files[1]["reason"]), (2, False, "google", "Google Drive is not answering right now"))
        self.capture("gmail")                                         # the same answer writes no further revision
        self.assertEqual(self.files()[0]["revision"], 2)
        self.drive.down = False
        self.capture("gmail")
        entry, files = self.files()
        self.assertEqual((entry["revision"], [file["name"] for file in files], files[1]["kept"]), (3, ["quiz.pdf", "2nd Dr Note.pdf"], True))

    def test_a_token_made_before_drive_was_allowed_is_renewed_once(self):
        self.allow()
        self.drive.add(NOTE, "2nd Dr Note.pdf", "application/pdf", b"%PDF-1.4 the second doctor's note")
        issued = iter(["made-before", "made-after"])
        tokens = self.record.google.http
        self.record.google.http = lambda url, data=None, headers=None, timeout=30: (
            (200, {"access_token": next(issued), "expires_in": 3600}) if (data or {}).get("grant_type") == "refresh_token" else tokens(url, data, headers, timeout))
        self.drive.stale = "Bearer made-before"                       # the worker's token of before the Allow click
        self.note_mail()
        self.capture("gmail")
        self.assertEqual(self.files()[1][1]["kept"], True)
        self.assertEqual(self.drive.calls, [(NOTE, "info"), (NOTE, "info"), (NOTE, "media")])

    def test_the_message_is_kept_whatever_becomes_of_its_linked_file(self):
        self.allow()
        self.note_mail()
        with mock.patch.object(self.record.drive, "http", side_effect=RuntimeError("unexpected")), self.assertLogs("communications.service", "ERROR"):
            outcome = self.capture("gmail")
        self.assertEqual((outcome["status"], json.loads(outcome["counts_json"])["new"]), ("ok", 1))
        files = self.files()[1]
        self.assertEqual((files[0]["name"], files[1]["kept"], files[1]["wait"]), ("quiz.pdf", False, "google"))

    def test_a_file_too_large_is_not_kept_and_never_tried_again(self):
        self.allow()
        self.drive.add(NOTE, "Lecture recording.mp4", "video/mp4", b"0" * 64)
        self.note_mail(files=())
        with mock.patch.object(comm_service, "ATTACHMENT_LIMIT", 32):
            self.capture("gmail")
            self.capture("gmail")
        self.assertEqual(self.files()[1], [{"index": 0, "name": "2nd Dr Note.pdf", "kept": False, "reason": "larger than Faculty keeps", "linked": "google_drive"}])
        self.assertEqual(self.drive.calls, [(NOTE, "info")])          # its size was enough; its bytes were never asked for


class StorageTests(GmailTests):
    storage = True

    def test_originals_are_a_verified_bundle_backed_up_then_kept_on_the_hdd_and_checked_on_every_read(self):
        self.add_mail()
        self.capture("gmail")
        with self.db() as db:
            bundle = db.execute("SELECT * FROM comm_bundles").fetchone()
            files = [dict(r) for r in db.execute("SELECT * FROM comm_files")]
        self.assertEqual((bundle["storage_status"], len(files)), ("staged", 3))
        hot = self.drives.nvme / "communications" / bundle["id"] / "data"
        self.assertTrue(all((hot / f["path"]).is_file() for f in files))
        state = self.drives.backup_all()
        self.assertEqual(state["status"], "complete", state)
        with self.db() as db:
            after = db.execute("SELECT * FROM comm_bundles").fetchone()
        self.assertEqual(after["storage_status"], "archived")
        self.assertFalse(hot.exists())
        entry = next(e for e in self.timeline(ALICE) if e["attachments"])
        self.assertEqual(self.client.get(f"/api/communications/messages/{entry['message_id']}/attachments/0").data, b"%PDF-1.4 doctor's note")
        # A kept file changed on the HDD is refused, never served.
        snapshot = self.drives.hdd / "communications" / bundle["id"] / "snapshots" / after["storage_snapshot"] / "data"
        target = next(snapshot.iterdir())
        os.chmod(target, 0o600)
        target.write_bytes(b"tampered")
        with self.assertRaises(StorageError):
            self.record.files.read(files[0] | {"path": target.name, "sha256": next(f["sha256"] for f in files if f["path"] == target.name)})

    def test_an_interrupted_capture_leaves_no_record_and_its_folder_is_swept(self):
        self.add_mail()
        with mock.patch("communications.record.record", side_effect=sqlite3.OperationalError("interrupted")), \
                self.assertLogs("communications.service", "ERROR"):
            outcome = self.capture("gmail")
        self.assertEqual(outcome["status"], "failed")
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM comm_bundles").fetchone()[0], 0)
        left = list((self.drives.nvme / "communications").iterdir())
        self.assertEqual(len(left), 1)
        old = time.time() - 2 * 3600
        os.utime(left[0], (old, old))
        self.record.files.sweep()
        self.assertEqual(list((self.drives.nvme / "communications").iterdir()), [])
        self.assertEqual(self.capture("gmail")["status"], "ok")
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM comm_messages").fetchone()[0], 3)


class CanvasTests(Base):
    def setUp(self):
        super().setUp()
        self.canvas = FakeCanvas().start()
        self.addCleanup(self.canvas.stop)
        self.settings = canvas_web.Settings(session_file=os.path.join(self.temp.name, "config", "canvas.json"), base=self.canvas.url)
        self.app.extensions["canvas"].settings = self.settings
        canvas_web.save(self.settings, self.canvas.session(), user={"name": "Pat Instructor"})
        self.course = self.canvas.add_course("MATH-140-507", "MATH-140-507-2026C", "Fall 2026")
        section = self.canvas.add_section(self.course, "507")
        self.alice = self.canvas.add_student(self.course, section, "Example, Alice", ALICE)
        self.bob = self.canvas.add_student(self.course, section, "Example, Bob", BOB)
        self.stranger = self.canvas.add_student(self.course, section, "Visitor, Val", "999888777")
        self.ta = "8001"
        with self.db() as db:
            db.execute("INSERT INTO canvas_course_links VALUES (?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), self.sections[507], self.course, "MATH-140-507", "MATH-140-507-2026C", "Fall 2026", "workspace",
                        "2026-09-27T00:00:00+00:00"))

    def test_conversations_and_comments_are_captured_read_only_with_their_attachments(self):
        note = self.canvas.add_blob("work.pdf", b"%PDF-1.4 work")
        conversation = self.canvas.add_conversation(self.course, "Late homework", [self.alice])
        self.canvas.add_message(conversation, self.alice, "Can I turn it in late?", "2026-09-22T14:00:00Z", attachments=[note])
        self.canvas.add_message(conversation, self.canvas.user["id"], "Yes, by Friday.", "2026-09-22T15:00:00Z")
        archived = self.canvas.add_conversation(self.course, "Old thread", [self.bob], archived=True)
        self.canvas.add_message(archived, self.bob, "Thanks!", "2026-09-10T14:00:00Z")
        homework = self.canvas.add_assignment(self.course, "Homework 3")
        self.canvas.add_comment(self.course, homework, self.alice, self.alice, "Example, Alice", "I uploaded the wrong file.", "2026-09-23T10:00:00Z")
        self.canvas.add_comment(self.course, homework, self.alice, self.canvas.user["id"], "Pat Instructor", "Fixed.", "2026-09-23T11:00:00Z")
        self.canvas.add_comment(self.course, homework, self.alice, self.ta, "Terry TA", "Graded.", "2026-09-23T12:00:00Z")
        stranger = self.canvas.add_conversation(self.course, "Not on the roster", [self.stranger])
        self.canvas.add_message(stranger, self.stranger, "Hello?", "2026-09-22T09:00:00Z")
        outcome = self.capture("canvas")
        self.assertEqual(outcome["status"], "ok", outcome)
        counts = json.loads(outcome["counts_json"])
        self.assertEqual((counts["other_authors"], counts["unmatched_students"]), (1, 1))
        alice = self.timeline(ALICE)
        self.assertEqual([(e["channel"], e["author_role"], e["text"]) for e in alice], [
            ("canvas_comment", "instructor", "Fixed."), ("canvas_comment", "student", "I uploaded the wrong file."),
            ("canvas_message", "instructor", "Yes, by Friday."), ("canvas_message", "student", "Can I turn it in late?")])
        self.assertEqual(alice[1]["subject"], "Comment on Homework 3")
        kept = alice[3]["attachments"][0]
        self.assertTrue(kept["kept"])
        self.assertEqual(self.client.get(f"/api/communications/messages/{alice[3]['message_id']}/attachments/0").data, b"%PDF-1.4 work")
        self.assertEqual([e["subject"] for e in self.timeline(BOB)], ["Old thread"])
        # Read only: nothing marked read, no write of any kind, and the file service never saw the session's cookies.
        self.assertEqual(self.canvas.marked_read, [])
        self.assertEqual([r for r in self.canvas.requests if r["method"] != "GET"], [])
        # The download went through Canvas's two redirects; off Canvas's host the session's cookies never went along.
        away = [r for r in self.canvas.file_requests if not r["path"].startswith("/files/")]
        self.assertEqual([r["path"].split("/")[1] for r in away], ["fileservice", "storage"])
        self.assertTrue(all(r["cookies"] == {} and r["host"].startswith("localhost") for r in away))
        # Nothing new: the conversation is not opened again, and an edited comment is a new revision.
        opened = sum(1 for r in self.canvas.requests if r["path"] == f"/api/v1/conversations/{conversation}")
        comment = self.canvas.comments[(self.course, self.alice, homework)][0]
        comment.update(comment="I uploaded the wrong file (fixed now).", edited_at="2026-09-24T10:00:00Z")
        self.capture("canvas")
        self.assertEqual(sum(1 for r in self.canvas.requests if r["path"] == f"/api/v1/conversations/{conversation}"), opened)
        edited = next(e for e in self.timeline(ALICE) if e["text"].startswith("I uploaded"))
        self.assertTrue(edited["edited"])
        history = self.client.get(f"/api/communications/messages/{edited['message_id']}/revisions").json["revisions"]
        self.assertEqual([r["text"] for r in history], ["I uploaded the wrong file.", "I uploaded the wrong file (fixed now)."])

    def test_a_message_read_from_canvas_and_its_notification_email_are_one_entry(self):
        conversation = self.canvas.add_conversation(self.course, "Late homework", [self.alice])
        self.canvas.add_message(conversation, self.alice, "Can I turn it in late?", "2026-09-22T14:00:00Z")
        with_file = self.canvas.add_conversation(self.course, "Photo", [self.alice])
        photo = self.canvas.add_message(with_file, self.alice, "", "2026-09-22T16:00:00Z", attachments=[self.canvas.add_blob("p.jpg", b"JPG", "image/jpeg")])
        at = int(datetime(2026, 9, 22, 14, 1, tzinfo=timezone.utc).timestamp() * 1000)
        self.gmail.add("d1", notice("Alice Example", "26 FALL MATH 140 507", conversation, "Can I turn it in late?", subject="Late homework"), at=at)
        self.gmail.add("d2", notice("Alice Example", "26 FALL MATH 140 507", with_file, "", subject="Photo", message=photo), at=at + 7200 * 1000)
        self.capture("gmail")
        before = [(e["channel"], e["text"]) for e in self.timeline(ALICE)]
        self.assertEqual([channel for channel, _ in before], ["gmail", "gmail"])  # only the emails while Canvas is not read
        self.capture("canvas")
        entries = self.timeline(ALICE)
        self.assertEqual([(e["channel"], e["subject"]) for e in entries], [("canvas_message", "Photo"), ("canvas_message", "Late homework")])
        with self.db() as db:
            emails = {r[0]: r[1] for r in db.execute("SELECT external_id, id FROM comm_messages WHERE channel='gmail'")}
        self.assertEqual([e["email_copy"] for e in entries], [emails["d2"], emails["d1"]])  # by the message it names, and by its words
        copy = self.client.get(f"/api/communications/messages/{emails['d1']}/original")
        self.assertEqual(copy.data, self.gmail.messages["d1"]["raw"])

    def test_a_file_sent_to_many_students_is_kept_once_and_one_that_failed_is_tried_again(self):
        memo = self.canvas.add_blob("memo.pdf", b"%PDF-1.4 memo")
        for student in (self.alice, self.bob):  # sent one by one: a conversation each, the same file in Canvas
            conversation = self.canvas.add_conversation(self.course, "Memo", [student])
            self.canvas.add_message(conversation, self.canvas.user["id"], "See the memo.", "2026-09-22T15:00:00Z", attachments=[memo])
        late = self.canvas.add_blob("late.pdf", b"%PDF-1.4 late")
        conversation = self.canvas.add_conversation(self.course, "Late", [self.alice])
        self.canvas.add_message(conversation, self.alice, "Here is my work.", "2026-09-23T15:00:00Z", attachments=[late])
        self.canvas.failing.add(late)
        # Another section's course, with a file failing too: every course's retries are made in the same round.
        other = self.canvas.add_course("MATH-140-508", "MATH-140-508-2026C", "Fall 2026")
        carol = self.canvas.add_student(other, self.canvas.add_section(other, "508"), "Example, Carol", CAROL)
        with self.db() as db:
            db.execute("INSERT INTO canvas_course_links VALUES (?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), self.sections[508], other, "MATH-140-508", "MATH-140-508-2026C", "Fall 2026", "workspace",
                        "2026-09-27T00:00:00+00:00"))
        sick = self.canvas.add_blob("sick.pdf", b"%PDF-1.4 sick")
        self.canvas.add_message(self.canvas.add_conversation(other, "Sick", [carol]), carol, "Doctor's note.", "2026-09-23T16:00:00Z", attachments=[sick])
        self.canvas.failing.add(sick)
        outcome = self.capture("canvas")
        self.assertEqual(json.loads(outcome["counts_json"])["files_reused"], 1)
        stored = [r for r in self.canvas.file_requests if r["path"].startswith("/storage/")]
        self.assertEqual(len(stored), 1)  # the memo, once
        entries = {e["subject"]: e for e in self.timeline(ALICE)}
        bob = {e["subject"]: e for e in self.timeline(BOB)}
        self.assertTrue(entries["Memo"]["attachments"][0]["kept"] and bob["Memo"]["attachments"][0]["kept"])
        self.assertEqual(self.client.get(f"/api/communications/messages/{bob['Memo']['message_id']}/attachments/0").data, b"%PDF-1.4 memo")
        self.assertEqual(entries["Late"]["attachments"][0]["reason"], "Canvas's file service answered 500.")
        # Tried again once in a worker run: the next round asks Canvas again, the one after does not.
        requests = lambda blob: sum(1 for r in self.canvas.file_requests if r["path"] == f"/fileservice/{blob}")
        self.capture("canvas")
        self.assertEqual((requests(late), requests(sick)), (2, 2))
        self.capture("canvas")
        self.assertEqual((requests(late), requests(sick)), (2, 2))
        # Once the file is there (a new worker run), it is kept; the message is not marked edited.
        self.canvas.failing.clear()
        self.record._retried = set()
        self.capture("canvas")
        again = {e["subject"]: e for e in self.timeline(ALICE)}["Late"]
        self.assertTrue(again["attachments"][0]["kept"])
        self.assertFalse(again["edited"])
        self.assertEqual(self.client.get(f"/api/communications/messages/{again['message_id']}/attachments/0").data, b"%PDF-1.4 late")
        self.assertTrue({e["subject"]: e for e in self.timeline(CAROL)}["Sick"]["attachments"][0]["kept"])

    def test_messages_to_the_whole_class_are_marked_mass_by_their_reach(self):
        group = self.canvas.add_conversation(self.course, "Mistake in Example 5", [self.alice, self.bob])
        self.canvas.add_message(group, self.canvas.user["id"], "I chose the wrong pivot.", "2026-09-24T15:00:00Z")
        for student in (self.alice, self.bob):  # the same words sent one by one: a conversation each
            one = self.canvas.add_conversation(self.course, "Quiz moved", [student])
            self.canvas.add_message(one, self.canvas.user["id"], "The quiz is on Friday.", "2026-09-25T15:00:00Z")
        mine = self.canvas.add_conversation(self.course, "Just you", [self.alice])
        self.canvas.add_message(mine, self.canvas.user["id"], "See me after class.", "2026-09-26T15:00:00Z")
        self.canvas.add_message(group, self.alice, "Thanks, I see it now.", "2026-09-24T16:00:00Z")
        self.capture("canvas")
        with mock.patch("communications.record.MASS", 1):
            entries = {e["text"]: e for e in self.timeline(ALICE)}
        self.assertEqual(entries["I chose the wrong pivot."]["mass"], {"students": 2})
        self.assertEqual(entries["The quiz is on Friday."]["mass"], {"students": 2})
        self.assertNotIn("mass", entries["See me after class."])
        self.assertNotIn("mass", entries["Thanks, I see it now."])  # a student's own message is never folded
        self.assertNotIn("mass", {e["text"]: e for e in self.timeline(ALICE)}["I chose the wrong pivot."])  # five or fewer: shown

    def test_canvas_runs_after_gmail_and_gradescope(self):
        order = []
        with mock.patch.object(self.record, "round", side_effect=lambda channel: order.append(channel)), \
                mock.patch.object(self.record, "due", return_value=["canvas", "gradescope", "gmail"]):
            self.record.run_due()
        self.assertEqual(order, ["gmail", "gradescope", "canvas"])

    def test_a_signed_out_session_waits_and_says_so(self):
        self.canvas.sessions.clear()
        outcome = self.capture("canvas")
        self.assertEqual(outcome["status"], "waiting")
        self.assertIn("Sign in to Canvas", outcome["detail"])


class GradescopeTests(Base):
    def setUp(self):
        super().setUp()
        self.gs = FakeGradescope().start()
        self.addCleanup(self.gs.stop)
        credentials = os.path.join(self.temp.name, "gradescope.json")
        descriptor = os.open(credentials, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"email": GS_EMAIL, "password": GS_PASSWORD}, handle)
        self.record.gradescope_settings = gw.Settings(credentials_file=credentials, base=self.gs.url, pace=0)
        self.course = self.gs.add_course("MATH 140-507", "Math 140 section 507", "Fall 2026")
        self.forbidden = self.gs.add_course("MATH 140 for Instructors", "Math 140 for Instructors", "Fall 2026")
        self.alice = self.gs.add_member(self.course, "Alice Example", ALICE, "alice@email.tamu.edu")
        self.nobody = self.gs.add_member(self.course, "Nora Nobody", "999000111", "nora@email.tamu.edu")
        exam = self.gs.add_assignment(self.course, "Exam 1", 2)
        question = self.gs.add_question(exam, 1, "Problem 3", 6, [(6, "Correct"), (0, "Incorrect")])
        self.qsub = self.gs.submissions[self.gs.add_submission(exam, [self.alice])]["questions"][question]
        self.other_qsub = self.gs.submissions[self.gs.add_submission(exam, [self.nobody])]["questions"][question]
        with self.db() as db:
            db.execute("""INSERT INTO frq_gradescope_courses(id, section_id, course_id, course_title, actor, created_at)
                          VALUES (?,?,?,?,?,?)""", (str(uuid.uuid4()), self.sections[507], str(self.course), "Math 140 section 507",
                                                  "workspace", "2026-09-25T00:00:00+00:00"))

    def test_regrade_requests_and_replies_are_captured_by_sid_and_only_read(self):
        request = self.gs.add_regrade(self.qsub, "Part (b) was correct; please look again.")
        self.gs.add_regrade(self.other_qsub, "Not on Faculty's roster.")
        outcome = self.capture("gradescope")
        self.assertEqual(outcome["status"], "ok", outcome)
        self.assertEqual(json.loads(outcome["counts_json"])["unmatched"], 1)
        entries = self.timeline(ALICE)
        self.assertEqual([(e["channel"], e["author_role"], e["text"]) for e in entries],
                         [("gradescope_regrade", "student", "Part (b) was correct; please look again.")])
        self.assertIn("Problem 3", entries[0]["subject"])
        request.update(staff_comment="Agreed, +2.", closed=True, completed_at="2026-09-30T10:00:00.000-05:00")
        self.capture("gradescope")
        entries = self.timeline(ALICE)
        self.assertEqual([(e["author_role"], e["text"], e["roles"]) for e in entries],
                         [("staff", "Agreed, +2.", ["about"]), ("student", "Part (b) was correct; please look again.", ["author"])])
        paths = [(r["method"], r["path"]) for r in self.gs.requests]
        self.assertTrue(all(method == "GET" or path == "/login" for method, path in paths))
        self.assertFalse(any("logout" in path for _, path in paths))
        self.assertFalse(any(f"/courses/{self.forbidden}" in path for _, path in paths))

    def test_listing_refuses_a_table_it_does_not_recognize(self):
        self.assertEqual(gradescope_capture.listing("<p>There are no regrade requests yet.</p>"), [])
        with self.assertRaises(gw.GradescopeError):
            gradescope_capture.listing("<table><tr><th>Who</th><th>What</th></tr><tr><td>A</td><td>B</td></tr></table>")


class TimelineTests(Base):
    def test_faculty_sends_and_card_answers_are_shown_by_reference_and_a_gmail_copy_is_shown_once(self):
        alice = self.students[ALICE]
        raw = sqlite3.connect(self.app.config["DATABASE"])
        raw.execute("PRAGMA foreign_keys=OFF")
        snapshot = json.dumps({"schema": {"title": "Exam 1 · your seat", "fields": [
            {"id": "confirm_seat", "label": "Confirm your seat", "type": "short_text"},
            {"id": "confirm_uin", "label": "Type your UIN", "type": "short_text", "verification": {"mode": "uin-digest"}}]}})
        definition = raw.execute("SELECT definition_id FROM course_terms WHERE id=?", (self.term,)).fetchone()[0]
        raw.execute("""INSERT INTO response_card_templates(id, definition_id, name, schema_json, created_at, updated_at)
                       VALUES ('template-1',?,'Seat','{}','2026-09-18T00:00:00Z','2026-09-18T00:00:00Z')""", (definition,))
        raw.execute("""INSERT INTO response_card_versions(id, template_id, number, template_revision, schema_json, created_at)
                       VALUES ('version-1','template-1',1,1,'{}','2026-09-18T00:00:00Z')""")
        raw.execute("""INSERT INTO response_card_issues(id, version_id, term_id, context_json, policy, request_id, request_hash, created_at)
                       VALUES ('issue-1','version-1',?,'{}','single','issue-request','h','2026-09-18T00:00:00Z')""", (self.term,))
        raw.execute("""INSERT INTO response_card_recipients(id, issue_id, section_id, enrollment_id, student_name, email, token, snapshot_json,
                       snapshot_hash, created_at, updated_at) VALUES ('card-1','issue-1',?,?, 'Example, Alice','alice@email.tamu.edu','tok',?,'h',
                       '2026-09-18T00:00:00Z','2026-09-18T00:00:00Z')""", (self.sections[507], alice, snapshot))
        raw.execute("""INSERT INTO response_card_mail(id, card_id, kind, recipient, subject, text_body, html_body, status, provider,
                       provider_receipt_json, created_at, updated_at) VALUES ('mail-1','card-1','invitation','alice@email.tamu.edu',
                       'Your Exam 1 seat','Hello, your seat is A5.','', 'accepted','gmail',?, '2026-09-18T10:00:00Z','2026-09-18T10:01:00Z')""",
                    (json.dumps({"mail_id": "mail-1", "status": "accepted", "message_id": "c0ffee"}),))
        raw.execute("""INSERT INTO response_card_submissions(id, card_id, request_id, submission_json, submitted_at, received_at)
                       VALUES ('sub-1','card-1','req',?, '2026-09-19T08:00:00Z','2026-09-19T08:01:00Z')""",
                    (json.dumps({"answers": {"confirm_seat": "A5", "confirm_uin": "[UIN confirmed]"}, "status": "verified"}),))
        raw.commit()
        raw.close()
        self.gmail.add("c0ffee", mail(f"Faculty <{ACCOUNT}>", "alice@email.tamu.edu", "Your Exam 1 seat", "Hello, your seat is A5."),
                       at=millis(18), labels=("SENT",))
        self.capture("gmail")
        entries = self.timeline(ALICE)
        self.assertEqual([(e["channel"], e["subject"]) for e in entries],
                         [("response_card", "Exam 1 · your seat"), ("faculty_mail", "Your Exam 1 seat")])
        self.assertEqual(entries[0]["text"], "Confirm your seat\nA5")
        self.assertNotIn("UIN", json.dumps(entries[0]))
        self.assertTrue(entries[1]["original"])  # the Gmail copy is its original, not a second entry
        self.assertEqual(entries[1]["context"]["source"], "Response Card invitation")
        # An invitation goes to everyone the card was issued to: folded once more than MASS students have one.
        self.assertNotIn("mass", entries[1])
        with mock.patch("communications.record.MASS", 0):
            self.assertEqual(self.timeline(ALICE)[1]["mass"], {"students": 1})

    def test_status_capture_now_and_unknown_students(self):
        status = self.client.get("/api/communications/status").json["channels"]
        self.assertEqual(set(status), {"gmail", "canvas", "gradescope"})
        self.assertFalse(status["gmail"]["requested"])
        answer = self.client.post("/api/communications/capture", json={"channels": ["gmail"]}, headers=HEADERS)
        self.assertTrue(answer.json["channels"]["gmail"]["requested"])
        self.assertEqual(self.record.due(datetime(2026, 9, 1, tzinfo=timezone.utc))[0], "gmail")
        self.assertEqual(self.client.post("/api/communications/capture", json={"channels": ["fax"]}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.get("/api/communications/students/0000000000").status_code, 404)
        self.assertEqual(self.client.get("/api/communications/students/not-an-id").status_code, 404)
        self.record.run_due()
        self.assertFalse(self.client.get("/api/communications/status").json["channels"]["gmail"]["requested"])

    def test_one_round_at_a_time(self):
        with self.record.lock():
            with self.assertRaises(Exception) as caught:
                with self.record.lock():
                    pass
        self.assertIn("running", str(caught.exception))

    def test_record_rows_are_append_only(self):
        self.gmail.add("a1", mail("Alice <alice@tamu.edu>", ACCOUNT, "Hi", "Hello"), at=millis(20))
        self.capture("gmail")
        with self.db() as db:
            for statement in ("UPDATE comm_revisions SET body_text='x'", "DELETE FROM comm_links", "DELETE FROM comm_messages",
                              "UPDATE comm_files SET sha256='x'", "DELETE FROM comm_bundles"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)


class MigrationTests(unittest.TestCase):
    def test_the_links_table_is_rebuilt_with_every_row_index_and_rule(self):
        from pathlib import Path
        from catalog_migrations import execute_statements
        db = sqlite3.connect(":memory:")
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript("CREATE TABLE course_terms(id TEXT PRIMARY KEY); CREATE TABLE student_enrollments(id TEXT PRIMARY KEY, term_id TEXT);"
                         "INSERT INTO course_terms VALUES('t'),('u'); INSERT INTO student_enrollments VALUES('e','t');")
        root = Path(comm_service.__file__).parent / "migrations"
        execute_statements(db, (root / "001_record.sql").read_text())
        db.execute("INSERT INTO comm_messages VALUES('m','gmail','x','','now')")
        db.execute("INSERT INTO comm_links VALUES('l1','m','e','t','author','roster_address','now')")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO comm_links VALUES('l2','m','e','t','to','canvas_notice','now')")
        execute_statements(db, (root / "002_canvas_notices.sql").read_text())
        self.assertEqual(db.execute("SELECT * FROM comm_links").fetchall(), [("l1", "m", "e", "t", "author", "roster_address", "now")])
        db.execute("INSERT INTO comm_links VALUES('l2','m','e','t','to','canvas_notice','now')")
        for statement in ("INSERT INTO comm_links VALUES('l3','m','e','t','cc','a_guess','now')",
                          "INSERT INTO comm_links VALUES('l4','m','e','u','cc','roster_address','now')",
                          "UPDATE comm_links SET role='cc'", "DELETE FROM comm_links"):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                db.execute(statement)
        self.assertEqual({row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE tbl_name='comm_links' AND type IN ('index','trigger')")},
                         {"sqlite_autoindex_comm_links_1", "sqlite_autoindex_comm_links_2", "comm_links_enrollment", "comm_links_term",
                          "comm_links_term_matches", "comm_links_immutable", "comm_links_retained"})


class MessageParsingTests(unittest.TestCase):
    def test_unknown_sender_charset_keeps_the_message_readable(self):
        """COMMUNICATIONS: malformed text metadata cannot stop other messages being captured."""
        for kind, content in (("text/plain", b"Hello"), ("text/html", b"<p>Hello</p>")):
            raw = f'From: student@example.invalid\r\nContent-Type: {kind}; charset="unknown-charset"\r\n\r\n'.encode() + content
            self.assertEqual(gmail_capture.parse(raw)["text"], "Hello")


class RawReadTests(unittest.TestCase):
    def test_gmail_reader_reads_a_whole_raw_message_through_the_shared_google_call(self):
        """COMMUNICATIONS: a raw message (up to 25 MB, base64 inside JSON) is read with room for it, over the same no-redirect call."""
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status, response.read.return_value = 200, b'{"raw": "AA"}'
        with mock.patch("google_calendar.client.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            result = gmail_capture.GmailReader(mock.Mock()).http("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/ab", None,
                                                                {"Authorization": "Bearer t"}, 30)
        self.assertEqual(result, (200, {"raw": "AA"}))
        response.read.assert_called_once_with(gmail_capture.RAW_LIMIT)
        self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 30)


class TimeTests(unittest.TestCase):
    def test_every_channel_s_instant_sorts_the_same(self):
        from communications.record import utc
        spelled = ["2026-09-15T17:23:46.646380+00:00", "2026-09-15T17:23:46Z", "2026-09-15T12:23:47-05:00", "2026-09-15T17:23:46.5Z"]
        self.assertEqual([utc(value) for value in spelled], ["2026-09-15T17:23:46.646Z", "2026-09-15T17:23:46.000Z",
                                                             "2026-09-15T17:23:47.000Z", "2026-09-15T17:23:46.500Z"])
        self.assertEqual(sorted(map(utc, spelled))[-1], "2026-09-15T17:23:47.000Z")
        self.assertEqual(utc(""), "")


class TermWindowTests(unittest.TestCase):
    def test_each_term_opens_by_its_fixed_rule(self):
        cases = {("Fall", 2026): datetime(2026, 7, 1, tzinfo=timezone.utc), ("Spring", 2027): datetime(2026, 11, 1, tzinfo=timezone.utc),
                 ("Summer I", 2027): datetime(2027, 4, 1, tzinfo=timezone.utc), ("Summer II", 2027): datetime(2027, 5, 15, tzinfo=timezone.utc)}
        for (term, year), expected in cases.items():
            self.assertEqual(comm_service.opens({"term": term, "year": year, "created_at": ""}), expected)
        other = comm_service.opens({"term": "Other", "year": 2026, "created_at": "2026-09-13T17:05:27+00:00"})
        self.assertEqual(other.date().isoformat(), "2026-05-16")


if __name__ == "__main__":
    unittest.main()
