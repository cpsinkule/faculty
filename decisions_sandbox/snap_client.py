"""SubQ Snap (Subquadratic, public preview; docs.subq.ai read 2026-10-13): one bounded request, no redirect, proxy or automatic retry.

Snap takes Jev's own body (`state`, `questions` of noul, choice and score) at its own `/v1/systemone`, so nothing is translated
(`systemone.Host`). What it adds is `images` (at most four, inline) and a 384K-token window. It refuses a request of more than
16 questions (measured 2026-10-13: 16 answered, 17 and up HTTP 422), and Faculty's checks ask up to 73, so a longer one goes
as several requests over the same state.
"""
from . import systemone
from .decisions_client import DecisionError  # noqa: F401 - what a caller catches

MODEL = 'subq-snap-preview'
URL = 'https://api.subq.ai/v1/systemone'
TIMEOUT = 120  # a 384K-token request is read whole; the guide promises about a second per 100K tokens
MAX_IMAGES = 4
MAX_QUESTIONS = 16
NoRedirect = systemone.NoRedirect


def open_request(request):
    return systemone.open_request(request, TIMEOUT)


HOST = systemone.Host('Snap', URL, MODEL, 'SUBQ_API_KEY', lambda request: open_request(request),
                      max_questions=MAX_QUESTIONS, max_images=MAX_IMAGES)
available, request_body, normalize, evaluate = HOST.available, HOST.request_body, HOST.normalize, HOST.evaluate
