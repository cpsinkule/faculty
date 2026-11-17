"""Quizzes drafted by a model from the instructor's material (grading/quiz_drafts.py), end to end on a scratch workspace.

A loopback model host plays the Rubric drafting and review model. Everything else is real: the LaTeX is written by Faculty and
compiled in the document sandbox (the toolchain under .tools/texlive), its layout read from the build log, the pages rendered, the
print jobs stamped and validated. A draft creates nothing; approving it makes the assignment, one printed version per quiz version,
the grading plan with every version's questions, confirmed templates and answer areas, and unnamed print jobs captioned with the
version."""
import copy
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
import uuid
from unittest.mock import patch

from pypdf import PdfReader
from werkzeug.exceptions import Conflict

from app import create_app
from rosters import connect
from document_identity import quiz_latex
from grading.quiz_drafts import FORMAT, check_record, resolved, signature
from fake_openai import FakeHost, images_of, payload_of, prompt_name
from test_course_notes import notes_pdf
from test_grading_cloud import endpoint
from test_rosters import csv_bytes
from teach_inbox.service import sha

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
FIGURE = (r"\begin{tikzpicture}[x=0.3cm,y=0.3cm]\quizaxes{20}{10}{4}{2}\draw[thick] (0,10) -- (20,0);"
          r"\node[above right] at (10,5) {$x+2y=20$};\end{tikzpicture}")


def quiz_answer(payload, *, harder=False):
    letters = payload["request"]["versions"]
    numbers = {letter: index + 2 for index, letter in enumerate(letters)}
    return {"sections_line": "Sections 3.1–3.3", "directions": r"PRINT your name. Show all work for free-response problems and box your final answer.",
            "notes": "", "changes": "Problem 2 asks for the intercepts too." if harder else "",
            "problems": [
                {"kind": "multiple_choice", "tag": "Section 3.3", "points": 2, "page": 1, "space": 0, "parts": [], "rubric_feedback": "",
                 "versions": [{"version": v, "statement": f"Which value of $x$ solves ${numbers[v]}x = {2 * numbers[v]}$?", "left": "", "parts": [],
                               "choices": ["$x = 1$", "$x = 2$", "$x = 3$", "$x = 4$"], "correct_choice": "b", "figure": "", "figure_side": "",
                               "scaffold": "", "solution": f"Divide by {numbers[v]}.", "final_answers": [{"part": "", "answer": "(b) $x = 2$"}]} for v in letters]},
                {"kind": "free_response", "tag": "Section 3.2", "points": 3, "page": 2 if harder else 1, "space": 1,
                 "parts": [{"label": "a", "points": 1}, {"label": "b", "points": 2}], "rubric_feedback": "",
                 "versions": [{"version": v, "statement": r"Consider the line graphed at right, which costs \$40 to draw." + (" Find its intercepts too." if harder else ""),
                               "left": r"\[x + 2y = 20\]", "parts": [{"label": "a", "text": "Is the region bounded?"}, {"label": "b", "text": f"Find $y$ when $x = {numbers[v]}$."}],
                               "choices": [], "correct_choice": "", "figure": FIGURE, "figure_side": "right", "scaffold": r"\vspace{20mm}\textbf{Solution:}",
                               "solution": "Substitute.", "final_answers": [{"part": "b", "answer": f"$y = {10 - numbers[v] / 2:g}$"}]} for v in letters]}]}


def rubric_answer(payload):
    versions = [v["version"] for v in payload["problem"]["versions"]]
    points = payload["problem"]["versions"][0]["points"]
    if payload["problem"]["versions"][0]["choices"]:
        items = [(points, "Correct choice: $$(b)$$ $$x = 2$$"), (0, "No answer or completely incorrect")]
    else:
        items = [(points, "Correct answer with supporting work: $$y$$"), (1, "Part A: Correct answer: bounded"), (2, "Part B: Correct answer: $$y$$"),
                 (0, "No answer or completely incorrect")]
    return {"solutions": [{"version": v, "solution": "Worked.", "final_answers": []} for v in versions], "points": points, "notes": "",
            # LINKED VERSIONS (2026-10-02): Part A's outcome differs between the versions in this fake; every other line is the same.
            "items": [{"points": p, "link": "related" if d.startswith("Part A") else "same",
                       "descriptions": [{"version": v, "description": d, "clarification": "Apply only when no other rubric item applies to the response." if p == 0 else ""}
                                        for v in versions]} for p, d in items]}


def key_answer(payload):
    """The answer-key reader (`answer_key_read`): the writer's final answers in the comparable form, one part each; a multiple-choice
    key holds the letter and then the entry itself."""
    if payload.get("correct_choice"):
        return {"parts": [{"label": "", "accept": [f"({payload['correct_choice']})", "x = 2"], "basis": "The correct choice and its entry."}], "unreadable": ""}
    return {"parts": [{"label": a["part"], "accept": [a["answer"].strip("$")], "basis": "From the worked solution."} for a in payload["author_answers"]], "unreadable": ""}


def deducting_answer(payload):
    """A deducting rubric, as the instructor's drafted quizzes use: every line takes points off, so no line is worth the whole
    question and `answers.key_from_rubric` reads no key from it (measured 2026-10-11: 0 of 16 and 0 of 15 questions)."""
    versions = [v["version"] for v in payload["problem"]["versions"]]
    points = payload["problem"]["versions"][0]["points"]
    items = [(0, "Correct answer with supporting work"), (-1, "Arithmetic error"), (-points, "No answer or completely incorrect")]
    return {"solutions": [{"version": v, "solution": "Worked.", "final_answers": []} for v in versions], "points": points, "notes": "",
            "items": [{"points": p, "link": "same", "descriptions": [{"version": v, "description": d, "clarification": ""} for v in versions]} for p, d in items]}


def review_answer(payload, errors=(), clarify=None):
    """The rubric reviewer (`rubric_quality_versions`: every version of one problem; `rubric_quality`: one question alone) with
    nothing to pair: each line examined once, `clarify` ({line ID: text}) proposed on every version, `errors` as its suspected
    errors (each as the reviewer's v4 names one finding for the versions it arises on)."""
    lines = lambda rubric: [{"item_id": item["id"], "clarification": (clarify or {}).get(item["id"], ""), "excludes": [], "reason": ""} for item in rubric["items"]]
    if "problem" not in payload:
        return {"summary": "", "suspected_errors": list(errors), "items": lines(payload["question"]["rubric"])}
    return {"summary": "", "suspected_errors": list(errors), "consistency_issues": [], "parallel_lines": [],
            "versions": [{"version": v["version"], "summary": "", "items": lines(v["question"]["rubric"])} for v in payload["problem"]["versions"]]}


def resolve_answer(payload, how="Settled as you said.", clarify=None, resolved=True):
    """The reviewer resolving a problem's concerns (`rubric_quality_resolve`): every concern answered by its number, the guidance it
    was given kept line for line, `clarify` ({line ID: text}) written on every version."""
    return {"summary": "", "suspected_errors": [], "consistency_issues": [], "parallel_lines": [],
            "versions": [{"version": v["version"], "summary": "", "items": [
                {"item_id": row["item_id"], "clarification": (clarify or {}).get(row["item_id"], row["clarification"]), "excludes": row["excludes"], "reason": ""}
                for row in v["current"]]} for v in payload["problem"]["versions"]],
            "concerns": [{"concern": c["concern"], "resolved": resolved, "how": how, "not_applicable": [], "title": "", "decision": "", "group": "", "choices": []}
                         for c in payload["concerns"]]}


def reviewing_a_draft(path):
    """Whether the rubric reviewer's request in flight is a quiz REVISION's own (the check made while the quiz is drafted, QUIZZES "A
    REVISION", 2026-10-11) rather than a grading plan's. The two send the same prompt and payload by design, so only the call's
    owner tells them apart. These fixtures answer the first and leave the second unanswered, as they always did: a check the
    approval queues waits, and the tests of what an approval leaves behind read as before."""
    with connect(path) as db:
        return bool(db.execute("SELECT 1 FROM frq_cloud_calls WHERE state='submitted' AND quiz_revision_id IS NOT NULL AND step LIKE 'rubric_quality:%'").fetchone())


# A writer's answer that changes nothing: the request's previous quiz, with no edits.
UNTOUCHED = {"base": "previous", "sections_line": "", "directions": "", "problems": [], "notes": "", "changes": "", "edits": []}


