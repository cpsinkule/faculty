"""Gradescope regrade requests through Follow-up (REGRADES "FOLLOW-UP", requirement, 2026-10-07): a captured request is a Follow-up conversation needing a reply; its
preparation is told the question, the rubric (lines, points, applied, clarifications, comments), the grade, the reading and the class's
results, with the crops attached to the turn; its one decide card changes or keeps the grade and, once approved, runs the same Apply as
Return's; the answered request leaves Follow-up; Home has no regrade card of its own. Fakes only: the fake Gradescope and a scripted
model."""
import json
import re
import time
import unittest
import uuid
from unittest.mock import patch

from rosters import connect
from test_grading_certification import HEADERS, Certify
from workspace_overview import overview


class PictureEngine:
    """A scripted engine that keeps the turn's prompt and pictures and runs `respond` (proposals through the ordinary tools)."""
    name = "scripted"

    def __init__(self, respond=None):
        self.respond, self.prompt, self.images = respond, "", []

    def available(self):
        return True

    def why_unavailable(self):
        return ""

    def stop(self):
        pass

    def close(self):
        pass

    def turn(self, c, prompt, images=()):
        self.prompt, self.images = prompt, list(images)
        if self.respond:
            self.respond(c, prompt)
        c.emit("assistant", text="Review the decision.")


