"""Liquid d1-ultra through OpenRouter (Chris, 2026-10-18: "there is a new decision model available through openrouter that I
want to test against Jev. run a comparison using a sample of various tasks"): one bounded request, no redirect, proxy or
automatic retry.

Like Microsoft-Decision-1 (`openrouter_client`), it answers on OpenRouter's System One surface, which takes Jev's own body and
answers in Jev's shape, so nothing is translated (`systemone.Host`). What it reads and its limits are measured by the probe
(`probe --modes liquid`).
"""
from . import systemone
from .decisions_client import DecisionError  # noqa: F401 - what a caller catches

MODEL = 'liquid/d1-ultra'
URL = 'https://openrouter.ai/api/v1/systemone'
TIMEOUT = 60
MAX_QUESTIONS = 0  # 73 questions answered in one request (probe 2026-10-18)
MAX_IMAGES = 8  # images go as parts of the state (a top-level `images` is refused, HTTP 400); Chris: "it has image capabilities just like jev"


def open_request(request):
    return systemone.open_request(request, TIMEOUT)


HOST = systemone.Host('Liquid d1-ultra', URL, MODEL, 'OPENROUTER_API_KEY', lambda request: open_request(request),
                      max_questions=MAX_QUESTIONS, max_images=MAX_IMAGES, state_parts=True)
available, request_body, normalize, evaluate = HOST.available, HOST.request_body, HOST.normalize, HOST.evaluate
