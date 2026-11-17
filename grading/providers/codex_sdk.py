"""Grading on the instructor's ChatGPT subscription through Codex (requirement, 2026-09-27).

The same connection as the Claude subscription (`agent_sdk`: bundles, whose-answer check, concurrency, usage-limit pause, zero cost),
with the Codex binary: `codex exec` is the non-interactive run the Codex SDKs drive. It runs as published, signed in with ChatGPT
through OpenAI's own flow: Faculty checks only that `auth.json` exists and never reads it. Every call runs in a fresh empty folder,
READ-ONLY sandbox, no saved session, without the instructor's Codex config or rules, with web search off and every tool that could act
(apps and connectors, plugins, browser and computer use, image generation, the shell, sub-agents, skills, hooks) switched off, and a
plain environment (never `OPENAI_API_KEY`, which would bill the API). Codex has no system-prompt flag, so the instruction opens the
prompt. Images go as files (`-i`), in order, and the prompt marks where each belongs ("[image 3]"), so a bundle still says which
response each image is from. Codex adds its own base instructions (about 9,600 tokens measured on 2026-09-27) to every call. A drafting
request that carries Faculty's own tools (`tools.py`, grading.md "DRAFTING TOOLS") gets them as its only MCP server, nothing more.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from . import tools as tool_server
from .agent_sdk import AgentSdkEndpoint, asking
from .base import Cancelled, ProviderError, current_abort

KIND = "codex_sdk"
# The plan's models with the reasoning levels each offers. Browse lists what the installed Codex offers today (`listed_models`;
# requirement, 2026-10-01: GPT-6.1-Sol and the other GPT-6 models were missing); this list, from Codex's own on 2026-10-01, is what it
# falls back on and what a connection saved without its levels takes them from. "ultra" is left out everywhere: it adds automatic
# task delegation, which a grading call must not do. The first is a new connection's model.
MODELS = (("gpt-6-astra", "GPT-6-Astra", ("low", "medium", "high", "xhigh", "max"), "medium"),
          ("gpt-6.1-sol", "GPT-6.1-Sol", ("low", "medium", "high", "xhigh", "max"), "low"),
          ("gpt-6-sol", "GPT-6-Sol", ("low", "medium", "high", "xhigh", "max"), "medium"),
          ("gpt-6-luna", "GPT-6-Luna", ("low", "medium", "high", "xhigh", "max"), "medium"),
          ("gpt-5.6-sol", "GPT-5.6-Sol", ("low", "medium", "high", "xhigh", "max"), "low"),
          ("gpt-5.6-terra", "GPT-5.6-Terra", ("low", "medium", "high", "xhigh", "max"), "medium"),
          ("gpt-5.6-luna", "GPT-5.6-Luna", ("low", "medium", "high", "xhigh", "max"), "medium"),
          ("gpt-5.5", "GPT-5.5", ("low", "medium", "high", "xhigh"), "medium"))
FALLBACK_FACTS = {"supported": True, "mandatory": True, "efforts": ["low", "medium", "high", "xhigh"], "default_effort": "medium"}
DISABLED = ("apps", "plugins", "remote_plugin", "browser_use", "browser_use_external", "browser_use_full_cdp_access", "computer_use",
            "image_generation", "in_app_browser", "in_app_local_automation", "multi_agent", "shell_tool", "unified_exec", "skill_search",
            "skill_mcp_dependency_install", "tool_suggest", "sleep_tool", "goals", "hooks", "code_mode_host", "workspace_dependencies")
TOOLS_DISABLED = tuple(f for f in DISABLED if f != "code_mode_host")  # a call with drafting tools (`tools.py`)
ENV = ("HOME", "PATH", "LANG", "LC_ALL", "USER", "LOGNAME", "CODEX_HOME")
LIMIT_WORDS = ("usage limit", "rate limit", "hit your limit", "too many requests")
# The service is busy, not wrong (2026-09-27: "Selected model is at capacity. Please try a different model." failed three flag reviews
# of a trial batch, which were then skipped as failed rulings). Waited for and asked again, like an overloaded host.
CAPACITY_WORDS = ("at capacity", "overloaded", "server is busy", "temporarily unavailable", "try again later", "529", "503")
# Waited for with the connection's backoff (`agent_sdk.CAPACITY_WAIT`).


def facts(model):
    found = next((m for m in MODELS if m[0] == model), None)
    return {"supported": True, "mandatory": True, "efforts": list(found[2]), "default_effort": found[3]} if found else dict(FALLBACK_FACTS)


def cli():
    found = os.environ.get("FACULTY_CODEX_CLI") or shutil.which("codex") or str(Path.home() / ".local/bin/codex")
    return found if Path(found).is_file() and os.access(found, os.X_OK) else ""


def signed_in():
    """Whether Codex holds a sign-in. Only the file's presence is looked at, never its contents."""
    return (Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json").is_file()


def listed_models():
    """The models the ChatGPT plan offers now, as the installed Codex lists them (`codex app-server`, `model/list`, every page), each
    with its reasoning levels and whether it reads images; models Codex hides from its own picker are left out. Asked only when the
    instructor browses the plan's models (grading.md "CHATGPT SUBSCRIPTION"): no model runs, no thread starts, nothing of Faculty's is
    sent and nothing is kept. A plain environment as in a grading call, with no MCP servers and every acting feature off."""
    binary = cli()
    if not binary:
        raise ProviderError("The codex command is not installed for the account Faculty runs as.", confirmed=True, fixable=False)
    if not signed_in():
        raise ProviderError("Codex is not signed in for the account Faculty runs as: run `codex login` in a terminal and sign in with ChatGPT.",
                            confirmed=True, fixable=False)
    command = [binary, "app-server", "--listen", "stdio://", "-c", "mcp_servers={}", "-c", 'web_search="disabled"']
    for feature in DISABLED:
        command += ["--disable", feature]
    env = {k: os.environ[k] for k in ENV if k in os.environ}
    rows, cursor = [], None
    with asking(command, env, "Codex") as (send, answer):
        def ask(number, method, params):
            send({"jsonrpc": "2.0", "id": number, "method": method, "params": params})
            reply = answer(lambda message: message.get("id") == number)
            if "error" in reply or not isinstance(reply.get("result"), dict):
                said = reply.get("error", {}).get("message", "") if isinstance(reply.get("error"), dict) else ""
                raise ProviderError(f"Codex did not list its models: {str(said)[:200].rstrip('.')}.", confirmed=True)
            return reply["result"]
        ask(0, "initialize", {"clientInfo": {"name": "faculty-models", "version": "1"}})
        send({"jsonrpc": "2.0", "method": "initialized"})
        for page in range(1, 21):
            listed = ask(page, "model/list", {"cursor": cursor} if cursor else {})
            rows += [row for row in listed.get("data") or [] if isinstance(row, dict)]
            cursor = listed.get("nextCursor")
            if not cursor:
                break
    return [{"id": row.get("model") or row.get("id"), "name": str(row.get("displayName") or row.get("model") or row.get("id") or ""),
             "description": str(row.get("description") or ""), "default_effort": row.get("defaultReasoningEffort"),
             "efforts": [e.get("reasoningEffort") if isinstance(e, dict) else e for e in row.get("supportedReasoningEfforts") or []],
             "vision": "image" in (row.get("inputModalities") or ["text", "image"])} for row in rows if not row.get("hidden")]


def wait_seconds(text):
    """How long a usage-limit message says to wait ("try again in 2 hours 13 minutes"), else 15 minutes."""
    said = text.casefold()
    hours = re.search(r"(\d+)\s*(?:hours?|hrs?|h)\b", said)
    minutes = re.search(r"(\d+)\s*(?:minutes?|mins?|m)\b", said)
    days = re.search(r"(\d+)\s*days?\b", said)
    total = (int(days.group(1)) * 86400 if days else 0) + (int(hours.group(1)) * 3600 if hours else 0) + (int(minutes.group(1)) * 60 if minutes else 0)
    return max(60, total + 5) if total else 900


class CodexEndpoint(AgentSdkEndpoint):
    """One OpenAI model on the instructor's ChatGPT subscription. `endpoint`: a connection of kind `codex_sdk`."""
    kind = KIND
    PLAN, BILLING, UPSTREAM, LINEAGE = "ChatGPT subscription", "chatgpt_subscription", "chatgpt-subscription", "gpt"
    BINARY, SIGN_IN = "the codex command", "run `codex login` once in a terminal and sign in with ChatGPT"

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
        return {"type": "image", "bytes": data}  # Written to a file for `-i`; the prompt marks its place.

    def _run(self, instruction, content, schema, bundled=0, tools=None):
        binary = self.binary()
        if not binary:
            raise ProviderError("The codex command is not installed for the account Faculty runs as.", confirmed=True, fixable=False)
        if not self.signed_in():
            raise ProviderError("Codex is not signed in for the account Faculty runs as. Run `codex login` in a terminal and sign in with ChatGPT, then retry.",
                                confirmed=True, fixable=False)
        abort = current_abort()
        if abort is not None:
            abort.check()
        work = tempfile.mkdtemp(prefix="faculty-codex-")
        started = time.monotonic()
        stack = ExitStack()
        try:
            text, images = [instruction, ""], []
            for part in content:
                if part["type"] == "image":
                    path = Path(work) / f"image-{len(images) + 1}.png"
                    path.write_bytes(part["bytes"])
                    images.append(str(path))
                    text.append(f"[image {len(images)}]")
                else:
                    text.append(part["text"])
            if images:
                text.insert(1, f"\n{len(images)} images are attached in order; each [image N] above or below marks where image N belongs.")
            command = [binary, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
                       "--sandbox", "read-only", "--cd", work, "-m", self.model, "-c", 'web_search="disabled"']
            if tools is not None:
                # Faculty's drafting tools (`tools.py`) as the only MCP server (the instructor's own Codex config is ignored), approved
                # without asking; this Codex calls MCP tools through code mode, so `code_mode_host` stays on for such a call alone.
                relay = [str(tool_server.PROXY), "--socket", stack.enter_context(tools.serving(work))]
                command += ["-c", 'approval_policy="never"', "-c", f"mcp_servers.{tool_server.SERVER}.command={json.dumps(sys.executable)}",
                            "-c", f"mcp_servers.{tool_server.SERVER}.args={json.dumps(relay)}",
                            "-c", f'mcp_servers.{tool_server.SERVER}.default_tools_approval_mode="approve"',
                            "-c", f"mcp_servers.{tool_server.SERVER}.tool_timeout_sec=600"]
            for feature in DISABLED if tools is None else TOOLS_DISABLED:
                command += ["--disable", feature]
            if effort := self._effort():
                command += ["-c", f'model_reasoning_effort="{effort}"']
            if schema is not None:
                schema_path = Path(work) / "schema.json"
                schema_path.write_text(json.dumps(schema))
                command += ["--output-schema", str(schema_path)]
            for path in images:
                command += ["-i", path]
            command.append("-")  # The prompt comes on stdin.
            # A plain environment: the sign-in is Codex's own, and no service credential (an API key would bill the API) goes along.
            env = {k: os.environ[k] for k in ENV if k in os.environ}
            env.update(TMPDIR=work)
            process = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       start_new_session=True)
            if abort is not None:
                abort.attach(process)  # Cancelling the batch ends the process (`base._shut`).
            try:
                stdout, stderr = process.communicate("\n\n".join(text))
            finally:
                if process.poll() is None:
                    process.kill()
            if abort is not None and abort.stopped:
                raise Cancelled(abort.reason, sent=True)
        finally:
            stack.close()
            shutil.rmtree(work, ignore_errors=True)
        messages, usage, thread, errors, finished = [], {}, None, [], False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "thread.started":
                thread = event.get("thread_id")
            elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
                messages.append(event["item"].get("text") or "")
            elif kind == "turn.completed":
                usage, finished = event.get("usage") or {}, True
            elif kind in ("turn.failed", "error"):
                errors.append(str((event.get("error") or {}).get("message") or event.get("message") or kind))
        said = " ".join(errors) or (stderr.strip().splitlines() or [""])[-1]
        if not finished:
            if any(word in said.casefold() for word in LIMIT_WORDS):
                seconds = self._pause({"resetsAt": time.time() + wait_seconds(said)})
                raise ProviderError(f"The ChatGPT subscription's usage limit was reached; this connection waits until it resets. ({said[:200]})",
                                    confirmed=True, status=429, retry_after=seconds, confirmation={"status": 429, "usage_limit": True})
            if any(word in said.casefold() for word in CAPACITY_WORDS):
                raise self._capacity(said)
            if any(word in said.casefold() for word in ("not logged in", "login", "unauthorized", "401", "403")):
                raise ProviderError("Codex's sign-in was refused. Run `codex login` in a terminal and sign in with ChatGPT again, then retry.",
                                    confirmed=True, fixable=False, status=401)
            if bundled > 1 and any(word in said.casefold() for word in ("max_output_tokens", "output token", "context length", "too long")):
                raise ProviderError(f"The bundled answer outgrew the model's limit; smaller bundles are sent for a while. ({said[:200]})",
                                    confirmed=True, confirmation={"output_limit": True, "bundle": bundled})
            if process.returncode not in (0, None) and not errors:
                raise ProviderError(f"Codex ended without an answer (exit {process.returncode}). {said[:300]}", confirmed=False)
            raise ProviderError(f"Codex reported an error: {said[:300]}", confirmed=True)
        final = messages[-1].strip() if messages else ""
        structured = None
        if schema is not None:
            try:
                structured = json.loads(final.strip("`").removeprefix("json").strip() if final.startswith("```") else final)
            except ValueError:
                structured = None
        # Shaped like Claude Code's result, so receipts, usage and parsing are one code path (`AgentSdkEndpoint._metadata`, `_raw`).
        result = {"subtype": "success", "result": final, "structured_output": structured, "session_id": thread, "num_turns": 1,
                  "duration_ms": int((time.monotonic() - started) * 1000), "modelUsage": {self.model: {}}, "total_cost_usd": None,
                  "usage": {"input_tokens": int(usage.get("input_tokens") or 0), "output_tokens": int(usage.get("output_tokens") or 0) + int(usage.get("reasoning_output_tokens") or 0),
                            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "cached_input_tokens": int(usage.get("cached_input_tokens") or 0)}}
        return result, []
