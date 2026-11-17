"""Non-secret workspace preferences; endpoint credentials live only in environment.

Grading reasons in modes (`local`, `hybrid`, and the Cloud tier `cloud_standard`) and roles (reader, primary
grader, second grader, verifier, yardstick). A tier is a policy over roles and named endpoints, not a piece
of hardware; vendors and models appear only here, so replacing one never touches grading code. An endpoint
names the ENVIRONMENT VARIABLE that holds its key; the key itself never enters SQLite or the repository.
"""
import os
import re
from urllib.parse import urlsplit

from . import risk
from .models import identifier, items, number, text

AGGREGATOR = "https://openrouter.ai/api/v1"
VERCEL_GATEWAY = "https://ai-gateway.vercel.sh/v1"
CATALOG_PROVIDERS = {
    "openrouter": {"label": "OpenRouter", "base_url": AGGREGATOR, "key_env": "OPENROUTER_API_KEY"},
    "vercel": {"label": "Vercel AI Gateway", "base_url": VERCEL_GATEWAY, "key_env": "AI_GATEWAY_API_KEY"},
}


def catalog_provider(endpoint):
    """Discovery/transport identity derives from the saved connection, never its model publisher."""
    url = str(endpoint.get("base_url", "")).rstrip("/")
    return next((key for key, value in CATALOG_PROVIDERS.items() if url == value["base_url"]), "")


ROLES = ("reader", "primary", "second", "verifier", "flag_reviewer")
# The roles that may have an optional spare, used only for a request that has exhausted its own attempt cap. The additional
# reviewer has none: it is already the conditional step, and in arbitrator mode the arbitrator.
FALLBACK_ROLES = ("reader", "primary", "second")
TIERS = ("cloud_standard",)
# One cloud scheme and the local ones (requirement, 2026-09-23). A cloud batch is stored as mode 'nebius' with its tier beside it (the catalog CHECK cannot change).
MODES = ("local", "hybrid", *TIERS)
# Schemes removed the same day. Their batches, results, automatic acceptances and exports stay readable and final; nothing can start,
# resume or retry them, and these names only recognize those batches (`historical_scheme`, `CloudMixin.compute`). Their tables
# (migrations 016-019) are retained records and stay.
REMOVED_SCHEMES = {"one_shot": "One-shot", "tag_team": "Tag-Team", "jev_routing": "Jev Routing",
                   "cloud_high_assurance": "Cloud High Assurance", "nebius": "Nebius cloud"}
ONE_SHOT_WORKFLOW, TAG_TEAM_WORKFLOW = "one-shot-v1", "tag-team-v1"


def historical_scheme(profile):
    """The removed image scheme a frozen batch profile ran under, or None."""
    if profile.get("jev_routing_rule"):
        return "jev_routing"
    return {ONE_SHOT_WORKFLOW: "one_shot", TAG_TEAM_WORKFLOW: "tag_team"}.get(profile.get("grading_workflow"))


def removed(scheme):
    """Why a batch of a removed scheme can no longer run, or ""."""
    label = REMOVED_SCHEMES.get(scheme)
    return (f"{label} was removed on 2026-09-23, so this batch cannot continue or be retried. Its results stay readable; grade these "
            "responses again in a new Cloud batch if they need it.") if label else ""


def removed_option(profile):
    """Why a batch that froze a removed option can no longer run, or "". Final Arbitrator Mode v1 and the Jev tie-breaker were options
    of a team batch, not schemes, so its compute is still a tier: resumed now, it would be graded by rules it never froze. The
    conditions are the ones under which those options ran (`arbitration.enabled` and `jev_enabled` as they were that day)."""
    rule = profile.get("final_arbitrator_rule")
    arbitrating = bool(profile.get("final_arbitrator")) and (
        rule == "final-arbitrator-v2" or rule == "final-arbitrator-v1" and bool(profile.get("final_arbitrator_model")))
    if arbitrating and rule == "final-arbitrator-v1":
        label = "Final Arbitrator Mode v1"
    elif not arbitrating and profile.get("jev_tiebreak") and profile.get("jev_tiebreak_rule") in ("jev-tiebreak-v1", "jev-tiebreak-v2", "jev-tiebreak-v3"):
        label = "The Jev tie-breaker"
    else:
        return ""
    return (f"{label}, which this batch froze, was removed on 2026-09-23, so the batch cannot continue or be retried. Its results stay "
            "readable; grade these responses again in a new Cloud batch if they need it.")
ENDPOINT = {"name": "", "kind": "hosted", "base_url": "", "key_env": "", "model": "", "lineage": "", "modality": "vision",
    "structured": "json_schema", "reasoning": False, "reasoning_tokens": 8192, "max_tokens": 8192, "price_in": 0.0, "price_out": 0.0,
    # Requests in flight per connection: 256 by default, the most allowed (requirement, 2026-09-24). At 16, a second grader added
    # from Browse held a 2,600-response batch to about 20 grades a minute while its gateway accepted far more.
    "upstream": "", "aggregator": False, "batch_api": False, "max_inflight": 256, "profile": "", "enabled": True, "notes": "",
    # What the catalogue says this model can be asked about thinking: {"supported", "mandatory", "efforts", "default_effort"}.
    # None means nothing is known (a manual connection, or one saved before these facts existed): its own `reasoning` tick decides.
    "reasoning_options": None}
