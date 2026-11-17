#!/usr/bin/env python3
"""A stand-in for Google's Antigravity CLI (`agy`), for tests of `grading/providers/antigravity.py`.

It behaves as agy 1.3.4 was seen to on 2026-10-14: the prompt arrives as one stream-json line on stdin, the instruction is the body
of the agent definition in the working folder (`.agents/agents/<name>.md`, whose `tools` it obeys), pictures are files it "views"
with `view_file` steps, a schema's answer comes back as `structured_output` after a `finish` step, and every run KEEPS its
conversation under `$HOME/.gemini/antigravity-cli` (which Faculty must delete). It answers every item as `fake_openai.default_answer`
answers the same request, writes a log line per run beside its control file, and is scripted through `fake_agy.json`:
"limit", "capacity", "error", "signed_out", "skip_image" (the last picture is never viewed), "search" (a web search step),
"denied" (an action it refused for want of permission). `agy models` prints the control file's `models` (one name per reasoning
level, `MODELS` by default; `"listing": "error"` refuses, `"silent"` never answers), and `-p /usage` its `usage` groups.

Run as the Assistant's agent (`assistant/engines.AntigravityEngine`: the definition's description says so) it stays open and
answers one turn per stdin line, "Answer N", in two pieces as the real program streams them, with the conversation's usage so
far. A message holding "hang" is never answered (for Stop), "limit" and "denied" end the turn as the real program does. Given
`tool_calls` and the definition's MCP server, it starts that server, lists its tools and calls them before answering.
"""
import json
import os
import stat
import sys
import time
import uuid
from pathlib import Path

# As `agy models` prints them: a slug per reasoning level, a tab, the name with the level in brackets.
MODELS = [("gemini-4-argon-medium", "Gemini 4 Argon (Medium)"), ("gemini-4-argon-high", "Gemini 4 Argon (High)"), ("gemini-4-argon-low", "Gemini 4 Argon (Low)"),
          ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"), ("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)"),
          ("claude-sonnet-5-5-low", "Claude Sonnet 5.5 (Low)"), ("claude-sonnet-5-5-high", "Claude Sonnet 5.5 (High)"),
          ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)"), ("gemini-next", "Gemini Next")]
USAGE = [{"name": "Gemini Models", "buckets": [{"id": "gemini-weekly", "window": "weekly", "remaining_fraction": .6, "reset_time": "2099-01-08T00:00:00Z"},
                                               {"id": "gemini-5h", "window": "5h", "remaining_fraction": 1, "reset_time": "2099-01-01T05:00:00Z"}]},
         {"name": "Claude and GPT models", "buckets": [{"id": "3p-weekly", "window": "weekly", "remaining_fraction": 1, "reset_time": "2099-01-08T00:00:00Z"}]}]
EMPTY = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0}


def install(directory, repository_tests):
    directory = Path(directory)
    target = directory / "agy"
    target.write_text(f"#!{sys.executable}\nimport sys; sys.path.insert(0, {str(repository_tests)!r})\nimport fake_agy\nfake_agy.main({str(directory / 'fake_agy.json')!r})\n")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    control = directory / "fake_agy.json"
    control.write_text(json.dumps({"script": ""}))
    return str(target), control


