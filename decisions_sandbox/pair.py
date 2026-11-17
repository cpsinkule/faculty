"""PairService: duck-types `jev.service.JevService.ask` so Faculty's own check code runs unchanged, once per model.

Mode 'jev' asks Jev (or replays a live receipt whose input hash and questions match exactly: that costs nothing and proves the
copy rebuilt the live input); mode 'decisions' asks OpenAI Decisions and gives the site Jev-shaped answers; mode 'snap'
(2026-10-13, requirement: "test a new decision model against Jev and Decision API") asks SubQ Snap; the four modes after
those in `MODES` (microsoft, jev114, jev113x, liquid, gemini) are described below. Each distinct
(state, questions, images, attempt) is sent once per model, whichever thread asks first; the others wait for that answer.
Kept: hashes, questions, answers, usage and timing (calls.jsonl in the run folder, 0700). Never kept: the state itself.
"""
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone

from jev import client as jev_client
from jev.service import JevService, encoded, fingerprint
from course_storage import atomic_json, fsync_dir

from . import decisions_client, gemini_client, gemini_native_client, jev_preview_client, liquid_client, openrouter_client, snap_client

# 'microsoft' (2026-10-13, requirement: "let's test it against jev") asks Microsoft-Decision-1 through OpenRouter.
# 'jev114' and 'jev113x' (2026-10-15, requirement: "Jev has updated their models, see if we can use the updated stuff"): Jev 1.14,
# and the pinned Jev 1.13 with its opt-in images and long context (`jev_preview_client`).
# 'liquid' (2026-10-18, Chris: "a new decision model available through openrouter that I want to test against Jev") asks
# Liquid d1-ultra through OpenRouter.
# 'gemini' (2026-11-11, Chris: "google released a new decisions model ... it has more modalities than Jev") asks Gemini
# Decision 1 through OpenRouter; what it is sent beside pictures (a PDF, a recording, a clip) travels as `images` do.
# 'google' and 'googleultra' (2026-11-12, Chris: "retest those specific tests just on the new gemini key") ask the same model of
# Google itself, the second with every picture at its highest resolution (`gemini_native_client`).
MODES = ('jev', 'decisions', 'snap', 'microsoft', 'jev114', 'jev113x', 'liquid', 'gemini', 'google', 'googleultra')
PARALLEL = {'jev': 8, 'decisions': 4, 'snap': 4, 'microsoft': 4, 'liquid': 4, 'gemini': 4, 'jev114': 8, 'jev113x': 8, 'google': 4,
            'googleultra': 4}
# Modes whose client is one of several hosts of a module (`HOSTS`), asked through `_hosted` or a test's `ask_preview`.
NAMED = (*jev_preview_client.MODELS, *gemini_native_client.MODELS)


def _copy(value):
    return json.loads(json.dumps(value))


# Each hosted model's client module (or `systemone.Host`) by mode: what a mode asks and whether its key is saved.
HOSTED = {'decisions': decisions_client, 'snap': snap_client, 'microsoft': openrouter_client, 'liquid': liquid_client,
          'gemini': gemini_client, **gemini_native_client.HOSTS,
          **jev_preview_client.HOSTS}


def _hosted(mode):
    def ask(state, questions, images):
        value = HOSTED[mode].evaluate(state, questions, images)  # looked up now, so a patched client is the one asked
        return {k: value[k] for k in ('model', 'answers', 'usage', 'gaps', 'response_keys')}
    return ask


class RequestNotSent(BaseException):
    """A local gate refused this request before any provider call."""


