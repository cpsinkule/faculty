"""Certification and Export, the sixth grading stage (requirement, 2026-09-26; GRADING.md "Certification and Export").

Part two (role A2, the second half): the service and its API on the same graded workspace (the page read, the drill-downs, the
UIN search, the certify preview and the certify itself with every correction of the critique: the preview's and the apply's gradebook
plans agree (C2), no roster payload is read inside the transaction (C3), information checks never move the fingerprint (C4), an
approved-unchanged grade reopens nothing (C7), a column linked to some parts takes only those (C10), a left-out student's earlier
certified cell is cleared (C11), and `exam_participants` is never written), and the gradebook's certified source.

Part one (this file's first half, role A1): migration 029's guarantees on a minimal catalog, the pure rules of
`grading/certification_rules.py` (statistics, the models' flags, the student classifier and its invariant, every check, the
snapshot, the fingerprint and the certify request), and on the real graded workspace of `test_gradescope_send` (two students on
Versions A and B, the instructor's approved reviews): the content digests, `certification_summary`'s states and the Gradescope
coverage detail against `_coverage`.
"""
import itertools
import json
import os
import sqlite3
import sys
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gradebook import sources  # noqa: E402
from grading import certification_rules as rules  # noqa: E402
from grading import gradescope_web as gw  # noqa: E402
from grading import progress  # noqa: E402
from grading.gradescope_sync import GradescopeSender  # noqa: E402
from grading.models import digest  # noqa: E402
from werkzeug.exceptions import Conflict  # noqa: E402
from rosters import connect  # noqa: E402
import test_gradescope_send as graded  # noqa: E402

MIGRATION = Path(__file__).resolve().parents[1] / "grading" / "migrations" / "029_certifications.sql"
TODAY = "2026-09-26"


def tearDownModule():
    graded.tearDownModule()


# ----- migration 029 ------------------------------------------------------------------------------------------------------------
class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""CREATE TABLE frq_assignments(id TEXT PRIMARY KEY, event_id TEXT);
            CREATE TABLE exam_participants(event_id TEXT, section_id TEXT, enrollment_id TEXT);
            CREATE TABLE roster_memberships(section_id TEXT, enrollment_id TEXT, PRIMARY KEY(section_id,enrollment_id));
            INSERT INTO frq_assignments VALUES('p1','e1'),('p2','e2');
            INSERT INTO roster_memberships VALUES('s1','1000000001'),('s1','1000000002'),('s2','1000000003');
            INSERT INTO exam_participants VALUES('e1','s1','1000000001'),('e1','s1','1000000002'),('e2','s2','1000000003');""")
        self.db.executescript(MIGRATION.read_text())

    def certify(self, cid, plan="p1", supersedes=None, request=None):
        self.db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
            participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,supersedes_id,actor,created_at)
            VALUES(?,?,?,'s','f','certify-v1','["frq"]','d','b','p','n','{}','[]','{}','{}','{}',?,'workspace','2026-09-26T00:00:00Z')""",
                        (cid, plan, request or cid, supersedes))

    def student(self, cid, enrollment="1000000001", section="s1", status="scored", reason="", earned="5", possible="10", dr_date=None, version=""):
        self.db.execute("""INSERT INTO frq_certification_students(certification_id,section_id,enrollment_id,status,reason,earned,possible,exam_version,parts_json,dr_date)
            VALUES(?,?,?,?,?,?,?,?,'{}',?)""", (cid, section, enrollment, status, reason, earned, possible, version, dr_date))

    def refused(self, call, text=None):
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            call()
        if text:
            self.assertIn(text, str(caught.exception))

    def test_a_certification_supersedes_exactly_the_latest_and_never_changes(self):
        self.certify("c1")
        self.refused(lambda: self.certify("c2"), "supersedes exactly")  # a second first certification
        self.certify("c2", supersedes="c1")
        self.refused(lambda: self.certify("c3", supersedes="c1"), "supersedes exactly")  # not the latest any more
        self.certify("d1", plan="p2")  # another plan's history is its own
        self.refused(lambda: self.certify("c3", supersedes="c2", request="c1"))  # a request ID is used once per plan
        self.refused(lambda: self.db.execute("UPDATE frq_certifications SET actor='x' WHERE id='c1'"), "immutable")
        self.refused(lambda: self.db.execute("DELETE FROM frq_certifications WHERE id='c1'"), "retained")

    def test_certified_grades_belong_to_participants_of_the_latest_certification(self):
        self.certify("c1")
        self.student("c1")
        self.refused(lambda: self.student("c1", enrollment="1000000003", section="s2"), "participant")  # another exam's student
        self.certify("c2", supersedes="c1")
        self.refused(lambda: self.student("c1", enrollment="1000000002"), "with its certification")  # never added to an older one
        self.student("c2", enrollment="1000000002")
        self.refused(lambda: self.db.execute("UPDATE frq_certification_students SET earned='9'"), "immutable")
        self.refused(lambda: self.db.execute("DELETE FROM frq_certification_students"), "retained")

    def test_each_status_carries_exactly_its_reasons_and_points(self):
        self.certify("c1")
        bad = [dict(status="zero", reason="no_paper", earned="1"),                 # a zero is 0
               dict(status="scored", reason="no_paper"),                          # a scored row has no zero reason
               dict(status="zero", reason="", earned="0"),                        # a zero says why
               dict(status="excused", reason="excused", earned="0"),              # the excused have no points
               dict(status="excused", reason="", earned=None, possible=None),
               dict(status="left_out", reason="dr_ahead", earned=None, possible=None),  # a DR student left out has a date
               dict(status="left_out", reason="dr_ahead", earned=None, possible=None, dr_date="Oct 2"),
               dict(status="scored", reason="", earned=None),
               dict(status="scored", version="AB")]
        for values in bad:
            self.refused(lambda: self.student("c1", **values))
        for enrollment, values in (("1000000001", dict(status="zero", reason="absent", earned="0", possible="10")),
                                   ("1000000002", dict(status="left_out", reason="dr_ahead", earned=None, possible=None, dr_date="2026-10-02"))):
            self.student("c1", enrollment=enrollment, **values)


# ----- pure rules ----------------------------------------------------------------------------------------------------------------
class StatisticsTests(unittest.TestCase):
    def test_mean_median_sd_for_every_size(self):
        empty = rules.stats([])
        self.assertEqual((empty["n"], empty["mean"], empty["median"], empty["sd"]), (0, None, None, None))
        one = rules.stats([("7", "10")])
        self.assertEqual((one["n"], one["mean"], one["median"], one["sd"], one["min"], one["max"]), (1, 7.0, 7.0, None, 7.0, 7.0))
        two = rules.stats([(4, 10), (6, 10)])
        self.assertEqual((two["mean"], two["median"], two["sd"]), (5.0, 5.0, 1.41))
        odd = rules.stats([(1, 10), (2, 10), (9, 10)])
        self.assertEqual((odd["median"], odd["mean"]), (2.0, 4.0))
        even = rules.stats([(1, 10), (2, 10), (3, 10), (10, 10)])
        self.assertEqual(even["median"], 2.5)
        self.assertEqual(rules.stats([(0.1, 1), (0.2, 1)])["mean"], 0.15)  # exact decimals, not floats
        self.assertEqual(rules.stats([(1, None), (None, 5)])["n"], 1)  # no earned points: not counted

    def test_histogram_edges(self):
        bins = rules.stats([(0, 10), (10, 10), (9.99, 10), (1, 10), (11, 10), (-1, 10)])["histogram"]
        self.assertEqual(len(bins), 10)
        self.assertEqual((bins[0]["from"], bins[0]["to"], bins[9]["from"], bins[9]["to"]), (0, 10, 90, 100))
        self.assertEqual([b["count"] for b in bins], [2, 1, 0, 0, 0, 0, 0, 0, 0, 3])  # 100% and above in the last, below 0 in the first
        # Each value is binned by its own possible: 8 of 10 and 16 of 20 are the same 80%.
        self.assertEqual(rules.stats([(8, 10), (16, 20)])["histogram"][8]["count"], 2)

    def test_totals_count_only_complete_students_and_split_by_version_and_section(self):
        students = [{"status": "scored", "reason": "", "earned": "80", "possible": "100", "exam_version": "A", "section": 507},
                    {"status": "scored", "reason": "", "earned": "60", "possible": "100", "exam_version": "B", "section": 507},
                    {"status": "scored", "reason": "missing_part", "earned": "30", "possible": "100", "exam_version": "A", "section": 508},
                    {"status": "zero", "reason": "no_paper", "earned": "0", "possible": "100"},
                    {"status": "excused", "reason": "excused"}, {"status": "left_out", "reason": "dr_ahead"}, {"status": "blocked"}]
        total = rules.total_stats(students)
        self.assertEqual((total["n"], total["mean"]), (2, 70.0))
        self.assertEqual(total["excluded"], {"zero": 1, "missing_part": 1, "missing": 1, "left_out": 1, "excused": 1})
        self.assertEqual({k: v["n"] for k, v in rules.grouped(students, "exam_version").items()}, {"A": 2, "B": 1})
        self.assertEqual(list(rules.grouped(students, "section")), ["507", "508"])

    def test_score_histograms_and_line_frequencies(self):
        self.assertEqual(rules.score_histogram([3, "3", 0.5, None, 0]), [{"score": "0", "count": 1}, {"score": "0.5", "count": 1}, {"score": "3", "count": 2}])
        items = [{"id": "R1"}, {"id": "R2"}, {"id": "R3"}]
        self.assertEqual(rules.line_frequencies([["R1"], ["R1", "R2"], [], ["R1", "R1"]], items),
                         [{"id": "R1", "count": 3, "pct": 75.0}, {"id": "R2", "count": 1, "pct": 25.0}, {"id": "R3", "count": 0, "pct": 0.0}])
        self.assertEqual(rules.line_frequencies([], items)[0]["pct"], None)


class FlagTests(unittest.TestCase):
    def test_original_flags_come_from_the_result_and_survive_finalization(self):
        self.assertEqual(rules.flags({"state": "proposed", "base_state": "proposed"}), [])
        self.assertEqual(rules.flags({"state": "both"}), ["reading", "grading"])
        # The finalization raised a proposal the models had not flagged.
        self.assertEqual(rules.flags({"state": "grading_review", "base_state": "proposed"}), ["finalization"])
        self.assertEqual(rules.flags({"state": "proposed", "reasons": 2, "arbiter": "adopted", "flag_review": "finalized", "committee": "unresolved",
                                      "risk_blocks": 0, "risk_individual": 1, "risk_waived": 1}),
                         ["reasons", "arbitrator", "flag_reviewer", "disagreement", "risk", "waived"])
        self.assertEqual(rules.flags({"state": "proposed", "arbiter": "not_asked", "flag_review": "not_asked", "committee": "resolved"}), [])

    def test_a_grade_is_settled_only_when_a_reviewing_model_ruled_on_every_flag(self):
        settled = lambda **row: rules.settled({**row, "flags": rules.flags({"state": "proposed", **row})})
        self.assertTrue(settled(arbiter="adopted"))
        self.assertTrue(settled(arbiter="revised"))
        self.assertTrue(settled(flag_review="finalized"))
        self.assertTrue(settled(arbiter="adopted", flag_review="finalized"))
        for outcome in ("unreliable", "unsettled", "failed"):
            self.assertFalse(settled(arbiter=outcome), outcome)
        for outcome in ("manual_review", "unsettled", "failed"):
            self.assertFalse(settled(flag_review=outcome), outcome)
        self.assertFalse(settled(arbiter="adopted", risk_blocks=1))  # a flag no model settled stays
        self.assertFalse(settled(flag_review="finalized", committee="unresolved"))
        self.assertFalse(settled())  # nothing flagged is nothing settled
        question = {"question_id": "q1", "title": "Question 2", "version": "A", "max": 3}
        row = rules.question_row(question, [{"final": True, "score": 3, "kind": "automatic", "flags": ["arbitrator"], "settled": True},
                                            {"final": True, "score": 1, "kind": "automatic", "flags": ["flag_reviewer"], "settled": True},
                                            {"final": True, "score": 2, "kind": "review", "flags": ["arbitrator", "risk"], "settled": False}])
        self.assertEqual((row["flagged"], row["settled"]), (1, 2))
        counts = rules.page_counts([{"row": "a", "view": "complete", "flagged": 1, "settled": 2}, {"row": "b", "view": "complete", "flagged": 0, "settled": 5}])
        self.assertEqual((counts["flagged"], counts["settled"]), (1, 7))

    def test_basis_names_arbitrator_and_flag_reviewer_grades(self):
        self.assertEqual(rules.basis("team_consensus", {"arbiter": "revised"}), "final_arbitrator")
        self.assertEqual(rules.basis("team_consensus", {"flag_review": "finalized"}), "flag_review")
        self.assertEqual(rules.basis("team_consensus", {}), "team_consensus")
        self.assertEqual(rules.basis("benchmark", {"arbiter": "adopted"}), "benchmark")

    def test_an_override_is_a_manual_score_or_different_lines_or_adjustments(self):
        self.assertTrue(rules.overridden("manual", ["R1"], [], ["R1"], []))
        self.assertFalse(rules.overridden("approved", ["R2", "R1"], [], ["R1", "R2"], []))
        self.assertTrue(rules.overridden("approved", ["R1"], [], ["R2"], []))
        self.assertTrue(rules.overridden("approved", ["R1"], [{"points": -1, "reason": "x"}], ["R1"], []))
        self.assertFalse(rules.overridden("approved", ["R1"], [{"points": -1.0, "reason": "x"}], ["R1"], [{"points": -1, "reason": "y"}]))
        self.assertFalse(rules.overridden("draft", ["R1"], [], ["R2"], []))

    def test_a_question_row(self):
        question = {"question_id": "q1", "title": "Question 20", "printed_number": "20", "version": "A", "max": 10}
        responses = [{"final": True, "score": 10, "kind": "automatic", "basis": "team_consensus", "flags": [], "reviewed": False},
                     {"final": True, "score": 0, "kind": "automatic", "basis": "final_arbitrator", "flags": ["arbitrator"], "reviewed": True},
                     {"final": True, "score": 4, "kind": "review", "flags": ["grading"], "overridden": True},
                     {"final": False, "score": None, "kind": None, "flags": ["reading"]}]
        row = rules.question_row(question, responses, {"sent": 1, "same_content": 1, "changed": 0, "not_sent": 1, "unsendable": None})
        self.assertEqual({k: row[k] for k in ("n_final", "not_final", "full", "zero", "automatic", "instructor", "automatic_unchecked",
                                             "flagged_finalized", "flagged", "overridden", "pct_full", "max")},
                         {"n_final": 3, "not_final": 1, "full": 1, "zero": 1, "automatic": 2, "instructor": 1, "automatic_unchecked": 1,
                          "flagged_finalized": 1, "flagged": 3, "overridden": 1, "pct_full": 33.33, "max": "10"})
        self.assertEqual(row["basis"], {"team_consensus": 1, "final_arbitrator": 1})
        self.assertEqual(row["gradescope"], {"sent": 2, "changed": 0, "not_sent": 1, "unsendable": None})


def complete(earned, possible):
    return {"state": "complete", "earned": str(earned), "possible": str(possible), "detail": ""}


NONE = {"state": "none", "earned": None, "possible": None, "detail": "no graded paper"}
WAITING = {"state": "waiting", "earned": None, "possible": None, "detail": "1 of 2 questions final"}
MAXIMA = {"frq": Decimal(40), "bubble": Decimal(60)}


