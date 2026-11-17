"""Provider-independent cloud grading: hosted per-token endpoints first, a dedicated GPU provider second.

Faculty owns students and grades. Cloud workers are a disposable execution layer: a provider takes one
request (a prompt contract, a payload and images), and Faculty's durable call records own submission,
recovery, retries and idempotency. Grading code speaks in tiers and roles, never in vendors or models.

    submit(request)  -> {"state": "completed", "result": ...} or {"state": "submitted", "call_id": ...}
    poll(call_id)    -> {"state": "submitted" | "completed" | "failed" | "cancelled" | "expired", ...}
    cancel(call_id), health(), capabilities()
"""
import base64
import copy
import json
import os
import time
import uuid

from ..config import EFFORTS, reasoning_default, catalog_provider, key_problem
from ..models import digest
from ..schemas import SCHEMAS
from . import bundles
from .base import ProviderError, request_json, request_stream
from .bundles import BundleMixin
from .local_vlm import prompt

LOOPBACK = ("localhost", "127.0.0.1", "::1")
REASK_TEMPERATURE = .3  # Only for an answer that was already refused once; every first request is deterministic.
HINT_WITHHELD_SECONDS = 900  # After a host refused the advisory hint for a model: long enough to spare a batch the refusals, short enough to notice a change.
HINT_NOTES = {"withhold": "withheld: the router found no host of this model that accepts it",
              "think": "refused: reasoning is mandatory for this model, so it was given its reasoning budget"}
TEMPERATURE_NOTE = "withheld: the router found no host of this model that accepts a temperature; the model samples as its host decides"
ROOM_NOTE = "answer limit withheld: the gateway refused its published maximum for this model; the host's default applies"
BOUNDS_NOTE = "array length bounds withheld: the host refused the schema as too complex; the backend still requires one decision per rubric line"
EMPTY_ENUM_NOTE = "empty enum members withheld: this host refuses \"\" as an enum member; the backend still refuses an unknown adopted label or flag"
GONE = (404, 410)  # Asked about a durable job, the host says it does not hold it: purged after its retention, or never known.


def ask_about_job(fetch):
    """What the host says about one of its durable jobs, as `poll` reads it.

    A job the host no longer holds cannot be collected by asking again, and a refused key (401/403, already `fixable=False`
    from `request_json`) is refused again: both are CONFIRMED failures of the poll, and `fixable=False` tells the dispatcher to
    settle the call rather than wait on it. A timeout, an outage or a rate limit is left as it was (unknown, or confirmed and
    fixable), since asking later can still collect the job. What the JOB did (it may have run and been billed) stays unknown.
    """
    try:
        return fetch()
    except ProviderError as error:
        if error.status in GONE:
            raise ProviderError(f"The host no longer holds this job (HTTP {error.status}); its outcome cannot be collected.", confirmed=True,
                                status=error.status, fixable=False, confirmation={**error.confirmation, "job_gone": True}) from None
        raise


def grading_schema(name, payload, crop_count):
    """The output contract of one prompt, narrowed to this request's rubric items and crops. Backend validation stays mandatory."""
    schema = copy.deepcopy(SCHEMAS[name])
    if name == 'rubric_write':
        negative = payload.get('mode', 'positive') == 'negative'
        points = schema['properties']['items']['items']['properties']['points']
        points['maximum' if negative else 'minimum'] = 0
        # No printed total (or no problem at all): the sign bound alone; `written_rubric` still checks each line against the total.
        total = (payload.get('problem') or {}).get('points')
        if total is not None:
            points['minimum' if negative else 'maximum'] = -total if negative else total
    if name == 'rubric_group_versions' and payload.get('versions'):
        schema['properties']['groups']['items']['properties']['members']['items']['properties']['version'] = {'enum': [v['version'] for v in payload['versions']]}
    if name in ('quiz_write', 'makeup_write') and (payload.get('request') or {}).get('versions'):
        # Every problem gives each requested version exactly once; `quiz_latex.clean` still refuses anything else.
        letters = payload['request']['versions']
        versions = schema['properties']['problems']['items']['properties']['versions']
        versions['items']['properties']['version'] = {'enum': letters}
        versions.update(minItems=len(letters), maxItems=len(letters))
        schema['properties']['edits']['items']['properties']['content']['anyOf'][0]['properties']['version'] = {'enum': letters}
    if name == 'quiz_check' and (payload.get('quiz') or {}).get('versions'):
        schema['properties']['issues']['items']['properties']['version'] = {'enum': [*payload['quiz']['versions'], 'all']}
    if name in ('rubric_write_versions', 'quiz_rubric') and (payload.get('problem') or {}).get('versions'):
        names = [v['version'] for v in payload['problem']['versions']]
        schema['properties']['solutions']['items']['properties']['version'] = {'enum': names}
        schema['properties']['solutions'].update(minItems=len(names), maxItems=len(names))
        descriptions = schema['properties']['items']['items']['properties']['descriptions']
        descriptions['items']['properties']['version'] = {'enum': names}
        descriptions.update(minItems=len(names), maxItems=len(names))
        negative = payload.get('mode', 'positive') == 'negative'
        points = schema['properties']['items']['items']['properties']['points']
        points['maximum' if negative else 'minimum'] = 0
        printed = {v['points'] for v in payload['problem']['versions'] if v.get('points') is not None}
        if len(printed) == 1:
            total = next(iter(printed))
            points['minimum' if negative else 'maximum'] = -total if negative else total
    if name == 'rubric_adapt' and (payload.get('reference') or {}).get('rubric'):
        # Every reference line once, by its ID; the backend (`adapted_rubric`) still refuses a missing, repeated or unknown line.
        ids = [item['id'] for item in payload['reference']['rubric']['items']]
        schema['properties']['items'].update(minItems=len(ids), maxItems=len(ids))
        schema['properties']['items']['items']['properties']['id'] = {'enum': ids}
    if name == 'rubric_adapt_match' and payload.get('target') and payload.get('reference'):
        pairs = schema['properties']['pairs']
        targets = [p['number'] for p in payload['target']['problems']]
        pairs.update(minItems=len(targets), maxItems=len(targets))
        pairs['items']['properties']['target'] = {'enum': targets}
        pairs['items']['properties']['reference'] = {'anyOf': [{'enum': [p['number'] for p in payload['reference']['problems']]}, {'type': 'null'}]}
    # The valid crop IDs as a RANGE, never an integer enum. Measured live 2026-09-21: Google's structured output takes enums of
    # strings only, and given `{"enum": [0]}` it silently dropped every field of the object holding it. As the additional reviewer
    # google/gemini-3.8-flash returned `"evidence": [{}, {}]` for all 169 quotations of a batch (every other nested object filled),
    # and each response it touched was flagged for quotations it never got to write. Seven test requests pinned it: removing `bbox`
    # changed nothing, a typed integer enum failed the same way, and this range returned real quotations. Backend validation
    # (`recognition.validate_grade`) still refuses a crop that does not exist.
    crops = {"type": "integer", "minimum": 0, "maximum": crop_count - 1} if crop_count else None
    if name in ("reconcile", "reconcile_literal", "reconcile_context") and crops:
        schema["properties"]["transcript"]["properties"]["lines"]["items"]["properties"]["elements"]["items"]["properties"]["crop"] = crops
        if name == "reconcile_context":
            schema["properties"]["page_requests"]["maxItems"] = crop_count
            schema["properties"]["page_requests"]["items"]["properties"]["crop"] = crops
    if name in ("grade", "primary_vision", "transcript_grade", "second_vision", "adjudicate", "third_grade", "arbitrate"):
        item_ids = [item["id"] for item in payload["rubric"]["items"]]
        applications = schema["properties"]["applications"]
        # The final arbitrator returns its own ticks only when it revises: none, or one decision per rubric line.
        applications.update(maxItems=len(item_ids)) if name == "arbitrate" else applications.update(minItems=len(item_ids), maxItems=len(item_ids))
        applications["items"]["properties"]["rubric_item_id"] = {"enum": item_ids}
        if crops:
            applications["items"]["properties"]["evidence"]["items"]["properties"]["crop"] = crops
    if name in ("grade", "primary_vision", "transcript_grade", "second_vision") and payload.get("deductions"):
        # GRADING "GRADERS TAKE OFF APPROVED DEDUCTIONS": one decision for each offered deduction, by its id. A request without
        # them keeps the contract it always had; `grader_deductions.decide` still refuses a missing, repeated or unknown one.
        ids = [d["id"] for d in payload["deductions"]]
        schema["properties"]["deductions"] = {"type": "array", "minItems": len(ids), "maxItems": len(ids), "items": {
            "type": "object", "properties": {"id": {"enum": ids}, "applied": {"type": "boolean"}, "reason": {"type": "string"}},
            "required": ["id", "applied", "reason"], "additionalProperties": False}}
        schema["required"] = [*schema["required"], "deductions"]
    if name == "arbitrate":
        schema["properties"]["adopt"] = {"enum": ["", *payload.get("assessments", {})]}
    if name in ("arbitrate", "flag_review"):
        schema["properties"]["flag_rulings"]["items"]["properties"]["flag"] = {"enum": [flag["id"] for flag in payload.get("flags", [])] or [""]}
    return schema


