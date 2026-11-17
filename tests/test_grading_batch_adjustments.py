"""A line added to every final grade of one question in one section, from Certification and Export (requirement, 2026-09-27;
grading/batch_adjustments.py, GRADING.md "Certification and Export").

On the graded workspace of `test_gradescope_send` (section 501, two students on Versions A and B, the instructor's approved reviews):
the preview writes nothing and says each grade before and after; the apply writes ONE ordinary review per final grade (same lines,
status and comment, the line as a point adjustment) with its feedback row and events and one receipt; the same request ID replays;
another line under that ID, a changed grade or a changed rubric is refused; responses that are not final, already carry the line or
that the question's bounds hold are left alone; an automatic grade it replaces is not recorded as overturned; and the review burden
counts a batch as one action.
"""
import json
import os
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grading import batch_adjustments as batch  # noqa: E402
from grading import progress  # noqa: E402
from grading.review import forget_result_facts  # noqa: E402
from grading.gradescope_sync import GradescopeSender  # noqa: E402
from rosters import connect  # noqa: E402
import test_gradescope_send as graded  # noqa: E402

HEADERS = {"X-Teaching-Request": "1"}
COUNTED = ("frq_reviews", "frq_review_feedback", "frq_events", "frq_acceptance_reviews", "frq_acceptance_permission_events")


def tearDownModule():
    graded.tearDownModule()