class RegradeFollowup(Certify):
    def setUp(self):
        super().setUp()
        self.send()
        self.sender.drain()
        self.certify()
        record = self.app.extensions["communications"]
        record.gradescope_settings = self.settings
        self.app.extensions["regrade_panel"].sender.settings = self.settings
        self.request = self.fake.add_regrade(self.qsub("q20", "A", self.alice)["id"], "I got 4 but my setup was right, please look again.")
        with record.lock():
            self.assertEqual(record.round("gradescope")["status"], "ok")
        self.base = f"/api/grading/assignments/{self.aid}/regrade-requests"
        self.message = self.client.get(self.base).json["requests"][0]["id"]
        self.assistant = self.app.extensions["assistant"]
        self.suggestions = self.assistant.followups
        self.suggestions.interval, self.suggestions.automatic = 0, False
        model = patch("assistant.conversations.config.assistant", return_value={"kind": "hosted", "name": "Scripted", "reasoning_choice": "high"})
        model.start()
        self.addCleanup(model.stop)
        self.addCleanup(self.wait)

    def wait(self):
        until = time.monotonic() + 10
        while self.suggestions.busy() and time.monotonic() < until:
            time.sleep(.01)

    def items(self):
        answer = self.client.get("/api/student-support?kind=followup&suggestions=1")
        self.assertEqual(answer.status_code, 200, answer.json)
        return [i for i in answer.json["items"] if (i.get("source_key") or "").startswith("gradescope_regrade:")]

    def prepare(self, respond=None):
        self.assertTrue(self.suggestions.process_one())
        engine = PictureEngine(respond)
        with patch("assistant.conversations.engine_for", return_value=engine):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        return engine

    def decide(self, request_id="$new_request_id", **body):
        def respond(c, prompt):
            path = f"/api/grading/assignments/{self.aid}/regrade-requests/{self.message}/decide"
            result, event = c.service.run_tool(c.session, "faculty_write", {"method": "POST", "path": path,
                "body": {**body, **({"request_id": request_id} if request_id else {})}, "summary": "Answer the regrade request"})
            c.tool_event(event)
            respond.outcome = json.loads(result)
        return respond

    def test_a_regrade_request_is_a_followup_told_everything_with_its_crops(self):
        [item] = self.items()
        self.assertEqual((item["state"], item["attention"]), ("needs_reply", True))
        self.assertTrue(item["title"].startswith("Regrade request"), item["title"])
        engine = self.prepare()
        self.assertIn("REGRADE REQUEST. The selected follow-up IS this Gradescope regrade request", engine.prompt)
        self.assertIn(f"/regrade-requests/{self.message}/decide", engine.prompt)
        facts = json.loads(engine.prompt[engine.prompt.index('{"kind": "Gradescope regrade request"'):].split("\n")[0])
        self.assertEqual(facts["thread"][0]["student_wrote"], "I got 4 but my setup was right, please look again.")
        self.assertEqual((facts["grade"]["score"], facts["grade"]["of"]), ("4", "10"))
        lines = {line["id"]: line for line in facts["rubric"]["lines"]}
        self.assertTrue(lines["R3_2"]["applied"])
        self.assertTrue(all("points" in line and "description" in line for line in lines.values()))
        self.assertEqual(facts["class_on_this_question"]["final_grades"], 1)
        self.assertEqual(facts["where_the_grade_is"]["gradescope"], {"points": "4", "state": "sent"})
        self.assertGreater(facts["pictures_attached"], 0)
        self.assertEqual(len(engine.images), facts["pictures_attached"])
        self.assertTrue(all(media in ("image/png", "image/jpeg") and data for media, data in engine.images))

    def test_a_text_only_model_gets_no_pictures_and_is_told_so(self):
        with patch("assistant.conversations.config.assistant", return_value={"kind": "hosted", "name": "Text", "modality": "text", "reasoning_choice": "off"}), \
                patch.object(self.suggestions, "endpoint", return_value={"kind": "hosted", "name": "Text", "modality": "text", "reasoning_choice": "off"}):
            engine = self.prepare()
        self.assertEqual(engine.images, [])
        self.assertIn("the Assistant model chosen reads text only", engine.prompt)

    def test_clarifications_and_comments_are_given_beside_each_line(self):
        from grading import rubric_quality
        guidance = lambda self_, db, review_id, qid, rubric_id: {"clarifications": {"R3_2": "Only when the setup is complete."}, "pairs": []}  # noqa: E731
        with patch.object(type(self.frq), "rubric_guidance", guidance), \
                patch.object(rubric_quality, "exclusive_pairs", return_value=[]), \
                patch.object(type(self.frq), "line_comments", staticmethod(lambda db, aid, qid=None: {qid: {"R3_3": "Accept 107/7 or 15.29."}})):
            engine = self.prepare()
        facts = json.loads(engine.prompt[engine.prompt.index('{"kind": "Gradescope regrade request"'):].split("\n")[0])
        lines = {line["id"]: line for line in facts["rubric"]["lines"]}
        self.assertEqual(lines["R3_2"]["clarification"], "Only when the setup is complete.")
        self.assertEqual(lines["R3_3"]["instructor_comment"], "Accept 107/7 or 15.29.")

    def test_a_change_card_carries_the_grade_everywhere_and_the_request_leaves_followup(self):
        respond = self.decide(decision="change", lines=["R3_3"], reply="Hi, you were right about the setup: Question 20 is now 2 of 10.",
                              reason="The setup line applies.")
        self.prepare(respond)
        self.assertEqual(respond.outcome.get("outcome"), "waiting_for_approval", respond.outcome)
        [item] = self.items()
        [card] = item["suggestions"]["actions"]
        self.assertEqual(card["effect"]["title"], "Change the grade to 2 / 10 and answer the regrade request")
        self.assertEqual(card["effect"]["button"], "Apply")
        self.assertTrue(any("Gradescope 4 → 2" in note for note in card["effect"]["notes"]), card["effect"]["notes"])
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        run = approved.json["result"]["apply"]
        self.assertEqual([(s["step"], s["status"]) for s in run["steps"]][:2], [("gradescope", "waiting"), ("reply", "pending")])
        self.assertEqual(self.response("q20", "A")["review"]["score"]["score"], 2.0)  # saved through the ordinary review
        self.sender.drain()
        done = self.client.post(f"{self.base}/applies/{run['id']}/continue", json={}, headers=HEADERS).json
        self.assertEqual([(s["step"], s["status"]) for s in done["steps"]], [("gradescope", "done"), ("reply", "done"), ("gradebook", "done")])
        self.assertGraded("q20", "A", self.alice, [3], score=2.0)
        self.assertEqual(self.request["staff_comment"], "Hi, you were right about the setup: Question 20 is now 2 of 10.")
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        self.assertEqual(self.state()["state"], "current")
        self.assertFalse(any(item["attention"] for item in self.items()))  # answered: it leaves Follow-up
        self.assertFalse(any(card["key"].startswith("regrades:") for card in overview(self.app)["attention"]))

    def test_a_keep_card_posts_the_reply_alone(self):
        # Made without a request ID, as the live model did on 2026-10-07: Faculty supplies one, so Approve cannot fail for it.
        respond = self.decide(request_id=None, decision="keep", reply="Hi, the grade stays: the final answer must be exact.",
                              reason="The key asks for the exact value.")
        self.prepare(respond)
        [card] = self.items()[0]["suggestions"]["actions"]
        self.assertEqual(card["effect"]["title"], "Keep the grade and answer the regrade request")
        reply = next(c for c in card["effect"]["changes"] if c["label"] == "Reply")
        self.assertTrue(reply["show"])  # on the card's summary, never folded (requirement, 2026-10-07)
        self.assertNotIn("certified", card["reason"])  # keeping the grade changes no certified work
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        run = approved.json["result"]["apply"]
        self.assertEqual([(s["step"], s["status"]) for s in run["steps"]], [("reply", "done")])
        self.assertTrue(run["finished"])
        self.assertEqual(self.request["staff_comment"], "Hi, the grade stays: the final answer must be exact.")
        self.assertEqual(len(self.fake.saves()), 4)  # no grade written

    def test_a_keep_card_after_a_student_moved_since_certifying_posts_the_reply_alone(self):
        # A GRADE AS CERTIFIED (requirement, 2026-10-22): a student added since certifying leaves the kept grade where the gradebook has it.
        self.add_carol()
        self.assertEqual(self.state()["state"], "changed")
        respond = self.decide(decision="keep", reply="Hi, the grade stays: the final answer must be exact.", reason="The key asks for the exact value.")
        self.prepare(respond)
        [card] = self.items()[0]["suggestions"]["actions"]
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        run = approved.json["result"]["apply"]
        self.assertEqual([(s["step"], s["status"]) for s in run["steps"]], [("reply", "done")])
        self.assertTrue(run["finished"])

    def test_a_reply_card_or_another_students_request_is_refused(self):
        def respond(c, prompt):
            path = f"/api/grading/assignments/{self.aid}/regrade-requests/{self.message}/reply"
            result, _ = c.service.run_tool(c.session, "faculty_write", {"method": "POST", "path": path,
                "body": {"reply": "Looked again.", "request_id": "$new_request_id"}, "summary": "Reply"})
            respond.outcome = json.loads(result)
        self.prepare(respond)
        self.assertIn("decide card", json.dumps(respond.outcome))

    def test_a_reply_made_on_return_withdraws_the_suggestion(self):
        respond = self.decide(decision="keep", reply="Hi, the grade stays.", reason="Exact value required.")
        self.prepare(respond)
        [card] = self.items()[0]["suggestions"]["actions"]
        self.assertTrue(card["pending"])
        regrades = self.app.extensions["regrades"]
        preview = regrades.preview(self.aid, self.message, {"reply": "Answered on Return."})
        regrades.reply(self.aid, self.message, {"reply": "Answered on Return.", "fingerprint": preview["fingerprint"],
                                                 "request_id": str(uuid.uuid4()), "confirmation": "reply"})
        self.suggestions.process_one()  # the worker's turn reconciles
        self.assertFalse(any(item["attention"] for item in self.items()))
        with connect(self.path) as db:
            kinds = [r[0] for r in db.execute("""SELECT (SELECT kind FROM assistant_action_events e WHERE e.action_id=s.action_id ORDER BY e.id DESC LIMIT 1)
                FROM student_support_suggestion_actions s""")]
        self.assertEqual(kinds, ["expired"])  # the waiting card is withdrawn, never applied

    def edit(self, item, card, **body):
        return self.client.post(f"/api/student-support/followups/suggestions/{item['suggestions']['id']}/edit",
                                json={"action_id": card["id"], "seen": card["seen"], "subject": "", "request_id": str(uuid.uuid4()), **body}, headers=HEADERS)

    def test_the_instructor_edits_a_keep_card_into_a_line_given_back(self):
        # EDIT THE GRADE (requirement, 2026-10-08).
        self.prepare(self.decide(decision="keep", reply="Hi, the grade stays.", reason="The final answer is wrong."))
        [item] = self.items()
        [card] = item["suggestions"]["actions"]
        self.assertEqual(card["preview"]["data"]["current"], {"lines": ["R3_2"], "adjustments": []})
        self.assertEqual(self.edit(item, card, body="Hi, the grade stays.", decision="keep").status_code, 400)  # nothing changed
        refused = self.edit(item, card, body="Hi.", decision="change", lines=["R3_2", "R9_9"])
        self.assertEqual(refused.status_code, 400, refused.json)  # the decide preview checks the lines against this rubric
        edited = self.edit(item, card, body="Hi, I gave you the final answer back.", decision="change", lines=["R3_3", "R3_2"])
        self.assertEqual(edited.status_code, 200, edited.json)
        cards = {c["id"]: c for c in self.items()[0]["suggestions"]["actions"]}
        self.assertFalse(cards.get(card["id"], {}).get("pending"))  # declined as edited, its edit in its place
        new = cards[edited.json["action_id"]]
        self.assertTrue(new["pending"])
        self.assertEqual({k: new["body"][k] for k in ("decision", "lines", "reply", "reason")},
                         {"decision": "change", "lines": ["R3_2", "R3_3"], "reply": "Hi, I gave you the final answer back.", "reason": "You changed the grade on this card."})
        shown = new["preview"]["data"]
        self.assertEqual(new["effect"]["title"], f"Change the grade to {shown['to']} / 10 and answer the regrade request")
        lines = next(c for c in new["effect"]["changes"] if c["label"] == "Rubric lines")
        self.assertTrue(lines["show"])  # what the change IS sits on the card's summary
        self.assertRegex(lines["value"], r"^\+ 3\. \S")  # the line as the rubric numbers and words it, not its ID
        approved = self.client.post(f"/api/assistant/actions/{new['id']}/approve", json={"seen": new["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        review = self.response("q20", "A")["review"]
        self.assertEqual((review["score"]["selected"], review["score"]["score"], review["comment"]),
                         (["R3_2", "R3_3"], float(shown["to"]), "You changed the grade on this card."))

    def test_a_changed_grade_keeps_its_point_adjustments(self):
        # A point adjustment the grade already carries (a batch line for the whole class among them) stays with a regrade's change.
        response = self.response("q20", "A")
        self.frq.save_review(response["id"], {"version": response["version"], "result_id": response["current_result_id"], "status": "approved",
                                              "confirm_review": True, "selected_items": ["R3_2"], "adjustments": [{"points": 1, "reason": "Misprint in part b"}]})
        preview = self.client.post(f"{self.base}/{self.message}/decide/preview", json={"decision": "change", "lines": ["R3_3"], "reply": "Hi.", "reason": "x"},
                                   headers=HEADERS).json
        self.assertEqual((preview["from"], preview["to"]), ("5", "3"))
        self.assertEqual(preview["current"], {"lines": ["R3_2"], "adjustments": [{"points": 1.0, "reason": "Misprint in part b"}]})
        self.assertEqual(set(preview["line_words"]), {"R3_2", "R3_3"})
        respond = self.decide(decision="change", lines=["R3_3"], adjustment={"points": 0.5, "reason": "Partial setup"}, reply="Hi.", reason="x")
        self.prepare(respond)
        [card] = self.items()[0]["suggestions"]["actions"]
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        score = self.response("q20", "A")["review"]["score"]
        self.assertEqual((score["adjustments"], score["score"]),
                         ([{"points": 1.0, "reason": "Misprint in part b"}, {"points": 0.5, "reason": "Partial setup"}], 3.5))

    def test_the_decide_preview_forecasts_every_step_and_writes_nothing(self):
        before = len(self.fake.requests)
        preview = self.client.post(f"{self.base}/{self.message}/decide/preview", json={"decision": "change", "lines": ["R3_3"], "reply": "Hi.", "reason": "x"},
                                   headers=HEADERS).json
        self.assertEqual((preview["from"], preview["to"], preview["of"]), ("4", "2", "10"))
        self.assertEqual([s["step"] for s in preview["steps"]], ["grade", "gradescope", "reply", "gradebook"])
        self.assertEqual(next(s for s in preview["steps"] if s["step"] == "gradebook")["to"], "8")
        self.assertTrue(all(r["method"] == "GET" or r["path"] == "/login" for r in self.fake.requests[before:]))
        same = self.client.post(f"{self.base}/{self.message}/decide/preview", json={"decision": "change", "lines": ["R3_2"], "reply": "Hi."}, headers=HEADERS)
        self.assertEqual(same.status_code, 400)  # the grade already has exactly these lines

    def test_a_review_changed_during_decision_checks_is_never_overwritten(self):
        # REGRADES: the approval pins the grade the preview read, even when another review lands before its save.
        route = f"{self.base}/{self.message}/decide"
        data = {"decision": "change", "lines": ["R3_3"], "reply": "Hi.", "reason": "Check the answer."}
        preview = self.client.post(route + "/preview", json=data, headers=HEADERS).json
        panel = self.app.extensions["regrade_panel"]
        original = panel._decision_preview

        def changed(*args, **kwargs):
            checked = original(*args, **kwargs)
            response = self.response("q20", "A")
            self.frq.save_review(response["id"], {"version": response["version"], "result_id": response["current_result_id"],
                "status": "approved", "confirm_review": True, "selected_items": ["R3_2"],
                "adjustments": [{"points": 1, "reason": "A newer instructor correction."}]})
            return checked

        with patch.object(panel, "_decision_preview", side_effect=changed):
            result = self.client.post(route, json={**data, "request_id": str(uuid.uuid4()), "confirmation": "decide",
                                                   "fingerprint": preview["fingerprint"]}, headers=HEADERS)
        self.assertEqual(result.status_code, 409, result.json)
        self.assertEqual(self.response("q20", "A")["review"]["score"]["score"], 5)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_regrade_applies").fetchone()[0], 0)
        self.assertFalse(self.request["closed"])


    def test_see_their_work_names_the_answer_a_regrade_conversation_is_about(self):
        # SEE THEIR WORK (2026-10-21): Follow-up's popup reads which answer it shows by the conversation's key; the answer's own
        # route then gives its crops and rubric lines. Catalog only: nothing reaches Gradescope.
        [item] = self.items()
        before = len(self.fake.requests)
        work = self.client.get("/api/grading/regrade-work", query_string={"key": item["source_key"]}).json
        detail = self.client.get(f"{self.base}/{self.message}").json
        self.assertEqual(work["response_id"], detail["response_id"])
        self.assertEqual(work["plan_id"], self.aid)
        answer = self.client.get(f"/api/grading/responses/{work['response_id']}?details=review").json
        self.assertTrue(answer["question"]["rubric"]["rules"]["items"])
        self.assertEqual(len(self.fake.requests), before)
        unknown = self.client.get("/api/grading/regrade-work", query_string={"key": "gradescope_regrade:1:2"}).json
        self.assertNotIn("response_id", unknown)
        self.assertEqual(self.client.get("/api/grading/regrade-work", query_string={"key": "gmail:x"}).status_code, 400)

    def test_a_rubric_note_card_says_the_line_the_added_words_and_the_grades_it_reopens(self):
        # RUBRIC NOTE CARDS (requirement, 2026-10-21): the question, the line, the words a note adds, and the grades that go back to review.
        q = next(q for q in self.frq.assignment(self.aid)["questions"] if q["question_key"] == "q20" and q["exam_version"] == "A")
        rubric = q["rubric"]
        before = self.frq.edit_rubric(self.aid, q["question_id"], {"version": q["version"], "rubric_id": rubric["id"], "sha256": rubric["sha256"],
                                                                   "comments": {"R3_2": "Charge a missing setup."}})
        q = next(x for x in before["questions"] if x["question_id"] == q["question_id"])

        def respond(c, prompt):
            path = f"/api/grading/assignments/{self.aid}/questions/{q['question_id']}/rubric-edits"
            result, event = c.service.run_tool(c.session, "faculty_write", {"method": "POST", "path": path, "summary": "Revise the selected rubric",
                "body": {"version": q["version"], "rubric_id": rubric["id"], "sha256": rubric["sha256"],
                         "comments": {"R3_2": "Charge a missing setup. A table of values counts as a setup."}}})
            c.tool_event(event)
            respond.outcome = json.loads(result)
        self.prepare(respond)
        self.assertEqual(respond.outcome.get("outcome"), "waiting_for_approval", respond.outcome)
        [card] = self.items()[0]["suggestions"]["actions"]
        effect = card["effect"]
        self.assertTrue(effect["title"].startswith("Change the note on "), effect["title"])
        self.assertNotIn("Sha256", [c["label"] for c in effect["changes"]])
        self.assertEqual(effect["who"], "every student who answered it")
        labels = [c["label"] for c in effect["changes"]]
        self.assertTrue(labels[0].startswith("Line "), labels)  # the line's own words are a row of their own: labels stay short
        self.assertLessEqual(max(map(len, labels)), 10, labels)
        note = next(c for c in effect["changes"] if c["label"] == "Note adds")
        self.assertEqual((note["value"], note["show"]), ("A table of values counts as a setup.", True))
        grades = next(c for c in effect["changes"] if c["label"] == "Grades")
        self.assertTrue(grades["show"])
        self.assertRegex(grades["value"], r"^(\d+ automatic grades? on this question go(es)? back to review|No automatic grade)")

    def two_cards(self):
        """The request of setUp (version A's paper) and a second on the same question (version B's), each prepared into its decide
        card: the first changes the grade, the second keeps it. Returns {"change": card, "keep": card}."""
        self.fake.add_regrade(self.qsub("q20", "B", self.bob)["id"], "Please look at my setup again.")
        record = self.app.extensions["communications"]
        with record.lock():
            self.assertEqual(record.round("gradescope")["status"], "ok")
        def respond(c, prompt):
            message = re.search(r"/regrade-requests/([^/\s\"]+)/decide", prompt).group(1)
            body = ({"decision": "change", "lines": ["R3_3"], "reply": "Hi, you were right about the setup.", "reason": "The setup line applies."} if message == self.message
                    else {"decision": "keep", "reply": "Hi, the grade stays.", "reason": "The final answer is not exact."})
            result, event = c.service.run_tool(c.session, "faculty_write", {"method": "POST", "path": f"{self.base}/{message}/decide",
                                               "body": {**body, "request_id": "$new_request_id"}, "summary": "Answer the regrade request"})
            c.tool_event(event)
        self.prepare(respond)
        self.prepare(respond)
        cards = [c for item in self.items() for c in item["suggestions"]["actions"] if c["pending"]]
        self.assertEqual(len(cards), 2, cards)
        return {c["body"]["decision"]: c for c in cards}

    def approve(self, card):
        answer = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def test_approve_all_takes_keeps_first_since_an_approved_change_outdates_the_exams_other_cards(self):
        # REGRADES "APPROVE ALL" (requirement, 2026-10-29): the tab approves each card through its own Approve, keeps first. A keep changes no grade, so the
        # change after it still goes through; the change's own saved grade then leaves the next card of the exam outdated (shown
        # again, nothing submitted) until its Apply has certified it.
        cards = self.two_cards()
        kept = self.approve(cards["keep"])
        self.assertFalse(kept["stale"], kept)
        self.assertTrue(kept["result"]["apply"]["finished"])
        changed = self.approve(cards["change"])
        self.assertFalse(changed["stale"], changed)
        self.assertEqual(self.response("q20", "A")["review"]["score"]["score"], 2.0)
        self.assertEqual(self.request["staff_comment"], None)  # the change's reply waits for Gradescope to show its grade
        # A LOST ANSWER is settled by reading the card's own record (instructor-only, read-only).
        read = self.client.get(f"/api/assistant/actions/{cards['keep']['id']}")
        self.assertEqual((read.status_code, read.json["action"]["status"], read.json["action"]["pending"]), (200, "succeeded", False))
        self.assertEqual(self.client.get("/api/assistant/actions/nothing-here").status_code, 404)

    def test_a_change_approved_first_leaves_the_exams_keep_to_look_at_again(self):
        cards = self.two_cards()
        self.assertFalse(self.approve(cards["change"])["stale"])
        again = self.approve(cards["keep"])
        self.assertTrue(again["stale"], again)  # nothing submitted: the card waits with its new preview
        self.assertTrue(again["action"]["pending"])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in loader.getTestCaseNames(RegradeFollowup):
        if name in RegradeFollowup.__dict__:
            suite.addTest(RegradeFollowup(name))
    return suite


if __name__ == "__main__":
    unittest.main()
