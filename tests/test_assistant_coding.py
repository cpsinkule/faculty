"""Changing Faculty itself (assistant/coding.py, AGENTS.md "Faculty assistant"): a coding task starts only from an Approve card or the
instructor, works in its own clone on its own branch, is read round by round, and Apply merges it into main (rebased when main moved),
pushes and restarts, only with the instructor's confirmation of the exact head they saw. A stand-in coding agent, a scratch repository
and a local "GitHub" remote keep every test away from the real ones; the student tools read one student without paging."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_storage import HEADERS

from app import create_app
from assistant.coding import CodingService
from assistant.transport import narrow

FAKE = r'''
import json, os, pathlib, subprocess, sys, time
control = json.loads(pathlib.Path(CONTROL).read_text())
prompt = sys.stdin.read()
pathlib.Path(CONTROL + ".prompts").open("a").write(json.dumps(prompt) + "\n")
mode = control.get("mode", "commit")
def emit(event): print(json.dumps(event), flush=True)
if mode == "sleep":
    time.sleep(60)
if mode == "fail":
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "It broke."}); sys.exit(1)
target = pathlib.Path(control.get("file", "app.py"))
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text((target.read_text() if target.exists() else "") + control.get("line", "VALUE = 2\n"))
emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": str(target)}}]}})
if mode != "leftover":
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Change it"], check=True)
emit({"type": "result", "subtype": "success", "result": "I changed **" + str(target) + "**.", "total_cost_usd": 0})
'''


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class CodingTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.root, self.remote = base / "live", base / "remote.git"
        git(base, "init", "-q", "--bare", str(self.remote))
        git(base, "init", "-q", "-b", "main", str(self.root))
        for key, value in (("user.name", "Tester"), ("user.email", "t@example.com")):
            git(self.root, "config", key, value)
        # No `.gitignore`, on purpose: whatever Apply's own checks leave in a clone shows here as a change and refuses the Apply.
        (self.root / "app.py").write_text("VALUE = 1\n")
        (self.root / "other.py").write_text("OTHER = 1\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "start")
        git(self.root, "remote", "add", "github", str(self.remote))
        git(self.root, "push", "-q", "-u", "github", "main")
        # The stand-in agent, and a sign-in file for the status check (only its presence is looked at).
        self.control = base / "control.json"
        self.control.write_text(json.dumps({"mode": "commit"}))
        claude = base / "claude"
        claude.write_text(f"#!{sys.executable}\nCONTROL = {str(self.control)!r}\n{FAKE}")
        claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
        (base / "claude-home").mkdir()
        (base / "claude-home" / ".credentials.json").write_text("{}")
        environment = patch.dict(os.environ, {"FACULTY_CLAUDE_CLI": str(claude), "CLAUDE_CONFIG_DIR": str(base / "claude-home")})
        environment.start()
        self.addCleanup(environment.stop)
        self.app = create_app(str(base / "data"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.assistant = self.app.extensions["assistant"]
        self.restarts = []
        self.coding = self.assistant.coding = CodingService(self.assistant, base / "tasks", root=self.root,
                                                            restart=lambda: self.restarts.append(1) or "", systemd=False)

    def start(self, **extra):
        return self.client.post("/api/assistant/code-tasks", json={"request_id": f"r-{time.time_ns()}", "title": "Change the value",
                                                                   "request": "Make VALUE two.", "brief": "It is in `app.py`.", **extra},
                                headers=HEADERS).json

    def wait(self, task_id, state="ready"):
        for _ in range(200):
            found = self.client.get(f"/api/assistant/code-tasks/{task_id}").json
            if found["state"] == state:
                return found
            time.sleep(0.05)
        self.fail(f"The task stayed {found['state']}: {found}")

    def apply(self, task, **extra):
        return self.client.post(f"/api/assistant/code-tasks/{task['id']}/apply",
                                json={"confirmation": "apply", "head": task["head"], "seen": task["seen"], **extra}, headers=HEADERS)

    def test_the_assistant_only_proposes_and_the_card_starts_it(self):
        session = self.assistant.open_session("test")
        text, _ = self.assistant.run_tool(session, "propose_code_change", {"title": "Change the value", "request": "Make VALUE two.", "brief": "In app.py."})
        self.assertEqual(json.loads(text)["outcome"], "waiting_for_approval")
        self.assertEqual(self.coding.listing()["tasks"], [])
        card = self.client.get("/api/assistant/actions").json["pending"][0]
        self.assertEqual(card["endpoint"], "assistant.code_task_start")
        approved = self.client.post(f"/api/assistant/actions/{card['id']}/approve", json={"seen": card["seen"]}, headers=HEADERS).json
        task = self.wait(approved["result"]["id"])
        self.assertEqual([f["path"] for f in task["files"]], ["app.py"])
        prompt = json.loads(Path(str(self.control) + ".prompts").read_text().splitlines()[0])
        self.assertIn("Make VALUE two.", prompt)
        self.assertIn("faculty/code-", prompt)
        for route in ("apply", "discard", "follow-up", "stop"):
            refused = json.loads(self.assistant.run_tool(session, "faculty_write", {"method": "POST", "path": f"/api/assistant/code-tasks/{task['id']}/{route}",
                                                                                  "body": {"confirmation": "apply"}, "summary": "x"})[0])
            self.assertEqual(refused["outcome"], "refused", route)

    def test_a_task_works_in_its_own_clone_and_apply_merges_pushes_and_restarts(self):
        task = self.wait(self.start()["id"])
        clone = Path(task["rounds"][0] and self.coding._task(task["id"])[0]["directory"]) / "repo"
        self.assertEqual(git(clone, "remote"), "")
        self.assertEqual(git(self.root, "rev-parse", "main"), git(self.remote, "rev-parse", "main"))
        self.assertEqual((self.root / "app.py").read_text(), "VALUE = 1\n")
        self.assertEqual(self.apply(task, confirmation="").status_code, 400)
        self.assertEqual(self.apply(task, head="0" * 40).status_code, 409)
        # Main moved meanwhile (another commit): the branch is rebased onto it first.
        (self.root / "other.py").write_text("OTHER = 2\n")
        git(self.root, "commit", "-q", "-am", "meanwhile")
        done = self.apply(task)
        self.assertEqual(done.status_code, 200, done.json)
        self.assertEqual(done.json["state"], "applied")
        self.assertEqual((self.root / "app.py").read_text(), "VALUE = 1\nVALUE = 2\n")
        self.assertEqual((self.root / "other.py").read_text(), "OTHER = 2\n")
        self.assertEqual(git(self.remote, "rev-parse", "main"), git(self.root, "rev-parse", "main"))
        self.assertEqual(self.restarts, [1])
        kinds = [e["kind"] for e in done.json["events"]]
        self.assertEqual(kinds[-3:], ["applied", "pushed", "restart_scheduled"])
        self.assertFalse(clone.exists())
        self.assertEqual(self.apply(task).status_code, 409)

    def test_a_conflict_or_a_busy_workspace_refuses_apply(self):
        task = self.wait(self.start()["id"])
        with patch.object(CodingService, "_busy", return_value={"grading tasks": 3}):
            busy = self.apply(task)
        self.assertEqual(busy.status_code, 409)
        self.assertIn("3 grading tasks", busy.json["error"])
        (self.root / "app.py").write_text("VALUE = 7\n")
        git(self.root, "commit", "-q", "-am", "conflicting")
        refused = self.apply(task)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("conflicts", refused.json["error"])
        self.assertEqual((self.root / "app.py").read_text(), "VALUE = 7\n")
        after = self.client.get(f"/api/assistant/code-tasks/{task['id']}").json
        self.assertEqual((after["state"], after["events"][-1]["kind"]), ("ready", "apply_failed"))
        self.assertEqual(self.restarts, [])

    def test_leftovers_are_committed_migrations_are_named_and_follow_ups_continue(self):
        self.control.write_text(json.dumps({"mode": "leftover", "file": "gradebook/migrations/009_new.sql", "line": "CREATE TABLE x(y);\n"}))
        task = self.wait(self.start()["id"])
        self.assertEqual(task["migrations"], ["gradebook/migrations/009_new.sql"])
        self.assertEqual(len(task["commits"]), 1)
        self.assertIn("left uncommitted", task["commits"][0]["message"])
        self.control.write_text(json.dumps({"mode": "commit", "line": "VALUE = 3\n"}))
        follow = self.client.post(f"/api/assistant/code-tasks/{task['id']}/follow-up", json={"text": "Also make it three."}, headers=HEADERS)
        self.assertEqual(follow.status_code, 202)
        second = self.wait(task["id"])
        self.assertEqual(len(second["rounds"]), 2)
        prompts = Path(str(self.control) + ".prompts").read_text().splitlines()
        self.assertIn("Also make it three.", json.loads(prompts[1]))
        self.assertIn("I changed", json.loads(prompts[1]))

    def test_the_linked_runtimes_never_reach_a_commit_or_main(self):
        """Each clone links the live runtimes; committed and applied, a link would replace the ignored live folder (2026-10-01)."""
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "python").write_text("live\n")
        (self.root / ".git" / "info" / "exclude").write_text(".venv\n")  # as on the live checkout: a clone does not inherit it
        self.control.write_text(json.dumps({"mode": "leftover"}))
        task = self.wait(self.start()["id"])
        clone = Path(self.coding._task(task["id"])[0]["directory"]) / "repo"
        self.assertTrue((clone / ".venv").is_symlink())
        self.assertEqual([f["path"] for f in task["files"]], ["app.py"])
        # A link committed anyway (an agent's own `git add -f`) is refused at Apply, and the live folder stays as it was.
        git(clone, "add", "-f", ".venv")
        git(clone, "-c", "user.name=Tester", "-c", "user.email=t@example.com", "commit", "-q", "-m", "links")
        refused = self.apply(self.client.get(f"/api/assistant/code-tasks/{task['id']}").json)
        self.assertEqual(refused.status_code, 409)
        self.assertIn(".venv", refused.json["error"])
        self.assertEqual((self.root / ".venv" / "python").read_text(), "live\n")
        self.assertEqual(self.restarts, [])

    def test_a_failed_round_stop_and_discard(self):
        self.control.write_text(json.dumps({"mode": "fail"}))
        failed = self.wait(self.start()["id"], "failed")
        self.assertIn("It broke.", failed["rounds"][0]["error"])
        self.assertEqual(self.apply(failed).status_code, 409)
        self.control.write_text(json.dumps({"mode": "sleep"}))
        running = self.wait(self.start()["id"], "running")
        self.assertEqual(self.client.post(f"/api/assistant/code-tasks/{running['id']}/discard", json={}, headers=HEADERS).status_code, 409)
        stopped = self.client.post(f"/api/assistant/code-tasks/{running['id']}/stop", json={}, headers=HEADERS).json
        self.assertEqual(stopped["events"][-1]["kind"], "stopped")
        self.wait(running["id"], "failed")
        gone = self.client.post(f"/api/assistant/code-tasks/{running['id']}/discard", json={}, headers=HEADERS).json
        self.assertEqual(gone["state"], "discarded")


    def test_canvas_writes_refuse_apply_and_show_in_the_restart_check(self):
        """AGENTS.md CHECKS (requirement, 2026-10-17): no fixed morning window; a restart waits only while work runs, the morning
        sync with Canvas included (it registers as a Canvas write while it runs)."""
        task = self.wait(self.start()['id'])
        self.assertEqual(self.client.get('/api/assistant/restart-check').json, {'idle': True, 'busy': {}, 'canvas_writes': []})
        writes = self.app.extensions['canvas'].writes
        writes.begin('morning:term', 'the morning sync with Canvas', 'stopping')
        try:
            check = self.client.get('/api/assistant/restart-check').json
            self.assertEqual(check, {'idle': False, 'busy': {'Canvas writes': 1}, 'canvas_writes': ['the morning sync with Canvas']})
            response = self.apply(task)
            self.assertEqual(response.status_code, 409)
            self.assertIn('Canvas writes', response.json['error'])
        finally:
            writes.end('morning:term')
        self.assertEqual(self.restarts, [])

    def test_symbolic_links_never_reach_main(self):
        """A link merged into the live checkout could expose a credentials file to the source tools (2026-10-09 review)."""
        task = self.wait(self.start()["id"])
        clone = Path(self.coding._task(task["id"])[0]["directory"]) / "repo"
        outside = Path(self.coding.tasks_dir).parent / "sign-in.json"
        outside.write_text('{"password": "synthetic"}')
        (clone / "tools").mkdir()
        (clone / "tools" / "settings.json").symlink_to(outside)
        git(clone, "add", "tools/settings.json")
        git(clone, "-c", "user.name=Tester", "-c", "user.email=t@example.com", "commit", "-q", "-m", "a link")
        refused = self.apply(self.client.get(f"/api/assistant/code-tasks/{task['id']}").json)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("symbolic links (tools/settings.json)", refused.json["error"])
        self.assertFalse((self.root / "tools").exists())
        # The same refusal after a rebase (`_check`), whatever reached the branch first.
        from assistant.coding import GitError
        with self.assertRaisesRegex(GitError, "symbolic links"):
            self.coding._check(clone, git(self.root, "rev-parse", "main"))
        self.assertEqual(self.restarts, [])

    def test_applys_own_checks_leave_the_clone_as_committed_and_a_change_made_during_them_still_refuses(self):
        """Apply compares the clone with the commit it checked. Its compile step left `__pycache__/` there (py_compile writes it
        whatever -B says), hidden only by the real repository's `.gitignore`; this one ignores nothing, so it showed."""
        from assistant.coding import GitError
        task = self.wait(self.start()["id"])
        clone = Path(self.coding._task(task["id"])[0]["directory"]) / "repo"
        main = git(self.root, "rev-parse", "main")
        self.coding._check(clone, main)
        self.assertEqual(git(clone, "status", "--porcelain", "--ignored"), "")

        # A file written into the clone while the checks ran (an agent still editing), or a tracked one edited, is still refused.
        def checks(leave):
            def run(repo, runtime, changed, head, base, progress=None):
                leave(Path(repo))
                return {"head": head, "base": base, "python": [], "javascript": [], "not_run": [], "results": [], "selection_sha256": "x"}
            return run
        for leave in (lambda repo: (repo / "late.py").write_text("LATE = 1\n"), lambda repo: (repo / "app.py").write_text("VALUE = 9\n")):
            with patch("assistant.code_checks.run", side_effect=checks(leave)):
                refused = self.apply(self.client.get(f"/api/assistant/code-tasks/{task['id']}").json)
            self.assertEqual(refused.status_code, 409)
            self.assertIn("code changed during verification", refused.json["error"])
            git(clone, "checkout", "-q", "--", ".")
            (clone / "late.py").unlink(missing_ok=True)
        self.assertEqual(git(self.root, "rev-parse", "main"), main)
        self.assertEqual((self.root / "app.py").read_text(), "VALUE = 1\n")
        # Changed Python that does not compile is refused by its file and line, and that check leaves nothing behind either.
        (clone / "app.py").write_text("VALUE = = 3\n")
        git(clone, "-c", "user.name=Tester", "-c", "user.email=t@example.com", "commit", "-q", "-am", "broken")
        with self.assertRaisesRegex(GitError, r'does not compile: File "app\.py", line 1\n(?s:.*)SyntaxError'):
            self.coding._check(clone, main)
        self.assertEqual(git(clone, "status", "--porcelain", "--ignored"), "")
        refused = self.apply(self.client.get(f"/api/assistant/code-tasks/{task['id']}").json)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("does not compile", refused.json["error"])
        self.assertEqual((self.root / "app.py").read_text(), "VALUE = 1\n")
        self.assertEqual(self.restarts, [])

    def test_apply_reports_its_stage_while_it_runs_and_forgets_it_after(self):
        task = self.wait(self.start()["id"])
        seen = []

        def checks(repo, runtime, changed, head, base, progress=None):
            progress(0, 2)
            seen.append((self.coding.detail(task["id"]).get("applying"),
                         next(t for t in self.coding.listing()["tasks"] if t["id"] == task["id"]).get("applying")))
            return {"head": head, "base": base, "python": [], "javascript": [], "not_run": [], "results": [], "selection_sha256": "x"}
        self.assertNotIn("applying", task)
        with patch("assistant.code_checks.run", side_effect=checks):
            done = self.apply(task)
        self.assertEqual(done.status_code, 200, done.json)
        detail, listed = seen[0]
        self.assertEqual((detail["stage"], detail["done"], detail["commands"]), ("checking", 0, 2))
        self.assertIn("started_at", detail)
        self.assertEqual(listed, detail)
        self.assertNotIn("applying", done.json)
        self.assertNotIn("applying", self.client.get(f"/api/assistant/code-tasks/{task['id']}").json)
        # A refused Apply clears it too: nothing in memory outlives the click.
        second = self.wait(self.start()["id"])
        with patch("assistant.code_checks.run", side_effect=ValueError("ASSISTANT: required checks failed; nothing was applied.")):
            self.assertEqual(self.apply(second).status_code, 409)
        self.assertNotIn("applying", self.client.get(f"/api/assistant/code-tasks/{second['id']}").json)
        self.assertEqual(self.coding._applying, {})

    def test_the_claude_coder_has_claude_codes_tools_settings_and_plugins_and_its_copy_stays_sealed(self):
        # UNLOCKED CODER (requirement, 2026-10-11).
        from assistant.coding import ALLOWED, REFUSED
        folder = Path(self.coding.tasks_dir) / "t"
        (folder / "repo").mkdir(parents=True)
        (folder / "tmp").mkdir()
        task = {"coder": "claude", "model": "", "reasoning": ""}
        command = self.coding._command(task, folder, folder / "repo")
        # The instructor's own settings, plugins and hooks load, and skills are on: nothing switches them off any more.
        for locked in ("--setting-sources", "--disable-slash-commands"):
            self.assertNotIn(locked, command)
        # Its tools are named: subagents, skills, web search and the repository's browser beside files and the shell.
        allowed = command[command.index("--allowedTools") + 1:command.index("--disallowedTools")]
        self.assertEqual(tuple(allowed), ALLOWED)
        for tool in ("Bash", "Edit", "Task", "Agent", "SendMessage", "Skill", "WebSearch", "WebFetch", "mcp__playwright"):
            self.assertIn(tool, ALLOWED)
        # Permission checks stay on (a tool that is not named is refused), and nothing that acts on their account is named.
        for never in ("--dangerously-skip-permissions", "--permission-mode", "--allow-dangerously-skip-permissions"):
            self.assertNotIn(never, command)
        self.assertFalse({"RemoteTrigger", "CronCreate", "ScheduleWakeup", "PushNotification"} & set(ALLOWED))
        # What reaches past its copy stays shut: the refusals, no server of the instructor's account, no kept session.
        refused = command[command.index("--disallowedTools") + 1:command.index("--add-dir")]
        self.assertEqual(tuple(refused), REFUSED)
        for needed in ("Bash(git push:*)", "Bash(git remote add:*)", "Bash(systemctl:*)", "Bash(sudo:*)", "Bash(ssh:*)", "RemoteTrigger"):
            self.assertIn(needed, REFUSED)
        self.assertIn("--strict-mcp-config", command)
        self.assertNotIn("--mcp-config", command)                   # this copy declares no server of its own
        self.assertIn("--no-session-persistence", command)
        # The servers the repository itself declares are its own, and nothing else.
        (folder / "repo" / ".mcp.json").write_text('{"mcpServers": {}}')
        declared = self.coding._command(task, folder, folder / "repo")
        self.assertEqual(declared[declared.index("--mcp-config") + 1], str(folder / "repo" / ".mcp.json"))
        self.assertIn("--strict-mcp-config", declared)
        # Its environment still holds no credentials and only scratch space.
        env = self.coding._environment(folder, folder / "repo")
        self.assertEqual((env["GH_CONFIG_DIR"], env["GIT_CONFIG_GLOBAL"], env["TMPDIR"], env["DATA_DIR"], env["GIT_TERMINAL_PROMPT"]),
                         (str(folder / "gh"), str(folder / "gitconfig"), str(folder / "tmp"), str(folder / "data"), "0"))
        self.assertFalse([key for key in env if any(word in key for word in ("KEY", "SECRET", "TOKEN", "PASSWORD"))])
        # Codex keeps its sandbox and its disabled features.
        with patch.dict(os.environ, {"FACULTY_CODEX_CLI": sys.executable}):
            codex = self.coding._command({"coder": "codex", "model": "", "reasoning": ""}, folder, folder / "repo")
        self.assertEqual(codex[codex.index("--sandbox") + 1], "workspace-write")
        self.assertIn("multi_agent", codex)


