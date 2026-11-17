"""Creating the Gradescope assignment from Faculty (AGENTS.md "Sending grades to Gradescope"), offline against FakeGradescope.

The fixture is the sender's (test_gradescope_send): section 501 with two students on Versions A and B, questions 20 and 21 per
version with final grades. Here the section's Gradescope course is 140-502, which has no assignment for it yet: Faculty makes one
(two versions grouped as one set, each with its blank, its questions, their boxes and rubrics), links the section to it, and the
ordinary send then uploads the papers and sends the grades to it, matched by what Faculty made.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from grading import gradescope_create as gc
from rosters import connect
from test_gradescope_send import HEADERS, Q20, Q21, GradescopeCase, pdf


class CreateCase(GradescopeCase):
    def setUp(self):
        super().setUp()
        fake = self.fake
        self.empty = next(c for c, v in fake.courses.items() if v["short"] == "140-502")
        self.alice2 = fake.add_member(self.empty, "Alice Example", self.uins[self.enrollment], "alice@example.invalid")
        self.bob2 = fake.add_member(self.empty, "Bob Example", self.uins[self.second], "bob@example.invalid")
        with connect(self.path) as db:
            self.pages = dict(db.execute("SELECT id,page_count FROM assignment_templates"))
        reading = self.frq.templates.read
        self.enterContext(patch.object(self.frq.templates, "read", side_effect=lambda identifier, kind, page=None:
                                       (pdf(self.pages[identifier]), "application/pdf") if kind == "pdf" else reading(identifier, kind, page)))
        located = self.frq.location_status
        # The fixture's questions have areas but no confirmation receipt; creating needs confirmed areas, which a test says.
        self.enterContext(patch.object(self.frq, "location_status", side_effect=lambda db, aid, question, **_: {**located(db, aid, question), "status": "confirmed"}))
        with connect(self.path) as db:
            self.title = self.frq.plan(db, self.aid)["title"]
        self.targets = {self.section["id"]: {"course_id": str(self.empty), "container_id": ""}}

    def create_view(self, **extra):
        return self.sender.create_preview(self.aid, {"targets": self.targets, **extra})

    def create(self, view=None, **extra):
        view = view or self.create_view()
        return self.sender.create(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "create", "fingerprint": view["fingerprint"],
                                             "targets": self.targets, **extra})

    def creation_units(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_creation_units ORDER BY created_at,rowid")]

    def soon(self):
        with connect(self.path) as db:
            db.execute("UPDATE frq_gradescope_creation_units SET not_before=? WHERE not_before IS NOT NULL",
                       ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        self.sender._busy_until = None

    def writes(self):
        return [r for r in self.fake.requests if r["method"] in ("POST", "PATCH", "DELETE") and r["path"] != "/login"]

    def made(self):
        """{letter: assignment} of the set Faculty made, and the set."""
        container = next(a for a in self.fake.assignments.values() if a["container"] and a["title"] == self.title and a["course"] == self.empty)
        return container, {a["version"]: a for a in self.fake.assignments.values() if a["parent"] == container["id"]}


class CreateTests(CreateCase):
    def test_publication_blocks_creation_even_when_it_started_after_the_preview(self):
        """GRADESCOPE: creation and publication cannot write the same plan concurrently."""
        planned = self.sender._creation_plan(self.aid, {"targets": self.targets})
        with connect(self.path) as db:
            db.execute("INSERT INTO frq_gradescope_publications VALUES(?,?,?,?,?,?,'pending',?,?,?)",
                       (str(uuid.uuid4()), self.aid, str(uuid.uuid4()), "request", "fingerprint", "{}", "workspace", "now", "now"))
        view = self.create_view()
        self.assertFalse(view["ready"])
        self.assertTrue(any("publishing" in problem for problem in view["problems"]))
        with self.assertRaisesRegex(Conflict, "publishing"):
            self.sender._record_creation(self.aid, planned, str(uuid.uuid4()), "request", planned["view"]["fingerprint"])
        self.assertEqual(self.creation_units(), [])
        self.assertEqual(self.writes(), [])

    def test_the_preview_says_what_would_be_made_and_writes_nothing(self):
        view = self.create_view()
        self.assertTrue(view["ready"], view)
        self.assertEqual(view["title"], self.title)
        self.assertEqual([(v["letter"], v["title"], v["pages"], v["questions"], v["lines"]) for v in view["versions"]],
                         [("A", f"{self.title} Version A", 2, 2, 11), ("B", f"{self.title} Version B", 2, 2, 11)])
        self.assertEqual([(c["course_id"], c["sections"], c["problem"]) for c in view["courses"]], [(str(self.empty), [self.section["number"]], "")])
        self.assertEqual(self.writes(), [])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_creations").fetchone()[0], 0)
        self.assertNothingForbidden()

    def test_everything_is_made_linked_and_the_send_then_goes_to_it(self):
        receipt = self.create()
        self.assertEqual(receipt["status"], "pending")
        self.sender.drain()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "complete", unit["error"])
        container, versions = self.made()
        self.assertEqual(sorted(versions), ["A", "B"])
        for letter, assignment in versions.items():
            self.assertEqual(assignment["title"], f"{self.title} Version {letter}")
            self.assertEqual(assignment["pages"], 2)
            questions = sorted((q for q in self.fake.questions.values() if q["assignment"] == assignment["id"]), key=lambda q: q["index"])
            self.assertEqual([(q["title"], q["weight"]) for q in questions], [("Question 20", 10.0), ("Question 21", 8.0)])
            self.assertEqual(questions[0]["crops"], [{"page_number": 1, "x1": 10.0, "x2": 90.0, "y1": 10.0, "y2": 50.0}])
            for question, entries in zip(questions, (Q20, Q21)):
                made = [(i["description"], i["weight"]) for i in self.fake.rubric_items(question["id"])]
                self.assertEqual(len(made), sum(len(e[1]) if isinstance(e[1], list) else 1 for e in entries))
                self.assertNotIn(("Correct", 0.0), made)  # Gradescope's default item is gone
                self.assertTrue(question["floor"] and question["ceiling"])
        # Only the default items of the new questions were ever deleted, and Faculty made nothing twice.
        self.assertTrue(self.fake.deleted and all(i["description"] == "Correct" and i["weight"] == 0 for i in self.fake.deleted))
        self.assertEqual(len([r for r in self.writes() if r["path"].endswith("/assignments")]), 2)
        with connect(self.path) as db:
            receipts = db.execute("SELECT COUNT(*) FROM frq_gradescope_created_questions").fetchone()[0]
            link = db.execute("SELECT * FROM frq_gradescope_targets WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (self.aid,)).fetchone()
        self.assertEqual(receipts, 4)
        self.assertEqual((link["container_id"], json.loads(link["versions_json"])), (str(container["id"]), {k: str(v["id"]) for k, v in versions.items()}))
        # The send preview now goes to the new assignment and matches every question by what Faculty made.
        view = self.view()
        [section] = view["sections"]
        self.assertEqual((section["course_id"], section["container_id"], section["problem"]), (str(self.empty), str(container["id"]), ""))
        self.assertEqual(view["summary"]["grades"]["refused"], 0, view["questions"])
        self.assertEqual(view["summary"]["grades"]["send"], 4)
        self.send(view)
        self.sender.drain()
        self.assertEqual([u["status"] for u in self.units("uploads")], ["accepted", "accepted"])
        self.assertEqual([g["status"] for g in self.units("grades")], ["accepted"] * 4)
        self.assertNothingForbidden()

    def test_a_question_faculty_created_counts_as_sendable_whatever_its_rubric_came_from(self):
        """2026-09-28: Quiz 2's rubrics were drafted in Faculty, then created on Gradescope and every grade sent there; the
        certification still called every question "can never be sent" because the rubric lacked the export's numbering."""
        from unittest.mock import patch
        from grading.gradescope_sync import GradescopeSender
        self.create()
        for _ in range(4):
            self.sender.drain()
            self.soon()
        def detail():
            rows = self.frq.responses(self.aid)
            with connect(self.path) as db:
                return GradescopeSender.coverage_detail(db, self.aid, rows)
        with patch("grading.gradescope_sync.rubric_numbers", return_value={}):  # as a drafted rubric reads
            self.assertEqual(detail()["unsendable"], {})
            with connect(self.path) as db:
                created = {r[0] for r in db.execute("SELECT DISTINCT question_id FROM frq_gradescope_created_questions")}
                db.execute("DROP TRIGGER IF EXISTS frq_gradescope_created_question_retained")
                db.execute("DELETE FROM frq_gradescope_created_questions")  # without Faculty's receipts it could never be sent
                db.commit()
            self.assertEqual(set(detail()["unsendable"]), created)

    def test_a_write_whose_answer_was_lost_is_read_again_and_never_made_twice(self):
        self.fake.fail("POST", r"^/courses/\d+/assignments$", "drop_after")
        self.fake.fail("POST", r"/rubric_items$", "drop_after")
        self.fake.fail("PATCH", r"/outline/$", "drop_after")
        self.create()
        for _ in range(6):
            self.sender.drain()
            self.soon()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "complete", unit["error"])
        titles = [a["title"] for a in self.fake.assignments.values() if a["course"] == self.empty]
        self.assertEqual(sorted(titles), sorted([self.title, f"{self.title} Version A", f"{self.title} Version B"]))
        _, versions = self.made()
        for assignment in versions.values():
            for question in (q for q in self.fake.questions.values() if q["assignment"] == assignment["id"]):
                wording = [i["description"] for i in self.fake.rubric_items(question["id"])]
                self.assertEqual(len(wording), len(set(wording)), wording)  # no line made twice
            self.assertEqual(len([q for q in self.fake.questions.values() if q["assignment"] == assignment["id"]]), 2)

    def test_gradescope_still_reading_the_template_is_waited_for(self):
        self.fake.template_reads = 2
        self.create()
        self.sender.drain()
        [unit] = self.creation_units()
        self.assertIn(unit["status"], ("pending", "uncertain"))
        self.assertIn("still reading", unit["error"])
        for _ in range(4):
            self.soon()
            self.sender.drain()
        self.assertEqual(self.creation_units()[0]["status"], "complete")

    def test_a_name_already_on_gradescope_is_refused_before_anything_is_made(self):
        self.fake.add_assignment(self.empty, f"{self.title} Version B", 2)
        view = self.create_view()
        self.assertFalse(view["ready"])
        self.assertIn("already has an assignment named", view["courses"][0]["problem"])
        with self.assertRaises(Conflict):
            self.create(view)
        # Another name makes it.
        view = self.create_view(title="Quiz 9 (Faculty)")
        self.assertTrue(view["ready"])
        self.assertEqual(view["versions"][0]["title"], "Quiz 9 (Faculty) Version A")
        self.create(view, title="Quiz 9 (Faculty)")
        self.sender.drain()
        self.assertEqual(self.creation_units()[0]["status"], "complete")

    def test_an_assignment_edited_on_gradescope_meanwhile_stops_for_retry(self):
        self.create()
        self.sender.process_one()  # Version A is made
        mine = next(a for a in self.fake.assignments.values() if a["title"] == f"{self.title} Version A")
        extra = self.fake.add_question(mine["id"], 1, "Someone's own question", 3, [(3, "Right")])
        self.sender.drain()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "failed")
        self.assertIn("not the one Faculty made", unit["error"])
        del self.fake.questions[extra]
        with connect(self.path) as db:
            creation = db.execute("SELECT id FROM frq_gradescope_creations").fetchone()[0]
        self.sender.create_retry(self.aid, creation)
        with self.assertRaises(Conflict):
            self.sender.create_retry(self.aid, creation)  # nothing stopped any more
        self.sender.drain()
        self.assertEqual(self.creation_units()[0]["status"], "complete")
        self.assertEqual(self.client.get(f"/api/grading/gradescope/assignments/{self.aid}", headers=HEADERS).json["creations"][0]["units"][0]["status"],
                         "complete")

    def test_a_changed_preview_is_refused_and_request_ids_are_idempotent(self):
        view = self.create_view()
        self.fake.add_assignment(self.empty, "Unrelated", 1)  # an unrelated change on Gradescope does not matter ...
        request = {"request_id": str(uuid.uuid4()), "confirmation": "create", "fingerprint": view["fingerprint"], "targets": self.targets}
        first = self.sender.create(self.aid, dict(request))
        self.assertEqual(self.sender.create(self.aid, dict(request))["creation_id"], first["creation_id"])
        with self.assertRaises(Conflict):
            self.sender.create(self.aid, {**request, "title": "Another"})
        with self.assertRaises(ValueError):
            self.sender.create(self.aid, {**request, "request_id": str(uuid.uuid4()), "confirmation": "yes"})
        self.sender.create_cancel(self.aid, first["creation_id"])
        self.fake.add_assignment(self.empty, f"{self.title} Version A", 2)  # ... but one that takes the name does
        with self.assertRaises(Conflict):
            self.sender.create(self.aid, {**request, "request_id": str(uuid.uuid4())})

    def test_cancel_stops_what_waits(self):
        receipt = self.create()
        self.sender.process_one()
        self.sender.create_cancel(self.aid, receipt["creation_id"])
        self.sender.drain()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "cancelled")
        self.assertEqual(len([a for a in self.fake.assignments.values() if a["course"] == self.empty]), 1)  # what was made stays; nothing more

    def test_the_forbidden_course_is_never_offered_or_touched(self):
        self.targets = {self.section["id"]: {"course_id": str(self.forbidden), "container_id": ""}}
        view = self.create_view()
        self.assertFalse(view["ready"])
        self.assertIn("never touches", view["sections"][0]["reason"])
        self.assertNothingForbidden()

    def test_a_question_without_confirmed_areas_blocks_creating(self):
        with patch.object(self.frq, "location_status", side_effect=lambda db, aid, question, **_: {"status": "pending", "context": {"template": None}}):
            view = self.create_view()
        self.assertFalse(view["ready"])
        self.assertTrue(any("confirm its answer areas" in p for p in view["problems"]), view["problems"])

    def test_a_rubric_changed_after_creating_refuses_only_that_question(self):
        self.create()
        self.sender.drain()
        _, versions = self.made()
        question = next(q for q in self.fake.questions.values() if q["assignment"] == versions["A"]["id"] and q["title"] == "Question 20")
        item = self.fake.rubric_items(question["id"])[0]
        item["description"] = "Something else entirely"
        view = self.view()
        self.assertEqual(view["summary"]["grades"]["refused"], 1)
        self.assertEqual(view["summary"]["grades"]["send"], 3)

    def test_a_recorded_gradescope_wording_is_matched_and_nothing_else_changes(self):
        """RENAMED ITEMS (requirement, 2026-10-31, Exam 2 Version B Q25 renamed on Gradescope to the booklet's words): the wording
        recorded from Gradescope is matched like the sent one; Faculty's rubric stays, and an item worth something else is refused."""
        self.create()
        self.sender.drain()
        _, versions = self.made()
        question = next(q for q in self.fake.questions.values() if q["assignment"] == versions["A"]["id"] and q["title"] == "Question 20")
        item = self.fake.rubric_items(question["id"])[0]
        item["description"] = "Renamed to match the booklet"
        with connect(self.path) as db:
            [qid] = [r[0] for r in db.execute("""SELECT r.question_id FROM frq_gradescope_created_questions r JOIN frq_questions q ON q.id=r.question_id
                WHERE q.question_key LIKE 'q20%' AND q.exam_version='A'""")]
            [line] = [k for k, v in json.loads(db.execute("SELECT items_json FROM frq_gradescope_created_questions WHERE question_id=?", (qid,)).fetchone()[0]).items()
                      if str(v["id"]) == str(item["id"])]
            rubric_before = db.execute("SELECT rubric_id FROM frq_assignment_questions WHERE question_id=?", (qid,)).fetchone()[0]
        with self.assertRaises(ValueError):
            self.sender.record_item_wordings(self.aid, {"question_id": qid, "items": [line]})  # no reason
        reads = len(self.fake.requests)
        recorded = self.sender.record_item_wordings(self.aid, {"question_id": qid, "items": [line], "reason": "Renamed on Gradescope"})
        self.assertEqual(recorded["recorded"], [{"course_id": str(self.empty), "item_id": line, "description": "Renamed to match the booklet"}])
        self.assertEqual([r for r in self.fake.requests[reads:] if r["method"] != "GET" and r["path"] != "/login"], [])  # only read
        again = self.sender.record_item_wordings(self.aid, {"question_id": qid, "items": [line], "reason": "Renamed on Gradescope"})
        self.assertEqual(again["recorded"], [])  # already known: nothing recorded twice
        view = self.view()
        self.assertEqual(view["summary"]["grades"]["refused"], 0, view["refused"])
        self.assertEqual(view["summary"]["grades"]["send"], 4)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT rubric_id FROM frq_assignment_questions WHERE question_id=?", (qid,)).fetchone()[0], rubric_before)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_item_wordings").fetchone()[0], 1)
        item["weight"] = float(item["weight"]) + 1  # worth something else: never recorded, still refused
        with self.assertRaises(Conflict):
            self.sender.record_item_wordings(self.aid, {"question_id": qid, "items": [line], "reason": "Renamed on Gradescope"})
        base = f"/api/grading/gradescope/assignments/{self.aid}"
        answer = self.client.post(f"{base}/item-wordings", json={"question_id": qid, "items": [line], "reason": "x"}, headers=HEADERS)
        self.assertEqual(answer.status_code, 409, answer.json)


    def test_the_routes_preview_create_and_cancel(self):
        base = f"/api/grading/gradescope/assignments/{self.aid}"
        preview = self.client.post(f"{base}/create/preview", json={"targets": self.targets}, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertTrue(preview.json["ready"])
        refused = self.client.post(f"{base}/create", json={"request_id": str(uuid.uuid4()), "fingerprint": preview.json["fingerprint"], "targets": self.targets},
                                   headers=HEADERS)
        self.assertEqual(refused.status_code, 400)  # no confirmation: nothing recorded
        made = self.client.post(f"{base}/create", json={"request_id": str(uuid.uuid4()), "confirmation": "create", "fingerprint": preview.json["fingerprint"],
                                                        "targets": self.targets}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        stopped = self.client.post(f"{base}/creations/{made.json['creation_id']}/cancel", json={}, headers=HEADERS)
        self.assertEqual(stopped.json["status"], "cancelled")
        self.assertEqual(self.writes(), [])  # cancelled before the sender's first step: nothing reached Gradescope


class AddVersionTests(CreateCase):
    """2026-10-06: a makeup's Version E, sat by one section's student, was missing from the Quiz 4 set the sections' papers went to.
    A section already sent to a version set that lacks a version its papers are in gets that version added: made as any version is
    (its blank, questions, boxes, rubric), then saved into the set after its versions, which Gradescope letters in order. Here the
    section goes to "Weekly Quiz", a set with Version A only, and its second student's paper is Version B."""

    def setUp(self):
        super().setUp()
        self.container, self.old = self.fake.add_container(self.empty, "Weekly Quiz", "A", 2)
        course = self.fake.courses[self.empty]
        with connect(self.path) as db:
            self.sender._record_links(db, self.aid, {self.section["id"]: {
                "course_id": str(self.empty), "course_title": course["short"], "course_name": course["name"], "container_id": str(self.container),
                "container_title": "Weekly Quiz", "versions": {"A": str(self.old["A"])}}}, "2026-10-06T12:00:00+00:00")
        self.targets = {self.section["id"]: {"course_id": str(self.empty), "container_id": str(self.container)}}

    def in_set(self):
        return {a["version"]: a for a in self.fake.assignments.values() if a["parent"] == self.container}

    def test_the_preview_adds_only_the_version_a_paper_needs_and_writes_nothing(self):
        view = self.create_view()
        self.assertTrue(view["ready"], view)
        self.assertEqual([(v["letter"], v["title"]) for v in view["versions"]], [("B", f"{self.title} Version B")])
        [course] = view["courses"]
        self.assertEqual((course["course_id"], course["problem"], course["adds"]),
                         (str(self.empty), "", {"container": "Weekly Quiz", "has": ["A"], "letters": ["B"]}))
        self.assertEqual(self.writes(), [])
        # No paper of the section is Version B: nothing to add.
        with patch.object(self.frq, "paper_version", return_value={"status": "confirmed", "version": "A"}):
            view = self.create_view()
        self.assertFalse(view["ready"])
        self.assertEqual((view["courses"], [s["reason"] for s in view["sections"]]), ([], ["Already goes to an assignment on Gradescope."]))
        self.assertNothingForbidden()

    def test_the_version_is_made_added_to_the_set_and_the_send_then_goes_to_it(self):
        self.assertEqual(self.view()["sections"][0]["missing_versions"], ["B"])  # what the send dialog offers to add
        self.create()
        self.sender.drain()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "complete", unit["error"])
        versions = self.in_set()
        self.assertEqual(sorted(versions), ["A", "B"])
        self.assertEqual(versions["A"]["id"], self.old["A"])  # the version already there stays as it was
        self.assertEqual((versions["B"]["title"], versions["B"]["pages"]), (f"{self.title} Version B", 2))
        questions = sorted((q for q in self.fake.questions.values() if q["assignment"] == versions["B"]["id"]), key=lambda q: q["index"])
        self.assertEqual([(q["title"], q["weight"]) for q in questions], [("Question 20", 10.0), ("Question 21", 8.0)])
        self.assertTrue(all(("Correct", 0.0) not in [(i["description"], i["weight"]) for i in self.fake.rubric_items(q["id"])] for q in questions))
        # One Save of the set, keeping its title and anonymity, and no new set.
        [edit] = self.fake.version_set_edits
        self.assertEqual((edit["form"]["assignment_ids"], edit["form"]["assignment_container[title]"], edit["form"]["assignment_container[submissions_anonymized]"]),
                         (f"{self.old['A']},{versions['B']['id']}", "Weekly Quiz", "0"))
        self.assertEqual([r for r in self.writes() if r["path"].endswith("/assignment_containers")], [])
        self.assertEqual(self.fake.assignments[self.container]["title"], "Weekly Quiz")
        with connect(self.path) as db:
            receipts = [r[0] for r in db.execute("SELECT exam_version FROM frq_gradescope_created_questions")]
            link = db.execute("SELECT * FROM frq_gradescope_targets WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (self.aid,)).fetchone()
        self.assertEqual(receipts, ["B", "B"])
        self.assertEqual((link["container_id"], json.loads(link["versions_json"])), (str(self.container), {"A": str(self.old["A"]), "B": str(versions["B"]["id"])}))
        # The send now goes to both versions: Version B's paper and grades too.
        view = self.view()
        [section] = view["sections"]
        self.assertEqual((section["container_id"], section["problem"], sorted(section["versions"])), (str(self.container), "", ["A", "B"]))
        self.assertNotIn("missing_versions", section)
        self.assertFalse([s for s in view["skipped"] if "version" in s["reason"]], view["skipped"])
        self.assertEqual(sum(q["send"] for q in view["questions"] if q["version"] == "B"), 2, view["questions"])
        self.assertEqual(self.fake.published(), [])
        self.assertNothingForbidden()

    def test_a_save_whose_answer_was_lost_is_read_again_and_never_sent_twice(self):
        self.fake.fail("POST", r"^/courses/\d+/assignment_containers/\d+$", "drop_after")
        self.create()
        for _ in range(4):
            self.sender.drain()
            self.soon()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "complete", unit["error"])
        self.assertEqual(len(self.fake.version_set_edits), 1)
        self.assertEqual(sorted(self.in_set()), ["A", "B"])

    def test_a_set_changed_on_gradescope_since_the_preview_stops_and_is_left_alone(self):
        self.create()
        self.fake.add_assignment(self.empty, "Weekly Quiz Version B", 2, parent=self.container, version="B")  # someone added one there
        for _ in range(4):
            self.sender.drain()
            self.soon()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "failed")
        self.assertIn("no longer has the versions the preview showed", unit["error"])
        self.assertEqual(self.fake.version_set_edits, [])

    def test_only_the_added_versions_problems_hold_it_up(self):
        confirmed = self.frq.location_status  # the fixture's: every area confirmed
        with patch.object(self.frq, "location_status", side_effect=lambda db, aid, question, **_: {
                **confirmed(db, aid, question), "status": "confirmed" if question["exam_version"] == "B" else "needs_review"}):
            view = self.create_view()
            self.assertTrue(view["ready"], view["problems"])  # Version A's unconfirmed areas: it is already on Gradescope
            self.targets = {self.section["id"]: {"course_id": str(self.empty), "container_id": ""}}
            view = self.create_view()  # making the whole assignment: Version A's problem counts
        self.assertFalse(view["ready"])
        self.assertTrue(view["problems"] and all("Version A" in p for p in view["problems"]), view["problems"])

    def test_a_set_whose_letters_cannot_continue_is_refused(self):
        odd, _ = self.fake.add_container(self.empty, "Only B", "B", 2)
        self.targets = {self.section["id"]: {"course_id": str(self.empty), "container_id": str(odd)}}
        view = self.create_view()
        self.assertFalse(view["ready"])
        self.assertEqual(view["courses"], [])
        self.assertIn("Faculty cannot add Version A", view["sections"][0]["reason"])