class PureTests(unittest.TestCase):
    RULES = {"mode": "positive", "floor": 0, "ceiling": 10, "groups": [],
             "items": [{"id": i, "points": p, "requires": [], "excludes": [], "stackable": True, "group": None, "follow_through": False,
                        "repeat_penalty_for_same_originating_error": True} for i, p in (("R1", 4), ("R2", 6))]}

    def final(self, applied, adjustments=(), score=None):
        applications = [{"rubric_item_id": i, "applied": i in applied, "reason": "Model", "evidence": [{"line_id": "l1"}], "actor": "model"} for i in ("R1", "R2")]
        total = sum(p for i, p in (("R1", 4), ("R2", 6)) if i in applied) + sum(a["points"] for a in adjustments)
        return {"id": "d1", "applications": applications, "score": {"score": score if score is not None else total, "adjustments": list(adjustments)}}

    def test_a_line_is_added_before_the_bounds_and_kept_apart_from_earlier_ones(self):
        line = {"section_id": "s", "points": 2, "reason": "Misprint"}
        after, why = batch.change(self.final({"R1"}, [{"points": 1, "reason": "Neat"}]), self.RULES, 10, line)
        self.assertIsNone(why)
        self.assertEqual((after["score"], after["adjustments"]), (7.0, [{"points": 1, "reason": "Neat"}, {"points": 2, "reason": "Misprint"}]))
        self.assertEqual(after["selected"], ["R1"])

    def test_left_alone_when_it_is_already_there_or_the_bounds_hold_the_score(self):
        line = {"section_id": "s", "points": 2, "reason": "Misprint"}
        self.assertEqual(batch.change(self.final({"R1"}, [{"points": 2.0, "reason": " Misprint "}]), self.RULES, 10, line)[1][0], "already")
        self.assertEqual(batch.change(self.final({"R1", "R2"}), self.RULES, 10, line)[1][0], "bounded")
        # A deduction from lines that add up past the ceiling is held by it too: the score does not move.
        over = {**self.RULES, "items": [{**self.RULES["items"][0], "points": 8}, {**self.RULES["items"][1], "points": 6}]}
        self.assertEqual(batch.change(self.final({"R1", "R2"}, score=10), over, 10, {**line, "points": -2})[1][0], "bounded")

    def test_the_review_keeps_every_line_and_its_evidence_decided_by_the_instructor(self):
        found = batch.applications_of(self.final({"R2"}), self.RULES)
        self.assertEqual([(a["rubric_item_id"], a["applied"], a["actor"], a["evidence"]) for a in found],
                         [("R1", False, "workspace", [{"line_id": "l1"}]), ("R2", True, "workspace", [{"line_id": "l1"}])])

    def test_an_edit_replaces_the_line_in_place_and_a_removal_takes_it_off(self):
        old, new = {"points": 2, "reason": "Misprint"}, {"points": 3, "reason": "Misprint (b)"}
        earlier = [{"points": -1, "reason": "Manual"}, {"points": 2.0, "reason": "Misprint"}, {"points": 1, "reason": "Neat"}]
        self.assertEqual(batch.adjusted(earlier, old, new)[0], [earlier[0], {"points": 3, "reason": "Misprint (b)"}, earlier[2]])
        self.assertEqual(batch.adjusted(earlier, old, None)[0], [earlier[0], earlier[2]])
        self.assertEqual(batch.adjusted(earlier[:1], old, None)[1][0], "gone")
        self.assertEqual(batch.adjusted(earlier, old, {"points": 1, "reason": "Neat"})[1][0], "already")
        # An edit or a removal writes even where the bounds keep the score (the grade's own adjustment changes).
        after, why = batch.change(self.final({"R1", "R2"}, [{"points": 2, "reason": "Misprint"}], score=10), self.RULES, 10, None, old)
        self.assertIsNone(why)
        self.assertEqual((after["score"], after["adjustments"]), (10.0, []))

    def test_a_line_worth_the_whole_question_in_every_section_is_full_credit_and_a_later_version_has_it_too(self):
        """GRADING "A FULL-CREDIT LINE REACHES LATER PAPERS"."""
        question = lambda key, version, created: {"question_id": f"{key}-{version}", "question_key": f"{key}-{version}", "exam_version": version, "created_at": created, "max_points": 3}
        line = lambda version, points=3, section="all", at="t2": {"question_id": f"q4-{version}", "section_id": section, "points": points, "reason": "Not required", "created_at": at}
        versions = [question("q4", "A", "t1"), question("q4", "B", "t1"), question("q4", "E", "t3")]
        self.assertEqual(sorted(batch.full_credit_lines([line("A"), line("B")], versions)), ["q4-A", "q4-B", "q4-E"])
        self.assertEqual(batch.full_credit_lines([line("A"), line("B")], versions)["q4-E"]["points"], 3)
        # One version's line is that version's misprint: no other version has it, nor a makeup added since.
        self.assertEqual(sorted(batch.full_credit_lines([line("A")], versions)), ["q4-A"])
        # A smaller line, or one of a single section, is no full credit.
        self.assertEqual(batch.full_credit_lines([line("A", points=2), line("B", points=2)], versions), {})
        self.assertEqual(batch.full_credit_lines([line("A", section="s1"), line("B", section="s1")], versions), {})
        # A version worth more than the line has no full credit from it.
        self.assertNotIn("q4-E", batch.full_credit_lines([line("A"), line("B")], [*versions[:2], {**versions[2], "max_points": 5}]))

    def test_the_command_is_bounded_like_any_adjustment(self):
        question = {"content": {"max_points": 10}}
        self.assertEqual(batch.command({"section_id": "all", "points": -1.5, "reason": "  Typo  "}, question), {"section_id": "all", "points": -1.5, "reason": "Typo"})
        for bad in ({"points": 2, "reason": "x"}, {"section_id": "", "points": 2, "reason": "x"}, {"section_id": "s", "points": 0, "reason": "x"},
                    {"section_id": "s", "points": 11, "reason": "x"}, {"section_id": "s", "points": 0.005, "reason": "x"}, {"section_id": "s", "points": True, "reason": "x"},
                    {"section_id": "s", "points": 2, "reason": "  "}, {"section_id": "s", "points": 2, "reason": "x" * 201}, []):
            with self.assertRaises(ValueError, msg=bad):
                batch.command(bad, question)


