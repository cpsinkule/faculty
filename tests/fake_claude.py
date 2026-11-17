#!/usr/bin/env python3
"""A stand-in for `claude -p --input-format stream-json --output-format stream-json`, for tests of `grading/providers/agent_sdk.py`.

It answers every item the way `fake_openai.default_answer` answers the same request on the fake OpenAI host, so a whole batch can
run on it. The provider gives the binary a plain environment, so its behaviour comes from `fake_claude.json` beside a copy of this
script (`install`): the scripted outcome, and a log of invocations beside it (`fake_claude.json.log`), and optionally a scripted outcome ("limit", "drop:<item>", "error").
Given an MCP server (`--mcp-config`, a drafting request's tools), it calls the `tool_calls` the control file scripts ([name, arguments]
pairs) through it before answering, and logs what they answered. Started without a prompt (`agent_sdk.listed_models`), it answers the
`initialize` control request with the control file's `models` (Claude Code's own rows, `MODELS` by default), or as its `listing` says:
"exit" stops without answering, "silent" never answers.
"""
import json
import os
import stat
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# Rows as Claude Code 2.1 lists them: aliases name the model they resolve to, Haiku has no effort levels, an older model lacks xhigh.
MODELS = [{"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)", "description": "Opus 5.5 · Best for everyday, complex tasks",
           "supportsEffort": True, "supportedEffortLevels": EFFORTS},
          {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5", "description": "Best for everyday, complex tasks",
           "supportsEffort": True, "supportedEffortLevels": EFFORTS},
          {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet 5.5", "description": "Efficient for routine tasks",
           "supportsEffort": True, "supportedEffortLevels": EFFORTS},
          {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku 4.5", "description": "Fastest for quick answers"},
          {"value": "claude-sonnet-5", "resolvedModel": "claude-sonnet-5", "displayName": "Sonnet 5", "description": "Efficient for routine tasks",
           "supportsEffort": True, "supportedEffortLevels": EFFORTS},
          {"value": "claude-sonnet-4-6", "resolvedModel": "claude-sonnet-4-6", "displayName": "Sonnet 4.6", "description": "Efficient for routine tasks",
           "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "max"]},
          {"value": "gpt-oops", "displayName": "Not a Claude model"}]


def install(directory, repository_tests):
    """Copy this script to `directory/claude` with its control file; returns (path, control path)."""
    directory = Path(directory)
    target = directory / "claude"
    target.write_text(f"#!{sys.executable}\nimport sys; sys.path.insert(0, {str(repository_tests)!r})\nimport fake_claude\nfake_claude.main({str(directory / 'fake_claude.json')!r})\n")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    control = directory / "fake_claude.json"
    control.write_text(json.dumps({"script": ""}))
    return str(target), control


def list_models(control_path, args):
    control = json.loads(Path(control_path).read_text())
    with open(str(control_path) + ".log", "a") as log:
        log.write(json.dumps({"listing": True, "args": args, "pid": os.getpid(), "api_key": "ANTHROPIC_API_KEY" in os.environ,
                              "tmpdir": os.environ.get("TMPDIR")}) + "\n")
    request = json.loads(sys.stdin.readline())
    if control.get("listing") == "exit":
        sys.exit(3)
    if control.get("listing") == "silent":
        time.sleep(120)
        return
    print(json.dumps({"type": "system", "subtype": "status"}), flush=True)  # what is not the answer is passed over
    print("not json", flush=True)
    print(json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": request["request_id"],
                                                               "response": {"commands": [], "models": control.get("models", MODELS)}}}), flush=True)
    sys.stdin.readline()  # like Claude Code, it waits for more until it is stopped


