"""Mail in Faculty (AGENTS.md "Gmail in Faculty"): reading the instructor's Gmail as plain text, a student's mail found by their
roster address, and composing and sending with a fingerprinted approval, a record written before the request leaves, and no second
send after an uncertain one. A fake Google answers everything; nothing reaches the network."""
import base64
import email
import http.client
import io
import json
import os
import socket
import sqlite3
import tempfile
import unittest
import urllib.parse
import uuid
from email import policy
from unittest import mock

from browser_course_pages import seed_course
from test_rosters import csv_bytes
from test_storage import HEADERS

from app import create_app
from communications.record import record
from response_cards.gmail import MAIL_MANAGE_SCOPES, MAIL_SCOPES, SCOPES, TOKEN_URL, USERINFO_URL
from rosters import connect

ACCOUNT = "prof@tamu.edu"


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(mid, sender, to, subject, text="", html="", labels=("INBOX",), millis="1790000000000", files=(), extra=()):
    parts = []
    if text:
        parts.append({"mimeType": "text/plain", "filename": "", "body": {"size": len(text), "data": b64(text)}})
    if html:
        parts.append({"mimeType": "text/html", "filename": "", "body": {"size": len(html), "data": b64(html)}})
    for name, size in files:
        parts.append({"partId": str(len(parts)), "mimeType": "application/pdf", "filename": name, "body": {"size": size, "attachmentId": "att"}})
    headers = [{"name": "From", "value": sender}, {"name": "To", "value": to}, {"name": "Subject", "value": subject},
               {"name": "Date", "value": "Mon, 28 Sep 2026 10:00:00 -0500"}, *({"name": k, "value": v} for k, v in extra)]
    return {"id": mid, "threadId": "", "labelIds": list(labels), "internalDate": millis, "snippet": (text or "html")[:40],
            "payload": {"mimeType": "multipart/alternative", "headers": headers, "parts": parts}}


class FakeGmail:
    def __init__(self):
        self.threads = {
            "a1": [message("m1", "Alex Student <alex@tamu.edu>", ACCOUNT, "Question about Exam 1", "Can I move my exam?\x00", labels=("INBOX", "UNREAD"),
                           extra=[("Message-ID", "<m1@mail.example>"), ("References", "<root@mail.example>")]),
                   message("m2", ACCOUNT, "alex@tamu.edu", "Re: Question about Exam 1", "Come see me.", labels=("SENT",), millis="1790000100000",
                           extra=[("Message-ID", "<m2@mail.example>")])],
            "c3": [message("m5", "Alex Student <notifications@instructure.com>", ACCOUNT, "Alex Student (26 FALL MATH 140 507: CALCULUS) just sent you a message in Canvas.",
                           "Subject: Absent\n\nI will miss class.", extra=[("Message-ID", "<m5@canvas.example>")])],
            "b2": [message("m3", "Registrar <reg@tamu.edu>", ACCOUNT, "Grades due", html="<html><style>p{}</style><p>Post <b>grades</b> &amp; sign.</p><script>alert(1)</script></html>",
                           files=[("form.pdf", 1200)], extra=[("Message-ID", "<m3@mail.example>")])],
        }
        self.calls, self.sent, self.fail, self.lose_answer, self.trashed, self.modified = [], [], {}, False, [], []
        self.attachments = {"att": b"%PDF-1.4 a registrar form\n"}

    def __call__(self, method, url, body=None, headers=None, timeout=30):
        parts = urllib.parse.urlsplit(url)
        assert parts.hostname == "gmail.googleapis.com" and headers["Authorization"].startswith("Bearer ")
        upload = parts.path.startswith("/upload/")
        path, query = parts.path.removeprefix("/upload").removeprefix("/gmail/v1/users/me"), urllib.parse.parse_qs(parts.query)
        self.calls.append((method, path, query))
        if upload:
            body = self.unpack(body, headers["Content-Type"])
        if (method, path) in self.fail:
            return self.fail.pop((method, path))
        if method == "GET" and path == "/threads":
            wanted = query.get("q", [""])[0]
            ids = [i for i, msgs in self.threads.items() if not wanted or "alex@tamu.edu" not in wanted or any("alex@tamu.edu" in str(m) for m in msgs)]
            return 200, {"threads": [{"id": i, "historyId": "h1"} for i in ids]}
        if method == "GET" and path.startswith("/threads/"):
            thread_id = path.rsplit("/", 1)[1]
            if thread_id not in self.threads:
                return 404, {"error": {"code": 404, "message": "Not found"}}
            msgs = self.threads[thread_id]
            if query["format"] == ["metadata"]:
                msgs = [{**m, "payload": {"headers": m["payload"]["headers"]}} for m in msgs]
            return 200, {"id": thread_id, "messages": msgs}
        if method == "GET" and path.startswith("/messages/") and "/attachments/" in path:
            data = self.attachments.get(path.rsplit("/", 1)[1])
            return (200, {"size": len(data), "data": base64.urlsafe_b64encode(data).decode()}) if data else (404, {"error": {"code": 404, "message": "Not found"}})
        if method == "GET" and path.startswith("/messages/"):
            found = next((m for msgs in self.threads.values() for m in msgs if m["id"] == path.rsplit("/", 1)[1]), None)
            return (200, found) if found else (404, {"error": {"code": 404, "message": "Not found"}})
        if method == "POST" and path == "/messages/send":
            if self.lose_answer:
                lost = self.lose_answer if isinstance(self.lose_answer, BaseException) else socket.timeout("timed out")
                self.lose_answer = False
                self.sent.append(body)
                raise lost
            self.sent.append(body)
            return 200, {"id": "f" + str(len(self.sent)), "threadId": body.get("threadId") or "c" + str(len(self.sent))}
        if method == "POST" and path.endswith("/modify"):
            thread_id = path.split("/")[2]
            self.modified.append((thread_id, body))
            for m in self.threads.get(thread_id, []):
                m["labelIds"] = [label for label in m.get("labelIds", []) if label not in body.get("removeLabelIds", [])] + \
                    [label for label in body.get("addLabelIds", []) if label not in m.get("labelIds", [])]
            return 200, {"id": thread_id}
        if method == "POST" and path.endswith(("/trash", "/untrash")):
            self.trashed.append(path)
            return 200, {"id": path.split("/")[2]}
        raise AssertionError((method, path))

    @staticmethod
    def unpack(body, content_type):
        """A multipart upload as Google reads it: JSON metadata, then the message (message/rfc822)."""
        assert content_type.startswith("multipart/related; boundary=")
        boundary = content_type.split("boundary=", 1)[1].encode()
        meta, message = [part for part in body.split(b"--" + boundary) if part.strip(b"\r\n-")]
        head, _, meta = meta.partition(b"\r\n\r\n")
        assert b"application/json" in head
        head, _, raw = message.partition(b"\r\n\r\n")
        assert b"message/rfc822" in head and raw.endswith(b"\r\n")
        return {**json.loads(meta), "raw": base64.urlsafe_b64encode(raw[:-2]).decode(), "upload": True}

    def parsed(self, index=-1):
        raw = base64.urlsafe_b64decode(self.sent[index]["raw"] + "==")
        return email.message_from_bytes(raw, policy=policy.default)


class FakeTokens:
    def __init__(self, scope=" ".join(sorted(set(SCOPES) | set(MAIL_SCOPES))), email=ACCOUNT):
        self.scope, self.email = scope, email

    def __call__(self, url, data=None, headers=None, timeout=30):
        if url == TOKEN_URL:
            if data.get("grant_type") == "authorization_code":
                return 200, {"access_token": "signed-in", "refresh_token": "refresh-new", "expires_in": 3600, "scope": self.scope}
            return 200, {"access_token": "refreshed", "expires_in": 3600}
        if url == USERINFO_URL:
            return 200, {"email": self.email}
        raise AssertionError(url)


def connected_gmail_app(directory):
    """The app with the instructor's Gmail connected (a client file and a token file in `directory`), for the browser scripts."""
    client_file, token_file = os.path.join(directory, "client.json"), os.path.join(directory, "token.json")
    with open(client_file, "w") as handle:
        json.dump({"web": {"client_id": "client-1.apps.googleusercontent.com", "client_secret": "secret", "project_id": "p"}}, handle)
    with open(token_file, "w") as handle:
        json.dump({"refresh_token": "r", "email": ACCOUNT, "scopes": sorted(set(SCOPES) | set(MAIL_SCOPES)), "connected_at": "2026-09-16T00:00:00+00:00"}, handle)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("FACULTY_")}
    with mock.patch.dict(os.environ, {**clean, "FACULTY_GMAIL_CLIENT_FILE": client_file, "FACULTY_GMAIL_TOKEN_FILE": token_file}, clear=True):
        return create_app(os.path.join(directory, "data"))