class BatchTests(graded.GradescopeCase):
    def setUp(self):
        super().setUp()
        self.q20a, self.q21a, self.q21b = self.q[("q20", "A")], self.q[("q21", "A")], self.q[("q21", "B")]

    def url(self, qid, preview=False):
        return f"/api/grading/assignments/{self.aid}/certification/questions/{qid}/adjustments{'/preview' if preview else ''}"

    def preview(self, qid, status=200, section=None, **line):
        body = {"section_id": section or self.section["id"], "points": 2, "reason": "Misprint in part (c)", **line}
        response = self.client.post(self.url(qid, True), json=body, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def apply(self, qid, found, status=201, request_id=None, **extra):
        body = {"section_id": found["section_id"], "points": float(found["points"]), "reason": found["reason"], "fingerprint": found["fingerprint"],
                "request_id": request_id or str(uuid.uuid4()), "confirmation": "adjust", **extra}
        response = self.client.post(self.url(qid), json=body, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def counts(self):
        with connect(self.path) as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COUNTED}

    def current(self, key, letter):
        response = self.response(key, letter)
        return response, response["review"]

    def test_the_preview_writes_nothing_and_says_each_grade_before_and_after(self):
        before = self.counts()
        found = self.preview(self.q20a)
        self.assertEqual(self.counts(), before)
        self.assertEqual([(c["before"], c["after"], c["kind"], c["section"]) for c in found["changes"]], [("4", "6", "review", 501)])
        self.assertEqual((found["counts"]["change"], found["counts"]["left"], found["points"], found["reason"], found["question"]["version"]),
                         (1, 0, "2", "Misprint in part (c)", "A"))
        self.assertTrue(all(not k.startswith("_") for c in found["changes"] for k in c))
        for uin in self.uins.values():
            self.assertNotIn(uin, json.dumps(found))
        self.assertEqual(self.preview(self.q20a, section="all")["counts"]["change"], 1)
        self.preview(self.q20a, status=400, section="not-a-section")
        self.preview(self.q20a, status=400, points=0)
        self.preview(self.q20a, status=400, points=11)
        self.preview(self.q20a, status=400, reason="")

    def test_the_apply_writes_one_ordinary_review_per_final_grade(self):
        response, earlier = self.current("q20", "A")
        found = self.preview(self.q20a)
        result = self.apply(self.q20a, found)
        self.assertEqual((result["replay"], result["changed"], result["points"], result["reason"]), (False, 1, "2", "Misprint in part (c)"))
        now, review = self.current("q20", "A")
        self.assertEqual(now["version"], response["version"] + 1)
        self.assertEqual((review["status"], review["actor"], review["result_id"], review["comment"]), ("approved", "workspace", earlier["result_id"], earlier["comment"]))
        self.assertEqual([a["rubric_item_id"] for a in review["applications"] if a["applied"]], ["R3_2"])
        self.assertEqual((review["score"]["score"], review["score"]["adjustments"]), (6.0, [{"points": 2.0, "reason": "Misprint in part (c)"}]))
        self.assertEqual(review["transcript"], earlier["transcript"])
        with connect(self.path) as db:
            feedback = db.execute("SELECT * FROM frq_review_feedback WHERE review_id=?", (review["id"],)).fetchone()
            changed = json.loads(feedback["lines_changed_json"])
            self.assertEqual((changed["batch_adjustment"]["batch_id"], changed["batch_adjustment"]["points"]), (result["batch_id"], 2.0))
            self.assertEqual(json.loads(feedback["provenance_json"])["replaces"], {"kind": "review", "id": earlier["id"]})
            receipt = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='grades_adjusted'").fetchone()[0])
            self.assertEqual((receipt["rule"], receipt["question_id"], [r["review_id"] for r in receipt["reviews"]]), (batch.RULE, self.q20a, [review["id"]]))
            reviewed = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE kind='response_reviewed' AND json_extract(detail_json,'$.batch_adjustment') IS NOT NULL")]
            self.assertEqual([r["review_id"] for r in reviewed], [review["id"]])
            # The ONE final rule sees the new grade; its content differs, so a certification made before it is "changed".
            _, decisions, _ = self.frq.final_decisions(db, self.aid, question_id=self.q20a)
            self.assertEqual([d["final"]["score"]["score"] for d in decisions], [6.0])
            rows = progress.decision_rows(db, self.aid, self.frq.responses(self.aid, self.q20a))
            self.assertEqual([r["adjustments"] for r in rows.values()], [["2"]])
        # The certification page reads the new score; the question panel says it is the instructor's.
        panel = self.client.get(f"/api/grading/assignments/{self.aid}/certification/questions/{self.q20a}", headers=HEADERS).get_json()
        self.assertEqual([(r["score"], r["decided"]["kind"], r["overridden"]) for r in panel["responses"]], [("6", "instructor", True)])
        # Every other question is untouched.
        self.assertEqual(self.current("q21", "A")[1]["score"]["score"], 6.0)

    def test_a_manual_grade_stays_manual_with_its_reason(self):
        self.decide("q21", "A", ["R4_2"], status="manual", manual_score=5, comment="Partial credit for part B")
        found = self.preview(self.q21a, points=-1, reason="Units missing")
        self.assertEqual([(c["before"], c["after"]) for c in found["changes"]], [("5", "4")])
        self.apply(self.q21a, found)
        _, review = self.current("q21", "A")
        self.assertEqual((review["status"], review["comment"], review["score"]["score"]), ("manual", "Partial credit for part B", 4.0))
        self.assertEqual([a["reason"] for a in review["score"]["adjustments"]], ["Partial credit for part B", "Units missing"])

    def test_the_same_request_replays_and_another_line_under_it_is_refused(self):
        found = self.preview(self.q20a)
        request_id = str(uuid.uuid4())
        first = self.apply(self.q20a, found, request_id=request_id)
        written = self.counts()
        again = self.apply(self.q20a, found, status=200, request_id=request_id)
        self.assertEqual((again["replay"], again["batch_id"], again["reviews"]), (True, first["batch_id"], first["reviews"]))
        self.assertEqual(self.counts(), written)
        self.apply(self.q20a, {**found, "reason": "Another line"}, status=409, request_id=request_id)
        self.assertEqual(self.counts(), written)
        # The line is on the grade now: previewing it again leaves the grade alone and there is nothing to apply.
        repeat = self.preview(self.q20a)
        self.assertEqual((repeat["counts"]["change"], [item["code"] for item in repeat["left"]]), (0, ["already"]))
        self.apply(self.q20a, repeat, status=400)

    def test_a_grade_or_rubric_changed_since_the_preview_is_refused_and_nothing_is_written(self):
        found = self.preview(self.q20a)
        self.decide("q20", "A", ["R3_3"])
        before = self.counts()
        self.apply(self.q20a, found, status=409)
        self.apply(self.q20a, {**found, "fingerprint": "0" * 64}, status=409)
        self.apply(self.q20a, found, status=400, confirmation="yes")
        self.apply(self.q20a, found, status=400, fingerprint="not-a-fingerprint")
        self.assertEqual(self.counts(), before)

    def test_responses_that_are_not_final_or_held_by_the_bounds_are_left_alone(self):
        self.decide("q21", "A", ["R4_2"], status="draft")
        found = self.preview(self.q21a)
        self.assertEqual((found["counts"]["change"], [(item["code"], item["state"], item["score"]) for item in found["left"]]),
                         (0, [("not_final", "draft", None)]))
        self.assertIn("draft", found["left"][0]["reason"])
        # Bob's 5 of 8 on Version B: +3 reaches the maximum, and a further +1 is held there.
        self.apply(self.q21b, self.preview(self.q21b, points=3, reason="Misprint"))
        held = self.preview(self.q21b, points=1, reason="Neat work")
        self.assertEqual([(item["code"], item["score"]) for item in held["left"]], [("bounded", "8")])

    def test_a_full_credit_line_reaches_an_answer_held_for_review_and_any_other_line_does_not(self):
        """GRADING "A FULL-CREDIT LINE REACHES LATER PAPERS" (requirement, 2026-11-04)."""
        service = self.client.application.extensions["certification"]
        self.apply(self.q21b, self.preview(self.q21b, section="all", points=8, reason="Not required"))
        self.assertEqual(self.current("q21", "B")[1]["score"]["score"], 8)
        self.assertEqual(service.reach_full_credit(self.aid), 0)  # nothing lacks it
        response, _ = self.current("q21", "B")
        with connect(self.path) as db:  # a later paper's answer, waiting for review
            db.execute("UPDATE frq_responses SET current_review_id=NULL WHERE id=?", (response["id"],))
        self.assertTrue(self.frq.auto_full_credit_one())  # the worker's step
        _, review = self.current("q21", "B")
        self.assertEqual((review["status"], review["actor"], review["score"]["score"], review["comment"]), ("approved", "automatic", 8, batch.AUTOMATIC_COMMENT))
        self.assertEqual(review["score"]["adjustments"][-1], {"points": 8, "reason": "Not required"})
        with connect(self.path) as db:
            receipts = service._adjustment_receipts(db, self.aid, self.q21b)
            actors = [row[0] for row in db.execute("SELECT actor FROM frq_events WHERE kind='grades_adjusted' ORDER BY id")]
        self.assertTrue(receipts[-1]["automatic"])
        self.assertEqual(actors, ["workspace", "automatic"])
        self.assertEqual(service.reach_full_credit(self.aid), 0)
        # A grade the instructor typed stays theirs.
        self.decide("q21", "B", [], status="manual", manual_score=2, comment="Typed")
        self.assertEqual(service.reach_full_credit(self.aid), 0)
        self.assertEqual(self.current("q21", "B")[1]["score"]["score"], 2)
        # An ordinary line reaches only the grades final when it was applied.
        self.apply(self.q21a, self.preview(self.q21a, section="all", points=1, reason="Misprint"))
        response, _ = self.current("q21", "A")
        with connect(self.path) as db:
            db.execute("UPDATE frq_responses SET current_review_id=NULL WHERE id=?", (response["id"],))
        self.assertEqual(service.reach_full_credit(self.aid), 0)
        self.assertIsNone(self.current("q21", "A")[0]["current_review_id"])

    def test_an_automatic_grade_it_replaces_is_not_recorded_as_overturned(self):
        response, earlier = self.current("q21", "B")
        with connect(self.path) as db:
            # Bob's q21-B as an active automatic grade under a (benchmark) permission, blind and not yet judged.
            db.execute("UPDATE frq_responses SET current_review_id=NULL WHERE id=?", (response["id"],))
            # The fixture's grader proposes lines that wait for review; on this throwaway copy its proposal is a clean one.
            db.execute("DROP TRIGGER frq_result_immutable")
            db.execute("UPDATE frq_results SET state='proposed' WHERE id=?", (response["current_result_id"],))
            db.execute("CREATE TRIGGER frq_result_immutable BEFORE UPDATE ON frq_results BEGIN SELECT RAISE(ABORT,'Proposals are immutable'); END")
            forget_result_facts()  # the listings keep what a retained result says, which only this rewrite can change
            result = db.execute("SELECT * FROM frq_results WHERE id=?", (response["current_result_id"],)).fetchone()
            db.execute("INSERT INTO frq_acceptance_permissions VALUES('p1',?,?,'scope','report',0.05,0.1,'2026-09-27T00:00:00+00:00')", (self.aid, self.q21b))
            db.execute("INSERT INTO frq_acceptance_permission_events(permission_id,kind,reason,actor,created_at) VALUES('p1','enabled','test','workspace','2026-09-27T00:00:00+00:00')")
            db.execute("INSERT INTO frq_acceptances VALUES('a1',?,?,?,?,'automatic','scope','{}','policy',1,0,'p1','2026-09-27T00:00:00+00:00')",
                       (result["id"], response["id"], self.aid, self.q21b))
        found = self.preview(self.q21b, points=-1, reason="Units missing")  # the fixture proposes full marks, which +1 would not move
        self.assertEqual([(c["kind"], c["decision_id"]) for c in found["changes"]], [("automatic", "a1")])
        before = self.counts()
        self.apply(self.q21b, found)
        _, review = self.current("q21", "B")
        proposed = sorted(a["rubric_item_id"] for a in json.loads(result["applications_json"]) if a["applied"])
        self.assertEqual((review["status"], review["comment"], sorted(a["rubric_item_id"] for a in review["applications"] if a["applied"])), ("approved", "", proposed))
        after = self.counts()
        self.assertEqual((after["frq_acceptance_reviews"], after["frq_acceptance_permission_events"]),
                         (before["frq_acceptance_reviews"], before["frq_acceptance_permission_events"]))
        with connect(self.path) as db:
            revealed = db.execute("SELECT detail_json FROM frq_events WHERE kind='blind_revealed'").fetchall()
        self.assertEqual([json.loads(r[0])["result_id"] for r in revealed], [result["id"]])

    def question(self, qid):
        response = self.client.get(f"/api/grading/assignments/{self.aid}/certification/questions/{qid}", headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def change(self, qid, action, batch_id, status=200, **line):
        body = {"action": action, "batch_id": batch_id, **line}
        response = self.client.post(self.url(qid, True), json=body, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def apply_change(self, qid, found, status=201, request_id=None):
        body = {"action": found["action"], "batch_id": found["batch_id"], "fingerprint": found["fingerprint"], "request_id": request_id or str(uuid.uuid4()),
                "confirmation": "adjust"}
        if found["action"] == "edit":
            body.update(points=float(found["points"]), reason=found["reason"])
        response = self.client.post(self.url(qid), json=body, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def test_the_question_names_each_grades_adjustments_and_lists_its_batches(self):
        added = self.apply(self.q20a, self.preview(self.q20a))
        found = self.question(self.q20a)
        self.assertEqual([r["adjustments"] for r in found["responses"]], [[{"points": "2", "reason": "Misprint in part (c)"}]])
        self.assertEqual([{k: b[k] for k in ("batch_id", "action", "section", "points", "reason", "changed", "carrying")} for b in found["batches"]],
                         [{"batch_id": added["batch_id"], "action": "add", "section": 501, "points": "2", "reason": "Misprint in part (c)", "changed": 1, "carrying": 1}])
        self.assertEqual(self.question(self.q21a)["batches"], [])  # another question lists its own batches only

    def test_an_edit_changes_the_line_on_the_grades_that_still_carry_it(self):
        self.decide("q21", "A", ["R4_2"], status="manual", manual_score=5, comment="Partial credit for part B")
        added = self.apply(self.q21a, self.preview(self.q21a, points=1, reason="Misprint"))
        self.change(self.q21a, "edit", added["batch_id"], status=400, points=1, reason="Misprint")  # nothing changed
        found = self.change(self.q21a, "edit", added["batch_id"], points=2, reason="Misprint in part (b)")
        self.assertEqual((found["action"], found["old"], found["points"], [(c["before"], c["after"]) for c in found["changes"]]),
                         ("edit", {"points": "1", "reason": "Misprint"}, "2", [("6", "7")]))
        request_id = str(uuid.uuid4())
        edited = self.apply_change(self.q21a, found, request_id=request_id)
        self.assertEqual((edited["action"], edited["of"], edited["changed"]), ("edit", added["batch_id"], 1))
        _, review = self.current("q21", "A")
        self.assertEqual((review["status"], review["score"]["score"]), ("manual", 7.0))
        self.assertEqual([(a["points"], a["reason"]) for a in review["score"]["adjustments"]], [(1.0, "Partial credit for part B"), (2.0, "Misprint in part (b)")])
        # The same request replays; the first batch no longer carries its line, the edit does.
        self.assertEqual(self.apply_change(self.q21a, found, status=200, request_id=request_id)["batch_id"], edited["batch_id"])
        batches = {b["batch_id"]: b for b in self.question(self.q21a)["batches"]}
        self.assertEqual((batches[added["batch_id"]]["carrying"], batches[edited["batch_id"]]["carrying"], batches[edited["batch_id"]]["old"]),
                         (0, 1, {"points": "1", "reason": "Misprint"}))

    def test_a_removal_takes_the_line_off_and_leaves_a_grade_changed_by_hand_alone(self):
        added = self.apply(self.q20a, self.preview(self.q20a))
        found = self.change(self.q20a, "remove", added["batch_id"])
        self.assertEqual((found["action"], found["reason"], [(c["before"], c["after"]) for c in found["changes"]]), ("remove", "Misprint in part (c)", [("6", "4")]))
        removed = self.apply_change(self.q20a, found)
        _, review = self.current("q20", "A")
        self.assertEqual((review["score"]["score"], review["score"]["adjustments"]), (4.0, []))
        self.change(self.q20a, "remove", removed["batch_id"], status=400)  # a removal has no line of its own
        self.change(self.q20a, "remove", "no-such-batch", status=404)
        again = self.change(self.q20a, "remove", added["batch_id"])
        self.assertEqual((again["counts"]["change"], [item["code"] for item in again["left"]]), (0, ["gone"]))
        # Added again, then changed by hand at the desk: the batch leaves that grade alone.
        second = self.apply(self.q20a, self.preview(self.q20a, reason="Misprint, second time"))
        self.decide("q20", "A", ["R3_2"])
        self.assertEqual([item["code"] for item in self.change(self.q20a, "remove", second["batch_id"])["left"]], ["gone"])
        history = self.question(self.q20a)["batches"]
        self.assertEqual([(b["action"], b["carrying"]) for b in history], [("add", 0), ("remove", 0), ("add", 0)])

    def coverage(self, qid):
        with self.frq.rosters.lock(), connect(self.path) as db:
            return list(GradescopeSender._grade_coverage(db, self.aid, self.frq.responses(self.aid, qid)).values())

    def test_the_grades_batch_lines_are_its_comment_on_gradescope(self):
        self.allowed_comments = ("+2 Misprint in part (c)", "+3 Misprint in part (c)")
        added = self.apply(self.q20a, self.preview(self.q20a))
        # Sent as Faculty sent it before 2026-09-27: the line's points and no comment.
        with patch("grading.gradescope_sync.batch_lines", return_value={}):
            self.send(self.view())
            self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [2], points=2.0, score=6.0, comment=None)
        # That grade is not on Gradescope as it should be: it counts as changed, and the next send writes the comment over
        # Faculty's own earlier grade.
        self.assertEqual(self.coverage(self.q20a), ["changed"])
        view = self.view()
        self.assertEqual((view["summary"]["grades"]["update"], view["summary"]["grades"]["send"]), (1, 0))
        self.send(view)
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [2], points=2.0, score=6.0, comment="+2 Misprint in part (c)")
        self.assertEqual(self.coverage(self.q20a), ["sent"])
        self.assertEqual(self.view()["summary"]["grades"]["update"], 0)
        # An edited line updates the comment; a removed one clears it.
        self.apply_change(self.q20a, self.change(self.q20a, "edit", added["batch_id"], points=3, reason="Misprint in part (c)"))
        self.send()
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [2], points=3.0, score=7.0, comment="+3 Misprint in part (c)")
        edited = next(b for b in self.question(self.q20a)["batches"] if b["action"] == "edit")
        self.apply_change(self.q20a, self.change(self.q20a, "remove", edited["batch_id"]))
        self.send()
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [2], points=None, score=4.0, comment=None)
        self.assertEqual(self.coverage(self.q20a), ["sent"])
        # A manual grade's reason and the other questions' grades were never a comment.
        self.assertNothingForbidden()
        self.assertEqual(batch.comment_text([{"points": -0.5, "reason": " Units "}, {"points": 1, "reason": "Typed at the desk"}],
                                            [{"points": -0.5, "reason": "Units"}]), "-0.5 Units")

    def test_the_review_burden_counts_a_batch_as_one_action(self):
        before = self.frq.metrics(self.aid)["burden"]
        self.apply(self.q20a, self.preview(self.q20a, section="all"))
        after = self.frq.metrics(self.aid)["burden"]
        self.assertEqual(after["instructor_actions"], before["instructor_actions"] + 1)
        self.assertEqual((after["responses_decided"], after["flagged"], after["false_alarms"]), (before["responses_decided"], before["flagged"], before["false_alarms"]))


if __name__ == "__main__":
    unittest.main()