class ClassifierTests(unittest.TestCase):
    def classify(self, certified=("frq", "bubble"), **student):
        base = {"row": "s1:1000000001", "expectation": "expected", "collection": "complete", "sheets": 1, "dr": [], "duplicate": None,
                "parts": {"frq": complete(30, 40), "bubble": complete(50, 60)}}
        return rules.classify({**base, **student}, list(certified), TODAY, MAXIMA)

    def test_every_part_complete_is_scored(self):
        found = self.classify()
        self.assertEqual((found["status"], found["reason"], found["earned"], found["possible"], found["view"]), ("scored", "", "80", "100", "complete"))

    def test_no_work_at_all_is_a_zero_to_confirm_and_never_for_the_excused(self):
        nothing = dict(collection="no_scan", sheets=0, parts={"frq": NONE, "bubble": NONE})
        found = self.classify(**nothing)
        self.assertEqual((found["status"], found["reason"], found["earned"], found["possible"], found["detail"]),
                         ("zero", "no_paper", "0", "100", "No paper scanned"))
        self.assertEqual(self.classify(**{**nothing, "expectation": "absent", "collection": "absent"})["reason"], "absent")
        excused = self.classify(**{**nothing, "expectation": "excused", "collection": "excused"})
        self.assertEqual((excused["status"], excused["reason"], excused["earned"]), ("excused", "excused", None))
        # A complete score of an excused student is still not certified silently: their paper conflicts with the excuse.
        self.assertEqual(self.classify(expectation="excused")["block"], "excused_with_paper")
        self.assertEqual(self.classify(expectation="absent", collection="absent", parts={"frq": NONE, "bubble": complete(1, 60)})["block"], "excused_with_sheet")

    def test_dr_students_ahead_are_left_out_and_past_ones_are_zeros(self):
        nothing = dict(collection="no_scan", sheets=0, parts={"frq": NONE, "bubble": NONE})
        for date in ("2026-09-27", TODAY):
            found = self.classify(**nothing, dr=[{"date": "2026-09-01"}, {"date": date}])
            self.assertEqual((found["status"], found["reason"], found["dr_date"]), ("left_out", "dr_ahead", date))  # the LATEST booking counts
        past = self.classify(**nothing, dr=[{"date": "2026-09-25"}])
        self.assertEqual((past["status"], past["reason"], past["detail"]), ("zero", "no_paper", "DR exam was 2026-09-25; no paper found"))

    def test_a_makeup_owed_leaves_a_student_without_work_out_and_a_dr_exam_ahead_comes_first(self):
        # MAKEUP OWED (requirement, 2026-10-07): an excused or undecided absence over the day it was given is never a 0 while it waits.
        nothing = dict(collection="no_scan", sheets=0, parts={"frq": NONE, "bubble": NONE})
        owed = {"absence_id": "a1", "status": "excused", "state": "owed", "dates": "Fri, Oct 2", "given_date": "2026-10-02"}
        found = self.classify(**nothing, makeup=owed)
        self.assertEqual((found["status"], found["reason"], found["view"], found["detail"], found["earned"], found["possible"]),
                         ("left_out", "makeup", "makeup", "Makeup owed · absence Fri, Oct 2 excused", None, None))
        pending = self.classify(**nothing, makeup={**owed, "status": "pending", "state": "if_excused"})
        self.assertEqual((pending["reason"], pending["detail"]), ("makeup", "Absence Fri, Oct 2 waiting for your decision · 0 if not excused"))
        self.assertEqual(self.classify(**nothing, makeup=owed, dr=[{"date": "2026-12-01"}])["reason"], "dr_ahead")
        self.assertEqual(self.classify(**{**nothing, "expectation": "excused", "collection": "excused"}, makeup=owed)["status"], "excused")
        self.assertEqual(self.classify(makeup=owed)["status"], "scored")  # their paper came back: graded like anyone's
        self.assertEqual(self.classify(**nothing)["status"], "zero")  # nothing claimed: the 0 to confirm

    def test_a_missing_part_counts_zero_only_where_it_has_no_work(self):
        found = self.classify(sheets=0, parts={"frq": complete(30, 40), "bubble": {**NONE, "detail": "no bubble sheet"}})
        self.assertEqual((found["status"], found["reason"], found["missing_parts"], found["earned"], found["possible"]),
                         ("scored", "missing_part", ["bubble"], "30", "100"))
        sheet_only = self.classify(collection="no_scan", parts={"frq": NONE, "bubble": complete(50, 60)})
        self.assertEqual((sheet_only["reason"], sheet_only["missing_parts"], sheet_only["possible"]), ("missing_part", ["frq"], "100"))
        # A paper that arrived but is not graded yet is work: never a zero, a block names the student.
        waiting = self.classify(collection="complete", sheets=0, parts={"frq": NONE, "bubble": complete(50, 60)})
        self.assertEqual((waiting["status"], waiting["block"]), ("blocked", "student_incomplete"))
        self.assertIn("Free response", waiting["detail"])

    def test_duplicate_memberships(self):
        nothing = dict(collection="no_scan", sheets=0, parts={"frq": NONE, "bubble": NONE})
        found = self.classify(**nothing, duplicate={"other_has_work": True})
        self.assertEqual((found["status"], found["reason"]), ("left_out", "duplicate_membership"))
        self.assertEqual(self.classify(**nothing, duplicate={"other_has_work": False})["status"], "zero")

    def test_the_invariant_every_participant_is_one_status_or_a_block_names_them(self):
        states = [complete(3, 4), NONE, WAITING]
        for expectation, collection, sheets, frq, bubble, dr, duplicate, makeup in itertools.product(
                ("expected", "absent", "excused"), ("complete", "partial", "needs_review", "missing_pages", "no_scan", "absent", "excused"),
                (0, 1), states, states, ([], [{"date": "2026-09-01"}], [{"date": "2026-12-01"}]), (None, {"other_has_work": True}),
                (None, {"absence_id": "a1", "status": "pending", "dates": "Fri, Sep 25"})):
            found = self.classify(expectation=expectation, collection=collection, sheets=sheets, parts={"frq": frq, "bubble": bubble}, dr=dr, duplicate=duplicate,
                                  makeup=makeup)
            if found["status"] == "blocked":
                self.assertIn(found["block"], ("student_incomplete", "excused_with_paper", "excused_with_sheet"))
            else:
                self.assertIn(found["status"], ("scored", "zero", "excused", "left_out"))
                self.assertIsNone(found["block"])
                # Never a zero or a zero part for work that exists.
                if found["status"] == "zero":
                    self.assertTrue(collection not in rules.WORK and not sheets and frq is NONE and bubble is NONE)
                if found["reason"] == "missing_part":
                    for name in found["missing_parts"]:
                        self.assertFalse(rules.part_work(name, {"frq": frq, "bubble": bubble}[name], collection, sheets))
                if expectation == "excused" or makeup:
                    self.assertNotEqual(found["status"], "zero")

    def test_one_part_assignments(self):
        found = rules.classify({"row": "r", "collection": "no_scan", "sheets": 0, "parts": {"frq": NONE}}, ["frq"], TODAY, {"frq": Decimal(18)})
        self.assertEqual((found["status"], found["possible"]), ("zero", "18"))
        self.assertEqual(rules.classify({"row": "r", "collection": "complete", "parts": {"frq": WAITING}}, ["frq"], TODAY)["block"], "student_incomplete")
        self.assertEqual(rules.classify({"row": "r", "collection": "no_scan"}, [], TODAY)["block"], "student_incomplete")


def student(row, status, reason="", **extra):
    return {"row": row, "name": f"Student {row[-2:]}", "section": 507, "status": status, "reason": reason, "block": None, "earned": "0",
            "possible": "100", "detail": "", "view": "complete", **extra}


class CheckTests(unittest.TestCase):
    def facts(self, **extra):
        base = {"plan_id": "p1", "event_id": "e1", "parts": ["frq", "bubble"], "students": [student("s1:1000000001", "scored", earned="80")],
                "work": {"states": {"approved": 3, "auto_accepted": 10}, "new": 0, "failed": 0, "pending_tasks": 0}}
        return {**base, **extra}

    def ids(self, checks, level=None):
        return [c["id"] for c in checks if level is None or c["level"] == level]

    def test_all_final_blocks_nothing(self):
        self.assertEqual(self.ids(rules.build_checks(self.facts()), "block"), [])

    def test_unfinished_grading_blocks(self):
        for states in ({"proposed": 1}, {"draft": 1}, {"stale": 2}, {"grading_review": 1}, {"trial_accepted": 1}, {"pending": 1}):
            checks = rules.build_checks(self.facts(work={"states": {"approved": 3, **states}, "new": 0, "failed": 0}))
            self.assertEqual(self.ids(checks, "block"), ["grading_unfinished"], states)
        new = rules.build_checks(self.facts(work={"states": {}, "new": 2, "failed": 1, "pending_tasks": 1}))
        blocked = next(c for c in new if c["id"] == "grading_unfinished")
        self.assertEqual((blocked["count"], blocked["action"]["href"]), (3, "#grading/p1?tab=jobs"))

    def test_unreadable_and_rejected_are_a_block_never_a_zero(self):
        checks = rules.build_checks(self.facts(no_grade=[{"row": "s1:1000000001", "name": "A", "section": 507, "question": "Question 20",
                                                           "status": "unreadable", "response_id": "r1"}], work={"states": {"unreadable": 1}}))
        self.assertIn("no_grade_recorded", self.ids(checks, "block"))
        self.assertNotIn("grading_unfinished", self.ids(checks))
        self.assertNotIn("zero_questions", self.ids(checks))  # critique C1: no zero_questions confirmation at all

    def test_each_block(self):
        facts = self.facts(
            versions=[{"row": "s1:1000000002", "name": "B", "section": 507, "detail": "Confirm whether it is Version A or B."}],
            inbox={"awaiting": 1, "href": "#inbox/job/j1"},
            papers_in_review=[{"row": "s1:1000000003", "name": "C", "section": 507, "detail": "Several copies"}],
            bubble={"part": True, "has_key": True, "sheets": [{"status": "needs_review", "reason": "Question 3", "row": None}], "beyond_read": [41]},
            skipped=[{"response_id": "r9", "reason": "suspended", "row": "s1:1000000001"}],
            out_of_range=[{"response_id": "r8", "detail": "12 of 10"}],
            roster_additions=[{"row": "s1:1000000009", "name": "New", "section": 507}],
            gradescope={"configured": True, "live": True},
            students=[student("s1:1000000001", "blocked", block="excused_with_paper"), student("s1:1000000004", "blocked", block="excused_with_sheet"),
                      student("s1:1000000005", "blocked", block="student_incomplete")])
        self.assertEqual(sorted(self.ids(rules.build_checks(facts), "block")),
                         sorted(["excused_with_paper", "excused_with_sheet", "student_incomplete", "version_decisions", "inbox_pending", "papers_in_review",
                                 "bubble_unsettled", "looked_final_skipped", "score_out_of_range", "roster_additions", "gradescope_live"]))
        self.assertIn("nothing_to_certify", self.ids(rules.build_checks(self.facts(students=[])), "block"))
        self.assertIn("bubble_unsettled", self.ids(rules.build_checks(self.facts(bubble={"part": True, "has_key": False, "sheets": [{"status": "no_key"}]}))))

    def test_rubric_finalization_is_information_only(self):
        checks = rules.build_checks(self.facts(rubric_quality={"state": "queued", "not_standing": []}))
        self.assertEqual([(c["id"], c["level"]) for c in checks if c["id"] == "rubric_finalization"], [("rubric_finalization", "info")])

    def test_confirm_checks_and_their_digests(self):
        students = [student("s1:1000000001", "zero", "no_paper"), student("s1:1000000002", "zero", "absent"),
                    student("s1:1000000003", "scored", "missing_part", missing_parts=["bubble"]),
                    student("s1:1000000004", "left_out", "dr_ahead", dr_date="2026-10-02", dr=[{"booking_id": "b1", "date": "2026-10-02"}]),
                    student("s1:1000000005", "left_out", "duplicate_membership"),
                    student("s1:1000000006", "scored", frq_version="A", bubble_version="B"), student("s1:1000000007", "excused", "excused")]
        questions = [{"question_id": "q1", "title": "Q1", "version": "A", "n_final": 6, "zero": 6, "full": 0},
                     {"question_id": "q2", "title": "Q2", "version": "A", "n_final": 4, "zero": 4, "full": 0}]  # too few to say anything
        facts = self.facts(students=students, questions=questions, unclaimed={"unmatched_sheets": 1},
                           bubble={"part": True, "has_key": True, "sheets": [], "unmatched": [{"sheet_id": "x1", "reason": "No student"}]})
        checks = {c["id"]: c for c in rules.build_checks(facts)}
        self.assertEqual(sorted(i for i, c in checks.items() if c["level"] == "confirm"),
                         sorted(["zero_no_work", "zero_missing_part", "dr_ahead", "duplicate_membership", "bubble_frq_version_mismatch",
                                 "zero_question_all", "unmatched_sheets"]))
        self.assertEqual(checks["zero_no_work"]["count"], 2)
        self.assertIn("unmatched bubble sheet", checks["zero_no_work"]["warning"])
        self.assertEqual(checks["zero_question_all"]["count"], 1)
        # A digest names exactly its members: a student whose reason changes (absent -> no paper) changes it.
        again = {c["id"]: c for c in rules.build_checks(self.facts(**{**facts, "students": [student("s1:1000000001", "zero", "no_paper"),
                                                                                             student("s1:1000000002", "zero", "no_paper")] + students[2:]}))}
        self.assertNotEqual(again["zero_no_work"]["digest"], checks["zero_no_work"]["digest"])
        self.assertEqual(again["dr_ahead"]["digest"], checks["dr_ahead"]["digest"])
        self.assertTrue(all(c["requires"] == "tick" for c in checks.values() if c["level"] == "confirm"))
        # Every student has a sheet: an unmatched sheet takes nothing from anyone.
        calm = {c["id"]: c for c in rules.build_checks(self.facts(bubble=facts["bubble"]))}
        self.assertEqual(calm["unmatched_sheets"]["level"], "info")

    def test_students_owed_a_makeup_are_left_out_under_their_own_tick(self):
        # MAKEUP OWED (requirement, 2026-10-07): the tick names each absence and its decision, so a decision taken meanwhile is new.
        owed = lambda status: student("s1:1000000008", "left_out", "makeup", earned=None, detail="Makeup owed · absence Fri, Oct 2 excused",
                                      makeup={"absence_id": "a1", "status": status, "dates": "Fri, Oct 2"})
        checks = {c["id"]: c for c in rules.build_checks(self.facts(students=[owed("excused"), student("s1:1000000001", "zero", "no_paper")]))}
        found = checks["makeup_ahead"]
        self.assertEqual((found["level"], found["requires"], found["count"], found["title"]), ("confirm", "tick", 1, "1 student left out until their makeup"))
        self.assertEqual([(r["row"], r["detail"]) for r in found["rows"]], [("s1:1000000008", "Makeup owed · absence Fri, Oct 2 excused")])
        self.assertEqual(checks["zero_no_work"]["count"], 1)  # never also a 0
        pending = {c["id"]: c for c in rules.build_checks(self.facts(students=[owed("pending"), student("s1:1000000001", "zero", "no_paper")]))}
        self.assertNotEqual(pending["makeup_ahead"]["digest"], found["digest"])
        self.assertEqual(pending["zero_no_work"]["digest"], checks["zero_no_work"]["digest"])

    def test_a_sheet_on_no_participant_of_the_exam_is_unmatched_and_warns_the_missing_part(self):
        """Review F1: a sheet matched to someone outside the exam, or to a student of two sections with neither section, is placed
        on nobody; it is counted with the unmatched sheets, never silently dropped (critique C.2)."""
        from grading.certification import CertificationService
        participants = [{"enrollment_id": "1000000001", "section_id": "s1", "section_number": 501},
                        {"enrollment_id": "1000000002", "section_id": "s1", "section_number": 501},
                        {"enrollment_id": "1000000002", "section_id": "s2", "section_number": 502}]
        sheets = [{"sheet_id": "a", "enrollment_id": "1000000001", "section": 501}, {"sheet_id": "b", "enrollment_id": "9999999999", "section": 501},
                  {"sheet_id": "c", "enrollment_id": "1000000002", "section": 503}, {"sheet_id": "d", "enrollment_id": None, "reason": "No student"}]
        placed, unmatched = CertificationService._sheets(None, {"participants": participants, "bubble": {"sheets": sheets}})
        self.assertEqual(list(placed), ["s1:1000000001"])
        self.assertEqual(sorted(s["sheet_id"] for s in unmatched), ["b", "c", "d"])
        self.assertEqual([s.get("outside", False) for s in sorted(unmatched, key=lambda s: s["sheet_id"])], [True, True, False])
        students = [student("s1:1000000003", "scored", "missing_part", missing_parts=["bubble"])]
        facts = self.facts(students=students, unclaimed={"unmatched_sheets": 1},
                           bubble={"part": True, "has_key": True, "sheets": [], "unmatched": [{"sheet_id": "b", "reason": "Matched to someone who is not in this exam", "outside": True}]})
        checks = {c["id"]: c for c in rules.build_checks(facts)}
        self.assertEqual(checks["unmatched_sheets"]["level"], "confirm")
        self.assertEqual(checks["unmatched_sheets"]["rows"][0]["name"], "Sheet matched elsewhere")
        self.assertIn("one may be theirs", checks["zero_missing_part"]["warning"])

    def test_gradescope_groups_need_a_decision_and_remember_the_reason(self):
        written = {"uncovered": [["r1", "not_sent"], ["r2", "changed"]], "papers_missing": ["s1:1000000001"], "unsendable": {"q2": "drafted"},
                   "questions": {"q1": {"not_sent": 1, "changed": 1, "unsendable": None}, "q2": {"not_sent": 0, "changed": 0, "unsendable": "drafted"}},
                   "never": {"grades": {}, "papers": {}}, "totals": {"final": 10, "sent": 8, "changed": 1, "not_sent": 1}}
        sheets = {"missing": ["s1:1000000002"], "never": {}, "totals": {"matched": 5}}
        facts = self.facts(gradescope={"configured": True, "written": written, "sheets": sheets})
        checks = {c["id"]: c for c in rules.build_checks(facts)}
        self.assertEqual({checks[k]["requires"] for k in ("gradescope_written", "gradescope_sheets")}, {"decision"})
        self.assertEqual(checks["gradescope_written"]["action"]["kind"], "send_written")
        self.assertEqual(checks["gradescope_written"]["count"], 4)
        members = rules.gradescope_members("written", written)
        self.assertEqual(members, ["paper:s1:1000000001", "question:q2", "response:r1:not_sent", "response:r2:changed"])
        previous = {"written": {"decision": "do_not_send", "reason": "Quiz rubrics were drafted in Faculty.", "members": members + ["response:r3:not_sent"]}}
        self.assertEqual(rules.prefill_reason(previous, "written", members), "Quiz rubrics were drafted in Faculty.")  # smaller set: same reason
        self.assertEqual(rules.prefill_reason(previous, "written", members + ["response:r4:not_sent"]), "")  # a new grade: say why again
        self.assertEqual(rules.prefill_reason(None, "sheets", ["sheet:x"], configured=False), "Gradescope is not set up.")
        # A version nobody has sat (Quiz 6's makeup, 2026-11-03): a question with no answer has no grade to leave off Gradescope.
        unsat = {**written, "uncovered": [], "papers_missing": [], "unsendable": {"q9": "drafted"}, "questions": {}}
        self.assertEqual(rules.gradescope_members("written", unsat), [])
        self.assertNotIn("gradescope_written", self.ids(rules.build_checks(self.facts(gradescope={"configured": True, "written": unsat, "sheets": None}))))
        # Not set up: everything is uncovered, nothing is offered to send, and the reason is prefilled.
        unset = {c["id"]: c for c in rules.build_checks(self.facts(gradescope={"configured": False, "written": written, "sheets": sheets}))}
        self.assertEqual((unset["gradescope_written"].get("action"), unset["gradescope_written"]["prefill"]), (None, "Gradescope is not set up."))
        # Everything covered: nothing to decide.
        covered = rules.build_checks(self.facts(gradescope={"configured": True, "written": {**written, "uncovered": [], "papers_missing": [], "unsendable": {}},
                                                            "sheets": {**sheets, "missing": []}}))
        self.assertNotIn("gradescope_written", self.ids(covered))
        self.assertNotIn("gradescope_sheets", self.ids(covered))

    def test_information_checks(self):
        by_version = {"A": {"n": 12, "mean": 80, "possible": 100}, "B": {"n": 12, "mean": 65, "possible": 100}}
        facts = self.facts(by_version=by_version, possible_by_version={"A": Decimal(100), "B": Decimal(98)},
                           students=[student("s1:1000000001", "scored", earned="50", possible="98", dr=[{"date": "2026-09-17"}])],
                           questions=[{"question_id": "q1", "title": "Q1", "version": "", "n_final": 5, "zero": 0, "full": 5, "automatic_unchecked": 3,
                                       "flagged_finalized": 1}],
                           gradescope={"configured": True, "anything_sent": True}, training_waiting=2,
                           answer_groups=[{"question_id": "q1", "title": "Q1", "answer": "x=6", "scores": {"10": 5, "4": 1}}],
                           bubble_suspects=[{"version": "A", "number": 3, "answer": "B", "common": "C", "correct_pct": 12.0}],
                           changes=[{"row": "s1:1000000001", "name": "A", "section": 507, "before": {"earned": "40"}, "after": {"earned": "50"}}])
        info = self.ids(rules.build_checks(facts), "info")
        for expected in ("version_gap", "version_max_differs", "dr_graded", "gradescope_published_unknown", "gradescope_side_changes",
                         "training_waiting", "question_all_full", "automatic_unchecked", "flagged_finalized", "answer_inconsistency", "bubble_key_suspect",
                         "changes_since"):
            self.assertIn(expected, info)

    def test_bubble_key_suspects(self):
        key = {"versions": [{"letter": "A", "questions": [{"number": 1, "answer": "A"}, {"number": 2, "answer": "B"}, {"number": 3, "answer": "C"}]}]}
        stats = {"A": {"1": {"sheets": 20, "full": 18, "common": "A", "common_count": 18}, "2": {"sheets": 20, "full": 5, "common": "D", "common_count": 12},
                       "3": {"sheets": 20, "full": 3, "common": "C", "common_count": 3}}}
        self.assertEqual([s["number"] for s in rules.bubble_key_suspects(key, stats)], [2, 3])
        self.assertEqual(rules.bubble_key_suspects(key, {"A": {"2": {"sheets": 5, "full": 0, "common": "D", "common_count": 5}}}), [])

    def test_provisional_marks_every_check(self):
        checks = rules.build_checks(self.facts(work={"states": {"proposed": 1}}), provisional=True)
        self.assertTrue(checks and all(c["provisional"] for c in checks))


