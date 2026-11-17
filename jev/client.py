"""JEV: a bounded, non-generative API with no tools, redirect or automatic retry."""
import math
import os
import json
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MODEL = 'jev-1.13.0'
# JEV PICTURES (requirement, 2026-10-15): checks that look at scanned pages ask Jev 1.14, which reads images and answers Box
# questions; every text check keeps MODEL.
IMAGE_MODEL = 'jev-1.14.0'
URL = 'https://api.typesafe.ai/v1/systemone'
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 4 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def open_request(request, timeout=15):
    return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)


def prepare_image(content):
    """PNG bytes Jev takes (docs.typesafe.ai/images "Limits", read 2026-10-15): each side at most 1568 pixels, 78,400 to 2,458,624
    pixels in all. Measured that day, a side under about 280 pixels is refused although the guide says 256, so a short side is padded
    with white to 280. A larger picture is scaled down whole, a smaller one padded and never stretched; grayscale, as scans are read."""
    import io
    from PIL import Image
    image = Image.open(io.BytesIO(content))
    image.load()
    image = image.convert('L')
    width, height = image.size
    scale = min(1.0, 1568 / max(width, height), math.sqrt(2_458_624 / (width * height)))
    if scale < 1:
        image = image.resize((max(1, min(1568, round(width * scale))), max(1, min(1568, round(height * scale)))), Image.LANCZOS)
    width, height = image.size
    padded = (max(width, 280), max(height, 280))
    if padded[0] * padded[1] < 78_400:
        padded = (padded[0], max(padded[1], math.ceil(78_400 / padded[0])))
    if padded != (width, height):
        canvas = Image.new('L', padded, 255)
        canvas.paste(image, (0, 0))
        image = canvas
    out = io.BytesIO()
    image.save(out, format='PNG', optimize=True)
    return out.getvalue()


def image_body(state, questions, images, model):
    """Jev's body for a check over pictures (docs.typesafe.ai/images, read 2026-10-15): each picture listed once in `assets` under a
    fresh reference that the state names ("image", or "image 1", "image 2", ...), beside the state's own words."""
    import base64
    import uuid
    if not 1 <= len(images) <= MAX_IMAGES or any(not isinstance(p, bytes) or not 0 < len(p) <= MAX_IMAGE_BYTES for p in images):
        raise DecisionError('These pictures exceed the check limits; nothing was sent.')
    refs = [str(uuid.uuid4()) for _ in images]
    named = {'image' if len(refs) == 1 else f'image {i}': ref for i, ref in enumerate(refs, 1)}
    return {'model': model, 'state': {'description': state, **named}, 'questions': questions,
            'assets': {ref: {'type': 'image', 'format': 'png', 'data_base64': base64.b64encode(picture).decode()}
                       for ref, picture in zip(refs, images)}}


class DecisionError(ValueError):
    """A check that gave no answer. `uncertain`: nobody knows whether the host decided it. JEV HOST FAILURES (2026-10-09): `host`, the
    failure is the provider's (an outage, a rejected key, a changed model), not this input's; `refused`, the host refused service
    before any answer: a host-level error status (`host_status`) or a connection never made, so nothing was decided. Neither flag
    carries host text."""
    def __init__(self, message, uncertain=False, host=False, refused=False):
        super().__init__(message)
        self.uncertain, self.host, self.refused = uncertain, host or refused, refused


# JEV HOST FAILURES: statuses that say the host, not this request's content, failed (credentials, rate, outage, a missing endpoint
# or model). Any other status (400, 413, 422) is this input's own failure.
HOST_STATUSES = frozenset({401, 403, 404, 408, 409, 429})


def host_status(code):
    return code in HOST_STATUSES or 500 <= code <= 599


def connection_failure(error, name):
    """The DecisionError for a transport failure. A connection the host refused, a name that did not resolve, or a certificate refused
    in the handshake sent nothing (as grading's `_exchange_once` reads them): a host refusal. Anything else (a timeout, a reset, a
    closed connection) may have been decided: uncertain, never resent."""
    reason = getattr(error, 'reason', error)
    if isinstance(reason, (ConnectionRefusedError, socket.gaierror, ssl.SSLCertVerificationError)):
        return DecisionError(f'{name} could not be reached; nothing was sent.', refused=True)
    return DecisionError(f'The {name} request outcome is uncertain; it was not resent.', uncertain=True, host=True)


def available():
    return os.environ.get('FACULTY_JEV_ENABLED', '1') != '0' and bool(os.environ.get('TYPESAFE_API_KEY'))


def probability(value):
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise DecisionError('Jev returned an invalid probability.')
    return float(value)