class CloudGradingProvider:
    """What every cloud provider implements. Subclasses never see rosters, grades or the catalog."""
    kind = "abstract"

    def submit(self, request):
        raise NotImplementedError

    def poll(self, call_id):
        raise ProviderError("This provider gives no durable call ID; a lost request can only be resent.", confirmed=None)

    def cancel(self, call_id):
        return {"state": "not_supported"}

    def health(self):
        raise NotImplementedError

    def capabilities(self):
        raise NotImplementedError


class HostedEndpoint(BundleMixin, CloudGradingProvider):
    """One named OpenAI-compatible HTTPS endpoint: a base URL, the NAME of its key variable, and a model.

    With a `bundle_size` above one, responses of one batch, question, exam version and step are sent together in one request
    (`bundles`); `max_inflight` then counts those requests, each carrying up to `bundle_size` responses."""
    kind = "hosted"
    # {(url, model, upstream): (monotonic deadline, "withhold" | "think")}: what a host's refusal of the advisory hint taught this
    # process. Advisory only: no retained record depends on it, and the retained step says what was sent.
    hint_refused = {}
    # {(url, model, upstream): monotonic deadline}: the same kind of lesson about `temperature`. The newest reasoning models take
    # none (openai/gpt-5.6-sol and anthropic/claude-fable-5.1 on 2026-09-21: not one host lists it), and as a routing requirement
    # that one field left them without an eligible host: five unbilled 404s, and a mandatory rubric check that could not run.
    temperature_refused = {}
    # {(url, model, upstream): monotonic deadline}: a host whose structured output refused the schema as too complex. Measured live
    # 2026-09-21 on google/gemini-3.8-flash: `minItems = maxItems = 18` on the rubric decisions (one per line of a long rubric) made
    # Google answer 400 INVALID_ARGUMENT to EVERY request for the two questions with 16 and 18 rubric lines, 125 unbilled refusals in
    # one batch, while rubrics of 13 lines and fewer passed. Array LENGTH bounds are a convenience for the decoder, never the
    # contract: `recognition.validate_grade` and the team's own check refuse an answer that does not decide every line.
    bounds_refused = {}
    # {(url, model, upstream): monotonic deadline}: a host that refuses an EMPTY STRING as an enum member. The arbitrator and
    # Tag-Team contracts offer `adopt: ""` and, when a response has no flags, `flag` enum `[""]`, because their prompts tell the
    # model to answer "" for "no assessment adopted". Measured live 2026-09-22 on google/gemini-3.8-flash through the Vercel
    # gateway (resolved provider vertex): 400 for EVERY request, "response_schema.properties[adopt].enum[0]: cannot be empty"
    # and the same for flag_rulings — 913 refusals in one tag-team run, five per response, so not one grade came out. Other
    # hosts accept it, so the contract is unchanged for them and the members are dropped only for the host that refuses.
    empty_enum_refused = {}
    # {(url, model, upstream): (monotonic deadline, effort)}: a host that refused a reasoning EFFORT as an invalid parameter. Measured
    # live 2026-09-24 on meta/muse-spark-1.3-contributor through OpenRouter (host Meta): "max" is listed in the gateway's catalogue, yet
    # every request with it was answered 400 "The request contains invalid parameters", while "xhigh" was answered. As the arbitrator
    # of trial batch 964f1940 that was 465 unbilled refusals and not one ruling. The request is sent once more one listed effort lower
    # (the step records both), and the rest of the batch goes there directly; one step only, and only if that answer came back.
    effort_refused = {}

    def __init__(self, endpoint, timeout=180):
        self.endpoint, self.timeout = endpoint, timeout
        self.name, self.url, self.model = endpoint["name"], endpoint["base_url"].rstrip("/"), endpoint["model"]

    def _route(self):
        return self.url, self.model, self.endpoint["upstream"]

    def _lesson(self):
        deadline, lesson = self.hint_refused.get(self._route(), (0, None))
        return lesson if deadline > time.monotonic() else None

    def _no_temperature(self):
        return self.temperature_refused.get(self._route(), 0) > time.monotonic()

    @staticmethod
    def _refused_parameters(error, body):
        """The router's 404 for a request whose fields no host of the model accepts together. Nothing ran and nothing was billed."""
        if error.status != 404 or not (body.get("provider") or {}).get("require_parameters"):
            return False
        said = str((error.confirmation or {}).get("body") or "")
        return "Filter by Parameters" in said or "requested parameters" in said.casefold()  # Any other 404 (no such model) is not about fields.

    @staticmethod
    def _refused_hint(error, body):
        """What a refusal of the advisory "do not think" hint teaches, or None when the hint is not what was refused."""
        if body.get("reasoning") != {"enabled": False}:
            return None
        if error.status == 404 and (body.get("provider") or {}).get("require_parameters"):
            return "withhold"  # No host lists `reasoning`, and every field is a routing requirement.
        said = str((error.confirmation or {}).get("body") or "").casefold()
        if error.status == 400 and "reasoning" in said and any(word in said for word in ("mandatory", "cannot be disabled", "required")):
            return "think"  # The model cannot answer without thinking.
        return None

    def _no_bounds(self):
        return self.bounds_refused.get(self._route(), 0) > time.monotonic()

    @staticmethod
    def invalid_parameters(error):
        """A host's 400 saying the request's parameters are invalid: the same request is refused the same way every time."""
        said = str((error.confirmation or {}).get("body") or "").casefold()
        return error.status == 400 and "invalid parameter" in said

    def _lower_effort(self, effort):
        """The next effort below `effort` that the connection's facts list (all efforts without facts), or None."""
        if effort not in EFFORTS:
            return None
        listed = [item for item in ((self.endpoint.get("reasoning_options") or {}).get("efforts") or EFFORTS) if item in EFFORTS]
        lower = [item for item in listed if EFFORTS.index(item) < EFFORTS.index(effort)]
        return lower[-1] if lower else None

    def _effort(self, choice):
        """The effort to send for `choice`: one step below an effort this host refused (see `effort_refused`), else the choice."""
        deadline, refused = self.effort_refused.get(self._route(), (0, None))
        if not refused or deadline <= time.monotonic() or EFFORTS.index(choice) < EFFORTS.index(refused):
            return choice
        return self._lower_effort(refused) or choice

    @staticmethod
    def _refused_schema(error, body):
        """A host's 400 for the SCHEMA of a structured request that still carries array length bounds. Any other 400 is not about them."""
        schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema")
        if error.status != 400 or not schema or not any(key in json.dumps(schema) for key in ('"minItems"', '"maxItems"')):
            return False
        said = str((error.confirmation or {}).get("body") or "").casefold()
        # What was measured (Google: "Request contains an invalid argument.", status INVALID_ARGUMENT) and the two ways a decoder says a
        # schema has too many states. A 400 that merely mentions a schema ("too deep") is not something dropping length bounds can fix.
        return any(words in said for words in ("invalid_argument", "invalid argument", "too many states", "too complex"))

    @staticmethod
    def _without_bounds(node):
        if isinstance(node, dict):
            return {key: HostedEndpoint._without_bounds(value) for key, value in node.items() if key not in ("minItems", "maxItems")}
        return [HostedEndpoint._without_bounds(value) for value in node] if isinstance(node, list) else node

    def _no_empty_enum(self):
        return self.empty_enum_refused.get(self._route(), 0) > time.monotonic()

    @staticmethod
    def _refused_empty_enum(error, body):
        """A host's 400 for an enum member that is the empty string. Any other 400 is not something dropping it can fix."""
        schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema")
        if error.status != 400 or not schema or '""' not in json.dumps(schema):
            return False
        said = str((error.confirmation or {}).get("body") or "").casefold()
        return "cannot be empty" in said and "enum" in said

    @staticmethod
    def _without_empty_enum(node):
        """The same contract with "" removed from every enum. An enum left with nothing is dropped to a plain string, so the
        field still exists: the BACKEND decides what is acceptable (`arbitration.validate` refuses an adopted label that is not
        one of the assessments, and a ruling that names a flag the model was not shown)."""
        if isinstance(node, dict):
            value = {key: HostedEndpoint._without_empty_enum(item) for key, item in node.items()}
            kept = [member for member in value["enum"] if member != ""] if isinstance(value.get("enum"), list) else None
            if kept is not None:
                value["enum"] = kept
                if not kept:
                    del value["enum"]
                    value.setdefault("type", "string")
            return value
        return [HostedEndpoint._without_empty_enum(item) for item in node] if isinstance(node, list) else node

    def _key(self):
        return os.environ.get(self.endpoint["key_env"], "") if self.endpoint.get("key_env") else ""

    def _headers(self):
        # A key bound to another service (`config.key_problem`) is never sent. Whatever would carry it (a grading request, a batch,
        # a poll, a cancellation) is refused before anything leaves, as a confirmed failure no retry can fix. A connection stored
        # before the binding existed, and a batch frozen with one, therefore stop here instead of handing the key to that host.
        if problem := key_problem(self.endpoint):
            raise ProviderError(f"Endpoint {self.name} is not used: {problem}", confirmed=True, fixable=False)
        key = self._key()
        return {"Authorization": "Bearer " + key} if key else {}

    def configured(self):
        from urllib.parse import urlsplit
        if key_problem(self.endpoint):
            return False
        return bool(self._key()) or urlsplit(self.url).hostname in LOOPBACK

    def capabilities(self):
        e = self.endpoint
        return {"provider": self.kind, "endpoint": self.name, "model": self.model, "lineage": e["lineage"], "image_input": e["modality"] == "vision",
            "schema_constrained_output": e["structured"] == "json_schema", "json_output": e["structured"] != "none",
            "separated_reasoning": (e.get("reasoning_choice") or reasoning_default(e) or ("on" if e["reasoning"] else "off")) != "off", "batch_api": bool(e["batch_api"]), "durable_call_ids": bool(e["batch_api"]),
            "max_inflight": e["max_inflight"], "rate_limits": "honors 429 and Retry-After; in-flight requests are halved on a rate limit",
            "price_per_million_input": e["price_in"], "price_per_million_output": e["price_out"], "upstream": e["upstream"] or None}

    def listing(self):
        """The host's model list, asked once for every endpoint that shares this URL and key. Never runs inference."""
        if problem := key_problem(self.endpoint):
            return {"status": "key_not_allowed_for_host", "reason": problem}
        if not self.configured():
            return {"status": "not_configured"}
        try:
            result = request_json(self.url + "/models", headers=self._headers(), timeout=8, limit=16 * 1024**2)
        except ProviderError as error:
            return {"status": "authentication_failed" if error.status in (401, 403) else "no_credits" if error.status == 402 else "unavailable"}
        return {"status": "available", "models": [v.get("id") for v in result.get("data", []) if isinstance(v, dict)]}

    def health(self, listing=None):
        listing = listing or self.listing()
        status = listing["status"] if listing["status"] != "available" else "available" if self.model in listing["models"] else "model_unavailable"
        return {"status": status, "model": self.model, "endpoint": self.name, "lineage": self.endpoint["lineage"], **({"reason": listing["reason"]} if listing.get("reason") else {})}

    def body(self, request):
        """An ordinary chat completion with inline images. `request`: {prompt, payload, images, schema?}"""
        e, name, payload, images = self.endpoint, request["prompt"], request["payload"], request.get("images") or []
        plan = request.get("bundle_plan")  # Several responses in one request (`bundles`): their texts and images, in order.
        vercel = catalog_provider(e) == "vercel"
        router_options = e["aggregator"] and not vercel
        if (images or (plan and any(item["images"] for item in plan["items"]))) and e["modality"] != "vision":
            raise ProviderError(f"Endpoint {self.name} is text-only and cannot receive response images.", confirmed=None)
        instruction, metadata = prompt(name)
        if plan:
            instruction += plan["preface"]  # Each item carries its own crop note.
        crop_count = bundles.crops(request)
        if not plan and (note := bundles.crop_note(name, crop_count)):
            # Only the reader is sent enhanced copies; telling a grader about copies it never receives only confuses the crop count.
            instruction += "\n" + note
        # A prompt with no output contract is a PLAIN TEXT prompt (`read`): no schema, no response_format, no schema text in
        # the instruction, and `parse` returns what the model wrote. Measured 2026-09-21: asking the reader for JSON at all is
        # what makes it rewrite a student's digits into the printed question's (see grading/direct_read.py).
        schema = bundles.schema_for(request, crop_count)
        relaxed = schema is not None and e["structured"] == "json_schema" and self._no_bounds()
        if relaxed:
            schema = self._without_bounds(schema)
        unempty = schema is not None and e["structured"] == "json_schema" and self._no_empty_enum()
        if unempty:
            schema = self._without_empty_enum(schema)
        if schema is not None and e["structured"] != "json_schema":
            # No decoding grammar on this host: the prompt carries the contract, and backend validation decides.
            instruction += "\nOutput JSON schema (return one compact JSON instance, not the schema, and nothing else):\n" + json.dumps(schema, separators=(",", ":"))
        picture = lambda image: {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}}
        if plan:
            content = []
            for (_, part), item in zip(bundles.plan_texts(plan), [None, *plan["items"]]):
                content.append({"type": "text", "text": part})
                if item:
                    content.extend(picture(image) for image in item["images"])
        else:
            content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, allow_nan=False)}]
            content.extend(picture(image) for image in images)
        temperature = REASK_TEMPERATURE if request.get("reask") else 0
        # What this request asks about thinking. A role's choice (or the connection's default) comes from the catalogue's facts:
        # "off", "on" or an effort. Without facts the connection's own tick decides, as it always did. Either way a host's refusal
        # of "do not think" is a lesson for this process (see submit): withhold the field, or let the model think.
        choice = e.get("reasoning_choice") or reasoning_default(e)
        facts = e.get("reasoning_options") or {}
        asks_off = choice == "off" if choice else not e["reasoning"]
        lesson = self._lesson() if e["aggregator"] and asks_off else None
        thinking, reasoning_budget = not asks_off or lesson == "think", e["reasoning_tokens"] or 8192
        limits = e.get("token_limits", True)
        body = {"model": self.model, "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": content if images or plan else content[0]["text"]}],
            "stream": False, "store": False}
        sampled = e.get("temperature_supported", True) and not (router_options and self._no_temperature())
        if sampled:
            body["temperature"] = temperature
        # Every request asks for it, thinking or not (requirement, 2026-09-24): for a while only thinking requests
        # did, and DeepSeek, grading without thinking on its own host, then stopped at that host's default of 8,192 tokens.
        room = None if limits or not request.get("room", True) else e.get("output_limit")
        if limits:
            # `max_tokens` on the wire covers reasoning AND the answer, so a thinking model gets both budgets and its answer always has room.
            # Off: the host stops the call (a hosted request has no timeout, `cloud_call`). The model never sees these numbers.
            # A bundle's answer holds one answer per response, so its answer budget is the connection's cap per response.
            body["max_tokens"] = e["max_tokens"] * (len(plan["items"]) if plan else 1) + (reasoning_budget if thinking else 0)
        elif room:
            # Off, yet a host's DEFAULT still stops a model that names no maximum (DeepInfra: 65,536 for MiMo V2.6 Pro; DeepSeek's own
            # host: 8,192), so the most its gateway publishes for this host is asked for instead (`model_catalog.output_room`). Advice,
            # never a cap of ours: a gateway refusing it gets the request once more without it (`submit`).
            body["max_tokens"] = room
        if schema is None:
            pass  # A text prompt: the host decodes nothing, and `require_parameters` below stays for the structured requests.
        elif e["structured"] == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": name.replace("-", "_"), "strict": True, "schema": schema}}
        elif e["structured"] == "json_object":
            body["response_format"] = {"type": "json_object"}
        effort = self._effort(choice) if thinking and choice in EFFORTS else None
        if effort:
            # An effort the model offers, sent as that effort; the wire limit above still leaves the answer its own room. A host that
            # refused this effort gets the next one down (`effort_refused`), and the step says so.
            body["reasoning"] = {"effort": effort, "exclude": False}
        elif thinking and vercel and "budget_tokens" not in e.get("reasoning_controls", []):
            body["reasoning"] = {"enabled": True, "exclude": False}
        elif thinking and limits:
            # A thinking model's reasoning stays apart from the answer, under its own budget; a cut-off answer is a failure.
            body["reasoning"] = {"max_tokens": reasoning_budget, "exclude": False}
        elif thinking:
            body["reasoning"] = {"exclude": False}
        elif e["aggregator"] and lesson != "withhold" and facts.get("supported", True):
            # A hybrid model thinks unless told not to, and some hosts ignore a reasoning budget: measured live, one reviewer spent
            # its whole 8,192 tokens thinking and returned nothing, six times in seven. An endpoint that is not configured as a
            # reasoning model says so; hosts of models that never think ignore the field. It is advice, never a requirement: see submit().
            body["reasoning"] = {"enabled": False}
        if e["upstream"]:
            # An aggregator serves one model from many hosts. Pin the host, so receipts name what actually served the request.
            if vercel:
                body["providerOptions"] = {"gateway": {"order": [e["upstream"]], "only": [e["upstream"]]}}
            else:
                body["provider"] = {"order": [e["upstream"]], "allow_fallbacks": False}
        if router_options:
            if e["structured"] != "none" and schema is not None:
                # Catalogue capabilities are a union across hosts. Route structured requests
                # only to hosts that support their parameters, including response_format.
                # A text request sends no response_format, so it needs no such host.
                body.setdefault("provider", {})["require_parameters"] = True
            body["usage"] = {"include": True}
        if choice:
            metadata = {**metadata, "reasoning": choice}
        if effort and effort != choice:
            metadata = {**metadata, "reasoning_sent": effort, "reasoning_note": f"The host refused the reasoning effort “{choice}” as an invalid parameter, so “{effort}” was sent."}
        if lesson:
            metadata = {**metadata, "reasoning_hint": HINT_NOTES[lesson]}
        if room:
            metadata = {**metadata, "output_limit": room}
        elif not limits and request.get("room", True) is False:
            metadata = {**metadata, "output_limit_note": ROOM_NOTE}
        if not sampled:
            metadata = {**metadata, "temperature_note": "withheld: the catalogue says this model does not support temperature" if e.get("temperature_supported") is False else TEMPERATURE_NOTE}
        if relaxed:
            metadata = {**metadata, "schema_note": BOUNDS_NOTE}
        if unempty:
            metadata = {**metadata, "enum_note": EMPTY_ENUM_NOTE}
        metadata = {**metadata, "provider": self.kind, "endpoint": self.name, "requested_model": self.model, "lineage": e["lineage"],
            "instruction_sha256": digest(instruction.encode()), "output_schema_sha256": digest(schema) if schema is not None else None,
            **({"text_only": True} if schema is None else {}), "endpoint_sha256": digest(self.url.encode()),
            "temperature": temperature if sampled else None, "structured": e["structured"], "request_sha256": digest({"body": {k: v for k, v in body.items() if k != "messages"},
            "instruction": instruction, "payload": payload, "images": [digest(i) for i in images]})}
        return body, metadata

    @staticmethod
    def room_refused(error):
        """The refusals of a maximum above what a host allows, as measured on 2026-09-24: OpenRouter drops every host whose ceiling
        is lower (404 naming its context-length filter) or refuses more than the context holds (400); Xiaomi behind Vercel answers
        400 "Param Incorrect"."""
        detail = str((error.confirmation or {}).get("body") or "")
        return (error.status == 404 and "Filter by Context Length" in detail) or (error.status == 400 and ("maximum context length" in detail or "Param Incorrect" in detail))

    @staticmethod
    def parse(raw, metadata):
        """One validated JSON object, or a confirmed failure with its private receipt. Truncation is a failure, never half an answer."""
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        metadata = {**metadata, "reported_model": raw.get("model"), "system_fingerprint": raw.get("system_fingerprint"),
            "upstream": raw.get("provider"), "provider_request_id": raw.get("id"), "usage": usage}
        try:
            choice = raw["choices"][0]
            if choice.get("finish_reason") in ("length", "max_tokens", "MAX_TOKENS"):
                # The same request ends the same way at temperature 0, and every attempt is paid for: this is never asked again automatically.
                spent = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") if isinstance(usage.get("completion_tokens_details"), dict) else None
                raise ProviderError("The model used its whole output budget" + (f" ({usage.get('completion_tokens')} tokens, {spent} of them reasoning)" if type(spent) is int else "")
                    + " before finishing its answer, so nothing was accepted. Asking again would end the same way: in Compute, raise this endpoint’s answer or reasoning budget, or clear Reasoning model so it answers without thinking; then retry.",
                    raw=raw, metadata=metadata, confirmed=True, fixable=False, confirmation={"malformed": True, "finish_reason": choice.get("finish_reason")})
            if choice.get("finish_reason") not in ("stop", "STOP", "end_turn"):
                raise ValueError("unfinished")
            text = choice["message"]["content"]
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
            text = text.strip()
            if text.startswith("```"):
                text = text.strip("`").removeprefix("json").strip()
            if metadata.get("text_only"):
                # A text prompt (`read`): what the model wrote IS the answer. It is still untrusted data, and the caller
                # validates it (`direct_read.read` refuses an empty reading, `models.transcript` bounds what it becomes).
                result = {"text": text}
            else:
                result = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
                if not isinstance(result, dict):
                    raise ValueError("not an object")
            metadata["reasoning_separated"] = bool(choice["message"].get("reasoning") or choice["message"].get("reasoning_content"))
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            choices = raw.get("choices")
            first = choices[0] if isinstance(choices, list) and choices else None
            raise ProviderError("Model output was malformed or incomplete. No grade was accepted.", raw=raw, metadata=metadata, confirmed=True,
                confirmation={"malformed": True, "finish_reason": first.get("finish_reason") if isinstance(first, dict) else None}) from None
        return {"output": result, "raw": raw, "metadata": metadata}

    def cost(self, usage):
        """(USD, basis). The host's own figure when it reports one; otherwise tokens times the configured prices.

        A connection with NO configured price (the built-in Nebius endpoint, a manual connection left at its defaults) has an
        UNKNOWN cost, never a zero one: a zero would let such a batch run past its spending cap as if every call were free."""
        if isinstance(usage.get("cost"), (int, float)) and not isinstance(usage.get("cost"), bool) and usage["cost"] >= 0:
            return float(usage["cost"]), "reported by the host"
        tokens_in, tokens_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if not self.endpoint.get("price_in") and not self.endpoint.get("price_out"):
            return None, "unavailable: no configured prices"
        if type(tokens_in) is int and type(tokens_out) is int:
            return (tokens_in * self.endpoint["price_in"] + tokens_out * self.endpoint["price_out"]) / 1e6, "estimated from token usage and configured prices"
        return None, "unavailable"

    def _bundle_size(self):
        # A durable batch-API request is one job per response, collected later: it is never bundled.
        return 1 if self.endpoint.get("batch_api") else max(1, int(self.endpoint.get("bundle_size") or 1))

    def _bundle_parallel(self):
        return self.endpoint["max_inflight"]

    def submit(self, request):
        return self._join(request) if self.bundled(request) else self._submit_checked(request)

    def _bundle_single(self, request):
        return self._submit_checked(request)

    def _bundle_call(self, plan):
        """One chat completion for a bundle, through the same corrections as any request (`_submit`)."""
        outcome = self._submit_checked({"prompt": plan["name"], "payload": bundles.plan_payload(plan), "images": [], "schema": plan["wrapper"],
                                        "bundle_plan": plan, "reask": plan["reask"], "durable": False})
        return outcome["result"]["output"], {"result": outcome["result"], "size": len(plan["items"])}

    def _bundle_receipt(self, context, request, answer, share, schema):
        result = context["result"]
        raw = result["raw"] if isinstance(result["raw"], dict) else {}
        metadata = {**result["metadata"], "bundle": share, "usage": bundles.split_usage(result["metadata"].get("usage") or {}, context["size"]),
                    "output_schema_sha256": digest(schema) if schema is not None else None, **({"text_only": True} if schema is None else {}),
                    "request_sha256": digest({"bundle": share["id"], "payload": request["payload"], "images": [digest(i) for i in request.get("images") or []]})}
        # This member's own answer and the call's facts: never another student's answer.
        return {"bundle": share, "answer": answer, "completion": {k: raw.get(k) for k in ("id", "model", "provider", "system_fingerprint", "usage")}}, metadata

    def _submit_checked(self, request):
        try:
            return self._submit(request)
        except ProviderError as error:
            if not (self.invalid_parameters(error) and error.fixable):
                raise
            # What is left after every correction: the host refuses this request's parameters, and it refuses the same request the same
            # way each time (465 unbilled refusals of one setting, trial batch 964f1940). It stops at once, like a refused key.
            raise ProviderError("The host refused this request's parameters (HTTP 400); the same request is refused the same way, so it is not "
                                "retried. Change the setting it refused (a reasoning effort, say) in Compute settings, then retry.",
                                raw=error.raw, metadata=error.metadata, confirmed=True, status=400, fixable=False, confirmation=error.confirmation) from None

    def _submit(self, request):
        self._headers()  # A key bound to another host: refused with its reason before anything leaves.
        if not self.configured():
            raise ProviderError(f"Endpoint {self.name} is not configured: set {self.endpoint['key_env'] or 'its key variable'} on the server.", confirmed=True, fixable=False)
        body, metadata = self.body(request)
        if self.endpoint["batch_api"] and request.get("durable", True):
            return self._submit_batch(body, metadata)
        # Every answer is STREAMED (2026-09-23): a gateway may end a non-streamed request while the model is still writing (Vercel's
        # AI Gateway at about 340 seconds). The stream is reassembled into the same completion, so everything below reads it as before.
        router = self.endpoint["aggregator"] and catalog_provider(self.endpoint) != "vercel"
        send = lambda content: request_stream(self.url + "/chat/completions", headers=self._headers(), timeout=self.timeout, body=content,
                                              limit=8 * 1024**2, usage_option=not router)

        def first():
            # A gateway that refuses the published maximum (`output_limit`, token limits off) gets this request once more without it,
            # as it was sent before that fact existed: the host's default applies. Nothing ran and nothing was billed.
            nonlocal request, body, metadata
            try:
                return send(body)
            except ProviderError as refused:
                if not (metadata.get("output_limit") and self.room_refused(refused)):
                    raise
                request = {**request, "room": False}
                body, metadata = self.body(request)
                return send(body)
        try:
            raw = first()
        except ProviderError as error:
            # The host refused the SCHEMA: "" as an enum member (see `empty_enum_refused`) or its array length bounds (see
            # `bounds_refused`). The same request is sent once more without what was refused, and the lesson is kept for this route
            # only if that was what stood in the way. Nothing ran and nothing was billed. The empty member is tested FIRST: its
            # refusal names it, while the bounds are recognised only by a generic "invalid argument", which Google's error body
            # also carries for the empty member (`"status": "INVALID_ARGUMENT"`) and every grading contract has bounds (`BOX`).
            # Tested the other way round, the empty member was never dropped: the bounds were, uselessly, and the same refusal came
            # back. Once the empty member is gone, a second generic refusal may still be the bounds, so they are tried next.
            fixes = {"empty_enum": (self.empty_enum_refused, self._refused_empty_enum), "bounds": (self.bounds_refused, self._refused_schema)}
            refused = next((name for name, (_, test) in fixes.items() if test(error, body)), None)
            if refused:
                route, until, tried = self._route(), time.monotonic() + HINT_WITHHELD_SECONDS, []
                while refused:
                    fixes[refused][0][route] = until
                    tried.append(refused)
                    try:
                        body, metadata = self.body(request)
                        raw = send(body)
                        break
                    except ProviderError as again:
                        if again.status != 400:
                            raise  # Accepted this time, and then a limit or a drop of its own: that outcome is what happened.
                        refused = next((name for name, (_, test) in fixes.items() if name not in tried and test(again, body)), None)
                        if not refused:
                            for name in tried:
                                fixes[name][0].pop(route, None)  # Not what was dropped: the first refusal is the one to record.
                            raise error from None
                return {"state": "completed", "result": self.parse(raw, metadata)}
            # A reasoning effort the host refuses (see `effort_refused`): once more one listed effort lower. Kept only if answered.
            effort = (body.get("reasoning") or {}).get("effort")
            if effort and self.invalid_parameters(error) and self._lower_effort(effort):
                route = self._route()
                self.effort_refused[route] = (time.monotonic() + HINT_WITHHELD_SECONDS, effort)
                try:
                    body, metadata = self.body(request)
                    raw = send(body)
                except ProviderError as again:
                    if again.status == 400:
                        self.effort_refused.pop(route, None)  # Refused the same way: the effort is not what stood in the way.
                        raise error from None
                    raise  # Accepted this time, and then something of its own: that outcome is what happened.
                return {"state": "completed", "result": self.parse(raw, metadata)}
            # `require_parameters` makes EVERY field a routing requirement, the advisory "do not think" hint included. A model whose hosts
            # do not list `reasoning` (qwen3-vl-32b's only host, 2026-09-20) then has no eligible host, and the router answers 404 before
            # choosing one: nothing ran and nothing was billed, yet every reader and primary request of both cloud tiers was refused.
            # The hint is withheld and this same request sent once more; whatever else a 404 means simply fails again, unchanged.
            # The opposite refusal was measured the same day: a model that cannot answer without thinking (z-ai/glm-5.3-flash, saved
            # without the reasoning mark) answered 400 "Reasoning is mandatory for this endpoint and cannot be disabled" to every
            # request, five unbilled attempts per response, until each response the graders disagreed on had failed. It is then
            # sent as the reasoning model it is: its reasoning budget beside its answer budget, exactly as a marked endpoint.
            # `temperature` is the third such field (2026-09-21): a model none of whose hosts takes one is refused the same way, so
            # it is withheld and the request sent once more. Each correction is tried alone first and kept only if it helped;
            # `response_format` (the output contract) and `max_tokens` (the instructor's cap) are requirements and are never withheld.
            lesson, until = self._refused_hint(error, body), time.monotonic() + HINT_WITHHELD_SECONDS
            cold = self._refused_parameters(error, body) and "temperature" in body
            if not lesson and not cold:
                raise
            route, answered, last = self._route(), False, error
            for hint, no_temperature in [(True, False)] * bool(lesson) + [(False, True)] * cold + [(True, True)] * bool(lesson and cold):
                self.hint_refused.pop(route, None)
                self.temperature_refused.pop(route, None)
                if hint:
                    self.hint_refused[route] = (until, lesson)
                if no_temperature:
                    self.temperature_refused[route] = until
                try:
                    body, metadata = self.body(request)
                    raw, answered = send(body), True
                    break
                except ProviderError as again:
                    last = again
                    if not (self._refused_parameters(again, body) or self._refused_hint(again, body)):
                        break  # Another kind of answer: these fields are not what it is about.
            if not answered:
                self.hint_refused.pop(route, None)  # Neither field was what stood in the way.
                self.temperature_refused.pop(route, None)
                raise last
        return {"state": "completed", "result": self.parse(raw, metadata)}

    # The OpenAI-compatible batch API: a JSONL file, a batch over it, and an output file. It gives a durable
    # job ID that survives a restart, so an interrupted request is recovered instead of resent.
    def _submit_batch(self, body, metadata):
        custom = str(uuid.uuid4())
        line = json.dumps({"custom_id": custom, "method": "POST", "url": "/v1/chat/completions", "body": body}, allow_nan=False).encode() + b"\n"
        boundary = uuid.uuid4().hex
        form = b"".join((f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nbatch\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"request.jsonl\"\r\nContent-Type: application/jsonl\r\n\r\n".encode(), line, f"\r\n--{boundary}--\r\n".encode()))
        uploaded = request_json(self.url + "/files", headers={**self._headers(), "Content-Type": f"multipart/form-data; boundary={boundary}"}, content=form, timeout=self.timeout)
        batch = request_json(self.url + "/batches", headers=self._headers(), timeout=self.timeout,
            body={"input_file_id": uploaded.get("id"), "endpoint": "/v1/chat/completions", "completion_window": "24h"})
        if not batch.get("id"):
            raise ProviderError("The host accepted no batch job.", confirmed=True, confirmation={"batch": batch.get("status")})
        return {"state": "submitted", "call_id": batch["id"], "metadata": {**metadata, "batch_custom_id": custom, "transport": "batch"}}

    def poll(self, call_id, metadata=None):
        batch = ask_about_job(lambda: request_json(f"{self.url}/batches/{call_id}", headers=self._headers(), timeout=30))
        status = batch.get("status")
        # "cancelling" is not an outcome yet: the host may still finish (and bill) the request, so it is waited on like a running job.
        if status in ("validating", "in_progress", "finalizing", "cancelling"):
            return {"state": "submitted"}
        if status in ("failed", "expired", "cancelled"):
            return {"state": status, "confirmation": {"batch_status": status, "errors": batch.get("errors")}}
        if status != "completed" or not batch.get("output_file_id"):
            raise ProviderError("The host reported an unknown batch state.", confirmed=None)
        lines = ask_about_job(lambda: request_json(f"{self.url}/files/{batch['output_file_id']}/content", headers=self._headers(), timeout=60, raw=True,
                                                   limit=16 * 1024**2)).decode("utf-8", "replace").splitlines()
        entry = json.loads(lines[0]) if lines else {}
        response = entry.get("response") or {}
        if entry.get("error") or response.get("status_code", 200) >= 400 or not isinstance(response.get("body"), dict):
            return {"state": "failed", "confirmation": {"batch_status": status, "error": entry.get("error"), "status": response.get("status_code")}}
        return {"state": "completed", "result": self.parse(response["body"], {**(metadata or {}), "provider_call_id": call_id})}

    def cancel(self, call_id):
        if not self.endpoint["batch_api"]:
            return {"state": "not_supported"}
        batch = request_json(f"{self.url}/batches/{call_id}/cancel", headers=self._headers(), timeout=30, body={})
        return {"state": "cancelled" if str(batch.get("status", "")).startswith("cancel") else "requested"}


