"""Tools a drafting model may call while it answers on a Claude or ChatGPT subscription connection (grading/providers/tools.py,
grading/drafting_tools.py, the quiz writer's tools in grading/quiz_drafts.py and the question bank reader's in question_bank/runs.py;
requirement, 2026-10-01).

No subscription is used. The relay is spoken to over MCP as Claude Code and Codex speak to it; the fake binaries start it from the very
flags Faculty gives them and call a tool through it; and in the features' own tests a scripted subscription connection plays the model,
calling each request's tools in place through the real provider's request path, so receipts, fingerprints and validation are Faculty's
own. LaTeX compiles and PDFs are read in the real document sandbox."""
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from grading import config
from grading.drafting_tools import covered_sections, rubric_tools, undoubled
from grading.providers import agent_sdk, codex_sdk, tools
from grading.providers.cloud import provider
from grading.quiz_drafts import compiled_from
from rosters import connect
import fake_codex
from fake_mcp import call_tools
from fake_openai import survey_answer, write_answer
from test_agent_sdk import PROBE_SCHEMA, FakeBinary, quick_bundles, subscription
from test_course_notes import OUTLINE, PAGES, notes_pdf
from test_grading_cloud import endpoint
import test_frq_grading as grading_fixture
import test_grading_rubric_drafts as draft_fixture
import test_grading_rubric_quality as quality_fixture
import test_quiz_drafts as quiz_fixture

TESTS = Path(__file__).resolve().parent
TOOLED = {"kind": "agent_sdk"}


def png(width=60, height=80, color="white"):
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


def toy(budget=2):
    def add(arguments):
        if not all(type(arguments.get(k)) is int for k in ("a", "b")):
            raise tools.ToolError("a and b are whole numbers.")
        return str(arguments["a"] + arguments["b"]), [png()]
    return tools.ToolSet("toy-v1", [tools.Tool("add", "Adds two whole numbers.", {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                                                                                   "required": ["a", "b"]}, add)], budget=budget, guide="Add numbers.")


class Scripted(agent_sdk.AgentSdkEndpoint):
    """The Claude subscription connection with its binary replaced by `script(prompt, payload, session, instruction)`, which plays the
    model: it may call the request's tools in place (the same session the relay serves) and returns the structured answer."""
    script = None

    def submit(self, request):
        self.request = request
        return super().submit(request)

    def _run(self, instruction, content, schema, bundled=0, tools=None):
        text = content[0]["text"]
        output = type(self).script(self.request["prompt"], json.loads(text[text.index("{"):]), tools, instruction)
        return ({"subtype": "success", "structured_output": output, "result": "", "session_id": "scripted", "num_turns": 1, "duration_ms": 1,
                 "usage": {"input_tokens": 1, "output_tokens": 1}, "modelUsage": {self.model: {}}}, [])


def scripted(case, frq, script):
    """Every agent_sdk connection of `frq` answers through `script`; other connections are unchanged."""
    real = frq.cloud_provider
    patcher = patch.object(Scripted, "script", staticmethod(script))
    patcher.start()
    case.addCleanup(patcher.stop)
    patcher = patch.object(frq, "cloud_provider", side_effect=lambda e, timeout=180: Scripted(e) if e["kind"] == agent_sdk.KIND else real(e, timeout))
    patcher.start()
    case.addCleanup(patcher.stop)


def receipts(path, column, owner):
    with connect(path) as db:
        return {row["step"]: json.loads(row["receipt_json"] or "{}").get("result", {}).get("metadata", {})
                for row in db.execute(f"SELECT step, receipt_json FROM frq_cloud_calls WHERE {column}=? AND state='completed' ORDER BY rowid", (owner,))}