class SnapshotTests(unittest.TestCase):
    def records(self):
        decisions = [{"question_id": "q2", "score": 3.5, "max": 4, "kind": "review", "decision_id": "v1", "result_id": "x1", "response_id": "r1"},
                     {"question_id": "q1", "score": 10, "max": 10, "kind": "automatic", "decision_id": "a1", "result_id": "x2", "response_id": "r2"}]
        scored = rules.record({"section_id": "s1", "enrollment_id": "1000000001", "exam_version": "A"},
                              {"status": "scored", "reason": "missing_part", "earned": "13.5", "possible": "74", "dr_date": None},
                              {"frq": rules.frq_part("complete", decisions, "13.5", "14"), "bubble": rules.zero_part(60, "no bubble sheet")})
        zero = rules.record({"section_id": "s1", "enrollment_id": "1000000002"}, {"status": "zero", "reason": "no_paper", "earned": "0", "possible": "74",
                                                                                   "dr_date": "2026-09-17"}, {})
        left = rules.record({"section_id": "s1", "enrollment_id": "1000000003"}, {"status": "left_out", "reason": "dr_ahead", "earned": None,
                                                                                   "possible": None, "dr_date": "2026-10-02"}, {})
        return [scored, zero, left]

    def test_records_reference_decisions_and_digest_canonically(self):
        scored, zero, left = self.records()
        self.assertEqual(scored["parts_json"]["frq"]["questions"]["q1"], {"score": "10", "max": "10", "decision": "automatic:a1", "result_id": "x2", "response_id": "r2"})
        self.assertEqual(scored["parts_json"]["bubble"], {"state": "zero", "earned": "0", "possible": "60", "reason": "no bubble sheet"})
        self.assertEqual((zero["dr_date"], left["dr_date"], left["earned"]), ("2026-09-17", "2026-10-02", None))
        self.assertEqual(rules.snapshot_digest([scored, zero, left]), rules.snapshot_digest([left, scored, zero]))
        self.assertNotEqual(rules.snapshot_digest([scored, zero, left]), rules.snapshot_digest([scored, {**zero, "possible": "75"}, left]))
        with self.assertRaises(ValueError):
            rules.record({"section_id": "s", "enrollment_id": "1"}, {"status": "blocked", "reason": ""}, {})

    def test_summary_and_changes(self):
        records = self.records()
        found = rules.summary(records)
        self.assertEqual({k: found[k] for k in ("students", "scored", "zero", "excused", "left_out", "left_out_dr", "left_out_makeup", "missing_part")},
                         {"students": 3, "scored": 1, "zero": 1, "excused": 0, "left_out": 1, "left_out_dr": 1, "left_out_makeup": 0, "missing_part": 1})
        waiting = rules.record({"section_id": "s1", "enrollment_id": "1000000004"}, {"status": "left_out", "reason": "makeup", "earned": None,
                                                                                     "possible": None, "dr_date": None}, {})
        self.assertEqual({k: rules.summary([*records, waiting])[k] for k in ("left_out", "left_out_dr", "left_out_makeup")},
                         {"left_out": 2, "left_out_dr": 1, "left_out_makeup": 1})
        self.assertEqual(found["stats"]["n"], 0)  # a total with a part confirmed as 0 is not an every-part total
        self.assertEqual(found["per_part"]["frq"]["n"], 1)
        before = [{**records[0], "earned": "12"}, records[1]]
        self.assertEqual([(c["row"], c["before"], c["after"]) for c in rules.changes(before, records)],
                         [("s1:1000000001", {"status": "scored", "earned": "12"}, {"status": "scored", "earned": "13.5"}),
                          ("s1:1000000003", None, {"status": "left_out", "earned": None})])

    def test_the_fingerprint_ignores_information(self):
        checks = [{"id": "zero_no_work", "level": "confirm", "digest": "d1"}, {"id": "training_waiting", "level": "info", "count": 3}]
        base = dict(parts=["frq"], snapshot_sha256="s", gradescope={"written": "w"}, gradebook={"item_id": "new:a1", "setup_revision": None})
        first = rules.fingerprint(checks=checks, **base)
        moved = [checks[0], {"id": "training_waiting", "level": "info", "count": 1}, {"id": "automatic_unchecked", "level": "info", "count": 9}]
        self.assertEqual(rules.fingerprint(checks=moved, **base), first)  # critique C4
        self.assertNotEqual(rules.fingerprint(checks=[{**checks[0], "digest": "d2"}], **base), first)
        self.assertNotEqual(rules.fingerprint(checks=checks, **{**base, "snapshot_sha256": "t"}), first)


class RequestTests(unittest.TestCase):
    def body(self, **extra):
        return {"request_id": str(uuid.uuid4()), "fingerprint": "f", "confirmation": "certify", "acknowledged": {"zero_no_work": "d"},
                "gradescope": {"sheets": {"decision": "do_not_send", "reason": " Not set up "}},
                "gradebook": {"replace": ["s1:1000000001"], "category_id": None, "column": {"action": "create", "parts": "certified"}}, **extra}

    def test_a_valid_request_and_its_sha(self):
        request_id, request, sha = rules.parse_request(self.body())
        self.assertEqual(request["gradescope"]["sheets"], {"decision": "do_not_send", "reason": "Not set up"})
        other_id, _, other_sha = rules.parse_request({**self.body(), "request_id": str(uuid.uuid4())})
        self.assertNotEqual(request_id, other_id)
        self.assertEqual(sha, other_sha)  # the request ID is not part of what was asked
        self.assertNotEqual(sha, rules.parse_request(self.body(acknowledged={"zero_no_work": "e"}))[2])

    def test_malformed_requests_are_refused_before_anything_is_read(self):
        for bad in ({"confirmation": "yes"}, {"request_id": "not-a-uuid"}, {"extra": 1}, {"fingerprint": ""},
                    {"gradescope": {"written": {"decision": "do_not_send", "reason": ""}}},
                    {"gradescope": {"written": {"decision": "do_not_send", "reason": "x" * 501}}},
                    {"gradescope": {"other": {"decision": "send"}}}, {"gradebook": {"replace": ["123456789"]}},
                    {"gradebook": {"column": {"parts": "some"}}}, {"acknowledged": {"zero_no_work": 1}},
                    {"gradescope": ["x"]}, {"gradescope": "send"}, {"gradebook": {"column": {"action": ["create"]}}}):  # review S2: 400, never 500
            with self.assertRaises(ValueError, msg=bad):
                rules.parse_request(self.body(**bad))
        with self.assertRaises(ValueError):
            rules.parse_request([])

    def test_every_confirm_check_needs_its_current_digest_and_each_gradescope_group_a_decision(self):
        checks = [{"id": "zero_no_work", "level": "confirm", "title": "Zeros", "digest": "d", "requires": "tick"},
                  {"id": "gradescope_written", "level": "confirm", "title": "Written", "digest": "w", "requires": "decision"},
                  {"id": "gradescope_sheets", "level": "confirm", "title": "Sheets", "digest": "s", "requires": "decision"},
                  {"id": "training_waiting", "level": "info", "title": "Training"}]
        _, request, _ = rules.parse_request(self.body())
        self.assertEqual(rules.unconfirmed(checks, request), ["Written"])
        _, stale, _ = rules.parse_request(self.body(acknowledged={"zero_no_work": "old"}))
        self.assertEqual(rules.unconfirmed(checks, stale), ["Zeros", "Written"])
        record = rules.gradescope_record(checks, request, {"sheets": {"missing": ["s1:1000000002"], "totals": {"matched": 1}}}, configured=False)
        self.assertEqual(record["sheets"]["members"], ["sheet:s1:1000000002"])
        self.assertEqual((record["sheets"]["decision"], record["sheets"]["reason"]), ("do_not_send", "Not set up"))
        self.assertEqual(record["written"]["decision"], "none")


