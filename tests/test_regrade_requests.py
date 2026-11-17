"""Regrade requests on the Review step (AGENTS.md "Regrade requests"): a regrade request the communications record captured is placed
beside the response Faculty sent for that student's question, never guessed; the reply is previewed (writes nothing), recorded
before it is sent, posted once with the grading page's own Close & Respond, and accepted only when Gradescope reads back with it;
an unreadable outcome is settled by reading, never by sending again; marks are append-only; Home counts open requests per plan."""
import json
import unittest

from rosters import connect
from test_gradescope_send import GradescopeCase
from workspace_overview import overview


class RegradeTests(GradescopeCase):
    def setUp(self):
        super().setUp()
        self.send()
        self.sender.drain()
        self.regrades = self.app.extensions["regrades"]
        self.record = self.app.extensions["communications"]
        self.record.gradescope_settings = self.settings
        self.qsub_a = self.qsub("q20", "A", self.alice)
        self.request = self.fake.add_regrade(self.qsub_a["id"], "Question 20: my answer was right, please look again.")

    def capture(self):
        with self.record.lock():
            return self.record.round("gradescope")

    def listing(self, **args):
        return self.client.get(f"/api/grading/assignments/{self.aid}/regrade-requests", query_string=args).json

    def test_a_captured_request_is_placed_beside_its_response_and_answered_from_faculty(self):
        self.assertEqual(self.capture()["status"], "ok")
        found = self.listing()
        self.assertEqual(found["counts"], {"open": 1, "answered": 0, "handled": 0})
        item = found["requests"][0]
        response = self.response("q20", "A")
        self.assertEqual((item["response_id"], item["question_id"], item["state"], item["can_reply"]), (response["id"], self.q[("q20", "A")], "open", True))
        self.assertEqual(item["how"], "Faculty's grade on Gradescope")
        self.assertNotIn("Alice", json.dumps(found))  # the Review step is anonymous
        self.assertEqual(self.listing(response_id=response["id"])["requests"][0]["id"], item["id"])
        cards = [card for card in overview(self.app)["attention"] if card["key"].startswith("regrades:")]
        # A regrade request is a Follow-up conversation (REGRADES "FOLLOW-UP", 2026-10-07): Home has no regrade card of its own.
        self.assertEqual(cards, [])

        route = f"/api/grading/assignments/{self.aid}/regrade-requests/{item['id']}/reply"
        before = len(self.fake.requests)
        preview = self.client.post(f"{route}/preview", json={"reply": "You are right: +2 points."}, headers={"X-Teaching-Request": "1"}).json
        self.assertTrue(preview["open"])
        self.assertEqual(preview["faculty"]["points"], 4.0)
        self.assertTrue(all(r["method"] == "GET" or r["path"] == "/login" for r in self.fake.requests[before:]))  # a preview writes nothing
        missing = self.client.post(route, json={"reply": "You are right: +2 points.", "fingerprint": preview["fingerprint"], "request_id": "r1"},
                                   headers={"X-Teaching-Request": "1"})
        self.assertEqual(missing.status_code, 400)  # no confirmation
        stale = self.client.post(route, json={"reply": "Something else.", "fingerprint": preview["fingerprint"], "request_id": "r1", "confirmation": "reply"},
                                 headers={"X-Teaching-Request": "1"})
        self.assertEqual(stale.status_code, 409)
        done = self.client.post(route, json={"reply": "You are right: +2 points.", "fingerprint": preview["fingerprint"], "request_id": "r2",
                                             "confirmation": "reply"}, headers={"X-Teaching-Request": "1"})
        self.assertEqual(done.status_code, 200, done.json)
        self.assertEqual(done.json["reply"]["status"], "accepted")
        self.assertEqual((self.request["staff_comment"], self.request["closed"]), ("You are right: +2 points.", True))
        puts = [r for r in self.fake.requests if r["method"] == "PUT"]
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0]["json"], {"regrade_request": {"id": self.request["id"], "staff_comment": "You are right: +2 points."}})
        again = self.client.post(route, json={"reply": "You are right: +2 points.", "fingerprint": preview["fingerprint"], "request_id": "r2",
                                              "confirmation": "reply"}, headers={"X-Teaching-Request": "1"})
        self.assertTrue(again.json["repeated"])
        self.assertEqual(len([r for r in self.fake.requests if r["method"] == "PUT"]), 1)  # never sent twice
        self.assertEqual(self.listing()["counts"], {"open": 0, "answered": 1, "handled": 0})
        self.assertNothingForbidden()

    def test_an_unreadable_outcome_is_settled_by_reading_never_by_sending_again(self):
        self.capture()
        item = self.listing()["requests"][0]
        route = f"/api/grading/assignments/{self.aid}/regrade-requests/{item['id']}/reply"
        preview = self.regrades.preview(self.aid, item["id"], {"reply": "Looked again: the grade stands."})
        with unittest.mock.patch.object(type(self.regrades), "_read_back", return_value=("uncertain", "Gradescope could not be read back.")):
            done = self.regrades.reply(self.aid, item["id"], {"reply": "Looked again: the grade stands.", "fingerprint": preview["fingerprint"],
                                                               "request_id": "r1", "confirmation": "reply"})
        self.assertEqual(done["reply"]["status"], "uncertain")
        checked = self.client.post(f"/api/grading/assignments/{self.aid}/regrade-requests/replies/{done['reply']['id']}/check", json={},
                                   headers={"X-Teaching-Request": "1"}).json
        self.assertEqual(checked["reply"]["status"], "accepted")  # the reply had landed: read back, not sent again
        self.assertEqual(len([r for r in self.fake.requests if r["method"] == "PUT"]), 1)
        self.assertTrue(route)

    def test_a_request_already_answered_on_gradescope_is_not_answered_again(self):
        self.capture()
        item = self.listing()["requests"][0]
        preview = self.regrades.preview(self.aid, item["id"], {"reply": "Late reply."})
        self.request.update(staff_comment="Answered in Gradescope.", closed=True)
        with self.assertRaises(Exception) as refused:
            self.regrades.reply(self.aid, item["id"], {"reply": "Late reply.", "fingerprint": preview["fingerprint"], "request_id": "r1",
                                                       "confirmation": "reply"})
        self.assertEqual(getattr(refused.exception, "code", None), 409)
        self.assertEqual([r for r in self.fake.requests if r["method"] == "PUT"], [])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_regrade_replies").fetchone()[0], 0)

    def test_marks_are_append_only_and_reopen(self):
        self.capture()
        item = self.listing()["requests"][0]
        route = f"/api/grading/assignments/{self.aid}/regrade-requests/{item['id']}/mark"
        handled = self.client.post(route, json={"request_id": "m1", "action": "handled", "note": "Talked in office hours"}, headers={"X-Teaching-Request": "1"}).json
        self.assertEqual(handled["counts"], {"open": 0, "answered": 0, "handled": 1})
        replay = self.client.post(route, json={"request_id": "m1", "action": "handled", "note": " Talked in office hours "}, headers={"X-Teaching-Request": "1"})
        self.assertEqual(replay.status_code, 200)
        for changed in ({"action": "reopened"}, {"action": "handled", "note": "A different decision"}):
            refused = self.client.post(route, json={"request_id": "m1", **changed}, headers={"X-Teaching-Request": "1"})
            self.assertEqual(refused.status_code, 409, refused.json)
        reopened = self.client.post(route, json={"request_id": "m2", "action": "reopened"}, headers={"X-Teaching-Request": "1"}).json
        self.assertEqual(reopened["counts"]["open"], 1)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_regrade_marks").fetchone()[0], 2)
            with self.assertRaises(Exception):
                db.execute("DELETE FROM frq_regrade_marks")

    def test_a_request_about_a_question_faculty_does_not_have_stays_unmatched(self):
        other = self.fake.qsub_of(self.versions["A"], self.alice, next(q for q, found in self.fake.questions.items()
                                                                        if found["assignment"] == self.versions["A"] and found["title"] == "Question 18"))
        self.fake.add_regrade(other["id"], "Question 18 too, please.")
        self.capture()
        # It is about this assignment (its Gradescope exam is linked) but no question of the version prints 18: listed, unplaced.
        found = {item["text"]: item for item in self.listing()["requests"]}
        other = found["Question 18 too, please."]
        self.assertEqual((other["matched"], other["response_id"], other["can_reply"]), (False, None, True))
        self.assertIn("prints the number", other["reason"])
        self.assertTrue(found["Question 20: my answer was right, please look again."]["matched"])


if __name__ == "__main__":
    unittest.main()
