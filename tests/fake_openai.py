"""A local fake OpenAI-compatible host, for tests and the scratch app. No credentials, no network.

It serves chat completions (with usage, an upstream `provider` name and optional cost), the models list, the
OpenAI-style batch API (files, batches, output file) and the asynchronous job API of a dedicated GPU provider
(/run, /status, /cancel, /health). A script decides what each request meets: an answer, an HTTP error with
Retry-After, a dropped connection or a stall past the client's timeout.

    python tests/fake_openai.py --port 3290      # then name http://127.0.0.1:3290/v1 as an endpoint's base URL
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import uuid


def prompt_name(body):
    name = ((body.get("response_format") or {}).get("json_schema") or {}).get("name")
    if name:
        return "flag_resolution" if name == "flag_resolution_adjustments" else name
    system = body["messages"][0]["content"].split("Output JSON schema")[0]
    if 'page_requests' in system:
        return 'reconcile_context'
    if 'literal_changes' in system:
        return 'reconcile_literal'
    if system.startswith('You inspect a blind image sample'):
        return 'quality_audit_blind'
    if system.startswith('You compare a blind image sample'):
        return 'quality_audit_sample'
    return ("rubric_survey" if "You list the problems printed on ONE blank" in system else
            "rubric_write_versions" if "on EVERY version of it at once" in system else "rubric_group_versions" if "You group the problems of the versions" in system else
            "rubric_write" if "You draft the grading rubric for ONE" in system else
            "rubric_adapt_match" if "You pair the problems of two versions" in system else "rubric_adapt" if "You adapt the instructor's grading rubric for ONE" in system else
            "read_context" if "You transcribe ADDITIONAL handwriting" in system else
            "read" if "Return the transcription as plain text" in system else
            "probe" if "capability check" in system else "flag_review" if "You review whether the reasons for flagging" in system else "arbitrate" if "FINAL ARBITRATOR" in system else "adjudicate" if "additional reviewer resolving" in system else "third_grade" if "independent third grader" in system else "adversarial" if "adversarial reviewer" in system else "primary_vision" if "PRIMARY GRADER" in system else "second_vision" if "ORIGINAL RESPONSE CROPS" in system else "transcript_grade" if "independent second grader" in system
            else "reconcile" if "visual_confidence" in system else "grade")


def payload_of(body):
    content = body["messages"][1]["content"]
    text = content if isinstance(content, str) else next(part["text"] for part in content if part["type"] == "text")
    return json.loads(text)


def images_of(body):
    content = body["messages"][1]["content"]
    return [] if isinstance(content, str) else [part for part in content if part["type"] == "image_url"]


def reconcile_answer(payload, confidence=.96):
    """Read back the first OCR candidate: a fake host has no eyes, but it keeps the contract."""
    transcript = payload["candidates"][0]["transcript"] if payload.get("candidates") else {"lines": [], "plain_text": "", "latex": "", "confidence": None, "ambiguities": []}
    return {"transcript": transcript, "visual_confidence": confidence}


def grade_answer(payload, chosen=None, *, image=True, consistent="yes", confidence=.97, answer_line=-1, attempts=1, full_credit_eligible=None):
    """Applies `chosen` rubric items (default: none) and quotes one transcript line (default: the last) as the final answer and evidence."""
    lines = payload["transcript"]["lines"]
    last = lines[answer_line] if lines else None
    written = (last["elements"][0].get("text") or last["elements"][0].get("latex")) if last and last["elements"] else ""
    evidence = [{"line_id": last["id"], "student_text": written, **({"bbox": None, "crop": 0} if image else {})}] if written else []
    chosen = set(chosen or ())
    return {"interpretation": {"meaning": "Scripted", "method": "Scripted", "work_consistent": consistent, "attempts": attempts, "error_graph": [],
                               "final_answers": [{"part": payload["answer_parts"][0], "text": written, "line_id": last["id"]}] if written else []},
        "applications": [{"rubric_item_id": item["id"], "applied": item["id"] in chosen, "reason": "Scripted", "evidence": evidence if item["id"] in chosen else [],
                          "origin_error_id": None, "confidence": confidence, "ambiguity": None} for item in payload["rubric"]["items"]],
        "full_credit_eligible": 'R1' in chosen if full_credit_eligible is None else full_credit_eligible,
        "confidence": confidence, "rubric_ambiguities": [], "review_reasons": [], "reading_issues": []}


def flag_review_answer(payload, decision="manual_review", resolve=False):
    return {"decision": decision, "reason": "Scripted flag review", "flag_rulings": [
        {"flag": f["id"], "resolved": resolve, "note": "Scripted evidence assessment"} for f in payload["flags"]]}


def arbitrate_answer(payload, decision="adopt", adopt=None, chosen=None, *, resolve=True, reason="Scripted ruling"):
    """The final arbitrator's ruling: adopts the standing assessment and resolves every flag unless told otherwise."""
    own = grade_answer(payload, chosen if decision == "revise" else None)
    return {**own, "applications": own["applications"] if decision == "revise" else [], "decision": decision,
            "adopt": (adopt or payload["standing"]) if decision == "adopt" else "", "reason": reason,
            "flag_rulings": [{"flag": flag["id"], "resolved": resolve if isinstance(resolve, bool) else flag["id"] in resolve, "note": "Checked against the transcript."} for flag in payload["flags"]]}


