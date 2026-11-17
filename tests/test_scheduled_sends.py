"""Messages sent later (docs/decisions/mail.md MAIL-SEND-LATER), offline: a reviewed Mail message or Canvas message to sections kept
exactly as approved and sent by the round at its time through the ordinary send and its receipts; waiting while Gmail, Canvas or
Faculty cannot send, held after two hours; Cancel, Send now and Dismiss; Home's card; the Assistant's Approve rules. The rounds run
with the test's own clock, the fake Google and the loopback FakeCanvas; nothing reaches the network."""
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from test_canvas import ALICE, BOB, Seeded
from test_gmail_mail import Base as MailBase
from test_storage import HEADERS

from assistant import policy
from rosters import connect
from scheduled_sends import service as later
from scheduling.clock import CAMPUS_ZONE
from workspace_overview import overview

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)  # Mon, Nov 9, 9:00 AM campus time
# Scheduling reads the real clock: the messages go tomorrow at 8:00 AM campus time, and the rounds run on the test's own clock.
_TOMORROW = (datetime.now(CAMPUS_ZONE) + timedelta(days=1)).date()
AT = f"{_TOMORROW.isoformat()}T08:00"
DUE = datetime(_TOMORROW.year, _TOMORROW.month, _TOMORROW.day, 8, 0, tzinfo=CAMPUS_ZONE).astimezone(timezone.utc)


class FakeFiles:
    """Faculty's file routes, as AppFiles reads them: one file whose bytes the test may change."""

    def __init__(self):
        self.content = b"%PDF-1.4 the syllabus\n"

    def read(self, path, wanted=""):
        return path, {"name": wanted or "syllabus.pdf", "type": "application/pdf", "size": len(self.content),
                      "sha256": hashlib.sha256(self.content).hexdigest()}, self.content


