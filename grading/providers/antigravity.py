"""Grading on the instructor's Google AI (Gemini) subscription through Google's Antigravity CLI (requirement, 2026-10-14).

The same connection as the Claude subscription (`agent_sdk`: bundles, whose-answer check, concurrency, usage-limit pause, zero cost),
with the `agy` binary run headless (`agy -p`, stream-json in and out, `--json-schema`). It is signed in with Google through Google's
own flow: Faculty looks only at whether the sign-in file exists, never at its contents, and the run gets a plain environment (never
`GEMINI_API_KEY`, which would bill the API). `agy` differs from Claude Code and Codex in three ways, each measured on 2026-10-14 with
invented prompts (agy 1.3.4):

ITS OWN HOME. `agy` has no flag for an unsaved session: every run keeps its conversation under `$HOME/.gemini` (the transcript and a
copy of each picture viewed, about half a megabyte a run). So it never runs in the instructor's own home. It runs with HOME set to a
folder of Faculty's own beside the other runtime work on NVMe (`home`, made by `prepare`), where the instructor signs in once
(`sign_in_hint`); what a run kept there is deleted when it ends (`forget`), and whatever any run left behind is swept once no run is
in progress (`running`, `_sweep`). The program keeps its own settings there; Faculty adds one line to them, the permission for
its own tool server (`ALLOW`, below). (The program removes a telemetry key written into its settings file, so that choice is the
instructor's to make in the program itself.)

ITS OWN AGENT. `agy` has no flag for a system prompt or for switching its tools off (it offers sixty: a shell, a browser, web
search, file writes, sub-agents). Each call writes an agent definition into its scratch folder (`.agents/agents/faculty.md`) and runs
as that agent: the definition's body is the instruction and replaces the program's own, and its tools are `finish` (how `agy` hands
back the answer a schema asks for) and, when pictures are attached, `view_file`; no other tool is offered to the model. Rule files
(GEMINI.md, AGENTS.md) above the scratch folder do not reach such an agent, and one run sees nothing of another.

PICTURES AS FILES. `agy` takes text only. Pictures are written into the scratch folder, the prompt marks where each belongs
("[image 3: image-3.png]"), and the model looks at each with `view_file`. The run's own event stream names every tool step, so an
answer is accepted only when every attached picture was viewed and nothing but viewing those files and finishing was done; anything
`agy` refused to do for want of permission (`denied_actions`) refuses the answer too. A refused answer is asked again like any other.

Models are named without their reasoning level (`gemini-4-argon` with `--effort high`; `agy models` lists one name per level). The
Google plan also serves Claude and GPT-OSS models, which this connection may name. Drafting tools (`tools.py`) and the coding
agent do not run on this connection (`config.TOOL_KINDS`). The Assistant does (`assistant/engines.AntigravityEngine`): the same
program in the same folder, as an agent whose only tools are Faculty's own tool server.
"""
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import agent_sdk
from .agent_sdk import AgentSdkEndpoint
from .base import Cancelled, ProviderError, current_abort

KIND = "antigravity"
# The plan's models with the reasoning levels each offers, from `agy models` on 2026-10-14. Browse lists what the installed program
# offers today (`listed_models`); this is what it falls back on and what a connection saved without its levels takes them from.
# The first is a new connection's model.
MODELS = (("gemini-4-argon", "Gemini 4 Argon", ("low", "medium", "high"), "medium"),
          ("gemini-3.8-flash", "Gemini 3.8 Flash", ("low", "medium", "high"), "medium"),
          ("claude-opus-5-5", "Claude Opus 5.5", ("low", "medium", "high"), "medium"),
          ("claude-sonnet-5-5", "Claude Sonnet 5.5", ("low", "medium", "high"), "medium"),
          ("gpt-oss-120b", "GPT-OSS 120B", ("medium",), "medium"))
