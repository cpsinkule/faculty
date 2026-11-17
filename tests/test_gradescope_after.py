"""Gradescope after certifying (requirement, 2026-10-12; GRADESCOPE "AFTER CERTIFYING"), offline against FakeGradescope, on the graded
workspace of Certification and Export's tests: section 501 with Alice and Bob (Versions A and B) and their final grades.

With "Send to Gradescope and publish the grades" ticked, Certify queues one run in its own transaction; the sender then makes the
assignment where a linked course has none, sends what is not there, and publishes each section that is clean. A late paper Faculty
certified and sent is published where its set already was."""
import json
import sqlite3
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assistant import automation  # noqa: E402
from grading import gradescope_after  # noqa: E402
from rosters import connect  # noqa: E402
import test_certification_automatic as late_fixture  # noqa: E402
import test_grading_certification as certification_fixture  # noqa: E402
import test_gradescope_mc as mc_fixture  # noqa: E402
import test_gradescope_mc_corrections as corrections_fixture  # noqa: E402
import test_gradescope_sheets as sheets_fixture  # noqa: E402
from test_gradescope_create import CreateCase  # noqa: E402

DECLINED = "Not on Gradescope for this test."


class AfterCase(certification_fixture.Certify):
    def after_body(self, preview, declined=()):
        """The dialog's request with the tick: a group left unanswered is one Faculty sends; `declined` groups are not sent."""
        body = self.body(preview, reason=DECLINED)
        body["gradescope"] = {group: (answer if group in declined else {"decision": "send"}) for group, answer in body["gradescope"].items()}
        return {**body, "gradescope_after": True}

    def certify_after(self, declined=(), status=201):
        return self.api("post", "", self.after_body(self.preview(), declined), status)

    def runs(self):
        with connect(self.path) as db:
            return [{**dict(r), "outcome": json.loads(r["outcome_json"]), "asked": json.loads(r["plan_json"])}
                    for r in db.execute("SELECT * FROM frq_gradescope_automatic_runs ORDER BY created_at,rowid")]

    def publications(self):
        with connect(self.path) as db:
            return [{**dict(r), "plan": json.loads(r["plan_json"])} for r in db.execute("SELECT * FROM frq_gradescope_publications ORDER BY created_at,rowid")]

    def work(self, turns=20):
        """The sender's turns, time passing between them, until nothing Faculty does by itself is left."""
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        for _ in range(turns):
            self.sender.drain()
            self.ready_now()
            with connect(self.path) as db:
                for table in ("frq_gradescope_automatic_runs", "frq_gradescope_automatic_sends", "frq_gradescope_publication_units", "frq_gradescope_creation_units",
                              "frq_gradescope_sheet_uploads", "frq_gradescope_mc_checks"):
                    db.execute(f"UPDATE {table} SET not_before=? WHERE not_before IS NOT NULL AND status IN ('pending','uncertain')", (past,))
                left = db.execute("""SELECT (SELECT COUNT(*) FROM frq_gradescope_automatic_runs WHERE status!='done')
                    + (SELECT COUNT(*) FROM frq_gradescope_publications WHERE status IN ('pending','running'))
                    + (SELECT COUNT(*) FROM frq_gradescope_sends WHERE status IN ('pending','running'))""").fetchone()[0]
            if not left:
                return
        self.fail(f"Faculty's Gradescope work did not finish: {self.runs()}")

    def published_on_gradescope(self):
        return [self.fake.assignments[self.container]["published"]] + [self.fake.assignments[v]["published"] for v in self.versions.values()]

    def after(self):
        return self.service.view(self.aid)["gradescope"].get("after") or []

    def automation(self):
        """The Assistant's Automation tab: the lines of what Faculty did by itself on Gradescope, and this behaviour's own row."""
        with connect(self.path) as db:
            title = self.frq.plan(db, self.aid)["title"]
            lines = [line["text"] for line in automation.done_by_faculty(db)["lines"] if "Gradescope" in line["text"]]
            return title, lines, automation.gradescope_after(db, self.sender)

    def change_alice(self, lines=("R3_3",)):
        """The instructor decides Alice's Question 20 again: the grade Gradescope has is no longer the final one."""
        alice = self.frq.response(next(r["id"] for r in self.frq.responses(self.aid, self.q[("q20", "A")]) if r["enrollment_id"] == self.enrollment))
        self.frq.save_review(alice["id"], {"version": alice["version"], "result_id": alice["current_result_id"], "status": "approved",
                                           "confirm_review": True, "selected_items": list(lines)})


