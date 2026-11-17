"""Several responses in one model request (requirement, 2026-09-27).

A connection's `bundle_size` (1-20; absent is 1, so connections and batches frozen before it are unchanged) says how many requests
may share one call. Requests share one only where the dispatcher named the same batch, question, exam version and step
(`CloudMixin.cloud_call`) AND their whole contract is the same: prompt, narrowed output schema (so the same rubric lines and crop
count) and whether it is a re-ask. The dispatcher's threads each still own their response's `frq_cloud_calls` row, written before
anything leaves; a bundle fills while its leader waits for a free call, and leaves when full, when no more arrive for `QUIET`
seconds, or after `GATHER`. The model is told the items are independent; fields every item shares are sent once. Each member gets
back only its own answer, validated by Faculty like any grader's; a successful member's receipt holds only its own answer. A failed
call fails each member under the ordinary retry policy (its receipt is the whole call's); a missing or malformed item is that
member's own refused answer, asked again like any other, and so is an answer whose content is about ANOTHER response of the bundle
(`matches`). An answer cut off at its output limit halves that bundle's size for a while.

A provider using this implements `_bundle_size`, `_bundle_parallel`, `_bundle_single(request)` (one request alone, as before) and
`_bundle_call(plan)` (one call for the plan; returns the parsed wrapper object and a context), plus `_bundle_receipt`.
"""
import json
import re
import threading
import time
import uuid

from ..models import digest
from ..schemas import SCHEMAS
from .base import Cancelled, ProviderError, current_abort

RULE = "bundles-v3"  # v2: shared fields' words and thousands separators no longer count; v3 (2026-09-27): only what students wrote counts.
QUIET, GATHER = 2.0, 12.0  # Seconds without a new member before a bundle leaves, and the longest a bundle waits to fill.
IMAGES, BYTES = 90, 18 * 1024**2  # Hosts take about 100 images and 32 MB per request; base64 adds a third.
SHRINK_SECONDS = 3600  # After a bundle's answer outgrew its output limit, bundles with that contract are halved for this long.
CROP_PROMPTS = ("reconcile", "reconcile_literal", "reconcile_context", "grade", "primary_vision", "second_vision", "adjudicate", "third_grade", "adversarial", "arbitrate")
CUT_OFF = ("length", "max_tokens", "MAX_TOKENS")

PREFACE = ("\n\nBUNDLED REQUEST. This message holds {count} INDEPENDENT items ({labels}), each one student's response to the SAME question. "
           "Do for EACH item exactly what the instructions above ask for one request, as if it were the only one: never let one item's work, "
           "reading or grade influence another's, never compare items, and never carry a value from one item to another. Each item's input "
           "is the SHARED fields merged with that item's own fields. An item's images follow its own text, and its crop IDs count from 0 "
           "within that item only. Return one entry in `results` per item, in any order, each naming its item and holding exactly the "
           "answer the instructions ask for.")


def crops(request):
    images = request.get("images") or []
    return (len(images) + 1) // 2 if request["prompt"] in ("reconcile", "reconcile_literal", "reconcile_context") else len(images)


def crop_note(name, count):
    if not count or name not in CROP_PROMPTS:
        return ""
    return (f"There are {count} original response crops. The ONLY valid crop IDs are {list(range(count))}. "
            + ("Enhanced image copies refer to the same original crop IDs. " if name in ("reconcile", "reconcile_literal", "reconcile_context") else "") + "For one original crop, every crop field is 0.")


def schema_for(request, count):
    from .cloud import grading_schema  # `cloud` imports the providers that use this module.
    name = request["prompt"]
    if request.get("schema"):
        return request["schema"]
    return grading_schema(name, request["payload"], count) if name in SCHEMAS else None


def split_usage(usage, share):
    """One member's part of a bundled call's usage: token counts and a reported cost divided evenly, the rest as reported."""
    if share <= 1 or not isinstance(usage, dict):
        return usage
    parts = {}
    for key, value in usage.items():
        if type(value) is int:
            parts[key] = value // share
        elif isinstance(value, float) and key in ("cost", "total_cost"):
            parts[key] = value / share
        else:
            parts[key] = value
    return {**parts, "bundle_size": share}


class _Bundle:
    def __init__(self, key, limit):
        self.key, self.limit, self.items, self.closed, self.done = key, limit, [], False, threading.Event()
        self.next_label = 1
        self.images = self.bytes = 0
        self.started = self.last = time.monotonic()
        self.answers, self.error, self.sent = {}, None, False

    def fits(self, item):
        return not self.closed and len(self.items) < self.limit and self.images + len(item["images"]) <= IMAGES \
            and self.bytes + sum(map(len, item["images"])) <= BYTES