def flag_look_answer(payload, spots=None):
    """The spot a flag review's note asks the instructor to look at (`grading/flag_look.py`): one box on the first crop unless told."""
    return {"spots": [{"label": "the digit on the answer line", "crop": 0, "bbox": [.2, .3, .3, .4]}] if spots is None else spots}


def flag_resolution_answer(payload, decision="adopt", adopt=None, chosen=None, *, resolve=True, clarifications=(), changes=(), needs_page=False):
    """The flag review's rulings (prompt v2's words: uphold, retick, instructor): every response upholds its standing assessment and
    resolves every flag unless told otherwise. `decision` takes the stored words too (adopt, revise, unreliable). `decision`, `adopt`,
    `chosen`, `resolve`, `changes` (transcript changes) and `needs_page` may be callables of the response. A revised grade quotes the last
    line of the transcript as the ruling changed it."""
    pick = lambda value, r: value(r) if callable(value) else value
    words = {"adopt": "uphold", "revise": "retick", "unreliable": "instructor"}
    rulings = []
    for r in payload["responses"]:
        d = words.get(pick(decision, r), pick(decision, r))
        changed = list(pick(changes, r) or ())
        lines = [dict(line) for line in r["transcript"]["lines"]]
        for c in changed:
            ids = [line["id"] for line in lines]
            line = {"id": c["line_id"], "elements": c["elements"]}
            if c["line_id"] in ids:
                lines[ids.index(c["line_id"])] = line
            else:
                lines.insert(ids.index(c["after"]) + 1 if c.get("after") else 0, line)
        if changed:  # the quoted line is the last one changed, as a model quoting its own correction would
            last = next(line for line in lines if line["id"] == changed[-1]["line_id"])
            lines = [line for line in lines if line is not last] + [last]
        own = grade_answer({"transcript": {**r["transcript"], "lines": lines}, "rubric": payload["rubric"], "answer_parts": payload["answer_parts"]},
                           pick(chosen, r) if d == "retick" else None)
        settle = pick(resolve, r)
        rulings.append({"ref": r["ref"], **own, "applications": own["applications"] if d == "retick" else [], "decision": d,
                        "uphold": (pick(adopt, r) or r["standing"] or sorted(r["assessments"])[0]) if d == "uphold" else "", "reason": "Scripted flag review",
                        "needs_page": bool(pick(needs_page, r)), "transcript_changes": changed,
                        "flag_rulings": [{"flag": f["id"], "resolved": settle, "note": "Checked against the crop."} for f in r["flags"]]})
        if "deduction_rules" in payload:
            from grading.scoring import score
            rubric_score = (r["assessments"][rulings[-1]["uphold"]]["score"]["score"] if d == "uphold" else
                            score(payload["rubric"], own["applications"], payload["question"]["points"])["score"] if d == "retick" else None)
            rulings[-1].update(expected_score=rubric_score, deductions=[
                {"id": rule["id"], "applied": False, "reason": "The scripted case does not meet this condition."} for rule in payload["deduction_rules"]])
    return {"rulings": rulings, "clarifications": [{"line_id": line, "clarification": text, "why": "R1"} for line, text in clarifications], "summary": "Scripted."}


def adversarial_answer(objections=()):
    return {"objections": [{"rubric_item_id": item, "claim": claim, "evidence": "Scripted", "line_ids": [], "severity": "material"} for item, claim in objections],
            "no_material_contradiction": not objections, "transcript_disagreements": [], "confidence": .9}


