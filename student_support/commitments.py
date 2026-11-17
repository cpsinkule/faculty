"""TEACHING-WORKFLOWS SEPARATE COMMITMENTS: enrollment-owned follow-up content, no new store."""
import json
import re
import uuid
from datetime import date

from werkzeug.exceptions import Conflict


def confirmed(db, ident):
    """An action's own receipt, including Mail's accepted outcome, never its proposal or HTTP success alone."""
    row = db.execute('''SELECT a.endpoint,a.body_json,e.kind,e.detail_json FROM assistant_actions a
        LEFT JOIN assistant_action_events e ON e.action_id=a.id
        AND e.id=(SELECT MAX(id) FROM assistant_action_events WHERE action_id=a.id) WHERE a.id=?''', (ident,)).fetchone()
    if not row:
        return False
    replacement = db.execute('SELECT send_id FROM student_support_suggestion_replacements WHERE action_id=?', (ident,)).fetchone()
    if replacement:
        receipt = db.execute('SELECT status FROM gmail_mail_outcomes WHERE send_id=? ORDER BY id DESC LIMIT 1', (replacement[0],)).fetchone()
        return bool(receipt and receipt[0] == 'accepted')
    if row['kind'] != 'succeeded':
        return False
    if row['endpoint'] == 'gmail_mail.send':
        receipt = db.execute('''SELECT o.status FROM gmail_mail_sends s JOIN gmail_mail_outcomes o ON o.send_id=s.id
            WHERE s.request_id=? ORDER BY o.id DESC LIMIT 1''', (json.loads(row['body_json']).get('request_id'),)).fetchone()
        return bool(receipt and receipt[0] == 'accepted')
    return (json.loads(row['detail_json']).get('result') or {}).get('status') not in ('failed', 'uncertain')


def project(db, rows):
    by_id, states = {r['id']: r for r in rows}, {}
    def state(ident):
        if ident in states:
            return states[ident]
        row = by_id[ident]
        final = row['state']
        if final != 'cancelled' and row.get('action_id') and confirmed(db, row['action_id']):
            final = 'completed'
        if final == 'completed' and any(dep not in by_id or state(dep) != 'completed' for dep in row.get('depends_on', [])):
            final = 'open'
        states[ident] = final
        return final
    return [{**row, 'effective_state': state(row['id'])} for row in rows]


def heard(db, enrollment, rows):
    """RECORDINGS "Student meetings" (step 4): a commitment that names the recording item it was heard in carries `heard`, where
    to hear it ({recording_id, start_ms, day, href}), while that item is still of THIS student's own meeting. A view, by
    reference: nothing of it is saved, and a save ignores it."""
    if not any(row.get('recording_item_id') for row in rows):
        return rows
    from recordings import meetings
    found = []
    for row in rows:
        where = meetings.source(db, enrollment, row['recording_item_id']) if row.get('recording_item_id') else None
        found.append({**row, 'heard': where} if where else row)
    return found


def is_open(row):
    return row.get('effective_state', row.get('state', 'open')) == 'open'


def all_tasks(item):
    return [*item.get('commitments', []), *[c for owner in item.get('related_commitments', []) for c in owner['commitments']]]


def needs_attention(rows, today):
    return any(is_open(row) and (row['waiting_on'] == 'you' or row.get('check_back') and row['check_back'] <= today) for row in rows)


