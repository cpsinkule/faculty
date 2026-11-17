"""TEACHING-WORKFLOWS: explicit, enrollment-owned, one-shot follow-up watches.

The worker reads local owner projections. It never sends, resolves tracking, or
retries failed/uncertain work; a meaningful transition may prepare a fresh review.
"""
import json
import time
import uuid
from datetime import date, timedelta

from werkzeug.exceptions import Conflict, NotFound
from course_storage import timestamp
from rosters import connect
from scheduling.clock import campus_today
from student_notes.service import _request
from .evidence import digest
from .followup_plan import problem, running


KINDS = {'date': 'Check back', 'absence': 'Absence decision or documentation',
         'paper': 'Paper received', 'grade': 'Grade or due date changed'}


class FollowupWatches:
    def __init__(self, owner):
        self.owner = owner

    def read(self, path):
        status, data, note = self.owner.assistant.transport.get(path)
        if status != 200 or not isinstance(data, dict):
            raise ValueError('The watched record could not be read. Review it on its page.')
        return data

    def observe(self, enrollment, kind, target):
        if kind == 'date':
            return {'value': 'waiting', 'ready': False}
        if kind == 'absence':
            data = self.read('/api/absences/' + target['id'])
            if data['enrollment_id'] != enrollment:
                raise ValueError('TEACHING-WORKFLOWS: choose this student’s absence.')
            return {'value': digest([data.get('revision'), data.get('status'),
                                    [(d.get('id'), d.get('removed')) for d in data.get('documents', [])]]), 'ready': False}
        with connect(self.owner.path) as db:
            term = db.execute('SELECT term_id FROM student_enrollments WHERE id=?', (enrollment,)).fetchone()
        if not term:
            raise NotFound('That enrollment was not found.')
        if kind == 'paper':
            data = self.read('/api/exams/' + target['id'])
            if data.get('term_id') != term[0]:
                raise ValueError('TEACHING-WORKFLOWS: choose an assessment in this student’s term.')
            students = (data.get('reconciliation') or {}).get('students', [])
            from absences.makeups import RECEIVED
            selected = [s for s in students if s.get('enrollment_id') == enrollment]
            if not selected:
                raise ValueError('This student is not a participant in that assessment.')
            received = any(s.get('status') in RECEIVED for s in selected)
            return {'value': 'received' if received else 'waiting', 'ready': received}
        data = self.read('/api/gradebook/terms/' + term[0])
        column = next((c for c in data.get('setup', {}).get('items', []) if c['id'] == target['id']), None)
        if not column:
            raise ValueError('Choose a gradebook column in this student’s term.')
        rows = sorted(s['row'] for s in data.get('students', []) if s.get('enrollment_id') == enrollment and not s.get('dropped'))
        if not rows:
            raise ValueError('The student is no longer in this gradebook.')
        return {'value': digest([column, [(r, data.get('cells', {}).get(column['id'], {}).get(r)) for r in rows]]), 'ready': False}

    def options(self, enrollment):
        with connect(self.owner.path) as db:
            term = db.execute('SELECT term_id FROM student_enrollments WHERE id=?', (enrollment,)).fetchone()
            if not term:
                raise NotFound('That enrollment was not found.')
            absences = [{'id': r['id'], 'label': r['first_date'] + ' · ' + r['reason']} for r in db.execute('''
                SELECT a.id,r.first_date,r.reason FROM absences a JOIN absence_revisions r ON r.absence_id=a.id
                AND r.revision=(SELECT MAX(revision) FROM absence_revisions WHERE absence_id=a.id)
                WHERE a.enrollment_id=? AND r.status!='withdrawn' ORDER BY r.first_date,a.id''', (enrollment,))]
            papers = [{'id': r['id'], 'label': r['title']} for r in db.execute('''SELECT DISTINCT e.id,a.title
                FROM exam_events e JOIN assignments a ON a.id=e.assignment_id JOIN exam_participants p ON p.event_id=e.id
                WHERE p.enrollment_id=? ORDER BY a.title,e.id''', (enrollment,))]
        gradebook = self.read('/api/gradebook/terms/' + term[0])
        grades = [{'id': c['id'], 'label': c.get('title') or c.get('name') or c['id']}
                  for c in gradebook.get('setup', {}).get('items', [])]
        return {'kinds': KINDS, 'targets': {'date': [], 'absence': absences, 'paper': papers, 'grade': grades}}

    def preview(self, data):
        if not isinstance(data, dict):
            raise ValueError('Supply a watch configuration.')
        enrollment, item_key, kind = data.get('enrollment_id'), data.get('item_key'), data.get('kind')
        if not isinstance(enrollment, str) or not isinstance(item_key, str) or not item_key.startswith(('conversation:', 'item:')) or not isinstance(kind, str) or kind not in KINDS:
            raise ValueError('Choose a follow-up and what to watch.')
        target = data.get('target') or {}
        commitment = target.get('commitment_id') if isinstance(target, dict) else None
        if kind != 'date' and (not isinstance(target, dict) or not isinstance(target.get('id'), str)
                               or not target['id'] or len(target['id']) > 100 or any(c in target['id'] for c in '/?#')):
            raise ValueError('Choose the exact record to watch.')
        target = {} if kind == 'date' else {'id': target['id']}
        if commitment:
            if not isinstance(commitment, str) or len(commitment) > 64:
                raise ValueError('TEACHING-WORKFLOWS: choose a valid commitment to watch.')
            target['commitment_id'] = commitment
        check_back = data.get('check_back') or ''
        if check_back:
            try:
                day = date.fromisoformat(check_back)
            except (ValueError, TypeError):
                raise ValueError('Choose a check-back date.') from None
            if not campus_today() <= day <= campus_today() + timedelta(days=180):
                raise ValueError('Choose a check-back within the next 180 days.')
        elif kind == 'date':
            raise ValueError('Choose a check-back date.')
        with connect(self.owner.path) as db:
            item = self.owner.index(db, enrollment).get((enrollment, item_key))
            source = self.owner.source(db, enrollment, item_key) if item else None
            if not item or item['state'] == 'resolved' or not source or not source['current'] or not any(not m['dropped'] for m in source['memberships']):
                raise Conflict('Choose an open follow-up of a current student.')
            if commitment:
                from student_support.commitments import is_open
                if item and not any(c['id'] == commitment and is_open(c) for c in item.get('commitments', [])):
                    raise Conflict('TEACHING-WORKFLOWS: choose an open commitment of this follow-up.')
        observed = self.observe(enrollment, kind, target)
        if observed['ready']:
            raise Conflict('The paper is already received. Review the follow-up now.')
        spec = {'enrollment_id': enrollment, 'item_key': item_key, 'kind': kind, 'target': target, 'check_back': check_back}
        return {**spec, 'title': KINDS[kind], 'baseline': observed['value'],
                'fingerprint': digest([spec, observed, source]),
                'note': 'Checks local records every two minutes. Prepares a review once; sends nothing and never retries uncertain work.'}

    def create(self, data):
        if not isinstance(data, dict):
            raise ValueError('Supply a watch configuration.')
        request = _request(data.get('request_id'))
        spec_hash = digest({k: data.get(k) for k in ('enrollment_id', 'item_key', 'kind', 'target', 'check_back', 'fingerprint')})
        with self.owner.rosters.lock(), connect(self.owner.path) as db:
            old = db.execute('SELECT * FROM student_support_watches WHERE request_id=?', (request,)).fetchone()
            if old:
                if old['request_sha256'] != spec_hash:
                    raise Conflict('This request ID already saved a different watch.')
                return self.view(old)
            preview = self.preview(data)
            if data.get('fingerprint') != preview['fingerprint']:
                raise Conflict('The follow-up or watched record changed. Review the watch again.')
            if db.execute("SELECT COUNT(*) FROM student_support_watches WHERE state IN ('active','paused','error')").fetchone()[0] >= 50:
                raise ValueError('Keep at most 50 active follow-up watches.')
            ident = uuid.uuid4().hex
            db.execute('INSERT INTO student_support_watches VALUES(?,?,?,?,?,?,?,?,?,?,1,0,?)',
                       (ident, preview['enrollment_id'], preview['item_key'], preview['kind'], json.dumps(preview['target']),
                        preview['check_back'], preview['baseline'], request, spec_hash, 'active', timestamp()))
            self.event(db, ident, 'active', {'message': 'Watching for ' + preview['title'].lower()})
            return self.view(db.execute('SELECT * FROM student_support_watches WHERE id=?', (ident,)).fetchone())

    @staticmethod
    def event(db, ident, state, detail, request=None):
        db.execute('INSERT INTO student_support_watch_events(watch_id,state,detail_json,request_id,created_at) VALUES(?,?,?,?,?)',
                   (ident, state, json.dumps(detail), request, timestamp()))

    @staticmethod
    def view(row):
        target = json.loads(row['target_json'])
        return {k: row[k] for k in ('id', 'enrollment_id', 'item_key', 'kind', 'check_back', 'state', 'revision')} | {
            'title': KINDS[row['kind']], **({'commitment_id': target['commitment_id']} if target.get('commitment_id') else {})}

    def listing(self, db, enrollment=None):
        rows = db.execute("SELECT w.*,(SELECT detail_json FROM student_support_watch_events e WHERE e.watch_id=w.id ORDER BY e.id DESC LIMIT 1) detail FROM student_support_watches w WHERE w.state!='removed'" +
                          (' AND enrollment_id=?' if enrollment else '') + ' ORDER BY w.created_at,w.id', (enrollment,) if enrollment else ())
        return [{**self.view(r), 'detail': json.loads(r['detail'] or '{}')} for r in rows]

    def update(self, ident, data):
        if not isinstance(data, dict):
            raise ValueError('Supply the watch state and revision you reviewed.')
        request = _request(data.get('request_id'))
        state = data.get('state')
        request_hash = digest({'state': state, 'revision': data.get('revision')})
        if state not in ('active', 'paused', 'removed'):
            raise ValueError('Choose resume, pause or remove.')
        with self.owner.rosters.lock(), connect(self.owner.path) as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT watch_id,state,detail_json FROM student_support_watch_events WHERE request_id=?', (request,)).fetchone()
            if old:
                if (old['watch_id'], old['state']) != (ident, state) or json.loads(old['detail_json']).get('request_hash') != request_hash:
                    raise Conflict('This request ID already changed a different watch.')
                return {'state': state}
            row = db.execute('SELECT * FROM student_support_watches WHERE id=?', (ident,)).fetchone()
            if not row or row['revision'] != data.get('revision'):
                raise Conflict('The watch changed. Read it again.')
            if row['state'] in ('removed', 'triggered') and state != 'removed':
                raise Conflict('Create a new watch to watch again.')
            db.execute('UPDATE student_support_watches SET state=?,revision=revision+1,next_check=0 WHERE id=?', (state, ident))
            self.event(db, ident, state, {'request_hash': request_hash}, request)
        return {'state': state}

    def process_one(self):
        o = self.owner
        with o.rosters.lock(), connect(o.path) as db:
            db.execute('BEGIN IMMEDIATE')
            if o.paused(db):
                return False
            row = db.execute("SELECT * FROM student_support_watches WHERE state='active' AND next_check<=? ORDER BY next_check,id LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return False
            commitment = json.loads(row['target_json']).get('commitment_id')
            if commitment:
                from student_support.commitments import is_open, all_tasks
                item = o.index(db, row['enrollment_id']).get((row['enrollment_id'], row['item_key']), {})
                if item and not any(c['id'] == commitment and is_open(c) for c in all_tasks(item)):
                    db.execute("UPDATE student_support_watches SET state='triggered',revision=revision+1 WHERE id=?", (row['id'],))
                    self.event(db, row['id'], 'triggered', {'message': 'The linked task is finished. Nothing was prepared.', 'completed': True})
                    return True
            claimed = time.time() + 120
            db.execute('UPDATE student_support_watches SET next_check=? WHERE id=?', (claimed, row['id']))
        error = None
        try:
            observation = self.observe(row['enrollment_id'], row['kind'], json.loads(row['target_json']))
            due = bool(row['check_back'] and row['check_back'] <= campus_today().isoformat())
            triggered = due or (row['kind'] == 'paper' and observation['ready']) or (row['kind'] in ('absence', 'grade') and observation['value'] != row['baseline'])
        except Exception:
            error, triggered = 'The watched record could not be checked. Review it, then resume this watch.', False
        with o.assistant._approve_lock, o.rosters.lock(), connect(o.path) as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM student_support_watches WHERE id=?', (row['id'],)).fetchone()
            if current['state'] != 'active' or current['revision'] != row['revision'] or current['next_check'] != claimed or o.paused(db):
                return True
            if error:
                db.execute("UPDATE student_support_watches SET state='error',revision=revision+1 WHERE id=?", (row['id'],))
                self.event(db, row['id'], 'error', {'message': error})
                return True
            if not triggered:
                return True
            item = o.index(db, row['enrollment_id']).get((row['enrollment_id'], row['item_key']))
            source = o.source(db, row['enrollment_id'], row['item_key']) if item else None
            run_id, message = None, 'The watched condition changed. Review this follow-up.'
            if not item or item['state'] == 'resolved' or not source or not source['current'] or not any(not m['dropped'] for m in source['memberships']):
                message = 'The follow-up is closed or the student is no longer current. Nothing was prepared.'
            else:
                previous = o.latest(db, row['enrollment_id'], row['item_key'])
                try:
                    endpoint = o.endpoint()
                except ValueError:
                    endpoint = None
                if previous and (previous['state'] in ('running', 'failed', 'interrupted') or any(problem(a) or running(a) for a in o._actions(db, previous['id']))):
                    message = 'The check-back is due or the record changed. Review the existing work; nothing was retried.'
                elif endpoint and (row['enrollment_id'], row['item_key']) not in o.muted(db):
                    if previous:
                        o._expire_cards(db, previous['id'])
                        o.event(db, previous['id'], 'superseded', {'watch': row['id']})
                    run_id = o.insert(db, o.target(item), source, endpoint, instruction='The instructor asked to watch this follow-up. ' +
                                      ('Its check-back date is due.' if due else KINDS[row['kind']] + ' changed.') +
                                      ' Read the current case and prepare the next steps. Never repeat completed or uncertain work.')
                    message = 'A fresh review is being prepared: ' + ('the check-back date is due.' if due else 'the watched record changed.')
            db.execute("UPDATE student_support_watches SET state='triggered',revision=revision+1 WHERE id=?", (row['id'],))
            self.event(db, row['id'], 'triggered', {'message': message, 'run_id': run_id})
        return True
