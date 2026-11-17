"""A host that takes Jev's own body (System One: `state`, `questions` of noul, choice and score) and answers in Jev's shape,
asked from the sandbox: one bounded request, no redirect, proxy or automatic retry. SubQ Snap (`snap_client`), Microsoft-Decision-1
(`openrouter_client`), Liquid d1-ultra (`liquid_client`), Gemini Decision 1 (`gemini_client`), and Jev 1.14 and the pinned Jev 1.13
(`jev_preview_client`) are six such hosts, so nothing is translated for any of them: the state goes as Jev gets it. Google's own
Decisions API (`gemini_native_client`) answers in Jev's shape but takes another body, so its host translates the request. What a real answer does differently from Jev's
is reconciled here and recorded (`gaps`), never hidden, and an answer outside Jev's validation bounds is kept and marked
(`gaps.outside_jev_bounds`) so the report can count it.
"""
import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .decisions_client import DecisionError, NoRedirect, _finite, check_bounds

MEDIA = ((b'\x89PNG\r\n\x1a\n', 'image/png'), (b'\xff\xd8\xff', 'image/jpeg'))
# What a host that reads more than pictures (`documents`) is also sent: a PDF, a WAV or MP3 recording, an MP4 clip.
DOCUMENTS = ((b'%PDF-', 'application/pdf'), (b'ID3', 'audio/mp3'), (b'\xff\xfb', 'audio/mp3'), (b'\xff\xf3', 'audio/mp3'))


def open_request(request, timeout):
    return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)


def kind(detail):
    """The host's own error type or code from an error body, never its message: that may echo request text."""
    try:
        parsed = json.loads(detail)
    except ValueError:
        return ''
    if not isinstance(parsed, dict):
        return ''
    inner = next((parsed[name] for name in ('detail', 'error') if isinstance(parsed.get(name), dict)), {})
    return ' '.join(str(inner[name]) for name in ('error_type', 'type', 'code') if inner.get(name))[:80]


