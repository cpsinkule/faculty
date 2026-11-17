"""One student's cell sent to Canvas (docs/decisions/canvas.md ONE CELL; a regrade request's Apply, REGRADES 2026-10-07), offline
against the loopback FakeCanvas: that cell alone goes, with the sync's receipts; a grade changed in Canvas waits for Sync with Canvas's
Keep Faculty's; nothing else of the column or the course moves, and the column stays as in step as it was."""
import time
import unittest
from unittest.mock import patch

from test_canvas_sync import WEBASSIGN, SyncBase
from test_storage import HEADERS

from gradebook.service import GradebookService
from rosters import connect


class CellTests(SyncBase):
    def cell_preview(self, name="Example, Alice", item="Quiz 1", status=200):
        answer = self.client.post(self.base + "/canvas-cell/preview", json={"item_id": self.items[item], "row": self.rows[name]}, headers=HEADERS)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def cell_send(self, found, request_id=None, status=200, name="Example, Alice", item="Quiz 1"):
        answer = self.client.post(self.base + "/canvas-cell", json={"item_id": self.items[item], "row": self.rows[name], "fingerprint": found["fingerprint"],
                                                                    "request_id": request_id or f"cell-{time.monotonic_ns()}", "confirmation": "send-cell"},
                                  headers=HEADERS)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def finished(self, sent):
        view = sent["send"]
        for _ in range(200):
            if view["status"] != "running":
                break
            time.sleep(0.05)
            view = self.client.get(f"{self.base}/canvas-cell/{view['id']}").json["send"]
        return view

    def test_one_cell_goes_alone(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.type("Example, Carol", "Quiz 1", status="scored", points="3")  # another change in the column: not part of this send
        before = self.canvas(self.course_b, self.quiz_b, self.carol)["score"]
        requests = len(self.fake.requests)
        found = self.cell_preview()
        self.assertEqual((found["action"], found["canvas"], found["faculty"], found["ready"], found["problems"]),
                         ("update", {"status": "scored", "points": "9"}, {"status": "scored", "points": "10"}, True, []))
        self.assertFalse(any(f"/courses/{self.course_b}" in r["path"] for r in self.fake.requests[requests:]))  # one course read
        sent = self.finished(self.cell_send(found))
        self.assertEqual((sent["status"], sent["progress"]["accepted"]), ("done", 1))
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 10.0)
        self.assertEqual(self.canvas(self.course_b, self.quiz_b, self.carol)["score"], before)
        self.assertEqual(self.receipts(self.alice), ["uncertain", "accepted"])
        with connect(self.app.config["DATABASE"]) as db:
            row = db.execute("SELECT section_id FROM gradebook_canvas_sends WHERE id=?", (sent["id"],)).fetchone()
            syncs = db.execute("SELECT COUNT(*) FROM gradebook_requests WHERE kind='import'").fetchone()[0]
        self.assertEqual(row["section_id"], self.sections[507])
        # Nothing recorded as a sync (no `in_step`): the next Sync with Canvas finds Alice the same and still sends Carol's change.
        plan = self.preview()
        quiz_b = next(c for c in plan["columns"] if c["title"] == "Quiz 1" and c["course_id"] == self.course_b)
        self.assertEqual(quiz_b["counts"]["update"], 1)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_requests WHERE kind='import'").fetchone()[0], syncs)
        self.assertEqual(self.cell_preview()["action"], "same")

    def test_the_same_request_is_the_same_send(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        found = self.cell_preview()
        first = self.cell_send(found, request_id="cell-request-1")
        self.finished(first)
        again = self.cell_send(found, request_id="cell-request-1")
        self.assertEqual((again["replay"], again["send"]["id"]), (True, first["send"]["id"]))
        self.assertEqual(self.receipts(self.alice), ["uncertain", "accepted"])

    def test_a_grade_changed_in_canvas_waits_for_the_sync(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.fake.grade(self.course_a, self.quiz_a, self.alice, 5)  # someone changed it in Canvas
        found = self.cell_preview()
        self.assertEqual((found["action"], found["ready"], found["canvas"]), ("held", False, {"status": "scored", "points": "5"}))
        self.cell_send(found, status=409)
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 5)

    def test_a_change_after_the_preview_is_a_conflict(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        found = self.cell_preview()
        self.type("Example, Alice", "Quiz 1", status="scored", points="8")
        self.cell_send(found, status=409)
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 9)

    def test_a_running_send_holds_the_cell(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        found = self.cell_preview()
        service = self.app.extensions["gradebook"]
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("INSERT INTO gradebook_canvas_sends VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("running-send", self.term, None, "running-1", "x", "x", "[]", "running", "{}", "workspace", "2026-10-07T00:00:00Z", None))
        service.canvas.writes.begin(service._send_key("running-send"), "a test sync", "stopping")
        self.addCleanup(service.canvas.writes.end, service._send_key("running-send"))
        answer = self.cell_send(found, status=409)
        self.assertIn("sending grades", answer["error"] if "error" in answer else str(answer))
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 9)


class QuizGradeTests(SyncBase):
    """A CANVAS QUIZ'S GRADE (canvas ONE CELL; requirement, 2026-10-13, of a 2/2 the Assistant could only offer as a change to
    Faculty): one student's grade on a quiz Canvas grades is set in Canvas, with the sync's receipts, and the cell stays Canvas's."""
    TITLE = "Lecture Video and Quiz 1"

    def setUp(self):
        super().setUp()
        self.video = self.fake.add_assignment(self.course_a, self.TITLE, 2, group="Lecture Quizzes", quiz=True)
        self.fake.grade(self.course_a, self.video, self.alice, 0)
        self.fake.grade(self.course_a, self.video, self.bob, 1)
        self.sync()
        self.items[self.TITLE] = next(i["id"] for i in self.client.get(self.base).json["setup"]["items"] if i["title"] == self.TITLE)

    def body(self, points="2", name="Example, Alice", item=None):
        return {"item_id": self.items[item or self.TITLE], "row": self.rows[name], "points": points}

    def quiz_preview(self, status=200, **named):
        answer = self.client.post(self.base + "/canvas-quiz-grade/preview", json=self.body(**named), headers=HEADERS)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def quiz_send(self, found, request_id=None, status=200, **named):
        answer = self.client.post(self.base + "/canvas-quiz-grade", json={**self.body(**named), "fingerprint": found["fingerprint"],
            "request_id": request_id or f"quiz-{time.monotonic_ns()}", "confirmation": "set-quiz-grade"}, headers=HEADERS)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def grade_writes(self):
        return [w for w in self.fake.writes if f"/assignments/{self.video}/" in w[1]]

    def test_one_students_quiz_grade_is_set_in_canvas_and_the_next_sync_reads_it_back(self):
        found = self.quiz_preview()
        self.assertEqual((found["action"], found["canvas"], found["faculty"], found["points"], found["possible"], found["ready"], found["problems"]),
                         ("update", {"status": "scored", "points": "0"}, {"status": "scored", "points": "0"}, "2", "2", True, []))
        self.assertEqual(found["assignment"]["name"], self.TITLE)
        self.assertEqual(self.grade_writes(), [])  # the preview writes nothing
        sent = self.quiz_send(found)
        self.assertEqual((sent["status"], sent["send"]["status"], sent["receipt"]["state"], sent["receipt"]["canvas"], sent["replay"]),
                         ("accepted", "done", "accepted", {"status": "scored", "points": "2"}, False))
        self.assertEqual(self.canvas(self.course_a, self.video, self.alice)["score"], 2.0)
        self.assertEqual(self.canvas(self.course_a, self.video, self.bob)["score"], 1)  # that student alone
        [write] = self.grade_writes()
        self.assertEqual(list(write[2]), [self.alice])
        self.assertEqual(self.receipts(self.alice), ["uncertain", "accepted"])
        with connect(self.app.config["DATABASE"]) as db:
            row = db.execute("SELECT section_id,actor FROM gradebook_canvas_sends WHERE id=?", (sent["send"]["id"],)).fetchone()
        self.assertEqual(row["section_id"], self.sections[507])
        self.assertEqual(self.cell(self.TITLE, "Example, Alice")["points"], "0")  # the cell is Canvas's: nothing typed into it
        self.assertEqual(self.quiz_preview()["action"], "same")
        plan, _ = self.sync()  # the mirror reads the 2 back; nothing is held or sent for the quiz
        self.assertFalse([h for h in plan["held"] if h["title"] == self.TITLE])
        self.assertFalse([c for c in plan["columns"] if c["title"] == self.TITLE])
        self.assertEqual(self.cell(self.TITLE, "Example, Alice")["points"], "2")
        self.assertEqual(len(self.grade_writes()), 1)

    def test_a_student_canvas_shows_no_grade_for_gets_one(self):
        self.fake.grade(self.course_a, self.video, self.bob, None)  # Canvas shows nothing for him now
        found = self.quiz_preview(points="1.5", name="Example, Bob")
        self.assertEqual((found["action"], found["canvas"], found["ready"]), ("add", None, True))
        self.assertEqual(self.quiz_send(found, points="1.5", name="Example, Bob")["status"], "accepted")
        self.assertEqual(self.canvas(self.course_a, self.video, self.bob)["score"], 1.5)

    def test_the_same_request_is_the_same_send(self):
        found = self.quiz_preview()
        first = self.quiz_send(found, request_id="quiz-request-1")
        again = self.quiz_send(found, request_id="quiz-request-1")
        self.assertEqual((again["replay"], again["send"]["id"], again["status"]), (True, first["send"]["id"], "accepted"))
        self.assertEqual(len(self.grade_writes()), 1)
        other = self.client.post(self.base + "/canvas-quiz-grade", json={**self.body(points="1"), "fingerprint": found["fingerprint"],
            "request_id": "quiz-request-1", "confirmation": "set-quiz-grade"}, headers=HEADERS)
        self.assertEqual(other.status_code, 409, other.json)

    def test_a_grade_changed_in_canvas_after_the_preview_is_a_conflict(self):
        found = self.quiz_preview()
        self.fake.grade(self.course_a, self.video, self.alice, 1)  # he took it meanwhile
        self.quiz_send(found, status=409)
        self.assertEqual(self.canvas(self.course_a, self.video, self.alice)["score"], 1)
        self.assertEqual(self.grade_writes(), [])

    def test_what_canvas_does_not_take_is_not_reported_as_set(self):
        found = self.quiz_preview()
        self.fake.refuse.add(("POST", f"/assignments/{self.video}/submissions/update_grades"))  # Canvas: not authorized
        sent = self.quiz_send(found)
        self.assertEqual((sent["status"], sent["receipt"]["state"]), ("failed", "failed"))
        self.assertEqual(self.canvas(self.course_a, self.video, self.alice)["score"], 0)
        self.fake.refuse.clear()
        # Taken and not applied yet: uncertain, never sent again by itself, and settled by what a later read shows.
        found = self.quiz_preview()
        self.fake.hold_grades = True
        with patch.object(GradebookService, "SEND_WAIT", 0.1):
            sent = self.quiz_send(found)
        self.fake.hold_grades = False
        self.assertEqual((sent["status"], sent["receipt"]["state"]), ("uncertain", "uncertain"))
        self.assertEqual(self.fake.apply_held_grades(), 1)
        self.assertEqual(self.quiz_preview()["action"], "same")

    def test_only_a_canvas_quiz_within_its_points_and_only_with_a_confirmation(self):
        refused = self.quiz_preview(status=400, item="Quiz 1", points="9")
        self.assertIn("not a quiz Canvas grades", refused["error"])
        wa = self.fake.add_assignment(self.course_a, "Sect. 1.1 Matrices", 10, group="WebAssign Homework", tool=WEBASSIGN)
        self.fake.grade(self.course_a, wa, self.alice, 9.5)
        self.sync()
        self.items["Sect. 1.1 Matrices"] = next(i["id"] for i in self.client.get(self.base).json["setup"]["items"] if i["title"] == "Sect. 1.1 Matrices")
        self.assertIn("WebAssign", self.quiz_preview(status=400, item="Sect. 1.1 Matrices", points="10")["error"])
        self.assertIn("worth 2 points", self.quiz_preview(status=400, points="3")["error"])
        self.quiz_preview(status=400, points="two")
        found = self.quiz_preview(points="0")
        self.assertEqual((found["action"], found["ready"]), ("same", False))
        self.quiz_send(found, status=409, points="0")
        found = self.quiz_preview()
        unconfirmed = self.client.post(self.base + "/canvas-quiz-grade", json={**self.body(), "fingerprint": found["fingerprint"],
                                                                               "request_id": "quiz-unconfirmed"}, headers=HEADERS)
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.json)
        self.assertEqual(self.grade_writes(), [])
        # The cell itself is still read-only, and the one-cell send of a Faculty column is as it was.
        typed = self.client.post(self.base + "/scores", json={"request_id": "typed-quiz", "changes": [
            {"item_id": self.items[self.TITLE], "row": self.rows["Example, Alice"], "base": self.cell(self.TITLE, "Example, Alice")["id"],
             "status": "scored", "points": "2"}]}, headers=HEADERS)
        self.assertEqual(typed.status_code, 400, typed.json)
        cell = self.client.post(self.base + "/canvas-cell/preview", json={"item_id": self.items[self.TITLE], "row": self.rows["Example, Alice"]},
                                headers=HEADERS).json
        self.assertEqual((cell["action"], cell["problems"]), ("none", ["This column is not sent to Canvas."]))

    def test_an_assignment_that_cannot_take_the_grade_in_points_is_named_and_nothing_is_set(self):
        assignment = self.fake.assignment(self.course_a, self.video)
        assignment["points_possible"] = 3
        assignment["grading_type"] = "pass_fail"
        found = self.quiz_preview()
        self.assertEqual((found["problems"], found["ready"], found["assignment"]["points"]),
                         ([f"“{self.TITLE}” is not graded in points in Canvas", f"“{self.TITLE}” is worth 3 points in Canvas and 2 in the gradebook"],
                          False, "3"))
        self.quiz_send(found, status=409)
        self.assertEqual(self.grade_writes(), [])

    def test_the_assistant_proposes_it_as_one_card_and_your_approval_sets_it(self):
        service = self.app.extensions["assistant"]
        session = service.open_session("test")
        path = f"/api/gradebook/terms/{self.term}/canvas-quiz-grade"
        prepared = service.write(session, "POST", path, self.body(), "Give Alice 2/2 on the video quiz as a one-time courtesy")
        self.assertEqual(prepared["outcome"], "waiting_for_approval", prepared)
        self.assertEqual(self.grade_writes(), [])
        card = service._view(*service.action(prepared["action_id"]))
        self.assertEqual((card["tier"], card["preview"]["data"]["action"], card["preview"]["data"]["canvas"]["points"]), ("approve", "update", "0"))
        self.assertNotIn("confirmation", card["body"])
        from assistant.followup_effects import Describer
        with connect(self.app.config["DATABASE"]) as db:
            effect = Describer(db, service.transport, {"enrollment_id": self.rows["Example, Alice"].rsplit(":", 1)[1]}, {"name": "Alice Example"},
                               None).describe(card)
        self.assertEqual((effect["title"], effect["where"], effect["button"], effect["changes"]),
                         (f"Change the grade of “{self.TITLE}” to 2 / 2 in Canvas", "Canvas", "Set grade in Canvas",
                          [{"label": self.TITLE, "value": "2 / 2", "show": True, "before": "0 / 2"}]))
        approved = self.client.post("/api/assistant/actions/" + card["id"] + "/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertEqual((approved.status_code, approved.json["status"], approved.json["result"]["status"]), (200, 200, "accepted"), approved.json)
        self.assertEqual(self.canvas(self.course_a, self.video, self.alice)["score"], 2.0)
        # A grade changed in Canvas after the card was made: Approve shows the new preview and sets nothing.
        prepared = service.write(session, "POST", path, self.body(name="Example, Bob"), "Give Bob 2/2 on the video quiz")
        card = service._view(*service.action(prepared["action_id"]))
        self.fake.grade(self.course_a, self.video, self.bob, 0)
        again = self.client.post("/api/assistant/actions/" + card["id"] + "/approve", json={"seen": card["seen"]}, headers=HEADERS)
        self.assertTrue(again.json["stale"], again.json)
        self.assertEqual(self.canvas(self.course_a, self.video, self.bob)["score"], 0)
        self.assertEqual(len(self.grade_writes()), 1)

if __name__ == "__main__":
    unittest.main()
