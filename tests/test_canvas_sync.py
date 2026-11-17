"""Sync with Canvas (docs/decisions/canvas.md SYNC WITH CANVAS; gradebook/README.md "Syncing with Canvas"; requirement, 2026-10-06), offline against the loopback FakeCanvas: the
first sync taking Canvas's grades into the columns it links, Faculty's changes going to Canvas without a tick, grades changed in
Canvas held for the instructor, WebAssign's columns mirroring Canvas, missing assignments created, and every value sent keeping a
receipt whatever happens after Canvas is asked."""
import gzip
import json
import signal
import sqlite3
import threading
import time
import unittest
from unittest.mock import Mock, patch

from test_canvas import ALICE, BOB, CAROL, GradebookCanvasBase
from test_storage import HEADERS

from canvas import stopping, web
from gradebook import canvas_export, canvas_snapshot, reconciliation
from rosters import connect

WEBASSIGN = "https://gateway.cengage.com/rest/lti/launch?ref=1"


def entry(plan, title, course):
    return next(c for c in plan["columns"] if c["title"] == title and c["course_id"] == course)


def held(plan, name):
    return [h for h in plan["held"] if h["name"] == name]


class SyncBase(GradebookCanvasBase):
    """The term after its first sync (Start from Canvas): Canvas's grades taken into the columns it linked."""

    def setUp(self):
        super().setUp()
        self.sync()
        view = self.client.get(self.base).json
        self.items = {item["title"]: item["id"] for item in view["setup"]["items"]}

    def type(self, name, item, **value):
        view = self.client.get(self.base).json
        current = view["cells"].get(self.items[item], {}).get(self.rows[name])
        answer = self.client.post(self.base + "/scores", json={"request_id": f"t-{time.monotonic_ns()}", "changes": [
            {"item_id": self.items[item], "row": self.rows[name], "base": current["id"] if current else None, **value}]}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer

    def cell(self, item, name):
        return (self.client.get(self.base).json["cells"].get(self.items[item]) or {}).get(self.rows[name])

    def receipts(self, user):
        with connect(self.app.config["DATABASE"]) as db:
            return [r[0] for r in db.execute("SELECT state FROM gradebook_canvas_sent WHERE canvas_user_id=? ORDER BY rowid", (user,))]

    def canvas(self, course, assignment, user):
        return self.fake.submissions[course][(user, assignment)]

    def blanks_not_counted(self):
        """`ungraded: "exclude"`: no empty cell counts as 0 (Quiz 1's grades are posted in Canvas, so under ZERO ONCE DUE an emptied
        cell of it is a 0, and Sync sends the 0: ZEROS GO TO CANVAS)."""
        view = self.client.get(self.base).json
        setup = {key: view["setup"][key] for key in ("categories", "items", "mode", "ungraded", "scale", "letter_rounding") if key in view["setup"]}
        saved = self.client.post(self.base + "/setup", json={"base_revision": view["setup"]["revision"], "request_id": f"exclude-{time.monotonic_ns()}",
                                                             "setup": {**setup, "ungraded": "exclude"}}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)


class FirstSyncTests(GradebookCanvasBase):
    """Start from Canvas: the term's first sync matches Canvas's assignments to columns (or makes them) and takes their grades into
    the cells Faculty never filled; a grade both hold differently waits."""

    def test_invalid_response_section_refuses_sync_before_writing(self):
        """CANVAS: an invalid section cannot commit a sync that reports failure only when building its answer."""
        plan = self.preview()
        for section in ("not-this-terms-section", [], {"id": self.sections[507]}):
            with self.subTest(section=section):
                with connect(self.app.config["DATABASE"]) as db:
                    before = [db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                              ("gradebook_setups", "gradebook_requests", "gradebook_canvas_sends")]
                writes = len(self.fake.writes)
                answer = self.client.post(self.base + "/canvas-sync", json={
                    "options": plan["options"], "fingerprint": plan["fingerprint"], "request_id": "invalid-sync-section",
                    "confirmation": "sync", "section_id": section}, headers=HEADERS)
                self.assertEqual(answer.status_code, 400, answer.json)
                with connect(self.app.config["DATABASE"]) as db:
                    after = [db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                             ("gradebook_setups", "gradebook_requests", "gradebook_canvas_sends")]
                self.assertEqual(after, before)
                self.assertEqual(len(self.fake.writes), writes)

    def test_sync_rechecks_a_send_started_while_it_read_canvas(self):
        """CANVAS: one live send per term also holds when another request starts after the initial busy check."""
        plan = self.preview()
        svc = self.app.extensions["gradebook"]
        original = svc._sync_plan
        inserted = False
        send_id = "other-send-started-during-read"

        def during_read(*args, **kwargs):
            nonlocal inserted
            result = original(*args, **kwargs)
            if not inserted:
                inserted = True
                svc.canvas.writes.begin(svc._send_key(send_id), "another Canvas send", "stopping")
                with connect(svc.path) as db:
                    db.execute("INSERT INTO gradebook_canvas_sends VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (send_id, self.term, None, "other-sync", "sha", "fingerprint", "[]", "running", "{}",
                                "workspace", "2026-10-24T00:00:00+00:00", None))
            return result

        try:
            with patch.object(svc, "_sync_plan", side_effect=during_read):
                answer = self.apply(plan, "sync-raced-with-another")
            self.assertEqual(answer.status_code, 409, answer.json)
            self.assertIn("already sending", answer.json["error"])
            with connect(svc.path) as db:
                self.assertIsNone(db.execute("SELECT id FROM gradebook_requests WHERE request_id=?",
                                             ("sync-raced-with-another",)).fetchone())
                self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_canvas_sends").fetchone()[0], 1)
            self.assertEqual(self.fake.writes, [])
        finally:
            svc.canvas.writes.end(svc._send_key(send_id))

    def test_the_first_sync_links_takes_canvas_grades_and_holds_what_differs(self):
        view = self.client.get(self.base).json["canvas"]
        self.assertEqual((view["linked_sections"], view["started"], view["last_sync"]), (2, False, None))
        plan = self.preview()
        brought = plan["from_canvas"]
        column = {(c["course_id"], c["title"]): c for c in brought["columns"]}
        self.assertEqual(column[(self.course_a, "Quiz 1")]["mapping"], {"action": "item", "item_id": self.items["Quiz 1"]})
        self.assertEqual(column[(self.course_a, "Test 1")]["mapping"], {"action": "item", "item_id": self.items["Exam 1"]})  # Test 1 is Exam 1
        homework = next(e for e in brought["new_items"] if e["title"] == "Homework 2")
        reading = next(e for e in brought["new_items"] if e["title"] == "Reading check")
        self.assertEqual(homework["sections"], [])  # both courses have it: every section
        self.assertEqual(reading["sections"], [self.sections[508]])  # only 508's course has it: 508's students only
        added = {(c["name"], c["column"]): c["proposed"] for c in brought["changes"]}
        self.assertEqual(added[("Example, Alice", f"{self.course_a}:{self.quiz_a}")], {"status": "scored", "points": "9"})
        self.assertEqual(added[("Example, Alice", f"{self.course_a}:{self.test_a}")], {"status": "scored", "points": "88.1235"})
        self.assertEqual(added[("Example, Bob", f"{self.course_a}:{self.hw_a}")], {"status": "excused", "points": None})
        self.assertNotIn(("Example, Bob", f"{self.course_a}:{self.quiz_a}"), added)  # Faculty has its own 7 there
        self.assertNotIn(("Example, Bob", f"{self.course_a}:{self.test_a}"), added)  # pending review is not a score
        self.assertNotIn(("Example, Carol", f"{self.course_b}:{self.hw_b}"), added)  # the missing flag is not recorded
        # Bob's 7 (typed in Faculty) and Canvas's 8 (never Faculty's): held, nothing else differs.
        self.assertEqual([(h["name"], h["canvas"], h["faculty"], h["take"]) for h in plan["held"]],
                         [("Example, Bob", {"status": "scored", "points": "8"}, {"status": "scored", "points": "7"}, True)])
        self.assertEqual(plan["to_canvas"]["sending"], 0)
        exam_b = entry(plan, "Exam 1", self.course_b)
        self.assertIn("put the column in a category first", exam_b["problems"][0])
        text = json.dumps(plan)
        for uin in (ALICE, BOB, CAROL, "999888777"):
            self.assertNotIn(uin, text)
        answer = self.apply(plan, "start-from-canvas")
        self.assertEqual(self.ended(answer), None)  # nothing to send
        synced = answer.json["sync"]
        self.assertEqual((synced["sending"], synced["held"], synced["written"], synced["new_columns"]), (0, 1, 5, 2))
        cells = answer.json["gradebook"]["cells"]
        self.assertEqual(cells[self.items["Quiz 1"]][self.rows["Example, Bob"]]["points"], "7")  # Faculty's, still
        self.assertEqual(cells[self.items["Exam 1"]][self.rows["Example, Alice"]]["points"], "88.1235")
        view = self.client.get(self.base).json["canvas"]
        self.assertEqual((view["started"], view["last_sync"]["held"], view["changed_since"]), (True, 1, 0))
        self.assertEqual(self.apply(plan, "start-from-canvas").status_code, 200)  # a replay
        # The next summary has nothing to take or send; Bob still waits.
        again = self.preview()
        self.assertEqual((again["from_canvas"]["changes"], again["to_canvas"]["sending"], len(again["held"])), ([], 0, 1))

    def test_an_assignment_canvas_gives_no_points_waits_and_holds_nothing_back(self):
        bonus = self.fake.add_assignment(self.course_a, "Bonus", None, group="Extra")
        self.fake.grade(self.course_a, bonus, self.alice, 2)
        plan = self.preview()
        column = next(c for c in plan["from_canvas"]["columns"] if c["key"] == f"{self.course_a}:{bonus}")
        self.assertEqual((column["mapping"], column["suggested"], plan["ready"]), ({"action": "skip"}, "points", True))
        self.assertIn("Homework 2", [e["title"] for e in plan["from_canvas"]["new_items"]])

    def test_the_first_sync_takes_the_grade_as_entered_before_canvas_late_penalty(self):
        self.fake.grade(self.course_a, self.quiz_a, self.alice, 8.1, entered=9)
        self.sync()
        view = self.client.get(self.base).json
        self.assertEqual(view["cells"][self.items["Quiz 1"]][self.rows["Example, Alice"]]["points"], "9")
        self.assertEqual(entry(self.preview(), "Quiz 1", self.course_a)["counts"]["same"], 1)  # 9 entered, 8.1 after the penalty

    def test_signed_out_is_a_401(self):
        self.fake.sessions.clear()
        self.preview(status=401)