class Base(unittest.TestCase):
    scopes = sorted(set(SCOPES) | set(MAIL_SCOPES))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        client_file, self.token_file = os.path.join(self.temp.name, "client.json"), os.path.join(self.temp.name, "token.json")
        with open(client_file, "w") as handle:
            json.dump({"web": {"client_id": "client-1.apps.googleusercontent.com", "client_secret": "secret", "project_id": "faculty-mail-1"}}, handle)
        self.write_token(self.scopes)
        with mock.patch.dict(os.environ, {"FACULTY_GMAIL_CLIENT_FILE": client_file, "FACULTY_GMAIL_TOKEN_FILE": self.token_file}):
            self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.mail = self.app.extensions["gmail_mail"]
        self.google = FakeGmail()
        self.mail.client.http = self.google
        self.mail.google.http = FakeTokens()

    def write_token(self, scopes, email=ACCOUNT):
        with open(self.token_file, "w") as handle:
            json.dump({"refresh_token": "refresh-1", "email": email, "scopes": scopes, "connected_at": "2026-09-16T00:00:00+00:00"}, handle)

    def fields(self, **changes):
        return {"request_id": str(uuid.uuid4()), "to": "alex@tamu.edu", "subject": "Office hours", "body": "Moved to 3 pm.", **changes}

    def preview(self, **changes):
        response = self.client.post("/api/mail/preview", json=self.fields(**changes), headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def send(self, data, **changes):
        return self.client.post("/api/mail/send", json={**data, "confirmation": "send", **changes}, headers=HEADERS)

    def approved(self, **changes):
        data = self.fields(**changes)
        review = self.client.post("/api/mail/preview", json=data, headers=HEADERS)
        self.assertEqual(review.status_code, 200, review.json)
        return {**data, "fingerprint": review.json["fingerprint"]}

    def writes(self):
        return [call for call in self.google.calls if call[0] != "GET"]

    def records(self):
        with connect(self.app.config["DATABASE"]) as db:
            return ([dict(row) for row in db.execute("SELECT * FROM gmail_mail_sends")], [dict(row) for row in db.execute("SELECT * FROM gmail_mail_outcomes")])


class ConnectionTests(Base):
    def test_reading_needs_the_mail_permission_and_sending_needs_only_the_connection(self):
        self.write_token(sorted(SCOPES))  # connected for Response Card mail only
        status = self.client.get("/api/mail").json
        self.assertEqual((status["connected"], status["permission"]), (True, False))
        self.assertEqual(self.client.get("/api/mail/threads").status_code, 400)
        self.assertEqual(self.google.calls, [])
        review = self.approved()
        self.assertEqual(self.send(review).json["status"], "accepted")

    def test_connect_asks_for_read_access_for_the_same_account_and_returns_to_mail(self):
        self.write_token(sorted(SCOPES))
        url = self.client.post("/api/mail/connect", json={}, headers=HEADERS).json["url"]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", query["scope"][0].split())
        self.assertEqual(query["login_hint"], [ACCOUNT])
        with open(self.token_file) as handle:
            state = json.load(handle)["pending"]["state"]
        done = self.client.get("/api/response-cards/gmail/callback", query_string={"state": state, "code": "abc"})
        self.assertEqual((done.status_code, urllib.parse.urlsplit(done.headers["Location"]).path + "#mail"), (302, "/#mail"))
        self.assertTrue(self.client.get("/api/mail").json["permission"])

    def test_a_refused_sign_in_started_from_mail_goes_back_to_mail(self):
        # CALENDAR: the one callback returns to the page that started the sign-in, a refusal's page included.
        for manage in (False, True):
            self.write_token(sorted(SCOPES))
            self.client.post("/api/mail/connect", json={"manage": manage}, headers=HEADERS)
            self.mail.google.http = FakeTokens(email="other@gmail.com")
            with open(self.token_file) as handle:
                state = json.load(handle)["pending"]["state"]
            page = self.client.get("/api/response-cards/gmail/callback", query_string={"state": state, "code": "abc"})
            self.assertEqual(page.status_code, 400)
            self.assertIn(b'<a href="/#mail">Back to Mail</a>', page.data, manage)
            self.assertNotIn(b"/#response-cards", page.data)
            self.client.post("/api/mail/connect", json={"manage": manage}, headers=HEADERS)
            denied = self.client.get("/api/response-cards/gmail/callback", query_string={"error": "access_denied"})
            self.assertIn(b'<a href="/#mail">Back to Mail</a>', denied.data, manage)

    def test_connecting_as_another_account_is_refused(self):
        self.write_token(sorted(SCOPES))
        self.client.post("/api/mail/connect", json={}, headers=HEADERS)
        self.mail.google.http = FakeTokens(email="other@gmail.com")
        with open(self.token_file) as handle:
            state = json.load(handle)["pending"]["state"]
        page = self.client.get("/api/response-cards/gmail/callback", query_string={"state": state, "code": "abc"})
        self.assertIn(b"other@gmail.com", page.data)
        self.assertFalse(self.client.get("/api/mail").json["permission"])


class ReadingTests(Base):
    def test_snippet_entities_are_readable_without_changing_cached_mail(self):
        original = "Yes, I&#39;ll bring &lt;notes&gt; &amp; a pen for today&#x27;s quiz &mdash; thanks!"
        self.google.threads["a1"][-1]["snippet"] = original
        for _ in range(2):  # the second read uses the unchanged Gmail metadata cache
            result = self.client.get("/api/mail/threads", query_string={"tab": "all"}).json
            snippet = next(item["snippet"] for item in result["threads"] if item["id"] == "a1")
            self.assertEqual(snippet, "Yes, I'll bring <notes> & a pen for today's quiz — thanks!")
        self.assertEqual(self.google.threads["a1"][-1]["snippet"], original)
        self.assertEqual(self.writes(), [])

    def test_snippet_decoding_is_once_only_and_bounded_after_decoding(self):
        from gmail_mail.message import summary, read_thread
        one = message("m1", "Alex <alex@tamu.edu>", ACCOUNT, "Subject", "Keep &#39; literal in plain text.")
        thread = {"id": "a1", "messages": [one]}
        for raw, expected in (("&amp;#39;", "&#39;"), ("&amp;" * 301, "&" * 300),
                              ("A&#13;B\x01", "A\nB"), ("&lt;img src=x&gt;", "<img src=x>")):
            with self.subTest(raw=raw):
                one["snippet"] = raw
                self.assertEqual(summary(thread)["snippet"], expected)
        one["snippet"] = ""
        thread["snippet"] = "It&rsquo;s ready."
        self.assertEqual(summary(thread)["snippet"], "It’s ready.")
        self.assertEqual(read_thread(thread)["messages"][0]["text"], "Keep &#39; literal in plain text.")

    def test_threads_list_as_summaries_and_nothing_is_modified(self):
        result = self.client.get("/api/mail/threads", query_string={"tab": "all", "q": "exam"}).json
        first = next(item for item in result["threads"] if item["id"] == "a1")
        self.assertEqual((first["subject"], first["count"], first["unread"], first["from"]), ("Question about Exam 1", 2, True, ["Alex Student", "me"]))
        self.assertEqual(self.google.calls[0][2]["q"], ["exam"])
        self.assertEqual(self.writes(), [])

    def test_a_thread_that_has_not_changed_is_not_fetched_again(self):
        self.client.get("/api/mail/threads")
        first = len([c for c in self.google.calls if c[1].startswith("/threads/")])
        self.client.get("/api/mail/threads")
        self.assertEqual((first, len([c for c in self.google.calls if c[1].startswith("/threads/")])), (3, 3))

    def test_a_rate_limited_read_waits_and_asks_again_but_a_send_is_never_repeated(self):
        limit = (429, {"error": {"code": 429, "message": "Too many concurrent requests for user."}})
        self.google.fail[("GET", "/threads")] = limit
        with mock.patch("gmail_mail.client.time.sleep") as sleep:
            self.assertEqual(self.client.get("/api/mail/threads").status_code, 200)
        sleep.assert_called_once()
        data = self.approved()
        self.google.fail[("POST", "/messages/send")] = limit
        with mock.patch("gmail_mail.client.time.sleep") as sleep:
            result = self.send(data).json
        self.assertEqual((result["status"], sleep.called, len(self.google.sent)), ("failed", False, 0))

    def test_a_thread_is_plain_text_html_is_reduced_and_attachments_are_named_not_fetched(self):
        one = self.client.get("/api/mail/threads/a1").json
        self.assertEqual(one["messages"][0]["text"], "Can I move my exam?")  # the control character is gone
        self.assertEqual([m["mine"] for m in one["messages"]], [False, True])
        two = self.client.get("/api/mail/threads/b2").json["messages"][0]
        self.assertEqual(two["text"], "Post grades & sign.")
        self.assertNotIn("alert", json.dumps(two))
        self.assertEqual(two["files"], [{"name": "form.pdf", "type": "application/pdf", "size": 1200, "part": "1"}])
        self.assertEqual(self.writes(), [])

    def test_an_attachment_is_fetched_from_gmail_when_opened_and_never_kept(self):
        """MAIL-ATTACHMENTS-READ: the instructor clicks an attachment; it is fetched then, answered as a download, and kept nowhere."""
        self.google.threads["b2"][0]["id"] = "e3"
        opened = self.client.get("/api/mail/messages/e3/parts/1")
        self.assertEqual(opened.status_code, 200, opened.json if opened.is_json else "")
        self.assertEqual(opened.data, b"%PDF-1.4 a registrar form\n")
        self.assertEqual((opened.mimetype, opened.headers["Content-Disposition"], opened.headers["X-Content-Type-Options"]),
                         ("application/pdf", 'attachment; filename="form.pdf"', "nosniff"))
        self.assertEqual([c[1] for c in self.google.calls[-2:]], ["/messages/e3", "/messages/e3/attachments/att"])
        self.assertEqual(self.writes(), [])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM gmail_mail_sends").fetchone()[0], 0)
        before = len(self.google.calls)
        for path, status in (("/api/mail/messages/zz/parts/1", 400), ("/api/mail/messages/e3/parts/x", 400)):
            self.assertEqual(self.client.get(path).status_code, status, path)
        self.assertEqual(len(self.google.calls), before)  # IDs are checked before any URL is built
        self.assertIn("no such attachment", self.client.get("/api/mail/messages/e3/parts/7").json["error"])
        # The app's file viewer may read it (it shows pictures of it: a sender's file is never framed); other mail routes stay closed.
        from app_files import blocked
        self.assertEqual((blocked("gmail_mail.attachment"), blocked("gmail_mail.thread"), blocked("gmail_mail.sent")), (False, True, True))
        shown = self.client.get("/api/viewer/file", query_string={"route": "/api/mail/messages/e3/parts/1", "native": "1"}).json
        self.assertEqual((shown["name"], shown.get("native")), ("form.pdf", None))

    def test_a_thread_id_that_is_not_gmails_never_reaches_google(self):
        for bad in ("../../messages", "A1", "x" * 40):
            self.assertEqual(self.client.get("/api/mail/threads/" + urllib.parse.quote(bad, safe="")).status_code in (400, 404), True)
        self.assertEqual(self.google.calls, [])

    def test_gmail_errors_are_502_with_the_reason(self):
        self.google.fail[("GET", "/threads")] = (403, {"error": {"code": 403, "message": "Gmail API has not been used in project 1 before or it is disabled.",
                                                                  "errors": [{"reason": "accessNotConfigured"}]}})
        response = self.client.get("/api/mail/threads")
        self.assertEqual(response.status_code, 502)
        self.assertIn("gmail.googleapis.com", response.json["error"])
        self.google.fail[("GET", "/threads")] = (404, {})
        self.assertEqual(self.client.get("/api/mail/threads").status_code, 502)


class LargeAttachmentTests(unittest.TestCase):
    """MAIL-ATTACHMENTS-READ: Gmail sends an attachment as base64 inside JSON, about 4/3 of the file; the Mail page's read must hold
    Gmail's largest message (25 MB), not stop at the 8 MiB every other Google read keeps."""

    def serve(self, content):
        import http.server
        import threading
        encoded = base64.urlsafe_b64encode(content).decode()
        message = {"id": "e3", "payload": {"partId": "", "parts": [{"partId": "1", "filename": "scan.pdf", "mimeType": "application/pdf",
                                                                     "body": {"attachmentId": "att", "size": len(content)}}]}}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"size": len(content), "data": encoded} if "/attachments/" in self.path else message).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_an_attachment_over_six_megabytes_opens(self):
        from gmail_mail import client as gmail_client
        content = os.urandom(7_000_000)  # 9.3 MB as base64: past 8 MiB, inside Gmail's 25 MB
        port = self.serve(content)
        connection = mock.Mock(project="p")
        connection.access_token.return_value = "token"
        with mock.patch.object(gmail_client, "API", f"http://127.0.0.1:{port}/gmail/v1/users/me"), mock.patch.object(gmail_client, "HOST", "127.0.0.1"):
            name, kind, opened = gmail_client.GmailMailClient(connection).attachment("e3", "1")
        self.assertEqual((name, kind, opened == content), ("scan.pdf", "application/pdf", True))