class AutomaticVersionTests(CreateCase):
    """2026-10-09 (instructor: "When the makeup version is approved (ie, everything is settled up to the point grading begins), it should
    automatically create that version in all sections' gradescopes"): the sender adds a settled makeup version to every section's linked
    set by itself, whether or not a paper is in it yet, once. Here Version B stands for the makeup, "Weekly Quiz" has Version A only."""

    def setUp(self):
        super().setUp()
        self.container, self.old = self.fake.add_container(self.empty, "Weekly Quiz", "A", 2)
        course = self.fake.courses[self.empty]
        with connect(self.path) as db:
            self.sender._record_links(db, self.aid, {self.section["id"]: {
                "course_id": str(self.empty), "course_title": course["short"], "course_name": course["name"], "container_id": str(self.container),
                "container_title": "Weekly Quiz", "versions": {"A": str(self.old["A"])}}}, "2026-10-09T12:00:00+00:00")
        self.enterContext(patch.object(self.frq, "approved_makeup_versions", return_value=[(self.aid, "B")]))
        # No paper is in Version B yet: the version goes to every linked set all the same.
        self.enterContext(patch.object(self.frq, "paper_version", return_value={"status": "confirmed", "version": "A"}))

    def look(self):
        self.sender._makeup_looked = -10**6  # a minute has passed
        self.sender.drain()

    def creations(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_creations ORDER BY rowid")]

    def not_added(self):
        with connect(self.path) as db:
            return [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE kind='gradescope_version_not_added' ORDER BY id")]

    def test_a_settled_makeup_version_is_added_to_the_linked_set_by_itself_once(self):
        with connect(self.path) as db:
            self.assertTrue(self.frq.makeup_settled(db, self.aid, "B"))
        self.look()
        [creation] = self.creations()
        self.assertEqual((creation["actor"], json.loads(creation["plan_json"])["automatic"]["letter"]), ("automatic", "B"))
        self.look()  # its units are made
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "complete", unit["error"])
        versions = {a["version"]: a for a in self.fake.assignments.values() if a["parent"] == self.container}
        self.assertEqual(sorted(versions), ["A", "B"])
        self.look()
        self.assertEqual(len(self.creations()), 1)  # never made twice
        self.assertEqual(self.not_added(), [])
        self.assertNothingForbidden()

    def test_a_version_not_settled_yet_is_not_added(self):
        located = self.frq.location_status
        with patch.object(self.frq, "location_status", side_effect=lambda db, aid, question, **_: {
                **located(db, aid, question), "status": "needs_review" if question["exam_version"] == "B" else "confirmed"}):
            with connect(self.path) as db:
                self.assertFalse(self.frq.makeup_settled(db, self.aid, "B"))
            self.look()
        self.assertEqual(self.creations(), [])
        self.assertEqual(self.writes(), [])

    def test_a_set_it_cannot_be_added_to_is_said_once_and_left_to_the_instructor(self):
        odd, _ = self.fake.add_container(self.empty, "Only B", "B", 2)
        course = self.fake.courses[self.empty]
        with connect(self.path) as db:
            self.sender._record_links(db, self.aid, {self.section["id"]: {
                "course_id": str(self.empty), "course_title": course["short"], "course_name": course["name"], "container_id": str(odd),
                "container_title": "Only B", "versions": {"B": "1"}}}, "2026-10-09T12:01:00+00:00")
        self.look()
        self.look()
        self.assertEqual(self.creations(), [])
        [said] = self.not_added()
        self.assertEqual(said["letter"], "B")
        self.assertTrue(said["reasons"] and "Only B" in said["reasons"][0], said)

    def test_a_plan_never_sent_to_gradescope_is_left_alone(self):
        with patch.object(self.sender, "_targets", return_value=[]):  # no section links it on Gradescope
            self.look()
        self.assertEqual((self.creations(), self.not_added(), self.writes()), ([], [], []))

    def queue_late(self, enrollment):
        """A late paper Faculty certified by itself, queued to go to Gradescope (the certification's own row, synthetic here)."""
        now = datetime.now(timezone.utc).isoformat()
        unit = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=OFF")  # the certification it names is not part of this fixture
            db.execute("""INSERT INTO frq_gradescope_automatic_sends(id,certification_id,assignment_id,section_id,enrollment_id,kind,papers_only,status,attempts,error,
                actor,created_at,updated_at) VALUES(?,?,?,?,?,'written',0,'pending',0,'','automatic',?,?)""", (unit, "c-late", self.aid, self.section["id"], enrollment, now, now))
        return unit

    def automatic_row(self, unit):
        with connect(self.path) as db:
            return dict(db.execute("SELECT * FROM frq_gradescope_automatic_sends WHERE id=?", (unit,)).fetchone())

    def test_a_late_paper_in_a_makeup_version_not_added_yet_waits_for_it_and_then_goes(self):
        # 2026-10-09 review: a late send prepared before the makeup version's own (once a minute) look was skipped for good with
        # "has no version B". An approved makeup version of this plan that Faculty has not tried to add counts as being added.
        self.enterContext(patch.object(self.frq, "paper_version", side_effect=lambda db, plan, person, shared=None:
                                       {"status": "confirmed", "version": "B" if person["enrollment_id"] == self.second else "A"}))
        unit = self.queue_late(self.second)
        self.sender._auto_unit({**self.automatic_row(unit), "kind": "automatic", "part": "written"})
        row = self.automatic_row(unit)
        self.assertEqual((row["status"], row["error"]), ("pending", "Waiting: Faculty is still adding this version on Gradescope."))
        self.look()  # the version is recorded, then made
        self.look()
        self.assertEqual([u["status"] for u in self.creation_units()], ["complete"])
        with connect(self.path) as db:
            db.execute("UPDATE frq_gradescope_automatic_sends SET not_before=NULL WHERE id=?", (unit,))  # its minute has passed
        self.sender.drain()
        row = self.automatic_row(unit)
        self.assertEqual(row["status"], "recorded", row["error"])
        self.assertNothingForbidden()

    def test_a_version_faculty_could_not_add_is_shown_and_its_late_paper_says_why(self):
        odd, _ = self.fake.add_container(self.empty, "A and C", "AC", 2)  # Version B cannot follow C there
        course = self.fake.courses[self.empty]
        self.assertNotIn("automatic", self.sender.status(self.aid))  # nothing to say: the panel's answer is what it was
        with connect(self.path) as db:
            self.sender._record_links(db, self.aid, {self.section["id"]: {
                "course_id": str(self.empty), "course_title": course["short"], "course_name": course["name"], "container_id": str(odd),
                "container_title": "A and C", "versions": {}}}, "2026-10-09T12:01:00+00:00")
            # A reason saying the set has the version already is no news to the instructor: never shown.
            self.frq.event(db, self.aid, "gradescope_version_not_added", {"letter": "C", "reasons": ["“Weekly Quiz” on Gradescope already has Version C."]}, "automatic")
        self.look()
        self.look()
        said = next(x for x in self.not_added() if x["letter"] == "B")
        self.assertTrue(said["reasons"] and not any("already has Version" in r for r in said["reasons"]), said)
        found = self.sender.status(self.aid)["automatic"]["versions_not_added"]
        self.assertEqual([(v["letter"], v["reasons"]) for v in found], [("B", said["reasons"])])
        # A late paper in Version B is then not sent by itself (one attempt per version, left to the instructor's Create), and says why.
        self.enterContext(patch.object(self.frq, "paper_version", side_effect=lambda db, plan, person, shared=None:
                                       {"status": "confirmed", "version": "B" if person["enrollment_id"] == self.second else "A"}))
        with connect(self.path) as db:
            self.sender._record_links(db, self.aid, {self.section["id"]: {
                "course_id": str(self.empty), "course_title": course["short"], "course_name": course["name"], "container_id": str(self.container),
                "container_title": "Weekly Quiz", "versions": {"A": str(self.old["A"])}}}, "2026-10-09T12:02:00+00:00")
        unit = self.queue_late(self.second)
        self.sender._auto_unit({**self.automatic_row(unit), "kind": "automatic", "part": "written"})
        row = self.automatic_row(unit)
        self.assertEqual((row["status"], row["error"]), ("skipped", "Weekly Quiz has no version B."))
        row_key = f"{self.section['id']}:{self.second}"
        self.assertEqual(self.sender.status(self.aid)["automatic"]["sends"], [{"row": row_key, "kind": "written", "status": "skipped", "reason": "Weekly Quiz has no version B."}])
        bob = next(x for x in self.app.extensions["certification"].view(self.aid)["students"] if x["row"] == row_key)
        self.assertEqual(bob["gradescope"]["automatic"], {"kind": "written", "status": "skipped", "reason": "Weekly Quiz has no version B."})
        self.assertNothingForbidden()

    def test_the_approved_makeups_are_read_from_the_catalog(self):
        with connect(self.path) as db:
            self.assertEqual(type(self.frq).approved_makeup_versions(self.frq, db), [])

class HelperTests(CreateCase):
    def test_the_version_set_edit_page_is_read_as_the_sandbox_served_it(self):
        """The form and gon values of Edit Exam Version Set (sandbox course, 2026-09-27), with anonymous grading switched on: kept."""
        from grading.gradescope_web import version_set_form
        text = ('<script>window.gon={};gon.unversioned_assignments=[{"id":8746632,"title":"Faculty create test Version A","settings":'
                '{"bubble_sheet":false,"question_settings":{"scoring_type":"negative"}},"users_seen_assisted_grading_feature":[]},'
                '{"id":8746634,"title":"B"},{"id":8746640,"title":"Faculty create test Version C"}];gon.selected_assignment_ids=[8746632,8746634];'
                'gon.versions=[{"name":"All"}];</script>'
                '<form class="form js-assignmentContainerForm" id="edit_assignment_container_408577" enctype="multipart/form-data" '
                'action="/courses/1413803/assignment_containers/408577" accept-charset="UTF-8" method="post"><input name="utf8" type="hidden" '
                'value="&#x2713;" /><input type="hidden" name="_method" value="patch" /><input type="hidden" name="authenticity_token" value="tok" />'
                '<input placeholder="Name your assignment container" class="js-assignmentContainerName form--textInput" type="text" '
                'value="Faculty &amp; test" name="assignment_container[title]" id="assignment_container_title" /><input type="hidden" '
                'name="assignment_ids" id="assignment_ids" value="8746632,8746634" class="js-assignmentVersionsField form--textInput" />'
                '<input name="assignment_container[submissions_anonymized]" type="hidden" value="0" /><input class="x" type="checkbox" '
                'value="1" checked="checked" name="assignment_container[submissions_anonymized]" id="assignment_container_submissions_anonymized" />'
                '<button name="button" type="button">Delete Exam Version Set</button><input type="submit" name="commit" value="Save" /></form>')
        form = version_set_form(text, 1413803, 408577)
        self.assertEqual(form["fields"], [("utf8", "✓"), ("_method", "patch"), ("authenticity_token", "tok"), ("assignment_container[title]", "Faculty & test"),
                                          ("assignment_ids", ""), ("assignment_container[submissions_anonymized]", "0"),
                                          ("assignment_container[submissions_anonymized]", "1")])
        self.assertEqual((form["title"], form["selected"], form["offered"]), ("Faculty & test", ["8746632", "8746634"], ["8746632", "8746634", "8746640"]))
        self.assertIsNone(version_set_form(text, 1413803, 408578))  # another set's page
        self.assertIsNone(version_set_form(text.replace('value="patch"', 'value="delete"'), 1413803, 408577))
        self.assertIsNone(version_set_form(text.replace("gon.selected_assignment_ids=", "gon.other="), 1413803, 408577))

    def test_boxes_and_weights(self):
        self.assertEqual(gc.crop_rects([{"page": 3, "region": {"x": .1, "y": .2, "width": .85, "height": .333}}]),
                         [{"page_number": 3, "x1": 10.0, "x2": 95.0, "y1": 20.0, "y2": 53.3}])
        self.assertEqual(str(gc.line_weight({"points": -2}, "negative")), "2.0000")
        self.assertEqual(str(gc.line_weight({"points": 3}, "positive")), "3.0000")
        self.assertEqual(gc.question_title({"content": {"printed_number": "4", "title": "Matrix product"}, "question_key": "q4"}), "Question 4: Matrix product")
        self.assertEqual(gc.question_title({"content": {"printed_number": "18", "title": "Question 18 a and b"}, "question_key": "q18"}), "Question 18 a and b")
        with self.assertRaises(ValueError):
            gc.clean_title("x" * 101, "")