class QuizDrafts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.client = self.app.test_client()
        course = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Math", "term": "Fall", "year": 2026, "sections": [501, 502], "multiple_sections": True,
                                                        "is_current": True}, headers=HEADERS)
        self.assertEqual(course.status_code, 201, course.json)
        group = self.client.get("/api/state").json["course_groups"][0]
        roster = self.client.post(f"/api/course-groups/{group['id']}/sections/501/roster", data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS)
        self.assertEqual(roster.status_code, 201, roster.json)
        self.frq, self.documents = self.app.extensions["frq_grading"], self.app.extensions["documents"]
        self.path = self.app.config["DATABASE"]
        self.host = FakeHost().start()
        self.addCleanup(self.host.stop)
        self.harder = False
        self.host.script = self.answer
        current = self.frq.settings()
        tiers = {tier: {"reader": "alpha-vision", "primary": "alpha-vision", "second": "", "verifier": ""} for tier in ("cloud_standard", "cloud_high_assurance")}
        self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "endpoints": [endpoint(self.host, "alpha-vision")],
                                                                             "tiers": tiers, "yardstick": "", "rubric_reviewer": "alpha-vision", "max_retries": 3}})
        with connect(self.path) as db:
            self.term = db.execute("SELECT id,definition_id FROM course_terms").fetchone()
            self.sections = {r["number"]: r["id"] for r in db.execute("SELECT id,number FROM course_sections")}

    def answer(self, body, host):
        name, payload = prompt_name(body), payload_of(body)
        if name == "quiz_write":
            return quiz_answer(payload, harder=self.harder)
        if name == "quiz_check":
            return {"issues": [], "notes": ""}
        if name == "quiz_rubric":
            return rubric_answer(payload)
        if name == "answer_key_read":
            return key_answer(payload)
        # The rubric check a revision makes as its last step (2026-10-11): the reviewer finds nothing. A plan's own check, queued by
        # an approval that could not carry the draft's, is still left unanswered (`reviewing_a_draft`).
        if name in ("rubric_quality_versions", "rubric_quality") and reviewing_a_draft(self.path):
            return review_answer(payload)
        return ("status", 500, {}, json.dumps({"error": "not scripted"}))

    def draft(self, examples=(), **more):
        return self.frq.create_quiz_draft({"request_id": str(uuid.uuid4()), "term_id": self.term["id"], "title": "Chapter 3 Quiz", "total": 5,
                                           "versions": 2, "mode": "positive", "instructions": "Sections 3.1-3.3: one multiple choice, one free response.",
                                           "examples": list(examples), **more},
                                          [("notes.txt", b"Linear programming: corner points, bounded regions.")])

    def example(self):
        """A past quiz's blank template: an earlier quiz compiled by the same class, uploaded as the instructor uploads a template."""
        from document_identity.compiler import compile_sources
        earlier = quiz_latex.clean(quiz_answer({"request": {"versions": ["A"]}}), ["A"], title_line="MATH 140 Fall 2026 Chapter 2 Quiz", total=5)
        pdf = compile_sources(quiz_latex.sources(earlier, "A"))[0]
        quiz2 = self.documents.create_assignment(self.term["definition_id"], "quiz-2", "Quiz 2")
        return self.app.extensions["assignment_templates"].upload(quiz2["id"], "Version A · uploaded template 1", str(uuid.uuid4()), {"q2a.pdf": pdf})

    def test_a_draft_compiles_every_version_is_revised_and_approved_into_print_and_grading(self):
        example = self.example()
        options = self.frq.quiz_draft_options(self.term["definition_id"])
        self.assertEqual(options["courses"][0]["suggested_examples"], [example["id"]])  # the newest quiz's first version
        draft = self.draft([example["id"]])
        self.assertEqual((draft["state"], draft["versions"], draft["title_line"]), ("drafting", ["A", "B"], "MATH 140 Fall 2026 Chapter 3 Quiz"))
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        revision = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])
        self.assertEqual(draft["state"], "ready", revision.get("error"))
        result = revision["result"]
        quiz = result["quiz"]
        self.assertEqual([p["kind"] for p in quiz["problems"]], ["multiple_choice", "free_response"])
        # The writer saw the instructor's material; the check saw every printed page; each rubric saw its problem on every version.
        bodies = [r["body"] for r in self.host.requests]
        self.assertEqual([prompt_name(b) for b in bodies][:2], ["quiz_write", "quiz_check"])
        self.assertIn("Linear programming", json.dumps(payload_of(bodies[0])["material"]))
        # The format the dialog starts from reached the writer and the check (QUIZZES, 2026-10-01).
        self.assertEqual([payload_of(b)["request"]["format"] for b in bodies[:2]], [FORMAT, FORMAT])
        # The past quiz to imitate: its text and page to the writer, its page to the check after the drafted pages.
        self.assertIn("Chapter 2 Quiz", json.dumps(payload_of(bodies[0])["example"]))
        self.assertEqual(payload_of(bodies[0])["request"]["examples"], [{"quiz": "Quiz 2", "pages": 1}])
        drafted_pages = sum(len(v["pages"]) for v in result["versions"].values())
        self.assertEqual(len(images_of(bodies[1])), drafted_pages + 1)
        self.assertEqual(payload_of(bodies[1])["pictures"][-1], {"picture": drafted_pages, "example": "Quiz 2 (Version A · uploaded template 1)", "page": 1})
        self.assertEqual([prompt_name(b) for b in bodies[2:4]], ["quiz_rubric", "quiz_rubric"])
        # Every rubric writer reads the instructor's instructions for the model (QUIZZES, 2026-10-01).
        self.assertEqual({payload_of(b).get("instructor_instructions") for b in bodies[2:4]}, {"Sections 3.1-3.3: one multiple choice, one free response."})
        # Then each problem's answer key is read on each version (2026-10-11; AnswerKeysAreCarriedThrough below).
        self.assertEqual([prompt_name(b) for b in bodies[4:8]], ["answer_key_read"] * 4)
        self.assertEqual({payload_of(b)["full_credit_line"] for b in bodies[4:8]}, {"R1"})  # the positive rubric's line worth the whole question
        # Last, the rubric check Key & Rubric Finalization would run, one request per problem (2026-10-11; RubricQuestionsAtDraftReview below).
        self.assertEqual([prompt_name(b) for b in bodies[8:]], ["rubric_quality_versions"] * 2)
        # Each version is a real stamp-safe PDF from Faculty's LaTeX, with its problems' answer areas read from the build log.
        for letter in ("A", "B"):
            pdf, media = self.frq.quiz_file(draft["id"], revision["id"], letter, "pdf")
            self.assertEqual(media, "application/pdf")
            self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 1)
            tex = self.frq.quiz_file(draft["id"], revision["id"], letter, "tex")[0].decode()
            self.assertIn(r"\quizheader{MATH 140 Fall 2026 Chapter 3 Quiz}{Sections 3.1--3.3}{" + letter + "}{5 points}", tex)
            self.assertIn("MATH 140 Fall 2026 Chapter 3 Quiz", PdfReader(io.BytesIO(pdf)).pages[0].extract_text())
            areas = result["versions"][letter]["areas"]
            self.assertEqual(sorted(areas), ["1", "2"])
            self.assertLess(areas["1"][0]["region"]["y"], areas["2"][0]["region"]["y"])
            self.assertAlmostEqual(areas["1"][0]["region"]["y"] + areas["1"][0]["region"]["height"], areas["2"][0]["region"]["y"], places=5)
            self.assertEqual(self.frq.quiz_file(draft["id"], revision["id"], letter, "page", 1)[1], "image/png")
        self.assertEqual([r["rubric"]["points"] for r in result["rubrics"]], [2, 3])

        # Asking for changes: a new revision from the one shown. Problem 1 is unchanged, so its rubric is kept; problem 2 moved and changed.
        self.harder = True
        before = len(self.host.requests)
        command = {"request_id": str(uuid.uuid4()), "based_on": revision["id"], "feedback": "Ask for the intercepts in problem 2."}
        self.frq.revise_quiz_draft(draft["id"], command)
        self.assertEqual(len(self.frq.revise_quiz_draft(draft["id"], command)["revisions"]), 2)
        with self.assertRaises(Conflict):
            self.frq.revise_quiz_draft(draft["id"], {**command, "feedback": "A different request must not replay the earlier one."})
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        second = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])
        self.assertEqual((second["number"], second["based_on"]), (2, revision["id"]))
        asked = [r["body"] for r in self.host.requests[before:]]
        self.assertEqual(payload_of(asked[0])["feedback"], "Ask for the intercepts in problem 2.")
        self.assertEqual(payload_of(asked[0])["previous"]["problems"][1]["page"], 1)
        self.assertEqual([prompt_name(b) for b in asked].count("quiz_rubric"), 1)
        self.assertEqual(second["result"]["rubrics"][0]["carried_from"], revision["id"])
        self.assertEqual(second["result"]["changes"], "Problem 2 asks for the intercepts too.")
        self.assertEqual(len(PdfReader(io.BytesIO(self.frq.quiz_file(draft["id"], second["id"], "A", "pdf")[0])).pages), 2)

        # Approving an older revision is refused; the latest is approved for its sections, with the copies of each version printed
        # for all of them as one stack (requirement, 2026-10-01).
        with self.assertRaises(Conflict):
            self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": revision["id"], "confirmation": "approve",
                                                      "key": "chapter-3-quiz", "title": "Chapter 3 Quiz", "sections": []})
        options = self.frq.quiz_approval_options(draft["id"])
        self.assertEqual([s["number"] for s in options["sections"]], [501, 502])
        self.assertEqual(sum(options["copies"].values()), sum(s["students"] for s in options["sections"]))
        self.assertNotIn("caption", options)  # nothing printed says the version (requirement, 2026-10-01)
        command = {"request_id": str(uuid.uuid4()), "revision_id": second["id"], "confirmation": "approve", "key": options["assignment"]["key"],
                   "title": "Chapter 3 Quiz", "sections": [s["id"] for s in options["sections"][:1]], "copies": {"A": 2, "B": 1}}
        # 'Given on' (requirement, 2026-10-02) is optional and a campus date as typed (YYYY-MM-DD); anything else is refused.
        for wrong in ("10/08/2026", "2026-02-30", 20261008):
            with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                self.frq.approve_quiz_draft(draft["id"], {**command, "request_id": str(uuid.uuid4()), "given_on": wrong})
        command["given_on"] = "2026-10-08"
        self.frq.approve_quiz_draft(draft["id"], command)
        self.assertEqual(self.frq.approve_quiz_draft(draft["id"], command)["approved"]["status"], "pending")  # the same request is the same approval
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        approval = draft["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))
        done = approval["result"]
        # One printed version per quiz version, each exactly the approved PDF.
        with connect(self.path) as db:
            versions = {r["label"]: dict(r) for r in db.execute("SELECT * FROM assignment_versions WHERE assignment_id=?", (done["assignment_id"],))}
            self.assertEqual(sorted(versions), ["A", "B"])
            self.assertEqual(json.loads(versions["A"]["compiler_json"])["generator"], "faculty-quiz-draft")
            self.assertEqual(versions["B"]["page_count"], 2)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        # The grading plan: each version's questions on its own document, confirmed templates, answer areas and review readiness.
        plan = self.frq.assignment(done["plan_id"])
        questions = plan["questions"]
        # LINKED VERSIONS: the writer's kinds are in each rubric's provenance, and the links between the versions start from them.
        free = next(q for q in questions if q["exam_version"] == "A" and q["content"]["printed_number"] == "2")
        self.assertEqual(free["rubric"]["compiler"]["line_kinds"], {"R1": "same", "R2": "related", "R3": "same", "R4": "same"})
        links = self.frq.rubric_links(done["plan_id"])
        self.assertTrue(links["sets"] and all(s["origin"] == "drafted" for s in links["sets"]))
        self.assertEqual(sorted(s["kind"] for s in links["sets"]), ["related", "same", "same", "same", "same", "same"])
        self.assertEqual(sorted((q["exam_version"], q["content"]["printed_number"]) for q in questions), [("A", "1"), ("A", "2"), ("B", "1"), ("B", "2")])
        self.assertTrue(all(q["assignment_version_id"] == versions[q["exam_version"]]["id"] for q in questions))
        self.assertTrue(all(q["location_check"]["status"] == "confirmed" for q in questions))
        self.assertTrue(self.frq.assignment_templates(done["plan_id"])["confirmed"])
        with connect(self.path) as db:
            status = self.frq.rubric_quality_status(db, done["plan_id"])
            self.assertTrue(all(r["ready"] for r in status["readiness"].values()))
            second_page = [q for q in questions if q["content"]["printed_number"] == "2"][0]
            self.assertEqual(second_page["content"]["regions"][0]["page"], 2)
            # Drafted clarifications became the line comments the rubric review reads.
            self.assertTrue(db.execute("SELECT COUNT(*) FROM frq_rubric_line_comments WHERE assignment_id=?", (done["plan_id"],)).fetchone()[0])
            self.assertEqual(db.execute("SELECT status FROM frq_rubric_quality_checks WHERE assignment_id=?", (done["plan_id"],)).fetchone()[0], "pending")
        self.assertEqual(done["rubric_review"], "requested")
        # On Grading from the approval on, before any paper: its rubrics can be finalized while the quiz is being written.
        listed = self.frq.overview(navigation=True)["assignments"]
        self.assertEqual([(p["id"], p["entry"].get("set_up_by_quiz_draft")) for p in listed], [(done["plan_id"], True)])
        # Its Assignments & printing card leads there too, and back to this draft's page; grading_plan_id still waits for responses.
        [row] = [r for r in self.documents.catalog()["lifecycle"] if r["assignment_id"] == done["assignment_id"]]
        self.assertEqual((row["grading_entry_plan_id"], row["grading_plan_id"], row["quiz_draft_id"]), (done["plan_id"], None, draft["id"]))
        # Printed as one stack for the term in both versions: 2 + 1 copies.
        self.assertEqual((row["printed_copies"], row["printed_versions"], row["printed_stack"]), (3, ["A", "B"], True))
        # One unnamed print job per version for the term, naming no section, captioned with the version, attached to the exam record.
        self.assertEqual(sorted((b["version"], b["copies"]) for b in done["batches"]), [("A", 2), ("B", 1)])
        event = self.frq.exams.get(done["event_id"])
        self.assertEqual({b["id"] for b in event["batches"]}, {b["batch_id"] for b in done["batches"]})
        # Given on: the exam record's main sitting on that very date, through the Exams page's own sittings path (Home's Coming up,
        # the Calendar and the Exams hub read it from there).
        self.assertEqual([(s["label"], s["kind"], s["date"], s["time"]) for s in event["sittings"]], [("Chapter 3 Quiz", "main", "2026-10-08", "")])
        self.assertEqual((approval["input"]["given_on"], approval["progress"]["plan"]["given_on"]), ("2026-10-08", "2026-10-08"))
        self.assertNotIn("given_on_kept", approval["progress"]["plan"])
        self.assertEqual(row["exam_date"], "2026-10-08")
        for batch in done["batches"]:
            printed = self.documents.get_batch(batch["batch_id"])
            self.assertEqual((printed["section_id"], printed["term_id"], [s["number"] for s in printed["sections"]]), (None, self.term["id"], [501]))
            copies = printed["copies"]
            self.assertTrue(all(c["enrollment_id"] is None for c in copies))
            # No caption: "I don't want the assignments to have this quiz#/version/copy caption on them" (requirement, 2026-10-01).
            self.assertTrue(all(not c["caption"] for c in copies))
        # Nor may any other print job's caption name the version.
        with self.assertRaisesRegex(ValueError, "nothing printed may say which version"):
            self.documents.create_batch(done["versions"]["A"], None, 1, str(uuid.uuid4()), caption="Chapter 3 Quiz · Version A",
                                        term_id=self.term["id"], section_ids=[printed["sections"][0]["id"]])
            # Two 7 mm corner codes, the class's default since 2026-10-18 ("I want all stamping to be this size matrix now").
            self.assertEqual(json.loads(printed["configuration_json"])["id"], "faculty-corners-7mm-v1")
        while self.documents.build_one():
            pass
        stack, name = self.frq.quiz_section_stack(draft["id"], approval["id"])
        self.assertEqual(name, "Chapter-3-Quiz-all-versions.pdf")
        pages = PdfReader(io.BytesIO(stack)).pages
        self.assertEqual(len(pages), 3 * 2)
        # Copy 1 of A, copy 1 of B, then copy 2 of A: each copy's pages together. Nothing printed names the version (instructor,
        # 2026-10-01: "I don't want the students to be able to tell which version they had"), so each page is told by its printed
        # code (no alias is printed since 2026-10-18), against the print jobs' own manifests.
        printed_as = {}
        for batch in done["batches"]:
            for entry in json.loads(self.documents.download(batch["batch_id"], manifest=True))["pages"]:
                printed_as[entry["token"]] = (batch["version"], entry["copy_number"], entry["page_number"])
        texts = [p.extract_text() for p in pages]
        from document_identity.media import decode_page, render_pages
        self.assertEqual([printed_as[decode_page(image)[0]["payload"]] for image in render_pages(stack, 300)],
                         [("A", 1, 1), ("A", 1, 2), ("B", 1, 1), ("B", 1, 2), ("A", 2, 1), ("A", 2, 2)])
        self.assertFalse([text for text in texts if re.search(r"Version|Copy \d", text)])
        # The Print page's one Print PDF is the same stack (requirement, 2026-10-01), whichever order it names the jobs in.
        ids = [b["batch_id"] for b in reversed(done["batches"])]
        printed = self.client.get(f"/api/documents/stack?batches={','.join(ids)}", headers=HEADERS)
        self.assertEqual(printed.status_code, 200, printed.json)
        self.assertIn("Chapter-3-Quiz-all-versions.pdf", printed.headers["Content-Disposition"])
        self.assertEqual(printed.data, stack)
        # The Print page opens it in the file viewer (VIEWER), the browser's own PDF viewer there, its comma percent-encoded
        # (static/print-files.js): a bare comma is not a file route, and the click did nothing (2026-10-02).
        route = f"/api/documents/stack?batches={'%2C'.join(ids)}"
        opened = self.client.get("/api/viewer/file", query_string={"route": route, "native": "1"}, headers=HEADERS)
        self.assertEqual(opened.status_code, 200, opened.json)
        self.assertEqual((opened.json["kind"], opened.json["native"], opened.json["size"]), ("pdf", True, len(stack)))
        framed = self.client.get("/api/viewer/pdf", query_string={"route": route, "sha256": opened.json["sha256"]}, headers=HEADERS)
        self.assertEqual((framed.status_code, framed.data), (200, stack))
        bare = self.client.get("/api/viewer/file", query_string={"route": f"/api/documents/stack?batches={','.join(ids)}"}, headers=HEADERS)
        self.assertEqual(bare.status_code, 404)  # the viewer refuses every route it cannot read as not found (file_viewer _read)
        self.assertIn("percent-encode", bare.get_data(as_text=True))
        # Two jobs of one version are not a stack.
        self.assertEqual(self.client.get(f"/api/documents/stack?batches={ids[0]},{ids[0]}", headers=HEADERS).status_code, 400)
        # Approved: no more revisions, and no second approval for the term.
        with self.assertRaises(Conflict):
            self.frq.revise_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "based_on": second["id"], "feedback": "More."})

    def approved(self):
        draft = self.draft()
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        options = self.frq.quiz_approval_options(draft["id"])
        self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": draft["latest_complete"], "confirmation": "approve",
                                                  "key": options["assignment"]["key"], "title": "Chapter 3 Quiz",
                                                  "sections": [self.sections[501]], "copies": {"A": 2, "B": 1}})
        self.frq.process_cloud()
        done = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(done["status"], "complete", done.get("error"))
        while self.documents.build_one():
            pass
        return done["result"]

    def test_a_given_on_date_never_replaces_the_date_the_exam_record_already_has(self):
        """'Given on' (requirement, 2026-10-02): an exam record whose main sitting is already dated (the Exams page, Create New
        Assignment, or an earlier failed approval of this draft) keeps that date. The approval records the date the record holds
        and that it was kept, which the draft's page says (static/quiz-drafts.js `givenOnMarkup`, tests/test_quiz_drafts_ui.mjs)."""
        draft = self.draft()
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        options = self.frq.quiz_approval_options(draft["id"])
        assignment = self.documents.create_assignment(self.term["definition_id"], options["assignment"]["key"], "Chapter 3 Quiz")
        event = self.frq.exams.create({"term_id": self.term["id"], "assignment_id": assignment["id"], "title": "Chapter 3 Quiz",
                                       "section_ids": [self.sections[501]]})
        self.frq.exams.save_sitting(event["id"], {"version": event["version"], "label": "Chapter 3 Quiz", "kind": "main", "date": "2026-10-06"})
        self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": draft["latest_complete"], "confirmation": "approve",
                                                  "key": options["assignment"]["key"], "title": "Chapter 3 Quiz", "sections": [self.sections[501]],
                                                  "copies": {"A": 2, "B": 1}, "given_on": "2026-10-08"})
        self.frq.process_cloud()
        approval = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))
        self.assertEqual(approval["result"]["event_id"], event["id"])
        self.assertEqual([(s["kind"], s["date"]) for s in self.frq.exams.get(event["id"])["sittings"]], [("main", "2026-10-06")])
        self.assertEqual(approval["input"]["given_on"], "2026-10-08")  # what was typed stays the approval's input
        plan = approval["progress"]["plan"]
        self.assertEqual((plan["given_on"], plan.get("given_on_kept")), ("2026-10-06", True))
        [row] = [r for r in self.documents.catalog()["lifecycle"] if r["assignment_id"] == assignment["id"]]
        self.assertEqual((row["exam_date"], row["exam_date_chart_id"]), ("2026-10-06", None))  # the main sitting's: changed on its Exams page

    def test_a_planned_row_follows_the_quiz_once_it_is_approved(self):
        """SCHEDULING "CLASS SCHEDULE" (pre-fill): the approval names the planned row of the term's class schedule the quiz stands
        for; the worker links it once the assignment exists, and the row then follows the quiz's own date. A row that is not open
        is refused, and an approval that names none reads as it always did."""
        from rosters import connect
        from scheduling import class_schedule
        saved = class_schedule.save(self.frq, self.term["id"], {"request_id": "plan-1", "base_revision": 0, "set": [
            {"kind": "quiz", "title": "Chapter 3 Quiz", "date": "2026-10-09"}, {"kind": "quiz", "title": "Chapter 4 Quiz", "date": "2026-10-16"}]})
        rows = {row["title"]: row["item"] for row in saved["items"]}
        draft = self.draft()
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        options = self.frq.quiz_approval_options(draft["id"])
        command = {"revision_id": draft["latest_complete"], "confirmation": "approve", "key": options["assignment"]["key"], "title": "Chapter 3 Quiz",
                   "sections": [self.sections[501]], "copies": {"A": 2, "B": 1}, "given_on": "2026-10-08"}
        with self.assertRaisesRegex(ValueError, "no longer open"):
            self.frq.approve_quiz_draft(draft["id"], {**command, "request_id": str(uuid.uuid4()), "planned_item": "0000000000000000"})
        with self.assertRaisesRegex(ValueError, "planned row"):
            self.frq.approve_quiz_draft(draft["id"], {**command, "request_id": str(uuid.uuid4()), "planned_item": 7})
        self.frq.approve_quiz_draft(draft["id"], {**command, "request_id": "approve-1", "planned_item": rows["Chapter 3 Quiz"]})
        self.frq.process_cloud()
        approval = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))
        self.assertEqual((approval["input"]["planned_item"], approval["progress"]["plan"]["planned_linked"]), (rows["Chapter 3 Quiz"], True))
        with connect(self.frq.path) as db:
            plan = {row["title"]: row["link"] for row in class_schedule.plan(db, self.term["id"])["items"]}
            self.assertEqual(plan, {"Chapter 3 Quiz": {"assignment_id": approval["result"]["assignment_id"]}, "Chapter 4 Quiz": None})
            self.assertEqual(db.execute("SELECT request_id, actor FROM scheduling_plan_links").fetchall()[0][0], f"quiz-approval:{approval['id']}")
            view = class_schedule.section_schedule(db, self.sections[501], "2026-10-01", "2026-10-31")
        days = {day["date"]: day["work"] for day in view["days"] if day["work"]}
        # The row follows the quiz: its own date (Oct 8) shows, with what was planned (Oct 9); the other row stays planned.
        self.assertEqual(sorted(days), ["2026-10-08", "2026-10-16"])
        row = days["2026-10-08"][0]
        self.assertEqual((row["title"], row["linked"], row["planned"], row["planned_was"], row["source"]), ("Chapter 3 Quiz", True, False, "2026-10-09", "exam"))
        self.assertEqual((days["2026-10-16"][0]["planned"], view["open_planned"]), (True, 1))

    def test_the_interleaved_stack_is_cut_once_and_refused_once_a_version_is_superseded(self):
        """The file viewer describes a file, shows it, and Download reads it again: the same jobs' stack is cut once (PERFORMANCE)."""
        from document_identity import process
        done = self.approved()
        ids = [b["batch_id"] for b in done["batches"]]
        real, cuts = process.run, []
        def counting(kind, *args, **kwargs):
            cuts.append(kind)
            return real(kind, *args, **kwargs)
        with patch.object(process, "run", side_effect=counting):
            first = self.client.get(f"/api/documents/stack?batches={'%2C'.join(ids)}", headers=HEADERS)
            again = self.client.get(f"/api/documents/stack?batches={','.join(reversed(ids))}", headers=HEADERS)
        self.assertEqual((first.status_code, again.status_code), (200, 200), first.json)
        self.assertEqual(first.data, again.data)
        self.assertEqual(cuts.count("reorder"), 1)
        # A superseded version is refused even though its stack is remembered: the check comes before the memo.
        superseded = self.client.post(f"/api/documents/batches/{ids[0]}/supersede", json={}, headers=HEADERS)
        self.assertEqual(superseded.status_code, 200, superseded.json)
        refused = self.client.get(f"/api/documents/stack?batches={'%2C'.join(ids)}", headers=HEADERS)
        self.assertEqual(refused.status_code, 409, refused.json)
        [row] = [r for r in self.documents.catalog()["lifecycle"] if r["assignment_id"] == done["assignment_id"]]
        self.assertEqual((row["printed_copies"], row["printed_stack"]), (1, False))  # one version left: no longer a stack

    def test_an_approved_quiz_is_rebuilt_in_the_current_page_layout(self):
        # Approved while Faculty still printed the single
        # 20 mm code (`pagecode`), the quiz is compiled again, its plan's questions move onto the new printed versions and new print jobs
        # replace the superseded ones (QUIZZES "REBUILDING AN APPROVED QUIZ").
        real = quiz_latex.render
        with patch("document_identity.quiz_latex.render", side_effect=lambda quiz, letter: real(quiz, letter).replace("[letterpaper,12pt]", "[letterpaper,12pt,pagecode]", 1)):
            done = self.approved()
        self.frq.cancel_rubric_quality(done["plan_id"], {})  # the review the approval asked for is stopped (the fixture model cannot answer it)
        with connect(self.path) as db:
            approval_id, draft_id = db.execute("SELECT id,draft_id FROM quiz_draft_approvals WHERE status='complete'").fetchone()
        layouts = lambda: {v["id"]: (v["label"], v["layout_profile"]) for v in self.documents.catalog()["versions"]}
        self.assertEqual({layouts()[v][1] for v in done["versions"].values()}, {"faculty-page-v1"})
        route = f"/api/quiz-drafts/{draft_id}/approvals/{approval_id}/rebuild"
        self.assertEqual(self.client.post(route, json={"request_id": str(uuid.uuid4())}, headers=HEADERS).status_code, 400)  # not confirmed
        asked = self.client.post(route, json={"request_id": str(uuid.uuid4()), "confirmation": "rebuild"}, headers=HEADERS)
        self.assertEqual(asked.status_code, 202, asked.json)
        self.assertEqual(asked.json["state"], "rebuilding")
        self.frq.process_cloud()
        approved = self.frq.quiz_draft(draft_id)["approved"]
        self.assertEqual(approved["rebuilds"][-1]["status"], "complete", approved["rebuilds"][-1]["error"])
        result = approved["result"]
        # New printed versions of the same assignment, A2 and B2, with the two corner codes and no version printed.
        self.assertEqual(sorted(layouts()[v] for v in result["versions"].values()), [("A2", "faculty-corners-7mm-v1"), ("B2", "faculty-corners-7mm-v1")])
        self.assertEqual(result["replaced_versions"], done["versions"])
        # The plan holds the new questions only, as many as before; the earlier ones are off it, and their versions take none again.
        with connect(self.path) as db:
            pinned = {r[0] for r in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=?", (result["plan_id"],))}
            documents = {d["id"] for d in self.frq.rubric_upload_documents(db, self.frq.plan(db, result["plan_id"]))}
            withdrawn = db.execute("SELECT detail_json FROM frq_events WHERE assignment_id=? AND kind='quiz_rebuild_questions_withdrawn'", (result["plan_id"],)).fetchall()
        self.assertEqual(pinned, set(result["questions"].values()))
        self.assertEqual(len(pinned), len(done["questions"]))
        self.assertFalse(pinned & set(done["questions"].values()))
        self.assertEqual({q["question_id"] for q in json.loads(withdrawn[0][0])["questions"]}, set(done["questions"].values()))
        self.assertEqual(documents, set(result["versions"].values()))
        # Templates and answer areas of the new versions, confirmed as the approval confirmed them.
        setup = self.frq.assignment_templates(result["plan_id"])
        self.assertEqual({s["document_id"] for s in setup["slots"]}, set(result["versions"].values()))
        self.assertTrue(all(s["confirmed"] for s in setup["slots"]))
        self.assertEqual(result["areas_confirmed"], len(pinned))
        # The earlier print jobs are superseded; the new ones print the corner codes and no caption, and the stack is theirs.
        self.assertTrue(all(self.documents.get_batch(b["batch_id"])["superseded_at"] for b in done["batches"]))
        while self.documents.build_one():
            pass
        for batch in result["batches"]:
            printed = self.documents.get_batch(batch["batch_id"])
            self.assertEqual((printed["status"], json.loads(printed["configuration_json"])["id"]), ("ready", "faculty-corners-7mm-v1"))
            self.assertTrue(all(not c["caption"] for c in printed["copies"]))
        self.assertEqual(sorted((b["version"], b["copies"]) for b in result["batches"]), [("A", 2), ("B", 1)])
        stack, _ = self.frq.quiz_section_stack(draft_id, approval_id)
        new_tokens = {e["token"] for b in result["batches"] for e in json.loads(self.documents.download(b["batch_id"], manifest=True))["pages"]}
        texts = [page.extract_text() for page in PdfReader(io.BytesIO(stack)).pages]
        from document_identity.media import decode_page, render_pages
        self.assertTrue(all({c["payload"] for c in decode_page(image)} <= new_tokens for image in render_pages(stack, 300)))
        self.assertFalse([text for text in texts if "Version" in text])
        # Not while the rubric review the rebuild asked for is running; then nothing more to rebuild, and the worker says so.
        waiting = self.client.post(route, json={"request_id": str(uuid.uuid4()), "confirmation": "rebuild"}, headers=HEADERS)
        self.assertEqual((waiting.status_code, "still running" in waiting.json["error"]), (409, True))
        self.frq.cancel_rubric_quality(result["plan_id"], {})
        again = self.client.post(route, json={"request_id": str(uuid.uuid4()), "confirmation": "rebuild"}, headers=HEADERS)
        self.assertEqual(again.status_code, 202, again.json)
        self.frq.process_cloud()
        last = self.frq.quiz_draft(draft_id)["approved"]["rebuilds"][-1]
        self.assertEqual((last["status"], "already printed in the current page layout" in last["error"]), ("failed", True))
        # Once a copy came back in a scan, the quiz is never rebuilt.
        self.scan([self.documents.get_batch(result["batches"][0]["batch_id"])["copies"][0]["id"]])
        refused = self.client.post(route, json={"request_id": str(uuid.uuid4()), "confirmation": "rebuild"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("came back in a scan", refused.json["error"])

    def scan(self, pages):
        """A reviewed-looking scan holding page 1 of each given copy, its stamp read."""
        inbox, scans = self.app.extensions["inbox"], self.app.extensions["document_scans"]
        job = inbox.submit({"action": "scan.receive"}, [{"filename": "quiz.pdf", "content": b"%PDF-fixture" + uuid.uuid4().bytes}], source="email",
                           sender="scanner", original=b"fixture mail", idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        job = inbox.get(job["id"])
        original = next(a for a in job["artifacts"] if a["role"] == "input")
        for index, copy_id in enumerate(pages, 1):
            with connect(self.path) as db:
                token = db.execute("SELECT token FROM page_stamps WHERE printed_copy_id=? AND page_number=1", (copy_id,)).fetchone()[0]
            scans._retain_page(job, original, {"index": index, "pdf": f"page-{index}-{copy_id}".encode(), "png": b"png", "decoder": {}, "blank": False,
                                               "candidates": [{"payload": token}]})
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (job["message_id"],))
        return job

    def test_the_format_starts_as_the_current_quizzes_and_the_instructor_may_change_or_clear_it(self):
        self.assertEqual(self.frq.quiz_draft_options(self.term["definition_id"])["default_format"],
                         "One multiple-choice problem and three free-response problems.")
        base = {"term_id": self.term["id"], "title": "Chapter 4 Quiz", "total": 10, "versions": 2, "mode": "positive", "instructions": "Sections 4.1-4.3."}
        material = [("notes.txt", b"Matrices: row reduction.")]
        for given, kept in ((None, FORMAT), ("  Two multiple-choice problems and two free-response problems. ", "Two multiple-choice problems and two free-response problems."), ("", "")):
            data = {**base, "request_id": str(uuid.uuid4()), **({} if given is None else {"format": given})}
            self.assertEqual(self.frq.create_quiz_draft(data, material)["format"], kept)
        with self.assertRaises(ValueError):
            self.frq.create_quiz_draft({**base, "request_id": str(uuid.uuid4()), "format": "x" * 1001}, material)
        # A cleared format is left out of the request, so the instructions and past quizzes decide.
        self.frq.process_cloud()
        writes = [payload_of(r["body"])["request"] for r in self.host.requests if prompt_name(r["body"]) == "quiz_write"]
        self.assertEqual({w.get("format", "") for w in writes}, {"", "Two multiple-choice problems and two free-response problems.", FORMAT})

    def test_every_rubric_writer_is_sent_the_notes_sections_the_quiz_covers(self):
        """QUIZZES "THE NOTES THE QUIZ COVERS" (requirement, 2026-10-01): the sections the quiz prints in its sections line and tags go
        to each problem's rubric writer, so that no line is built on a method the students were not taught. A rubric drafted without
        them is drafted again once they exist; one drafted with the same notes is kept."""
        def rubric_payloads(since):
            return [payload_of(r["body"]) for r in self.host.requests[since:] if prompt_name(r["body"]) == "quiz_rubric"]

        def latest(draft_id):
            draft = self.frq.quiz_draft(draft_id)
            revision = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])
            self.assertEqual(draft["state"], "ready", revision.get("error"))
            return revision
        # No notes in the course yet: the rubrics are drafted without them, and the revision says so.
        draft = self.draft()
        self.frq.process_cloud()
        first = latest(draft["id"])
        self.assertEqual([("course_notes" in p) for p in rubric_payloads(0)], [False, False])
        self.assertEqual(first["result"]["rubric_notes"], [])
        self.assertIn("The rubrics were drafted without lecture notes: this course has none in Faculty.", first["result"]["warnings"])
        # The course's notes arrive: 3.1 to 3.4, the quiz prints "Sections 3.1–3.3" and tags its problems 3.3 and 3.2.
        pages = [["Lecture Notes"],
                 ["Linear Programming", "3.1 Setting Up Linear Programming Problems", "Objectives: define the decision variables."],
                 ["3.2 Graphing Systems of Linear Inequalities", "Objectives: shade the feasible region S."],
                 ["3.3 Graphical Solution of Linear Programming Problems", "Use the Method of Corners."],
                 ["Corner Point table", "Evaluate the objective at every corner point."],
                 ["3.4 Simplex Method", "Objectives: pivot in the simplex tableau."]]
        outline = [(0, "3.1 Setting Up Linear Programming Problems", 2), (0, "3.2 Graphing Systems of Linear Inequalities", 3),
                   (0, "3.3 Graphical Solution of Linear Programming Problems", 4), (0, "3.4 Simplex Method", 6)]
        self.app.extensions["course_notes"].upload(self.term["definition_id"], notes_pdf(pages, outline), "notes.pdf", "notes-1")
        # A revision changes problem 2 only, but problem 1's rubric had no notes: both are drafted again, with them.
        self.harder = True
        before = len(self.host.requests)
        self.frq.revise_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "based_on": first["id"], "feedback": "Ask for the intercepts in problem 2."})
        self.frq.process_cloud()
        second = latest(draft["id"])
        sent = rubric_payloads(before)
        self.assertEqual(len(sent), 2)
        for payload in sent:
            notes = payload["course_notes"]
            self.assertEqual([s["number"] for s in notes["sections"]], ["3.1", "3.2", "3.3"])
            self.assertEqual([s["pages"] for s in notes["sections"]], [1, 1, 2])
            self.assertIn("Method of Corners", notes["sections"][2]["text"])
            self.assertIn("[page 2]\n", notes["sections"][2]["text"])
            self.assertNotIn("simplex", json.dumps(notes))
        self.assertEqual(second["result"]["rubric_notes"], ["3.1", "3.2", "3.3"])
        self.assertFalse([w for w in second["result"]["warnings"] if "lecture notes" in w])
        self.assertFalse([r for r in second["result"]["rubrics"] if r.get("carried_from")])
        # Asked again with nothing changed in a problem or the notes: both rubrics are kept.
        before = len(self.host.requests)
        self.frq.revise_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "based_on": second["id"], "feedback": "Keep it as it is."})
        self.frq.process_cloud()
        third = latest(draft["id"])
        self.assertEqual(rubric_payloads(before), [])
        self.assertEqual([r["carried_from"] for r in third["result"]["rubrics"]], [second["id"], second["id"]])

    def test_the_notes_sections_chosen_for_a_draft_go_to_the_quiz_writer(self):
        """QUIZZES "NOTES SECTIONS FOR THE WRITER" (requirement, 2026-10-28): the new-draft window
        offers the notes' sections; the ones chosen are the draft's request, by number in the notes' order, and their text goes to the
        writer with every revision, on every connection, and joins what the rubric writers are sent."""
        def latest(draft_id):
            draft = self.frq.quiz_draft(draft_id)
            revision = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])
            self.assertEqual(draft["state"], "ready", revision.get("error"))
            return revision

        def payloads(since, name):
            return [payload_of(r["body"]) for r in self.host.requests[since:] if prompt_name(r["body"]) == name]
        self.assertEqual(self.frq.quiz_draft_options(self.term["definition_id"])["courses"][0]["notes_sections"], [])
        with self.assertRaises(ValueError):  # no notes yet: nothing to choose
            self.draft(notes_sections="3.1")
        pages = [["Lecture Notes"],
                 ["Linear Programming", "3.1 Setting Up Linear Programming Problems", "Objectives: define the decision variables."],
                 ["3.2 Graphing Systems of Linear Inequalities", "Objectives: shade the feasible region S."],
                 ["3.3 Graphical Solution of Linear Programming Problems", "Use the Method of Corners."],
                 ["3.4 Simplex Method", "Objectives: pivot in the simplex tableau."]]
        outline = [(0, "3.1 Setting Up Linear Programming Problems", 2), (0, "3.2 Graphing Systems of Linear Inequalities", 3),
                   (0, "3.3 Graphical Solution of Linear Programming Problems", 4), (0, "3.4 Simplex Method", 5)]
        self.app.extensions["course_notes"].upload(self.term["definition_id"], notes_pdf(pages, outline), "notes.pdf", "notes-1")
        offered = self.frq.quiz_draft_options(self.term["definition_id"])["courses"][0]["notes_sections"]
        self.assertEqual([(s["number"], s["title"]) for s in offered], [("3.1", "Setting Up Linear Programming Problems"),
                         ("3.2", "Graphing Systems of Linear Inequalities"), ("3.3", "Graphical Solution of Linear Programming Problems"),
                         ("3.4", "Simplex Method")])
        for refused in ("5.1", "3.4,3.4x"):
            with self.assertRaises(ValueError):
                self.draft(notes_sections=refused)
        # Chosen out of order: kept in the notes' order, the writer reads 3.1 and 3.4 and nothing else.
        before = len(self.host.requests)
        draft = self.draft(notes_sections="3.4,3.1")
        self.assertEqual(draft["notes_sections"], ["3.1", "3.4"])
        self.frq.process_cloud()
        first = latest(draft["id"])
        writes = payloads(before, "quiz_write")
        notes = writes[0]["course_notes"]
        self.assertEqual([s["number"] for s in notes["sections"]], ["3.1", "3.4"])
        self.assertIn("simplex tableau", notes["sections"][1]["text"])
        self.assertNotIn("Method of Corners", json.dumps(notes))
        self.assertEqual(first["result"]["writer_notes"], ["3.1", "3.4"])
        # The rubric writers get the chosen sections with those the quiz prints ("Sections 3.1-3.3").
        self.assertEqual(first["result"]["rubric_notes"], ["3.1", "3.2", "3.3", "3.4"])
        for payload in payloads(before, "quiz_rubric"):
            self.assertEqual([s["number"] for s in payload["course_notes"]["sections"]], ["3.1", "3.2", "3.3", "3.4"])
        self.assertFalse([w for w in first["result"]["warnings"] if "lecture notes" in w])
        # A revision is written with them too.
        before = len(self.host.requests)
        self.frq.revise_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "based_on": first["id"], "feedback": "Ask for the intercepts in problem 2."})
        self.frq.process_cloud()
        latest(draft["id"])
        self.assertEqual([s["number"] for s in payloads(before, "quiz_write")[0]["course_notes"]["sections"]], ["3.1", "3.4"])
        # Sections alone are enough to draft from (requirement, 2026-10-28); with nothing at all, the request says what to give.
        bare = {"request_id": str(uuid.uuid4()), "term_id": self.term["id"], "title": "Quiz 6", "total": 5, "versions": 2, "mode": "positive", "instructions": ""}
        with self.assertRaisesRegex(ValueError, "Tick the lecture notes sections the quiz covers"):
            self.frq.create_quiz_draft({**bare, "notes_sections": ""}, [])
        self.assertEqual(self.frq.create_quiz_draft({**bare, "notes_sections": "3.2"}, [])["notes_sections"], ["3.2"])
        # Not sent at all (a caller other than the window), they are the sections the instructions name, else the title.
        self.assertEqual(self.frq.create_quiz_draft({**bare, "request_id": str(uuid.uuid4()), "title": "Chapter 3 Quiz"}, [])["notes_sections"],
                         ["3.1", "3.2", "3.3", "3.4"])
        self.assertEqual(self.draft()["notes_sections"], ["3.1", "3.2", "3.3"])   # "Sections 3.1-3.3: ...", over the title's chapter
        self.frq.process_cloud()
        # None ticked in the window is none: the request it always was, no notes sent to the writer, nothing recorded.
        before = len(self.host.requests)
        plain = self.draft(notes_sections="")
        self.assertNotIn("notes_sections", plain)
        self.frq.process_cloud()
        self.assertNotIn("writer_notes", latest(plain["id"])["result"])
        self.assertFalse([p for p in payloads(before, "quiz_write") if "course_notes" in p])

    def test_bank_questions_of_the_chosen_sections_go_to_the_writer_and_a_reprint_is_refused(self):
        """QUIZZES "NOTES SECTIONS FOR THE WRITER" (requirement, 2026-10-28): the bank's questions filed under a chosen section (or a topic under it) go to the writer,
        retired ones never, at most `BANK_PER_SECTION` a section; an answer that reprints one with its numbers is refused and asked again."""
        from grading import drafting_tools
        pages = [["Lecture Notes"], ["Linear Programming", "3.1 Setting Up Linear Programming Problems", "Define the decision variables."],
                 ["3.2 Graphing Systems of Linear Inequalities", "Shade the feasible region S."],
                 ["3.3 Graphical Solution of Linear Programming Problems", "Use the Method of Corners."]]
        outline = [(0, "3.1 Setting Up Linear Programming Problems", 2), (0, "3.2 Graphing Systems of Linear Inequalities", 3),
                   (0, "3.3 Graphical Solution of Linear Programming Problems", 4)]
        definition = self.term["definition_id"]
        self.app.extensions["course_notes"].upload(definition, notes_pdf(pages, outline), "notes.pdf", "notes-1")
        bank = self.app.extensions["question_bank"]
        bank.sync(definition)
        with connect(self.path) as db:
            nodes = {node["number"]: node["id"] for node in bank._outline(db, definition) if node["level"] == "section"}
        topic = bank.add_node(definition, {"request_id": str(uuid.uuid4()), "level": "topic", "parent_id": nodes["3.2"], "title": "Corner points"})["node_id"]

        def add(statement, under, difficulty="medium", kind="free_response", **content):
            return bank.add_question(definition, {"request_id": str(uuid.uuid4()), "content": {"kind": kind, "statement": statement, "points": 3, **content},
                                                  "classification": {"nodes": [under], "difficulty": difficulty}})
        # The fake writer's first problem, word for word: a reprint once it is sent.
        add("Which value of $x$ solves $2x = 4$?", nodes["3.3"], kind="multiple_choice", choices=["$x = 1$", "$x = 2$", "$x = 3$", "$x = 4$"], correct_choice="b")
        add("Shade the region $x + y \\le 6$ and name its corner points.", topic, "easy")
        retired = add("A retired question about the feasible region.", nodes["3.2"])
        bank.retire(retired["id"], {"request_id": str(uuid.uuid4())})
        add("Define the decision variables for a bakery's two breads.", nodes["3.1"])
        for n in range(drafting_tools.BANK_PER_SECTION + 2):
            add(f"Graph the inequality $y \\ge {n}x$.", nodes["3.2"], "hard")
        before = len(self.host.requests)
        draft = self.draft(notes_sections="3.2,3.3")
        self.frq.process_cloud()
        writes = [payload_of(r["body"]) for r in self.host.requests[before:] if prompt_name(r["body"]) == "quiz_write"]
        sent = writes[0]["bank_questions"]["sections"]
        self.assertEqual([(s["number"], s["available"], len(s["questions"])) for s in sent],
                         [("3.2", drafting_tools.BANK_PER_SECTION + 3, drafting_tools.BANK_PER_SECTION), ("3.3", 1, 1)])
        shown = json.dumps(sent)
        self.assertIn("Graph the inequality", shown)
        self.assertNotIn("retired question", shown)
        self.assertNotIn("bakery", shown)   # 3.1 was not chosen
        self.assertEqual(sent[1]["questions"][0]["correct_choice"], "b")
        # The first answer reprinted the 3.3 question (Version A's numbers are 2 and 4): refused, and asked again with the reason.
        self.assertIn("reprints the question bank's question", json.dumps(writes[1]["faults"]))
        self.assertTrue(writes[1].get("bank_questions"))
        draft = self.frq.quiz_draft(draft["id"])
        self.assertEqual(draft["state"], "failed")   # the fake writes the same quiz again; a real writer changes it
        self.assertIn("reprints the question bank's question", next(r for r in draft["revisions"])["error"])
        self.assertEqual(drafting_tools.chosen_bank(self.frq, definition, ["3.1"])[0]["sections"][0]["questions"][0]["statement"],
                         "Define the decision variables for a bakery's two breads.")

    def test_the_quick_rubric_choices_reach_the_writers_the_drafts_check_and_the_approved_plan(self):
        """GRADING "RUBRIC CHOICES" (requirement, 2026-10-24): the answers given with the request (the form sends them as JSON text) are
        the draft's own; every rubric writer and the draft's rubric check read them, and the approval makes them the plan's."""
        from grading import rubric_choices
        self.assertEqual(self.frq.quiz_draft_options(self.term["definition_id"])["courses"][0]["rubric_choices"], {})
        with self.assertRaises(ValueError):
            self.draft(rubric_choices='{"work": "sometimes"}')
        draft = self.draft(rubric_choices='{"work": "not_needed", "decimals": ""}')
        self.frq.process_cloud()
        writers = [payload_of(r["body"]) for r in self.host.requests if prompt_name(r["body"]) == "quiz_rubric"]
        checks = [payload_of(r["body"]) for r in self.host.requests if prompt_name(r["body"]) == "rubric_quality_versions"]
        self.assertTrue(writers and checks)
        for payload in writers + checks:
            self.assertEqual([c["topic"] for c in payload["assignment_choices"]], ["Shown work"])
        self.assertEqual(self.frq.quiz_draft_options(self.term["definition_id"])["courses"][0]["rubric_choices"], {"work": "not_needed"})
        draft = self.frq.quiz_draft(draft["id"])
        options = self.frq.quiz_approval_options(draft["id"])
        self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": draft["latest_complete"], "confirmation": "approve",
                                                  "key": options["assignment"]["key"], "title": "Chapter 3 Quiz", "sections": [self.sections[501]], "copies": {"A": 1, "B": 1}})
        self.frq.process_cloud()
        done = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(done["status"], "complete", done.get("error"))
        with connect(self.path) as db:
            self.assertEqual(rubric_choices.of_plan(db, done["result"]["plan_id"]), {"work": "not_needed"})
        # A draft asked for without choices sends none: the request it always was.
        before = len(self.host.requests)
        self.draft()
        self.frq.process_cloud()
        self.assertFalse([r for r in self.host.requests[before:] if "assignment_choices" in json.dumps(payload_of(r["body"])) and prompt_name(r["body"]) == "quiz_rubric"])

    def test_graded_for_participation_is_kept_with_the_draft_and_made_the_plans_by_its_approval(self):
        """GRADING "GRADED FOR PARTICIPATION" (requirement, 2026-11-01): the form's box is the draft's own, named only when ticked, and
        the approval records it on the new plan; the model writes the quiz as ever."""
        from grading import participation
        plain = self.draft()
        self.assertNotIn("participation", plain)
        draft = self.draft(participation="1", versions=1)
        self.assertEqual((draft["participation"], draft["versions"]), (True, ["A"]))
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        options = self.frq.quiz_approval_options(draft["id"])
        self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": draft["latest_complete"], "confirmation": "approve",
                                                  "key": options["assignment"]["key"], "title": "Chapter 3 Quiz", "sections": [self.sections[501]], "copies": {"A": 1}})
        self.frq.process_cloud()
        done = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(done["status"], "complete", done.get("error"))
        with connect(self.path) as db:
            self.assertTrue(participation.enabled(db, done["result"]["plan_id"]))
            self.assertEqual(db.execute("SELECT actor FROM frq_participation").fetchall()[0][0], "quiz-approval")

    def test_the_routes_form_carries_the_graded_for_participation_box_into_the_draft(self):
        """The form's box reaches the service through POST /api/quiz-drafts, not only through a direct call."""
        def post(**more):
            form = {"request_id": str(uuid.uuid4()), "term_id": self.term["id"], "title": "Chapter 3 Quiz", "total": "5", "versions": "1", "mode": "positive",
                    "instructions": "Sections 3.1-3.3: one multiple choice, one free response.", **more,
                    "files": (io.BytesIO(b"Linear programming: corner points."), "notes.txt")}
            return self.client.post("/api/quiz-drafts", data=form, headers=HEADERS, content_type="multipart/form-data")
        ticked, plain = post(participation="1"), post()
        self.assertEqual((ticked.status_code, plain.status_code), (201, 201), (ticked.json, plain.json))
        self.assertIs(ticked.json["participation"], True)
        self.assertNotIn("participation", plain.json)

    def test_the_rubric_writers_read_the_courses_kept_memories_as_they_stood_when_the_revision_was_asked_for(self):
        """GRADING "RUBRIC MEMORIES" (2026-10-14): every problem's rubric writer is given the course's kept rules and watches, as
        they stood when the revision was asked for; one only proposed goes nowhere."""
        from frq_fixtures import keep_memory
        definition = self.term["definition_id"]
        keep_memory(self.path, definition)
        keep_memory(self.path, definition, 2, "watch", "Say which near-labels count.", waiting=True)
        draft = self.draft()
        self.frq.process_cloud()
        sent = [payload_of(r["body"]) for r in self.host.requests if prompt_name(r["body"]) == "quiz_rubric"]
        self.assertTrue(sent)
        self.assertEqual({tuple(m["id"] for m in p["standing_rules"]) for p in sent}, {("M1",)})
        with connect(self.path) as db:
            row = dict(db.execute("SELECT * FROM quiz_drafts WHERE id=?", (draft["id"],)).fetchone())
            revision = dict(db.execute("SELECT * FROM quiz_draft_revisions WHERE draft_id=? ORDER BY number LIMIT 1", (draft["id"],)).fetchone())
        self.assertEqual([m["id"] for m in self.frq._quiz_standing(row, revision)], ["M1"])
        self.assertEqual(self.frq._quiz_standing(row, {**revision, "created_at": "2019-01-01T00:00:00+00:00"}), [])  # before it was kept

    def test_the_check_and_every_fix_round_are_told_what_the_instructor_asked_for(self):
        """QUIZZES "THE CHECK KEEPS WHAT WAS ASKED" (requirement, 2026-11-04): a check shown only the format and instructions had the answer
        lines just asked for taken off again, and "What changed" still said every problem had them."""
        issue = {"version": "A", "problem": 1, "kind": "layout", "issue": "Too little room.", "fix": "Make room."}
        checks, fixes = [], []

        def answer(body, host):
            name, payload = prompt_name(body), payload_of(body)
            if name == "quiz_check":
                checks.append(payload)
                return {"issues": [issue], "notes": ""}
            if name == "quiz_write" and payload.get("faults"):
                fixes.append(payload)
                return {**UNTOUCHED, "changes": f"Fix {len(fixes)}."}
            if name == "quiz_write" and payload.get("previous"):
                # Something printed changes, so the check is asked again (a revision that prints the same asks no check).
                room = {"problem": 2, "version": "", "content": None, "page": None, "space": 2 + len(checks), "tag": None, "rubric_feedback": None}
                return {**UNTOUCHED, "changes": "Added the answer lines.", "edits": [room]}
            return self.answer(body, host)
        self.host.script = answer
        draft = self.draft()
        self.frq.process_cloud()
        self.assertNotIn("asked", checks[0])  # a first draft: nothing asked, the requests as they were
        self.assertNotIn("asked", fixes[0])

        def revise(feedback):
            found = self.frq.quiz_draft(draft["id"])
            self.frq.revise_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "based_on": found["latest_complete"], "feedback": feedback})
            self.frq.process_cloud()
            found = self.frq.quiz_draft(draft["id"])
            return next(r for r in found["revisions"] if r["id"] == found["latest_complete"])

        # A fix round after a compile or layout fault is told too, as the fix of the check's findings is.
        real, built = self.frq._quiz_build, []

        def build(*args):
            done, faults = real(*args)
            built.append(1)
            return done, [*faults, "Version A: Problem 1 runs onto page 2."] if len(built) == 1 else faults
        with patch.object(self.frq, "_quiz_build", build):
            second = revise("Add answer lines.")
        self.assertEqual([p["asked"] for p in fixes[1:]], [["Add answer lines."]] * 2)
        self.assertEqual(checks[-1]["asked"], ["Add answer lines."])
        # "What changed" keeps the writer's account and adds each fix's.
        self.assertEqual(second["number"], 2)
        self.assertEqual(second["result"]["changes"], "Added the answer lines.\n\nFix 2.\n\nFix 3.")
        # A later revision is still told what the earlier one asked, oldest first; a revision with no words adds nothing.
        third = revise("Make problem 2 harder.")
        self.assertEqual(third["number"], 3)
        self.assertEqual(checks[-1]["asked"], ["Add answer lines.", "Make problem 2 harder."])
        self.assertEqual(fixes[-1]["asked"], ["Add answer lines.", "Make problem 2 harder."])

    def test_each_stage_is_named_from_its_first_request(self):
        """The rubric check read as "Reading each problem's answer key" until its first answer, and a fix of the check's findings as
        drafting the rubrics (2026-11-04): the page names a stage once its first request is sent."""
        seen, draft = {}, {}
        issue = {"version": "A", "problem": 1, "kind": "layout", "issue": "Too little room.", "fix": "Make room."}

        def answer(body, host):
            name, payload = prompt_name(body), payload_of(body)
            kind = "fix" if name == "quiz_write" and payload.get("faults") else name
            found = self.frq.quiz_draft(draft["id"]) if draft else None
            if found:
                seen.setdefault(kind, next(r for r in found["revisions"] if r["status"] in ("pending", "running"))["progress"])
            if name == "quiz_check":
                return {"issues": [issue], "notes": ""}
            if kind == "fix":
                return UNTOUCHED
            return self.answer(body, host)
        self.host.script = answer
        draft.update(self.draft())
        self.frq.process_cloud()
        self.assertTrue(seen["fix"].get("fixing"), seen["fix"])
        self.assertNotIn("fixing", seen["quiz_rubric"])
        self.assertTrue(seen["answer_key_read"].get("keys"), seen["answer_key_read"])
        check = next(v for k, v in seen.items() if k.startswith("rubric_quality"))
        self.assertTrue(check.get("rubric_check"), check)

    def test_a_fix_round_answers_with_only_its_edits_to_the_previous_quiz(self):
        """The check's finding goes back to the writer, which answers with one edit to the quiz it was given, not every version
        retyped (2026-10-01); the other versions and problems stay exactly as written."""
        issue = {"version": "B", "problem": 2, "kind": "wording", "issue": "Version B does not say which graph.", "fix": "Name the graph."}
        checked = []

        def answer(body, host):
            name, payload = prompt_name(body), payload_of(body)
            if name == "quiz_check":
                checked.append(payload)
                return {"issues": [issue] if len(checked) == 1 else [], "notes": ""}
            if name == "quiz_write" and payload.get("faults"):
                content = copy.deepcopy(next(v for v in payload["previous"]["problems"][1]["versions"] if v["version"] == "B"))
                content["statement"] += " Use the graph at right."
                return {"base": "previous", "sections_line": "", "directions": "", "problems": [], "notes": "", "changes": "Version B names its graph.",
                        "edits": [{"problem": 2, "version": "B", "content": content, "page": None, "space": None, "tag": None, "rubric_feedback": None}]}
            if name == "quiz_write" and payload.get("previous"):
                return UNTOUCHED  # asked for changes: the writer keeps the quiz exactly as the earlier revision left it
            return self.answer(body, host)
        self.host.script = answer
        draft = self.draft()
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        result = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])["result"]
        self.assertTrue(result["check"]["fixed"], result["check"])
        first = quiz_answer({"request": {"versions": ["A", "B"]}})
        quiz = result["quiz"]
        self.assertEqual(quiz["problems"][1]["content"]["B"]["statement"], first["problems"][1]["versions"][1]["statement"] + " Use the graph at right.")
        self.assertEqual(quiz["problems"][1]["content"]["A"]["statement"], first["problems"][1]["versions"][0]["statement"])
        self.assertEqual([p["content"]["B"]["statement"] for p in quiz["problems"][:1]], [first["problems"][0]["versions"][1]["statement"]])
        self.assertEqual(len([r for r in result["rubrics"] if r.get("rubric")]), 2)
        fix = next(payload_of(r["body"]) for r in self.host.requests if prompt_name(r["body"]) == "quiz_write" and payload_of(r["body"]).get("faults"))
        self.assertIn("Version B does not say which graph.", fix["faults"][0])
        # The fixed quiz is not checked again, so the check never SAW problem 2 as it is now printed (QUIZZES "A REVISION", 2026-10-11):
        # only problem 1 is kept as seen, the finding the writer fixed does not stand, and the whole quiz is not as checked.
        seen = result["check"]["seen"]
        self.assertEqual((sorted(seen["problems"]), seen["issues"], seen["whole"]), (["1"], [], ""))
        # So a revision that changes nothing printed still asks about problem 2, and not about problem 1 again.
        before = len(self.host.requests)
        self.frq.revise_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "based_on": result["revision_id"], "feedback": "Keep it as it is."})
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        second = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])
        self.assertEqual((draft["state"], second["based_on"]), ("ready", result["revision_id"]), draft["revisions"][-1].get("error"))
        self.assertEqual(second["result"]["quiz"], quiz)
        self.assertEqual([prompt_name(r["body"]) for r in self.host.requests[before:]], ["quiz_write", "quiz_check"])  # both rubrics are kept
        self.assertEqual((len(checked), checked[1]["unchanged"]), (2, [1]))
        self.assertEqual(second["result"]["check"]["unchanged"], [1])
        self.assertEqual((sorted(second["result"]["check"]["seen"]["problems"]), bool(second["result"]["check"]["seen"]["whole"])), (["1", "2"], True))

    def test_the_name_written_on_an_unnamed_stamped_paper_names_its_copy_when_nothing_argues_against_it(self):
        done = self.approved()
        copies = {(b["version"], c["copy_number"]): c["id"] for b in done["batches"] for c in self.documents.get_batch(b["batch_id"])["copies"]}
        job = self.scan([copies[("A", 1)], copies[("A", 2)], copies[("B", 1)]])
        scans = self.app.extensions["document_scans"]
        texts = {copies[("A", 1)]: "Name: Example, Alice UIN: 001234567", copies[("A", 2)]: "Name: UIN:", copies[("B", 1)]: "Name: Bob Example UIN: 123456789"}
        with connect(self.path) as db:
            by_content = {r["content_sha256"]: r["printed_copy_id"] for r in db.execute(
                "SELECT sp.content_sha256,p.printed_copy_id FROM scan_pages sp JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE sp.message_id=?", (job["message_id"],))}

        blanked = []

        def read(pages, *args, **kwargs):
            blanked.extend(kwargs.get("blank_corners") or [])
            return {"model_id": "fixture", "revision": "r", "pages": [{"source_sha256": sha(page), "reads": [
                {"kind": "band", "rotation": 0, "text": texts[by_content[sha(page)]]}]} for page in pages]}
        overview = scans.review_item(job["message_id"])["overview"]
        self.assertEqual(overview["copy_names"], {"total": 3, "named": 0, "unread": 3, "waiting": 0})
        with patch("document_identity.copy_names.faculty_ocr.recognize_covers", side_effect=read):
            self.assertTrue(scans.copy_names.process_one())
            self.assertFalse(scans.copy_names.process_one())  # read once per page and rule
        # The copies carry the two 7 mm corner codes, so their 8 mm stamp boxes are whited out before the top band is read.
        self.assertEqual(blanked, ["small", "small", "small"])
        states = {c["copy_id"]: c for c in scans.copy_names.summary(job["message_id"])["copies"]}
        # Each match is remembered per reading and roster: the view drawn again on the next turn or refresh matches nothing anew.
        with patch("bubble_sheets.matching.match", side_effect=AssertionError("matched again")):
            self.assertEqual(scans.copy_names.summary(job["message_id"])["copies"], list(states.values()))
        self.assertEqual(states[copies[("A", 1)]]["state"], "named")  # an exact roster UIN names it with no click
        self.assertEqual(states[copies[("B", 1)]]["state"], "named")
        self.assertEqual(states[copies[("A", 2)]]["state"], "needs_review")  # nothing legible: the instructor names it
        self.assertEqual(states[copies[("A", 1)]]["student"]["name"], "Example, Alice")
        with connect(self.path) as db:
            events = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM document_events WHERE kind='copy_assignment' AND actor='automatic'")]
        self.assertEqual(len(events), 2)
        item = scans.review_item(job["message_id"])
        self.assertEqual(item["overview"]["copy_names"], {"total": 3, "named": 2, "unread": 0, "waiting": 1})
        step = next(s for s in item["overview"]["steps"] if s["tool"] == "copy_names" and s["kind"] == "todo")
        self.assertIn("Say whose 1 stamped paper is", step["text"])
        self.assertIsNotNone(item["copy_names"])
        students = {s["name"]: s["enrollment_id"] for s in states[copies[("A", 2)]]["students"]}
        after = scans.copy_names.assign(job["message_id"], {"printed_copy_id": copies[("A", 2)], "enrollment_id": students["Example, Bob"]})
        self.assertEqual({c["state"] for c in after["copies"]}, {"named"})

    def test_papers_photocopied_from_one_copy_each_get_their_own_copy_and_are_named_from_the_paper(self):
        self.photocopied_papers(split_first=False)

    def test_photocopied_papers_split_before_their_names_are_read_are_named_the_same(self):
        # QUIZZES "NAMES ON UNNAMED STAMPED COPIES" > AFTER THE INSPECTION (2026-11-14): once a scan is inspected the worker's rotation
        # usually reaches the photocopies before the name reader, so the papers are split first; each copy is named from its own paper.
        self.photocopied_papers(split_first=True)

    def photocopied_papers(self, split_first):
        """QUIZZES "PHOTOCOPIED STAMPS" (requirement, 2026-10-16): one printed copy photocopied for the class puts its stamps on every
        paper. The name reader names no student for that copy; once the scan is inspected each paper after the first moves, page for
        page, to an unused copy of the same job, and the readings name every copy."""
        draft = self.draft()
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        options = self.frq.quiz_approval_options(draft["id"])
        self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": draft["latest_complete"], "confirmation": "approve",
                                                  "key": options["assignment"]["key"], "title": "Chapter 3 Quiz",
                                                  "sections": [self.sections[501]], "copies": {"A": 3, "B": 1}})
        self.frq.process_cloud()
        done = self.frq.quiz_draft(draft["id"])["approved"]
        while self.documents.build_one():
            pass
        copies = {(b["version"], c["copy_number"]): c["id"] for b in done["result"]["batches"] for c in self.documents.get_batch(b["batch_id"])["copies"]}
        with connect(self.path) as db:
            stamps = {(r["printed_copy_id"], r["page_number"]): (r["id"], r["token"]) for r in db.execute("SELECT * FROM page_stamps")}
        sides = sorted(n for c, n in stamps if c == copies[("A", 1)])
        names = ["Name: Example, Alice UIN: 001234567", "Name: Bob Example UIN: 123456789", "Name: UIN:"]
        inbox, scans = self.app.extensions["inbox"], self.app.extensions["document_scans"]
        job = inbox.submit({"action": "scan.receive"}, [{"filename": "quiz.pdf", "content": b"%PDF-fixture" + uuid.uuid4().bytes}], source="email",
                           sender="scanner", original=b"fixture mail", idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        job = inbox.get(job["id"])
        original = next(a for a in job["artifacts"] if a["role"] == "input")
        texts, index = {}, 0
        for paper, name in enumerate(names):
            for side in sides:
                index += 1
                content = f"paper-{paper}-side-{side}".encode()
                texts[sha(content)] = name
                scans._retain_page(job, original, {"index": index, "pdf": content, "png": b"png", "decoder": {}, "blank": False,
                                                   "candidates": [{"payload": stamps[(copies[("A", 1)], side)][1]}]})
            index += 1  # a blank back between papers
            scans._retain_page(job, original, {"index": index, "pdf": f"blank-{paper}".encode(), "png": b"png", "decoder": {}, "blank": True, "candidates": []})
        message = job["message_id"]

        def read(pages, *args, **kwargs):
            return {"model_id": "fixture", "revision": "r", "pages": [{"source_sha256": sha(page), "reads": [
                {"kind": "band", "rotation": 0, "text": texts[sha(page)]}]} for page in pages]}
        with patch("document_identity.copy_names.faculty_ocr.recognize_covers", side_effect=read):
            # Nothing moves while the scan is still being inspected: no name is read (QUIZZES "NAMES ON UNNAMED STAMPED COPIES" >
            # AFTER THE INSPECTION) and no paper is split.
            self.assertFalse(scans.copy_names.process_one())
            self.assertFalse(scans.photocopies.process_one())
            with connect(self.path) as db:
                db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (message,))
            if split_first:
                self.assertTrue(scans.photocopies.process_one())
                self.assertTrue(scans.copy_names.process_one())
            else:
                self.assertTrue(scans.copy_names.process_one())
                # Three papers on one copy: no student is named for it.
                self.assertEqual({c["state"] for c in scans.copy_names.summary(message)["copies"]}, {"conflict"})
                self.assertIsNone(self.documents.get_batch(done["result"]["batches"][0]["batch_id"])["copies"][0]["enrollment_id"])
                self.assertTrue(scans.photocopies.process_one())
        self.assertFalse(scans.copy_names.process_one())  # every first page read once
        self.assertFalse(scans.photocopies.process_one())  # nothing left to split
        with connect(self.path) as db:
            pages = [dict(r) for r in db.execute("""SELECT sp.source_page_index,sp.status,sp.review_state,p.printed_copy_id,p.page_number FROM scan_pages sp
                LEFT JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE sp.message_id=? ORDER BY sp.source_page_index""", (message,))]
            event = json.loads(db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                WHERE j.message_id=? AND e.kind='photocopied_papers'""", (message,)).fetchone()[0])
        papers = [[p for p in pages if p["printed_copy_id"]][i * len(sides):(i + 1) * len(sides)] for i in range(3)]
        for paper, number in zip(papers, (1, 2, 3)):
            self.assertEqual({(p["printed_copy_id"], p["page_number"]) for p in paper}, {(copies[("A", number)], side) for side in sides})
            self.assertEqual({p["review_state"] for p in paper}, {"candidate"})
        self.assertEqual({p["status"] for p in papers[0]}, {"resolved"})  # the first paper keeps the copy it was printed as
        self.assertEqual({p["status"] for p in papers[1] + papers[2]}, {"manual"})
        self.assertEqual([(m["from_copy_id"], m["to_copy_id"]) for m in event["moved"]], [(copies[("A", 1)], copies[("A", 2)]), (copies[("A", 1)], copies[("A", 3)])])
        self.assertEqual(event["held"], [])
        # The readings already kept name each copy from its own paper; the unreadable one waits for the instructor.
        states = {c["copy_id"]: c for c in scans.copy_names.summary(message)["copies"]}
        self.assertEqual((states[copies[("A", 1)]]["state"], states[copies[("A", 1)]]["student"]["name"]), ("named", "Example, Alice"))
        self.assertEqual((states[copies[("A", 2)]]["state"], states[copies[("A", 2)]]["student"]["name"]), ("named", "Example, Bob"))
        self.assertEqual(states[copies[("A", 3)]]["state"], "needs_review")

    def test_a_stamp_back_on_two_papers_is_reported_only_where_each_student_must_use_their_own_copy(self):
        """QUIZZES "SHARED STAMPS" (requirement, 2026-11-04): the papers move and are graded as PHOTOCOPIED STAMPS says; the box in the
        approval adds a list naming whose they are. Unticked, the assignment's answer has no such key."""
        def approved(key, own):
            draft = self.draft()
            self.frq.process_cloud()
            draft = self.frq.quiz_draft(draft["id"])
            self.frq.quiz_approval_options(draft["id"])
            self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": draft["latest_complete"], "confirmation": "approve",
                                                      "key": key, "title": key, "sections": [self.sections[501]], "copies": {"A": 3, "B": 1},
                                                      **({"own_copies": True} if own else {})})
            self.frq.process_cloud()
            return self.frq.quiz_draft(draft["id"])["approved"]
        done = approved("take-home", True)
        self.assertTrue(done["input"]["own_copies"])
        plain = approved("ordinary", False)
        self.assertNotIn("own_copies", plain["input"])
        while self.documents.build_one():
            pass
        term, assignment = done["term_id"], done["result"]["assignment_id"]
        read = lambda which=assignment: self.client.get(f"/api/assignments/{which}/{term}").get_json()
        self.assertEqual(read()["shared_stamps"], [])  # ticked: the list is there, and empty
        self.assertNotIn("shared_stamps", read(plain["result"]["assignment_id"]))
        self.assertNotIn("shared_stamps", [card["key"] for card in self.client.get("/api/workspace/overview").get_json()["attention"]])

        copies = {(b["version"], c["copy_number"]): c["id"] for b in done["result"]["batches"] for c in self.documents.get_batch(b["batch_id"])["copies"]}
        with connect(self.path) as db:
            stamps = {(r["printed_copy_id"], r["page_number"]): r["token"] for r in db.execute("SELECT * FROM page_stamps")}
        sides = sorted(n for c, n in stamps if c == copies[("A", 1)])
        inbox, scans = self.app.extensions["inbox"], self.app.extensions["document_scans"]
        job = inbox.submit({"action": "scan.receive"}, [{"filename": "quiz.pdf", "content": b"%PDF-fixture" + uuid.uuid4().bytes}], source="email",
                           sender="scanner", original=b"fixture mail", idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        job = inbox.get(job["id"])
        original = next(a for a in job["artifacts"] if a["role"] == "input")
        names, texts, index = ["Name: Example, Alice UIN: 001234567", "Name: Bob Example UIN: 123456789"], {}, 0
        for paper, name in enumerate(names):
            for side in sides:
                index += 1
                content = f"shared-{paper}-side-{side}".encode()
                texts[sha(content)] = name
                scans._retain_page(job, original, {"index": index, "pdf": content, "png": b"png", "decoder": {}, "blank": False,
                                                   "candidates": [{"payload": stamps[(copies[("A", 1)], side)]}]})
        message = job["message_id"]

        def recognized(pages, *args, **kwargs):
            return {"model_id": "fixture", "revision": "r", "pages": [{"source_sha256": sha(page), "reads": [
                {"kind": "band", "rotation": 0, "text": texts[sha(page)]}]} for page in pages]}
        with patch("document_identity.copy_names.faculty_ocr.recognize_covers", side_effect=recognized):
            with connect(self.path) as db:
                db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (message,))
            self.assertTrue(scans.copy_names.process_one())
            self.assertTrue(scans.photocopies.process_one())
        found = read()["shared_stamps"]
        self.assertEqual((len(found), found[0]["copy_number"], found[0]["version"]), (1, 1, "A"))
        self.assertEqual(sorted(p["student"] for p in found[0]["papers"]), ["Example, Alice", "Example, Bob"])
        self.assertEqual({p["job_id"] for p in found[0]["papers"]}, {job["id"]})
        self.assertEqual(sorted(p["copy_number"] for p in found[0]["papers"]), [1, 2])
        self.assertNotIn("shared_stamps", read(plain["result"]["assignment_id"]))
        card = next(c for c in self.client.get("/api/workspace/overview").get_json()["attention"] if c["key"] == "shared_stamps")
        self.assertEqual(card["count"], 1)
        self.assertIn(assignment, card["href"])
        # Certified, the card goes and the list stays.
        with patch("certified_archive.certified", return_value={(assignment, term): {}}):
            self.assertNotIn("shared_stamps", [c["key"] for c in self.client.get("/api/workspace/overview").get_json()["attention"]])
        self.assertEqual(len(read()["shared_stamps"]), 1)
        # Material deleted in the Inbox never counts (INBOX PRESENT): the scan gone, nothing is shared.
        with connect(self.path) as db:
            db.execute("INSERT INTO inbox_deletions(message_id,actor,created_at) VALUES(?,?,?)", (message, "workspace", "2026-11-04T12:00:00+00:00"))
        self.assertEqual(read()["shared_stamps"], [])

    def test_a_photocopied_copy_already_named_by_a_reading_is_cleared_and_named_again_from_its_own_paper(self):
        """A copy the name reader named before its photocopies were seen loses that automatic name; one the instructor gave stays."""
        done = self.approved()
        copies = {(b["version"], c["copy_number"]): c["id"] for b in done["batches"] for c in self.documents.get_batch(b["batch_id"])["copies"]}
        scans = self.app.extensions["document_scans"]
        students = {s["name"]: s["enrollment_id"] for s in self.documents.section_students(self.sections[501])["students"]}
        self.documents.assign_copy(copies[("A", 1)], students["Example, Bob"], "Read from an earlier paper.", actor="automatic")
        job = self.scan([copies[("A", 1)], copies[("A", 1)]])
        texts = ["Name: Example, Alice UIN: 001234567", "Name: Bob Example UIN: 123456789"]
        with connect(self.path) as db:
            order = [r[0] for r in db.execute("SELECT content_sha256 FROM scan_pages WHERE message_id=? ORDER BY source_page_index", (job["message_id"],))]

        def read(pages, *args, **kwargs):
            return {"model_id": "fixture", "revision": "r", "pages": [{"source_sha256": sha(page), "reads": [
                {"kind": "band", "rotation": 0, "text": texts[order.index(sha(page))]}]} for page in pages]}
        with patch("document_identity.copy_names.faculty_ocr.recognize_covers", side_effect=read):
            self.assertTrue(scans.photocopies.process_one())
            while scans.copy_names.process_one():
                pass
        named = {c["copy_number"]: c["enrollment_id"] for c in self.documents.get_batch(done["batches"][0]["batch_id"])["copies"]}
        self.assertEqual(named, {1: students["Example, Alice"], 2: students["Example, Bob"]})
        with connect(self.path) as db:
            actors = [r[0] for r in db.execute("SELECT actor FROM document_events WHERE kind='copy_assignment' AND copy_id=? ORDER BY rowid", (copies[("A", 1)],))]
        self.assertEqual(actors, ["automatic", "automatic", "automatic"])  # named, cleared, named again from its own paper

    def test_a_printed_versions_stamp_decides_the_version_where_its_document_holds_one_letter(self):
        with connect(self.path) as db:
            db.execute("CREATE TEMP TABLE IF NOT EXISTS unused(x)")
        # Unit-level: the rule reads the paper's printed document and the plan's questions only.
        from grading.exam_versions import ExamVersionsMixin

        class Rows:
            def __init__(self, document, placed):
                self.document, self.placed = document, placed

            def execute(self, sql, args):
                class Result:
                    def __init__(self, rows):
                        self.rows = rows

                    def fetchone(self):
                        return self.rows[0] if self.rows else None

                    def fetchall(self):
                        return self.rows
                return Result([(self.document,)] if "assembly_revisions" in sql else self.placed)
        own = [("A", "doc-a"), ("B", "doc-b")]
        self.assertEqual(ExamVersionsMixin.printed_document_version(Rows("doc-b", own), {"id": "p"}, "r"), "B")
        shared = [("A", "labels"), ("B", "labels")]  # one label sheet for every version: the seating chart's rule, unchanged
        self.assertIsNone(ExamVersionsMixin.printed_document_version(Rows("labels", shared), {"id": "p"}, "r"))
        self.assertIsNone(ExamVersionsMixin.printed_document_version(Rows("doc-a", [("", "doc-a")]), {"id": "p"}, "r"))

    def test_nothing_printed_names_the_version(self):
        # 
        for bad in ("Version A of the quiz", r"On Version~B, find $x$.", r"Version \textbf{C}", "versions A and B"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "nothing printed may say which version"):
                quiz_latex.fragment(bad, "Statement")
        for fine in ("A newer version of the model costs \\$5.", "Section 3.2", "Find the inverse of $A$."):
            self.assertEqual(quiz_latex.fragment(fine, "Statement"), fine)
        quiz = quiz_answer({"request": {"versions": ["A"]}})
        quiz["sections_line"] = "Sections 3.1–3.3 · Version A"
        with self.assertRaisesRegex(ValueError, "The sections line says"):
            quiz_latex.clean(quiz, ["A"], title_line="MATH 140 Fall 2026 Chapter 2 Quiz", total=5)
        # Faculty's own header and footer print the sections, the points and "Page 1 of 1", never the version.
        style = (Path(quiz_latex.__file__).parent / "templates" / "facultyquiz.sty").read_text()
        self.assertNotIn("Version #3", style)
        self.assertNotIn("Version \\quiz@version", style)

    def test_fragments_cannot_change_the_page_or_read_files(self):
        for bad in (r"\input{/etc/passwd}", r"\newpage", r"\vspace*{-5cm}", r"\setlength{\textheight}{30cm}", r"50% off", r"\def\x{1}", "{unbalanced"):
            with self.assertRaises(ValueError, msg=bad):
                quiz_latex.fragment(bad, "Statement")
        self.assertEqual(quiz_latex.fragment(r"A table as wide as \textwidth: \begin{tabularx}{\textwidth}{X}a\end{tabularx} costs \$5 and 5\%.", "S")[:7], "A table")

    def test_a_fragment_tells_an_escaped_percent_or_brace_from_a_line_break_before_one(self):
        # A TeX line break (two backslashes) followed by a percent starts a comment; one before a brace leaves the brace live.
        for bad in ("a\\\\%b", "a\\\\\\\\%b"):
            with self.assertRaisesRegex(ValueError, "comment character", msg=bad):
                quiz_latex.fragment(bad, "S")
        for good in (r"a\%b", "a\\\\\\%b", r"x \\{y}", r"{x \\}", r"\{a"):
            self.assertEqual(quiz_latex.fragment(good, "S"), good)
        for bad in ("{", "a\\\\\\{b}"):
            with self.assertRaisesRegex(ValueError, "unbalanced braces", msg=bad):
                quiz_latex.fragment(bad, "S")


