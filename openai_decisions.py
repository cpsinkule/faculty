"""OPENAI DECISIONS (requirement, 2026-10-08: student triage and the multiple-choice grader on OpenAI's Decisions API, after the
sandbox of 2026-10-07). A bounded, non-generative API with no tools, redirect, proxy or automatic retry, like Jev's
(`jev/client.py`), at the pinned `gpt-6-luna`. Jev's questions (`jev/service.py` noul, choice, score) are translated here and
the answers come back in Jev's shape, held to Jev's own validation bounds (JEV VALIDATION; all 815 sandbox answers fit them),
so a site reads both providers the same way. A choice's confidence is Jev's formula over the returned probabilities, so a
gate keeps its meaning; the API's own value is not used. Nothing here keeps a request or a host response."""
import base64
import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from jev import client as jev_client

MODEL = 'gpt-6-luna'
URL = 'https://api.openai.com/v1/decisions'
TIMEOUT = 15
MAX_TEXT_REQUEST = 110000  # bytes of a text check, as JevService.ask allows
MAX_RESPONSE = 200000


class DecisionError(jev_client.DecisionError):
    """Failed (confirmed) or, with `uncertain`, an outcome nobody knows; never resent automatically."""


NoRedirect = jev_client.NoRedirect  # Jev's handler: a credentialed request is never redirected


def open_request(request):
    return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=TIMEOUT)


def available():
    return os.environ.get('FACULTY_DECISIONS_ENABLED', '1') != '0' and bool(os.environ.get('OPENAI_API_KEY'))


def _label(level, index):
    head, sep, _ = str(level).partition(':')
    return head.strip()[:60] if sep and head.strip() else f'Level {index}'


def question(name, asked):
    """One Jev question as a Decisions question named `name`: a noul's meanings join its instructions; a score's level label is
    its text before the colon."""
    kind, instructions = asked['type'], asked['instructions']
    if kind == 'noul':
        criteria = asked.get('criteria') or {}
        if criteria:
            instructions += f" Answer yes when: {criteria.get('true', '')} Answer no when: {criteria.get('false', '')}"
        return {'type': 'predicate', 'name': name, 'instructions': instructions}
    if kind == 'choice':
        return {'type': 'choice', 'name': name, 'instructions': instructions,
                'choices': [{'value': value, 'description': description or value} for value, description in asked['criteria'].items()]}
    if kind == 'score':
        return {'type': 'score', 'name': name, 'instructions': instructions,
                'levels': [{'label': _label(level, i), 'description': str(level)} for i, level in enumerate(asked['criteria'])]}
    raise DecisionError('This integration does not use that question type.')


def request_body(state, questions, images=()):
    """The exact JSON body: the state as compact JSON text (a string as it is), then any images as inline PNG data URLs."""
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    given = text
    if images:
        given = [{'role': 'user', 'content': [{'type': 'input_text', 'text': text}] + [
            {'type': 'input_image', 'image_url': 'data:image/png;base64,' + base64.b64encode(png).decode()} for png in images]}]
    return {'model': MODEL, 'input': given, 'questions': [question(name, asked) for name, asked in questions.items()]}


def _probability(value):
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise DecisionError('Decisions returned an invalid probability.')
    return float(value)