class Relay(unittest.TestCase):
    def test_only_subscription_connections_are_offered_tools_and_the_environment_can_switch_them_off(self):
        self.assertTrue(tools.offered({"kind": "agent_sdk"}) and tools.offered({"kind": "codex_sdk"}))
        self.assertFalse(tools.offered({"kind": "hosted"}) or tools.offered({"kind": "dedicated"}))
        with patch.dict(os.environ, {"FACULTY_DRAFTING_TOOLS": "0"}):
            self.assertFalse(tools.offered({"kind": "agent_sdk"}))

    def test_the_relay_serves_the_tools_on_a_private_socket_and_each_call_is_recorded_within_the_budget(self):
        session = toy().session()
        with tempfile.TemporaryDirectory() as folder, session.serving(folder) as path:
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            found = call_tools(sys.executable, [str(tools.PROXY), "--socket", path],
                               [("add", {"a": 2, "b": 3}), ("add", {"a": "x"}), ("missing", {}), ("add", {"a": 1, "b": 1})])
        self.assertEqual(found["listed"], ["add"])
        first, refused, missing, spent = found["results"]
        self.assertEqual((first["text"], first["error"], first["images"], first["mime"]), ("5", False, 1, ["image/png"]))
        self.assertTrue(refused["error"] and "whole numbers" in refused["text"])
        self.assertTrue(missing["error"] and "no tool named missing" in missing["text"])
        self.assertTrue(spent["error"] and "No tool calls are left" in spent["text"])
        receipt = session.receipt()
        self.assertEqual(receipt["set"], "toy-v1")
        self.assertEqual([(c["tool"], c["ok"]) for c in receipt["calls"]], [("add", True), ("add", False), ("missing", False), ("add", False)])
        self.assertEqual((receipt["calls"][0]["arguments"], receipt["calls"][0]["answer"], len(receipt["calls"][0]["images"])), ({"a": 2, "b": 3}, "5", 1))
        self.assertEqual(len(toy().session().trace), 0)  # each request starts with its whole budget

    def test_a_tool_set_s_fingerprint_names_its_tools_budget_and_guide(self):
        self.assertEqual(toy().fingerprint, toy().fingerprint)
        self.assertNotEqual(toy().fingerprint, toy(budget=3).fingerprint)


class ClaudeWithTools(FakeBinary):
    def test_a_request_with_tools_runs_claude_with_only_faculty_s_relay_and_its_receipt_keeps_each_call(self):
        self.control.write_text(json.dumps({"script": "", "tool_calls": [["add", {"a": 2, "b": 3}]]}))
        toolset = toy()
        request = {"prompt": "probe", "payload": {"task": "Add 17 and 25."}, "images": [], "schema": PROBE_SCHEMA}
        # A drafting request never joins a bundle, even on a bundling connection.
        answer = provider(subscription()).submit({**request, "tools": toolset, "bundle": {"key": ["job", "q", "A", "s"]}})
        self.assertEqual(answer["result"]["output"]["sum"], 42)
        run = self.invocations()[0]
        self.assertFalse(run["bundled"])
        self.assertFalse(run["safe_mode"])  # safe mode would drop the MCP server; CLAUDE.md and memory are off by environment instead
        self.assertEqual((run["tools"], run["allowed"]), ("", "mcp__faculty_drafting"))
        self.assertIn("CLAUDE_CODE_DISABLE_CLAUDE_MDS", run["env"])
        self.assertIn("CLAUDE_CODE_DISABLE_AUTO_MEMORY", run["env"])
        self.assertEqual(run["mcp"]["listed"], ["add"])
        self.assertEqual((run["mcp"]["results"][0]["text"], run["mcp"]["results"][0]["images"]), ("5", 1))
        self.assertIn("TOOLS. Add numbers. You may make at most 2 tool calls", run["system"])
        receipt = answer["result"]["metadata"]["tools"]
        self.assertEqual((receipt["set"], receipt["fingerprint"], [c["tool"] for c in receipt["calls"]]), ("toy-v1", toolset.fingerprint, ["add"]))

        # The same request without tools: safe mode, no MCP server, no receipt of tools, and another fingerprint.
        plain = provider(subscription()).submit(request)
        run = self.invocations()[1]
        self.assertTrue(run["safe_mode"])
        self.assertIsNone(run["mcp"])
        self.assertNotIn("tools", plain["result"]["metadata"])
        self.assertNotEqual(plain["result"]["metadata"]["request_sha256"], answer["result"]["metadata"]["request_sha256"])