FALLBACK_FACTS = {"supported": True, "mandatory": True, "efforts": ["low", "medium", "high"], "default_effort": "medium"}
TEXT_ONLY = ("gpt-oss",)  # `agy models` does not say what a model reads; these families read no pictures.
ENV = ("PATH", "LANG", "LC_ALL")  # HOME is Faculty's folder (`home`), TMPDIR the call's scratch; nothing else goes along.
AGENT = "faculty"
SLUG = re.compile(r"[a-z][a-z0-9.-]{1,60}")
LEVEL = re.compile(r"-(minimal|low|medium|high|xhigh|max)$")
NAMED_LEVEL = re.compile(r"\s*\((?:minimal|low|medium|high|xhigh|extra high|max)\)\s*$", re.IGNORECASE)
CONVERSATION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MARKER = ".faculty-antigravity-home"   # Faculty made this folder: the only kind of folder it will run `agy` in, and sweep.
TOKEN = "antigravity-oauth-token"
# The one permission Faculty writes into the program's settings in its own folder: the tools of an MCP server named "faculty".
# Only the Assistant's engine defines such a server (the proxy to Faculty's own tools, in its conversation's agent definition);
# without the rule a headless run cannot ask, refuses the call and ends the turn (measured). Nothing else is allowed by it.
ALLOW = "mcp(faculty/*)"
# What a run leaves under `$HOME/.gemini/antigravity-cli` besides its own conversation: folders emptied and files removed by `_sweep`.
KEPT_FOLDERS = ("conversations", "brain", "annotations", "presence", "implicit", "log", "crashes")
KEPT_FILES = ("conversation_summaries.db", "conversation_summaries.db-shm", "conversation_summaries.db-wal", "jetbox_summaries_proto.pb",
              "history.jsonl", "cli.log", "cache/last_conversations.json")
IMAGE_NOTE = ("{count} picture{s} belong{verb} to this request, as the file{s} {names} in the current folder; each [image N: file] marker "
              "below shows where that picture belongs. Look at EVERY one with view_file before you answer: an answer given without viewing "
              "them all is discarded.")
HOW = ("\n\nHOW YOU WORK HERE. You answer this one request and nothing else. Never ask a question, wait for a reply or start a task: "
       "everything you need is in the request.")
LIMIT_WORDS = ("resource_exhausted", "quota", "usage limit", "rate limit", "hit your limit", "too many requests", "(code 429)")
CAPACITY_WORDS = ("unavailable", "overloaded", "at capacity", "high demand", "try again later", "(code 503)", "(code 529)")
SIGN_IN_WORDS = ("authentication required", "authentication failed", "unauthenticated", "not logged in", "(code 401)")
LENGTH_WORDS = ("max_tokens", "output token", "token limit", "too long")

_configured = ""


def configure(folder):
    """Where `agy` keeps its sign-in and whatever it writes: set once by `create_app` (NVMe runtime work), else FACULTY_ANTIGRAVITY_HOME."""
    global _configured
    _configured = str(folder or "")


def cli():
    found = os.environ.get("FACULTY_ANTIGRAVITY_CLI") or shutil.which("agy") or str(Path.home() / ".local/bin/agy")
    return found if Path(found).is_file() and os.access(found, os.X_OK) else ""


def home():
    """Faculty's own HOME for `agy`, or None when none is configured. Never the home of the account Faculty runs as: the program
    would keep every conversation in the instructor's own history, and the sweep must never run there."""
    value = _configured or os.environ.get("FACULTY_ANTIGRAVITY_HOME") or ""
    if not value or not os.path.isabs(value):
        return None
    folder = Path(os.path.abspath(value))
    return None if folder == Path(os.path.abspath(Path.home())) else folder


def data(folder):
    return folder / ".gemini" / "antigravity-cli"


def prepare():
    """Make Faculty's folder for `agy` (0700) with its marker and the one permission Faculty adds to the program's settings there
    (`ALLOW`), and return it; None when no folder is configured or the configured one holds something Faculty did not make."""
    folder = home()
    if folder is None:
        return None
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not (folder / MARKER).is_file():
            if any(folder.iterdir()):
                return None
            (folder / MARKER).write_text("Faculty runs Google's Antigravity CLI (agy) with this folder as its HOME. See grading/providers/antigravity.py.\n")
        data(folder).mkdir(mode=0o700, parents=True, exist_ok=True)
        path = data(folder) / "settings.json"
        try:
            settings = json.loads(path.read_text())
        except (OSError, ValueError):
            settings = {}
        settings = settings if isinstance(settings, dict) else {}
        permissions = settings.get("permissions") if isinstance(settings.get("permissions"), dict) else {}
        allowed = permissions.get("allow") if isinstance(permissions.get("allow"), list) else []
        if ALLOW not in allowed:
            # The program's own keys stay as they are; the file is replaced whole, never left half written under a running program.
            settings["permissions"] = {**permissions, "allow": [*allowed, ALLOW]}
            fresh = path.with_name(f".settings-{os.getpid()}.json")
            fresh.write_text(json.dumps(settings, indent=2) + "\n")
            fresh.chmod(0o600)
            fresh.replace(path)
    except OSError:
        return None
    return folder