class QuizAnswers(unittest.TestCase):
    """What an answer that gives only its edits stands for (`quiz_drafts.resolved`), and what it may not do."""

    def setUp(self):
        self.previous = quiz_answer({"request": {"versions": ["A", "B"]}})

    def edit(self, **change):
        return {"problem": 2, "version": "", "content": None, "page": None, "space": None, "tag": None, "rubric_feedback": None, **change}

    def answer(self, base, edits, **fields):
        return {"base": base, "sections_line": "", "directions": "", "problems": [], "edits": edits, **fields}

    def test_edits_replace_one_versions_content_and_set_a_problems_own_fields_leaving_the_rest(self):
        content = {**copy.deepcopy(self.previous["problems"][1]["versions"][0]), "statement": "A new statement."}
        quiz = resolved(self.answer("previous", [self.edit(version="A", content=content), self.edit(page=2, rubric_feedback="Two points for the setup.")],
                                    directions="New directions."), self.previous, None, ["A", "B"])
        self.assertEqual(quiz["problems"][1]["versions"][0]["statement"], "A new statement.")
        self.assertEqual(quiz["problems"][1]["versions"][1], self.previous["problems"][1]["versions"][1])
        self.assertEqual((quiz["problems"][1]["page"], quiz["problems"][1]["rubric_feedback"]), (2, "Two points for the setup."))
        self.assertEqual((quiz["sections_line"], quiz["directions"]), (self.previous["sections_line"], "New directions."))
        self.assertEqual(quiz["problems"][0], self.previous["problems"][0])
        self.assertEqual(self.previous["problems"][1]["page"], 1)  # the base itself is never changed
        quiz_latex.clean(quiz, ["A", "B"], title_line="MATH 140 Fall 2026 Chapter 3 Quiz", total=5)
        # A whole quiz is still an answer, and so is one recorded before answers had a base.
        whole = {k: self.previous[k] for k in ("sections_line", "directions", "problems")}
        self.assertEqual(resolved(self.previous, None, None, ["A", "B"]), whole)
        self.assertEqual(resolved({**self.previous, "base": "none", "edits": []}, None, None, ["A", "B"]), whole)

    def test_an_answer_that_cannot_stand_for_one_quiz_is_refused(self):
        content = copy.deepcopy(self.previous["problems"][1]["versions"][0])
        for answer, previous, compiled, said in (
                (self.answer("previous", []), None, None, "no previous quiz"),
                (self.answer("compiled", []), self.previous, None, "Nothing was compiled"),
                (self.answer("previous", [], problems=self.previous["problems"]), self.previous, None, "problems must be []"),
                ({**self.previous, "base": "none", "edits": [self.edit(page=2)]}, None, None, "no edits"),
                (self.answer("previous", [self.edit(problem=3, page=2)]), self.previous, None, "from 1 to 2"),
                (self.answer("previous", [self.edit(version="C", content=content)]), self.previous, None, "one of A, B"),
                (self.answer("previous", [self.edit(version="B")]), self.previous, None, "gives no content"),
                (self.answer("draft", []), self.previous, None, "base must be")):
            with self.assertRaises(ValueError, msg=said) as refused:
                resolved(answer, previous, compiled, ["A", "B"])
            self.assertIn(said, str(refused.exception))