class RuleTests(SyncBase):
    def test_a_grade_changed_in_faculty_goes_without_a_tick(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.assertEqual(self.client.get(self.base).json["canvas"]["changed_since"], 1)
        plan, sent = self.sync()
        quiz_a = entry(plan, "Quiz 1", self.course_a)
        self.assertEqual((quiz_a["counts"]["update"], quiz_a["to_send"]), (1, 1))
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 10.0)
        # Recorded as uncertain before Canvas was asked, then settled by the read-back (2026-10-04 review).
        self.assertEqual(self.receipts(self.alice), ["uncertain", "accepted"])
        self.assertEqual(sent["progress"]["accepted"], 1)
        self.assertEqual(self.client.get(self.base).json["canvas"]["changed_since"], 0)

    def test_a_grade_changed_in_canvas_is_held_until_the_instructor_picks(self):
        self.fake.grade(self.course_b, self.quiz_b, self.carol, 4)  # someone changed Carol's 6 in Canvas
        plan = self.preview()
        [carol] = held(plan, "Example, Carol")
        self.assertEqual((carol["canvas"], carol["faculty"]), ({"status": "scored", "points": "4"}, {"status": "scored", "points": "6"}))
        # Undecided: nothing is sent, Faculty keeps its 6, the next sync asks again.
        self.sync()
        self.assertEqual(self.cell("Quiz 1", "Example, Carol")["points"], "6")
        self.assertEqual(self.canvas(self.course_b, self.quiz_b, self.carol)["score"], 4)
        # Take Canvas's: Faculty takes the 4 (an import from Canvas), and the sync after finds them alike.
        plan, _ = self.sync(held={carol["key"]: "canvas"})
        self.assertEqual(self.cell("Quiz 1", "Example, Carol")["points"], "4")
        self.assertEqual(held(self.preview(), "Example, Carol"), [])
        # Changed in Canvas again, and this time Keep Faculty's: the 4 goes back.
        self.fake.grade(self.course_b, self.quiz_b, self.carol, 2)
        [carol] = held(self.preview(), "Example, Carol")
        self.sync(held={carol["key"]: "faculty"})
        self.assertEqual(self.canvas(self.course_b, self.quiz_b, self.carol)["score"], 4.0)

    def test_keep_faculty_s_sends_it_and_faculty_s_later_changes_go_without_asking(self):
        [bob] = held(self.preview(), "Example, Bob")
        self.sync(held={bob["key"]: "faculty"})
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.bob)["score"], 7.0)
        self.type("Example, Bob", "Quiz 1", status="excused")
        plan, _ = self.sync()
        self.assertEqual(entry(plan, "Quiz 1", self.course_a)["counts"]["update"], 1)
        self.assertTrue(self.canvas(self.course_a, self.quiz_a, self.bob)["excused"])
        # Scored again in Faculty: the excuse is taken away in Canvas too ("excuse": false beside the grade).
        self.type("Example, Bob", "Quiz 1", status="scored", points="6")
        self.sync()
        bob = self.canvas(self.course_a, self.quiz_a, self.bob)
        self.assertEqual((bob["excused"], bob["score"]), (False, 6.0))

    def test_a_grade_taken_out_in_faculty_is_taken_out_in_canvas_and_missing_sends_zero(self):
        self.blanks_not_counted()
        self.type("Example, Carol", "Quiz 1", status="missing")
        plan, _ = self.sync()
        self.assertEqual(entry(plan, "Quiz 1", self.course_b)["counts"]["update"], 1)
        self.assertEqual(self.canvas(self.course_b, self.quiz_b, self.carol)["score"], 0.0)
        self.type("Example, Carol", "Quiz 1", status="cleared")
        plan, _ = self.sync()
        quiz_b = entry(plan, "Quiz 1", self.course_b)
        self.assertEqual((quiz_b["counts"]["clear"], quiz_b["clears"]), (1, 1))
        self.assertIsNone(self.canvas(self.course_b, self.quiz_b, self.carol)["score"])
        self.assertEqual(entry(self.preview(), "Quiz 1", self.course_b)["counts"]["empty"], 1)

    def test_a_grade_faculty_never_had_is_filled_from_canvas_never_taken_out(self):
        """Canvas holds a grade for a student Faculty never filled and never sent: it comes into Faculty (requirement, 2026-10-06), never taken out of Canvas."""
        self.fake.grade(self.course_a, self.test_a, self.bob, 70)  # Bob's pending review graded in Canvas
        plan = self.preview()
        exam = entry(plan, "Exam 1", self.course_a)
        self.assertEqual((exam["counts"]["fill"], exam["counts"]["clear"], exam["fills"]), (1, 0, 1))
        self.assertEqual([h for h in held(plan, "Example, Bob") if h["title"] == "Exam 1"], [])
        self.assertEqual(plan["to_canvas"]["filling"], 1)
        self.ended(self.apply(plan, "fill-bob-1"))
        self.assertEqual(self.cell("Exam 1", "Example, Bob")["points"], "70")
        self.assertEqual(self.canvas(self.course_a, self.test_a, self.bob)["score"], 70)
        self.assertEqual(entry(self.preview(), "Exam 1", self.course_a)["counts"]["same"], 2)
        # A grade the instructor emptied in Faculty is their decision: held, never filled.
        self.type("Example, Bob", "Exam 1", status="cleared")
        self.fake.grade(self.course_a, self.test_a, self.bob, 71)
        [bob] = [h for h in held(self.preview(), "Example, Bob") if h["title"] == "Exam 1"]
        self.assertEqual((bob["faculty"], bob["canvas"]), (None, {"status": "scored", "points": "71"}))

    def test_canvas_ignoring_a_clear_keeps_the_clear_planned(self):
        self.blanks_not_counted()
        self.fake.ignore_clears = True
        self.type("Example, Carol", "Quiz 1", status="cleared")
        plan = self.preview()
        sent = self.ended(self.apply(plan, "clear-ignored"))
        self.assertEqual(sent["progress"]["failed"], 1)
        self.assertEqual(self.receipts(self.carol), ["uncertain", "failed"])
        self.fake.ignore_clears = False
        self.assertEqual(entry(self.preview(), "Quiz 1", self.course_b)["counts"]["clear"], 1)  # never held: still Faculty's to take out

    def test_canvas_s_own_form_of_a_sent_grade_is_the_same_grade(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="9.3333")
        self.sync()
        self.fake.grade(self.course_a, self.quiz_a, self.alice, 9.33)  # as if Canvas kept two decimals
        self.assertEqual(held(self.preview(), "Example, Alice"), [])

    def test_a_late_penalty_in_canvas_is_not_a_change(self):
        self.fake.grade(self.course_a, self.quiz_a, self.alice, 8.1, entered=9)
        self.assertEqual(entry(self.preview(), "Quiz 1", self.course_a)["counts"]["same"], 1)

    def test_a_waiting_submission_is_left_alone(self):
        self.type("Example, Bob", "Exam 1", status="scored", points="60")
        exam = entry(self.preview(), "Exam 1", self.course_a)
        self.assertEqual((exam["counts"]["waiting"], exam["to_send"]), (1, 0))

    def test_what_cannot_take_grades_is_named_and_nothing_is_sent_there(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.fake.assignment(self.course_a, self.quiz_a)["grading_type"] = "pass_fail"
        quiz_a = entry(self.preview(), "Quiz 1", self.course_a)
        self.assertEqual((quiz_a["problems"], quiz_a["send"]), (["“Quiz 1” is not graded in points in Canvas"], False))

    def test_an_assignment_worth_other_points_than_the_column_is_named_and_nothing_is_sent_there(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.fake.assignment(self.course_a, self.quiz_a)["points_possible"] = 12
        quiz_a = entry(self.preview(), "Quiz 1", self.course_a)
        self.assertEqual((quiz_a["problems"], quiz_a["send"], quiz_a["assignment"]["points"]),
                         (["“Quiz 1” is worth 12 points in Canvas and 10 in the gradebook"], False, "12"))

    def test_the_summary_is_checked_again_before_anything_is_written(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        plan = self.preview()
        body = {"options": plan["options"], "fingerprint": plan["fingerprint"], "request_id": "sync-check-1"}
        self.assertEqual(self.client.post(self.base + "/canvas-sync", json=body, headers=HEADERS).status_code, 400)  # no confirmation
        self.assertEqual(self.client.post(self.base + "/canvas-sync", json={**body, "confirmation": "sync", "request_id": "short"},
                                          headers=HEADERS).status_code, 400)
        self.fake.grade(self.course_a, self.quiz_a, self.alice, 3)  # Canvas changed since the summary
        refused = self.apply(plan, "sync-check-2")
        self.assertEqual(refused.status_code, 409, refused.json)
        self.fake.grade(self.course_a, self.quiz_a, self.alice, 9)
        plan = self.preview()
        self.type("Example, Carol", "Quiz 1", status="scored", points="5")  # the gradebook changed since the summary
        self.assertEqual(self.apply(plan, "sync-check-3").status_code, 409)
        self.assertFalse([w for w in self.fake.writes if "update_grades" in w[1]])
        self.assertEqual(self.apply(plan, "sync-check-4", held={"nope:1:2": "faculty"}).status_code, 409)

    def test_ticked_sections_alone_are_written(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.type("Example, Carol", "Quiz 1", status="scored", points="5")
        plan, _ = self.sync(courses=[self.course_a])
        self.assertFalse(entry(plan, "Quiz 1", self.course_b)["send"])
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 10.0)
        self.assertEqual(self.canvas(self.course_b, self.quiz_b, self.carol)["score"], 6)  # 508 waits for its own sync
        self.assertEqual(entry(self.preview(), "Quiz 1", self.course_b)["counts"]["update"], 1)

    def test_a_column_kept_off_canvas_is_never_synced(self):
        view = self.client.get(self.base).json
        setup = {key: view["setup"][key] for key in ("categories", "items", "mode", "ungraded", "scale", "letter_rounding") if key in view["setup"]}
        setup["items"] = [{**item, "canvas": "off"} if item["title"] == "Quiz 1" else item for item in setup["items"]]
        saved = self.client.post(self.base + "/setup", json={"base_revision": view["setup"]["revision"], "request_id": "off-1", "setup": setup},
                                 headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        plan = self.preview()
        self.assertFalse([c for c in plan["columns"] if c["title"] == "Quiz 1"])
        self.assertEqual(held(plan, "Example, Bob"), [])

    def test_no_sync_starts_once_faculty_is_stopping(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        plan = self.preview()
        self.service.writes.close()
        answer = self.apply(plan, "sync-stopping")
        self.assertEqual(answer.status_code, 503, answer.json)
        self.assertIn("restarting", answer.json["error"])
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_canvas_sends").fetchone()[0], 0)
        self.assertFalse([w for w in self.fake.writes if "update_grades" in w[1]])

    def test_a_restart_waits_for_a_sync_still_reading_canvas_which_then_writes_nothing(self):
        """requirement, 2026-10-06: a restart while the sync read Canvas again cut its request
        off, and the page could not read the answer. Now the stop waits; the sync, not yet written, ends with nothing written."""
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        plan = self.preview()
        with connect(self.app.config["DATABASE"]) as db:
            requests = db.execute("SELECT COUNT(*) FROM gradebook_requests").fetchone()[0]
        real, reading, release, answers, stopped = self.service.read_for_send, threading.Event(), threading.Event(), [], []

        def slow(*args, **kwargs):  # Canvas slow to answer the sync's second read
            reading.set()
            release.wait(10)
            return real(*args, **kwargs)

        def sync():
            answers.append(self.app.test_client().post(self.base + "/canvas-sync", json={
                "options": plan["options"], "fingerprint": plan["fingerprint"], "request_id": "sync-restart", "confirmation": "sync"},
                headers=HEADERS))

        worker = threading.Thread(target=sync)
        self.addCleanup(release.set)
        with patch.object(self.service, "read_for_send", side_effect=slow), \
             patch("canvas.stopping.os.kill", side_effect=lambda pid, signum: stopped.append(signum)), \
             patch("canvas.stopping.signal.signal"):
            worker.start()
            self.assertTrue(reading.wait(10))
            self.assertEqual(len(self.service.writes.busy("send:")), 1)  # registered before it read Canvas
            stopping.stop_handler(self.service.writes, timeout=20, previous=Mock(), answer_wait=1)(signal.SIGTERM, None)
            time.sleep(0.3)
            self.assertEqual(stopped, [])  # the stop waits for the sync
            release.set()
            worker.join(10)
            self.assertEqual(stopped, [])  # and for its answer to go out
            until = time.monotonic() + 5
            while not stopped and time.monotonic() < until:
                time.sleep(0.05)
        self.assertEqual(stopped, [signal.SIGTERM])
        self.assertEqual(answers[0].status_code, 503, answers[0].json)
        self.assertEqual(answers[0].json["error"], "Faculty is restarting. Sync again in a minute; nothing was written.")
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_canvas_sends").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_requests").fetchone()[0], requests)
        self.assertFalse([w for w in self.fake.writes if "update_grades" in w[1]])
        self.assertEqual(self.service.writes.busy(), [])


    def test_a_lost_answer_is_found_by_its_request_id(self):
        """SYNC WITH CANVAS, A LOST ANSWER (requirement, 2026-10-31): the page asks Faculty about its request ID; unknown before the sync, working
        while it reads Canvas, then the sync's own answer; nothing is asked of Canvas and nothing is written by asking."""
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        plan = self.preview()
        lookup = f"{self.base}/canvas-sync/requests/lost-answer-1"
        self.assertEqual(self.client.get(lookup).json, {"state": "unknown"})
        real, reading, release, answers = self.service.read_for_send, threading.Event(), threading.Event(), []

        def slow(*args, **kwargs):
            reading.set()
            release.wait(10)
            return real(*args, **kwargs)

        worker = threading.Thread(target=lambda: answers.append(self.app.test_client().post(self.base + "/canvas-sync", json={
            "options": plan["options"], "fingerprint": plan["fingerprint"], "request_id": "lost-answer-1", "confirmation": "sync"},
            headers=HEADERS)))
        self.addCleanup(release.set)
        with patch.object(self.service, "read_for_send", side_effect=slow):
            worker.start()
            self.assertTrue(reading.wait(10))
            self.assertEqual(self.client.get(lookup).json, {"state": "working"})
            release.set()
            worker.join(10)
        self.assertEqual(answers[0].status_code, 200, answers[0].json)
        with self.assertLogs("gradebook.service", "WARNING") as logged:
            found = self.client.get(lookup + "?lost=unreadable%2C+HTTP+502+after+31+s")
        self.assertEqual(found.status_code, 200, found.json)
        self.assertIn("unreadable, HTTP 502 after 31 s", logged.output[0])
        self.assertEqual(found.json["state"], "recorded")
        self.assertEqual(found.json["sync"], answers[0].json["sync"])
        self.assertEqual(found.json["send"]["id"], answers[0].json["send"]["id"])
        self.assertEqual(found.json["gradebook"]["term"], answers[0].json["gradebook"]["term"])
        self.assertEqual(self.client.get(lookup + "?section_id=not-this-terms-section").status_code, 400)
        self.ended(answers[0])
        # Another kind of request (a typed score) is no sync.
        with connect(self.app.config["DATABASE"]) as db:
            typed = db.execute("SELECT request_id FROM gradebook_requests WHERE kind='scores' ORDER BY created_at DESC").fetchone()[0]
        self.assertEqual(self.client.get(f"{self.base}/canvas-sync/requests/{typed}").json, {"state": "unknown"})

class ReceiptTests(SyncBase):
    """Every value sent keeps a receipt whatever happens after Canvas is asked (2026-10-04 review)."""

    def test_canvas_refusing_the_update_is_failed_and_planned_again(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.fake.refuse.add(("POST", "update_grades"))
        sent = self.ended(self.apply(self.preview(), "refused-1"))
        self.assertEqual((sent["status"], sent["progress"]["failed"], sent["progress"]["uncertain"]), ("done", 1, 0), sent)
        self.assertEqual(self.receipts(self.alice), ["uncertain", "failed"])
        self.fake.refuse.clear()
        self.assertEqual(entry(self.preview(), "Quiz 1", self.course_a)["counts"]["update"], 1)  # never held: Canvas still shows the 9

    def test_a_send_cut_off_after_canvas_took_it_keeps_uncertain_receipts_and_is_never_held(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        real = web.Client.assignment_submissions

        def reading(client, course_id, assignment_id):
            if threading.current_thread().name.startswith("canvas-sync"):  # the send's read-back, not the summary's read
                raise web.SignedOut("Canvas no longer accepts Faculty's session. Sign in to Canvas again.", status=401)
            return real(client, course_id, assignment_id)
        with patch("canvas.web.Client.assignment_submissions", reading):
            sent = self.ended(self.apply(self.preview(), "cut-off-1"))
        self.assertEqual((sent["status"], sent["progress"]["uncertain"]), ("failed", 1), sent)
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 10.0)  # Canvas took it
        self.assertEqual(self.receipts(self.alice), ["uncertain"])
        # Faculty changes Alice again: Canvas shows what Faculty may have sent, so it is Faculty's to update, not held.
        self.type("Example, Alice", "Quiz 1", status="scored", points="8")
        plan = self.preview()
        self.assertEqual((entry(plan, "Quiz 1", self.course_a)["counts"]["update"], held(plan, "Example, Alice")), (1, []))

    def test_a_send_read_just_before_it_ended_is_not_shown_interrupted(self):
        """2026-10-06, under load: the row was read while the send ran, and it had recorded "failed" and left canvas.writes by the
        time the view asked whether it still ran; the view must read it again, not call it interrupted."""
        self.app.extensions["canvas"].owner = True  # the web process, which settles a send it no longer runs (CanvasService.owner)
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        _, sent = self.sync()
        with connect(self.app.config["DATABASE"]) as db:
            row = dict(db.execute("SELECT * FROM gradebook_canvas_sends WHERE id=?", (sent["id"],)).fetchone())
        view = self.app.extensions["gradebook"]._send_view({**row, "status": "running"})
        self.assertEqual((view["status"], view["progress"]["accepted"]), ("done", 1))

    def test_one_canvas_assignment_never_takes_two_columns(self):
        copy = f"/api/canvas/content/{self.term}"
        started = self.client.post(f"{copy}/start", json={"course_id": self.course_a, "request_id": "copy-start-1", "base_revision": None}, headers=HEADERS)
        self.assertEqual(started.status_code, 200, started.json)
        view = self.client.get(copy).json
        content = view["content"]
        # The sync made "Reading check" from 508's assignment; the copy now links 507's Quiz 1 to it as well.
        reading = next(i["id"] for i in self.client.get(self.base).json["setup"]["items"] if i["title"] == "Reading check")
        next(a for a in content["assignments"] if a["name"] == "Quiz 1")["gradebook_item"] = reading
        saved = self.client.post(copy, json={"base_revision": view["revision"], "request_id": "copy-save-2", "content": content}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        plan = self.preview()
        quiz = entry(plan, "Quiz 1", self.course_a)
        self.assertFalse(quiz["send"])
        self.assertTrue(any("also takes the column" in problem for problem in quiz["problems"]), quiz["problems"])
        self.assertEqual(entry(plan, "Quiz 1", self.course_b)["problems"], [])


class ParallelTests(SyncBase):
    """The send writes to its Canvas courses at once, each course's assignments in order (requirement, 2026-10-06, after a send of
    20 assignments one after another took seven minutes and a restart cut it off)."""

    def setUp(self):
        super().setUp()
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.type("Example, Alice", "Homework 2", status="scored", points="4")
        self.type("Example, Carol", "Quiz 1", status="scored", points="5")
        self.type("Example, Carol", "Homework 2", status="scored", points="3")

    def test_the_courses_go_at_once_and_each_course_in_order(self):
        real, calls, lock = web.Client.update_grades, [], threading.Lock()
        together = threading.Barrier(2, timeout=10)  # broken unless both courses ask Canvas at the same time

        def update(client, course_id, assignment_id, grades):
            with lock:
                first = not any(c == str(course_id) for c, _ in calls)
                calls.append((str(course_id), str(assignment_id)))
            if first:
                together.wait()
            return real(client, course_id, assignment_id, grades)
        with patch("canvas.web.Client.update_grades", update):
            plan, sent = self.sync()
        self.assertEqual((sent["progress"]["accepted"], sent["progress"]["done_assignments"], sent["progress"]["current"]), (4, 4, ""))
        for course in (self.course_a, self.course_b):
            planned = [str(c["assignment"]["id"]) for c in plan["columns"] if c["course_id"] == course and c["to_send"]]
            self.assertEqual([a for c, a in calls if c == course], planned)
        self.assertEqual(self.canvas(self.course_b, self.hw_b, self.carol)["score"], 3.0)

    def test_a_course_signed_out_stops_the_other_before_its_next_assignment(self):
        real, asked = web.Client.update_grades, []

        def signed_out():  # recorded once course A has stopped the send (canvas-sync: the progress says so at once)
            with connect(self.app.config["DATABASE"]) as db:
                return any(json.loads(r[0]).get("signed_out") for r in db.execute("SELECT progress_json FROM gradebook_canvas_sends"))

        def update(client, course_id, assignment_id, grades):
            asked.append((str(course_id), str(assignment_id)))
            if str(course_id) == self.course_a:
                raise web.SignedOut("Canvas no longer accepts Faculty's session. Sign in to Canvas again.", status=401)
            until = time.monotonic() + 20  # course B's first assignment is still out when course A finds the session gone
            while not signed_out() and time.monotonic() < until:
                time.sleep(0.02)
            return real(client, course_id, assignment_id, grades)
        with patch("canvas.web.Client.update_grades", update):
            sent = self.ended(self.apply(self.preview(), "signed-out-1"))
        self.assertEqual((sent["status"], sent["progress"]["failed"], sent["progress"]["accepted"]), ("failed", 1, 1), sent)
        self.assertTrue(sent["progress"]["signed_out"])
        self.assertEqual([e["label"] for e in sent["progress"]["errors"]], ["Canvas"])
        self.assertEqual([c for c, _ in asked].count(self.course_b), 1)  # its second assignment never went
        self.assertEqual(self.receipts(self.carol), ["uncertain", "accepted"])
        self.assertEqual(len(self.receipts(self.alice)), 2)  # Quiz 1 failed; Homework 2 never asked: no receipt

    def test_canvas_s_throttle_is_waited_out_once(self):
        self.fake.throttle = 1
        with patch("canvas.web.THROTTLE_WAIT", 0):
            _, sent = self.sync()
        self.assertEqual((sent["progress"]["accepted"], sent["progress"]["failed"], sent["progress"]["errors"]), (4, 0, []))
        self.assertEqual(self.fake.throttle, 0)

    def test_a_throttle_refusing_twice_fails_those_grades_alone(self):
        self.fake.throttle = 2
        with patch("canvas.web.THROTTLE_WAIT", 0):  # 507's course alone: its Quiz 1 is refused twice, its Homework 2 goes
            sent = self.ended(self.apply(self.preview(courses=[self.course_a]), "throttled-1"))
        self.assertEqual((sent["status"], sent["progress"]["failed"], sent["progress"]["accepted"]), ("done", 1, 1), sent)
        self.assertEqual(self.receipts(self.alice), ["uncertain", "failed", "uncertain", "accepted"])
        self.assertIn("Canvas answered 403", sent["progress"]["errors"][0]["message"])
        self.assertEqual(self.fake.throttle, 0)

    def test_only_the_send_waits_out_the_throttle(self):
        self.fake.throttle = 1
        with self.service.client() as client, self.assertRaises(web.CanvasError) as refused:
            client.update_grades(self.course_a, self.quiz_a, {self.alice: {"posted_grade": "10"}})
        self.assertEqual(refused.exception.status, 403)


class CreateTests(SyncBase):
    """A column without a Canvas assignment is created in its category's assignment group (requirement, 2026-10-06)."""

    CFU = "Checks for Understanding Category"

    def setUp(self):
        super().setUp()
        self.fake.groups[self.course_a].append({"id": self.fake.next_id(), "name": self.CFU, "position": 9, "assignments": []})
        view = self.client.get(self.base).json
        setup = {key: view["setup"][key] for key in ("categories", "items", "mode", "ungraded", "scale", "letter_rounding") if key in view["setup"]}
        setup["categories"] = [*setup["categories"], {"ref": "cfu", "name": self.CFU}]
        setup["items"] = [*setup["items"], {"title": "Session 1", "points": "10", "category_id": "cfu"}]
        saved = self.client.post(self.base + "/setup", json={"base_revision": view["setup"]["revision"], "request_id": "cfu-setup", "setup": setup},
                                 headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.items["Session 1"] = next(item["id"] for item in saved.json["setup"]["items"] if item["title"] == "Session 1")

    def test_a_column_is_created_in_its_group_once_it_has_grades_then_sent(self):
        plan = self.preview()
        session_a, session_b = entry(plan, "Session 1", self.course_a), entry(plan, "Session 1", self.course_b)
        self.assertEqual((session_a["assignment"]["new"], session_a["to_send"]), (True, 0))  # nothing to send yet: nothing made
        self.assertIn(f"no Assignment Group named “{self.CFU}”", session_b["problems"][0])  # 508's course has no such group
        self.type("Example, Alice", "Session 1", status="scored", points="9")
        plan, sent = self.sync()
        self.assertEqual((plan["to_canvas"]["creating"], sent["progress"]["created"], sent["progress"]["accepted"]), (1, 1, 1))
        made = next(a for g in self.fake.groups[self.course_a] if g["name"] == self.CFU for a in g["assignments"])
        self.assertEqual((made["name"], made["points_possible"], made["published"], made["submission_types"]), ("Session 1", 10.0, True, ["none"]))
        self.assertEqual(self.canvas(self.course_a, made["id"], self.alice)["score"], 9.0)
        # The next sync finds it: nothing is created twice, and its From-Canvas half knows it is the column's.
        again = self.preview()
        self.assertEqual((entry(again, "Session 1", self.course_a)["assignment"]["id"], entry(again, "Session 1", self.course_a)["counts"]["same"]),
                         (made["id"], 1))
        column = next(c for c in again["from_canvas"]["columns"] if c["assignment_id"] == made["id"])
        self.assertEqual((column["mapping"], column["suggested"]), ({"action": "item", "item_id": self.items["Session 1"]}, "remembered"))

    def test_canvas_refusing_to_create_a_column_makes_nothing_and_says_so(self):
        self.type("Example, Alice", "Session 1", status="scored", points="9")
        self.fake.refuse.add(("POST", f"/courses/{self.course_a}/assignments"))
        sent = self.ended(self.apply(self.preview(), "create-refused-1"))
        self.assertEqual((sent["progress"]["created"], sent["progress"]["accepted"]), (0, 0))
        self.assertEqual(([e["label"] for e in sent["progress"]["errors"]], sent["progress"]["failed"]), (["Session 1"], 1))
        self.assertIn("Canvas refused to create “Session 1”", sent["progress"]["errors"][0]["message"])
        self.assertFalse(any(a["name"] == "Session 1" for g in self.fake.groups[self.course_a] for a in g["assignments"]))
        self.assertEqual(sum(1 for r in self.fake.requests if r["method"] == "POST" and r["path"].endswith(f"/courses/{self.course_a}/assignments")), 1)

    def test_a_column_in_faculty_s_copy_is_made_as_the_copy_has_it_and_the_push_finds_it_in_step(self):
        """COLUMN ASSIGNMENTS (requirement, 2026-10-06): the column's assignment joins Faculty's copy when the copy starts; Sync makes it in Canvas as the copy has it
        (its due date too) and records the copy's link, so the next push finds it in step instead of making it again."""
        started = self.client.post(f"/api/canvas/content/{self.term}/start", json={"course_id": self.course_a, "request_id": "copy-start-1",
                                                                                    "base_revision": None}, headers=HEADERS)
        self.assertEqual(started.status_code, 200, started.json)
        mine = next(a for a in started.json["content"]["assignments"] if a.get("gradebook_item") == self.items["Session 1"])
        self.assertEqual({k: mine[k] for k in ("name", "points", "group", "published", "submission_types", "due_at", "description")},
                         {"name": "Session 1", "points": "10", "group": self.CFU, "published": True, "submission_types": ["none"],
                          "due_at": None, "description": ""})
        # A due date given in the copy goes with it.
        content = started.json["content"]
        next(a for a in content["assignments"] if a["id"] == mine["id"])["due_at"] = "2026-10-02T04:59:00Z"
        saved = self.client.post(f"/api/canvas/content/{self.term}", json={"base_revision": started.json["revision"], "request_id": "copy-due-1",
                                                                            "content": content}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.type("Example, Alice", "Session 1", status="scored", points="9")
        plan, sent = self.sync()
        session_a = entry(plan, "Session 1", self.course_a)
        self.assertEqual((session_a["create"]["copy"]["key"], session_a["problems"]), (mine["id"], []))
        self.assertEqual((plan["to_canvas"]["creating"], sent["progress"]["created"], sent["progress"]["accepted"]), (1, 1, 1))
        made = next(a for g in self.fake.groups[self.course_a] if g["name"] == self.CFU for a in g["assignments"])
        self.assertEqual((made["name"], made["published"], made["submission_types"], made["due_at"]), ("Session 1", True, ["none"], "2026-10-02T04:59:00Z"))
        self.assertEqual(self.canvas(self.course_a, made["id"], self.alice)["score"], 9.0)
        with connect(self.app.config["DATABASE"]) as db:
            link = db.execute("""SELECT canvas_id,source FROM canvas_content_links WHERE term_id=? AND course_id=? AND plan_key=?
                ORDER BY created_at DESC,rowid DESC LIMIT 1""", (self.term, self.course_a, mine["id"])).fetchone()
        self.assertEqual(tuple(link), (str(made["id"]), "matched"))
        pushed = self.client.post(f"/api/canvas/content/{self.term}/preview", json={"choices": {}}, headers=HEADERS)
        self.assertEqual(pushed.status_code, 200, pushed.json)
        course_a = next(c for c in pushed.json["courses"] if c["course_id"] == self.course_a)
        self.assertEqual([op for op in course_a["ops"] if op.get("key") == mine["id"] or op.get("canvas_id") == str(made["id"])], [])
        # The copy's assignment with more than Sync makes (a description) is left to the push.
        self.assertFalse(canvas_export.from_copy({**mine, "description": "<p>Bring a clicker.</p>"}, {"points": "10"}))
        self.assertFalse(canvas_export.from_copy({**mine, "published": False}, {"points": "10"}))
        self.assertFalse(canvas_export.from_copy(mine, {"points": "12"}))

    def test_the_copy_s_assignment_covers_each_section_sync_made_it_in(self):
        """SECTIONS FOLLOW THE COLUMN (requirement, 2026-10-17): the copy's assignment was for 508 alone (the iClicker column's first section); Sync made it in 507's course as
        the column gained 507; the copy then covers 507 too, and the push neither deletes nor remakes it."""
        started = self.client.post(f"/api/canvas/content/{self.term}/start", json={"course_id": self.course_a, "request_id": "copy-start-1",
                                                                                    "base_revision": None}, headers=HEADERS)
        self.assertEqual(started.status_code, 200, started.json)
        content = started.json["content"]
        mine = next(a for a in content["assignments"] if a.get("gradebook_item") == self.items["Session 1"])
        mine["sections"] = [self.sections[508]]
        saved = self.client.post(f"/api/canvas/content/{self.term}", json={"base_revision": started.json["revision"], "request_id": "copy-508",
                                                                            "content": content}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.type("Example, Alice", "Session 1", status="scored", points="9")
        plan, sent = self.sync()
        self.assertEqual(sent["progress"]["created"], 1)
        made = next(a for g in self.fake.groups[self.course_a] if g["name"] == self.CFU for a in g["assignments"])
        copy = self.client.get(f"/api/canvas/content/{self.term}").json["content"]
        self.assertEqual(next(a for a in copy["assignments"] if a["id"] == mine["id"])["sections"], sorted([self.sections[507], self.sections[508]]))
        pushed = self.client.post(f"/api/canvas/content/{self.term}/preview", json={"choices": {}}, headers=HEADERS).json
        course_a = next(c for c in pushed["courses"] if c["course_id"] == self.course_a)
        self.assertEqual([e["label"] for e in course_a["extra"]], [])
        self.assertEqual([op for op in course_a["ops"] if op.get("key") == mine["id"] or op.get("canvas_id") == str(made["id"])], [])

    def test_an_assignment_of_that_name_made_since_the_summary_is_never_doubled(self):
        self.type("Example, Alice", "Session 1", status="scored", points="9")
        plan = self.preview()
        self.fake.add_assignment(self.course_a, "Session 1", 10, group=self.CFU)
        self.assertEqual(self.apply(plan, "sync-doubled").status_code, 409)
        self.assertEqual(sum(1 for w in self.fake.writes if w[0] == "POST" and w[1].endswith("/assignments")), 0)


class PublishTests(SyncBase):
    """PUBLISH FROM SYNC (requirement, 2026-10-07): an unpublished assignment is offered a tick in the summary; ticked, the Sync
    publishes it first (a receipt before Canvas is asked, settled by reading it back), then sends its grades; unticked, it is left
    alone and its grades wait."""

    def setUp(self):
        super().setUp()
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.fake.assignment(self.course_a, self.quiz_a)["published"] = False

    def puts(self):
        return [w for w in self.fake.writes if w[0] == "PUT" and w[1].endswith(f"/assignments/{self.quiz_a}")]

    def published(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [tuple(r) for r in db.execute("SELECT state,json_extract(canvas_json,'$.published') FROM gradebook_canvas_published ORDER BY rowid")]

    def apply_publish(self, plan, request_id, publish):
        return self.client.post(self.base + "/canvas-sync", json={"options": plan["options"], "fingerprint": plan["fingerprint"], "request_id": request_id,
                                                                   "confirmation": "sync", "publish": publish}, headers=HEADERS)

    def test_an_unpublished_assignment_is_offered_and_left_alone_unticked(self):
        plan = self.preview()
        quiz_a = entry(plan, "Quiz 1", self.course_a)
        self.assertEqual((quiz_a["publish"], quiz_a["problems"], quiz_a["send"], quiz_a["to_send"]), (True, [], True, 1))
        self.assertIn("is not published in Canvas", quiz_a["notes"][0])
        self.assertNotIn("publish", entry(plan, "Quiz 1", self.course_b))  # published there: nothing to offer
        self.assertIsNone(self.ended(self.apply(plan, "publish-off-1")))  # nothing ticked: nothing sent
        self.assertEqual((self.puts(), self.published()), ([], []))
        self.assertFalse(self.fake.assignment(self.course_a, self.quiz_a)["published"])
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 9)
        # Not left in step by that sync: Alice's 10 still goes once it is published (not held as a change made in Canvas).
        plan = self.preview()
        self.assertEqual((entry(plan, "Quiz 1", self.course_a)["publish"], entry(plan, "Quiz 1", self.course_a)["counts"]["update"]), (True, 1))
        self.assertEqual(held(plan, "Example, Alice"), [])

    def test_ticked_it_is_published_first_then_its_grades_go(self):
        plan = self.preview()
        answer = self.apply_publish(plan, "publish-on-1", [self.items["Quiz 1"]])
        sent = self.ended(answer)
        self.assertEqual((sent["status"], sent["progress"]["published"], sent["progress"]["accepted"]), ("done", 1, 1))
        self.assertEqual(answer.json["sync"]["publishing"], 1)
        self.assertEqual([w[2] for w in self.puts()], [{"published": True}])
        self.assertTrue(self.fake.assignment(self.course_a, self.quiz_a)["published"])
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 10.0)
        self.assertEqual(self.published(), [("uncertain", 0), ("accepted", 1)])
        again = entry(self.preview(), "Quiz 1", self.course_a)
        self.assertEqual(("publish" in again, again["counts"]["same"]), (False, 1))
        # The same request is the same sync: nothing is published or sent twice.
        self.assertEqual(self.apply_publish(plan, "publish-on-1", [self.items["Quiz 1"]]).status_code, 200)
        self.assertEqual((len(self.puts()), len(self.published())), (1, 2))

    def test_canvas_refusing_to_publish_sends_nothing_and_asks_nothing_again(self):
        self.fake.refuse.add(("PUT", f"/assignments/{self.quiz_a}"))
        sent = self.ended(self.apply_publish(self.preview(), "publish-refused-1", [self.items["Quiz 1"]]))
        self.assertEqual((sent["progress"]["published"], sent["progress"]["failed"], sent["progress"]["accepted"]), (0, 1, 0))
        self.assertIn("Canvas refused to publish “Quiz 1”", sent["progress"]["errors"][0]["message"])
        self.assertEqual(self.published(), [("uncertain", 0), ("failed", 0)])
        self.assertEqual(self.receipts(self.alice), ["failed"])  # never sent
        self.assertEqual(sum(1 for r in self.fake.requests if r["method"] == "PUT" and r["path"].endswith(f"/assignments/{self.quiz_a}")), 1)
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 9)
        # Offered again, the instructor's to tick; Alice's 10 still goes (Canvas never took it: not held as a change made in Canvas).
        plan = self.preview()
        self.assertEqual((entry(plan, "Quiz 1", self.course_a)["publish"], entry(plan, "Quiz 1", self.course_a)["counts"]["update"]), (True, 1))
        self.assertEqual(held(plan, "Example, Alice"), [])

    def test_faculty_s_copy_is_published_too_and_the_push_finds_it_in_step(self):
        started = self.client.post(f"/api/canvas/content/{self.term}/start", json={"course_id": self.course_a, "request_id": "copy-start-1",
                                                                                    "base_revision": None}, headers=HEADERS)
        self.assertEqual(started.status_code, 200, started.json)
        mine = next(a for a in started.json["content"]["assignments"] if a["name"] == "Quiz 1")
        self.assertFalse(mine["published"])
        sent = self.ended(self.apply_publish(self.preview(), "publish-copy-1", [self.items["Quiz 1"]]))
        self.assertEqual((sent["status"], sent["progress"]["published"], sent["progress"]["accepted"]), ("done", 1, 1))
        copy = self.client.get(f"/api/canvas/content/{self.term}").json
        self.assertEqual((copy["revision"], next(a for a in copy["content"]["assignments"] if a["id"] == mine["id"])["published"]),
                         (started.json["revision"] + 1, True))
        pushed = self.client.post(f"/api/canvas/content/{self.term}/preview", json={"choices": {}}, headers=HEADERS)
        self.assertEqual(pushed.status_code, 200, pushed.json)
        course_a = next(c for c in pushed.json["courses"] if c["course_id"] == self.course_a)
        self.assertEqual([op for op in course_a["ops"] if op.get("key") == mine["id"] or op.get("canvas_id") == str(self.quiz_a)], [])

    def test_only_sync_publishes_and_only_for_the_instructor(self):
        found = self.client.post(self.base + "/canvas-cell/preview", json={"item_id": self.items["Quiz 1"], "row": self.rows["Example, Alice"]},
                                 headers=HEADERS).json
        self.assertEqual((found["ready"], found["problems"]), (False, ["“Quiz 1” is not published in Canvas, so it cannot take grades"]))
        from gradebook.service import GradebookService
        with self.assertRaisesRegex(ValueError, "instructor's"):
            GradebookService._sync_publish([self.items["Quiz 1"]], {"items": [{"id": self.items["Quiz 1"]}]}, "assistant")
        self.assertEqual(self.apply_publish(self.preview(), "publish-bad-1", ["item_nope"]).status_code, 400)
        self.assertEqual(self.puts(), [])


class OneReadTests(SyncBase):
    """The summary reads Canvas once; Sync reads again only the assignments it writes to or takes from."""

    def per_assignment(self):
        return [r["path"] for r in self.fake.requests if r["method"] == "GET" and "/assignments/" in r["path"] and r["path"].endswith("/submissions")]

    def test_the_summary_reads_once_and_sync_reads_again_only_what_it_writes(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.fake.requests.clear()
        plan = self.preview()
        self.assertEqual(self.per_assignment(), [])  # one listing of every submission per course, nothing per assignment
        self.ended(self.apply(plan, "one-read-1"))
        self.assertEqual(sorted(set(self.per_assignment())), [f"/api/v1/courses/{self.course_a}/assignments/{self.quiz_a}/submissions"])


class SnapshotTests(SyncBase):
    """CANVAS AS SYNC FOUND IT (requirement, 2026-10-08): every sync keeps, compressed,
    the Canvas it planned from; with its send's receipts, Canvas before and after."""

    def kept_rows(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("""SELECT r.request_id,s.format,s.courses,s.submissions,s.size,length(s.data) AS stored
                FROM gradebook_canvas_snapshots s JOIN gradebook_requests r ON r.id = s.request_ref ORDER BY s.rowid""")]

    def test_each_sync_keeps_canvas_as_it_found_it_compressed_without_a_uin(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.fake.grade(self.course_b, self.quiz_b, self.carol, 4)  # changed in Canvas: held, undecided
        plan = self.preview()
        self.ended(self.apply(plan, "kept-copy-1"))
        with connect(self.app.config["DATABASE"]) as db:
            kept = canvas_snapshot.load(db, self.term, "kept-copy-1")
        self.assertEqual((kept["format"], sorted(kept["courses"])), ("canvas-read/1", sorted([self.course_a, self.course_b])))
        a, b = kept["courses"][self.course_a], kept["courses"][self.course_b]
        shown = {(s["assignment_id"], s["user_id"]): s for s in a["submissions"] + b["submissions"]}
        # Canvas before the send (Alice's 9, read again because Sync wrote there), Canvas after it (her 10, and its receipts).
        self.assertEqual(shown[(self.quiz_a, self.alice)]["score"], 9)
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 10.0)
        self.assertEqual(self.receipts(self.alice), ["uncertain", "accepted"])
        self.assertEqual(shown[(self.quiz_b, self.carol)]["score"], 4)
        self.assertEqual((a["read_again"], b["read_again"]), ([self.quiz_a], []))
        self.assertIn("Quizzes", a["groups"].values())
        # Students by Faculty's membership, never by UIN; one Faculty does not have is kept with none.
        self.assertEqual({s["name"]: s["row"] for s in a["students"]},
                         {"Example, Alice": self.rows["Example, Alice"], "Example, Bob": self.rows["Example, Bob"], "Newcomer, Dana": None})
        text = json.dumps(kept)
        for uin in (ALICE, BOB, CAROL, "999888777"):
            self.assertNotIn(uin, text)
        self.assertFalse(any("sis" in s for s in a["students"] + b["students"]))
        # One per sync (the first, Start from Canvas, kept one too); a replay keeps no second one.
        self.assertEqual(self.apply(plan, "kept-copy-1").status_code, 200)
        rows = self.kept_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[1]["request_id"], rows[1]["format"], rows[1]["courses"], rows[1]["submissions"]),
                         ("kept-copy-1", "canvas-read/1", 2, len(a["submissions"]) + len(b["submissions"])))
        self.assertLess(rows[1]["stored"], rows[1]["size"])
        with connect(self.app.config["DATABASE"]) as db:
            with self.assertRaisesRegex(sqlite3.DatabaseError, "immutable"):
                db.execute("UPDATE gradebook_canvas_snapshots SET size=1")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "retained"):
                db.execute("DELETE FROM gradebook_canvas_snapshots")

    def test_a_sync_refused_keeps_nothing(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        plan = self.preview()
        self.fake.grade(self.course_a, self.quiz_a, self.alice, 3)  # changed in Canvas after the summary: a 409
        self.assertEqual(self.apply(plan, "kept-copy-2").status_code, 409)
        self.assertEqual(len(self.kept_rows()), 1)  # the first sync's alone


class SnapshotBuildTests(unittest.TestCase):
    """canvas_snapshot.build: a student's SIS ID becomes Faculty's membership, a current one before a dropped one."""

    def test_a_current_membership_comes_before_a_dropped_one_and_the_copy_reads_back(self):
        canvas = {"11": {"groups": {"5": "Quizzes"}, "assignments": [{"id": "7", "name": "Quiz 1"}],
                         "students": [{"user_id": "3", "section_id": "1", "state": "active", "sis": ALICE, "name": "Example, Alice"}],
                         "submissions": {("7", "3"): {"score": 9, "entered_score": 9, "excused": False, "state": "graded", "posted": True}}}}
        students = [{"row": "s-old:0000000001", "section_id": "s-old", "uin": ALICE, "dropped": True},
                    {"row": "s-new:0000000002", "section_id": "s-new", "uin": ALICE, "dropped": False}]
        links = {"s-old": {"course_id": "11"}, "s-new": {"course_id": "11"}}
        built = canvas_snapshot.build(canvas, students, links)
        self.assertEqual((built["courses"], built["submissions"]), (1, 1))
        kept = json.loads(gzip.decompress(built["data"]))
        self.assertEqual(kept["courses"]["11"]["students"][0]["row"], "s-new:0000000002")
        self.assertEqual(kept["courses"]["11"]["submissions"], [{"assignment_id": "7", "user_id": "3", "score": 9, "entered_score": 9,
                                                                 "excused": False, "state": "graded", "posted": True}])
        self.assertEqual(canvas_snapshot.build(canvas, students, links)["data"], built["data"])  # the same read, the same bytes


class CanvasQuizTests(GradebookCanvasBase):
    """Canvas quizzes are Canvas's, like WebAssign (requirement, 2026-10-06): mirrored, read-only, never sent."""

    def test_a_canvas_quiz_mirrors_canvas(self):
        quiz = self.fake.add_assignment(self.course_a, "Lecture Video and Quiz 1", 2, group="Lecture Quizzes", quiz=True)
        self.fake.grade(self.course_a, quiz, self.alice, 2)
        self.sync()
        view = self.client.get(self.base).json
        column = next(item for item in view["setup"]["items"] if item["title"] == "Lecture Video and Quiz 1")
        self.assertEqual(column["canvas"], "quiz")
        self.assertEqual(view["cells"][column["id"]][self.rows["Example, Alice"]]["points"], "2")
        refused = self.client.post(self.base + "/scores", json={"request_id": "typed-quiz", "changes": [
            {"item_id": column["id"], "row": self.rows["Example, Bob"], "base": None, "status": "scored", "points": "1"}]}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400, refused.json)
        self.fake.grade(self.course_a, quiz, self.bob, 1)  # Bob takes it later: comes in, never held
        plan, _ = self.sync()
        self.assertFalse([h for h in plan["held"] if h["title"] == "Lecture Video and Quiz 1"])
        self.assertFalse([c for c in plan["columns"] if c["title"] == "Lecture Video and Quiz 1"])
        self.assertEqual(self.client.get(self.base).json["cells"][column["id"]][self.rows["Example, Bob"]]["points"], "1")
        self.assertFalse([w for w in self.fake.writes if f"/assignments/{quiz}/" in w[1]])


class WebAssignTests(GradebookCanvasBase):
    """WebAssign's columns: Canvas ALWAYS wins (requirement, 2026-10-06), read-only in Faculty, never sent."""

    def setUp(self):
        super().setUp()
        self.wa_a = self.fake.add_assignment(self.course_a, "Sect. 1.1 Matrices", 10, group="WebAssign Homework", tool=WEBASSIGN)
        self.wa_b = self.fake.add_assignment(self.course_b, "Sect. 1.1 Matrices", 10, group="WebAssign Homework", tool=WEBASSIGN)
        self.fake.grade(self.course_a, self.wa_a, self.alice, 9.5)
        self.fake.grade(self.course_a, self.wa_a, self.bob, 8.0, entered=10)  # late: WebAssign's score is the one after the penalty

    def test_webassign_columns_mirror_canvas_and_refuse_typed_scores(self):
        self.sync()
        view = self.client.get(self.base).json
        column = next(item for item in view["setup"]["items"] if item["title"] == "Sect. 1.1 Matrices")
        self.assertEqual(column["canvas"], "webassign")
        cells = view["cells"][column["id"]]
        self.assertEqual((cells[self.rows["Example, Alice"]]["points"], cells[self.rows["Example, Bob"]]["points"]), ("9.5", "8"))
        refused = self.client.post(self.base + "/scores", json={"request_id": "typed-wa", "changes": [
            {"item_id": column["id"], "row": self.rows["Example, Bob"], "base": cells[self.rows["Example, Bob"]]["id"], "status": "scored", "points": "10"}]},
            headers=HEADERS)
        self.assertEqual(refused.status_code, 400, refused.json)
        self.assertIn("WebAssign", refused.json["error"])
        # Canvas changes and takes a grade out: the next sync follows both; nothing is ever sent there.
        self.fake.grade(self.course_a, self.wa_a, self.alice, 10)
        self.fake.grade(self.course_a, self.wa_a, self.bob, None)
        plan, _ = self.sync()
        self.assertFalse([c for c in plan["columns"] if c["title"] == "Sect. 1.1 Matrices"])
        actions = {(c["name"], c["action"]) for c in plan["from_canvas"]["changes"] if c["item_id"] == column["id"]}
        self.assertEqual(actions, {("Example, Alice", "update"), ("Example, Bob", "clear")})
        cells = self.client.get(self.base).json["cells"][column["id"]]
        self.assertEqual(cells[self.rows["Example, Alice"]]["points"], "10")
        self.assertNotIn(self.rows["Example, Bob"], cells)  # emptied, as Canvas is
        self.assertFalse([w for w in self.fake.writes if f"/assignments/{self.wa_a}/" in w[1]])

    def test_a_value_typed_before_the_mark_is_replaced_by_canvas(self):
        setup = self.client.get(self.base).json["setup"]
        saved = self.client.post(self.base + "/setup", json={"base_revision": setup["revision"], "request_id": "wa-col",
                                 "setup": {"items": [*setup["items"], {"title": "Sect. 1.1 Matrices", "points": "10"}]}}, headers=HEADERS)
        column = next(item["id"] for item in saved.json["setup"]["items"] if item["title"] == "Sect. 1.1 Matrices")
        typed = self.client.post(self.base + "/scores", json={"request_id": "typed-wa-1", "changes": [
            {"item_id": column, "row": self.rows["Example, Alice"], "base": None, "status": "scored", "points": "3"}]}, headers=HEADERS)
        self.assertEqual(typed.status_code, 200, typed.json)
        self.sync()
        view = self.client.get(self.base).json
        self.assertEqual(view["cells"][column][self.rows["Example, Alice"]]["points"], "9.5")
        self.assertEqual(next(item for item in view["setup"]["items"] if item["id"] == column)["canvas"], "webassign")

    def test_a_browser_save_neither_gives_nor_takes_the_mark(self):
        self.sync()
        view = self.client.get(self.base).json
        setup = {key: view["setup"][key] for key in ("categories", "items", "mode", "ungraded", "scale", "letter_rounding") if key in view["setup"]}
        unmarked = [{**item, "canvas": None} if item.get("canvas") == "webassign" else item for item in setup["items"]]
        answer = self.client.post(self.base + "/setup", json={"base_revision": view["setup"]["revision"], "request_id": "unmark",
                                                              "setup": {**setup, "items": unmarked}}, headers=HEADERS)
        self.assertEqual(answer.status_code, 400, answer.json)
        marked = [{**item, "canvas": "webassign"} if item["title"] == "Quiz 1" else item for item in setup["items"]]
        answer = self.client.post(self.base + "/setup", json={"base_revision": view["setup"]["revision"], "request_id": "mark",
                                                              "setup": {**setup, "items": marked}}, headers=HEADERS)
        self.assertEqual(answer.status_code, 400, answer.json)
        kept = self.client.post(self.base + "/setup", json={"base_revision": view["setup"]["revision"], "request_id": "keep",
                                                            "setup": {**setup, "items": [{**i, "notes": "x"} for i in setup["items"]]}}, headers=HEADERS)
        self.assertEqual(kept.status_code, 200, kept.json)


class ZeroTests(SyncBase):
    """ZEROS GO TO CANVAS (requirement, 2026-10-09): an empty cell the gradebook counts as 0 goes to Canvas as 0."""

    def test_a_counted_zero_goes_as_zero_and_comes_out_once_it_stops_counting(self):
        # Quiz 1's grades are posted in Canvas, so its empty cells count as 0 (ZERO ONCE DUE, POSTED): Carol's emptied 6 is a 0.
        self.type("Example, Carol", "Quiz 1", status="cleared")
        rows = self.client.get(self.base).json["results"]["blanks"]
        self.assertEqual(rows[self.rows["Example, Carol"]][self.items["Quiz 1"]], "zero")
        plan, _ = self.sync()
        quiz_b = entry(plan, "Quiz 1", self.course_b)
        self.assertEqual((quiz_b["counts"]["update"], quiz_b["counts"]["clear"], quiz_b["zeros"]), (1, 0, 1))
        self.assertNotIn("zeros", entry(plan, "Quiz 1", self.course_a))
        self.assertEqual(self.canvas(self.course_b, self.quiz_b, self.carol)["score"], 0.0)
        self.assertEqual(self.receipts(self.carol)[-1], "accepted")
        self.assertIsNone(self.cell("Quiz 1", "Example, Carol"))  # still empty: nothing written into Faculty
        again = self.preview()
        self.assertEqual((entry(again, "Quiz 1", self.course_b)["counts"]["same"], entry(again, "Quiz 1", self.course_b)["to_send"]), (1, 0))
        # The next sync's copy of Canvas shows the 0: the comparison counts it as Faculty's.
        self.sync()
        found = reconciliation.read(self.app.extensions["gradebook"], self.term)
        self.assertEqual(found["sources"]["canvas"]["state"], "retained")
        self.assertFalse([d for d in found["differences"] if d["item_id"] == self.items["Quiz 1"] and d["row"] == self.rows["Example, Carol"]])
        # No longer counted: the 0 Faculty sent comes out.
        self.blanks_not_counted()
        plan, _ = self.sync()
        self.assertEqual(entry(plan, "Quiz 1", self.course_b)["counts"]["clear"], 1)
        self.assertIsNone(self.canvas(self.course_b, self.quiz_b, self.carol)["score"])

    def test_a_webassign_column_takes_a_zero_only_where_canvas_shows_no_grade(self):
        wa_a = self.fake.add_assignment(self.course_a, "Sect. 1.1 Matrices", 10, group="WebAssign Homework", tool=WEBASSIGN,
                                        due_at="2026-01-05T05:59:59Z")
        wa_b = self.fake.add_assignment(self.course_b, "Sect. 1.1 Matrices", 10, group="WebAssign Homework", tool=WEBASSIGN,
                                        due_at="2026-01-05T05:59:59Z")
        self.fake.grade(self.course_a, wa_a, self.alice, 9.5)
        self.sync()  # links the new column: its blanks count from the next sync
        plan, _ = self.sync()
        column_a, column_b = entry(plan, "Sect. 1.1 Matrices", self.course_a), entry(plan, "Sect. 1.1 Matrices", self.course_b)
        self.assertEqual((column_a["zeros_only"], column_a["to_send"], column_a["zeros"], column_b["to_send"]), (True, 1, 1, 1))
        self.assertEqual(self.canvas(self.course_a, wa_a, self.bob)["score"], 0.0)
        self.assertEqual(self.canvas(self.course_b, wa_b, self.carol)["score"], 0.0)
        self.assertEqual(self.canvas(self.course_a, wa_a, self.alice)["score"], 9.5)  # Canvas's own grade is never written
        self.assertFalse([w for w in self.fake.writes if f"/assignments/{wa_a}" in w[1] and w[0] in ("PUT", "POST") and "submissions" not in w[1]])
        # The next sync reads Canvas's 0 into the cell, as any Canvas grade there, and sends nothing to the column.
        plan, _ = self.sync()
        self.assertFalse([c for c in plan["columns"] if c["title"] == "Sect. 1.1 Matrices"])
        column = next(item["id"] for item in self.client.get(self.base).json["setup"]["items"] if item["title"] == "Sect. 1.1 Matrices")
        self.items["Sect. 1.1 Matrices"] = column
        self.assertEqual(self.cell("Sect. 1.1 Matrices", "Example, Bob")["points"], "0")


class PlanTests(unittest.TestCase):
    """The pure To-Canvas planner."""

    def test_a_cell_faculty_never_filled_is_filled_and_one_faculty_grades_is_held(self):
        canvas = {("5", "u1"): {"score": 70, "excused": False}}
        found = self.plan({}, canvas)
        self.assertEqual((found["held"], found["filling"]), ([], 1))
        self.assertEqual(canvas_export.choose(found, {})[1], [{"key": "i1:9:s:1", "item_id": "i1", "row": "s:1", "value": {"status": "scored", "points": "70"}}])
        self.assertEqual(len(self.plan({}, canvas, link={"kind": "grading", "parts": ["frq"]})["held"]), 1)

    def plan(self, cells, submissions, revisions=None, receipts=None, synced=None, link=None, zeros=frozenset(), remembered=None, **item):
        setup = {"categories": [], "items": [{"id": "i1", "title": "Exam 1", "points": "100", "sections": [], "link": link, **item}]}
        students = [{"row": "s:1", "section_id": "s", "section": 1, "name": "A", "uin": ALICE, "dropped": False}]
        canvas = {"9": {"assignments": [{"id": "5", "name": "Exam 1", "points": 100, "published": True, "grading_type": "points",
                                         "post_manually": True}], "groups": {},
                        "students": [{"user_id": "u1", "sis": ALICE, "state": "active"}], "submissions": submissions}}
        return canvas_export.plan(setup=setup, cells={"i1": cells}, students=students, links={"s": {"course_id": "9", "name": "C"}},
                                  canvas=canvas, remembered={"9:5": {"item_id": "i1"}} if remembered is None else remembered, copied={},
                                  revisions=revisions or {}, receipts=receipts or {}, synced=synced or {}, zeros=zeros)

    def test_an_empty_cell_the_gradebook_counts_as_zero_goes_as_zero(self):
        zero = {("i1", "s:1")}
        [value] = self.plan({}, {}, zeros=zero)["columns"][0]["values"]
        self.assertEqual((value["value"], value["action"], canvas_export.grade_data(value)), (canvas_export.ZERO, "add", {"posted_grade": "0"}))
        shown = {("5", "u1"): {"score": 0, "excused": False}}
        self.assertEqual(self.plan({}, shown, zeros=zero)["columns"][0]["counts"]["same"], 1)
        # No longer counted: the 0 Faculty sent there comes out.
        sent = {("9", "5", "u1"): [{"sent": canvas_export.ZERO, "canvas": {"status": "scored", "points": "0"}, "state": "accepted", "at": "3"}]}
        self.assertEqual(self.plan({}, shown, receipts=sent)["columns"][0]["counts"]["clear"], 1)
        # A later sync left it in step: the empty cell stood at that 0 then, so it still comes out (never held).
        self.assertEqual(self.plan({}, shown, receipts=sent, synced={("i1", "9"): "5"})["columns"][0]["counts"]["clear"], 1)
        # A grade Faculty never knew is still filled into the cell, or held where Faculty grades the column.
        canvas = {("5", "u1"): {"score": 70, "excused": False}}
        self.assertEqual(self.plan({}, canvas, zeros=zero)["filling"], 1)
        [h] = self.plan({}, canvas, zeros=zero, link={"kind": "grading", "parts": ["frq"]})["held"]
        self.assertEqual(h["faculty"], canvas_export.ZERO)
        # A grade Faculty knew there (it sent 70, then the cell was emptied) becomes the 0.
        known = {("9", "5", "u1"): [{"sent": {"status": "scored", "points": "70"}, "canvas": {"status": "scored", "points": "70"},
                                     "state": "accepted", "at": "3"}]}
        self.assertEqual(self.plan({}, canvas, receipts=known, zeros=zero)["columns"][0]["counts"]["update"], 1)

    def test_a_column_canvas_grades_takes_only_zeros_into_its_blanks(self):
        zero = {("i1", "s:1")}
        [column] = self.plan({}, {}, zeros=zero, canvas="webassign")["columns"]
        self.assertTrue(column["zeros_only"])
        self.assertEqual([(v["value"], v["action"]) for v in column["values"]], [(canvas_export.ZERO, "add")])
        for shown in ({"score": 3, "excused": False}, {"score": None, "excused": True}, {"score": None, "state": "pending_review"}):
            self.assertEqual(self.plan({}, {("5", "u1"): shown}, zeros=zero, canvas="quiz")["columns"], [])
        self.assertEqual(self.plan({}, {}, canvas="webassign")["columns"], [])  # nothing counted: not in the plan
        self.assertEqual(self.plan({}, {}, zeros=zero, canvas="webassign", remembered={})["columns"], [])  # never created
        self.assertEqual(self.plan({}, {}, zeros=zero, canvas="off")["columns"], [])  # kept off Canvas

    def test_the_rule(self):
        mine = {"s:1": {"id": "c", "status": "scored", "points": "80"}}
        canvas = {("5", "u1"): {"score": 70, "excused": False}}
        self.assertEqual(len(self.plan(mine, canvas)["held"]), 1)  # Faculty never knew 70
        known = {("i1", "s:1"): [{"value": {"status": "scored", "points": "70"}, "at": "1", "canvas": True},
                                 {"value": {"status": "scored", "points": "80"}, "at": "2", "canvas": False}]}
        self.assertEqual(self.plan(mine, canvas, known)["columns"][0]["counts"]["update"], 1)  # imported from Canvas, changed in Faculty
        later = {("9", "5", "u1"): [{"sent": {"status": "scored", "points": "75"}, "canvas": {"status": "scored", "points": "75"},
                                     "state": "accepted", "at": "3"}]}
        self.assertEqual(len(self.plan(mine, canvas, known, later)["held"]), 1)  # Faculty sent 75 since: the 70 is Canvas's own
        # A sync that left them in step knew Faculty's value then.
        stale = {("i1", "s:1"): [{"value": {"status": "scored", "points": "70"}, "at": "1", "canvas": False},
                                 {"value": {"status": "scored", "points": "80"}, "at": "5", "canvas": False}]}
        self.assertEqual(len(self.plan(mine, canvas, stale)["held"]), 1)
        self.assertEqual(self.plan(mine, canvas, stale, synced={("i1", "9"): "2"})["columns"][0]["counts"]["update"], 1)

    def test_faculty_s_own_grading_is_sent_but_never_taken_from_canvas(self):
        found = self.plan({"s:1": {"id": "c", "status": "scored", "points": "80"}}, {("5", "u1"): {"score": 70, "excused": False}},
                          link={"kind": "grading", "parts": ["frq"]})
        [h] = found["held"]
        self.assertFalse(h["take"])
        with self.assertRaisesRegex(ValueError, "graded in Faculty"):
            canvas_export.choose(found, {h["key"]: "canvas"})
        batches, taken = canvas_export.choose(found, {h["key"]: "faculty"})
        self.assertEqual(([v["value"] for v in batches[0]["values"]], taken), ([{"status": "scored", "points": "80"}], []))

    def test_grade_data(self):
        self.assertEqual(canvas_export.grade_data({"value": None}), {"posted_grade": ""})
        self.assertEqual(canvas_export.grade_data({"value": {"status": "excused", "points": None}}), {"excuse": True})
        self.assertEqual(canvas_export.grade_data({"value": {"status": "scored", "points": "7"}, "unexcuse": True}),
                         {"posted_grade": "7", "excuse": False})


if __name__ == "__main__":
    unittest.main()
