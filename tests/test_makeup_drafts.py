"""Makeup versions drafted by a model (GRADING "MAKEUP VERSIONS", grading/makeup_drafts.py), end to end on a scratch workspace.

A loopback model host plays the Rubric drafting and review model; everything else is real (the quiz layout compiled in the document
sandbox, print jobs stamped). A quiz is drafted and approved first (Versions A and B, graded in one plan); a makeup is then drafted as
Version C, parallel to Version A, its rubrics adapted from A's line by line, and approved into the SAME plan: every earlier question,
pin and rubric stays, one unnamed print job is attached to a new makeup sitting, and the ticked student is placed on it."""
import json
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from fake_openai import payload_of, prompt_name
from rosters import connect
import test_quiz_drafts as quiz_fixture
from test_quiz_drafts import quiz_answer, rubric_answer


class MakeupDrafts(unittest.TestCase):
    # QuizDrafts' scratch workspace, model host and quiz request, without running its own tests again.
    setUp, draft = quiz_fixture.QuizDrafts.setUp, quiz_fixture.QuizDrafts.draft

    def answer(self, body, host):
        name, payload = prompt_name(body), payload_of(body)
        if name == "makeup_write":
            return quiz_answer(payload)
        if name == "rubric_adapt":
            items = payload["reference"]["rubric"]["items"]
            return {"solution": "Worked for the makeup.", "final_answers": [], "notes": "",
                    "items": [{"id": item["id"], "description": item["description"].replace("x = 2", "x = 5") + " (makeup)"} for item in items]}
        if name == "quiz_rubric":
            return rubric_answer(payload)
        return quiz_fixture.QuizDrafts.answer(self, body, host)

    def approved_quiz(self):
        draft = self.draft()
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        options = self.frq.quiz_approval_options(draft["id"])
        self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": draft["latest_complete"], "confirmation": "approve",
                                                  "key": options["assignment"]["key"], "title": "Chapter 3 Quiz",
                                                  "sections": [s["id"] for s in options["sections"]], "copies": {"A": 1, "B": 1}, "given_on": "2026-09-17"})
        self.frq.process_cloud()
        approval = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))
        return approval["result"]

    def pins(self, plan_id):
        with connect(self.path) as db:
            return {tuple(r) for r in db.execute("SELECT question_id,question_revision_id,rubric_id FROM frq_assignment_questions WHERE assignment_id=?", (plan_id,))}

    def test_a_makeup_is_drafted_parallel_to_a_version_and_approved_into_the_same_plan(self):
        done = self.approved_quiz()
        plan_id = done["plan_id"]
        before = self.pins(plan_id)
        options = self.frq.makeup_options(plan_id)
        self.assertEqual((options["letter"], options["problem"], options["certified"]), ("C", "", False))
        self.assertEqual([(s["label"], s["problems"], s["points"]) for s in options["sources"]], [("Version A", 2, 5), ("Version B", 2, 5)])
        self.host.requests.clear()
        draft = self.frq.create_makeup_draft(plan_id, {"request_id": "mk-1", "instructions": "Keep the numbers small."})
        self.assertEqual((draft["versions"], draft["makeup"]["letter"], draft["makeup"]["source"]), (["C"], "C", "Version A"))
        self.assertEqual(self.frq.create_makeup_draft(plan_id, {"request_id": "mk-1", "instructions": "Keep the numbers small."})["id"], draft["id"])
        with self.assertRaises(Conflict):  # one open makeup draft per plan
            self.frq.create_makeup_draft(plan_id, {"request_id": "mk-2"})
        self.assertNotIn(draft["id"], [d["id"] for d in self.frq.quiz_drafts()])  # listed with its assignment, not with the quizzes
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        self.assertEqual(draft["state"], "ready", [r.get("error") for r in draft["revisions"]])
        bodies = [r["body"] for r in self.host.requests]
        names = [prompt_name(b) for b in bodies]
        self.assertEqual(names[:2], ["makeup_write", "quiz_check"])
        self.assertEqual(names.count("rubric_adapt"), 2)
        write = payload_of(bodies[0])
        # The writer saw the original version's problems with their rubrics, every version's statements to avoid, and A's own fragments.
        self.assertEqual([p["points"] for p in write["original"]], [2, 3])
        self.assertTrue(write["original"][0]["rubric"]["items"])
        self.assertEqual({a["version"] for a in write["avoid"]}, {"Version A", "Version B"})
        self.assertEqual([p["versions"][0]["version"] for p in write["original_quiz"]["problems"]], ["A", "A"])
        self.assertEqual(write["request"]["versions"], ["C"])
        result = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])["result"]
        adapted = result["rubrics"][0]["rubric"]
        self.assertEqual([line["id"] for line in adapted["items"]], [line["id"] for line in adapted["reference_items"]])
        self.assertIn("(makeup)", adapted["items"][0]["description"])
        # Each problem's answer key was read once while the makeup was drafted (requirement, 2026-10-11): by
        # the plan's own reader contract, with the worked solution and the writer's answers in view, against the adapted lines the
        # question will get, each request the revision's own. The revision keeps the keys beside the rubrics.
        read = {payload_of(b)["question"]["printed_number"]: payload_of(b) for b in bodies if prompt_name(b) == "answer_key_read"}
        self.assertEqual(sorted(read), ["1", "2"])
        self.assertEqual((read["2"]["worked_solution"], read["2"]["author_answers"], read["2"]["full_credit_line"], read["2"]["question"]["title"]),
                         ("Substitute.", [{"part": "b", "answer": "$y = 9$"}], "R1", "Question 2"))
        self.assertEqual(read["1"]["correct_choice"], "b")
        self.assertEqual(read["2"]["rubric_lines"], [{k: line[k] for k in ("id", "points", "description")} for line in result["rubrics"][1]["rubric"]["items"]])
        with connect(self.path) as db:
            calls = [tuple(r) for r in db.execute("SELECT step,quiz_revision_id FROM frq_cloud_calls WHERE prompt='answer_key_read' AND quiz_revision_id=? ORDER BY step",
                                                  (result["revision_id"],))]
        self.assertEqual(calls, [("quiz:key:P1:C", result["revision_id"]), ("quiz:key:P2:C", result["revision_id"])])
        keys = {str(r["problem"]): r["keys"]["C"] for r in result["rubrics"]}
        self.assertEqual((keys["1"]["key"], keys["2"]["key"], keys["2"]["rule"]), ([{"label": "", "accept": ["(b)", "x = 2"]}], [{"label": "b", "accept": ["y = 9"]}], "answer-key-read-v2"))
        # Approval: a certified plan needs the instructor's explicit confirmation.
        # (The window that showed the keys says `save_keys`: this approval is the instructor's save of them.)
        command = {"request_id": str(uuid.uuid4()), "revision_id": result["revision_id"], "confirmation": "approve", "copies": 1, "save_keys": True,
                   "sitting": {"label": "Makeup", "date": "2026-09-24", "time": "10:00", "location": "BLOC 164"}}
        with connect(self.path) as db:
            alice = db.execute("SELECT m.section_id,m.enrollment_id FROM roster_memberships m JOIN student_enrollments e ON e.id=m.enrollment_id "
                               "WHERE e.uin='001234567'").fetchone()
        command["students"] = [[alice[0], alice[1]]]
        with patch("grading.makeup_drafts.plan_progress.latest_certification", return_value={"id": "c1"}):
            with self.assertRaisesRegex(Conflict, "certified"):
                self.frq.approve_quiz_draft(draft["id"], command)
        with self.assertRaisesRegex(ValueError, "makeup sitting"):
            self.frq.approve_quiz_draft(draft["id"], {**command, "request_id": str(uuid.uuid4()), "sitting": None})
        self.frq.approve_quiz_draft(draft["id"], command)
        self.frq.process_cloud()
        approval = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))
        made = approval["result"]
        self.assertEqual((made["letter"], made["plan_id"], made["placed"]), ("C", plan_id, 1))
        # Every earlier pin stays exactly; Version C's questions are added beside them.
        after = self.pins(plan_id)
        self.assertTrue(before < after)
        plan = self.frq.assignment(plan_id)
        makeup = sorted((q for q in plan["questions"] if q["exam_version"] == "C"), key=lambda q: q["content"]["printed_number"])
        reference = sorted((q for q in plan["questions"] if q["exam_version"] == "A"), key=lambda q: q["content"]["printed_number"])
        self.assertEqual(len(makeup), 2)
        for mine, theirs in zip(makeup, reference):
            self.assertEqual([(i["id"], i["points"]) for i in mine["rubric"]["rules"]["items"]], [(i["id"], i["points"]) for i in theirs["rubric"]["rules"]["items"]])
            self.assertEqual(mine["rubric"]["compiler"]["adapted_from"]["question_id"], theirs["question_id"])
            self.assertEqual(mine["assignment_version_id"], made["versions"]["C"])
            # The makeup's questions were created with the keys its draft showed; the quiz's own, approved without `save_keys`
            # (as from a tab opened before), have none. The key was read against the very lines the makeup's question got.
            self.assertEqual((mine["content"]["answer_key"], theirs["content"]["answer_key"]), (keys[mine["content"]["printed_number"]]["key"], []))
            self.assertEqual(read[mine["content"]["printed_number"]]["rubric_lines"], [{k: i[k] for k in ("id", "points", "description")} for i in mine["rubric"]["rules"]["items"]])
        self.assertIs(approval["input"]["save_keys"], True)
        self.assertEqual((approval["progress"]["questions"]["count"], approval["progress"]["questions"]["keys"]), (2, 2))
        with connect(self.path) as db:
            self.assertIn("C", self.frq.added_versions(db, self.frq.plan(db, plan_id)))
            status = self.frq.rubric_quality_status(db, plan_id)
            self.assertTrue(all(status["readiness"][q["question_id"]]["ready"] for q in makeup))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        # The rubric review is requested for the instructor (ABSENCES "MAKEUP BOOKING", 2026-10-04); finalizing stays theirs. Here the
        # quiz's own review is still waiting, so approval says so, and the booking worker asks once nothing runs.
        self.assertFalse(approval["progress"]["review"]["requested"])
        self.assertIn("The rubric review was not requested (A rubric review is already queued or running", " ".join(made["notes"]))
        booking = self.app.extensions["absences"].booking
        self.assertFalse(booking._review_one([]))  # still running: nothing asked
        self.frq.cancel_rubric_quality(plan_id, {})
        self.assertTrue(booking._review_one([]))
        self.assertFalse(booking._review_one([]))  # once per rubric set
        with connect(self.path) as db:
            asked = [r[0] for r in db.execute("SELECT request_id FROM frq_rubric_quality_checks WHERE assignment_id=?", (plan_id,)) if r[0].startswith("makeup-review:")]
            self.assertEqual(len(asked), 1)
            self.assertTrue(asked[0].startswith(f"makeup-review:{plan_id}:"))
            # The review reads each makeup problem beside the key saved with its question.
            reviewed = {q["question_id"]: q["reference"]["answer_key"] for q in json.loads(db.execute(
                "SELECT input_json FROM frq_rubric_quality_checks WHERE request_id=?", (asked[0],)).fetchone()[0])["questions"]}
            self.assertEqual([reviewed[q["question_id"]] for q in makeup], [keys["1"]["key"], keys["2"]["key"]])
        # One unnamed print job attached to the new makeup sitting; the ticked student is on it, still expected.
        event = self.frq.exams.get(done["event_id"])
        sitting = next(s for s in event["sittings"] if s["kind"] == "makeup")
        self.assertEqual((sitting["date"], sitting["location"], made["sitting_id"]), ("2026-09-24", "BLOC 164", sitting["id"]))
        job = next(b for b in event["batches"] if b["id"] == made["batches"][0]["batch_id"])
        self.assertEqual((job["version_label"], job["sitting_id"], job["quantity"]), ("C", sitting["id"], 1))
        person = next(p for p in event["participants"] if (p["section_id"], p["enrollment_id"]) == tuple(alice))
        self.assertEqual((person["sitting_id"], person["expectation"]), (sitting["id"], "expected"))
        # The Absences page's makeups name the version approved for this plan; the next makeup would be D.
        self.assertEqual(self.frq.makeup_options(plan_id)["letter"], "D")
        with connect(self.path) as db:
            from absences.makeups import _makeup_versions
            self.assertEqual([v["letter"] for v in _makeup_versions(db)[plan_id]], ["C"])
        # A makeup approval is never rebuilt.
        with self.assertRaisesRegex(ValueError, "not rebuilt"):
            self.frq.rebuild_quiz_approval(draft["id"], approval["id"], {"request_id": str(uuid.uuid4()), "confirmation": "rebuild"})

    def test_a_makeup_waiting_on_the_instructor_is_on_its_assignment(self):
        """ASSIGNMENT-WORKSPACE "A MAKEUP VERSION WAITING TO BE APPROVED" (requirement, 2026-11-05): one needs-you item from ready until it is approved or scrapped, on the list and the
        assignment alike, and none while the model still drafts it."""
        plan_id = self.approved_quiz()["plan_id"]

        def row():
            return next(r for r in self.client.get("/api/assignments").json["assignments"] if r["plan_id"] == plan_id)

        def makeups():
            return [i for i in row()["needs_you"] if i["key"] == "makeup"]

        self.assertEqual(makeups(), [])
        draft = self.frq.create_makeup_draft(plan_id, {"request_id": "mk-waits"})
        self.assertEqual(makeups(), [])  # the model is still drafting it
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        self.assertEqual(draft["state"], "ready", [r.get("error") for r in draft["revisions"]])
        with connect(self.path) as db:
            self.assertEqual(self.frq.makeups_waiting(db), {plan_id: {"letter": "C", "state": draft["state"]}})
        listed = row()
        area = f"#assignments/{listed['assignment_id']}/{listed['term_id']}?area=create&makeup=1"
        self.assertEqual(makeups(), [{"key": "makeup", "label": "Approve makeup version C", "href": area, "count": None, "unit": "setup"}])
        self.assertIn("preparation", listed["filters"])
        detail = self.client.get(f"/api/assignments/{listed['assignment_id']}/{listed['term_id']}?delivery=0").json
        self.assertIn(makeups()[0], detail["needs_you"])
        # One card on Home and the Assignments badge (instructor, the same day: "show on the notification badge"), never also in the Makeups card.

        def cards():
            return [c for c in self.client.get("/api/workspace/overview").json["attention"] if c["key"] == f"makeup:{plan_id}"]

        card = cards()[0]
        self.assertEqual((card["label"], card["count"], card["href"], card["assignment_work"], card["verb"]),
                         ("Approve makeup version C · Chapter 3 Quiz", 1, area, True, "Approve"))
        # Only a current term's draft asks for the badge.
        with connect(self.path) as db:
            db.execute("UPDATE course_terms SET is_current=0")
        self.assertEqual(cards(), [])
        with connect(self.path) as db:
            db.execute("UPDATE course_terms SET is_current=1")
        # A draft with nothing to approve waits on them too, in its own words.
        with patch.object(self.frq, "makeups_waiting", return_value={plan_id: {"letter": "C", "state": "failed"}}):
            self.assertEqual(makeups()[0]["label"], "Try again: makeup draft C")
            self.assertEqual((cards()[0]["label"], cards()[0]["verb"]), ("Try again: makeup draft C · Chapter 3 Quiz", "Open"))
        # Approved, the version is in the pipeline, and its rubrics are Prepare's.
        self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": draft["latest_complete"], "confirmation": "approve", "copies": 0})
        self.assertEqual((makeups(), cards()), ([], []))
        self.frq.process_cloud()
        self.assertEqual(makeups(), [])
        # A second draft, scrapped: gone at once.
        second = self.frq.create_makeup_draft(plan_id, {"request_id": "mk-waits-2"})
        self.frq.process_cloud()
        self.assertEqual(makeups()[0]["label"], "Approve makeup version D")
        self.assertEqual(len(cards()), 1)
        self.frq.scrap_quiz_draft(second["id"], {"confirmation": "scrap"})
        self.assertEqual((makeups(), cards()), ([], []))

    def test_a_makeup_asked_for_changes_is_checked_only_where_it_is_printed_otherwise(self):
        """QUIZZES "A REVISION" (instructor's decision, 2026-10-11): a makeup's revisions share the quiz draft's check, so a later one
        asks only about the problems not printed as the earlier check saw them, and sends no check when nothing printed changed."""
        done = self.approved_quiz()
        self.frq.cancel_rubric_quality(done["plan_id"], {})  # the review the quiz's approval asked for is stopped (the fixture model cannot answer it)
        part_b, checks = [None], []

        def answer(body, host):
            name, payload = prompt_name(body), payload_of(body)
            if name == "makeup_write":
                quiz = quiz_answer(payload)
                if part_b[0]:
                    quiz["problems"][1]["versions"][0]["parts"][1]["text"] = part_b[0]
                return quiz
            if name == "quiz_check":
                checks.append(payload)
            return MakeupDrafts.answer(self, body, host)

        def revision(draft_id, based_on=None):
            before = len(self.host.requests)
            if based_on:
                self.frq.revise_quiz_draft(draft_id, {"request_id": str(uuid.uuid4()), "based_on": based_on, "feedback": "Change what I said."})
            self.frq.process_cloud()
            draft = self.frq.quiz_draft(draft_id)
            found = next((r for r in draft["revisions"] if r["id"] == draft["latest_complete"]), {})
            self.assertEqual((draft["state"], found.get("based_on")), ("ready", based_on), [r.get("error") for r in draft["revisions"]])
            return found, [prompt_name(r["body"]) for r in self.host.requests[before:]]
        self.host.script = answer
        draft = self.frq.create_makeup_draft(done["plan_id"], {"request_id": "mk-check"})
        first, names = revision(draft["id"])
        # The first revision's check is asked as before: the makeup's page, nothing named unchanged.
        # (Then each problem's answer key is read once: 2026-10-11, "Carry the key through".)
        self.assertEqual(names, ["makeup_write", "quiz_check", "rubric_adapt", "rubric_adapt", "answer_key_read", "answer_key_read"])
        self.assertEqual((sorted(checks[0]), [(p["version"], p["page"]) for p in checks[0]["pictures"] if "version" in p]), (["pictures", "quiz", "request"], [("C", 1)]))
        self.assertEqual((sorted(first["result"]["check"]["seen"]["problems"]), bool(first["result"]["check"]["seen"]["whole"])), (["1", "2"], True))
        # Problem 2's part (b) asks otherwise: the check is asked about problem 2 only, and its rubric is adapted again.
        part_b[0] = "Find $y$ when $x = 8$."
        second, names = revision(draft["id"], first["id"])
        self.assertEqual(names, ["makeup_write", "quiz_check", "rubric_adapt", "answer_key_read"])
        self.assertEqual((checks[1]["unchanged"], second["result"]["check"]["unchanged"]), ([1], [1]))
        # Nothing printed changes: no check is sent, and the revision is finished from what the earlier check saw.
        third, names = revision(draft["id"], second["id"])
        self.assertEqual((names, len(checks)), (["makeup_write"], 2))
        self.assertEqual((third["result"]["check"]["unchanged"], third["result"]["check"]["seen"]), ([1, 2], second["result"]["check"]["seen"]))
        self.assertEqual(third["result"]["quiz"], second["result"]["quiz"])

    def test_a_rubric_changed_after_drafting_is_adapted_from_its_new_lines_and_approved(self):
        """CURRENT RUBRICS (requirement, 2026-11-01: Quiz 6's Version E was refused twice): an original rubric edited after the makeup was
        drafted refuses the approval; a revision then adapts that problem from the rubric as it is now, and its approval goes through."""
        done = self.approved_quiz()
        plan_id = done["plan_id"]
        draft = self.frq.create_makeup_draft(plan_id, {"request_id": "mk-current"})
        self.frq.process_cloud()
        first = self.frq.quiz_draft(draft["id"])["latest_complete"]
        question = next(q for q in self.frq.assignment(plan_id)["questions"] if q["exam_version"] == "A" and q["content"]["printed_number"] == "2")
        line = question["rubric"]["rules"]["items"][0]
        self.frq.edit_rubric(plan_id, question["question_id"], {"version": question["version"], "rubric_id": question["rubric_id"],
                                                                "sha256": question["rubric"]["sha256"],
                                                                "lines": [{"item_id": line["id"], "description": "Edited after the makeup was drafted."}]})
        command = {"request_id": str(uuid.uuid4()), "revision_id": first, "confirmation": "approve", "copies": 0, "sitting": None}
        self.frq.approve_quiz_draft(draft["id"], command)
        self.frq.process_cloud()
        refused = self.frq.quiz_draft(draft["id"])
        self.assertIsNone(refused["approved"])
        self.assertIn("changed after the makeup was drafted", refused["approvals"][-1]["error"])
        self.host.requests.clear()
        self.frq.revise_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "based_on": first, "feedback": "Adapt the changed rubric again."})
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        second = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])
        self.assertNotEqual(second["id"], first)
        adapted = {r["problem"]: r for r in second["result"]["rubrics"]}
        current = self.frq.assignment(plan_id)
        rubric_id = next(q["rubric_id"] for q in current["questions"] if q["question_id"] == question["question_id"])
        self.assertEqual(adapted[2]["source"]["rubric_id"], rubric_id)
        adapts = [payload_of(r["body"]) for r in self.host.requests if prompt_name(r["body"]) == "rubric_adapt"]
        self.assertIn("Edited after the makeup was drafted.", json.dumps(adapts))
        self.frq.approve_quiz_draft(draft["id"], {**command, "request_id": str(uuid.uuid4()), "revision_id": second["id"]})
        self.frq.process_cloud()
        approval = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))

    def test_a_revision_drafted_with_the_old_finding_is_read_again(self):
        """GRADING "LEFTOVER VALUES": Quizzes 3 and 5 were waiting with the old sentence stored, and a stored result never changes. The window reads it on the new rule."""
        plan_id = self.approved_quiz()["plan_id"]
        draft = self.frq.create_makeup_draft(plan_id, {"request_id": "mk-old"})
        self.frq.process_cloud()
        latest = self.frq.quiz_draft(draft["id"])["latest_complete"]
        old = "R1 is word for word Version A's, though it names values: check it for this version."
        with connect(self.path) as db:
            stored = json.loads(db.execute("SELECT result_json FROM quiz_draft_revisions WHERE id=?", (latest,)).fetchone()[0])
            first, second = stored["rubrics"][:2]
            for entry, wording in ((first, "Applied the rule on the sheet"), (second, "Value $$987654$$ missing")):
                entry["rubric"]["warnings"].append(old)
                stored["warnings"].append(f"Problem {entry['problem']} rubric: {old}")
                entry["rubric"]["items"][0]["description"] = entry["rubric"]["reference_items"][0]["description"] = wording
            shown = self.frq._makeup_leftovers_now(db, draft["id"], stored)
        self.assertNotIn(old, json.dumps(shown))
        self.assertEqual(shown["rubrics"][0]["rubric"]["warnings"], [w for w in first["rubric"]["warnings"] if w != old])
        named = "R1 (987654) still names Version A's values, which this version's problem and answers do not have: check it."
        self.assertIn(named, shown["rubrics"][1]["rubric"]["warnings"])
        self.assertIn(f"Problem {second['problem']} rubric: {named}", shown["warnings"])
        self.assertIn(old, first["rubric"]["warnings"])  # What was read stays as it was.
        # The window's own read goes through it; a revision with no such sentence comes back as stored.
        with patch.object(type(self.frq), "_makeup_leftovers_now", wraps=self.frq._makeup_leftovers_now) as reading:
            self.frq.quiz_revision(draft["id"], latest)
        reading.assert_called_once()
        with connect(self.path) as db:
            plain = {"warnings": ["Check the room."], "rubrics": []}
            self.assertIs(self.frq._makeup_leftovers_now(db, draft["id"], plain), plain)

    def test_a_makeup_approved_without_save_keys_saves_no_key(self):
        """A window opened before it showed the keys approves as it always did (2026-10-11): the makeup's questions start without a
        key, and the approval reads as an earlier one."""
        done = self.approved_quiz()
        draft = self.frq.create_makeup_draft(done["plan_id"], {"request_id": "mk-old"})
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        self.assertEqual(draft["state"], "ready", [r.get("error") for r in draft["revisions"]])
        result = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])["result"]
        self.assertTrue(all(r["keys"]["C"]["key"] for r in result["rubrics"]))  # read and shown all the same
        self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": draft["latest_complete"], "confirmation": "approve", "copies": 0, "sitting": None})
        self.frq.process_cloud()
        approval = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))
        self.assertNotIn("save_keys", approval["input"])
        self.assertEqual(sorted(approval["progress"]["questions"]), ["at", "count"])
        makeup = [q for q in self.frq.assignment(done["plan_id"])["questions"] if q["exam_version"] == "C"]
        self.assertEqual([q["content"]["answer_key"] for q in makeup], [[], []])

    def test_a_makeup_writer_that_changes_the_structure_is_refused(self):
        done = self.approved_quiz()

        def answer(body, host):
            if prompt_name(body) == "makeup_write":
                bad = quiz_answer(payload_of(body))
                bad["problems"][1]["points"] = 4
                bad["problems"][1]["parts"][1]["points"] = 3
                return bad
            return MakeupDrafts.answer(self, body, host)
        self.host.script = answer
        draft = self.frq.create_makeup_draft(done["plan_id"], {"request_id": "mk-bad"})
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        self.assertEqual(draft["state"], "failed")
        self.assertIn("worth", draft["revisions"][-1]["error"])
