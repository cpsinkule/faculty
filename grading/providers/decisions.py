"""OpenAI Decisions behind the grading seam (GRADING MULTIPLE-CHOICE READER, 2026-10-08): the multiple-choice reader's one request
per answer area, recorded and retried like every cloud call (`GradingService.cloud_call`, `frq_cloud_calls`). The request body
and the answer's checks are the shared client's (`openai_decisions`: Jev's questions translated, answers in Jev's shape and
bounds); the transport is grading's own, so a cancelled batch closes the connection and a host error follows the retry policy."""
import os

import openai_decisions

from .base import ProviderError, request_json
from .cloud import CloudGradingProvider


class DecisionsEndpoint(CloudGradingProvider):
    kind = "decisions"

    def __init__(self, endpoint, timeout=180):
        self.endpoint, self.timeout = endpoint, timeout or 60

    def health(self):
        return {"ok": openai_decisions.available(), "detail": "OpenAI Decisions" if openai_decisions.available() else "No OPENAI_API_KEY."}

    def capabilities(self):
        return {"provider": self.kind, "endpoint": self.endpoint["name"], "image_input": True, "schema_constrained_output": True,
                "batch_api": False, "durable_call_ids": False, "max_inflight": self.endpoint["max_inflight"],
                "price_per_million_input": self.endpoint["price_in"], "price_per_million_output": 0}

    def cost(self, usage):
        tokens = usage.get("prompt_tokens")
        return (round(tokens * self.endpoint["price_in"] / 1e6, 8), "Decisions input tokens at $0.10 per million") if type(tokens) is int else (None, "no usage reported")

    def submit(self, request):
        """request: {prompt: "choice", payload: {state, questions}, images: [PNG]}."""
        key = os.environ.get("OPENAI_API_KEY")
        if not key or not openai_decisions.available():
            raise ProviderError("OpenAI Decisions has no key here, or it is switched off; nothing was sent.", confirmed=True, fixable=False)
        payload = request["payload"]
        body = openai_decisions.request_body(payload["state"], payload["questions"], request.get("images") or ())
        value = request_json(openai_decisions.URL, body=body, headers={"Authorization": "Bearer " + key}, timeout=self.timeout,
                             limit=openai_decisions.MAX_RESPONSE)
        try:
            answer = openai_decisions.normalize(value, payload["questions"])
        except openai_decisions.DecisionError as error:
            # A malformed answer is a confirmed failure with its receipt; the lane's bounded rule decides whether it is asked again.
            raise ProviderError(str(error), raw=value, metadata={"provider": self.kind}, confirmed=True) from None
        usage = answer["usage"]
        return {"state": "completed", "result": {"output": answer, "raw": value, "metadata": {
            "provider": self.kind, "endpoint": self.endpoint["name"], "reported_model": value.get("model"), "prompt": request["prompt"],
            "usage": {"prompt_tokens": usage["input_tokens"], "completion_tokens": usage["output_tokens"],
                      "total_tokens": usage["input_tokens"] + usage["output_tokens"]}}}}