def signed_in():
    """Whether `agy` holds a sign-in in Faculty's folder. Only the file's presence is looked at, never its contents."""
    folder = home()
    return folder is not None and (data(folder) / TOKEN).is_file()


def sign_in_hint():
    folder = home()
    return f"run `HOME={folder} agy` once in a terminal, sign in with Google, then leave with Ctrl+D twice" if folder else \
        "set FACULTY_ANTIGRAVITY_HOME to a folder of Faculty's own on NVMe"


def ready():
    """The binary and Faculty's folder for a run (a grading call, or the Assistant's process), or the reason there is none."""
    binary = cli()
    if not binary:
        raise ProviderError("The agy command (Google Antigravity) is not installed for the account Faculty runs as.", confirmed=True, fixable=False)
    folder = prepare()
    if folder is None:
        raise ProviderError("Faculty has no folder of its own to run Antigravity in: " + (
            "the configured one holds files Faculty did not put there." if home() else "set FACULTY_ANTIGRAVITY_HOME to one on NVMe."), confirmed=True, fixable=False)
    if not signed_in():
        raise ProviderError(f"Antigravity is not signed in for Faculty: {sign_in_hint()}.", confirmed=True, fixable=False)
    return binary, folder


def environment(folder, scratch):
    # A plain environment: the sign-in is the program's own, and no service credential (a Gemini API key would bill the API) goes along.
    # The program does not look for a newer version of itself during a call (measured: only the value "true" stops its updater).
    return {**{k: os.environ[k] for k in ENV if k in os.environ}, "HOME": str(folder), "TMPDIR": str(scratch), "AGY_CLI_DISABLE_AUTO_UPDATE": "true"}


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)  # The program and anything it started (`start_new_session`).
    except (ProcessLookupError, PermissionError):
        pass


def forget(folder, conversation):
    """Delete what one run kept in Faculty's folder: its conversation, transcript and the copies of the pictures it viewed."""
    if not isinstance(conversation, str) or not CONVERSATION.fullmatch(conversation):
        return
    root = data(folder)
    shutil.rmtree(root / "brain" / conversation, ignore_errors=True)
    for name in (f"conversations/{conversation}.db", f"conversations/{conversation}.db-shm", f"conversations/{conversation}.db-wal",
                 f"annotations/{conversation}.pbtxt", f"presence/{conversation}.lock"):
        (root / name).unlink(missing_ok=True)


def _sweep(folder):
    """Empty everything runs keep in Faculty's folder (a run that was killed could not delete its own). The sign-in and the
    program's settings stay."""
    if not (folder / MARKER).is_file():
        return
    root = data(folder)
    for name in KEPT_FOLDERS:
        for entry in (root / name).iterdir() if (root / name).is_dir() else ():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
    for name in KEPT_FILES:
        (root / name).unlink(missing_ok=True)