class AfterCertifyingTests(AfterCase):
    def test_the_dialog_is_offered_it_and_a_ticked_certify_queues_one_run_with_the_certification(self):
        preview = self.preview()
        self.assertIs(preview["gradescope"]["after"], True)
        # Unticked, nothing changes: a group not on Gradescope is still sent first or declined with a reason, and nothing is queued.
        body = self.body(preview)
        body["gradescope"] = {group: {"decision": "send"} for group in body["gradescope"]}
        refused = self.api("post", "", body, 409)
        self.assertIn("Send these to Gradescope first", refused["error"])
        self.assertEqual(self.runs(), [])
        done = self.certify_after()
        [run] = self.runs()
        self.assertEqual((run["certification_id"], run["kind"], run["step"], run["status"], run["actor"]),
                         (done["certification"]["id"], "certified", "create", "pending", "workspace"))
        self.assertEqual(run["asked"], {"written": {"decision": "send"}})
        with connect(self.path) as db:
            event = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='grades_certified' ORDER BY id DESC LIMIT 1").fetchone()[0])
            recorded = json.loads(db.execute("SELECT gradescope_json FROM frq_certifications WHERE id=?", (run["certification_id"],)).fetchone()[0])
        self.assertIs(event["gradescope_after"], True)
        self.assertEqual(recorded["written"]["decision"], "send")
        self.assertNoUin(run)

    def test_a_certification_without_the_tick_is_what_it_always_was(self):
        self.certify()  # "do not send", with its reason
        self.assertEqual(self.runs(), [])
        with connect(self.path) as db:
            event = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='grades_certified' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertNotIn("gradescope_after", event)
        self.assertNotIn("after", self.service.view(self.aid)["gradescope"])
        self.assertEqual(self.sender.drain(), 0)
        self.assertEqual(self.published_on_gradescope(), [False] * 4)

    def test_faculty_sends_what_is_not_there_and_publishes_once_it_is(self):
        self.send()
        self.sender.drain()  # the instructor sent everything before; then one grade changed
        self.change_alice()
        before = len(self.fake.requests)
        self.certify_after()
        self.assertEqual([r for r in self.fake.requests[before:] if r["method"] != "GET"], [])  # certifying itself never contacts Gradescope
        self.assertEqual(self.after()[0]["state"], "working")
        self.work()
        [run] = self.runs()
        self.assertEqual((run["step"], run["status"]), ("done", "done"), run)
        self.assertEqual(run["outcome"]["create"], {})  # the section's assignment is linked: nothing to make
        self.assertEqual((run["outcome"]["written"]["papers"], run["outcome"]["written"]["grades"]), (0, 1))
        with connect(self.path) as db:
            send = dict(db.execute("SELECT * FROM frq_gradescope_sends WHERE id=?", (run["outcome"]["written"]["send_id"],)).fetchone())
        self.assertEqual((send["actor"], send["status"]), ("automatic", "complete"))
        self.assertEqual(json.loads(send["plan_json"])["automatic"], {"certification_id": run["certification_id"], "run": run["id"], "after": "certified"})
        [publication] = self.publications()
        self.assertEqual((publication["actor"], publication["status"], publication["id"]), ("automatic", "complete", run["outcome"]["publish"]["publication_id"]))
        self.assertEqual(publication["plan"]["automatic"]["after"], "certified")
        self.assertEqual(self.published_on_gradescope(), [True] * 4)  # Gradescope's own "Publish All Grades", read back
        [shown] = self.after()
        self.assertEqual((shown["state"], shown["kind"], shown["published"], shown["sent"]), ("done", "certified", ["501"], {"papers": 0, "grades": 1, "sheets": 0}))
        self.assertNotIn("held", shown)
        self.assertEqual(self.fake.forbidden_hits, [])
        self.assertFalse(any(r["path"].endswith("/notify_students") for r in self.fake.requests))  # nobody is emailed
        # Nothing is done twice: more turns find nothing.
        self.assertEqual(self.sender.drain(), 0)
        self.assertEqual(len(self.publications()), 1)
        title, lines, row = self.automation()
        self.assertEqual(lines, [f"Sent to Gradescope · Published 1 section on Gradescope · {title}"])
        self.assertEqual((row["last_outcome"], row.get("blocker"), row.get("waiting")), (lines[0], None, None))

    def test_a_section_gradescope_shows_less_than_fully_graded_is_held_until_the_instructor_publishes(self):
        self.send()
        self.sender.drain()
        self.fake.assignments[self.container]["graded"] = 62.5  # someone's paper there has a question nobody graded
        self.certify_after()
        self.work()
        [run] = self.runs()
        self.assertEqual(run["status"], "done")
        [held] = run["outcome"]["publish"]["held"]
        self.assertEqual((str(held["section"]), held["part"]), ("501", "written"))
        self.assertIn("62.5% graded", held["reason"])
        self.assertEqual(self.publications(), [])
        self.assertEqual(self.published_on_gradescope(), [False] * 4)
        self.assertEqual(self.after()[0]["held"], run["outcome"]["publish"]["held"])
        self.assertNoUin(run)
        title, lines, row = self.automation()
        self.assertEqual(lines, [f"1 section held back on Gradescope · {title}"])
        self.assertTrue(row["blocker"].startswith(f"Faculty did not publish section 501 of {title} on Gradescope: Gradescope shows it 62.5% graded"), row)
        # The instructor's own Publish grades still works, and answers the notice.
        view = self.sender.publish_preview(self.aid, {})
        self.sender.publish(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "publish", "fingerprint": view["fingerprint"]})
        self.work()
        self.assertEqual(self.published_on_gradescope(), [True] * 4)
        self.assertNotIn("held", self.after()[0])
        self.assertNotIn("blocker", self.automation()[2])

    def test_a_grade_that_is_not_on_gradescope_as_faculty_has_it_holds_its_section(self):
        self.send()
        self.sender.drain()
        self.change_alice()
        self.certify_after(declined=("written",))  # "Do not send these": Faculty sends nothing of them
        self.work()
        [run] = self.runs()
        self.assertEqual(run["asked"], {"written": {"decision": "do_not_send"}})  # the reason stays with the certification
        self.assertNotIn(DECLINED, run["plan_json"])
        self.assertEqual(run["outcome"]["written"], {"declined": True})
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_sends WHERE actor='automatic'").fetchone()[0], 0)
        [held] = run["outcome"]["publish"]["held"]
        self.assertEqual(held["reason"], "1 final grade of papers on Gradescope is not there as Faculty has it.")
        self.assertEqual(self.published_on_gradescope(), [False] * 4)

    def test_a_section_with_no_assignment_linked_gets_nothing_and_says_why(self):
        self.certify_after()
        self.work()
        [run] = self.runs()
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["outcome"]["create"]["notes"], ["Section 501: This section's Gradescope course is not linked yet."])
        self.assertEqual(run["outcome"]["written"]["notes"], ["Section 501: its Gradescope course is not linked yet."])
        self.assertEqual(run["outcome"]["publish"], {})
        self.assertEqual([r for r in self.fake.requests if r["method"] != "GET" and r["path"] != "/login"], [])
        self.assertEqual(self.after()[0]["notes"], run["outcome"]["create"]["notes"] + run["outcome"]["written"]["notes"])

    def test_a_section_only_papers_were_sent_for_keeps_its_grades_on_gradescope_and_is_held(self):
        self.send(self.sender.preview(self.aid, {"papers_only": True}), papers_only=True)
        self.sender.drain()
        self.certify_after()
        self.work()
        [run] = self.runs()
        self.assertEqual(run["outcome"]["written"]["notes"], ["Section 501: only papers were sent to Gradescope for it, so its grades are left to Gradescope."])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_grades").fetchone()[0], 0)
        self.assertIn("not there as Faculty has", run["outcome"]["publish"]["held"][0]["reason"])
        self.assertEqual(self.published_on_gradescope(), [False] * 4)

    def test_a_run_waits_for_a_send_of_the_plan_and_gives_up_after_six_hours(self):
        self.certify_after()
        self.send()  # the instructor's own send starts before the sender's next turn, and is still running
        [queued] = self.runs()
        self.sender._run_unit({**queued, "kind": "runs", "part": queued["kind"]})
        [run] = self.runs()
        self.assertEqual((run["step"], run["status"], run["error"]), ("create", "pending", "Waiting: Its papers and grades are going to Gradescope."))
        self.assertEqual(self.after()[0]["waiting"], run["error"])
        with connect(self.path) as db:
            db.execute("DROP TRIGGER frq_gradescope_automatic_run_identity")
            db.execute("UPDATE frq_gradescope_automatic_runs SET created_at=?,not_before=NULL", ((datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(),))
        [queued] = self.runs()
        self.sender._run_unit({**queued, "kind": "runs", "part": queued["kind"]})
        [run] = self.runs()
        self.assertEqual(run["status"], "done")
        self.assertIn("More than six hours passed since the certification", run["outcome"]["stopped"])
        self.assertEqual(self.after()[0]["stopped"], run["outcome"]["stopped"])

    def look(self):
        """One look of the sender at the newest run."""
        queued = self.runs()[-1]
        self.sender._run_unit({**queued, "kind": "runs", "part": queued["kind"]})
        return self.runs()[-1]

    def test_a_queued_run_stops_when_the_switch_is_turned_off(self):
        self.send()
        self.sender.drain()
        self.change_alice()
        self.certify_after()
        settings = self.frq.settings()
        self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "auto_gradescope": False}})
        before = len(self.fake.requests)
        run = self.look()
        self.assertEqual((run["status"], run["outcome"]), ("done", {"stopped": "“Send to Gradescope and publish after certifying” was turned off in Compute settings."}))
        self.assertEqual(self.fake.requests[before:], [])  # nothing was even read
        self.assertEqual(self.sender.drain(), 0)
        self.assertEqual(self.published_on_gradescope(), [False] * 4)

    def test_a_queued_run_stops_when_the_instructor_certifies_the_assignment_again(self):
        self.send()
        self.sender.drain()
        self.change_alice()
        self.certify_after()
        first = self.runs()[0]
        self.certify()  # again, without the tick ("do not send", with its reason): that certification decides
        self.assertEqual(len(self.runs()), 1)
        with connect(self.path) as db:
            db.execute("UPDATE frq_gradescope_automatic_runs SET not_before=NULL")
        self.assertEqual(self.sender.drain(), 1)
        [run] = self.runs()
        self.assertEqual((run["id"], run["status"]), (first["id"], "done"))
        self.assertIn("You certified this assignment again since", run["outcome"]["stopped"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_sends WHERE actor='automatic'").fetchone()[0], 0)
        self.assertEqual(self.published_on_gradescope(), [False] * 4)

    def test_something_of_the_plan_going_to_gradescope_while_the_plan_is_read_makes_the_run_look_again(self):
        self.send()
        self.sender.drain()
        self.certify_after(declined=("written",))
        for _ in range(3):  # create, sheets, written: nothing to do
            self.look()
        self.assertEqual(self.runs()[0]["step"], "publish")
        real, calls = self.sender._publish_plan, []

        def moving(aid, data, session=None):
            found = real(aid, data, session=session)
            if not calls:  # a send that started and settled between the plan's own reads
                found["view"]["problems"] = ["A send to Gradescope is still running for this assignment. Publish when it has finished."]
            calls.append(1)
            return found
        with patch.object(self.sender, "_publish_plan", side_effect=moving):
            run = self.look()
        self.assertEqual((run["step"], run["status"]), ("publish", "pending"))  # not "done, nothing published, nothing said"
        self.assertTrue(run["error"].startswith("Waiting:"), run["error"])
        self.work()
        self.assertEqual(self.published_on_gradescope(), [True] * 4)

    def test_a_set_gradescope_gives_no_graded_share_for_is_read_from_its_versions_and_held_when_they_say_nothing(self):
        self.send()
        self.sender.drain()
        self.fake.assignments[self.container]["graded"] = None  # the set's own row does not say; its versions do (100%)
        self.certify_after()
        self.work()
        self.assertEqual(self.published_on_gradescope(), [True] * 4)

    def test_an_assignment_gradescope_says_nothing_about_is_not_published_by_itself(self):
        self.send()
        self.sender.drain()
        for assignment in [self.container, *self.versions.values()]:
            self.fake.assignments[assignment]["graded"] = None
        self.certify_after()
        self.work()
        [run] = self.runs()
        self.assertEqual(run["outcome"]["publish"]["held"][0]["reason"], "Gradescope does not say how much of it is graded, so Faculty cannot tell that every paper there is.")
        self.assertEqual(self.published_on_gradescope(), [False] * 4)

    def test_a_step_cut_off_is_looked_at_again_and_nothing_is_recorded_twice(self):
        self.send()
        self.sender.drain()
        self.change_alice()
        self.certify_after()
        with connect(self.path) as db:  # the process stopped in the middle of a look
            db.execute("UPDATE frq_gradescope_automatic_runs SET status='working'")
        self.sender._recover()
        self.assertEqual(self.runs()[0]["status"], "pending")
        # Its send was recorded and the run's own row was not moved: the next look only moves it.
        real = self.sender._run_move
        with patch.object(self.sender, "_run_move", side_effect=lambda db, unit, did: None if unit["step"] == "written" and "send_id" in did and not seen else real(db, unit, did)) as _:
            seen = []
            for _ in range(6):
                if not self.sender.process_one():
                    break
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_sends WHERE actor='automatic'").fetchone()[0], 1)
            db.execute("UPDATE frq_gradescope_automatic_runs SET status='pending' WHERE status='working'")
        seen.append(True)
        self.work()
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_sends WHERE actor='automatic'").fetchone()[0], 1)
        self.assertEqual(self.runs()[0]["status"], "done")
        self.assertEqual(self.published_on_gradescope(), [True] * 4)

    def test_the_switch_off_offers_nothing_and_refuses_the_tick(self):
        settings = self.frq.settings()
        self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "auto_gradescope": False}})
        preview = self.preview()
        self.assertNotIn("after", preview["gradescope"])
        refused = self.api("post", "", self.after_body(preview), 409)
        self.assertIn("cannot send to Gradescope by itself", refused["error"])
        self.assertEqual(self.runs(), [])

    def test_a_malformed_tick_is_refused_and_a_finished_run_never_changes(self):
        preview = self.preview()
        self.api("post", "", {**self.body(preview), "gradescope_after": "yes"}, 400)
        self.certify_after()
        self.work()
        with connect(self.path) as db:
            for statement in ("UPDATE frq_gradescope_automatic_runs SET error='x'", "DELETE FROM frq_gradescope_automatic_runs",
                              "UPDATE frq_gradescope_automatic_runs SET plan_json='{}'"):
                with self.assertRaises(sqlite3.IntegrityError, msg=statement):
                    db.execute(statement)