class StudentTests(Base):
    def setUp(self):
        super().setUp()
        roster = {"section_id": "sec-1", "section": 507, "students": [
            {"id": "e1", "fields": {"Name": "Student, Alex", "Email": "alex@tamu.edu"}},
            {"id": "e2", "fields": {"Name": "Student, Bo"}},
            {"id": "e3", "fields": {"Name": "Student, Cy", "Email": "ALEX@tamu.edu"}},
            {"id": "e4", "fields": {"Name": "Student, Di", "Email": "not an address"}},
            {"id": "e5", "fields": {"Name": "Student, Ed", "Email": "ed@tamu.edu"}, "dropped": {"at": "x"}}]}
        self.mail.tas = mock.Mock(section=lambda section_id: {"contacts": [{"name": "Tia TA", "email": "tia@tamu.edu"}, {"name": "No Mail", "email": ""}]})
        patcher = mock.patch.object(self.mail, "_roster", return_value=roster)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_message_from_a_students_roster_address_names_them_and_their_photo(self):
        self.mail.photos = mock.Mock(latest=lambda term_id: {"e1": "abcdef012345"})
        self.mail._roster.return_value["term_id"] = "t1"
        self.mail._roster.return_value["students"] = [s for s in self.mail._roster.return_value["students"] if s["id"] != "e3"]  # e3 shares Alex's address
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            first, second = self.client.get("/api/mail/threads/a1").json["messages"]
        self.assertEqual(first["student"], {"name": "Student, Alex", "section": 507, "code": "MATH 140", "enrollment_id": "e1",
                                            "term_id": "t1", "section_id": "sec-1", "dropped": False, "photo": "abcdef012345"})
        self.assertNotIn("student", second)  # the instructor's own message names no student

    def test_the_roster_form_of_a_tamu_address_and_the_form_students_write_from_are_one_mailbox(self):
        from gmail_mail.service import variants
        self.assertEqual(variants("Alex@email.tamu.edu"), ["alex@email.tamu.edu", "alex@tamu.edu"])
        self.assertEqual(variants("alex@tamu.edu"), variants("ALEX@EMAIL.TAMU.EDU"))
        self.assertEqual(variants("alex@gmail.com"), ["alex@gmail.com"])
        self.assertEqual(variants("alex@lists.tamu.edu"), ["alex@lists.tamu.edu"])  # another domain of TAMU's is not the student's mailbox
        self.mail.photos = mock.Mock(latest=lambda term_id: {})
        roster = self.mail._roster.return_value
        roster["students"] = [{"id": "e1", "fields": {"Name": "Student, Alex", "Email": "alex@email.tamu.edu"}}]
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            first, _ = self.client.get("/api/mail/threads/a1").json["messages"]  # the fixture's message is from alex@tamu.edu
        self.assertEqual(first["student"]["name"], "Student, Alex")
        found = self.client.get("/api/mail/student", query_string={"group": "g", "section": 507, "student": "e1"})
        self.assertEqual(found.status_code, 200)
        self.assertIn("from:alex@tamu.edu", self.google.calls[-2][2]["q"][0] if self.google.calls[-1][1].startswith("/threads/") else self.google.calls[0][2]["q"][0])

    def canvas(self, roster, subject="Alex Student (26 FALL MATH 140 507: CALCULUS) just sent you a message in Canvas.", sender="Alex Student <notifications@instructure.com>"):
        self.google.threads["c3"][0]["payload"]["headers"] = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}, {"name": "To", "value": ACCOUNT}]
        self.mail.photos = mock.Mock(latest=lambda term_id: {"e1": "abcdef012345"})
        self.mail._roster.return_value.update(students=roster, term_id="t1")
        self.mail._index = (0.0, {}, [])
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            return self.client.get("/api/mail/threads/c3").json["messages"][0]

    def test_a_canvas_message_is_answered_by_email_at_the_students_roster_address(self):
        """MAIL-CANVAS-REPLY: never through Canvas; the subject the student gave in Canvas; an older tab's Canvas address replaced."""
        canvas_reply = "reply+a1b2c3d4e5f60718-10000~100000001-1000000002@notifications.canvaslms.com"
        roster = [{"id": "e1", "fields": {"Name": "Student, Alex", "Email": "alex@email.tamu.edu"}}, {"id": "e2", "fields": {"Name": "Other, Bo"}}]
        self.assertEqual(self.canvas(roster)["reply_to"], [{"name": "Student, Alex", "email": "alex@email.tamu.edu"}])
        self.google.threads["c3"][0]["payload"]["headers"].append({"name": "Reply-To", "value": f"Alex Student via Canvas Notifications <{canvas_reply}>"})
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            read = self.client.get("/api/mail/threads/c3").json
            self.assertEqual((read["messages"][0]["reply_to"], read["reply_subject"]),
                             ([{"name": "Student, Alex", "email": "alex@email.tamu.edu"}], "Re: Absent"))  # never Canvas's Reply-To
            for to in ("", "notifications@instructure.com", canvas_reply, f"{canvas_reply}, ta@tamu.edu"):
                review = self.client.post("/api/mail/preview", json=self.fields(to=to, subject="Re: Absent", reply_thread_id="c3"), headers=HEADERS)
                self.assertEqual(review.status_code, 200, review.json)
                self.assertEqual(review.json["message"]["to"], ["alex@email.tamu.edu"] + (["ta@tamu.edu"] if "ta@" in to else []), to)
                self.assertTrue(any("by email (alex@email.tamu.edu), not in Canvas" in n for n in review.json["notes"]), to)
            result = self.send(self.approved(to="notifications@instructure.com", subject="Re: Absent", reply_thread_id="c3"))
            self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.google.parsed()["To"], "alex@email.tamu.edu")
        self.assertEqual(self.google.sent[-1]["threadId"], "c3")

    def test_a_canvas_message_from_nobody_on_the_rosters_is_not_answered_through_canvas(self):
        message = self.canvas([{"id": "e2", "fields": {"Name": "Other, Bo", "Email": "bo@tamu.edu"}}])
        self.assertNotIn("reply_to", message)
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            for to in ("", "notifications@instructure.com"):
                refused = self.client.post("/api/mail/preview", json=self.fields(to=to, reply_thread_id="c3"), headers=HEADERS)
                self.assertEqual(refused.status_code, 400, to)
                self.assertIn("Write the student's email address in To", refused.json["error"])
            written = self.client.post("/api/mail/preview", json=self.fields(to="alex@tamu.edu", reply_thread_id="c3"), headers=HEADERS)
        self.assertEqual((written.status_code, written.json["message"]["to"]), (200, ["alex@tamu.edu"]))
        self.assertEqual(self.google.sent, [])

    def canvas_world(self):
        """MAIL-CANVAS-POST: a loopback FakeCanvas with a kept session, Alex's conversation in it, and c3 the notification linking to it."""
        from fake_canvas import FakeCanvas, now as fake_now
        from canvas import web
        fake = FakeCanvas().start()
        self.addCleanup(fake.stop)
        settings = web.Settings(session_file=os.path.join(self.temp.name, "canvas-session.json"), base=fake.url)
        web.save(settings, fake.session(), user={"name": "Pat Instructor"})
        self.mail.canvas = mock.Mock(settings=settings, client=lambda: web.Client(settings))
        course = fake.add_course("MATH-140-507", "MATH-140-507", "Fall 2026")
        self.alex_canvas = fake.add_student(course, fake.add_section(course, "507"), "Alex Student", "001234567")
        self.conversation = fake.add_conversation(course, "Absent", [self.alex_canvas])
        fake.add_message(self.conversation, self.alex_canvas, "I will miss class.", fake_now())
        self.google.threads["c3"] = [message(
            "m5", "Alex Student <notifications@instructure.com>", ACCOUNT, "Alex Student (26 FALL MATH 140 507: CALCULUS) just sent you a message in Canvas.",
            f"  Subject: Absent\n\nI will miss class.\n\n  You can reply to this message in Canvas by replying directly to this email, or by clicking "
            f"this link: {fake.url}/conversations/{self.conversation} .", extra=[("Message-ID", "<m5@canvas.example>")])]
        self.mail._roster.return_value.update(students=[{"id": "e1", "fields": {"Name": "Student, Alex", "Email": "alex@email.tamu.edu"}}], term_id="t1")
        self.mail._index = (0.0, {}, [])
        self.mail.photos = mock.Mock(latest=lambda term_id: {})
        patcher = mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}])
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake, settings

    def test_a_reply_to_a_canvas_message_is_posted_in_its_canvas_conversation(self):
        fake, _ = self.canvas_world()
        for to in ("", "notifications@instructure.com", "alex@email.tamu.edu"):  # the page's prefill, an older tab's, the roster address
            review = self.client.post("/api/mail/preview", json=self.fields(to=to, subject="Re: Absent", reply_thread_id="c3", body="See you Friday."), headers=HEADERS)
            self.assertEqual(review.status_code, 200, review.json)
            message = review.json["message"]
            self.assertEqual((message["via"], message["to"], message["canvas"]["recipient_name"], message["canvas"]["conversation_id"], review.json["recipients"]),
                             ("canvas", [], "Alex Student", self.conversation, 1), to)
            self.assertTrue(any("posted in Canvas" in note for note in review.json["notes"]))
            self.assertNotIn("canvas_sign_in", review.json)
        data = self.approved(to="", subject="Re: Absent", reply_thread_id="c3", body="See you Friday.")
        result = self.send(data)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual((result.json["status"], result.json["via"], result.json["canvas"]["recipient_name"]), ("accepted", "canvas", "Alex Student"))
        self.assertEqual(fake.added_messages, [{"conversation": self.conversation, "body": "See you Friday.", "recipients": [self.alex_canvas]}])
        self.assertEqual(self.google.sent, [])  # nothing through Gmail
        self.assertNotIn(self.conversation, fake.marked_read)  # Faculty never marks Canvas conversations read
        self.assertEqual(self.send(data).json["id"], result.json["id"])  # the same request is never posted twice
        self.assertEqual(len(fake.added_messages), 1)
        with connect(self.app.config["DATABASE"]) as db:
            post = dict(db.execute("SELECT * FROM gmail_mail_canvas_posts").fetchone())
            outcome = dict(db.execute("SELECT * FROM gmail_mail_outcomes").fetchone())
            sent = dict(db.execute("SELECT * FROM gmail_mail_sends").fetchone())
        self.assertEqual((post["conversation_id"], post["recipient_user_id"], post["recipient_name"]), (self.conversation, self.alex_canvas, "Alex Student"))
        self.assertEqual((outcome["status"], outcome["message_id"], outcome["thread_id"]), ("accepted", "", self.conversation))
        self.assertEqual((sent["to_json"], sent["reply_thread_id"], sent["body_text"]), ("[]", "c3", "See you Friday."))
        # The thread shows the post as the instructor's, and the Students list counts the thread as answered.
        shown = self.client.get("/api/mail/threads/c3").json["messages"]
        self.assertEqual(([m["from"][0]["name"] for m in shown], shown[-1]["text"], shown[-1]["mine"]), (["Alex Student", "You, in Canvas"], "See you Friday.", True))
        self.assertEqual(self.client.get("/api/mail/threads", query_string={"tab": "students"}).json["threads"], [])
        self.assertEqual(self.client.get("/api/mail/sent").json["sent"][0]["via"], "canvas")

    def test_canvas_post_intent_failure_clears_its_restart_registration(self):
        """CANVAS A STOP WAITS: a rolled-back intent is not an active outside write."""
        from canvas.stopping import Writes
        fake, _ = self.canvas_world()
        self.mail.canvas.writes = Writes()
        data = self.approved(to="", reply_thread_id="c3")
        first = self.send(data).json
        planned, _, _ = self.mail._plan(data)
        with mock.patch("gmail_mail.service.uuid.uuid4", return_value=uuid.UUID(first["id"])):
            with self.assertRaises(sqlite3.IntegrityError):
                self.mail._post("different-request", "different-fingerprint", planned, "workspace")
        self.assertEqual(self.mail.canvas.writes.busy(), [])
        self.assertEqual(len(fake.added_messages), 1)

    def test_a_canvas_reply_that_cannot_be_posted_goes_by_email_and_says_why(self):
        from canvas import web
        from fake_canvas import now as fake_now
        fake, settings = self.canvas_world()
        for changes, why in (({"to": "", "via": "email"}, ""), ({"to": "alex@email.tamu.edu", "cc": "ta@tamu.edu"}, "more people than the student")):
            review = self.client.post("/api/mail/preview", json=self.fields(reply_thread_id="c3", **changes), headers=HEADERS)
            self.assertEqual((review.json["message"].get("via"), review.json["message"]["to"]), (None, ["alex@email.tamu.edu"]), changes)
            self.assertTrue(any(why in note for note in review.json["notes"]), (changes, review.json["notes"]))
        # Canvas refusing the post is failed; no answer is uncertain; neither is ever posted again by itself.
        for answered, status in (((400, [{"attribute": "body", "message": "blank"}]), "failed"), ((503, {"errors": []}), "uncertain")):
            data = self.approved(to="", reply_thread_id="c3")
            fake.message_answer = answered
            result = self.send(data).json
            fake.message_answer = None
            self.assertEqual((result["status"], result["via"]), (status, "canvas"))
        self.assertEqual(len(fake.added_messages), 2)
        # A new message from the student after the review makes it stale.
        data = self.approved(to="", reply_thread_id="c3")
        fake.add_message(self.conversation, self.alex_canvas, "Also, one more thing.", fake_now())
        self.assertEqual(self.send(data).status_code, 409)
        # Signed out of Canvas: the review says so and offers email.
        web.forget(settings)
        review = self.client.post("/api/mail/preview", json=self.fields(to="", reply_thread_id="c3"), headers=HEADERS).json
        self.assertEqual(review["message"]["to"], ["alex@email.tamu.edu"])
        self.assertTrue(any("signed out of Canvas" in note for note in review["notes"]), review["notes"])
        self.assertIs(review["canvas_sign_in"], True)  # the review offers Sign in to Canvas, only then
        # Signed in again (the sign-in window keeps a new session): the same reply is posted in Canvas once more.
        web.save(settings, fake.session(), user={"name": "Pat Instructor"})
        review = self.client.post("/api/mail/preview", json=self.fields(to="", reply_thread_id="c3"), headers=HEADERS).json
        self.assertEqual((review["message"]["via"], "canvas_sign_in" in review), ("canvas", False))

    def test_a_canvas_message_names_the_student_by_display_name_and_the_section_in_the_subject(self):
        roster = [{"id": "e1", "fields": {"Name": "Student, Alex Middle", "Email": "alex@email.tamu.edu"}}, {"id": "e2", "fields": {"Name": "Other, Bo"}}]
        message = self.canvas(roster)
        self.assertEqual((message["student"]["name"], message["student"]["via"], message["student"]["photo"]), ("Student, Alex Middle", "Canvas", "abcdef012345"))
        self.assertNotIn("student", self.canvas(roster, subject="Alex Student (26 FALL MATH 140 999: CALCULUS) just sent you a message in Canvas."))  # not in that section
        self.assertNotIn("student", self.canvas(roster, sender="Alex <notifications@instructure.com>"))  # one word names nobody
        self.assertNotIn("student", self.canvas(roster, sender="Alex Student <someone@example.com>"))  # not Canvas
        twins = roster + [{"id": "e3", "fields": {"Name": "Student, Alex Twin"}}]
        self.assertNotIn("student", self.canvas(twins))  # two students carry that name

    def test_the_students_tab_lists_only_inbox_threads_from_students_and_their_canvas_messages(self):
        roster = self.mail._roster.return_value
        roster["students"] = [{"id": "e1", "fields": {"Name": "Student, Alex Middle", "Email": "alex@email.tamu.edu"}}]
        self.mail._index = (0.0, {}, [])
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            result = self.client.get("/api/mail/threads", query_string={"tab": "students"}).json
        self.assertEqual({item["id"] for item in result["threads"]}, {"c3"})  # the registrar's message is no student's; a1 ends with the instructor's reply
        by_id = {item["id"]: item["student"] for item in result["threads"]}
        self.google.threads["a1"].append(message("m6", "Alex Student <alex@tamu.edu>", ACCOUNT, "Re: Question about Exam 1", "Thanks!", millis="1790000200000"))
        self.mail.client._summaries.clear()  # the fake reports one history ID; Gmail's changes with the new message
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            again = self.client.get("/api/mail/threads", query_string={"tab": "students"}).json
        self.assertEqual({item["id"] for item in again["threads"]}, {"a1", "c3"})  # the student wrote last: it is theirs to answer again
        self.assertEqual((again["threads"][0]["student"]["name"] if again["threads"][0]["id"] == "a1" else again["threads"][1]["student"]["name"]), "Student, Alex Middle")
        self.assertEqual((by_id["c3"]["section"], by_id["c3"]["via"]), (507, "Canvas"))
        query = self.google.calls[0][2]["q"][0]
        self.assertTrue(query.startswith("in:inbox (from:(@"), query)
        self.assertIn("subject:\"sent you a message in Canvas\"", query)
        self.assertEqual(self.client.get("/api/mail/threads", query_string={"tab": "unread"}).status_code, 400)

    def test_mail_a_follow_up_has_settled_leaves_students_and_the_scan_goes_on(self):
        # MAIL-FOLLOW-UP (requirement, 2026-10-10).
        self.assertIs(self.mail.followups, self.app.extensions["student_support"])  # the app gives Mail the follow-up service
        self.mail._roster.return_value["students"] = [{"id": "e1", "fields": {"Name": "Student, Alex Middle", "Email": "alex@email.tamu.edu"}}]
        self.mail._index = (0.0, {}, [])
        self.google.threads["a1"].append(message("m6", "Alex Student <alex@tamu.edu>", ACCOUNT, "Re: Question about Exam 1", "Thanks!", millis="1790000200000"))
        asked = []
        self.mail.followups = mock.Mock(settled_mail=lambda pairs: asked.append(sorted(pairs)) or {"m5"})
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            listed = self.client.get("/api/mail/threads", query_string={"tab": "students"}).json
            self.assertEqual([item["id"] for item in listed["threads"]], ["a1"])  # c3, the Canvas notice, is its follow-up's now
            self.assertEqual(listed["threads"][0]["student"], {"name": "Student, Alex Middle", "section": 507, "code": "MATH 140", "dropped": False})
            # Asked once a Gmail page, about the student's message that ends each thread; other mailboxes never ask.
            self.assertEqual(asked, [[("e1", "m5"), ("e1", "m6")]])
            self.assertIn("c3", {item["id"] for item in self.client.get("/api/mail/threads", query_string={"tab": "all"}).json["threads"]})
            self.assertEqual(len(asked), 1)
            # A settled thread does not count toward a page of students: the scan reads on, and a page without students asks nothing.
            first = [{"id": "c3", "messages": self.google.threads["c3"]}, {"id": "b2", "messages": self.google.threads["b2"]}]
            with mock.patch.object(self.mail.client, "threads", side_effect=[(first, "next1"), ([{"id": "b2", "messages": self.google.threads["b2"]}], "next2"),
                                                                             ([{"id": "a1", "messages": self.google.threads["a1"]}], "")]) as pages:
                scanned = self.mail.threads("students")
            self.assertEqual((pages.call_count, [item["id"] for item in scanned["threads"]], len(asked)), (3, ["a1"], 3))
        self.assertEqual(self.writes(), [])  # Gmail itself is not touched

    def test_a_rate_limited_scan_shows_what_it_found_and_continues_from_the_page_that_failed(self):
        from gmail_mail.client import MailError
        self.mail._roster.return_value["students"] = [{"id": "e1", "fields": {"Name": "Student, Alex Middle", "Email": "alex@email.tamu.edu"}}]
        self.mail._index = (0.0, {}, [])
        page = [{"id": "a1", "messages": self.google.threads["a1"][:1]}]  # the student's message, not yet answered
        slow = MailError("Gmail asked Faculty to slow down; try again in a few minutes.", 429)
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]), \
                mock.patch.object(self.mail.client, "threads", side_effect=[(page, "next1"), slow]):
            result = self.mail.threads("students")
        self.assertEqual(([t["id"] for t in result["threads"]], result["next"]), (["a1"], "next1"))
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]), \
                mock.patch.object(self.mail.client, "threads", side_effect=slow):
            with self.assertRaises(MailError):
                self.mail.threads("students")  # nothing found yet: the error is shown, and the threads read are remembered for the retry

    def test_a_stepped_students_scan_reads_one_page_at_a_time_and_names_the_scans_limits(self):
        from gmail_mail.service import STUDENT_SCAN, STUDENT_WANT
        self.mail._roster.return_value["students"] = [{"id": "e1", "fields": {"Name": "Student, Alex Middle", "Email": "alex@email.tamu.edu"}}]
        self.mail._index = (0.0, {}, [])
        page = [{"id": "a1", "messages": self.google.threads["a1"][:1]}]  # the student's message, not yet answered
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]), \
                mock.patch.object(self.mail.client, "threads", side_effect=[(page, "next1"), ([], "")]) as listed:
            stepped = self.client.get("/api/mail/threads", query_string={"tab": "students", "step": "1"}).json
            self.assertEqual(listed.call_count, 1)  # one page only, though it found fewer than STUDENT_WANT
            whole = self.client.get("/api/mail/threads", query_string={"tab": "students"}).json
        self.assertEqual(([t["id"] for t in stepped["threads"]], stepped["next"], stepped["scan"]), (["a1"], "next1", {"want": STUDENT_WANT, "pages": STUDENT_SCAN}))
        self.assertEqual((whole["next"], "scan" in whole), ("", False))  # the default request still scans on by itself, unchanged

    def test_a_students_mail_search_includes_canvas_notifications_by_name(self):
        self.mail._roster.return_value["students"] = [{"id": "e1", "fields": {"Name": "Student, Alex Middle", "Email": "alex@email.tamu.edu"}}]
        self.client.get("/api/mail/student", query_string={"group": "g", "section": 507, "student": "e1"})
        self.assertIn("from:instructure.com subject:(student alex)", self.google.calls[0][2]["q"][0])

    def test_an_address_two_students_share_names_nobody(self):
        self.mail.photos = mock.Mock(latest=lambda term_id: {})
        with mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            self.mail._roster.return_value["students"].append({"id": "e9", "fields": {"Name": "Copy, Cat", "Email": "alex@tamu.edu"}})
            first, _ = self.client.get("/api/mail/threads/a1").json["messages"]
        self.assertNotIn("student", first)

    def test_a_students_mail_is_found_by_their_roster_address(self):
        found = self.client.get("/api/mail/student", query_string={"group": "g", "section": 507, "student": "e1"}).json
        self.assertEqual((found["email"], [t["id"] for t in found["threads"]]), ("alex@tamu.edu", ["a1"]))
        self.assertIn("from:alex@tamu.edu", self.google.calls[0][2]["q"][0])
        self.assertEqual(self.writes(), [])

    def test_a_student_without_a_usable_address_has_no_mail_and_nothing_is_guessed(self):
        for student in ("e2", "e4"):
            found = self.client.get("/api/mail/student", query_string={"group": "g", "section": 507, "student": student}).json
            self.assertEqual((found["threads"], found["email"]), ([], ""))
            self.assertIn("no usable email address", found["reason"])
        self.assertEqual(self.google.calls, [])
        self.assertEqual(self.client.get("/api/mail/student", query_string={"group": "g", "section": 507, "student": "zz"}).status_code, 404)

    def test_an_audience_lists_names_and_addresses_only_and_says_who_it_skipped(self):
        audience = self.client.get("/api/mail/audience", query_string={"group": "g", "section": 507}).json
        self.assertEqual(audience["students"], [{"name": "Student, Alex", "email": "alex@tamu.edu"}])
        self.assertEqual({item["name"]: item["reason"] for item in audience["skipped"]},
                         {"Student, Bo": "no email address on the roster", "Student, Cy": "shares an address with Student, Alex",
                          "Student, Di": "the roster address is not a valid email address"})
        self.assertEqual(audience["tas"], [{"name": "Tia TA", "email": "tia@tamu.edu"}])
        self.assertNotIn("uin", json.dumps(audience).lower())


