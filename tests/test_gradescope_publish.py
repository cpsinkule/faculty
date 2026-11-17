"""Publishing grades on Gradescope from Certification and Export (AGENTS.md "Sending grades to Gradescope", PUBLISHING GRADES;
requirement, 2026-10-02), offline against FakeGradescope.

The fixture is the sender's (test_gradescope_send): section 501 with two students on Versions A and B and their final grades. Each
test first sends them, which links the section to the Gradescope exam "Common Exam 1 - Partial Credit" (a version set A, B, C);
publishing then publishes that set with Gradescope's own Review Grades form, only on the instructor's approved request, and reads
it back.
"""
import json
import sqlite3
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from assistant import policy
from grading import gradescope_publish as gp
from grading import gradescope_web as gw
from rosters import connect
from test_gradescope_send import HEADERS, GradescopeCase


class PublishCase(GradescopeCase):
    def setUp(self):
        super().setUp()
        self.send()
        self.sender.drain()
        self.key = f"written:{self.section['id']}"

    def publish_view(self, **extra):
        return self.sender.publish_preview(self.aid, extra)

    def publish(self, view=None, **extra):
        view = view or self.publish_view(**({"leave_out": extra["leave_out"]} if "leave_out" in extra else {}))
        return self.sender.publish(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "publish", "fingerprint": view["fingerprint"], **extra})

    def publication_units(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_publication_units ORDER BY created_at,rowid")]

    def ready_now(self):
        super().ready_now()
        with connect(self.path) as db:
            db.execute("UPDATE frq_gradescope_publication_units SET not_before=? WHERE not_before IS NOT NULL",
                       ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))

    def published_on_gradescope(self):
        return [self.fake.assignments[self.container]["published"]] + [self.fake.assignments[v]["published"] for v in self.versions.values()]

    def writes_since(self, before):
        return [r for r in self.fake.requests[before:] if r["method"] != "GET" and r["path"] != "/login"]