# Two live failures on 2026-09-20 came from guessing: a model that never thinks was told not to (no host accepted the field), and
# one that must think was told not to (every request refused). The catalogue publishes both facts, so a role's choice is offered
# from them, and a model with one possible configuration simply uses it. Efforts are ordered from least to most thinking.
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
_facts = lambda mandatory=False, efforts=(), default="", supported=True: {"supported": supported, "mandatory": mandatory, "efforts": list(efforts), "default_effort": default}
NEVER_THINKS = _facts(supported=False)
# Prices are USD per million tokens as listed by the aggregator on 2026-09-19; they are settings, and receipts
# prefer the host's own reported cost. Lineage is optional model-family metadata for the audit record.
_open = lambda name, model, lineage, price_in, price_out, **extra: {**ENDPOINT, "name": name, "base_url": AGGREGATOR,
    "key_env": "OPENROUTER_API_KEY", "model": model, "lineage": lineage, "price_in": price_in, "price_out": price_out, "aggregator": True, **extra}
ENDPOINTS = [
    _open("qwen3-vl-32b", "qwen/qwen3-vl-32b-instruct", "qwen3", .104, .416, reasoning_options=NEVER_THINKS, notes="Default primary grader and reader: the model every candidate has to beat."),
    _open("qwen3-vl-235b", "qwen/qwen3-vl-235b-a22b-instruct", "qwen3", .21, 1.9, reasoning_options=NEVER_THINKS, notes="Alternative vision grader."),
    _open("qwen3-vl-235b-thinking", "qwen/qwen3-vl-235b-a22b-thinking", "qwen3", .4, 4.0, reasoning=True, max_tokens=16384, reasoning_options=_facts(mandatory=True), notes="Challenger with separated reasoning."),
    _open("gpt-oss-120b", "openai/gpt-oss-120b", "gpt-oss", .15, .6, modality="text", reasoning=True,
          reasoning_options=_facts(True, ("low", "medium", "high"), "medium"), notes="Transcript grader: text-only."),
    # OpenRouter catalog verified 2026-09-20: text + image input and structured outputs. Pin the release, not a moving alias.
    _open("deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash", "deepseek", .15, .6, reasoning_options=_facts(False, ("low", "high", "max"), "high"),
          notes="DeepSeek V4.1 Flash via OpenRouter. Images and text; optional alternative for grading teams. Estimated token prices; actual host cost is recorded."),
    # Answers without thinking: measured live, its hosts ignore a reasoning budget, and thinking cost five times as much and took
    # nine times as long for the same objections. The benchmark may show the thinking variant earns that; add it as a challenger then.
    _open("kimi-k2.5", "moonshotai/kimi-k2.5", "kimi", .45, 2.25, reasoning=False, reasoning_options=_facts(), notes="Additional reviewer: a vision model answering without thinking."),
    _open("yardstick", "anthropic/claude-sonnet-5", "claude", 2.0, 10.0, reasoning=False,
          reasoning_options=_facts(False, ("low", "medium", "high", "xhigh", "max"), "high"), notes="Closed frontier yardstick for the benchmark only."),
]
PROFILES = {"small": {"gpu_type": "24-48 GB (one card)", "gpu_count": 1, "cpu": 8, "ram_gb": 32, "timeout": 900, "container_concurrency": 8,
                      "quantization": "int4 or int8", "engine": "vllm", "min_workers": 0, "idle_seconds": 60},
            "large": {"gpu_type": "80 GB", "gpu_count": 1, "cpu": 16, "ram_gb": 96, "timeout": 1800, "container_concurrency": 16,
                      "quantization": "bf16", "engine": "vllm", "min_workers": 0, "idle_seconds": 60}}
