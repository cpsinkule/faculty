"""Gemini Decision 1 asked of Google itself (Chris, 2026-11-12, after whole bubble sheets read poorly through OpenRouter, where a
picture gets about 1,100 tokens whatever its size and Google's higher resolution is refused: "retest those specific tests just
on the new gemini key"): one bounded request, no redirect, proxy or automatic retry.

Google's Decisions API (aistudio.google.com/docs/decisions-api, read 2026-11-12) is not Jev's body: the state is `input`, a
list of blocks (`text`, `image`, `document`, `audio`, `video`, each with raw base64 `data` and a `mime_type`), a yes/no
question's criteria are `true_description` and `false_description`, an option with no description takes "", and a question's
key is letters, digits, "_" and "-" only, so each question goes under a plain key and comes back under its own name. An image
block may say its `resolution` (low, medium, high, ultra_high). Answers are already in Jev's shape. The key is
`GEMINI_API_KEY`, sent as `x-goog-api-key`.

Two sandbox modes, asked only when `--modes` names them: `google` (no resolution said) and `googleultra` (`ultra_high`).
"""
import base64
import json

from . import systemone
from .decisions_client import DecisionError

URL = 'https://generativelanguage.googleapis.com/v1beta/decisions'
MODEL = 'gemini-decision-1'
TIMEOUT = 120
MEGABYTE = 1024 * 1024
MODELS = {'google': '', 'googleultra': 'ultra_high'}  # mode: the resolution every image block names
BLOCK = {'image': 'image', 'audio': 'audio', 'video': 'video', 'application': 'document'}


def open_request(request):
    return systemone.open_request(request, TIMEOUT)


class Native(systemone.Host):
    def __init__(self, resolution=''):
        super().__init__('Gemini Decision 1 (Google' + (f', {resolution}' if resolution else '') + ')', URL, MODEL, 'GEMINI_API_KEY',
                         lambda request: open_request(request), max_images=16, state_parts=True, documents=True,
                         max_image=20 * MEGABYTE, max_images_total=40 * MEGABYTE, max_request=60 * MEGABYTE)
        self.resolution = resolution

    def auth(self, key):
        return {'x-goog-api-key': key}

    @staticmethod
    def _keys(questions):
        return {name: f'q{position}' for position, name in enumerate(questions)}

    def _block(self, item):
        media = self._media(item)
        if media.startswith('image/'):
            item = self.prepare(item)
            media = self._media(item)
        block = {'type': BLOCK[media.split('/')[0]], 'data': base64.b64encode(item).decode(), 'mime_type': media}
        if block['type'] == 'image' and self.resolution:
            block['resolution'] = self.resolution
        return block

    def request_body(self, state, questions, images=()):
        if len(images) > self.max_images:
            raise DecisionError(f'{self.who} reads at most {self.max_images} files in a request; this one was not sent.')
        if any(len(item) > self.max_image for item in images) or sum(len(item) for item in images) > self.max_images_total:
            raise DecisionError(f'These files exceed the size limits of {self.who}; the request was not sent.')
        asked = {}
        for name, key in self._keys(questions).items():
            question = questions[name]
            sent = {'type': question['type'], 'instructions': question['instructions']}
            criteria = question.get('criteria')
            if question['type'] == 'noul' and criteria:
                sent['criteria'] = {f'{side}_description': criteria[side] for side in ('true', 'false') if criteria.get(side)}
            elif question['type'] == 'choice':
                sent['criteria'] = {option: described or '' for option, described in criteria.items()}
            elif question['type'] == 'score':
                sent['criteria'] = list(criteria)
            asked[key] = sent
        text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        return {'model': self.model, 'input': [{'type': 'text', 'text': text}] + [self._block(item) for item in images] if images else text,
                'questions': asked}

    def normalize(self, value, questions):
        raw = value.get('answers') if isinstance(value, dict) else None
        if isinstance(raw, dict):
            named = {key: name for name, key in self._keys(questions).items()}
            value = {**value, 'answers': {named.get(key, key): answer for key, answer in raw.items()}}
        return super().normalize(value, questions)

    def usage(self, value):
        usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
        kept = {ours: usage[theirs] for ours, theirs in (('input_tokens', 'total_input_tokens'), ('output_tokens', 'total_output_tokens'),
                                                         ('total_tokens', 'total_tokens')) if type(usage.get(theirs)) is int}
        for part in usage.get('input_tokens_by_modality') or []:
            if isinstance(part, dict) and type(part.get('tokens')) is int:
                kept[f"{part.get('modality')}_tokens"] = part['tokens']
        return kept


def host(resolution=''):
    return Native(resolution)


HOSTS = {mode: host(resolution) for mode, resolution in MODELS.items()}