class ScrappedDrafts(unittest.TestCase):
    """A draft the instructor does not want is scrapped (QUIZZES "SCRAPPING A DRAFT", 2026-10-01): it leaves the list, a revision still
    running stops, and nothing about it is revised or approved again; an approved quiz is never scrapped."""
    setUp, answer, draft, approved = QuizDrafts.setUp, QuizDrafts.answer, QuizDrafts.draft, QuizDrafts.approved

    def scrap(self, draft_id, body):
        return self.client.post(f"/api/quiz-drafts/{draft_id}/scrap", json=body, headers=HEADERS)

    def test_a_draft_never_approved_is_scrapped_its_running_revision_stops_and_it_leaves_the_list(self):
        draft = self.draft()
        self.assertEqual(self.scrap(draft["id"], {}).status_code, 400)  # the confirmation is the server's to check
        scrapped = self.scrap(draft["id"], {"confirmation": "scrap"})
        self.assertEqual(scrapped.status_code, 200, scrapped.json)
        self.assertEqual((scrapped.json["state"], scrapped.json["scrapped"]["actor"]), ("scrapped", "workspace"))
        self.assertEqual([(r["status"], r["error"]) for r in scrapped.json["revisions"]], [("cancelled", "Stopped: the draft was scrapped.")])
        self.frq.process_cloud()
        self.assertEqual(self.host.requests, [])  # the stopped revision sends nothing
        self.assertEqual(self.client.get("/api/quiz-drafts", headers=HEADERS).json["drafts"], [])
        page = self.client.get(f"/api/quiz-drafts/{draft['id']}", headers=HEADERS)
        self.assertEqual((page.status_code, page.json["state"]), (200, "scrapped"))  # a tab opened before still reads it
        with self.assertRaisesRegex(Conflict, "scrapped"):
            self.frq.revise_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "feedback": "Again."})
        with self.assertRaisesRegex(Conflict, "scrapped"):
            self.frq.quiz_approval_options(draft["id"])
        with self.assertRaisesRegex(Conflict, "scrapped"):
            self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "confirmation": "approve"})
        self.assertEqual(self.scrap(draft["id"], {"confirmation": "scrap"}).json["scrapped"], scrapped.json["scrapped"])  # once only
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM document_events WHERE kind='quiz_draft_scrapped'").fetchone()[0], 1)
            with self.assertRaisesRegex(Exception, "stays scrapped"):
                db.execute("DELETE FROM quiz_draft_scraps")
        # Another draft of the course is listed as before.
        kept = self.draft()
        self.assertEqual([d["id"] for d in self.client.get("/api/quiz-drafts", headers=HEADERS).json["drafts"]], [kept["id"]])
        self.assertNotIn("scrapped", self.frq.quiz_draft(kept["id"]))

    def test_an_approved_quiz_is_never_scrapped(self):
        self.approved()
        with connect(self.path) as db:
            draft_id = db.execute("SELECT id FROM quiz_drafts").fetchone()[0]
        refused = self.scrap(draft_id, {"confirmation": "scrap"})
        self.assertEqual(refused.status_code, 409, refused.json)
        self.assertIn("approved", refused.json["error"])
        self.assertEqual(self.frq.quiz_draft(draft_id)["state"], "approved")


