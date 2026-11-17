"""THE MORNING SYNC (gradebook/canvas_nightly.py; docs/decisions/canvas.md NIGHTLY SYNC; requirement, 2026-10-08), offline against the loopback FakeCanvas, for a term a sync has written to: the campus clock, only
the courses written to before, held grades left for the instructor, a value whose last send is uncertain never sent again by itself,
unpublished assignments published as the summary ticks them, posting by choice, an uncertain post never asked again by itself, a
signed-out morning tried again, a stopping Faculty writing nothing, and Jev never asked. CONFIRMED BY A LATER READ
(gradebook/canvas_confirm.py; requirement, 2026-10-10): a grade Canvas applies after Faculty stopped waiting is confirmed by the
morning's later rounds, which then carry out the post withheld for it, and by any later read of a sync."""
import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_canvas_posting import PostingBase

from canvas import web
from gradebook import canvas_confirm, canvas_nightly, canvas_posting
from gradebook.service import GradebookService
from rosters import connect

# Oct 5, 2026 in Central daylight time (UTC-5): 4:55 am and 5:05 am.
EARLY = datetime(2026, 10, 5, 9, 55, tzinfo=timezone.utc)
MORNING = datetime(2026, 10, 5, 10, 5, tzinfo=timezone.utc)


class MorningBase(PostingBase):
    """After the first sync, the instructor's Sync wrote Alice's 10 on Quiz 1 to course A: course A is written to, course B is not."""

    def setUp(self):
        super().setUp()
        self.type("Example, Alice", "Quiz 1", status="scored", points="10")
        self.sync_posting()
        self.morning = self.app.extensions["canvas_nightly"]

    def runs(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in
                    db.execute("SELECT night,kind,status,detail_json,request_ref,send_ref FROM gradebook_canvas_nightly_runs ORDER BY created_at,rowid")]

    def morning_run(self, when=MORNING):
        """A morning round, then its send and the posts after it followed to the end."""
        ran = self.morning.round(when)
        run = (self.runs() or [{}])[-1]
        if run.get("send_ref"):
            self.settled(type("Answer", (), {"status_code": 200, "json": {"send": {"id": run["send_ref"], "status": "running"}}})())
        return ran

    def requests(self):
        with connect(self.app.config["DATABASE"]) as db:
            return db.execute("SELECT COUNT(*) FROM gradebook_requests").fetchone()[0]