class CodexWithTools(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.binary, self.control = fake_codex.install(folder.name, TESTS)
        for target, value in (("cli", lambda: self.binary), ("signed_in", lambda: True)):
            patcher = patch.object(codex_sdk, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        quick_bundles(self)
        agent_sdk.AgentSdkEndpoint._paused.clear()
        agent_sdk.AgentSdkEndpoint._busy.clear()

    def runs(self):
        return [json.loads(line) for line in Path(str(self.control) + ".log").read_text().splitlines()]

    def test_a_request_with_tools_runs_codex_with_only_faculty_s_relay_approved_and_code_mode_on(self):
        self.control.write_text(json.dumps({"script": "", "tool_calls": [["add", {"a": 4, "b": 5}]]}))
        e = config.endpoint_entry({"name": "chatgpt", "kind": "codex_sdk", "model": "gpt-6-astra"}, saving=True)
        request = {"prompt": "probe", "payload": {"task": "Add 17 and 25."}, "images": [], "schema": PROBE_SCHEMA}
        answer = provider(e).submit({**request, "tools": toy()})
        self.assertEqual(answer["result"]["output"]["sum"], 42)
        run = self.runs()[0]
        self.assertEqual(run["mcp"]["results"][0]["text"], "9")
        self.assertEqual(list(run["servers"]), ["faculty_drafting"])
        self.assertTrue(run["servers"]["faculty_drafting"]["args"][0].endswith("tool_proxy.py"))
        self.assertIn('mcp_servers.faculty_drafting.default_tools_approval_mode="approve"', run["config"])
        self.assertIn('approval_policy="never"', run["config"])
        self.assertTrue(run["ignore_user_config"])  # the instructor's own MCP servers are never loaded
        self.assertNotIn("code_mode_host", run["disabled"])  # this Codex calls MCP tools through code mode
        for tool in ("shell_tool", "unified_exec", "apps", "plugins", "browser_use", "computer_use", "multi_agent"):
            self.assertIn(tool, run["disabled"])
        self.assertEqual([c["tool"] for c in answer["result"]["metadata"]["tools"]["calls"]], ["add"])
        provider(e).submit(request)
        run = self.runs()[1]
        self.assertIn("code_mode_host", run["disabled"])
        self.assertEqual(run["servers"], {})


class QuizWriterTools(unittest.TestCase):
    """A quiz drafted on a subscription connection: the writer reads the course's notes and bank and compiles and looks before it
    answers, the rubric writer has the course's context; the check and the hosted path are unchanged."""
    answer = quiz_fixture.QuizDrafts.answer
    draft = quiz_fixture.QuizDrafts.draft

    def setUp(self):
        quiz_fixture.QuizDrafts.setUp(self)
        current = self.frq.settings()
        self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "endpoints": [
            endpoint(self.host, "alpha-vision"), subscription("claude-sub")], "rubric_reviewer": "claude-sub"}})
        self.seen, self.called = [], {}
        scripted(self, self.frq, self.model)

    def model(self, prompt, payload, session, instruction):
        self.seen.append((prompt, session and sorted(session.toolset.tools), "\n\nTOOLS. " in instruction))
        if prompt == "quiz_write":
            quiz = {"base": "none", **quiz_fixture.quiz_answer(payload)}
            # After the first whole compile only the change is sent, and the answer names the last compile (2026-10-01).
            content = copy.deepcopy(quiz["problems"][1]["versions"][1])
            content["statement"] += " Use the graph."
            edit = {"problem": 2, "version": "B", "content": content, "page": None, "space": None, "tag": None, "rubric_feedback": None}
            self.called["context"] = [session.call("lecture_notes", {}), session.call("question_bank", {"query": "solve the equation"})]
            self.called["write"] = [session.call("compile_quiz", {"base": "none", "problems": []}), session.call("look_at_pages", {"version": "A", "pages": [1]}),
                                    session.call("compile_quiz", quiz), session.call("look_at_pages", {"version": "B", "pages": [1]}),
                                    session.call("compile_quiz", {"base": "compiled", "sections_line": "", "directions": "", "problems": [], "edits": [edit]})]
            return {"base": "compiled", "sections_line": "", "directions": "", "problems": [], "edits": [], "notes": "", "changes": ""}
        if prompt == "quiz_check":
            return {"issues": [], "notes": ""}
        if prompt == "quiz_rubric":
            self.called.setdefault("rubric", []).append([
                session.call("look_closer", {"version": "B", "page": 1, "region": {"left": 0, "top": 0, "right": .5, "bottom": .4}}),
                session.call("look_closer", {"version": "B", "page": 1, "region": {"left": 0, "top": 0, "right": .01, "bottom": .4}}),
                session.call("lecture_notes", {})])
            return quiz_fixture.rubric_answer(payload)
        if prompt == "answer_key_read":  # each problem's key, read once after the rubrics (2026-10-11, "Carry the key through"): no tools
            return quiz_fixture.key_answer(payload)
        if prompt in ("rubric_quality_versions", "rubric_quality"):  # the rubric check a revision ends with (2026-10-11): it finds nothing
            return quiz_fixture.review_answer(payload)
        raise AssertionError(prompt)

    def test_the_writer_compiles_and_looks_before_it_answers_and_the_receipt_keeps_what_it_did(self):
        draft = self.draft()
        self.frq.process_cloud()
        draft = self.frq.quiz_draft(draft["id"])
        self.assertTrue(draft["latest_complete"], draft)
        refused, early, compiled, looked, edited = self.called["write"]
        self.assertTrue(refused[1] and "Faculty would refuse this quiz before compiling it" in refused[0])
        self.assertTrue(early[1] and "compile the quiz with compile_quiz first" in early[0])
        self.assertFalse(compiled[1])
        self.assertIn("Version A: 1 page.", compiled[0])
        self.assertIn("Problem 2 (free response, planned for page 1): page 1", compiled[0])
        self.assertIn("cm left to write in", compiled[0])
        self.assertIn("No faults", compiled[0])
        self.assertEqual((looked[1], [image["type"] for image in looked[2]]), (False, ["image/jpeg"]))
        self.assertFalse(edited[1])
        self.assertIn("No faults", edited[0])
        # The answer was the last compile: Version B of problem 2 carries the edit, every other version is as first compiled.
        result = next(r for r in draft["revisions"] if r["id"] == draft["latest_complete"])["result"]
        statements = {letter: c["statement"] for letter, c in result["quiz"]["problems"][1]["content"].items()}
        self.assertTrue(statements["B"].endswith(" Use the graph.") and not statements["A"].endswith(" Use the graph."), statements)
        tooled = {prompt: names for prompt, names, _ in self.seen}
        self.assertEqual(tooled["quiz_write"], ["compile_quiz", "lecture_notes", "look_at_pages", "question_bank"])
        notes, bank = self.called["context"]  # the draft's course: no notes uploaded, and nothing in its bank yet
        self.assertTrue(notes[1] and "no lecture notes" in notes[0])
        self.assertEqual((bank[1], bank[0]), (False, "No question in this course's bank matches those words."))
        self.assertIsNone(tooled["quiz_check"])  # the check reads the printed pages it is sent, as before
        self.assertEqual(tooled["quiz_rubric"], ["lecture_notes", "look_closer", "past_rubrics", "question_bank"])
        self.assertTrue(all(preface == bool(names) for _, names, preface in self.seen))
        for look, tiny, notes in self.called["rubric"]:
            self.assertEqual((look[1], len(look[2])), (False, 1))
            self.assertTrue(tiny[1] and "at least 5%" in tiny[0])
            self.assertTrue(notes[1] and "no lecture notes" in notes[0])
        steps = receipts(self.path, "quiz_revision_id", draft["latest_complete"])
        write = steps["quiz:write"]["tools"]
        self.assertEqual((write["set"], [c["tool"] for c in write["calls"]], [c["ok"] for c in write["calls"]]),
                         ("quiz-writer-v3", ["lecture_notes", "question_bank", "compile_quiz", "look_at_pages", "compile_quiz", "look_at_pages", "compile_quiz"],
                          [False, True, False, False, True, True, True]))
        # The receipt keeps each compile whole, so a replayed answer naming the last compile rebuilds the very same quiz.
        rebuilt = compiled_from({"tools": write}, None, ["A", "B"])
        self.assertEqual([v["statement"] for v in rebuilt["problems"][1]["versions"]], [statements["A"], statements["B"]])
        self.assertNotIn("tools", steps["quiz:check"])
        self.assertEqual(steps["quiz:rubric:P1"]["tools"]["set"], "rubric-writer-v1")