# The free-text pass of `read`: plain text, no JSON, one written line per row. The same work the OCR fixtures read, so a
# direct-reading batch and an OCR batch differ only in where the reading came from.
DIRECT_READING = "3x - 8 = 10\n3x = 10 + 8\n3x = 18\nx = 6"


def survey_answer(numbers=("1",), points=10):
    """A blank assignment's printed problems. A fake host has no eyes: one problem per number, each on page 1."""
    return {"problems": [{"number": n, "label": f"{n}.", "points": points, "points_text": f"({points} points)", "pages": [1], "parts": [],
                          "statement": "Solve $$3x - 8 = 10$$ for $$x$$."} for n in numbers], "notes": []}


ZERO_LINE = "Apply only when no other rubric item applies to the response."


def write_answer(payload):
    """A drafted rubric in the department's style: the full-credit line, steps that add up to the total, and the no-credit line (in
    positive scoring only; a deducting draft has the full-credit line and deductions that add up to the total), each with its
    clarification for the graders where it needs one."""
    total = payload["problem"]["points"] or 10
    if payload.get('mode') == 'negative':
        return {"solution": "3x - 8 = 10, so 3x = 18 and x = 6.", "final_answers": [{"part": "", "answer": "x = 6"}], "points": total,
                "items": [{"description": "Correct answer with supporting work: $$x = 6$$", "points": 0, "clarification": ""},
                          {"description": "Adds 8 to just one side of $$3x - 8 = 10$$, or the step is missing", "points": -total / 2,
                           "clarification": "A blank response takes this deduction too."},
                          {"description": "Makes a new arithmetic error dividing the preceding equation by 3, or the step is missing", "points": -total / 2,
                           "clarification": ""}], "notes": ""}
    return {"solution": "3x - 8 = 10, so 3x = 18 and x = 6.", "final_answers": [{"part": "", "answer": "x = 6"}], "points": total,
            "items": [{"description": "Correct answer with supporting work: $$x = 6$$", "points": total, "clarification": ""},
                      {"description": "Adds 8 to both sides: $$3x = 18$$", "points": total / 2, "clarification": "Any equivalent of 3x = 18 counts."},
                      {"description": "Divides by 3: $$x = 6$$", "points": total / 2, "clarification": ""},
                      {"description": "No answer or completely incorrect", "points": 0, "clarification": ZERO_LINE}], "notes": ""}


def match_answer(payload, pairs=None):
    """Which reference problem each problem of the other version is a version of: by default the one in the same position."""
    references = [p["number"] for p in payload["reference"]["problems"]]
    pairs = pairs or {p["number"]: (references[i] if i < len(references) else None) for i, p in enumerate(payload["target"]["problems"])}
    return {"pairs": [{"target": p["number"], "reference": pairs.get(p["number"]), "reason": "Same task on other values."} for p in payload["target"]["problems"]],
            "notes": []}


def adapt_answer(payload, drop=()):
    """Every reference line rewritten for the other version by its ID (a fake host changes x to y), the lines in `drop` left out."""
    return {"solution": "3y - 8 = 13, so 3y = 21 and y = 7.", "final_answers": [{"part": "", "answer": "y = 7"}],
            "items": [{"id": line["id"], "description": line["description"].replace("x_", "y_")} for line in payload["reference"]["rubric"]["items"] if line["id"] not in drop],
            "notes": ""}


def group_answer(payload):
    """The versions' problems grouped by position: each version's i-th problem is the same task."""
    versions = payload["versions"]
    size = max(len(v["problems"]) for v in versions)
    return {"groups": [{"members": [{"version": v["version"], "number": v["problems"][i]["number"]} for v in versions if i < len(v["problems"])]}
                       for i in range(size)], "notes": []}