def main(control_path):
    from fake_openai import default_answer
    args = sys.argv[1:]
    value = lambda flag: args[args.index(flag) + 1] if flag in args else None
    if "--system-prompt-file" not in args:
        return list_models(control_path, args)
    system = Path(value("--system-prompt-file")).read_text()
    schema = json.loads(value("--json-schema")) if value("--json-schema") else None
    message = json.loads(sys.stdin.readline())["message"]["content"]
    control = json.loads(Path(control_path).read_text())
    script = control.get("script", "")
    if "ANTHROPIC_API_KEY" in os.environ:
        script = control.get("api_script", script)
    texts = [part["text"] for part in message if part["type"] == "text"]
    bundled = "BUNDLED REQUEST" in system
    items = []
    if bundled:
        shared = json.loads(texts[0].split("\n", 1)[1])
        current = None
        for part in message[1:]:
            if part["type"] == "text":
                label = part["text"].split(".", 1)[0].removeprefix("ITEM ").strip()
                current = {"label": label, "payload": {**shared, **json.loads(part["text"].split("Fields of this item:\n", 1)[1])}, "images": []}
                items.append(current)
            else:
                current["images"].append(part)
    else:
        text = texts[0]
        items.append({"label": "", "payload": json.loads(text[text.index("{"):]), "images": [p for p in message if p["type"] == "image"]})
    tools = None
    if value("--mcp-config"):
        from fake_mcp import call_tools
        [server] = json.loads(Path(value("--mcp-config")).read_text())["mcpServers"].values()
        tools = call_tools(server["command"], server["args"], control.get("tool_calls", []))
    # One line per run, appended: runs in parallel sessions must not overwrite each other's record.
    with open(str(control_path) + ".log", "a") as log:
        # What each item was about, so a test can see that one call held one question: its rubric (one question's) and any tag.
        log.write(json.dumps({"bundled": bundled, "items": len(items), "model": value("--model"), "effort": value("--effort"),
                              "rubrics": sorted({json.dumps(i["payload"].get("rubric"), sort_keys=True) for i in items}),
                              "tags": [i["payload"].get("tag") for i in items],
                              "api_key": "ANTHROPIC_API_KEY" in os.environ, "custom_headers": os.environ.get("ANTHROPIC_CUSTOM_HEADERS"),
                              "tools": value("--tools"), "safe_mode": "--safe-mode" in args,
                              "allowed": value("--allowedTools"), "mcp": tools, "system": system[-600:],
                              "env": sorted(k for k in os.environ if k.startswith(("CLAUDE_CODE_", "MCP_", "MAX_MCP")))}) + "\n")
    emit = lambda event: print(json.dumps(event), flush=True)
    emit({"type": "system", "subtype": "init", "apiKeySource": "none", "model": value("--model")})
    if script == "partial_limit":
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Already started work."}]}})
    if script == "limit_message":
        emit({"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit", "message": {
            "content": [{"type": "text", "text": "You've hit your limit"}]}})
    if script in ("limit", "partial_limit", "limit_message"):
        emit({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 4102444800, "rateLimitType": "five_hour", "utilization": 1.0}})
        emit({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429, "result": "You've hit your limit"})
        return
    if script == "rate":
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "api_error_status": 429, "result": "Too many requests"})
        return
    if script == "unknown":
        return
    if script == "workspace_required":
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "api_error_status": 400,
              "result": "This API key is not scoped to a workspace. Set the anthropic-workspace-id header."})
        return
    if script == "error":
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Overloaded", "api_error_status": 529})
        return
    emit({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour", "utilization": .1}})
    answers = []
    for item in items:
        body = {"messages": [{"role": "system", "content": system.split("\n\nBUNDLED REQUEST")[0]},
                             {"role": "user", "content": [{"type": "text", "text": json.dumps(item["payload"])}, *[{"type": "image_url"} for _ in item["images"]]]}]}
        answer = default_answer(body)
        if isinstance(answer, tuple):
            answer = answer[1]  # A plain-text prompt's answer (`read`).
        if item["payload"].get("double") is not None:
            answer = {"sum": 2 * item["payload"]["double"], "digits_seen": item["payload"].get("tag", "")}
        answers.append((item["label"], answer))
    usage = {"input_tokens": 100, "output_tokens": 50 * len(items), "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    result = {"type": "result", "subtype": "success", "is_error": False, "session_id": "fake-session", "num_turns": 2, "duration_ms": 10,
              "total_cost_usd": .01, "usage": usage, "modelUsage": {value("--model"): {}}}
    if bundled:
        entries = [{"item": label, "answer": answer} for label, answer in answers if script != f"drop:{label}"]
        result["structured_output"] = {"results": entries}
    elif schema is None:
        result["result"] = answers[0][1]
    else:
        result["structured_output"] = answers[0][1]
    emit(result)
