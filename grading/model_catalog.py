"""Disposable per-provider discovery caches; selected connections remain workspace settings.

Reads public metadata only, on demand. No credentials, inference, catalog writes,
or retained teaching payloads. Existing jobs keep their frozen endpoint profiles.
A subscription's models come from its installed program, asked on demand and never kept.
"""
import math
import re
import threading
import time
from collections import OrderedDict

from course_storage import timestamp
from . import config
from .providers.base import ProviderError, request_json

TTL = 3600
FAILURE_BACKOFF = 30
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*/[A-Za-z0-9][A-Za-z0-9_.:/-]{0,180}")


def family(model):
    """Optional base-family metadata, including named derivatives; unknowns stay blank.

    A publisher or tokenizer alone does not establish a base family.
    Keep these spellings consistent with existing configured endpoints.
    """
    slug = model.lower().split("/", 1)[-1]
    if slug.startswith("deepseek-r1-distill-qwen-"):
        return "qwen2"
    if slug.startswith("deepseek-r1-distill-llama-"):
        return "llama3"
    for pattern, name in ((r"gpt-oss", "gpt-oss"), (r"qwen-?3", "qwen3"), (r"qwen-?2", "qwen2"),
                          (r"llama-?3", "llama3"), (r"llama-?4", "llama4"), (r"deepseek", "deepseek"),
                          (r"claude", "claude"), (r"gpt-[3456]", "gpt"), (r"gemini", "gemini"), (r"kimi", "kimi"),
                          (r"glm-?[45]", "glm"), (r"grok", "grok"), (r"^mimo(?:-|$)", "mimo")):
        if re.search(pattern, slug):
            return name
    return ""


def price(value):
    try:
        result = float(value) * 1_000_000
        return result if math.isfinite(result) and 0 <= result <= 10000 else None
    except (ValueError, TypeError, OverflowError):
        return None


def vercel_row(row):
    """Translate Vercel's public language-model metadata without borrowing OpenRouter capabilities."""
    if row.get("type") != "language":
        return None
    modalities, pricing, tags = row.get("modalities") or {}, row.get("pricing") or {}, row.get("tags") or []
    if not isinstance(modalities, dict) or not isinstance(pricing, dict) or not isinstance(tags, list):
        return None
    options = row.get("reasoning_options")
    controls, values = [], []
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            kind = option.get("type")
            if kind in ("toggle", "effort", "budget_tokens") and kind not in controls:
                controls.append(kind)
            if kind == "effort" and isinstance(option.get("values"), list):
                values.extend(option["values"])
        supported = bool(controls)
        facts = {"supported": supported, "mandatory": supported and "toggle" not in controls and "none" not in values,
                 "efforts": [effort for effort in config.EFFORTS if effort in values], "default_effort": ""}
    else:
        # A reasoning tag alone does not tell us whether thinking can be disabled.
        facts = None if "reasoning" in tags else dict(config.NEVER_THINKS)
    return {**row, "architecture": {"input_modalities": modalities.get("input", ["text", "image"] if "vision" in tags else ["text"]),
                                    "output_modalities": modalities.get("output", ["text"])},
            "context_length": row.get("context_window"), "top_provider": {"max_completion_tokens": row.get("max_tokens")},
            "pricing": {"prompt": pricing.get("input"), "completion": pricing.get("output")},
            # Vercel's chat-completions surface implements response_format.json_schema; its model parameter list omits it.
            "supported_parameters": ["structured_outputs"], "catalogue_reasoning": facts, "catalogue_controls": controls}