@contextmanager
def running(folder):
    """One run in Faculty's folder. Runs of every Faculty process share a lock on it; the run that finds itself the last one sweeps."""
    lock = os.open(folder / ".faculty-runs.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pass  # Another run is in progress: it, or a later one, sweeps.
        else:
            _sweep(folder)
        os.close(lock)


def _asked(command, folder, program_input=None, seconds=None):
    """`agy` run for a question that is not a model call (its models, its usage): stdout, stderr and exit code."""
    scratch = tempfile.mkdtemp(prefix="faculty-gemini-")
    try:
        with running(folder):
            try:
                process = subprocess.Popen(command, cwd=scratch, env=environment(folder, scratch), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, start_new_session=True)
            except OSError as error:
                raise ProviderError(f"Antigravity could not be started ({type(error).__name__}).", confirmed=True) from None
            try:
                out, err = process.communicate(program_input or "", timeout=seconds or agent_sdk.LIST_SECONDS)
            except subprocess.TimeoutExpired:
                stop(process)
                process.communicate()
                raise ProviderError(f"Antigravity did not answer within {seconds or agent_sdk.LIST_SECONDS} s.", confirmed=True) from None
            finally:
                stop(process)
        return out, err, process.returncode
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def facts(model):
    found = next((m for m in MODELS if m[0] == model), None)
    return {"supported": True, "mandatory": True, "efforts": list(found[2]), "default_effort": found[3]} if found else dict(FALLBACK_FACTS, efforts=list(FALLBACK_FACTS["efforts"]))


def lineage(model):
    return next((family for family in ("gemini", "claude", "gpt") if str(model).startswith(family)), "")


def listed_models():
    """The models the Google plan offers now, as the installed Antigravity lists them (`agy models`: one name per reasoning level,
    folded here into the model with its levels). Asked only when the instructor browses the plan's models (grading.md "GEMINI
    SUBSCRIPTION"): no model runs, nothing of Faculty's is sent and nothing is kept."""
    binary, folder = ready()
    out, err, code = _asked([binary, "models"], folder)
    rows = {}
    for line in out.splitlines():
        slug, tab, name = line.partition("\t")
        slug = slug.strip()
        if not tab or not SLUG.fullmatch(slug):
            continue
        level = LEVEL.search(slug)
        model = slug[:level.start()] if level else slug
        row = rows.setdefault(model, {"id": model, "name": NAMED_LEVEL.sub("", name).strip() or model, "description": "", "efforts": [],
                                      "default_effort": "medium", "vision": not model.startswith(TEXT_ONLY)})
        if level and level.group(1) not in row["efforts"]:
            row["efforts"].append(level.group(1))
    if not rows:
        said = (err.strip().splitlines() or out.strip().splitlines() or [""])[-1][:200].rstrip(".")
        raise ProviderError(f"Antigravity did not list its models (exit {code}){': ' + said if said else ''}.", confirmed=True)
    return list(rows.values())


def accepted(schema):
    """The schema as Gemini's function declarations take it: an enum may not hold an empty string (measured: 400 INVALID_ARGUMENT
    "enum[2]: cannot be empty" on the quiz writer's `figure_side`), so such a field is asked for as a string that names its
    choices. Faculty's own validation of the answer is unchanged."""
    if isinstance(schema, list):
        return [accepted(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    value = {key: accepted(item) for key, item in schema.items()}
    choices = value.get("enum")
    if isinstance(choices, list) and "" in choices:
        del value["enum"]
        value.setdefault("type", "string")
        named = ", ".join(json.dumps(choice) for choice in choices if choice != "")
        value["description"] = (f"{value['description']} " if value.get("description") else "") + f"Exactly one of {named}, or an empty string."
    return value


def _reset(binary, folder, model):
    """When the plan's limit for this model's group resets, as `agy` reports its usage (`/usage`: a weekly and a five-hour limit
    for Gemini models, and for Claude and GPT models), as a Unix time; None when it cannot say."""
    try:
        out, _, _ = _asked([binary, "--output-format", "json", "-p", "/usage"], folder)
        groups = (json.loads(out).get("command") or {}).get("data", {}).get("groups") or []
    except (ProviderError, ValueError, AttributeError):
        return None
    gemini, resets = lineage(model) == "gemini", []
    for own in (True, False):  # the model's own group first; any exhausted limit otherwise
        for group in groups:
            if not isinstance(group, dict) or (own and ("gemini" in str(group.get("name", "")).casefold()) != gemini):
                continue
            for bucket in group.get("buckets") or []:
                left, when = bucket.get("remaining_fraction"), bucket.get("reset_time")
                if isinstance(left, (int, float)) and left < .01 and isinstance(when, str):
                    try:
                        resets.append(datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())
                    except ValueError:
                        pass
        if resets:
            return max(resets)
    return None


class AntigravityEndpoint(AgentSdkEndpoint):
    """One model on the instructor's Google AI subscription. `endpoint`: a connection of kind `antigravity`."""
    kind = KIND
    PLAN, BILLING, UPSTREAM, LINEAGE = "Gemini subscription", "gemini_subscription", "gemini-subscription", "gemini"
    BINARY = "the agy command (Google Antigravity)"

    def __init__(self, endpoint, timeout=None):
        super().__init__(endpoint, timeout)
        self.LINEAGE = lineage(self.model) or "gemini"

    @property
    def SIGN_IN(self):
        return sign_in_hint()

    @staticmethod
    def binary():
        return cli()

    @staticmethod
    def signed_in():
        return signed_in()

    @staticmethod
    def models():
        return [m[0] for m in MODELS]

    def _facts(self):
        return facts(self.model)

    @staticmethod
    def _image(data):
        return {"type": "image", "bytes": data}  # Written to a file the model views; the prompt marks its place.

    def _run(self, instruction, content, schema, bundled=0, tools=None):
        if tools is not None:  # `tools.offered` never gives this connection a ToolSet; one that arrives is not run without them.
            raise ProviderError("The Gemini subscription connection does not run Faculty's drafting tools.", confirmed=True, fixable=False,
                                confirmation={"not_sent": True})
        binary, folder = ready()
        abort = current_abort()
        if abort is not None:
            abort.check()
        scratch = Path(tempfile.mkdtemp(prefix="faculty-gemini-"))
        started, conversation = time.monotonic(), None
        try:
            work = scratch / "work"   # The run's workspace: its agent definition and the pictures, nothing else.
            (work / ".agents" / "agents").mkdir(parents=True)
            (scratch / "tmp").mkdir()
            text, images = [], []
            for part in content:
                if part["type"] == "image":
                    images.append(f"image-{len(images) + 1}.png")
                    (work / images[-1]).write_bytes(part["bytes"])
                    text.append(f"[image {len(images)}: {images[-1]}]")
                else:
                    text.append(part["text"])
            if images:
                many = len(images) > 1
                text.insert(0, IMAGE_NOTE.format(count=len(images), s="s" if many else "", verb="" if many else "s",
                                                 names=f"image-1.png to {images[-1]}" if many else images[0]))
            offered = [*(["view_file"] if images else []), *(["finish"] if schema is not None else [])]
            (work / ".agents" / "agents" / f"{AGENT}.md").write_text(
                f"---\nname: {AGENT}\ndescription: Answers one request of the Faculty teaching app.\ntools: [{', '.join(offered)}]\n"
                "mainAgent: true\nsubagent: false\ncommandExecutionPolicy: off\n---\n" + instruction.strip() + HOW + "\n")
            command = [binary, "--input-format", "stream-json", "--output-format", "stream-json", "--disable-slash-commands", "--agent", AGENT,
                       "--model", self.model, "--log-file", str(scratch / "agy.log")]
            if (effort := self._effort()) and not LEVEL.search(self.model):
                command += ["--effort", effort]
            if schema is not None:
                (scratch / "schema.json").write_text(json.dumps(accepted(schema)))
                command += ["--json-schema", str(scratch / "schema.json")]
            command += ["-p", ""]  # The prompt comes on stdin, one stream-json line.
            message = json.dumps({"event": "user", "message": {"content": [{"type": "text", "text": "\n\n".join(text)}]}}, ensure_ascii=False)
            roots, events = {str(work), os.path.realpath(work)}, []
            with running(folder):
                try:
                    try:
                        process = subprocess.Popen(command, cwd=work, env=environment(folder, scratch / "tmp"), stdin=subprocess.PIPE,
                                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
                    except OSError as error:
                        raise ProviderError(f"Antigravity could not be started ({type(error).__name__}).", confirmed=True, confirmation={"not_sent": True}) from None
                    if abort is not None:
                        abort.attach(process)  # Cancelling the batch ends the process (`base._shut`).
                    try:
                        stdout, stderr = process.communicate(message + "\n")
                    finally:
                        stop(process)
                    for line in stdout.splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(event, dict):
                            events.append(event)
                    conversation = next((e.get("conversation_id") for e in events if e.get("event") == "init"), None) \
                        or next(((e.get(e.get("event")) or {}).get("conversation_id") for e in events if isinstance(e.get(e.get("event")), dict)), None)
                finally:
                    forget(folder, conversation)
            if abort is not None and abort.stopped:
                raise Cancelled(abort.reason, sent=True)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        result = next((e.get("result") for e in reversed(events) if e.get("event") == "result" and isinstance(e.get("result"), dict)), None)
        if result is None or result.get("status") != "SUCCESS":
            said = str((result or {}).get("error") or "").strip() or next(
                (line.strip() for line in reversed(stderr.strip().splitlines()) if line.strip()), "")
            raise self._failure(said, result, process.returncode, bundled, binary, folder)
        # What the run did, from its own events: every picture viewed, and nothing but viewing them and finishing.
        viewed, other = set(), []
        for event in events:
            step = event.get("step_update") if event.get("event") == "step_update" else None
            if not isinstance(step, dict) or step.get("step_type") != "tool" or step.get("state") == "ACTIVE":
                continue
            name, info = step.get("tool_name"), step.get("tool_info")
            if isinstance(info, str):
                try:
                    info = json.loads(info)
                except ValueError:
                    info = {}
            parameters = info.get("parameters") if isinstance(info, dict) and isinstance(info.get("parameters"), dict) else {}
            target = Path(os.path.normpath(str(parameters.get("AbsolutePath") or "")))
            inside = str(target.parent) in roots
            if name == "view_file" and step.get("state") == "DONE" and inside and target.name in images:
                viewed.add(target.name)
            elif name != "finish" and not (name == "view_file" and inside):
                other.append(str(name))
        facts = {"conversation": conversation, "images": len(images), "viewed": len(viewed), "other_tools": sorted(set(other)),
                 "denied": [str(d.get("action")) for d in result.get("denied_actions") or [] if isinstance(d, dict)]}
        if facts["denied"] or other or len(viewed) < len(images):
            why = (f"it tried what a grading call does not permit ({', '.join(facts['denied'])})" if facts["denied"] else
                   f"it used a tool this call does not offer ({', '.join(facts['other_tools'])})" if other else
                   f"it looked at {len(viewed)} of the {len(images)} attached pictures")
            raise ProviderError(f"The model's answer was not accepted: {why}. It is asked again.", raw={"antigravity": facts}, confirmed=True,
                                confirmation={"malformed": True, "antigravity": facts})
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        count = lambda key: int(usage.get(key) or 0)
        structured = result.get("structured_output") if schema is not None else None
        # Shaped like Claude Code's result, so receipts, usage and parsing are one code path (`AgentSdkEndpoint._metadata`, `_raw`).
        return {"subtype": "success", "result": str(result.get("response") or ""), "structured_output": structured if isinstance(structured, dict) else None,
                "session_id": conversation, "num_turns": result.get("num_turns"), "antigravity": facts,
                "duration_ms": int(float(result.get("duration_seconds") or 0) * 1000) or int((time.monotonic() - started) * 1000),
                "modelUsage": {self.model: {}}, "total_cost_usd": None,
                "usage": {"input_tokens": count("input_tokens"), "output_tokens": count("output_tokens") + count("thinking_tokens"),
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "cached_input_tokens": count("cache_read_tokens")}}, []

    def _failure(self, said, result, code, bundled, binary, folder):
        """The error for a run that ended without an accepted answer, by what `agy` said of it."""
        low = said.casefold()
        if any(word in low for word in SIGN_IN_WORDS):
            return ProviderError(f"Antigravity's sign-in was refused. To sign in again, {sign_in_hint()}; then retry.", confirmed=True, fixable=False, status=401)
        if any(word in low for word in LIMIT_WORDS):
            reset = _reset(binary, folder, self.model)
            seconds = self._pause({"resetsAt": reset} if reset else {})
            return ProviderError(f"The Gemini subscription's usage limit was reached; this connection waits until it resets. ({said[:200]})",
                                 confirmed=True, status=429, retry_after=seconds, confirmation={"status": 429, "usage_limit": True})
        if any(word in low for word in CAPACITY_WORDS):
            # Busy, not wrong: waited for and asked again (`cloud._settle_failure`), never a failed grade or a skipped ruling.
            return self._capacity(said)
        if bundled > 1 and any(word in low for word in LENGTH_WORDS):
            return ProviderError(f"The bundled answer outgrew the model's limit; smaller bundles are sent for a while. ({said[:200]})",
                                 confirmed=True, confirmation={"output_limit": True, "bundle": bundled})
        if result is None:
            return ProviderError(f"Antigravity ended without an answer (exit {code}). {said[:300]}", confirmed=False)
        return ProviderError(f"Antigravity reported an error: {said[:300]}", confirmed=True, confirmation={"status": result.get("status")})

    @staticmethod
    def _raw(result, answer, bundle=None):
        return {**AgentSdkEndpoint._raw(result, answer, bundle), "antigravity": result.get("antigravity")}