class Store:
    def __init__(self, folder, seed=None, ask_jev=None, ask_decisions=None, ask_snap=None, ask_microsoft=None,
                 available=None, ask_preview=None, ask_liquid=None, ask_gemini=None):
        self.folder = folder
        if available is not None:
            self._available = available
        self.calls_path = folder / 'calls.jsonl'
        self.pending_folder = folder / 'pending-calls'
        self._lock, self._write = threading.Lock(), threading.Lock()
        self._entries, self._pending = {}, {}
        self.seed = seed or {}
        self._gates = {mode: threading.BoundedSemaphore(n) for mode, n in PARALLEL.items()}
        self._ask = {'jev': ask_jev or self._jev, 'decisions': ask_decisions or self._decisions, 'snap': ask_snap or self._snap,
                     'microsoft': ask_microsoft or self._microsoft, 'liquid': ask_liquid or self._liquid,
                     'gemini': ask_gemini or self._gemini,
                     **{mode: (ask_preview or _hosted)(mode) for mode in NAMED}}
        # A later run in the same folder reads what earlier runs were answered: nothing is asked twice, a failed or uncertain call
        # is never resent (only `attempt` makes a separately identified request), and an unavailable one may be asked once a key exists.
        if self.calls_path.exists():
            for line in self.calls_path.read_text().splitlines():
                entry = json.loads(line)
                if entry.get('status') != 'unavailable':
                    self._entries[entry['key']] = entry
        # A durable intent without a finished receipt may have reached the provider.
        # Keep it uncertain across restarts, including termination before Python cleanup.
        if self.pending_folder.exists():
            for path in sorted(self.pending_folder.glob('*.json')):
                entry = json.loads(path.read_text())
                if entry['key'] not in self._entries:
                    self._record(entry)
                    self._entries[entry['key']] = entry
                else:
                    self._discard_pending(entry['key'])

    def _discard_pending(self, key):
        path = self.pending_folder / (key + '.json')
        if path.exists():
            path.unlink()
            fsync_dir(self.pending_folder)

    def _record(self, entry):
        with self._write, self.calls_path.open('a') as log:
            os.chmod(self.calls_path, 0o600)
            log.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + '\n')
            log.flush()
            os.fsync(log.fileno())
        # The first append creates the log's directory entry. Keep its pending
        # receipt until that name, as well as the file contents, is durable.
        fsync_dir(self.folder)
        self._discard_pending(entry['key'])

    @staticmethod
    def _jev(state, questions, images):
        if images:
            raise jev_client.DecisionError('Jev reads text only; this check is for Decisions alone.')
        value = jev_client.evaluate(state, questions)
        return {'model': value['model'], 'answers': value['answers'], 'usage': value['usage'], 'gaps': {}}

    _decisions = staticmethod(_hosted('decisions'))
    _snap = staticmethod(_hosted('snap'))
    _microsoft = staticmethod(_hosted('microsoft'))
    _liquid = staticmethod(_hosted('liquid'))
    _gemini = staticmethod(_hosted('gemini'))

    @staticmethod
    def _available(mode):
        return (jev_client if mode == 'jev' else HOSTED[mode]).available()

    def entries(self):
        with self._lock:
            return [dict(e) for e in self._entries.values()]

    def answer(self, mode, purpose, owner, state, questions, rule, images=(), attempt=''):
        digest = fingerprint(state)
        asked = encoded(questions)
        pictures = [hashlib.sha256(png).hexdigest() for png in images]
        key = fingerprint([mode, digest, asked, pictures, attempt])
        while True:
            with self._lock:
                entry = self._entries.get(key)
                waiting = self._pending.get(key) if entry is None else None
                if entry is not None or waiting is None:
                    if entry is None:
                        self._pending[key] = threading.Event()
                    break
            waiting.wait()
        if entry is None:
            try:
                entry = self._send(key, mode, purpose, owner, digest, asked, questions, state, rule, images, pictures, attempt)
            except RequestNotSent:
                self._discard_pending(key)
                raise
            except BaseException as why:
                path = self.pending_folder / (key + '.json')
                entry = json.loads(path.read_text()) if path.exists() else {
                         'id': 'sbx-' + key[:16], 'key': key, 'mode': mode, 'purpose': purpose, 'owner': owner, 'rule': rule,
                         'status': 'uncertain', 'error': f'{type(why).__name__} in the sandbox', 'answers': {}, 'usage': {},
                         'gaps': {}, 'model': '', 'replayed': False, 'seconds': None}
                self._record(entry)
                raise
            else:
                self._record(entry)
            finally:
                with self._lock:
                    if entry is not None:
                        self._entries[key] = entry
                    self._pending.pop(key).set()
        view = {'id': entry['id'], 'status': entry['status'], 'input_sha256': digest, 'error': entry['error'],
                'model': entry['model'], 'answers': _copy(entry['answers']), 'usage': dict(entry['usage'])}
        return view, key

    def _send(self, key, mode, purpose, owner, digest, asked, questions, state, rule, images, pictures, attempt):
        entry = {'id': 'sbx-' + key[:16], 'key': key, 'mode': mode, 'purpose': purpose, 'owner': owner, 'rule': rule,
                 'input_sha256': digest, 'questions': json.loads(asked), 'images': pictures, 'attempt': attempt,
                 'input_chars': len(state if isinstance(state, str) else encoded(state)),
                 'created_at': datetime.now(timezone.utc).isoformat(), 'replayed': False, 'answers': {}, 'usage': {},
                 'gaps': {}, 'model': '', 'error': '', 'seconds': None}
        seeded = self.seed.get((digest, asked)) if mode == 'jev' and not attempt and not images else None
        if seeded is not None:
            return {**entry, 'status': 'complete', 'replayed': True, 'answers': seeded['answers'],
                    'usage': seeded.get('usage') or {}, 'model': seeded.get('model') or jev_client.MODEL}
        if not self._available(mode):
            return {**entry, 'status': 'unavailable', 'error': f'{mode} is not configured for the sandbox.'}
        with self._gates[mode]:
            self.pending_folder.mkdir(mode=0o700, exist_ok=True)
            atomic_json(self.pending_folder / (key + '.json'), {
                **entry, 'status': 'uncertain',
                'error': 'The sandbox stopped before a final receipt; this request was not resent.'})
            fsync_dir(self.folder)
            started = time.monotonic()
            try:
                value = self._ask[mode](state, questions, images)
                status, error = 'complete', ''
            except (jev_client.DecisionError, decisions_client.DecisionError) as why:
                value, status, error = {'gaps': getattr(why, 'gaps', {}) or {}}, ('uncertain' if why.uncertain else 'failed'), str(why)
            except Exception as why:  # noqa: BLE001 - recorded; never resent
                value, status, error = {}, 'uncertain', f'{type(why).__name__}: the check did not finish; it was not resent.'
            seconds = round(time.monotonic() - started, 3)
        return {**entry, 'status': status, 'error': error, 'seconds': seconds, 'answers': value.get('answers') or {},
                'usage': value.get('usage') or {}, 'gaps': value.get('gaps') or {}, 'model': value.get('model') or '',
                **({'response_keys': value['response_keys']} if value.get('response_keys') else {})}


class PairService:
    """One model's side of a case: `used` lists the call keys the site's code asked for (thread-safe appends)."""
    _view = staticmethod(JevService._view)

    def __init__(self, store, mode, path=None, attempt=''):
        self.store, self.mode, self.path, self.attempt = store, mode, path, attempt
        self.used = []

    def ask(self, purpose, owner, state, questions, rule, enrollment=None, attempt='', images=()):
        view, key = self.store.answer(self.mode, purpose, owner, state, questions, rule, images=images,
                                      attempt=attempt or self.attempt)
        self.used.append(key)
        return view

    def recover_interrupted(self):
        return 0


def seed_from(db):
    """{(input_sha256, questions_json): {answers, usage, model}} of the copy's complete Jev receipts."""
    found = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='jev_calls'").fetchone() is None:
        return found
    for row in db.execute("SELECT input_sha256,questions_json,result_json,model FROM jev_calls WHERE status='complete' ORDER BY rowid"):
        result = json.loads(row[2] or '{}')
        if result.get('answers'):
            found[(row[0], row[1])] = {'answers': result['answers'], 'usage': result.get('usage') or {}, 'model': row[3]}
    return found
