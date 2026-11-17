"""The Review step's navigation: question, then the rubric lines the AI proposed. A view only, and it never shows a proposal hidden for a blind review."""
import ast
import inspect
import json
import random
import sqlite3
import textwrap
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import NotFound

from rosters import connect
import test_frq_grading as grading_fixture
import test_grading_answer_groups as groups_fixture
import test_grading_exam_versions as versions_fixture
import test_grading_acceptance as acceptance_fixture
from test_grading_answer_groups import BARE, WORKED
from test_grading_pipeline import CORRECT
from frq_fixtures import finalize_rubrics
from grading import answers, arbitration, committee, models, recognition, review, review_queue, review_rules, risk, rubric_quality, standard


class ReviewQueueTests(unittest.TestCase):
    managed = False
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric
    grade = groups_fixture.AnswerGroupWorkflow.grade

    def setUp(self):
        groups_fixture.AnswerGroupWorkflow.setUp(self)

    def queue(self):
        listing = self.frq.review_queue(self.plan["id"])
        return listing, next(q for q in listing["questions"] if q["question_id"] == self.qid)

    def test_review_details_are_opt_in_navigation_facts_and_change_no_decision(self):
        # GRADING/PERFORMANCE: reasons help navigation only; default queue bodies stay unchanged.
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R3"}},
                   {tuple(WORKED): ["Unclear whether R2 earns credit without a written explanation."]})
        aid = self.plan["id"]
        before = self.frq.responses(aid)
        default = self.frq.review_queue(aid)
        detailed = self.frq.review_queue(aid, review_details=True)
        kinds = []
        for question in detailed["questions"]:
            for group in question["groups"]:
                for member in group["members"]:
                    kinds.extend(member.pop("reason_kinds"))
        self.assertIn("rubric", kinds)
        self.assertEqual(detailed, default)
        self.assertEqual(self.frq.responses(aid), before)
        client = self.app.test_client()
        answer = client.get(f"/api/grading/assignments/{aid}/review-queue?question_id={self.qid}&details=review").get_json()
        self.assertTrue(all("reason_kinds" in m for q in answer["questions"] for g in q["groups"] for m in g["members"]))

    def test_the_desks_reasons_by_cause_are_opt_in_and_change_no_default_body(self):
        # GRADING/PERFORMANCE (requirement, 2026-10-11, one line per cause): `?details=review` adds `reason_causes` to the response read
        # and nothing else; a read without it is byte for byte what it was, and reading writes nothing.
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R3"}},
                   {tuple(WORKED): ["Unclear whether R2 earns credit without a written explanation."]})
        aid, client = self.plan["id"], self.app.test_client()
        with connect(self.path) as db:
            events = db.execute("SELECT COUNT(*) FROM frq_events").fetchone()[0]
        seen = set()
        for row in self.frq.responses(aid):
            url = f"/api/grading/responses/{row['id']}"
            default, body = self.frq.response(row["id"]), client.get(url).get_data()
            self.assertNotIn(b"reason_causes", body)
            self.assertEqual(client.get(url + "?details=other").get_data(), body)
            detailed = self.frq.response(row["id"], review_details=True)
            causes = detailed.pop("reason_causes")
            self.assertEqual(detailed, default)
            answer = client.get(url + "?details=review").get_json()
            self.assertEqual(answer.pop("reason_causes"), json.loads(json.dumps(causes)))
            self.assertEqual(answer, json.loads(body))
            self.assertEqual(client.get(url).get_data(), body)
            # Exactly the sentences the desk lists, each once and verbatim, the causes in the order of their first sentence.
            listed = review_queue.listed_reasons(default["result"])
            self.assertTrue(listed)
            placed = [[listed.index(reason["text"]) for reason in entry["reasons"]] for entry in causes]
            self.assertEqual(sorted(index for entry in placed for index in entry), list(range(len(listed))))
            self.assertTrue(all(entry == sorted(entry) for entry in placed))
            self.assertEqual([entry[0] for entry in placed], sorted(entry[0] for entry in placed))
            self.assertEqual([entry["label"] for entry in causes], [review_queue.CAUSES[entry["key"]] for entry in causes])
            self.assertEqual(len({entry["key"] for entry in causes}), len(causes))
            self.assertTrue(all(reason["role"] == review_queue.reason_author(reason["text"]) for entry in causes for reason in entry["reasons"]))
            seen.update(entry["key"] for entry in causes)
        self.assertIn("rubric", seen)  # The grader's own doubt, by the retained assessment that wrote it.
        self.assertNotIn("other", seen)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events").fetchone()[0], events)

    def regrade_one(self):
        row = self.frq.responses(self.plan["id"])[0]
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "response_ids": [row["id"]],
                   "mode": "local", "operation": "grade"}
        return self.frq.create_job(self.plan["id"], {**command, "fingerprint": self.frq.preview(self.plan["id"], command)["fingerprint"]})

    def test_batch_review_never_borrows_flags_from_an_earlier_attempt_or_another_response(self):
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2"}, tuple(BARE): {"R3"}},
                   {tuple(WORKED): ["Unclear rubric wording"], tuple(BARE): ["Unclear rubric wording"]})
        with connect(self.path) as db:
            old = db.execute("SELECT id FROM frq_jobs ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            retained = [tuple(r) for r in db.execute("SELECT id,state FROM frq_results ORDER BY rowid")]
        job = self.regrade_one()
        aid = self.plan["id"]
        queue = self.frq.review_queue(aid, job["id"])
        self.assertEqual((queue["waiting"], queue["other_waiting"], queue["batch"]["id"]), (0, 2, job["id"]))
        self.assertEqual(self.frq.review_queue(aid)["waiting"], 2)
        for _ in range(60):
            if not self.frq.process_one():
                break
        queue = self.frq.review_queue(aid, job["id"])
        self.assertEqual((queue["waiting"], queue["other_waiting"]), (1, 1))
        self.assertEqual(self.frq.review_queue(aid, old)["waiting"], 1)
        self.assertEqual(self.frq.job(job["id"])["progress"]["awaiting_review"], 1)
        self.assertEqual(self.frq.job(old)["progress"]["awaiting_review"], 1)
        member = queue["questions"][0]["groups"][0]["members"][0]["id"]
        value = self.frq.response(member)
        self.frq.save_review(member, {"version": value["version"], "result_id": value["current_result_id"],
            "status": "approved", "confirm_review": True, "selected_items": ["R2"]})
        self.assertEqual(self.frq.review_queue(aid, job["id"])["waiting"], 0)
        self.assertEqual(self.frq.job(job["id"])["progress"]["awaiting_review"], 0)
        self.assertEqual(self.frq.review_queue(aid)["waiting"], 1)
        with connect(self.path) as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT id,state FROM frq_results ORDER BY rowid")][:len(retained)], retained)
        client = self.app.test_client()
        self.assertEqual(client.get(f"/api/grading/assignments/{aid}/review-queue?job_id={job['id']}").get_json()["waiting"], 0)
        self.assertEqual(client.get(f"/api/grading/assignments/{aid}/review-queue?job_id=missing").status_code, 404)

    def test_what_the_listings_read_of_a_retained_result_is_read_once_and_is_what_a_fresh_read_says(self):
        # Results are immutable and retained (frq_result_immutable, frq_result_retained; performance audit, 2026-09-29).
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2", "R3"}, tuple(BARE): {"R3"}}, {tuple(WORKED): ["Unclear rubric wording"]})
        aid = self.plan["id"]
        review.forget_result_facts()
        fresh = (self.frq.responses(aid), self.frq.review_queue(aid))
        with patch.object(review, "FACTS_SQL", "SELECT * FROM no_such_table WHERE id IN ({})"):  # a second read of them would fail
            self.assertEqual((self.frq.responses(aid), self.frq.review_queue(aid)), fresh)
            with connect(self.path) as db, self.assertRaises(Exception):  # kept per catalog: another catalog reads its own
                review.result_facts(db, "another catalog", [fresh[0][0]["current_result_id"]])
        # Bounded: a fill that passes the bound still answers in full and keeps no more than the bound.
        review.forget_result_facts()
        with patch.object(review, "FACTS_MAX", 1):
            self.assertEqual((self.frq.responses(aid), self.frq.review_queue(aid)), fresh)
            self.assertEqual(len(review._FACTS), 1)

    def test_the_result_cache_lets_the_least_recently_read_results_go_first(self):
        # One Grading poll reads every plan; when their results pass the bound, each plan still keeps most of its own rather than
        # every fill starting the cache over (review of the performance work, 2026-09-29).
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE frq_results(id,state,applications_json,score_json,signals_json)")
        db.executemany("INSERT INTO frq_results VALUES(?,'proposed',?,'{}','{}')",
                       [(f"x{i}", json.dumps([{"rubric_item_id": "R1", "applied": True}])) for i in range(30)])
        read = []
        db.set_trace_callback(lambda sql: read.append(sql.count("'x")) if "FROM frq_results WHERE id IN" in sql else None)
        review.forget_result_facts()
        self.addCleanup(review.forget_result_facts)
        with patch.object(review, "FACTS_MAX", 10):
            one = [f"x{i}" for i in range(25)]
            self.assertEqual(sorted(review.result_facts(db, "catalog", one)), sorted(one))  # a fill past the bound: all answered
            self.assertEqual(list(review._FACTS), [("catalog", i) for i in one[-10:]])  # the latest kept, no more than the bound
            review.forget_result_facts()
            first, second = [f"x{i}" for i in range(6)], [f"x{i}" for i in range(6, 12)]
            for poll in range(3):
                del read[:]
                answers = review.result_facts(db, "catalog", first), review.result_facts(db, "catalog", second)
                self.assertEqual([set(a) for a in answers], [set(first), set(second)])
                self.assertEqual(read, [6, 6] if poll == 0 else [2, 2])  # only the two each lost to the other are read again

    def test_waiting_responses_are_gathered_by_the_exact_lines_the_ai_proposed(self):
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2", "R3"}, tuple(BARE): {"R3"}})  # Never the full-credit line beside another: that is read as the line alone.
        listing, question = self.queue()
        self.assertEqual((listing["waiting"], question["responses"], question["waiting"], question["approved"], question["not_graded"]), (2, 2, 2, 0, 0))
        by_lines = {tuple(g["applied"]): g for g in question["groups"]}
        self.assertEqual(sorted(by_lines), [("R2", "R3"), ("R3",)])
        both = by_lines[("R2", "R3")]
        self.assertEqual(([i["id"] for i in both["items"]], [i["points"] for i in both["items"]], both["kind"], both["size"]), (["R2", "R3"], [4, 2], "proposal", 1))
        self.assertEqual(by_lines[("R3",)]["scores"], [2])
        # A pile names no student, and its members are exactly the responses the detailed review steps through.
        member, = both["members"]
        self.assertEqual(sorted(member), ["id", "question_key", "state"])
        self.assertEqual(sorted(a["rubric_item_id"] for a in self.frq.response(member["id"])["result"]["applications"] if a["applied"]), ["R2", "R3"])
        self.assertEqual(question["flagged"], sum(g["flagged"] for g in question["groups"]))
        # The same number the workflow calls responses to review.
        self.assertEqual(listing["waiting"], self.frq.assignment(self.plan["id"])["workflow"]["review_responses"])
        # The instructor's decision is the only thing that empties a pile; the AI's lines of the other are untouched.
        response = self.frq.response(member["id"])
        self.frq.save_review(member["id"], {"version": response["version"], "result_id": response["current_result_id"], "status": "approved",
            "confirm_review": True, "selected_items": ["R2"]})
        listing, question = self.queue()
        self.assertEqual((listing["waiting"], question["approved"], [g["applied"] for g in question["groups"]]), (1, 1, [["R3"]]))
        # A saved correction is not a final decision: the response stays in the pile of the lines the AI proposed.
        other = question["groups"][0]["members"][0]["id"]
        response = self.frq.response(other)
        self.frq.save_review(other, {"version": response["version"], "result_id": response["current_result_id"], "status": "draft", "selected_items": ["R2"]})
        _, question = self.queue()
        self.assertEqual([(g["applied"], g["states"]) for g in question["groups"]], [(["R3"], {"draft": 1})])

    def test_a_doubt_about_the_rubric_is_shown_once_for_the_question_however_each_grader_worded_it(self):
        doubts = {tuple(WORKED): ["R1 (10 pts) overlaps R2; the rubric does not say whether they stack.", "What counts as a justification is not defined."],
                  tuple(BARE): ["It is unclear whether R2 may be selected together with the full-credit line R1."]}
        self.grade([WORKED, BARE], {tuple(WORKED): {"R1"}, tuple(BARE): {"R3"}}, doubts)
        _, question = self.queue()
        self.assertEqual(question["doubted"], 2)
        # Two wordings, two responses, two piles: ONE doubt, about stacking the full-credit line R1, which the built-in rule
        # settles whatever other lines each grader happened to name. A doubt naming no line is about the rubric as a whole.
        self.assertEqual([(d["items"], d["responses"], d["settled"]) for d in question["rubric_doubts"]], [(["R1"], 2, "full_credit"), ([], 1, None)])
        self.assertEqual(sorted(question["rubric_doubts"][0]["examples"]), sorted([doubts[tuple(WORKED)][0], doubts[tuple(BARE)][0]]))
        # Gathering is a view: each response is still flagged for its doubt, and an approved one leaves the count.
        self.assertEqual(question["flagged"], 2)
        member = question["groups"][0]["members"][0]["id"]
        response = self.frq.response(member)
        self.frq.save_review(member, {"version": response["version"], "result_id": response["current_result_id"], "status": "approved", "confirm_review": True,
            "selected_items": [a["rubric_item_id"] for a in response["result"]["applications"] if a["applied"]]})
        _, question = self.queue()
        self.assertEqual((question["doubted"], sum(d["responses"] for d in question["rubric_doubts"] if d["items"])), (1, 1))
        # Lines that are not ruled keep the exact set they name, and one ruled line named alone is an ordinary doubt about it.
        from grading.review_queue import doubt_lines
        self.assertEqual(doubt_lines("R3 vs R2: which applies to a bare answer? (not R30)", ["R1", "R2", "R3"]), ["R2", "R3"])
        self.assertNotIn("student", json.dumps(question["rubric_doubts"]).lower())

    def test_identical_proposals_share_one_pile_and_the_largest_pile_comes_first(self):
        self.grade([WORKED, WORKED], {tuple(WORKED): {"R1"}})
        _, question = self.queue()
        self.assertEqual([(g["applied"], g["size"], len(g["members"])) for g in question["groups"]], [(["R1"], 2, 2)])
        with connect(self.path) as db:
            before = db.execute("SELECT COUNT(*) FROM frq_events").fetchone()[0]
            self.frq.review_queue(self.plan["id"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events").fetchone()[0], before)  # Looking records and reveals nothing.

    def test_grades_finalized_automatically_are_counted_per_question_for_a_spot_check(self):
        # A count only (the page samples the responses itself): final like any approval, and never waiting.
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2", "R3"}, tuple(BARE): {"R3"}})
        rows = self.frq.responses(self.plan["id"])
        rows[0] = {**rows[0], "state": "auto_accepted"}
        with patch.object(self.frq, "responses", return_value=rows):
            listing, question = self.queue()
        self.assertEqual((question["automatic"], question["approved"], question["waiting"], listing["waiting"]), (1, 1, 1, 1))
        _, untouched = self.queue()
        self.assertEqual(untouched["automatic"], 0)

    def test_final_grades_are_summed_up_per_question_and_nothing_else_counts(self):
        # grun:P17: a read-only mean, full-credit and zero share over the question's current FINAL grades only.
        self.grade([WORKED, BARE], {tuple(WORKED): {"R2", "R3"}, tuple(BARE): {"R3"}})
        _, question = self.queue()
        self.assertIsNone(question["scores"])  # proposals waiting for review are not grades yet
        rows = self.frq.responses(self.plan["id"])
        rows[0] = {**rows[0], "state": "auto_accepted"}
        with connect(self.path) as db:
            proposed = json.loads(db.execute("SELECT x.score_json FROM frq_responses r JOIN frq_results x ON x.id=r.current_result_id WHERE r.id=?",
                                             (rows[0]["id"],)).fetchone()[0])
        with patch.object(self.frq, "responses", return_value=rows):
            _, question = self.queue()
        self.assertEqual((question["scores"]["count"], question["scores"]["mean"]), (1, round(proposed["score"], 2)))
        self.assertNotIn("enrollment", json.dumps(question["scores"]))
        self.assertEqual(review_queue.score_summary([(5.0, 5), (0.0, 5), (2.5, 5), (5.0, None)], 5),
                         {"count": 4, "mean": 3.12, "max": 5.0, "full": 0.5, "zero": 0.25})
        self.assertIsNone(review_queue.score_summary([], 5))

    def test_work_graded_against_an_earlier_rubric_waits_to_be_graded_again_not_for_review(self):
        self.grade([WORKED, BARE], {tuple(WORKED): {"R1"}, tuple(BARE): {"R3"}})
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        rules = {**groups_fixture.RULES, "items": [*groups_fixture.RULES["items"], {"id": "R5", "description": "Sign error", "points": 6}]}
        q = self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "source_text": "Revised rubric", "rules": rules})["questions"][0]
        self.frq.approve_rubric(self.plan["id"], self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"], "acknowledge_rules": True})
        finalize_rubrics(self.frq, self.plan["id"])
        # 2026-09-25: it cannot be approved as graded, so it forms no pile and waits for the next batch (`outdated`).
        listing, question = self.queue()
        self.assertEqual((listing["waiting"], question["waiting"], question["regrade"], question["groups"]), (0, 0, 2, []))
        self.assertEqual(listing["waiting"], self.frq.assignment(self.plan["id"])["workflow"]["review_responses"])


class ReviewQueueBlindTests(acceptance_fixture.AcceptanceCase):
    def test_a_proposal_hidden_for_a_blind_review_is_never_sorted_by_its_lines(self):
        """The one reason a proposal is hidden: a blind acceptance the instructor has not judged. Its pile would show its lines."""
        self.configure_acceptance(blind_share=1.0)
        response, _ = self.grade(CORRECT)
        self.assertTrue(self.frq.response(response["id"])["hidden"])
        question = next(q for q in self.frq.review_queue(self.plan["id"])["questions"] if q["question_id"] == self.qid)
        hidden, = question["groups"]
        self.assertEqual((hidden["id"], hidden["kind"], hidden["size"], hidden["applied"], hidden["items"], hidden["scores"]), ("hidden", "hidden", 1, [], [], []))
        self.assertEqual([m["id"] for m in hidden["members"]], [response["id"]])
        self.assertEqual((question["rubric_doubts"], question["doubted"]), ([], 0))  # What a grader doubted would say which lines it weighed.
        detailed = self.frq.review_queue(self.plan["id"], review_details=True)
        self.assertEqual([m["reason_kinds"] for q in detailed["questions"] for g in q["groups"] for m in g["members"]], [["blind"]])
        # The response read exposes nothing new for it either (2026-10-11): asked for its reasons by cause, the hidden body is the same bytes.
        hidden_read, client = self.frq.response(response["id"]), self.app.test_client()
        self.assertEqual(self.frq.response(response["id"], review_details=True), hidden_read)
        url = f"/api/grading/responses/{response['id']}"
        self.assertEqual(client.get(url + "?details=review").get_data(), client.get(url).get_data())
        self.assertNotIn(b"reason_causes", client.get(url + "?details=review").get_data())
        # The instructor's own decision ends the hiding for that response, exactly as in the detailed review.
        self.review(response["id"], [], status="draft")
        question = next(q for q in self.frq.review_queue(self.plan["id"])["questions"] if q["question_id"] == self.qid)
        self.assertEqual([(g["kind"], g["size"]) for g in question["groups"]], [("proposal", 1)])
        shown = self.frq.response(response["id"], review_details=True)
        self.assertEqual((shown["hidden"], [reason["text"] for entry in shown["reason_causes"] for reason in entry["reasons"]]),
                         (False, review_queue.listed_reasons(shown["result"])))


class ReviewQueueVersionTests(unittest.TestCase):
    managed = False
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric
    blank = versions_fixture.ExamVersionTests.blank
    compare = versions_fixture.ExamVersionTests.compare
    seat = versions_fixture.ExamVersionTests.seat
    question = versions_fixture.ExamVersionTests.question
    two_versions = versions_fixture.ExamVersionTests.two_versions
    compare_all = versions_fixture.ExamVersionTests.compare_all

    def setUp(self):
        versions_fixture.ExamVersionTests.setUp(self)

    def graded(self):
        """One student wrote Version A and the other Version B; each version has its own Question 1 and rubric."""
        self.seat(enrollment="A", second="B")
        self.two_versions()
        self.compare_all()
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qa, self.qb], "mode": "local", "operation": "recognize"}
        self.frq.create_job(self.plan["id"], {**command, "fingerprint": self.frq.preview(self.plan["id"], command)["fingerprint"]})
        for _ in range(60):
            if not self.frq.process_one():
                break

    def test_each_exam_version_keeps_its_own_question_rubric_and_piles(self):
        self.graded()
        listing = self.frq.review_queue(self.plan["id"])
        piles = {q["question_id"]: q["groups"] for q in listing["questions"]}
        self.assertEqual(sorted(piles), sorted([self.qa, self.qb]))
        # The same printed number and even the same line ID: still two piles, each with its own version's points.
        self.assertEqual({qid: [(g["applied"], g["items"][0]["points"], g["scores"], g["size"]) for g in groups] for qid, groups in piles.items()},
                         {self.qa: [(["R1"], -1, [9], 1)], self.qb: [(["R1"], -2, [8], 1)]})
        versions = {q["question_id"]: q["exam_version"] for q in self.frq.assignment(self.plan["id"])["questions"]}
        self.assertEqual((versions[self.qa], versions[self.qb]), ("A", "B"))

    def test_a_queue_for_one_question_is_that_questions_entry_of_the_whole_queue(self):
        # The review desk refreshes one group after each approval (performance audit, 2026-09-29): with `question_id` the queue holds
        # that question's entry exactly as the whole queue has it, and its totals count that question only. Without it, as before.
        self.graded()
        aid, client = self.plan["id"], self.app.test_client()
        with connect(self.path) as db:
            job = db.execute("SELECT id FROM frq_jobs WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (aid,)).fetchone()[0]
        for scope in (None, job):
            whole = self.frq.review_queue(aid, scope)
            self.assertEqual(whole["waiting"], 2)
            self.assertNotIn("question_id", whole)  # the review desk reads the next step only from an answer without it
            for qid in (self.qa, self.qb):
                one = self.frq.review_queue(aid, scope, qid)
                entry = next(q for q in whole["questions"] if q["question_id"] == qid)
                self.assertEqual((one["questions"], one["waiting"], one["other_waiting"], one["batch"]), ([entry], entry["waiting"], 0, whole["batch"]))
                self.assertEqual(one["question_id"], qid)
                url = f"/api/grading/assignments/{aid}/review-queue?question_id={qid}" + (f"&job_id={scope}" if scope else "")
                self.assertEqual(client.get(url).get_json(), json.loads(json.dumps(one)))
        # An empty question_id is the whole queue, as a request without it.
        base = f"/api/grading/assignments/{aid}/review-queue"
        self.assertEqual(client.get(base + "?question_id=").get_json(), client.get(base).get_json())
        # A question this plan does not pin is not found, like an unknown batch: an empty queue would read as a finished group.
        for scope in (None, job):
            with self.assertRaises(NotFound):
                self.frq.review_queue(aid, scope, "missing")
            self.assertEqual(client.get(base + "?question_id=missing" + (f"&job_id={scope}" if scope else "")).status_code, 404)


# The desk's own reading of a reason's author, one text per recorded prefix. test_grading_review_layout.mjs holds the SAME table for
# `reasonRole` (grading-model-comments.js): the server's causes are drawn only while both sides list the same sentences.
AUTHORS = [("primary: note", "primary"), ("Primary grader: note", "primary"), ("One-shot grader (layout): note", "primary"), ("second: note", "second"),
           ("Secondary: note", "second"), ("Second grader: note", "second"), ("Second grader (transcription): note", "second"), ("Secondary grader: note", "second"),
           ("verifier: note", "verifier"), ("reviewer: note", "verifier"), ("Additional reviewer: note", "verifier"), ("Adversarial reviewer: note", "verifier"),
           ("arbiter: note", "arbiter"), ("The final arbitrator named a grade but left a flag standing: note", "arbiter"), ("Tag-Team reviewer: note", "arbiter"),
           ("flag_reviewer: note", "flag_reviewer"), ("The flag reviewer left a flag unresolved: note", "flag_reviewer"), ("Reader: note", "reader"),
           ("Uncertain reading of line-4: 3x or 3π.", "reader"), ("Uncertain layout from line-4 to line-1: the arrow may join them.", "reader"),
           ("Jev could not break the tie: note", "jev"), ("The flag reviewer: note", ""), ("primary note", ""), ("primary:note", ""), ("The graders disagree.", ""),
           ("The standing grade raised or left a flag it did not settle: note", "")]
# The shape of a real Exam 1 response, as test_grading_review_layout.mjs lists it for `reviewReasons`: ten reasons, two repeated with a role key.
EXAM_ONE = {"reasons": ["Uncertain layout from line-4 to line-1: the arrow may join them.", "Uncertain reading of line-4: 3x or 3π.", "Confirm side margin for R2_4.",
                        "Whether a lone 80,000 satisfies R2_2.", "primary: Confirm side margin for R2_4.", "primary: Whether a lone 80,000 satisfies R2_2.",
                        "The grader found the rubric's wording unclear for this response.", "The handwriting is ambiguous in places.",
                        "The grader was uncertain about a rubric decision.", "second: The second grader doubts R2_1."],
            "signals": {"risk": {"blocks": ["The grader found the rubric's wording unclear for this response.", "primary: Confirm side margin for R2_4.",
                                            "primary: Whether a lone 80,000 satisfies R2_2.", "The handwriting is ambiguous in places."]}}}
EXAM_ONE_LISTED = ["The grader found the rubric's wording unclear for this response.", "The handwriting is ambiguous in places.",
                   "The grader was uncertain about a rubric decision.", "primary: Confirm side margin for R2_4.", "primary: Whether a lone 80,000 satisfies R2_2.",
                   "second: The second grader doubts R2_1.", "Uncertain layout from line-4 to line-1: the arrow may join them.", "Uncertain reading of line-4: 3x or 3π."]


def held(reasons=(), blocks=(), **more):
    """A retained result as the desk reads it: its notes, what would not be accepted unseen, and whatever signals the case needs."""
    return {"reasons": list(reasons), "score": {"score": 4}, "applications": [], **{k: more.pop(k) for k in ("transcript", "score", "applications") if k in more},
            "signals": {"risk": {"blocks": list(blocks), "individual": True}, **more}}


def opinion(lines, score=4, lineage="alpha", **more):
    return {"applications": [{"rubric_item_id": item, "applied": item in lines} for item in ("R1", "R2")], "score": {"score": score}, "lineage": lineage, **more}


class ReasonCauseTests(unittest.TestCase):
    """One line per cause on the review card (requirement, 2026-10-11): every listed sentence in exactly one cause, verbatim, by its NAME in
    the module that says it or by what the result retains. Display only: nothing here grades, flags or holds anything."""

    def placed(self, result, *scorer):
        """{sentence: cause}, having checked the partition: every listed sentence once, each cause and the causes in the desk's order."""
        listed, causes = review_queue.listed_reasons(result), review_queue.reason_causes(result, *scorer)
        at = [[listed.index(reason["text"]) for reason in entry["reasons"]] for entry in causes]
        self.assertEqual(sorted(index for entry in at for index in entry), list(range(len(listed))))
        self.assertEqual((at, [entry[0] for entry in at]), ([sorted(entry) for entry in at], sorted(entry[0] for entry in at)))
        self.assertTrue(all(entry["label"] == review_queue.CAUSES[entry["key"]] for entry in causes))
        return {reason["text"]: entry["key"] for entry in causes for reason in entry["reasons"]}

    def cause(self, text, **signals):
        return self.placed(held(blocks=[text], **signals))[text.strip()]

    def test_the_listed_sentences_and_their_authors_are_the_desks_own(self):
        self.assertEqual(review_queue.listed_reasons(EXAM_ONE), EXAM_ONE_LISTED)
        self.assertEqual([review_queue.reason_author(text) for text in EXAM_ONE_LISTED], ["", "", "", "primary", "primary", "second", "reader", "reader"])
        self.assertEqual([(text, review_queue.reason_author(text)) for text, _ in AUTHORS], AUTHORS)
        # The finalization's reasons, then what would not be accepted unseen, then the notes; a role-keyed copy kept once in the block's place.
        merged = held(["Graders disagree", "  ", None], ["Graders disagree", "Little work shown"], rubric_quality={"reasons": ["Exclusion violated"]})
        self.assertEqual(review_queue.listed_reasons(merged), ["Exclusion violated", "Graders disagree", "Little work shown"])
        self.assertEqual(review_queue.listed_reasons(held(["verifier: Little work shown"], ["Little work shown"])), ["verifier: Little work shown"])
        self.assertEqual((review_queue.listed_reasons({}), review_queue.reason_causes({})), ([], []))
        self.assertEqual(sorted(review_queue.CAUSES), sorted(["finalization", "blank", "disagreement", "reading", "rubric", "checks", "grader", "other"]))

    def said_by(self, function, module):
        """({name: sentence} a function says by name, the sentence literals still written inside it)."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
        doc = tree.body[0].value if isinstance(tree.body[0], ast.Expr) else None
        inline = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node is not doc and len(node.value.split()) >= 3]
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        return {name: vars(module)[name] for name in names if isinstance(vars(module).get(name), str) and " " in vars(module)[name]}, inline

    def test_no_sentence_assess_or_review_state_can_say_is_without_a_cause(self):
        # Built from the names themselves, so a sentence added to either function fails here until it has a name and a cause: an
        # inline literal is refused, a named one must be placed (`_SAID`, `_BEGINS`), and every one `assess` names must be seen below.
        named, inline = self.said_by(risk.assess, risk)
        self.assertEqual(inline, [])
        self.assertGreaterEqual(len(named), 35)
        sentences = {text for text in named.values() if text.endswith(".")}
        begins = set(named.values()) - sentences
        self.assertEqual(begins, {risk.SECOND_READING_PREFIX, risk.SECOND_PREFIX, risk.ONE_SHOT_PREFIX, risk.SYMBOLIC_PREFIX})
        for text in sentences | {risk.EMPTY_SELECTION, risk.EMPTY_FULL, risk.SECOND_FAILED, risk.NO_SECOND, risk.NO_PRIMARY, risk.INCOMPLETE, risk.KEY_UNREAD}:
            self.assertNotEqual(self.cause(text), "other", text)
        # Every verdict `assess` gives over the checks' whole range: each sentence it says, held or waived, has a cause.
        pick = random.Random(20261011).choice
        compared = lambda: pick([None, {"agrees": True, "same_score": True, "disagreements": []}, {"agrees": False, "same_score": pick([True, False]), "disagreements": pick([[], ["R1", "R2"]])}])
        seen = set()
        for _ in range(6000):
            c = {"ocr": {"providers": pick([0, 1, 2]), "agreement": pick([None, .5, .99]), "disagreements": pick([0, 2]), "min_confidence": pick([None, .2, .99]), "min_rate": pick([None, .5, .99]),
                         **({"direct": "direct-reading-v4"} if pick([True, False]) else {})},
                 "reading": {"visual_confidence": pick([None, .3, .99]), "ambiguities": pick([0, 1]), "unclear": pick([0, 1]), "registered": pick([True, False]),
                             **({"blank_crops": pick([1, 2])} if pick([True, False, False]) else {})},
                 "primary": {"confidence": pick([.3, .99]), "min_item_confidence": pick([None, .3, .99]), "review_reasons": pick([0, 1]), "rubric_ambiguities": pick([0, 1]),
                             "scored": pick([True, True, False]), "work_consistent": pick(["yes", "no", "unclear"]), "attempts": pick([1, 2]), "ambiguous_items": pick([0, 1])},
                 "answer": {"check": pick(["match", "mismatch", "unknown"]), "readers_agree": pick([None, True, False]), "work": pick(["none", "shown"]), "work_required": pick([True, False]),
                            "key": pick([True, False]), "full_credit": pick([True, False])},
                 "symbolic": {"fail": pick([0, 1]), "contradictions": ["2 + 2 = 5 is false.", "x cannot be both 1 and 2."]},
                 "second": pick([None, None, {**(compared() or {"agrees": True}), "independent": pick([True, False]),
                                              "reading_issues": pick([[], [{"kind": "layout", "description": "The arrow may join the two lines."}]]),
                                              "review_reasons": pick([[], ["Look at the last step."]])}]),
                 "verifier": pick([None, None, {"material": pick([0, 1]), "no_material_contradiction": pick([True, False]), "transcript_disagreements": pick([0, 1])}]),
                 "output": {"rejected_outputs": pick([0, 1])}, "rubric": {"exceptions": pick([True, False])}, "escalation": pick([None, {"rubric_disagreement": compared()}]),
                 **({"committee": {"status": pick(["resolved", "unresolved"]), "reason": "The team's own reason for waiting.", "pair": compared(), "third_comparisons": {},
                                   "review_reasons": pick([[], ["primary: Is the margin work part of the answer?"]])}} if pick([True, False]) else {}),
                 **({"one_shot": {"reading_issues": pick([[], [{"kind": "unreadable", "description": "A smudge covers the last line."}]])}} if pick([True, False, False, False]) else {}),
                 **({"choice": {"rule": "choice-v1", "orders_agree": pick([True, False]), "confidence": pick([None, .9])}} if pick([True, False, False, False]) else {})}
            policy = {**risk.DEFAULTS, **{key: pick([True, False]) for key in ("require_second_grader", "require_key_match", "require_work_shown", "verifier_routes", "confidence_checks")},
                      "review_policy": pick(risk.REVIEW_POLICIES)}
            verdict = risk.assess(c, policy, pick([(), ("contradiction",)]))
            waived = verdict.get("waived") or {}
            said = [*verdict["blocks"], *verdict["reasons"], *verdict["escalate"], *waived.get("blocks", []), *waived.get("reasons", []),
                    *(waived.get("direct_reading") or {}).get("blocks", []), *(waived.get("choice_reader") or {}).get("blocks", [])]
            new = set(said) - seen
            if new:
                where = self.placed(held(verdict["reasons"], said, **({"committee": c["committee"]} if "committee" in c else {})))
                self.assertFalse([text for text in new if where[text] == "other"])
                seen |= new
        self.assertFalse(sentences - seen)  # The cases above reach every sentence `assess` names: a new one must be reached too.
        self.assertTrue(all(any(text.startswith(start) for text in seen) for start in begins))
        # `review_state`: the reader's notes by the transcript that holds them, its own sentences by name, the grader's by what it retained.
        named, inline = self.said_by(recognition.review_state, recognition)
        self.assertEqual((inline, set(named.values())), ([], {recognition.OCR_DISAGREE, recognition.VISUAL, recognition.OCR_UNSURE, recognition.BOUNDARIES, recognition.APPLICATION}))
        read = models.plain_transcript("3x = 18\nx = 6", confidence=.2, ambiguities=["The 6 may be a 0."])
        read["layout_relations"] = [{"from_line": "line-1", "to_line": "line-2", "kind": "continuation", "uncertain": True, "description": "an arrow joins them"}]
        read["reading_alternatives"] = [{"line_id": "line-2", "options": ["x = 6", "x = 0"], "description": "the loop is open"}]
        doubt, condition = "Does R2 need the equation written out?", review_rules.CONDITION_PREFIX + "R2: only if boxed — check its application to this response."
        own = [doubt, condition, "The work stops early; please look.", "R1: evidence does not quote the retained transcript.", "R2: no supported evidence; the selection requires instructor review.",
               recognition.UNDECIDED, recognition.ERROR_LINE, recognition.FOLLOW_THROUGH, answers.UNQUOTED, answers.STRUCK_LINE, answers.STRUCK_WORK, answers.KEY_MISMATCH, answers.ONE_READING,
               answers.LITTLE_WORK, answers.NOT_FULL, answers.NO_WORK_LINE, answers.ADDS_UP, rubric_quality.REFUSED_LINE, rubric_quality.REFUSED_SUM]
        state, signals, reasons = recognition.review_state(
            {"candidates": [{"provider": "glm", "transcript": read, "confidence_rate": .2, "blank_crops": [0]}, {"provider": "mathpix", "transcript": read}],
             "consensus": {"agreement": .4, "disagreements": [{"providers": ["glm", "mathpix"]}]}},
            {"transcript": read, "visual_confidence": .2}, {"review_reasons": own, "confidence": .2, "applications": [], "rubric_ambiguities": [doubt]},
            {"crops": [{"metadata": {"registered": False}}]}, {"visual_threshold": .85, "ocr_threshold": .9, "grading_threshold": .9, "risk": dict(risk.DEFAULTS)})
        self.assertEqual(state, "both")
        self.assertTrue(set(named.values()) | {risk.blank_reason(1), *models.reading_ambiguities(read), *own} <= set(reasons))
        where = self.placed({"transcript": read, "reasons": reasons, "signals": signals})
        self.assertEqual({key: sorted(text for text in where if where[text] == key) for key in set(where.values())}, {
            "blank": [risk.blank_reason(1)],
            "reading": sorted([*models.reading_ambiguities(read), recognition.OCR_DISAGREE, recognition.VISUAL, recognition.OCR_UNSURE, recognition.BOUNDARIES, answers.ONE_READING]),
            "rubric": sorted([doubt, condition]),
            "checks": sorted([recognition.FOLLOW_THROUGH, answers.KEY_MISMATCH, answers.LITTLE_WORK, answers.NOT_FULL, answers.NO_WORK_LINE, answers.ADDS_UP,
                              rubric_quality.REFUSED_LINE, rubric_quality.REFUSED_SUM]),
            # A grader's quotation that Faculty could not find is about that grader's evidence; its own words are nobody's until an
            # assessment that retains them, or a role key, says whose they are.
            "grader": sorted(["R1: evidence does not quote the retained transcript.", "R2: no supported evidence; the selection requires instructor review.",
                              recognition.UNDECIDED, recognition.ERROR_LINE, answers.UNQUOTED, answers.STRUCK_LINE, answers.STRUCK_WORK, recognition.APPLICATION]),
            "other": ["The work stops early; please look."]})
        retained = {"transcript": read, "reasons": reasons, "signals": {**signals, "panels": [{"opinions": {"primary": {"review_reasons": own}}}]}}
        self.assertEqual({**where, "The work stops early; please look.": "grader"}, self.placed(retained))  # The assessment that asked retains it.
        # An empty transcript's own note is the blank answer said again, beside the reader's reason.
        nothing = models.plain_transcript("", confidence=None)
        self.assertEqual(self.placed({"transcript": nothing, "reasons": [models.NOTHING_TRANSCRIBED], "signals": {"risk": {"blocks": [risk.blank_reason(2)]}}}),
                         {risk.blank_reason(2): "blank", models.NOTHING_TRANSCRIBED: "blank"})

    def test_one_cause_said_two_or_three_times_is_one_cause(self):
        # The graders' disagreement: Faculty's flag, the sentence naming the lines and the team's own reason (the three `arbitration.flags`
        # folds into one for the arbitrator). The words are those the modules say: the names are theirs.
        differ = committee.decide(opinion({"R1"}, 10), opinion({"R2"}, 4, "beta"), families=False)
        named = risk.disagreement_reason(differ["pair"])
        self.assertTrue(differ["reason"].startswith(arbitration.TEAM_DIFFERS) and named.startswith(arbitration.DISAGREE_ON) and arbitration.DISAGREE == risk.DISAGREE)
        doubt, issue, request = "Does R2 need the equation written out?", "Line 3 reads 46,206 where the image shows 40,206.", "The second step is hard to follow."
        second = opinion({"R2"}, 4, "beta", rubric_ambiguities=[doubt], reading_issues=[{"kind": "transcription", "description": issue, "line_ids": []}], review_reasons=[request])
        finalization = "Rubric quality check: R1 and R2 were both selected but are confirmed as mutually exclusive. Review this grade manually."
        team = {**differ, "review_reasons": [f"second: {request}", f"second: {issue}", f"second: {doubt}"]}
        result = held(
            [named, differ["reason"], *team["review_reasons"], f"{risk.SECOND_READING_PREFIX}transcription): {issue}", f"{risk.SECOND_PREFIX}{request}", doubt, risk.KEY_DIFFERS,
             "Something nobody named.", finalization],
            [risk.SECOND_READING, risk.SECOND_ASKED, differ["reason"], *team["review_reasons"], risk.WORDING, risk.KEY_DIFFERS, risk.DISAGREE, risk.HANDWRITING],
            committee=team, second=second, rubric_ambiguities=[], panels=[{"tier": "cloud_standard", "decision": team, "opinions": {"primary": opinion({"R1"}, 10), "second": second}}],
            rubric_quality={"reasons": [finalization]})
        where = self.placed(result)
        self.assertEqual({key: [text for text in review_queue.listed_reasons(result) if where[text] == key] for key in dict.fromkeys(where.values())}, {
            "finalization": [finalization],
            "reading": [risk.SECOND_READING, risk.HANDWRITING, f"second: {issue}", f"{risk.SECOND_READING_PREFIX}transcription): {issue}"],
            "grader": [risk.SECOND_ASKED, f"second: {request}", f"{risk.SECOND_PREFIX}{request}"],
            "disagreement": [differ["reason"], risk.DISAGREE, named],
            "rubric": [risk.WORDING, f"second: {doubt}"],  # The bare copy of the doubt is the same reason; the copy naming its grader is kept.
            "checks": [risk.KEY_DIFFERS],
            "other": ["Something nobody named."]})  # Never dropped, never guessed at.
        self.assertEqual([entry["key"] for entry in review_queue.reason_causes(result)], ["finalization", "reading", "grader", "disagreement", "rubric", "checks", "other"])
        # The team's own reason is a disagreement only where two assessments were compared and differ: a missing, failed or
        # same-family assessment is a check that did not pass.
        first, other = opinion({"R1"}, 10), opinion({"R2"}, 4, "beta")
        for decision, expected in ((committee.decide(first, None), "checks"), (committee.decide({**first, "failed": True}, other), "checks"),
                                   (committee.decide(first, {**other, "failed": True}, opinion({"R1"}, 10, "gamma")), "checks"),
                                   (committee.decide(first, opinion({"R1"}, 10)), "checks"),  # One family agreeing with itself, before `standard.FAMILY_RULE`.
                                   (committee.decide(first, other, opinion(set(), 0, "gamma"), required=True, families=False), "disagreement"),
                                   (committee.decide(first, other, None, required=True), "disagreement")):
            with self.subTest(reason=decision["reason"]):
                self.assertEqual((decision["status"], self.cause(decision["reason"], committee=decision)), ("unresolved", expected))
        self.assertEqual(self.cause("The team's own reason for waiting."), "other")  # Without the decision that says it, nobody's.

    def test_a_ruling_that_left_the_response_with_the_instructor_is_its_authors_note(self):
        flag = "Check whether the final digit is clear."
        team = lambda: {"status": "unresolved", "reason": "The primary and secondary graders differ on R1; review this response individually.",
                        "pair": {"agrees": False, "same_score": False, "disagreements": ["R1"]}, "third_comparisons": {}}
        output = lambda: {**held([flag, "Another doubt."], [flag, "Another doubt."], committee=team()), "transcript": {}, "interpretation": {}, "state": "grading_review"}
        ruling = lambda rule, **more: {"rule": rule, "outcome": "adopted", "flags": [{"id": "F1", "text": flag, "about": ["standard.primary"], "resolved": True, "note": "clear"}], **more}
        from grading import flag_resolution
        for apply, record, begins in ((arbitration.apply, ruling(arbitration.RULE), arbitration.UNSETTLED),
                                      (standard.apply, {**ruling(standard.RULE), "outcome": "finalized", "reason": "fine"}, standard.UNSETTLED),
                                      (flag_resolution.apply, ruling(flag_resolution.RULE), "The flag review's grade raised or left a flag it did not settle: ")):
            with self.subTest(begins=begins):
                kept = apply(output(), record)
                said = kept["signals"]["committee"]["reason"]  # The ruling wrote its sentence over the team's reason and recorded the flags it left.
                self.assertTrue(said.startswith(begins) and said in kept["reasons"] and said in kept["signals"]["risk"]["blocks"])
                where = self.placed(kept)
                self.assertEqual((where[said], where["Another doubt."]), ("grader", "other"))
        for record, role in (({"rule": arbitration.RULE, "outcome": "unreliable", "reason": "The crop is cut off.", "flags": []}, "arbiter"),
                             ({"rule": arbitration.RULE, "outcome": "unsettled", "reason": "R1 stays open.", "flags": []}, "arbiter"),
                             ({"rule": arbitration.RULE, "outcome": "failed", "reason": "No answer.", "flags": []}, "arbiter")):
            kept = arbitration.apply(output(), record)
            where = self.placed(kept)
            said = next(text for text in kept["reasons"] if review_queue.reason_author(text) == role)
            # The team's reason is still the disagreement it was: the ruling added its own sentence beside it.
            self.assertEqual((where[said], self.cause(kept["signals"]["committee"]["reason"], committee=kept["signals"]["committee"], arbiter=kept["signals"]["arbiter"])),
                             ("grader", "disagreement"))
        asked = standard.apply(output(), {"rule": standard.RULE, "outcome": "manual_review", "reason": "Look at the digit.", "flags": []})
        said = next(text for text in asked["reasons"] if review_queue.reason_author(text) == "flag_reviewer")
        self.assertEqual(self.placed(asked)[said], "grader")

    def test_the_scorers_own_refusal_is_a_check_by_scoring_the_same_ticks_again(self):
        line = lambda item, points, **more: {"id": item, "points": points, "requires": [], "excludes": [], "stackable": True, "group": "", "follow_through": False,
                                              "repeat_penalty_for_same_originating_error": True, **more}
        rubric = {"mode": "positive", "floor": 0, "ceiling": 10, "groups": [], "items": [line("R1", 10, excludes=["R2"]), line("R2", 4)]}
        ticks = [{"rubric_item_id": "R1", "applied": True}, {"rubric_item_id": "R2", "applied": True}]
        refusal = "R1 conflicts with another selected item."
        result = held([refusal, f"primary: {refusal}"], [risk.UNSCORABLE], score=None, applications=ticks)
        self.assertEqual(self.placed(result, rubric, 10), {risk.UNSCORABLE: "checks", f"primary: {refusal}": "checks"})
        self.assertEqual(self.placed(held([refusal], [risk.UNSCORABLE], score=None, applications=ticks)), {risk.UNSCORABLE: "checks", refusal: "other"})
        self.assertEqual(self.placed(held([refusal], score={"score": 4}, applications=ticks), rubric, 10), {refusal: "other"})  # Scored: nothing was refused.


if __name__ == "__main__":
    unittest.main()