DEFAULTS = {"glm_enabled": True, "mathpix_enabled": False,
    "local_verifier_model": "Qwen3-VL-8B-Instruct", "local_grader_model": "Qwen3-VL-8B-Instruct",
    "local_verifier_backend": "compatible", "local_grader_backend": "compatible",
    "nebius_model": "Qwen/Qwen3-VL-30B-A3B-Instruct", "nebius_compute": "hosted",
    "timeout": 180, "max_cloud_jobs": 8, "max_batch_responses": 5000,
    "cost_per_response": 0.0, "max_retries": 5, "unknown_resends": 2,
    # When on, each cloud request uses that connection's own max_tokens / reasoning_tokens.
    # Off omits those caps; the host stops the call.
    "token_limits": True,
    # Full concurrency (requirement, 2026-09-23): every cloud component starts as soon as it is
    # ready. `concurrency` and each connection's `max_inflight` are ignored while it is on; a host's "too busy" still slows its
    # own connection, answer areas are still cut a few at a time (`jobs.CUTTING`), and Mathpix keeps its per-minute pace.
    "full_concurrency": False,
    # Grading batches start by themselves (requirement, 2026-09-28). On, the worker starts the dialog's default batch for an
    # assignment's new, reset or changed work once it has stayed the same for two minutes (`auto_grade.py`); off, every batch is the
    # instructor's. A workspace preference like Full concurrency: it never enters a batch's scope.
    "auto_grade": True,
    # The flag review runs by itself (requirement, 2026-10-11): on, about two minutes after a
    # grading batch finishes the worker requests the ordinary flag review for that batch's held grades it may rule on, once each
    # (`auto_flag_review.py`); off, it runs only from its button. A workspace preference like the two beside it: never in a batch's scope.
    "auto_flag_review": True,
    # Late papers certify themselves (requirement, 2026-10-09): on, the worker certifies a certified assignment's late papers once they are graded and cleared,
    # and sends them to Gradescope the way the assignment was sent (`auto_certify.py`, GRADING "LATE PAPERS CERTIFY THEMSELVES").
    # A workspace preference like Start grading batches by themselves: it never enters a batch's scope.
    "auto_certify": True,
    # Gradescope after certifying (requirement, 2026-10-12): on, the Certify dialog offers "Send to Gradescope and publish the grades" (ticked), and
    # Faculty then makes, sends and publishes by itself; a late paper it certified is published where its set already is
    # (`gradescope_after.py`, GRADESCOPE "AFTER CERTIFYING"). Off, every Gradescope write but the late paper's send is the
    # instructor's click, as before. A workspace preference like the one above: it never enters a batch's scope.
    "auto_gradescope": True,
    # The rubric check while a quiz is drafted (requirement, 2026-10-11): on, a quiz draft's revision ends with the check Key & Rubric Finalization would run; off, a revision
    # ends with its rubrics and keys and the approval queues the plan's check, as before 2026-10-11 (`quiz_drafts._quiz_rubric_check`,
    # read from the settings the revision was requested with). A workspace preference: it never enters a batch's scope.
    "draft_rubric_check": True,
    # Mathpix counts requests per minute per account: 200 by default, which the first parallel batch met; the instructor's was
    # raised to 1,000 on 2026-09-20. The pace leaves room for the live application's own reads on the same account.
    "mathpix_per_minute": 800,
    "concurrency": 16, "ocr_threshold": .85, "visual_threshold": .85, "grading_threshold": .9,
    "retention_policy": "store_false",
    "endpoints": ENDPOINTS, "default_tier": "local",
    # Workspace UI history only; the picker shows five and preserves the selected model.
    "recent_models": [],
    "rubric_reviewer": "",
    "tiers": {"cloud_standard": {"reader": "qwen3-vl-32b", "primary": "qwen3-vl-32b", "second": "gpt-oss-120b", "verifier": ""}},
    # {tier: {role: "off" | "on" | an effort}}. A missing entry means the connection's default (`reasoning_default`).
    "role_reasoning": {},
    # Optional per-tier spare for the reader and the two graders (requirement, 2026-09-21): {tier: {role: endpoint name}}. It is
    # used ONLY for the one request that exhausted its attempt cap, and the next response starts again with the original choice;
    # nothing is switched permanently. A grader that never fails never costs anything here.
    "role_fallbacks": {},
    "rubric_reviewer_reasoning": "",  # The rubric reviewer is no team role, so its reasoning choice has its own key.
    # The Faculty assistant's model (AGENTS.md "Faculty assistant", requirement, 2026-09-29): any saved connection, hosted or subscription, with its own reasoning choice. Like the rubric reviewer it is
    # no grading role: kept out of batch previews, frozen profiles and template-reading profiles. Empty: the assistant is off.
    "assistant_model": "",
    "assistant_reasoning": "",
    # The coding agent the assistant hands changes of Faculty itself to (assistant/coding.py; requirement, 2026-09-29): a Claude or ChatGPT subscription connection. Empty: Claude Code with its own default model. No grading role.
    "coding_model": "",
    "coding_reasoning": "",
    # Cloud Standard reads the paper itself (requirement, 2026-09-21), off by default: no GLM-OCR and no Mathpix, the tier's own
    # reader reads once in PLAIN TEXT, and the ordinary reconcile call renders that reading into the contract. Measured that day
    # against 12 readings verified by eye: qwen3-vl-32b 7 of 12 with OCR, 10 reading in plain text; the printed question's value
    # stopped being written over the student's. See `grading/direct_read.py` for what was measured and what is waived.
    "direct_reading": False,
    "reading_page_recovery": False,  # Opt in after checking representative scans; Cloud direct reading, one page round only.
    # GRADING MULTIPLE-CHOICE READER (requirement, 2026-10-08): a Cloud batch reads a question confirmed as multiple choice with
    # OpenAI Decisions (grading/choice.py). Off until the instructor enables it after its calibration.
    "choice_reader": False,
    "yardstick": "yardstick",
    "symbolic_enabled": True, "risk": risk.DEFAULTS,
    # Trial acceptance is a TEST of the risk policy: never final, never exported, needs no gate. A share of it is blind
    # (the AI's decision stays hidden until the instructor has graded), and the gate is computed from that share.
    # Real automatic acceptance is never a setting: it exists only as a permission resting on a retained report.
    "trial_acceptance": True, "blind_share": .2, "audit_share": .05, "error_limit": .01,
    "dedicated_enabled": False, "dedicated_profiles": PROFILES}
KEY_ENV = re.compile(r"[A-Z][A-Z0-9_]{1,60}_API_KEY")
NEBIUS_URL = "https://api.tokenfactory.nebius.com/v1"
# Keys that belong to ONE service, so that no setting can send them anywhere else as a Bearer token (on a listing, a health check
# or a grading request). TypeSafe's key is reserved for the JEV decision adapter (2026-10-03); the old grading connection stays
# removed. No generic connection may name it, exactly as the Mathpix and Faculty variables. An aggregator's key may be named only by
# a connection to that aggregator's own host. OpenAI's key (2026-10-08, OPENAI DECISIONS: triage and the multiple-choice reader)
# may be named only by a connection to OpenAI's own API host; Google's (2026-11-12, GEMINI DECISIONS: whose paper a first page is)
# only by one to Google's. Any other `*_API_KEY` stays the instructor's to point.
RESERVED_KEYS = ("TYPESAFE_API_KEY",)
KEY_HOSTS = {"OPENROUTER_API_KEY": (urlsplit(AGGREGATOR).hostname,), "AI_GATEWAY_API_KEY": (urlsplit(VERCEL_GATEWAY).hostname,),
             "NEBIUS_API_KEY": (urlsplit(NEBIUS_URL).hostname,), "OPENAI_API_KEY": ("api.openai.com",),
             "GEMINI_API_KEY": ("generativelanguage.googleapis.com",)}


