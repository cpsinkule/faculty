"""Changing Faculty itself (requirement, 2026-09-29).

The in-app assistant gathers what a change needs (the page, its data, the source that draws it, the rules in AGENTS.md) and proposes
a coding task: an Approve card showing the brief. The instructor's Approve starts it. Asked, they chose: the coding agent is a setting
(Claude Code or Codex on the subscriptions, `coding_model` in Compute settings, Claude Code by default); it may edit and run commands in
its own copy; and one Apply click merges, restarts and pushes. Since 2026-10-11 the Claude coder has Claude Code's full capabilities
(UNLOCKED CODER, at `ALLOWED`): subagents, skills, plugins, web search and the instructor's settings. Its copy is still sealed.

- Each task works in its OWN clone of the repository (`git clone --shared`, its origin removed), on its own branch, under the task
  folder (`tasks_dir`, on NVMe, never TMPDIR, which each service clears when it starts). It never touches the live checkout, whose
  refs it cannot reach. It runs as a transient systemd unit (`systemd-run --user`), so restarting Faculty does not stop it, with an
  environment holding no service credentials: a scratch TMPDIR and DATA_DIR (so any app it starts is isolated), git with no
  credential helper and no GitHub token, and the checks' scratch inside the task folder.
- Each round (the first request, then each "ask for changes") is a folder with its prompt, the agent's event stream, and its exit
  code. Faculty reads them when asked; nothing polls in the background. A finished round's uncommitted edits are committed by Faculty.
- Apply (the instructor's click, `confirmation: "apply"`, the exact head they saw) is refused while the workspace is busy, rebases the branch on
  the live `main` when it moved, refuses added symbolic links, compiles the changed Python and JavaScript, runs the selected checks
  (`code_checks`; its stage shows as `applying` while it runs), fast-forwards `main` in the live checkout (uncommitted
  work of other sessions stays uncommitted; an overlap refuses), pushes `main` to its upstream and schedules the restart of the web and
  worker services a few seconds later. A new migration file reaches the live catalog within a minute of the merge (the per-minute
  timer builds the app): the task says so before Apply.

Owner: WORKSPACE (`assistant_code_tasks`, append-only `assistant_code_task_events`, migration `assistant/002_code_tasks`), in the
nightly catalog backup; the task folders are runtime work, not teaching data.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from course_storage import timestamp
from grading import config
from grading.providers import agent_sdk, codex_sdk
from request_ids import valid_request_id
from rosters import connect

from .engines import EngineError, gateway, provider_flags
from .store import text as clean_text

ROOT = Path(__file__).resolve().parent.parent
PROMPT = Path(__file__).resolve().parent / "coding_prompt.md"
KINDS = ("round_started", "round_finished", "round_failed", "stopped", "applied", "apply_failed", "pushed", "push_failed",
         "restart_scheduled", "restart_skipped", "discarded", "checks_passed")
RUNNING_LIMIT = 2
DIFF_LIMIT = 400_000
# UNLOCKED CODER (requirement, 2026-10-11). The tools Claude Code
# works with, by name: files and the shell, subagents (`Task`, called `Agent` in newer versions) and talking to them, skills, web
# search and fetch, and the browser the repository itself declares. Permission checks stay ON: a tool that is not named is refused,
# since nobody is there to answer a prompt. Left out on purpose: what acts on the instructor's account or outlives the task
# (scheduled and remote agents, notifications).
ALLOWED = ("Read", "Edit", "MultiEdit", "Write", "Glob", "Grep", "Bash", "TodoWrite", "NotebookEdit",
           "Task", "Agent", "SendMessage", "ListAgents", "TaskStop", "Skill", "ToolSearch", "WebFetch", "WebSearch", "Monitor",
           "Workflow", "EnterWorktree", "ExitWorktree", "mcp__playwright")
# Commands the Claude coding agent is refused outright, whatever else is allowed (Codex runs in its workspace-write sandbox
# instead): only what reaches past its own copy, to the live services, other machines, GitHub, or agents scheduled on the
# instructor's account.
REFUSED = ("Bash(git push:*)", "Bash(git remote add:*)", "Bash(systemctl:*)", "Bash(sudo:*)", "Bash(ssh:*)", "Bash(scp:*)", "Bash(rsync:*)",
           "RemoteTrigger")
SHARED = (".venv", ".tools", "node_modules")  # untracked runtimes the checks need, linked read-only into each clone, never committed
# Apply's compile step, in memory. `python -m py_compile` writes `__pycache__/` beside each file whatever -B or
# PYTHONDONTWRITEBYTECODE say, and Apply refuses a clone its checks did not leave exactly as committed ("code changed during
# verification"); only the repository's own `.gitignore` hid the folder from that comparison. This names each failure as
# py_compile did and writes nothing, so the comparison needs no exception for what the checks themselves produce.
COMPILES = """import sys, traceback
failed = 0
for path in sys.argv[1:]:
    try:
        with open(path, "rb") as source:
            compile(source.read(), path, "exec", dont_inherit=True)
    except Exception as error:
        failed = 1
        sys.stderr.write("".join(traceback.format_exception_only(type(error), error)))