def normalize(raw, provider="openrouter"):
    source = config.CATALOG_PROVIDERS.get(provider)
    if source is None:
        raise ValueError("Choose OpenRouter or Vercel AI Gateway.")
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
        raise ValueError("Missing model list.")
    models = {}
    for row in raw["data"][:5000]:
        if not isinstance(row, dict):
            continue
        if provider == "vercel":
            row = vercel_row(row)
            if row is None:
                continue
        mid, architecture = row.get("id"), row.get("architecture") or {}
        if not isinstance(mid, str) or not MODEL_ID.fullmatch(mid) or not isinstance(architecture, dict):
            continue
        inputs, outputs = architecture.get("input_modalities", []), architecture.get("output_modalities", [])
        if not isinstance(inputs, list) or not isinstance(outputs, list) or "text" not in inputs or "text" not in outputs:
            continue
        if mid.startswith("openrouter/"):  # Routers do not identify a fixed model.
            continue
        parameters = row.get("supported_parameters") or []
        pricing, host = row.get("pricing") or {}, row.get("top_provider") or {}
        if not isinstance(parameters, list) or not isinstance(pricing, dict) or not isinstance(host, dict):
            continue
        limit = host.get("max_completion_tokens")
        limit = min(8192, limit) if type(limit) is int and limit > 0 else 8192
        context = row.get("context_length")
        # Leave room for the rubric/transcript inside a small model's total context.
        if type(context) is int and context > 0:
            limit = min(limit, context // 2)
        prices = [price(pricing.get(key)) for key in ("prompt", "completion")]
        structured = "json_schema" if "structured_outputs" in parameters else "json_object" if "response_format" in parameters else "none"
        # The host's own statement about thinking: whether the model can, whether it must, and which efforts it offers. "none" is
        # the same request as Off, and an effort this application does not know is left out rather than guessed at.
        stated = row.get("reasoning") if isinstance(row.get("reasoning"), dict) else {}
        offered = stated.get("supported_efforts") if isinstance(stated.get("supported_efforts"), list) else []
        efforts = [effort for effort in config.EFFORTS if effort in offered]
        thinks = "reasoning" in parameters or bool(stated)
        facts = {"supported": thinks, "mandatory": thinks and stated.get("mandatory") is True, "efforts": efforts if thinks else [],
                 "default_effort": stated.get("default_effort") if thinks and stated.get("default_effort") in efforts else ""}
        if provider == "vercel":
            facts = row["catalogue_reasoning"]
        lineage = family(mid)
        # Shipped profiles retain deliberately chosen reasoning/budget settings.
        preset = next((e for e in config.ENDPOINTS if e["model"] == mid and e["base_url"] == source["base_url"]), None)
        endpoint = {**config.ENDPOINT, "base_url": source["base_url"], "key_env": source["key_env"], "aggregator": True,
                    "model": mid, "lineage": lineage, "modality": "vision" if "image" in inputs else "text", "structured": structured,
                    "max_tokens": limit, "price_in": prices[0], "price_out": prices[1], "reasoning_options": facts,
                    "notes": f"Added from the {source['label']} catalogue. Base token prices are estimates; context tiers and reported call costs may differ."}
        room = output_room(host.get("max_completion_tokens"), context)
        if room is not None:
            endpoint["output_limit"] = room
        if provider == "vercel":
            endpoint["reasoning_controls"] = row["catalogue_controls"]
            if type(row.get("temperature")) is bool:
                endpoint["temperature_supported"] = row["temperature"]
        if preset:
            endpoint.update({key: preset[key] for key in ("lineage", "reasoning", "reasoning_tokens")})
        # A model that must think needs room for its answer beside its thinking, marked or not. The mark itself stays what a shipped
        # connection chose: it decides the DEFAULT effort (a marked model keeps its host's default, an unmarked one thinks least).
        if endpoint["reasoning"] or (facts or {}).get("mandatory"):
            # The wire limit covers thinking and the answer together.
            total = host.get("max_completion_tokens")
            if type(total) is not int or total <= 0:
                total = context // 2 if type(context) is int and context > 0 else None
            if type(total) is int and total > 0:
                if type(context) is int and context > 0:
                    total = min(total, context // 2)
                endpoint["max_tokens"] = min(limit, max(256, total // 2))
                endpoint["reasoning_tokens"] = min(endpoint["reasoning_tokens"], max(0, total - endpoint["max_tokens"]))
        name, description = row.get("name"), row.get("description")
        models[mid] = {"id": mid, "name": name[:200] if isinstance(name, str) else mid,
                       "description": description[:1200] if isinstance(description, str) else "",
                       "context_length": context if type(context) is int and context > 0 else None,
                       "endpoint": endpoint,
                       "unavailable": "Token prices are unavailable. Use a manual connection to set an estimate." if None in prices else
                                      "The advertised answer limit is too small for grading." if limit < 256 else ""}
    if not models:
        raise ValueError("No usable model metadata.")
    return sorted(models.values(), key=lambda model: (model["name"].casefold(), model["id"]))


# How much a model may write when token limits are off (requirement, 2026-09-24). A request that names no maximum gets the host's
# DEFAULT, which can be far below what the host allows: DeepInfra documents 65,536 for MiMo V2.6 Pro against a ceiling of 943,718, and
# two MiMo rulings ended there after 17 minutes of thinking. So a connection keeps its gateway's PUBLISHED maximum as a fact
# (`output_limit`) and sends it. Both gateways refuse more than a host allows: OpenRouter drops every host whose ceiling is lower
# (none left: 404) and refuses more than the context holds beside the prompt (400); on Vercel the host itself may refuse ("Param
# Incorrect" from Xiaomi at 500,000, probed that day). So the fact is the ceiling of the chosen host, or for any host the smallest
# ceiling among those that allow at least `ROOM_FLOOR`, always leaving a tenth of the context (at least `PROMPT_ROOM`) for the prompt.
# A maximum below `ROOM_FLOOR` is recorded as 0 and never sent: it could only cut a model off sooner than the defaults measured that day.
ROOM_FLOOR = 65536
PROMPT_ROOM = 32768


def output_room(limit, context):
    """The maximum to send for a host that allows `limit` answer tokens in `context`: 0 when nothing is gained by one, None when unknown."""
    if type(limit) is not int or limit <= 0:
        return None
    if type(context) is int and context > 0:
        limit = min(limit, context - max(context // 10, PROMPT_ROOM))
    return limit if limit >= ROOM_FLOOR else 0


def any_host_room(hosts):
    """For a connection that lets the gateway choose: the most that every host allowing at least ROOM_FLOOR accepts."""
    rooms = [host["output_limit"] for host in hosts if host.get("output_limit")]
    return min(rooms) if rooms else 0 if any(host.get("output_limit") == 0 for host in hosts) else None


# The hosts ("providers") that serve one model through a gateway (requirement, 2026-09-23). Both gateways publish them at /models/{model}/endpoints, in the same shape.
# What a connection saves as `upstream` is what that gateway routes by: OpenRouter's endpoint slug (`tag`, "deepinfra/fp8", which
# names the exact variant), Vercel's provider name ("xiaomi").
HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,60}(?:/[A-Za-z0-9][A-Za-z0-9_.:-]{0,60})?")
HOSTS_TTL = 600
HOSTS_MAX = 128  # PERFORMANCE: browsing different public model IDs cannot grow a process cache without bound.


def rounded(value):
    return None if value is None else round(value, 6)


def normalize_hosts(raw, provider):
    data = raw.get("data") if isinstance(raw, dict) else None
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, list):
        raise ValueError("No host listing.")
    hosts = {}
    for row in endpoints[:100]:
        if not isinstance(row, dict):
            continue
        slug = row.get("tag") if provider == "openrouter" else row.get("provider_name")
        if not isinstance(slug, str) or not HOST.fullmatch(slug) or slug in hosts:
            continue
        supported = {v for v in row.get("supported_parameters") or [] if isinstance(v, str)}
        tags = {v for v in row.get("tags") or [] if isinstance(v, str)}
        pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
        whole = lambda value: value if type(value) is int and value > 0 else None
        quantization = row.get("quantization") if isinstance(row.get("quantization"), str) else ""
        hosts[slug] = {"id": slug, "name": str(row.get("provider_name") or slug)[:80], "quantization": quantization[:20],
            # Per-token strings times a million carry float noise (0.39999999999999997); a chosen host's prices are saved, so they are rounded.
            "price_in": rounded(price(pricing.get("prompt"))), "price_out": rounded(price(pricing.get("completion"))),
            "context_length": whole(row.get("context_length")), "max_completion_tokens": whole(row.get("max_completion_tokens")),
            "output_limit": output_room(whole(row.get("max_completion_tokens")), whole(row.get("context_length"))),
            # Vercel's chat surface takes a JSON schema for every model and says so per host with a tag; OpenRouter lists the parameter.
            "structured": ("structured-output" in tags) if provider == "vercel" else bool(supported & {"response_format", "structured_outputs"}),
            "reasoning": "reasoning" in tags or "reasoning" in supported,
            "vision": ("vision" in tags) if provider == "vercel" else None,
            "uptime": row["uptime_last_30m"] if isinstance(row.get("uptime_last_30m"), (int, float)) and not isinstance(row.get("uptime_last_30m"), bool) else None}
    return sorted(hosts.values(), key=lambda host: (host["name"].casefold(), host["id"]))


# The subscriptions' own programs, asked each time the instructor browses a plan's models or refreshes the list (requirement, 2026-10-01). Nothing is kept between asks; the built-in list stands in when the program cannot answer.
SUBSCRIPTION_PROGRAMS = {"claude": ("agent_sdk", "Claude Code", "Claude"), "chatgpt": ("codex_sdk", "Codex", "ChatGPT"),
                         "gemini": ("antigravity", "Antigravity", "Gemini")}


def subscription_facts(efforts, default):
    """A listed model's reasoning facts in a connection's terms (`config.reasoning_facts`): the efforts Faculty knows, in its order.
    Codex's "ultra" is not one of them and is never offered (grading.md "CHATGPT SUBSCRIPTION")."""
    efforts = [effort for effort in config.EFFORTS if effort in efforts]
    default = default if default in efforts else "medium" if "medium" in efforts else efforts[len(efforts) // 2] if efforts else ""
    return {"supported": True, "mandatory": True, "efforts": efforts, "default_effort": default}


def subscription_listing(provider):
    """The models a subscription's installed program offers now (`agent_sdk.listed_models`, `codex_sdk.listed_models`,
    `antigravity.listed_models`), as model IDs a connection of that kind may name, each with its reasoning facts and whether it
    reads images."""
    kind, program, plan = SUBSCRIPTION_PROGRAMS[provider]
    from .providers import agent_sdk, antigravity, codex_sdk
    module = {"codex_sdk": codex_sdk, "antigravity": antigravity}.get(kind, agent_sdk)
    built_in = [{"id": m[0], "name": m[1], "description": "", "vision": not m[0].startswith(antigravity.TEXT_ONLY) if kind == "antigravity" else True,
                 "reasoning_options": dict(agent_sdk.FACTS, efforts=list(agent_sdk.FACTS["efforts"])) if kind == "agent_sdk" else subscription_facts(m[2], m[3])}
                for m in module.MODELS]
    try:
        rows = module.listed_models()
    except ProviderError as error:
        return {"provider": provider, "models": built_in, "checked_at": None, "stale": True,
                "notice": f"{error} Showing Faculty's built-in list of your {plan} plan's models."}
    pattern, models = config.SUBSCRIPTION_MODELS[kind][0], {}
    for row in rows:
        model = row.get("id")
        if isinstance(model, str) and re.fullmatch(pattern, model) and model not in models:
            models[model] = {"id": model, "name": str(row.get("name") or model)[:120], "description": str(row.get("description") or "")[:300],
                             "vision": row.get("vision") is not False,
                             "reasoning_options": subscription_facts([e for e in row.get("efforts") or [] if isinstance(e, str)], row.get("default_effort"))}
    if not models:
        return {"provider": provider, "models": built_in, "checked_at": None, "stale": True,
                "notice": f"{program} named no models Faculty can use. Showing Faculty's built-in list of your {plan} plan's models."}
    return {"provider": provider, "models": list(models.values()), "checked_at": timestamp(), "stale": False, "notice": ""}


class ModelCatalog:
    def __init__(self, provider="openrouter"):
        self.provider, self.catalogues = provider, {}
        self.hosts_cache, self.hosts_lock = OrderedDict(), threading.Lock()
        self.catalogue_lock = threading.Lock()
        self.lock = threading.Lock()
        self.models, self.checked_at = [], None
        self.loaded = self.failed = None

    def listing(self, refresh=False, provider="openrouter"):
        if provider in SUBSCRIPTION_PROGRAMS:
            return subscription_listing(provider)  # Asked anew every time, so `refresh` changes nothing.
        source = config.CATALOG_PROVIDERS.get(provider)
        if source is None:
            raise ValueError("Choose OpenRouter or Vercel AI Gateway.")
        if provider != self.provider:
            with self.catalogue_lock:
                catalogue = self.catalogues.setdefault(provider, ModelCatalog(provider))
            return catalogue.listing(refresh=refresh, provider=provider)
        requested = time.monotonic()
        with self.lock:
            now = time.monotonic()
            fresh = self.loaded is not None and now - self.loaded < TTL
            coalesced = self.loaded is not None and self.loaded >= requested
            backed_off = self.failed is not None and now - self.failed < FAILURE_BACKOFF
            if not backed_off and not coalesced and (refresh or not fresh or self.failed is not None):
                try:
                    models = normalize(request_json(source["base_url"] + "/models", timeout=8, limit=16 * 1024**2), provider)
                except (ProviderError, ValueError, TypeError):
                    self.failed = time.monotonic()
                else:
                    self.models, self.checked_at = models, timestamp()
                    self.loaded, self.failed = time.monotonic(), None
            stale = self.failed is not None or self.loaded is None or time.monotonic() - self.loaded >= TTL
            return {"provider": provider, "models": self.models, "checked_at": self.checked_at, "stale": stale,
                    "notice": (f"{source['label']} could not be reached. Showing the last downloaded catalogue." if self.models else
                               f"{source['label']} could not be reached. Your saved connections are still available. Try again shortly.") if stale else ""}

    def hosts(self, provider, model):
        """The hosts serving `model` through one gateway, read on demand from its public listing and kept ten minutes. Public
        metadata only: no key is sent, nothing is saved, and a connection's host changes only when the instructor saves one."""
        source = config.CATALOG_PROVIDERS.get(provider)
        if source is None:
            raise ValueError("Choose OpenRouter or Vercel AI Gateway.")
        if not isinstance(model, str) or not MODEL_ID.fullmatch(model) or ".." in model:
            raise ValueError("Choose a catalogue model ID such as xiaomi/mimo-v2.6-pro.")
        key = (provider, model)
        with self.hosts_lock:
            found = self.hosts_cache.get(key)
            if found and time.monotonic() - found[0] < HOSTS_TTL:
                self.hosts_cache.move_to_end(key)
                return found[1]
        try:
            hosts = normalize_hosts(request_json(f"{source['base_url']}/models/{model}/endpoints", timeout=8, limit=4 * 1024**2), provider)
        except (ProviderError, ValueError, TypeError):
            return {"provider": provider, "model": model, "hosts": [], "any_host_limit": None, "checked_at": None,
                    "notice": f"{source['label']} did not list the hosts of {model}. Try again shortly, or keep letting the gateway choose."}
        result = {"provider": provider, "model": model, "hosts": hosts, "any_host_limit": any_host_room(hosts), "checked_at": timestamp(), "notice": ""}
        with self.hosts_lock:
            self.hosts_cache[key] = (time.monotonic(), result)
            self.hosts_cache.move_to_end(key)
            while len(self.hosts_cache) > HOSTS_MAX:
                self.hosts_cache.popitem(last=False)
        return result
