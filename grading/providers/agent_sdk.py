"""Grading on the instructor's Claude subscription through the Agent SDK's engine (requirement, 2026-09-27).

The Claude Code binary is run headless (`claude -p`, the documented way to drive the Agent SDK's loop from another language), as
published and signed in through Anthropic's own flow: Faculty never reads, stores or passes the subscription's credentials, and the
subprocess gets a plain environment (no service keys, except an explicitly enabled API fallback after a confirmed usage limit).
An `ANTHROPIC_API_KEY` is passed only to that fallback subprocess. It has no tools, no MCP
servers, no settings, skills or CLAUDE.md, and keeps no session on disk: one question in, one structured answer out. The one exception
is a drafting request that carries Faculty's own tools (`tools.py`, grading.md "DRAFTING TOOLS"): its model may call those, and only
those, before the same structured answer.

BUNDLES. Anthropic's usage limits assume ordinary individual use, so hundreds of parallel requests are the wrong shape. Requests of
one batch, one question (a question belongs to one exam version) and one step that arrive together are sent as ONE call: the
dispatcher's threads each still own their response's `frq_cloud_calls` row, written before anything leaves, and each gets back only
its own answer, which Faculty validates exactly as any grader's. The model is told the items are independent. A bundle fills while
it waits for one of the connection's few sessions, and leaves when full, when no more arrive for `QUIET` seconds, or after `GATHER`.
One failed call fails each of its members under the ordinary retry policy; an answer missing or malformed for one item is that
item's own refused answer, asked again like any other.
"""
import base64
import math
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from ..models import digest
from . import bundles
from . import tools as tool_server
from .base import Cancelled, ProviderError, current_abort
from .bundles import BundleMixin
from .cloud import CloudGradingProvider  # This module is imported by `cloud.provider`, after `cloud` is loaded.
from .local_vlm import prompt

KIND = "agent_sdk"
RULE = "agent-sdk-bundles-v1"  # How this connection runs; the bundling itself is `bundles.RULE`.
# Claude models the subscription serves. Model IDs are the connection's own setting. Browse lists what the installed Claude Code
# offers today (`listed_models`; requirement, 2026-10-01: Sonnet 5.5 was out and missing); this is the list it falls back on.
MODELS = (("claude-opus-5-5", "Claude Opus 5.5"), ("claude-sonnet-5-5", "Claude Sonnet 5.5"), ("claude-sonnet-5", "Claude Sonnet 5"),
          ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"), ("claude-fable-5-1", "Claude Fable 5.1"))
FACTS = {"supported": True, "mandatory": True, "efforts": ["low", "medium", "high", "xhigh", "max"], "default_effort": "medium"}
BUNDLE_SIZE, SESSIONS = 6, 2  # A new connection's defaults: responses per call, and calls at once.
# A service at capacity is waited for with a backoff shared by the whole connection (requirement, 2026-09-28): the first busy answer
# waits CAPACITY_WAIT, each further one while no call has succeeded doubles it, to CAPACITY_MAX; a success starts over. Answers that
# arrive during a wait (calls already in flight) take its remainder without doubling, and nothing is sent until it ends.
CAPACITY_WAIT, CAPACITY_MAX = 30, 900
ENV = ("HOME", "PATH", "LANG", "LC_ALL", "USER", "LOGNAME", "TMPDIR")
# A call with drafting tools runs without safe mode (which drops MCP servers): no CLAUDE.md or auto-memory is read instead, and a tool
# may take minutes (a quiz compiles every version in the document sandbox) and answer with pictures.
TOOL_ENV = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "MCP_TOOL_TIMEOUT": "600000", "MAX_MCP_OUTPUT_TOKENS": "60000"}


def cli():
    found = os.environ.get("FACULTY_CLAUDE_CLI") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    return found if Path(found).is_file() and os.access(found, os.X_OK) else ""


def signed_in():
    """Whether the Claude Code binary holds a sign-in. Only the file's presence is looked at, never its contents."""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return (home / ".credentials.json").is_file()


