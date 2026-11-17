"""JEV: workspace receipts; no teaching writes and no copies of source bodies."""
import hashlib
import json
import os
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

from canonical_json import digest as fingerprint, encoded
from catalog_migrations import apply_sql, migration_transaction
from course_storage import timestamp
from rosters import connect

from . import client


def noul(instructions, criteria=None):
    """A yes/no question; `criteria` {"true": ..., "false": ...} says what each answer means (left out when not given, so
    existing questions keep their exact receipts)."""
    return {'type': 'noul', 'instructions': instructions, **({'criteria': criteria} if criteria else {})}


MAX_CHOICE_OPTIONS = 254  # a Choice holds at most 255 options; a caller keeps one for its sentinel (NEW, NONE)


def choice(instructions, criteria):
    return {'type': 'choice', 'instructions': instructions, 'criteria': criteria}


def distinct_labels(labels, *, reserved=(), casefold=False, numbered_by_position=False):
    """Workspace choice labels, keeping real names and sentinel options distinct (JEV).

    Reserve every original label before adding suffixes, so a generated ``Lee (2)``
    cannot displace a real name later in the list. Normal labels keep their spelling.
    """
    labels = list(labels)
    fold = str.casefold if casefold else lambda value: value
    occupied = {fold(value) for value in reserved}
    originals = {fold(value) for value in labels} | occupied
    result, counts = [], {}
    for position, label in enumerate(labels, 1):
        key = fold(label)
        counts[key] = counts.get(key, 0) + 1
        chosen = label
        if key in occupied:
            suffix = max(2, position if numbered_by_position else counts[key])
            chosen = f'{label} ({suffix})'
            while fold(chosen) in originals or fold(chosen) in occupied:
                suffix += 1
                chosen = f'{label} ({suffix})'
        occupied.add(fold(chosen))
        result.append(chosen)
    return result


def both(name, instructions, criteria):
    """A Choice that decides something, asked twice in one request with its options reversed (JEV "Shared": Jev 1.13 leans to the
    first option): {name: ..., name + "__reversed": ...}."""
    return {name: choice(instructions, criteria), name + '__reversed': choice(instructions, dict(reversed(list(criteria.items()))))}


def agreed(answers, name):
    """(the option both orders of `both` chose, the lower of their confidences), or (None, 0) when they differ."""
    first, second = answers[name], answers[name + '__reversed']
    return (first['choice'], min(first['confidence'], second['confidence'])) if first['choice'] == second['choice'] else (None, 0)


def score(instructions, levels):
    """A rating over ordered `levels` (lowest first, 2 to 10), answered as a probability-weighted level."""
    return {'type': 'score', 'instructions': instructions, 'criteria': list(levels)}


TRIAGE_PURPOSE = 'student-triage'
TRIAGE_DEFAULT = 'jev'  # JEV TRIAGE PROVIDER: 'decisions' once the instructor OKs the calibrated gates
# JEV STUDENT MATCH PROVIDER (requirement, 2026-11-12): whose paper a
# first page is (`document_identity.jev_papers.identify`, its purpose) asks Gemini Decisions; 'jev' asks Jev as before.
STUDENT_MATCH_PURPOSE = 'paper-student'
STUDENT_MATCH_DEFAULT = 'gemini'
# JEV HOST FAILURES (2026-10-09): after a host-level failure the worker that asks waits this long before its next ask, longer with
# each failure in a row (minutes); the next ask is the probe.
HOST_PAUSE_MINUTES = (5, 10, 20, 40, 60)


