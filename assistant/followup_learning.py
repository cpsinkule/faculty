"""ASSISTANT-MEMORY "LEARNED FROM AN EDIT": the instructor's confirmed corrections of a follow-up's cards, found and compared.

A correction is a card the instructor edited (a reply, a regrade's reply, an absence's decision), a reply they sent from Mail
in a draft's place, or a round prepared again at their request, once what they approved has a confirmed outcome. Cases
(`student_support_learning_cases`, enrollment-owned through their run) are the durable queue: they reference immutable
actions, runs and sends and copy no words. What a correction teaches is memory's to write, check and keep
(`AssistantMemory.learn`); a case's last event says what came of it, so nothing is handed twice.

The lesson review these tables were made for (2026-10-08) is withdrawn (requirement, 2026-10-12). The cases
it analysed stay as they are, `ready`, and are never picked up.
"""
import difflib
import json
import os
import re
import threading
import time
import uuid

from course_storage import timestamp
from rosters import connect

from .evidence import digest
from .followup_guidance import sentences
from .memory import OPEN_CHARS, OPEN_MESSAGES, nameless, spelled
from .outcomes import Outcomes

CASES = '''SELECT c.*,e.id seen,e.state,e.detail_json FROM student_support_learning_cases c
 JOIN student_support_learning_events e ON e.case_id=c.id
 AND e.id=(SELECT MAX(id) FROM student_support_learning_events WHERE case_id=c.id)'''
# Only what the instructor confirms from here on teaches: nothing from before this path was live is kept by itself (their
# past corrections were shown to them as a dry run to choose from).
LEARNS_FROM = '2026-10-13T01:42:09+00:00'
SCAN = 60  # seconds between looks when no approval woke one (a Mail send accepted later)
BATCH = 10  # corrections one look takes of each kind; each gets a case, so the next look takes the next ones
ASKED = 2000  # a longer request holds more than one rule can say
LAST = 'e.action_id=a.id AND e.id=(SELECT MAX(id) FROM assistant_action_events WHERE action_id=a.id)'
CURRENT = '''JOIN student_enrollments s ON s.id=r.enrollment_id JOIN course_terms t ON t.id=s.term_id
 WHERE t.is_current=1 AND t.deleted_at IS NULL'''
REPLIES = {'gmail_mail.send': 'body', 'frq_regrade_panel.decide': 'reply'}
DECISIONS = ('absences.create', 'absences.revise')
CARDS = ('reply', 'absence decision', 'follow-up status')


def mask(value, names=()):
    value = str(value or '')
    # A name as `memory.spelled` reads the roster's: whole in either order, then a given or family name alone, which is
    # case-sensitive (Will or May must not erase ordinary verbs).
    for pattern, _ in spelled(value, names):
        value = re.sub(pattern, '[person]', value)
    value = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[contact]', value)
    value = re.sub(r'\b[0-9a-f]{8}-[0-9a-f-]{27,}\b', '[record]', value, flags=re.I)
    value = re.sub(r'\d+(?:[.:/+-]\d+)*', '[value]', value)
    return value


def changed(before, after):
    """(sentences taken out, sentences put in) between a draft and what was approved in its place, in order."""
    old, new = sentences(before), sentences(after)
    removed, added = [], []
    for tag, i, j, k, l in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag in ('replace', 'delete'):
            removed += old[i:j]
        if tag in ('replace', 'insert'):
            added += new[k:l]
    return removed, added