class MailLaterTests(MailBase):
    def setUp(self):
        super().setUp()
        self.later = self.app.extensions["scheduled_sends"]

    def schedule(self, approved, **changes):
        body = {**approved, "confirmation": "schedule", "send_at": AT, **changes}
        return self.client.post("/api/mail/schedule", json=body, headers=HEADERS)

    def kept(self, approved, **changes):
        answer = self.schedule(approved, **changes)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def at(self, when):
        self.later.round(when)
        return self.client.get("/api/scheduled-sends").json["scheduled"]

    def test_the_time_is_campus_time_and_must_be_ahead(self):
        self.assertEqual(later.send_time("2026-11-10T08:00", NOW), datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc))  # CST, -06:00
        self.assertEqual(later.send_time("2026-11-10T14:00:00Z", NOW), datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc))
        self.assertEqual(later.send_time("2026-07-10T08:00", datetime(2026, 7, 1, tzinfo=timezone.utc)),
                         datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc))  # CDT, -05:00
        for value, words in (("", "Choose the date"), ("next week", "not a date"), ("2026-11-09T09:00", "at least a minute"),
                             ("2027-11-10T08:00", "within 180 days")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, words):
                later.send_time(value, NOW)
        self.assertEqual(later.campus_text(datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)), "Tue, Nov 10, 8:00 AM")

    def test_a_message_goes_at_its_time_exactly_as_approved_and_only_once(self):
        approved = self.approved(bcc="alex@tamu.edu, blair@tamu.edu", to="")
        self.assertEqual(self.schedule(approved, confirmation="").status_code, 400)
        self.assertEqual(self.schedule(approved, fingerprint="0" * 64).status_code, 409)
        self.assertEqual(self.schedule(approved, send_at="2020-01-01T08:00").status_code, 400)
        self.assertEqual(self.client.post("/api/mail/schedule", json={**approved, "confirmation": "schedule", "send_at": AT}).status_code, 403)
        item = self.kept(approved)
        self.assertEqual((item["state"], item["channel"], item["send_at"], item["recipients"], item["audience"], item["subject"]),
                         ("waiting", "mail", DUE.isoformat(), 2, "2 on Bcc", "Office hours"))
        self.assertEqual(self.writes(), [])  # scheduling sends nothing
        self.assertEqual(self.kept(approved)["id"], item["id"])  # the same request: the same message, never two
        self.assertEqual(self.schedule(approved, fingerprint="f" * 64).status_code, 409)  # the same request, another message

        self.assertEqual(self.at(DUE - timedelta(minutes=1))[0]["state"], "waiting")
        self.assertEqual(self.google.sent, [])
        [found] = self.at(DUE)
        self.assertEqual((found["state"], found["result"]["status"], found["result"]["recipients"]), ("sent", "accepted", 2))
        self.assertNotIn("late_minutes", found["result"])
        message = self.google.parsed()
        self.assertEqual((message["Subject"], message["Bcc"], message["To"]), ("Office hours", "alex@tamu.edu, blair@tamu.edu", self.mail.google.email))  # addressed to the instructor, as a class send is
        self.at(DUE + timedelta(minutes=5))
        self.assertEqual(len(self.google.sent), 1)
        sends, outcomes = self.records()
        self.assertEqual([(s["request_id"], s["actor"]) for s in sends], [("later-" + item["id"], "workspace")])
        self.assertEqual([o["status"] for o in outcomes], ["accepted"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([e["kind"] for e in db.execute("SELECT kind FROM scheduled_send_events")], ["sent"])
            for sql in ("UPDATE scheduled_sends SET subject='x'", "DELETE FROM scheduled_sends", "DELETE FROM scheduled_send_events"):
                with self.subTest(sql=sql), self.assertRaisesRegex(Exception, "append-only|retained"):
                    db.execute(sql)

    def test_a_reply_or_a_file_from_the_computer_is_not_sent_later(self):
        approved = self.approved()
        answer = self.schedule(approved, reply_thread_id="a1")
        self.assertEqual(answer.status_code, 400)
        self.assertIn("reply", answer.json["error"])
        answer = self.schedule(approved, attachments=[{"upload": 0, "name": "notes.pdf"}])
        self.assertEqual(answer.status_code, 400)
        self.assertIn("computer", answer.json["error"])

    def test_it_waits_while_gmail_is_not_connected_then_is_held_for_the_instructor(self):
        item = self.kept(self.approved())
        os.remove(self.token_file)  # Gmail disconnected
        [found] = self.at(DUE)
        self.assertEqual((found["state"], found["waiting"]), ("waiting", "Gmail is not connected (Connect Gmail on the Mail page)."))
        [found] = self.at(DUE + timedelta(hours=1, minutes=59))
        self.assertEqual(found["state"], "waiting")
        self.assertFalse([card for card in overview(self.app)["attention"] if card["key"] == "scheduled_sends"])
        [found] = self.at(DUE + timedelta(hours=2, minutes=1))
        self.assertEqual(found["state"], "held")
        self.assertEqual(found["detail"], f"Faculty could not send it by {later.campus_text(DUE + later.LATE)}: Gmail is not connected (Connect Gmail on the Mail page).")
        [card] = [card for card in overview(self.app)["attention"] if card["key"] == "scheduled_sends"]
        self.assertEqual((card["count"], card["href"]), (1, "#mail?scheduled=1"))
        self.at(DUE + timedelta(hours=5))  # a held message never goes by itself
        self.assertEqual(self.google.sent, [])

        route = f"/api/scheduled-sends/{item['id']}/send"
        self.assertEqual(self.client.post(route, json={}, headers=HEADERS).status_code, 400)
        refused = self.client.post(route, json={"confirmation": "send"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Gmail is not connected", refused.json["error"])
        self.write_token(self.scopes)
        sent = self.client.post(route, json={"confirmation": "send"}, headers=HEADERS)
        self.assertEqual((sent.status_code, sent.json["state"], sent.json["result"]["status"]), (200, "sent", "accepted"))
        self.assertEqual(len(self.google.sent), 1)
        self.assertEqual(self.client.post(route, json={"confirmation": "send"}, headers=HEADERS).json["state"], "sent")
        self.assertEqual(len(self.google.sent), 1)
        self.assertFalse([card for card in overview(self.app)["attention"] if card["key"] == "scheduled_sends"])

    def test_a_message_goes_late_within_two_hours(self):
        self.kept(self.approved())
        [found] = self.at(DUE + timedelta(minutes=95))
        self.assertEqual((found["state"], found["result"]["status"]), ("sent", "accepted"))
        self.assertEqual(found["result"]["late_minutes"], 95)

    def test_cancelled_it_is_never_sent(self):
        item = self.kept(self.approved())
        route = f"/api/scheduled-sends/{item['id']}"
        self.assertEqual(self.client.post(route + "/cancel", json={}, headers=HEADERS).json["state"], "cancelled")
        self.assertEqual(self.client.post(route + "/cancel", json={}, headers=HEADERS).json["state"], "cancelled")
        self.assertEqual(self.at(DUE)[0]["state"], "cancelled")
        self.assertEqual(self.google.sent, [])
        self.assertEqual(self.client.post(route + "/send", json={"confirmation": "send"}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.google.sent, [])

    def test_a_sent_message_cannot_be_cancelled(self):
        item = self.kept(self.approved())
        self.at(DUE)
        self.assertEqual(self.client.post(f"/api/scheduled-sends/{item['id']}/cancel", json={}, headers=HEADERS).status_code, 409)

    def test_an_attached_file_that_changed_holds_the_message(self):
        self.mail.files = FakeFiles()
        approved = self.approved(attachments=[{"route": "/api/course-notes/n1/file", "name": "syllabus.pdf"}])
        item = self.kept(approved)
        self.assertEqual(item["attachments"], [{"name": "syllabus.pdf", "route": "/api/course-notes/n1/file"}])
        self.mail.files.content = b"%PDF-1.4 the syllabus, edited\n"
        [found] = self.at(DUE)
        self.assertEqual((found["state"], found["detail"]), ("held", later.CHANGED))
        self.assertEqual(self.google.sent, [])

    def test_a_send_gmail_refused_waits_on_home_until_dismissed(self):
        item = self.kept(self.approved())
        self.google.fail[("POST", "/messages/send")] = (400, {"error": {"code": 400, "message": "Invalid To header"}})
        [found] = self.at(DUE)
        self.assertEqual((found["state"], found["result"]["status"], found["problem"]), ("sent", "failed", True))
        [card] = [card for card in overview(self.app)["attention"] if card["key"] == "scheduled_sends"]
        self.assertEqual(card["count"], 1)
        self.at(DUE + timedelta(minutes=5))
        self.assertEqual(len([call for call in self.google.calls if call[1] == "/messages/send"]), 1)  # never sent again
        dismissed = self.client.post(f"/api/scheduled-sends/{item['id']}/dismiss", json={}, headers=HEADERS).json
        self.assertEqual((dismissed["state"], dismissed["problem"]), ("sent", False))
        self.assertFalse([card for card in overview(self.app)["attention"] if card["key"] == "scheduled_sends"])

    def test_the_assistant_schedules_only_through_an_approve_card(self):
        self.assertEqual(policy.base_rule("gmail_mail.preview").tier, "read")
        for endpoint, preview in (("gmail_mail.schedule", "gmail_mail.preview"), ("canvas.message_schedule", "canvas.message_preview")):
            rule = policy.base_rule(endpoint)
            self.assertEqual((rule.tier, rule.preview, rule.confirmation), ("approve", preview, "schedule"))
            self.assertIn(endpoint, policy.REQUEST_IDS)
        self.assertEqual((policy.base_rule("scheduled_sends.send_now").tier, policy.base_rule("scheduled_sends.send_now").confirmation), ("approve", "send"))
        self.assertEqual(policy.base_rule("scheduled_sends.cancel").tier, "approve")


class CanvasLaterTests(Seeded):
    def setUp(self):
        super().setUp()
        self.keep()
        self.later = self.app.extensions["scheduled_sends"]
        self.course_id = self.fake.add_course("MATH-140-507", "MATH-140-507", "Fall 2026")
        self.canvas_section = self.fake.add_section(self.course_id, "MATH-140-507")
        self.users = {who: self.fake.add_student(self.course_id, self.canvas_section, who, sis, state="active")
                      for who, sis in (("Example, Alice", ALICE), ("Example, Bob", BOB))}
        answer = self.client.post(f"/api/canvas/sections/{self.sections[507]}", json={"course_id": self.course_id, "base": ""}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)

    def scheduled(self):
        body = {"sections": [self.sections[507]], "subject": "Exam 2 room", "body": "Exam 2 is in HECC 207."}
        preview = self.client.post("/api/canvas/messages/preview", json=body, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        kept = self.client.post("/api/canvas/messages/schedule", json={**body, "fingerprint": preview.json["fingerprint"], "request_id": str(uuid.uuid4()),
                                                                       "confirmation": "schedule", "send_at": AT}, headers=HEADERS)
        self.assertEqual(kept.status_code, 200, kept.json)
        return kept.json

    def test_it_goes_at_its_time_to_exactly_the_students_approved(self):
        item = self.scheduled()
        self.assertEqual((item["channel"], item["audience"], item["recipients"], item["state"]), ("canvas", "Section 507", 2, "waiting"))
        self.fake.add_student(self.course_id, self.canvas_section, "Newcomer, Dana", "999888777", state="active")  # after the approval
        self.later.round(DUE - timedelta(minutes=1))
        self.assertEqual(self.fake.sent_messages, [])
        self.later.round(DUE)
        self.assertTrue(self.app.extensions["canvas_messages"].wait_idle(10))
        [posted] = self.fake.sent_messages
        self.assertEqual(sorted(posted["recipients"]), sorted(self.users.values()))
        self.assertEqual((posted["subject"], posted["body"], posted["group_conversation"]), ("Exam 2 room", "Exam 2 is in HECC 207.", True))
        found = self.client.get(f"/api/scheduled-sends/{item['id']}").json
        self.assertEqual((found["state"], found["result"]["status"]), ("sent", "accepted"))
        listed = self.client.get("/api/canvas/messages").json["sent"]
        self.assertEqual([(m["subject"], m["status"], m["request_id"]) for m in listed], [("Exam 2 room", "accepted", "later-" + item["id"])])
        self.later.round(DUE + timedelta(minutes=5))
        self.assertTrue(self.app.extensions["canvas_messages"].wait_idle(10))
        self.assertEqual(len(self.fake.sent_messages), 1)

    def test_it_waits_while_faculty_is_signed_out_of_canvas(self):
        item = self.scheduled()
        self.fake.sessions.clear()  # Canvas no longer takes the kept session
        self.later.round(DUE)
        found = self.client.get(f"/api/scheduled-sends/{item['id']}").json
        self.assertEqual((found["state"], found["waiting"]), ("waiting", "Faculty is signed out of Canvas (sign in on the Canvas page)."))
        self.assertEqual(self.fake.sent_messages, [])
        self.keep()
        self.later.round(DUE + timedelta(minutes=30))
        self.assertTrue(self.app.extensions["canvas_messages"].wait_idle(10))
        self.assertEqual(len(self.fake.sent_messages), 1)
        self.assertEqual(self.client.get("/api/scheduled-sends?channel=canvas").json["scheduled"][0]["state"], "sent")
        self.assertEqual(self.client.get("/api/scheduled-sends?channel=mail").json["scheduled"], [])

    def test_a_file_from_the_computer_is_refused(self):
        answer = self.client.post("/api/canvas/messages/schedule", data={"message": json.dumps({"sections": [self.sections[507]]})}, headers=HEADERS)
        self.assertEqual(answer.status_code, 415)  # JSON only: nothing from the computer is kept until then
        answer = self.client.post("/api/canvas/messages/schedule", json={"sections": [self.sections[507]], "subject": "s", "body": "b",
                                  "attachments": [{"upload": 0, "name": "notes.pdf"}], "request_id": "r-1", "confirmation": "schedule",
                                  "send_at": AT}, headers=HEADERS)
        self.assertEqual(answer.status_code, 400)
        self.assertIn("computer", answer.json["error"])


if __name__ == "__main__":
    import unittest
    unittest.main()
