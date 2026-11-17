"""A course's question bank (AGENTS.md "Question bank"; question_bank/), end to end on a scratch workspace.

A loopback host plays the Rubric drafting and review model (fake_openai.FakeHost); the lecture notes are split in the real document
sandbox, the PDF to read is rendered in it, and every cloud call goes through the grading dispatcher with its receipts. Nothing reaches a
real service."""
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from reportlab.pdfgen import canvas

from app import create_app
from assistant import policy
from fake_openai import FakeHost, payload_of, prompt_name
from question_bank import content as contents
from rosters import connect
from test_course_notes import OUTLINE, PAGES, notes_pdf
from test_grading_cloud import endpoint
from test_storage import HEADERS
import test_frq_grading as grading_fixture
import test_quiz_drafts as quiz_fixture


def configure_model(frq, host):
    current = frq.settings()
    tiers = {tier: {"reader": "alpha-vision", "primary": "alpha-vision", "second": "", "verifier": ""} for tier in ("cloud_standard", "cloud_high_assurance")}
    frq.save_settings({"version": current["version"], "settings": {**current["settings"], "endpoints": [endpoint(host, "alpha-vision")], "tiers": tiers,
                                                                     "yardstick": "", "rubric_reviewer": "alpha-vision", "max_retries": 1}})


def tag_answer(payload, *, objective=True):
    """Each question under the outline's first topic (or section), with a shared objective: written once, then reused by its ref."""
    entries = payload["outline"]
    topic = next((e for e in entries if e["level"] == "topic"), None) or next((e for e in entries if e["level"] == "section"), None)
    existing = next((e for e in entries if e["level"] == "objective"), None)
    answers = []
    for index, question in enumerate(payload["questions"]):
        objectives = []
        if objective and topic:
            if existing:
                objectives = [{"ref": existing["ref"], "parent": "", "text": ""}]
            elif index == 0:
                objectives = [{"ref": "", "parent": topic["ref"], "text": "Add and subtract matrices of the same size"}]
            else:
                objectives = [{"ref": "", "parent": topic["ref"], "text": "add and subtract matrices of the same size."}]
        answers.append({"ref": question["ref"], "nodes": [topic["ref"]] if topic else [], "objectives": objectives, "difficulty": "medium",
                        "types": ["word_problem", "computation"] if "costs" in question["text"] else ["computation"], "reason": "It adds matrices."})
    return {"questions": answers, "notes": ""}


def versions_answer(payload):
    """Versions of one problem start with the same word; at most one per version, as the contract asks."""
    groups = {}
    for question in payload["questions"]:
        group = groups.setdefault(question["text"].split()[0], {})
        group.setdefault(question["version"], question["ref"])
    return {"groups": [{"refs": list(g.values()), "reason": "Same problem."} for g in groups.values() if len(g) > 1], "notes": ""}


def questions_pdf():
    """Two pages of an old quiz, as a PDF the sandbox can read."""
    target = io.BytesIO()
    page = canvas.Canvas(target, pagesize=(612, 792), invariant=1)
    for lines in (["MATH 140 Quiz 1", "1. (3 pts) Find A + B for the matrices given.", "2. (2 pts) Which matrix is square?", "(a) 2x3  (b) 3x3"],
                  ["3. (4 pts) A company's costs are C(x) = 28x + 14000. Find the break-even point."]):
        y = 740
        for line in lines:
            page.drawString(72, y, line)
            y -= 18
        page.showPage()
    page.save()
    return target.getvalue()


def extract_answer(payload):
    found = []
    for item in payload["pages"]:
        if item["page"] == 1:
            found += [{"number": "1", "page": 1, "kind": "free_response", "statement": "Find $A + B$ for the matrices given.", "parts": [], "choices": [],
                       "correct_choice": "", "points": 3, "final_answers": [], "solution": "", "figure": ""},
                      {"number": "2", "page": 1, "kind": "multiple_choice", "statement": "Which matrix is square?", "parts": [], "choices": ["$2 \\times 3$", "$3 \\times 3$"],
                       "correct_choice": "", "points": 2, "final_answers": [], "solution": "", "figure": ""},
                      {"number": "9", "page": 1, "kind": "multiple_choice", "statement": "", "parts": [], "choices": [], "correct_choice": "", "points": None,
                       "final_answers": [], "solution": "", "figure": ""}]
        if item["page"] == 2:
            found.append({"number": "3", "page": 2, "kind": "free_response", "statement": "A company's costs are $C(x) = 28x + 14000$. Find the break-even point.",
                          "parts": [], "choices": [], "correct_choice": "", "points": 4, "final_answers": [], "solution": "", "figure": "A table of prices."})
    return {"questions": found, "notes": ["Page 2 continues past the pages read."]}