LIST_SECONDS = 30  # How long Browse waits for a subscription's program to name its models before the built-in list stands in.


def _stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)  # The program and anything it started (`start_new_session`).
    except (ProcessLookupError, PermissionError):
        pass


@contextmanager
def asking(command, env, program):
    """A subscription's program started to say which models it offers, never to run one: JSON lines in, JSON lines out, in a scratch
    folder under TMPDIR, and stopped with everything it started when the block ends or after `LIST_SECONDS`. Yields `send(message)`
    and `answer(accept)`, the first message from the program that `accept` takes."""
    work = tempfile.mkdtemp(prefix="faculty-models-")
    try:
        process = subprocess.Popen(command, cwd=work, env={**env, "TMPDIR": work}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    except OSError as error:
        shutil.rmtree(work, ignore_errors=True)
        raise ProviderError(f"{program} could not be started ({type(error).__name__}).", confirmed=True) from None
    late = threading.Event()
    timer = threading.Timer(LIST_SECONDS, lambda: (late.set(), _stop(process)))
    timer.daemon = True
    timer.start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def answer(accept):
        for line in process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and accept(message):
                return message
        try:
            code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        raise ProviderError(f"{program} did not name its models within {LIST_SECONDS} s." if late.is_set() else
                            f"{program} stopped before naming its models (exit {code}).", confirmed=True)
    try:
        yield send, answer
    except OSError as error:
        raise ProviderError(f"{program} could not be asked for its models ({type(error).__name__}).", confirmed=True) from None
    finally:
        timer.cancel()
        _stop(process)
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except OSError:
                pass
        process.wait()
        shutil.rmtree(work, ignore_errors=True)


def listed_models():
    """The models the Claude subscription offers now, as the installed Claude Code names them when it starts (its `initialize`
    answer), each with the reasoning levels Claude Code offers for it. Asked only when the instructor browses the plan's models
    (grading.md "CLAUDE SUBSCRIPTION CONNECTIONS"): no model runs, nothing of Faculty's is sent and nothing is kept. The same
    isolation as a grading call: a plain environment, no tools, MCP servers, settings or slash commands, no saved session."""
    binary = cli()
    if not binary:
        raise ProviderError("The claude command is not installed for the account Faculty runs as.", confirmed=True, fixable=False)
    if not signed_in():
        raise ProviderError("Claude Code is not signed in for the account Faculty runs as: run `claude` once in a terminal and sign in.",
                            confirmed=True, fixable=False)
    command = [binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--tools", "", "--strict-mcp-config",
               "--safe-mode", "--no-session-persistence", "--disable-slash-commands", "--setting-sources", ""]
    env = {k: os.environ[k] for k in ENV if k in os.environ}
    env["DISABLE_AUTOUPDATER"] = "1"
    with asking(command, env, "Claude Code") as (send, answer):
        send({"type": "control_request", "request_id": "faculty-models", "request": {"subtype": "initialize"}})
        reply = answer(lambda message: message.get("type") == "control_response")
    response = reply.get("response") if isinstance(reply.get("response"), dict) else {}
    if response.get("subtype") == "error":
        raise ProviderError(f"Claude Code did not list its models: {str(response.get('error') or '')[:200].rstrip('.')}.", confirmed=True)
    rows = (response.get("response") if isinstance(response.get("response"), dict) else response).get("models")
    if not isinstance(rows, list):
        raise ProviderError("Claude Code's answer named no models.", confirmed=True)
    found = {}
    # "default" is another name for one of the models listed after it: the model's own row names it.
    for row in sorted((r for r in rows if isinstance(r, dict)), key=lambda r: r.get("value") == "default"):
        model = row.get("resolvedModel") or row.get("value")
        if not isinstance(model, str) or model in found:
            continue
        named = str(row.get("displayName") or "") if row.get("value") != "default" else ""
        found[model] = {"id": model, "name": (named if named.startswith("Claude") else f"Claude {named}") if named else model, "description": str(row.get("description") or ""),
                        "efforts": [str(e) for e in row.get("supportedEffortLevels") or []] if row.get("supportsEffort") else [],
                        "default_effort": FACTS["default_effort"], "vision": True}
    return list(found.values())


class AgentSdkEndpoint(BundleMixin, CloudGradingProvider):
    """One Claude model on the instructor's subscription. `endpoint`: a connection of kind `agent_sdk`. The ChatGPT subscription
    (`codex_sdk.CodexEndpoint`) is the same connection with another binary: it overrides what these class attributes name and `_run`."""
    kind = KIND
    PLAN, BILLING, UPSTREAM, LINEAGE = "Claude subscription", "claude_subscription", "claude-subscription", "claude"
    BINARY, SIGN_IN = "the claude command", "run `claude` once in a terminal and sign in"
    _paused = {}        # (kind, model) -> (monotonic end, message): the subscription's usage limit was reached
    _busy = {}          # (kind, model) -> (monotonic end, busy answers in a row): the service is at capacity (`CAPACITY_WAIT`)

    def __init__(self, endpoint, timeout=None):
        self.endpoint, self.timeout = endpoint, timeout
        self.name, self.model = endpoint["name"], endpoint["model"]
        self.bundle_size = int(endpoint.get("bundle_size") or BUNDLE_SIZE)
        self.parallel = int(endpoint.get("sessions") or SESSIONS)

    def _bundle_size(self):
        return self.bundle_size

    def _bundle_parallel(self):
        return self.parallel

    # What the settings page and a probe read ----------------------------------------------------------------------
    def capabilities(self):
        e = self.endpoint
        return {"provider": self.kind, "endpoint": self.name, "model": self.model, "lineage": e.get("lineage") or self.LINEAGE, "image_input": e["modality"] == "vision",
                "schema_constrained_output": True, "json_output": True, "separated_reasoning": True, "batch_api": False, "durable_call_ids": False,
                "max_inflight": self.bundle_size * self.parallel, "bundle_size": self.bundle_size, "sessions": self.parallel,
                "rate_limits": "the subscription's usage limits: a limit reached pauses this connection until its reset time",
                "price_per_million_input": 0.0, "price_per_million_output": 0.0, "billing": self.PLAN, "upstream": None,
                **({"api_fallback": {"enabled": True, "configured": self._api_configured()}} if self._api_enabled() else {})}

    @staticmethod
    def binary():
        return cli()

    @staticmethod
    def signed_in():
        return signed_in()

    @staticmethod
    def models():
        return [m for m, _ in MODELS]

    def configured(self):
        return bool(self.binary()) and self.signed_in()

    def health(self, listing=None):
        installed, signed = bool(self.binary()), self.signed_in()
        status = "not_configured" if not installed else "authentication_failed" if not signed else "available"
        return {"status": status, "model": self.model, "endpoint": self.name, "lineage": self.endpoint.get("lineage") or self.LINEAGE,
                **({"reason": f"{self.BINARY[0].upper()}{self.BINARY[1:]} is not installed for the account Faculty runs as."} if not installed else
                   {"reason": f"It is not signed in for the account Faculty runs as: {self.SIGN_IN}."} if not signed else {})}

    def listing(self):
        return {"status": self.health()["status"], "models": self.models()}

    def cost(self, usage):
        if usage.get("billing") == "anthropic_api":
            cost = usage.get("api_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                return float(cost), "estimated by Claude Code for direct Anthropic API usage"
            return None, "direct Anthropic API cost was not reported"
        return 0.0, f"included in the {self.PLAN} (no API billing)"

    def _api_enabled(self):
        return self.kind == KIND and self.endpoint.get("api_fallback") is True

    def _api_configured(self):
        return self._api_enabled() and bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    def _can_use_api(self, failure):
        # A confirmed subscription rejection is safe to resend. Partial/uncertain work and ordinary rate limits are not.
        return self._api_configured() and failure.confirmed is True and failure.confirmation.get("usage_limit") is True

    # Requests -------------------------------------------------------------------------------------------------------
    @staticmethod
    def _image(data):
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode()}}

    def submit(self, request):
        if request.get("images") and self.endpoint["modality"] != "vision":
            raise ProviderError(f"Endpoint {self.name} is text-only and cannot receive response images.", confirmed=None)
        try:
            self._check_pause()
        except ProviderError as failure:
            if not self._can_use_api(failure):
                raise
        # A request with tools (`tools.py`, drafting only) always goes alone: its model works through its own calls.
        return self._join(request) if self.bundled(request) and not request.get("tools") else self._single(request)

    def _bundle_single(self, request):
        return self._single(request, held=True)  # The bundle's leader already holds one of the connection's sessions.

    def _single(self, request, held=False):
        name, count = request["prompt"], bundles.crops(request)
        instruction, metadata = prompt(name)
        tools = request["tools"].session() if request.get("tools") else None
        if tools is not None:
            instruction += tools.toolset.preface()
        note = bundles.crop_note(name, count)
        schema = bundles.schema_for(request, count)
        content = [{"type": "text", "text": ((note + "\n\n") if note else "") + json.dumps(request["payload"], ensure_ascii=False, allow_nan=False)}]
        content.extend(self._image(i) for i in request.get("images") or [])
        try:
            if held:
                result, events = self._attempt(instruction, content, schema, tools=tools)
            else:
                with self._session():
                    result, events = self._attempt(instruction, content, schema, tools=tools)
        except ProviderError as failure:
            if tools is not None:
                failure.metadata = {**failure.metadata, "tools": tools.receipt()}  # what the tools did is kept with a failed call too
            raise
        metadata = self._metadata(metadata, instruction, schema, request, result, events)
        if tools is not None:
            metadata["tools"] = tools.receipt()
        if schema is None:
            text = str(result.get("result") or "").strip()
            return {"state": "completed", "result": {"output": {"text": text}, "raw": self._raw(result, None), "metadata": metadata}}
        output = result.get("structured_output")
        if not isinstance(output, dict):
            raise ProviderError("Model output was malformed or incomplete. No grade was accepted.", raw=self._raw(result, None), metadata=metadata,
                                confirmed=True, confirmation={"malformed": True})
        return {"state": "completed", "result": {"output": output, "raw": self._raw(result, None), "metadata": metadata}}

    def _bundle_call(self, plan):
        instruction, metadata = prompt(plan["name"])
        instruction += plan["preface"]
        content = []
        for (label, text), item in zip(bundles.plan_texts(plan), [None, *plan["items"]]):
            content.append({"type": "text", "text": text})
            if item:
                content.extend(self._image(i) for i in item["images"])
        result, events = self._attempt(instruction, content, plan["wrapper"], bundled=len(plan["items"]))
        return result.get("structured_output"), {"result": result, "events": events, "instruction": instruction, "metadata": metadata, "size": len(plan["items"])}

    def _bundle_receipt(self, context, request, answer, share, schema):
        meta = {**self._metadata(context["metadata"], context["instruction"], schema, request, context["result"], context["events"], share=context["size"]), "bundle": share}
        return self._raw(context["result"], answer, share), meta

    def _session(self):
        semaphore, abort = self._semaphore(), current_abort()

        class Held:
            def __enter__(self_):
                while not semaphore.acquire(timeout=.5):
                    if abort is not None:
                        abort.check()
                return self_

            def __exit__(self_, *exc):
                semaphore.release()
        return Held()

    def _check_pause(self):
        until, message = self._paused.get((self.kind, self.model), (0, ""))
        remaining = until - time.monotonic()
        if remaining > 0:
            raise ProviderError(message, confirmed=True, status=429, retry_after=int(remaining) + 1, confirmation={"status": 429, "usage_limit": True, "not_sent": True})
        until, _ = self._busy.get((self.kind, self.model), (0, 0))
        remaining = until - time.monotonic()
        if remaining > 0:
            raise ProviderError(f"The {self.PLAN}'s service is at capacity; this connection waits before asking again.", confirmed=True, status=529,
                                retry_after=int(remaining) + 1, confirmation={"status": 529, "capacity": True, "not_sent": True})

    def _attempt(self, *args, **kwargs):
        """One run of the program; a run that answers ends any capacity backoff."""
        try:
            self._check_pause()  # A different request may have reached the limit while this one waited for its session.
            answer = self._run(*args, **kwargs)
        except ProviderError as failure:
            if not self._can_use_api(failure):
                raise
            fallback = {"reason": "subscription_usage_limit", "confirmation": failure.confirmation,
                        "retry_after": failure.retry_after}
            try:
                answer = self._run(*args, **kwargs, api=True)
            except ProviderError as api_error:
                api_error.metadata = {**api_error.metadata, "api_fallback": fallback}
                raise
            answer[0]["faculty_api_fallback"] = fallback
        if (self.kind, self.model) in self._busy:
            with self._guard:
                self._busy.pop((self.kind, self.model), None)
        return answer

    def _capacity(self, said, status=None):
        """The error for a service at capacity, after stepping the connection's backoff (`CAPACITY_WAIT`)."""
        key, now = (self.kind, self.model), time.monotonic()
        with self._guard:
            until, step = self._busy.get(key, (0, 0))
            if until <= now:
                step += 1
                until = now + min(CAPACITY_MAX, CAPACITY_WAIT * 2 ** (step - 1))
                self._busy[key] = (until, step)
        seconds = max(1, math.ceil(until - now))
        return ProviderError(f"The {self.PLAN}'s service is at capacity; this request waits {seconds} s and is asked again. ({said[:200]})", confirmed=True,
                             status=529, retry_after=seconds, confirmation={"status": status or 529, "capacity": True, "backoff": step})

    def _facts(self):
        return FACTS

    def _effort(self):
        facts = self.endpoint.get("reasoning_options") or self._facts()
        choice = self.endpoint.get("reasoning_choice") or facts["default_effort"]
        return choice if choice in facts["efforts"] else None

    def _run(self, instruction, content, schema, bundled=0, tools=None, api=False):
        binary = cli()
        if not binary:
            raise ProviderError("The claude command is not installed for the account Faculty runs as.", confirmed=True, fixable=False)
        if not api and not signed_in():
            raise ProviderError("Claude Code is not signed in for the account Faculty runs as. Run `claude` once in a terminal and sign in, then retry.",
                                confirmed=True, fixable=False)
        abort = current_abort()
        if abort is not None:
            abort.check()
        work = tempfile.mkdtemp(prefix="faculty-claude-")
        try:
            with ExitStack() as stack:
                system = Path(work) / "system.txt"
                system.write_text(instruction)
                if tools is None:
                    access = ["--tools", "", "--strict-mcp-config", "--safe-mode"]
                else:
                    # Faculty's drafting tools and nothing else (`tools.py`): every built-in tool stays off and the only MCP server is
                    # the relay to this call's socket. Safe mode would drop MCP too, so CLAUDE.md files and auto-memory are switched off
                    # by environment instead; settings, skills and slash commands stay off as in every call.
                    mcp = Path(work) / "mcp.json"
                    mcp.write_text(json.dumps({"mcpServers": {tool_server.SERVER: {
                        "command": sys.executable, "args": [str(tool_server.PROXY), "--socket", stack.enter_context(tools.serving(work))]}}}))
                    access = ["--tools", "", "--strict-mcp-config", "--mcp-config", str(mcp), "--allowedTools", f"mcp__{tool_server.SERVER}"]
                command = [binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--model", self.model,
                           *access, "--no-session-persistence", "--disable-slash-commands", "--setting-sources", "", "--system-prompt-file", str(system)]
                if schema is not None:
                    command += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
                if effort := self._effort():
                    command += ["--effort", effort]
                # GRADING CLAUDE API FALLBACK: subscription first; only an opted-in fallback subprocess gets the API credential.
                env = {k: os.environ[k] for k in ENV if k in os.environ}
                if api:
                    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
                    if not key:
                        raise ProviderError("Direct Anthropic API fallback needs ANTHROPIC_API_KEY in Faculty's grading credentials.",
                                            confirmed=True, fixable=False)
                    env["ANTHROPIC_API_KEY"] = key
                    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
                    if workspace:
                        if not re.fullmatch(r"wrkspc_[A-Za-z0-9]{1,100}", workspace):
                            raise ProviderError("ANTHROPIC_WORKSPACE_ID must be the workspace ID from the Claude Console.",
                                                confirmed=True, fixable=False)
                        env["ANTHROPIC_CUSTOM_HEADERS"] = "anthropic-workspace-id: " + workspace
                # No output cap of ours: each model answers up to its own maximum, as Claude Code reports it (2026-09-27: Haiku 4.5 32,000,
                # Sonnet 5 64,000, Opus 5.5 and Fable 5.1 128,000; a fixed 64,000 halved the last two and exceeded Haiku's).
                env.update(TMPDIR=work, DISABLE_AUTOUPDATER="1")
                if tools is not None:
                    env.update(TOOL_ENV)
                message = json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False)
                process = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           start_new_session=True)
                if abort is not None:
                    abort.attach(process)  # Cancelling the batch ends the process (`base._shut`).
                try:
                    stdout, stderr = process.communicate(message + "\n")
                finally:
                    if process.poll() is None:
                        process.kill()
                if abort is not None and abort.stopped:
                    raise Cancelled(abort.reason, sent=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        events, result, progressed = [], None, False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "rate_limit_event":
                events.append(event.get("rate_limit_info") or {})
            elif event.get("type") == "result":
                result = event
            elif (event.get("type") == "assistant" and not event.get("isApiErrorMessage") and not event.get("error")
                  and (event.get("message") or {}).get("content")):
                progressed = True
        limited = next((e for e in reversed(events) if e.get("status") == "rejected"), None)
        # Provider errors may echo authentication details. A fallback key never reaches a receipt or log.
        redact = lambda value: str(value).replace(key, "[redacted]") if api else str(value)
        if result is None:
            if limited and not api:
                self._pause(limited)
            raise ProviderError(f"Claude Code ended without an answer (exit {process.returncode}). " + redact((stderr.strip().splitlines() or [""])[-1])[:300],
                                confirmed=False)
        if result.get("is_error") or result.get("subtype") != "success":
            said = redact(result.get("result") or result.get("subtype") or "")
            status = result.get("api_error_status")
            if not api and (limited or "usage limit" in said.casefold() or "hit your limit" in said.casefold()):
                seconds = self._pause(limited or {})
                raise ProviderError(f"The Claude subscription's usage limit was reached; this connection waits until it resets. ({said[:200]})",
                                    confirmed=not progressed, status=429, retry_after=seconds, confirmation={"status": 429, "usage_limit": True})
            if status == 429:
                raise ProviderError(f"{'Direct Anthropic API' if api else 'Claude Code'} is rate limited; retrying under the normal rate-limit policy.",
                                    confirmed=not progressed, status=429, retry_after=60, confirmation={"status": 429})
            if api and "anthropic-workspace-id" in said.casefold():
                raise ProviderError("This Anthropic API key needs a workspace. Set ANTHROPIC_WORKSPACE_ID in Faculty's grading credentials, "
                                    "or use a key scoped to one workspace.", confirmed=True, fixable=False, status=400)
            if status in (401, 403) or "login" in said.casefold() or "not logged in" in said.casefold():
                raise ProviderError("Direct Anthropic API authentication was refused. Check Faculty's ANTHROPIC_API_KEY." if api else
                                    "Claude Code's sign-in was refused. Run `claude` in a terminal and sign in again, then retry.", confirmed=True, fixable=False,
                                    status=status or 401)
            if "output token" in said.casefold() and bundled > 1:
                raise ProviderError(f"The bundled answer outgrew the model's output limit; smaller bundles are sent for a while. ({said[:200]})",
                                    confirmed=True, confirmation={"status": status, "output_limit": True, "bundle": bundled})
            if status in (503, 529) or any(word in said.casefold() for word in ("overloaded", "at capacity")):
                # Busy, not wrong: waited for and asked again (`cloud._settle_failure`), never a failed grade or a skipped ruling.
                raise self._capacity(said, status)
            raise ProviderError(f"Claude Code reported an error: {said[:300]}", confirmed=True, status=status if status in (500, 503, 529) else None,
                                confirmation={"status": status, "subtype": result.get("subtype")})
        return result, events

    def _pause(self, info):
        resets = info.get("resetsAt")
        seconds = max(60, int(resets - time.time()) + 5) if isinstance(resets, (int, float)) else 900
        with self._guard:
            self._paused[(self.kind, self.model)] = (time.monotonic() + seconds, f"The {self.PLAN}'s usage limit was reached; this connection waits until it resets.")
        return seconds

    # Receipts -------------------------------------------------------------------------------------------------------
    def _metadata(self, metadata, instruction, schema, request, result, events, share=1):
        usage = result.get("usage") or {}
        tokens_in = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        tokens_out = int(usage.get("output_tokens") or 0)
        split = lambda value: value // share if share > 1 else value
        window = next((e for e in reversed(events) if e.get("rateLimitType") or e.get("unifiedWindows")), {})
        api = result.get("faculty_api_fallback")
        cost = result.get("total_cost_usd")
        api_cost = cost / share if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0 else None
        return {**metadata, "provider": self.kind, "endpoint": self.name, "requested_model": self.model, "lineage": self.endpoint.get("lineage") or self.LINEAGE,
                "reported_model": next(iter(result.get("modelUsage") or {}), self.model), "upstream": "anthropic-api" if api else self.UPSTREAM,
                **({"api_fallback": api} if api else {}),
                "provider_request_id": result.get("session_id"), "instruction_sha256": digest(instruction.encode()),
                "output_schema_sha256": digest(schema) if schema is not None else None, **({"text_only": True} if schema is None else {}),
                "structured": "json_schema", "temperature": None, "temperature_note": f"the {self.PLAN} samples as its agent decides",
                "reasoning": self._effort(), "reasoning_separated": True,
                "request_sha256": digest({"prompt": request["prompt"], "payload": request["payload"], "images": [digest(i) for i in request.get("images") or []],
                                          "model": self.model, "effort": self._effort(),
                                          **({"tools": request["tools"].fingerprint} if request.get("tools") else {})}),
                "usage": {"prompt_tokens": split(tokens_in), "completion_tokens": split(tokens_out), "total_tokens": split(tokens_in + tokens_out),
                          "billing": "anthropic_api" if api else self.BILLING, "notional_cost_usd": result.get("total_cost_usd"),
                          **({"api_cost_usd": api_cost} if api else {}), **({"bundle_size": share} if share > 1 else {}),
                          **({"subscription_window": {"type": window.get("rateLimitType"), "utilization": window.get("utilization"), "status": window.get("status")}} if window and not api else {})},
                "seconds_reported": (result.get("duration_ms") or 0) / 1000}

    @staticmethod
    def _raw(result, answer, bundle=None):
        """The private receipt of one member: its own answer, and the call's own facts. Never another item's answer."""
        facts = {k: result.get(k) for k in ("session_id", "num_turns", "duration_ms", "duration_api_ms", "stop_reason", "terminal_reason", "usage", "modelUsage")}
        return {"claude_code": facts, **({"answer": answer} if bundle else {"structured_output": result.get("structured_output"), "result": result.get("result")}),
                **({"bundle": bundle} if bundle else {})}
