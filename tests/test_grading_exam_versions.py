"""Exam versions behind one label sheet: seat first, confirmed against the blank exams, never switched silently."""
import json
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from grading import version_match
from grading.exam_versions import FIRST_PAGE, NOT_NEEDED, first_page_version
from grading.models import digest
from rosters import connect
import test_frq_grading as grading_fixture
from test_gradescope_rubrics import encode, export_rows
from frq_fixtures import finalize_rubrics


class ExamVersionTests(unittest.TestCase):
    managed = False
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric

    def setUp(self):
        grading_fixture.FrqWorkflowTests.setUp(self)  # One unversioned question q1, approved, and the first student's reviewed paper.
        with connect(self.path) as db:
            self.second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? ORDER BY enrollment_id",
                                     (self.section["id"], self.enrollment)).fetchone()[0]
            self.term_id = db.execute("SELECT term_id FROM course_sections WHERE id=?", (self.section["id"],)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=self.second), changed="second")
        self.templates = {letter: self.blank(letter) for letter in "AB"}
        # Location confirmation has its own tests; blank exams are read through a stub here.
        self.enterContext(patch.object(self.frq, "require_location_review"))
        self.enterContext(patch.object(self.frq.templates, "read", side_effect=lambda identifier, kind, page=None: (f"blank-{identifier}-{page}".encode(), "image/png")))
        self.looks_like = {}  # enrollment -> the version its pages resemble; default: its seat
        self.enterContext(patch("grading.version_match.compare", side_effect=self.compare))

    def blank(self, letter):
        identifier = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO assignment_templates VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (identifier, self.assignment["id"], f"Version {letter} blank",
                str(uuid.uuid4()), letter * 64, 2, "pdf", "[]", f"grading-templates/{identifier}/manifest.json", letter.lower() * 64, timestamp(), None))
        return identifier

    def compare(self, pages, blanks):
        """The second student's fixture pages carry 'second'; a paper resembles its seat unless the test says otherwise."""
        student = self.second if b"second" in next(iter(pages.values())) else self.enrollment
        resembles = self.looks_like.get(student, self.seats.get(student))
        return {"matcher": version_match.MATCHER, "versions": {letter: {"pages": {}, "mean": .84 if letter == resembles else .56 if resembles else .5} for letter in blanks}}

    def seat(self, **versions):
        """One chart for this exam and term; each named student gets a seat with that exam version."""
        self.seats = {getattr(self, name): letter for name, letter in versions.items()}
        now, room, chart = timestamp(), str(uuid.uuid4()), str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO rooms(id,name,created_at,updated_at) VALUES(?,?,?,?)", (room, "Fixture room", now, now))
            db.execute("INSERT INTO seating_charts(id,assignment_id,term_id,room_id,created_at,updated_at,exam_version_count) VALUES(?,?,?,?,?,?,2)",
                       (chart, self.assignment["id"], self.term_id, room, now, now))
            db.execute("INSERT INTO seating_chart_sections VALUES(?,?)", (chart, self.section["id"]))
            for index, (enrollment, letter) in enumerate(self.seats.items()):
                seat = str(uuid.uuid4())
                db.execute("INSERT INTO room_seats(id,room_id,label,grid_row,grid_column,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (seat, room, f"A{index+1}", 1, index + 1, now, now))
                db.execute("INSERT INTO seating_assignments VALUES(?,?,?,?,?)", (chart, seat, self.section["id"], enrollment, now))
                db.execute("INSERT INTO seating_exam_versions VALUES(?,?,?)", (chart, seat, letter))
        return chart

    def question(self, key, letter, template, points):
        """A question of one exam version with that version's blank exam and its own approved rubric."""
        plan = self.frq.assignment(self.plan["id"])
        data = {"version": plan["version"], "question_key": key, "assignment_version_id": self.version_id,
            "content": {"title": "Question 1", "max_points": 10, "problem": f"Solve, version {letter}", "template_id": template,
                        "regions": [{"page": 1, "region": {"x": .1, "y": .1, "width": .8, "height": .4}}]}}
        if letter is not None:
            data["exam_version"] = letter
        plan = self.frq.save_question(plan["id"], data)
        q = next(q for q in plan["questions"] if q["question_key"] == key)
        plan = self.frq.import_rubric(plan["id"], q["question_id"], {"version": q["version"], "source_text": f"-{points} Arithmetic error",
            "rules": {"items": [{"id": "R1", "description": "Arithmetic error", "points": -points, "original_source_text": f"-{points} Arithmetic error"}]}})
        q = next(q for q in plan["questions"] if q["question_key"] == key)
        self.frq.approve_rubric(plan["id"], q["question_id"], {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"]})
        finalize_rubrics(self.frq, plan["id"])
        return q["question_id"]

    def two_versions(self):
        plan = self.frq.assignment(self.plan["id"])
        self.frq.assign_exam_version(plan["id"], {"version": plan["version"], "exam_version": "A", "question_ids": [self.qid]})
        self.qa = self.question("q1", None, self.templates["A"], 1)
        self.qb = self.question("q1-B", "B", self.templates["B"], 2)
        self.assertEqual(self.qa, self.qid)

    def preview(self, questions=None):
        command = {"request_id": str(uuid.uuid4()), "question_ids": questions or [self.qa, self.qb], "mode": "local", "operation": "recognize"}
        return command, self.frq.preview(self.plan["id"], command)

    def compare_all(self):
        for _ in range(10):
            if not self.frq.process_version_one():
                break

    def who(self, preview):
        return sorted((e["question_id"], e["enrollment_id"]) for e in preview["eligible"])

    def test_each_paper_is_read_and_graded_against_its_own_version(self):
        self.seat(enrollment="A", second="B")
        self.two_versions()
        plan = self.frq.assignment(self.plan["id"])
        self.assertEqual(plan["exam_versions"]["letters"], ["A", "B"])
        self.assertEqual({k: v["template_id"] for k, v in plan["exam_versions"]["blanks"].items()}, self.templates)
        with self.assertRaisesRegex(Conflict, "once"):
            self.frq.assign_exam_version(plan["id"], {"version": plan["version"], "exam_version": "B", "question_ids": [self.qid]})
        # The seat alone is not enough: nothing is gradable until the whole paper was compared with the blank exams.
        _, waiting = self.preview()
        self.assertEqual(waiting["eligible"], [])
        self.assertTrue(all("still being confirmed" in s["reason"] for s in waiting["skipped"]), waiting["skipped"])
        self.compare_all()
        command, ready = self.preview()
        self.assertEqual(self.who(ready), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))
        self.assertEqual(ready["skipped"], [])
        self.assertEqual({e["source"]["exam_version"] for e in ready["eligible"]}, {"A", "B"})
        job = self.frq.create_job(self.plan["id"], {**command, "fingerprint": ready["fingerprint"]})
        for _ in range(60):
            if not self.frq.process_one():
                break
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        scores = {(r["question_id"], r["enrollment_id"]): self.frq.response(r["id"])["result"]["score"]["score"] for r in self.frq.responses(self.plan["id"])}
        self.assertEqual(scores, {(self.qa, self.enrollment): 9, (self.qb, self.second): 8})  # Each version's own rubric: -1 and -2.
        # Answer groups are per question, and a question belongs to one exam version: groups never mix versions.
        for question, student in ((self.qa, self.enrollment), (self.qb, self.second)):
            members = [m["id"] for g in self.frq.answer_groups(self.plan["id"], question)["groups"] for m in g["members"]]
            owners = {r["id"]: r["enrollment_id"] for r in self.frq.responses(self.plan["id"], question)}
            self.assertEqual([owners[m] for m in members], [student])
        # A total needs only the questions of the student's own exam version.
        first = next(r for r in self.frq.responses(self.plan["id"], self.qa))
        response = self.frq.response(first["id"])
        self.frq.save_review(first["id"], {"version": response["version"], "result_id": response["current_result_id"], "status": "approved",
            "confirm_review": True, "selected_items": ["R1"]})
        record = json.loads(self.frq.export(self.plan["id"])[0])["records"][0]
        self.assertEqual((record["exam_version"], record["expected_questions"]), ("A", ["q1"]))

    def test_a_paper_that_matches_the_other_version_waits_for_the_instructor_and_is_never_switched(self):
        chart = self.seat(enrollment="A", second="A")
        self.looks_like[self.second] = "B"  # Seated for A, but the student wrote the yellow exam.
        self.two_versions()
        self.preview()
        self.compare_all()
        _, preview = self.preview()
        self.assertEqual(self.who(preview), [(self.qa, self.enrollment)])
        reasons = {s["reason"] for s in preview["skipped"] if s["enrollment_id"] == self.second}
        self.assertEqual(len(reasons), 1)
        self.assertRegex(next(iter(reasons)), "Version A.*Version B")
        listing = self.frq.paper_versions(self.plan["id"])
        self.assertEqual(listing["counts"], {"confirmed": 1, "mismatch": 1})
        waiting = listing["waiting"][0]
        self.assertEqual((waiting["enrollment_id"], waiting["seat"], waiting["matched"]), (self.second, "A", "B"))
        # The assignment says a paper waits for this decision (2026-09-26: makeup papers waited unseen while every step showed done).
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["version_decisions"], 1)
        # The decision dialog shows the paper itself: its pages in order, each with the scan that holds it.
        with connect(self.path) as db:
            selected = json.loads(db.execute("SELECT selection_json FROM assembly_revisions WHERE id=?", (waiting["assembly_revision_id"],)).fetchone()[0])
            scans = {row[0]: row[1] for row in db.execute("SELECT id,message_id FROM scan_pages")}
        self.assertEqual(waiting["pages"], [{"page_number": p["page_number"], "scan_page_id": p["scan_page_id"], "message_id": scans[p["scan_page_id"]]}
                                            for p in sorted(selected, key=lambda p: p["page_number"])])
        self.assertTrue(waiting["pages"])
        with self.assertRaises(ValueError):
            self.frq.confirm_paper_version(self.plan["id"], {**waiting, "exam_version": "B"})  # No explicit confirmation.
        with self.assertRaises(Conflict):
            self.frq.confirm_paper_version(self.plan["id"], {**waiting, "exam_version": "B", "confirm_version": True, "assembly_revision_id": "stale"})
        self.frq.confirm_paper_version(self.plan["id"], {**waiting, "exam_version": "B", "confirm_version": True, "note": "Yellow paper"})
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["version_decisions"], 0)
        _, preview = self.preview()
        self.assertEqual(self.who(preview), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))
        with connect(self.path) as db:  # The decision is about this paper only: the seating chart still says A.
            self.assertEqual([r[0] for r in db.execute("SELECT exam_version FROM seating_exam_versions WHERE chart_id=?", (chart,))], ["A", "A"])
            self.assertEqual(db.execute("SELECT exam_version,actor FROM frq_version_reviews").fetchall()[0][:], ("B", "workspace"))

    def test_a_paper_with_no_resolvable_version_is_not_graded(self):
        self.seat(enrollment="A")  # The second student has no seat.
        self.looks_like[self.second] = None  # ... and pages that match neither blank exam clearly.
        self.two_versions()
        for _ in range(2):  # the whole paper, then its page 1, which no reader here can read
            self.preview()
            self.compare_all()
        _, preview = self.preview()
        self.assertEqual(self.who(preview), [(self.qa, self.enrollment)])
        self.assertTrue(all("Confirm whether it is Version A or Version B" in s["reason"] for s in preview["skipped"]), preview["skipped"])
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 1, "unresolved": 1})

    def test_a_decision_an_earlier_matcher_reached_stands_and_an_undecided_paper_is_compared_again(self):
        """whole-paper-v2 (2026-09-23) straightens tilted pages. A paper v1 already decided keeps that decision, so a new
        matcher never puts every paper back to "checking"; one v1 could not decide (a tilted page, say) is compared again."""
        self.seat(enrollment="A", second="B")
        self.looks_like[self.second] = None  # Under v1 the second paper matched neither blank exam clearly.
        self.two_versions()
        with patch.object(version_match, "MATCHER", "whole-paper-v1"):
            self.preview()
            self.compare_all()
            self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 1, "unclear": 1})
        with connect(self.path) as db:
            earlier = {row[0]: row[1] for row in db.execute("SELECT enrollment_id,id FROM frq_version_checks")}
        # Under v2 the decided paper stands on its v1 check; only the undecided one is queued again.
        found = self.frq.paper_versions(self.plan["id"])
        self.assertEqual(found["counts"], {"confirmed": 1, "checking": 1})
        self.looks_like[self.second] = "B"  # Straightened, its pages now match its seat.
        self.compare_all()
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})
        with connect(self.path) as db:
            rows = db.execute("SELECT enrollment_id,id,json_extract(input_json,'$.matcher') FROM frq_version_checks ORDER BY rowid").fetchall()
        rows = [r for r in rows if r[2] != FIRST_PAGE]  # no local OCR here: page 1 was not read, so whole papers are compared
        self.assertEqual(sorted((r[0], r[2]) for r in rows), sorted([(self.enrollment, "whole-paper-v1"), (self.second, "whole-paper-v1"), (self.second, version_match.MATCHER)]))
        self.assertIn((self.enrollment, earlier[self.enrollment]), [(r[0], r[1]) for r in rows])
        self.assertEqual(self.who(self.preview()[1]), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))

    def test_without_a_seat_the_comparison_only_proposes_and_the_instructor_confirms(self):
        self.seat(enrollment="A")
        self.looks_like[self.second] = "B"
        self.two_versions()
        for _ in range(2):  # the whole paper, then (it matching a version) its page 1, which no reader here can read
            self.preview()
            self.compare_all()
        _, preview = self.preview()
        self.assertEqual(self.who(preview), [(self.qa, self.enrollment)])
        waiting = self.frq.paper_versions(self.plan["id"])["waiting"][0]
        self.assertEqual((waiting["status"], waiting["seat"], waiting["matched"]), ("proposed", None, "B"))
        self.frq.confirm_paper_version(self.plan["id"], {**waiting, "exam_version": "B", "confirm_version": True})
        self.assertEqual(self.who(self.preview()[1]), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))

    def test_without_a_seat_page_one_printing_the_matched_version_confirms_the_paper(self):
        """NO SEAT (requirement, 2026-10-26): page 1 and the whole paper agreeing confirm a paper with no seat; page 1 naming another version leaves it proposed."""
        self.seat(enrollment="A")
        self.looks_like[self.second] = "B"
        self.two_versions()
        self.read_first_pages({self.enrollment: "A", self.second: "B"})
        for _ in range(2):  # the whole paper, then (it matching a version) its page 1
            self.preview()
            self.compare_all()
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})
        self.assertEqual(self.who(self.preview()[1]), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))
        with connect(self.path) as db:
            read = db.execute("SELECT json_extract(input_json,'$.expected'),json_extract(result_json,'$.agrees') FROM frq_version_checks WHERE enrollment_id=? AND json_extract(input_json,'$.matcher')=?",
                              (self.second, FIRST_PAGE)).fetchone()
            self.assertEqual(tuple(read), (None, 0))  # expecting no letter, its reading sets no comparison aside
            self.assertEqual(ExamVersionTests.progress(db, self.plan["id"]), {"left": 0, "done": 2, "failed": 0, "total": 2})

    def test_without_a_seat_page_one_naming_another_version_leaves_it_proposed(self):
        self.seat(enrollment="A")
        self.looks_like[self.second] = "B"
        self.two_versions()
        self.read_first_pages({self.enrollment: "A", self.second: "A"})
        for _ in range(2):  # the whole paper, then (it matching a version) its page 1
            self.preview()
            self.compare_all()
        listing = self.frq.paper_versions(self.plan["id"])
        self.assertEqual(listing["counts"], {"confirmed": 1, "proposed": 1})
        self.assertEqual((listing["waiting"][0]["enrollment_id"], listing["waiting"][0]["matched"]), (self.second, "B"))

    def read_on_first_page(self, enrollment, letter, registered_by=None):
        """This student's paper was registered from a scan without labels, and `letter` was read in its first page's title box.
        `registered_by` names who asked for the registration (`workspace`: the instructor's Register click; `automatic`)."""
        with connect(self.path) as db:
            page = db.execute("""SELECT sp.id,sp.message_id,sp.page_stamp_id,c.id copy_id FROM printed_copies c
                JOIN page_stamps p ON p.printed_copy_id=c.id AND p.page_number=1 JOIN scan_pages sp ON sp.page_stamp_id=p.id
                WHERE c.enrollment_id=? AND EXISTS(SELECT 1 FROM assembly_revisions ar WHERE ar.printed_copy_id=c.id) LIMIT 1""", (enrollment,)).fetchone()
            run, now = str(uuid.uuid4()), timestamp()
            attempt = 1 + db.execute("SELECT COUNT(*) FROM scan_unlabeled_runs WHERE message_id=?", (page["message_id"],)).fetchone()[0]
            db.execute("""INSERT INTO scan_unlabeled_runs(id,message_id,attempt,assignment_id,term_id,sides,status,error,actor,created_at,updated_at,versions)
                          VALUES(?,?,?,?,?,2,'complete','','workspace',?,?,'AB')""", (run, page["message_id"], attempt, self.assignment["id"], self.term_id, now, now))
            db.execute("INSERT INTO scan_unlabeled_reads VALUES(?,?,1,?,?)", (run, page["id"], json.dumps({"version": letter}), now))
            db.execute("INSERT INTO scan_unlabeled_registrations VALUES(?,?,?,?,?,?,?)", (page["id"], run, page["copy_id"], page["page_stamp_id"], enrollment, "uin", now))
            if registered_by:
                db.execute("INSERT INTO scan_unlabeled_plans VALUES(?,?,?,?,?,?,?,?,?,?)", (run, page["id"], enrollment, self.section["id"], "uin",
                           page["copy_id"], None, str(uuid.uuid4()), registered_by, now))

    def test_registering_in_the_inbox_confirms_the_printed_version_even_against_a_seat(self):
        # Requirement, 2026-09-26: makeup papers still on the room chart were "kicked back to confirm its version in the grading
        # pipeline, but I already confirmed it in the scan inbox". The Register click confirms the printed version shown there.
        self.seat(enrollment="A", second="A")
        self.two_versions()
        self.looks_like[self.second] = "B"
        self.read_on_first_page(self.second, "B", registered_by="workspace")
        _, waiting = self.preview()
        self.assertTrue(any("still being confirmed" in s["reason"] for s in waiting["skipped"]), "The blank-exam comparison is waited for.")
        self.compare_all()
        _, ready = self.preview()
        self.assertEqual(self.who(ready), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))
        versions = self.frq.paper_versions(self.plan["id"])
        self.assertEqual((versions["counts"], versions["waiting"]), ({"confirmed": 2}, []))
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_version_reviews").fetchone()[0], 0, "Nothing is written: it is read from the registration.")
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["version_decisions"], 0)
        # A letter the assignment does not have confirms nothing: the seat and the comparison decide as before.
        self.read_on_first_page(self.enrollment, "E", registered_by="workspace")
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})
        self.assertEqual(self.who(self.preview()[1]), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))

    def test_an_automatic_registration_confirms_nothing_and_its_paper_keeps_grading_unfinished(self):
        self.seat(enrollment="A", second="A")
        self.two_versions()
        self.looks_like[self.second] = "B"
        self.read_on_first_page(self.second, "B", registered_by="automatic")  # Registered after a confident match: nobody clicked.
        self.preview()
        self.compare_all()
        [paper] = self.frq.paper_versions(self.plan["id"])["waiting"]
        self.assertEqual((paper["enrollment_id"], paper["status"], paper["seat"], paper["matched"]), (self.second, "mismatch", "A", "B"))
        workflow = self.frq.assignment(self.plan["id"])["workflow"]
        self.assertEqual((workflow["version_decisions"], workflow["decision_responses"]), (1, 1))
        self.assertEqual(workflow["progress"]["grade"]["total"], 2, "The waiting paper's question is unfinished grading work.")

    def test_a_registered_version_the_blank_exams_contradict_waits_for_the_instructor(self):
        self.seat(enrollment="A", second="A")
        self.two_versions()
        self.read_on_first_page(self.second, "B", registered_by="workspace")  # Its pages resemble its seat's Version A.
        self.preview()
        self.compare_all()
        [paper] = self.frq.paper_versions(self.plan["id"])["waiting"]
        self.assertEqual((paper["enrollment_id"], paper["status"], paper["printed"], paper["matched"]), (self.second, "mismatch", "B", "A"))
        self.assertIn("Version B was read on this paper’s first page and confirmed when you registered it in the Inbox, but its pages match Version A’s", paper["reason"])

    def test_a_preview_queues_comparisons_only_for_the_papers_it_looked_at(self):
        # The "new" preview lists the plan's responses on its own read (performance audit, 2026-09-29), with copies of the
        # participants: what that listing works out never becomes a comparison queued for a paper the preview itself skipped.
        self.seat(enrollment="A", second="B")
        self.two_versions()
        self.preview()
        self.compare_all()
        command, ready = self.preview()
        self.frq.create_job(self.plan["id"], {**command, "fingerprint": ready["fingerprint"]})
        for _ in range(60):
            if not self.frq.process_one():
                break
        chosen = next(r["id"] for r in self.frq.responses(self.plan["id"]) if r["enrollment_id"] == self.enrollment)
        with patch.object(version_match, "MATCHER", "whole-paper-test"):  # every paper's comparison is due again
            preview = self.frq.preview(self.plan["id"], {**command, "request_id": str(uuid.uuid4()), "selection": "new", "response_ids": [chosen]})
            self.assertEqual((preview["eligible"], [s["enrollment_id"] for s in preview["skipped"]]), ([], [self.enrollment]))
            with connect(self.path) as db:
                queued = [r[0] for r in db.execute("SELECT enrollment_id FROM frq_version_checks WHERE json_extract(input_json,'$.matcher')='whole-paper-test'")]
        self.assertEqual(queued, [self.enrollment])

    def test_a_read_over_many_papers_decides_each_paper_as_it_decides_one_paper_alone(self):
        # Performance audit, 2026-09-29: from its second paper a read's memo reads the plan's seats and version decisions once, and a
        # pass that asks for it (`prefetch`: discovery, paper_versions) each paper's document and newest registration; one paper
        # (a grading step's check) keeps its own indexed reads, and a tie between registrations is left to the one-paper read.
        self.seat(enrollment="A", second="A")
        self.two_versions()
        self.looks_like[self.second] = "B"
        self.read_on_first_page(self.second, "B", registered_by="workspace")
        self.preview()
        self.compare_all()

        def decide(memo):
            with connect(self.path) as db:
                plan = self.frq.plan(db, self.plan["id"])
                _, participants = self.frq.manual._context(db, plan["event_id"])
                asked = []
                db.set_trace_callback(asked.append)
                return [self.frq.paper_version(db, plan, p, memo()) for p in participants], asked
        seat_alone = lambda asked: [sql for sql in asked if "AND a.section_id=" in sql]
        registration_alone = lambda asked: [sql for sql in asked if "scan_unlabeled_registrations" in sql and "WHERE ar.id=" in sql]
        # Performance pass, 2026-10-02: the plan's comparisons too are read once from the second paper on.
        comparison_alone = lambda asked: [sql for sql in asked if "FROM frq_version_checks WHERE assignment_id=" in sql and "AND section_id=" in sql]
        alone, alone_asked = decide(dict)
        shared, prefetched = {}, {"prefetch": {}}
        many, many_asked = decide(lambda: shared)
        passed, passed_asked = decide(lambda: prefetched)
        self.assertEqual([found["status"] for found in alone], ["confirmed", "confirmed"])
        self.assertEqual((many, passed), (alone, alone))
        self.assertEqual([len(seat_alone(asked)) for asked in (alone_asked, many_asked)], [2, 1])
        self.assertEqual([len(comparison_alone(asked)) for asked in (alone_asked, many_asked)], [2, 1])
        self.assertEqual([len(registration_alone(asked)) for asked in (alone_asked, passed_asked)], [2, 0])
        # Two registrations of one paper at the same moment: which one counts is the one-paper read's to say, as before.
        with connect(self.path) as db:
            first = db.execute("SELECT * FROM scan_unlabeled_registrations").fetchone()
            other = db.execute("SELECT id FROM scan_pages WHERE id!=? ORDER BY id LIMIT 1", (first["scan_page_id"],)).fetchone()[0]
            db.execute("INSERT INTO scan_unlabeled_reads VALUES(?,?,1,?,?)", (first["run_id"], other, json.dumps({"version": "A"}), first["created_at"]))
            db.execute("INSERT INTO scan_unlabeled_registrations VALUES(?,?,?,?,?,?,?)", (other, first["run_id"], first["printed_copy_id"],
                       first["page_stamp_id"], first["enrollment_id"], "uin", first["created_at"]))
        alone, _ = decide(dict)
        prefetched = {"prefetch": {}}
        passed, passed_asked = decide(lambda: prefetched)
        self.assertEqual(passed, alone)
        self.assertEqual(len(registration_alone(passed_asked)), 1)
        self.assertEqual(sum(row is None for row in prefetched["prefetch"]["registrations"].values()), 1)

    def test_the_workflow_reads_every_paper_document_together_as_each_student_alone_reads_it(self):
        # Performance pass, 2026-10-02: the workflow counts take each paper's document from the read's prefetch (a selected paper of the
        # exam's print jobs) and from one read of the exam's current copies (a student without a selected paper); each must be what
        # `manual._paper_version` answers for that student alone, and a paper outside the exam's print jobs is still read alone.
        self.ready_batch(version_id=self.document_version("B"), enrollment_id=self.second)  # copies of two documents: no single one
        with connect(self.path) as db:
            plan = self.frq.plan(db, self.plan["id"])
            _, participants = self.frq.manual._context(db, plan["event_id"])
            first = next(p for p in participants if p["enrollment_id"] == self.enrollment)
            self.assertTrue(first["collection"]["assembly_revision_id"])
            people = [*participants, *({**p, "collection": {}} for p in participants),  # each student as if no paper were selected
                      {**first, "collection": {"assembly_revision_id": "no-such-paper"}}, {**first, "enrollment_id": "no-such-student", "collection": {}}]
            alone = [(p, self.frq.manual._paper_version(db, plan["event_id"], p)) for p in people]
            asked = []
            db.set_trace_callback(asked.append)
            together = self.frq.paper_documents(db, plan, people, {"prefetch": {}})
            db.set_trace_callback(None)
            self.assertEqual(together, alone)
            self.assertEqual(self.frq.paper_documents(db, plan, people, {}), alone)  # no prefetch asked for: each selected paper alone
        unselected = {p["enrollment_id"]: document for p, document in alone[len(participants):2 * len(participants)]}
        self.assertEqual(unselected, {self.enrollment: self.version_id, self.second: None})
        self.assertEqual([document for _, document in alone[-2:]], [None, None])
        self.assertEqual(len([sql for sql in asked if "WHERE ar.id=" in sql]), 1)  # only the paper outside the exam's print jobs
        self.assertEqual(len([sql for sql in asked if "c.enrollment_id=" in sql]), 0)

    def test_one_listing_of_every_response_answers_as_each_student_and_the_workflow_reads_alone(self):
        # Performance pass, 2026-10-02: a listing of every student's responses reads the exam's paper documents and registrations together
        # (`prefetch`) and keeps the questions it read for the workflow counts of the same read. Every answer is the one a listing of a
        # single student (no prefetch) and a workflow read with nothing shared give.
        self.seat(enrollment="A", second="B")
        self.two_versions()
        self.preview()
        self.compare_all()
        command, ready = self.preview()
        job = self.frq.create_job(self.plan["id"], {**command, "fingerprint": ready["fingerprint"]})
        for _ in range(60):
            if not self.frq.process_one():
                break
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        with self.frq.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, self.plan["id"])
            plan["template_setup"] = self.frq.template_setup(db, plan["id"])  # read here, so the count below is the workflow's own
            event = self.frq.exams._event(db, plan["event_id"])  # as the Grading index reads a plan
            participants = self.frq.exams._participants(db, event)
            collection = self.frq.exams._collection(db, event, participants)
            shared, quality = {}, self.frq.rubric_quality_status(db, plan["id"])
            every = self.frq._responses(db, plan, event, participants, shared=shared, quality=quality)
            self.assertIn("registrations", shared["prefetch"])
            self.assertEqual(set(shared["read_questions"]), {self.qa, self.qb})
            with patch.object(self.frq, "question", wraps=self.frq.question) as read:
                workflow = self.frq.workflow_summary(db, plan, every, context=(event, participants, collection), shared=shared, quality=quality)
            self.assertEqual(read.call_count, 0)  # both questions came from the listing
            self.assertEqual(workflow, self.frq.workflow_summary(db, dict(plan)))
            alone = []
            for person in self.frq.manual._context(db, plan["event_id"])[1]:
                memo = {}
                alone += self.frq._responses(db, plan, event, [person], shared=memo, student=(person["section_id"], person["enrollment_id"]))
                self.assertNotIn("prefetch", memo)
        self.assertEqual(len(every), 2)
        self.assertEqual(sorted(every, key=lambda r: r["id"]), sorted(alone, key=lambda r: r["id"]))

    def test_declared_versions_take_the_version_read_on_the_first_page_and_compare_no_blank(self):
        # An assignment made from a scan (requirement, 2026-09-24): no seating chart, two declared versions, no blank exam to compare.
        self.two_versions()
        with connect(self.path) as db:
            db.execute("INSERT INTO frq_declared_versions VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()), self.plan["id"], 2, "declared", "workspace", timestamp()))
        self.read_on_first_page(self.enrollment, "B")
        self.read_on_first_page(self.second, "E")
        self.enterContext(patch("grading.version_match.compare", side_effect=AssertionError("No blank exam is compared for declared versions.")))
        self.preview()
        self.compare_all()
        _, preview = self.preview()
        self.assertEqual(self.who(preview), [(self.qb, self.enrollment)])
        versions = self.frq.paper_versions(self.plan["id"])
        self.assertEqual((versions["declared"], versions["counts"]), (["A", "B"], {"confirmed": 1, "unresolved": 1}))
        [paper] = versions["waiting"]
        self.assertEqual((paper["enrollment_id"], paper["status"], paper["printed"]), (self.second, "unresolved", "E"))
        self.assertIn("Version E was read on this paper’s first page, but this assignment has Versions A and B.", paper["reason"])
        # The instructor's decision settles it, as for any paper.
        self.frq.confirm_paper_version(self.plan["id"], {**paper, "exam_version": "A", "confirm_version": True})
        self.assertEqual(self.who(self.preview()[1]), sorted([(self.qb, self.enrollment), (self.qa, self.second)]))

    def test_a_version_without_a_blank_exam_is_named_and_its_papers_wait(self):
        self.seat(enrollment="A", second="B")
        plan = self.frq.assignment(self.plan["id"])
        self.frq.assign_exam_version(plan["id"], {"version": plan["version"], "exam_version": "A", "question_ids": [self.qid]})
        self.qa, self.qb = self.question("q1", None, self.templates["A"], 1), self.question("q1-B", "B", None, 2)
        self.preview()
        self.compare_all()
        _, preview = self.preview()
        self.assertEqual(self.who(preview), [(self.qa, self.enrollment)])  # One blank exam: Version A's own mark still confirms its paper.
        self.assertTrue(all("Version B, which has no blank exam yet" in s["reason"] for s in preview["skipped"]), preview["skipped"])
        self.assertEqual(sorted(self.frq.assignment(self.plan["id"])["exam_versions"]["blanks"]), ["A"])

    def test_a_changed_decision_makes_earlier_work_stale_instead_of_regrading_it_silently(self):
        self.seat(enrollment="A", second="B")
        self.two_versions()
        self.preview()
        self.compare_all()
        command, ready = self.preview()
        job = self.frq.create_job(self.plan["id"], {**command, "fingerprint": ready["fingerprint"]})
        for _ in range(60):
            if not self.frq.process_one():
                break
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        participant = {"section_id": self.section["id"], "enrollment_id": self.second, "exam_version": "A", "confirm_version": True,
            "assembly_revision_id": next(e["source"]["assembly_revision_id"] for e in ready["eligible"] if e["enrollment_id"] == self.second)}
        self.frq.confirm_paper_version(self.plan["id"], participant)
        states = {r["enrollment_id"]: r["state"] for r in self.frq.responses(self.plan["id"], self.qb)}
        self.assertEqual(states, {self.second: "stale"})

    def test_identically_named_rubric_files_import_once_per_exam_version(self):
        self.seat(enrollment="A", second="B")
        source = encode(export_rows()).encode()
        upload = lambda **form: self.frq.upload_rubric(self.plan["id"], "1_Question_7.csv", source, {"request_id": str(uuid.uuid4()), **form})
        with self.assertRaisesRegex(ValueError, "Choose the exam version"):
            upload()  # The seating chart handed out A and B, and the file name cannot say which.
        with self.assertRaisesRegex(Conflict, "without an exam version"):
            upload(exam_version="B")  # q1 was imported before versions existed; say what it describes first.
        plan = self.frq.assignment(self.plan["id"])
        self.frq.assign_exam_version(plan["id"], {"version": plan["version"], "exam_version": "A", "question_ids": [self.qid]})
        first, second = upload(exam_version="A"), upload(exam_version="B")
        self.assertEqual((first["exam_version"], second["exam_version"], first["printed_number"], second["printed_number"]), ("A", "B", "7", "7"))
        self.assertNotEqual(first["question_id"], second["question_id"])
        with self.assertRaisesRegex(Conflict, "already exists for Version B"):
            self.frq.upload_rubric(self.plan["id"], "1_Question_7.csv", source + b"\n", {"request_id": str(uuid.uuid4()), "exam_version": "B"})
        questions = {(q["exam_version"], q["question_key"]) for q in self.frq.assignment(self.plan["id"])["questions"]}
        self.assertEqual(questions, {("A", "q1"), ("A", "q7-A"), ("B", "q7-B")})
        with self.assertRaises(ValueError):
            upload(exam_version="b")

    def test_an_exam_without_versions_is_graded_as_before(self):
        command, preview = self.preview([self.qid])
        self.assertEqual(len(preview["eligible"]), 2)
        self.assertNotIn("exam_version", preview["eligible"][0]["source"])
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"unversioned": 2})

    def read_first_pages(self, printed):
        """The local OCR as FIRST PAGE asks it: two boxes per paper, in the order the batch was taken. `printed`: enrollment -> letter."""
        def fields(image, boxes):
            with connect(self.path) as db:
                order = [r[0] for r in db.execute("""SELECT enrollment_id FROM frq_version_checks WHERE status='running'
                    AND json_extract(input_json,'$.matcher')=? ORDER BY updated_at,rowid""", (FIRST_PAGE,))]
            self.assertEqual(len(boxes), 2 * len(order))
            reads = []
            for student in order:
                letter = printed[student]
                # The title line, with a seat label beside it naming the SEAT's version, and the footer.
                reads += [{"text": f"Seat A1 · Version {self.seats.get(student, '-')}\nCommon Exam II Version {letter}, Fall 2026"}, {"text": f"Version {letter} -- Page 1 of 14"}]
            return {"reads": reads}
        from PIL import Image
        opened = Image.open
        def page(content, *args):  # the fixture's scanned pages are not pictures; a blank Letter page stands in
            try:
                return opened(content, *args)
            except OSError:
                return Image.new("L", (1700, 2200), 255)
        self.enterContext(patch("PIL.Image.open", side_effect=page))
        self.enterContext(patch("faculty_ocr.available", return_value=True))
        self.enterContext(patch("faculty_ocr.recognize_fields", side_effect=fields))

    def test_page_one_printing_the_seats_version_confirms_the_paper_without_comparing_it(self):
        """FIRST PAGE (requirement, 2026-10-25)."""
        self.seat(enrollment="A", second="B")
        self.two_versions()
        self.read_first_pages({self.enrollment: "A", self.second: "B"})
        self.enterContext(patch("grading.version_match.compare", side_effect=AssertionError("No whole paper is compared.")))
        _, waiting = self.preview()
        self.assertEqual(waiting["eligible"], [])
        self.assertTrue(all("still being confirmed from its first page" in s["reason"] for s in waiting["skipped"]), waiting["skipped"])
        self.assertTrue(self.frq.process_version_one())  # both first pages in one read
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})
        self.assertEqual(self.who(self.preview()[1]), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))
        with connect(self.path) as db:
            self.assertEqual(ExamVersionTests.progress(db, self.plan["id"]), {"left": 0, "done": 2, "failed": 0, "total": 2})

    def test_page_one_naming_another_version_compares_the_whole_paper(self):
        self.seat(enrollment="A", second="A")
        self.looks_like[self.second] = "B"  # Seated for A, but the student wrote the yellow exam, and page 1 says so.
        self.two_versions()
        self.read_first_pages({self.enrollment: "A", self.second: "B"})
        self.preview()
        self.compare_all()
        # Page 1 and the whole paper both say B: the booklet decides, and the paper is graded as B.
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})
        self.assertEqual(self.who(self.preview()[1]), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))
        # ... and its bubble sheet takes B too (BUBBLE-SHEETS "THE BOOKLET'S VERSION").
        with connect(self.path) as db:
            self.assertEqual(self.frq.booklet_versions(db, self.assignment["id"], self.term_id),
                             {(self.section["id"], self.enrollment): "A", (self.section["id"], self.second): "B"})

    def test_a_booklet_scanned_again_keeps_its_version_for_the_bubble_sheet(self):
        """BUBBLE-SHEETS › A BOOKLET SCANNED AGAIN (Exam 2, 2026-10-25: 164 rescanned booklets waited for assembly review, and 33
        sheets went back to their seat's key)."""
        self.seat(enrollment="A", second="A")
        self.looks_like[self.second] = "B"
        self.two_versions()
        self.read_first_pages({self.enrollment: "A", self.second: "B"})
        self.preview()
        self.compare_all()
        with connect(self.path) as db:
            copy = db.execute("""SELECT c.id FROM printed_copies c JOIN assembly_revisions ar ON ar.printed_copy_id=c.id
                WHERE c.enrollment_id=?""", (self.second,)).fetchone()[0]
            stamp = db.execute("SELECT token FROM page_stamps WHERE printed_copy_id=? AND page_number=1", (copy,)).fetchone()[0]
        job = self.inbox.get(self.inbox.submit({"action": "scan.receive"}, [{"filename": "rescan.pdf", "content": b"%PDF-fixture-rescan"}],
                                               source="email", sender="scanner", original=b"fixture mail", idempotency_key=str(uuid.uuid4()),
                                               authenticated=True, instructor_review_required=False)["id"])
        original = next(a for a in job["artifacts"] if a["role"] == "input")
        self.scans._retain_page(job, original, {"index": 1, "pdf": b"rescan-pdf-1", "png": b"rescan-png-1", "decoder": {}, "blank": False,
                                                "candidates": [{"payload": stamp}]})
        # The new page waits for assembly review, so the paper has no current revision and is not graded meanwhile ...
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 1})
        # ... but its booklet is still Version B, so its bubble sheet keeps B's key instead of going back to the seat's A.
        with connect(self.path) as db:
            self.assertEqual(self.frq.booklet_versions(db, self.assignment["id"], self.term_id),
                             {(self.section["id"], self.enrollment): "A", (self.section["id"], self.second): "B"})

    def test_page_one_and_the_pages_disagreeing_with_each_other_wait_for_the_instructor(self):
        self.seat(enrollment="A", second="A")
        self.looks_like[self.second] = "B"  # The pages match B, but page 1 reads C: nothing settles it.
        self.two_versions()
        self.read_first_pages({self.enrollment: "A", self.second: "C"})
        self.preview()
        self.compare_all()
        listing = self.frq.paper_versions(self.plan["id"])
        self.assertEqual(listing["counts"], {"confirmed": 1, "mismatch": 1})
        self.assertEqual((listing["waiting"][0]["enrollment_id"], listing["waiting"][0]["matched"]), (self.second, "B"))

    def test_a_paper_compared_before_page_one_was_read_is_read_before_it_waits(self):
        self.seat(enrollment="A", second="A")
        self.looks_like[self.second] = "B"
        self.two_versions()
        with patch.object(self.frq, "_first_page_input", return_value=None):  # as on the live catalog before FIRST PAGE
            self.preview()
            self.compare_all()
            self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 1, "mismatch": 1})
        self.read_first_pages({self.enrollment: "A", self.second: "B"})
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 1, "checking": 1})
        self.preview()
        self.compare_all()
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})

    def test_a_whole_paper_comparison_already_waiting_is_set_aside_once_page_one_agrees(self):
        self.seat(enrollment="A", second="B")
        self.two_versions()
        self.preview()  # queues each paper's first page
        with connect(self.path) as db:  # ... and, as before FIRST PAGE, each whole paper's comparison
            plan = self.frq.plan(db, self.plan["id"])
            blanks = self.frq.version_blanks(db, plan["id"])
            for row in db.execute("SELECT section_id,enrollment_id,assembly_revision_id FROM frq_version_checks").fetchall():
                value, now = self.frq._version_input(db, plan, row[2], blanks), timestamp()
                db.execute("INSERT INTO frq_version_checks VALUES(?,?,?,?,?,?,?,'pending',0,NULL,'',?,?)", (str(uuid.uuid4()), plan["id"], row[0], row[1], row[2],
                           digest(value), json.dumps(value), now, now))
        self.read_first_pages({self.enrollment: "A", self.second: "B"})
        self.enterContext(patch("grading.version_match.compare", side_effect=AssertionError("No whole paper is compared.")))
        self.compare_all()
        with connect(self.path) as db:
            self.assertEqual(sorted(tuple(r) for r in db.execute("SELECT status,error FROM frq_version_checks WHERE json_extract(input_json,'$.matcher')!=?", (FIRST_PAGE,))),
                             [("failed", NOT_NEEDED)] * 2)
            self.assertEqual(ExamVersionTests.progress(db, self.plan["id"]), {"left": 0, "done": 2, "failed": 0, "total": 2})
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})
        # Should the seat change, page 1 no longer agrees and the comparison set aside runs after all.
        with connect(self.path) as db:
            db.execute("UPDATE seating_exam_versions SET exam_version='A' WHERE exam_version='B'")
        self.seats[self.second] = "A"
        self.looks_like[self.second] = "B"
        compare = self.enterContext(patch("grading.version_match.compare", side_effect=self.compare))
        self.preview()
        self.compare_all()
        self.assertTrue(compare.called)  # page 1 (B) and the pages (B) agree against the new seat (A): the booklet decides
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})

    def jev_reads(self, printed):
        """JEV PAGE ONE: a stand-in for Jev's receipts that answers each paper's page 1 by its check's student (`printed`: enrollment ->
        letter, or None for a split answer); the local OCR is not installed here, so anything Jev leaves goes to the whole paper."""
        frq, path = self.frq, self.path

        class Jev:
            asked = []

            def ask(self, purpose, owner, state, questions, rule, images=()):
                with connect(path) as db:
                    student = db.execute("SELECT enrollment_id FROM frq_version_checks WHERE id=?", (owner,)).fetchone()[0]
                Jev.asked.append((purpose, rule, sorted(questions["version"]["criteria"])))
                letter = printed[student]
                first = f"Version {letter}" if letter else "Version A"
                second = f"Version {letter}" if letter else "Version B"
                return {"status": "complete", "id": "receipt-" + owner, "answers": {"version": {"choice": first, "confidence": 1.0},
                                                                                 "version__reversed": {"choice": second, "confidence": 1.0}}}
        frq.jev = Jev()
        self.enterContext(patch("jev.client.available", return_value=True))
        self.enterContext(patch("jev.client.prepare_image", side_effect=lambda content: content))
        return Jev

    def test_jev_reads_page_one_first_and_a_split_answer_falls_back(self):
        """JEV PAGE ONE (requirement, 2026-10-25)."""
        self.seat(enrollment="A", second="B")
        self.two_versions()
        asked = self.jev_reads({self.enrollment: "A", self.second: None})  # the second paper's two orders disagree
        self.preview()
        self.compare_all()
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})
        self.assertEqual(asked.asked[0][:2], ("paper-version", "jev-page-one-version-v1"))
        self.assertEqual(asked.asked[0][2], ["No version is printed", "Version A", "Version B"])
        with connect(self.path) as db:
            rows = {r[0]: (r[1], r[2], r[3]) for r in db.execute("""SELECT enrollment_id,status,json_extract(result_json,'$.reader'),
                COALESCE(json_extract(input_json,'$.matcher'),'') FROM frq_version_checks WHERE json_extract(input_json,'$.matcher')=?""", (FIRST_PAGE,))}
            compared = [r[0] for r in db.execute("SELECT enrollment_id FROM frq_version_checks WHERE json_extract(input_json,'$.matcher')!=? AND status='complete'", (FIRST_PAGE,))]
        self.assertEqual(rows[self.enrollment][:2], ("complete", "jev"))
        self.assertEqual(rows[self.second][0], "failed")  # no OCR here: the old way, the whole paper, decided it
        self.assertEqual(compared, [self.second])

    def test_jev_naming_another_version_than_the_seat_still_compares_the_whole_paper(self):
        self.seat(enrollment="A", second="A")
        self.looks_like[self.second] = "B"
        self.two_versions()
        self.jev_reads({self.enrollment: "A", self.second: "B"})
        self.preview()
        self.compare_all()
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})
        with connect(self.path) as db:
            self.assertEqual(self.frq.booklet_versions(db, self.assignment["id"], self.term_id)[(self.section["id"], self.second)], "B")

    def test_without_a_seat_jev_on_page_one_decides_when_the_pages_match_no_version_clearly(self):
        """JEV FALLBACK (requirement, 2026-10-26)."""
        self.seat(enrollment="A")  # the second paper has no seat, and its pages match no blank exam clearly
        self.two_versions()
        self.jev_reads({self.enrollment: "A", self.second: "B"})
        for _ in range(2):
            self.preview()
            self.compare_all()
        found = self.frq.paper_versions(self.plan["id"])
        self.assertEqual(found["counts"], {"confirmed": 2})
        self.assertEqual(self.who(self.preview()[1]), sorted([(self.qa, self.enrollment), (self.qb, self.second)]))

    def test_without_a_seat_a_split_jev_or_the_ocr_alone_leaves_an_unclear_paper_to_the_instructor(self):
        self.seat(enrollment="A")
        self.two_versions()
        self.jev_reads({self.enrollment: "A", self.second: None})  # Jev's two orders disagree; the OCR reads B, which alone settles nothing
        self.read_first_pages({self.enrollment: "A", self.second: "B"})
        for _ in range(2):
            self.preview()
            self.compare_all()
        listing = self.frq.paper_versions(self.plan["id"])
        self.assertEqual(listing["counts"], {"confirmed": 1, "unresolved": 1})
        self.assertEqual(listing["waiting"][0]["enrollment_id"], self.second)

    def test_without_a_seat_a_failed_comparison_takes_jevs_page_one(self):
        self.seat(enrollment="A")
        self.two_versions()
        self.jev_reads({self.enrollment: "A", self.second: "B"})
        compare = self.compare
        def failing(pages, blanks):
            if b"second" in next(iter(pages.values())):
                raise ValueError("Exam version comparison failed in the document sandbox.")
            return compare(pages, blanks)
        self.enterContext(patch("grading.version_match.compare", side_effect=failing))
        for _ in range(2):
            self.preview()
            self.compare_all()
        self.assertEqual(self.frq.paper_versions(self.plan["id"])["counts"], {"confirmed": 2})

    @staticmethod
    def progress(db, aid):
        from grading.exam_versions import ExamVersionsMixin
        return ExamVersionsMixin.version_check_progress(db, aid)