class Bank(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.client = self.app.test_client()
        course = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Math", "term": "Fall", "year": 2026, "sections": [501],
                                                        "is_current": True}, headers=HEADERS)
        self.assertEqual(course.status_code, 201, course.json)
        self.definition = course.json["definition_id"]
        self.bank, self.frq, self.notes = self.app.extensions["question_bank"], self.app.extensions["frq_grading"], self.app.extensions["course_notes"]
        self.path = self.app.config["DATABASE"]
        self.host = FakeHost().start()
        self.addCleanup(self.host.stop)
        self.fail_tags = False
        self.tag_first = None   # a bad first answer to bank_tag (a function of the payload); every later ask gets the ordinary one
        self.extract = None
        self.host.script = self.answer
        configure_model(self.frq, self.host)

    def answer(self, body, host):
        name, payload = prompt_name(body), payload_of(body)
        if name == "bank_tag":
            if self.fail_tags:
                return {"questions": [], "notes": ""}  # refused twice: the run fails
            if self.tag_first:
                bad, self.tag_first = self.tag_first, None
                return bad(payload)
            return tag_answer(payload)
        if name == "bank_extract":
            return (self.extract or extract_answer)(payload)
        if name == "bank_versions":
            return versions_answer(payload)
        return ("status", 500, {}, json.dumps({"error": "not scripted"}))

    def post(self, route, body, status=200):
        response = self.client.post(route, json=body, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.json)
        return response.json

    def view(self):
        return self.client.get(f"/api/question-bank/definitions/{self.definition}").json

    def outline(self):
        return {(node["level"], node["number"] or node["title"]): node for node in self.view()["outline"]}

    def write(self, statement, **extra):
        return self.post(f"/api/question-bank/definitions/{self.definition}/questions",
                         {"request_id": str(uuid.uuid4()), "content": {"kind": "free_response", "statement": statement, "points": 3}, **extra})

    def asked(self, name):
        return [payload_of(r["body"]) for r in self.host.requests if prompt_name(r["body"]) == name]

    def test_the_outline_follows_the_lecture_notes_and_keeps_the_instructors_changes(self):
        self.assertEqual(self.bank.sync(self.definition), 0)  # no notes yet: nothing to read
        self.notes.upload(self.definition, notes_pdf(PAGES, OUTLINE), "notes.pdf", "notes-1")
        self.assertGreater(self.bank.sync(self.definition), 0)
        outline = self.view()["outline"]
        self.assertEqual([n["number"] for n in outline if n["level"] == "section"], ["1.1", "1.2", "2.1", "2.2"])
        self.assertEqual([(n["number"], n["title"]) for n in outline if n["level"] == "chapter"], [("1", "Matrices"), ("2", "Functions")])
        by_id = {n["id"]: n for n in outline}
        topics = [(by_id[n["parent_id"]]["number"], n["title"]) for n in outline if n["level"] == "topic"]
        self.assertIn(("1.1", "Adding and Subtracting Matrices"), topics)
        self.assertTrue(all(by_id[n["parent_id"]]["level"] == "chapter" for n in outline if n["level"] == "section"))
        self.assertEqual({n["origin"] for n in outline}, {"notes"})
        self.assertEqual(self.view()["outline_source"]["title"], self.notes.view(self.definition)["current"]["title"])
        self.assertEqual(self.bank.sync(self.definition), 0)  # the same edition is read once

        # The instructor renames 1.1, retires 2.2 and adds an objective; a new edition renames 1.2 and 1.1 in the notes.
        section = self.outline()[("section", "1.1")]
        self.post(f"/api/question-bank/nodes/{section['id']}", {"request_id": "rename", "based_on": section["revision"], "title": "Matrix Basics"})
        self.post(f"/api/question-bank/nodes/{section['id']}", {"request_id": "rename", "based_on": section["revision"], "title": "Matrix Basics"})  # repeated
        stale = self.client.post(f"/api/question-bank/nodes/{section['id']}", json={"request_id": "stale", "based_on": section["revision"], "title": "X"}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        retired = self.outline()[("section", "2.2")]
        self.post(f"/api/question-bank/nodes/{retired['id']}", {"request_id": "retire", "based_on": retired["revision"], "retired": True})
        added = self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "objective", "level": "objective",
                                                                                       "parent_id": section["id"], "title": "Add two matrices"})
        self.assertEqual(self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "objective", "level": "objective",
                                                                                                "parent_id": section["id"], "title": "Add two matrices"})["node_id"], added["node_id"])
        for wrong in ({"level": "topic", "title": "Loose topic"}, {"level": "chapter", "title": "Nested", "parent_id": section["id"]},
                      {"level": "section", "title": "Under a section", "parent_id": section["id"]}):
            response = self.client.post(f"/api/question-bank/definitions/{self.definition}/nodes", json={"request_id": str(uuid.uuid4()), **wrong}, headers=HEADERS)
            self.assertEqual(response.status_code, 400, wrong)
        pages = [list(lines) for lines in PAGES]
        pages[2][1], pages[4][0], pages[4][1] = "1.1 Basic Operations on Matrices", "1.2 Multiplying Matrices", "Objectives: In this section, you will multiply."
        outline = [(level, {"1.1 Basic Matrix Operations": "1.1 Basic Operations on Matrices",
                            "1.2 Matrix Multiplication and Its Applications to Real Scenarios": "1.2 Multiplying Matrices"}.get(title, title), page)
                   for level, title, page in OUTLINE]
        self.notes.upload(self.definition, notes_pdf(pages, outline, title="Edition two"), "notes-2.pdf", "notes-2")
        self.assertGreater(self.bank.sync(self.definition), 0)
        now = self.outline()
        self.assertEqual(now[("section", "1.1")]["title"], "Matrix Basics")       # the instructor's name stands
        self.assertEqual(now[("section", "1.2")]["title"], "Multiplying Matrices")  # the notes' own entry follows the notes
        self.assertTrue(now[("section", "2.2")]["retired"])
        self.assertEqual(now[("objective", "Add two matrices")]["origin"], "instructor")
        with connect(self.path) as db, self.assertRaises(sqlite3.DatabaseError):
            db.execute("UPDATE question_bank_node_revisions SET title='x'")

    def test_questions_are_tagged_automatically_once_and_a_failure_waits_for_the_instructors_retry(self):
        chapter = self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "c", "level": "chapter", "number": "1", "title": "Matrices"})["node_id"]
        section = self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "s", "level": "section", "number": "1.1",
                                                                                         "parent_id": chapter, "title": "Basic Matrix Operations"})["node_id"]
        topic = self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "t", "level": "topic", "parent_id": section,
                                                                                       "title": "Adding and Subtracting Matrices"})["node_id"]
        first = self.write("Find $A + B$ when $A = I_2$ and $B = 2I_2$.")
        second = self.write("A store's costs are given by two matrices; find the total.")
        tagged_by_hand = self.write("Find $A - B$.", classification={"nodes": [section], "difficulty": "easy", "types": ["computation"]})
        self.assertEqual(tagged_by_hand["classification"]["origin"], "instructor")
        self.assertEqual(self.view()["counts"]["tagged"], 1)
        self.bank.sync(self.definition)
        self.assertEqual(self.view()["counts"]["tagging"], 2)
        self.assertEqual(self.bank.sync(self.definition), 0)  # queued once
        self.frq.process_cloud()
        [request] = self.asked("bank_tag")
        self.assertEqual([q["text"] for q in request["questions"]], [first["content"]["statement"], second["content"]["statement"]])
        self.assertEqual([e["title"] for e in request["outline"]], ["Matrices", "Basic Matrix Operations", "Adding and Subtracting Matrices"])
        self.assertEqual(request["outline"][2]["parent"], request["outline"][1]["ref"])
        view = self.view()
        questions = {q["id"]: q for q in view["questions"]}
        objective = next(n for n in view["outline"] if n["level"] == "objective")
        self.assertEqual((objective["parent_id"], objective["origin"], objective["title"]), (topic, "model", "Add and subtract matrices of the same size"))
        self.assertEqual(sum(1 for n in view["outline"] if n["level"] == "objective"), 1)  # the same wording is one objective
        for question in (first, second):
            classification = questions[question["id"]]["classification"]
            self.assertEqual((classification["origin"], classification["nodes"], classification["difficulty"]), ("model", [topic, objective["id"]], "medium"))
        self.assertEqual(questions[second["id"]]["classification"]["types"], ["computation", "word_problem"])  # in the vocabulary's order
        self.assertEqual(questions[tagged_by_hand["id"]]["classification"]["origin"], "instructor")  # never sent to the model
        counts = {n["id"]: n["questions"] for n in view["outline"]}
        self.assertEqual((counts[chapter], counts[section], counts[topic]), (3, 3, 2))  # a question counts under every ancestor of its entries

        # A failed run waits: the pass does not send it again; the instructor's retry does.
        self.fail_tags = True
        third = self.write("Find $3A$.")
        self.bank.sync(self.definition)
        self.frq.process_cloud()
        failed = next(q for q in self.view()["questions"] if q["id"] == third["id"])
        self.assertEqual(failed["tagging"], "failed")
        self.assertIn("could not tag", failed["tagging_error"])
        self.assertEqual(self.bank.sync(self.definition), 0)
        self.fail_tags = False
        run = next(r for r in self.view()["runs"] if r["status"] == "failed")
        self.post(f"/api/question-bank/runs/{run['id']}/retry", {"request_id": "retry"})
        self.frq.process_cloud()
        self.assertEqual(next(q for q in self.view()["questions"] if q["id"] == third["id"])["classification"]["origin"], "model")
        retried = self.client.post(f"/api/question-bank/runs/{run['id']}/retry", json={"request_id": "retry-again"}, headers=HEADERS)
        self.assertEqual(retried.status_code, 409)

        # A revision that changes only what the tagger never reads (the correct choice, notes) keeps the model's tags.
        kept = self.post(f"/api/question-bank/questions/{second['id']}/revise", {"request_id": "notes-only", "based_on": 1,
                                                                                   "content": {**second["content"], "notes": "From a store's books."}})
        self.assertEqual(kept["revision"], 2)
        self.assertEqual(self.bank.sync(self.definition), 0)
        self.assertEqual(self.client.get(f"/api/question-bank/questions/{second['id']}").json["classification"]["content_revision"], 1)

        # Editing what the tagger reads tags it again; the instructor's tags given while a run waits are kept.
        edited = self.post(f"/api/question-bank/questions/{first['id']}/revise", {"request_id": "edit", "based_on": 1,
                                                                                    "content": {**first["content"], "statement": "Find $A + B$ for $A = I_3$."}})
        self.assertEqual((edited["revision"], edited["edited"]), (2, True))
        self.bank.sync(self.definition)
        mine = self.post(f"/api/question-bank/questions/{first['id']}/classify", {"request_id": "mine", "based_on": edited["classification"]["revision"],
                                                                                   "nodes": [section], "difficulty": "hard", "types": ["concept"]})
        self.frq.process_cloud()
        final = self.client.get(f"/api/question-bank/questions/{first['id']}").json
        self.assertEqual((final["classification"]["origin"], final["classification"]["difficulty"]), ("instructor", "hard"))
        self.assertEqual(final["classification"]["revision"], mine["classification"]["revision"])
        self.assertEqual([(r["revision"], r["origin"]) for r in final["revisions"]], [(2, "instructor"), (1, "instructor")])
        self.assertIn("source_ref", final["revisions"][0])

        # Asked explicitly, the model tags a question the instructor tagged.
        self.post(f"/api/question-bank/definitions/{self.definition}/tag", {"request_id": "again", "question_ids": [tagged_by_hand["id"]]})
        self.frq.process_cloud()
        self.assertEqual(self.client.get(f"/api/question-bank/questions/{tagged_by_hand['id']}").json["classification"]["origin"], "model")

        # Retired questions leave the counts and are not tagged; Restore brings them back.
        self.post(f"/api/question-bank/questions/{second['id']}/retire", {"request_id": "retire"})
        self.assertEqual(self.client.post(f"/api/question-bank/questions/{second['id']}/retire", json={"request_id": "retire-2"}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.view()["counts"]["retired"], 1)
        self.post(f"/api/question-bank/questions/{second['id']}/restore", {"request_id": "restore"})
        self.assertEqual(self.view()["counts"]["retired"], 0)
        with connect(self.path) as db:
            calls = db.execute("SELECT COUNT(*) FROM frq_cloud_calls WHERE bank_run_id IS NOT NULL").fetchone()[0]
            self.assertGreaterEqual(calls, 5)  # every request is an ordinary cloud call owned by its run
            for statement in ("UPDATE question_bank_classifications SET difficulty='easy'", "DELETE FROM question_bank_questions",
                              "UPDATE question_bank_runs SET error='x' WHERE status='complete'"):
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute(statement)

    def test_a_question_gets_at_most_one_learning_objective(self):
        """QUESTION-BANK TAGGING (requirement, 2026-11-06): a second objective, new or reused, in `nodes` or `objectives`, is refused, and the model is asked once more with the reason."""
        chapter = self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "c", "level": "chapter", "number": "1", "title": "Matrices"})["node_id"]
        section = self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "s", "level": "section", "number": "1.1",
                                                                                         "parent_id": chapter, "title": "Basic Matrix Operations"})["node_id"]
        topic = self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "t", "level": "topic", "parent_id": section,
                                                                                       "title": "Adding and Subtracting Matrices"})["node_id"]
        one = self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "o1", "level": "objective", "parent_id": topic,
                                                                                     "title": "Add matrices"})["node_id"]
        self.post(f"/api/question-bank/definitions/{self.definition}/nodes", {"request_id": "o2", "level": "objective", "parent_id": topic, "title": "Subtract matrices"})
        def refs(payload):
            return {e["title"]: e["ref"] for e in payload["outline"]}
        def two_in_objectives(payload):
            r = refs(payload)
            return {"questions": [{**a, "objectives": [{"ref": r["Add matrices"], "parent": "", "text": ""}, {"ref": r["Subtract matrices"], "parent": "", "text": ""}]}
                                  for a in tag_answer(payload)["questions"]], "notes": ""}
        def new_beside_reused(payload):
            r = refs(payload)
            return {"questions": [{**a, "objectives": [{"ref": r["Add matrices"], "parent": "", "text": ""},
                                                       {"ref": "", "parent": r["Adding and Subtracting Matrices"], "text": "Find a matrix sum"}]}
                                  for a in tag_answer(payload)["questions"]], "notes": ""}
        def one_in_nodes_one_in_objectives(payload):
            r = refs(payload)
            return {"questions": [{**a, "nodes": [r["Adding and Subtracting Matrices"], r["Add matrices"]],
                                   "objectives": [{"ref": r["Subtract matrices"], "parent": "", "text": ""}]} for a in tag_answer(payload)["questions"]], "notes": ""}
        def two_in_nodes(payload):
            r = refs(payload)
            return {"questions": [{**a, "nodes": [r["Add matrices"], r["Subtract matrices"]], "objectives": []} for a in tag_answer(payload)["questions"]], "notes": ""}
        for number, bad in enumerate((two_in_objectives, new_beside_reused, one_in_nodes_one_in_objectives, two_in_nodes)):
            question = self.write(f"Find $A + {number}B$.")
            self.tag_first, before = bad, len(self.asked("bank_tag"))
            self.bank.sync(self.definition)
            self.frq.process_cloud()
            asked = self.asked("bank_tag")[before:]
            self.assertEqual(len(asked), 2, bad.__name__)  # refused once, asked again with the reason
            self.assertIn("at most one learning objective", asked[1]["refused"], bad.__name__)
            classification = self.client.get(f"/api/question-bank/questions/{question['id']}").json["classification"]
            self.assertEqual(classification["origin"], "model", bad.__name__)
            objective_ids = {n["id"] for n in self.view()["outline"] if n["level"] == "objective"}
            self.assertEqual(len(objective_ids & set(classification["nodes"])), 1, bad.__name__)
        self.assertIn(one, objective_ids)

    def test_versions_of_one_problem_are_linked_by_the_model_and_the_instructor_decides(self):
        exam = self.app.extensions["documents"].create_assignment(self.definition, "exam-1", "Exam 1")
        def printed(statement, version, number):
            question = self.write(statement)
            self.post(f"/api/question-bank/questions/{question['id']}/printed", {"request_id": str(uuid.uuid4()), "based_on": 0,
                                                                                  "assignment_id": exam["id"], "exam_version": version, "number": number})
            return question["id"]
        a1, a2 = printed("Write the line through $(4,-8)$ and $(4,8)$.", "A", "1"), printed("Compute $3A - B^T$.", "A", "2")
        b9, b10 = printed("Write the line through $(-3,6)$ and $(3,6)$.", "B", "9"), printed("Compute $2A^T + B$.", "B", "10")
        self.bank.sync(self.definition)
        self.frq.process_cloud()
        [request] = self.asked("bank_versions")
        self.assertEqual(request["assessment"], "Exam 1")
        self.assertEqual(sorted((q["version"], q["number"]) for q in request["questions"]), [("A", "1"), ("A", "2"), ("B", "10"), ("B", "9")])
        view = {q["id"]: q for q in self.view()["questions"]}
        self.assertEqual(view[a1]["source_label"], "Exam 1 · Problem 1 · Version A")
        self.assertEqual((view[a1]["group"]["origin"], view[a1]["group"]["id"]), ("model", view[b9]["group"]["id"]))
        self.assertEqual(view[a2]["group"]["id"], view[b10]["group"]["id"])
        self.assertNotEqual(view[a1]["group"]["id"], view[a2]["group"]["id"])
        detail = self.client.get(f"/api/question-bank/questions/{a1}").json
        self.assertEqual([(v["id"], v["version"]) for v in detail["other_versions"]], [(b9, "B")])
        self.assertEqual(self.bank.sync(self.definition), 0)  # every question was looked at: nothing is asked again

        # The instructor's unlink stands; their link joins a group; a stale view conflicts.
        unlinked = self.post(f"/api/question-bank/questions/{b9}/versions", {"request_id": "unlink", "based_on": view[b9]["group_revision"], "unlink": True})
        self.assertIsNone(unlinked["group"])
        stale = self.client.post(f"/api/question-bank/questions/{b9}/versions", json={"request_id": "late", "based_on": 1, "with": [a1]}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        c10 = printed("Write the vertical line through $(-2,3)$.", "C", "10")
        self.bank.sync(self.definition)
        self.frq.process_cloud()
        view = {q["id"]: q for q in self.view()["questions"]}
        self.assertIsNone(view[b9]["group"])  # the model never re-links what the instructor unlinked
        self.assertEqual(view[c10]["group"]["id"], view[a1]["group"]["id"])  # a new version joins its problem's group
        self.post(f"/api/question-bank/questions/{a1}/versions", {"request_id": "link", "based_on": view[a1]["group_revision"], "with": [b9]})
        view = {q["id"]: q for q in self.view()["questions"]}
        self.assertEqual((view[b9]["group"]["id"], view[b9]["group"]["origin"]), (view[a1]["group"]["id"], "instructor"))
        stale = self.client.post(f"/api/question-bank/questions/{a1}/printed", json={"request_id": "x", "based_on": 0, "assignment_id": exam["id"],
                                                                                      "exam_version": "B", "number": "3"}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)  # its link is at revision 1 now
        foreign = self.client.post(f"/api/question-bank/questions/{a1}/printed", json={"request_id": "y", "based_on": 1, "assignment_id": "nope"}, headers=HEADERS)
        self.assertEqual(foreign.status_code, 400)
        with connect(self.path) as db, self.assertRaises(sqlite3.DatabaseError):
            db.execute("UPDATE question_bank_version_links SET group_id=NULL")

    def test_bad_content_is_refused_and_a_stale_edit_conflicts(self):
        route = f"/api/question-bank/definitions/{self.definition}/questions"
        for content in ({"kind": "free_response", "statement": "  "}, {"kind": "multiple_choice", "statement": "Pick", "choices": ["only one"]},
                        {"kind": "multiple_choice", "statement": "Pick", "choices": ["a", "b"], "correct_choice": "c"}, {"kind": "essay", "statement": "x"},
                        {"statement": "x", "points": -1}, {"statement": "x", "points": 10**400}):
            self.assertEqual(self.client.post(route, json={"request_id": str(uuid.uuid4()), "content": content}, headers=HEADERS).status_code, 400, content)
        question = self.write("Find $x$.", request_id="same")
        self.assertEqual(self.write("Find $x$.", request_id="same")["id"], question["id"])  # the same request is the same question
        stale = self.client.post(f"/api/question-bank/questions/{question['id']}/revise", json={"request_id": "late", "based_on": 7,
                                                                                               "content": {"statement": "New"}}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        other = self.client.post("/api/courses", json={"code": "MATH 142", "name": "Calc", "term": "Fall", "year": 2026, "sections": [501]}, headers=HEADERS).json
        foreign = self.post(f"/api/question-bank/definitions/{other['definition_id']}/nodes", {"request_id": "foreign", "level": "chapter", "title": "Limits"})["node_id"]
        refused = self.client.post(f"/api/question-bank/questions/{question['id']}/classify", json={"request_id": "x", "based_on": 0, "nodes": [foreign]}, headers=HEADERS)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(contents.tidy_statement("18. (4 pts) Assuming $G$ is a matrix.\n\nVersion A -- Page 7 of 12", "18"), "Assuming $G$ is a matrix.")
        # An answer key's labels are kept as written: a variable's case and a function's parentheses mean something.
        kept = contents.from_frq({"problem": "Maximize.", "answer_key": [{"label": "corner points", "accept": ["(0,0), (8,4)"]}, {"label": "C(x)", "accept": ["2x"]},
                                                                        {"label": "P", "accept": ["60"]}]})
        self.assertEqual([a["part"] for a in kept["final_answers"]], ["corner points", "C(x)", "P"])

    def test_request_ids_cannot_silently_acknowledge_another_question(self):
        first, second = self.write('First question'), self.write('Second question')
        route = '/api/question-bank/questions/{}/revise'
        change = {'request_id': 'one-edit', 'based_on': 1, 'content': {'statement': 'Changed first question'}}
        self.post(route.format(first['id']), change)
        self.post(route.format(second['id']), change, status=409)
        self.assertEqual(self.bank.question(second['id'])['content']['statement'], 'Second question')
        with patch.object(self.bank, '_latest', wraps=self.bank._latest) as latest:
            self.bank.question(first['id'])
        self.assertEqual(latest.call_count, 1)

    def test_a_version_link_request_id_cannot_acknowledge_another_questions_link(self):
        a, b, c, d = (self.write(text) for text in ('Question A', 'Question B', 'Question C', 'Question D'))
        route = '/api/question-bank/questions/{}/versions'
        link = {'request_id': 'one-link', 'based_on': 0, 'with': [b['id']]}
        self.post(route.format(a['id']), link)
        self.post(route.format(a['id']), link)  # the same request again is a replay
        self.post(route.format(c['id']), {'request_id': 'one-link', 'based_on': 0, 'with': [d['id']]}, status=409)
        view = {q['id']: q for q in self.view()['questions']}
        self.assertIsNone(view[c['id']]['group'])
        self.assertIsNone(view[d['id']]['group'])

    def test_retiring_a_parent_hides_descendants_from_new_tags(self):
        """QUESTION-BANK: descendants of a retired entry are unavailable to both taggers."""
        route = f'/api/question-bank/definitions/{self.definition}/nodes'
        parent = self.post(route, {'request_id': 'parent', 'level': 'section', 'title': 'Matrices'})['node_id']
        child = self.post(route, {'request_id': 'child', 'level': 'topic', 'parent_id': parent, 'title': 'Addition'})['node_id']
        self.post(f'/api/question-bank/nodes/{parent}', {'request_id': 'retire-parent', 'based_on': 1, 'retired': True})
        question = self.write('Add the matrices')
        self.post(f"/api/question-bank/questions/{question['id']}/classify",
                  {'request_id': 'hidden-tag', 'based_on': 0, 'nodes': [child]}, status=400)
        self.post(f"/api/question-bank/questions/{question['id']}/classify",
                  {'request_id': 'bad-type', 'based_on': 0, 'types': [['nested']]}, status=400)

    def test_a_pdf_is_read_and_the_instructor_adds_the_questions_they_choose(self):
        upload = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                  data={"request_id": "pdf-1", "file": (io.BytesIO(questions_pdf()), "quiz-1.pdf")})
        self.assertEqual(upload.status_code, 200, upload.json)
        upload_id = upload.json["upload_id"]
        again = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                 data={"request_id": "pdf-2", "file": (io.BytesIO(questions_pdf()), "copy.pdf")})
        self.assertEqual((again.json["upload_id"], again.json["repeated"]), (upload_id, True))
        word = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                data={"request_id": "pdf-3", "file": (io.BytesIO(b"PK\x03\x04"), "quiz.docx")})
        self.assertEqual(word.status_code, 400)
        self.assertIn("Save As", word.json["error"])
        with self.assertRaisesRegex(ValueError, "larger than 25 MB"):  # what the sandbox could not open is refused before it is kept
            self.bank.upload(self.definition, b"%PDF-1.4 " + b"0" * (25 * 1024**2), "big.pdf", "pdf-big")
        self.frq.process_cloud()
        [request] = self.asked("bank_extract")
        self.assertEqual(([p["page"] for p in request["pages"]], request["next_page"]), ([1, 2], None))
        self.assertIn("break-even", request["pages"][1]["text"])
        found = self.client.get(f"/api/question-bank/uploads/{upload_id}/found").json
        self.assertEqual([(q["page"], q["content"]["number"], q["content"]["kind"]) for q in found["questions"]],
                         [(1, "1", "free_response"), (1, "2", "multiple_choice"), (2, "3", "free_response")])
        self.assertEqual(found["questions"][2]["content"]["notes"], "Figure: A table of prices.")
        self.assertTrue(any("page 1 was left out" in note for note in found["notes"]))  # the empty statement, not the whole page
        self.assertEqual(self.view()["counts"]["questions"], 0)  # nothing enters the bank by itself
        stale = self.client.post(f"/api/question-bank/uploads/{upload_id}/accept", json={"request_id": "a", "run_id": "other", "indices": [0]}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        exam = self.app.extensions["documents"].create_assignment(self.definition, "quiz-1", "Quiz 1")
        accepted = self.post(f"/api/question-bank/uploads/{upload_id}/accept", {"request_id": "add", "run_id": found["run_id"], "indices": [1, 2],
                                                                                 "printed": {"assignment_id": exam["id"], "exam_version": "A"}})
        self.assertEqual([q["question_id"] is not None for q in accepted["extraction"]["questions"]], [False, True, True])
        self.post(f"/api/question-bank/uploads/{upload_id}/accept", {"request_id": "add-again", "run_id": found["run_id"], "indices": [1]})
        view = self.view()
        self.assertEqual(view["counts"]["questions"], 2)
        self.assertEqual(sorted(q["source_label"] for q in view["questions"]), ["Quiz 1 · Problem 2 · Version A", "Quiz 1 · Problem 3 · Version A"])
        self.assertEqual({q["source_file"] for q in view["questions"]}, {"quiz-1.pdf"})
        self.assertEqual(view["uploads"][0]["added"], 2)
        pdf = self.client.get(view["uploads"][0]["url"])
        self.assertEqual((pdf.status_code, pdf.headers["Cache-Control"], pdf.data), (200, "no-store", questions_pdf()))
        self.frq.process_cloud()
        self.assertEqual(len([q for q in self.view()["questions"] if q["classification"]]), 0)  # added questions wait for the pass ...
        self.bank.sync(self.definition)
        self.frq.process_cloud()
        self.assertEqual(len([q for q in self.view()["questions"] if q["classification"]]), 2)  # ... which tags them

    def test_accept_rechecks_the_reading_after_acquiring_the_lifecycle_lock(self):
        """QUESTION-BANK: a reread completed while Add waited must invalidate its preview."""
        upload = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                 data={"request_id": "reread-race", "file": (io.BytesIO(questions_pdf()), "quiz.pdf")}).json
        self.frq.process_cloud()
        found = self.bank.extraction(upload['upload_id'])
        original_lock = self.bank.rosters.lock

        @contextmanager
        def completed_reread():
            with original_lock():
                with connect(self.path) as db:
                    newer = dict(db.execute('SELECT * FROM question_bank_runs WHERE id=?', (found['run_id'],)).fetchone())
                    newer.update(id=str(uuid.uuid4()), request_id='completed-reread', created_at='2100-01-01T00:00:00Z')
                    db.execute(f"INSERT INTO question_bank_runs ({','.join(newer)}) VALUES ({','.join('?' for _ in newer)})", tuple(newer.values()))
                yield

        with patch.object(self.bank.rosters, 'lock', completed_reread):
            self.post(f"/api/question-bank/uploads/{upload['upload_id']}/accept",
                      {'request_id': 'stale-add', 'run_id': found['run_id'], 'indices': [0]}, status=409)
        self.assertEqual(self.view()['counts']['questions'], 0)

    def test_the_reader_keeps_a_select_all_question_and_tidies_its_latex_for_the_page(self):
        def webassign(payload):
            if payload["first_page"] != 1:
                return {"questions": [], "notes": []}
            return {"questions": [
                {"number": "1", "page": 1, "kind": "multiple_choice", "statement": "Which of the graphs are one-to-one? (Select all that apply.)", "parts": [],
                 "choices": ["A", "B", "C", "D", "E"], "correct_choice": "d, e", "points": 0.71, "final_answers": [], "solution": "", "figure": "Five graphs A-E."},
                {"number": "2", "page": 1, "kind": "free_response", "statement": "Fixed costs are $\\$8,100$ and a scrap value of \\$1,500; one point is $(0,100)$.",
                 "parts": [{"label": "a", "text": "vertex $(x, y) = $", "points": None}], "choices": [], "correct_choice": "", "points": 1,
                 "final_answers": [{"part": "a", "answer": "$(-4, -21)$"}, {"part": "", "answer": "$\\$4,125.00$"}], "solution": "", "figure": ""}], "notes": []}
        self.extract = webassign
        upload = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                  data={"request_id": "pdf-wa", "file": (io.BytesIO(questions_pdf()), "webassign.pdf")}).json["upload_id"]
        self.frq.process_cloud()
        found = self.client.get(f"/api/question-bank/uploads/{upload}/found").json
        select, costs = (q["content"] for q in found["questions"])
        # Kept (a bank question names one correct choice, so the key is its final answer), not left out as an unreadable key.
        self.assertEqual((select["kind"], select["correct_choice"], select["final_answers"]), ("multiple_choice", "", [{"part": "", "answer": "D, E"}]))
        self.assertEqual(select["notes"], "Figure: Five graphs A-E.\nSelect all that apply: the key marks D, E.")
        self.assertEqual(costs["statement"], "Fixed costs are $\\$8{,}100$ and a scrap value of \\$1,500; one point is $(0,100)$.")  # text outside mathematics stays
        self.assertEqual(costs["parts"][0]["text"], "vertex $(x, y) =$")
        self.assertEqual([a["answer"] for a in costs["final_answers"]], ["$(-4, -21)$", "$\\$4{,}125.00$"])
        self.assertEqual(contents.several_keyed("b", 5), [])
        self.assertEqual(contents.several_keyed("none", 5), [])
        self.assertEqual(contents.tidy_latex("$a$ and $b$, $$x = 1,000$$"), "$a$ and $b$, $$x = 1{,}000$$")

    def textbook(self, payload):
        """A textbook's exercises (bank_extract v2): both pages at once are more than the reader can list, so each page is read by itself;
        page 2 continues Section 1.1, then the chapter's review starts its numbering again."""
        pages = [p["page"] for p in payload["pages"]]
        problem = lambda number, page, statement, **extra: {"section": "", "number": number, "page": page, "kind": "free_response",  # noqa: E731
                                                             "statement": statement, "parts": [], "choices": [], "correct_choice": "", "points": None,
                                                             "final_answers": [], "solution": "", "figures": [], "check": [], **extra}
        if pages == [1, 2]:
            return {"questions": [problem("1", 1, "Find $A + B$.", section="1.1")], "notes": [], "complete": False}
        if pages == [1]:
            return {"questions": [
                problem("1", 1, "Find $A + B$.", section="1.1", check=["The heading names Exercises 44 - 45, but the problems are numbered 45 and 46."]),
                problem("2", 1, "Write the inequality shown by the graph.", section="1.1",
                        figures=[{"page": 1, "box": {"left": .08, "top": .04, "right": .7, "bottom": .12}, "description": "A graph of the line $y = 2x$."},
                                 {"page": 1, "box": {"left": .5, "top": .5, "right": .505, "bottom": .9}, "description": "A sliver."}])],
                "notes": ["No answers are printed."], "complete": True}
        return {"questions": [problem("3", 2, "Find the break-even point."), problem("1", 2, "Find $AB$.", section="Chapter 1 Review")],
                "notes": ["No answers are printed."], "complete": True}

    def test_a_textbook_is_read_whole_with_its_sections_figures_and_checks(self):
        node = lambda body: self.post(f"/api/question-bank/definitions/{self.definition}/nodes", body)["node_id"]  # noqa: E731
        chapter = node({"request_id": "c", "level": "chapter", "number": "1", "title": "Matrices"})
        s11 = node({"request_id": "s1", "level": "section", "number": "1.1", "parent_id": chapter, "title": "Matrix Operations"})
        node({"request_id": "t1", "level": "topic", "parent_id": s11, "title": "Adding Matrices"})
        s12 = node({"request_id": "s2", "level": "section", "number": "1.2", "parent_id": chapter, "title": "Inverses"})
        node({"request_id": "t2", "level": "topic", "parent_id": s12, "title": "Finding Inverses"})
        self.extract = self.textbook
        upload = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                  data={"request_id": "pdf-book", "file": (io.BytesIO(questions_pdf()), "exercises.pdf")}).json["upload_id"]
        self.frq.process_cloud()
        # Never cut short: the pages the reader could not list whole are read again, one page at a time.
        self.assertEqual([[p["page"] for p in r["pages"]] for r in self.asked("bank_extract")], [[1, 2], [1], [2]])
        found = self.client.get(f"/api/question-bank/uploads/{upload}/found").json
        self.assertEqual(found["rule"], "bank-extract-v2")
        self.assertEqual([(q["page"], q.get("section"), q["content"]["number"]) for q in found["questions"]],
                         [(1, "1.1", "1"), (1, "1.1", "2"), (2, "1.1", "3"), (2, "Chapter 1 Review", "1")])  # a section carries over a page
        self.assertEqual(found["notes"], ["No answers are printed."])  # one note said twice is listed once
        first, graph = found["questions"][0], found["questions"][1]
        self.assertEqual(first["check"], ["The heading names Exercises 44 - 45, but the problems are numbered 45 and 46."])
        self.assertEqual([f["description"] for f in graph["figures"]], ["A graph of the line $y = 2x$."])  # a box too small to cut is not cut ...
        self.assertIn("could not be cut", graph["check"][0])  # ... and the instructor is told
        self.assertEqual(graph["content"]["notes"], "Figure: A graph of the line $y = 2x$.\nFigure: A sliver.")
        accepted = self.post(f"/api/question-bank/uploads/{upload}/accept", {"request_id": "add-book", "run_id": found["run_id"], "indices": [0, 1, 2, 3]})
        self.assertEqual((accepted["pictures"], accepted["pictures_missed"]), (1, 0))
        listed = {q["source_label"]: q for q in accepted["questions"]}
        self.assertEqual(sorted(listed), ["exercises.pdf · Chapter 1 Review · page 2 · Problem 1", "exercises.pdf · Section 1.1 · page 1 · Problem 1",
                                          "exercises.pdf · Section 1.1 · page 1 · Problem 2", "exercises.pdf · Section 1.1 · page 2 · Problem 3"])
        self.assertIn("Check: The heading names Exercises 44 - 45", listed["exercises.pdf · Section 1.1 · page 1 · Problem 1"]["content"]["notes"])
        [picture] = listed["exercises.pdf · Section 1.1 · page 1 · Problem 2"]["pictures"]
        self.assertEqual((picture["origin"], picture["page"], picture["caption"]), ("page", 1, "A graph of the line $y = 2x$."))
        self.assertEqual(self.client.get(picture["url"]).mimetype, "image/png")
        again = self.post(f"/api/question-bank/uploads/{upload}/accept", {"request_id": "add-book", "run_id": found["run_id"], "indices": [1]})
        self.assertEqual((again["pictures"], len(again["questions"])), (0, 4))  # the same request adds nothing twice
        with connect(self.path) as db:
            families = sorted(row[0] for row in db.execute("SELECT family FROM question_bank_questions WHERE upload_id=?", (upload,)))
        self.assertEqual(families[0], f"pdf:{found['run_id']}:1.1:1")
        # The tagger is told where each was printed (a section, or for a chapter's review the chapter) and what its figures show.
        self.bank.sync(self.definition)
        self.frq.process_cloud()
        requests = self.asked("bank_tag")
        refs = {e["ref"]: e["number"] for e in requests[0]["outline"]}
        self.assertEqual(sorted(refs.get(q.get("printed_in"), "") for q in requests[0]["questions"]), ["1", "1.1", "1.1", "1.1"])
        self.assertEqual([q.get("figures") for q in requests[0]["questions"] if "graph" in q["text"]], [["A graph of the line $y = 2x$.", "A sliver."]])
        self.assertEqual(len([q for q in self.view()["questions"] if q["classification"]]), 4)

    def test_a_placement_outside_the_printed_section_is_asked_again(self):
        node = lambda body: self.post(f"/api/question-bank/definitions/{self.definition}/nodes", body)["node_id"]  # noqa: E731
        s11 = node({"request_id": "s1", "level": "section", "number": "1.1", "title": "Matrix Operations"})
        node({"request_id": "t1", "level": "topic", "parent_id": s11, "title": "Adding Matrices"})
        node({"request_id": "s2", "level": "section", "number": "1.2", "title": "Inverses"})
        self.extract = lambda payload: {"questions": [{"section": "1.2", "number": "4", "page": 1, "kind": "free_response", "statement": "Find $A^{-1}$.",
                                                      "parts": [], "choices": [], "correct_choice": "", "points": None, "final_answers": [], "solution": "",
                                                      "figures": [], "check": []}] if payload["first_page"] == 1 else [], "notes": [], "complete": True}
        upload = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                  data={"request_id": "pdf-inv", "file": (io.BytesIO(questions_pdf()), "inverses.pdf")}).json["upload_id"]
        self.frq.process_cloud()
        found = self.client.get(f"/api/question-bank/uploads/{upload}/found").json
        self.post(f"/api/question-bank/uploads/{upload}/accept", {"request_id": "add-inv", "run_id": found["run_id"], "indices": [0]})
        self.bank.sync(self.definition)
        self.frq.process_cloud()  # the fake places everything under 1.1's topic, twice: refused, then the run fails
        requests = self.asked("bank_tag")
        self.assertEqual(len(requests), 2)
        self.assertIn("printed in 1.2", requests[1]["refused"])
        self.assertEqual(self.view()["counts"]["failed"], 1)

    def test_a_pdf_read_again_knows_the_problems_added_from_its_earlier_reading(self):
        upload = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                  data={"request_id": "pdf-again", "file": (io.BytesIO(questions_pdf()), "quiz.pdf")}).json["upload_id"]
        self.frq.process_cloud()
        first = self.client.get(f"/api/question-bank/uploads/{upload}/found").json
        self.post(f"/api/question-bank/uploads/{upload}/accept", {"request_id": "add-1", "run_id": first["run_id"], "indices": [0]})
        self.post(f"/api/question-bank/uploads/{upload}/read", {"request_id": "read-again"})
        self.post(f"/api/question-bank/uploads/{upload}/read", {"request_id": "read-again"})  # the same request is one reading
        self.post(f"/api/question-bank/uploads/{upload}/read", {"request_id": "read-again-2"}, status=409)  # one reading at a time
        self.frq.process_cloud()
        second = self.client.get(f"/api/question-bank/uploads/{upload}/found").json
        self.assertNotEqual(second["run_id"], first["run_id"])
        self.assertEqual([(q["content"]["number"], bool(q["question_id"]), q.get("earlier", False)) for q in second["questions"]],
                         [("1", True, True), ("2", False, False), ("3", False, False)])
        stale = self.client.post(f"/api/question-bank/uploads/{upload}/accept", json={"request_id": "add-2", "run_id": first["run_id"], "indices": [1]}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        self.post(f"/api/question-bank/uploads/{upload}/accept", {"request_id": "add-3", "run_id": second["run_id"], "indices": [0, 1]})
        self.assertEqual(sorted(q["content"]["number"] for q in self.view()["questions"]), ["1", "2"])  # problem 1 is not added twice

    def upload_variant(self, tag):
        """questions_pdf() with a tag line, so each upload is its own file (the same file in one course is one upload)."""
        target = io.BytesIO()
        page = canvas.Canvas(target, pagesize=(612, 792), invariant=1)
        for lines in (["MATH 140 Quiz 1", "1. (3 pts) Find A + B for the matrices given.", tag], ["3. (4 pts) Find the break-even point."]):
            for y, line in zip(range(740, 0, -18), lines):
                page.drawString(72, y, line)
            page.showPage()
        page.save()
        upload = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                  data={"request_id": f"pdf-{tag}", "file": (io.BytesIO(target.getvalue()), f"{tag}.pdf")})
        self.assertEqual(upload.status_code, 200, upload.json)
        self.frq.process_cloud()
        return upload.json["upload_id"]

    def waiting(self, upload_id):
        return next(u["waiting"] for u in self.view()["uploads"] if u["id"] == upload_id)

    def test_what_another_source_banked_is_never_added_again(self):
        """requirement, 2026-10-03: Add all must not copy what a blank exam or quiz already has in the bank (from grading, another file)."""
        quiz = self.app.extensions["documents"].create_assignment(self.definition, "quiz-1", "Quiz 1")
        # A problem that relies on a figure is never matched by its words alone.
        self.write("A company's costs are $C(x) = 28x + 14000$. Find the break-even point.")
        first = self.upload_variant("first")
        found = self.client.get(f"/api/question-bank/uploads/{first}/found").json
        self.assertEqual([q.get("elsewhere") for q in found["questions"]], [None, None, None])
        self.assertEqual(self.waiting(first), 3)
        self.post(f"/api/question-bank/uploads/{first}/accept", {"request_id": "add-first", "run_id": found["run_id"], "indices": [0, 1, 2],
                                                                  "printed": {"assignment_id": quiz["id"], "exam_version": "A"}})
        self.assertEqual(self.waiting(first), 0)
        # Another file of the same version: its text-only problems are Quiz 1 A's word for word under the same numbers, so it is that version,
        # and its figure problem is Quiz 1 A #3 by its number. Nothing is added again.
        second = self.upload_variant("second")
        found = self.client.get(f"/api/question-bank/uploads/{second}/found").json
        self.assertEqual([q.get("elsewhere") for q in found["questions"]], [f"Quiz 1 · Problem {n} · Version A" for n in (1, 2, 3)])
        self.assertEqual(self.waiting(second), 0)
        self.post(f"/api/question-bank/uploads/{second}/accept", {"request_id": "add-second", "run_id": found["run_id"], "indices": [0, 1, 2]})
        self.assertEqual(len(self.view()["questions"]), 4)
        # A worksheet with one of Quiz 1 A's problems and one written in the bank (word for word: in the bank already, though it says nothing
        # of a version) is not known to be Quiz 1 A, so its figure problem is its own.
        self.write("Which of these matrices is a row matrix?\n(a) $1 \\times 3$\n(b) $3 \\times 1$")
        def borrowed(payload):
            answer = extract_answer(payload)
            for item in answer["questions"]:
                if item["number"] == "2":
                    item.update(kind="free_response", statement="Which of these matrices is a row matrix?\n(a) $1 \\times 3$\n(b) $3 \\times 1$", choices=[])
            return answer
        self.extract = borrowed
        third = self.upload_variant("third")
        found = self.client.get(f"/api/question-bank/uploads/{third}/found").json
        self.assertEqual([q.get("elsewhere") for q in found["questions"]], ["Quiz 1 · Problem 1 · Version A", "Written in the bank", None])
        self.assertEqual(self.waiting(third), 1)

    def picture(self, question_id, data, request_id, caption="", name="graph.png"):
        return self.client.post(f"/api/question-bank/questions/{question_id}/pictures", headers=HEADERS,
                                data={"request_id": request_id, "caption": caption, "file": (io.BytesIO(data), name)})

    def test_a_picture_goes_with_its_question_from_a_file_or_cut_from_a_pdf_page(self):
        from PIL import Image
        question = self.write("Use the graph to find the slope of the line.")
        out = io.BytesIO()
        Image.new("RGB", (40, 30), (200, 30, 30)).save(out, "PNG")
        png = out.getvalue()
        added = self.picture(question["id"], png, "pic-1", "A line through (0, 2) and (4, 0)")
        self.assertEqual(added.status_code, 200, added.json)
        [kept] = added.json["pictures"]
        self.assertEqual((kept["origin"], kept["width"], kept["height"], kept["caption"], kept["source_name"], kept["removed"]),
                         ("file", 40, 30, "A line through (0, 2) and (4, 0)", "graph.png", False))
        self.assertEqual(len(self.picture(question["id"], png, "pic-1").json["pictures"]), 1)  # the same request is one picture
        for data, name in ((b"hello", "notes.txt"), (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "graph.svg"), (png[:40], "cut.png")):
            refused = self.picture(question["id"], data, f"bad-{name}", name=name)
            self.assertEqual(refused.status_code, 400, name)
            self.assertIn("PNG, JPEG, GIF or WebP", refused.json["error"])
        with self.assertRaisesRegex(ValueError, "larger than 10 MB"):
            self.bank.add_picture(question["id"], png + b"0" * (10 * 1024**2), "big.png", "", "pic-big")
        served = self.client.get(kept["url"])
        self.assertEqual((served.status_code, served.mimetype, served.headers["Cache-Control"], served.data), (200, "image/png", "no-store", png))
        listed = next(q for q in self.view()["questions"] if q["id"] == question["id"])
        self.assertEqual([p["id"] for p in listed["pictures"]], [kept["id"]])
        self.assertEqual(listed["revision"], 1)  # a picture never changes the question's content
        # Removing is reversible and kept: the list no longer shows it, the question's history does.
        self.post(f"/api/question-bank/pictures/{kept['id']}/remove", {"request_id": "rm-1"})
        self.post(f"/api/question-bank/pictures/{kept['id']}/remove", {"request_id": "rm-2"}, 409)
        self.assertEqual(next(q for q in self.view()["questions"] if q["id"] == question["id"])["pictures"], [])
        detail = self.client.get(f"/api/question-bank/questions/{question['id']}").json
        self.assertEqual(([p["removed"] for p in detail["pictures"]], [e["action"] for e in detail["picture_events"]]), ([True], ["removed", "added"]))
        self.post(f"/api/question-bank/pictures/{kept['id']}/restore", {"request_id": "rs-1"})
        self.assertEqual(len(next(q for q in self.view()["questions"] if q["id"] == question["id"])["pictures"]), 1)
        with sqlite3.connect(self.path) as db, self.assertRaises(sqlite3.DatabaseError):
            db.execute("UPDATE question_bank_pictures SET caption='changed'")

        # A region of a page of a PDF read for the bank, cut from the page picture kept beside the upload.
        upload = self.client.post(f"/api/question-bank/definitions/{self.definition}/uploads", headers=HEADERS,
                                  data={"request_id": "pdf-1", "file": (io.BytesIO(questions_pdf()), "quiz-1.pdf")}).json["upload_id"]
        self.frq.process_cloud()
        found = self.client.get(f"/api/question-bank/uploads/{upload}/found").json
        self.post(f"/api/question-bank/uploads/{upload}/accept", {"request_id": "add", "run_id": found["run_id"], "indices": [2]})
        read = next(q for q in self.view()["questions"] if q["upload_id"] == upload)
        self.assertEqual(next(u for u in self.view()["uploads"] if u["id"] == upload)["pages"], 2)
        page = self.client.get(f"/api/question-bank/uploads/{upload}/pages/2.jpg")
        self.assertEqual((page.status_code, page.mimetype, page.headers["Cache-Control"]), (200, "image/jpeg", "no-store"))
        self.assertEqual(self.client.get(f"/api/question-bank/uploads/{upload}/pages/3.jpg").status_code, 404)
        with Image.open(io.BytesIO(page.data)) as whole:
            width, height = whole.size
        region = {"left": 0.1, "top": 0.05, "right": 0.6, "bottom": 0.25}
        cut = self.post(f"/api/question-bank/questions/{read['id']}/pictures/cut", {"request_id": "cut-1", "page": 2, "region": region, "caption": "A table of prices."})
        [piece] = cut["pictures"]
        self.assertEqual((piece["origin"], piece["page"], piece["source_name"]), ("page", 2, "quiz-1.pdf"))
        self.assertEqual((piece["width"], piece["height"]), (round(.6 * width) - round(.1 * width), round(.25 * height) - round(.05 * height)))
        self.assertEqual(self.client.get(piece["url"]).mimetype, "image/png")
        for bad, why in (({**region, "right": 0.11}, "larger box"), ({**region, "left": -1}, "shares of the page")):
            refused = self.client.post(f"/api/question-bank/questions/{read['id']}/pictures/cut", json={"request_id": f"cut-{len(why)}", "page": 2, "region": bad}, headers=HEADERS)
            self.assertEqual(refused.status_code, 400)
            self.assertIn(why, refused.json["error"])
        self.post(f"/api/question-bank/questions/{read['id']}/pictures/cut", {"request_id": "cut-3", "page": 3, "region": region}, 400)
        # Another course's PDF is not offered: a question is cut from a PDF of its own course.
        other = self.client.post("/api/courses", json={"code": "MATH 142", "name": "Calculus", "term": "Fall", "year": 2026, "sections": [501],
                                                      "is_current": True}, headers=HEADERS).json["definition_id"]
        theirs = self.post(f"/api/question-bank/definitions/{other}/questions", {"request_id": "q-142", "content": {"statement": "Find the derivative."}})
        self.post(f"/api/question-bank/questions/{theirs['id']}/pictures/cut", {"request_id": "cut-4", "upload_id": upload, "page": 1, "region": region}, 400)
        # Pictures are part of the course's verified storage: a snapshot checks each file.
        with connect(self.path) as db:
            kept_paths = {row["path"] for row in db.execute("SELECT path FROM question_bank_pictures")}
        self.assertEqual(len(kept_paths), 2)
        storage = self.app.extensions["documents"].files
        with storage.files("definition", self.definition) as root:
            for relative in kept_paths:
                self.assertTrue((root / relative).is_file(), relative)

    def test_the_assistant_never_uploads_and_asks_before_every_other_write(self):
        writes = {rule.endpoint for rule in self.app.url_map.iter_rules() if rule.endpoint.startswith("question_bank.") and "POST" in rule.methods}
        uploads = {"question_bank.upload", "question_bank.add_picture"}
        self.assertEqual({policy.base_rule(name).tier for name in uploads}, {"never"})
        self.assertEqual({policy.base_rule(name).tier for name in writes - uploads}, {"approve"})
        self.assertIn("question_bank.upload_file", policy.GET_BLOCKED)  # a download, fetched as a file, never read as data
        # A question's pictures and a PDF's pages are files: never read as data, shown with fetch_files when the instructor asks.
        self.assertTrue({"question_bank.picture", "question_bank.page_picture"} <= policy.GET_BLOCKED - policy.FILE_BLOCKED)