def _moment(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def host_outage(db, purpose, model):
    """JEV HOST FAILURES: while the newest settled calls of `purpose` to `model` failed at the host (`host` in their result), {since
    (the first of that run), failures, minutes (the current pause), until (the next ask), refused}; None once one is not.
    A catalog read, so every process sees the same outage."""
    rows = db.execute("""SELECT result_json,created_at,completed_at FROM jev_calls WHERE purpose=? AND model=? AND status!='running'
        ORDER BY created_at DESC LIMIT 50""", (purpose, model)).fetchall()
    streak = []
    for row in rows:
        if not json.loads(row['result_json'] or '{}').get('host'):
            break
        streak.append(row)
    if not streak:
        return None
    minutes = HOST_PAUSE_MINUTES[min(len(streak), len(HOST_PAUSE_MINUTES)) - 1]
    last = _moment(streak[0]['completed_at'] or streak[0]['created_at']) or datetime.now(timezone.utc)
    return {'since': streak[-1]['created_at'], 'failures': len(streak), 'minutes': minutes,
            'until': (last + timedelta(minutes=minutes)).isoformat(), 'refused': bool(json.loads(streak[0]['result_json']).get('refused'))}


def paused_line(provider, outage):
    """"Student triage paused: OpenAI Decisions unavailable since 10:42 AM. Retrying every 5 minutes." (campus time; a day named
    when it is not today)."""
    from scheduling.clock import CAMPUS_ZONE
    name = 'Jev' if provider is client else 'OpenAI Decisions'
    since = (_moment(outage['since']) or datetime.now(timezone.utc)).astimezone(CAMPUS_ZONE)
    clock = since.strftime('%I:%M %p').lstrip('0')
    if since.date() != datetime.now(CAMPUS_ZONE).date():
        clock = f"{since.strftime('%b')} {since.day}, {clock}"
    return f"Student triage paused: {name} unavailable since {clock}. Retrying every {outage['minutes']} minutes."


def provider_for(purpose):
    """JEV TRIAGE PROVIDER (requirement, 2026-10-08): student triage asks
    OpenAI Decisions (`openai_decisions`) when FACULTY_TRIAGE_PROVIDER, else TRIAGE_DEFAULT, is 'decisions'; every other check,
    SAME MATTER included, asks Jev. Both answer in Jev's shape, with the same receipts, bounds and no resends.

    JEV STUDENT MATCH PROVIDER (requirement, 2026-11-12): whose paper a first page is asks Gemini Decisions (`gemini_decisions`)
    when FACULTY_STUDENT_MATCH_PROVIDER, else STUDENT_MATCH_DEFAULT, is 'gemini' and its key is configured; without the key
    Jev is asked as before, so the match never stops for a missing key."""
    if purpose == TRIAGE_PURPOSE and os.environ.get('FACULTY_TRIAGE_PROVIDER', TRIAGE_DEFAULT) == 'decisions':
        import openai_decisions
        return openai_decisions
    if purpose == STUDENT_MATCH_PURPOSE and os.environ.get('FACULTY_STUDENT_MATCH_PROVIDER', STUDENT_MATCH_DEFAULT) == 'gemini':
        import gemini_decisions
        if gemini_decisions.available():
            return gemini_decisions
    return client


def provider_name(provider):
    return 'Jev' if provider is client else getattr(provider, 'NAME', 'OpenAI Decisions')


class JevService:
    def __init__(self, rosters):
        self.rosters, self.path = rosters, rosters.path
        with rosters.lock(), connect(self.path) as db, migration_transaction(db):
            for file in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
                apply_sql(db, 'jev', file.stem, file.read_text())

    def status(self):
        triage = provider_for(TRIAGE_PURPOSE)
        outage = self.outage(TRIAGE_PURPOSE, triage.MODEL) if triage.available() else None
        paused = paused_line(triage, outage) if outage else ''
        return {'available': client.available(), 'model': client.MODEL,
                'detail': 'Jev analyzes new captured conversations and checks drafts and answers.' if client.available()
                else 'Jev is disabled or its server-side TypeSafe key is missing. Existing workflows remain available.',
                **({} if triage is client else {'triage': {'available': triage.available(), 'model': triage.MODEL}}),
                **({'triage_paused': paused} if paused else {})}

    def outage(self, purpose, model):
        with connect(self.path) as db:
            return host_outage(db, purpose, model)

    def triage_status(self):
        """JEV HOST FAILURES, for a page that shows student triage (the Automation tab's Student triage row): {provider ('jev' or
        'decisions'), model, available, paused (the one line while the triage provider fails at its host, else ''), failed_count
        (current-term conversations whose newest triage receipt from that model failed or is uncertain; a host refusal, which
        decided nothing, is not one)}. Configuration and catalog only; it sends nothing."""
        provider = provider_for(TRIAGE_PURPOSE)
        with connect(self.path) as db:
            outage = host_outage(db, TRIAGE_PURPOSE, provider.MODEL)
            newest = {}
            for owner, status, result in db.execute("""SELECT c.owner_id,c.status,c.result_json FROM jev_calls c
                    JOIN student_enrollments e ON e.id=c.enrollment_id JOIN course_terms t ON t.id=e.term_id
                    WHERE c.purpose=? AND c.model=? AND c.status!='running' AND t.is_current=1 AND t.deleted_at IS NULL
                    ORDER BY c.created_at,c.rowid""", (TRIAGE_PURPOSE, provider.MODEL)):
                if not json.loads(result or '{}').get('refused'):
                    newest[owner] = status
        return {'provider': 'jev' if provider is client else 'decisions', 'model': provider.MODEL, 'available': provider.available(),
                'paused': paused_line(provider, outage) if outage else '',
                'failed_count': sum(status in ('failed', 'uncertain') for status in newest.values())}

    def recover_interrupted(self):
        """JEV: worker recovery records uncertainty; it never sends another request."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        with self.rosters.lock(), connect(self.path) as db:
            return db.execute("UPDATE jev_calls SET status='uncertain',error='The interrupted check was not resent.',completed_at=? WHERE status='running' AND created_at<?",
                              (timestamp(), cutoff)).rowcount

    def ask(self, purpose, owner, state, questions, rule, enrollment=None, attempt='', images=(), receipt=None):
        """`images`: pictures the check looks at (JEV PICTURES), asked of Jev's IMAGE_MODEL unless the purpose's own provider reads
        pictures (JEV STUDENT MATCH PROVIDER: Gemini Decisions); the receipt keeps their sha256, never the pictures. `receipt` {questions, result}: what the receipt keeps instead of the asked questions, and a function
        turning the answers into what it keeps (a roster check keeps enrollment IDs, never the names it showed); the caller then
        reads that kept form, on a first ask as on a repeated one."""
        provider = provider_for(purpose)
        if images and not getattr(provider, 'PICTURES', False):
            provider = client
        if not provider.available():
            return {'status': 'unavailable', 'error': f'{provider_name(provider)} is disabled or not configured.'}
        model = client.IMAGE_MODEL if images and provider is client else provider.MODEL
        oversized = len(encoded({'state': state, 'questions': questions}).encode()) > 110000
        digest = fingerprint([state, [hashlib.sha256(picture).hexdigest() for picture in images]] if images else state)
        key = fingerprint([purpose, owner, digest, questions, rule, model, attempt])
        kept = receipt['questions'] if receipt else questions
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jev_calls WHERE call_key=?', (key,)).fetchone()
            if row:
                return self._view(row)
            ident = str(uuid.uuid4())
            db.execute('INSERT INTO jev_calls(id,call_key,purpose,owner_id,enrollment_id,input_sha256,questions_json,model,rule,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (ident, key, purpose, owner, enrollment, digest, encoded(kept), model, rule, 'running', timestamp()))
            db.commit()
        try:
            if oversized:
                raise client.DecisionError('This source exceeds the check limit; no verification was claimed.')
            result = provider.evaluate(state, questions, images) if images else provider.evaluate(state, questions)
            if receipt:
                result = receipt['result'](result)
            status, error = 'complete', ''
        except client.DecisionError as failure:
            # JEV HOST FAILURES: the receipt keeps only the flags, never host text; a refusal decided nothing (`refused`).
            flags = {'host': True, **({'refused': True} if failure.refused else {})} if getattr(failure, 'host', False) else {}
            result, status, error = flags, 'uncertain' if failure.uncertain else 'failed', str(failure)
        except Exception:
            result, status, error = {}, 'uncertain', 'The Jev check did not finish; it was not resent.'
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE jev_calls SET status=?,result_json=?,error=?,completed_at=? WHERE id=? AND status='running'",
                       (status, encoded(result), error, timestamp(), ident))
            db.commit()
            return self._view(db.execute('SELECT * FROM jev_calls WHERE id=?', (ident,)).fetchone())

    @staticmethod
    def _view(row):
        return {'id': row['id'], 'status': row['status'], 'input_sha256': row['input_sha256'],
                'error': row['error'], **json.loads(row['result_json'])}
