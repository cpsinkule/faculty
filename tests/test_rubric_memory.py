"""Rubric memories (GRADING "RUBRIC MEMORIES", requirement, 2026-10-14): general rules and watches learned from one certified
assignment's rubric questions, the instructor's answers and the graders' doubts, kept or dropped by the instructor, and read by the
rubric writers and the rubric check. Synthetic rubric data and loopback models."""
import json
import sqlite3
import uuid
from unittest.mock import patch

from rosters import connect
from fake_openai import grade_answer, payload_of, prompt_name
from grading.models import encoded
from grading import rubric_memory
from grading.rubric_memory import SPECIFIC, read_memories, standing, standing_rules
from test_grading_rubric_quality import QualityCase, suggestion
from test_gradescope_rubrics import encode, export_rows
from test_storage import HEADERS


RULE_TEXT = "A value wrong only because of a mistake already charged on an earlier line is not charged again on a later line."
WATCH_TEXT = "Where a line requires a label, say which near-labels count, such as the name without its equals sign."


class FakeJev:
    """Jev as the rubric memories ask it: `judge(purpose, state, name)` gives each question's probability (None: no answer)."""
    def __init__(self, judge, status="complete"):
        self.judge, self.status, self.calls = judge, status, []

    def ask(self, purpose, owner, state, questions, rule, enrollment=None, attempt="", images=(), receipt=None):
        self.calls.append((purpose, owner, state, sorted(questions), rule))
        if self.status != "complete":
            return {"id": str(uuid.uuid4()), "status": self.status}
        return {"id": str(uuid.uuid4()), "status": "complete", "answers": {name: {"noul": self.judge(purpose, state, name)} for name in questions}}


def memory_answer(payload, new=None, revisions=None):
    ruling = payload["rulings"][0]["id"] if payload["rulings"] else None
    return {"new": new if new is not None else [
        {"rule": RULE_TEXT, "covers": "Any later line that a student's earlier, already charged mistake carries into.", "not_when": "",
         "kind": "rule", "sources": [ruling]},
        {"rule": WATCH_TEXT, "covers": "Any line that pays for a label on an answer.", "not_when": "", "kind": "watch",
         "sources": [d["id"] for d in payload["grader_doubts"][:1]] or [ruling]}],
        "revisions": revisions or [], "not_general": []}