class RubricDraftTools(unittest.TestCase):
    """Rubrics drafted on a subscription connection: the survey may look closer, each writer has the course's context too."""
    managed = False
    document_version = draft_fixture.RubricDrafts.document_version
    ready_batch = draft_fixture.RubricDrafts.ready_batch
    reviewed_source = draft_fixture.RubricDrafts.reviewed_source
    upload = draft_fixture.RubricDrafts.upload
    uploaded_questions = draft_fixture.RubricDrafts.uploaded_questions
    template = draft_fixture.RubricDrafts.template
    configure = draft_fixture.RubricDrafts.configure
    draft = draft_fixture.RubricDrafts.draft

    def setUp(self):
        draft_fixture.RubricDrafts.setUp(self)
        current = self.frq.settings()
        self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "endpoints": [
            endpoint(self.host, "alpha-vision"), subscription("claude-sub")], "rubric_reviewer": "claude-sub"}})
        self.seen = []
        scripted(self, self.frq, self.model)

    def model(self, prompt, payload, session, instruction):
        names = session and sorted(session.toolset.tools)
        found = session.call("look_closer", {"page": 1, "region": {"left": .1, "top": .1, "right": .6, "bottom": .5}}) if session else None
        self.seen.append((prompt, names, found))
        return survey_answer(("18", "19")) if prompt == "rubric_survey" else write_answer(payload)

    def test_the_survey_may_look_closer_and_each_writer_also_reads_the_course_s_context(self):
        generation = self.draft()
        self.assertEqual(generation["status"], "complete", generation)
        by_prompt = {}
        for prompt, names, found in self.seen:
            by_prompt.setdefault(prompt, []).append(names)
            self.assertEqual((found[1], len(found[2])), (False, 1))
        self.assertEqual(by_prompt["rubric_survey"], [["look_closer"]])
        self.assertEqual(by_prompt["rubric_write"], [["lecture_notes", "look_closer", "past_rubrics", "question_bank"]] * 2)
        steps = receipts(self.path, "rubric_generation_id", generation["id"])
        self.assertEqual(steps["rubric_draft:survey"]["tools"]["set"], "rubric-survey-v1")
        self.assertEqual(steps["rubric_draft:q18"]["tools"]["calls"][0]["tool"], "look_closer")

    def test_a_hosted_drafting_model_gets_no_tools(self):
        self.configure("alpha-vision")
        generation = self.draft()
        self.assertEqual(generation["status"], "complete")
        self.assertEqual(self.seen, [])
        self.assertTrue(all("tools" not in step for step in receipts(self.path, "rubric_generation_id", generation["id"]).values()))