def main(control_path):
    args, control = sys.argv[1:], json.loads(Path(control_path).read_text())
    script = control.get("script", "")
    values = lambda flag: [args[i + 1] for i, a in enumerate(args) if a == flag]
    home = Path(os.environ["HOME"]) / ".gemini" / "antigravity-cli"
    facts = {"args": args, "pid": os.getpid(), "home": os.environ["HOME"], "tmpdir": os.environ.get("TMPDIR", ""), "cwd": os.getcwd(),
             "api_key": any(key in os.environ for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY")), "environment": sorted(os.environ),
             "signed_in": (home / "antigravity-oauth-token").is_file(), "update_switch": os.environ.get("AGY_CLI_DISABLE_AUTO_UPDATE")}

    def log(**more):
        with open(str(control_path) + ".log", "a") as out:
            out.write(json.dumps({**facts, **more}) + "\n")

    if args[:1] == ["models"]:
        log(listing=True)
        if control.get("listing") == "silent":
            time.sleep(60)
        if control.get("listing") == "error":
            print("error: could not fetch the model list", file=sys.stderr)
            raise SystemExit(1)
        print("Fetching available models...")
        for slug, name in control.get("models", MODELS):
            print(f"{slug}\t{name}")
        return
    if values("-p") == ["/usage"]:
        log(usage=True)
        print(json.dumps({"conversation_id": "", "status": "SUCCESS", "response": "", "duration_seconds": 0, "num_turns": 0, "usage": EMPTY,
                          "command": {"name": "usage", "data": {"groups": control.get("usage", USAGE)}}}))
        return
    emit = lambda event, **body: print(json.dumps({"event": event, **body}), flush=True)
    if script == "signed_out":
        log(run=True)
        print("Error: authentication required. Run 'agy' to log in, then retry.", file=sys.stderr)
        emit("result", result={"conversation_id": "", "status": "ERROR", "response": "", "error": "authentication required", "num_turns": 0, "usage": EMPTY})
        raise SystemExit(1)
    definition = (Path(".agents") / "agents" / f"{values('--agent')[0]}.md").read_text()
    _, front, instruction = definition.split("---\n", 2)
    offered = next(line.split(":", 1)[1].strip().strip("[]").replace(" ", "").split(",") for line in front.splitlines() if line.startswith("tools:"))
    offered = [tool for tool in offered if tool]
    if "description: The Faculty assistant." in front:
        return assistant(control, front, instruction, offered, values, home, log, emit)
    prompt = "".join(part["text"] for part in json.loads(sys.stdin.readline())["message"]["content"])
    schema = json.loads(Path(values("--json-schema")[0]).read_text()) if values("--json-schema") else None
    # Every run keeps its conversation in the home: the transcript, and a copy of each picture viewed.
    conversation = str(uuid.uuid4())
    pictures = sorted(path.name for path in Path(".").glob("image-*.png"))
    for folder in ("conversations", f"brain/{conversation}/.system_generated/logs", f"brain/{conversation}/.tempmediaStorage", "annotations", "presence", "implicit", "log"):
        (home / folder).mkdir(parents=True, exist_ok=True)
    (home / "conversations" / f"{conversation}.db").write_text(prompt)
    (home / "brain" / conversation / ".system_generated" / "logs" / "transcript.jsonl").write_text(prompt)
    for name in pictures:
        (home / "brain" / conversation / ".tempmediaStorage" / name).write_bytes(Path(name).read_bytes())
    (home / "annotations" / f"{conversation}.pbtxt").write_text("x")
    (home / "presence" / f"{conversation}.lock").write_text("")
    (home / "implicit" / f"{uuid.uuid4()}.pb").write_text("x")
    (home / "conversation_summaries.db").write_text("x")
    parts = prompt.split("\n\n")
    bundled = "BUNDLED REQUEST" in instruction
    marker = lambda p: p.startswith("[image ")
    items = []
    if bundled:
        shared = next(json.loads(p.split("\n", 1)[1]) for p in parts if p.startswith("SHARED fields"))
        for p in parts:
            if p.startswith("ITEM "):
                items.append({"label": p.split(".", 1)[0].removeprefix("ITEM ").strip(), "payload": {**shared, **json.loads(p.split("Fields of this item:\n", 1)[1])}, "images": []})
            elif marker(p) and items:
                items[-1]["images"].append(p)
    else:
        text = next(p for p in parts if p.strip().startswith("{") or "\n{" in p)
        items.append({"label": "", "payload": json.loads(text[text.index("{"):]), "images": [p for p in parts if marker(p)]})
    log(run=True, model=values("--model")[0], effort=values("--effort"), agent=values("--agent")[0], offered=offered, instruction=instruction,
        schema=schema, pictures=pictures, note=parts[0] if pictures else "", bundled=bundled, items=len(items), markers=[len(i["images"]) for i in items],
        conversation=conversation, log_file=values("--log-file"))
    emit("init", conversation_id=conversation, init={"model": values("--model")[0], "cwd": os.getcwd(), "tools": ["run_command", "search_web", "view_file", "finish"],
                                                     "permission_mode": "request-review"})
    step = lambda index, kind, **more: emit("step_update", step_update={"conversation_id": conversation, "step_index": index, "state": "DONE", "step_type": kind, **more})
    step(0, "user_input")
    failures = {"limit": "RESOURCE_EXHAUSTED (code 429): You have exhausted your capacity on this model. Your quota will reset later.",
                "capacity": "UNAVAILABLE (code 503): The model is overloaded. Please try again later.", "error": "INTERNAL (code 500): stream closed"}
    if script in failures:
        emit("result", result={"conversation_id": conversation, "status": "ERROR", "response": "", "error": failures[script], "num_turns": 1, "usage": EMPTY})
        print(f"error: {failures[script]}", file=sys.stderr)
        raise SystemExit(3)
    index = 1
    if script == "denied":
        step(index, "tool", tool_name="run_command", tool_info={"name": "run_command", "parameters": {"CommandLine": "ls"}})
        emit("result", result={"conversation_id": conversation, "status": "SUCCESS", "response": "", "num_turns": 1, "usage": EMPTY,
                               "denied_actions": [{"action": "command", "display_name": "RunCommand"}]})
        return
    if script == "search":
        step(index, "tool", tool_name="search_web", tool_info={"name": "search_web", "parameters": {"query": "the answer"}})
        index += 1
    for name in pictures[:-1] if script == "skip_image" else pictures:
        if "view_file" not in offered:
            break
        step(index, "tool", tool_name="view_file", tool_info={"name": "view_file", "parameters": {"AbsolutePath": str(Path.cwd() / name)}})
        index += 1
    from fake_openai import default_answer
    answers = []
    for item in items:
        body = {"messages": [{"role": "system", "content": instruction.split("\n\nBUNDLED REQUEST")[0]},
                             {"role": "user", "content": [{"type": "text", "text": json.dumps(item["payload"])}, *[{"type": "image_url"} for _ in item["images"]]]}]}
        answer = default_answer(body)
        if isinstance(answer, tuple):
            answer = answer[1]
        if item["payload"].get("double") is not None:
            answer = {"sum": 2 * item["payload"]["double"], "digits_seen": str(len(item["images"]))}
        answers.append((item["label"], answer))
    output = {"results": [{"item": label, "answer": answer} for label, answer in answers]} if bundled else answers[0][1]
    usage = {"input_tokens": 2100, "output_tokens": 40 * len(items), "thinking_tokens": 10, "cache_read_tokens": 0, "total_tokens": 2110 + 40 * len(items)}
    result = {"conversation_id": conversation, "status": "SUCCESS", "duration_seconds": 1.5, "num_turns": 1, "usage": usage}
    if schema is None:
        result["response"] = output if isinstance(output, str) else json.dumps(output)
    elif "finish" in offered:
        # The answer goes through agy's finish tool, which adds two fields of its own to the text and none to the structured answer.
        result.update(response=json.dumps({**output, "toolAction": "Answering", "toolSummary": "Answer"}), structured_output=output, json_schema=schema)
        step(index, "finish")
    else:
        result["response"] = "I do not have access to a finish tool."
    emit("result", result=result)


def assistant(control, front, instruction, offered, values, home, log, emit):
    """The Assistant's conversation: one process, a turn per line of stdin."""
    field = lambda name: next((json.loads(line.split(":", 1)[1]) for line in front.splitlines() if line.strip().startswith(name + ":")), None)
    server = {"command": field("command"), "args": field("args"), "timeout": field("timeoutSeconds")} if "mcpServers:" in front else None
    conversation, turns, tokens = str(uuid.uuid4()), 0, 0
    for folder in ("conversations", f"brain/{conversation}/.system_generated/logs", "annotations", "presence", "implicit"):
        (home / folder).mkdir(parents=True, exist_ok=True)
    (home / "conversations" / f"{conversation}.db").write_text("kept by the program")
    (home / "annotations" / f"{conversation}.pbtxt").write_text("x")
    (home / "presence" / f"{conversation}.lock").write_text("")
    emit("init", conversation_id=conversation, init={"model": values("--model")[0], "cwd": os.getcwd(), "permission_mode": "request-review"})
    step = lambda index, kind, state="DONE", **more: emit("step_update", step_update={"conversation_id": conversation, "step_index": index, "state": state, "step_type": kind, **more})
    for line in sys.stdin:
        text = "".join(part["text"] for part in json.loads(line)["message"]["content"])
        turns += 1
        (home / "brain" / conversation / ".system_generated" / "logs" / "transcript.jsonl").write_text(text)
        pictures = sorted(str(path) for path in Path(".").glob("pictures/*"))
        tools = None
        if server and control.get("tool_calls") and turns == 1:
            from fake_mcp import call_tools
            tools = call_tools(server["command"], server["args"], control["tool_calls"])  # as the program starts it
        log(turn=turns, text=text, model=values("--model")[0], effort=values("--effort"), offered=offered, instruction=instruction, server=server,
            pictures=pictures, conversation=conversation, mcp=tools, others=sorted(path.name for path in Path("..").iterdir()))
        base = turns * 10
        step(base, "user_input")
        if "hang" in text:
            continue
        if "limit" in text:
            emit("result", result={"conversation_id": conversation, "status": "ERROR", "response": "", "error": "RESOURCE_EXHAUSTED (code 429): quota exceeded", "num_turns": turns})
            continue
        if "denied" in text:
            emit("result", result={"conversation_id": conversation, "status": "SUCCESS", "response": "", "num_turns": turns,
                                   "denied_actions": [{"action": "read_file", "display_name": "ViewFile"}]})
            continue
        for index, name in enumerate(pictures, 1):
            step(base + index, "tool", tool_name="view_file", tool_info={"name": "view_file", "parameters": {"AbsolutePath": str(Path.cwd() / name)}})
        step(base + 8, "agent_response")  # a step that only called a tool: no words
        step(base + 9, "agent_response", "ACTIVE", text_delta="Answer ")
        step(base + 9, "agent_response", text_delta=f"{turns}\n")
        tokens += 100
        emit("result", result={"conversation_id": conversation, "status": "SUCCESS", "response": f"Answer {turns}\n", "num_turns": turns,
                               "usage": {"input_tokens": 30 * tokens, "output_tokens": tokens // 2, "thinking_tokens": tokens // 10}})