class FollowUpTests(Base):
    """MAIL-FOLLOW-UP with the real roster, record and follow-up service: a thread leaves Students while the follow-up its latest
    message is part of shows anything but Needs my reply."""

    def setUp(self):
        super().setUp()
        seed_course(self.client, sections=[507, 508])
        group = self.client.get("/api/state").json["course_groups"][0]["id"]
        added = self.client.post(f"/api/course-groups/{group}/sections/507/roster", data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS)
        self.assertEqual(added.status_code, 201, added.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.alice = db.execute("SELECT id FROM student_enrollments WHERE uin='001234567'").fetchone()[0]
            self.term = db.execute("SELECT term_id FROM course_sections LIMIT 1").fetchone()[0]
        self.google.threads["f1"] = [message("s1", "Alice Example <alice@example.test>", ACCOUNT, "Missed quiz", "I was sick on Tuesday.")]

    def captured(self, ident, at):
        """The communications record's copy of Alice's Gmail message `ident` in thread f1, as a capture round keeps it."""
        with connect(self.app.config["DATABASE"]) as db:
            record(db, "gmail", ident, {"sent_at": at, "author_role": "student", "author": {"name": "Alice"}, "recipients": [], "subject": "Missed quiz",
                   "body_text": "I was sick on Tuesday.", "attachments": [], "context": {"thread_id": "f1"}},
                   [(self.alice, self.term, "author", "roster_address")], "f1")
            db.commit()

    def students(self):
        self.mail.client._summaries.clear()  # the fake reports one history ID; Gmail's changes with a new message
        response = self.client.get("/api/mail/threads", query_string={"tab": "students"})
        self.assertEqual(response.status_code, 200, response.json)
        return [item["id"] for item in response.json["threads"]]

    def followup(self):
        return self.client.get("/api/student-support", query_string={"kind": "followup"}).json["items"][0]

    def test_a_thread_leaves_students_while_its_follow_up_does_not_wait_for_a_reply(self):
        self.assertEqual(self.students(), ["f1"])  # not captured yet: nothing but Gmail knows it
        self.captured("s1", "2026-10-01T12:00:00Z")
        suggestion = self.followup()
        self.assertEqual((suggestion["id"], suggestion["state"]), ("", "needs_reply"))
        self.assertEqual(self.students(), ["f1"])  # a conversation no follow-up tracks stays the instructor's to answer
        saved = self.client.post("/api/student-support", json={**suggestion, "state": "resolved", "request_id": "resolved"}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.assertEqual(self.students(), [])
        # It is only in All mail, as a thread the instructor answered is, and still opens; Gmail was never changed.
        self.assertIn("f1", {item["id"] for item in self.client.get("/api/mail/threads", query_string={"tab": "all"}).json["threads"]})
        self.assertEqual(self.client.get("/api/mail/threads/f1").json["inbox"], True)
        self.assertEqual(self.writes(), [])
        # Alice writes again. Until the record has that message the follow-up has not seen it, so it is never hidden ...
        self.google.threads["f1"].append(message("s2", "Alice Example <alice@example.test>", ACCOUNT, "Re: Missed quiz", "Can I make it up?", millis="1790000300000"))
        self.assertEqual(self.students(), ["f1"])
        # ... and once captured it reopens the follow-up as Needs my reply, so it stays until that is settled again.
        self.captured("s2", "2026-10-01T15:00:00Z")
        reopened = self.followup()
        self.assertEqual((reopened["state"], reopened["new_reply"]), ("needs_reply", True))
        self.assertEqual(self.students(), ["f1"])
        waiting = self.client.post("/api/student-support/" + reopened["id"], json={**reopened, "state": "waiting", "base_revision": reopened["revision"],
                                   "request_id": "waiting"}, headers=HEADERS)
        self.assertEqual(waiting.status_code, 200, waiting.json)
        self.assertEqual(self.students(), [])


class TrashTests(Base):
    def trash(self, thread="a1", **changes):
        return self.client.post(f"/api/mail/threads/{thread}/trash", json={"request_id": str(uuid.uuid4()), "confirmation": "trash", **changes}, headers=HEADERS)

    def test_trash_needs_the_wider_permission_and_asks_for_it_only_when_chosen(self):
        self.assertFalse(self.client.get("/api/mail").json["can_trash"])
        response = self.trash()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Allow", response.json["error"])
        self.assertEqual(self.google.trashed, [])
        plain = urllib.parse.parse_qs(urllib.parse.urlsplit(self.client.post("/api/mail/connect", json={}, headers=HEADERS).json["url"]).query)["scope"][0]
        self.assertNotIn("gmail.modify", plain)
        wide = urllib.parse.parse_qs(urllib.parse.urlsplit(self.client.post("/api/mail/connect", json={"manage": True}, headers=HEADERS).json["url"]).query)["scope"][0]
        self.assertIn("https://www.googleapis.com/auth/gmail.modify", wide.split())

    def test_a_confirmed_trash_moves_the_thread_and_records_only_its_id(self):
        self.write_token(sorted(set(self.scopes) | set(MAIL_MANAGE_SCOPES)))
        self.assertTrue(self.client.get("/api/mail").json["can_trash"])
        self.assertEqual(self.trash(confirmation="").status_code, 400)
        self.assertEqual(self.google.trashed, [])
        request_id = str(uuid.uuid4())
        first = self.trash(request_id=request_id).json
        again = self.trash(request_id=request_id).json
        self.assertEqual((first["status"], first["action"], again["id"], self.google.trashed), ("done", "trash", first["id"], ["/threads/a1/trash"]))
        undone = self.client.post("/api/mail/threads/a1/untrash", json={"request_id": str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual((undone.status_code, self.google.trashed[-1]), (200, "/threads/a1/untrash"))
        with connect(self.app.config["DATABASE"]) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM gmail_mail_trash ORDER BY rowid")]
            self.assertEqual([(r["thread_id"], r["action"], r["status"]) for r in rows], [("a1", "trash", "done"), ("a1", "untrash", "done")])
            self.assertNotIn("Exam", json.dumps(rows))
            with self.assertRaises(Exception):
                db.execute("DELETE FROM gmail_mail_trash")
        self.assertEqual(self.trash(request_id=request_id).status_code, 200)  # still the same record, nothing called
        self.assertEqual(len(self.google.trashed), 2)

    def test_a_refusal_is_a_502_and_the_same_request_is_not_silently_done(self):
        self.write_token(sorted(set(self.scopes) | set(MAIL_MANAGE_SCOPES)))
        self.google.fail[("POST", "/threads/a1/trash")] = (404, {})
        request_id = str(uuid.uuid4())
        self.assertEqual(self.trash(request_id=request_id).status_code, 502)
        self.assertEqual(self.trash(request_id=request_id).status_code, 502)
        self.assertEqual(self.google.trashed, [])
        self.assertEqual(self.trash("../x").status_code in (400, 404), True)

    def test_a_trashed_thread_leaves_the_cache(self):
        self.write_token(sorted(set(self.scopes) | set(MAIL_MANAGE_SCOPES)))
        self.client.get("/api/mail/threads")
        self.assertTrue(any(key[0] == "a1" for key in self.mail.client._summaries))
        self.trash()
        self.assertFalse(any(key[0] == "a1" for key in self.mail.client._summaries))


class ArchiveTests(Base):
    def archive(self, thread="a1", undo=False, **changes):
        return self.client.post(f"/api/mail/threads/{thread}/{'unarchive' if undo else 'archive'}", json={"request_id": str(uuid.uuid4()), **changes}, headers=HEADERS)

    def test_archive_needs_the_wider_permission(self):
        response = self.archive()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Allow", response.json["error"])
        self.assertEqual(self.google.modified, [])

    def test_archive_takes_off_only_the_inbox_label_and_undo_puts_it_back(self):
        self.write_token(sorted(set(self.scopes) | set(MAIL_MANAGE_SCOPES)))
        self.assertTrue(self.client.get("/api/mail/threads/a1").json["inbox"])
        request_id = str(uuid.uuid4())
        first = self.archive(request_id=request_id)  # one click: no confirmation is asked for
        self.assertEqual((first.status_code, first.json["action"], first.json["status"]), (200, "archive", "done"))
        self.assertEqual(self.google.modified, [("a1", {"removeLabelIds": ["INBOX"]})])
        opened = self.client.get("/api/mail/threads/a1").json
        self.assertFalse(opened["inbox"])
        self.assertEqual(self.google.threads["a1"][0]["labelIds"], ["UNREAD"])  # unread stays unread: no other label is touched
        self.assertEqual(self.archive(request_id=request_id).json["id"], first.json["id"])  # the same request calls nothing again
        self.assertEqual(len(self.google.modified), 1)
        self.assertEqual(self.archive(undo=True, request_id=request_id).status_code, 409)  # nor serves another action
        back = self.archive(undo=True)
        self.assertEqual((back.status_code, back.json["action"], self.google.modified[-1]), (200, "unarchive", ("a1", {"addLabelIds": ["INBOX"]})))
        self.assertTrue(self.client.get("/api/mail/threads/a1").json["inbox"])
        self.assertEqual(self.google.trashed, [])
        with connect(self.app.config["DATABASE"]) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM gmail_mail_archive ORDER BY rowid")]
            self.assertEqual([(r["thread_id"], r["action"], r["status"]) for r in rows], [("a1", "archive", "done"), ("a1", "unarchive", "done")])
            self.assertNotIn("Exam", json.dumps(rows))
            with self.assertRaises(Exception):
                db.execute("DELETE FROM gmail_mail_archive")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gmail_mail_trash").fetchone()[0], 0)

    def test_a_refusal_is_a_502_recorded_and_a_bad_thread_never_reaches_google(self):
        self.write_token(sorted(set(self.scopes) | set(MAIL_MANAGE_SCOPES)))
        self.google.fail[("POST", "/threads/a1/modify")] = (404, {})
        request_id = str(uuid.uuid4())
        self.assertEqual(self.archive(request_id=request_id).status_code, 502)
        self.assertEqual(self.archive(request_id=request_id).status_code, 502)  # the failure is returned, not tried again
        self.assertEqual(self.google.modified, [])
        self.assertEqual(self.archive("../x").status_code in (400, 404), True)
        self.assertEqual(self.archive("A1").status_code, 400)

    def test_an_archived_thread_leaves_the_cache(self):
        self.write_token(sorted(set(self.scopes) | set(MAIL_MANAGE_SCOPES)))
        self.client.get("/api/mail/threads")
        self.assertTrue(any(key[0] == "a1" for key in self.mail.client._summaries))
        self.archive()
        self.assertFalse(any(key[0] == "a1" for key in self.mail.client._summaries))


class SendTests(Base):
    def test_a_preview_sends_and_writes_nothing(self):
        review = self.preview()
        self.assertEqual((review["recipients"], review["sender"]), (1, ACCOUNT))
        self.assertEqual((self.google.sent, self.records()), ([], ([], [])))

    def test_a_send_needs_the_confirmation_and_the_fingerprint_of_what_was_reviewed(self):
        data = self.approved()
        self.assertEqual(self.client.post("/api/mail/send", json=data, headers=HEADERS).status_code, 400)
        self.assertEqual(self.send({**data, "body": "Moved to 4 pm."}).status_code, 409)
        self.assertEqual(self.send({k: v for k, v in data.items() if k != "fingerprint"}).status_code, 409)
        self.assertEqual((self.google.sent, self.records()), ([], ([], [])))

    def test_a_send_is_recorded_before_it_leaves_and_its_outcome_after(self):
        data = self.approved(cc="ta@tamu.edu")
        seen = []
        original = self.google.__call__
        self.mail.client.http = lambda *a, **k: (seen.append(self.records()), original(*a, **k))[1]
        result = self.send(data)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(len(seen[-1][0]), 1)
        self.assertEqual(seen[-1][1], [])  # the intent was on record while the request was leaving
        self.assertEqual((result.json["status"], result.json["message_id"], result.json["recipients"]), ("accepted", "f1", 2))
        sent, outcomes = self.records()
        self.assertEqual([o["status"] for o in outcomes], ["accepted"])
        mail = self.google.parsed()
        self.assertEqual((mail["From"].addresses[0].addr_spec, mail["To"], mail["Cc"], mail["Subject"]), (ACCOUNT, "alex@tamu.edu", "ta@tamu.edu", "Office hours"))
        self.assertEqual(mail["From"].addresses[0].display_name, "Christopher Sinkule")  # MAIL-SENDER-NAME, not Response Cards' signature
        self.assertEqual(mail.get_content().strip(), "Moved to 3 pm.")
        self.assertEqual(self.client.get("/api/mail/sent").json["sent"][0]["subject"], "Office hours")

    def test_the_same_request_returns_what_happened_and_never_sends_twice(self):
        data = self.approved()
        first = self.send(data).json
        again = self.send(data).json
        self.assertEqual((again["id"], again["status"], len(self.google.sent)), (first["id"], "accepted", 1))
        other = self.client.post("/api/mail/preview", json={**data, "body": "Different."}, headers=HEADERS).json
        self.assertEqual(self.send({**data, "body": "Different.", "fingerprint": other["fingerprint"]}).status_code, 409)
        self.assertEqual(len(self.google.sent), 1)

    def test_a_section_goes_on_bcc_in_one_message_addressed_to_the_instructor(self):
        data = self.approved(to="", bcc=["a@tamu.edu", "b@tamu.edu", "c@tamu.edu"])
        review = self.client.post("/api/mail/preview", json=data, headers=HEADERS).json
        self.assertTrue(any("Bcc" in note for note in review["notes"]))
        self.assertEqual(self.send(data).status_code, 200)
        mail = self.google.parsed()
        self.assertEqual((mail["To"], mail["Bcc"]), (ACCOUNT, "a@tamu.edu, b@tamu.edu, c@tamu.edu"))
        self.assertEqual(len(self.google.sent), 1)

    def test_an_answer_that_never_arrives_is_uncertain_and_is_never_sent_again(self):
        data = self.approved()
        self.google.lose_answer = True
        result = self.send(data).json
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.send(data).json["status"], "uncertain")
        self.assertEqual(len(self.google.sent), 1)

    def test_a_connection_lost_while_google_answers_is_uncertain_too(self):
        # Google kept the message and only its answer was lost (gmail.never_sent): uncertain, never a failure to send again.
        for lost in (ConnectionResetError(104, "Connection reset by peer"), http.client.RemoteDisconnected("Remote end closed connection")):
            with self.subTest(lost=type(lost).__name__):
                data = self.approved()
                self.google.lose_answer = lost
                self.assertEqual(self.send(data).json["status"], "uncertain")
                self.assertEqual(self.send(data).json["status"], "uncertain")
        self.assertEqual(len(self.google.sent), 2)

    def test_an_interrupted_send_with_no_outcome_reads_as_uncertain(self):
        data = self.approved()
        with mock.patch.object(self.mail.client, "send", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.send(data)
        sent, outcomes = self.records()
        self.assertEqual((len(sent), outcomes), (1, []))
        self.assertEqual(self.send(data).json["status"], "uncertain")
        self.assertEqual(self.google.sent, [])

    def test_a_refusal_is_a_recorded_failure_that_a_new_request_may_try_again(self):
        data = self.approved()
        self.google.fail[("POST", "/messages/send")] = (400, {"error": {"code": 400, "message": "Invalid To header"}})
        result = self.send(data).json
        self.assertEqual(result["status"], "failed")
        retry = self.approved(request_id=str(uuid.uuid4()))
        self.assertEqual(self.send(retry).json["status"], "accepted")

    def test_the_records_are_append_only(self):
        self.send(self.approved())
        with connect(self.app.config["DATABASE"]) as db:
            for sql in ("UPDATE gmail_mail_sends SET subject='x'", "DELETE FROM gmail_mail_sends", "UPDATE gmail_mail_outcomes SET status='failed'",
                        "DELETE FROM gmail_mail_outcomes"):
                with self.assertRaises(Exception):
                    db.execute(sql)

    def test_what_cannot_be_sent_is_refused_in_the_preview(self):
        for changes, words in (({"to": "not-an-address"}, "does not look like"), ({"to": "a@tamu.edu", "cc": "A@tamu.edu"}, "more than once"),
                               ({"subject": ""}, "subject"), ({"body": "  "}, "Write the message"), ({"to": ""}, "at least one recipient"),
                               ({"bcc": [f"s{i}@tamu.edu" for i in range(200)]}, "at most 200")):
            response = self.client.post("/api/mail/preview", json=self.fields(**changes), headers=HEADERS)
            self.assertEqual(response.status_code, 400, changes)
            self.assertIn(words, response.json["error"])
        injected = self.preview(subject="Hi\r\nBcc: evil@example.com")
        self.assertEqual(injected["message"]["subject"], "Hi Bcc: evil@example.com")

    def test_a_reply_is_built_from_the_thread_the_server_reads(self):
        data = self.approved(to="", reply_thread_id="a1", subject="ignored by nothing", body="Thursday works.")
        review = self.client.post("/api/mail/preview", json=data, headers=HEADERS).json["message"]
        self.assertEqual((review["to"], review["thread_id"], review["in_reply_to"]), (["alex@tamu.edu"], "a1", "<m1@mail.example>"))
        result = self.send(data)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.google.sent[-1]["threadId"], "a1")
        mail = self.google.parsed()
        self.assertEqual((mail["In-Reply-To"], mail["References"]), ("<m1@mail.example>", "<root@mail.example> <m1@mail.example>"))

    def test_a_reply_goes_to_the_reply_to_with_message_ids_as_written(self):
        """MAIL-REPLY-TO: a sender naming a Reply-To is answered there; a long Message-ID (Canvas's are) stays readable."""
        long_id = "<010001a0fa23177f-c43000c2-b066-43f3-bdc8-418d43326e1e-000000@email.amazonses.com>"
        self.google.threads["a1"][0]["payload"]["headers"][4:] = [{"name": "Message-ID", "value": long_id},
                                                                  {"name": "Reply-To", "value": "Alex Student <alex.replies@tamu.edu>"}]
        read = self.client.get("/api/mail/threads/a1").json["messages"][0]
        self.assertEqual(read["reply_to"], [{"name": "Alex Student", "email": "alex.replies@tamu.edu"}])
        self.assertNotIn("reply_to", self.client.get("/api/mail/threads/b2").json["messages"][0])  # other threads read as before
        for to in ("", "alex@tamu.edu", "ALEX@tamu.edu"):  # the server's own, a page that used From
            review = self.client.post("/api/mail/preview", json=self.fields(to=to, reply_thread_id="a1"), headers=HEADERS).json
            self.assertEqual(review["message"]["to"], ["alex.replies@tamu.edu"], to)
            self.assertEqual(any("asks for replies at" in note for note in review["notes"]), bool(to), to)
        result = self.send(self.approved(to="alex@tamu.edu, ta@tamu.edu", reply_thread_id="a1", body="Come by."))
        self.assertEqual(result.status_code, 200, result.json)
        mail = self.google.parsed()
        self.assertEqual(mail["To"], "alex.replies@tamu.edu, ta@tamu.edu")
        self.assertEqual((mail["In-Reply-To"], mail["References"]), (long_id, long_id))
        self.assertNotIn(b"=?utf-8?", base64.urlsafe_b64decode(self.google.sent[-1]["raw"] + "=="))

    def test_a_reply_review_goes_stale_when_the_thread_gets_a_newer_message(self):
        data = self.approved(to="", reply_thread_id="b2", body="Done.")
        self.google.threads["b2"].append(message("m9", "Registrar <reg@tamu.edu>", ACCOUNT, "Grades due", "Also…", extra=[("Message-ID", "<m9@mail.example>")]))
        self.assertEqual(self.send(data).status_code, 409)
        self.assertEqual(self.google.sent, [])



PDF = b"%PDF-1.4\n% a reviewed paper\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"page one"


class AttachmentTests(Base):
    """MAIL-ATTACHMENTS: files named by the Faculty routes that serve them, read by the server at the preview and again at the send;
    the fingerprint covers their digests, the record keeps references, and the assistant's draft is an Approve card that carries them."""

    def setUp(self):
        super().setUp()
        self.files = {"paper": (PDF, "application/pdf", "Lee paper.pdf"), "page": (PNG, "image/png", "")}
        self.app.add_url_rule("/api/test-files/<name>", "test_files.file", self.serve)

    def serve(self, name):
        from flask import Response, jsonify
        if name not in self.files:
            return jsonify(error="No such file."), 404
        content, media, filename = self.files[name]
        return Response(content, mimetype=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'} if filename else {})

    def attached(self, index=-1):
        return [(part.get_filename(), part.get_content_type(), part.get_content()) for part in self.google.parsed(index).iter_attachments()]

    def test_the_preview_names_each_file_and_the_send_carries_exactly_those(self):
        data = self.approved(attachments=["/api/test-files/paper", {"route": "/api/test-files/page", "name": "Page 1"}])
        review = self.client.post("/api/mail/preview", json=data, headers=HEADERS).json
        files = review["message"]["attachments"]
        self.assertEqual([(f["route"], f["name"], f["type"], f["size"]) for f in files],
                         [("/api/test-files/paper", "Lee paper.pdf", "application/pdf", len(PDF)), ("/api/test-files/page", "Page 1.png", "image/png", len(PNG))])
        self.assertTrue(any("2 attachments" in note for note in review["notes"]))
        self.assertEqual((self.google.sent, self.records()), ([], ([], [])))  # a preview reads the files and sends nothing
        result = self.send(data)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.attached(), [("Lee paper.pdf", "application/pdf", PDF), ("Page 1.png", "image/png", PNG)])
        self.assertEqual(self.google.parsed().get_body(("plain",)).get_content().strip(), "Moved to 3 pm.")
        self.assertNotIn("upload", self.google.sent[-1])  # a small message keeps the raw JSON path
        with connect(self.app.config["DATABASE"]) as db:
            rows = [dict(row) for row in db.execute("SELECT route,name,media_type,size,sha256 FROM gmail_mail_send_attachments ORDER BY position")]
        self.assertEqual([(r["route"], r["name"], r["sha256"]) for r in rows], [(f["route"], f["name"], f["sha256"]) for f in files])
        self.assertEqual([f["name"] for f in self.client.get("/api/mail/sent").json["sent"][0]["attachments"]], ["Lee paper.pdf", "Page 1.png"])

    def test_a_message_without_files_answers_as_it_always_did(self):
        review = self.preview()
        self.assertNotIn("attachments", review["message"])
        self.send({**self.fields(), "fingerprint": review["fingerprint"]})
        self.assertNotIn("attachments", self.client.get("/api/mail/sent").json["sent"][0])

    def test_a_file_that_changed_since_the_review_is_stale_and_nothing_is_sent(self):
        data = self.approved(attachments=["/api/test-files/paper"])
        self.files["paper"] = (PDF + b"% regraded\n", "application/pdf", "Lee paper.pdf")
        self.assertEqual(self.send(data).status_code, 409)
        self.assertEqual((self.google.sent, self.records()), ([], ([], [])))

    def test_what_cannot_be_attached_is_refused_in_the_preview(self):
        for attachments, words in ((["https://evil.example/a.pdf"], "not a Faculty file route"), (["/api/../etc/passwd"], "not a Faculty file route"),
                                   (["/api/no-such-route"], "No Faculty file"), (["/api/export"], "cannot be attached"),
                                   (["/api/mail/sent"], "cannot be attached"), (["/api/test-files/missing"], "answered 404: No such file."),
                                   (["/api/state"], "data, not a file"), (["/api/test-files/paper"] * 2, "attached twice"),
                                   (["/api/test-files/paper?x=a+b"], "percent-encode"), ("/api/test-files/paper", "must be a list"),
                                   ([f"/api/test-files/paper?n={n}" for n in range(11)], "at most 10")):
            with self.subTest(attachments=attachments):
                response = self.client.post("/api/mail/preview", json=self.fields(attachments=attachments), headers=HEADERS)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertIn(words, response.json["error"])
        with mock.patch("gmail_mail.attachments.MAX_TOTAL", len(PDF) + len(PNG) - 1):
            response = self.client.post("/api/mail/preview", json=self.fields(attachments=["/api/test-files/paper", "/api/test-files/page"]), headers=HEADERS)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Gmail takes at most", response.json["error"])
        self.assertEqual(self.google.sent, [])

    def test_a_large_message_is_a_multipart_upload_that_keeps_its_thread(self):
        data = self.approved(to="", reply_thread_id="a1", body="Your paper is attached.", attachments=["/api/test-files/paper"])
        with mock.patch("gmail_mail.client.JSON_RAW_MAX", 100):
            result = self.send(data)
        self.assertEqual((result.status_code, result.json["status"]), (200, "accepted"))
        self.assertEqual(self.google.calls[-1], ("POST", "/messages/send", {"uploadType": ["multipart"]}))
        self.assertEqual((self.google.sent[-1]["upload"], self.google.sent[-1]["threadId"]), (True, "a1"))
        self.assertEqual(self.attached(), [("Lee paper.pdf", "application/pdf", PDF)])
        self.assertEqual(self.google.parsed()["In-Reply-To"], "<m1@mail.example>")

    def test_the_most_files_a_message_carries_fit_gmails_upload(self):
        """MAIL-ATTACHMENTS (requirement, 2026-11-12): 25 MB of files with the longest text, once
        encoded, stay inside the 35 MB Gmail's upload URI takes."""
        import hashlib
        from gmail_mail.attachments import MAX_TOTAL
        from gmail_mail.message import MAX_TEXT, compose
        self.assertEqual(MAX_TOTAL, 25_000_000)
        content = os.urandom(MAX_TOTAL)
        raw = compose({"sender": "me@example.edu", "to": ["lee@example.edu"], "cc": [], "bcc": [], "subject": "Notes", "in_reply_to": "",
                       "references": "", "text": "é" * MAX_TEXT,
                       "attachments": [{"name": "Notes.pdf", "type": "application/pdf", "sha256": hashlib.sha256(content).hexdigest()}]},
                      "Christopher Sinkule", [content])
        self.assertLess(len(raw) + 1000, 35_000_000)  # with the multipart wrapper and thread metadata of client.send

    def test_the_attachment_records_are_append_only(self):
        self.send(self.approved(attachments=["/api/test-files/paper"]))
        with connect(self.app.config["DATABASE"]) as db:
            for sql in ("UPDATE gmail_mail_send_attachments SET name='x'", "DELETE FROM gmail_mail_send_attachments"):
                with self.assertRaises(Exception):
                    db.execute(sql)

    def multipart(self, path, fields, uploads):
        """The compose window's request with files from the computer: the fields as JSON in `message`, each file a `file` part."""
        data = {"message": json.dumps(fields), "file": [(io.BytesIO(content), name, media) for name, media, content in uploads]}
        return self.client.post(path, data=data, content_type="multipart/form-data", headers=HEADERS)

    def test_files_from_the_computer_go_with_the_preview_and_the_send_and_are_never_kept(self):
        fields = self.fields(attachments=[{"upload": 0, "name": "Syllabus.pdf"}, "/api/test-files/page", {"upload": 1}])
        uploads = [("syllabus final.pdf", "application/octet-stream", PDF), ("grades.csv", "text/csv", b"name,score\nLee,9\n")]
        review = self.multipart("/api/mail/preview", fields, uploads)
        self.assertEqual(review.status_code, 200, review.json)
        files = review.json["message"]["attachments"]
        self.assertEqual([(f.get("route"), f.get("from"), f["name"], f["type"]) for f in files],
                         [(None, "computer", "Syllabus.pdf", "application/pdf"), ("/api/test-files/page", None, "page.png", "image/png"),
                          (None, "computer", "grades.csv", "text/csv")])
        self.assertEqual(self.google.sent, [])
        result = self.multipart("/api/mail/send", {**fields, "fingerprint": review.json["fingerprint"], "confirmation": "send"}, uploads)
        self.assertEqual((result.status_code, result.json["status"]), (200, "accepted"))
        self.assertEqual([name for name, _, _ in self.attached()], ["Syllabus.pdf", "page.png", "grades.csv"])
        self.assertEqual(self.attached()[0][2], PDF)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT position,name,media_type FROM gmail_mail_send_uploads ORDER BY position")],
                             [(0, "Syllabus.pdf", "application/pdf"), (2, "grades.csv", "text/csv")])
            self.assertEqual([tuple(row) for row in db.execute("SELECT position,route FROM gmail_mail_send_attachments")], [(1, "/api/test-files/page")])
            for sql in ("UPDATE gmail_mail_send_uploads SET name='x'", "DELETE FROM gmail_mail_send_uploads"):
                with self.assertRaises(Exception):
                    db.execute(sql)
        listed = self.client.get("/api/mail/sent").json["sent"][0]["attachments"]
        self.assertEqual([(f["name"], f.get("from")) for f in listed], [("Syllabus.pdf", "computer"), ("page.png", None), ("grades.csv", "computer")])

    def test_a_different_file_at_the_send_is_stale_and_nothing_is_sent(self):
        fields = self.fields(attachments=[{"upload": 0}])
        review = self.multipart("/api/mail/preview", fields, [("notes.pdf", "application/pdf", PDF)]).json
        result = self.multipart("/api/mail/send", {**fields, "fingerprint": review["fingerprint"], "confirmation": "send"},
                                [("notes.pdf", "application/pdf", PDF + b"% changed\n")])
        self.assertEqual(result.status_code, 409)
        self.assertEqual((self.google.sent, self.records()), ([], ([], [])))

    def test_files_from_the_computer_that_cannot_go_are_refused_in_the_preview(self):
        for attachments, uploads, words in (([], [("a.pdf", "application/pdf", PDF)], "does not list"),
                                            ([{"upload": 1}], [("a.pdf", "application/pdf", PDF)], "not sent with the message"),
                                            ([{"upload": 0}, {"upload": 0}], [("a.pdf", "application/pdf", PDF)], "not sent with the message"),
                                            ([{"upload": 0}], [("setup.exe", "application/octet-stream", b"MZ")], "Gmail does not send .exe"),
                                            ([{"upload": 0, "name": "notes.js"}], [("a.txt", "text/plain", b"x")], "Gmail does not send .js"),
                                            ([{"upload": 0}], [("empty.pdf", "application/pdf", b"")], "is empty")):
            with self.subTest(words=words):
                response = self.multipart("/api/mail/preview", self.fields(attachments=attachments), uploads)
                self.assertEqual(response.status_code, 400, response.json)
                self.assertIn(words, response.json["error"])
        self.assertEqual(self.client.post("/api/mail/preview", data={"message": "not json"}, content_type="multipart/form-data", headers=HEADERS).status_code, 400)
        # A JSON request cannot carry files: the assistant's requests are JSON, so it never uploads.
        response = self.client.post("/api/mail/preview", json=self.fields(attachments=[{"upload": 0}]), headers=HEADERS)
        self.assertIn("not sent with the message", response.json["error"])

    def test_the_compose_windows_faculty_files_are_read_through_their_owners_routes(self):
        from gmail_mail.library import FacultyFiles
        library = FacultyFiles(self.app, self.mail)
        notes = {"current": {"original_url": "/api/course-notes/n1/original/notes.pdf", "source_name": "Notes.pdf", "size": 9000, "title": "Calculus notes",
                             "pages": 40, "parts": [{"number": "1.2", "title": "Limits", "url": "/api/course-notes/n1/parts/2/1-2-limits.pdf", "size": 900,
                                                     "first_page": 3, "last_page": 7}]}}
        artifacts = {"artifacts": [{"url": "/api/assistant/artifacts/a1/file", "filename": "worksheet.pdf", "size": 1200, "title": "Worksheet",
                                    "created_at": "2026-10-01T10:00:00Z"}]}
        reads = []
        with mock.patch.object(FacultyFiles, "_get", side_effect=lambda path: reads.append(path) or (notes if "course-notes" in path else artifacts)), \
             mock.patch("gmail_mail.library.group_courses", return_value=[{"code": "MATH 140", "definition_id": "d1", "is_current": True},
                                                                           {"code": "MATH 140", "definition_id": "d1", "is_current": True},
                                                                           {"code": "MATH 151", "definition_id": "d2", "is_current": False}]):
            found = library.listing("notes")
            saved = library.listing("saved")
        self.assertEqual(reads, ["/api/course-notes/definitions/d1", "/api/assistant/artifacts"])
        self.assertEqual(found["groups"][0]["label"], "MATH 140 · Calculus notes")
        self.assertEqual([(i["route"], i["name"]) for i in found["groups"][0]["items"]],
                         [("/api/course-notes/n1/original/notes.pdf", "Notes.pdf"), ("/api/course-notes/n1/parts/2/1-2-limits.pdf", "1.2 Limits.pdf")])
        self.assertEqual(saved["groups"][0]["items"][0]["name"], "worksheet.pdf")
        self.assertEqual(self.client.get("/api/mail/files?kind=papers&q=a").json["note"], "Type a student's name or email.")
        self.assertEqual(self.client.get("/api/mail/files?kind=other").status_code, 400)

    def test_the_assistants_draft_with_files_waits_for_the_instructors_send(self):
        assistant = self.app.extensions["assistant"]
        session = assistant.open_session("test")
        body = {"to": ["alex@tamu.edu"], "subject": "Your Exam 1", "body": "Attached is your paper.", "request_id": "$new_request_id",
                "attachments": [{"route": "/api/test-files/paper", "name": "Exam 1"}], "confirmation": "send"}
        text, _ = assistant.run_tool(session, "faculty_write", {"method": "POST", "path": "/api/mail/send", "body": body, "summary": "Email Alex their Exam 1"})
        self.assertEqual(json.loads(text)["outcome"], "waiting_for_approval")
        self.assertEqual(self.google.sent, [])
        card = self.client.get("/api/assistant/actions").json["pending"][0]
        self.assertNotIn("confirmation", card["body"])
        self.assertEqual([(f["name"], f["size"]) for f in card["preview"]["data"]["message"]["attachments"]], [("Exam 1.pdf", len(PDF))])
        answer = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual(self.attached(), [("Exam 1.pdf", "application/pdf", PDF)])


if __name__ == "__main__":
    unittest.main()
