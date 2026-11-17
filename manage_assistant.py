#!/usr/bin/env python3
"""The Faculty assistant's tools as an MCP server over stdio (AGENTS.md "Faculty assistant").

    python3 manage_assistant.py mcp [--url http://127.0.0.1:3000]

A thin proxy: every tool is listed and run by the running Faculty web process (`/api/assistant`), where the assistant's rules
are applied, so this holds no logic of its own. Register it in Claude Code (`claude mcp add faculty -- .venv/bin/python
manage_assistant.py mcp`) or Claude desktop; the panel's claude, codex and gemini engines start it with their own session.
Changes outside the instructor's chosen categories become Approve cards on Faculty's Assistant page.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
INSTRUCTIONS = ("Faculty is the instructor's teaching workspace. Read assistant/guide.md with read_docs first. Reads are free; "
                "changes outside the instructor's chosen categories become Approve cards that are NOT done until the instructor "
                "approves them in Faculty.")


class Faculty:
    def __init__(self, url, session=None, token=None, engine="external"):
        self.url, self.session, self.token, self.engine = url.rstrip("/"), session, token, engine
        self.fixed = bool(session)

    def call(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"X-Teaching-Request": "1", "Content-Type": "application/json"}
        if token:
            headers["X-Assistant-Token"] = token
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"{}")
            except ValueError:
                return error.code, {"error": f"HTTP {error.code}"}

    def open(self):
        status, data = self.call("POST", "/api/assistant/sessions", {"engine": self.engine, "label": "MCP"})
        if status != 201:
            raise RuntimeError(data.get("error") or f"Faculty answered {status}")
        self.session, self.token = data["id"], data["token"]

    def tools(self):
        status, data = self.call("GET", "/api/assistant/tools")
        if status != 200:
            raise RuntimeError(data.get("error") or f"Faculty answered {status}")
        return data["tools"]

    def run(self, name, arguments):
        if not self.session:
            self.open()
        path = f"/api/assistant/sessions/{self.session}/tools/{name}"
        status, data = self.call("POST", path, {"arguments": arguments or {}}, self.token)
        if status == 410 and not self.fixed:  # Faculty restarted: sessions live in its memory
            self.open()
            status, data = self.call("POST", f"/api/assistant/sessions/{self.session}/tools/{name}", {"arguments": arguments or {}}, self.token)
        if status != 200:
            return data.get("error") or f"Faculty answered {status}", True
        return data["text"], not data.get("ok", True), data.get("images") or []


def serve(faculty, stdin=sys.stdin, stdout=sys.stdout):
    def send(message):
        stdout.write(json.dumps(message) + "\n")
        stdout.flush()

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if not isinstance(message, dict):
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}})
            continue
        method, ident = message.get("method"), message.get("id")
        params = message.get("params")
        if params is None:
            params = {}
        if ident is None:
            continue  # notifications (initialized, cancelled) need no answer
        if not isinstance(params, dict):
            send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": "Invalid params"}})
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "faculty", "version": "1"}, "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": faculty.tools()}
                if getattr(faculty, "engine", "") == "gemini":
                    # Antigravity gives its model only the NAMES of a server's tools, to be looked up in files, unless the server
                    # marks them always loaded (measured: unmarked, the model guessed their arguments). The same tools either way.
                    result["tools"] = [{**tool, "_meta": {**(tool.get("_meta") or {}), "antigravity/alwaysLoad": True}} for tool in result["tools"]]
            elif method == "tools/call":
                text, error, *more = faculty.run(params.get("name"), params.get("arguments"))
                # Pictures fetch_files sends for the model to look at (scanned pages, crops): MCP image content after the text.
                images = [{"type": "image", "data": item["data"], "mimeType": item["type"]} for item in (more[0] if more else [])
                          if isinstance(item, dict) and isinstance(item.get("data"), str) and str(item.get("type", "")).startswith("image/")]
                result = {"content": [{"type": "text", "text": text}, *images], "isError": error}
            else:
                send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"Unknown method {method}"}})
                continue
            send({"jsonrpc": "2.0", "id": ident, "result": result})
        except Exception as error:  # the client sees the reason; the server keeps running
            send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32603, "message": str(error)[:500]}})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    mcp = sub.add_parser("mcp", help="serve the assistant's tools over stdio")
    mcp.add_argument("--url", default=os.environ.get("FACULTY_URL") or f"http://127.0.0.1:{os.environ.get('PORT', '3000')}")
    mcp.add_argument("--session", default=os.environ.get("FACULTY_ASSISTANT_SESSION"))
    mcp.add_argument("--engine", default="external")
    mcp.add_argument("--token-file", help="a private file holding the session's token (the panel's engines use it)")
    args = parser.parse_args(argv)
    # The token never goes on the command line, where other users could read it: an environment variable or a private file.
    token = os.environ.get("FACULTY_ASSISTANT_TOKEN")
    if args.token_file:
        with open(args.token_file, encoding="utf-8") as handle:
            token = handle.read().strip()
    serve(Faculty(args.url, args.session, token, args.engine))


if __name__ == "__main__":
    main()