class FollowupLearning:
    def __init__(self, owner):
        self.owner = owner
        self._scanned = 0
        self._woken = threading.Event()
        self._woken.set()  # the first look also hands on what a restart left queued
        self._handed, self._working, self._looking, self._lock = set(), 0, False, threading.Lock()

    @staticmethod
    def event(db, ident, state, detail=None):
        db.execute('INSERT INTO student_support_learning_events(case_id,state,detail_json,created_at) VALUES(?,?,?,?)',
                   (ident, state, json.dumps(detail or {}), timestamp()))

    def names(self):
        # One verified-roster read per operation; never a retained process-wide cache.
        return [person.get('name', '') for person in self.owner.support.people().values()]

    # -- finding ---------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _first(db, action_id):
        """The Assistant's own card an edited card goes back to: an edit of an edit is still a correction of the first."""
        for _ in range(12):
            parent = db.execute('SELECT action_id FROM student_support_suggestion_edits WHERE edited_action_id=?', (action_id,)).fetchone()
            if not parent:
                break
            action_id = parent[0]
        return action_id

    def _case(self, db, row, original, final, feedback, fingerprint, send=None):
        ident = uuid.uuid4().hex
        db.execute('''INSERT INTO student_support_learning_cases
            (id,term_id,enrollment_id,run_id,action_id,final_action_id,final_send_id,feedback,strength,fingerprint,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (ident, row['term_id'], row['enrollment_id'], row['run_id'], original, final, send,
                                             feedback, 'strong', fingerprint, timestamp()))
        self.event(db, ident, 'queued')

    def discover(self):
        """A bounded look for corrections confirmed since `LEARNS_FROM` that have no case yet. Every one it examines gets a
        case, so the next look takes the next ones; an unchanged approval, a decline and an abandoned edit are no correction and
        get none. No model and no Jev. Orders are pinned (PERFORMANCE)."""
        since = self.since()
        with self.owner.rosters.lock(), connect(self.owner.path) as db, Outcomes(self.owner.assistant, db).sql() as confirmed:
            db.execute('BEGIN IMMEDIATE')
            added = 0
            # 1. A card the instructor edited (reply, regrade reply, absence decision), approved as they left it.
            for row in db.execute(f'''SELECT a.id,x.run_id,r.enrollment_id,s.term_id FROM student_support_suggestion_edits x
                    JOIN assistant_actions a ON a.id=x.edited_action_id JOIN assistant_action_events e ON {LAST}
                    JOIN student_support_suggestion_runs r ON r.id=x.run_id {CURRENT}
                    AND a.tier='approve' AND e.created_at>=? AND {confirmed}
                    AND NOT EXISTS(SELECT 1 FROM student_support_learning_cases c WHERE c.final_action_id=a.id AND c.feedback='edited')
                    ORDER BY e.id LIMIT ?''', (since, BATCH)).fetchall():
                self._case(db, row, self._first(db, row['id']), row['id'], 'edited', digest(['edit', row['id']]))
                added += 1
            # 2. A round prepared again at their request (Ask for changes, the chat, an "Instead" choice), once a card of it is
            # approved: one correction a round, whatever its cards.
            for row in db.execute(f'''SELECT r.id run_id,r.enrollment_id,s.term_id,
                    (SELECT a.id FROM student_support_suggestion_actions l JOIN assistant_actions a ON a.id=l.action_id
                      JOIN assistant_action_events e ON {LAST} WHERE l.run_id=r.id AND a.tier='approve' AND {confirmed}
                      ORDER BY e.id LIMIT 1) first
                    FROM student_support_suggestion_runs r {CURRENT} AND r.reason='revise' AND r.instruction!=''
                    AND NOT EXISTS(SELECT 1 FROM student_support_learning_cases c WHERE c.run_id=r.id AND c.feedback='requested_change')
                    AND EXISTS(SELECT 1 FROM student_support_suggestion_actions l JOIN assistant_actions a ON a.id=l.action_id
                      JOIN assistant_action_events e ON {LAST} WHERE l.run_id=r.id AND a.tier='approve' AND e.created_at>=? AND {confirmed})
                    ORDER BY r.created_at,r.id LIMIT ?''', (since, BATCH)).fetchall():
                self._case(db, row, row['first'], row['first'], 'requested_change', digest(['round', row['run_id']]))
                added += 1
            # 3. A reply they sent from Mail in a draft's place, once Gmail accepted it.
            for row in db.execute(f'''SELECT p.action_id,p.run_id,p.send_id,r.enrollment_id,s.term_id FROM student_support_suggestion_replacements p
                    JOIN student_support_suggestion_runs r ON r.id=p.run_id {CURRENT} AND p.created_at>=?
                    AND (SELECT o.status FROM gmail_mail_outcomes o WHERE o.send_id=p.send_id ORDER BY o.id DESC LIMIT 1)='accepted'
                    AND NOT EXISTS(SELECT 1 FROM student_support_learning_cases c WHERE c.final_send_id=p.send_id)
                    ORDER BY p.created_at,p.action_id LIMIT ?''', (since, BATCH)).fetchall():
                self._case(db, row, self._first(db, row['action_id']), row['action_id'], 'edited_mail', digest(['mail', row['send_id']]), row['send_id'])
                added += 1
            db.commit()
        return added

    # -- comparing -------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _said(db, case, run, names):
        """What the student wrote, for the kind of case alone: their latest messages among those the round was prepared from (its
        own captured revisions, not the conversation as it has become), cut short, names and UINs taken out."""
        wrote = []
        for item in json.loads(run['source_json']).get('messages', []):
            found = db.execute('''SELECT r.subject,r.body_text,r.author_role FROM comm_revisions r JOIN comm_links l ON l.message_id=r.message_id
                WHERE r.message_id=? AND r.revision=? AND l.enrollment_id=? LIMIT 1''', (item['id'], item['revision'], case['enrollment_id'])).fetchone()
            if found and found['author_role'] == 'student':
                wrote.append(' '.join(f"{found['subject']}\n{found['body_text']}".split())[:OPEN_CHARS])
        return nameless('\n\n'.join(wrote[-OPEN_MESSAGES:]), names)

    @staticmethod
    def _reply(before, after, names):
        """A reply as the Assistant drafted it against the one the instructor approved, or None when nothing was changed. The
        Assistant's words lose names, contacts and every number; the sentences the instructor wrote lose only names and UINs."""
        removed, added = changed(before, after)
        if not removed and not added:
            return None
        return {'removed': [mask(s, names)[:700] for s in removed[:8]], 'added': [nameless(s, names)[:700] for s in added[:8]],
                'before': mask(before, names)[:2500], 'after': mask(after, names)[:2500]}

    @staticmethod
    def _decision(body, new):
        from absences.service import REASONS, STATUSES
        status = body.get('status', 'pending' if new else None)
        return status, {'decision': STATUSES.get(status, ''), 'absence': REASONS.get(body.get('reason'), ''), 'documents': bool(body.get('documents'))}

    def _card(self, endpoint, body, names):
        """One card of a round, for a request's comparison: only what a rule could be about. Never a grade, a date or a recipient."""
        if endpoint in REPLIES:
            return CARDS[0], mask(' '.join(f"{body.get('subject') or ''}\n{body.get(REPLIES[endpoint]) or ''}".split()), names)[:1500]
        if endpoint in DECISIONS:
            status, shown = self._decision(body, endpoint == 'absences.create')
            return CARDS[1], ', '.join(part for part in (shown['decision'], shown['absence']) if part) if status else ''
        if endpoint in ('student_support.create', 'student_support.revise') and body.get('kind', 'followup') == 'followup' and body.get('state'):
            return CARDS[2], mask(' '.join(f"{body['state']}. {body.get('next') or ''}".split()), names)[:400]
        return '', ''

    def _round(self, db, run, names):
        asked = ' '.join(str(run['instruction'] or '').split())
        if len(asked) > ASKED:
            return {'why': 'long'}

        def cards(run_id, which):
            return [(row['endpoint'], json.loads(row['body_json'])) for row in db.execute(
                f'''SELECT a.endpoint,a.body_json FROM student_support_suggestion_actions l JOIN assistant_actions a ON a.id=l.action_id
                    JOIN assistant_action_events e ON {LAST} WHERE l.run_id=? AND a.tier='approve' AND {which} ORDER BY e.id''', (run_id,))]
        with Outcomes(self.owner.assistant, db).sql() as confirmed:
            after = cards(run['id'], confirmed)
        # What waited when they asked again: the cards the request replaced, never a step already done.
        before = cards(run['parent_id'], "e.kind='declined' AND json_extract(e.detail_json,'$.replaced_by_request')=1") if run['parent_id'] else []
        shown = []
        for endpoint in dict.fromkeys(e for e, _ in after):
            now, then = [b for e, b in after if e == endpoint], [b for e, b in before if e == endpoint]
            what, words = self._card(endpoint, now[0], names)
            if len(now) != 1 or not words:
                continue
            was = self._card(endpoint, then[0], names)[1] if len(then) == 1 else ''
            if was != words:
                shown.append({'what': what, **({'before': was} if was else {}), 'after': words})
        shown.sort(key=lambda card: CARDS.index(card['what']))
        return {'kind': 'request', 'own': asked, 'change': {'what': "a round prepared again at the instructor's request",
                                                            'asked': nameless(asked, names), 'cards': shown[:3]}}

    def comparison(self, db, case, names):
        """What one correction changed, for memory's writer and its check: {'kind', 'change', 'own', 'about'}, or {'why'} when it
        cannot teach. `own`: the words the instructor wrote themselves, which a rule's numbers and limits are held to; `about`:
        what the student wrote. A pure read: nothing is recorded, and no private note, grade, date or recipient is in it."""
        run = db.execute('SELECT * FROM student_support_suggestion_runs WHERE id=?', (case['run_id'],)).fetchone()
        about = self._said(db, case, run, names)
        if case['feedback'] == 'requested_change':
            found = self._round(db, run, names)
            return found if 'why' in found else {**found, 'about': about}
        first = db.execute('SELECT endpoint,body_json FROM assistant_actions WHERE id=?', (case['action_id'],)).fetchone()
        endpoint, before = first['endpoint'], json.loads(first['body_json'])
        if case['final_send_id']:
            sent = db.execute('SELECT subject,body_text FROM gmail_mail_sends WHERE id=?', (case['final_send_id'],)).fetchone()
            after = {'subject': sent['subject'], 'body': sent['body_text']}
        else:
            after = json.loads(db.execute('SELECT body_json FROM assistant_actions WHERE id=?', (case['final_action_id'],)).fetchone()[0])
        if endpoint in DECISIONS:
            new = endpoint == 'absences.create'
            (suggested, was), (chosen, now) = self._decision(before, new), self._decision(after, new)
            if suggested == chosen:
                return {'why': 'unchanged'}  # changed and changed back: the Assistant's suggestion stood
            if chosen in ('withdrawn', 'pending') or not about:
                return {'why': 'unexplained'}  # left undecided or withdrawn, or nothing says what kind of absence it is
            return {'kind': 'decision', 'own': '', 'about': about, 'change': {
                'what': 'the decision on an absence', 'suggested': was['decision'], 'chosen': now['decision'],
                **({'absence': now['absence']} if now['absence'] else {}), 'documents': now['documents']}}
        if endpoint in REPLIES:
            field = REPLIES[endpoint]
            drafted, approved = str(before.get(field) or ''), str(after.get(field) or '')
            reply = self._reply(drafted, approved, names)
            if reply is None:
                return {'why': 'unchanged'}  # a regrade's grade alone, or only a subject: no grade becomes a rule
            return {'kind': 'edit', 'own': '\n'.join(changed(drafted, approved)[1]), 'about': about,
                    'change': {'what': 'a reply to a regrade request' if field == 'reply' else 'a reply to the student', **reply}}
        return {'why': 'unchanged'}

    # -- handing on ------------------------------------------------------------------------------------------------------------
    def wake(self):
        """An approval just went through: the dispatcher's next look finds what it confirmed. Sets a flag only."""
        self._woken.set()

    def busy(self):
        """Whether a correction is on its way to being considered, for the page to read again soon: from the approval that woke
        the look until what it found has been considered, with no gap between (a page read in one would wait half a minute for
        what was kept). Process memory only."""
        return self.active() and (self._woken.is_set() or self._looking or self._working > 0)

    def _settings(self):
        """The instructor's switch on the Memory page (ASSISTANT-MEMORY "ONE PAGE") and the instant it was last turned on."""
        memory = getattr(self.owner.assistant, 'memory', None)
        return memory.settings() if memory is not None else {'learning': True, 'learning_since': ''}

    def active(self):
        """Never in checks, off with FACULTY_LEARNING_ENABLED=0, and off while the instructor has switched it off."""
        return (bool(self.owner.automatic) and os.environ.get('FACULTY_LEARNING_ENABLED', '1') != '0'
                and self._settings()['learning'])

    def since(self):
        """The instant from which a correction teaches: when this went live, or when the instructor last switched it on.
        What they corrected while it was off is not learned from afterwards."""
        return max(LEARNS_FROM, self._settings()['learning_since'] or '')

    def turn(self):
        """The dispatcher's look: find new corrections and hand the queued ones to memory's worker. It follows the instructor's
        own approvals, as a chat memory follows what they type, so the pause on automatic suggestions does not hold it."""
        if not self.active() or not self._woken.is_set() and time.monotonic() - self._scanned < SCAN:
            return
        self._scanned, self._looking = time.monotonic(), True
        try:
            self._woken.clear()
            self.discover()
            self.hand()
        finally:
            self._looking = False

    def hand(self):
        # Only cases this path made: one the lesson review left queued when it was withdrawn is from before, and is not learned from.
        with connect(self.owner.path) as db:
            queued = [row['id'] for row in db.execute(CASES + " WHERE e.state='queued' AND c.created_at>=? ORDER BY c.created_at,c.id LIMIT ?",
                                                      (self.since(), BATCH * 3))]
        for ident in queued:
            with self._lock:
                if ident in self._handed:
                    continue
                self._handed.add(ident)
                self._working += 1
            self.owner.assistant.memory.later(lambda ident=ident: self._job(ident))

    def _job(self, ident):
        try:
            self.consider(ident)
        finally:
            with self._lock:
                self._working -= 1
                # Considered, so no longer queued and never handed again. One that could not even be claimed is still
                # queued, was asked of no one, and is handed at the next look instead of waiting for a restart.
                self._handed.discard(ident)

    def consider(self, ident):
        """One correction, once: claimed, compared, given to memory, and what came of it recorded as the case's last event
        (`remembered`, `known`, `nothing` or `failed`). A memory and its `remembered` event are one transaction."""
        with self.owner.rosters.lock(), connect(self.owner.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(CASES + ' WHERE c.id=?', (ident,)).fetchone()
            if not row or row['state'] != 'queued':
                return None
            self.event(db, ident, 'running')
            db.commit()
        case, state, detail = dict(row), 'failed', {'why': 'error'}
        try:
            with connect(self.owner.path) as db:
                found = self.comparison(db, case, self.names())
            if 'why' in found:
                state, detail = 'nothing', found
            else:
                kind = found['kind']
                kept = self.owner.assistant.memory.learn(
                    found['change'], ident, own=found['own'], about=found['about'], enrollment=case['enrollment_id'],
                    recorded=lambda db, memory: self.event(db, ident, 'remembered', {'memory_id': memory['id'], 'kind': kind,
                                                           **({'replaced': memory['replaced']} if memory.get('replaced') else {})}))
                if kept['action'] in ('kept', 'replaced'):
                    return kept
                state, detail = ('known', {'memory_id': kept['memory']}) if kept['action'] == 'known' else ('nothing', {'why': kept['why']})
        except Exception:  # noqa: BLE001 - one correction never stops the others; nothing is kept and nothing is asked again
            self.owner.assistant.app.logger.exception('ASSISTANT MEMORY: a correction could not be considered')
        with self.owner.rosters.lock(), connect(self.owner.path) as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute(CASES + ' WHERE c.id=?', (ident,)).fetchone()
            if latest and latest['state'] == 'running':
                self.event(db, ident, state, detail)
            db.commit()
        return None

    def recover(self):
        """At start: a correction left mid-way keeps nothing and is not asked about again (JEV: no resends)."""
        with self.owner.rosters.lock(), connect(self.owner.path) as db:
            for row in db.execute(CASES + " WHERE e.state='running'").fetchall():
                self.event(db, row['id'], 'interrupted')
            db.commit()

    # -- saying ----------------------------------------------------------------------------------------------------------------
    def learned(self, db):
        """SAID WHERE IT HAPPENED: what was remembered from corrections, newest first, by follow-up:
        {(enrollment_id, item_key): [{'case', 'kind', 'at', 'memory', 'replaced'?}]}. `remembered` is a case's last event. Only a
        memory that still stands as it was kept, or that the instructor took back, is said. One bounded read."""
        rows = db.execute('''SELECT c.id,c.enrollment_id,r.item_key,e.created_at,e.detail_json FROM student_support_learning_events e
            JOIN student_support_learning_cases c ON c.id=e.case_id JOIN student_support_suggestion_runs r ON r.id=c.run_id
            WHERE e.state='remembered' ORDER BY e.id DESC LIMIT 50''').fetchall()
        if not rows:
            return {}
        details = [json.loads(row['detail_json']) for row in rows]
        views = self.owner.assistant.memory.views([d['memory_id'] for d in details] + [d['replaced'] for d in details if d.get('replaced')])
        found = {}
        for row, detail in zip(rows, details):
            memory = views.get(detail['memory_id']) or {}
            taken_back = memory.get('status') == 'removed' and memory.get('reason') == 'undone'
            if memory.get('erased') or not (taken_back or memory.get('learned') and memory.get('status') == 'current'):
                continue
            replaced = views.get(detail.get('replaced')) or {}
            found.setdefault((row['enrollment_id'], row['item_key']), []).append({
                'case': row['id'], 'kind': detail.get('kind', 'edit'), 'at': row['created_at'],
                'memory': {'id': memory['id'], 'content': memory['content'], 'standing': not taken_back},
                **({'replaced': replaced['content']} if replaced.get('content') and not taken_back else {})})
        return found
