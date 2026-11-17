"""Microsoft-Decision-1 through OpenRouter (requirement, 2026-10-13): one bounded request, no redirect, proxy
or automatic retry.

OpenRouter's System One surface (`/api/v1/systemone`; its guide read 2026-10-13) takes Jev's own body and answers in Jev's
shape, so nothing is translated (`systemone.Host`). The model reads text only, in a 32,768-token window. The key is the
OpenRouter key the server's key file already holds; OpenRouter passes the request on to the model's provider.
"""
from . import systemone
from .decisions_client import DecisionError  # noqa: F401 - what a caller catches

MODEL = 'microsoft/microsoft-decision-1'
URL = 'https://openrouter.ai/api/v1/systemone'
TIMEOUT = 30
MAX_QUESTIONS = 0  # no limit enforced; 73 questions answered in one request (README, probe 2026-10-13)


def open_request(request):
    return systemone.open_request(request, TIMEOUT)


HOST = systemone.Host('Microsoft-Decision-1', URL, MODEL, 'OPENROUTER_API_KEY', lambda request: open_request(request),
                      max_questions=MAX_QUESTIONS)
available, request_body, normalize, evaluate = HOST.available, HOST.request_body, HOST.normalize, HOST.evaluate