def validate(value, questions, model=MODEL):
    """Jev's answer, or another provider's in Jev's shape (`openai_decisions`), held to JEV VALIDATION's bounds."""
    if not isinstance(value, dict) or value.get('model') != model:
        raise DecisionError('Jev did not return the pinned model.', host=isinstance(value, dict))
    answers = value.get('answers')
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise DecisionError('Jev returned incomplete decision answers.')
    cleaned = {}
    for name, question in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get('type') != question['type']:
            raise DecisionError('Jev returned a different question type.')
        if question['type'] == 'noul':
            cleaned[name] = {'type': 'noul', 'noul': probability(answer.get('noul'))}
        elif question['type'] == 'choice':
            probs = answer.get('probabilities')
            options = question['criteria']
            if not isinstance(probs, dict) or set(probs) != set(options) or not isinstance(answer.get('choice'), str) or answer['choice'] not in options:
                raise DecisionError('Jev returned an unknown choice.')
            probs = {k: probability(p) for k, p in probs.items()}
            # JEV: the API rounds probabilities and confidence to two decimals (measured 2026-10-03 on real answers: sums
            # off by up to .010 with 55 options, confidence off by up to .020). Each rounding moves a value by at most .005:
            # a sum drifts with the option count, a rounded top probability moves the expected confidence by up to
            # .005/(1-1/n) <= .01, plus .005 for the confidence's own rounding; a near tie may swap by .01.
            if abs(sum(probs.values()) - 1) > .005 + .005 * math.sqrt(len(probs)) or probs[answer['choice']] < max(probs.values()) - .01:
                raise DecisionError('Jev returned an inconsistent choice distribution.')
            confidence = probability(answer.get('confidence'))
            expected = 1.0 if len(probs) == 1 else (max(probs.values()) - 1 / len(probs)) / (1 - 1 / len(probs))  # one option: certain
            if abs(confidence - expected) > .03:  # measured up to .020 (24 options, top shown as 1.00 with confidence .98)
                raise DecisionError('Jev returned inconsistent confidence.')
            cleaned[name] = {'type': 'choice', 'choice': answer['choice'], 'probabilities': probs, 'confidence': confidence}
        elif question['type'] == 'score':
            # JEV URGENCY: levels "0".."n-1"; the score is the probability-weighted level, each value rounded to two decimals.
            levels = [str(i) for i in range(len(question['criteria']))]
            probs = answer.get('probabilities')
            if not isinstance(probs, dict) or set(probs) != set(levels):
                raise DecisionError('Jev returned an unknown score level.')
            probs = {k: probability(probs[k]) for k in levels}
            if abs(sum(probs.values()) - 1) > .005 + .005 * math.sqrt(len(probs)):
                raise DecisionError('Jev returned an inconsistent score distribution.')
            score = answer.get('score')
            if type(score) not in (int, float) or not 0 <= score <= len(levels) - 1 or not math.isfinite(score):
                raise DecisionError('Jev returned an invalid score.')
            if abs(score - sum(i * p for i, p in enumerate(probs.values()))) > .005 + .005 * len(levels) * (len(levels) - 1) / 2:
                raise DecisionError('Jev returned an inconsistent score.')
            cleaned[name] = {'type': 'score', 'score': float(score), 'probabilities': probs,
                             'confidence': probability(answer.get('confidence'))}
        elif question['type'] == 'box':
            # JEV PICTURES: one box, its edges fractions of the picture from its top-left corner (docs.typesafe.ai/primitives/box,
            # read 2026-10-15). A Box answer always holds a box, found or not; its confidence says which.
            box = answer.get('box')
            if not isinstance(box, dict) or set(box) != {'top', 'bottom', 'left', 'right'}:
                raise DecisionError('Jev returned an invalid box.')
            edges = {k: probability(box[k]) for k in ('top', 'bottom', 'left', 'right')}
            if not (edges['left'] < edges['right'] and edges['top'] < edges['bottom']):
                raise DecisionError('Jev returned an invalid box.')
            cleaned[name] = {'type': 'box', 'box': edges, 'confidence': probability(answer.get('confidence'))}
        else:
            raise DecisionError('This integration does not use that question type.')
    usage = value.get('usage')
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('input_tokens', 'output_tokens')):
        raise DecisionError('Jev returned invalid token usage.')
    return {'model': model, 'answers': cleaned, 'usage': {k: usage[k] for k in ('input_tokens', 'output_tokens')}}


def evaluate(state, questions, images=()):
    """`images`: PNG pictures the check looks at (JEV PICTURES): asked of IMAGE_MODEL with TypeSafe's opt-in images header; a check
    without pictures is the text check it always was."""
    if not available():
        raise DecisionError('Jev is not configured or is disabled.')
    model = IMAGE_MODEL if images else MODEL
    body = image_body(state, questions, list(images), model) if images else {'model': MODEL, 'state': state, 'questions': questions}
    headers = {'Authorization': 'Bearer ' + os.environ['TYPESAFE_API_KEY'], 'Content-Type': 'application/json',
               **({'TypeSafe-Danger-Zone-Images': 'true'} if images else {})}
    request = Request(URL, data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method='POST')
    try:
        with (open_request(request, 30) if images else open_request(request)) as response:
            if response.status != 200:
                # Never expose a host response: it may echo the credential or private state.
                raise DecisionError(f'Jev could not complete this check (HTTP {response.status}).',
                                    refused=host_status(response.status))
            body = response.read(200001)
            if len(body) > 200000:
                raise DecisionError('Jev returned an oversized answer.')
            try:
                return validate(json.loads(body), questions, model)
            except (TypeError, ValueError) as error:
                if isinstance(error, DecisionError):
                    raise
                raise DecisionError('Jev returned an unreadable answer.') from None
    except HTTPError as error:
        code = error.code
        error.close()
        # An error status is the host's answer that it decided nothing; only a host-level status pauses the work that asks.
        raise DecisionError(f'Jev could not complete this check (HTTP {code}).', refused=host_status(code)) from None
    except (URLError, TimeoutError, OSError) as error:
        raise connection_failure(error, 'Jev') from None
