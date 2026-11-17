"""POSTING GRADES (docs/decisions/canvas.md; requirement, 2026-10-06), offline against the loopback FakeCanvas: a
column's choice recorded without asking Canvas and shown as pending, every sync carrying out each column's choice or its category's
rule after its send (a post only where its grades were accepted, never while a grade of it waits for the instructor), a by-hand
column's automatic assignment switched before its grades, an uncertain receipt per course before Canvas is asked, settled by reading
it back, and the grid's mark from what Faculty last read."""
import threading
import time
import unittest
from unittest.mock import patch

from test_canvas_sync import SyncBase
from test_storage import HEADERS

from assistant import policy
from canvas import web
from gradebook import canvas_posting
from rosters import connect


class PostingReadTests(unittest.TestCase):
    def test_an_assignment_with_no_current_students_keeps_the_last_read(self):
        """ZERO ONCE DUE, POSTED: an assignment read with no current students records nothing, so a posted column stays posted;
        one with current students is recorded, and an assignment this read did not cover never is."""
        targets = {("item", "course"): {"id": "assignment", "post_manually": True}}
        data = {"students": [{"user_id": "former-student", "state": "completed"}],
                "submissions": {("assignment", "former-student"): {"score": 7, "posted": False}}}
        self.assertEqual(canvas_posting.read_rows(targets, {"course": data}), [])
        self.assertEqual(canvas_posting.read_rows(targets, {"course": {**data, "read": {"assignment"}}}), [])
        current = {"students": [{"user_id": "student", "state": "active"}],
                   "submissions": {("assignment", "student"): {"score": 7, "posted": True}}}
        self.assertEqual(canvas_posting.read_rows(targets, {"course": {**current, "read": {"assignment"}}}),
                         [{"item_id": "item", "course_id": "course", "assignment_id": "assignment", "manual": True, "graded": 1, "hidden": 0}])
        self.assertEqual(canvas_posting.read_rows(targets, {"course": {**current, "read": set()}}), [])