class DedicatedGpuProvider(CloudGradingProvider):
    """Rented GPUs behind an asynchronous job API (RunPod serverless shape): submit returns a durable job ID at once.

    Built second and only for what no host serves: a specialist reader, a small verifier, the instructor's own
    fine-tuned model. The worker behind the endpoint runs an OpenAI-compatible server; its input is the same
    chat completion body a hosted endpoint receives, so grading code cannot tell the two apart.
    """
    kind = "dedicated"

    def __init__(self, endpoint, timeout=60):
        self.endpoint, self.timeout = endpoint, timeout
        self.name, self.url, self.model = endpoint["name"], endpoint["base_url"].rstrip("/"), endpoint["model"]
        self._hosted = HostedEndpoint({**endpoint, "batch_api": False})

    def _headers(self):
        return self._hosted._headers()

    def capabilities(self):
        return {**self._hosted.capabilities(), "provider": self.kind, "batch_api": False, "durable_call_ids": True, "profile": self.endpoint["profile"],
                "rate_limits": "bounded by the profile's worker count and container concurrency"}

    def cost(self, usage):
        return self._hosted.cost(usage)

    def health(self):
        if problem := key_problem(self.endpoint):
            return {"status": "key_not_allowed_for_host", "reason": problem, "model": self.model, "endpoint": self.name}
        if not self._hosted.configured():
            return {"status": "not_configured", "model": self.model, "endpoint": self.name}
        try:
            result = request_json(self.url + "/health", headers=self._headers(), timeout=8)
        except ProviderError as error:
            return {"status": "authentication_failed" if error.status in (401, 403) else "unavailable", "model": self.model, "endpoint": self.name}
        return {"status": "available", "model": self.model, "endpoint": self.name, "workers": result.get("workers")}

    def submit(self, request):
        self._headers()  # A key bound to another host: refused with its reason before anything leaves.
        if not self._hosted.configured():
            raise ProviderError(f"Endpoint {self.name} is not configured: set {self.endpoint['key_env'] or 'its key variable'} on the server.", confirmed=True, fixable=False)
        body, metadata = self._hosted.body(request)
        job = request_json(self.url + "/run", headers=self._headers(), timeout=self.timeout, body={"input": {"openai_route": "/v1/chat/completions", "openai_input": body}})
        if not job.get("id"):
            raise ProviderError("The GPU provider accepted no job.", confirmed=True, confirmation={"status": job.get("status")})
        return {"state": "submitted", "call_id": job["id"], "metadata": {**metadata, "provider": self.kind, "transport": "dedicated", "profile": self.endpoint["profile"]}}

    def poll(self, call_id, metadata=None):
        # A job the provider no longer holds (RunPod keeps an asynchronous result for a limited time, then answers 404) is a
        # confirmed failure no poll can fix, like a refused key: see `ask_about_job`.
        job = ask_about_job(lambda: request_json(f"{self.url}/status/{call_id}", headers=self._headers(), timeout=30, limit=8 * 1024**2))
        status = job.get("status")
        if status in ("IN_QUEUE", "IN_PROGRESS"):
            return {"state": "submitted"}
        if status in ("FAILED", "TIMED_OUT", "CANCELLED"):
            return {"state": "cancelled" if status == "CANCELLED" else "failed", "confirmation": {"job_status": status, "error": str(job.get("error", ""))[:2000]}}
        if status != "COMPLETED":
            # A result the provider no longer holds cannot be recovered; say so rather than wait forever.
            return {"state": "expired", "confirmation": {"job_status": status}}
        output = job.get("output")
        output = output[0] if isinstance(output, list) and output else output
        if not isinstance(output, dict):
            return {"state": "failed", "confirmation": {"job_status": status, "error": "no output"}}
        result = self._hosted.parse(output, {**(metadata or {}), "provider_call_id": call_id, "execution_ms": job.get("executionTime"), "delay_ms": job.get("delayTime")})
        return {"state": "completed", "result": result}

    def cancel(self, call_id):
        job = request_json(f"{self.url}/cancel/{call_id}", headers=self._headers(), timeout=30, body={})
        return {"state": "cancelled" if job.get("status") == "CANCELLED" else "requested"}