class PublishTests(PublishCase):
    def test_creation_blocks_publication_even_when_it_started_after_the_preview(self):
        """GRADESCOPE: recheck creation while recording the approved publication."""
        planned = self.sender._publish_plan(self.aid, {})
        with connect(self.path) as db:
            db.execute("INSERT INTO frq_gradescope_creations VALUES(?,?,?,?,?,?,?,'pending',?,?,?)",
                       (str(uuid.uuid4()), self.aid, str(uuid.uuid4()), "request", "fingerprint", "title", "{}", "workspace", "now", "now"))
        with self.assertRaisesRegex(Conflict, "still making"):
            self.sender._record_publication(self.aid, planned, str(uuid.uuid4()), "request", planned["view"]["fingerprint"], [])
        self.assertEqual(self.publication_units(), [])
        self.assertEqual(self.fake.publish_hits, [])

    def test_the_preview_lists_the_linked_assignment_warns_and_writes_nothing(self):
        before = len(self.fake.requests)
        view = self.publish_view()
        self.assertTrue(view["ready"], view)
        self.assertEqual(view["publish"], 1)
        [row] = view["rows"]
        self.assertEqual((row["key"], row["part"], row["course_id"], row["target_id"], row["kind"], row["title"], row["submissions"], row["published"], row["problem"]),
                         (self.key, "written", str(self.course), str(self.container), "assignment_container", "Common Exam 1 - Partial Credit", 2, False, ""))
        self.assertEqual([v["letter"] for v in row["versions"]], ["A", "B", "C"])
        # Not certified: a warning, never a refusal (the instructor decides). Everything final is on Gradescope, so nothing else.
        self.assertEqual(view["certification"], "none")
        self.assertEqual(view["warnings"], ["These grades are not certified yet."])
        self.assertEqual(self.writes_since(before), [])
        self.assertEqual(self.fake.publish_hits, [])
        self.assertNoUin(view)

    def test_publishing_uses_gradescopes_own_form_for_the_version_set_and_reads_it_back(self):
        receipt = self.publish()
        self.assertEqual(receipt["status"], "pending")
        self.assertEqual(self.published_on_gradescope(), [False] * 4)  # nothing leaves before the sender's turn
        self.assertEqual(self.sender.drain(), 1)
        [unit] = self.publication_units()
        self.assertEqual((unit["status"], unit["part"], unit["target_type"], unit["target_id"], json.loads(unit["sections_json"])),
                         ("accepted", "written", "assignment_container", str(self.container), ["501"]))
        self.assertTrue(unit["requested_at"])
        self.assertTrue(json.loads(unit["receipt_json"])["published"])
        self.assertEqual(self.published_on_gradescope(), [True] * 4)  # "Publish All Grades": the set and every version
        [hit] = self.fake.published()
        self.assertEqual((hit["path"], hit["form"]["_method"], hit["form"]["assignment_container[published]"]),
                         (f"/courses/{self.course}/assignment_containers/{self.container}", "patch", "true"))
        self.assertTrue(hit["form"]["authenticity_token"])
        self.assertNotIn("setting_changed", hit)
        status = self.sender.publish_status(self.aid)
        self.assertEqual(status["publications"][0]["status"], "complete")
        self.assertEqual([(u["status"], u["sections"], u["title"]) for u in status["publications"][0]["units"]],
                         [("accepted", ["501"], "Common Exam 1 - Partial Credit")])
        with connect(self.path) as db:
            events = [r[0] for r in db.execute("SELECT kind FROM frq_events WHERE assignment_id=? AND kind LIKE 'gradescope_publish%' ORDER BY rowid", (self.aid,))]
        self.assertEqual(events, ["gradescope_publish", "gradescope_publish_complete"])
        # Published is published: the next preview has nothing to do, and an approval of it is refused.
        again = self.publish_view()
        self.assertEqual((again["rows"][0]["published"], again["publish"], again["ready"]), (True, 0, False))
        with self.assertRaisesRegex(Conflict, "nothing Faculty can publish"):
            self.publish(again)
        self.assertEqual(self.fake.forbidden_hits, [])
        self.assertFalse(any(r["path"].endswith("/notify_students") for r in self.fake.requests))  # nobody is emailed

    def test_the_modal_form_is_used_when_review_grades_asks_first_and_its_setting_is_never_sent(self):
        self.fake.publish_modal = True
        self.publish()
        self.sender.drain()
        [hit] = self.fake.published()
        self.assertEqual({k: v for k, v in hit["form"].items() if k != "authenticity_token"},
                         {"utf8": "✓", "_method": "put", "assignment[published]": "true", "assignment_container[published]": "true"})
        self.assertEqual(self.published_on_gradescope(), [True] * 4)

    def test_a_plain_assignment_is_published_with_its_own_form(self):
        homework = next(a for a in self.fake.assignments.values() if a["title"] == "Homework 1")
        self.fake.add_submission(homework["id"], [self.alice])
        session = gw.Session(self.settings)
        try:
            session.publish(self.course, "assignment", homework["id"])
        finally:
            session.close()
        [hit] = self.fake.published()
        self.assertEqual((hit["path"], hit["form"]["_method"], hit["form"]["assignment[published]"]), (f"/courses/{self.course}/assignments/{homework['id']}", "put", "true"))
        self.assertTrue(homework["published"])

    def test_nothing_is_published_without_the_confirmation_and_a_current_preview(self):
        view = self.publish_view()
        base = {"request_id": str(uuid.uuid4()), "fingerprint": view["fingerprint"]}
        with self.assertRaisesRegex(ValueError, "Confirm publishing"):
            self.sender.publish(self.aid, base)
        with self.assertRaisesRegex(ValueError, "request ID"):
            self.sender.publish(self.aid, {**base, "request_id": "nope", "confirmation": "publish"})
        # Someone published one version on Gradescope after the preview: the preview is stale.
        self.fake.assignments[self.versions["A"]]["published"] = True
        with self.assertRaisesRegex(Conflict, "changed since the preview"):
            self.sender.publish(self.aid, {**base, "confirmation": "publish"})
        fresh = self.publish_view()
        self.assertEqual((fresh["rows"][0]["partly"], fresh["ready"]), (True, True))  # partly published: the set is published whole
        request = {"request_id": str(uuid.uuid4()), "confirmation": "publish", "fingerprint": fresh["fingerprint"]}
        first = self.sender.publish(self.aid, request)
        self.assertEqual(self.sender.publish(self.aid, request)["publication_id"], first["publication_id"])  # the same request, once
        with self.assertRaisesRegex(Conflict, "already used"):
            self.sender.publish(self.aid, {**request, "leave_out": [self.key]})
        self.assertEqual(len(self.publication_units()), 1)
        self.assertEqual(self.fake.publish_hits, [])

    def test_gradescopes_graded_share_is_shown_but_does_not_make_the_preview_stale(self):
        self.fake.assignments[self.container]["graded"] = 62.5
        view = self.publish_view()
        self.assertIn("62.5% graded", view["rows"][0]["warning"])
        self.fake.assignments[self.container]["graded"] = 75.0  # a TA grades there meanwhile
        self.publish(view)
        self.sender.drain()
        self.assertEqual(self.published_on_gradescope(), [True] * 4)

    def test_the_graded_share_is_said_to_one_decimal_and_never_as_100_or_0(self):
        # Exam 2's MC of 140-515, 2026-10-28: two of 1,377 questions ungraded showed as "99.8547567175018%" (instructor: round it).
        self.fake.assignments[self.container]["graded"] = 99.8547567175018
        self.assertIn("shows it 99.9% graded", self.publish_view()["rows"][0]["warning"])
        self.assertEqual([gp.percent(s) for s in (99.96, 62.5, 33.333, 0.01)], ["99.9", "62.5", "33.3", "0.1"])
        # The page prints the same rule (static/grading-gradescope-publish.js `percent`, tests/test_grading_gradescope_publish.mjs).
        self.assertEqual([gp.percent(s) for s in (6.25, 6.75, 1.25, 33.25, 0.25, 0.15, 0.95, 62.55)], ["6.2", "6.8", "1.2", "33.2", "0.2", "0.1", "0.9", "62.5"])

    def test_an_assignment_published_meanwhile_is_left_alone(self):
        self.publish()
        for target in [self.container, *self.versions.values()]:
            self.fake.assignments[target]["published"] = True
        self.sender.drain()
        [unit] = self.publication_units()
        self.assertEqual(unit["status"], "present")
        self.assertIsNone(unit["requested_at"])
        self.assertEqual(self.fake.publish_hits, [])

    def test_a_section_left_out_is_not_read_and_nothing_is_published(self):
        before = len(self.fake.requests)
        view = self.publish_view(leave_out=[self.key])
        self.assertEqual((view["rows"][0]["left_out"], view["rows"][0]["submissions"], view["publish"], view["ready"]), (True, 0, 0, False))
        self.assertFalse(any(r["path"].startswith(f"/courses/{self.course}/") for r in self.fake.requests[before:]))
        with self.assertRaisesRegex(ValueError, "keys"):
            self.publish_view(leave_out="all")
        with self.assertRaisesRegex(Conflict, "nothing Faculty can publish"):
            self.publish(view, leave_out=[self.key])

    def test_a_running_send_and_a_running_publication_wait_for_each_other(self):
        self.decide("q20", "A", ["R3_1"])  # a changed grade, so there is something to send again
        self.send()
        view = self.publish_view()
        self.assertFalse(view["ready"])
        self.assertIn("still running", view["problems"][0])
        with self.assertRaisesRegex(Conflict, "nothing Faculty can publish"):
            self.publish(view)
        self.sender.drain()
        self.publish()
        self.decide("q20", "A", ["R3_2"])
        with self.assertRaisesRegex(Conflict, "publishing this assignment"):
            self.send()
        self.sender.drain()
        self.assertEqual(self.publication_units()[0]["status"], "accepted")

    def test_retry_cannot_restart_a_failed_send_under_a_publication(self):
        """GRADESCOPE: explicit Retry observes the same publication boundary as Send."""
        self.decide("q20", "A", ["R3_1"])
        send_id = self.send()["send_id"]
        self.fake.fail("POST", r"/save_grade$", "drop_before")
        self.sender.drain()
        failed = [unit for unit in self.units("grades") if unit["send_id"] == send_id]
        self.assertEqual([unit["status"] for unit in failed], ["failed"])
        self.publish()
        with self.assertRaisesRegex(Conflict, "publishing"):
            self.sender.retry(self.aid, send_id)
        self.assertEqual([unit["status"] for unit in self.units("grades") if unit["send_id"] == send_id], ["failed"])

    def test_a_lost_answer_is_settled_by_reading_and_never_published_twice(self):
        self.fake.fail("POST", rf"/assignment_containers/{self.container}$", "drop_after")
        self.publish()
        self.sender.drain()
        [unit] = self.publication_units()
        self.assertEqual(unit["status"], "accepted")  # read back: it was done
        self.assertEqual(len(self.fake.published()), 1)
        # Its read failing too leaves it uncertain; only a read settles it, and nothing is sent again.
        for target in [self.container, *self.versions.values()]:
            self.fake.assignments[target]["published"] = False
        self.fake.fail("POST", rf"/assignment_containers/{self.container}$", "drop_after")
        self.fake.fail("GET", rf"/courses/{self.course}/assignments$", ("status", 500), when=lambda: len(self.fake.published()) == 2)
        self.publish()
        self.sender.drain(limit=1)
        unit = self.publication_units()[1]
        self.assertEqual(unit["status"], "uncertain")
        posts = len([r for r in self.fake.requests if r["method"] == "POST" and r["path"].endswith(f"/assignment_containers/{self.container}")])
        self.sender.drain()  # its next turn only reads
        self.assertEqual(self.publication_units()[1]["status"], "accepted")
        self.assertEqual(len([r for r in self.fake.requests if r["method"] == "POST" and r["path"].endswith(f"/assignment_containers/{self.container}")]), posts)
        self.assertEqual(len(self.fake.published()), 2)

    def test_a_busy_gradescope_is_waited_for(self):
        self.fake.fail("POST", rf"/assignment_containers/{self.container}$", ("status", 503))  # not done: it goes again after the wait
        self.publish()
        self.sender.drain()
        self.assertEqual(self.publication_units()[0]["status"], "pending")
        self.ready_now()
        self.sender.drain()
        self.assertEqual(self.publication_units()[0]["status"], "accepted")
        self.fake.fail("POST", rf"/assignment_containers/{self.container}$", ("status_after", 502))  # done, then answered 502
        for target in [self.container, *self.versions.values()]:
            self.fake.assignments[target]["published"] = False
        self.publish()
        self.sender.drain()
        self.assertEqual(self.publication_units()[1]["status"], "accepted")  # read back before anything else
        self.assertEqual(len(self.fake.published()), 2)

    def test_a_page_without_the_form_stops_for_the_instructor_and_retry_publishes(self):
        self.publish()
        with patch.object(gw, "publish_form", return_value=None):
            self.sender.drain()
        [unit] = self.publication_units()
        self.assertEqual(unit["status"], "failed")
        self.assertIn("no Publish Grades form", unit["error"])
        publication = self.sender.publish_status(self.aid)["publications"][0]
        self.assertEqual(publication["status"], "complete")
        self.sender.publish_retry(self.aid, publication["id"])
        self.sender.drain()
        self.assertEqual(self.publication_units()[0]["status"], "accepted")
        with self.assertRaisesRegex(Conflict, "Nothing in this publication failed"):
            self.sender.publish_retry(self.aid, publication["id"])

    def test_cancel_stops_what_waits(self):
        receipt = self.publish()
        self.assertEqual(self.sender.publish_cancel(self.aid, receipt["publication_id"])["status"], "cancelled")
        self.sender.drain()
        self.assertEqual(self.publication_units()[0]["status"], "cancelled")
        self.assertEqual(self.fake.publish_hits, [])
        with self.assertRaisesRegex(Conflict, "cancelled publication"):
            self.sender.publish_retry(self.aid, receipt["publication_id"])

    def test_records_never_change_and_the_coordinators_course_is_refused(self):
        receipt = self.publish()
        self.sender.drain()
        with connect(self.path) as db:
            with self.assertRaisesRegex(sqlite3.DatabaseError, "settled"):
                db.execute("UPDATE frq_gradescope_publication_units SET status='pending'")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "never changes"):
                db.execute("UPDATE frq_gradescope_publications SET fingerprint='x'")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "retained"):
                db.execute("DELETE FROM frq_gradescope_publications")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "Math 140 for Instructors"):
                db.execute("""INSERT INTO frq_gradescope_publication_units(id,send_id,assignment_id,part,course_id,course_title,target_type,target_id,target_title,
                    sections_json,status,created_at,updated_at) VALUES('u',?,?,'written','1','Math 140 for Instructors','assignment','2','x','[]','pending','t','t')""",
                           (receipt["publication_id"], self.aid))

    def test_the_routes_and_the_assistant_tiers(self):
        client = self.client
        status = client.get(f"/api/grading/gradescope/assignments/{self.aid}/publications")
        self.assertEqual((status.status_code, status.json["publications"]), (200, []))
        preview = client.post(f"/api/grading/gradescope/assignments/{self.aid}/publish/preview", json={}, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        published = client.post(f"/api/grading/gradescope/assignments/{self.aid}/publish", headers=HEADERS,
                                json={"request_id": str(uuid.uuid4()), "confirmation": "publish", "fingerprint": preview.json["fingerprint"]})
        self.assertEqual(published.status_code, 201, published.json)
        retry = client.post(f"/api/grading/gradescope/assignments/{self.aid}/publications/{published.json['publication_id']}/retry", json={}, headers=HEADERS)
        self.assertEqual(retry.status_code, 409)
        cancel = client.post(f"/api/grading/gradescope/assignments/{self.aid}/publications/{published.json['publication_id']}/cancel", json={}, headers=HEADERS)
        self.assertEqual((cancel.status_code, cancel.json["status"]), (200, "cancelled"))
        # The assistant may look at a preview, never publish: the page asks the instructor.
        self.assertEqual(policy.base_rule("frq_gradescope.publish_preview").tier, "read")
        self.assertEqual(policy.base_rule("frq_gradescope.publish").tier, "page")
        self.assertEqual(policy.base_rule("frq_gradescope.publish_retry").tier, "approve")


class PublishFormTests(unittest.TestCase):
    """The Review Grades form as read on the real site, 2026-10-02 (tokens replaced)."""

    BAR_SET = ('<form class="review-grades-next-button" method="post" action="/courses/7/assignment_containers/9"><input type="hidden" name="_method" value="patch" autocomplete="off" />'
               '<button class="tiiBtn" type="submit">Publish All Grades</button><input type="hidden" name="authenticity_token" value="T" autocomplete="off" />'
               '<input type="hidden" name="assignment_container[published]" value="true" autocomplete="off" /></form>')
    BAR_ONE = BAR_SET.replace("assignment_containers/9", "assignments/9").replace("patch", "put").replace("assignment_container[published]", "assignment[published]")
    UNPUBLISH = BAR_SET.replace("review-grades-next-button", "button_to").replace('value="true"', 'value="false"')
    MODAL = ('<form class="js-publishGradesForm" action="/courses/7/assignment_containers/9" accept-charset="UTF-8" method="post"><input name="utf8" type="hidden" value="&#x2713;" autocomplete="off" />'
             '<input type="hidden" name="_method" value="put" autocomplete="off" /><input type="hidden" name="authenticity_token" value="M" autocomplete="off" />'
             '<input type="hidden" name="assignment[published]" id="assignment_published" value="true" autocomplete="off" class=" form--textInput" />'
             '<input type="hidden" name="assignment_container[published]" id="assignment_container_published" value="true" autocomplete="off" class=" form--textInput" />'
             '<input type="checkbox" name="user[settings_ignore_publish_grades_modal]" id="user_settings_ignore_publish_grades_modal" value="1" /></form>')

    def test_the_bar_form_comes_first_the_modal_second_and_unpublish_never(self):
        self.assertEqual(gw.publish_form(self.MODAL + self.BAR_SET, 7, "assignment_container", 9),
                         ("/courses/7/assignment_containers/9", [("_method", "patch"), ("authenticity_token", "T"), ("assignment_container[published]", "true")]))
        self.assertEqual(gw.publish_form(self.MODAL, 7, "assignment_container", 9)[1],
                         [("utf8", "✓"), ("_method", "put"), ("authenticity_token", "M"), ("assignment[published]", "true"), ("assignment_container[published]", "true")])
        self.assertEqual(gw.publish_form(self.BAR_ONE, 7, "assignment", 9)[0], "/courses/7/assignments/9")
        self.assertIsNone(gw.publish_form(self.UNPUBLISH, 7, "assignment_container", 9))
        self.assertIsNone(gw.publish_form(self.BAR_SET, 7, "assignment_container", 8))  # another set's form
        self.assertIsNone(gw.publish_form(self.BAR_SET, 7, "assignment", 9))  # a set is not an assignment
        self.assertIsNone(gw.publish_form(self.BAR_SET.replace('value="T"', 'value=""'), 7, "assignment_container", 9))

    def test_warnings_say_what_students_would_see_unfinished(self):
        self.assertEqual(gp.warnings_for({"state": "current"}, {"papers": {"total": 3, "sent": 3}, "grades": {"not_sent": 0, "changed": 0}}, None), [])
        found = gp.warnings_for({"state": "changed"}, {"papers": {"total": 3, "sent": 2}, "grades": {"not_sent": 2, "changed": 1}},
                                {"sheets": {"matched": 4, "sent": 3}})
        self.assertEqual(len(found), 4)
        self.assertIn("changed since they were certified", found[0])
        self.assertIn("1 paper is not uploaded", found[1])
        self.assertIn("3 final grades are not on Gradescope", found[2])
        self.assertIn("1 matched bubble sheet is not uploaded", found[3])