class PostingBase(SyncBase):
    """After the first sync: Exam 1 goes to course A's "Test 1" (Alice's 88 is hidden; Bob's 50 waits for review and shows); course B
    has no assignment for it. Quiz 1 goes to both courses' "Quiz 1", every grade shown; Bob's grade there is held (Faculty's 7,
    Canvas's 8). Every assignment posts by hand."""

    def setUp(self):
        super().setUp()
        self.exam, self.quiz = self.items["Exam 1"], self.items["Quiz 1"]

    def choose(self, item, choice, request_id=None, status=200, **extra):
        answer = self.client.post(self.base + "/canvas-posting/choice", json={"item_id": item, "choice": choice, "confirmation": "choose",
                                                                              "request_id": request_id or f"choose-{time.monotonic_ns()}", **extra},
                                  headers=HEADERS)
        self.assertEqual(answer.status_code, status, answer.json)
        return answer.json

    def mark(self, item):
        return self.client.get(self.base).json.get("canvas_posting", {}).get(item, {})

    def settled(self, answer):
        """A sync followed until its send and the posts after it end: its send's last view (None when it sent nothing)."""
        sent = self.ended(answer)
        for _ in range(400):
            if not sent:
                return sent
            sent = self.client.get(f"{self.base}/canvas-send/{sent['id']}").json
            if not sent.get("posting"):
                return sent
            time.sleep(0.05)
        self.fail("the posts after the sync did not end")

    def sync_posting(self, request_id=None, held=None):
        plan = self.preview()
        return plan, self.settled(self.apply(plan, request_id or f"sync-{time.monotonic_ns()}", held))

    def post_receipts(self, post_id=None):
        with connect(self.app.config["DATABASE"]) as db:
            where, args = ("WHERE post_id=?", (post_id,)) if post_id else ("", ())
            return [(r[0], r[1]) for r in db.execute(f"SELECT course_id,state FROM gradebook_canvas_posted {where} ORDER BY rowid", args)]

    def graphql(self):
        return [w[2] for w in self.fake.writes if w[1] == "/api/graphql"]

    def posted(self, course, assignment):
        return {user: bool(s["posted_at"]) for (user, a), s in self.fake.submissions[course].items() if a == assignment}

    def save(self, change):
        """A setup revision: `change(setup)` returns the new setup."""
        view = self.client.get(self.base).json
        setup = {key: view["setup"][key] for key in ("categories", "items", "mode", "ungraded", "scale", "letter_rounding") if key in view["setup"]}
        saved = self.client.post(self.base + "/setup", json={"base_revision": view["setup"]["revision"], "request_id": f"setup-{time.monotonic_ns()}",
                                                              "setup": change(setup)}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        return saved.json

    def quizzes_rule(self, **posting):
        return self.save(lambda setup: {**setup, "categories": [{**c, "posting": posting or None} if c["name"] == "Quizzes" else c
                                                                for c in setup["categories"]]})


class ChoiceTests(PostingBase):
    def test_a_choice_asks_nothing_of_canvas_and_the_grid_says_what_the_next_sync_does(self):
        before = len(self.fake.requests)
        view = self.choose(self.exam, "post")
        self.assertEqual((len(self.fake.requests), self.graphql()), (before, []))
        mark = view["canvas_posting"][self.exam]
        self.assertEqual((mark["choice"], mark["pending"], mark["policy"]["why"], mark["hidden"]), ("post", ["post"], "choice", 1))
        mark = self.choose(self.exam, "rule", base=mark["choice_id"])["canvas_posting"][self.exam]
        self.assertNotIn("choice", mark)
        self.assertNotIn("pending", mark)  # no category: posted by hand, and nobody chose
        self.assertEqual((mark["policy"]["state"], mark["policy"]["manual"]), (None, True))

    def test_a_request_id_is_never_used_twice_and_a_stale_page_is_a_409(self):
        first = self.choose(self.exam, "post", request_id="choose-once-1")
        self.choose(self.exam, "post", request_id="choose-once-1")  # a replay
        self.assertIn("different change", self.choose(self.exam, "hide", request_id="choose-once-1", status=409)["error"])
        self.assertIn("another window", self.choose(self.exam, "hide", base=None, status=409)["error"])
        self.choose(self.exam, "hide", base=first["canvas_posting"][self.exam]["choice_id"])
        self.assertEqual(self.mark(self.exam)["choice"], "hide")
        answer = self.client.post(self.base + "/canvas-posting/choice", json={"item_id": self.exam, "choice": "post", "request_id": "no-confirm-1"},
                                  headers=HEADERS)
        self.assertEqual(answer.status_code, 400, answer.json)
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT choice FROM gradebook_canvas_posting_choices ORDER BY rowid")], ["post", "hide"])

    def test_a_column_kept_off_canvas_has_no_choice(self):
        saved = self.save(lambda setup: {**setup, "items": [{**i, "canvas": "off"} if i["title"] == "Exam 1" else i for i in setup["items"]]})
        self.assertIn("kept off Canvas", self.choose(self.exam, "post", status=400)["error"])
        self.assertNotIn("policy", saved.get("canvas_posting", {}).get(self.exam, {}))

    def test_the_assistant_proposes_a_choice_on_an_approve_card(self):
        rule = policy.APPROVE["gradebook.canvas_posting_choice"]
        self.assertEqual((rule.tier, rule.confirmation), ("approve", "choose"))
        self.assertNotIn("gradebook.canvas_posting", policy.PAGE)
        with patch("gradebook.api.actor", return_value="assistant"):
            self.choose(self.exam, "post")  # the card's confirmation, added on the instructor's click
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT actor FROM gradebook_canvas_posting_choices").fetchone()[0], "assistant")


class SyncPostingTests(PostingBase):
    def test_the_next_sync_posts_a_chosen_column_with_a_receipt_per_course(self):
        self.choose(self.exam, "post")
        plan, sent = self.sync_posting()
        shown, = [entry for entry in plan["posting_plan"] if entry["item_id"] == self.exam]
        self.assertEqual((shown["action"], shown["why"], shown["courses"], shown["grades"], shown["waits"]), ("post", "choice", 1, 1, []))
        post, = sent["posts"]
        self.assertEqual((post["status"], post["action"], post["send_id"], post["progress"]["accepted"]), ("done", "post", sent["id"], 1))
        self.assertEqual(self.post_receipts(post["id"]), [(self.course_a, "uncertain"), (self.course_a, "accepted")])
        self.assertEqual(self.graphql(), [{"mutation": "postAssignmentGrades", "assignmentId": self.test_a}])
        self.assertTrue(all(self.posted(self.course_a, self.test_a).values()))
        mark = self.mark(self.exam)
        self.assertEqual((mark["hidden"], mark["source"], mark.get("pending")), (0, "post", None))
        # Nothing left: the next sync asks nothing of Canvas; then Hide hides it.
        self.assertEqual(self.sync_posting()[1], None)
        self.choose(self.exam, "hide")
        _, sent = self.sync_posting()
        self.assertEqual([(p["action"], p["status"]) for p in sent["posts"]], [("hide", "done")])
        self.assertFalse(any(self.posted(self.course_a, self.test_a).values()))
        self.assertEqual((self.mark(self.exam)["hidden"], self.mark(self.exam)["source"]), (2, "hide"))

    def test_posted_stays_posted_and_a_held_grade_holds_the_post_there(self):
        self.choose(self.quiz, "post")
        self.canvas(self.course_a, self.quiz_a, self.alice)["posted_at"] = None  # Canvas hides a grade that came in after posting
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        plan, sent = self.sync_posting()
        shown, = [entry for entry in plan["posting_plan"] if entry["item_id"] == self.quiz]
        self.assertEqual((shown["courses"], shown["waiting"]), (0, 1))
        self.assertIn("wait for your choice", shown["waits"][0])  # Bob's grade changed in Canvas
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 10.0)  # sent, still hidden
        self.assertFalse(self.posted(self.course_a, self.quiz_a)[self.alice])
        self.assertEqual(self.graphql(), [])
        key = next(h["key"] for h in self.preview()["held"] if h["name"] == "Example, Bob")
        _, sent = self.sync_posting(held={key: "faculty"})
        self.assertEqual([(p["action"], p["status"]) for p in sent["posts"]], [("post", "done")])
        self.assertTrue(all(self.posted(self.course_a, self.quiz_a).values()))
        self.assertEqual(self.graphql(), [{"mutation": "postAssignmentGrades", "assignmentId": self.quiz_a}])  # course B showed everything

    def test_a_post_waits_where_its_grades_did_not_reach_canvas(self):
        self.choose(self.exam, "post")
        self.type("Example, Alice", "Exam 1", status="scored", points="95")
        self.fake.fail_writes = {("POST", f"/assignments/{self.test_a}/submissions/update_grades")}
        _, sent = self.sync_posting()
        post, = sent["posts"]
        self.assertEqual(post["status"], "failed")
        self.assertIn("did not reach Canvas", post["progress"]["errors"][0]["message"])
        self.assertEqual(self.graphql(), [])

    def test_the_category_s_due_date_rule_posts_the_day_after(self):
        self.quizzes_rule(due=True)
        saved = self.save(lambda setup: {**setup, "items": [{**i, "due_date": "2026-01-01"} if i["title"] == "Quiz 1" else i for i in setup["items"]]})
        self.assertEqual(saved["canvas_posting"][self.quiz]["policy"]["why"], "due")
        self.canvas(self.course_b, self.quiz_b, self.carol)["posted_at"] = None  # Carol's grade came in hidden
        plan, sent = self.sync_posting()
        shown, = [entry for entry in plan["posting_plan"] if entry["item_id"] == self.quiz]
        self.assertEqual((shown["why"], shown["courses"], shown["grades"]), ("due", 1, 1))
        self.assertTrue(self.posted(self.course_b, self.quiz_b)[self.carol])
        self.assertEqual([w for w in self.graphql() if w["mutation"] == "postAssignmentGrades"][-1]["assignmentId"], self.quiz_b)
        changes = self.client.get(self.base + "/changes").json["changes"]
        self.assertTrue(any("posted in Canvas by hand → after its due date" in line for entry in changes for line in entry.get("changes", [])), changes)
        # Due today is not past due.
        from scheduling.clock import campus_today
        self.canvas(self.course_b, self.quiz_b, self.carol)["posted_at"] = None
        self.save(lambda setup: {**setup, "items": [{**i, "due_date": campus_today().isoformat()} if i["title"] == "Quiz 1" else i for i in setup["items"]]})
        self.assertEqual([e for e in self.preview()["posting_plan"] if e["item_id"] == self.quiz], [])

    def test_a_by_hand_column_s_automatic_assignment_is_switched_before_its_grades(self):
        self.fake.assignment(self.course_b, self.quiz_b)["post_manually"] = False
        self.type("Example, Carol", "Quiz 1", status="scored", points="4")
        plan, sent = self.sync_posting()
        self.assertEqual([(e["action"], e["courses"]) for e in plan["posting_plan"] if e["item_id"] == self.quiz], [("switch", 1)])
        mutations = [i for i, w in enumerate(self.fake.writes) if w[1] == "/api/graphql"]
        update = next(i for i, w in enumerate(self.fake.writes) if w[1].endswith(f"/assignments/{self.quiz_b}/submissions/update_grades"))
        self.assertEqual(self.fake.writes[mutations[0]][2], {"mutation": "setAssignmentPostPolicy", "assignmentId": self.quiz_b, "postManually": True})
        self.assertLess(mutations[0], update)
        self.assertTrue(self.fake.assignment(self.course_b, self.quiz_b)["post_manually"])
        self.assertEqual((sent["progress"]["switched"], sent["progress"]["accepted"]), (1, 1))
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT state FROM gradebook_canvas_policies ORDER BY rowid")], ["uncertain", "accepted"])
        # A category that posts by itself is never switched.
        self.fake.assignment(self.course_b, self.quiz_b)["post_manually"] = False
        self.quizzes_rule(due=True)
        self.assertEqual([e for e in self.preview()["posting_plan"] if e["action"] == "switch"], [])

    def test_a_refused_switch_sends_nothing_there(self):
        self.fake.assignment(self.course_b, self.quiz_b)["post_manually"] = False
        self.fake.policy_answer = (200, {"data": {"setAssignmentPostPolicy": {"postPolicy": None, "errors": [{"message": "not allowed"}]}}})
        self.type("Example, Carol", "Quiz 1", status="scored", points="4")
        _, sent = self.sync_posting()
        self.assertEqual((sent["progress"]["failed"], sent["progress"]["switched"]), (1, 0))
        self.assertIn("refused to make “Quiz 1” post by hand", sent["progress"]["errors"][0]["message"])
        self.assertEqual(self.canvas(self.course_b, self.quiz_b, self.carol)["score"], 6)
        self.assertEqual(self.receipts(self.carol), ["failed"])

    def test_a_choice_made_after_the_summary_is_a_409_and_old_ticks_are_refused(self):
        plan = self.preview()
        self.choose(self.exam, "post")
        self.assertEqual(self.apply(plan, "stale-sync-1").status_code, 409)
        plan = self.preview()
        answer = self.client.post(self.base + "/canvas-sync", json={"options": plan["options"], "fingerprint": plan["fingerprint"],
                                                                   "request_id": "old-tick-1", "confirmation": "sync", "post": [self.exam]}, headers=HEADERS)
        self.assertEqual(answer.status_code, 400, answer.json)
        self.assertIn("reload the page", answer.json["error"])
        for path in ("/canvas-posting/preview", "/canvas-posting"):
            self.assertIn(self.client.post(self.base + path, json={"item_id": self.exam, "action": "post"}, headers=HEADERS).status_code, (404, 405))

    def test_canvas_refusing_is_failed_and_your_next_sync_asks_again(self):
        self.choose(self.exam, "post")
        self.fake.graphql_answer = (200, {"errors": [{"message": "not allowed to post"}]})
        _, sent = self.sync_posting()
        post, = sent["posts"]
        self.assertEqual((post["status"], post["progress"]["failed"]), ("failed", 1))
        self.assertIn("not allowed to post", post["progress"]["errors"][0]["message"])
        self.assertEqual(self.post_receipts(post["id"]), [(self.course_a, "uncertain"), (self.course_a, "failed")])
        self.fake.graphql_answer = None
        self.assertEqual(self.mark(self.exam)["pending"], ["post"])
        _, sent = self.sync_posting()
        self.assertEqual([p["status"] for p in sent["posts"]], ["done"])
        self.assertEqual(len(self.graphql()), 2)

    def test_an_unfinished_post_whose_read_back_fails_stays_uncertain(self):
        self.choose(self.exam, "post")
        self.fake.posting_state = "running"
        real, original = web.Client.assignment_submissions, canvas_posting.run
        armed = threading.Event()

        def cut(client, course, assignment):
            if armed.is_set():
                raise web.CanvasError("Canvas could not be reached", status=None)
            return real(client, course, assignment)

        def run(*args, **kwargs):
            armed.set()  # only the post's own read-back fails
            return original(*args, **kwargs)
        with patch.object(canvas_posting, "WAIT", 0.2), patch.object(web.Client, "assignment_submissions", cut), patch.object(canvas_posting, "run", run):
            _, sent = self.sync_posting()
        post, = sent["posts"]
        self.assertEqual((post["status"], post["progress"]["uncertain"]), ("failed", 1))
        self.assertEqual(self.post_receipts(post["id"])[-1], (self.course_a, "uncertain"))

    def test_a_signed_out_session_ends_the_post_failed(self):
        self.choose(self.exam, "post")
        with patch.object(web.Client, "post_grades", side_effect=web.SignedOut("Sign in to Canvas again.")):
            _, sent = self.sync_posting()
        post, = sent["posts"]
        self.assertEqual((post["status"], post["progress"].get("signed_out"), post["progress"]["failed"]), ("failed", True, 1))
        self.assertEqual(self.post_receipts(post["id"])[-1], (self.course_a, "failed"))

    def test_a_restart_waits_for_the_posts_after_a_sync(self):
        self.choose(self.exam, "post")
        self.fake.posting_state = "running"
        writes = self.app.extensions["canvas"].writes
        with patch.object(canvas_posting, "WAIT", 1.0):
            answer = self.apply(self.preview(), "restart-wait-1")
            self.assertTrue(writes.busy("after:"))
            self.settled(answer)
        self.assertEqual(writes.busy("after:"), [])

    def test_a_column_posted_by_hand_everywhere_is_posted_for_the_gradebook_s_zeros(self):
        """GRADEBOOK ZERO ONCE DUE, POSTED (requirement, 2026-10-08):
        Faculty's latest read of every Canvas assignment of the column shows it posted by hand, with grades, none hidden."""
        with connect(self.app.config["DATABASE"]) as db:
            self.assertNotIn(self.exam, canvas_posting.posted(db, self.term))  # Alice's 88 is hidden
        self.choose(self.exam, "post")
        self.sync_posting()
        with connect(self.app.config["DATABASE"]) as db:
            self.assertIn(self.exam, canvas_posting.posted(db, self.term))
            # Automatic posting says nothing (Canvas shows each grade as it comes in); one course still hiding, or no grades, is not posted.
            canvas_posting.record(db, self.term, [
                {"item_id": "auto", "course_id": "91", "assignment_id": "1", "manual": False, "graded": 5, "hidden": 0},
                {"item_id": "split", "course_id": "91", "assignment_id": "2", "manual": True, "graded": 5, "hidden": 0},
                {"item_id": "split", "course_id": "92", "assignment_id": "3", "manual": True, "graded": 5, "hidden": 1},
                {"item_id": "none", "course_id": "91", "assignment_id": "4", "manual": True, "graded": 0, "hidden": 0},
                {"item_id": "both", "course_id": "91", "assignment_id": "5", "manual": True, "graded": 5, "hidden": 0},
                {"item_id": "both", "course_id": "92", "assignment_id": "6", "manual": True, "graded": 3, "hidden": 0}], "sync")
            self.assertEqual(canvas_posting.posted(db, self.term) & {"auto", "split", "none", "both"}, {"both"})

    def test_a_student_who_left_the_canvas_course_never_counts_as_hidden(self):
        """requirement, 2026-10-07: the grid said "10 hidden" while Post found nothing to post. A course's full read keeps the
        submissions of students who left it; Canvas neither lists them for the assignment nor posts them."""
        section = self.fake.enrollments[self.course_a][0]["course_section_id"]
        for name, sis, state in (("Left, Eve", "555666777", "inactive"), ("Done, Finn", "555666778", "completed")):
            self.fake.grade(self.course_a, self.test_a, self.fake.add_student(self.course_a, section, name, sis, state=state), 70, posted=False)
        self.choose(self.exam, "post")
        shown, = [entry for entry in self.preview()["posting_plan"] if entry["item_id"] == self.exam]
        self.assertEqual(shown["grades"], 1)  # Alice's 88 only
        self.assertEqual(self.mark(self.exam)["hidden"], 1)
        _, sent = self.sync_posting()
        self.assertEqual([(p["status"], p["progress"]["accepted"]) for p in sent["posts"]], [("done", 1)])
        mark = self.mark(self.exam)
        self.assertEqual((mark["hidden"], mark["graded"]), (0, 2))  # Alice and Bob; the two who left are still unposted in Canvas
        self.assertEqual([e for e in self.preview()["posting_plan"] if e["item_id"] == self.exam], [])

    def test_sync_keeps_what_it_read_for_the_grid_mark(self):
        mark = self.mark(self.exam)
        self.assertEqual((mark["hidden"], mark["source"]), (1, "sync"))  # the first sync read Alice's hidden 88