class BundleMixin:
    _guard = threading.Condition()
    _open = {}          # bundle key -> the bundle still taking members
    _sessions = {}      # connection name -> (calls at once, semaphore)
    _shrunk = {}        # bundle key -> (monotonic end, size)

    def bundled(self, request):
        """Whether this request goes through a bundle: the dispatcher named its batch, question, version and step, the connection
        allows more than one response per call, and the request carries text of its own to check its answer against (`matches`).
        A request with none (the reader's free-text pass over one crop: an image and no text) always goes alone."""
        return bool(request.get("bundle")) and self._bundle_size() > 1 and bool(words(request.get("payload")))

    def _join(self, request):
        count = crops(request)
        schema = schema_for(request, count)
        e = self.endpoint
        key = digest({"endpoint": e["name"], "model": e["model"], "reasoning": e.get("reasoning_choice"), "prompt": request["prompt"],
                      "schema": schema, "reask": bool(request.get("reask")), "hint": request["bundle"]["key"]})
        item = {"request": request, "images": request.get("images") or [], "crops": count, "label": None}
        abort = current_abort()
        with self._guard:
            limit = self._bundle_size()
            shrunk = self._shrunk.get(key)
            if shrunk and shrunk[0] > time.monotonic():
                limit = min(limit, shrunk[1])
            bundle = self._open.get(key)
            leader = bundle is None or not bundle.fits(item)
            if leader:
                bundle = self._open[key] = _Bundle(key, limit)
            # GRADING "BUNDLED REQUESTS": a cancelled member may leave before the call is sent. Its label cannot be reused:
            # later members still carry their original labels, and every response must receive only its own answer.
            item["label"] = f"R{bundle.next_label}"
            bundle.next_label += 1
            bundle.items.append(item)
            bundle.images += len(item["images"])
            bundle.bytes += sum(map(len, item["images"]))
            bundle.last = time.monotonic()
            if len(bundle.items) >= bundle.limit and self._open.get(key) is bundle:
                del self._open[key]  # Full: the next arrival starts another.
            self._guard.notify_all()
        if leader:
            return self._lead(bundle, request["prompt"], schema, abort)
        return self._follow(bundle, item, abort)

    def _follow(self, bundle, item, abort):
        while not bundle.done.wait(1):
            if abort is not None and abort.stopped:
                with self._guard:
                    if not bundle.closed and item in bundle.items and bundle.items[0] is not item:
                        bundle.items.remove(item)  # Not sent yet: it leaves the bundle, and nothing of it is ever sent.
                        bundle.images -= len(item["images"])
                        bundle.bytes -= sum(map(len, item["images"]))
                abort.check()
        if bundle.error is not None:
            raise bundle.error
        found = bundle.answers.get(item["label"])
        if isinstance(found, Exception):
            raise found
        return found

    def _semaphore(self):
        parallel = max(1, int(self._bundle_parallel()))
        with self._guard:
            found = self._sessions.get(self.endpoint["name"])
            if not found or found[0] != parallel:
                found = self._sessions[self.endpoint["name"]] = (parallel, threading.BoundedSemaphore(parallel))
            return found[1]

    def _lead(self, bundle, name, schema, abort):
        session = self._semaphore()
        held = False
        try:
            # Gather while waiting for a free call: members keep arriving until the bundle is full, quiet or old enough.
            while True:
                if abort is not None:
                    abort.check()
                if not held:
                    held = session.acquire(timeout=.25)
                with self._guard:
                    moment = time.monotonic()
                    ready = len(bundle.items) >= bundle.limit or moment - bundle.last >= QUIET or moment - bundle.started >= GATHER
                    if held and ready:
                        bundle.closed = True
                        if self._open.get(bundle.key) is bundle:
                            del self._open[bundle.key]
                        items = list(bundle.items)
                        break
                    if held:
                        self._guard.wait(.25)
            bundle.sent = True
            try:
                answers = self._send(items, name, schema, bundle.key)
            except (ProviderError, Cancelled) as error:
                bundle.error = error
                raise
            except Exception as error:  # Anything else still settles every member, never leaves them waiting.
                bundle.error = ProviderError(f"The bundled request could not be completed ({type(error).__name__}).", confirmed=False)
                raise bundle.error from error
            bundle.answers = answers
            found = answers[items[0]["label"]]
            if isinstance(found, Exception):
                raise found
            return found
        finally:
            if held:
                session.release()
            with self._guard:
                bundle.closed = True
                if self._open.get(bundle.key) is bundle:
                    del self._open[bundle.key]
                if bundle.error is None and not bundle.answers and not bundle.sent:
                    bundle.error = Cancelled("It was cancelled before its bundle was sent.")
            bundle.done.set()

    def _send(self, items, name, schema, key):
        """ONE call for every item; each item's answer (or the reason it has none) by its label."""
        if len(items) == 1:
            return {items[0]["label"]: self._bundle_single(items[0]["request"])}
        labels = [i["label"] for i in items]
        payloads = [i["request"]["payload"] for i in items]
        # What every item's input shares (the question, its rubric, its key) is sent once; each item carries the rest.
        shared = {k: v for k, v in payloads[0].items() if all(isinstance(p, dict) and k in p and p[k] == v for p in payloads[1:])} if isinstance(payloads[0], dict) else {}
        answer = schema if schema is not None else {"type": "string"}
        plan = {"name": name, "labels": labels, "shared": shared, "schema": schema, "reask": items[0]["request"].get("reask"),
                "preface": PREFACE.format(count=len(items), labels=", ".join(labels)),
                "wrapper": {"type": "object", "additionalProperties": False, "required": ["results"], "properties": {"results": {
                    "type": "array", "minItems": len(items), "maxItems": len(items), "items": {"type": "object", "additionalProperties": False,
                    "required": ["item", "answer"], "properties": {"item": {"enum": labels}, "answer": answer}}}}},
                "items": [{"label": i["label"], "note": crop_note(name, i["crops"]), "images": i["images"],
                           "own": {k: v for k, v in p.items() if k not in shared} if isinstance(p, dict) else p} for i, p in zip(items, payloads)]}
        try:
            output, context = self._bundle_call(plan)
        except ProviderError as error:
            cut = error.confirmation.get("output_limit") or error.confirmation.get("finish_reason") in CUT_OFF
            if not cut:
                raise
            with self._guard:
                self._shrunk[key] = (time.monotonic() + SHRINK_SECONDS, max(1, len(items) // 2))
            # A bundle's answer that outgrew its budget is asked again in smaller bundles, never given up on as a single answer would be.
            raise ProviderError(f"The bundled answer for {len(items)} responses outgrew its output limit; they are asked again in smaller bundles.",
                                raw=error.raw, metadata=error.metadata, confirmed=True, status=error.status,
                                confirmation={**error.confirmation, "output_limit": True, "bundle": len(items)}) from None
        bundle_id = str(uuid.uuid4())
        entries = output.get("results") if isinstance(output, dict) else None
        found = {}
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("item") in labels:
                found.setdefault(entry["item"], []).append(entry.get("answer"))
        answers = {}
        sources, shared_words = [content_words(i["own"]) for i in plan["items"]], words(plan["shared"])
        for position, item in enumerate(items):
            share = {"id": bundle_id, "size": len(items), "item": item["label"], "position": position, "rule": RULE}
            given = found.get(item["label"]) or []
            if len(given) == 1:
                share["check"] = matches(given[0], sources, position, shared_words)
            raw, metadata = self._bundle_receipt(context, item["request"], given[0] if len(given) == 1 else given or None, share, schema)
            if len(given) != 1 or (schema is None and not isinstance(given[0], str)) or (schema is not None and not isinstance(given[0], dict)):
                answers[item["label"]] = ProviderError("The bundled answer had no single valid answer for this response. Nothing was accepted; it is asked again.",
                                                       raw=raw, metadata=metadata, confirmed=True, confirmation={"malformed": True, "bundle": share})
                continue
            if share["check"]["verdict"] == "other":
                # Filed under this response, yet about another one of the bundle: never applied to either. It is asked again.
                answers[item["label"]] = ProviderError("The bundled answer under this response's label matches another response of the bundle better than its own. "
                                                       "Nothing was accepted; it is asked again.", raw=raw, metadata=metadata, confirmed=True,
                                                       confirmation={"malformed": True, "bundle": share, "swapped": True})
                continue
            output_value = {"text": given[0].strip()} if schema is None else given[0]
            answers[item["label"]] = {"state": "completed", "result": {"output": output_value, "raw": raw, "metadata": metadata}}
        return answers


# Whose answer is it (requirement, 2026-09-27: build the check against answers filed under the wrong response). A label says only where
# the model PUT an answer, and a code beside the label would be copied with it; the answer's own CONTENT has to say whose it is. So
# each answer's words and numbers are compared with every item's own input (never the shared question, rubric or key), counting only
# what tells the items apart: words in some items' inputs but not in all. An answer that quotes, reads or reports what another item
# holds clearly more than what its own holds is refused as filed under the wrong response (`other`). Too little that tells them apart
# (`unknown`, e.g. two identical papers) proves nothing either way, and a swap between identical inputs changes nothing.
# Identifiers say nothing about a student's work, and a UUID read as words is random numbers and letter pairs that coincide with real
# ones (seen in the first test runs: a reading refused on four "telling" words, all from `response_id`). So identifier fields and
# identifier-shaped strings are left out, and too few telling words decide nothing.
MATCH_MIN, MATCH_MARGIN, MATCH_OWN = 5, .25, .5
TOKEN = re.compile(r"[a-z]{2,}|\d+(?:\.\d+)?")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
IDENTIFIER = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,}", re.IGNORECASE)


def identifier_key(key):
    key = str(key).casefold()
    return key == "id" or key.endswith(("_id", "_ids", "sha256", "_digest"))


def words(value):
    """The lower-case words (two letters or more) and numbers in every string of a value: keys, identifier fields and
    identifier-shaped strings excluded."""
    found = set()
    if isinstance(value, str):
        # "19,000" is 19000: a thousands separator is how a number is written, not another number.
        found.update(TOKEN.findall(THOUSANDS.sub("", IDENTIFIER.sub(" ", value)).casefold()))
    elif isinstance(value, dict):
        for key, item in value.items():
            if not identifier_key(key):
                found |= words(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= words(item)
    return found


# What a student WROTE, wherever a contract carries it: transcript lines and elements, final answers, quoted evidence. A grader's own
# explanation ("reason", "meaning", "description") and a reader's layout notes are not the student's work, and their ordinary words
# matched other students' transcripts by chance: measured on the second subscription trial batch (2026-09-27), a grade was refused
# on "from", "margin", "price", "text" and "top", all from the grader's remark "top margin text is from another question".
CONTENT_KEYS = frozenset({"text", "latex", "plain_text", "student_text", "quote"})


def content_words(value, inside=False):
    """The words and numbers of what the student wrote: strings under `CONTENT_KEYS` only (a bare string answer is all content)."""
    found = set()
    if isinstance(value, str):
        return words(value) if inside else found
    if isinstance(value, dict):
        for key, item in value.items():
            if not identifier_key(key):
                found |= content_words(item, inside or str(key) in CONTENT_KEYS)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= content_words(item, inside)
    return found


def matches(answer, sources, own, shared=frozenset()):
    """Which item's own input an answer is about: `own`, `other` (refused), or `unknown` (not enough to tell).

    `shared` is every word of the fields all items share (the problem, solution, rubric and key). A grader explains its grade with
    the correct values, and another student of the bundle may have written exactly those: measured on the first subscription trial
    batch (2026-09-27), both refusals were such answers ("42x", "19000"; "decreases", "168/11"), each about its own response. So a word
    the shared fields hold says nothing about whose answer it is."""
    common = (set.intersection(*sources) if sources else set()) | set(shared)
    telling = [source - common for source in sources]
    said = content_words(answer, inside=isinstance(answer, str)) & set().union(*telling)
    if len(said) < MATCH_MIN:
        return {"verdict": "unknown", "telling_words": len(said)}
    scores = [len(said & source) / len(said) for source in telling]
    best = max((j for j in range(len(scores)) if j != own), key=lambda j: scores[j], default=None)
    other = best is not None and scores[own] < MATCH_OWN and scores[best] - scores[own] >= MATCH_MARGIN
    return {"verdict": "other" if other else "own", "telling_words": len(said), "own_share": round(scores[own], 3),
            **({"closest_other": round(scores[best], 3)} if best is not None else {})}


def plan_payload(plan):
    """What a bundled request's receipt names as its payload: each item's own fields by digest, never another student's work."""
    return {"bundle": plan["labels"], "shared_sha256": digest(plan["shared"]), "items_sha256": [digest(i["own"]) for i in plan["items"]]}


def plan_texts(plan):
    """The bundled message's text parts, in order, with the index of the part each item's images follow."""
    parts = [("shared", "SHARED fields of every item:\n" + json.dumps(plan["shared"], ensure_ascii=False, allow_nan=False))]
    for item in plan["items"]:
        parts.append((item["label"], f"ITEM {item['label']}. {item['note']}\nFields of this item:\n" + json.dumps(item["own"], ensure_ascii=False, allow_nan=False)))
    return parts