def key_hosts(key_env):
    """The hosts a bound key may be sent to, or () for a key bound to none. The built-in Nebius endpoint's URL is server
    configuration (`NEBIUS_GRADING_URL`, a Faculty-managed endpoint), never a setting, so its host is Nebius's too."""
    hosts = KEY_HOSTS.get(key_env, ())
    if key_env == "NEBIUS_API_KEY" and (configured := urlsplit(os.environ.get("NEBIUS_GRADING_URL", "")).hostname):
        hosts = (*hosts, configured)
    return hosts


def key_problem(endpoint):
    """Why this connection may not send its key where it points, or "" when it may. A connection with a problem sends no key at
    all (`providers.cloud.HostedEndpoint` refuses before anything leaves); saving one is refused, loading one never is."""
    key = str(endpoint.get("key_env") or "")
    if key in RESERVED_KEYS:
        return f"{key} was the removed Jev connection’s own key; no connection may name it."
    hosts = key_hosts(key)
    parsed = urlsplit(str(endpoint.get("base_url") or ""))
    if hosts and (parsed.scheme != "https" or parsed.hostname not in hosts):
        return (f"{key} belongs to {' or '.join(hosts)}, and this connection points at {parsed.hostname or 'no host'}, so its key is "
                f"never sent there. Point the connection at {hosts[0]}, or name the key variable of the host it points at.")
    return ""


def hosted_url(value, label="Endpoint"):
    """HTTPS for anything remote; plain HTTP only on loopback, where the scratch app's fake server and tunnels live."""
    parsed = urlsplit(text(value, label + " URL", 300, False))
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError("Inference endpoints cannot contain credentials, queries or fragments.")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("Cloud inference requires HTTPS.")
    return value.rstrip("/")


