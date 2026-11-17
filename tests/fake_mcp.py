"""What the fake Claude Code and Codex binaries do with an MCP server they are given (`fake_claude.py`, `fake_codex.py`): start it as
the real binaries do (its command and arguments, over stdio), list its tools and call the ones the test scripted, in order."""
import json
import subprocess


def call_tools(command, args, calls):
    """[(name, arguments)] → {"listed": [tool names], "results": [{"tool", "text", "error", "images"}]}"""
    process = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    asked = iter(range(1, 1000))

    def request(method, params=None):
        ident = next(asked)
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}}) + "\n")
        process.stdin.flush()
        answer = json.loads(process.stdout.readline())
        assert answer["id"] == ident, answer
        return answer

    try:
        request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake", "version": "1"}})
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        listed = [tool["name"] for tool in request("tools/list")["result"]["tools"]]
        results = []
        for name, arguments in calls:
            answer = request("tools/call", {"name": name, "arguments": arguments})
            if "error" in answer:
                results.append({"tool": name, "text": answer["error"]["message"], "error": True, "images": 0})
                continue
            content = answer["result"]["content"]
            results.append({"tool": name, "text": "".join(part["text"] for part in content if part["type"] == "text"),
                            "error": answer["result"]["isError"], "images": sum(part["type"] == "image" for part in content),
                            "mime": [part["mimeType"] for part in content if part["type"] == "image"]})
        return {"listed": listed, "results": results}
    finally:
        process.stdin.close()
        process.wait(timeout=10)
        process.stdout.close()