class Memories(QualityCase):
    def setUp(self):
        super().setUp()
        self.service = self.frq.rubric_memory
        self.service.names = lambda term_id=None: ["Rosen, Petra"]
        with connect(self.path) as db:
            self.definition = db.execute("""SELECT d.definition_id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id
                JOIN assignments d ON d.id=e.assignment_id WHERE a.id=?""", (self.plan["id"],)).fetchone()[0]

    # -- helpers --------------------------------------------------------------------------------------------------------------------
    def memory(self, number, kind="rule", text=RULE_TEXT, action="new", replaces=None, at="2026-10-14T00:00:00+00:00"):
        mid = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("""INSERT INTO frq_rubric_memories(id,definition_id,number,kind,action,replaces_id,rule,covers,not_when,why,sources_json,run_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?)""", (mid, self.definition, number, kind, action, replaces, text, "Any such line.", "", "",
                                                           encoded({"assignment_id": self.plan["id"], "ids": ["R1"]}), at))
        return mid

    def decide(self, mid, decision, at):
        with connect(self.path) as db:
            db.execute("INSERT INTO frq_rubric_memory_decisions(id,memory_id,decision,actor,created_at) VALUES(?,?,?,'workspace',?)", (str(uuid.uuid4()), mid, decision, at))

    def answered_check(self, answer="Charge it once, on the earlier line; Petra Rosen's case is like every other."):
        """A completed check whose input answers one concern (Resolve concerns), and a fresh one that raised it: the evidence."""
        aid, qid = self.plan["id"], self.qid
        concern = {"kind": "suspected_error", "question_ids": [qid], "item_ids": ["R2"], "message": "Is a carried mistake charged again on R3?",
                   "title": "Carried mistake charged twice?", "response": answer}
        now = "2026-10-14T01:00:00+00:00"
        with connect(self.path) as db:
            question = {"question_id": qid, "title": "Question 1", "printed_number": "1", "max_points": 10, "exam_version": "",
                        "rubric": json.loads(db.execute("SELECT b.rules_json FROM frq_assignment_questions p JOIN frq_rubric_revisions b ON b.id=p.rubric_id WHERE p.assignment_id=?",
                                                        (aid,)).fetchone()[0])}
            for n, given in enumerate(({"questions": [question]}, {"questions": [question], "resolves": {"concerns": [concern]}})):
                db.execute("""INSERT INTO frq_rubric_quality_checks(id,assignment_id,request_id,request_sha256,input_sha256,input_json,profile_json,status,result_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,'complete',?,?,?)""", (str(uuid.uuid4()), aid, str(uuid.uuid4()), "0" * 64, "0" * 64, encoded(given),
                                                               encoded({"endpoint": {"name": "beta-text"}, "settings": {}}),
                                                               encoded({"questions": [], "suspected_errors": [{**concern, "question_id": qid}] if not n else []}), now, now))

    def learned(self, script=None):
        self.host.script = lambda body, host: (script or memory_answer)(payload_of(body)) if prompt_name(body) == "rubric_memory" else suggestion(payload_of(body))
        rid = self.service.queue(self.plan["id"], "instructor")
        self.frq.process_cloud()
        with connect(self.path) as db:
            return rid, dict(db.execute("SELECT * FROM frq_rubric_memory_runs WHERE id=?", (rid,)).fetchone())

    def asked(self, name):
        return [payload_of(r["body"]) for r in self.host.requests if r["path"].endswith("/chat/completions") and prompt_name(r["body"]) == name]

    # -- what stands -----------------------------------------------------------------------------------------------------------
    def test_only_kept_memories_stand_and_a_kept_revision_replaces_what_it_revises(self):
        rule, watch = self.memory(1), self.memory(2, "watch", WATCH_TEXT)
        with connect(self.path) as db:
            self.assertEqual(standing(db, self.definition), [])  # proposed is not kept
        self.decide(rule, "keep", "2026-10-14T02:00:00+00:00")
        self.decide(watch, "keep", "2026-10-14T02:00:00+00:00")
        narrowed = self.memory(1, text="A value wrong only because of an earlier charged mistake is charged once.", action="narrow", replaces=rule,
                               at="2026-10-14T03:00:00+00:00")
        retired = self.memory(2, "watch", WATCH_TEXT, action="retire", replaces=watch, at="2026-10-14T03:00:00+00:00")
        with connect(self.path) as db:
            self.assertEqual([m["id"] for m in standing(db, self.definition)], [rule, watch])  # revisions wait for the instructor
        self.decide(narrowed, "keep", "2026-10-14T04:00:00+00:00")
        self.decide(retired, "keep", "2026-10-14T04:00:00+00:00")
        with connect(self.path) as db:
            now = standing_rules(db, self.definition)
            self.assertEqual([(m["id"], m["rule"][:40]) for m in now], [("M1", "A value wrong only because of an earlier")])
            # As it stood at an earlier moment: what a draft asked for then reads, on every retry.
            self.assertEqual([m["id"] for m in standing_rules(db, self.definition, "2026-10-14T02:30:00+00:00")], ["M1", "M2"])
            self.assertEqual(standing_rules(db, self.definition, "2026-10-14T01:00:00+00:00"), [])
        self.decide(narrowed, "drop", "2026-10-14T05:00:00+00:00")  # taken back: the memory it revised stands again
        with connect(self.path) as db:
            self.assertEqual([m["id"] for m in standing(db, self.definition)], [rule])
            for sql in ("UPDATE frq_rubric_memories SET rule='x'", "DELETE FROM frq_rubric_memories", "UPDATE frq_rubric_memory_decisions SET decision='drop'",
                        "DELETE FROM frq_rubric_memory_decisions"):
                with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                    db.execute(sql)

    # -- what the rubric check reads -------------------------------------------------------------------------------------------
    def test_the_check_reads_kept_memories_and_its_confirmation_does_not_cover_them(self):
        before = self.frq.rubric_quality(self.plan["id"])["input_sha256"]
        self.check()
        self.assertNotIn("standing_rules", self.asked("rubric_quality")[-1])  # a course without memories sends what it always sent
        rule = self.memory(1)
        self.decide(rule, "keep", "2026-10-14T02:00:00+00:00")
        self.assertEqual(self.frq.rubric_quality(self.plan["id"])["input_sha256"], before)  # memories are in no fingerprint
        checked = self.check()
        sent = self.asked("rubric_quality")[-1]
        self.assertEqual(sent["standing_rules"], [{"id": "M1", "kind": "rule", "rule": RULE_TEXT, "covers": "Any such line.", "not_when": ""}])
        with connect(self.path) as db:
            stored = json.loads(db.execute("SELECT input_json FROM frq_rubric_quality_checks WHERE id=?", (checked["check"]["id"],)).fetchone()[0])
        self.assertEqual(stored["standing_rules"][0]["id"], "M1")
        confirmed = self.confirm(checked)
        self.assertEqual(confirmed["state"], "confirmed")
        record = self.frq.confirmation_record
        with connect(self.path) as db:
            self.assertIsNotNone(record(db, confirmed["review"]["id"])["inputs"])  # the fingerprint reproduces without the memories

    def test_a_resolution_reads_the_memories_as_they_stand_and_not_as_its_check_froze_them(self):
        # 2026-11-09 review: Resolve concerns copied the earlier check's input, so a memory dropped since still reached the reviewer.
        rule, watch = self.memory(1), self.memory(2, "watch", WATCH_TEXT)
        self.decide(rule, "keep", "2026-10-14T02:00:00+00:00")
        checked = self.check()
        self.assertEqual([m["id"] for m in self.asked("rubric_quality")[-1]["standing_rules"]], ["M1"])
        self.service.decide(self.definition, {"decisions": [{"memory_id": rule, "decision": "drop"}, {"memory_id": watch, "decision": "keep"}]})
        import copy
        queued = self.frq.resolve_rubric_concerns(self.plan["id"], {
            "check_id": checked["check"]["id"], "input_sha256": checked["input_sha256"], "request_id": str(uuid.uuid4()),
            "questions": copy.deepcopy(checked["check"]["result"]["questions"]), "responses": [{"concern": 0, "response": "The key accepts 4 for R3."}]})
        with connect(self.path) as db:
            stored = json.loads(db.execute("SELECT input_json FROM frq_rubric_quality_checks WHERE id=?", (queued["check"]["id"],)).fetchone()[0])
            earlier = json.loads(db.execute("SELECT input_json FROM frq_rubric_quality_checks WHERE id=?", (checked["check"]["id"],)).fetchone()[0])
        self.assertEqual(([m["id"] for m in earlier["standing_rules"]], [m["id"] for m in stored["standing_rules"]]), (["M1"], ["M2"]))

    def test_a_plan_graded_to_the_coordinators_rubric_reads_the_rules_and_its_pass_is_kept_at_once(self):
        """THE COORDINATOR'S RUBRIC, REPLACED 2026-10-28 by FITTED TO EACH QUESTION: a plan whose rubrics came from Gradescope reads
        the course's memories like any other, and what a pass learns from it is kept at once."""
        self.decide(self.memory(1), "keep", "2026-10-14T02:00:00+00:00")
        self.frq.upload_rubric(self.plan["id"], "1_Question_2.csv", encode(export_rows(question=2)).encode(), {"request_id": str(uuid.uuid4())})
        self.check()
        self.assertEqual([m["id"] for m in self.asked("rubric_quality")[-1]["standing_rules"]], ["M1"])
        self.answered_check()
        _, run = self.learned()
        self.assertEqual(run["status"], "complete", run["error"])
        view = self.service.view(self.definition)
        self.assertEqual(([m["label"] for m in view["standing"]], view["waiting"]), (["M1", "M2", "M3"], []))

    # -- FITTED TO EACH QUESTION (2026-10-28) ---------------------------------------------------------------------------------
    def test_a_memory_jev_finds_contradicting_the_question_is_left_out_of_its_request(self):
        self.decide(self.memory(1), "keep", "2026-10-14T02:00:00+00:00")
        self.decide(self.memory(2, "watch", WATCH_TEXT), "keep", "2026-10-14T02:00:00+00:00")
        # M1 contradicts the instructor's word on this question at .82; nothing else reaches .7.
        self.frq.jev = FakeJev(lambda purpose, state, name: .82 if name == "instructor_0" and state["memories"][0]["rule"] == RULE_TEXT else .3)
        checked = self.check()
        self.assertEqual(checked["check"]["status"], "complete")
        sent = self.asked("rubric_quality")[-1]
        self.assertEqual([m["id"] for m in sent["standing_rules"]], ["M2"])
        purpose, owner, state, questions, rule = self.frq.jev.calls[-1]
        self.assertEqual((purpose, owner, rule, questions), (rubric_memory.FIT_PURPOSE, checked["check"]["id"], rubric_memory.FIT_RULE,
                                                              ["instructor_0", "instructor_1", "rubric_0", "rubric_1"]))
        self.assertEqual((state["question"]["question"], [m["rule"] for m in state["memories"]]), ("Question 1", [RULE_TEXT, WATCH_TEXT]))
        self.assertIn("rubric_lines", state["question"])
        with connect(self.path) as db:  # the stored input still holds what stands; only the request is fitted
            stored = json.loads(db.execute("SELECT input_json FROM frq_rubric_quality_checks WHERE id=?", (checked["check"]["id"],)).fetchone()[0])
        self.assertEqual([m["id"] for m in stored["standing_rules"]], ["M1", "M2"])
        self.assertNotIn("memory_fit", stored)

    def test_every_memory_left_out_sends_the_request_without_rules(self):
        self.decide(self.memory(1), "keep", "2026-10-14T02:00:00+00:00")
        self.frq.jev = FakeJev(lambda purpose, state, name: .9 if name == "rubric_0" else .1)
        self.check()
        self.assertNotIn("standing_rules", self.asked("rubric_quality")[-1])

    def test_without_an_answer_from_jev_every_memory_is_sent(self):
        self.decide(self.memory(1), "keep", "2026-10-14T02:00:00+00:00")
        for jev in (None, FakeJev(lambda *_: .99, status="uncertain"), FakeJev(lambda *_: None)):
            with self.subTest(jev=jev and jev.status):
                self.frq.jev = jev
                self.check()
                self.assertEqual([m["id"] for m in self.asked("rubric_quality")[-1]["standing_rules"]], ["M1"])

    def test_the_fit_state_reads_the_instructors_word_on_that_question_only(self):
        context = {"earlier_answers": [{"asked": "a", "answered": "on 1", "about": ["Version A · Question 1"]},
                                       {"asked": "b", "answered": "on 2", "about": ["Version A · Question 2"]},
                                       {"asked": "c", "answered": "draft", "about": ["At the quiz draft review"]}],
                   "assignment_choices": [{"choice": "rounding", "answer": "two places"}],
                   "resolves": {"concerns": [{"question_ids": ["q1"], "decision": "d", "response": "now"}, {"question_ids": ["q2"], "decision": "e", "response": "other"}]}}
        question = {"question_id": "q1", "exam_version": "A", "title": "Question 1", "printed_number": "1", "max_points": 4,
                    "rubric": {"items": [{"id": "R1", "description": "Correct", "points": 4, "notes": ""}]}, "comments": {"R1": "Decimals count."}}
        state = rubric_memory.fit_state(context, question)
        self.assertEqual([a["answered"] for a in state["instructor_answers"]], ["on 1", "draft", "now"])
        self.assertEqual((state["question"]["rubric_lines"], state["question"]["clarifications"], state["assignment_choices"]),
                         ([{"id": "R1", "description": "Correct", "points": 4}], {"R1": "Decimals count."}, context["assignment_choices"]))

    # -- NEVER WRITTEN AGAIN (2026-10-28) -------------------------------------------------------------------------------------
    def test_a_pass_does_not_write_again_what_the_instructor_dropped(self):
        dropped = self.memory(1)
        self.decide(dropped, "drop", "2026-10-14T00:30:00+00:00")
        self.answered_check()
        self.frq.jev = FakeJev(lambda purpose, state, name: .91 if purpose == rubric_memory.SAME_PURPOSE and state["new_memory"]["rule"] == RULE_TEXT else .05)
        _, run = self.learned()
        self.assertEqual(run["status"], "complete", run["error"])
        view = self.service.view(self.definition)
        self.assertEqual([(m["label"], m["rule"]) for m in view["standing"]], [("M2", WATCH_TEXT)])  # the rule M1 said is not written again
        result = json.loads(run["result_json"])
        self.assertEqual(result["dropped_again"], [{"kind": "rule", "rule": RULE_TEXT, "same_as": ["M1"]}])
        self.assertEqual(len(result["created"]), 1)
        # Without Jev it is written as before.
        self.frq.jev = None
        self.assertEqual([m["rule"] for m in rubric_memory.not_dropped(None, "x", [{"rule": RULE_TEXT}], [{"number": 1}])[0]], [RULE_TEXT])

    # -- learning --------------------------------------------------------------------------------------------------------------
    def test_a_pass_reads_answers_and_grader_doubts_without_names_and_keeps_what_it_writes(self):
        doubt = "R2: the rubric does not say whether Petra Rosen's label C counts as C(x); student 123456789 wrote it."
        self.grade(["3x - 8 = 10"], primary=["R2"], script=lambda name, body: {**grade_answer(payload_of(body), ["R2"]), "rubric_ambiguities": [doubt]}
                   if name in ("grade", "primary_vision") else None)
        self.answered_check()  # after grading, which confirms the plan's own finalization first
        rid, run = self.learned()
        self.assertEqual(run["status"], "complete", run["error"])
        sent = self.asked("rubric_memory")[-1]
        text = json.dumps(sent)
        self.assertNotIn("Petra", text)
        self.assertNotIn("Rosen", text)
        self.assertNotIn("123456789", text)
        self.assertEqual(sent["rulings"][0]["instructor_answered"], "Charge it once, on the earlier line; the student's case is like every other.")
        self.assertEqual(sent["grader_doubts"][0]["line"].split(" ")[0], "R2")
        self.assertEqual(sent["memories"], [])
        view = self.service.view(self.definition)
        # Kept at once (GRADING "RUBRIC MEMORIES" KEPT AT ONCE); the instructor's Drop takes one back.
        self.assertEqual([(m["label"], m["kind"], m["state"]) for m in view["standing"]], [("M1", "rule", "kept"), ("M2", "watch", "kept")])
        self.assertEqual((view["waiting"], view["standing"][0]["evidence"]["answers"], view["standing"][0]["from"]), ([], 1, self.event["title"]))
        with connect(self.path) as db:
            self.assertEqual({r[0] for r in db.execute("SELECT actor FROM frq_rubric_memory_decisions")}, {"automatic"})
        view = self.service.decide(self.definition, {"decisions": [{"memory_id": view["standing"][1]["id"], "decision": "drop"}]})
        self.assertEqual(([m["label"] for m in view["standing"]], [m["label"] for m in view["dropped"]]), (["M1"], ["M2"]))
        # The same evidence asks nothing again.
        requests = len(self.asked("rubric_memory"))
        _, again = self.learned()
        self.assertEqual((again["status"], len(self.asked("rubric_memory"))), ("complete", requests))
        self.assertEqual(json.loads(again["result_json"])["same_as"], rid)

    def test_an_answer_in_this_assignments_own_terms_is_refused_once_with_the_reason(self):
        self.answered_check()
        answers = iter([
            lambda p: memory_answer(p, new=[{"rule": "Charge R3 once.", "covers": "Version A's R3.", "not_when": "", "kind": "rule", "sources": [p["rulings"][0]["id"]]}]),
            lambda p: memory_answer(p, new=[{"rule": RULE_TEXT, "covers": "Any later line.", "not_when": "", "kind": "rule", "sources": [p["rulings"][0]["id"]]}]),
        ])
        _, run = self.learned(lambda p: next(answers)(p))
        self.assertEqual(run["status"], "complete", run["error"])
        asked = self.asked("rubric_memory")
        self.assertEqual(len(asked), 2)
        self.assertIn("names this assignment's own line", asked[1]["refused"])
        self.assertEqual([m["rule"] for m in self.service.view(self.definition)["standing"]], [RULE_TEXT])

    def test_a_memory_still_refused_on_the_second_answer_is_left_out_and_the_rest_is_written(self):
        # 2026-11-09 review: one memory the model would not reword failed the whole pass, twice the same, and every other
        # memory of it was lost. The second answer's refused memory is left out and named; a row operation is no line ID.
        self.answered_check()
        rows = "Accept a row operation written R2 - 2R1 or as swapping rows R1 and R3, in the version I wrote or any other."

        def answer(p):
            ruling = p["rulings"][0]["id"]
            return memory_answer(p, new=[{"rule": "Charge R3 once.", "covers": "Version A's R3.", "not_when": "", "kind": "rule", "sources": [ruling]},
                                         {"rule": rows, "covers": "Any line about a row operation.", "not_when": "", "kind": "rule", "sources": [ruling]}])
        _, run = self.learned(answer)
        self.assertEqual(run["status"], "complete", run["error"])
        self.assertEqual(len(self.asked("rubric_memory")), 2)
        self.assertEqual([m["rule"] for m in self.service.view(self.definition)["standing"]], [rows])
        refused = json.loads(run["result_json"])["refused"]
        self.assertEqual(len(refused), 1)
        self.assertIn("names this assignment's own line", refused[0])
        # An answer that is no answer still fails, on the second asking as on the first.
        with self.assertRaises(ValueError):
            read_memories({"new": "none", "revisions": [], "not_general": []}, {"R1"}, {"R1"}, {}, again=True)
        with self.assertRaises(ValueError):
            read_memories({"new": [{"rule": "Charge R3 once.", "covers": "x", "not_when": "", "kind": "rule", "sources": ["R1"]}], "revisions": [], "not_general": []},
                          {"R1"}, {"R1"}, {})

    def test_a_memory_dropped_while_a_pass_reads_is_not_revised_back(self):
        # 2026-11-09 review: what stood was read before the model's answer, a minute or more earlier, and a revision of a memory
        # dropped meanwhile was written and kept at once in its place.
        rule = self.memory(1)
        self.decide(rule, "keep", "2026-10-14T00:30:00+00:00")
        self.answered_check()

        def answer(p):
            self.service.decide(self.definition, {"decisions": [{"memory_id": rule, "decision": "drop"}]})  # while the model writes
            return memory_answer(p, new=[], revisions=[{"id": "M1", "action": "narrow", "why": "Narrower.", "rule": "A value already charged is charged once.",
                                                        "covers": "Later lines.", "not_when": "", "kind": "rule", "sources": [p["rulings"][0]["id"]]}])
        _, run = self.learned(answer)
        self.assertEqual((run["status"], json.loads(run["result_json"])["stale"], json.loads(run["result_json"])["created"]), ("complete", ["M1"], []))
        view = self.service.view(self.definition)
        self.assertEqual((view["standing"], [m["label"] for m in view["dropped"]]), ([], ["M1"]))

    def test_a_kept_retirement_is_listed_and_dropping_it_brings_the_memory_back(self):
        # 2026-11-09 review: a retirement is kept at once like every revision, and was then in no list of the page.
        watch = self.memory(2, "watch", WATCH_TEXT)
        self.decide(watch, "keep", "2026-10-14T02:00:00+00:00")
        self.assertNotIn("retired", self.service.view(self.definition))  # a view with none reads as it always did
        retired = self.memory(2, "watch", WATCH_TEXT, action="retire", replaces=watch, at="2026-10-14T03:00:00+00:00")
        self.decide(retired, "keep", "2026-10-14T04:00:00+00:00")
        view = self.service.view(self.definition)
        self.assertEqual((view["standing"], [(m["id"], m["label"], m["action"], m["replaces"]["id"]) for m in view["retired"]]),
                         ([], [(retired, "M2", "retire", watch)]))
        view = self.service.decide(self.definition, {"decisions": [{"memory_id": retired, "decision": "drop"}]})
        self.assertEqual(([m["id"] for m in view["standing"]], "retired" in view, [m["id"] for m in view["dropped"]]), ([watch], False, [retired]))

    def test_a_pass_reads_without_the_names_of_the_plans_own_term(self):
        # 2026-11-09 review: only the current rosters' names were taken out, and a pass can read a plan of a term that is over.
        asked = []
        self.service.names = lambda term_id=None: asked.append(term_id) or ["Example, Alice"]
        self.answered_check("Charge it once; Alice Example's case is like every other.")
        _, run = self.learned()
        self.assertEqual(run["status"], "complete", run["error"])
        with connect(self.path) as db:
            term = db.execute("SELECT e.term_id FROM frq_assignments a JOIN exam_events e ON e.id=a.event_id WHERE a.id=?", (self.plan["id"],)).fetchone()[0]
        self.assertEqual((set(asked), bool(term)), ({term}, True))
        self.assertNotIn("Alice", json.dumps(self.asked("rubric_memory")[-1]))
        # The app reads that term's roster when the term is not a current one, and a roster it cannot read fails the pass.
        notes = self.app.extensions["student_notes"]
        self.assertEqual(notes.term_names("no-such-term"), [])
        self.assertIsInstance(notes.term_names(term), list)

    def test_answers_are_read_strictly(self):
        known, rulings = {"R1", "A1", "D1"}, {"R1"}
        good = {"rule": RULE_TEXT, "covers": "Any later line.", "not_when": "", "kind": "rule", "sources": ["R1"]}
        self.assertEqual(read_memories({"new": [good], "revisions": [], "not_general": []}, known, rulings, {})["new"][0]["sources"], ["R1"])
        for bad in ({**good, "sources": [{}]}, {**good, "sources": 3},
                    {**good, "sources": ["D1"]},  # a rule rests on an answer of the instructor
                    {**good, "sources": ["Z9"]},  # evidence of this assignment only
                    {**good, "rule": "x" * 451},
                    {**good, "covers": "Version B only."},
                    {**good, "rule": "Accept $$x=6$$."}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                read_memories({"new": [bad], "revisions": [], "not_general": []}, known, rulings, {})
        with self.assertRaises(ValueError):
            read_memories({"new": [good] * 9, "revisions": [], "not_general": []}, known, rulings, {})
        # A revision of a memory that does not stand is left out, not applied.
        found = read_memories({"new": [], "revisions": [{**good, "id": "M7", "action": "narrow", "why": "Narrower."}], "not_general": []}, known, rulings, {})
        self.assertEqual((found["revisions"], found["ignored"]), ([], ["M7"]))
        self.assertTrue(SPECIFIC.search("R2_4 charges it"))
        self.assertIsNone(SPECIFIC.search("A line that requires a label"))
        # What looks like a line ID and is none: rows named after the word or joined by an operation; "the version I wrote".
        from grading.rubric_memory import checked_text
        for said in ("Accept R2 - 2R1 without the arrow.", "Swap rows R1 and R2.", "R1 <-> R3 is one operation.", "R2 -> 2R1 + R2 is one step.",
                     "The version I wrote stands."):
            self.assertEqual(checked_text(said, "rule", "x"), said)
        for said in ("Charge R5 only once.", "Swap R1 and R2.", "See R2 where rows R1 and R3 were used.", "In version B the sign differs.",
                     "Lines R2-R4 are alternatives."):
            with self.subTest(said=said), self.assertRaises(ValueError):
                checked_text(said, "rule", "x")
        with self.assertRaises(ValueError):
            read_memories({"new": [], "revisions": [{"id": "M1", "action": "confirm", "sources": ["R1"]}] * 2,
                           "not_general": []}, known, rulings, {"M1": {"id": "memory"}})

    def test_grader_disagreements_alone_teach_and_new_disagreements_are_not_skipped(self):
        evidence = {"assignment": "Quiz", "problems": [], "rulings": [], "reviewer_questions": [], "grader_doubts": [],
                    "grader_disagreements": [{"id": "G1", "problem": "P1", "line": "A label earns credit", "graded_by_two": 100, "ticked_differently": 10}]}
        watch = {"kind": "watch", "rule": WATCH_TEXT, "covers": "An answer with a label.", "not_when": "", "sources": ["G1"]}
        with patch.object(self.service, "evidence", return_value=("Quiz", evidence)):
            _, first = self.learned(lambda payload: memory_answer(payload, new=[watch]))
            self.assertEqual(first["status"], "complete", first["error"])
            self.assertEqual(len(self.asked("rubric_memory")), 1)
            self.assertEqual(len(self.service.view(self.definition)["standing"]), 1)
            evidence["grader_disagreements"][0]["line"] = "A different method earns credit"
            _, second = self.learned(lambda payload: memory_answer(payload, new=[]))
            self.assertEqual(second["status"], "complete", second["error"])
            self.assertEqual(len(self.asked("rubric_memory")), 2)

    def test_a_kept_memory_is_revised_by_a_later_pass_and_the_revision_is_kept(self):
        rule = self.memory(1)
        self.decide(rule, "keep", "2026-10-14T00:30:00+00:00")
        self.answered_check()

        def answer(p):
            self.assertEqual(p["memories"][0]["id"], "M1")
            return memory_answer(p, new=[], revisions=[{"id": "M1", "action": "narrow", "why": "The answer limits it to values already charged.",
                                                        "rule": "A value wrong only because of a mistake already charged is charged once.",
                                                        "covers": "Later lines a charged mistake carries into.", "not_when": "", "kind": "rule", "sources": [p["rulings"][0]["id"]]}])
        self.learned(answer)
        view = self.service.view(self.definition)
        narrowed = "A value wrong only because of a mistake already charged is charged once."
        self.assertEqual([(m["label"], m["action"], m["replaces"]["rule"]) for m in view["standing"]], [("M1", "narrow", RULE_TEXT)])
        self.assertEqual((view["waiting"], [m["rule"] for m in view["standing"]]), ([], [narrowed]))
        # Dropping the revision brings back what it revised.
        self.service.decide(self.definition, {"decisions": [{"memory_id": view["standing"][0]["id"], "decision": "drop"}]})
        self.assertEqual([m["rule"] for m in self.service.view(self.definition)["standing"]], [RULE_TEXT])

    def test_responses_of_a_deleted_scan_count_nowhere(self):
        self.grade(["3x - 8 = 10"], primary=["R2"], script=lambda name, body: {**grade_answer(payload_of(body), ["R2"]), "rubric_ambiguities": ["R2: unclear label."]}
                   if name in ("grade", "primary_vision") else None)
        with connect(self.path) as db:
            _, evidence = self.service.evidence(db, self.plan["id"])
            self.assertEqual(len(evidence["grader_doubts"]), 1)
            message = db.execute("SELECT json_extract(source_json,'$.regions[0].message_id') FROM frq_tasks ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            self.assertTrue(message)
            db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,?,?,?)", (message, "workspace", "Wrong scan", "2026-10-14T00:00:00+00:00"))
            _, evidence = self.service.evidence(db, self.plan["id"])
            self.assertEqual(evidence["grader_doubts"], [])

    def test_certifying_queues_one_pass_and_never_fails_without_a_model(self):
        rid = self.service.after_certified(self.plan["id"])
        self.assertTrue(rid)
        self.assertIsNone(self.service.after_certified(self.plan["id"]))  # one waiting pass per assignment
        current = self.frq.settings()
        self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "rubric_reviewer": "",
                                                                            "tiers": {"cloud_standard": {"reader": "", "primary": "", "second": "", "verifier": ""}}}})
        with connect(self.path) as db:
            db.execute("UPDATE frq_rubric_memory_runs SET status='failed', error='x' WHERE id=?", (rid,))
        self.assertIsNone(self.service.after_certified(self.plan["id"]))  # no rubric model: nothing queued, nothing raised

    def test_learning_by_itself_follows_the_instructors_switch_and_their_own_request_does_not(self):
        # ASSISTANT-MEMORY "ONE PAGE" (requirement, 2026-11-10).
        memory = self.app.extensions["assistant"].memory
        self.assertTrue(self.app.extensions["frq_grading"].rubric_memory.learns())
        memory.configure({"version": memory.settings()["version"], "rubric_learning": False})
        service = self.app.extensions["frq_grading"].rubric_memory
        self.assertFalse(service.learns())
        self.assertIsNone(service.after_certified(self.plan["id"]))
        self.assertIsNone(service.after_finalization(self.plan["id"]))
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rubric_memory_runs").fetchone()[0], 0)
        self.assertTrue(service.queue(self.plan["id"], "instructor"))  # Learn from certified assignments is their own request

    def test_the_routes(self):
        client = self.app.test_client()
        rule = self.memory(1)
        view = client.get(f"/api/rubric-memories/{self.definition}")
        self.assertEqual((view.status_code, [m["label"] for m in view.json["waiting"]]), (200, ["M1"]))
        kept = client.post(f"/api/rubric-memories/{self.definition}/decisions", json={"decisions": [{"memory_id": rule, "decision": "keep"}]}, headers=HEADERS)
        self.assertEqual((kept.status_code, [m["label"] for m in kept.json["standing"]]), (200, ["M1"]))
        self.assertEqual(client.post(f"/api/rubric-memories/{self.definition}/decisions", json={"decisions": [{"memory_id": "other", "decision": "keep"}]},
                                     headers=HEADERS).status_code, 400)
        self.assertEqual(client.get("/api/rubric-memories/no-such-course").status_code, 404)
        learned = client.post(f"/api/rubric-memories/{self.definition}/learn", json={}, headers=HEADERS)
        self.assertEqual((learned.status_code, learned.json["queued"]), (200, 0))  # nothing certified yet

    def test_the_instructors_word_on_this_assignment_outranks_a_standing_rule(self):
        # GRADING "RUBRIC MEMORIES" THIS ASSIGNMENT COMES FIRST (requirement, 2026-10-15). Every prompt that reads their word on the assignment says that word wins there.
        from grading.providers.local_vlm import prompt
        for name in ("rubric_quality", "rubric_quality_versions", "rubric_quality_added", "rubric_quality_resolve"):
            self.assertIn("THIS ASSIGNMENT COMES FIRST", prompt(name)[0], name)
            self.assertIn("follow it, never the rule", prompt(name)[0], name)
        self.assertIn("THIS QUIZ COMES FIRST", prompt("quiz_rubric")[0])
        self.assertIn("The rule still stands for other assignments.", prompt("quiz_rubric")[0])
