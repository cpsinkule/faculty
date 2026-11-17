"""Instructor decisions on student requests, and explicit exam communications.

Requests and decisions are term-exam records referencing section memberships.
Reusable wording belongs to the course definition. Personalized cards remain
term-owned immutable Response Card issues. Nothing here publishes or sends mail.
"""
import json
import uuid
from pathlib import Path

from werkzeug.exceptions import Conflict, NotFound

from canonical_json import digest, encoded
from course_storage import timestamp
from rosters import connect
from response_cards.schema import validate_schema


KINDS = {'seat': 'Seat problem', 'schedule': 'Scheduling conflict', 'notice': 'Incorrect notice',
         'makeup': 'Makeup request', 'other': 'Other question'}
STATUSES = {'open', 'in_review', 'approved', 'declined', 'resolved'}


def text(value, label, maximum=4000, required=True):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (required and not value.strip()):
        raise ValueError(f'{label} must be text with {1 if required else 0}–{maximum} characters.')
    return value.strip()


class ExamRequestService:
    def __init__(self, exams, grading):
        self.exams, self.grading = exams, grading
        self.path, self.rosters, self.cards = exams.path, exams.rosters, exams.response_cards
        with self.rosters.lock(), connect(self.path) as db:
            db.executescript(Path(__file__).with_name('requests.sql').read_text())

    @staticmethod
    def _event(db, event_id):
        row = db.execute('SELECT e.*,t.definition_id FROM exam_events e JOIN course_terms t ON t.id=e.term_id WHERE e.id=?', (event_id,)).fetchone()
        if row is None:
            raise NotFound('This exam no longer exists.')
        return dict(row)

    @staticmethod
    def _request(db, event_id, request_id):
        row = db.execute('SELECT * FROM exam_requests WHERE id=? AND event_id=?', (request_id, event_id)).fetchone()
        if row is None:
            raise NotFound('This request does not belong to this exam.')
        return dict(row)

    @staticmethod
    def _participant(db, event_id, section_id, enrollment_id):
        row = db.execute('SELECT * FROM exam_participants WHERE event_id=? AND section_id=? AND enrollment_id=?',
                         (event_id, section_id, enrollment_id)).fetchone()
        if row is None:
            raise ValueError('Choose a student participating in this exam and section.')
        return dict(row)

    @staticmethod
    def _version(row, version):
        if type(version) is not int or version != row['version']:
            raise Conflict('This request changed. Reload it before continuing.')

    @staticmethod
    def _history(db, request_id, kind, details):
        db.execute('INSERT INTO exam_request_history(request_id,kind,detail_json,created_at) VALUES(?,?,?,?)',
                   (request_id, kind, encoded(details), timestamp()))

    def listing(self, event_id):
        # This call holds its own roster lock. Do not nest the lifecycle lock.
        event = self.exams.get(event_id)
        names = {(s['section_id'], s['enrollment_id']): s for s in event['participants']}
        with connect(self.path) as db:
            requests = []
            for r in db.execute('SELECT * FROM exam_requests WHERE event_id=? ORDER BY created_at DESC,id', (event_id,)):
                row = dict(r)
                member = names.get((row['section_id'], row['enrollment_id']), {})
                row.update(name=member.get('name', 'Retained student'), section_number=member.get('section_number'))
                row['history'] = [{**dict(h), 'detail': json.loads(h['detail_json'])} for h in db.execute(
                    'SELECT * FROM exam_request_history WHERE request_id=? ORDER BY id', (row['id'],))]
                requests.append(row)
            issues = [dict(r) for r in db.execute('SELECT issue_id AS id,kind,created_at FROM exam_communication_issues WHERE event_id=? ORDER BY created_at DESC', (event_id,))]
            waiting = len(self._unimported(db, event_id))
        return {'requests': requests, 'waiting': waiting, 'issues': [{**i, 'title': {'request': 'Student request cards', 'decision': 'Request decision', 'feedback': 'Released feedback'}[i['kind']]} for i in issues]}

    def create(self, event_id, data):
        kind = data.get('kind')
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError('Choose a request type.')
        description = text(data.get('description'), 'Request description')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self._event(db, event_id)
            self._participant(db, event_id, data.get('section_id'), data.get('enrollment_id'))
            request_id, when = str(uuid.uuid4()), timestamp()
            db.execute('INSERT INTO exam_requests(id,event_id,section_id,enrollment_id,kind,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                       (request_id, event_id, data['section_id'], data['enrollment_id'], kind, description, when, when))
            self._history(db, request_id, 'created', {'source': 'instructor', 'description': description})
        return self._get(event_id, request_id)

    def _get(self, event_id, request_id):
        return next(r for r in self.listing(event_id)['requests'] if r['id'] == request_id)

    def update(self, event_id, request_id, data):
        status = data.get('status')
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError('Choose a valid request status.')
        decision = text(data.get('decision', ''), 'Decision', required=status in {'approved', 'declined', 'resolved'})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._request(db, event_id, request_id)
            self._version(row, data.get('version'))
            if row['applied_hash']:
                raise Conflict('This decision was applied. Create a new request to make a further change.')
            db.execute('UPDATE exam_requests SET status=?,decision=?,version=version+1,updated_at=? WHERE id=?',
                       (status, decision, timestamp(), request_id))
            self._history(db, request_id, 'decision', {'status': status, 'decision': decision})
        return self._get(event_id, request_id)

    def _preview(self, db, event_id, request_id, data):
        event = self._event(db, event_id)
        row = self._request(db, event_id, request_id)
        participant = self._participant(db, event_id, row['section_id'], row['enrollment_id'])
        if row['status'] != 'approved':
            raise Conflict('Approve the request with a recorded decision before applying a change.')
        sitting_id, seat_id = data.get('sitting_id') or None, data.get('seat_id') or None
        if bool(sitting_id) == bool(seat_id):
            raise ValueError('Choose exactly one replacement sitting or seat.')
        plan = {'event_id': event_id, 'event_version': event['version'], 'request_id': request_id,
                'request_version': row['version'], 'participant_version': participant['version'],
                'sitting_id': sitting_id, 'seat_id': seat_id,
                'participant_before': {key: participant[key] for key in ('sitting_id', 'expectation', 'reason')}}
        changes, warnings = [], []
        if sitting_id:
            if row['kind'] not in {'makeup', 'schedule'}:
                raise ValueError('A sitting change requires a makeup or scheduling request.')
            sitting = db.execute('SELECT * FROM exam_sittings WHERE id=? AND event_id=?', (sitting_id, event_id)).fetchone()
            if sitting is None:
                raise ValueError('Choose a sitting of this exam.')
            plan['sitting'] = dict(sitting)
            changes.append({'message': f"Assign this student to {sitting['label']} · {sitting['date']} {sitting['time']} · {sitting['location']}."})
            changes.append({'message': f"Set attendance expectation from {participant['expectation']} to expected and replace the exam-plan reason with this approved decision: {row['decision']}"})
            warnings.append('Existing DR bookings and printed paper stay retained. Review their schedule and any earlier notices.')
        else:
            if row['kind'] != 'seat':
                raise ValueError('A seat change requires a seat request.')
            seat = db.execute('SELECT s.*,r.version room_version,r.name room_name,r.review_required FROM room_seats s JOIN rooms r ON r.id=s.room_id WHERE s.id=?', (seat_id,)).fetchone()
            if seat is None or seat['broken'] or seat['blocked'] or json.loads(seat['review_required']):
                raise ValueError('Choose a usable seat in a reviewed room.')
            charts = db.execute('''SELECT c.*,x.sitting_id exam_sitting_id FROM seating_charts c JOIN exam_event_charts x ON x.chart_id=c.id
                JOIN seating_chart_sections cs ON cs.chart_id=c.id
                WHERE x.event_id=? AND cs.section_id=? AND c.room_id=?''', (event_id, row['section_id'], seat['room_id'])).fetchall()
            if len(charts) != 1:
                raise ValueError('Choose a seat in exactly one linked chart for this student’s section.')
            chart = dict(charts[0])
            if participant['sitting_id'] and chart['exam_sitting_id'] and participant['sitting_id'] != chart['exam_sitting_id']:
                raise Conflict('This chart belongs to a different sitting from the student’s exam plan. Review the sitting assignment first.')
            occupant = db.execute('SELECT * FROM seating_assignments WHERE chart_id=? AND seat_id=?', (chart['id'], seat_id)).fetchone()
            if occupant and occupant['enrollment_id'] != row['enrollment_id']:
                raise Conflict('Choose an empty seat; request resolution does not move another student.')
            sections = [r[0] for r in db.execute('SELECT section_id FROM seating_chart_sections WHERE chart_id=?', (chart['id'],))]
            if row['enrollment_id'] in self.exams.seating.links.excluded(db, sections, event['assignment_id']):
                raise Conflict('This student has a linked DR booking; resolve the booking before classroom seating.')
            plan.update(chart=chart, seat=dict(seat), occupant=dict(occupant) if occupant else None)
            changes.append({'message': f"Move this student to {seat['label']} in {seat['room_name']}."})
            warnings.append('Send an updated seat card from the seating chart after applying this change. Already printed paper keeps its original seat caption.')
        plan['decision'] = row['decision']
        return {'preview_hash': digest(plan), 'plan': plan, 'changes': changes, 'warnings': warnings}

    def preview(self, event_id, request_id, data):
        with self.rosters.lock(), connect(self.path) as db:
            return self._preview(db, event_id, request_id, data)

    def apply(self, event_id, request_id, data):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._request(db, event_id, request_id)
            if row['applied_hash'] and row['applied_hash'] == data.get('preview_hash'):
                return row  # A lost response never applies the seat change twice.
            self._version(row, data.get('version'))
            preview = self._preview(db, event_id, request_id, data)
            if preview['preview_hash'] != data.get('preview_hash'):
                raise Conflict('The affected exam records changed. Preview this decision again.')
            plan = preview['plan']
            if plan['sitting_id']:
                db.execute('UPDATE exam_participants SET sitting_id=?,expectation=\'expected\',reason=?,version=version+1,updated_at=? WHERE event_id=? AND section_id=? AND enrollment_id=?',
                           (plan['sitting_id'], row['decision'], timestamp(), event_id, row['section_id'], row['enrollment_id']))
                db.execute('UPDATE exam_events SET version=version+1,updated_at=? WHERE id=?', (timestamp(), event_id))
            else:
                self.exams.seating._move(db, plan['chart']['id'], plan['chart']['version'], row['enrollment_id'], plan['seat_id'])
            db.execute('UPDATE exam_requests SET status=\'resolved\',applied_hash=?,version=version+1,updated_at=? WHERE id=?',
                       (preview['preview_hash'], timestamp(), request_id))
            self._history(db, request_id, 'applied', preview)
            self.exams._history(db, event_id, 'request_applied', {'request_id': request_id, 'changes': preview['changes']})
        if not plan['sitting_id']:
            self.exams.seating._revoke_changed_cards(plan['chart']['id'])
        return self._get(event_id, request_id)

    @staticmethod
    def _unimported(db, event_id=None):
        """Already-synced request-card submissions not yet imported as requests: [(row, kind, description)]. Read only."""
        found = []
        rows = db.execute('''SELECT s.*,r.section_id,r.enrollment_id,i.event_id FROM response_card_submissions s
            JOIN response_card_recipients r ON r.id=s.card_id JOIN exam_communication_issues i ON i.issue_id=r.issue_id
            LEFT JOIN exam_requests q ON q.source_submission_id=s.id
            WHERE i.kind='request' AND q.id IS NULL AND (? IS NULL OR i.event_id=?) ORDER BY s.received_at,s.id''', (event_id, event_id)).fetchall()
        for row in rows:
            answers = json.loads(row['submission_json']).get('answers', {})
            if not isinstance(answers, dict):
                continue
            kind, description = answers.get('request_kind'), answers.get('request_details')
            kind = next((key for key, label in KINDS.items() if label == kind), kind)
            if not isinstance(kind, str) or kind not in KINDS or not isinstance(description, str) or not description.strip():
                continue
            found.append((row, kind, description))
        return found

    def sync(self, event_id=None):
        """Import immutable, already-synced card responses into an open review queue."""
        added = 0
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            if event_id:
                self._event(db, event_id)
            for row, kind, description in self._unimported(db, event_id):
                self._participant(db, row['event_id'], row['section_id'], row['enrollment_id'])
                request_id, when = str(uuid.uuid4()), timestamp()
                db.execute('INSERT INTO exam_requests(id,event_id,section_id,enrollment_id,kind,description,source_submission_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                           (request_id, row['event_id'], row['section_id'], row['enrollment_id'], kind, description[:4000], row['id'], when, when))
                self._history(db, request_id, 'received', {'source': 'response_card', 'submission_id': row['id'], 'card_id': row['card_id']})
                added += 1
        return {'added': added}

    def issue_context(self, db, normalized, student=None):
        event = self._event(db, normalized['exam_event_id'])
        if event['term_id'] != normalized['term_id'] or event['assignment_id'] != normalized['assignment_id']:
            raise ValueError('The response card context must match this exam’s term and assignment.')
        if student is None:
            return {}
        participant = self._participant(db, event['id'], student['section_id'], student['enrollment_id'])
        variables = {'exam.event_name': event['title']}
        if participant['sitting_id']:
            s = db.execute('SELECT * FROM exam_sittings WHERE id=?', (participant['sitting_id'],)).fetchone()
            variables.update({'exam.sitting': s['label'], 'exam.date': s['date'], 'exam.time': s['time'], 'exam.room': s['location']})
        release_id = normalized.get('feedback_release_id')
        if release_id:
            release = self.grading.release_snapshot(release_id, db=db)
            if (release['event_id'], release['section_id'], release['enrollment_id']) != (event['id'], student['section_id'], student['enrollment_id']):
                raise ValueError('Released feedback belongs to another exam or student.')
            variables.update({'grade.score': str(release['total']), 'grade.maximum': str(release['max_points']),
                              'grade.feedback': '\n\n'.join(f"{q['label']}: {q['score']} / {q['max_points']}\n{q.get('feedback', '')}" for q in release['questions'])})
        request_id = normalized.get('exam_request_id')
        if request_id:
            r = self._request(db, event['id'], request_id)
            if (r['section_id'], r['enrollment_id']) != (student['section_id'], student['enrollment_id']) or r['status'] not in {'approved', 'declined', 'resolved'}:
                raise ValueError('Choose a decided request for this student.')
            if r['version'] != normalized.get('exam_request_version'):
                raise Conflict('The request decision changed. Prepare its notice again.')
            variables.update({'request.decision': r['decision'], 'request.status': r['status'], 'request.description': r['description']})
        return variables

    def _template(self, definition_id, kind):
        """Reuse a course-wide wording template; freeze its current instructor edits."""
        fields = []
        if kind == 'request':
            title = 'Request help with {{exam.event_name}}'
            fields = [{'id': 'request_kind', 'type': 'single_select', 'label': 'What do you need help with?', 'required': True,
                       'options': list(KINDS.values())},
                      {'id': 'request_details', 'type': 'long_text', 'label': 'Describe the problem or request.', 'required': True}]
        elif kind == 'feedback':
            title = 'Feedback · {{exam.event_name}}'
            fields = [{'id': 'score', 'type': 'static', 'label': 'Score: {{grade.score}} / {{grade.maximum}}'},
                      {'id': 'feedback', 'type': 'static', 'label': '{{grade.feedback}}'}]
        else:
            title = 'Your request · {{exam.event_name}}'
            fields = [{'id': 'request', 'type': 'static', 'label': '{{request.description}}'},
                      {'id': 'decision', 'type': 'static', 'label': '{{request.status}}: {{request.decision}}'}]
        fields.append({'id': 'acknowledge', 'type': 'acknowledgment', 'label': 'I have read this information.', 'required': True})
        schema = validate_schema({'title': title, 'fields': fields})
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT t.* FROM exam_managed_templates m JOIN response_card_templates t ON t.id=m.template_id WHERE m.definition_id=? AND m.kind=?', (definition_id, kind)).fetchone()
            if row is None:
                key, when = str(uuid.uuid4()), timestamp()
                db.execute('INSERT INTO response_card_templates(id,definition_id,name,schema_json,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                           (key, definition_id, {'request': 'Exam requests', 'feedback': 'Exam feedback', 'decision': 'Exam request decisions'}[kind] + ' (Faculty)', encoded(schema), when, when))
                db.execute('INSERT INTO exam_managed_templates VALUES(?,?,?)', (definition_id, kind, key))
                revision = 1
            else:
                key, revision = row['id'], row['version']
        return self.cards.freeze(key, revision)['id']

    def prepare_cards(self, event_id, data, kind='request'):
        request_id = text(data.get('request_id'), 'Request ID', 60)
        with self.rosters.lock(), connect(self.path) as db:
            event = self._event(db, event_id)
            sections = [r[0] for r in db.execute('SELECT section_id FROM exam_event_sections WHERE event_id=?', (event_id,))]
            normalized = {'term_id': event['term_id'], 'assignment_id': event['assignment_id'], 'exam_event_id': event_id,
                          'section_ids': data.get('section_ids', sections), 'enrollment_ids': data.get('enrollment_ids', []),
                          'policy': 'once', 'request_id': 'exam-' + kind + '-' + request_id}
            if kind == 'feedback':
                release = self.grading.release_snapshot(text(data.get('release_id'), 'Feedback release'), db=db)
                if release['event_id'] != event_id:
                    raise ValueError('Choose released feedback for this exam.')
                normalized.update(feedback_release_id=release['id'], section_ids=[release['section_id']], enrollment_ids=[release['enrollment_id']])
            elif kind == 'decision':
                r = self._request(db, event_id, data.get('exam_request_id'))
                normalized.update(exam_request_id=r['id'], exam_request_version=r['version'], section_ids=[r['section_id']], enrollment_ids=[r['enrollment_id']])
            # Retries use the original frozen template, even after wording edits.
            old = db.execute('SELECT version_id FROM response_card_issues WHERE request_id=?', (normalized['request_id'],)).fetchone()
        normalized['version_id'] = old['version_id'] if old else self._template(event['definition_id'], kind)
        issue = self.cards.issue(normalized)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('INSERT OR IGNORE INTO exam_communication_issues(issue_id,event_id,kind,request_id,release_id,created_at) VALUES(?,?,?,?,?,?)',
                       (issue['id'], event_id, kind, normalized.get('exam_request_id'), normalized.get('feedback_release_id'), timestamp()))
        return {'id': issue['id'], 'issue_id': issue['id'], 'title': issue['title'], 'recipients': len(issue['recipients']),
                'url': '#response-cards/issue/' + issue['id'], 'published': False}

    def validate_publication(self, issue_id, db=None):
        """A prepared grade/decision notice must still describe current reviewed data."""
        if db is None:
            with self.rosters.lock(), connect(self.path) as owned:
                return self.validate_publication(issue_id, owned)
        row = db.execute('SELECT context_json FROM response_card_issues WHERE id=?', (issue_id,)).fetchone()
        if row is None:
            return
        context = json.loads(row['context_json'])
        if not context.get('exam_event_id'):
            return
        self.issue_context(db, context)
        for recipient in db.execute('SELECT section_id,enrollment_id FROM response_card_recipients WHERE issue_id=?', (issue_id,)):
            self.issue_context(db, context, dict(recipient))