class UnchangedProblemsAreNotCheckedAgain(unittest.TestCase):
    """QUIZZES "A REVISION" (instructor's decision, 2026-10-11: the quiz check skips problems unchanged since the last revision). A
    revision keeps the key of each problem its check SAW, with what the check found about it that still stands; the next revision
    asks the check only about the problems printed otherwise, carries the findings about the others, and sends no check when nothing
    printed changed. What the check judges, and a first revision's request, are as they were."""
    ISSUE = {"version": "A", "problem": 1, "kind": "wording", "issue": "Version A does not say what to circle.", "fix": "Say to circle one option."}
    draft = QuizDrafts.draft

    def setUp(self):
        QuizDrafts.setUp(self)
        # What the scripted model does next: the quiz it writes, how it answers the check's findings, and each check's findings in turn.
        self.write, self.fix, self.found, self.checks = quiz_answer, lambda payload: UNTOUCHED, [], []

    def answer(self, body, host):
        name, payload = prompt_name(body), payload_of(body)
        if name == "quiz_write":
            return self.fix(payload) if payload.get("faults") else self.write(payload)
        if name == "quiz_check":
            self.checks.append(body)
            return {"issues": self.found.pop(0) if self.found else [], "notes": ""}
        if name == "quiz_rubric":
            return rubric_answer(payload)
        if name == "answer_key_read":
            return key_answer(payload)
        return QuizDrafts.answer(self, body, host)  # the rubric check a revision ends with (2026-10-11); anything else is not scripted

    def revision(self, draft_id, based_on=None):
        """Carry out the draft's first revision, or one asked of `based_on`: the finished revision and the requests the model got for
        it, up to its last step: the rubric check it ends with (2026-10-11) has its own tests (RubricQuestionsAtDraftReview)."""
        before = len(self.host.requests)
        if based_on:
            self.frq.revise_quiz_draft(draft_id, {"request_id": str(uuid.uuid4()), "based_on": based_on, "feedback": "Change what I said."})
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft_id)
        found = next((r for r in draft["revisions"] if r["id"] == draft["latest_complete"]), {})
        self.assertEqual((draft["state"], found.get("based_on")), ("ready", based_on), draft["revisions"][-1].get("error"))
        return found, [r["body"] for r in self.host.requests[before:] if not prompt_name(r["body"]).startswith("rubric_quality")]

    @staticmethod
    def two_pages(payload, part_b=None):
        """The fixture's quiz with problem 1 alone on page 1 and problem 2 on page 2; `part_b` asks problem 2's part (b) otherwise."""
        quiz = quiz_answer(payload)
        quiz["problems"][1]["page"] = 2
        for version in quiz["problems"][1]["versions"] if part_b else ():
            version["parts"][1]["text"], version["final_answers"] = part_b, [{"part": "b", "answer": "$y = 6$"}]
        return quiz

    def test_a_later_check_is_asked_only_about_what_changed_and_not_at_all_when_nothing_did(self):
        shown = lambda body: [(p["version"], p["page"]) for p in payload_of(body)["pictures"]]
        faults = lambda bodies: [payload_of(b)["faults"] for b in bodies if prompt_name(b) == "quiz_write" and payload_of(b).get("faults")]
        checks = lambda revision: [w for w in revision["result"]["warnings"] if w.startswith("Check")]
        self.write, self.found = self.two_pages, [[self.ISSUE]]
        draft = self.draft()
        first, asked = self.revision(draft["id"])
        # A first revision is asked as it always was: every page of every version, and nothing named unchanged.
        self.assertEqual([prompt_name(b) for b in asked][:3], ["quiz_write", "quiz_check", "quiz_write"])
        self.assertEqual(sorted(payload_of(asked[1])), ["pictures", "quiz", "request"])
        self.assertEqual((shown(asked[1]), len(images_of(asked[1]))), ([("A", 1), ("A", 2), ("B", 1), ("B", 2)], 4))
        # The finding went back to the writer once; the writer left problem 1 as it was, which a fix round still calls fixed (as before).
        self.assertEqual((first["result"]["check"]["issues"], first["result"]["check"]["fixed"], checks(first)), ([self.ISSUE], True, []))
        self.assertNotIn("unchanged", first["result"]["check"])
        # Both problems are printed as the check saw them, so both are kept as seen, and so is the finding: problem 1 is as it was found.
        seen = first["result"]["check"]["seen"]
        self.assertEqual((sorted(seen["problems"]), seen["issues"], bool(seen["whole"])), (["1", "2"], [self.ISSUE], True))

        # Problem 2 is asked otherwise. The check gets the whole quiz's data, problem 1 named unchanged, and only the pages problem 2
        # is on; the finding about problem 1 is carried as it was and goes back to the writer with this check's (it has none).
        self.write = lambda payload: self.two_pages(payload, "Find $y$ when $x = 8$.")
        second, asked = self.revision(draft["id"], first["id"])
        # (Problem 2's rubric is drafted again, so its key is read again on both versions: 2026-10-11, "Carry the key through".)
        self.assertEqual([prompt_name(b) for b in asked], ["quiz_write", "quiz_check", "quiz_write", "quiz_rubric", "answer_key_read", "answer_key_read"])
        check = payload_of(asked[1])
        self.assertEqual((check["unchanged"], shown(asked[1]), len(images_of(asked[1]))), ([1], [("A", 2), ("B", 2)], 2))
        self.assertEqual([p["page"] for p in check["quiz"]["problems"]], [1, 2])
        self.assertEqual(check["request"], payload_of(self.checks[0])["request"])
        self.assertEqual(len(faults(asked)), 1)
        self.assertIn("Version A does not say what to circle.", faults(asked)[0][0])
        self.assertEqual((second["result"]["check"]["issues"], second["result"]["check"]["unchanged"]), ([self.ISSUE], [1]))
        seen = second["result"]["check"]["seen"]
        self.assertEqual((sorted(seen["problems"]), seen["issues"]), (["1", "2"], [self.ISSUE]))
        self.assertEqual(seen["problems"]["1"], first["result"]["check"]["seen"]["problems"]["1"])
        self.assertNotEqual(seen["problems"]["2"], first["result"]["check"]["seen"]["problems"]["2"])

        # Nothing printed changes: the check is not asked at all. What it found still stands and goes to the writer; this time the
        # writer's fix cannot be laid out, so the quiz stays as it was and the instructor is shown the finding.
        def overflowing(payload):
            content = copy.deepcopy(next(v for v in payload["previous"]["problems"][0]["versions"] if v["version"] == "A"))
            content["statement"] += r"\vspace{30cm}"
            return {**UNTOUCHED, "edits": [{"problem": 1, "version": "A", "content": content, "page": None, "space": None, "tag": None, "rubric_feedback": None}]}
        self.fix = overflowing
        third, asked = self.revision(draft["id"], second["id"])
        self.assertEqual([prompt_name(b) for b in asked], ["quiz_write"] * 4)  # the revision, the fix and its two rounds: no check, no rubric, no key
        self.assertEqual(len(self.checks), 2)
        self.assertIn("Version A does not say what to circle.", faults(asked)[0][0])
        self.assertEqual(third["result"]["quiz"], second["result"]["quiz"])
        self.assertEqual((third["result"]["check"]["issues"], third["result"]["check"]["fixed"], third["result"]["check"]["unchanged"]), ([self.ISSUE], False, [1, 2]))
        self.assertEqual(checks(third), ["Check, Version A, problem 1: Version A does not say what to circle."])
        self.assertEqual(third["result"]["check"]["seen"], second["result"]["check"]["seen"])
        self.assertEqual([r["carried_from"] for r in third["result"]["rubrics"]], [second["id"], second["id"]])  # the rubric step is as it was

    def test_a_problem_only_respaced_or_moved_is_checked_again_though_its_rubric_is_kept(self):
        """`signature` leaves spacing out, which a rubric does not depend on; the check judges the layout, so its key does not."""
        box = lambda revision, number: revision["result"]["versions"]["A"]["layout"]["problems"][str(number)]
        kept = lambda revision: revision["result"]["check"]["seen"]["problems"]
        draft = self.draft()
        first, _ = self.revision(draft["id"])

        # More room in problem 2's scaffold on every version: the same words, ending lower on the page. Problem 1, above it, is as it was.
        def roomier(payload):
            quiz = quiz_answer(payload)
            for version in quiz["problems"][1]["versions"]:
                version["scaffold"] = version["scaffold"].replace("20mm", "35mm")
            return quiz
        self.write = roomier
        second, asked = self.revision(draft["id"], first["id"])
        self.assertEqual([prompt_name(b) for b in asked], ["quiz_write", "quiz_check"])  # neither rubric is drafted again
        self.assertEqual([signature(p, "positive") for p in second["result"]["quiz"]["problems"]], [signature(p, "positive") for p in first["result"]["quiz"]["problems"]])
        self.assertEqual(payload_of(asked[1])["unchanged"], [1])
        self.assertEqual(box(second, 1), box(first, 1))
        self.assertGreater(box(second, 2)["bottom"], box(first, 2)["bottom"])
        self.assertEqual((kept(second)["1"] == kept(first)["1"], kept(second)["2"] == kept(first)["2"]), (True, False))

        # Space under problem 1's statement: problem 2 is written exactly as before and only printed lower. Both are checked again.
        def lower(payload):
            quiz = roomier(payload)
            for version in quiz["problems"][0]["versions"]:
                version["statement"] += r"\vspace{12mm}"
            return quiz
        self.write = lower
        third, asked = self.revision(draft["id"], second["id"])
        self.assertEqual([prompt_name(b) for b in asked], ["quiz_write", "quiz_check"])
        self.assertEqual(third["result"]["quiz"]["problems"][1], second["result"]["quiz"]["problems"][1])
        self.assertGreater(box(third, 2)["top"], box(second, 2)["top"])
        self.assertNotIn("unchanged", payload_of(asked[1]))
        self.assertEqual(len(images_of(asked[1])), 2)
        self.assertNotIn("unchanged", third["result"]["check"])
        self.assertFalse(set(kept(third).values()) & set(kept(second).values()))

    def test_a_finding_about_the_whole_quiz_has_every_page_sent_again(self):
        """Nothing says which page a finding about the whole quiz (problem 0) was about, so the next check sees every page and makes
        that judgment again; the unchanged problem is still not worked again."""
        whole = {"version": "all", "problem": 0, "kind": "layout", "issue": "Page 1 is badly unbalanced.", "fix": "Move problem 2 up."}
        self.write, self.found = self.two_pages, [[whole, self.ISSUE]]
        draft = self.draft()
        first, _ = self.revision(draft["id"])
        self.assertEqual(first["result"]["check"]["seen"]["issues"], [whole, self.ISSUE])
        self.write = lambda payload: self.two_pages(payload, "Find $y$ when $x = 8$.")
        second, asked = self.revision(draft["id"], first["id"])
        check = payload_of(asked[1])
        self.assertEqual((check["unchanged"], len(images_of(asked[1]))), ([1], 4))
        # This check found nothing about the whole quiz, so that finding is gone; the one about the unchanged problem is carried.
        self.assertEqual(second["result"]["check"]["issues"], [self.ISSUE])


