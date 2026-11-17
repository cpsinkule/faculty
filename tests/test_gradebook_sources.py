"""Gradebook pulls from grading (gradebook/README.md "Pulling from grading"): which sources an assignment has, each student's
total by each source's own rule, and the preview a pull would apply.

Two real workspaces: the bubble sheet one of `test_bubble_keys` (four students in section 501, synthetic sheets read by the real
reader) and the graded FRQ one of `test_gradescope_send` (two students seated on Versions A and B, the instructor's approved
reviews), with the Exams page's manual grading of the same exam. The preview itself is pure and tested on its own.
"""
import copy
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

# The sibling fixtures below import each other by bare name; `manage_checks` puts tests/ on the path, a direct run may not.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app  # noqa: E402
from bubble_fixture import sheet  # noqa: E402
from gradebook import common, sources  # noqa: E402
from rosters import connect  # noqa: E402
from test_bubble_sheets import HEADERS, KEY, STUDENTS  # noqa: E402
from test_dr_exams import booking, workbook as dr_workbook  # noqa: E402
from test_rosters import COLUMNS, csv_bytes  # noqa: E402
import test_bubble_keys as bubble_keys_fixture  # noqa: E402
import test_exam_grading as exam_fixture  # noqa: E402
import test_gradescope_send as graded  # noqa: E402
from frq_fixtures import finalize_rubrics  # noqa: E402


def catalog_digest(path):
    """Every row of the catalog: a read must leave it byte for byte as it was."""
    with connect(path) as db:
        return hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest()


def term_rows(path, term_id):
    """The term's students as the gradebook service passes them."""
    with connect(path) as db:
        return [{"row": common.row_key(r["section_id"], r["enrollment_id"]), "section_id": r["section_id"], "enrollment_id": r["enrollment_id"],
                 "section": r["number"]} for r in db.execute("""SELECT m.section_id,m.enrollment_id,s.number FROM roster_memberships m
                 JOIN course_sections s ON s.id=m.section_id WHERE s.term_id=? ORDER BY s.number,m.enrollment_id""", (term_id,))]


def item(points="20", parts=("bubble",), sections=(), assignment_id="a1", item_id="item_1"):
    return {"id": item_id, "title": "Test 1", "category_id": None, "points": points, "due_date": "", "counts": True, "extra_credit": False,
            "archived": False, "sections": list(sections), "assignment_id": assignment_id, "link": {"kind": "grading", "parts": list(parts)}, "notes": ""}


def cell(status="scored", points=None, source="grading", cell_id="c1"):
    return {"id": cell_id, "status": status, "points": points, "note": "", "source": source, "actor": "workspace", "at": "2026-09-26T00:00:00Z"}