class MathpixEndpoint(CloudGradingProvider):
    """The existing Mathpix reader behind the same interface, so its calls are recorded and retried like any other cloud call."""
    kind = "mathpix"
    ENDPOINT = {"name": "mathpix", "kind": "mathpix", "model": "mathpix-v3-text", "lineage": "mathpix", "modality": "vision", "max_inflight": 16,
                "profile": "", "key_env": "MATHPIX_APP_KEY", "price_in": 0, "price_out": 0}

    def __init__(self, endpoint=None, timeout=180):
        from .mathpix import MathpixProvider
        self.endpoint, self.reader = endpoint or self.ENDPOINT, MathpixProvider()

    def health(self):
        return self.reader.health()

    def capabilities(self):
        return {"provider": self.kind, "endpoint": "mathpix", "image_input": True, "schema_constrained_output": False, "batch_api": False, "durable_call_ids": False,
                "max_inflight": self.endpoint["max_inflight"], "price_per_million_input": None, "price_per_million_output": None, "note": "Billed per image by Mathpix, separately."}

    def cost(self, usage):
        return None, "billed per image by Mathpix, separately"

    def submit(self, request):
        result = self.reader.recognize(request["images"][0])
        return {"state": "completed", "result": {"output": result, "raw": result.get("raw"), "metadata": {**result.get("metadata", {}), "provider": self.kind, "usage": {}}}}


