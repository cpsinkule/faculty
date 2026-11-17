"""GRADEBOOK / CANVAS / ASSISTANT (2026-10-09 review of preparations), offline against the loopback FakeCanvas.

The term's reconciliation projection compares Canvas by Sync's own column map (a column kept off Canvas is not Canvas drift) and
marks a Faculty change that only waits for the next Sync. Only the process that runs Faculty's Canvas writes (CanvasService.owner:
the web process, where the morning sync starts) settles a send or post it no longer runs as `interrupted`; the Inbox worker's daily
reconciliation read shows them as they are, so a post still running in the web process keeps its real outcome.
"""
import uuid
from datetime import timedelta

from test_canvas_nightly import MORNING, MorningBase

from gradebook import canvas_posting
from rosters import connect


class ReconciliationTests(MorningBase):
    def reconciliation(self):
        answer = self.client.get(self.base + "/reconciliation")
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def canvas_differences(self, item):
        return [d for d in self.reconciliation()["differences"] if d["source"] == "canvas" and d["item_id"] == item]

    def test_a_faculty_change_waits_for_the_next_sync_and_a_column_kept_off_canvas_is_not_drift(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="8")
        found = self.canvas_differences(self.quiz)
        alice = [d for d in found if d["row"] == self.rows["Example, Alice"]]
        self.assertTrue(alice and all(d["faculty_changed_since_sync"] for d in alice), found)
        self.save(lambda setup: {**setup, "items": [{**item, "canvas": "off"} if item["id"] == self.quiz else item for item in setup["items"]]})
        self.assertEqual(self.canvas_differences(self.quiz), [])

    def running_post(self, send_id=None, at=None):
        ident = str(uuid.uuid4())
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("""INSERT INTO gradebook_canvas_posts(id,term_id,item_id,action,send_id,request_id,request_sha256,fingerprint,
                plan_json,status,progress_json,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (ident, self.term, self.quiz, "post", send_id, "post-" + ident, "x", "x", "{}", "running", "{}", "instructor",
                        (at or MORNING).isoformat()))
        return ident

    def owner(self, value):
        canvas = self.app.extensions["canvas"]
        previous, canvas.owner = canvas.owner, value
        self.addCleanup(setattr, canvas, "owner", previous)

    def status(self, post):
        with connect(self.app.config["DATABASE"]) as db:
            return db.execute("SELECT status FROM gradebook_canvas_posts WHERE id=?", (post,)).fetchone()[0]

    def test_only_the_process_running_canvas_writes_settles_a_running_post_or_send(self):
        gradebook = self.app.extensions["gradebook"]
        self.assertFalse(self.app.extensions["canvas"].owner)  # create_app alone: the Inbox worker, a command, these checks
        post = self.running_post()
        self.assertEqual(canvas_posting.status(gradebook, self.term, post)["status"], "running")
        send = str(uuid.uuid4())
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("""INSERT INTO gradebook_canvas_sends(id,term_id,request_id,request_sha256,fingerprint,plan_json,status,actor,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (send, self.term, "send-" + send, "x", "x", "{}", "running", "instructor", MORNING.isoformat()))
            row = db.execute("SELECT * FROM gradebook_canvas_sends WHERE id=?", (send,)).fetchone()
        self.assertEqual(gradebook._send_view(row)["status"], "running")
        self.assertEqual(self.status(post), "running")
        self.owner(True)  # the web process: a running write it does not hold was cut off by a restart
        self.assertEqual(canvas_posting.status(gradebook, self.term, post)["status"], "interrupted")
        self.assertEqual(gradebook._send_view(row)["status"], "interrupted")

    def test_the_workers_daily_read_never_ends_a_post_the_web_process_runs(self):
        self.type("Example, Alice", "Quiz 1", status="scored", points="8")
        self.assertEqual(self.morning_run(), {self.term: "synced"})
        with connect(self.app.config["DATABASE"]) as db:
            send = db.execute("SELECT send_ref FROM gradebook_canvas_nightly_runs ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()[0]
        self.assertTrue(send)
        post = self.running_post(send, MORNING + timedelta(minutes=1))
        workflows = self.app.extensions["assistant"].workflows
        observation = workflows.observe("reconciliation", self.term)  # as process_one reads it in the Inbox worker
        self.assertEqual(self.status(post), "running")
        self.assertNotIn("posts_failed", observation["canvas"]["nightly"])
        self.owner(True)  # control: the same read in the web process settles a post nobody runs
        workflows.observe("reconciliation", self.term)
        self.assertEqual(self.status(post), "interrupted")

    def test_the_daily_fingerprint_ignores_sync_and_observation_times(self):
        workflows = self.app.extensions["assistant"].workflows
        self.morning_run()
        first = workflows.observe("reconciliation", self.term)
        self.morning_run(MORNING + timedelta(days=1))  # a later morning with nothing to do: new times, the same facts
        second = workflows.observe("reconciliation", self.term)
        self.assertNotEqual(first["canvas"]["nightly"]["at"], second["canvas"]["nightly"]["at"])
        self.assertEqual(workflows.fingerprint(first), workflows.fingerprint(second))
        self.type("Example, Carol", "Quiz 1", status="scored", points="2")  # only waits for the next Sync
        self.assertEqual(workflows.fingerprint(first), workflows.fingerprint(workflows.observe("reconciliation", self.term)))
        self.save(lambda setup: {**setup, "items": [{**item, "title": "Quiz one"} if item["id"] == self.quiz else item
                                                    for item in setup["items"]]})
        mark, parts = workflows.marks(workflows.observe("reconciliation", self.term))
        self.assertNotEqual(mark, workflows.fingerprint(first))
        self.assertNotEqual(parts["columns"], workflows.marks(first)[1]["columns"])