class AfterCreateTests(AfterCase, CreateCase):
    """The section's Gradescope course is linked and has no assignment for this plan: Faculty makes it, as the instructor's Create."""

    def setUp(self):
        super().setUp()
        self.sender.link_section(self.section["id"], {"course_id": str(self.empty), "base": ""})

    def made(self):
        return {a["title"]: key for key, a in self.fake.assignments.items() if a["course"] == self.empty}

    def test_faculty_makes_the_assignment_sends_and_publishes(self):
        self.certify_after()
        self.work(turns=60)
        [run] = self.runs()
        self.assertEqual(run["status"], "done", run)
        with connect(self.path) as db:
            creation = dict(db.execute("SELECT * FROM frq_gradescope_creations WHERE id=?", (run["outcome"]["create"]["creation_id"],)).fetchone())
            target = dict(db.execute("SELECT * FROM frq_gradescope_targets WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (self.aid,)).fetchone())
        self.assertEqual((creation["actor"], creation["status"], creation["title"]), ("automatic", "complete", self.title))
        self.assertEqual(json.loads(creation["plan_json"])["automatic"]["run"], run["id"])
        self.assertIn(self.title, self.made())
        self.assertEqual((run["outcome"]["written"]["papers"], run["outcome"]["written"]["grades"] > 0), (2, True))
        [publication] = self.publications()
        self.assertEqual((publication["actor"], publication["status"]), ("automatic", "complete"))
        self.assertTrue(self.fake.assignments[int(target["container_id"])]["published"])
        [shown] = self.after()
        self.assertEqual((shown["made"], shown["published"], shown["state"]), (True, ["501"], "done"))
        title, lines, row = self.automation()  # never said to be a makeup version Faculty added
        self.assertEqual(lines, [f"Made the assignment on Gradescope · Sent to Gradescope · Published 1 section on Gradescope · {title}"])
        with connect(self.path) as db:
            self.assertEqual(automation.makeup_versions(db, self.sender)["last_outcome"], "No makeup version added by itself yet")
        self.assertEqual(self.fake.forbidden_hits, [])

    def test_an_assignment_that_may_be_this_one_under_another_title_is_never_duplicated(self):
        made = set(self.fake.assignments)
        self.fake.add_container(self.empty, "Common Exam 1 - Partial Credit", "AB", 2)  # made there by hand: both versions, the same number
        there = set(self.fake.assignments)
        self.certify_after()
        self.work()
        [run] = self.runs()
        self.assertNotIn("creation_id", run["outcome"]["create"])
        self.assertTrue(any("“Common Exam 1 - Partial Credit” in" in note and "may be this assignment" in note for note in run["outcome"]["create"]["notes"]), run["outcome"])
        self.assertEqual(set(self.fake.assignments), there)  # nothing was made beside it
        self.assertEqual(run["outcome"]["written"]["notes"], ["Section 501: no Gradescope assignment is linked for it. Send it once from the Gradescope panel."])
        self.assertEqual(self.publications(), [])
        self.assertTrue(made < there)

    def test_an_assignment_of_that_name_faculty_did_not_make_is_never_linked_by_itself(self):
        self.fake.add_assignment(self.empty, f"{self.title} Version A", 2)  # someone made it there by hand
        self.certify_after()
        self.work()
        [run] = self.runs()
        self.assertNotIn("creation_id", run["outcome"]["create"])
        self.assertTrue(any("already has an assignment named" in note for note in run["outcome"]["create"]["notes"]), run["outcome"])
        self.assertEqual(self.publications(), [])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_targets").fetchone()[0], 0)


class AfterLateTests(late_fixture.LateTests, AfterCase):
    """A late paper Faculty certified and sent by itself: published where its set already is."""

    def late_sent(self, publish):
        self.add_carol()
        self.carol_on_gradescope()
        self.send()
        self.sender.drain()
        self.certify()
        for assignment in publish:
            self.fake.assignments[assignment]["published"] = True
        self.carol_paper()
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["gradescope"]), ("done", 1), done)
        return done

    def test_a_late_paper_s_version_is_published_where_its_set_already_is(self):
        self.late_sent([self.container, self.versions["B"], self.versions["C"]])  # Version A was added later: not published
        [run] = self.runs()
        self.assertEqual((run["kind"], run["step"], run["actor"], run["asked"]), ("late", "publish", "automatic", {"rows": [self.row3]}))
        self.assertEqual(self.service.view(self.aid)["gradescope"].get("unpublished_versions"), None)  # Faculty is on it: no "Publish grades…" notice
        self.work()
        [run] = self.runs()
        self.assertEqual(run["status"], "done", run)
        [publication] = self.publications()
        self.assertEqual((publication["actor"], publication["status"], publication["plan"]["automatic"]["after"]), ("automatic", "complete", "late"))
        self.assertTrue(all(self.fake.assignments[a]["published"] for a in [self.container, *self.versions.values()]))
        view = self.service.view(self.aid)
        self.assertNotIn("unpublished_versions", view["gradescope"])
        self.assertEqual((view["gradescope"]["after"][0]["kind"], view["gradescope"]["after"][0]["published"]), ("late", ["501"]))
        # The send's own card no longer says its version is not published.
        sent = next(x for x in self.sender.status(self.aid)["sends"] if (x["plan"].get("automatic") or {}).get("row") == self.row3)
        self.assertNotIn("unpublished_version", sent["plan"]["automatic"])
        self.assertEqual(self.automation()[1][0].split(" · ")[0], "Published a late paper’s grade on Gradescope (section 501)")
        self.assertGraded("q20", "A", self.carol_user, [1], score=10.0)
        self.assertEqual(self.fake.forbidden_hits, [])

    def test_a_set_gradescope_shows_unpublished_only_because_of_the_new_version_is_published(self):
        """The real site's shape (Quiz 4, section 515, 2026-10-12): the set's own row reads unpublished while a version is."""
        self.late_sent([self.versions["B"], self.versions["C"]])  # the instructor published; Version A was added afterwards
        self.assertFalse(self.fake.assignments[self.container]["published"])
        self.work()
        [run] = self.runs()
        self.assertEqual(run["status"], "done", run)
        [publication] = self.publications()
        self.assertEqual((publication["actor"], publication["status"]), ("automatic", "complete"))
        self.assertTrue(all(self.fake.assignments[a]["published"] for a in [self.container, *self.versions.values()]))

    def test_a_set_the_instructor_has_not_published_is_left_unpublished(self):
        self.late_sent([])
        self.work()
        [run] = self.runs()
        self.assertEqual((run["status"], run["outcome"]["publish"]), ("done", {}))
        self.assertEqual(self.publications(), [])
        self.assertFalse(any(self.fake.assignments[a]["published"] for a in [self.container, *self.versions.values()]))

    def test_a_set_published_whole_has_nothing_to_publish(self):
        self.late_sent([self.container, *self.versions.values()])
        self.work()
        [run] = self.runs()
        self.assertEqual((run["status"], run["outcome"]["publish"]), ("done", {}))
        self.assertEqual(self.publications(), [])

    def test_a_late_paper_on_a_version_someone_left_ungraded_is_held(self):
        self.late_sent([self.container, self.versions["B"], self.versions["C"]])
        self.fake.assignments[self.container]["graded"] = 80.0
        self.work()
        [run] = self.runs()
        self.assertIn("80% graded", run["outcome"]["publish"]["held"][0]["reason"])
        self.assertFalse(self.fake.assignments[self.versions["A"]]["published"])
        view = self.service.view(self.aid)["gradescope"]
        self.assertNotIn("unpublished_versions", view)  # the run's own line says why; the notice is not repeated
        self.assertEqual(view["after"][0]["held"], run["outcome"]["publish"]["held"])

    def test_a_late_paper_never_publishes_another_paper_s_grade_that_is_not_there_as_faculty_has_it(self):
        """Publish All Grades shows EVERY version not yet published: Alice's paper is on Version A too, and her grade changed."""
        self.add_carol()
        self.carol_on_gradescope()
        self.send()
        self.sender.drain()
        self.certify()
        for assignment in (self.container, self.versions["B"], self.versions["C"]):
            self.fake.assignments[assignment]["published"] = True
        self.change_alice()  # after the certification, not sent: Gradescope has her earlier grade
        self.carol_paper()
        done = self.late().run(self.aid)
        self.assertEqual((done["state"], done["gradescope"]), ("done", 1), done)
        self.work()
        run = self.runs()[-1]
        self.assertEqual(run["outcome"]["publish"]["held"][0]["reason"], "1 final grade of papers on Gradescope is not there as Faculty has it.")
        self.assertEqual(self.publications(), [])
        self.assertFalse(self.fake.assignments[self.versions["A"]]["published"])

    def test_a_late_paper_on_a_version_that_is_published_publishes_nothing_else(self):
        self.late_sent([self.container, self.versions["A"], self.versions["C"]])  # Carol's Version A is published; B is not
        self.work()
        [run] = self.runs()
        self.assertEqual((run["status"], run["outcome"]["publish"]), ("done", {}))
        self.assertEqual(self.publications(), [])
        self.assertFalse(self.fake.assignments[self.versions["B"]]["published"])

    def test_the_switch_off_leaves_publishing_to_the_instructor(self):
        settings = self.frq.settings()
        self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "auto_gradescope": False}})
        self.late_sent([self.container, self.versions["B"], self.versions["C"]])
        self.assertEqual(self.runs(), [])
        self.sender.drain()
        self.assertEqual(self.service.view(self.aid)["gradescope"]["unpublished_versions"], ["A"])
        self.assertEqual(self.publications(), [])


