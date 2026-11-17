"""GEMINI DECISIONS (requirement, 2026-11-12). Google's Decisions API at the pinned `gemini-decision-1`:
a bounded, non-generative API with no tools, redirect, proxy or automatic retry, like Jev's (`jev/client.py`) and OpenAI's
(`openai_decisions.py`). Jev's questions (`jev/service.py` noul, choice, score) are translated here and the answers come back
in Jev's shape, held to Jev's own validation bounds (JEV VALIDATION), so a site reads every provider the same way. A choice's
confidence is Jev's formula over the returned probabilities, so a gate keeps its meaning; the API's own value (the top
probability) is not used. Nothing here keeps a request or a host response.

What differs from Jev's body (aistudio.google.com/docs/decisions-api, read 2026-11-12): the state is `input`, text then one
`image` block for each picture (raw base64 and its type); a yes/no question's meanings are `true_description` and
`false_description`; an option with no description takes ""; a question's key holds letters, digits, "_" and "-" only, so each
goes under a plain key and comes back under its own name. The answer names no model, so the pinned one is the one asked.
The key is `GEMINI_API_KEY`, sent as `x-goog-api-key` and reserved for this: no chat or grading connection may name it."""
import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from jev import client as jev_client

MODEL = 'gemini-decision-1'
NAME = 'Gemini Decisions'
URL = 'https://generativelanguage.googleapis.com/v1beta/decisions'
TIMEOUT = 30
PICTURES = True  # `JevService.ask` sends a check's pictures here, where Jev's own client would otherwise be asked
MAX_REQUEST = 40 * 1024 * 1024  # bytes; eight pictures of at most 4 MiB each, base64
MAX_RESPONSE = 400000


class DecisionError(jev_client.DecisionError):
    """Failed (confirmed) or, with `uncertain`, an outcome nobody knows; never resent automatically."""


NoRedirect = jev_client.NoRedirect  # Jev's handler: a credentialed request is never redirected


def open_request(request):
    return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=TIMEOUT)


def available():
    return os.environ.get('FACULTY_GEMINI_DECISIONS_ENABLED', '1') != '0' and bool(os.environ.get('GEMINI_API_KEY'))


def _keys(questions):
    return {name: f'q{position}' for position, name in enumerate(questions)}


def question(asked):
    """One Jev question as Google takes it."""
    kind, sent = asked['type'], {'type': asked['type'], 'instructions': asked['instructions']}
    criteria = asked.get('criteria')
    if kind == 'noul':
        described = {f'{side}_description': criteria[side] for side in ('true', 'false') if (criteria or {}).get(side)}
        return {**sent, **({'criteria': described} if described else {})}
    if kind == 'choice':
        return {**sent, 'criteria': {option: described or '' for option, described in criteria.items()}}
    if kind == 'score':
        return {**sent, 'criteria': [str(level) for level in criteria]}
    raise DecisionError('This integration does not use that question type.')


def request_body(state, questions, images=()):
    """The exact JSON body: the state as compact JSON text (a string as it is), then any pictures as inline PNG blocks."""
    if len(images) > jev_client.MAX_IMAGES or any(not isinstance(p, bytes) or not 0 < len(p) <= jev_client.MAX_IMAGE_BYTES for p in images):
        raise DecisionError('These pictures exceed the check limits; nothing was sent.')
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    given = text
    if images:
        given = [{'type': 'text', 'text': text}] + [{'type': 'image', 'data': base64.b64encode(png).decode(), 'mime_type': 'image/png'}
                                                    for png in images]
    keys = _keys(questions)
    return {'model': MODEL, 'input': given, 'questions': {keys[name]: question(asked) for name, asked in questions.items()}}


def normalize(value, questions):
    """{model, answers, usage} in Jev's shape, or DecisionError: every question answered once in its own type, a choice's
    confidence Jev's formula over its probabilities, then Jev's bounds."""
    listed = value.get('answers') if isinstance(value, dict) else None
    if not isinstance(listed, dict):
        raise DecisionError('Gemini Decisions returned no answers.')
    keys = _keys(questions)
    if set(listed) != set(keys.values()):
        raise DecisionError('Gemini Decisions returned incomplete decision answers.')
    answers = {}
    for name, asked in questions.items():
        answer = listed[keys[name]]
        if not isinstance(answer, dict) or answer.get('type') != asked['type']:
            raise DecisionError('Gemini Decisions returned a different question type.')
        if asked['type'] == 'choice':
            probabilities = answer.get('probabilities')
            if not isinstance(probabilities, dict) or any(type(p) not in (int, float) for p in probabilities.values()):
                raise DecisionError('Gemini Decisions returned no distribution.')
            n = len(asked['criteria'])
            top = max(probabilities.values(), default=0)
            answer = {**answer, 'confidence': 1.0 if n == 1 else max(0.0, min(1.0, (top - 1 / n) / (1 - 1 / n)))}
        answers[name] = answer
    usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
    shaped = {'model': MODEL, 'answers': answers,
              'usage': {'input_tokens': usage.get('total_input_tokens'), 'output_tokens': usage.get('total_output_tokens', 0)}}
    try:
        return jev_client.validate(shaped, questions, model=MODEL)
    except jev_client.DecisionError as error:
        raise DecisionError(str(error).replace('Jev', NAME), uncertain=error.uncertain, host=error.host) from None


def evaluate(state, questions, images=()):
    """{model, answers, usage} in Jev's shape, of a text check or one over `images` (PNG pictures). Raises DecisionError,
    `uncertain` when the outcome is unknown."""
    if not available():
        raise DecisionError('Gemini Decisions is not configured or is disabled.')
    body = json.dumps(request_body(state, questions, images), ensure_ascii=False).encode()
    if len(body) > MAX_REQUEST:
        raise DecisionError('This source exceeds the check limit; no verification was claimed.')
    request = Request(URL, data=body, headers={'x-goog-api-key': os.environ['GEMINI_API_KEY'], 'Content-Type': 'application/json'},
                      method='POST')
    try:
        with open_request(request) as response:
            if response.status != 200:
                raise DecisionError(f'Gemini Decisions could not complete this check (HTTP {response.status}).',
                                    refused=jev_client.host_status(response.status))
            payload = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        code = error.code
        error.close()  # never kept: a host response may echo the request
        # JEV HOST FAILURES: 401/403 (key), 429 (rate), 5xx (outage) and the like decided nothing and say so without host text.
        raise DecisionError(f'Gemini Decisions could not complete this check (HTTP {code}).', refused=jev_client.host_status(code)) from None
    except (URLError, TimeoutError, OSError) as error:
        failure = jev_client.connection_failure(error, NAME)
        raise DecisionError(str(failure), uncertain=failure.uncertain, host=failure.host, refused=failure.refused) from None
    if len(payload) > MAX_RESPONSE:
        raise DecisionError('Gemini Decisions returned an oversized answer.')
    try:
        value = json.loads(payload)
    except ValueError:
        raise DecisionError('Gemini Decisions returned an unreadable answer.') from None
    return normalize(value, questions)
