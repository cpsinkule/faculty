"""Gemini Decision 1 through OpenRouter (Chris, 2026-11-11: "google released a new decisions model ... available through
openrouter. the interesting part is that it has more modalities than Jev which only has text and image. Run a few experiments
calling this decision model through open router to see how it performs on tasks we already use Jev/DecisionAPI for"): one
bounded request, no redirect, proxy or automatic retry.

Like Microsoft-Decision-1 and Liquid d1-ultra it answers on OpenRouter's System One surface, which takes Jev's own body and
answers in Jev's shape, so nothing is translated (`systemone.Host`); OpenRouter passes the request to Google AI Studio. Beside
text and pictures it reads recordings, clips and PDFs, each a part of the state (`Host._part`). What it reads and its limits
are measured by the probe (`probe --modes gemini`).
"""
from . import systemone
from .decisions_client import DecisionError  # noqa: F401 - what a caller catches

MODEL = 'google/gemini-decision-1'
URL = 'https://openrouter.ai/api/v1/systemone'
TIMEOUT = 120  # a scanned paper as one PDF is slower than a crop
MAX_QUESTIONS = 0  # 73 questions answered in one request (probe 2026-11-11); Google's guide says up to 1,000
MAX_IMAGES = 16
MEGABYTE = 1024 * 1024


def open_request(request):
    return systemone.open_request(request, TIMEOUT)


def host(detail=''):
    return systemone.Host('Gemini Decision 1', URL, MODEL, 'OPENROUTER_API_KEY', lambda request: open_request(request),
                          max_questions=MAX_QUESTIONS, max_images=MAX_IMAGES, state_parts=True, documents=True, detail=detail,
                          max_image=20 * MEGABYTE, max_images_total=40 * MEGABYTE, max_request=60 * MEGABYTE)


HOST = host()
# The same model told how closely to look at every picture (OpenRouter's `detail`, which it passes on as Google's resolution).
DETAIL = {'low': host('low'), 'high': host('high')}
available, request_body, normalize, evaluate = HOST.available, HOST.request_body, HOST.normalize, HOST.evaluate