def normalize(value, previous, db, enrollment, source_key, item_id=None):
    """Checked as part of the ordinary lifecycle-locked follow-up revision; omission never deletes tasks."""
    from .service import text, conversation_of
    if not isinstance(value, list) or len(value) > 40:
        raise ValueError('TEACHING-WORKFLOWS: a follow-up holds at most 40 commitments.')
    old = {r['id']: r for r in previous}
    rows, seen = [], set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError('TEACHING-WORKFLOWS: supply each commitment as an object.')
        ident = raw.get('id') or str(uuid.uuid4())
        if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', ident) or ident in seen:
            raise ValueError('TEACHING-WORKFLOWS: each commitment needs a unique, stable identity.')
        seen.add(ident)
        row = {'id': ident, 'title': text(raw.get('title', ''), 'Commitment', 200, required=True),
               'kind': raw.get('kind', 'work'), 'state': raw.get('state', 'open'),
               'waiting_on': raw.get('waiting_on', 'you'), 'check_back': raw.get('check_back') or '',
               'completion_note': text(raw.get('completion_note', ''), 'Completion note', 2000),
               'source_message_id': text(raw.get('source_message_id', ''), 'Source message', 100),
               'action_id': text(raw.get('action_id', ''), 'Action receipt', 100),
               'blocks_reply': raw.get('blocks_reply', False), 'depends_on': raw.get('depends_on', [])}
        # HEARD IN A RECORDED MEETING (RECORDINGS step 4): a commitment may name the recording item it was heard in. Kept only
        # where it is given, so every other commitment's saved content is what it was.
        heard_in = text(raw.get('recording_item_id', ''), 'Recording item', 100)
        if heard_in:
            row['recording_item_id'] = heard_in
        if row['kind'] not in ('work', 'reply', 'check_back') or row['state'] not in ('open', 'completed', 'cancelled') or row['waiting_on'] not in ('you', 'student', 'date'):
            raise ValueError('TEACHING-WORKFLOWS: choose a valid commitment kind, state and owner of the next step.')
        if type(row['blocks_reply']) is not bool or (row['blocks_reply'] and row['kind'] != 'work'):
            raise ValueError('TEACHING-WORKFLOWS: only a work commitment can be a prerequisite for the reply.')
        if row['check_back']:
            try:
                if date.fromisoformat(row['check_back']).isoformat() != row['check_back']:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError('TEACHING-WORKFLOWS: choose a valid check-back date.') from None
        if row['waiting_on'] == 'date' and not row['check_back']:
            raise ValueError('TEACHING-WORKFLOWS: a scheduled check-back needs its recorded date.')
        deps = row['depends_on']
        if not isinstance(deps, list) or len(deps) > 10 or any(not isinstance(d, str) for d in deps) or len(set(deps)) != len(deps):
            raise ValueError('TEACHING-WORKFLOWS: choose at most ten distinct prerequisites.')
        if row['source_message_id']:
            message = db.execute('''SELECT m.channel,m.thread_key,m.external_id,r.context_json FROM comm_messages m
                JOIN comm_links l ON l.message_id=m.id JOIN comm_revisions r ON r.message_id=m.id
                WHERE m.id=? AND l.enrollment_id=? ORDER BY r.revision DESC LIMIT 1''', (row['source_message_id'], enrollment)).fetchone()
            if not message:
                raise ValueError('TEACHING-WORKFLOWS: the source message must belong to this student.')
            from .service import conversation_key
            key = conversation_key(message, json.loads(message['context_json']))
            if not source_key or conversation_of(db, key, enrollment) != conversation_of(db, source_key, enrollment):
                raise ValueError('TEACHING-WORKFLOWS: choose a source message from this follow-up.')
        if row['action_id']:
            action = db.execute('''SELECT r.* FROM student_support_suggestion_runs r JOIN student_support_suggestion_actions a
                ON a.run_id=r.id WHERE a.action_id=? AND r.enrollment_id=?''', (row['action_id'], enrollment)).fetchone()
            if not action or not (item_id and action['item_id'] == item_id or source_key and action['source_key'] and
                                  conversation_of(db, action['source_key'], enrollment) == conversation_of(db, source_key, enrollment)):
                raise ValueError('TEACHING-WORKFLOWS: choose an action receipt from this follow-up.')
            if row['state'] == 'completed' and not confirmed(db, row['action_id']):
                raise Conflict('TEACHING-WORKFLOWS: the linked action has no confirmed completion receipt.')
        before = old.get(ident)
        if heard_in and (not before or before.get('recording_item_id') != heard_in):
            from recordings import meetings
            if not meetings.source(db, enrollment, heard_in):
                raise ValueError('TEACHING-WORKFLOWS: a commitment heard in a recorded meeting must be from this student’s own part of it.')
            elsewhere = meetings.tracked(db, [enrollment]).get((enrollment, heard_in))
            if elsewhere and elsewhere != item_id:
                raise Conflict('TEACHING-WORKFLOWS: what was heard there is already a commitment on one of this student’s follow-ups.')
        if row['state'] in ('completed', 'cancelled') and (not before or before['state'] != row['state']) and not row['action_id'] and not row['completion_note']:
            raise ValueError('TEACHING-WORKFLOWS: add a note explaining the completed work or cancelled commitment.')
        rows.append(row)
    if old.keys() - seen:
        raise Conflict('TEACHING-WORKFLOWS: commitments are retained; cancel a commitment instead of deleting it.')
    heard_in = [row['recording_item_id'] for row in rows if row.get('recording_item_id')]
    if len(set(heard_in)) != len(heard_in):
        raise ValueError('TEACHING-WORKFLOWS: two commitments name the same moment of a recorded meeting.')
    by_id = {r['id']: r for r in rows}
    visited = set()
    def visit(ident, path):
        if ident in path:
            raise ValueError('TEACHING-WORKFLOWS: commitment prerequisites cannot form a cycle.')
        if ident in visited:
            return
        for dep in by_id[ident]['depends_on']:
            if dep not in by_id:
                raise ValueError('TEACHING-WORKFLOWS: a prerequisite must belong to this follow-up.')
            visit(dep, path | {ident})
        visited.add(ident)
    for row in rows:
        visit(row['id'], set())
    states = {r['id']: r['effective_state'] for r in project(db, rows)}
    for row in rows:
        if row['state'] == 'completed' and any(states[d] != 'completed' for d in row['depends_on']):
            raise Conflict('TEACHING-WORKFLOWS: complete this commitment’s prerequisites first.')
    return rows