class RetainedFromAssessments(unittest.TestCase):
    """An assessment's question enters the bank once the instructor approved its rubric in Key & Rubric Finalization."""
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric
    managed = False

    def setUp(self):
        grading_fixture.FrqWorkflowTests.setUp(self)
        self.bank = self.app.extensions["question_bank"]
        with connect(self.app.config["DATABASE"]) as db:
            self.definition = db.execute("SELECT definition_id FROM assignments").fetchone()[0]

    def bank_questions(self):
        return self.bank.view(self.definition)["questions"]

    def save(self, problem, key="q1"):
        plan = self.frq.assignment(self.plan["id"])
        return self.frq.save_question(self.plan["id"], {"version": plan["version"], "question_key": key, "assignment_version_id": self.version_id,
            "content": {"title": "Question 1", "max_points": 10, "printed_number": "4", "problem": problem, "answer_key": [{"label": "", "accept": ["x=6"]}],
                        "worked_solution": "3x = 18, so x = 6", "regions": [{"page": 1, "region": {"x": .1, "y": .1, "width": .8, "height": .4}}]}})

    def test_approved_questions_are_retained_by_reference_and_follow_unedited_revisions(self):
        self.bank.sync(self.definition)
        [question] = self.bank_questions()
        self.assertEqual((question["origin"], question["content"]["statement"], question["content"]["points"]), ("assessment", "Solve", 10))
        with connect(self.app.config["DATABASE"]) as db:
            title = db.execute("SELECT title FROM assignments").fetchone()[0]
            frq = db.execute("SELECT frq_question_id FROM question_bank_questions").fetchone()[0]
        self.assertEqual(frq, self.qid)
        self.assertTrue(question["source_label"].startswith(title))
        self.assertEqual(self.bank.sync(self.definition), 0)

        # A later revision of the question waits for its rubric's approval, then is followed while the bank's copy is unedited.
        self.save("4. (10 pts) Solve $3x = 18$.\nPage 1 of 2")
        self.assertEqual(self.bank.sync(self.definition), 0)
        self.configure_rubric()
        self.bank.sync(self.definition)
        [question] = self.bank_questions()
        self.assertEqual((question["revision"], question["content"]["statement"], question["content"]["number"]), (2, "Solve $3x = 18$.", "4"))
        self.assertEqual(question["content"]["final_answers"], [{"part": "", "answer": "x=6"}])
        detail = self.bank.question(question["id"])
        self.assertEqual(detail["content"]["solution"], "3x = 18, so x = 6")

        # The instructor's edit in the bank stands: a later revision of the graded question is not followed, and the graded one is untouched.
        self.bank.revise_question(question["id"], {"request_id": "edit", "based_on": 2, "content": {**detail["content"], "statement": "Solve for $x$: $3x = 18$."}})
        self.save("4. (10 pts) Solve $3x = 21$.")
        self.configure_rubric()
        self.bank.sync(self.definition)
        [question] = self.bank_questions()
        self.assertEqual((question["revision"], question["content"]["statement"]), (3, "Solve for $x$: $3x = 18$."))
        self.assertIn("3x = 21", self.frq.assignment(self.plan["id"])["questions"][0]["content"]["problem"])

    def test_a_graded_question_is_banked_while_its_rubric_is_being_revised(self):
        self.save("4. (10 pts) Solve $3x = 18$.")  # its pinned rubric is no longer approved, but one of its rubrics was
        self.bank.sync(self.definition)
        [question] = self.bank_questions()
        self.assertEqual((question["origin"], question["content"]["statement"]), ("assessment", "Solve $3x = 18$."))

    def test_graded_bubble_sheet_questions_are_banked_once_from_their_blank(self):
        host = FakeHost().start()
        self.addCleanup(host.stop)
        host.script = lambda body, _host: extract_answer(payload_of(body)) if prompt_name(body) == "bank_extract" else tag_answer(payload_of(body))
        configure_model(self.frq, host)
        with connect(self.app.config["DATABASE"]) as db:
            assignment = db.execute("SELECT id FROM assignments").fetchone()[0]
        self.app.extensions["assignment_templates"].upload(assignment, "Version A · uploaded template 1", str(uuid.uuid4()), {"blank.pdf": questions_pdf()})
        key = {"defaults": {"points": "4", "scoring": "exact"}, "rule": "bubble-scoring-v2",
               "versions": [{"letter": "A", "questions": [{"number": 1, "answer": "C"}, {"number": 2, "answer": "B"}, {"number": 9, "answer": "ABCDE", "scoring": "either"}]}]}
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("""INSERT INTO frq_bubble_keys(id,assignment_id,revision,key_json,sha256,request_id,request_sha256,actor,created_at)
                          VALUES ('key-1',?,1,?,'x','r','x','workspace','2026-10-01T00:00:00+00:00')""", (self.plan["id"], json.dumps(key)))
        def sheets(count):
            return patch.object(self.frq, "bubble_analysis", return_value={"versions": [{"letter": "A", "sheets": count, "questions": []}]})
        with sheets(0):
            self.bank.sync(self.definition)
        self.assertFalse([q for q in self.bank_questions() if q["origin"] == "pdf"])  # no sheet graded yet: nothing read
        self.bank._bubble_checked.clear()
        with sheets(12):
            self.bank.sync(self.definition)
        self.frq.process_cloud()
        banked = {q["content"]["number"]: q for q in self.bank_questions() if q["origin"] == "pdf"}
        self.assertEqual(sorted(banked), ["1", "2"])  # keyed and read; 3 is not on the key, 9 could not be read
        self.assertTrue(banked["2"]["source_label"].endswith(" · Problem 2 · Version A"), banked["2"]["source_label"])
        self.assertEqual((banked["2"]["printed"]["number"], banked["2"]["printed"]["version"], banked["2"]["content"]["correct_choice"]), ("2", "A", "b"))
        self.assertEqual(banked["1"]["content"]["correct_choice"], "")  # read as free response: the key's letter has no choice to mark
        view = self.bank.view(self.definition)
        self.assertEqual(view["counts"]["bubbles_unread"], 1)
        self.assertEqual(view["uploads"][0]["added"], 2)
        self.bank._bubble_checked.clear()
        with sheets(12) as analysis:
            self.bank.sync(self.definition)
        analysis.assert_not_called()  # read once: never again by itself
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM question_bank_runs WHERE kind='extract'").fetchone()[0], 1)

    def test_measured_difficulty_comes_from_the_latest_certification(self):
        self.bank.sync(self.definition)
        [question] = self.bank_questions()
        self.assertEqual(self.bank.statistics(self.definition)["questions"], {})  # graded, never certified: nothing measured yet
        with connect(self.app.config["DATABASE"]) as db:
            event = db.execute("SELECT event_id FROM frq_assignments WHERE id=?", (self.plan["id"],)).fetchone()[0]
            people = db.execute("SELECT section_id, enrollment_id FROM exam_participants WHERE event_id=? LIMIT 2", (event,)).fetchall()
            self.assertEqual(len(people), 2)
            db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
                participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,supersedes_id,actor,created_at)
                VALUES ('cert-1',?,'r','x','x','fixture','{}','x','x','x','x','{}','{}','{}','{}','{}',NULL,'workspace','2026-10-01T00:00:00+00:00')""", (self.plan["id"],))
            for (section, enrollment), score in zip(people, ("5", "10")):
                parts = {"frq": {"state": "complete", "earned": score, "possible": "10", "questions": {self.qid: {"score": score, "max": "10"}}}}
                db.execute("""INSERT INTO frq_certification_students(certification_id,section_id,enrollment_id,status,reason,earned,possible,parts_json)
                    VALUES ('cert-1',?,?,'scored','',?,?,?)""", (section, enrollment, score, "10", json.dumps(parts)))
        stats = self.bank.statistics(self.definition)
        self.assertEqual(stats["questions"][question["id"]], {"earned_pct": 75, "students": 2, "terms": ["Fall 2026"], "sources": ["certified grades"]})
        self.assertEqual([p["certified"] for p in stats["plans"]], [True])
        self.assertEqual(self.client.get(f"/api/question-bank/definitions/{self.definition}/statistics").status_code, 200)

    def test_unapproved_questions_and_questions_without_a_statement_wait(self):
        plan = self.frq.assignment(self.plan["id"])
        self.frq.save_question(self.plan["id"], {"version": plan["version"], "question_key": "q2", "assignment_version_id": self.version_id,
            "content": {"title": "Question 2", "max_points": 5, "problem": "Graph it", "regions": [{"page": 1, "region": {"x": .1, "y": .5, "width": .8, "height": .4}}]}})
        self.bank.sync(self.definition)
        self.assertEqual([q["content"]["statement"] for q in self.bank_questions()], ["Solve"])  # q2 has no approved rubric
        self.assertEqual(self.bank.view(self.definition)["counts"]["waiting_for_text"], 0)
        # Approved with no statement (a rubric uploaded without its problem): counted as waiting, not kept empty.
        plan = self.frq.assignment(self.plan["id"])
        self.frq.save_question(self.plan["id"], {"version": plan["version"], "question_key": "q3", "assignment_version_id": self.version_id,
            "content": {"title": "Question 3", "max_points": 5, "problem": "", "regions": [{"page": 1, "region": {"x": .1, "y": .7, "width": .8, "height": .2}}]}})
        q3 = next(q for q in self.frq.assignment(self.plan["id"])["questions"] if q["question_key"] == "q3")
        q3 = next(q for q in self.frq.import_rubric(self.plan["id"], q3["question_id"], {"version": q3["version"], "source_text": "-1 Error",
            "rules": {"items": [{"id": "R1", "description": "Error", "points": -1, "original_source_text": "-1 Error"}]}})["questions"] if q["question_key"] == "q3")
        self.frq.approve_rubric(self.plan["id"], q3["question_id"], {"version": q3["version"], "rubric_id": q3["rubric_id"], "sha256": q3["rubric"]["sha256"]})
        self.bank.sync(self.definition)
        self.assertEqual(len(self.bank_questions()), 1)
        self.assertEqual(self.bank.view(self.definition)["counts"]["waiting_for_text"], 1)


class RetainedFromDraftedQuizzes(unittest.TestCase):
    """A quiz Faculty drafted enters the bank when its approval is carried out, from the LaTeX the instructor approved."""
    answer_quiz = quiz_fixture.QuizDrafts.answer
    draft = quiz_fixture.QuizDrafts.draft
    approved = quiz_fixture.QuizDrafts.approved

    def setUp(self):
        quiz_fixture.QuizDrafts.setUp(self)
        self.host.script = self.answer
        self.bank = self.app.extensions["question_bank"]

    def answer(self, body, host):
        if prompt_name(body) == "bank_tag":
            return tag_answer(payload_of(body), objective=False)
        return self.answer_quiz(body, host)

    def test_an_approved_quiz_enters_the_bank_with_its_versions_and_is_tagged(self):
        result = self.approved()
        self.bank.sync(self.term["definition_id"])
        questions = sorted(self.bank.view(self.term["definition_id"])["questions"], key=lambda q: (q["content"]["number"], q["version"]))
        self.assertEqual([(q["origin"], q["content"]["number"], q["version"], q["content"]["kind"]) for q in questions],
                         [("quiz_draft", "1", "A", "multiple_choice"), ("quiz_draft", "1", "B", "multiple_choice"),
                          ("quiz_draft", "2", "A", "free_response"), ("quiz_draft", "2", "B", "free_response")])
        self.assertEqual(questions[0]["content"]["choices"], ["$x = 1$", "$x = 2$", "$x = 3$", "$x = 4$"])
        self.assertEqual(questions[0]["content"]["correct_choice"], "b")
        self.assertEqual([p["label"] for p in questions[2]["content"]["parts"]], ["a", "b"])
        self.assertEqual([p["points"] for p in questions[2]["content"]["parts"]], [1, 2])
        self.assertIn("\\quizaxes", self.bank.question(questions[2]["id"])["content"]["figure"])
        self.assertEqual(questions[0]["family"], questions[1]["family"])
        with connect(self.path) as db:
            kept = {row[0] for row in db.execute("SELECT frq_question_id FROM question_bank_questions")}
        self.assertEqual(kept, set(result["questions"].values()))  # by reference to the grading questions the approval created
        detail = self.bank.question(questions[0]["id"])
        self.assertEqual([v["version"] for v in detail["other_versions"]], ["B"])
        self.frq.process_cloud()
        tagged = [q for q in self.bank.view(self.term["definition_id"])["questions"] if q["classification"]]
        self.assertEqual(len(tagged), 4)
        [request] = [payload_of(r["body"]) for r in self.host.requests if prompt_name(r["body"]) == "bank_tag"]
        self.assertEqual(request["questions"][0]["label"], "Section 3.3")  # the quiz's printed section travels as a hint
        self.assertEqual(self.bank.sync(self.term["definition_id"]), 0)

    def test_a_rebuilt_quiz_is_still_one_copy_in_the_bank_and_its_grades_count_for_it(self):
        """requirement, 2026-10-02: Quiz 4's rebuild was banked a second time as an assessment once its rubrics were approved, its
        multiple-choice options lost. A rebuild reprints the SAME approved problems: they stay the bank's one copy from the approved
        LaTeX, and the grades of the rebuilt questions count for it."""
        from document_identity import quiz_latex
        from frq_fixtures import finalize_rubrics
        real = quiz_latex.render  # approved while Faculty still printed the old single corner code, so a rebuild has work to do
        with patch("document_identity.quiz_latex.render", side_effect=lambda quiz, letter: real(quiz, letter).replace("[letterpaper,12pt]", "[letterpaper,12pt,pagecode]", 1)):
            done = self.approved()
        self.frq.cancel_rubric_quality(done["plan_id"], {})
        definition = self.term["definition_id"]
        self.bank.sync(definition)
        def held():
            with connect(self.path) as db:
                return {row[0]: (row[1], row[2]) for row in db.execute("SELECT id, frq_question_id, origin FROM question_bank_questions")}
        before = held()
        self.assertEqual(len(before), 4)
        with connect(self.path) as db:
            approval_id, draft_id = db.execute("SELECT id,draft_id FROM quiz_draft_approvals WHERE status='complete'").fetchone()
        asked = self.client.post(f"/api/quiz-drafts/{draft_id}/approvals/{approval_id}/rebuild", json={"request_id": str(uuid.uuid4()), "confirmation": "rebuild"},
                                 headers=quiz_fixture.HEADERS)
        self.assertEqual(asked.status_code, 202, asked.json)
        self.frq.process_cloud()
        rebuilt = self.frq.quiz_draft(draft_id)["approved"]
        self.assertEqual(rebuilt["rebuilds"][-1]["status"], "complete", rebuilt["rebuilds"][-1]["error"])
        rebuilt = rebuilt["result"]
        self.assertFalse(set(rebuilt["questions"].values()) & set(done["questions"].values()))
        self.frq.cancel_rubric_quality(rebuilt["plan_id"], {})  # the review the rebuild asked for (the fixture model cannot answer it)
        finalize_rubrics(self.frq, rebuilt["plan_id"])  # the rebuilt questions' rubrics approved, which made them "graded assessments"
        self.assertEqual(self.bank.sync(definition), 0)
        self.assertEqual(held(), before)  # no second copy of any question
        self.assertTrue(all(origin == "quiz_draft" for _, origin in before.values()))
        self.assertEqual(self.bank.view(definition)["counts"]["waiting_for_text"], 0)
        # The rebuilt question's certified grade counts for the approval's copy, and the term it was used in shows on it.
        copy_id = next(qid for qid, (frq, _) in before.items() if frq == done["questions"]["1:A"])
        copy = next(q for q in self.bank.view(definition)["questions"] if q["id"] == copy_id)
        self.assertEqual(copy["terms"], ["Fall 2026"])
        with connect(self.path) as db:
            event = db.execute("SELECT event_id FROM frq_assignments WHERE id=?", (rebuilt["plan_id"],)).fetchone()[0]
            section, enrollment = db.execute("SELECT section_id, enrollment_id FROM exam_participants WHERE event_id=? LIMIT 1", (event,)).fetchone()
            db.execute("""INSERT INTO frq_certifications(id,assignment_id,request_id,request_sha256,fingerprint,rule,parts_json,decisions_sha256,bubble_sha256,
                participants_sha256,snapshot_sha256,summary_json,checks_json,confirmations_json,gradescope_json,gradebook_json,supersedes_id,actor,created_at)
                VALUES ('cert-q',?,'r','x','x','fixture','{}','x','x','x','x','{}','{}','{}','{}','{}',NULL,'workspace','2026-10-02T00:00:00+00:00')""", (rebuilt["plan_id"],))
            parts = {"frq": {"questions": {rebuilt["questions"]["1:A"]: {"score": "2", "max": "4"}}}}
            db.execute("""INSERT INTO frq_certification_students(certification_id,section_id,enrollment_id,status,reason,earned,possible,parts_json)
                VALUES ('cert-q',?,?,'scored','','2','4',?)""", (section, enrollment, json.dumps(parts)))
        self.assertEqual(self.bank.statistics(definition)["questions"][copy["id"]]["earned_pct"], 50)


class FigureCut(unittest.TestCase):
    def test_a_figure_is_cut_without_the_text_its_widened_box_reaches(self):
        """A reader's box a little off (2026-10-03, a textbook's graphs): widened, then trimmed to the drawing; a line of the problem's
        text just above it, and a speck of JPEG noise between, stay out."""
        from PIL import Image, ImageDraw
        from question_bank.pictures import figure_cut
        page = Image.new("RGB", (1000, 1000), "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle((100, 280, 700, 292), fill="black")       # a line of text, 8 px above the graph
        draw.point((400, 296), fill="black")                      # a speck between them
        draw.rectangle((150, 300, 650, 700), outline="black", width=3)
        draw.line((150, 700, 650, 300), fill="blue", width=4)
        out = io.BytesIO()
        page.save(out, "JPEG", quality=90)
        _, width, height, region = figure_cut(out.getvalue(), {"left": .16, "top": .305, "right": .64, "bottom": .69})
        self.assertGreater(region["top"], .292)  # the text is left out ...
        self.assertLessEqual(region["top"], .300)  # ... the graph's frame is in, though the box began below it
        self.assertLessEqual(region["left"], .150)
        self.assertGreaterEqual(region["right"], .652)
        self.assertGreaterEqual(region["bottom"], .702)
        self.assertEqual((width, height), (round((region["right"] - region["left"]) * 1000), round((region["bottom"] - region["top"]) * 1000)))


if __name__ == "__main__":
    unittest.main()
