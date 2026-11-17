"""Announcements in the Canvas courses of chosen sections (canvas ANNOUNCEMENTS, canvas/announcements.py) offline, against the
loopback FakeCanvas: the courses it is posted in and the sections that share them, the receipts written before Canvas is asked, no
second post, a refusal and a signed-out session, and the Assistant's Approve card. Nothing here reaches Canvas."""
import json

from test_canvas import Seeded
from test_storage import HEADERS

from assistant import policy
from assistant.outcomes import Outcomes
from canvas import announcements
from rosters import connect

BASE = "/api/canvas/announcements"
EXAM = "Exam 2 is Thursday, October 23, 7:00-9:00 PM.\nRooms: HECC 209 or HECC 207.\n\nCheck your email for your room & seat."


class AnnouncementTests(Seeded):
    def setUp(self):
        super().setUp()
        self.keep()
        self.a = self.fake.add_course("MATH-140-507", "MATH-140-507", "Fall 2026")
        self.b = self.fake.add_course("MATH-140-508", "MATH-140-508", "Fall 2026")

    def link(self, number, course):
        answer = self.client.post(f"/api/canvas/sections/{self.sections[number]}", json={"course_id": course, "base": ""}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)

    def ask(self, path, body):
        return self.client.post(path, json=body, headers=HEADERS)

    def rows(self, table):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def outcome(self, record):
        """How the Assistant's card reads the post, from its receipts."""
        with connect(self.app.config["DATABASE"]) as db:
            return Outcomes(db=db).read("canvas.announcement_post", "succeeded", {"result": record})

    def test_an_announcement_is_posted_once_in_each_chosen_section_s_course(self):
        self.link(507, self.a)
        self.link(508, self.b)
        before = len(self.fake.requests)
        view = self.client.get(BASE).json
        self.assertEqual([(s["number"], s["course"]["course_id"]) for s in view["terms"][0]["sections"]], [(507, self.a), (508, self.b)])
        body = {"sections": [self.sections[507], self.sections[508]], "title": "  Exam 2   rooms ", "message": EXAM.replace("\n", "\r\n")}
        found = self.ask(f"{BASE}/preview", body)
        self.assertEqual(found.status_code, 200, found.json)
        found = found.json
        self.assertEqual((found["title"], found["message"], found["unlinked"]), ("Exam 2 rooms", EXAM, []))
        self.assertEqual([(c["course_id"], c["sections"], c["also"]) for c in found["courses"]], [(self.a, [507], []), (self.b, [508], [])])
        self.assertEqual(len(self.fake.requests), before)  # neither the page nor the preview contacts Canvas

        post = {**body, "fingerprint": found["fingerprint"], "request_id": "ann-1", "confirmation": "post"}
        self.assertEqual(self.client.post(f"{BASE}/post", json=post).status_code, 403)  # the workspace guard
        self.assertEqual(self.ask(f"{BASE}/post", {**post, "confirmation": "send"}).status_code, 400)
        self.assertEqual(self.ask(f"{BASE}/post", {**post, "fingerprint": "0" * 64}).status_code, 409)
        self.assertEqual((self.rows("canvas_announcements"), self.fake.announcements), ([], []))
        record = self.ask(f"{BASE}/post", post)
        self.assertEqual(record.status_code, 200, record.json)
        record = record.json
        self.assertEqual((record["status"], [(c["course_id"], c["status"], c["sections"]) for c in record["courses"]]),
                         ("posted", [(self.a, "posted", [507]), (self.b, "posted", [508])]))
        self.assertTrue(all(f"/courses/{c['course_id']}/discussion_topics/" in c["url"] for c in record["courses"]))
        html = ("<p>Exam 2 is Thursday, October 23, 7:00-9:00 PM.<br>Rooms: HECC 209 or HECC 207.</p>"
                "<p>Check your email for your room &amp; seat.</p>")
        self.assertEqual([(p["course"], p["title"], p["message"], p["is_announcement"], p["published"]) for p in self.fake.announcements],
                         [(self.a, "Exam 2 rooms", html, True, True), (self.b, "Exam 2 rooms", html, True, True)])

        again = self.ask(f"{BASE}/post", post)  # the same request ID: what happened, never a second announcement
        self.assertEqual((again.status_code, again.json["id"]), (200, record["id"]))
        self.assertEqual(len(self.fake.announcements), 2)
        self.assertEqual(self.ask(f"{BASE}/post", {**post, "fingerprint": "f" * 64}).status_code, 409)
        self.assertEqual([(a["title"], a["status"]) for a in self.client.get(BASE).json["posted"]], [("Exam 2 rooms", "posted")])
        self.assertEqual(self.client.get(f"{BASE}/{record['id']}").json["id"], record["id"])
        [row] = self.rows("canvas_announcements")
        self.assertEqual((row["actor"], row["message_html"], json.loads(row["sections_json"])),
                         ("workspace", html, sorted([self.sections[507], self.sections[508]])))
        self.assertEqual(self.outcome(record), "succeeded")
        with connect(self.app.config["DATABASE"]) as db:
            for sql in ("UPDATE canvas_announcements SET title='x'", "DELETE FROM canvas_announcement_posts",
                        "UPDATE canvas_announcement_outcomes SET status='failed'"):
                with self.subTest(sql=sql), self.assertRaisesRegex(Exception, "append-only|retained"):
                    db.execute(sql)

    def test_a_shared_course_is_posted_once_and_says_who_else_sees_it(self):
        self.link(507, self.a)
        self.link(508, self.a)
        body = {"sections": [self.sections[507]], "title": "Exam 2", "message": "Rooms posted."}
        found = self.ask(f"{BASE}/preview", body).json
        self.assertEqual([(c["course_id"], c["sections"], c["also"]) for c in found["courses"]], [(self.a, [507], [508])])
        both = self.ask(f"{BASE}/preview", {**body, "sections": [self.sections[507], self.sections[508]]}).json
        self.assertEqual([(c["sections"], c["also"]) for c in both["courses"]], [([507, 508], [])])
        self.assertEqual(both["fingerprint"], found["fingerprint"])  # the same course, the same words: the same post
        record = self.ask(f"{BASE}/post", {**both, "sections": [self.sections[507], self.sections[508]], "request_id": "ann-2", "confirmation": "post"}).json
        self.assertEqual([c["course_id"] for c in record["courses"]], [self.a])
        self.assertEqual(len(self.fake.announcements), 1)

    def test_an_unlinked_section_is_named_and_a_refusal_or_sign_out_is_recorded(self):
        self.link(507, self.a)
        body = {"sections": [self.sections[507], self.sections[508]], "title": "Exam 2", "message": "Rooms posted."}
        found = self.ask(f"{BASE}/preview", body).json
        self.assertEqual(([c["course_id"] for c in found["courses"]], found["unlinked"]), ([self.a], [508]))
        nowhere = self.ask(f"{BASE}/preview", {**body, "sections": [self.sections[508]]}).json
        self.assertEqual(self.ask(f"{BASE}/post", {**body, "sections": [self.sections[508]], "fingerprint": nowhere["fingerprint"],
                                                   "request_id": "ann-0", "confirmation": "post"}).status_code, 400)
        for change, status in (({"title": ""}, 400), ({"message": " "}, 400), ({"sections": []}, 400), ({"sections": ["nope"]}, 400),
                               ({"title": "x" * 256}, 400)):
            self.assertEqual(self.ask(f"{BASE}/preview", {**body, **change}).status_code, status, change)

        self.link(508, self.b)
        both = self.ask(f"{BASE}/preview", body).json
        self.fake.announcement_answer[self.a] = (403, {"errors": [{"message": "user not authorized to perform that action"}]})
        self.fake.announcement_answer[self.b] = (500, {"errors": [{"message": "Internal error"}]})
        record = self.ask(f"{BASE}/post", {**body, "fingerprint": both["fingerprint"], "request_id": "ann-3", "confirmation": "post"}).json
        self.assertEqual([c["status"] for c in record["courses"]], ["failed", "uncertain"])
        self.assertEqual(record["status"], "mixed")
        self.assertIn("may or may not have posted", record["courses"][1]["detail"])
        self.assertEqual(self.outcome(record), "uncertain")  # a course Canvas did not answer for is never "partly"

        self.fake.announcement_answer.clear()
        self.fake.sessions.clear()  # Canvas no longer takes the kept session: nothing is posted, nothing more is tried
        record = self.ask(f"{BASE}/post", {**body, "fingerprint": both["fingerprint"], "request_id": "ann-4", "confirmation": "post"}).json
        self.assertEqual((record["status"], self.fake.announcements), ("failed", []))
        self.assertTrue(all("sign in" in c["detail"] for c in record["courses"]), record)

    def test_the_assistant_drafts_an_announcement_and_only_approve_posts_it(self):
        self.link(507, self.a)
        self.link(508, self.b)
        self.assertEqual(policy.base_rule("canvas.announcement_preview").tier, "read")
        rule = policy.base_rule("canvas.announcement_post")
        self.assertEqual((rule.tier, rule.preview, rule.confirmation, rule.request_id), ("approve", "canvas.announcement_preview", "post", True))
        assistant = self.app.extensions["assistant"]
        body = {"sections": [self.sections[507], self.sections[508]], "title": "Exam 2", "message": EXAM + "\n" + "x" * 3000}
        asked, _ = assistant.run_tool(assistant.open_session("test"), "faculty_write", {
            "method": "POST", "path": f"{BASE}/post", "summary": "Announce Exam 2 in both sections", "body": body})
        self.assertEqual(json.loads(asked)["outcome"], "waiting_for_approval", asked)
        card, = self.client.get("/api/assistant/actions").json["pending"]
        shown = card["preview"]["data"]
        self.assertEqual((card["endpoint"], shown["title"], shown["message"]), ("canvas.announcement_post", "Exam 2", body["message"]))
        self.assertEqual(self.fake.announcements, [])  # nothing until the instructor approves
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual([p["course"] for p in self.fake.announcements], [self.a, self.b])
        self.assertEqual([r["actor"] for r in self.rows("canvas_announcements")], ["assistant"])

    def test_the_text_becomes_escaped_paragraphs(self):
        self.assertEqual(announcements.as_html("One <b>\nTwo\n\n\n  \nThree"), "<p>One &lt;b&gt;<br>Two</p><p>Three</p>")
