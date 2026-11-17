"""OpenAI Decisions (public beta, guide read 2026-10-07): one bounded request, no redirect, proxy or automatic retry.

Jev's question format (jev/service.py `noul`, `choice`, `score`) is translated here, and the answers are normalized back into
Jev's answer shape so Faculty's own site code reads both models the same way. The API's own `confidence` is kept beside the
value Jev's formula gives (`api_confidence`), because the guide does not define it; every rounding gap is recorded, and an answer
outside Jev's validation bounds is kept and marked (`gaps.outside_jev_bounds`) so the report can count it.
"""
import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from jev import client as jev_client

MODEL = 'gpt-6-luna'
URL = 'https://api.openai.com/v1/decisions'
TIMEOUT = 30
MAX_REQUEST = 16 * 1024 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
NoRedirect = jev_client.NoRedirect  # Jev's handler: a credentialed request is never redirected


def open_request(request):
    return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=TIMEOUT)


class DecisionError(ValueError):
    def __init__(self, message, uncertain=False, status=None, gaps=None):
        super().__init__(message)
        self.uncertain, self.status, self.gaps = uncertain, status, gaps or {}


def available():
    return bool(os.environ.get('OPENAI_API_KEY'))


# Since 2026-10-08 the body is the production one (`openai_decisions`, promoted from this file), so a calibration measures exactly
# what the app sends; the normalization below stays the sandbox's own, which records gaps instead of failing on them.
from openai_decisions import _label, request_body  # noqa: E402


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _probability(value, gaps, where):
    if not _finite(value):
        raise DecisionError(f'Decisions returned an invalid probability ({where}).')
    if not 0 <= value <= 1:
        gaps.setdefault('clamped', []).append(where)
        if not -.01 <= value <= 1.01:
            raise DecisionError(f'Decisions returned an out-of-range probability ({where}).')
        value = min(1.0, max(0.0, value))
    return float(value)


def normalize(value, questions):
    """(Jev-shaped answers, raw answers by name, gaps). A refusal of any question fails the whole answer, as an incomplete Jev
    answer does; `gaps` records what the normalization had to reconcile."""
    answers = value.get('answers') if isinstance(value, dict) else None
    if not isinstance(answers, list):
        raise DecisionError('Decisions returned no answers.')
    raw = {}
    for answer in answers:
        if not isinstance(answer, dict) or not isinstance(answer.get('name'), str) or answer['name'] in raw:
            raise DecisionError('Decisions returned an unnamed or repeated answer.')
        raw[answer['name']] = answer
    gaps = {}
    refused = sorted(name for name, answer in raw.items() if answer.get('type') == 'refusal')
    if refused:
        raise DecisionError('Decisions refused: ' + ', '.join(refused), gaps={'refused': refused})
    if set(raw) != set(questions):
        raise DecisionError('Decisions returned incomplete decision answers.', gaps={'missing': sorted(set(questions) - set(raw)),
                                                                                    'extra': sorted(set(raw) - set(questions))})
    cleaned = {}
    for name, asked in questions.items():
        answer = raw[name]
        expected = {'noul': 'predicate', 'choice': 'choice', 'score': 'score'}[asked['type']]
        if answer.get('type') != expected:
            raise DecisionError('Decisions returned a different question type.')
        if expected == 'predicate':
            cleaned[name] = {'type': 'noul', 'noul': _probability(answer.get('probability'), gaps, name)}
            continue
        listed = answer.get('probabilities')
        if not isinstance(listed, list):
            raise DecisionError('Decisions returned no distribution.')
        if expected == 'choice':
            options = list(asked['criteria'])
            probs = {}
            for entry in listed:
                if not isinstance(entry, dict) or entry.get('value') not in options or entry['value'] in probs:
                    raise DecisionError('Decisions returned an unknown choice.')
                probs[entry['value']] = _probability(entry.get('probability'), gaps, name)
            missing = [option for option in options if option not in probs]
            if missing:
                gaps.setdefault('unlisted_options', {})[name] = len(missing)
                probs.update({option: 0.0 for option in missing})
            if answer.get('choice') not in options:
                raise DecisionError('Decisions chose an unknown option.')
            n = len(options)
            formula = 1.0 if n == 1 else (max(probs.values()) - 1 / n) / (1 - 1 / n)
            api = answer.get('confidence')
            api = _probability(api, gaps, name + '.confidence') if api is not None else None
            if api is not None and abs(api - formula) > .03:
                gaps.setdefault('confidence_vs_formula', {})[name] = round(api - formula, 4)
            cleaned[name] = {'type': 'choice', 'choice': answer['choice'], 'probabilities': {o: probs[o] for o in options},
                             'confidence': max(0.0, min(1.0, formula)), 'api_confidence': api}
        else:
            levels = [str(i) for i in range(len(asked['criteria']))]
            labels = [_label(level, i) for i, level in enumerate(asked['criteria'])]
            probs = {}
            for entry in listed:
                if not isinstance(entry, dict) or not _finite(entry.get('value', 0)):
                    raise DecisionError('Decisions returned an unreadable score level.')
            for position, entry in enumerate(sorted(listed, key=lambda e: e.get('value', 0))):
                # By label when it names one of ours, else by position (the base of `value` is not documented).
                index = labels.index(entry['label']) if entry.get('label') in labels else position
                if index >= len(levels) or levels[index] in probs:
                    raise DecisionError('Decisions returned an unknown score level.')
                probs[levels[index]] = _probability(entry.get('probability'), gaps, name)
            probs.update({level: 0.0 for level in levels if level not in probs})
            weighted = sum(i * probs[level] for i, level in enumerate(levels))
            score = answer.get('score')
            if not _finite(score):
                raise DecisionError('Decisions returned an invalid score.')
            if abs(score - weighted) > .02:
                gaps.setdefault('score_vs_weighted', {})[name] = round(score - weighted, 4)
            api = answer.get('confidence')
            cleaned[name] = {'type': 'score', 'score': float(weighted), 'probabilities': probs,
                             'confidence': _probability(api, gaps, name + '.confidence') if api is not None else max(probs.values()),
                             'api_score': float(score)}
    return cleaned, raw, gaps