def provider(endpoint, timeout=180):
    if endpoint["kind"] == "system_one":
        # TypeSafe's Jev was removed on 2026-09-23. Its kind must never fall through to the chat class, which would post a grading payload.
        raise ProviderError("The Jev connection was removed on 2026-09-23; nothing was sent.", confirmed=True, fixable=False)
    if endpoint["kind"] == "agent_sdk":
        from .agent_sdk import AgentSdkEndpoint  # The Claude subscription: never an HTTP request, so never the chat class.
        return AgentSdkEndpoint(endpoint, timeout)
    if endpoint["kind"] == "codex_sdk":
        from .codex_sdk import CodexEndpoint  # The ChatGPT subscription through Codex, likewise.
        return CodexEndpoint(endpoint, timeout)
    if endpoint["kind"] == "antigravity":
        from .antigravity import AntigravityEndpoint  # The Gemini subscription through Google's Antigravity CLI, likewise.
        return AntigravityEndpoint(endpoint, timeout)
    if endpoint["kind"] == "decisions":
        from .decisions import DecisionsEndpoint  # The multiple-choice reader (grading/choice.py): never the chat class.
        return DecisionsEndpoint(endpoint, timeout)
    return {"dedicated": DedicatedGpuProvider, "mathpix": MathpixEndpoint}.get(endpoint["kind"], HostedEndpoint)(endpoint, timeout)