def normalize(value, questions):
    """{model, answers, usage} in Jev's shape, or DecisionError: the pinned model, every question answered once in its own type
    (a refusal of any fails the whole answer, as an incomplete Jev answer does), every listed option known, then Jev's bounds."""
    if not isinstance(value, dict) or value.get('model') != MODEL:
        # JEV HOST FAILURES: an answer naming another model is the host's change, not this input's; every later ask fails alike.
        raise DecisionError('Decisions did not return the pinned model.', host=isinstance(value, dict))
    listed = value.get('answers')
    if not isinstance(listed, list):
        raise DecisionError('Decisions returned no answers.')
    raw = {}
    for answer in listed:
        if not isinstance(answer, dict) or not isinstance(answer.get('name'), str) or answer['name'] in raw:
            raise DecisionError('Decisions returned an unnamed or repeated answer.')
        raw[answer['name']] = answer
    if any(answer.get('type') == 'refusal' for answer in raw.values()):
        raise DecisionError('Decisions refused a question.')
    if set(raw) != set(questions):
        raise DecisionError('Decisions returned incomplete decision answers.')
    answers = {}
    for name, asked in questions.items():
        answer = raw[name]
        expected = {'noul': 'predicate', 'choice': 'choice', 'score': 'score'}[asked['type']]
        if answer.get('type') != expected:
            raise DecisionError('Decisions returned a different question type.')
        if expected == 'predicate':
            answers[name] = {'type': 'noul', 'noul': _probability(answer.get('probability'))}
            continue
        distribution = answer.get('probabilities')
        if not isinstance(distribution, list):
            raise DecisionError('Decisions returned no distribution.')
        if expected == 'choice':
            options, probabilities = list(asked['criteria']), {}
            for entry in distribution:
                if not isinstance(entry, dict) or entry.get('value') not in options or entry['value'] in probabilities:
                    raise DecisionError('Decisions returned an unknown choice.')
                probabilities[entry['value']] = _probability(entry.get('probability'))
            if answer.get('choice') not in options:
                raise DecisionError('Decisions chose an unknown option.')
            probabilities = {option: probabilities.get(option, 0.0) for option in options}  # an unlisted option is 0
            n = len(options)
            confidence = 1.0 if n == 1 else (max(probabilities.values()) - 1 / n) / (1 - 1 / n)
            answers[name] = {'type': 'choice', 'choice': answer['choice'], 'probabilities': probabilities,
                             'confidence': max(0.0, min(1.0, confidence))}
        else:
            labels = [_label(level, i) for i, level in enumerate(asked['criteria'])]
            probabilities = {}
            if any(not isinstance(entry, dict) or type(entry.get('value', 0)) not in (int, float)
                   or not 0 <= entry.get('value', 0) <= len(labels) - 1 for entry in distribution):
                raise DecisionError('Decisions returned an unreadable score level.')
            for position, entry in enumerate(sorted(distribution, key=lambda e: e.get('value', 0))):  # every entry is a dict (the check above)
                index = labels.index(entry['label']) if entry.get('label') in labels else position
                if index >= len(labels) or str(index) in probabilities:
                    raise DecisionError('Decisions returned an unknown score level.')
                probabilities[str(index)] = _probability(entry.get('probability'))
            probabilities = {str(i): probabilities.get(str(i), 0.0) for i in range(len(labels))}
            confidence = answer.get('confidence')
            answers[name] = {'type': 'score', 'score': sum(i * p for i, p in enumerate(probabilities.values())),
                             'probabilities': probabilities,
                             'confidence': _probability(confidence) if confidence is not None else max(probabilities.values())}
    usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
    shaped = {'model': MODEL, 'answers': answers,
              'usage': {'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens', 0)}}
    try:
        return jev_client.validate(shaped, questions, model=MODEL)
    except jev_client.DecisionError as error:
        raise DecisionError(str(error).replace('Jev', 'Decisions'), uncertain=error.uncertain, host=error.host) from None


def evaluate(state, questions):
    """A text check: {model, answers, usage} in Jev's shape. Raises DecisionError, `uncertain` when the outcome is unknown."""
    if not available():
        raise DecisionError('OpenAI Decisions is not configured or is disabled.')
    body = json.dumps(request_body(state, questions), ensure_ascii=False).encode()
    if len(body) > MAX_TEXT_REQUEST:
        raise DecisionError('This source exceeds the check limit; no verification was claimed.')
    request = Request(URL, data=body, headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'],
                                               'Content-Type': 'application/json'}, method='POST')
    try:
        with open_request(request) as response:
            if response.status != 200:
                raise DecisionError(f'Decisions could not complete this check (HTTP {response.status}).',
                                    refused=jev_client.host_status(response.status))
            payload = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        code = error.code
        error.close()  # never kept: a host response may echo the request
        # JEV HOST FAILURES: 401/403 (key), 429 (rate), 5xx (outage) and the like decided nothing and say so without host text.
        raise DecisionError(f'Decisions could not complete this check (HTTP {code}).', refused=jev_client.host_status(code)) from None
    except (URLError, TimeoutError, OSError) as error:
        failure = jev_client.connection_failure(error, 'Decisions')
        raise DecisionError(str(failure), uncertain=failure.uncertain, host=failure.host, refused=failure.refused) from None
    if len(payload) > MAX_RESPONSE:
        raise DecisionError('Decisions returned an oversized answer.')
    try:
        value = json.loads(payload)
    except ValueError:
        raise DecisionError('Decisions returned an unreadable answer.') from None
    return normalize(value, questions)