class DecisionTests(unittest.TestCase):
    def test_page_one_is_read_for_one_version_and_never_from_a_seat_label(self):
        self.assertEqual(first_page_version(["Common Exam II Version B, Fall 2026", "Version B -- Page 1 of 14"]), "B")
        self.assertEqual(first_page_version(["Seat B4 · Version B\nMcCarty, Katherine", ""]), "")  # the label says the seat's
        self.assertEqual(first_page_version(["Version B · Exam 2 · Section 508", "Version A -- Page 1 of 14"]), "A")
        self.assertEqual(first_page_version(["Common Exam II Version A", "Version B -- Page 1 of 14"]), "")  # two letters: none
        self.assertEqual(first_page_version(["Math 140 Common Exam II", ""]), "")

    def test_a_clear_lead_matches_and_anything_less_does_not(self):
        measured = lambda **means: {"versions": {k: {"pages": {}, "mean": v} for k, v in means.items()}}
        self.assertEqual(version_match.decide(measured(A=.84, B=.56))["matched"], "A")
        self.assertIsNone(version_match.decide(measured(A=.62, B=.56))["matched"])  # No clear lead.
        self.assertIsNone(version_match.decide(measured(A=.50, B=.30))["matched"])  # A lead, but the paper matches neither.
        # With one blank exam there is no lead, so the mark sits above what another version's paper reaches.
        self.assertEqual(version_match.decide(measured(A=.67))["matched"], "A")
        self.assertIsNone(version_match.decide(measured(A=.60))["matched"])
        self.assertIsNone(version_match.decide(measured(A=None))["matched"])

    def test_the_whole_paper_is_compared_in_the_actual_document_sandbox(self):
        """Synthetic pages: page 1 is identical in both versions, page 2 differs. One page cannot tell; the paper can."""
        import io
        from PIL import Image, ImageDraw

        def sheet(lines, handwriting=False):
            image = Image.new("L", (850, 1100), 255)
            draw = ImageDraw.Draw(image)
            draw.rectangle((60, 60, 790, 1040), outline=0, width=4)
            for index, line in enumerate(lines):
                draw.rectangle((100, 140 + index * 90, 100 + line, 170 + index * 90), fill=0)
            if handwriting:
                for y in range(200, 1000, 97):
                    draw.line((420, y, 700, y + 25), fill=60, width=3)
            data = io.BytesIO()
            image.save(data, "PNG")
            return data.getvalue()
        shared, only_a, only_b = [300, 500, 200, 420], [620, 120, 480, 260, 560, 90], [140, 560, 210, 640, 100, 380]
        blanks = {"A": {1: sheet(shared), 2: sheet(only_a)}, "B": {1: sheet(shared), 2: sheet(only_b)}}
        result = version_match.compare({1: sheet(shared, True), 2: sheet(only_b, True)}, blanks)
        self.assertAlmostEqual(result["versions"]["A"]["pages"]["1"], result["versions"]["B"]["pages"]["1"], places=3)
        self.assertGreater(result["versions"]["B"]["pages"]["2"], result["versions"]["A"]["pages"]["2"] + .2)
        self.assertEqual(version_match.decide(result)["matched"], "B")
        with self.assertRaises(ValueError):
            version_match.compare({1: b"not an image"}, blanks)


if __name__ == "__main__":
    unittest.main()