class MorningTests(MorningBase):
    def test_from_five_am_once_a_morning_only_the_courses_written_to_before(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="8")
        self.type("Example, Carol", "Quiz 1", status="scored", points="4")
        with connect(self.app.config["DATABASE"]) as db:
            snapshots = db.execute("SELECT COUNT(*) FROM gradebook_canvas_snapshots").fetchone()[0]
        self.assertEqual(self.morning_run(EARLY), {})
        self.assertEqual(self.morning_run(), {self.term: "synced"})
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 8.0)
        self.assertEqual(self.canvas(self.course_b, self.quiz_b, self.carol)["score"], 6)  # course B's first write is the instructor's
        run = self.runs()[-1]
        self.assertEqual((run["night"], run["kind"], run["status"], run["detail"]["courses"], run["detail"]["sending"]),
                         ("2026-10-05", "sync", "synced", [self.course_a], 1))
        with connect(self.app.config["DATABASE"]) as db:
            summary = json.loads(db.execute("SELECT summary_json FROM gradebook_requests WHERE id=?", (run["request_ref"],)).fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM gradebook_canvas_snapshots").fetchone()[0], snapshots + 1)
            send = db.execute("SELECT status FROM gradebook_canvas_sends WHERE id=?", (run["send_ref"],)).fetchone()[0]
        self.assertEqual((summary["automatic"], summary["sync"]["automatic"], send), ("canvas-sync", True, "done"))
        view = self.client.get(self.base).json["canvas"]
        self.assertEqual((view["nightly"]["kind"], view["nightly"]["status"], view["nightly"]["send"]["status"]), ("sync", "synced", "done"))
        self.assertTrue(view["last_sync"]["automatic"])
        self.assertEqual(self.morning_run(MORNING + timedelta(minutes=30)), {})  # this morning is done
        # The next morning with nothing to do records the morning and no request.
        requests = self.requests()
        self.assertEqual(self.morning_run(MORNING + timedelta(days=1)), {self.term: "unchanged"})
        self.assertEqual(self.requests(), requests)

    def test_a_held_grade_waits_for_the_instructor(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="8")
        self.morning_run()
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.bob)["score"], 8)  # Canvas's 8, Faculty's 7: still held
        self.assertEqual(self.runs()[-1]["detail"]["held"], 1)
        self.assertEqual(len(self.preview()["held"]), 1)

    def test_a_value_whose_last_send_is_uncertain_waits_for_your_sync(self):
        # Canvas takes the 9 and does not apply it: the read-back still shows 10, and so does the morning's read.
        self.type("Example, Alice", "Quiz 1", status="scored", points="9")
        self.fake.hold_grades = True
        with patch.object(GradebookService, "SEND_WAIT", 0.1):
            self.ended(self.apply(self.preview(), "slow-canvas-1"))
        self.fake.hold_grades = False
        self.assertEqual(self.receipts(self.alice)[-1], "uncertain")
        self.type("Example, Alice", "Quiz 1", status="scored", points="7")
        self.assertEqual(self.morning_run(), {self.term: "unchanged"})
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 10.0)  # never sent again by itself
        self.assertEqual((self.runs()[-1]["detail"]["unsure"], self.client.get(self.base).json["canvas"]["nightly"]["unsure"]), (1, 1))
        self.assertEqual(self.receipts(self.alice)[-1], "uncertain")  # a read that does not show it confirms nothing
        self.ended(self.apply(self.preview(), "your-sync-1"))  # the instructor's own Sync sends it
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 7.0)

    def test_an_unpublished_assignment_is_published_as_the_summary_ticks_it(self):
        assignment = self.fake.assignment(self.course_a, self.quiz_a)
        assignment.update(published=False, workflow_state="unpublished")
        self.type("Example, Alice", "Quiz 1", status="scored", points="8")
        self.assertEqual(self.morning_run(), {self.term: "synced"})
        self.assertTrue(self.fake.assignment(self.course_a, self.quiz_a)["published"])
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 8.0)
        self.assertEqual(self.runs()[-1]["detail"]["publishing"], 1)

    def test_a_chosen_post_is_carried_out_in_the_morning(self):
        self.choose(self.exam, "post")
        self.assertEqual(self.morning_run(), {self.term: "synced"})
        self.assertTrue(all(self.posted(self.course_a, self.test_a).values()))
        self.assertEqual(self.runs()[-1]["detail"]["posting"], {"post": 1})
        self.assertEqual(self.mark(self.exam)["source"], "post")

    def test_an_uncertain_post_is_never_asked_again_by_the_morning(self):
        self.choose(self.exam, "post")
        self.fake.posting_state = "running"
        real, original = web.Client.assignment_submissions, canvas_posting.run
        armed = threading.Event()

        def cut(client, course, assignment):
            if armed.is_set():
                raise web.CanvasError("Canvas could not be reached", status=None)
            return real(client, course, assignment)

        def run(*args, **kwargs):
            armed.set()
            return original(*args, **kwargs)
        with patch.object(canvas_posting, "WAIT", 0.2), patch.object(web.Client, "assignment_submissions", cut), patch.object(canvas_posting, "run", run):
            self.sync_posting()
        asked = len(self.graphql())
        self.fake.posting_state = "completed"
        for submission in self.fake.submissions[self.course_a].values():
            submission["posted_at"] = None if submission["assignment_id"] == self.test_a else submission["posted_at"]
        self.morning_run()
        self.assertEqual(len(self.graphql()), asked)  # not asked again by itself
        self.assertFalse(any(self.posted(self.course_a, self.test_a).values()))
        shown, = [entry for entry in self.preview()["posting_plan"] if entry["item_id"] == self.exam]
        self.assertEqual(shown["waits"], [])  # the instructor's own Sync does it
        self.sync_posting()
        self.assertTrue(all(self.posted(self.course_a, self.test_a).values()))

    def test_a_signed_out_morning_is_tried_again_hourly(self):
        self.fake.sessions.clear()
        with patch.object(canvas_nightly, "HOUR", 0), patch.object(canvas_nightly, "END", 24):
            self.assertEqual(self.morning.round(), {self.term: "signed_out"})
            self.assertEqual(self.morning.round(), {})  # within the hour: not again
            self.assertEqual((self.runs()[-1]["kind"], self.client.get(self.base).json["canvas"]["nightly"]["status"]), ("sync", "signed_out"))

    def test_a_stopping_faculty_starts_no_morning(self):
        writes = self.app.extensions["canvas"].writes
        writes.close()
        try:
            self.assertEqual(self.morning.round(MORNING), {})
        finally:
            writes._closing = False
        self.assertEqual(self.runs(), [])

    def test_a_running_morning_holds_a_restart(self):
        """AGENTS.md CHECKS (requirement, 2026-10-17): a restart waits while the morning sync runs, not for a fixed window. The
        morning registers with the Canvas writes while it reads and sends, and leaves nothing registered between rounds."""
        writes = self.app.extensions["canvas"].writes
        seen, read = [], self.app.extensions["canvas"].read_grades

        def reading(*args, **kwargs):
            seen.append(writes.labels())
            return read(*args, **kwargs)

        self.type("Example, Alice", "Quiz 1", status="scored", points="8")
        with patch.object(self.app.extensions["canvas"], "read_grades", side_effect=reading):
            self.assertEqual(self.morning_run(), {self.term: "synced"})
        self.assertIn("the morning sync with Canvas", seen[0])
        self.assertEqual(writes.busy("morning:"), [])

    def test_jev_is_never_asked(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="8")
        with patch("jev.matching.suggest", side_effect=AssertionError("the morning sync never asks Jev")):
            self.assertEqual(self.morning_run(), {self.term: "synced"})