class Host:
    """`who`: the model's name in messages. `opener(request)`: how its module opens a request. `max_questions`: the most a
    request takes, when a longer check goes as several (0: no limit known). `max_images`: 0 for a model that reads text only.
    `assets`: images go as Jev's own `assets` map, each named in the state by a fresh reference, instead of an inline list.
    `headers(body, images)`: any extra request headers (Jev's opt-in "danger zone" headers). `prepare(picture)`: the bytes sent
    for an image (Jev takes 256 to 1568 pixels a side). `state_parts`: images go in the state, which becomes a list of parts
    (the state's own text first, then one `image_url` data URL per image), as OpenRouter's System One surface takes them.
    `documents`: with `state_parts`, an "image" may also be a PDF, a recording or a clip, each sent as the part OpenRouter names
    for it (`file`, `input_audio`, `video_url`). `detail`: the `detail` of every image part ('low' or 'high'), when given."""
    def __init__(self, who, url, model, key, opener, max_questions=0, max_images=0, max_image=4 * 1024 * 1024,
                 max_images_total=8 * 1024 * 1024, max_request=13 * 1024 * 1024, max_response=2 * 1024 * 1024,
                 assets=False, headers=None, prepare=None, state_parts=False, documents=False, detail=''):
        self.who, self.url, self.model, self.key, self.opener = who, url, model, key, opener
        self.max_questions, self.max_images = max_questions, max_images
        self.state_parts, self.documents, self.detail = state_parts, documents, detail
        self.assets, self.headers, self.prepare = assets, headers or (lambda body, images: {}), prepare or (lambda picture: picture)
        self.max_image, self.max_images_total, self.max_request, self.max_response = max_image, max_images_total, max_request, max_response

    def available(self):
        return bool(os.environ.get(self.key))

    def auth(self, key):
        """The request header that carries the key."""
        return {'Authorization': 'Bearer ' + key}

    def usage(self, value):
        """The token counts an answer reports, and what it cost when the host says."""
        usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
        kept = {k: usage[k] for k in ('input_tokens', 'output_tokens', 'total_tokens') if type(usage.get(k)) is int}
        if _finite(usage.get('cost')):
            kept['cost'] = float(usage['cost'])  # OpenRouter says what the request cost, in dollars
        return kept

    def _media(self, picture):
        for magic, media in MEDIA:
            if picture.startswith(magic):
                return media
        if picture[:4] == b'RIFF' and picture[8:12] == b'WEBP':
            return 'image/webp'
        if self.documents:
            for magic, media in DOCUMENTS:
                if picture.startswith(magic):
                    return media
            if picture[:4] == b'RIFF' and picture[8:12] == b'WAVE':
                return 'audio/wav'
            if picture[4:8] == b'ftyp':
                return 'video/mp4'
            raise DecisionError(f'{self.who} is sent PNG, JPEG, WebP, PDF, WAV, MP3 and MP4 only; this request was not sent.')
        raise DecisionError(f'{self.who} reads PNG, JPEG and WebP images only; this request was not sent.')

    def _part(self, item):
        """One part of the state for a picture, recording, clip or PDF (OpenRouter's multimodal decisions guide, read 2026-11-11,
        and measured that day: a part of a type the surface does not know is ignored without an error, and an `audio_url` part
        is read as text)."""
        media = self._media(item)
        if media.startswith('image/'):
            item = self.prepare(item)
            media = self._media(item)
        data = base64.b64encode(item).decode()
        if media.startswith('image/'):
            return {'type': 'image_url', 'image_url': {'url': f'data:{media};base64,' + data, **({'detail': self.detail} if self.detail else {})}}
        if media.startswith('audio/'):
            return {'type': 'input_audio', 'input_audio': {'data': data, 'format': media.split('/')[1]}}
        if media.startswith('video/'):
            return {'type': 'video_url', 'video_url': {'url': f'data:{media};base64,' + data}}
        return {'type': 'file', 'file': {'file_data': f'data:{media};base64,' + data}}

    def _probability(self, value, gaps, where):
        if not _finite(value):
            raise DecisionError(f'{self.who} returned an invalid probability ({where}).')
        if not 0 <= value <= 1:
            gaps.setdefault('clamped', []).append(where)
            if not -.01 <= value <= 1.01:
                raise DecisionError(f'{self.who} returned an out-of-range probability ({where}).')
            value = min(1.0, max(0.0, value))
        return float(value)

    def request_body(self, state, questions, images=()):
        """The exact JSON body: Jev's, with the model's name, and any images inline as one line of base64 each."""
        if images and not self.max_images:
            raise DecisionError(f'{self.who} reads text only; this request was not sent.')
        if len(images) > self.max_images:
            raise DecisionError(f'{self.who} reads at most {self.max_images} images in a request; this one was not sent.')
        if any(len(picture) > self.max_image for picture in images) or sum(len(picture) for picture in images) > self.max_images_total:
            raise DecisionError(f'These images exceed the size limits of {self.who}; the request was not sent.')
        body = {'model': self.model, 'state': state, 'questions': questions}
        if images and self.assets:
            # Jev (docs.typesafe.ai/go/images, read 2026-10-15): each image is listed once in `assets` under a reference that the
            # request's strings name; a fresh UUID each time cannot occur in the text by accident. The state keeps its words and
            # names the images "image 1", "image 2", ... as the sandbox's states already call them.
            import uuid
            if any(self._media(picture) == 'image/webp' for picture in images):
                raise DecisionError(f'{self.who} reads PNG and JPEG images only; this request was not sent.')
            images = [self.prepare(picture) for picture in images]
            refs = [str(uuid.uuid4()) for _ in images]
            pictures = {'image' if len(refs) == 1 else f'image {i}': ref for i, ref in enumerate(refs, 1)}
            body['state'] = {'description': state, **pictures}
            body['assets'] = {ref: {'type': 'image', 'format': {'image/png': 'png', 'image/jpeg': 'jpg'}[self._media(picture)],
                                    'data_base64': base64.b64encode(picture).decode()} for ref, picture in zip(refs, images)}
            return body
        if images and self.state_parts:
            # OpenRouter (its 400 for a top-level `images`, 2026-10-18): "Put each image in the `state` array as {"type":
            # "image_url", "image_url": {"url": "data:image/png;base64,..."}}". The state's words go first, as one text part.
            text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
            body['state'] = [{'type': 'text', 'text': text}] + [self._part(picture) for picture in images]
            return body
        if images:
            body['images'] = [{'mediaType': self._media(picture), 'base64': base64.b64encode(picture).decode()} for picture in images]
        return body

    def _distribution(self, listed, keys, gaps, name):
        """{key: probability} over `keys`, in their order, from an object keyed by them (the guide's shape) or a positional list."""
        if isinstance(listed, list) and len(listed) == len(keys):
            gaps.setdefault('positional_probabilities', []).append(name)
            listed = dict(zip(keys, listed))
        if not isinstance(listed, dict):
            raise DecisionError(f'{self.who} returned no distribution.')
        if any(key not in keys for key in listed):
            raise DecisionError(f'{self.who} returned an unknown option or level.')
        missing = [key for key in keys if key not in listed]
        if missing:
            gaps.setdefault('unlisted_options', {})[name] = len(missing)
        return {key: self._probability(listed[key], gaps, name) if key in listed else 0.0 for key in keys}

    def normalize(self, value, questions):
        """(Jev-shaped answers, raw answers by name, gaps). An answer missing or of another type fails the whole answer, as an
        incomplete Jev answer does."""
        raw = value.get('answers') if isinstance(value, dict) else None
        if not isinstance(raw, dict):
            raise DecisionError(f'{self.who} returned no answers.')
        gaps = {}
        if set(raw) != set(questions):
            raise DecisionError(f'{self.who} returned incomplete decision answers.',
                                gaps={'missing': sorted(set(questions) - set(raw)), 'extra': sorted(set(raw) - set(questions))})
        cleaned = {}
        for name, asked in questions.items():
            answer = raw[name]
            if not isinstance(answer, dict) or answer.get('type') != asked['type']:
                raise DecisionError(f'{self.who} returned a different question type.')
            if asked['type'] == 'noul':
                cleaned[name] = {'type': 'noul', 'noul': self._probability(answer.get('noul'), gaps, name)}
            elif asked['type'] == 'choice':
                options = list(asked['criteria'])
                probs = self._distribution(answer.get('probabilities'), options, gaps, name)
                if answer.get('choice') not in options:
                    raise DecisionError(f'{self.who} chose an unknown option.')
                n = len(options)
                formula = 1.0 if n == 1 else (max(probs.values()) - 1 / n) / (1 - 1 / n)
                api = answer.get('confidence')
                api = self._probability(api, gaps, name + '.confidence') if api is not None else None
                if api is None or abs(api - formula) > .03:
                    gaps.setdefault('confidence_vs_formula', {})[name] = None if api is None else round(api - formula, 4)
                # Jev's formula over the returned probabilities, as for Decisions, so a gate keeps its meaning across the models.
                cleaned[name] = {'type': 'choice', 'choice': answer['choice'], 'probabilities': probs,
                                 'confidence': max(0.0, min(1.0, formula)), 'api_confidence': api}
            elif asked['type'] == 'score':
                levels = [str(i) for i in range(len(asked['criteria']))]
                probs = self._distribution(answer.get('probabilities'), levels, gaps, name)
                weighted = sum(i * probs[level] for i, level in enumerate(levels))
                score = answer.get('score')
                if not _finite(score):
                    raise DecisionError(f'{self.who} returned an invalid score.')
                if abs(score - weighted) > .005 + .005 * len(levels) * (len(levels) - 1) / 2:
                    gaps.setdefault('score_vs_weighted', {})[name] = round(score - weighted, 4)
                api = answer.get('confidence')
                cleaned[name] = {'type': 'score', 'score': float(weighted), 'probabilities': probs,
                                 'confidence': self._probability(api, gaps, name + '.confidence') if api is not None else max(probs.values()),
                                 'api_score': float(score)}
            else:
                raise DecisionError('This sandbox does not use that question type.')
        return cleaned, raw, gaps

    def evaluate(self, state, questions, images=()):
        """{model, answers (Jev shape), raw, usage, gaps}. Raises DecisionError; `uncertain` when the outcome is unknown.

        A check of more questions than the host takes in a request goes as several requests over the same state, in order,
        and counts as one answer: its usage is their sum (`gaps.split_requests` says how many), and one part's failure fails
        it all, nothing resent."""
        names = list(questions)
        if not self.max_questions or len(names) <= self.max_questions:
            return self._evaluate(state, questions, images)
        merged = {'model': self.model, 'answers': {}, 'raw': {}, 'gaps': {}, 'usage': {}, 'response_keys': []}
        parts = [names[i:i + self.max_questions] for i in range(0, len(names), self.max_questions)]
        for part in parts:
            value = self._evaluate(state, {name: questions[name] for name in part}, images)
            merged['model'] = value['model']
            merged['answers'].update(value['answers'])
            merged['raw'].update(value['raw'])
            merged['response_keys'] = value['response_keys']
            for key, count in value['usage'].items():
                merged['usage'][key] = merged['usage'].get(key, 0) + count
            for gap, found in value['gaps'].items():
                merged['gaps'].setdefault(gap, found)
        merged['gaps']['split_requests'] = len(parts)
        return merged

    def _evaluate(self, state, questions, images=()):
        if not self.available():
            raise DecisionError(f'{self.key} is not set for the sandbox.')
        body = json.dumps(self.request_body(state, questions, images), ensure_ascii=False).encode()
        if len(body) > self.max_request:
            raise DecisionError(f'This request exceeds the body limit of {self.who}; it was not sent.')
        request = Request(self.url, data=body, headers={**self.auth(os.environ[self.key]),
                                                        'Content-Type': 'application/json', **self.headers(body, images)},
                          method='POST')
        try:
            with self.opener(request) as response:
                if response.status != 200:
                    raise DecisionError(f'{self.who} could not complete this check (HTTP {response.status}).', status=response.status)
                payload = response.read(self.max_response + 1)
        except HTTPError as error:
            code = error.code
            try:
                detail = error.read(4000).decode('utf-8', 'replace')
            except Exception:  # noqa: BLE001 - the body is only a hint
                detail = ''
            error.close()
            found = kind(detail)
            raise DecisionError(f'{self.who} could not complete this check (HTTP {code}{": " + found if found else ""}).',
                                status=code) from None
        except (URLError, TimeoutError, OSError):
            raise DecisionError(f'The {self.who} request outcome is uncertain; it was not resent.', uncertain=True) from None
        if len(payload) > self.max_response:
            raise DecisionError(f'{self.who} returned an oversized answer.')
        try:
            value = json.loads(payload)
        except ValueError:
            raise DecisionError(f'{self.who} returned an unreadable answer.') from None
        if isinstance(value, dict) and isinstance(value.get('error'), dict) and 'answers' not in value:
            # A host that answers 200 with an error body (OpenRouter passes a provider's failure on this way).
            found = ' '.join(str(value['error'][name]) for name in ('type', 'code') if value['error'].get(name))[:80]
            raise DecisionError(f'{self.who} could not complete this check ({found or "an error body"}).')
        cleaned, raw, gaps = self.normalize(value, questions)
        try:
            check_bounds(cleaned, questions)
        except ValueError as error:
            gaps['outside_jev_bounds'] = str(error)
        return {'model': value.get('model') or self.model, 'answers': cleaned, 'raw': raw, 'gaps': gaps, 'usage': self.usage(value),
                'response_keys': sorted(value)}