class GatewayCoderTest(CodingTest):
    def test_a_gateway_task_runs_codex_with_the_provider_and_hides_the_key_from_commands(self):
        folder = Path(self.coding.tasks_dir) / "t"
        (folder / "tmp").mkdir(parents=True)
        (folder / "provider.json").write_text(json.dumps({"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "env_key": "OPENROUTER_API_KEY"}))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret-value", "FACULTY_CODEX_CLI": sys.executable}):
            command = self.coding._command({"coder": "codex", "model": "deepseek/x", "reasoning": ""}, folder, folder / "repo")
            env = self.coding._environment(folder, folder / "repo")
        self.assertIn('model_providers.faculty_gateway.env_key="OPENROUTER_API_KEY"', command)
        self.assertIn('shell_environment_policy.exclude=["*KEY*","*SECRET*","*TOKEN*"]', command)
        self.assertEqual(command[command.index("-m") + 1], "deepseek/x")
        self.assertEqual(env["OPENROUTER_API_KEY"], "secret-value")
        without = self.coding._environment(self.coding.tasks_dir, Path("repo"))
        self.assertNotIn("OPENROUTER_API_KEY", without)


for _name in [n for n in vars(CodingTest) if n.startswith("test_") and n not in vars(GatewayCoderTest)]:
    setattr(GatewayCoderTest, _name, None)  # CodingTest's own tests run once, above
del _name


class StudentToolsTest(unittest.TestCase):
    def test_narrow_keeps_matching_items_and_everything_else(self):
        value = {"setup": {"items": [{"title": "Quiz 1"}]}, "students": [{"name": "Ann Lee", "row": "s:1"}, {"name": "Bo Frost", "row": "s:2"}]}
        self.assertEqual(narrow(value, "frost"), {"setup": {"items": [{"title": "Quiz 1"}]}, "students": [{"name": "Bo Frost", "row": "s:2"}]})
        self.assertEqual(narrow(value, "nobody"), value)

    def test_find_student_and_student_grades(self):
        from browser_course_pages import seed_course
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(os.path.join(temp, "data"))
            client = app.test_client()
            seed_course(client, sections=[507, 508])
            assistant = app.extensions["assistant"]
            session = assistant.open_session("test")
            gradebook = {"terms": [{"id": "t1", "is_current": True, "code": "MATH 140", "term_label": "Fall 2026", "group_id": "g1"}]}
            view = {"sections": [{"id": "s507", "number": 507}], "setup": {"mode": "points", "categories": [{"id": "c1", "name": "Exams"}],
                    "items": [{"id": "i1", "title": "Exam 1", "points": "100", "category_id": "c1"}, {"id": "i2", "title": "Old", "archived": True}]},
                    "students": [{"row": "s507:111", "section_id": "s507", "section": 507, "name": "Frost, Corbin", "enrollment_id": "1000000111"},
                                 {"row": "s507:222", "section_id": "s507", "section": 507, "name": "Lee, Ann"}],
                    "cells": {"i1": {"s507:111": {"status": "scored", "points": "21"}}},
                    "results": {"students": {"s507:111": {"percent": "21.00", "letter": "F"}}, "blanks": {"s507:222": {"i1": "makeup"}}}}
            answers = {"/api/gradebook": gradebook, "/api/gradebook/terms/t1": view, "/api/gradebook/terms/t1?section=s507": view}
            answers['/api/absences/students'] = {'groups': [{'term_id': 't1', 'code': 'MATH 140', 'term_label': 'Fall 2026',
                'students': [{'name': 'Frost, Corbin', 'section_id': 's507', 'section': 507, 'enrollment_id': '1000000111', 'dropped': False}]}]}
            with patch.object(assistant.transport, "request", side_effect=lambda method, path, body=None: (200, answers[path], "")):
                found = json.loads(assistant.run_tool(session, "find_student", {"query": "corbin frost"})[0])
                grades = json.loads(assistant.run_tool(session, "student_grades", {"term_id": "t1", "row": "s507:111", "section_id": "s507"})[0])
                by_name = json.loads(assistant.run_tool(session, "student_grades", {"term_id": "t1", "name": "ann"})[0])
            self.assertEqual([(m["name"], m["section"], m["page"]) for m in found["matches"]], [("Frost, Corbin", 507, "#course/g1/section/507")])
            self.assertEqual(found["matches"][0]["enrollment_id"], "1000000111")  # INSTRUCTOR'S NOTES: the notes route takes it
            self.assertEqual(grades["overall"], {"percent": "21.00", "letter": "F"})
            self.assertEqual(grades["columns"], [{"column": "Exam 1", "category": "Exams", "points_possible": "100", "due_date": None,
                                                  "value": {"status": "scored", "points": "21"}}])
            self.assertEqual(by_name["student"]["name"], "Lee, Ann")
            self.assertEqual(by_name["columns"][0]["value"], None)
            self.assertEqual(by_name["columns"][0]["empty_cell"], "not counted yet: makeup owed")  # GRADEBOOK "ZERO ONCE DUE"


class SelectionTest(unittest.TestCase):
    """VERIFIED CODE APPLY: a changed file selects its own tests and its package's family, never just test_app (2026-10-09 review)."""
    def repo(self, names, texts=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        (repo / "tests").mkdir()
        for name in names:
            (repo / "tests" / name).write_text((texts or {}).get(name, "import unittest\n"))
        return repo

    def test_packages_select_their_families_and_siblings(self):
        from assistant.code_checks import selection
        repo = self.repo(["test_app.py", "test_catalog_migrations.py", "test_design_language.py", "test_grading_certification.py",
                          "test_grading_pipeline.py", "test_frq_grading.py", "test_gradescope_send.py", "test_certification_automatic.py",
                          "test_inbox.py", "test_inbox_scan_groups.py", "test_scheduling_engine.py", "test_scheduling_clock.py",
                          "test_assistant.py", "test_assistant_workspace.py", "test_assistant_integration.py",
                          "test_assistant_tool_guidance.py", "test_followup_learning.py", "test_seating.py", "test_exam_workflow.py",
                          "test_assistant.mjs", "test_grading_run.mjs", "test_ui.mjs"],
                         {"test_exam_workflow.py": "from teach_inbox.service import InboxService\n",
                          "test_grading_run.mjs": 'import { x } from "../static/grading.js";\n',
                          "test_ui.mjs": 'import { y } from "../static/ui.js";\n'})
        python, _ = selection(repo, ["grading/certification.py"])
        self.assertEqual(python, ["test_certification_automatic", "test_frq_grading", "test_gradescope_send", "test_grading_certification",
                                  "test_grading_pipeline"])
        self.assertEqual(selection(repo, ["teach_inbox/service.py"])[0], ["test_exam_workflow", "test_inbox", "test_inbox_scan_groups"])
        self.assertEqual(selection(repo, ["scheduling/engine.py"])[0], ["test_scheduling_clock", "test_scheduling_engine"])
        python, _ = selection(repo, ["assistant/tool_guidance.py"])
        self.assertIn("test_assistant_tool_guidance", python)
        self.assertIn("test_assistant", python)
        self.assertIn("test_followup_learning", selection(repo, ["assistant/followup_learning.py"])[0])
        python, scripts = selection(repo, ["grading/certification.py", "static/grading.js"])
        self.assertIn("test_grading_certification", python)
        self.assertIn("test_design_language", python)
        self.assertEqual(scripts, ["tests/test_grading_run.mjs"])
        self.assertEqual(selection(repo, ["static/ui.js"])[1], ["tests/test_ui.mjs"])
        self.assertEqual(selection(repo, ["seating.py"])[0], ["test_seating"])
        self.assertEqual(selection(repo, ["docs/decisions/assistant.md"])[0], ["test_app"])

    def test_a_large_family_is_capped_by_priority_and_names_what_did_not_run(self):
        from assistant import code_checks
        names = [f"test_grading_part{n:02}.py" for n in range(60)] + ["test_grading_certification.py", "test_zz_changed.py"]
        repo = self.repo(names)
        chosen = code_checks.plan(repo, ["grading/certification.py", "tests/test_zz_changed.py"])
        self.assertEqual(len(chosen["python"]), code_checks.PYTHON_CAP)
        # The changed test and the file's own test always run; the family fills the rest in name order.
        self.assertIn("test_zz_changed", chosen["python"])
        self.assertIn("test_grading_certification", chosen["python"])
        self.assertEqual(len(chosen["not_run"]), 62 - code_checks.PYTHON_CAP)
        self.assertEqual(chosen["not_run"][0], f"test_grading_part{code_checks.PYTHON_CAP - 2:02}")
        self.assertEqual(code_checks.plan(repo, ["tests/test_zz_changed.py", "grading/certification.py"]), chosen)

    def test_the_actual_tests_folder_maps_grading_inbox_and_scheduling(self):
        from assistant.code_checks import selection
        repo = Path(__file__).resolve().parents[1]
        self.assertIn("test_grading_certification", selection(repo, ["grading/certification.py"])[0])
        self.assertIn("test_certification_automatic", selection(repo, ["grading/certification_automatic.py"])[0])
        self.assertIn("test_inbox", selection(repo, ["teach_inbox/service.py"])[0])
        self.assertIn("test_scheduling_engine", selection(repo, ["scheduling/engine.py"])[0])
        self.assertIn("test_assistant_tool_guidance", selection(repo, ["assistant/tool_guidance.py"])[0])
        self.assertIn("tests/test_assistant.mjs", selection(repo, ["static/assistant-code.js"])[1])


class SourceLinkTest(unittest.TestCase):
    """The source tools never read through a symbolic link (2026-10-09 review: a link could reach a credentials file)."""
    def test_source_files_and_reads_skip_links(self):
        from assistant import tools
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, outside = base / "repo", base / "outside"
            outside.mkdir()
            (outside / "sign-in.json").write_text('{"password": "synthetic"}')
            git(base, "init", "-q", str(root))
            (root / "app.py").write_text("SECRET_FREE = 1  # password lookalike\n")
            (root / "settings.json").symlink_to(outside / "sign-in.json")
            (root / "linked").symlink_to(outside)
            git(root, "add", "-A")
            self.assertEqual(tools.plain_source(root, "app.py"), True)
            for name in ("settings.json", "linked/sign-in.json", "../outside/sign-in.json", "/etc/passwd"):
                self.assertFalse(tools.plain_source(root, name), name)
            app = create_app(str(base / "data"))
            assistant = app.extensions["assistant"]
            assistant._sources = None
            with patch.object(tools, "ROOT", root):
                self.assertEqual(assistant.source_files(), ["app.py"])
                session = assistant.open_session("test")
                with patch.object(assistant, "source_files", return_value=["app.py", "settings.json"]):
                    read = json.loads(assistant.run_tool(session, "read_source", {"path": "settings.json"})[0])
                    found = json.loads(assistant.run_tool(session, "search_source", {"pattern": "password"})[0])
            self.assertIn("error", read)
            self.assertEqual(found["hits"], ["app.py:1: SECRET_FREE = 1  # password lookalike"])


if __name__ == "__main__":
    unittest.main()