class AnswerKeysAreCarriedThrough(unittest.TestCase):
    """(QUIZZES "A REVISION" and "APPROVAL"; GRADING, the answer key). Measured that
    day: the drafting model worked each answer out three times, yet a question was created without a key, a deducting rubric gave
    none to read (0 of 16 and 0 of 15 questions), and every key came from a reading asked for afterwards that solved the problem
    again without the solution; the rubric check queued at approval ran before any key existed. So each problem's key is read ONCE
    while the quiz is drafted, by the same reader contract with the worked solution, the writer's answers and the rubric lines in
    view; the draft shows it; and an approval sent from the page that showed it (`save_keys`) saves it with the question."""
    UNREADABLE = {"parts": [], "unreadable": "The graph does not give the line."}

    def setUp(self):
        QuizDrafts.setUp(self)
        # What the scripted reader answers for one problem and version instead of `key_answer`, and every request it was sent.
        self.keys, self.read = {}, []

    @staticmethod
    def about(payload):
        """(printed number, version) a key request is about: the fixture's Version A prints 2x = 4 and "when x = 2", Version B 3 and 3."""
        return payload["question"]["printed_number"], "A" if "$2x = 4$" in payload["problem_text"] or "when $x = 2$" in payload["problem_text"] else "B"

    def answer(self, body, host):
        name, payload = prompt_name(body), payload_of(body)
        if name == "quiz_rubric" and payload["mode"] == "negative":
            return deducting_answer(payload)
        if name == "answer_key_read":
            self.read.append(body)
            if self.about(payload) in self.keys:
                return self.keys[self.about(payload)]
            if "author_answers" not in payload and "correct_choice" not in payload:  # the plan's own reading: no writer's answers
                return {"parts": [{"label": "b", "accept": ["y = 9"], "basis": "From the worked solution."}], "unreadable": ""}
        return QuizDrafts.answer(self, body, host)

    def draft(self, mode="negative"):
        return self.frq.create_quiz_draft({"request_id": str(uuid.uuid4()), "term_id": self.term["id"], "title": "Chapter 3 Quiz", "total": 5, "versions": 2,
                                           "mode": mode, "instructions": "Sections 3.1-3.3: one multiple choice, one free response.", "examples": []},
                                          [("notes.txt", b"Linear programming: corner points, bounded regions.")])

    def revision(self, draft_id, based_on=None):
        """Carry out the draft's first revision, or one asked of `based_on`: the finished revision, and the key requests made for it."""
        self.read.clear()
        if based_on:
            self.frq.revise_quiz_draft(draft_id, {"request_id": str(uuid.uuid4()), "based_on": based_on, "feedback": "Change what I said."})
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft_id)
        found = next((r for r in draft["revisions"] if r["id"] == draft["latest_complete"]), {})
        self.assertEqual((draft["state"], found.get("based_on")), ("ready", based_on), draft["revisions"][-1].get("error"))
        return found, sorted(self.about(payload_of(body)) for body in self.read)

    def approve(self, draft_id, revision_id, **more):
        options = self.frq.quiz_approval_options(draft_id)
        self.frq.approve_quiz_draft(draft_id, {"request_id": str(uuid.uuid4()), "revision_id": revision_id, "confirmation": "approve", "key": options["assignment"]["key"],
                                               "title": "Chapter 3 Quiz", "sections": [self.sections[501]], "copies": {"A": 2, "B": 1}, **more})
        self.frq.process_cloud()
        approval = self.frq.quiz_draft(draft_id)["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))
        self.frq.cancel_rubric_quality(approval["result"]["plan_id"], {})  # the review the approval asked for is stopped (the fixture model cannot answer it)
        return approval

    @staticmethod
    def drafted(revision):
        return {(str(r["problem"]), letter): reading["key"] for r in revision["result"]["rubrics"] for letter, reading in r["keys"].items()}

    def questions(self, plan_id):
        return {(q["content"]["printed_number"], q["exam_version"]): q for q in self.frq.assignment(plan_id)["questions"]}

    def test_each_problems_key_is_read_once_while_the_quiz_is_drafted_with_the_solution_in_view(self):
        draft = self.draft()
        revision, asked = self.revision(draft["id"])
        # One request per problem and version, after the rubrics, each the revision's own under its own step.
        self.assertEqual([prompt_name(r["body"]) for r in self.host.requests], ["quiz_write", "quiz_check", "quiz_rubric", "quiz_rubric"] + ["answer_key_read"] * 4
                         + ["rubric_quality_versions"] * 2)  # then the rubric check, which reads each key (2026-10-11, RubricQuestionsAtDraftReview)
        self.assertEqual(asked, [("1", "A"), ("1", "B"), ("2", "A"), ("2", "B")])
        with connect(self.path) as db:
            calls = [tuple(r) for r in db.execute("SELECT step,quiz_revision_id,state FROM frq_cloud_calls WHERE prompt='answer_key_read' ORDER BY step")]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rubric_generations").fetchone()[0], 0)  # no reading of a plan's: there is no plan yet
        self.assertEqual(calls, [(f"quiz:key:P{number}:{letter}", revision["id"], "completed") for number in (1, 2) for letter in "AB"])
        # The reader's own contract (question, rubric lines, full-credit line, problem, pictures), and what the draft already knows:
        # the worked solution, the writer's final answers, the correct choice, and the printed page that holds the problem.
        sent = {self.about(payload_of(body)): body for body in self.read}
        free = payload_of(sent[("2", "B")])
        self.assertEqual(sorted(free), ["author_answers", "full_credit_line", "pictures", "problem_text", "question", "rubric_lines", "worked_solution"])
        self.assertEqual(free["question"], {"title": "Question 2", "printed_number": "2", "max_points": 3})
        self.assertEqual((free["worked_solution"], free["author_answers"]), ("Substitute.", [{"part": "b", "answer": "$y = 8.5$"}]))
        self.assertIn("(b) Find $y$ when $x = 3$.", free["problem_text"])
        self.assertEqual(free["rubric_lines"], [{"id": "R1", "points": 0, "description": "Correct answer with supporting work"},
                                                {"id": "R2", "points": -1, "description": "Arithmetic error"},
                                                {"id": "R3", "points": -3, "description": "No answer or completely incorrect"}])
        self.assertIsNone(free["full_credit_line"])  # a deducting rubric has no line worth the whole question
        self.assertEqual((free["pictures"], len(images_of(sent[("2", "B")]))), (1, 1))
        choice = payload_of(sent[("1", "A")])
        self.assertEqual((choice["correct_choice"], choice["worked_solution"], choice["author_answers"]), ("b", "Divide by 2.", [{"part": "", "answer": "(b) $x = 2$"}]))
        # Kept with the revision, beside each problem's rubric, per version; the draft's page reads them from there.
        keys = {r["problem"]: r["keys"] for r in revision["result"]["rubrics"]}
        self.assertEqual(keys[2]["B"], {"rule": "answer-key-read-v2", "key": [{"label": "b", "accept": ["y = 8.5"]}], "basis": ["From the worked solution."], "unreadable": ""})
        self.assertEqual(keys[1]["A"]["key"], [{"label": "", "accept": ["(b)", "x = 2"]}])  # multiple choice: the letter and the entry itself
        self.assertEqual(revision["summary"]["keys"], 4)
        self.assertEqual(self.frq.quiz_drafts()[0]["revisions"][-1]["summary"]["keys"], 4)  # the drafts list's summary says the count too
        self.assertFalse([w for w in revision["result"]["warnings"] if "answer key" in w])

    def test_a_revision_that_carries_a_rubric_carries_its_key_and_asks_nothing(self):
        draft = self.draft()
        first, _ = self.revision(draft["id"])
        # Problem 2 is asked otherwise: its rubric is drafted again, and so its key is read again on both versions. Problem 1's rubric
        # is carried from the revision before, and its keys with it: no request.
        self.harder = True
        second, asked = self.revision(draft["id"], first["id"])
        self.assertEqual(asked, [("2", "A"), ("2", "B")])
        self.assertIn("Find its intercepts too.", payload_of(self.read[0])["problem_text"])
        one, two = second["result"]["rubrics"]
        self.assertEqual((one["carried_from"], one["keys"]), (first["id"], first["result"]["rubrics"][0]["keys"]))
        self.assertNotIn("carried_from", two)
        self.assertEqual(second["summary"]["keys"], 4)
        # Nothing changes: both rubrics are carried, and every key with them.
        third, asked = self.revision(draft["id"], second["id"])
        self.assertEqual(asked, [])
        self.assertEqual(self.drafted(third), self.drafted(second))
        self.assertEqual([r["carried_from"] for r in third["result"]["rubrics"]], [second["id"], second["id"]])

    def test_a_reading_that_fails_or_cannot_read_never_fails_the_revision(self):
        # Version B's problem 2 cannot be read; Version A's problem 1 is answered with no accepted answer both times it is asked.
        self.keys = {("2", "B"): self.UNREADABLE, ("1", "A"): {"parts": [{"label": "", "accept": [], "basis": ""}], "unreadable": ""}}
        draft = self.draft()
        first, asked = self.revision(draft["id"])
        self.assertEqual(asked, [("1", "A"), ("1", "A"), ("1", "B"), ("2", "A"), ("2", "B")])  # a refused answer is asked once more
        keys = {r["problem"]: r["keys"] for r in first["result"]["rubrics"]}
        self.assertEqual((keys[2]["B"]["key"], keys[2]["B"]["unreadable"]), ([], "The graph does not give the line."))
        self.assertEqual((keys[1]["A"]["key"], keys[1]["A"]["unreadable"]), ([], ""))
        self.assertIn("at least one accepted answer", keys[1]["A"]["error"])
        self.assertEqual([w for w in first["result"]["warnings"] if "answer key" in w], [
            "Version A: Problem 1 has no answer key: reading it failed (Give at least one accepted answer for each part, or remove the part.). Approving saves none for it.",
            "Version B: Problem 2 has no answer key: the model could not read one (The graph does not give the line.). Approving saves none for it."])
        self.assertEqual(first["summary"]["keys"], 2)
        # The next revision carries both rubrics with the keys they have, and reads again only the two versions without one.
        self.keys = {}
        second, asked = self.revision(draft["id"], first["id"])
        self.assertEqual(asked, [("1", "A"), ("2", "B")])
        self.assertEqual((second["summary"]["keys"], [w for w in second["result"]["warnings"] if "answer key" in w]), (4, []))

    def test_a_part_with_several_values_is_read_with_each_value_numbered(self):
        """ONE PART, SEVERAL VALUES (2026-10-29): Quiz 6's Problem 4(b) asks how many and the maximum revenue, and the reader gave each
        value its own entry labelled "b", as its prompt says. That was refused twice on Versions B, C and D, which were left without
        a key. The repeated letter is now numbered in order and the key kept, asked once; the same label with the same answers twice,
        or a blank label beside lettered ones, is still refused."""
        from grading.rubric_generation import read_key
        self.keys = {("2", "B"): {"parts": [{"label": "b", "accept": ["2000"], "basis": "The vertex."},
                                             {"label": "B", "accept": ["\\$80{,}000"], "basis": "R at the vertex."}], "unreadable": ""}}
        draft = self.draft()
        revision, asked = self.revision(draft["id"])
        self.assertEqual(asked, [("1", "A"), ("1", "B"), ("2", "A"), ("2", "B")])  # nothing refused, so nothing asked again
        keys = {r["problem"]: r["keys"] for r in revision["result"]["rubrics"]}
        self.assertEqual(keys[2]["B"]["key"], [{"label": "b (1)", "accept": ["2000"]}, {"label": "B (2)", "accept": ["\\$80{,}000"]}])
        self.assertEqual(keys[2]["B"]["basis"], ["The vertex.", "R at the vertex."])
        self.assertEqual((revision["summary"]["keys"], [w for w in revision["result"]["warnings"] if "answer key" in w]), (4, []))
        read = lambda *parts: read_key({"parts": [{"label": label, "accept": accept, "basis": ""} for label, accept in parts], "unreadable": ""})["key"]
        self.assertEqual(read(("", ["dependent"]), ("", ["(3+t, t)"])), [{"label": "(1)", "accept": ["dependent"]}, {"label": "(2)", "accept": ["(3+t, t)"]}])
        self.assertEqual(read(("a", ["1"]), ("b (tickets)", ["2"])), [{"label": "a", "accept": ["1"]}, {"label": "b (tickets)", "accept": ["2"]}])
        for refused in ((("b", ["2"]), ("b", ["2"])), (("a", ["1"]), ("", ["2"]))):
            with self.subTest(refused=refused), self.assertRaisesRegex(ValueError, "Label each part of the answer key once"):
                read(*refused)

    def test_approving_from_the_page_that_showed_the_keys_saves_them_before_the_rubric_check_is_queued(self):
        self.keys = {("2", "B"): self.UNREADABLE}
        draft = self.draft()
        revision, _ = self.revision(draft["id"])
        sent = {self.about(payload_of(body)): payload_of(body) for body in self.read}
        approval = self.approve(draft["id"], revision["id"], save_keys=True)
        plan_id = approval["result"]["plan_id"]
        self.assertIs(approval["input"]["save_keys"], True)
        self.assertEqual((approval["progress"]["questions"]["count"], approval["progress"]["questions"]["keys"]), (4, 3))
        # Each question holds the key its problem showed on its version; the one that could not be read has none.
        questions = self.questions(plan_id)
        self.assertEqual({where: q["content"]["answer_key"] for where, q in questions.items()}, self.drafted(revision))
        self.assertEqual((questions[("2", "A")]["content"]["answer_key"], questions[("2", "B")]["content"]["answer_key"]), ([{"label": "b", "accept": ["y = 9"]}], []))
        self.assertEqual(questions[("2", "A")]["answer"]["key"], [{"label": "b", "accept": ["y = 9"]}])  # what Key & Rubric Finalization shows as saved
        self.assertTrue(all(q["location_check"]["status"] == "confirmed" for q in questions.values()))  # the rest of the setup is as it was
        with connect(self.path) as db:
            created = {r["question_id"]: json.loads(r["content_json"])["answer_key"] for r in db.execute("SELECT question_id,content_json FROM frq_question_revisions WHERE revision=1")}
            check = db.execute("SELECT input_json FROM frq_rubric_quality_checks WHERE assignment_id=?", (plan_id,)).fetchone()
            readings = db.execute("SELECT COUNT(*) FROM frq_rubric_generations WHERE assignment_id=?", (plan_id,)).fetchone()[0]
            events = {r[0] for r in db.execute("SELECT kind FROM frq_events WHERE assignment_id=?", (plan_id,))}
        # Saved with each question's FIRST revision, so the rubric check the approval queued read every problem beside its key.
        self.assertEqual(created, {q["question_id"]: q["content"]["answer_key"] for q in questions.values()})
        self.assertEqual({q["question_id"]: q["reference"]["answer_key"] for q in json.loads(check["input_json"])["questions"]}, created)
        # Nothing was read from the blank assignment afterwards, and nothing but the approval saved a key.
        self.assertEqual((readings, events & {"answer_key_reading_queued", "answer_key_saved"}), (0, set()))
        # The key was read against the very lines, problem and solution the question got.
        for where in (("1", "B"), ("2", "A")):
            q = questions[where]
            self.assertEqual(sent[where]["rubric_lines"], [{k: item[k] for k in ("id", "points", "description")} for item in q["rubric"]["rules"]["items"]])
            self.assertEqual((sent[where]["problem_text"], sent[where]["worked_solution"]), (q["content"]["problem"], q["content"]["worked_solution"]))
            self.assertEqual(sent[where]["question"], {"title": q["content"]["title"], "printed_number": q["content"]["printed_number"], "max_points": q["content"]["max_points"]})

    def test_an_approval_that_does_not_say_save_keys_saves_none_and_the_plans_own_reading_sends_the_solution(self):
        # A tab opened before the page showed the keys approves as it always did: no key is saved (a key stays unused until the
        # instructor saves it), and the approval reads as an earlier one.
        draft = self.draft()
        revision, _ = self.revision(draft["id"])
        approval = self.approve(draft["id"], revision["id"])
        plan_id = approval["result"]["plan_id"]
        self.assertEqual(sorted(approval["input"]), ["copies", "key", "revision_id", "rule", "sections", "term_id", "title"])
        self.assertEqual(sorted(approval["progress"]["questions"]), ["at", "count"])
        questions = self.questions(plan_id)
        self.assertEqual([q["content"]["answer_key"] for q in questions.values()], [[]] * 4)
        with connect(self.path) as db:
            check = db.execute("SELECT input_json FROM frq_rubric_quality_checks WHERE assignment_id=?", (plan_id,)).fetchone()
        self.assertEqual([q["reference"]["answer_key"] for q in json.loads(check["input_json"])["questions"]], [[]] * 4)
        # Key & Rubric Finalization reads such a key on request, as before; the reading is now sent the question's stored worked
        # solution (answer_key_read v2), and still saves nothing.
        self.read.clear()
        qid = questions[("2", "A")]["question_id"]
        self.frq.queue_answer_key_reading(plan_id, {"request_id": "keys-1", "question_ids": [qid]})
        self.frq.process_cloud()
        [sent] = [payload_of(body) for body in self.read]
        self.assertEqual(sorted(sent), ["full_credit_line", "pictures", "problem_text", "question", "rubric_lines", "worked_solution"])
        self.assertEqual(sent["worked_solution"], "Substitute.")
        question = self.questions(plan_id)[("2", "A")]
        self.assertEqual((question["answer"]["read_key"]["status"], question["answer"]["read_key"]["key"]), ("complete", [{"label": "b", "accept": ["y = 9"]}]))
        self.assertEqual(question["content"]["answer_key"], [])
        with connect(self.path) as db:
            self.assertEqual(json.loads(db.execute("SELECT input_json FROM frq_rubric_generations WHERE assignment_id=?", (plan_id,)).fetchone()[0])["rule"], "answer-key-read-v2")

    def test_a_rebuild_keeps_the_keys_and_asks_again_only_for_a_question_still_without_one(self):
        self.keys = {("2", "B"): self.UNREADABLE}
        real = quiz_latex.render
        with patch("document_identity.quiz_latex.render", side_effect=lambda quiz, letter: real(quiz, letter).replace("[letterpaper,12pt]", "[letterpaper,12pt,pagecode]", 1)):
            draft = self.draft()
            revision, _ = self.revision(draft["id"])
            approval = self.approve(draft["id"], revision["id"], save_keys=True)
        done, plan_id = approval["result"], approval["result"]["plan_id"]
        # The instructor asks for a reading of the one question without a key; it finds none either. An event also names the other
        # questions, as when they were created without keys by an earlier attempt and read since (a reading refuses a keyed question).
        self.frq.queue_answer_key_reading(plan_id, {"request_id": "keys-1", "question_ids": [done["questions"]["2:B"]]})
        self.frq.process_cloud()
        with connect(self.path) as db:
            self.frq.event(db, plan_id, "answer_key_reading_queued", {"request_id": "keys-0", "question_ids": sorted(done["questions"].values()), "model": "alpha-vision"})
        self.frq.rebuild_quiz_approval(draft["id"], approval["id"], {"request_id": str(uuid.uuid4()), "confirmation": "rebuild"})
        self.frq.process_cloud()
        approved = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(approved["rebuilds"][-1]["status"], "complete", approved["rebuilds"][-1]["error"])
        result = approved["result"]
        self.assertFalse(set(result["questions"].values()) & set(done["questions"].values()))
        # The new questions hold the keys the approval saved, in their own first revisions ...
        questions = self.questions(plan_id)
        self.assertEqual({where: q["content"]["answer_key"] for where, q in questions.items()}, self.drafted(revision))
        self.assertEqual({q["question_id"] for q in questions.values()}, set(result["questions"].values()))
        # ... and only the question still without one is read again: one keyed question in the request would have refused it whole.
        with connect(self.path) as db:
            again = [json.loads(r[0])["question_id"] for r in db.execute("SELECT input_json FROM frq_rubric_generations WHERE request_id LIKE 'quiz-rebuild-keys:%'")]
            check = db.execute("SELECT input_json FROM frq_rubric_quality_checks WHERE assignment_id=? ORDER BY rowid DESC LIMIT 1", (plan_id,)).fetchone()
        self.assertEqual((result["answer_keys"], again), ("requested", [result["questions"]["2:B"]]))
        self.assertEqual({q["question_id"]: q["reference"]["answer_key"] for q in json.loads(check["input_json"])["questions"]},
                         {q["question_id"]: q["content"]["answer_key"] for q in questions.values()})


class CheckRecords(unittest.TestCase):
    """What a revision keeps of its check (`quiz_drafts.check_record`): only what the check saw, with what still stands of it."""
    ISSUES = [{"version": "all", "problem": 0, "kind": "layout", "issue": "Whole.", "fix": ""},
              {"version": "A", "problem": 1, "kind": "wording", "issue": "One.", "fix": ""}, {"version": "B", "problem": 2, "kind": "figure", "issue": "Two.", "fix": ""}]

    def test_a_quiz_left_as_checked_keeps_every_key_and_finding(self):
        shown = ({1: "k1", 2: "k2"}, "w")
        kept = check_record({"issues": self.ISSUES, "notes": "", "unchanged": []}, False, shown, shown)
        self.assertEqual(sorted(kept), ["fixed", "issues", "notes", "seen"])  # `unchanged` is named only when a problem was not asked about
        self.assertEqual((kept["seen"]["problems"], kept["seen"]["whole"], kept["seen"]["issues"]), ({"1": "k1", "2": "k2"}, "w", self.ISSUES))

    def test_a_fix_leaves_only_the_problems_it_did_not_touch_as_seen(self):
        kept = check_record({"issues": self.ISSUES, "notes": "", "unchanged": [1]}, True, ({1: "k1", 2: "k2"}, "w"), ({1: "k1", 2: "fixed"}, "w2"))
        self.assertEqual((kept["issues"], kept["fixed"], kept["unchanged"]), (self.ISSUES, True, [1]))
        self.assertEqual((kept["seen"]["problems"], kept["seen"]["whole"], kept["seen"]["issues"]), ({"1": "k1"}, "", self.ISSUES[1:2]))


class RubricQuestionsAtDraftReview(unittest.TestCase):
    """(QUIZZES "A REVISION", "APPROVAL" and "REBUILDING"; GRADING, the rubric
    finalization). Measured: the rubric check ran only after approval, its concerns waited for a second sitting, Quiz 4 needed 10
    check runs, and every proposed clarification was confirmed as proposed. So the SAME check runs as a revision's last step on what
    approving would create, its decisions are answered on the draft (a revision that runs only Resolve), and an approval records the
    check for the plan and, on one explicit tick, the ordinary confirmation. Wherever the reviewer is not proved to have read what a
    check of the created questions would send, the approval queues the plan's own check exactly as before."""
    draft = QuizDrafts.draft
    # One finding the reviewer names once for both versions of problem 2 (a v4 finding): one decision, two concerns.
    DECISION = {"versions": ["A", "B"], "item_ids": [{"version": v, "item_ids": ["R2"]} for v in "AB"], "version_notes": [],
                "message": "Part A's line pays for 'bounded' alone: must the student say why?", "title": "Does Part A need a reason?",
                "decision": "Should Part A's point require a justification?", "group": "",
                "choices": [{"label": "The answer alone", "feedback": "Give Part A's point for the answer alone.", "consequence": ""},
                            {"label": "A reason too", "feedback": "Require one sentence of justification for Part A's point.", "consequence": ""}]}

    def setUp(self):
        QuizDrafts.setUp(self)
        # The scripted reviewer: what it finds per problem ({printed number: suspected errors}), how it answers a resolution, and
        # every check and resolution request it was sent. `refuse` answers the check with nothing valid for those problems.
        self.findings, self.resolving, self.checks, self.resolutions, self.refuse = {}, lambda payload: resolve_answer(payload), [], [], set()

    @staticmethod
    def about(payload):
        return payload["problem"]["versions"][0]["question"]["printed_number"] if "problem" in payload else payload["question"]["printed_number"]

    def answer(self, body, host):
        name, payload = prompt_name(body), payload_of(body)
        if name in ("rubric_quality_versions", "rubric_quality") and reviewing_a_draft(self.path):
            self.checks.append(body)
            if self.about(payload) in self.refuse:
                return {"summary": "", "suspected_errors": [], "consistency_issues": [], "versions": []}  # no version reviewed: refused, asked once more
            return review_answer(payload, errors=self.findings.get(self.about(payload), ()))
        if name == "rubric_quality_resolve":
            self.resolutions.append(body)
            return self.resolving(payload)
        return QuizDrafts.answer(self, body, host)

    def revision(self, draft_id, based_on=None, feedback="Change what I said."):
        """Carry out the draft's first revision, or one asked of `based_on`: the finished revision and the requests made for it."""
        before = len(self.host.requests)
        if based_on:
            self.frq.revise_quiz_draft(draft_id, {"request_id": str(uuid.uuid4()), "based_on": based_on, "feedback": feedback})
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft_id)
        found = next((r for r in draft["revisions"] if r["id"] == draft["latest_complete"]), {})
        self.assertEqual((draft["state"], found.get("based_on")), ("ready", based_on), draft["revisions"][-1].get("error"))
        return found, [r["body"] for r in self.host.requests[before:]]

    def answered(self, draft_id, revision, responses, **more):
        """POST the instructor's answers to the decisions of the check `revision` shows, and carry the new revision out."""
        before = len(self.host.requests)
        sent = self.client.post(f"/api/quiz-drafts/{draft_id}/rubric-answers", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "based_on": revision["id"], "check_sha256": revision["result"]["rubric_check"]["sha256"],
            "responses": [{"concern": n, "response": text} for n, text in responses.items()], **more})
        if sent.status_code != 202:
            return sent, []
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft_id)
        return next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"]), [r["body"] for r in self.host.requests[before:]]

    def approve(self, draft_id, revision, **more):
        options = self.frq.quiz_approval_options(draft_id)
        self.frq.approve_quiz_draft(draft_id, {"request_id": str(uuid.uuid4()), "revision_id": revision["id"], "confirmation": "approve", "key": options["assignment"]["key"],
                                               "title": "Chapter 3 Quiz", "sections": [self.sections[501]], "copies": {"A": 2, "B": 1}, **more})
        self.frq.process_cloud()
        approval = self.frq.quiz_draft(draft_id)["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))
        return approval

    def finalize(self, revision):
        return {"confirm": True, "check_sha256": revision["result"]["rubric_check"]["sha256"]}

    def checks_of(self, plan_id):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_rubric_quality_checks WHERE assignment_id=? ORDER BY rowid", (plan_id,))]

    def test_a_revision_ends_with_the_plans_own_check_of_what_approving_would_create(self):
        self.findings = {"2": [self.DECISION]}
        draft = self.draft()
        revision, asked = self.revision(draft["id"])
        # One request per problem with both versions side by side, after the keys: the plan's prompt, rules, scheme and links.
        self.assertEqual([prompt_name(b) for b in asked][-2:], ["rubric_quality_versions"] * 2)
        sent = {self.about(payload_of(b)): b for b in self.checks}
        free = payload_of(sent["2"])
        self.assertEqual((sorted(free), free["rules"]), (["instructor_comments_elsewhere", "known_links", "problem", "rules"], "rubric-builtin-v1"))
        self.assertEqual([v["version"] for v in free["problem"]["versions"]], ["A", "B"])
        first = free["problem"]["versions"][0]
        self.assertEqual({k: first["question"][k] for k in ("title", "printed_number", "max_points")}, {"title": "Question 2", "printed_number": "2", "max_points": 3})
        self.assertEqual([(i["id"], i["points"]) for i in first["question"]["rubric"]["items"]], [("R1", 3), ("R2", 1), ("R3", 2), ("R4", 0)])
        self.assertEqual(sorted(rule["item_id"] for rule in first["question"]["builtin"]), ["R1", "R4"])
        # The drafted clarifications are the line comments the questions will get; the reference is the problem as written, the worked
        # solution and the key read for that version; each version's printed problem is cropped from the revision's own pages.
        self.assertEqual(first["question"]["instructor_comments"], {"R4": "Apply only when no other rubric item applies to the response."})
        self.assertEqual((first["reference"]["worked_solution"], first["reference"]["answer_key"]), ("Substitute.", [{"label": "b", "accept": ["y = 9"]}]))
        self.assertIn("(b) Find $y$ when $x = 2$.", first["reference"]["problem"])
        self.assertEqual(([v["reference"]["problem_images"] for v in free["problem"]["versions"]], len(images_of(sent["2"]))), ([[0], [1]], 2))
        # The drafted links are candidates to verify, as a new plan's first check names them; the other problem's comments travel too
        # (so a problem's result is never judged by its whole request).
        self.assertEqual({(link["kind"], tuple(line["version"] for line in link["lines"])) for link in free["known_links"]}, {("seed", ("A", "B"))})
        self.assertEqual([c["problem"] for c in free["instructor_comments_elsewhere"]], ["Question 1"])
        self.assertNotIn("draft:", json.dumps(free))  # the reviewer is sent no identity
        # Each request is the REVISION's, under the check's own step names; nothing exists for a plan yet.
        with connect(self.path) as db:
            calls = [tuple(r) for r in db.execute("SELECT step,quiz_revision_id,rubric_check_id,state FROM frq_cloud_calls WHERE prompt='rubric_quality_versions' ORDER BY step")]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rubric_quality_checks").fetchone()[0], 0)
            stored = json.loads(db.execute("SELECT result_json FROM quiz_draft_revisions WHERE id=?", (revision["id"],)).fetchone()[0])["rubric_check"]
            receipts = json.loads(db.execute("SELECT provider_json FROM quiz_draft_revisions WHERE id=?", (revision["id"],)).fetchone()[0])["rubric_check"]
        self.assertEqual(calls, [(f"rubric_quality:problem:draft:{n}:A", revision["id"], None, "completed") for n in (1, 2)])
        # Kept with the revision: the result under the virtual IDs, each question's digests, each version's PDF, the reviewer.
        check = revision["result"]["rubric_check"]
        self.assertEqual((check["status"], check["rule"], check["model"]), ("complete", "quiz-draft-check-v1", "alpha-vision"))
        self.assertEqual(sorted(check["questions"]), ["draft:1:A", "draft:1:B", "draft:2:A", "draft:2:B"])
        self.assertEqual(sorted(check["questions"]["draft:2:B"]), ["content", "printed", "problem", "reference", "version"])
        self.assertEqual(check["pdf"], {letter: revision["result"]["versions"][letter]["files"]["main.pdf"]["sha256"] for letter in "AB"})
        self.assertEqual(check["result"]["families"], [["draft:1:A", "draft:1:B"], ["draft:2:A", "draft:2:B"]])
        self.assertEqual((stored["profile"]["endpoint"]["name"], "profile" in check, sorted(receipts["questions"])), ("alpha-vision", False, sorted(check["questions"])))
        # Its concerns are in the result (one row per version, one shared key) and counted in the summary as ONE decision.
        errors = check["result"]["suspected_errors"]
        self.assertEqual([(e["question_id"], e["item_ids"], e["title"]) for e in errors], [(f"draft:2:{v}", ["R2"], "Does Part A need a reason?") for v in "AB"])
        self.assertEqual(len({e["shared"] for e in errors}), 1)
        self.assertEqual(revision["summary"]["rubric_check"], {"status": "complete", "decisions": 1, "overflow": 0, "clarifications": 4, "exclusions": 0, "answered": 0})
        self.assertEqual(self.frq.quiz_drafts()[0]["revisions"][-1]["summary"]["rubric_check"]["decisions"], 1)
        # The page draws the decisions from the questions as the check read them.
        self.assertEqual([(q["question_id"], q["exam_version"], q["title"]) for q in check["view"]][2:], [("draft:2:A", "A", "Question 2"), ("draft:2:B", "B", "Question 2")])
        self.assertFalse([w for w in revision["result"]["warnings"] if "could not be checked" in w])

    def test_a_problem_that_reads_as_before_keeps_its_result_and_is_not_asked_again(self):
        self.findings = {"1": [{**self.DECISION, "item_ids": [{"version": v, "item_ids": ["R1"]} for v in "AB"], "title": "Does the letter alone count?"}]}
        draft = self.draft()
        first, _ = self.revision(draft["id"])
        self.assertEqual(len(self.checks), 2)
        # Problem 2 is asked otherwise: its rubric is drafted again and so it is checked again, alone, as any problem is (the plan's
        # ordinary prompt, both versions). Problem 1's rubric and keys were carried and it reads as before: nothing is asked.
        self.harder = True
        self.checks.clear()
        second, asked = self.revision(draft["id"], first["id"])
        self.assertEqual([prompt_name(b) for b in asked].count("rubric_quality_versions"), 1)
        self.assertEqual(([self.about(payload_of(b)) for b in self.checks], sorted(payload_of(self.checks[0]))), (["2"], ["instructor_comments_elsewhere", "known_links", "problem", "rules"]))
        was, now = first["result"]["rubric_check"], second["result"]["rubric_check"]
        self.assertEqual((now["problems"]["1"]["carried_from"], "carried_from" in now["problems"]["2"]), (first["id"], False))
        self.assertEqual((now["problems"]["1"]["digest"], now["problems"]["2"]["digest"] == was["problems"]["2"]["digest"]), (was["problems"]["1"]["digest"], False))
        entries = lambda check: {q["question_id"]: q for q in check["result"]["questions"]}
        self.assertEqual({k: v for k, v in entries(now).items() if k.startswith("draft:1:")}, {k: v for k, v in entries(was).items() if k.startswith("draft:1:")})
        # Its open decision came with it, numbered among this check's concerns; the summary says what was asked and what was kept.
        self.assertEqual(now["result"]["suspected_errors"], was["result"]["suspected_errors"])
        self.assertEqual((second["summary"]["rubric_check"]["decisions"], "settled" in now["result"]), (1, False))
        self.assertIn("1 problem checked in this revision", now["result"]["summary"])
        self.assertIn("Problem 1 reads as in the revision before and keeps its check.", now["result"]["summary"])
        self.assertNotEqual(now["sha256"], was["sha256"])
        with connect(self.path) as db:
            receipts = json.loads(db.execute("SELECT provider_json FROM quiz_draft_revisions WHERE id=?", (second["id"],)).fetchone()[0])["rubric_check"]
        self.assertEqual(sorted(receipts["questions"]), sorted(now["questions"]))  # the kept problem's receipt comes along
        # Nothing changes: every problem keeps its result and no request is made at all.
        self.checks.clear()
        third, asked = self.revision(draft["id"], second["id"])
        self.assertEqual((self.checks, [prompt_name(b) for b in asked]), ([], ["quiz_write"]))
        self.assertEqual(third["result"]["rubric_check"]["result"], now["result"])
        self.assertEqual({n: p["carried_from"] for n, p in third["result"]["rubric_check"]["problems"].items()}, {"1": first["id"], "2": second["id"]})

    def test_answers_to_the_decisions_are_a_revision_that_runs_only_resolve(self):
        self.findings = {"2": [self.DECISION]}
        self.resolving = lambda payload: resolve_answer(payload, clarify={"R2": "Require one sentence of justification."})
        draft = self.draft()
        first, _ = self.revision(draft["id"])
        # The answers must be about the check shown, and its concerns by their numbers.
        stale = self.client.post(f"/api/quiz-drafts/{draft['id']}/rubric-answers", headers=HEADERS, json={
            "request_id": str(uuid.uuid4()), "based_on": first["id"], "check_sha256": "0" * 64, "responses": []})
        self.assertEqual((stale.status_code, "not the one this revision holds" in stale.json["error"]), (409, True))
        unknown, _ = self.answered(draft["id"], first, {7: "No such decision."})
        self.assertEqual((unknown.status_code, "does not list" in unknown.json["error"]), (409, True))
        second, asked = self.answered(draft["id"], first, {0: "Require one sentence of justification."})
        # ONE request: the reviewer's Resolve for the problem the decision names. No writer, no page check, no rubric, no key.
        self.assertEqual([prompt_name(b) for b in asked], ["rubric_quality_resolve"])
        resolve = payload_of(asked[0])
        self.assertEqual([(c["concern"], c.get("instructor_response"), c["versions"]) for c in resolve["concerns"]],
                         [(0, "Require one sentence of justification.", ["A"]), (1, None, ["B"])])
        self.assertEqual(len({c["shared"] for c in resolve["concerns"]}), 1)  # one answer reaches both versions
        self.assertEqual([row["item_id"] for row in resolve["problem"]["versions"][0]["current"]], ["R1", "R2", "R3", "R4"])
        self.assertEqual(len(images_of(asked[0])), 2)  # the problem as printed, from the revision it answers
        with connect(self.path) as db:
            calls = [tuple(r) for r in db.execute("SELECT step,state FROM frq_cloud_calls WHERE quiz_revision_id=?", (second["id"],))]
        self.assertEqual(calls, [("rubric_quality:resolve:draft:2:A", "completed")])
        self.assertEqual((second["number"], second["based_on"], second["rubric_answers"]), (2, first["id"], [{"concern": 0, "response": "Require one sentence of justification."}]))
        with connect(self.path) as db:
            request_id = db.execute("SELECT request_id FROM quiz_draft_revisions WHERE id=?", (second["id"],)).fetchone()[0]
        replay = {"request_id": request_id, "based_on": first["id"], "check_sha256": first["result"]["rubric_check"]["sha256"],
                  "responses": [{"concern": 0, "response": "Require one sentence of justification."}]}
        self.assertEqual(len(self.frq.answer_quiz_rubric_check(draft["id"], replay)["revisions"]), 2)
        with self.assertRaises(Conflict):
            self.frq.answer_quiz_rubric_check(draft["id"], {**replay, "responses": [{"concern": 0, "response": "A different decision."}]})
        # Nothing of the quiz changed: the quiz, its rubrics with their keys, its page check and its files are the revision's it answers.
        for part in ("quiz", "rubrics", "versions", "check", "warnings", "mode"):
            self.assertEqual(second["result"][part], first["result"][part], part)
        self.assertEqual(self.frq.quiz_file(draft["id"], second["id"], "B", "pdf"), self.frq.quiz_file(draft["id"], first["id"], "B", "pdf"))
        # The check holds the resolved guidance: the decision is answered on both versions, nothing is open.
        check = second["result"]["rubric_check"]
        self.assertEqual((check["result"]["suspected_errors"], len(check["result"]["resolution"]["resolved"])), ([], 2))
        self.assertEqual([(row["question_id"], row.get("comment"), row.get("without_comment")) for row in check["result"]["resolution"]["resolved"]],
                         [("draft:2:A", "Require one sentence of justification.", None), ("draft:2:B", None, True)])
        entries = lambda found: {q["question_id"]: q for q in found["result"]["questions"]}
        self.assertEqual({qid: e["clarifications"]["R2"] for qid, e in entries(check).items() if qid.startswith("draft:2:")},
                         {"draft:2:A": "Require one sentence of justification.", "draft:2:B": "Require one sentence of justification."})
        guidance = lambda found: {qid: (e["clarifications"], e["pairs"]) for qid, e in entries(found).items() if qid.startswith("draft:1:")}
        self.assertEqual(guidance(check), guidance(first["result"]["rubric_check"]))  # the problem no decision named keeps its guidance
        self.assertEqual(second["summary"]["rubric_check"], {"status": "complete", "decisions": 0, "overflow": 0, "clarifications": 6, "exclusions": 0, "answered": 1})
        self.assertEqual((check["questions"], check["pdf"], check["problems"]), tuple(first["result"]["rubric_check"][k] for k in ("questions", "pdf", "problems")))
        self.assertNotEqual(check["sha256"], first["result"]["rubric_check"]["sha256"])
        # With nothing left to answer there is nothing to send.
        again, _ = self.answered(draft["id"], second, {})
        self.assertEqual((again.status_code, "no concerns" in again.json["error"]), (409, True))
        # A later revision that changes nothing keeps the resolved guidance and its record, and asks no one.
        third, asked = self.revision(draft["id"], second["id"], "Keep it as it is.")
        self.assertEqual([prompt_name(b) for b in asked], ["quiz_write"])
        self.assertEqual(third["summary"]["rubric_check"], second["summary"]["rubric_check"])

    def test_a_resolution_that_fails_leaves_the_revision_it_answered(self):
        self.findings = {"2": [self.DECISION]}
        self.resolving = lambda payload: {**resolve_answer(payload), "concerns": []}  # no concern answered: refused, both times
        draft = self.draft()
        first, _ = self.revision(draft["id"])
        command = {"request_id": "answers-1", "based_on": first["id"], "check_sha256": first["result"]["rubric_check"]["sha256"],
                   "responses": [{"concern": 0, "response": "A reason too."}]}
        sent = self.client.post(f"/api/quiz-drafts/{draft['id']}/rubric-answers", headers=HEADERS, json=command)
        self.assertEqual((sent.status_code, sent.json["state"], sent.json["revisions"][-1]["rubric_answers"]), (202, "drafting", [{"concern": 0, "response": "A reason too."}]))
        self.frq.process_cloud()
        page = self.frq.quiz_draft(draft["id"])
        failed = page["revisions"][-1]
        self.assertEqual((page["state"], page["latest_complete"], failed["status"]), ("ready", first["id"], "failed"))
        self.assertIn("Your answers could not be resolved", failed["error"])
        self.assertEqual(failed["rubric_answers"], [{"concern": 0, "response": "A reason too."}])  # the page sends them again as they were
        shown = next(r for r in page["revisions"] if r["id"] == first["id"])
        self.assertEqual(shown["result"]["rubric_check"]["sha256"], first["result"]["rubric_check"]["sha256"])
        # An exact replay is the same failed revision; changing its answers cannot reuse that request's identity.
        replay = self.client.post(f"/api/quiz-drafts/{draft['id']}/rubric-answers", headers=HEADERS, json=command)
        self.assertEqual((replay.status_code, len(replay.json["revisions"])), (202, 2))
        changed = self.client.post(f"/api/quiz-drafts/{draft['id']}/rubric-answers", headers=HEADERS, json={**command, "responses": []})
        self.assertEqual(changed.status_code, 409)
        self.assertIn("request ID", changed.json["error"])
        self.assertEqual(len(self.frq.quiz_draft(draft["id"])["revisions"]), 2)
        # A new request, once the reviewer answers, resolves it.
        self.resolving = lambda payload: resolve_answer(payload)
        second, _ = self.answered(draft["id"], first, {0: "A reason too."})
        self.assertEqual((second["number"], second["summary"]["rubric_check"]["decisions"]), (3, 0))

    def test_an_answer_rewrites_the_drafted_comment_it_contradicts_and_the_approval_still_carries_the_check(self):
        """GRADING "FEWER RUBRIC QUESTIONS" › 2026-10-29 (Quiz 5: answered, the drafted R7 comment still said otherwise, and the check
        asked twice for the instructor to edit it). The no-credit line R4 is drafted with a clarification; the answer changes it."""
        self.findings = {"2": [{**self.DECISION, "item_ids": [{"version": v, "item_ids": ["R4"]} for v in "AB"]}]}
        self.resolving = lambda payload: resolve_answer(payload, clarify={"R4": "Apply also to a response whose only work is crossed out."})
        draft = self.draft()
        first, _ = self.revision(draft["id"])
        second, _ = self.answered(draft["id"], first, {0: "Apply it also when the only work is crossed out."})
        line = lambda revision: next(e for e in revision["result"]["rubrics"] if e["problem"] == 2)["rubric"]["items"][3]["clarifications"]
        self.assertEqual(line(first), {"A": "Apply only when no other rubric item applies to the response.", "B": "Apply only when no other rubric item applies to the response."})
        self.assertEqual(line(second), {"A": "Apply also to a response whose only work is crossed out.", "B": "Apply only when no other rubric item applies to the response."})
        self.assertEqual(second["rubric_answers"], [{"concern": 0, "response": "Apply it also when the only work is crossed out."}])
        check, was = second["result"]["rubric_check"], first["result"]["rubric_check"]
        self.assertNotEqual(check["questions"]["draft:2:A"]["content"], was["questions"]["draft:2:A"]["content"])
        self.assertEqual(check["questions"]["draft:2:B"], was["questions"]["draft:2:B"])
        self.assertEqual(check["questions"]["draft:1:A"], was["questions"]["draft:1:A"])
        # Approved, the plan's questions read as the check recorded them: it is carried, and no reviewer is asked again.
        asked = len(self.host.requests)
        approval = self.approve(draft["id"], second, save_keys=True)
        self.assertFalse([r for r in self.host.requests[asked:] if prompt_name(r["body"]).startswith("rubric_quality")])
        self.assertEqual(approval["progress"]["review"]["carried"], {"check_id": approval["result"]["rubric_check"]})

    def test_approval_records_the_check_for_the_plan_where_its_questions_are_the_ones_it_read(self):
        self.findings = {"2": [self.DECISION]}
        draft = self.draft()
        revision, _ = self.revision(draft["id"])
        asked = len(self.host.requests)
        approval = self.approve(draft["id"], revision, save_keys=True)
        plan_id, done = approval["result"]["plan_id"], approval["result"]
        # No request at all: the check the revision made IS the plan's completed check, under the approval's own request ID.
        self.assertFalse([r for r in self.host.requests[asked:] if prompt_name(r["body"]).startswith("rubric_quality")])
        [row] = self.checks_of(plan_id)
        self.assertEqual((row["request_id"], row["status"], row["cancelled"], row["id"]), (f"quiz-review:{revision['id']}", "complete", 0, done["rubric_check"]))
        self.assertEqual((approval["progress"]["review"]["carried"], done["rubric_review"], "finalization" in done), ({"check_id": row["id"]}, "requested", False))
        # Key & Rubric Finalization shows it exactly as a check that ran there: current, complete, waiting for the one confirmation.
        status = self.frq.rubric_quality(plan_id)
        self.assertEqual((status["state"], status["check"]["id"], status["check"]["current"], status["check"]["model"], status["review"]),
                         ("needs_review", row["id"], True, "alpha-vision", None))
        questions = {(q["content"]["printed_number"], q["exam_version"]): q for q in self.frq.assignment(plan_id)["questions"]}
        real = {f"draft:{number}:{letter}": q["question_id"] for (number, letter), q in questions.items()}
        result = status["check"]["result"]
        was = revision["result"]["rubric_check"]["result"]
        # The result names the plan's own questions and rubrics wherever the draft's named its virtual ones; nothing else differs.
        self.assertEqual({q["question_id"]: q["rubric_id"] for q in result["questions"]}, {q["question_id"]: q["rubric"]["id"] for q in questions.values()})
        self.assertEqual(result["families"], [[real[qid] for qid in family] for family in was["families"]])
        self.assertEqual([(e["question_id"], e["item_ids"], e["title"]) for e in result["suspected_errors"]], [(real[e["question_id"]], e["item_ids"], e["title"]) for e in was["suspected_errors"]])
        self.assertEqual(result["summary"], was["summary"])
        self.assertEqual(sorted(json.loads(row["provider_json"])["questions"]), sorted(real.values()))
        # Recorded with the very input and fingerprint a check queued now computes (asserted by queueing one).
        self.frq.queue_rubric_quality(plan_id, {"request_id": "again", "input_sha256": status["input_sha256"]})
        carried, queued = self.checks_of(plan_id)
        self.assertEqual((queued["status"], carried["input_sha256"]), ("pending", queued["input_sha256"]))
        strip = lambda text: {k: v for k, v in json.loads(text).items() if k != "known_links"}  # the queued one names the links this check left
        self.assertEqual(strip(carried["input_json"]), strip(queued["input_json"]))
        self.assertEqual({q["question_id"]: q["reference"]["answer_key"] for q in json.loads(carried["input_json"])["questions"]},
                         {q["question_id"]: q["content"]["answer_key"] for q in questions.values()})
        self.frq.cancel_rubric_quality(plan_id, {})
        # It was only recorded: nothing is confirmed, no rubric approved, and the one event says where the check ran.
        with connect(self.path) as db:
            events = {r["kind"]: json.loads(r["detail_json"]) for r in db.execute("SELECT kind,detail_json FROM frq_events WHERE assignment_id=?", (plan_id,))}
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rubric_quality_reviews").fetchone()[0], 0)
        self.assertEqual((events["rubric_quality_ready"]["quiz_revision_id"], "rubric_quality_confirmed" in events, "rubric_approved" in events), (revision["id"], False, False))
        held = self.frq.batch_finalization(self.frq.rubric_quality(plan_id), status["context"]["questions"])
        self.assertEqual((held[0], len(held[1])), (None, 4))
        # The instructor's one tick there confirms it, as any check: its open decisions never block confirming.
        confirmed = self.frq.confirm_rubric_quality(plan_id, {"version": self.frq.rubric_quality(plan_id)["version"], "check_id": row["id"], "input_sha256": status["input_sha256"],
                                                             "confirm_review": True, "questions": copy.deepcopy(result["questions"])})
        self.assertEqual(confirmed["state"], "confirmed")

    def test_anything_less_than_proof_queues_the_plans_own_check_as_before(self):
        draft = self.draft()
        revision, _ = self.revision(draft["id"])
        # An approval that saves no keys (a tab opened before, an assistant's card): the questions hold no key, the check read one.
        approval = self.approve(draft["id"], revision)
        plan_id = approval["result"]["plan_id"]
        [row] = self.checks_of(plan_id)
        self.assertEqual((row["request_id"], row["status"]), (f"quiz-review:{revision['id']}", "pending"))
        self.assertEqual((sorted(approval["progress"]["review"]), "rubric_check" in approval["result"]), (["at", "note", "requested"], False))
        self.frq.cancel_rubric_quality(plan_id, {})
        # The comparison itself, on a plan whose questions do hold what the check read: another PDF than the one checked is no proof.
        other = self.draft()
        second, _ = self.revision(other["id"])
        self.frq.approve_quiz_draft(other["id"], {"request_id": str(uuid.uuid4()), "revision_id": second["id"], "confirmation": "approve", "key": "chapter-3-quiz-b",
                                                  "title": "Chapter 3 Quiz B", "sections": [self.sections[501]], "copies": {"A": 1, "B": 1}, "save_keys": True})
        self.frq.process_cloud()
        done = self.frq.quiz_draft(other["id"])["approved"]
        self.assertEqual(done["status"], "complete", done.get("error"))
        created = {(int(key.split(":")[0]), key.split(":")[1]): qid for key, qid in done["result"]["questions"].items()}
        with connect(self.path) as db:
            check = json.loads(db.execute("SELECT result_json FROM quiz_draft_revisions WHERE id=?", (second["id"],)).fetchone()[0])["rubric_check"]
        carry = lambda check, printed, request: self.frq._carry_drafted_check(done["result"]["plan_id"], {"check": check, "receipts": {}, "printed": printed}, created,
                                                                               done["result"]["versions"], request, {"quiz_draft_id": other["id"], "quiz_revision_id": second["id"]})
        self.assertEqual(carry(check, True, f"quiz-review:{second['id']}"), done["result"]["rubric_check"])  # the same request: the check it recorded
        reprinted = copy.deepcopy(check)
        reprinted["questions"]["draft:1:A"]["printed"] = "0" * 64
        self.assertIsNone(carry(reprinted, True, "another-request"))
        edited = copy.deepcopy(check)
        edited["questions"]["draft:2:B"]["content"] = "0" * 64
        self.assertIsNone(carry(edited, False, "another-request"))
        self.assertIsNone(carry({**check, "status": "failed"}, True, "another-request"))
        self.assertEqual(len(self.checks_of(done["result"]["plan_id"])), 1)

    def test_one_tick_in_the_approval_records_the_ordinary_confirmation(self):
        self.findings = {"2": [self.DECISION]}
        draft = self.draft()
        first, _ = self.revision(draft["id"])
        command = {"request_id": str(uuid.uuid4()), "revision_id": first["id"], "confirmation": "approve", "key": "chapter-3-quiz", "title": "Chapter 3 Quiz",
                   "sections": [self.sections[501]], "copies": {"A": 2, "B": 1}, "save_keys": True}
        # Refused while a decision is open, for a check that is not the one shown, or without the explicit tick: nothing is approved.
        with self.assertRaisesRegex(Conflict, "still has 1 decision open"):
            self.frq.approve_quiz_draft(draft["id"], {**command, "finalization": self.finalize(first)})
        with self.assertRaisesRegex(Conflict, "not the one this revision holds"):
            self.frq.approve_quiz_draft(draft["id"], {**command, "finalization": {"confirm": True, "check_sha256": "0" * 64}})
        for wrong in (True, {"confirm": False, "check_sha256": first["result"]["rubric_check"]["sha256"]}, {"confirm": True}):
            with self.assertRaisesRegex(ValueError, "Tick the rubric finalization"):
                self.frq.approve_quiz_draft(draft["id"], {**command, "finalization": wrong})
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM quiz_draft_approvals").fetchone()[0], 0)
        # The decision answered, nothing is open; the fingerprint of the check answered before no longer names the one shown.
        second, _ = self.answered(draft["id"], first, {0: "A reason too."})
        with self.assertRaisesRegex(Conflict, "not the one this revision holds"):
            self.frq.approve_quiz_draft(draft["id"], {**command, "revision_id": second["id"], "finalization": self.finalize(first)})
        approval = self.approve(draft["id"], second, save_keys=True, finalization=self.finalize(second))
        plan_id, done = approval["result"]["plan_id"], approval["result"]
        self.assertEqual(approval["input"]["finalization"], self.finalize(second))
        [row] = self.checks_of(plan_id)
        status = self.frq.rubric_quality(plan_id)
        # The ORDINARY confirmation of that check: the plan points at it, every rubric is approved, every question stands.
        self.assertEqual((status["state"], status["review"]["check_id"], status["review"]["id"]), ("confirmed", row["id"], done["finalization"]["review_id"]))
        self.assertEqual(approval["progress"]["review"]["carried"], {"check_id": row["id"], "review_id": status["review"]["id"]})
        policy = {q["question_id"]: q for q in status["review"]["policy"]["questions"]}
        shown = {q["question_id"]: q for q in status["check"]["result"]["questions"]}
        self.assertEqual({qid: (q["clarifications"], q["pairs"]) for qid, q in policy.items()}, {qid: (q["clarifications"], q["pairs"]) for qid, q in shown.items()})
        self.assertEqual(sorted(status["review"]["policy"]["inputs"]), sorted(policy))
        with connect(self.path) as db:
            review = db.execute("SELECT actor,input_sha256 FROM frq_rubric_quality_reviews WHERE id=?", (status["review"]["id"],)).fetchone()
            approved = db.execute("SELECT COUNT(*) FROM frq_rubric_approvals").fetchone()[0]
        self.assertEqual((review["actor"], review["input_sha256"], approved), ("workspace", row["input_sha256"], 4))
        # A batch may take every question: the gate each batch passes finds the confirmation standing for all of them.
        finalization, held = self.frq.batch_finalization(status, status["context"]["questions"])
        self.assertEqual((finalization, held), (status["review"]["id"], []))

    def test_a_tick_that_cannot_be_honoured_confirms_nothing_and_says_so(self):
        # Ticked, but the approval saved no keys: the check cannot be carried, so the plan checks its rubrics itself and waits.
        draft = self.draft()
        revision, _ = self.revision(draft["id"])
        approval = self.approve(draft["id"], revision, finalization=self.finalize(revision))
        plan_id = approval["result"]["plan_id"]
        self.assertIn("could not be carried", approval["progress"]["review"]["carried"]["not_confirmed"])
        self.assertEqual(("finalization" in approval["result"], "rubric_check" in approval["result"], [r["status"] for r in self.checks_of(plan_id)]), (False, False, ["pending"]))
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rubric_quality_reviews").fetchone()[0], 0)
        self.assertEqual(self.frq.rubric_quality(plan_id)["review"], None)

    def test_a_rebuild_carries_the_check_never_the_confirmation_and_a_finalized_quiz_is_still_rebuilt(self):
        real = quiz_latex.render  # approved while Faculty still printed the old single corner code, so a rebuild has work to do
        with patch("document_identity.quiz_latex.render", side_effect=lambda quiz, letter: real(quiz, letter).replace("[letterpaper,12pt]", "[letterpaper,12pt,pagecode]", 1)):
            draft = self.draft()
            revision, _ = self.revision(draft["id"])
            approval = self.approve(draft["id"], revision, save_keys=True, finalization=self.finalize(revision))
        plan_id, done = approval["result"]["plan_id"], approval["result"]
        self.assertEqual(self.frq.rubric_quality(plan_id)["state"], "confirmed")
        # The confirmation the approval recorded is the approval's own: the quiz is rebuilt although the plan holds its events.
        asked = len(self.host.requests)
        self.frq.rebuild_quiz_approval(draft["id"], approval["id"], {"request_id": str(uuid.uuid4()), "confirmation": "rebuild"})
        self.frq.process_cloud()
        approved = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(approved["rebuilds"][-1]["status"], "complete", approved["rebuilds"][-1]["error"])
        result = approved["result"]
        self.assertFalse(set(result["questions"].values()) & set(done["questions"].values()))
        # The new questions read as the check read them (the PDFs are new, and are not compared): the check is carried, unasked ...
        self.assertFalse([r for r in self.host.requests[asked:] if prompt_name(r["body"]).startswith("rubric_quality")])
        first, second = self.checks_of(plan_id)
        self.assertEqual((second["request_id"].split(":")[0], second["status"], result["rubric_check"]), ("quiz-rebuild-review", "complete", second["id"]))
        self.assertNotEqual(first["input_sha256"], second["input_sha256"])
        status = self.frq.rubric_quality(plan_id)
        self.assertEqual({q["question_id"] for q in status["check"]["result"]["questions"]}, set(result["questions"].values()))
        # ... never the confirmation: the rebuilt quiz waits for the instructor's one tick, and no batch takes its questions.
        self.assertEqual((status["state"], status["review"], status["check"]["id"]), ("needs_review", None, second["id"]))
        finalization, held = self.frq.batch_finalization(status, status["context"]["questions"])
        self.assertEqual((finalization, len(held)), (None, 4))
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rubric_quality_reviews WHERE assignment_id=?", (plan_id,)).fetchone()[0], 1)
        self.assertEqual(result["finalization"], done["finalization"])  # the approval's own, by which a later rebuild knows its events
        # Their own confirmation in Key & Rubric Finalization is their work on the questions: from then on the quiz is not rebuilt.
        self.frq.confirm_rubric_quality(plan_id, {"version": status["version"], "check_id": second["id"], "input_sha256": status["input_sha256"], "confirm_review": True,
                                                  "questions": copy.deepcopy(status["check"]["result"]["questions"])})
        with self.assertRaisesRegex(Conflict, "changes of your own"):
            self.frq.rebuild_quiz_approval(draft["id"], approval["id"], {"request_id": str(uuid.uuid4()), "confirmation": "rebuild"})

    def test_a_check_that_fails_is_a_warning_and_the_approval_queues_the_plans_check(self):
        self.refuse = {"2"}
        draft = self.draft()
        revision, asked = self.revision(draft["id"])
        self.assertEqual([self.about(payload_of(b)) for b in self.checks].count("2"), 2)  # refused, asked once more, refused
        check = revision["result"]["rubric_check"]
        self.assertEqual((check["status"], sorted(check)), ("failed", ["error", "rule", "status"]))
        self.assertIn("1 of 2 problems could not be checked", check["error"])
        [warning] = [w for w in revision["result"]["warnings"] if "could not be checked" in w]
        self.assertTrue(warning.startswith("The rubrics could not be checked while the quiz was drafted (1 of 2 problems") and warning.endswith("once the quiz is approved."))
        self.assertEqual(revision["summary"]["rubric_check"], {"status": "failed"})
        # Nothing to answer, nothing to confirm with the approval; approving behaves as it always did.
        refused, _ = self.answered(draft["id"], {**revision, "result": {"rubric_check": {"sha256": None}}}, {})
        self.assertEqual(refused.status_code, 409)
        with self.assertRaisesRegex(Conflict, "not the one this revision holds"):
            self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": revision["id"], "confirmation": "approve", "key": "chapter-3-quiz",
                                                      "title": "Chapter 3 Quiz", "sections": [self.sections[501]], "copies": {"A": 2, "B": 1}, "finalization": {"confirm": True, "check_sha256": ""}})
        approval = self.approve(draft["id"], revision, save_keys=True)
        [row] = self.checks_of(approval["result"]["plan_id"])
        self.assertEqual((row["request_id"], row["status"], "carried" in approval["progress"]["review"]), (f"quiz-review:{revision['id']}", "pending", False))

    def test_the_switch_off_leaves_the_check_to_the_approval_as_before(self):
        """(workspace `draft_rubric_check`, on by default): off in the settings a revision
        was requested with, the revision ends with its rubrics and keys and the approval queues the plan's check."""
        def switch(on):
            settings = self.frq.settings()
            self.assertIs(settings["settings"]["draft_rubric_check"], not on)  # on until it is turned off
            self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "draft_rubric_check": on}})
        switch(False)
        draft = self.draft()
        first, asked = self.revision(draft["id"])
        self.assertEqual((self.checks, "rubric_check" in first["result"], "rubric_check" in first["summary"]), ([], False, False))
        self.assertIn("answer_key_read", [prompt_name(b) for b in asked])  # everything before the check is as it was
        self.assertFalse([w for w in first["result"]["warnings"] if "could not be checked" in w])  # a choice, never a warning
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_cloud_calls WHERE prompt LIKE 'rubric_quality%'").fetchone()[0], 0)
        # Nothing to answer on the draft.
        refused, _ = self.answered(draft["id"], {**first, "result": {"rubric_check": {"sha256": None}}}, {})
        self.assertEqual(refused.status_code, 409)
        # On again: the next revision checks every problem, since the revision it is based on holds no result to keep.
        switch(True)
        second, _ = self.revision(draft["id"], first["id"])
        self.assertEqual((sorted(self.about(payload_of(b)) for b in self.checks), second["result"]["rubric_check"]["status"]), (["1", "2"], "complete"))
        # Off again: a revision asked for now holds no check, the one before keeps its own, and approving queues the plan's check.
        switch(False)
        self.checks.clear()
        third, _ = self.revision(draft["id"], second["id"])
        self.assertEqual((self.checks, "rubric_check" in third["result"]), ([], False))
        self.assertEqual(next(r for r in self.frq.quiz_draft(draft["id"])["revisions"] if r["id"] == second["id"])["summary"]["rubric_check"]["status"], "complete")
        with self.assertRaisesRegex(Conflict, "not the one this revision holds"):
            self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": third["id"], "confirmation": "approve", "key": "chapter-3-quiz",
                                                      "title": "Chapter 3 Quiz", "sections": [self.sections[501]], "copies": {"A": 2, "B": 1}, "finalization": {"confirm": True, "check_sha256": ""}})
        approval = self.approve(draft["id"], third, save_keys=True)
        [row] = self.checks_of(approval["result"]["plan_id"])
        self.assertEqual((row["request_id"], row["status"], "carried" in approval["progress"]["review"]), (f"quiz-review:{third['id']}", "pending", False))

    def test_a_quiz_with_one_version_is_checked_one_question_per_request_and_finalized_the_same_way(self):
        draft = self.frq.create_quiz_draft({"request_id": str(uuid.uuid4()), "term_id": self.term["id"], "title": "Chapter 3 Quiz", "total": 5, "versions": 1,
                                            "mode": "positive", "instructions": "Sections 3.1-3.3: one multiple choice, one free response.", "examples": []},
                                           [("notes.txt", b"Linear programming: corner points, bounded regions.")])
        revision, asked = self.revision(draft["id"])
        # A plan without versions asks each question alone (GRADING "ONE QUESTION PER REQUEST"), and so does its draft.
        self.assertEqual([prompt_name(b) for b in asked][-2:], ["rubric_quality"] * 2)
        check = revision["result"]["rubric_check"]
        self.assertEqual((check["result"]["mode"], sorted(check["questions"]), [q["exam_version"] for q in check["view"]]), ("per_question", ["draft:1:A", "draft:2:A"], ["", ""]))
        options = self.frq.quiz_approval_options(draft["id"])
        self.frq.approve_quiz_draft(draft["id"], {"request_id": str(uuid.uuid4()), "revision_id": revision["id"], "confirmation": "approve", "key": options["assignment"]["key"],
                                                  "title": "Chapter 3 Quiz", "sections": [self.sections[501]], "copies": {"A": 2}, "save_keys": True, "finalization": self.finalize(revision)})
        self.frq.process_cloud()
        approval = self.frq.quiz_draft(draft["id"])["approved"]
        self.assertEqual(approval["status"], "complete", approval.get("error"))
        status = self.frq.rubric_quality(approval["result"]["plan_id"])
        self.assertEqual((status["state"], status["check"]["result"]["mode"], len(status["standing"])), ("confirmed", "per_question", 2))


if __name__ == "__main__":
    unittest.main()