class ConfirmedLaterTests(MorningBase):
    """CONFIRMED BY A LATER READ (gradebook/canvas_confirm.py; requirement, 2026-10-10, after the morning sync's five grades reached
    Canvas minutes after Faculty's two-minute wait and the gradebook kept two warnings): reading Canvas again is never a resend."""

    LATER = MORNING + timedelta(minutes=5)

    def slow_morning(self):
        """A morning sync whose grades Canvas takes and has not applied when Faculty stops waiting: its send, as it ended."""
        self.fake.hold_grades = True
        with patch.object(GradebookService, "SEND_WAIT", 0.1):
            self.assertEqual(self.morning_run(), {self.term: "synced"})
        self.fake.hold_grades = False
        return self.runs()[-1]["send_ref"]

    def posts(self, send_id):
        return self.client.get(f"{self.base}/canvas-send/{send_id}").json.get("posts", [])

    def test_a_grade_canvas_applies_late_is_confirmed_and_its_withheld_post_carried_out(self):
        self.choose(self.exam, "post")
        self.type("Example, Alice", "Exam 1", status="scored", points="95")
        send = self.slow_morning()
        self.assertEqual(self.receipts(self.alice)[-2:], ["uncertain", "uncertain"])  # before Canvas was asked, and read back
        self.assertEqual(self.canvas(self.course_a, self.test_a, self.alice)["score"], 88.123456)  # Canvas has not applied the 95
        withheld, = self.posts(send)
        self.assertEqual((withheld["status"], withheld["progress"]["errors"][0]["waits"], withheld["progress"]["errors"][0]["course_id"]),
                         ("failed", "grades", self.course_a))
        view = self.client.get(self.base).json["canvas"]
        self.assertEqual((view["last_sync"]["send"]["progress"]["uncertain"], view["last_sync"]["send"]["unconfirmed"], view["nightly"]["posts_failed"]),
                         (1, 1, 1))
        # Canvas still has not applied it: the next round reads, confirms nothing and asks nothing.
        sent = len(self.fake.writes)
        self.assertEqual(self.morning.round(self.LATER), {})
        self.assertEqual((self.receipts(self.alice)[-1], len(self.fake.writes), self.graphql()), ("uncertain", sent, []))
        # Canvas catches up: the round after confirms the grade by reading, then posts the column there, once.
        self.assertEqual(self.fake.apply_held_grades(), 1)
        self.assertEqual(self.morning.round(self.LATER + timedelta(minutes=5)), {})
        self.assertEqual(self.receipts(self.alice)[-1], "accepted")
        self.assertEqual(self.graphql(), [{"mutation": "postAssignmentGrades", "assignmentId": self.test_a}])
        self.assertTrue(all(self.posted(self.course_a, self.test_a).values()))
        first, followed = self.posts(send)
        self.assertEqual((first["status"], followed["status"], followed["progress"]["accepted"], followed["progress"]["confirmed"]["course_id"]),
                         ("failed", "done", 1, self.course_a))
        self.assertEqual(self.post_receipts(followed["id"]), [(self.course_a, "uncertain"), (self.course_a, "accepted")])
        view = self.client.get(self.base).json["canvas"]
        self.assertEqual((view["last_sync"]["send"]["progress"]["uncertain"], view["last_sync"]["send"]["unconfirmed"]), (1, 0))  # the record stands
        self.assertNotIn("posts_failed", view["nightly"])
        self.assertEqual((self.mark(self.exam)["hidden"], self.mark(self.exam)["source"]), (0, "post"))
        # Nothing is left to follow: later rounds read nothing and ask nothing; the grade was sent once.
        asked = len(self.fake.requests)
        self.assertEqual(self.morning.round(self.LATER + timedelta(minutes=10)), {})
        self.assertEqual((len(self.fake.requests), len(self.graphql())), (asked, 1))
        updates = [w for w in self.fake.writes if w[1].endswith(f"/assignments/{self.test_a}/submissions/update_grades")]
        self.assertEqual(len(updates), 1)

    def test_a_post_with_nothing_left_to_show_is_done_without_asking_canvas(self):
        # The morning of 2026-10-10: the late grades were of students whose grades already showed, so nothing was hidden.
        self.choose(self.exam, "post")
        self.sync_posting()  # Alice's 88 now shows
        asked = len(self.graphql())
        self.type("Example, Alice", "Exam 1", status="scored", points="95")
        send = self.slow_morning()
        self.assertEqual(self.client.get(self.base).json["canvas"]["nightly"]["posts_failed"], 1)
        self.fake.apply_held_grades()
        self.morning.round(self.LATER)
        self.assertEqual((self.receipts(self.alice)[-1], len(self.graphql())), ("accepted", asked))
        followed = self.posts(send)[-1]
        self.assertEqual((followed["status"], followed["progress"]["courses"], followed["progress"]["errors"]), ("done", 0, []))
        view = self.client.get(self.base).json["canvas"]
        self.assertEqual((view["last_sync"]["send"]["unconfirmed"], view["nightly"].get("posts_failed")), (0, None))

    def test_a_column_no_longer_to_be_posted_is_not_posted_by_the_follow_up(self):
        self.choose(self.exam, "post")
        self.type("Example, Alice", "Exam 1", status="scored", points="95")
        send = self.slow_morning()
        self.choose(self.exam, "rule")  # the instructor took the choice back before Canvas caught up
        self.fake.apply_held_grades()
        self.morning.round(self.LATER)
        self.assertEqual((self.receipts(self.alice)[-1], self.graphql()), ("accepted", []))
        self.assertEqual(self.posts(send)[-1]["status"], "done")
        self.assertFalse(self.posted(self.course_a, self.test_a)[self.alice])

    def test_a_later_sync_takes_over_and_the_follow_up_stops(self):
        self.choose(self.exam, "post")
        self.type("Example, Alice", "Exam 1", status="scored", points="95")
        send = self.slow_morning()
        self.assertEqual(self.client.get(self.base).json["canvas"]["nightly"]["posts_failed"], 1)
        self.fake.apply_held_grades()
        self.sync_posting()  # the instructor's own Sync: its read confirms the grade, and it posts the column itself
        self.assertEqual(self.receipts(self.alice)[-1], "accepted")
        self.assertNotIn("posts_failed", self.client.get(self.base).json["canvas"]["nightly"])  # that Sync tried again: nothing left to say
        self.assertTrue(all(self.posted(self.course_a, self.test_a).values()))
        asked, posts = len(self.fake.requests), len(self.posts(send))
        self.assertEqual(self.morning.round(self.LATER), {})
        self.assertEqual((len(self.fake.requests), len(self.posts(send))), (asked, posts))  # the morning's send is no longer the last sync

    def test_the_next_morning_s_read_confirms_what_your_own_sync_could_not_read_back(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="9")
        real = web.Client.assignment_submissions

        def reading(client, course_id, assignment_id):
            if threading.current_thread().name.startswith("canvas-sync"):  # the send's read-back, not the summary's read
                raise web.SignedOut("Canvas no longer accepts Faculty's session. Sign in to Canvas again.", status=401)
            return real(client, course_id, assignment_id)
        with patch("canvas.web.Client.assignment_submissions", reading):
            self.ended(self.apply(self.preview(), "cut-off-1"))
        self.assertEqual(self.receipts(self.alice)[-1], "uncertain")
        self.assertEqual(self.client.get(self.base).json["canvas"]["last_sync"]["send"]["unconfirmed"], 1)
        requests = self.requests()
        self.assertEqual(self.morning_run(), {self.term: "unchanged"})  # Canvas shows the 9: nothing to send
        self.assertEqual((self.receipts(self.alice)[-1], self.requests()), ("accepted", requests))
        view = self.client.get(self.base).json["canvas"]
        self.assertEqual((view["last_sync"]["send"]["unconfirmed"], view["nightly"].get("unsure")), (0, None))
        # A grade changed since then goes by itself the morning after: its last send is no longer uncertain.
        self.type("Example, Alice", "Quiz 1", status="scored", points="7")
        self.assertEqual(self.morning_run(MORNING + timedelta(days=1)), {self.term: "synced"})
        self.assertEqual(self.canvas(self.course_a, self.quiz_a, self.alice)["score"], 7.0)

    def test_a_read_canvas_refuses_changes_nothing_and_the_next_round_reads_again(self):
        self.type("Example, Alice", "Exam 1", status="scored", points="95")
        self.slow_morning()
        self.fake.apply_held_grades()
        kept = set(self.fake.sessions)
        self.fake.sessions.clear()
        self.assertEqual(self.morning.round(self.LATER), {})
        self.assertEqual(self.receipts(self.alice)[-1], "uncertain")
        self.fake.sessions.update(kept)
        self.morning.round(self.LATER + timedelta(minutes=5))
        self.assertEqual(self.receipts(self.alice)[-1], "accepted")

    def test_only_the_latest_receipt_and_only_what_canvas_shows_as_sent_is_confirmed(self):
        self.type("Example, Alice", "Exam 1", status="scored", points="95")
        self.slow_morning()
        with connect(self.app.config["DATABASE"]) as db:
            waiting, = canvas_confirm.waiting(db, self.term)
            key = (waiting["assignment_id"], waiting["canvas_user_id"])
            shows = lambda score, **more: {self.course_a: {"submissions": {key: {"score": score, "entered_score": score, "excused": False}}, **more}}
            self.assertEqual(canvas_confirm.record(db, self.term, shows(88.123456)), 0)  # the old grade
            self.assertEqual(canvas_confirm.record(db, self.term, shows(95, read=[])), 0)  # that assignment was not read
            self.assertEqual(canvas_confirm.record(db, self.term, {self.course_a: {"submissions": {}}}), 0)  # no submission listed
            self.assertEqual(canvas_confirm.record(db, self.term, {self.course_b: shows(95)[self.course_a]}), 0)  # another course
            self.assertEqual(canvas_confirm.left(db, self.term, waiting["send_id"]), 1)
            self.assertEqual(canvas_confirm.record(db, self.term, shows(95, read=[waiting["assignment_id"]])), 1)
            self.assertEqual((canvas_confirm.record(db, self.term, shows(95)), canvas_confirm.waiting(db, self.term)), (0, []))  # once
            self.assertEqual(canvas_confirm.left(db, self.term, waiting["send_id"]), 0)
            last = db.execute("SELECT send_id,state,sent_json,canvas_json FROM gradebook_canvas_sent ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual((last["send_id"], last["state"], json.loads(last["sent_json"]), json.loads(last["canvas_json"])),
                         (waiting["send_id"], "accepted", {"status": "scored", "points": "95"}, {"status": "scored", "points": "95"}))

    def test_posts_that_still_need_you_are_counted_per_column(self):
        post = lambda item, status, **progress: {"item_id": item, "action": "post", "status": status,
                                                 "progress": {"failed": 0, "uncertain": 0, "errors": [], **progress}}
        waits = lambda course: {"label": "Section 507", "message": canvas_posting.UNCONFIRMED, "waits": "grades", "course_id": course, "assignment_id": "9"}
        done = lambda item, course, **more: post(item, "done", confirmed={"course_id": course, "assignment_id": "9"}, **more)
        self.assertEqual(canvas_posting.outstanding([]), 0)
        self.assertEqual(canvas_posting.outstanding([post("a", "done", accepted=2)]), 0)
        self.assertEqual(canvas_posting.outstanding([post("a", "failed", errors=[waits("1"), waits("2")])]), 1)
        self.assertEqual(canvas_posting.outstanding([post("a", "failed", errors=[waits("1"), waits("2")]), done("a", "1")]), 1)  # course 2 still waits
        self.assertEqual(canvas_posting.outstanding([post("a", "failed", errors=[waits("1"), waits("2")]), done("a", "1"), done("a", "2")]), 0)
        self.assertEqual(canvas_posting.outstanding([post("a", "done", accepted=1, errors=[waits("2")])]), 1)  # done elsewhere, withheld here
        self.assertEqual(canvas_posting.outstanding([post("a", "failed", errors=[waits("1")]), done("a", "1", failed=1)]), 1)  # Canvas refused it
        self.assertEqual(canvas_posting.outstanding([post("a", "failed", errors=[{"label": "Section 507", "message": "Not done"}]), done("a", "1")]), 1)
        self.assertEqual(canvas_posting.outstanding([post("a", "failed"), post("b", "done", uncertain=1), post("c", "interrupted")]), 3)
