#!/usr/bin/env python3
"""A stand-in for `codex exec --json`, for tests of `grading/providers/codex_sdk.py`.

It answers every item as `fake_openai.default_answer` answers the same request, writes a log line per run (its flags, images and
environment) beside its control file, and can be scripted ("limit", "error") through `fake_codex.json`, like `fake_claude.py`. Given an
MCP server (`-c mcp_servers.<name>.command=...` and `.args=...`, a drafting request's tools), it calls the scripted `tool_calls` through
it before answering, and logs what they answered. As `codex app-server` (`codex_sdk.listed_models`) it answers `initialize` and pages
`model/list` two models at a time from the control file's `models` (Codex's own rows, `MODELS` by default); `"listing": "error"`
refuses the list.
"""
import json
import os
import stat
import sys
from pathlib import Path


def _model(model, name, efforts, default, hidden=False, inputs=("text", "image")):
    return {"id": model, "model": model, "displayName": name, "description": f"{name} for tests.", "hidden": hidden, "isDefault": False,
            "supportedReasoningEfforts": [{"reasoningEffort": e, "description": ""} for e in efforts], "defaultReasoningEffort": default,
            "inputModalities": list(inputs)}


# Rows as Codex 0.159 lists them: "ultra" is offered on several models, one is hidden from its picker, one reads text only.
MODELS = [_model("gpt-6.1-sol", "GPT-6.1-Sol", ("low", "medium", "high", "xhigh", "max", "ultra"), "low"),
          _model("gpt-6-astra", "GPT-6-Astra", ("low", "medium", "high", "xhigh", "max", "ultra"), "medium"),
          _model("gpt-6-internal", "GPT-6-Internal", ("low", "medium"), "low", hidden=True),
          _model("gpt-6-luna", "GPT-6-Luna", ("low", "medium", "high", "xhigh", "max"), "medium"),
          _model("gpt-6-text", "GPT-6-Text", ("low", "high", "ultra"), "ultra", inputs=("text",))]


def serve(control_path, args):
    control = json.loads(Path(control_path).read_text())
    rows = control.get("models", MODELS)
    with open(str(control_path) + ".log", "a") as log:
        log.write(json.dumps({"listing": True, "args": args, "pid": os.getpid(), "api_key": "OPENAI_API_KEY" in os.environ}) + "\n")
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue  # a notification ("initialized")
        method, params = message["method"], message.get("params") or {}
        if method == "initialize":
            reply = {"result": {"userAgent": "fake-codex"}}
            print(json.dumps({"jsonrpc": "2.0", "method": "account/updated", "params": {}}), flush=True)  # not the answer
        elif method == "model/list" and control.get("listing") == "error":
            reply = {"error": {"code": -32600, "message": "not signed in"}}
        elif method == "model/list":
            start = int(params.get("cursor") or 0)
            reply = {"result": {"data": rows[start:start + 2], "nextCursor": str(start + 2) if start + 2 < len(rows) else None}}
        else:
            reply = {"error": {"code": -32601, "message": f"unknown method {method}"}}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], **reply}), flush=True)


def install(directory, repository_tests):
    directory = Path(directory)
    target = directory / "codex"
    target.write_text(f"#!{sys.executable}\nimport sys; sys.path.insert(0, {str(repository_tests)!r})\nimport fake_codex\nfake_codex.main({str(directory / 'fake_codex.json')!r})\n")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    control = directory / "fake_codex.json"
    control.write_text(json.dumps({"script": ""}))
    return str(target), control


def main(control_path):
    from fake_openai import default_answer
    args = sys.argv[1:]
    if args[:1] == ["app-server"]:
        return serve(control_path, args)
    values = lambda flag: [args[i + 1] for i, a in enumerate(args) if a == flag]
    prompt = sys.stdin.read()
    schema = json.loads(Path(values("--output-schema")[0]).read_text()) if values("--output-schema") else None
    script = json.loads(Path(control_path).read_text()).get("script", "")
    parts = prompt.split("\n\n")
    bundled = "BUNDLED REQUEST" in prompt
    payload_at = next((i for i, p in enumerate(parts) if p.strip().startswith("{")), len(parts))
    instruction = prompt.split("\n\nBUNDLED REQUEST")[0] if bundled else "\n\n".join(parts[:payload_at])
    items = []
    if bundled:
        shared = next(json.loads(p.split("\n", 1)[1]) for p in parts if p.startswith("SHARED fields"))
        for p in parts:
            if p.startswith("ITEM "):
                items.append({"label": p.split(".", 1)[0].removeprefix("ITEM ").strip(), "payload": {**shared, **json.loads(p.split("Fields of this item:\n", 1)[1])}})
            elif p.startswith("[image") and items:
                items[-1].setdefault("images", []).append(p)
    else:
        text = parts[payload_at]
        items.append({"label": "", "payload": json.loads(text), "images": [p for p in parts if p.startswith("[image")]})
    servers = {}
    for setting in values("-c"):
        key, _, raw = setting.partition("=")
        if key.startswith("mcp_servers.") and key.count(".") == 2:
            name, field = key.split(".")[1:]
            servers.setdefault(name, {})[field] = json.loads(raw) if raw[:1] in '["' else raw
    tools = None
    if servers:
        from fake_mcp import call_tools
        [server] = servers.values()
        tools = call_tools(server["command"], server["args"], json.loads(Path(control_path).read_text()).get("tool_calls", []))
    with open(str(control_path) + ".log", "a") as log:
        log.write(json.dumps({"bundled": bundled, "items": len(items), "model": values("-m")[0], "images": len(values("-i")), "disabled": values("--disable"),
                              "mcp": tools, "servers": servers,
                              "sandbox": values("--sandbox"), "config": values("-c"), "api_key": "OPENAI_API_KEY" in os.environ,
                              "ephemeral": "--ephemeral" in args, "ignore_user_config": "--ignore-user-config" in args,
                              "markers": [len(i.get("images", [])) for i in items]}) + "\n")
    emit = lambda event: print(json.dumps(event), flush=True)
    emit({"type": "thread.started", "thread_id": "fake-thread"})
    emit({"type": "turn.started"})
    if script == "limit":
        emit({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again in 2 hours 5 minutes."}})
        return
    if script == "capacity":
        emit({"type": "turn.failed", "error": {"message": "Selected model is at capacity. Please try a different model."}})
        return
    if script == "error":
        emit({"type": "turn.failed", "error": {"message": "stream disconnected before completion"}})
        return
    answers = []
    for item in items:
        body = {"messages": [{"role": "system", "content": instruction},
                             {"role": "user", "content": [{"type": "text", "text": json.dumps(item["payload"])}, *[{"type": "image_url"} for _ in item.get("images", [])]]}]}
        answer = default_answer(body)
        if isinstance(answer, tuple):
            answer = answer[1]
        if item["payload"].get("double") is not None:
            answer = {"sum": 2 * item["payload"]["double"], "digits_seen": str(len(item.get("images", [])))}
        answers.append((item["label"], answer))
    if bundled:
        text = json.dumps({"results": [{"item": label, "answer": answer} for label, answer in answers]})
    else:
        text = answers[0][1] if schema is None else json.dumps(answers[0][1])
    emit({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 9600, "cached_input_tokens": 0, "output_tokens": 40 * len(items), "reasoning_output_tokens": 10}})