class RubricCheckTools(quality_fixture.QualityCase):
    """The rubric check on a subscription connection (requirement, 2026-10-01): the reviewer
    may look closer at the blank page its question is printed on and read the course's context, never the checked assignment's own
    approved rubrics."""
    def setUp(self):
        super().setUp()
        current = self.frq.settings()
        endpoints = [endpoint(self.host, "alpha-vision"), endpoint(self.host, "beta-text", modality="text"), endpoint(self.host, "gamma-vision"), subscription("claude-sub")]
        self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "endpoints": endpoints, "rubric_reviewer": "claude-sub"}})
        self.seen = []
        scripted(self, self.frq, self.model)

    def model(self, prompt, payload, session, instruction):
        calls = [session.call("look_closer", {"page": 7, "region": {"left": 0, "top": .5, "right": 1, "bottom": .8}}),
                 session.call("look_closer", {"page": 9}), session.call("past_rubrics", {"query": "solve the equation"}),
                 session.call("lecture_notes", {})] if session else []
        self.seen.append((prompt, session and sorted(session.toolset.tools), calls, "\n\nTOOLS. " in instruction))
        return quality_fixture.suggestion(payload)

    def test_the_reviewer_looks_closer_and_reads_the_course_but_never_the_checked_assignment_s_own_rubrics(self):
        area = {"template_id": "blank-exam", "page": 7, "region": {"x": .02, "y": .565, "width": .96, "height": .16}}
        reference = self.frq.rubric_reference

        def confirmed(db, aid):  # as after the answer-area step: the question is printed on a confirmed blank exam
            found = reference(db, aid)
            found[self.qid]["printed"] = [area]
            return found
        crops = {"metadata": {}, "images": {k: png() for k in ("original", "grayscale", "contrast", "ink", "cloud")}}
        with patch.object(self.frq, "rubric_reference", side_effect=confirmed), patch.object(self.frq.templates, "read", return_value=(png(400, 500), "image/png")), \
                patch("grading.rubric_quality.TemplatePages", lambda templates, template_id: [png(400, 500)] * 8), patch("grading.extraction.extract", return_value=crops):
            checked = self.check()
        self.assertEqual(checked["state"], "needs_review")
        [(prompt, names, (look, beyond, rubrics, notes), preface)] = self.seen
        self.assertEqual((prompt, names, preface), ("rubric_quality", ["lecture_notes", "look_closer", "past_rubrics", "question_bank"], True))
        self.assertEqual((look[0], look[1], len(look[2])), ("page 7 (the region asked for).", False, 1))
        self.assertTrue(beyond[1] and "from 1 to 8" in beyond[0])
        self.assertIn("No approved rubric", rubrics[0])  # the course's only approved rubric is the one being checked
        self.assertTrue(notes[1])
        steps = receipts(self.path, "rubric_check_id", checked["check"]["id"])
        self.assertEqual(steps["rubric_quality:" + self.qid]["tools"]["set"], "rubric-reviewer-v1")

    def test_a_hosted_reviewer_gets_no_tools(self):
        current = self.frq.settings()
        self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "rubric_reviewer": "beta-text"}})
        self.assertEqual(self.check()["state"], "needs_review")
        self.assertEqual(self.seen, [])