class SheetRuns:
    """The bubble-sheet fixtures have no certifiable plan: a certification of the bubble part is written as a row, and its run queued
    as Certify queues it."""

    def queue(self, asked=None):
        from course_storage import timestamp
        now, cid = timestamp(), str(uuid.uuid4())
        with connect(self.app.config["DATABASE"]) as db:
            latest = db.execute("SELECT id FROM frq_certifications WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (self.aid,)).fetchone()
            db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
                participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,supersedes_id,actor,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'{}','[]','{}','{}','{}',?,'workspace',?)""",
                       (cid, self.aid, str(uuid.uuid4()), "a" * 64, "b" * 64, "test", '["bubble"]', "c" * 64, "d" * 64, "e" * 64, "f" * 64, latest[0] if latest else None, now))
            gradescope_after.queue_run(db, cid, self.aid, "certified", asked or {}, "workspace", now)

    def run_row(self):
        with connect(self.app.config["DATABASE"]) as db:
            row = dict(db.execute("SELECT * FROM frq_gradescope_automatic_runs ORDER BY rowid DESC LIMIT 1").fetchone())
        return {**row, "outcome": json.loads(row["outcome_json"])}

    def work(self, turns=20):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        for _ in range(turns):
            self.sender.drain()
            self.ready_now()
            with connect(self.app.config["DATABASE"]) as db:
                for table in ("frq_gradescope_automatic_runs", "frq_gradescope_publication_units", "frq_gradescope_mc_checks", "frq_gradescope_mc_corrections"):
                    db.execute(f"UPDATE {table} SET not_before=? WHERE not_before IS NOT NULL AND status IN ('pending','uncertain')", (past,))
                left = db.execute("""SELECT (SELECT COUNT(*) FROM frq_gradescope_automatic_runs WHERE status!='done')
                    + (SELECT COUNT(*) FROM frq_gradescope_publications WHERE status IN ('pending','running'))""").fetchone()[0]
            if not left:
                return
        self.fail(f"Faculty's Gradescope work did not finish: {self.run_row()}")

    def sheet_publications(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_publications ORDER BY rowid")]


class AfterSheetsTests(SheetRuns, mc_fixture.Check):
    """The three bubble sheets are on Gradescope, which scored Bob's Question 3 differently from the instructor's key."""

    def test_a_sheet_gradescope_scored_differently_holds_its_section_after_one_read_of_its_scores(self):
        self.queue()
        self.work()
        run = self.run_row()
        self.assertEqual((run["status"], run["outcome"]["sheets"], run["outcome"]["mc_asked"]), ("done", {}, 1))  # nothing to send; one MC check asked
        [read] = self.reads()
        self.assertEqual((read["status"], read["actor"]), ("read", "automatic"))
        [held] = run["outcome"]["publish"]["held"]
        self.assertEqual((str(held["section"]), held["part"], held["reason"]), ("501", "sheets", "Gradescope scored 1 bubble sheet differently from your key."))
        self.assertEqual(self.sheet_publications(), [])
        self.assertFalse(self.fake.assignments[self.exam]["published"])
        self.assertEqual(self.fake.publish_hits, [])
        self.assertFalse(any(uin in run["outcome_json"] for uin in mc_fixture.UINS))

    def test_sheets_gradescope_scored_as_the_key_does_are_published(self):
        self.fake.score_sheet(self.versions[self.on_gradescope(mc_fixture.BOB[0])[0]], self.users[mc_fixture.BOB[0]], {n: 4.0 for n in mc_fixture.KEY})
        self.queue()
        self.work()
        run = self.run_row()
        self.assertEqual(run["status"], "done", run)
        self.assertNotIn("held", run["outcome"]["publish"])
        [publication] = self.sheet_publications()
        self.assertEqual((publication["actor"], publication["status"]), ("automatic", "complete"))
        self.assertTrue(self.fake.assignments[self.exam]["published"])
        self.assertEqual(self.fake.forbidden_hits, [])

    def test_a_comparison_as_new_as_the_sheets_is_not_asked_for_again(self):
        self.sender.mc_check_start(self.aid)
        self.sender.drain()  # the instructor's Check MC, after the sheets went up
        self.queue()
        self.work()
        run = self.run_row()
        self.assertNotIn("mc_asked", run["outcome"])
        self.assertEqual(len(self.reads()), 1)
        self.assertIn("scored 1 bubble sheet differently", run["outcome"]["publish"]["held"][0]["reason"])

    def test_scores_that_could_not_be_read_hold_the_section(self):
        self.queue()
        with patch.object(self.sender, "_mc_unit", side_effect=lambda unit: self.sender._mc_set(unit["id"], "failed", error="Gradescope's scores could not be downloaded.")):
            self.work()
        run = self.run_row()
        self.assertIn("could not be compared with your key", run["outcome"]["publish"]["held"][0]["reason"])
        self.assertEqual(self.sheet_publications(), [])


class AfterSheetsCorrectedTests(SheetRuns, corrections_fixture.CorrectionCase):
    """MC CORRECTIONS: the run's MC check finds Bob's Question 3 scored differently, Faculty corrects it on Gradescope, reads the
    scores again, and the section is then clean and published."""

    def test_a_question_faculty_corrected_on_gradescope_no_longer_holds_its_section(self):
        self.queue()
        self.work()
        run = self.run_row()
        self.assertEqual(run["status"], "done", run)
        self.assertNotIn("held", run["outcome"]["publish"])
        self.assertEqual([(f["status"], f["question_number"]) for f in self.corrections()], [("accepted", 3)])
        self.assertEqual([r["actor"] for r in self.reads()], ["automatic", "automatic"])  # the run's read, then the read after the correction
        [publication] = self.sheet_publications()
        self.assertEqual((publication["actor"], publication["status"]), ("automatic", "complete"))
        self.assertTrue(self.fake.assignments[self.exam]["published"])


class AfterSheetsSendTests(SheetRuns, sheets_fixture.SheetCase):
    def setUp(self):
        super().setUp()
        key = {"defaults": {"points": "4", "scoring": "exact"},
               "versions": [{"letter": letter, "questions": [{"number": n, "answer": answer} for n, answer in mc_fixture.KEY.items()]} for letter in "AB"]}
        self.frq.save_bubble_key(self.aid, {"request_id": "key-1", "base_revision": None, **key})

    def linked_and_not_sent(self):
        sent = self.send()
        self.sender.sheet_cancel(self.aid, sent["send_id"])  # the section's bubble-sheet exam is chosen; no sheet went
        self.assertEqual(self.sender.drain(), 0)

    def score_all(self):
        for uin in (sheets_fixture.ALICE[0], sheets_fixture.BOB[0], sheets_fixture.CAROL[0]):
            self.fake.score_sheet(self.versions[self.on_gradescope(uin)[0]], self.users[uin], {n: 4.0 for n in mc_fixture.KEY})

    def test_sheets_that_are_not_there_go_up_first_and_are_published_once_gradescope_has_scored_them(self):
        self.linked_and_not_sent()
        real, reads = self.sender._mc_unit, []

        def unit(found):
            reads.append(1)
            if len(reads) == 2:  # Gradescope has scored the sheets by the second read, a minute after the first
                self.score_all()
            return real(found)
        self.queue()
        with patch.object(self.sender, "_mc_unit", side_effect=unit):
            self.work()
        run = self.run_row()
        self.assertEqual((run["status"], run["outcome"]["sheets"]["sheets"]), ("done", 3), run)
        with connect(self.app.config["DATABASE"]) as db:
            send = dict(db.execute("SELECT * FROM frq_gradescope_sheet_sends WHERE id=?", (run["outcome"]["sheets"]["send_id"],)).fetchone())
        self.assertEqual((send["actor"], send["status"], json.loads(send["plan_json"])["automatic"]["after"]), ("automatic", "complete", "certified"))
        self.assertEqual(sorted(u["status"] for u in self.units() if u["send_id"] == send["id"]), ["accepted"] * 3)
        self.assertEqual((run["outcome"]["mc_asked"], len(reads)), (2, 2))  # read once the sheets were there, and again while one was unscored
        self.assertNotIn("held", run["outcome"]["publish"])
        self.assertTrue(self.fake.assignments[self.exam]["published"])
        self.assertEqual(self.fake.forbidden_hits, [])

    def test_sheets_gradescope_has_not_scored_are_never_published_as_if_they_agreed(self):
        self.linked_and_not_sent()
        self.queue()
        self.work(turns=40)
        run = self.run_row()
        self.assertEqual((run["status"], run["outcome"]["mc_asked"]), ("done", gradescope_after.MC_TRIES), run)
        self.assertEqual(run["outcome"]["publish"]["held"][0]["reason"],
                         "Gradescope has not scored 3 bubble sheets yet. Use Check MC in the Gradescope panel once it has.")
        self.assertEqual(self.sheet_publications(), [])
        self.assertFalse(self.fake.assignments[self.exam]["published"])

    def test_a_section_with_no_bubble_sheet_exam_chosen_gets_nothing_and_a_declined_part_is_left_alone(self):
        self.queue()
        self.work()
        run = self.run_row()
        self.assertEqual(run["outcome"]["sheets"]["notes"], ["Section 501: no Gradescope bubble-sheet exam is linked for it yet. Send its sheets once from the Gradescope panel."])
        self.assertEqual(self.units(), [])
        self.queue({"sheets": {"decision": "do_not_send"}})
        self.work()
        self.assertEqual(self.run_row()["outcome"]["sheets"], {"declined": True})
        self.assertEqual([r for r in self.fake.requests if r["method"] != "GET" and r["path"] != "/login"], [])


# The fixtures' own tests run in their own modules: only the tests written here run here.
for _case in (AfterCase, AfterCertifyingTests, AfterCreateTests, AfterLateTests, AfterSheetsTests, AfterSheetsSendTests):
    for _name in {n for base in _case.__mro__[1:] for n in vars(base) if n.startswith("test_")} - set(vars(_case)):
        setattr(_case, _name, None)
del CreateCase, _case, _name


if __name__ == "__main__":
    unittest.main()
