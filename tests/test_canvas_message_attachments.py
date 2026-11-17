"""Files with a Canvas message (canvas MESSAGES ATTACHMENTS, requirement, 2026-10-09) offline, against the loopback FakeCanvas: Faculty
files by route (a file attached in the Assistant chat included) and files from the computer, each uploaded once into the instructor's
"conversation attachments" folder with no cookie on the file service, the message sent only once every file is uploaded, and the
review's fingerprint covering each file. Nothing here reaches Canvas."""
import io
import json

from test_canvas_messages import BASE, MessageTests
from test_canvas import ALICE, BOB
from test_storage import HEADERS


class AttachmentTests(MessageTests):
    def prepared(self):
        course, _ = self.course("MATH-140-507", [("Example, Alice", ALICE, "active"), ("Example, Bob", BOB, "active")])
        self.link(507, course)
        chat = self.app.extensions["assistant"].uploads.save([("Exam 1 review.pdf", "application/pdf", b"%PDF-1.4 synthetic review sheet")])[0]
        return course, chat

    def multipart(self, path, fields, files):
        data = {"message": json.dumps(fields), "file": [(io.BytesIO(content), name, kind) for name, kind, content in files]}
        return self.client.post(path, data=data, headers=HEADERS, content_type="multipart/form-data")

    def test_faculty_and_computer_files_go_once_into_conversation_attachments_then_with_the_message(self):
        course, chat = self.prepared()
        fields = {"sections": [self.sections[507]], "subject": "Exam 1 review", "body": "The review sheet and the formula card are attached.",
                  "attachments": [{"route": chat["route"]}, {"upload": 0, "name": "Formula card.pdf"}]}
        files = [("card.pdf", "application/pdf", b"%PDF-1.4 synthetic formula card")]
        preview = self.multipart(f"{BASE}/preview", fields, files)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual([(a["name"], a.get("from"), a["size"]) for a in preview.json["attachments"]],
                         [("Exam 1 review.pdf", None, 31), ("Formula card.pdf", "computer", 31)])
        self.assertEqual(preview.json["attachments"][0]["route"], chat["route"])
        plain = self.ask(f"{BASE}/preview", {k: v for k, v in fields.items() if k != "attachments"}).json
        self.assertNotEqual(plain["fingerprint"], preview.json["fingerprint"])  # the files are part of what is approved
        self.assertNotIn("attachments", plain)
        self.assertFalse([r for r in self.fake.requests if r["method"] == "POST"])  # the review uploads nothing

        sent = self.multipart(f"{BASE}/send", {**fields, "fingerprint": preview.json["fingerprint"], "request_id": "with-files",
                                                "confirmation": "send"}, files)
        record = self.settled(sent)
        self.assertEqual(record["status"], "accepted", record)
        self.assertEqual([(a["name"], a["from"], a["upload"]) for a in record["attachments"]],
                         [("Exam 1 review.pdf", "faculty", "uploaded"), ("Formula card.pdf", "computer", "uploaded")])
        uploaded = list(self.fake.message_files)
        self.assertEqual([self.fake.message_files[f]["name"] for f in uploaded], ["Exam 1 review.pdf", "Formula card.pdf"])
        self.assertEqual([self.fake.message_files[f]["cookies"] for f in uploaded], [{}, {}])  # the file service never sees the session
        self.assertEqual(self.fake.message_files[uploaded[1]]["content"], b"%PDF-1.4 synthetic formula card")
        self.assertEqual(self.fake.sent_messages[-1]["attachment_ids"], uploaded)
        [conversation] = [c for c in self.fake.conversations.values() if c["subject"] == "Exam 1 review"]
        self.assertEqual(conversation["messages"][0]["attachments"], uploaded)  # one group conversation carries both files
        rows = self.rows("canvas_message_attachments")
        self.assertEqual([(r["position"], r["name"], r["source"]) for r in rows],
                         [(0, "Exam 1 review.pdf", chat["route"]), (1, "Formula card.pdf", "computer")])
        self.assertEqual([r["status"] for r in self.rows("canvas_message_uploads")], ["uploaded", "uploaded"])
        again = self.multipart(f"{BASE}/send", {**fields, "fingerprint": preview.json["fingerprint"], "request_id": "with-files",
                                                 "confirmation": "send"}, files)
        self.assertEqual(again.json["id"], record["id"])  # never sent twice
        self.assertEqual(len(self.fake.message_files), 2)

    def test_a_file_service_that_answers_with_a_redirect_is_confirmed_on_canvas(self):
        _, chat = self.prepared()
        self.fake.upload_mode = "redirect"
        fields = {"sections": [self.sections[507]], "subject": "Review", "body": "Attached.", "attachments": [chat["route"]]}
        preview = self.ask(f"{BASE}/preview", fields)
        self.assertEqual(preview.status_code, 200, preview.json)
        record = self.settled(self.ask(f"{BASE}/send", {**fields, "fingerprint": preview.json["fingerprint"], "request_id": "redirected",
                                                        "confirmation": "send"}))
        self.assertEqual((record["status"], record["attachments"][0]["upload"]), ("accepted", "uploaded"))
        confirms = [r for r in self.fake.requests if "/create_success" in r["path"]]
        self.assertEqual(len(confirms), 1)
        self.assertIn("canvas_session", confirms[0]["cookies"])  # the confirmation is Canvas's own, with the session

    def test_a_file_canvas_refuses_sends_nothing(self):
        _, chat = self.prepared()
        self.fake.upload_answer = (400, {"errors": [{"message": "user quota exceeded"}]})
        fields = {"sections": [self.sections[507]], "subject": "Review", "body": "Attached.", "attachments": [chat["route"]]}
        preview = self.ask(f"{BASE}/preview", fields).json
        record = self.settled(self.ask(f"{BASE}/send", {**fields, "fingerprint": preview["fingerprint"], "request_id": "refused",
                                                        "confirmation": "send"}))
        self.assertEqual(record["status"], "failed")
        self.assertIn("user quota exceeded", record["courses"][0]["detail"])
        self.assertEqual(record["attachments"][0]["upload"], "failed")
        self.assertEqual(self.fake.sent_messages, [])  # no course is sent without its files

    def test_a_file_that_changed_after_the_review_needs_a_new_review(self):
        _, chat = self.prepared()
        fields = {"sections": [self.sections[507]], "subject": "Review", "body": "Attached.", "attachments": [{"upload": 0}]}
        preview = self.multipart(f"{BASE}/preview", fields, [("sheet.pdf", "application/pdf", b"%PDF-1.4 first")]).json
        changed = self.multipart(f"{BASE}/send", {**fields, "fingerprint": preview["fingerprint"], "request_id": "changed", "confirmation": "send"},
                                 [("sheet.pdf", "application/pdf", b"%PDF-1.4 second")])
        self.assertEqual(changed.status_code, 409, changed.json)
        self.assertEqual(self.fake.message_files, {})
        refused = self.ask(f"{BASE}/preview", {**fields, "attachments": ["/api/canvas/messages"]})
        self.assertEqual(refused.status_code, 400, refused.json)  # a route that does not answer with a file is no attachment


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(AttachmentTests(name) for name in AttachmentTests.__dict__ if name.startswith("test_"))