class CourseContext(unittest.TestCase):
    """What the rubric writer's context tools read: the course's notes, bank and approved rubrics, never the drafted assignment's own."""
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric
    managed = False

    def setUp(self):
        grading_fixture.FrqWorkflowTests.setUp(self)
        self.bank, self.notes = self.app.extensions["question_bank"], self.app.extensions["course_notes"]
        with connect(self.app.config["DATABASE"]) as db:
            self.definition, self.assignment, self.title = db.execute("SELECT definition_id, id, title FROM assignments").fetchone()

    def session(self, assignment=None, pages=None):
        return rubric_tools(self.frq, TOOLED, self.definition, pages or {"": [png(), png()]}, assignment_id=assignment).session()

    def test_approved_rubrics_and_bank_questions_of_the_course_match_by_words_except_the_drafted_assignment_s(self):
        text, error, _ = self.session().call("past_rubrics", {"query": "solve the equation"})
        self.assertFalse(error)
        self.assertIn(self.title, text)
        self.assertIn("never values to copy", text)
        self.assertIn("R1 (", text)
        self.assertEqual(self.session().call("past_rubrics", {"query": "solve"})[0], text)
        self.assertIn("No approved rubric", self.session(self.assignment).call("past_rubrics", {"query": "solve"})[0])
        self.assertTrue(self.session().call("past_rubrics", {"query": "the of"})[1])  # no telling words
        self.bank.sync(self.definition)
        text, error, _ = self.session().call("question_bank", {"query": "solve"})
        self.assertFalse(error)
        self.assertIn("Statement: Solve", text)
        self.assertIn("No question", self.session(self.assignment).call("question_bank", {"query": "solve"})[0])
        self.assertIn("No question", self.session().call("question_bank", {"query": "eigenvalues"})[0])

    def test_lecture_notes_list_their_sections_and_read_one_a_few_pages_at_a_time(self):
        self.assertIn("no lecture notes", self.session().call("lecture_notes", {})[0])
        self.notes.upload(self.definition, notes_pdf(PAGES, OUTLINE), "notes.pdf", "notes-1")
        listing, error, _ = self.session().call("lecture_notes", {})
        self.assertFalse(error)
        self.assertIn("1.1 Basic Matrix Operations", listing)
        self.assertIn("topics: Defining a Matrix; Adding and Subtracting Matrices", listing)
        section, error, _ = self.session().call("lecture_notes", {"section": "1.1"})
        self.assertFalse(error)
        self.assertIn("Section 1.1 Basic Matrix Operations, pages 1-2 of 2.", section)
        self.assertIn("[page 1]", section)
        self.assertIn("Objectives", section)
        self.assertTrue(self.session().call("lecture_notes", {"section": "9.9"})[1])
        self.assertTrue(self.session().call("lecture_notes", {"section": "1.1", "page": 7})[1])

    def test_look_closer_enlarges_a_page_or_a_region_of_one_version(self):
        versions = {"A": [png(400, 500, "white")], "B": [png(400, 500, "black")]}
        session = self.session(pages=versions)
        text, error, images = session.call("look_closer", {"version": "B", "page": 1, "region": {"left": .5, "top": .5, "right": 1, "bottom": 1}})
        self.assertEqual((text, error, len(images)), ("Version B, page 1 (the region asked for).", False, 1))
        with Image.open(io.BytesIO(__import__("base64").b64decode(images[0]["data"]))) as picture:
            self.assertEqual(picture.size, (400, 500))  # a quarter of the page, enlarged twice
            self.assertLess(picture.convert("L").getpixel((10, 10)), 30)
        self.assertTrue(session.call("look_closer", {"page": 1})[1])  # which version
        self.assertTrue(session.call("look_closer", {"version": "A", "page": 2})[1])
        self.assertIsNone(rubric_tools(self.frq, {"kind": "hosted"}, self.definition, versions))
        self.assertEqual(sorted(rubric_tools(self.frq, TOOLED, self.definition, versions, survey=True).tools), ["look_closer"])