def reasoning_facts(value):
    """The catalogue's facts about a model's thinking, or None when nothing is known."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"supported", "mandatory", "efforts", "default_effort"} or type(value["supported"]) is not bool \
            or type(value["mandatory"]) is not bool or not isinstance(value["efforts"], list) or not isinstance(value["default_effort"], str):
        raise ValueError("Reasoning options say whether a model can think, whether it must, and which efforts it offers.")
    efforts = [effort for effort in EFFORTS if effort in value["efforts"]]
    if len(efforts) != len(value["efforts"]) or (value["mandatory"] and not value["supported"]) or (not value["supported"] and efforts) \
            or (value["default_effort"] and value["default_effort"] not in efforts):
        raise ValueError("Reasoning options name known efforts once, and a model that must think can think.")
    return {"supported": value["supported"], "mandatory": value["mandatory"], "efforts": efforts, "default_effort": value["default_effort"]}


def reasoning_choices(endpoint):
    """What a role may ask of this connection, least thinking first; None when nothing is known and its own tick decides.

    "off" asks the model not to think, "on" lets a model without effort levels think under the connection's reasoning budget,
    and an effort is sent as that effort. A model that never thinks offers only "off"; one that must think never offers it.
    """
    facts = endpoint.get("reasoning_options")
    if not facts:
        return None
    if not facts["supported"]:
        return ["off"]
    return ([] if facts["mandatory"] else ["off"]) + (facts["efforts"] or ["on"])


def reasoning_default(endpoint):
    """The choice of a role that made none. It keeps what the connection did before these facts existed wherever that was possible."""
    choices = reasoning_choices(endpoint)
    if choices is None:
        return None
    levels = [choice for choice in choices if choice != "off"]
    if not levels or (not endpoint["reasoning"] and "off" in choices):
        return "off"
    if not endpoint["reasoning"]:
        return levels[0]  # It must think and was never set up to: the least thinking that works.
    preferred = endpoint["reasoning_options"]["default_effort"]
    return preferred if preferred in levels else levels[len(levels) // 2] if len(levels) > 1 else levels[0]


def _with_choice(endpoint, wanted=None):
    """The connection with its reasoning choice: `wanted` when it offers it, else its own default; untouched when no facts are known
    (or there is no connection)."""
    choices = reasoning_choices(endpoint) if endpoint else None
    return {**endpoint, "reasoning_choice": wanted if wanted in choices else reasoning_default(endpoint)} if choices else endpoint


def _offered(wanted, endpoint):
    """`wanted` when it is a string the connection offers, else "" (settings always load)."""
    return wanted if isinstance(wanted, str) and endpoint and wanted in (reasoning_choices(endpoint) or ()) else ""


def endpoint_entry(value, saving=False):
    # Optional catalogue facts stay absent on legacy connections, preserving their frozen fingerprints.
    if not isinstance(value, dict) or set(value) - (set(ENDPOINT) | {"temperature_supported", "reasoning_controls", "output_limit", "api_fallback", *SUBSCRIPTION}):
        raise ValueError("Unknown endpoint setting. Keys stay in the server environment; an endpoint only names its key variable.")
    e = {**ENDPOINT, **value}
    # GRADING CLAUDE API FALLBACK: this workspace preference belongs to a Claude connection; old frozen connections stay unchanged.
    if "api_fallback" in e:
        if e["kind"] != "agent_sdk" or type(e["api_fallback"]) is not bool:
            raise ValueError("Direct Anthropic API fallback is a checkbox on a Claude subscription connection.")
        if not e["api_fallback"]:
            del e["api_fallback"]
    if "temperature_supported" in e and type(e["temperature_supported"]) is not bool:
        raise ValueError("Temperature support must be a boolean.")
    if "output_limit" in e:
        # The gateway's published maximum for this connection's host (`model_catalog.output_room`), sent when token limits are off.
        if e["output_limit"] is None:
            del e["output_limit"]
        elif type(e["output_limit"]) is not int or not 0 <= e["output_limit"] <= 10_000_000:  # 0: read, and nothing worth sending.
            raise ValueError("An answer limit is a whole number of tokens.")
    if "reasoning_controls" in e and (not isinstance(e["reasoning_controls"], list) or
            any(control not in ("toggle", "effort", "budget_tokens") for control in e["reasoning_controls"])):
        raise ValueError("Unknown reasoning control.")
    e["reasoning_options"] = reasoning_facts(e["reasoning_options"])
    if e["reasoning_options"] is None:
        # A shipped connection saved before these facts existed takes them from its shipped definition; nothing is fetched.
        shipped = next((p for p in ENDPOINTS if p["model"] == e["model"] and p["base_url"].rstrip("/") == str(e["base_url"]).rstrip("/")), None)
        e["reasoning_options"] = reasoning_facts(shipped["reasoning_options"]) if shipped else None
    e["name"] = identifier(e["name"])
    if e["name"] == "nebius":
        raise ValueError("The name nebius belongs to the built-in Nebius endpoint.")
    if e["kind"] not in ("hosted", "dedicated", *SUBSCRIPTION_KINDS) or e["modality"] not in ("vision", "text") or e["structured"] not in ("json_schema", "json_object", "none"):
        raise ValueError("An endpoint is hosted, dedicated or a subscription (Claude, ChatGPT or Gemini), vision or text, and returns json_schema, json_object or plain output.")
    if e["kind"] in SUBSCRIPTION_KINDS:
        subscription(e)
    else:
        if "sessions" in value:
            raise ValueError("Sessions belong to Claude subscription connections only.")
        if "bundle_size" in value:
            # Responses per request on an API connection (requirement, 2026-09-27). One is the default and is left absent, so connections and frozen batches keep their fingerprints.
            low, high, _ = SUBSCRIPTION["bundle_size"]
            if e["kind"] != "hosted" or type(e["bundle_size"]) is not int or not low <= e["bundle_size"] <= high:
                raise ValueError(f"Responses per request is a whole number from {low} to {high}, on a hosted connection.")
            if e["bundle_size"] == 1:
                del e["bundle_size"]
        e["base_url"] = hosted_url(e["base_url"], e["name"])
    # Only variables that look like provider keys can be named, so a setting can never send another secret to a host.
    if e["key_env"] and (not KEY_ENV.fullmatch(text(e["key_env"], "Key variable", 64)) or e["key_env"].startswith(("FACULTY_", "MATHPIX_"))):
        raise ValueError("Name the environment variable holding this endpoint’s key, such as OPENROUTER_API_KEY. It must end in _API_KEY.")
    # A key bound to another service (`key_problem`) is refused when SAVED. A connection stored before the binding existed still
    # loads exactly as stored, so the settings page and every frozen batch keep working: its key is simply never sent.
    if saving and (problem := key_problem(e)):
        raise ValueError(f"Connection {e['name']}: {problem}")
    text(e["model"], "Endpoint model", 200, False)
    text(e["lineage"], "Endpoint lineage", 60)  # Optional provenance, never a selection requirement.
    for key in ("upstream", "profile", "notes"):
        text(e[key], f"Endpoint {key}", 300)
    for key in ("reasoning", "aggregator", "batch_api", "enabled"):
        if type(e[key]) is not bool:
            raise ValueError(f"Endpoint {key} must be a boolean.")
    for key, low, high in (("max_tokens", 256, 65536), ("reasoning_tokens", 0, 65536), ("max_inflight", 1, 256)):
        if type(e[key]) is not int:
            raise ValueError(f"Endpoint {key} must be an integer.")
        number(e[key], key, low, high)
    for key in ("price_in", "price_out"):
        number(e[key], "Price per million tokens", 0, 10000)
    return e


# A Claude subscription connection (`providers.agent_sdk`; requirement, 2026-09-27): the Claude Code binary on this machine, signed in
# with the instructor's own subscription, instead of API billing. It has no URL and names no key: the binary's sign-in is its own
# and Faculty never touches it. Its two settings, present on these connections only, are how many responses of one question go in
# one call and how many calls run at once; the limiter lets exactly that many requests in (`CloudMixin.limiter`).
# sessions: the connection's concurrency, a typed number (requirement, 2026-09-28; a dropdown of 1-10 before). What is in flight stays
# bounded by `max_inflight` (responses: calls × responses per call, at most 256) and the batch's concurrency.
SUBSCRIPTION = {"bundle_size": (1, 20, 6), "sessions": (1, 256, 2)}
SUBSCRIPTION_FACTS = {"supported": True, "mandatory": True, "efforts": ["low", "medium", "high", "xhigh", "max"], "default_effort": "medium"}
# The ChatGPT subscription through Codex (`providers.codex_sdk`) is the same kind of connection with OpenAI's models, and the
# Gemini subscription through Google's Antigravity CLI (`providers.antigravity`; requirement, 2026-10-14) with the Google plan's.
SUBSCRIPTION_KINDS = ("agent_sdk", "codex_sdk", "antigravity")
# The subscription programs that also run Faculty's drafting tools (`providers.tools`) and may be the coding agent. The Assistant
# runs on every subscription connection (`assistant.engines`).
TOOL_KINDS = ("agent_sdk", "codex_sdk")
SUBSCRIPTION_MODELS = {"agent_sdk": (r"claude-[a-z0-9.-]{1,60}|opus|sonnet|haiku|fable", "a Claude model, such as claude-sonnet-5"),
                       "codex_sdk": (r"(gpt|codex|o)[a-z0-9.-]{0,60}", "an OpenAI model the ChatGPT plan offers, such as gpt-6-astra"),
                       "antigravity": (r"[a-z][a-z0-9.-]{1,60}", "a model the Google plan offers, such as gemini-4-argon")}


def subscription(e):
    """Check a Claude subscription connection in place (see `SUBSCRIPTION`)."""
    if e["base_url"] or e["key_env"] or e["upstream"] or e["aggregator"] or e["batch_api"]:
        raise ValueError("A subscription connection has no URL, key variable, host or batch API: its program's own sign-in is used.")
    pattern, named = SUBSCRIPTION_MODELS[e["kind"]]
    if not re.fullmatch(pattern, str(e["model"])):
        raise ValueError(f"This subscription connection names {named}.")
    e["structured"] = "json_schema"
    if e["reasoning_options"] is None:
        if e["kind"] == "codex_sdk":
            from .providers.codex_sdk import facts  # Each OpenAI model offers its own reasoning levels.
            e["reasoning_options"] = reasoning_facts(facts(e["model"]))
        elif e["kind"] == "antigravity":
            from .providers.antigravity import facts  # So does each model of the Google plan.
            e["reasoning_options"] = reasoning_facts(facts(e["model"]))
        else:
            e["reasoning_options"] = reasoning_facts(SUBSCRIPTION_FACTS)
    e["reasoning"] = True
    for key, (low, high, default) in SUBSCRIPTION.items():
        e.setdefault(key, default)
        if type(e[key]) is not int or not low <= e[key] <= high:
            raise ValueError(f"A subscription connection's {key.replace('_', ' ')} is a whole number from {low} to {high}.")
    e["max_inflight"] = min(256, e["bundle_size"] * e["sessions"])
    e["price_in"] = e["price_out"] = 0.0
    return e


# Preferences that no longer exist. Settings saved while they did must still load, so they are dropped, not refused. Shadow mode
# (hide every proposal of a batch until the instructor's own decision) was removed on 2026-09-20: the instructor never used it.
# `jev_review*` was a first reading of the Jev mode (a gate on agreed grades) that the instructor replaced the same day with the
# tie-breaker (`jev_tiebreak*`). It never ran, since its connection was never installed; a page opened before the change may still send it.
# The removed schemes' preferences (2026-09-23) load the same way: One-shot, Tag-Team, Jev Routing, the Jev tie-breaker, Final
# Arbitrator Mode's own model, and the High Assurance escalation switch.
RETIRED = ("shadow", "answer_tokens", "reasoning_budget", "jev_review", "jev_review_threshold",
           "one_shot_model", "one_shot_reasoning", "tag_team_model", "tag_team_reasoning", "tag_team_reviewer", "tag_team_reviewer_reasoning",
           *(f"jev_routing_{field}_{level}" for level in range(1, 6) for field in ("model", "reasoning")),
           "jev_tiebreak", "jev_tiebreak_confidence", "final_arbitrator", "final_arbitrator_model", "final_arbitrator_reasoning", "escalation_enabled",
           # Faculty's own spending cap per batch, removed on 2026-09-23 at the instructor's request: the OpenRouter and Vercel
           # accounts carry the spending limits; a request a gateway refuses for credit (402) fails at once and is never resent.
           "max_job_cost",
           # The canary (approved responses graded again before each cloud batch), removed on 2026-09-28 at the instructor's request:
           # "delete the canary functionality, I don't see myself ever using it". Its past runs stay readable on their batches.
           "canary_enabled", "canary_size", "canary_margin",
           # Rechecking saved transcriptions with other models, cancelled and removed at the instructor's request (2026-11-02 "Cancel that",
           # 2026-11-03 "go back to the originals"; GRADING "THE SAVED TRANSCRIPTIONS STAND").
           "training_transcript_check")


def validate(value, saving=False):
    """Clean settings. The SAME function loads stored settings and checks new ones, and stored settings must always load, so
    what may only be refused when the instructor saves (a key bound to another host, `key_problem`) is checked with `saving`."""
    if isinstance(value, dict):
        value = {key: item for key, item in value.items() if key not in RETIRED}
        # High Assurance's team, reasoning and spares were saved beside the Cloud tier's; they load and are dropped.
        for key in ("tiers", "role_reasoning", "role_fallbacks"):
            if isinstance(value.get(key), dict):
                value[key] = {tier: item for tier, item in value[key].items() if tier != "cloud_high_assurance"}
        if isinstance(value.get("default_tier"), str) and value["default_tier"] in REMOVED_SCHEMES:
            value["default_tier"] = "cloud_standard"
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("Unknown grading preference. Credentials and endpoints must be configured on the server.")
    result = {**DEFAULTS, **value}
    result["risk"] = risk.settings(result["risk"])
    for key in ("glm_enabled", "mathpix_enabled", "dedicated_enabled", "symbolic_enabled", "trial_acceptance", "token_limits", "direct_reading", "reading_page_recovery", "full_concurrency", "auto_grade", "auto_flag_review", "auto_certify", "auto_gradescope", "draft_rubric_check", "choice_reader"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} must be a boolean.")
    for key in ("local_verifier_model", "local_grader_model", "nebius_model"):
        text(result[key], "Model name", 200, False)
    for key in ("local_verifier_backend", "local_grader_backend"):
        if result[key] not in ("compatible", "ollama"):
            raise ValueError("Choose a compatible chat endpoint or native Ollama backend.")
    for key, low, high in (("timeout", 10, 600), ("max_cloud_jobs", 1, 32), ("max_batch_responses", 1, 20000),
                           ("max_retries", 0, 10), ("unknown_resends", 0, 5), ("concurrency", 1, 256),
                           ("mathpix_per_minute", 1, 5000)):
        if type(result[key]) is not int:
            raise ValueError(f"{key} must be an integer.")
        number(result[key], key, low, high)
    for key, high in (("cost_per_response", 100), ("ocr_threshold", 1),
                      ("visual_threshold", 1), ("grading_threshold", 1), ("blind_share", 1), ("audit_share", 1), ("error_limit", .5)):
        number(result[key], key, 0, high)
    if result["nebius_compute"] not in ("hosted", "managed_endpoint") or result["retention_policy"] != "store_false":
        raise ValueError("Choose hosted or managed endpoint compute with store_false retention.")
    result["endpoints"] = [endpoint_entry(e, saving) for e in items(result["endpoints"], "Endpoints", 40)]
    names = [e["name"] for e in result["endpoints"]]
    if len(set(names)) != len(names):
        raise ValueError("Endpoint names must be distinct.")
    known = {e["name"]: e for e in result["endpoints"]}
    result['rubric_reviewer'] = text(result['rubric_reviewer'], 'Rubric drafting and review model', 200)
    if result['rubric_reviewer'] and result['rubric_reviewer'] not in known and result['rubric_reviewer'] != 'nebius':
        raise ValueError('Choose a configured rubric drafting and review model.')
    result['assistant_model'] = text(result['assistant_model'], 'Assistant model', 200)
    result['coding_model'] = text(result['coding_model'], 'Coding agent', 200)
    coder_connection = known.get(result['coding_model'])
    if result['coding_model'] and (coder_connection is None or not (coder_connection['kind'] in TOOL_KINDS or (
            coder_connection['kind'] == 'hosted' and catalog_provider(coder_connection) in ('openrouter', 'vercel') and not coder_connection.get('batch_api')))):
        raise ValueError('The coding agent is a Claude or ChatGPT subscription connection, or an OpenRouter or Vercel model (run by Codex).')
    if result['assistant_model'] and result['assistant_model'] not in known and result['assistant_model'] != 'nebius':
        raise ValueError('Choose a configured model for the Faculty assistant.')
    recent = [identifier(name) for name in items(result["recent_models"], "Recent models", 40)]
    result["recent_models"] = list(dict.fromkeys(name for name in recent if name == "nebius" or name in known))
    tiers = result["tiers"]
    if not isinstance(tiers, dict) or "cloud_standard" not in tiers or set(tiers) - set(TIERS):
        raise ValueError("Configure the Cloud models.")
    clean = {}
    for tier, roles in tiers.items():
        if not isinstance(roles, dict) or set(roles) - set(ROLES):
            raise ValueError("A tier assigns endpoints to reader, primary, second, verifier and flag_reviewer.")
        roles = {role: roles.get(role) or "" for role in ROLES}
        for role, name in roles.items():
            if not isinstance(name, str):
                raise ValueError(f"{tier} needs an endpoint name for its {role}.")
            if name and name != "nebius" and name not in known:
                raise ValueError(f"{tier} names an unknown endpoint for its {role}.")
            if name in known and role in ("reader", "primary", "verifier", "flag_reviewer") and known[name]["modality"] != "vision":
                raise ValueError(f"The {role} sees the response image, so it needs a vision endpoint. A text-only model can be the second grader.")
        # Any model family may fill any role (requirement, 2026-09-23): the rule that a same-lineage second grader or verifier is no
        # independent vote is gone, and a batch frozen with `standard.FAMILY_RULE` asks nothing about families. Lineages are still
        # recorded. A batch frozen before it keeps its family checks, and its settings were validated under them.
        clean[tier] = roles
    result["tiers"] = clean
    spare = result["role_fallbacks"]
    if not isinstance(spare, dict) or set(spare) - set(TIERS) or any(not isinstance(v, dict) or set(v) - set(FALLBACK_ROLES) for v in spare.values()):
        raise ValueError("A fallback belongs to the handwriting reader, the primary grader or the second grader of a cloud tier.")
    kept_spares = {}
    for tier, roles_chosen in spare.items():
        for role, name in roles_chosen.items():
            if not name:
                continue
            if not isinstance(name, str) or name not in known:
                raise ValueError(f"{tier} names an unknown endpoint as the fallback {role} grader.")
            if role in ("reader", "primary") and known[name]["modality"] != "vision":
                raise ValueError(f"The {'handwriting reader' if role == 'reader' else 'primary grader'} sees the response image, so its fallback needs a vision endpoint.")
            kept_spares.setdefault(tier, {})[role] = name
    result["role_fallbacks"] = kept_spares
    chosen = result["role_reasoning"]
    if not isinstance(chosen, dict) or set(chosen) - set(TIERS) or any(not isinstance(v, dict) or set(v) - set(ROLES) for v in chosen.values()):
        raise ValueError("Reasoning choices belong to the reader, primary, second and verifier of a cloud tier.")
    # Settings must always load: a choice its connection no longer offers (another model was chosen, or the catalogue's facts
    # changed) simply falls back to that connection's default instead of refusing every other setting.
    result["role_reasoning"] = {tier: kept for tier in TIERS if (kept := {role: choice for role, choice in chosen.get(tier, {}).items()
        if isinstance(choice, str) and clean[tier][role] in known and choice in (reasoning_choices(known[clean[tier][role]]) or ())})}
    # The rubric reviewer's choice follows the same rule: kept when its model offers it, otherwise that model's default.
    reviewer = known.get(result["rubric_reviewer"] or clean["cloud_standard"]["primary"])
    result["rubric_reviewer_reasoning"] = _offered(result["rubric_reviewer_reasoning"], reviewer)
    result["assistant_reasoning"] = _offered(result["assistant_reasoning"], known.get(result["assistant_model"]))
    result["coding_reasoning"] = _offered(result["coding_reasoning"], known.get(result["coding_model"]))
    if result["default_tier"] not in ("local", "hybrid", *TIERS) or (result["yardstick"] and
            (not isinstance(result["yardstick"], str) or result["yardstick"] not in known)):
        raise ValueError("Choose a known default tier and yardstick endpoint.")
    profiles = result["dedicated_profiles"]
    if not isinstance(profiles, dict) or not profiles or len(profiles) > 10 or any(not isinstance(p, dict) or set(p) - set(PROFILES["small"]) for p in profiles.values()):
        raise ValueError("Dedicated GPU profiles describe gpu_type, gpu_count, cpu, ram_gb, timeout, container_concurrency, quantization, engine, min_workers and idle_seconds.")
    result["dedicated_profiles"] = {identifier(name): {**PROFILES["small"], **profile} for name, profile in profiles.items()}
    return result


def endpoint(kind):
    env = {"local_verifier": "FACULTY_GRADING_VERIFIER_URL", "local_grader": "FACULTY_GRADING_GRADER_URL",
           "nebius": "NEBIUS_GRADING_URL"}[kind]
    value = os.environ.get(env, NEBIUS_URL if kind == "nebius" else "")
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError("Inference endpoints cannot contain credentials, queries or fragments.")
    if kind == "nebius":
        if parsed.scheme != "https":
            raise ValueError("Cloud inference requires HTTPS.")
    elif parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.scheme not in ("http", "https"):
        raise ValueError("Fully local inference must use a loopback endpoint. Use an SSH tunnel for another local host.")
    return value.rstrip("/")


def apply_token_policy(endpoint, settings):
    """Mark whether this request should send the connection's own token caps.

    The caps stay on the connection (any OpenRouter or hosted model). Missing
    workspace keys keep the previous behaviour: limits on.
    """
    return {**endpoint, "token_limits": bool(settings.get("token_limits", True))}


def nebius_endpoint(settings):
    """The existing Nebius mode as one named hosted endpoint among the others: same client, same records."""
    return apply_token_policy({**ENDPOINT, "name": "nebius", "base_url": endpoint("nebius"), "key_env": "NEBIUS_API_KEY", "model": settings["nebius_model"],
            "lineage": "qwen3" if "qwen" in settings["nebius_model"].casefold() else "unrecorded", "structured": "json_object",
            "max_inflight": settings["concurrency"], "notes": "Built-in: Nebius Token Factory or a Faculty-managed compatible endpoint."}, settings)


def named_endpoint(settings, name):
    if name == "nebius":
        return nebius_endpoint(settings)
    found = next((e for e in settings["endpoints"] if e["name"] == name), None)
    if not found or not found["enabled"]:
        raise ValueError(f"Endpoint {name} is not configured or is disabled.")
    if found["kind"] == "dedicated" and not settings["dedicated_enabled"]:
        raise ValueError("The dedicated GPU provider is disabled in Compute settings.")
    return apply_token_policy(found, settings)


def roles(settings, mode):
    """{role: endpoint} for the Cloud tier."""
    chosen = (settings.get("role_reasoning") or {}).get(mode, {})
    found = {}
    for role, name in settings["tiers"][mode].items():
        endpoint = named_endpoint(settings, name) if name else None
        # A frozen batch keeps the settings it was submitted with: one from before these facts existed has none and behaves as it did.
        found[role] = _with_choice(endpoint, chosen.get(role))
    return found


def fallback(settings, mode, role):
    """The spare connection for one grader of a tier, or None. It uses its OWN default reasoning: the choice saved for the role
    belongs to the model that was chosen there, and another model may not offer it."""
    name = ((settings.get("role_fallbacks") or {}).get(mode) or {}).get(role)
    if not name or mode not in TIERS:
        return None
    return _with_choice(named_endpoint(settings, name))


def rubric_reviewer(settings):
    """The rubric reviewer's connection with its reasoning choice, exactly as `roles` gives a team role. None when nothing is chosen."""
    name = settings["rubric_reviewer"] or settings["tiers"]["cloud_standard"]["primary"]
    if not name:
        return None
    return _with_choice(named_endpoint(settings, name), settings.get("rubric_reviewer_reasoning"))


def assistant(settings):
    """The Faculty assistant's connection with its reasoning choice, as `rubric_reviewer` resolves the rubric model. None when no
    model is chosen (the assistant is off)."""
    name = settings.get("assistant_model")
    if not name:
        return None
    return _with_choice(named_endpoint(settings, name), settings.get("assistant_reasoning"))


def coding_agent(settings):
    """The coding agent's connection with its reasoning choice; without a choice, Claude Code with its own default model."""
    name = settings.get("coding_model")
    if not name:
        return {"name": "Claude Code (its default model)", "kind": "agent_sdk", "model": "", "reasoning_choice": ""}
    endpoint = named_endpoint(settings, name)
    choices = reasoning_choices(endpoint) or ()
    wanted = settings.get("coding_reasoning")
    return {**endpoint, "reasoning_choice": wanted if wanted in choices else (reasoning_default(endpoint) or "")}