def write_versions_answer(payload):
    """One problem's rubric for all its versions: the same lines and points, each version's own answer (x = 6 on the first, 7 on the
    second ...)."""
    members = payload["problem"]["versions"]
    total = next((m["points"] for m in members if m["points"] is not None), 10)
    answer = {m["version"]: f"x = {6 + i}" for i, m in enumerate(members)}
    negative = payload.get("mode") == "negative"
    lines = ([("Correct answer with supporting work: $$%s$$", 0, ""), ("Adds 8 to just one side (%s), or the step is missing", -total / 2, ""),
              ("Divides wrongly or not at all (%s)", -total / 2, "")] if negative else
             [("Correct answer with supporting work: $$%s$$", total, ""), ("Adds 8 to both sides, then divides: $$%s$$", total / 2, "Any equivalent form of %s counts."),
              ("Checks the answer $$%s$$ in the equation", total / 2, ""), ("No answer or completely incorrect", 0, ZERO_LINE)])
    fill = lambda wording, m: wording % answer[m["version"]] if "%s" in wording else wording
    return {"solutions": [{"version": m["version"], "solution": f"Solved: {answer[m['version']]}.", "final_answers": [{"part": "", "answer": answer[m["version"]]}]} for m in members],
            # LINKED VERSIONS (2026-10-02): the check of the answer is "related" in this fake, every other line the same on each version.
            "points": total, "items": [{"points": points, "link": "related" if wording.startswith("Checks") else "same",
                                        "descriptions": [{"version": m["version"], "description": fill(wording, m), "clarification": fill(note, m)}
                                                         for m in members]} for wording, points, note in lines], "notes": ""}


def bundle_items(body):
    """A bundled request's items (`grading/providers/bundles.py`): each one's label and the single-request body it stands for."""
    system = body["messages"][0]["content"]
    parts, items, shared = body["messages"][1]["content"], [], {}
    for part in parts:
        if part["type"] == "text" and part["text"].startswith("SHARED fields"):
            shared = json.loads(part["text"].split("\n", 1)[1])
        elif part["type"] == "text":
            own = json.loads(part["text"].split("Fields of this item:\n", 1)[1])
            items.append({"label": part["text"].split(".", 1)[0].removeprefix("ITEM ").strip(),
                          "content": [{"type": "text", "text": json.dumps({**shared, **own})}]})
        else:
            items[-1]["content"].append(part)
    single = system.split("\n\nBUNDLED REQUEST")[0]
    return [(item["label"], {**body, "messages": [{"role": "system", "content": single}, {"role": "user", "content": item["content"]}]}) for item in items]


def bundled_answer(body, answer=None):
    """Every item answered as the same request alone would be (`answer`, default `default_answer`), in the bundle's wrapper."""
    results = []
    for label, single in bundle_items(body):
        value = (answer or default_answer)(single)
        results.append({"item": label, "answer": value[1] if isinstance(value, tuple) and value[0] == "raw" else value})
    return {"results": results}


def default_answer(body):
    if "BUNDLED REQUEST" in str(body["messages"][0]["content"]):
        return bundled_answer(body)
    name, payload = prompt_name(body), payload_of(body)
    if name == "rubric_survey":
        return survey_answer()
    if name == "rubric_write":
        return write_answer(payload)
    if name == "rubric_group_versions":
        return group_answer(payload)
    if name == "rubric_write_versions":
        return write_versions_answer(payload)
    if name == "rubric_adapt_match":
        return match_answer(payload)
    if name == "rubric_adapt":
        return adapt_answer(payload)
    if name == "read":
        return ("raw", DIRECT_READING, "stop")
    if name == "read_context":
        return ("raw", "[no additional work]", "stop")
    if name == "probe":
        return {"sum": 42, "digits_seen": "47" if images_of(body) else ""}
    if name == "adversarial":
        return adversarial_answer()
    if name == "flag_review":
        return flag_review_answer(payload)
    if name == "arbitrate":
        return arbitrate_answer(payload)
    if name == "flag_resolution":
        return flag_resolution_answer(payload)
    if name == "flag_look":
        return flag_look_answer(payload)
    if name == "quality_audit_blind":
        return {"readings": [{"ref": r["ref"], "work": "Scripted", "lines": [], "reason": "Scripted", "uncertain": False}
                             for r in payload["images_per_ref"]]}
    if name in ("quality_audit", "quality_audit_sample"):
        return {"summary": "Consistent.", "findings": []}
    if name == "quality_audit_confirm":
        return {"verdict": "confirmed", "explanation": "Seen in the scans.", "suggestions": []}
    if name in ("reconcile", "reconcile_literal", "reconcile_context"):
        return {**reconcile_answer(payload), **({"page_requests": []} if name == "reconcile_context" else {})}
    return grade_answer(payload, image=name in ("grade", "primary_vision", "second_vision", "adjudicate", "third_grade"))