def check_bounds(cleaned, questions):
    """Jev's VALIDATION bounds (jev/client.py `validate`) applied to the normalized answers: a failure is recorded, not hidden."""
    shaped = {'model': jev_client.MODEL, 'usage': {'input_tokens': 0, 'output_tokens': 0},
              'answers': {name: {k: v for k, v in answer.items() if k not in ('api_confidence', 'api_score')}
                          for name, answer in cleaned.items()}}
    jev_client.validate(shaped, questions)


def evaluate(state, questions, images=()):
    """{model, answers (Jev shape), raw, usage, gaps}. Raises DecisionError; `uncertain` when the outcome is unknown."""
    if not available():
        raise DecisionError('OPENAI_API_KEY is not set for the sandbox.')
    body = json.dumps(request_body(state, questions, images), ensure_ascii=False).encode()
    if len(body) > MAX_REQUEST:
        raise DecisionError('This request exceeds the sandbox size limit; it was not sent.')
    request = Request(URL, data=body, headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'],
                                               'Content-Type': 'application/json'}, method='POST')
    try:
        with open_request(request) as response:
            if response.status != 200:
                raise DecisionError(f'Decisions could not complete this check (HTTP {response.status}).', status=response.status)
            payload = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        code = error.code
        try:
            detail = error.read(4000).decode('utf-8', 'replace')
        except Exception:  # noqa: BLE001 - the body is only a hint
            detail = ''
        error.close()
        # Only the API's error type/code is kept: a host response may echo request text.
        kind = ''
        try:
            parsed = json.loads(detail).get('error') or {}
            kind = ' '.join(str(parsed.get(k) or '') for k in ('type', 'code')).strip()
        except (ValueError, AttributeError):
            pass
        raise DecisionError(f'Decisions could not complete this check (HTTP {code}{": " + kind if kind else ""}).', status=code) from None
    except (URLError, TimeoutError, OSError):
        raise DecisionError('The Decisions request outcome is uncertain; it was not resent.', uncertain=True) from None
    if len(payload) > MAX_RESPONSE:
        raise DecisionError('Decisions returned an oversized answer.')
    try:
        value = json.loads(payload)
    except ValueError:
        raise DecisionError('Decisions returned an unreadable answer.') from None
    cleaned, raw, gaps = normalize(value, questions)
    try:
        check_bounds(cleaned, questions)
    except jev_client.DecisionError as error:
        # Kept and counted: the report says how many answers Jev's validator (as written for Jev's rounding) would refuse.
        gaps['outside_jev_bounds'] = str(error)
    usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
    return {'model': value.get('model') or MODEL, 'answers': cleaned, 'raw': raw, 'gaps': gaps,
            'usage': {k: usage[k] for k in ('input_tokens', 'output_tokens', 'total_tokens') if type(usage.get(k)) is int},
            'response_keys': sorted(value)}