class PureTests(unittest.TestCase):
    """The pieces with no catalog: one part of one student, the combination, and the preview."""

    def test_an_frq_total_needs_every_expected_question_final_and_a_settled_version(self):
        settled = {"status": "confirmed", "version": "A"}
        expected = [("q1", 10), ("q2", 8)]
        finals = {"q1": {"score": 4.0, "max_score": 10}, "q2": {"score": 6.5, "max_score": 8}, "other": {"score": 99.0, "max_score": 99}}
        self.assertEqual(sources._frq_student(expected, finals, settled),
                         {"state": "complete", "earned": "10.5", "possible": "18", "detail": "2 of 2 questions final"})
        self.assertEqual(sources._frq_student(expected, {"q2": finals["q2"]}, settled)["detail"], "1 of 2 questions final")
        unresolved = {"status": "unresolved", "version": None, "reason": "Confirm whether it is Version A or Version B."}
        self.assertEqual(sources._frq_student(expected, finals, unresolved), {"state": "waiting", "earned": None, "possible": None, "detail": unresolved["reason"]})
        self.assertEqual(sources._frq_student(None, finals, settled)["state"], "waiting")
        self.assertEqual(sources._frq_student([], finals, {"status": "unversioned", "version": ""})["state"], "waiting")
        # Float scores are added exactly: 0.1 + 0.2 is 0.3, not 0.30000000000000004.
        self.assertEqual(sources._frq_student([("a", 1), ("b", 1)], {"a": {"score": 0.1, "max_score": 1}, "b": {"score": 0.2, "max_score": 1}}, settled)["earned"], "0.3")

    def test_a_bubble_total_is_exactly_one_scored_sheet(self):
        scored = {"status": "scored", "version": "A", "earned": "18.50", "possible": "20", "reason": ""}
        self.assertEqual(sources._bubble_student([scored]), {"state": "complete", "earned": "18.5", "possible": "20", "detail": "Version A sheet"})
        self.assertEqual(sources._bubble_student([]), {"state": "none", "earned": None, "possible": None, "detail": "no bubble sheet"})
        self.assertEqual(sources._bubble_student([scored, scored])["detail"], "two sheets claim this student")
        waiting = sources._bubble_student([{"status": "needs_review", "reason": "Question 3 is unclear.", "version": "", "earned": None, "possible": None}])
        self.assertEqual((waiting["state"], waiting["detail"]), ("waiting", "the sheet needs review: Question 3 is unclear."))
        self.assertEqual(sources._bubble_student([{"status": "no_key", "reason": "", "version": ""}])["detail"], "no answer key for the sheet's version")

    def test_a_student_is_complete_only_when_every_part_is(self):
        frq = {"state": "complete", "earned": "10", "possible": "18", "detail": ""}
        bubble = {"state": "complete", "earned": "7.5", "possible": "10", "detail": ""}
        both = sources._combine({"frq": frq, "bubble": bubble})
        self.assertEqual((both["state"], both["earned"], both["possible"], both["reason"]), ("complete", "17.5", "28", ""))
        none = {"state": "none", "earned": None, "possible": None, "detail": "no bubble sheet"}
        half = sources._combine({"frq": frq, "bubble": none}, dr=True)
        self.assertEqual((half["state"], half["earned"], half["reason"]), ("waiting", None, "Bubble sheet: no bubble sheet; DR-center booking for this exam"))
        nothing = sources._combine({"frq": {**none, "detail": "no graded paper"}, "bubble": none}, absent=True)
        self.assertEqual((nothing["state"], nothing["reason"]), ("none", "FRQ: no graded paper; Bubble sheet: no bubble sheet; marked absent on the Exams page"))
        self.assertEqual(sources._combine({"frq": frq}, excused=True)["reason"], "")  # a complete score needs no excuse
        self.assertEqual(sources._combine({})["state"], "none")

    def totals(self, **students):
        base = {"state": "none", "earned": None, "possible": None, "parts": {}, "reason": "no bubble sheet", "excused": False, "absent": False, "dr": False}
        return {"plan_id": "p1", "event_id": "e1", "parts": ["bubble"], "warnings": ["2 sheets are in scans still under Inbox review."],
                "students": {row: {**base, **value} for row, value in students.items()}}

    def test_a_cell_cleared_by_hand_is_kept_until_replace_is_ticked(self):
        # A grade the instructor took out (e.g. a mis-assigned paper) must not come back quietly with the next pull.
        students = [{"row": "s1:1000000001", "section_id": "s1", "section": 501, "name": "Student 1"},
                    {"row": "s1:1000000002", "section_id": "s1", "section": 501, "name": "Student 2"}]
        done = {"state": "complete", "earned": "18", "possible": "20", "reason": ""}
        totals = self.totals(**{"s1:1000000001": done, "s1:1000000002": done})
        cells = {"s1:1000000001": cell(status="cleared", source="instructor", cell_id="c1"),
                 "s1:1000000002": cell(status="cleared", source="grading", cell_id="c2")}
        options = {"replace": set(), "absent_as_missing": False}
        preview = sources.plan(item(), totals, students=students, cells=cells, options=options, revision=1)
        rows = {r["row"]: r for r in preview["rows"]}
        self.assertEqual((rows["s1:1000000001"]["action"], rows["s1:1000000002"]["action"]), ("kept", "add"))
        self.assertIn("You cleared this cell", rows["s1:1000000001"]["reason"])
        ticked = sources.plan(item(), totals, students=students, cells=cells, options={**options, "replace": {"s1:1000000001"}}, revision=1)
        self.assertEqual(next(r["action"] for r in ticked["rows"] if r["row"] == "s1:1000000001"), "add")
        self.assertEqual(ticked["fingerprint"], preview["fingerprint"])

    def test_the_preview_follows_the_one_cell_rule(self):
        students = [{"row": f"s1:10000000{n:02d}", "section_id": "s1", "section": 501, "name": f"Student {n}"} for n in range(10)] + \
                   [{"row": "s2:1000000099", "section_id": "s2", "section": 502, "name": "Other section"}]
        row = [s["row"] for s in students]
        done = lambda earned, possible="20": {"state": "complete", "earned": earned, "possible": possible, "reason": ""}
        totals = self.totals(**{
            row[0]: done("18"),                                            # empty cell: add
            row[1]: done("18"),                                            # already 18 from an earlier pull: same
            row[2]: done("18"),                                            # typed by hand: kept
            row[3]: done("18"),                                            # typed by hand, Replace ticked: update
            row[4]: done("17", "10"),                                      # an earlier import said 12: update; possible differs
            row[5]: {"state": "waiting", "reason": "the sheet needs review"},
            row[6]: {"state": "none", "reason": "no bubble sheet"},
            row[7]: {"state": "none", "reason": "no bubble sheet; marked excused on the Exams page", "excused": True},
            row[8]: {"state": "none", "reason": "no bubble sheet; marked absent on the Exams page", "absent": True},
            row[9]: {**done("20"), "excused": True, "absent": True},      # a complete score wins over any mark on the Exams page
            row[10]: done("5"),
        })
        cells = {row[1]: cell(points="18", cell_id="c1"), row[2]: cell(points="15", source="instructor", cell_id="c2"),
                 row[3]: cell(points="15", source="instructor", cell_id="c3"), row[4]: cell(points="12", source="import", cell_id="c4"),
                 row[8]: cell(status="cleared", cell_id="c8")}
        column = item(sections=["s1"])
        preview = sources.plan(column, totals, students=students, cells=cells, options={"replace": {row[3]}, "absent_as_missing": False}, revision=3)
        actions = {r["row"]: (r["action"], r["proposed"]) for r in preview["rows"]}
        self.assertNotIn(row[10], actions)  # the column applies to section s1 only
        scored = lambda points: {"status": "scored", "points": points}
        self.assertEqual([actions[r] for r in row[:10]], [
            ("add", scored("18")), ("same", scored("18")), ("kept", scored("18")), ("update", scored("18")), ("update", scored("17")),
            ("waiting", None), ("none", None), ("add", {"status": "excused", "points": None}), ("none", None), ("add", scored("20"))])
        self.assertEqual(preview["counts"], {"add": 3, "update": 2, "same": 1, "kept": 1, "waiting": 1, "none": 2})
        self.assertEqual(preview["warnings"], ["2 sheets are in scans still under Inbox review.",
                                               "1 student's possible points (10) differ from the column's 20 points; the points earned are recorded as they are."])
        kept = next(r for r in preview["rows"] if r["row"] == row[2])
        self.assertIn("Replace", kept["reason"])
        self.assertEqual((kept["current"]["id"], kept["name"], kept["section"]), ("c2", "Student 2", 501))
        self.assertEqual(next(r for r in preview["rows"] if r["row"] == row[5])["reason"], "the sheet needs review")
        self.assertEqual({k: preview[k] for k in ("item_id", "assignment_id", "plan_id", "parts", "points")},
                         {"item_id": "item_1", "assignment_id": "a1", "plan_id": "p1", "parts": ["bubble"], "points": "20"})

        # Absent may be recorded as missing only when the instructor ticks it; the fingerprint follows every proposed write.
        ticked = sources.plan(column, totals, students=students, cells=cells, options={"replace": {row[3]}, "absent_as_missing": True}, revision=3)
        absent = next(r for r in ticked["rows"] if r["row"] == row[8])
        self.assertEqual((absent["action"], absent["proposed"]), ("add", {"status": "missing", "points": None}))
        self.assertNotEqual(ticked["fingerprint"], preview["fingerprint"])
        again = sources.plan(copy.deepcopy(column), copy.deepcopy(totals), students=list(reversed(students)), cells=dict(cells),
                             options={"replace": {row[3]}, "absent_as_missing": False}, revision=3)
        self.assertEqual(again["fingerprint"], preview["fingerprint"])  # display order does not matter
        for changed in (dict(revision=4), dict(cells={**cells, row[0]: cell(points="1", cell_id="c0")}), dict(item=item(points="25", sections=["s1"])),
                        dict(cells={**cells, row[1]: cell(points="18", cell_id="c1-newer")})):
            arguments = {"item": column, "totals": totals, "students": students, "cells": cells,
                         "options": {"replace": {row[3]}, "absent_as_missing": False}, "revision": 3, **changed}
            self.assertNotEqual(sources.plan(arguments.pop("item"), arguments.pop("totals"), **arguments)["fingerprint"], preview["fingerprint"], changed)
        # Ticking Replace changes that row's action, never the fingerprint: the instructor ticks after the (slow) preview, and the apply
        # recomputes the plan with the ticks it is sent.
        for ticks in (set(), {row[2], row[3]}, {row[0], row[2], row[3], row[5]}):
            ticked = sources.plan(column, totals, students=students, cells=cells, options={"replace": ticks, "absent_as_missing": False}, revision=3)
            self.assertEqual(ticked["fingerprint"], preview["fingerprint"], ticks)
            self.assertEqual({r["row"]: r["action"] for r in ticked["rows"] if r["row"] in (row[2], row[3])},
                             {row[2]: "update" if row[2] in ticks else "kept", row[3]: "update" if row[3] in ticks else "kept"})
            self.assertEqual((ticked["counts"]["kept"], ticked["counts"]["update"]), (2 - len(ticks & {row[2], row[3]}), 1 + len(ticks & {row[2], row[3]})))
        # A reason-only difference (nothing to write changes) keeps the fingerprint.
        reworded = copy.deepcopy(totals)
        reworded["students"][row[5]]["reason"] = "1 of 2 questions final"
        self.assertEqual(sources.plan(column, reworded, students=students, cells=cells, options={"replace": {row[3]}, "absent_as_missing": False}, revision=3)["fingerprint"],
                         preview["fingerprint"])

    def test_the_maximum_is_one_paper_of_the_plan_by_each_part_of_its_own_setup(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""CREATE TABLE frq_assignment_questions(assignment_id,question_id,question_revision_id);
            CREATE TABLE frq_questions(id,assignment_version_id,exam_version); CREATE TABLE frq_question_revisions(id,content_json);
            CREATE TABLE frq_bubble_keys(assignment_id,revision,key_json);
            CREATE TABLE exam_grading_rubrics(event_id,assignment_version_id,rubric_id); CREATE TABLE exam_rubric_versions(id,questions_json);""")

        def question(qid, document, letter, points, plan="p1"):
            db.execute("INSERT INTO frq_questions VALUES(?,?,?)", (qid, document, letter))
            db.execute("INSERT INTO frq_question_revisions VALUES(?,?)", (f"r-{qid}", json.dumps({"max_points": points, "title": qid})))
            db.execute("INSERT INTO frq_assignment_questions VALUES(?,?,?)", (plan, qid, f"r-{qid}"))

        self.assertIsNone(sources._frq_maximum(db, "p1"))
        # One printed document with exam versions A and B: a shared question counts for both, each version adds its own.
        question("shared", "doc1", "", 4)
        question("a1", "doc1", "A", 10)
        question("a2", "doc1", "A", 8.5)
        question("b1", "doc1", "B", 20)
        # A second document without exam versions (a stamps-only makeup): all of its questions are one paper.
        question("m1", "doc2", "", 6)
        question("m2", "doc2", "", 7)
        self.assertEqual(sources._frq_maximum(db, "p1"), Decimal("24"))  # Version B: 4 + 20 (A is 22.5, the makeup 13)
        question("other", "doc9", "", 500, plan="p2")  # another plan's questions never count
        self.assertEqual(sources._frq_maximum(db, "p1"), Decimal("24"))
        question("broken", "doc1", "B", None)
        self.assertIsNone(sources._frq_maximum(db, "p1"))  # a question without a readable maximum: the paper's total is unknown

        self.assertIsNone(sources._bubble_maximum(db, "p1"))
        key = lambda revision, versions: db.execute("INSERT INTO frq_bubble_keys VALUES(?,?,?)", ("p1", revision, json.dumps(
            {"rule": "bubble-scoring-v2", "defaults": {"points": "2", "scoring": "exact"}, "versions": versions})))
        key(1, [{"letter": "A", "questions": []}])
        self.assertIsNone(sources._bubble_maximum(db, "p1"))  # a key without answers says nothing
        key(2, [{"letter": "A", "questions": [{"number": n, "answer": "A"} for n in range(1, 11)]},
                {"letter": "B", "questions": [{"number": 1, "answer": "B", "points": "5.25"}, {"number": 2, "answer": "C"}]}])
        self.assertEqual(sources._bubble_maximum(db, "p1"), Decimal("20"))  # A: ten at the default 2; B: 5.25 + 2
        key(3, [{"letter": "A", "questions": [{"number": 1, "answer": "A", "points": "30.5"}]}])
        self.assertEqual(sources._bubble_maximum(db, "p1"), Decimal("30.5"))  # the latest revision counts

        self.assertIsNone(sources._exams_maximum(db, "e1"))
        for version, rubric, points in (("doc1", "x1", [5, 10.5]), ("doc2", "x2", [15]), ("doc1", "x3", [99])):
            db.execute("INSERT INTO exam_rubric_versions VALUES(?,?)", (rubric, json.dumps([{"id": f"q{i}", "max_points": value} for i, value in enumerate(points)])))
            db.execute("INSERT INTO exam_grading_rubrics VALUES(?,?,?)", ("e1" if rubric != "x3" else "e2", version, rubric))
        self.assertEqual(sources._exams_maximum(db, "e1"), Decimal("15.5"))

        link = {"plan_id": "p1", "event_id": "e1", "parts": ["frq", "bubble", "exams"]}
        self.assertEqual(sources.maxima(db, link), {"bubble": Decimal("30.5"), "exams": Decimal("15.5")})  # FRQ is unknown now
        found = {"frq": Decimal("24"), "bubble": Decimal("30.5"), "exams": Decimal("15.5")}
        self.assertEqual(sources.total(found, ["frq"]), "24")
        self.assertEqual(sources.total(found, ["exams", "frq"]), "39.5")
        self.assertEqual(sources.total(found, ["frq", "bubble", "exams"]), "70")
        self.assertEqual(sources.total({"exams": Decimal("15.5")}, ["frq", "exams"]), "15.5")  # an unknown part adds nothing
        self.assertIsNone(sources.total({"exams": Decimal("15.5")}, ["frq", "bubble"]))
        self.assertIsNone(sources.total({}, ["frq"]))
        self.assertEqual(sources.maxima(db, {"plan_id": None, "event_id": "e1", "parts": ["exams"]}), {"exams": Decimal("15.5")})

    def test_a_deleted_scan_never_moves_when_bubble_grading_last_changed(self):
        """A scan deleted in the Inbox counts nowhere (AGENTS.md): its reads and reviews do not say grading changed, a restore
        brings them back, and only each scan's latest complete read counts, as the pull reads it."""
        db = sqlite3.connect(":memory:")
        db.executescript("""
            CREATE TABLE frq_bubble_keys (assignment_id TEXT, created_at TEXT);
            CREATE TABLE exam_events (id TEXT, assignment_id TEXT, term_id TEXT);
            CREATE TABLE bubble_runs (id TEXT, message_id TEXT, assignment_id TEXT, term_id TEXT, status TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE bubble_sheets (id TEXT, run_id TEXT);
            CREATE TABLE bubble_reviews (id INTEGER PRIMARY KEY, sheet_id TEXT, created_at TEXT);
            CREATE TABLE inbox_deletions (id INTEGER PRIMARY KEY, message_id TEXT, created_at TEXT, restored_at TEXT);
            INSERT INTO frq_bubble_keys VALUES ('p1', '2026-09-20T09:00:00+00:00');
            INSERT INTO exam_events VALUES ('e1', 'a1', 't1');
            INSERT INTO bubble_runs VALUES ('r-kept', 'm-kept', 'a1', 't1', 'complete', '2026-09-21T09:00:00+00:00', '2026-09-21T09:00:00+00:00');
            INSERT INTO bubble_runs VALUES ('r-old', 'm-kept', 'a1', 't1', 'complete', '2026-09-19T09:00:00+00:00', '2026-09-19T09:00:00+00:00');
            INSERT INTO bubble_sheets VALUES ('s-old', 'r-old');
            INSERT INTO bubble_reviews (sheet_id, created_at) VALUES ('s-old', '2026-09-25T09:00:00+00:00');
            INSERT INTO bubble_runs VALUES ('r-gone', 'm-gone', 'a1', 't1', 'complete', '2026-09-22T09:00:00+00:00', '2026-09-22T09:00:00+00:00');
            INSERT INTO bubble_sheets VALUES ('s-gone', 'r-gone');
            INSERT INTO bubble_reviews (sheet_id, created_at) VALUES ('s-gone', '2026-09-26T09:00:00+00:00');
            INSERT INTO inbox_deletions (message_id, created_at) VALUES ('m-gone', '2026-09-26T10:00:00+00:00');
        """)
        link = {"plan_id": "p1", "event_id": "e1"}
        # The kept scan's latest read (Sep 21); its superseded read's review (Sep 25) and the deleted scan (Sep 22, 26) do not count.
        self.assertEqual(sources.changed_at(db, link, ["bubble"]), "2026-09-21T09:00:00+00:00")
        db.execute("UPDATE inbox_deletions SET restored_at='2026-09-26T11:00:00+00:00'")
        self.assertEqual(sources.changed_at(db, link, ["bubble"]), "2026-09-26T09:00:00+00:00")


class BubbleSources(unittest.TestCase):
    """Four students in section 501: three sheets, then a key; flags from the Exams page and a DR booking."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.client = self.app.test_client()
        self.inbox, self.scans, self.bubbles, self.frq = (self.app.extensions[name] for name in ("inbox", "document_scans", "bubble_sheets", "frq_grading"))
        created = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [501, 502], "multiple_sections": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        state = self.client.get("/api/state").json
        rows = [["", student["name"], student["uin"], "Registered", "", "", "", "", "", "", "", f"s{index}@example.test"] for index, student in enumerate(STUDENTS)]
        self.assertEqual(self.client.post(f"/api/course-groups/{state['course_groups'][0]['id']}/sections/501/roster",
                                          data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS).status_code, 201)
        self.assignment = self.client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        self.path = self.app.config["DATABASE"]
        with connect(self.path) as db:
            self.term_id = db.execute("SELECT term_id FROM course_sections WHERE number=501").fetchone()[0]
            uins = dict(db.execute("SELECT uin,id FROM student_enrollments"))
            self.section = db.execute("SELECT id FROM course_sections WHERE number=501").fetchone()[0]
        self.row = {student["name"].split(",")[1].split()[0]: common.row_key(self.section, uins[student["uin"]]) for student in STUDENTS}

    read = bubble_keys_fixture.BubbleGradingTests.read
    clear = bubble_keys_fixture.BubbleGradingTests.clear

    def plan_id(self, message):
        with connect(self.path) as db:
            return self.frq.scan_status(db, message)["plan"]["id"]

    def totals(self, parts=("bubble",)):
        with self.frq.rosters.lock(), connect(self.path) as db:
            return sources.totals(self.frq, db, self.term_id, self.assignment["id"], list(parts), rows=term_rows(self.path, self.term_id))

    def available(self):
        with connect(self.path) as db:
            return sources.available(db, self.term_id)

    def maximum(self, parts=("bubble",)):
        with connect(self.path) as db:
            return sources.maximum(db, self.term_id, self.assignment["id"], list(parts))

    def save_key(self, plan_id, versions, base=None):
        answers = lambda key: [{"number": n, "answer": key[n]} for n in range(1, 11)]
        return self.frq.save_bubble_key(plan_id, {"request_id": str(uuid.uuid4()), "base_revision": base, "defaults": {"points": "2", "scoring": "exact"},
                                                  "versions": [{"letter": letter, "questions": answers(key) if key else []} for letter, key in versions]})

    def state(self, totals, name):
        student = totals["students"][self.row[name]]
        return student["state"], student["earned"], student["possible"], student["reason"]

    def test_sheets_scored_by_the_key_become_totals_and_nothing_is_written(self):
        self.assertEqual(self.available(), {})  # no exam of this assignment in the term yet
        nothing = self.totals()
        self.assertEqual((nothing["plan_id"], nothing["parts"], nothing["warnings"]),
                         (None, [], ["This assignment has no exam in this term, so there is no grading to pull from."]))
        self.assertEqual({s["state"] for s in nothing["students"].values()}, {"none"})

        other = {**KEY, 1: "A", 2: "AB"}
        message = self.read([sheet(KEY, "A"), sheet(other, "B"), sheet({**KEY, 3: ""}, "A")],
                            [("123456789", "Alice Example"), ("223456780", "Bob Example"), ("987650001", "Carol Nguyen")])
        plan_id = self.plan_id(message)
        found = self.available()[self.assignment["id"]]
        self.assertEqual((found["plan_id"], found["parts"]), (plan_id, []))  # sheets but no key: nothing to pull yet
        self.assertIn("no bubble sheet answer key", self.totals()["warnings"][0])

        self.assertIsNone(self.maximum())
        self.save_key(plan_id, [("A", KEY), ("B", other)])
        self.assertEqual(self.available()[self.assignment["id"]]["parts"], ["bubble"])
        self.assertEqual(self.maximum(), "20")  # ten questions at the key's default 2 points, per version
        waiting = self.totals()
        # Before the scan's Inbox review its sheets are only counted, as on the Bubble sheets page.
        self.assertEqual(waiting["warnings"], ["3 sheets are in scans still under Inbox review."])
        self.assertEqual(self.state(waiting, "Alice"), ("none", None, None, "no bubble sheet"))

        self.clear(message)
        before = catalog_digest(self.path)
        totals = self.totals()
        self.assertEqual(catalog_digest(self.path), before)
        self.assertEqual((totals["plan_id"], totals["parts"], totals["warnings"]), (plan_id, ["bubble"], []))
        self.assertEqual(self.state(totals, "Alice"), ("complete", "20", "20", ""))
        self.assertEqual(self.state(totals, "Bob"), ("complete", "20", "20", ""))  # Version B's own key
        self.assertEqual(self.state(totals, "Carol"), ("complete", "18", "20", ""))
        self.assertEqual(self.state(totals, "Dylan"), ("none", None, None, "no bubble sheet"))
        self.assertEqual(totals["students"][self.row["Carol"]]["parts"]["bubble"],
                         {"state": "complete", "earned": "18", "possible": "20", "detail": "Version A sheet"})
        # A part that is not available is left out and said so; asking for nothing known is refused.
        mixed = self.totals(("frq", "bubble", "exams"))
        self.assertEqual(mixed["parts"], ["bubble"])
        self.assertEqual(len(mixed["warnings"]), 2)
        self.assertEqual(self.state(mixed, "Carol")[:3], ("complete", "18", "20"))
        with self.assertRaises(ValueError):
            self.totals(("essays",))

        # Version B without answers: Bob's sheet waits for its key; the others are unchanged.
        self.save_key(plan_id, [("A", KEY), ("B", None)], base=1)
        self.assertEqual(self.state(self.totals(), "Bob")[:3], ("waiting", None, None))
        self.assertIn("Version B has no answers in the key yet", self.state(self.totals(), "Bob")[3])

        # A second scan still in the Inbox holds another sheet for Alice and Dylan's sheet: it is only counted, and Alice's first
        # sheet now waits for the instructor (the reader flags two sheets of the assignment for one student; nothing is guessed).
        self.read([sheet(KEY, "A"), sheet(KEY, "A")], [("555000111", "Dylan O'Brien"), ("123456789", "Alice Example")])
        again = self.totals()
        self.assertEqual(again["warnings"], ["2 sheets are in scans still under Inbox review."])
        self.assertEqual(self.state(again, "Dylan"), ("none", None, None, "no bubble sheet"))
        alice = self.state(again, "Alice")
        self.assertEqual(alice[:3], ("waiting", None, None))
        self.assertEqual(alice[3], "the sheet needs review: A sheet in another scan of this assignment is recorded for the same student.")
        self.assertEqual(self.state(again, "Carol")[:3], ("complete", "18", "20"))

    def test_exams_page_marks_and_dr_bookings_explain_a_missing_score(self):
        message = self.read([sheet(KEY, "A")], [("123456789", "Alice Example")])
        plan_id = self.plan_id(message)
        self.save_key(plan_id, [("A", KEY)])
        self.clear(message)
        event_id = self.available()[self.assignment["id"]]["event_id"]
        exams = self.frq.exams
        people = {p["enrollment_id"]: p for p in exams.get(event_id)["participants"]}
        enrollment = lambda name: self.row[name].split(":")[1]
        exams.update_participant(event_id, self.section, enrollment("Carol"), {"version": people[enrollment("Carol")]["version"], "expectation": "excused", "reason": "Illness"})
        exams.update_participant(event_id, self.section, enrollment("Dylan"), {"version": people[enrollment("Dylan")]["version"], "expectation": "absent", "reason": "No show"})
        exams.update_participant(event_id, self.section, enrollment("Alice"), {"version": people[enrollment("Alice")]["version"], "expectation": "absent", "reason": "Recorded by mistake"})
        # Bob sits the exam at the DR center: the booking is linked to this assignment by its name.
        content = dr_workbook([booking(Section="501", UIN="223456780", **{"Last Name": "Example", "First Name": "Bob", "Exam Type": "Test 1"})])
        preview = self.client.post("/api/accommodations/exams/preview", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None)}, headers=HEADERS).json
        submitted = self.client.post("/api/accommodations/exams/import", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None),
                                     "preview_hash": preview["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(submitted.status_code, 202, submitted.json)
        self.assertTrue(self.inbox.process_one())
        self.assertEqual(self.inbox.get(submitted.json["id"])["status"], "completed")

        totals = self.totals()
        flags = lambda name: tuple(totals["students"][self.row[name]][k] for k in ("state", "excused", "absent", "dr"))
        self.assertEqual([flags(name) for name in ("Alice", "Bob", "Carol", "Dylan")],
                         [("complete", False, True, False), ("none", False, False, True), ("none", True, False, False), ("none", False, True, False)])
        self.assertEqual(totals["students"][self.row["Bob"]]["reason"], "no bubble sheet; DR-center booking for this exam")
        self.assertEqual(totals["students"][self.row["Alice"]]["reason"], "")

        names = {row: name for name, row in self.row.items()}
        students = [{"row": r["row"], "section_id": r["section_id"], "section": r["section"], "name": names[r["row"]]} for r in term_rows(self.path, self.term_id)]
        column = item(assignment_id=self.assignment["id"])
        for ticked, dylan in ((False, ("none", None)), (True, ("add", {"status": "missing", "points": None}))):
            preview = sources.plan(column, totals, students=students, cells={}, options={"replace": set(), "absent_as_missing": ticked}, revision=1)
            actions = {r["name"]: (r["action"], r["proposed"]) for r in preview["rows"]}
            self.assertEqual(actions, {"Alice": ("add", {"status": "scored", "points": "20"}), "Bob": ("none", None),
                                       "Carol": ("add", {"status": "excused", "points": None}), "Dylan": dylan})


BUILT = {}


def tearDownModule():
    if BUILT:
        shutil.rmtree(BUILT.pop("template"), ignore_errors=True)


class Graded(graded.Fixture):
    """The FRQ workspace of `test_gradescope_send`, with one decision left open and the Exams page's grading of the same exam:
    the first student (Version A) has both questions final and a finalized Exams grade; the second (Version B) has one question
    final of two and one Exams question graded of two."""
    mark = exam_fixture.ExamGradingTests.mark
    data = exam_fixture.ExamGradingTests.data
    participant = exam_fixture.ExamGradingTests.participant
    complete = exam_fixture.ExamGradingTests.complete
    KEPT = graded.Fixture.KEPT + ("path",)

    def build(self):
        exam_fixture.ExamGradingTests.setUp(self)
        self.frq = self.app.extensions["frq_grading"]
        with connect(self.path) as db:
            self.second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? ORDER BY enrollment_id",
                                     (self.section["id"], self.enrollment)).fetchone()[0]
            self.term_id = db.execute("SELECT term_id FROM course_sections WHERE id=?", (self.section["id"],)).fetchone()[0]
            self.uins = dict(db.execute("SELECT id,uin FROM student_enrollments"))
        self.reviewed_source(batch=self.ready_batch(enrollment_id=self.second), changed="second")
        self.plan = self.frq.create_assignment({"event_id": self.event["id"]})
        self.templates = {letter: self.blank(letter) for letter in "AB"}
        self.looks_like = {}
        self.patches()
        self.seat(enrollment="A", second="B")
        self.q = {}
        for letter in "AB":
            self.q[("q20", letter)] = self.question(f"q20{'-B' if letter == 'B' else ''}", letter, 10, "20", 3, graded.Q20)
            self.q[("q21", letter)] = self.question(f"q21{'-B' if letter == 'B' else ''}", letter, 8, "21", 4, graded.Q21)
        finalize_rubrics(self.frq, self.plan["id"])
        for _ in range(10):
            if not self.frq.process_version_one():
                break
        self.grade_all()
        self.decide("q20", "A", ["R3_2"])        # 4 of 10
        self.decide("q21", "A", ["R4_2", "R4_6"])  # 6 of 8
        self.decide("q20", "B", ["R3_3"])        # 2 of 10; the second student's q21 is left for the instructor
        self.complete()  # the first student's Exams grade: 6 + 4 of 15, finalized
        other = next(p for p in self.grading.details(self.event["id"])["participants"] if p["enrollment_id"] == self.second)
        self.grading.save_mark(self.event["id"], {"section_id": self.section["id"], "enrollment_id": self.second, "version": other["version"],
                                                  "assembly_revision_id": other["assembly_revision_id"], "question_id": "q1", "score": 6,
                                                  "criterion_ids": ["method"], "feedback": ""})


class Workspace(Graded, unittest.TestCase):
    """Builds the graded workspace once for the module; it has no test of its own."""


class FrqSources(Graded, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BUILT:
            builder = Workspace("build")
            try:
                builder.build()
                BUILT["template"] = tempfile.mkdtemp()
                shutil.copytree(builder.directory.name, BUILT["template"], dirs_exist_ok=True)
                BUILT["kept"] = {name: getattr(builder, name) for name in cls.KEPT if name != "path"}
            finally:
                builder.doCleanups()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        shutil.copytree(BUILT["template"], self.directory.name, dirs_exist_ok=True)
        for name, value in copy.deepcopy(BUILT["kept"]).items():
            setattr(self, name, value)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.frq = self.app.extensions["frq_grading"]
        self.documents, self.scans = self.app.extensions["documents"], self.app.extensions["document_scans"]
        self.inbox, self.rosters = self.app.extensions["inbox"], self.app.extensions["rosters"]
        self.path, self.exams = self.frq.path, self.frq.exams
        self.patches()
        self.first, self.other = (common.row_key(self.section["id"], e) for e in (self.enrollment, self.second))

    def totals(self, parts):
        with self.frq.rosters.lock(), connect(self.path) as db:
            return sources.totals(self.frq, db, self.term_id, self.assignment["id"], list(parts), rows=term_rows(self.path, self.term_id))

    def test_available_names_the_plan_and_what_each_source_holds(self):
        with connect(self.path) as db:
            found = sources.available(db, self.term_id)
        self.assertEqual(found, {self.assignment["id"]: {"plan_id": self.plan["id"], "event_id": self.event["id"], "parts": ["frq", "exams"]}})

    def test_the_maximum_and_a_linked_column_take_one_paper_of_the_plan(self):
        with connect(self.path) as db:
            maximum = lambda parts: sources.maximum(db, self.term_id, self.assignment["id"], parts)
            # Two FRQ questions of 10 and 8 points on each version; the Exams page's rubric totals 15; no bubble key yet.
            self.assertEqual([maximum(["frq"]), maximum(["exams"]), maximum(["exams", "frq"]), maximum(["bubble"])], ["18", "15", "33", None])
            self.assertEqual(maximum(["frq", "bubble"]), "18")
            self.assertIsNone(sources.maximum(db, self.term_id, "not-an-assignment", ["frq"]))
        before = catalog_digest(self.path)
        gradebook = self.app.extensions["gradebook"]
        view = gradebook.view(self.term_id)
        self.assertEqual(catalog_digest(self.path), before)
        entry = next(entry for entry in view["assignments"] if entry["id"] == self.assignment["id"])
        self.assertEqual((entry["parts"], entry["maximum"]), (["frq", "exams"], "33"))
        linked = gradebook.link_assignment(self.term_id, {"assignment_id": self.assignment["id"], "request_id": "link-1"})
        column = next(item for item in linked["setup"]["items"] if item["id"] == linked["item_id"])
        self.assertEqual((column["points"], column["link"]), ("33", {"kind": "grading", "parts": ["frq", "exams"]}))
        self.assertEqual(linked["links"][linked["item_id"]]["maximum"], "33")
        # A key saved later: the column's points stay the instructor's, and the view offers the new total of its parts.
        self.frq.save_bubble_key(self.plan["id"], {"request_id": "k1", "base_revision": None, "defaults": {"points": "1", "scoring": "exact"},
                                                   "versions": [{"letter": "A", "questions": [{"number": 1, "answer": "B"}]}]})
        view = gradebook.view(self.term_id)
        entry = next(entry for entry in view["assignments"] if entry["id"] == self.assignment["id"])
        self.assertEqual((entry["maximum"], view["links"][linked["item_id"]]["maximum"]), ("34", "33"))
        self.assertEqual(next(item for item in view["setup"]["items"] if item["id"] == linked["item_id"])["points"], "33")

    def test_the_term_list_counts_graded_assignments_without_a_column(self):
        gradebook = self.app.extensions["gradebook"]
        term = lambda: next(entry for entry in gradebook.catalog()["terms"] if entry["id"] == self.term_id)
        with connect(self.path) as db:
            self.assertEqual(sources.graded_by_term(db), {self.term_id: {self.assignment["id"]}})
        before = catalog_digest(self.path)
        self.assertEqual(term()["graded_without_column"], 1)  # graded here, no column yet
        self.assertEqual(gradebook.view(self.term_id)["term"]["graded_without_column"], 1)
        self.assertEqual(catalog_digest(self.path), before)  # a read-only count
        linked = gradebook.link_assignment(self.term_id, {"assignment_id": self.assignment["id"], "request_id": "link-count"})
        self.assertEqual((term()["graded_without_column"], linked["term"]["graded_without_column"]), (0, 0))
        # An archived column no longer holds the assignment: it counts again.
        setup = {key: value for key, value in linked["setup"].items() if key not in ("revision", "created_at", "actor")}
        setup["items"] = [{**entry, "archived": True} for entry in setup["items"]]
        gradebook.save_setup(self.term_id, {"base_revision": linked["setup"]["revision"], "request_id": "archive-count", "setup": setup})
        self.assertEqual(term()["graded_without_column"], 1)
        self.assertEqual(sources.without_column({"a", "b"}, {"items": [{"assignment_id": "a"}, {"assignment_id": "b", "archived": True}]}), 1)
        self.assertEqual(sources.without_column((), None), 0)

    def test_a_linked_column_says_when_its_grading_last_changed(self):
        gradebook = self.app.extensions["gradebook"]
        with connect(self.path) as db:
            link = sources.available(db, self.term_id)[self.assignment["id"]]
            before = catalog_digest(self.path)
            frq, exams, bubble = (sources.changed_at(db, link, [part]) for part in ("frq", "exams", "bubble"))
            both = sources.changed_at(db, link, ["frq", "exams"])
            nothing = sources.changed_at(db, None, ["frq"])
        self.assertEqual(catalog_digest(self.path), before)  # catalog timestamps only: nothing is written
        self.assertIsNotNone(frq)  # the instructor's decisions of the FRQ questions
        self.assertIsNotNone(exams)  # the first student's finalized Exams grade
        self.assertIsNone(bubble)  # no answer key, no sheet
        self.assertIsNone(nothing)
        self.assertEqual(both, max(frq, exams, key=sources._instant))
        self.assertTrue(both.endswith("+00:00"), both)
        linked = gradebook.link_assignment(self.term_id, {"assignment_id": self.assignment["id"], "request_id": "link-changed"})
        self.assertEqual(linked["links"][linked["item_id"]]["grading_changed_at"], both)
        # A key saved later is a change of the bubble part, and so of a column pulling it.
        self.frq.save_bubble_key(self.plan["id"], {"request_id": "k-changed", "base_revision": None, "defaults": {"points": "1", "scoring": "exact"},
                                                   "versions": [{"letter": "A", "questions": [{"number": 1, "answer": "B"}]}]})
        with connect(self.path) as db:
            keyed = sources.changed_at(db, sources.available(db, self.term_id)[self.assignment["id"]], ["bubble"])
        self.assertIsNotNone(keyed)
        self.assertGreaterEqual(sources._instant(keyed), sources._instant(both))

    def test_frq_totals_count_only_complete_papers_of_final_grades(self):
        before = catalog_digest(self.path)
        totals = self.totals(["frq"])
        self.assertEqual(catalog_digest(self.path), before)  # a read of grading writes nothing, anywhere
        self.assertEqual((totals["plan_id"], totals["event_id"], totals["parts"], totals["warnings"]), (self.plan["id"], self.event["id"], ["frq"], []))
        first, other = totals["students"][self.first], totals["students"][self.other]
        self.assertEqual((first["state"], first["earned"], first["possible"], first["reason"]), ("complete", "10", "18", ""))
        self.assertEqual(first["parts"]["frq"]["detail"], "2 of 2 questions final")
        self.assertEqual((other["state"], other["earned"], other["reason"]), ("waiting", None, "1 of 2 questions final"))

    def test_exams_page_totals_and_combined_parts(self):
        before = catalog_digest(self.path)
        exams = self.totals(["exams"])
        self.assertEqual(catalog_digest(self.path), before)
        first, other = exams["students"][self.first], exams["students"][self.other]
        self.assertEqual((first["state"], first["earned"], first["possible"], first["parts"]["exams"]["detail"]), ("complete", "10", "15", "finalized"))
        self.assertEqual((other["state"], other["reason"]), ("waiting", "1 of 2 questions graded on the Exams page, not finalized"))
        both = self.totals(["exams", "frq"])  # order of the request does not matter
        self.assertEqual(both["parts"], ["frq", "exams"])
        first, other = both["students"][self.first], both["students"][self.other]
        self.assertEqual((first["state"], first["earned"], first["possible"]), ("complete", "20", "33"))
        self.assertEqual(other["reason"], "FRQ: 1 of 2 questions final; Exams page: 1 of 2 questions graded on the Exams page, not finalized")

        # With a bubble sheet key, bubble sheets join: none read yet, so no one is complete.
        self.frq.save_bubble_key(self.plan["id"], {"request_id": "k1", "base_revision": None, "defaults": {"points": "1", "scoring": "exact"},
                                                   "versions": [{"letter": "A", "questions": [{"number": 1, "answer": "B"}]}]})
        with connect(self.path) as db:
            self.assertEqual(sources.available(db, self.term_id)[self.assignment["id"]]["parts"], ["frq", "bubble", "exams"])
        mixed = self.totals(["frq", "bubble"])
        self.assertEqual((mixed["students"][self.first]["state"], mixed["students"][self.first]["reason"]), ("waiting", "Bubble sheet: no bubble sheet"))
        # The first student's sheet scored (the bubble view as the Bubble sheets page reads it): FRQ and bubble add up.
        view = {"waiting_scans": [], "sheets": [{"enrollment_id": self.enrollment, "section": 501, "status": "scored", "version": "A", "earned": "7.50",
                                                 "possible": "10", "reason": ""}]}
        with patch.object(self.frq, "_bubble_view", return_value=(None, view)):
            mixed = self.totals(["frq", "bubble"])
        first = mixed["students"][self.first]
        self.assertEqual((first["state"], first["earned"], first["possible"]), ("complete", "17.5", "28"))

        # The preview on real totals: the first student is added, the second waits with the reason.
        students = [{"row": r["row"], "section_id": r["section_id"], "section": r["section"], "name": r["row"]} for r in term_rows(self.path, self.term_id)]
        preview = sources.plan(item(points="18", parts=["frq"], assignment_id=self.assignment["id"]), self.totals(["frq"]), students=students, cells={},
                               options={"replace": set(), "absent_as_missing": False}, revision=1)
        self.assertEqual({r["row"]: (r["action"], r["proposed"], r["reason"]) for r in preview["rows"]},
                         {self.first: ("add", {"status": "scored", "points": "10"}, ""), self.other: ("waiting", None, "1 of 2 questions final")})
        self.assertEqual((preview["counts"]["add"], preview["counts"]["waiting"], preview["warnings"]), (1, 1, []))
        column = sources.plan(item(points="100", parts=["frq"], assignment_id=self.assignment["id"]), self.totals(["frq"]), students=students, cells={},
                              options={"replace": set(), "absent_as_missing": False}, revision=1)
        self.assertEqual(column["warnings"], ["1 student's possible points (18) differ from the column's 100 points; the points earned are recorded as they are."])

    def test_an_excuse_after_grading_is_proposed_instead_of_the_grades_it_made_stale(self):
        # Marking the first student excused on the Exams page changes their participant version, which grading's own rule treats
        # as a changed paper (`validate_task`): their grades are no longer final, and the preview proposes the excuse instead.
        exams = self.frq.exams
        person = next(p for p in exams.get(self.event["id"])["participants"] if p["enrollment_id"] == self.enrollment)
        exams.update_participant(self.event["id"], self.section["id"], self.enrollment, {"version": person["version"], "expectation": "excused", "reason": "Late add"})
        totals = self.totals(["frq"])
        first = totals["students"][self.first]
        self.assertEqual((first["state"], first["excused"], first["reason"]), ("waiting", True, "0 of 2 questions final; marked excused on the Exams page"))
        self.assertEqual(totals["warnings"], ["2 FRQ grades that looked final are not counted (for example: The reviewed paper changed after this was graded. "
                                              "The next batch grades it again.)"])
        students = [{"row": r["row"], "section_id": r["section_id"], "section": r["section"], "name": r["row"]} for r in term_rows(self.path, self.term_id)]
        preview = sources.plan(item(points="18", parts=["frq"], assignment_id=self.assignment["id"]), totals, students=students,
                               cells={self.first: cell(points="10", cell_id="c1")}, options={"replace": set(), "absent_as_missing": False}, revision=2)
        row = next(r for r in preview["rows"] if r["row"] == self.first)
        self.assertEqual((row["action"], row["proposed"], row["current"]["id"]), ("update", {"status": "excused", "points": None}, "c1"))


if __name__ == "__main__":
    unittest.main()