class BankReaderTools(unittest.TestCase):
    def test_the_pdf_reader_opens_any_page_or_a_region_of_it(self):
        from question_bank.runs import QuestionBankRuns
        pages = [{"number": n, "text": f"Problem {n}. Solve it.", "picture": png(300, 400)} for n in (1, 2, 3)]
        self.assertIsNone(QuestionBankRuns._page_tools(pages, {"kind": "hosted"}))
        session = QuestionBankRuns._page_tools(pages, TOOLED).session()
        text, error, images = session.call("pdf_page", {"page": 3})
        self.assertEqual((error, len(images)), (False, 1))
        self.assertTrue(text.startswith("Page 3 of 3. Its text as the PDF holds it:\nProblem 3."))
        text, error, images = session.call("pdf_page", {"page": 2, "region": {"left": 0, "top": 0, "right": .5, "bottom": .5}})
        self.assertEqual((error, len(images)), (False, 1))
        self.assertIn("(the region asked for)", text)
        self.assertTrue(session.call("pdf_page", {"page": 4})[1])
        self.assertTrue(session.call("pdf_page", {"page": "2"})[1])


class QuizNotesRules(unittest.TestCase):
    """Which notes sections a drafted quiz covers (QUIZZES "THE NOTES THE QUIZ COVERS"), read from what the quiz prints."""
    SECTIONS = [{"number": n} for n in ("2.4", "3.1", "3.2", "3.3", "3.4", "4.1", "4.2", "10.1")]

    def covered(self, *mentions):
        return [part["number"] for part in covered_sections(self.SECTIONS, mentions)]

    def test_a_sections_line_and_tags_name_sections_ranges_and_chapters(self):
        self.assertEqual(self.covered("Sections 3.1–3.3", "Section 3.3", "Section 3.2"), ["3.1", "3.2", "3.3"])
        self.assertEqual(self.covered("Sections 3.1--3.3"), ["3.1", "3.2", "3.3"])
        self.assertEqual(self.covered("Sections 3.4 to 4.1"), ["3.4", "4.1"])
        self.assertEqual(self.covered("Sections 2.4, 3.2 and 4.2"), ["2.4", "3.2", "4.2"])
        self.assertEqual(self.covered("Chapter 3"), ["3.1", "3.2", "3.3", "3.4"])
        self.assertEqual(self.covered("Chapters 2-3", ""), ["2.4", "3.1", "3.2", "3.3", "3.4"])
        self.assertEqual(self.covered("Section 10.1"), ["10.1"])
        self.assertEqual(self.covered("Linear programming", ""), [])
        self.assertEqual(self.covered("Section 13.1", "Section 3.12"), [])

    def test_doubled_math_letters_are_halved_only_when_every_run_is_doubled(self):
        self.assertEqual(undoubled("Maximize 𝑃𝑃 = 4𝑥𝑥 + 3𝑦𝑦, 𝑥𝑥𝑥𝑥 ≥ 0"), "Maximize 𝑃 = 4𝑥 + 3𝑦, 𝑥𝑥 ≥ 0")
        self.assertEqual(undoubled("𝑃𝑃 = 4𝑥 + 3𝑦𝑦"), "𝑃𝑃 = 4𝑥 + 3𝑦𝑦")
        self.assertEqual(undoubled("P = 4x + 3y"), "P = 4x + 3y")


if __name__ == "__main__":
    unittest.main()