class FakeHost:
    """script(body, host) returns an answer dict, or ("status", code, headers, body), ("drop",), ("stall", seconds) or ("raw", text, finish_reason).

    A request that asks for a stream is answered with server-sent chunks, as the hosts do; ("cut",) then ends the stream halfway
    without its finish, ("stream_error", code) sends an error chunk after the answer has begun, and ("thinking", seconds) streams
    reasoning for that long before the answer (a third item is that answer), noting in `hung_up` how long it had streamed when the client closed the connection."""
    def __init__(self, script=None, port=0, upstream="FakeUpstream", batch_polls=1):
        self.script, self.upstream, self.batch_polls = script or (lambda body, host: default_answer(body)), upstream, batch_polls
        self.requests, self.lock, self.active, self.peak = [], threading.Lock(), 0, 0
        self.files, self.batches, self.jobs, self.cancelled, self.hung_up = {}, {}, {}, [], []
        host = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def reply(self, code, value, headers=None, raw=None):
                content = raw if raw is not None else json.dumps(value).encode()
                self.send_response(code)
                for key, item in {"Content-Type": "application/json", "Content-Length": str(len(content)), **(headers or {})}.items():
                    self.send_header(key, item)
                try:
                    self.end_headers()
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The client gave up first (a scripted stall); that is the point of such a test.

            def completion(self, body):
                """One chat completion under the script. Returns (code, value, headers) or None when the connection was dropped."""
                with host.lock:
                    host.active += 1
                    host.peak = max(host.peak, host.active)
                    host.requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization"), "at": time.monotonic()})
                try:
                    outcome = host.script(body, host)
                    if isinstance(outcome, tuple) and outcome[0] == "stall":
                        time.sleep(outcome[1])
                        outcome = default_answer(body)
                    if isinstance(outcome, tuple) and outcome[0] == "drop":
                        self.connection.close()
                        return None
                    if isinstance(outcome, tuple) and outcome[0] == "status":
                        return outcome[1], outcome[3] if len(outcome) > 3 else {"error": {"message": "scripted failure"}}, outcome[2] if len(outcome) > 2 else {}
                    if isinstance(outcome, tuple) and outcome[0] in ("cut", "stream_error", "thinking"):
                        answer = self.answer(body, outcome[2] if outcome[0] == "thinking" and len(outcome) > 2 else default_answer(body), "stop")
                        return 200, answer, {"stream": outcome}
                    text, finish = (outcome[1], outcome[2]) if isinstance(outcome, tuple) and outcome[0] == "raw" else (json.dumps(outcome), "stop")
                    return 200, self.answer(body, text, finish), {}
                finally:
                    with host.lock:
                        host.active -= 1

            def answer(self, body, text, finish):
                text = text if isinstance(text, str) else json.dumps(text)
                images = len(images_of(body))
                usage = {"prompt_tokens": 400 + 900 * images, "completion_tokens": max(1, len(text) // 4), "total_tokens": 400 + 900 * images + max(1, len(text) // 4)}
                return {"id": "gen-" + uuid.uuid4().hex[:12], "model": body["model"], "provider": host.upstream, "choices": [{"finish_reason": finish,
                    "message": {"role": "assistant", "content": text}}], "usage": usage}

            def stream(self, answer, outcome=None):
                """The answer as the chunks a streaming host sends: role, the text in pieces, the finish, the usage, [DONE]."""
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                text, base = answer["choices"][0]["message"]["content"], {k: answer[k] for k in ("id", "model", "provider")}
                pieces = [text[i:i + max(1, len(text) // 3 + 1)] for i in range(0, len(text), max(1, len(text) // 3 + 1))] or [""]
                chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}]
                chunks += [{**base, "choices": [{"index": 0, "delta": {"content": piece}}]} for piece in pieces]
                if outcome and outcome[0] == "cut":
                    chunks = chunks[:2]
                elif outcome and outcome[0] == "stream_error":
                    chunks = chunks[:2] + [{**base, "error": {"code": outcome[1], "message": "scripted stream error"}, "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}]}]
                else:
                    chunks += [{**base, "choices": [{"index": 0, "delta": {}, "finish_reason": answer["choices"][0]["finish_reason"]}]},
                               {**base, "choices": [], "usage": answer["usage"]}]
                try:
                    self.wfile.write(b": keep-alive\n\n")
                    if outcome and outcome[0] == "thinking":
                        began = time.monotonic()
                        self.wfile.write(b"data: " + json.dumps(chunks[0]).encode() + b"\n\n")
                        while time.monotonic() - began < outcome[1]:
                            try:
                                self.wfile.write(b"data: " + json.dumps({**base, "choices": [{"index": 0, "delta": {"reasoning": "Still weighing the rubric. "}}]}).encode() + b"\n\n")
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                with host.lock:
                                    host.hung_up.append(time.monotonic() - began)
                                self.close_connection = True
                                return
                            time.sleep(.05)
                        chunks, outcome = chunks[1:], None  # Then the answer as usual: its text, finish and usage follow.
                    for chunk in chunks:
                        self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                    if not outcome:
                        self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

            def do_GET(self):
                if self.path.endswith("/models"):
                    return self.reply(200, {"data": [{"id": name} for name in host.models()]})
                if "/batches/" in self.path:
                    batch = host.batches.get(self.path.rsplit("/", 1)[1])
                    if not batch:
                        return self.reply(404, {"error": {"message": "no such batch"}})
                    batch["polls"] += 1
                    if batch["status"] == "in_progress" and batch["polls"] >= host.batch_polls:
                        batch.update(status="completed", output_file_id=batch["output"])
                    return self.reply(200, {k: v for k, v in batch.items() if k not in ("polls", "output")})
                if "/files/" in self.path and self.path.endswith("/content"):
                    return self.reply(200, None, {"Content-Type": "application/jsonl"}, raw=host.files[self.path.split("/")[-2]])
                if "/status/" in self.path:
                    job = host.jobs.get(self.path.rsplit("/", 1)[1])
                    return self.reply(200, job) if job else self.reply(404, {"error": "no such job"})
                if self.path.endswith("/health"):
                    return self.reply(200, {"workers": {"idle": 0, "running": 0}, "jobs": {"inQueue": 0}})
                self.reply(404, {"error": {"message": "not found"}})

            def do_POST(self):
                content = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path.endswith("/chat/completions"):
                    body = json.loads(content)
                    found = self.completion(body)
                    if found is not None and found[0] == 200 and body.get("stream"):
                        return self.stream(found[1], found[2].get("stream"))
                    return None if found is None else self.reply(found[0], found[1], {k: v for k, v in found[2].items() if k != "stream"})
                if self.path.endswith("/files"):
                    identifier = "file-" + uuid.uuid4().hex[:10]
                    host.files[identifier] = content[content.index(b"\r\n\r\n{") + 4:content.rindex(b"\r\n--")]
                    return self.reply(200, {"id": identifier})
                if self.path.endswith("/batches"):
                    request = json.loads(content)
                    line = json.loads(host.files[request["input_file_id"]].decode().splitlines()[0])
                    found = self.completion(line["body"])
                    if found is None:
                        return None
                    identifier, output = "batch-" + uuid.uuid4().hex[:10], "file-" + uuid.uuid4().hex[:10]
                    host.files[output] = (json.dumps({"custom_id": line["custom_id"], "response": {"status_code": found[0], "body": found[1]},
                                                      "error": None if found[0] < 400 else found[1]}) + "\n").encode()
                    host.batches[identifier] = {"id": identifier, "status": "in_progress", "polls": 0, "output": output}
                    return self.reply(200, {"id": identifier, "status": "validating"})
                if "/batches/" in self.path and self.path.endswith("/cancel"):
                    identifier = self.path.split("/")[-2]
                    host.batches[identifier]["status"] = "cancelled"
                    host.cancelled.append(identifier)
                    return self.reply(200, {"id": identifier, "status": "cancelled"})
                if self.path.endswith("/run"):
                    found = self.completion(json.loads(content)["input"]["openai_input"])
                    if found is None:
                        return None
                    if found[0] >= 400:
                        return self.reply(found[0], found[1], found[2])
                    identifier = "job-" + uuid.uuid4().hex[:10]
                    host.jobs[identifier] = {"id": identifier, "status": "COMPLETED", "output": found[1], "executionTime": 1200, "delayTime": 300}
                    return self.reply(200, {"id": identifier, "status": "IN_QUEUE"})
                if "/cancel/" in self.path:
                    identifier = self.path.rsplit("/", 1)[1]
                    host.jobs.setdefault(identifier, {"id": identifier})["status"] = "CANCELLED"
                    host.cancelled.append(identifier)
                    return self.reply(200, {"id": identifier, "status": "CANCELLED"})
                self.reply(404, {"error": {"message": "not found"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def models(self):
        return sorted({r["body"]["model"] for r in self.requests} | set(getattr(self, "served", ())))

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3290)
    args = parser.parse_args()
    fake = FakeHost(port=args.port).start()
    print(f"Fake OpenAI-compatible host at {fake.url} (scripted answers only; nothing leaves this machine).", flush=True)
    try:
        fake.thread.join()
    except KeyboardInterrupt:
        fake.stop()