# ----- markers on a minimal catalog ---------------------------------------------------------------------------------------------
class BubbleMarkerTests(unittest.TestCase):
    PLAN = {"id": "p1", "source_assignment_id": "a1", "term_id": "t1", "event_id": "e1"}

    def setUp(self):
        from bubble_sheets import matching
        self.matcher = matching.MATCHER
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE frq_bubble_keys(id TEXT, assignment_id TEXT, revision INTEGER);
            CREATE TABLE bubble_settings(id INTEGER PRIMARY KEY, auto_identify REAL);
            CREATE TABLE inbox_messages(id TEXT, instructor_review_required INTEGER, created_at TEXT);
            CREATE TABLE inbox_jobs(id TEXT, message_id TEXT);
            CREATE TABLE inbox_deletions(id INTEGER PRIMARY KEY, message_id TEXT, restored_at TEXT);
            CREATE TABLE bubble_runs(id TEXT, message_id TEXT, assignment_id TEXT, term_id TEXT, status TEXT, created_at TEXT, question_count INTEGER);
            CREATE TABLE bubble_sheets(id TEXT, run_id TEXT, match_json TEXT);
            CREATE TABLE bubble_matches(sheet_id TEXT, matcher TEXT, match_json TEXT);
            CREATE TABLE bubble_reviews(id INTEGER PRIMARY KEY, sheet_id TEXT, section_id TEXT, enrollment_id TEXT, answers_json TEXT, version TEXT);
            INSERT INTO frq_bubble_keys VALUES('k1','p1',1);
            INSERT INTO inbox_messages VALUES('m1',0,'2026-09-20');
            INSERT INTO inbox_jobs VALUES('j1','m1');
            INSERT INTO bubble_runs VALUES('r1','m1','a1','t1','complete','2026-09-20',50);""")
        match = json.dumps({"state": "suggested", "confidence": .8, "enrollment_id": "1000000001", "notes": []})
        self.db.execute("INSERT INTO bubble_sheets VALUES('b1','r1',?)", (match,))
        self.db.execute("INSERT INTO bubble_sheets VALUES('b2','r1',?)", (json.dumps({"state": "unmatched", "confidence": 0, "enrollment_id": None, "notes": []}),))

    def marker(self):
        return progress.bubble_marker(self.db, self.PLAN)

    def test_only_changes_to_what_a_score_is_read_from_move_the_marker(self):
        first = self.marker()
        self.assertEqual(self.marker(), first)
        # The same threshold saved again, and a re-match under the current matcher naming the same student, change nothing.
        self.db.execute("INSERT INTO bubble_settings(auto_identify) VALUES(0.7)")
        self.db.execute("INSERT INTO bubble_settings(auto_identify) VALUES(0.7)")
        self.db.execute("INSERT INTO bubble_matches VALUES('b1',?,?)", (self.matcher, json.dumps({"state": "suggested", "confidence": .81,
                                                                                                   "enrollment_id": "1000000001", "notes": []})))
        self.assertEqual(self.marker(), first)
        # A threshold above the match takes the student away.
        self.db.execute("INSERT INTO bubble_settings(auto_identify) VALUES(0.9)")
        raised = self.marker()
        self.assertNotEqual(raised, first)
        # A review, then the same review again: only its content counts.
        self.db.execute("INSERT INTO bubble_reviews(sheet_id,section_id,enrollment_id,answers_json,version) VALUES('b2','s1','1000000002','[\"A\"]','A')")
        reviewed = self.marker()
        self.assertNotEqual(reviewed, raised)
        self.db.execute("INSERT INTO bubble_reviews(sheet_id,section_id,enrollment_id,answers_json,version) VALUES('b2','s1','1000000002','[\"A\"]','A')")
        self.assertEqual(self.marker(), reviewed)
        self.db.execute("INSERT INTO frq_bubble_keys VALUES('k2','p1',2)")
        keyed = self.marker()
        self.assertNotEqual(keyed, reviewed)
        # A deleted scan counts nowhere; restoring it brings it back.
        self.db.execute("INSERT INTO inbox_deletions(message_id) VALUES('m1')")
        self.assertNotEqual(self.marker(), keyed)
        self.db.execute("UPDATE inbox_deletions SET restored_at='x'")
        self.assertEqual(self.marker(), keyed)
        # A scan still under Inbox review is part of the marker too: clearing it changes what counts.
        self.db.execute("UPDATE inbox_messages SET instructor_review_required=1")
        self.assertNotEqual(self.marker(), keyed)


class CertifiedTotalsTests(unittest.TestCase):
    """`gradebook.sources.certified_totals`, pure: the certified rows in `totals()`'s shape for a column's own parts (C10, C11)."""
    RECORDS = [
        {"section_id": "s1", "enrollment_id": "1000000001", "status": "scored", "reason": "missing_part", "earned": "30", "possible": "100", "dr_date": None,
         "parts_json": {"frq": {"state": "complete", "earned": "30", "possible": "40"}, "bubble": {"state": "zero", "earned": "0", "possible": "60", "reason": "no bubble sheet"}}},
        {"section_id": "s1", "enrollment_id": "1000000002", "status": "zero", "reason": "no_paper", "earned": "0", "possible": "100", "dr_date": None,
         "parts_json": json.dumps({"frq": {"state": "zero", "earned": "0", "possible": "40"}, "bubble": {"state": "zero", "earned": "0", "possible": "60"}})},
        {"section_id": "s1", "enrollment_id": "1000000003", "status": "excused", "reason": "excused", "earned": None, "possible": None, "dr_date": None, "parts_json": {}},
        {"section_id": "s1", "enrollment_id": "1000000004", "status": "left_out", "reason": "dr_ahead", "earned": None, "possible": None, "dr_date": "2026-10-02",
         "parts_json": {}}]

    def test_a_column_takes_only_its_parts_and_left_out_cells_are_cleared(self):
        both = sources.certified_totals(self.RECORDS, ["frq", "bubble"], certified_parts=["frq", "bubble"], clear={"s1:1000000004"})
        self.assertEqual((both["students"]["s1:1000000001"]["earned"], both["students"]["s1:1000000001"]["possible"]), ("30", "100"))
        self.assertEqual(both["students"]["s1:1000000001"]["parts"]["bubble"]["detail"], "confirmed 0: no bubble sheet")
        frq = sources.certified_totals(self.RECORDS, ["frq"], certified_parts=["frq", "bubble"])
        self.assertEqual([(frq["students"][r]["state"], frq["students"][r]["earned"], frq["students"][r]["possible"]) for r in ("s1:1000000001", "s1:1000000002")],
                         [("complete", "30", "40"), ("complete", "0", "40")])
        self.assertTrue(frq["students"]["s1:1000000003"]["excused"])
        self.assertTrue(both["students"]["s1:1000000004"]["clear"])
        self.assertFalse(frq["students"]["s1:1000000004"]["clear"])
        # The plan records the certified values and clears a left-out student's earlier certified cell (a `grading` clear).
        item = {"id": "i1", "points": "100", "sections": [], "assignment_id": "a1"}
        students = [{"row": f"s1:100000000{i}", "section_id": "s1", "section": 1, "name": f"S{i}"} for i in range(1, 6)]
        earlier = {"id": "c4", "status": "scored", "points": "0", "note": "", "source": "grading"}
        found = sources.plan(item, both, students=students, cells={"s1:1000000004": earlier}, options={}, revision=1)
        actions = {r["row"]: (r["action"], r["proposed"]) for r in found["rows"]}
        self.assertEqual(actions["s1:1000000002"], ("add", {"status": "scored", "points": "0"}))
        self.assertEqual(actions["s1:1000000003"], ("add", {"status": "excused", "points": None}))
        self.assertEqual(actions["s1:1000000004"], ("update", {"status": "cleared", "points": None}))
        self.assertEqual((actions["s1:1000000005"][0], found["rows"][-1]["reason"]), ("none", "not in the certification"))
        # A newer certification makes an older pull preview stale; the certify step itself names none (C2).
        named = sources.certified_totals(self.RECORDS, ["frq", "bubble"], certified_parts=["frq", "bubble"], certification_id="c1")
        self.assertNotEqual(sources.plan(item, named, students=students, cells={}, options={}, revision=1)["fingerprint"],
                            sources.plan(item, both | {"students": {k: {kk: vv for kk, vv in v.items() if kk != "clear"} for k, v in both["students"].items()}},
                                         students=students, cells={}, options={}, revision=1)["fingerprint"])


class AmountTests(unittest.TestCase):
    def test_amounts_are_exact_text(self):
        self.assertEqual([progress.amount(v) for v in (3.5, 4.0, "4.00", 0, -0.0, 0.1 + 0.2, None, "x", True)],
                         ["3.5", "4", "4", "0", "0", "0.30000000000000004", None, None, None])
        self.assertEqual(progress.amount(1e2), "100")

    def test_score_content_is_what_its_text_says_and_each_caller_gets_lists_of_its_own(self):
        text = json.dumps({"score": 4.0, "selected": ["R2", "R1"], "adjustments": [{"points": -0.5}, {"points": 1}]})
        self.assertEqual(progress.score_content(text), ("4", ("R1", "R2"), ("-0.5", "1")))
        self.assertEqual(progress.score_content(None), (None, (), ()))
        rows = [{"id": "r", "state": "approved", "current_result_id": "x"}]
        with sqlite3.connect(":memory:") as db:  # the stored scores decision_rows reads, and nothing else
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE frq_responses(id,assignment_id,current_review_id,current_result_id)")
            db.execute("CREATE TABLE frq_reviews(id,result_id,score_json)")
            db.execute("CREATE TABLE frq_results(id,score_json)")
            db.execute("INSERT INTO frq_responses VALUES('r','p','v','x')")
            db.execute("INSERT INTO frq_reviews VALUES('v','x',?)", (text,))
            first, second = (progress.decision_rows(db, "p", rows, automatic={})["r"] for _ in range(2))
        first["selected"].append("R9")
        first["adjustments"].clear()
        self.assertEqual((second["score"], second["selected"], second["adjustments"]), ("4", ["R1", "R2"], ["-0.5", "1"]))


# ----- the graded workspace ------------------------------------------------------------------------------------------------------
class Graded(graded.GradescopeCase):
    """Two students (Versions A and B), four final grades from the instructor's approved reviews."""

    def read(self):
        with self.frq.rosters.lock(), connect(self.path) as db:
            plan = self.frq.plan(db, self.aid)
            responses = self.frq.responses(self.aid)
            event = self.frq.exams._event(db, plan["event_id"])
            participants = self.frq.exams._participants(db, event)
            collection = self.frq.exams._collection(db, event, participants)
            workflow = self.frq.workflow_summary(db, plan, responses)
            return plan, responses, participants, collection, workflow

    def certify(self, students=None, supersede=True):
        """A certification recorded as it stands now (the service's own writing is role A2's; here only the markers matter)."""
        plan, responses, participants, collection, _ = self.read()
        with self.frq.rosters.lock(), connect(self.path) as db:
            latest = progress.latest_certification(db, self.aid)
            cid = str(uuid.uuid4())
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
                participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,supersedes_id,actor,created_at)
                VALUES(?,?,?,'s','f',?,'["frq"]',?,?,?,'n',?,'[]','{}','{}',?,?,'workspace','2026-09-26T12:00:00+00:00')""",
                       (cid, self.aid, cid, progress.RULE, digest(progress.decision_state(db, self.aid, responses)), progress.bubble_marker(db, plan),
                        progress.participants_marker(db, plan, collection["students"], ["frq"]), json.dumps({"left_out": 0}),
                        json.dumps({"item_id": "i1", "title": "Test 1", "written": 2, "kept": 0}), latest["id"] if latest and supersede else None))
            for p in participants:
                values = (students or {}).get(p["enrollment_id"]) or {"status": "scored", "reason": "", "earned": "6", "possible": "18", "dr_date": None}
                db.execute("""INSERT INTO frq_certification_students(certification_id,section_id,enrollment_id,status,reason,earned,possible,parts_json,dr_date)
                    VALUES(?,?,?,?,?,?,?,'{}',?)""", (cid, p["section_id"], p["enrollment_id"], values["status"], values["reason"], values["earned"],
                                                      values["possible"], values["dr_date"]))
        return cid

    def state(self):
        return self.read()[4]["certification"]

    def test_the_summary_is_none_then_current_then_changed_by_a_grade(self):
        self.assertEqual(self.state(), {"state": "none", "id": None, "created_at": None, "changed": [], "gradebook": None, "left_out": 0, "left_out_dr": 0})
        cid = self.certify()
        found = self.state()
        self.assertEqual((found["state"], found["id"], found["changed"], found["gradebook"]),
                         ("current", cid, [], {"item_id": "i1", "title": "Test 1", "written": 2, "kept": 0}))
        self.assertIn("export", self.read()[4])  # the old field stays for old readers
        # Approving the same lines again is a new decision with the same content: nothing reopens (critique C7).
        self.decide("q20", "A", ["R3_2"])
        self.assertEqual(self.state()["state"], "current")
        self.decide("q20", "A", ["R3_3"])
        self.assertEqual((self.state()["state"], self.state()["changed"]), ("changed", ["grades"]))
        # A newer certification makes it current again.
        self.certify()
        self.assertEqual(self.state()["state"], "current")
        # A draft takes a grade out of final: the content becomes open work.
        self.decide("q21", "B", ["R4_3"], status="draft")
        self.assertIn("grades", self.state()["changed"])

    def test_a_makeup_after_certifying_reopens_grading_with_the_same_rubrics(self):
        # Requirement, 2026-09-28: a scan landing for a certified assignment joins its grading, graded as the others were.
        import io
        from test_exam_grading import HEADERS
        from test_rosters import STUDENTS, csv_bytes
        self.certify()
        self.assertEqual(self.state()["state"], "current")
        with connect(self.path) as db:
            pins = db.execute("SELECT question_id,question_revision_id,rubric_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY question_id",
                              (self.aid,)).fetchall()
        finalization = self.frq.rubric_quality(self.aid)["review"]["id"]
        # A student who sat the makeup joins the roster and the exam; their paper is scanned and reviewed.
        group = self.client.get("/api/state").json["course_groups"][0]
        makeup = ["", "Example, Carol", "234567890", "Registered", "MATH", "U1", "", "N", "", "", "", "carol@example.test"]
        result = self.client.post(f"/api/course-groups/{group['id']}/sections/501/roster",
                                  data={"file": (io.BytesIO(csv_bytes([*STUDENTS, makeup])), "roster.csv")}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        self.exams.sync_roster(self.event["id"], {"version": self.exams.get(self.event["id"])["version"]})
        with connect(self.path) as db:
            carol = db.execute("SELECT id FROM student_enrollments WHERE uin='234567890'").fetchone()[0]
        _, revision = self.reviewed_source(batch=self.ready_batch(enrollment_id=carol), changed="makeup")
        # The pipeline comes back from Archived: the certification no longer says what the grades are.
        found = self.state()
        self.assertEqual(found["state"], "changed")
        self.assertIn("students", found["changed"])
        self.frq.confirm_paper_version(self.aid, {"exam_version": "A", "section_id": self.section["id"], "enrollment_id": carol,
                                                  "assembly_revision_id": revision["id"], "confirm_version": True})
        self.assertIn("new_work", self.state()["changed"])
        # Nothing to set up again: the same pinned rubrics and the same confirmed finalization grade the makeup, and only it.
        command = {"request_id": str(uuid.uuid4()), "question_ids": list(self.q.values()), "mode": "local", "operation": "recognize", "selection": "new"}
        preview = self.frq.preview(self.aid, command)
        self.assertEqual(sorted({r["enrollment_id"] for r in preview["eligible"]}), [carol], preview["skipped"])
        self.assertEqual(len(preview["eligible"]), 2)
        job = self.frq.create_job(self.aid, {**command, "fingerprint": preview["fingerprint"]})
        for _ in range(80):
            if not self.frq.process_one():
                break
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT question_id,question_revision_id,rubric_id FROM frq_assignment_questions WHERE assignment_id=? ORDER BY question_id",
                                        (self.aid,)).fetchall(), pins)
        self.assertEqual(self.frq.rubric_quality(self.aid)["review"]["id"], finalization)
        self.assertEqual(self.frq.job(job["id"])["profile"]["rubric_finalization"], finalization)

    def test_participants_new_work_and_a_passed_dr_date_reopen_it(self):
        self.certify()
        plan, responses, participants, collection, workflow = self.read()
        with self.frq.rosters.lock(), connect(self.path) as db:
            self.assertEqual(progress.certification_summary(db, plan, responses, {"new_responses": 1}, collection["students"], today=TODAY)["changed"],
                             ["new_work"])
        person = next(p for p in participants if p["enrollment_id"] == self.second)
        self.exams.update_participant(self.event["id"], person["section_id"], person["enrollment_id"],
                                      {"version": person["version"], "expectation": "excused", "reason": "Medical"})
        # (An excuse beside a received paper also takes that paper's grades out of final; the participants marker alone says so too.)
        self.assertIn("students", self.state()["changed"])
        self.certify(students={self.second: {"status": "left_out", "reason": "dr_ahead", "earned": None, "possible": None, "dr_date": "2026-09-30"}})
        plan, responses, participants, collection, workflow = self.read()
        with self.frq.rosters.lock(), connect(self.path) as db:
            summary = lambda today: progress.certification_summary(db, plan, responses, {}, collection["students"], today=today)
            self.assertEqual(summary("2026-09-30")["state"], "current")  # the exam is today: still ahead
            self.assertEqual(summary("2026-10-01")["changed"], ["dr_date_passed"])

    def test_a_drop_never_asks_to_certify_again(self):
        """A DROP NEVER ASKS TO CERTIFY AGAIN (requirement, 2026-10-23):
        a participant dropped after certifying leaves it current, and a certification recorded while the digest still named the
        drops (until 2026-10-23) stays current with the drops of its time."""
        self.certify()
        plan, responses, participants, collection, _ = self.read()
        person = next(p for p in participants if p["enrollment_id"] == self.second)
        with self.frq.rosters.lock(), connect(self.path) as db:
            db.execute("""INSERT INTO roster_drops(id,section_id,enrollment_id,action,source,reason,request_id,actor,created_at)
                VALUES(?,?,?,'drop','roster','Q drop',?,'workspace','2026-09-27T09:00:00+00:00')""",
                       (str(uuid.uuid4()), person["section_id"], person["enrollment_id"], str(uuid.uuid4())))
        self.assertEqual(self.state()["state"], "current")
        plan, responses, participants, collection, _ = self.read()
        with self.frq.rosters.lock(), connect(self.path) as db:
            older = progress.participants_marker(db, plan, collection["students"], ["frq"], dropped_as_of="2026-09-28T00:00:00+00:00")
            self.assertNotEqual(older, progress.participants_marker(db, plan, collection["students"], ["frq"]))
            changed = lambda at: progress.participants_changed(db, plan, collection["students"], ["frq"], {"participants_sha256": older, "created_at": at})
            self.assertFalse(changed("2026-09-28T00:00:00+00:00"))  # certified after the drop, under the older digest
            self.assertTrue(changed("2026-09-26T00:00:00+00:00"))   # that digest never matched a certification made before it

    def test_decision_state_is_content_and_export_summary_keeps_its_answer(self):
        plan, responses, *_ = self.read()
        with self.frq.rosters.lock(), connect(self.path) as db:
            rows = progress.decision_rows(db, self.aid, responses)
            self.assertEqual(sorted((r["kind"], r["score"], tuple(r["selected"])) for r in rows.values()),
                             [("review", "2", ("R3_3",)), ("review", "4", ("R3_2",)), ("review", "5", ("R4_3", "R4_5")), ("review", "6", ("R4_2", "R4_6"))])
            self.assertEqual(progress.export_summary(db, self.aid, responses), {"current": False, "created_at": None})
        self.frq.export(self.aid, "csv")
        plan, responses, *_ = self.read()
        with connect(self.path) as db:
            self.assertTrue(progress.export_summary(db, self.aid, responses)["current"])
        self.decide("q20", "A", ["R3_2"])  # a new decision ID: the export snapshot names decision IDs, so it is not current any more
        plan, responses, *_ = self.read()
        with connect(self.path) as db:
            self.assertFalse(progress.export_summary(db, self.aid, responses)["current"])

    def test_an_exports_ids_are_read_once_per_catalog_and_a_new_export_is_read_afresh(self):
        # Exports are immutable and retained (frq_export_immutable, frq_export_retained): the workflow reads each export's IDs once.
        self.frq.export(self.aid, "csv")
        plan, responses, *_ = self.read()
        catalog, walked = str(self.frq.path), []
        with connect(self.path) as db:
            db.set_trace_callback(lambda sql: walked.append(sql) if "json_each" in sql else None)
            found = progress.export_summary(db, self.aid, responses, catalog=catalog)
            self.assertEqual((found["current"], walked), (True, []))  # the workflow's read above already read it
            self.assertEqual(progress.export_summary(db, self.aid, responses), found)  # without the path: read afresh, the same
            self.assertEqual(len(walked), 1)
        self.decide("q20", "A", ["R3_2"])
        plan, responses, *_ = self.read()
        with connect(self.path) as db:
            self.assertFalse(progress.export_summary(db, self.aid, responses, catalog=catalog)["current"])
        self.frq.export(self.aid, "audit")  # a new export is another ID, read afresh
        plan, responses, *_ = self.read()
        with connect(self.path) as db:
            self.assertTrue(progress.export_summary(db, self.aid, responses, catalog=catalog)["current"])
            # Encoded once: each stored checksum is the digest of the stored snapshot's own bytes.
            for text, sha in db.execute("SELECT snapshot_json,sha256 FROM frq_exports"):
                self.assertEqual(sha, digest(text.encode()))
        # Least recently read first out, never more than the bound: with room for two, the export read again stays.
        self.addCleanup(progress._EXPORTED.clear)
        with connect(self.path) as db, patch.object(progress, "EXPORTED_MAX", 2):
            progress._EXPORTED.clear()
            del walked[:]
            db.set_trace_callback(lambda sql: walked.append(sql) if "json_each" in sql else None)
            for key in ("a", "b", "a", "c", "a"):
                self.assertTrue(progress.export_summary(db, self.aid, responses, catalog=key)["current"])
            self.assertEqual((len(walked), [k[0] for k in progress._EXPORTED]), (3, ["c", "a"]))

    def test_a_drill_down_reads_only_its_own_responses_and_finds_what_the_whole_read_finds(self):
        from grading.certification import CertificationService
        self.send()
        self.sender.drain()
        self.decide("q20", "A", ["R3_2"])  # the same lines approved again: `changed` on Gradescope, so the receipts are read again
        plan, responses, *_ = self.read()
        q20 = [r for r in responses if r["question_id"] == self.q[("q20", "A")]]
        with connect(self.path) as db:
            for group in (responses, [r for r in responses if r["enrollment_id"] == self.enrollment], q20, []):
                only = [r["id"] for r in group]
                rows = progress.decision_rows(db, self.aid, group)
                self.assertEqual(progress.decision_rows(db, self.aid, group, only=only), rows)
                self.assertEqual(GradescopeSender._grade_coverage(db, self.aid, group, only=only), GradescopeSender._grade_coverage(db, self.aid, group))
                self.assertEqual(GradescopeSender._grade_coverage(db, self.aid, group, decisions=rows), GradescopeSender._grade_coverage(db, self.aid, group))
                self.assertEqual(CertificationService._coverage(db, self.aid, group, rows, only=only), CertificationService._coverage(db, self.aid, group, rows))
            self.assertEqual(list(GradescopeSender._grade_coverage(db, self.aid, q20, only=[r["id"] for r in q20]).values()), ["changed"])

    def test_coverage_detail_totals_agree_with_coverage_and_see_same_content(self):
        def both():
            plan, responses, participants, collection, _ = self.read()
            with connect(self.path) as db:
                return GradescopeSender._coverage(db, self.aid, responses), GradescopeSender.coverage_detail(db, self.aid, responses, participants)
        coverage, detail = both()
        self.assertEqual((coverage["grades"]["final"], detail["totals"]["final"], detail["totals"]["not_sent"]), (4, 4, 4))
        self.assertEqual(len(detail["uncovered"]), 4)
        self.assertEqual(detail["unsendable"], {})  # imported from Gradescope's export
        self.assertEqual(len(detail["papers_missing"]), 2)
        self.send()
        self.sender.drain()
        coverage, detail = both()
        self.assertEqual(coverage["grades"], {"final": 4, "sent": 4, "changed": 0, "not_sent": 0})
        self.assertEqual((detail["totals"]["sent"], detail["uncovered"], detail["papers_missing"]), (4, [], []))
        self.assertEqual({k: v["paper"] for k, v in detail["students"].items()}, {f"{self.section['id']}:{e}": True for e in (self.enrollment, self.second)})
        # The same lines approved again: `_coverage` says changed (another decision), the detail knows it is the same grade.
        certified = self.certify()
        self.decide("q20", "A", ["R3_2"])
        coverage, detail = both()
        self.assertEqual((coverage["grades"]["changed"], coverage["grades"]["sent"]), (1, 3))
        q20 = self.q[("q20", "A")]
        self.assertEqual((detail["questions"][q20]["same_content"], detail["totals"]["sent"], detail["uncovered"]), (1, 4, []))
        self.assertEqual(self.state()["id"], certified)
        self.assertEqual(self.state()["state"], "current")  # and a send after certifying reopens nothing
        # Other lines: really changed, and uncovered.
        self.decide("q20", "A", ["R3_3"])
        coverage, detail = both()
        self.assertEqual(detail["questions"][q20]["changed"], 1)
        self.assertEqual(len(detail["uncovered"]), 1)

    def test_a_question_whose_rubric_was_not_imported_can_never_be_sent(self):
        plan, responses, participants, *_ = self.read()
        q20 = self.q[("q20", "A")]
        with connect(self.path) as db:
            real = GradescopeSender.coverage_detail(db, self.aid, responses, participants)
            self.assertEqual(real["never"], {"grades": {}, "papers": {}})
        from grading import gradescope_sync
        original = gradescope_sync.rubric_numbers
        gradescope_sync.rubric_numbers = lambda rubric: None
        try:
            with connect(self.path) as db:
                detail = GradescopeSender.coverage_detail(db, self.aid, responses, participants)
        finally:
            gradescope_sync.rubric_numbers = original
        self.assertIn(q20, detail["unsendable"])
        self.assertEqual(detail["questions"][q20]["unsendable"], detail["unsendable"][q20])
        self.assertEqual(len(detail["never"]["grades"]), 4)



class InboxCountTests(unittest.TestCase):
    def test_first_pages_waiting_count_each_registered_one_once(self):
        """missed:bugs-backend[2]: every registration comes from a plan, so a registered first page was subtracted twice."""
        from types import SimpleNamespace
        from grading.certification import CertificationService
        found = {"covers": 3, "planned": 1, "registered": 1, "not_cover": 0}
        unlabeled = SimpleNamespace(status=lambda db, message_id: found)
        service = CertificationService(SimpleNamespace(exams=SimpleNamespace(scans=SimpleNamespace(unlabeled=unlabeled)), rosters=None, path=None), None, None)
        state = {"details": {"scans": [{"message_id": "m1", "job_id": "j1", "inbox_ready": True}]}, "workflow": {"awaiting_review": 0}, "bubble": None}
        self.assertEqual(service._inbox(None, state)["unregistered"], 2)
        found.update(covers=10, planned=5, registered=5, not_cover=1)
        self.assertEqual(service._inbox(None, state)["unregistered"], 4)  # the scan overview's own count: 10 - 1 - 5


# ----- part two: the service, its API and the gradebook's certified source (role A2) -------------------------------------------
CAROL = ["", "Example, Carol", "234567890", "Registered", "MATH", "U1", "", "N", "", "", "", "carol@example.test"]
HEADERS = {"X-Teaching-Request": "1"}
COUNTED = ("frq_certifications", "frq_certification_students", "gradebook_setups", "gradebook_requests", "gradebook_scores", "frq_events",
           "exam_participants")


class Certify(graded.GradescopeCase):
    """The graded workspace with FRQ grading its one part (the fixture's Exams-page rubric is not part of these tests)."""

    def setUp(self):
        super().setUp()
        original = sources._links

        def links(db, term_id, assignment_id=None):
            found = original(db, term_id, assignment_id)
            for value in found.values():
                value["parts"] = [part for part in value["parts"] if part != "exams" and part in self.parts]
            return found
        self.parts = ("frq",)
        self.enterContext(patch("gradebook.sources._links", side_effect=links))
        self.service = self.app.extensions["certification"]
        self.gradebook = self.app.extensions["gradebook"]
        self.row1, self.row2 = f"{self.section['id']}:{self.enrollment}", f"{self.section['id']}:{self.second}"

    # --- helpers ------------------------------------------------------------------------------------------------------------
    def api(self, method, path, body=None, status=200):
        response = getattr(self.client, method)(f"/api/grading/assignments/{self.aid}/certification{path}", json=body, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def preview(self, **body):
        return self.api("post", "/preview", body)

    def body(self, preview, reason="Not needed on Gradescope for this test.", **extra):
        acknowledged = {c["id"]: c["digest"] for c in preview["confirm"] if c.get("requires") == "tick"}
        decisions = {("written" if c["id"] == "gradescope_written" else "sheets"): {"decision": "do_not_send", "reason": reason}
                     for c in preview["confirm"] if c.get("requires") == "decision"}
        return {"request_id": str(uuid.uuid4()), "fingerprint": preview["fingerprint"], "confirmation": "certify", "acknowledged": acknowledged,
                "gradescope": decisions, "gradebook": {}, **extra}

    def certify(self, preview=None, status=201, **extra):
        preview = preview or self.preview()
        return self.api("post", "", self.body(preview, **extra), status)

    def counts(self):
        with connect(self.path) as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COUNTED}

    def participants(self):
        with connect(self.path) as db:
            return [tuple(r) for r in db.execute("SELECT * FROM exam_participants ORDER BY section_id,enrollment_id")]

    def add_carol(self, sync=True):
        """A third student joins the roster after the exam: first a roster addition, then (synced) a participant with no paper."""
        group = self.client.get("/api/state").get_json()["course_groups"][0]
        from test_rosters import csv_bytes, STUDENTS
        import io
        result = self.client.post(f"/api/course-groups/{group['id']}/sections/501/roster", data={"file": (io.BytesIO(csv_bytes([*STUDENTS, CAROL])), "roster.csv")},
                                  headers=HEADERS)
        self.assertIn(result.status_code, (200, 201), result.get_json())
        with connect(self.path) as db:
            self.carol = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id NOT IN (?,?)",
                                    (self.section["id"], self.enrollment, self.second)).fetchone()[0]
        self.row3 = f"{self.section['id']}:{self.carol}"
        if sync:
            self.exams.sync_roster(self.event["id"], {"version": self.exams.get(self.event["id"])["version"]})

    def cells(self):
        with connect(self.path) as db:
            return {f"{r['section_id']}:{r['enrollment_id']}": (r["status"], r["points"], r["source"]) for r in db.execute(
                """SELECT g.* FROM gradebook_scores g WHERE g.seq=(SELECT MAX(n.seq) FROM gradebook_scores n WHERE n.term_id=g.term_id AND n.item_id=g.item_id
                   AND n.section_id=g.section_id AND n.enrollment_id=g.enrollment_id)""")}

    def setup(self):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM gradebook_setups WHERE term_id=? ORDER BY revision DESC LIMIT 1", (self.term_id,)).fetchone()
            return (row["revision"], json.loads(row["setup_json"])) if row else (None, None)

    def state(self):
        return self.frq.assignment(self.aid)["workflow"]["certification"]

    def name(self, row):
        """The roster name of a row (the fixture's enrollment IDs are random, so which of Alice and Bob is first varies)."""
        return next(s["name"] for s in self.service.view(self.aid)["students"] if s["row"] == row)

    def assertNoUin(self, value):
        text = json.dumps(value)
        for uin in list(self.uins.values()) + ["234567890"]:
            self.assertNotIn(uin, text)

    # --- the page and the drill-downs ------------------------------------------------------------------------------------------
    def test_the_page_names_students_counts_their_grades_and_never_a_uin(self):
        view = self.api("get", "")
        self.assertEqual((view["plan_id"], view["parts"], view["possible"]), (self.aid, ["frq"], {"frq": "18", "total": "18"}))
        self.assertEqual(sorted(s["name"] for s in view["students"]), ["Example, Alice", "Example, Bob"])
        alice = next(s for s in view["students"] if s["row"] == self.row1)
        self.assertEqual((alice["exam_version"], alice["status"], alice["frq"]["state"], alice["total"]["earned"], alice["scores"]),
                         ("A", "complete", "complete", "10", ["4", "6"]))
        self.assertEqual(type(alice["participant_version"]), int)  # the Exams page's PATCH needs the participant row's own version (C12)
        self.assertEqual(len(view["questions"]), 4)
        self.assertEqual({q["version"] for q in view["questions"]}, {"A", "B"})
        self.assertEqual(view["stats"]["total"]["n"], 2)
        self.assertEqual(view["certification"]["state"], "none")
        self.assertTrue(all(c.get("provisional") for c in view["checks"]))
        self.assertEqual(view["counts"]["participants"], 2)
        self.assertNoUin(view)

    def test_question_and_student_drill_downs(self):
        q20 = self.q[("q20", "A")]
        found = self.api("get", f"/questions/{q20}")
        self.assertEqual((found["question"]["max"], [r["id"] for r in found["question"]["rubric"]], found["question"]["rubric"][1]["number"]),
                         ("10", ["R3_1", "R3_2", "R3_3", "R3_4"], "3.2"))
        self.assertEqual([(r["name"], r["score"], r["decided"]["kind"], r["decided"]["status"], r["selected"]) for r in found["responses"]],
                         [(self.name(self.row1), "4", "instructor", "approved", ["R3_2"])])
        self.assertTrue(found["responses"][0]["overridden"])  # the proposal ticked R3_1; the instructor chose R3_2
        self.assertEqual(next(line for line in found["lines"] if line["id"] == "R3_2")["count"], 1)
        student = self.api("get", f"/students/{self.section['id']}/{self.enrollment}")
        self.assertEqual((student["student"]["name"], student["student"]["exam_version"], len(student["questions"])), (self.name(self.row1), "A", 2))
        self.assertEqual(student["parts"]["frq"], {"final": 2, "expected": 2, "earned": "10", "possible": "18"})
        first = next(q for q in student["questions"] if q["question_id"] == q20)
        self.assertEqual((first["score"], first["decided"]["kind"], first["decided"]["actor"], first["later_proposal"]), ("4", "instructor", "workspace", False))
        self.assertIn("names", first["flags"])
        self.assertNoUin(student)
        missing = self.client.get(f"/api/grading/assignments/{self.aid}/certification/students/{self.section['id']}/9999999999")
        self.assertEqual(missing.status_code, 404)

    def test_the_uin_search_answers_names_only(self):
        found = self.api("post", "/students/search", {"digits": self.uins[self.second]})
        self.assertEqual([m["name"] for m in found["matches"]][:1], [self.name(self.row2)])
        self.assertEqual(set(found["matches"][0]), {"row", "name", "section", "similarity"})
        self.assertNoUin(found)
        for bad in ({"digits": "123"}, {"digits": "12a45"}, {"digits": 1234}, {"uin": "1234"}):
            error = self.api("post", "/students/search", bad, 400)
            self.assertNotIn("123", error["error"])

    # --- preview and certify ---------------------------------------------------------------------------------------------------
    def test_the_preview_writes_nothing_and_a_zero_student_is_certified_into_a_new_column(self):
        self.add_carol(sync=False)
        blocked = self.preview()
        addition = next(c for c in blocked["checks"] if c["id"] == "roster_additions")
        self.assertEqual((addition["level"], [r["name"] for r in addition["rows"]]), ("block", ["Example, Carol"]))
        self.certify(blocked, status=409)
        self.add_carol()
        participants, before = self.participants(), self.counts()
        preview = self.preview()
        self.assertEqual(self.counts(), before)  # a preview writes nothing anywhere
        self.assertEqual([c["id"] for c in preview["checks"] if c["level"] == "block"], [])
        self.assertEqual(sorted(c["id"] for c in preview["confirm"]), ["gradebook_column", "gradescope_written", "zero_no_work"])
        zero = next(c for c in preview["confirm"] if c["id"] == "zero_no_work")
        self.assertEqual([r["name"] for r in zero["rows"]], ["Example, Carol"])
        self.assertEqual(preview["snapshot"]["students"], 3)
        self.assertEqual((preview["snapshot"]["scored"], preview["snapshot"]["zero"]), (2, 1))
        self.assertEqual(preview["gradebook"]["selected"], "create")
        self.assertEqual(preview["gradebook"]["counts"]["add"], 3)
        self.assertNoUin(preview)
        # The fingerprint the preview gave (the column still to be made, its ID a placeholder) is the one the apply recomputes (C2).
        done = self.certify(preview)
        self.assertFalse(done["replay"])
        self.assertNotIn("view", done)  # the page reads its new state with its own GET (P1)
        self.assertEqual(self.api("get", "")["certification"]["state"], "current")
        revision, setup = self.setup()
        self.assertEqual(revision, 1)
        column = setup["items"][0]
        self.assertEqual((column["title"], column["points"], column["link"]["parts"]), ("Exam 1", "18", ["frq"]))
        cells = self.cells()
        self.assertEqual({row: cells[row] for row in (self.row1, self.row2, self.row3)},
                         {self.row1: ("scored", "10", "grading"), self.row2: ("scored", "7", "grading"), self.row3: ("scored", "0", "grading")})
        with connect(self.path) as db:
            certification = dict(db.execute("SELECT * FROM frq_certifications").fetchone())
            rows = {f"{r['section_id']}:{r['enrollment_id']}": dict(r) for r in db.execute("SELECT * FROM frq_certification_students")}
            request = dict(db.execute("SELECT * FROM gradebook_requests").fetchone())
            event = db.execute("SELECT detail_json FROM frq_events WHERE kind='grades_certified'").fetchone()
        self.assertEqual((rows[self.row3]["status"], rows[self.row3]["reason"], rows[self.row3]["earned"], rows[self.row3]["possible"]), ("zero", "no_paper", "0", "18"))
        self.assertEqual((rows[self.row1]["status"], rows[self.row1]["exam_version"]), ("scored", "A"))
        frq = json.loads(rows[self.row1]["parts_json"])["frq"]
        self.assertEqual(sorted(q["decision"].split(":")[0] for q in frq["questions"].values()), ["review", "review"])
        self.assertEqual(request["kind"], "pull")
        self.assertEqual(json.loads(request["summary_json"])["certification_id"], certification["id"])
        receipt = json.loads(certification["gradebook_json"])
        self.assertEqual((receipt["item_id"], receipt["created_column"], receipt["gradebook_request_id"], receipt["written"]),
                         (column["id"], True, request["id"], 3))
        self.assertEqual(json.loads(certification["gradescope_json"])["written"]["decision"], "do_not_send")
        self.assertEqual(json.loads(event[0])["zero"], 1)
        self.assertEqual(self.participants(), participants)  # certifying never writes `exam_participants` ("No scan is not an inferred absence")
        self.assertNoUin(certification)
        self.assertEqual(self.state()["state"], "current")

    def test_a_certify_needs_its_confirmation_its_digests_and_an_unchanged_preview(self):
        preview = self.preview()
        body = self.body(preview)
        for broken in ({k: v for k, v in body.items() if k != "confirmation"}, {**body, "request_id": "x"}, {**body, "extra": 1},
                       {**body, "gradescope": {"written": {"decision": "do_not_send", "reason": ""}}}):
            self.api("post", "", broken, 400)
        refused = self.api("post", "", {**body, "acknowledged": {"gradebook_column": "old"}}, 409)
        self.assertIn("Confirm", refused["error"])
        self.assertTrue(refused["checks"])
        sending = self.api("post", "", {**body, "gradescope": {"written": {"decision": "send", "reason": ""}}}, 409)
        self.assertIn("Send these to Gradescope first", sending["error"])
        self.decide("q20", "A", ["R3_3"])  # a grade changes after the preview
        self.assertIn("changed", self.api("post", "", body, 409)["error"])
        self.assertEqual(self.counts()["frq_certifications"], 0)
        self.certify()

    def test_a_block_refuses_certification(self):
        self.decide("q21", "B", ["R4_3"], status="draft")  # a draft takes the grade out of final
        preview = self.preview()
        self.assertIn("grading_unfinished", [c["id"] for c in preview["checks"] if c["level"] == "block"])
        refused = self.certify(preview, status=409)
        self.assertIn("Finish these", refused["error"])
        self.assertEqual(self.counts()["frq_certifications"], 0)

    def test_the_same_request_replays_and_another_body_under_its_id_is_refused(self):
        preview = self.preview()
        body = self.body(preview)
        first = self.api("post", "", body, 201)
        again = self.api("post", "", body, 200)
        self.assertTrue(again["replay"])
        self.assertEqual(again["certification"]["id"], first["certification"]["id"])
        self.assertEqual((self.counts()["frq_certifications"], self.counts()["gradebook_requests"]), (1, 1))
        self.api("post", "", {**body, "gradescope": {"written": {"decision": "do_not_send", "reason": "Another reason"}}}, 409)

    def test_approving_a_grade_unchanged_does_not_reopen_and_a_changed_grade_keeps_the_certified_value(self):
        def hint():
            links = self.client.get(f"/api/gradebook/terms/{self.term_id}", headers=HEADERS).get_json()["links"]
            return next(link["certification"] for link in links.values() if link.get("certification"))["may_have_changed"]
        self.certify()
        certified = self.cells()[self.row1]
        self.assertFalse(hint())
        self.decide("q20", "A", ["R3_2"])  # the same lines again: a new decision, the same grade (C7)
        self.assertEqual(self.state()["state"], "current")
        self.assertFalse(hint())  # review F4: the gradebook's hint agrees with Grading
        self.decide("q20", "A", ["R3_3"])
        self.assertEqual((self.state()["state"], self.state()["changed"]), ("changed", ["grades"]))
        self.assertTrue(hint())
        view = self.api("get", "")
        self.assertTrue(next(s for s in view["students"] if s["row"] == self.row1)["changed_since"])
        # The gradebook keeps the certified value; its ordinary pull reads the certification, not live grading.
        self.assertEqual(self.cells()[self.row1], certified)
        item = self.setup()[1]["items"][0]["id"]
        pulled = self.gradebook.pull_preview(self.term_id, item, {})
        self.assertEqual(next(r for r in pulled["rows"] if r["row"] == self.row1)["action"], "same")
        # Certify again: the newer certification supersedes, records the change, and a pull preview made before it is stale.
        preview = self.preview()
        self.assertEqual([c["id"] for c in preview["checks"] if c["id"] == "changes_since"], ["changes_since"])
        self.certify(preview)
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        self.assertNotEqual(self.gradebook.pull_preview(self.term_id, item, {})["fingerprint"], pulled["fingerprint"])
        with connect(self.path) as db:
            first, second = [dict(r) for r in db.execute("SELECT id,supersedes_id FROM frq_certifications ORDER BY rowid")]
        self.assertEqual(second["supersedes_id"], first["id"])

    def participation(self, on=True):
        """The plan graded for participation or not (GRADING "GRADED FOR PARTICIPATION"), where it can be: written questions only."""
        from grading import participation
        with connect(self.path) as db:
            if participation.has_bubble_key(db, self.aid):
                self.skipTest("Graded for participation covers written questions only.")
        self.frq.set_participation(self.aid, {"enabled": on})

    def test_a_certification_graded_for_participation_stands_until_a_grade_or_the_setting_changes(self):
        """requirement, 2026-11-12, of the Take Home Assignment reading "Grades changed since the certification" from the moment it was
        certified: the certification recorded its grades as they count (full credit, with the line) and the check that it still
        stands read them as stored (GRADING "GRADED FOR PARTICIPATION" > THE CERTIFICATION COUNTS THEM TOO)."""
        self.participation()
        self.certify()
        self.assertEqual(self.cells()[self.row1], ("scored", "18", "grading"))
        self.assertEqual((self.state()["state"], self.state()["changed"]), ("current", []))
        self.participation(False)  # the line is gone: the gradebook keeps the full credit until it is certified again
        self.assertEqual((self.state()["state"], self.state()["changed"]), ("changed", ["grades"]))
        self.participation()
        self.assertEqual(self.state()["state"], "current")
        self.decide("q20", "A", ["R3_3"])  # 4 -> 2 of 10 as graded: full credit either way, by other lines
        self.assertEqual((self.state()["state"], self.state()["changed"]), ("changed", ["grades"]))
        self.certify()
        self.assertEqual(self.state()["state"], "current")

    def test_the_setting_turned_on_after_certifying_asks_to_certify_again(self):
        self.certify()
        self.assertEqual(self.cells()[self.row1], ("scored", "10", "grading"))
        self.participation()
        self.assertEqual((self.state()["state"], self.state()["changed"]), ("changed", ["grades"]))
        self.certify()
        self.assertEqual((self.state()["state"], self.cells()[self.row1]), ("current", ("scored", "18", "grading")))

    def test_a_hand_typed_value_stays_unless_replace_is_ticked(self):
        self.certify()
        item = self.setup()[1]["items"][0]["id"]
        cell = self.gradebook.view(self.term_id)["cells"][item][self.row1]
        self.gradebook.write_scores(self.term_id, {"request_id": str(uuid.uuid4()), "changes": [
            {"item_id": item, "row": self.row1, "base": cell["id"], "status": "scored", "points": "9.5"}]})
        self.decide("q20", "A", ["R3_3"])
        preview = self.preview()
        kept = next(c for c in preview["confirm"] if c["id"] == "gradebook_kept")
        self.assertEqual([r["row"] for r in kept["rows"]], [self.row1])
        self.certify(preview)
        self.assertEqual(self.cells()[self.row1], ("scored", "9.5", "instructor"))
        self.decide("q20", "A", ["R3_2"])
        preview = self.preview(gradebook={"replace": [self.row1]})
        self.assertEqual(preview["gradebook"]["counts"]["update"], 1)
        self.certify(preview, gradebook={"replace": [self.row1]})
        self.assertEqual(self.cells()[self.row1], ("scored", "10", "grading"))

    # --- DIFFERS FROM GRADING (GRADEBOOK, 2026-10-07): the gradebook names cells grading's final grade differs from ----------------
    def check(self, section=None, status=200):
        response = self.client.get(f"/api/gradebook/terms/{self.term_id}/grading-check" + (f"?section={section}" if section else ""), headers=HEADERS)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def test_a_grade_changed_after_certifying_differs_until_it_is_certified_again(self):
        self.assertEqual(self.check()["columns"], {})  # no column yet
        self.certify()
        item = self.setup()[1]["items"][0]["id"]
        self.assertEqual(self.check()["columns"], {})
        self.decide("q20", "A", ["R3_2"])  # the same lines again: a new decision, the same grade
        self.assertEqual(self.check()["columns"], {})
        self.decide("q20", "A", ["R3_3"])
        before = self.counts()
        column = self.check()["columns"][item]
        self.assertEqual(self.counts(), before)  # a check reads only
        self.assertEqual((column["fix"], column["plan_id"]), ("certify", self.aid))
        self.assertTrue(column["certified_at"])
        [row] = column["rows"]
        self.assertEqual((row["row"], row["section_id"], row["kind"], row["cell"]["points"], row["cell"]["source"], row["grading"]),
                         (self.row1, self.section["id"], "changed", "10", "grading", {"status": "scored", "points": "8"}))
        self.assertNoUin(column)
        # A section tab's check lists its own students and counts the others' (none here).
        scoped = self.check(self.section["id"])
        self.assertEqual((scoped["section_id"], scoped["columns"][item]["elsewhere"]), (self.section["id"], {"changed": 0, "typed": 0}))
        self.assertEqual([entry["row"] for entry in scoped["columns"][item]["rows"]], [self.row1])
        self.check("not-a-section", 400)
        # Certifying again records the grade: nothing differs.
        self.certify()
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        self.assertEqual(self.check()["columns"], {})

    def test_a_typed_value_grading_differs_from_is_named_without_a_fix(self):
        self.certify()
        item = self.setup()[1]["items"][0]["id"]
        cell = self.gradebook.view(self.term_id)["cells"][item][self.row1]
        typed = self.gradebook.write_scores(self.term_id, {"request_id": str(uuid.uuid4()), "changes": [
            {"item_id": item, "row": self.row1, "base": cell["id"], "status": "scored", "points": "9.5"}]})
        column = self.check()["columns"][item]
        self.assertIsNone(column["fix"])  # a pull or a certification keeps a typed value: nothing to bring in
        [row] = column["rows"]
        self.assertEqual((row["kind"], row["cell"]["points"], row["cell"]["source"], row["grading"]["points"]), ("typed", "9.5", "instructor", "10"))
        # Typing grading's own value: nothing differs.
        self.gradebook.write_scores(self.term_id, {"request_id": str(uuid.uuid4()), "changes": [
            {"item_id": item, "row": self.row1, "base": typed["cells"][item][self.row1]["id"], "status": "scored", "points": "10"}]})
        self.assertEqual(self.check()["columns"], {})

    def test_a_column_pulled_live_differs_once_grading_changes_after_the_pull(self):
        item = self.gradebook.link_assignment(self.term_id, {"assignment_id": self.assignment["id"], "request_id": str(uuid.uuid4())})["item_id"]
        self.assertEqual(self.check()["columns"], {})  # linked and never pulled: the Pull icon's mark says so, not this check

        def pull():
            preview = self.gradebook.pull_preview(self.term_id, item, {})
            self.gradebook.pull(self.term_id, item, {"fingerprint": preview["fingerprint"], "request_id": str(uuid.uuid4())})
        pull()
        self.assertEqual(self.cells()[self.row1], ("scored", "10", "grading"))
        self.assertEqual(self.check()["columns"], {})
        self.decide("q20", "A", ["R3_3"])
        column = self.check()["columns"][item]
        self.assertEqual((column["fix"], column["certified_at"]), ("pull", None))
        self.assertEqual([(entry["row"], entry["kind"], entry["grading"]["points"]) for entry in column["rows"]], [(self.row1, "changed", "8")])
        pull()
        self.assertEqual(self.check()["columns"], {})

    # --- certify just this change (a regrade request's Apply, REGRADES 2026-10-07) ------------------------------------------------
    def sent_and_certified(self):
        self.send()
        self.sender.drain()
        self.certify()
        from grading.certification_change import OneChange
        return OneChange(self.service)

    def send_one(self, key="q20", letter="A"):
        rid = self.response(key, letter)["id"]
        view = self.sender.preview(self.aid, {"only_responses": [rid]})
        self.sender.send(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {},
                                    "only_responses": [rid]})
        self.sender.drain()

    def amend(self, change, check, status=None, **extra):
        body = {"request_id": str(uuid.uuid4()), "confirmation": "certify-change", "fingerprint": check["fingerprint"],
                "response_id": self.response("q20", "A")["id"], **extra}
        return change.amend(self.aid, body)

    def test_one_changed_grade_is_certified_alone_and_writes_only_its_cell(self):
        change = self.sent_and_certified()
        before, other = self.cells()[self.row1], self.cells()[self.row2]
        self.assertEqual(before, ("scored", "10", "grading"))
        self.decide("q20", "A", ["R3_3"])  # 4 -> 2
        rid = self.response("q20", "A")["id"]
        check = change.check(self.aid, rid)
        self.assertEqual((check["possible"], check["from"], check["to"], check["state"], check["reasons"]), (True, "4", "2", "changed", []))
        with self.assertRaisesRegex(Conflict, "Send this grade to Gradescope first"):
            self.amend(change, check)
        self.send_one()
        counts = self.counts()
        done = self.amend(change, check, message_id="message-1")
        self.assertEqual(done["gradebook"]["written"], 1)
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        self.assertEqual(self.cells()[self.row2], other)
        self.assertEqual(self.state()["state"], "current")
        after = self.counts()
        self.assertEqual((after["frq_certifications"] - counts["frq_certifications"], after["gradebook_scores"] - counts["gradebook_scores"],
                          after["gradebook_requests"] - counts["gradebook_requests"]), (1, 1, 1))
        with connect(self.path) as db:
            first, second = [dict(r) for r in db.execute("SELECT * FROM frq_certifications ORDER BY rowid")]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_certification_students WHERE certification_id=?", (second["id"],)).fetchone()[0], 2)
            import certified_archive
            archived = next(iter(certified_archive.certified(db).values()))
        self.assertEqual((second["amends_id"], second["supersedes_id"]), (first["id"], first["id"]))
        self.assertEqual(json.loads(second["summary_json"])["amends"]["message_id"], "message-1")
        self.assertEqual((archived["certification_id"], archived["certified_at"]), (second["id"], first["created_at"]))
        history = self.api("get", "")["history"]
        self.assertEqual(next(h for h in history if h["id"] == second["id"])["amends"]["to"], "2")
        # Certified: the step reads done.
        self.assertTrue(change.check(self.aid, rid)["done"])

    def test_one_changed_grade_of_a_plan_graded_for_participation_is_certified_alone(self):
        """The grade as certified is read with the line it was certified with, as the grade now is: full credit before and after, and
        no other grade reads changed (GRADING "GRADED FOR PARTICIPATION" > THE CERTIFICATION COUNTS THEM TOO)."""
        self.participation()
        change = self.sent_and_certified()
        rid = self.response("q20", "A")["id"]
        self.assertTrue(change.check(self.aid, rid)["done"])
        self.decide("q20", "A", ["R3_3"])  # 4 -> 2 of 10 as graded
        check = change.check(self.aid, rid)
        self.assertEqual((check["possible"], check["from"], check["to"], check["same"], check["state"], check["reasons"]),
                         (True, "10", "10", False, "changed", []))
        self.send_one()
        self.amend(change, check)
        self.assertEqual((self.state()["state"], self.cells()[self.row1]), ("current", ("scored", "18", "grading")))
        self.assertTrue(change.check(self.aid, rid)["done"])
        # Certified before the setting was turned on, the grade as certified is the stored one.
        self.participation(False)
        self.certify()
        self.participation()
        self.assertEqual(change.check(self.aid, rid)["from"], "2")

    def test_the_same_request_replays(self):
        change = self.sent_and_certified()
        self.decide("q20", "A", ["R3_3"])
        check = change.check(self.aid, self.response("q20", "A")["id"])
        self.send_one()
        body = {"request_id": str(uuid.uuid4()), "confirmation": "certify-change", "fingerprint": check["fingerprint"],
                "response_id": self.response("q20", "A")["id"]}
        first = change.amend(self.aid, body)
        counts = self.counts()
        again = change.amend(self.aid, body)
        self.assertEqual((again["replay"], again["certification"]["id"]), (True, first["certification"]["id"]))
        self.assertEqual(self.counts(), counts)
        with self.assertRaisesRegex(Conflict, "different certification"):
            change.amend(self.aid, {**body, "replace": True})

    def test_another_changed_grade_never_stops_this_one_and_keeps_waiting_itself(self):
        """A REGRADE NEVER ASKS TO CERTIFY AGAIN (requirement, 2026-11-16): another grade changed since certifying, yet this one goes to
        its cell; the other's row stays exactly as certified and the plan still reads changed (markers carried)."""
        change = self.sent_and_certified()
        other = self.cells()[self.row2]
        self.decide("q20", "A", ["R3_3"])
        self.decide("q21", "B", ["R4_3"])
        check = change.check(self.aid, self.response("q20", "A")["id"])
        self.assertEqual((check["possible"], check["reasons"]), (True, []))
        self.send_one()
        self.send_one("q21", "B")
        done = self.amend(change, check)
        self.assertEqual((done["markers"], done["gradebook"]["written"]), ("carried", 1))
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))
        self.assertEqual(self.cells()[self.row2], other)
        self.assertEqual(self.state()["state"], "changed")
        with connect(self.path) as db:
            before, after = [dict(r) for r in db.execute("SELECT * FROM frq_certifications ORDER BY rowid")][-2:]
            rows = lambda cid: {r[0]: tuple(r[1:]) for r in db.execute("SELECT enrollment_id,status,earned,parts_json FROM frq_certification_students WHERE certification_id=?", (cid,))}
            self.assertEqual(rows(after["id"])[self.second], rows(before["id"])[self.second])
            self.assertNotEqual(rows(after["id"])[self.enrollment], rows(before["id"])[self.enrollment])
        amends = json.loads(after["summary_json"])["amends"]
        self.assertEqual((amends["markers"], amends["others"][0]), ("carried", "other grades"))
        self.assertTrue(change.check(self.aid, self.response("q20", "A")["id"])["same"])
        # The other grade, a regrade of its own, goes to its cell the same way, after the first one moved the latest certification.
        later = change.check(self.aid, self.response("q21", "B")["id"])
        self.assertTrue(later["possible"], later)
        self.amend(change, later, response_id=self.response("q21", "B")["id"])
        self.assertNotEqual(self.cells()[self.row2], other)
        # The instructor's Certify then records everything with live markers.
        self.certify()
        self.assertEqual(self.state()["state"], "current")

    def test_a_preview_made_before_another_certification_still_names_the_same_change(self):
        """A certification recorded between the preview and the step (another regrade, a late paper) leaves this grade's from and to
        as they were, so the same change is certified, not refused as "changed since the preview"."""
        change = self.sent_and_certified()
        self.decide("q20", "A", ["R3_3"])
        self.decide("q21", "B", ["R4_3"])
        first = change.check(self.aid, self.response("q20", "A")["id"])
        second = change.check(self.aid, self.response("q21", "B")["id"])
        self.send_one()
        self.send_one("q21", "B")
        self.amend(change, second, response_id=self.response("q21", "B")["id"])
        self.amend(change, first)
        self.assertEqual(self.cells()[self.row1], ("scored", "8", "grading"))

    def test_this_students_other_grade_changed_too_is_refused(self):
        change = self.sent_and_certified()
        self.decide("q20", "A", ["R3_3"])
        self.decide("q21", "A", ["R4_3"])
        check = change.check(self.aid, self.response("q20", "A")["id"])
        self.send_one()
        self.send_one("q21", "A")
        with self.assertRaisesRegex(Conflict, "This student's other grades changed"):
            self.amend(change, check)
        self.assertEqual(self.cells()[self.row1], ("scored", "10", "grading"))

    def test_a_grade_changed_after_the_preview_is_not_certified(self):
        change = self.sent_and_certified()
        self.decide("q20", "A", ["R3_3"])
        check = change.check(self.aid, self.response("q20", "A")["id"])
        self.decide("q20", "A", ["R3_4"])
        self.send_one()
        with self.assertRaisesRegex(Conflict, "changed since the preview"):
            self.amend(change, check)

    def test_a_typed_cell_stays_unless_replace_and_an_unchanged_grade_is_not_offered(self):
        change = self.sent_and_certified()
        rid = self.response("q20", "A")["id"]
        self.assertEqual((change.check(self.aid, rid)["possible"], change.check(self.aid, rid)["done"]), (False, True))
        item = self.setup()[1]["items"][0]["id"]
        cell = self.gradebook.view(self.term_id)["cells"][item][self.row1]
        self.gradebook.write_scores(self.term_id, {"request_id": str(uuid.uuid4()), "changes": [
            {"item_id": item, "row": self.row1, "base": cell["id"], "status": "scored", "points": "9.5"}]})
        self.decide("q20", "A", ["R3_3"])
        check = change.check(self.aid, rid)
        self.send_one()
        kept = self.amend(change, check)
        self.assertEqual((kept["gradebook"]["written"], kept["gradebook"]["kept"]), (0, 1))
        self.assertEqual(self.cells()[self.row1], ("scored", "9.5", "instructor"))
        self.assertEqual(self.state()["state"], "current")
        self.decide("q20", "A", ["R3_4"])
        check = change.check(self.aid, rid)
        self.send_one()
        self.amend(change, check, replace=True)
        self.assertEqual(self.cells()[self.row1][2], "grading")

    def test_nothing_certified_yet_is_not_offered(self):
        from grading.certification_change import OneChange
        check = OneChange(self.service).check(self.aid, self.response("q20", "A")["id"])
        self.assertEqual((check["state"], check["possible"]), ("none", False))
        self.assertIn("not certified yet", check["reasons"][0])

    def test_a_column_without_a_link_is_linked_and_one_linked_to_some_parts_takes_only_those(self):
        self.gradebook.save_setup(self.term_id, {"base_revision": None, "request_id": str(uuid.uuid4()), "setup": {"items": [
            {"title": "Exam 1 (typed)", "points": "18", "assignment_id": self.assignment["id"]}]}})
        preview = self.preview()
        self.assertEqual(preview["gradebook"]["selected"], "link:" + self.setup()[1]["items"][0]["id"])
        self.certify(preview)
        column = self.setup()[1]["items"][0]
        self.assertEqual((column["title"], column["link"]), ("Exam 1 (typed)", {"kind": "grading", "parts": ["frq"]}))
        self.assertEqual(self.cells()[self.row1], ("scored", "10", "grading"))
        # The plan gets a bubble part (a key and two scored sheets): the column links FRQ only (C10).
        self.parts = ("frq", "bubble")
        self.frq.save_bubble_key(self.aid, {"request_id": str(uuid.uuid4()), "base_revision": None,
                                            "defaults": {"points": "1", "scoring": "exact"},
                                            "versions": [{"letter": "A", "questions": [{"number": 1, "answer": "A"}, {"number": 2, "answer": "B"}]},
                                                         {"letter": "B", "questions": [{"number": 1, "answer": "C"}, {"number": 2, "answer": "D"}]}]})
        sheets = [{"sheet_id": f"s{i}", "job_id": "j", "page": i, "student": name, "section": 501, "enrollment_id": enrollment, "uin": "",
                   "marked_version": letter, "version": letter, "status": "scored", "reason": "", "earned": earned, "possible": "2", "wrong": [], "answers": []}
                  for i, (name, enrollment, letter, earned) in enumerate((("Example, Alice", self.enrollment, "A", "2"), ("Example, Bob", self.second, "B", "1")))]
        view = {"key": None, "sheets": sheets, "stats": {}, "waiting_scans": [], "counts": {}, "beyond_read": [], "read_questions": 2}
        original = self.frq._bubble_view

        def bubble(aid):
            plan, real = original(aid)
            return plan, {**view, "key": real["key"]}
        self.enterContext(patch.object(self.frq, "_bubble_view", side_effect=bubble))
        preview = self.preview()
        keys = [o["key"] for o in preview["gradebook"]["options"]]
        self.assertEqual(keys, ["parts:" + column["id"], "keep:" + column["id"]])
        kept = self.preview(gradebook={"column": {"action": "ok", "item_id": column["id"], "parts": "keep"}})
        self.assertEqual(kept["fingerprint"], preview["fingerprint"])  # the column chosen in the dialog needs no new fingerprint
        self.assertEqual(next(r for r in kept["gradebook"]["rows"] if r["row"] == self.row1)["proposed"], {"status": "scored", "points": "10"})
        # Each column has its own confirmations, listed with it: the dialog switches columns without a new preview.
        chosen = next(o for o in preview["gradebook"]["options"] if o["key"] == "keep:" + column["id"])
        self.assertEqual([c["digest"] for c in chosen["checks"] if c["id"] == "gradebook_column"],
                         [c["digest"] for c in kept["confirm"] if c["id"] == "gradebook_column"])
        self.certify(kept, gradebook={"column": {"action": "ok", "item_id": column["id"], "parts": "keep"}})
        self.assertEqual(self.setup()[1]["items"][0]["link"]["parts"], ["frq"])  # kept as it was
        self.assertEqual(self.cells()[self.row1], ("scored", "10", "grading"))
        self.decide("q20", "A", ["R3_2"])  # nothing changed; certify again with the parts changed to the certified ones
        preview = self.preview()
        self.certify(preview)
        self.assertEqual(self.setup()[1]["items"][0]["link"]["parts"], ["frq", "bubble"])
        self.assertEqual(self.cells()[self.row1], ("scored", "12", "grading"))

    # --- the gradebook column's category (rest-shell-1, certify-1) ---------------------------------------------------------------
    def weighted(self, items, categories=({"ref": "exams", "name": "Semester Exams", "weight": "60"}, {"ref": "other", "name": "Other", "weight": "0"})):
        self.gradebook.save_setup(self.term_id, {"base_revision": self.setup()[0], "request_id": str(uuid.uuid4()),
                                                 "setup": {"mode": "weighted", "categories": list(categories), "items": items}})
        return {c["name"]: c["id"] for c in self.setup()[1]["categories"]}

    def test_a_new_column_is_proposed_a_category_and_gets_the_one_chosen(self):
        # An earlier exam linked to grading proposes its category for "Exam 1" (the title's first word); a weighted gradebook.
        earlier = self.documents.create_assignment(self.assignment["definition_id"], "exam-zero", "Exam 0")
        ids = self.weighted([{"title": "Exam 0", "points": "18", "category_id": "exams", "assignment_id": earlier["id"], "link": {"kind": "grading", "parts": ["frq"]}}])
        preview = self.preview()
        book = preview["gradebook"]
        self.assertEqual(book["selected"], "create")  # no column has the assignment's title, so a new column is the default
        self.assertEqual((book["mode"], book["proposed_category_id"]), ("weighted", ids["Semester Exams"]))
        self.assertEqual([(c["id"], c["name"], c["counted"]) for c in book["categories"]],
                         [(ids["Semester Exams"], "Semester Exams", True), (ids["Other"], "Other", False), (None, "Uncategorized", False)])
        create = next(o for o in book["options"] if o["key"] == "create")
        self.assertIsNone(create["item"]["category_id"])  # the request named none: a proposal is not a choice
        column = next(c for c in create["checks"] if c["id"] == "gradebook_column")
        self.assertEqual(column["detail"], "18 points · Free response. Uncategorized: it will not count toward Overall until it has a category.")
        self.assertEqual(column["category_details"], {ids["Semester Exams"]: "18 points · Free response · Semester Exams",
                                                      ids["Other"]: "18 points · Free response · Other", "": "18 points · Free response · Uncategorized"})
        named = self.preview(gradebook={"category_id": ids["Semester Exams"]})
        named_column = next(c for c in named["confirm"] if c["id"] == "gradebook_column")
        self.assertEqual(named_column["detail"], "18 points · Free response · Semester Exams")
        # The category is in no fingerprint and no digest: the dialog chooses it without a new preview, and certifying records it.
        self.assertEqual((named["fingerprint"], named_column["digest"]), (preview["fingerprint"], column["digest"]))
        self.certify(preview, gradebook={"category_id": ids["Semester Exams"]})
        made = next(i for i in self.setup()[1]["items"] if i.get("assignment_id") == self.assignment["id"])
        self.assertEqual((made["title"], made["category_id"], made["link"]), ("Exam 1", ids["Semester Exams"], {"kind": "grading", "parts": ["frq"]}))
        with connect(self.path) as db:
            confirmations = json.loads(db.execute("SELECT confirmations_json FROM frq_certifications").fetchone()[0])
        self.assertEqual(confirmations["gradebook_column"]["category_id"], ids["Semester Exams"])

    def test_a_hand_made_column_with_the_title_is_offered_and_proposes_its_category(self):
        ids = self.weighted([{"title": "Homework 1", "points": "5"}, {"title": "exam  1", "points": "18", "category_id": "other"}])
        hand = {i["title"]: i["id"] for i in self.setup()[1]["items"]}
        book = self.preview()["gradebook"]
        # The one column with the assignment's title (spaces and case aside) first, the default (requirement, 2026-10-02); then a new
        # column, then every other unlinked column in the setup's order.
        self.assertEqual([o["key"] for o in book["options"]], ["use:" + hand["exam  1"], "create", "use:" + hand["Homework 1"]])
        self.assertEqual(book["selected"], "use:" + hand["exam  1"])
        self.assertEqual(book["proposed_category_id"], ids["Other"])  # the same title, spaces and case aside
        self.assertEqual(next(o for o in book["options"] if o["key"] == "use:" + hand["exam  1"])["item"]["category_id"], ids["Other"])

    # --- the one same-titled column is the default (requirement, 2026-10-02) ----------------------
    def test_the_one_same_titled_column_is_the_default_and_certifying_records_there(self):
        ids = self.weighted([{"title": "Homework 1", "points": "5"}, {"title": "EXAM  1", "points": "18", "category_id": "exams"}])
        hand = next(i["id"] for i in self.setup()[1]["items"] if i["title"] == "EXAM  1")
        # A value typed in that column (the instructor's own) stays unless Replace is ticked: the default option asks about it.
        self.gradebook.write_scores(self.term_id, {"request_id": str(uuid.uuid4()), "changes": [
            {"item_id": hand, "row": self.row1, "base": None, "status": "scored", "points": "9.5"}]})
        preview = self.preview()
        book = preview["gradebook"]
        self.assertEqual((book["selected"], book["column"], book["item"]["title"], book["item"]["category_id"]),
                         ("use:" + hand, {"action": "use", "item_id": hand}, "EXAM  1", ids["Semester Exams"]))
        self.assertEqual([o["key"] for o in book["options"]][:2], ["use:" + hand, "create"])
        self.assertEqual(sorted(c["id"] for c in preview["confirm"] if c["id"].startswith("gradebook_")), ["gradebook_column", "gradebook_kept"])
        self.assertEqual(next(c for c in preview["confirm"] if c["id"] == "gradebook_column")["title"], "Record in the column “EXAM  1”")
        # The order is not fingerprinted: choosing a new column in the dialog needs no new preview, and the default is what a request
        # naming no column gets.
        created = self.preview(gradebook={"column": {"action": "create"}})
        self.assertEqual((created["fingerprint"], created["gradebook"]["selected"]), (preview["fingerprint"], "create"))
        items = len(self.setup()[1]["items"])
        self.certify(preview, gradebook={"column": {"action": "use", "item_id": hand}, "category_id": ids["Semester Exams"]})
        revision, setup = self.setup()
        column = next(i for i in setup["items"] if i["id"] == hand)
        self.assertEqual(len(setup["items"]), items)  # no second "Exam 1"
        self.assertEqual((column["title"], column["category_id"], column["assignment_id"], column["link"]),
                         ("EXAM  1", ids["Semester Exams"], self.assignment["id"], {"kind": "grading", "parts": ["frq"]}))
        cells = self.cells()
        self.assertEqual((cells[self.row1], cells[self.row2]), (("scored", "9.5", "instructor"), ("scored", "7", "grading")))
        # Linked now: the next certification records in it without asking which column.
        self.assertEqual(self.preview()["gradebook"]["selected"], "ok:" + hand)

    def test_two_same_titled_columns_leave_a_new_column_the_default(self):
        self.weighted([{"title": "Exam 1", "points": "18", "category_id": "exams"}, {"title": "exam 1", "points": "18", "category_id": "other"}])
        hand = [i["id"] for i in self.setup()[1]["items"]]
        book = self.preview()["gradebook"]
        self.assertEqual([o["key"] for o in book["options"]], ["create", *("use:" + h for h in hand)])
        self.assertEqual(book["selected"], "create")

    def test_archived_and_linked_same_titled_columns_are_never_the_default(self):
        earlier = self.documents.create_assignment(self.assignment["definition_id"], "exam-zero", "Exam 0")
        self.weighted([{"title": "Exam 1", "points": "18", "category_id": "exams", "archived": True},
                       {"title": "Exam 1", "points": "18", "category_id": "exams", "assignment_id": earlier["id"], "link": {"kind": "grading", "parts": ["frq"]}},
                       {"title": "Homework 1", "points": "5"}])
        items = {(i["archived"], bool(i["link"])): i["id"] for i in self.setup()[1]["items"] if i["title"] == "Exam 1"}
        homework = next(i["id"] for i in self.setup()[1]["items"] if i["title"] == "Homework 1")
        book = self.preview()["gradebook"]
        self.assertEqual([o["key"] for o in book["options"]], ["create", "use:" + homework])
        self.assertEqual(book["selected"], "create")
        # A live unlinked "Exam 1" beside them is the only one that counts: it becomes the default.
        revision, setup = self.setup()
        self.gradebook.save_setup(self.term_id, {"base_revision": revision, "request_id": str(uuid.uuid4()),
                                                 "setup": {**setup, "items": [*setup["items"], {"title": " Exam 1", "points": "18"}]}})
        live = next(i["id"] for i in self.setup()[1]["items"] if i["title"] == "Exam 1" and i["id"] not in items.values())
        book = self.preview()["gradebook"]
        self.assertEqual([o["key"] for o in book["options"]], ["use:" + live, "create", "use:" + homework])
        self.assertEqual(book["selected"], "use:" + live)

    def test_points_mode_counts_every_category_and_proposes_nothing_without_a_match(self):
        book = self.preview()["gradebook"]
        self.assertEqual((book["mode"], book["categories"], book["proposed_category_id"]),
                         ("points", [{"id": None, "name": "Uncategorized", "weight": None, "counted": True}], None))
        column = next(c for c in next(o for o in book["options"] if o["key"] == "create")["checks"] if c["id"] == "gradebook_column")
        # No category in the gradebook: none is named (review nit), and the dialog has no category's words to choose among.
        self.assertEqual(column["detail"], "18 points · Free response")
        self.assertNotIn("category_details", column)

    def test_the_column_checks_no_longer_repeat_every_option_and_nothing_a_certify_names_moves(self):
        """missed:rest-shell[0](b): each option's column check carried the whole options list (6,400 entries on a term with 79
        unlinked columns). The same preview built the old way (every option listed in each column check) has the same fingerprint
        and the same digests: the list was never in a check's members."""
        self.weighted([{"title": f"iClicker {n}", "points": "1"} for n in range(40)])
        preview = self.preview()
        real = rules.check

        def old(id, *args, **kwargs):
            if id == "gradebook_column":
                kwargs["options"] = [{"column": o["column"], "title": o["key"], "counts": o["counts"]} for o in preview["gradebook"]["options"]]
            return real(id, *args, **kwargs)
        with patch.object(rules, "check", side_effect=old):
            before = self.preview()
        self.assertEqual(len(preview["gradebook"]["options"]), 41)
        self.assertEqual(before["fingerprint"], preview["fingerprint"])
        digests = lambda found: {o["key"]: [(c["id"], c.get("digest")) for c in o["checks"]] for o in found["gradebook"]["options"]}
        self.assertEqual(digests(before), digests(preview))
        self.assertEqual([(c["id"], c.get("digest")) for c in before["checks"]], [(c["id"], c.get("digest")) for c in preview["checks"]])
        self.assertTrue(all("options" in c for o in before["gradebook"]["options"] for c in o["checks"] if c["id"] == "gradebook_column"))
        self.assertFalse(any("options" in c for o in preview["gradebook"]["options"] for c in o["checks"]))
        self.assertLess(len(json.dumps(preview)) * 3, len(json.dumps(before)))
        self.certify(preview)  # the digests the dialog ticks are the ones the certify checks

    def test_a_left_out_dr_student_has_an_earlier_certified_value_cleared(self):
        self.add_carol()
        self.certify()
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))
        booking = {"booking_id": "b1", "section_id": self.section["id"], "enrollment_id": self.carol, "exam_type": "Exam 1", "date": "2099-01-05",
                   "start_time": "09:00", "end_time": "11:00", "length_minutes": 120, "campus": "Main"}
        with patch("grading.progress.dr_bookings", return_value=[booking]):
            self.assertIn("students", self.state()["changed"])  # a booking linked after certifying reopens the step (C6)
            preview = self.preview()
            ahead = next(c for c in preview["confirm"] if c["id"] == "dr_ahead")
            self.assertEqual(ahead["rows"][0]["dr"][0]["date"], "2099-01-05")
            self.assertEqual([r["row"] for r in preview["gradebook"]["cleared"]], [self.row3])
            self.certify(preview)
            with connect(self.path) as db:
                row = db.execute("SELECT status,reason,dr_date FROM frq_certification_students WHERE enrollment_id=? ORDER BY rowid DESC LIMIT 1",
                                 (self.carol,)).fetchone()
            self.assertEqual(tuple(row), ("left_out", "dr_ahead", "2099-01-05"))
            self.assertEqual(self.cells()[self.row3], ("cleared", None, "grading"))
            self.assertEqual(self.state()["state"], "current")

    def test_excused_students_are_excused_and_absent_ones_receive_0_when_confirmed(self):
        self.add_carol()
        person = next(p for p in self.exams.get(self.event["id"])["participants"] if p["enrollment_id"] == self.carol)
        self.exams.update_participant(self.event["id"], person["section_id"], self.carol, {"version": person["version"], "expectation": "absent", "reason": "Sick"})
        preview = self.preview()
        zero = next(c for c in preview["confirm"] if c["id"] == "zero_no_work")
        self.assertEqual(zero["rows"][0]["detail"], "Marked absent")
        self.certify(preview)
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))
        person = next(p for p in self.exams.get(self.event["id"])["participants"] if p["enrollment_id"] == self.carol)
        self.exams.update_participant(self.event["id"], person["section_id"], self.carol, {"version": person["version"], "expectation": "excused", "reason": "Dean"})
        preview = self.preview()
        self.assertNotIn("zero_no_work", [c["id"] for c in preview["confirm"]])
        self.certify(preview)
        self.assertEqual(self.cells()[self.row3], ("excused", None, "grading"))

    # --- absences beside zeros (requirement, 2026-10-02: display only; ABSENCES NO-GRADEBOOK stands) -------------------------------
    def absence(self, enrollment, first, last=None, status="pending"):
        return self.app.extensions["absences"].create({"request_id": str(uuid.uuid4()), "section_id": self.section["id"], "enrollment_id": enrollment,
                                                       "first_date": first, "last_date": last or first, "reason": "illness", "status": status})

    def absence_rows(self):
        with connect(self.path) as db:
            return [tuple(r) for table in ("absences", "absence_revisions") for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def test_the_students_who_receive_0_show_their_absences_and_nothing_a_certify_names_moves(self):
        self.add_carol()
        bare = self.preview()
        zero = next(c for c in bare["confirm"] if c["id"] == "zero_no_work")
        self.assertEqual([(r["name"], r["absences"]) for r in zero["rows"]], [("Example, Carol", [])])  # none recorded: an empty list
        self.absence(self.carol, "2026-09-25", status="not_excused")
        self.absence(self.carol, "2026-09-01", "2026-09-02", status="excused")
        self.absence(self.enrollment, "2026-09-25", status="excused")  # Alice has work: scored, never listed
        before, recorded = self.counts(), self.absence_rows()
        preview = self.preview()
        self.assertEqual((self.counts(), self.absence_rows()), (before, recorded))  # the preview writes nothing, absences included
        listed = next(c for c in preview["confirm"] if c["id"] == "zero_no_work")
        # The assessment has no date yet: each absence is listed, oldest first, without `covers`.
        self.assertEqual(listed["rows"][0]["absences"], [
            {"first_date": "2026-09-01", "last_date": "2026-09-02", "dates": "Tue, Sep 1 – Wed, Sep 2", "status": "excused"},
            {"first_date": "2026-09-25", "last_date": "2026-09-25", "dates": "Fri, Sep 25", "status": "not_excused"}])
        self.assertFalse([c["id"] for c in preview["checks"] if c["id"] != "zero_no_work" and any("absences" in r for r in c.get("rows") or [])])
        # Rows are in no digest: the fingerprint and every digest are what they were before any absence, and a certify named on the
        # earlier preview still records (nothing excused: Carol receives 0, the Exams page untouched).
        self.assertEqual(preview["fingerprint"], bare["fingerprint"])
        self.assertEqual([(c["id"], c.get("digest")) for c in preview["checks"]], [(c["id"], c.get("digest")) for c in bare["checks"]])
        participants = self.participants()
        self.certify(bare)
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))
        self.assertEqual((self.participants(), self.absence_rows()), (participants, recorded))

    def test_an_absence_is_marked_when_it_covers_the_day_it_was_given_in_the_students_section(self):
        # Not excused: Carol still receives 0 (an excused or undecided one over the day leaves her out, MAKEUP OWED below).
        self.add_carol()
        self.absence(self.carol, "2026-09-24", "2026-09-26", status="not_excused")
        self.absence(self.carol, "2026-09-29", status="not_excused")
        covers = lambda: [a.get("covers") for a in next(c for c in self.preview()["confirm"] if c["id"] == "zero_no_work")["rows"][0]["absences"]]
        self.assertEqual(covers(), [None, None])
        # A makeup sitting is not the date it was given; the main sitting is.
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Makeup", "kind": "makeup", "date": "2026-09-29"})
        self.assertEqual(covers(), [None, None])
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        self.assertEqual(covers(), [True, False])
        # A seating chart of the assignment naming the student's section gives that section's date (campus dates, compared as written).
        now = "2026-10-02T12:00:00+00:00"
        with connect(self.path) as db:
            db.execute("INSERT INTO rooms(id,name,created_at,updated_at) VALUES('room-x','Room X',?,?)", (now, now))
            db.execute("INSERT INTO seating_charts(id,assignment_id,term_id,room_id,exam_date,created_at,updated_at) VALUES('chart-x',?,?,'room-x','2026-09-29',?,?)",
                       (self.assignment["id"], self.term_id, now, now))
            db.execute("INSERT INTO seating_chart_sections VALUES('chart-x',?)", (self.section["id"],))
            db.commit()
        self.assertEqual(covers(), [False, True])

    def test_the_day_it_was_given_leaves_out_makeup_and_dr_charts_and_prefers_the_students_own_sitting(self):
        # Review of "Absences beside zeros": a makeup's or a DR exam's chart is not the day the section took it; a student's own
        # sitting on the Exams page (a makeup, say) is the day they were to take it; a withdrawn absence covers nothing.
        self.add_carol()
        self.absence(self.carol, "2026-09-25", status="not_excused")
        self.absence(self.carol, "2026-09-29", status="not_excused")
        self.absence(self.carol, "2026-09-30", status="withdrawn")
        covers = lambda: [a.get("covers") for a in next(c for c in self.preview()["confirm"] if c["id"] == "zero_no_work")["rows"][0]["absences"]]
        version = lambda: self.exams.get(self.event["id"])["version"]
        self.exams.save_sitting(self.event["id"], {"version": version(), "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        self.assertEqual(covers(), [True, False, None])  # withdrawn: no `covers` at all
        makeup = next(s["id"] for s in self.exams.save_sitting(self.event["id"], {"version": version(), "label": "Makeup", "kind": "makeup", "date": "2026-09-29"})["sittings"]
                      if s["kind"] == "makeup")
        now = "2026-10-02T12:00:00+00:00"
        with connect(self.path) as db:
            db.execute("INSERT INTO rooms(id,name,created_at,updated_at) VALUES('room-x','Room X',?,?)", (now, now))
            for chart, date, dr in (("chart-makeup", "2026-09-29", ""), ("chart-dr", "2026-09-28", "dr-1")):
                db.execute("INSERT INTO seating_charts(id,assignment_id,term_id,room_id,exam_date,dr_exam_key,created_at,updated_at) VALUES(?,?,?,'room-x',?,?,?,?)",
                           (chart, self.assignment["id"], self.term_id, date, dr, now, now))
                db.execute("INSERT INTO seating_chart_sections VALUES(?,?)", (chart, self.section["id"]))
            db.commit()
        self.exams.attach_chart(self.event["id"], {"version": version(), "chart_id": "chart-makeup", "sitting_id": makeup})
        self.assertEqual(covers(), [True, False, None])  # neither the makeup's chart nor the DR exam's moves the section's day
        # Carol's own sitting is the makeup: its date is hers.
        participant = next(p for p in self.exams.get(self.event["id"])["participants"] if p["enrollment_id"] == self.carol)
        self.exams.update_participant(self.event["id"], self.section["id"], self.carol, {"version": participant["version"], "sitting_id": makeup})
        self.assertEqual(covers(), [False, True, None])

    # --- makeup owed (requirement, 2026-10-07) --------------------
    def decide_absence(self, absence, status):
        return self.app.extensions["absences"].revise(absence["id"], {"base_revision": absence["revision"], "request_id": str(uuid.uuid4()),
                                                                      "status": status})

    def test_a_student_owed_a_makeup_waits_and_receives_0_once_the_absence_is_not_excused(self):
        self.add_carol()
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        absence = self.absence(self.carol, "2026-09-24", "2026-09-26")  # undecided, over the day it was given
        self.absence(self.enrollment, "2026-09-25", status="excused")  # Alice handed in her paper: scored, owes nothing
        preview = self.preview()
        self.assertNotIn("zero_no_work", [c["id"] for c in preview["confirm"]])
        waiting = next(c for c in preview["confirm"] if c["id"] == "makeup_ahead")
        self.assertEqual([(r["row"], r["detail"]) for r in waiting["rows"]],
                         [(self.row3, "Absence Thu, Sep 24 – Sat, Sep 26 waiting for your decision · 0 if not excused")])
        self.assertEqual((preview["snapshot"]["left_out"], preview["snapshot"]["left_out_makeup"]), (1, 1))
        recorded = self.absence_rows()
        self.certify(preview)
        self.assertNotIn(self.row3, self.cells())  # nothing recorded: the gradebook's empty cell waits (ZERO ONCE DUE)
        self.assertEqual(self.absence_rows(), recorded)  # the absence is only read
        with connect(self.path) as db:
            row = db.execute("SELECT status,reason,earned FROM frq_certification_students WHERE enrollment_id=? ORDER BY rowid DESC LIMIT 1", (self.carol,)).fetchone()
        self.assertEqual(tuple(row), ("left_out", "makeup", None))
        self.assertEqual((self.state()["state"], self.state()["left_out_makeup"]), ("current", 1))
        self.assertEqual(self.service.view(self.aid)["history"][0]["left_out_makeup"], 1)
        absence = self.decide_absence(absence, "excused")  # still owed: nothing reopens
        self.assertEqual(self.state()["state"], "current")
        self.decide_absence(absence, "not_excused")
        self.assertEqual((self.state()["state"], self.state()["changed"]), ("changed", ["makeup_ended"]))
        preview = self.preview()
        zero = next(c for c in preview["confirm"] if c["id"] == "zero_no_work")
        self.assertEqual([(r["row"], [a["status"] for a in r["absences"]]) for r in zero["rows"]], [(self.row3, ["not_excused"])])
        self.assertNotIn("makeup_ahead", [c["id"] for c in preview["confirm"]])
        self.certify(preview)
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))
        self.assertEqual(self.state()["state"], "current")

    def test_a_makeup_recorded_missed_receives_0_at_the_next_certify(self):
        # ABSENCES MAKEUPS "MISSED" (requirement, 2026-10-31).
        self.add_carol()
        self.exams.save_sitting(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "label": "Exam 1", "kind": "main", "date": "2026-09-25"})
        self.absence(self.carol, "2026-09-24", "2026-09-26", status="excused")
        self.certify(self.preview())
        self.assertEqual(self.state()["state"], "current")
        made = self.app.extensions["absences"].makeup_missed({"request_id": "missed-1", "missed": True, "event_id": self.event["id"],
                                                               "section_id": self.section["id"], "enrollment_id": self.carol})
        self.assertTrue(made["missed"])
        self.assertEqual((self.state()["state"], self.state()["changed"]), ("changed", ["makeup_ended"]))
        preview = self.preview()
        zero = next(c for c in preview["confirm"] if c["id"] == "zero_no_work")
        self.assertEqual([(r["row"], r["detail"]) for r in zero["rows"]], [(self.row3, "Missed the makeup")])
        self.assertNotIn("makeup_ahead", [c["id"] for c in preview["confirm"]])
        self.certify(preview)
        self.assertEqual(self.cells()[self.row3], ("scored", "0", "grading"))
        self.assertEqual(self.state()["state"], "current")

    def test_no_roster_payload_is_read_inside_the_certify_transaction(self):
        from grading import certification
        began = {"now": False}
        real_connect, real_files = certification.connect, self.rosters.files

        class Watched:
            def __init__(self, db):
                self.db = db

            def execute(self, sql, *args):
                if sql.strip().upper().startswith("BEGIN"):
                    began["now"] = True
                return self.db.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self.db, name)
        from contextlib import contextmanager

        @contextmanager
        def watched(path):
            with real_connect(path) as db:
                try:
                    yield Watched(db)
                finally:
                    began["now"] = False

        def files(*args, **kwargs):
            if began["now"]:
                raise AssertionError("A roster payload was read inside the certify transaction.")
            return real_files(*args, **kwargs)
        with patch.object(certification, "connect", side_effect=watched), patch.object(self.rosters, "files", side_effect=files):
            self.certify()
        self.assertEqual(self.counts()["frq_certifications"], 1)

    def test_the_fingerprint_does_not_move_while_training_examples_are_saved(self):
        preview = self.preview()
        waiting = next(c for c in preview["checks"] if c["id"] == "training_waiting")["count"]
        self.assertGreater(waiting, 0)
        self.frq.capture_training_one()
        again = self.preview()
        self.assertNotIn("training_waiting", [c["id"] for c in again["checks"]])
        self.assertEqual(again["fingerprint"], preview["fingerprint"])  # information checks never make "the grades changed" (C4)
        self.certify(preview)

    def test_nothing_is_partial(self):
        from gradebook.service import GradebookService
        preview = self.preview()
        before = self.counts()
        with patch.object(GradebookService, "_write_proposed", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                self.service.certify(self.aid, self.body(preview))
        self.assertEqual(self.counts(), before)

    def test_gradescope_not_sent_is_decided_with_a_reason_that_is_remembered(self):
        self.app.extensions["gradescope"].settings = gw.Settings(credentials_file=None, base="https://example.invalid")
        preview = self.preview()
        written = next(c for c in preview["confirm"] if c["id"] == "gradescope_written")
        self.assertEqual(written["prefill"], "Gradescope is not set up.")
        self.assertNotIn("action", written)
        self.certify(preview, reason="We grade this one on paper only.")
        self.decide("q20", "A", ["R3_3"])  # the uncovered set is the same four grades and two papers
        again = next(c for c in self.preview()["confirm"] if c["id"] == "gradescope_written")
        self.assertEqual(again["prefill"], "We grade this one on paper only.")

    def test_everything_on_gradescope_needs_no_decision(self):
        self.send()
        self.sender.drain()
        preview = self.preview()
        self.assertNotIn("gradescope_written", [c["id"] for c in preview["checks"]])
        self.certify(preview)
        self.assertEqual(json.loads(self.latest()["gradescope_json"])["written"]["decision"], "covered")
        # A send after certifying reopens nothing.
        self.assertEqual(self.state()["state"], "current")

    def latest(self):
        with connect(self.path) as db:
            return dict(db.execute("SELECT * FROM frq_certifications ORDER BY rowid DESC LIMIT 1").fetchone())

    # --- the gradebook's certified source -------------------------------------------------------------------------------------
    def test_the_certified_source_has_the_totals_shape_and_the_view_names_the_certification(self):
        rows = [{"row": r, "section_id": r.split(":")[0], "enrollment_id": r.split(":")[1], "section": 501} for r in (self.row1, self.row2)]
        with self.frq.rosters.lock(), connect(self.path) as db:
            live = sources.totals(self.frq, db, self.term_id, self.assignment["id"], ["frq"], rows=rows)
            decided = sources.totals(self.frq, db, self.term_id, self.assignment["id"], ["frq"], rows=rows, decisions=self.frq.final_decisions(db, self.aid))
            self.assertEqual(live, decided)
            self.assertIsNone(sources.certified(db, self.aid, ["frq"]))
        self.certify()
        with connect(self.path) as db:
            found = sources.certified(db, self.aid, ["frq", "bubble"])
            certified_at = self.latest()["created_at"]
            link = sources.available(db, self.term_id)[self.assignment["id"]]
            self.assertEqual(sources._instant(sources.changed_at(db, link, ["frq"])), sources._instant(certified_at))
        self.assertEqual(set(found), set(live) | {"certification_id", "certified_at", "absent_reason"})
        self.assertEqual(found["parts"], ["frq"])
        self.assertEqual((found["students"][self.row1]["state"], found["students"][self.row1]["earned"]), (live["students"][self.row1]["state"], "10"))
        link = self.gradebook.view(self.term_id)["links"]
        self.assertEqual(next(iter(link.values()))["certification"]["id"], self.latest()["id"])


if __name__ == "__main__":
    unittest.main()