sys.exit(failed)
"""


def _git(repo, *args, check=True, timeout=120):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and result.returncode != 0:
        raise GitError((result.stderr or result.stdout).strip()[-1500:] or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _exclude_shared(repo):
    """Keeps the linked runtimes out of every commit made in a clone: `.gitignore`'s `.venv/` matches folders only, never these links,
    and the live checkout's own exclude file is not cloned. A committed link, fast-forwarded into main, would silently replace the
    ignored live folder with a link to itself (2026-10-01, the first task: caught before Apply)."""
    exclude = Path(repo) / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.exists() else ""
    missing = [f"/{name}" for name in SHARED if f"/{name}" not in text.splitlines()]
    if missing:
        exclude.write_text(text + ("\n" if text and not text.endswith("\n") else "") + "".join(f"{line}\n" for line in missing))


class GitError(Exception):
    pass


def default_restart(delay=3):
    """Restart the web and worker services a few seconds from now, so the answer to the Apply click leaves first. Only under systemd."""
    if not os.environ.get("INVOCATION_ID") or not shutil.which("systemd-run"):
        return "Faculty is not running as a service here: restart it yourself."
    unit = f"faculty-apply-restart-{uuid.uuid4().hex[:8]}"
    subprocess.run(["systemd-run", "--user", "--collect", f"--unit={unit}", f"--on-active={delay}", "systemctl", "--user", "restart",
                    "faculty", "faculty-inbox"], check=True, capture_output=True, timeout=30)
    return ""


class CodingService:
    def __init__(self, service, tasks_dir, *, root=ROOT, restart=default_restart, systemd=None):
        self.service, self.database = service, service.database
        self.root, self.tasks_dir, self.restart = Path(root), Path(tasks_dir), restart
        self.systemd = bool(os.environ.get("INVOCATION_ID") and shutil.which("systemd-run")) if systemd is None else systemd
        self._lock = threading.Lock()
        # Apply's progress, per task, in memory only: set under the lock, cleared in its `finally`, so a restart mid-apply leaves no
        # stale "Applying" behind. {stage: rebasing|checking|merging|pushing|restarting, started_at, modules, scripts, commands, done}.
        self._applying = {}

    # Settings -----------------------------------------------------------------------------------------------------------------
    def coder(self):
        settings = self.service.app.extensions["frq_grading"].settings()["settings"]
        return config.coding_agent(settings)

    def status(self):
        endpoint = self.coder()
        problem, provider = "", None
        try:
            provider = gateway(endpoint)  # an OpenRouter or Vercel model, run by Codex
        except EngineError as error:
            problem = str(error)
        kind = "codex" if endpoint["kind"] == codex_sdk.KIND or endpoint["kind"] == "hosted" else "claude"
        module = codex_sdk if kind == "codex" else agent_sdk
        if not problem and not module.cli():
            problem = f"The {kind} command is not installed for the account Faculty runs as."
        if not problem and not provider and not module.signed_in():
            problem = "Claude Code is not signed in (run `claude` once in a terminal)." if kind == "claude" else "Codex is not signed in (run `codex login`)."
        return {"coder": kind, "model": endpoint.get("model") or "", "name": endpoint.get("name"), "reasoning": endpoint.get("reasoning_choice") or "",
                "available": not problem, "reason": problem, "provider": provider}

    # Starting ---------------------------------------------------------------------------------------------------------------
    def start(self, data):
        """Starts a coding task. Reached only through an Approve card (policy `approve`) or the instructor's own click."""
        with self._lock:
            return self._start(data)

    def _start(self, data):
        data = data if isinstance(data, dict) else {}
        request_id = valid_request_id(data.get("request_id"))
        title = clean_text(data.get("title"), "The title", 200)
        request = clean_text(data.get("request"), "The request", 20000)
        brief = str(data.get("brief") or "").strip()
        if len(brief) > 200000:
            raise ValueError("The brief is too long.")
        page = data.get("page") if isinstance(data.get("page"), str) and data["page"].startswith("#") else ""
        with connect(self.database) as db:
            earlier = db.execute("SELECT id FROM assistant_code_tasks WHERE request_id=?", (request_id,)).fetchone()
        if earlier:
            return self.detail(earlier["id"])
        status = self.status()
        if not status["available"]:
            raise ValueError(status["reason"])
        if sum(1 for task in self.listing()["tasks"] if task["state"] == "running") >= RUNNING_LIMIT:
            raise Conflict(f"{RUNNING_LIMIT} coding tasks are already running. Wait for one to finish.")
        if _git(self.root, "symbolic-ref", "--short", "HEAD") != "main":
            raise Conflict("The live checkout is not on main.")
        task_id = uuid.uuid4().hex
        folder = self.tasks_dir / task_id
        repo, branch = folder / "repo", f"faculty/code-{task_id[:8]}"
        base = _git(self.root, "rev-parse", "main")
        folder.mkdir(parents=True, mode=0o700)
        try:
            _git(folder, "clone", "--quiet", "--shared", "--no-checkout", str(self.root), str(repo), timeout=300)
            _git(repo, "remote", "remove", "origin")
            _git(repo, "checkout", "--quiet", "-b", branch, base)
            for name in SHARED:
                if (self.root / name).exists():
                    (repo / name).symlink_to(self.root / name)
            _exclude_shared(repo)
            for name in ("tmp", "data", "gh"):
                (folder / name).mkdir(mode=0o700)
            name, email = _git(self.root, "config", "user.name", check=False), _git(self.root, "config", "user.email", check=False)
            (folder / "gitconfig").write_text(f"[user]\n\tname = {name or 'Faculty'}\n\temail = {email or 'faculty@localhost'}\n")
            if status["provider"]:
                # The gateway this task's Codex uses, for every round of it (names only: the key stays in the server's environment).
                (folder / "provider.json").write_text(json.dumps(status["provider"]))
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        with connect(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO assistant_code_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (task_id, request_id, title, request, brief, page, status["coder"], status["model"], status["reasoning"], branch,
                        base, str(folder), timestamp()))
        self._launch(task_id, 1, self._first_prompt(title, request, brief, page, branch))
        return self.detail(task_id)

    def _first_prompt(self, title, request, brief, page, branch):
        return (PROMPT.read_text(encoding="utf-8").replace("{branch}", branch)
                + f"\n\n# The change: {title}\n\n## The instructor's request, in their words\n\n{request}\n\n"
                + (f"## Where they were\n\n{page}\n\n" if page else "")
                + (f"## What the assistant gathered\n\n{brief}\n" if brief else ""))

    def follow_up(self, task_id, data):
        """Another round in the same copy: the instructor's new request, with what earlier rounds reported."""
        with self._lock:
            return self._follow_up(task_id, data)

    def _follow_up(self, task_id, data):
        text = clean_text((data or {}).get("text"), "The request", 20000)
        task, events = self._task(task_id)
        rounds = self._rounds(task)
        state = self._state(events, rounds)
        if state in ("running", "applied", "discarded"):
            raise Conflict(f"This task is {state}.")
        if sum(1 for task in self.listing()["tasks"] if task["state"] == "running") >= RUNNING_LIMIT:
            raise Conflict(f"{RUNNING_LIMIT} coding tasks are already running. Wait for one to finish.")
        earlier = "\n\n".join(f"### Round {r['number']}\n\n{r.get('summary') or '(no report)'}" for r in rounds)
        prompt = (PROMPT.read_text(encoding="utf-8").replace("{branch}", task["branch"])
                  + f"\n\n# Continuing: {task['title']}\n\nThe branch already holds the earlier rounds' commits (`git log main..HEAD` "
                  "in this copy shows them). The original request:\n\n" + task["request"]
                  + f"\n\n## What earlier rounds reported\n\n{earlier}\n\n## The instructor now asks\n\n{text}\n")
        self._launch(task_id, len(rounds) + 1, prompt)
        return self.detail(task_id)

    @staticmethod
    def _provider(folder):
        try:
            return json.loads((Path(folder) / "provider.json").read_text())
        except (OSError, ValueError):
            return None

    def _environment(self, folder, repo):
        home = Path.home()
        env = {key: os.environ[key] for key in ("HOME", "LANG", "LC_ALL", "USER", "LOGNAME", "CODEX_HOME", "XDG_RUNTIME_DIR") if key in os.environ}
        path = [str(repo / ".venv" / "bin"), str(repo / ".tools" / "node22" / "bin"), str(home / ".local" / "bin"), "/usr/local/bin", "/usr/bin", "/bin"]
        browser = self.root / ".tools" / "browser-libs" / "usr" / "lib" / "x86_64-linux-gnu"
        env.update(PATH=os.pathsep.join(path), TMPDIR=str(folder / "tmp"), DATA_DIR=str(folder / "data"),
                   FACULTY_STORAGE_CONFIG=str(folder / "no-storage.json"), FACULTY_CHECKS_SCRATCH=str(folder / "tmp" / "checks"),
                   PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=f"{repo}{os.pathsep}{repo / 'tests'}",
                   GIT_CONFIG_GLOBAL=str(folder / "gitconfig"), GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                   GH_CONFIG_DIR=str(folder / "gh"), DISABLE_AUTOUPDATER="1",
                   FACULTY_TEXLIVE_ROOT=str(self.root / ".tools" / "texlive"), LD_LIBRARY_PATH=str(browser),
                   PLAYWRIGHT_BROWSERS_PATH=str(home / ".cache" / "ms-playwright"))
        provider = self._provider(folder)
        if provider and os.environ.get(provider["env_key"]):
            # Codex reads it for its gateway; the commands it runs never see it (`shell_environment_policy` in `_command`).
            env[provider["env_key"]] = os.environ[provider["env_key"]]
        return env

    def _command(self, task, folder, repo):
        if task["coder"] == "codex":
            command = [codex_sdk.cli(), "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--sandbox", "workspace-write",
                       "--cd", str(repo), "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                       "-c", f"sandbox_workspace_write.writable_roots={json.dumps([str(folder / 'tmp'), str(folder / 'data')])}",
                       "-c", "sandbox_workspace_write.network_access=true", "-c", 'shell_environment_policy.inherit="all"',
                       "-c", 'shell_environment_policy.exclude=["*KEY*","*SECRET*","*TOKEN*"]', *provider_flags(self._provider(folder))]
            for feature in ("apps", "plugins", "remote_plugin", "browser_use", "browser_use_external", "browser_use_full_cdp_access",
                            "computer_use", "image_generation", "in_app_browser", "in_app_local_automation", "multi_agent", "hooks"):
                command += ["--disable", feature]
            if task["model"]:
                command += ["-m", task["model"]]
            if task["reasoning"] in config.EFFORTS:
                command += ["-c", f'model_reasoning_effort="{task["reasoning"]}"']
            return command + ["-"]
        # UNLOCKED CODER: Claude Code as the instructor runs it. Their own settings, plugins and hooks load (no `--setting-sources`),
        # skills and slash commands are on, and ALLOWED names its tools. Its copy stays sealed: REFUSED, the scrubbed environment
        # of `_environment`, and no MCP server or connector of the instructor's account (`--strict-mcp-config`: only the servers
        # the repository itself declares in `.mcp.json`, never mail, calendar or hosting). Sessions are still not kept (the home
        # disk is small). Codex is unchanged: its sandbox and disabled features stay.
        declared = repo / ".mcp.json"
        command = [agent_sdk.cli(), "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
                   "--strict-mcp-config", *(["--mcp-config", str(declared)] if declared.is_file() else []),
                   "--allowedTools", *ALLOWED, "--disallowedTools", *REFUSED, "--add-dir", str(folder / "tmp")]
        if task["model"]:
            command += ["--model", task["model"]]
        if task["reasoning"] in config.EFFORTS:
            command += ["--effort", task["reasoning"]]
        return command

    def _launch(self, task_id, number, prompt):
        task, _ = self._task(task_id)
        folder, repo = Path(task["directory"]), Path(task["directory"]) / "repo"
        round_dir = folder / "rounds" / str(number)
        round_dir.mkdir(parents=True, mode=0o700)
        (round_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        (round_dir / "run.sh").write_text('#!/bin/sh\necho $$ > "$ROUND/pid"\ncd "$REPO" || exit 90\n'
                                          '"$@" < "$ROUND/prompt.md" > "$ROUND/out.jsonl" 2> "$ROUND/err.txt"\necho $? > "$ROUND/exit.txt"\n')
        env = {**self._environment(folder, repo), "ROUND": str(round_dir), "REPO": str(repo)}
        head = _git(repo, "rev-parse", "HEAD")
        command = ["/bin/sh", str(round_dir / "run.sh"), *self._command(task, folder, repo)]
        unit = f"faculty-code-{task_id[:8]}-{number}"
        if self.systemd:
            # A transient unit of its own: restarting Faculty (an Apply) never ends a coding agent that is still working.
            subprocess.run(["systemd-run", "--user", "--collect", f"--unit={unit}", f"--working-directory={repo}",
                            *[f"--setenv={k}={v}" for k, v in env.items()], *command], check=True, capture_output=True, timeout=30)
        else:
            subprocess.Popen(command, cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        self._event(task_id, "round_started", "instructor", {"round": number, "unit": unit if self.systemd else "", "head": head})

    # Reading ----------------------------------------------------------------------------------------------------------------
    def _task(self, task_id):
        with connect(self.database) as db:
            task = db.execute("SELECT * FROM assistant_code_tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise NotFound("That coding task was not found.")
            events = db.execute("SELECT * FROM assistant_code_task_events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        return dict(task), [dict(e, detail=json.loads(e["detail_json"])) for e in events]

    def _event(self, task_id, kind, actor, detail):
        assert kind in KINDS, kind
        with connect(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("INSERT INTO assistant_code_task_events(task_id,kind,actor,detail_json,created_at) VALUES(?,?,?,?,?)",
                                (task_id, kind, actor, json.dumps(detail, ensure_ascii=False, default=str), timestamp()))
            return cursor.lastrowid

    @staticmethod
    def _alive(round_dir):
        try:
            pid = int((round_dir / "pid").read_text().strip())
        except (OSError, ValueError):
            # Launched but not yet written its PID: running for its first minute, interrupted after that.
            try:
                return time.time() - (round_dir / "prompt.md").stat().st_mtime < 60
            except OSError:
                return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _rounds(self, task):
        rounds, folder = [], Path(task["directory"]) / "rounds"
        for round_dir in sorted((p for p in folder.glob("*") if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name)):
            parsed = self._parse(round_dir, task["coder"], Path(task["directory"]) / "repo")
            done = (round_dir / "exit.txt").exists()
            exit_code = int((round_dir / "exit.txt").read_text().strip() or 1) if done else None
            state = "running" if not done and self._alive(round_dir) else "interrupted" if not done else \
                "finished" if exit_code == 0 and parsed["finished"] else "failed"
            rounds.append({"number": int(round_dir.name), "state": state, "exit": exit_code, **parsed})
        return rounds

    @staticmethod
    def _parse(round_dir, coder, repo):
        """What a round's event stream says: its steps as short lines (paths relative to the copy), the final report, and cost."""
        steps, summary, finished, cost, error = [], "", False, None, ""
        try:
            lines = (round_dir / "out.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if coder == "codex":
                item = event.get("item") or {}
                if event.get("type") == "item.completed":
                    if item.get("type") == "command_execution":
                        steps.append("Ran " + str(item.get("command") or "")[:160])
                    elif item.get("type") == "file_change":
                        steps.append("Changed " + ", ".join(str(c.get("path", "")).rsplit("/", 1)[-1] for c in item.get("changes") or [])[:160])
                    elif item.get("type") == "agent_message" and item.get("text"):
                        summary = item["text"]
                elif event.get("type") == "turn.completed":
                    finished = True
                elif event.get("type") in ("turn.failed", "error"):
                    error = str((event.get("error") or {}).get("message") or event.get("message") or "")[:500]
                continue
            if event.get("type") == "assistant":
                for part in (event.get("message") or {}).get("content") or []:
                    if part.get("type") == "tool_use":
                        data = part.get("input") or {}
                        what = data.get("file_path") or data.get("command") or data.get("pattern") or data.get("path") or ""
                        steps.append(f"{part.get('name')} {str(what)[:160]}".strip())
            elif event.get("type") == "result":
                finished = event.get("subtype") == "success" and not event.get("is_error")
                summary = str(event.get("result") or "")
                cost = event.get("total_cost_usd")
                if not finished:
                    error = summary[:500]
        if not error:
            try:
                error = (round_dir / "err.txt").read_text(encoding="utf-8", errors="replace").strip()[-500:]
            except OSError:
                error = ""
        prefix = str(repo).rstrip("/") + "/"
        steps = [step.replace(prefix, "") for step in steps]
        return {"steps": steps[-400:], "summary": summary, "finished": finished, "cost": cost, "error": error if not finished else ""}

    @staticmethod
    def _state(events, rounds):
        kinds = [e["kind"] for e in events]
        if "discarded" in kinds:
            return "discarded"
        if "applied" in kinds:
            return "applied"
        if rounds and rounds[-1]["state"] == "running":
            return "running"
        if rounds and rounds[-1]["state"] in ("failed", "interrupted"):
            return "failed"
        return "ready" if rounds else "failed"

    def _settle(self, task, events, rounds):
        """Record a round that ended since it was last read, committing edits the agent left uncommitted."""
        recorded = {e["detail"].get("round") for e in events if e["kind"] in ("round_finished", "round_failed", "stopped")}
        for found in rounds:
            if found["state"] == "running" or found["number"] in recorded:
                continue
            repo = Path(task["directory"]) / "repo"
            committed = ""
            if repo.exists():
                _exclude_shared(repo)  # also a clone made before the links were excluded
            if found["state"] == "finished" and _git(repo, "status", "--porcelain", "--untracked-files=all", check=False):
                _git(repo, "add", "-A")
                _git(repo, "commit", "--quiet", "-m", f"{task['title']}: the edits round {found['number']} left uncommitted\n\n"
                     "Co-Authored-By: Faculty coding agent <noreply@anthropic.com>")
                committed = _git(repo, "rev-parse", "HEAD")
            kind = "round_finished" if found["state"] == "finished" else "round_failed"
            self._event(task["id"], kind, "faculty", {"round": found["number"], "exit": found["exit"], "committed_leftovers": committed,
                                                      "summary": found["summary"][:20000], "error": found["error"]})

    @staticmethod
    def _base(task, events):
        """What the branch is measured from: where it began, or the live main it was last brought up to date with."""
        for event in reversed(events):
            if event["detail"].get("rebased_onto"):
                return event["detail"]["rebased_onto"]
        return task["base_commit"]

    def _changes(self, task, events):
        repo = Path(task["directory"]) / "repo"
        if not repo.exists():
            return {"commits": [], "files": [], "diff": "", "migrations": [], "head": ""}
        task = {**task, "base_commit": self._base(task, events)}
        head = _git(repo, "rev-parse", "HEAD")
        commits = [line.split("\t", 1) for line in _git(repo, "log", "--format=%h\t%s", f"{task['base_commit']}..HEAD", check=False).splitlines() if "\t" in line]
        files = [line.split("\t") for line in _git(repo, "diff", "--numstat", f"{task['base_commit']}..HEAD", check=False).splitlines()]
        added = _git(repo, "diff", "--name-only", "--diff-filter=A", f"{task['base_commit']}..HEAD", check=False).splitlines()
        diff = _git(repo, "diff", f"{task['base_commit']}..HEAD", check=False)
        return {"head": head, "commits": [{"id": c[0], "message": c[1]} for c in commits],
                "files": [{"path": f[2], "added": f[0], "removed": f[1]} for f in files if len(f) == 3],
                "migrations": [path for path in added if re.search(r"(^|/)migrations/[^/]+\.sql$", path)],
                "diff": diff[:DIFF_LIMIT] + ("\n… (the diff is cut here)" if len(diff) > DIFF_LIMIT else "")}

    def detail(self, task_id):
        task, events = self._task(task_id)
        rounds = self._rounds(task) if Path(task["directory"]).exists() else []
        self._settle(task, events, rounds)
        task, events = self._task(task_id)
        state = self._state(events, rounds)
        changes = self._changes(task, events) if state not in ("discarded",) else {"commits": [], "files": [], "diff": "", "migrations": [], "head": ""}
        return {"id": task["id"], "title": task["title"], "request": task["request"], "brief": task["brief"], "page": task["page"],
                "coder": task["coder"], "model": task["model"], "branch": task["branch"], "base": task["base_commit"][:10],
                "created_at": task["created_at"], "state": state, "seen": events[-1]["id"] if events else 0, "rounds": rounds, **changes,
                **self._progress(task_id),
                "events": [{"id": e["id"], "kind": e["kind"], "actor": e["actor"], "at": e["created_at"],
                            "detail": {k: v for k, v in e["detail"].items() if k != "summary"}} for e in events]}

    def listing(self):
        with connect(self.database) as db:
            rows = db.execute("SELECT id FROM assistant_code_tasks ORDER BY created_at DESC LIMIT 30").fetchall()
        tasks = []
        for row in rows:
            task, events = self._task(row["id"])
            rounds = self._rounds(task) if Path(task["directory"]).exists() else []
            self._settle(task, events, rounds)
            task, events = self._task(row["id"])
            tasks.append({"id": task["id"], "title": task["title"], "state": self._state(events, rounds), "coder": task["coder"],
                          "created_at": task["created_at"], "rounds": len(rounds), **self._progress(row["id"])})
        return {"tasks": tasks, "coder": self.status()}

    def _progress(self, task_id):
        """`applying` while Apply runs (absent otherwise, so an idle task's answer is unchanged)."""
        found = self._applying.get(task_id)
        return {"applying": dict(found)} if found else {}

    def _stage(self, task_id, stage, **extra):
        self._applying[task_id] = {**self._applying.get(task_id, {}), "stage": stage, **extra}

    @staticmethod
    def _links(repo, base):
        """Paths the branch adds or changes as symbolic links (git mode 120000): a link merged into the live checkout could point the
        source tools at a credentials file or the live data, so Apply refuses every one."""
        found = []
        for line in _git(repo, "diff", "--raw", "--no-renames", f"{base}..HEAD", check=False).splitlines():
            meta, _, path = line.partition("\t")
            fields = meta.split()
            if len(fields) >= 5 and fields[1] == "120000" and not fields[4].startswith("D"):
                found.append(path)
        return found

    # The instructor's actions -------------------------------------------------------------------------------------------------
    def stop(self, task_id):
        task, events = self._task(task_id)
        rounds = self._rounds(task)
        if not rounds or rounds[-1]["state"] != "running":
            raise Conflict("Nothing is running in this task.")
        number = rounds[-1]["number"]
        unit = next((e["detail"].get("unit") for e in reversed(events) if e["kind"] == "round_started" and e["detail"].get("round") == number), "")
        if unit:
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=30)
        else:
            try:
                os.killpg(int((Path(task["directory"]) / "rounds" / str(number) / "pid").read_text().strip()), signal.SIGTERM)
            except (OSError, ValueError):
                pass
        self._event(task_id, "stopped", "instructor", {"round": number})
        return self.detail(task_id)

    def discard(self, task_id):
        task, events = self._task(task_id)
        if self._state(events, self._rounds(task)) == "running":
            raise Conflict("Stop the task before discarding it.")
        if "applied" in [e["kind"] for e in events]:
            raise Conflict("This task was applied.")
        shutil.rmtree(Path(task["directory"]) / "repo", ignore_errors=True)
        self._event(task_id, "discarded", "instructor", {})
        return self.detail(task_id)

    def _busy(self):
        with connect(self.database) as db:
            def count(sql):
                try:
                    return db.execute(sql).fetchone()[0]
                except Exception as error:
                    raise Conflict('ASSISTANT: workspace activity could not be checked. Apply later.') from error
            conversations = self.service.conversations
            canvas = self.service.app.extensions.get('canvas')
            aim = self.service.app.extensions.get('aim_sessions')
            return {"assistant answers": conversations.busy() if conversations is not None else 0,
                    "Canvas writes": len(canvas.writes.busy()) if canvas else 0,
                    "AIM sends": aim.sending() if aim is not None and hasattr(aim, "sending") else 0,
                    "approvals": self.service.approving() if hasattr(self.service, "approving") else 0,
                    "inbox jobs": count("SELECT COUNT(*) FROM inbox_jobs WHERE status IN ('queued','running')"),
                    "grading tasks": count("SELECT COUNT(*) FROM frq_tasks WHERE status IN ('pending','running')"),
                    # A flag review between two requests, or waiting to send one again, has no task and no open request (2026-10-11:
                    # it now starts by itself after a batch, so a restart must wait for it as for the batch).
                    "flag reviews": count("SELECT COUNT(*) FROM frq_flag_resolutions WHERE status IN ('pending','running')"),
                    "model requests": count("SELECT COUNT(*) FROM frq_cloud_calls WHERE state='submitted'")}

    def restart_check(self):
        """May Faculty be restarted now? (AGENTS.md CHECKS; requirement, 2026-10-17: no fixed morning window, a restart waits only
        while work is actually running.) The same counts Apply refuses on; "Canvas writes" include the morning sync with Canvas
        while a term's morning runs (gradebook/canvas_nightly.py). `GET /api/assistant/restart-check`, read-only."""
        busy = {name: count for name, count in self._busy().items() if count}
        canvas = self.service.app.extensions.get('canvas')
        return {"idle": not busy, "busy": busy, "canvas_writes": canvas.writes.labels() if canvas else []}

    def apply(self, task_id, data):
        """Merge, restart and push (the instructor's one click). Refused, with the reason, whenever any step cannot be done safely."""
        data = data if isinstance(data, dict) else {}
        if data.get("confirmation") != "apply":
            raise ValueError("Confirm the apply: Faculty's code changes only when you choose Apply.")
        with self._lock:
            try:
                self._apply(task_id, data)
            finally:
                self._applying.pop(task_id, None)
        return self.detail(task_id)

    def _apply(self, task_id, data):
        task, events = self._task(task_id)
        rounds = self._rounds(task)
        if self._state(events, rounds) != "ready":
            raise Conflict("Only a finished task that was not applied or discarded can be applied.")
        if data.get("seen") != (events[-1]["id"] if events else 0):
            raise Conflict("This task changed since you looked at it. Look again, then apply.")
        repo = Path(task["directory"]) / "repo"
        if data.get("head") != _git(repo, "rev-parse", "HEAD"):
            raise Conflict("The task's code changed since you looked at it. Look again, then apply.")
        if _git(repo, "rev-list", "--count", f"{self._base(task, events)}..HEAD") == "0":
            raise Conflict("This task changed nothing.")
        linked = sorted({path.split("/", 1)[0] for path in _git(repo, "diff", "--name-only", f"{self._base(task, events)}..HEAD").splitlines()
                         if path.split("/", 1)[0] in SHARED})
        if linked:
            raise Conflict(f"The task's branch changes {', '.join(linked)}: Faculty's own runtimes, linked into the task's copy. Applying "
                           "it would replace the live folders, so it is refused. Ask for changes: \"remove "
                           f"{', '.join(linked)} from the branch\".")
        links = self._links(repo, self._base(task, events))
        if links:
            raise Conflict(f"The task's branch adds symbolic links ({', '.join(links[:5])}). A link merged into Faculty could expose "
                           "files outside the repository, so it is refused. Ask for changes: \"replace the links with ordinary files\".")
        busy ={name: count for name, count in self._busy().items() if count}
        if busy:
            raise Conflict("Faculty is busy (" + ", ".join(f"{count} {name}" for name, count in busy.items()) + "). Apply when it is idle.")
        if _git(self.root, "symbolic-ref", "--short", "HEAD") != "main":
            raise Conflict("The live checkout is not on main.")
        rebased = None
        self._applying[task_id] = {"stage": "starting", "started_at": timestamp()}
        try:
            live = _git(self.root, "rev-parse", "main")
            if _git(repo, "merge-base", "HEAD", live, check=False) != live:
                self._stage(task_id, "rebasing")
                _git(repo, "fetch", "--quiet", str(self.root), "main")
                rebased = subprocess.run(["git", "-C", str(repo), "rebase", "--quiet", "FETCH_HEAD"], capture_output=True, text=True,
                                         env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}, timeout=300)
                if rebased.returncode != 0:
                    _git(repo, "rebase", "--abort", check=False)
                    raise GitError("It conflicts with changes made to Faculty since the task began. Ask for changes: "
                                   "\"bring this up to date with main and resolve the conflicts\".")
                rebased = live
            self._stage(task_id, "checking")
            self._check(repo, live)
            head = _git(repo, "rev-parse", "HEAD")
            from .code_checks import plan, run
            changed = _git(repo, 'diff', '--name-only', f'{live}..{head}').splitlines()
            chosen = plan(repo, changed)
            self._stage(task_id, "checking", modules=len(chosen["python"]), scripts=len(chosen["javascript"]))
            try:
                receipt = run(repo, self.root, changed, head, live,
                              progress=lambda done, total: self._stage(task_id, "checking", done=done, commands=total))
            except (ValueError, subprocess.TimeoutExpired) as error:
                raise GitError(str(error)) from error
            if head != _git(repo, 'rev-parse', 'HEAD') or live != _git(self.root, 'rev-parse', 'main') or _git(repo, 'status', '--porcelain'):
                raise GitError('ASSISTANT: code changed during verification. Review and apply the new commit.')
            self._event(task_id, 'checks_passed', 'faculty', receipt)
            if any(self._busy().values()):
                raise GitError('ASSISTANT: Faculty became busy during checks. Apply when it is idle.')
            self._stage(task_id, "merging")
            _git(self.root, "fetch", "--quiet", str(repo), f"{task['branch']}")
            merged = subprocess.run(["git", "-C", str(self.root), "merge", "--ff-only", "--quiet", "FETCH_HEAD"], capture_output=True,
                                    text=True, timeout=120, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if merged.returncode != 0:
                raise GitError("The live checkout has uncommitted changes to the same files (another session's work): "
                               + (merged.stderr.strip()[-800:] or "the merge was refused"))
        except GitError as error:
            self._event(task_id, "apply_failed", "faculty", {"reason": str(error), **({"rebased_onto": rebased} if rebased else {})})
            raise Conflict(str(error)) from None
        migrations = [path for path in _git(repo, "diff", "--name-only", "--diff-filter=A", f"{live}..{head}").splitlines()
                      if re.search(r"(^|/)migrations/[^/]+\.sql$", path)]
        self._event(task_id, "applied", "instructor", {"head": head, "previous_main": live, "migrations": migrations})
        self._stage(task_id, "pushing")
        try:
            upstream = _git(self.root, "rev-parse", "--abbrev-ref", "main@{upstream}")
            remote = upstream.split("/", 1)[0]
            _git(self.root, "push", "--quiet", remote, "main", timeout=180)
            self._event(task_id, "pushed", "faculty", {"remote": remote, "head": head})
        except (GitError, subprocess.SubprocessError) as error:
            self._event(task_id, "push_failed", "faculty", {"reason": str(error)[:1000]})
        self._stage(task_id, "restarting")
        try:
            problem = 'ASSISTANT: new work started; restart Faculty when it is idle.' if any(self._busy().values()) else self.restart()
        except (OSError, subprocess.SubprocessError) as error:
            problem = f"The restart could not be scheduled: {error}"
        self._event(task_id, "restart_skipped" if problem else "restart_scheduled", "faculty", {"reason": problem} if problem else {})
        shutil.rmtree(repo, ignore_errors=True)

    def _check(self, repo, base):
        """The changed Python compiles and the changed JavaScript parses, so a merged change cannot stop Faculty from starting.
        Neither writes anything into the clone (`COMPILES`; `node --check` only parses)."""
        links = self._links(repo, base)
        if links:
            raise GitError(f"The branch adds symbolic links ({', '.join(links[:5])}); Apply refuses them. Ask for changes: "
                           "\"replace the links with ordinary files\".")
        changed = [path for path in _git(repo, "diff", "--name-only", "--diff-filter=AM", f"{base}..HEAD").splitlines()]
        python = [path for path in changed if path.endswith(".py")]
        if python:
            interpreter = self.root / ".venv" / "bin" / "python"
            result = subprocess.run([str(interpreter) if interpreter.exists() else sys.executable, "-B", "-c", COMPILES, *python], cwd=repo,
                                    capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise GitError("Changed Python does not compile: " + result.stderr.strip()[-800:])
        node = self.root / ".tools" / "node22" / "bin" / "node"
        for path in (p for p in changed if p.endswith((".js", ".mjs"))):
            if node.exists():
                result = subprocess.run([str(node), "--check", path], cwd=repo, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    raise GitError(f"{path} does not parse: " + result.stderr.strip()[-600:])
